"""JARVIS02_WRITER/post_scorer.py — 블로그 글 100점 루브릭 채점기 (사용자 박제 2026-07-16).

4개 조합 (플랫폼 × 글 종류) 모두 100점 만점:
  Section A (LLM 콘텐츠 품질, 20점) — judge_engagement 5축 LLM 점수 매핑
  Section B (헌법 공통, 50점)        — 결정론적 HTML 분석
  Section C-N/C-T (SEO, 20점)        — 플랫폼별 SEO 기준
  Section D-TH/D-EC (글 종류, 10점)  — 테마주/경제브리핑 전용

70점 이상만 발행. 미달 시 prepublish_gate 가 "engagement" 이슈로 재작성 순환.
채점 원칙: 모든 항목 0~만점 연속 (이진 pass/fail 금지).

킬스위치: PREPUBLISH_SCORE_GATE=0 → 채점 게이트 비활성화.
"""
from __future__ import annotations
import re
import os
import logging
import logging as _logging
from functools import lru_cache as _lru_cache
from typing import Any

log = logging.getLogger(__name__)

PASS_THRESHOLD: float = 70.0


# ═══════════════════════════════════════════════════════
# ★ 배점·목표 단일 진실 소스 (사용자 박제 2026-07-21)
#
#   종전엔 한 항목의 만점이 ① items dict 의 "max" ② 채점 함수 내부 상수
#   두 곳에 따로 적혀 있었다 → 한쪽만 고치면 "채점은 6점 만점인데 표시는 5점"
#   으로 조용히 어긋난다. 목표 개수도 ① 채점 함수 ② 작성 프롬프트 두 곳에 있었다.
#   (실제 사고: EC2 지시"4회"↔채점"5회", T8 지시"1개 이상"↔채점"정확히 1개")
#   → 배점은 RUBRIC_MAX, 목표 구간은 RUBRIC_COUNT 한 곳에만 적고 전부 파생한다.
# ═══════════════════════════════════════════════════════

try:
    from JARVIS02_WRITER.length_manager import (
        MIN_IMAGES as _MIN_IMAGES,
        NAVER_HASHTAG_MIN as _HT_MIN,
        NAVER_HASHTAG_MAX as _HT_MAX,
        TARGET_KOREAN as _TARGET_KOREAN,
    )
except Exception:  # 단독 실행·import 실패 시 헌법 기본값
    _MIN_IMAGES, _HT_MIN, _HT_MAX, _TARGET_KOREAN = 5, 5, 10, 1600

_NOCAP = 10 ** 6   # 상한 없음 — 많을수록 좋은 항목 (초과 감점 안 함)


def _std(platform: str, key: str, default):
    """플랫폼 SEO 기준 조회 — seo_standards 단일 진입점에서 파생 (하드코딩 금지)."""
    try:
        from JARVIS02_WRITER.seo_standards import PLATFORM_STANDARDS
        v = PLATFORM_STANDARDS.get(platform, {}).get(key)
        return v if v else default
    except Exception:
        return default


# ── 항목별 만점 (섹션 합계: A20 · B50 · C-N20 · C-T20 · D-TH10 · D-EC10) ──
#    ★ 프로세스 자동만점 5종(B3·B4·B20·B23·T9)은 관측 불가라 항상 만점이므로
#      각 1점으로 축소하고 회수한 4점을 편차가 실재하는 항목에 재분배
#      (사용자 지시 2026-07-21). B5·B21 은 실측 위반율 0 이라 대상에서 제외 —
#      거기 올리면 회수한 무상 점수를 이름만 바꿔 되돌려주는 셈이라 총점이 안 변한다.
RUBRIC_MAX: dict[str, float] = {
    # ── Section A — LLM 콘텐츠 품질 (20)
    "A1_engagement": 7, "A2_usefulness": 5, "A3_originality": 4,
    "A4_structure": 3,  "A5_title_hook": 1,
    # ── Section B — 헌법 공통 (50)
    "B1_intro": 6,          # ★ 5→6 (헌법 제1조 — AI 자동생성형 회피를 배점화)
    "B2_paragraphs": 3,
    "B3_differentiate": 1,  # ★ 2→1 (프로세스 자동만점 축소)
    "B4_dynamic": 1,        # ★ 2→1 (프로세스 자동만점 축소)
    "B5_factuality": 5,
    "B6_incomplete": 1,  "B7_empty_hdr": 2,  "B8_img_consec": 2,
    "B9_para_consec": 2, "B10_disclaimer": 3, "B11_tone": 2,
    "B12_forbidden": 1,  "B13_llm_dir": 1,   "B14_incomplete2": 1,
    "B15_img_pos": 1,
    "B16_img_count": 4,     # ★ 3→4 (헌법 제8조 이미지 5+α — 독자 체감 1순위)
    "B17_body_len": 4,      # ★ 3→4 (헌법 제8조 분량 — SEO 직결)
    "B18_spacing": 2,   "B19_chart": 2,
    "B20_visual_div": 1,    # ★ 2→1 (프로세스 자동만점 축소)
    "B21_consistency": 2, "B22_tags": 2,
    "B23_process": 1,       # 프로세스 자동만점 (이미 1점)
    # ── Section C-N — 네이버 SEO (20)
    "N1_title_len": 3, "N2_kw_in_title": 3, "N3_h3_count": 3, "N4_section_sents": 2,
    "N5_kw_density": 3, "N6_kw_in_body": 2, "N7_hashtags": 2, "N8_hayeo": 2,
    # ── Section C-T — 티스토리 SEO (20)
    "T1_title_len": 2, "T2_kw_in_title": 2, "T3_h1_count": 2, "T4_h2_count": 3,
    "T5_h3_range": 2,  "T6_longtail": 3,
    "T7_meta_desc": 3,      # ★ 2→3 (티스토리=Google SEO, 메타설명이 검색 스니펫 그 자체)
    "T8_internal_link": 2,
    "T9_no_dup": 1,         # ★ 2→1 (프로세스 자동만점 축소)
    # ── Section D-TH — 테마주 (10)
    "TH1_3m_return": 5, "TH2_no_fin_text": 5,
    # ── Section D-EC — 경제브리핑 (10)
    "EC1_real_data": 4, "EC2_causal": 4, "EC3_term_explain": 2,
}

# ── 개수 기반 항목의 목표 구간 (lo, hi) — 배점은 RUBRIC_MAX 에서 파생 ──
RUBRIC_COUNT: dict[str, tuple[int, int]] = {
    "B16_img_count":    (_MIN_IMAGES, _NOCAP),   # 헌법 "5+α" → 초과 무감점
    "N3_h3_count":      (3, 4),
    "N6_kw_in_body":    (3, 5),
    "N7_hashtags":      (_HT_MIN, _HT_MAX),
    "T3_h1_count":      (1, 1),
    "T4_h2_count":      (3, 5),
    "T6_longtail":      (3, 5),                  # 전 헤더 도배는 키워드 스터핑 → 상한 존재
    "T8_internal_link": (1, 3),                  # SEO 기준이 "1개 이상" 이므로 2~3개도 만점
    "EC2_causal":       (5, _NOCAP),
    "EC3_term_explain": (3, _NOCAP),
}

# ── 섹션 배점 합계 (드리프트 자동 검증용) ──
SECTION_TOTALS: dict[str, float] = {
    "A": 20.0, "B": 50.0, "C-N": 20.0, "C-T": 20.0, "D-TH": 10.0, "D-EC": 10.0,
}


def _section_of(key: str) -> str:
    if key.startswith("TH"): return "D-TH"
    if key.startswith("EC"): return "D-EC"
    if key.startswith("A"):  return "A"
    if key.startswith("B"):  return "B"
    if key.startswith("N"):  return "C-N"
    if key.startswith("T"):  return "C-T"
    return "?"



