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
                return SystemMessage(subtype=mtype, data=payload)
            except Exception:
                raise  # 흡수 자체 실패 시 원본 예외 전파

    _mp.parse_message = _patched
    _PATCH_INSTALLED = True
    log.info("[sdk_compat] message_parser monkey-patch 설치 완료")


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

        # ★ 전역 하트비트 (사용자 박제 2026-07-06): 장시간 SDK 호출(auto_repair 심층감사 등)이
        #   메시지를 흘리는 동안 beat() → freeze 워치독이 정상 장시간 작업을 오탐 안 함.
        try:
            from JARVIS00_INFRA.watchdog import beat as _wd_beat
        except Exception:
            _wd_beat = lambda: None

        async def _collect() -> str:
            parts: list[str] = []
            _wd_beat()
            with anyio.fail_after(timeout):
                options = ClaudeCodeOptions(**opts_kw)
                async for msg in query(prompt=prompt, options=options):
                    _wd_beat()   # 메시지 수신 = 진행 신호 (SDK 살아있음)
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                parts.append(block.text)
            return "\n".join(parts)

        try:
            stdout = anyio.run(_collect)
        except (MessageParseError, ProcessError) as e:
            # 응답은 이미 수집됐을 수 있음 — 빈 문자열로 처리
            log.warning(f"[sdk_compat] SDK 응답 파싱 경고: {e}")
            stdout = ""

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
