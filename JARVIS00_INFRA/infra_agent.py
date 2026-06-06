"""JARVIS00_INFRA/infra_agent.py — 시스템 메타 관리 에이전트.

★ 책임 (확장):
- 런타임 메타: 데몬 프로세스 라이프사이클 + 시스템 상태 종합 보고
- 설계타임 메타: 새 에이전트·도구·잡·skill 신설 기획서 산출 (architect.py 위임)

jarvis_daemon 의 런타임 상태는 lazy import 로 참조 (순환 import 안전).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")


def register_capability() -> None:
    """capability 레지스트리에 jarvis00_infra 등록."""
    try:
        from shared.capabilities import declare
        declare(
            agent_id="jarvis00_infra",
            domain="infra",
            intents=[
                "infra.status",
                "infra.daemon.start",
                "infra.daemon.restart",
                "infra.daemon.shutdown",
                # ── 설계타임 메타 (ARCHITECT) ──
                "architect.design",
            ],
            tools=[],
            requires_approval=["infra.daemon.restart", "infra.daemon.shutdown"],
            cost_class="low",
            description="시스템 메타 관리 — 데몬 lifecycle·상태 보고 + 새 에이전트 설계 기획서 산출 (ARCHITECT).",
            tags=["infra", "daemon", "system", "architect", "meta"],
            help_section=(
                "⚙️ *시스템 관리 (JARVIS00)*\n"
                "/status   전체 상태 확인\n"
                "/restart  데몬 재시작\n"
                "/quit     데몬 종료"
            ),
        )
    except Exception as e:
        log.warning(f"⚠️ jarvis00_infra capability 등록 실패: {e}")
        _g_report("infra", e, module=__name__)


# ── 시스템 상태 ─────────────────────────────────────────────────

def build_status() -> str:
    """텔레그램 /status 용 실시간 감시판.

    각 에이전트의 status_fn() 을 순서대로 호출해 조립.
    새 에이전트는 declare(status_fn=...) 만 추가하면 자동 포함.
    """
    import jarvis_daemon as _dm
    now    = datetime.now()
    lines  = []

    # JARVIS00 헤더 (데몬 가동 상태 — 인프라 고유)
    delta  = now - _dm._daemon_start_time
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m      = rem // 60
    uptime = f"{h}시간 {m}분" if h else f"{m}분"
    lines.append("🟢 *JARVIS 통합 데몬 실행 중*")
    lines.append(f"⚙️ *JARVIS00_INFRA*  |  가동 {uptime}  |  PID {os.getpid()}")
    if _dm._st_disabled:
        lines.append("❌ 대시보드: 자동재시작 중단 (5회 실패)")
    elif _dm._streamlit_alive():
        lines.append(f"🖥 대시보드: 가동 중 (port {_dm.ST_PORT}, PID {_dm._st_proc.pid})")
    else:
        lines.append(f"⚠️ 대시보드: 다운 ({_dm._st_fail_count}/5)")

    # 각 에이전트 섹션 — capability registry 순회 (agent_id 사전순 = JARVIS01→05)
    from shared import capabilities as _caps
    for cap in sorted(_caps.all_capabilities(), key=lambda c: c.agent_id):
        if cap.agent_id == "jarvis00_infra":
            continue  # 헤더에서 이미 표시
        if not cap.status_fn:
            continue
        lines.append("━━━━━━━━━━━━━━━━━━")
        try:
            lines.append(cap.status_fn())
        except Exception as e:
            lines.append(f"⚠️ {cap.agent_id} 상태 조회 실패: {str(e)[:60]}")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"🕐 {now.strftime('%m/%d %H:%M')} 기준  |  /help")
    return "\n".join(lines)


# ── 재시작 헬퍼 ─────────────────────────────────────────────────

def _spawn_restart(delay: int = 5) -> None:
    """현재 프로세스와 독립된 셸에서 delay초 후 데몬을 재기동.

    start_new_session=True 로 부모 종료 후에도 생존.
    Keeper plist 없이도 동작.
    """
    import subprocess, sys
    python  = sys.executable                          # .venv/bin/python
    daemon  = str(os.path.abspath(                    # ~/jarvis-agent/jarvis_daemon.py
        os.path.join(os.path.dirname(__file__), "..", "jarvis_daemon.py")
    ))
    script  = (
        f"sleep {delay} && "
        f"nohup {python} {daemon} >> {os.path.dirname(daemon)}/logs/daemon.log 2>&1 &"
    )
    subprocess.Popen(
        ["bash", "-c", script],
        close_fds=True,
        start_new_session=True,   # 부모 종료와 무관하게 독립 실행
    )
    log.info(f"🚀 재시작 셸 스폰 완료 — {delay}초 후 기동")


# ── 슬래시 명령 핸들러 ──────────────────────────────────────────

def handle_command(cmd: str) -> bool:
    """슬래시 명령어(/status, /restart, /quit) 처리. 처리했으면 True."""
    import jarvis_daemon as _dm

    if cmd == "/status":
        _dm._send_tg(build_status())
        return True

    if cmd in ("/restart", "/restart_daemon"):
        _dm._send_tg("🔄 JARVIS 재시작 중... 5초 후 자동 재기동됩니다.")
        log.info("🔄 텔레그램 /restart 명령 — 데몬 재시작")
        _spawn_restart(delay=5)
        if _dm._sched:
            _dm._sched._shutdown = True
        _dm._daemon_shutdown.set()
        return True

    if cmd == "/quit":
        _dm._send_tg("🛑 JARVIS 데몬 종료 중... (10초 내 종료)\n다시 시작: /start")
        log.info("🛑 텔레그램 /quit 명령 — 데몬 종료")
        if _dm._sched:
            _dm._sched._shutdown = True
        _dm._daemon_shutdown.set()
        return True

    return False


# ── 자유문장 라우터 (JARVIS01) 연동 ────────────────────────────

def handle_safe_intent(intent: str, params: dict | None = None) -> bool:
    """SAFE infra 인텐트 처리. 처리했으면 True.

    params: 자유 문장에서 추출된 파라미터 (예: architect.design 의 user_intent).
    """
    if intent == "infra.status":
        import jarvis_daemon as _dm
        _dm._send_tg(build_status())
        return True
    if intent == "infra.daemon.start":
        # 데몬은 *이미 실행 중* (이 함수가 호출된다는 것은 데몬 동작 증거).
        # 외부에서 데몬을 *시작* 하는 명령은 jarvis_keeper.py 또는 manual.
        import jarvis_daemon as _dm
        _dm._send_tg(
            "✅ JARVIS 데몬은 이미 실행 중입니다.\n"
            "/status — 상세 상태\n"
            "/restart — 재시작 (Keeper 자동 재기동)\n"
            "/quit — 완전 종료"
        )
        return True
    if intent == "architect.design":
        return _handle_architect_design(params or {})
    return False


def _handle_architect_design(params: dict) -> bool:
    """architect.design SAFE 인텐트 — 기획서 산출 + exec_plan 버튼 송출."""
    import jarvis_daemon as _dm
    user_intent = (params.get("user_intent") or params.get("intent_text") or "").strip()
    if not user_intent:
        _dm._send_tg(
            "⚠️ ARCHITECT — 사용자 의도 (`user_intent`) 가 비어 있습니다.\n"
            "예: \"가계부 자동화 에이전트 만들고 싶어\""
        )
        return True
    scope = (params.get("scope") or "agent").strip()
    _dm._send_tg("🏛 ARCHITECT — 기획서 산출 중... (잠시 기다려 주세요)")
    try:
        from JARVIS00_INFRA.architect import design_new_agent
        result = design_new_agent(user_intent=user_intent, scope=scope)
    except Exception as e:
        log.error(f"❌ architect.design 호출 실패: {e}")
        _g_report("infra", e, module=__name__)
        _dm._send_tg(f"❌ ARCHITECT 호출 실패: {str(e)[:200]}")
        return True

    if not result.get("ok"):
        _dm._send_tg(f"❌ ARCHITECT 산출 실패: {result.get('error', 'unknown')}")
        return True

    spec_path = result.get("spec_path", "")
    summary   = result.get("summary", "")
    warnings  = result.get("warnings", [])
    high_risks = [r for r in result.get("errors_risk", []) if r.get("risk") == "high"]
    exec_steps = result.get("exec_plan_steps") or []

    # ── 공통 상단 메시지 ──────────────────────────────────────
    header_lines = [
        "🏛 *ARCHITECT — 설계 기획서 산출 완료*",
        "",
        summary,
        "",
        f"📄 기획서: `{spec_path}`",
    ]
    if warnings:
        header_lines.append(f"\n⚠️ *경고 {len(warnings)}건*:")
        for w in warnings[:5]:
            header_lines.append(f"  • {w[:120]}")
    if high_risks:
        header_lines.append(f"\n🚨 *ERRORS 재현 위험 {len(high_risks)}건*:")
        for r in high_risks[:5]:
            header_lines.append(f"  • {r['id']}: {r['note'][:100]}")

    # ── 기획 요약 알림 후 즉시 자동 실행 (승인 없음) ───────────
    step_lines = [
        f"  {i}. `[{s.get('tool','?')}]` {s.get('note') or s.get('tool','')}"
        for i, s in enumerate(exec_steps, 1)
    ]
    notify_msg = (
        "\n".join(header_lines)
        + (f"\n\n📋 구현 단계 {len(exec_steps)}개:\n" + "\n".join(step_lines)
           + "\n\n🔨 구현을 자동 시작합니다..."
           if exec_steps else "\n\n⚠️ exec_plan 없음 — §12 참조 수동 구현 필요")
    )
    _dm._send_tg(notify_msg)

    if not exec_steps:
        return True

    # ── 백그라운드에서 즉시 실행 (write_file / run_bash — LLM 0회) ──
    import threading as _threading
    import time as _time

    def _auto_exec():
        from shared.tools import tool_invoke, approved_context
        n = len(exec_steps)
        ok_n = 0
        for i, s in enumerate(exec_steps, 1):
            tool = s.get("tool", "")
            args = s.get("args") if isinstance(s.get("args"), dict) else {}
            note = s.get("note") or tool
            _dm._send_tg(f"⚙️ [{i}/{n}] `{tool}` — {note[:80]}")
            t0 = _time.time()
            try:
                with approved_context():
                    res = tool_invoke(tool, **args)
                elapsed = _time.time() - t0
                ok = res.get("ok", True) if isinstance(res, dict) else True
                if ok:
                    ok_n += 1
                    _dm._send_tg(f"  ✅ 완료 ({elapsed:.1f}초)")
                else:
                    err = res.get("error") if isinstance(res, dict) else str(res)
                    _dm._send_tg(f"  ❌ 실패: {str(err)[:200]}\n⛔ 구현 중단 ({i}/{n})")
                    return
            except Exception as e:
                _dm._send_tg(f"  ❌ 예외: {str(e)[:200]}\n⛔ 구현 중단 ({i-1}/{n})")
                return
        _dm._send_tg(f"🎉 구현 완료 — {ok_n}/{n}단계 성공")

    _threading.Thread(target=_auto_exec, daemon=True, name="arch_auto_exec").start()
    return True


def execute_approval(intent: str) -> bool:
    """승인 후 인프라 인텐트 실행. 처리했으면 True."""
    import jarvis_daemon as _dm

    if intent == "infra.daemon.restart":
        _dm._send_tg("🔄 JARVIS 재시작 중... 5초 후 자동 재기동됩니다.")
        log.info("[J00 승인] 데몬 재시작")
        _spawn_restart(delay=5)
        if _dm._sched:
            _dm._sched._shutdown = True
        _dm._daemon_shutdown.set()
        return True

    if intent == "infra.daemon.shutdown":
        _dm._send_tg("🛑 JARVIS 데몬 종료 중...")
        log.info("[J00 승인] 데몬 종료")
        if _dm._sched:
            _dm._sched._shutdown = True
        _dm._daemon_shutdown.set()
        return True

    return False


# ── 자동등록 진입점 ─────────────────────────────────────────────

def register(scheduler, bus):
    """데몬 부팅 시 _autoregister_agents 가 호출하는 표준 시그니처.

    JARVIS00_INFRA 는 *cron 잡 등록* 없이 capability 만 선언.
    실제 인프라 동작 (텔레그램 봇 polling·Streamlit 자식 프로세스 등) 은
    jarvis_daemon.py 의 부트스트랩 흐름이 그대로 보유 — 본 모듈은 *위임 대상* 역할.
    """
    register_capability()


# ── Infra scheduled jobs (jarvis_daemon 에서 이관) ────────────────

def job_db_backup() -> None:
    """매일 03:00 — SQLite 백업 + 30일 retention."""
    from shared import db
    try:
        r = db.backup_db(retention_days=30)
        msg = (
            f"💾 DB 백업 완료: {r['backup'].name} "
            f"({r['size_kb']}KB"
            + (f", {r['removed']}개 만료 삭제" if r["removed"] else "")
            + ")"
        )
        log.info(msg)
    except Exception as e:
        log.error(f"❌ DB 백업 실패: {e}")
        _g_report("infra", e, module=__name__)
        from shared.notify import send_tg
        send_tg(f"⚠️ DB 백업 실패: {e}")


def job_cleanup_events() -> None:
    """매주 일요일 03:30 — events 테이블 30일 이전 row 삭제."""
    from shared import db
    try:
        n = db.cleanup_events(days=30)
        log.info(f"🧹 events 테이블 정리: {n}건 삭제")
    except Exception as e:
        log.error(f"❌ events 정리 실패: {e}")
        _g_report("infra", e, module=__name__)


def job_file_cleanup() -> None:
    """격주 월요일 04:00 — 오래된 로그·임시파일·스크린샷 자동 정리."""
    try:
        from shared.file_cleanup import run_cleanup
        from shared.notify import send_tg
        stats = run_cleanup(verbose=False)
        total = stats.pop("total", 0)
        if total:
            detail = "  ".join(f"{k}:{v}" for k, v in stats.items())
            msg = f"🧹 파일 정리 완료: {total}개 삭제  ({detail})"
            log.info(msg)
            send_tg(msg)
        else:
            log.info("🧹 파일 정리: 삭제 대상 없음")
    except Exception as e:
        log.error(f"❌ 파일 정리 실패: {e}")
        _g_report("infra", e, module=__name__)


__all__ = [
    "register", "register_capability",
    "build_status",
    "handle_command", "handle_safe_intent", "execute_approval",
    "job_db_backup", "job_cleanup_events", "job_file_cleanup",
]
