"""shared/tracing.py — 요청 추적 correlation ID 관리.

Python contextvars 기반 — 외부 의존 없음. thread-safe + async-safe.

사용:
    from shared import tracing

    with tracing.trace_scope(correlation_id, source="foo") as cid:
        # cid: str — 새로 발급되거나 전달된 correlation_id
        ...

    cid  = tracing.current_correlation_id()
    cause = tracing.current_causation_id()
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
_causation_id:   ContextVar[str] = ContextVar("causation_id",   default="")


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def current_correlation_id() -> str:
    return _correlation_id.get("")


def current_causation_id() -> str:
    return _causation_id.get("")


@contextmanager
def trace_scope(correlation_id: Optional[str] = None, source: str = ""):
    """새 trace 스코프 — correlation_id 없으면 발급, 있으면 재사용.

    Yields:
        cid (str): 이 스코프에서 사용할 correlation_id.
    """
    cid = correlation_id or _new_id()
    tok_corr = _correlation_id.set(cid)
    tok_caus = _causation_id.set(f"{source}:{cid}" if source else cid)
    try:
        yield cid
    finally:
        _correlation_id.reset(tok_corr)
        _causation_id.reset(tok_caus)
