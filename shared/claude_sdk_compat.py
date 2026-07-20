"""shared/claude_sdk_compat.py — Claude Code SDK 호환 단일 진입점.

★ 사용자 박제 2026-06-07 — Claude CLI 잔존 흔적 일소.

문제 인식:
  claude_code_sdk 는 wrapper 지만 내부에서 *claude CLI 바이너리를 subprocess 로 호출* 하고
  Anthropic 서버 응답을 메시지 파서로 decode 한다. 이 *내부 CLI/파서 layer* 가 데몬·cron
  환경에서 깨지는 3대 원인 — *모두 외부 코드에서 해결해야* 하는 것:

  1. **PATH 누수** — launchd/cron 에서 `/opt/homebrew/bin` 등이 PATH 에 없음.
     SDK 가 `claude` 바이너리를 못 찾고 `CLINotFoundError`.
  2. **ANTHROPIC_API_KEY 가짜 키 누수** — shared/llm.py 가 LangChain sentinel 로
     `os.environ.setdefault("ANTHROPIC_API_KEY", "max-...")` 박아둠. SDK subprocess 가
     이 가짜 키를 보고 API 모드 진입 → exit code 1 (잔액 0).
  3. **MessageParseError: Unknown message type: rate_limit_event** — Anthropic 이
     `rate_limit_event` 같은 새 system message 타입을 도입했는데 SDK 라이브러리는
     모름. *옛 라이브러리 = 옛 화이트리스트*. `.venv` 내부 수동 패치는 `pip install`
     로 사라지므로 *런타임 monkey-patch* 가 영구 해법.

이 모듈은 *모듈 import 시점에 단 1회* 모든 보장을 수행:
  - `_install_message_parser_patch()` — 미지 메시지 타입을 SystemMessage 로 흡수
  - `_ensure_runtime_env()` — PATH prepend
  - `run_sdk_query()` — 동기 wrapper. 모든 호출자가 이것만 쓰면 ProcessError·
    MessageParseError·CLINotFoundError·TimeoutError 통합 처리됨.

사용:
    from shared.claude_sdk_compat import run_sdk_query
    text = run_sdk_query(
        prompt="...", model="claude-sonnet-5",
        max_turns=60, cwd=str(ROOT), timeout=1200,
    )

CLAUDE.md `자율 코드 자가수정 규정` 의 *side_effect="internal"* 영역 —
파일 시스템·외부 API 호출 없음, 라이브러리 hook 만 설정.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.claude_sdk_compat")


# ── PATH 보장 ──────────────────────────────────────────────────────────
# macOS Homebrew (Intel/ARM) + npm-global + ~/.local 모두 커버.
# 한 곳에서만 관리 — auto_repair.py 3곳·incident_responder 등 *반드시* 이 리스트 참조.
_EXTRA_PATHS: list[str] = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    str(Path.home() / ".npm-global" / "bin"),
    str(Path.home() / ".local" / "bin"),
]


def _ensure_runtime_env() -> None:
    """데몬·cron 환경에서 claude 바이너리 탐색 보장.

    각 호출자가 PATH 를 수동 prepend 하던 것을 단일 진입점으로 흡수.
    """
    cur = os.environ.get("PATH", "")
    parts = cur.split(":") if cur else []
    new_parts = [p for p in _EXTRA_PATHS if p and p not in parts] + parts
    os.environ["PATH"] = ":".join(new_parts)


def build_oauth_env() -> dict[str, str]:
    """SDK subprocess 용 env dict — ANTHROPIC_API_KEY="" 강제 (OAuth 모드).

    shared/llm.py:25 가 LangChain sentinel 로 가짜 키를 박아두므로
    SDK 호출 직전에는 *반드시* 빈 문자열로 오버라이드해야 함.
    PATH 도 _EXTRA_PATHS prepend.
    """
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = ""
    # PATH 이중 보장 (모듈 import 후 호출자가 PATH 변경했을 수 있음)
    cur = env.get("PATH", "")
    parts = cur.split(":") if cur else []
    new_parts = [p for p in _EXTRA_PATHS if p and p not in parts] + parts
    env["PATH"] = ":".join(new_parts)
    return env


# ── MessageParseError monkey-patch ─────────────────────────────────────
# claude_code_sdk `_internal/message_parser.parse_message` 는 type 화이트리스트
# 매칭 — 미지 type 은 MessageParseError. Anthropic 이 새 system message
# (rate_limit_event 등) 도입하면 SDK 업데이트 전까지 query 루프 중단.
# 우리 monkey-patch: 미지 type 을 SystemMessage 로 흡수 → 루프 계속.

_PATCH_INSTALLED = False


def _install_message_parser_patch() -> None:
    """parse_message 를 감싸서 미지 message type 을 SystemMessage 로 흡수.

    .venv 내부 수동 패치는 pip install 시 사라지므로 *런타임 monkey-patch* 만이
    영구 해법. 모듈 import 시 1회 실행.
    """
    global _PATCH_INSTALLED
    if _PATCH_INSTALLED:
        return
    try:
        from claude_code_sdk._internal import message_parser as _mp
        from claude_code_sdk._errors import MessageParseError
        from claude_code_sdk import SystemMessage
    except Exception as e:
        log.warning(f"[sdk_compat] claude_code_sdk import 실패 — patch 건너뜀: {e}")
        return

    _original = _mp.parse_message

    def _patched(data: Any):
        try:
            return _original(data)
        except MessageParseError as e:
            # "Unknown message type" 만 흡수 — 다른 파싱 오류는 그대로 전파
            msg = str(e)
            if "Unknown message type" not in msg:
                raise
            try:
                mtype = data.get("type", "unknown") if isinstance(data, dict) else "unknown"
                payload = data if isinstance(data, dict) else {}
                log.info(f"[sdk_compat] 미지 message type 흡수: {mtype}")
                # ★ rate_limit_event 는 Anthropic 이 주는 *한도·리셋 정보* 를 담는다.
                #   종전엔 타입명만 찍고 페이로드를 통째로 버려 사용량 관측이 불가능했다
                #   (ERRORS [456]). 원문을 DB 에 박제해 대시보드에서 확인 가능하게 한다.
                if mtype == "rate_limit_event":
                    try:
                        from shared.token_usage import record_rate_limit
                        record_rate_limit(payload, source="sdk_compat")
                    except Exception:
                        pass
                return SystemMessage(subtype=mtype, data=payload)
            except Exception:
                raise  # 흡수 자체 실패 시 원본 예외 전파

    _mp.parse_message = _patched

    # ★★ 바인딩된 참조까지 교체 (ERRORS [457] — 2026-07-20)
    #   `_internal/client.py` 는 `from .message_parser import parse_message` 로
    #   함수를 *모듈 로드 시점에 직접 바인딩* 한다. 따라서 message_parser 모듈의
    #   속성만 바꾸면 client 는 여전히 *원본* 을 호출 → 패치가 무력화된다.
    #   (오늘 아침 경제 브리핑 실패의 근본 원인: rate_limit_event 가 ResultMessage
    #    직전에 도착 → MessageParseError 로 스트림 중단 → 빈 응답 → topic_pack
    #    fail-closed. 한도는 46% 밖에 안 찼는데 '한도 소진' 으로 오진되었다.)
    #   pytrends 사례(ERRORS [455])와 동일한 monkey-patch 실패 클래스.
    import sys as _sys
    _rebound = 0
    for _name, _mod in list(_sys.modules.items()):
        if not _name.startswith("claude_code_sdk"):
            continue
        try:
            if getattr(_mod, "parse_message", None) is _original:
                setattr(_mod, "parse_message", _patched)
                _rebound += 1
        except Exception:
            continue

    _PATCH_INSTALLED = True
    log.info(f"[sdk_compat] message_parser monkey-patch 설치 완료 "
             f"(바인딩 참조 {_rebound}곳 동시 교체)")


def patch_effective() -> bool | None:
    """패치가 *실제로 먹는지* 동작으로 확인 (설치 플래그가 아니라).

    ★ 왜 필요한가 (ERRORS [457]):
      `_PATCH_INSTALLED = True` 는 "설치를 시도했다" 는 뜻일 뿐 "모두가 새 함수를
      쓴다" 는 보장이 아니다. `client.py` 가 `from .message_parser import parse_message`
      로 원본을 *미리 복사* 해뒀다면 패치는 설치돼도 무력하다. 실제로 그 상태로
      수일간 모든 LLM 호출이 빈 응답을 냈고, 플래그는 내내 True 였다.

    그래서 여기서는 *실제 소비자가 쓰는 경로* 로 가짜 rate_limit_event 를 한 번
    통과시켜 본다. 예외가 안 나면 유효, 나면 무력.

    반환: True(유효) / False(무력 — 즉시 수리 필요) / None(판정 불가)
    """
    try:
        from claude_code_sdk._internal import client as _cl
    except Exception:
        return None
    fn = getattr(_cl, "parse_message", None)   # ★ 소비자가 실제로 부르는 그 참조
    if fn is None:
        return None
    try:
        # ★ `__smoke__` 표식 — record_rate_limit 이 이 합성 입력을 *박제하지 않도록*.
        #   (표식 없이 던지면 검사용 가짜 이벤트가 진짜처럼 DB 에 쌓여 한도 이력을
        #    오염시킨다 — 실제로 그렇게 되어 사용자가 발견. 관측 도구가 관측 대상을
        #    더럽히면 안 된다.)
        fn({"type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed"}, "__smoke__": True})
        return True
    except Exception:
        return False


# ── 동기 query wrapper — 모든 호출자 단일 진입점 ────────────────────────


def run_sdk_query(
    prompt: str,
    model: str = "claude-sonnet-5",
    *,
    cwd: str | None = None,
    max_turns: int | None = None,
    permission_mode: str = "default",
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
    allowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    """claude_code_sdk.query 동기 래퍼 — 모든 오류 통합 처리.

    Returns:
      {
        "returncode": 0 (성공) | -1 (cli_not_found) | -2 (timeout) | -3 (sdk_error),
        "stdout":      수집된 텍스트,
        "stderr":      오류 요약,
        "elapsed":     초,
        "error_kind":  None | "cli_not_found" | "timeout" | "auth_error" | "sdk_error",
      }

    *호출자는 returncode 만 확인하면 됨* — MessageParseError / ProcessError /
    CLINotFoundError / TimeoutError 같은 라이브러리 내부 예외는 여기서 다 흡수.
    """
    import time as _time

    _ensure_runtime_env()
    env = build_oauth_env()
    if extra_env:
        env.update(extra_env)

    t0 = _time.time()
    try:
        import anyio
        from claude_code_sdk import (
            query, ClaudeCodeOptions, AssistantMessage, TextBlock,
        )
        from claude_code_sdk._errors import (
            MessageParseError, ProcessError, CLINotFoundError,
        )

        opts_kw: dict[str, Any] = {
            "model": model,
            "permission_mode": permission_mode,
            "env": env,
        }
        if cwd:
            opts_kw["cwd"] = cwd
        if max_turns is not None:
            opts_kw["max_turns"] = max_turns
        if allowed_tools:
            opts_kw["allowed_tools"] = allowed_tools

        # ★ 전역 하트비트 (사용자 박제 2026-07-06): 장시간 SDK 호출(auto_repair 심층감사 등)이
        #   메시지를 흘리는 동안 beat() → freeze 워치독이 정상 장시간 작업을 오탐 안 함.
        try:
            from JARVIS00_INFRA.watchdog import beat as _wd_beat
        except Exception:
            _wd_beat = lambda: None

        # ★ FIX[2] (전수감사 2026-07-17): bare anyio.run 을 shared/llm._run_sdk_sync 와 동일하게
        #   하드닝. ① ThreadPoolExecutor + fut.result(timeout) 벽시계 상한 — blocking-I/O 가
        #   anyio.fail_after 를 관통해도 강제 포기 ② 매 호출 새 이벤트루프 — 'Loop is closed'
        #   재사용 오염 차단 ③ shared.llm 스폰 직렬화(세마포어+크로스프로세스 fcntl 락)에 합류 —
        #   auto_repair 심층감사 CLI spawn 과 writer invoke_text 가 같은 Max burst 를 직렬화(무력화
        #   방지). shared.llm 은 지연 import(llm.py 가 compat 을 import 하므로 순환 회피).
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
        import asyncio as _aio

        _parts: list[str] = []
        _err_box = {"exc": None}

        async def _collect() -> None:
            _wd_beat()
            with anyio.fail_after(timeout):
                options = ClaudeCodeOptions(**opts_kw)
                async for msg in query(prompt=prompt, options=options):
                    _wd_beat()   # 메시지 수신 = 진행 신호 (SDK 살아있음)
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                _parts.append(block.text)

        def _run_blocking() -> None:
            _aio.set_event_loop(_aio.new_event_loop())   # 재사용 오염 차단
            try:
                anyio.run(_collect)
            except (MessageParseError, ProcessError) as e:
                log.warning(f"[sdk_compat] SDK 응답 파싱 경고: {e}")   # 부분 응답 사용
            except BaseException as e:   # CLINotFound·Timeout·auth 등 — 상위서 error_kind 분류
                _err_box["exc"] = e

        # spawn 직렬화 합류 (shared.llm 과 동일 세마포어·크로스프로세스 락)
        try:
            from shared import llm as _sl
        except Exception:
            _sl = None
        if _sl is not None:
            try:
                _sl._pace_spawn()
                _sl._acquire_llm_sem()
            except Exception:
                _sl = None
        _proc_locked = False
        try:
            if _sl is not None:
                try:
                    _proc_locked = bool(_sl._proc_lock_acquire(timeout=timeout))
                except Exception:
                    _proc_locked = False
                if not _proc_locked:
                    log.warning(f"[sdk_compat] 크로스프로세스 잠금 {timeout}s 초과 — 포기(hang 취급)")
                    return {
                        "returncode": -2, "stdout": "", "stderr": "proc_lock timeout",
                        "elapsed": int(_time.time() - t0), "error_kind": "timeout",
                    }
            exe = ThreadPoolExecutor(max_workers=1)
            try:
                fut = exe.submit(_run_blocking)
                wall_deadline = timeout + 30.0
                waited = 0.0
                poll = 15.0
                while True:
                    try:
                        fut.result(timeout=min(poll, max(0.1, wall_deadline - waited)))
                        break
                    except _FutTimeout:
                        waited += poll
                        _wd_beat()
                        if waited >= wall_deadline:
                            log.warning(f"[sdk_compat] SDK 벽시계 상한 {wall_deadline:.0f}s 초과 — 강제 포기(수집 {len(_parts)}개)")
                            break
            finally:
                exe.shutdown(wait=False)   # 내부 스레드 leak 가능 — 비블로킹 우선
        finally:
            if _sl is not None:
                if _proc_locked:
                    try: _sl._proc_lock_release()
                    except Exception: pass
                try: _sl._LLM_SPAWN_SEM.release()
                except Exception: pass

        stdout = "\n".join(_parts)
        _exc = _err_box["exc"]
        if _exc is not None and not stdout:
            raise _exc   # 상위 except 로 error_kind 분류 (cli_not_found/timeout/auth/sdk_error)

        return {
            "returncode": 0,
            "stdout": stdout or "",
            "stderr": "",
            "elapsed": int(_time.time() - t0),
            "error_kind": None,
        }

    except CLINotFoundError as e:
        log.error(f"[sdk_compat] claude 바이너리 미발견 — PATH={os.environ.get('PATH','')[:200]}")
        return {
            "returncode": -1, "stdout": "", "stderr": f"cli_not_found: {e}",
            "elapsed": int(_time.time() - t0), "error_kind": "cli_not_found",
        }
    except TimeoutError:
        return {
            "returncode": -2, "stdout": "", "stderr": f"timeout ({timeout}s 초과)",
            "elapsed": int(_time.time() - t0), "error_kind": "timeout",
        }
    except Exception as e:
        emsg = str(e).lower()
        kind = "sdk_error"
        if "credit" in emsg or "balance" in emsg or "api key" in emsg:
            kind = "auth_error"
        log.error(f"[sdk_compat] SDK 예외 ({kind}): {e}")
        return {
            "returncode": -3, "stdout": "", "stderr": f"{type(e).__name__}: {e}",
            "elapsed": int(_time.time() - t0), "error_kind": kind,
        }


# ── 모듈 import 시 1회 자동 설치 ────────────────────────────────────────
_ensure_runtime_env()
_install_message_parser_patch()


__all__ = [
    "run_sdk_query",
    "build_oauth_env",
    "_EXTRA_PATHS",
]
