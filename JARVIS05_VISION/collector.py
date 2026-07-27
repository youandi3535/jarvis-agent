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
_prev_loaded = False


def _load_prev_status() -> None:
    """부팅 시 직전 상태를 **DB 에서 복원** (2026-07-27).

    ★ 왜: `_prev_status` 는 메모리라 데몬을 재시작하면 비어 있었다. 그러면
      재시작 *직전* 에 죽어 있던 에이전트가 재시작 후 첫 수집에서 `prev=None` 이 되어
      **상태 변화로 인식되지 않고**, 알림도 이력도 남지 않았다(무증상 누락).
    ★ 어디서 복원하나: `vision_agent_status`(에이전트당 1행, 항상 최신) — 이력 테이블이
      아니라 여기다. 이력은 이제 *변화만* 담으므로 "가장 최근 상태" 의 진실은 status 다.
    """
    global _prev_loaded
    if _prev_loaded:
        return
    _prev_loaded = True
    try:
        from shared.db import get_db
        with get_db() as conn:
            for r in conn.execute("SELECT agent_id, status FROM vision_agent_status"):
                if r[0] and r[1]:
                    _prev_status[str(r[0])] = str(r[1])
        if _prev_status:
            log.info(f"[VISION] 직전 상태 복원 {len(_prev_status)}종 — 재시작 직후 변화 감지 유지")
    except Exception as e:                                  # noqa: BLE001
        log.warning(f"[VISION] 직전 상태 복원 실패(첫 수집은 변화 미감지): {e}")

# ── 텔레그램 알림 ──────────────────────────────────────────────────
# (실제 전송은 shared.notify.send_tg 단일 진입점 — raw TELEGRAM_* 상수 직접참조 제거: 전수감사 DELETE[13])


def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg)
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

    _load_prev_status()   # ★ 첫 수집 전 1회 — 재시작 직후 변화 감지 유지 (내부에서 멱등)

    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j05", "에이전트 헬스 수집", ttl=120)
    except Exception:
        pass

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
            # ── 상태 변화 감지 → 이력 적재 + 텔레그램 알림 (2026-07-27 개편) ──
            #   ★ 종전엔 **매 수집마다** history 를 append 했다(30초 × 10에이전트 =
            #     하루 28,800행). 실측 182,437행으로 **DB 최대 테이블** 이 됐는데,
            #     그 이력을 읽는 코드는 **하나도 없었다**(`get_history()` 호출자 0).
            #     같은 상태를 30초마다 반복 기록한 것이라 정보량은 변화 시점과 동일하다.
            #   ★ 이제 **상태가 바뀐 순간만** 적재한다 — "언제 죽었고 언제 살아났나" 는
            #     그대로 답할 수 있고(오히려 대시보드 차트가 생겼다), 양은 1/1000 이 된다.
            #     그래서 보존기간도 7일 → 30일로 *늘렸다*(shared/db.RETENTION).
            #   ★ 폴링 주기(30초)는 **그대로** — 그건 '장애를 얼마나 빨리 아느냐' 의 값이고
            #     이력 촘촘함과 별개다. 5분으로 늘리면 발행 중 장애를 5분간 모른다.
            prev = _prev_status.get(aid)
            if prev != status:                    # 첫 관측(prev=None) 도 기록 — 시작점이 있어야 구간이 그려진다
                try:
                    with get_db() as conn:
                        conn.execute(
                            """INSERT INTO vision_agent_history
                               (agent_id, agent_name, status, message, metrics_json, recorded_at)
                               VALUES (?,?,?,?,?,?)""",
                            (aid, agent_name, status, message, metrics_str, now),
                        )
                except Exception as e:            # noqa: BLE001
                    log.warning(f"  ⚠️ [{aid}] 이력 적재 실패: {e}")
                if prev is not None:
                    _alert_status_change(agent_name, prev, status, message)
            _prev_status[aid] = status

        except Exception as e:
            log.warning(f"  ⚠️ [{aid}] DB 저장 실패: {e}")
            _g_report("vision", e, module=__name__)

    online  = sum(1 for v in results.values() if v["status"] == "online")
    offline = sum(1 for v in results.values() if v["status"] == "offline")
    log.debug(f"[VISION] 수집 완료 — online:{online} offline:{offline} total:{len(agents)}")
    try:
        from shared.pipeline_activity import clear_busy as _cb
        _cb("j05")
    except Exception:
        pass
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

