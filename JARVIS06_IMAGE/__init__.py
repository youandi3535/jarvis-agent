"""JARVIS06_IMAGE — 이미지 생성 에이전트 패키지.

단일 진입점 규정: 시스템 내 모든 이미지 생성은 이 패키지를 통해서만.

★ `generate_chart` export 삭제 (2026-08-10) — 본체가 초크포인트
  (`infographic_engine._emit` → `certify_image`) 를 지나지 않는 우회로였고,
  이 export 가 `shared.bus` 와 함께 그 우회로의 외부 도달 경로였다.
  수치 차트는 `infographic_engine.generate_infographic` 하나로만 만든다.
"""
from JARVIS06_IMAGE.image_agent import (  # noqa: F401
    generate_photo,
    generate_thumbnail,
    register,
    handle_safe_intent,
)

__all__ = [
    "generate_photo",
    "generate_thumbnail",
    "register",
    "handle_safe_intent",
]
