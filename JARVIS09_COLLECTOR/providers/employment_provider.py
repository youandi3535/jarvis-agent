"""고용노동부 고용통계 프로바이더 — KOSIS API 경유.

KosisProvider 의 코드 해석 로직을 위임해 다차원 통계표도 정상 처리.
고용 전용 키워드로 고정 검색.
"""
from __future__ import annotations
import logging

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.employment")

_SEARCH_KEYWORDS = ["경제활동인구", "취업자", "고용률", "실업률"]


class EmploymentProvider(BaseProvider):
    """고용 통계 — KosisProvider 코드 해석 경유 (KOSIS_API_KEY 필요)."""
    source_type = "employment"

    def __init__(self):
        from .kosis_provider import KosisProvider
        self._kosis = KosisProvider()

    def collect(self, theme: str, sector: str = "", max_items: int = 4) -> list[RawDocument]:
        if not self._kosis._available:
            log.info("[Employment] KOSIS_API_KEY 없음 — 스킵")
            return []

        results: list[RawDocument] = []
        for kw in _SEARCH_KEYWORDS:
            if len(results) >= max_items:
                break
            docs = self._kosis.collect(kw, sector, max_items=2)
            for d in docs:
                d.source_type = self.source_type
                d.title = d.title.replace("KOSIS 통계청", "고용 통계(통계청)")
            results.extend(docs)

        log.info(f"[Employment] '{theme}' → {len(results)}건 수집")
        return results[:max_items]


__all__ = ["EmploymentProvider"]
