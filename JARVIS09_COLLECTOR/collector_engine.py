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
from .source_registry import main_providers   # ★ 메인 수집 provider 목록 SSOT 파생

log = logging.getLogger("jarvis.collector.engine")

# ★ 수집 풍부 원칙 (사용자 박제 2026-07-03 ×2 — ADR 013 / ERRORS [314]):
#   "주제가 설정되면 그 주제에 맞는 정보는 싹다 받아버려, 제한 두지 말고."
#   소스별 상한은 무한루프 방지용 안전망일 뿐 — 신뢰순 *선별* 은 사용 시점(주입·검증)에.
#   ★ source_registry.SOURCES 의 max_items 에서 파생 (사용자 박제 2026-07-24) — 사본 폐지. default 8.
from .source_registry import MAX_PER_SOURCE as _PROVIDER_LIMITS

# ★ 메인 수집 provider — source_registry.SOURCES(SSOT) 에서 파생 (사용자 박제 2026-07-24).
#   종전엔 여기 16줄을 손수 유지했고 provider 목록이 chart_data._m·providers/__init__ 과 3벌
#   흩어져 소스 하나 넣고 뺄 때마다 3곳을 고쳐야 했다. 이제 SOURCES 한 줄이면 전부 자동 반영.
_PROVIDERS = main_providers()
_MAX_WORKERS = 8   # 병렬 수집


