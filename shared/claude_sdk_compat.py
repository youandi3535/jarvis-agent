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
    text = run_sdk_query(              # model 생략 = shared/llm.MODELS 기본값 파생
        prompt="...", max_turns=60, cwd=str(ROOT), timeout=1200,
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


# ── SDK 예외 이름은 **항상 바인딩된다** (2026-08-17 — 잠복 결함 수정) ────────
#   ★ 왜 모듈 레벨인가: 종전엔 이 세 이름을 `run_sdk_query` 의 `try:` **안에서** import
#     했는데, 그 import 자체가 실패하는 경우(= claude_code_sdk 미설치)에는 이름이
#     바인딩되지 않은 채 아래 `except CLINotFoundError` 절이 그것을 참조했다.
#     파이썬은 except 절을 순서대로 *평가* 하므로 함수 지역이름 조회에서
#     `UnboundLocalError: local variable 'CLINotFoundError' referenced before assignment`
#     가 나고 — **예외 처리기 자체가 터진다.** 즉 "SDK 가 없다" 는 흔한 환경이
#     계약(`error_kind` dict)이 아니라 생짜 예외로 호출자에게 튀었다.
#     이름은 예외를 *잡는 쪽* 이 항상 갖고 있어야 한다. 폴백 클래스는 절대 raise 되지
#     않으므로 잡는 동작을 바꾸지 않는다 — 오직 *이름 조회* 만 성립시킨다.
#   ★ 미설치 환경의 반환 규약: 새 error_kind 를 만들지 않는다. import 실패는 아래
#     `except Exception` 으로 떨어져 `error_kind="sdk_error"` 가 된다(계약 그대로).
#     `cli_not_found` 로 적지 않는 이유 — 그 kind 를 받은 소비자
#     (`auto_repair._send_tg`)가 "claude 바이너리 PATH 미등록" 이라는 **틀린 처방** 을
#     사람에게 보낸다. 패키지 부재를 PATH 문제로 안내하면 진단이 한 번 더 꼬인다.
try:                                                    # pragma: no cover - 환경 분기
    from claude_code_sdk._errors import (                # type: ignore[attr-defined]
        CLINotFoundError, MessageParseError, ProcessError,
    )
except Exception:                                       # noqa: BLE001
    class CLINotFoundError(Exception):                   # type: ignore[no-redef]
        """claude_code_sdk 부재 시 자리표 — raise 되지 않는다(이름 바인딩 전용)."""

    class MessageParseError(Exception):                  # type: ignore[no-redef]
        """〃"""

    class ProcessError(Exception):                       # type: ignore[no-redef]
        """〃"""


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

# ★ 텔레메트리 소스 태그 — **이 이름의 주인은 여기다** (사용자 박제 2026-08-12)
#   `llm_token_usage.source` 에 박히는 값이고, 소비자(비용 집계·예산 가드)가 이 문자열로
#   조회한다. 종전엔 기록부(:225)와 조회부(repair_budget)에 리터럴이 각각 있어, 태그가
#   바뀌면 비용이 조용히 $0.00 이 되고 금액 상한이 영원히 안 무는 구조였다.
USAGE_SOURCE = "sdk_query"

# ★ 쓰기범위 가드 노브 이름의 **주인은 `JARVIS07_GUARDIAN.sdk_tool_guard.MODE_ENV_KEY`** 다.
#   여기 사본이 있는 유일한 이유는 위 fail-closed 탈출구 — 주인 모듈을 import 하지 못하는
#   상황에서도 `off` 는 읽혀야 한다. 어긋남은 precommit `sdkwrite/knob-mismatch` 가 잡는다.
_GUARD_MODE_ENV = "GUARDIAN_SDK_TOOL_GUARD"

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


def _record_sdk_usage(meter: dict, ok: bool, model: str = "") -> None:
    """`run_sdk_query` 소비를 장부에 박제 — 계측 단일 진입점(`token_usage.record_call`) 경유.

    ★ alias 는 `shared.llm._CURRENT_ALIAS` 에서 가져온다(문자열 박제 금지). 이 경로는
      `invoke_text` 밖에서도 불리므로 비어 있을 수 있고, 그때는 `sdk_query` 로 표기해
      **'어디서 왔는지 모름' 과 '0' 을 구분** 한다.

    ★ `model` 은 **호출자가 넘긴다** (2026-08-09 정정 — ERRORS [592]).
      종전엔 `model=""` 이 코드에 박혀 있었다. 값이 없어서가 아니라 **안 넘겨서** 비었다 —
      `run_sdk_query` 는 145줄 위에서 이미 `shared.llm.model_id()` 로 모델을 정하고
      SDK 옵션에는 제대로 실어 보내면서, 장부에만 빈 문자열을 적고 있었다.
      그 결과 이 경로(전체 캐시 읽기의 50.7%)만 "어느 모델이 썼는지" 를 알 수 없어
      모델 교체 전후 비교가 불가능했다.
    """
    try:
        from shared.token_usage import record_call
        try:
            from shared.llm import _CURRENT_ALIAS
            alias = _CURRENT_ALIAS.get() or "sdk_query"
        except Exception:                                   # noqa: BLE001
            alias = "sdk_query"
        record_call(
            alias=alias, model=model or "", usage=meter.get("usage"),
            cost_usd=meter.get("cost") or 0.0, duration_ms=meter.get("dur") or 0,
            num_turns=meter.get("turns") or 0, ok=ok, source=USAGE_SOURCE,
        )
    except Exception:                                       # noqa: BLE001
        pass


def run_sdk_query(
    prompt: str,
    model: str | None = None,   # None = shared/llm.MODELS 에서 파생 (ID 리터럴 금지)
    *,
    cwd: str | None = None,
    max_turns: int | None = None,
    permission_mode: str = "default",
    timeout: int = 300,
    background: bool = False,
    extra_env: dict[str, str] | None = None,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    """claude_code_sdk.query 동기 래퍼 — 모든 오류 통합 처리.

    Returns:
      {
        "returncode": 0 (성공) | -1 (cli_not_found) | -2 (timeout) | -3 (sdk_error),
        "stdout":      수집된 텍스트,
        "stderr":      오류 요약,
        "elapsed":     초,
        "error_kind":  None | "cli_not_found" | "timeout" | "auth_error" | "sdk_error"
                       | "deferred" | "guard_error",
      }

    `disallowed_tools` (2026-08-14 T2 — 자율 수리 세션의 쓰기 범위를 *좁히는* 파라미터):
      미지정이면 `JARVIS07_GUARDIAN.sdk_tool_guard` 가 보호 목록에서 **파생** 한다.
      호출자가 명시하면 그것도 함께 건다(덮어쓰지 않는다 — 좁히는 방향으로만 합친다).
      종전엔 이 파라미터가 아예 없어서 `bypassPermissions` 세션이 헌법 문서·오류 기록·
      학습 원장·자기 브레이크 모듈까지 편집할 수 있었다.

    *호출자는 returncode 만 확인하면 됨* — MessageParseError / ProcessError /
    CLINotFoundError / TimeoutError 같은 라이브러리 내부 예외는 여기서 다 흡수.
    """
    import time as _time

    # ★ 발행창 보류 — 네 통로 공통 판정 (사용자 박제 2026-07-25).
    #   이 문이 무방비여서 GUARDIAN auto_repair(Tier-2, timeout 1200s)가 발행 중에도
    #   Claude Code SDK 세션을 잡았다. `background=True` 인 호출만 보류하므로
    #   사용자 승인 기반 delegate_to_claude_code(background 미지정=False)는 영향 없다.
    try:
        from shared.llm import defer_reason as _defer_reason
        _sdk_why = _defer_reason(background=background)
    except Exception:
        _sdk_why = ""
    if _sdk_why:
        import logging as _lgs
        _lgs.getLogger("jarvis.llm").info(
            f"🛡 {_sdk_why} — 배경 SDK 세션 보류 (한도를 글 작성에 우선 배정)")
        return {"returncode": -4, "stdout": "", "stderr": f"보류: {_sdk_why}",
                "elapsed": 0.0, "error_kind": "deferred"}

    # ★ 자율 세션 쓰기 범위 (2026-08-14 T2) — 경계를 *모르면 띄우지 않는다*(fail-closed).
    #   판정·목록의 주인은 `JARVIS07_GUARDIAN.sdk_tool_guard` 하나다. 여기서는 묻기만 한다.
    #   ★ 아래 `_GUARD_MODE_ENV` 한 벌이 더 있는 이유: **주인을 못 읽는 상황이 곧 이
    #     탈출구가 필요한 상황** 이라 주인에게 물어볼 수가 없다. 두 값이 어긋나면
    #     precommit `sdkwrite/knob-mismatch` 가 잡는다(사본을 두되 기계가 감시한다).
    _guard: dict[str, Any] = {"mode": "off", "disallowed_tools": [], "env": {}}
    try:
        from JARVIS07_GUARDIAN.sdk_tool_guard import session_guard as _session_guard
        _guard = _session_guard()
    except Exception as _ge:                                # noqa: BLE001
        if (os.getenv(_GUARD_MODE_ENV) or "").strip().lower() != "off":
            log.error("[sdk_compat] 쓰기 범위 파생 실패 — 세션 미기동(fail-closed): %s", _ge)
            return {"returncode": -3, "stdout": "",
                    "stderr": f"guard_derivation_failed: {_ge}",
                    "elapsed": 0.0, "error_kind": "guard_error"}
        log.warning("[sdk_compat] 쓰기 범위 가드 off — 무가드 세션으로 진행")

    if not model:
        # 지연 import — shared.llm 이 이 모듈을 import 하므로 모듈 최상단은 순환.
        from shared.llm import model_id as _model_id
        model = _model_id()

    _ensure_runtime_env()
    env = build_oauth_env()
    if extra_env:
        env.update(extra_env)
    # 가드 표식은 **마지막에** — 호출자가 extra_env 로 표식을 덮어 우회하지 못하게.
    env.update(_guard.get("env") or {})

    t0 = _time.time()
    try:
        import anyio
        from claude_code_sdk import (
            query, ClaudeCodeOptions, AssistantMessage, TextBlock,
        )
        # ★ 예외 세 이름은 **모듈 레벨** 에서 이미 바인딩됐다(위 폴백 블록).
        #   여기서 다시 import 하면 그 순간 함수 지역이름이 되어, import 가 실패한
        #   경로에서 `except CLINotFoundError` 가 UnboundLocalError 로 터진다.

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
        # ★ 금지목록은 *합집합* — 호출자 명시분 + 가드 파생분(enforce 일 때만 채워진다).
        #   observe 는 비어 있다: 무엇이 막힐지 먼저 보고 사람이 승격한다(T2 (4)).
        _deny_tools = list(disallowed_tools or []) + list(_guard.get("disallowed_tools") or [])
        if _deny_tools:
            opts_kw["disallowed_tools"] = sorted(set(_deny_tools))

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
        # ★ 토큰 계측 (2026-07-26) — 종전 이 경로는 `record_call` 을 **한 번도 부르지 않았다**.
        #   그런데 여기로 도는 것이 auto_repair 심층감사·발행실패 즉시수정처럼 max_turns 가
        #   큰 *가장 무거운* 호출들이다. 장부에 0으로 적히니 "무엇을 줄여야 하나" 를 물어도
        #   답이 안 나왔다. 소비를 못 보면 절감도 증명할 수 없다.
        _meter = {"usage": None, "cost": 0.0, "dur": 0, "turns": 0}

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
                    elif type(msg).__name__ == "ResultMessage":
                        # usage 는 ResultMessage 에만 있다 (AssistantMessage 에는 필드 자체가 없음 — 실측 확인)
                        _meter["usage"] = getattr(msg, "usage", None)
                        _meter["cost"]  = float(getattr(msg, "total_cost_usd", 0) or 0)
                        _meter["dur"]   = int(getattr(msg, "duration_ms", 0) or 0)
                        _meter["turns"] = int(getattr(msg, "num_turns", 0) or 0)

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
        _sem_held = False
        if _sl is not None:
            try:
                _sl._pace_spawn()
                # ★ 배경 작업은 순번 대기에 상한 (ERRORS [474]) — 줄이 길면 포기하고 defer.
                #   긴급 경로(background=False)는 종전대로 끝까지 대기.
                _sem_wait = _sl.bg_sem_wait_max() if background else None
                _sem_held = _sl._acquire_llm_sem(timeout=_sem_wait)
                if not _sem_held:
                    return {
                        "returncode": -2, "stdout": "",
                        "stderr": f"llm_sem timeout ({_sem_wait:.0f}s 순번 대기 초과)",
                        "elapsed": int(_time.time() - t0), "error_kind": "deferred",
                    }
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
                if _sem_held:   # ★ 획득 못 했으면 반납 금지 (BoundedSemaphore 는 초과 release 시 예외)
                    try: _sl._LLM_SPAWN_SEM.release()
                    except Exception: pass

        stdout = "\n".join(_parts)
        # ★ 계측 박제 — 성공/부분수집 무관하게 항상. 실패해도 본류를 막지 않는다.
        _record_sdk_usage(_meter, ok=bool(stdout), model=model)

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

    # ★ 계약 보증 — **어떤 경로로도 None 을 반환하지 않는다** (2026-08-07 감사).
    #   docstring 이 "returncode/stdout/elapsed 를 담은 dict" 를 약속하는데, 함수가
    #   `try` 로 끝나 어떤 분기가 return 없이 빠지면 파이썬이 조용히 None 을 돌려준다.
    #   호출자는 `result["elapsed"]` 로 첨자 접근하므로 즉시
    #   `'NoneType' object is not subscriptable` 로 터진다 —
    #   실제로 GUARDIAN Tier-2 브리지가 21회 그렇게 죽었고, 삼키는 except 탓에
    #   **밴딧 보상 경로가 11일간 조용히 막혔다.**
    #   계약은 계약의 주인이 지킨다(①). 호출자마다 None 검사를 흩지 않는다.
    log.error("[sdk_compat] run_sdk_query 가 반환 없이 빠졌다 — 계약 위반 (fail-closed)")
    return {
        "returncode": -3, "stdout": "", "stderr": "run_sdk_query 반환 경로 누락",
        "elapsed": int(_time.time() - t0), "error_kind": "sdk_error",
    }


# ── 모듈 import 시 1회 자동 설치 ────────────────────────────────────────
_ensure_runtime_env()
_install_message_parser_patch()


__all__ = [
    "USAGE_SOURCE",
    "run_sdk_query",
    "build_oauth_env",
    "_EXTRA_PATHS",
]