def _target_korean() -> int:
    """본문 분량 목표 — `length_manager` 단일 진입점에서 파생.

    ★ 이름표에 수치를 **복사**해 두면 기준이 바뀌는 날 이름만 옛 값을 주장한다.
      실측 2026-08-07: 이름은 `본문 분량 1500자+` 인데 채점은 1600 을 썼다 —
      1500자 글은 만점이 아닌데 이름은 만점이라고 말하고 있었다.
      그리고 이 **이름표가 이제 작성 프롬프트로 나간다**(약점 항목 주입) —
      낡은 이름은 작성자에게 거짓 목표를 준다.
    """
    try:
        from JARVIS02_WRITER.length_manager import TARGET_KOREAN
        return int(TARGET_KOREAN)
    except Exception:
        return 1600

def mx(key: str) -> float:
    """항목 만점 — 채점 함수·표시 양쪽의 *유일한* 배점 출처."""
    return float(RUBRIC_MAX[key])


def rubric_totals() -> dict[str, float]:
    """섹션별 실제 배점 합계 (선언값 SECTION_TOTALS 과 대조용)."""
    out: dict[str, float] = {k: 0.0 for k in SECTION_TOTALS}
    for k, v in RUBRIC_MAX.items():
        out[_section_of(k)] = out.get(_section_of(k), 0.0) + float(v)
    return out


def rubric_drift() -> list[str]:
    """배점 합계가 선언과 어긋나면 사유 목록 반환 (정상이면 빈 리스트)."""
    got = rubric_totals()
    return [f"{s} 배점합 {got.get(s, 0)} ≠ 선언 {want}"
            for s, want in SECTION_TOTALS.items() if abs(got.get(s, 0) - want) > 1e-9]


_drift = rubric_drift()
if _drift:   # 조용한 어긋남 방지 — 부팅 즉시 드러낸다
    log.error("[post_scorer] ★ 배점 드리프트: %s", " / ".join(_drift))


def graded(key: str, n: int) -> float:
    """RUBRIC_COUNT 항목 채점 — 개수 기반 항목의 *유일한* 채점 경로."""
    lo, hi = RUBRIC_COUNT[key]
    return graded_count(n, lo, hi, mx(key))


def target_phrase(key: str, unit: str = "개") -> str:
    """작성 프롬프트용 목표 문구 — 채점 기준에서 자동 파생 (하드코딩 금지)."""
    lo, hi = RUBRIC_COUNT[key]
    if hi >= _NOCAP: return f"{lo}{unit} 이상"
    if lo == hi:     return f"{lo}{unit}"
    return f"{lo}~{hi}{unit}"


# ═══════════════════════════════════════════════════════
# 내부 HTML 분석 헬퍼
# ═══════════════════════════════════════════════════════

def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")

#   ★ 소수점·자릿점을 문장 종결로 세지 않는다 (2026-08-07 — 실측 계측 결함).
#     종전 정규식은 `[.!?。]` 를 무조건 종결로 봐서 **"8.7%" 를 문장 2개**로 셌다.
#     경제·테마 글은 수치가 본문의 골격이라, 문장 수를 쓰는 B1(도입부)·B2(문단)·
#     B10(면책)·N4(섹션 문장수)가 전부 부풀려진 값으로 채점되고 있었다
#     (B2_paragraphs 손실의 49% 가 이 한 줄에서 나왔다).
#     `(?<!\d)\.(?!\d)` — 앞뒤가 숫자인 마침표는 종결이 아니다. `!?。` 는 그대로 종결.
_SENT_END = re.compile(r'[가-힣a-zA-Z0-9][^.!?。]*(?:(?<!\d)\.(?!\d)|[!?。])')


def _sentences(text: str) -> int:
    return len(_SENT_END.findall(_strip(text)))

def _korean(html: str) -> int:
    from JARVIS02_WRITER.length_manager import count as _klen
    return _klen(_strip(html))

def _body(draft: Any) -> str:
    if isinstance(draft, dict):
        return draft.get("full_html") or draft.get("html") or draft.get("content") or ""
    return str(draft or "")

def _blocks(draft: Any) -> list:
    return draft.get("blocks") or [] if isinstance(draft, dict) else []

def _tags(draft: Any) -> list:
    return draft.get("tags") or [] if isinstance(draft, dict) else []

def _platform(draft: Any, override: str = "") -> str:
    if override: return override.lower()
    return (draft.get("platform") or "" if isinstance(draft, dict) else "").lower()

def _post_type(draft: Any, override: str = "") -> str:
    if override: return override.lower()
    return (draft.get("post_type") or "" if isinstance(draft, dict) else "").lower()

def _keyword(draft: Any, override: str = "") -> str:
    if override: return override
    if isinstance(draft, dict):
        return draft.get("keyword") or draft.get("theme") or ""
    return ""


# ═══════════════════════════════════════════════════════
# Section A — LLM 콘텐츠 품질 (20점)
# ═══════════════════════════════════════════════════════

def _a1(s: int) -> float:  # engagement 7점
    # 0~100 LLM 점수 → 0.5 단위 graded (50→0, 85+→7). 기존 계단 앵커(55/65/75/85) 재현 + 사이 세분. max 7 보존.
    frac = (s - 50) / 35.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return round(frac * mx("A1_engagement") * 2) / 2

def _a2(s: int) -> float:  # usefulness 5점
    # 0~100 LLM 점수 → 0.5 단위 graded (45→0, 85+→5). 기존 계단 앵커(55/65/75/85) 재현 + 사이 세분. max 5 보존.
    frac = (s - 45) / 40.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return round(frac * mx("A2_usefulness") * 2) / 2

def _a3(s: int) -> float:  # originality 4점
    # 0~100 LLM 점수 → 0.5 단위 graded (40→0, 80+→4). 기존 계단 앵커(50/60/70/80) 재현 + 사이 세분. max 4 보존.
    frac = (s - 40) / 40.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return round(frac * mx("A3_originality") * 2) / 2

def _a4(s: int) -> float:  # structure 3점
    # 0~100 LLM 점수 → 0.5 단위 graded (50→0, 80+→3). 기존 계단 앵커(60/70/80) 재현 + 사이 세분. max 3 보존.
    frac = (s - 50) / 30.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return round(frac * mx("A4_structure") * 2) / 2

def _a5(s: int) -> float:  # title_hook 1점
    # 0~100 LLM 점수 → 0.5 단위 graded (50→0, 80+→1). max 1 이므로 achievable set = {0, 0.5, 1.0}. max 1 보존.
    frac = (s - 50) / 30.0
    frac = 0.0 if frac < 0.0 else (1.0 if frac > 1.0 else frac)
    return round(frac * mx("A5_title_hook") * 2) / 2

def score_section_a(llm: dict) -> dict:
    llm = llm or {}
    def _int(k): return int(llm.get(k) or 0)
    items = {
        "A1_engagement": {"score": _a1(_int("engagement_score")), "max": mx("A1_engagement"), "name": "독자 몰입도"},
        "A2_usefulness":  {"score": _a2(_int("usefulness_score")), "max": mx("A2_usefulness"), "name": "실용적 유익성"},
        "A3_originality": {"score": _a3(_int("originality_score")), "max": mx("A3_originality"), "name": "독창적 관점"},
        "A4_structure":   {"score": _a4(_int("structure_score")), "max": mx("A4_structure"), "name": "논리 흐름"},
        "A5_title_hook":  {"score": _a5(_int("title_hook_score")), "max": mx("A5_title_hook"), "name": "제목 후킹"},
    }
    return {"total": round(sum(v["score"] for v in items.values()), 2), "max": 20.0, "items": items}


# ═══════════════════════════════════════════════════════
# Section B — 헌법 공통 (50점)
# ═══════════════════════════════════════════════════════

