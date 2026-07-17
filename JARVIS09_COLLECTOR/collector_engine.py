"""JARVIS09_COLLECTOR/collector_engine.py — 수집 오케스트레이터."""
from __future__ import annotations

import logging
import os as _os
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as _FutureTimeout
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
    BlogProvider, NewsProvider,
    FinanceProvider, WebProvider, KorEconProvider,
    NaverNewsProvider, DartProvider, EcosProvider,
    KosisProvider, KrxProvider, BokProvider,
    CustomsProvider, KofiaProvider, FssProvider,
    MlitProvider, EmploymentProvider,
)

log = logging.getLogger("jarvis.collector.engine")

# ★ 수집 풍부 원칙 (사용자 박제 2026-07-03 ×2 — ADR 013 / ERRORS [314]):
#   "주제가 설정되면 그 주제에 맞는 정보는 싹다 받아버려, 제한 두지 말고."
#   "데이터가 부족해서 이미지를 생성 못하는 상황을 만들지 마라. 데이터는 충분해야 해."
#   아래 상한은 무한루프 방지용 안전망일 뿐 — 신뢰순 *선별* 은 사용 시점(주입·검증)에.
_PROVIDER_LIMITS = {
    "naver_news": 30,  # 네이버 뉴스 API: 가장 정확한 한국어 뉴스
    "news":       25,  # Google News + 경제지 RSS
    "kor_econ":   15,  # 네이버 금융 + 전문 경제지
    "krx":          20,  # KRX 시장 통계 (Tier 2 API)
    "dart":         20,  # DART 전자공시 (Tier 2 API)
    "ecos":         20,  # 한국은행 거시경제 지표 (Tier 2 API)
    "kosis":        20,  # 통계청 산업 통계 (Tier 2 API)
    "finance":      15,  # yfinance 글로벌 지표 (Tier 2 API)
    "bok_official": 10,  # 한국은행 기준금리·환율·CPI (Tier 2 API)
    "customs":     10,  # 관세청 수출입 통계 (Tier 2 API)
    "kofia":        8,  # 금융투자협회 채권 수익률 (Tier 2 API)
    "fss":          8,  # 금융감독원 금융통계 (Tier 2 API)
    "mlit":         8,  # 국토교통부 부동산 통계 (Tier 2 API)
    "employment":  10,  # 고용노동부 고용통계 (Tier 2 API)
    "blog":       10,  # 네이버 블로그
    "web":        10,  # 위키 + 지식백과 + 다음
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
    BokProvider(),         # 한국은행 기준금리·달러/원·CPI 공식 지표
    CustomsProvider(),     # 관세청 수출입 통계 (KOSIS 경유)
    KofiaProvider(),       # 금융투자협회 채권·국고채 수익률 (ECOS 경유)
    FssProvider(),         # 금융감독원 금융통계 (금융 주제 전용)
    MlitProvider(),        # 국토교통부 부동산 통계 (부동산 주제 전용)
    EmploymentProvider(),  # 고용노동부 고용통계 (KOSIS 경유)
]
_MAX_WORKERS = 8   # 병렬 수집


