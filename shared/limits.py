"""shared/limits.py — 재시도 상한 파생 leaf (2026-08-10 신설).

★ 사용자 박제 (①단일 진입점 · ②동적 설계)

  재시도 상한의 **단일 진실 소스는 `JARVIS00_INFRA/harness.py` 의
  `DEFAULT_MAX_ATTEMPTS` 하나뿐** 이다(CLAUDE.md, 사용자 박제 2026-07-21:
  "어떤 재시도도 최대 2회"). 호출자는 미지정으로 상속하거나 이 함수로 파생한다.

  종전엔 똑같은 `_max_attempts()` 가 J08(3벌)·J03(2벌)·J00·J02·J04·shared 에
  흩어져 **9벌** 있었고, 그 대부분이 `except Exception: return 2` 라는 폴백
  리터럴을 각자 들고 있었다. 값 드리프트는 아직 없었다 — 그래서 더 위험했다.
  아무 증상이 없으니 아무도 세지 않는다. harness 를 못 읽는 날(순환 import·
  부분 배포)엔 폴백이 발동하고, 그때 상한을 3 으로 올려 둔 운영자는 *어떤
  경로만* 2 로 도는지 알 길이 없다. "복사본을 진실로 믿는" 사고의 교과서다.

  참조 구현: `JARVIS06_IMAGE/limits.py` (이미지 도메인 leaf, 같은 계약).
  새 사본을 만들지 말 것 — 다른 폴더에서 상한이 필요하면 **이 함수를 import** 한다.
"""
from __future__ import annotations

__all__ = ["max_attempts"]


def max_attempts() -> int:
    """재시도 상한 — `harness.DEFAULT_MAX_ATTEMPTS`(SSOT) 파생. 숫자를 여기 적지 말 것.

    ★ 폴백을 두지 않는다 — 폴백에 숫자를 적는 순간 그것이 새 사본이 되고,
      harness 를 못 읽는 날엔 그 경로만 조용히 다른 횟수로 돈다. 저장소 내부
      모듈이므로 import 실패는 '설정 차이' 가 아니라 *설치가 깨진 상태* 다.
      **조용한 드리프트보다 예외가 낫다.**

    ★ 매 호출 조회 (모듈 로드 시점 캡처 아님): `HARNESS_MAX_ATTEMPTS` 무배포
      조정·테스트의 monkeypatch 가 이미 뜬 프로세스에도 즉시 먹어야 한다.
    """
    from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS
    return max(1, int(DEFAULT_MAX_ATTEMPTS))