_AI_OPEN = re.compile(
    r'^[^가-힣]{0,10}(오늘|이번\s*주|최근\s*경제|오늘의\s*핵심|코스피|코스닥|\d+[월일년]|\d+[,\d]*원|\d+%)'
)
_INCOMPLETE = re.compile(r'추가\s*분석\s*필요|데이터\s*없음|해당\s*없음|TBD|미정|추후\s*업데이트')
_LLM_DIR   = re.compile(r'~등\s*더\s*구체적인|마무리\s*후\s*추가:|구체적인\s*실행\s*단계\s*제시')
_IMG_POS   = re.compile(r'위\s*표는|아래\s*표는|위\s*그래프는|아래\s*그래프는|위\s*차트는|아래\s*차트에서')
_FORBIDDEN = re.compile(r'©|All rights reserved|마켓시그널|구독|공감은|좋아요')
_EMOJI     = re.compile(r'[\U00010000-\U0010FFFF]|[☀-➿]', re.UNICODE)
_DISCLAIM  = re.compile(r'참고|권유|책임|판단.*본인|투자.*아님|정보.*제공')
_UNIT_MIX  = re.compile(r'(?:KRW.*?원|원.*?KRW|퍼센트.*?%|%.*?퍼센트)')


def _stem(word: str) -> str:
    """어절의 앞머리만 — 조사 차이를 흡수한다 (`퇴근길` ≡ `퇴근길에`)."""
    w = re.sub(r"[^가-힣A-Za-z]", "", word or "")
    return w[:3] if len(w) >= 3 else w


def _phrase_ngrams(text: str, n: int = 2) -> set[str]:
    """어절 n-gram(어간 기준) — 표현의 '뼈대' 만 남긴다."""
    words = [_stem(w) for w in re.split(r"\s+", _strip(text or "")) if w]
    words = [w for w in words if w]
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def repetition_penalty(intro_first: str, recent_intros: list[str] | None) -> float:
    """도입부가 최근 글들과 **얼마나 닮았는가** — 0.0(신선) ~ 1.0(판박이).

    ★ 왜 상투구 *목록* 을 박지 않는가 (2026-08-08 — 사용자 지시 감사)
      실측: 본문 첫 문단의 감성 상투구 비율이 **32.9% → 70.1%** 로 두 배가 됐다.
      그런데 기존 `_AI_OPEN` 은 `퇴근길에…` `주말 아침…` `요즘…` 을 **전부 통과** 시키고,
      통과하면 'AI 회피' 보너스를 준다 — **반복이 보상받는 구조** 였다.
      여기서 금지어를 박으면 다음 상투구를 찾아낼 뿐이다(두더지 잡기).
      → **최근 발행글에서 파생** 한다. 새 상투구가 생기면 그것도 자동으로 잡힌다.

    ★ 왜 '첫 어절' 이 주 신호인가 (실측으로 정한 것 — 초판은 여기서 틀렸다)
      최근 40편을 세어 보니 3어절 공유는 **0종**, 2어절은 최대 5편,
      그런데 **첫 어절은 40편 중 23편(58%)이 단 6개 단어**(요즘8·퇴근길6·출근길3·주말2
      ·아침2…)로 시작했다. 초판은 2어절만 봐서 감점이 **전부 0.00** 이었다.
      상투구는 문장 *앞* 에 산다 — 거기를 봐야 잡힌다.

    Args:
        recent_intros: 최근 발행글의 도입부 첫 문장들. **None 이면 0.0** —
            호출자가 맥락을 못 주면 감점하지 않는다(종전 동작 보존).
    """
    if not recent_intros:
        return 0.0
    text = _strip(intro_first or "")
    words = [w for w in re.split(r"\s+", text) if w]
    if not words:
        return 0.0

    def _head(t):
        ws = [w for w in re.split(r"\s+", _strip(t or "")) if w]
        return _stem(ws[0]) if ws else ""

    # ① 첫 어절 — 같은 말로 시작하는 글이 최근에 얼마나 흔한가 (주 신호)
    head = _stem(words[0])
    heads = [_head(r) for r in recent_intros]
    head_share = (sum(1 for h in heads if h and h == head) / len(recent_intros)) if head else 0.0

    # ② 2어절 뼈대 공유 — 보조 신호. 표본 크기에서 정족수를 파생한다.
    grams = _phrase_ngrams(text, 2)
    gram_share = 0.0
    if grams:
        quorum = max(2, len(recent_intros) // 5)
        shared = sum(1 for g in grams
                     if sum(1 for r in recent_intros if g in _phrase_ngrams(r, 2)) >= quorum)
        gram_share = shared / len(grams)

    # 첫 어절에 무게를 싣는다 — 실측상 그쪽이 변별력이 크다.
    return min(1.0, head_share * 2.0 + gram_share)


def _b1_intro(html: str, recent_intros: list[str] | None = None) -> float:
    """B1: 도입부 4문장 + 도입 이미지 + AI 자동생성형 금지 (헌법 제1조).

    ★ `recent_intros` 가 주어지면 **반복 감점** 을 적용한다 (2026-08-08).
      종전엔 상투구가 `_AI_OPEN` 을 통과해 오히려 'AI 회피' 보너스를 받았다.
    """
    _mx = mx("B1_intro")
    _ai_pt = max(0.0, _mx - 5.0)   # 5점 초과 배점 = 'AI 자동생성형 회피' 몫
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)[:6]
    if not paras: return 0.0
    intro = " ".join(_strip(p) for p in paras[:4])
    sents = _sentences(intro)
    first_text = _strip(paras[0]).strip()[:60]
    ai_open = bool(_AI_OPEN.search(first_text))
    has_img = bool(re.search(r'<img|<figure', html[:len("".join(paras[:4])) * 10], re.I))

    # AI 자동생성형 도입 위반 시: 문장수 기반 부분점수만 (구조 보너스 몰수)
    if ai_open:
        return round(max(0.0, (min(sents, 4) - 1) * 0.5) * 2) / 2
    # 정상: 4문장 근접도(문장당 1점, 상한 4.0) + 도입부 이미지 1점 + AI 회피 배점
    score = min(4.0, sents * 1.0) + (1.0 if has_img else 0.0) + _ai_pt
    # ★ 반복 감점 — 'AI 회피' 몫(_ai_pt)을 상한으로 깎는다. 상투구를 피한 대가로 준 점수이니
    #   판박이면 그 몫부터 회수하는 것이 맞다. 문장수·이미지 같은 구조 점수는 건드리지 않는다.
    score -= _ai_pt * repetition_penalty(first_text, recent_intros)
    return round(max(0.0, min(_mx, score)) * 2) / 2


def _b2_paragraphs(html: str) -> float:
    """B2: 문단 최대 2문장 (3점)"""
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
    v = sum(1 for p in paras if _sentences(_strip(p)) > 2)
    # 2문장 초과 문단 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(v, mx("B2_paragraphs"))


def _b5_factuality(issues: list) -> float:
    """B5: 수치 진실성 (5점) — 사실성 이슈 수로 역산"""
    n = sum(1 for i in (issues or []) if i.get("kind") == "factuality")
    # 사실성 이슈 위반 카운트: 위반당 1.5 감점 (하한 0)
    return graded_violation(n, mx("B5_factuality"), penalty=1.5)   # 사실성은 중대 → 1.5


def _b7_empty_headers(html: str) -> float:
    """B7: 빈 헤더 없음 (2점)"""
    headers = re.findall(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html, re.DOTALL)
    empty = sum(1 for h in headers if not _strip(h).strip())
    # 빈 헤더 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(empty, mx("B7_empty_hdr"))


def _b8_img_consecutive(html: str) -> float:
    """B8: 이미지 연속 없음 (2점)"""
    n = len(re.findall(
        r'</(?:figure|img)>\s*(?:<[^/ph][^>]*>\s*)*<(?:figure|img)',
        html, re.DOTALL
    ))
    # 이미지 연속 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(n, mx("B8_img_consec"))


def _b9_para_consecutive(html: str) -> float:
    """B9: 문단 3개 이상 연속 없음 (2점)"""
    segs = re.split(r'<(?:figure|img|table)[^>]*(?:/>|>.*?</(?:figure|table)>)', html, flags=re.DOTALL)
    v = sum(1 for seg in segs if len(re.findall(r'<p[^>]*>.*?</p>', seg, re.DOTALL)) >= 3)
    # 문단 3개+ 연속 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(v, mx("B9_para_consec"))


