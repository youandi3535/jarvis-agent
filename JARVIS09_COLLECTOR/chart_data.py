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

아래는 수집된 자료 발췌입니다. 각 발췌 앞의 [n] 은 출처 인덱스입니다.

{excerpts}

위 자료에 *실제로 등장한 수치*만으로 차트용 데이터셋을 최대 3개 만드세요.
각 데이터셋은 동질적인 항목들의 비교/추이여야 합니다 (단위·맥락이 같아야 함).
숫자를 지어내지 말고, 발췌에 없는 항목은 넣지 마세요.

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
                          _WEB_PROMPT.format(theme=theme, excerpts=excerpts),
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


# ── 공개 API ──────────────────────────────────────────────────────────────
def collect_chart_data(theme: str, sector: str = "", description: str = "",
                       exclude_titles=None, max_datasets: int = 6) -> dict:
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

    combined = f"{theme} {sector} {description}"
    is_stock = any(k in combined for k in _STOCK_THEME_KWS)
    is_macro = any(k in combined.lower() for k in _MACRO_KWS)

    # ★ 지표 일치성 (사용자 박제 2026-06-30): 직원수·매출·인구처럼 구조화 provider(KRX:PER/ROE/주가)가
    #   *보유하지 않은* 특정 지표를 요청하면, generic 종목지표를 엉뚱하게 라벨링해 반환하면 안 된다
    #   (요청≠데이터 = 거짓 정보). → 그런 요청은 종목지표 억제, 웹 실데이터(출처 URL)로만 응답.
    _SPECIFIC_NON_VALUATION = [
        "직원", "고용", "인원", "임직원", "매출", "인구", "발행", "가맹점", "점포", "지점",
        "생산량", "판매량", "수출", "수입액", "점유율", "시장규모", "가입자", "이용자", "방문자",
        "출하량", "등록", "건수", "보급",
    ]
    _wants_specific = any(k in combined for k in _SPECIFIC_NON_VALUATION)
    if _wants_specific:
        is_stock = False   # 종목 valuation 지표는 요청과 무관 → 억제 (웹 실데이터로만)

    datasets: list[dict] = []
    # 구조화 API 우선 (provenance 명확·고신뢰)
    if is_stock:
        datasets.extend(_stock_datasets(theme))
    if is_macro:
        datasets.extend(_market_datasets())
        datasets.extend(_ecos_datasets(theme, description))
    # 웹 출처 보강 (URL 박제) — 항상 시도 (구조화 데이터 부족분 보완)
    try:
        datasets.extend(_web_datasets(theme, sector, description))
    except Exception as e:
        log.warning(f"[chart_data] 웹 dataset 예외: {e}")
        _g_report("collector", e, module=__name__)

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

    log.info(f"[chart_data] '{theme}' → {len(final)}개 dataset "
             f"(stock={is_stock} macro={is_macro})")
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
