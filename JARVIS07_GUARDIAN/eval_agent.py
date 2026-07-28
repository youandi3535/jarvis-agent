"""JARVIS07 Eval Agent — 수정 결과 평가 + learned_patterns 등록 게이트.

A모델 분리 (ADR 007 — Self-Evolving Harness 비전, docs/decisions/007):
  - auto_repair / pattern_fixer 는 *진단·수정* 만 수행
  - eval_agent 는 *수정 결과 평가 + 학습 자산화 결정* 만 수행
  - auditor 는 *헌법 위반·드리프트 검출 + Refine Rules 제안* 만 수행

# 책임 경계 (단일 진입점)

evaluate(error_record, fixer_name, patch=..., target_file=..., verification=...) → EvalResult
should_register(error_record, fixer_name, ...) → bool  (간편 진입점)
record_fix_failure(error_record, fixer_name=..., ...) → dict  (실패 반영 = 강등)
pattern_health(error_record | fingerprint=...) → dict  (eval_meta·fail_count 조회)
prune_quarantined(dry_run=...) → dict  (격리 패턴 정리)

* learned_patterns 에 *어떤 수정을 학습 자산화할지* 결정하는 *유일한 게이트*.
* `pattern_fixer.record_pattern_hit()` 가 본 모듈의 게이트를 통과한 후만 등록.

# ★ 결함 1 정정 (2026-07-25) — fixer 집합은 pattern_fixer 에서 *런타임 파생*

종전 `STATIC_FIXERS` 는 손으로 나열한 리터럴 5종이었고, 주석은 "pattern_fixer 의
fixer 목록과 동기" 라고 적혀 있었으나 **사실이 아니었다**.
실측: `STATIC_FIXERS ∩ pattern_fixer._FIXER_REGISTRY = ∅` (공집합).
→ 정적 fixer 전종이 "unknown → 보수적 통과 70점" 으로 떨어져 게이트가 *도장 찍기* 였다.
이는 드리프트가 아니라 *처음부터 두 곳에 나열한* ① 단일 진입점 위반.
→ 이제 `fixer_sets()` 가 매 호출 `pattern_fixer` 를 조회해 세 집합을 파생한다 (② 동적 설계).

    static = _STATIC_FIXERS_CORE 의 이름들          (결정적 AST 패치 — 자동 통과 자격)
    replay = _FIXER_REGISTRY - static               (저장 diff 재적용 — 내용 출처는 LLM)
    llm    = _ACTIONABLE_FIXERS - _FIXER_REGISTRY   (LLM 패치)

세 집합 모두 *pattern_fixer 가 실제로 쓰는 자료구조* 에서 파생 — 이 파일에 fixer 이름 리터럴 0.

# ★ 결함 2 정정 — 외생적(non-LLM) 검증 신호를 게이트에 결합

LLM 이 자기 수정을 자기가 채점하면 rubber-stamp 체제(수락↑·정확도↓)로 퇴화한다.
→ `verification` (error_fixer 가 생산하는 *재현 시도 결과*) 를 판정에 섞는다:
    "still_reproduces" → LLM 점수 무관 *즉시 거부* (외생 신호가 LLM 을 이긴다. LLM 호출도 생략)
    "unverifiable"     → 통과 문턱 상향 (llm 80→90, static 95→85점) · 신호 0 이면 거부
    "reproduced_gone"  → 정상 평가 (+ 격리 해제 자격)
    None/키 없음       → **종전 동작 그대로** (error_fixer 동시 수정 중 — 방어적 degrade)

# ★ 결함 3 정정 — 실패를 반영하는 경로 (감쇠·강등·격리)

`hit_count` 는 오르기만 하고 롤백돼도 줄지 않았다. `eval_meta` 는 쓰기만 하고 읽는 코드가 0.
→ `record_fix_failure()` : fail_count++ · hit_count-- · eval_meta.score 감쇠 ·
  임계 도달 시 `patterns` → `quarantined` 로 격리 (pattern_fixer 는 `patterns` 만 순회하므로
  *pattern_fixer 수정 없이* 즉시 무력화된다).
→ `evaluate()` 는 `pattern_health()` 로 **eval_meta 를 읽어** 판정에 쓴다 (감쇠·격리 반영).

# ★ P2 정정 (2026-07-25) — "LLM 판정 불가" 와 "검증 불가" 는 다른 것이다

`learn_eval` 은 `background=True` alias 라 **발행창 + 발행 前 보호구간(90분)** 동안
`shared.llm` 이 모델을 호출하지 않고 `("", False)` 를 즉시 반환한다. 종전 코드는 그것을
"LLM 신호 0" 으로 보고 `unverifiable` 과 곱해 `should_register=False, score=0` 으로
**학습을 전량 폐기** 했다 — 로그도 알림도 없이. `verify_fix` 는 결정론적 6종 밖이면 전부
`unverifiable` 이므로 "흔한 오류 × 발행창" 이 곧 전량 폐기였다.

→ 판정 불가는 *증거가 없는 것* 이지 *나쁜 수정이라는 증거* 가 아니다:
    발행창 보류(LLM 미호출) → 종전의 **보수적 통과 70 으로 degrade** (`_hold_degraded`)
    LLM 호출 실패·파싱 실패 → 종전대로 거부 (rubber-stamp 방지) — 단 **WARNING 로그 필수**
  외생 *부정* 신호(still_reproduces·격리)는 그 앞 게이트에서 이미 걸러지므로 완화 대상 아님.
  모든 강등·폐기는 `eval_signal_stats()` 카운터 + `eval_meta.{llm_judged,degraded,hold_reason}`
  로 관측된다 (침묵 금지).

# 킬스위치

GUARDIAN_EVAL_EXOGENOUS=0        → 결함 2·3 로직 전부 무효 (종전 동작)
GUARDIAN_EVAL_STATIC_DERIVE=0    → 결함 1 파생 무효 (레거시 리터럴로 롤백)
GUARDIAN_EVAL_QUARANTINE_FAILS=N → 격리 임계 (기본 3)
GUARDIAN_EVAL_HOLD_DEGRADE=0     → ★ P2 발행창 degrade 무효 (종전 = 폐기)
GUARDIAN_EVAL_LLM_FAIL_PASS=1    → LLM 호출·파싱 실패까지 보수적 통과로 완화 (기본 off)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("jarvis.guardian.eval")

# ──────────────────────────────────────────────────────────────
# 계약 상수 — error_fixer 가 생산, eval_agent·bandit 이 소비
# ──────────────────────────────────────────────────────────────
V_GONE = "reproduced_gone"      # 재현 시도했고 더는 재현되지 않음 (진짜 검증됨)
V_UNVERIFIABLE = "unverifiable"  # 재현 시도 자체가 불가능한 유형 (양의 보상 금지)
V_STILL = "still_reproduces"    # 재현됨 = 수정 실패
VERIFICATION_VALUES = (V_GONE, V_UNVERIFIABLE, V_STILL)

# 통과 점수 임계값 (종전 유지 — 외부 참조 있음)
SCORE_PASS = 80
# 외생 검증 불가 시 상향 문턱 (LLM 패치)
SCORE_PASS_UNVERIFIED = 90

_SCORE_STATIC = 95            # 정적 fixer 자동 통과
_SCORE_STATIC_UNVERIFIED = 85  # 정적 fixer + 외생 검증 불가
_SCORE_REPLAY_VERIFIED = 90   # 재적용 fixer + 외생 검증 통과
_SCORE_CONSERVATIVE = 70      # 보수적 통과 (종전 unknown 경로와 동일)

_FAIL_DECAY = 15              # 실패 1회당 eval_meta.score 감쇠폭

# ★ 레거시 리터럴 — GUARDIAN_EVAL_STATIC_DERIVE=0 롤백 전용.
#   실측 2026-07-25: pattern_fixer._FIXER_REGISTRY 와 교집합 ∅ (= 전종 unknown 70점).
#   정상 경로에서는 절대 사용하지 않는다.
_LEGACY_STATIC_FIXERS: tuple[str, ...] = (
    "relative_import_fix",
    "nonetype_subscript_safe",
    "nametype_error_typo_fix",
    "nonetype_attribute_safe",
    "import_error_alias_fix",
)
# ★ 레거시 LLM fixer 이름 — 파생 실패 시에만 쓰는 최소 폴백.
#   (파생 실패 시 static 은 빈 집합으로 두어 *자동 통과 특권만* 잃게 한다 = fail-closed on privilege.
#    llm 은 유지해야 LLM 채점 경로가 살아남는다 = 더 엄격한 쪽으로 degrade.)
_LEGACY_LLM_FIXERS: tuple[str, ...] = ("llm_patch",)


def _flag(name: str, default: str = "1") -> bool:
    """환경변수 킬스위치 — *호출 시점* 조회 (import 시 스냅샷 금지)."""
    return os.environ.get(name, default) != "0"


# ★ 평가 LLM alias — 두 곳(보류 판정·실제 호출)이 같은 이름을 봐야 한다 (① 단일 진입점)
_EVAL_ALIAS = "learn_eval"

# ★ P2 관측 카운터 (2026-07-25) — "조용한 폐기" 금지. 로그 + 이 카운터로 항상 드러난다.
#   프로세스 메모리 카운터라 재시작에 사라지지만, *영구* 기록은 learned_patterns 의
#   `eval_meta.degraded / llm_judged / hold_reason` 로 남는다 (to_meta 가 그대로 실어보낸다).
_SIGNAL_STATS: dict[str, int] = {
    "llm_judged": 0,        # LLM 이 실제로 판정한 횟수
    "hold_degrade": 0,      # 발행창 보류 → 보수적 통과로 degrade
    "no_signal_reject": 0,  # LLM 판정 불가 + 외생 검증 불가 → 학습 폐기
    "exogenous_pass": 0,    # LLM 없이 외생 검증(reproduced_gone) 만으로 통과
}


def eval_signal_stats() -> dict[str, int]:
    """P2 관측 — 이번 프로세스에서 판정 신호가 어떻게 처리됐는가."""
    return dict(_SIGNAL_STATS)


def _llm_hold_reason() -> str:
    """지금 평가 LLM 이 *발행창 보호* 로 보류되는가 — 보류 사유 문자열 (없으면 "").

    ★ P2 (사용자 지적 2026-07-25): `learn_eval` 은 `background=True` alias 라
      발행창 + 발행 前 보호구간(90분) 동안 `shared.llm` 이 모델을 **아예 호출하지 않고**
      `("", False)` 를 즉시 반환한다. 그것은 *나쁜 수정이라는 증거* 가 아니라
      **증거가 없는 상태** 다. 종전 코드는 이 둘을 구분하지 못해
      "흔한 오류(=unverifiable) × 발행창" 조합에서 학습을 전량 폐기했다 — 그것도 침묵으로.
      → 판정은 `shared.llm.defer_reason()` *단일 소스* 에서 파생한다 (사본 금지).
    """
    try:
        from shared import llm as _llm  # type: ignore
        fn = getattr(_llm, "defer_reason", None)
        if callable(fn):
            return str(fn(_EVAL_ALIAS) or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _hold_degrade_enabled() -> bool:
    """P2 전용 킬스위치 — 발행창 degrade 를 끄면 종전(전량 폐기) 동작으로 회귀.

    ★ 외생 로직 전체(`GUARDIAN_EVAL_EXOGENOUS=0`)를 끄지 않고 *이 동작만* 되돌릴 수 있게
      전용 스위치를 둔다 (종전엔 거친 스위치 하나뿐이었다).
    """
    return _flag("GUARDIAN_EVAL_HOLD_DEGRADE")


def _quarantine_fails() -> int:
    try:
        return max(1, int(os.environ.get("GUARDIAN_EVAL_QUARANTINE_FAILS", "3")))
    except Exception:
        return 3


# ──────────────────────────────────────────────────────────────
# 결함 1 — fixer 집합 런타임 파생 (② 동적 설계)
# ──────────────────────────────────────────────────────────────

@dataclass
class FixerSets:
    static: tuple[str, ...]
    replay: tuple[str, ...]
    llm: tuple[str, ...]
    derived: bool
    source: str


def _pattern_fixer_mod():
    """살아있는 pattern_fixer 모듈. 미로드 시 import (실패 시 None)."""
    mod = sys.modules.get("JARVIS07_GUARDIAN.pattern_fixer")
    if mod is not None:
        return mod
    try:
        from JARVIS07_GUARDIAN import pattern_fixer as mod  # type: ignore
        return mod
    except Exception as e:  # noqa: BLE001
        log.warning("[GUARDIAN/eval] pattern_fixer 로드 실패 — fixer 집합 파생 불가: %s", e)
        return None


def fixer_sets() -> FixerSets:
    """pattern_fixer 의 실제 자료구조에서 fixer 3집합을 *매 호출* 파생.

    캐시 0 — pattern_fixer 에 fixer 가 추가되면 다음 호출에서 자동 반영 (복사본 금지 원칙).

    파생 실패 시 정책 = **fail-closed on privilege / fail-open on flow**
      · static = () : 아무도 "자동 통과 95점" 특권을 못 받는다 (보수적)
      · llm    = 레거시 1종 : LLM 채점 경로는 살려둔다 (더 엄격한 쪽)
      · 흐름 자체는 막지 않는다 — eval_agent 는 *학습 등록* 게이트이지 발행 게이트가 아니다.
        여기서 hard block 하면 학습 루프만 조용히 멈추고 사고는 안 막는다.
    """
    if not _flag("GUARDIAN_EVAL_STATIC_DERIVE"):
        return FixerSets(
            static=_LEGACY_STATIC_FIXERS, replay=(), llm=_LEGACY_LLM_FIXERS,
            derived=False, source="killswitch:GUARDIAN_EVAL_STATIC_DERIVE=0",
        )

    pf = _pattern_fixer_mod()
    if pf is None:
        return FixerSets(static=(), replay=(), llm=_LEGACY_LLM_FIXERS,
                         derived=False, source="degraded:pattern_fixer-unavailable")

    try:
        registry = dict(getattr(pf, "_FIXER_REGISTRY", {}) or {})
        core = [n for n, _fn in (getattr(pf, "_STATIC_FIXERS_CORE", []) or [])]
        actionable = set(getattr(pf, "_ACTIONABLE_FIXERS", set()) or set())

        if not registry:
            raise ValueError("_FIXER_REGISTRY 비어 있음")

        static = tuple(sorted(n for n in core if n in registry)) or tuple(sorted(registry))
        replay = tuple(sorted(set(registry) - set(static)))
        llm = tuple(sorted(actionable - set(registry))) or _LEGACY_LLM_FIXERS
        return FixerSets(static=static, replay=replay, llm=llm,
                         derived=True, source="pattern_fixer")
    except Exception as e:  # noqa: BLE001
        log.warning("[GUARDIAN/eval] fixer 집합 파생 실패 → 특권 회수(static=∅): %s", e)
        return FixerSets(static=(), replay=(), llm=_LEGACY_LLM_FIXERS,
                         derived=False, source=f"degraded:{type(e).__name__}")


# ──────────────────────────────────────────────────────────────
# 평가 결과
# ──────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    """수정 결과 평가."""
    should_register: bool
    score: int                  # 0-100
    safe: bool                  # 같은 위치 추가 오류 가능성 없는가
    accurate: bool              # 근본 원인 해결인가 (단순 증상 가림 아닌가)
    reusable: bool              # 다른 위치 동일 패턴 재사용 가치
    rationale: str
    tier: str                   # "static" | "replay" | "llm" | "unknown"
    evaluated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    # ★ 외생 검증 (결함 2) — 비-LLM 신호
    verification: str = ""      # reproduced_gone | unverifiable | still_reproduces | ""
    exogenous: bool = False     # 외생 신호가 판정에 실제로 참여했는가
    # ★ 학습 자산 건강도 (결함 3) — eval_meta 를 *읽어* 반영한 결과
    fail_count: int = 0
    quarantined: bool = False
    # ★ P2 관측 (2026-07-25) — 판정 신호의 출처를 *영구 기록* 에 남긴다 (침묵 금지)
    llm_judged: bool = True     # LLM 이 실제로 판정했는가 (False = 증거 없음)
    degraded: bool = False      # 보수적 통과로 강등됐는가 (발행창 보류 등)
    hold_reason: str = ""       # 보류 사유 (shared.llm.defer_reason 파생)


def to_meta(result: EvalResult) -> dict[str, Any]:
    """learned_patterns 의 `eval_meta` 필드용 dict 변환."""
    return asdict(result)


# ──────────────────────────────────────────────────────────────
# 결함 3 — 학습 자산 건강도 (eval_meta 를 읽는 유일한 경로)
# ──────────────────────────────────────────────────────────────

def _fingerprint(error_record: dict) -> str:
    pf = _pattern_fixer_mod()
    if pf is None:
        return ""
    try:
        return pf._make_fingerprint(
            error_record.get("error_type", "") or "",
            error_record.get("message", "") or "",
        )
    except Exception:  # noqa: BLE001
        return ""


def _load_learned_safe(pf) -> dict | None:
    """learned_patterns 를 pattern_fixer 의 *자기 lock·자기 로더* 로 읽는다.

    ★ 저장소가 하나면 접근 함수도 하나여야 한다 (① 단일 진입점) — 여기서 json 을
      직접 열지 않는다. lock 은 timeout 으로 잡아 데드락 가능성 0.
    """
    loader = getattr(pf, "_load_learned", None)
    if loader is None:
        return None
    lock = getattr(pf, "_LEARNED_LOCK", None)
    if lock is None:
        return loader()
    got = lock.acquire(timeout=2.0)
    try:
        return loader()
    finally:
        if got:
            lock.release()


def pattern_health(error_record: dict | None = None, fingerprint: str = "") -> dict:
    """학습 패턴의 건강도 — `eval_meta` + `fail_count` 를 *읽어* 반환.

    쓰기만 하고 읽지 않는 데이터는 죽은 데이터다. evaluate() 가 이 값을 판정에 쓴다.
    """
    out = {
        "found": False, "fingerprint": fingerprint, "hit_count": 0, "fail_count": 0,
        "last_score": None, "last_tier": "", "quarantined": False, "trust": 1.0,
    }
    fp = fingerprint or (_fingerprint(error_record or {}) if error_record else "")
    out["fingerprint"] = fp
    if not fp:
        return out

    pf = _pattern_fixer_mod()
    if pf is None:
        return out
    try:
        data = _load_learned_safe(pf)
        if not data:
            return out
        for bucket, quarantined in (("patterns", False), ("quarantined", True)):
            for p in data.get(bucket, []) or []:
                if p.get("fingerprint") != fp:
                    continue
                meta = p.get("eval_meta") or {}
                hc = int(p.get("hit_count", 0) or 0)
                fc = int(p.get("fail_count", 0) or 0)
                out.update({
                    "found": True,
                    "hit_count": hc,
                    "fail_count": fc,
                    "last_score": meta.get("score"),
                    "last_tier": meta.get("tier", ""),
                    "quarantined": quarantined or bool(p.get("quarantined")),
                    "trust": round(max(0.0, (hc + 1) / (hc + 1 + fc * 2)), 3),
                })
                return out
    except Exception as e:  # noqa: BLE001
        log.debug("[GUARDIAN/eval] pattern_health 조회 실패: %s", e)
    return out


def record_fix_failure(
    error_record: dict,
    fixer_name: str = "",
    reason: str = "",
    verification: str = "",
    fingerprint: str = "",
) -> dict:
    """★ 실패를 학습 자산에 반영 — 감쇠·강등·격리 (결함 3).

    호출 시점: 적용한 수정이 롤백됐거나 `verification == "still_reproduces"` 일 때.

    동작
      1. `fail_count` += 1
      2. `hit_count` -= 1 (하한 0)   — 오르기만 하던 카운터에 내려가는 길
      3. `eval_meta.score` -= _FAIL_DECAY, `eval_meta.should_register` = False
      4. fail_count ≥ 임계(기본 3) → `patterns` 에서 빼서 `quarantined` 로 이동
         (pattern_fixer 의 `_fix_from_learned` 는 `patterns` 만 순회 → 즉시 무력화.
          pattern_fixer 를 고치지 않고 얻는 유일한 실효 강등 수단. 이력은 보존 = 복구 가능)

    Returns: {"ok", "action", "fail_count", "hit_count", "quarantined", "fingerprint"}
    """
    res = {"ok": False, "action": "noop", "fail_count": 0, "hit_count": 0,
           "quarantined": False, "fingerprint": fingerprint or ""}

    if not _flag("GUARDIAN_EVAL_EXOGENOUS"):
        res["action"] = "killswitch"
        return res

    pf = _pattern_fixer_mod()
    if pf is None:
        res["action"] = "pattern_fixer-unavailable"
        return res

    fp = fingerprint or _fingerprint(error_record or {})
    res["fingerprint"] = fp
    if not fp:
        res["action"] = "no-fingerprint"
        return res

    loader = getattr(pf, "_load_learned", None)
    saver = getattr(pf, "_save_learned", None)
    lock = getattr(pf, "_LEARNED_LOCK", None)
    if loader is None or saver is None:
        res["action"] = "no-store-api"
        return res

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    threshold = _quarantine_fails()
    # ★ ERRORS [497] — read-modify-write 를 **교차 프로세스** 락으로 감싼다.
    #   종전엔 `got=False`(타임아웃)여도 그대로 RMW 를 강행해 *lost update* 가 났다.
    #   게다가 `threading.Lock` 은 같은 프로세스만 방어하는데, 테마가 subprocess 로
    #   바뀐 뒤(c9c7c2b) 교차 프로세스에서는 `got` 이 **항상 True** 라 아무것도 못 막았다.
    #   json_store.locked() 는 flock 기반이고 재진입 가능하다(내부 write_json 과 중첩 안전).
    from JARVIS07_GUARDIAN.json_store import locked as _xp_locked  # noqa: PLC0415
    _lpath = getattr(pf, "_LEARNED_PATH", None)
    got = lock.acquire(timeout=5.0) if lock is not None else True
    _xp = _xp_locked(_lpath) if _lpath is not None else None
    if _xp is not None:
        _xp.__enter__()
    try:
        data = loader() or {}
        target = None
        for p in data.get("patterns", []) or []:
            if p.get("fingerprint") == fp:
                target = p
                break
        if target is None:
            res["action"] = "not-found"
            return res

        fc = int(target.get("fail_count", 0) or 0) + 1
        hc = max(0, int(target.get("hit_count", 0) or 0) - 1)
        target["fail_count"] = fc
        target["hit_count"] = hc
        target["last_failed"] = now
        hist = target.setdefault("failures", [])
        hist.append({"ts": now, "reason": (reason or "")[:200],
                     "verification": verification, "fixer": fixer_name})
        if len(hist) > 10:
            hist[:] = hist[-10:]

        meta = target.get("eval_meta")
        if isinstance(meta, dict):
            try:
                meta["score"] = max(0, int(meta.get("score", 0) or 0) - _FAIL_DECAY)
            except Exception:  # noqa: BLE001
                meta["score"] = 0
            meta["should_register"] = False
            meta["demoted_at"] = now
            meta["fail_count"] = fc

        res.update({"ok": True, "fail_count": fc, "hit_count": hc, "action": "demoted"})

        if fc >= threshold:
            target["quarantined"] = True
            target["quarantined_at"] = now
            data["patterns"] = [p for p in data.get("patterns", []) or []
                                if p.get("fingerprint") != fp]
            data.setdefault("quarantined", []).append(target)
            res["quarantined"] = True
            res["action"] = "quarantined"
            log.warning("[GUARDIAN/eval] ★ 패턴 격리 — fp='%s' fail_count=%d (≥%d) reason=%s",
                        fp[:60], fc, threshold, (reason or "")[:80])
        else:
            log.info("[GUARDIAN/eval] 패턴 강등 — fp='%s' fail=%d hit=%d", fp[:60], fc, hc)

        saver(data)
    except Exception as e:  # noqa: BLE001
        log.warning("[GUARDIAN/eval] record_fix_failure 실패: %s", e)
        res["action"] = f"error:{type(e).__name__}"
    finally:
        if lock is not None and got:
            lock.release()
        if _xp is not None:                       # ★ 교차 프로세스 락 해제 (ERRORS [497])
            try:
                _xp.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
    return res


def prune_quarantined(dry_run: bool = True, keep_days: int = 30) -> dict:
    """격리된 패턴 정리 — 기본 dry-run (라이브 안전).

    격리 자체로 이미 무력화되어 있으므로 삭제는 *디스크 위생* 목적. 기본은 보고만.
    """
    pf = _pattern_fixer_mod()
    if pf is None:
        return {"ok": False, "reason": "pattern_fixer-unavailable"}
    loader, saver = getattr(pf, "_load_learned", None), getattr(pf, "_save_learned", None)
    if loader is None or saver is None:
        return {"ok": False, "reason": "no-store-api"}
    lock = getattr(pf, "_LEARNED_LOCK", None)
    # ★ ERRORS [497] — read-modify-write 를 **교차 프로세스** 락으로 감싼다.
    #   종전엔 `got=False`(타임아웃)여도 그대로 RMW 를 강행해 *lost update* 가 났다.
    #   게다가 `threading.Lock` 은 같은 프로세스만 방어하는데, 테마가 subprocess 로
    #   바뀐 뒤(c9c7c2b) 교차 프로세스에서는 `got` 이 **항상 True** 라 아무것도 못 막았다.
    #   json_store.locked() 는 flock 기반이고 재진입 가능하다(내부 write_json 과 중첩 안전).
    from JARVIS07_GUARDIAN.json_store import locked as _xp_locked  # noqa: PLC0415
    _lpath = getattr(pf, "_LEARNED_PATH", None)
    got = lock.acquire(timeout=5.0) if lock is not None else True
    _xp = _xp_locked(_lpath) if _lpath is not None else None
    if _xp is not None:
        _xp.__enter__()
    try:
        data = loader() or {}
        q = data.get("quarantined", []) or []
        cutoff = datetime.now().timestamp() - keep_days * 86400
        keep, drop = [], []
        for p in q:
            ts = p.get("quarantined_at", "")
            try:
                old = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").timestamp() < cutoff
            except Exception:  # noqa: BLE001
                old = False
            (drop if old else keep).append(p)
        if not dry_run and drop:
            data["quarantined"] = keep
            saver(data)
        return {"ok": True, "quarantined": len(q), "pruned": len(drop),
                "dry_run": dry_run}
    finally:
        if lock is not None and got:
            lock.release()
        if _xp is not None:                       # ★ 교차 프로세스 락 해제 (ERRORS [497])
            try:
                _xp.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


# ──────────────────────────────────────────────────────────────
# 공용 진입점
# ──────────────────────────────────────────────────────────────

def _resolve_verification(
    verification: str | None,
    fix_result: dict | None,
    error_record: dict,
) -> str:
    """외생 검증 신호 해석 — 없으면 "" (= 종전 동작으로 안전 degrade).

    ★ error_fixer.py 는 다른 에이전트가 동시 수정 중 — `verification` 키가 아직 없을 수 있다.
      키 부재 / 미지의 값 은 *신호 없음* 으로 취급하고 절대 추측하지 않는다.
    ★ apply_fix 반환값은 dict 가 아니라 `FixResult(int)` 처럼 *`.get()` 만 가진* 객체일 수 있다
      → isinstance(dict) 로 보지 말고 오리처럼 걷는지(.get / 속성) 로 볼 것.
    """
    def _pluck(obj) -> Any:
        if obj is None:
            return None
        getter = getattr(obj, "get", None)
        if callable(getter):
            try:
                return getter("verification")
            except Exception:  # noqa: BLE001
                return None
        return getattr(obj, "verification", None)

    for cand in (verification, _pluck(fix_result), _pluck(error_record)):
        if isinstance(cand, str) and cand.strip() in VERIFICATION_VALUES:
            return cand.strip()
    return ""


def evaluate(
    error_record: dict,
    fixer_name: str,
    patch: str = "",
    target_file: str = "",
    verification: str | None = None,
    fix_result: dict | None = None,
) -> EvalResult:
    """수정 결과 평가. fixer_name 에 따라 Tier 분기 + 외생 검증·건강도 결합.

    하위호환: verification / fix_result 미지정 시 종전 동작 그대로.
    """
    fn = (fixer_name or "").strip()
    sets = fixer_sets()
    exo_on = _flag("GUARDIAN_EVAL_EXOGENOUS")
    verif = _resolve_verification(verification, fix_result, error_record) if exo_on else ""
    health = pattern_health(error_record) if exo_on else {}
    fail_count = int(health.get("fail_count", 0) or 0)
    was_quarantined = bool(health.get("quarantined"))

    def _mk(should: bool, score: int, tier: str, rationale: str,
            safe: bool = True, accurate: bool = True, reusable: bool = True) -> EvalResult:
        return EvalResult(
            should_register=should, score=max(0, min(100, score)),
            safe=safe, accurate=accurate, reusable=reusable,
            rationale=rationale, tier=tier,
            verification=verif, exogenous=bool(verif) or was_quarantined,
            fail_count=fail_count, quarantined=was_quarantined,
        )

    # ── 게이트 0 (외생) — 재현되면 수정 실패. LLM 점수와 무관하게 즉시 거부 ──
    #    LLM 호출조차 하지 않는다 (비용 0 + rubber-stamp 여지 0).
    if verif == V_STILL:
        log.info("[GUARDIAN/eval] 외생 거부 — verification=still_reproduces (fixer=%s)", fn or "?")
        return _mk(False, 0, _tier_of(fn, sets),
                   "외생 검증: 원 오류가 여전히 재현됨 — 수정 실패, LLM 점수 무관 거부",
                   safe=False, accurate=False, reusable=False)

    # ── 게이트 1 (건강도) — 격리된 패턴은 실검증 없이 부활 금지 ──
    if was_quarantined and verif != V_GONE:
        return _mk(False, 0, _tier_of(fn, sets),
                   f"격리 패턴 (fail_count={fail_count}) — reproduced_gone 없이 재등록 거부",
                   safe=False, accurate=False, reusable=False)

    # ── Tier A — 정적 fixer (결정적 패턴) ──
    if fn and fn in sets.static:
        base = _SCORE_STATIC if verif != V_UNVERIFIABLE else _SCORE_STATIC_UNVERIFIED
        score = base - _FAIL_DECAY * fail_count
        ok = score >= _SCORE_CONSERVATIVE
        why = f"정적 fixer ({fn}) — 결정적 패턴, 자동 통과"
        if verif:
            why += f" · 외생={verif}"
        if fail_count:
            why += f" · 과거 실패 {fail_count}회 감쇠"
        if not ok:
            why += " → 누적 실패로 등록 거부"
        return _mk(ok, score, "static", why)

    # ── Tier A' — 재적용(replay) fixer: 기전은 결정적이나 *내용 출처는 LLM* ──
    if fn and fn in sets.replay:
        if verif == V_GONE:
            score = _SCORE_REPLAY_VERIFIED - _FAIL_DECAY * fail_count
            why = f"재적용 fixer ({fn}) — 외생 검증(reproduced_gone) 통과"
        else:
            score = _SCORE_CONSERVATIVE - _FAIL_DECAY * fail_count
            why = (f"재적용 fixer ({fn}) — 외생 검증 {verif or '없음'}, 보수적 통과")
        ok = score >= _SCORE_CONSERVATIVE
        if not ok:
            why += " → 누적 실패로 등록 거부"
        return _mk(ok, score, "replay", why)

    # ── Tier B — LLM 패치 (정밀 평가) ──
    if fn and fn in sets.llm:
        return _evaluate_llm_patch(
            error_record, patch, target_file,
            verif=verif, fail_count=fail_count, quarantined=was_quarantined,
        )

    # ── Tier 외 — 알 수 없는 fixer (보수적 통과 + 낮은 점수) ──
    score = _SCORE_CONSERVATIVE - _FAIL_DECAY * fail_count
    ok = score >= _SCORE_CONSERVATIVE
    note = "" if sets.derived else f" [fixer 집합 파생 실패: {sets.source}]"
    return _mk(ok, score, "unknown",
               f"알 수 없는 fixer ({fn or 'unknown'}) — 보수적 통과, 점수 {score}{note}")


def _tier_of(fn: str, sets: FixerSets) -> str:
    if fn in sets.static:
        return "static"
    if fn in sets.replay:
        return "replay"
    if fn in sets.llm:
        return "llm"
    return "unknown"


def should_register(
    error_record: dict,
    fixer_name: str,
    patch: str = "",
    target_file: str = "",
    verification: str | None = None,
    fix_result: dict | None = None,
) -> bool:
    """간편 진입점 — evaluate() 의 bool 축약 (docstring·__all__ 이 약속하던 함수)."""
    return evaluate(error_record, fixer_name, patch=patch, target_file=target_file,
                    verification=verification, fix_result=fix_result).should_register


# ──────────────────────────────────────────────────────────────
# Tier B — LLM 평가
# ──────────────────────────────────────────────────────────────

_EVAL_PROMPT_TEMPLATE = """당신은 JARVIS 자가 학습 시스템의 평가 에이전트입니다.
아래 자동 수정 결과를 *학습 자산화* 해도 될지 평가하세요.

