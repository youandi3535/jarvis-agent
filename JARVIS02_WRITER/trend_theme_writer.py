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

def _build_blocks(theme: str, sector: str, stocks_data: dict, platform: str, img_dir: Path,
                  supreme_block: str | None = None,
                  collection_docs: list | None = None,
                  evidence_pack: dict | None = None) -> dict:
    """대본 생성(JARVIS02) → 이미지 생성(JARVIS06) → 완성 블록 반환.

    ★ 사용자 박제 2026-05-31 — 역할 분리:
      JARVIS02: Pass-1 텍스트 대본 + 플레이스홀더 생성
      JARVIS06: [CHART_N]/[PHOTO_N]/섹션이미지/썸네일 생성 + 블록 조립
      JARVIS08: 완성 블록 수신 후 발행

    Returns:
        {"success", "title", "content", "html", "blocks", "error"}
    """
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

    # ── JARVIS02: Pass-1 텍스트 대본 생성 (★ ADR 012 — 근거 팩 주입) ──────
    from JARVIS02_WRITER.theme_html_writer import generate_theme_html, extract_text_content
    draft_html = generate_theme_html(
        theme, sector, stocks_data, supreme_block,
        platform=platform, collection_docs=collection_docs or [],
        evidence_pack=evidence_pack,
    )
    if not draft_html:
        return {"success": False, "error": "Pass-1 대본 생성 실패", "blocks": [],
                "title": "", "content": "", "html": ""}

    # ── JARVIS06: 이미지 생성 + 블록 조립 (★ ADR 012 — 근거 팩 접지) ──────
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(
        draft_html=draft_html,
        theme=theme, sector=sector,
        stocks_data=stocks_data,
        collection_docs=collection_docs or [],
        platform=platform,
        out_dir=img_dir,
        evidence_pack=evidence_pack,
    )
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
    draft = _build_blocks(theme, sector, stocks_data, "tistory", TISTORY_IMG_DIR)
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
    draft = _build_blocks(theme, sector, stocks_data, "naver", NAVER_IMG_DIR)
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
    Layer 2: ①규정로드 → ②수집+TS쿠키완료(글작성前보장) → ③TS → ④NV 대본 → ⑤쿠키확인
    Layer 3: 로그인 세션 + 2 플랫폼 draft 품질 검증 (최대 5회, 동일 실패 패턴 즉시 차단)
    Layer 4: Tistory/Naver Selenium 순차 발행

    Returns:
        {"theme", "tistory": {...}, "naver": {...}}
    """
    # ★ 글 작성 전 인메모리 캐시 전체 초기화 — 이전 글 잔재 완전 제거
    try:
        from JARVIS06_IMAGE.chart_generator import clear_session_cache as _clear_cache
        _clear_cache()
    except Exception as _ce:
        print(f"  ⚠️ [cache clear] 스킵: {_ce}")

    from concurrent.futures import ThreadPoolExecutor as _TExec
    from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue

    # ── Layer 2 스텝 정의 ────────────────────────────────────

    @action_step(name="① 규정 로드")
    def _step_load_rules(state):
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        sb = _law_blk()
        print("  📜 [① 규정 로드] BLOG_SUPREME_LAW.md 숙지 완료")
        return {"supreme_block": sb}

    @action_step(name="② 종목 수집 + TS쿠키 완료")
    def _step_collect(state):
        # ★ data_empty 재시도 스킵 (ERRORS [174]) — attempt 2+에서 종목 0개가 반복될 경우
        if state.get("_collect_data_empty"):
            print("  ⏭️ [② 수집] 이전 시도 종목 0개 — collect 재실행 스킵 (결과 동일 예상)")
            return {}

        # ⓪ TS 쿠키 갱신 + 종목 수집 + JARVIS09 — 3개 동시 시작, 모두 완료 후 리턴
        # 글 작성(③④) 전에 로그인 완료 보장
        _exec = _TExec(max_workers=1)

        def _refresh_ts():
            try:
                from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr
                ok, drv = _tcr(force=True, return_driver=True)
                if ok:
                    load_dotenv(override=True)
                    print("  ✅ [THEME] ⓪ 티스토리 쿠키 갱신 완료")
                    return drv
                if drv:
                    try: drv.quit()
                    except Exception: pass
                return None
            except Exception as e:
                print(f"  ❌ [THEME] ⓪ 티스토리 쿠키 갱신 예외: {e}")
                _g_report("writer", e, module=__name__)
                return None

        print("  🍪 [THEME] ⓪ 티스토리 쿠키 갱신 시작 (종목 수집과 병렬)")
        _ts_fut = _exec.submit(_refresh_ts)

        _col_exec = _TExec(max_workers=1)

        def _run_jarvis09():
            """★ ADR 012 — 설계-우선 리서치 수집 (킬스위치 RESEARCH_FIRST=0 → 종전 스윕).

            반환: {"docs": [...], "pack": dict|None}
            """
            try:
                if os.getenv("RESEARCH_FIRST", "1") != "0":
                    from JARVIS09_COLLECTOR import collect_research
                    res = collect_research(state["theme"], state.get("sector", ""))
                    docs = res.get("docs") or []
                    pack = res.get("evidence_pack") or None
                    n_facts = len((pack or {}).get("facts", []))
                    print(f"  ✅ [THEME] JARVIS09 리서치 수집 완료: 문서 {len(docs)}건 "
                          f"· 근거 fact {n_facts}개")
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

        _col_fut = _col_exec.submit(_run_jarvis09)

        # 종목 데이터 수집 (주식 시세·재무)
        data = _collect(state["theme"])
        _empty = not (data.get("stocks") or [])
        if _empty:
            try:
                _ts_fut.cancel()
                _exec.shutdown(wait=False)
                _col_fut.cancel()
                _col_exec.shutdown(wait=False)
            except Exception:
                pass
            print("  ⏭️ [② 수집] 종목 0개 — TS 쿠키·JARVIS09 수집 취소")
            return {"stocks_data": data, "_collect_data_empty": True,
                    "ts_driver": None, "collection_docs": [], "evidence_pack": None}

        # ★ 글 작성 전 쿠키 갱신 완료 대기 (글 작성 시작 전 로그인 보장)
        _ts_driver = None
        try:
            _ts_driver = _ts_fut.result(timeout=300)
        except Exception as e:
            print(f"  ❌ [THEME] ⓪ 티스토리 쿠키 수령 실패: {e}")
            _g_report("writer", e, module=__name__)
        finally:
            _exec.shutdown(wait=False)

        # JARVIS09 결과 수령 (★ ADR 012 — 리서치 수집은 LLM 추출 포함, 넉넉히 대기)
        try:
            _col_res = _col_fut.result(timeout=600) or {}
        except Exception:
            _col_res = {}
        finally:
            _col_exec.shutdown(wait=False)
        collection_docs = _col_res.get("docs") or []
        evidence_pack   = _col_res.get("pack") or None

        print(f"  {'✅' if _ts_driver else '⚠️'} [THEME] ⓪ 로그인 {'완료' if _ts_driver else '실패 — 발행 시 재시도'} | 글 작성 시작")
        return {"stocks_data": data, "ts_driver": _ts_driver,
                "collection_docs": collection_docs,
                "evidence_pack": evidence_pack}

    @action_step(name="③ 티스토리 대본 생성")
    def _step_ts_draft(state):
        if state.get("_ts_draft_skip_regen"):
            print("  ⏭️ [③ 티스토리] 이전 대본 검증 통과 — 재생성 건너뜀")
            return {}
        sd = state.get("stocks_data") or {}
        if not sd.get("stocks"):
            return {"ts_draft": {"success": False, "error": "종목 데이터 없음", "blocks": [], "content": "", "html": ""}}
        try:
            draft = _build_blocks(
                state["theme"], state["sector"], sd, "tistory", TISTORY_IMG_DIR,
                supreme_block=state.get("supreme_block"),
                collection_docs=state.get("collection_docs") or [],
                evidence_pack=state.get("evidence_pack"),
            )
        except Exception as e:
            _g_report("writer", e, module=__name__)
            draft = {"success": False, "error": str(e)[:120], "blocks": [], "content": "", "html": ""}
        return {"ts_draft": draft, "_ts_draft_skip_regen": False}

    @action_step(name="④ 네이버 대본 생성")
    def _step_nv_draft(state):
        if state.get("_nv_draft_skip_regen"):
            print("  ⏭️ [④ 네이버] 이전 대본 검증 통과 — 재생성 건너뜀")
            return {}
        sd = state.get("stocks_data") or {}
        if not sd.get("stocks"):
            return {"nv_draft": {"success": False, "error": "종목 데이터 없음", "blocks": [], "content": "", "html": ""}}
        try:
            draft = _build_blocks(
                state["theme"], state["sector"], sd, "naver", NAVER_IMG_DIR,
                supreme_block=state.get("supreme_block"),
                collection_docs=state.get("collection_docs") or [],
                evidence_pack=state.get("evidence_pack"),
            )
        except Exception as e:
            _g_report("writer", e, module=__name__)
            draft = {"success": False, "error": str(e)[:120], "blocks": [], "content": "", "html": ""}
        return {"nv_draft": draft, "_nv_draft_skip_regen": False}

    @action_step(name="⑤ 티스토리 쿠키 확인")
    def _step_ts_cookie_collect(state):
        # ② 에서 이미 완료 — state 그대로 전달
        _ts_driver = state.get("ts_driver")
        print(f"  {'✅' if _ts_driver else '⚠️'} [⑤] 티스토리 driver {'준비됨' if _ts_driver else '없음 — 발행 시 재시도'}")
        return {"ts_driver": _ts_driver}

    # ── Layer 3 검증 함수 ────────────────────────────────────

    def _verify_all(state):
        """Layer 3 — 2 플랫폼 대본 전체 검증. list[Issue] 반환 (빈 리스트 = 통과).

        ★ 순수 검증만 — 즉시 수정·fingerprint·abort는 harness fix 훅(_fix_theme_drafts)이 담당.
        검증 범위 (사용자 박제 2026-05-17):
          - [L1] 로그인 세션 유효성 (만료 시 자동 갱신 시도 → 실패 시 Issue)
          - [L2] 종목 데이터 유효성
          - [L3] 2 플랫폼 각각 규정 준수: 분량·키워드·이미지·헤더 등
        """
        issues = []

        # [L1] 로그인 세션 검증 (편집창 접속 가능 여부까지)
        try:
            from JARVIS08_PUBLISH.credentials.login_manager import (
                auto_refresh_if_needed as _auto_refresh,
                verify_all_logins      as _verify_logins,
            )
            _auto_refresh()
            if not _verify_logins():
                issues.append(Issue(step="① 전제조건", kind="login_invalid",
                    detail="로그인 세션 만료 — 재로그인 필요 (auto_refresh 후에도 실패)"))
        except Exception as _le:
            issues.append(Issue(step="① 전제조건", kind="login_error",
                detail=f"로그인 확인 오류: {_le}"))

        # [L2] 종목 데이터 유효성
        sd = state.get("stocks_data") or {}
        if not sd.get("stocks"):
            issues.append(Issue(step="② 종목 수집 + TS쿠키 시작", kind="data_empty",
                detail="종목 데이터 0개 — 수집 실패"))

        # [L3] 2 플랫폼 대본 규정 준수 검증 (순수 "발견"만) ──────────────────
        # ★ 수정·fingerprint·abort는 harness fix 훅(_fix_theme_drafts)이 자동 담당
        _draft_map = {
            "ts_draft": ("③ 티스토리 대본 생성", "tistory"),
            "nv_draft": ("④ 네이버 대본 생성",   "naver"),
        }
        for key, (step_name, platform) in _draft_map.items():
            draft = state.get(key) or {}
            if not draft.get("success"):
                issues.append(Issue(step=step_name, kind="draft_failed",
                    detail=f"대본 생성 실패: {draft.get('error', 'unknown')}"))
                continue
            di_list = _layer3_verify_draft(draft, platform)
            for di in di_list:
                issues.append(Issue(step=step_name, kind="draft_quality", detail=di))
            # ★ 발행 전 품질 게이트 (2026-06-28) — 구조 검증 통과 시에만.
            #   테마글은 출처 약함(종목 0개 시 빈 코퍼스) → 웹 재검증을 1차 근거로
            #   "웹에서도 확인 불가만 차단" (factuality_issues 의 source_weak 완화 자동).
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
                for q in prepublish_quality_issues(
                        draft, post_type="theme",
                        source_docs=_src_docs,
                        market_data=None,
                        stocks_data=state.get("stocks_data")):   # ★ 1-c 실측 재무 결정론 대조
                    issues.append(Issue(step=step_name, kind=q["kind"], detail=q["detail"]))

        return issues

    def _fix_theme_drafts(state: dict, issues: list) -> tuple:
        """harness fix 훅 — Layer 3 draft_quality 이슈 즉시 패치 + GUARDIAN 학습.

        ★ 전체 에이전트 디폴트 — harness.run_action이 verify 후 자동 호출.
        흐름: 이슈 발견 → inline 패치(state 직접 수정) → GUARDIAN 2단 박제
              (report_manual_fix + record_pattern_hit) → (fixed, unfixed) 반환.
        harness가 fixed→학습, unfixed→fingerprint, 전체→재생성 자동 처리.
        login_invalid / data_empty / draft_failed 등 패치 불가 항목은 unfixed 그대로 반환.
        """
        from JARVIS02_WRITER.draft_fixer import fix_and_learn as _fx

        _key_map = {
            "③ 티스토리 대본 생성": ("ts_draft", "tistory"),
            "④ 네이버 대본 생성":   ("nv_draft", "naver"),
        }

        # draft_quality 이슈를 step별로 묶기; 나머지(login, data_empty 등)는 즉시 unfixed
        by_step: dict = {}
        non_draft: list = []
        for iss in issues:
            if iss.kind == "draft_quality" and iss.step in _key_map:
                by_step.setdefault(iss.step, []).append(iss.detail)
            else:
                non_draft.append(iss)

        fixed_all: list = []
        unfixed_all: list = list(non_draft)

        # 이슈가 없는 플랫폼은 재생성 불필요 표시 (재시도 시 이미지 폴더 불필요 리셋 방지)
        # ★ draft_failed 이슈가 있는 플랫폼은 skip_regen 설정 금지 — 재생성해야 함
        _non_draft_steps = {iss.step for iss in non_draft}
        for step_name, (draft_key, _plat) in _key_map.items():
            if step_name not in by_step and step_name not in _non_draft_steps:
                state[f"_{draft_key}_skip_regen"] = True  # 이슈 없음 → 재생성 불필요

        for step_name, raw_strs in by_step.items():
            draft_key, platform = _key_map[step_name]
            fixed_strs, unfixed_strs = _fx(state, draft_key, platform, raw_strs, "theme")
            for s in fixed_strs:
                fixed_all.append(Issue(step=step_name, kind="draft_fixed", detail=s))
            for s in unfixed_strs:
                unfixed_all.append(Issue(step=step_name, kind="draft_invalid", detail=s))
            # 수정 불가 이슈가 있으면 재생성 필요, 없으면 인라인 패치로 해결됨 → 재생성 불필요
            state[f"_{draft_key}_skip_regen"] = not bool(unfixed_strs)

        # ★ 회복 불가 조건 → kind="abort" 직접 반환 (harness 즉시 차단, 2차 시도 낭비 없음)
        _has_data_empty = any(iss.kind == "data_empty" for iss in non_draft)
        _has_login_issue = any(iss.kind in ("login_invalid", "login_error") for iss in non_draft)
        _all_steps = set(_key_map.keys())
        _failed_steps = {iss.step for iss in non_draft if iss.kind == "draft_failed"}
        _has_all_draft_failed = bool(_failed_steps) and _failed_steps >= _all_steps
        _platforms_with_unfixed = {iss.step for iss in unfixed_all
                                    if iss.kind in ("draft_failed", "draft_invalid")}
        _has_partial_abort = (bool(_failed_steps) and _platforms_with_unfixed >= _all_steps)

        _should_abort = (
            (_has_data_empty or _has_all_draft_failed or _has_partial_abort)
            and not _has_login_issue  # 로그인 오류는 재시도 가능 — abort 제외
        )
        if _should_abort:
            _reason = (
                "종목 데이터 0개 — 다른 테마로 전환 필요" if _has_data_empty else
                "전 플랫폼 대본 생성 실패 — LLM 거부 또는 구조적 실패" if _has_all_draft_failed else
                "부분 draft_failed + 나머지 플랫폼 수정 불가 — 재시도해도 동일 결과"
            )
            print(f"  ⚡ [fix] 회복 불가 확정 → abort 즉시 발동: {_reason}")
            return fixed_all, [Issue(step="전체", kind="abort", detail=_reason)]

        return fixed_all, unfixed_all

    # ── Layer 4 발행 함수 ────────────────────────────────────

    def _send_all(state):
        """Layer 4 — 검증 통과 후 2 플랫폼 발행.

        ★ ADR 009 v2 strict 모드: 활성 플랫폼 *하나라도* 실패 시 raise → harness 재진입.
        published_platforms 집합으로 이미 *진짜 성공한* 플랫폼은 재시도 시 스킵 (이중 발행 방지).

        ★ 사용자 박제 2026-06-07 (ERRORS [265]) — 부분 실패 자율 회복:
          attempt>=2 + 이전 실패 (success=False) → 플래그 해제 → 진짜 재발행 시도.
          harness max_attempts=3 와 함께 최대 3회.
        """
        _theme   = state["theme"]
        _sector  = state["sector"]
        _ts_drv  = state.get("ts_driver")

        # ★ 이미 발행된 플랫폼 추적 (retry 시 이중 발행 방지)
        published = state.setdefault("published_platforms", set())

        # ★ attempt 추적 — 첫 호출 = 1, 재진입마다 +1 (ERRORS [265])
        send_attempt = state.get("__send_attempt__", 0) + 1
        state["__send_attempt__"] = send_attempt

        # ★ attempt >= 2 + 이전 실패 플랫폼 *플래그 해제* → 진짜 재발행 기회
        if send_attempt >= 2:
            _ts_prev = state.get("ts_pub_result", {})
            _nv_prev = state.get("nv_pub_result", {})
            if ("tistory" not in published and state.get("__ts_send_attempted__")
                    and not _ts_prev.get("success")):
                print(f"  🔄 [티스토리] attempt={send_attempt} — 이전 발행 실패 → 플래그 해제·재발행")
                state["__ts_send_attempted__"] = False
            if ("naver" not in published and state.get("__nv_send_attempted__")
                    and not _nv_prev.get("success")):
                print(f"  🔄 [네이버] attempt={send_attempt} — 이전 발행 실패 → 플래그 해제·재발행")
                state["__nv_send_attempted__"] = False

        print(f"\n  📤 [Phase 2] 발행 (Tistory/Naver 순차, send_attempt={send_attempt})")

        # Tistory
        if "tistory" not in published:
            if state.get("__ts_send_attempted__"):
                # ★ 이미 시도+성공 케이스 — 이중 발행 방지로 published 처리
                print("  ⚠️ 티스토리 발행 이미 시도 완료 (이중 방지)")
                published.add("tistory")
            else:
                state["__ts_send_attempted__"] = True  # 반드시 시도 *전* 에 설정
                ts_res = _publish_tistory(state.get("ts_draft", {}), _theme, _sector,
                                          preloaded_driver=_ts_drv)
                state["ts_pub_result"] = ts_res
                if ts_res.get("success"):
                    published.add("tistory")
        else:
            print("  ⏭ 티스토리 이미 발행 완료 (재시도 스킵)")

        # Naver
        if "naver" not in published:
            if state.get("__nv_send_attempted__"):
                # ★ 이미 시도+성공 케이스 — 이중 발행 방지로 published 처리
                print("  ⚠️ 네이버 발행 이미 시도 완료 (이중 방지)")
                published.add("naver")
            else:
                state["__nv_send_attempted__"] = True  # 반드시 시도 *전* 에 설정
                nv_res = _publish_naver(state.get("nv_draft", {}), _theme, _sector)
                state["nv_pub_result"] = nv_res
                if nv_res.get("success"):
                    published.add("naver")
        else:
            print("  ⏭ 네이버 이미 발행 완료 (재시도 스킵)")

        ts_ok = "tistory" in published
        nv_ok = "naver" in published

        print(f"\n{'='*60}")
        print(f"  ★ 테마 발행: {_theme}")
        print(f"     Tistory : {'✅' if ts_ok else '❌'}")
        print(f"     Naver   : {'✅' if nv_ok else '❌'}")
        print(f"{'='*60}\n")

        # ★ ADR 009 v2 strict: 활성 플랫폼 *하나라도* 미발행 시 raise → 검증 순환 재진입
        required = {"tistory", "naver"}  # 테마글은 양쪽 모두 항상 활성
        missing = required - published
        if missing:
            raise RuntimeError(
                f"[Layer4] {sorted(missing)} 발행 실패 (theme={_theme}) — 송출 미완료 → 검증 순환 재진입"
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

    _action_def = ActionDefinition(
        name=f"theme-publish-{theme}",
        steps=[
            _step_load_rules,
            _step_collect,
            _step_ts_draft,
            _step_nv_draft,
            _step_ts_cookie_collect,
        ],
        verify=_verify_all,
        fix=_fix_theme_drafts,  # ★ "수정→기록→누적→순환" 전체 에이전트 디폴트 (사용자 박제 2026-05-18)
        send=_send_all,
        precondition=_precondition,
        max_attempts=2,  # ★ 외부 발행은 비멱등 → 최대 2회 (sentinel이 중복 방지)
    )

    # ★ 단일 진입점 — 새 테마 = 전체 상태 초기화
    from JARVIS09_COLLECTOR.run_context import new_run as _new_run
    _new_run(theme)

    _result = run_action(_action_def, {"theme": theme, "sector": sector})
    _st = _result.state

    _ts_res = _st.get("ts_pub_result", {"success": False, "url": "", "keyword": theme})
    _nv_res = _st.get("nv_pub_result", {"success": False, "url": "", "keyword": theme})
    _data_empty = bool(_st.get("_collect_data_empty"))

    if not _result.delivered:
        _reason = getattr(_result, "escalation_reason", "최대 시도 초과 또는 abort")
        _tg(f"❌ [THEME] 발행 최종 실패\n테마: {theme}\n사유: {_reason}")

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
