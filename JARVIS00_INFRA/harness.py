"""JARVIS00_INFRA/harness.py — 검증 순환 → 송출 표준 인프라 (★ ADR 009 v2 사용자 박제 2026-05-17).

★ 불변 원칙 (CLAUDE.md 헌법):
1. 송출 = 완료 표시. 외부 도달까지 *포함*된 단일 종착 상태.
2. 결함 있는 결과물은 *영원히 송출되지 않는다*. 검증 순환 안에서만 수정.
3. 송출 후 "실패"라는 개념은 존재하지 않는다. 외부 응답 실패 = 송출 미완료 = 검증 순환 재진입.
4. 모든 명령·트리거·동작에 동일 적용 (블로그·영상·텔레그램·자유 문장·API — 트리거 무관).

★ 단일 진입점 (CLAUDE.md 헌법): Layer 1~4 코드는 이 파일 단독 관리. 다른 위치 박지 말 것.
   외부 영향 행위 (발행·전송·파일 적용 등) 는 *반드시* Layer 4 `send` 콜백 통과.

★ 즉시 수정 → 기록 → 누적 → 순환 (전체 에이전트 디폴트 — 사용자 박제 2026-05-18):
   ActionDefinition.fix 훅을 등록하면 검증 실패 시 자동으로:
     ① 수정 가능 항목 inline 패치 (state 직접 수정)
     ② GUARDIAN 학습 박제 (report_manual_fix + record_pattern_hit)
     ③ fingerprint abort — 수정 불가 항목이 이전 시도와 동일하면 즉시 차단
     ④ 재생성 (수정 완료·불가 모두 재생성 트리거 — "고쳤더라도 더 나은 결과 위해 재시도")
   fix 훅 미등록 시 → 기존 GUARDIAN 보고만 → 재생성 (backward-compat 완전 보장).

★ 누수 방지 설계:
   - 표준 라이브러리만 사용 — 외부 의존 0.
   - GUARDIAN 연동 try/except 격리 (학습 자산화 실패해도 검증 순환 지속).
   - `max_attempts` 박제로 무한 루프 방지.
   - max 도달 시 → escalation + 사용자 텔레그램 + *송출 절대 안 함*.
   - send 실패 = 송출 미완료 → 검증 순환 재진입 (송출 후 실패 개념 없음).

사용:
    from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue

    @action_step(name="① 데이터 수집")
    def collect_data(state):
        return {"data": [...]}

    @action_step(name="② 글 작성")
    def write_content(state):
        return {"text": "..."}

    def verify_blog(state):
        # ★ 순수 검증만 — 수정 로직 박지 말 것. 수정은 fix 훅이 담당.
        issues = []
        if len(state.get("text", "")) < 1000:
            issues.append(Issue(step="② 글 작성", kind="length", detail="1000자 미달"))
        return issues

    def fix_blog(state, issues):
        # ★ 즉시 수정 훅 — fix(state, issues) → (fixed_issues, unfixed_issues)
        # state를 직접 수정 후 fixed/unfixed 분리 반환.
        fixed, unfixed = [], []
        for iss in issues:
            if iss.kind == "length" and _try_pad(state):
                fixed.append(iss)
            else:
                unfixed.append(iss)
        return fixed, unfixed

    def send_blog(state):
        # 외부 도달까지 포함. 실패 시 raise → 검증 순환 재진입.
        publish_to_wp(state)

    ACTION = ActionDefinition(
        name="블로그 발행",
        steps=[collect_data, write_content],
        verify=verify_blog,
        fix=fix_blog,          # ★ 선택 — 등록 시 "수정→기록→누적→순환" 자동 활성화
        send=send_blog,
    )

    result = run_action(ACTION, input_data={"theme": "환율"})
    # result.delivered == True  → 송출 완료
    # result.delivered == False → escalation (송출 절대 안 됨)
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger("jarvis")


# ── 상수 ─────────────────────────────────────────────

DEFAULT_MAX_ATTEMPTS = 5
"""검증 순환 무한 루프 방지 — 기본 5회. 동작별 ActionDefinition.max_attempts 로 재정의 가능."""


# ── ★ P1-⑤ 패치 (사용자 박제 2026-05-18 — ADR 009 v2 동시성 보호) ──────────
# 동일 ActionDefinition.name 동시 실행 차단. cron + 텔레그램 + 자유 문장이
# 같은 동작을 동시에 발동하면 *두 번째 호출은 즉시 escalation* (대기 안 함 — 비블로킹).
# state dict 공유로 인한 중복 외부 발행·race condition 방지.
_ACTION_LOCKS: dict[str, threading.Lock] = {}
_ACTION_LOCKS_GUARD = threading.Lock()


def _acquire_action_lock(name: str) -> Optional[threading.Lock]:
    """비블로킹 락 획득. 이미 잡혀 있으면 None 반환 → 호출자가 escalation 처리."""
    with _ACTION_LOCKS_GUARD:
        lk = _ACTION_LOCKS.setdefault(name, threading.Lock())
    if lk.acquire(blocking=False):
        return lk
    return None


# ── 데이터 클래스 ──────────────────────────────────────

@dataclass
class Issue:
    """검증 실패 항목 — 어느 단계의 어떤 문제인지."""
    step: str           # 문제 발생한 step 이름 (또는 "전체")
    kind: str           # 문제 종류 (예: "length", "draft_quality", "login_invalid", "draft_fixed")
    detail: str = ""    # 상세 설명

    def to_context(self) -> dict:
        return {"step": self.step, "kind": self.kind, "detail": self.detail[:300]}


@dataclass
class ActionStep:
    """수행 단계 — 이름 + 실행 함수.

    fn(state: dict) -> dict — state 의 부분 갱신을 반환하거나 state 자체를 반환.
    엔진이 자동으로 merge.
    """
    name: str
    fn: Callable[[dict], dict]

    def __call__(self, state: dict) -> dict:
        result = self.fn(state)
        if isinstance(result, dict):
            merged = dict(state)
            merged.update(result)
            return merged
        return state


@dataclass
class ActionDefinition:
    """동작 정의 — 단계 시퀀스 + 검증 + 즉시수정 + 송출 콜백.

    Required:
        name:        동작 식별자 (로그·박제용)
        steps:       수행 단계 시퀀스 (Layer 2)
        verify:      결과 검증 함수 — list[Issue] 반환. 빈 리스트면 통과.
                     ★ 순수 검증만 — 수정 로직 박지 말 것. 수정은 fix 훅이 담당.
        send:        송출 콜백 — 외부 도달까지 *포함*. 실패 시 raise → 순환 재진입.

    Optional:
        precondition: Layer 1 — 입력·전제조건 검증. list[Issue] 반환.
        fix:          ★ 즉시 수정 훅 — "수정→기록→누적→순환" 전체 에이전트 디폴트.
                      fix(state, issues) → (fixed_issues, unfixed_issues)
                      - fixed_issues  : 즉시 패치 완료 (재생성 트리거 O, fingerprint 제외)
                      - unfixed_issues: 패치 불가   (재생성 트리거 O, fingerprint 포함)
                      등록하지 않으면 기존 GUARDIAN 보고만 → backward-compat 완전 보장.
        max_attempts: 검증 순환 한계 (기본 DEFAULT_MAX_ATTEMPTS=5)
    """
    name: str
    steps: list[ActionStep]
    verify: Callable[[dict], list[Issue]]
    send: Callable[[dict], None]
    precondition: Optional[Callable[[dict], list[Issue]]] = None
    fix: Optional[Callable[[dict, list[Issue]], tuple[list[Issue], list[Issue]]]] = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


@dataclass
class ActionResult:
    """동작 실행 결과."""
    delivered: bool                         # 송출 완료 여부 (★ 비전 핵심)
    attempts: int = 0                       # 검증 순환 시도 횟수
    final_state: dict = field(default_factory=dict)
    issues_history: list[list[Issue]] = field(default_factory=list)
    escalation_reason: str = ""             # 송출 안 된 사유 (delivered=False 일 때만)

    @property
    def state(self) -> dict:
        """final_state 별칭 — 호출자 편의."""
        return self.final_state


# ── 데코레이터 ─────────────────────────────────────────

def action_step(name: str) -> Callable:
    """수행 단계 데코레이터 — 함수를 ActionStep 으로 래핑.

    사용:
        @action_step(name="① 데이터 수집")
        def collect_data(state):
            return {"data": [...]}
    """
    def decorator(fn: Callable[[dict], dict]) -> ActionStep:
        return ActionStep(name=name, fn=fn)
    return decorator


# ── GUARDIAN 연동 ─────────────────────────────────────

def _report_issues_to_guardian(action_name: str, attempt: int, issues: list[Issue]) -> None:
    """검증 실패 → error_collector.report() 박제. learned_patterns 자동 등록.

    실패해도 (예: GUARDIAN 미가용) 검증 순환은 계속 진행 — try/except 격리.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import report as g_report
    except Exception:
        _log.warning("[harness] GUARDIAN import 실패 — 학습 자산화 생략 (검증 순환은 계속)")
        return

    for issue in issues:
        try:
            exc = RuntimeError(
                f"[harness:{action_name}] attempt={attempt} step={issue.step}: {issue.detail}"
            )
            g_report(
                source="harness",
                exc=exc,
                module=f"JARVIS00_INFRA.harness.{action_name}",
                func_name=issue.step,
                context={
                    "layer": 3,
                    "action": action_name,
                    "attempt": attempt,
                    "step": issue.step,
                    "kind": issue.kind,
                    "detail": issue.detail,
                },
            )
        except Exception as e:
            _log.warning(f"[harness] GUARDIAN report 실패 (계속 진행): {e}")