@_auto_catch("collector", reraise=True)
def collect_for_theme(theme: str, sector: str = "") -> list[CollectionResult]:
    """주제·섹터에 맞는 전 소스 병렬 수집 → 정제 결과 반환.

    수집 소스: 뉴스(Google+한국경제지) + 한국경제전문 + 블로그 + 웹(위키+지식백과) + 금융지표 + 논문
    """
    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j09", f"{theme[:12]} 수집", ttl=600)   # 안전망 10분 — 실소요 기준 축소
    except Exception:
        pass
    # busy 신호 수명 = 함수 수명 — 종료(성공·실패) 시 finally 에서 즉시 해제 (근본 수정 2026-07-16)
    try:
        log.info(f"[Engine] 수집 시작: theme='{theme}' sector='{sector}'")
        raw_docs: list[RawDocument] = []

        def _run_provider(prov):
            # ★ 수집 폭 배율 (사용자 박제 2026-07-03 — ADR 013): "제한을 두지 말고 최대한
            #   많은 진실성 있는 데이터를 전부" — 프로바이더별 상한에 배율. env 튜닝.
            limit = int(_PROVIDER_LIMITS.get(prov.source_type, 8)
                        * max(1.0, float(_os.getenv("J09_BREADTH", "3.0") or "3.0")))
            try:
                docs = prov.collect(theme, sector, max_items=limit)
                log.info(f"[Engine] {prov.source_type} → {len(docs)}건 수집")
                return docs
            except Exception as e:
                log.warning(f"[Engine] {prov.source_type} 실패: {e}")
                return []

        try:
            from JARVIS00_INFRA.watchdog import beat  # 지역 import (순환 방지)
        except Exception:
            def beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)
        # ★ shutdown(wait=False): 타임아웃된 프로바이더 스레드를 버리고 즉시 진행
        #   (yfinance 등 무한 hang 방지 — ERRORS [401])
        exe = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        futures = {exe.submit(_run_provider, p): p.source_type for p in _PROVIDERS}
        try:
            for fut in as_completed(futures, timeout=90):  # 전체 90초 상한
                beat()
                try:
                    docs = fut.result(timeout=30)  # 개별 프로바이더 30초 상한
                except _FutureTimeout:
                    ptype = futures.get(fut, "unknown")
                    log.warning(f"[Engine] {ptype} 30초 타임아웃 — 스킵")
                    docs = []
                except Exception as e:
                    log.warning(f"[Engine] 프로바이더 결과 취합 실패: {e}")
                    docs = []
                raw_docs.extend(docs)
        except _FutureTimeout:
            log.warning("[Engine] 전체 수집 90초 초과 — 수집된 데이터만 사용")
        finally:
            exe.shutdown(wait=False)  # 잔여 스레드 백그라운드로 버림

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

        # ★ 신뢰 우선 정렬 + 동일 내용 중복 시 고신뢰 소스 유지 (사용자 박제 2026-07-03 — ADR 013)
        #   "논문 > API > 뉴스 > 기사 > 웹 — 겹치면 이 순서로 선택. 수집 자체는 전부."
        from .models import trust_rank as _trust
        results.sort(key=lambda r: _trust(r.source_type))   # stable — 티어 내 원래 순서 보존
        _seen_hash: set[str] = set()
        _uniq: list = []
        for r in results:
            h = getattr(r, "content_hash", "") or ""
            if h and h in _seen_hash:
                continue    # 동일 내용 — 앞선(더 신뢰 높은) 소스가 이미 보존됨
            if h:
                _seen_hash.add(h)
            _uniq.append(r)
        results = _uniq

        # 소스 다양성 확보: 같은 source_type에서 너무 많이 몰리지 않도록 배분
        _per_source: dict[str, int] = {}
        # ★ 30 → 100 상향 (풍부 원칙 [314] — 이미 받은 데이터 절삭 금지, 사실상 무제한)
        _MAX_PER_SOURCE = int(_os.getenv("J09_MAX_PER_SOURCE", "100") or "100")
        balanced = []
        for r in results:
            src = r.source_type
            if _per_source.get(src, 0) < _MAX_PER_SOURCE:
                balanced.append(r)
                _per_source[src] = _per_source.get(src, 0) + 1

        log.info(f"[Engine] 수집 완료: 원본 {len(raw_docs)}건 → 정제 {len(results)}건 → 배분 {len(balanced)}건")
        return balanced
    finally:
        # 작업 종료 — busy 즉시 해제 (해제 실패는 조용히 무시, TTL 은 안전망으로 잔존)
        try:
            from shared.pipeline_activity import clear_busy as _cb
            _cb("j09")
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ★ 설계-우선 리서치 수집 — collect_research (ADR 012, 사용자 박제 2026-07-02)
#
#  "항상 설계를 먼저 하고 그 설계대로 수집한다. 부족하면 더 받아온다."
#
#  흐름: ① 티어순 광역 수집(_collect_tier — 논문>API>뉴스>기사>웹, 신뢰순위) + discover 웹발견
#        → ② 얇은 문서 전문 딥페치 → ③ EvidencePack 추출·커버리지 측정
#        → ④ 미충족 시 2라운드 재수집(변형 쿼리+discover) → ⑤ 박제·반환
#  (구 plan_research 설계-LLM·질문별 조준수집은 2026-07-11 _collect_tier 재작성으로 폐지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROVIDER_BY_TYPE = {p.source_type: p for p in _PROVIDERS}


SOURCE_CATEGORIES = ["blog", "news", "academic", "finance", "web"]
"""수집 소스 카테고리 (표시 SSOT — 대시보드가 이 목록·개수에서 파생)."""


