"""관세청 수출입 통계 프로바이더 — KOSIS API 를 통한 무역 데이터 수집.

KosisProvider 의 코드 해석 로직을 위임해 다차원 통계표도 정상 처리.
수출입 통계는 주제와 무관하게 항상 유용한 경제 맥락 지표이므로
theme 키워드를 무역 전용으로 고정해 도메인 특화 수집을 수행.
"""
from __future__ import annotations
import logging

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.customs")

_SEARCH_KEYWORDS = ["수출금액", "수입금액", "무역수지", "수출입현황"]


class CustomsProvider(BaseProvider):
    """관세청 수출입 통계 — KosisProvider 코드 해석 경유 (KOSIS_API_KEY 필요)."""
    source_type = "customs"

    def __init__(self):
        from .kosis_provider import KosisProvider
        self._kosis = KosisProvider()

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._kosis._available:
            log.info("[Customs] KOSIS_API_KEY 없음 — 스킵")
            return []

        results: list[RawDocument] = []
        for kw in _SEARCH_KEYWORDS:
            if len(results) >= max_items:
                break
            docs = self._kosis.collect(kw, sector, max_items=2)
            for d in docs:
                d.source_type = self.source_type
                d.title = d.title.replace("KOSIS 통계청", "관세청")
            results.extend(docs)

        log.info(f"[Customs] '{theme}' → {len(results)}건 수집")
        return results[:max_items]


__all__ = ["CustomsProvider"]
