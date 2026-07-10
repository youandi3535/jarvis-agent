"""JARVIS04_SCHEDULER/job_catalog.py — APScheduler 인스턴스 wrap + 잡 메타 조회.

★ 데몬이 부팅 시 set_apscheduler() 로 인스턴스를 등록 → JARVIS04 가 그것으로
모든 잡 조회·제어. 다른 폴더는 이 모듈을 통해서만 scheduler 접근 (단일 진입점).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_APSCHED: Any = None  # BackgroundScheduler 인스턴스 (데몬이 set)

# processpool worker 부팅 시 1회 실행 — macOS spawn 환경에서 sys.path + .env 보장
def _worker_init() -> None:
    root = str(Path(__file__).parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(root) / ".env", override=False)
    except Exception:
        pass


def create_scheduler(timezone: str = "Asia/Seoul") -> Any:
    """APScheduler BackgroundScheduler 인스턴스 생성 — *단일 진입점*.

    ★ 데몬을 포함한 모든 호출자가 이 함수만 사용. 다른 위치에서
    `BackgroundScheduler(...)` 직접 생성 금지 (CLAUDE.md 강제 규정).

    executor 구성:
      - default: ThreadPoolExecutor(10) — 경량 잡 (대부분)
      - processpool: ProcessPoolExecutor(2) — 중량 잡 (학습·리뷰 등)
        job_registry 에서 executor='processpool' 로 지정한 잡만 해당.
        프로세스 격리 → 중량 잡 OOM/패닉이 데몬·봇에 전파되지 않음.

    Returns: BackgroundScheduler 인스턴스. apscheduler 미설치 시 None.
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.executors.pool import (
            ThreadPoolExecutor as _TPE,
            ProcessPoolExecutor as _PPE,
        )
    except ImportError:
        return None

    executors = {
        "default": _TPE(10),
        "processpool": _PPE(2, pool_kwargs={"initializer": _worker_init}),
    }
    sched = BackgroundScheduler(executors=executors, timezone=timezone)
    set_apscheduler(sched)
    return sched


def set_apscheduler(scheduler: Any) -> None:
    """데몬 부팅 시 1회 호출 — APScheduler 인스턴스 등록."""
    global _APSCHED
    _APSCHED = scheduler


def get_apscheduler() -> Any:
    return _APSCHED


def list_jobs() -> list[dict]:
    """현재 등록된 모든 잡 메타 (next_run_time 포함)."""
    out: list[dict] = []
    if _APSCHED is None:
        return out
    try:
        from JARVIS04_SCHEDULER.job_registry import get_owner
        for j in _APSCHED.get_jobs():
            nr = getattr(j, "next_run_time", None)
            paused = nr is None
            out.append({
                "id": j.id,
                "name": j.name or "",
                "next_run": nr.strftime("%Y-%m-%d %H:%M:%S") if nr else None,
                "paused": paused,
                "trigger": str(j.trigger),
                "owner": get_owner(j.id) or "",
            })
        # next_run 가까운 순 정렬 (paused=None 은 끝으로)
        out.sort(key=lambda r: (r["next_run"] is None, r["next_run"] or ""))
    except Exception as e:
        print(f"  ⚠️ JARVIS04 list_jobs 실패: {e}")
        _g_report("scheduler", e, module=__name__)
    return out


def next_runs(limit: int = 10) -> list[dict]:
    """가장 가까운 N개 잡."""
    jobs = [j for j in list_jobs() if j["next_run"]]
    return jobs[:max(1, min(int(limit or 10), 50))]


__all__ = [
    "create_scheduler",
    "set_apscheduler", "get_apscheduler",
    "list_jobs", "next_runs",
]
