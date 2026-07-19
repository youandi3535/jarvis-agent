"""JARVIS04_SCHEDULER/job_history.py — APScheduler 잡 실행 이력 적재·조회.

★ APScheduler EventListener 가 모든 잡 실행을 자동으로 job_runs 테이블에 기록.
EVENT_JOB_EXECUTED (성공) / EVENT_JOB_ERROR (실패) 둘 다 캐치.

기록 항목:
  job_id, job_name, started_at, finished_at, duration_ms,
  success, error, scheduled_run_time, owner_agent

NOTE: JARVIS02 의 schedule 라이브러리 기반 잡 (legacy) 은 APScheduler 가 아니라
이 listener 가 잡지 못함. 그건 events 테이블의 post_published 이벤트로 우회 추적.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from shared import db


# ── EventListener (APScheduler EVENT_JOB_EXECUTED / EVENT_JOB_ERROR) ──

# 잡 시작 시각 추적 (event 만으로 duration 계산 어려움 → 별도 메모리)
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_job_start_ts: dict[str, float] = {}

# ★ EventListener 이중 부착 방어 (전수감사 2026-07-17): attach_listeners 가 정상 부팅에서
#   jarvis_daemon.py(직접) + scheduler_agent.register()(autoregister) 두 경로로 호출돼
#   리스너가 100% 이중 부착 → job_runs 2행 적재·GUARDIAN 2중 보고로 통계·자가학습 지문 오염.
#   같은 scheduler 인스턴스면 1회만 부착(단일 데몬이라 id() 키 안정). 데몬 재시작 시 모듈
#   재로드로 자동 리셋. CLAUDE_SCHEDULER.md '단일 부착·중복 금지' 준수.
_ATTACHED: set[int] = set()


# ── 잡 → 파이프라인 엣지 매핑 — job_registry.DEFAULT_JOBS 에서 자동 파생 ──
# 새 잡에 edges 필드를 추가하면 이 매핑도 자동 갱신됨 (하드코딩 금지).
def _build_job_edges() -> dict:
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        return {j["id"]: j["edges"] for j in DEFAULT_JOBS if j.get("edges")}
    except Exception:
        return {}

_JOB_EDGES: dict = _build_job_edges()


def _owner_agent(job_id: str) -> "str | None":
    """잡 owner(예: 'jarvis07_guardian') → 에이전트 id('j07'). 작업 주체 노드 busy 표시용."""
    try:
        from JARVIS04_SCHEDULER.job_registry import get_owner
        import re as _re
        m = _re.search(r'jarvis(\d\d)', str(get_owner(job_id) or ""), _re.I)
        return f"j{m.group(1)}" if m else None
    except Exception:
        return None


def _on_job_submitted(event):
    """EVENT_JOB_SUBMITTED — 잡 시작 직전. start ts 기록 + 파이프라인 활동 표시."""
    try:
        _job_start_ts[event.job_id] = time.time()
    except Exception:
        pass
    try:
        from shared.pipeline_activity import mark_active, mark_busy
        # infra_heartbeat → e11(J00→J01): 데몬이 살아있음을 J01에 60초마다 알림
        # 그 외 잡은 job_registry edges 필드에 정의된 엣지만 발화
        edges = ["e11"] if event.job_id == "infra_heartbeat" else (_JOB_EDGES.get(event.job_id) or [])
        if edges:
            mark_active(edges)
        # ★ 작업 주체(owner) 에이전트 busy 모션 (사용자 박제 2026-07-19): 잡을 실제 수행하는
        #   에이전트가 '작업 중' 표시 (예: j07_retry_pending 10분 → J07 자체 작동 모션).
        #   owner 불명 시 J04(스케줄러). 완료 시 clear 하지 않고 ttl 로 최소 표시 보장 →
        #   짧은 잡(수초)도 대시보드 2초 폴링에 반드시 포착됨.
        if event.job_id != "infra_heartbeat":
            _worker = _owner_agent(event.job_id) or "j04"
            mark_busy(_worker, f"잡 실행: {event.job_id}", ttl=40)
    except Exception:
        pass


def _on_job_executed(event):
    """EVENT_JOB_EXECUTED — 잡 성공 완료. busy 는 ttl 로 자연 만료 (짧은 잡 표시 보장)."""
    _record(event, success=True, error=None)


def _on_job_error(event):
    """EVENT_JOB_ERROR — 잡 예외."""
    err = ""
    try:
        if getattr(event, "exception", None) is not None:
            err = f"{type(event.exception).__name__}: {event.exception}"[:500]
    except Exception:
        pass
    _record(event, success=False, error=err or "unknown error")


def _record(event, success: bool, error: Optional[str]):
    from JARVIS04_SCHEDULER.job_registry import get_owner

    job_id = getattr(event, "job_id", "")
    if not job_id:
        return
    started = _job_start_ts.pop(job_id, None)
    finished_ts = time.time()
    duration_ms = int((finished_ts - started) * 1000) if started else None
    started_iso = (
        datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S")
        if started else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    finished_iso = datetime.fromtimestamp(finished_ts).strftime("%Y-%m-%d %H:%M:%S")
    sched_run = ""
    try:
        srt = getattr(event, "scheduled_run_time", None)
        if srt is not None:
            sched_run = srt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    # job_name 은 scheduler 에서 lookup
    job_name = ""
    try:
        from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
        sched = get_apscheduler()
        if sched is not None:
            j = sched.get_job(job_id)
            if j is not None:
                job_name = j.name or ""
    except Exception:
        pass

    try:
        with db.get_db() as conn:
            conn.execute(
                """INSERT INTO job_runs
                   (job_id, job_name, started_at, finished_at, duration_ms,
                    success, error, scheduled_run_time, owner_agent)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (job_id, job_name, started_iso, finished_iso, duration_ms,
                 1 if success else 0, error, sched_run, get_owner(job_id) or ""),
            )
    except Exception as e:
        print(f"  ⚠️ JARVIS04 job_runs 적재 실패 {job_id}: {e}")
        _g_report("scheduler", e, module=__name__)


