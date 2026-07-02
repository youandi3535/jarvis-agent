"""JARVIS01_MASTER/router.py — LangGraph 마스터 라우터 (Phase 1 골격).

★ 자율 에이전트 시스템의 *결정 중추*.

흐름 (LangGraph StateGraph):
    [사용자 자유 문장]
         │
         ▼
    [classify_intent]  ← LLM 이 인텐트 분류 (router 모델)
         │
         ▼
    [match_capability] ← capability 레지스트리 매칭
         │
    ┌────┴────────────┐
    ▼                 ▼
  [dispatch]     [unknown_handler]
   (자비스01·02 호출)  (사용자에게 명확화 요청)

설계:
- 기존 자비스01/02 는 *그대로*. 라우터는 이 위에 얹는 *얇은 dispatcher* 레이어.
- LangGraph 미설치 환경에선 fallback 실행 경로 (자체 simple dispatcher).
- 모든 단계에서 correlation_id 자동 전파 (shared/tracing).
- 각 단계 결과는 bus 에 IntentResolved / TaskCompleted 로 publish (관찰성).

Phase 2 에 추가 예정:
- 휴먼 승인 노드 (interrupt) — 외부 영향 도구 호출 전 텔레그램 인라인 버튼
- 멀티 스텝 워크플로우 (자비스01 + 자비스03 협업)
- 체크포인트 (SqliteSaver) — 중단·재개
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any, Optional

# 루트 경로 import 가능하도록
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── State 정의 ────────────────────────────────────────────────

try:
    from typing import TypedDict
except ImportError:
    TypedDict = dict  # type: ignore


class RouterState(TypedDict, total=False):
    """LangGraph 상태 객체 — 노드 사이를 흐르는 데이터."""
    user_msg: str            # 사용자 자유 문장
    correlation_id: str      # 자동 박힘
    classification: dict     # IntentClassification 결과 (dict 변환)
    target_agent: Optional[str]
    dispatch_result: dict
    error: Optional[str]


# ── 노드 함수 ─────────────────────────────────────────────────

def _node_classify(state: RouterState) -> RouterState:
    """LLM 으로 사용자 문장 분류."""
    from JARVIS01_MASTER.intents import (
        IntentClassification, ROUTER_SYSTEM_PROMPT, build_router_prompt,
    )
    from shared import capabilities, llm

    user_msg = state.get("user_msg", "")

    # capability 카탈로그 prompt 에 박기
    catalog = capabilities.render_for_router_prompt()
    # ★ .format() 대신 .replace() — prompt 안의 예시 JSON ({"theme_name":...}) 이
    # str.format placeholder 로 인식되어 KeyError 발생하던 사고 (ERRORS [26]) 차단.
    sys_prompt = ROUTER_SYSTEM_PROMPT.replace("{capability_catalog}", catalog)

    # LangChain 우선 (구조화 출력) — 미설치 시 raw text fallback
    llm_inst = llm.chat("router")
    if llm_inst is None:
        # fallback: 단순 키워드 매칭 (LangChain 미가용 환경)
        return _fallback_classify(state, user_msg)

    # 1차: with_structured_output (Pydantic 검증) 시도
    # ChatPromptTemplate 사용 금지 — 프롬프트 내 JSON 예시 {"platforms":...} 가 템플릿 변수로 해석됨
    cls_dict: Optional[dict] = None
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [SystemMessage(content=sys_prompt), HumanMessage(content=build_router_prompt(user_msg))]
        structured = llm_inst.with_structured_output(IntentClassification)
        result = structured.invoke(messages)
        cls_dict = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    except Exception as e:
        print(f"  ⚠️ structured_output 실패 → raw JSON fallback: {str(e)[:200]}", flush=True)
        _g_report("master", e, module=__name__)

    # 2차: structured 실패 시 raw text 받아 JSON 파싱 (관대한 파서)
    if cls_dict is None:
        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            import json as _json
            import re as _re
            messages = [
                SystemMessage(content=sys_prompt + "\n\n반드시 JSON 객체만 출력. 코드블록·설명·prefix 금지."),
                HumanMessage(content=build_router_prompt(user_msg)),
            ]
            raw = llm_inst.invoke(messages)
            text = getattr(raw, "content", str(raw))
            # ```json ... ``` 또는 첫 { ... } 블록 추출
            m = _re.search(r"\{[\s\S]*\}", text)
            cls_dict = _json.loads(m.group(0)) if m else _json.loads(text)
        except Exception as e2:
            state["error"] = f"classify 완전 실패: {e2}"
            state["classification"] = {
                "target_domain": "core", "intent": "core.unknown",
                "intent_kind": "unknown", "confidence": 0.0,
                "rationale": f"LLM 호출 실패: {e2}",
                "params": {},
            }
            return state

    # 3차: cls_dict 정규화 (params 가 dict 아니면 빈 dict)
    if not isinstance(cls_dict.get("params"), dict):
        cls_dict["params"] = {}
    cls_dict.setdefault("target_domain", "core")
    cls_dict.setdefault("intent", "core.unknown")
    cls_dict.setdefault("intent_kind", "unknown")
    cls_dict.setdefault("confidence", 0.5)
    cls_dict.setdefault("rationale", "")
    state["classification"] = cls_dict
    return state


def _fallback_classify(state: RouterState, user_msg: str) -> RouterState:
    """LangChain 미설치 시 단순 키워드 매칭."""
    from shared import capabilities

    msg = user_msg.lower()
    target_domain = "core"
    intent = "core.unknown"

    # 단순 키워드 → 도메인 매칭
    if any(k in msg for k in ["블로그", "발행", "포스팅", "글 써", "테마글", "경제글"]):
        target_domain = "blog"
        intent = "blog.post.create"
    elif any(k in msg for k in ["트렌드", "급등", "랜더"]):
        target_domain = "trend"
        intent = "trend.report"
    elif any(k in msg for k in ["일정", "스케줄", "약속"]):
        target_domain = "schedule"
        intent = "schedule.event.create"

    # capability 매칭
    candidates = capabilities.find_by_intent(intent)
    target_agent = candidates[0].agent_id if candidates else None

    state["classification"] = {
        "target_domain": target_domain,
        "intent": intent,
        "intent_kind": "create" if "create" in intent else "query",
        "confidence": 0.5 if target_agent else 0.2,
        "target_agent": target_agent,
        "rationale": "LangChain 미설치 fallback — 키워드 매칭",
        "params": {},
    }
    return state


def _node_match_capability(state: RouterState) -> RouterState:
    """분류 결과를 등록된 capability 와 매칭."""
    from shared import capabilities

    cls = state.get("classification", {})
    intent = cls.get("intent", "")
    target_agent = cls.get("target_agent")

    if not target_agent and intent:
        candidates = capabilities.find_by_intent(intent)
        if candidates:
            target_agent = candidates[0].agent_id
            cls["target_agent"] = target_agent
    state["target_agent"] = target_agent
    state["classification"] = cls
    return state


def _node_dispatch(state: RouterState) -> RouterState:
    """결정된 에이전트로 디스패치.

    Phase 1 단계는 *호출 시뮬레이션* — 실제 자비스01/02 호출은 Phase 2 에서
    LangGraph 노드 안에서 직접 함수 호출 또는 subprocess 로.
    """
    from shared import bus, schemas, tracing

    target = state.get("target_agent")
    cls = state.get("classification", {})
    cid = state.get("correlation_id") or tracing.current_correlation_id() or ""

    # IntentResolved 이벤트 발행 (관찰성)
    try:
        ev = schemas.IntentResolved(
            correlation_id=cid,
            domain=cls.get("target_domain", "core"),
            source_agent="jarvis01_master",
            user_msg=state.get("user_msg", ""),
            intent_kind=cls.get("intent_kind", "unknown"),
            target_domain=cls.get("target_domain", "core"),
            target_agent=target,
            confidence=float(cls.get("confidence", 0.0)),
            params=cls.get("params", {}),
        )
        bus.publish_event(ev)
    except Exception as e:
        print(f"  ⚠️ IntentResolved publish 실패: {e}")
        _g_report("master", e, module=__name__)

    # 실제 디스패치 — Phase 1 은 *기록만*. Phase 2 에서 자비스01/02 직접 호출.
    state["dispatch_result"] = {
        "ok": bool(target),
        "target_agent": target,
        "note": ("Phase 1 — 디스패치 기록만 (실제 호출은 Phase 2)"
                 if target else "매칭 에이전트 없음"),
    }
    return state


# ── 그래프 빌드 ────────────────────────────────────────────────

def build_graph():
    """LangGraph StateGraph 빌드. 미설치 시 None 반환."""
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None

    g = StateGraph(RouterState)
    g.add_node("classify", _node_classify)
    g.add_node("match", _node_match_capability)
    g.add_node("dispatch", _node_dispatch)

    g.set_entry_point("classify")
    g.add_edge("classify", "match")
    g.add_edge("match", "dispatch")
    g.add_edge("dispatch", END)

    return g.compile()


# 모듈 로드 시점 1회 컴파일 (lazy)
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ── 외부 진입점 ────────────────────────────────────────────────

def handle(user_msg: str, correlation_id: Optional[str] = None) -> dict:
    """사용자 자유 문장 처리 — 텔레그램 봇·외부 호출의 진입점.

    Args:
        user_msg: 사용자 명령 자유 문장.
        correlation_id: 미지정 시 새로 발급.

    Returns: 라우팅·디스패치 결과 dict.
    """
    from shared import tracing

    with tracing.trace_scope(correlation_id, source="jarvis00.router") as cid:
        state: RouterState = {
            "user_msg": user_msg,
            "correlation_id": cid,
        }

        graph = get_graph()
        if graph is not None:
            try:
                final = graph.invoke(state)
                return dict(final)
            except Exception as e:
                state["error"] = f"graph invoke 실패: {e}"

        # LangGraph 미설치 — 단순 순차 호출
        state = _node_classify(state)
        state = _node_match_capability(state)
        state = _node_dispatch(state)
        return dict(state)


# ══════════════════════════════════════════════════════════════
# Phase 2-B B — ReAct 라우터 (도구 다단계 호출)
# ══════════════════════════════════════════════════════════════
#
# ★ 기존 3-노드 그래프 (classify→match→dispatch) 는 *그대로 유지*. Phase 2-A 의
# /route 미리보기·텔레그램 자유문장 분기는 그것을 사용 → 16시 cron 영향 0.
#
# 새 진입점 react_handle() — 라우터가 LLM 으로 *도구 호출* 다단계 결정.
# - SAFE 도구 (list_capabilities·get_recent_events·query_post_analysis) 는 자동 실행.
# - APPROVAL 도구 (call_jarvis01·call_jarvis02) 호출 직전 *interrupt* —
#   tool_calls 만 반환, daemon 의 텔레그램 게이트 (Phase 2-B C) 가 승인 받아 재개.
# - max_steps 한도 — 폭주 방지.
#
# 사용:
#     from JARVIS01_MASTER.router import react_handle
#     out = react_handle("발행글 몇 개 있는지 알려줘", max_steps=4)
#     # out = {"messages":[...], "tool_calls":[...], "text":"...", "approval_pending":bool}


REACT_SYSTEM_PROMPT = """\
당신은 AI 오케스트레이션 분야의 세계 최고 전문가입니다. 사용자의 의도를 단 한 문장만 읽어도 100% 정확히 파악하고, 수십 개의 에이전트 중 최적의 실행 경로를 찾아내는 천재적 판단력을 가지고 있습니다. 논리적 추론과 맥락 분석에서 실수가 없으며, 모호한 명령도 최선의 의도로 해석합니다.