def list_provider_names() -> list[str]:
    """등록된 수집 프로바이더 이름 목록 (표시용 SSOT — 사용자 박제 2026-07-04).

    프로바이더를 추가/제거하면 텔레그램 /status·대시보드 표시가 자동으로 따라온다.
    """
    return [p.source_type for p in _PROVIDERS]


_DEEPFETCH_MAX = 8               # 전문 딥페치 상한 (시간 가드)
_DEEPFETCH_MIN_WORDS = 90        # 이보다 짧으면 스니펫 수준 → 전문 시도
_DEEPFETCH_TYPES = {"news", "naver_news", "web", "kor_econ", "blog"}


# (_collect_for_question·_TARGET_LIMIT_PER_Q 제거 — collect_research 가 _collect_tier 방식으로
#  재작성되며 질문별 조준수집 경로 폐지, 호출 0: 전수감사 DELETE[18])


def _deep_fetch_thin_docs(results: list[CollectionResult], theme: str) -> list[CollectionResult]:
    """스니펫 수준(짧은) 뉴스·웹 문서 → 기사 전문으로 확장 (근거 밀도↑)."""
    from .generic_fetch import fetch_article
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)
    expanded = 0
    for r in results:
        if expanded >= _DEEPFETCH_MAX:
            break
        if r.source_type not in _DEEPFETCH_TYPES or r.word_count >= _DEEPFETCH_MIN_WORDS:
            continue
        if not (r.url or "").startswith("http"):
            continue
        _wd_beat()   # ★ 순차 전문 딥페치(최대 8건) 진행 신호 — freeze 오탐 방지
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


def select_by_trust_quota(docs: list[CollectionResult],
                          budget: int | None = None) -> list[CollectionResult]:
    """★ 신뢰 서열 쿼터 선별 (사용자 박제 2026-07-06 v2 — "인포그래픽 만들 만큼 총 15개").

    논문 최대 3 · API 최대 7 · 나머지 5(소스별 1개씩), 총 `budget`(기본 15)개.
    상위 티어 미달분은 다음 티어로 이월(cascade):
      논문 2개 → API 8개까지 / 논문 0개 → API 10개까지 /
      논문·API 모두 0 → 나머지에서 budget 전부.
    나머지는 source_type 라운드로빈(각 1개씩 우선)으로 다양성 확보.
    env: J09_QUOTA_BUDGET(총량)·J09_PAPER_CAP·J09_API_CAP 로 튜닝.
    """
    from .models import (quota_group, trust_rank,
                         COLLECT_QUOTA_BUDGET, COLLECT_PAPER_CAP, COLLECT_API_CAP)
    budget = int(_os.getenv("J09_QUOTA_BUDGET", str(budget or COLLECT_QUOTA_BUDGET))
                 or COLLECT_QUOTA_BUDGET)
    paper_cap = int(_os.getenv("J09_PAPER_CAP", str(COLLECT_PAPER_CAP)) or COLLECT_PAPER_CAP)
    api_cap = int(_os.getenv("J09_API_CAP", str(COLLECT_API_CAP)) or COLLECT_API_CAP)

    groups: dict[str, list] = {"paper": [], "api": [], "rest": []}
    for d in docs:
        groups[quota_group(d.source_type)].append(d)
    # 각 그룹 내부: 신뢰 높은 소스 우선 (stable — 티어 내 원래 관련도 순서 보존)
    for g in groups.values():
        g.sort(key=lambda r: trust_rank(r.source_type))

    selected: list[CollectionResult] = []
    remaining = budget

    # 논문: 최대 paper_cap
    take = groups["paper"][:min(paper_cap, remaining)]
    selected += take
    remaining -= len(take)

    # API: 최대 api_cap + 논문 미달분 이월
    api_allow = api_cap + (paper_cap - len(take))
    take_api = groups["api"][:min(api_allow, remaining)]
    selected += take_api
    remaining -= len(take_api)

    # 나머지: 라운드로빈(각 source_type 1개씩) — 남은 예산 전부 (논문·API 미달분 자동 이월)
    if remaining > 0 and groups["rest"]:
        by_src: dict[str, list] = {}
        for d in groups["rest"]:   # 이미 trust 정렬됨 → 삽입 순서가 신뢰 순
            by_src.setdefault(d.source_type, []).append(d)
        while remaining > 0 and any(by_src.values()):
            for lst in by_src.values():
                if remaining <= 0:
                    break
                if lst:
                    selected.append(lst.pop(0))
                    remaining -= 1

    log.info(f"[quota] 신뢰 쿼터 선별: 논문 {len(take)} · API {len(take_api)} · "
             f"나머지 {len(selected) - len(take) - len(take_api)} = 총 {len(selected)}건 "
             f"(후보 {len(docs)}건 중, 예산 {budget})")
    return selected


