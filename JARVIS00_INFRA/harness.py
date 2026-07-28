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
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from JARVIS00_INFRA.watchdog import (
    Watchdog, StuckError, DEFAULT_ACTION_DEADLINE_SEC, FREEZE_LIMIT_SEC,
    is_killable_subprocess,
)

_log = logging.getLogger("jarvis")


# ── 상수 ─────────────────────────────────────────────

import os as _os_mx

DEFAULT_MAX_ATTEMPTS = max(1, int(_os_mx.getenv("HARNESS_MAX_ATTEMPTS", "2") or "2"))
"""검증 순환 무한 루프 방지 — **어떤 재시도도 최대 2회** (★ 사용자 박제 2026-07-21).

★ 재시도 상한의 *단일 진실 소스*. 호출자는 `max_attempts=` 를 하드코딩하지 말고
  이 기본값을 상속할 것 — 종전엔 economic·theme·auto_repair 가 각자 `max_attempts=3`
  으로 덮어써 이 상수를 무력화했고, 값을 바꾸려면 6곳을 찾아 고쳐야 했다
  ('복사본을 진실로 믿는' 병, CLAUDE.md 최우선 설계 원칙 참조).

  LLM 계층 재시도(`shared/llm.invoke_text`)도 이 상수에서 파생한다 →
  두 층의 곱(최악 증폭 배수)이 상수 하나로 통제된다.

  무배포 조정: 환경변수 `HARNESS_MAX_ATTEMPTS`.
  (2026-07-06 '3회 통일' → 2026-07-21 '2회' 로 사용자 재박제)"""

HARNESS_VERSION = "v3"
"""Self-Evolving Harness 진화 단계 표기 (표시 SSOT — 대시보드·문서가 이 상수에서 파생)."""


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


# ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ────────────────
def interpreter_shutting_down() -> bool:
    """인터프리터(파이썬 실행기)가 종료 단계에 진입했는지 — 데몬 재시작 레이스 감지.

    ★ 근본 원인: 데몬 재시작 시 옛 프로세스가 종료되며 파이썬 표준 정리 훅
    `concurrent.futures.thread._python_exit` 가 전역 `_shutdown=True` 로 바꾼다.
    그 순간, misfire 유예(misfire_grace_time)로 *뒤늦게* 실행되던 발행 잡이 남아
    있으면 수집 단계의 ThreadPoolExecutor.submit() 이
    'cannot schedule new futures after interpreter shutdown' RuntimeError 로 폭발
    → 헛된 "발행 실패 / 글자수 실패" 보고 + GUARDIAN 트리거 + 테마 실패 오기록.

    → 종료 중이면 무거운 동작(발행)을 *아예 시작하지 않고* 연기(deferred)한다.
    keeper 가 재기동한 *새* 프로세스가 같은 misfire 잡을 재실행 → 정상 발행.

    두 신호를 확인한다 (둘 중 하나라도 참이면 종료 중):
      ① concurrent.futures 전역 `_shutdown` — 크래시를 유발하는 *바로 그 조건* (권위 신호)
      ② jarvis_daemon `_daemon_shutdown` 이벤트 — 공개 신호 (이미 로드된 경우만, 순환 import 회피)
    """
    # ① 크래시를 유발하는 바로 그 전역 플래그 (표준 라이브러리 — 여러 파이썬 버전 안정)
    try:
        import concurrent.futures.thread as _cft
        if getattr(_cft, "_shutdown", False):
            return True
    except Exception:
        pass
    # ② 데몬 자체 종료 이벤트 (이미 import 된 경우만 — 순환/재import 회피)
    try:
        import sys as _sys
        _dm = _sys.modules.get("jarvis_daemon")
        _ev = getattr(_dm, "_daemon_shutdown", None)
        if _ev is not None and _ev.is_set():
            return True
    except Exception:
        pass
    return False


# ── 데이터 클래스 ──────────────────────────────────────

@dataclass
class Issue:
    """검증 실패 항목 — 어느 단계의 어떤 문제인지."""
    step: str           # 문제 발생한 step 이름 (또는 "전체")
    kind: str           # 문제 종류 (예: "length", "draft_quality", "login_invalid", "draft_fixed")
    detail: str = ""    # 상세 설명
    # ★ 원인 예외 보존 (결함1 — 2026-07-25).
    #   종전엔 Layer 2/3/4 에서 실제로 터진 예외를 `f"{type(e).__name__}: {e}"` 문자열로
    #   납작하게 만든 뒤 GUARDIAN 에 `RuntimeError` 로 합성해 보고했다. 그 결과
    #   error_log.error_type 이 harness 소스 **342/342 = 100% RuntimeError** 가 되어
    #   `_TRANSIENT_TYPES`·`DETERMINISTIC_CODE_ERROR_TYPES`·`_PATTERN_FIXABLE_TYPES`
    #   같은 *타입 기반 게이트가 전부 무효* 였다 (남은 판별 수단이 메시지 정규식뿐인
    #   상태를 시스템이 스스로 만든 것). 이제 원 예외를 구조로 들고 다닌다.
    #   compare/repr 제외 — fingerprint(=(step,kind,detail[:80]))·로그 의미 불변.
    cause: Optional[BaseException] = field(default=None, repr=False, compare=False)

    @property
    def cause_type(self) -> str:
        """원 예외 타입명 — 없으면 빈 문자열(정직하게 '모름')."""
        return type(self.cause).__name__ if self.cause is not None else ""

    def to_context(self) -> dict:
        return {"step": self.step, "kind": self.kind, "detail": self.detail[:300],
                "cause_type": self.cause_type}