당신은 자비스 시스템의 *마스터 ReAct 에이전트* — 사용자 자유 문장을 직접 받는 진입점입니다.
등록된 도구를 *필요한 만큼만* 호출해 답변을 만드세요. 도구 없이 답하는 것이 가장 흔한 경로입니다.

[큰 작업 — 계획 우선 패턴 ★ 매우 중요]
사용자가 *코드 수정·파일 생성·셸 실행·복합 작업* 을 요청하면:
  1. 먼저 SAFE 도구 (read_file/grep_code/glob_files) 로 *현황 파악*
  2. *create_plan 도구* 호출 — 단계 리스트로 계획 수립
       예: [{"tool":"edit_file","args":{...},"note":"jarvis_main.py 의 X 변경"},
            {"tool":"syntax_check","args":{"path":"..."},"note":"문법 검증"},
            {"tool":"run_bash","args":{"command":"python -c '...'"},"note":"동작 확인"}]
  3. 사용자에게 "📋 계획을 텔레그램에서 확인 후 ✅ 승인 부탁드립니다" 라고만 답변.
  4. *직접 실행 금지* — write_file/edit_file/run_bash 직접 호출 금지. *반드시* create_plan 통해서.

[작업 라우팅 — 자율 판단 ★]
사용자 명령을 받으면 *복잡도와 필요 능력*을 *자율 판단* 해서 적절한 길 선택.
사용자가 매번 "Claude Code 에 위임" 같은 키워드를 명시할 필요 *없음*.