@_auto_catch("collector", reraise=True)
def collect_for_theme(theme: str, sector: str = "") -> list[CollectionResult]:
    """주제·섹터에 맞는 전 소스 병렬 수집 → 정제 결과 반환.

    수집 소스: 뉴스(Google+한국경제지) + 한국경제전문 + 블로그 + 웹(위키+지식백과) + 금융지표
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
        #   "API > 뉴스 > 기사 > 웹 — 겹치면 이 순서로 선택. 수집 자체는 전부."
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
#  흐름: ① 티어순 광역 수집(_collect_tier — API>뉴스>기사>웹, 신뢰순위) + discover 웹발견
#        → ② 얇은 문서 전문 딥페치 → ③ EvidencePack 추출·커버리지 측정
#        → ④ 미충족 시 2라운드 재수집(변형 쿼리+discover) → ⑤ 박제·반환
#  (구 plan_research 설계-LLM·질문별 조준수집은 2026-07-11 _collect_tier 재작성으로 폐지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PROVIDER_BY_TYPE = {p.source_type: p for p in _PROVIDERS}


SOURCE_CATEGORIES = ["blog", "news", "finance", "web"]
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

    API 최대 10 · 나머지 5(소스별 1개씩), 총 `budget`(기본 15)개.
    상위 티어 미달분은 다음 티어로 이월(cascade):
      API 8개 → 나머지 7개까지 / API 0개 → 나머지에서 budget 전부.
    나머지는 source_type 라운드로빈(각 1개씩 우선)으로 다양성 확보.
    env: J09_QUOTA_BUDGET(총량)·J09_API_CAP 로 튜닝.
    """
    from .models import (quota_group, trust_rank,
                         COLLECT_QUOTA_BUDGET, COLLECT_API_CAP)
    budget = int(_os.getenv("J09_QUOTA_BUDGET", str(budget or COLLECT_QUOTA_BUDGET))
                 or COLLECT_QUOTA_BUDGET)
    api_cap = int(_os.getenv("J09_API_CAP", str(COLLECT_API_CAP)) or COLLECT_API_CAP)

    groups: dict[str, list] = {"api": [], "rest": []}
    for d in docs:
        groups[quota_group(d.source_type)].append(d)
    # 각 그룹 내부: 신뢰 높은 소스 우선 (stable — 티어 내 원래 관련도 순서 보존)
    for g in groups.values():
        g.sort(key=lambda r: trust_rank(r.source_type))

    selected: list[CollectionResult] = []
    remaining = budget

    # API: 최대 api_cap
    take_api = groups["api"][:min(api_cap, remaining)]
    selected += take_api
    remaining -= len(take_api)

    # 나머지: 라운드로빈(각 source_type 1개씩) — 남은 예산 전부 (API 미달분 자동 이월)
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

    log.info(f"[quota] 신뢰 쿼터 선별: API {len(take_api)} · "
             f"나머지 {len(selected) - len(take_api)} = 총 {len(selected)}건 "
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
                     max_rounds: int = 3, with_facts: bool = False,
                     with_digest: bool = False) -> dict:
    """★ 티어순 상한 수집 (사용자 박제 2026-07-11 — ERRORS [423]):
    처음부터 API 최대 10·나머지 최대 5, cascade 이월.
    광역수집 후 절삭 방식 완전 폐지 — 각 티어가 수집 시점에 상한 적용.

    ★ fact 추출 09 통일 (사용자 박제 2026-07-18): with_facts=True 면 수집 직후 09 내부에서
      build_evidence_pack 실행 → 반환에 "pack"(facts) 동봉. 호출자(JARVIS02)는 res["pack"] 만
      쓰고 09 내부 모듈(evidence_pack)을 직접 import 하지 않는다("09는 수집·추출 단일 진입점").
      기본 False — collect_all 등 원시 수집만 원하는 호출자는 무변경.

    Returns:
        {"docs": list[CollectionResult],  # 신뢰순 최대 15개 원시 문서
         "plan": dict,                    # 빈 dict (설계 LLM 제거 — _collect_tier가 plan 미사용)
         "pack": dict}                    # with_facts=True 일 때만 — evidence_pack(facts·coverage)
    """
    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j09", f"{theme[:12]} 리서치", ttl=600)   # 안전망 10분 — 실소요 기준 축소
    except Exception:
        pass
    # busy 신호 수명 = 함수 수명 — 종료(성공·실패) 시 finally 에서 즉시 해제 (근본 수정 2026-07-16)
    try:
        from .models import (quota_group,
                             COLLECT_QUOTA_BUDGET, COLLECT_API_CAP)

        api_cap   = int(_os.getenv("J09_API_CAP",      str(COLLECT_API_CAP))      or COLLECT_API_CAP)
        budget    = int(_os.getenv("J09_QUOTA_BUDGET", str(COLLECT_QUOTA_BUDGET)) or COLLECT_QUOTA_BUDGET)

        log.info(f"[research] 티어순 수집 시작: theme='{theme}' "
                 f"쿼터=API{api_cap}·총{budget}")

        # 티어별 프로바이더 분류
        api_provs   = [p for p in _PROVIDERS if quota_group(p.source_type) == "api"]
        rest_provs  = [p for p in _PROVIDERS if quota_group(p.source_type) == "rest"]

        seen_urls: set[str] = set()

        # ① API: 최대 api_cap
        # ★ 뉴스·웹 최소보장 (2026-07-17): API 예산이 '나머지'(뉴스·웹) 슬롯을 굶기지 않도록
        #   budget 에서 rest_floor 는 남긴다. 기본 rest_floor=5 는 현행 '나머지5' 와 정합.
        _rest_floor = int(_os.getenv("J09_REST_FLOOR", "5") or "5")
        api_allow = min(api_cap, max(0, budget - _rest_floor))
        api_docs  = _collect_tier(api_provs, theme, sector, api_allow, seen_urls)
        log.info(f"[research] API {len(api_docs)}/{api_allow}건 확보")

        # ② 나머지: 남은 예산 전부 (cascade 자동)
        rest_allow = budget - len(api_docs)
        rest_docs  = (_collect_tier(rest_provs, theme, sector, rest_allow, seen_urls)
                      if rest_allow > 0 else [])
        log.info(f"[research] 나머지 {len(rest_docs)}/{rest_allow}건 확보")

        all_docs = api_docs + rest_docs

        # 얇은 문서 전문 딥페치
        all_docs = _deep_fetch_thin_docs(all_docs, theme)

        total = len(all_docs)
        # ★ fact 추출 09 통일 (2026-07-18): with_facts=True 면 여기서 추출 — 호출자는 pack 만 받음.
        out = {"docs": all_docs, "plan": {}}
        if with_facts:
            try:
                from .evidence_pack import build_evidence_pack
                _pack = build_evidence_pack(theme, {}, all_docs) or {}
                out["pack"] = _pack
                log.info(f"[research] 완료: API{len(api_docs)}"
                         f"+나머지{len(rest_docs)}={total}건 → fact {len(_pack.get('facts', []))}개 추출(09)")
            except Exception as _fe:
                out["pack"] = {}
                log.warning(f"[research] fact 추출 실패: {_fe} — 문서만 반환")
        else:
            log.info(f"[research] 완료: API{len(api_docs)}"
                     f"+나머지{len(rest_docs)}={total}건 (원시 문서만)")
        # ★ corpus digest (distill 압축 2026-07-19) — 선계산 창에서만 생성(발행창이면 build_corpus_digest
        #   가 "" 반환 → 호출자 원문 폴백). writer 프롬프트 축소용. docs(원문)는 그대로 유지(사실성용).
        if with_digest:
            try:
                from .evidence_pack import build_corpus_digest
                _dig = build_corpus_digest(all_docs)
                if _dig:
                    out["corpus_digest"] = _dig
            except Exception as _de:
                log.warning(f"[research] corpus digest 실패: {_de}")
        return out
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
    from .models import CollectedData, policy_for, dataset_is_stock_financial
    from .collect_theme import stocks_to_datasets
    from .evidence_pack import facts_to_datasets
    stocks_data = stocks_data or {}
    pack = evidence_pack or {}
    entities = _stocks_to_entities(stocks_data)
    stock_ds = stocks_to_datasets(stocks_data) if stocks_data.get("stocks") else []
    fact_ds = facts_to_datasets(pack) if pack else []
    datasets = _dedupe_datasets(list(extra_datasets or []) + list(stock_ds) + list(fact_ds))
    # ★ 종목재무 배제는 *조립 지점 한 곳* 에서 (사용자 박제 2026-07-23 — 수집 09 이관).
    #   종전엔 경제 파이프라인(02)만 fact 유래 dataset 을 걸러서, 같은 조립 함수를 쓰는
    #   다른 경로는 정책이 새어나갔다. 판정 근거는 models.dataset_is_stock_financial 단일 소스.
    if not policy_for(category).get("allow_stock_financial", True):
        _before = len(datasets)
        datasets = [d for d in datasets if not dataset_is_stock_financial(d)]
        if _before != len(datasets):
            log.info(f"[compose] '{category}' 종목재무 dataset {_before - len(datasets)}개 배제")
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


