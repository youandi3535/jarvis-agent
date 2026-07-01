"""JARVIS09_COLLECTOR/data_planner.py — 주제별 데이터 소싱 *설계* (LLM, 하드코딩 0).

★ 사용자 박제 2026-06-30: "주제가 결정되면, 그 주제에 맞게 어떤 데이터를·어디서·어떻게
  받아야겠다라는 *설계*가 먼저 짜져야 한다. 주제가 뭐가 되어도 동작해야 하고 하드코딩 금지."

흐름:  주제 확정 → plan_data_sources(topic) → [설계도: series별 {지표·단위·차트·출처후보·쿼리}]
       → (실행기 collect_chart_data 가 설계도대로 조준 수집)

설계는 *완전 동적* — LLM 이 주제를 보고 매번 series·출처·쿼리를 새로 결정한다.
provider(출처 메커니즘)는 *고정 카탈로그*(아래 _SOURCE_CATALOG, 11종)에서 LLM 이 고를 뿐,
주제별 if-else 분기는 어디에도 없다. 카탈로그는 "가용 도구 목록"이지 주제 로직이 아니다.
"""
from __future__ import annotations
import json
import logging
import re

log = logging.getLogger("jarvis.collector.planner")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

# ── 가용 출처 메커니즘 카탈로그 (provider 능력 설명 — LLM 이 주제에 맞게 *선택* 한다) ──────
#   ★ 이건 "도구 목록"이지 주제 로직이 아님. provider 추가 시 여기에 한 줄 추가하면 LLM 이 자동 활용.
_SOURCE_CATALOG = {
    "kosis":      "통계청 국가통계포털 — 인구·산업·고용·물가·소비·지역경제 등 공식 통계표(시계열)",
    "ecos":       "한국은행 ECOS — 거시경제(기준금리·환율·통화량·물가·국제수지·실업률) 시계열",
    "dart":       "금융감독원 전자공시 — 상장기업 재무제표·사업보고서·직원수·매출·영업이익",
    "krx":        "한국거래소 — 상장 종목 주가·등락률·거래량·시가총액",
    "finance":    "글로벌 시장지표(yfinance) — 해외 지수(S&P·나스닥)·환율·금·유가·미국채",
    "academic":   "arXiv 학술 논문 — 기술·과학·AI·경제 연구의 수치·통계(영어 논문 출처)",
    "kci":        "KCI 국내 학술논문(한국연구재단)+Crossref/Semantic Scholar — 국내 연구의 수치·통계(한국어 논문 우선, 거짓 없음)",
    "naver_news": "네이버 뉴스 — 한국어 시사·정책·기업 뉴스에 인용된 수치(가장 정확한 한국어 뉴스)",
    "news":       "Google News + 경제지(한국경제·매경·연합) — 뉴스 인용 수치",
    "kor_econ":   "산업부·중소벤처부 보도자료 + 네이버금융 — 정부 정책·산업 공식 발표 수치",
    "web":        "위키백과·지식백과 — 개념·배경·정의(수치보다 설명 위주)",
    "blog":       "네이버 블로그 — 체감·후기(보조, 신뢰도 낮음)",
}
_VALID_SOURCES = set(_SOURCE_CATALOG)

_PLAN_SYSTEM = """당신은 데이터 저널리스트의 '데이터 소싱 설계자'다. 글 주제가 주어지면,
그 주제를 가장 잘 설명할 *차트용 데이터 series* 들을 정하고, 각 series 를 *어느 출처에서 어떤
검색어로* 받을지 설계한다. 절대 수치를 지어내지 않는다 — 설계만 한다(실제 수집은 다른 단계)."""

_PLAN_PROMPT = """글 주제: {topic}
섹터: {sector}
맥락: {desc}

[사용 가능한 출처(이 키만 sources 에 사용)]
{catalog}

이 주제에 *진짜 의미 있는* 차트용 데이터 series 를 3~6개 설계하라. 각 series:
- name: 지표명(한국어, 구체적). 예: "연도별 발행액 추이"
- unit: 단위(반드시). 예: "조원","억원","%","명","개","원","pt","달러"
- chart: line(추이) | bar(항목비교) | stat(단일 핵심수치) | donut(비중)
- sources: 위 출처 키 중 *이 지표에 맞는* 1~3개 (우선순위 순). 공식 통계·정부·공시를 기사보다 우선.
- query: 그 출처에서 검색할 *구체적 한국어 검색어*(주제+지표). 예: "지역사랑상품권 발행액 연도별"

원칙:
- 주제에 맞춰 series·출처·검색어를 *완전히 새로* 설계 (예시 복붙 금지).
- ★ **각도(aspect)를 다양하게** (사용자 박제 2026-07-01): 같은 지표의 변형(예: 만족도-이용유무,
  만족도-불만족이유)만 여러 개 넣지 마라. *서로 다른 차원* 으로 골고루 설계하라 —
  ① 규모·발행액(시계열) ② 수량·개수(가맹점 수·이용자 수) ③ 구성비·비중(업종별·연령별)
  ④ 비교(지역별·연도별) ⑤ 평가·효과(만족도·경제효과). 최소 3개 이상 서로 다른 차원을 커버.
- 출처 우선순위: ① 공식 통계·공시 API(kosis·ecos·dart·kor_econ) → ② **논문(academic·kci)** →
  ③ 뉴스(naver_news·news)·웹(web). ★ 논문은 거짓이 없으니 *API 다음으로 적극 활용*하라.
  ★ 규모·발행액·추이 series 는 뉴스(news·naver_news)·정부보도(kor_econ)를 꼭 후보에 — 통계표에
  없는 최신 규모 수치가 거기 있다.
- 한 series 의 sources 는 2~4개를 넉넉히(우선순위 순) — 데이터는 많을수록 좋다(여러 출처 시도).
- 데이터로 만들 수 없는 추상 주장은 series 로 넣지 마라(수치 series 만). series 는 4~6개로 풍부하게.

JSON만 출력:
{{"series":[{{"name":"...","unit":"...","chart":"line","sources":["kosis","news"],"query":"..."}}]}}"""