def _b10_disclaimer(html: str) -> float:
    """B10: 면책 문구 완비 (3점)"""
    tail = _strip(html)[-600:]
    if not _DISCLAIM.search(tail): return 0.0
    sents = _sentences(tail)
    elems = sum([
        bool(re.search(r'참고|정보', tail)),
        bool(re.search(r'권유.*아님|매수.*매도.*아님|아닙니다', tail)),
        bool(re.search(r'책임.*본인|본인.*책임|본인.*판단', tail)),
    ])
    # 면책 완비도: 기본 0.5(문구 존재) + 3요소 각 0.5 + 문장충분도(2문장+ 1.0 / 1문장 0.5)
    sent_score = 1.0 if sents >= 2 else (0.5 if sents >= 1 else 0.0)
    score = 0.5 + elems * 0.5 + sent_score
    return round(min(mx("B10_disclaimer"), score) * 2) / 2


def _b11_tone(html: str, plat: str) -> float:
    """B11: 플랫폼 어조 (2점)"""
    text = _strip(html)
    if plat == "naver":
        hayeo = len(re.findall(r'해요|이에요|였어요|하네요|겠어요|드려요|거에요|인데요', text))
        hamnida = len(re.findall(r'습니다[^만]|었습니다|겠습니다', text))
        # 해요체 우세도(우세 1.0/동률 0.5) + 빈도 충분도(5개+ 1.0/3개+ 0.5)
        dom = 1.0 if hayeo > hamnida else (0.5 if hayeo == hamnida else 0.0)
        cnt = 1.0 if hayeo >= 5 else (0.5 if hayeo >= 3 else 0.0)
        return round(min(mx("B11_tone"), dom + cnt) * 2) / 2
    if plat == "tistory":
        total = len(re.findall(r'습니다|이에요|해요|됩니다|합니다', text))
        # 어미 빈도 근접도: 5개+ 2.0, 하한 1.0 (문구 존재 보장)
        return round(min(mx("B11_tone"), max(1.0, total * 0.4)) * 2) / 2
    return mx("B11_tone") / 2   # 플랫폼 미상 — 중립


def graded_count(n: int, lo: int, hi: int, mx: float) -> float:
    """개수 기반 항목의 0.5 단위 채점 — **단일 진입점** (사용자 박제 2026-07-21).

    ★ 모든 항목은 0.5 단위로 세분화한다(전부 아니면 0 금지). 종전엔 개수 항목에
      `0.0 if n == 0 else ...` 절벽이 박혀, 소제목이 0개면 부분점수 없이 즉시 0 이었다.
      반대로 절벽을 그냥 없애면 0개인데도 공식상 1.5점이 나와 '관대화' 가 된다
      (그래서 원래 절벽이 있었다). → 0 에서 목표까지 *선형 상승* 시켜 둘 다 해결.

    구간:
      n <= 0        → 0.0            (아예 없으면 0 — 관대화 차단)
      0 < n < lo    → 0 → mx 선형    (있는 만큼 부분점수)
      lo <= n <= hi → mx             (목표 구간 만점)
      n > hi        → 초과 1개당 -0.5 (하한 0)
    """
    if n <= 0:
        return 0.0
    if n < lo:
        return round(mx * n / lo * 2) / 2
    if n <= hi:
        return float(mx)
    return max(0.0, round((mx - 0.5 * (n - hi)) * 2) / 2)


def graded_violation(n: int, mx: float, penalty: float = 0.5) -> float:
    """위반 카운트형 항목의 0.5 단위 채점 — **단일 진입점** (사용자 박제 2026-07-21).

    "없어야 정상"인 항목(금지어·빈 헤더·이미지 연속 등)은 만점에서 위반 1건당 감점.
    penalty 기본 0.5, 중대 항목(B5 사실성)만 상향.
    """
    return max(0.0, round((mx - penalty * max(0, n)) * 2) / 2)


def graded_scale(v: float, lo: float, hi: float, mx: float) -> float:
    """연속값(글자수 등) 항목의 0.5 단위 채점 — lo 이하 0, hi 이상 만점, 사이 선형."""
    if v <= lo: return 0.0
    if v >= hi: return float(mx)
    return round((v - lo) / (hi - lo) * mx * 2) / 2


def graded_limit(v: float, limit: float, mx: float, step: float = 0.0) -> float:
    """상한형(제목 길이 등) 0.5 단위 채점 — limit 이하 만점, 초과 step 당 -0.5.

    ★ limit 이하 = 만점. 작성 지시가 "N자 이내" 이므로 N자면 만점이어야 한다
      (종전엔 40자 지시인데 40자에서 2/3점만 줘 지시↔채점이 어긋났다).
    """
    if v <= limit: return float(mx)
    st = step or max(1.0, limit * 0.05)
    return max(0.0, round((mx - 0.5 * ((v - limit) / st)) * 2) / 2)


def _b16_image_count(html: str) -> float:
    """B16: 이미지 최소 5장(썸네일 제외) (3점)"""
    n = max(0, len(re.findall(r'<(?:img|figure)[^>]*>', html, re.I)) - 1)
    # 목표(MIN_IMAGES) 근접도 0.5 단위 — 초과는 헌법 "5+α" 라 무감점
    return graded("B16_img_count", n)


def _b17_body_length(html: str) -> float:
    """B17: 본문 분량 1,500자+ (3점)"""
    # 목표(TARGET_KOREAN)=만점 — 작성 지시가 "N자 이상" 이므로 충족 시 만점.
    return graded_scale(_korean(html), _TARGET_KOREAN / 2, _TARGET_KOREAN, mx("B17_body_len"))


def _b18_spacing(html: str) -> float:
    """B18: 여백 규정 준수 (2점)"""
    excess = len(re.findall(r'<p[^>]*>&nbsp;</p>\s*<p[^>]*>&nbsp;</p>', html))
    # 연속 빈문단(여백 초과) 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(excess, mx("B18_spacing"))


def _b19_chart(draft: Any) -> float:
    """B19: 차트 실데이터+팔레트 없음 (2점)"""
    try:
        from JARVIS06_IMAGE.validators.image_data_verifier import lookup_provenance
        unverified = 0
        for blk in _blocks(draft):
            if isinstance(blk, (list, tuple)) and len(blk) >= 2:
                p = str(blk[1])
                if re.search(r'\.(png|jpg|jpeg|webp)$', p, re.I):
                    prov = lookup_provenance(p)
                    if prov and prov.get("verified") is False:
                        unverified += 1
        # 미검증(verified=False) 차트 위반 카운트: 위반당 0.5 감점 (하한 0)
        return graded_violation(unverified, mx("B19_chart"))
    except Exception:
        return mx("B19_chart")


def _b21_consistency(html: str) -> float:
    """B21: 데이터 일관성 (2점)"""
    text = _strip(html)
    v1 = len(_UNIT_MIX.findall(text))
    v2 = len(re.findall(r'약\s*\d+[%원배]|대략\s*\d+', text))
    # 단위혼용·근사표현 위반 카운트 합: 위반당 0.5 감점 (하한 0)
    return graded_violation(v1 + v2, mx("B21_consistency"))


def _b22_tags(draft: Any) -> float:
    """B22: 태그 특수기호 없음 (2점)"""
    ts = _tags(draft)
    if not ts: return mx("B22_tags")
    bad = re.compile(r'[^가-힣a-zA-Z0-9]')
    v = sum(1 for t in ts if bad.search(str(t)))
    # 특수기호 포함 태그 위반 카운트: 위반당 0.5 감점 (하한 0)
    return graded_violation(v, mx("B22_tags"))


