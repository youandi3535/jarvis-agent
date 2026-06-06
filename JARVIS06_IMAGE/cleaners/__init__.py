"""JARVIS06_IMAGE/cleaners — 이미지 파일·디렉터리 정리 단일 진입점.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — economic_poster._cleanup_economic_images 이관.
이 폴더 외 위치에 이미지 정리 함수 정의 금지.
"""
from JARVIS06_IMAGE.cleaners.economic_image_cleaner import (  # noqa: F401
    cleanup_economic_images,
)

__all__ = [
    "cleanup_economic_images",
]
