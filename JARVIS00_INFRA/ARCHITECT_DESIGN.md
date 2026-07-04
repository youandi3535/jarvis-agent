# JARVIS00_INFRA · ARCHITECT — 에이전트 설계 기획서

**작성일**: 2026-05-09
**대상**: 새 에이전트·도구·잡·skill 신설 시 일관된 설계·검증·계획 산출
**위치**: `JARVIS00_INFRA/architect.py` (신규) — INFRA 책임 확장 (런타임 lifecycle + 설계타임 agent 설계)

---

## 0. 위치 결정 — 왜 JARVIS00_INFRA 인가

| 후보 | 근거 | 결정 |
|------|------|------|
| JARVIS00_INFRA | 사용자 지시. *시스템 메타 관리* 의 자연스러운 묶음 (lifecycle = 런타임 메타 / architect = 설계타임 메타). 모든 다른 에이전트보다 *상위 메타* 책임. | ✅ 채택 |
| JARVIS01_MASTER | LLM 라우터·도구 등록 인프라가 이미 있음. ARCHITECT 도 LLM 추론. | ❌ MASTER 는 *런타임 라우팅* 전용. *설계타임 메타* 와 책임 다름. |
| 신규 JARVIS06_ARCHITECT | 한 폴더 한 책임 원칙. | ❌ 메타 무한루프 위험 (ARCHITECT 가 ARCHITECT v2 설계 등). 단일 파일 충분. |

★ 부수 결정: INFRA 의 책임 정의를 *"데몬 라이프사이클 + 상태"* → *"시스템 메타 관리 (런타임 + 설계타임)"* 로 확장. `CLAUDE_INFRA.md` 동시 갱신.

별도 에이전트 폴더 신설 안 함. ARCHITECT 는 INFRA 의 *기능* 이지 별도 에이전트가 아님.

---

## 1. 핵심 원칙 (사용자 박제)

★ **설계·기획만. 실행은 절대 안 함.** 출력은 *기획서 + 구현 계획서 (마크다운)*. 실제 코드 수정·잡 등록은 기존 `create_plan` 도구 통해서만 → 인라인 버튼 ✅ 거치고만. **자율 설계 + 사용자 실행 결정** 패턴 유지.

→ 도구 분류: `side_effect="none"`, `requires_approval=False`. 파일 쓰기는 *기획서 마크다운 단일 산출물* 만 (`docs/architect/yyyy-mm-dd_<slug>.md`). 코드 변경은 0건.

---

## 2. 5 핵심 책임 → 구현 매핑

| # | 책임 | 구현 |
|---|------|------|
| 1 | 의도 파악 | `_parse_intent(user_text)` — Sonnet 호출 → 정형 스펙 dict (역할·범위·side_effect·스케줄·외부 의존성) |
| 2 | 설계 산출 | `_generate_spec(parsed, ctx)` — Sonnet 호출 (`invoke_text("writer")`) + 표준 양식 강제 prompt → 기획서 마크다운 |
| 3 | 일관성 검증 | `_verify_against_rules(spec)` — CLAUDE.md 검증 명령 5종 시뮬레이션 (스케줄·승인 게이트·하드코딩·인프라·글자수) |
| 4 | 대안 제시 | `_check_alternative(parsed)` — "이건 에이전트 필요 없음 — skill/도구로 충분" 자동 판단 |
| 5 | 계획 위임 | `_emit_plan_steps(spec)` — `create_plan` 인자 형태로 단계 dict 리스트 반환. 사용자 ✅ 후 실행 |

→ 단일 진입점 함수: `design_new_agent(user_intent, scope, output_path) -> dict`.

---

## 3. 5 박제 의무 → 구현 매핑

