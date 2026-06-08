"""
JARVIS05_VISION/api_server.py — FastAPI REST API 서버 (포트 8505).

hub.py 및 외부 클라이언트가 SQLite 직접 읽는 대신 이 API 를 호출.
uvicorn 을 백그라운드 스레드로 실행 — 데몬 종료 시 함께 종료.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

import socket as _socket
import time as _time

log = logging.getLogger("jarvis.vision.api")

VISION_PORT = 8505
_server_thread: threading.Thread | None = None
_app = None  # FastAPI 앱 인스턴스 (lazy init)


def _port_in_use(port: int) -> bool:
    """해당 포트가 현재 사용 중인지 확인."""
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _build_app():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="JARVIS05 VISION API",
        description="모든 JARVIS 에이전트 메트릭 수집·제공 REST API",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ── 헬스 ────────────────────────────────────────────────
    @app.get("/api/health", tags=["system"])
    def health_check() -> dict:
        """VISION 자체 헬스 체크."""
        from JARVIS05_VISION.collector import get_summary
        summary = get_summary()
        return {"ok": True, "vision": "online", "summary": summary}

    # ── 전체 에이전트 ────────────────────────────────────────
    @app.get("/api/agents", tags=["agents"])
    def list_agents() -> list[dict]:
        """등록된 모든 에이전트 최신 상태 + 메트릭."""
        from JARVIS05_VISION.collector import get_latest_snapshot
        return get_latest_snapshot()

    # ── 특정 에이전트 ────────────────────────────────────────
    @app.get("/api/agents/{agent_id}", tags=["agents"])
    def get_agent(agent_id: str) -> dict:
        """특정 에이전트 최신 상태 + 메트릭."""
        from JARVIS05_VISION.collector import get_latest_snapshot
        snapshot = get_latest_snapshot()
        for a in snapshot:
            if a["agent_id"] == agent_id:
                return a
        return {"error": f"agent '{agent_id}' not found"}

    # ── 실시간 수집 트리거 ───────────────────────────────────
    @app.post("/api/collect", tags=["system"])
    def trigger_collect() -> dict:
        """즉시 수집 트리거 (테스트·강제 갱신용)."""
        from JARVIS05_VISION.collector import _collect_once
        results = _collect_once()
        online  = sum(1 for v in results.values() if v["status"] == "online")
        return {"ok": True, "collected": len(results), "online": online}

    # ── 시스템 KPI 요약 ──────────────────────────────────────
    @app.get("/api/metrics/summary", tags=["metrics"])
    def metrics_summary() -> dict:
        """시스템 전체 KPI."""
        from JARVIS05_VISION.collector import get_summary
        return get_summary()

    # ── 레지스트리 목록 ─────────────────────────────────────
    @app.get("/api/registry", tags=["system"])
    def list_registry() -> list[dict]:
        """레지스트리에 등록된 에이전트 매니페스트 목록."""
        from JARVIS05_VISION.registry import get_registry
        return [a.get_manifest() for a in get_registry().get_all()]

    # ── 스케줄러 잡 목록 ─────────────────────────────────────
    @app.get("/api/scheduler/jobs", tags=["scheduler"])
    def scheduler_jobs() -> list[dict]:
        """JARVIS04 APScheduler 잡 목록 + 다음 실행 시각."""
        try:
            from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
            from datetime import datetime as _dt
            aps = get_apscheduler()
            if not aps:
                return []
            jobs = []
            for j in aps.get_jobs():
                nrt = getattr(j, "next_run_time", None)
                jobs.append({
                    "id":       j.id,
                    "name":     j.name,
                    "next_run": nrt.strftime("%Y-%m-%d %H:%M") if nrt else None,
                    "trigger":  str(j.trigger),
                })
            return sorted(jobs, key=lambda x: x["next_run"] or "")
        except Exception as e:
            return [{"error": str(e)}]

    # ── 최근 잡 실행 이력 ────────────────────────────────────
    @app.get("/api/scheduler/history", tags=["scheduler"])
    def scheduler_history(hours: int = 24, limit: int = 50) -> list[dict]:
        """최근 N시간 잡 실행 이력."""
        from shared.db import get_db
        with get_db() as conn:
            rows = conn.execute(
                """SELECT job_id, job_name, started_at, duration_ms, success, error, owner_agent
                   FROM job_runs
                   WHERE started_at >= datetime('now','localtime',?)
                   ORDER BY started_at DESC LIMIT ?""",
                (f"-{hours} hours", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── 발행 현황 ────────────────────────────────────────────
    @app.get("/api/posts/summary", tags=["writer"])
    def posts_summary() -> dict[str, Any]:
        """오늘 발행 현황 + 플랫폼별 카운트."""
        from shared.db import get_post_summary
        return get_post_summary()

    # ── 트렌드 TOP ───────────────────────────────────────────
    @app.get("/api/radar/trends", tags=["radar"])
    def radar_trends(limit: int = 20) -> list[dict]:
        """오늘 날짜 트렌드 상위 키워드."""
        from shared.db import get_db
        from datetime import date
        today = date.today().isoformat()
        with get_db() as conn:
            rows = conn.execute(
                """SELECT keyword, sector, opportunity_score, score, source
                   FROM trends WHERE date=?
                   ORDER BY opportunity_score DESC LIMIT ?""",
                (today, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    return app


def start_api_server() -> None:
    global _server_thread, _app

    try:
        import uvicorn
        from fastapi import FastAPI  # noqa — 설치 확인
    except ImportError as e:
        log.warning(f"⚠️ FastAPI/uvicorn 미설치 — API 서버 비활성: {e}")
        _g_report("vision", e, module=__name__)
        return

    if _server_thread and _server_thread.is_alive():
        log.info("ℹ️  VISION API 서버 이미 실행 중")
        return

    _app = _build_app()

    def _run():
        # ★ ERRORS [275] 박제 2026-06-08 — port 충돌 시 uvicorn SystemExit(1) 발생
        # 이전 프로세스가 포트 해제하기까지 대기 후 재시도 (critical Guardian 경고 방지)
        import errno as _errno
        for _attempt in range(3):
            if _attempt > 0 and _port_in_use(VISION_PORT):
                log.warning(f"⚠️ 포트 {VISION_PORT} 여전히 사용 중 — {3}초 추가 대기 ({_attempt}/2)")
                _time.sleep(3)
            try:
                uvicorn.run(
                    _app,
                    host="127.0.0.1",
                    port=VISION_PORT,
                    log_level="warning",
                    access_log=False,
                )
                break
            except SystemExit:
                if _attempt < 2:
                    log.warning(f"⚠️ VISION API SystemExit — 포트 충돌 의심, 재시도 ({_attempt+1}/2)")
                    _time.sleep(3)
                else:
                    log.warning("⚠️ VISION API 서버 기동 포기 (재시도 소진)")
            except OSError as e:
                if e.errno == _errno.EADDRINUSE and _attempt < 2:
                    log.warning(f"⚠️ 포트 {VISION_PORT} 충돌 — {3}초 대기 후 재시도")
                    _time.sleep(3)
                else:
                    log.error(f"❌ VISION API 서버 오류: {e}")
                    _g_report("vision", e, module=__name__, func_name="start_api_server")
                    break
            except Exception as e:
                log.error(f"❌ VISION API 서버 오류: {e}")
                _g_report("vision", e, module=__name__, func_name="start_api_server")
                break

    _server_thread = threading.Thread(target=_run, daemon=True, name="VisionAPI")
    _server_thread.start()
    log.info(f"✅ VISION API 서버 시작 — http://127.0.0.1:{VISION_PORT}/docs")


def get_app():
    """hub.py 또는 테스트에서 직접 앱 인스턴스 접근용."""
    global _app
    if _app is None:
        _app = _build_app()
    return _app
