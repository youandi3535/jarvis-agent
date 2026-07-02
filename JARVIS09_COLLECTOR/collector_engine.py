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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ★ 설계-우선 리서치 수집 — collect_research (ADR 012, 사용자 박제 2026-07-02)
#
#  "항상 설계를 먼저 하고 그 설계대로 수집한다. 부족하면 더 받아온다."
#
#  흐름: ① plan_research(설계) → ② 광역 스윕 ∥ 질문별 조준 수집(프로바이더+웹발견)
#        → ③ 얇은 문서 전문 딥페치 → ④ EvidencePack 추출·커버리지 측정
#        → ⑤ 미충족 질문만 2라운드 재수집(변형 쿼리+discover) → ⑥ 박제·반환
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROVIDER_BY_TYPE = {p.source_type: p for p in _PROVIDERS}
_TARGET_LIMIT_PER_Q = 4          # 질문·출처당 조준 수집 상한
_DEEPFETCH_MAX = 8               # 전문 딥페치 상한 (시간 가드)
_DEEPFETCH_MIN_WORDS = 90        # 이보다 짧으면 스니펫 수준 → 전문 시도
_DEEPFETCH_TYPES = {"news", "naver_news", "web", "kor_econ", "blog"}


def _collect_for_question(question: dict, theme: str, sector: str) -> list[RawDocument]:
    """설계된 질문 1개 → 지정 출처 조준 수집 (+discover 는 웹 발견·범용 fetch)."""
    docs: list[RawDocument] = []
    queries = question.get("queries") or [theme]
    q_main = queries[0]
    for src in (question.get("sources") or [])[:3]:
        try:
            if src == "discover":
                from .discovery import web_search
                from .generic_fetch import fetch_documents
                hits = web_search(q_main, max_results=6)
                docs.extend(fetch_documents(hits, theme=theme, max_docs=3))
                continue
            prov = _PROVIDER_BY_TYPE.get(src)
            if prov is None:
                continue
            got = prov.collect(q_main, sector, max_items=_TARGET_LIMIT_PER_Q)
            for d in got:
                d.extra.setdefault("question_id", question.get("id", ""))
            docs.extend(got)
        except Exception as e:
            log.debug(f"[research] 질문 {question.get('id')} 소스 {src} 실패: {e}")
    return docs


def _deep_fetch_thin_docs(results: list[CollectionResult], theme: str) -> list[CollectionResult]:
    """스니펫 수준(짧은) 뉴스·웹 문서 → 기사 전문으로 확장 (근거 밀도↑)."""
    from .generic_fetch import fetch_article
    expanded = 0
    for r in results:
        if expanded >= _DEEPFETCH_MAX:
            break
        if r.source_type not in _DEEPFETCH_TYPES or r.word_count >= _DEEPFETCH_MIN_WORDS:
            continue
        if not (r.url or "").startswith("http"):
            continue
        try:
            raw = fetch_article(r.url, theme=theme, title=r.title, source_type=r.source_type)
            if raw is None:
                continue
            full = clean_document(raw)
            if full.word_count > r.word_count * 2:
                r.cleaned_text = full.cleaned_text
                r.word_count = full.word_count
                r.meta["deep_fetched"] = True
                expanded += 1
        except Exception as e:
            log.debug(f"[research] 딥페치 실패({r.url}): {e}")
    if expanded:
        log.info(f"[research] 전문 딥페치 {expanded}건 — 스니펫 → 기사 본문 확장")
    return results


def _clean_raw_docs(raw_docs: list[RawDocument], theme: str,
                    seen_urls: set[str]) -> list[CollectionResult]:
    out = []
    for raw in raw_docs:
        if not raw or raw.url in seen_urls:
            continue
        seen_urls.add(raw.url)
        try:
            raw.extra["theme"] = raw.extra.get("theme") or theme
            cleaned = clean_document(raw)
            if cleaned.word_count >= 20:
                if raw.extra.get("question_id"):
                    cleaned.meta["question_id"] = raw.extra["question_id"]
                out.append(cleaned)
        except Exception as e:
            log.debug(f"[research] 정제 실패({raw.url}): {e}")
    return out