| # | 의무 | 구현 |
|---|------|------|
| 1 | Knowledge base 동적 로드 | `_load_context()` — 매 호출마다 새로. 캐시 0. CLAUDE.md + ERRORS.md 최근 30 항목 + 모든 `*_agent.py` declare + `agent_tools.py` 도구 목록 + `JARVIS04_SCHEDULER.job_registry.DEFAULT_JOBS` |
| 2 | 출력 표준화 | LLM prompt 안 마크다운 양식 *완전 박제*. 8 섹션 고정 (§7 참조) |
| 3 | Anti-pattern 자동 경고 | `_verify_against_rules` 5종 검사. 기획서 끝 "## 일관성 검증 결과" 섹션에 ✅/⚠️ 표 자동 삽입 |
| 4 | 범위 제한 | `scope` 파라미터: v1 = `"agent"` 만. `"tool"`/`"job"`/`"skill"` 호출 시 `NotImplementedError` |
| 5 | 자기참조 안전망 | ARCHITECT 자체도 본 기획서 양식·검증 통과. `scope="meta"` 로 자기 자신 재설계 시 *재귀 깊이 1* 제한 |

---

## 4. 통합 지점 — JARVIS00_INFRA + JARVIS01_MASTER

### 신규 파일
- `JARVIS00_INFRA/architect.py` — 본체. `design_new_agent`, `_load_context`, `_parse_intent`, `_generate_spec`, `_verify_against_rules`, `_check_alternative`, `_replay_errors_check`, `_emit_plan_steps`. `__all__` 명시.

### 기존 파일 수정 (5곳)
| 파일 | 수정 내용 |
|------|-----------|
| `JARVIS00_INFRA/infra_agent.py` | `register_capability().intents` 에 `architect.design` 추가. `handle_safe_intent("architect.design", params)` 분기 → `architect.design_new_agent()` 위임 |
| `JARVIS00_INFRA/CLAUDE_INFRA.md` | 역할 정의 확장: "데몬 lifecycle + 상태" → "시스템 메타 관리 (런타임 lifecycle + 설계타임 agent 설계)". 비직관 규칙 표에 ARCHITECT 행 추가 |
| `JARVIS01_MASTER/agent_tools.py` | `@register_tool(side_effect="none", requires_approval=False, domain="meta")` 로 `design_new_agent` 래퍼 등록. 본체는 `from JARVIS00_INFRA.architect import design_new_agent as _impl` 호출 위임. `ensure_loaded()` expected set 에 `"design_new_agent"` 추가 |
| `JARVIS01_MASTER/core_agent.py` | `CAPABILITIES.tools` 에 `"design_new_agent"` 추가 |
| `JARVIS01_MASTER/dispatchers.py` | `SAFE_INTENTS` 에 `"architect.design"` 추가. `execute_safe()` 에 분기 추가 → `infra_agent.handle_safe_intent("architect.design", params)` 위임 |
| `JARVIS01_MASTER/intents.py` | `ROUTER_SYSTEM_PROMPT` 에 자유 문장 → `architect.design` 매핑 규칙 추가 (§8 참조) |

→ 새 capability 추가 *3 곳 동시 갱신* 의무 (declare + dispatchers + ROUTER_SYSTEM_PROMPT) 준수. ERRORS [29] 재발 방지.

---

## 5. Knowledge Base 동적 로드 — `_load_context()`

```python
def _load_context() -> dict:
    """매 호출마다 새로 로드. 캐시 금지 — 코드 변경 즉시 반영 의무."""
    root = Path(__file__).resolve().parents[1]  # jarvis-agent/
    return {
        "claude_md": (root / "CLAUDE.md").read_text(encoding="utf-8"),
        "claude_writer": (root / "JARVIS02_WRITER" / "CLAUDE_WRITER.md").read_text(...),
        "claude_radar": (root / "JARVIS03_RADAR" / "CLAUDE_RADAR.md").read_text(...),
        "claude_infra": (root / "JARVIS00_INFRA" / "CLAUDE_INFRA.md").read_text(...),
        "errors_recent": _tail_errors_md(root / "ERRORS.md", n_entries=30),
        "agent_declares": _scan_capability_declares(root),  # *_agent.py grep declare(...) 추출
        "tools_catalog": _list_tools(),  # shared.tools._TOOLS metadata
        "default_jobs": _import_default_jobs(),  # JARVIS04_SCHEDULER.job_registry.DEFAULT_JOBS 요약
        "existing_agents": _list_agent_folders(root),  # JARVIS{NN}_NAME 매핑
        "infra_rules": _extract_infra_invariants(),  # CLAUDE.md 의 단일 진입점 규정 7개 요약
    }
```

