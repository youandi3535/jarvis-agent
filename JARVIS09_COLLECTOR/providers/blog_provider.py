"""블로그 프로바이더 — 네이버 블로그·티스토리 RSS (허용된 공개 피드만)."""
from __future__ import annotations
import feedparser
from ..models import RawDocument
from ..robots_guard import can_crawl
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.blog")

_NAVER_BLOG_SEARCH = "https://search.naver.com/search.naver?where=blog&query={query}&sm=tab_jum"
_NAVER_RSS = "https://rss.blog.naver.com/search?query={query}"


class BlogProvider(BaseProvider):
    source_type = "blog"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        results = []
        query = f"{theme} {sector}".strip()
        # 네이버 블로그 공개 RSS
        feed_url = f"https://rss.blog.naver.com/search.nhn?q={query}"
        try:
            if not can_crawl(feed_url):
                log.info(f"[Blog] robots.txt 차단: {feed_url}")
                return []
            wait_for(feed_url)
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_items]:
                url = entry.get("link", "")
                if not url or not can_crawl(url):
                    continue
                wait_for(url)
                results.append(RawDocument(
                    url=url,
                    source_type=self.source_type,
                    raw_text=entry.get("summary", ""),
                    title=entry.get("title", ""),
                    published_at=entry.get("published", ""),
                    extra={"theme": theme},
                ))
        except Exception as e:
            log.warning(f"[Blog] 수집 실패: {e}")
        return results
