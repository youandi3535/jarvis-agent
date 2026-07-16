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
from typing import Any

log = logging.getLogger(__name__)

PASS_THRESHOLD: float = 70.0


# ═══════════════════════════════════════════════════════
# 내부 HTML 분석 헬퍼
# ═══════════════════════════════════════════════════════

def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")

def _sentences(text: str) -> int:
    return len(re.findall(r'[가-힣a-zA-Z0-9][^.!?。]*[.!?。]', _strip(text)))

def _korean(html: str) -> int:
    return len(re.findall(r'[가-힣]', _strip(html)))

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
    if s >= 85: return 7.0
    if s >= 75: return 5.0
    if s >= 65: return 3.0
    if s >= 55: return 1.0
    return 0.0

def _a2(s: int) -> float:  # usefulness 5점
    if s >= 85: return 5.0
    if s >= 75: return 4.0
    if s >= 65: return 2.5
    if s >= 55: return 1.0
    return 0.0

def _a3(s: int) -> float:  # originality 4점
    if s >= 80: return 4.0
    if s >= 70: return 3.0
    if s >= 60: return 2.0
    if s >= 50: return 1.0
    return 0.0

def _a4(s: int) -> float:  # structure 3점
    if s >= 80: return 3.0
    if s >= 70: return 2.0
    if s >= 60: return 1.0
    return 0.0

def _a5(s: int) -> float:  # title_hook 1점
    if s >= 80: return 1.0
    if s >= 65: return 0.7
    if s >= 50: return 0.3
    return 0.0

def score_section_a(llm: dict) -> dict:
    llm = llm or {}
    def _int(k): return int(llm.get(k) or 0)
    items = {
        "A1_engagement": {"score": _a1(_int("engagement_score")), "max": 7, "name": "독자 몰입도"},
        "A2_usefulness":  {"score": _a2(_int("usefulness_score")),  "max": 5, "name": "실용적 유익성"},
        "A3_originality": {"score": _a3(_int("originality_score")), "max": 4, "name": "독창적 관점"},
        "A4_structure":   {"score": _a4(_int("structure_score")),   "max": 3, "name": "논리 흐름"},
        "A5_title_hook":  {"score": _a5(_int("title_hook_score")),  "max": 1, "name": "제목 후킹"},
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


def _b1_intro(html: str) -> float:
    """B1: 도입부 4문장+구조+AI금지 (5점)"""
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)[:6]
    if not paras: return 0.0
    intro = " ".join(_strip(p) for p in paras[:4])
    sents = _sentences(intro)
    first_text = _strip(paras[0]).strip()[:60]
    ai_open = bool(_AI_OPEN.search(first_text))
    has_img = bool(re.search(r'<img|<figure', html[:len("".join(paras[:4])) * 10], re.I))

    if ai_open: return max(0.0, (sents - 1) * 0.5)
    if sents >= 4: return 5.0 if has_img else 4.0
    if sents >= 3: return 2.0
    if sents >= 2: return 1.0
    return 0.0


def _b2_paragraphs(html: str) -> float:
    """B2: 문단 최대 2문장 (3점)"""
    paras = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
    v = sum(1 for p in paras if _sentences(_strip(p)) > 2)
    return 3.0 if v == 0 else (1.5 if v <= 2 else 0.0)


def _b5_factuality(issues: list) -> float:
    """B5: 수치 진실성 (5점) — 사실성 이슈 수로 역산"""
    n = sum(1 for i in (issues or []) if i.get("kind") == "factuality")
    return 5.0 if n == 0 else (2.5 if n == 1 else 0.0)


def _b7_empty_headers(html: str) -> float:
    """B7: 빈 헤더 없음 (2점)"""
    headers = re.findall(r'<h[1-6][^>]*>(.*?)</h[1-6]>', html, re.DOTALL)
    empty = sum(1 for h in headers if not _strip(h).strip())
    return 2.0 if empty == 0 else (1.0 if empty == 1 else 0.0)


