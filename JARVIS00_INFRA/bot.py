"""JARVIS00_INFRA/bot.py — 통합 텔레그램 봇 (jarvis_daemon 에서 이관).

외부 진입점:
  - run_bot_polling(shutdown_event) : 메인 polling 루프 (daemon 이 스레드로 실행)
  - _send_tg(text)                  : 텔레그램 메시지 전송 (daemon 시작·종료 알림 등)

내부 상태:
  - _PENDING_J00 / _PENDING_J00_REACT / _PENDING_J00_PLAN : 승인 대기 딕셔너리

daemon 런타임 참조:
  - _sched (JARVIS02 scheduler) → 함수 내 lazy import  ★circular-import 방지
"""
from __future__ import annotations

import os, sys, time, threading, logging, requests, uuid as _uuid
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# ── 환경변수 ──────────────────────────────────────────────────
TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

JARVIS_ROOT = Path(__file__).resolve().parent.parent
RADAR_DIR   = JARVIS_ROOT / "JARVIS03_RADAR"
PLANS_DIR   = JARVIS_ROOT / "logs" / "pending_plans"

# ── 승인 대기 딕셔너리 ─────────────────────────────────────────
# JARVIS01 승인 대기 { "j00:intent:xxxx": {"intent", "params", "user_msg"} }
_PENDING_J00: dict = {}
# JARVIS01 ReAct 승인 대기 { "j00r:tool:xxxx": {"tool", "args", "user_msg", "thread_id"} }
_PENDING_J00_REACT: dict = {}
# JARVIS01 계획 승인 대기 { "plan:xxxx": {"goal", "steps", "single_approval"} }
_PENDING_J00_PLAN: dict = {}
# JARVIS02 외부 발행 슬래시 명령 승인 대기 (★ 2026-06-28) { "economic:xxxx": {"cmd"} }
_PENDING_J02_CMD: dict = {}
# 외부 영향(실제 발행) 명령 → 인라인 버튼 승인 게이트 필수
_J02_EXT_CMDS = {
    "/economic":         "경제 브리핑 발행 (네이버+티스토리)",
    "/economic_naver":   "경제 브리핑 발행 (네이버만)",
    "/economic_tistory": "경제 브리핑 발행 (티스토리만)",
    "/next":             "다음 테마 즉시 발행",
}


# ════════════════════════════════════════════════════════════
# 텔레그램 헬퍼
# ════════════════════════════════════════════════════════════

def _send_tg(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        log.warning("[봇] _send_tg 스킵: TOKEN 또는 CHAT_ID 없음")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not r.json().get("ok"):
            desc = r.json().get("description", "")
            log.warning(f"[봇] sendMessage 실패 ({r.status_code}): {desc}")
            if "can't parse" in desc or "parse" in desc.lower():
                r2 = requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={"chat_id": TG_CHAT_ID, "text": text},
                    timeout=10,
                )
                if not r2.json().get("ok"):
                    log.warning(f"[봇] plain text 재시도도 실패: {r2.json().get('description')}")
    except Exception as e:
        log.warning(f"[봇] 텔레그램 전송 오류: {e}")
        _g_report("infra", e, module=__name__)


