"""JARVIS02_WRITER/trend_theme_writer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테마주 발행 — trend_economic_writer 와 동일한 1-pass 블록 파이프라인.

파이프라인 (8단계 — 경제 트렌드와 100% 동일, ①단계 입력만 다름):
  ① 데이터 수집  — collect_stocks_data(theme) 종목 7개 + 시세 + 재무
  ② 규정 로드    — BLOG_SUPREME_LAW.build_writing_rules_block()
  ③ 원고 생성    — Claude Code SDK 1-pass HTML (텍스트 + inline SVG)
  ④ HTML 저장    — output/html/{date}_{theme}_{platform}/article.html
  ⑤ SVG 캡처     — JARVIS06.html_screenshotter (inline SVG → JPG)
  ⑥ 블록 조립    — assemble_blocks() + 썸네일 맨 앞 + 제4조 보강
  ⑦ 품질 검증    — enforce_text_between_images + enforce_supreme_law
  ⑧ 발행         — post_to_naver / post_to_tistory

병렬 처리 (trend_economic_writer 패턴 그대로):
  Phase 1: ts/nv 대본 *순차* 생성 (서로 다른 키워드 보장 위해 — ts_keyword 전달)
  Phase 2: Tistory/Naver Selenium 순차 (충돌 방지)

진입점 (하나뿐 — 레거시 직접발행은 2026-07-23 삭제):
  run_all_themes(theme, sector="")  — 데몬·scheduler·CLI 공통. 하네스 액션 2개
    (네이버 완결 → 티스토리) 로 발행. 검증 순환을 거치지 않는 발행 경로는 없다.
"""
from __future__ import annotations

import os
import sys
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# ── sys.path 보정 (subprocess 직접 실행과 데몬 모듈 로드 양쪽 호환) ──
#   ★ 반드시 JARVIS* 패키지 import *보다 먼저* (2026-07-25 실행모델 통일에서 발견):
#   종전엔 아래 `from JARVIS00_INFRA...` 가 이 보정보다 위에 있어, 저장소 루트에서
#   `python3 JARVIS02_WRITER/trend_theme_writer.py <테마>` 로 띄우면 ModuleNotFoundError 로
#   즉사했다. 데몬이 *직접 호출* 만 하던 동안엔 드러나지 않던 잠복 결함
#   (economic_poster.py 는 처음부터 보정이 먼저였다 — 같은 파일에서 순서만 달랐다).
_JARVIS_ROOT = Path(__file__).parent.parent
if str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))

# ★ 블로그(플랫폼) 액션 하드 데드라인 SSOT (watchdog.py) — economic_poster.py 와 동일 상수 참조
#   (2026-07-18: 1800 리터럴 하드코딩이 SSOT 상향과 어긋나던 것을 상수 참조로 정정)
from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

# ── 카테고리 상수 ────────────────────────────────────
try:
    from JARVIS08_PUBLISH.category import THEME_CATEGORY
except ImportError:
    THEME_CATEGORY = "주식 - 테마분류"

load_dotenv()

# ── 텔레그램 알림 ──────────────────────────────────────
def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass


# ── ★ 발행창 표시 — 단일 진입점 (2026-07-25) ─────────────────────────────────
# 왜 컨텍스트 매니저인가: `mark_publishing(True)` / `(False)` 를 *손으로* 짝맞추면
# 예외·조기 return 한 번에 짝이 깨진다. 그 순간 `is_publishing()` 이 True 로 굳는데,
# 커밋 d0af298 이후 그것은 'timeout 강등' 이 아니라 **GUARDIAN 자동수정 + 모든 background
# LLM 의 전면 보류** 를 뜻한다 → 데몬 재시작 전까지 영구 정지. 회수 잡(j07_retry_pending)
# 조차 같은 `_orchestrate` 로 재투입돼 자기가 닫은 문에 갇힌다(스스로 회복 불가).
# 짝 맞춤을 사람의 규율이 아니라 **문법(finally)** 에 맡긴다.
#
# ① 단일 진입점: 정의는 여기 한 곳뿐. `economic_poster.py` 는 이 함수를 import 해서 쓴다.
#    ※ 본래 자리는 `shared/llm.py`(mark_publishing 의 주인)이나 이번 작업 소유 범위 밖이라
#      차선책으로 여기 둔다 — 이관 요구사항은 보고서에 명시.
# ② 동적 설계: 킬스위치는 **호출 시점** 에 읽는다 (모듈 로드 시점 캡처 금지).
# ③ 모든 글 적용: 테마(데몬 in-process)·경제(subprocess) 4조합이 같은 규약을 쓴다.
@contextmanager
def publishing(label: str = ""):
    """with 블록 동안만 `is_publishing()` 이 True — 어떤 경로로 나가도 반드시 닫힌다.

    킬스위치: `JARVIS_PUBLISH_MARK=0` → 표시 자체를 하지 않음 (호출 시점 조회).
    표시 실패(shared.llm import 실패 등)는 삼킨다 — 발행을 절대 막지 않는다.
    """
    _on = (os.getenv("JARVIS_PUBLISH_MARK", "1").strip() != "0")   # ★ 호출 시점 조회
    _marked = False
    if _on:
        try:
            from shared.llm import mark_publishing as _mark_pub
            _mark_pub(True)
            _marked = True
        except Exception as _pe:
            print(f"  ⚠️ 발행창 표시 실패(무시하고 발행 진행){(' — ' + label) if label else ''}: {_pe}")
    try:
        yield
    finally:
        if _marked:                     # True 표시에 성공한 경우에만 짝을 맞춘다 (음수 방지)
            try:
                from shared.llm import mark_publishing as _mark_pub
                _mark_pub(False)
            except Exception:
                pass

# ── 글자수 정책 ────────────────────────────────────────
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L

# ── 이미지 폴더 (플랫폼별) ─────────────────────────────
try:
    from JARVIS06_IMAGE import image_agent as _img_agent
    NAVER_IMG_DIR   = _img_agent.OUTPUT_DIR / 'images' / 'theme_naver'
    TISTORY_IMG_DIR = _img_agent.OUTPUT_DIR / 'images' / 'theme_tistory'
    for _d in (NAVER_IMG_DIR, TISTORY_IMG_DIR):
        _d.mkdir(parents=True, exist_ok=True)
except Exception:
    NAVER_IMG_DIR = TISTORY_IMG_DIR = _JARVIS_ROOT / 'JARVIS06_IMAGE' / 'output' / 'images'