# 오류
error_type: {error_type}
message: {message}
traceback (요약):
{traceback}

# 적용 패치 (target_file: {target_file})
```
{patch}
```

# 외부 재현 검증 결과 (비-LLM 신호)
{verif_note}

# 평가 항목 — 각 0/1
1. safe: 같은 위치에 추가 오류 발생 가능성이 없는가?
2. accurate: 근본 원인 해결인가? (단순 증상 가림 아닌가?)
3. reusable: 다른 위치에서 동일 패턴 발생 시 재사용 가치가 있는가?

# 출력 형식 (JSON 한 줄)
{{"safe": 1, "accurate": 1, "reusable": 1, "score": 88, "rationale": "..."}}

score 는 0~100. {pass_score}+ 가 통과. 3축 모두 1 이 아니면 {pass_score} 미만 점수.
rationale 은 50자 이내 한국어로.
"""

_VERIF_NOTE = {
    V_GONE: "재현 시도 결과 원 오류가 더 이상 재현되지 않음 (외부 검증 통과).",
    V_UNVERIFIABLE: "재현 시도 자체가 불가능한 유형 — 외부 검증 없음. 더 보수적으로 채점하라.",
    "": "외부 재현 검증 정보 없음.",
}


def _evaluate_llm_patch(
    error_record: dict,
    patch: str,
    target_file: str,
    verif: str = "",
    fail_count: int = 0,
    quarantined: bool = False,
) -> EvalResult:
    """LLM 패치 평가 — Sonnet 5 (learn_eval alias).

    ★ LLM 은 *혼자서* 통과를 결정하지 못한다:
      - still_reproduces 는 evaluate() 에서 이미 차단됨 (여기 도달 안 함)
      - unverifiable 이면 문턱 상향 (80 → 90)
      - unverifiable + LLM 판정 불가 = 신호 0 → 거부 (종전엔 무조건 보수적 통과였다)
    """
    def _mk(should, score, rationale, safe=True, accurate=True, reusable=True) -> EvalResult:
        return EvalResult(
            should_register=should, score=max(0, min(100, score)),
            safe=safe, accurate=accurate, reusable=reusable,
            rationale=rationale, tier="llm",
            verification=verif, exogenous=bool(verif) or quarantined,
            fail_count=fail_count, quarantined=quarantined,
        )

    if not patch:
        # ★ 하드닝 (2026-07-02): patch 없는 llm_patch 는 stored_patch 부재 → 재적용 불가
        #   (비actionable). 학습 자산화 거부 → junk 패턴 등록·밴딧 헛보상 차단.
        return _mk(False, 0, "llm_patch 이지만 patch 본문 없음 — 비actionable, 학습 거부",
                   safe=True, accurate=False, reusable=False)

    # ── ★ P2 — LLM 을 호출하기 *전에* 보류 여부를 묻는다 ─────────────────
    #   발행창·보호구간에서는 `shared.llm` 이 모델을 호출하지 않고 즉시 반환한다.
    #   그 경우는 "판정 불가(증거 없음)" 이지 "나쁜 수정(증거 있음)" 이 아니다.
    _hold = _llm_hold_reason()
    if _hold:
        return _hold_degraded(_hold, verif, fail_count, quarantined)

    pass_score = SCORE_PASS_UNVERIFIED if verif == V_UNVERIFIABLE else SCORE_PASS

    et = error_record.get("error_type", "")
    msg = (error_record.get("message", "") or "")[:200]
    tb = (error_record.get("traceback", "") or "")[:400]
    patch_view = patch[:2000]

    prompt = _EVAL_PROMPT_TEMPLATE.format(
        error_type=et, message=msg, traceback=tb,
        target_file=target_file or "?",
        patch=patch_view,
        verif_note=_VERIF_NOTE.get(verif, _VERIF_NOTE[""]),
        pass_score=pass_score,
    )

    judged = True   # LLM 이 *판정을 했는가* (빈 응답·호출 실패와 구분)
    try:
        from shared import llm as _llm  # type: ignore
        _result_fn = getattr(_llm, "invoke_text_result", None)
        if callable(_result_fn):
            raw, judged = _result_fn(_EVAL_ALIAS, prompt, max_tokens=300)
        else:
            raw = _llm.invoke_text(_EVAL_ALIAS, prompt, max_tokens=300)
    except Exception as e:
        log.warning("[GUARDIAN/eval] LLM 호출 실패: %s", e)
        return _no_llm_signal("LLM 호출 실패", e, verif, fail_count, quarantined)

    if not judged:
        # 호출 직전엔 열려 있었으나 그 사이 발행창이 열렸을 수 있다 — 다시 물어본다.
        _hold2 = _llm_hold_reason()
        if _hold2:
            return _hold_degraded(_hold2, verif, fail_count, quarantined)
        return _no_llm_signal("LLM 판정 불가 (ok=False)", (raw or "")[:120],
                              verif, fail_count, quarantined)

    parsed = _parse_eval_response(raw or "")
    if parsed is None:
        return _no_llm_signal("LLM 응답 파싱 실패", (raw or "")[:200],
                              verif, fail_count, quarantined)

    _SIGNAL_STATS["llm_judged"] += 1
    safe = bool(parsed.get("safe", 0))
    accurate = bool(parsed.get("accurate", 0))
    reusable = bool(parsed.get("reusable", 0))
    score = int(parsed.get("score", 0)) - _FAIL_DECAY * fail_count
    rationale = str(parsed.get("rationale", ""))[:200]

    should_register_ = score >= pass_score and safe and accurate

    extra = []
    if verif:
        extra.append(f"외생={verif}")
    if pass_score != SCORE_PASS:
        extra.append(f"문턱↑{pass_score}")
    if fail_count:
        extra.append(f"과거실패{fail_count}회 감쇠")
    suffix = (" · " + " · ".join(extra)) if extra else ""

    result = EvalResult(
        should_register=should_register_,
        score=max(0, min(100, score)),
        safe=safe, accurate=accurate, reusable=reusable,
        rationale=(rationale or f"LLM 평가 — score={score}") + suffix,
        tier="llm",
        verification=verif, exogenous=bool(verif) or quarantined,
        fail_count=fail_count, quarantined=quarantined,
    )

    if not should_register_:
        log.info(
            "[GUARDIAN/eval] 학습 자산화 거부 — score=%d(문턱 %d) safe=%s acc=%s: %s",
            score, pass_score, safe, accurate, rationale,
        )
        _notify_rejection(error_record, target_file, result)

    return result


def _parse_eval_response(raw: str) -> dict[str, Any] | None:
    """LLM 응답에서 JSON 1줄 추출. 실패 시 None."""
    if not raw:
        return None
    # ```json ... ``` 블록 우선 매칭
    m = re.search(r"\{[^{}]*\"safe\"[^{}]*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _hold_degraded(hold: str, verif: str, fail_count: int,
                   quarantined: bool) -> EvalResult:
    """★ P2 — 발행창 보류로 LLM 이 *호출조차 되지 않은* 경우의 처리.

    "LLM 판정 불가" ≠ "검증 불가". 전자는 *증거가 없는 것* 이고 후자는 *증거를 만들 수 없는 것*
    이다. 둘을 곱해 학습을 폐기하면, 하루 중 발행창(+前 90분)에 걸린 모든 수정이
    조용히 버려진다 — 그게 실측된 P2 다.
    → 종전(외생 로직 도입 전)의 **보수적 통과 70** 으로 degrade 한다.
      · 격리 패턴은 이 경로로 못 온다 (evaluate 게이트 1 이 먼저 막는다)
      · still_reproduces 도 못 온다 (게이트 0 이 먼저 막는다)
      → 외생 *부정* 신호는 그대로 우선한다. 여기서 완화되는 것은 *신호 없음* 뿐이다.
    킬스위치: `GUARDIAN_EVAL_HOLD_DEGRADE=0` → 종전(폐기) 동작.
    """
    if not _hold_degrade_enabled():
        _SIGNAL_STATS["no_signal_reject"] += 1
        log.warning("[GUARDIAN/eval] ★ 학습 폐기 — 발행창 보류(%s) + 외생=%s "
                    "· HOLD_DEGRADE=0 으로 degrade 비활성", hold[:60], verif or "없음")
        return EvalResult(
            should_register=False, score=0, safe=True, accurate=False, reusable=False,
            rationale=f"발행창 보류로 LLM 판정 없음 + 외생={verif or '없음'} — 폐기(킬스위치)",
            tier="llm", verification=verif, exogenous=bool(verif) or quarantined,
            fail_count=fail_count, quarantined=quarantined,
            llm_judged=False, degraded=False, hold_reason=hold[:120],
        )

    score = _SCORE_CONSERVATIVE - _FAIL_DECAY * fail_count
    ok = score >= _SCORE_CONSERVATIVE
    _SIGNAL_STATS["hold_degrade"] += 1
    # ★ 침묵 금지 — 강등이 일어날 때마다 WARNING 으로 드러낸다 (+ eval_meta 에 영구 박제)
    log.warning(
        "[GUARDIAN/eval] ★ 판정 강등 — 발행창 보류로 LLM 미호출(%s) · 외생=%s "
        "→ 보수적 통과 score=%d register=%s (폐기 아님)",
        hold[:60], verif or "없음", score, ok,
    )
    return EvalResult(
        should_register=ok, score=max(0, score),
        safe=True, accurate=True, reusable=True,
        rationale=(f"발행창 보류로 LLM 판정 없음({hold[:40]}) · 외생={verif or '없음'} "
                   f"— 증거 부재는 실패가 아님 → 보수적 통과"),
        tier="llm", verification=verif, exogenous=bool(verif) or quarantined,
        fail_count=fail_count, quarantined=quarantined,
        llm_judged=False, degraded=True, hold_reason=hold[:120],
    )


def _no_llm_signal(reason: str, detail: Any, verif: str,
                   fail_count: int, quarantined: bool) -> EvalResult:
    """LLM 신호 부재 시 처리 (발행창 보류가 *아닌* 실패 — 호출 오류·파싱 실패).

    · 외생 검증 통과(reproduced_gone) → 외생 신호만으로 통과 (LLM 없이도 근거 있음)
    · 외생 검증 불가(unverifiable)     → 신호 0 → **거부** (rubber-stamp 방지)
        ※ 단 `GUARDIAN_EVAL_LLM_FAIL_PASS=1` 이면 보수적 통과로 degrade.
    · 신호 없음(키 부재)               → 종전 그대로 보수적 통과 70
    ★ 어느 경로든 *관측 가능* — 폐기는 WARNING 으로 반드시 남긴다.
    """
    if verif == V_GONE:
        _SIGNAL_STATS["exogenous_pass"] += 1
        score = _SCORE_REPLAY_VERIFIED - _FAIL_DECAY * fail_count
        return EvalResult(
            should_register=score >= _SCORE_CONSERVATIVE, score=max(0, score),
            safe=True, accurate=True, reusable=True,
            rationale=f"{reason} — 그러나 외생 검증(reproduced_gone) 통과로 등록 ({str(detail)[:80]})",
            tier="llm", verification=verif, exogenous=True,
            fail_count=fail_count, quarantined=quarantined,
            llm_judged=False,
        )
    if verif == V_UNVERIFIABLE:
        if _flag("GUARDIAN_EVAL_LLM_FAIL_PASS", "0"):
            # 운영 판단으로 LLM 실패까지 완화하고 싶을 때 (기본 off — rubber-stamp 방지 유지)
            _SIGNAL_STATS["hold_degrade"] += 1
            score = _SCORE_CONSERVATIVE - _FAIL_DECAY * fail_count
            log.warning("[GUARDIAN/eval] ★ 판정 강등 — %s + 외생 검증 불가 "
                        "· LLM_FAIL_PASS=1 → 보수적 통과 score=%d", reason, score)
            return EvalResult(
                should_register=score >= _SCORE_CONSERVATIVE, score=max(0, score),
                safe=True, accurate=True, reusable=True,
                rationale=f"{reason} + 외생 검증 불가 — 킬스위치로 보수적 통과",
                tier="llm", verification=verif, exogenous=True,
                fail_count=fail_count, quarantined=quarantined,
                llm_judged=False, degraded=True,
            )
        # ★ 침묵 금지 — 폐기는 반드시 WARNING 으로 드러낸다 (종전엔 로그 0 이었다)
        _SIGNAL_STATS["no_signal_reject"] += 1
        log.warning("[GUARDIAN/eval] ★ 학습 폐기 — %s + 외생 검증 불가(unverifiable) "
                    "→ 판정 신호 0 (%s). 완화하려면 GUARDIAN_EVAL_LLM_FAIL_PASS=1",
                    reason, str(detail)[:80])
        return EvalResult(
            should_register=False, score=0,
            safe=True, accurate=False, reusable=False,
            rationale=f"{reason} + 외생 검증 불가 — 판정 신호 0, 학습 거부 ({str(detail)[:80]})",
            tier="llm", verification=verif, exogenous=True,
            fail_count=fail_count, quarantined=quarantined,
            llm_judged=False,
        )
    return _conservative_pass(reason, detail, fail_count)


def _conservative_pass(reason: str, detail: Any = "", fail_count: int = 0) -> EvalResult:
    """LLM 평가 실패 + 외생 신호 없음 → 보수적 통과 (종전 동작 유지, 학습 중단 방지)."""
    score = _SCORE_CONSERVATIVE - _FAIL_DECAY * fail_count
    return EvalResult(
        should_register=score >= _SCORE_CONSERVATIVE,
        score=max(0, score),
        safe=True, accurate=True, reusable=True,
        rationale=f"{reason} — 보수적 통과 ({str(detail)[:100]})",
        tier="llm", fail_count=fail_count, llm_judged=False, degraded=True,
    )


def _notify_rejection(error_record: dict, target_file: str, result: EvalResult) -> None:
    """학습 자산화 거부 — 텔레그램 비활성 (사용자 박제), 로그만 기록."""
    log.info("[Eval] 학습 자산화 거부 — %s 점수=%s 파일=%s",
             error_record.get("error_type", ""), result.score, target_file)


__all__ = ["evaluate", "should_register", "to_meta", "EvalResult",
           "STATIC_FIXERS", "REPLAY_FIXERS", "LLM_FIXERS", "fixer_sets", "FixerSets",
           "SCORE_PASS", "SCORE_PASS_UNVERIFIED",
           "pattern_health", "record_fix_failure", "prune_quarantined",
           "eval_signal_stats",
           "V_GONE", "V_UNVERIFIABLE", "V_STILL", "VERIFICATION_VALUES"]
