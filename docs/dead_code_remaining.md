# 죽은 코드 감사 — 남은 DELETE 인벤토리 (2026-07-06)

전체 감사 DELETE 152개 중 58개 삭제 완료, 94개 남음.

각 항목 grep 0참조 + 동적경로 검증된 DELETE 판정. 후속 작업 시 파일별 도달성 재확인 후 삭제.


## JARVIS00_INFRA/architect.py
- `_extract_exec_plan` (stale_shim) — 호출처 0. 전 리포 grep(--include='*.py')이 정의행 997과 __all__ 1186 2건만 반환. 현 파이프라인은 design_new_agent 7단계에서 별도

## JARVIS00_INFRA/verification.py
- `registered_task_types` (unreferenced_func) — 전 리포 grep(모든 파일 타입) 결과 정의행(verification.py:114)과 __all__ 등재행(155)뿐, 실호출자 0. 동적 경로 전무: verification 모

## JARVIS02_WRITER/draft_writer.py
- `generate_draft` (stale_shim) — generate_draft(draft_writer.py:1320)는 word-boundary grep 상 draft_writer.py 내부 정의 1곳 + docstring 예시 4

## JARVIS02_WRITER/law_enforcer.py
- `wrap_prompt_with_law` (unreferenced_func) — 전 리포 grep 결과 wrap_prompt_with_law 등장은 정의부(1109)·자기 docstring Usage 예시(1117)·__all__ 수출(1764) 3곳뿐, 실호

