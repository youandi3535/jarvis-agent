"""
JARVIS02_WRITER / trend_economic_writer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
티스토리·네이버 아침 경제 관련 글 — 트렌드 기반 독립 발행 모듈

역할 (★ 2026-07-23 — 주제선정·수집 코드 전량 삭제 후):
  - *대본 생성·발행* 만 한다. 플랫폼별 독립 (공유 없음)
  - 주제는 JARVIS03 `topic_pack.pick_slot_candidate()` 가 준다 (02 는 고르지 않는다)
  - 데이터는 JARVIS09 `collect_all()` 이 준다 (02 는 수집하지 않는다)

이미지 디렉터리:
  JARVIS06_IMAGE/output/images/economic_tistory/  ← 티스토리 전용
  JARVIS06_IMAGE/output/images/economic_naver/    ← 네이버 전용

진입점 (harness step — economic_poster.run() 의 액션이 순서대로 호출):
  ts_collect → ts_generate_draft → ts_publish     (티스토리 액션)
  nv_collect → nv_generate_draft → nv_publish     (네이버 액션)
  ※ 레거시 직접발행 run_tistory/run_naver 는 **삭제됨** (2026-07-23)

포맷 (★ 분량은 length_manager / post_type_specs 위임 — 본 docstring 박제 X):
  티스토리 — 생활 밀착 Q&A형 (다음 검색 중심, 실용적)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from datetime import date, datetime

from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

load_dotenv()

_JARVIS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_JARVIS_ROOT))

BASE_DIR          = Path(__file__).parent
JARVIS06_BASE     = BASE_DIR.parent / "JARVIS06_IMAGE"             # 이미지 단일 진입점 (CLAUDE.md 규정)
TISTORY_IMG_DIR   = JARVIS06_BASE / 'output' / 'images' / 'economic_tistory'
NAVER_IMG_DIR     = JARVIS06_BASE / 'output' / 'images' / 'economic_naver'
TISTORY_IMG_DIR.mkdir(parents=True, exist_ok=True)
NAVER_IMG_DIR.mkdir(parents=True, exist_ok=True)

TODAY_STR  = date.today().strftime("%Y-%m-%d")
TODAY      = date.today()
TODAY_DOW  = ['월', '화', '수', '목', '금', '토', '일'][date.today().weekday()]
TODAY_PREFIX = f"[{TODAY.month}/{TODAY.day}]"

# ADR 008 Phase 2 — 카테고리 상수 단일 진입점 (JARVIS08_PUBLISH/category)
from JARVIS08_PUBLISH.category import ECONOMIC_CATEGORY  # noqa: F401

# length_manager 단일 진입점
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 같은 폴더 직접 실행 시


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1~2. 트렌드 로드·주제 선정 — ★ 이 파일에 없다 (사용자 박제 2026-07-23)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#   · 주제 선정 = JARVIS03 `topic_pack.pick_slot_candidate()` 단독 (키워드+프로필 동봉)
#   · 트렌드 데이터 = JARVIS03 소유. 02 는 읽지도 쓰지도 않는다.
#   · 수집       = JARVIS09 `collect_all()` 파사드 한 줄
#
#   삭제된 것 (816줄) — `load_today_trends` / `_build_emergency_trends` /
#   `select_{naver,tistory}_topic` / `_emergency_topic` / `_first_fit_topic` /
#   `_topic_econ_fit` / `_is_same_topic` / `_normalize_keyword` /
#   `_get_used_keywords` / `_mark_keyword_used` / `_USED_KW_FILE` /
#   레거시 직접발행 `run_tistory` · `run_naver` + 그 가드.
#
#   왜 지웠나: 호출자가 없어 *죽어 있었지만*, ① 03 의 데이터 폴더에 LLM 이 지어낸
#   트렌드를 써 넣고 ② 02 가 주제를 자체 선정하는, 규정이 금지한 코드가 그대로
#   살아 있었다. 파일이 남아 있으면 다음 작업자의 손이 거기로 간다 (ERRORS [489]).


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 텔레그램 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg, parse_mode="HTML")
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 썸네일 이미지 생성 (matplotlib)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _img_dir(platform: str):
    """플랫폼별 이미지 저장 디렉터리."""
    if platform == 'naver':
        return NAVER_IMG_DIR
    return TISTORY_IMG_DIR


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. 원고 생성 — 티스토리 생활 밀착 Q&A형
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# (죽은 코드 정리 2026-07-16 — _TS_SECTIONS 템플릿·_TS_Q1~Q4 상수 참조 0회 삭제.
#  현행 대본 구조는 draft_writer._gen_economic_ts_nv 의 출력 형식 블록이 단일 소스)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. 이미지 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cleanup_tistory_images() -> None:
    """티스토리 이미지 폴더 전체 초기화 — 패턴 무관 모든 파일 삭제."""
    for f in TISTORY_IMG_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink(missing_ok=True)
            except (PermissionError, OSError):
                pass

def _cleanup_naver_images() -> None:
    """Naver 이미지 폴더 전체 초기화 — 패턴 무관 모든 파일 삭제."""
    from JARVIS02_WRITER.economic_poster import ECONOMIC_IMG_DIR
    naver_dir = ECONOMIC_IMG_DIR if hasattr(ECONOMIC_IMG_DIR, 'glob') else Path(ECONOMIC_IMG_DIR)
    naver_dir.mkdir(parents=True, exist_ok=True)
    for f in naver_dir.iterdir():
        if f.is_file():
            try:
                f.unlink(missing_ok=True)
            except (PermissionError, OSError):
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  9. 내부 유틸
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_block(text: str, start_marker: str, end_marker: str | None) -> str:
    """LLM 응답에서 마커 사이 텍스트 추출."""
    idx = text.find(start_marker)
    if idx == -1:
        return ""
    start = idx + len(start_marker)
    if end_marker:
        end = text.find(end_marker, start)
        return text[start:end].strip() if end != -1 else text[start:].strip()
    return text[start:].strip()


def _enforce_paragraph_rule(html: str) -> str:
    """최대 2문장 단락 규칙 적용 (개선사항 #3).

    <p> 태그 내 3문장 이상이면 2문장 단위로 분리.
    문장 구분: 마침표+공백 또는 다/니다/습니다 패턴.
    """
    import re

    def split_sentences(text: str) -> list[str]:
        # 한국어 문장 끝: 다./요./니다./습니다./이다. 등 + 따옴표 포함
        pattern = r'(?<=[다요니]\.)\s+|(?<=다\.)\s+|(?<=요\.)\s+'
        parts = re.split(pattern, text.strip())
        # 공백만 남거나 빈 것 제거
        return [p.strip() for p in parts if p.strip()]

    def process_p(match):
        inner = match.group(1)
        # 이미 하위 태그(li, strong 등) 포함된 복잡한 p는 건드리지 않음
        if re.search(r'<[a-z]', inner):
            return match.group(0)
        sentences = split_sentences(inner)
        if len(sentences) <= 2:
            return match.group(0)
        # 2문장씩 묶어서 별도 <p>로 분리
        chunks = []
        for i in range(0, len(sentences), 2):
            chunk = ' '.join(sentences[i:i+2])
            chunks.append(f'<p>{chunk}</p>')
        return '\n'.join(chunks)

    return re.sub(r'<p>(.*?)</p>', process_p, html, flags=re.DOTALL)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  섹션별 콘텐츠 차트 생성 — 섹션 내용 유형에 맞는 실제 시각화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _detect_section_type(html: str) -> str:
    """섹션 HTML 내용 분석 → 시각화 유형 결정."""
    import re
    text = re.sub(r'<[^>]+>', '', html)
    if re.search(r'S&P|나스닥|다우|달러.?원|환율|WTI|금\s*현물|코스피|코스닥', text):
        return 'market'
    circ = len(re.findall(r'[①②③④⑤⑥⑦⑧⑨]', text))
    num  = len(re.findall(r'\n\s*\d+\.\s', text))
    if circ >= 2 or num >= 2:
        return 'checklist'
    if re.search(r'낙관|비관|중립|시나리오|[강약]세\s*시나리오', text):
        return 'scenario'
    nums = re.findall(r'\d+\.?\d*\s*%', text)
    if len(nums) >= 2 and re.search(r'영향|상승|하락|증가|감소', text):
        return 'impact'
    return 'highlight'


def _extract_list_items(html: str, max_items: int = 5) -> list:
    """HTML에서 리스트 항목 추출 (①②③ 또는 번호 목록)."""
    import re
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'<[^>]+>', '', text)
    items = []
    # ①②③ 패턴
    for m in re.finditer(r'[①②③④⑤⑥⑦⑧⑨]\s*([^\n①②③④⑤⑥⑦⑧⑨]{8,80})', text):
        items.append(m.group(1).strip()[:45])
    if not items:
        # 번호 목록 패턴
        for m in re.finditer(r'\d+\.\s+([^\n]{8,80})', text):
            items.append(m.group(1).strip()[:45])
    if not items:
        # 문장 분리 fallback
        sentences = [s.strip() for s in re.split(r'[.。]', text) if len(s.strip()) >= 10]
        items = sentences[:max_items]
    return items[:max_items]


def _extract_scenarios(html: str) -> list:
    """HTML에서 낙관/중립/비관 시나리오 추출."""
    import re
    text = re.sub(r'<[^>]+>', '', html)
    result = []
    patterns = [
        ('낙관', r'낙관[^。.]*[。.]?([^。.]{10,80})'),
        ('중립', r'중립[^。.]*[。.]?([^。.]{10,80})'),
        ('비관', r'비관[^。.]*[。.]?([^。.]{10,80})'),
    ]
    for label, pat in patterns:
        m = re.search(pat, text)
        if m:
            result.append((label, m.group(1).strip()[:50]))
        else:
            # 키워드만 찾아서 주변 문장 추출
            idx = text.find(label)
            if idx != -1:
                snippet = text[idx:idx+60].split('。')[0].split('.')[0]
                result.append((label, snippet.strip()[:50]))
            else:
                result.append((label, '추가 분석 필요'))
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  콘텐츠 차트 — JARVIS06_IMAGE draft_processor 위임
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _analyze_section_content(text_plain: str, keyword: str) -> dict:
    """섹션 텍스트를 읽고 최적 차트 유형 + 실제 데이터를 반환.

    위치 고정 없음 — 매일 달라지는 글 내용 기반으로 동적 결정.
    Returns: {'type': str, 'data': any, 'label': str}
    """
    import re

    # ── 1. 레이블 + 수치% 패턴 (가장 명확한 차트 데이터)
    labeled = re.findall(
        r'([가-힣A-Za-z·\/\-]{2,15})\s*[은는이가]?\s*(?:약\s*)?(\d+\.?\d*)\s*%',
        text_plain)
    if len(labeled) >= 2:
        factors = []
        for n, v in labeled[:5]:
            val = float(v)
            if val > 100:
                val = round(val / 100, 1)
            factors.append((n[:14], val))
        return {'type': 'impact', 'data': factors, 'label': '주요 지표 분석'}

    # ── 2. 원문자(①②③) / 번호 목록
    circle = re.findall(r'[①②③④⑤⑥⑦⑧⑨⑩]\s*([^\n①②③④⑤⑥⑦⑧⑨⑩]{5,50})', text_plain)
    num_list = re.findall(r'(?:^|\n)\s*\d+[\.)\s]\s*(.{5,50})', text_plain)
    items = circle or num_list
    if len(items) >= 3:
        return {'type': 'checklist', 'data': [i.strip() for i in items[:6]],
                'label': '핵심 포인트'}

    # ── 3. 시나리오 구조
    if re.search(r'낙관|비관|중립|시나리오|[강약]세', text_plain):
        return {'type': 'scenario', 'data': None, 'label': '시나리오 분석'}

    # ── 4. 레이블 없는 수치% (레이블은 앞 문맥에서 추출 시도)
    context = re.findall(
        r'([가-힣]{2,8})\s+(?:\w+\s*){0,3}?(\d+\.?\d*)\s*%', text_plain)
    raw_pcts = re.findall(r'(\d+\.?\d*)\s*%', text_plain)
    if len(raw_pcts) >= 2:
        if context and len(context) >= 2:
            factors = [(n[:12], float(v) if float(v) <= 100 else round(float(v)/10, 1))
                       for n, v in context[:5]]
        else:
            factors = [(f'지표 {i+1}', float(p)) for i, p in enumerate(raw_pcts[:5])]
        return {'type': 'impact', 'data': factors, 'label': '수치 분석'}

    # ── 5. 레이블 + 정수/소수 (단위 없는 수치)
    labeled_nums = re.findall(
        r'([가-힣]{2,8})\s*[은는이가]?\s*(\d{1,6}(?:,\d{3})*(?:\.\d+)?)\s*'
        r'(?:만|억|조|개|명|달러|원|위안|%)?',
        text_plain)
    if len(labeled_nums) >= 2:
        factors = []
        for n, v in labeled_nums[:5]:
            raw = float(v.replace(',', ''))
            if raw > 100_000:  raw = round(raw / 10_000, 1)
            elif raw > 10_000: raw = round(raw / 1_000, 1)
            elif raw > 1_000:  raw = round(raw / 100, 1)
            elif raw > 100:    raw = round(raw / 10, 1)
            factors.append((n[:12], raw))
        if len(factors) >= 2:
            return {'type': 'impact', 'data': factors, 'label': '주요 수치'}

    # ── 6. fallback → 핵심 문장 하이라이트 (숫자 포함 문장 우선)
    sentences = [s.strip() for s in re.split(r'[.。!?]', text_plain)
                 if len(s.strip()) >= 15]
    best = (next((s for s in sentences if re.search(r'\d', s)), None)
            or (sentences[0] if sentences else keyword))
    return {'type': 'highlight', 'data': best[:60], 'label': '핵심 인사이트'}


def _split_long_paragraphs(html: str) -> str:
    """각 <p> 안의 문장 중 2문장(약 100자) 이상인 것이 있으면 1문장씩 별도 <p>로 분리.

    분리된 <p>들은 _inject_paragraph_images PASS 1에서 자연스럽게
    이미지 삽입 대상이 됨 (마지막 <p> 제외).
    """
    import re

    def _split_p(match):
        inner = match.group(1).strip()
        if not inner:
            return match.group(0)
        # 문장 경계: 한국어 마침표·물음표·느낌표 + 공백 or 끝
        sents = re.split(r'(?<=[.。!?])\s+', inner)
        sents = [s.strip() for s in sents if s.strip()]
        if len(sents) <= 1:
            return match.group(0)  # 단일 문장 → 그대로
        # 2문장(약 100자) 이상인 문장이 하나라도 있을 때만 분리
        if any(len(s) >= 100 for s in sents):
            return '\n'.join(f'<p>{s}</p>' for s in sents)
        return match.group(0)

    return re.sub(r'<p>(.*?)</p>', _split_p, html, flags=re.DOTALL)


# 섹션 이미지 + 단락 이미지 경로 임시 저장 (generate → run 간 전달)
_section_img_paths: dict[int, str] = {}
_para_img_paths:    dict[int, str] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  분리 함수 — 대본 생성 + 발행 분리 (병렬화용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ★ _market_data_to_datasets 는 JARVIS09 로 이관 (사용자 박제 2026-07-23).
#   시장지표 → datasets 변환도 *수집 산출물의 형태 결정* 이므로 09 소관 —
#   `JARVIS09_COLLECTOR.market_data_to_datasets`. 02 는 호출조차 하지 않는다
#   (collect_all 이 차트 0개일 때 정책(market_fallback)에 따라 자동 사용).


def ts_collect(nv_keyword: str = '', supreme_block=None, market_data: dict | None = None,
               use_cache: bool = True) -> dict:
    """티스토리 주제선정 + JARVIS09 수집 + CollectedData 조립.

    Returns: success, keyword, sector, reason, collected (CollectedData),
             supreme_block (enriched), source_docs

    ★ use_cache: 09 로 그대로 전달만 — 재사용 여부는 09 판단 (nv_collect 참조).
    """
    from datetime import datetime as _dt_ts
    print(f"\n  🔴 [TISTORY-COLLECT] 주제 선정 + 수집 중... [{_dt_ts.now().strftime('%H:%M:%S')}]")

    keyword = ""
    try:
        # ★ 주제 선정은 자비스03 단독 (사용자 박제 2026-07-03) — 강제주제·소진복구 재빌드까지
        #   `pick_slot_candidate()` 한 곳. 02 는 "앞 슬롯이 뭘 가져갔는지" 만 알려준다.
        from JARVIS03_RADAR.topic_pack import pick_slot_candidate as _pick_slot
        _cand = _pick_slot(exclude_keyword=nv_keyword, force_env="JARVIS_FORCE")
        if _cand is None:
            return {"success": False, "keyword": "",
                    "error": "자비스03 주제 패키지 없음 (트렌드·적합 후보·LLM 확인)"}
        keyword = _cand.get('keyword', '')
        sector = _cand.get('sector', '')
        _profile = _cand.get('profile') or {}
        reason = _profile.get('summary') or _cand.get('reason', '')
        print(f"  📌 [티스토리 주제 — 자비스03 팩] [{sector}] {keyword}"
              + (f" — {reason[:60]}" if reason else ""))

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass
        # ★ 규정 숙지 (2026-07-16): 발행 전 게이트가 실제 채점하는 기준(분량·SEO·매력도 5축)
        #   을 Pass-1 프롬프트에 사전 고지 — supreme_block 합류로 모든 Pass-1 변형 자동 상속.
        try:
            from JARVIS02_WRITER.law_enforcer import build_gate_checklist_block as _gate_chk
            supreme_block = (supreme_block or "") + "\n" + _gate_chk("economic", "tistory")
        except Exception:
            pass
        _rel_terms = ", ".join(_profile.get('related_terms') or [])
        if reason:
            supreme_block = (supreme_block or "") + (
                f"\n\n[주제 프로필 — 자비스03]\n- 주제: {keyword} ({sector})\n- 정의: {reason}"
                + (f"\n- 관련어: {_rel_terms}" if _rel_terms else ""))

        # ★ 수집은 자비스09 단독 (사용자 박제 2026-07-23) — 02 는 "이 주제로 수집해줘" 한 줄.
        #   차트·리서치·fact 변환·종목재무 배제·시장지표 폴백은 전부 09 소관(테마와 동일 함수).
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e1")  # J03→J09 수집 요청 시작
        except Exception:
            pass
        print(f"  🕸️ [JARVIS09] '{keyword}' 수집 시작...")
        from JARVIS09_COLLECTOR import collect_all
        _bundle = collect_all(keyword, profile=_profile, sector=sector, category="economic",
                              angle=reason, synonyms=_cand.get("synonyms"),
                              plan_cache=_cand.get("data_plan"), market_data=market_data,
                              extra_meta={"section_plan": _cand.get("section_plan")},
                              use_cache=use_cache)
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e2")  # J09→J02 데이터 전달 완료
        except Exception:
            pass
        collected           = _bundle["collected"]
        _pool               = _bundle.get("datasets") or []
        _kw_collection_docs = _bundle.get("docs") or []
        _ev_pack            = _bundle.get("evidence_pack") or {}
        _corpus_digest      = _bundle.get("corpus_digest") or ""
        print(f"  🕸️ [JARVIS09] '{keyword}' 수집 완료: 문서 {len(_kw_collection_docs)}건, "
              f"데이터셋 {len(_pool)}개")

        # 데이터 카탈로그 주입
        try:
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 데이터 주입 스킵: {_de}")

        # 근거 브리프 주입
        try:
            from JARVIS09_COLLECTOR.evidence_pack import evidence_brief
            _brief = evidence_brief(_ev_pack)
            if _brief:
                supreme_block = (supreme_block or "") + "\n\n" + _brief
                print(f"  📚 [근거 브리프] fact {len(_ev_pack.get('facts', []))}개 "
                      f"→ 대본 프롬프트 직접 주입")
        except Exception as _ebe:
            print(f"  ⚠️ [근거 브리프] 주입 스킵: {_ebe}")

        # 수집 자료 주입 — 선계산 digest(요약) 우선, 없으면 원문 전문 (distill 압축 2026-07-19)
        try:
            _corpus = _corpus_digest
            if _corpus:
                supreme_block = (supreme_block or "") + "\n\n" + _corpus
                print(f"  📖 [수집 요약] digest ~{len(_corpus) // 1000}K자 주입 (원문 대비 압축 — writer 프롬프트 축소)")
            else:
                from JARVIS02_WRITER.draft_writer import build_corpus_block as _bcb
                _corpus = _bcb(_kw_collection_docs)
                if _corpus:
                    supreme_block = (supreme_block or "") + "\n\n" + _corpus
                    print(f"  📖 [수집 전문] 문서 {len(_kw_collection_docs)}건 "
                          f"→ 원문 주입 (~{len(_corpus) // 1000}K자, digest 미가용 폴백)")
        except Exception as _cbe:
            print(f"  ⚠️ [수집 전문] 주입 스킵: {_cbe}")

        # CollectedData 조립은 09(collect_all→compose_collected)가 이미 완료 — 여기선 조립 0.

        print(f"  ✅ [TISTORY-COLLECT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "sector": sector,
            "reason": reason,
            "collected": collected,
            "supreme_block": supreme_block,
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-COLLECT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


def ts_generate_draft(keyword: str, sector: str, reason: str,
                      collected, supreme_block=None,
                      gate_feedback: list | None = None,
                      source_docs: list | None = None) -> dict:
    """티스토리 Pass-1 대본 생성 + JARVIS06 이미지 파이프라인.

    ts_collect() 결과를 받아 대본 생성 단계만 담당.
    """
    from datetime import datetime as _dt_ts
    print(f"\n  🔴 [TISTORY-DRAFT] 대본 생성 중... [{_dt_ts.now().strftime('%H:%M:%S')}]")
    _section_img_paths.clear()
    _para_img_paths.clear()
    # ★ 재실행 시 이미지 삭제는 정당 — 하네스 VERIFY_ONLY 수정(2026-07-16) 이후
    #   이 스텝 재실행 = 진짜 재생성 필요 시점뿐 (인프라 실패는 재검증만 수행).
    _cleanup_tistory_images()

    # 대시보드 작동 신호 — 대본 작성 시작/종료 (finally 에서 해제)
    try:
        from shared.pipeline_activity import mark_busy as _mb_j02
        _mb_j02("j02", "티스토리 대본 작성", ttl=900)
    except Exception:
        pass

    try:
        from JARVIS02_WRITER.tistory_html_writer import generate_article_html, extract_text_content
        from JARVIS06_IMAGE.draft_processor import process_draft

        # Pass-1-only 대본(placeholder) → process_draft 단일 이미지 경로
        _ref_ds_ts = getattr(collected, "datasets", None) or []
        draft_html = generate_article_html(keyword, sector, reason, supreme_block,
                                           ref_datasets=_ref_ds_ts,
                                           section_plan=(getattr(collected, "meta", None) or {}).get("section_plan"),
                                           gate_feedback=gate_feedback, pass2=False)
        if not draft_html:
            # ★ 인프라 스로틀/절단(일시적)과 콘텐츠 결함을 구분해 태깅(rank4). circuit_is_open()은
            #   프로세스 전역(워커 스레드 안전), last_call_infra_incomplete()는 동일 스레드 직전 호출.
            #   둘 중 하나면 infra_throttle → harness 가 재작성 대신 defer/backoff.
            from shared.llm import (last_call_infra_incomplete as _infra, circuit_is_open as _copen,
                                     make_infra_error as _mk_infra)
            _err = _mk_infra() if (_infra() or _copen()) else "HTML 생성 실패"
            return {"success": False, "keyword": keyword, "error": _err}

        result = process_draft(draft_html, collected=collected, platform="tistory",
                               out_dir=TISTORY_IMG_DIR)
        html = result["html"]
        title = result["title"]
        content = extract_text_content(html)
        html_path = result.get("html_path", "")
        img_dir = str(TISTORY_IMG_DIR)
        visual_paths = []
        blocks = result["blocks"]  # J06 이 썸네일 prepend + 법률집행 완료

        print(f"  ✅ [TISTORY-DRAFT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "title": title,
            "html": html,
            "content": content,
            "html_path": html_path,
            "img_dir": img_dir,
            "blocks": blocks,
            "visual_paths": visual_paths,
            "source_docs": source_docs or [],
            "collected": collected,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-DRAFT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}
    finally:
        try:
            from shared.pipeline_activity import clear_busy as _cb_j02
            _cb_j02("j02")
        except Exception:
            pass


def ts_publish(draft: dict) -> dict:
    """티스토리 대본 발행 (⑧ 단계)."""
    if not draft.get('success'):
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}

    try:
        from JARVIS06_IMAGE.draft_processor import publish_assembled
        from JARVIS08_PUBLISH.platforms import post_to_tistory
        print(f"  📤 [TISTORY-PUB] J06→J08 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']
        html = draft['html']

        def _pub_fn(blocks, title, **_kw):
            return post_to_tistory(
                title=title,
                html_content=draft['content'],
                blocks=blocks,
                category=ECONOMIC_CATEGORY,
            )

        result = publish_assembled(draft, _pub_fn, "tistory")

        if result:
            # ★ DB 기록 (ERRORS [370]): 성공 발행 → on_post_published_detail 이 posts·post_analysis
            #   *둘 다* 기록 → 대시보드(오늘 발행 글)·Daily Review 자동 동기화. 하네스 경제 흐름은
            #   이 함수를 send 콜백으로 쓰는데 emit 이 누락돼 07-01 이후 발행이 기록 0 이었음.
            try:
                from shared.bus import on_post_published_detail as _emit
                from JARVIS08_PUBLISH.platforms import last_post_url as _last_url
                _imgs = [str(b[1]) for b in (blocks or []) if b and b[0] == "image"]
                _emit(theme=keyword, platform="tistory", title=draft['title'],
                      url=_last_url("tistory"),   # ★ ERRORS [482] — URL 누락 시 조회수 수집 불가
                      content=draft.get('content', ''), html=html,
                      source_keyword=keyword, post_type="economic", image_paths=_imgs)
                print(f"  ✅ [DB] post_analysis·posts 저장 완료 (이미지 {len(_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류(무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
            _tg(f"✅ [TISTORY-TREND] 발행 완료!\n제목: {draft['title']}\n키워드: {keyword}")
            print(f"  ✅ [TISTORY-PUB] 완료")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [TISTORY-TREND] 발행 실패")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-PUB] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}


def nv_collect(ts_keyword: str = '', supreme_block=None, market_data: dict | None = None,
               use_cache: bool = True) -> dict:
    """네이버 주제선정 + JARVIS09 수집 + CollectedData 조립.

    Returns: success, keyword, sector, reason, collected (CollectedData),
             supreme_block (enriched), source_docs

    ★ use_cache: 09 로 그대로 전달만 한다 — *재사용 여부 판단은 09 안* (`collect_all`).
      02 는 캐시를 열어보지 않는다 (사용자 박제 2026-07-23 — 수집 단일 진입점).
    """
    from datetime import datetime as _dt_nv
    print(f"\n  🟢 [NAVER-COLLECT] 주제 선정 + 수집 중... [{_dt_nv.now().strftime('%H:%M:%S')}]")

    keyword = ""
    try:
        # ★ 주제 선정은 자비스03 단독 (사용자 박제 2026-07-03) — ts_collect 와 동일 단일 진입점.
        from JARVIS03_RADAR.topic_pack import pick_slot_candidate as _pick_slot
        _cand = _pick_slot(exclude_keyword=ts_keyword, force_env="JARVIS_FORCE_NV")
        if _cand is None:
            return {"success": False, "keyword": "",
                    "error": "자비스03 주제 패키지 없음 (트렌드·적합 후보·LLM 확인)"}
        keyword = _cand.get('keyword', '')
        sector = _cand.get('sector', '')
        _profile = _cand.get('profile') or {}
        reason = _profile.get('summary') or _cand.get('reason', '')
        print(f"  📌 [네이버 주제 — 자비스03 팩] [{sector}] {keyword}"
              + (f" — {reason[:60]}" if reason else ""))

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass
        # ★ 규정 숙지 (2026-07-16): 발행 전 게이트가 실제 채점하는 기준(분량·SEO·매력도 5축)
        #   을 Pass-1 프롬프트에 사전 고지 — supreme_block 합류로 모든 Pass-1 변형 자동 상속.
        try:
            from JARVIS02_WRITER.law_enforcer import build_gate_checklist_block as _gate_chk
            supreme_block = (supreme_block or "") + "\n" + _gate_chk("economic", "naver")
        except Exception:
            pass
        _rel_terms = ", ".join(_profile.get('related_terms') or [])
        if reason:
            supreme_block = (supreme_block or "") + (
                f"\n\n[주제 프로필 — 자비스03]\n- 주제: {keyword} ({sector})\n- 정의: {reason}"
                + (f"\n- 관련어: {_rel_terms}" if _rel_terms else ""))

        # ★ 수집은 자비스09 단독 (사용자 박제 2026-07-23) — 02 는 "이 주제로 수집해줘" 한 줄.
        #   차트·리서치·fact 변환·종목재무 배제·시장지표 폴백은 전부 09 소관(테마와 동일 함수).
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e1")  # J03→J09 수집 요청 시작
        except Exception:
            pass
        print(f"  🕸️ [JARVIS09] '{keyword}' 수집 시작...")
        from JARVIS09_COLLECTOR import collect_all
        _bundle = collect_all(keyword, profile=_profile, sector=sector, category="economic",
                              angle=reason, synonyms=_cand.get("synonyms"),
                              plan_cache=_cand.get("data_plan"), market_data=market_data,
                              extra_meta={"section_plan": _cand.get("section_plan")},
                              use_cache=use_cache)
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e2")  # J09→J02 데이터 전달 완료
        except Exception:
            pass
        collected           = _bundle["collected"]
        _pool               = _bundle.get("datasets") or []
        _kw_collection_docs = _bundle.get("docs") or []
        _ev_pack            = _bundle.get("evidence_pack") or {}
        _corpus_digest      = _bundle.get("corpus_digest") or ""
        print(f"  🕸️ [JARVIS09] '{keyword}' 수집 완료: 문서 {len(_kw_collection_docs)}건, "
              f"데이터셋 {len(_pool)}개")

        # 데이터 카탈로그 주입
        try:
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 데이터 주입 스킵: {_de}")

        # 근거 브리프 주입
        try:
            from JARVIS09_COLLECTOR.evidence_pack import evidence_brief
            _brief = evidence_brief(_ev_pack)
            if _brief:
                supreme_block = (supreme_block or "") + "\n\n" + _brief
                print(f"  📚 [근거 브리프] fact {len(_ev_pack.get('facts', []))}개 "
                      f"→ 대본 프롬프트 직접 주입")
        except Exception as _ebe:
            print(f"  ⚠️ [근거 브리프] 주입 스킵: {_ebe}")

        # 수집 자료 주입 — 선계산 digest(요약) 우선, 없으면 원문 전문 (distill 압축 2026-07-19)
        try:
            _corpus = _corpus_digest
            if _corpus:
                supreme_block = (supreme_block or "") + "\n\n" + _corpus
                print(f"  📖 [수집 요약] digest ~{len(_corpus) // 1000}K자 주입 (원문 대비 압축 — writer 프롬프트 축소)")
            else:
                from JARVIS02_WRITER.draft_writer import build_corpus_block as _bcb
                _corpus = _bcb(_kw_collection_docs)
                if _corpus:
                    supreme_block = (supreme_block or "") + "\n\n" + _corpus
                    print(f"  📖 [수집 전문] 문서 {len(_kw_collection_docs)}건 "
                          f"→ 원문 주입 (~{len(_corpus) // 1000}K자, digest 미가용 폴백)")
        except Exception as _cbe:
            print(f"  ⚠️ [수집 전문] 주입 스킵: {_cbe}")

        # CollectedData 조립은 09(collect_all→compose_collected)가 이미 완료 — 여기선 조립 0.

        print(f"  ✅ [NAVER-COLLECT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "sector": sector,
            "reason": reason,
            "collected": collected,
            "supreme_block": supreme_block,
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-COLLECT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


# ★ 02 에는 선계산(precollect) 코드가 없다 (사용자 박제 2026-07-23).
#   "언제 미리 수집해 둘지" 는 수집 시점 판단 = 수집 도메인의 일 →
#   `JARVIS09_COLLECTOR/precollect.py:precollect_economic()` 단독.


def nv_generate_draft(keyword: str, sector: str, reason: str,
                      collected, supreme_block=None,
                      gate_feedback: list | None = None,
                      source_docs: list | None = None) -> dict:
    """네이버 Pass-1 대본 생성 + JARVIS06 이미지 파이프라인.

    nv_collect() 결과를 받아 대본 생성 단계만 담당.
    """
    from datetime import datetime as _dt_nv
    print(f"\n  🟢 [NAVER-DRAFT] 대본 생성 중... [{_dt_nv.now().strftime('%H:%M:%S')}]")
    _section_img_paths.clear()
    _para_img_paths.clear()
    # ★ 재실행 시 이미지 삭제는 정당 — 하네스 VERIFY_ONLY 수정(2026-07-16) 이후
    #   이 스텝 재실행 = 진짜 재생성 필요 시점뿐 (인프라 실패는 재검증만 수행).
    _cleanup_naver_images()

    # 대시보드 작동 신호 — 대본 작성 시작/종료 (finally 에서 해제)
    try:
        from shared.pipeline_activity import mark_busy as _mb_j02
        _mb_j02("j02", "네이버 대본 작성", ttl=900)
    except Exception:
        pass

    try:
        from JARVIS02_WRITER.tistory_html_writer import generate_article_html, extract_text_content
        from JARVIS06_IMAGE.draft_processor import process_draft

        # Pass-1-only 대본(placeholder) → process_draft 단일 이미지 경로
        _ref_ds = getattr(collected, "datasets", None) or []
        draft_html = generate_article_html(keyword, sector, reason, supreme_block, platform="naver",
                                           ref_datasets=_ref_ds,
                                           section_plan=(getattr(collected, "meta", None) or {}).get("section_plan"),
                                           gate_feedback=gate_feedback, pass2=False)
        if not draft_html:
            # ★ 인프라 스로틀/절단(일시적)과 콘텐츠 결함 구분 태깅(rank4) — 경제 네이버.
            from shared.llm import (last_call_infra_incomplete as _infra, circuit_is_open as _copen,
                                     make_infra_error as _mk_infra)
            _err = _mk_infra() if (_infra() or _copen()) else "HTML 생성 실패"
            return {"success": False, "keyword": keyword, "error": _err}

        result = process_draft(draft_html, collected=collected, platform="naver",
                               out_dir=NAVER_IMG_DIR)
        html = result["html"]
        title = result["title"]
        img_dir = str(NAVER_IMG_DIR)
        visual_paths = []
        blocks = result["blocks"]  # J06 이 썸네일 prepend + 법률집행 완료

        print(f"  ✅ [NAVER-DRAFT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "title": title,
            "content": extract_text_content(html),
            "html": html,
            "blocks": blocks,
            "visual_paths": visual_paths,
            "source_docs": source_docs or [],
            "collected": collected,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-DRAFT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}
    finally:
        try:
            from shared.pipeline_activity import clear_busy as _cb_j02
            _cb_j02("j02")
        except Exception:
            pass


def nv_publish(draft: dict, ts_keyword: str = '') -> dict:
    """네이버 대본 발행 (⑧ 단계)."""
    if not draft.get('success'):
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}

    try:
        from JARVIS06_IMAGE.draft_processor import publish_assembled
        from JARVIS08_PUBLISH.platforms import post_to_naver
        print(f"  📤 [NAVER-PUB] J06→J08 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']

        def _pub_fn(blocks, title, **_kw):
            return post_to_naver(
                title=title,
                html_content=draft['content'],
                blocks=blocks,
                category=ECONOMIC_CATEGORY,
            )

        result = publish_assembled(draft, _pub_fn, "naver")

        if result:
            # ★ DB 기록 (ERRORS [370]): 성공 발행 → posts·post_analysis 둘 다 기록 → 대시보드 동기화
            try:
                from shared.bus import on_post_published_detail as _emit
                from JARVIS08_PUBLISH.platforms import last_post_url as _last_url
                _imgs = [str(b[1]) for b in (blocks or []) if b and b[0] == "image"]
                _emit(theme=keyword, platform="naver", title=draft['title'],
                      url=_last_url("naver"),   # ★ ERRORS [482] — URL 누락 시 조회수 수집 불가
                      content=draft.get('content', ''), html=draft.get('html', ''),
                      source_keyword=keyword, post_type="economic", image_paths=_imgs)
                print(f"  ✅ [DB] post_analysis·posts 저장 완료 (이미지 {len(_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류(무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
            _tg(f"✅ [NAVER-TREND] 발행 완료!\n제목: {draft['title']}\n키워드: {keyword}")
            print(f"  ✅ [NAVER-PUB] 완료")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [NAVER-TREND] 발행 실패")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-PUB] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}
