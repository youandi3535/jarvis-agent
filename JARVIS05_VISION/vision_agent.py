"""
JARVIS05_VISION/vision_agent.py — 데몬 자동 등록 진입점.

jarvis_daemon._autoregister_agents() 가 이 파일의 register() 를 자동 호출.
register() 안에서:
  1. shared/db.py 에 vision_agent_status 테이블 초기화
  2. 기존 JARVIS00~04 어댑터 레지스트리 등록
  3. VisionCollector 스레드 시작 (30초 주기 메트릭 수집)
  4. FastAPI 서버 스레드 시작 (port 8505)
  5. JARVIS04 DEFAULT_JOBS 에 1분 주기 수집 잡 등록
"""
from __future__ import annotations

import logging

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis.vision")


def _status_section() -> str:
    lines = ["👁 *JARVIS05 — VISION (메트릭 모니터링)*"]
    try:
        from JARVIS05_VISION.collector import get_summary
        s = get_summary()
        total   = s.get("total", 0)
        online  = s.get("online", 0)
        warn    = s.get("warn", 0)
        offline = s.get("offline", 0)
        pct     = s.get("health_pct", 0)
        lines.append(f"✅ 에이전트: {total}개  |  온라인 {online}  경고 {warn}  오프라인 {offline}")
        lines.append(f"📊 헬스: {pct}%")
    except Exception:
        lines.append("⚠️ 메트릭 조회 실패 (collector 미초기화)")
    from JARVIS05_VISION.api_server import VISION_PORT
    lines.append(f"🌐 API: http://127.0.0.1:{VISION_PORT}/docs")
    return "\n".join(lines)


# ── capability 등록 (모듈 레벨 — 데몬 capability 스캔 시 자동 실행) ─

def _register_capability():
    try:
        from shared.capabilities import declare
        declare(
            agent_id="jarvis05_vision",
            domain="vision",
            intents=[
                "vision.status",
                "vision.agents.list",
                "vision.metrics.summary",
            ],
            tools=[],
            requires_approval=[],
            cost_class="low",
            description="VISION — 모든 에이전트 메트릭 수집·집계·시각화 API 제공.",
            tags=["vision", "monitoring", "dashboard"],
            help_section=(
                "👁 *모니터링 (JARVIS05)*\n"
                "슬래시 명령어 없음 — 자유 문장으로 조회\n"
                "예: 에이전트 상태 보여줘 / 메트릭 요약해줘"
            ),
            status_fn=_status_section,
        )
    except Exception as e:
        log.warning(f"⚠️ jarvis05_vision capability 등록 실패: {e}")
        _g_report("vision", e, module=__name__)


_register_capability()


# ── 데몬 자동등록 진입점 ────────────────────────────────────────

def register(scheduler, bus) -> None:
    """jarvis_daemon 자동등록 시 호출. scheduler·bus 는 JARVIS04 인스턴스."""
    log.info("🔌 JARVIS05_VISION register() 시작")

    # 1. DB 테이블 초기화
    try:
        from shared.db import _init_vision_tables
        _init_vision_tables()
        log.info("  ✅ vision_agent_status 테이블 준비 완료")
    except Exception as e:
        log.error(f"  ❌ vision DB 초기화 실패: {e}")
        _g_report("vision", e, module=__name__)
        return

    # 2. 레지스트리 — 기존 JARVIS00~04 어댑터 + 신규 에이전트 자동 감지
    try:
        from JARVIS05_VISION.registry import bootstrap_builtin_adapters, auto_discover_agents
        bootstrap_builtin_adapters()
        new_cnt = auto_discover_agents()
        if new_cnt:
            log.info(f"  🔌 auto-discover: {new_cnt}개 신규 에이전트 등록")
    except Exception as e:
        log.error(f"  ❌ 레지스트리 부팅 실패: {e}")
        _g_report("vision", e, module=__name__)
        return

    # 3. Collector 스레드 시작
    try:
        from JARVIS05_VISION.collector import start_collector
        start_collector()
    except Exception as e:
        log.error(f"  ❌ Collector 시작 실패: {e}")
        _g_report("vision", e, module=__name__)

    # 4. FastAPI 서버 시작
    try:
        from JARVIS05_VISION.api_server import start_api_server
        start_api_server()
    except Exception as e:
        log.error(f"  ❌ API 서버 시작 실패: {e}")
        _g_report("vision", e, module=__name__)

    # 5. 이벤트 버스 구독 — POST_PUBLISHED 시 즉시 Writer 메트릭 갱신
    try:
        def _on_post_published(payload, source):
            from JARVIS05_VISION.collector import _collect_once
            _collect_once()

        if hasattr(bus, "EventType") and hasattr(bus.EventType, "POST_PUBLISHED"):
            bus.subscribe(bus.EventType.POST_PUBLISHED, _on_post_published)
            log.info("  ✅ POST_PUBLISHED 이벤트 구독 완료")
    except Exception as e:
        log.warning(f"  ⚠️ 이벤트 버스 구독 실패 (무시): {e}")
        _g_report("vision", e, module=__name__)

    from JARVIS05_VISION.api_server import VISION_PORT
    log.info(f"✅ JARVIS05_VISION 등록 완료 — API: http://127.0.0.1:{VISION_PORT}/docs")
