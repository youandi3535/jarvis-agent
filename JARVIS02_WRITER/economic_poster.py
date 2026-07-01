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

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TODAY = datetime.now()
TODAY_STR = TODAY.strftime("%Y년 %m월 %d일")
TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][TODAY.weekday()]


# ══════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════

def tg(msg: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


# ══════════════════════════════════════════
#  데이터 수집
# ══════════════════════════════════════════

def get_market_data() -> dict:
    """★ shim → JARVIS09 단일 진입점 위임 (2026-05-31 이관)."""
    return _j09_get_market_data()


def get_economic_calendar() -> list:
    """★ shim → JARVIS09 단일 진입점 위임 (2026-05-31 이관)."""
    return _j09_get_economic_calendar()


# ══════════════════════════════════════════
#  Claude API로 기사 생성
# ══════════════════════════════════════════

def generate_article(market: dict, calendar: list) -> dict:
    """Claude Code SDK로 경제 브리핑 기사 생성 — 색상 동적 생성 (제11조·제12조)"""
    from shared.llm import invoke_text as _inv_cli
    from JARVIS06_IMAGE.style_engine import generate_sector_colors

    # 시장 데이터 텍스트 변환
    market_text = "\n".join([
        f"- {k}: {v['value']} ({v['change']:+.2f}%)"
        for k, v in market.items()
    ])

    # 경제 캘린더 텍스트 변환
    if calendar:
        cal_text = "\n".join([
            f"- {e['time']} {e['name']}: 실제={e['actual']}, 예상={e['forecast']}, 이전={e['previous']}"
            for e in calendar
        ])
    else:
        cal_text = "오늘은 주요 지표 발표가 없거나 수집 불가"

    # ★ 동적 색상 생성 (제11조·제12조 준수) — 매번 새로운 색상 창작
    _colors = generate_sector_colors(sector="경제_브리핑", keyword=f"{TODAY_STR}_배너")
    _primary = _colors.get("primary_color", "#1a237e")
    _accent = _colors.get("accent_color", "#0d47a1")
    _text_main = _colors.get("text_color", "#ffffff")
    _text_sub = _colors.get("neutral_color", "#bbdefb")
    _color_up = _colors.get("up_color", "#e74c3c")      # 표 상승색
    _color_down = _colors.get("down_color", "#2980b9")  # 표 하락색
    _color_box_bg = _colors.get("primary_color", "#fff3cd")    # 핵심박스 배경
    _color_box_border = _colors.get("accent_color", "#ffc107") # 핵심박스 보더

    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_block
    _supreme = _law_block()

    # ★ 동적 배너 HTML 생성 (색상 변수 주입) — 고정 템플릿 제거
    _banner_html = f"""<div style="background:linear-gradient(135deg,{_primary} 0%,{_accent} 50%,{_accent} 100%);padding:40px 24px;border-radius:12px;text-align:center;margin-bottom:24px;">
  <div style="color:{_accent};font-size:13px;letter-spacing:2px;margin-bottom:8px;">DAILY ECONOMIC BRIEFING</div>
  <div style="color:{_text_main};font-size:26px;font-weight:700;margin-bottom:6px;">📊 경제 브리핑</div>
  <div style="color:{_text_sub};font-size:14px;">{TODAY_STR} ({TODAY_DOW}요일) 시장 브리핑</div>
</div>"""

    prompt = f"""{_supreme}
오늘({TODAY_STR} {TODAY_DOW}요일) 기준으로 개인 투자자를 위한 경제 브리핑 블로그 포스팅을 작성해줘.

[오늘 수집된 시장 데이터]
{market_text}

[오늘 경제 캘린더]
{cal_text}

아래 구성으로 HTML 형식으로 작성해줘. 톤은 캐주얼하고 친근하게 (해요체).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""" + _L_LEN_BLOCK + """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

구성:
0. 썸네일 배너 (본문 맨 처음에 삽입, 아래 HTML 그대로 사용):
{_banner_html}
1. 도입부 ({_L.build_length_phrase(_L.INTRO_TARGET_SENTS)} 이내):
   (헌법 제0조 적용 — 감성 오프닝. 해요체.)
2. 한줄 요약 박스 (핵심 흐름 {ECO_ONELINER_PHRASE}, <div style="background:{_color_box_bg};border-left:4px solid {_color_box_border};padding:12px 16px;margin:16px 0;border-radius:0 8px 8px 0;">📌 오늘의 핵심: ...</div>)
3. <h2>오늘의 시장 현황</h2> + 표 (<table> 태그, 지수명/현재값/등락률 3열) + 표 아래 시장 흐름 해설 {_L.build_length_phrase(_L.SECTION_COMMENTARY_SENTS)}
   - 등락률 셀: 상승(▲)이면 color:{_color_up} (빨간색), 하락(▼)이면 color:{_color_down} (파란색), font-weight:bold 적용
4. <h2>오늘의 경제 브리핑</h2> + 표 (없으면 이번 주 주목 지표 미리보기) + 표 아래 핵심 지표 해설 {_L.build_length_phrase(_L.SECTION_COMMENTARY_SENTS)}
5. <h2>지표 쉽게 이해하기</h2> + 가장 중요한 지표 1개를 초보자도 이해하게 {_L.build_length_phrase(_L.ECO_TERM_EXPLAIN_SENTS)} 내외 설명 (예시·비유 활용)
6. <h2>국내 증시 영향 분석</h2> + 오늘 지표가 국내 증시에 미치는 영향을 ①②③ 3개 항목으로 통합 요약 (수혜·주의 구분 없이 핵심만, 각 항목 대표 종목 1~2개 포함, 전체 {_L.build_length_phrase(_L.ECO_MARKET_IMPACT_SENTS)} 이내)
7. <h2>이번 주 주요 일정</h2> + 날짜/지표명/중요도 표 + 표 아래 핵심 관전 포인트 {_L.build_length_phrase(_L.WEEKLY_INSIGHT_SENTS)}
8. <h2>투자자 행동 가이드</h2> + 단기/중기 행동 지침 {_L.build_length_phrase(_L.ECO_BEHAVIOR_GUIDE_SENTS)} 내외
9. <h2>마무리</h2> + 오늘 요약 + 독자 응원 메시지 {_L.build_length_phrase(_L.OUTRO_TARGET_SENTS)} 내외

HTML 태그 사용, 표는 <table border="1" style="border-collapse:collapse;width:100%;"> 태그로 작성.
※ <h2> 태그는 위 9개 섹션 제목에만 사용할 것. 섹션 내부 소제목은 반드시 <h3> 또는 <strong> 태그 사용.
※ 각 소제목(<h2>·<h3>) 바로 아래 최소 1문장 필수. (헌법 제3조 적용)
※ 본문에 투자 주의사항·면책 조항·권유 관련 문구를 절대 포함하지 말 것.

반드시 아래 형식으로만 답변해:
===INTRO===
(감성 인사말 {_L.build_length_phrase(_L.INTRO_TARGET_SENTS)} 이내, 순수 텍스트만, HTML 태그 없이)
===TITLE===
(제목 {_L.TITLE_MAX}자 이내, 날짜 제외)
===CONTENT===
(HTML 본문 전체, 도입부부터 마무리까지. 감성 인사말은 CONTENT에 포함하지 말 것)
===END===""" \
        .replace("{_L.TITLE_MAX}", str(_L.TITLE_MAX)) \
        .replace("{_L.INTRO_TARGET}", str(_L.INTRO_TARGET)) \
        .replace("{_L.SECTION_COMMENTARY}", str(_L.SECTION_COMMENTARY)) \
        .replace("{_L.ECO_TERM_EXPLAIN}", str(_L.ECO_TERM_EXPLAIN)) \
        .replace("{_L.ECO_MARKET_IMPACT}", str(_L.ECO_MARKET_IMPACT)) \
        .replace("{_L.WEEKLY_INSIGHT}", str(_L.WEEKLY_INSIGHT)) \
        .replace("{_L.ECO_BEHAVIOR_GUIDE}", str(_L.ECO_BEHAVIOR_GUIDE)) \
        .replace("{_L.OUTRO_TARGET}", str(_L.OUTRO_TARGET)) \
        .replace("{ECO_ONELINER_PHRASE}", _L.build_length_phrase(1))

    from shared.personas import get as _persona
    _sys = _persona("jarvis02_writer")
    _full_prompt = f"{_sys}\n\n{prompt}".strip() if _sys else prompt
    _raw_gen = (_inv_cli("writer", _full_prompt, timeout=600) or "").strip()

    import re as _re

    def _parse(raw: str) -> dict:
        try:
            intro   = raw.split("===INTRO===")[1].split("===TITLE===")[0].strip() if "===INTRO===" in raw else ""
            title   = raw.split("===TITLE===")[1].split("===CONTENT===")[0].strip()
            content = raw.split("===CONTENT===")[1].split("===END===")[0].strip()
            return {"title": title, "content": content, "intro": intro}
        except Exception:
            return {"title": f"{TODAY_STR} 경제 브리핑", "content": raw, "intro": ""}

    def _korean_count(html: str) -> int:
        # length_manager.count 위임 — 정규식 일관성
        return _L.count(_re.sub(r'<[^>]+>', '', html))

    result = _parse(_raw_gen)
    kor = _korean_count(result["content"])
    print(f"  📊 한글 글자수: {kor:,}자 (목표: 1,000~{_L.MAX_KOREAN:,}자)")

    if kor > _L.MAX_KOREAN:
        result["content_capped"] = _cap_eco_content(result["content"])
        print(f"    ⚠️ 한글 {kor}자 > {_L.MAX_KOREAN} — content_capped 안전망 생성")
        try:
            tg(f"⚠️ [경제 브리핑] 한글 {kor}자(~{kor//_L.KOREAN_PER_SENTENCE}문장) — 목표 {_L.TARGET_SENTENCES}문장(약 {_L.MAX_KOREAN}자) cap 안전망 작동")
        except Exception:
            pass

    return result


# ── 섹션 구조 공통 템플릿 (동적 색상 대체용) ──
def _build_sections_template(color_up: str, color_down: str, color_box_bg: str, color_box_border: str) -> str:
    """동적 색상을 주입한 섹션 구조 생성 (제11조·제12조 준수)"""
    return f"""섹션 구조 (동일한 <h2> 제목 사용):
※ 썸네일 배너는 시스템 자동 삽입 — 본문에 포함 금지.
[완결 규칙] 모든 문장은 반드시 마침표(.)로 끝낼 것. 문장 중간 종료 절대 금지.

1. 도입부 ({{INTRO_TARGET}}):
   (헌법 제0조·제0-B조 적용 — 감성 오프닝 + 한 <p> 최대 2문장)
   ① 감성문단: 정확히 {{ECO_GREETING_SENTS}}문장 → <p>2문장</p><p>1문장</p> 형태로 분리. 마침표 완결. (해요체)
   ② 핵심박스: 정확히 {{ECO_HIGHLIGHT_SENTS}}문장.
      <div style="background:{color_box_bg};border-left:4px solid {color_box_border};padding:12px 16px;margin:16px 0;border-radius:0 8px 8px 0;">📌 오늘의 핵심: ...</div>

2. <h2>① 간밤 글로벌 시장</h2> ({{SECTION_COMMENTARY}}):
   소개글: 정확히 {{ECO_SEC_INTRO_SENTS}}문장. 마침표 완결.
   표: 지수명/현재값/등락률 (상승▲ color:{color_up} 하락▼ color:{color_down} font-weight:bold)
   분석글: 정확히 {{ECO_SEC_ANALYSIS_SENTS}}문장. 마침표 완결.

3. <h2>② 오늘 주목할 지표</h2> ({{SECTION_COMMENTARY}}):
   소개글: 정확히 {{ECO_SEC_INTRO_SENTS}}문장. 마침표 완결.
   표: 지표명/실제치/예상치/이전치
   분析글: 정확히 {{ECO_SEC_ANALYSIS_SENTS}}문장. 마침표 완결.

4. <h2>③ 지표 쉽게 이해하기</h2> ({{ECO_TERM_EXPLAIN}}):
   가장 중요한 지표 1개를 <h3> 소제목 2개로 나눠 초보자 설명.
   각 <h3> 아래 정확히 {{ECO_SEC_TERM_MIN}}~{{ECO_SEC_TERM_MAX}}문장. 예시·비유 활용. 마침표 완결.

5. <h2>④ 국내 증시 영향</h2> ({{ECO_MARKET_IMPACT}}):
   오늘 지표가 국내 증시에 미치는 영향 ①②③ 3개 항목.
   <p>① ...</p><p>② ...</p><p>③ ...</p> — 각 항목 정확히 {{ECO_SEC_ITEM_SENTS}}문장. 대표종목 1~2개 포함. 마침표 완결.

6. <h2>⑤ 이번 주 일정 & 포인트</h2> ({{WEEKLY_INSIGHT}}):
   소개글: 정확히 {{ECO_SEC_INTRO_SENTS}}문장. 마침표 완결.
   표: 날짜/지표명/중요도
   분析글: 정확히 {{ECO_SEC_WEEKLY_SENTS}}문장. 핵심 관전 포인트. 마침표 완결.

7. <h2>⑥ 투자자 행동 가이드</h2> ({{ECO_BEHAVIOR_GUIDE}}):
   오늘 시장 상황에 맞는 구체적 행동 지침 ①②③ 3개 항목.
   <p>① ...</p><p>② ...</p><p>③ ...</p> — 각 항목 정확히 {{ECO_SEC_ITEM_SENTS}}문장. 단기·중기 구분. 마침표 완결.

8. <h2>마무리</h2> ({{OUTRO_TARGET}}):
   오늘 핵심 요약 {{ECO_OUTRO_SUMMARY_SENTS}}문장 + 응원 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)} + 면책 {_L.build_length_phrase(_L.DISCLAIMER_INLINE_SENTS)}. 마침표 완결.
   (면책: 헌법 제5조 적용 — 표현은 매번 다양하게 LLM 생성)

표: <table border="1" style="border-collapse:collapse;width:100%;">
※ 표 있는 섹션: <h2>→<p>소개</p>→<table>→<p>분析</p> | 표 없는 섹션: <h2>→<p>내용(<h3>가능)</p>
※ ①②③은 항목마다 별도 <p> 태그. 표 데이터 텍스트 반복 금지.
※ 각 <h2>·<h3> 바로 아래 최소 1문장 필수. (헌법 제3조 적용)
★ [글+이미지 교차 규정 — 절대 금지 위반] ★
   - 시스템이 각 섹션 단락 사이에 이미지를 자동 삽입함. 따라서 반드시 아래 규칙 준수:
   - 표 있는 섹션: [소개글 <p>] → [표] → [분析글 <p>] 순서 엄수. 표 앞·뒤 텍스트 필수.
   - 표 없는 섹션(③④⑥ 등): 내용을 반드시 2개 이상의 <p> 단락으로 분리 작성.
     예) <p>첫 단락 내용.</p><p>두 번째 단락 내용.</p> ← 이미지 삽입 공간 확보됨.
   - 섹션 마지막 내용이 텍스트로 끝나야 함. 마지막 텍스트 뒤에 이미지가 오고 그 다음 섹션이 시작되는 구조 금지.
   - 잘못된 예: <p>글A</p><p>글B</p> ← 마지막 2개 단락 연속, 이미지 공간 없음.
   - 올바른 예: <p>글A</p> (이미지 삽입됨) <p>글B</p> (다음 섹션 시작)"""



def generate_articles_triple(market: dict, calendar: list) -> dict:
    """
    ⚠️ DEPRECATED — 실제 실행 경로는 generate_article_single (플랫폼별 5000토큰) 사용.
    이 함수는 dead code. 호출 시 generate_article_single로 redirect.
    """
    import warnings
    warnings.warn(
        "generate_articles_triple은 deprecated입니다. generate_article_single을 플랫폼별로 호출하세요.",
        DeprecationWarning, stacklevel=2
    )
    result = {}
    for p in ["naver", "tistory"]:
        result[p] = generate_article_single(p, market, calendar)
    return result


def generate_article_single(platform: str, market: dict, calendar: list) -> dict:
    """단일 플랫폼용 기사 생성 (naver / tistory) — 색상 동적 생성 (제11조·제12조).
    Returns: {"intro": ..., "title": ..., "content": ..., "outro": ...}
    """
    from shared.llm import invoke_text as _inv_cli
    from JARVIS06_IMAGE.style_engine import generate_sector_colors
    import re as _re

    market_text = "\n".join([
        f"- {k}: {v['value']} ({v['change']:+.2f}%)"
        for k, v in market.items()
    ])
    cal_text = "\n".join([
        f"- {e['time']} {e['name']}: 실제={e['actual']}, 예상={e['forecast']}, 이전={e['previous']}"
        for e in calendar
    ]) if calendar else "오늘은 주요 지표 발표가 없거나 수집 불가"

    # ★ 동적 색상 생성 (제11조·제12조 준수) — 플랫폼별 다른 색상
    _colors = generate_sector_colors(sector=f"경제_브리핑_{platform}", keyword=f"{TODAY_STR}_{platform}")
    _color_up = _colors.get("up_color", "#e74c3c")      # 상승 (빨강)
    _color_down = _colors.get("down_color", "#2980b9")  # 하락 (파랑)
    _color_box_bg = _colors.get("primary_color", "#fff3cd")    # 핵심 박스 배경
    _color_box_border = _colors.get("accent_color", "#ffc107") # 핵심 박스 보더

    # 분량 한도 (lo, hi) 는 length_manager 단일 진입점 — 정책 변경 시 거기만 수정
    _styles = {
        "naver": ("네이버", "일반 투자자 대상. 친근한 해요체. 이모지 최소화(8개 이하). 쉬운 비유와 설명."),
        "tistory": ("티스토리", "트렌드에 민감한 독자. 간결하고 핵심만. 인사이트 중심. 세련된 문체."),
    }
    label, style = _styles[platform]
    lo, hi = _L.MIN_VALID, _L.MAX_KOREAN

    # SEO 심화 지침 (seo_standards 단일 진실 소스) — f-string 밖에서 미리 조합
    try:
        _eco_seo_raw = _build_seo_block(platform, theme="경제 브리핑")
    except Exception:
        _eco_seo_raw = ""
    _eco_seo_block = ("[SEO 심화 지침]\n" + _eco_seo_raw + "\n") if _eco_seo_raw else ""

    # ★ 동적 색상이 주입된 섹션 템플릿 생성 (제11조·제12조)
    SECTIONS = _build_sections_template(_color_up, _color_down, _color_box_bg, _color_box_border) \
        .replace("{INTRO_TARGET}", _L.build_length_phrase(_L.INTRO_TARGET_SENTS)) \
        .replace("{SECTION_COMMENTARY}", _L.build_length_phrase(_L.SECTION_COMMENTARY_SENTS)) \
        .replace("{ECO_TERM_EXPLAIN}", _L.build_length_phrase(_L.ECO_TERM_EXPLAIN_SENTS)) \
        .replace("{ECO_MARKET_IMPACT}", _L.build_length_phrase(_L.ECO_MARKET_IMPACT_SENTS)) \
        .replace("{WEEKLY_INSIGHT}", _L.build_length_phrase(_L.WEEKLY_INSIGHT_SENTS)) \
        .replace("{ECO_BEHAVIOR_GUIDE}", _L.build_length_phrase(_L.ECO_BEHAVIOR_GUIDE_SENTS)) \
        .replace("{OUTRO_TARGET}", _L.build_length_phrase(_L.OUTRO_TARGET_SENTS)) \
        .replace("{ECO_GREETING_SENTS}문장",      _L.build_length_phrase(_L.ECO_GREETING_SENTS)) \
        .replace("{ECO_HIGHLIGHT_SENTS}문장",     _L.build_length_phrase(_L.ECO_HIGHLIGHT_SENTS)) \
        .replace("{ECO_SEC_INTRO_SENTS}문장",     _L.build_length_phrase(_L.ECO_SEC_INTRO_SENTS)) \
        .replace("{ECO_SEC_ANALYSIS_SENTS}문장",  _L.build_length_phrase(_L.ECO_SEC_ANALYSIS_SENTS)) \
        .replace("{ECO_SEC_TERM_MIN}~{ECO_SEC_TERM_MAX}문장", _L.build_length_phrase(_L.ECO_SEC_TERM_MIN, _L.ECO_SEC_TERM_MAX)) \
        .replace("{ECO_SEC_ITEM_SENTS}문장",      _L.build_length_phrase(_L.ECO_SEC_ITEM_SENTS)) \
        .replace("{ECO_SEC_WEEKLY_SENTS}문장",    _L.build_length_phrase(_L.ECO_SEC_WEEKLY_SENTS)) \
        .replace("{ECO_OUTRO_SUMMARY_SENTS}문장", _L.build_length_phrase(_L.ECO_OUTRO_SUMMARY_SENTS))

    # ── 누적 학습 지침 — 초기 생성 단계 주입 (learning_insights → 초안 prompt) ──
    _eco_learn_section = ""
    try:
        from shared import db as _shared_db
        _li_rows = _shared_db.get_top_learning_insights(limit=8, days=14, scope="economic")
        if _li_rows:
            _ll = [
                "",
                "─" * 30,
                "📚 *과거 글 분석에서 도출된 작성 지침* — 이번 글 작성 시 반드시 적용:",
                "",
            ]
            for _i, _r in enumerate(_li_rows, 1):
                _d = (_r.get("directive") or _r.get("description") or "").strip()
                _occ = _r.get("occurrences", 1)
                _sc = _r.get("scope", "all")
                _stag = "" if _sc == "all" else f" [{_sc}]"
                if _d:
                    _ll.append(f"{_i}.{_stag} {_d}  (재발견 {_occ}회)")
            _eco_learn_section = "\n".join(_ll) + "\n"
            print(f"  📚 경제 브리핑 글 학습 지침 {len(_li_rows)}개 주입됨")
    except Exception as _le:
        print(f"  ⚠️ 학습 블록 로드 실패(무시): {_le}")
        _g_report("writer", _le, module=__name__)

    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_block
    _supreme = _law_block()

    prompt = f"""{_supreme}
오늘({TODAY_STR} {TODAY_DOW}요일) 기준 경제 브리핑을 {label} 블로그용으로 작성해줘.

[오늘 수집된 시장 데이터]
{market_text}

[오늘 경제 캘린더]
{cal_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[완결 규칙 — 이 규칙은 어떤 이유로도 어길 수 없음]
- 헌법 제0조·제0-B조·제3조·제4조 적용 (위 헌법 블록 참조 — 감성 오프닝·단락 2문장·빈 헤더 금지·이미지 교차).
- 모든 문장은 반드시 마침표(.)로 끝낼 것. 문장 중간 종료 절대 금지.
- 각 섹션은 지정된 문장 수만큼만 작성. 더 많이 쓰지 말 것.
- 도입부: 정확히 {_L.build_length_phrase(_L.ECO_GREETING_SENTS)}. 마무리: 정확히 {_L.build_length_phrase(_L.ECO_OUTRO_SUMMARY_SENTS)}.
- 글+이미지 교차 규정(절대): 시스템이 각 섹션 <p> 단락 사이에 이미지를 자동 삽입함.
  표 있는 섹션은 반드시 [소개글<p>]→[표]→[분析글<p>] 순서 엄수 (표 앞뒤 텍스트 필수).
  표 없는 섹션은 내용을 2개 이상 <p> 단락으로 분리 작성할 것 (이미지 삽입 공간 확보).
  섹션 끝에 <p> 단락 2개 이상이 연속되는 구조 금지 — 이미지가 들어갈 자리가 없어짐.

[스타일]
{style}

{_eco_seo_block}━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_eco_learn_section}
{SECTIONS}

반드시 아래 형식으로만 답변해:
===INTRO===
(감성 인사말 정확히 {_L.build_length_phrase(_L.ECO_GREETING_SENTS)}. 마침표 완결. 순수 텍스트, 해요체.
 헌법 제0조 적용 — 첫 문장 감성 오프닝.)
===TITLE===
(제목 {_L.TITLE_MAX}자 이내, 날짜·연도·월일 절대 포함 금지)
===CONTENT===
(HTML 본문 전체. 각 섹션 지정 문장 수 엄수. 썸네일 배너 div 포함 금지)
===OUTRO===
(마무리 인사말 정확히 {_L.build_length_phrase(_L.ECO_OUTRO_SUMMARY_SENTS)}. 마침표 완결. 순수 텍스트, 해요체)
===END===""".replace("{TODAY_STR}", TODAY_STR).replace("{TODAY_DOW}", TODAY_DOW)

    def _kor(html): return _L.count(_re.sub(r'<[^>]+>', '', html))

    def _parse(raw: str) -> dict:
        try:
            intro   = raw.split("===INTRO===")[1].split("===TITLE===")[0].strip()
            title   = raw.split("===TITLE===")[1].split("===CONTENT===")[0].strip()
            content = raw.split("===CONTENT===")[1].split("===OUTRO===")[0].strip()
            outro   = raw.split("===OUTRO===")[1].split("===END===")[0].strip()
            return {"intro": intro, "title": title, "content": content, "outro": outro}
        except Exception:
            from shared.llm import invoke_text as _llm_pt
            _fallback_title = _llm_pt(
                "writer_fast",
                f"경제 브리핑 블로그 포스팅 제목 1개만 출력. 날짜 미포함. {_L.ECO_TITLE_PROMPT_MAX}자 이내. 한국어. 제목만.",
                max_tokens=30, temperature=0.7
            ) or "오늘의 경제 브리핑 핵심 정리"
            return {"intro": "", "title": _fallback_title, "content": raw, "outro": ""}

    from shared.personas import get as _persona
    _sys = _persona("jarvis02_writer")
    _full = f"{_sys}\n\n{prompt}".strip() if _sys else prompt
    result = _parse((_inv_cli("writer", _full, timeout=600) or "").strip())
    # 제목 날짜 패턴 제거
    import re as _re_date2
    _dp = _re_date2.compile(r'\d{4}년\s*\d{1,2}월\s*\d{1,2}일\s*|\d{4}-\d{2}-\d{2}\s*|\d{1,2}/\d{1,2}\s*경제\s*지표')
    _clean_title = _dp.sub("", result["title"]).strip()
    if not _clean_title:
        from shared.llm import invoke_text as _llm_ct
        _clean_title = _llm_ct(
            "writer_fast",
            f"경제 브리핑 블로그 포스팅 제목 1개만 출력. 날짜 미포함. {_L.ECO_TITLE_PROMPT_MAX}자 이내. 한국어. 제목만.",
            max_tokens=30, temperature=0.7
        ) or "오늘의 경제 브리핑 핵심 정리"
    result["title"] = _clean_title
    kor = _kor(result["content"])
    print(f"  📊 {label} 한글 글자수: {kor:,}자 (목표: 1,000~{_L.MAX_KOREAN:,}자)")

    # ★ 글자수 초과 시 content_capped로 실제 사용 (이전: 생성만 하고 미사용)
    if kor > _L.MAX_KOREAN:
        _capped = _cap_eco_content(result["content"])
        result["content"] = _capped  # ← 실제 content로 사용
        result["content_capped"] = _capped  # ← 기록용 백업도 유지
        kor_capped = _kor(_capped)
        print(f"    ⚠️ {label} 한글 {kor}자 > {_L.MAX_KOREAN} — 자동 압축 적용 → {kor_capped}자")
        try:
            tg(f"⚠️ [경제 브리핑 {label}] 한글 {kor}자 → {kor_capped}자 압축")
        except Exception:
            pass

    return result


def _apply_shared_placeholders(html: str, table_imgs: list) -> str:
    """
    table_imgs 인덱스를 다른 플랫폼 HTML에 적용.
    <table>과 <h2> 태그를 순서대로 [[TABLE_N]]으로 교체 (이미지 재생성 없음).
    """
    import re as _re
    modified = html
    idx = [0]

    # 1) <table> 교체
    def _repl_table(m):
        n = idx[0]
        if n < len(table_imgs):
            idx[0] += 1
            return f'[[TABLE_{n}]]'
        return m.group(0)
    modified = _re.sub(r'<table[\s\S]*?</table>', _repl_table, modified, flags=_re.IGNORECASE)

    # 2) <h2> 교체
    def _repl_h2(m):
        n = idx[0]
        if n < len(table_imgs):
            idx[0] += 1
            return f'[[TABLE_{n}]]'
        return m.group(0)
    modified = _re.sub(r'<h2[^>]*>.*?</h2>', _repl_h2, modified, flags=_re.IGNORECASE | _re.DOTALL)

    return modified


from JARVIS08_PUBLISH.category import ECONOMIC_CATEGORY, ECONOMIC_TAGS_DEFAULT  # noqa: F401
TODAY_PREFIX       = f"[{TODAY.month}/{TODAY.day}]"

def generate_thumbnail(market: dict, out_dir: Path | None = None) -> str:
    """경제 브리핑 썸네일 — AI 사진 기반 (image_agent.generate_thumbnail)."""
    from JARVIS06_IMAGE.economic_charts import generate_thumbnail as _gen
    return _gen(market, out_dir=out_dir or ECONOMIC_IMG_DIR)


def generate_insight_card(heading: str, idx: int, out_dir: Path | None = None) -> str:
    """섹션 헤더 소제목 배너 — section_title 방식 (economic_charts.generate_insight_card)."""
    from JARVIS06_IMAGE.economic_charts import generate_insight_card as _gen
    return _gen(heading, idx, out_dir=out_dir or ECONOMIC_IMG_DIR)


def inject_text_summary_images(blocks: list, num_tables: int,
                                table_img_paths: list, for_tistory: bool = False) -> list:
    """표 없는 섹션(h2카드 → 텍스트만 있는 경우)에 텍스트 요약 이미지를 자동 삽입.
    구조: [h2-img] [text] → [h2-img] [text] [summary-img]
    """
    # h2 카드 경로 집합 (num_tables 이후 인덱스)
    h2_paths = set(table_img_paths[num_tables:])
    new_blocks = []
    summary_idx = 0
    i = 0

    while i < len(blocks):
        btype, bdata = blocks[i]
        new_blocks.append(blocks[i])

        # h2 카드 이미지 다음 패턴 감지
        if btype == 'image' and bdata in h2_paths:
            # 다음 블록이 텍스트이고, 그 다음이 h2 카드거나 끝인 경우 → 표 없는 섹션
            if (i + 1 < len(blocks) and blocks[i + 1][0] == 'text'):
                next_after_text = blocks[i + 2] if i + 2 < len(blocks) else None
                has_table_next = (
                    next_after_text is not None
                    and next_after_text[0] == 'image'
                    and next_after_text[1] not in h2_paths
                )
                if not has_table_next:
                    # 텍스트 블록을 먼저 추가
                    i += 1
                    new_blocks.append(blocks[i])
                    text_content = blocks[i][1]
                    # 요약 이미지 생성 & 삽입
                    try:
                        summary_path = generate_text_summary_card(str(text_content), summary_idx)
                        if summary_path:
                            if for_tistory:
                                new_blocks.append(('html',
                                    f'<figure class="blog-image size-full">'
                                    f'<img src="{summary_path}" style="width:100%;"/>'
                                    f'</figure>'))
                                new_blocks.append(('html',
                                    '<p style="font-size:14px;color:#555;line-height:1.8;">'
                                    '위 이미지에 이 섹션의 핵심 내용을 한눈에 정리했습니다. '
                                    '복잡한 경제 흐름도 핵심만 파악하면 훨씬 쉽게 이해할 수 있습니다. '
                                    '투자 결정에 앞서 꼭 참고해 주시기 바랍니다.</p>'))
                            else:
                                new_blocks.append(('image', summary_path))
                                new_blocks.append(('text',
                                    '위 이미지에 이 섹션의 핵심 내용을 한눈에 정리해 봤어요. '
                                    '복잡한 경제 흐름도 핵심만 잡으면 훨씬 쉽게 이해할 수 있어요. '
                                    '투자 결정에 앞서 꼭 참고해 주세요.'))
                            summary_idx += 1
                    except Exception as e:
                        print(f"  ⚠️ 요약 카드 생성 실패: {e}")
                        _g_report("writer", e, module=__name__)
        i += 1

    return new_blocks


def _enforce_min_text_after_images(blocks: list, min_chars: int = None,
                                    for_tistory: bool = False) -> list:
    """이미지 바로 다음 텍스트 블록이 length_manager.IMG_FOLLOWUP_MIN 미만이면 보완 문장을 덧붙임."""
    if min_chars is None:
        min_chars = _L.IMG_FOLLOWUP_MIN
    SUPPLEMENT = (
        " 위 내용을 꼼꼼히 확인하고 오늘의 시장 흐름을 파악해 보세요. "
        "경제 지표 하나하나가 여러분의 투자 결정에 중요한 단서가 될 수 있어요."
    )
    SUPPLEMENT_HTML = (
        " 위 내용을 꼼꼼히 확인하고 오늘의 시장 흐름을 파악해 보시기 바랍니다. "
        "경제 지표 하나하나가 투자 결정에 중요한 단서가 될 수 있습니다."
    )
    result = list(blocks)
    for i in range(len(result)):
        btype, bdata = result[i]
        if btype in ('text', 'html') and i > 0 and result[i - 1][0] == 'image':
            text_len = len(str(bdata).replace('\n', '').strip())
            if text_len < min_chars:
                if btype == 'html':
                    # </p> 직전에 보완 문장 삽입
                    new_data = str(bdata).rstrip()
                    if new_data.endswith('</p>'):
                        new_data = new_data[:-4] + SUPPLEMENT_HTML + '</p>'
                    else:
                        new_data += f'<p style="font-size:14px;color:#555;">{SUPPLEMENT_HTML}</p>'
                    result[i] = ('html', new_data)
                else:
                    result[i] = ('text', str(bdata).strip() + SUPPLEMENT)
    return result


def _ensure_image_text_alternation(blocks: list, for_tistory: bool = False) -> list:
    """최종 안전망: 연속된 이미지 블록 사이에 설명 텍스트(length_manager.IMG_FOLLOWUP_MIN+)를 삽입해 image+image 패턴 제거."""
    from shared.llm import invoke_text as _llm_sep
    _sep_raw = _llm_sep(
        "writer_fast",
        f"경제 블로그에서 차트 이미지 두 개 사이에 들어가는 자연스러운 연결 설명 {_L.build_length_phrase(1, _L.MAX_P_SENTS)}. 해요체. 문장만 출력.",
        max_tokens=80, temperature=0.8
    ) or "위 지표를 함께 참고해 주세요."
    _sep_html_raw = _llm_sep(
        "writer_fast",
        f"경제 블로그에서 차트 이미지 두 개 사이 연결 설명 {_L.build_length_phrase(1, _L.MAX_P_SENTS)}. 합니다체. 문장만 출력.",
        max_tokens=80, temperature=0.8
    ) or "위 지표를 함께 참고해 주시기 바랍니다."
    FALLBACK_TEXT = _sep_raw
    FALLBACK_HTML = f'<p style="font-size:14px;color:#555;line-height:1.8;">{_sep_html_raw}</p>'
    result = []
    for i, (btype, bdata) in enumerate(blocks):
        result.append((btype, bdata))
        # 현재가 이미지이고 다음도 이미지이면 사��에 텍스트 삽입
        if btype == 'image' and i + 1 < len(blocks) and blocks[i + 1][0] == 'image':
            if for_tistory:
                result.append(('html', FALLBACK_HTML))
            else:
                result.append(('text', FALLBACK_TEXT))
    return result


def generate_filler_image(idx: int, market: dict = None, out_dir: Path | None = None) -> str:
    from JARVIS06_IMAGE.economic_charts import generate_filler_image as _j06
    return _j06(idx, market, out_dir=out_dir or ECONOMIC_IMG_DIR)


def generate_quote_card(text_snippet: str, idx: int, market: dict = None) -> str:
    return generate_filler_image(idx, market)


def build_naver_blocks(
    html_ph: str,
    table_img_paths: list,
    thumbnail_path: str,
    chart_path: str,
    filler_paths: list,
    intro: str = '',
    outro: str = '',
    num_tables: int = 3,
) -> list:
    """네이버 블록 생성 (로컬 파일 경로 직접 사용).
    html_ph([[TABLE_N]] 포함)를 BeautifulSoup으로 파싱해
    배너/중복 제거 후 블록 리스트 반환.

    filler 배치 전략:
      filler_0: intro_p 뒤 / 핵심요약 앞 (parts[0] 내부 분리)
      filler_1: 시장현황 데이터표 분석글 뒤
      filler_2: 경제 브리핑 데이터표 분析글 뒤
      filler_3: 국내증시 ✅수혜 뒤 / ⚠️주의 앞 (Price Action)
      filler_4: 지표이해 내용 뒤
      filler_5~7: 이후 섹션 순차
    """
    from bs4 import BeautifulSoup as _BS4
    from JARVIS08_PUBLISH.platforms.naver_poster import html_to_naver_text
    import re as _re

    blocks = []
    filler_idx = [0]

    # ★ ERRORS [136] 사용자 박제 2026-05-17 — 썸네일 본문 반복 차단:
    # thumbnail_path 를 _used_images 에 *사전 등록* 하여 본문 파싱·filler 배치 시
    # 같은 경로 이미지 추가되지 못하게 함.
    _used_image_paths: set[str] = set()

    # ── 썸네일 + 인트로 ──────────────────────────────────────
    if thumbnail_path and Path(thumbnail_path).exists():
        blocks.append(('image', thumbnail_path))
        _used_image_paths.add(str(Path(thumbnail_path).resolve()))
    if intro:
        print(f"  📝 네이버 인트로 삽입: {len(intro)}자")
        blocks.append(('text', intro))
    # ※ filler_0 (인트로 직후) 삽입은 _maybe_filler 정의 후에 실행 (아래 참고)

    # ── [[TABLE_N]] 기준으로 분리 ────────────────────────────
    parts = _re.split(r'\[\[TABLE_(\d+)\]\]', html_ph)
    pending_chart = False

    CHART_CAPTION = (
        "위 차트는 주요 시장 지수의 전일 대비 등락률을 한눈에 비교할 수 있도록 정리한 그래프예요. "
        "S&P500, 나스닥 등 글로벌 지수의 방향성을 파악하는 데 참고해 주세요."
    )

    def _numbered_spacing(text: str) -> str:
        """①②③ 앞 빈 줄 보장 + 첫째/둘째/셋째 → ①②③ 변환"""
        # 원형 숫자 앞에 빈 줄 추가 (변환 없이 유지)
        circle_list = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨']
        for circle in circle_list:
            text = text.replace(circle, f'\n\n{circle} ')

        # 첫째/둘째/셋째/넷째 → ①②③ 형식 변환
        korean_map = [('첫째', '①'), ('둘째', '②'), ('셋째', '③'), ('넷째', '④'),
                      ('다섯째', '⑤')]
        for kor, circle in korean_map:
            text = _re.sub(rf'{kor}[,，]?\s*', f'\n\n{circle} ', text)

        # 연속 빈 줄 3개 이상 → 2개로 정리
        text = _re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    def _parse_segment(html_seg: str, is_first: bool) -> list:
        """HTML 세그먼트 → 텍스트/이미지 블록 리스트 반환"""
        seg_blocks = []
        soup = _BS4(html_seg, 'html.parser')
        pending_h3 = None

        def _flush_h3():
            nonlocal pending_h3
            if pending_h3:
                seg_blocks.append(('heading', pending_h3))
                pending_h3 = None

        for el in soup.children:
            if not hasattr(el, 'name') or not el.name:
                continue
            tag = el.name
            style = el.get('style', '') if hasattr(el, 'get') else ''

            if tag == 'div':
                if 'linear-gradient' in style:
                    continue
                if 'f0f4ff' in style:
                    continue
                _flush_h3()
                text = html_to_naver_text(str(el)).strip()
                if text:
                    seg_blocks.append(('text', _numbered_spacing(text)))

            elif tag == 'p':
                text = html_to_naver_text(str(el)).strip()
                if is_first and intro and text and intro[:25] in text:
                    continue
                if pending_h3:
                    if text:
                        seg_blocks.append(('heading', pending_h3))
                        seg_blocks.append(('text', _numbered_spacing(text)))
                    else:
                        seg_blocks.append(('heading', pending_h3))
                    pending_h3 = None
                elif text:
                    seg_blocks.append(('text', _numbered_spacing(text)))

            elif tag in ('h2',):
                _flush_h3()
                text = el.get_text(strip=True)
                if text:
                    seg_blocks.append(('heading2', text))

            elif tag == 'h3':
                _flush_h3()
                pending_h3 = el.get_text(strip=True)

            elif tag == 'figure':
                _flush_h3()
                img = el.find('img')
                if img:
                    src = img.get('src', '')
                    filename = src.split('/')[-1].split('?')[0]
                    local = ECONOMIC_IMG_DIR / filename
                    if local.exists():
                        seg_blocks.append(('image', str(local)))

        _flush_h3()
        return seg_blocks

    def _get_filler(idx: int) -> list:
        """특정 인덱스의 필러 반환 (범위 초과 시 빈 리스트)"""
        if idx < len(filler_paths) and filler_paths[idx] and Path(filler_paths[idx]).exists():
            return [('image', filler_paths[idx])]
        return []

    def _maybe_filler() -> list:
        """순차 필러 반환 (filler_idx 자동 증가)"""
        fi = filler_idx[0]
        filler_idx[0] += 1
        return _get_filler(fi)

    # ── filler_0: 인트로 직후 삽입 (감성글 → 이미지 → 핵심요약 순서 보장) ─────
    # _maybe_filler 정의 후에 실행 (위에서 함수 미정의 상태 호출 방지)
    if intro:
        blocks.extend(_maybe_filler())   # filler_idx: 0 → 1

    # ── [[TABLE_N]] 기준 분리 결과 확인 ─────────────────────────────────────
    print(f"  🔍 [빌드] parts={len(parts)}개, intro={'있음' if intro else '없음'}({len(intro)}자), thumbnail={'있음' if thumbnail_path and Path(thumbnail_path).exists() else '없음'}")

    # ── parts[0]: intro_p → (filler_0은 이미 intro 직후 삽입됨) → 핵심요약박스 ──
    # intro가 있으면 filler_0은 위에서 이미 삽입됨 → parts[0]에서는 filler 재호출 생략
    if parts:
        seg0_html = parts[0].strip()
        # 첫 번째 <p> 태그 끝 위치 찾기
        first_p_end = seg0_html.find('</p>')
        if first_p_end != -1:
            first_p_end += 4
            first_p_html = seg0_html[:first_p_end]
            rest_html = seg0_html[first_p_end:].strip()

            # intro_p 텍스트 (intro와 중복이면 스킵)
            first_p_text = html_to_naver_text(first_p_html).strip()
            if first_p_text and (not intro or intro[:25] not in first_p_text):
                blocks.append(('text', first_p_text))

            # filler_0: intro 없을 때만 여기서 삽입 (intro 있으면 위에서 이미 삽입됨)
            if not intro:
                blocks.extend(_maybe_filler())   # filler_idx: 0 → 1

            # 핵심요약박스 등 나머지
            if rest_html:
                rest_seg = _parse_segment(rest_html, is_first=False)
                rest_seg = [b for b in rest_seg if b[0] == 'text']
                blocks.extend(rest_seg)
        else:
            seg0 = _parse_segment(seg0_html, is_first=True)
            seg0 = [b for b in seg0 if b[0] == 'text']
            if seg0:
                blocks.append(seg0[0])          # 오늘 시장 소개글 (첫 텍스트)
                if not intro:
                    blocks.extend(_maybe_filler())  # filler_0: 소개글 뒤 / 핵심요약 앞 (intro 없을 때만)
                blocks.extend(seg0[1:])         # 핵심요약박스 등 나머지
            else:
                if not intro:
                    blocks.extend(_maybe_filler())  # filler_0 (텍스트 없어도 배치, intro 없을 때만)

    # ── 메인 루프 ─────────────────────────────────────────────────────────────
    # num_tables: 실제 데이터표 개수 (0..num_tables-1 = 데이터표, num_tables+ = h2 카드)
    # filler_idx는 이미 1 (filler_0 사용 후)
    # 배치: filler_1=시장표분석뒤, filler_2=경제브리핑분析뒤, filler_3=수혜/주의 사이,
    #       filler_4=지표이해뒤, filler_5+=이후순차
    print(f"  🔍 [빌드] parts[0] 처리 후 blocks={len(blocks)}개, filler_idx={filler_idx[0]}")
    data_table_count = [0]  # 지금까지 처리한 데이터표 개수

    for i in range(1, len(parts)):
        part = parts[i]

        if i % 2 == 1:
            # 홀수 → TABLE 인덱스
            tbl_idx = int(part)
            if tbl_idx < len(table_img_paths) and table_img_paths[tbl_idx]:
                p = table_img_paths[tbl_idx]
                if Path(p).exists():
                    blocks.append(('image', p))
            # 첫 데이터표(tbl_idx==0) 다음 차트 예약
            if tbl_idx == 0 and chart_path:
                pending_chart = True
            # 데이터표 카운트
            if tbl_idx < num_tables:
                data_table_count[0] += 1

        else:
            # 짝수 → 텍스트 세그먼트
            seg = _parse_segment(part, is_first=False)
            seg = [b for b in seg if b[0] == 'text']

            # ── 국내증시 섹터 감지: ✅수혜 + ⚠️주의 동시 포함 → filler_3 삽입 ──
            combined_text = ' '.join(b[1] for b in seg)
            has_benefit  = '✅' in combined_text or '수혜 가능' in combined_text
            has_caution  = '⚠️' in combined_text or '주의가 필요한' in combined_text

            if has_benefit and has_caution:
                # ⚠️ 또는 '주의가 필요한' 앞에서 분리
                before, after = [], []
                in_caution = False
                for b in seg:
                    if not in_caution and ('⚠️' in b[1] or '주의가 필요한' in b[1]):
                        in_caution = True
                    (after if in_caution else before).append(b)
                blocks.extend(before)
                # filler_3 (Price Action) 고정 삽입
                blocks.extend(_get_filler(3))
                blocks.extend(after)
                # 이 섹션은 sequential filler 스킵 (filler_3 이미 삽입)
            else:
                # 다음 TABLE이 데이터표인지 미리 확인
                next_tbl_is_data = False
                if i + 1 < len(parts) and (i + 1) % 2 == 1:
                    next_tbl_idx = int(parts[i + 1])
                    next_tbl_is_data = next_tbl_idx < num_tables

                # filler_3은 국내증시 전용 → 순차 흐름에서 건너뜀
                if not next_tbl_is_data and filler_idx[0] == 3:
                    filler_idx[0] = 4

                should_filler = seg and not next_tbl_is_data

                # 텍스트 블록 2개 이상인 섹션 → 중간에 필러 삽입 (2개: 1번째 뒤, 3개: 2번째 뒤)
                if should_filler and len(seg) >= 2:
                    mid = (len(seg) + 1) // 2
                    blocks.extend(seg[:mid])
                    blocks.extend(_maybe_filler())
                    blocks.extend(seg[mid:])
                else:
                    blocks.extend(seg)
                    if should_filler:
                        blocks.extend(_maybe_filler())

                # 차트: 텍스트 뒤에 삽입
                if pending_chart:
                    if chart_path and Path(chart_path).exists():
                        blocks.append(('image', chart_path))
                        blocks.append(('text', CHART_CAPTION))
                    pending_chart = False

    # 루프 후 차트 미삽입 시 추가
    if pending_chart and chart_path and Path(chart_path).exists():
        blocks.append(('image', chart_path))
        blocks.append(('text', CHART_CAPTION))

    # 아웃트로
    if outro:
        blocks.append(('text', outro))

    # 투자 주의사항 분리: 마지막 텍스트 블록에서 추출 → 블록 끝(이미지 뒤)으로 이동
    # 목표 순서: 마무리 내용 → filler 이미지 → 투자 주의사항
    _disc_kws = ('본 포스팅은 투자 권유', '투자에 대한 최종', '참고용으로만 활용', '투자 주의사항')
    _disc_block = None
    for _bi in range(len(blocks) - 1, -1, -1):
        if blocks[_bi][0] == 'text':
            _txt = blocks[_bi][1]
            for _kw in _disc_kws:
                _idx = _txt.find(_kw)
                if _idx != -1:
                    _cut = _txt.rfind('\n', 0, _idx)
                    _main = _txt[:_cut].strip() if _cut != -1 else ''
                    _disc = _txt[(_cut + 1 if _cut != -1 else 0):].strip()
                    if _main:
                        blocks[_bi] = ('text', _main)
                    else:
                        blocks.pop(_bi)
                    _disc_block = ('text', _disc)
                    break
            if _disc_block:
                break

    if _disc_block:
        blocks.append(_disc_block)
    else:
        # ★ 2026-05-15 면책 문구 강제 추가 — 기존 추출 실패 시 동적 생성
        # (제5조: 투자 글 마지막은 2문장(약 100자) 면책 의무)
        try:
            from shared.llm import invoke_text
            disclaimer_prompt = (
                f"투자 관련 글 마지막에 넣을 {_L.build_length_phrase(_L.DISCLAIMER_SENTS)}의 면책 문구를 생성하세요.\n"
                "내용: 1)정보 제공용이지 투자 권유가 아님, 2)투자 결정과 책임은 본인\n"
                "예시: '본 글은 투자 참고용 정보 제공 목적이며 매수·매도 권유가 아닙니다. "
                "모든 투자 결정과 그 결과는 본인의 책임임을 명심해 주세요.'\n"
                "면책 문구만 출력하세요."
            )
            disclaimer = invoke_text("writer_fast", disclaimer_prompt)
            if disclaimer:
                blocks.append(('text', disclaimer.strip()))
                print(f"  ✅ 면책 문구 추가: {len(disclaimer)}자")
        except Exception as e:
            # fallback: 최소 면책 문구
            fallback_disc = (
                "본 글은 금융 정보 제공 목적이며 투자 권유가 아닙니다. "
                "투자 결정과 책임은 본인에게 있습니다."
            )
            blocks.append(('text', fallback_disc))
            print(f"  ⚠️ 면책 문구 폴백 사용: {e}")

    # ★ ERRORS [136] 사용자 박제 2026-05-17 — 썸네일·이미지 중복 발행 차단:
    # 같은 image path 가 본문에 반복되면 *둘째 등장부터 skip*. 썸네일이 본문에 또
    # 들어오는 사고 + AI 사진 fallback 같은 파일 재사용 사고 모두 차단.
    _seen_paths: set[str] = set()
    _deduped: list = []
    _removed = 0
    for _btype, _bdata in blocks:
        if _btype == 'image' and isinstance(_bdata, str):
            try:
                _resolved = str(Path(_bdata).resolve())
            except Exception:
                _resolved = _bdata
            if _resolved in _seen_paths:
                _removed += 1
                continue
            _seen_paths.add(_resolved)
        _deduped.append((_btype, _bdata))
    if _removed:
        print(f"  🧹 [경로 dedupe] 중복 이미지 {_removed}건 제거 (썸네일 본문 반복 차단)")
    blocks = _deduped

    return blocks


def split_and_inject_images(blocks: list, for_tistory: bool = False) -> list:
    """긴 텍스트/HTML 블록(length_manager.BLOCK_SPLIT_THRESHOLD+)을 단락으로 쪼개고 매 3단락마다 KEY POINT 카드 삽입."""
    from JARVIS08_PUBLISH.platforms.naver_poster import html_to_naver_text
    import re as _re

    result = []
    quote_idx = [0]

    for btype, bdata in blocks:
        if btype not in ('text', 'html'):
            result.append((btype, bdata))
            continue

        text_content = html_to_naver_text(str(bdata)) if btype == 'html' else str(bdata)
        # length_manager.BLOCK_SPLIT_THRESHOLD 미만은 그냥 통과
        if len(text_content) < _L.BLOCK_SPLIT_THRESHOLD:
            result.append((btype, bdata))
            continue

        # HTML 블록의 단락 분리: </p> 기준
        if btype == 'html':
            raw = str(bdata)
            # <p> 태그 기준으로 단락 분리
            paras = _re.split(r'(?<=</p>)', raw)
            paras = [p.strip() for p in paras if p.strip()]
        else:
            paras = [p.strip() for p in str(bdata).split('\n\n') if p.strip()]

        if len(paras) <= 3:
            result.append((btype, bdata))
            continue

        # 3단락마다 카드 삽입
        for i, para in enumerate(paras):
            if btype == 'html':
                result.append(('html', para))
            else:
                result.append(('text', para))

            # 3번째 단락마다 (i=2, 5, 8...) 카드 삽입 (마지막 그룹 제외)
            if (i + 1) % 3 == 0 and i < len(paras) - 1:
                # 현재 단락에서 핵심 문장 추출 (첫 문장)
                plain = html_to_naver_text(para) if btype == 'html' else para
                sentences = [s.strip() for s in plain.replace('\n', ' ').split('.') if len(s.strip()) > 15]
                snippet = sentences[0] if sentences else plain[:60]
                try:
                    card = generate_quote_card(snippet, quote_idx[0])
                    if card:
                        result.append(('image', card))
                        quote_idx[0] += 1
                except Exception as e:
                    print(f"  ⚠️ 인용카드 생성 실패: {e}")
                    _g_report("writer", e, module=__name__)

    return result


def inject_extra_charts(blocks: list, sector_chart: str) -> list:
    """국내 증시 분석 텍스트 블록 뒤에 섹터 차트 주입 (이미지+이미지 방지: 캡션 포함)"""
    result = []
    done = False
    for btype, bdata in blocks:
        result.append((btype, bdata))
        if not done and sector_chart and btype in ('text', 'html'):
            txt = str(bdata)
            if any(kw in txt for kw in ('수혜 섹터', '수혜주', '조선', '반도체', '섹터별', '수출주')):
                result.append(('image', sector_chart))
                caption = '국내 주요 섹터별 오늘 시장 데이터 기반 영향도 추정입니다. 수혜·주의 섹터를 파악해 투자에 참고하세요.'
                result.append(('text', caption) if btype == 'text' else ('html', f'<p style="font-size:14px;color:#555;line-height:1.8;">{caption}</p>'))
                done = True
    return result


def render_market_table(market: dict) -> str:
    """시장 현황 표 이미지 — JARVIS06_IMAGE.economic_charts 위임."""
    from JARVIS06_IMAGE.economic_charts import render_market_table as _gen
    return _gen(market, out_dir=ECONOMIC_IMG_DIR)


def render_calendar_table(calendar: list):
    """경제 캘린더 표 이미지 — JARVIS06_IMAGE.economic_charts 위임."""
    from JARVIS06_IMAGE.economic_charts import render_calendar_table as _gen
    return _gen(calendar, out_dir=ECONOMIC_IMG_DIR)


def render_html_table_as_image(table_html: str, idx: int):
    """HTML 테이블 하나를 PNG 이미지로 렌더링 — JARVIS06_IMAGE 위임."""
    from JARVIS06_IMAGE.economic_charts import render_html_table_as_image as _j06
    return _j06(table_html, idx, out_dir=ECONOMIC_IMG_DIR)


def extract_table_images(html_content: str) -> tuple:
    """HTML에서 모든 <table>과 <h2>/<h3>를 추출 → PNG 이미지 변환
    Returns: (html_with_placeholders, [img_path, ...])
    """
    import re as _re
    from bs4 import BeautifulSoup as _BS4
    table_imgs = []
    modified = html_content

    # [1] 테이블 → 이미지
    tables = _re.findall(r'<table[\s\S]*?</table>', modified, flags=_re.IGNORECASE)
    for i, table_html in enumerate(tables):
        try:
            img_path = render_html_table_as_image(table_html, i)
            if img_path:
                table_imgs.append(img_path)
                modified = modified.replace(table_html, f'[[TABLE_{i}]]', 1)
        except Exception as e:
            print(f"  ⚠️ 테이블 {i} 변환 실패: {e}")
            _g_report("writer", e, module=__name__)

    # [2] H2/H3 제목 → 섹션 헤더 카드 이미지
    _img_idx  = [len(table_imgs)]  # table_imgs 내 실제 인덱스
    _h2_disp  = [0]               # 카드에 표시되는 번호 (01, 02, …)

    def _replace_heading(m):
        raw_html = m.group(1)
        heading_text = _BS4(raw_html, 'html.parser').get_text(strip=True)
        heading_text = _re.sub(r'[\U0001F000-\U0001FFFF]', '', heading_text)
        heading_text = _re.sub(r'[\u2600-\u27BF]', '', heading_text).strip()
        # \uC18C\uC81C\uBAA9 \uC774\uBBF8\uC9C0 \uC67C\uCABD \uD328\uB110\uC774 \uC774\uBBF8 \uBC88\uD638\uB97C \uD45C\uC2DC\uD558\uBBC0\uB85C, \uD14D\uC2A4\uD2B8 \uC55E \uC911\uBCF5 \uBC88\uD638\u00B7\uAE30\uD638 \uC81C\uAC70
        # \uC608: \u2460 \u2461 \u2462 / 1. 2. / (1) (2) / \u25B6 \u25BA \u25CF \u25A0 \u25A1 \u25C6 \u25B7 \u00B7 \u2022 \uB4F1
        heading_text = _re.sub(
            r'^[\\u2460\u2461\u2462\u2463\u2464\u2465\u2466\u2467\u2468\u2469\u246A\u246B\u246C\u246D\u246E'
            r'\u2460-\u2473'          # \u2460~\u2473 \uC720\uB2C8\uCF54\uB4DC \uBE14\uB85D
            r'\u24B6-\u24FF'          # \u24B6\u24B7\u2026 \uC6D0\uBB38\uC790 \uD655\uC7A5
            r']\s*', '', heading_text)
        heading_text = _re.sub(r'^\d+[.)]\s*', '', heading_text)   # 1. 1) 2. 2)
        heading_text = _re.sub(r'^\(\d+\)\s*', '', heading_text)   # (1) (2)
        heading_text = _re.sub(r'^[\u25B6\u25BA\u25CF\u25A0\u25A1\u25C6\u25B7\u00B7\u2022\-\u2013\u2014]\s*', '', heading_text)  # \uAE30\uD638 \uBD88\uB9BF
        heading_text = heading_text.strip()
        if not heading_text:
            return ''
        n = _img_idx[0]
        try:
            img_path = generate_insight_card(heading_text, _h2_disp[0])
            if img_path:
                table_imgs.append(img_path)
                _img_idx[0] += 1
                _h2_disp[0] += 1
                return f'[[TABLE_{n}]]'
        except Exception as e:
            print(f"  ⚠️ 섹션헤더 카드 실패 ({heading_text[:20]}): {e}")
            _g_report("writer", e, module=__name__)
        return heading_text  # 실패 시 텍스트로 유지

    modified = _re.sub(
        r'<h2[^>]*>(.*?)</h2>',
        _replace_heading,
        modified,
        flags=_re.IGNORECASE | _re.DOTALL
    )
    print(f"  ✅ {len(table_imgs)}개 이미지 생성 (테이블 {len(tables)}개 + 섹션헤더 {_h2_disp[0]}개)")
    return modified, table_imgs, len(tables)  # num_tables 반환 (h2 vs table 구분용)


# ══════════════════════════════════════════
#  스마트 태그 생성
# ══════════════════════════════════════════

def generate_smart_tags(title: str, content: str) -> list:
    """제목 + 본문 핵심어 기반 태그 6개 생성, 최근 10일치만 중복 방지"""
    from shared.llm import invoke_text as _inv_cli
    from JARVIS08_PUBLISH.platforms.naver_poster import html_to_naver_text

    tags_file = BASE_DIR / 'economic_used_tags.json'
    used = json.load(open(tags_file, encoding='utf-8')) if tags_file.exists() else []
    # 최근 20개만 중복 방지 (너무 많으면 Claude가 신조어 만들어냄)
    recent = used[-20:]
    recent_str = ', '.join(recent) if recent else '없음'

    plain_snippet = html_to_naver_text(content)[:800]

    # 제목 → 2개
    _title_raw = _inv_cli(
        "writer",
        f"아래 제목에서 핵심 태그 2개를 뽑아줘.\n"
        f"규칙: 공백 없이, {_L.TAG_MAX}자 이하, 한국어, 아래 최근 태그와 중복 금지: {recent_str}\n"
        f"쉼표로만 구분, 2개만 출력. 번호·설명 없이 태그만.\n\n"
        f"제목: {title}",
        timeout=60
    ) or ""
    # 본문 → 2개
    _body_raw = _inv_cli(
        "writer",
        f"아래 본문에서 핵심 태그 2개를 뽑아줘.\n"
        f"규칙: 공백 없이, {_L.TAG_MAX}자 이하, 한국어, 아래 최근 태그와 중복 금지: {recent_str}\n"
        f"쉼표로만 구분, 2개만 출력. 번호·설명 없이 태그만.\n\n"
        f"본문: {plain_snippet}",
        timeout=60
    ) or ""

    def _parse(text):
        return [t.strip().replace(' ', '').lstrip('0123456789.-)')
                for t in text.replace('\n', ',').split(',') if t.strip()
                and 2 <= len(t.strip()) <= 10 and not t.strip().isdigit()]

    title_tags = [t for t in _parse(_title_raw) if t not in recent][:2]
    body_tags  = [t for t in _parse(_body_raw)  if t not in recent and t not in title_tags][:2]
    tags = title_tags + body_tags

    # 부족하면 기본 태그로 보충
    if len(tags) < 4:
        for t in ECONOMIC_TAGS_DEFAULT:
            if t not in tags:
                tags.append(t)
            if len(tags) >= 4:
                break

    tags = tags[:4]
    used.extend(tags)
    with open(tags_file, 'w', encoding='utf-8') as f:
        json.dump(used[-300:], f, ensure_ascii=False)
    return tags


# ══════════════════════════════════════════
#  블록 구성
# ══════════════════════════════════════════

def _convert_naver_blocks_to_haeyoche(blocks: list) -> list:
    """네이버 블록의 평문 텍스트를 해요체로 일괄 변환.
    이미지 블록은 그대로 유지. 평문만 변환하므로 구조 오염 없음.
    """
    # length_manager.SHORT_BLOCK_THRESHOLD 미만 짧은 블록은 변환 불필요 (어미 없는 단문/제목 등)
    text_indices = [i for i, (t, d) in enumerate(blocks) if t == 'text' and len(str(d)) >= _L.SHORT_BLOCK_THRESHOLD]
    if not text_indices:
        return blocks

    SEP = "\n\n<<<BLOCK>>>\n\n"
    combined = SEP.join(str(blocks[i][1]) for i in text_indices)

    try:
        from shared.llm import invoke_text as _inv_cli
        converted = (_inv_cli(
            "writer",
            f"아래 한국어 텍스트를 친근한 해요체로 변환해줘.\n"
            f"규칙:\n"
            f"- 격식체 어미(~합니다/~습니다/~입니다/~됩니다)만 해요체(~해요/~어요/~이에요/~돼요)로 변환\n"
            f"- 이모지·부호·줄바꿈 등 형식 변경 금지\n"
            f"- 내용 추가/삭제 금지. 어미만 변환\n"
            f"- <<<BLOCK>>> 구분자 개수와 위치는 절대 변경 금지\n"
            f"- 변환된 텍스트만 출력. 안내문·설명·인사말 절대 추가 금지\n\n"
            f"{combined}",
            timeout=300
        ) or "").strip()
        converted_parts = converted.split("<<<BLOCK>>>")

        # Claude가 앞에 안내문을 붙인 경우 첫 번째 파트 제거
        if len(converted_parts) == len(text_indices) + 1:
            converted_parts = converted_parts[1:]

        for i, idx in enumerate(text_indices):
            if i < len(converted_parts):
                blocks[idx] = ('text', converted_parts[i].strip())
        print(f"  ✅ 네이버 해요체 변환 완료 ({len(text_indices)}개 블록)")
    except Exception as e:
        print(f"  ⚠️ 해요체 변환 실패 (원문 유지): {e}")
        _g_report("writer", e, module=__name__)

    return blocks


def _split_disclaimer(html: str) -> tuple:
    """본문 HTML에서 투자 주의사항 단락을 분리해 반환.
    Returns: (main_html, disclaimer_html or None)
    """
    import re as _re
    # <p> 또는 <div> 태그 중 투자 면책 관련 키워드 포함 단락 분리
    # '투자 참고용 정보', '참고용으로만', '투자 권유', '투자에 대한', '투자 판단', '매수·매도 권유' 등 모두 커버
    patterns = [
        r'(<(?:p|div)[^>]*>(?:(?!</?(?:p|div)).)*?(?:투자\s*권유|투자에\s*대한|투자\s*판단|참고용으로만|투자\s*참고용|매수.매도\s*권유|책임은\s*투자자|최종\s*투자\s*결정)(?:(?!</?(?:p|div)).)*?</(?:p|div)>)',
        r'(<p[^>]*>.*?(?:투자\s*권유|투자에\s*대한|투자\s*판단|참고용으로만|투자\s*참고용|매수.매도\s*권유|책임은\s*투자자|최종\s*투자\s*결정).*?</p>)',
    ]
    # ※ 로 시작하는 투자 주의 plain text 단락도 제거 (HTML 태그 밖에 있을 경우)
    plain_pat = _re.compile(
        r'※[^\n]*(?:투자\s*참고용|매수.매도\s*권유|최종\s*투자\s*결정|투자\s*결정|투자를?\s*권유|투자\s*권유|손익은?\s*투자자|책임은?\s*투자자|정보\s*제공용)[^\n]*\n?',
        flags=_re.IGNORECASE
    )
    html = plain_pat.sub('', html)

    for pat in patterns:
        matches = list(_re.finditer(pat, html, flags=_re.DOTALL | _re.IGNORECASE))
        if matches:
            last = matches[-1]
            main = html[:last.start()].rstrip()
            disc = last.group(0)
            return main, disc
    return html, None


def build_blocks_with_tables(
    html_content_with_placeholders: str,
    table_img_paths: list,
    thumbnail_path: str,
    chart_path: str,
    for_tistory: bool = False,
    intro: str = '',
    outro: str = '',
    filler_paths: list = None,
) -> list:
    """테이블 플레이스홀더가 있는 HTML을 블록 리스트로 변환
    구조: 썸네일 → 감성인트로 → 구분선 → 섹션헤더이미지 → 텍스트 → 테이블이미지 → ...
    """
    import re as _re
    from JARVIS08_PUBLISH.platforms.naver_poster import html_to_naver_text

    blocks = []

    if thumbnail_path:
        blocks.append(('image', thumbnail_path))

    # 감성 인트로 삽입 (썸네일 직후)
    if intro:
        print(f"  📝 인트로 삽입: {len(intro)}자 — '{intro[:30]}...'")
        if for_tistory:
            blocks.append(('html',
                f'<div style="background:#f0f4ff;border-left:4px solid #5c9eda;'
                f'padding:14px 18px;border-radius:0 8px 8px 0;font-size:15px;'
                f'line-height:1.9;color:#333;margin:8px 0;">{intro}</div>'))
        else:
            blocks.append(('text', intro))
    else:
        print("  ⚠️ 인트로 비어있음 — 블록에 추가 안 됨")

    # [[TABLE_0]], [[TABLE_1]] ... 으로 분리
    parts = _re.split(r'\[\[TABLE_(\d+)\]\]', html_content_with_placeholders)
    pending_chart = False  # 차트 삽입 예약 (텍스트 블록 뒤에 삽입하기 위해 지연)
    _filler_paths = list(filler_paths) if filler_paths else []
    filler_idx = [0]

    CHART_CAPTION = (
        "위 차트는 주요 시장 지수의 전일 대비 등락률을 한눈에 비교할 수 있도록 정리한 그래프예요. "
        "S&P500, 나스닥, 코스피 등 글로벌 지수가 오늘 어떤 방향으로 움직였는지 한눈에 확인하고, "
        "포트폴리오 조정에 참고해 보세요."
    )

    def _next_filler():
        fi = filler_idx[0]
        filler_idx[0] += 1
        if fi < len(_filler_paths) and _filler_paths[fi] and Path(_filler_paths[fi]).exists():
            return [('image', _filler_paths[fi])]
        return []

    # ── 인트로 직후 필러 이미지 삽입 (감성글 → 이미지 순서 보장, 네이버/티스토리 공통) ──
    if intro:
        blocks.extend(_next_filler())

    # ── parts[0]: 핵심요약박스 처리 (banner/intro는 위에서 이미 추가됨) ───────────
    if parts:
        seg0 = parts[0].strip()
        # intro와 동일한 p태그 내용만 제거하고 나머지(핵심요약박스 등) 삽입
        if seg0:
            if for_tistory:
                # intro p태그 제거 후 나머지 삽입
                clean0 = _re.sub(r'^<p[^>]*>.*?</p>\s*', '', seg0, count=1, flags=_re.DOTALL).strip()
                if clean0:
                    blocks.append(('html', clean0))
            else:
                from bs4 import BeautifulSoup as _BS4
                from JARVIS08_PUBLISH.platforms.naver_poster import html_to_naver_text as _h2n
                soup0 = _BS4(seg0, 'html.parser')
                texts0 = []
                for el in soup0.children:
                    if not hasattr(el, 'name') or not el.name:
                        continue
                    style0 = el.get('style', '') if hasattr(el, 'get') else ''
                    if el.name == 'div' and ('linear-gradient' in style0 or 'f0f4ff' in style0):
                        continue
                    t = _h2n(str(el)).strip()
                    if t and (not intro or intro[:25] not in t):
                        texts0.append(('text', t))
                blocks.extend(texts0)

    # ── 메인 루프: TABLE → 텍스트 → 차트 → 필러 ──────────────────────────────────
    for i in range(1, len(parts)):
        part = parts[i]

        if i % 2 == 1:
            # 홀수 → TABLE 인덱스
            tbl_idx = int(part)
            if tbl_idx < len(table_img_paths) and table_img_paths[tbl_idx]:
                blocks.append(('image', table_img_paths[tbl_idx]))
            if tbl_idx == 0 and chart_path:
                pending_chart = True

        else:
            # 짝수 → 텍스트/HTML
            has_content = False
            if for_tistory:
                clean = part.strip()
                if clean:
                    blocks.append(('html', clean))
                    has_content = True
                if pending_chart and clean:
                    blocks.append(('image', chart_path))
                    blocks.append(('html',
                        f'<p style="font-size:14px;color:#555;line-height:1.8;">'
                        f'{CHART_CAPTION}</p>'))
                    pending_chart = False
            else:
                plain = html_to_naver_text(part).strip()
                plain = _re.sub(r'\n{3,}', '\n\n', plain)
                if plain:
                    blocks.append(('text', plain))
                    has_content = True
                if pending_chart and plain:
                    blocks.append(('image', chart_path))
                    blocks.append(('text', CHART_CAPTION))
                    pending_chart = False

            # 필러: 텍스트 세그먼트마다 순서대로 삽입
            if has_content:
                blocks.extend(_next_filler())

    # 루프 종료 후에도 pending_chart가 남아 있으면 마지막에 추가
    if pending_chart:
        blocks.append(('image', chart_path))
        if for_tistory:
            blocks.append(('html',
                f'<p style="font-size:14px;color:#555;line-height:1.8;">'
                f'{CHART_CAPTION}</p>'))
        else:
            blocks.append(('text', CHART_CAPTION))
        pending_chart = False

    # 투자 주의사항 분리: 본문에서 추출해 임시 저장
    disclaimer_block = None
    for bi in range(len(blocks) - 1, -1, -1):
        btype, bdata = blocks[bi]
        if btype in ('text', 'html'):
            if btype == 'html':
                main_html, disc_html = _split_disclaimer(str(bdata))
                if disc_html:
                    blocks[bi] = ('html', main_html)
                    disclaimer_block = ('html', disc_html)
            else:
                text = str(bdata)
                disc_markers = ['투자 권유', '투자에 대한', '투자 판단', '참고용으로만']
                found_idx = -1
                for marker in disc_markers:
                    idx = text.find(marker)
                    if idx != -1:
                        split_pt = text.rfind('\n', 0, idx)
                        if split_pt == -1:
                            split_pt = text.rfind('. ', 0, idx)
                            split_pt = split_pt + 2 if split_pt != -1 else 0
                        found_idx = split_pt
                        break
                if found_idx > 0:
                    blocks[bi] = ('text', text[:found_idx].rstrip())
                    disc_text = text[found_idx:].strip()
                    # 해요체 → ~다 체 변환 (투자 주의사항 전용)
                    import re as _re_disc
                    disc_text = _re_disc.sub(r'있어요\.?', '있습니다.', disc_text)
                    disc_text = _re_disc.sub(r'바랍니다\.?', '바랍니다.', disc_text)
                    disclaimer_block = ('text', disc_text)
            break

    # 마무리 감성 멘트 먼저 삽입
    if outro:
        if for_tistory:
            blocks.append(('html',
                f'<div style="background:#f0f4ff;border-left:4px solid #5c9eda;'
                f'padding:14px 18px;border-radius:0 8px 8px 0;font-size:15px;'
                f'line-height:1.9;color:#333;margin:16px 0;">{outro}</div>'))
        else:
            blocks.append(('text', outro))

    # 투자 주의사항은 맨 마지막에 삽입
    if disclaimer_block:
        blocks.append(disclaimer_block)

    return blocks


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

def _eco_inject_para_images(blocks: list, keyword: str = '경제') -> list:
    """경제 브리핑 네이버 블록 — text 블록 내 2문장 초과 시 단락 분리 + AI 이미지 삽입.

    CLAUDE.md 모든 블로그글 동일 기준:
    - 단락(\n\n 또는 2문장 초과) 사이마다 이미지 1개 (마지막 제외)
    - Pollinations.ai 무료 API 사용
    """
    import urllib.request
    import urllib.parse
    import hashlib
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime

    para_img_dir = ECONOMIC_IMG_DIR / "naver_para"
    para_img_dir.mkdir(parents=True, exist_ok=True)

    _KO_EN = {
        '금리': 'interest rate chart', '환율': 'currency exchange', '주가': 'stock price',
        '인플레이션': 'inflation graph', '경제': 'economic data', '지표': 'economic indicator',
        '소비자': 'consumer price', '생산': 'industrial production', '고용': 'employment data',
    }
    def _make_prompt(text: str) -> str:
        hits = [v for k, v in _KO_EN.items() if k in text]
        base = ' '.join(hits[:2]) if hits else 'economic indicator analysis'
        return (f"professional data visualization of {base}, "
                f"clean infographic, financial chart, minimal white background, "
                f"blue accent colors, no text overlay")

    def _gen_para_img(text: str, idx: int) -> str:
        try:
            prompt = _make_prompt(text)
            # ★ JARVIS06_IMAGE 단일 진입점 위임 (Pollinations 단독 — Bing/HF 폐기 2026-06-07)
            from JARVIS06_IMAGE.image_agent import generate_photo as _gen_photo
            seed = int(hashlib.md5(
                f"{datetime.now().strftime('%Y-%m-%d')}_{keyword}_eco_{idx}".encode()
            ).hexdigest(), 16) % 9999
            dest = para_img_dir / f"eco_para_{idx:04d}.png"
            result = _gen_photo(
                prompt_ko="", prompt_en=prompt,
                out_dir=para_img_dir,
                width=1200, height=630, seed=seed,
            )
            # rename to expected path
            if result and result.exists() and result != dest:
                result.rename(dest)
            return str(dest) if dest.exists() else str(result)
        except Exception as e:
            print(f"  ⚠️ [경제 브리핑-네이버] 단락이미지 {idx} 실패: {e}")
            _g_report("writer", e, module=__name__)
            return ''

    def _split_2sent(text: str) -> list:
        raw = [p.strip() for p in str(text).split('\n\n') if p.strip()]
        if len(raw) >= 2:
            return raw
        import re as _re_s
        sents = _re_s.split(r'(?<=[.!?])\s+', str(text).strip())
        sents = [s.strip() for s in sents if s.strip()]
        if len(sents) <= 2:
            return [str(text)]
        return [' '.join(sents[i:i+2]) for i in range(0, len(sents), 2)]

    # PASS 1: 분리 대상 수집
    tasks = []
    block_paras: dict = {}
    for bi, (btype, bdata) in enumerate(blocks):
        if btype != 'text':
            continue
        paras = _split_2sent(bdata)
        if len(paras) < 2:
            continue
        block_paras[bi] = paras
        for pi in range(len(paras) - 1):
            tasks.append((bi, pi, paras[pi] + ' ' + paras[pi + 1]))

    if not tasks:
        return blocks

    print(f"  🤖 [경제 브리핑-네이버] 단락이미지 {len(tasks)}개 병렬 생성 중...")

    # PASS 2: 병렬 생성
    img_results: dict = {}
    def _gen(t):
        bi, pi, ctx = t
        path = _gen_para_img(ctx, bi * 100 + pi)
        return (bi, pi), path

    with ThreadPoolExecutor(max_workers=1) as ex:  # 순차 생성
        futs = {ex.submit(_gen, t): t for t in tasks}
        for fut in as_completed(futs):
            try:
                key, path = fut.result()
                if path:
                    img_results[key] = path
            except Exception as e:
                print(f"  ⚠️ 단락이미지 결과 오류: {e}")
                _g_report("writer", e, module=__name__)

    # PASS 3: blocks 재조립
    result = []
    for bi, (btype, bdata) in enumerate(blocks):
        if bi not in block_paras:
            result.append((btype, bdata))
            continue
        paras = block_paras[bi]
        for pi, para in enumerate(paras):
            result.append(('text', para))
            if pi < len(paras) - 1:
                img_path = img_results.get((bi, pi), '')
                if img_path and Path(img_path).exists():
                    result.append(('image', img_path))
    return result


def _fix_trailing_section_images(blocks: list) -> list:
    """섹션 마지막 텍스트 뒤에 붙은 filler 이미지를 텍스트 중간으로 이동.

    감지 패턴: [text] [non-h2-image] [h2-heading-image]
    수정 후  : [text-상반부] [non-h2-image] [text-하반부] [h2-heading-image]

    _eco_inject_para_images 이후 실행 — 섹션 경계 이미지 배치 최종 정리.
    """
    import re as _re_ts

    def _is_h2_img(btype: str, bdata: str) -> bool:
        s = str(bdata)
        return btype == 'image' and (
            'economic_h2_' in s or 'heading_' in s or 'section_title' in s
        )

    result = list(blocks)
    i = 0
    while i < len(result) - 2:
        bt0, bd0 = result[i]
        bt1, bd1 = result[i + 1]
        bt2, bd2 = result[i + 2]

        # Case B: [text1] [text2] [non-h2-image] [h2-heading-image]
        # → [text1] [non-h2-image] [text2] [h2-heading-image]
        if (i + 3 < len(result)
                and bt0 == 'text'
                and bt1 == 'text'
                and bt2 == 'image' and not _is_h2_img(bt2, bd2)
                and _is_h2_img(*result[i + 3])):
            # text2 와 filler-img 위치 교환
            result[i + 1], result[i + 2] = result[i + 2], result[i + 1]
            i += 3  # text1·filler·text2 건너뜀

        # Case A: [text] [non-h2-image] [h2-heading-image]
        elif (bt0 == 'text'
                and bt1 == 'image' and not _is_h2_img(bt1, bd1)
                and _is_h2_img(bt2, bd2)):
            text = str(bd0).strip()
            # 문장 분리 (마침표·요·다·요 뒤 공백 기준)
            sents = _re_ts.split(r'(?<=[.!?요다])\s+', text)
            sents = [s.strip() for s in sents if s.strip()]

            if len(sents) >= 2:
                mid = max(1, len(sents) // 2)
                part1 = ' '.join(sents[:mid])
                part2 = ' '.join(sents[mid:])
                # [text-상반부] [filler-img] [text-하반부] [h2-img]
                result[i]     = ('text', part1)
                # result[i+1] = filler-img (유지)
                result.insert(i + 2, ('text', part2))
                i += 3  # 상반부·filler·하반부 건너뜀
            else:
                # 1문장뿐 → filler를 텍스트 앞으로 이동 (image+image 방지)
                filler = result.pop(i + 1)
                result.insert(i, filler)
                i += 2  # filler·text 건너뜀
        else:
            i += 1

    return result


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

    tg(f"📰 경제 브리핑 포스팅 시작 ({TODAY_STR})\n"
       f"2개 플랫폼 각기 다른 트렌드 주제로 발행")

    # ── JARVIS09: 경제 뉴스 수집 (단일 진입점 — 2026-05-31 이관) ──────
    _j09_news_context = ""
    _j09_collection_docs: list = []
    try:
        from JARVIS09_COLLECTOR.collector_engine import collect_for_theme as _j09_collect
        _j09_results = _j09_collect("오늘의 경제 시장 뉴스", "금융")
        if _j09_results:
            _j09_collection_docs = _j09_results  # ★ chart_generator에 전달할 원본 유지
            _j09_news_context = "\n\n[JARVIS09 수집 뉴스 컨텍스트]\n" + "\n---\n".join(
                f"제목: {r.title}\n{r.cleaned_text[:300]}" for r in _j09_results[:5]
            )
            tg(f"🕸️ JARVIS09 뉴스 수집: {len(_j09_results)}건 → 기사 컨텍스트 반영")
            print(f"  🕸️ JARVIS09 수집 완료: {len(_j09_results)}건")
        else:
            print("  🕸️ JARVIS09 수집: 0건 (컨텍스트 없이 진행)")
    except Exception as _j09_e:
        print(f"  ⚠️ JARVIS09 수집 스킵: {_j09_e}")

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

    # ── 1. 트렌드글 생성·검증·발행 (harness 5-Layer — ADR 009 v2) ───────────
    # Layer 1: precondition (트렌드 데이터·로그인 확인)
    # Layer 2: ① 규정 로드 → ② 티스토리 대본 → ③ 네이버 대본
    # Layer 3: 2 플랫폼 대본 전체 검증 (문제 0까지 재작성 순환, max_attempts=3)
    # Layer 4: 검증 통과 시에만 발행 (티스토리/네이버 순차)
    print("\n📤 [1/4] 경제 브리핑 생성·검증·발행 (harness 5-Layer)...")

    from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue
    from JARVIS02_WRITER.trend_economic_writer import (
        ts_generate_draft, nv_generate_draft,
        ts_publish, nv_publish,
    )

    # ── Layer 1: precondition (harness 내장 — scheduler 수동 체크 대체) ──────
    def _precondition(state):
        """환경·쿠키·모듈 검증 — 실패 시 harness가 즉시 발행 차단 + GUARDIAN 박제."""
        pc_issues = []
        for _k in ("NV_USERNAME", "NV_PASSWORD",
                   "TS_URL", "TS_USERNAME", "TS_PASSWORD", "TS_COOKIE",
                   "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
            if not os.environ.get(_k, "").strip():
                pc_issues.append(Issue(step="① 전제조건", kind="env_missing",
                                       detail=f"환경변수 {_k} 누락"))
        try:
            import importlib as _il
            _il.import_module("JARVIS02_WRITER.collect_theme")
        except Exception as _e:
            pc_issues.append(Issue(step="① 전제조건", kind="import_error",
                                   detail=f"collect_theme import 실패: {type(_e).__name__}: {str(_e)[:80]}"))
        _nv_cookie = BASE_DIR / "naver_cookies.pkl"
        if not _nv_cookie.exists():
            pc_issues.append(Issue(step="① 전제조건", kind="cookie_missing",
                                   detail=f"네이버 쿠키 파일 누락: {_nv_cookie.name}"))
        return pc_issues

    @action_step(name="① 규정 로드")
    def _step_load_rules(state):
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        sb = _law_blk()
        print("  📜 [① 규정 로드] BLOG_SUPREME_LAW.md 숙지 완료")
        return {"supreme_block": sb}

    @action_step(name="② 티스토리 대본 생성")
    def _step_ts_draft(state):
        if not state.get("post_tistory"):
            print("  ─ [②] 티스토리 건너뜀")
            return {"ts_draft": {"success": False, "keyword": ""}}
        try:
            draft = ts_generate_draft(
                supreme_block=state.get("supreme_block"),
                collection_docs=state.get("collection_docs"),
            )
        except Exception as _e:
            print(f"  ❌ [②] 티스토리 대본 생성 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            draft = {"success": False, "keyword": ""}
        return {"ts_draft": draft}

    @action_step(name="③ 네이버 대본 생성")
    def _step_nv_draft(state):
        if not state.get("post_naver"):
            print("  ─ [③] 네이버 건너뜀")
            return {"nv_draft": {"success": False, "keyword": ""}}
        _ts_kw = state.get("ts_draft", {}).get("keyword", "")
        try:
            draft = nv_generate_draft(
                ts_keyword=_ts_kw,
                supreme_block=state.get("supreme_block"),
                collection_docs=state.get("collection_docs"),
            )
        except Exception as _e:
            print(f"  ❌ [③] 네이버 대본 생성 오류: {_e}")
            _g_report("writer", _e, module=__name__)
            draft = {"success": False, "keyword": ""}
        return {"nv_draft": draft}

    def _verify_all(state):
        """Layer 3 — 2 플랫폼 대본 전체 검증. list[Issue] 반환 (빈 리스트 = 통과).

        ★ 순수 검증만 — 즉시 수정·fingerprint·abort는 harness fix 훅(_fix_drafts)이 담당.
        검증 범위 (사용자 박제 2026-05-17):
          - [L1] 로그인 세션 유효성 (만료 시 자동 갱신 시도 → 실패 시 Issue)
          - [L3] 2 플랫폼 각각 규정 준수: 분량·키워드·이미지·헤더 등
        """
        issues = []

        # ── [L1] 로그인 세션 검증 (대본 생성 중 만료 대응) ─────────────────
        try:
            from JARVIS08_PUBLISH.credentials.login_manager import (
                auto_refresh_if_needed as _auto_refresh,
                verify_all_logins as _verify_logins,
            )
            _auto_refresh()           # 만료 임박 쿠키 선제 갱신
            if not _verify_logins():  # 갱신 후에도 실패 → 진짜 로그인 오류
                issues.append(Issue(
                    step="① 전제조건",
                    kind="login_invalid",
                    detail="로그인 세션 만료 — 재로그인 필요 (auto_refresh 후에도 실패)",
                ))
        except Exception as _le:
            issues.append(Issue(
                step="① 전제조건",
                kind="login_error",
                detail=f"로그인 확인 오류: {_le}",
            ))

        # ── [L3] 3 플랫폼 대본 규정 준수 검증 (순수 "발견"만) ────────────────
        # ★ 수정·fingerprint·abort는 harness fix 훅(_fix_drafts)이 자동 담당
        # ★ P2-⑦ 패치 (사용자 박제 2026-05-18): flag 변조 방어 — 활성 플랫폼이 *최소 1개* 보장 +
        #   비활성 플랫폼에 draft.success=True 가 남아 있으면 *flag 변조 검출* 로 차단.
        _flag_map = {
            "ts_draft":  ("post_tistory",  "② 티스토리 대본 생성", "tistory"),
            "nv_draft":  ("post_naver",    "③ 네이버 대본 생성",   "naver"),
        }

        # P2-⑦ — 활성 플랫폼 0개: 재생성으로 해결 불가 → 즉시 abort
        if not any(state.get(flag) for flag, _, _ in _flag_map.values()):
            issues.append(Issue(
                step="① 전제조건", kind="abort",
                detail="활성 플랫폼 0개 — post_tistory/post_naver 모두 False (발행 불가, 즉시 차단)",
            ))

        for key, (flag, step_name, platform) in _flag_map.items():
            draft = state.get(key) or {}
            if not state.get(flag):
                # 비활성 플랫폼: draft 가 success=True 면 flag 변조 의심 → 즉시 검출
                if draft.get("success"):
                    issues.append(Issue(
                        step=step_name, kind="abort",
                        detail=f"{platform} 비활성인데 draft.success=True — flag 변조 의심 (즉시 차단)",
                    ))
                continue  # 검증 skip
            if not draft.get("success"):
                issues.append(Issue(
                    step=step_name, kind="draft_failed",
                    detail=f"대본 생성 실패: {draft.get('error', 'unknown')}",
                ))
                continue
            di_list = _layer3_verify_draft(draft, platform)
            for di in di_list:
                issues.append(Issue(step=step_name, kind="draft_quality", detail=di))
            # ★ 발행 전 품질 게이트 (2026-06-28) — 구조 검증 통과 시에만 (LLM 비용 절약).
            #   사실성(차단)·매력도(재생성). kind 가 draft_quality 아니므로 _fix_drafts 가
            #   곧장 unfixed → 해당 WRITER step 재실행 = 재작성 순환.
            if not di_list:
                from JARVIS02_WRITER.prepublish_gate import prepublish_quality_issues
                _pt = (draft.get("post_type") or "economic").strip().lower()
                for q in prepublish_quality_issues(
                        draft, post_type=_pt,
                        source_docs=state.get("collection_docs"),
                        market_data=state.get("market_data")):
                    issues.append(Issue(step=step_name, kind=q["kind"], detail=q["detail"]))

        return issues

    def _fix_drafts(state: dict, issues: list) -> tuple:
        """harness fix 훅 — Layer 3 draft_quality 이슈 즉시 패치 + GUARDIAN 학습.

        ★ 전체 에이전트 디폴트 — harness.run_action이 verify 후 자동 호출.
        흐름: 이슈 발견 → inline 패치(state 직접 수정) → GUARDIAN 2단 박제
              (report_manual_fix + record_pattern_hit) → (fixed, unfixed) 반환.
        harness가 fixed→학습, unfixed→fingerprint, 전체→재생성 자동 처리.
        login_invalid / draft_failed 등 패치 불가 항목은 unfixed 그대로 반환.
        """
        from JARVIS02_WRITER.draft_fixer import fix_and_learn as _fx

        _key_map = {
            "② 티스토리 대본 생성": ("ts_draft",  "tistory"),
            "③ 네이버 대본 생성":   ("nv_draft",  "naver"),
        }

        # draft_quality 이슈를 step별로 묶기; 나머지(login 등)는 즉시 unfixed
        by_step: dict = {}
        non_draft: list = []
        for iss in issues:
            if iss.kind == "draft_quality" and iss.step in _key_map:
                by_step.setdefault(iss.step, []).append(iss.detail)
            else:
                non_draft.append(iss)

        fixed_all: list = []
        unfixed_all: list = list(non_draft)

        for step_name, raw_strs in by_step.items():
            draft_key, platform = _key_map[step_name]
            fixed_strs, unfixed_strs = _fx(state, draft_key, platform, raw_strs, "economic")
            for s in fixed_strs:
                fixed_all.append(Issue(step=step_name, kind="draft_fixed", detail=s))
            for s in unfixed_strs:
                unfixed_all.append(Issue(step=step_name, kind="draft_invalid", detail=s))

        return fixed_all, unfixed_all

    def _send_all(state):
        """Layer 4 — 검증 통과 후 2 플랫폼 발행.

        ★ ADR 009 v2 strict 모드: 활성 플랫폼 *하나라도* 실패 시 raise → harness 재진입.
        published_platforms 집합으로 이미 *진짜 성공한* 플랫폼은 재시도 시 스킵 (이중 발행 방지).

        ★ 사용자 박제 2026-06-07 (ERRORS [265]) — 부분 실패 자율 회복:
          기존: `__send_attempted__` True 면 *진짜 실패였더라도* 다음 attempt 에서
                skip → `published.add` *가짜 성공*. 사용자 알림만 가고 글은 없음.
          신규: __send_attempt__ 카운터 + attempt>=2 + 이전 실패 (`*_ok=False`)
                플래그 해제 → 다음 attempt 에서 *진짜 재발행*. harness max_attempts=3 와
                함께 최대 3회 발행 시도. ts/nv_publish 가 정확히 success/false 반환하면
                재시도는 진짜 새 글로 이어지지 않음 (실패 = 발행 안 됨).
        """
        # ★ 이미 발행된 플랫폼 추적 (retry 시 이중 발행 방지)
        published = state.setdefault("published_platforms", set())

        # ★ attempt 추적 — 첫 호출 = 1, 재진입마다 +1
        send_attempt = state.get("__send_attempt__", 0) + 1
        state["__send_attempt__"] = send_attempt

        # ★ attempt >= 2 + 이전 실패 플랫폼 *플래그 해제* → 진짜 재발행 기회 (ERRORS [265])
        if send_attempt >= 2:
            if (state.get("post_tistory") and "tistory" not in published
                    and state.get("__ts_send_attempted__") and not state.get("tistory_ok")):
                print(f"  🔄 [티스토리] attempt={send_attempt} — 이전 발행 실패 → 플래그 해제·재발행 시도")
                state["__ts_send_attempted__"] = False
            if (state.get("post_naver") and "naver" not in published
                    and state.get("__nv_send_attempted__") and not state.get("naver_ok")):
                print(f"  🔄 [네이버] attempt={send_attempt} — 이전 발행 실패 → 플래그 해제·재발행 시도")
                state["__nv_send_attempted__"] = False

        # 티스토리
        if state.get("post_tistory") and "tistory" not in published:
            if state.get("__ts_send_attempted__"):
                # ★ 이미 시도+성공 케이스 (success=True 잔존) — 이중 발행 방지로 published 처리
                print("  ⚠️ 티스토리 발행 이미 시도 완료 (이중 방지)")
                published.add("tistory")
            elif state.get("ts_draft", {}).get("success"):
                state["__ts_send_attempted__"] = True  # 반드시 시도 *전* 에 설정
                _ts_r = ts_publish(state["ts_draft"])
                state["ts_pub_result"] = _ts_r
                state["tistory_ok"] = _ts_r.get("success", False)
                if state["tistory_ok"]:
                    published.add("tistory")
                print(f"  {'✅' if state['tistory_ok'] else '⚠️'} 티스토리 {'완료' if state['tistory_ok'] else '미발행'}")
        elif "tistory" in published:
            print("  ⏭ 티스토리 이미 발행 완료 (재시도 스킵)")
        else:
            state.setdefault("tistory_ok", False)
            state.setdefault("ts_pub_result", {"success": False, "url": ""})
            if not state.get("post_tistory"):
                print("  ─ 티스토리 건너뜀")

        # 네이버 (ts_keyword는 이전 발행 결과 또는 현재 발행 결과에서 추출)
        _ts_kw = state.get("ts_pub_result", {}).get("keyword", "")
        if state.get("post_naver") and "naver" not in published:
            if state.get("__nv_send_attempted__"):
                # ★ 이미 시도+성공 케이스 (success=True 잔존) — 이중 발행 방지로 published 처리
                print("  ⚠️ 네이버 발행 이미 시도 완료 (이중 방지)")
                published.add("naver")
            elif state.get("nv_draft", {}).get("success"):
                state["__nv_send_attempted__"] = True  # 반드시 시도 *전* 에 설정
                _nv_r = nv_publish(state["nv_draft"], ts_keyword=_ts_kw)
                state["nv_pub_result"] = _nv_r
                state["naver_ok"] = _nv_r.get("success", False)
                if state["naver_ok"]:
                    published.add("naver")
                print(f"  {'✅' if state['naver_ok'] else '⚠️'} 네이버 {'완료' if state['naver_ok'] else '미발행'}")
        elif "naver" in published:
            print("  ⏭ 네이버 이미 발행 완료 (재시도 스킵)")
        else:
            state.setdefault("naver_ok", False)
            state.setdefault("nv_pub_result", {"success": False, "url": ""})
            if not state.get("post_naver"):
                print("  ─ 네이버 건너뜀")

        # ★ ADR 009 v2 strict: 활성 플랫폼 *하나라도* 미발행 시 raise → 검증 순환 재진입
        required = {p for p, flag in [("tistory", "post_tistory"), ("naver", "post_naver")]
                    if state.get(flag)}
        missing = required - published
        if missing:
            raise RuntimeError(
                f"[Layer4] {sorted(missing)} 발행 실패 (attempt={send_attempt}) — 송출 미완료 → 검증 순환 재진입"
            )

    _econ_action = ActionDefinition(
        name="경제 브리핑 발행",
        precondition=_precondition,          # ★ Layer 1 harness 내장 (scheduler 수동 체크 대체)
        steps=[_step_load_rules, _step_ts_draft, _step_nv_draft],
        verify=_verify_all,
        fix=_fix_drafts,
        send=_send_all,
        max_attempts=3,
    )
    _action_result = run_action(
        _econ_action,
        input_data={"post_naver": post_naver, "post_tistory": post_tistory,
                    "collection_docs": _j09_collection_docs,
                    "market_data": _j09_market_data},
    )
    _st = _action_result.state

    # harness 결과 추출 — 이하 기존 tg 요약·품질 분석 코드가 그대로 사용
    naver_ok   = bool(_st.get("naver_ok"))
    tistory_ok = bool(_st.get("tistory_ok"))
    ts_keyword = _st.get("ts_draft", {}).get("keyword", "")

    if not _action_result.delivered:
        print(f"\n  🚫 harness max_attempts 도달 — 발행 차단 (attempts={_action_result.attempts})")
        tg(f"🚫 경제 브리핑 harness max_attempts 도달 — 발행 차단\nattempts={_action_result.attempts}")

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
        # 네이버·티스토리 발행 URL — harness state 에서 직접 읽음 (ADR 009 v2)
        _naver_pub_url   = _st.get("nv_pub_result", {}).get("url", "")
        _tistory_pub_url = _st.get("ts_pub_result", {}).get("url", "")

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
            if not (_action_result.delivered if "_action_result" in dir() else True):
                _escalation_reason = getattr(_action_result, "escalation_reason", "")
                for _hist in (getattr(_action_result, "issues_history", None) or []):
                    for _iss in _hist:
                        _harness_issues.append(
                            f"{getattr(_iss,'step','?')}: {getattr(_iss,'kind','?')}: "
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
            run(post_naver=post_naver, post_tistory=post_tistory)
        except Exception as _e:
            _g_report("writer", _e, module=__name__, func_name="run")
            raise
        finally:
            LOCK_FILE.unlink(missing_ok=True)
    else:
        run(post_naver=post_naver, post_tistory=post_tistory)
