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

    @staticmethod
    def _fetch_body(url: str) -> str:
        """trafilatura로 기사 본문 추출 (실패 시 빈 문자열)."""
        try:
            import trafilatura
            resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible)"},
                             timeout=4, follow_redirects=True)
            if resp.status_code == 200:
                return trafilatura.extract(resp.text, include_comments=False) or ""
        except Exception:
            pass
        return ""

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

        # 소스별 최적화 쿼리 생성 (query_expander 활용)
        _core0 = theme.split()[0] if theme.split() else theme
        try:
            from ..query_expander import expand as _expand, news_queries_for as _nqs
            _eq = _expand(theme, sector)
            _core0 = _eq.core_keywords[0] if _eq.core_keywords else _core0
            _nq_base = _nqs(_eq, max_n=4)
        except Exception:
            _nq_base = [f"{theme} {sector}".strip()]

        # 뉴스 검색에 유리한 수식어 추가 (도메인별 커버리지 확장)
        _queries = list(_nq_base)
        for _sfx in ["동향 전망", "실적 영향"]:
            _q = f"{_core0} {_sfx}"
            if _q not in _queries:
                _queries.append(_q)
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

        # 상위 3건 본문 보강 (snippet만 있는 경우 trafilatura로 실제 기사 본문 추가)
        enriched = 0
        for doc in results[:3]:
            if len(doc.raw_text) < 300:
                body = self._fetch_body(doc.url)
                if len(body) > 200:
                    doc.raw_text = f"{doc.title}\n\n{body[:3000]}"
                    enriched += 1

        log.info(f"[NaverNews] 총 {len(results)}건 수집 완료 (본문 보강 {enriched}건)")
        return results
