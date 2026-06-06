"""
JARVIS05_VISION/registry.py — 에이전트 레지스트리 + 기존 에이전트 어댑터.

새 에이전트(BaseAgent 구현체)는 register() 로 등록.
기존 JARVIS00~04 는 어댑터로 감싸서 동일 인터페이스 제공.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List

from shared.agent_base import BaseAgent

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis.vision.registry")


# ════════════════════════════════════════════════════════════
# 기존 에이전트 어댑터 (JARVIS00~04)
# BaseAgent 없이도 동작 — 각 에이전트 모듈을 lazy import 해서 래핑
# ════════════════════════════════════════════════════════════

class _Infra00Adapter(BaseAgent):
    agent_id     = "jarvis00_infra"
    agent_name   = "JARVIS00 INFRA"
    agent_domain = "infra"

    def get_health(self) -> dict:
        try:
            import jarvis_daemon as _dm
            alive = _dm._streamlit_alive()
            disabled = _dm._st_disabled
            if disabled:
                return {"status": "warn", "message": "Streamlit 자동재시작 중단 (5회 실패)"}
            if alive:
                return {"status": "online", "message": f"데몬 정상 / 대시보드 port {_dm.ST_PORT}"}
            return {"status": "warn", "message": "대시보드 다운 — 재시작 시도 중"}
        except Exception as e:
            return {"status": "warn", "message": f"상태 조회 오류: {e}"}

    def get_metrics(self) -> dict:
        try:
            import os
            import jarvis_daemon as _dm
            delta = datetime.now() - _dm._daemon_start_time
            h, rem = divmod(int(delta.total_seconds()), 3600)
            m = rem // 60
            uptime = f"{h}h {m}m" if h else f"{m}m"
            return {
                "pid":            os.getpid(),
                "uptime":         uptime,
                "uptime_seconds": int(delta.total_seconds()),
                "streamlit_alive": _dm._streamlit_alive(),
                "st_port":        _dm.ST_PORT,
                "st_fail_count":  _dm._st_fail_count,
                "st_disabled":    _dm._st_disabled,
            }
        except Exception as e:
            return {"error": str(e)}


class _Master01Adapter(BaseAgent):
    agent_id     = "jarvis01_master"
    agent_name   = "JARVIS01 MASTER"
    agent_domain = "master"

    def get_health(self) -> dict:
        try:
            from JARVIS01_MASTER.router import get_graph
            graph_ok = get_graph() is not None
            status = "online" if graph_ok else "warn"
            msg = "LangGraph 라우터 정상" if graph_ok else "LangGraph 미초기화"
            return {"status": status, "message": msg}
        except Exception as e:
            return {"status": "warn", "message": f"라우터 로드 실패: {e}"}

    def get_metrics(self) -> dict:
        try:
            from shared import capabilities as _caps
            caps = _caps.all_capabilities()
            intents = _caps.list_intents()
            try:
                from shared import llm as _llm
                lc_ok = _llm.is_langchain_available()
            except Exception:
                lc_ok = False
            try:
                from shared.db import get_tool_stats
                tool_stats = get_tool_stats(hours=24)
                total_calls = sum(t.get("calls", 0) for t in tool_stats)
                avg_success = (
                    round(sum(t.get("success_rate", 0) for t in tool_stats) / len(tool_stats), 1)
                    if tool_stats else 0
                )
            except Exception:
                total_calls = 0
                avg_success = 0
            return {
                "capabilities":     len(caps),
                "intents":          len(intents),
                "langchain_ok":     lc_ok,
                "tool_calls_24h":   total_calls,
                "tool_success_rate": avg_success,
            }
        except Exception as e:
            return {"error": str(e)}


class _Writer02Adapter(BaseAgent):
    agent_id     = "jarvis02_writer"
    agent_name   = "JARVIS02 WRITER"
    agent_domain = "writer"

    def get_health(self) -> dict:
        try:
            import jarvis_daemon as _dm
            sched_ok = _dm._sched is not None
            status = "online" if sched_ok else "warn"
            msg = "스케줄러 로드 정상" if sched_ok else "스케줄러 미로드"
            return {"status": status, "message": msg}
        except Exception as e:
            return {"status": "warn", "message": f"상태 조회 실패: {e}"}

    def get_metrics(self) -> dict:
        try:
            from shared.db import get_db
            with get_db() as conn:
                today_posts = conn.execute(
                    "SELECT COUNT(*) FROM post_analysis WHERE date(created_at)=date('now','localtime')"
                ).fetchone()[0]
                pending_approval = conn.execute(
                    "SELECT COUNT(*) FROM post_analysis WHERE status='pending_approval'"
                ).fetchone()[0]
                pending_queue = conn.execute(
                    "SELECT COUNT(*) FROM pipeline WHERE status='suggested' AND date(created_at)=date('now','localtime')"
                ).fetchone()[0]
                platform_cnt = conn.execute(
                    """SELECT platform, COUNT(*) as cnt FROM post_analysis
                       WHERE date(created_at)=date('now','localtime')
                       GROUP BY platform"""
                ).fetchall()
            by_platform = {r["platform"]: r["cnt"] for r in platform_cnt}
            return {
                "today_posts":       today_posts,
                "pending_approval":  pending_approval,
                "pending_queue":     pending_queue,
                "by_platform":       by_platform,
            }
        except Exception as e:
            return {"error": str(e)}


class _Radar03Adapter(BaseAgent):
    agent_id     = "jarvis03_radar"
    agent_name   = "JARVIS03 RADAR"
    agent_domain = "radar"

    def get_health(self) -> dict:
        try:
            from JARVIS03_RADAR import radar_agent  # noqa
            return {"status": "online", "message": "RADAR 에이전트 정상"}
        except Exception as e:
            return {"status": "warn", "message": f"RADAR 로드 실패: {e}"}

    def get_metrics(self) -> dict:
        try:
            from shared.db import get_db
            from datetime import date
            today = date.today().isoformat()
            with get_db() as conn:
                today_trends = conn.execute(
                    "SELECT COUNT(*) FROM trends WHERE date=?", (today,)
                ).fetchone()[0]
                top_score = conn.execute(
                    "SELECT MAX(opportunity_score) FROM trends WHERE date=?", (today,)
                ).fetchone()[0] or 0
                sectors = conn.execute(
                    "SELECT sector, COUNT(*) as cnt FROM trends WHERE date=? GROUP BY sector ORDER BY cnt DESC LIMIT 5",
                    (today,),
                ).fetchall()
                pending_qa = conn.execute(
                    "SELECT COUNT(*) FROM post_analysis WHERE status='pending_approval'"
                ).fetchone()[0]
            return {
                "today_trends":   today_trends,
                "top_opp_score":  round(float(top_score), 1),
                "top_sectors":    {r["sector"]: r["cnt"] for r in sectors},
                "pending_qa":     pending_qa,
            }
        except Exception as e:
            return {"error": str(e)}


class _Scheduler04Adapter(BaseAgent):
    agent_id     = "jarvis04_scheduler"
    agent_name   = "JARVIS04 SCHEDULER"
    agent_domain = "scheduler"

    def get_health(self) -> dict:
        try:
            from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
            aps = get_apscheduler()
            if aps and aps.running:
                return {"status": "online", "message": f"APScheduler 실행 중 ({len(aps.get_jobs())}개 잡)"}
            return {"status": "warn", "message": "APScheduler 미실행"}
        except Exception as e:
            return {"status": "warn", "message": f"스케줄러 조회 실패: {e}"}

    def get_metrics(self) -> dict:
        try:
            from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
            from shared.db import get_db
            aps = get_apscheduler()
            jobs = aps.get_jobs() if aps else []
            next_jobs = []
            for j in sorted(jobs, key=lambda x: getattr(x, "next_run_time", None) or datetime.max):
                nrt = getattr(j, "next_run_time", None)
                if nrt:
                    next_jobs.append({"id": j.id, "name": j.name, "next": nrt.strftime("%H:%M")})
                if len(next_jobs) >= 5:
                    break
            with get_db() as conn:
                runs_24h = conn.execute(
                    "SELECT COUNT(*) FROM job_runs WHERE started_at >= datetime('now','localtime','-24 hours')"
                ).fetchone()[0]
                fails_24h = conn.execute(
                    "SELECT COUNT(*) FROM job_runs WHERE success=0 AND started_at >= datetime('now','localtime','-24 hours')"
                ).fetchone()[0]
            return {
                "total_jobs":   len(jobs),
                "runs_24h":     runs_24h,
                "fails_24h":    fails_24h,
                "next_5_jobs":  next_jobs,
            }
        except Exception as e:
            return {"error": str(e)}


# ════════════════════════════════════════════════════════════
# 레지스트리
# ════════════════════════════════════════════════════════════

class AgentRegistry:
    """에이전트 인스턴스 저장소. thread-safe (읽기 전용 dict, 데몬 부팅 시 1회 구성)."""

    def __init__(self):
        self._agents: Dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.agent_id] = agent
        log.info(f"  📋 VISION 레지스트리 등록: {agent.agent_id}")

    def get(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    def get_all(self) -> List[BaseAgent]:
        return list(self._agents.values())

    def ids(self) -> List[str]:
        return list(self._agents.keys())


# 싱글턴
_registry = AgentRegistry()


def get_registry() -> AgentRegistry:
    return _registry


class _Vision05Adapter(BaseAgent):
    agent_id     = "jarvis05_vision"
    agent_name   = "JARVIS05 VISION"
    agent_domain = "vision"

    def get_health(self) -> dict:
        try:
            from JARVIS05_VISION.collector import get_summary
            s = get_summary()
            total  = s.get("total", 0)
            online = s.get("online", 0)
            pct    = s.get("health_pct", 0)
            status = "online" if pct >= 80 else "warn" if pct >= 50 else "danger"
            return {"status": status,
                    "message": f"에이전트 {total}개 모니터링<br>온라인 {online} / 건강도 {pct}%"}
        except Exception as e:
            return {"status": "warn", "message": f"collector 미초기화: {e}"}

    def get_metrics(self) -> dict:
        try:
            from JARVIS05_VISION.collector import get_summary
            return get_summary()
        except Exception as e:
            return {"error": str(e)}


class _Image06Adapter(BaseAgent):
    agent_id     = "jarvis06_image"
    agent_name   = "JARVIS06 IMAGE"
    agent_domain = "image"

    def get_health(self) -> dict:
        try:
            import os
            bing_ok = bool(os.getenv("BING_COOKIE", "").strip())
            hf_ok   = bool(os.getenv("HUGGINGFACE_API_KEY", "").strip())
            backends = []
            if bing_ok:  backends.append("Bing")
            if hf_ok:    backends.append("HuggingFace")
            backends.append("Pollinations")
            return {"status": "online", "message": f"활성 백엔드: {', '.join(backends)}"}
        except Exception as e:
            return {"status": "warn", "message": f"상태 조회 실패: {e}"}

    def get_metrics(self) -> dict:
        try:
            import os
            return {
                "bing_available":         bool(os.getenv("BING_COOKIE", "").strip()),
                "huggingface_available":  bool(os.getenv("HUGGINGFACE_API_KEY", "").strip()),
                "pollinations_available": True,
            }
        except Exception as e:
            return {"error": str(e)}


def bootstrap_builtin_adapters() -> None:
    """JARVIS00~06 어댑터를 레지스트리에 등록. vision_agent.register() 에서 1회 호출."""
    for cls in [
        _Infra00Adapter,
        _Master01Adapter,
        _Writer02Adapter,
        _Radar03Adapter,
        _Scheduler04Adapter,
        _Vision05Adapter,
        _Image06Adapter,
    ]:
        try:
            _registry.register(cls())
        except Exception as e:
            log.warning(f"⚠️ 어댑터 등록 실패 {cls.__name__}: {e}")
            _g_report("vision", e, module=__name__)
    log.info(f"✅ VISION 레지스트리 부팅 완료 — {len(_registry.ids())}개 에이전트")


# ── 기존 builtin agent_id (자동 감지 대상에서 제외) ──────────────
_BUILTIN_IDS = {
    "jarvis00_infra", "jarvis01_master", "jarvis02_writer",
    "jarvis03_radar", "jarvis04_scheduler", "jarvis05_vision",
    "jarvis06_image",
}


def auto_discover_agents() -> int:
    """JARVIS{NN}_*/ 폴더를 스캔해 BaseAgent 구현체를 자동 등록.

    규칙:
      - 폴더명이 JARVIS + 숫자 + _ 로 시작
      - _BUILTIN_IDS 에 없는 agent_id
      - 폴더 안 *_agent.py 에 BaseAgent 서브클래스 존재
      - 이미 등록된 agent_id 는 skip (중복 방지)
    반환: 새로 등록된 에이전트 수
    """
    import importlib
    import inspect
    import re
    from pathlib import Path

    base_dir  = Path(__file__).parent.parent  # jarvis-agent 루트
    pattern   = re.compile(r"^JARVIS\d+_", re.IGNORECASE)
    new_count = 0

    for folder in sorted(base_dir.iterdir()):
        if not folder.is_dir():
            continue
        if not pattern.match(folder.name):
            continue

        # *_agent.py 파일 탐색
        for agent_file in folder.glob("*_agent.py"):
            module_name = f"{folder.name}.{agent_file.stem}"
            try:
                mod = importlib.import_module(module_name)
            except Exception as e:
                log.debug(f"[auto-discover] {module_name} import 실패: {e}")
                continue

            # BaseAgent 서브클래스 탐색
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if cls is BaseAgent:
                    continue
                if not issubclass(cls, BaseAgent):
                    continue
                aid = getattr(cls, "agent_id", None)
                if not aid or aid in _BUILTIN_IDS:
                    continue
                if aid in _registry.ids():
                    log.debug(f"[auto-discover] {aid} 이미 등록 — skip")
                    continue
                try:
                    _registry.register(cls())
                    log.info(f"🔌 [auto-discover] 신규 에이전트 등록: {aid}")
                    new_count += 1
                except Exception as e:
                    log.warning(f"⚠️ [auto-discover] {aid} 등록 실패: {e}")
                    _g_report("vision", e, module=__name__)

    if new_count:
        log.info(f"✅ auto-discover 완료 — {new_count}개 신규 등록")
    return new_count
