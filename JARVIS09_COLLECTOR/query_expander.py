"""JARVIS09 쿼리 확장기 — 키워드 패키지 → 소스별 최적화 쿼리 생성.

JARVIS03에서 받은 복합 주제("미국 관세 인상 한국 수출 영향")를 각 데이터 소스가
잘 찾을 수 있는 형태로 변환한다. LLM 호출 0회, 규칙 기반.

핵심 원칙:
  - 뉴스 API: 자연어 구, 다양한 표현
  - KOSIS/통계: 공식 통계 용어 (섹터 매핑)
  - Wikipedia: 짧은 단일 명사/고유명사
  - DART: 관련 대기업명 힌트
  - 영문 소스: 핵심어 영문 번역
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict

# ─── 섹터 → 분야별 KOSIS 통계 용어 매핑 ───────────────────────
_SECTOR_KOSIS: Dict[str, List[str]] = {
    "무역":     ["수출금액", "수입금액", "무역수지", "수출입현황"],
    "수출":     ["수출금액", "수출증가율", "수출입현황"],
    "금융":     ["은행대출", "가계부채", "통화량"],
    "은행":     ["은행대출", "예금잔액", "가계부채"],
    "부동산":   ["아파트매매가격지수", "주택가격", "부동산가격지수"],
    "고용":     ["취업자", "실업률", "고용률", "경제활동인구"],
    "소비":     ["소비자물가", "소매판매", "소비지출"],
    "경기":     ["경제성장률", "경기동향지수", "산업생산지수"],
    "반도체":   ["반도체 수출", "반도체 생산지수"],
    "자동차":   ["자동차 생산", "자동차 수출"],
    "에너지":   ["에너지 수입금액", "원유 수입"],
    "물가":     ["소비자물가지수", "생산자물가지수", "수입물가"],
    "금리":     ["기준금리", "시장금리"],
    "환율":     ["원달러환율", "환율"],
}

# 테마 키워드 → KOSIS 용어 힌트 (섹터 없을 때 주제어 기반 유추)
_THEME_KOSIS_HINTS: Dict[str, List[str]] = {
    "관세": ["수출금액", "수입금액", "무역수지"],
    "수출": ["수출금액", "수출증가율"],
    "수입": ["수입금액", "수입증가율"],
    "환율": ["원달러환율"],
    "물가": ["소비자물가지수"],
    "고용": ["취업자", "실업률"],
    "부동산": ["아파트매매가격지수"],
    "금리": ["기준금리"],
}

# 섹터 → Wikipedia 명확한 경제 문서 목록 (단어 조합이 모호한 경우 대비 고정 매핑)
_SECTOR_WIKI: Dict[str, List[str]] = {
    "무역":     ["자유무역협정", "보호무역주의", "무역수지"],
    "수출":     ["수출", "무역수지"],
    "금융":     ["금융시장", "중앙은행"],
    "은행":     ["상업은행", "금융시장"],
    "부동산":   ["부동산 거품", "주택 가격"],
    "고용":     ["실업률", "경제활동인구"],
    "소비":     ["소비자물가지수"],
    "경기":     ["경기순환", "경제성장"],
    "반도체":   ["반도체", "집적 회로"],
    "자동차":   ["자동차 산업"],
    "에너지":   ["에너지 정책"],
    "물가":     ["인플레이션", "소비자물가지수"],
    "금리":     ["기준금리", "금리"],
    "환율":     ["환율", "외환시장"],
}

# 대표 뉴스 표현 변형 (테마 키워드 → 뉴스 검색에 유리한 표현)
_NEWS_EXPANSIONS: Dict[str, List[str]] = {
    "관세":   ["관세 인상", "관세 전쟁", "트럼프 관세"],
    "수출":   ["수출 실적", "수출 감소", "수출 영향"],
    "금리":   ["금리 인상", "기준금리"],
    "환율":   ["원화 약세", "달러 강세", "환율 변동"],
    "반도체": ["반도체 산업", "반도체 수출"],
    "부동산": ["집값", "아파트 매매", "부동산 시장"],
    "인플레이션": ["물가 상승", "인플레"],
    "경기침체": ["경기 둔화", "불황"],
}


@dataclass
class ExpandedQueries:
    """소스별로 최적화된 쿼리 세트."""
    theme: str                          # 원본 테마 (항상 포함)
    news_queries: List[str] = field(default_factory=list)   # 뉴스 API용 (2~5개)
    kosis_terms: List[str] = field(default_factory=list)    # KOSIS 통계 검색어
    wiki_terms: List[str] = field(default_factory=list)     # Wikipedia (2~3단어)
    core_keywords: List[str] = field(default_factory=list)  # 핵심어 목록 (dedup 기준)


def expand(theme: str, sector: str = "", profile: dict | None = None) -> ExpandedQueries:
    """키워드 패키지 → 소스별 최적화 쿼리 생성. LLM 호출 0회.

    Args:
        theme:   JARVIS03이 보낸 주제 (예: "미국 관세 인상 한국 수출 영향")
        sector:  섹터 (예: "무역", "반도체")
        profile: JARVIS03 keyword_profile (엔티티 유형·관련어 포함 시 활용)
    """
    words = [w for w in theme.split() if len(w) >= 2]
    eq = ExpandedQueries(theme=theme)

    # ── 핵심어 추출 ────────────────────────────────────────────
    # 명사성 단어 우선 (조사·동사성 제외), 2~4글자 단어
    _stop = {"이상", "이하", "관련", "영향", "현황", "동향", "분석",
             "전망", "최근", "문제", "상황", "변화", "대비", "위한"}
    eq.core_keywords = [w for w in words if w not in _stop][:6]

    # ── 뉴스 쿼리 ─────────────────────────────────────────────
    # 1) 원본 그대로
    eq.news_queries.append(theme)

    # 2) 앞 3단어 조합 (복잡한 구문 → 단순화)
    if len(words) > 3:
        eq.news_queries.append(" ".join(words[:3]))

    # 3) 핵심어 조합 (stop words 제거 후 앞 2단어)
    core2 = eq.core_keywords[:2]
    if len(core2) >= 2:
        pair = " ".join(core2)
        if pair not in eq.news_queries:
            eq.news_queries.append(pair)

    # 4) 키워드별 뉴스 확장 표현
    for w in eq.core_keywords[:3]:
        for hint_kw, expansions in _NEWS_EXPANSIONS.items():
            if hint_kw in w or w in hint_kw:
                for exp in expansions[:2]:
                    if exp not in eq.news_queries:
                        eq.news_queries.append(exp)

    # 5) 섹터 추가 구문 (섹터가 독립 의미일 때)
    if sector and sector not in theme:
        eq.news_queries.append(f"{sector} {core2[0]}" if core2 else sector)

    # ── KOSIS 통계 용어 ────────────────────────────────────────
    # 섹터 우선
    if sector in _SECTOR_KOSIS:
        eq.kosis_terms.extend(_SECTOR_KOSIS[sector])
    # 테마 키워드 기반 유추
    for w in eq.core_keywords:
        for hint_kw, terms in _THEME_KOSIS_HINTS.items():
            if hint_kw in w:
                for t in terms:
                    if t not in eq.kosis_terms:
                        eq.kosis_terms.append(t)
    eq.kosis_terms = list(dict.fromkeys(eq.kosis_terms))[:6]  # dedup + 최대 6개

    # ── Wikipedia 검색어 ───────────────────────────────────────
    # 섹터 기반 고정 매핑 우선 (모호한 단어 조합보다 신뢰성 높음)
    if sector in _SECTOR_WIKI:
        eq.wiki_terms = list(_SECTOR_WIKI[sector][:3])
    else:
        # 매핑 없는 섹터: 핵심어에서 명확한 도메인 단어만 사용
        _wiki_too_broad = {"미국", "한국", "중국", "일본", "세계", "글로벌", "국제",
                           "최근", "현재", "올해", "작년", "관세", "인상"}
        _wiki_candidates = [w for w in eq.core_keywords if w not in _wiki_too_broad]
        eq.wiki_terms = _wiki_candidates[:2]
        if len(_wiki_candidates) >= 2:
            combo = " ".join(_wiki_candidates[:2])
            if combo not in eq.wiki_terms:
                eq.wiki_terms.append(combo)

    # ── 프로필 활용 (JARVIS03이 관련어 포함 시) ────────────────
    if profile:
        related = profile.get("related_terms", []) or profile.get("entities", [])
        for r in related[:3]:
            r = str(r).strip()
            if r and r not in eq.news_queries:
                eq.news_queries.append(r)

    return eq


def news_queries_for(eq: ExpandedQueries, max_n: int = 4) -> list[str]:
    """뉴스 쿼리 목록 (중복 제거, max_n개)."""
    seen, out = set(), []
    for q in eq.news_queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
        if len(out) >= max_n:
            break
    return out


def wiki_queries_for(eq: ExpandedQueries) -> list[str]:
    """Wikipedia 검색 용어 목록 (섹터 기반 고정 매핑 우선)."""
    seen, out = set(), []
    for q in eq.wiki_terms:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:4]


__all__ = ["ExpandedQueries", "expand", "news_queries_for", "wiki_queries_for"]
