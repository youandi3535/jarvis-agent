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

# crewai telemetry 종료 노이즈('Loop is closed') + 외부 전송 차단
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

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


# ★ 모델 계층 — 사용자 박제 2026-07-06 (ADR 017): Sonnet 5 단일 모델 통일 (ADR 015 폐지).
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
    "coder": ModelSpec(
        alias="coder",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.1,
        description="코드 수정·patch 생성·자가수정 (Sonnet 5 — 오류 수정 전용)",
    ),
    "guardian": ModelSpec(
        alias="guardian",
        model_id="claude-sonnet-5",
        max_tokens=8000,
        temperature=0.1,
        description="JARVIS07 오류 분석·패치 생성 (Sonnet 5)",
    ),
    "architect": ModelSpec(
        alias="architect",
        model_id="claude-sonnet-5",
        max_tokens=10000,
        temperature=0.3,
        description="ARCHITECT 새 에이전트·시스템 설계 (Sonnet 5)",
    ),
    "diagnostic": ModelSpec(
        alias="diagnostic",
        model_id="claude-sonnet-5",
        max_tokens=6000,
        temperature=0.2,
        description="복잡 multi-cause traceback 진단·근본 원인 추론 (Sonnet 5)",
    ),
    "learn_eval": ModelSpec(
        alias="learn_eval",
        model_id="claude-sonnet-5",
        max_tokens=4000,
        temperature=0.1,
        description="learned_patterns 등록 게이트 — patch 안전성·정확성·재사용 가치 채점 (Sonnet 5)",
    ),
    "fact_judge": ModelSpec(
        alias="fact_judge",
        model_id="claude-sonnet-5",
        max_tokens=4000,
        temperature=0.0,
        description="발행 전 사실성 검수 — claim 추출·출처 대조 판정 (Sonnet 5, temp 0 결정성 우선)",
    ),
    "engagement_judge": ModelSpec(
        alias="engagement_judge",
        model_id="claude-sonnet-5",
        max_tokens=2500,
        temperature=0.2,
        description="발행 전 유익성·매력도 채점 — 독자 흡인력 judge (Sonnet 5)",
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
# ★ 토큰 계측용 alias 전파 (ERRORS [456]) — _run_sdk_sync 는 model 만 받으므로
#   "어느 용도(alias)가 얼마나 썼는지" 를 귀속하려면 호출 문맥이 필요하다.
import contextvars as _contextvars
_CURRENT_ALIAS: "_contextvars.ContextVar[str]" = _contextvars.ContextVar("llm_alias", default="")

_LLM_MAX_CONCURRENCY = max(1, int(os.getenv("LLM_MAX_CONCURRENCY", "1") or "1"))
_LLM_SPAWN_SEM = _threading.BoundedSemaphore(_LLM_MAX_CONCURRENCY)
# spawn 간 최소 간격(초) — 기본 0(off). rate-limit 잦으면 0.5~1 로 상향.
_LLM_MIN_INTERVAL = float(os.getenv("LLM_MIN_INTERVAL_SEC", "0") or "0")
_LLM_PACE_LOCK = _threading.Lock()
_LLM_LAST_SPAWN = [0.0]
_LLM_SEM_POLL_SEC = 15.0  # 세마포어 대기 중 heartbeat 주기 (watchdog freeze_sec=300 보다 충분히 작게)


# ── 크로스 프로세스 LLM 직렬화 잠금 ─────────────────────────────────────────
# daemon 과 수동 실행(--tistory-only 등)은 별개 프로세스 → 각자 독립된 BoundedSemaphore.
# 두 프로세스가 동시에 claude CLI 를 spawn 하면 Max 구독 포화 → SDK hang(0응답) 원인.
# fcntl advisory lock: POSIX 보장 + 프로세스 종료 시 자동 해제(교착 위험 0).
_LLM_PROC_LOCK_PATH = Path(
    os.environ.get("JARVIS_DB_PATH", str(Path.home() / ".jarvis" / "jarvis.sqlite"))
).parent / "llm_exec.lock"
_llm_proc_fd: list = [None]
_llm_proc_fd_lock = _threading.Lock()

# ★ 락 획득 대기 상한 (P2-a 사용자 박제 2026-07-18) — LLM timeout(최대 300s)만큼 기다리다
#   사망하지 말고, 짧게(45s) 시도 후 실패하면 lock_contention 으로 defer. 락 경합은 rate-limit
#   스로틀이 아니므로 회로차단기를 오염시키지 않는다(hung 오분류 차단).
_LOCK_ACQUIRE_MAX_WAIT = float(os.getenv("LLM_LOCK_ACQUIRE_MAX_WAIT", "45") or "45")


def _proc_lock_acquire(timeout: float | None = None) -> bool:
    """크로스 프로세스 배타 잠금 — 다른 JARVIS 프로세스가 CLI 사용 중이면 폴링 대기.

    ★ timeout 상한 (ERRORS [439] 후속 — 사용자 박제 2026-07-16): 무제한 폴링은
    다른 프로세스가 잠금을 오래 점유할 때 harness 액션 데드라인(블로그 발행=30분)을
    조용히 관통한다 — 스텝 *내부* 블로킹이라 협조적 wd.check() 가 못 잡고, 백그라운드
    감시 스레드의 '데드라인 초과(블로킹)' 로만 뒤늦게 걸린다. timeout 지정 시 그 안에서
    포기하고 False 반환 → 호출자가 SDK hang 과 동일하게 취급(재시도/회로차단기 경유).
    """
    import fcntl as _fcntl, time as _t
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd
    except Exception:
        def _wd(): pass
    if _llm_proc_fd[0] is None:
        with _llm_proc_fd_lock:
            if _llm_proc_fd[0] is None:
                _LLM_PROC_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
                _llm_proc_fd[0] = open(_LLM_PROC_LOCK_PATH, "w")
    _fd = _llm_proc_fd[0]
    _wd()
    _waited = 0.0
    while True:
        try:
            _fcntl.flock(_fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            return True
        except OSError:
            _wd()
            if timeout is not None and _waited >= timeout:
                return False
            _t.sleep(_LLM_SEM_POLL_SEC)
            _waited += _LLM_SEM_POLL_SEC


def _proc_lock_release() -> None:
    """크로스 프로세스 잠금 해제."""
    import fcntl as _fcntl
    if _llm_proc_fd[0] is not None:
        try:
            _fcntl.flock(_llm_proc_fd[0], _fcntl.LOCK_UN)
        except Exception:
            pass


# ── 발행 기간 LLM 우선권 ──────────────────────────────────────────────────────
# mark_publishing(True) 동안 background alias(guardian 등)의 timeout 을 90s 로 단축,
# retries=1 → 세마포어 장기 점유로 발행 파이프라인을 최대 300s 블로킹하던 사고 방지.
_PUBLISHING_ACTIVE = _threading.Event()
_BG_ALIASES = frozenset({"guardian", "learn_eval", "architect", "diagnostic"})

# ★ 발행창 essential 재시도 캡 대상 (P-C 사용자 박제 2026-07-18) — 본문 생성·발행전 검증 호출.
#   스로틀/SDK 스톨 시 재시도 증폭(913s) 차단용 retries=1. analyzer(추출)는 제외(선계산으로 이전).
_PUBLISH_ESSENTIAL_CAP = frozenset(
    a.strip() for a in
    (os.getenv("LLM_PUBLISH_ESSENTIAL_CAP", "writer,fact_judge,engagement_judge") or "").split(",")
    if a.strip()
)


def mark_publishing(active: bool) -> None:
    """발행 파이프라인 시작/종료 신호 — background alias LLM 호출 자동 강등."""
    if active:
        _PUBLISHING_ACTIVE.set()
    else:
        _PUBLISHING_ACTIVE.clear()


# ── 발행창 보호 구간 (사용자 승인 2026-07-20 — 제안 ③) ─────────────────
#
# 배경: 새벽 심층감사·상시 잡이 한도를 쓴 뒤 발행창에서 LLM 이 스로틀되면 발행이
#   차단된다. 발행 *직전* 일정 시간 동안 background alias 를 아예 막아 한도를
#   발행에 몰아준다.
#
# ★ 동적 설계: 발행 시각을 하드코딩하지 않는다. JARVIS04 DEFAULT_JOBS 의 실제
#   cron(hour/minute)에서 도출 → 사용자가 발행 시각을 바꾸면 보호 구간이 자동으로
#   따라 이동한다. (2026-07-20 '복사본을 진실로 믿지 말 것' 원칙)
_PROTECT_MIN = int(os.getenv("LLM_PUBLISH_PROTECT_MIN", "90") or "90")   # 발행 前 보호 분
# 스로틀 시 재시도 생략 (제안 ① — 킬스위치 0 으로 종전 동작 복귀)
_THROTTLE_NO_RETRY = (os.getenv("LLM_THROTTLE_NO_RETRY", "1") or "1") != "0"
_protect_cache: list = [0.0, ()]     # (계산시각, ((hour,minute), ...))
_PROTECT_TTL = 600.0


def _publish_times() -> tuple:
    """발행 잡의 (시,분) 목록 — DEFAULT_JOBS 에서 실시간 도출. 실패 시 빈 튜플."""
    import time as _t
    now = _t.time()
    if _protect_cache[1] and now - _protect_cache[0] < _PROTECT_TTL:
        return _protect_cache[1]
    times = []
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        for j in DEFAULT_JOBS:
            cb = str(j.get("callback", ""))
            # 실제 *발행* 콜백만 (선계산·로그점검 제외)
            if j.get("trigger") != "cron" or "run_self_repair_then_" not in cb:
                continue
            kw = j.get("kwargs") or {}
            h, m = kw.get("hour"), kw.get("minute", 0)
            if isinstance(h, int):
                times.append((h, int(m or 0)))
    except Exception:
        pass
    _protect_cache[0], _protect_cache[1] = now, tuple(sorted(set(times)))
    return _protect_cache[1]


def in_publish_protection() -> bool:
    """지금이 발행 직전 보호 구간인가 (발행 시각 前 _PROTECT_MIN 분)."""
    if _PROTECT_MIN <= 0:
        return False
    times = _publish_times()
    if not times:
        return False
    from datetime import datetime as _dt
    now = _dt.now()
    cur = now.hour * 60 + now.minute
    for h, m in times:
        start = (h * 60 + m - _PROTECT_MIN) % (24 * 60)
        end = h * 60 + m
        if start <= end:
            if start <= cur < end:
                return True
        else:                      # 자정 넘김
            if cur >= start or cur < end:
                return True
    return False


def is_publishing() -> bool:
    """현재 발행 파이프라인 실행 중인지 (in-process 한정)."""
    return _PUBLISHING_ACTIVE.is_set()


def _acquire_llm_sem() -> None:
    """★ 전역 LLM 세마포어 획득 — 대기 중에도 워치독 진행 신호 전송 (freeze 오탐 방지).

    다른 에이전트(GUARDIAN 심층감사·WRITER 장문 생성 등)가 슬롯을 오래 점유해도
    대기 자체는 정상 흐름이다. plain `with _LLM_SPAWN_SEM:` 은 대기 구간에 beat가
    없어 워치독이 300초 무진전으로 오판해 강제 종료(os._exit 75)하는 사고 원인이었다.
    호출 후 반드시 `try/finally: _LLM_SPAWN_SEM.release()` 로 짝을 맞출 것.
    """
    try:
        from JARVIS00_INFRA.watchdog import beat as _beat
    except Exception:
        def _beat() -> None: pass
    _beat()
    while not _LLM_SPAWN_SEM.acquire(timeout=_LLM_SEM_POLL_SEC):
        _beat()   # ★ 세마포어 대기 중에도 진행 신호 — freeze-kill 오탐 방지


# ★ Rate-limit 회로 차단기 (ERRORS [288] — 2026-07-03)
# 연속 *진짜 스로틀* N회 시 open → 비필수 호출은 즉시 "" 반환 (재시도 0)
# 쿨다운 후 probe 1회(1샷) 허용 → 성공 시 close.
_CIRCUIT_THRESHOLD = int(os.getenv("LLM_CIRCUIT_THRESHOLD", "3") or "3")
_CIRCUIT_COOLDOWN_SEC = float(os.getenv("LLM_CIRCUIT_COOLDOWN_SEC", "90") or "90")
# 필수 alias 면제 셋 — open 중에도 1회 실시도 허용 (대본 본문·사실성 게이트가 "" 즉사
# → 발행 통째 실패로 번지는 것 방지). 장식성 호출(번역·라벨·태그)만 즉시 폴백.
_CIRCUIT_EXEMPT_ALIASES = {
    a.strip() for a in
    (os.getenv("LLM_CIRCUIT_EXEMPT", "writer,fact_judge,engagement_judge,analyzer") or "").split(",")
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


def _run_sdk_sync(
    prompt: str,
    model: str = _DEFAULT_MODEL_ID,
    system: str = "",
    timeout: int = 300,
) -> str:
    """claude-code-sdk 동기 래퍼 — 응답 수집 후 ProcessError/MessageParseError 무시.

    ★ anyio.fail_after(timeout) 는 SDK subprocess 전송이 블로킹(비-yield) I/O로 멈추면
    인터럽트를 못 걸 수 있다 — google_collector._bounded() 가 pytrends 에 대해 이미 고친
    것과 동일한 클래스의 버그(레이더 수집이 메시지 0건인 채 300초+를 통째로 블로킹해
    watchdog freeze 880s 로 감지된 사고). ThreadPoolExecutor + fut.result(timeout=) 로
    호출 자체에 강한 벽시계 상한을 걸고, 대기 중에도 주기적으로 beat() 해 오탐/무한
    블로킹을 동시에 방지한다.
    """
    import anyio
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import MessageParseError, ProcessError

    full_prompt = f"{system}\n\n{prompt}".strip() if system else prompt
    full_prompt = _sanitize_prompt(full_prompt)   # ★ embedded null byte 크래시 차단
    options = ClaudeCodeOptions(model=model, env={"ANTHROPIC_API_KEY": ""})
    parts: list[str] = []
    throttled = {"v": False}
    hung = {"v": False}
    truncated = {"v": False}   # ★ 우리 데드라인이 스트림을 끊었는데 부분출력 존재 = 인프라 절단
    # ★ 토큰 계측 (ERRORS [456]): ResultMessage 의 usage/cost 를 박제해 사용량 가시화.
    #   종전엔 num_turns 만 보고 나머지를 버려 "언제 얼마나 썼는지" 를 알 수 없었다.
    _meter = {"usage": None, "cost": 0.0, "dur": 0, "turns": 0}

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        _wd_beat = lambda: None

    async def _collect():
        nonlocal parts
        _wd_beat()
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full_prompt, options=options):
                _wd_beat()   # ★ 메시지 수신 = 진행 신호 (SDK 살아있음 — 워치독 오탐 freeze-kill 방지)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)
                # ★ Max 구독 burst 스로틀 감지 (사용자 박제 2026-07-01): rate-limit 시 CLI 는
                #   모델을 호출하지 않고 ResultMessage(num_turns=0, duration_api_ms=0, success)만
                #   흘려 *빈 응답* 을 낸다(예외 아님). 조용한 degrade 방지 위해 플래그로 표식.
                elif type(msg).__name__ == "ResultMessage":
                    if getattr(msg, "num_turns", 1) == 0:
                        throttled["v"] = True
                    # 계측 — 성공·스로틀 무관하게 항상 수집 (스로틀도 데이터)
                    _meter["usage"] = getattr(msg, "usage", None)
                    _meter["cost"]  = getattr(msg, "total_cost_usd", 0.0) or 0.0
                    _meter["dur"]   = getattr(msg, "duration_ms", 0) or 0
                    _meter["turns"] = getattr(msg, "num_turns", 0) or 0
                # ※ rate_limit_event 박제는 claude_sdk_compat._patched 가 *모든 경로*
                #   공통으로 수행한다. 여기서 또 기록하면 같은 이벤트가 2건으로
                #   중복 적재되므로(화면에 2줄) 의도적으로 하지 않는다.

    def _run_blocking() -> None:
        # ★ 이벤트 루프 오염 방지 (ERRORS [443] — 사용자 박제 2026-07-16):
        #   anyio.run() 완료 후 스레드의 이벤트 루프가 closed 상태로 남는다.
        #   ThreadPoolExecutor 가 스레드를 재사용하면 다음 anyio.run() 이 닫힌 루프를 만나
        #   "Loop is closed" 경고 → SDK 0 응답 → 또 300s 낭비(경제 발행 hang 연쇄 사고 근본 원인).
        #   매 호출마다 새 이벤트 루프 강제 설정 → 재사용 오염 제거.
        import asyncio as _aio
        _aio.set_event_loop(_aio.new_event_loop())
        try:
            anyio.run(_collect)
        except (MessageParseError, ProcessError):
            pass  # rate_limit_event 또는 프로세스 종료 — 응답은 이미 수집됨
        except TimeoutError:
            import logging as _logging
            _logging.getLogger("jarvis.llm").warning(f"SDK timeout {timeout}s — 수집된 응답: {len(parts)}개")
            if not parts:
                hung["v"] = True       # 0개 hang → 회로차단기 신호
            else:
                truncated["v"] = True  # ★ 부분출력 + 우리 데드라인 절단 = 인프라 스로틀(콘텐츠 결함 아님)
        except Exception as e:
            if not parts:
                import logging as _logging
                _logging.getLogger("jarvis.llm").warning(f"SDK 오류: {e}")

    # ★ 프로세스 전역 세마포어 — claude CLI 동시 spawn 직렬화 (Max burst 초과 방지)
    _pace_spawn()
    _acquire_llm_sem()
    try:
        # ★ P2-a (사용자 박제 2026-07-18): 락 대기를 짧게(45s) 캡 — LLM timeout(300s)만큼 기다리다
        #   사망하지 말 것. 락 획득 실패는 rate-limit 스로틀이 아니라 *경합* 이므로 hung(회로차단기
        #   신호)로 오분류하지 말고 lock_contention 으로 분리 → 회로 무오염 + harness defer.
        _lock_wait = min(timeout, _LOCK_ACQUIRE_MAX_WAIT)
        if not _proc_lock_acquire(timeout=_lock_wait):
            import logging as _logging
            _logging.getLogger("jarvis.llm").warning(
                f"크로스 프로세스 잠금 {_lock_wait:.0f}s 대기 초과 — lock_contention (회로 무오염, defer 위임)"
            )
            _LAST_CALL.throttled = False
            _LAST_CALL.hung = False           # ★ hung 아님 — 회로차단기 카운트 제외 (락 경합≠스로틀)
            _LAST_CALL.truncated = False
            _LAST_CALL.lock_contention = True
            return ""
        try:
            exe = ThreadPoolExecutor(max_workers=1)
            try:
                fut = exe.submit(_run_blocking)
                wall_deadline = timeout + 30.0   # anyio 내부 타임아웃 위 안전 마진
                waited = 0.0
                poll = 15.0                      # watchdog freeze_sec(300s) 보다 충분히 작게
                while True:
                    try:
                        fut.result(timeout=min(poll, max(0.1, wall_deadline - waited)))
                        break
                    except _FutTimeout:
                        waited += poll
                        _wd_beat()   # ★ 벽시계 대기 중에도 진행 신호 — freeze 오탐 방지
                        if waited >= wall_deadline:
                            import logging as _logging
                            _logging.getLogger("jarvis.llm").warning(
                                f"SDK 벽시계 상한 {wall_deadline:.0f}s 초과 — 강제 포기(수집 {len(parts)}개)"
                            )
                            if parts:
                                truncated["v"] = True  # ★ 부분출력 + 벽시계 절단 = 인프라 스로틀
                            else:
                                hung["v"] = True       # ★ 0개 = hang (기존 무신호 구멍 보강)
                            break
            finally:
                exe.shutdown(wait=False)   # 내부 스레드 leak 가능 — 메인 흐름 비블로킹 우선(_bounded() 와 동일 정책)
        finally:
            _proc_lock_release()
    finally:
        _LLM_SPAWN_SEM.release()
    _was_throttled = bool(throttled["v"] and not parts)
    _was_hung = bool(hung["v"] and not parts)
    _was_truncated = bool(truncated["v"] and parts)  # ★ parts>0 일 때만 (정의상 데드라인 절단)
    _LAST_CALL.throttled = _was_throttled
    _LAST_CALL.hung = _was_hung  # ★ hang(TimeoutError+0parts) 도 회로차단기 신호로 전달
    _LAST_CALL.truncated = _was_truncated  # ★ 절단(부분출력+데드라인) — 인프라 스로틀 신호
    if _was_throttled:
        import logging as _logging
        _logging.getLogger("jarvis.llm").debug("rate-limit 스로틀 (num_turns=0) — 재시도/폴백")
    # ★ 토큰 계측 박제 (ERRORS [456]) — 실패해도 본류를 막지 않는다.
    try:
        from shared.token_usage import record_call
        record_call(
            alias=_CURRENT_ALIAS.get() or "", model=model,
            usage=_meter["usage"], cost_usd=_meter["cost"],
            duration_ms=_meter["dur"], num_turns=_meter["turns"],
            ok=bool(parts), source="daemon",
        )
    except Exception:
        pass
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

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        _wd_beat = lambda: None

    async def _collect():
        _wd_beat()
        with anyio.fail_after(timeout):
            async for msg in query(prompt=full, options=options):
                _wd_beat()   # ★ 메시지 수신 = 진행 신호 (SDK 살아있음 — 워치독 오탐 freeze-kill 방지)
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            parts.append(block.text)

    _pace_spawn()
    _acquire_llm_sem()
    try:
        # ★ P2-a: 락 대기 짧게 캡 — vision 은 회로 미참여라 신호 불필요, 대기 낭비만 제거
        _lock_wait = min(timeout, _LOCK_ACQUIRE_MAX_WAIT)
        if not _proc_lock_acquire(timeout=_lock_wait):
            print(f"  ⚠️ vision 크로스 프로세스 잠금 {_lock_wait:.0f}s 대기 초과 — 포기")
            return ""
        try:
            try:
                anyio.run(_collect)
            except (MessageParseError, ProcessError):
                pass
            except TimeoutError:
                print(f"  ⚠️ vision SDK timeout {timeout}s — 수집 {len(parts)}개")
            except Exception as e:
                if not parts:
                    print(f"  ❌ vision SDK 오류: {e}")
        finally:
            _proc_lock_release()
    finally:
        _LLM_SPAWN_SEM.release()
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


def last_call_infra_incomplete() -> bool:
    """직전 invoke_text 호출이 *인프라 사유*(스로틀 빈응답/hang/데드라인 절단)로 미완결이었는지.

    ★ _LAST_CALL 은 thread-local — invoke_text 를 호출한 *동일 스레드* 에서 *반환 직후* 읽어야
      유효. 다른(워커) 스레드에서 관측하려면 circuit_is_open() 사용.
    콘텐츠 결함(정상 완료인데 짧음·빈약)과 인프라 스로틀을 호출자가 구분하는 유일한 신호원.
    """
    return bool(
        getattr(_LAST_CALL, "throttled", False)
        or getattr(_LAST_CALL, "hung", False)
        or getattr(_LAST_CALL, "truncated", False)
        or getattr(_LAST_CALL, "lock_contention", False)   # ★ P2-a: 락 경합도 미완결(defer 대상, 회로 무오염)
    )


def circuit_is_open() -> bool:
    """rate-limit 회로차단기 open 여부 — 순수 read-only peek (probe 전이·상태변이 없음).

    ★ 프로세스 전역 상태 — parallel 워커 스레드에서도 관측 가능(thread-local 아님).
      probe 를 소비하는 _circuit_gate() 와 달리 회로 계정을 오염시키지 않는다.
    """
    with _circuit_lock:
        return _circuit_open_since[0] != 0.0


def invoke_text(alias: str, prompt: str, system: str = "", timeout: int = 180,
                _retries: int = 4, _essential: bool = False,
                _nonessential: bool = False, **overrides) -> str:
    """Claude Code SDK 호출 단일 진입점.

    모든 alias — Sonnet 5 단일 모델 (ADR 017, 사용자 박제 2026-07-06 — ADR 015 폐지).

    ★ rate-limit 재시도 (사용자 박제 2026-07-01): 빈 응답이면 지수 백오프+지터로
      재시도. ★ 회로 차단기 (ERRORS [288] — 2026-07-03): 연속 *진짜 스로틀* ≥3 회 시
      쿨다운 동안 비필수 호출 즉시 "" 폴백. 필수 alias(_CIRCUIT_EXEMPT_ALIASES)와
      probe 는 1샷 실시도. ★ 데드라인 강등: JARVIS_LLM_DEADLINE_TS(epoch) 잔여 <10분
      이면 재시도 1회·백오프 0 — 발행(Layer 4) 시간 보호.

    ★ _nonessential (사용자 박제 2026-07-05, ERRORS [368]): *비필수* 호출(자기비평·
      매력도·번역·썸네일 등 — 폴백이 있어 없어도 발행되는 것). 스로틀(회로 open/probe)
      감지 시 *SDK 호출 없이 즉시 "" 반환* — timeout·재시도로 임계경로를 막지 않는다.
      회로 정상일 때도 1회·시간상자(≤45초). 필수 alias 면제(writer 등)보다 *우선* 적용.
    """
    import time as _t, random as _r

    # ★ 스레드로컬 위생 (truncated 신호 도입): 진입 시 1회 리셋해 early-return
    #   (회로 open 폴백 등) 경로에서 이전 호출의 인프라 플래그가 새는 것을 방지.
    _LAST_CALL.throttled = False
    _LAST_CALL.hung = False
    _LAST_CALL.truncated = False
    _LAST_CALL.lock_contention = False   # ★ P2-a: 락 경합 신호도 진입 리셋

    # ★ 재시도 최대 3회 상한 (사용자 박제 2026-07-06): 어떤 재시도도 3회 초과 금지.
    #   기본 _retries=4 → 실효 3으로 캡. deadline/_nonessential/probe/open 강등은 더 낮춤.
    retries = max(1, min(3, _retries))
    backoff = True
    _CURRENT_ALIAS.set(alias or "")   # ★ 토큰 계측 귀속 (ERRORS [456])

    # ★ 글로벌 데드라인 강등 — 발행 파이프라인(economic_poster 등)이 설정
    try:
        _dl = float(os.environ.get("JARVIS_LLM_DEADLINE_TS", "0") or "0")
        _rem = _dl - _t.time()
        # ★ stale carryover 차단 (사용자 박제 2026-07-18): 발행 액션 데드라인 임박(잔여<600s) 시에만
        #   강등하되, 이미 1시간 이상 지난 데드라인은 이전 발행의 잔재(예: 06:30 경제 값이 pop 안 돼
        #   21:00 테마에서 관측 → 모든 테마 호출 상시 강등)이므로 무시. 활성 액션의 정상 overrun
        #   (잔여 0~-3600s)은 여전히 강등해 발행창 밖 호출만 부당 강등에서 제외한다.
        if _dl and -3600 < _rem < 600:
            retries, backoff = 1, False
    except Exception:
        pass

    # ★ 발행 중 background alias 자동 강등 (2026-07-15):
    #   동일 프로세스(daemon) 안에서 guardian 이 세마포어를 timeout=300s 로 점유해
    #   발행 파이프라인을 최대 300s 차단하던 사고 방지.
    #   mark_publishing(True) → 모든 BG alias 호출을 timeout ≤90s·retries=1 로 단축.
    # ★ 발행창 보호 구간 (사용자 승인 2026-07-20 — 제안 ③): 발행 시각 前
    #   _PROTECT_MIN 분 동안 background alias 를 *아예 차단* 해 한도를 발행에 몰아준다.
    #   종전엔 발행이 *시작된 뒤*(mark_publishing) 강등만 했으므로, 발행 직전에
    #   심층감사·학습이 한도를 태워버리는 것을 막지 못했다.
    #   보호 시각은 DEFAULT_JOBS cron 에서 도출 — 하드코딩 없음.
    if alias in _BG_ALIASES and not _PUBLISHING_ACTIVE.is_set():
        try:
            if in_publish_protection():
                import logging as _lg
                _lg.getLogger("jarvis.llm").info(
                    f"🛡 발행창 보호 구간 — background alias '{alias}' 차단 "
                    f"(발행 前 {_PROTECT_MIN}분). 한도를 발행에 우선 배정."
                )
                return ""
        except Exception:
            pass

    if alias in _BG_ALIASES and _PUBLISHING_ACTIVE.is_set():
        retries = min(retries, 1)
        backoff = False
        timeout = min(timeout, 90)
    # ★ P-C 발행창 essential 재시도 캡 (사용자 박제 2026-07-18): writer(본문 생성)·fact_judge·
    #   engagement_judge(발행 전 검증)는 필수라 timeout 은 유지하되, 스로틀/SDK 스톨 시 재시도로
    #   913s(최대 3×300+백오프)로 증폭되는 것을 차단 — retries=1. 스로틀·스톨 창에서 같은 창
    #   재발사는 무의미하므로 1회 후 defer 가 정상경로다. analyzer(fact·chart 추출)는 품질 보존 위해
    #   강등 제외 — 추출은 선계산(06:00/20:30 저부하 창)으로 발행창 밖 이전됨.
    elif alias in _PUBLISH_ESSENTIAL_CAP and _PUBLISHING_ACTIVE.is_set():
        retries = min(retries, 1)
        backoff = False

    # ★ 회로 차단기 게이트 (_essential=True 는 호출 단위 필수 면제 —
    #   설계 planner 등 품질 조타수 호출이 스로틀 중에도 1회 실시도, ERRORS [300])
    _gate = _circuit_gate()
    # ★ 비필수 호출 — 스로틀 시 임계경로 블로킹 절대 금지 (ERRORS [368]). 필수 면제보다 우선.
    if _nonessential:
        if _gate in ("open", "probe"):
            return ""                      # 스로틀 중 — SDK 미호출·즉시 폴백 (발행 안 막음)
        retries, backoff = 1, False        # 정상일 때도 1샷
        timeout = min(timeout, 90)         # 시간 상자 — 최악 90초 (max_tokens≤700 안에 완료)
    elif _gate == "open":
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
    hung_seen = False
    truncated_seen = False
    for _attempt in range(retries):
        # ★ 전역 하트비트 (사용자 박제 2026-07-06): LLM 호출 = 진행 신호 → freeze 워치독
        #   이 오래 걸리는 정상 LLM 작업을 멈춤으로 오탐하지 않도록 매 시도마다 beat.
        try:
            from JARVIS00_INFRA.watchdog import beat as _wd_beat
            _wd_beat()
        except Exception:
            pass
        try:
            _LAST_CALL.throttled = False
            _LAST_CALL.hung = False
            _LAST_CALL.truncated = False
            _LAST_CALL.lock_contention = False
            result = _run_sdk_sync(prompt, model=model, system=system, timeout=timeout) or ""
        except Exception:
            result = ""
        _truncated = getattr(_LAST_CALL, "truncated", False)
        if result.strip() and not _truncated:
            _circuit_record_success()
            return result
        # ★ 절단(우리 데드라인이 스트림을 끊음 + 부분출력) = 인프라 스로틀 — 성공 처리·회로 리셋
        #   금지. 빈 응답과 동급으로 재시도 루프에 흘리고, 소진 후 best-effort 로 절단본 반환.
        if _truncated:
            truncated_seen = True
        if getattr(_LAST_CALL, "throttled", False):
            throttled_seen = True
            # ★ 스로틀 = 재시도 금지 (사용자 승인 2026-07-20 — 제안 ①)
            #   num_turns=0 은 *모델을 아예 호출하지 않았다* 는 신호(한도/스로틀).
            #   같은 창에서 즉시 재발사해도 같은 결과이고, 한도가 없을 때 한도를 더
            #   태운다. LLM 재시도(최대 3) × harness max_attempts(3) = 최악 9배 증폭의
            #   진원지. 여기서 끊고 상위(harness)의 defer 에 위임한다.
            #   킬스위치: LLM_THROTTLE_NO_RETRY=0
            if _THROTTLE_NO_RETRY and _attempt < retries - 1:
                import logging as _lg
                _lg.getLogger("jarvis.llm").info(
                    f"⏭ 스로틀 감지 — 재시도 생략 후 defer (alias={alias}, "
                    f"시도 {_attempt + 1}/{retries}). 같은 창 재발사는 한도만 소모."
                )
                break
        if getattr(_LAST_CALL, "hung", False):
            hung_seen = True
        if backoff and _attempt < retries - 1:
            _t.sleep(min(30.0, 4 * (2 ** _attempt)) + _r.uniform(0, 1.5))
    # 모든 재시도 실패 — 진짜 스로틀(ResultMessage) OR SDK hang(TimeoutError+0parts) 모두
    # 회로차단기 카운트. CLI 부재·auth 빠른 실패는 hung=False라 오탐 없음.
    if throttled_seen or hung_seen or truncated_seen:
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
    "last_call_infra_incomplete", "circuit_is_open",
    "is_langchain_available",
    "ClaudeSDKLLM", "ClaudeCLILLM",
]
