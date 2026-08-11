"""JARVIS09_COLLECTOR — 수집 단일 진입점.

★ 모든 에이전트의 데이터 수집은 이 모듈을 통해서만 허용.
  (JARVIS03 RADAR 자체 트렌드 수집 부분 예외)

허용 호출 패턴:
    from JARVIS09_COLLECTOR import (
        collect_for_theme,       # 주제 관련 텍스트 자료 (뉴스·블로그·학술 등)
        collect_stocks_data,     # 테마 종목 데이터 (시세·재무)
        collect_chart_data,      # ★ 주제 연관 차트용 실데이터 (출처 박제 — 2026-06-29)
        chart_datasets,          # ★ 차트 datasets 파사드 (재수집 시점 판단 포함 — 2026-07-23)
        naver_theme_catalog,     # ★ 네이버 공식 테마 카탈로그 (공개 정문 — 2026-07-23)
        get_market_data,         # 글로벌 시장 지표 (yfinance)
        get_economic_calendar,   # 경제 일정 (investing.com)
        web_verify,              # 발행 전 사실성 게이트용 웹 재검증
    )

금지:
    - 다른 에이전트에서 yfinance / pykrx / requests / pytrends 직접 호출
    - JARVIS09 외부에서 수집 로직 신설
"""

# ══════════════════════════════════════════════════════════════════════════════
# ★ 지연 파사드 (PEP 562) — 사용자 박제 2026-08-10
#
# 종전엔 여기서 파사드 36개를 **import 시점에 전부** 끌어왔다. 그래서
# `JARVIS09_COLLECTOR.models.grounds` 같은 *데이터 모델 한 줄* 을 쓰려 해도
# 패키지 __init__ 이 수집 스택 전체(collector_engine·collect_theme·providers…)를
# 로드했고, `feedparser`·`pandas` 같은 **수집 전용 의존성이 없으면 통째로 실패**했다.
#
# 실제 사고: 정적 정책 검사(precommit `image/self-check`)가 CI 에서
#   No module named 'feedparser' → verifier_effective()=False
#   → "검증기가 조작 수치를 통과시킨다" 로 **오판**되어 머지가 막혔다.
# 로컬은 .venv 에 전부 깔려 있어 초록이었다 — 전형적인 '로컬만 초록'(커밋 47b2574).
# fail-closed 판정 자체는 옳았다. 문제는 *검사기가 수집 의존성을 필요로 한 것* 이다.
#
# 이제 이름을 **실제로 쓸 때** 그 모듈만 로드한다. 공개 API 는 그대로 —
# `from JARVIS09_COLLECTOR import collect_all` 도 동일하게 동작한다(PEP 562).
#
# ★ `__all__` 은 정적 리터럴로 남긴다 — precommit `collect` 카테고리가 이 파일을
#   *소스에서 정규식으로* 읽고, 못 읽으면 검사를 무력화로 보고 위반을 낸다(fail-closed).
#   그래서 아래 `_LAZY` 와 두 벌이 되는데, **드리프트는 기계가 막는다**(파일 끝 가드).
# ══════════════════════════════════════════════════════════════════════════════
_LAZY: dict[str, tuple[str, str]] = {    'CollectedData': ('JARVIS09_COLLECTOR.models', 'CollectedData'),    'CATEGORY_POLICY': ('JARVIS09_COLLECTOR.models', 'CATEGORY_POLICY'),    'policy_for': ('JARVIS09_COLLECTOR.models', 'policy_for'),    'grounds': ('JARVIS09_COLLECTOR.models', 'grounds'),    'ATTR_UNITS': ('JARVIS09_COLLECTOR.models', 'ATTR_UNITS'),    'collect_for_theme': ('JARVIS09_COLLECTOR.collector_engine', 'collect_for_theme'),    'collect_for_theme_delta': ('JARVIS09_COLLECTOR.collector_engine', 'collect_for_theme_delta'),    'collect_research': ('JARVIS09_COLLECTOR.collector_engine', 'collect_research'),    'collect_all': ('JARVIS09_COLLECTOR.collector_engine', 'collect_all'),    'compose_collected': ('JARVIS09_COLLECTOR.collector_engine', 'compose_collected'),    'market_data_to_datasets': ('JARVIS09_COLLECTOR.collector_engine', 'market_data_to_datasets'),    'market_snapshot': ('JARVIS09_COLLECTOR.collector_engine', 'market_snapshot'),    'select_by_trust_quota': ('JARVIS09_COLLECTOR.collector_engine', 'select_by_trust_quota'),    'evidence_brief': ('JARVIS09_COLLECTOR.evidence_pack', 'evidence_brief'),    'as_source_docs': ('JARVIS09_COLLECTOR.evidence_pack', 'as_source_docs'),    'check_source_onboarding': ('JARVIS09_COLLECTOR.source_onboarding', 'check_and_notify'),    'register_source_key': ('JARVIS09_COLLECTOR.source_onboarding', 'register_key'),    'onboarding_status': ('JARVIS09_COLLECTOR.source_onboarding', 'onboarding_status'),    'collect_stocks_data': ('JARVIS09_COLLECTOR.collect_theme', 'collect_stocks_data'),    'stocks_to_datasets': ('JARVIS09_COLLECTOR.collect_theme', 'stocks_to_datasets'),    'naver_theme_catalog': ('JARVIS09_COLLECTOR.collect_theme', 'naver_theme_catalog'),    'facts_to_datasets': ('JARVIS09_COLLECTOR.evidence_pack', 'facts_to_datasets'),    'collect_chart_data': ('JARVIS09_COLLECTOR.chart_data', 'collect_chart_data'),    'chart_datasets': ('JARVIS09_COLLECTOR.chart_data', 'chart_datasets'),    'get_ecos_raw': ('JARVIS09_COLLECTOR.chart_data', 'get_ecos_raw'),    'get_krx_raw': ('JARVIS09_COLLECTOR.chart_data', 'get_krx_raw'),    'get_market_data': ('JARVIS09_COLLECTOR.providers.economic_data_provider', 'get_market_data'),    'get_economic_calendar': ('JARVIS09_COLLECTOR.providers.economic_data_provider', 'get_economic_calendar'),    'get_ticker_history': ('JARVIS09_COLLECTOR.providers.economic_data_provider', 'get_ticker_history'),    'download_ticker': ('JARVIS09_COLLECTOR.providers.economic_data_provider', 'download_ticker'),    'web_verify': ('JARVIS09_COLLECTOR.providers.verify_provider', 'web_verify'),    'seo_reference_docs': ('JARVIS09_COLLECTOR.providers.economic_data_provider', 'fetch_seo_docs'),    'published_post_kor_counts': ('JARVIS09_COLLECTOR.providers.published_provider', 'published_post_kor_counts'),    'load_pinned_theme': ('JARVIS09_COLLECTOR.precollect_cache', 'load_pinned_theme'),    'precollect_theme': ('JARVIS09_COLLECTOR.precollect', 'precollect_theme'),    'precollect_economic': ('JARVIS09_COLLECTOR.precollect', 'precollect_economic'),}

