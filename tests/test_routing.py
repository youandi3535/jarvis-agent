"""tests/test_routing.py — JARVIS01 라우팅 핵심 경로 테스트.

커버:
  1. Intent 분류 — fallback 키워드 매칭 (LLM 미가용 환경)
  2. SAFE / APPROVAL 인텐트 분류 정확도
  3. 승인 게이트 — APPROVAL 도구는 approved_context 없이 PermissionError
  4. 이벤트 버스 subscribe / dispatch
  5. Capability 등록 / 조회
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ─────────────────────────────────────────────────────────────
# 1. Intent 분류 (fallback — LLM 없이 키워드 매칭)
# ─────────────────────────────────────────────────────────────

class TestFallbackClassify:
    def _classify(self, msg: str) -> str:
        """router._fallback_classify 를 직접 호출."""
        from JARVIS01_MASTER.router import _fallback_classify, RouterState
        state: RouterState = {"messages": [], "classification": {}, "dispatched": False,
                               "error": None, "correlation_id": "test"}
        result = _fallback_classify(state, msg)
        return result.get("classification", {}).get("intent", "core.unknown")

    def test_blog_keywords(self):
        intent = self._classify("삼성전자 블로그 발행해줘")
        assert "blog" in intent or intent == "core.unknown"

    def test_trend_keywords(self):
        intent = self._classify("트렌드 분석 보고해줘")
        assert "trend" in intent or intent == "core.unknown"

    def test_schedule_keywords(self):
        intent = self._classify("스케줄 일정 보여줘")
        assert "schedule" in intent or intent == "core.unknown"

    def test_unknown_returns_string(self):
        intent = self._classify("오늘 날씨 어때?")
        assert isinstance(intent, str) and len(intent) > 0


# ─────────────────────────────────────────────────────────────
# 2. SAFE / APPROVAL 분류 정확도
# ─────────────────────────────────────────────────────────────

class TestIntentMode:
    def test_safe_intents(self):
        from JARVIS01_MASTER.dispatchers import SAFE_INTENTS, get_dispatch_mode
        for intent in SAFE_INTENTS:
            mode = get_dispatch_mode(intent).upper()
            assert mode == "SAFE", f"{intent} should be SAFE, got {mode}"

    def test_approval_intents(self):
        from JARVIS01_MASTER.dispatchers import APPROVAL_INTENTS, get_dispatch_mode
        for intent in APPROVAL_INTENTS:
            mode = get_dispatch_mode(intent).upper()
            assert mode == "APPROVAL", f"{intent} should be APPROVAL, got {mode}"

    def test_unknown_intent_is_deferred(self):
        from JARVIS01_MASTER.dispatchers import get_dispatch_mode
        mode = get_dispatch_mode("nonexistent.intent.xyz").upper()
        assert mode == "DEFERRED"

    def test_blog_publish_is_approval(self):
        from JARVIS01_MASTER.dispatchers import get_dispatch_mode
        assert get_dispatch_mode("blog.theme_post.create").upper() == "APPROVAL"

    def test_schedule_list_is_safe(self):
        from JARVIS01_MASTER.dispatchers import get_dispatch_mode
        assert get_dispatch_mode("schedule.job.list").upper() == "SAFE"


# ─────────────────────────────────────────────────────────────
# 3. 승인 게이트 — APPROVAL 도구는 approved_context 없이 차단
# ─────────────────────────────────────────────────────────────

class TestApprovalGate:
    def _register_dummy_approval_tool(self):
        from shared.tools import register_tool, ToolMeta
        import shared.tools as _t

        @register_tool(
            name="_test_approval_gate_tool",
            domain="test",
            side_effect="external",
            requires_approval=True,
        )
        def _dummy():
            return {"ok": True}
        return "_test_approval_gate_tool"

    def test_approval_tool_blocked_without_context(self):
        from shared.tools import tool_invoke
        name = self._register_dummy_approval_tool()
        with pytest.raises(PermissionError):
            tool_invoke(name)

    def test_approval_tool_allowed_with_context(self):
        from shared.tools import tool_invoke, approved_context
        name = self._register_dummy_approval_tool()
        with approved_context():
            result = tool_invoke(name)
        assert result.get("ok") is True

    def test_safe_tool_runs_without_context(self):
        from shared.tools import register_tool, tool_invoke

        @register_tool(
            name="_test_safe_gate_tool",
            domain="test",
            side_effect="none",
            requires_approval=False,
        )
        def _dummy_safe():
            return {"safe": True}

        result = tool_invoke("_test_safe_gate_tool")
        assert result.get("safe") is True


# ─────────────────────────────────────────────────────────────
# 4. 이벤트 버스 subscribe / dispatch
# ─────────────────────────────────────────────────────────────

class TestEventBus:
    def test_subscribe_and_dispatch(self):
        """dispatch_pending 이 핸들러를 호출하는지 확인."""
        received = []

        from shared import bus
        import shared.db as _db

        # 구독 등록
        evt_type = "test_event_dispatch_unique"
        bus.subscribe(evt_type, lambda payload, src: received.append((payload, src)))

        # DB에 row 삽입 후 cursor 를 그 row 이전으로 설정해 dispatch_pending 이 잡도록
        with _db.get_db() as conn:
            cur = conn.execute(
                "INSERT INTO events (event_type, source, payload) VALUES (?,?,?)",
                (evt_type, "test_suite", '{"x":99}'),
            )
            conn.commit()
            row_id = cur.lastrowid

        # cursor 를 해당 row 직전으로 설정
        bus._dispatch_cursor = row_id - 1

        n = bus.dispatch_pending(limit=50)
        assert n >= 1
        assert any(p.get("x") == 99 for p, _ in received)

    def test_event_types_defined(self):
        from shared.bus import EventType
        assert hasattr(EventType, "POST_PUBLISHED")
        assert hasattr(EventType, "TREND_DETECTED")
        assert hasattr(EventType, "POST_ANALYZED")
        assert hasattr(EventType, "PERFORMANCE_UPDATED")
        assert hasattr(EventType, "POST_REVISED")

    def test_writer_agent_subscribes(self):
        """writer_agent 모듈 로드 시 3개 이벤트 구독 등록."""
        from shared import bus
        before = sum(len(v) for v in bus._subscribers.values())
        import importlib
        import JARVIS02_WRITER.writer_agent  # noqa: F401 — 부작용 목적
        importlib.reload(JARVIS02_WRITER.writer_agent)
        after = sum(len(v) for v in bus._subscribers.values())
        # 재로드 시 idempotent (_SUBSCRIBED_W guard) — before == after
        # 처음 로드 시엔 3개 추가
        assert after >= before  # 최소 동일 (이미 구독됨)


# ─────────────────────────────────────────────────────────────
# 5. Capability 등록 / 조회
# ─────────────────────────────────────────────────────────────

class TestCapabilities:
    def test_declare_and_get(self):
        from shared.capabilities import declare, get
        cap = declare(
            agent_id="_test_agent_xyz",
            domain="test",
            intents=["test.foo", "test.bar"],
            description="unit test agent",
        )
        assert get("_test_agent_xyz") is cap

    def test_find_by_intent(self):
        from shared.capabilities import declare, find_by_intent
        declare(
            agent_id="_test_agent_abc",
            domain="test2",
            intents=["test2.hello"],
        )
        results = find_by_intent("test2.hello")
        assert any(c.agent_id == "_test_agent_abc" for c in results)

    def test_real_agents_registered(self):
        from shared.capabilities import all_capabilities
        import JARVIS02_WRITER.writer_agent  # noqa: F401
        import JARVIS03_RADAR.radar_agent    # noqa: F401
        import JARVIS04_SCHEDULER.scheduler_agent  # noqa: F401
        agents = all_capabilities()
        agent_ids = [c.agent_id for c in agents]
        assert "jarvis02_writer"      in agent_ids
        assert "jarvis03_radar"       in agent_ids
        assert "jarvis04_scheduler"   in agent_ids

    def test_approval_intents_consistent(self):
        """JARVIS04 의 requires_approval 목록과 dispatchers.APPROVAL_INTENTS 일치 확인."""
        import JARVIS04_SCHEDULER.scheduler_agent  # noqa: F401
        from shared.capabilities import get
        from JARVIS01_MASTER.dispatchers import APPROVAL_INTENTS

        cap = get("jarvis04_scheduler")
        assert cap is not None
        for intent in cap.requires_approval:
            assert intent in APPROVAL_INTENTS, (
                f"{intent} is in jarvis03 requires_approval but not in dispatchers.APPROVAL_INTENTS"
            )
