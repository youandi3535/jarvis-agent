"""한국 경제·산업 정보 프로바이더 — 공개 데이터 소스.

수집 대상:
  - 네이버 금융 종목 토론실 / 시장 요약 (공개 API)
  - 한국거래소 (KRX) 공개 통계
  - 금융투자협회 (KOFIA) 공개 데이터
  - 통계청 KOSIS 공개 API
"""
from __future__ import annotations
import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.kor_econ")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; JarvisCollector/1.0; +research)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}
_TIMEOUT = 12


class KorEconProvider(BaseProvider):
    """한국 경제·산업 정보 수집 — 공개 소스."""
    source_type = "kor_econ"

    def collect(self, theme: str, sector: str = "", max_items: int = 10) -> list[RawDocument]:
        results = []

        # ── 1. 네이버 금융 섹터/테마 검색 ──────────────────────────────
        try:
            enc = quote_plus(theme)
            url = f"https://finance.naver.com/search/searchList.naver?query={enc}"
            wait_for(url)
            resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                texts = []
                for el in soup.select(".box_type_l .name, .tit_area, .desc")[:8]:
                    t = el.get_text(strip=True)
                    if len(t) > 10:
                        texts.append(t)
                if texts:
                    results.append(RawDocument(
                        url=url,
                        source_type=self.source_type,
                        raw_text="\n".join(texts),
                        title=f"네이버 금융 — {theme}",
                        extra={"theme": theme, "source": "naver_finance"},
                    ))
                    log.info(f"[KorEcon] 네이버 금융 {len(texts)}건 텍스트 수집")
        except Exception as e:
            log.debug(f"[KorEcon] 네이버 금융 실패: {e}")

        # ── 2. 네이버 뉴스 — 경제 섹션 검색 RSS ────────────────────────
        import feedparser
        _news_queries = [
            f"{theme} 산업 동향",
            f"{theme} 시장 현황",
            f"{sector} {theme}" if sector else f"{theme} 투자",
        ]
        for q in _news_queries:
            if len(results) >= max_items:
                break
            gnews_url = (f"https://news.google.com/rss/search?q={quote_plus(q)}"
                         f"+site:sedaily.com+OR+site:inews24.com+OR+site:bizwatch.co.kr"
                         f"&hl=ko&gl=KR&ceid=KR:ko")
            try:
                wait_for(gnews_url)
                feed = feedparser.parse(gnews_url)
                for entry in feed.entries[:4]:
                    url = entry.get("link", "")
                    title = entry.get("title", "")
                    if url and title:
                        results.append(RawDocument(
                            url=url,
                            source_type=self.source_type,
                            raw_text=f"{title}\n{entry.get('summary', '')}",
                            title=title,
                            published_at=entry.get("published", ""),
                            extra={"theme": theme, "source": "kor_biz_news", "query": q},
                        ))
            except Exception as e:
                log.debug(f"[KorEcon] 전문지 뉴스 실패 ({q}): {e}")

        # ── 3. 산업통상자원부 / 중소벤처기업부 보도자료 검색 ───────────
        _gov_feeds = [
            ("motie",  "https://www.motie.go.kr/kor/article/ATCL39/list.do?selectedId=5&num=1"),
            ("msit",   "https://www.msit.go.kr/bbs/list.do?sCode=user&mId=86&mPid=83"),
        ]
        for src, url in _gov_feeds:
            if len(results) >= max_items:
                break
            try:
                wait_for(url)
                resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for el in soup.select("a[href*='view'], .tit, .subject")[:5]:
                        title = el.get_text(strip=True)
                        if theme in title or (sector and sector in title):
                            results.append(RawDocument(
                                url=url,
                                source_type=self.source_type,
                                raw_text=title,
                                title=title,
                                extra={"theme": theme, "source": src},
                            ))
            except Exception as e:
                log.debug(f"[KorEcon] 정부 보도자료 실패 ({src}): {e}")

        log.info(f"[KorEcon] 총 {len(results)}건 수집 완료")
        return results[:max_items]