def _collect_tier(provs: list, theme: str, sector: str, cap: int,
                  seen_urls: set | None = None) -> list[CollectionResult]:
    """티어 내 프로바이더 병렬 수집 → cap 개 이하 반환 (신뢰 순 정렬).

    ★ 처음부터 cap 적용 (ERRORS [423]): 광역수집 후 절삭 방식 폐지.
    각 프로바이더 max_items = min(자체 상한, cap) → 티어 전체 합계도 cap 이하.
    """
    if cap <= 0 or not provs:
        return []
    if seen_urls is None:
        seen_urls = set()
    from .models import trust_rank as _trust
    try:
        from JARVIS00_INFRA.watchdog import beat  # 지역 import (순환 방지)
    except Exception:
        def beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)

    raw_docs: list[RawDocument] = []

    def _run(prov):
        limit = min(_PROVIDER_LIMITS.get(prov.source_type, 8), cap)
        try:
            docs = prov.collect(theme, sector, max_items=limit)
            log.info(f"[tier] {prov.source_type} → {len(docs)}건")
            return docs
        except Exception as e:
            log.warning(f"[tier] {prov.source_type} 실패: {e}")
            return []

    exe = ThreadPoolExecutor(max_workers=min(len(provs), _MAX_WORKERS))
    futures = {exe.submit(_run, p): p.source_type for p in provs}
    try:
        for fut in as_completed(futures, timeout=90):
            beat()   # ★ 프로바이더 결과 취합마다 진행 신호 (ERRORS [394]/[426] 동일 클래스)
            try:
                raw_docs.extend(fut.result(timeout=30) or [])
            except _FutureTimeout:
                log.warning(f"[tier] {futures.get(fut)} 30초 타임아웃 — 스킵")
            except Exception as e:
                log.warning(f"[tier] 결과 취합 실패: {e}")
    except _FutureTimeout:
        log.warning("[tier] 전체 90초 초과 — 수집된 데이터만 사용")
    finally:
        exe.shutdown(wait=False)

    results = []
    for raw in raw_docs:
        if raw.url in seen_urls:
            continue
        seen_urls.add(raw.url)
        try:
            raw.extra["theme"] = raw.extra.get("theme") or theme
            cleaned = clean_document(raw)
            if cleaned.word_count >= 20:
                results.append(cleaned)
        except Exception:
            pass

    results.sort(key=lambda r: _trust(r.source_type))
    return results[:cap]  # ★ 티어 상한 강제


