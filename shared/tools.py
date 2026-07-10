"""shared/tools.py — 도구 (Tool) 중앙 등록 + 권한·로그·LangChain 호환.

★ 비가역적 — N 에이전트가 자유롭게 도구를 호출하기 시작하면 *누가 뭘 호출했는지*
추적 불가, 사고 시 폭발. 처음부터 *중앙 등록 + 호출 시 권한 검증 + 로그* 박는다.

설계:
- @register_tool 데코레이터: 도구 등록 (메타: domain·side_effect·rollback·cost).
- tool_invoke(): 권한 검증 + correlation_id 로그 + 실행 + 이벤트 publish.
- LangChain @tool 와 호환 — 같은 함수가 LangChain Tool 으로도 노출 가능.

도구 메타:
- domain: "blog", "schedule" ... (capability 매칭).
- side_effect: "none" (조회) | "internal" (DB 쓰기) | "external" (외부 API).
- rollback: 되돌리기 도구명 (있으면 자동 undo 후보 등록).
- cost_class: "free" | "low" (DB) | "medium" (LLM) | "high" (외부 결제 등).
- requires_approval: True 면 호출 시 휴먼 승인 이벤트 발행 후 대기.

사용:
    @register_tool(
        name="naver_publish",
        domain="blog",
        side_effect="external",
        rollback="naver_unpublish",
        cost_class="low",
    )
    def naver_publish(title: str, content: str) -> dict:
        return post_to_naver(title, content)

    # 호출
    result = tool_invoke("naver_publish", title="...", content="...")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import inspect
import functools


# ── 도구 메타 ──────────────────────────────────────────────────

@dataclass
class ToolMeta:
    """도구 등록 메타데이터."""
    name: str
    domain: str
    func: Callable
    side_effect: str = "none"  # "none" | "internal" | "external"
    rollback: Optional[str] = None  # 되돌리기 도구명
    cost_class: str = "free"  # "free" | "low" | "medium" | "high"
    requires_approval: bool = False
    description: str = ""
    signature: Optional[inspect.Signature] = None  # 자동 추출

    @property
    def is_external(self) -> bool:
        return self.side_effect == "external"


# ── 글로벌 레지스트리 ──────────────────────────────────────────
_TOOLS: dict[str, ToolMeta] = {}


# ── 승인 컨텍스트 ──────────────────────────────────────────────
# APPROVAL 도구는 사용자 텔레그램 ✅ 후 daemon 콜백에서만 호출 가능.
# contextvars 로 thread-local 마커 관리.
import contextvars as _ctxvars
_APPROVED_CONTEXT: _ctxvars.ContextVar[bool] = _ctxvars.ContextVar(
    "_jarvis_tool_approved", default=False)


def _is_approved() -> bool:
    return bool(_APPROVED_CONTEXT.get())


class approved_context:
    """daemon 콜백이 사용 — 인라인 버튼 ✅ 후 APPROVAL 도구 호출 직전.

    사용:
        with approved_context():
            tool_invoke("delegate_to_claude_code", ...)
    """
    def __init__(self):
        self._token = None
    def __enter__(self):
        self._token = _APPROVED_CONTEXT.set(True)
        return self
    def __exit__(self, *exc):
        if self._token is not None:
            _APPROVED_CONTEXT.reset(self._token)


# ── 데코레이터 ─────────────────────────────────────────────────

def register_tool(name: str, domain: str,
                  side_effect: str = "none",
                  rollback: Optional[str] = None,
                  cost_class: str = "free",
                  requires_approval: bool = False,
                  description: str = ""):
    """도구 등록 데코레이터."""
    def _wrap(func: Callable):
        sig = inspect.signature(func)
        meta = ToolMeta(
            name=name, domain=domain, func=func,
            side_effect=side_effect, rollback=rollback,
            cost_class=cost_class, requires_approval=requires_approval,
            description=description or (func.__doc__ or "").strip().split("\n")[0],
            signature=sig,
        )
        _TOOLS[name] = meta
        # 함수 자체에도 메타 attached (LangChain 어댑터에서 활용)
        func._tool_meta = meta  # type: ignore
        return func
    return _wrap


# ── 조회 ───────────────────────────────────────────────────────

def get_tool(name: str) -> Optional[ToolMeta]:
    return _TOOLS.get(name)


def all_tools() -> list[ToolMeta]:
    return list(_TOOLS.values())


def tools_by_domain(domain: str) -> list[ToolMeta]:
    return [t for t in _TOOLS.values() if t.domain == domain]


# ── 호출 ───────────────────────────────────────────────────────

def tool_invoke(name: str, **kwargs) -> Any:
    """도구 호출 — 자동 trace·로그·이벤트.

    1) ToolMeta 조회
    2) requires_approval 이면 텔레그램 승인 이벤트 publish 후 대기
       (현 단계는 *경고 로그* 만 — 승인 게이트는 Phase 2 에서 도입)
    3) func 호출
    4) task.completed 이벤트 publish

    Args:
        name: 등록된 도구명.
        **kwargs: 도구 함수 인수.

    Returns: 도구 결과.
    """
    meta = _TOOLS.get(name)
    if meta is None:
        raise KeyError(f"tool '{name}' not registered. 등록된 도구: {list(_TOOLS.keys())}")

    # 트레이싱 컨텍스트 — tracing 모듈은 lazy import (순환 회피)
    try:
        from shared import tracing
        cid = tracing.current_correlation_id()
    except Exception:
        cid = None

    # 호출 로그
    print(f"  🔧 [tool] {name} (domain={meta.domain}, side={meta.side_effect}, cid={cid})")

    if meta.requires_approval:
        # ★ Phase 3 — APPROVAL 도구는 *컨텍스트 변수* 통과해야 실행.
        # daemon 의 _execute_j00_react_approval / _execute_plan / _execute_j00_approval 가
        # 호출 직전 set_approval_context() 로 승인 마커를 박는다.
        # 그 외 경로 (ReAct LLM 직접 호출 포함) 는 차단 — 사용자 미인지 외부 영향 방지.
        if not _is_approved():
            raise PermissionError(
                f"tool '{name}' requires user approval — "
                f"call from approved context only (daemon callback after telegram ✅)."
            )

    # 실제 호출 + 실행 시간 측정
    import time as _time
    _t0 = _time.monotonic()
    try:
        result = meta.func(**kwargs)
        _duration_ms = int((_time.monotonic() - _t0) * 1000)
        # 성공 이벤트 (lazy import 로 순환 회피)
        try:
            from shared import bus, schemas
            bus.publish_event(schemas.TaskCompleted(
                event_type="task.completed",
                domain=meta.domain,
                source_agent="tools",
                task_kind=f"tool.{name}",
                success=True,
                result={"tool": name, "ok": True, "duration_ms": _duration_ms},
                correlation_id=cid or "unknown",
            ))
        except Exception:
            pass
        # tool_runs 기록
        try:
            from shared import db as _tdb
            _tdb.log_tool_run(name, meta.domain, True, _duration_ms, cid)
        except Exception:
            pass
        return result
    except Exception as e:
        # 실패 이벤트
        _duration_ms = int((_time.monotonic() - _t0) * 1000)
        try:
            from shared import bus, schemas
            bus.publish_event(schemas.TaskCompleted(
                event_type="task.completed",
                domain=meta.domain,
                source_agent="tools",
                task_kind=f"tool.{name}",
                success=False,
                error=str(e)[:200],
                correlation_id=cid or "unknown",
            ))
        except Exception:
            pass
        try:
            from shared import db as _tdb
            _tdb.log_tool_run(name, meta.domain, False, _duration_ms, cid, error=str(e)[:200])
        except Exception:
            pass
        raise


# ── LangChain 어댑터 ──────────────────────────────────────────

def to_langchain_tool(meta: ToolMeta):
    """ToolMeta 를 LangChain Tool 로 변환 (LangGraph 노드에서 사용).

    LangChain 미설치 시 None 반환.

    ★ 핵심 — wrapper 함수의 시그니처가 *원본 함수와 동일* 해야 langchain 이
    schema 를 정확히 추출. 그렇지 않으면 LLM 이 args 를 `{"kwargs": {...}}`
    형식으로 nested 하게 보내서 호출 시 unexpected keyword 발생 (ERRORS [31]).

    해결: `_wrapped.__signature__ = inspect.signature(meta.func)` 명시 — langchain 의
    `inspect.signature()` 가 이 attribute 를 우선 사용하여 원본 시그니처 인식.
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        return None

    desc = (meta.description
            or (meta.func.__doc__ or "").strip().split("\n")[0]
            or meta.name)

    def _wrapped(**kwargs):
        return tool_invoke(meta.name, **kwargs)
    _wrapped.__name__ = meta.name
    _wrapped.__doc__ = desc
    # ★ 원본 함수 시그니처 명시 — langchain schema 추출이 이 값을 우선 사용.
    # from __future__ import annotations 로 인해 어노테이션이 string 으로 저장됨.
    # get_type_hints() 로 실제 타입 복원 후 __annotations__ 에 덮어쓰기.
    sig = meta.signature or inspect.signature(meta.func)
    _wrapped.__signature__ = sig
    try:
        import typing
        hints = typing.get_type_hints(meta.func)
        _wrapped.__annotations__ = hints
    except Exception:
        pass

    try:
        return StructuredTool.from_function(
            func=_wrapped,
            name=meta.name,
            description=desc,
        )
    except Exception as e:
        # schema 추출 실패 — 도구 시그니처 단순화 필요. 로그만 남기고 None.
        print(f"  ⚠️ to_langchain_tool 실패 ({meta.name}): {e}")
        return None


def all_langchain_tools(domain: Optional[str] = None) -> list:
    """등록된 모든 도구를 LangChain Tool 형태로. domain 필터 옵션."""
    tools = tools_by_domain(domain) if domain else all_tools()
    out = []
    for m in tools:
        lc = to_langchain_tool(m)
        if lc is not None:
            out.append(lc)
    return out


# ── public ────────────────────────────────────────────────────

__all__ = [
    "ToolMeta", "register_tool", "tool_invoke",
    "get_tool", "all_tools", "tools_by_domain",
    "to_langchain_tool", "all_langchain_tools",
    "approved_context",
]