def get_status_timeline(days: int | None = None) -> dict:
    """에이전트별 **상태 구간(segment)** 타임라인 — 대시보드 차트의 단일 데이터 소스.

    ★ 왜 구간으로 주나 (① 단일 진입점): 이력은 *변화 시점* 만 담는다. 화면이 그것을
      받아 "언제부터 언제까지 무슨 상태" 로 조립하면 **그 조립 규칙이 UI 로 새어나간다**
      (다른 화면·텔레그램이 같은 걸 그리려면 규칙을 복제해야 함). 조립은 여기서 끝낸다.

    ★ 기간(days)을 박지 않는다 (② 동적 설계): 기본값은 `shared/db.RETENTION` 의
      실제 보존일수에서 파생한다 — 보존을 늘리면 차트도 자동으로 길어진다.
      보관하지 않는 구간을 그려봐야 빈 칸이고, 보관하는데 안 그리면 낭비다.

    ★ 위치·너비(%)까지 여기서 계산한다 (① 단일 진입점 연장): 화면이 시각 문자열을 받아
      좌표로 환산하면 그 환산식이 UI 마다 복제된다. 화면은 받은 %로 막대만 그린다.

    ★ 관측 시작 이전은 `observed_start` 로 알린다: 이력이 보존기간보다 짧으면
      (예: 보존 30일인데 데이터는 7일치) 그 앞을 online 으로 칠하는 것은 **거짓**이다.
      화면은 이 값 앞을 '관측 없음' 으로 비운다.

    Returns:
        {"days","generated_at","window_start","window_end","window_minutes",
         "agents":[{"agent_id","agent_name","current","uptime_pct","incidents",
                    "observed_start","observed_pct",
                    "segments":[{"status","start","end","minutes","message",
                                 "left_pct","width_pct"}]}]}
    """
    from shared.db import get_db, retention_days
    from datetime import datetime as _dt, timedelta as _td

    if days is None:
        days = retention_days("vision_agent_history") or 30
    now = _dt.now()
    w_start = now - _td(days=days)
    since = w_start.strftime("%Y-%m-%d %H:%M:%S")
    w_min = max(1.0, (now - w_start).total_seconds() / 60)
    out: list[dict] = []

    def _pct(t: _dt) -> float:
        """창 시작으로부터의 위치(%) — 0~100 로 clamp."""
        return min(100.0, max(0.0, (t - w_start).total_seconds() / 60 / w_min * 100))

    try:
        with get_db() as conn:
            cur = {str(r[0]): {"name": (r[1] or r[0]), "status": (r[2] or "unknown")}
                   for r in conn.execute(
                       "SELECT agent_id, agent_name, status FROM vision_agent_status")}
            rows = conn.execute(
                """SELECT agent_id, agent_name, status, message, recorded_at
                   FROM vision_agent_history
                   WHERE recorded_at >= ?
                   ORDER BY agent_id, recorded_at""", (since,)).fetchall()
        by_agent: dict[str, list] = {}
        for r in rows:
            by_agent.setdefault(str(r[0]), []).append(dict(r))

        for aid in sorted(set(list(by_agent.keys()) + list(cur.keys()))):
            evs = by_agent.get(aid, [])
            segs: list[dict] = []
            for i, e in enumerate(evs):
                s_raw = str(e["recorded_at"])
                e_raw = str(evs[i + 1]["recorded_at"]) if i + 1 < len(evs) else now.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    t0, t1 = _dt.fromisoformat(s_raw), _dt.fromisoformat(e_raw)
                except Exception:                           # noqa: BLE001
                    continue
                left, right = _pct(t0), _pct(t1)
                segs.append({
                    "status": e["status"], "start": s_raw, "end": e_raw,
                    "minutes": max(0, int((t1 - t0).total_seconds() // 60)),
                    "message": (e.get("message") or "")[:120],
                    "left_pct": round(left, 3),
                    # ★ 0분 구간도 보이게 최소 폭 — 짧은 장애일수록 봐야 할 것이다
                    "width_pct": round(max(right - left, 0.15), 3),
                })
            total = sum(s["minutes"] for s in segs)
            up = sum(s["minutes"] for s in segs if s["status"] == "online")
            obs = segs[0]["start"] if segs else None
            out.append({
                "agent_id":       aid,
                "agent_name":     cur.get(aid, {}).get("name") or (evs[0]["agent_name"] if evs else aid),
                "segments":       segs,
                # 현재 상태의 진실은 status 표 — 이력 마지막이 아니다 (변화만 적재하므로)
                "current":        cur.get(aid, {}).get("status") or (segs[-1]["status"] if segs else "unknown"),
                "uptime_pct":     round(100 * up / total, 1) if total else None,
                "incidents":      sum(1 for s in segs if s["status"] != "online"),
                "observed_start": obs,
                "observed_pct":   round(segs[0]["left_pct"], 3) if segs else 100.0,
            })
    except Exception as e:                                  # noqa: BLE001
        log.warning(f"[VISION] 타임라인 조회 실패: {e}")
        _g_report("vision", e, module=__name__)
    return {
        "days": days,
        "generated_at":   now.isoformat(timespec="seconds"),
        "window_start":   w_start.isoformat(timespec="seconds"),
        "window_end":     now.isoformat(timespec="seconds"),
        "window_minutes": int(w_min),
        "agents": out,
    }


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