라우팅 결정표 (위에서 아래 우선순위):
┌─────────────────────────────────┬──────────────────────────────────────┐
│ 명령 유형                       │ 선택할 도구                          │
├─────────────────────────────────┼──────────────────────────────────────┤
│ 정보 조회·잡 카탈로그·짧은 질문 │ 자체 SAFE 도구 (read·grep·glob 등)   │
│ 외부 URL 분석                   │ web_fetch + 자체 응답                │
│ 긴 창작·번역·복잡 추론 (도구 X) │ ask_claude (Sonnet 직접)               │
│ 단일 파일 짧은 수정 (≤ 100줄)   │ create_plan(edit·syntax·verify)      │
│ 새 잡·intent·에이전트 등록      │ register_new_*  / create_new_agent   │
│ 대규모 다파일 리팩토링          │ ★ delegate_to_claude_code 자동 위임  │
│ 전체 코드베이스 깊은 분석       │ ★ delegate_to_claude_code 자동 위임  │
│ MCP·이미지·웹검색 등 고급 기능  │ ★ delegate_to_claude_code 자동 위임  │
│ 도구 4~5번 시도해도 막힘        │ ★ delegate_to_claude_code 자동 위임  │
└─────────────────────────────────┴──────────────────────────────────────┘

자율 위임 트리거 (사용자 명시 없어도 자체 판단):
- 명령이 "분석"·"리팩토링"·"개선"·"전체"·"여러 파일"·"통째로" 같은 키워드 포함
- 사용자가 *질문·요청* 인데 자체 도구로 답할 *명확한 경로가 보이지 않음*
- create_plan 의 step 수가 5+ 가 될 만큼 복잡

