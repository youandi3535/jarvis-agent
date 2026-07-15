"""
economic_poster.py
경제 브리핑 해설 — 자동 생성 & 블로그 포스팅
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
매일 오전 7시 실행
1. investing.com 에서 오늘 주요 지표 수집
2. yfinance 로 시장 데이터 수집
3. Claude API 로 해설 기사 생성
4. 네이버·티스토리 '경제 브리핑' 카테고리에 발행

사용법:
  python economic_poster.py
"""

import os, re, json, base64, requests, sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from bs4 import BeautifulSoup

# ── sys.path 보정 (subprocess 직접 실행 호환) ──
_JARVIS_ROOT = Path(__file__).parent.parent
if str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))

# ★ .env 최상단·명시경로 로드 (부차 A 수정 2026-07-01): 아래 JARVIS import·provider 인스턴스화
#   *전* 에 키를 실어야 KOSIS/ECOS/DART/NAVER 등이 '없음'으로 스킵되지 않는다. bare load_dotenv()
#   는 CWD/호출프레임 의존이라 실행 위치(수동 실행·subprocess)에 따라 키 누락 → 명시 경로로 제거.
#   (데몬 jarvis_daemon.py 의 load_dotenv(JARVIS_ROOT/'.env') 와 동일 패턴.)
load_dotenv(_JARVIS_ROOT / ".env")

# ★ 수집 단일 진입점 (2026-05-31): get_market_data / get_economic_calendar 본체 → JARVIS09
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_market_data as _j09_get_market_data,
    get_economic_calendar as _j09_get_economic_calendar,
)

# JARVIS03 품질 분석 연동
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

