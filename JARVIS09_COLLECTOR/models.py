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


# ★ 출처 신뢰 우선순위 — source_registry(SSOT) 에서 파생 (사용자 박제 2026-07-24)
#   "API > 뉴스 > 기사 > 웹. 데이터가 겹치면 이 순서로 하나를 선택. 수집 자체는 전부에서."
#   이 티어는 *중복·충돌 해소 전용* — 수집 범위 제한에 사용 금지.
#   ★ 종전엔 여기 dict 를 손수 유지했으나 같은 정보(티어·provider·카탈로그)가 5~10곳에 흩어져
#     소스 하나 바꾸면 전체를 훑어야 했다(논문 제거 시 17파일). 이제 source_registry.SOURCES 파생.
from .source_registry import SOURCE_TRUST_TIER   # noqa: F401 — SSOT 파생 재노출(evidence_pack 등 소비)


# ★ '미지 출처' 의 신뢰 등급 — 레지스트리에서 *파생* 한다 (2026-08-10, ②동적 설계).
#   종전엔 `5` 를 trust_rank·evidence_pack 4곳이 각자 박고 있었다. source_registry 에
#   tier 6 짜리 소스가 하나 생기는 순간 '미지' 가 '블로그보다 신뢰됨' 이 된다 —
#   목록의 주인이 바뀌었는데 사본은 옛 값을 가리킨 채 남는 전형적인 꼴.
LOWEST_TRUST_TIER: int = max(SOURCE_TRUST_TIER.values())


def trust_rank(source_type: str) -> int:
    """출처 신뢰 순위 (낮을수록 신뢰 높음). 미지 소스는 최하위(LOWEST_TRUST_TIER)."""
    return SOURCE_TRUST_TIER.get((source_type or "").strip().lower(), LOWEST_TRUST_TIER)


def source_tier(src) -> int:
    """dataset/fact 의 `source` dict → 신뢰 티어 정수. 값이 없거나 망가졌으면 최하위.

    ★ 단일 소스 (2026-08-10): 종전엔 `int(src.get("tier", 5) or 5)` 라는 *같은 강제변환*
      이 evidence_pack 안에만 3벌 있었다. 코드가 같은 세 줄은 이름이 없어도 사본이다.
    """
    try:
        return int((src or {}).get("tier", LOWEST_TRUST_TIER) or LOWEST_TRUST_TIER)
    except (TypeError, ValueError, AttributeError):
        return LOWEST_TRUST_TIER


# ★ 수집 쿼터 (사용자 박제 2026-07-06, v2 정정): "인포그래픽을 만들 수 있을 만큼"의 자료를
#   신뢰 서열대로 총 15개 확보 — API 최대 10, 나머지 5(소스별 1개씩 라운드로빈).
#   상위 티어가 슬롯을 못 채우면 미달분을 다음 티어로 이월(cascade). 예: API 8개면
#   나머지 7개, API 0개면 나머지에서 15개 전부.
COLLECT_QUOTA_BUDGET = 15   # 총 수집 상한
COLLECT_API_CAP      = 10   # 공식 데이터 API(kosis·ecos·dart·krx·finance) 기본 상한

def quota_group(source_type: str) -> str:
    """수집 쿼터 그룹: api(공식 데이터 — 신뢰 tier 1) | rest(그 외: 뉴스·기사·웹·블로그).

    ★ 2026-07-24 (② 동적설계): SOURCE_TRUST_TIER(trust_rank) *단일 소스* 에서 파생.
      종전엔 별도 _QUOTA_GROUP 사본을 두고 "반드시 tier 분류와 동치 유지" 라고 주석까지
      달았는데, 그 사본이 tier 와 어긋나 '뉴스 0건 사고'(2026-07-17)를 냈다 — 사본을
      진실로 믿던 전형. 이제 API 그룹 = 공식 데이터 API(tier 1) 로 파생해 드리프트 원천 차단.
    """
    return "api" if trust_rank(source_type) == 1 else "rest"