★ 박제 — *어떤 글로벌 캐시도 금지*. functools.lru_cache·module-level cache·class attribute cache 0. 매 호출마다 디스크 read. 시스템 진화 즉시 반영.

---

## 6. 도구 시그니처

```python
@register_tool(
    name="design_new_agent",
    domain="meta",
    side_effect="none",
    requires_approval=False,
    rollback="N/A (read-only + 단일 마크다운 산출)",
    cost="LLM 2-3 호출 (Sonnet 의도 파싱 + Sonnet 설계 + Sonnet 검증) — 전체 Sonnet 5 단일 모델",
)
def design_new_agent(
    user_intent: str,           # 사용자 자유 문장
    scope: str = "agent",        # v1: "agent" 만. "tool"|"job"|"skill" → NotImplementedError
    output_path: Optional[str] = None,  # 기획서 저장 경로 — 없으면 docs/architect/{date}_{slug}.md
) -> dict:
    """
    반환:
    {
        "ok": bool,
        "spec_path": str,            # 산출 기획서 절대 경로
        "summary": str,              # 1-3 문장 요약 (텔레그램 송출용)
        "verdict": "agent"|"skill"|"tool"|"job"|"unnecessary",  # 대안 판단
        "warnings": [str],           # anti-pattern 경고 리스트
        "errors_risk": [{"id": "[29]", "risk": "high|med|low", "note": "..."}],
        "next_plan_steps": [dict],   # create_plan 호환 step 리스트
    }
    """
```

---

## 7. 기획서 표준 양식 (LLM prompt 박제)

```markdown
# JARVIS{NN}_<NAME> — 기획서

## 1. 의도 (사용자 원문)
{원문 인용 + 1-2 문장 정리}

## 2. 정형 스펙
- 이름·번호: JARVIS{NN}_<NAME>
- 역할 1줄: ...
- 단일 책임 (단일 진입점): ...
- side_effect 분류: none / internal / external
- 외부 의존성: API·CLI·라이브러리 ...
- 스케줄 필요: yes/no — yes 면 cron/interval 명시
- 데이터 의존: shared/db.py 의 어느 테이블·shared/bus.py 의 어느 이벤트

## 3. 도구 카탈로그 (신설)
| 도구 이름 | side_effect | requires_approval | 시그니처 | 1줄 설명 |
|-----------|-------------|-------------------|----------|----------|

## 4. intent 카탈로그
- SAFE: <agent>.<verb> ...
- APPROVAL: <agent>.<verb> ...

## 5. DEFAULT_JOBS 추가분 (JARVIS04_SCHEDULER 박제)
| 잡 ID | trigger | callback path | misfire | owner |
|-------|---------|---------------|---------|-------|

## 6. dispatchers.py 매핑 (3 곳 동시 갱신)
- SAFE_INTENTS += { ... }
- APPROVAL_INTENTS += { ... }
- _APPROVAL_META 추가: { "<intent>": (title, detail_fn), ... }
- execute_safe / execute_approval 분기 추가

## 7. ROUTER_SYSTEM_PROMPT 추가분
{LLM 라우터에 추가할 자유 문장 → intent 매핑 규칙}

## 8. CLAUDE.md 비직관 규칙 후보
- 단일 진입점 정의: ...
- 검증 명령: ...

## 9. ERRORS.md 헛다리 회피 체크
- [27] 한국어 하드코딩 위험: ...
- [29] 3 곳 동시 갱신: ✅ 본 기획서 §6 명시
- [30] 승인 게이트: ...
- [31] wrapper 시그니처: ...
- [32] subprocess PATH: ...

## 10. 일관성 검증 결과 (자동 삽입)
| 검증 항목 | 결과 |
|-----------|------|
| 스케줄 단일 진입점 | ✅ / ⚠️ <설명> |
| 승인 게이트 (external→approval) | ✅ / ⚠️ |
| 한국어 하드코딩 (발행 본문) | ✅ / ⚠️ |
| 인프라 단일 진입점 | ✅ / ⚠️ |
| 글자수 단일 진입점 | ✅ / ⚠️ |

## 11. 대안 판단
- 이게 정말 에이전트여야 하는가? — 이유 ...
- 더 작게 가능한 옵션: skill / 도구 / 잡 / 라이브러리 모듈 / ...
- 결론: agent / skill / tool / job / unnecessary

## 12. 구현 계획 (create_plan 인자)
1. write_file: <path> — <목적>
2. edit_file: <path>:<함수> — <목적>
3. register_new_intent: <intent>
...
N. run_bash: <검증 명령>
```

