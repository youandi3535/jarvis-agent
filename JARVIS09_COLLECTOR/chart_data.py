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
    rows, _seen = [], set()
    for d in data or []:
        try:
            v = float(str(d.get("value")).replace(",", ""))
        except (TypeError, ValueError):
            continue
        label = _clean_label(d.get("label", ""))   # 괄호 설명 제거 + 길이 제한 (짤림·장황 방지)
        if label and v == v and label not in _seen:  # NaN·중복 라벨 제외
            _seen.add(label)
            rows.append({"label": label, "value": v})
    # 차트 의미 최소 기준: kpi 1개, 그 외 2개
    _min = 1 if viz_hint == "kpi_cards" else 2
    if len(rows) < _min:
        return None
    rows = rows[:10]   # ★ 가독성 — 한 차트 최대 10개 (30셀 막대벽 방지)
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
            KosisProvider, KrxProvider, KciProvider,
        )
        _m = {"blog": BlogProvider, "news": NewsProvider, "academic": AcademicProvider,
              "finance": FinanceProvider, "web": WebProvider, "kor_econ": KorEconProvider,
              "naver_news": NaverNewsProvider, "dart": DartProvider, "ecos": EcosProvider,
              "kosis": KosisProvider, "krx": KrxProvider, "kci": KciProvider}
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


def _reduce_crosstab(rows: list, max_rows: int = 7) -> list:
    """KOSIS 교차표(성별·지역·업종 × 응답 × 연도 등 다차원 셀)를 *읽을 수 있는 단일 분포* 로 축약.
    ★ 사용자 박제 2026-07-01: 30셀 덤프 금지. 핵심 = '전체/계' 값을 가진 *모든* 차원을 전체로
    collapse → 순수 응답 분포만 남김 (성별·지역·업종 무엇이든 일반 처리). 최신 연도만. 라벨은
    응답명만(괄호 설명 제거), 최대 max_rows개. 전체 차원이 없으면 원본 정리 후 반환."""
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
        return [{"label": _clean_label(r.get("label", "")), "value": r.get("value")} for r in rows[:max_rows]]

    # 1) 최신 시점만 (연도 혼재 방지)
    periods = sorted({p["period"] for p in parsed if p["period"]})
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
                return out[:max_rows]

    # 4) 폴백(전체 차원 없음): 응답명만 라벨로 dedup
    out, seen = [], set()
    for p in parsed:
        lab = _clean_label(" ".join(p["rest"]))
        if not lab or lab in seen:
            continue
        seen.add(lab)
        out.append({"label": lab, "value": p["value"]})
    return out[:max_rows]


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
    title = re.sub(r"\s*[(（][^)）]*[)）]", "", title).strip() or "KOSIS 통계"   # 제목 괄호 설명 제거
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
    # ★ 교차표(30셀 덤프) → 읽을 수 있는 단일 분포(전체×최신연도 응답별 ≤7개)로 축약
    rows = _reduce_crosstab(rows, max_rows=7)
    if len(rows) < 2:
        return None
    # ★ 다질문 표 감지 (사용자 박제 2026-07-01): 한 KOSIS 표에 여러 질문(인지도+구매한도 등)이
    #   섞이면 비율 합이 100%를 크게 초과(합계 200% 도넛 사고). 이 경우 fast-path 포기 → LLM 추출이
    #   *하나의 일관된 질문* 만 골라내게 위임 (None 반환 시 _extract_series_from_docs LLM 경로로 진행).
    _vals = [r["value"] for r in rows]
    if _vals and all(0 <= v <= 100 for v in _vals) and sum(_vals) > 140:
        return None
    src = {"provider": "kosis", "name": "통계청 KOSIS",
           "url": getattr(doc, "url", "") or "https://kosis.kr/", "as_of": _now_as_of()}
    return _mk_dataset(title[:40], "bar_chart", unit, rows, src)


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
    # ★ LLM 추출 라벨도 교차표 축약 (사용자 박제 2026-07-01): LLM이 '합계·신용카드' / '읍소재시장·
    #   신용카드' 처럼 전체+특정 차원을 섞어 반환하면 _reduce_crosstab 으로 전체 차원 collapse →
    #   '신용카드' 만 남김 (KOSIS fast-path 와 동일 정리). 단일 차원이면 그대로 보존.
    rows = _reduce_crosstab(rows, max_rows=8)
    # ★ 단위 일관성 (사용자 박제 2026-07-01): LLM이 공통 단위를 못 정하면(=비교 불가 수치 짬뽕)
    #   그 차트는 버린다. 단위 없는 무의미 막대(부가가치 1.9 + 비용 1.88 …) 원천 차단.
    _llm_unit = str(parsed.get("unit", "") or "").strip()
    _unit = _llm_unit or str(series.get("unit", "") or "").strip()
    if len(rows) < 2 or not src_url or not _unit:
        return None
    src = {"provider": src_name or "web", "name": src_name or "web", "url": src_url, "as_of": _now_as_of()}
    viz = {"line": "line_chart", "bar": "bar_chart", "stat": "kpi", "donut": "pie_chart"}.get(series.get("chart"), "bar_chart")
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
    """표/문서 제목이 주제 고유명사와 겹치면 관련. 겹침 0 → 무관(농촌관광·식품소비 차단)."""
    if not ref_tokens:
        return True
    title = str(title or "")
    toks = _specific_tokens(title)
    return any((rt in title) or (rt in toks) for rt in ref_tokens)


