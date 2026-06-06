"""JARVIS09_COLLECTOR/models.py — 수집 데이터 모델."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawDocument:
    """수집 직후 원본 문서."""
    url: str
    source_type: str          # blog | news | academic | finance | web
    raw_html: str = ""
    raw_text: str = ""
    title: str = ""
    published_at: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extra: dict = field(default_factory=dict)


@dataclass
class CollectionResult:
    """정제 완료 결과 — JARVIS02 WRITER 전달용."""
    theme: str
    source_type: str
    url: str
    title: str
    cleaned_text: str         # 잡음 제거된 원본 텍스트 (요약 아님)
    word_count: int = 0
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    meta: dict = field(default_factory=dict)