@dataclass
class RawDocument:
    """수집 직후 원본 문서."""
    url: str
    source_type: str          # blog | news | finance | web
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
# ★ 수집 노브 (사용자 박제 2026-07-23 — 수집 오케스트레이션 09 이관): 카테고리별로 무엇을
#   수집하느냐의 차이를 `if category == "theme"` 분기로 박지 않고 여기서 파생한다.
#   collect_stocks = 개별 종목 시세·재무 수집 여부 / collect_charts = 주제 연관 차트 실데이터
#   수집 여부 / market_fallback = 차트 0개일 때 시장지표(yfinance)로 datasets 를 채울지.
#   profile_provider — 프로필 없이 키워드만 들어왔을 때 프로필을 받아올 자비스03 진입점
#     ("모듈경로:함수명", `fn(keyword, sector=...)` → {"profile","sector"}). ADR 013 의
#     '키워드 단독 전송 금지' 를 *수집 경계에서* 강제한다 — 02 에 프로필 재조회 코드를
#     두지 않기 위한 노브 (사용자 박제 2026-07-23).
CATEGORY_POLICY: dict[str, dict] = {
    "theme":    {"min_images": 5, "thumbnail_body_chars": 3000,
                 "allow_stock_financial": True,
                 "collect_stocks": True,  "collect_charts": False,
                 "market_fallback": False,
                 "profile_provider": "JARVIS03_RADAR.theme_picker:theme_topic"},
    "economic": {"min_images": 5, "thumbnail_body_chars": 3000,
                 "allow_stock_financial": False,
                 "collect_stocks": False, "collect_charts": True,
                 "market_fallback": True,
                 # 경제는 자비스03 topic_pack 이 후보와 함께 프로필을 항상 동봉한다.
                 "profile_provider": ""},
}
# ★ 차트 승격 문턱 (2026-08-10) — 카테고리별로 다르지 않으므로 *기본값에만* 둔다.
#   chart_max_source_tier      : 이 티어보다 낮은 신뢰의 출처는 차트가 되지 못한다.
#   chart_verbatim_above_tier  : 이 티어를 넘는 출처는 원문 대조를 통과해야 차트가 된다.
#   ★ 값을 카테고리 dict 에 복사해 두지 않는다 — 종전엔 theme·economic·기본값 세 곳에
#     같은 숫자가 박혀 있었고, 소비처(evidence_pack)까지 `pol.get(..., 2)` 로 네 번째·
#     다섯 번째 사본을 들고 있었다. 문턱을 바꾸면 다섯 곳이 어긋난다.
_DEFAULT_POLICY = {"min_images": 5, "thumbnail_body_chars": 3000,
                   "allow_stock_financial": True,
                   "collect_stocks": True,  "collect_charts": False,
                   "market_fallback": False,
                   "chart_max_source_tier": 2, "chart_verbatim_above_tier": 1,
                   "profile_provider": ""}


def policy_for(category: str) -> dict:
    """카테고리 정책 조회 (미등록 카테고리는 기본값 — 미래 카테고리 안전 상속).

    ★ 기본값 위에 카테고리 차이를 덮는다 (2026-08-10): 종전엔 카테고리 dict 를 *통째로*
      돌려줘, 카테고리가 명시하지 않은 노브는 아예 없는 키가 됐다. 그래서 소비처마다
      `pol.get("...", <리터럴>)` 폴백이 자라났고 그 리터럴이 곧 정책의 사본이었다.
      이제 모든 노브가 항상 존재하므로 소비처는 `pol["..."]` 로 곧장 읽으면 된다.
    """
    return {**_DEFAULT_POLICY, **CATEGORY_POLICY.get((category or "").strip().lower(), {})}


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