def __getattr__(name: str):
    """공개 이름을 *처음 쓸 때* 그 모듈만 로드한다 (PEP 562)."""
    try:
        mod_path, orig = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib
    val = getattr(importlib.import_module(mod_path), orig)
    globals()[name] = val          # 두 번째부터는 __getattr__ 을 타지 않는다
    return val


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY))

__all__ = [
    'CollectedData',
    'CATEGORY_POLICY',
    'policy_for',
    'grounds',
    'ATTR_UNITS',
    'collect_for_theme',
    'collect_for_theme_delta',
    'collect_research',
    'collect_all',
    'compose_collected',
    'market_data_to_datasets',
    'market_snapshot',
    'select_by_trust_quota',
    'evidence_brief',
    'as_source_docs',
    'check_source_onboarding',
    'register_source_key',
    'onboarding_status',
    'collect_stocks_data',
    'stocks_to_datasets',
    'naver_theme_catalog',
    'facts_to_datasets',
    'collect_chart_data',
    'chart_datasets',
    'get_ecos_raw',
    'get_krx_raw',
    'get_market_data',
    'get_economic_calendar',
    'get_ticker_history',
    'download_ticker',
    'web_verify',
    'seo_reference_docs',
    'published_post_kor_counts',
    'load_pinned_theme',
    'precollect_theme',
    'precollect_economic',
]

# ★ 드리프트 차단 — `__all__`(검사기 계약)과 `_LAZY`(실제 해석표)가 갈리면 즉시 터진다.
#   import 만 하고 모듈은 안 건드리므로 비용 0. 조용히 어긋나는 것이 가장 위험하다.
if set(__all__) != set(_LAZY):
    _only_all = sorted(set(__all__) - set(_LAZY))
    _only_map = sorted(set(_LAZY) - set(__all__))
    raise ImportError(
        "JARVIS09_COLLECTOR 파사드 드리프트 — "
        f"__all__ 에만: {_only_all} / _LAZY 에만: {_only_map}")
