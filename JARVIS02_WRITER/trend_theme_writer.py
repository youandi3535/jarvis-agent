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

진입점:
  run_all_themes(theme)  — 데몬·scheduler 가 호출. 2개 플랫폼 통합 발행.
  run_naver_theme(theme, sector="", ts_keyword="") -> dict
  run_tistory_theme(theme, sector="") -> dict
"""
from __future__ import annotations

import os
import sys
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# ── sys.path 보정 (subprocess 직접 실행과 데몬 모듈 로드 양쪽 호환) ──
_JARVIS_ROOT = Path(__file__).parent.parent
if str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))

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
#  ① 데이터 수집 — collect_stocks_data 위임
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _collect(theme: str) -> dict:
    """테마 키워드 → 종목 데이터. collect_theme.collect_stocks_data 위임."""
    # ★ 단일 진입점 — 새 테마 = 전체 상태 초기화
    from JARVIS09_COLLECTOR.run_context import new_run as _new_run
    _new_run(theme)
    try:
        from JARVIS02_WRITER.collect_theme import collect_stocks_data
        return collect_stocks_data(theme)
    except Exception as e:
        print(f"  ❌ [theme] collect_stocks_data 실패: {e}")
        _g_report("writer", e, module=__name__)
        return {"theme": theme, "stocks": [], "summary": {}}


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
        return {"success": False, "error": "Pass-1 대본 생성 실패", "blocks": [],
                "title": "", "content": "", "html": ""}

    # ── JARVIS06: 이미지 생성 + 블록 조립 (process_draft v2 — collected) ──────
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(draft_html, collected=collected, platform=platform, out_dir=img_dir)
    blocks = result["blocks"]
    html   = result["html"]
    title  = result["title"]

    # 썸네일 맨 앞에 추가
    thumb = result.get("thumbnail_path")
    if thumb and Path(thumb).exists():
        blocks = [("image", str(thumb))] + blocks
        print(f"  ✅ 썸네일: {Path(thumb).name}")

    # ── 품질 검증 ───────────────────────────────────────────────
    try:
        from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
        blocks = enforce_text_between_images(blocks, source=f'THEME-{platform.upper()}')
    except Exception as e:
        _g_report("writer", e, module=__name__)
    try:
        from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
        blocks, viols = enforce_supreme_law(blocks, platform, f"THEME-{platform.upper()}")
        notify_violations(viols, platform, f"THEME-{platform.upper()}")
    except Exception as e:
        _g_report("writer", e, module=__name__)

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

        # ★ 사용자 박제 2026-05-15 — 태그 특수기호 절대 금지 (제14조 단일 진입점)
        from shared.seo import sanitize_tags as _stg
        tags = _stg([theme, sector, '테마주', '주식', '투자'])
        ok_pub = post_to_tistory(
            title=draft["title"],
            html_content=draft["content"],
            blocks=draft["blocks"],
            category=THEME_CATEGORY,
            preloaded_driver=preloaded_driver,
            tags=tags,
        )
        if ok_pub:
            _tg(f"✅ [THEME-TISTORY] 발행 완료\n제목: {draft['title']}\n테마: {theme}")
            try:
                from shared.bus import on_post_published_detail as _emit
                _imgs = [str(b[1]) for b in draft["blocks"] if b[0] == "image"]
                _emit(theme=theme, platform="tistory", title=draft["title"],
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
        # ★ 사용자 박제 2026-05-15 — 태그 특수기호 절대 금지 (제14조 단일 진입점)
        from shared.seo import sanitize_tags as _stg
        tags = _stg([theme, sector, '테마주', '주식', '투자'])
        ok_pub = post_to_naver(
            title=draft["title"],
            html_content=draft["content"],
            blocks=draft["blocks"],
            category=THEME_CATEGORY,
            tags=tags,
        )
        if ok_pub:
            _tg(f"✅ [THEME-NAVER] 발행 완료\n제목: {draft['title']}\n테마: {theme}")
            try:
                from shared.bus import on_post_published_detail as _emit
                _imgs = [str(b[1]) for b in draft["blocks"] if b[0] == "image"]
                _emit(theme=theme, platform="naver", title=draft["title"],
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
#  진입점 — 플랫폼별 단일 함수 (외부에서 직접 호출 가능)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_tistory_theme(theme: str, sector: str = "",
                      stocks_data: dict | None = None) -> dict:
    """테마주 티스토리 발행 — ⓪ 쿠키 갱신 → ①~⑦ → ⑧ 발행 (driver 재사용).

    ★ P0-② 테마 버전 (사용자 박제 2026-05-18) — harness 외부 호출 차단.
    """
    from JARVIS02_WRITER.trend_economic_writer import _legacy_publish_guard as _gd
    _gd("run_tistory_theme")
    print(f"\n  🔴 [THEME-TISTORY] 테마 발행 시작: {theme}")

    # ── ⓪ 쿠키 사전 갱신 (★ 글 작성 전 항상 — 사용자 직접 박제 2026-05-14) ──
    print(f"  🍪 [THEME-TISTORY] ⓪ 글 작성 전 쿠키 강제 갱신")
    _preloaded_driver = None
    try:
        from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr_run
        ok, _preloaded_driver = _tcr_run(force=True, return_driver=True)
        if not ok:
            _tg(f"❌ [THEME-TISTORY] 쿠키 갱신 실패 — 글 작성 중단: {theme}")
            if _preloaded_driver:
                try: _preloaded_driver.quit()
                except Exception: pass
            return {"success": False, "url": "", "keyword": theme, "error": "쿠키 갱신 실패"}
        load_dotenv(override=True)
        print(f"  ✅ [THEME-TISTORY] ⓪ 쿠키 갱신 완료 — driver 재사용")
    except Exception as e:
        print(f"  ❌ [THEME-TISTORY] ⓪ 쿠키 갱신 예외: {e}")
        _g_report("writer", e, module=__name__)
        if _preloaded_driver:
            try: _preloaded_driver.quit()
            except Exception: pass
        return {"success": False, "url": "", "keyword": theme, "error": str(e)[:100]}

    # ── ① 데이터 수집 ───────────────────────────────────────────
    if stocks_data is None:
        stocks_data = _collect(theme)
    if not stocks_data.get("stocks"):
        _tg(f"⚠️ [THEME-TISTORY] 종목 데이터 없음 — 발행 건너뜀: {theme}")
        if _preloaded_driver:
            try: _preloaded_driver.quit()
            except Exception: pass
        return {"success": False, "url": "", "keyword": theme, "error": "종목 데이터 없음"}

    # ── ②~⑦ HTML 생성 + 블록 조립 + 검증 ─────────────────────
    from JARVIS09_COLLECTOR import compose_collected
    collected = compose_collected(theme, stocks_data=stocks_data, sector=sector, category="theme")
    draft = _build_blocks(collected, "tistory", TISTORY_IMG_DIR)
    # ── ⑧ 발행 (⓪에서 받은 driver 재사용) ─────────────────────
    return _publish_tistory(draft, theme, sector, preloaded_driver=_preloaded_driver)


def run_naver_theme(theme: str, sector: str = "",
                    stocks_data: dict | None = None,
                    ts_keyword: str = "") -> dict:
    """테마주 네이버 발행.

    ★ P0-② 테마 버전 (사용자 박제 2026-05-18) — harness 외부 호출 차단.
    """
    from JARVIS02_WRITER.trend_economic_writer import _legacy_publish_guard as _gd
    _gd("run_naver_theme")
    print(f"\n  🟢 [THEME-NAVER] 테마 발행 시작: {theme}")
    if stocks_data is None:
        stocks_data = _collect(theme)
    if not stocks_data.get("stocks"):
        _tg(f"⚠️ [THEME-NAVER] 종목 데이터 없음 — 발행 건너뜀: {theme}")
        return {"success": False, "url": "", "keyword": theme, "error": "종목 데이터 없음"}
    from JARVIS09_COLLECTOR import compose_collected
    collected = compose_collected(theme, stocks_data=stocks_data, sector=sector, category="theme")
    draft = _build_blocks(collected, "naver", NAVER_IMG_DIR)
    return _publish_naver(draft, theme, sector)


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

    return issues


def run_all_themes(theme: str, sector: str = "") -> dict:
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

    from concurrent.futures import ThreadPoolExecutor as _TExec
    from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue

    # ── Layer 2 스텝 정의 ────────────────────────────────────

    @action_step(name="① 규정 로드")
    def _step_load_rules(state):
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        sb = _law_blk()
        print("  📜 [① 규정 로드] BLOG_SUPREME_LAW.md 숙지 완료")
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

        _col_exec = _TExec(max_workers=1)

        def _run_jarvis09():
            """★ ADR 012 — 설계-우선 리서치 수집 (킬스위치 RESEARCH_FIRST=0 → 종전 스윕).

            반환: {"docs": [...], "pack": dict|None}
            """
            # ★ 키워드 단독 전송 금지 (사용자 박제 2026-07-03 — ADR 013 강제):
            #   테마 키워드도 자비스03 프로필(정의·관련어)을 동봉해 JARVIS09 에 전달.
            _angle = ""
            try:
                from JARVIS03_RADAR.topic_pack import keyword_profile as _kw_prof
                _prof = _kw_prof(state["theme"], state.get("sector", ""))
                _angle = (_prof.get("summary") or "").strip()
                if _angle:
                    state["theme_profile"] = _prof
                    print(f"  🏷️ [THEME] 자비스03 프로필: {_angle[:60]}")
            except Exception:
                pass
            try:
                if os.getenv("RESEARCH_FIRST", "1") != "0":
                    from JARVIS09_COLLECTOR import collect_research
                    res = collect_research(state["theme"], state.get("sector", ""),
                                           angle=_angle)
                    docs = res.get("docs") or []
                    pack = res.get("evidence_pack") or None
                    n_facts = len((pack or {}).get("facts", []))
                    print(f"  ✅ [THEME] JARVIS09 리서치 수집 완료: 문서 {len(docs)}건 "
                          f"· 근거 fact {n_facts}개")
                    # ★ 근거 품질 경고 (ERRORS [300]): 테마는 종목 실데이터가 1차 근거라
                    #   차단하지 않고 가시화만 (조용한 강등 금지).
                    if res.get("insufficient") or res.get("plan_fallback"):
                        _tags = []
                        if res.get("plan_fallback"):
                            _tags.append("설계 폴백")
                        if res.get("insufficient"):
                            _tags.append(f"근거 부족(커버리지 {res.get('coverage_ratio')})")
                        print(f"  ⚠️ [THEME] 리서치 품질 경고: {', '.join(_tags)}")
                        _tg(f"⚠️ [THEME] '{state['theme']}' 리서치 품질 경고: {', '.join(_tags)}")
                    return {"docs": docs, "pack": pack}
            except Exception as e:
                print(f"  ⚠️ [THEME] 리서치 수집 실패 — 종전 스윕 폴백: {e}")
                _g_report("writer", e, module=__name__, func_name="_run_jarvis09")
            try:
                from JARVIS09_COLLECTOR.collector_engine import collect_for_theme
                docs = collect_for_theme(state["theme"], state.get("sector", ""))
                print(f"  ✅ [THEME] JARVIS09 수집 완료(폴백): {len(docs)}건")
                return {"docs": docs, "pack": None}
            except Exception as e:
                print(f"  ⚠️ [THEME] JARVIS09 수집 실패: {e}")
                return {"docs": [], "pack": None}

        # ★ 인터프리터 종료 레이스 (ERRORS [361]): 데몬 재시작 등으로 인터프리터가
        #   종료 단계에 들어가면 ThreadPoolExecutor.submit 이
        #   'cannot schedule new futures after interpreter shutdown' RuntimeError 를 던진다.
        #   병렬 이득만 포기하고 리서치 수집은 동기 폴백으로 이어간다 — 종료 레이스로 발행 크래시 금지.
        try:
            _col_fut = _col_exec.submit(_run_jarvis09)
        except RuntimeError as _sub_e:
            print(f"  ⚠️ [② 수집] 스레드 스케줄 불가(인터프리터 종료 중?) — 동기 폴백: {_sub_e}")
            _col_fut = None

        # 종목 데이터 수집 (주식 시세·재무)
        data = _collect(state["theme"])

        # ★ 다소스 결손 분리 (사용자 박제 2026-07-04 — 경제 파이프라인과 동렬화, ERRORS [351]):
        #   종목(stocks)이 0개여도 JARVIS09 다소스 리서치(논문·뉴스·DART·ECOS·웹 등)를
        #   *취소하지 않고 항상 수령*. 경제글이 구조데이터 0개여도 collection_docs·
        #   evidence_pack 을 보존하고 계속 쓰는 것과 동일. 리서치만으로도 글은 성립하며,
        #   차트는 실데이터 폴백/AI 사진으로 대체(_generate_charts). 진짜 폐기·테마 교체는
        #   종목·리서치·근거가 *전부* 비었을 때만 (KRX 종속 결합 해제).
        if _col_fut is not None:
            try:
                _col_res = _col_fut.result(timeout=600) or {}
            except Exception:
                _col_res = {}
            finally:
                _col_exec.shutdown(wait=False)
        else:
            # submit 실패(인터프리터 종료 레이스) → 리서치를 동기 실행(스레드 미사용)
            _col_exec.shutdown(wait=False)
            try:
                _col_res = _run_jarvis09() or {}
            except Exception:
                _col_res = {}
        collection_docs = _col_res.get("docs") or []
        evidence_pack   = _col_res.get("pack") or None

        _n_stocks = len(data.get("stocks") or [])
        _n_facts  = len((evidence_pack or {}).get("facts") or [])
        # ★ Step 7: 조각 → CollectedData 단일 상자 (재수집 없음). 옛 state 키는 back-compat 유지.
        from JARVIS09_COLLECTOR import compose_collected
        collected = compose_collected(
            state["theme"], stocks_data=data, docs=collection_docs,
            evidence_pack=evidence_pack, sector=state.get("sector", ""),
            category="theme", profile=state.get("theme_profile"))
        if _n_stocks == 0 and not collection_docs and _n_facts == 0:
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
        if state.get("ts_driver") is not None:
            print("  ⏭️ [④] 티스토리 driver 이미 준비됨 (재시도 — 재갱신 스킵)")
            return {}
        try:
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr
            ok, drv = _tcr(force=True, return_driver=True)
            if ok:
                load_dotenv(override=True)
                print("  ✅ [④] 티스토리 쿠키 갱신 완료 (신선 세션)")
                return {"ts_driver": drv}
            if drv:
                try:
                    drv.quit()
                except Exception:
                    pass
        except Exception as e:
            print(f"  ❌ [④] 티스토리 쿠키 갱신 예외: {e}")
            _g_report("writer", e, module=__name__)
        print("  ⚠️ [④] 티스토리 driver 없음 — 발행 시 재로그인 폴백")
        return {"ts_driver": None}

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
            _auto_refresh()
            _login_res = _verify_logins() or {}
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
            issues.append(Issue(step=step_name, kind="draft_failed",
                detail=f"대본 생성 실패: {draft.get('error', 'unknown')}"))
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
                    from JARVIS09_COLLECTOR.collect_theme import stocks_to_datasets

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
                    for _ds in stocks_to_datasets(_sd):
                        _unit = _ds.get("unit", "")
                        _rows = ", ".join(
                            f"{_r['label']} {_fmt_val(_r['value'])}{_unit}"
                            for _r in _ds.get("data", []))
                        if _rows:
                            _stock_docs.append(
                                f"[종목 실측] {_ds.get('title', '')}: {_rows} "
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
                            _flds = []
                            for _f, _lb in (("marcap", "시가총액"), ("revenue", "연매출")):
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
                                    f"(출처: 네이버 금융/KRX)")
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
                    draft, post_type="theme",
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

        # 재생성 필요성 표시 (재시도 시 이미지 폴더 불필요 리셋 방지)
        # ★ 리뷰 확정 수정 (2026-07-03): 해당 step 의 *어떤* 이슈든(draft_failed 뿐 아니라
        #   prepublish 게이트 factuality/engagement 포함) 있으면 skip 금지 — 재작성 순환 보존.
        _has_step_issue = any(i.step == step_name for i in non_draft)
        if not raw_strs and not _has_step_issue:
            state[f"_{draft_key}_skip_regen"] = True   # 대본 이슈 없음 → 재생성 불필요
        if raw_strs:
            fixed_strs, unfixed_strs = _fx(state, draft_key, platform, raw_strs, "theme")
            for s in fixed_strs:
                fixed_all.append(Issue(step=step_name, kind="draft_fixed", detail=s))
            for s in unfixed_strs:
                unfixed_all.append(Issue(step=step_name, kind="draft_invalid", detail=s))
            # 수정 불가 이슈가 있으면 재생성 필요, 없으면 인라인 패치로 해결
            state[f"_{draft_key}_skip_regen"] = not bool(unfixed_strs)

        # ★ 회복 불가 조건 → abort (harness 즉시 차단, 2차 시도 낭비 없음)
        _has_data_empty = any(i.kind == "data_empty" for i in non_draft)
        _has_login_issue = any(i.kind in ("login_invalid", "login_error") for i in non_draft)
        if _has_data_empty and not _has_login_issue:
            print("  ⚡ [fix] 회복 불가 확정 → abort: 종목 데이터 0개 — 다른 테마로 전환 필요")
            return fixed_all, [Issue(step="전체", kind="abort",
                                     detail="종목 데이터 0개 — 다른 테마로 전환 필요")]
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
            _ts_drv = state.get("ts_driver")
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
    # 한쪽의 재작성 순환·실패가 다른 쪽을 지연·차단하지 않음 (실패 격리, max_attempts 각 2)
    _nv_action_def = ActionDefinition(
        name=f"theme-publish-{theme}-naver",
        steps=[_step_load_rules, _step_collect, _step_nv_draft],
        verify=lambda st: _verify_theme_platform(st, "naver", "nv_draft",
                                                 "③ 네이버 대본 생성", check_data=True),
        fix=lambda st, iss: _fix_theme_platform(st, iss, "naver", "nv_draft",
                                                "③ 네이버 대본 생성"),
        send=lambda st: _send_theme_platform(st, "naver", "nv_draft",
                                             "nv_pub_result", "__nv_send_attempted__"),
        precondition=_precondition,
        max_attempts=2,  # ★ 외부 발행은 비멱등 → 최대 2회 (sentinel이 중복 방지)
    )
    _ts_action_def = ActionDefinition(
        name=f"theme-publish-{theme}-tistory",
        steps=[_step_ts_cookie, _step_ts_draft],
        verify=lambda st: _verify_theme_platform(st, "tistory", "ts_draft",
                                                 "⑤ 티스토리 대본 생성"),
        fix=lambda st, iss: _fix_theme_platform(st, iss, "tistory", "ts_draft",
                                                "⑤ 티스토리 대본 생성"),
        send=lambda st: _send_theme_platform(st, "tistory", "ts_draft",
                                             "ts_pub_result", "__ts_send_attempted__"),
        precondition=_precondition,
        max_attempts=2,
    )

    # ★ 단일 진입점 — 새 테마 = 전체 상태 초기화
    from JARVIS09_COLLECTOR.run_context import new_run as _new_run
    _new_run(theme)

    # ① 네이버 액션 (공유 수집 포함) — 완전 종결까지
    _nv_result = run_action(_nv_action_def, {"theme": theme, "sector": sector})
    _nv_st = _nv_result.state
    _nv_res = _nv_st.get("nv_pub_result", {"success": False, "url": "", "keyword": theme})
    # ★ 리뷰 확정 수정 (2026-07-03): data_empty 는 *수집이 실행되어 비었을 때만* —
    #   precondition 실패·동시성 차단 등 수집 미실행을 테마 교체로 오분류 금지.
    _sd = _nv_st.get("stocks_data")
    _stocks_ok = bool((_sd or {}).get("stocks"))
    _data_empty = bool(_nv_st.get("_collect_data_empty")) or (_sd is not None and not _stocks_ok)
    if not _nv_result.delivered:
        _reason = getattr(_nv_result, "escalation_reason", "최대 시도 초과 또는 abort")
        _tg(f"❌ [THEME] 네이버 발행 최종 실패\n테마: {theme}\n사유: {_reason}")

    # ② 티스토리 액션 — 네이버 *종결 후* 시작. 종목 데이터 없으면 스킵
    #    (진짜 data_empty → 상위 테마 교체 / 수집 미실행 → 교체 아닌 단순 실패)
    _ts_res = {"success": False, "url": "", "keyword": theme}
    if not _stocks_ok:
        print(f"  ⏭️ [티스토리] 종목 데이터 {'0개' if _data_empty else '미수집(네이버 액션 조기 종결)'} — 발행 스킵")
    else:
        _ts_result = run_action(_ts_action_def, {
            "theme": theme, "sector": sector,
            "collected": _nv_st.get("collected"),          # ★ Step 7: 액션1 → 액션2 전달
            "stocks_data": _nv_st.get("stocks_data"),      # back-compat (verify 등)
            "collection_docs": _nv_st.get("collection_docs") or [],
            "evidence_pack": _nv_st.get("evidence_pack"),
            "supreme_block": _nv_st.get("supreme_block"),
        })
        _ts_st = _ts_result.state
        _ts_res = _ts_st.get("ts_pub_result", {"success": False, "url": "", "keyword": theme})
        if not _ts_result.delivered:
            _reason = getattr(_ts_result, "escalation_reason", "최대 시도 초과 또는 abort")
            _tg(f"❌ [THEME] 티스토리 발행 최종 실패\n테마: {theme}\n사유: {_reason}")

    return {"theme": theme, "tistory": _ts_res, "naver": _nv_res, "data_empty": _data_empty}


__all__ = [
    "run_all_themes",
    "run_tistory_theme", "run_naver_theme",
]


# ── 직접 실행 진입점 ──────────────────────────────────────
if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    # ★ P0-② 테마 버전 우회 허용 (사용자 박제 2026-05-18) — CLI 직접 실행 디버그 모드.
    # run_tistory_theme/run_naver_theme 의 _legacy_publish_guard 차단을
    # 명시적으로 우회. 데몬 흐름에서는 환경변수 미설정 — 자동 차단.
    import os as _osg
    _osg.environ["JARVIS_ALLOW_LEGACY_PUBLISH"] = "1"

    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("theme", help="테마명 (예: '반도체')")
    p.add_argument("--sector", default="", help="섹터 (선택)")
    p.add_argument("--naver-only", action="store_true")
    p.add_argument("--tistory-only", action="store_true")
    args = p.parse_args()

    if args.naver_only:
        r = run_naver_theme(args.theme, args.sector)
        sys.exit(0 if r.get("success") else 1)
    if args.tistory_only:
        r = run_tistory_theme(args.theme, args.sector)
        sys.exit(0 if r.get("success") else 1)
    # 기본 — 2개 통합
    r = run_all_themes(args.theme, args.sector)
    ok = any(r.get(p, {}).get("success") for p in ("tistory", "naver"))
    sys.exit(0 if ok else 1)
