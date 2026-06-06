# 004. 텔레그램 승인 게이트 — 외부 영향 도구의 단일 차단점

## 상태
확정 (2026-04 박제, 사용자 직접 박제 — 영구)

## 배경
JARVIS 가 자율 멀티 에이전트로 진화하면서 *외부 영향* (블로그 발행·이메일·결제·파일 수정·셸 실행) 을 일으키는 도구가 누적됐다. 자율 에이전트가 사용자 미인지 상태에서 외부 영향을 일으키면:
- 잘못된 글 발행 (네이버·티스토리 동시) → 회수 불가 + 검색 노출 + 신뢰 손상
- 외부 API 호출 (결제·메일) → 과금 + 복구 불가
- 파일·셸 변경 → 데이터 손실

*자율성의 가장 큰 위협은 사용자가 모르는 사이의 외부 행동*. 이를 통제할 *물리적 차단점* 이 필요했다.

## 결정
모든 *외부 영향* (`side_effect="external"`) 도구는 *언제 어디서 누가 호출하든* 텔레그램 인라인 버튼 ✅/❌ 통과 후만 실행. *어떤 길로도 우회 0*.

집행 메커니즘:
- `shared/tools.py` 의 `@register_tool` 데코레이터에 `side_effect` (none/internal/external) 필수
- `side_effect="external"` 이면 `requires_approval=True` *필수* (검증 명령으로 누락 차단)
- 자율 에이전트 (JARVIS01 라우터·ReAct) 가 APPROVAL 도구 호출 시:
  1. `react_handle()` 의 `pending_approvals` 로만 노출
  2. `_PENDING_J00_REACT` 에 보관
  3. 텔레그램 인라인 버튼 (`j00r_yes` / `j00r_no`) 송출
  4. 콜백 `_execute_j00_react_approval()` 에서 `tool_invoke()` 실행
- `auto_approve=True` 는 *테스트 전용* — 운영 코드 박제 금지

자비스의 자율 판단은 *어떤 도구 쓸지·어떤 계획 만들지* 만. *실행 여부* 는 *항상 사용자*.

## 이유
1. **신뢰의 근본**: 사용자가 *볼 수 없는* 경로로 외부 영향이 발생하면 *자율성 통제 상실*. 통제 불가능한 자율성은 사용 불가능.
2. **단일 차단점**: 텔레그램 ✅/❌ 가 *유일한 게이트* → 게이트 통과 = 사용자 인지. 우회 경로 0 = 통제 완전.
3. **계획 우선 패턴**: 큰 작업은 `create_plan(goal, steps)` 으로 묶어 *한 번에* 사용자 승인. ReAct 가 *직접* write_file 호출 금지 → 사용자가 모든 단계 가시화 후 일괄 ✅.
4. **진행 표시 의무 (사용자 박제)**: 게이트 통과 후 *실행 중* 도 텔레그램에 진행 상황 표시 (`_run_tool_with_heartbeat` 60초마다 ⏳ N분 경과). 사용자가 *언제든* 현재 상황 파악 가능.

## 포기한 대안
1. **사후 알림** (실행 후 텔레그램 보고): 잘못된 발행은 회수 불가. 사용자가 *멈출 기회* 없음. 포기.
2. **role-based auto-approve** (자주 쓰는 도구는 자동 승인): "자주 쓴다" 의 기준이 모호 + 한 번의 사고로 신뢰 붕괴. 포기.
3. **타임아웃 자동 승인** (N분 응답 없으면 자동 진행): 사용자 부재 시 *침묵 = 동의* 안 됨. 부재 = 진행 보류 가 안전. 포기.
4. **자율 평가 후 자가 승인** (LLM 이 위험도 평가 후 결정): LLM 의 자기 평가는 *원리적으로 불완전*. 외부 영향 통제를 LLM 에 위임할 수 없음. 포기.

## 결과
- `shared/tools.py` `@register_tool(side_effect=..., requires_approval=...)` 강제.
- `CLAUDE.md` 의 "자율 에이전트 도구·승인 게이트 규정" 및 "자율 코드 자가수정 규정" 섹션.
- `shared/precommit_check.py` `tools` 카테고리 — `external + requires_approval=False` 동시 검출 (3-line window).
- `JARVIS01_MASTER/router.py` REACT_SYSTEM_PROMPT — 큰 작업은 계획 우선 패턴 명시.
- `jarvis_daemon.py` `_run_tool_with_heartbeat` + `_execute_plan` — 진행 표시 의무 단일 진입점.
- ERRORS [29] — 새 capability/intent 추가 시 SAFE_INTENTS / APPROVAL_INTENTS 분류 누락 사고.

## 절대 박제 (사용자 직접)
> *외부 영향 도구·계획·위임은 언제 어디서 누가 호출하든 텔레그램 인라인 버튼이 반드시 송출되어야 함. 그 후 사용자 ✅ 통과 후에만 실행. 어떤 길로도 우회 0.*

이 규정은 변경·예외·완화 *불가능*. 검증 명령으로 *영구 회귀 차단*.
