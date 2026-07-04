"""JARVIS09_COLLECTOR — 수집 단일 진입점.

★ 모든 에이전트의 데이터 수집은 이 모듈을 통해서만 허용.
  (JARVIS03 RADAR 자체 트렌드 수집 부분 예외)

허용 호출 패턴:
    from JARVIS09_COLLECTOR import (
        collect_for_theme,       # 주제 관련 텍스트 자료 (뉴스·블로그·학술 등)
        collect_stocks_data,     # 테마 종목 데이터 (시세·재무)
        collect_chart_data,      # ★ 주제 연관 차트용 실데이터 (출처 박제 — 2026-06-29)
        get_market_data,         # 글로벌 시장 지표 (yfinance)
        get_economic_calendar,   # 경제 일정 (investing.com)
        web_verify,              # 발행 전 사실성 게이트용 웹 재검증
    )

금지:
    - 다른 에이전트에서 yfinance / pykrx / requests / pytrends 직접 호출
    - JARVIS09 외부에서 수집 로직 신설
"""
from JARVIS09_COLLECTOR.models import (
    CollectedData,             # ★ 통합 수집 계약 (4-part) — 전 카테고리 단일 상자
    CATEGORY_POLICY,           # ★ 카테고리 정책 레지스트리 (min_images 등)
    policy_for,
    grounds,                   # ★ 통일 수치 grounding 예측자 (올림/버림 or ±5%)
    ATTR_UNITS,
)
from JARVIS09_COLLECTOR.collector_engine import (
    collect_for_theme,
    collect_for_theme_delta,   # ★ delta-aware 교류 (사용자 박제 2026-06-07)
    collect_research,          # ★ 설계-우선 리서치 수집 (ADR 012 — 2026-07-02)
    collect_all,               # ★ 통합 수집 컴포저 → CollectedData (Step 3 — 2026-07-05)
    compose_collected,         # ★ 조각 → CollectedData 조립 (재수집 없음)
)
from JARVIS09_COLLECTOR.evidence_pack import (
    evidence_brief,            # ★ 대본 프롬프트용 근거 브리프 (ADR 012)
    as_source_docs,            # ★ prepublish 사실성 게이트용 어댑터 (ADR 012)
)
from JARVIS09_COLLECTOR.research_planner import plan_research
from JARVIS09_COLLECTOR.source_onboarding import (
    check_and_notify as check_source_onboarding,
    register_key as register_source_key,
    onboarding_status,
)
from JARVIS09_COLLECTOR.collect_theme import collect_stocks_data, stocks_to_datasets
from JARVIS09_COLLECTOR.evidence_pack import facts_to_datasets   # 텍스트 수치 → 차트 승격 (ERRORS [302][315])
from JARVIS09_COLLECTOR.chart_data import (
    collect_chart_data,
    get_ecos_raw,        # ★ JARVIS06 차트용 ECOS 원시 수집 (provider 단일 진입점)
    get_krx_raw,         # ★ JARVIS06 차트용 KRX 원시 수집 (provider 단일 진입점)
)
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_market_data,
    get_economic_calendar,
    get_ticker_history,  # ★ JARVIS06 차트용 yfinance 단일 진입점
    download_ticker,     # ★ JARVIS06 차트용 yfinance 단일 진입점
)
from JARVIS09_COLLECTOR.providers.verify_provider import web_verify

__all__ = [
    "CollectedData",
    "CATEGORY_POLICY",
    "policy_for",
    "grounds",
    "ATTR_UNITS",
    "collect_for_theme",
    "collect_for_theme_delta",
    "collect_research",
    "collect_all",
    "compose_collected",
    "evidence_brief",
    "as_source_docs",
    "plan_research",
    "check_source_onboarding",
    "register_source_key",
    "onboarding_status",
    "collect_stocks_data",
    "stocks_to_datasets",
    "facts_to_datasets",
    "collect_chart_data",
    "get_ecos_raw",
    "get_krx_raw",
    "get_market_data",
    "get_economic_calendar",
    "get_ticker_history",
    "download_ticker",
    "web_verify",
]
