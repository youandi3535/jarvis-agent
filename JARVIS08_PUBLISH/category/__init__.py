"""JARVIS08_PUBLISH/category — 카테고리 상수·검색 단일 진입점.

ADR 008 Phase 2 (사용자 박제 2026-05-17).

이관 대상:
  - ECONOMIC_CATEGORY      (네이버 카테고리명)
  - resolve_naver_category (네이버 발행 시 카테고리 검색·선택 — naver_poster.py 이관)
  - resolve_tistory_category (티스토리 발행 시 카테고리 검색·선택 — tistory_poster.py 이관)

이 폴더 외부에서 카테고리 상수 정의 금지 (precommit_check `domain/category` 강제).
"""
from JARVIS08_PUBLISH.category.constants import (  # noqa: F401
    ECONOMIC_CATEGORY,
    ECONOMIC_TAGS_DEFAULT,
    THEME_CATEGORY,
)

__all__ = [
    "ECONOMIC_CATEGORY",
    "ECONOMIC_TAGS_DEFAULT",
    "THEME_CATEGORY",
]