def _answer_callback(callback_id: str, text: str = "처리 완료"):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _send_tg_buttons(text: str, buttons: list[list[dict]]):
    """인라인 키보드 버튼이 달린 메시지 전송. Markdown 실패 시 plain text 재시도."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
        if not r.json().get("ok"):
            desc = r.json().get("description", "")
            log.warning(f"[봇] _send_tg_buttons Markdown 실패: {desc}")
            r2 = requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": text,
                    "reply_markup": {"inline_keyboard": buttons},
                },
                timeout=10,
            )
            if not r2.json().get("ok"):
                log.warning(f"[봇] _send_tg_buttons plain text 재시도도 실패: {r2.json().get('description')}")
    except Exception as e:
        log.warning(f"[봇] _send_tg_buttons 오류: {e}")
        _g_report("infra", e, module=__name__)


def _clear_webhook():
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook",
            timeout=10,
        )
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
# daemon 런타임 참조 — lazy import (_sched)
# ════════════════════════════════════════════════════════════

def _get_sched():
    """JARVIS02 scheduler 인스턴스 — daemon 모듈에서 lazy import."""
    try:
        import jarvis_daemon as _dm
        return getattr(_dm, "_sched", None)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# 자유 문장 라우팅
# ════════════════════════════════════════════════════════════

def _route_free_text(text: str, session_id: str = ""):
    """자유 문장 → JARVIS01 자동 라우팅.

    1) ReAct (다단계) → 2) 레거시 1-step 분류기 fallback.
    session_id: Telegram chat_id → LangGraph thread_id (멀티턴 세션).
    """
    try:
        ok = _run_react(text, max_steps=12, verbose=False, session_id=session_id)
    except Exception as e:
        log.warning(f"[J00] ReAct 라우팅 예외 → fallback: {e}")
        _g_report("infra", e, module=__name__)
        ok = False

    if ok:
        return

    log.info("[J00] ReAct 미가용 → 레거시 1-step 분류기 fallback")
    try:
        from JARVIS01_MASTER.router import handle as _route00
        r = _route00(text)
    except Exception as e:
        _send_tg(f"⚠️ JARVIS01 라우터 오류: {e}")
        return

    cls    = r.get("classification", {})
    intent = cls.get("intent", "core.unknown")
    params = cls.get("params", {})
    conf   = float(cls.get("confidence", 0.0))
    agent  = r.get("target_agent")

    log.info(f"  [J00 fallback] 자유문장 분류: intent={intent} conf={conf:.2f} agent={agent}")

    if conf < 0.4:
        intent = "core.unknown"

    from JARVIS01_MASTER.dispatchers import (
        get_dispatch_mode, describe_approval, execute_safe,
    )
    mode = get_dispatch_mode(intent)

    if mode == "SAFE":
        if intent.startswith("infra."):
            from JARVIS00_INFRA.infra_agent import handle_safe_intent
            handle_safe_intent(intent, params)
            return
        elif intent == "core.list_agents":
            from shared import capabilities as _caps
            cat = _caps.render_for_router_prompt()
            n = len(_caps.all_capabilities())
            _send_tg(f"🧭 *등록 에이전트 {n}개*\n```\n{cat}\n```")
        elif intent in ("core.chat", "core.preview_route"):
            _send_tg(
                f"안녕하세요! 자비스입니다. 😊\n\n"
                f"_{cls.get('rationale', '무엇을 도와드릴까요?')}_\n\n"
                f"/help 로 전체 명령어를 확인하세요."
            )
        elif intent == "core.unknown":
            _send_tg(
                f"죄송해요, 이해하지 못했어요.\n\n"
                f"_{cls.get('rationale', '분류 불가')}_\n\n"
                f"/help 로 명령어를 확인하거나, 더 구체적으로 말씀해주세요."
            )
        else:
            result = execute_safe(intent, params, text)
            _send_tg(result or f"✅ `{intent}` 처리 완료")
        return

    if mode == "APPROVAL":
        key = f"j00:{intent}:{hash(text) & 0xFFFF:04x}"
        _PENDING_J00[key] = {"intent": intent, "params": params, "user_msg": text}
        desc = describe_approval(intent, params, text)
        _send_tg_buttons(desc, [[
            {"text": "✅ 실행", "callback_data": f"j00_yes:{key}"},
            {"text": "❌ 취소", "callback_data": f"j00_no:{key}"},
        ]])
        return

    domain = intent.split(".")[0] if "." in intent else intent
    _send_tg(
        f"🤔 *{text[:60]}*\n\n"
        f"_분류 결과: `{intent}` (신뢰도 {conf:.2f}) — 자동 라우팅 모호_\n\n"
        f"💡 *다시 시도*: 더 구체적으로 말씀해주세요.\n"
        f"  • 코드 수정: \"X 파일의 Y를 Z로 바꿔줘\"\n"
        f"  • 잡 조회: \"등록된 잡 보여줘\" / \"다음 5개 잡\"\n"
        f"  • 발행: \"네이버에 반도체 발행해줘\"\n"
        f"  • 깊은 분석: \"Claude Code 에 위임해서 ...\"\n\n"
        f"/help 로 전체 기능 확인 가능."
    )


# ════════════════════════════════════════════════════════════
# 승인 실행 핸들러
# ════════════════════════════════════════════════════════════

def _execute_j00_approval(key: str):
    """인라인 버튼 '✅ 실행' 콜백 — 승인 대기 액션 실행."""
    pending = _PENDING_J00.pop(key, None)
    if not pending:
        _send_tg("⚠️ 만료되었거나 이미 처리된 요청입니다.")
        return

    intent   = pending["intent"]
    params   = pending["params"]
    user_msg = pending["user_msg"]

    if intent.startswith("infra."):
        from JARVIS00_INFRA.infra_agent import execute_approval
        execute_approval(intent)
        return

    if intent.startswith("schedule.job."):
        from JARVIS01_MASTER.dispatchers import execute_schedule_change
        result = execute_schedule_change(intent, params)
        _send_tg(result)
        return

    from JARVIS01_MASTER.dispatchers import build_j01_command
    j01_cmd = build_j01_command(intent, params)
    _sched = _get_sched()
    if j01_cmd and _sched:
        cmds = [j01_cmd] if isinstance(j01_cmd, str) else list(j01_cmd)
        log.info(f"[J00 승인] J01 위임: {cmds}")
        if len(cmds) == 1:
            _send_tg(f"✅ 승인 완료 — `{cmds[0]}` 실행합니다!")
        else:
            _send_tg(f"✅ 승인 완료 — {len(cmds)}개 명령 순차 실행:\n" +
                     "\n".join(f"  • `{c}`" for c in cmds))
        for c in cmds:
            try:
                _sched.handle_telegram_command(c)
            except Exception as e:
                _send_tg(f"❌ `{c}` 실행 오류: {e}")
    elif not _sched:
        _send_tg("⚠️ JARVIS02 스케줄러가 로드되지 않았습니다.")
    else:
        _send_tg(f"✅ 승인 완료 (`{intent}`) — 데몬이 직접 처리합니다.")


# ════════════════════════════════════════════════════════════
# ReAct 라우터 실행
# ════════════════════════════════════════════════════════════

def _run_react(user_msg: str, max_steps: int = 12, verbose: bool = True, session_id: str = "") -> bool:
    """JARVIS01 ReAct 라우터 실행 + 승인 게이트 통합.

    Returns True 면 정상 처리. False 면 LLM/도구 미가용 → 호출자 fallback.
    """
    try:
        from JARVIS01_MASTER.router import react_handle
    except Exception as e:
        log.warning(f"[J00] ReAct 라우터 로드 실패 → fallback: {e}")
        _g_report("infra", e, module=__name__)
        if verbose:
            _send_tg(f"⚠️ ReAct 라우터 로드 실패: {e}")
        return False

    if verbose:
        _send_tg(f"🧠 ReAct 시작: _{user_msg[:60]}_")

    try:
        _thread_id = session_id if session_id else None
        out = react_handle(user_msg, max_steps=max_steps, auto_approve=False,
                           thread_id=_thread_id)
    except Exception as e:
        log.warning(f"[J00] ReAct 실행 예외 → fallback: {type(e).__name__}: {e}")
        _g_report("infra", e, module=__name__)
        if verbose:
            _send_tg(f"❌ ReAct 실행 오류: {e}")
        return False

    err = out.get("error") or ""
    if err:
        log.warning(f"[J00] ReAct error: {err}")
        if verbose:
            _send_tg(f"⚠️ ReAct 오류: {err}\n\n{out.get('text','')[:200]}")
        return False

    tcalls = out.get("tool_calls", [])
    if verbose and tcalls:
        lines = ["🔧 *ReAct 도구 호출 흐름*"]
        for i, tc in enumerate(tcalls, 1):
            mark = "🔒" if tc.get("approval") else "✅"
            lines.append(f"  {i}. {mark} `{tc['name']}`")
        _send_tg("\n".join(lines))

    pending = out.get("pending_approvals", [])
    if pending:
        for p in pending:
            if p["name"] == "create_plan":
                args = p.get("args") or {}
                steps = args.get("steps") or []
                goal = args.get("goal") or user_msg[:80]
                single_approval = bool(args.get("single_approval", True))
                if not steps:
                    _send_tg("⚠️ 빈 계획 — 단계 없음.")
                    continue
                plan_id = "plan:" + _uuid.uuid4().hex[:8]
                _PENDING_J00_PLAN[plan_id] = {
                    "goal": goal, "steps": steps,
                    "single_approval": single_approval,
                    "user_msg": user_msg,
                }
                step_lines = []
                for i, s in enumerate(steps, 1):
                    note = s.get("note") or s.get("tool", "")
                    step_lines.append(f"  {i}. `[{s.get('tool','?')}]` {note}")
                plan_msg = (
                    f"📋 *계획 승인 요청*\n\n"
                    f"목표: {goal[:200]}\n\n"
                    f"단계 {len(steps)}개:\n" + "\n".join(step_lines) +
                    f"\n\n실행하시겠습니까?"
                )
                _send_tg_buttons(plan_msg, [[
                    {"text": "✅ 전체 실행", "callback_data": f"plan_yes:{plan_id}"},
                    {"text": "❌ 취소",      "callback_data": f"plan_no:{plan_id}"},
                ]])
                continue
            key = f"j00r:{p['name']}:{abs(hash(str(p['args']))) & 0xFFFF:04x}"
            _PENDING_J00_REACT[key] = {
                "tool":      p["name"],
                "args":      p["args"],
                "user_msg":  user_msg,
                "thread_id": p.get("thread_id"),
            }
            tname = p["name"]
            args_brief = str(p["args"])[:200]
            desc = (
                f"🔔 *외부 도구 실행 요청*\n\n"
                f"도구: `{tname}`\n"
                f"인자: `{args_brief}`\n\n"
                f"실행하시겠습니까?"
            )
            _send_tg_buttons(desc, [[
                {"text": "✅ 실행", "callback_data": f"j00r_yes:{key}"},
                {"text": "❌ 취소", "callback_data": f"j00r_no:{key}"},
            ]])
        return True

    _raw_text = out.get("text", "") or ""
    if isinstance(_raw_text, list):
        _raw_text = " ".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in _raw_text)
    final = _raw_text.strip() or "(빈 응답)"
    prefix = "🧠 *ReAct 결과*\n\n" if verbose else ""
    _send_tg(f"{prefix}{final[:3500]}")
    return True


# ════════════════════════════════════════════════════════════
# PM / 계획 / ReAct 실행 핸들러
# ════════════════════════════════════════════════════════════

def _execute_pm_fix(fix_id: str):
    """ProactiveMonitor pm_yes 콜백 — execute_fix 위임."""
    try:
        from JARVIS01_MASTER.proactive_monitor import execute_fix
        execute_fix(fix_id)
    except Exception as e:
        _send_tg(f"❌ PM 수정 실행 실패: {e}")


def _execute_plan(plan_id: str):
    """Phase 3-C — 계획 승인 후 단계별 tool_invoke 실행."""
    import json as _json
    plan = _PENDING_J00_PLAN.pop(plan_id, None)
    if not plan:
        plan_file = PLANS_DIR / f"{plan_id.replace(':', '_')}.json"
        if plan_file.exists():
            try:
                plan = _json.loads(plan_file.read_text(encoding="utf-8"))
                plan_file.unlink(missing_ok=True)
            except Exception as e:
                log.warning(f"[플랜] 파일 로드 실패 {plan_file}: {e}")
                _g_report("infra", e, module=__name__)
    if not plan:
        _send_tg("⚠️ 만료되었거나 이미 처리된 계획입니다.")
        return
    steps = plan["steps"]
    goal  = plan["goal"]
    _send_tg(f"▶️ 계획 실행 시작 — {len(steps)}단계\n_목표: {goal[:100]}_")

    from shared.tools import tool_invoke, approved_context
    success_n = 0
    for i, s in enumerate(steps, 1):
        tname = s.get("tool", "")
        args  = s.get("args") if isinstance(s.get("args"), dict) else {}
        note  = s.get("note") or tname
        _send_tg(f"⚙️ [{i}/{len(steps)}] `{tname}` 실행 중 — {note[:80]}")
        _t_step = time.time()
        try:
            with approved_context():
                result = tool_invoke(tname, **args)
            _dur_step = time.time() - _t_step
            ok = result.get("ok", True) if isinstance(result, dict) else True
            if ok:
                success_n += 1
                _send_tg(f"  ✅ 완료 ({_dur_step:.1f}초)")
            else:
                err = result.get("error") if isinstance(result, dict) else str(result)
                _send_tg(f"  ❌ 실패: {str(err)[:200]}")
                _send_tg(f"⛔ 계획 중단 ({i}/{len(steps)} 단계 완료)")
                return
        except Exception as e:
            _send_tg(f"  ❌ 예외: {e}")
            _send_tg(f"⛔ 계획 중단 ({i-1}/{len(steps)} 단계 완료)")
            return
    _send_tg(f"🎉 계획 완료 — {success_n}/{len(steps)} 단계 성공")


def _execute_j00_react_approval(key: str):
    """ReAct 승인 — LangGraph 체크포인트 재개 (thread_id 있음) 또는 직접 실행."""
    pending = _PENDING_J00_REACT.pop(key, None)
    if not pending:
        _send_tg("⚠️ 만료되었거나 이미 처리된 ReAct 요청입니다.")
        return

    tool_name = pending["tool"]
    args      = pending["args"] if isinstance(pending.get("args"), dict) else {}
    thread_id = pending.get("thread_id")

    _send_tg(f"✅ 승인 — `{tool_name}` 실행 중...\n_긴 작업은 60초마다 진행 상황 알림_")

    if thread_id:
        def _resume():
            try:
                from JARVIS01_MASTER.router import resume_react
                out = resume_react(thread_id, approved=True)
                _handle_react_result(out, pending.get("user_msg", ""), verbose=False)
            except Exception as e:
                _send_tg(f"❌ ReAct 재개 실패: {e}")
        threading.Thread(target=_resume, daemon=True).start()
    else:
        _run_tool_with_heartbeat(tool_name, args)


def _handle_react_result(out: dict, user_msg: str, verbose: bool = False):
    """react_handle / resume_react 결과 공통 처리 → 텔레그램 전송."""
    err = out.get("error") or ""
    if err:
        _send_tg(f"⚠️ ReAct 오류: {err}\n\n{out.get('text','')[:200]}")
        return
    pending = out.get("pending_approvals", [])
    if pending:
        for p in pending:
            if p["name"] == "create_plan":
                continue
            key = f"j00r:{p['name']}:{abs(hash(str(p['args']))) & 0xFFFF:04x}"
            _PENDING_J00_REACT[key] = {
                "tool": p["name"], "args": p["args"],
                "user_msg": user_msg, "thread_id": p.get("thread_id"),
            }
            desc = (
                f"🔔 *추가 승인 요청*\n\n"
                f"도구: `{p['name']}`\n인자: `{str(p['args'])[:200]}`\n\n실행하시겠습니까?"
            )
            _send_tg_buttons(desc, [[
                {"text": "✅ 실행", "callback_data": f"j00r_yes:{key}"},
                {"text": "❌ 취소", "callback_data": f"j00r_no:{key}"},
            ]])
        return
    final = out.get("text", "").strip() or "(완료)"
    prefix = "🧠 *ReAct 결과*\n\n" if verbose else ""
    _send_tg(f"{prefix}{final[:3500]}")


def _run_tool_with_heartbeat(tool_name: str, args: dict,
                              heartbeat_seconds: int = 60,
                              max_wait_minutes: int = 15):
    """도구 실행 별도 스레드 + 주기적 하트비트 텔레그램 송출.

    - 60초마다 "⏳ N분 경과" 메시지
    - 완료 시 "🎉 완료 (소요 X)" + 결과
    - max_wait_minutes 초과 시 모니터링 중단 (도구는 계속)
    """
    holder = {"done": False, "result": None, "error": None}

    def _run():
        try:
            from shared.tools import tool_invoke, approved_context
            with approved_context():
                holder["result"] = tool_invoke(tool_name, **args)
        except Exception as e:
            holder["error"] = e
        finally:
            holder["done"] = True

    th = threading.Thread(target=_run, daemon=True, name=f"tool_{tool_name}")
    th.start()

    _t_start = time.time()
    _deadline = _t_start + max_wait_minutes * 60

    while not holder["done"]:
        time.sleep(heartbeat_seconds)
        if holder["done"]:
            break
        elapsed = int((time.time() - _t_start) / 60)
        _send_tg(f"⏳ `{tool_name}` 실행 중 — {elapsed}분 경과")
        if time.time() > _deadline:
            _send_tg(
                f"⏱️ `{tool_name}` 최대 대기 {max_wait_minutes}분 초과 — "
                f"모니터링 중단 (도구는 계속 실행 중)"
            )
            return

    elapsed_total = time.time() - _t_start
    mm, ss = divmod(int(elapsed_total), 60)
    dur_str = f"{mm}분 {ss}초" if mm else f"{ss}초"

    if holder["error"]:
        _send_tg(f"❌ `{tool_name}` 실패 ({dur_str}): {holder['error']}")
    else:
        result = holder["result"]
        if isinstance(result, dict):
            ok  = result.get("ok", True)
            msg = result.get("message") or result.get("result") or str(result)[:300]
            if ok:
                _send_tg(f"🎉 `{tool_name}` 완료 ({dur_str})\n{msg[:500]}")
            else:
                _send_tg(f"❌ `{tool_name}` 실패 ({dur_str})\n{msg[:500]}")
        else:
            _send_tg(f"🎉 `{tool_name}` 완료 ({dur_str})")


# ════════════════════════════════════════════════════════════
# 명령어 & 콜백 라우터
# ════════════════════════════════════════════════════════════

def _handle_radar_query(cmd: str):
    """JARVIS03 RADAR 읽기 전용 명령 (/trend·/radar·/report) — 승인 불필요."""
    try:
        if cmd == "/report":
            import threading as _th
            from JARVIS03_RADAR.daily_review import run_daily_review
            _send_tg("📊 일일 분석 리포트 생성 중...")
            _th.Thread(target=run_daily_review, daemon=True, name="cmd_report").start()
            return
        import glob, json as _json
        from pathlib import Path as _P
        _root = _P(__file__).resolve().parent.parent
        files = sorted(glob.glob(str(_root / "JARVIS03_RADAR" / "data" / "trends_*.json")))
        if not files:
            _send_tg("⚠️ 수집된 트렌드 데이터가 없습니다.")
            return
        data = _json.loads(_P(files[-1]).read_text(encoding="utf-8"))
        date = data.get("date", "?")
        if cmd == "/trend":
            kws = data.get("google_trending") or data.get("scored_keywords") or []
            lines = [f"📡 *트렌드 TOP 10* ({date})"]
            for i, k in enumerate(kws[:10], 1):
                kw = k.get("keyword") if isinstance(k, dict) else str(k)
                lines.append(f"{i}. {kw}")
            _send_tg("\n".join(lines) if len(lines) > 1 else "⚠️ 트렌드 키워드 없음")
        else:  # /radar
            recs = data.get("recommendations", [])
            lines = [f"🎯 *추천 테마 TOP 5* ({date})"]
            for i, r in enumerate(recs[:5], 1):
                lines.append(
                    f"{i}. *{r.get('theme','?')}* (점수 {r.get('score','?')}·{r.get('sector','?')})\n"
                    f"   {(r.get('topic') or '')[:60]}"
                )
            _send_tg("\n".join(lines) if recs else "⚠️ 추천 테마 없음")
    except Exception as e:
        _send_tg(f"⚠️ {cmd} 실패: {e}")
        _g_report("infra", e, module=__name__)


def _handle_cookie_refresh(cmd: str):
    """JARVIS08 쿠키 갱신 (/refresh_naver·/refresh_tistory) — 유지보수(외부 발행 아님), 직접 실행."""
    import threading as _th
    def _bg():
        try:
            if cmd == "/refresh_naver":
                from JARVIS08_PUBLISH.credentials.login_manager import refresh_naver_cookies
                ok = refresh_naver_cookies(force=True)
                _send_tg("✅ 네이버 쿠키 갱신 완료" if ok else "❌ 네이버 쿠키 갱신 실패 — 수동 확인 필요")
            else:
                from JARVIS08_PUBLISH.credentials.login_manager import refresh_tistory_cookies
                ok = refresh_tistory_cookies(force=True)
                _send_tg("✅ 티스토리 쿠키 갱신 완료" if ok else "❌ 티스토리 쿠키 갱신 실패 — 수동 확인 필요")
        except Exception as e:
            _send_tg(f"⚠️ {cmd} 실패: {e}")
            _g_report("infra", e, module=__name__)
    _send_tg(f"🔄 {cmd.lstrip('/')} 진행 중...")
    _th.Thread(target=_bg, daemon=True, name="cmd_refresh").start()


def _dispatch_text_command(text: str):
    """텍스트 명령어를 적절한 핸들러로 라우팅."""
    cmd = text.strip().lower().split()[0] if text.strip() else ""

    if cmd == "/start":
        _send_tg(
            "✅ JARVIS 데몬이 이미 실행 중입니다.\n"
            "/status — 전체 상태 확인\n"
            "/help   — 명령어 목록"
        )
        return

    if cmd == "/help":
        from shared.capabilities import build_help_text
        _send_tg(build_help_text())
        return

    from JARVIS00_INFRA.infra_agent import handle_command as _infra_cmd
    if _infra_cmd(cmd):
        return

    if cmd == "/agents":
        try:
            from shared import capabilities as _caps
            cat = _caps.render_for_router_prompt()
            n = len(_caps.all_capabilities())
            _send_tg(f"🧭 *등록 에이전트 {n}개*\n```\n{cat}\n```")
        except Exception as e:
            _send_tg(f"⚠️ /agents 실패: {e}")
        return

    if cmd == "/react":
        user_msg = text.strip()[len("/react"):].strip()
        if not user_msg:
            _send_tg(
                "사용법: `/react 자유 문장`\n"
                "예: `/react 최근 발행 글 5개 알려주고, 그 중 반도체 글 한 번 더 발행해줘`\n\n"
                "ReAct 모드는 SAFE 도구를 자동 호출하고, 외부 영향 도구는 인라인 버튼 승인 후 실행합니다."
            )
            return
        _run_react(user_msg, max_steps=12)
        return

    if cmd == "/route":
        user_msg = text.strip()[len("/route"):].strip()
        if not user_msg:
            _send_tg("사용법: `/route 자유 문장 입력`\n예: `/route 오늘 트렌드로 블로그 써줘`")
            return
        try:
            from JARVIS01_MASTER.router import handle as _route
            r = _route(user_msg)
            cls = r.get("classification", {})
            tgt = r.get("target_agent")
            disp = r.get("dispatch_result", {})
            _send_tg(
                f"🧭 *라우팅 결과*\n"
                f"입력: {user_msg}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"도메인: `{cls.get('target_domain','?')}`\n"
                f"인텐트: `{cls.get('intent','?')}` ({cls.get('intent_kind','?')})\n"
                f"확신도: {cls.get('confidence', 0):.2f}\n"
                f"근거: {cls.get('rationale','')[:120]}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"매칭 에이전트: `{tgt or '없음'}`\n"
                f"디스패치: {disp.get('note','')[:100]}"
            )
        except Exception as e:
            _send_tg(f"⚠️ /route 실패: {e}")
        return

    if cmd == "/jobs":
        try:
            from JARVIS01_MASTER.dispatchers import _schedule_job_list
            _send_tg(_schedule_job_list())
        except Exception as e:
            _send_tg(f"⚠️ /jobs 실패: {e}")
        return

    if cmd == "/jobs_next":
        try:
            from JARVIS01_MASTER.dispatchers import _schedule_next_runs
            _send_tg(_schedule_next_runs({"limit": 10}))
        except Exception as e:
            _send_tg(f"⚠️ /jobs_next 실패: {e}")
        return

    if cmd == "/jobs_log":
        try:
            parts = text.strip().split()
            hours = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 24
        except Exception:
            hours = 24
        try:
            from JARVIS01_MASTER.dispatchers import _schedule_history
            _send_tg(_schedule_history({"since_hours": hours, "limit": 20}))
        except Exception as e:
            _send_tg(f"⚠️ /jobs_log 실패: {e}")
        return

    if cmd == "/jobs_report":
        try:
            from JARVIS04_SCHEDULER.briefing import build_briefing_text
            _send_tg(build_briefing_text(hours=24))
        except Exception as e:
            _send_tg(f"⚠️ /jobs_report 실패: {e}")
        return

    if cmd == "/errors":
        # /errors [new|analyzing|fixed|wontfix|ignored] [limit]
        parts = text.strip().split()
        status_filter = parts[1] if len(parts) > 1 and not parts[1].isdigit() else "new"
        limit = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 10
        try:
            from shared import db as _db
            errors = _db.list_errors(status=status_filter, limit=limit)
            if not errors:
                _send_tg(f"✅ [{status_filter}] 오류 없음")
                return
            sev_icon = {"critical": "🚨", "high": "⚠️", "medium": "ℹ️", "low": "🔵"}
            lines = [f"🛡️ *GUARDIAN 오류 목록* [{status_filter}] {len(errors)}건"]
            for e in errors:
                icon = sev_icon.get(e.get("severity", "medium"), "ℹ️")
                ts   = (e.get("timestamp") or "")[:16]
                msg  = (e.get("message") or "")[:80]
                lines.append(
                    f"{icon} `#{e['id']}` [{e.get('severity','?')}] {e.get('error_type','?')}\n"
                    f"   {ts} | {e.get('module','?')}\n"
                    f"   {msg}"
                )
            _send_tg("\n\n".join(lines))
        except Exception as e:
            _send_tg(f"⚠️ /errors 실패: {e}")
        return

    if cmd == "/errors_stats":
        try:
            from shared import db as _db
            s = _db.get_error_stats(days=7)
            by_status = s.get("by_status", {})
            by_sev    = s.get("by_severity", {})
            lines = [
                "🛡️ *GUARDIAN 오류 통계 (최근 7일)*",
                f"총계: *{s.get('total', 0)}건*",
                "",
                "📋 상태별",
                f"  신규: {by_status.get('new', 0)}",
                f"  분석 중: {by_status.get('analyzing', 0)}",
                f"  자동수정 완료: {by_status.get('fixed', 0)}",
                f"  수정 불가: {by_status.get('wontfix', 0)}",
                f"  무시됨: {by_status.get('ignored', 0)}",
                "",
                "🔥 심각도별",
                f"  🚨 CRITICAL: {by_sev.get('critical', 0)}",
                f"  ⚠️ HIGH: {by_sev.get('high', 0)}",
                f"  ℹ️ MEDIUM: {by_sev.get('medium', 0)}",
                f"  🔵 LOW: {by_sev.get('low', 0)}",
            ]
            _send_tg("\n".join(lines))
        except Exception as e:
            _send_tg(f"⚠️ /errors_stats 실패: {e}")
        return

    # ── JARVIS02 외부 발행 (★ 2026-06-28 — 승인 게이트 필수: 실제 발행 동작) ──
    if cmd in _J02_EXT_CMDS:
        key = f"{cmd.lstrip('/')}:{abs(hash(text)) & 0xFFFF:04x}"
        _PENDING_J02_CMD[key] = {"cmd": cmd}
        _send_tg_buttons(
            f"🔒 *승인 필요 — 외부 발행*\n{_J02_EXT_CMDS[cmd]}\n\n"
            f"실행 시 네이버·티스토리에 *실제 발행* 됩니다. 진행할까요?",
            [[{"text": "✅ 발행", "callback_data": f"j02cmd_yes:{key}"},
              {"text": "❌ 취소", "callback_data": f"j02cmd_no:{key}"}]],
        )
        return

    # ── JARVIS03 RADAR 조회 (읽기 전용 — 승인 불필요) ──
    if cmd in ("/trend", "/radar", "/report"):
        _handle_radar_query(cmd)
        return

    # ── JARVIS08 쿠키 갱신 (유지보수 — 직접) ──
    if cmd in ("/refresh_naver", "/refresh_tistory"):
        _handle_cookie_refresh(cmd)
        return

    _sched = _get_sched()
    if _sched:
        try:
            _sched.handle_telegram_command(text.strip())
        except Exception as e:
            log.warning(f"[봇] JARVIS02 명령 처리 오류: {e}")
            _g_report("infra", e, module=__name__)
            _send_tg(f"❌ 명령어 처리 오류: {e}")
    else:
        _send_tg("⚠️ JARVIS02 스케줄러가 로드되지 않았습니다.")


def _handle_jarvis02_callback(cq: dict):
    """JARVIS03 인라인 버튼 콜백 처리 (approval_bot 로직 내장)."""
    sys.path.insert(0, str(RADAR_DIR))
    from JARVIS03_RADAR.approval_bot import _handle_callback
    _handle_callback(cq)


# ════════════════════════════════════════════════════════════
# 메인 polling 루프
# ════════════════════════════════════════════════════════════

def run_bot_polling(shutdown_event: threading.Event):
    """단일 통합 텔레그램 봇 — JARVIS02 명령어 + JARVIS03 인라인 버튼.

    shutdown_event: daemon 의 _daemon_shutdown — 세트 시 루프 종료.
    """
    if not TG_TOKEN:
        log.warning("⚠️ TELEGRAM_TOKEN 없음 — 봇 비활성")
        return

    log.info("📲 통합 텔레그램 봇 시작")
    _clear_webhook()
    time.sleep(2)

    _409_count = 0
    _conn_fail_count = 0  # ★ ERRORS [274] — DNS/연결 오류 연속 횟수 (backoff용)

    offset = 0
    try:
        _r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": -1, "timeout": 0},
            timeout=10,
        )
        _results = _r.json().get("result", [])
        if _results:
            offset = _results[-1]["update_id"] + 1
            log.info(f"  [봇] 시작 offset={offset} (과거 메시지 skip)")
    except Exception:
        pass

    while not shutdown_event.is_set():
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
                params={
                    "offset": offset, "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                },
                timeout=35,
            )

            if resp.status_code == 409:
                _409_count += 1
                if _409_count == 1:
                    log.warning("⚠️ 409 Conflict — webhook 제거 후 재시도")
                    _clear_webhook()
                    time.sleep(5)
                else:
                    wait = min(60 * _409_count, 300)
                    log.warning(f"⚠️ 409 반복 ({_409_count}회) — {wait}초 대기")
                    time.sleep(wait)
                continue
            _409_count = 0

            if resp.status_code != 200:
                log.warning(f"[봇] getUpdates {resp.status_code}")
                time.sleep(5)
                continue

            for upd in resp.json().get("result", []):
                offset = upd["update_id"] + 1

                # ── 텍스트 메시지 처리 ─────────────────────────
                if "message" in upd:
                    msg     = upd["message"]
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text    = msg.get("text", "")
                    if not text or chat_id != TG_CHAT_ID:
                        pass
                    elif text.startswith("/"):
                        cmd_log = text.split()[0]
                        log.info(f"  [봇] 명령어 수신: {cmd_log}")
                        try:
                            _dispatch_text_command(text)
                        except Exception as e:
                            log.warning(f"[봇] 명령 오류 ({cmd_log}): {e}")
                            _g_report("infra", e, module=__name__)
                            _send_tg(f"❌ 명령 처리 오류 ({cmd_log}): {e}")
                    else:
                        log.info(f"  [봇] 자유문장 수신: {text[:40]}")
                        try:
                            _route_free_text(text, session_id=chat_id)
                        except Exception as e:
                            log.warning(f"[봇] 자유문장 라우팅 오류: {e}")
                            _g_report("infra", e, module=__name__)
                            _send_tg(f"❌ 처리 오류: {e}")

                # ── 인라인 버튼 콜백 ───────────────────────────
                elif "callback_query" in upd:
                    cq      = upd["callback_query"]
                    cq_id   = cq.get("id", "")
                    cq_data = cq.get("data", "")
                    try:
                        if cq_data.startswith("j00_yes:"):
                            key = cq_data[len("j00_yes:"):]
                            _answer_callback(cq_id, "실행합니다!")
                            _execute_j00_approval(key)
                        elif cq_data.startswith("j00_no:"):
                            key = cq_data[len("j00_no:"):]
                            _PENDING_J00.pop(key, None)
                            _answer_callback(cq_id, "취소했습니다.")
                            _send_tg("❌ 취소되었습니다.")
                        elif cq_data.startswith("j00r_yes:"):
                            key = cq_data[len("j00r_yes:"):]
                            _answer_callback(cq_id, "ReAct 도구 실행!")
                            _execute_j00_react_approval(key)
                        elif cq_data.startswith("j00r_no:"):
                            key = cq_data[len("j00r_no:"):]
                            _PENDING_J00_REACT.pop(key, None)
                            _answer_callback(cq_id, "취소했습니다.")
                            _send_tg("❌ ReAct 도구 호출 취소됨.")
                        elif cq_data.startswith("plan_yes:"):
                            plan_id = cq_data[len("plan_yes:"):]
                            _answer_callback(cq_id, "계획 실행!")
                            threading.Thread(
                                target=_execute_plan, args=(plan_id,),
                                daemon=True, name=f"plan_{plan_id}",
                            ).start()
                        elif cq_data.startswith("plan_no:"):
                            plan_id = cq_data[len("plan_no:"):]
                            _PENDING_J00_PLAN.pop(plan_id, None)
                            _answer_callback(cq_id, "취소했습니다.")
                            _send_tg("❌ 계획 취소됨.")
                        elif cq_data.startswith("pm_batch_yes:"):
                            batch_id = cq_data[len("pm_batch_yes:"):]
                            _answer_callback(cq_id, "수정을 시작합니다!")
                            def _run_batch(bid=batch_id):
                                try:
                                    from JARVIS01_MASTER.proactive_monitor import execute_batch_fix
                                    execute_batch_fix(bid)
                                except Exception as e:
                                    _send_tg(f"❌ 배치 수정 실패: {e}")
                            threading.Thread(
                                target=_run_batch, daemon=True, name=f"pm_batch_{batch_id}",
                            ).start()
                        elif cq_data.startswith("pm_batch_no:"):
                            batch_id = cq_data[len("pm_batch_no:"):]
                            try:
                                from JARVIS01_MASTER.proactive_monitor import _PENDING_PM
                                _PENDING_PM.pop(batch_id, None)
                            except Exception:
                                pass
                            _answer_callback(cq_id, "무시했습니다.")
                            _send_tg("🔕 자가진단 묶음 무시됨.")
                        elif cq_data.startswith("pm_yes:"):
                            fix_id = cq_data[len("pm_yes:"):]
                            _answer_callback(cq_id, "수정을 시작합니다!")
                            threading.Thread(
                                target=_execute_pm_fix, args=(fix_id,),
                                daemon=True, name=f"pm_fix_{fix_id}",
                            ).start()
                        elif cq_data.startswith("pm_no:"):
                            fix_id = cq_data[len("pm_no:"):]
                            try:
                                from JARVIS01_MASTER.proactive_monitor import _PENDING_PM
                                _PENDING_PM.pop(fix_id, None)
                            except Exception:
                                pass
                            _answer_callback(cq_id, "무시했습니다.")
                            _send_tg("🔕 자가진단 항목 무시됨.")
                        elif cq_data.startswith("j02cmd_yes:"):
                            key  = cq_data[len("j02cmd_yes:"):]
                            pend = _PENDING_J02_CMD.pop(key, None)
                            _answer_callback(cq_id, "발행을 시작합니다!")
                            if pend:
                                _send_tg(f"🚀 발행 시작: `{pend['cmd']}` (백그라운드 실행)")
                                _s = _get_sched()
                                if _s:
                                    _s.handle_telegram_command(pend["cmd"])
                                else:
                                    _send_tg("⚠️ JARVIS02 스케줄러 미로드 — 발행 불가")
                            else:
                                _send_tg("⚠️ 만료된 승인 요청입니다. 명령을 다시 입력하세요.")
                        elif cq_data.startswith("j02cmd_no:"):
                            key = cq_data[len("j02cmd_no:"):]
                            _PENDING_J02_CMD.pop(key, None)
                            _answer_callback(cq_id, "취소했습니다.")
                            _send_tg("❌ 발행 취소됨.")
                        else:
                            _handle_jarvis02_callback(cq)
                    except Exception as e:
                        log.warning(f"[봇] 콜백 오류: {e}")
                        _g_report("infra", e, module=__name__)
                        _answer_callback(cq_id, "처리 오류")

        except requests.exceptions.Timeout:
            _conn_fail_count = 0
        except requests.exceptions.ConnectionError as e:
            # ★ ERRORS [274] — DNS/연결 오류 backoff (Wi-Fi 재연결 시 폭주 방지)
            _conn_fail_count += 1
            wait = min(10 * _conn_fail_count, 120)  # 최대 2분
            if not shutdown_event.is_set():
                log.warning(f"[봇] 연결 오류 ({_conn_fail_count}회) — {wait}초 후 재시도")
                if _conn_fail_count == 1:  # 첫 번째만 Guardian 보고
                    _g_report("infra", e, module=__name__)
                time.sleep(wait)
        except Exception as e:
            _conn_fail_count = 0
            if not shutdown_event.is_set():
                log.warning(f"[봇] polling 오류: {e}")
                _g_report("infra", e, module=__name__)
                time.sleep(5)

    log.info("📲 통합 텔레그램 봇 종료")


__all__ = [
    "run_bot_polling", "_send_tg", "_send_tg_buttons", "_answer_callback",
    "_PENDING_J00", "_PENDING_J00_REACT", "_PENDING_J00_PLAN",
]
