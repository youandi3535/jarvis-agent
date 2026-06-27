"""JARVIS09_COLLECTOR/collector_engine.py — 수집 오케스트레이터."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from .models import RawDocument, CollectionResult
from .cleaner import clean_document

try:
    from JARVIS07_GUARDIAN.error_collector import auto_catch as _auto_catch
except ImportError:
    import functools
    class _auto_catch:  # type: ignore[no-redef]
        def __init__(self, *a, **kw): pass
        def __call__(self, fn): return fn
        def __enter__(self): return self
        def __exit__(self, *a): return False
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


@_auto_catch("collector", reraise=True)
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


# ── delta-aware 교류 프로토콜 (★ 사용자 박제 2026-06-07) ────────────────
# JARVIS06 등 호출자가 이미 가진 doc fingerprint(content_hash)를 제외하고
# *신규/갱신분만* 수령할 수 있도록 한 진입점. 단일 진입점 원칙은 그대로 —
# 호출자는 yfinance/requests 직접 호출 금지. 단, collect_for_theme*만 자유.

# aspect → 우선 노출할 source_type 화이트리스트
_ASPECT_SOURCES = {
    "scene_context":  {"naver_news", "news", "blog", "web"},          # 사진·배경 컨텍스트
    "numeric_facts":  {"dart", "ecos", "kosis", "krx", "finance",
                       "kor_econ"},                                    # 차트·수치
    "mixed":          None,  # 전체
}


def collect_for_theme_delta(
    theme: str,
    sector: str = "",
    exclude_hashes: list[str] | set[str] | None = None,
    aspect: str | None = None,
) -> dict:
    """delta-aware 수집 — 이미 가진 hash 제외하고 신규/갱신분만 반환.

    Args:
        theme:          수집 키워드
        sector:         섹터 힌트
        exclude_hashes: 호출자가 이미 보유한 content_hash 목록
        aspect:         "scene_context" | "numeric_facts" | "mixed" | None
                        None = mixed (전체)

    Returns:
        {
            "status":  "no_change" | "fresh",
            "added":   list[CollectionResult],  # exclude 제외 + aspect 매칭
            "version": float (epoch ts),
            "aspect":  aspect or "mixed",
            "total_pool": int,                  # 필터링 전 전체 수집량
        }
    """
    import time as _t
    excl: set[str] = set(exclude_hashes or [])
    aspect_key = aspect or "mixed"
    allow_src = _ASPECT_SOURCES.get(aspect_key)

    # 전체 수집 (기존 collect_for_theme 재사용)
    pool = collect_for_theme(theme, sector=sector)

    # aspect 필터링
    if allow_src is not None:
        pool_filtered = [d for d in pool if d.source_type in allow_src]
    else:
        pool_filtered = pool

    # exclude_hashes 제외
    added = [d for d in pool_filtered if d.content_hash not in excl]

    status = "no_change" if not added else "fresh"
    log.info(
        f"[Engine/delta] theme='{theme}' aspect={aspect_key} "
        f"pool={len(pool)} filtered={len(pool_filtered)} added={len(added)} status={status}"
    )
    return {
        "status":     status,
        "added":      added,
        "version":    _t.time(),
        "aspect":     aspect_key,
        "total_pool": len(pool),
    }
