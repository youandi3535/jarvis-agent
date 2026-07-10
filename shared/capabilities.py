"""shared/capabilities.py — 에이전트 역량 (capability) 선언 + 글로벌 레지스트리.

★ 비가역적 — 마스터 라우터가 사용자 자유 문장을 어느 에이전트에 디스패치할지
결정하려면 *각 에이전트가 무엇을 할 수 있는지* 알아야 한다. 처음부터
역량 선언 규약을 박아둬야 N개 에이전트로 확장 가능.

설계:
- declare(...) 로 에이전트가 자신의 역량 명시. 모듈 import 시점에 자동 등록.
- intents 는 dot-naming: "blog.theme_post.create", "schedule.event.create".
- 마스터 라우터가 capability 매칭으로 라우팅.
- requires_approval 은 휴먼 승인 필수 인텐트 목록.
- cost_class 로 LLM 비용 등급 표시 (라우팅 정책에 활용).

사용 패턴:
    # JARVIS02_WRITER/writer_agent.py
    from shared.capabilities import declare

    CAPABILITIES = declare(
        agent_id="jarvis02_writer",
        domain="blog",
        intents=[
            "blog.theme_post.create",
            "blog.economic_post.create",
            "blog.post.revise",
        ],
        tools=["naver_publish", "tistory_publish"],
        requires_approval=["blog.post.delete"],
        cost_class="medium",
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# ── 역량 모델 ──────────────────────────────────────────────────

@dataclass
class Capability:
    """단일 에이전트의 역량 선언."""
    agent_id: str
    domain: str  # "blog", "schedule", "research", "finance", "memo", "core" ...
    intents: list[str] = field(default_factory=list)  # dot-naming
    tools: list[str] = field(default_factory=list)  # tool name 리스트 (shared/tools.py 등록명)
    requires_approval: list[str] = field(default_factory=list)  # 휴먼 승인 필수 인텐트
    cost_class: str = "low"  # "low" | "medium" | "high" — LLM 비용 등급
    description: str = ""  # 자유 설명 (마스터 라우터의 LLM 분류에 도움)
    tags: list[str] = field(default_factory=list)  # 보조 메타
    help_section: str = ""  # 텔레그램 /help 에 포함될 명령어 섹션 (에이전트 자율 작성)
    status_fn: Optional[Callable[[], str]] = field(default=None)  # 텔레그램 /status 섹션 빌더


# ── 글로벌 레지스트리 ──────────────────────────────────────────
_REGISTRY: dict[str, Capability] = {}


def declare(agent_id: str, domain: str, **kwargs) -> Capability:
    """에이전트 역량 선언 + 자동 등록.

    같은 agent_id 재선언 시 덮어쓰기 (모듈 reload 대응).

    Returns: 등록된 Capability.
    """
    cap = Capability(agent_id=agent_id, domain=domain, **kwargs)
    _REGISTRY[agent_id] = cap
    return cap


def get(agent_id: str) -> Optional[Capability]:
    """ID 로 capability 조회."""
    return _REGISTRY.get(agent_id)


def all_capabilities() -> list[Capability]:
    """등록된 모든 capability."""
    return list(_REGISTRY.values())


def find_by_intent(intent: str) -> list[Capability]:
    """주어진 intent 를 처리할 수 있는 에이전트 목록.

    매칭 우선순위:
        1) 정확 일치 (intent in cap.intents)
        2) prefix 일치 ("blog.post.delete" → "blog.post.*")
    """
    exact = [c for c in _REGISTRY.values() if intent in c.intents]
    if exact:
        return exact
    # prefix 매칭 (와일드카드 향후 확장)
    parts = intent.split(".")
    candidates: list[Capability] = []
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        for c in _REGISTRY.values():
            for it in c.intents:
                if it.startswith(prefix + ".") or it == prefix:
                    if c not in candidates:
                        candidates.append(c)
        if candidates:
            return candidates
    return candidates


def find_by_domain(domain: str) -> list[Capability]:
    """도메인으로 에이전트 조회."""
    return [c for c in _REGISTRY.values() if c.domain == domain]


def list_intents() -> list[str]:
    """전체 시스템에서 등록된 모든 intent (마스터 라우터 prompt 빌드용)."""
    seen: set[str] = set()
    for c in _REGISTRY.values():
        for it in c.intents:
            seen.add(it)
    return sorted(seen)


def render_for_router_prompt() -> str:
    """마스터 라우터 LLM prompt 에 박을 capability 카탈로그 (자연어).

    예시 출력:
        - jarvis02_writer (domain=blog, cost=medium)
            intents: blog.theme_post.create, blog.economic_post.create, blog.post.revise
            tools: naver_publish, tistory_publish
            description: 블로그 발행 (네이버·티스토리)
    """
    if not _REGISTRY:
        return "(등록된 에이전트 없음)"
    lines: list[str] = []
    for c in _REGISTRY.values():
        lines.append(f"- {c.agent_id} (domain={c.domain}, cost={c.cost_class})")
        if c.intents:
            lines.append(f"    intents: {', '.join(c.intents)}")
        if c.tools:
            lines.append(f"    tools: {', '.join(c.tools)}")
        if c.requires_approval:
            lines.append(f"    requires_approval: {', '.join(c.requires_approval)}")
        if c.description:
            lines.append(f"    description: {c.description}")
    return "\n".join(lines)


def build_help_text() -> str:
    """등록된 capability.help_section 조합 → 텔레그램 /help 전체 텍스트."""
    lines = ["🤖 *JARVIS 명령어*", "━━━━━━━━━━━━━━━━"]
    for cap in sorted(_REGISTRY.values(), key=lambda c: c.agent_id):
        if cap.help_section:
            lines.append("")
            lines.append(cap.help_section.strip())
    if len(lines) == 2:
        lines.append("(등록된 에이전트 없음)")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append("💬 자유 문장으로도 대화 가능")
    return "\n".join(lines)


# ── public ────────────────────────────────────────────────────

__all__ = [
    "Capability", "declare",
    "get", "all_capabilities",
    "find_by_intent", "find_by_domain",
    "list_intents", "requires_approval",
    "render_for_router_prompt",
    "build_help_text",
]
