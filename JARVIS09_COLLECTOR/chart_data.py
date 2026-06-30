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

# 거시경제·시장 키워드 — market/ECOS dataset 허용 판정
_MACRO_KWS = frozenset([
    "금리", "기준금리", "물가", "cpi", "환율", "달러", "수출", "수입", "실업",
    "성장", "gdp", "경제", "경기", "증시", "코스피", "나스닥", "시장", "인플레",
])


def _now_as_of() -> str:
    return datetime.now().strftime("%Y-%m")


def _fingerprint(title: str, unit: str) -> str:
    seed = f"{title.strip()}|{unit.strip()}"
    return hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


def _mk_dataset(title, viz_hint, unit, data, source) -> dict | None:
    """dataset dict 생성. data 가 비거나 값이 부족하면 None."""
    rows = []
    for d in data or []:
        try:
            v = float(str(d.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        label = str(d.get("label", "")).strip()[:20]
        if label and v == v:  # NaN 제외
            rows.append({"label": label, "value": v})
    # 차트 의미 최소 기준: kpi 1개, 그 외 2개
    _min = 1 if viz_hint == "kpi_cards" else 2
    if len(rows) < _min:
        return None
    return {
        "title": title.strip()[:30],
        "viz_hint": viz_hint,
        "unit": unit.strip(),
        "data": rows,
        "source": source,
        "fingerprint": _fingerprint(title, unit),
    }


# ── 1. 종목 재무 dataset (collect_stocks_data) ───────────────────────────
def _stock_datasets(theme: str) -> list[dict]:
    try:
        from JARVIS09_COLLECTOR import collect_stocks_data
        data = collect_stocks_data(theme) or {}
    except Exception as e:
        log.warning(f"[chart_data] collect_stocks_data 실패: {e}")
        return []
    stocks = data.get("stocks") or []
    if len(stocks) < 2:
        return []

    src = {"provider": "krx", "name": "한국거래소·금융감독원 DART",
           "url": "https://data.krx.co.kr", "as_of": _now_as_of()}
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


# ── 2. 글로벌 시장 지표 dataset (get_market_data) ─────────────────────────
def _market_datasets() -> list[dict]:
    try:
        from JARVIS09_COLLECTOR import get_market_data
        market = get_market_data() or {}
    except Exception as e:
        log.warning(f"[chart_data] get_market_data 실패: {e}")
        return []
    if not market:
        return []
    src = {"provider": "yfinance", "name": "글로벌 시장(yfinance)",
           "url": "https://finance.yahoo.com", "as_of": _now_as_of()}
    # 등락률(%) — 동질 단위 → 막대 비교 가능
    changes = [{"label": name, "value": d.get("change", 0.0)}
               for name, d in market.items() if isinstance(d, dict)]
    ds = _mk_dataset("주요 시장 지표 등락률", "bar_chart", "%", changes, dict(src))
    return [ds] if ds else []


# ── 3. ECOS 거시경제 시계열 dataset ──────────────────────────────────────
def _ecos_datasets(theme: str, description: str) -> list[dict]:
    combined = f"{theme} {description}".lower()
    if not any(k in combined for k in _MACRO_KWS):
        return []
    try:
        from JARVIS09_COLLECTOR.providers.ecos_provider import EcosProvider
        docs = EcosProvider().collect(theme)
    except Exception as e:
        log.warning(f"[chart_data] EcosProvider 실패: {e}")
        return []
    if not docs:
        return []
    # (label, value) 시계열 추출 — "2025.01  3.50" 류
    text = getattr(docs[0], "raw_text", "") or ""
    pairs = re.findall(r"(\d{4}[.\-/]\s?\d{1,2})\D{0,6}?(-?\d+(?:\.\d+)?)", text)
    rows = [{"label": lab.replace(" ", ""), "value": val} for lab, val in pairs[:12]]
    src = {"provider": "ecos", "name": "한국은행 ECOS",
           "url": "https://ecos.bok.or.kr", "as_of": _now_as_of()}
    ds = _mk_dataset(f"{theme} 추이", "line_chart", "", rows, src)
    return [ds] if ds else []


# ── 4. 웹 출처 수치 dataset (collect_for_theme + LLM 구조화, URL 박제) ─────
_WEB_SYSTEM = """당신은 수집된 뉴스·자료에서 *실제로 등장한 수치*만 골라 차트용 데이터로
구조화하는 데이터 분석가입니다. 본문에 명시되지 않은 숫자는 절대 만들지 마세요."""

_WEB_PROMPT = """주제: {theme}
요청 초점(이 내용과 *직접 관련된* 수치만): {focus}

아래는 수집된 자료 발췌입니다. 각 발췌 앞의 [n] 은 출처 인덱스입니다.

{excerpts}

위 자료에 *실제로 등장한 수치* 중 **요청 초점과 직접 관련된 것만**으로 차트용 데이터셋을
최대 3개 만드세요. 각 데이터셋은 동질적인 항목들의 비교/추이여야 합니다 (단위·맥락 동일).
★ 요청 초점과 무관한 수치(예: 직원수를 물었는데 주가·지수)는 *절대 넣지 마세요* — 무관 데이터는
   거짓 정보입니다. 숫자를 지어내지 말고, 발췌에 없는 항목·초점과 무관한 항목은 넣지 마세요.
관련 수치가 없으면 빈 배열을 출력하세요.

출력(JSON만):
{{"datasets": [
  {{"title": "제목(20자 이내)", "unit": "단위", "viz_hint": "bar_chart|line_chart|pie_chart",
    "source_idx": 출처인덱스정수,
    "data": [{{"label": "항목", "value": 숫자}}]}}
]}}
데이터를 만들 수 없으면 {{"datasets": []}} 를 출력하세요."""


def _web_datasets(theme: str, sector: str, description: str) -> list[dict]:
    try:
        from JARVIS09_COLLECTOR import collect_for_theme
        docs = collect_for_theme(theme, sector=sector) or []
    except Exception as e:
        log.warning(f"[chart_data] collect_for_theme 실패: {e}")
        return []
    if not docs:
        return []
    # 소스 다양성 확보하며 상위 8건
    seen_src: dict[str, int] = {}
    selected = []
    for d in docs:
        st = getattr(d, "source_type", "")
        if seen_src.get(st, 0) < 2:
            selected.append(d)
            seen_src[st] = seen_src.get(st, 0) + 1
        if len(selected) >= 8:
            break
    if not selected:
        return []

    excerpts = "\n\n".join(
        f"[{i}] ({getattr(d, 'source_type', '')}) {getattr(d, 'title', '')}\n"
        f"{(getattr(d, 'cleaned_text', '') or '')[:600]}"
        for i, d in enumerate(selected)
    )
    try:
        from shared.llm import invoke_text
        raw = invoke_text("analyzer",
                          _WEB_PROMPT.format(theme=theme, focus=(description or theme), excerpts=excerpts),
                          system=_WEB_SYSTEM, max_tokens=900, temperature=0.1)
    except Exception as e:
        log.warning(f"[chart_data] 웹 수치 구조화 LLM 실패: {e}")
        _g_report("collector", e, module=__name__)
        return []
    if not raw:
        return []
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []

    out = []
    for ds in parsed.get("datasets") or []:
        idx = ds.get("source_idx")
        try:
            doc = selected[int(idx)]
        except (TypeError, ValueError, IndexError):
            continue   # 출처 인덱스 불명 → 폐기 (provenance 없는 데이터 금지)
        src = {
            "provider": "web",
            "name": getattr(doc, "source_type", "web") or "web",
            "url": getattr(doc, "url", "") or "",
            "as_of": _now_as_of(),
        }
        if not src["url"]:
            continue   # URL 없는 웹 데이터 폐기
        built = _mk_dataset(str(ds.get("title", theme)),
                            ds.get("viz_hint", "bar_chart"),
                            str(ds.get("unit", "")),
                            ds.get("data"), src)
        if built:
            out.append(built)
    return out


# ── 설계도 실행기 — data_planner 의 series 를 조준 수집 (라이브러리 자동설치 포함) ──────────
#   ★ 사용자 박제 2026-06-30: 필요 라이브러리/API 는 *말 안 해도 자동 설치·연결*. 못 구하면
#   가만히 있지 말고 설계된 출처를 우선순위로 다 시도. (API 키는 .env 사전 등록 — 가입만 1회 수동.)
_PROVIDER_REGISTRY: dict = {}
_SOURCE_LIBS = {   # source → 자동설치 무료 라이브러리 (import명, pip명)
    "krx":      [("pykrx", "pykrx")],
    "finance":  [("yfinance", "yfinance")],
    "news":     [("feedparser", "feedparser")],
    "blog":     [("feedparser", "feedparser")],
    "web":      [("bs4", "beautifulsoup4")],
    "kor_econ": [("bs4", "beautifulsoup4")],
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
            KosisProvider, KrxProvider,
        )
        _m = {"blog": BlogProvider, "news": NewsProvider, "academic": AcademicProvider,
              "finance": FinanceProvider, "web": WebProvider, "kor_econ": KorEconProvider,
              "naver_news": NaverNewsProvider, "dart": DartProvider, "ecos": EcosProvider,
              "kosis": KosisProvider, "krx": KrxProvider}
        cls = _m.get(source)
        inst = cls() if cls else None
    except Exception as e:
        log.warning(f"[chart_data] provider 로드 실패({source}): {e}")
    _PROVIDER_REGISTRY[source] = inst
    return inst


_SERIES_SYSTEM = """당신은 수집 자료에서 *실제로 등장한 수치*만 뽑아 차트 데이터로 구조화하는
데이터 분석가다. 요청 지표와 무관한 수치·자료에 없는 수치는 절대 만들지 않는다."""

_SERIES_PROMPT = """요청 지표: "{name}"  (단위: {unit})

아래 자료에서 위 지표에 *직접 해당하는 실제 수치*만 뽑아 차트 데이터로 구조화하라.
각 발췌 앞 [n] 은 출처 인덱스. 지표와 무관한 수치는 넣지 마라. 숫자를 지어내지 마라.

{excerpts}

출력 JSON만: {{"data": [{{"label": "연도/항목", "value": 숫자, "source_idx": n}}]}}
관련 수치가 없으면 {{"data": []}}."""


def _parse_clean_doc(doc):
    """KOSIS 등 '[KOSIS 통계표: ...] / 라벨: 값 단위' 정형 텍스트를 LLM 없이 직접 파싱 (빠름).
    반환 dataset 또는 None. (LLM 추출 호출 폭증 방지 — 풍부 수집 속도 핵심)"""
    text = getattr(doc, "raw_text", "") or ""
    if "[KOSIS 통계표:" not in text:
        return None
    title = getattr(doc, "title", "") or "KOSIS"
    for pre in ("KOSIS 통계청 — ", "통계청 KOSIS — "):
        if title.startswith(pre):
            title = title[len(pre):]
    unit = ""
    mu = re.search(r"단위:\s*([^)\]]+)", text)
    if mu:
        unit = mu.group(1).strip()
    rows = []
    for line in text.splitlines():
        m = re.match(r"\s+(.+?):\s*(-?[\d.,]+)\s*(\S*)\s*$", line)
        if not m:
            continue
        lab = m.group(1).strip()
        try:
            val = round(float(m.group(2).replace(",", "")), 2)
        except ValueError:
            continue
        rows.append({"label": lab, "value": val})
    if len(rows) < 2:
        return None
    src = {"provider": "kosis", "name": "통계청 KOSIS",
           "url": getattr(doc, "url", "") or "https://kosis.kr/", "as_of": _now_as_of()}
    return _mk_dataset(title[:40], "bar_chart", unit, rows[:30], src)


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
        f"{(getattr(d, 'raw_text', '') or getattr(d, 'cleaned_text', '') or '')[:600]}"
        for i, d in enumerate(sel))
    try:
        from shared.llm import invoke_text
        raw = invoke_text("analyzer",
                          _SERIES_PROMPT.format(name=series["name"], unit=series.get("unit", ""), excerpts=excerpts),
                          system=_SERIES_SYSTEM, max_tokens=700, temperature=0.1)
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
    for r in parsed.get("data") or []:
        try:
            doc = sel[int(r.get("source_idx"))]
        except (TypeError, ValueError, IndexError):
            continue
        if not src_url:
            src_url = getattr(doc, "url", "") or ""
            src_name = getattr(doc, "source_type", "") or ""
        rows.append({"label": str(r.get("label", "")), "value": r.get("value")})
    if len(rows) < 1 or not src_url:
        return None
    src = {"provider": src_name or "web", "name": src_name or "web", "url": src_url, "as_of": _now_as_of()}
    viz = {"line": "line_chart", "bar": "bar_chart", "stat": "kpi", "donut": "pie_chart"}.get(series.get("chart"), "bar_chart")
    return _mk_dataset(series["name"], viz, series.get("unit", ""), rows, src)


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


def _collect_one_series(series: dict, sector: str, theme: str = ""):
    """한 series 를 설계된 출처 우선순위로 조준 수집 (라이브러리 자동설치 + 쿼리 점진 확장). 첫 성공 사용."""
    queries = _query_candidates(series, theme)
    for source in series.get("sources", []):
        _ensure_source_ready(source)
        prov = _get_provider(source)
        if not prov:
            continue
        docs = []
        for q in queries:               # 넓은 쿼리로 재시도 — 0건이면 다음 후보
            try:
                docs = prov.collect(q, sector, max_items=10)
            except Exception as e:
                log.warning(f"[chart_data] {source} 수집 실패: {e}")
                docs = []
            if docs:
                break
        ds = _extract_series_from_docs(series, docs) if docs else None
        if ds:
            log.info(f"[chart_data] '{series['name']}' ← {source} ({len(ds['data'])}점)")
            return ds
    return None


# ── 공개 API ──────────────────────────────────────────────────────────────
def collect_chart_data(theme: str, sector: str = "", description: str = "",
                       exclude_titles=None, max_datasets: int = 12) -> dict:
    """주제 연관 차트용 실데이터를 출처(provenance)와 함께 수집.

    Args:
        theme:          글 주제/키워드 (예: "삼성전자", "반도체").
        sector:         섹터 (선택).
        description:    관련 섹션 본문/설명 — 관련 dataset 우선순위 힌트.
        exclude_titles: 이미 사용한 dataset title 집합 (같은 글 내 중복 방지).
        max_datasets:   반환 최대 dataset 수.

    Returns:
        {"theme": theme, "datasets": [dataset, ...]}.
        실데이터 없으면 datasets=[] (거짓 데이터 합성 안 함).
    """
    theme = (theme or "").strip()
    if not theme:
        return {"theme": theme, "datasets": []}

    datasets: list[dict] = []

    # ── 1) 설계 우선 (data_planner): 주제별 series·출처·쿼리 설계 → 조준 수집(병렬) ──────
    #    ★ 사용자 박제 2026-06-30: 무작정 수집이 아니라 "설계 → 조준 수집". 라이브러리 자동설치.
    try:
        from JARVIS09_COLLECTOR.data_planner import plan_data_sources
        plan = plan_data_sources(theme, sector, description)
    except Exception as e:
        log.warning(f"[chart_data] 설계 실패: {e}")
        plan = []
    if plan:
        log.info(f"[chart_data] '{theme}' 설계 {len(plan)}개 series → 조준 수집")
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=4) as _ex:
            for ds in _ex.map(lambda s: _collect_one_series(s, sector, theme), plan):
                if ds:
                    datasets.append(ds)

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
        datasets.extend(_stock_datasets(theme))

    # ── 3) 적응형 포착 — 풍부하게 + 관련성 (사용자 박제 2026-06-30): 설계 수집이 빈약하면
    #    KOSIS(정부통계)·논문(거짓 없음)·정부보도자료를 *설계 쿼리(공식 용어)* 로 직접 수집해
    #    실제 표/논문 수치를 자연 제목으로 포착. ★ 관련성 필터로 무관 데이터(키프로스·물가 등) 차단.
    #    모든 데이터 출처(URL) 박제. 데이터는 많을수록 좋다.
    if len(datasets) < max_datasets:
        # 검색어: 주제 + 설명(공식 용어 포함, 예 '지역사랑상품권') + 설계 쿼리
        _raw_terms = [theme, description] + [s.get("query", "") for s in (plan or [])]
        # 검색어 확장: 긴 쿼리는 정부통계·논문 검색에서 0건 → 앞2토큰·핵심명사로 넓힘
        _terms = []
        for t in _raw_terms:
            if not t:
                continue
            tk = t.split()
            _terms.append(t)
            if len(tk) > 2:
                _terms.append(" ".join(tk[:2]))
            if len(tk) > 1:
                _terms.append(tk[0])   # 핵심 명사 (예: 지역사랑상품권)
        _terms = list(dict.fromkeys(_terms))[:10]
        # 주제 토큰: 주제+설계쿼리에서 *구체 명사*만 (불용어·일반어 제외 → 관련성 정확도↑)
        _STOP = {"연도별", "추이", "현황", "비교", "규모", "전망", "분석", "통계", "지표", "변화",
                 "증가", "감소", "월별", "분기", "연간", "전국", "시도", "관련", "주요", "최근", "수준"}
        _theme_tokens = {t for term in _terms for t in re.split(r"\s+", term)
                         if len(t) >= 2 and t not in _STOP}

        def _natural_title(d):   # provider 접두(주제명 스탬프) 제거한 *실제* 표/논문명
            t = getattr(d, "title", "") or ""
            for pre in ("KOSIS 통계청 — ", "한국은행 ECOS", "arXiv", "통계청 KOSIS"):
                if t.startswith(pre):
                    t = t[len(pre):]
            return t

        # ECOS 제외: 항상 '거시 일반(금리·환율·물가)'을 주제명만 찍어 반환 → 무관 혼입원.
        # 출처 한정 안 함(사용자 박제): 정부통계·논문·정부보도자료·뉴스·웹 폭넓게. 논문 우선.
        for source in ("kosis", "academic", "kor_econ", "naver_news", "news", "web"):
            if len(datasets) >= max_datasets:
                break
            _ensure_source_ready(source)
            prov = _get_provider(source)
            if not prov:
                continue
            for term in _terms:
                if len(datasets) >= max_datasets:
                    break
                try:
                    docs = prov.collect(term, sector, max_items=8)   # 풍부하게
                except Exception:
                    docs = []
                for d in docs[:5]:
                    nat = _natural_title(d)
                    # 관련성: *자연 제목*(주제명 스탬프 제거)에 구체 주제 토큰이 있어야 채택
                    if not any(tok in nat for tok in _theme_tokens):
                        continue
                    pseudo = {"name": (nat[:40] or term), "unit": "", "chart": "bar"}
                    ds = _extract_series_from_docs(pseudo, [d])
                    if ds:
                        datasets.append(ds)
                    if len(datasets) >= max_datasets:
                        break

    # dedup (fingerprint) + exclude_titles 필터
    _excl = {str(t).strip() for t in (exclude_titles or [])}
    seen: set[str] = set()
    final: list[dict] = []
    for ds in datasets:
        fp = ds["fingerprint"]
        if fp in seen or ds["title"] in _excl:
            continue
        seen.add(fp)
        final.append(ds)
        if len(final) >= max_datasets:
            break

    log.info(f"[chart_data] '{theme}' → {len(final)}개 dataset (설계 {len(plan) if plan else 0} series)")
    return {"theme": theme, "datasets": final}


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
