"""JARVIS07_GUARDIAN/severity.py — 오류 심각도 분류기.

심각도 기준:
  critical — DB 손상 / 데몬 종료 위험 / 핵심 공유 모듈 파괴
  high     — 핵심 모듈 ImportError / 데몬 스레드 크래시 / 인증 실패
  medium   — 특정 기능 실패 (블로그 발행 1건 등)
  low      — 경고 수준 / 재시도로 해결 가능
"""
from __future__ import annotations

import re

# ── 심각도별 패턴 ─────────────────────────────────────────────

_CRITICAL_TYPES = frozenset({
    "SystemExit", "KeyboardInterrupt",
    "MemoryError", "RecursionError",
})

_CRITICAL_PATTERNS = [
    re.compile(r"database disk image is malformed", re.I),
    re.compile(r"unable to open database", re.I),
    re.compile(r"jarvis\.sqlite.*locked", re.I),
    re.compile(r"daemon.*shutting down", re.I),
]

_HIGH_TYPES = frozenset({
    "ImportError", "ModuleNotFoundError",
    "PermissionError", "OSError",
})

_HIGH_PATTERNS = [
    re.compile(r"(shared|jarvis_daemon).*import", re.I),
    re.compile(r"no module named", re.I),
    re.compile(r"authentication.*failed|token.*invalid|api.?key", re.I),
    re.compile(r"thread.*crashed|daemon thread", re.I),
]

_LOW_TYPES = frozenset({
    "TimeoutError", "ConnectionError", "HTTPError",
    "StopIteration", "GeneratorExit",
})

_LOW_PATTERNS = [
    re.compile(r"timeout|timed out", re.I),
    re.compile(r"connection reset|connection refused", re.I),
    re.compile(r"rate limit|too many requests", re.I),
    re.compile(r"retry", re.I),
    # ★ ERRORS [260] 박제 2026-06-07 — transient LLM 응답 형식 오류 (코드 버그 아님)
    re.compile(r"\[transient\]|transient_llm_format|LLM 응답.*(빈|JSON 형식 누락)", re.I),
]


def classify(
    error_type: str,
    message: str,
    source: str = "",
    module: str = "",
) -> str:
    """오류 심각도 반환: 'critical' | 'high' | 'medium' | 'low'"""
    et = error_type or ""
    msg = (message or "").lower()

    # critical
    if et in _CRITICAL_TYPES:
        return "critical"
    for pat in _CRITICAL_PATTERNS:
        if pat.search(msg):
            return "critical"

    # high
    if et in _HIGH_TYPES:
        return "high"
    for pat in _HIGH_PATTERNS:
        if pat.search(msg):
            return "high"

    # low
    if et in _LOW_TYPES:
        return "low"
    for pat in _LOW_PATTERNS:
        if pat.search(msg):
            return "low"

    # 소스별 보정
    if source in ("scheduler",) and "job" in msg:
        return "high"

    return "medium"


# 패턴 기반 fixer 가 명확히 처리 가능한 error_type
# pattern_fixer.py 의 6종 패턴과 일치 — 자동 시도 우선
# ★ 사용자 박제 2026-05-16 — ValueError 추가 (ERRORS [111]) — tuple unpack mismatch 자동 fix
_PATTERN_FIXABLE_TYPES = frozenset({
    "ModuleNotFoundError",  # 상대 import → 절대 import 자동 변환
    "ImportError",          # cannot import name → 유사 심볼 자동 교정
    "TypeError",            # NoneType subscriptable → (x or "")[:N]
    "NameError",            # 오타 → 유사 식별자 교정
    "AttributeError",       # NoneType has no attribute → None 가드 삽입
    "ValueError",           # ★ NEW 2026-05-16 — tuple unpack mismatch (3→5 같은 시그니처 변경)
})


def is_auto_fixable(severity: str, error_type: str) -> bool:
    """자동 수정 시도 가능 여부.

    원칙:
      - critical 은 사람 판단 (DB 손상·데몬 종료 등)
      - SystemExit/MemoryError 류는 코드 수정 불가
      - 패턴 기반 fixer 가 처리 가능한 type 은 *severity 무관* 자동 시도
        (high·medium 자동 처리 확대 — '진짜 어려운 거 빼곤 자동' 원칙)
      - 나머지는 medium 만 LLM fallback
    """
    if severity == "critical":
        return False
    if error_type in _CRITICAL_TYPES:
        return False
    # 패턴 기반 fixer 가 처리 가능한 type 은 high 도 자동 시도
    if error_type in _PATTERN_FIXABLE_TYPES:
        return True
    return severity in ("high", "medium")
