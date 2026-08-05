"""JARVIS04_SCHEDULER/job_registry.py — 모든 default 잡의 *단일 진실 소스*.

★ 데몬에 박혀 있던 16개 add_job 호출이 여기로 이관됨.
콜백 함수는 lazy import (importlib) — 모듈 import 순서 영향 없음.

새 default 잡 추가:
    DEFAULT_JOBS 리스트에 dict 추가 → 데몬 재시작.

새 *온디맨드* 잡 (1회성·임시):
    job_controller.add_oneoff_job() 사용 — APPROVAL 게이트 통과 후 등록.

owner_agent: 잡 소유 에이전트 — job_runs 적재 + UI 표시용.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Optional


# ── default 잡 카탈로그 ──────────────────────────────────────────
# 데몬 _start_scheduler() 의 16개 add_job 호출이 여기로 이관됨.
# 잡 ID·name·callback·cron 표현 *불변* — 16시 cron 영향 0 보장.

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

DEFAULT_JOBS: list[dict] = [
    # ── JARVIS03 RADAR ─────────────────────────────────────────
    # ★ 06:00 조기 수집 (ERRORS [290] — 2026-07-03): 종전 최조기 09:00 은 07:00 경제
    #   브리핑보다 늦어 아침 발행이 *항상* 전일 폴백 데이터(신선도·DataLab 無) 사용.
    {"id":"radar_trends_06", "name":"트렌드 수집(06시 — 경제 브리핑 前)", "trigger":"cron",
     "kwargs":{"hour":6,  "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends_morning",
     "misfire_grace_time":1200, "owner":"jarvis03_radar", "edges":["e13"]},
    {"id":"radar_trends_09", "name":"트렌드 수집(09시)",  "trigger":"cron",
     "kwargs":{"hour":9,  "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "edges":["e13"]},
    {"id":"radar_trends_12", "name":"트렌드 수집(12시)",  "trigger":"cron",
     "kwargs":{"hour":12, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "edges":["e13"]},
    {"id":"radar_trends_15", "name":"트렌드 수집(15시)",  "trigger":"cron",
     "kwargs":{"hour":15, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "edges":["e13"]},
    {"id":"radar_perf",      "name":"성과 수집",            "trigger":"cron",
     "kwargs":{"hour":23, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_performance",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "edges":["e9","e10"]},
    {"id":"analyzer_fb",     "name":"분석 fallback",        "trigger":"interval",
     "kwargs":{"minutes":5}, "callback":"JARVIS03_RADAR.jobs.job_analyzer_fallback",
     "misfire_grace_time":600,  "owner":"jarvis03_radar"},
    {"id":"recycle",         "name":"재활용 제안",          "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "hour":9, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_recycle_check",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"auto_approve",    "name":"1h 자동 승인",         "trigger":"interval",
     "kwargs":{"minutes":30}, "callback":"JARVIS03_RADAR.jobs.job_auto_approve",
     "misfire_grace_time":600,  "owner":"jarvis03_radar"},
    {"id":"keyword_embed_backfill", "name":"키워드 임베딩 백필 (RAG cold-start)",
     "trigger":"cron", "kwargs":{"hour":2, "minute":45},
     "callback":"JARVIS03_RADAR.jobs.job_keyword_embed_backfill",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
    {"id":"daily_review",    "name":"일일 종합 분석",       "trigger":"cron",
     "kwargs":{"hour":22, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_daily_review",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
    {"id":"learn_log",       "name":"예측/실측 적재",       "trigger":"cron",
     "kwargs":{"hour":23, "minute":30}, "callback":"JARVIS03_RADAR.jobs.job_learn_log",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
    {"id":"feedback_upd",    "name":"피드백 페널티 갱신",   "trigger":"cron",
     "kwargs":{"hour":4,  "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_feedback_update",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
    {"id":"train_weights",   "name":"가중치 학습 + 백테스트", "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":4, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_train_weights",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
    # ── JARVIS02 WRITER (legacy schedule_mode → 이관 완료) ─────
    # ★ 경제 선계산은 별도 고정 잡이 아니라 06:00 트렌드 잡(radar_trends_06 → job_collect_trends_morning)
    # 말미에 *이벤트 체이닝*: 트렌드 분석(topic_pack 빌드)이 끝나는 즉시 이어서 실행(고정 지연 없음 —
    # 사용자 박제 2026-07-18). 09 의 job_precollect_economic 이 동적 데드라인으로 발행창 미침범.
    # ★ 발행 시각 07:00 (사용자 박제 2026-07-18): 06:00 트렌드+선계산(~06:20 완료) → 07:00 발행 사이
    # ~40분 Max 풀 회복 갭 확보 → writer 스톨 방지 강화. (종전 06:30 은 회복 갭 ~5분으로 빡빡)
    # ★ 발행 전 자체수리 + 발행 *하나의 세트* (사용자 박제 2026-06-28):
    # 07:00 callback 진입 → 발행 전 Tier-1 자체수리(LLM-0 sweep, 수초) → 즉시 경제 브리핑 발행.
    # 비싼 LLM 심층 감사는 `j07_deep_audit`(주 1회, 토) 로 분리 (발행 지연 0).
    {"id":"j01_economic_post",      "name":"자가진단+경제 브리핑 발행 07:00", "trigger":"cron",
     "kwargs":{"hour":7, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.run_self_repair_then_economic",
     # ★ requires — 선행 없으면 발행하지 않는다 (사용자 박제 2026-07-23, job_prereq.py 참조).
     #   06:00 트렌드 수집이 주제팩·데이터를 만든다. 잠들었다 깨면 유예 차이로 선행만 폐기되고
     #   발행만 살아남던 결함 차단. 미충족 시 선행 즉시 실행 → 회복 갭(07:00-06:00) 뒤 발행.
     "requires":["radar_trends_06"],
     "misfire_grace_time":3600, "owner":"jarvis02_writer", "edges":["e13"]},
    # ★ 테마 선계산 (20:00 = 21:00 발행 1시간 전 — 발행창 밖 저부하 창, 사용자 박제 2026-07-18):
    # 테마를 고정(pin)하고 무거운 fact·chart 추출을 미리 수행·캐시 → 발행창 추출 LLM 0회 → writer 가
    # 회복된 Max 풀에서 실행(300s 스톨 조건 제거). 회복 갭 = 21:00-20:00 = 1시간(cron 차이에서 파생).
    # ★ 2026-07-23 부터 *필수 선행* — 종전 "순수 최적화·실패해도 random 폴백" 은 폐지됐다.
    #   이게 안 돌면 21:00 발행은 시작되지 않는다 (job_prereq.gate 가 requires 로 강제).
    {"id":"j02_theme_precollect",   "name":"테마 선계산 20:00", "trigger":"cron",
     "kwargs":{"hour":20, "minute":0},
     "callback":"JARVIS09_COLLECTOR.precollect.job_precollect_theme",
     "misfire_grace_time":1200, "owner":"jarvis09_collector"},
    # ★ 발행 전 자체수리 + 테마글 발행 *하나의 세트* (사용자 박제 2026-06-28):
    # 16:00 callback 진입 → 발행 전 Tier-1 자체수리(LLM-0 sweep, 수초) → 즉시 테마글 발행.
    # 비싼 LLM 심층 감사는 `j07_deep_audit`(주 1회, 토) 로 분리.
    {"id":"j01_theme_post_21",      "name":"자가진단+테마 발행 21:00 ★", "trigger":"cron",
     "kwargs":{"hour":21, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.run_self_repair_then_theme",
     # ★ requires — 20:00 선계산(테마 고정 + fact·chart 선수집)이 *필수 선행*
     #   (사용자 박제 2026-07-23 — 종전 "순수 최적화·폴백 허용" 폐지).
     "requires":["j02_theme_precollect"],
     "misfire_grace_time":3600, "owner":"jarvis02_writer", "edges":["e13"]},

    # ── JARVIS01 MASTER ────────────────────────────────────────
    {"id":"jarvis00_router_health", "name":"JARVIS01 라우터 헬스", "trigger":"cron",
     "kwargs":{"minute":0},
     "callback":"JARVIS01_MASTER.core_agent._job_router_health",
     "misfire_grace_time":600, "owner":"jarvis01_master"},
    # ── JARVIS07 자가 진단·수정 (★ 사용자 박제 2026-06-28 — 2단 분리) ──
    # 발행 직전(07:00 / 21:00 callback): Tier-1 LLM-0 자체수리 sweep 만 (수초, 발행 지연 0).
    #   callback: `run_self_repair_then_economic` / `run_self_repair_then_theme`.
    # 심층 LLM 감사(backlog Tier1→2 + 광범위 코드 감사): `j07_deep_audit` 별도 cron(주 1회, 토).
    # → 학습 자산이 쌓일수록 다음 발행 전 sweep 자동수리율↑ (복리 학습 루프).
    # ── JARVIS02 WRITER — SEO 학습 ────────────────────────────────
    {"id":"weekly_seo_learn",  "name":"주간 SEO 학습·비교·업데이트", "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "hour":6, "minute":0},
     "callback":"JARVIS02_WRITER.seo_learner.run_seo_learning",
     "misfire_grace_time":7200, "owner":"jarvis02_writer"},
    # ── JARVIS02 WRITER — 분량 학습 보정 (ERRORS [139], 매월 1일 04:00) ────
    {"id":"monthly_spec_learn", "name":"분량 학습 보정 — post_type_specs 자동 제안", "trigger":"cron",
     "kwargs":{"day":1, "hour":4, "minute":0},
     "callback":"JARVIS02_WRITER.post_type_specs_job.run_monthly_analysis",
     "misfire_grace_time":7200, "owner":"jarvis02_writer"},
    # ── JARVIS00_INFRA ───────────────────────────────────────────
    # ★ 데몬 hang 워치독 신호 (ERRORS [318] — 2026-07-04): 스케줄러 스레드풀이
    #   *실제로 잡을 실행 중* 임을 60초마다 heartbeat 파일 mtime 으로 각인.
    #   06:07 hang 사고(메인스레드 무한 파이썬 루프 → GIL 기아 → 전 잡 정지)처럼
    #   PID 는 살아있어도 스케줄러가 멎으면 이 잡이 안 돌아 heartbeat stale →
    #   jarvis_keeper.py 워치독이 강제 재시작. interval 잡이라 스케줄러 기아 시
    #   동반 정지 = 정확한 hang 신호.
    {"id":"infra_heartbeat", "name":"데몬 heartbeat (keeper 워치독)", "trigger":"interval",
     "kwargs":{"seconds":180}, "callback":"JARVIS00_INFRA.infra_agent.job_heartbeat",
     "misfire_grace_time":60, "owner":"jarvis00_infra"},
    {"id":"db_backup",       "name":"DB 백업",              "trigger":"cron",
     "kwargs":{"hour":3, "minute":0}, "callback":"JARVIS00_INFRA.infra_agent.job_db_backup",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    # ★★ DB 보존 정책 일괄 적용 (2026-07-27) — 종전 `ev_cleanup`(주1회)·
    #   `vision_history_cleanup`(매일) 두 잡을 **하나로 통합**했다.
    #   왜: 테이블마다 잡을 따로 두니 *규칙 없는 테이블이 방치* 됐다 — 실측 `job_runs`
    #   155,483행 · `qa_ingested_sessions` 15,567행이 무한 누적(DB 209MB 의 주범).
    #   보존 일수는 `shared/db.RETENTION` 단일 레지스트리가 소유하고 이 잡은 집행만 한다.
    #   매일 도는 이유: vision_agent_history 가 30초마다 쌓여 하루만 밀려도 커진다.
    {"id":"db_retention",    "name":"DB 보존정책 정리 (매일 03:15)", "trigger":"cron",
     "kwargs":{"hour":3, "minute":15},
     "callback":"JARVIS00_INFRA.infra_agent.job_db_retention",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    {"id":"file_cleanup",    "name":"파일 정리",            "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "week":"*/2", "hour":4, "minute":0},
     "callback":"JARVIS00_INFRA.infra_agent.job_file_cleanup",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    {"id":"fuse_hidden_cleanup", "name":".fuse_hidden 즉시 정리 (15분)", "trigger":"interval",
     "kwargs":{"minutes":15},
     "callback":"shared.file_cleanup.cleanup_fuse_hidden",
     "misfire_grace_time":300, "owner":"jarvis00_infra"},
    # ★ 알림 아웃박스 재전송 (사용자 승인 2026-07-25) — 네트워크 단절 중 전송 실패한
    #   텔레그램 메시지를 되살린다. 평소엔 표가 비어 있어 즉시 반환(비용 ~0).
    #   복구 순간의 즉시 전달은 `notify._on_send_success` 가 담당하고, 이 잡은 *바닥* 이다
    #   (아무 메시지도 안 나가는 조용한 시간대에도 밀린 것이 5분 안에 흘러가도록).
    {"id":"notify_outbox_flush", "name":"밀린 알림 재전송 (5분)", "trigger":"interval",
     "kwargs":{"minutes":5},
     "callback":"shared.notify.job_flush_outbox",
     "misfire_grace_time":300, "owner":"jarvis00_infra"},
    # ── 발행 완결성 감사 ────────────────────────────────────────────
    # ★ 2026-07-29: 종전 `log_monitor_{economic,theme}` 2잡 폐기.
    #   그 잡들은 로그 텍스트에 '네이버' 와 '✅' 가 각각 한 번이라도 있으면 성공으로 봤다 —
    #   10일 표본 위양성 2일·위음성 1일. 틀린 초록불은 없느니만 못하다.
    #   대체 잡은 아래 `_build_publish_audit_jobs()` 가 **발행 잡에서 파생** 해 만든다
    #   (시각을 박지 않는다 — 선례: 쿠키 사전점검).
    # ── JARVIS06 IMAGE — 인포그래픽 디자인 강화학습 (★ 사용자 박제 2026-07-05) ──
    # 매일 05:00 Claude 가 새 전문 디자인 레시피 1개 창작 → 게이트 통과분만 누적 → pro_templates 소비.
    # 오류학습과 동형: 검증된 자산만 생존 → 다양성·품질 복리 상승. (ERRORS [359])
    # ★ 2026-07-28 매일 → **일요일 주 1회** (사용자 승인). 토큰 절감.
    #   근거: 레시피가 이미 33종 누적돼 한계효용이 낮다. 게다가 실측상 비전 학습 성공률이
    #   30건 중 7건(23%)이고, 실패하면 LLM 을 27회(호출당 $1.95) 쓰고도 결과물은
    #   **LLM 0인 코드 라이브러리/결정론** 에서 나온다 — 즉 비용만 나가고 산출은 무관.
    #   주 1회면 학습은 계속되면서 소비는 1/7 이 된다.
    {"id":"j06_design_learn",   "name":"인포그래픽 디자인 학습 (일 05:00)", "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":5, "minute":0},
     "callback":"JARVIS06_IMAGE.design_learner.job_learn_design",
     "misfire_grace_time":7200, "owner":"jarvis06_image"},
    # ── JARVIS07 GUARDIAN ─────────────────────────────────────────
    {"id":"auditor_weekly",     "name":"GUARDIAN Auditor (헌법 위반·드리프트 — 일요일 04:30)",
     "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":4, "minute":30},
     "callback":"JARVIS07_GUARDIAN.auditor.job_auditor_weekly",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    {"id":"guardian_log_scan",  "name":"GUARDIAN 로그 스캔 (5분)", "trigger":"interval",
     "kwargs":{"minutes":5},
     "callback":"JARVIS07_GUARDIAN.guardian_agent.job_scan_logs",
     "misfire_grace_time":300, "owner":"jarvis07_guardian"},
    # guardian_archive 잡 제거 — 오류 DB 영구 보존 정책 (사용자 박제 2026-05-25)
    {"id":"j07_git_audit",      "name":"GUARDIAN git 회고 박제 (매일 03:30)", "trigger":"cron",
     "kwargs":{"hour":3, "minute":30},
     "callback":"JARVIS07_GUARDIAN.guardian_agent.job_git_audit",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    # ★ 발행과 분리된 심층 LLM 감사 (사용자 박제 2026-06-28) — DB 백업 03:00 이후.
    #   1) backlog Tier 1→2 (실제 지문 학습) 2) 광범위 코드 감사. 발행 직전엔 LLM-0 sweep 만.
    # ★★ 매일 → **토요일 주 1회** (사용자 박제 2026-07-26 — 토큰 절감).
    #   근거(실측): 이 잡이 `guardian` alias 소비의 **전량**(7일 3.41M, 단건 최대 439K·10턴).
    #   도구를 들고 저장소를 훑는 무거운 세션이라 호출 1건이 크다.
    #   ★ 안전 근거 — 매일→주1회로 늦어지는 것과 늦어지지 않는 것을 구분해 둔다:
    #     · 늦어지지 않음: 신규 오류 자동수정. `j07_retry_pending`(10분)이 `status='new'` 를
    #       계속 `_orchestrate`(Tier1→Tier2, 상한 3회)로 돌린다. 발행 직전 LLM-0 sweep 도 그대로.
    #     · 늦어짐: ① 광범위 코드 감사(새 잠재버그 발굴) ② backlog 재처리 ③ 격리버킷 보고.
    #       즉 "이미 난 오류의 처리"가 아니라 "아직 안 난 문제 찾기"가 주 1회가 된다.
    {"id":"j07_deep_audit",     "name":"GUARDIAN 심층 코드 감사 (토 03:00)", "trigger":"cron",
     "kwargs":{"day_of_week":"sat", "hour":3, "minute":0},
     "callback":"JARVIS07_GUARDIAN.guardian_agent.job_deep_audit",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    {"id":"j07_retry_pending",  "name":"GUARDIAN 잔류 오류 재처리 (10분)", "trigger":"interval",
     "kwargs":{"minutes":10},
     "callback":"JARVIS07_GUARDIAN.guardian_agent.job_retry_pending",
     "misfire_grace_time":600, "owner":"jarvis07_guardian"},
    {"id":"j07_qa_ingest",      "name":"QA 지식베이스 세션 증분 학습 (매일 02:00)", "trigger":"cron",
     "kwargs":{"hour":2, "minute":0},
     "callback":"JARVIS07_GUARDIAN.qa_store.job_ingest_sessions",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    # ★ 사용자 박제 2026-05-25 (ERRORS [167]) — Cowork (Claude Desktop App) 대화 학습 흡수
    #   Cowork 에는 hook 메커니즘 없어 5분 간격 잡으로 거의 실시간 흡수.
    #   매 Q&A 끝나면 최대 5분 내 qa_store 에 누적.
    {"id":"j07_cowork_ingest",  "name":"Cowork 대화 학습 흡수 (5분 간격)", "trigger":"interval",
     "kwargs":{"minutes":5},
     "callback":"JARVIS07_GUARDIAN.qa_store.job_ingest_cowork_sessions",
     "misfire_grace_time":300, "owner":"jarvis07_guardian"},
    # ★ 벡터 인덱스 백필 — 매주 일요일 02:30 전수 재동기화
    #   ChromaDB 에 아직 없는 qa_entries 를 임베딩해서 시맨틱 검색 가능하게 함.
    #   최초 실행 시 ~3,859건 처리 (배치 500건씩). 이후 증분 upsert (안전).
    {"id":"j07_vector_backfill", "name":"벡터 인덱스 백필 (매주 일요일 02:30)", "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":2, "minute":30},
     "callback":"JARVIS07_GUARDIAN.vector_store.job_build_vector_index",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    # ★ 글 품질 강화학습 보상 귀속 (ADR 014 — 2026-07-03) — 매일 23:45.
    #   daily_review(22:00)·learn_log(23:30) 이후 실행. days=3 롤링 윈도우로
    #   하루 중 어느 시각에 쓴 글이든 당일 미귀속 기록 전수 처리.
    {"id":"j07_quality_learn",  "name":"글 품질 강화학습 보상 귀속 (매일 23:45)", "trigger":"cron",
     "kwargs":{"hour":23, "minute":45},
     "callback":"JARVIS07_GUARDIAN.quality_learner.job_quality_learn",
     "misfire_grace_time":3600, "owner":"jarvis07_guardian"},
    # ── JARVIS09 COLLECTOR ────────────────────────────────────────────
    {"id":"j09_cleanup",        "name":"COLLECTOR 7일 캐시 정리 (매주 월요일 03:00)", "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "hour":3, "minute":0},
     "callback":"JARVIS09_COLLECTOR.collector_agent.job_cleanup_cache",
     "misfire_grace_time":3600, "owner":"jarvis09_collector"},
]


# ══════════════════════════════════════════════════════════════════
#  발행 前 쿠키 사전 점검 — **발행 시각에서 파생** (사용자 박제 2026-07-25)
# ══════════════════════════════════════════════════════════════════
# ★ 왜 파생인가 (2026-07-25 발견한 단절):
#   `JARVIS08_PUBLISH/credentials/LOGIN_SUPREME_LAW.md` 는 쿠키 자동갱신 cron 잡 4개
#   (`j02_{naver,tistory}_cookie_pre_{morning,afternoon}` 06:30·15:30)를 규정하고 있었는데
#   **DEFAULT_JOBS 에 하나도 없었다** — `login_manager.job_pre_publish_check` 는 호출자 0인
#   죽은 함수였다. 문서가 진실이라 믿고 코드가 따라오지 않은 전형적 드리프트.
#   게다가 그 문서 시각(15:30)은 옛 16시 발행 기준이라, 살아 있었어도 21:00 발행에는 어긋났다.
#   → 시각을 박지 않고 *실제 발행 잡 cron 에서 30분 전* 으로 파생한다.
#     발행 시각을 옮기면 쿠키 사전점검도 자동으로 따라 이동한다(② 동적 설계).
_COOKIE_PRECHECK_LEAD_MIN = 30



# ── heartbeat 파생 — **잡 카탈로그의 주인이 직접 답한다** (2026-08-05) ──────────
#   종전엔 `jarvis_keeper.py` 가 `j.get("id") == "infra_heartbeat"` 문자열과 kwargs
#   파싱을 자체 보유했다(①위반). 공백 회계까지 같은 값이 필요해지면서 **세 번째 사본**이
#   생길 참이었다. 잡 정의의 주인이 잡에 대한 질문에 답한다.
def heartbeat_job_id() -> str:
    """데몬 생존 신호 잡의 ID — 콜백 소유자(infra_agent.job_heartbeat)에서 파생.

    ID 문자열을 비교하지 않는다. *무엇을 하는 잡인가* 로 찾는다 — 이름이 바뀌어도 따라온다.
    """
    for j in DEFAULT_JOBS:
        if str(j.get("callback", "")).endswith(".job_heartbeat"):
            return str(j.get("id") or "")
    return ""


def heartbeat_interval_seconds() -> int:
    """그 잡의 실제 주기(초). 못 읽으면 0 — 호출자가 fail-closed 판단."""
    jid = heartbeat_job_id()
    for j in DEFAULT_JOBS:
        if j.get("id") == jid and j.get("trigger") == "interval":
            kw = j.get("kwargs") or {}
            return int(kw.get("seconds") or 0) + int(kw.get("minutes") or 0) * 60
    return 0


def _publish_job_times() -> list[tuple[str, int, int]]:
    """(발행잡ID, 시, 분) — 위 리터럴 카탈로그에서 직접 파생(순환 import 회피)."""
    out = []
    for j in DEFAULT_JOBS:
        if j.get("trigger") != "cron":
            continue
        # ★ 발행 잡 판별은 `job_llm_priority.is_publish_callback` 단독 (마커 소유자).
        #   종전엔 여기서 문자열을 다시 검사해 같은 판단이 2벌이었다.
        from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback
        if not is_publish_callback(j.get("callback")):
            continue
        kw = j.get("kwargs") or {}
        h = kw.get("hour")
        if isinstance(h, int):
            out.append((j["id"], h, int(kw.get("minute") or 0)))
    return out


def _build_cookie_precheck_jobs() -> list[dict]:
    """발행 잡마다 '발행 N분 전 쿠키 점검' 잡 1개씩 생성 (플랫폼 전체 일괄)."""
    jobs = []
    for jid, h, m in _publish_job_times():
        total = (h * 60 + m - _COOKIE_PRECHECK_LEAD_MIN) % (24 * 60)
        ph, pm = divmod(total, 60)
        jobs.append({
            "id": f"j08_cookie_precheck_{jid}",
            "name": f"발행 前 쿠키 점검 ({ph:02d}:{pm:02d} — {jid} 발행 {_COOKIE_PRECHECK_LEAD_MIN}분 전)",
            "trigger": "cron", "kwargs": {"hour": ph, "minute": pm},
            "callback": "JARVIS08_PUBLISH.credentials.login_manager.job_pre_publish_check",
            "misfire_grace_time": 1200, "owner": "jarvis08_publish",
        })
    return jobs


DEFAULT_JOBS.extend(_build_cookie_precheck_jobs())


# ══════════════════════════════════════════════════════════════════
# ★ 발행 완결성 감사 (2026-07-29 전수 감사 1위 — 사용자 승인)
#   실측: 2026-07-12~28 기대 68건 중 결손 18건(달성률 73.5%)인데 job_runs 는
#   경제 23/23·테마 14/14 전부 success=1. 잡은 '내가 끝까지 돌았는가' 만 알고
#   '글이 나갔는가' 는 모른다 — 그걸 묻는 코드가 저장소에 0줄이었다.
#   판정 본체는 `JARVIS08_PUBLISH/publish_ledger.py` (발행 도메인 소유). 여기는 *시각* 만 정한다.
#   쿠키 사전점검과 같은 형태로 **발행 잡 cron 에서 파생** — 발행 시각을 옮기면 감사도 따라 이동.
#   ★ 지연(lag)도 리터럴로 박지 않는다 — 초판은 `50분` 고정이었는데 실측 59건 중 **19건(32%)**
#     이 그 창을 넘겼다(최대 +246분). 감사가 너무 이르면 *성공한 발행을 결손으로 오신고* 해
#     폐기한 log_monitor 의 위양성을 방향만 바꿔 재도입하게 된다.
#     → `publish_ledger.audit_lag_minutes(misfire_grace)` 가 파생한다:
#       잡이 늦게 시작될 수 있는 상한(misfire_grace_time) + 플랫폼 수 × 플랫폼당 상한
#       (`watchdog.BLOG_ACTION_DEADLINE_SEC`). 그 값들을 바꾸면 감사 시각이 따라온다.
def _build_publish_audit_jobs() -> list[dict]:
    """발행 잡마다 '발행 N분 후 완결성 감사' 잡 1개씩 생성 (N 은 파생)."""
    from JARVIS08_PUBLISH.publish_ledger import audit_lag_minutes

    grace_by_id = {j["id"]: int(j.get("misfire_grace_time") or 0) for j in DEFAULT_JOBS}
    jobs = []
    for jid, h, m in _publish_job_times():
        lag = audit_lag_minutes(grace_by_id.get(jid, 0))
        total = (h * 60 + m + lag) % (24 * 60)
        ah, am = divmod(total, 60)
        jobs.append({
            "id": f"j08_publish_audit_{jid}",
            "name": f"발행 완결성 감사 ({ah:02d}:{am:02d} — {jid} 발행 {lag}분 후)",
            "trigger": "cron", "kwargs": {"hour": ah, "minute": am},
            "callback": "JARVIS08_PUBLISH.publish_ledger.job_audit_publish_completeness",
            # 데몬이 늦게 떠도 그 슬롯 감사는 살린다 — 다음 발행 슬롯 전까지 유효.
            "misfire_grace_time": 6 * 3600, "owner": "jarvis08_publish",
        })
    return jobs


DEFAULT_JOBS.extend(_build_publish_audit_jobs())


def _resolve_callback(path: str) -> Callable:
    """'module.func' → 함수 객체. lazy import."""
    mod_name, fn_name = path.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise AttributeError(f"callback '{path}' 미존재")
    return fn


# 잡 ID → owner agent 매핑 (job_history listener 가 사용)
def get_owner(job_id: str) -> Optional[str]:
    # 연기분(`<id>__deferred`)도 같은 잡이다 — owner 가 비면 job_runs 통계·브리핑이 어긋난다.
    from JARVIS04_SCHEDULER.job_prereq import DEFERRED_SUFFIX
    base = str(job_id).split(DEFERRED_SUFFIX)[0]
    for j in DEFAULT_JOBS:
        if j["id"] == base:
            return j.get("owner")
    return None


def job_specs() -> list[dict]:
    """실제로 등록되는 잡 명세 — DEFAULT_JOBS 의 *선언* 이 아니라 *유효값*.

    ★ 선행 잡의 misfire 유예는 자신을 요구하는 후행에서 파생된다
      (job_prereq.effective_grace — 사용자 박제 2026-07-23). 등록은 파생값을 쓰는데
      화면은 선언값을 보여주면 "복사본을 진실로 믿는" 그 병이다. 등록·표시가 같은
      함수를 읽게 한다. 표시 계층(api_server /api/jobs)도 이것을 호출할 것.
    """
    from JARVIS04_SCHEDULER.job_prereq import effective_grace
    out = []
    for j in DEFAULT_JOBS:
        spec = dict(j)
        try:
            spec["misfire_grace_time"] = effective_grace(j["id"])
        except Exception:
            pass
        out.append(spec)
    return out


def job_func_for(spec: dict) -> tuple[Any, tuple]:
    """잡 하나가 스케줄러에 등록할 `(func, args)` — **등록·자가검사 공통 단일 결정 지점**.

    ★ 왜 함수로 뽑았나 (ERRORS [499]): 종전엔 이 결정이 `register_default_jobs` 몸통 안에만
      있어서, 자가검사(`job_llm_priority.selfcheck`)가 *등록이 실제로 무엇을 넘기는지* 를
      볼 방법이 없었다. 그래서 검사는 상수 하나만 확인하며 통과했고(거짓 보증) 회귀가 그대로
      운영에 나갔다. 이제 검사와 등록이 **같은 함수에 묻는다** — 둘이 갈라질 수 없다.
    """
    jid, cb = spec["id"], spec["callback"]

    if spec.get("executor") == "processpool":
        # ★ 워커가 별도 *프로세스* 라 func 는 pickle 이 아니라 참조(module:qualname)로 왕복한다.
        #   콜러블 인스턴스(`_JobGate`)를 넘기면 그 이름이 인스턴스가 아니라 *클래스* 를 가리켜
        #   워커에서 인자 없이 재구성돼 `_JobGate.__init__() missing 3 ...` TypeError 가 난다.
        #   문자열은 참조가 아니라 *값* 이므로 job_id·callback 을 args 로 넘기고, 콜백 해석·
        #   선행조건·발행창 판정은 워커 안에서 `run_gated` 가 그때그때 재수행한다(② 동적 설계).
        _resolve_callback(cb)                        # 등록 시점 존재 검증 (fail-fast)
        from JARVIS04_SCHEDULER.job_llm_priority import run_gated
        return run_gated, (jid, cb)

    fn = _resolve_callback(cb)
    # ★ 선행조건 집행 단일 지점 (사용자 박제 2026-07-23) — `requires` 가 선언된 잡만
    #   래핑된다. 각 콜백에 if 문을 흩지 않는다. 상세 사유: job_prereq.py 모듈 docstring.
    from JARVIS04_SCHEDULER.job_prereq import gate as _prereq_gate
    fn = _prereq_gate(jid, fn)
    # ★ 발행창 LLM 우선권 (사용자 박제 2026-07-25) — 파이프라인 잡(03 트렌드·09 선계산·
    #   02 발행) 실행 구간을 '발행중' 으로 표시해 배경 LLM 을 보류시킨다. 선례와 같은 자리.
    #   (processpool 이 아닌 잡은 in-process 실행이라 클로저·인스턴스 래핑이 안전하다.)
    from JARVIS04_SCHEDULER.job_llm_priority import gate as _llm_gate
    return _llm_gate(jid, fn), ()


def register_default_jobs(scheduler: Any) -> int:
    """DEFAULT_JOBS 의 모든 잡을 APScheduler 에 등록.

    데몬 _start_scheduler() 가 1회 호출. 잡 ID 동일하므로 기존 동작 유지.
    executor='processpool' 인 잡은 별도 프로세스에서 실행 — 장애 격리.
    Returns: 등록한 잡 수.
    """
    n = 0
    for j in job_specs():
        try:
            fn, args = job_func_for(j)
            exec_kwargs = {}
            if j.get("executor"):
                exec_kwargs["executor"] = j["executor"]
            if j.get("executor") == "processpool":
                # ★ 효과를 동작으로 확인 (CLAUDE.md `patch_effective()` 표준, ERRORS [499]).
                #   워커가 실제로 겪는 참조 왕복을 등록 *전* 에 재현한다. 어긋나면 그 잡이
                #   처음 발화하는 밤 22:00 이 아니라 **부팅 즉시** 드러난다.
                from JARVIS04_SCHEDULER.job_llm_priority import assert_ref_serializable
                why = assert_ref_serializable(j["id"], fn, args)
                if why:
                    raise TypeError(why)
            scheduler.add_job(
                fn, j["trigger"], args=args, **j["kwargs"],
                id=j["id"], name=j["name"],
                misfire_grace_time=j["misfire_grace_time"],
                replace_existing=True,
                **exec_kwargs,
            )
            n += 1
        except Exception as e:
            print(f"  ⚠️ JARVIS04 잡 등록 실패 {j['id']}: {e}")
            _g_report("scheduler", e, module=__name__)
    return n


def render_default_summary() -> str:
    """default 잡 카탈로그 요약 (로그 출력용)."""
    by_owner: dict[str, list[str]] = {}
    for j in DEFAULT_JOBS:
        by_owner.setdefault(j.get("owner", "unknown"), []).append(j["id"])
    lines = []
    for owner, ids in sorted(by_owner.items()):
        lines.append(f"   [{owner}] {len(ids)}개: {', '.join(ids)}")
    return "\n".join(lines)


def cron_times(*, job_id_prefix: str = "", callback_contains: str = "") -> list[str]:
    """DEFAULT_JOBS 의 cron 잡 실행시각 'HH:MM' 목록 (표시용 SSOT 파생).

    ★ 사용자 박제 2026-07-04: 데몬 시작 메시지·대시보드가 스케줄을 *하드코딩*
      하지 말고 이 함수로 파생 → DEFAULT_JOBS 를 바꾸면 텔레그램·웹 표시가
      자동으로 따라온다 (2중·3중 수정 제거).
    """
    out: set[str] = set()
    for j in DEFAULT_JOBS:
        if j.get("trigger") != "cron":
            continue
        if job_id_prefix and not str(j.get("id", "")).startswith(job_id_prefix):
            continue
        if callback_contains and callback_contains not in str(j.get("callback", "")):
            continue
        kw = j.get("kwargs", {})
        if "hour" in kw:
            out.add(f"{int(kw['hour']):02d}:{int(kw.get('minute', 0)):02d}")
    return sorted(out)


def job_window_deadline(job_id: str):
    """이 잡의 **오늘치 실행 창 마감시각** (datetime) — 없으면 None.

    창 = `예정 발화 시각 + misfire_grace_time`. APScheduler 가 "늦어도 여기까지는 실행한다"
    고 이미 정해 둔 경계를 *그대로* 쓴다 — 새 숫자를 만들지 않는다(② 동적 설계).

    ★ 왜 이 함수가 필요한가 (사용자 박제 "발행은 07시와 21시뿐"):
      전제조건이 일시적 이유(네트워크)로 실패했을 때 재시도하려면 **언제까지** 를 정해야
      하는데, 여기에 "30분" 같은 숫자를 새로 박으면 그게 곧 시간외 발행의 씨앗이 된다.
      잡 자신의 유예시간을 경계로 삼으면 발행 시각을 옮겨도 창이 자동으로 따라온다.
    """
    from datetime import datetime, timedelta
    from JARVIS04_SCHEDULER.job_prereq import effective_grace
    spec = next((j for j in DEFAULT_JOBS if j.get("id") == job_id), None)
    if not spec or spec.get("trigger") != "cron":
        return None
    kw = spec.get("kwargs") or {}
    if "hour" not in kw:
        return None
    fire = datetime.now().replace(hour=int(kw["hour"]), minute=int(kw.get("minute", 0)),
                                  second=0, microsecond=0)
    try:
        grace = float(effective_grace(job_id) or spec.get("misfire_grace_time") or 0)
    except Exception:
        grace = float(spec.get("misfire_grace_time") or 0)
    return fire + timedelta(seconds=grace)


_DOW_KO = {"mon": "월", "tue": "화", "wed": "수", "thu": "목",
           "fri": "금", "sat": "토", "sun": "일"}


def job_ids(prefix: str) -> list[str]:
    """id 접두사로 잡 ID 목록 파생 (표시용 SSOT). 예: 'radar_trends' → 06/09/12/15."""
    return [str(j["id"]) for j in DEFAULT_JOBS if str(j.get("id", "")).startswith(prefix)]


def cron_phrase(job_id: str) -> str:
    """잡 1개의 실행 주기를 사람이 읽는 한글 구절로 (표시용 SSOT 파생).

    cron:     '매일 06:30' / '매주 일요일 04:00' / '격주 월요일 04:00' / '매월 1일 03:00'
    interval: '5분 주기' / '30분 주기' / '15분 주기'
    ★ 사용자 박제 2026-07-04: 표시 계층이 스케줄을 하드코딩하지 말고 이 함수로 파생.
    """
    j = next((x for x in DEFAULT_JOBS if x.get("id") == job_id), None)
    if not j:
        return "?"
    kw = j.get("kwargs", {}) or {}
    if j.get("trigger") == "interval":
        for unit, ko in (("weeks", "주"), ("days", "일"), ("hours", "시간"),
                         ("minutes", "분"), ("seconds", "초")):
            if unit in kw:
                return f"{kw[unit]}{ko} 주기"
        return "주기 실행"
    hm = f"{int(kw['hour']):02d}:{int(kw.get('minute', 0)):02d}" if "hour" in kw else ""
    dow = kw.get("day_of_week")
    if dow:
        parts = [_DOW_KO.get(d.strip().lower(), d.strip()) for d in str(dow).split(",")]
        prefix = "격주 " if kw.get("week") else "매주 "
        return f"{prefix}{'·'.join(parts)}요일 {hm}".rstrip()
    if "day" in kw:
        return f"매월 {kw['day']}일 {hm}".rstrip()
    return f"매일 {hm}".rstrip() if hm else "매일"


__all__ = [
    "DEFAULT_JOBS", "register_default_jobs",
    "get_owner", "render_default_summary",
    "cron_times", "cron_phrase", "job_ids",
]