@_auto_catch("collector", reraise=True)
def collect_research(theme: str, sector: str = "", angle: str = "",
                     max_rounds: int = 3) -> dict:
    """★ 티어순 상한 수집 (사용자 박제 2026-07-11 — ERRORS [423]):
    처음부터 논문 최대 3·API 최대 7·나머지 최대 5, cascade 이월.
    광역수집 후 절삭 방식 완전 폐지 — 각 티어가 수집 시점에 상한 적용.

    Returns:
        {"docs": list[CollectionResult],  # 신뢰순 최대 15개 원시 문서
         "plan": dict}                    # 빈 dict (설계 LLM 제거 — _collect_tier가 plan 미사용)
    """
    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j09", f"{theme[:12]} 리서치", ttl=600)   # 안전망 10분 — 실소요 기준 축소
    except Exception:
        pass
    # busy 신호 수명 = 함수 수명 — 종료(성공·실패) 시 finally 에서 즉시 해제 (근본 수정 2026-07-16)
    try:
        from .models import (quota_group,
                             COLLECT_QUOTA_BUDGET, COLLECT_PAPER_CAP, COLLECT_API_CAP)

        paper_cap = int(_os.getenv("J09_PAPER_CAP",    str(COLLECT_PAPER_CAP))    or COLLECT_PAPER_CAP)
        api_cap   = int(_os.getenv("J09_API_CAP",      str(COLLECT_API_CAP))      or COLLECT_API_CAP)
        budget    = int(_os.getenv("J09_QUOTA_BUDGET", str(COLLECT_QUOTA_BUDGET)) or COLLECT_QUOTA_BUDGET)

        log.info(f"[research] 티어순 수집 시작: theme='{theme}' "
                 f"쿼터=논문{paper_cap}·API{api_cap}·총{budget}")

        # 티어별 프로바이더 분류
        paper_provs = [p for p in _PROVIDERS if quota_group(p.source_type) == "paper"]
        api_provs   = [p for p in _PROVIDERS if quota_group(p.source_type) == "api"]
        rest_provs  = [p for p in _PROVIDERS if quota_group(p.source_type) == "rest"]

        seen_urls: set[str] = set()

        # ① 논문: 최대 paper_cap
        paper_docs = _collect_tier(paper_provs, theme, sector, paper_cap, seen_urls)
        log.info(f"[research] 논문 {len(paper_docs)}/{paper_cap}건 확보")

        # ② API: 최대 api_cap + 논문 이월
        # ★ 뉴스·웹 최소보장 (2026-07-17): cascade(논문 미달분 이월)가 API 예산을 부풀려
        #   '나머지'(뉴스·웹) 슬롯을 굶기지 않도록 상한 — budget 에서 rest_floor 는 남긴다.
        #   기본 rest_floor=5 는 현행 '나머지5' 와 정합(숫자 변화 0, 순수 회귀 방지 안전망).
        _rest_floor = int(_os.getenv("J09_REST_FLOOR", "5") or "5")
        api_allow = api_cap + (paper_cap - len(paper_docs))
        api_allow = min(api_allow, max(0, budget - len(paper_docs) - _rest_floor))
        api_docs  = _collect_tier(api_provs, theme, sector, api_allow, seen_urls)
        log.info(f"[research] API {len(api_docs)}/{api_allow}건 확보")

        # ③ 나머지: 남은 예산 전부 (cascade 자동)
        rest_allow = budget - len(paper_docs) - len(api_docs)
        rest_docs  = (_collect_tier(rest_provs, theme, sector, rest_allow, seen_urls)
                      if rest_allow > 0 else [])
        log.info(f"[research] 나머지 {len(rest_docs)}/{rest_allow}건 확보")

        all_docs = paper_docs + api_docs + rest_docs

        # 얇은 문서 전문 딥페치
        all_docs = _deep_fetch_thin_docs(all_docs, theme)

        total = len(all_docs)
        log.info(f"[research] 완료: 논문{len(paper_docs)}+API{len(api_docs)}"
                 f"+나머지{len(rest_docs)}={total}건 → JARVIS02 fact·수치 추출")
        return {"docs": all_docs, "plan": {}}
    finally:
        # 작업 종료 — busy 즉시 해제 (해제 실패는 조용히 무시, TTL 은 안전망으로 잔존)
        try:
            from shared.pipeline_activity import clear_busy as _cb
            _cb("j09")
        except Exception:
            pass


# ── ★ 통합 수집 컴포저 — CollectedData 방출 (Step 3, UNIFIED_PIPELINE_SPEC) ──
#   전 카테고리 J09-측 단일 진입점. 종목(테마)→entities, research→docs+facts,
#   stocks/facts→datasets(통일 스키마). 대본·process_draft·검증이 이 상자만 소비.

# 엔티티 attr 표시단위 스케일 — ATTR_UNITS 와 정합 필수
#   (marcap·revenue: 원→조원, net_income: 원→억원, roe·op_margin: 소수→%)
_ENTITY_SCALE = {
    "price": 1.0, "per": 1.0,
    "marcap": 1e-12, "revenue": 1e-12, "net_income": 1e-8,
    "roe": 100.0, "op_margin": 100.0,
}