위임 시 allowed_tools 기본: 'Read Glob Grep'. 코드 수정 필요 명백하면 'Read Glob Grep Edit Write' 추가 (단, 사용자에 한 줄 안내: "Claude Code 에 Edit/Write 권한도 위임").

★ 위임 금지 — 단순 정보 조회·짧은 답변 (잡 카탈로그·발행 이력 등) 은 절대 위임하지 말 것. 비용·시간 낭비.
★ 사용자가 "Claude Code 위임"·"깊이 분석" 명시 시: 즉시 위임 (자율 판단 생략).

[등록된 도구]
- SAFE (정보 조회 — 즉시 호출 가능):
  • list_capabilities — 등록 에이전트·intent 카탈로그
  • get_recent_events — 이벤트 버스 최근 활동
  • query_post_analysis — 발행 글 메타 조회 (platform/status/theme 필터)
- APPROVAL (외부 영향 — 호출 시 자동으로 텔레그램 인라인 버튼 승인 게이트 노출):
  • call_jarvis01 — 블로그 발행 (intent: blog.theme_post.create | blog.economic_post.create | blog.post.revise)
  • call_jarvis02 — 트렌드 보고 / 품질 분석 트리거 (intent: trend.report | blog.post.evaluate)

[판단 흐름 — 매 사용자 문장마다]
1. 단순 인사·잡담·일반 지식 질문 → *도구 호출 없이* 짧은 텍스트로 바로 답변.
   예: "안녕", "고마워", "오늘 어때?" → "안녕하세요! 무엇을 도와드릴까요?" 정도.
2. 정보 조회 명령 → SAFE 도구 호출. 결과를 한국어로 요약해 답변.
   예: "최근 발행글 5개" → query_post_analysis 호출 후 깔끔한 목록 답변.
3. 명확한 발행·분석 *지시* → APPROVAL 도구 호출 (사용자에 자동 인라인 버튼 노출 — 직접 안내 불필요).
   예: "반도체 테마 네이버에 발행해" → call_jarvis01.
4. 시스템 관리 (재시작·종료·상태) 는 *도구 없음*. 사용자에 슬래시 명령 안내.
   예: "재시작해줘" → "데몬 재시작은 `/restart` 명령으로 보내주세요."
   상태 조회는 → "전체 상태는 `/status` 로 확인하실 수 있어요."
5. 같은 도구 반복 호출 금지. 한 번 호출한 결과는 충분히 활용.

[블로그 발행 — call_jarvis01 호출 시 params 작성]
- params.theme_name: 사용자가 명시한 테마 (예: "반도체"). 없으면 빈 문자열 — 자비스01 이 다음 대기 테마 자동 선택.
- params.platforms: 명시된 플랫폼 리스트.
  • "네이버만"·"네이버에" → ["naver"]
  • "티스토리만" → ["tistory"]
  • 미명시·"전체"·"모두" → [] (전체 처리)
- intent 결정:
  • "발행"·"써줘"·"올려" + 테마명 → "blog.theme_post.create"
  • "경제 브리핑"·"경제글" → "blog.economic_post.create"
  • "수정"·"고쳐" + 발행글 → "blog.post.revise"