def _record_fixed_to_guardian(action_name: str, attempt: int, fixed_issues: list[Issue]) -> None:
    """즉시 수정 완료 항목 → GUARDIAN 학습 박제 (2단: report_manual_fix + record_pattern_hit).

    ★ 전체 에이전트 디폴트 — fix 훅 등록 시 자동 호출. 실패해도 검증 순환 지속.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import report_manual_fix
        from JARVIS07_GUARDIAN.pattern_fixer import record_pattern_hit
    except Exception as e:
        _log.warning(f"[harness] GUARDIAN 학습 import 실패 (무시): {e}")
        return

    for iss in fixed_issues:
        # ① 수정 이력 박제
        try:
            report_manual_fix(
                source=f"harness/{action_name}",
                fixed_file="JARVIS00_INFRA/harness.py",
                description=(
                    f"[Layer3 즉시수정] attempt={attempt} [{iss.step}] {iss.detail[:120]}\n"
                    f"harness.fix 훅 inline 패치 완료."
                ),
                error_type="HarnessIssueFixed",
                severity="low",
                actor="harness_auto_fix",
            )
        except Exception as e:
            _log.warning(f"[harness] report_manual_fix 실패 (무시): {e}")

        # ② learned_patterns 자가 학습 등록
        try:
            _err_rec = {
                "error_type": "HarnessIssueFixed",
                "module": f"JARVIS00_INFRA.harness.{action_name}",
                "message": iss.detail,
                "source": f"harness/{action_name}",
            }
            record_pattern_hit(
                _err_rec,
                fixer_name=f"harness_fix_{action_name}",
                fixed_file="JARVIS00_INFRA/harness.py",
                source="harness_auto_fix",
            )
        except Exception as e:
            _log.warning(f"[harness] record_pattern_hit 실패 (무시): {e}")


def _notify_escalation(action_name: str, attempts: int, last_issues: list[Issue],
                       reason: str = "") -> None:
    """max_attempts 도달 또는 precondition 실패 — 사용자 텔레그램 escalation.

    송출은 *절대 안 함*. 사용자가 수동 검토해야 함.
    """
    msg = (
        f"🚨 *하네스 검증 순환 한계 — 송출 차단*\n\n"
        f"동작: `{action_name}`\n"
    )
    if reason:
        msg += f"사유: {reason}\n"
    msg += f"시도: {attempts}회 모두 검증 실패\n\n"
    if last_issues:
        msg += "❌ *마지막 시도 issues*:\n"
        for issue in last_issues[:10]:
            msg += f"  • `{issue.step}` — {issue.kind}: {issue.detail[:80]}\n"
    msg += "\n*송출은 차단됨*. 호스트에서 수동 검토 필요."

    # 1순위: shared.notify
    try:
        from shared.notify import send_tg  # type: ignore
        send_tg(msg)
        return
    except Exception:
        pass

    # 2순위: 로깅만
    _log.error(f"[harness] escalation: {msg}")


# ── 실행 엔진 ─────────────────────────────────────────

def _find_resume_step(action_def: ActionDefinition, last_issues: list[Issue]) -> Optional[str]:
    """이전 시도의 issues 에서 *재실행 시작 step* 식별.

    문제 step 들 중 *action_def.steps 순서에서 가장 앞* 인 step 부터 재실행.
    """
    if not last_issues:
        return None
    problem_step_names = {
        iss.step for iss in last_issues
        if iss.step not in ("전체", "verify", "송출 (Layer 4)")
    }
    if not problem_step_names:
        return None
    for step in action_def.steps:
        if step.name in problem_step_names:
            return step.name
    return None


def _execute_steps(action_def: ActionDefinition, state: dict,
                   from_step_name: Optional[str] = None) -> dict:
    """Layer 2 — 수행 단계 시퀀스 실행.

    from_step_name 가 주어지면 그 step 부터 *재실행*. 이전 step 의 결과는 state 에 유지.
    step 실행 자체가 폭발 시 state["__step_error__"] = Issue 박고 즉시 반환.
    """
    start_idx = 0
    if from_step_name:
        for i, step in enumerate(action_def.steps):
            if step.name == from_step_name:
                start_idx = i
                break

    for step in action_def.steps[start_idx:]:
        try:
            state = step(state)
        except Exception as e:
            _log.error(f"[harness] Layer 2 step '{step.name}' 폭발: {type(e).__name__}: {e}")
            state = dict(state)
            state["__step_error__"] = Issue(
                step=step.name, kind="execution_error",
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )
            break

    return state


def run_action(action_def: ActionDefinition, input_data: Optional[dict] = None) -> ActionResult:
    """동작 실행 — Layer 1~4 통합. 검증 순환 → 송출.

    흐름:
        Layer 1: precondition 검증 (있으면)
        ↓ 통과
        Layer 2: 수행 단계 실행 → 1차 결과
        Layer 3: 결과 검증 → (fix 훅 있으면) 즉시수정+GUARDIAN학습+fingerprint →
                 문제 있으면 재실행 (max_attempts 까지)
        Layer 4: 검증 통과 시 송출 콜백 호출 (외부 도달까지 포함)

    ★ fix 훅 등록 시 Layer 3에서 자동으로:
        ① 수정 가능 항목 inline 패치 (state 직접 수정)
        ② GUARDIAN 학습 박제 (2단: report_manual_fix + record_pattern_hit)
        ③ fingerprint abort — 수정 불가 항목이 이전 시도와 동일하면 즉시 차단
        ④ fixed + unfixed 모두 재생성 트리거 (최대 max_attempts 회)

    송출 실패 시: 검증 순환 재진입 (송출 미완료 = 송출 안 됨)
    max 도달 시: escalation + 송출 절대 안 함

    Args:
        action_def: 동작 정의
        input_data: 초기 state (선택)

    Returns:
        ActionResult — delivered=True 면 송출 완료. False 면 escalation.
    """
    state: dict = dict(input_data or {})
    result = ActionResult(delivered=False, final_state=state)

    # ── ★ P1-⑤ 동시성 락 (비블로킹) ──
    _lock = _acquire_action_lock(action_def.name)
    if _lock is None:
        # 이미 같은 동작 실행 중 — 즉시 escalation (대기 안 함)
        reason = f"동시 실행 중복 차단 — '{action_def.name}' 이미 다른 호출에서 실행 중"
        _log.warning(f"[harness] 🚫 동시성 차단: {reason}")
        dup_issue = Issue(step="전체", kind="concurrent_duplicate", detail=reason)
        result.issues_history.append([dup_issue])
        result.escalation_reason = reason
        try:
            _report_issues_to_guardian(action_def.name, 0, [dup_issue])
        except Exception:
            pass
        _notify_escalation(action_def.name, 0, [dup_issue], reason=reason)
        return result

    try:
        _log.info(f"[harness] ▶️ 동작 시작: {action_def.name}")
        return _run_action_locked(action_def, state, result)
    finally:
        _lock.release()


def _run_action_locked(action_def: ActionDefinition, state: dict,
                       result: ActionResult) -> ActionResult:
    """run_action 본체 — 락 보유 상태에서만 호출. _ACTION_LOCKS 외부에서 직접 호출 금지."""

    # ── Layer 1: precondition (선택) ──
    if action_def.precondition is not None:
        try:
            pre_issues = action_def.precondition(state) or []
        except Exception as e:
            _log.error(f"[harness] Layer 1 precondition 폭발: {type(e).__name__}: {e}")
            pre_issues = [Issue(
                step="precondition (Layer 1)", kind="precondition_error",
                detail=f"{type(e).__name__}: {str(e)[:200]}",
            )]

        if pre_issues:
            _log.warning(f"[harness] Layer 1 precondition 실패: {len(pre_issues)} issues")
            _report_issues_to_guardian(action_def.name, 0, pre_issues)
            result.issues_history.append(pre_issues)
            result.escalation_reason = "Layer 1 precondition 실패"
            _notify_escalation(action_def.name, 0, pre_issues, reason="precondition")
            return result

    # ── Layer 2 + 3: 수행 + 검증 순환 ──
    for attempt in range(1, action_def.max_attempts + 1):
        result.attempts = attempt

        # 재시도는 *문제 step 부터* (이전 시도의 issues 에서 식별)
        from_step = None
        if result.issues_history:
            from_step = _find_resume_step(action_def, result.issues_history[-1])

        # Layer 2: 수행 단계 실행
        state = _execute_steps(action_def, state, from_step_name=from_step)
        result.final_state = state

        # Layer 2 자체 실패 → 즉시 issue 로 박제
        if "__step_error__" in state:
            step_err = state.pop("__step_error__")
            issues = [step_err]
        else:
            # Layer 3: 결과 전체 검증 (★ 순수 검증만 — 수정은 fix 훅이 담당)
            try:
                issues = action_def.verify(state) or []
                if not isinstance(issues, list):
                    _log.warning("[harness] verify 반환값 비정상 (list 아님) — 빈 리스트로 처리")
                    issues = []
            except Exception as e:
                _log.error(f"[harness] verify 폭발: {type(e).__name__}: {e}")
                issues = [Issue(
                    step="verify (Layer 3)", kind="verify_error",
                    detail=f"{type(e).__name__}: {str(e)[:200]}",
                )]

        # ── 검증 통과 → Layer 4 송출 ──
        if not issues:
            _log.info(
                f"[harness] ✅ 검증 통과 (시도 {attempt}/{action_def.max_attempts}) — "
                f"송출 진행: {action_def.name}"
            )
            try:
                action_def.send(state)
                result.delivered = True
                result.issues_history.append([])   # 통과 기록
                _log.info(f"[harness] 📤 송출 완료: {action_def.name}")
                return result
            except Exception as e:
                # 송출 실패 = 송출 미완료 — 검증 순환 재진입
                _log.warning(
                    f"[harness] Layer 4 송출 실패 (시도 {attempt}) — 검증 순환 재진입: "
                    f"{type(e).__name__}: {e}"
                )
                send_issue = Issue(
                    step="송출 (Layer 4)", kind="send_failure",
                    detail=f"{type(e).__name__}: {str(e)[:200]}",
                )
                result.issues_history.append([send_issue])
                _report_issues_to_guardian(action_def.name, attempt, [send_issue])
                continue

        # ── ★ 즉시 수정 훅 — "수정→기록→누적→순환" 전체 에이전트 디폴트 ──
        fixed_issues: list[Issue] = []
        unfixed_issues: list[Issue] = list(issues)

        if action_def.fix is not None:
            try:
                fixed_issues, unfixed_issues = action_def.fix(state, issues)
                result.final_state = state   # fix가 state를 in-place 수정했을 수 있음

                if fixed_issues:
                    print(
                        f"  🔧 [harness] 즉시 수정 {len(fixed_issues)}건 완료 "
                        f"/ 재생성 필요 {len(unfixed_issues)}건 → GUARDIAN 학습 등록"
                    )
                    _log.info(
                        f"[harness] 🔧 즉시 수정 {len(fixed_issues)}건 — GUARDIAN 학습: "
                        f"{action_def.name} (attempt={attempt})"
                    )
                    # ② GUARDIAN 학습 박제 (2단: report_manual_fix + record_pattern_hit)
                    _record_fixed_to_guardian(action_def.name, attempt, fixed_issues)

                # ★ unfixed=0 — 재생성 없이 즉시 재검증 후 통과 시 바로 발행
                if fixed_issues and not unfixed_issues:
                    try:
                        _rev = action_def.verify(state) or []
                    except Exception:
                        _rev = []
                    if not _rev:
                        _log.info(f"[harness] ✅ 즉시수정 후 재검증 통과 — 재생성 없이 송출: {action_def.name}")
                        print(f"  ✅ [harness] 즉시수정 후 검증 통과 — 재생성 건너뜀")
                        try:
                            action_def.send(state)
                            result.delivered = True
                            result.issues_history.append([])
                            return result
                        except Exception as _se:
                            send_issue = Issue(step="송출 (Layer 4)", kind="send_failure",
                                               detail=f"{type(_se).__name__}: {str(_se)[:200]}")
                            result.issues_history.append([send_issue])
                            _report_issues_to_guardian(action_def.name, attempt, [send_issue])
                            # 송출 실패 → 정상 재시도 루프로 fall-through

            except Exception as _fe:
                _log.warning(f"[harness] fix 콜백 실패 (무시, 전체 unfixed 처리): {_fe}")
                unfixed_issues = list(issues)

        # ── ★ fingerprint abort — 수정 불가 항목 기준 + 통합 누적 추적 ──
        # ★ P2-⑧ 패치 (사용자 박제 2026-05-18):
        #   ① unfixed fingerprint 반복 → 즉시 abort (기존 로직 유지)
        #   ② 누적 issue 카운터 — *fixed+unfixed 합산* 이 max_attempts*3 초과 시 abort
        #      (fix 가 새 종류 issue 만들어 fingerprint 변동만 시키는 위장 회피 차단)
        # 수정 완료(draft_fixed) 재발은 unfixed abort 대상 아님 (패치 후 재생성 정상 흐름)
        _curr_fp = frozenset(
            (iss.step, iss.kind, iss.detail[:80])
            for iss in unfixed_issues
        )
        _prev_fp = state.get("__harness_fp__")
        state["__harness_fp__"] = _curr_fp

        # P2-⑧ — 통합 누적 카운터 (fixed+unfixed 합산)
        _cumulative = state.get("__harness_total_issues__", 0)
        _cumulative += len(fixed_issues) + len(unfixed_issues)
        state["__harness_total_issues__"] = _cumulative
        _cum_threshold = max(action_def.max_attempts * 3, 15)

        if _prev_fp is not None and _curr_fp and _curr_fp == _prev_fp:
            _abort = Issue(
                step="전체", kind="abort",
                detail=(
                    f"수정 불가 {len(unfixed_issues)}건 패턴 반복 — "
                    f"재생성해도 동일 결과 예상 (attempt={attempt})"
                ),
            )
            all_issues = fixed_issues + unfixed_issues + [_abort]
            result.issues_history.append(all_issues)
            result.escalation_reason = "수정 불가 항목 fingerprint 반복 — abort"
            print(
                f"  🚫 [harness] fingerprint abort — 수정 불가 {len(unfixed_issues)}건 반복: "
                f"{action_def.name}"
            )
            _log.warning(f"[harness] 🚫 fingerprint abort: {action_def.name}")
            _report_issues_to_guardian(action_def.name, attempt, [_abort])
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
            )
            return result

        # P2-⑧ — 누적 issue 가 임계치 초과 시 abort (fingerprint 변동만 시키는 위장 회피 차단)
        if _cumulative > _cum_threshold:
            _abort = Issue(
                step="전체", kind="abort",
                detail=(
                    f"누적 issue {_cumulative}건 ≥ 임계 {_cum_threshold} — "
                    f"fingerprint 변동만 반복 의심, abort (attempt={attempt})"
                ),
            )
            all_issues = fixed_issues + unfixed_issues + [_abort]
            result.issues_history.append(all_issues)
            result.escalation_reason = "누적 issue 임계 초과 — abort"
            print(f"  🚫 [harness] 누적 abort: {action_def.name} (총 {_cumulative}건)")
            _log.warning(f"[harness] 🚫 누적 abort: {action_def.name}")
            _report_issues_to_guardian(action_def.name, attempt, [_abort])
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
            )
            return result

        # ── 검증 실패 — 모든 issues 기록 후 재시도 ──
        # fixed + unfixed 모두 재생성 트리거 ("고쳤더라도 더 나은 결과 위해 재시도")
        all_issues = fixed_issues + unfixed_issues
        result.issues_history.append(all_issues)

        _log.warning(
            f"[harness] ⚠️ 검증 실패 (시도 {attempt}/{action_def.max_attempts}) — "
            f"fixed={len(fixed_issues)}, unfixed={len(unfixed_issues)}: {action_def.name}"
        )
        # unfixed만 GUARDIAN 보고 (fixed는 이미 _record_fixed_to_guardian에서 처리됨)
        if unfixed_issues:
            _report_issues_to_guardian(action_def.name, attempt, unfixed_issues)

        # ── backward-compat abort 신호 (fix 훅 없는 경우 — verify 내부에서 abort 반환) ──
        if any(iss.kind == "abort" for iss in all_issues):
            result.escalation_reason = "verify 즉시 차단 (abort) — 동일 검증 실패 반복, 재시도 무의미"
            _log.warning(f"[harness] 🚫 abort 신호 수신 — 즉시 차단: {action_def.name}")
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
            )
            return result

    # ── max_attempts 도달 — escalation (송출 절대 안 함) ──
    result.escalation_reason = f"max_attempts({action_def.max_attempts}) 도달 — 검증 통과 실패"
    last = result.issues_history[-1] if result.issues_history else []
    _log.error(f"[harness] ❌ escalation — {action_def.name}: {result.escalation_reason}")
    _notify_escalation(action_def.name, action_def.max_attempts, last, reason=result.escalation_reason)
    return result


__all__ = [
    "Issue",
    "ActionStep",
    "ActionDefinition",
    "ActionResult",
    "action_step",
    "run_action",
    "DEFAULT_MAX_ATTEMPTS",
]
