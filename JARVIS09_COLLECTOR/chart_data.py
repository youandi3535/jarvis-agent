"""JARVIS09_COLLECTOR/chart_data.py — 주제 연관 *차트용 실데이터* 단일 수집 API.

★ 사용자 박제 2026-06-29 — "본문에서 숫자 짜내기 금지. 글 주제와 연관된 실데이터를
  JARVIS09 가 수집해서 차트로 만든다. 정확히 본문과 일치할 필요 없고 *주제 연관성*만
  있으면 됨 (예: 삼성전자 → 주가·매출·직원수·공장수·협력사 등 무엇이든 실데이터)."

핵심 원칙:
  1. 모든 dataset 은 *출처(provenance)* 를 반드시 박는다 → 사실성 검증의 근거.
     source = {"provider", "name", "url", "as_of"}.
  2. 구조화 API(KRX/DART/yfinance/ECOS) 우선, 웹 출처는 *URL 박제* 조건부 허용.
  3. 실데이터가 없으면 빈 리스트 반환 — 거짓 데이터 합성 절대 금지.

공개 API:
  collect_chart_data(theme, sector="", description="", exclude_titles=None,
                     max_datasets=6) -> {"theme": str, "datasets": [dataset, ...]}

dataset 스키마:
  {
    "title":      "삼성전자 PER 비교",
    "viz_hint":   "bar_chart" | "line_chart" | "pie_chart" | "kpi_cards",
    "unit":       "배",
    "data":       [{"label": "삼성전자", "value": 12.3}, ...],
    "source":     {"provider": "krx", "name": "한국거래소·DART",
                   "url": "https://...", "as_of": "2026-06"},
    "fingerprint":"<title+unit hash>",   # dedup·검증 매칭용
  }
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime

log = logging.getLogger("jarvis.collector.chart_data")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

# 주식 테마 키워드 — 종목 재무 dataset 허용 판정
_STOCK_THEME_KWS = frozenset([
    "반도체", "2차전지", "배터리", "바이오", "제약", "자동차", "전기차", "게임",
    "방산", "조선", "철강", "정유", "에너지", "부동산", "건설", "항공", "유통",
    "플랫폼", "금융주", "은행주", "보험주", "통신주", "시가총액", "주가", "종목",
    "코스피", "코스닥", "증시", "상장", "기업", "그룹", "전자", "화학", "엔터",
])

# 시장 전체 거래대금 dataset 적용 대상 키워드
_MARKET_KWS = frozenset(["코스피", "코스닥", "증시", "주식시장", "한국증시"])

# 거시경제·시장 키워드 — market/ECOS dataset 허용 판정
_MACRO_KWS = frozenset([
    "금리", "기준금리", "물가", "cpi", "환율", "달러", "수출", "수입", "실업",
    "성장", "gdp", "경제", "경기", "증시", "코스피", "나스닥", "시장", "인플레",
])


def _now_as_of() -> str:
    return datetime.now().strftime("%Y-%m")


def _fingerprint(title: str, unit: str) -> str:
    from JARVIS09_COLLECTOR.models import dataset_fingerprint
    return dataset_fingerprint(title, unit)   # 단일 소스 (3 생산자 공통)


def _mk_dataset(title, viz_hint, unit, data, source) -> dict | None:
    """dataset dict 생성. data 가 비거나 값이 부족하면 None."""
    rows, _seen = [], set()
    for d in data or []:
        try:
            v = float(str(d.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        label = _clean_label(d.get("label", ""))   # 괄호 설명 제거 + 길이 제한 (짤림·장황 방지)
        if label and v == v and label not in _seen:  # NaN·중복 라벨 제외
            if v == 0:  # ★ 0값 제외 (viz_hint 무관) — 0개 항목은 데이터 없음·비교 불가
                continue
            _seen.add(label)
            rows.append({"label": label, "value": v})
    # ★ bar_chart: 합계 행과 세부 항목이 섞이면 합계 행 제거 (LLM·KOSIS 양 경로 공통)
    # "전체/계/합계" 라벨은 개별 항목들의 집계이므로 동일 차트에 놓으면 비교 왜곡.
    if viz_hint == "bar_chart" and len(rows) > 2:
        non_total = [r for r in rows if r["label"] not in _DEMO_TOTAL]
        if len(non_total) >= 2:
            rows = non_total
    # 차트 의미 최소 기준: kpi 1개, 그 외 2개
    _min = 1 if viz_hint == "kpi_cards" else 2
    if len(rows) < _min:
        return None
    # ★ 가독성 — 막대는 최대 10개(30셀 막대벽 방지), 라인(시계열)은 12점까지 보존(추이 유지)
    rows = rows[:12] if viz_hint == "line_chart" else rows[:10]
    _title = str(title).split("(")[0].split("（")[0].strip() or str(title).strip()   # 제목 괄호 설명 제거
    return {
        "title": _title[:30],
        "viz_hint": viz_hint,
        "unit": unit.strip(),
        "data": rows,
        "source": source,
        "fingerprint": _fingerprint(title, unit),
    }


# ── 0. 코스닥 150 섹터 지수 dataset (ERRORS [420]) ───────────────────────────
_KOSDAQ150_KWS = frozenset(["코스닥 150", "kosdaq 150", "kosdaq150", "150 섹터", "150 지수",
                             "150 소재", "150 헬스케어", "150 정보기술", "150 산업재", "섹터 지수"])


def _kosdaq150_sector_datasets(theme: str) -> list[dict]:
    """코스닥 150 섹터 지수(8개 전체) dataset.

    ★ 왜 필요: KOSIS/web 수집 시 8개 중 일부만 반환 → 최고/최저 KPI 오표시 (ERRORS [420]).
      KRX get_index_ohlcv_by_date 로 8개 전부 보장.
    실데이터 실패 시 빈 리스트 — ADR 010: 합성 수치 절대 금지.
    """
    theme_lower = theme.lower()
    if not any(kw in theme_lower for kw in _KOSDAQ150_KWS):
        return []
    try:
        from JARVIS09_COLLECTOR.providers.krx_provider import (
            _collect_kosdaq150_sectors, _last_trading_day,
        )
        doc = _collect_kosdaq150_sectors()
        if not doc:
            log.info("[chart_data] 코스닥 150 섹터 지수 실데이터 없음 — 차트 스킵 (ADR 010)")
            return []

        # RawDocument → rows 파싱
        rows = []
        in_table = False
        for line in doc.raw_text.splitlines():
            if line.startswith("섹터명|"):
                in_table = True
                continue
            if in_table and "|" in line:
                parts = line.split("|")
                if len(parts) >= 2:
                    try:
                        rows.append({"label": parts[0].strip(), "value": float(parts[1].strip())})
                    except (ValueError, TypeError):
                        pass

        if len(rows) < 3:
            return []

        _td = _last_trading_day()
        src = {
            "provider": "krx",
            "name": "한국거래소 KRX",
            "url": "https://data.krx.co.kr",
            "as_of": f"{_td.year}년 {_td.month}월",
        }
        ds = _mk_dataset("코스닥 150 섹터 지수", "bar_chart", "pt", rows, src)
        log.info(f"[chart_data] 코스닥 150 섹터 지수 {len(rows)}개 dataset 생성")
        return [ds] if ds else []
    except Exception as e:
        log.warning(f"[chart_data] 코스닥 150 섹터 dataset 실패: {e}")
        return []


# ── 0.5. 글로벌 시장 지표 fast-path (_MACRO_KWS 주제 전용, plan 없어도 동작) ──
def _global_market_datasets(theme: str) -> list[dict]:
    """거시경제 주제(나스닥·환율·금리 등) 전용 fast-path.

    get_market_data() 구조화 API → kpi_cards dataset. LLM 불필요.
    plan_data_sources 가 timeout/실패해도 항상 실데이터 공급.
    ADR 010: 출처(provenance) 박제, 합성 수치 절대 금지.
    """
    theme_lc = theme.lower()
    if not any(k in theme_lc for k in _MACRO_KWS):
        return []
    try:
        from JARVIS09_COLLECTOR.providers.economic_data_provider import get_market_data
        market = get_market_data() or {}
    except Exception as e:
        log.warning(f"[chart_data] 글로벌 시장 fast-path 실패: {e}")
        return []
    if not market:
        return []

    _INDICES = ["코스피", "코스닥", "S&P500", "NASDAQ", "DOW"]
    _FX_COMMO = ["달러/원", "금", "유가(WTI)"]
    _RATES = ["미국채10년"]

    def _make_ds(title, keys, unit):
        rows = [(k, market[k]) for k in keys if k in market and market[k].get("value")]
        if not rows:
            return None
        as_of = max((v.get("as_of") or "") for _, v in rows)
        data = [{"label": k, "value": v.get("value", 0), "change_pct": v.get("change", 0)}
                for k, v in rows]
        fp = _fingerprint(title, unit)
        return {"title": title, "viz_hint": "kpi_cards", "unit": unit, "data": data,
                "source": {"provider": "yfinance", "name": "Yahoo Finance",
                           "url": "https://finance.yahoo.com", "as_of": as_of},
                "fingerprint": fp}

    result = [ds for ds in [
        _make_ds("주요 증시 지표", _INDICES, "pt"),
        _make_ds("환율·원자재", _FX_COMMO, ""),
        _make_ds("금리 지표", _RATES, "%"),
    ] if ds]

    # ── ★ 시계열 라인차트 (사용자 박제 2026-07-17): 당일 스냅샷(kpi)만으로는 추이·맥락이 0.
    #    코스피·S&P500·달러/원 각각 최근 3개월 시계열을 *지표별 개별* 라인차트로 kpi 와 함께 방출.
    #    _FX_COMMO 처럼 스케일 혼재(1400원 vs 4000달러)를 한 차트에 합치지 않도록 지표당 1 dataset. ──
    result.extend(_market_timeseries_datasets())

    if result:
        log.info(f"[chart_data] '{theme}' 글로벌 시장 fast-path: {len(result)}개 dataset")
    return result


def _market_timeseries_datasets() -> list[dict]:
    """코스피·S&P500·달러/원 최근 3개월 주간 시계열 라인차트 (지표별 개별 dataset).

    시계열 조회 단일 진입점 = economic_data_provider.get_ticker_history (yfinance 직접 접근 금지).
    시계열 소스가 없거나 조회 실패 시 빈 리스트 — 합성 수치 절대 금지 (ADR 010).
    스케일 혼재 방지: 지표를 한 차트에 합치지 않고 각각 별도 dataset 으로 방출."""
    try:
        from JARVIS09_COLLECTOR.providers.economic_data_provider import (
            get_ticker_history, _MARKET_TICKERS,
        )
    except Exception as e:
        log.warning(f"[chart_data] 시계열 헬퍼 로드 실패 — 시계열 스킵: {e}")
        return []
    _TS_SPEC = [("코스피", "pt"), ("S&P500", "pt"), ("달러/원", "원")]   # (지표명, 단위)
    out: list[dict] = []
    for name, unit in _TS_SPEC:
        ticker = _MARKET_TICKERS.get(name)
        if not ticker:
            continue
        try:
            hist = get_ticker_history(ticker, period="3mo", interval="1wk")   # 3개월 주간 ≈ 13점
        except Exception as e:
            log.warning(f"[chart_data] '{name}' 시계열 조회 실패: {e}")
            continue
        if hist is None or len(hist) < 3:
            continue
        rows: list[dict] = []
        try:
            for idx, close in zip(hist.index, hist["Close"]):
                try:
                    v = float(close)
                except (TypeError, ValueError):
                    continue
                if v != v or v <= 0:   # NaN·0 제외
                    continue
                rows.append({"label": idx.strftime("%m.%d"), "value": round(v, 2)})
        except Exception as e:
            log.warning(f"[chart_data] '{name}' 시계열 파싱 실패: {e}")
            continue
        rows = rows[-12:]   # 최근 12점
        if len(rows) < 3:
            continue
        try:
            _as_of = hist.index[-1].strftime("%Y-%m-%d")
        except Exception:
            _as_of = _now_as_of()
        src = {"provider": "yfinance", "name": "Yahoo Finance",
               "url": "https://finance.yahoo.com", "as_of": _as_of}
        ds = _mk_dataset(f"{name} 최근 추이", "line_chart", unit, rows, src)
        if ds:
            out.append(ds)
    if out:
        log.info(f"[chart_data] 글로벌 시장 시계열 {len(out)}개 라인차트 생성")
    return out


# ── 1. 시장 거래대금 dataset (코스피·코스닥 주제 전용) ─────────────────────
def _market_trading_volume_datasets(theme: str) -> list[dict]:
    """코스피·코스닥 일평균 거래대금 비교 dataset.

    실데이터(pykrx) 획득 실패 시 빈 리스트 반환 — ADR 010: 합성 수치 절대 금지.
    """
    if not any(k in theme for k in _MARKET_KWS):
        return []
    try:
        from JARVIS09_COLLECTOR.providers.krx_provider import (
            collect_market_trading_volume, _last_trading_day,
        )
        vol = collect_market_trading_volume()
        if not vol:
            log.info("[chart_data] 시장 거래대금 실데이터 없음 — 차트 스킵 (ADR 010)")
            return []
        rows = []
        if vol.get("kospi") is not None:
            rows.append({"label": "코스피", "value": vol["kospi"]})
        if vol.get("kosdaq") is not None:
            rows.append({"label": "코스닥", "value": vol["kosdaq"]})
        if len(rows) < 2:
            return []
        _td = _last_trading_day()
        src = {
            "provider": "krx",
            "name": "한국거래소 KRX",
            "url": "https://data.krx.co.kr",
            "as_of": vol.get("as_of", f"{_td.year}년 {_td.month}월"),
        }
        ds = _mk_dataset("코스피·코스닥 일평균 거래대금", "bar_chart", "조원", rows, src)
        return [ds] if ds else []
    except Exception as e:
        log.warning(f"[chart_data] 시장 거래대금 dataset 실패: {e}")
        return []


# ── 2. 종목 재무 dataset (collect_stocks_data) ───────────────────────────
def _stock_datasets(theme: str, related_terms: list | None = None) -> list[dict]:
    try:
        from JARVIS09_COLLECTOR import collect_stocks_data
        data = collect_stocks_data(theme, related_terms=related_terms) or {}
    except Exception as e:
        log.warning(f"[chart_data] collect_stocks_data 실패: {e}")
        return []
    stocks = data.get("stocks") or []
    if len(stocks) < 2:
        return []

    # ★ as_of: 실제 최근 거래일 기준
    from JARVIS09_COLLECTOR.providers.krx_provider import _last_trading_day
    _td = _last_trading_day()
    src = {"provider": "krx", "name": "한국거래소·금융감독원 DART",
           "url": "https://data.krx.co.kr", "as_of": f"{_td.year}년 {_td.month}월 {_td.day}일"}
    # (metric 필드, 제목, 단위)
    _metrics = [
        ("per",       f"{theme} 주요 종목 PER",   "배"),
        ("roe",       f"{theme} 주요 종목 ROE",   "%"),
        ("op_margin", f"{theme} 영업이익률",       "%"),
        ("price",     f"{theme} 주요 종목 현재가", "원"),
    ]
    out = []
    for field, title, unit in _metrics:
        rows = []
        for s in stocks[:8]:
            v = s.get(field)
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            # ★ 수치 정확성: ROE·영업이익률은 비율(0.19)로 옴 → % 표기 위해 ×100 (0.1916→19.16%).
            #   abs<5 일 때만 변환(이미 %인 값 이중변환 방지). 사용자 박제 2026-06-30.
            if field in ("roe", "op_margin") and 0 < abs(v) < 5:
                v = v * 100
            if v and v > 0:
                rows.append({"label": s.get("name", "?"), "value": round(v, 2)})
        ds = _mk_dataset(title, "bar_chart", unit, rows, dict(src))
        if ds:
            out.append(ds)
    return out




# ── 설계도 실행기 — data_planner 의 series 를 조준 수집 (라이브러리 자동설치 포함) ──────────
#   ★ 사용자 박제 2026-06-30: 필요 라이브러리/API 는 *말 안 해도 자동 설치·연결*. 못 구하면
#   가만히 있지 말고 설계된 출처를 우선순위로 다 시도. (API 키는 .env 사전 등록 — 가입만 1회 수동.)
_PROVIDER_REGISTRY: dict = {}

# ★ per-run 수집 캐시 (속도 — 사용자 박제 2026-07-01): KOSIS 등 느린 출처를 step1(series별)+step3
#   에서 같은 (source,term) 으로 반복 스캔하던 낭비 제거. collect_chart_data 진입 시 clear.
_COLLECT_CACHE: dict = {}


def _cached_collect(prov, source: str, term: str, sector: str, max_items: int) -> list:
    """provider.collect 결과를 (source,term,sector) 키로 run 내 캐시. 느린 출처 반복 호출 방지."""
    key = (source, str(term), str(sector))
    if key in _COLLECT_CACHE:
        return _COLLECT_CACHE[key]
    try:
        docs = prov.collect(term, sector, max_items=max_items) or []
    except Exception as e:
        log.warning(f"[chart_data] {source} 수집 실패('{term}'): {e}")
        docs = []
    _COLLECT_CACHE[key] = docs
    return docs


_SOURCE_LIBS = {   # source → 자동설치 무료 라이브러리 (import명, pip명)
    "krx":      [("pykrx", "pykrx")],
    "finance":  [("yfinance", "yfinance")],
    "news":     [("feedparser", "feedparser")],
    "blog":     [("feedparser", "feedparser")],
    "web":      [("bs4", "beautifulsoup4")],
    "kor_econ": [("bs4", "beautifulsoup4")],
    # ★ discover: 웹 발견·범용 파싱 (ddgs 는 discovery 내부에서 자동설치)
    "discover": [("bs4", "beautifulsoup4"), ("pandas", "pandas"), ("lxml", "lxml")],
}


def _ensure_source_ready(source: str) -> None:
    try:
        from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
        for imp, pip in _SOURCE_LIBS.get(source, []):
            ensure_lib(imp, pip)
    except Exception as e:
        log.warning(f"[chart_data] {source} 라이브러리 자동설치 스킵: {e}")


def _get_provider(source: str):
    if source in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[source]
    inst = None
    try:
        from JARVIS09_COLLECTOR.providers import (
            BlogProvider, NewsProvider, AcademicProvider, FinanceProvider, WebProvider,
            KorEconProvider, NaverNewsProvider, DartProvider, EcosProvider,
            KosisProvider, KrxProvider, KciProvider, DiscoveryProvider,
        )
        _m = {"blog": BlogProvider, "news": NewsProvider, "academic": AcademicProvider,
              "finance": FinanceProvider, "web": WebProvider, "kor_econ": KorEconProvider,
              "naver_news": NaverNewsProvider, "dart": DartProvider, "ecos": EcosProvider,
              "kosis": KosisProvider, "krx": KrxProvider, "kci": KciProvider,
              "discover": DiscoveryProvider}
        cls = _m.get(source)
        inst = cls() if cls else None
    except Exception as e:
        log.warning(f"[chart_data] provider 로드 실패({source}): {e}")
    _PROVIDER_REGISTRY[source] = inst
    return inst


_SERIES_SYSTEM = """당신은 수집 자료에서 *실제로 등장한 수치*만 뽑아 *하나의 일관된 차트* 로
구조화하는 데이터 분석가다. 자료에 없는 수치는 절대 만들지 않고, 단위가 다른 수치를
한 차트에 섞지 않는다 (그건 무의미한 차트가 된다)."""

_SERIES_PROMPT = """요청 지표: "{name}"  (단위 힌트: {unit})