_TODAY     = date.today()
_TODAY_KR  = _TODAY.strftime("%Y년 %m월 %d일")
_TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][_TODAY.weekday()]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ① 데이터 수집 — ★ 02 에는 코드가 없다 (사용자 박제 2026-07-23)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  종전 이 자리에 `_collect`(종목)·`_theme_collect_bundle`(프로필·리서치·종목·조립)·
#  `_theme_collect`(런컨텍스트·프로필 조회·수집 호출)·`precollect_theme`(선계산 잡) 이
#  있었다. 09 API 를 *호출* 하긴 했지만 수집의 순서·조합·재사용·폴백 판단을 02 가 했으므로
#  사실상 02 가 수집을 오케스트레이션한 것 — 수집 단일 진입점(JARVIS09) 위반.
#
#  지금 02 에 남은 수집 관련 코드는 `_step_collect` 의 `collect_all()` *호출 한 줄* 뿐이다.
#  프로필 조회(자비스03)·런컨텍스트 초기화·선계산 캐시 재사용은 전부 09 안에서 끝난다.
#    - 선계산 잡    → `JARVIS09_COLLECTOR.precollect.precollect_theme`
#    - 캐시 재사용  → `JARVIS09_COLLECTOR.collector_engine.collect_all(use_cache=True)`
#  같은 병이 경제 브리핑에도 있었으므로 함께 이관(③ 4조합).


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공통 파이프라인 — ②~⑦ (플랫폼 무관)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_blocks(collected, platform: str, img_dir: Path,
                  supreme_block: str | None = None,
                  gate_feedback: list | None = None) -> dict:
    """대본 생성(JARVIS02) → 이미지 생성(JARVIS06 process_draft v2) → 완성 블록.

    ★ Step 7 (2026-07-05): collected(CollectedData) 단일 소스. theme/sector·검증정답·
      이미지 컨텍스트를 모두 collected 에서 파생. (JARVIS02 Pass-1 / JARVIS06 이미지·조립)

    Returns:
        {"success", "title", "content", "html", "blocks", "error"}
    """
    theme = collected.meta.get("keyword", "")
    sector = collected.meta.get("sector", "")
    # 규정 로드
    if supreme_block is None:
        try:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        except Exception as e:
            print(f"  ⚠️ 헌법 로드 실패: {e}")
            supreme_block = ""
    # ★ 규정 숙지 (2026-07-16): 발행 전 게이트가 실제 채점하는 기준(분량·SEO·매력도 5축)
    #   을 Pass-1 프롬프트에 사전 고지 — supreme_block 합류로 모든 Pass-1 변형 자동 상속.
    try:
        from JARVIS02_WRITER.law_enforcer import build_gate_checklist_block as _gate_chk
        supreme_block = (supreme_block or "") + "\n" + _gate_chk("theme", platform)
    except Exception:
        pass

    # 이미지 초기화 — 폴더는 유지, 파일만 삭제
    import shutil
    img_dir = Path(img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    removed = 0
    for item in img_dir.iterdir():
        if item.is_file():
            item.unlink(missing_ok=True)
            removed += 1
        elif item.is_dir():
            removed += sum(1 for _ in item.rglob("*") if _.is_file())
            shutil.rmtree(item)
    if removed:
        print(f"  🔄 [Theme/{platform}] 이전 이미지 {removed}개 삭제 (폴더 유지)")

    # ── JARVIS02: Pass-1 텍스트 대본 생성 (collected 단일 소스) ──────
    from JARVIS02_WRITER.theme_html_writer import generate_theme_html, extract_text_content
    draft_html = generate_theme_html(collected, supreme_block, platform=platform,
                                     gate_feedback=gate_feedback)
    if not draft_html:
        # ★ 인프라 스로틀/절단(일시적)과 콘텐츠 결함 구분 태깅(rank4) — 테마.
        #   circuit_is_open()은 프로세스 전역(워커 스레드 안전), last_call_infra_incomplete()는
        #   동일 스레드 직전 호출. 둘 중 하나면 infra_throttle → harness 가 defer/backoff.
        from shared.llm import (last_call_infra_incomplete as _infra,
                                circuit_is_open as _copen,
                                make_infra_error as _mk_infra)
        _err = _mk_infra() if (_infra() or _copen()) else "Pass-1 대본 생성 실패"
        return {"success": False, "error": _err, "blocks": [],
                "title": "", "content": "", "html": ""}

    # ── JARVIS06: 이미지 생성 + 블록 조립 (process_draft v2 — collected) ──────
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(draft_html, collected=collected, platform=platform, out_dir=img_dir)
    blocks = result["blocks"]  # J06 이 썸네일 prepend + 법률집행 완료
    html   = result["html"]
    title  = result["title"]

    content = extract_text_content(html)
    n_text = sum(1 for b in blocks if b[0] == "text")
    n_img  = sum(1 for b in blocks if b[0] == "image")
    print(f"  ✅ [Theme/{platform}] 완성 블록 {len(blocks)}개 (텍스트 {n_text} + 이미지 {n_img})")
    return {
        "success": True, "title": title, "content": content,
        "html": html, "blocks": blocks, "error": "",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ⑧ 발행 — 플랫폼별
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _publish_tistory(draft: dict, theme: str, sector: str,
                     preloaded_driver=None) -> dict:
    """티스토리 Selenium 발행. preloaded_driver 가 *이미 갱신된 driver* 면 재사용.
    없으면 *발행 직전* 갱신 (fallback)."""
    if not draft.get("success"):
        return {"success": False, "url": "", "keyword": theme}
    try:
        # preloaded_driver 없으면 여기서 갱신 (예: 단독 호출 시)
        if preloaded_driver is None:
            print(f"  🍪 [Theme/Tistory] 쿠키 갱신 (preloaded_driver 없음 — fallback)")
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr_run
            ok, preloaded_driver = _tcr_run(force=True, return_driver=True)
            if not ok:
                _tg(f"❌ [THEME-TISTORY] 쿠키 갱신 실패 — 발행 중단")
                return {"success": False, "url": "", "keyword": theme}
            load_dotenv(override=True)

        # ★ ADR 008 Phase 2 완전 이관 (사용자 박제 2026-05-18) — shim 제거, 신 경로 직접 import
        import JARVIS08_PUBLISH.platforms.tistory_poster as _tp_mod
        # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
        from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
        _tp_mod.TS_COOKIE = get_tistory_cookie().strip('"').strip("'")
        from JARVIS08_PUBLISH.platforms import post_to_tistory
        from JARVIS06_IMAGE.draft_processor import publish_assembled

        # ★ 태그는 JARVIS08 단일 진입점 (2026-07-29). 종전엔 여기서
        #   `[theme, sector, '테마주', '주식', '투자']` 고정 템플릿을 만들었다 —
        #   ① 모든 테마 글이 같은 태그라 검색 변별력 0 ② BLOG_SUPREME_LAW 제1-B조
        #   (고정 풀·고정 템플릿 금지) 위반 ③ 실측 네이버 4개로 NAVER_HASHTAG_MIN(5)
        #   미달이라 post_scorer N7 감점. 테마명·섹터는 seed 로 살리고 나머지는 LLM 이 채운다.
        #   (특수기호 제거는 generate_tags 안의 sanitize 가 담당 — 제14조 그대로 준수)
        from JARVIS08_PUBLISH.tags import generate_tags as _gen_tags
        tags = _gen_tags(draft.get("title", theme), draft.get("content", ""),
                         "tistory", seed_tags=[theme, sector])

        def _pub_fn(blocks, title, **_kw):
            return post_to_tistory(
                title=title,
                html_content=draft["content"],
                blocks=blocks,
                category=THEME_CATEGORY,
                preloaded_driver=preloaded_driver,
                tags=tags,
            )

        ok_pub = publish_assembled(draft, _pub_fn, "tistory")
        if ok_pub:
            _tg(f"✅ [THEME-TISTORY] 발행 완료\n제목: {draft['title']}\n테마: {theme}")
            try:
                from shared.bus import on_post_published_detail as _emit
                from JARVIS08_PUBLISH.platforms import last_post_url as _last_url
                _imgs = [str(b[1]) for b in draft["blocks"] if b[0] == "image"]
                _emit(theme=theme, platform="tistory", title=draft["title"],
                      url=_last_url("tistory"),   # ★ ERRORS [482] — URL 누락 시 조회수 수집 불가
                      content=draft["content"], html=draft["html"],
                      source_keyword=theme, post_type="theme",
                      image_paths=_imgs)
            except Exception as e:
                print(f"  ⚠️ [DB] 저장 오류 (무시): {e}")
                _g_report("writer", e, module=__name__)
            return {"success": True, "url": "", "keyword": theme}
        _tg(f"❌ [THEME-TISTORY] 발행 실패\n테마: {theme}")
        return {"success": False, "url": "", "keyword": theme}
    except Exception as e:
        print(f"  ❌ [Theme/Tistory] 발행 예외: {e}")
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        return {"success": False, "url": "", "keyword": theme}


def _publish_naver(draft: dict, theme: str, sector: str) -> dict:
    """네이버 Selenium 발행."""
    if not draft.get("success"):
        return {"success": False, "url": "", "keyword": theme}
    try:
        from JARVIS08_PUBLISH.platforms import post_to_naver
        from JARVIS06_IMAGE.draft_processor import publish_assembled
        # ★ 태그는 JARVIS08 단일 진입점 (2026-07-29). 종전엔 여기서
        #   `[theme, sector, '테마주', '주식', '투자']` 고정 템플릿을 만들었다 —
        #   ① 모든 테마 글이 같은 태그라 검색 변별력 0 ② BLOG_SUPREME_LAW 제1-B조
        #   (고정 풀·고정 템플릿 금지) 위반 ③ 실측 네이버 4개로 NAVER_HASHTAG_MIN(5)
        #   미달이라 post_scorer N7 감점. 테마명·섹터는 seed 로 살리고 나머지는 LLM 이 채운다.
        #   (특수기호 제거는 generate_tags 안의 sanitize 가 담당 — 제14조 그대로 준수)
        from JARVIS08_PUBLISH.tags import generate_tags as _gen_tags
        tags = _gen_tags(draft.get("title", theme), draft.get("content", ""),
                         "naver", seed_tags=[theme, sector])

        def _pub_fn(blocks, title, **_kw):
            return post_to_naver(
                title=title,
                html_content=draft["content"],
                blocks=blocks,
                category=THEME_CATEGORY,
                tags=tags,
            )

        ok_pub = publish_assembled(draft, _pub_fn, "naver")
        if ok_pub:
            _tg(f"✅ [THEME-NAVER] 발행 완료\n제목: {draft['title']}\n테마: {theme}")
            try:
                from shared.bus import on_post_published_detail as _emit
                from JARVIS08_PUBLISH.platforms import last_post_url as _last_url
                _imgs = [str(b[1]) for b in draft["blocks"] if b[0] == "image"]
                _emit(theme=theme, platform="naver", title=draft["title"],
                      url=_last_url("naver"),   # ★ ERRORS [482] — URL 누락 시 조회수 수집 불가
                      content=draft["content"], html=draft["html"],
                      source_keyword=theme, post_type="theme",
                      image_paths=_imgs)
            except Exception as e:
                print(f"  ⚠️ [DB] 저장 오류 (무시): {e}")
                _g_report("writer", e, module=__name__)
            return {"success": True, "url": "", "keyword": theme}
        _tg(f"❌ [THEME-NAVER] 발행 실패\n테마: {theme}")
        return {"success": False, "url": "", "keyword": theme}
    except Exception as e:
        print(f"  ❌ [Theme/Naver] 발행 예외: {e}")
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        return {"success": False, "url": "", "keyword": theme}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  레거시 직접발행 진입점 — ★ 삭제됨 (사용자 박제 2026-07-23)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   `run_tistory_theme` / `run_naver_theme` 는 수집→대본→발행을 **하네스 밖에서**
#   한 벌 더 구현한 복사본이었다 — prepublish 게이트(사실성·매력도)도, Layer 3 검증
#   순환도 타지 않고 곧장 실제 블로그로 나갔다. `JARVIS_ALLOW_LEGACY_PUBLISH=1` 로
#   차단을 스스로 풀던 CLI 가 유일한 호출자.
#   경제(`trend_economic_writer.run_naver/run_tistory`)를 지운 것과 같은 이유·같은 조치
#   (③ 모든 글에 적용). 발행 경로는 `run_all_themes()` 하네스 액션 **하나**.

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  통합 진입점 — run_all_themes (scheduler 가 호출) — 하네스 5-Layer 적용
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _layer3_verify_draft(draft: dict, platform: str) -> list[str]:
    """Layer 3 단일 플랫폼 대본 품질 검증. 위반 메시지 리스트 반환 (0건 = 통과)."""
    issues = []
    blocks = draft.get("blocks") or []
    content = draft.get("content") or ""
    html    = draft.get("html") or ""

    # 블록 최소 수
    if len(blocks) < 3:
        issues.append(f"[{platform}] 블록 수 부족: {len(blocks)}개 (최소 3)")
    # 이미지 블록 최소 1개
    n_img = sum(1 for b in blocks if b[0] == "image")
    if n_img < 1:
        issues.append(f"[{platform}] 이미지 블록 없음")
    # 텍스트 블록 최소 1개
    n_txt = sum(1 for b in blocks if b[0] == "text")
    if n_txt < 1:
        issues.append(f"[{platform}] 텍스트 블록 없음")
    # 본문 최소 길이 (한글 기준 — INDEXER_BODY_MIN = 4문장 ≈ 200자)
    import re as _re
    kor_len = _L.count(content)
    if kor_len < _L.INDEXER_BODY_MIN:
        issues.append(f"[{platform}] 본문 한글 {kor_len}자 — 너무 짧음 (최소 {_L.INDEXER_BODY_MIN}자)")
    # HTML 빈 헤더 검출 (<h2></h2> 등)
    empty_hdrs = _re.findall(r'<h[1-6][^>]*>\s*</h[1-6]>', html)
    if empty_hdrs:
        issues.append(f"[{platform}] 빈 헤더 {len(empty_hdrs)}개 (제3조 위반)")

    # ★ 분량 상한·하한 검증 (2026-07-16 — 경제와 대칭화, 생성-검증 임계 일치)
    #   메시지 형식은 economic_poster 와 동일 유지 — draft_fixer 가 "> N문장" 패턴을
    #   파싱해 인라인 수정하므로 형식이 다르면 수정 루프가 작동하지 않음.
    try:
        from JARVIS02_WRITER.post_type_specs import get_spec as _gs_theme
        _sp = _gs_theme("theme")
        # ★ 2026-07-18: blocks(law_enforcer 정제 후 = 실제 발행 콘텐츠) 우선.
        #   html 은 enforce_supreme_law 이전 원본이라 draft_fixer 의 분량 트림
        #   대상(blocks)과 어긋나 재검증이 stale 상태를 다시 위반으로 보는 문제 방지.
        if isinstance(blocks, list) and blocks:
            _body_v = "".join(
                bd for bt, bd in blocks
                if bt in ("text", "html") and isinstance(bd, str)
            )
        else:
            _body_v = html or content
        _p_tags = _re.findall(r"<p[^>]*>.*?</p>", _body_v, _re.DOTALL | _re.IGNORECASE)
        if _p_tags:
            _sent_cnt = sum(
                len(_re.findall(r'[.!?。]\s*(?=[^<]|$)', _re.sub(r"<[^>]+>", "", p)))
                for p in _p_tags
            )
        else:
            _sent_cnt = _L.count_sentences(_re.sub(r"<[^>]+>", " ", _body_v))
        _kor_total = _L.count(_re.sub(r"<[^>]+>", " ", _body_v))
        # ★ 분량 상한 = OR 기준 (사용자 박제 2026-07-18): 45문장 이하 '또는' 2500자 이하면 통과.
        #   둘 다 초과할 때만 차단 (한쪽만 넉넉해도 발행 허용).
        if _sent_cnt > _sp.max_sentences and _kor_total > _sp.max_korean:
            # ★ '{max}문장' 토큰 유지 필수 (draft_fixer 정규식 계약 — 빠지면 전체 재작성 강등, 2026-07-18 회귀 복구).
            issues.append(f"분량 상한 초과: {_sent_cnt}문장 > {_sp.max_sentences}문장 '그리고' {_kor_total}자 > {_sp.max_korean}자 "
                          f"(OR 기준 — 둘 다 초과 시에만 차단, theme)")
    except Exception:
        pass

    return issues


def run_all_themes(theme: str, sector: str = "", gate_feedback: dict | None = None) -> dict:
    """테마 1개 → 2개 플랫폼 발행 — 하네스 5-Layer 검증 적용 (ADR 009).

    Layer 0: preflight (데몬 부팅 시 완료)
    Layer 1: precondition (theme 비어있지 않음)
    Layer 2: ①규정로드 → ②수집+TS쿠키완료(글작성前보장) → ③NV → ④TS 대본 → ⑤쿠키확인 (★ 네이버 우선 직렬 2026-07-03)
    Layer 3: 로그인 세션 + 2 플랫폼 draft 품질 검증 (최대 5회, 동일 실패 패턴 즉시 차단)
    Layer 4: Tistory/Naver Selenium 순차 발행

    Returns:
        {"theme", "tistory": {...}, "naver": {...}}
    """
    # chart_generator 경로 폐기 — infographic_engine 경로로 통합 (ERRORS [355])

    from JARVIS00_INFRA.harness import (
        action_step, ActionDefinition, run_action, Issue, interpreter_shutting_down,
    )

    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 데몬 재시작으로 인터프리터가 종료 단계면 발행을 *시작하지 않고* 연기.
    # 여기서 시작하면 ②수집 스텝 ThreadPoolExecutor 가 크래시 → 헛된 실패 보고.
    if interpreter_shutting_down():
        print("  ⏸ [THEME] 인터프리터 종료 중(데몬 재시작) — 테마 발행 연기, 재시작 후 재시도")
        return {"theme": theme, "tistory": {"success": False, "url": "", "keyword": theme},
                "naver": {"success": False, "url": "", "keyword": theme},
                "data_empty": False, "shutdown_deferred": True}

    # ── Layer 2 스텝 정의 ────────────────────────────────────

    @action_step(name="① 규정 로드")
    def _step_load_rules(state):
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        sb = _law_blk()
        print("  📜 [① 규정 로드] 헌법 숙지 완료 — 게이트 검증 기준(분량·SEO·매력도)은 대본 단계에서 플랫폼별 합류")
        return {"supreme_block": sb}

    @action_step(name="② 종목·근거 수집")
    def _step_collect(state):
        """공유 수집 — 종목 데이터 + JARVIS09 리서치. 두 플랫폼이 함께 사용.

        ★ 플랫폼 직렬 (사용자 박제 2026-07-03): TS 쿠키 갱신은 여기서 하지 않는다 —
        티스토리 *차례*(액션 2 시작)에 갱신해야 세션이 신선하다 (선로그인 대기 사망 방지).
        """
        # ★ data_empty 재시도 스킵 (ERRORS [174]) — attempt 2+에서 종목 0개가 반복될 경우
        if state.get("_collect_data_empty"):
            print("  ⏭️ [② 수집] 이전 시도 종목 0개 — collect 재실행 스킵 (결과 동일 예상)")
            return {}

        # ★ 수집은 자비스09 단독 (사용자 박제 2026-07-23): 02 는 "이 주제로 수집해줘" 한 줄만.
        #   무엇을·어떤 순서로·어떻게 병렬로·실패 시 무엇으로 폴백할지, 선계산(20:00) 캐시를
        #   재사용할지, 프로필을 자비스03 에서 받아올지 — 전부 09 소관. 02 에는 판단 0.
        from JARVIS09_COLLECTOR import collect_all
        _bundle = collect_all(state["theme"], profile=state.get("theme_profile"),
                              sector=state.get("sector", ""), category="theme")
        collected       = _bundle.get("collected")
        data            = _bundle.get("stocks_data") or {}
        collection_docs = _bundle.get("docs") or []
        evidence_pack   = _bundle.get("evidence_pack") or None
        _n_stocks = len(data.get("stocks") or [])
        _n_facts  = len((evidence_pack or {}).get("facts") or [])

        # ★ 다소스 결손 분리 (사용자 박제 2026-07-04 — 경제 파이프라인과 동렬화, ERRORS [351]):
        #   종목(stocks)이 0개여도 리서치(뉴스·DART·ECOS·웹)만으로 글은 성립한다.
        #   진짜 폐기·테마 교체는 종목·리서치·근거가 *전부* 비었을 때만 (KRX 종속 결합 해제).
        if _bundle.get("data_empty"):
            print("  ⏭️ [② 수집] 종목·리서치·근거 전부 0 — 데이터 없음(테마 교체 대상)")
            return {"collected": collected, "_collect_data_empty": True,
                    "stocks_data": data, "collection_docs": [], "evidence_pack": None}

        if _n_stocks == 0:
            print(f"  ℹ️ [② 수집] 종목 0개지만 리서치 보존 — 문서 {len(collection_docs)}건·근거 {_n_facts}개로 작성 진행")
        print(f"  ✅ [② 수집] 종목 {_n_stocks}개 · 문서 {len(collection_docs)}건 · 근거 {_n_facts}개 | 글 작성 시작")
        return {"collected": collected, "stocks_data": data,
                "collection_docs": collection_docs, "evidence_pack": evidence_pack}

    # ★ 직렬 순서 — 네이버 먼저, 티스토리 나중 (사용자 박제 2026-07-03)
    @action_step(name="③ 네이버 대본 생성")
    def _step_nv_draft(state):
        if state.get("_nv_draft_skip_regen"):
            print("  ⏭️ [③ 네이버] 이전 대본 검증 통과 — 재생성 건너뜀")
            return {}
        collected = state.get("collected")
        # ★ 종목 0개여도 다소스 리서치가 있으면 작성 진행 (경제 동렬화, ERRORS [351] —
        #   차트는 실데이터/AI사진 대체). 종목·리서치·근거 전부 없을 때만 실패.
        if collected is None or not (collected.entities or collected.docs or collected.facts):
            return {"nv_draft": {"success": False, "error": "데이터 없음(종목·리서치 모두 0)", "blocks": [], "content": "", "html": ""}}
        try:
            draft = _build_blocks(
                collected, "naver", NAVER_IMG_DIR,
                supreme_block=state.get("supreme_block"),
                gate_feedback=state.get("_nv_draft_gate_feedback"),
            )
        except Exception as e:
            _g_report("writer", e, module=__name__)
            draft = {"success": False, "error": str(e)[:120], "blocks": [], "content": "", "html": ""}
        return {"nv_draft": draft, "_nv_draft_skip_regen": False}

    @action_step(name="⑤ 티스토리 대본 생성")
    def _step_ts_draft(state):
        if state.get("_ts_draft_skip_regen"):
            print("  ⏭️ [⑤ 티스토리] 이전 대본 검증 통과 — 재생성 건너뜀")
            return {}
        collected = state.get("collected")
        # ★ 종목 0개여도 다소스 리서치가 있으면 작성 진행 (경제 동렬화, ERRORS [351]).
        if collected is None or not (collected.entities or collected.docs or collected.facts):
            return {"ts_draft": {"success": False, "error": "데이터 없음(종목·리서치 모두 0)", "blocks": [], "content": "", "html": ""}}
        try:
            draft = _build_blocks(
                collected, "tistory", TISTORY_IMG_DIR,
                supreme_block=state.get("supreme_block"),
                gate_feedback=state.get("_ts_draft_gate_feedback"),
            )
        except Exception as e:
            _g_report("writer", e, module=__name__)
            draft = {"success": False, "error": str(e)[:120], "blocks": [], "content": "", "html": ""}
        return {"ts_draft": draft, "_ts_draft_skip_regen": False}

    @action_step(name="④ 티스토리 쿠키 갱신")
    def _step_ts_cookie(state):
        """★ 플랫폼 직렬 (2026-07-03): 티스토리 액션 *시작* 시 갱신 — 세션 신선 보장.

        (종전에는 ②에서 선로그인 후 네이버 발행 내내 대기 → 세션 사망 위험, ERRORS [265])
        """
        # ★ 살아있는 핸들은 state 밖 (ERRORS [544]) — state 엔 키 문자열만.
        #   경제(economic_poster)와 **동일 규약** (원칙③ — 4조합 전부).
        from JARVIS00_INFRA import resources as _res
        from JARVIS00_INFRA.harness import ACTION_NAME_KEY as _ANK
        if _res.get(state.get("ts_driver_key")) is not None:
            print("  ⏭️ [④] 티스토리 driver 이미 준비됨 (재시도 — 재갱신 스킵)")
            return {}
        try:
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr
            ok, drv = _tcr(force=False, return_driver=True)
            if ok:
                load_dotenv(override=True)
                print("  ✅ [④] 티스토리 쿠키 갱신 완료 (신선 세션)")
                return {"ts_driver_key": _res.put(state.get(_ANK, ""), "ts_driver", drv)}
            if drv:
                try:
                    drv.quit()
                except Exception:
                    pass
        except Exception as e:
            print(f"  ❌ [④] 티스토리 쿠키 갱신 예외: {e}")
            _g_report("writer", e, module=__name__)
        print("  ⚠️ [④] 티스토리 driver 없음 — 발행 시 재로그인 폴백")
        return {"ts_driver_key": ""}

    # ── Layer 3 검증·수정 — ★ 플랫폼 단위 (사용자 박제 2026-07-03: 끝까지 직렬) ──

    def _verify_theme_platform(state, platform: str, draft_key: str, step_name: str,
                               check_data: bool = False):
        """Layer 3 — *단일 플랫폼* 대본 검증. list[Issue] 반환 (빈 리스트 = 통과)."""
        issues = []

        # [L1] 로그인 세션 검증 — ★ 리뷰 확정 수정 (2026-07-03): dict 반환은 항상
        #   truthy 라 종전 체크 사문. *해당 플랫폼* ok 직접 판정.
        try:
            from JARVIS08_PUBLISH.credentials.login_manager import (
                auto_refresh_if_needed as _auto_refresh,
                verify_all_logins      as _verify_logins,
            )
            _auto_refresh(platforms=(platform,))   # 현재 플랫폼만 갱신
            _login_res = _verify_logins(platforms=(platform,)) or {}  # 현재 플랫폼만 확인 (Naver 검증 중 Tistory 건드리지 않음)
            _pl = _login_res.get(platform) or {}
            if not _pl.get("ok", True):   # 구조 변경 시 fail-open
                _why = "; ".join(_pl.get("issues") or ["재로그인 필요"])[:150]
                issues.append(Issue(step="① 전제조건", kind="login_invalid",
                    detail=f"{platform} 로그인 세션 무효 — {_why}"))
        except Exception as _le:
            issues.append(Issue(step="① 전제조건", kind="login_error",
                detail=f"로그인 확인 오류: {_le}"))

        # [L2] 종목 데이터 유효성 (공유 수집을 실행한 네이버 액션에서만)
        if check_data:
            sd = state.get("stocks_data") or {}
            if not sd.get("stocks"):
                issues.append(Issue(step="② 종목·근거 수집", kind="data_empty",
                    detail="종목 데이터 0개 — 수집 실패"))

        # [L3] 단일 플랫폼 대본 규정 준수 검증 (순수 "발견"만)
        draft = state.get(draft_key) or {}
        if not draft.get("success"):
            # ★ 인프라 스로틀(일시적)과 콘텐츠 결함 분리 — 판정은 harness 단독
            #   (`classify_failure_issue`). 종전엔 이 블록이 economic_poster 에도
            #   **그대로 복사**돼 있어 한쪽만 고치면 다른 쪽에서 재발했다
            #   (CLAUDE.md ①단일 진입점·③모든 곳 적용 위반, 2026-07-25 통합).
            from JARVIS00_INFRA.harness import classify_failure_issue as _classify_fail
            issues.append(_classify_fail(step_name, draft.get("error")))
            return issues
        di_list = _layer3_verify_draft(draft, platform)
        for di in di_list:
            issues.append(Issue(step=step_name, kind="draft_quality", detail=di))
        # ★ 발행 전 품질 게이트 (2026-06-28) — 구조 검증 통과 시에만.
        if not di_list:
            from JARVIS02_WRITER.prepublish_gate import prepublish_quality_issues
            # ★ ADR 012 — 사실성 게이트 대조군에 근거 팩(fact 단위·출처 박제) 합류
            _src_docs = list(state.get("collection_docs") or [])
            try:
                if state.get("evidence_pack"):
                    from JARVIS09_COLLECTOR.evidence_pack import as_source_docs
                    _src_docs = _src_docs + as_source_docs(state["evidence_pack"])
            except Exception:
                pass
            # ★ 종목 실측 재무를 grounding 코퍼스에 합류 (ERRORS [343] — 수집된 실데이터
            #   시가총액·현재가·PER 등이 출처 코퍼스에 없어, 진실한 수치인데도
            #   "출처·웹 모두 확인 불가"로 false-positive 차단되던 갭. 경제글이
            #   market_data 를 ground truth 로 넘기는 것과 동일하게, 테마글은
            #   stocks_data(네이버 금융/KRX 실측)를 groundable 텍스트로 합류시킨다.)
            try:
                _sd = state.get("stocks_data") or {}
                if _sd.get("stocks"):
                    # ★ 2026-07-23: 09 가 조립해준 collected.datasets 를 *읽기만* 한다.
                    #   종전엔 여기서 stocks_to_datasets 를 다시 돌려 09 의 조립을 재현했고,
                    #   그 사이 09 가 정책으로 걸러낸 항목이 코퍼스에서 되살아날 수 있었다.
                    _all_ds = list(getattr(state.get("collected"), "datasets", None) or [])

                    def _fmt_val(v):
                        # ★ ERRORS [346] — 코퍼스 수치를 본문 표기와 정합.
                        #   본문은 "461,500원"(천단위 콤마)로 쓰는데 승격값은
                        #   461500.0(round(nd=0) float .0) → 진실한 현재가인데도
                        #   grounding LLM 이 매칭 실패 → "출처·웹 모두 확인 불가" 오차단.
                        #   정수 실수는 천단위 콤마 정수로, 소수(5.9·13.6)는 그대로.
                        if isinstance(v, float) and v.is_integer():
                            return f"{int(v):,}"
                        if isinstance(v, int):
                            return f"{v:,}"
                        return f"{v}"

                    _stock_docs = []
                    for _ds in _all_ds:
                        _unit = _ds.get("unit", "")
                        _rows = ", ".join(
                            f"{_r['label']} {_fmt_val(_r['value'])}{_unit}"
                            for _r in _ds.get("data", []) if isinstance(_r, dict) and "label" in _r)
                        if _rows:
                            _stock_docs.append(
                                f"[수집 실측] {_ds.get('title', '')}: {_rows} "
                                f"(출처: {(_ds.get('source') or {}).get('name', 'KRX 시세')})")
                    # ★ ERRORS [347] — 조원 필드(marcap·revenue)를 본문 표기와 정합.
                    #   본문(_stocks_text→프로즈)은 `_fmt_marcap` 으로 규모별 조원(대형주
                    #   5.9조원)/억원(소형주 2,644억원)을 택하는데, stocks_to_datasets 는
                    #   항상 조원 단일 단위(0.26조원)로만 렌더 → 소형주 억원 표기가 코퍼스에
                    #   없어 진실 시가총액이 grounding false-positive 로 오차단([346] 단위 변종,
                    #   nd 자리 정합만으론 미해결). 본문 정본 포맷터(_fmt_marcap)로 두 단위
                    #   (조원·억원)를 코퍼스에 합류 — 종목 규모 무관 표기 정합 보증.
                    try:
                        from JARVIS02_WRITER.draft_writer import _fmt_marcap as _fmc
                        for _s in _sd.get("stocks", []):
                            if not isinstance(_s, dict):
                                continue
                            _nm = str(_s.get("name") or "").strip()
                            if not _nm:
                                continue
                            # ★ '연매출' 거짓 라벨 방지 (ERRORS [367]): 네이버 재무는 최근 *분기*.
                            #   fin_period 있으면 기간 명시, 없으면 '최근 실적'. grounding 코퍼스가
                            #   정확해야 본문도 정확한 기간으로 작성·검증됨.
                            _fp = str(_s.get("fin_period") or "").strip()
                            _rev_lb = f"매출액({_fp} 기준)" if _fp else "매출액(최근 실적)"
                            _flds = []
                            for _f, _lb in (("marcap", "시가총액"), ("revenue", _rev_lb)):
                                try:
                                    _mv = float(_s.get(_f) or 0)
                                except (TypeError, ValueError):
                                    _mv = 0.0
                                if _mv >= 1e8:
                                    _flds.append(f"{_lb} {_fmc(_mv)}({_mv/1e8:,.0f}억원)")
                                elif _mv > 0:
                                    _flds.append(f"{_lb} {_fmc(_mv)}")
                            if _flds:
                                _stock_docs.append(
                                    f"[종목 실측] {_nm}: {', '.join(_flds)} "
                                    f"(출처: 네이버 금융 재무제표·시세)")
                    except Exception:
                        pass
                    # ★ ERRORS [346] — 최고 신뢰 ground truth 는 코퍼스 *앞* 에 배치.
                    #   collection_docs(수만 자)가 _FACT_SOURCE_CORPUS_CAP(12000자)로
                    #   잘리면 뒤에 붙인 실측 수치가 코퍼스에서 탈락 → 진실 수치 오차단.
                    #   앞에 두어 [343] grounding 승격을 truncation 으로부터 보증.
                    _src_docs = _stock_docs + _src_docs
            except Exception:
                pass
            for q in prepublish_quality_issues(
                    draft, post_type="theme", platform=platform,
                    source_docs=_src_docs,
                    market_data=None,
                    stocks_data=state.get("stocks_data"),   # ★ 1-c 실측 재무 ±10% 밴드
                    collected=state.get("collected")):      # ★ Step 10: 통일 grounding
                issues.append(Issue(step=step_name, kind=q["kind"], detail=q["detail"]))
        return issues

    def _fix_theme_platform(state: dict, issues: list, platform: str,
                            draft_key: str, step_name: str) -> tuple:
        """harness fix 훅 — *단일 플랫폼* draft_quality 인라인 패치 + GUARDIAN 학습.

        회복 불가(data_empty)는 kind="abort" 즉시 반환 → 상위가 테마 교체.
        draft_failed 는 재생성 순환에 맡긴다 (LLM 재시도 기회 — _LLM_SKIP_PATTERNS 가
        반복 거부 테마를 별도 차단).
        """
        from JARVIS02_WRITER.draft_fixer import fix_and_learn as _fx
        raw_strs = [i.detail for i in issues
                    if i.kind == "draft_quality" and i.step == step_name]
        non_draft = [i for i in issues
                     if not (i.kind == "draft_quality" and i.step == step_name)]
        fixed_all: list = []
        unfixed_all: list = list(non_draft)

        # ★ 게이트 차단 사유 → 재작성 프롬프트 피드백 (ERRORS [311] — 미전달 시
        #   같은 창작 수치를 재생산해 max_attempts 그대로 소진)
        _gate_details = [i.detail for i in non_draft
                         if i.kind in ("factuality", "engagement") and i.detail]
        if _gate_details:
            _fb = list(state.get(f"_{draft_key}_gate_feedback") or [])
            for d in _gate_details:
                if d not in _fb:
                    _fb.append(d)
            state[f"_{draft_key}_gate_feedback"] = _fb[-8:]

        # ★ 사실성 차단 → 영구 학습 인사이트 (ERRORS [454] 재발 대응 — 2026-07-24)
        #   위 gate_feedback 은 state 딕셔너리(이번 harness 실행 한정)라 프로세스가
        #   끝나면 사라진다. 같은 테마가 며칠 뒤 새 harness 실행으로 다시 돌면
        #   LLM 이 동일한(또는 숫자만 바뀐) 산업 총계 수치를 파라메트릭 지식에서
        #   그대로 재생성 — draft_writer 의 절대제약 문구만으론 확률적으로 못 막는다.
        #   차단된 주장을 learning_insights(scope='theme')에 영구 기록해
        #   _load_learn_insights 가 이후 *모든* 테마 실행에서 프롬프트에 재주입하게 한다.
        _fact_details = [i.detail for i in non_draft if i.kind == "factuality" and i.detail]
        if _fact_details:
            try:
                from shared.db import upsert_learning_insight
                _theme = str(state.get("theme") or "").strip()
                for d in _fact_details:
                    _key = f"factuality_block:{_theme}:{d[:60]}"
                    upsert_learning_insight(
                        insight_key=_key,
                        insight_type="avoid",
                        description=f"[{_theme}] 사실성 게이트 차단 이력: {d[:120]}",
                        directive=f"다음 주장·수치(또는 비슷한 변형)는 출처가 확인되지 않아 "
                                  f"과거 발행이 차단됐다 — 다시 쓰지 말 것: {d[:200]}",
                        weight=1.0, scope="theme",
                    )
            except Exception as _e:
                print(f"  ⚠️ 사실성 차단 인사이트 영구화 실패(무시): {_e}")

        # 재생성 필요성 표시 (재시도 시 이미지 폴더 불필요 리셋 방지)
        # ★ 리뷰 확정 수정 (2026-07-03): 해당 step 의 *어떤* 이슈든(draft_failed 뿐 아니라
        #   prepublish 게이트 factuality/engagement 포함) 있으면 skip 금지 — 재작성 순환 보존.
        if raw_strs:
            fixed_strs, unfixed_strs = _fx(state, draft_key, platform, raw_strs, "theme")
            for s in fixed_strs:
                fixed_all.append(Issue(step=step_name, kind="draft_fixed", detail=s))
            for s in unfixed_strs:
                unfixed_all.append(Issue(step=step_name, kind="draft_invalid", detail=s))
        # ★ 진짜결함 수정 (재현테스트로 발견): skip_regen 을 raw_strs(구조 이슈) 인라인
        #   패치 성공 여부만으로 판단하면, 같은 step 의 factuality/engagement 이슈가
        #   non_draft 에 남아있어도 skip_regen=True 로 덮어써져 대본이 영원히 재생성
        #   되지 않는 무한 루프 발생(매력도 미달이 재검증마다 재발해도 대본 불변 —
        #   "attempt=1 step=③ 대본: 매력도 미달" 이 재시도에서도 그대로 반복되는 원인).
        #   unfixed_all(구조+게이트 통틀어) 에 이 step 이슈가 하나라도 남아있는지로
        #   단일 판단 — 이 step 이 완전히 깨끗할 때만 재생성 스킵.
        _remaining_step_issue = any(i.step == step_name for i in unfixed_all)
        state[f"_{draft_key}_skip_regen"] = not _remaining_step_issue

        # ★ 회복 불가 조건 → abort (harness 즉시 차단, 2차 시도 낭비 없음)
        #   ★ 2026-07-24 P1: data_insufficient(이미지 사실성 — 수집 datasets 부족) 도 동일 —
        #   재시도는 collect step 을 건너뛰어 datasets 불변이라 재작성으로 충족 불가.
        _has_data_empty = any(i.kind == "data_empty" for i in non_draft)
        _has_data_insuff = any(i.kind == "data_insufficient" for i in non_draft)
        _has_login_issue = any(i.kind in ("login_invalid", "login_error") for i in non_draft)
        if (_has_data_empty or _has_data_insuff) and not _has_login_issue:
            _reason = ("종목 데이터 0개 — 다른 테마로 전환 필요" if _has_data_empty
                       else "검증 데이터 부족(이미지 사실성) — 재작성으로 충족 불가")
            print(f"  ⚡ [fix] 회복 불가 확정 → abort: {_reason}")
            return fixed_all, [Issue(step="전체", kind="abort", detail=_reason)]
        return fixed_all, unfixed_all

    # ── Layer 4 발행 — ★ 플랫폼 단위 (사용자 박제 2026-07-03: 끝까지 직렬) ──────

    def _send_theme_platform(state, platform: str, draft_key: str,
                             result_key: str, attempted_key: str):
        """Layer 4 — *단일 플랫폼* 발행. 실패 시 raise → 이 플랫폼만 검증 순환 재진입.

        ★ 센티널 (ERRORS [265]): attempted 플래그는 시도 *전* 설정 (이중 발행 방지),
          attempt>=2 + 이전 실패(success=False) → 플래그 해제 → 진짜 재발행 기회.
        """
        _theme  = state["theme"]
        _sector = state["sector"]
        send_attempt = state.get("__send_attempt__", 0) + 1
        state["__send_attempt__"] = send_attempt
        print(f"\n  📤 [Phase 2] {platform} 발행 (send_attempt={send_attempt})")
        published = state.setdefault("published_platforms", set())

        # ★ attempt >= 2 + 이전 실패 → 플래그 해제 (진짜 재발행, ERRORS [265])
        if (send_attempt >= 2 and platform not in published
                and state.get(attempted_key)
                and not (state.get(result_key) or {}).get("success")):
            print(f"  🔄 [{platform}] 이전 발행 실패 → 플래그 해제·재발행")
            state[attempted_key] = False

        if platform in published:
            print(f"  ⏭ {platform} 이미 발행 완료 (재시도 스킵)")
            return
        if state.get(attempted_key):
            # 시도 플래그 잔존 + 해제 미발동(=성공 잔존) — 이중 발행 방지
            print(f"  ⚠️ {platform} 발행 이미 시도 완료 (이중 방지)")
            published.add(platform)
            return

        state[attempted_key] = True  # 반드시 시도 *전* 에 설정
        if platform == "naver":
            res = _publish_naver(state.get(draft_key, {}), _theme, _sector)
        else:
            from JARVIS00_INFRA import resources as _res_p
            _ts_drv = _res_p.get(state.get("ts_driver_key"))
            if _ts_drv is not None:
                try:
                    _ = _ts_drv.title   # 세션 생존 확인
                except Exception:
                    print("  ⚠️ 티스토리 driver 세션 만료 — 발행 시 재로그인")
                    _ts_drv = None
            res = _publish_tistory(state.get(draft_key, {}), _theme, _sector,
                                   preloaded_driver=_ts_drv)
        state[result_key] = res
        if res.get("success"):
            published.add(platform)
        print(f"  {'✅' if res.get('success') else '❌'} [{platform}] 테마 발행: {_theme}")

        # ★ strict: 미발행이면 raise → 이 플랫폼만 검증 순환 재진입 (타 플랫폼 무영향)
        if platform not in published:
            raise RuntimeError(
                f"[Layer4] ['{platform}'] 발행 실패 (theme={_theme}) — 송출 미완료 → 검증 순환 재진입"
            )

    # ── 하네스 실행 ──────────────────────────────────────────

    print(f"\n{'='*60}\n  ★ 테마 통합 발행 시작: {theme}\n{'='*60}")
    _tg(f"📝 [THEME] 테마 발행 시작: *{theme}*")

    def _precondition(s):
        # precondition은 list[Issue] 반환이 규약 — bool이 아님
        if not s.get("theme"):
            return [Issue(step="입력 확인", kind="missing_input",
                          detail="theme 미입력 — run_all_themes(theme=...) 확인 필요")]
        return []

    # ── ★ 플랫폼 단위 끝까지 직렬 (사용자 박제 2026-07-03) ──────────────────
    # 네이버 액션(공유 수집 포함): ①규정 → ②수집 → ③NV대본 → 검증 순환 → 발행 [종결]
    #   → 티스토리 액션: ④TS쿠키(신선 로그인) → ⑤TS대본 → 검증 순환 → 발행
    # 한쪽의 재작성 순환·실패가 다른 쪽을 지연·차단하지 않음 (실패 격리, max_attempts 각 3 — 사용자 지시로 3회 통일)
    _nv_action_def = ActionDefinition(
        name=f"theme-publish-{theme}-naver",
        # ★ escalation "지금 다시 실행" 버튼 대상 (ERRORS [544])
        retry_job_id="j01_theme_post_21",
        steps=[_step_load_rules, _step_collect, _step_nv_draft],
        verify=lambda st: _verify_theme_platform(st, "naver", "nv_draft",
                                                 "③ 네이버 대본 생성", check_data=True),
        fix=lambda st, iss: _fix_theme_platform(st, iss, "naver", "nv_draft",
                                                "③ 네이버 대본 생성"),
        send=lambda st: _send_theme_platform(st, "naver", "nv_draft",
                                             "nv_pub_result", "__nv_send_attempted__"),
        precondition=_precondition,
        # ★ max_attempts 미지정 = harness.DEFAULT_MAX_ATTEMPTS 상속 (SSOT, 현재 2회).
        #   하드코딩하면 상한 변경 시 여기가 누락된다. sentinel(__nv_send_attempted__)이 중복 발행 방지
        deadline_sec=BLOG_ACTION_DEADLINE_SEC,   # ★ 블로그(플랫폼)당 SSOT (watchdog.py) — 사용자 박제 2026-07-06
    )
    _ts_action_def = ActionDefinition(
        name=f"theme-publish-{theme}-tistory",
        # ★ escalation "지금 다시 실행" 버튼 대상 (ERRORS [544])
        retry_job_id="j01_theme_post_21",
        steps=[_step_ts_cookie, _step_ts_draft],
        verify=lambda st: _verify_theme_platform(st, "tistory", "ts_draft",
                                                 "⑤ 티스토리 대본 생성"),
        fix=lambda st, iss: _fix_theme_platform(st, iss, "tistory", "ts_draft",
                                                "⑤ 티스토리 대본 생성"),
        send=lambda st: _send_theme_platform(st, "tistory", "ts_draft",
                                             "ts_pub_result", "__ts_send_attempted__"),
        precondition=_precondition,
        # ★ max_attempts 미지정 = harness.DEFAULT_MAX_ATTEMPTS 상속 (SSOT, 현재 2회).
        #   sentinel(__ts_send_attempted__)이 중복 발행 방지
        deadline_sec=BLOG_ACTION_DEADLINE_SEC,   # ★ 블로그(플랫폼)당 SSOT (watchdog.py) — 사용자 박제 2026-07-06
    )

    # ★ 단일 진입점 — 새 테마 = 전체 상태 초기화
    from JARVIS09_COLLECTOR.run_context import new_run as _new_run
    _new_run(theme)

    # ★ 발행 기간 LLM 우선권 선언 — background alias 자동 강등
    #   ★ 2026-07-25 refcount 안전화: mark_publishing(True/False) 손 짝맞춤 → publishing() CM.
    #     예외·조기 return·deferred 어느 경로로 나가도 finally 가 창을 닫는다.
    #     (누수 1회 = GUARDIAN 자동수정·모든 background LLM 이 데몬 재시작까지 영구 보류)
    with publishing("theme"):
        import time as _tm_act
        # ★ 액션별 LLM 데드라인 (economic_poster.py 와 동일 SSOT 패턴 — ERRORS [438][440][441]류
        #   재발 방지): 반드시 _nv_action_def.deadline_sec 과 동일한 BLOG_ACTION_DEADLINE_SEC 사용.
        #   더 큰/다른 값을 쓰면 "잔여 <10분 강등"이 harness 하드 데드라인보다 늦게 트리거되어
        #   watchdog 이 재시도·백오프 도중 강제 종료한다.
        os.environ["JARVIS_LLM_DEADLINE_TS"] = str(_tm_act.time() + BLOG_ACTION_DEADLINE_SEC)
        # ① 네이버 액션 (공유 수집 포함) — 완전 종결까지
        #   ★ gate_feedback: GUARDIAN 재시도가 물려준 직전 차단사유 — 첫 시도부터 보완 재작성
        #     (경제 economic_poster.run(resume=) 과 동일 규약. ③ 모든 글 적용)
        _gfb = gate_feedback or {}
        _nv_result = run_action(_nv_action_def, {
            "theme": theme, "sector": sector,
            "_nv_draft_gate_feedback": list(_gfb.get("naver") or []),
        })
        _nv_st = _nv_result.state
        _nv_res = _nv_st.get("nv_pub_result", {"success": False, "url": "", "keyword": theme})
        # ★ 리뷰 확정 수정 (2026-07-03): data_empty 는 *수집이 실행되어 비었을 때만* —
        #   precondition 실패·동시성 차단 등 수집 미실행을 테마 교체로 오분류 금지.
        _sd = _nv_st.get("stocks_data")
        _stocks_ok = bool((_sd or {}).get("stocks"))
        _data_empty = bool(_nv_st.get("_collect_data_empty")) or (_sd is not None and not _stocks_ok)
        _deferred = bool(getattr(_nv_result, "deferred", False))
        if not _nv_result.delivered and not _deferred:
            _reason = getattr(_nv_result, "escalation_reason", "최대 시도 초과 또는 abort")
            _tg(f"❌ [THEME] 네이버 발행 최종 실패\n테마: {theme}\n사유: {_reason}")
        if _deferred:
            print("  ⏸ [THEME] 네이버 액션 연기(인터프리터 종료) — 티스토리·보고 스킵, 재시작 후 재시도")
            return {"theme": theme,
                    "tistory": {"success": False, "url": "", "keyword": theme},
                    "naver": _nv_res, "data_empty": False, "shutdown_deferred": True}

        # ② 티스토리 액션 — 네이버 *종결 후* 시작. 종목 데이터 없으면 스킵
        #    (진짜 data_empty → 상위 테마 교체 / 수집 미실행 → 교체 아닌 단순 실패)
        _ts_res = {"success": False, "url": "", "keyword": theme}
        _ts_deferred = False
        if not _stocks_ok:
            print(f"  ⏭️ [티스토리] 종목 데이터 {'0개' if _data_empty else '미수집(네이버 액션 조기 종결)'} — 발행 스킵")
        else:
            # ★ _ts_action_def.deadline_sec 과 동일한 SSOT 상수 (위 네이버 리셋과 동일 사유) —
            #   네이버 액션 소요로 흘러간 시간만큼 티스토리 액션에도 신선한 예산을 부여.
            os.environ["JARVIS_LLM_DEADLINE_TS"] = str(_tm_act.time() + BLOG_ACTION_DEADLINE_SEC)
            _ts_result = run_action(_ts_action_def, {
                "theme": theme, "sector": sector,
                "collected": _nv_st.get("collected"),          # ★ Step 7: 액션1 → 액션2 전달
                "stocks_data": _nv_st.get("stocks_data"),      # back-compat (verify 등)
                "collection_docs": _nv_st.get("collection_docs") or [],
                "evidence_pack": _nv_st.get("evidence_pack"),
                "supreme_block": _nv_st.get("supreme_block"),
                "_ts_draft_gate_feedback": list(_gfb.get("tistory") or []),
            })
            _ts_st = _ts_result.state
            _ts_res = _ts_st.get("ts_pub_result", {"success": False, "url": "", "keyword": theme})
            if not _ts_result.delivered:
                _reason = getattr(_ts_result, "escalation_reason", "최대 시도 초과 또는 abort")
                if getattr(_ts_result, "deferred", False):
                    # ★ rank8: 인프라 스로틀 지속 — 하드 실패 아님. 다음 회차 자연 재시도.
                    _ts_deferred = True
                    print(f"  ⏸ [THEME] 티스토리 인프라 스로틀 지속 — 발행 연기(다음 회차 재시도)")
                    _tg(f"⏸ [THEME] 티스토리 인프라 스로틀 지속 — 발행 연기, 다음 회차 재시도\n테마: {theme}")
                else:
                    _tg(f"❌ [THEME] 티스토리 발행 최종 실패\n테마: {theme}\n사유: {_reason}")


    # ★ 차단사유를 밖으로 (2026-07-25): GUARDIAN 재시도가 *무엇이 부족했는지* 를 물려받아
    #   같은 테마로 보완 재작성할 수 있게 한다. 경제 EP_RESULT_FILE["harness_issues"] 와 동일 규약.
    def _issues_of(res) -> list:
        out = []
        for _hist in (getattr(res, "issues_history", None) or []):
            for _iss in _hist:
                out.append(f"{getattr(_iss,'step','?')}: {getattr(_iss,'kind','?')}: "
                           f"{getattr(_iss,'detail','?')[:120]}")
        return out[-8:]

    return {"theme": theme, "tistory": _ts_res, "naver": _nv_res, "data_empty": _data_empty,
            "tistory_deferred": _ts_deferred,
            "issues": {"naver": _issues_of(_nv_result),
                       "tistory": _issues_of(_ts_result) if _stocks_ok else []}}


__all__ = [
    "run_all_themes",
    "publishing",      # ★ 발행창 표시 CM — economic_poster 가 파생해 쓴다 (①단일 진입점)
]


# ── 직접 실행 진입점 ──────────────────────────────────────
if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    # ★ 우회 환경변수(JARVIS_ALLOW_LEGACY_PUBLISH) 폐기 (2026-07-23):
    #   그 변수는 *하네스 검증 순환을 건너뛰는 발행* 을 CLI 가 스스로 허용하던 스위치였다.
    #   레거시 직접발행 함수를 지운 지금 우회할 대상 자체가 없다. CLI 도 하네스로만 나간다.
    #   플랫폼 단독(--naver-only/--tistory-only) 도 폐기 — run_all_themes 가 플랫폼별
    #   독립 액션으로 실행하며 한쪽 실패가 다른 쪽을 막지 않는다.
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("theme", help="테마명 (예: '반도체')")
    p.add_argument("--sector", default="", help="섹터 (선택)")
    args = p.parse_args()

    # ★ 재시도 이어받기 — 부모가 env 로 넘긴 직전 차단사유 (실행모델 통일 2026-07-25).
    #   프로세스 경계를 넘어야 하므로 메모리가 아니라 env(문자열 JSON)로 받는다.
    _gate_fb = None
    try:
        _raw_fb = os.environ.get("JARVIS_GATE_FEEDBACK", "").strip()
        if _raw_fb:
            import json as _json_fb
            _gate_fb = _json_fb.loads(_raw_fb)
    except Exception as _fe:
        print(f"⚠️ gate_feedback 파싱 실패(무시): {_fe}")

    # ★ 정지 방어 (사용자 박제 2026-07-06): 일회성 발행 작업 freeze/deadline 가드.
    from JARVIS00_INFRA.watchdog import guard_main
    with guard_main("테마 발행", deadline_sec=2 * BLOG_ACTION_DEADLINE_SEC + 600):   # 부모 backstop — 플랫폼당 데드라인×2 + 여유
        r = run_all_themes(args.theme, args.sector, gate_feedback=_gate_fb)

        # ★ 결과를 부모에게 — 경제(EP_RESULT_FILE)와 *동일 규약* (실행모델 통일 2026-07-25).
        #   함수 반환값은 프로세스 경계를 못 넘는다. 파일이 유일한 통로.
        _res_file = os.environ.get("JARVIS_EP_RESULT_FILE", "")
        if _res_file:
            try:
                import json as _json_r
                with open(_res_file, "w", encoding="utf-8") as _rf:
                    _json_r.dump(r, _rf, ensure_ascii=False, default=str)
            except Exception as _re:
                print(f"⚠️ 결과 파일 기록 실패: {_re}")

        ok = any(r.get(p, {}).get("success") for p in ("tistory", "naver"))
        sys.exit(0 if ok else 1)
