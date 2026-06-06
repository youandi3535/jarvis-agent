"""
shared/agent_base.py — 모든 JARVIS 에이전트의 표준 인터페이스.

새 에이전트는 BaseAgent 를 상속하고 get_health / get_metrics 를 구현.
JARVIS05_VISION 이 이 인터페이스를 통해 모든 에이전트 상태를 수집.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

HealthStatus = Literal["online", "warn", "offline"]


class BaseAgent(ABC):
    """JARVIS 에이전트 표준 계약.

    필수 구현:
        agent_id    : 고유 식별자  (예: "jarvis02_writer")
        agent_name  : 표시 이름    (예: "JARVIS02 WRITER")
        agent_domain: 도메인       (예: "writer")
        get_health()  → {"status": "online|warn|offline", "message": str}
        get_metrics() → 에이전트별 자유 dict
    """

    agent_id: str
    agent_name: str
    agent_domain: str
    version: str = "1.0"

    @abstractmethod
    def get_health(self) -> dict:
        """에이전트 상태 반환.

        Returns:
            {
                "status":  "online" | "warn" | "offline",
                "message": str,          # 한 줄 상태 설명
            }
        """

    @abstractmethod
    def get_metrics(self) -> dict:
        """에이전트별 핵심 지표 반환 (자유 구조).

        hub.py 대시보드와 JARVIS05 API 가 소비.
        키 이름은 snake_case, 값은 JSON-serializable.
        """

    def get_manifest(self) -> dict:
        """레지스트리 등록용 메타. 자동 생성."""
        return {
            "agent_id":     self.agent_id,
            "agent_name":   self.agent_name,
            "agent_domain": self.agent_domain,
            "version":      self.version,
        }