# ★ dataset `data` 행의 *선택* 메타 키 — 단일 소스 (사용자 박제 2026-08-10).
#   왜 필요한가: 조립 단계에서 행별 진실(어느 출처의·언제 기준의·실적인가 전망인가)을
#   버리고 대표 1건을 dataset 전체에 박제해, KOFIA·yfinance 수치가 '한국은행' 출처로,
#   2023년 값이 '2026.08 기준' 배지로 발행됐다 (2026-08-10 경제 브리핑 slot3·slot4).
#   행이 자기 출처·시점을 들고 다니면 하류가 '단일 시점 주장 불가'를 *판정* 할 수 있다.
#   ★ 전부 선택 키다 — {label, value} 만 읽는 기존 소비자는 영향 없음(추가 전용).
#     행 dict 를 *재구성* 하는 곳은 이 상수로 메타를 함께 실어 나를 것.
ROW_META: tuple[str, ...] = ("as_of", "source", "category", "basis", "fact_id", "verbatim")

def max_attempts() -> int:
    """재시도 상한 — `JARVIS00_INFRA.harness.DEFAULT_MAX_ATTEMPTS` 파생 (SSOT).

    ★ 사용자 박제 2026-07-21: "어떤 재시도도 최대 2회". 상한을 코드에 박지 않는다.
      09 안에 이 파생이 여러 벌 생기지 않도록 *패키지 공용 leaf* 인 여기가 소유한다
      (종전엔 collect_theme 에만 있고, data_planner·collect_theme 다른 루프는 2·3 을
      각자 박고 있었다 — 상한을 바꿔도 그 루프들만 어긋난다).
    ★ 폴백 리터럴을 두지 않는다 (2026-08-10 정정 — 같은 커밋의 J06 `limits.py` 와 판단 일치):
      `except: return 2` 를 두면 그것이 상한의 *또 하나의 사본* 이 된다. harness 를 못 읽는
      날에도 09 만 조용히 2 로 돌기 때문에 드리프트가 눈에 띄지 않는다. harness 는 외부
      설정이 아니라 저장소 내부 모듈이므로, import 실패는 '설정 차이' 가 아니라
      *설치가 깨진 상태* 다 — 조용히 넘기지 말고 그대로 터뜨린다.
    ★ 매 호출 조회 (모듈 로드 시점 캡처 아님): `HARNESS_MAX_ATTEMPTS` 무배포 조정이
      이미 뜬 프로세스에도 먹어야 한다 — 복사본을 진실로 믿지 않는다.
    ★ lazy import — models 는 stdlib-only leaf 계약을 유지해야 한다(순환 방지).
    """
    from JARVIS00_INFRA import harness as _h
    return max(1, int(_h.DEFAULT_MAX_ATTEMPTS))


# ★ 차트 축 라벨 최대 길이 — 단일 소스 (2026-08-10).
#   종전에 chart_data._clean_label 의 `lab[:22]` 와 evidence_pack._QUALIFIED_LABEL_MAX = 22
#   두 벌이 각자 22 를 들고 있었다. 두 생산자가 같은 축에 라벨을 얹으므로 값이 어긋나면
#   한쪽 차트만 잘린다 — 사본을 만들지 말고 여기서 파생할 것.
AXIS_LABEL_MAX: int = 22

# ★ 수치의 성격(basis) 어휘 → 제목에 붙일 한국어 — 단일 소스 (2026-08-10).
#   ① 추출 단계 유효성 검사 ② 그룹 키 ③ 제목 작명 세 곳이 같은 어휘를 쓴다. 목록을 세 번
#   적으면 값이 하나 늘 때 두 곳만 고쳐지고 나머지가 조용히 어긋난다(실적/전망 혼합 재발).
#   ""(미상) 은 유효 어휘가 아니라 *부재* 다 → BASIS_KINDS 에서 제외해 파생한다.
BASIS_TITLE: dict[str, str] = {"actual": "", "forecast": "전망", "threshold": "기준선"}
BASIS_KINDS: tuple[str, ...] = tuple(BASIS_TITLE)


