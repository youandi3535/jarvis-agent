"""
JARVIS05_VISION/collector.py — 30초 주기 메트릭 수집기.

- 전체 등록 에이전트 get_health() / get_metrics() 폴링
- vision_agent_status (최신 1건 upsert) + vision_agent_history (이력 append)
- 상태 변화(online↔offline↔warn) 감지 → 텔레그램 즉시 알림
- 에이전트 1개 실패가 전체 수집을 막지 않도록 per-agent 예외 격리
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

import requests as _req

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis.vision.collector")

_stop_event = threading.Event()
_collector_thread: threading.Thread | None = None

COLLECT_INTERVAL = 30  # 초

# 이전 수집 상태 캐시 — 상태 변화 감지용
_prev_status: dict[str, str] = {}

# ── 텔레그램 알림 ──────────────────────────────────────────────────

_TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _tg(msg: str) -> None:
    if not (_TG_TOKEN and _TG_CHAT_ID):
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT_ID, "text": msg},
            timeout=8,
        )
    except Exception:
        pass


def _alert_status_change(agent_name: str, prev: str, curr: str, message: str) -> None:
    """상태 전환 텔레그램 알림."""
    emoji = {"online": "✅", "warn": "⚠️", "offline": "❌"}.get(curr, "❓")
    prev_label = {"online": "정상", "warn": "경고", "offline": "오프라인"}.get(prev, prev)
    curr_label = {"online": "정상", "warn": "경고", "offline": "오프라인"}.get(curr, curr)
    _tg(
        f"{emoji} [VISION] {agent_name}\n"
        f"{prev_label} → {curr_label}\n"
        f"{message or '상태 변화 감지'}"
    )


# ── 수집 핵심 ────────────────────────────────────────────────────

def _collect_once() -> dict:
    """전체 에이전트 1회 수집. 결과 요약 반환."""
    global _prev_status

    from JARVIS05_VISION.registry import get_registry
    from shared.db import get_db

    registry = get_registry()
    agents   = registry.get_all()
    results  = {}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for agent in agents:
        aid = agent.agent_id
        try:
            health  = agent.get_health()
            metrics = agent.get_metrics()
            status  = health.get("status", "offline")
            message = health.get("message", "")
            results[aid] = {"status": status, "ok": True}
        except Exception as e:
            log.warning(f"  ⚠️ [{aid}] 수집 실패: {e}")
            _g_report("vision", e, module=__name__)
            status   = "offline"
            message  = f"수집 오류: {e}"
            metrics  = {}
            results[aid] = {"status": status, "ok": False}

        try:
            manifest    = agent.get_manifest()
            agent_name  = manifest.get("agent_name", aid)
            metrics_str = json.dumps(metrics, ensure_ascii=False, default=str)

            with get_db() as conn:
                # 최신 상태 upsert
                conn.execute(
                    """INSERT INTO vision_agent_status
                       (agent_id, agent_name, agent_domain, status, message,
                        metrics_json, last_seen)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(agent_id) DO UPDATE SET
                         agent_name   = excluded.agent_name,
                         agent_domain = excluded.agent_domain,
                         status       = excluded.status,
                         message      = excluded.message,
                         metrics_json = excluded.metrics_json,
                         last_seen    = excluded.last_seen""",
                    (
                        aid,
                        agent_name,
                        manifest.get("agent_domain", ""),
                        status,
                        message,
                        metrics_str,
                        now,
                    ),
                )
                # 히스토리 append (매 수집마다)
                conn.execute(
                    """INSERT INTO vision_agent_history
                       (agent_id, agent_name, status, message, metrics_json, recorded_at)
                       VALUES (?,?,?,?,?,?)""",
                    (aid, agent_name, status, message, metrics_str, now),
                )

            # 상태 변화 감지 → 텔레그램 알림
            prev = _prev_status.get(aid)
            if prev is not None and prev != status:
                _alert_status_change(agent_name, prev, status, message)
            _prev_status[aid] = status

        except Exception as e:
            log.warning(f"  ⚠️ [{aid}] DB 저장 실패: {e}")
            _g_report("vision", e, module=__name__)

    online  = sum(1 for v in results.values() if v["status"] == "online")
    offline = sum(1 for v in results.values() if v["status"] == "offline")
    log.debug(f"[VISION] 수집 완료 — online:{online} offline:{offline} total:{len(agents)}")
    return results


# ── 루프 / 시작 / 종료 ──────────────────────────────────────────

def _collector_loop() -> None:
    log.info("▶️  VISION Collector 시작 (30초 주기)")
    while not _stop_event.is_set():
        try:
            _collect_once()
        except Exception as e:
            log.error(f"[VISION] Collector 루프 오류: {e}")
            _g_report("vision", e, module=__name__)
        _stop_event.wait(timeout=COLLECT_INTERVAL)
    log.info("⏹  VISION Collector 종료")


def start_collector() -> None:
    global _collector_thread
    if _collector_thread and _collector_thread.is_alive():
        return
    _stop_event.clear()
    _collector_thread = threading.Thread(
        target=_collector_loop, daemon=True, name="VisionCollector"
    )
    _collector_thread.start()
    log.info("✅ VISION Collector 스레드 시작")


def stop_collector() -> None:
    _stop_event.set()
    if _collector_thread:
        _collector_thread.join(timeout=5)


# ── 조회 API ─────────────────────────────────────────────────────

def get_latest_snapshot() -> list[dict]:
    """vision_agent_status 전체 최신 상태 반환."""
    from shared.db import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT agent_id, agent_name, agent_domain, status, message,
                          metrics_json, last_seen
                   FROM vision_agent_status
                   ORDER BY agent_id"""
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
            except Exception:
                d["metrics"] = {}
            result.append(d)
        return result
    except Exception as e:
        log.warning(f"[VISION] snapshot 조회 실패: {e}")
        _g_report("vision", e, module=__name__)
        return []


def get_history(agent_id: str | None = None, hours: int = 24, limit: int = 200) -> list[dict]:
    """vision_agent_history 이력 조회."""
    from shared.db import get_db
    try:
        with get_db() as conn:
            if agent_id:
                rows = conn.execute(
                    """SELECT agent_id, agent_name, status, message, recorded_at
                       FROM vision_agent_history
                       WHERE agent_id=?
                         AND recorded_at >= datetime('now','localtime',?)
                       ORDER BY recorded_at DESC LIMIT ?""",
                    (agent_id, f"-{hours} hours", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT agent_id, agent_name, status, message, recorded_at
                       FROM vision_agent_history
                       WHERE recorded_at >= datetime('now','localtime',?)
                       ORDER BY recorded_at DESC LIMIT ?""",
                    (f"-{hours} hours", limit),
                ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"[VISION] history 조회 실패: {e}")
        _g_report("vision", e, module=__name__)
        return []


def get_summary() -> dict:
    """시스템 전체 KPI 요약."""
    snapshot = get_latest_snapshot()
    total   = len(snapshot)
    online  = sum(1 for a in snapshot if a["status"] == "online")
    warn    = sum(1 for a in snapshot if a["status"] == "warn")
    offline = total - online - warn
    return {
        "total":   total,
        "online":  online,
        "warn":    warn,
        "offline": offline,
        "health_pct": round(online / total * 100, 1) if total else 0,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
