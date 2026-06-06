"""JARVIS08_PUBLISH/category/constants.py — 카테고리 상수 단일 진입점.

ADR 008 Phase 2 (사용자 박제 2026-05-17).

옛 위치:
  - JARVIS02_WRITER/economic_poster.py:1051 ECONOMIC_CATEGORY = "경제 브리핑"
  - JARVIS02_WRITER/economic_poster.py:1053 ECONOMIC_TAGS_DEFAULT = [...]

호출자 가이드:
    from JARVIS08_PUBLISH.category import ECONOMIC_CATEGORY, ECONOMIC_TAGS_DEFAULT
"""
from __future__ import annotations

# 네이버 블로그 카테고리명 — 정확히 일치해야 함 (naver_poster.py 의 검색 흐름이 매칭)
ECONOMIC_CATEGORY: str = "경제 브리핑"

# 경제 브리핑 기본 태그
ECONOMIC_TAGS_DEFAULT: list[str] = ['경제지표', '오늘의경제', '주식시장', '환율']

# 티스토리·네이버 테마글 카테고리명
THEME_CATEGORY: str = "주식 - 테마분류"

__all__ = [
    "ECONOMIC_CATEGORY",
    "ECONOMIC_TAGS_DEFAULT",
    "THEME_CATEGORY",
]
