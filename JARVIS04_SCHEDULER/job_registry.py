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
    # ★ 06:00 조기 수집 (ERRORS [290] — 2026-07-03): 종전 최조기 09:00 은 06:30 경제
    #   브리핑보다 늦어 아침 발행이 *항상* 전일 폴백 데이터(신선도·DataLab 無) 사용.
    {"id":"radar_trends_06", "name":"트렌드 수집(06시 — 경제 브리핑 前)", "trigger":"cron",
     "kwargs":{"hour":6,  "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":1200, "owner":"jarvis03_radar"},
    {"id":"radar_trends_09", "name":"트렌드 수집(09시)",  "trigger":"cron",
     "kwargs":{"hour":9,  "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"radar_trends_12", "name":"트렌드 수집(12시)",  "trigger":"cron",
     "kwargs":{"hour":12, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"radar_trends_15", "name":"트렌드 수집(15시)",  "trigger":"cron",
     "kwargs":{"hour":15, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_trends",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"radar_perf",      "name":"성과 수집",            "trigger":"cron",
     "kwargs":{"hour":23, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_collect_performance",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"analyzer_fb",     "name":"분석 fallback",        "trigger":"interval",
     "kwargs":{"minutes":5}, "callback":"JARVIS03_RADAR.jobs.job_analyzer_fallback",
     "misfire_grace_time":600,  "owner":"jarvis03_radar"},
    {"id":"recycle",         "name":"재활용 제안",          "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "hour":9, "minute":0}, "callback":"JARVIS03_RADAR.jobs.job_recycle_check",
     "misfire_grace_time":3600, "owner":"jarvis03_radar"},
    {"id":"auto_approve",    "name":"1h 자동 승인",         "trigger":"interval",
     "kwargs":{"minutes":30}, "callback":"JARVIS03_RADAR.jobs.job_auto_approve",
     "misfire_grace_time":600,  "owner":"jarvis03_radar"},
    {"id":"voice_index",     "name":"브랜드 보이스 인덱싱", "trigger":"cron",
     "kwargs":{"hour":2, "minute":30}, "callback":"JARVIS03_RADAR.jobs.job_voice_index",
     "misfire_grace_time":3600, "owner":"jarvis03_radar", "executor":"processpool"},
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
    {"id":"j01_screenshot_cleanup", "name":"스크린샷 주간 정리", "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":2, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.cleanup_screenshots",
     "misfire_grace_time":3600, "owner":"jarvis02_writer"},
    # ★ 발행 전 자체수리 + 발행 *하나의 세트* (사용자 박제 2026-06-28):
    # 06:30 callback 진입 → 발행 전 Tier-1 자체수리(LLM-0 sweep, 수초) → 즉시 경제 브리핑 발행.
    # 비싼 LLM 심층 감사는 새벽 04:30 j07_deep_audit 로 분리 (발행 지연 0).
    {"id":"j01_economic_post",      "name":"자가진단+경제 브리핑 발행 06:30", "trigger":"cron",
     "kwargs":{"hour":6, "minute":30},
     "callback":"JARVIS02_WRITER.scheduler.run_self_repair_then_economic",
     "misfire_grace_time":3600, "owner":"jarvis02_writer"},
    # ★ 발행 전 자체수리 + 테마글 발행 *하나의 세트* (사용자 박제 2026-06-28):
    # 16:00 callback 진입 → 발행 전 Tier-1 자체수리(LLM-0 sweep, 수초) → 즉시 테마글 발행.
    # 비싼 LLM 심층 감사는 새벽 04:30 j07_deep_audit 로 분리.
    {"id":"j01_theme_post_21",      "name":"자가진단+테마 발행 21:00 ★", "trigger":"cron",
     "kwargs":{"hour":21, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.run_self_repair_then_theme",
     "misfire_grace_time":3600, "owner":"jarvis02_writer"},

    {"id":"j01_radar_check_09",     "name":"RADAR 자동실행 체크 09:00", "trigger":"cron",
     "kwargs":{"hour":9, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.job_radar_pipeline_check",
     "misfire_grace_time":1800, "owner":"jarvis02_writer"},
    {"id":"j01_radar_check_15",     "name":"RADAR 자동실행 체크 15:00", "trigger":"cron",
     "kwargs":{"hour":15, "minute":0},
     "callback":"JARVIS02_WRITER.scheduler.job_radar_pipeline_check",
     "misfire_grace_time":1800, "owner":"jarvis02_writer"},
    # ── JARVIS01 MASTER ────────────────────────────────────────
    {"id":"jarvis00_router_health", "name":"JARVIS01 라우터 헬스", "trigger":"cron",
     "kwargs":{"minute":0},
     "callback":"JARVIS01_MASTER.core_agent._job_router_health",
     "misfire_grace_time":600, "owner":"jarvis01_master"},
    # ── JARVIS07 자가 진단·수정 (★ 사용자 박제 2026-06-28 — 2단 분리) ──
    # 발행 직전(06:30 / 16:00 callback): Tier-1 LLM-0 자체수리 sweep 만 (수초, 발행 지연 0).
    #   callback: `run_self_repair_then_economic` / `run_self_repair_then_theme`.
    # 심층 LLM 감사(backlog Tier1→2 + 광범위 코드 감사): 새벽 04:30 `j07_deep_audit` 별도 cron.
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
    # j05_sla_monitor 비활성화 — SLA 경고 불필요 (2026-05-09 사용자 요청)
    # ── JARVIS00_INFRA ───────────────────────────────────────────
    # ★ 데몬 hang 워치독 신호 (ERRORS [318] — 2026-07-04): 스케줄러 스레드풀이
    #   *실제로 잡을 실행 중* 임을 60초마다 heartbeat 파일 mtime 으로 각인.
    #   06:07 hang 사고(메인스레드 무한 파이썬 루프 → GIL 기아 → 전 잡 정지)처럼
    #   PID 는 살아있어도 스케줄러가 멎으면 이 잡이 안 돌아 heartbeat stale →
    #   jarvis_keeper.py 워치독이 강제 재시작. interval 잡이라 스케줄러 기아 시
    #   동반 정지 = 정확한 hang 신호.
    {"id":"infra_heartbeat", "name":"데몬 heartbeat (keeper 워치독)", "trigger":"interval",
     "kwargs":{"seconds":60}, "callback":"JARVIS00_INFRA.infra_agent.job_heartbeat",
     "misfire_grace_time":30, "owner":"jarvis00_infra"},
    {"id":"db_backup",       "name":"DB 백업",              "trigger":"cron",
     "kwargs":{"hour":3, "minute":0}, "callback":"JARVIS00_INFRA.infra_agent.job_db_backup",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    {"id":"ev_cleanup",      "name":"events 정리",          "trigger":"cron",
     "kwargs":{"day_of_week":"sun", "hour":3, "minute":30}, "callback":"JARVIS00_INFRA.infra_agent.job_cleanup_events",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    # ★ vision_agent_history 는 30초 주기 수집(에이전트당 1행)이라 events 보다 훨씬
    #   빨리 누적 — 방치 시 DB 팽창 → get_db() 지연 → keeper hang 오탐 (본 사고 원인).
    {"id":"vision_history_cleanup", "name":"VISION 이력 정리", "trigger":"cron",
     "kwargs":{"hour":3, "minute":15}, "callback":"JARVIS00_INFRA.infra_agent.job_cleanup_vision_history",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    {"id":"file_cleanup",    "name":"파일 정리",            "trigger":"cron",
     "kwargs":{"day_of_week":"mon", "week":"*/2", "hour":4, "minute":0},
     "callback":"JARVIS00_INFRA.infra_agent.job_file_cleanup",
     "misfire_grace_time":3600, "owner":"jarvis00_infra"},
    {"id":"fuse_hidden_cleanup", "name":".fuse_hidden 즉시 정리 (15분)", "trigger":"interval",
     "kwargs":{"minutes":15},
     "callback":"shared.file_cleanup.cleanup_fuse_hidden",
     "misfire_grace_time":300, "owner":"jarvis00_infra"},
    # ── JARVIS02 로그 모니터링 ──────────────────────────────────────
    {"id":"log_monitor_economic", "name":"경제 브리핑 로그 확인 (07:00)", "trigger":"cron",
     "kwargs":{"hour":7, "minute":0},
     "callback":"JARVIS02_WRITER.log_monitor.job_check_economic_result",
     "misfire_grace_time":1800, "owner":"jarvis02_writer"},
    {"id":"log_monitor_theme",     "name":"테마주 로그 확인 (16:30)", "trigger":"cron",
     "kwargs":{"hour":16, "minute":30},
     "callback":"JARVIS02_WRITER.log_monitor.job_check_theme_result",
     "misfire_grace_time":1800, "owner":"jarvis02_writer"},
    # ── JARVIS06 IMAGE — 인포그래픽 디자인 강화학습 (★ 사용자 박제 2026-07-05) ──
    # 매일 05:00 Claude 가 새 전문 디자인 레시피 1개 창작 → 게이트 통과분만 누적 → pro_templates 소비.
    # 오류학습과 동형: 검증된 자산만 생존 → 다양성·품질 복리 상승. (ERRORS [359])
    {"id":"j06_design_learn",   "name":"인포그래픽 디자인 학습 05:00 (하루 1개)", "trigger":"cron",
     "kwargs":{"hour":5, "minute":0},
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
    {"id":"j07_deep_audit",     "name":"GUARDIAN 심층 코드 감사 (매일 04:30)", "trigger":"cron",
     "kwargs":{"hour":4, "minute":30},
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
    #   daily_review(22:00)·learn_log(23:30) 이후 실행: 주입 인사이트 ↔ 분석 결과
    #   매칭 → 보상 계산 → weight EMA 갱신. LLM 호출 0 (순수 통계).
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
    for j in DEFAULT_JOBS:
        if j["id"] == job_id:
            return j.get("owner")
    return None


def register_default_jobs(scheduler: Any) -> int:
    """DEFAULT_JOBS 의 모든 잡을 APScheduler 에 등록.

    데몬 _start_scheduler() 가 1회 호출. 잡 ID 동일하므로 기존 동작 유지.
    executor='processpool' 인 잡은 별도 프로세스에서 실행 — 장애 격리.
    Returns: 등록한 잡 수.
    """
    n = 0
    for j in DEFAULT_JOBS:
        try:
            fn = _resolve_callback(j["callback"])
            exec_kwargs = {}
            if j.get("executor"):
                exec_kwargs["executor"] = j["executor"]
            scheduler.add_job(
                fn, j["trigger"], **j["kwargs"],
                id=j["id"], name=j["name"],
                misfire_grace_time=j.get("misfire_grace_time", 600),
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
