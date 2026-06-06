"""JARVIS01_MASTER/core_agent.py — 데몬 자동등록 진입점.

데몬이 부팅 시 _autoregister_agents() 가 이 파일을 발견 → register(scheduler, bus)
호출. capability 선언 + (선택) 스케줄 잡 등록.

JARVIS01 은 *마스터 라우터* 라 자체 cron 잡은 거의 없음. 대신 텔레그램 봇이
사용자 메시지를 router.handle() 로 흘림 (Phase 2 에서 통합).
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.capabilities import declare


def _status_section() -> str:
    lines = ["🧭 *JARVIS01 — MASTER (마스터 라우터)*"]
    try:
        from shared import capabilities as _caps
        n_caps    = len(_caps.all_capabilities())
        n_intents = len(_caps.list_intents())
        try:
            from JARVIS01_MASTER.router import get_graph as _gg
            graph_ok = _gg() is not None
        except Exception:
            graph_ok = False
        try:
            from shared import llm as _llm
            lc_ok = _llm.is_langchain_available()
        except Exception:
            lc_ok = False
        lines.append(
            f"{'✅' if graph_ok else '⚠️'} LangGraph 라우터  |  "
            f"{'✅' if lc_ok else '⚠️'} LangChain"
        )
        lines.append(f"📋 등록 에이전트 {n_caps}개  |  인텐트 {n_intents}개")
        agent_ids = ", ".join(
            c.agent_id.replace("jarvis", "J") for c in _caps.all_capabilities()
        )
        lines.append(f"   {agent_ids}")
    except Exception as e:
        lines.append(f"⚠️ 상태 조회 실패: {str(e)[:60]}")
    return "\n".join(lines)


# ── capability 선언 ──────────────────────────────────────────
CAPABILITIES = declare(
    agent_id="jarvis01_master",
    domain="core",
    intents=[
        "core.chat",          # 일반 잡담·인사
        "core.unknown",       # 분류 실패 처리
        "core.dispatch",      # 다른 에이전트로 라우팅
        "core.list_agents",   # 등록된 에이전트 카탈로그 (/agents)
        "core.preview_route", # 라우팅 미리보기 (/route 자유문장)
    ],
    tools=[
        # SAFE — ReAct 정보 수집
        "list_capabilities",
        "get_recent_events",
        "query_post_analysis",
        # APPROVAL — 위임 (텔레그램 게이트 필수, Phase 2-B C 통합)
        "call_jarvis01",
        "call_jarvis02",
        # Phase 3-A — 파일 도구 (코드 자가수정)
        "read_file", "glob_files", "grep_code", "syntax_check",  # SAFE
        "write_file", "edit_file",                                # APPROVAL
        # Phase 3-B — 셸 도구
        "run_bash",                                               # APPROVAL
        # Phase 3-C — 계획·실행
        "create_plan",                                            # APPROVAL
        # Phase 3-D — 자기 등록
        "register_new_job", "register_new_intent", "create_new_agent",  # APPROVAL
        # Phase 3-E — Claude Code SDK 위임 (옵션 A) — 표기 통일 2026-06-06
        "delegate_to_claude_code",                                      # APPROVAL
        # Phase 3-F — 자비스 자체 강화 (옵션 B)
        "web_fetch", "ask_claude",                                       # SAFE
        # Phase 4 — ARCHITECT (설계타임 메타, JARVIS00_INFRA 위임)
        "design_new_agent",                                             # SAFE
    ],
    requires_approval=[],
    cost_class="low",
    description="순수 마스터 라우터. 사용자 자유 문장 → 인텐트 분류 → 적절 에이전트 디스패치. 인프라 관리는 jarvis00_infra 가 담당.",
    tags=["router", "master", "core"],
    help_section=(
        "🧭 *마스터 라우터 (JARVIS01)*\n"
        "자유 문장을 입력하면 자동으로 적절한 에이전트로 라우팅\n"
        "/agents              등록된 에이전트 목록\n"
        "/route [자유문장]     라우팅 경로 미리보기"
    ),
    status_fn=_status_section,
)


def _job_router_health():
    """매 시간 라우터 헬스 체크 (Phase 1 — 단순 로그)."""
    try:
        from shared import capabilities
        n = len(capabilities.all_capabilities())
        print(f"  🧭 [JARVIS01 헬스] 등록 에이전트 {n}개")
    except Exception as e:
        print(f"  ⚠️ JARVIS01 헬스 체크 실패: {e}")
        _g_report("master", e, module=__name__)


def register(scheduler, bus):
    """데몬 부팅 시 자동 호출. AGENTS.md 규약.

    ★ 잡 등록은 JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 단일 진입점.
    여기서는 라우터 그래프 컴파일·도구 등록만.
    """
    # 1) 라우터 그래프 lazy 컴파일 검증
    try:
        from JARVIS01_MASTER.router import get_graph
        g = get_graph()
        if g is not None:
            print("  ✅ JARVIS01 LangGraph 라우터 컴파일 완료")
        else:
            print("  ⚠️ JARVIS01 LangGraph 미가용 — fallback 키워드 매칭 모드")
    except Exception as e:
        print(f"  ⚠️ JARVIS01 라우터 빌드 실패 (fallback 모드): {e}")
        _g_report("master", e, module=__name__)

    # 3) Phase 2-B A — agent_tools 등록 (5개 도구 _TOOLS 레지스트리에 박힘)
    try:
        from JARVIS01_MASTER import agent_tools  # import 만으로 @register_tool 트리거
        loaded = agent_tools.ensure_loaded()
        print(f"  🔧 JARVIS01 agent_tools 등록: {len(loaded)}개 ({', '.join(loaded)})")
    except Exception as e:
        print(f"  ⚠️ JARVIS01 agent_tools 등록 실패 (라우터 ReAct 비활성): {e}")
        _g_report("master", e, module=__name__)