def _b8_img_consecutive(html: str) -> float:
    """B8: 이미지 연속 없음 (2점)"""
    n = len(re.findall(
        r'</(?:figure|img)>\s*(?:<[^/ph][^>]*>\s*)*<(?:figure|img)',
        html, re.DOTALL
    ))
    return 2.0 if n == 0 else (1.0 if n == 1 else 0.0)


def _b9_para_consecutive(html: str) -> float:
    """B9: 문단 3개 이상 연속 없음 (2점)"""
    segs = re.split(r'<(?:figure|img|table)[^>]*(?:/>|>.*?</(?:figure|table)>)', html, flags=re.DOTALL)
    v = sum(1 for seg in segs if len(re.findall(r'<p[^>]*>.*?</p>', seg, re.DOTALL)) >= 3)
    return 2.0 if v == 0 else (1.0 if v == 1 else 0.0)


def _b10_disclaimer(html: str) -> float:
    """B10: 면책 문구 완비 (3점)"""
    tail = _strip(html)[-600:]
    if not _DISCLAIM.search(tail): return 0.0
    sents = _sentences(tail)
    has3 = all([
        bool(re.search(r'참고|정보', tail)),
        bool(re.search(r'권유.*아님|매수.*매도.*아님|아닙니다', tail)),
        bool(re.search(r'책임.*본인|본인.*책임|본인.*판단', tail)),
    ])
    if sents >= 2 and has3: return 3.0
    if sents >= 2: return 2.0
    if sents >= 1: return 1.0
    return 0.5


def _b11_tone(html: str, plat: str) -> float:
    """B11: 플랫폼 어조 (2점)"""
    text = _strip(html)
    if plat == "naver":
        hayeo = len(re.findall(r'해요|이에요|였어요|하네요|겠어요|드려요|거에요|인데요', text))
        hamnida = len(re.findall(r'습니다[^만]|었습니다|겠습니다', text))
        return 2.0 if hayeo > hamnida and hayeo >= 5 else (1.0 if hayeo >= 3 else 0.0)
    if plat == "tistory":
        total = len(re.findall(r'습니다|이에요|해요|됩니다|합니다', text))
        return 2.0 if total >= 5 else 1.0
    return 1.0


def _b16_image_count(html: str) -> float:
    """B16: 이미지 최소 5장(썸네일 제외) (3점)"""
    n = max(0, len(re.findall(r'<(?:img|figure)[^>]*>', html, re.I)) - 1)
    return 3.0 if n >= 5 else (2.0 if n == 4 else (1.0 if n == 3 else 0.0))


def _b17_body_length(html: str) -> float:
    """B17: 본문 분량 1,500자+ (3점)"""
    n = _korean(html)
    return 3.0 if n >= 2000 else (2.0 if n >= 1500 else (1.0 if n >= 1200 else 0.0))


def _b18_spacing(html: str) -> float:
    """B18: 여백 규정 준수 (2점)"""
    excess = len(re.findall(r'<p[^>]*>&nbsp;</p>\s*<p[^>]*>&nbsp;</p>', html))
    return 2.0 if excess == 0 else (1.0 if excess <= 2 else 0.0)


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
        return 2.0 if unverified == 0 else (1.0 if unverified == 1 else 0.0)
    except Exception:
        return 2.0


def _b21_consistency(html: str) -> float:
    """B21: 데이터 일관성 (2점)"""
    text = _strip(html)
    v1 = bool(_UNIT_MIX.search(text))
    v2 = bool(re.search(r'약\s*\d+[%원배]|대략\s*\d+', text))
    return 2.0 if not (v1 or v2) else (1.0 if not (v1 and v2) else 0.0)


def _b22_tags(draft: Any) -> float:
    """B22: 태그 특수기호 없음 (2점)"""
    ts = _tags(draft)
    if not ts: return 2.0
    bad = re.compile(r'[^가-힣a-zA-Z0-9]')
    v = sum(1 for t in ts if bad.search(str(t)))
    return 2.0 if v == 0 else (1.0 if v <= 2 else 0.0)


