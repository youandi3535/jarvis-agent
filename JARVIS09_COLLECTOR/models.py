"""JARVIS09_COLLECTOR/models.py — 수집 데이터 모델.

★ 사용자 박제 2026-06-07 — delta-aware 교류 프로토콜 지원:
   CollectionResult 에 `content_hash`(SHA1) + `fetched_at`(epoch sec) 추가.
   호출자(예: JARVIS06)가 이미 가진 hash 목록을 제외하고 신규/갱신분만
   수령할 수 있도록 fingerprint 부여.
"""
from __future__ import annotations
import hashlib
import math
import time as _time_mod
from dataclasses import dataclass, field, asdict
from datetime import datetime


def _hash_text(text: str) -> str:
    """SHA1 hex digest (앞 16자) — content fingerprint."""
    return hashlib.sha1((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


# ★ 출처 신뢰 우선순위 — 단일 진입점 (사용자 박제 2026-07-03 — ADR 013)
#   "논문 > API > 뉴스 > 기사 > 웹. 데이터가 겹치면 이 순서로 하나를 선택.
#    단, 수집은 받을 수 있는 곳 전부에서 다 받는다 — 논문만 받으면 안 된다."
#   이 티어는 *중복·충돌 해소 전용* — 수집 범위 제한에 사용 금지.
SOURCE_TRUST_TIER: dict[str, int] = {
    "academic": 1, "kci": 1,                                   # 논문
    "kosis": 2, "ecos": 2, "dart": 2, "krx": 2, "finance": 2, "bok_official": 2,  # 공식 데이터 API
    "customs": 2, "kofia": 2, "fss": 2, "mlit": 2, "employment": 2,              # 국내 공공 경제 API
    "naver_news": 3, "news": 3,                                # 뉴스
    "kor_econ": 4, "web_data": 4,                              # 기사·전문지
    "web": 5,                                                  # 웹
    "blog": 6,                                                 # 블로그
}


def trust_rank(source_type: str) -> int:
    """출처 신뢰 순위 (낮을수록 신뢰 높음). 미지 소스는 웹 수준(5)."""
    return SOURCE_TRUST_TIER.get((source_type or "").strip().lower(), 5)


# ★ 수집 쿼터 (사용자 박제 2026-07-06, v2 정정): "인포그래픽을 만들 수 있을 만큼"의 자료를
#   신뢰 서열대로 총 15개 확보 — 논문 최대 3, API 최대 7, 나머지 5(소스별 1개씩 라운드로빈).
#   상위 티어가 슬롯을 못 채우면 미달분을 다음 티어로 이월(cascade). 예: 논문 2개면
#   API 는 7+1=8개, 논문 0개면 API 10개, 논문·API 모두 0이면 나머지에서 15개 전부.
COLLECT_QUOTA_BUDGET = 15   # 총 수집 상한
COLLECT_PAPER_CAP    = 3    # 논문(academic·kci) 기본 상한
COLLECT_API_CAP      = 7    # 공식 데이터 API(kosis·ecos·dart·krx·finance) 기본 상한

_QUOTA_GROUP: dict[str, str] = {
    "academic": "paper", "kci": "paper",
    "kosis": "api", "ecos": "api", "dart": "api", "krx": "api", "finance": "api",
    # ★ 국내 공공 경제 API 6종 (2026-07-17): SOURCE_TRUST_TIER 에선 tier 2(공식 API)인데
    #   여기 누락돼 default "rest" 로 오분류 → tier2 라 뉴스(3)·웹(5)을 '나머지5' 슬롯에서
    #   밀어내 뉴스 0건 사고. 반드시 tier 분류와 동치 유지 (라이브 재현: 뉴스 0→5).
    "bok_official": "api", "customs": "api", "kofia": "api",
    "fss": "api", "mlit": "api", "employment": "api",
    # 나머지(naver_news·news·kor_econ·web·web_data·blog·discover 등) → "rest"
}


def quota_group(source_type: str) -> str:
    """수집 쿼터 그룹: paper(논문) | api(공식데이터) | rest(뉴스·기사·웹·블로그)."""
    return _QUOTA_GROUP.get((source_type or "").strip().lower(), "rest")


@dataclass
class RawDocument:
    """수집 직후 원본 문서."""
    url: str
    source_type: str          # blog | news | academic | finance | web
    raw_html: str = ""
    raw_text: str = ""
    title: str = ""
    published_at: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extra: dict = field(default_factory=dict)


@dataclass
class CollectionResult:
    """정제 완료 결과 — JARVIS02 WRITER 전달용.

    ★ delta 교류 필드 (사용자 박제 2026-06-07):
        content_hash: SHA1(cleaned_text)[:16] — 내용 동일성 판정
        fetched_at:   epoch seconds — 신선도 비교
    """
    theme: str
    source_type: str
    url: str
    title: str
    cleaned_text: str         # 잡음 제거된 원본 텍스트 (요약 아님)
    word_count: int = 0
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    meta: dict = field(default_factory=dict)
    content_hash: str = ""    # ★ 자동 계산 — __post_init__ 처리
    fetched_at: float = field(default_factory=_time_mod.time)

    def __post_init__(self) -> None:
        # content_hash 미지정 시 cleaned_text + title + url 조합으로 자동 산출.
        if not self.content_hash:
            seed = f"{self.url}|{self.title}|{self.cleaned_text}"
            self.content_hash = _hash_text(seed)


# ══════════════════════════════════════════════════════════════════════════
# ★ 통합 콘텐츠 파이프라인 계약 (사용자 공동설계 2026-07-05 — UNIFIED_PIPELINE_SPEC)
#   경제·테마·미래 카테고리가 동일 오케스트레이션을 타기 위한 단일 데이터 계약.
#   이 모듈은 stdlib-only leaf — JARVIS02/06 어디서 import 해도 순환 없음.
# ══════════════════════════════════════════════════════════════════════════

# ★ 엔티티 attr → 단위 표 (단일 소스). 암묵 단위를 명시화해 all_numbers() 가
#   단위보유 grounding 에서 엔티티 수치를 누락하지 않도록 함.
#   ★ 새 재무지표(예: PBR·배당수익률) 를 collect_stocks_data 에 추가하면
#     반드시 이 표도 동시 갱신 (미갱신 시 그 수치가 단위없이 방출 → 오차단 위험).
ATTR_UNITS: dict[str, str] = {
    "price": "원", "current_price": "원", "eps": "원", "bps": "원",
    "marcap": "조원", "market_cap": "조원", "revenue": "조원",
    "net_income": "억원", "op_income": "억원", "operating_income": "억원",
    "per": "배", "pbr": "배", "pcr": "배", "psr": "배",
    "roe": "%", "roa": "%", "op_margin": "%", "operating_margin": "%",
    "dividend_yield": "%", "change": "%", "change_pct": "%",
}

# ★ 카테고리 정책 레지스트리 (단일 소스). process_draft v2 가 collected.meta.category
#   로 조회. 새 카테고리 = dict 한 줄. min_images 는 BLOG_SUPREME_LAW 제8조(5+α) 준수.
# ★ allow_stock_financial (사용자 박제 2026-07-18): 테마주=개별 종목 재무(PER·ROE·영업이익률·
#   현재가) 차트 허용. 경제 브리핑=트렌드 경제·금융 상식/배경 글이므로 종목 재무 *배제*
#   (거시지표·개념 인포그래픽만). 두 글은 성격이 완전히 다름 → 데이터·이미지도 분리.
CATEGORY_POLICY: dict[str, dict] = {
    "theme":    {"min_images": 5, "chart_ai_fallback": True, "thumbnail_body_chars": 3000,
                 "allow_stock_financial": True},
    "economic": {"min_images": 5, "chart_ai_fallback": True, "thumbnail_body_chars": 3000,
                 "allow_stock_financial": False},
}
_DEFAULT_POLICY = {"min_images": 5, "chart_ai_fallback": True, "thumbnail_body_chars": 3000,
                   "allow_stock_financial": True}


def policy_for(category: str) -> dict:
    """카테고리 정책 조회 (미등록 카테고리는 기본값 — 미래 카테고리 안전 상속)."""
    return CATEGORY_POLICY.get((category or "").strip().lower(), _DEFAULT_POLICY)


# ★ 종목 재무 dataset 판별 — 경제 브리핑 배제용 단일 근거 (사용자 박제 2026-07-18).
#   fact 필터(trend_economic_writer)·이미지 필터(draft_processor) 공통 소스.
_STOCK_FIN_MARKERS = ("PER", "ROE", "영업이익률", "현재가", "시가총액", "EPS", "BPS", "PBR", "PSR")


def dataset_is_stock_financial(ds: dict) -> bool:
    """dataset 이 '개별 종목 재무' 차트인가 (경제 브리핑에서 배제 대상).

    판별: ① kind=='stock_financial' 태그(chart_data._stock_datasets 가 박제) 1순위
          ② 태그 없어도 provider(krx/dart/finance)+제목의 종목재무 마커 휴리스틱
             (collect_research fact 유래 승격 dataset 포착).
    테마는 이 판정과 무관하게 종목재무 허용(policy allow_stock_financial=True).
    """
    if not isinstance(ds, dict):
        return False
    if ds.get("kind") == "stock_financial":
        return True
    prov = ((ds.get("source") or {}).get("provider") or "").lower()
    title = ds.get("title") or ""
    return any(p in prov for p in ("krx", "dart", "finance")) and any(m in title for m in _STOCK_FIN_MARKERS)


def dataset_fingerprint(title: str, unit: str) -> str:
    """dataset dedupe fingerprint (title|unit sha1[:12]) — 3 생산자(_mk_dataset·
    stocks_to_datasets·facts_to_datasets) 공통 단일 소스."""
    seed = f"{(title or '').strip()}|{(unit or '').strip()}"
    return hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


_GROUND_ABS_FLOOR = 1e-9   # g 가 0 근처일 때 절대 바닥 tolerance


def _decimals_of(x: float) -> int:
    """부동소수 표시 소수 자릿수 추정 (display_precision 폴백)."""
    s = repr(float(x))
    if "e" in s or "E" in s:
        return 0
    if "." in s:
        return len(s.split(".", 1)[1].rstrip("0"))
    return 0


def grounds(n, g, display_precision: int | None = None) -> bool:
    """대본 수치 n 이 수집값 g 에 grounding 되는가 (★ 단위 일치는 호출측 게이트).

    통과 조건 (하나라도 참):
      ① |n − g| ≤ max(5%·|g|, 절대바닥)          — ±5% (사용자 박제 tolerance)
      ② n 이 g 의 표시자리 올림(ceil) 또는 버림(floor) — 읽기용 반올림 허용

    display_precision: 대본 원토큰의 소수 자릿수. None 이면 n 에서 추정
                       (★ _canon_num 이 정밀도를 버리므로 호출측이 원토큰 자릿수 전달 권장).
    """
    try:
        n = float(n)
        g = float(g)
    except (TypeError, ValueError):
        return False
    # ① ±5% (절대 바닥 포함)
    if abs(n - g) <= max(abs(g) * 0.05, _GROUND_ABS_FLOOR):
        return True
    # ② 표시자리 올림/버림 (같은 단위 기준 — 단위 일치는 호출측 책임)
    dp = display_precision if display_precision is not None else _decimals_of(n)
    q = 10.0 ** dp
    floor_v = math.floor(g * q) / q
    ceil_v = math.ceil(g * q) / q
    return abs(n - floor_v) <= 1e-9 or abs(n - ceil_v) <= 1e-9


@dataclass
class CollectedData:
    """★ 통합 수집 계약 (4-part) — 전 카테고리 J09 가 이 구조로 방출.

    대본 작성기·process_draft·prepublish_gate·law_enforcer 검증이 *모두* 이 상자를 소비.
      meta     : {keyword, profile, sector, category, as_of, + 사이드채널(coverage_ratio…)}
      datasets : 차트-준비 수치 [{title, viz_hint, unit, data:[{label,value}], source, fingerprint}]
      docs     : 텍스트 코퍼스 [CollectionResult]  (논문>API>뉴스>기사>웹)
      facts    : 원자적 검증 수치 [{claim/statement, value, unit, source, as_of}]
      entities : 다속성 도메인 객체 [{name, type, attrs, source}] (종목·매물·코인…)
    """
    meta: dict = field(default_factory=dict)
    datasets: list = field(default_factory=list)
    docs: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    entities: list = field(default_factory=list)

    def all_numbers(self) -> list[tuple[float, str]]:
        """검증 정답 풀 — (value, unit) 튜플 리스트.

        datasets(row value + dataset.unit) + facts(value+unit) +
        entities.attrs(value + ATTR_UNITS 단위) 를 평탄화.
        ★ fact-유래 dataset 과 원본 fact 가 같은 수를 이중표현하므로 (value,unit) dedupe.
        """
        seen: set = set()
        out: list[tuple[float, str]] = []

        def _add(v, u) -> None:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                return
            unit = (u or "").strip()
            key = (round(fv, 6), unit)
            if key in seen:
                return
            seen.add(key)
            out.append((fv, unit))

        for ds in self.datasets or []:
            unit = (ds.get("unit") or "").strip()
            row_vals: list[float] = []
            for row in ds.get("data") or []:
                _add(row.get("value"), unit)
                try:
                    row_vals.append(float(row.get("value")))
                except (TypeError, ValueError):
                    pass
            # KOSIS 세부 항목처럼 개별 row가 세부 분류일 때, 합계도 gt에 추가
            # (LLM이 합산한 총계 수치 grounding 가능하도록)
            if len(row_vals) >= 2:
                _add(sum(row_vals), unit)
        for f in self.facts or []:
            _add(f.get("value"), f.get("unit"))
        for e in self.entities or []:
            for k, av in (e.get("attrs") or {}).items():
                if isinstance(av, dict):
                    _add(av.get("value"), av.get("unit") or ATTR_UNITS.get(k, ""))
                else:
                    _add(av, ATTR_UNITS.get(k, ""))
        return out

    def to_dict(self) -> dict:
        """JSON 직렬화용 (topic_pack round-trip). docs 만 asdict, 나머지 dict 유지."""
        return {
            "meta": self.meta,
            "datasets": self.datasets,
            "docs": [asdict(x) if isinstance(x, CollectionResult) else x for x in self.docs],
            "facts": self.facts,
            "entities": self.entities,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CollectedData":
        """JSON 역직렬화 — docs 는 CollectionResult 객체로 rehydrate, 나머지 dict 유지."""
        d = d or {}
        docs = [x if isinstance(x, CollectionResult) else CollectionResult(**x)
                for x in (d.get("docs") or [])]
        return cls(
            meta=dict(d.get("meta") or {}),
            datasets=list(d.get("datasets") or []),
            docs=docs,
            facts=list(d.get("facts") or []),
            entities=list(d.get("entities") or []),
        )
