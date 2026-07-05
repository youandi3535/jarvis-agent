"""shared/llm.py — Claude LLM 단일 진입점 (claude-code-sdk 기반).

Max 구독 사용 — 외부 API 비용 0.

사용:
    from shared.llm import invoke_text
    text = invoke_text("writer", "프롬프트")

    from shared.llm import chat
    llm = chat("router").bind_tools(my_tools)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")


# LangChain/CrewAI 프로바이더 감지용 센티넬 — 실제 API 호출 금지
# SDK subprocess 에는 별도로 "" 오버라이드해서 OAuth 모드 강제 (아래 _run_sdk_sync 참조)
os.environ.setdefault("ANTHROPIC_API_KEY", "max-subscription-no-api-cost")

# ★ 사용자 박제 2026-06-07 — Claude CLI 잔존 흔적 일소.
# 모듈 import 시 단 1회 message_parser monkey-patch (rate_limit_event 등 미지 type 흡수)
# + PATH 보장 (/opt/homebrew/bin 자동 prepend). 데몬·cron 환경에서도 안전.
from shared import claude_sdk_compat as _sdk_compat  # noqa: E402,F401


# ── 모델 표준 ──────────────────────────────────────────────────

@dataclass(frozen=True)
class ModelSpec:
    alias: str           # 자비스 alias ("writer", "router" 등)
    model_id: str        # Claude 모델 ID
    max_tokens: int
    temperature: float
    description: str = ""


# ★ 모델 계층 — 사용자 박제 2026-07-04 (ADR 015): Sonnet 5 / Opus 4.8 2계층 단일화.
#   alias→model_id 매핑은 이 MODELS dict 가 시스템 전체의 유일 소스 — 다른 곳은 전부 파생.
# 자비스 모델 카탈로그 — 한 곳에서 관리
MODELS: dict[str, ModelSpec] = {
    "writer": ModelSpec(
        alias="writer",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="블로그 본문·도입부 생성 (Sonnet 5 — 복잡한 헌법 규정 준수용)",
    ),
    "writer_fast": ModelSpec(
        alias="writer_fast",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.4,
        description="짧은 본문·압축·재작성 (Sonnet 5)",
    ),
    "router": ModelSpec(
        alias="router",
        model_id="claude-sonnet-5",
        max_tokens=1000,
        temperature=0.0,
        description="마스터 라우터 — 인텐트 분류·도메인 매칭 (Sonnet 5)",
    ),
    "analyzer": ModelSpec(
        alias="analyzer",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="post_quality·daily_review 분석 (Sonnet 5)",
    ),
    # ★ 코드 수정·오류 분석 (Opus 4.8)
    "coder": ModelSpec(
        alias="coder",
        model_id="claude-opus-4-8",
        max_tokens=8000,
        temperature=0.1,
        description="코드 수정·patch 생성·자가수정 (Opus 4.8 — 최강 추론, 오류 수정 전용)",
    ),
    # ★ 오류 분석·패치 생성 (Opus 4.8)
    "guardian": ModelSpec(
        alias="guardian",
        model_id="claude-opus-4-8",
        max_tokens=8000,
        temperature=0.1,
        description="JARVIS07 오류 분석·패치 생성 (Opus 4.8 — 최강 추론)",
    ),
    # ★ 아키텍처 설계 (Opus 4.8 — 최신·최강)
    "architect": ModelSpec(
        alias="architect",
        model_id="claude-opus-4-8",
        max_tokens=10000,
        temperature=0.3,
        description="ARCHITECT 새 에이전트·시스템 설계 (Opus 4.8 — 최강 추론)",
    ),
    # ★ 복잡 진단·디버깅 (Opus 4.8)
    "diagnostic": ModelSpec(
        alias="diagnostic",
        model_id="claude-opus-4-8",
        max_tokens=6000,
        temperature=0.2,
        description="복잡 multi-cause traceback 진단·근본 원인 추론 (Opus 4.8)",
    ),
    # ★ 자가 학습 평가 게이트 (Opus 4.8) — eval_agent 가 llm_patch 등록 여부 채점
    "learn_eval": ModelSpec(
        alias="learn_eval",
        model_id="claude-opus-4-8",
        max_tokens=4000,
        temperature=0.1,
        description="learned_patterns 등록 게이트 — patch 안전성·정확성·재사용 가치 채점 (Opus 4.8)",
    ),
    # ★ 발행 전 품질 게이트 — 사실성 판정 (Opus 4.8, temp 0 결정성 우선)
    "fact_judge": ModelSpec(
        alias="fact_judge",
        model_id="claude-opus-4-8",
        max_tokens=4000,
        temperature=0.0,
        description="발행 전 사실성 검수 — claim 추출·출처 대조 판정 (Opus 4.8)",
    ),
    # ★ 발행 전 품질 게이트 — 유익성·매력도 채점 (Opus 4.8)
    "engagement_judge": ModelSpec(
        alias="engagement_judge",
        model_id="claude-opus-4-8",
        max_tokens=2500,
        temperature=0.2,
        description="발행 전 유익성·매력도 채점 — 독자 흡인력 judge (Opus 4.8)",
    ),
}

# 전 모듈 alias→model_id 단일 참조 — MODELS 에서 파생 (중복 리터럴 매핑 금지)
_ALIAS_MODEL: dict[str, str] = {alias: spec.model_id for alias, spec in MODELS.items()}
_DEFAULT_MODEL_ID = MODELS["writer"].model_id


# ════════════════════════════════════════════════════════════════
# ★ 표시용 모델 라벨 — 단일 진실 소스 파생 (SSOT, 사용자 박제 2026-07-04)
# ════════════════════════════════════════════════════════════════
# 웹 대시보드(hub.py)·텔레그램·문서가 모델명을 *하드코딩하지 말고* 이 함수로 파생.
# MODELS 한 곳만 바꾸면 모든 표시가 자동으로 따라온다 (2중·3중 수정 불필요).
# 하드코딩 금지는 precommit `ssot` 카테고리가 강제.

def pretty_model_id(model_id: str) -> str:
    """모델 ID → 사람이 읽는 라벨.

    'claude-opus-4-8'  → 'Opus 4.8'
    'claude-sonnet-5'  → 'Sonnet 5'
    """
    s = (model_id or "").replace("claude-", "")
    parts = [p for p in s.split("-") if p]
    if not parts:
        return model_id or "?"
    family = parts[0].capitalize()
    ver: list[str] = []
    for p in parts[1:]:
        if p.isdigit() and len(p) <= 2:   # 버전 조각만 (긴 날짜 접미사 제외)
            ver.append(p)
        else:
            break
    return (f"{family} {'.'.join(ver)}" if ver else family).strip()


def model_label(alias: str) -> str:
    """alias(writer/guardian/…) → 사람이 읽는 모델명. MODELS 에서 파생.

    코드가 모델을 바꾸면 이 라벨을 쓰는 모든 표시(웹·텔레그램)가 자동 갱신된다.
    표시 코드에 'Opus 4.x' 같은 리터럴을 직접 쓰지 말고 이 함수를 호출할 것.
    """
    return pretty_model_id(_ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID))


def get_spec(alias: str) -> ModelSpec:
    """alias 로 ModelSpec 조회."""
    if alias not in MODELS:
        raise KeyError(f"model alias '{alias}' 미등록. {list(MODELS.keys())}")
    return MODELS[alias]


def chat(alias: str = "writer", **overrides) -> Any:
    """LangChain BaseChatModel 호환 어댑터 반환.

    `.invoke(messages)` / `with_structured_output(Pydantic)` / `bind_tools(tools)`
    모두 Claude Code 위에 구현. LangGraph StateGraph 노드로 사용 가능.
    """
    try:
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa
    except ImportError:
        return None
    spec = get_spec(alias)
    return ClaudeSDKChatModel(
        alias=alias,
        model_id=spec.model_id,
        max_tokens=overrides.get("max_tokens", spec.max_tokens),
        temperature=overrides.get("temperature", spec.temperature),
    )


def is_langchain_available() -> bool:
    """LangChain core 사용 가능 여부."""
    try:
        from langchain_core.language_models.chat_models import BaseChatModel  # noqa
        return True
    except ImportError:
        return False


def _build_claude_sdk_chat_model():
    """LangChain 호환 chat model 클래스 lazy 빌드."""
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from pydantic import Field
    import json as _json
    import re as _re
    import uuid as _uuid

    class ClaudeSDKChatModel(BaseChatModel):
        """LangChain BaseChatModel 호환 — claude-code-sdk 위 구현.

        지원: .invoke / with_structured_output / bind_tools
        """
        alias: str = Field(default="writer")
        model_id: str = Field(default=_DEFAULT_MODEL_ID)
        max_tokens: int = Field(default=4000)
        temperature: float = Field(default=0.7)
        bound_tools: Optional[list] = Field(default=None)

        @property
        def _llm_type(self) -> str:
            return "claude-sdk"

        def _sdk_model(self) -> str:
            return _ALIAS_MODEL.get(self.alias, _DEFAULT_MODEL_ID)

        @staticmethod
        def _messages_to_prompt(messages) -> tuple[str, str]:
            """LangChain BaseMessage 리스트 → (system_prompt, user_prompt)."""
            sys_parts, user_parts = [], []
            for m in messages:
                content = getattr(m, "content", str(m))
                # content 가 list 일 때 (tool result 블록·멀티모달) → 텍스트 추출
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", str(b)) if isinstance(b, dict) else str(b)
                        for b in content
                    )
                content = str(content)
                if isinstance(m, SystemMessage):
                    sys_parts.append(content)
                else:
                    user_parts.append(content)
            return "\n\n".join(sys_parts), "\n\n".join(user_parts)

        def _tool_schema_injection(self) -> str:
            """bind_tools 결과 → prompt 안에 tool 스키마 주입."""
            if not self.bound_tools:
                return ""
            specs = []
            for t in self.bound_tools:
                name = getattr(t, "name", str(t))
                desc = getattr(t, "description", "")
                # args schema (LangChain Tool 의 args_schema 또는 .args)
                args = ""
                if hasattr(t, "args_schema") and t.args_schema:
                    try:
                        args = _json.dumps(
                            t.args_schema.model_json_schema().get("properties", {}),
                            ensure_ascii=False,
                        )[:400]
                    except Exception:
                        pass
                specs.append(f"- {name}: {desc[:200]} | args={args}")
            schema_block = "\n".join(specs)
            return (
                "\n\n[사용 가능한 도구]\n" + schema_block +
                '\n\n도구 호출이 필요하면 *마지막 줄* 에 JSON 으로:\n'
                '{"tool_calls": [{"name": "도구명", "args": {...}}]}\n'
                "도구 없이 답변 가능하면 평문으로만."
            )

        @staticmethod
        def _parse_tool_calls(text: str):
            """응답 text 끝의 JSON tool_calls 블록 추출."""
            # 마지막 { ... } JSON 블록
            m = _re.search(r'\{[^{}]*"tool_calls"\s*:\s*\[[^\]]*\][^{}]*\}\s*$',
                           text, _re.DOTALL)
            if not m:
                return None, text
            try:
                obj = _json.loads(m.group())
                tcs = obj.get("tool_calls", [])
                normalized = [
                    {"name": tc.get("name", ""),
                     "args": tc.get("args", {}) or {},
                     "id":   tc.get("id", str(_uuid.uuid4())[:8])}
                    for tc in tcs
                ]
                # 본문에서 JSON 부분 제거
                stripped = text[:m.start()].rstrip()
                return normalized, stripped
            except Exception:
                return None, text

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            system_prompt, user_prompt = self._messages_to_prompt(messages)
            tool_block = self._tool_schema_injection()
            if tool_block:
                system_prompt = (system_prompt + tool_block).strip()
            response_text = _run_sdk_sync(
                user_prompt, model=self._sdk_model(), system=system_prompt,
            ) or ""
            tool_calls, content = self._parse_tool_calls(response_text)
            msg = AIMessage(content=content, tool_calls=tool_calls or [])
            return ChatResult(generations=[ChatGeneration(message=msg)])

        def bind_tools(self, tools, **kwargs):
            """LangChain bind_tools — tool 리스트를 모델 상태에 박제."""
            return self.copy(update={"bound_tools": list(tools)})

    return ClaudeSDKChatModel


# ClaudeSDKChatModel 은 langchain_core import 시점에 클래스 정의
try:
    ClaudeSDKChatModel = _build_claude_sdk_chat_model()
except ImportError:
    ClaudeSDKChatModel = None  # langchain_core 미설치 환경 — chat() 가 None 반환


# ── 직접 호출 헬퍼 ────────────────────────────────────────────
# (alias→model_id 는 모듈 상단 _ALIAS_MODEL 단일 소스 — 여기 재정의 금지)


# ── LLM 호출 실패 근본 차단 (사용자 박제 2026-07-02) ──────────────────
#  ① embedded null byte: 수집 데이터(뉴스·웹)의 널바이트·제어문자가 프롬프트에 섞이면
#     claude CLI subprocess spawn 이 ValueError("embedded null byte") 로 크래시 → 사전 제거.
#  ② Max 구독 burst 초과: 발행이 claude CLI 를 동시에 여러 개(차트 4-way 등) spawn 하면
#     Max 구독 동시성 한도 초과 → CLI 가 모델 미호출(num_turns=0) 로 빈 응답 → 폴백.
#     단일 진입점에서 프로세스 전역 세마포어로 spawn 을 직렬화 → 각 호출이 실제 성공.
import re as _re_ctrl
import threading as _threading
import time as _time_pace

_CTRL_RE = _re_ctrl.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_prompt(s: str) -> str:
    """CLI subprocess spawn 안전 — 널바이트·제어문자 제거 (탭·개행·복귀는 보존)."""
    if not s:
        return s
    return _CTRL_RE.sub("", s)


# 동시 claude CLI spawn 상한 (기본 1 = 완전 직렬 — Max burst 안전). env 로 튜닝.
_LLM_MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "1") or "1"))
_LLM_SPAWN_SEM = _threading.BoundedSemaphore(_LLM_MAX_CONCURRENCY)
# spawn 간 최소 간격(초) — 기본 0(off). rate-limit 잦으면 0.5~1 로 상향.
_LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL_SEC", "0") or "0")
_LLM_PACE_LOCK = _threading.Lock()
_LLM_LAST_SPAWN = [0.0]

# ★ Rate-limit 회로 차단기 (ERRORS [288] — 2026-07-03)
# 연속 *진짜 스로틀* N회 시 open → 비필수 호출은 즉시 "" 반환 (재시도 0)
# 쿨다운 후 probe 1회(1샷) 허용 → 성공 시 close.
_CIRCUIT_THRESHOLD = int(os.getenv("LLM_CIRCUIT_THRESHOLD", "3") or "3")
_CIRCUIT_COOLDOWN_SEC = float(os.getenv("LLM_CIRCUIT_COOLDOWN_SEC", "90") or "90")
# 필수 alias 면제 셋 — open 중에도 1회 실시도 허용 (대본 본문·사실성 게이트가 "" 즉사
# → 발행 통째 실패로 번지는 것 방지). 장식성 호출(번역·라벨·태그)만 즉시 폴백.
_CIRCUIT_EXEMPT_ALIASES = {
    a.strip() for a in
    (os.getenv("LLM_CIRCUIT_EXEMPT", "writer,fact_judge,engagement_judge") or "").split(",")
    if a.strip()
}
_circuit_lock = _threading.Lock()
_circuit_consecutive_throttles = [0]
_circuit_open_since = [0.0]  # monotonic timestamp; 0 = closed
# ★ 직전 _run_sdk_sync 호출의 스로틀 여부 (스레드별) — CLI 부재·auth 오류 등
# 비스로틀 빈 응답으로 회로가 열리는 오탐 방지 (결함 b)
_LAST_CALL = _threading.local()


def _pace_spawn() -> None:
    """직전 spawn 과 최소 간격 유지 (burst rate-limit 완충). 기본 off."""
    if _LLM_MIN_INTERVAL <= 0:
        return
    with _LLM_PACE_LOCK:
        _now = _time_pace.monotonic()
        _wait = _LLM_LAST_SPAWN[0] + _LLM_MIN_INTERVAL - _now
        if _wait > 0:
            _time_pace.sleep(_wait)
            _now = _time_pace.monotonic()
        _LLM_LAST_SPAWN[0] = _now


async def _invoke_sdk_async(
    prompt: str,
    model: str = _DEFAULT_MODEL_ID,
    system: str = "",
) -> str:
    """claude-code-sdk 비동기 호출 — Max 구독 OAuth, API 비용 0."""
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError
    full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
    full_prompt = _sanitize_prompt(full_prompt)   # ★ embedded null byte 크래시 차단
    options = ClaudeCodeOptions(model=model, env={"ANTHROPIC_API_KEY": ""})
    parts: list[str] = []
    try:
        async for msg in query(prompt=full_prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
    except (MessageParseError, ProcessError):
        pass  # rate_limit_event 등 SDK 미지원 메시지 — 응답은 이미 수집됨
    return "".join(parts)


def _run_sdk_sync(
    prompt: str,
    model: str = _DEFAULT_MODEL_ID,
    system: str = "",
    timeout: int = 300,
) -> str:
    """claude-code-sdk 동기 래퍼 — 응답 수집 후 ProcessError/MessageParseError 무시."""
    import anyio
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError

    full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
    full_prompt = _sanitize_prompt(full_prompt)   # ★ embedded null byte 크래시 차단
    options = ClaudeCodeOptions(model=model, env={"ANTHROPIC_API_KEY": ""})
    parts: list[str] = []
    throttled = {"v": False}

    async def _collect():
        nonlocal parts
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full_prompt, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                # ★ Max 구독 burst 스로틀 감지 (사용자 박제 2026-07-01): rate-limit 시 CLI 는
                #   모델을 호출하지 않고 ResultMessage(num_turns=0, duration_api_ms=0, success)만
                #   흘려 *빈 응답* 을 낸다(예외 아님). 조용한 degrade 방지 위해 플래그로 표식.
                elif type(msg).__name__ == "ResultMessage" and getattr(msg, "num_turns", 1) == 0:
                    throttled["v"] = True

    # ★ 프로세스 전역 세마포어 — claude CLI 동시 spawn 직렬화 (Max burst 초과 방지)
    _pace_spawn()
    with _LLM_SPAWN_SEM:
        try:
            anyio.run(_collect)
        except (MessageParseError, ProcessError):
            pass  # rate_limit_event 또는 프로세스 종료 — 응답은 이미 수집됨
        except TimeoutError:
            print(f"  ⚠️ SDK timeout {timeout}s — 수집된 응답: {len(parts)}개")
        except Exception as e:
            if not parts:
                print(f"  ❌ SDK 오류: {e}")
    _was_throttled = bool(throttled["v"] and not parts)
    _LAST_CALL.throttled = _was_throttled   # ★ 호출자(invoke_text)가 진짜 스로틀만 카운트
    if _was_throttled:
        print("  ⏳ [LLM] rate-limit 스로틀 (num_turns=0, 모델 미호출) — 재시도/폴백")
    return "".join(parts)


def _invoke_sdk_vision(prompt: str, model: str, image_paths: list,
                       timeout: int = 180, cwd: str | None = None) -> str:
    """★ 비전(이미지 입력) SDK 호출 (사용자 박제 2026-07-05) — Read 도구로 이미지 파일 분석.

    invoke_text 는 텍스트 전용이라 이미지를 못 본다. SDK 가 구동하는 claude 에이전트에
    allowed_tools=['Read'] 를 주면 이미지 파일을 읽어 분석한다. 인포그래픽 디자인 학습 등
    실이미지 세밀 분석에 사용. permission_mode=bypassPermissions (Read 는 읽기전용, 안전).
    """
    import anyio
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError

    imgs = "\n".join(f"- {p}" for p in image_paths)
    full = _sanitize_prompt(f"다음 이미지 파일들을 Read 도구로 열어서 직접 보고 분석하라:\n{imgs}\n\n{prompt}")
    options = ClaudeCodeOptions(model=model, allowed_tools=["Read"],
                                permission_mode="bypassPermissions", max_turns=6,
                                cwd=cwd, env={"ANTHROPIC_API_KEY": ""})
    parts: list[str] = []

    async def _collect():
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)

    _pace_spawn()
    with _LLM_SPAWN_SEM:
        try:
            anyio.run(_collect)
        except (MessageParseError, ProcessError):
            pass
        except TimeoutError:
            print(f"  ⚠️ vision SDK timeout {timeout}s — 수집 {len(parts)}개")
        except Exception as e:
            if not parts:
                print(f"  ❌ vision SDK 오류: {e}")
    return "".join(parts)


def invoke_vision(alias: str, prompt: str, image_paths: list,
                  timeout: int = 180, cwd: str | None = None) -> str:
    """이미지 입력 LLM 단일 진입점 (SDK Read 도구). 텍스트 결과 반환. 실패/미가용 시 ""."""
    if not image_paths:
        return ""
    model = _ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID)
    try:
        return _invoke_sdk_vision(prompt, model, [str(p) for p in image_paths],
                                  timeout=timeout, cwd=cwd)
    except Exception as e:
        print(f"  ❌ invoke_vision 오류: {e}")
        return ""


def _circuit_record_throttle() -> None:
    """rate-limit 빈 응답 → 연속 카운터 증가, 임계 초과 시 회로 open."""
    with _circuit_lock:
        _circuit_consecutive_throttles[0] += 1
        if (_circuit_consecutive_throttles[0] >= _CIRCUIT_THRESHOLD
                and _circuit_open_since[0] == 0.0):
            import time as _tm
            _circuit_open_since[0] = _tm.monotonic()
            print(f"  🔴 [LLM] rate-limit 회로 차단 — 연속 {_circuit_consecutive_throttles[0]}회 throttle, "
                  f"{_CIRCUIT_COOLDOWN_SEC}s 쿨다운")


def _circuit_record_success() -> None:
    """정상 응답 → 회로 즉시 close."""
    with _circuit_lock:
        _circuit_consecutive_throttles[0] = 0
        _circuit_open_since[0] = 0.0


def _circuit_gate() -> str:
    """회로 상태 조회 + probe 획득 (★ 상태 전이 있음 — 순수 술어 아님).

    반환: 'closed' 정상 / 'open' 차단(비필수 호출 즉시 폴백) /
          'probe' 쿨다운 경과 — 이 호출 1회를 1샷 실시도로 허용 (open_since 리셋
          → 다음 probe 는 다시 쿨다운 후. 락 직렬화로 probe 폭주 없음).
    """
    with _circuit_lock:
        if _circuit_open_since[0] == 0.0:
            return "closed"
        import time as _tm
        elapsed = _tm.monotonic() - _circuit_open_since[0]
        if elapsed >= _CIRCUIT_COOLDOWN_SEC:
            _circuit_open_since[0] = _tm.monotonic()
            return "probe"
        return "open"


def invoke_text(alias: str, prompt: str, system: str = "", timeout: int = 300,
                _retries: int = 4, _essential: bool = False, **overrides) -> str:
    """Claude Code SDK 호출 단일 진입점.

    텍스트 생성(writer/router/analyzer): Sonnet 5
    오류수정·코드·설계(coder/guardian/architect/diagnostic/learn_eval): Opus 4.8

    ★ rate-limit 재시도 (사용자 박제 2026-07-01): 빈 응답이면 지수 백오프+지터로
      재시도. ★ 회로 차단기 (ERRORS [288] — 2026-07-03): 연속 *진짜 스로틀* ≥3 회 시
      쿨다운 동안 비필수 호출 즉시 "" 폴백. 필수 alias(_CIRCUIT_EXEMPT_ALIASES)와
      probe 는 1샷 실시도. ★ 데드라인 강등: JARVIS_LLM_DEADLINE_TS(epoch) 잔여 <10분
      이면 재시도 1회·백오프 0 — 발행(Layer 4) 시간 보호.
    """
    import time as _t, random as _r

    retries = max(1, _retries)
    backoff = True

    # ★ 글로벌 데드라인 강등 — 발행 파이프라인(economic_poster 등)이 설정
    try:
        _dl = float(os.environ.get("JARVIS_LLM_DEADLINE_TS", "0") or "0")
        if _dl and (_dl - _t.time()) < 600:
            retries, backoff = 1, False
    except Exception:
        pass

    # ★ 회로 차단기 게이트 (_essential=True 는 호출 단위 필수 면제 —
    #   설계 planner 등 품질 조타수 호출이 스로틀 중에도 1회 실시도, ERRORS [300])
    _gate = _circuit_gate()
    if _gate == "open":
        if _essential or alias in _CIRCUIT_EXEMPT_ALIASES:
            retries, backoff = 1, False   # 필수 호출 — open 중에도 1회 실시도
        else:
            print("  ⏳ [LLM] 회로 차단 중 — 즉시 폴백 (재시도 생략)")
            return ""
    elif _gate == "probe":
        retries, backoff = 1, False       # probe 는 1샷 — 최악 1 spawn 만 소모

    model = _ALIAS_MODEL.get(alias, _DEFAULT_MODEL_ID)
    result = ""
    throttled_seen = False
    for _attempt in range(retries):
        try:
            _LAST_CALL.throttled = False
            result = _run_sdk_sync(prompt, model=model, system=system, timeout=timeout) or ""
        except Exception:
            result = ""
        if result.strip():
            _circuit_record_success()
            return result
        if getattr(_LAST_CALL, "throttled", False):
            throttled_seen = True
        if backoff and _attempt < retries - 1:
            _t.sleep(min(30.0, 4 * (2 ** _attempt)) + _r.uniform(0, 1.5))
    # 모든 재시도 실패 — *진짜 스로틀* 관측 시에만 카운트 (CLI 부재·auth 오류 오탐 방지)
    if throttled_seen:
        _circuit_record_throttle()
    return result


class ClaudeSDKLLM:
    """CrewAI 호환 LLM 어댑터 — claude-code-sdk 경유.

    사용:
        from shared.llm import ClaudeSDKLLM
        researcher = Agent(
            role='리서처', goal=..., backstory=...,
            llm=ClaudeSDKLLM(alias='writer_fast', max_tokens=800),
        )
    """

    def __init__(self, alias: str = "writer", max_tokens: int | None = None,
                 temperature: float | None = None):
        spec = get_spec(alias)
        self.alias = alias
        self.model = spec.model_id
        self.max_tokens = max_tokens if max_tokens is not None else spec.max_tokens
        self.temperature = temperature if temperature is not None else spec.temperature
        self.api_key = ""
        self.stop = None

    @staticmethod
    def _format_messages(messages) -> tuple[str, str]:
        """CrewAI / LangChain message 리스트 → (system, user_prompt) 변환.

        지원 형식:
          - [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
          - 단일 문자열
        """
        if isinstance(messages, str):
            return "", messages
        system_parts: list[str] = []
        user_parts: list[str] = []
        for m in messages:
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
            elif isinstance(m, (list, tuple)) and len(m) == 2:
                role, content = m
            else:
                role, content = "user", str(m)
            if role == "system":
                system_parts.append(str(content))
            else:
                user_parts.append(str(content))
        return "\n\n".join(system_parts), "\n\n".join(user_parts)

    def call(self, messages, **_kwargs) -> str:
        """CrewAI 진입점."""
        model = _ALIAS_MODEL.get(self.alias, _DEFAULT_MODEL_ID)
        system, prompt = self._format_messages(messages)
        return _run_sdk_sync(prompt, model=model, system=system) or ""

    # LiteLLM 호환 entry point — CrewAI 의 일부 경로가 이걸 시도
    def __call__(self, messages, **kwargs):
        return self.call(messages, **kwargs)


# ── 진단 ──────────────────────────────────────────────────────

def render_catalog() -> str:
    lines = [f"  {a:12s} {s.model_id:40s} max_tokens={s.max_tokens}, temp={s.temperature}"
             for a, s in MODELS.items()]
    return "\n".join(lines)


# ── CrewAI BaseLLM virtual subclass 등록 ────────────────────────
# ClaudeSDKLLM 은 crewai 의 LLM/BaseLLM 을 상속하지 않으므로
# crewai create_llm() 이 강제 변환 시도 → "claude-sonnet-5" 모델을
# ANTHROPIC_MODELS 상수에 미등록 → provider=openai 기본값 → OPENAI_API_KEY 에러.
# ABC.register() 로 virtual subclass 등록 → isinstance 체크 통과 → 변환 없이 반환.
try:
    from crewai.llms.base_llm import BaseLLM as _CrewAIBaseLLM
    _CrewAIBaseLLM.register(ClaudeSDKLLM)
except Exception:
    pass

# backward compat alias (ClaudeCLILLM 이름으로 import 하던 기존 코드 호환)
ClaudeCLILLM = ClaudeSDKLLM

# ── public ────────────────────────────────────────────────────

__all__ = [
    "ModelSpec", "MODELS", "get_spec",
    "chat", "invoke_text", "invoke_vision",
    "is_langchain_available", "render_catalog",
    "ClaudeSDKLLM", "ClaudeCLILLM",
]