def _stocks_to_entities(stocks_data: dict) -> list[dict]:
    """collect_stocks_data.stocks → CollectedData.entities (다속성 레코드).
    attrs 값은 ATTR_UNITS 표시단위로 스케일 (all_numbers grounding 정합)."""
    from datetime import date as _d
    src = {"name": "네이버 금융(KRX 시세)", "url": "https://finance.naver.com",
           "as_of": _d.today().isoformat()}
    ents: list[dict] = []
    for s in (stocks_data or {}).get("stocks") or []:
        name = s.get("name")
        if not name:
            continue
        attrs: dict = {}
        for k, scale in _ENTITY_SCALE.items():
            raw = s.get(k)
            if raw in (None, ""):
                continue
            try:
                attrs[k] = round(float(raw) * scale, 2)
            except (TypeError, ValueError):
                continue
        ents.append({"name": str(name), "type": "stock",
                     "code": s.get("code") or "",
                     "ticker": s.get("ticker") or "",   # yfinance 형식 (005930.KS) — price chart 폴백용
                     "rank": s.get("rank"),              # 대장주=1, 부대장주=2 — _inject_leader_price_charts 폴백용
                     "attrs": attrs, "source": dict(src)})
    return ents


def _dedupe_datasets(datasets: list[dict]) -> list[dict]:
    """fingerprint(=title|unit) 기준 dataset dedupe — 생산자 간 중복 제거."""
    seen: set = set()
    out: list[dict] = []
    for ds in datasets or []:
        fp = ds.get("fingerprint") or (str(ds.get("title", "")), str(ds.get("unit", "")))
        if fp in seen:
            continue
        seen.add(fp)
        out.append(ds)
    return out


def compose_collected(keyword: str, stocks_data: dict | None = None,
                      docs: list | None = None, evidence_pack: dict | None = None,
                      sector: str = "", category: str = "theme",
                      profile: dict | None = None,
                      extra_datasets: list | None = None,
                      extra_meta: dict | None = None) -> "CollectedData":
    """★ 이미 수집된 조각 → CollectedData 조립 (재수집 없음).

    테마 하네스처럼 자체 수집 흐름(병렬 stocks + research)을 가진 호출자가
    조각을 넘겨 표준 상자를 만든다. process_draft 마이그레이션 브리지도 이 함수 사용.
    meta['raw_stocks'] 로 원본 종목 dict 를 side-channel 보존 (프롬프트 빌더용).
    """
    from datetime import datetime as _dt
    from .models import CollectedData
    from .collect_theme import stocks_to_datasets
    from .evidence_pack import facts_to_datasets
    stocks_data = stocks_data or {}
    pack = evidence_pack or {}
    entities = _stocks_to_entities(stocks_data)
    stock_ds = stocks_to_datasets(stocks_data) if stocks_data.get("stocks") else []
    fact_ds = facts_to_datasets(pack) if pack else []
    datasets = _dedupe_datasets(list(extra_datasets or []) + list(stock_ds) + list(fact_ds))
    facts = list(pack.get("facts") or [])
    meta = {
        "keyword": keyword, "profile": profile or {}, "sector": sector,
        "category": category,
        "as_of": pack.get("created_at") or _dt.now().isoformat(),
        "summary": (stocks_data or {}).get("summary") or {},
        "raw_stocks": stocks_data,        # ★ 프롬프트 빌더용 원본 side-channel
    }
    if extra_meta:
        meta.update(extra_meta)
    return CollectedData(meta=meta, datasets=datasets, docs=list(docs or []),
                         facts=facts, entities=entities)


def collect_all(keyword: str, profile: dict | None = None, sector: str = "",
                category: str = "theme", angle: str = "") -> "CollectedData":
    """★ 통합 수집 — J09-측 컴포저 (수집 + compose_collected).

    theme 카테고리는 종목(entities)+research(docs+facts)+datasets 를,
    그 외 카테고리는 research 만 수집(종목 없으면 entities 빈 리스트).
    """
    from .collect_theme import collect_stocks_data
    stocks_data: dict = {}
    if (category or "").strip().lower() == "theme":
        try:
            stocks_data = collect_stocks_data(keyword, related_terms=(profile or {}).get('related_terms'), profile=profile) or {}
        except Exception as e:
            log.warning(f"[collect_all] 종목 수집 실패: {e}")
    rs = collect_research(keyword, sector=sector, angle=angle) or {}
    # ★ 09는 원시 수집만 (단순 수집기 재설계 2026-07-06) — fact 추출은 02 몫.
    #   compose_collected 는 evidence_pack=None → facts 없이 docs+entities 만.
    return compose_collected(
        keyword, stocks_data=stocks_data, docs=rs.get("docs"),
        evidence_pack=None, sector=sector,
        category=category, profile=profile)


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