def issue_from_exception(step: str, kind: str, exc: BaseException,
                         prefix: str = "") -> Issue:
    """예외 → Issue 변환 *단일 진입점* (결함1).

    detail 포맷(`{타입}: {메시지}`)을 여기 한 곳에서만 만든다. 종전엔 Layer 1·2·3·4
    네 곳이 같은 f-string 을 각자 조립했고, 그 문자열이 원인 타입의 *유일한* 흔적이라
    (구조화 필드가 아니라) 하류가 정규식으로 되캐야 했다.
    """
    return Issue(step=step, kind=kind,
                 detail=f"{prefix}{type(exc).__name__}: {str(exc)[:200]}",
                 cause=exc)


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
        max_attempts: 검증 순환 한계 (미지정 시 `DEFAULT_MAX_ATTEMPTS` 상속 — 숫자를 적지 말 것.
                      이 자리에 "3" 이 박혀 있었으나 실제 상수는 2 였다, ERRORS [543])
    """
    name: str
    steps: list[ActionStep]
    verify: Callable[[dict], list[Issue]]
    send: Callable[[dict], None]
    precondition: Optional[Callable[[dict], list[Issue]]] = None
    fix: Optional[Callable[[dict, list[Issue]], tuple[list[Issue], list[Issue]]]] = None
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    # ★ 정지 방어 (사용자 박제 2026-07-06): 전체 데드라인(초) — 초과 시 중단(송출 안 함).
    #   블로그 발행 액션은 `watchdog.BLOG_ACTION_DEADLINE_SEC` 명시(현재 2400=40분).
    #   ★ 숫자를 여기 적지 말 것 — 종전 "1800(30분)" 은 실제 상수(2400)와 어긋나 있었다 (ERRORS [543]).
    #   미지정 시 넉넉한 기본(60분) 안전망.
    #   멈춤(freeze) 300초 워치독은 데드라인과 무관하게 항상 적용.
    deadline_sec: float = DEFAULT_ACTION_DEADLINE_SEC
    # ★ escalation 알림에 붙일 "지금 다시 실행" 버튼의 대상 잡 ID (ERRORS [543]).
    #   빈 문자열이면 버튼 없이 종전대로 글만 보낸다(하위호환).
    #   ★ 왜 호출자가 주나: 액션 이름→잡 ID 매핑표를 harness 가 들고 있으면 그게 곧 사본이고
    #     잡이 바뀔 때마다 어긋난다(원칙②). 잡을 아는 쪽(발행 모듈)이 알려준다.
    #   ★ 왜 "발행" 버튼이 아닌가: 헌법(ADR 009) — *결함 있는 결과물은 영원히 송출되지 않는다*.
    #     검증을 우회하는 버튼은 만들지 않는다. 재실행은 **검증 순환을 처음부터 다시** 타는 것이다.
    retry_job_id: str = ""


@dataclass
class ActionResult:
    """동작 실행 결과."""
    delivered: bool                         # 송출 완료 여부 (★ 비전 핵심)
    attempts: int = 0                       # 검증 순환 시도 횟수
    final_state: dict = field(default_factory=dict)
    issues_history: list[list[Issue]] = field(default_factory=list)
    escalation_reason: str = ""             # 송출 안 된 사유 (delivered=False 일 때만)
    deferred: bool = False                  # ★ 인터프리터 종료 레이스로 *연기* (실패 아님 — 재시도 대상)

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

# ── 시도 오류 ↔ 최종 결과 정합 (ERRORS [462] — 2026-07-21) ─────────────
#
# ★ 문제: harness 는 *시도마다* 실패를 GUARDIAN 에 즉시 보고하는데, 그 액션이
#   최종적으로 **성공(best-so-far 발행 등)** 해도 앞서 보고한 오류를 거두지 않았다.
#   → GUARDIAN 이 이미 해소된 문제를 붙잡고 자동수정을 돌린 뒤
#     "자동수정 실패 — 수동 검토" 오알림을 보냈다. 사용자는 *발행이 끝난 글* 에 대해
#     실패 알림을 받는다(경제·테마·네이버·티스토리 공통). 알림 신뢰가 무너지는 유형.
#
# ★ 해결: 액션 단위로 발급된 오류 ID 를 모아, 최종 delivered=True 면 일괄 해소한다.
#   단일 진입점 — 다른 곳에서 개별 해소 로직을 만들지 말 것.
_ATTEMPT_ERROR_IDS: dict[str, list[int]] = {}

# ── ★ 프로세스 경계를 넘는 되돌림 (결함3 — 2026-07-25) ────────────────────
#
# ★ 문제: 위 dict 은 *프로세스 메모리* 다. 그런데 판정을 되돌리는 경로는 이것 하나뿐이었다.
#   경제 브리핑은 **subprocess** 로 돌고, 워치독은 freeze 시 `os._exit` 로 강제 종료하며,
#   keeper 는 데몬을 통째로 재기동한다. 그 순간 id 목록이 증발하고 → 잠정(provisional)
#   표시가 영영 안 풀려 → 30분 뒤 `job_retry_pending` 이 **분석 0회로 `ignored`** 처리한다.
#   실측 2026-07-25: harness 오류 342건 중 `ignored` 225건(66%), 그중 178건이 llm_attempts=0.
#   CLAUDE.md 명문 위반이다 — "threading.Event·메모리 집합은 같은 프로세스만 방어한다".
#
# ★ 해결: id 목록을 *저장* 하지 않고 **DB 에서 파생**한다 (② 동적 설계).
#   harness 는 이미 모든 시도 오류를 `source='harness'` + `module=action_module(name)` 로
#   박아두고 있다 — 그것이 곧 소유 표식이다. 스키마 변경 0, 새 컬럼 0, 기존 공개 헬퍼만 사용.
#   메모리 목록은 *빠른 경로* 로 남기고 DB 파생분과 합집합을 취한다 (기존 동작 완전 보존).
_HARNESS_MODULE_PREFIX = "JARVIS00_INFRA.harness."


def action_module(action_name: str) -> str:
    """액션의 error_log.module 값 — 보고·회수 양쪽이 이 한 함수에서 파생 (① 단일 진입점)."""
    return f"{_HARNESS_MODULE_PREFIX}{action_name}"


def _orphan_window_min() -> float:
    try:
        return float(_os_mx.getenv("HARNESS_ORPHAN_WINDOW_MIN", "720") or "720")
    except Exception:
        return 720.0


def _db_attempt_error_ids(action_name: str, statuses: tuple) -> list[int]:
    """이 액션이 낸 *미결* 시도 오류 id 를 DB 에서 파생 — 프로세스 경계를 넘는다.

    킬스위치 `HARNESS_DB_ATTEMPT_IDS=0` → 즉시 종전(메모리 전용) 동작으로 복귀.
    창(window) 밖 과거 행은 건드리지 않는다 (`HARNESS_ORPHAN_WINDOW_MIN`, 기본 720분).
    """
    if (_os_mx.getenv("HARNESS_DB_ATTEMPT_IDS", "1") or "1").strip() == "0":
        return []
    try:
        from shared.db import list_errors
        from datetime import datetime as _dt, timedelta as _td
    except Exception:
        return []
    cutoff = _dt.now() - _td(minutes=_orphan_window_min())
    mod = action_module(action_name)
    out: list[int] = []
    for st in statuses:
        try:
            rows = list_errors(status=st, limit=300) or []
        except Exception:
            continue
        for r in rows:
            try:
                if str(r.get("source") or "") != "harness":
                    continue
                if str(r.get("module") or "") != mod:
                    continue
                ts = str(r.get("timestamp") or "")
                if ts:
                    try:
                        if _dt.fromisoformat(ts.replace("Z", "+00:00").split("+")[0]) < cutoff:
                            continue
                    except Exception:
                        pass
                out.append(int(r["id"]))
            except Exception:
                continue
    return out


def _attempt_error_ids(action_name: str, statuses: tuple) -> list[int]:
    """메모리(빠름) ∪ DB 파생(프로세스 경계 넘김) — 되돌림 대상 id 단일 조회."""
    ids = list(_ATTEMPT_ERROR_IDS.get(action_name) or [])
    seen = set(ids)
    for i in _db_attempt_error_ids(action_name, statuses):
        if i not in seen:
            ids.append(i)
            seen.add(i)
    return ids


def _resolve_attempt_errors(action_name: str, resolution: str) -> int:
    """액션이 최종 성공했을 때, 그 과정에서 보고된 시도 오류를 해소 처리.

    ★ 이번 실행분(메모리)뿐 아니라, *같은 액션이 앞서 죽으면서 남긴 고아 행*(DB 파생)도
      함께 해소한다 — 그 실패들은 지금의 성공으로 무효화된 것이 맞고, 방치하면 아무도
      분석하지 않은 채 `ignored` 로 썩는다.
    """
    ids = _attempt_error_ids(action_name, ("new", "analyzing", "ignored"))
    _ATTEMPT_ERROR_IDS.pop(action_name, None)
    if not ids:
        return 0
    try:
        from shared.db import mark_error_fixed
    except Exception:
        return 0
    done = 0
    for eid in ids:
        try:
            mark_error_fixed(eid, resolution, fixed_file=None)
            done += 1
        except Exception:
            continue
    if done:
        _log.info(f"[harness] 시도 오류 {done}건 해소 — 최종 성공으로 무효화: {action_name}")
    return done


def _finalize_attempt_errors(action_name: str) -> int:
    """액션이 최종 실패로 끝났을 때 — 잠정 표시를 풀어 Tier-2 판정 대상으로 승격.

    ★ `_resolve_attempt_errors`(최종 *성공* 시 무효화)와 대칭 (ERRORS [476]).
      성공하면 '문제가 아니었던 것' 이고, 최종 실패해야 비로소 '진짜 볼 만한 것' 이다.
      _ATTEMPT_ERROR_IDS 는 여기서 pop 하지 않는다 — escalation 후에도 소급 성공
      (best-so-far 발행)이 있을 수 있어 `_resolve_attempt_errors` 가 여전히 필요하다.

      ★ 결함3: DB 파생분을 합쳐, *이전 프로세스가 죽으면서 남긴* 잠정 행도 여기서 승격된다.
        (`ignored` 는 제외 — 이미 격리된 것을 되살리지 않는다. 승격은 미결 행에만.)
    """
    ids = _attempt_error_ids(action_name, ("new", "analyzing"))
    if not ids:
        return 0
    try:
        from shared.db import finalize_provisional_errors
        n = finalize_provisional_errors(ids)
    except Exception:
        return 0
    if n:
        _log.info(f"[harness] 잠정 오류 {n}건 확정 — 최종 실패로 Tier-2 판정 대상 승격: {action_name}")
    return n


def _report_issues_to_guardian(action_name: str, attempt: int, issues: list[Issue],
                               max_attempts: int = 0) -> None:
    """검증 실패 → error_collector.report() 박제. learned_patterns 자동 등록.

    실패해도 (예: GUARDIAN 미가용) 검증 순환은 계속 진행 — try/except 격리.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import report as g_report
    except Exception:
        _log.warning("[harness] GUARDIAN import 실패 — 학습 자산화 생략 (검증 순환은 계속)")
        return

    _preserve = (_os_mx.getenv("HARNESS_PRESERVE_CAUSE", "1") or "1").strip() != "0"

    for issue in issues:
        try:
            _msg = f"[harness:{action_name}] attempt={attempt} step={issue.step}: {issue.detail}"
            # ★ kind 는 *반드시* 실린다 (결함1). `severity.kind_of()` 는 context 만 읽으므로
            #   여기서 비면 하류의 kind 기반 게이트(NON_CODE_ISSUE_KINDS)가 통째로 죽는다.
            _ctx = {
                "layer": 3,
                "action": action_name,
                "attempt": attempt,
                "step": issue.step,
                "kind": (issue.kind or "unknown"),
                "detail": issue.detail,
                # ★ 원인 타입을 *구조화 필드* 로도 보존 — 메시지 정규식으로 되캐지 않게 한다.
                "cause_type": issue.cause_type,
                "harness_wrapped": True,
            }
            _cause = issue.cause if _preserve else None
            if _cause is not None:
                # ★ 래핑하되 원인을 잃지 않는다 — error_type 을 *원 예외 타입* 으로 보고한다.
                #   RuntimeError 합성이 타입 기반 게이트를 무력화하던 구간의 근본 차단.
                #   traceback 도 원 예외 것을 그대로 넘긴다(format_exc() 는 except 블록 밖에서
                #   "NoneType: None" 을 돌려주므로 신뢰 불가).
                try:
                    import traceback as _tbm
                    _tb = "".join(_tbm.format_exception(
                        type(_cause), _cause, _cause.__traceback__))
                except Exception:
                    _tb = None
                _eid = g_report(
                    type(_cause).__name__,     # ★ 문자열 형태 = error_type 직접 지정
                    source="harness",
                    message=_msg,
                    module=action_module(action_name),
                    func_name=issue.step,
                    tb_str=_tb,
                    context=_ctx,
                )
            else:
                exc = RuntimeError(_msg)
                _eid = g_report(
                    exc,                   # ★ catch(exc_or_type, ...) 첫 위치 인자 (exc= 키워드 없음)
                    source="harness",
                    module=action_module(action_name),
                    func_name=issue.step,
                    context=_ctx,
                )
            # ★ 최종 성공 시 되돌리기 위해 액션별로 보관 (ERRORS [462])
            if _eid:
                _ATTEMPT_ERROR_IDS.setdefault(action_name, []).append(int(_eid))
                # ★ 아직 재시도가 남았으면 '잠정' 표시 → Tier-2 판정 보류 (ERRORS [476]).
                #   이 시점엔 일시적인지 결정론적인지 알 수 없다. 기록은 즉시 남기되
                #   (대시보드 관측성 유지) *판정만* 액션 종료까지 미룬다.
                if max_attempts and attempt and attempt < max_attempts:
                    try:
                        from shared.db import mark_error_provisional
                        mark_error_provisional(int(_eid), True)
                    except Exception:
                        pass
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
                       reason: str = "", retry_job_id: str = "") -> None:
    """max_attempts 도달 또는 precondition 실패 — 사용자 텔레그램 escalation.

    송출은 *절대 안 함*. 사용자가 수동 검토해야 함.

    ★ 행동 버튼 (ERRORS [543]): 종전엔 *"호스트에서 수동 검토 필요"* 라는 **글만** 보내고 끝나
      사용자가 텔레그램에서 할 수 있는 일이 0이었다. `retry_job_id` 가 있으면
      "🔁 지금 다시 실행" 버튼을 붙인다.

    ★ 왜 대기 딕셔너리를 안 쓰나 (비직관 — 프로세스 경계): `_PENDING_*` 는 **데몬 메모리** 인데
      경제 브리핑은 **subprocess** 다. 서브프로세스가 거기 등록해도 데몬의 봇 루프는 못 본다
      (CLAUDE.md 프로세스 경계 규정 / ERRORS [474] 와 같은 클래스).
      → 필요한 정보(잡 ID)를 **callback_data 에 실어** 보낸다. 서버측 상태 0.
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

    # 1순위: shared.notify — 재실행 잡을 아는 경우 행동 버튼 첨부
    if retry_job_id:
        try:
            from shared.notify import send_tg_with_buttons  # type: ignore
            send_tg_with_buttons(
                msg + f"\n\n재실행 대상: `{retry_job_id}`",
                [[{"text": "🔁 지금 다시 실행", "callback_data": f"hesc_run:{retry_job_id}"},
                  {"text": "🗑 이번 회차 보류", "callback_data": "hesc_skip"}]],
            )
            return
        except Exception:
            pass          # 버튼 실패 시 아래 글 전송으로 폴백 (알림 자체는 절대 잃지 않는다)
    try:
        from shared.notify import send_tg  # type: ignore
        send_tg(msg)
        return
    except Exception:
        pass

    # 2순위: 로깅만
    _log.error(f"[harness] escalation: {msg}")


# ── 실행 엔진 ─────────────────────────────────────────

# 산출물 재생성 없이 재검증만 수행하는 재개 신호 (검증·송출 단계 이슈 / 전부 즉시수정된 경우)
VERIFY_ONLY = "__verify_only__"

# ★ 액션 이름이 담기는 state 키 (ERRORS [543]) — step 이 리소스 스코프로 쓴다.
#   `resources.close_scope(action_def.name)` 와 짝. 문자열을 양쪽에 박지 말 것.
ACTION_NAME_KEY = "__action_name__"

# 실제 수행 step 이 아닌 라벨 — 재생성 재개 지점 산정에서 제외
_NON_STEP_LABELS = ("전체", "verify", "verify (Layer 3)", "송출 (Layer 4)")


def _is_fixed_issue(iss: Issue) -> bool:
    """이미 즉시수정 완료된 이슈인지 — 재생성 트리거 대상이 아님."""
    return iss.kind == "draft_fixed" or str(iss.kind).startswith("fixed:")


# ★ 인프라 사유(일시적 — 재작성 대상 아님, fingerprint 제외·backoff·defer 로 처리) kind 집합.
#   콘텐츠 결함(draft_failed·draft_quality·factuality·engagement)은 *포함하지 않는다* —
#   그건 재작성으로 고칠 수 있는 것. 여기엔 '아직 인프라가 안 풀렸다' 신호만.
#   보수적으로 명시 kind 만(과대분류 시 진짜 코드버그가 abort 없이 max_attempts 소진).
INFRA_KIND = "infra_throttle"
_INFRA_ISSUE_KINDS = frozenset({INFRA_KIND})   # ★ 목록은 상수에서 *파생* (② 동적 설계)


def classify_failure_issue(step: str, error, *,
                           content_kind: str = "draft_failed",
                           content_prefix: str = "대본 생성 실패: ") -> Issue:
    """산출물 생성 실패 → Issue 로 *분류* 하는 단일 진입점 (결함2 — 2026-07-25).

    ★ 왜 여기인가: 인프라 미완결(`infra_throttle`)과 콘텐츠 결함을 가르는 판정은
      harness 의 재시도 정책(`_INFRA_ISSUE_KINDS` → fingerprint 제외·backoff·defer)에
      전적으로 종속된다. 그런데 이 판정 코드가 `economic_poster` 와 `trend_theme_writer`
      **두 파일에 그대로 복사**돼 있었다. "4조합에 자동 적용된다" 는 말이 성립한 이유가
      *구조화 필드라서* 가 아니라 *같은 코드를 두 벌 붙여놨기 때문* 이었다는 뜻이고,
      한쪽만 고치면 다른 쪽에서 재발한다 (CLAUDE.md ①단일 진입점 + ③모든 곳 적용 위반).
      → 판정은 여기 한 곳. 호출자는 결과를 *받기만* 한다.

    detail 은 fingerprint 안정성을 위해 고정 문자열만 쓴다 (attempt·점수 등 변동값 금지).
    """
    _derr = str(error if error is not None else "unknown")
    try:
        from shared.llm import is_infra_error as _is_infra, describe_infra_error as _desc
    except Exception:
        # shared.llm 미가용 = 판정 불가 → 보수적으로 콘텐츠 결함(재작성 시도) 유지.
        return Issue(step=step, kind=content_kind, detail=f"{content_prefix}{_derr}")
    if _is_infra(_derr):
        return Issue(step=step, kind=INFRA_KIND,
                     detail=_desc(_derr) + " — 대본 생성 미완결(일시적, 다음 시도/회차 재개)")
    return Issue(step=step, kind=content_kind, detail=f"{content_prefix}{_derr}")


def _is_infra_issue(iss: Issue) -> bool:
    """일시적 인프라 사유 이슈인지 — fingerprint 제외·backoff·defer 대상.

    인프라 실패의 지문 반복은 '재생성해도 동일'이 아니라 '아직 인프라가 안 풀렸다'의 신호이므로
    abort 근거가 될 수 없다. 콘텐츠 결함(재작성으로 고칠 수 있는 것)은 여기 포함되지 않는다.
    """
    return iss.kind in _INFRA_ISSUE_KINDS


def _backoff_infra_wait(action_def: "ActionDefinition", wd: Optional[Watchdog]) -> None:
    """인프라-only 실패 후 다음 attempt 전 backoff — 스로틀 창이 지나가길 대기(rank7).

    회로 쿨다운(LLM_CIRCUIT_COOLDOWN_SEC, 기본 90s)에 맞추되 action deadline 잔여 예산으로
    캡(송출 여유 60s 남김), 잘게 쪼개 wd.beat() 해 워치독 freeze 오탐을 방지한다. 콘텐츠
    결함엔 호출되지 않는다(즉시 재시도 유지 — 빠른 재작성).
    """
    import os as _os
    try:
        want = float(_os.getenv("LLM_CIRCUIT_COOLDOWN_SEC", "90") or "90")
    except Exception:
        want = 90.0
    # deadline 잔여 예산으로 캡 — 무제한 대기·데드라인 초과 방지 (송출 여유 60s 남김)
    if wd is not None and getattr(wd, "deadline_sec", None):
        try:
            _remain = wd.deadline_sec - wd.elapsed()
            want = min(want, max(0.0, _remain - 60.0))
        except Exception:
            pass
    if want <= 0:
        return
    _log.info(f"[harness] ⏳ 인프라 스로틀 backoff {want:.0f}s — 스로틀 창 회피 후 재시도")
    print(f"  ⏳ [harness] 인프라 스로틀 — {want:.0f}s 대기 후 재시도(창 회피)")
    _slept = 0.0
    while _slept < want:
        _chunk = min(10.0, want - _slept)
        time.sleep(_chunk)
        _slept += _chunk
        if wd is not None:
            try:
                wd.beat()
            except Exception:
                pass


def _find_resume_step(action_def: ActionDefinition, last_issues: list[Issue]) -> Optional[str]:
    """이전 시도의 issues 에서 *재실행 시작 step* 식별.

    문제 step 들 중 *action_def.steps 순서에서 가장 앞* 인 step 부터 재실행.

    ★ 근본 수정 (2026-07-16 — 즉시수정 완료 대본을 통째로 재생성하던 사고):
      ① 즉시수정 완료(draft_fixed) 이슈는 재생성 트리거에서 제외 — 수정된 산출물을
        버리고 처음부터 다시 만들 이유가 없다.
      ② 남은 이슈가 검증(verify)·송출 단계 것뿐이면 VERIFY_ONLY 반환 — 산출물은
        유효하므로 Layer 2 를 건너뛰고 재검증→재송출만 수행 (LLM 타임아웃 같은
        인프라 실패가 5분+ 산출물을 폐기시키는 경로 원천 차단).
    """
    if not last_issues:
        return None
    live = [iss for iss in last_issues if not _is_fixed_issue(iss)]
    if not live:
        return VERIFY_ONLY   # 전부 즉시수정 완료 — 수정된 산출물로 재검증만
    problem_step_names = {
        iss.step for iss in live
        if iss.step not in _NON_STEP_LABELS
    }
    if not problem_step_names:
        return VERIFY_ONLY   # 검증·송출 단계 이슈만 — 산출물 재생성 불필요
    for step in action_def.steps:
        if step.name in problem_step_names:
            return step.name
    return None


def _execute_steps(action_def: ActionDefinition, state: dict,
                   from_step_name: Optional[str] = None,
                   wd: Optional[Watchdog] = None) -> dict:
    """Layer 2 — 수행 단계 시퀀스 실행.

    from_step_name 가 주어지면 그 step 부터 *재실행*. 이전 step 의 결과는 state 에 유지.
    step 실행 자체가 폭발 시 state["__step_error__"] = Issue 박고 즉시 반환.
    wd(워치독): 스텝마다 beat()(freeze 리셋) + check()(데드라인 초과면 StuckError 전파).
    """
    start_idx = 0
    if from_step_name:
        for i, step in enumerate(action_def.steps):
            if step.name == from_step_name:
                start_idx = i
                break

    for step in action_def.steps[start_idx:]:
        if wd is not None:
            wd.check()      # 데드라인 초과 시 StuckError → run_action 이 escalation
            wd.beat()       # 스텝 진입 = 진행 신호 (freeze 카운터 리셋)
        try:
            state = step(state)
        except Exception as e:
            _log.error(f"[harness] Layer 2 step '{step.name}' 폭발: {type(e).__name__}: {e}")
            state = dict(state)
            # ★ 결함1: 원 예외를 그대로 들고 간다 (RuntimeError 합성으로 타입이 지워지던 구간)
            state["__step_error__"] = issue_from_exception(step.name, "execution_error", e)
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
    # ★ 액션 이름을 state 로 (ERRORS [543]) — step 이 리소스 스코프로 쓴다.
    #   왜 이렇게: 액션 이름을 step 쪽에 문자열로 박으면 두 곳이 되고(원칙① 위반),
    #   테마는 이름이 *동적*(`theme-publish-{theme}-tistory`)이라 박을 수도 없다.
    #   harness 가 알려주면 step 은 `resources.put(state[ACTION_NAME_KEY], ...)` 한 줄이면 된다.
    state[ACTION_NAME_KEY] = action_def.name
    result = ActionResult(delivered=False, final_state=state)

    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 데몬 재시작 등으로 인터프리터가 종료 단계면 무거운 발행을 *시작하지 않고* 연기.
    # 실행 시 ThreadPoolExecutor 크래시 → 헛된 "발행 실패" 를 원천 차단.
    # deferred=True 는 *실패 아님* — 호출자는 재시도 대상으로 처리 (진행상태 미기록·GUARDIAN 스킵).
    if interpreter_shutting_down():
        _log.warning(f"[harness] ⏸ 인터프리터 종료 중(데몬 재시작) — 동작 연기(deferred): {action_def.name}")
        result.deferred = True
        result.escalation_reason = "인터프리터 종료 중(데몬 재시작) — 발행 연기, 재시작 후 재시도"
        return result

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
        _notify_escalation(action_def.name, 0, [dup_issue], reason=reason, retry_job_id=action_def.retry_job_id)
        return result

    # ── ★ 정지 방어 워치독 (사용자 박제 2026-07-06) ──
    #   · 데드라인: action_def.deadline_sec 초과 시 중단(블로그 발행=30분).
    #   · 멈춤(freeze): 300초 무진전 시 중단. killable subprocess 면 os._exit(다음 예약 재시도).
    #   중단 = 검증 순환 밖 강제 종료 → escalation(송출 절대 안 함) + GUARDIAN 원인 진단.
    def _on_stuck(name: str, reason: str) -> None:
        try:
            iss = [Issue(step="전체", kind="stuck", detail=reason)]
            # 원인 진단은 killable 여부와 무관하게 항상 GUARDIAN 에 박제(가시성 유지 —
            # 반복 freeze 는 진짜 성능결함일 수 있음, 사용자 박제 2026-07-22).
            _report_issues_to_guardian(name, result.attempts, iss)
            # ★ 2026-07-24: killable subprocess 는 freeze 직후 os._exit(WATCHDOG_KILL_RC)
            #   → 다음 예약 회차에 자동 재시도된다. "🚨 송출 차단" escalation 은
            #   *오경보* — 수동 조치 불필요를 알리는 정보성 메시지로 대체.
            #   (non-killable in-process 동작은 종전대로 escalation — 자연 재시도 없음.)
            if is_killable_subprocess():
                try:
                    from shared.notify import send_tg
                    send_tg(
                        f"⏱ *정지 감지 — 자동 재시도 예정*\n\n"
                        f"동작: `{name}`\n사유: {reason}\n\n"
                        f"_killable 프로세스 강제 재기동 — 수동 조치 불필요 "
                        f"(다음 예약 회차 자동 재시도)_"
                    )
                except Exception:
                    pass
            else:
                _notify_escalation(name, result.attempts, iss, reason=reason, retry_job_id=action_def.retry_job_id)
        except Exception:
            pass

    try:
        _log.info(f"[harness] ▶️ 동작 시작: {action_def.name}")
        with Watchdog(action_def.name, deadline_sec=action_def.deadline_sec,
                      freeze_sec=FREEZE_LIMIT_SEC, on_stuck=_on_stuck) as _wd:
            return _run_action_locked(action_def, state, result, _wd)
    except StuckError as _se:
        reason = str(_se)
        _log.error(f"[harness] ⏱ 정지 감지 — {action_def.name}: {reason} (송출 안 함)")
        # ★ 결함1: StuckError 원 타입 보존 (detail 은 종전 문구 유지 — fingerprint 불변)
        iss = [Issue(step="전체", kind="stuck", detail=reason, cause=_se)]
        result.issues_history.append(iss)
        result.escalation_reason = reason
        try:
            _report_issues_to_guardian(action_def.name, result.attempts, iss)
            _notify_escalation(action_def.name, result.attempts, iss, reason=reason, retry_job_id=action_def.retry_job_id)
        except Exception:
            pass
        return result
    finally:
        # ★ 살아있는 핸들 일괄 정리 (ERRORS [543]) — 성공·실패·정지 **어느 경로로 끝나든**.
        #   왜 여기인가: state 는 액션이 끝나면 그냥 버려진다. 호출자가 close 를 잊으면
        #   아무도 안 닫는다 — 실제로 경제 브리핑이 티스토리 driver 를 성공할 때마다
        #   남기고 있었다(소비처 0 · quit 은 실패 분기에만). 정리를 *지나가야만 하는 문* 으로 둔다.
        try:
            from JARVIS00_INFRA.resources import close_scope as _close_scope
            _n = _close_scope(action_def.name)
            if _n:
                _log.info(f"[harness] 🧹 살아있는 핸들 {_n}개 정리: {action_def.name}")
        except Exception:
            pass          # 정리 실패가 본 작업 결과를 덮지 않는다
        _lock.release()


def _run_action_locked(action_def: ActionDefinition, state: dict,
                       result: ActionResult,
                       wd: Optional[Watchdog] = None) -> ActionResult:
    """run_action 본체 — 락 보유 상태에서만 호출. _ACTION_LOCKS 외부에서 직접 호출 금지."""

    # ── Layer 1: precondition (선택) ──
    if action_def.precondition is not None:
        try:
            pre_issues = action_def.precondition(state) or []
        except Exception as e:
            _log.error(f"[harness] Layer 1 precondition 폭발: {type(e).__name__}: {e}")
            pre_issues = [issue_from_exception(
                "precondition (Layer 1)", "precondition_error", e)]

        if pre_issues:
            _log.warning(f"[harness] Layer 1 precondition 실패: {len(pre_issues)} issues")
            _report_issues_to_guardian(action_def.name, 0, pre_issues)
            result.issues_history.append(pre_issues)
            result.escalation_reason = "Layer 1 precondition 실패"
            _notify_escalation(action_def.name, 0, pre_issues, reason="precondition", retry_job_id=action_def.retry_job_id)
            return result

    # ── Layer 2 + 3: 수행 + 검증 순환 ──
    for attempt in range(1, action_def.max_attempts + 1):
        result.attempts = attempt
        if wd is not None:
            wd.check()      # 시도 시작 전 데드라인 체크 (초과 시 StuckError → escalation)

        # 재시도는 *문제 step 부터* (이전 시도의 issues 에서 식별)
        from_step = None
        if result.issues_history:
            from_step = _find_resume_step(action_def, result.issues_history[-1])

        # ★ VERIFY_ONLY — 산출물은 유효 (즉시수정 완료 or 검증·송출 인프라 이슈만).
        #   Layer 2 재실행 없이 기존 state 그대로 재검증→재송출만 수행.
        #   (LLM 타임아웃 1회가 완성된 대본·차트를 폐기시키던 사고의 근본 차단)
        if from_step == VERIFY_ONLY:
            _log.info(f"[harness] ♻️ 산출물 유지 — 재검증만 진행 (attempt {attempt}): {action_def.name}")
            print(f"  ♻️ [harness] 이전 산출물 유지 — 재검증만 진행 (시도 {attempt})")
        else:
            # Layer 2: 수행 단계 실행
            state = _execute_steps(action_def, state, from_step_name=from_step, wd=wd)
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
                issues = [issue_from_exception("verify (Layer 3)", "verify_error", e)]

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
                # ★ 송출 성공 → 앞선 시도에서 보고한 오류 무효화 (ERRORS [462]).
                #   시도1 실패 → 시도2 통과 인 경우에도 GUARDIAN 오알림이 나갔다.
                _resolve_attempt_errors(
                    action_def.name,
                    f"harness 검증 통과·송출 완료(시도 {attempt}) — 시도 실패는 최종 결과로 무효")
                return result
            except Exception as e:
                # 송출 실패 = 송출 미완료 — 검증 순환 재진입
                _log.warning(
                    f"[harness] Layer 4 송출 실패 (시도 {attempt}) — 검증 순환 재진입: "
                    f"{type(e).__name__}: {e}"
                )
                send_issue = issue_from_exception("송출 (Layer 4)", "send_failure", e)
                result.issues_history.append([send_issue])
                _report_issues_to_guardian(action_def.name, attempt, [send_issue], action_def.max_attempts)
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
                    except Exception as _ve:
                        # ★ 재검증 자체 폭발 = 무검증 송출 금지 (ADR 009) — 이슈로 박제
                        #   (기존: _rev=[] 로 '통과' 처리 → 검증 안 된 산출물이 송출되는 구멍)
                        _rev = [issue_from_exception("verify (Layer 3)", "verify_error", _ve)]
                    if not _rev:
                        _log.info(f"[harness] ✅ 즉시수정 후 재검증 통과 — 재생성 없이 송출: {action_def.name}")
                        print(f"  ✅ [harness] 즉시수정 후 검증 통과 — 재생성 건너뜀")
                        try:
                            action_def.send(state)
                            result.delivered = True
                            result.issues_history.append([])
                            return result
                        except Exception as _se:
                            send_issue = issue_from_exception("송출 (Layer 4)", "send_failure", _se)
                            result.issues_history.append([send_issue])
                            _report_issues_to_guardian(action_def.name, attempt, [send_issue], action_def.max_attempts)
                            # 송출 실패 → 정상 재시도 루프로 fall-through
                    else:
                        # ★ 근본 수정 (2026-07-16): 재검증 이슈(_rev) 폐기 금지.
                        #   기존엔 _rev 를 버리고 fixed 이슈만 기록 → 다음 attempt 가
                        #   '수정 완료된 대본'을 재생성 지점으로 오판, 차트·이미지까지
                        #   전량 폐기(5분+ 낭비). 이제 _rev 에 fix 훅을 재실행해
                        #   (gate_feedback 저장 겸함) 실제 미해결 이슈로 채택한다.
                        print(f"  ⚠️ [harness] 즉시수정 후 재검증 신규 이슈 {len(_rev)}건 — 수정 시도")
                        try:
                            _fixed2, _unfixed2 = action_def.fix(state, _rev)
                            result.final_state = state
                        except Exception:
                            _fixed2, _unfixed2 = [], list(_rev)
                        if _fixed2:
                            _record_fixed_to_guardian(action_def.name, attempt, _fixed2)
                        fixed_issues = fixed_issues + list(_fixed2)
                        unfixed_issues = list(_unfixed2)
                        # unfixed2=0(전부 재수정)이면 아래 기록이 전부 fixed 이슈 →
                        # 다음 attempt 가 VERIFY_ONLY 로 재검증만 수행 (전체 재실행 아님)

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
            if not _is_infra_issue(iss)   # ★ rank6: 인프라 이슈는 지문 제외 — 반복=미해결 인프라(스로틀),
                                          #   재생성해도 동일이 아니라 '아직 안 풀림'. abort 근거 아님.
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
            _report_issues_to_guardian(action_def.name, attempt, [_abort], action_def.max_attempts)
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
                retry_job_id=action_def.retry_job_id,
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
            _report_issues_to_guardian(action_def.name, attempt, [_abort], action_def.max_attempts)
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
                retry_job_id=action_def.retry_job_id,
            )
            return result

        # ── 검증 실패 — 모든 issues 기록 후 재시도 ──
        # ★ 정책 갱신 (2026-07-16): fixed 이슈는 기록용일 뿐 재생성 트리거가 아니다.
        #   _find_resume_step 이 draft_fixed 를 제외하고 unfixed 기준으로만 재개 지점을
        #   잡는다 (전부 fixed 면 VERIFY_ONLY — 수정된 산출물로 재검증만).
        all_issues = fixed_issues + unfixed_issues
        result.issues_history.append(all_issues)

        _log.warning(
            f"[harness] ⚠️ 검증 실패 (시도 {attempt}/{action_def.max_attempts}) — "
            f"fixed={len(fixed_issues)}, unfixed={len(unfixed_issues)}: {action_def.name}"
        )
        # unfixed만 GUARDIAN 보고 (fixed는 이미 _record_fixed_to_guardian에서 처리됨)
        if unfixed_issues:
            _report_issues_to_guardian(action_def.name, attempt, unfixed_issues, action_def.max_attempts)

        # ── backward-compat abort 신호 (fix 훅 없는 경우 — verify 내부에서 abort 반환) ──
        if any(iss.kind == "abort" for iss in all_issues):
            result.escalation_reason = "verify 즉시 차단 (abort) — 동일 검증 실패 반복, 재시도 무의미"
            _log.warning(f"[harness] 🚫 abort 신호 수신 — 즉시 차단: {action_def.name}")
            _notify_escalation(
                action_def.name, attempt, all_issues,
                reason=result.escalation_reason,
                retry_job_id=action_def.retry_job_id,
            )
            return result

        # ── ★ rank7: 인프라-only 실패 → 다음 attempt 전 backoff (스로틀 창 회피) ──
        #   스로틀은 '조금 기다리면 풀리는' 일시 장애. 즉시 재시도는 같은 창에 재진입해 또
        #   절단당한다. 이번 시도의 미해결이 *전부* 인프라면 회로 쿨다운만큼(예산 내) 대기.
        #   콘텐츠 결함이 하나라도 섞이면 즉시 재시도 유지(빠른 재작성).
        if (attempt < action_def.max_attempts and unfixed_issues
                and all(_is_infra_issue(i) for i in unfixed_issues)):
            _backoff_infra_wait(action_def, wd)

    # ── max_attempts 도달 ──
    last = result.issues_history[-1] if result.issues_history else []
    # ★ rank8: 마지막 시도의 미해결이 *전부* 인프라(스로틀 등)면 하드 escalation 대신 deferred —
    #   다음 cron/keeper 가 자연 재시도(스로틀 해소 후 1패스 가능). 콘텐츠 결함은 기존 escalation
    #   유지(사용자 검토 필요). 발행(Layer4)은 어차피 안 함 — defer 는 '송출 안 함' 원칙과 정합.
    _live_last = [i for i in last if not _is_fixed_issue(i)]
    if _live_last and all(_is_infra_issue(i) for i in _live_last):
        result.deferred = True
        result.escalation_reason = (
            f"인프라 스로틀 {action_def.max_attempts}회 지속 — 발행 연기(deferred), 다음 회차 재시도"
        )
        _log.warning(f"[harness] ⏸ 인프라 스로틀 지속 — deferred: {action_def.name}")
        print(f"  ⏸ [harness] 인프라 스로틀 {action_def.max_attempts}회 지속 — 발행 연기(다음 회차 재시도)")
        return result

    # ── ★ best-so-far 발행 (사용자 박제 2026-07-19): 남은 미해결이 *품질 점수(engagement)뿐* 이면
    #   escalation(미발행) 대신 최선(마지막 개선분) 대본을 발행한다 — 좋아지던 글을 버리지 않는다.
    #   사실성·구조·분량 등 correctness 실패가 하나라도 섞이면 기존 escalation 유지(거짓·결함 발행 금지). ──
    _live_content = [i for i in last if not _is_fixed_issue(i) and not _is_infra_issue(i)]
    if _live_content and all(i.kind == "engagement" for i in _live_content):
        try:
            action_def.send(state)
            result.delivered = True
            result.escalation_reason = ""
            # ★ 발행 성공 → 시도 오류 무효화 (GUARDIAN 오알림 차단, ERRORS [462])
            _resolve_attempt_errors(
                action_def.name,
                "harness best-so-far 발행 성공 — 시도 실패는 최종 결과로 무효")
            _log.warning(
                f"[harness] ✅ best-so-far 발행 — 품질점수(100점)만 미달({action_def.max_attempts}회), "
                f"사실성·구조 결함 없어 최선 대본 송출: {action_def.name}")
            print("  ✅ [harness] best-so-far 발행 — 100점 미달이나 correctness 결함 0 → 최선 대본 발행(미발행 방지)")
            return result
        except Exception as _bse:
            _log.warning(f"[harness] best-so-far 송출 실패 → escalation: {_bse}")

    # ── 콘텐츠 결함 등 — escalation (송출 절대 안 함) ──
    result.escalation_reason = f"max_attempts({action_def.max_attempts}) 도달 — 검증 통과 실패"
    _log.error(f"[harness] ❌ escalation — {action_def.name}: {result.escalation_reason}")
    # ★ 최종 실패 확정 — 잠정 표시 해제로 Tier-2 판정 대상 승격 (ERRORS [476])
    _finalize_attempt_errors(action_def.name)
    _notify_escalation(action_def.name, action_def.max_attempts, last,
                       reason=result.escalation_reason, retry_job_id=action_def.retry_job_id)
    return result


__all__ = [
    "Issue",
    "ActionStep",
    "ActionDefinition",
    "ActionResult",
    "action_step",
    "run_action",
    "interpreter_shutting_down",
    "DEFAULT_MAX_ATTEMPTS",
    # ★ 2026-07-25 — 원인 보존·kind 분류 단일 진입점 (결함1·2)
    "issue_from_exception",
    "classify_failure_issue",
    "INFRA_KIND",
    "action_module",
]
