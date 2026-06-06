"""뉴스 프로바이더 — Google News + 한국 경제 뉴스 RSS."""
from __future__ import annotations
import feedparser
from urllib.parse import quote_plus
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.news")

# 한국 주요 경제 뉴스 RSS (공개 피드만)
_KOR_ECON_RSS = [
    ("hankyung", "https://www.hankyung.com/feed/economy"),
    ("mk",       "https://www.mk.co.kr/rss/30000001/"),
    ("yna",      "https://www.yna.co.kr/rss/economy.xml"),
    ("edaily",   "https://feeds.edaily.co.kr/edaily/stock.xml"),
]


class NewsProvider(BaseProvider):
    source_type = "news"

    def collect(self, theme: str, sector: str = "", max_items: int = 15) -> list[RawDocument]:
        results = []
        seen_urls: set[str] = set()

        def _add(url, title, summary, published, source):
            if not url or not title or url in seen_urls:
                return
            seen_urls.add(url)
            results.append(RawDocument(
                url=url,
                source_type=self.source_type,
                raw_text=f"{title}\n{summary}".strip(),
                title=title,
                published_at=published,
                extra={"theme": theme, "news_source": source},
            ))

        # ── 1. Google News RSS — 다중 쿼리 ─────────────────────────────────
        _queries = [
            f"{theme} {sector}".strip(),
            f"{theme} 주식 투자",
            f"{theme} 전망 시장",
        ]
        for q in _queries:
            if len(results) >= max_items:
                break
            feed_url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=ko&gl=KR&ceid=KR:ko"
            try:
                wait_for(feed_url)
                feed = feedparser.parse(feed_url)
                for entry in feed.entries[:max_items // len(_queries) + 3]:
                    _add(entry.get("link", ""), entry.get("title", ""),
                         entry.get("summary", ""), entry.get("published", ""), "google_news")
            except Exception as e:
                log.warning(f"[News] Google News 쿼리 실패 ({q}): {e}")

        log.info(f"[News] Google News {len(results)}건 수집")

        # ── 2. 한국 경제 뉴스 RSS ─────────────────────────────────────────
        for src_name, feed_url in _KOR_ECON_RSS:
            if len(results) >= max_items * 2:
                break
            try:
                wait_for(feed_url)
                feed = feedparser.parse(feed_url)
                added = 0
                for entry in feed.entries[:10]:
                    title = entry.get("title", "")
                    # 테마 관련 기사만 필터링 (제목에 키워드 포함)
                    theme_words = theme.replace(" ", "")
                    if not any(w in title for w in [theme, theme_words,
                                                     *theme.split(), sector] if w):
                        continue
                    _add(entry.get("link", ""), title,
                         entry.get("summary", ""), entry.get("published", ""), src_name)
                    added += 1
                if added:
                    log.info(f"[News] {src_name} {added}건 수집")
            except Exception as e:
                log.debug(f"[News] {src_name} 실패: {e}")

        log.info(f"[News] 총 {len(results)}건 수집 완료")
        return results