@_auto_catch("collector", reraise=True)
def collect_research(theme: str, sector: str = "", angle: str = "",
                     max_rounds: int = 2) -> dict:
    """설계-우선 리서치 수집 — 광역 스윕 + 질문별 조준 수집 + 갭 재수집 순환.

    Returns:
        {"evidence_pack": dict,          # evidence_pack.build_evidence_pack 산출물
         "docs": list[CollectionResult], # 전체 정제 문서 (JARVIS06·prepublish 용)
         "plan": dict,                   # research_planner 설계도
         "evidence_path": str}           # 박제 JSON 경로
    """
    from .research_planner import plan_research
    from .evidence_pack import (build_evidence_pack, coverage_gaps, merge_pack,
                                persist_evidence, _extract_facts_batch)

    log.info(f"[research] 설계-우선 수집 시작: theme='{theme}'")

    # ① 설계
    plan = plan_research(theme, sector=sector, angle=angle)

    # ①-b 키 누락 소스 감지 → 텔레그램 온보딩 안내 (fail-open)
    try:
        from .source_onboarding import check_and_notify
        check_and_notify(plan)
    except Exception:
        pass

    # ② 광역 스윕(기존 collect_for_theme) ∥ 질문별 조준 수집
    seen_urls: set[str] = set()
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as exe:
        broad_fut = exe.submit(collect_for_theme, theme, sector)
        q_futs = {exe.submit(_collect_for_question, q, theme, sector): q["id"]
                  for q in plan.get("questions", [])}
        targeted_raw: list[RawDocument] = []
        for fut in as_completed(q_futs):
            try:
                targeted_raw.extend(fut.result() or [])
            except Exception as e:
                log.debug(f"[research] 질문 {q_futs[fut]} 수집 실패: {e}")
        try:
            broad_docs = broad_fut.result() or []
        except Exception as e:
            log.warning(f"[research] 광역 스윕 실패: {e}")
            broad_docs = []

    for d in broad_docs:
        seen_urls.add(d.url)
    all_docs: list[CollectionResult] = list(broad_docs)
    all_docs.extend(_clean_raw_docs(targeted_raw, theme, seen_urls))

    # ③ 얇은 문서 전문 딥페치
    all_docs = _deep_fetch_thin_docs(all_docs, theme)

    # ④ 근거 팩 추출 + 커버리지 측정
    pack = build_evidence_pack(theme, plan, all_docs)

    # ⑤ 갭 재수집 순환 — 미충족 질문만, 변형 쿼리 + discover 강제
    rounds = 1
    while rounds < max_rounds:
        gaps = coverage_gaps(pack)
        if not gaps:
            break
        rounds += 1
        log.info(f"[research] 커버리지 미충족 {len(gaps)}개 질문 → {rounds}라운드 재수집")
        gap_raw: list[RawDocument] = []
        with ThreadPoolExecutor(max_workers=4) as exe:
            futs = []
            for q in gaps:
                q2 = dict(q)
                queries = list(q.get("queries") or [theme])
                q2["queries"] = queries[1:] + queries[:1]          # 변형 쿼리 우선
                srcs = [s for s in (q.get("sources") or []) if s != "discover"]
                q2["sources"] = (["discover"] + srcs)[:3]          # discover 강제 선두
                futs.append(exe.submit(_collect_for_question, q2, theme, sector))
            for fut in as_completed(futs):
                try:
                    gap_raw.extend(fut.result() or [])
                except Exception:
                    pass
        gap_docs = _clean_raw_docs(gap_raw, theme, seen_urls)
        if not gap_docs:
            log.info("[research] 재수집 신규 문서 0건 — 순환 종료")
            break
        gap_docs = _deep_fetch_thin_docs(gap_docs, theme)
        all_docs.extend(gap_docs)
        extra_facts = _extract_facts_batch(theme, plan, gap_docs[:8])
        pack = merge_pack(pack, extra_facts)

    # ⑥ 박제 + 반환
    path = persist_evidence(pack)
    cov = pack.get("coverage") or {}
    log.info(f"[research] 완료: 문서 {len(all_docs)}건 · fact {len(pack.get('facts', []))}개 "
             f"· 커버리지 {sum(1 for c in cov.values() if c.get('ok'))}/{len(cov)} "
             f"· 라운드 {rounds}")
    return {"evidence_pack": pack, "docs": all_docs, "plan": plan,
            "evidence_path": path}


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