try:
    _JARVIS_ROOT = Path(__file__).parent.parent
    sys.path.insert(0, str(_JARVIS_ROOT))


    # ★ ERRORS [137·138] Layer 3 검증 함수 (사용자 박제 2026-05-17 — ADR 009 v2) ─────
    # 누수 차단: html (작성 직후) + full_html (발행 직전 최종) + blocks (이미지 경로) *모두* 검사.
    # 누수 사고: 검증은 html 만 봤으나 발행은 full_html 그대로 — 이미지 중복·썸네일 반복 검증 0.
    def _layer3_verify_draft(draft: dict, platform: str) -> list[str]:
        """발행 직전 draft 결과 검증 — 7 패턴 + ★ 분량 (post_type_specs 동적).

        검사 대상 *4 종류* (★ ERRORS [139] 분량 동적 추가):
          1. body — 본문 길이·키워드·★ 분량 상한·하한
          2. full_html — 이미지 src 중복·여백·빈 헤더·★ 이미지 갯수 상한
          3. blocks 시퀀스 — 이미지 경로 중복
          4. spec 한계 — post_type 별 절대 상한·하한 (25문장·30문장 하드코딩 X)
        """
        import re as _re
        from JARVIS02_WRITER.post_type_specs import get_spec as _get_spec
        try:
            from JARVIS02_WRITER import length_manager as _LM
        except ImportError:
            import length_manager as _LM
        issues: list[str] = []
        if not draft or not draft.get("success"):
            return ["draft 자체 실패 (success=False)"]

        # post_type → spec 동적 (economic / theme / 미래 영상 등)
        post_type = (draft.get("post_type") or "economic").strip().lower()
        spec = _get_spec(post_type)

        # ── (1) body 검증 — 본문 길이·키워드·★ 분량 ────────────────
        body = (draft.get("html") or draft.get("content") or "")
        if isinstance(body, dict):
            body = body.get("html") or body.get("content") or ""
        if not body or len(body) < 200:
            issues.append(f"본문 너무 짧음: {len(body)}자")
            return issues
        keyword = (draft.get("keyword") or "").strip()
        if keyword and len(keyword) >= 2:
            # HTML 제거 후 텍스트에서 검사 (태그 경계로 키워드가 분리되는 오탐 방지)
            body_plain_kw = _re.sub(r"<[^>]+>", " ", body)
            # ★ SSOT: 검색어·최소횟수 규칙은 law_enforcer 단일 진입점 (작성 프롬프트와 동일 임계)
            from JARVIS02_WRITER.law_enforcer import keyword_search_terms, keyword_min_count
            _search_terms = keyword_search_terms(keyword)
            if _search_terms:
                total_kw_count = sum(body_plain_kw.count(t) for t in _search_terms)
                _min_kw = keyword_min_count(keyword)
                if total_kw_count < _min_kw:
                    issues.append(f"⑤ 키워드 '{keyword}' body 등장 {total_kw_count}회 (검색어: {_search_terms} — 최소 {_min_kw}회 필요)")

        # ★ ERRORS [139] — 분량 동적 검증 (post_type 별 상한·하한)
        # <p> 태그 내 문장종결부호만 카운트 — 헤더·차트경로·alt텍스트 과산정 방지
        body_text = _re.sub(r"<[^>]+>", " ", body)
        kor_count = _LM.count(body_text)
        p_tags = _re.findall(r"<p[^>]*>.*?</p>", body, _re.DOTALL | _re.IGNORECASE)
        if p_tags:
            sent_count = sum(
                len(_re.findall(r'[.!?。]\s*(?=[^<]|$)', _re.sub(r"<[^>]+>", "", p)))
                for p in p_tags
            )
        else:
            # <p> 태그 없는 경우 — 한국어 포함 문장만 카운트
            sent_count = _LM.count_sentences(body_text)
        if sent_count > spec.max_sentences:
            issues.append(f"분량 상한 초과: {sent_count}문장 > {spec.max_sentences}문장 (post_type={post_type})")
        if kor_count > spec.max_korean:
            issues.append(f"한국어 글자수 상한 초과: {kor_count}자 > {spec.max_korean}자 ({post_type})")
        if sent_count < spec.min_sentences:
            issues.append(f"분량 하한 미달: {sent_count}문장 < {spec.min_sentences}문장 ({post_type})")

        # ── (2) ★ full_html 검증 — *실제 발행되는 HTML* (이미지·여백·빈 헤더) ──
        full_html = draft.get("full_html") or ""
        # 연속 빈 p / br 검사: full_html 없으면 body 로 대체
        target_html = full_html if full_html else body
        # ② 5+ 연속 빈 p (spacer 2개 쌍까지는 허용 — enforce_spacing 정상 동작)
        if _re.search(r'(?:<p[^>]*>\s*(?:&nbsp;|\s)*</p>\s*){5,}', target_html, _re.IGNORECASE):
            issues.append("② full_html 5+ 연속 빈 p 검출")
        if _re.search(r'(?:<br\s*/?>\s*){3,}', target_html, _re.IGNORECASE):
            issues.append("② full_html 3+ 연속 br 검출")
        # ③④ 이미지 src 중복 — full_html(발행용 조립 HTML) 에서만 검사.
        # ts/nv draft 는 full_html 없음 — 발행 시 blocks 로 처리하고,
        # raw html 은 law_enforcer dedupe 전이라 오탐 → skip, blocks 검사로 대체
        if full_html:
            srcs = _re.findall(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', full_html)
            if srcs:
                non_header = [s for s in srcs if not _re.search(
                    r'(heading_|economic_h2_|section_title|/svg_\d+\.jpg)',
                    s, _re.IGNORECASE)]
                dup_html = len(non_header) - len(set(non_header))
                if dup_html:
                    issues.append(f"③④ full_html 이미지 src 중복 {dup_html}건 (썸네일 반복 또는 차트 중복)")
                # ★ ERRORS [139] — 이미지 갯수 상한 (post_type 별 spec)
                if len(non_header) > spec.max_images:
                    issues.append(f"이미지 갯수 상한 초과: {len(non_header)} > {spec.max_images} ({post_type})")
        # 빈 헤더 (제3조)
        empty_headers = _re.findall(r'<h[1-6][^>]*>\s*</h[1-6]>', target_html, _re.IGNORECASE)
        if empty_headers:
            issues.append(f"제3조 빈 헤더 {len(empty_headers)}건")

        # ── (3) ★ blocks 시퀀스 검증 — 이미지 경로 중복 (모든 플랫폼 공통) ──
        # SVG 차트 (svg_NN.jpg) 는 제4조 해결로 재삽입 가능 → 중복 허용
        # blocks 는 law_enforcer dedupe 후 → 차트·썸네일 중복 모두 포착
        blocks = draft.get("blocks") or []
        if isinstance(blocks, list) and blocks:
            image_paths_raw = [str(bd) for bt, bd in blocks
                               if bt == "image" and bd
                               and not _re.search(
                                   r'(heading_|economic_h2_|section_title|/svg_\d+\.jpg)',
                                   str(bd), _re.IGNORECASE)]
            dup_path = len(image_paths_raw) - len(set(image_paths_raw))
            if dup_path:
                issues.append(f"③④ blocks 이미지 경로 중복 {dup_path}건 (썸네일 본문 반복 가능성)")
        return issues


    def _layer3_verify_final(draft: dict, platform: str) -> list[str]:
        """★ 발행 직전 *최종* 검증 — ts/nv_publish 진입 시 마지막 방어망 (ERRORS [138]).

        Phase 1.5 검증·재작성 순환 *통과 후에도* 발행 직전 한 번 더. 통과 못하면 *발행 차단*.
        검증 항목은 _layer3_verify_draft 와 동일하나 *full_html 우선*.
        """
        return _layer3_verify_draft(draft, platform)
    # ────────────────────────────────────────────────────────────────────

    from shared.bus import on_post_published_detail as _emit_published
    try:
        # 글자수 정책은 length_manager 단일 진입점 — 한도 변경 시 거기만 수정
        try:
            from JARVIS02_WRITER import length_manager as _L
        except ImportError:
            import length_manager as _L  # 같은 폴더 직접 실행 시
        # SEO 기준은 seo_standards 단일 진입점
        try:
            from JARVIS02_WRITER.seo_standards import build_seo_block as _build_seo_block
        except ImportError:
            try:
                from seo_standards import build_seo_block as _build_seo_block
            except ImportError:
                def _build_seo_block(platform, theme=""):  # noqa: E306
                    return ""
        def _cap_eco_content(text, max_korean=_L.MAX_KOREAN, context="economic"):
            """legacy alias → length_manager.compress."""
            return _L.compress(text, context=context, max_korean=max_korean)
        # prompt 안에 박을 [글자수 절대 규정] 블록 — length_manager 가 동적 생성
        _L_LEN_BLOCK = _L.build_prompt_length_block()
    except ImportError:
        # length_manager 미가용 시 안전한 fallback (cap 안 함, prompt 블록 빈 문자열)
        def _cap_eco_content(text, **_kw): return text or ""
        _L_LEN_BLOCK = ""
    from JARVIS03_RADAR.post_quality_analyzer import run_single as _run_analyzer
    _ANALYZER_SCRIPT = _JARVIS_ROOT / "JARVIS03_RADAR" / "post_quality_analyzer.py"
    _QUALITY_ENABLED = True
except Exception as _e:
    _QUALITY_ENABLED = False
    print(f"  ⚠️ JARVIS03 연동 비활성: {_e}")
    _g_report("writer", _e, module=__name__)

# 실시간 출력 (VS Code 터미널용)
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

load_dotenv()

BASE_DIR        = Path(__file__).parent
JARVIS06_BASE   = BASE_DIR.parent / "JARVIS06_IMAGE"               # 이미지 단일 진입점 (CLAUDE.md 규정)

# ── 플랫폼별 이미지 디렉터리 — JARVIS06_IMAGE/output/ 아래 단일 관리 ────────
ECONOMIC_IMG_DIR        = JARVIS06_BASE / 'output' / 'images' / 'economic_naver'   # 네이버 전용
ECONOMIC_IMG_DIR_TISTORY= JARVIS06_BASE / 'output' / 'images' / 'economic_tistory' # 티스토리 전용
for _d in (ECONOMIC_IMG_DIR, ECONOMIC_IMG_DIR_TISTORY):
    _d.mkdir(parents=True, exist_ok=True)


# ── ADR 008 Phase 1 (★ 사용자 박제 2026-05-17) — 이미지 정리 단일 진입점 ──
# 본체는 JARVIS06_IMAGE/cleaners/economic_image_cleaner.py.
from JARVIS06_IMAGE.cleaners import cleanup_economic_images

# ★ 블로그(플랫폼) 액션 하드 데드라인 SSOT (watchdog.py) — harness ActionDefinition.deadline_sec 와
#   JARVIS_LLM_DEADLINE_TS(LLM 재시도 강등 기준) 양쪽이 반드시 같은 값을 봐야 한다. 두 값이
#   어긋나면(LLM 쪽이 더 크면) "잔여 <10분 강등"이 하드 킬 전에 트리거되지 않아 watchdog 이
#   재시도·백오프 도중 강제 종료한다 (ERRORS 참조 — 경제 브리핑 티스토리 데드라인 초과).
from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TODAY = datetime.now()
TODAY_STR = TODAY.strftime("%Y년 %m월 %d일")
TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][TODAY.weekday()]


# ══════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════

def tg(msg: str):
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass


# ══════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════


# ══════════════════════════════════════════
#  Claude API로 기사 생성
# ══════════════════════════════════════════


# ── 섹션 구조 공통 템플릿 (동적 색상 대체용) ──



from JARVIS08_PUBLISH.category import ECONOMIC_CATEGORY, ECONOMIC_TAGS_DEFAULT  # noqa: F401
TODAY_PREFIX       = f"[{TODAY.month}/{TODAY.day}]"


# ══════════════════════════════════════════
#  스마트 태그 생성
# ══════════════════════════════════════════


# ══════════════════════════════════════════
#  블록 구성
# ══════════════════════════════════════════


# ══════════════════════════════════════════
#  네이버 / 티스토리 포스팅
# ══════════════════════════════════════════

