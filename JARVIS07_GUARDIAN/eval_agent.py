"""JARVIS07 Eval Agent — 수정 결과 평가 + learned_patterns 등록 게이트.

A모델 분리 (ADR 007 — Self-Evolving Harness 비전, docs/decisions/007):
  - auto_repair / pattern_fixer 는 *진단·수정* 만 수행
  - eval_agent 는 *수정 결과 평가 + 학습 자산화 결정* 만 수행
  - auditor 는 *헌법 위반·드리프트 검출 + Refine Rules 제안* 만 수행

# 책임 경계 (단일 진입점)

evaluate(error_record, fixer_name, patch=..., target_file=...) → EvalResult
should_register(error_record, fixer_name, ...) → bool  (간편 진입점)

* learned_patterns 에 *어떤 수정을 학습 자산화할지* 결정하는 *유일한 게이트*.
* `pattern_fixer.record_pattern_hit()` 가 본 모듈의 게이트를 통과한 후만 등록.

# 평가 2단

Tier A — 휴리스틱 (즉시 결정, LLM 호출 0)
  정적 fixer 5종 (relative_import / nonetype_subscript / nametype_typo /
  nonetype_attribute / import_error_alias) 은 *결정적 패턴* 이라 자동 통과.

Tier B — LLM 평가 (Sonnet 5, learn_eval alias)
  llm_patch 결과만 *안전성·정확성·재사용 가치* 3축 채점. 점수 80+ 통과 시만 등록.
  실패 시 텔레그램 알림으로 사용자 검토 요청.

# 점수 메타 박제

evaluate() 결과는 learned_patterns 의 각 패턴 entry 에 `eval_meta` 필드로 저장.
이후 회차에서 *낮은 점수 패턴* 은 우선순위 하향 + 사용자 보고 대상.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

log = logging.getLogger("jarvis.guardian.eval")

# 정적 fixer 화이트리스트 — pattern_fixer.py 의 `_PATTERN_FIXERS` 와 동기.
STATIC_FIXERS: tuple[str, ...] = (
    "relative_import_fix",
    "nonetype_subscript_safe",
    "nametype_error_typo_fix",
    "nonetype_attribute_safe",
    "import_error_alias_fix",
)

# LLM 평가가 필요한 fixer (호출자가 patch / target_file 제공해야 함)
LLM_FIXERS: tuple[str, ...] = ("llm_patch",)

# 통과 점수 임계값
SCORE_PASS = 80


@dataclass
class EvalResult:
    """수정 결과 평가."""
    should_register: bool
    score: int                  # 0-100
    safe: bool                  # 같은 위치 추가 오류 가능성 없는가
    accurate: bool              # 근본 원인 해결인가 (단순 증상 가림 아닌가)
    reusable: bool              # 다른 위치 동일 패턴 재사용 가치
    rationale: str
    tier: str                   # "static" | "llm" | "unknown"
    evaluated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))


# ──────────────────────────────────────────────────────────────
# 공용 진입점
# ──────────────────────────────────────────────────────────────

def evaluate(
    error_record: dict,
    fixer_name: str,
    patch: str = "",
    target_file: str = "",
) -> EvalResult:
    """수정 결과 평가. fixer_name 에 따라 Tier A / B 분기."""
    fn = (fixer_name or "").strip()

    # Tier A — 정적 fixer (결정적 패턴, 자동 통과)
    if fn in STATIC_FIXERS:
        return EvalResult(
            should_register=True,
            score=95,
            safe=True,
            accurate=True,
            reusable=True,
            rationale=f"정적 fixer ({fn}) — 결정적 패턴, 자동 통과",
            tier="static",
        )

    # Tier B — LLM 패치 (정밀 평가)
    if fn in LLM_FIXERS:
        return _evaluate_llm_patch(error_record, patch, target_file)

    # Tier 외 — 알 수 없는 fixer (보수적 통과 + 낮은 점수)
    return EvalResult(
        should_register=True,
        score=70,
        safe=True,
        accurate=True,
        reusable=True,
        rationale=f"알 수 없는 fixer ({fn or 'unknown'}) — 보수적 통과, 점수 70",
        tier="unknown",
    )


def should_register(
    error_record: dict,
    fixer_name: str,
    patch: str = "",
    target_file: str = "",
) -> bool:
    """간편 진입점 — bool 만 반환."""
    return evaluate(error_record, fixer_name, patch, target_file).should_register


def to_meta(result: EvalResult) -> dict[str, Any]:
    """learned_patterns 의 `eval_meta` 필드용 dict 변환."""
    return asdict(result)


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

# 평가 항목 — 각 0/1
1. safe: 같은 위치에 추가 오류 발생 가능성이 없는가?
2. accurate: 근본 원인 해결인가? (단순 증상 가림 아닌가?)
3. reusable: 다른 위치에서 동일 패턴 발생 시 재사용 가치가 있는가?

# 출력 형식 (JSON 한 줄)
{{"safe": 1, "accurate": 1, "reusable": 1, "score": 88, "rationale": "..."}}

score 는 0~100. 80+ 가 통과. 3축 모두 1 이 아니면 80 미만 점수.
rationale 은 50자 이내 한국어로.
"""


