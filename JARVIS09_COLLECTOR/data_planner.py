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
import os
import re

# ★ 인프라 빈응답(스로틀) 시 런타임 1회 재시도 전 backoff (사용자 박제 2026-07-18).
#   warm_plan 캐시가 정상이면 이 경로 미발동 — 발행창 스로틀 회복 여지 확보용 2차 방어선.
_PLAN_BACKOFF_SEC = float(os.getenv("PLAN_BACKOFF_SEC", "8") or "8")

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
    "krx":        "한국거래소 — 상장 종목 주가·등락률·거래량·시가총액·코스닥/코스피 업종별 시가총액 비중·코스닥 150 섹터 지수(8개 전체)",
    "finance":    "글로벌 시장지표(yfinance) — 해외 지수(S&P·나스닥)·환율·금·유가·미국채",
    "academic":   "arXiv 학술 논문 — 기술·과학·AI·경제 연구의 수치·통계(영어 논문 출처)",
    "kci":        "KCI 국내 학술논문(한국연구재단)+Crossref/Semantic Scholar — 국내 연구의 수치·통계(한국어 논문 우선, 거짓 없음)",
    "naver_news": "네이버 뉴스 — 한국어 시사·정책·기업 뉴스에 인용된 수치(가장 정확한 한국어 뉴스)",
    "news":       "Google News + 경제지(한국경제·매경·연합) — 뉴스 인용 수치",
    "kor_econ":   "산업부·중소벤처부 보도자료 + 네이버금융 — 정부 정책·산업 공식 발표 수치",
    "web":        "위키백과·지식백과 — 개념·배경·정의(수치보다 설명 위주)",
    "blog":       "네이버 블로그 — 체감·후기(보조, 신뢰도 낮음)",
    # ★ discover — 위 고정 카탈로그로 못 받는 주제의 *만능 발견 경로* (사용자 박제 2026-07-01)
    "discover":   "웹 발견 — 구글(DuckDuckGo)·네이버검색·공공데이터포털로 *실제 데이터 페이지*를 "
                  "찾아 받음. 위 카탈로그에 딱 맞는 출처가 없는 주제(지역·교통·특정기업·신기술·해외 등)는 "
                  "*반드시* 이것을 넣어라. 어떤 주제든 동작. query 를 구체적으로.",
}
_VALID_SOURCES = set(_SOURCE_CATALOG)

# ★ 시장 지표 키워드 — 이 키워드가 series name/query 에 있으면 공식 API 소스만 허용.
# web/blog/news 에서 가져온 시장 지표 수치는 틀릴 확률이 높음 (ERRORS [416][418]).
_MARKET_INDICATOR_KWS = frozenset([
    # 시장 전체 지표
    "시가총액", "코스닥", "코스피", "주가지수", "증시", "지수",
    "기준금리", "환율", "달러", "나스닥", "s&p", "다우",
    "kosdaq", "kospi", "nasdaq",
    # ★ 업종별 시장 구성 — 섹터 비중은 KRX 실데이터로만 (ERRORS [418])
    # "코스닥 업종별 비중", "바이오 반도체 IT 비중" 같은 쿼리 포함
    "업종별", "업종비중", "섹터비중", "업종구성", "업종 시가총액",
    "바이오 반도체", "반도체 바이오",  # 코스닥 섹터 조합 쿼리 (web 폴백 차단)
    # ★ 코스닥 150 섹터 지수 — 8개 하위 지수는 KRX로만 (ERRORS [420])
    # KOSIS/web 수집 시 8개 중 일부만 반환 → 최고/최저 KPI 오표시 사례
    "코스닥 150", "kosdaq 150", "kosdaq150",
    "150 소재", "150 헬스케어", "150 정보기술", "150 산업재", "섹터 지수",
    # ★ 주간 등락률 — web 크롤링 수치 완전히 틀림 (ERRORS [424])
    # 코스피 -7.57% 실제 vs 이미지 +1.9% 표시 — 9.5p 오차 사례
    "주간 등락률", "주간등락률", "등락률", "등락", "수익률",
    "주간 수익률", "주간수익률", "주간 변동", "주간변동", "이번주", "금주",
])
# 시장 지표에 허용되는 공식 소스 — web/blog 은 틀린 수치 다수
_MARKET_OFFICIAL_SOURCES = frozenset(["finance", "krx", "ecos", "kor_econ", "kosis"])

