"""학술 논문 프로바이더 — arXiv Open Access API (공식 허용)."""
from __future__ import annotations
import arxiv
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.academic")


class AcademicProvider(BaseProvider):
    source_type = "academic"

    def collect(self, theme: str, sector: str = "", max_items: int = 3) -> list[RawDocument]:
        results = []
        query = f"{theme} {sector}".strip()
        try:
            client = arxiv.Client()
            search = arxiv.Search(query=query, max_results=max_items, sort_by=arxiv.SortCriterion.Relevance)
            for paper in client.results(search):
                results.append(RawDocument(
                    url=paper.pdf_url or paper.entry_id,
                    source_type=self.source_type,
                    raw_text=paper.summary,
                    title=paper.title,
                    published_at=str(paper.published),
                    extra={"theme": theme, "authors": [str(a) for a in paper.authors[:3]]},
                ))
        except Exception as e:
            log.warning(f"[Academic] arXiv 수집 실패: {e}")
        return results
