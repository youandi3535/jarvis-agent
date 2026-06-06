"""네이버 Open API — 뉴스 검색 (NAVER_CLIENT_ID/SECRET 필요)."""
from __future__ import annotations
import os
import httpx
from urllib.parse import quote
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.naver_news")

_API_URL = "https://openapi.naver.com/v1/search/news.json"


class NaverNewsProvider(BaseProvider):
    """네이버 뉴스 검색 API — 가장 최신·정확한 한국어 뉴스."""
    source_type = "naver_news"

    def __init__(self):
        self._client_id  = os.getenv("NAVER_CLIENT_ID", "")
        self._client_secret = os.getenv("NAVER_CLIENT_SECRET", "")

    @property
    def _available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def collect(self, theme: str, sector: str = "", max_items: int = 20) -> list[RawDocument]:
        if not self._available:
            log.warning("[NaverNews] NAVER_CLIENT_ID/SECRET 없음 — 건너뜀")
            return []

        results: list[RawDocument] = []
        seen_urls: set[str] = set()

        headers = {
            "X-Naver-Client-Id":     self._client_id,
            "X-Naver-Client-Secret": self._client_secret,
        }

        # 다중 쿼리: 기본 / 투자 / 산업 / 실적
        _queries = [
            f"{theme} {sector}".strip(),
            f"{theme} 투자 주가",
            f"{theme} 산업 동향",
            f"{theme} 실적 전망",
        ]
        per_query = max(max_items // len(_queries), 5)

        for q in _queries:
            if len(results) >= max_items:
                break
            try:
                wait_for(_API_URL)
                resp = httpx.get(
                    _API_URL,
                    params={"query": q, "display": per_query, "sort": "date"},
                    headers=headers,
                    timeout=10,
                )
                if resp.status_code != 200:
                    log.warning(f"[NaverNews] API 오류 {resp.status_code}: {resp.text[:200]}")
                    continue

                items = resp.json().get("items", [])
                for item in items:
                    url   = item.get("originallink") or item.get("link", "")
                    title = item.get("title", "").replace("<b>", "").replace("</b>", "")
                    desc  = item.get("description", "").replace("<b>", "").replace("</b>", "")
                    if not url or url in seen_urls or not title:
                        continue
                    seen_urls.add(url)
                    results.append(RawDocument(
                        url=url,
                        source_type=self.source_type,
                        raw_text=f"{title}\n{desc}".strip(),
                        title=title,
                        published_at=item.get("pubDate", ""),
                        extra={"theme": theme, "source": "naver_news_api", "query": q},
                    ))
                log.info(f"[NaverNews] '{q}' → {len(items)}건")
            except Exception as e:
                log.warning(f"[NaverNews] 쿼리 실패 ({q}): {e}")

        log.info(f"[NaverNews] 총 {len(results)}건 수집 완료")
        return results
