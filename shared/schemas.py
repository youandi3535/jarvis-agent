"""shared/schemas.py — 에이전트 간 이벤트 스키마 (관찰성).

bus.publish_event() 가 받는 CoreEvent 및 서브클래스.
외부 의존 없음 — dataclass 기반.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class CoreEvent:
    event_type:       str = "core.event"
    source_agent:     str = ""
    correlation_id:   str = ""
    schema_version:   str = "1"

    def to_legacy_dict(self) -> dict:
        return asdict(self)


@dataclass
class IntentResolved(CoreEvent):
    """JARVIS01 라우터가 인텐트 분류 완료 후 발행."""
    event_type:    str = "intent.resolved"
    domain:        str = ""
    user_msg:      str = ""
    intent_kind:   str = "unknown"
    target_domain: str = ""
    target_agent:  Optional[str] = None
    confidence:    float = 0.0
    params:        dict = field(default_factory=dict)


@dataclass
class TaskCompleted(CoreEvent):
    """도구 실행 완료 후 발행 (성공·실패 공통)."""
    event_type: str = "task.completed"
    domain:     str = ""
    task_kind:  str = ""
    success:    bool = True
    result:     dict = field(default_factory=dict)
    error:      Optional[str] = None
