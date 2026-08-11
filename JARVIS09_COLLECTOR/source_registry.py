"""JARVIS09_COLLECTOR/source_registry.py — 출처(source_type) 단일 진실 소스 (SSOT).

★ 사용자 박제 2026-07-24: "논문 하나 지우는데 17파일을 훑어야 했다 — 유지관리가 안 되는 설계."
  종전엔 한 source_type 의 정보(신뢰 티어·provider 클래스·카탈로그 설명·텍스트여부·수집상한·
  차트랭크·dedup우선순위)가 models·collector_engine·chart_data·data_planner·providers 등에
  *사본* 으로 흩어져, 소스 하나를 넣거나 빼려면 전체를 훑어야 했다. 이제 여기 `SOURCES` 에 **한 줄**.

  아래 파생 뷰가 전부 SOURCES 에서 자동 생성된다 (② 동적설계 — 사본 0):
    · SOURCE_TRUST_TIER  ← models 가 re-export (신뢰 티어, 중복·충돌 해소)
    · SOURCE_NAMES       ← 이미지·본문의 출처 표기 (JARVIS06 이 이름을 지어내지 않도록)
    · main_providers()   ← collector_engine._PROVIDERS (메인 수집 팬아웃, 순서 보존)
    · provider_class()   ← chart_data 의 source_type→provider 조회
    · CATALOG            ← data_planner._SOURCE_CATALOG (LLM 출처 선택 카탈로그)
    · TEXT_SOURCES       ← chart_data._TEXT_SOURCES (제목 관련성 필터 대상)
    · MAX_PER_SOURCE     ← collector_engine._PROVIDER_LIMITS (소스별 수집 상한)
    · CHART_TRUST_RANK   ← chart_data._SOURCE_TRUST_RANK (차트 신뢰 재정렬 순위)
    · PROV_RANK          ← chart_data._PROV_RANK (차트 dedup 라운드로빈 우선순위)

  ★ 새 소스 추가/삭제 = SOURCES 에 SourceSpec 한 줄 넣거나 빼기. 다른 파일 손 댈 필요 없음.
  provider 는 "module:Class" 문자열로 두고 *지연 import* — 이 모듈은 09 내부를 import 하지
  않는 leaf 라 순환 import 가 없다(models 가 이걸 import 하므로 반대 방향 금지).
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSpec:
    """한 수집 출처의 *모든* 정체성 — 여기가 유일한 정의처. int|None 필드는 None=미설정(소비처 default)."""
    key: str                    # source_type ("kosis")
    tier: int                   # 신뢰 티어 (1=공식 데이터 API … 5=블로그). 겹치는 데이터 충돌 해소용.
    provider: str = ""          # "module:Class" (providers/ 패키지) — 수집기 있으면. 없으면 문서에만 등장.
    main: bool = True           # 메인 수집 팬아웃(_PROVIDERS) 포함 여부. discover 는 차트/발견 전용이라 False.
    name: str = ""              # ★ 사람이 읽는 출처 표시명 ("한국은행") — 아래 주석 참조.
    desc: str = ""              # data_planner LLM 카탈로그 *설명* (있으면 출처 선택지로 노출).
    is_text: bool = False       # 차트 제목 관련성 1차 필터 대상 (뉴스·통계 등 텍스트 출처).
    max_items: int | None = None    # 소스별 수집 상한 (None=default 8). 무한루프 방지 안전망, 선별은 별도.
    chart_rank: int | None = None   # 차트 신뢰 재정렬 순위 (None=default 99, 낮을수록 우선). ERRORS [421].
    prov_rank: int | None = None    # 차트 dedup 라운드로빈 우선순위 (None=default 5, 0 유효). KOSIS 독점 방지.

    @property
    def catalog(self) -> str:
        """LLM 카탈로그/표기용 한 줄 = "표시명 — 설명" *파생*.

        설명이 없는 소스는 이름만 돌려준다. LLM 선택지(CATALOG)에 오르는 기준은 이 값이
        아니라 `desc` 의 유무다 — 설명 없는 출처를 선택지로 내밀면 LLM 이 근거 없이 고른다.
        """
        if self.name and self.desc:
            return f"{self.name} — {self.desc}"
        return self.name or self.desc


# ★ 출처 표시명(`name`)의 주인은 여기다 (사용자 박제 2026-08-10).
#   왜 필드로 갖는가 — 종전엔 표시명이 어디에도 없어서, 소비처(JARVIS06 `template_engine`)가
#   `catalog` 문자열의 *머리말을 잘라* 이름인 척 쓰고 있었다. 그 결과 실측 18키 중 10키가
#   빈 라벨이 됐고("finance" 는 머리말이 '글로벌 시장지표(yfinance)' 라 소문자 토큰 가드에
#   걸려 탈락, `bok_official`·`kofia`·`customs` 등 7키는 카탈로그 자체가 없어 탈락) 이미지
#   푸터가 '데이터 출처: 공개 통계' 로 열화했다. 이름을 06 이 지어내면 그게 사본이다 —
#   출처가 무엇인지 아는 것은 그 출처를 수집하는 09 뿐이다.
#   ★ 표시명은 *실제로 그 데이터를 공표한 기관* 이어야 한다. 모르면 비워 둘 것 —
#     빈 값이면 소비처가 출처 줄을 생략한다(거짓 출처 < 출처 없음).
#
# ★★★ 출처 단일 목록 — 소스 하나 = 한 줄. 순서 = _PROVIDERS 메인 팬아웃 순서(신뢰 정렬은 별도) ★★★
SOURCES: list[SourceSpec] = [
    # ── 뉴스(tier 2) ──
    SourceSpec("naver_news", 2, "naver_news_provider:NaverNewsProvider", is_text=True,
               max_items=30, chart_rank=4, prov_rank=3, name="네이버 뉴스",
               desc="한국어 시사·정책·기업 뉴스에 인용된 수치(가장 정확한 한국어 뉴스)"),
    SourceSpec("news", 2, "news_provider:NewsProvider", is_text=True,
               max_items=25, chart_rank=4, prov_rank=2, name="경제 뉴스",
               desc="Google News + 경제지(한국경제·매경·연합·이데일리) 인용 수치"),
    # ── 기사·전문지(tier 3) ──
    SourceSpec("kor_econ", 3, "kor_econ_provider:KorEconProvider", is_text=True,
               max_items=15, chart_rank=3, prov_rank=2, name="정부·산업 공개자료",
               desc="산업부·중소벤처부 보도자료 + 네이버금융의 정부 정책·산업 공식 발표 수치"),
    # ── 공식 데이터 API(tier 1) ──
    SourceSpec("krx", 1, "krx_provider:KrxProvider", max_items=20, chart_rank=1, prov_rank=2,
               name="한국거래소",
               desc="상장 종목 주가·등락률·거래량·시가총액·코스닥/코스피 업종별 시가총액 비중·코스닥 150 섹터 지수(8개 전체)"),
    SourceSpec("blog", 5, "blog_provider:BlogProvider", max_items=10, chart_rank=7,
               name="네이버 블로그", desc="체감·후기(보조, 신뢰도 낮음)"),
    SourceSpec("web", 4, "web_provider:WebProvider", is_text=True,
               max_items=10, chart_rank=6, prov_rank=4, name="위키백과·지식백과",
               desc="개념·배경·정의(수치보다 설명 위주)"),
    SourceSpec("dart", 1, "dart_provider:DartProvider", max_items=20, chart_rank=2, prov_rank=1,
               name="금융감독원 전자공시",
               desc="상장기업 재무제표·사업보고서·직원수·매출·영업이익"),
    SourceSpec("ecos", 1, "ecos_provider:EcosProvider", max_items=20, chart_rank=1, prov_rank=1,
               name="한국은행 ECOS",
               desc="거시경제(기준금리·환율·통화량·물가·국제수지·실업률) 시계열"),
    SourceSpec("kosis", 1, "kosis_provider:KosisProvider", is_text=True,
               max_items=20, chart_rank=2, prov_rank=0, name="통계청 국가통계포털",
               desc="인구·산업·고용·물가·소비·지역경제 등 공식 통계표(시계열)"),
    SourceSpec("finance", 1, "finance_provider:FinanceProvider", max_items=15, chart_rank=1, prov_rank=2,
               name="Yahoo Finance",
               desc="해외 지수(S&P·나스닥)·환율·금·유가·미국채 등 글로벌 시장지표(yfinance)"),
    SourceSpec("bok_official", 1, "bok_provider:BokProvider", max_items=10, name="한국은행"),
    SourceSpec("customs", 1, "customs_provider:CustomsProvider", max_items=10,
               name="관세청 무역통계"),   # 원자료=관세청, 배포=KOSIS
    SourceSpec("kofia", 1, "kofia_provider:KofiaProvider", max_items=8,
               name="금융투자협회"),      # 채권유통수익률 자료원(ECOS 경유 수집)
    SourceSpec("fss", 1, "fss_provider:FssProvider", max_items=8, name="금융감독원"),
    SourceSpec("mlit", 1, "mlit_provider:MlitProvider", max_items=8, name="국토교통부"),
    SourceSpec("employment", 1, "employment_provider:EmploymentProvider", max_items=10,
               name="통계청 고용통계"),   # 경제활동인구조사(통계청) — KOSIS 경유
    # ── 웹 발견 — 메인 팬아웃 제외(차트·discover 레그 전용) ──
    SourceSpec("discover", 5, "discovery_provider:DiscoveryProvider", main=False, chart_rank=5,
               name="웹 발견",
               desc="구글(DuckDuckGo)·네이버검색·공공데이터포털로 *실제 데이터 페이지*를 "
                       "찾아 받음. 위 카탈로그에 딱 맞는 출처가 없는 주제(지역·교통·특정기업·신기술·해외 등)는 "
                       "*반드시* 이것을 넣어라. 어떤 주제든 동작. query 를 구체적으로."),
    # ── provider 없는 source_type (수집기 없이 문서 source_type 으로만 등장) ──
    #   표시명 없음 — 공표 기관을 특정할 수 없는 일반 웹 데이터다(지어내지 않는다).
    SourceSpec("web_data", 3),
]

_BY_KEY: dict[str, SourceSpec] = {s.key: s for s in SOURCES}

# ── 파생 뷰 (사본 0 — 전부 SOURCES 에서 자동 생성) ─────────────────────────
SOURCE_TRUST_TIER: dict[str, int] = {s.key: s.tier for s in SOURCES}
# ★ source_type → 사람이 읽는 출처 표시명. 이미지·본문의 '데이터 출처:' 는 이 표에서만 온다.
SOURCE_NAMES: dict[str, str] = {s.key: s.name for s in SOURCES if s.name}
# LLM 선택지는 *설명이 있는* 소스만 (이름만 있는 소스를 선택지로 내밀지 않는다).
CATALOG: dict[str, str] = {s.key: s.catalog for s in SOURCES if s.desc}
TEXT_SOURCES: frozenset = frozenset(s.key for s in SOURCES if s.is_text)
MAX_PER_SOURCE: dict[str, int] = {s.key: s.max_items for s in SOURCES if s.max_items is not None}
CHART_TRUST_RANK: dict[str, int] = {s.key: s.chart_rank for s in SOURCES if s.chart_rank is not None}
PROV_RANK: dict[str, int] = {s.key: s.prov_rank for s in SOURCES if s.prov_rank is not None}


def _load(provider_ref: str):
    """'module:Class' → provider 클래스 (지연 import — 순환 방지)."""
    mod_name, _, cls_name = provider_ref.partition(":")
    mod = importlib.import_module(f"JARVIS09_COLLECTOR.providers.{mod_name}")
    return getattr(mod, cls_name)


def source_name(source_type: str) -> str:
    """source_type → 사람이 읽는 출처 표시명 (없으면 ""). 표시명 조회 단일 진입점.

    소비처(JARVIS06 이미지 푸터 등)는 이 함수/`SOURCE_NAMES` 만 본다. 이름이 없으면
    빈 문자열이 나가고, 소비처는 출처 줄을 *생략* 한다 — 없는 이름을 지어내지 않는다.
    """
    spec = _BY_KEY.get((source_type or "").strip().lower())
    return spec.name if spec else ""


def provider_class(source_type: str):
    """source_type → provider 클래스 (없으면 None). chart_data 의 source→provider 조회 단일 진입점."""
    spec = _BY_KEY.get((source_type or "").strip().lower())
    return _load(spec.provider) if (spec and spec.provider) else None


def main_providers() -> list:
    """메인 수집 팬아웃 provider 인스턴스 목록 (SOURCES 순서 보존). collector_engine._PROVIDERS."""
    return [_load(s.provider)() for s in SOURCES if s.main and s.provider]


def all_provider_classes() -> dict:
    """source_type → provider 클래스 (provider 있는 전부, discover 포함). chart_data._m 파생용."""
    return {s.key: _load(s.provider) for s in SOURCES if s.provider}


__all__ = ["SourceSpec", "SOURCES", "SOURCE_TRUST_TIER", "SOURCE_NAMES", "CATALOG",
           "TEXT_SOURCES", "MAX_PER_SOURCE", "CHART_TRUST_RANK", "PROV_RANK",
           "source_name", "provider_class", "main_providers", "all_provider_classes"]
