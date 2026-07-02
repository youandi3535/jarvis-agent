"""JARVIS04_SCHEDULER/job_controller.py — 잡 제어 (pause/resume/run_now/remove).

★ 모든 *변경* 작업은 APPROVAL 게이트 강제 — JARVIS01 ReAct 라우터가 호출 시
텔레그램 인라인 버튼 통과 후만 실행. 우회 금지.

NOTE:
- 데몬 재시작하면 메모리 잡스토어 → pause/remove 상태 *모두 초기화*. 영구 변경
  원하면 DEFAULT_JOBS 카탈로그 자체를 수정해야 함. 임시 변경은 세션 한정.
"""
from __future__ import annotations

from typing import Any

from JARVIS04_SCHEDULER.job_catalog import get_apscheduler


def pause_job(job_id: str) -> dict:
    sched = get_apscheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 미가용"}
    if sched.get_job(job_id) is None:   # ★ 대상 존재 확인 (2026-07-02)
        return {"ok": False, "error": f"job '{job_id}' 미존재", "job_id": job_id}
    try:
        sched.pause_job(job_id)
        # ★ 사후확인: 실제 paused(next_run=None) 반영됐는지
        j = sched.get_job(job_id)
        if j is not None and getattr(j, "next_run_time", None) is not None:
            return {"ok": False, "error": "pause 미반영(next_run 잔존)", "job_id": job_id}
        return {"ok": True, "job_id": job_id, "action": "paused"}
    except Exception as e:
        return {"ok": False, "error": str(e), "job_id": job_id}


def resume_job(job_id: str) -> dict:
    sched = get_apscheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 미가용"}
    if sched.get_job(job_id) is None:   # ★ 대상 존재 확인
        return {"ok": False, "error": f"job '{job_id}' 미존재", "job_id": job_id}
    try:
        sched.resume_job(job_id)
        return {"ok": True, "job_id": job_id, "action": "resumed"}
    except Exception as e:
        return {"ok": False, "error": str(e), "job_id": job_id}


def run_job_now(job_id: str) -> dict:
    """잡을 즉시 실행 — APScheduler 의 modify(next_run_time=now) 트릭 대신
    job.func() 직접 호출. listener 가 잡지 못할 수 있어 명시 로그."""
    sched = get_apscheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 미가용"}
    try:
        j = sched.get_job(job_id)
        if j is None:
            return {"ok": False, "error": f"job '{job_id}' 미존재"}
        # 별도 스레드에서 실행 — 텔레그램 봇 블록 방지
        import threading
        threading.Thread(target=j.func, daemon=True,
                         name=f"manual_{job_id}").start()
        return {"ok": True, "job_id": job_id, "action": "triggered"}
    except Exception as e:
        return {"ok": False, "error": str(e), "job_id": job_id}


def remove_job(job_id: str) -> dict:
    sched = get_apscheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 미가용"}
    if sched.get_job(job_id) is None:   # ★ 대상 존재 확인 (2026-07-02)
        return {"ok": False, "error": f"job '{job_id}' 미존재", "job_id": job_id}
    try:
        sched.remove_job(job_id)
        # ★ 사후확인: 실제 제거됐는지
        if sched.get_job(job_id) is not None:
            return {"ok": False, "error": "remove 미반영(잡 잔존)", "job_id": job_id}
        return {"ok": True, "job_id": job_id, "action": "removed",
                "note": "데몬 재시작 시 DEFAULT_JOBS 에 박혀 있으면 다시 등록됨"}
    except Exception as e:
        return {"ok": False, "error": str(e), "job_id": job_id}


__all__ = ["pause_job", "resume_job", "run_job_now", "remove_job"]