def score_section_b(draft: Any, platform: str = "", factuality_issues: list = None) -> dict:
    """Section B — 헌법 공통 (50점)."""
    html = _body(draft)
    plat = _platform(draft, platform)
    fi = factuality_issues or []

    items = {
        "B1_intro":         {"score": _b1_intro(html), "max": mx("B1_intro"), "name": "도입부 4문장+구조+AI금지"},
        "B2_paragraphs":    {"score": _b2_paragraphs(html), "max": mx("B2_paragraphs"), "name": "문단 최대 2문장"},
        "B3_differentiate": {"score": mx("B3_differentiate"), "max": mx("B3_differentiate"), "name": "플랫폼 차별화(프로세스)"},  # 근거: 단일 draft로 플랫폼 간 차별화 관측 불가(양쪽 대본 비교 필요) → 프로세스 보장, max 유지(rule 3)
        "B4_dynamic":       {"score": mx("B4_dynamic"), "max": mx("B4_dynamic"), "name": "동적 생성(프로세스)"},  # 근거: LLM 매 호출 동적생성 여부는 결정론 HTML 분석으로 관측 불가 → 프로세스 보장, max 유지(rule 3)
        "B5_factuality":    {"score": _b5_factuality(fi), "max": mx("B5_factuality"), "name": "수치 진실성"},
        "B6_incomplete":    {"score": graded_violation(len(_INCOMPLETE.findall(_strip(html))), 1.0), "max": mx("B6_incomplete"), "name": "미완성 표현 없음"},
        "B7_empty_hdr":     {"score": _b7_empty_headers(html), "max": mx("B7_empty_hdr"), "name": "빈 헤더 없음"},
        "B8_img_consec":    {"score": _b8_img_consecutive(html), "max": mx("B8_img_consec"), "name": "이미지 연속 없음"},
        "B9_para_consec":   {"score": _b9_para_consecutive(html), "max": mx("B9_para_consec"), "name": "문단 3개 연속 없음"},
        "B10_disclaimer":   {"score": _b10_disclaimer(html), "max": mx("B10_disclaimer"), "name": "면책 문구 완비"},
        "B11_tone":         {"score": _b11_tone(html, plat), "max": mx("B11_tone"), "name": "플랫폼 어조"},
        "B12_forbidden":    {"score": graded_violation(len(_FORBIDDEN.findall(_strip(html))) + len(_EMOJI.findall(_strip(html))), 1.0), "max": mx("B12_forbidden"), "name": "금지어·이모지 없음"},
        "B13_llm_dir":      {"score": graded_violation(len(_LLM_DIR.findall(_strip(html))), 1.0), "max": mx("B13_llm_dir"), "name": "LLM 지시문 노출 없음"},
        "B14_incomplete2":  {"score": graded_violation(len(_INCOMPLETE.findall(_strip(html))), 1.0), "max": mx("B14_incomplete2"), "name": "미완성 표현 없음(제7조)"},
        "B15_img_pos":      {"score": graded_violation(len(_IMG_POS.findall(_strip(html))), 1.0), "max": mx("B15_img_pos"), "name": "이미지 위치 지칭 없음"},
        "B16_img_count":    {"score": _b16_image_count(html), "max": mx("B16_img_count"), "name": "이미지 최소 5장"},
        "B17_body_len":     {"score": _b17_body_length(html), "max": mx("B17_body_len"), "name": f"본문 분량 {_target_korean()}자+"},
        "B18_spacing":      {"score": _b18_spacing(html), "max": mx("B18_spacing"), "name": "여백 규정 준수"},
        "B19_chart":        {"score": _b19_chart(draft), "max": mx("B19_chart"), "name": "차트 실데이터"},
        "B20_visual_div":   {"score": mx("B20_visual_div"), "max": mx("B20_visual_div"), "name": "시각화 스타일 중복 없음(프로세스)"},  # 근거: 차트 스타일 메타데이터 부재로 draft 단독 중복 관측 불가 → 프로세스 보장, max 유지(rule 3)
        "B21_consistency":  {"score": _b21_consistency(html), "max": mx("B21_consistency"), "name": "데이터 일관성"},
        "B22_tags":         {"score": _b22_tags(draft), "max": mx("B22_tags"), "name": "태그 특수기호 없음"},
        "B23_process":      {"score": mx("B23_process"), "max": mx("B23_process"), "name": "헌법 프로세스 준수"},  # 근거: 헌법 집행은 law_enforcer 프로세스가 보장(draft 관측 대상 아님) → max 유지(rule 3)
    }
    return {"total": round(sum(v["score"] for v in items.values()), 2), "max": 50.0, "items": items}


# ═══════════════════════════════════════════════════════
# Section C — 플랫폼별 SEO (20점)
# ═══════════════════════════════════════════════════════

def score_section_c_naver(draft: Any, kw: str = "") -> dict:
    """C-N — 네이버 SEO (20점)."""
    html = _body(draft)
    text = _strip(html)
    title = (draft.get("title") or "" if isinstance(draft, dict) else "")
    kw = kw or _keyword(draft)

    # N1 제목 40자 이내 (3점)
    tl = len(title.replace(" ", ""))
    n1 = graded_limit(tl, _std("naver", "title_max_chars", 40), mx("N1_title_len"), step=2)

    # N2 제목 키워드 앞부분 (3점)
    if kw and kw in title:
        rel = title.find(kw) / max(len(title), 1)
        _m = mx("N2_kw_in_title")
        n2 = (_m if rel <= 0.1 else _m - 0.5 if rel <= 0.25 else _m - 1.0 if rel <= 0.5
              else _m - 1.5 if rel <= 0.7 else _m - 2.0)
    else:
        n2 = 0.0 if kw else mx("N2_kw_in_title")

    # N3 H3 3~4개 (3점)
    h3 = len(re.findall(r'<h3[^>]*>', html, re.I))
    n3 = graded("N3_h3_count", h3)

    # N4 소제목 아래 2~3문장 (2점)
    sections = re.split(r'<h3[^>]*>.*?</h3>', html, flags=re.DOTALL | re.I)
    if len(sections) > 1:
        total = len(sections) - 1
        ok = sum(1 for s in sections[1:] if _sentences(_strip(s.split('<h')[0])) >= 2)
        n4 = round((ok / total) * mx("N4_section_sents") * 2) / 2
    else:
        n4 = 0.0

    # N5 키워드 밀도 1~2% (3점)
    if kw:
        words = max(len(re.findall(r'[가-힣a-zA-Z]+', text)), 1)
        d = (text.count(kw) / words) * 100
        _dev = 0.0 if 1.0 <= d <= 2.0 else (1.0 - d if d < 1.0 else d - 2.0)
        _m = mx("N5_kw_density")
        n5 = (_m if _dev == 0.0 else _m - 0.5 if _dev <= 0.25 else _m - 1.0 if _dev <= 0.5
              else _m - 1.5 if _dev <= 1.0 else _m - 2.0 if _dev <= 1.5
              else _m - 2.5 if _dev <= 2.0 else 0.0)
    else:
        n5 = mx("N5_kw_density")

    # N6 본문 키워드 3~5회 (2점)
    if kw:
        c = text.count(kw)
        n6 = graded("N6_kw_in_body", c)
    else:
        n6 = mx("N6_kw_in_body")

    # N7 해시태그 5~10개 (2점)
    nt = len(_tags(draft))
    n7 = graded("N7_hashtags", nt)

    # N8 해요체 일관 (2점)
    hayeo = len(re.findall(r'해요|이에요|였어요|하네요|겠어요|드려요|거에요|인데요', text))
    hamnida = len(re.findall(r'습니다[^만]|었습니다|겠습니다', text))
    _m = mx("N8_hayeo")
    if hayeo > hamnida:
        n8 = (_m if hayeo >= 5 else _m - 0.5 if hayeo >= 4 else _m - 1.0 if hayeo >= 3
              else _m - 1.5 if hayeo >= 1 else 0.0)
    else:
        n8 = (_m - 1.0) if hayeo >= 5 else ((_m - 1.5) if hayeo >= 3 else 0.0)

    items = {
        "N1_title_len":     {"score": n1, "max": mx("N1_title_len"), "name": f"제목 길이(≤{_std('naver', 'title_max_chars', 40):.0f})"},
        "N2_kw_in_title":   {"score": n2, "max": mx("N2_kw_in_title"), "name": "제목 키워드 앞부분"},
        "N3_h3_count":      {"score": n3, "max": mx("N3_h3_count"), "name": "H3 소제목 3~4개"},
        "N4_section_sents": {"score": n4, "max": mx("N4_section_sents"), "name": "소제목 아래 2~3문장"},
        "N5_kw_density":    {"score": n5, "max": mx("N5_kw_density"), "name": "키워드 밀도 1~2%"},
        "N6_kw_in_body":    {"score": n6, "max": mx("N6_kw_in_body"), "name": "본문 키워드 3~5회"},
        "N7_hashtags":      {"score": n7, "max": mx("N7_hashtags"), "name": f"해시태그 {_std('naver', 'hashtag_min', 5):.0f}~{_std('naver', 'hashtag_max', 10):.0f}개"},
        "N8_hayeo":         {"score": n8, "max": mx("N8_hayeo"), "name": "해요체 일관"},
    }
    return {"total": round(sum(v["score"] for v in items.values()), 2), "max": 20.0, "items": items}