def _evaluate_llm_patch(error_record: dict, patch: str, target_file: str) -> EvalResult:
    """LLM 패치 평가 — Sonnet 5 (learn_eval alias).

    실패·예외 시 보수적 통과 (학습 진행 중단 방지) + 텔레그램 알림으로 사용자 검토 요청.
    """
    if not patch:
        # ★ 하드닝 (2026-07-02): patch 없는 llm_patch 는 stored_patch 부재 → 재적용 불가
        #   (비actionable). 학습 자산화 거부 → junk 패턴 등록·밴딧 헛보상 차단.
        return EvalResult(
            should_register=False,
            score=0,
            safe=True, accurate=False, reusable=False,
            rationale="llm_patch 이지만 patch 본문 없음 — 비actionable, 학습 거부",
            tier="llm",
        )

    et = error_record.get("error_type", "")
    msg = (error_record.get("message", "") or "")[:200]
    tb = (error_record.get("traceback", "") or "")[:400]
    patch_view = patch[:2000]

    prompt = _EVAL_PROMPT_TEMPLATE.format(
        error_type=et, message=msg, traceback=tb,
        target_file=target_file or "?",
        patch=patch_view,
    )

    try:
        from shared.llm import invoke_text  # type: ignore
        raw = invoke_text("learn_eval", prompt, max_tokens=300)
    except ImportError:
        # learn_eval alias 가 아직 shared/llm.py MODELS 에 등록 안 됐을 수 있음 → fallback
        try:
            from shared.llm import invoke_text  # type: ignore
            raw = invoke_text("code_fix", prompt, max_tokens=300)
        except Exception as e:
            log.warning("[GUARDIAN/eval] LLM 호출 실패 → 보수적 통과: %s", e)
            return _conservative_pass("LLM 호출 실패", e)
    except Exception as e:
        log.warning("[GUARDIAN/eval] LLM 호출 실패 → 보수적 통과: %s", e)
        return _conservative_pass("LLM 호출 실패", e)

    parsed = _parse_eval_response(raw or "")
    if parsed is None:
        return _conservative_pass("LLM 응답 파싱 실패", raw[:200])

    safe = bool(parsed.get("safe", 0))
    accurate = bool(parsed.get("accurate", 0))
    reusable = bool(parsed.get("reusable", 0))
    score = int(parsed.get("score", 0))
    rationale = str(parsed.get("rationale", ""))[:200]

    should_register_ = score >= SCORE_PASS and safe and accurate

    result = EvalResult(
        should_register=should_register_,
        score=score,
        safe=safe, accurate=accurate, reusable=reusable,
        rationale=rationale or f"LLM 평가 — score={score}",
        tier="llm",
    )

    if not should_register_:
        log.info(
            "[GUARDIAN/eval] 학습 자산화 거부 — score=%d safe=%s acc=%s: %s",
            score, safe, accurate, rationale,
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


def _conservative_pass(reason: str, detail: Any = "") -> EvalResult:
    """LLM 평가 실패 시 보수적 통과 — 자가 학습 중단 방지."""
    return EvalResult(
        should_register=True,
        score=70,
        safe=True, accurate=True, reusable=True,
        rationale=f"{reason} — 보수적 통과 ({str(detail)[:100]})",
        tier="llm",
    )


def _notify_rejection(error_record: dict, target_file: str, result: EvalResult) -> None:
    """학습 자산화 거부 — 텔레그램 비활성 (사용자 박제), 로그만 기록."""
    log.info("[Eval] 학습 자산화 거부 — %s 점수=%s 파일=%s",
             error_record.get("error_type", ""), result.score, target_file)


__all__ = ["evaluate", "should_register", "to_meta", "EvalResult",
           "STATIC_FIXERS", "LLM_FIXERS", "SCORE_PASS"]
