"""JARVIS09_COLLECTOR/collector_engine.py — 수집 오케스트레이터."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from .models import RawDocument, CollectionResult
from .cleaner import clean_document
from .providers import (
    BlogProvider, NewsProvider, AcademicProvider,
    FinanceProvider, WebProvider, KorEconProvider,
    NaverNewsProvider, DartProvider, EcosProvider,
    KosisProvider, KrxProvider,
)

log = logging.getLogger("jarvis.collector.engine")

# 소스별 수집 한도 — provider마다 다르게 설정
_PROVIDER_LIMITS = {
    "naver_news": 20,  # 네이버 뉴스 API: 가장 정확한 한국어 뉴스
    "news":       15,  # Google News + 경제지 RSS
    "kor_econ":   10,  # 네이버 금융 + 전문 경제지
    "krx":         8,  # KRX 시장 통계 (키 불필요)
    "blog":        8,  # 네이버 블로그
    "web":         5,  # 위키 + 지식백과 + 다음
    "dart":        5,  # DART 전자공시
    "ecos":        3,  # 한국은행 거시경제 지표
    "kosis":       3,  # 통계청 산업 통계
    "finance":     3,  # yfinance 글로벌 지표
    "academic":    3,  # arxiv 논문
}

_PROVIDERS = [
    NaverNewsProvider(),   # 네이버 뉴스 API (키 있으면 최우선)
    NewsProvider(),        # Google News + 경제지 RSS
    KorEconProvider(),     # 네이버 금융 + 전문 경제지
    KrxProvider(),         # KRX 시장 통계 (키 불필요)
    BlogProvider(),        # 네이버 블로그
    WebProvider(),         # 위키 + 지식백과 + 다음
    DartProvider(),        # DART 전자공시 (키 필요)
    EcosProvider(),        # 한국은행 ECOS (키 필요)
    KosisProvider(),       # 통계청 KOSIS (키 필요)
    FinanceProvider(),     # yfinance 글로벌 지표
    AcademicProvider(),    # arxiv 논문
]
_MAX_WORKERS = 8   # 병렬 수집


def collect_for_theme(theme: str, sector: str = "") -> list[CollectionResult]:
    """주제·섹터에 맞는 전 소스 병렬 수집 → 정제 결과 반환.

    수집 소스: 뉴스(Google+한국경제지) + 한국경제전문 + 블로그 + 웹(위키+지식백과) + 금융지표 + 논문
    """
    log.info(f"[Engine] 수집 시작: theme='{theme}' sector='{sector}'")
    raw_docs: list[RawDocument] = []

    def _run_provider(prov):
        limit = _PROVIDER_LIMITS.get(prov.source_type, 8)
        try:
            docs = prov.collect(theme, sector, max_items=limit)
            log.info(f"[Engine] {prov.source_type} → {len(docs)}건 수집")
            return docs
        except Exception as e:
            log.warning(f"[Engine] {prov.source_type} 실패: {e}")
            return []

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as exe:
        futures = {exe.submit(_run_provider, p): p.source_type for p in _PROVIDERS}
        for fut in as_completed(futures):
            docs = fut.result()
            raw_docs.extend(docs)

    # 정제 + 중복 URL 제거
    seen_urls: set[str] = set()
    results = []
    for raw in raw_docs:
        if raw.url in seen_urls:
            continue
        seen_urls.add(raw.url)
        try:
            raw.extra["theme"] = raw.extra.get("theme") or theme
            cleaned = clean_document(raw)
            if cleaned.word_count >= 20:  # 20단어 이상만 (짧은 타이틀 제외)
                results.append(cleaned)
        except Exception as e:
            log.warning(f"[Engine] 정제 실패 ({raw.url}): {e}")

    # 소스 다양성 확보: 같은 source_type에서 너무 많이 몰리지 않도록 배분
    _per_source: dict[str, int] = {}
    _MAX_PER_SOURCE = 12
    balanced = []
    for r in results:
        src = r.source_type
        if _per_source.get(src, 0) < _MAX_PER_SOURCE:
            balanced.append(r)
            _per_source[src] = _per_source.get(src, 0) + 1

    log.info(f"[Engine] 수집 완료: 원본 {len(raw_docs)}건 → 정제 {len(results)}건 → 배분 {len(balanced)}건")
    return balanced
