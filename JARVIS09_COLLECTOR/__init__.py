"""JARVIS09_COLLECTOR — 수집 단일 진입점.

★ 모든 에이전트의 데이터 수집은 이 모듈을 통해서만 허용.
  (JARVIS03 RADAR 자체 트렌드 수집 부분 예외)

허용 호출 패턴:
    from JARVIS09_COLLECTOR import (
        collect_for_theme,       # 주제 관련 텍스트 자료 (뉴스·블로그·학술 등)
        collect_stocks_data,     # 테마 종목 데이터 (시세·재무)
        get_market_data,         # 글로벌 시장 지표 (yfinance)
        get_economic_calendar,   # 경제 일정 (investing.com)
    )

금지:
    - 다른 에이전트에서 yfinance / pykrx / requests / pytrends 직접 호출
    - JARVIS09 외부에서 수집 로직 신설
"""
from JARVIS09_COLLECTOR.collector_engine import collect_for_theme
from JARVIS09_COLLECTOR.collect_theme import collect_stocks_data
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_market_data,
    get_economic_calendar,
)

__all__ = [
    "collect_for_theme",
    "collect_stocks_data",
    "get_market_data",
    "get_economic_calendar",
]