def _relevance_filter(theme: str, description: str, datasets: list) -> list:
    """★ 의미 기반 관련성 게이트 (사용자 박제 2026-07-01): 주제와 *직접* 관련된 dataset만 남김.
    농촌관광·식품소비행태처럼 다른 주제를 다루며 주제를 스쳐 언급할 뿐인 표를 제거.
    LLM 호출 *자체* 실패 시에만 fail-open (상류 결정론 게이트가 이미 1차 차단).
    동의어(지역화폐↔지역사랑상품권)는 의미로 판단 — 토큰 매칭 한계 극복."""
    if len(datasets) <= 1:
        return datasets
    listing = "\n".join(f"{i}. {d.get('title', '')}" for i, d in enumerate(datasets))
    prompt = (
        f'블로그 글 주제: "{theme}"' + (f" — {description}" if description else "") + "\n\n"
        "아래 데이터 표 목록 중, 이 주제의 차트로 쓰기에 *직접 관련된* 표의 번호만 고르세요.\n"
        "판단 기준:\n"
        "- 주제와 같은 대상/현상을 다루면 관련 (동의어·공식명칭 포함, 예 '지역화폐'≈'지역사랑상품권').\n"
        "- *다른 주제*(예: 농촌관광·식품소비행태·인구·날씨)를 다루며 주제를 스쳐 언급할 뿐이면 제외.\n"
        "- 애매하면 제외(주제 일관성 우선).\n\n"
        f"{listing}\n\n"
        'JSON만 출력: {"keep": [관련 번호 목록]}'
    )
    try:
        from shared.llm import invoke_text
        raw = invoke_text("analyzer", prompt, max_tokens=200, temperature=0)
        m = re.search(r"\{[\s\S]*\}", raw or "")
        if m:
            keep = json.loads(m.group(0)).get("keep")
            if isinstance(keep, list):
                idx = {int(i) for i in keep if isinstance(i, int) or str(i).strip().isdigit()}
                filtered = [d for i, d in enumerate(datasets) if i in idx]
                if filtered:
                    if len(filtered) < len(datasets):
                        log.info(f"[chart_data] 관련성 게이트: {len(datasets)}→{len(filtered)}개 "
                                 f"(제외 {len(datasets) - len(filtered)})")
                    return filtered
                log.info("[chart_data] 관련성 게이트: 전부 무관 판정 → 원본 유지(빈 풀 방지)")
    except Exception as e:
        log.warning(f"[chart_data] 관련성 게이트 LLM 실패(유지): {e}")
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