→ LLM 이 *모든 12 섹션* 채워야 통과. 빈 섹션 검출 시 재호출.

---

## 8. ROUTER_SYSTEM_PROMPT 추가분 (`intents.py`)

```
자유 문장 → architect.design 매핑:
- 키워드: "에이전트 만들고 싶어", "에이전트 설계", "에이전트 기획", "X 자동화 에이전트",
   "X 하는 봇/에이전트", "...어떻게 만들지", "...설계해줘", "...기획해줘"
- params 추출: {"user_intent": <원문 그대로>, "scope": "agent"}
- 추가 형태: scope 키워드 (도구·잡·skill) 가 명시되면 해당 scope. 미명시 = "agent" 기본.
```

---

## 9. 검증 함수 — `_verify_against_rules(spec_md)` (5종)

기획서 마크다운 산출 후 *자동* 점검. 위반 가능성 발견 시 §10 검증 결과 표에 ⚠️ + 설명 삽입.

| 검증 | 패턴 | 위반 시 경고 |
|------|------|--------------|
| 스케줄 단일 진입점 | spec 안에 `BackgroundScheduler\(`·`add_job\(`·`schedule\.every\(` 등장 | "JARVIS04_SCHEDULER.DEFAULT_JOBS 로 등록 권장" |
| 승인 게이트 | `side_effect=external` 인데 `requires_approval=False` | "외부 영향 도구는 requires_approval=True 필수" |
| 한국어 하드코딩 | spec 안에 5+ 문장 한국어 블록이 *반환값 / 상수* 위치에 있는지 휴리스틱 | "LLM 호출 또는 변형 풀 + 시드 권장 — ERRORS [27]" |
| 인프라 단일 진입점 | spec 가 INFRA 외 폴더에 `build_status`·daemon control 박는 패턴 | "JARVIS00_INFRA 단일 진입점 위배" |
| 3 곳 동시 갱신 | spec 의 §4 intent 추가 vs §6 dispatchers 매핑 누락 | "ERRORS [29] 재발 — dispatchers + ROUTER_PROMPT 동시 갱신 필수" |

★ 검증 결과는 *경고만*, 차단 안 함. 사용자가 기획서 수동 검토 후 판단. 단, `verdict` 필드에 위반 강도 누적해서 *명시 보고*.

---

## 10. ERRORS replay 체크 — `_replay_errors_check(spec_md, errors_md)`

ERRORS [27]~[32] 의 *증상·원인·헛다리* 패턴이 신규 기획에 재현될 위험 점검:

```python
ERRORS_PATTERNS = {
    "[27]": ["고정 한국어 문자열", "tip_box", "_FALLBACK_OUTRO 단일 string", "disclaimer 금지"],
    "[28]": ["events.type", "단일 학습 신호", "조회수 의존만"],
    "[29]": ["intent 추가 + dispatchers 누락", "capability declare 만"],
    "[30]": ["하드코딩 set", "_approval_tool_names 하드코딩", "auto_approve=True 운영"],
    "[31]": ["functools.wraps 만", "__signature__ 누락"],
    "[32]": ["subprocess.run 환경 변수 미명시", "PATH 누락"],
}
```