def score_section_c_tistory(draft: Any, kw: str = "") -> dict:
    """C-T — 티스토리 SEO (20점)."""
    html = _body(draft)
    text = _strip(html)
    title = (draft.get("title") or "" if isinstance(draft, dict) else "")
    kw = kw or _keyword(draft)

    # T1 제목 55자 이내 (2점)
    tl = len(title.replace(" ", ""))
    t1 = graded_limit(tl, _std("tistory", "title_max_chars", 55), mx("T1_title_len"))

    # T2 제목 키워드 (2점)
    if kw and kw in title:
        rel = title.find(kw) / max(len(title), 1)
        _m = mx("T2_kw_in_title")
        t2 = (_m if rel <= 0.15 else _m - 0.5 if rel <= 0.35
              else _m - 1.0 if rel <= 0.6 else _m - 1.5)
    else:
        t2 = 0.0 if kw else mx("T2_kw_in_title")

    # T3 H1 1개 (2점)
    h1 = len(re.findall(r'<h1[^>]*>', html, re.I))
    t3 = graded("T3_h1_count", h1)

    # T4 H2 3~5개 (3점)
    h2 = len(re.findall(r'<h2[^>]*>', html, re.I))
    t4 = graded("T4_h2_count", h2)

    # T5 H3 범위 내 (2점)
    h3 = len(re.findall(r'<h3[^>]*>', html, re.I))
    t5 = graded_limit(h3, h2 * 3, mx("T5_h3_range"), step=1)

    # T6 롱테일 키워드 헤더 (3점)
    if kw:
        hdrs = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL | re.I)
        hits = sum(1 for h in hdrs if kw in _strip(h))
        t6 = graded("T6_longtail", hits)
    else:
        t6 = mx("T6_longtail")

    # T7 메타 설명 140~160자 (2점)
    meta = (draft.get("meta_description") or draft.get("meta_desc") or "" if isinstance(draft, dict) else "")
    _lo = _std("tistory", "meta_desc_min_chars", 140)
    _hi = _std("tistory", "meta_desc_max_chars", 160)
    if meta:
        ml = len(meta)
        _dev = 0 if _lo <= ml <= _hi else (_lo - ml if ml < _lo else ml - _hi)
        # 목표 범위 내 만점, 벗어난 10자마다 -0.5
        t7 = max(0.0, round((mx("T7_meta_desc") - 0.5 * ((_dev + 9) // 10)) * 2) / 2)
    else:
        t7 = 0.0

    # T8 내부 링크 1개 (2점)
    int_links = len(re.findall(r'<a[^>]+href=["\'][^"\'#][^"\']*["\']', html, re.I))
    t8 = graded("T8_internal_link", int_links)

    # T9 네이버 중복 없음 (2점) — 프로세스 보장
    t9 = mx("T9_no_dup")

    items = {
        "T1_title_len":     {"score": t1, "max": mx("T1_title_len"), "name": f"제목 길이(≤{_std('tistory', 'title_max_chars', 55):.0f})"},
        "T2_kw_in_title":   {"score": t2, "max": mx("T2_kw_in_title"), "name": "제목 키워드 포함"},
        "T3_h1_count":      {"score": t3, "max": mx("T3_h1_count"), "name": "H1 1개"},
        "T4_h2_count":      {"score": t4, "max": mx("T4_h2_count"), "name": "H2 3~5개"},
        "T5_h3_range":      {"score": t5, "max": mx("T5_h3_range"), "name": "H3 범위 내 활용"},
        "T6_longtail":      {"score": t6, "max": mx("T6_longtail"), "name": "롱테일 키워드 헤더"},
        "T7_meta_desc":     {"score": t7, "max": mx("T7_meta_desc"), "name": f"메타 설명 길이({_std('tistory', 'meta_desc_min_chars', 140):.0f}-{_std('tistory', 'meta_desc_max_chars', 160):.0f})"},
        "T8_internal_link": {"score": t8, "max": mx("T8_internal_link"), "name": "내부 링크 1개"},
        "T9_no_dup":        {"score": t9, "max": mx("T9_no_dup"), "name": "네이버 중복 없음(프로세스)"},
    }
    return {"total": round(sum(v["score"] for v in items.values()), 2), "max": 20.0, "items": items}


def score_section_c(draft: Any, platform: str = "", keyword: str = "") -> dict:
    plat = _platform(draft, platform)
    kw = keyword or _keyword(draft)
    if plat == "tistory":
        return score_section_c_tistory(draft, kw)
    return score_section_c_naver(draft, kw)


# ═══════════════════════════════════════════════════════
# Section D — 글 종류별 전용 (10점)
# ═══════════════════════════════════════════════════════

def score_section_d_theme(draft: Any) -> dict:
    """D-TH — 테마주 전용 (10점)."""
    text = _strip(_body(draft))

    # TH1: 수익률 3개월만 (5점)
    bad_period = len(re.findall(
        r'(?:1개월|한달|6개월|6month|1년|연간|YTD|YOY|월간)\s*(?:수익률|상승|하락|수익|성과)',
        text
    ))
    # 0.5 단위 graded — 위반 카운트형(max 5): 잘못된 기간 표기 1건당 -0.5, 0 하한
    th1 = graded_violation(bad_period, mx("TH1_3m_return"))

    # TH2: 재무수치 본문 기재 없음 (5점)
    fin_text = len(re.findall(
        r'PER\s*\d|ROE\s*\d|영업이익률\s*\d|현재가\s*\d|시가총액\s*\d|매출액\s*\d|순이익\s*\d|부채비율\s*\d',
        text
    ))
    # 0.5 단위 graded — 위반 카운트형(max 5): 본문 재무수치 1건당 -0.5, 0 하한
    th2 = graded_violation(fin_text, mx("TH2_no_fin_text"))

    items = {
        "TH1_3m_return":    {"score": th1, "max": mx("TH1_3m_return"), "name": "수익률 3개월만 표기"},
        "TH2_no_fin_text":  {"score": th2, "max": mx("TH2_no_fin_text"), "name": "재무수치 본문 기재 없음"},
    }
    return {"total": round(th1 + th2, 2), "max": 10.0, "items": items}


def score_section_d_economic(draft: Any) -> dict:
    """D-EC — 경제브리핑 전용 (10점)."""
    text = _strip(_body(draft))

    # EC1: 실데이터 사용 (4점) — 의심 수치 패턴 역산
    suspicious = len(re.findall(r'약\s*\d+\.?\d*\s*[%포인트p]|대략\s*\d+|추정\s*\d+', text))
    # 0.5 단위 graded — 위반 카운트형(max 4): 의심(약/대략/추정) 수치 1건당 -0.5, 0 하한
    ec1 = graded_violation(suspicious, mx("EC1_real_data"))

    # EC2: 시장 영향 인과 서술 (4점)
    causal = len(re.findall(
        r'→|따라서|그로\s*인해|로\s*인해|때문에|영향을\s*미|이어질|파급\s*효과|로\s*이어',
        text
    ))
    ec2 = graded("EC2_causal", causal)

    # EC3: 경제 용어 설명 (2점)
    explain = len(re.findall(r'이란|란\s*무엇|을?\s*의미|쉽게\s*말해|다시\s*말해|풀어\s*말하면|뜻하는', text))
    ec3 = graded("EC3_term_explain", explain)

    items = {
        "EC1_real_data":    {"score": ec1, "max": mx("EC1_real_data"), "name": "경제지표 실데이터"},
        "EC2_causal":       {"score": ec2, "max": mx("EC2_causal"), "name": "시장 영향 인과 서술"},
        "EC3_term_explain": {"score": ec3, "max": mx("EC3_term_explain"), "name": "경제 용어 쉬운 설명"},
    }
    return {"total": round(ec1 + ec2 + ec3, 2), "max": 10.0, "items": items}


def score_section_d(draft: Any, post_type: str = "") -> dict:
    ptype = _post_type(draft, post_type)
    if ptype == "economic":
        return score_section_d_economic(draft)
    return score_section_d_theme(draft)


def gate_checklist_lines(post_type: str = "", platform: str = "") -> list[str]:
    """작성 프롬프트 사전 고지용 — 이 모듈이 *실제 채점하는* 기준의 행동 지침 요약.

    ★ 2026-07-16 신설 (규정 숙지 단계): "다 만들고 채점에서 걸려 재작성" 대신
      "처음부터 채점 기준대로" 쓰게 한다. law_enforcer.build_gate_checklist_block 이 호출.
    ★ 채점 함수(Section C/D)와 같은 파일에 두어 드리프트 국소화 —
      위 채점 함수의 숫자를 바꾸면 이 요약도 *반드시 동시에* 수정할 것.
    """
    lines: list[str] = [f"본문 이미지 {target_phrase('B16_img_count', '장')} (썸네일 제외)"]
    plat = (platform or "").lower()
    if plat == "tistory":
        lines += [
            "제목 ≤55자 + 핵심 키워드를 제목 앞쪽에 배치",
            f"H1 {target_phrase('T3_h1_count')} · H2 소제목 {target_phrase('T4_h2_count')}"
            f" · 키워드 포함 헤더(롱테일) {target_phrase('T6_longtail')}",
            f"본문 키워드 {target_phrase('N6_kw_in_body', '회')} (밀도 1~2%)",
        ]
    else:  # naver
        lines += [
            "제목 ≤40자 + 핵심 키워드를 제목 앞쪽에 배치",
            f"H3 소제목 {target_phrase('N3_h3_count')}, 각 소제목 아래 2~3문장 이상",
            f"본문 키워드 {target_phrase('N6_kw_in_body', '회')} (밀도 1~2%)"
            f" · 해시태그 {target_phrase('N7_hashtags')} · 해요체 우세 문체 (~해요/~이에요)",
        ]
    pt = (post_type or "").lower()
    if pt == "economic":
        lines += [
            "숫자에 '약·대략·추정' 붙이지 말 것 — 수집 실데이터 수치만 그대로 인용",
            f"시장 영향은 인과 연결어(따라서·때문에·→ 등)로 {target_phrase('EC2_causal', '회')} 서술",
            f"경제 용어 {target_phrase('EC3_term_explain')} — 쉬운 말로 풀이 ('~이란', '쉽게 말해' 등)",
        ]
    elif pt == "theme":
        lines += [
            "수익률은 3개월 기준만 언급 (1개월·6개월·1년 수익률 금지)",
            "PER·ROE·현재가·시가총액 등 재무 수치를 본문 텍스트에 직접 쓰지 말 것 (차트로만)",
        ]
    return lines


# ═══════════════════════════════════════════════════════
# 메인 API
# ═══════════════════════════════════════════════════════

def score_post(
    draft: Any,
    platform: str = "",
    post_type: str = "",
    keyword: str = "",
    llm_scores: dict | None = None,
    factuality_issues: list | None = None,
) -> dict:
    """블로그 글 100점 루브릭 채점.

    Returns:
        {"total": float, "passed": bool, "sections": {...}, "platform": str, "post_type": str}
    """
    plat  = _platform(draft, platform)
    ptype = _post_type(draft, post_type)
    kw    = keyword or _keyword(draft)
    fi    = [x for x in (factuality_issues or []) if x.get("kind") == "factuality"]

    sec_a = score_section_a(llm_scores or {})
    sec_b = score_section_b(draft, plat, fi)
    sec_c = score_section_c(draft, plat, kw)
    sec_d = score_section_d(draft, ptype)

    total = round(min(100.0, sec_a["total"] + sec_b["total"] + sec_c["total"] + sec_d["total"]), 2)
    passed = total >= PASS_THRESHOLD

    log.info(
        "[post_scorer] %s/%s %.1f점 (A=%.1f B=%.1f C=%.1f D=%.1f) → %s",
        plat, ptype, total,
        sec_a["total"], sec_b["total"], sec_c["total"], sec_d["total"],
        "통과" if passed else "재작성"
    )

    return {
        "total": total,
        "passed": passed,
        "sections": {"A": sec_a, "B": sec_b, "C": sec_c, "D": sec_d},
        "platform": plat,
        "post_type": ptype,
    }


def draft_from_row(row: dict) -> dict:
    """저장된 `post_analysis` 행 → **채점용 draft** (2026-08-07 신설).

    ★ 왜 단일 진입점이 필요했나
      종전엔 `post_quality_analyzer` 안에 이 조립이 **인라인**으로 있었고, 거기서
      `"keyword": title` 로 넘겼다. 제목 전체 문자열이 본문에 통째로 나오는 일은 없으므로
      키워드 의존 항목들이 구조적으로 죽었다 — 실측 `N5_kw_density` 만점률 **0%**
      (40건 전부 정확히 1.5점, 분산 0) · `N6_kw_in_body` 평균 0.55/2.
      게다가 발행 메타(태그·메타 설명)를 싣지 않아 `N7`·`T7` 도 발행 후엔 다시 0점이었다.
      "발행 전엔 만점, DB 점수는 0점" — 학습이 개선을 벌로 받는 구조다.

    조립을 여기 하나로 모으면 *발행 전 draft* 와 *발행 후 복원 draft* 가 같은 것을 뜻하게 된다.
    """
    import json as _json
    body = row.get("original_html") or row.get("original_content") or ""
    meta = row.get("publish_meta")
    if isinstance(meta, str):
        try:
            meta = _json.loads(meta)
        except Exception:
            meta = {}
    meta = meta if isinstance(meta, dict) else {}
    return {
        "html": body, "content": body,
        "title": row.get("title") or "",
        # ★ 제목이 아니라 **실제 주제 키워드**.
        #   우선순위: ① 발행 시점에 게이트가 *채점에 쓴 바로 그 키워드*(publish_meta.keyword)
        #            ② source_keyword ③ theme.
        #   ①이 먼저인 이유 — `source_keyword` 는 `trends.keyword` 와 맞춰 보는 **조인 키**라
        #   원본 라벨(`황사/미세먼지`)이 그대로 들어온다. 그 라벨은 한국어 산문에 통째로
        #   나올 수 없어, 그걸로 채점하면 발행 전(미세먼지)과 발행 후(황사/미세먼지)가
        #   **다른 잣대**가 된다. 실측 6건 전부 2.0~5.5점 괴리.
        #   조인 키의 의미를 바꾸지 않고 채점용 값을 따로 나르는 이유다 —
        #   한 칸에 두 의미를 담으면 반드시 한쪽이 깨진다.
        "keyword": (meta.get("keyword") or row.get("source_keyword")
                    or row.get("theme") or "").strip(),
        "post_type": row.get("post_type") or "",
        "platform": row.get("platform") or "",
        "tags": meta.get("tags") or [],
        "meta_description": meta.get("meta_description") or "",
    }


def item_scores(sr: dict) -> list:
    """★ 채점된 **모든 항목** — 만점 항목까지 포함한 단일 진실 소스 (2026-08-07 신설).

    ★ 왜 신설했나 (사용자 요구: "결국 100점 맞기 위해서 학습하는 거 아냐?")
      종전엔 `deducted_items()` 하나뿐이라 **만점 항목이 시스템 눈에 보이지 않았다**.
      그래서 "만점이던 항목이 떨어졌다" 를 감지할 수단이 없었다 — 실측으로 퇴행 45건이
      개선 27건보다 많았는데 아무도 몰랐다. *유지* 도 학습 대상이다.

    `deducted_items()` 는 이 함수의 **필터**다 — 항목 목록을 만드는 로직을 두 곳에 두지
    않는다(① 단일 진입점). 항목명·만점은 sr 에서 파생하며 코드에 박지 않는다(② 동적 설계).

    Returns: [{"section","key","name","score","max","gap"}]  (gap=max-score, 내림차순)
      · max=0 인 항목은 *그 조합에 적용되지 않는 항목* 이라 제외한다
        (예: 네이버 글에 티스토리 전용 T7_meta_desc). 조합별 항목 수가 다른 이유다.
    """
    out: list = []
    for skey, sec in (sr.get("sections") or {}).items():
        if not isinstance(sec, dict):
            continue
        for ikey, iv in (sec.get("items") or {}).items():
            if not isinstance(iv, dict):
                continue
            _sc = float(iv.get("score", 0))
            _mx = float(iv.get("max", 0))
            if not _mx:
                continue                      # 이 조합에 적용되지 않는 항목
            out.append({"section": skey, "key": ikey, "name": iv.get("name", ikey),
                        "score": _sc, "max": _mx, "gap": round(_mx - _sc, 2)})
    out.sort(key=lambda x: -x["gap"])
    return out


def deducted_items(sr: dict) -> list:
    """★ 100점 루브릭에서 *감점된 항목만* 추출 — 발행 후 개선 제안의 단일 기준 (사용자 박제 2026-07-24).

    이 리스트가 "부족한 점" 의 유일한 출처 — 발행 후 품질 분석은 여기 항목만 개선안으로 만든다
    (발행 전 채점 기준과 100% 동일).

    ★ 2026-08-07 — `item_scores()` 의 **필터**로 재정의. 종전엔 순회 로직을 자체 보유했다.
    """
    return [d for d in item_scores(sr) if d["gap"] > 0]


def items_compact(sr: dict) -> dict:
    """항목 상세를 **저장용 최소 형태**로 — `{항목key: 점수}`.

    ★ 만점·섹션·표시명을 함께 저장하지 않는 이유 (② 동적 설계)
      셋 다 채점기가 이미 아는 값이다. 함께 저장하면 배점을 조정한 날부터 **옛 행이
      옛 배점을 주장**하는 복사본 사고가 난다(CLAUDE.md "복사본을 진실로 믿지 말 것").
      읽을 때 `RUBRIC_MAX` 와 `item_index()` 로 파생한다 — 진실은 언제나 채점기에 있다.
    """
    return {d["key"]: d["score"] for d in item_scores(sr)}


@_lru_cache(maxsize=1)
def item_index() -> dict:
    """항목 key → {"section","name"} — **채점기 자신에서 파생** (② 동적 설계).

    저장된 `{key: score}` 를 사람이 읽을 형태로 되돌릴 때 쓴다. 이름표를 따로 적어 두지
    않는 이유는 `items_compact()` 와 같다 — 항목이 추가·개명되면 **자동으로 따라온다**.
    빈 초안을 4조합으로 한 번씩 채점해 이름을 거둬들인다(LLM 호출 0 · 캐시 1회).
    """
    out: dict = {}
    _lvl = log.level
    log.setLevel(_logging.WARNING)          # 파생용 채점의 info 로그는 남기지 않는다
    try:
        for _plat in ("naver", "tistory"):
            for _pt in ("economic", "theme"):
                try:
                    _sr = score_post({"html": "", "content": "", "title": "",
                                      "keyword": "", "post_type": _pt},
                                     platform=_plat, post_type=_pt)
                except Exception:
                    continue
                for _sk, _sec in (_sr.get("sections") or {}).items():
                    for _k, _iv in ((_sec or {}).get("items") or {}).items():
                        if isinstance(_iv, dict):
                            out.setdefault(_k, {"section": _sk,
                                                "name": _iv.get("name", _k)})
    finally:
        log.setLevel(_lvl)
    return out


@_lru_cache(maxsize=1)
def pipeline_controlled_items() -> frozenset:
    """**파이프라인이 채우는** 항목들 — 작성 LLM 이 만들 수 없는 것 (2026-08-07 신설).

    ★ 왜 필요한가
      약점 항목을 작성 프롬프트에 주입하기 시작하면서, `내부 링크 1개: 최근 100% 의 글에서
      0점 — 이번엔 반드시 채울 것` 같은 지시가 실제로 나갔다. 그런데 내부 링크·태그·
      메타 설명은 **발행 파이프라인이 만든다**. 작성 LLM 에게 시키면 할 수 있는 일은
      하나뿐 — **URL 을 지어내는 것** 이다(BLOG_SUPREME_LAW 제5조 진실성 정면 위반).
      실제로 과거에 점수를 받은 3건이 전부 날조 URL 이었다.

    ★ 목록을 박지 않는다 (② 동적 설계)
      *생산물을 실제로 넣어 보고 점수가 움직이는 항목* 을 채점기에게 물어서 파생한다.
      새 생산자가 생기면 아래 probe 에 그 산출물만 더하면 되고, **항목 목록은 자동**이다.
    """
    body = "코스피가 올랐다. 반도체가 지수를 이끌었다. 외국인이 순매수했다. " * 20
    base = {"title": "코스피 상승", "keyword": "코스피", "post_type": "economic"}
    try:
        from JARVIS08_PUBLISH.internal_links import related_links_html
        link = related_links_html("tistory") or \
            '<hr/><ul><li><a href="https://example.com/1">이전 글</a></li></ul>'
    except Exception:
        link = '<hr/><ul><li><a href="https://example.com/1">이전 글</a></li></ul>'
    out: set = set()
    for plat in ("naver", "tistory"):
        for pt in ("economic", "theme"):
            try:
                a = score_post({**base, "post_type": pt, "html": body, "content": body},
                               platform=plat, post_type=pt)
                b = score_post({**base, "post_type": pt,
                                "html": body + link, "content": body + link,
                                "tags": ["코스피", "반도체", "증시", "투자전략", "시장분석"],
                                "meta_description": "가" * 150},
                               platform=plat, post_type=pt)
            except Exception:
                continue
            av = {i["key"]: i["score"] for i in item_scores(a)}
            bv = {i["key"]: (i["score"], i["max"]) for i in item_scores(b)}
            # ★ '값이 흔들렸다' 가 아니라 **0 → 만점** 인 항목만 잡는다.
            #   생산물을 본문에 덧붙이면 문단 수 같은 것도 덩달아 움직여서(실측 B1_intro),
            #   단순 차이로 보면 작성자가 고쳐야 할 항목까지 빼앗긴다.
            #   0 → 만점은 "그 생산물이 곧 그 항목" 이라는 뜻이다.
            out |= {k for k, (sc, mx) in bv.items()
                    if mx and av.get(k) == 0 and sc >= mx}
    return frozenset(out)


def items_expand(compact: dict) -> list:
    """`items_compact()` 의 역변환 — 저장된 `{key: score}` → item_scores 와 같은 꼴.

    만점은 `RUBRIC_MAX`(단일 진실 소스), 섹션·이름은 `item_index()` 에서 파생한다.
    """
    idx = item_index()
    out = []
    for k, sc in (compact or {}).items():
        _mx = float(RUBRIC_MAX.get(k, 0))
        if not _mx:
            continue                        # 폐지된 항목 — 옛 행이 새 코드를 오염시키지 않는다
        meta = idx.get(k) or {}
        _sc = float(sc)
        out.append({"section": meta.get("section", ""), "key": k,
                    "name": meta.get("name", k), "score": _sc, "max": _mx,
                    "gap": round(_mx - _sc, 2)})
    out.sort(key=lambda x: -x["gap"])
    return out