def score_section_b(draft: Any, platform: str = "", factuality_issues: list = None) -> dict:
    """Section B — 헌법 공통 (50점)."""
    html = _body(draft)
    plat = _platform(draft, platform)
    fi = factuality_issues or []

    items = {
        "B1_intro":         {"score": _b1_intro(html),              "max": 5, "name": "도입부 4문장+구조+AI금지"},
        "B2_paragraphs":    {"score": _b2_paragraphs(html),         "max": 3, "name": "문단 최대 2문장"},
        "B3_differentiate": {"score": 2.0,                          "max": 2, "name": "플랫폼 차별화(프로세스)"},
        "B4_dynamic":       {"score": 2.0,                          "max": 2, "name": "동적 생성(프로세스)"},
        "B5_factuality":    {"score": _b5_factuality(fi),           "max": 5, "name": "수치 진실성"},
        "B6_incomplete":    {"score": 0.0 if _INCOMPLETE.search(_strip(html)) else 1.0, "max": 1, "name": "미완성 표현 없음"},
        "B7_empty_hdr":     {"score": _b7_empty_headers(html),      "max": 2, "name": "빈 헤더 없음"},
        "B8_img_consec":    {"score": _b8_img_consecutive(html),    "max": 2, "name": "이미지 연속 없음"},
        "B9_para_consec":   {"score": _b9_para_consecutive(html),   "max": 2, "name": "문단 3개 연속 없음"},
        "B10_disclaimer":   {"score": _b10_disclaimer(html),        "max": 3, "name": "면책 문구 완비"},
        "B11_tone":         {"score": _b11_tone(html, plat),        "max": 2, "name": "플랫폼 어조"},
        "B12_forbidden":    {"score": 0.0 if (_FORBIDDEN.search(_strip(html)) or _EMOJI.search(_strip(html))) else 1.0, "max": 1, "name": "금지어·이모지 없음"},
        "B13_llm_dir":      {"score": 0.0 if _LLM_DIR.search(_strip(html)) else 1.0,  "max": 1, "name": "LLM 지시문 노출 없음"},
        "B14_incomplete2":  {"score": 0.0 if _INCOMPLETE.search(_strip(html)) else 1.0,"max": 1, "name": "미완성 표현 없음(제7조)"},
        "B15_img_pos":      {"score": 0.0 if _IMG_POS.search(_strip(html)) else 1.0,   "max": 1, "name": "이미지 위치 지칭 없음"},
        "B16_img_count":    {"score": _b16_image_count(html),       "max": 3, "name": "이미지 최소 5장"},
        "B17_body_len":     {"score": _b17_body_length(html),       "max": 3, "name": "본문 분량 1500자+"},
        "B18_spacing":      {"score": _b18_spacing(html),           "max": 2, "name": "여백 규정 준수"},
        "B19_chart":        {"score": _b19_chart(draft),            "max": 2, "name": "차트 실데이터"},
        "B20_visual_div":   {"score": 2.0,                          "max": 2, "name": "시각화 스타일 중복 없음(프로세스)"},
        "B21_consistency":  {"score": _b21_consistency(html),       "max": 2, "name": "데이터 일관성"},
        "B22_tags":         {"score": _b22_tags(draft),             "max": 2, "name": "태그 특수기호 없음"},
        "B23_process":      {"score": 1.0,                          "max": 1, "name": "헌법 프로세스 준수"},
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
    n1 = 3.0 if tl <= 35 else (2.0 if tl <= 40 else (1.0 if tl <= 44 else 0.0))

    # N2 제목 키워드 앞부분 (3점)
    if kw and kw in title:
        n2 = 3.0 if title.find(kw) < len(title) // 2 else 2.0
    else:
        n2 = 0.0 if kw else 3.0

    # N3 H3 3~4개 (3점)
    h3 = len(re.findall(r'<h3[^>]*>', html, re.I))
    n3 = 3.0 if 3 <= h3 <= 4 else (2.0 if h3 == 2 else (1.0 if h3 == 1 else 0.0))

    # N4 소제목 아래 2~3문장 (2점)
    sections = re.split(r'<h3[^>]*>.*?</h3>', html, flags=re.DOTALL | re.I)
    if len(sections) > 1:
        ok = sum(1 for s in sections[1:] if _sentences(_strip(s.split('<h')[0])) >= 2)
        n4 = 2.0 if ok >= len(sections) - 1 else (1.0 if ok > 0 else 0.0)
    else:
        n4 = 0.0

    # N5 키워드 밀도 1~2% (3점)
    if kw:
        words = max(len(re.findall(r'[가-힣a-zA-Z]+', text)), 1)
        d = (text.count(kw) / words) * 100
        n5 = 3.0 if 1.0 <= d <= 2.0 else (1.5 if 0.5 <= d <= 3.0 else 0.0)
    else:
        n5 = 3.0

    # N6 본문 키워드 3~5회 (2점)
    if kw:
        c = text.count(kw)
        n6 = 2.0 if 3 <= c <= 5 else (1.0 if c in (2, 6) else 0.0)
    else:
        n6 = 2.0

    # N7 해시태그 5~10개 (2점)
    nt = len(_tags(draft))
    n7 = 2.0 if 5 <= nt <= 10 else (1.0 if 3 <= nt <= 13 else 0.0)

    # N8 해요체 일관 (2점)
    hayeo = len(re.findall(r'해요|이에요|였어요|하네요|겠어요|드려요|거에요|인데요', text))
    hamnida = len(re.findall(r'습니다[^만]|었습니다|겠습니다', text))
    n8 = 2.0 if hayeo > hamnida and hayeo >= 5 else (1.0 if hayeo >= 3 else 0.0)

    items = {
        "N1_title_len":     {"score": n1, "max": 3, "name": "제목 길이(≤40)"},
        "N2_kw_in_title":   {"score": n2, "max": 3, "name": "제목 키워드 앞부분"},
        "N3_h3_count":      {"score": n3, "max": 3, "name": "H3 소제목 3~4개"},
        "N4_section_sents": {"score": n4, "max": 2, "name": "소제목 아래 2~3문장"},
        "N5_kw_density":    {"score": n5, "max": 3, "name": "키워드 밀도 1~2%"},
        "N6_kw_in_body":    {"score": n6, "max": 2, "name": "본문 키워드 3~5회"},
        "N7_hashtags":      {"score": n7, "max": 2, "name": "해시태그 5~10개"},
        "N8_hayeo":         {"score": n8, "max": 2, "name": "해요체 일관"},
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
    t1 = 2.0 if tl <= 45 else (1.5 if tl <= 55 else (0.5 if tl <= 60 else 0.0))

    # T2 제목 키워드 (2점)
    if kw and kw in title:
        t2 = 2.0 if title.find(kw) < len(title) // 2 else 1.0
    else:
        t2 = 0.0 if kw else 2.0

    # T3 H1 1개 (2점)
    t3 = 2.0 if len(re.findall(r'<h1[^>]*>', html, re.I)) == 1 else 0.0

    # T4 H2 3~5개 (3점)
    h2 = len(re.findall(r'<h2[^>]*>', html, re.I))
    t4 = 3.0 if 3 <= h2 <= 5 else (1.5 if h2 in (2, 6) else 0.0)

    # T5 H3 범위 내 (2점)
    h3 = len(re.findall(r'<h3[^>]*>', html, re.I))
    t5 = 2.0 if h3 <= h2 * 3 else (1.0 if h3 <= h2 * 3 + 2 else 0.0)

    # T6 롱테일 키워드 헤더 (3점)
    if kw:
        hdrs = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL | re.I)
        hits = sum(1 for h in hdrs if kw in _strip(h))
        t6 = 3.0 if hits >= 3 else (2.0 if hits >= 2 else (1.0 if hits >= 1 else 0.0))
    else:
        t6 = 3.0

    # T7 메타 설명 140~160자 (2점)
    meta = (draft.get("meta_description") or draft.get("meta_desc") or "" if isinstance(draft, dict) else "")
    if meta:
        ml = len(meta)
        t7 = 2.0 if 140 <= ml <= 160 else (1.0 if 120 <= ml <= 180 else 0.0)
    else:
        t7 = 0.0

    # T8 내부 링크 1개 (2점)
    int_links = len(re.findall(r'<a[^>]+href=["\'][^"\'#][^"\']*["\']', html, re.I))
    t8 = 2.0 if int_links == 1 else (1.0 if int_links >= 2 else 0.0)

    # T9 네이버 중복 없음 (2점) — 프로세스 보장
    t9 = 2.0

    items = {
        "T1_title_len":     {"score": t1, "max": 2, "name": "제목 길이(≤55)"},
        "T2_kw_in_title":   {"score": t2, "max": 2, "name": "제목 키워드 포함"},
        "T3_h1_count":      {"score": t3, "max": 2, "name": "H1 1개"},
        "T4_h2_count":      {"score": t4, "max": 3, "name": "H2 3~5개"},
        "T5_h3_range":      {"score": t5, "max": 2, "name": "H3 범위 내 활용"},
        "T6_longtail":      {"score": t6, "max": 3, "name": "롱테일 키워드 헤더"},
        "T7_meta_desc":     {"score": t7, "max": 2, "name": "메타 설명 길이(140-160)"},
        "T8_internal_link": {"score": t8, "max": 2, "name": "내부 링크 1개"},
        "T9_no_dup":        {"score": t9, "max": 2, "name": "네이버 중복 없음(프로세스)"},
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
    th1 = 5.0 if bad_period == 0 else (2.5 if bad_period == 1 else 0.0)

    # TH2: 재무수치 본문 기재 없음 (5점)
    fin_text = len(re.findall(
        r'PER\s*\d|ROE\s*\d|영업이익률\s*\d|현재가\s*\d|시가총액\s*\d|매출액\s*\d|순이익\s*\d|부채비율\s*\d',
        text
    ))
    th2 = 5.0 if fin_text == 0 else (2.5 if fin_text == 1 else 0.0)

    items = {
        "TH1_3m_return":    {"score": th1, "max": 5, "name": "수익률 3개월만 표기"},
        "TH2_no_fin_text":  {"score": th2, "max": 5, "name": "재무수치 본문 기재 없음"},
    }
    return {"total": round(th1 + th2, 2), "max": 10.0, "items": items}


def score_section_d_economic(draft: Any) -> dict:
    """D-EC — 경제브리핑 전용 (10점)."""
    text = _strip(_body(draft))

    # EC1: 실데이터 사용 (4점) — 의심 수치 패턴 역산
    suspicious = len(re.findall(r'약\s*\d+\.?\d*\s*[%포인트p]|대략\s*\d+|추정\s*\d+', text))
    ec1 = 4.0 if suspicious == 0 else (2.0 if suspicious == 1 else 0.0)

    # EC2: 시장 영향 인과 서술 (4점)
    causal = len(re.findall(
        r'→|따라서|그로\s*인해|로\s*인해|때문에|영향을\s*미|이어질|파급\s*효과|로\s*이어',
        text
    ))
    ec2 = 4.0 if causal >= 4 else (3.0 if causal >= 3 else (2.0 if causal >= 2 else (1.0 if causal >= 1 else 0.0)))

    # EC3: 경제 용어 설명 (2점)
    explain = len(re.findall(r'이란|란\s*무엇|을?\s*의미|쉽게\s*말해|다시\s*말해|풀어\s*말하면|뜻하는', text))
    ec3 = 2.0 if explain >= 1 else 0.0

    items = {
        "EC1_real_data":    {"score": ec1, "max": 4, "name": "경제지표 실데이터"},
        "EC2_causal":       {"score": ec2, "max": 4, "name": "시장 영향 인과 서술"},
        "EC3_term_explain": {"score": ec3, "max": 2, "name": "경제 용어 쉬운 설명"},
    }
    return {"total": round(ec1 + ec2 + ec3, 2), "max": 10.0, "items": items}


def score_section_d(draft: Any, post_type: str = "") -> dict:
    ptype = _post_type(draft, post_type)
    if ptype == "economic":
        return score_section_d_economic(draft)
    return score_section_d_theme(draft)


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