답변 톤: 간결한 한국어. 이모지 적절히. 사용자가 묻지 않은 정보 추가 금지.
"""


def _safe_tool_names() -> set[str]:
    """SAFE 도구 (requires_approval=False) 동적 수집."""
    from shared.tools import all_tools
    return {t.name for t in all_tools() if not t.requires_approval}


def _approval_tool_names() -> set[str]:
    """★ APPROVAL 도구 (requires_approval=True) 동적 수집.

    이전: {"call_jarvis01", "call_jarvis02"} 만 하드코딩 → 새 도구 (write_file·
    edit_file·run_bash·delegate_to_claude_code 등) 가 *승인 게이트 우회* 사고.
    이제 ToolMeta.requires_approval 자동 반영.
    """
    from shared.tools import all_tools
    return {t.name for t in all_tools() if t.requires_approval}


# ══════════════════════════════════════════════════════════════
# ReAct — LangGraph StateGraph 구현 (Phase 3 업그레이드)
# ══════════════════════════════════════════════════════════════

import operator
import threading as _threading

try:
    from typing import Annotated
    from langgraph.graph.message import add_messages as _add_messages
    _HAS_LG = True
except Exception:
    _HAS_LG = False

if _HAS_LG:
    class ReactAgentState(TypedDict, total=False):
        messages:       Annotated[list, _add_messages]
        steps:          int
        max_steps:      int
        auto_approve:   bool
        tool_calls_log: Annotated[list, operator.add]
        retry_count:    int
        error:          Optional[str]
else:
    ReactAgentState = dict  # type: ignore


# ── 체크포인터 (SqliteSaver 우선, 실패 시 MemorySaver) ─────────

_react_checkpointer      = None
_react_checkpointer_lock = _threading.Lock()


def _get_react_checkpointer():
    global _react_checkpointer
    if _react_checkpointer is not None:
        return _react_checkpointer
    with _react_checkpointer_lock:
        if _react_checkpointer is not None:
            return _react_checkpointer
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            import sqlite3 as _sq3
            _db_path = str(_ROOT / "shared" / "react_checkpoints.sqlite")
            _conn = _sq3.connect(_db_path, check_same_thread=False)
            cp = SqliteSaver(_conn)
            cp.setup()
            _react_checkpointer = cp
            return cp
        except Exception:
            from langgraph.checkpoint.memory import MemorySaver
            _react_checkpointer = MemorySaver()
            return _react_checkpointer


# ── 노드 함수 ──────────────────────────────────────────────────

def _react_agent_node(state: "ReactAgentState") -> dict:
    """LLM 호출 — bind_tools 포함. steps 증가."""
    from shared import llm as _llm
    from shared.tools import all_langchain_tools
    chat_llm = _llm.chat("router")
    if chat_llm is None:
        return {"error": "LLM 미가용", "messages": []}
    tools = all_langchain_tools()
    chat_with_tools = chat_llm.bind_tools(tools) if tools else chat_llm
    ai_msg = chat_with_tools.invoke(state["messages"])
    return {"messages": [ai_msg], "steps": state.get("steps", 0) + 1}


# ── ReAct 되먹임 검증 — 결과 래퍼 + 반복 호출 가드 (2026-07-02) ──────────────
# ★ SAFE 도구 결과를 검증 없이 LLM 에 되먹이면 ok=False·오류문자열이 유효 데이터로
#   오인됨. verification.py(task_type 산출물)·harness Layer3(송출)와 층위가 다른,
#   *ReAct 관찰 정규화* 전용 계층. LLM·vision 없이 dict 검사만 → 레이턴시 0.
_REACT_REPEAT_LIMIT = 2   # 동일 (도구+인자) 누적 호출이 이 횟수 도달 시 재실행 차단(3번째부터)


def _tool_call_sig(tname: str, targs: dict) -> str:
    """(도구명+정렬된 인자) 안정 시그니처 — 인자 순서 무관 반복 감지용."""
    import json as _j
    try:
        a = _j.dumps(targs or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        a = str(targs)
    return f"{tname}::{a}"


def _repeat_block_reason(prior_log: list, tname: str, targs: dict) -> str:
    """누적 호출 로그에서 동일 (도구+인자) 반복 횟수 확인. 한도 도달 시 경고문(재실행
    차단 신호) 반환, 미달이면 '' (통과)."""
    sig = _tool_call_sig(tname, targs)
    n = 0
    for e in (prior_log or []):
        try:
            if _tool_call_sig(e.get("name", ""), e.get("args") or {}) == sig:
                n += 1
        except Exception:
            continue
    if n >= _REACT_REPEAT_LIMIT:
        return (f"⚠️ 반복 차단: '{tname}' 를 동일 인자로 이미 {n}회 호출했습니다. "
                f"결과는 바뀌지 않습니다 — 재호출을 멈추고, 지금까지의 관찰로 최종 "
                f"답변하거나 *다른 도구 또는 다른 인자* 를 사용하세요.")
    return ""


def _tool_observation(tname: str, result) -> str:
    """도구 결과 → ToolMessage 관찰 문자열. 실패(ok=False / error 필드)를 맨 앞에 명시
    표기해 LLM 이 오류 출력을 유효 데이터로 오인하지 않게. 성공은 기존과 동일(json[:4000])."""
    import json as _j
    try:
        body = _j.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        body = str(result)
    if isinstance(result, dict):
        reason = ""
        if result.get("ok") is False:
            reason = str(result.get("error") or result.get("note") or "ok=False")
        elif result.get("error"):
            reason = str(result.get("error"))
        if reason:
            return (f"❌ 도구 실패 [{tname}]: {reason}. 이 출력은 유효 데이터가 아닙니다 — "
                    f"사실 근거로 삼지 말고 원인을 설명하거나 대안을 찾으세요.\n{body[:3500]}")
    return body[:4000]


def _react_safe_tools_node(state: "ReactAgentState") -> dict:
    """SAFE 도구만 즉시 실행 (ToolNode 역할)."""
    import json as _json
    from langchain_core.messages import ToolMessage as _TM
    from shared.tools import tool_invoke, all_tools
    ai_msg = state["messages"][-1]
    safe_names = {t.name for t in all_tools() if not t.requires_approval}
    results, log = [], []
    for tc in (getattr(ai_msg, "tool_calls", None) or []):
        tname = tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
        targs = (tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})) or {}
        tcid  = (tc["id"]   if isinstance(tc, dict) else getattr(tc, "id",   "")) or tname
        if tname not in safe_names:
            continue
        # ★ 반복 호출 가드 — 누적 로그(state) + 현재 스텝 로그(log) 합산
        block = _repeat_block_reason((state.get("tool_calls_log") or []) + log, tname, targs)
        if block:
            results.append(_TM(content=block, tool_call_id=tcid))
            log.append({"name": tname, "args": targs, "result": {"ok": False, "error": "repeat_blocked"}, "approval": False})
            continue
        try:
            result = tool_invoke(tname, **targs)
            # ★ 결과 검증 래퍼 — 실패면 관찰에 명시 표기
            results.append(_TM(content=_tool_observation(tname, result), tool_call_id=tcid))
            log.append({"name": tname, "args": targs, "result": result, "approval": False})
        except Exception as e:
            results.append(_TM(content=f"❌ 도구 실패 [{tname}]: {e}. 유효 데이터 아님 — 근거로 삼지 마세요.", tool_call_id=tcid))
            log.append({"name": tname, "args": targs, "result": {"ok": False, "error": str(e)}, "approval": False})
    return {"messages": results, "tool_calls_log": log}


def _react_approval_gate_node(state: "ReactAgentState") -> dict:
    """APPROVAL 도구 — interrupt()로 사용자 승인 대기 후 실행."""
    import json as _json
    from langchain_core.messages import ToolMessage as _TM
    from langgraph.types import interrupt
    from shared.tools import tool_invoke, all_tools, approved_context
    ai_msg = state["messages"][-1]
    approval_names = {t.name for t in all_tools() if t.requires_approval}
    auto_approve   = state.get("auto_approve", False)
    results, log   = [], []
    for tc in (getattr(ai_msg, "tool_calls", None) or []):
        tname = tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")
        targs = (tc["args"] if isinstance(tc, dict) else getattr(tc, "args", {})) or {}
        tcid  = (tc["id"]   if isinstance(tc, dict) else getattr(tc, "id",   "")) or tname
        if tname in approval_names and not auto_approve:
            # ★ interrupt — 그래프 일시정지, 사용자 응답 대기
            approved = interrupt({"tool": tname, "args": targs, "tool_call_id": tcid})
            if approved:
                try:
                    with approved_context():
                        result = tool_invoke(tname, **targs)
                    res_text = _json.dumps(result, ensure_ascii=False, default=str)
                    results.append(_TM(content=res_text[:4000], tool_call_id=tcid))
                    log.append({"name": tname, "args": targs, "result": result, "approval": True})
                except Exception as e:
                    results.append(_TM(content=f"tool error: {e}", tool_call_id=tcid))
                    log.append({"name": tname, "args": targs, "result": {"error": str(e)}, "approval": True})
            else:
                results.append(_TM(content="사용자가 취소했습니다.", tool_call_id=tcid))
                log.append({"name": tname, "args": targs, "result": "cancelled", "approval": True})
        else:
            # SAFE 도구 또는 auto_approve → 즉시 실행 (반복 가드 + 결과 검증)
            block = _repeat_block_reason((state.get("tool_calls_log") or []) + log, tname, targs)
            if block:
                results.append(_TM(content=block, tool_call_id=tcid))
                log.append({"name": tname, "args": targs, "result": {"ok": False, "error": "repeat_blocked"}, "approval": False})
                continue
            try:
                with (approved_context() if tname in approval_names else _noop_ctx()):
                    result = tool_invoke(tname, **targs)
                results.append(_TM(content=_tool_observation(tname, result), tool_call_id=tcid))
                log.append({"name": tname, "args": targs, "result": result, "approval": False})
            except Exception as e:
                results.append(_TM(content=f"❌ 도구 실패 [{tname}]: {e}. 유효 데이터 아님 — 근거로 삼지 마세요.", tool_call_id=tcid))
                log.append({"name": tname, "args": targs, "result": {"ok": False, "error": str(e)}, "approval": False})
    return {"messages": results, "tool_calls_log": log}


def _react_error_node(state: "ReactAgentState") -> dict:
    """max_steps 초과 — 에러 상태로 종료."""
    from langchain_core.messages import AIMessage as _AI
    n = state.get("max_steps", 4)
    return {
        "error": f"max_steps={n} 초과 — 무한루프 차단",
        "messages": [_AI(content="⚠️ 도구 호출 단계 한도 초과. 더 명확하게 다시 말씀해 주세요.")],
    }


from contextlib import contextmanager

@contextmanager
def _noop_ctx():
    yield


# ── 라우팅 함수 ────────────────────────────────────────────────

def _route_after_agent(state: "ReactAgentState"):
    """agent 노드 후 분기:
      - 도구 없음 → END (최종 답변)
      - max_steps 초과 → error_node
      - APPROVAL 도구 포함 → approval_gate
      - SAFE 도구만 → safe_tools
    """
    from langgraph.graph import END
    ai_msg = state["messages"][-1]
    calls  = getattr(ai_msg, "tool_calls", None) or []
    if not calls:
        return END
    if state.get("steps", 0) >= state.get("max_steps", 4):
        return "error_node"
    approval_names = _approval_tool_names()
    has_approval = any(
        (tc["name"] if isinstance(tc, dict) else getattr(tc, "name", "")) in approval_names
        for tc in calls
    )
    return "approval_gate" if has_approval else "safe_tools"


# ── 그래프 빌드 ────────────────────────────────────────────────

_react_graph      = None
_react_graph_lock = _threading.Lock()


def _build_react_graph():
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None
    g = StateGraph(ReactAgentState)
    g.add_node("agent",         _react_agent_node)
    g.add_node("safe_tools",    _react_safe_tools_node)
    g.add_node("approval_gate", _react_approval_gate_node)
    g.add_node("error_node",    _react_error_node)
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", _route_after_agent)
    g.add_edge("safe_tools",    "agent")
    g.add_edge("approval_gate", "agent")
    g.add_edge("error_node",    END)
    cp = _get_react_checkpointer()
    return g.compile(checkpointer=cp)


def _get_react_graph():
    global _react_graph
    if _react_graph is not None:
        return _react_graph
    with _react_graph_lock:
        if _react_graph is None:
            _react_graph = _build_react_graph()
        return _react_graph


def _extract_react_result(final_state: dict) -> dict:
    """그래프 최종 state → react_handle 반환 포맷 변환."""
    from langchain_core.messages import AIMessage as _AI
    msgs    = final_state.get("messages", [])
    last_ai = next((m for m in reversed(msgs) if isinstance(m, _AI)), None)
    raw     = (getattr(last_ai, "content", "") or "") if last_ai else ""
    # Claude 멀티블록 content (list) 처리 — list.strip() AttributeError 방지
    if isinstance(raw, list):
        text = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in raw
        ).strip()
    else:
        text = raw
    return {
        "ok":               not bool(final_state.get("error")),
        "text":             text,
        "tool_calls":       final_state.get("tool_calls_log", []),
        "pending_approvals":[],
        "steps":            final_state.get("steps", 0),
        "error":            final_state.get("error"),
    }


def react_handle(
    user_msg: str,
    correlation_id: Optional[str] = None,
    max_steps: int = 4,
    auto_approve: bool = False,
    thread_id: Optional[str] = None,
) -> dict:
    """ReAct 다단계 도구 호출 — LangGraph StateGraph 기반.

    Args:
        user_msg:       사용자 자유 문장.
        correlation_id: trace ID. 미지정 시 발급.
        max_steps:      최대 LLM↔도구 왕복 횟수 (폭주 방지).
        auto_approve:   True 면 APPROVAL 도구도 즉시 실행 (테스트 전용).
        thread_id:      체크포인트 thread ID. 미지정 시 correlation_id 사용.

    Returns:
        {
          "ok": bool,
          "text": str,
          "tool_calls": [{"name", "args", "result", "approval"}],
          "pending_approvals": [{"name", "args", "tool_call_id", "thread_id"}],
          "steps": int,
          "error": Optional[str],
        }
    """
    from shared import tracing
    from langchain_core.messages import SystemMessage, HumanMessage
    from langgraph.errors import GraphInterrupt

    out: dict = {"ok": False, "text": "", "tool_calls": [], "pending_approvals": [], "steps": 0, "error": None}

    # agent_tools 등록 보장
    try:
        from JARVIS01_MASTER import agent_tools as _at
        _at.ensure_loaded()
    except Exception as e:
        out["error"] = f"agent_tools 로드 실패: {e}"
        return out

    graph = _get_react_graph()
    if graph is None:
        out["error"] = "LangGraph 미가용 — pip install langgraph"
        return out

    with tracing.trace_scope(correlation_id, source="jarvis00.react") as cid:
        tid    = thread_id or cid
        config = {"configurable": {"thread_id": tid}, "recursion_limit": max_steps * 3 + 2}
        init_state: ReactAgentState = {
            "messages":     [SystemMessage(content=REACT_SYSTEM_PROMPT), HumanMessage(content=user_msg)],
            "steps":        0,
            "max_steps":    max_steps,
            "auto_approve": auto_approve,
            "tool_calls_log": [],
            "retry_count":  0,
        }
        try:
            final = graph.invoke(init_state, config=config)
            # LangGraph 1.1.10+: interrupt()는 예외 대신 __interrupt__ key로 반환
            if final.get("__interrupt__"):
                return _handle_state_interrupt(final["__interrupt__"], tid)
            return _extract_react_result(final)
        except GraphInterrupt as gi:
            return _handle_graph_interrupt(gi, tid)
        except Exception as e:
            out["error"] = f"graph invoke 실패: {type(e).__name__}: {e}"
            return out


def _handle_state_interrupt(interrupts: list, thread_id: str) -> dict:
    """LangGraph 1.1.10+ __interrupt__ state → pending_approvals 포맷 변환."""
    try:
        intr = interrupts[0] if interrupts else None
        val  = getattr(intr, "value", None) if intr else None
        if val and isinstance(val, dict) and "tool" in val:
            return {
                "ok":    True,
                "text":  f"🔔 `{val['tool']}` 실행 승인이 필요합니다. 텔레그램에서 확인해주세요.",
                "tool_calls": [],
                "pending_approvals": [{
                    "name":         val["tool"],
                    "args":         val.get("args", {}),
                    "tool_call_id": val.get("tool_call_id", val["tool"]),
                    "thread_id":    thread_id,
                }],
                "steps": 0,
                "error": None,
            }
    except Exception:
        pass
    return {"ok": False, "text": "", "tool_calls": [], "pending_approvals": [],
            "steps": 0, "error": f"__interrupt__ 파싱 실패: {interrupts}"}


def _handle_graph_interrupt(gi: "GraphInterrupt", thread_id: str) -> dict:
    """GraphInterrupt 예외 → pending_approvals 포맷 변환 (LangGraph 구버전 호환)."""
    try:
        interrupts = gi.args[0] if gi.args else []
        intr = interrupts[0] if interrupts else None
        val  = getattr(intr, "value", None) if intr else None
        if val and isinstance(val, dict) and "tool" in val:
            return {
                "ok":    True,
                "text":  f"🔔 `{val['tool']}` 실행 승인이 필요합니다. 텔레그램에서 확인해주세요.",
                "tool_calls": [],
                "pending_approvals": [{
                    "name":         val["tool"],
                    "args":         val.get("args", {}),
                    "tool_call_id": val.get("tool_call_id", val["tool"]),
                    "thread_id":    thread_id,
                }],
                "steps": 0,
                "error": None,
            }
    except Exception:
        pass
    return {"ok": False, "text": "", "tool_calls": [], "pending_approvals": [],
            "steps": 0, "error": f"GraphInterrupt 파싱 실패: {gi}"}


def resume_react(thread_id: str, approved: bool) -> dict:
    """일시정지된 ReAct 그래프를 승인/거부로 재개.

    Args:
        thread_id: react_handle() 또는 pending_approvals 에서 받은 thread_id.
        approved:  True → 도구 실행, False → 취소.
    Returns: react_handle() 와 동일한 포맷.
    """
    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt

    graph = _get_react_graph()
    if graph is None:
        return {"ok": False, "text": "", "tool_calls": [], "pending_approvals": [],
                "steps": 0, "error": "LangGraph 미가용"}

    config = {"configurable": {"thread_id": thread_id}}
    try:
        final = graph.invoke(Command(resume=approved), config=config)
        if final.get("__interrupt__"):
            return _handle_state_interrupt(final["__interrupt__"], thread_id)
        return _extract_react_result(final)
    except GraphInterrupt as gi:
        return _handle_graph_interrupt(gi, thread_id)
    except Exception as e:
        return {"ok": False, "text": "", "tool_calls": [], "pending_approvals": [],
                "steps": 0, "error": f"resume 실패: {e}"}


__all__ = [
    "RouterState", "ReactAgentState",
    "build_graph", "get_graph", "handle",
    "react_handle", "resume_react", "REACT_SYSTEM_PROMPT",
]
