"""웹 프로바이더 — 위키피디아 + 네이버 지식백과 + 다음 검색."""
from __future__ import annotations
import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.web")

_HEADERS = {"User-Agent": "JarvisCollector/1.0 (educational; +https://github.com/jarvis-agent)"}
_TIMEOUT = 10


class WebProvider(BaseProvider):
    source_type = "web"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        """허용된 공개 사이트에서 주제 관련 콘텐츠 수집."""
        results = []

        # ── 1. 위키피디아 한국어 (공식 API) ─────────────────────────────
        for query in [theme, f"{theme} {sector}".strip()]:
            wiki_url = (f"https://ko.wikipedia.org/w/api.php?action=query&prop=extracts"
                        f"&exintro=0&titles={quote_plus(query)}&format=json&exlimit=1")
            try:
                wait_for(wiki_url)
                resp = httpx.get(wiki_url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
                if resp.status_code == 200:
                    pages = resp.json().get("query", {}).get("pages", {})
                    for pid, page in pages.items():
                        if pid == "-1":
                            continue
                        extract = BeautifulSoup(page.get("extract", ""), "html.parser").get_text()
                        if len(extract) > 100:
                            results.append(RawDocument(
                                url=f"https://ko.wikipedia.org/wiki/{quote_plus(query)}",
                                source_type=self.source_type,
                                raw_text=extract[:8000],
                                title=page.get("title", query),
                                extra={"theme": theme, "source": "wikipedia"},
                            ))
                            log.info(f"[Web] Wikipedia '{query}' {len(extract)}자 수집")
            except Exception as e:
                log.debug(f"[Web] Wikipedia 실패 ({query}): {e}")

        # ── 2. 네이버 지식백과 (공개 검색 API) ──────────────────────────
        # 네이버 개발자 센터 지식백과 오픈 API (client_id 불필요한 공개 검색)
        try:
            enc_q = quote_plus(theme)
            terms_url = (f"https://terms.naver.com/search.naver?query={enc_q}"
                         f"&searchType=text&categoryId=0")
            wait_for(terms_url)
            resp = httpx.get(terms_url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # 검색 결과 첫 번째 항목 요약
                for item in soup.select(".info_area")[:3]:
                    title_el = item.select_one(".subject")
                    desc_el  = item.select_one(".txt_area")
                    if title_el and desc_el:
                        title = title_el.get_text(strip=True)
                        desc  = desc_el.get_text(strip=True)
                        if len(desc) > 50:
                            results.append(RawDocument(
                                url=terms_url,
                                source_type=self.source_type,
                                raw_text=f"{title}\n{desc}",
                                title=title,
                                extra={"theme": theme, "source": "naver_terms"},
                            ))
                log.info(f"[Web] 네이버 지식백과 {len([r for r in results if r.extra.get('source')=='naver_terms'])}건")
        except Exception as e:
            log.debug(f"[Web] 네이버 지식백과 실패: {e}")

        # ── 3. 다음 금융 섹터 정보 (공개 HTML) ──────────────────────────
        try:
            daum_url = f"https://finance.daum.net/search?q={quote_plus(theme)}"
            wait_for(daum_url)
            resp = httpx.get(daum_url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                text_parts = []
                for sel in ["[class*='summary']", "[class*='description']", "p"]:
                    for el in soup.select(sel)[:5]:
                        t = el.get_text(strip=True)
                        if len(t) > 30:
                            text_parts.append(t)
                if text_parts:
                    combined = "\n".join(text_parts[:10])
                    results.append(RawDocument(
                        url=daum_url,
                        source_type=self.source_type,
                        raw_text=combined[:3000],
                        title=f"{theme} 금융 정보",
                        extra={"theme": theme, "source": "daum_finance"},
                    ))
                    log.info(f"[Web] 다음 금융 수집 완료")
        except Exception as e:
            log.debug(f"[Web] 다음 금융 실패: {e}")

        log.info(f"[Web] 총 {len(results)}건 수집 완료")
        return results[:max_items]
