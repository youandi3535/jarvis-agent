"""JARVIS00_INFRA/verification.py — 범용 작업 검증 레지스트리 (단일 진입점).

★ 사용자 박제 2026-07-02 — "모든 에이전트가 작업 완료 시, 그 작업 종류에 맞는 검증을
  통과해야만 결과를 다음으로 넘긴다. 작업했다고 그냥 통과시키는 게 아니라 철저한 검증."

설계 원칙
────────────────────────────────────────────────────────────────────────
- harness(JARVIS00_INFRA/harness.py)의 Layer 3 검증 순환이 "통과 못하면 송출 안 함"을
  강제한다면, 이 모듈은 *무엇을 검증할지* 를 **작업 종류(task_type)별로 선언**하는
  레지스트리다. 둘은 수직 협력: 에이전트/하네스가 verify_output() 을 호출 → 실패
  체크포인트를 Issue 로 변환 → 재작업 순환.
- 작업 종류마다 확인해야 할 핵심 체크포인트가 다르다 (수집=팩트성·출처, 이미지=파일유효·
  수치진실, 발행=성공확인·승인물일치). 그래서 task_type 을 키로 체크를 등록한다.
- 체크는 "" 반환 = 통과, 실패 사유 문자열 반환 = 위반. 여러 체크를 등록 가능.
- severity="block" 은 발행/전파 차단(재작업), "warn" 은 경고만(전파는 허용).

사용
────────────────────────────────────────────────────────────────────────
    from JARVIS00_INFRA.verification import register_check, verify_output

    @register_check("collect_stocks_data", "실취득 종목만", severity="block")
    def _stocks_have_data(output, ctx):
        bad = [s for s in (output or {}).get("stocks", []) if not s.get("price")]
        return f"{len(bad)}개 종목 시세 미취득" if bad else ""

    issues = verify_output("collect_stocks_data", data, {"theme": theme})
    # issues: list[CheckResult] (실패분만). 빈 리스트면 통과.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger("jarvis.verification")

# 검증 함수 시그니처: (output, context) -> str   ("" = 통과, 사유 = 위반)
CheckFn = Callable[[Any, dict], str]


@dataclass
class _Check:
    task_type: str
    name: str
    severity: str            # "block" | "warn"
    fn: CheckFn


@dataclass
class CheckResult:
    """단일 체크포인트 결과 (실패분만 verify_output 이 반환)."""
    task_type: str
    checkpoint: str
    detail: str
    severity: str            # "block" | "warn"

    def as_issue_kwargs(self) -> dict:
        """harness.Issue 로 변환할 kwargs (step/kind/detail)."""
        return {"kind": "verification",
                "detail": f"[{self.task_type}/{self.checkpoint}] {self.detail}"}


# task_type -> [_Check]
_REGISTRY: dict[str, list[_Check]] = {}


def register_check(task_type: str, name: str, severity: str = "block") -> Callable[[CheckFn], CheckFn]:
    """작업 종류별 검증 체크포인트 등록 데코레이터.

    task_type: 작업 종류 (예: 'collect_stocks_data', 'generate_infographic', 'post_to_naver')
    name:      체크포인트 이름 (실패 리포트에 표시)
    severity:  'block'(재작업 차단) | 'warn'(경고만)
    """
    if severity not in ("block", "warn"):
        raise ValueError(f"severity must be block|warn, got {severity}")

    def deco(fn: CheckFn) -> CheckFn:
        _REGISTRY.setdefault(task_type, []).append(_Check(task_type, name, severity, fn))
        return fn
    return deco


def verify_output(task_type: str, output: Any, context: Optional[dict] = None) -> list[CheckResult]:
    """작업 산출물을 task_type 에 등록된 모든 체크포인트로 검증.

    Returns: 실패한 CheckResult 리스트 (빈 리스트 = 전부 통과).
    검증 함수 자체가 폭발하면 그 체크는 'warn' 으로 강등하고 로그 — 검증기 버그가
    정상 산출물을 무한 차단하지 않게 (단, 조용히 삼키지 않고 반드시 로그+결과에 남김).
    """
    ctx = context or {}
    checks = _REGISTRY.get(task_type) or []
    out: list[CheckResult] = []
    for c in checks:
        try:
            detail = c.fn(output, ctx) or ""
        except Exception as e:
            log.error(f"[verification] 체크 폭발 {task_type}/{c.name}: {type(e).__name__}: {e}")
            out.append(CheckResult(task_type, c.name, f"검증기 오류: {e}", "warn"))
            continue
        if detail:
            out.append(CheckResult(task_type, c.name, str(detail), c.severity))
    if out:
        blocks = [r for r in out if r.severity == "block"]
        log.warning(f"[verification] {task_type} 검증 실패 {len(out)}건 "
                    f"(block {len(blocks)}) — " + "; ".join(r.checkpoint for r in out))
    return out


def has_blocking(results: list[CheckResult]) -> bool:
    """block 심각도 실패가 하나라도 있으면 True."""
    return any(r.severity == "block" for r in (results or []))


def registered_task_types() -> list[str]:
    """등록된 task_type 목록 (내성·테스트용)."""
    return sorted(_REGISTRY.keys())


def checkpoints_for(task_type: str) -> list[str]:
    return [c.name for c in _REGISTRY.get(task_type, [])]


# ══════════════════════════════════════════════════════════════════════
# 범용(도메인 무관) 체크포인트 — 여러 작업이 공유. 도메인 특화 체크는 각 도메인
# 모듈에서 register_check 로 등록 (import 시 자동 등록).
# ══════════════════════════════════════════════════════════════════════

def is_valid_image_file(path) -> str:
    """이미지 파일 유효성 — 존재·0바이트 아님·PIL 로드 가능. "" 통과, 사유 반환."""
    from pathlib import Path
    if not path:
        return "이미지 경로 없음"
    p = Path(str(path))
    if not p.exists():
        return f"파일 없음: {p.name}"
    try:
        if p.stat().st_size == 0:
            return f"0바이트 파일: {p.name}"
    except OSError as e:
        return f"stat 실패: {e}"
    # SVG 는 텍스트라 PIL 대상 아님 — 존재·비영만 확인
    if p.suffix.lower() == ".svg":
        return ""
    try:
        from PIL import Image  # type: ignore
        with Image.open(p) as im:
            im.verify()   # 손상·잘림 감지
    except Exception as e:
        return f"이미지 손상·로드 실패({p.name}): {type(e).__name__}"
    return ""


__all__ = [
    "CheckResult", "register_check", "verify_output", "has_blocking",
    "registered_task_types", "checkpoints_for", "is_valid_image_file",
]