def _collect_stocks_leg(keyword: str, profile: dict | None) -> dict:
    """종목 시세·재무 수집 (실패해도 빈 dict — 리서치만으로 글은 성립)."""
    from .collect_theme import collect_stocks_data
    try:
        return collect_stocks_data(
            keyword, related_terms=(profile or {}).get("related_terms"),
            profile=profile) or {}
    except Exception as e:
        log.warning(f"[collect_all] 종목 수집 실패: {e}")
        return {}


def _collect_charts_leg(keyword: str, sector: str, angle: str,
                        profile: dict | None, synonyms: list | None,
                        plan_cache: dict | None, category: str) -> list:
    """주제 연관 차트 실데이터 수집 (ADR 010/011). 실패해도 빈 리스트."""
    try:
        from .chart_data import collect_chart_data
        chart = collect_chart_data(
            keyword, sector=sector, description=angle,
            synonyms=synonyms, related_terms=(profile or {}).get("related_terms"),
            profile=profile, plan_cache=plan_cache, category=category) or {}
        return list(chart.get("datasets") or [])
    except Exception as e:
        log.warning(f"[collect_all] 차트 실데이터 수집 실패: {e}")
        return []


def _collect_research_leg(keyword: str, sector: str, angle: str) -> dict:
    """설계-우선 리서치 (ADR 012) + fact 추출 + digest. 킬스위치 RESEARCH_FIRST=0 → 종전 스윕."""
    import os as _os
    if _os.getenv("RESEARCH_FIRST", "1") != "0":
        try:
            res = collect_research(keyword, sector=sector, angle=angle,
                                   with_facts=True, with_digest=True) or {}
            return {"docs": list(res.get("docs") or []),
                    "pack": res.get("pack") or None,
                    "corpus_digest": res.get("corpus_digest") or ""}
        except Exception as e:
            log.warning(f"[collect_all] 리서치 실패 — 종전 스윕 폴백: {e}")
    try:
        # ★ 2026-07-24 P2: 스윕 폴백도 신뢰 쿼터(기본 COLLECT_QUOTA_BUDGET=15) 적용.
        #   RESEARCH_FIRST 정상경로는 이미 쿼터를 타지만 이 예외 폴백은 무쿼터라
        #   205~226문서(119K자) 프롬프트 폭주의 진원이었다(발행 지연·overage). SSOT=models 쿼터.
        return {"docs": select_by_trust_quota(collect_for_theme(keyword, sector)),
                "pack": None, "corpus_digest": ""}
    except Exception as e:
        log.warning(f"[collect_all] 스윕 폴백도 실패: {e}")
        return {"docs": [], "pack": None, "corpus_digest": ""}