def _extract_json(raw):
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", str(raw))
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _sanitize(plan: dict) -> list[dict]:
    out = []
    for s in (plan or {}).get("series") or []:
        name = str(s.get("name", "")).strip()
        unit = str(s.get("unit", "")).strip()
        query = str(s.get("query", "")).strip()
        chart = str(s.get("chart", "bar")).strip().lower()
        if chart not in ("line", "bar", "stat", "donut"):
            chart = "bar"
        srcs = [x for x in (s.get("sources") or []) if x in _VALID_SOURCES]
        if not name or not query:
            continue
        if not srcs:                       # 출처 미지정 → 안전 기본(정부·논문·뉴스)
            srcs = ["kor_econ", "academic", "naver_news"]
        out.append({"name": name, "unit": unit, "chart": chart,
                    "sources": srcs[:4], "query": query})
    return out[:6]


def _fallback_plan(topic: str, syns: list) -> list[dict]:
    """★ LLM 설계 실패 시 결정론 폴백 (사용자 박제 2026-07-01): 주제 불문 *보편 aspect* 스캐폴드.
    규모·현황·추이·구성·평가 5차원을 주제명(+동의어)으로 조준 → step1 이 다양한 지표를 수집.
    하드코딩(주제별 if)이 아니라 *aspect 템플릿* — 어떤 주제든 동일 적용."""
    term = (syns[0] if syns else topic)   # 동의어(정식명) 우선 — KOSIS 수율
    return [
        {"name": f"{topic} 규모·발행 추이", "unit": "", "chart": "line",
         "sources": ["news", "kor_econ", "kosis", "ecos"], "query": f"{term} 규모 발행"},
        {"name": f"{topic} 현황·수량 비교", "unit": "", "chart": "bar",
         "sources": ["kosis", "kor_econ", "news"], "query": f"{term} 현황"},
        {"name": f"{topic} 구성·비중", "unit": "%", "chart": "donut",
         "sources": ["kosis", "news", "kor_econ"], "query": f"{term} 비중 구성"},
        {"name": f"{topic} 평가·효과", "unit": "%", "chart": "bar",
         "sources": ["kosis", "kci", "academic", "news"], "query": f"{term} 만족도 효과"},
    ]


def plan_data_sources(topic: str, sector: str = "", description: str = "") -> list[dict]:
    """주제 → 데이터 소싱 설계도(series 목록). LLM 동적 설계. 실패 시 결정론 aspect 폴백.

    반환: [{"name","unit","chart","sources":[provider...],"query"}, ...]
    """
    import time as _time
    topic = (topic or "").strip()
    if not topic:
        return []
    catalog = "\n".join(f"- {k}: {v}" for k, v in _SOURCE_CATALOG.items())
    prompt = _PLAN_PROMPT.format(topic=topic, sector=sector or "-",
                                 desc=(description or topic)[:400], catalog=catalog)
    # ★ 재시도 3회 + 백오프 (사용자 박제 2026-07-01): LLM 이 가끔 빈/파싱불가/rate-limit 응답 →
    #   설계 0개 → 수집 만족도 쏠림. 설계는 다양성의 시작점이라 *반드시* 성공하도록 재시도·백오프.
    for _attempt in range(3):
        try:
            from shared.llm import invoke_text
            raw = invoke_text("analyzer", prompt, system=_PLAN_SYSTEM,
                              max_tokens=1100, temperature=0.2 if _attempt == 0 else 0.4)
            plan = _sanitize(_extract_json(raw))
            if plan:
                log.info(f"[planner] '{topic}' → {len(plan)}개 series 설계 (시도 {_attempt + 1})")
                return plan
        except Exception as e:
            log.warning(f"[planner] 설계 시도{_attempt + 1} 실패: {e}")
            _g_report("collector", e, module=__name__, func_name="plan_data_sources")
        if _attempt < 2:
            _time.sleep(2 + _attempt * 3)   # 백오프 (rate-limit 회복)
    # ★ LLM 3회 전패 → 결정론 aspect 폴백 (빈 설계로 만족도 쏠리지 않게 다차원 보장)
    try:
        from JARVIS09_COLLECTOR.chart_data import _expand_theme
        _syns = _expand_theme(topic)
    except Exception:
        _syns = []
    fb = _fallback_plan(topic, _syns)
    log.warning(f"[planner] '{topic}' LLM 설계 3회 전패 → 결정론 aspect 폴백 {len(fb)}개")
    return fb


__all__ = ["plan_data_sources", "_SOURCE_CATALOG"]