def _collect_one_series(series: dict, sector: str, theme: str = "", ref_tokens: set = None):
    """한 series 를 설계된 출처 우선순위로 조준 수집 (라이브러리 자동설치 + 쿼리 점진 확장). 첫 성공 사용.
    ★ 텍스트 출처는 *제목 관련성* 으로 관련 문서를 우선 → KOSIS 가 엉뚱한 표(농촌관광 등)를
      반환해도 series·주제와 겹치는 표를 골라 추출. 관련 0이면 그 출처 스킵(엉뚱한 표 채택 금지)."""
    queries = _query_candidates(series, theme)
    _ser_tokens = set(ref_tokens or set()) | _specific_tokens(
        f"{series.get('name', '')} {series.get('query', '')}")
    for source in series.get("sources", []):
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

    _COLLECT_CACHE.clear()   # ★ per-run 수집 캐시 초기화 (이전 주제 잔재 제거 + 이번 run 내 재사용)
    datasets: list[dict] = []

    # ── 1) 설계 우선 (data_planner): 주제별 series·출처·쿼리 설계 → 조준 수집(병렬) ──────
    #    ★ 사용자 박제 2026-06-30: 무작정 수집이 아니라 "설계 → 조준 수집". 라이브러리 자동설치.
    try:
        from JARVIS09_COLLECTOR.data_planner import plan_data_sources
        plan = plan_data_sources(theme, sector, description)
    except Exception as e:
        log.warning(f"[chart_data] 설계 실패: {e}")
        plan = []
    # ★ 관련성 기준 토큰 — 주제 + 설명 + 설계(series명·쿼리, 공식 동의어 포함) 고유명사 집합.
    #   설계가 '지역사랑상품권' 같은 공식명을 쿼리로 내면 토큰에 포함 → 동의어 표도 관련 인정.
    _ref_tokens = _specific_tokens(f"{theme} {description}")
    for _s in (plan or []):
        _ref_tokens |= _specific_tokens(f"{_s.get('name', '')} {_s.get('query', '')}")
    if plan:
        log.info(f"[chart_data] '{theme}' 설계 {len(plan)}개 series → 조준 수집")
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=4) as _ex:
            for ds in _ex.map(lambda s: _collect_one_series(s, sector, theme, _ref_tokens), plan):
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

    # ── 3) 적응형 *멀티소스* 포착 — ★ '받을 수 있는 곳을 전부' (사용자 박제 2026-07-01):
    #    KOSIS 만이 아니라 뉴스·정부보도(kor_econ)·논문(academic)·웹에서도 *주제 관련 실데이터* 를
    #    출처별로 수집. ★ 풀이 차도 멈추지 않음(출처당 캡) → 최종 라운드로빈이 다양성 보장.
    #    관련성 필터로 무관 데이터(농촌관광·식품소비 등) 차단. 모든 출처 URL 박제. 데이터 많을수록 좋다.
    _cand_cap = max(max_datasets * 2, max_datasets + 6)   # 후보 상한(비용 가드)
    if len(datasets) < _cand_cap:
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
        _terms = list(dict.fromkeys(_terms))[:5]   # 속도: 상위 5개 검색어만 (step1이 설계쿼리 이미 커버)
        # 관련성 토큰: step1 과 동일한 _ref_tokens(주제+설명+설계 동의어 포함) 재사용 → 일관 게이트.

        def _natural_title(d):   # provider 접두(주제명 스탬프) 제거한 *실제* 표/논문명
            t = getattr(d, "title", "") or ""
            for pre in ("KOSIS 통계청 — ", "한국은행 ECOS", "arXiv", "통계청 KOSIS"):
                if t.startswith(pre):
                    t = t[len(pre):]
            return t

        # ECOS 제외: 항상 '거시 일반(금리·환율·물가)'을 주제명만 찍어 반환 → 무관 혼입원.
        # ★ 출처당 캡(_PER_SOURCE) — 한 출처가 후보를 독점하지 않게 (뉴스·정부보도·논문 슬롯 확보).
        #   논문(academic)·뉴스(news)·정부보도(kor_econ) 를 KOSIS 와 동등하게 수집. 논문 우선.
        # ★ 속도 (사용자 박제 2026-07-01): (A) 후보 문서 수집(빠름, 캐시) → (B) LLM 추출 *병렬*.
        #   기존 순차 추출이 12분 병목이라 추출만 ThreadPool 로 동시 실행.
        _PER_SOURCE = max(3, (max_datasets + 3) // 3)
        _cands = []   # (pseudo_name, doc)
        for source in ("kci", "academic", "news", "kor_econ", "kosis", "naver_news", "web"):
            if len(_cands) >= _cand_cap:
                break
            _ensure_source_ready(source)
            prov = _get_provider(source)
            if not prov:
                continue
            _from_src = 0
            for term in _terms:
                if _from_src >= _PER_SOURCE or len(_cands) >= _cand_cap:
                    break
                docs = _cached_collect(prov, source, term, sector, 8)   # 풍부하게(캐시)
                for d in docs[:5]:
                    nat = _natural_title(d)
                    # 관련성 1차(결정론): 자연 제목이 주제 고유명사와 겹쳐야 채택 (동의어 포함)
                    if not _doc_title_relevant(nat, _ref_tokens):
                        continue
                    _cands.append(((nat[:40] or term), d))
                    _from_src += 1
                    if _from_src >= _PER_SOURCE or len(_cands) >= _cand_cap:
                        break
        # (B) 병렬 추출 — LLM 호출이 느려 동시 실행 (단위 일관성·실수치 게이트는 추출기 내부)
        if _cands:
            from concurrent.futures import ThreadPoolExecutor as _TPE2

            def _extract_cand(c):
                nat, d = c
                return _extract_series_from_docs({"name": nat, "unit": "", "chart": "bar"}, [d])

            with _TPE2(max_workers=6) as _ex2:
                for ds in _ex2.map(_extract_cand, _cands):
                    if ds:
                        datasets.append(ds)

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

    # ── 4) ★ 의미 기반 관련성 게이트 (최종 관문) — '농촌관광·식품소비행태'처럼 다른 주제 표가
    #    새어든 것을 LLM 의미 판단으로 제거 (선택 *전* 적용 → 깨끗한 후보에서만 선택). ──────
    deduped = _relevance_filter(theme, description, deduped)

    # ── 5) ★ 출처 다양성 선택 (사용자 박제 2026-07-01 '전부 받아와') — provider 별로 묶어
    #    우선순위 라운드로빈 → 한 출처(KOSIS) 독점 방지, 뉴스·정부보도·논문이 함께 섞임. ──────
    _PROV_RANK = {"kosis": 0, "ecos": 1, "dart": 1, "academic": 1, "kci": 1, "krx": 2,
                  "finance": 2, "news": 2, "kor_econ": 2, "naver_news": 3, "web": 4, "market": 3}
    from collections import OrderedDict as _OD
    _groups: "_OD[str, list]" = _OD()
    for ds in sorted(deduped, key=lambda d: _PROV_RANK.get((d.get("source") or {}).get("provider", ""), 5)):
        p = (ds.get("source") or {}).get("provider", "?")
        _groups.setdefault(p, []).append(ds)
    final: list[dict] = []
    while len(final) < max_datasets and any(_groups.values()):
        for p in list(_groups):
            if _groups[p]:
                final.append(_groups[p].pop(0))
                if len(final) >= max_datasets:
                    break

    _prov_mix = {}
    for d in final:
        _p = (d.get("source") or {}).get("provider", "?")
        _prov_mix[_p] = _prov_mix.get(_p, 0) + 1
    log.info(f"[chart_data] '{theme}' → {len(final)}개 dataset (설계 {len(plan) if plan else 0} series) "
             f"출처분포 {_prov_mix}")
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