아래 자료에서 위 지표에 직접 해당하는 *실제 수치*로 *하나의 비교 가능한 차트* 를 만들어라.
각 발췌 앞 [n] 은 출처 인덱스.

★ 엄격 규칙 (어기면 가짜·무의미 차트):
1. 모든 항목은 *같은 단위·같은 종류* 여야 한다 (예: 전부 '%', 전부 '억원', 전부 '명').
   서로 다른 종류(효과 vs 비용, 금액 vs 비율)를 한 차트에 섞지 마라.
2. 각 수치의 *단위* 를 반드시 식별해 "unit" 에 적어라 (달러/원/억원/조원/%/명/건 등).
   단위를 확정할 수 없으면 그 수치는 버려라.
3. 자료에 실제로 적힌 숫자만. 추정·반올림 창작 금지.
4. 비교 가능한 항목이 2개 미만이면 빈 데이터로 반환.

{excerpts}

출력 JSON만: {{"unit": "공통단위", "data": [{{"label": "연도/항목", "value": 숫자, "source_idx": n}}]}}
일관된 수치가 없으면 {{"unit": "", "data": []}}."""


_SYNONYM_FILE = __import__("pathlib").Path(__file__).parent / "synonym_cache.json"


def _load_synonym_cache() -> dict:
    """영구 동의어 캐시 로드 (사용자 박제 2026-07-01): 한 번 학습한 정식명은 rate-limit 무관 재사용."""
    try:
        if _SYNONYM_FILE.exists():
            return json.loads(_SYNONYM_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


_SYNONYM_CACHE: dict = _load_synonym_cache()


def _expand_theme(theme: str) -> list:
    """주제 → 한국 공식 통계·정부자료 *검색용 정식 명칭·동의어* (LLM, 하드코딩 0).
    ★ 사용자 박제 2026-07-01: KOSIS 는 '지역사랑상품권'으로 인덱싱돼 '지역화폐'로는 0건.
    정식 명칭을 검색어·관련성 토큰에 포함해 *수율 안정화* + 동의어 관련성 인정.

    ★ 캐시 두 계층화: throttle 실패는 캐시 미저장(다음 호출 재시도),
      LLM 정상 응답은 [] 포함 영구 저장(확정 빈값 = 재호출 낭비 없음)."""
    theme = (theme or "").strip()
    if not theme:
        return []
    if theme in _SYNONYM_CACHE:   # [] 포함 — 확정 결과는 캐시 히트로 LLM 0회
        return _SYNONYM_CACHE[theme]
    syns = []
    _throttled = False
    _prompt = (
        f'"{theme}" 를 한국 정부통계(KOSIS)·공식자료에서 검색할 때 쓰는 *정식 명칭·동의어* 를 '
        f'최대 3개 알려줘 (예: "지역화폐"→"지역사랑상품권", "전기차"→"전기자동차"). '
        f'"{theme}" 자체가 이미 정식명이면 유사·상위 개념어라도 1~2개. 정말 없을 때만 빈 배열.\n'
        'JSON만: {"terms": ["정식명칭1", ...]}')
    try:
        from shared.llm import invoke_text
        # ★ _nonessential=True: 스로틀 시 즉시 폴백, circuit breaker 카운트 제외
        raw = invoke_text("analyzer", _prompt, max_tokens=120, temperature=0, _nonessential=True)
        if not raw or not raw.strip():
            # 빈 응답 = 회로차단기 선제 차단 or API 스로틀 — 캐시 미저장, 다음 호출 재시도
            _throttled = True
            log.warning(f"[chart_data] '{theme}' 동의어 확장 빈 응답(스로틀) → 동의어 없이 통과")
        else:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                terms = json.loads(m.group(0)).get("terms") or []
                syns = [str(t).strip() for t in terms if str(t).strip() and str(t).strip() != theme][:3]
    except Exception as e:
        _throttled = True
        log.warning(f"[chart_data] 동의어 확장 실패: {e}")

    if not _throttled:
        # 정상 응답([] 포함) → 확정 결과 캐시 + 파일 영구 저장
        _SYNONYM_CACHE[theme] = syns
        try:
            _SYNONYM_FILE.write_text(json.dumps(_SYNONYM_CACHE, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
        except Exception:
            pass
    # throttle 시: _SYNONYM_CACHE 미저장 → 다음 호출 시 재시도(캐시 미스 → LLM 재요청)

    if syns:
        log.info(f"[chart_data] '{theme}' 동의어 확장 → {syns}")
    return syns


def warm_synonyms(themes: list) -> dict:
    """주제 목록의 동의어를 미리 확장·캐시. topic_pack 빌드 시 선행 호출용.

    chart_data 파이프라인 LLM 부하 시작 전(topic_pack 생성 시점)에 실행하여
    위상 분리 — collect_chart_data 진입 시 _expand_theme 가 캐시 히트 → LLM 0회."""
    result = {}
    for t in (themes or []):
        t = (t or "").strip()
        if t:
            result[t] = _expand_theme(t)
    return result


_DEMO_TOTAL = {"전체", "계", "합계", "소계", "총계", "전국", "평균"}


def _clean_label(lab: str) -> str:
    """차트 라벨 정리 — 괄호 설명 제거 + 길이 제한 (짤림·장황함 방지)."""
    lab = re.sub(r"\s*[(（][^)）]*[)）]", "", str(lab or "")).strip()
    lab = re.sub(r"\s+", " ", lab)
    return lab[:22]


def _is_demo_value(s: str) -> bool:
    """세그먼트가 인구통계 분류값(성별·지역·연령)인지 — 교차표 축약 시 차원 식별용."""
    s = str(s or "").strip()
    if s in _DEMO_TOTAL:
        return True
    return bool(re.search(r"(남자|여자|^남$|^여$|[동읍면시군]부|\d+\s*[~∼\-]\s*\d+\s*세|\d+세|\d+대|"
                          r"이상|미만|수도권|비수도권|특별시|광역시|^.{1,4}도$|^.{1,5}시$|^.{1,4}군$)", s))


def _is_period(s: str) -> bool:
    s = str(s or "").strip()
    return bool(re.match(r"^(19|20)\d{2}", s) or re.match(r"^\d{6}$", s))


def _fmt_period(p: str) -> str:
    """period 코드를 읽기 쉬운 라벨로 정규화 (202505→'2025.5', 2025→'2025').
    이미 읽기 쉬운 형태('2025년 5월' 등)는 그대로 둔다."""
    p = str(p or "").strip()
    m = re.match(r"^(20\d{2})([01]\d)$", p)       # YYYYMM 6자리 코드
    if m:
        return f"{m.group(1)}.{int(m.group(2))}"
    if re.fullmatch(r"(19|20)\d{2}", p):          # 순수 연도 4자리
        return p
    return p


def _reduce_crosstab(rows: list, max_rows: int = 7) -> tuple[list, bool]:
    """KOSIS 교차표(성별·지역·업종 × 응답 × 연도 등 다차원 셀)를 *읽을 수 있는 형태* 로 축약.

    반환: (rows, is_temporal).
      - is_temporal=True  → rows 는 *시점(period) 축 시계열* (label=정규화 period, 오름차순, 최근 12점).
        호출부는 이 경우 viz_hint="line_chart" 로 렌더해 추이·맥락을 살린다.
      - is_temporal=False → rows 는 기존 *단일 분포* (막대차트용, 최대 max_rows개).

    ★ 사용자 박제 2026-07-01: 30셀 덤프 금지. '전체/계' 값을 가진 *모든* 차원을 전체로 collapse.
    ★ 사용자 박제 2026-07-17: 시계열 보존. 최신 1점 스냅샷으로 붕괴시키던 결함 수정 — 서로 다른
      시점이 3개 이상이고 비-period 차원(rest)이 '전체/계'로 collapse 가능하거나 rest 가 단일
      계열이면, period 를 라벨 축으로 유지해 시계열 라인차트로 만든다."""
    parsed = []
    for r in rows:
        segs = [s.strip() for s in re.split(r"·", str(r.get("label", ""))) if s.strip()]
        period, rest = None, []
        for s in segs:
            if _is_period(s):
                period = s
            else:
                rest.append(s)
        if not rest:
            continue
        parsed.append({"rest": rest, "period": period, "value": r.get("value")})
    if not parsed:
        return ([{"label": _clean_label(r.get("label", "")), "value": r.get("value")}
                 for r in rows[:max_rows]], False)

    periods = sorted({p["period"] for p in parsed if p["period"]})

    # ── ★ 시계열 분기 (사용자 박제 2026-07-17): 서로 다른 시점 3개 이상 + rest 가 '전체/계'로
    #    collapse 가능하거나 단일 계열이면 → period 를 라벨 축으로 유지하는 시계열 행 생성. ──────
    if len(periods) >= 3:
        # (a) rest 전부 '전체/계' 인 행 (성별·지역 등 세부 차원 없이 전체 집계만인 시점들)
        total_rows = [p for p in parsed
                      if p["period"] and p["rest"] and all(s in _DEMO_TOTAL for s in p["rest"])]
        # (b) 비-period rest 조합이 단일 계열인지 (시점마다 값 하나로 대응)
        rest_keys = {tuple(p["rest"]) for p in parsed if p["period"]}
        temporal_src = None
        if len({p["period"] for p in total_rows}) >= 3:
            temporal_src = total_rows                       # '전체/계' 로 collapse
        elif len(rest_keys) == 1:
            temporal_src = [p for p in parsed if p["period"]]  # rest 단일 계열
        if temporal_src:
            by_period: dict = {}
            for p in temporal_src:
                by_period[p["period"]] = p["value"]         # 같은 period 중복 시 마지막 값
            ordered = sorted(by_period.items(), key=lambda kv: str(kv[0]))[-12:]  # 오름차순·최근 12점
            ts_rows = [{"label": _fmt_period(pr), "value": v}
                       for pr, v in ordered if v is not None]
            if len(ts_rows) >= 3:
                return (ts_rows, True)

    # ── 이하 비시계열: 최신 시점만 남기고 단일 분포로 축약 (기존 로직) ──────────────────────
    # 1) 최신 시점만 (연도 혼재 방지)
    if periods:
        latest = periods[-1]
        kept = [p for p in parsed if p["period"] in (latest, None)]
        if kept:
            parsed = kept

    maxseg = max(len(p["rest"]) for p in parsed)
    if maxseg >= 2:
        # 2) '전체/계' 값을 가진 *차원 위치* 식별 (성별·지역·업종 무엇이든)
        total_pos = []
        for pos in range(maxseg):
            vals = {p["rest"][pos] for p in parsed if len(p["rest"]) > pos}
            if vals & _DEMO_TOTAL:
                total_pos.append(pos)
        if total_pos:
            # 3) 전체 차원은 모두 '전체/계'로 고정 → 나머지(응답) 차원만 라벨로
            ans_positions = [i for i in range(maxseg) if i not in total_pos]
            out, seen = [], set()
            for p in parsed:
                if len(p["rest"]) < maxseg:
                    continue
                if not all(p["rest"][tp] in _DEMO_TOTAL for tp in total_pos):
                    continue
                lab = _clean_label(" ".join(p["rest"][i] for i in ans_positions)) if ans_positions \
                    else _clean_label(p["rest"][total_pos[0]])
                if not lab or lab in seen:
                    continue
                seen.add(lab)
                out.append({"label": lab, "value": p["value"]})
            if len(out) >= 2:
                return (out[:max_rows], False)

    # 4) 폴백(전체 차원 없음): 응답명만 라벨로 dedup
    out, seen = [], set()
    for p in parsed:
        lab = _clean_label(" ".join(p["rest"]))
        if not lab or lab in seen:
            continue
        seen.add(lab)
        out.append({"label": lab, "value": p["value"]})
    # ★ 1D 테이블 합계 행 분리: "전체/계" 라벨과 세부 항목이 섞이면 합계 행 제거.
    # "전체: 355, 정보통신업: 136, ..." → 전체는 합계이지 비교 대상이 아님.
    # maxseg<2 인 1D 테이블(교차표 아님)에서도 동일 원칙 적용.
    if len(out) > 2:
        non_total = [r for r in out if r["label"] not in _DEMO_TOTAL]
        if len(non_total) >= 2:
            out = non_total
    return (out[:max_rows], False)


def _parse_clean_doc(doc):
    """KOSIS 등 '[KOSIS 통계표: ...] / 라벨: 값 단위' 정형 텍스트를 LLM 없이 직접 파싱 (빠름).
    반환 dataset 또는 None. (LLM 추출 호출 폭증 방지 — 풍부 수집 속도 핵심)

    ★ 단위 혼용 분리 (사용자 박제 2026-07-11): 행별 단위가 다른 경우(회사수=개, 자산총계=백만원)
      단위 그룹별로 별도 dataset 생성 → 첫 번째(또는 대표) dataset 반환.
      단위가 "개"인 재무 항목(값>100,000)은 단위를 "백만원"으로 자동 정정.
    """
    text = getattr(doc, "raw_text", "") or ""
    if "[KOSIS 통계표:" not in text:
        return None
    title = getattr(doc, "title", "") or "KOSIS"
    for pre in ("KOSIS 통계청 — ", "통계청 KOSIS — "):
        if title.startswith(pre):
            title = title[len(pre):]
    title = re.sub(r"\s*[(（][^)）]*[)）]", "", title).strip() or "KOSIS 통계"   # 제목 괄호 설명 제거
    # 헤더 단위 (폴백)
    _header_unit = ""
    mu = re.search(r"단위:\s*([^)\]]+)", text)
    if mu:
        _header_unit = mu.group(1).strip()

    # ★ 행별 단위 파싱 (m.group(3) = 행 끝 단위 토큰)
    rows_by_unit: dict[str, list] = {}   # unit → [{label, value}]
    for line in text.splitlines():
        m = re.match(r"\s+(.+?):\s*(-?[\d.,]+)\s*(\S*)\s*$", line)
        if not m:
            continue
        lab = m.group(1).strip()
        try:
            val = round(float(m.group(2).replace(",", "")), 2)
        except ValueError:
            continue
        row_unit = m.group(3).strip() if m.group(3) else _header_unit
        # ★ 단위 자동 정정: "개"이지만 재무 항목(값>100,000)이면 백만원으로 정정
        _fin_kws = {"자산", "부채", "자본", "매출", "이익", "영업", "경상", "당기", "순이익"}
        if row_unit == "개" and val > 100_000 and any(kw in lab for kw in _fin_kws):
            row_unit = "백만원"
        rows_by_unit.setdefault(row_unit, []).append({"label": lab, "value": val})

    if not rows_by_unit:
        return None

    # 단위 그룹 중 가장 많은 항목을 가진 그룹 선택 (단, "개" 그룹이 재무 그룹보다 작으면 재무 우선)
    _fin_units = {"백만원", "억원", "조원", "원", "%"}
    def _group_priority(u):
        if u in _fin_units:
            return (0, -len(rows_by_unit[u]))   # 재무 단위 우선
        return (1, -len(rows_by_unit[u]))
    best_unit = sorted(rows_by_unit, key=_group_priority)[0]
    rows = rows_by_unit[best_unit]
    unit = best_unit

    if len(rows) < 2:
        return None
    # ★ 교차표(30셀 덤프) → 시계열(라인) 또는 읽을 수 있는 단일 분포(≤7개 막대)로 축약
    rows, _is_temporal = _reduce_crosstab(rows, max_rows=7)
    if len(rows) < 2:
        return None
    # ★ 다질문 표 감지 (사용자 박제 2026-07-01): 한 KOSIS 표에 여러 질문(인지도+구매한도 등)이
    #   섞이면 비율 합이 100%를 크게 초과(합계 200% 도넛 사고). 이 경우 fast-path 포기 → LLM 추출이
    #   *하나의 일관된 질문* 만 골라내게 위임 (None 반환 시 _extract_series_from_docs LLM 경로로 진행).
    #   ★ 시계열은 시점 나열이라 '비율 합계' 개념이 없으므로 이 게이트 미적용 (지수 12시점 합≫140 오탐 방지).
    if not _is_temporal:
        _vals = [r["value"] for r in rows]
        if _vals and all(0 <= v <= 100 for v in _vals) and sum(_vals) > 140:
            return None
    # ★ as_of: 오늘 날짜가 아닌 수집된 데이터의 실제 최신 날짜 박제 (사용자 박제 2026-07-11)
    # "202605", "202509" 형태 날짜 코드에서 최신값 추출 → "2026년 5월" 같은 형태로 표시
    _date_codes = re.findall(r'\b(20\d{2})([01]\d)\b', text)   # (년, 월) 튜플 리스트
    if _date_codes:
        _latest = max(_date_codes)
        _as_of = f"{_latest[0]}년 {int(_latest[1])}월"
    else:
        _as_of = _now_as_of()
    src = {"provider": "kosis", "name": "통계청 KOSIS",
           "url": getattr(doc, "url", "") or "https://kosis.kr/", "as_of": _as_of}
    _viz = "line_chart" if _is_temporal else "bar_chart"   # ★ 시계열이면 라인차트로 추이 표현
    return _mk_dataset(title[:40], _viz, unit, rows, src)


def _korean_num_variants(fv: float) -> list[str]:
    """float 값의 한국어 조/억/만/천/백 단위 표기 후보 반환.

    출처 문서가 아라비아 숫자 대신 "11만5천", "5천661", "1백29", "3억2천만" 등으로
    표기한 경우도 grounding 이 통과하도록 가능한 표기 변형을 모두 생성.
    콤마(110,000 / 110000)는 _value_grounded ① 에서 nc(콤마제거) 로 이미 처리.

    예:
      115000   → ["11만5000","11만 5000","11만5천","11만 5천"]
      155661   → ["15만5661","15만 5661","15만5천661","15만5천6백61", ...]
      5000     → ["5000","5천"]
      129      → ["129","1백29"]
      50000000 → ["5000만","5천만"]
    """
    if fv != int(fv) or fv <= 0 or fv >= 1e16:
        return []
    v = int(fv)

    def _천형(n: int) -> list[str]:
        """0-9999 → 천/백 포함 한국어 표기 + 숫자. 0 → [""]."""
        if n == 0:
            return [""]
        forms = [str(n)]
        천 = n // 1000
        남_천 = n % 1000
        백 = 남_천 // 100
        남_백 = 남_천 % 100
        if 천 > 0:
            p천 = "천" if 천 == 1 else f"{천}천"
            forms.append(p천 + (str(남_천) if 남_천 else ""))        # "5천661"
            if 백 > 0:
                p백 = "백" if 백 == 1 else f"{백}백"
                forms.append(p천 + p백 + (str(남_백) if 남_백 else ""))  # "5천6백61"
        elif 백 > 0:
            p백 = "백" if 백 == 1 else f"{백}백"
            forms.append(p백 + (str(남_백) if 남_백 else ""))        # "1백29"
        return list(dict.fromkeys(forms))

    def _만형(n: int) -> list[str]:
        """0-99,999,999 → 만/천/백 포함 한국어 표기. 0 → [""]."""
        if n == 0:
            return [""]
        만_cnt = n // 10_000
        남_만 = n % 10_000
        if 만_cnt == 0:
            return _천형(n)
        만_cnts = _천형(만_cnt)   # 만 개수 자체의 천 표기 (5000만→5천만)
        남_forms = _천형(남_만)
        forms = []
        for mc in 만_cnts:
            만_str = mc + "만"
            for s in 남_forms:
                forms.append(만_str + s)
                if s:
                    forms.append(만_str + " " + s)
        return list(dict.fromkeys(forms))

    results: list[str] = []
    조 = v // 1_000_000_000_000
    rem_조 = v % 1_000_000_000_000
    억 = rem_조 // 100_000_000
    rem_억 = rem_조 % 100_000_000

    if 조 > 0:
        조_str = f"{조}조"
        if rem_조 == 0:
            results.append(조_str)
        elif 억 == 0:
            for s in _만형(rem_조):
                results.append(조_str + s)
                if s:
                    results.append(조_str + " " + s)
        else:
            억_str = f"{억}억"
            for s in _만형(rem_억):
                results.append(조_str + 억_str + s)
                if s:
                    results.append(조_str + 억_str + " " + s)
            if not rem_억:
                results.append(조_str + 억_str)
    elif 억 > 0:
        억_str = f"{억}억"
        if rem_억 == 0:
            results.append(억_str)
        else:
            for s in _만형(rem_억):
                results.append(억_str + s)
                if s:
                    results.append(억_str + " " + s)
    else:
        results.extend(_만형(v))

    return list(dict.fromkeys(r for r in results if r and r.strip()))


def _value_grounded(value, doc_text: str) -> bool:
    """★ 수치 grounding: LLM이 낸 value 가 출처 본문에 *형식·근사 무관* 등장하는지.

    ① 정확 매칭: 다양한 형식(콤마 제거·소수·정수부·유효숫자)으로 문자열 검색
    ② 한국어 만/억 단위 표기 매칭: "11만5000" → 115000 인정
    ③ 근사 매칭: 문서 내 모든 수치와 ±5% / 표시자리 올림·버림 비교 (models.grounds 재사용)
       "약 1030" → 1031 팩트로 인정, 스케일 10배 차이는 거부.
    본문 없음/파싱불가 = 판정 보류(통과)."""
    if not doc_text:
        return True
    try:
        fv = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return True
    nc = doc_text.replace(",", "")
    # ① 정확 매칭 (형식 변형 포함)
    cands = {str(fv), f"{fv:.1f}", f"{fv:.2f}"}
    if fv == int(fv):
        cands.add(str(int(fv)))
    for c in cands:
        if c and c in nc:
            return True
    ip = str(int(abs(fv)))                       # 정수부 (형식·스케일 변형 대비)
    if len(ip) >= 2 and ip in nc:
        return True
    sig = str(fv).replace(".", "").lstrip("0")   # 유효숫자열 (예: 45.2 → 452)
    if len(sig) >= 3 and sig in nc:
        return True
    # ② 한국어 만/억 단위 표기 매칭 — "11만5000" → 115000 (LLM이 아라비아 숫자로 변환한 경우)
    # nc(콤마 제거)에서도 검색 — "3만7,000" → nc에서 "3만7000" 매칭
    for kor in _korean_num_variants(fv):
        if kor and (kor in doc_text or kor in nc):
            return True
    # ③ 근사 매칭 — 문서 내 수치 전수 비교, ±5%/올림·버림 허용 (약 1030 → 1031 통과)
    try:
        from JARVIS09_COLLECTOR.models import grounds as _gnd
        for _m in re.findall(r'\d+(?:\.\d+)?', nc):
            try:
                if _gnd(fv, float(_m)):
                    return True
            except Exception:
                pass
    except ImportError:
        pass
    return False


def _extract_series_from_docs(series: dict, docs: list):
    """수집 문서에서 *해당 series 에 집중* 추출 → dataset(출처 URL·단위 박제). 없으면 None."""
    if not docs:
        return None
    # ★ KOSIS 정형 데이터는 LLM 없이 직접 파싱 (속도 — 풍부 수집 시 LLM 폭증 방지)
    if len(docs) == 1:
        fast = _parse_clean_doc(docs[0])
        if fast:
            return fast
    sel = docs[:8]
    excerpts = "\n\n".join(
        f"[{i}] ({getattr(d, 'source_type', '')}) {getattr(d, 'title', '')}\n"
        f"{(getattr(d, 'raw_text', '') or getattr(d, 'cleaned_text', '') or '')[:1500]}"
        for i, d in enumerate(sel))
    try:
        from shared.llm import invoke_text
        # ★ _nonessential=True: 스로틀 시 즉시 폴백, circuit breaker 카운트 제외
        raw = invoke_text("analyzer",
                          _SERIES_PROMPT.format(name=series["name"], unit=series.get("unit", ""), excerpts=excerpts),
                          system=_SERIES_SYSTEM, max_tokens=700, temperature=0.1, _nonessential=True)
    except Exception as e:
        log.warning(f"[chart_data] series 추출 LLM 실패: {e}")
        return None
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return None
    rows, src_url, src_name = [], "", ""
    # grounding 전체 문서 합본 (source_idx 오지정 대비 — 실제 수치가 다른 문서에 있는 경우)
    _all_dtext = " ".join(
        getattr(d, "raw_text", "") or getattr(d, "cleaned_text", "") or ""
        for d in sel
    )
    for r in parsed.get("data") or []:
        try:
            doc = sel[int(r.get("source_idx"))]
        except (TypeError, ValueError, IndexError):
            continue
        # ★ 수치 grounding: LLM이 낸 value 가 출처 본문에 실제 등장하는지 (환각 차단)
        # source_idx 지정 문서 우선 → 없으면 전체 수집 문서에서 재검색 (source_idx 오지정 보완)
        _val = r.get("value")
        _dtext = (getattr(doc, "raw_text", "") or getattr(doc, "cleaned_text", "") or "")
        if not _value_grounded(_val, _dtext) and not _value_grounded(_val, _all_dtext):
            log.warning(f"[chart_data] 환각 수치 드롭: {r.get('label')}={_val} (전체 문서 미등장)")
            continue
        if not src_url:
            src_url = getattr(doc, "url", "") or ""
            src_name = getattr(doc, "source_type", "") or ""
        rows.append({"label": str(r.get("label", "")), "value": _val})
    # ★ LLM 추출 라벨도 교차표 축약 (사용자 박제 2026-07-01): LLM이 '합계·신용카드' / '읍소재시장·
    #   신용카드' 처럼 전체+특정 차원을 섞어 반환하면 _reduce_crosstab 으로 전체 차원 collapse →
    #   '신용카드' 만 남김 (KOSIS fast-path 와 동일 정리). 단일 차원이면 그대로 보존.
    rows, _is_temporal = _reduce_crosstab(rows, max_rows=8)
    # ★ 단위 일관성 (사용자 박제 2026-07-01): LLM이 공통 단위를 못 정하면(=비교 불가 수치 짬뽕)
    #   그 차트는 버린다. 단위 없는 무의미 막대(부가가치 1.9 + 비용 1.88 …) 원천 차단.
    _llm_unit = str(parsed.get("unit", "") or "").strip()
    _unit = _llm_unit or str(series.get("unit", "") or "").strip()
    if len(rows) < 2 or not src_url or not _unit:
        return None
    # ★ as_of: 출처 문서에서 최신 날짜 코드 추출 (오늘 날짜 아닌 실제 데이터 기준)
    _ref_doc = sel[0] if sel else None
    _ref_text = (getattr(_ref_doc, "raw_text", "") or "") if _ref_doc else ""
    _dc = re.findall(r'\b(20\d{2})([01]\d)\b', _ref_text)
    _as_of_s = f"{max(_dc)[0]}년 {int(max(_dc)[1])}월" if _dc else _now_as_of()
    src = {"provider": src_name or "web", "name": src_name or "web", "url": src_url, "as_of": _as_of_s}
    viz = {"line": "line_chart", "bar": "bar_chart", "stat": "kpi", "donut": "pie_chart"}.get(series.get("chart"), "bar_chart")
    if _is_temporal:   # ★ 교차표가 시계열로 축약됐으면 라인차트 강제 (설계 chart 무관)
        viz = "line_chart"
    return _mk_dataset(series["name"], viz, _unit, rows, src)


# 일반 경제어 — 관련성 토큰에서 제외(과대매칭 방지). *주제 고유명사*만 토큰화.
_GENERIC_TOKENS = {
    "소비", "가격", "물가", "경제", "시장", "지수", "효과", "활성화", "동향", "실태",
    "조사", "행태", "트렌드", "동조성", "현황", "추이", "비교", "규모", "전망", "분석",
    "통계", "지표", "변화", "증가", "감소", "월별", "분기", "연간", "전국", "시도",
    "관련", "주요", "최근", "수준", "전체", "평균", "합계", "구성", "비율", "지원",
    "정책", "사업", "운영", "이용", "현재", "기준", "항목", "결과", "수치", "데이터",
}


def _specific_tokens(text: str) -> set:
    """주제 고유명사 토큰(len≥2, 일반어 제외) — 관련성 1차(결정론) 판정용."""
    return {t for t in re.split(r"[\s·,()/\-—]+", str(text or ""))
            if len(t) >= 2 and t not in _GENERIC_TOKENS}


def _doc_title_relevant(title: str, ref_tokens: set) -> bool:
    """표/문서 제목이 주제 고유명사와 겹치면 관련. 겹침 0 → 무관(농촌관광·식품소비 차단).

    ★ 부분문자열 낚임 방지 (2026-07-17): 2글자 짧은 토큰은 *완전 토큰 일치* 만 인정.
      '경제' 프로필 정의문에서 새어나온 '생산'·'활동' 같은 2글자 일반어가 '농업생산활동'
      제목에 부분문자열로 걸려 무관 표가 통과하던 사고 근절. 3글자+ 고유명사(반도체 등)는
      '반도체산업' 같은 복합어 매칭을 위해 부분문자열 매칭 유지.
    """
    if not ref_tokens:
        return True
    title = str(title or "")
    toks = _specific_tokens(title)
    return any((len(rt) >= 3 and rt in title) or (rt in toks) for rt in ref_tokens)


def _relevance_filter(theme: str, description: str, datasets: list) -> list:
    """★ 의미 기반 관련성 게이트 (사용자 박제 2026-07-01): 주제와 *직접* 관련된 dataset만 남김.
    농촌관광·식품소비행태처럼 다른 주제를 다루며 주제를 스쳐 언급할 뿐인 표를 제거.
    ★ 재시도 3회 (사용자 박제 2026-07-01): LLM 일시 실패 시 fail-open 으로 농촌관광이 새던 사고
    근절 — 게이트가 *확실히* 한 번은 판정하게 재시도. 동의어(지역화폐↔지역사랑상품권)는 의미 판단.
    최종 실패 시에만 결정론 백스톱(주제 핵심 토큰 미포함 표 제거)."""
    if len(datasets) <= 1:
        return datasets
    listing = "\n".join(f"{i}. {d.get('title', '')}" for i, d in enumerate(datasets))
    prompt = (
        f'블로그 글 주제: "{theme}"' + (f" — {description}" if description else "") + "\n\n"
        f'아래 데이터 표 목록 중, "{theme}" 자체를 다루는 표의 번호만 고르세요.\n'
        "판단 기준 (엄격):\n"
        f'- "{theme}"(또는 그 공식명칭·동의어, 예 \'지역화폐\'≈\'지역사랑상품권\')를 *직접* 다루면 관련.\n'
        "- *다른 주제*(예: 농촌관광·식품소비행태·인구·관광·날씨)를 다루는 표는, 주제를 스쳐 언급하거나\n"
        f'  같은 지역/경제권이어도 *제외*. 표의 핵심 대상이 "{theme}" 가 아니면 무조건 제외.\n'
        "- 애매하면 제외(주제 일관성 최우선).\n\n"
        f"{listing}\n\n"
        'JSON만 출력: {"keep": [관련 번호 목록]}'
    )
    for _attempt in range(2):   # ★ 재시도 — JSON 파싱 실패 시만 (ERRORS [399] 동일 패턴)
        try:
            from shared.llm import invoke_text
            # ★ _nonessential=True: 스로틀 시 즉시 폴백, circuit breaker 카운트 제외
            raw = invoke_text("analyzer", prompt, max_tokens=200, temperature=0, _nonessential=True)
            if not raw or not raw.strip():
                log.warning(f"[chart_data] '{theme}' 관련성 게이트 빈 응답(스로틀) → 결정론 백스톱")
                break
            m = re.search(r"\{[\s\S]*\}", raw)
            if not m:
                continue
            keep = json.loads(m.group(0)).get("keep")
            if not isinstance(keep, list):
                continue
            idx = {int(i) for i in keep if isinstance(i, int) or str(i).strip().isdigit()}
            filtered = [d for i, d in enumerate(datasets) if i in idx]
            if filtered:
                if len(filtered) < len(datasets):
                    log.info(f"[chart_data] 관련성 게이트: {len(datasets)}→{len(filtered)}개 "
                             f"(제외 {len(datasets) - len(filtered)})")
                return filtered
            # ★ 전부 무관 → 빈 풀 반환 (사용자 박제 2026-07-01): 농촌관광 등 오답 차트 < 차트 없음.
            #   '빈 풀 방지'로 원본 유지하던 것이 누수원이었음 — 차라리 차트 없이 AI사진으로 대체.
            log.warning(f"[chart_data] 관련성 게이트: '{theme}' 무관 {len(datasets)}개 전량 폐기 (빈 풀)")
            return []
        except Exception as e:
            log.warning(f"[chart_data] 관련성 게이트 LLM 시도{_attempt + 1} 실패: {e}")
    # 3회 모두 실패 → 결정론 백스톱: 주제 핵심 토큰 미포함 표 제거 (농촌관광 등 차단)
    core = _specific_tokens(theme)
    if core:
        kept = [d for d in datasets if _doc_title_relevant(d.get("title", ""), core)]
        if kept:
            log.warning(f"[chart_data] 관련성 게이트 LLM 전패 → 결정론 백스톱 {len(datasets)}→{len(kept)}")
            return kept
    return datasets


def _query_candidates(series: dict, theme: str) -> list[str]:
    """검색 쿼리 후보 — 구체적 → 점진적으로 넓게 (긴 쿼리는 뉴스검색 0건 → 짧게 재시도)."""
    q = (series.get("query") or series["name"]).strip()
    toks = q.split()
    cands = [q]
    if len(toks) > 3:
        cands.append(" ".join(toks[:3]))
    if len(toks) > 2:
        cands.append(" ".join(toks[:2]))
    if theme and theme not in cands:
        cands.append(theme)
    # 중복 제거(순서 유지)
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c); out.append(c)
    return out


# 텍스트 출처 — 제목 관련성 1차 필터 적용 대상 (finance 류는 제목이 종목/지수명이라 면제)
_TEXT_SOURCES = {"kosis", "academic", "kci", "kor_econ", "naver_news", "news", "web"}

# ★ 소스 신뢰도 순위 — LLM 설계 순서 무관, 항상 이 순위로 재정렬 (ERRORS [421])
# 근거: ADR 013 "신뢰순위 논문>API>뉴스>기사>웹". LLM이 ["web","kosis"] 설계해도
# 실행은 항상 kosis → web 순서. web/blog가 먼저 실행되어 틀린 수치 채택 사고 원천 차단.
_SOURCE_TRUST_RANK: dict[str, int] = {
    "finance": 1,     # yfinance — 공식 금융 API
    "krx":     1,     # 한국거래소 공식 API
    "ecos":    1,     # 한국은행 ECOS 공식 API
    "dart":    2,     # 금감원 전자공시 API
    "kosis":   2,     # 통계청 공식 통계 DB
    "kor_econ":3,     # 정부 보도자료
    "academic":3,     # 학술 논문 (arXiv 등)
    "kci":     3,     # 국내 학술논문
    "naver_news":4,   # 네이버 뉴스
    "news":    4,     # 언론사 뉴스
    "discover":5,     # 웹 발견 (구글·네이버 검색)
    "web":     6,     # 웹 (낮은 신뢰도)
    "blog":    7,     # 블로그 (최저 신뢰도)
}

# 시장 지표 키워드 — discover 웹 폴백 차단 대상 (ERRORS [421])
# 이 키워드가 series name/query에 있으면 API/kosis 실패해도 웹 검색 폴백 금지.
# 웹에서 가져온 시장 수치는 검증 불가 → 차트 없는 게 틀린 차트보다 낫다.
_NO_WEB_FALLBACK_KWS = frozenset([
    "시가총액", "코스닥", "코스피", "주가지수", "지수", "증시",
    "기준금리", "환율", "달러", "나스닥", "s&p",
    "업종별", "섹터", "업종비중", "코스닥 150", "섹터지수",
    "kosdaq", "kospi", "nasdaq",
    # ★ 주간 등락률 — web 크롤링 수치 완전히 틀림 (ERRORS [424])
    "등락률", "주간등락률", "등락", "수익률", "주간수익률",
    "주간변동", "이번주", "금주",
])


def _collect_docs_for_series(series: dict, sector: str, theme: str = "", ref_tokens: set = None):
    """한 series 를 신뢰도 순으로 조준 수집 — LLM 없이 문서만 반환.

    KOSIS 정형 fast-path 성공 시 {"fast_dataset": ds} 반환 (배치 불필요).
    그 외 {"series": series, "docs": docs, "source": src} → 배치 추출 큐에 투입.
    수집 실패 시 None.
    """
    queries = _query_candidates(series, theme)
    _ser_tokens = set(ref_tokens or set()) | _specific_tokens(
        f"{series.get('name', '')} {series.get('query', '')}")

    raw_sources = series.get("sources", [])
    sources_sorted = sorted(raw_sources, key=lambda s: _SOURCE_TRUST_RANK.get(s, 99))

    for source in sources_sorted:
        _ensure_source_ready(source)
        prov = _get_provider(source)
        if not prov:
            continue
        docs = []
        for q in queries:
            docs = _cached_collect(prov, source, q, sector, 10)
            if docs:
                break
        if source in _TEXT_SOURCES and docs and _ser_tokens:
            rel = [d for d in docs if _doc_title_relevant(getattr(d, "title", ""), _ser_tokens)]
            if not rel:
                log.info(f"[chart_data] '{series['name']}' ← {source}: 관련 표 0 (엉뚱한 표만) → 스킵")
                continue
            docs = rel
        if not docs:
            continue
        # ★ KOSIS 단일 문서 fast-path — LLM 없이 파싱 가능하면 배치 불필요
        if len(docs) == 1:
            fast = _parse_clean_doc(docs[0])
            if fast:
                log.info(f"[chart_data] '{series['name']}' ← KOSIS fast-path ({len(fast['data'])}점)")
                return {"fast_dataset": fast}
        return {"series": series, "docs": docs[:8], "source": source}

    # ★ discover 웹 폴백 — 시장 지표 series는 차단
    _combined = (series.get("name", "") + " " + series.get("query", "")).lower()
    if any(kw in _combined for kw in _NO_WEB_FALLBACK_KWS):
        log.info(f"[chart_data] '{series['name']}' 시장지표 — discover 웹 폴백 차단.")
        return None
    if "discover" not in series.get("sources", []):
        _ensure_source_ready("discover")
        prov = _get_provider("discover")
        if prov:
            for q in queries[:2]:
                docs = _cached_collect(prov, "discover", q, sector, 6)
                if docs:
                    return {"series": series, "docs": docs[:8], "source": "discover"}
    return None


_BATCH_SYSTEM = """당신은 수집 자료에서 *실제로 등장한 수치*만 뽑아 차트 데이터로 구조화하는 분석가다.
수치는 반드시 자료 원문에 있어야 하며, 주제와의 관련성도 함께 판정한다.
같은 단위·같은 종류 수치만 한 차트에 묶는다."""


def _batch_extract_all(pending_items: list, theme: str) -> list[dict]:
    """여러 수집 항목을 단일 LLM 호출로 일괄 추출 + 관련성 판정.

    pending_items: [{"series": dict, "docs": list, "source": str}, ...]
    Returns: 관련 있고 grounding 통과한 dataset 목록.
    """
    if not pending_items:
        return []

    item_blocks: list[str] = []
    docs_lookup: dict[int, list] = {}

    for i, item in enumerate(pending_items):
        series = item["series"]
        docs = item.get("docs", [])[:4]   # 항목당 최대 4개 문서
        docs_lookup[i] = []
        excerpts: list[str] = []
        for j, d in enumerate(docs):
            dtext = getattr(d, "raw_text", "") or getattr(d, "cleaned_text", "") or ""
            dtitle = getattr(d, "title", "") or ""
            src_type = getattr(d, "source_type", "") or ""
            docs_lookup[i].append((d, dtext))
            excerpts.append(f"[{i}-{j}] ({src_type}) {dtitle}\n{dtext[:500]}")
        item_blocks.append(
            f"[ITEM {i}] 지표: {series['name']}  (단위힌트: {series.get('unit', '') or '?'})\n"
            + "\n".join(excerpts)
        )

    prompt = (
        f'블로그 주제: "{theme}"\n'
        f"아래 {len(pending_items)}개 항목 각각에서 차트 데이터를 추출하고, 이 주제와의 관련성을 판정하라.\n\n"
        + "\n\n".join(item_blocks)
        + "\n\n★ 추출 규칙:\n"
        "1. 자료에 실제로 적힌 수치만. 추정·창작 금지.\n"
        "2. 같은 단위·같은 종류 수치만 한 차트에 (혼합 금지).\n"
        f'3. 이 주제("{theme}")를 *직접* 다루면 relevant=true. 스쳐 언급하면 false.\n'
        "4. 비교 가능한 수치 2개 미만이면 data=[] 처리.\n\n"
        'JSON만:\n{"results":[\n'
        '  {"idx":0,"relevant":true,"unit":"억원","data":[{"label":"2023년","value":1500,"src":"0-0"}]},\n'
        '  {"idx":1,"relevant":false},\n'
        '  ...\n'
        ']}'
    )
    try:
        from shared.llm import invoke_text
        raw = invoke_text("analyzer", prompt, system=_BATCH_SYSTEM,
                          max_tokens=4000, temperature=0.1, _nonessential=True)
    except Exception as e:
        log.warning(f"[chart_data] batch 추출 LLM 실패: {e}")
        return []

    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
    except Exception:
        return []

    results: list[dict] = []
    for r in parsed.get("results") or []:
        idx = r.get("idx")
        if not isinstance(idx, int) or idx >= len(pending_items):
            continue
        if not r.get("relevant", False):
            continue
        data_raw = r.get("data") or []
        if not data_raw:
            continue

        item = pending_items[idx]
        series = item["series"]
        item_docs = docs_lookup.get(idx, [])
        _all_dtext = " ".join(dtext for _, dtext in item_docs)

        rows: list[dict] = []
        src_url, src_name = "", ""
        for d_row in data_raw:
            val = d_row.get("value")
            label = str(d_row.get("label", "")).strip()
            src_ref = str(d_row.get("src", "")).strip()   # "0-0", "0-1" 등

            if not _value_grounded(val, _all_dtext):
                log.warning(f"[chart_data] batch 환각 드롭: [{idx}] {label}={val}")
                continue

            if not src_url:
                if src_ref and "-" in src_ref:
                    try:
                        _j = int(src_ref.split("-")[1])
                        doc, _ = item_docs[_j]
                        src_url = getattr(doc, "url", "") or ""
                        src_name = getattr(doc, "source_type", "") or item.get("source", "web")
                    except (IndexError, ValueError):
                        pass
                if not src_url and item_docs:
                    doc, _ = item_docs[0]
                    src_url = getattr(doc, "url", "") or ""
                    src_name = getattr(doc, "source_type", "") or item.get("source", "web")

            rows.append({"label": label, "value": val})

        rows, _is_temporal = _reduce_crosstab(rows, max_rows=8)
        _unit = str(r.get("unit", "") or "").strip() or str(series.get("unit", "") or "")
        if len(rows) < 2 or not src_url or not _unit:
            continue

        _ref_text = item_docs[0][1] if item_docs else ""
        _dc = re.findall(r'\b(20\d{2})([01]\d)\b', _ref_text)
        _as_of = f"{max(_dc)[0]}년 {int(max(_dc)[1])}월" if _dc else _now_as_of()

        src = {"provider": src_name or "web", "name": src_name or "web",
               "url": src_url, "as_of": _as_of}
        viz = {"line": "line_chart", "bar": "bar_chart", "stat": "kpi",
               "donut": "pie_chart"}.get(series.get("chart"), "bar_chart")
        if _is_temporal:   # ★ 교차표가 시계열로 축약됐으면 라인차트 강제
            viz = "line_chart"
        ds = _mk_dataset(series["name"], viz, _unit, rows, src)
        results.append(ds)
        log.info(f"[chart_data] batch [ITEM {idx}] '{series['name']}' ({len(rows)}점)")

    return results


def _collect_one_series(series: dict, sector: str, theme: str = "", ref_tokens: set = None):
    """한 series 를 신뢰도 순으로 조준 수집. 첫 성공 소스 사용.

    ★ 소스 신뢰도 강제 재정렬 (ERRORS [421]): LLM 설계 순서 무관, _SOURCE_TRUST_RANK 순으로
      항상 재정렬. API(1) → 공식통계(2) → 논문/정부(3) → 뉴스(4) → discover(5) → web(6) → blog(7).
      LLM이 ["web","kosis"] 설계해도 실행은 kosis → web 순서.
    ★ 텍스트 출처: 제목 관련성 필터 — KOSIS 가 엉뚱한 표 반환해도 주제·series 겹치는 것만 채택.
    ★ 시장 지표 discover 폴백 차단 (ERRORS [421]): _NO_WEB_FALLBACK_KWS 키워드 있는 series 는
      API/공식통계 실패해도 웹 검색 폴백 금지. 틀린 차트보다 차트 없음이 낫다.
    """
    queries = _query_candidates(series, theme)
    _ser_tokens = set(ref_tokens or set()) | _specific_tokens(
        f"{series.get('name', '')} {series.get('query', '')}")

    # ★ 핵심: 소스를 신뢰도 순으로 재정렬 (LLM 설계 순서 무시)
    raw_sources = series.get("sources", [])
    sources_sorted = sorted(raw_sources, key=lambda s: _SOURCE_TRUST_RANK.get(s, 99))
    if sources_sorted != raw_sources:
        log.debug(f"[chart_data] '{series['name']}' 소스 재정렬: {raw_sources} → {sources_sorted}")

    for source in sources_sorted:
        _ensure_source_ready(source)
        prov = _get_provider(source)
        if not prov:
            continue
        docs = []
        for q in queries:               # 넓은 쿼리로 재시도 — 0건이면 다음 후보
            docs = _cached_collect(prov, source, q, sector, 10)
            if docs:
                break
        # ★ 텍스트 출처: 제목이 주제·series 고유명사와 겹치는 문서만 (엉뚱한 표 차단)
        if source in _TEXT_SOURCES and docs and _ser_tokens:
            rel = [d for d in docs if _doc_title_relevant(getattr(d, "title", ""), _ser_tokens)]
            if not rel:
                log.info(f"[chart_data] '{series['name']}' ← {source}: 관련 표 0 (엉뚱한 표만) → 스킵")
                continue
            docs = rel
        ds = _extract_series_from_docs(series, docs) if docs else None
        if ds:
            log.info(f"[chart_data] '{series['name']}' ← {source} ({len(ds['data'])}점)")
            return ds

    # ★ discover 웹 폴백 — 시장 지표 series는 차단 (ERRORS [421])
    _combined = (series.get("name", "") + " " + series.get("query", "")).lower()
    _is_market = any(kw in _combined for kw in _NO_WEB_FALLBACK_KWS)
    if _is_market:
        log.info(f"[chart_data] '{series['name']}' 시장지표 — discover 웹 폴백 차단. 차트 없음 선택.")
        return None

    # 일반 주제: 고정 출처 전패 → 웹 발견 폴백 (사용자 박제 2026-07-01)
    if "discover" not in series.get("sources", []):
        _ensure_source_ready("discover")
        prov = _get_provider("discover")
        if prov:
            for q in queries[:2]:
                docs = _cached_collect(prov, "discover", q, sector, 6)
                if docs:
                    ds = _extract_series_from_docs(series, docs)
                    if ds:
                        log.info(f"[chart_data] '{series['name']}' ← discover(웹발견) ({len(ds['data'])}점)")
                        return ds
    return None


# ── 공개 API ──────────────────────────────────────────────────────────────
def collect_chart_data(theme: str, sector: str = "", description: str = "",
                       exclude_titles=None, max_datasets: int = 12,
                       synonyms: list | None = None,
                       related_terms: list | None = None) -> dict:
    """주제 연관 차트용 실데이터를 출처(provenance)와 함께 수집.

    Args:
        theme:          글 주제/키워드 (예: "삼성전자", "반도체").
        sector:         섹터 (선택).
        description:    관련 섹션 본문/설명 — 관련 dataset 우선순위 힌트.
        exclude_titles: 이미 사용한 dataset title 집합 (같은 글 내 중복 방지).
        max_datasets:   반환 최대 dataset 수.
        synonyms:       topic_pack 선행 확장 결과 (있으면 _expand_theme LLM 호출 스킵).
        related_terms:  자비스03 keyword_profile() 관련어 — 종목 dataset 수집 시
                        네이버 금융 공식 테마 매칭에 사용(★ 파운드리→리모델링/인테리어
                        오매칭 재발 방지, ERRORS 재발 — collect_stocks_data 참조).

    Returns:
        {"theme": theme, "datasets": [dataset, ...]}.
        실데이터 없으면 datasets=[] (거짓 데이터 합성 안 함).
    """
    theme = (theme or "").strip()
    if not theme:
        return {"theme": theme, "datasets": []}
    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j09", f"{theme[:12]} 차트수집", ttl=300)   # 안전망 5분 — 실소요 기준 축소
    except Exception:
        pass
    # busy 신호 수명 = 함수 수명 — 종료(성공·실패) 시 finally 에서 즉시 해제 (근본 수정 2026-07-16)
    try:

        _COLLECT_CACHE.clear()   # ★ per-run 수집 캐시 초기화 (이전 주제 잔재 제거 + 이번 run 내 재사용)
        datasets: list[dict] = []
        import time as _time
        _t0 = _time.monotonic()

        def _elapsed(label: str):
            log.info(f"[chart_data] ⏱ {label}: {_time.monotonic() - _t0:.1f}s")

        # ── 0) topic_pack 선행 동의어 (LLM 0) — plan 에 없으면 _plan_desc 에 힌트로 전달
        _syns_param = list(synonyms) if synonyms else []

        # ── 0.5) 거시경제 글로벌 시장 fast-path (LLM 0, plan 없어도 동작) ────────
        #   나스닥·코스피·환율·금리 등 _MACRO_KWS 주제는 get_market_data() 직접 조회.
        #   plan_data_sources 가 timeout/실패해도 항상 실데이터 공급 (ADR 010).
        datasets.extend(_global_market_datasets(theme))
        _elapsed(f"0.5) 글로벌 시장 fast-path (datasets={len(datasets)})")

        # ── 1) 설계 + 조준 수집 ──────────────────────────────────────────────────
        #    plan_data_sources 가 synonyms 도 함께 반환 (LLM 1회로 설계+동의어 통합)
        _plan_desc = (description or "") + ((" / " + " ".join(_syns_param)) if _syns_param else "")
        try:
            from JARVIS09_COLLECTOR.data_planner import plan_data_sources
            _plan_result = plan_data_sources(theme, sector, _plan_desc)
        except Exception as e:
            log.warning(f"[chart_data] 설계 실패: {e}")
            _plan_result = {"series": [], "synonyms": []}

        plan = _plan_result.get("series") or []
        # 동의어: topic_pack 선행 확장이 있으면 그것 우선, 없으면 plan 이 반환한 것 사용
        _syns = _syns_param or _plan_result.get("synonyms") or []

        # ★ 관련성 기준 토큰 — 주제 + 동의어 + 설명 + 설계(series명·쿼리) 고유명사 집합
        _ref_tokens = _specific_tokens(f"{theme} {' '.join(_syns)} {description}")
        for _s in plan:
            _ref_tokens |= _specific_tokens(f"{_s.get('name', '')} {_s.get('query', '')}")

        pending_items: list[dict] = []   # 배치 추출 큐
        if plan:
            log.info(f"[chart_data] '{theme}' 설계 {len(plan)}개 series → 조준 수집(문서만)")
            from concurrent.futures import ThreadPoolExecutor as _TPE
            with _TPE(max_workers=4) as _ex:
                for result in _ex.map(
                        lambda s: _collect_docs_for_series(s, sector, theme, _ref_tokens), plan):
                    if result is None:
                        continue
                    if "fast_dataset" in result:
                        datasets.append(result["fast_dataset"])   # KOSIS fast-path: LLM 불필요
                    else:
                        pending_items.append(result)              # 배치 추출 큐
        _elapsed(f"1) 설계+조준수집 (fast={len(datasets)}, pending={len(pending_items)})")

        # ── 2) 종목(기업) 테마 보강 — 설계 수집이 0일 때만, *명백한 종목 테마* 에 한해 ──────
        #    (글로벌 시장 dump(_market_datasets)·ecos dump 는 제거: 비관련 주제에 새어들어 불일치=거짓.
        #     거시·시장 데이터는 planner 가 finance/ecos/kosis 출처로 *정확히* 조준할 때만 들어옴.)
        combined = f"{theme} {sector} {description}"
        _SPECIFIC_NON_VALUATION = [
            "직원", "고용", "인원", "임직원", "매출", "인구", "발행", "가맹점", "점포", "지점",
            "생산량", "판매량", "수출", "수입액", "점유율", "시장규모", "가입자", "이용자", "방문자",
            "출하량", "등록", "건수", "보급", "만족도",
        ]
        is_stock = (any(k in combined for k in _STOCK_THEME_KWS)
                    and not any(k in combined for k in _SPECIFIC_NON_VALUATION))
        if not datasets and is_stock:
            datasets.extend(_stock_datasets(theme, related_terms=related_terms))
        _elapsed(f"2) 종목보강 (datasets={len(datasets)})")

        # ── 2.5) 시장 전용 dataset (코스닥·코스피, 코스닥 150) ───────────────────
        #   ADR 010: 실데이터 없으면 빈 리스트 반환 — 합성 수치 사용 금지.
        datasets.extend(_market_trading_volume_datasets(theme))
        datasets.extend(_kosdaq150_sector_datasets(theme))   # ★ 코스닥 150 섹터 지수 전체 보장 (ERRORS [420])
        _elapsed(f"2.5) 시장 dataset (datasets={len(datasets)})")

        # ── 3) 적응형 *멀티소스* 포착 — 문서 수집만 (LLM 없음, 배치 추출 큐에 투입) ──────
        #    ★ 배치 LLM 한 번에 처리: 개별 LLM N회 → 배치 1회로 통합.
        #    ★ 배치 크기 가드: step1 pending + step3 합쳐서 최대 14개만 (batch max_tokens 초과 방지).
        _BATCH_CAP = 14
        _cand_cap = max(max_datasets * 2, max_datasets + 6)   # 전체 후보 상한
        _step3_budget = max(0, _BATCH_CAP - len(pending_items))   # step3 에 줄 배치 슬롯
        if len(datasets) < _cand_cap and _step3_budget > 0:
            _raw_terms = [theme] + _syns + [description] + [s.get("query", "") for s in plan]
            _terms: list[str] = []
            for t in _raw_terms:
                if not t:
                    continue
                tk = t.split()
                _terms.append(t)
                if len(tk) > 2:
                    _terms.append(" ".join(tk[:2]))
                if len(tk) > 1:
                    _terms.append(tk[0])
            _terms = list(dict.fromkeys(_terms))[:4]

            def _natural_title(d):
                t = getattr(d, "title", "") or ""
                for pre in ("KOSIS 통계청 — ", "한국은행 ECOS", "arXiv", "통계청 KOSIS"):
                    if t.startswith(pre):
                        t = t[len(pre):]
                return t

            _PER_SOURCE = max(3, (max_datasets + 3) // 3)
            _cands: list[tuple] = []   # (nat_title, doc, source)
            for source in ("kci", "academic", "news", "kor_econ", "kosis", "naver_news", "web", "discover"):
                if len(_cands) >= _cand_cap:
                    break
                _ensure_source_ready(source)
                prov = _get_provider(source)
                if not prov:
                    continue
                _from_src = 0
                _src_terms = _terms[:2] if source == "discover" else _terms
                for term in _src_terms:
                    if _from_src >= _PER_SOURCE or len(_cands) >= _cand_cap:
                        break
                    docs = _cached_collect(prov, source, term, sector, 8)
                    for d in docs[:5]:
                        nat = _natural_title(d)
                        if not _doc_title_relevant(nat, _ref_tokens):
                            continue
                        _cands.append((nat[:40] or term, d, source))
                        _from_src += 1
                        if _from_src >= _PER_SOURCE or len(_cands) >= _cand_cap:
                            break

            # step3 후보를 배치 슬롯 한도 내에서 pending_items 에 추가
            for nat, d, src in _cands[:_step3_budget]:
                pending_items.append({"series": {"name": nat, "unit": "", "chart": "bar"},
                                      "docs": [d], "source": src})
            log.info(f"[chart_data] step3 후보 {len(_cands)}개 → 배치 큐 추가 {min(len(_cands), _step3_budget)}개")
            _elapsed(f"3) 멀티소스 수집 (pending={len(pending_items)})")

        # ── BATCH) 단일 LLM 호출로 pending_items 전체 추출 + 관련성 판정 ──────
        if pending_items:
            batch_results = _batch_extract_all(pending_items, theme)
            datasets.extend(batch_results)
            _elapsed(f"BATCH) 일괄 추출 (datasets={len(datasets)})")

        # dedup (fingerprint) + exclude_titles 필터
        _excl = {str(t).strip() for t in (exclude_titles or [])}
        seen: set[str] = set()
        deduped: list[dict] = []
        for ds in datasets:
            fp = ds["fingerprint"]
            if fp in seen or ds["title"] in _excl:
                continue
            seen.add(fp)
            deduped.append(ds)
        # ── 4) 관련성 게이트는 BATCH 추출 시 relevant=true/false 로 이미 처리됨.
        #    KOSIS fast-path 데이터는 제목 필터(_doc_title_relevant)를 통과한 것만 들어오므로 별도 불필요.

        # ── 5) ★ 출처 다양성 선택 (사용자 박제 2026-07-01 '전부 받아와') — provider 별로 묶어
        #    우선순위 라운드로빈 → 한 출처(KOSIS) 독점 방지, 뉴스·정부보도·논문이 함께 섞임. ──────
        _PROV_RANK = {"kosis": 0, "ecos": 1, "dart": 1, "academic": 1, "kci": 1, "krx": 2,
                      "finance": 2, "news": 2, "kor_econ": 2, "naver_news": 3, "web": 4, "market": 3}
        from collections import OrderedDict as _OD
        _groups: "_OD[str, list]" = _OD()
        for ds in sorted(deduped, key=lambda d: _PROV_RANK.get((d.get("source") or {}).get("provider", ""), 5)):
            p = (ds.get("source") or {}).get("provider", "?")
            _groups.setdefault(p, []).append(ds)
        # ★ aspect 다양성 (사용자 박제 2026-07-01): 같은 지표어(예 '만족도')가 여러 개 선택돼 단조로운
        #   차트가 되지 않게, 주제·동의어를 뺀 *구별 지표 토큰* 이 이미 2개 선택되면 그 데이터셋은 후순위로.
        _theme_syn_tokens = _specific_tokens(f"{theme} {' '.join(_syns)}")
        _aspect_used: dict = {}

        def _aspect_saturated(ds) -> bool:
            toks = _specific_tokens(ds.get("title", "")) - _theme_syn_tokens
            return bool(toks) and any(_aspect_used.get(t, 0) >= 2 for t in toks)

        def _mark_aspect(ds):
            for t in (_specific_tokens(ds.get("title", "")) - _theme_syn_tokens):
                _aspect_used[t] = _aspect_used.get(t, 0) + 1

        final: list[dict] = []
        _deferred: list[dict] = []   # aspect 포화로 미룬 것 — 자리 남으면 채움(빈 풀 방지)
        while len(final) < max_datasets and any(_groups.values()):
            for p in list(_groups):
                if not _groups[p]:
                    continue
                ds = _groups[p].pop(0)
                if _aspect_saturated(ds):
                    _deferred.append(ds)
                    continue
                _mark_aspect(ds)
                final.append(ds)
                if len(final) >= max_datasets:
                    break
        # 다양성 우선 선택 후 자리가 남으면 미룬 것으로 보충 (차트 수 확보)
        for ds in _deferred:
            if len(final) >= max_datasets:
                break
            final.append(ds)

        _prov_mix = {}
        for d in final:
            _p = (d.get("source") or {}).get("provider", "?")
            _prov_mix[_p] = _prov_mix.get(_p, 0) + 1
        log.info(f"[chart_data] '{theme}' → {len(final)}개 dataset (설계 {len(plan) if plan else 0} series) "
                 f"출처분포 {_prov_mix}")
        return {"theme": theme, "datasets": final}
    finally:
        # 작업 종료 — busy 즉시 해제 (해제 실패는 조용히 무시, TTL 은 안전망으로 잔존)
        try:
            from shared.pipeline_activity import clear_busy as _cb
            _cb("j09")
        except Exception:
            pass


# ── 원시 수집 공개 래퍼 (JARVIS06 차트용 — provider 단일 진입점) ────────────
# ★ ADR 010 후속 (2026-06-29): JARVIS06 가 JARVIS09 *내부 provider* 를 직접 import 하던
#   것을 차단. JARVIS06 은 이 공개 함수만 호출하고, provider 접근(수집)은 JARVIS09 단독.
#   (파싱·로테이션·flat감지 등 "무엇을 차트로 그릴지" 시각화 결정은 JARVIS06 잔류.)

def get_ecos_raw(keyword: str) -> str:
    """ECOS 거시경제 원시 텍스트 (첫 문서 raw_text). 없으면 ""."""
    try:
        from JARVIS09_COLLECTOR.providers.ecos_provider import EcosProvider
        docs = EcosProvider().collect(keyword)
        return docs[0].raw_text if docs else ""
    except Exception as e:
        log.warning(f"[chart_data] get_ecos_raw 실패: {e}")
        return ""


def get_krx_raw(keyword: str, max_items: int | None = None) -> str:
    """KRX 종목 시세 원시 텍스트 (전 문서 raw_text join). 없으면 ""."""
    try:
        from JARVIS09_COLLECTOR.providers.krx_provider import KrxProvider
        prov = KrxProvider()
        docs = prov.collect(keyword, max_items=max_items) if max_items is not None \
            else prov.collect(keyword)
        return "\n".join(d.raw_text for d in docs) if docs else ""
    except Exception as e:
        log.warning(f"[chart_data] get_krx_raw 실패: {e}")
        return ""


__all__ = ["collect_chart_data", "get_ecos_raw", "get_krx_raw"]
