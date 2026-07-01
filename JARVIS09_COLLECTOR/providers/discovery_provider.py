"""발견 프로바이더 — 고정 카탈로그로 못 받는 주제를 웹검색으로 발견·수집.

★ 사용자 박제 2026-07-01: 주제가 다양하니 고정 카탈로그에만 의존하지 말고, 검색어로 실제
   데이터 페이지를 *발견* 해 받아온다. discovery(발견) + generic_fetch(수집) 를 BaseProvider
   인터페이스로 감싼다 → chart_data 가 다른 provider 와 동일하게 `.collect()` 로 사용.

이 provider 는 *주제 로직이 없다* — 검색어를 그대로 web_search 에 넘길 뿐. 어떤 주제든 동작.
"""
from __future__ import annotations
import logging

from ..models import RawDocument
from . import BaseProvider

log = logging.getLogger("jarvis.collector.discover")


class DiscoveryProvider(BaseProvider):
    source_type = "discover"

    def collect(self, theme: str, sector: str = "", max_items: int = 6) -> list[RawDocument]:
        """검색어(theme) → 웹 발견 → 범용 fetch → 데이터 문서. 실패 시 빈 리스트."""
        theme = (theme or "").strip()
        if not theme:
            return []
        try:
            from ..discovery import web_search
            from ..generic_fetch import fetch_documents
        except Exception as e:
            log.warning(f"[discover] 모듈 로드 실패: {e}")
            return []
        try:
            hits = web_search(theme, max_results=max(8, max_items + 4))
            if not hits:
                return []
            return fetch_documents(hits, theme=theme, max_docs=max_items)
        except Exception as e:
            log.warning(f"[discover] 수집 실패('{theme}'): {e}")
            return []