→ 기획서에서 위 키워드 부재 + 관련 영역 (예: 발행 본문 / 외부 도구 / subprocess) 등장 시 ⚠️.

---

## 11. 자기 자신 설계 테스트 (첫 검증)

구현 직후 첫 호출:
```
design_new_agent(
    user_intent="에이전트 설계 기획 에이전트를 자비스00 인프라에 만들고 싶어",
    scope="agent",
)
```

기대: 산출 기획서가 *본 문서와 동등 수준* (12 섹션 모두 채워짐 + 위치 결정 = JARVIS00_INFRA + 검증 5종 통과). 미달 시 prompt 보강 (§7 양식·§8 LLM prompt 표현).

---

## 12. 점진 확장 로드맵

- **v1 (현재)**: `scope="agent"` — 새 에이전트 폴더 신설 기획. 본 문서.
- **v2**: `scope="tool"` — 단일 도구 추가 기획 (agent_tools.py 의 `@register_tool`).
- **v3**: `scope="job"` — 스케줄 잡 추가 기획 (`DEFAULT_JOBS` 항목).
- **v4**: `scope="skill"` — JARVIS01 라우터 슬래시 명령 / Claude Code skill 추가 기획.
- **v5**: `scope="rule"` — CLAUDE.md 신규 강제 규정 추가 기획 (검증 명령 포함).

각 단계마다 §7 양식 + §9 검증 5종 + §10 ERRORS replay 적용. 양식은 scope 별 약간 변형.

---

## 13. 구현 단계 (create_plan 인자)

```python
plan_steps = [
    {
        "tool": "write_file",
        "args": {
            "path": "JARVIS00_INFRA/architect.py",
            "content": "<본체 — _load_context, design_new_agent, _verify_*, _check_alternative, _replay_errors_check, _emit_plan_steps, 표준 양식 prompt 상수>",
        },
        "purpose": "ARCHITECT 본체 신설",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS00_INFRA/infra_agent.py",
            "edits": "register_capability().intents += ['architect.design']; handle_safe_intent 에 'architect.design' 분기 → architect.design_new_agent 위임",
        },
        "purpose": "INFRA capability 에 ARCHITECT 등록",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS00_INFRA/CLAUDE_INFRA.md",
            "edits": "역할 정의 확장 (런타임 + 설계타임). 비직관 규칙 표에 ARCHITECT 행 추가",
        },
        "purpose": "INFRA 책임 문서화",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS01_MASTER/agent_tools.py",
            "edits": "@register_tool design_new_agent 래퍼 추가 (side_effect=none, requires_approval=False); ensure_loaded() expected set 갱신",
        },
        "purpose": "JARVIS01 도구 카탈로그 등록",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS01_MASTER/core_agent.py",
            "edits": "CAPABILITIES.tools 에 'design_new_agent' 추가",
        },
        "purpose": "JARVIS01 CAPABILITIES 갱신",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS01_MASTER/dispatchers.py",
            "edits": "SAFE_INTENTS += {'architect.design'}; execute_safe 에 분기 추가 → infra_agent.handle_safe_intent 위임",
        },
        "purpose": "fallback 라우팅 통합 (ERRORS [29] 재발 방지)",
    },
    {
        "tool": "edit_file",
        "args": {
            "path": "JARVIS01_MASTER/intents.py",
            "edits": "ROUTER_SYSTEM_PROMPT 에 architect.design 매핑 규칙 추가 (§8)",
        },
        "purpose": "자유 문장 → architect.design 라우팅",
    },
    {
        "tool": "write_file",
        "args": {
            "path": "docs/architect/README.md",
            "content": "사용자 가이드 — 자유 문장 예시 + 산출물 위치 + create_plan 위임 흐름",
        },
        "purpose": "사용자 문서화",
    },
    {
        "tool": "run_bash",
        "args": {
            "command": "python -c 'from JARVIS01_MASTER.agent_tools import ensure_loaded; ensure_loaded(); from shared.tools import _TOOLS; assert \"design_new_agent\" in _TOOLS, _TOOLS.keys()'",
        },
        "purpose": "도구 등록 검증",
    },
    {
        "tool": "run_bash",
        "args": {
            "command": "grep -n 'architect.design' JARVIS01_MASTER/dispatchers.py JARVIS01_MASTER/intents.py JARVIS00_INFRA/infra_agent.py",
        },
        "purpose": "3 곳 동시 갱신 검증 (ERRORS [29])",
    },
    {
        "tool": "run_bash",
        "args": {
            "command": "python -c 'from JARVIS00_INFRA.architect import design_new_agent; r = design_new_agent(\"에이전트 설계 기획 에이전트\", scope=\"agent\"); print(r[\"ok\"], r[\"spec_path\"], r[\"verdict\"])'",
        },
        "purpose": "★ 자기 자신 설계 테스트 (§11)",
    },
]
```