def post_to_naver_economic(title: str, content: str, blocks: list, tags: list,
                           related_posts: list = None) -> bool:
    from JARVIS08_PUBLISH.platforms import post_to_naver
    return post_to_naver(
        title=title,
        html_content=content,
        blocks=blocks,
        category=ECONOMIC_CATEGORY,
        tags=tags,
        related_posts=related_posts,
    )


def post_to_tistory_economic(title: str, content: str, blocks: list, tags: list,
                             related_posts: list = None) -> bool:
    """티스토리 경제 브리핑 발행 — 쿠키 자동 강제 갱신 후 발행 (driver 재사용).

    ★ force=True (사용자 직접 박제 2026-05-14) — 글 작성 전 항상 카카오 로그인으로
    TSSESSION 새로 발급. 30분 사전 갱신만으로 카카오 세션 리다이렉트 사고 (ERRORS [62]) 재발 방지.
    """
    print("  🍪 티스토리 쿠키 강제 갱신 중 (.env 의 TS_USERNAME/TS_PASSWORD 사용)...")
    # 절대 import — sys.path 조작 없이 안전하게 호출 (driver 재사용 → 2번 로그인 방지)
    from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr_run
    ok, preloaded_driver = _tcr_run(force=True, return_driver=True)
    if not ok:
        print("  ❌ 티스토리 쿠키 갱신 실패")
        return False
    load_dotenv(override=True)
    # ★ ADR 008 Phase 2 완전 이관 (사용자 박제 2026-05-18) — shim 제거, 신 경로 직접 import
    import JARVIS08_PUBLISH.platforms.tistory_poster as tistory_poster
    # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
    from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
    tistory_poster.TS_COOKIE = get_tistory_cookie().strip('"').strip("'")
    from JARVIS08_PUBLISH.platforms import post_to_tistory
    return post_to_tistory(
        title=title,
        html_content=content,
        blocks=blocks,
        category=ECONOMIC_CATEGORY,
        preloaded_driver=preloaded_driver,
        tags=tags,
        related_posts=related_posts,
    )


# ══════════════════════════════════════════
#  메인
# ══════════════════════════════════════════



def _fix_consecutive_images(blocks: list, for_tistory: bool = False) -> list:
    """★ 글+이미지 규정 강제 안전망 — 이미지+이미지 연속 절대 금지.
    소제목 이미지(heading_* 파일명) 제외, 데이터 이미지 연속 시 설명 텍스트 삽입.
    """
    from shared.llm import invoke_text as _llm_fix
    _raw = _llm_fix(
        "writer_fast",
        f"경제 블로그에서 차트 이미지 두 개 사이에 들어가는 자연스러운 연결 설명 {_L.build_length_phrase(1, _L.MAX_P_SENTS)}. 해요체. 문장만 출력.",
        max_tokens=80, temperature=0.8
    ) or "위 지표와 차트를 함께 살펴보세요."
    _html_raw = _llm_fix(
        "writer_fast",
        f"경제 블로그에서 차트 이미지 두 개 사이 연결 설명 {_L.build_length_phrase(1, _L.MAX_P_SENTS)}. 합니다체. 문장만 출력.",
        max_tokens=80, temperature=0.8
    ) or "위 지표와 차트를 함께 확인해 주십시오."
    FALLBACK_TEXT = _raw
    FALLBACK_HTML = f'<p style="font-size:14px;color:#555;line-height:1.8;">{_html_raw}</p>'
    def _is_heading(bdata: str) -> bool:
        fname = str(bdata)
        return 'heading_' in fname or 'economic_h2_' in fname or 'section_title' in fname

    result = []
    for b in blocks:
        if (b[0] == 'image'
                and not _is_heading(b[1])
                and result
                and result[-1][0] == 'image'
                and not _is_heading(result[-1][1])):
            sep = ('html', FALLBACK_HTML) if for_tistory else ('text', FALLBACK_TEXT)
            result.append(sep)
        result.append(b)
    return result