_PLAN_SYSTEM = """당신은 데이터 저널리스트의 '데이터 소싱 설계자'다. 글 주제가 주어지면,
그 주제를 가장 잘 설명할 *차트용 데이터 series* 들을 정하고, 각 series 를 *어느 출처에서 어떤
검색어로* 받을지 설계한다. 절대 수치를 지어내지 않는다 — 설계만 한다(실제 수집은 다른 단계).
주제의 한국 정부통계·공식자료 검색용 정식 명칭·동의어가 있으면 synonyms 에 최대 3개.
예: '지역화폐' → '지역사랑상품권'. 없으면 빈 배열."""

_PLAN_PROMPT = """글 주제: {topic}
섹터: {sector}
엔티티유형: {entity_type}  (참고: 지표→ecos/finance, 기업→dart/krx/finance, 산업→kosis/kor_econ, 정책→kosis/kor_econ, 사건→naver_news/kosis, 제품→discover/news 우선. 금융 주제면 금리·환율·통화량(ecos)·종목 재무(dart)를 적극 조준)
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
- ★★ **카탈로그는 '아는 빠른 길' 힌트일 뿐, 상한이 아니다** (사용자 박제 2026-07-01):
  주제는 엄청 다양하다(지역·교통·특정기업·신기술·해외·문화 등). 위 고정 출처(kosis·ecos·dart 등)는
  *한국 거시경제·공공통계* 에만 맞는다. **주제가 그 범위 밖이면 그 출처는 0건**이다.
  그럴 땐 *반드시* `discover` 를 sources 에 넣어라 — 웹에서 실제 데이터 페이지를 찾아 받는다.
  ★ 확신이 안 서면 각 series 의 sources 마지막에 `discover` 를 항상 폴백으로 추가하라(손해 없음).
- ★ **각도(aspect)를 다양하게**: 같은 지표의 변형만 여러 개 넣지 마라. *서로 다른 차원* 으로
  골고루 — ① 규모·추이(시계열) ② 수량·개수 ③ 구성비·비중 ④ 비교(지역별·연도별) ⑤ 평가·효과.
  최소 3개 이상 서로 다른 차원을 커버.
- 출처 우선순위: ① 주제에 딱 맞는 공식 통계·공시(kosis·ecos·dart·kor_econ) → ② **논문(academic·kci)**
  → ③ 뉴스(naver_news·news) → ④ **discover(웹 발견 — 위로 안 되는 주제의 만능 경로)**.
  ★ 논문은 거짓이 없으니 적극 활용. ★ 최신 규모·실적 수치는 뉴스·discover 에 많다.
- 한 series 의 sources 는 2~4개를 넉넉히(우선순위 순) — 데이터는 많을수록 좋다(여러 출처 시도).
- 데이터로 만들 수 없는 추상 주장은 series 로 넣지 마라(수치 series 만). series 는 4~6개로 풍부하게.

JSON만 출력:
{{"synonyms":["공식명1"],"series":[{{"name":"...","unit":"...","chart":"line","sources":["kosis","news"],"query":"..."}}]}}"""


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


def _sanitize(plan: dict) -> tuple[list[dict], list[str]]:
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
        if not srcs:                       # 출처 미지정 → 안전 기본(뉴스·논문 + 웹발견 폴백)
            srcs = ["naver_news", "academic", "discover"]
        # ★ 시장 지표 키워드 → 공식 API 소스만 허용, web/blog 제거 (ERRORS [416])
        # web/blog 소스의 시장 수치는 틀린 사례 다수 — 공식 소스가 없으면 finance/ecos 기본값
        _combined = (name + " " + query).lower()
        if any(kw in _combined for kw in _MARKET_INDICATOR_KWS):
            official = [x for x in srcs if x in _MARKET_OFFICIAL_SOURCES]
            if not official:
                official = ["finance", "ecos"]
            srcs = official
            log.debug(f"[planner] 시장지표 감지 '{name}' → 소스 강제: {srcs}")
        out.append({"name": name, "unit": unit, "chart": chart,
                    "sources": srcs[:4], "query": query})
    syns = [str(t).strip() for t in (plan or {}).get("synonyms") or []
            if str(t).strip()][:3]
    return out[:6], syns


