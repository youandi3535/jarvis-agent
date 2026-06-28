"""JARVIS04_SCHEDULER/scheduler_agent.py — 진입점.

데몬 부팅 시 _autoregister_agents 가 register(scheduler, bus) 호출.
순서:
  1) capability declare
  2) APScheduler 인스턴스 등록 (job_catalog.set_apscheduler)
  3) EventListener attach (job_history.attach_listeners)
  4) DEFAULT_JOBS 등록 (job_registry.register_default_jobs) — 데몬에서 별도 호출도 가능
  5) JARVIS04 자체 cron 잡 (일일 브리핑) 등록
  6) 도구 9개 자동 등록 (모듈 import 시점에 @register_tool 트리거)

★ 강제 규정 (CLAUDE.md):
- 모든 APScheduler 잡은 *반드시* DEFAULT_JOBS (또는 자기 도메인 register()) 통해 등록.
- 변경 (pause/resume/run/remove) 작업은 *반드시* 텔레그램 인라인 버튼 승인.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.capabilities import declare
from shared.tools import register_tool


def _status_section() -> str:
    lines = ["🗓 *JARVIS04 — SCHEDULER*"]
    try:
        from JARVIS04_SCHEDULER.job_catalog import list_jobs, next_runs
        from JARVIS04_SCHEDULER.job_history import summarize_recent
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        all_jobs   = list_jobs()
        total_jobs = len(DEFAULT_JOBS)
        active_jobs = sum(1 for j in all_jobs if not j.get("paused"))
        paused_jobs = total_jobs - active_jobs
        pause_str = f"  |  ⏸ {paused_jobs}개 정지" if paused_jobs else ""
        lines.append(f"✅ 잡 {active_jobs}/{total_jobs}개 활성{pause_str}")
        upcoming = next_runs(limit=3)
        if upcoming:
            lines.append("⏭ 다음 실행:")
            for j in upcoming:
                nr   = j["next_run"]
                hhmm = nr[11:16] if nr else "?"
                lines.append(f"   {hhmm} {j['name']}")
        stats  = summarize_recent(hours=24)
        total_r = stats.get("total", 0)
        if total_r > 0:
            succ = stats.get("success", 0)
            fail = stats.get("fail", 0)
            fail_str = f"  |  ❌ 실패 {fail}건" if fail else ""
            lines.append(f"📊 24h: 총 {total_r}회 실행  |  ✅ {succ}건{fail_str}")
        else:
            lines.append("📊 24h: 실행 이력 없음")
    except Exception as e:
        lines.append(f"⚠️ 상태 조회 실패: {str(e)[:60]}")
    return "\n".join(lines)


# ── Capability 선언 ──────────────────────────────────────────
CAPABILITIES = declare(
    agent_id="jarvis04_scheduler",
    domain="schedule",
    intents=[
        # SAFE
        "schedule.job.list",         # 등록된 잡 카탈로그
        "schedule.job.next",         # 다음 실행 예정
        "schedule.history.query",    # 잡 실행 이력
        "schedule.report.daily",     # 일일 잡 종합 리포트
        # APPROVAL
        "schedule.job.pause",
        "schedule.job.resume",
        "schedule.job.run_now",
        "schedule.job.remove",
    ],
    tools=[
        # SAFE
        "list_scheduled_jobs", "get_next_runs",
        "get_job_history", "get_today_briefing",
        # APPROVAL
        "pause_scheduled_job", "resume_scheduled_job",
        "run_scheduled_job_now", "remove_scheduled_job",
    ],
    requires_approval=[
        "schedule.job.pause", "schedule.job.resume",
        "schedule.job.run_now", "schedule.job.remove",
    ],
    cost_class="low",
    description="모든 에이전트의 스케줄 잡 단일 진입점. 등록·조회·이력·제어.",
    tags=["scheduler", "ops", "jobs"],
    help_section=(
        "🗓 *잡 스케줄 (JARVIS04)*\n"
        "/jobs        전체 잡 목록\n"
        "/jobs\\_next  다음 실행 예정\n"
        "/jobs\\_log   최근 실행 이력 (기본 24h)\n"
        "/jobs\\_report 일일 잡 리포트\n"
        "※ 잡 변경은 자유 문장으로 → 인라인 버튼 ✅ 승인"
    ),
    status_fn=_status_section,
)


# ══════════════════════════════════════════════════════════════
# 도구 9개 — @register_tool (shared.tools)
# ══════════════════════════════════════════════════════════════

# ── SAFE ─────────────────────────────────────────────────────

@register_tool(
    name="list_scheduled_jobs",
    domain="schedule",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="모든 에이전트의 등록된 APScheduler 잡 카탈로그 — id·name·next_run·owner·trigger.",
)
def list_scheduled_jobs() -> dict:
    from JARVIS04_SCHEDULER.job_catalog import list_jobs
    jobs = list_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@register_tool(
    name="get_next_runs",
    domain="schedule",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="다음 실행 예정 잡 N개 (가까운 순). limit 기본 10.",
)
def get_next_runs(limit: int = 10) -> dict:
    from JARVIS04_SCHEDULER.job_catalog import next_runs
    jobs = next_runs(limit=limit)
    return {"jobs": jobs, "count": len(jobs)}


@register_tool(
    name="get_job_history",
    domain="schedule",
    side_effect="none",
    cost_class="low",
    requires_approval=False,
    description="잡 실행 이력 조회. job_id/owner_agent/success/since_hours 필터. limit 기본 20.",
)
def get_job_history(
    limit: int = 20,
    job_id: Optional[str] = None,
    owner_agent: Optional[str] = None,
    success: Optional[bool] = None,
    since_hours: Optional[int] = None,
) -> dict:
    from JARVIS04_SCHEDULER.job_history import query_runs
    runs = query_runs(
        limit=limit, job_id=job_id, owner_agent=owner_agent,
        success=success, since_hours=since_hours,
    )
    return {"runs": runs, "count": len(runs)}


@register_tool(
    name="get_today_briefing",
    domain="schedule",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="최근 24시간 잡 실행 종합 리포트 (성공·실패 통계 + 다음 5개 예정).",
)
def get_today_briefing(hours: int = 24) -> dict:
    from JARVIS04_SCHEDULER.briefing import build_briefing_text
    from JARVIS04_SCHEDULER.job_history import summarize_recent
    return {
        "text": build_briefing_text(hours),
        "summary": summarize_recent(hours),
    }


# ── APPROVAL ─────────────────────────────────────────────────

@register_tool(
    name="pause_scheduled_job",
    domain="schedule",
    side_effect="internal",
    cost_class="free",
    requires_approval=True,
    description="잡 일시정지 — APScheduler pause_job. 데몬 재시작 시 초기화.",
)
def pause_scheduled_job(job_id: str) -> dict:
    from JARVIS04_SCHEDULER.job_controller import pause_job
    return pause_job(job_id)


@register_tool(
    name="resume_scheduled_job",
    domain="schedule",
    side_effect="internal",
    cost_class="free",
    requires_approval=True,
    description="잡 재개 — APScheduler resume_job.",
)
def resume_scheduled_job(job_id: str) -> dict:
    from JARVIS04_SCHEDULER.job_controller import resume_job
    return resume_job(job_id)


@register_tool(
    name="run_scheduled_job_now",
    domain="schedule",
    side_effect="internal",
    cost_class="medium",
    requires_approval=True,
    description="잡 즉시 실행 — 별도 스레드. 외부 발행·분석 잡은 트리거 자체에 비용·영향 큼.",
)
def run_scheduled_job_now(job_id: str) -> dict:
    from JARVIS04_SCHEDULER.job_controller import run_job_now
    return run_job_now(job_id)


@register_tool(
    name="remove_scheduled_job",
    domain="schedule",
    side_effect="internal",
    cost_class="free",
    requires_approval=True,
    description="잡 제거 — APScheduler remove_job. 데몬 재시작 시 DEFAULT_JOBS 의 잡은 다시 등록됨.",
)
def remove_scheduled_job(job_id: str) -> dict:
    from JARVIS04_SCHEDULER.job_controller import remove_job
    return remove_job(job_id)


# ══════════════════════════════════════════════════════════════
# 데몬 부팅 진입점
# ══════════════════════════════════════════════════════════════


def ensure_loaded() -> list[str]:
    """등록된 8개 도구 검증 — 누락 시 명시 경고 (이전: 교집합만 반환).

    expected vs names 차이:
      - missing: expected 에 있는데 names 에 없음 → ⚠️ 명시 경고
      - extra:   names 에 있는데 expected 외 → 정보성 (다른 모듈 등록)
    """
    from shared.tools import all_tools
    expected = {
        "list_scheduled_jobs", "get_next_runs",
        "get_job_history", "get_today_briefing",
        "pause_scheduled_job", "resume_scheduled_job",
        "run_scheduled_job_now", "remove_scheduled_job",
    }
    names = {t.name for t in all_tools()}
    loaded = expected & names
    missing = expected - names
    if missing:
        print(f"  ❌ JARVIS04 도구 등록 누락: {sorted(missing)} "
              f"— scheduler_agent.py import 또는 @register_tool 데코레이터 점검 필요")
    return sorted(loaded)


def register(scheduler: Any, bus: Any) -> None:
    """데몬 자동등록 진입점.

    1) APScheduler 인스턴스 등록 (단일 진입점)
    2) EventListener attach (job_runs 자동 적재)
    3) DEFAULT_JOBS 등록 (데몬의 16개 잡 이관)
    4) JARVIS04 자체 일일 브리핑 잡
    """
    from JARVIS04_SCHEDULER.job_catalog import set_apscheduler
    from JARVIS04_SCHEDULER.job_history import attach_listeners
    from JARVIS04_SCHEDULER.job_registry import (
        register_default_jobs, render_default_summary,
    )

    set_apscheduler(scheduler)
    ok = attach_listeners(scheduler)
    print(f"  📚 JARVIS04 EventListener attach: {'OK' if ok else 'FAIL'}")

    n = register_default_jobs(scheduler)
    print(f"  📅 JARVIS04 default 잡 등록: {n}개 (이전 데몬 직박이 → JARVIS04 이관)")
    print(render_default_summary())

    # 도구 등록 검증
    loaded = ensure_loaded()
    print(f"  🔧 JARVIS04 도구 등록: {len(loaded)}개")


__all__ = [
    "CAPABILITIES", "register", "ensure_loaded",
    # SAFE 도구
    "list_scheduled_jobs", "get_next_runs",
    "get_job_history", "get_today_briefing",
    # APPROVAL 도구
    "pause_scheduled_job", "resume_scheduled_job",
    "run_scheduled_job_now", "remove_scheduled_job",
]
