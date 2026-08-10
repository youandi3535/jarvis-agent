"""JARVIS06_IMAGE/limits.py — 이미지 도메인 공통 한도.

★ 사용자 박제 2026-08-10 (①단일 진입점): 종전엔 똑같은 `_max_attempts()` 가
  design_learner·html_infographic·infographic_engine·thumbnail_maker **4벌** 있었다.
  네 벌이 전부 harness 의 같은 상수를 읽는 accessor 였는데, 사본이 넷이면 폴백값 하나만
  달라져도 이미지 경로마다 재시도 횟수가 갈린다 — 사본은 언제나 한쪽만 고쳐진다.
"""
from __future__ import annotations

# ★ 재시도 상한의 단일 진실 소스는 `JARVIS00_INFRA.harness.DEFAULT_MAX_ATTEMPTS` 다
#   (CLAUDE.md · 사용자 박제 2026-07-21: 어떤 재시도도 최대 2회).
#   **try/except 폴백을 두지 않는다** — 폴백에 숫자를 적는 순간 그것이 아홉 번째 사본이
#   되고, harness 를 못 읽는 날엔 이미지 경로만 조용히 다른 횟수로 돈다.
#   저장소 내부 모듈이므로 import 실패는 '설정 차이' 가 아니라 *설치가 깨진 상태* 다.
from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS as _DEFAULT_MAX_ATTEMPTS


def max_attempts() -> int:
    """재시도 상한 — harness.DEFAULT_MAX_ATTEMPTS(SSOT) 파생. 숫자를 여기 적지 말 것.

    ★ 매 호출 조회 (모듈 로드 시점 캡처 아님): `HARNESS_MAX_ATTEMPTS` 무배포 조정이
      이미 뜬 프로세스에도 먹어야 한다 — 복사본을 진실로 믿지 않는다.
    """
    from JARVIS00_INFRA import harness as _h
    return max(1, int(getattr(_h, "DEFAULT_MAX_ATTEMPTS", _DEFAULT_MAX_ATTEMPTS)))


__all__ = ["max_attempts"]