## JARVIS03_RADAR/collectors/google_collector.py
- `get_interest_over_time_df` (unreferenced_func) — 전 리포 참조가 정의행(391)과 자기 본체 내부 문자열 리터럴(411, _mark_pytrends_blocked(reason="get_interest_over_time_df"))
- `get_related_queries` (unreferenced_func) — google_collector.py:421 정의가 유일한 참조. 전 리포 py grep, 비-py grep(json/md/yml 등) 모두 정의행 외 0건. 파일에 __all__/
- `get_interest_by_region` (unreferenced_func) — JARVIS03_RADAR/collectors/google_collector.py:444 의 get_interest_by_region 는 전 리포에서 정의행 하나뿐이고 호출처 0.
- `_pytrends` (dead_only_caller) — _pytrends() 팩토리는 파일 내부 3곳(398·427·450)에서만 호출되고, 그 caller 3개(get_interest_over_time_df·get_related_qu

## JARVIS03_RADAR/collectors/naver_collector.py
- `get_shopping_trend` (unreferenced_func) — 전 리포 grep 결과 정의행(JARVIS03_RADAR/collectors/naver_collector.py:66) 단 하나, 호출처 0. collectors/__init__.p

## JARVIS04_SCHEDULER/job_catalog.py
- `get_job` (unreferenced_func) — 전 리포 grep(get_job 전수)에서 모듈 레벨 job_catalog.get_job 을 import/호출하는 곳 0. 모든 '.get_job(' 히트는 APScheduler 

## JARVIS06_IMAGE/economic_charts.py
- `generate_thumbnail` (unreferenced_func) — economic_charts.generate_thumbnail(market,...)를 import하는 유일한 곳은 economic_poster.py:669이고, 그 import는 

## JARVIS06_IMAGE/html_infographic.py
- `_exec_and_extract_html` (unreferenced_func) — 전 리포 grep 결과 _exec_and_extract_html 은 정의(html_infographic.py:194) 외 참조 0. 공개 함수 generate_html_infogr

## JARVIS06_IMAGE/providers/claude_svg_provider.py
- `_find_kor_font` (unreferenced_func) — 정의부(claude_svg_provider.py:25) 외 참조 0. 실제 코드는 상수 _KOR_FONT_NAME(fontconfig 등록명, line 32/51)을 사용하며 이 

## JARVIS07_GUARDIAN/auto_repair.py
- `_run_auto_repair_legacy` (legacy_file) — JARVIS07_GUARDIAN/auto_repair.py:680 정의된 private 모듈 함수. 전 리포 grep(.py+비.py) 결과 실제 호출처 0 — 매칭은 (1) 정의

## JARVIS07_GUARDIAN/bandit.py
- `learning_summary` (unreferenced_func) — bandit.py:610 정의 외 리포 전체(.py/.md/.json/git) 참조 0. bandit 모듈은 __all__ 없고 와일드카드 import도 0건이라 수출 불가. 실제
- `negative_reward_for_skipped` (unreferenced_func) — 전 리포 grep 결과 유일 참조는 정의부(bandit.py:548) 단 하나, 호출처 0. bandit.py 에 __all__ 없음. 외부 import 는 전부 명시적 named
- `_arm_win_rate` (unreferenced_func) — JARVIS07_GUARDIAN/bandit.py:568 의 _arm_win_rate 는 전 리포에서 정의부 단 한 줄만 존재하고 호출처 0. 언더스코어 프라이빗 함수이며 모듈에 

## JARVIS07_GUARDIAN/error_collector.py
- `get_log_scanner` (stale_shim) — 전 리포 grep 결과 get_log_scanner 참조가 정의 라인(JARVIS07_GUARDIAN/error_collector.py:257) 하나뿐. 호출처 0. error_c

## JARVIS07_GUARDIAN/eval_agent.py
- `should_register` (unreferenced_func) — 모듈 함수 should_register()(eval_agent.py:112)는 evaluate(...).should_register 로 위임하는 bool 래퍼. 전 리포 grep 

## JARVIS07_GUARDIAN/guardian_agent.py
- `job_archive_errors` (unreferenced_func) — 전 리포 grep 결과 job_archive_errors 는 guardian_agent.py:10(docstring)·:810(정의) 두 곳뿐, 호출자 0. DEFAULT_JOBS
- `list_errors` (unreferenced_func) — guardian_agent.py:863 의 list_errors 는 `return _db.list_errors(...)` 단순 패스스루 래퍼. 전 리포에서 이 래퍼를 import/
- `get_stats` (unreferenced_func) — get_stats(guardian_agent.py:868)는 shared.db.get_error_stats를 감싸는 3줄 래퍼. repo 전체 grep에서 정의 1건 외 호출처 0

## JARVIS07_GUARDIAN/qa_resolver.py
- `resolver_stats` (unreferenced_func) — 전 리포 grep 결과 resolver_stats 출현은 정의부(qa_resolver.py:258)와 __all__ 수출(:277) 2곳뿐, 호출처 0. qa_resolver 모듈

## JARVIS07_GUARDIAN/qa_store.py
- `vector_search` (stale_shim) — vector_search(qa_store.py:662)는 vector_store.search_vector로 위임하는 얇은 래퍼. 전 리포 grep 결과 참조는 자기 정의·docst

## JARVIS08_PUBLISH/credentials/naver_cookie_refresher.py
- `_type_string_clipboard` (unreferenced_func) — 전 리포 grep(모든 파일 타입, __pycache__/.git 제외) 결과 `_type_string_clipboard` 는 138행 정의 1건뿐, 호출처 0. 같은 파일의 re

## JARVIS08_PUBLISH/credentials/tistory_cookie_refresher.py
- `job_pre_publish_check` (unreferenced_func) — tistory_cookie_refresher.py:657 의 job_pre_publish_check 는 전 리포에서 자기 def(657)+__all__(696) 외 호출처 0. 모

## JARVIS08_PUBLISH/platforms/naver_poster.py
- `_js_login` (unreferenced_func) — 전 리포 grep(모든 확장자) 결과 유일 히트는 정의행 naver_poster.py:594 하나, 호출처 0. 언더스코어 접두라 import */__all__ 수출 불가이며 __
- `_split_into_paragraphs` (unreferenced_func) — naver_poster.py:83의 _split_into_paragraphs는 전 리포 grep 결과 정의만 있고 호출 0. __all__ 없음, 외부 import는 post_to
- `_switch_to_english` (unreferenced_func) — 전 리포 grep(.py 및 전체 파일)에서 `_switch_to_english` 는 naver_poster.py:265 정의행 1건뿐, 호출처 0. 언더스코어 접두 module-
- `input_divider` (unreferenced_func) — post_to_naver 내부 중첩 함수로 모듈 스코프 밖에 없어 __all__/register/import/shim 등 동적 경로 노출 불가. 본문은 docstring+pass 

## JARVIS08_PUBLISH/platforms/tistory_poster.py
- `_capslock_reset` (unreferenced_func) — grep '_capslock_reset' 전 리포에서 정의행(135) 외 호출처 0. 137행의 press('capslock')은 함수 본체이지 호출 아님. 리딩 언더스코어 pri
- `_ss` (unreferenced_func) — 전 리포 grep 결과 '_ss(' 는 정의행(tistory_poster.py:133)만 존재, 호출 0. precommit_check.py 의 check_ssot/CATEGORI

## JARVIS09_COLLECTOR/chart_data.py
- `_web_datasets` (unreferenced_func) — JARVIS09_COLLECTOR/chart_data.py:205 의 _web_datasets 는 전 리포 grep(py+json+md+전 파일타입)에서 정의부 단 1건, 호출처 
- `_market_datasets` (unreferenced_func) — JARVIS09_COLLECTOR/chart_data.py:138의 _market_datasets() 는 호출처 0건. 전 리포 grep 결과 참조는 정의(138)와 폐기를 명시한
- `_ecos_datasets` (unreferenced_func) — 전 리포 grep 결과 정의(chart_data.py:157) 한 줄뿐, 호출처·주석 언급 0건. 유일 잠재 호출자 collect_chart_data가 라인 837~838 주석으로

## JARVIS09_COLLECTOR/collect_theme.py
- `calc_fin` (unreferenced_func) — calc_fin 호출부 0건. 정의행·docstring·_g_report func_name 문자열 라벨 4건 외 참조 없음. import(is_official_theme/stock
- `_enrich_leader_desc` (unreferenced_func) — _enrich_leader_desc는 collect_stocks_data(621행) 안에 중첩 정의된 지역 함수(935행)이며 호출부가 0회다. 전 리포 grep 결과 유일한 매치

## JARVIS09_COLLECTOR/evidence_pack.py
- `coverage_gaps` (unreferenced_func) — coverage_gaps는 evidence_pack.py:260 정의 + 그 모듈 자체 __all__(476) 두 곳에만 존재하고 실호출 0건. 패키지 __init__.py는 ev
- `merge_pack` (unreferenced_func) — merge_pack 은 2라운드 갭 재수집 루프 전용 함수였으나 collector_engine.py:307 에서 'ADR 012 커버리지 재수집 루프 폐지'가 명시됨. 전 리포 g
- `restrict_pack_to_docs` (unreferenced_func) — 전 리포 참조 0건(정의부만). evidence_pack.py __all__ 미등재 + __init__.py import 안 됨 → export 안 됨. 동적 경로(getattr/
- `persist_evidence` (unreferenced_func) — persist_evidence는 evidence_pack.py 정의부(374)와 자기 모듈 __all__(477)에만 존재, 실호출 0건. 패키지 __init__.py는 evide

## JARVIS09_COLLECTOR/run_context.py
- `active_ctx` (unreferenced_func) — active_ctx()는 run_context.py 내부에서만 등장하고 리포 전역 호출 0건. 모듈은 살아있지만 import는 전부 `from ... import new_run`으

## shared/bus.py
- `on_performance_updated` (unreferenced_func) — 전 리포 grep 결과 정의부(shared/bus.py:333) 외 실제 호출 0건. radar_agent 의 `_on_performance_updated` 는 언더스코어 접두가 
- `on_post_failed` (unreferenced_func) — shared/bus.py의 on_post_failed(266행)는 리포 전역에서 정의+본문 2행만 등장하고 호출자 0. 버스는 emitter 헬퍼를 명시 호출로만 발화하는데(구독은
- `on_performance_updated` (unreferenced_func) — shared/bus.py:333 on_performance_updated 는 EventType.PERFORMANCE_UPDATED 의 유일 emitter이나 리포 전역 이름 gre

## shared/capabilities.py
- `requires_approval` (unreferenced_func) — shared/capabilities.py:119 의 모듈 함수 requires_approval(intent)은 전 리포 어디서도 호출되지 않음. 함수 호출식 grep [^.a-zA

## shared/db.py
- `update_keyword_views` (dead_only_caller) — update_keyword_views(shared/db.py:600)의 유일한 참조는 shared/bus.py:338, on_performance_updated 퍼블리셔 래퍼 내부
- `get_action_board` (unreferenced_func) — shared/db.py:1173의 get_action_board는 전 리포 grep에서 정의 1곳 외 참조 0. 신고된 유일 소비자 JARVIS03_RADAR/app.py는 fin
- `get_ops_metrics` (unreferenced_func) — 전 리포 grep 결과 유일 참조는 정의부(shared/db.py:1364)뿐. 호출부 0. db.py에 __all__ 없어 와일드카드 재수출 불가, 'from shared.db 
- `get_revision_effect` (unreferenced_func) — shared/db.py:1321 의 get_revision_effect 는 전 리포(.py/.md/.json/.txt/.html) 통틀어 정의 라인 1건 외 참조 0건. 순수 읽기
- `get_funnel_metrics` (unreferenced_func) — shared/db.py:1273 의 get_funnel_metrics 는 정의만 존재하고 호출부가 리포 전역에 0개다. (1) grep -rn 'get_funnel_metrics'
- `classify_lifecycle` (unreferenced_func) — shared/db.py:1136 의 classify_lifecycle 는 정의부 단 1곳만 존재하고 호출자가 전무. 전체 파일형(py/md/json/html/js) grep 에서 
- `next_collection_eta` (unreferenced_func) — shared/db.py:1520 의 next_collection_eta() 는 전 리포 grep 에서 정의 1건 외 참조 0. __all__ 없음 + shared.db 와일드카드 
- `get_best_publish_hour` (unreferenced_func) — shared/db.py:1003 get_best_publish_hour 는 리포 전체에서 정의부(1003) 외 참조가 0건. 정적/동적 경로 전수 확인 결과 모두 도달 불가: (1
- `get_opportunity_vs_views` (unreferenced_func) — shared/db.py:1304 의 get_opportunity_vs_views 는 정의만 존재하고 호출부가 전 리포에 0건(모든 확장자 grep 확인). __all__ 없음, g
- `get_revision_lifecycle` (unreferenced_func) — 전 리포 전 확장자 검색에서 shared/db.py:1503 자기 정의 1줄 외 참조 0건. db.py에 __all__ 없고 리포 전역 'from shared.db import *
- `get_event_timeline` (unreferenced_func) — shared/db.py:1416 get_event_timeline 은 리포 전체에서 정의부 1곳 외 참조 0. 유일 소비처였던 JARVIS03_RADAR/app.py 는 이미 삭제
- `get_error_resolution` (unreferenced_func) — shared/db.py:2291 get_error_resolution 는 리포 전체에서 정의 1건 외 참조 0. db.py 에 __all__ 없음(와일드카드 수출 불가), from
- `get_action_board` (unreferenced_func) — shared/db.py:1173 의 get_action_board 는 전 리포·전 git 히스토리 통틀어 정의행 1개뿐(호출 0). db.py 에 __all__ 없음 → star-
- `get_ops_metrics` (unreferenced_func) — 전 리포 grep(파이썬 및 전체 파일, .venv/__pycache__ 제외) 결과 shared/db.py:1364 정의 1줄 외 참조 0회. 현행 유일 대시보드 hub.py는 
- `get_revision_effect` (unreferenced_func) — shared/db.py:1321 의 get_revision_effect 는 리포 전역에서 정의부 단 1회만 존재하고 호출부가 전무하다. 반환 dict 키(lift_pct·revis
- `classify_lifecycle` (unreferenced_func) — shared/db.py:1136의 classify_lifecycle는 전역에서 정의 1곳 외 참조 0. db.py에 __all__ 없음(수출 게이팅 무관), getattr/impo
- `get_funnel_metrics` (unreferenced_func) — 전역 grep 결과 get_funnel_metrics 는 shared/db.py:1273 정의부 1회만 존재하고 호출·attribute 접근(db.get_funnel_metrics
- `next_collection_eta` (unreferenced_func) — shared/db.py:1520 의 next_collection_eta() 는 전 리포에서 정의 1곳 외 참조 0회. 전체 확장자 grep(json·md·html 포함)도 정의 라
- `get_best_publish_hour` (unreferenced_func) — shared/db.py:1003 get_best_publish_hour(platform) 는 전 리포 전수 grep(.py/.json/.md 및 모든 파일타입) 결과 정의 라인 단
- `style_corpus_stats` (unreferenced_func) — shared/db.py:1691에 정의된 style_corpus_stats는 전 리포(.py 및 전체 파일 타입) grep에서 자기 정의 1행 외 참조 0. 형제 style_cor
- `get_opportunity_vs_views` (unreferenced_func) — shared/db.py:1304 정의부가 리포 전체에서 유일한 출현(비-py 포함, venv·pycache 제외 grep 0건). db.py에 __all__ 없음, db 모듈 대상
- `get_keyword_perf_scatter` (unreferenced_func) — shared/db.py:1486 의 get_keyword_perf_scatter 는 정의부 단 1곳 외 참조 0개. 폐기된 레거시 대시보드(JARVIS03_RADAR/app.py)
- `get_revision_lifecycle` (unreferenced_func) — shared/db.py:1503 의 get_revision_lifecycle 은 전 리포 grep(모든 확장자, venv/pycache/tool-results 제외)에서 정의부 1
- `get_event_timeline` (unreferenced_func) — shared/db.py:1416의 def 1줄이 유일한 등장. 전 리포 grep에서 호출 0회. db.py에 __all__ 없고 `from shared.db import *` 와일
- `get_top_keywords` (unreferenced_func) — shared/db.py:1470 의 get_top_keywords 는 전역에서 호출자 0개. --include='*.py' 전수 grep 결과 정의부 한 줄뿐이고, 유일한 다른 언
- `get_all_settings` (unreferenced_func) — shared/db.py:1456 의 정의가 유일한 출현. 전 리포 grep(모든 파일유형) 호출부 0. db.py에 __all__ 없어 wildcard 수출도 아니고, 임포트는 전
- `get_setting` (unreferenced_func) — 전 리포 grep(.py 및 전체 파일타입) 결과 get_setting 은 정의부(shared/db.py:1432) 단 한 줄뿐이고 호출부 0. db.py 에 __all__ 없으며
- `get_post_history` (unreferenced_func) — shared/db.py:564 의 get_post_history 는 전 리포(모든 파일 종류) 통틀어 정의 1곳 외 참조 0회. db.py 에 __all__ 이 없어 와일드카드 재
- `get_pipeline_with_sector` (unreferenced_func) — 전 리포 grep 결과 정의 1행(shared/db.py:1056)만 존재, 호출부 0. shared/db.py에 __all__ 없어 수출 게이팅도 없음. getattr/impor
- `get_recent_daily_reviews` (unreferenced_func) — shared/db.py:1984 get_recent_daily_reviews 는 정의 1곳뿐, 전역 grep 0 호출. hub.py의 load_daily_review(1053행)는
- `get_error_resolution` (unreferenced_func) — shared/db.py:2291의 get_error_resolution은 전 리포 grep에서 정의 라인 하나뿐(호출·import·문자열 참조 0). 동적 경로 전수 점검 결과: 
- `get_pipeline_history` (unreferenced_func) — shared/db.py:536 정의부 외 전 리포 참조 0건(py·md·json·txt 전수). db.py에 __all__ 없어 명시 수출 아니며 'from shared.db im
- `get_today_posts` (unreferenced_func) — shared/db.py:1044의 get_today_posts는 전 리포(py/md/json/txt/yml) grep 결과 정의 1곳뿐, 호출 0회. shared/db.py에 __
- `get_performance_history` (unreferenced_func) — shared/db.py:1069의 get_performance_history는 정의 1곳 외 전 리포 참조 0 (py·md·json·txt 모두). db.py에 __all__ 없음
- `get_keyword_trend_history` (unreferenced_func) — shared/db.py:457 의 get_keyword_trend_history 는 전 리포 grep(모든 확장자, __pycache__/.git 제외) 결과 정의 1곳만 존재하고
- `set_setting` (unreferenced_func) — set_setting 은 shared/db.py:1445 정의 외 참조가 전 리포에서 0회다. git grep·전역 grep 모두 정의 한 줄만 반환. 짝 함수 get_settin
- `get_trend_history` (unreferenced_func) — shared/db.py:447 의 get_trend_history 는 정의 1곳 외 전 리포 참조 0회. __all__ 없음(재수출 없음), getattr/importlib 문자열
- `learned_weights_history` (unreferenced_func) — shared/db.py:1804의 learned_weights_history는 전 리포(모든 확장자, git-tracked 포함) grep에서 정의 1건 외 참조 0건. share
- `feedback_penalty_all` (unreferenced_func) — shared/db.py:1860 에 유일 정의, 호출부 0회. db.py에 __all__ 없고 getattr/importlib 문자열 디스패치·버스·DEFAULT_JOBS call
- `get_daily_review` (unreferenced_func) — shared/db.py:1976 get_daily_review 전 리포 grep 결과 정의 1행뿐 — 호출·속성접근(db.get_daily_review) 0건. db.py 에 __
- `style_corpus_count` (unreferenced_func) — shared/db.py:1647 의 style_corpus_count() 는 정의부만 존재하고 호출 0건. --include='*.py' 및 .md/.json/.txt 전수 gre

## shared/llm.py
- `render_catalog` (unreferenced_func) — shared/llm.py 의 render_catalog 는 정의(704)와 __all__ 수출(729) 두 곳에만 존재하고 호출자가 전무. from shared.llm import
- `_invoke_sdk_async` (unreferenced_func) — shared/llm.py:398의 async def _invoke_sdk_async 는 전 리포지토리 grep에서 정의부 1건 외 참조 0건. __all__ 미수출 + 언더스코어 

## shared/seo.py
- `seo_score` (unreferenced_func) — shared/seo.py:243 의 seo_score() 는 전 리포 grep 에서 정의부(243)와 모듈 docstring 자기언급(9) 두 줄만 나오고 실제 호출 0건. 동적 
- `_claude_compress` (dead_only_caller) — 확인 완료. `_claude_compress`(seo.py:163)는 유일하게 seo.py:135의 `compress_to_korean` 내부에서만 호출된다. 동적 경로 전수 확인
- `_emit_overflow_event` (dead_only_caller) — 신고 정확. `_emit_overflow_event`(shared/seo.py:228)는 오직 `compress_to_korean` 내부 4곳(128/144/154/159)에서만 

## shared/tools.py
- `tools_by_names` (unreferenced_func) — 전 리포 grep에서 shared/tools.py 내 def(132행)와 __all__ 수출(307행) 외 참조 0. shared.tools를 import하는 모든 곳(JARVIS
- `render_for_router_prompt` (unreferenced_func) — shared/tools.py의 render_for_router_prompt는 도구 카탈로그(_TOOLS)를 렌더하는 함수인데, 실사용 호출자 3곳(router.py:80, bot.