# ★ entity_type 7버킷 범용 스캐폴드 (사용자 박제 2026-07-18) — 주제 if-else 아님(entity_type은
#   topic-agnostic 범용 분류). LLM 설계 실패 시에도 주제의 '경제·금융 각도'를 조준하도록
#   각도·단위·chart·출처·쿼리를 entity_type 별로 파생. 각 행: (지표명, 단위, chart, 출처우선, 쿼리{t}).
_ENTITY_SCAFFOLD: dict[str, list[tuple]] = {
    "기업": [("매출·영업이익 추이", "억원", "line", ["dart", "discover", "news"], "{t} 매출 영업이익"),
            ("직원수·규모", "명", "bar", ["dart", "discover"], "{t} 직원수 종업원"),
            ("주가·시가총액 추이", "원", "line", ["krx", "finance", "discover"], "{t} 주가 시가총액")],
    "산업": [("시장규모 추이", "조원", "line", ["kosis", "kor_econ", "discover"], "{t} 시장규모 추이"),
            ("수출·생산 추이", "억달러", "line", ["kosis", "kor_econ", "discover"], "{t} 수출 생산"),
            ("업체·점유율 구성", "%", "donut", ["kor_econ", "discover", "news"], "{t} 점유율 구성")],
    "지표": [("시계열 추이", "", "line", ["ecos", "finance", "kosis"], "{t} 추이"),
            ("최근 수준", "", "stat", ["ecos", "finance"], "{t} 최근")],
    "정책": [("연도별 예산·집행", "억원", "line", ["kosis", "kor_econ", "naver_news"], "{t} 예산 집행"),
            ("수혜·대상 건수", "건", "bar", ["kosis", "kor_econ", "naver_news"], "{t} 건수 현황")],
    "사건": [("연도별 발생 건수", "건", "line", ["kosis", "naver_news", "discover"], "{t} 연도별 건수"),
            ("지역별·규모", "", "bar", ["kosis", "naver_news", "discover"], "{t} 지역별")],
    "제품": [("판매·출하 추이", "", "line", ["discover", "news", "naver_news"], "{t} 판매량 출하"),
            ("가격 추이", "원", "line", ["discover", "news"], "{t} 가격 추이"),
            ("시장 점유율", "%", "donut", ["discover", "news"], "{t} 점유율")],
    "기타": [("관련 통계 추이", "", "line", ["discover", "naver_news"], "{t}"),
            ("현황 수치", "", "bar", ["discover", "naver_news"], "{t} 현황")],
}


def _fallback_plan(topic: str, syns: list | None = None,
                   sector: str = "", entity_type: str = "") -> list[dict]:
    """★ LLM 설계 실패 시 *주제 적응형* 결정론 폴백 (사용자 박제 2026-07-18).

    옛 폴백은 topic 문자열만으로 고정 4-aspect(규모·추이/현황·실적/구성·비중/핵심지표)를 뱉어
    신도평화대교·라면·전세사기가 *모두 동일* 했다. 이제 entity_type(7버킷 범용 분류)로 각도·단위·
    출처·쿼리를 파생 → 주제마다 다른 series. 정식명(synonyms[0])이 있으면 쿼리에 써 KOSIS 수율 회복.
    사건/기타는 전문용어 연접 없이 bare 쿼리 + discover 우선 → 단일 개체 발견 오염 제거."""
    term = ((syns or [None])[0]) or topic
    key = entity_type if entity_type in _ENTITY_SCAFFOLD else "기타"
    rows = _ENTITY_SCAFFOLD[key]
    out = []
    for (name, unit, chart, srcs, q) in rows:
        _srcs = list(srcs) + (["discover"] if "discover" not in srcs else [])
        out.append({"name": f"{topic} {name}", "unit": unit, "chart": chart,
                    "sources": _srcs[:4], "query": q.format(t=term)})
    return out