def attach_listeners(scheduler: Any) -> bool:
    """APScheduler 에 listener 부착. 데몬 부팅 시 1회 호출.

    - job_history 3종 (submitted/executed/error)
    - GUARDIAN error collector (EVENT_JOB_ERROR 공유, apscheduler import 단일 진입점 유지)

    ★ idempotent: 같은 scheduler 에 이미 부착됐으면 즉시 True (이중 부착 방지).
    """
    if id(scheduler) in _ATTACHED:
        return True
    try:
        from apscheduler.events import (
            EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR,
        )
        scheduler.add_listener(_on_job_submitted, EVENT_JOB_SUBMITTED)
        scheduler.add_listener(_on_job_executed, EVENT_JOB_EXECUTED)
        scheduler.add_listener(_on_job_error,    EVENT_JOB_ERROR)

        # GUARDIAN 잡 실패 수집 리스너 — apscheduler import는 JARVIS04에서만
        try:
            from JARVIS07_GUARDIAN.error_collector import make_scheduler_listener
            g_listener = make_scheduler_listener()
            scheduler.add_listener(g_listener, EVENT_JOB_ERROR)
        except ImportError:
            pass

        _ATTACHED.add(id(scheduler))
        return True
    except Exception as e:
        print(f"  ⚠️ JARVIS04 listener attach 실패: {e}")
        _g_report("scheduler", e, module=__name__)
        return False


# ── 조회 ───────────────────────────────────────────────────────

def query_runs(
    limit: int = 20,
    job_id: Optional[str] = None,
    owner_agent: Optional[str] = None,
    success: Optional[bool] = None,
    since_hours: Optional[int] = None,
) -> list[dict]:
    """job_runs 필터 조회.

    Args:
        limit: 최대 행 수 (1~200, 기본 20).
        job_id: 정확 매칭.
        owner_agent: 정확 매칭 (예: "jarvis03_radar").
        success: True (성공만) / False (실패만) / None (전체).
        since_hours: 최근 N 시간 (started_at >= now - N).
    """
    limit = max(1, min(int(limit or 20), 200))
    where = []
    args: list[Any] = []
    if job_id:
        where.append("job_id = ?")
        args.append(job_id)
    if owner_agent:
        where.append("owner_agent = ?")
        args.append(owner_agent)
    if success is True:
        where.append("success = 1")
    elif success is False:
        where.append("success = 0")
    if since_hours is not None and since_hours > 0:
        where.append("started_at >= datetime('now','localtime', ?)")
        args.append(f"-{int(since_hours)} hours")
    sql = (
        "SELECT id, job_id, job_name, started_at, finished_at, duration_ms, "
        "success, error, scheduled_run_time, owner_agent "
        "FROM job_runs"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    rows: list[dict] = []
    try:
        with db.get_db() as conn:
            for r in conn.execute(sql, args).fetchall():
                rows.append(dict(r))
    except Exception as e:
        print(f"  ⚠️ JARVIS04 job_runs 조회 실패: {e}")
        _g_report("scheduler", e, module=__name__)
    return rows


def summarize_recent(hours: int = 24) -> dict:
    """최근 N 시간 통계 — 일일 브리핑용."""
    out = {"hours": hours, "total": 0, "success": 0, "fail": 0,
           "by_owner": {}, "fail_jobs": []}
    try:
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c, SUM(success) s "
                "FROM job_runs WHERE started_at >= datetime('now','localtime', ?)",
                (f"-{int(hours)} hours",),
            ).fetchone()
            out["total"] = int(row["c"] or 0)
            out["success"] = int(row["s"] or 0)
            out["fail"] = out["total"] - out["success"]

            for r in conn.execute(
                "SELECT owner_agent, COUNT(*) c, SUM(success) s "
                "FROM job_runs WHERE started_at >= datetime('now','localtime', ?) "
                "GROUP BY owner_agent",
                (f"-{int(hours)} hours",),
            ).fetchall():
                out["by_owner"][r["owner_agent"] or "?"] = {
                    "total": int(r["c"] or 0),
                    "success": int(r["s"] or 0),
                }

            for r in conn.execute(
                "SELECT job_id, job_name, started_at, error "
                "FROM job_runs WHERE success=0 "
                "AND started_at >= datetime('now','localtime', ?) "
                "ORDER BY id DESC LIMIT 10",
                (f"-{int(hours)} hours",),
            ).fetchall():
                out["fail_jobs"].append(dict(r))
    except Exception as e:
        out["error"] = str(e)
    return out


__all__ = [
    "attach_listeners",
    "query_runs", "summarize_recent",
]