def collect_all(keyword: str, profile: dict | None = None, sector: str = "",
                category: str = "theme", angle: str = "",
                synonyms: list | None = None, plan_cache: dict | None = None,
                market_data: dict | None = None,
                extra_meta: dict | None = None,
                parallel: bool = True, use_cache: bool = True) -> dict:
    """★★ 수집 단일 진입점 — 주제 하나 → 완성된 CollectedData 상자 (사용자 박제 2026-07-23).

    종전에는 이 함수가 죽은 코드였고, 실제 수집 오케스트레이션이 JARVIS02 안에 *5벌*
    흩어져 있었다 (테마 선계산·테마 발행·경제 네이버·경제 티스토리·시장지표 변환).
    "수집은 09, 02는 대본" 이라는 도메인 경계가 호출 한 줄 단위로만 지켜지고 *순서·조합·
    폴백 판단* 은 02 가 하고 있었던 것 — 그래서 테마만 고치면 경제에서 재발했다.
    이제 4조합(경제·테마 × 네이버·티스토리)이 전부 이 함수 하나를 부른다.

    카테고리별 차이(종목·차트·시장지표 폴백·종목재무 배제)는 `if category ==` 로 박지
    않고 `CATEGORY_POLICY` 에서 파생한다 — 새 카테고리는 레지스트리 한 줄.

    Args:
        keyword:    주제 (자비스03 이 프로필과 함께 준 것 — 키워드 단독 전송 금지)
        profile:    자비스03 keyword_profile (summary·related_terms·entity_type)
        angle:      리서치 조준 각도. 미지정 시 profile.summary 에서 파생
        synonyms/plan_cache: 자비스03 저부하창 선계산 산출물 (있으면 발행창 LLM 0회)
        market_data: 시장지표 (경제 브리핑에서 차트 0개일 때 폴백 소스)
        parallel:   리서치를 별도 스레드로 (종목·차트와 동시). 인터프리터 종료 중이면 자동 동기.
        use_cache:  선계산 잡(발행창 밖)이 미리 수집해 둔 상자가 있으면 재사용
                    (사용자 박제 2026-07-18 / 소유 이관 2026-07-23 — 종전엔 02 가 캐시를
                    먼저 뒤지고 없으면 수집을 불렀다. '캐시냐 수집이냐' 는 수집 시점 판단이므로
                    09 안으로 들어온다. 선계산 잡 자신은 use_cache=False 로 실제 수집.)
                    킬스위치: 환경변수 `PRECOLLECT_CACHE=0`.

    Returns:
        {"collected": CollectedData, "stocks_data": dict, "docs": list,
         "evidence_pack": dict|None, "datasets": list, "corpus_digest": str,
         "data_empty": bool}
        data_empty = 종목·문서·근거가 *전부* 0 (테마 교체 판단용 — 부분 결손은 진행).
    """
    import os as _os
    from .models import policy_for
    pol = policy_for(category)

    # ── 선계산 캐시 재사용 (수집 *시점* 판단 — 09 소유) ────────────────
    if use_cache and _os.environ.get("PRECOLLECT_CACHE", "1") != "0":
        try:
            from .precollect_cache import load_precollect
            _hit = load_precollect(category, keyword)
            if _hit is not None:
                log.info(f"[collect_all] 선계산 캐시 재사용: {category}/{keyword} — 발행창 추출 LLM 0회")
                return _hit
        except Exception as e:
            log.warning(f"[collect_all] 캐시 조회 스킵({category}/{keyword}): {e} — 실제 수집 진행")

    # ── 새 수집 런 = 09 메모리 상태 초기화 (09 소유 상태) ───────────────
    #   ★ 플랫폼은 *덮어쓰지 않는다* — 발행 액션(02)이 이미 naver/tistory 를 세팅해 두고,
    #     그 값이 이미지 출력 폴더(economic_naver 등)를 가른다. 여기서 기본값으로 되돌리면
    #     경제 네이버 글의 이미지가 theme 폴더로 새는 회귀가 난다. post_type 은 category 에서 파생.
    try:
        from .run_context import new_run as _new_run, active_run as _active
        _prev = _active()
        _new_run(keyword,
                 platform=(_prev.platform if _prev else "naver"),
                 post_type=(pol.get("run_post_type") or category))
    except Exception as e:
        log.warning(f"[collect_all] run_context 초기화 스킵: {e}")

    # ── 프로필 보강 — 키워드 단독 수집 금지 (ADR 013) ──────────────────
    #   ② 동적 설계: `if category ==` 이 아니라 정책 노브(profile_provider)에서 파생.
    if profile is None:
        _pp = (pol.get("profile_provider") or "").strip()
        if _pp:
            try:
                import importlib as _il
                _mod, _fn = _pp.split(":", 1)
                _topic = getattr(_il.import_module(_mod), _fn)(keyword, sector=sector) or {}
                profile = _topic.get("profile") or {}
                sector = _topic.get("sector") or sector
                if profile:
                    log.info(f"[collect_all] 자비스03 프로필 수령: {str(profile.get('summary'))[:60]}")
            except Exception as e:
                log.warning(f"[collect_all] 프로필 조회 실패({_pp}): {e}")
                profile = {}

    angle = (angle or (profile or {}).get("summary") or "").strip()

    # ── 리서치 레그 (느림) — 가능하면 병렬 ────────────────────────────
    _fut = None
    if parallel:
        try:
            from concurrent.futures import ThreadPoolExecutor as _TExec
            _exec = _TExec(max_workers=1)
            _fut = _exec.submit(_collect_research_leg, keyword, sector, angle)
        except RuntimeError as e:
            # 인터프리터 종료 레이스 (ERRORS [361]) — 병렬 이득만 포기, 수집은 계속
            log.warning(f"[collect_all] 스레드 스케줄 불가 — 동기 폴백: {e}")
            _fut = None

    # ── 구조데이터 레그 (정책 파생) ───────────────────────────────────
    stocks_data = _collect_stocks_leg(keyword, profile) if pol.get("collect_stocks") else {}
    chart_ds = (_collect_charts_leg(keyword, sector, angle, profile, synonyms,
                                    plan_cache, category)
                if pol.get("collect_charts") else [])

    if _fut is not None:
        try:
            rs = _fut.result(timeout=600) or {}
        except Exception as e:
            log.warning(f"[collect_all] 리서치 수령 실패: {e}")
            rs = {"docs": [], "pack": None, "corpus_digest": ""}
        finally:
            try:
                _exec.shutdown(wait=False)
            except Exception:
                pass
    else:
        rs = _collect_research_leg(keyword, sector, angle)

    docs = rs.get("docs") or []
    pack = rs.get("pack") or None
    digest = rs.get("corpus_digest") or ""

    # ── 시장지표 폴백 (차트 0개일 때만 — 경제 브리핑) ──────────────────
    if not chart_ds and market_data and pol.get("market_fallback"):
        chart_ds = market_data_to_datasets(market_data)
        if chart_ds:
            log.info(f"[collect_all] 차트 0 → 시장지표 {len(chart_ds)}개로 폴백")

    meta_extra = dict(extra_meta or {})
    if digest:
        meta_extra["corpus_digest"] = digest
    collected = compose_collected(
        keyword, stocks_data=stocks_data, docs=docs, evidence_pack=pack,
        sector=sector, category=category, profile=profile,
        extra_datasets=chart_ds, extra_meta=meta_extra or None)

    n_stocks = len((stocks_data or {}).get("stocks") or [])
    n_facts = len((pack or {}).get("facts") or [])
    log.info(f"[collect_all] '{keyword}'({category}) 종목 {n_stocks} · 문서 {len(docs)} · "
             f"근거 {n_facts} · 데이터셋 {len(collected.datasets)}")
    return {"collected": collected, "stocks_data": stocks_data, "docs": docs,
            "evidence_pack": pack, "datasets": list(collected.datasets),
            "corpus_digest": digest,
            "data_empty": (n_stocks == 0 and not docs and n_facts == 0)}