def run(post_naver=True, post_tistory=True):
    """경제 브리핑 포스팅 통합 진입점.

    2개 플랫폼 모두 JARVIS03 트렌드 기반, 각기 다른 주제(키워드)로 발행.
    대본 생성·발행은 trend_economic_writer 에 위임.
    """
    print(f"\n{'='*50}")
    print(f"  📰 경제 브리핑 포스터")
    print(f"  {TODAY_STR} ({TODAY_DOW}요일)")
    print(f"  네이버:{' ON' if post_naver else 'OFF'}  "
          f"티스토리:{' ON' if post_tistory else 'OFF'}")
    print(f"{'='*50}")

    # ★ 발행 시간 보호 (ERRORS [288][289] — 2026-07-03): 대본 생성 LLM 데드라인.
    #   잔여 <10분이면 shared/llm.invoke_text 가 재시도 1회·백오프 0 으로 강등 —
    #   rate-limit 폭풍 날에도 발행(Layer 4)이 subprocess 타임아웃 전에 시작되도록 보장.
    #   ★ 액션 시작 시 바로 아래에서 BLOG_ACTION_DEADLINE_SEC 기준으로 다시 리셋되므로
    #   이 초기값은 (수집 등) 액션 진입 전 구간에만 의미 — 반드시 같은 SSOT 상수 사용.
    import time as _tm_dl
    os.environ["JARVIS_LLM_DEADLINE_TS"] = str(_tm_dl.time() + BLOG_ACTION_DEADLINE_SEC)

    tg(f"📰 경제 브리핑 포스팅 시작 ({TODAY_STR})\n"
       f"1주제 공동수집 → 네이버·티스토리 각각 다른 대본으로 발행")

    # ── 발행 전 사실성 게이트용 시장 수치 ground truth (★ 2026-06-28) ──────
    # 작성에 쓰인 시장 지표·경제 일정을 verify 시점 state 로 전달 → 본문 수치를
    # *신뢰 가능한 구조화 데이터* 와 직접 대조 (웹 재검증 전 1차 근거).
    _j09_market_data: dict = {}
    try:
        _j09_market_data = {
            "market": _j09_get_market_data() or {},
            "calendar": _j09_get_economic_calendar() or {},
        }
    except Exception as _md_e:
        print(f"  ⚠️ 시장 수치 수집 스킵(게이트 ground truth): {_md_e}")

    cleanup_economic_images(post_naver=post_naver, post_tistory=post_tistory)

    def safe(fn, label, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"  ⚠️ {label} 실패: {e}")
            _g_report("writer", e, module=__name__)
            return None

    # ── 1. 트렌드글 생성·검증·발행 (harness — ★ 플랫폼 단위 끝까지 직렬, 2026-07-03) ──
    # 네이버 액션: ①규정 → ②네이버 대본 → 검증 순환 → 발행 [완전 종결]
    #   → 티스토리 액션: ①규정 → ③티스토리 대본(네이버 주제 제외) → 검증 순환 → 발행
    # 한쪽의 재작성 순환·실패가 다른 쪽을 지연·차단하지 않음 (실패 격리, max_attempts 각 3)
    print("\n📤 [1/4] 경제 브리핑 생성·검증·발행 (harness — 플랫폼 직렬)...")

    from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue
    from JARVIS02_WRITER.trend_economic_writer import (
        nv_collect, nv_generate_draft,
        ts_collect, ts_generate_draft,
        ts_publish, nv_publish,
    )

    # ── Layer 1: precondition (harness 내장 — scheduler 수동 체크 대체) ──────
    # ★ 리뷰 확정 수정 (2026-07-03): 플랫폼별 분리 — 상대 플랫폼 자격증명 결손이
    #   이쪽 발행을 차단하지 않도록 (실패 격리 목표를 Layer 1 까지 관철).
    def _precondition_for(platform: str):
        _plat_keys = (("NV_USERNAME", "NV_PASSWORD") if platform == "naver"
                      else ("TS_URL", "TS_USERNAME", "TS_PASSWORD"))
        # TS_COOKIE 는 precondition 에서 확인하지 않음 — _step_ts_cookie 가 티스토리 액션
        # 시작 시 force=True 로 신선 로그인 후 직접 갱신. 미리 .env 값으로 복원하면
        # 네이버 단계에서 불필요하게 TS_COOKIE를 메모리에 올려두게 됨.

        def _pc(state):
            pc_issues = []
            for _k in _plat_keys + ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
                if not os.environ.get(_k, "").strip():
                    pc_issues.append(Issue(step="① 전제조건", kind="env_missing",
                                           detail=f"환경변수 {_k} 누락"))
            try:
                import importlib as _il
                _il.import_module("JARVIS02_WRITER.collect_theme")
            except Exception as _e:
                pc_issues.append(Issue(step="① 전제조건", kind="import_error",
                                       detail=f"collect_theme import 실패: {type(_e).__name__}: {str(_e)[:80]}"))
            if platform == "naver":
                _nv_cookie = BASE_DIR / "naver_cookies.pkl"
                if not _nv_cookie.exists():
                    pc_issues.append(Issue(step="① 전제조건", kind="cookie_missing",
                                           detail=f"네이버 쿠키 파일 누락: {_nv_cookie.name}"))
            return pc_issues
        return _pc

    @action_step(name="① 규정 로드")
    def _step_load_rules(state):
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        sb = _law_blk()
        print("  📜 [① 규정 로드] BLOG_SUPREME_LAW.md 숙지 완료")
        return {"supreme_block": sb}

    # ★ 직렬 순서 — 네이버 먼저, 티스토리 나중 (사용자 박제 2026-07-03)
    @action_step(name="② NV 수집")
    def _step_nv_collect(state):
        if not state.get("post_naver"):
            print("  ─ [②] 네이버 수집 건너뜀")
            return {"nv_collect_result": {"success": False, "keyword": ""}}
        try:
            result = nv_collect(
                ts_keyword=state.get("ts_keyword_final", ""),
                supreme_block=state.get("supreme_block"),
                market_data=state.get("market_data"),
            )
        except Exception as _e:
            print(f"  ❌ [②] 네이버 수집 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            result = {"success": False, "keyword": ""}
        return {
            "nv_collect_result": result,
            "nv_keyword": result.get("keyword", "") if result.get("success") else "",
        }

    @action_step(name="③ NV 대본 생성")
    def _step_nv_draft(state):
        if not state.get("post_naver"):
            print("  ─ [③] 네이버 대본 건너뜀")
            return {"nv_draft": {"success": False, "keyword": ""}}
        collect_result = state.get("nv_collect_result") or {}
        if not collect_result.get("success"):
            return {"nv_draft": {"success": False, "keyword": "",
                                 "error": collect_result.get("error", "수집 실패")}}
        try:
            draft = nv_generate_draft(
                keyword=collect_result["keyword"],
                sector=collect_result["sector"],
                reason=collect_result["reason"],
                collected=collect_result["collected"],
                supreme_block=collect_result.get("supreme_block"),
                gate_feedback=state.get("_nv_draft_gate_feedback"),
                source_docs=collect_result.get("source_docs"),
            )
        except Exception as _e:
            print(f"  ❌ [③] 네이버 대본 생성 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            draft = {"success": False, "keyword": ""}
        return {"nv_draft": draft}

    @action_step(name="④ TS 쿠키")
    def _step_ts_cookie(state):
        if state.get("ts_driver") is not None:
            print("  ⏭️ [④] 티스토리 driver 이미 준비됨 (재시도 — 재갱신 스킵)")
            return {}
        try:
            from dotenv import load_dotenv
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr
            ok, drv = _tcr(force=False, return_driver=True)
            if ok:
                load_dotenv(override=True)
                return {"ts_driver": drv}
            if drv:
                try: drv.quit()
                except Exception: pass
        except Exception as _e:
            print(f"  ❌ [④] 티스토리 쿠키 갱신 예외: {_e}")
        return {"ts_driver": None}

    @action_step(name="⑤ TS 수집")
    def _step_ts_collect(state):
        if not state.get("post_tistory"):
            print("  ─ [⑤] 티스토리 수집 건너뜀")
            return {"ts_collect_result": {"success": False, "keyword": ""}}
        # ★ 수집 공유 (2026-07-12): 네이버 수집 성공 시 동일 주제·데이터 재사용.
        #   테마주와 동일 구조 — 수집 1회, 플랫폼별 대본만 따로.
        #   네이버 미실행·실패 시에만 ts_collect 독립 수집(폴백).
        shared = state.get("nv_collect_result") or {}
        if shared.get("success"):
            kw = shared.get("keyword", "")
            print(f"  🔗 [⑤] 수집 공유: 네이버 '{kw}' 데이터 재사용 (LLM 0회)")
            return {"ts_collect_result": shared}
        # 폴백: 네이버 수집 없음·실패 → 독립 수집
        try:
            result = ts_collect(
                nv_keyword=state.get("nv_keyword_final", ""),
                supreme_block=state.get("supreme_block"),
                market_data=state.get("market_data"),
            )
        except Exception as _e:
            print(f"  ❌ [⑤] 티스토리 수집 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            result = {"success": False, "keyword": ""}
        return {"ts_collect_result": result}

    @action_step(name="⑥ TS 대본 생성")
    def _step_ts_draft(state):
        if not state.get("post_tistory"):
            print("  ─ [⑥] 티스토리 대본 건너뜀")
            return {"ts_draft": {"success": False, "keyword": ""}}
        collect_result = state.get("ts_collect_result") or {}
        if not collect_result.get("success"):
            return {"ts_draft": {"success": False, "keyword": "",
                                 "error": collect_result.get("error", "수집 실패")}}
        try:
            draft = ts_generate_draft(
                keyword=collect_result["keyword"],
                sector=collect_result["sector"],
                reason=collect_result["reason"],
                collected=collect_result["collected"],
                supreme_block=collect_result.get("supreme_block"),
                gate_feedback=state.get("_ts_draft_gate_feedback"),
                source_docs=collect_result.get("source_docs"),
            )
        except Exception as _e:
            print(f"  ❌ [⑥] 티스토리 대본 생성 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            draft = {"success": False, "keyword": ""}
        return {"ts_draft": draft}

    # ── Layer 3·4 플랫폼 단위 헬퍼 (★ 사용자 박제 2026-07-03 — 플랫폼 끝까지 직렬) ──
    def _verify_platform(state, platform: str, draft_key: str, step_name: str):
        """Layer 3 — *단일 플랫폼* 대본 검증. list[Issue] 반환 (빈 리스트 = 통과).

        ★ 순수 검증만 — 즉시 수정·fingerprint·abort는 harness fix 훅이 담당.
        검증 범위: [L1] 로그인 세션 / [L3] 대본 규정(분량·키워드·이미지·헤더)
        + 발행 전 품질 게이트(사실성·매력도·이미지).
        """
        issues = []
        from datetime import datetime as _dt_v
        print(f"  🔍 [Layer 3] {platform} 검증 진입 [{_dt_v.now().strftime('%H:%M:%S')}]")

        # ── [L1] 로그인 세션 검증 (대본 생성 중 만료 대응) ─────────────────
        # ★ 리뷰 확정 수정 (2026-07-03): verify_all_logins() 는 항상 dict 반환(truthy) —
        #   종전 `if not _verify_logins()` 는 영구 통과 사문. *해당 플랫폼* ok 를 직접 판정.
        try:
            from JARVIS08_PUBLISH.credentials.login_manager import (
                auto_refresh_if_needed as _auto_refresh,
                verify_all_logins as _verify_logins,
            )
            _auto_refresh(platforms=(platform,))   # 현재 플랫폼만 갱신
            _login_res = _verify_logins(platforms=(platform,)) or {}  # 현재 플랫폼만 확인 (Naver 검증 중 Tistory 건드리지 않음)
            _pl = _login_res.get(platform) or {}
            if not _pl.get("ok", True):   # 구조 변경 시 fail-open
                _why = "; ".join(_pl.get("issues") or ["재로그인 필요"])[:150]
                issues.append(Issue(
                    step="① 전제조건",
                    kind="login_invalid",
                    detail=f"{platform} 로그인 세션 무효 — {_why}",
                ))
        except Exception as _le:
            issues.append(Issue(
                step="① 전제조건",
                kind="login_error",
                detail=f"로그인 확인 오류: {_le}",
            ))

        # ── [L3] 단일 플랫폼 대본 규정 준수 검증 (순수 "발견"만) ─────────────
        draft = state.get(draft_key) or {}
        if not draft.get("success"):
            issues.append(Issue(
                step=step_name, kind="draft_failed",
                detail=f"대본 생성 실패: {draft.get('error', 'unknown')}",
            ))
            return issues
        di_list = _layer3_verify_draft(draft, platform)
        for di in di_list:
            issues.append(Issue(step=step_name, kind="draft_quality", detail=di))
        # ★ 발행 전 품질 게이트 (2026-06-28) — 구조 검증 통과 시에만 (LLM 비용 절약).
        #   사실성(차단)·매력도(재생성). kind 가 draft_quality 아니므로 fix 훅이
        #   곧장 unfixed → 해당 WRITER step 재실행 = 재작성 순환.
        if not di_list:
            from JARVIS02_WRITER.prepublish_gate import prepublish_quality_issues
            _pt = (draft.get("post_type") or "economic").strip().lower()
            # ★ 2-2 (2026-07-02): 작성 corpus ↔ 검증 corpus 정합. 작성에 실제 쓴 주제
            #   특화 docs(draft.source_docs) 우선 + 일반 경제 docs 보강(union).
            _used_docs = list(draft.get("source_docs") or [])
            _gen_docs = list(state.get("collection_docs") or [])
            _seen_ids = {id(d) for d in _used_docs}
            _src_docs = _used_docs + [d for d in _gen_docs if id(d) not in _seen_ids]
            for q in prepublish_quality_issues(
                    draft, post_type=_pt,
                    source_docs=_src_docs,
                    market_data=state.get("market_data"),
                    collected=draft.get("collected")):   # ★ Step 10: 통일 grounding (topic_pack)
                issues.append(Issue(step=step_name, kind=q["kind"], detail=q["detail"]))
        return issues

    def _fix_platform(state: dict, issues: list, platform: str,
                      draft_key: str, step_name: str) -> tuple:
        """harness fix 훅 — *단일 플랫폼* draft_quality 인라인 패치 + GUARDIAN 학습.

        흐름: 이슈 발견 → inline 패치(state 직접 수정) → GUARDIAN 2단 박제
              → (fixed, unfixed) 반환. login_invalid / draft_failed 등
        패치 불가 항목은 unfixed 그대로 반환 (harness 가 재생성 순환).
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

        if raw_strs:
            fixed_strs, unfixed_strs = _fx(state, draft_key, platform, raw_strs, "economic")
            for s in fixed_strs:
                fixed_all.append(Issue(step=step_name, kind="draft_fixed", detail=s))
            for s in unfixed_strs:
                unfixed_all.append(Issue(step=step_name, kind="draft_invalid", detail=s))
        return fixed_all, unfixed_all

    def _send_platform(state, platform: str, draft_key: str, publish_fn,
                       ok_key: str, result_key: str, attempted_key: str):
        """Layer 4 — *단일 플랫폼* 발행. 실패 시 raise → 이 플랫폼만 검증 순환 재진입.

        ★ ADR 009 v2 strict + 센티널 (ERRORS [265]):
          attempted 플래그는 시도 *전* 설정 (이중 발행 방지).
          attempt>=2 + 이전 실패(ok=False) → 플래그 해제 → 진짜 재발행 기회.
          harness max_attempts=3 과 함께 플랫폼당 최대 3회 발행 시도.
        """
        from datetime import datetime as _dt_s
        send_attempt = state.get("__send_attempt__", 0) + 1
        state["__send_attempt__"] = send_attempt
        print(f"  📤 [Layer 4] {platform} 발행 진입 (attempt={send_attempt}) "
              f"[{_dt_s.now().strftime('%H:%M:%S')}]")
        published = state.setdefault("published_platforms", set())

        # ★ attempt >= 2 + 이전 실패 → 플래그 해제 (진짜 재발행, ERRORS [265])
        if (send_attempt >= 2 and platform not in published
                and state.get(attempted_key) and not state.get(ok_key)):
            print(f"  🔄 [{platform}] 이전 발행 실패 → 플래그 해제·재발행 시도")
            state[attempted_key] = False

        if platform in published:
            print(f"  ⏭ {platform} 이미 발행 완료 (재시도 스킵)")
            return
        if state.get(attempted_key):
            # 시도 플래그 잔존 + 해제 미발동(=성공 잔존) — 이중 발행 방지로 published 처리
            print(f"  ⚠️ {platform} 발행 이미 시도 완료 (이중 방지)")
            published.add(platform)
            return
        if state.get(draft_key, {}).get("success"):
            state[attempted_key] = True  # 반드시 시도 *전* 에 설정
            _r = publish_fn(state[draft_key])
            state[result_key] = _r
            state[ok_key] = _r.get("success", False)
            if state[ok_key]:
                published.add(platform)
            print(f"  {'✅' if state[ok_key] else '⚠️'} {platform} "
                  f"{'완료' if state[ok_key] else '미발행'}")
        # ★ strict: 미발행이면 raise → 이 플랫폼만 검증 순환 재진입 (타 플랫폼 무영향)
        if platform not in published:
            raise RuntimeError(
                f"[Layer4] ['{platform}'] 발행 실패 (attempt={send_attempt}) — 송출 미완료 → 검증 순환 재진입"
            )

    # ── 하네스 실행 — ★ 플랫폼 단위 끝까지 직렬 (사용자 박제 2026-07-03) ──────
    # 네이버: 대본 → 검증 순환 → 발행 *완전 종결* → 그 다음에야 티스토리 시작.
    # 한쪽의 재작성 순환·실패가 다른 쪽을 지연·차단하지 않는다 (실패 격리).
    _nv_action = ActionDefinition(
        name="경제 브리핑 발행 — 네이버",
        precondition=_precondition_for("naver"),   # ★ Layer 1 — 네이버 자격증명만
        steps=[_step_load_rules, _step_nv_collect, _step_nv_draft],
        verify=lambda st: _verify_platform(st, "naver", "nv_draft", "③ NV 대본 생성"),
        fix=lambda st, iss: _fix_platform(st, iss, "naver", "nv_draft", "③ NV 대본 생성"),
        send=lambda st: _send_platform(st, "naver", "nv_draft", nv_publish,
                                       "naver_ok", "nv_pub_result", "__nv_send_attempted__"),
        max_attempts=3,
        deadline_sec=BLOG_ACTION_DEADLINE_SEC,   # ★ 블로그(플랫폼)당 30분 — 사용자 박제 2026-07-06
    )
    _ts_action = ActionDefinition(
        name="경제 브리핑 발행 — 티스토리",
        precondition=_precondition_for("tistory"),  # ★ Layer 1 — 티스토리 자격증명만
        steps=[_step_load_rules, _step_ts_cookie, _step_ts_collect, _step_ts_draft],
        verify=lambda st: _verify_platform(st, "tistory", "ts_draft", "⑥ TS 대본 생성"),
        fix=lambda st, iss: _fix_platform(st, iss, "tistory", "ts_draft", "⑥ TS 대본 생성"),
        send=lambda st: _send_platform(st, "tistory", "ts_draft", ts_publish,
                                       "tistory_ok", "ts_pub_result", "__ts_send_attempted__"),
        max_attempts=3,
        deadline_sec=BLOG_ACTION_DEADLINE_SEC,   # ★ 블로그(플랫폼)당 30분 — 사용자 박제 2026-07-06
    )

    _results: dict = {}          # platform → ActionResult (EP 결과 파일·incident 용)
    _nv_state: dict = {}
    _ts_state: dict = {}
    naver_ok = tistory_ok = False
    nv_keyword = ts_keyword = ""
    _concurrent_blocked = False

    def _write_ep_partial():
        """★ 리뷰 확정 수정 (2026-07-03): 각 액션 종결 직후 플랫폼 결과를 즉시 기록.

        플랫폼 직렬화로 '네이버 완료 ~ 프로세스 종료' 구간이 티스토리 액션 시간만큼
        길어짐 — 그 사이 subprocess timeout 시 결과 파일이 없으면 incident responder 가
        *이미 발행된 네이버까지* 재발행 (이중 발행). 부분 기록으로 차단.
        """
        _f = os.environ.get("JARVIS_EP_RESULT_FILE", "")
        if not _f:
            return
        try:
            import json as _jp
            with open(_f, "w", encoding="utf-8") as _rf:
                _jp.dump({"naver": bool(naver_ok), "tistory": bool(tistory_ok)}, _rf)
        except Exception:
            pass

    # ★ 발행 기간 LLM 우선권 선언 — background alias(guardian 등) 자동 강등
    #   + 크로스 프로세스 잠금(llm.py)이 함께 동작해 daemon 과 수동 실행 충돌 방지.
    from shared.llm import mark_publishing as _mark_pub
    _mark_pub(True)
    import time as _tm_act
    if post_naver:
        # ★ 액션별 LLM 데드라인 (리뷰 확정 수정): 직렬화로 티스토리 시작이 늦어져
        #   단일 예산이면 티스토리 생성이 상시 강등됨 — 액션마다 리셋.
        #   ★ 반드시 _nv_action.deadline_sec 와 동일한 SSOT 상수 — 더 큰 값을 쓰면
        #   "잔여 <10분 강등"이 harness 하드 데드라인보다 늦게 트리거되어 watchdog 이
        #   재시도·백오프 도중 강제 종료한다(경제 브리핑 티스토리 데드라인 초과 사고 원인).
        os.environ["JARVIS_LLM_DEADLINE_TS"] = str(_tm_act.time() + BLOG_ACTION_DEADLINE_SEC)
        _nv_res = run_action(
            _nv_action,
            input_data={"post_naver": True, "post_tistory": False,
                        "market_data": _j09_market_data},
        )
        _results["naver"] = _nv_res
        _nv_state = _nv_res.state
        naver_ok = bool(_nv_state.get("naver_ok"))
        # ★ 수집 스텝(② NV 수집)이 state["nv_keyword"] 에 저장 — 대본 스킵돼도 키워드 확보
        nv_keyword = _nv_state.get("nv_keyword", "")
        _write_ep_partial()
        if not _nv_res.delivered:
            _esc = getattr(_nv_res, "escalation_reason", "") or ""
            if "동시 실행 중복 차단" in _esc:
                # ★ 리뷰 확정 수정: 다른 실행이 진행 중 — 티스토리도 중단 (인터리브 방지)
                _concurrent_blocked = True
                print("  🚫 동시 실행 중복 차단 — 티스토리 액션도 중단 (인터리브 이중 발행 방지)")
            else:
                print(f"\n  🚫 [네이버] harness max_attempts 도달 — 발행 차단 (attempts={_nv_res.attempts})")
                tg(f"🚫 경제 브리핑(네이버) harness max_attempts 도달 — 발행 차단\nattempts={_nv_res.attempts}")
    else:
        print("  ─ 네이버 건너뜀 (플래그 OFF)")
        # ★ tistory-only 재발행: DB에서 오늘 네이버 경제 발행글의 키워드 복구
        #   → ts_collect 폴백이 오늘 아침과 동일 주제(키워드)를 선택하도록.
        if post_tistory and not nv_keyword:
            try:
                from shared.db import DB_PATH as _DB_PATH
                import sqlite3 as _sq3b, datetime as _dtb
                _today_s = _dtb.date.today().isoformat()
                _con_b = _sq3b.connect(str(_DB_PATH))
                _row_b = _con_b.execute(
                    "SELECT source_keyword FROM post_analysis "
                    "WHERE created_at LIKE ? AND platform='naver' AND post_type='economic' "
                    "ORDER BY id DESC LIMIT 1",
                    (f"{_today_s}%",),
                ).fetchone()
                _con_b.close()
                if _row_b and _row_b[0]:
                    nv_keyword = _row_b[0]
                    print(f"  🔗 [tistory-only] 오늘 네이버 키워드 DB 복구: '{nv_keyword}'")
            except Exception as _nv_ke:
                print(f"  ⚠️ [tistory-only] 네이버 키워드 DB 조회 실패: {_nv_ke}")

    # ★ 네이버 수집 자체가 실패하면(키워드 없음 = topic_pack 빌드 실패) 티스토리도 건너뜀.
    # 동일 수집 경로(topic_pack)를 사용하므로 티스토리도 같은 이유로 실패 예상.
    # post_naver=False 이면 _nv_state 비어있으니 False 로 처리 (건너뜀 안 함).
    _nv_collect_failed = bool(
        post_naver
        and not (_nv_state.get("nv_collect_result") or {}).get("success")
        and not nv_keyword
    )
    if _nv_collect_failed:
        msg = "⏭ [티스토리] 네이버 수집 실패(topic_pack 없음) — 티스토리도 수집 실패 예상, 건너뜀"
        print(f"\n  {msg}")
        tg(msg)

    # ★ 티스토리는 네이버 *종결 후* 에만 시작 — 네이버 성패와 무관하게 독립 진행
    if post_tistory and not _concurrent_blocked and not _nv_collect_failed:
        # ★ _ts_action.deadline_sec 과 동일한 SSOT 상수 (위 네이버 리셋과 동일 사유)
        os.environ["JARVIS_LLM_DEADLINE_TS"] = str(_tm_act.time() + BLOG_ACTION_DEADLINE_SEC)
        _ts_res = run_action(
            _ts_action,
            input_data={"post_naver": False, "post_tistory": True,
                        "market_data": _j09_market_data,
                        "nv_keyword_final": nv_keyword,
                        # ★ 수집 공유 (2026-07-12): 네이버 수집 성공 결과를 ts 액션에 전달.
                        #   _step_ts_collect 가 이 값 확인 → 동일 주제·데이터 재사용.
                        "nv_collect_result": _nv_state.get("nv_collect_result")},
        )
        _results["tistory"] = _ts_res
        _ts_state = _ts_res.state
        tistory_ok = bool(_ts_state.get("tistory_ok"))
        ts_keyword = (_ts_state.get("ts_draft") or {}).get("keyword", "")
        _write_ep_partial()
        if not _ts_res.delivered:
            print(f"\n  🚫 [티스토리] harness max_attempts 도달 — 발행 차단 (attempts={_ts_res.attempts})")
            tg(f"🚫 경제 브리핑(티스토리) harness max_attempts 도달 — 발행 차단\nattempts={_ts_res.attempts}")
    elif _concurrent_blocked:
        print("  ⏭ 티스토리 스킵 — 동시 실행 중복 차단")
    else:
        print("  ─ 티스토리 건너뜀 (플래그 OFF)")
    _mark_pub(False)  # ★ 발행 완료 — background alias 강등 해제

    _nv_r  = '✅' if naver_ok   else ('⏭' if not post_naver   else '❌')
    _ts_r  = '✅' if tistory_ok else ('⏭' if not post_tistory else '❌')
    _all_ok = (not post_naver or naver_ok) and (not post_tistory or tistory_ok)

    tg(
        f"{'🎉' if _all_ok else '⚠️'} [아침 경제 포스팅] 완료\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🟢 네이버 {_nv_r}: {'트렌드글 발행' if (post_naver and naver_ok) else ('미발행' if not post_naver else '실패')}\n"
        f"🔴 티스토리 {_ts_r}: {'트렌드글 ' + ts_keyword if (post_tistory and ts_keyword) else ('미발행' if not post_tistory else '실패')}"
    )

    # ── JARVIS03 품질 분석 연동 ──────────────────────────────────────
    # 발행 성공한 플랫폼마다 post_analysis 레코드 생성 → 분석기 트리거
    if _QUALITY_ENABLED:
        import subprocess as _sp
        # 네이버·티스토리 발행 URL — 각 플랫폼 액션 state 에서 직접 읽음 (ADR 009 v2)
        _naver_pub_url   = _nv_state.get("nv_pub_result", {}).get("url", "")
        _tistory_pub_url = _ts_state.get("ts_pub_result", {}).get("url", "")

        _eco_theme = f"경제 브리핑 {TODAY_STR}"

        def _extract_search_keyword(title: str) -> str:
            """제목에서 검색 가능한 핵심 키워드 추출 (옵션 B 패치 2026-05-04, v2).
            예: "[4/29] 유가 급등·금 조정..."       → "유가 급등"
                "나스닥 0.89% 강세, 금리 하락..."  → "나스닥 강세"  (숫자%·소수점 제거)
                "FOMC 금리 동결..."                → "FOMC 금리"
            """
            import re as _re_kw
            if not title:
                return _eco_theme
            clean = title
            # [bracket], (paren), 날짜 제거
            clean = _re_kw.sub(r'\[[^\]]*\]|\([^\)]*\)', '', clean)
            clean = _re_kw.sub(r'\d{1,4}[/.]\d{1,2}\.?', '', clean)
            # 숫자+%, 숫자+소수점, 단독 숫자 제거 (예: "0.89%", "5.0", "2026")
            clean = _re_kw.sub(r'\b\d+(?:\.\d+)?%?\b', '', clean)
            # 특수문자 → 공백
            clean = _re_kw.sub(r'[·,…?!\-%]+', ' ', clean)
            clean = _re_kw.sub(r'\s+', ' ', clean).strip()
            parts = [p for p in clean.split(' ') if p and len(p) >= _L.MIN_TOKEN_LEN]  # length_manager.MIN_TOKEN_LEN 미만 토큰 제거
            if len(parts) >= 2:
                return f"{parts[0]} {parts[1]}"[:25]
            return parts[0][:25] if parts else _eco_theme

        _empty_art = {"title": f"경제 브리핑 {TODAY_STR}", "content": "", "outro": ""}
        _platforms = [
            ("naver",   post_naver,   naver_ok,   _empty_art, _naver_pub_url,   None, ""),
            ("tistory", post_tistory, tistory_ok, _empty_art, _tistory_pub_url, None, ""),
        ]
        # P2 패치 (2026-05-04): 환경변수 set 후 finally pop 으로 정리.
        # for 루프 전체를 try/finally 로 감싸 platform별 set 하되 함수 종료 시 정리 보장.
        try:
            for _plat, _enabled, _ok, _art, _url, _post_id, _html in _platforms:
                if _enabled and _ok:
                    try:
                        # 검색 가능한 핵심 키워드를 환경변수로 박아 _emit_published 가 source_keyword 로 사용
                        _search_kw = _extract_search_keyword(_art.get("title", ""))
                        os.environ["JARVIS_SOURCE_KEYWORD"] = _search_kw
                        os.environ["JARVIS_POST_TYPE"] = "economic"
                        # 발행 직전 길이 측정 — length_manager.warn_length 단일 호출
                        try:
                            _L.warn_length(_eco_theme, _plat, _art.get("content", ""), label = "경제 브리핑")
                        except Exception:
                            pass
                        # ★ 사용자 박제 2026-05-15 — 빈 content/html 로 중복 emit 차단.
                        # trend_economic_writer / naver_poster / tistory_poster 가 *이미*
                        # on_post_published_detail 을 호출함. economic_poster 의 후속 emit 은
                        # _empty_art (content="") + _html="" 빈 채로 호출 → IDs 111-113 같은
                        # original_html=0 미저장 row 양산 사고 (ERRORS [101]).
                        _content_to_emit = _L.cap_for_publish(_art.get("content", ""), context=f"economic_{_plat}")
                        _html_to_emit = (_L.cap_for_publish(_html, context=f"economic_{_plat}_html") if _html else _html)
                        if not (_content_to_emit or _html_to_emit):
                            print(f"  ℹ️ [{_plat.upper()}] emit skip — 이미 발행 흐름에서 emit 함 (중복 차단)")
                            _aid = None
                        else:
                            _aid = _emit_published(
                                theme=_eco_theme,
                                platform=_plat,
                                title=_art.get("title", ""),
                                url=_url or "",
                                content=_content_to_emit,
                                html=_html_to_emit,
                                post_type="economic",  # 글 종류별 분리 학습 키
                            )
                        _pre_app = _art.get("_pre_applied") or []
                        if _aid and _pre_app:
                            # 사전 수정 적용된 글: revision_patch 저장 + is_revised=1 → 사후 분석/수정 자동 skip
                            try:
                                from shared import db as _db
                                _db.save_pre_revise(_aid, _pre_app)
                                print(f"  ✏️ [{_plat.upper()}] 사전 수정 {len(_pre_app)}건 기록 (id={_aid}, 사후 분석 skip)")
                            except Exception as _e_pr:
                                print(f"  ⚠️ save_pre_revise 실패 (무시): {_e_pr}")
                                _g_report("writer", _e_pr, module=__name__)
                        elif _aid and _ANALYZER_SCRIPT.exists():
                            # 사전 수정 미적용 (분석기 모듈 오류 등) — 기존 사후 분석 흐름 fallback
                            print(f"  📋 [{_plat.upper()}] 품질 분석 등록 (id={_aid}) — fallback")
                            _sp.Popen(
                                [sys.executable, str(_ANALYZER_SCRIPT), str(_aid)],
                                cwd=str(_ANALYZER_SCRIPT.parent),
                            )
                    except Exception as _ex:
                        print(f"  ⚠️ [{_plat}] 품질 분석 등록 실패: {_ex}")
                        _g_report("writer", _ex, module=__name__)
        finally:
            # 환경변수 정리 — 같은 프로세스 내 후속 호출에 carryover 방지 (jarvis_main 패턴과 동일)
            os.environ.pop("JARVIS_SOURCE_KEYWORD", None)
            os.environ.pop("JARVIS_POST_TYPE", None)

    print(f"\n{'='*50}\n")

    # ── JARVIS07 incident_responder 용 플랫폼별 결과 파일 기록 ─────────
    _ep_result_file = os.environ.get("JARVIS_EP_RESULT_FILE", "")
    if _ep_result_file:
        try:
            import json as _jsr
            # ★ 하네스 이슈 구조화 데이터도 함께 기록 (incident_responder 에 정확한 컨텍스트 전달)
            _harness_issues: list[str] = []
            _escalation_reason = ""
            # ★ 플랫폼별 2액션 (2026-07-03) — 실패한 액션들의 이슈를 병합 기록
            for _plat_name, _plat_res in _results.items():
                if getattr(_plat_res, "delivered", True):
                    continue
                _escalation_reason = _escalation_reason or getattr(_plat_res, "escalation_reason", "")
                for _hist in (getattr(_plat_res, "issues_history", None) or []):
                    for _iss in _hist:
                        _harness_issues.append(
                            f"[{_plat_name}] {getattr(_iss,'step','?')}: {getattr(_iss,'kind','?')}: "
                            f"{getattr(_iss,'detail','?')[:120]}"
                        )
            with open(_ep_result_file, "w", encoding="utf-8") as _rf:
                _jsr.dump({
                    "naver": bool(naver_ok),
                    "tistory": bool(tistory_ok),
                    "harness_issues": _harness_issues,
                    "escalation_reason": _escalation_reason,
                }, _rf)
        except Exception:
            pass

    return naver_ok or tistory_ok


if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    # --scheduled 플래그: scheduler.py가 이미 락을 획득한 상태로 호출한 경우 → 락 체크 건너뜀
    scheduled      = "--scheduled"    in sys.argv
    naver_only     = "--naver-only"    in sys.argv
    tistory_only   = "--tistory-only"  in sys.argv
    force          = "--force"         in sys.argv  # 이미 성공이어도 강제 재실행
    any_flag       = naver_only or tistory_only
    post_naver     = naver_only     if any_flag else True
    post_tistory   = tistory_only   if any_flag else True
    LOCK_FILE = BASE_DIR / '.posting.lock'

    if not scheduled:
        # 수동 실행: 락 파일 확인
        if LOCK_FILE.exists():
            age = __import__('time').time() - LOCK_FILE.stat().st_mtime
            if age < 10800:  # 3시간 이내
                try:
                    owner = LOCK_FILE.read_text(encoding='utf-8').split('\n')[0]
                except Exception:
                    owner = "다른 작업"
                print(f"\n⛔ 현재 [{owner}] 진행 중입니다.")
                print(f"   포스팅이 끝날 때까지 기다리거나, 락 파일을 삭제하세요:")
                print(f"   rm {LOCK_FILE}\n")
                sys.exit(1)
        # 수동 실행용 락 파일 생성
        LOCK_FILE.write_text(
            f"경제 브리핑 포스터 (수동)\n{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nPID:{os.getpid()}",
            encoding='utf-8'
        )
        try:
            from JARVIS00_INFRA.watchdog import guard_main
            with guard_main("경제 발행", deadline_sec=3540):   # 부모 60분 backstop보다 먼저 곱게 종료
                run(post_naver=post_naver, post_tistory=post_tistory)
        except Exception as _e:
            _g_report("writer", _e, module=__name__, func_name="run")
            raise
        finally:
            LOCK_FILE.unlink(missing_ok=True)
    else:
        from JARVIS00_INFRA.watchdog import guard_main
        with guard_main("경제 발행", deadline_sec=3540):   # 부모 60분 backstop보다 먼저 곱게 종료
            run(post_naver=post_naver, post_tistory=post_tistory)