→ 사용자 ✅ 승인 후 `create_plan(goal="ARCHITECT 신설", steps=plan_steps)` 호출. 단계마다 진행 표시 의무 (`_execute_plan` 의무).

---

## 14. CLAUDE.md 추가 규정 후보

본 ARCHITECT 도입 후 CLAUDE.md 에 신규 강제 규정 1개 추가 권장:

> ## 새 에이전트·도구·잡·skill 신설 규정 (강제 — 절대)
>
> - **★ 단일 진입점**: 모든 신설 작업 (에이전트·도구·잡·skill·CLAUDE.md 규정) 은 *반드시* `design_new_agent` 도구 호출 후 산출 기획서 검토 → `create_plan` 위임 흐름 통과. 즉흥 신설 금지.
> - **신설 헛다리 회피**: ERRORS [29] (3 곳 동시 갱신 누락), [30] (승인 게이트 우회) 박제. 기획서 §6 dispatchers 매핑 + §10 검증 결과 표 모두 통과 후 구현.
> - **검증 명령**: `grep -nE 'architect\.design|design_new_agent' JARVIS00_INFRA JARVIS01_MASTER` — 3 곳 모두 매칭되어야 함.

---

## 15. 위험·우려 (evenhandedness)

- **메타 무한루프**: ARCHITECT 가 ARCHITECT v2 설계. → `scope="meta"` 호출 시 재귀 깊이 1 제한 박제.
- **LLM 호출 비용**: 호출당 Sonnet 5 (`writer` alias) 3회 (의도 파싱 + 설계 + 검증). 사용자 자유 문장 라우팅에서 너무 자주 호출되지 않도록 ROUTER_SYSTEM_PROMPT 에 *명시적 키워드* 매핑 (§8) — 모호한 문장은 매칭 안 함.
- **양식 변경 비용**: §7 12 섹션 양식이 미래에 부적합해질 수 있음. → 양식은 `architect.py` 의 `SPEC_TEMPLATE` 상수로 분리. 단일 수정으로 전체 반영.
- **자기 검증의 한계**: ARCHITECT 가 자기 자신을 검증하면 같은 사각지대 공유. → `_verify_against_rules` 는 *외부 grep 검증 명령* 시뮬레이션 (CLAUDE.md 박제 명령). LLM 자체 판단 의존 최소화.

---

## 16. 다음 단계 — 사용자 결정

1. ★ **본 기획서 승인 여부** — ✅ 면 §13 의 `create_plan` 호출. ❌ 면 어디 수정 필요한지 알려주기.
2. 첫 산출 시연: 자기 자신 설계 테스트 (§11). 본 문서와 동등 수준이면 통과 → `create_plan` 위임 패턴 확정.
3. v2~v5 확장 (§12) 은 v1 안정화 후.

★ 본 기획서 자체도 ARCHITECT v0 의 산출물 — *수동* 작성. v1 구현 후 자기 자신 재설계로 갱신.