def market_snapshot() -> dict:
    """★ 시장 스냅샷 수집 — 지표 + 일정 (사용자 박제 2026-07-23).

    종전엔 02 의 두 곳(economic_poster·precollect_economic)이 각자
    get_market_data()+get_economic_calendar() 를 불러 dict 를 조립했다. 두 번 조립하는
    순간 키 이름이 어긋날 수 있고, 그 조립 규칙이 02 에 있으면 수집 산출물의 *형태* 를
    02 가 정하는 셈이다. 형태도 09 가 정한다.
    """
    from .providers.economic_data_provider import get_market_data, get_economic_calendar
    out = {"market": {}, "calendar": {}}
    try:
        out["market"] = get_market_data() or {}
    except Exception as e:
        log.warning(f"[market_snapshot] 지표 수집 실패: {e}")
    try:
        out["calendar"] = get_economic_calendar() or {}
    except Exception as e:
        log.warning(f"[market_snapshot] 일정 수집 실패: {e}")
    return out


# ── 시장지표 → datasets (JARVIS02 에서 이관 2026-07-23) ──────────────────
_MD_INDICES  = ["코스피", "코스닥", "S&P500", "NASDAQ", "DOW"]
_MD_FX_COMMO = ["달러/원", "금", "유가(WTI)"]
_MD_RATES    = ["미국채10년"]
_MD_GROUPS = [
    ("주요 증시 지표", _MD_INDICES,  "pt"),
    ("환율·원자재",    _MD_FX_COMMO, ""),
    ("금리 지표",      _MD_RATES,    "%"),
]


def market_data_to_datasets(market_data: dict) -> list:
    """시장지표(get_market_data) → CollectedData datasets.

    수집 산출물의 형태 변환은 수집 도메인의 일 — 종전 JARVIS02 `_market_data_to_datasets`
    에 있던 것을 09 로 이관 (사용자 박제 2026-07-23). collect_all 이 차트 0개일 때 호출.
    """
    import hashlib as _hl
    market = (market_data or {}).get("market") or {}
    if not market:
        return []
    out: list[dict] = []
    for title, keys, unit in _MD_GROUPS:
        rows = [(k, market[k]) for k in keys if k in market]
        if not rows:
            continue
        as_of = max((v.get("as_of") or "") for _, v in rows)
        out.append({
            "title": title, "viz_hint": "kpi_cards", "unit": unit,
            "data": [{"label": k, "value": v.get("value", 0),
                      "change_pct": v.get("change", 0)} for k, v in rows],
            "source": {"provider": "yfinance", "name": "Yahoo Finance",
                       "url": "https://finance.yahoo.com", "as_of": as_of},
            "fingerprint": _hl.md5(f"{title}{as_of}".encode()).hexdigest()[:12],
        })
    return out


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