def plan_data_sources(topic: str, sector: str = "", description: str = "",
                      profile: dict | None = None, synonyms: list | None = None) -> dict:
    """주제 → 데이터 소싱 설계도. LLM 동적 설계. 실패 시 *주제 적응형* 결정론 폴백.

    ★ 신호 관통 (사용자 박제 2026-07-18): profile.entity_type 을 LLM 프롬프트 힌트 + 폴백
      스캐폴드 키로 사용해 '이 주제가 무엇인지'(기업/산업/지표/정책/사건/제품)를 알고 경제·금융
      각도를 조준한다. synonyms(정식명) 는 폴백 쿼리에 사용해 공식통계 수율 회복.

    반환: {"series": [...], "synonyms": [...]}
    """
    topic = (topic or "").strip()
    if not topic:
        return {"series": [], "synonyms": []}
    entity_type = str((profile or {}).get("entity_type", "")).strip()
    _syns_in = [str(s).strip() for s in (synonyms or []) if str(s).strip()]
    # ★ 2자 이하·섹터 수준 단독 단어는 LLM 설계 불가 — 폴백 즉시 반환 (90s hang 방지)
    _GENERIC_SKIP = {"경제", "금융", "무역", "부동산", "고용", "소비", "경기", "증시",
                     "주식", "투자", "환율", "금리", "물가", "수출", "수입", "통상"}
    if len(topic) <= 2 or topic in _GENERIC_SKIP:
        log.info(f"[planner] '{topic}' 너무 짧거나 섹터 단어 — LLM 설계 스킵 (결정론 폴백)")
        return {"series": _fallback_plan(topic, _syns_in, sector, entity_type),
                "synonyms": _syns_in}
    # ★ 발행창 감지 시 LLM 설계 스킵 (사용자 박제 2026-07-18) — 발행창(JARVIS_LLM_DEADLINE_TS 활성)은
    #   Max 스로틀 포화라 planner LLM 이 사실상 매번 빈응답→90s 낭비→폴백. warm(저부하창) 이 이미
    #   선확정했어야 하며, warm 미스 시엔 성공 못 할 호출을 임계경로에 태우지 말고 곧장 결정론 스캐폴드.
    #   warm_plan(발행창 밖)에선 이 env 가 없어 정상적으로 LLM 설계 수행.
    if os.environ.get("JARVIS_LLM_DEADLINE_TS"):
        log.info(f"[planner] '{topic}'(유형:{entity_type or '?'}) 발행창 — LLM 설계 스킵, 결정론 스캐폴드 (warm 미스 안전 분기)")
        return {"series": _fallback_plan(topic, _syns_in, sector, entity_type),
                "synonyms": _syns_in}
    catalog = "\n".join(f"- {k}: {v}" for k, v in _SOURCE_CATALOG.items())
    prompt = _PLAN_PROMPT.format(topic=topic, sector=sector or "-",
                                 entity_type=entity_type or "-",
                                 desc=(description or topic)[:400], catalog=catalog)
    # ★ 외부 재시도 = JSON 파싱 실패 시만 (ERRORS [399] — 스로틀 근본 차단).
    from shared.llm import invoke_text
    for _attempt in range(2):
        try:
            # ★ _essential=True (ERRORS [300]): 설계는 수집 품질의 조타수 —
            #   회로 차단 중에도 1회 실시도 보장 (즉시 폴백 금지).
            raw = invoke_text("analyzer", prompt, system=_PLAN_SYSTEM,
                              max_tokens=1200, temperature=0.2 if _attempt == 0 else 0.5,
                              _essential=True, timeout=90)
        except Exception as e:
            log.warning(f"[planner] 설계 시도{_attempt + 1} 예외: {e}")
            _g_report("collector", e, module=__name__, func_name="plan_data_sources")
            break  # 예외 → 폴백
        if not raw.strip():
            # ★ 인프라 빈응답(스로틀)이고 첫 시도면 짧은 backoff 후 1회 재시도 — 즉시 break 금지
            #   (warm_plan 이 정상이면 이 경로 자체를 안 탐. 발행창 스로틀 회복 여지 확보).
            from shared.llm import circuit_is_open, last_call_infra_incomplete
            if _attempt == 0 and (circuit_is_open() or last_call_infra_incomplete()):
                import time as _t
                log.info(f"[planner] '{topic}' 빈응답(인프라) — {_PLAN_BACKOFF_SEC}s backoff 후 1회 재시도")
                _t.sleep(_PLAN_BACKOFF_SEC)
                continue
            log.warning(f"[planner] '{topic}' 빈 응답(스로틀) → 주제적응 폴백")
            break
        series, syns = _sanitize(_extract_json(raw))
        if series:
            _syns_out = syns or _syns_in
            log.info(f"[planner] '{topic}' → {len(series)}개 series 설계, 동의어 {_syns_out} (시도 {_attempt + 1})")
            return {"series": series, "synonyms": _syns_out}
        log.debug(f"[planner] 시도{_attempt + 1} JSON 파싱 실패 — 온도 상향 재시도")
    # ★ LLM 2회 전패 → 주제 적응형(entity_type) 폴백
    fb = _fallback_plan(topic, _syns_in, sector, entity_type)
    log.warning(f"[planner] '{topic}'(유형:{entity_type or '?'}) LLM 설계 실패 → 적응형 폴백 {len(fb)}개")
    return {"series": fb, "synonyms": _syns_in}


__all__ = ["plan_data_sources", "_SOURCE_CATALOG", "_fallback_plan"]