def dataset_fingerprint(title: str, unit: str) -> str:
    """dataset dedupe fingerprint (title|unit sha1[:12]) — 3 생산자(_mk_dataset·
    stocks_to_datasets·facts_to_datasets) 공통 단일 소스."""
    seed = f"{(title or '').strip()}|{(unit or '').strip()}"
    return hashlib.sha1(seed.encode("utf-8", errors="replace")).hexdigest()[:12]


_GROUND_ABS_FLOOR = 1e-9   # g 가 0 근처일 때 절대 바닥 tolerance


def is_finite_num(v) -> bool:
    """유한 실수인가 (NaN·Inf·비수치 = False). ★ 결측 판정 단일 소스.

    yfinance·BOK 등 외부 소스는 휴장·미집계 구간을 **NaN** 으로 돌려준다. NaN 은
    '값이 없다'는 뜻이지 0 이 아니다 — 0 으로 채우면 거짓 데이터가 된다(제4조·ADR 010).
    grounds()·sanitize_datasets()·시장데이터 생산자가 모두 이 함수로 판정한다.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def sanitize_datasets(datasets: list | None) -> list:
    """dataset 의 결측(NaN/Inf) 포인트 제거 + 전량 결측 dataset 배제.

    ★ 왜 조립 지점에서 한 번에 (사용자 박제 2026-07-25 — ERRORS 사실성 오차단):
      NaN 이 상자에 남으면 *두 갈래로* 터진다 —
        ① 검증: gt 에 섞여 grounds() 가 math.floor(NaN) 로 ValueError →
           게이트가 except 로 삼켜 **진짜 사실을 '출처 미확인'으로 차단** (2026-07-25 경제 티스토리)
        ② 이미지: pro_templates int(NaN) 크래시 (같은 날 GUARDIAN 보고)
      결측은 채우지 않고 *버린다*. 포인트가 0 개가 되면 dataset 자체를 뺀다(빈 차트 방지).
    """
    out: list = []
    for ds in datasets or []:
        if not isinstance(ds, dict):
            out.append(ds)
            continue
        pts = ds.get("data")
        if not isinstance(pts, list):
            out.append(ds)
            continue
        keep = [p for p in pts
                if not isinstance(p, dict) or is_finite_num(p.get("value"))]
        if not keep:
            continue                      # 전량 결측 → dataset 배제
        out.append(ds if len(keep) == len(pts) else {**ds, "data": keep})
    return out


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
    # ★ 결측(NaN/Inf)은 근거가 될 수 없다 — *크래시 대신 미근거* (사용자 박제 2026-07-25).
    #   종전엔 NaN 이 ②의 math.floor(NaN) 에서 ValueError 로 터졌고, 호출측 게이트가
    #   그 예외를 except 로 삼켜 **진짜 사실을 '출처 미확인' 으로 차단**했다 (경제 티스토리 발행 실패).
    if not (is_finite_num(n) and is_finite_num(g)):
        return False
    n = float(n)
    g = float(g)
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
      docs     : 텍스트 코퍼스 [CollectionResult]  (API>뉴스>기사>웹)
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
            for row in ds.get("data") or []:
                _add(row.get("value"), unit)
            # ★ '행들을 더한 값' 은 *출처가 공표한 합계* 일 때만 정답으로 인정
            #   (사용자 박제 2026-08-10 — D06 의 텍스트 쌍둥이).
            #   종전엔 행이 2개 이상이면 무조건 sum(row_vals) 를 정답 풀에 넣었다.
            #   그러면 금리 8종의 합(27.2%)·환율 3시점의 합(4,368원)처럼 *현실에 없는 수* 가
            #   대본 사실성 게이트의 '근거 있음' 목록에 들어가, 본문이 그 수를 써도 통과한다.
            #   가산 가능 여부는 데이터가 증명해야 한다 → 생산자가 ds["totals"] 에 실어 보낸
            #   *공표 합계* 만 인정 (chart_data._mk_dataset 가 '전체/계/합계' 행에서 파생).
            _t = (ds.get("totals") or {}).get("value")
            if _t is not None:
                _add(_t, unit)
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
