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

        # ── 1. 위키피디아 한국어 (opensearch → extract) ──────────────────
        _wiki_api = "https://ko.wikipedia.org/w/api.php"

        # 소스별 최적화 쿼리 생성 (query_expander 활용)
        try:
            from ..query_expander import expand as _expand, wiki_queries_for as _wiki_qs
            _eq = _expand(theme, sector)
            _search_qs = _wiki_qs(_eq)  # 단어 3개 이하 쿼리만
        except Exception:
            _words = theme.split()
            _search_qs = list(dict.fromkeys(filter(None, [
                theme,
                " ".join(_words[:2]) if len(_words) > 2 else "",
                sector,
            ])))

        # 관련성 필터: 검색어 단어 중 하나라도 타이틀에 포함되어야 함
        _search_words = set()
        for sq in _search_qs:
            _search_words.update(sq.split())

        def _is_relevant_title(title: str) -> bool:
            t = title.lower()
            return any(w in t for w in _search_words if len(w) >= 2)

        _wiki_titles: list[str] = []
        for sq in _search_qs[:4]:
            if not sq or len(sq) < 2:
                continue
            try:
                wait_for(_wiki_api)
                r = httpx.get(_wiki_api, params={
                    "action": "opensearch", "search": sq,
                    "limit": 5, "format": "json",
                }, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
                if r.status_code == 200:
                    for t in (r.json()[1] if len(r.json()) > 1 else []):
                        if t not in _wiki_titles and _is_relevant_title(t):
                            _wiki_titles.append(t)
            except Exception as e:
                log.debug(f"[Web] Wikipedia opensearch 실패 ({sq}): {e}")

        for _title in _wiki_titles[:3]:
            try:
                wait_for(_wiki_api)
                r = httpx.get(_wiki_api, params={
                    "action": "query", "prop": "extracts",
                    "exintro": "1",    # intro만 — 안정적이고 충분한 분량
                    "explaintext": "1",  # HTML 제거, 텍스트만
                    "redirects": "1",    # 리다이렉트 자동 해결 (0자 방지)
                    "titles": _title, "format": "json",
                }, headers=_HEADERS, timeout=_TIMEOUT)
                if r.status_code != 200:
                    continue
                for pid, page in r.json().get("query", {}).get("pages", {}).items():
                    if pid == "-1":
                        continue
                    extract = page.get("extract", "")
                    if len(extract) > 100:
                        actual_title = page.get("title", _title)
                        results.append(RawDocument(
                            url=f"https://ko.wikipedia.org/wiki/{quote_plus(actual_title)}",
                            source_type=self.source_type,
                            raw_text=extract[:8000],
                            title=actual_title,
                            extra={"theme": theme, "source": "wikipedia"},
                        ))
                        log.info(f"[Web] Wikipedia '{actual_title}' {len(extract)}자 수집")
                        break
            except Exception as e:
                log.debug(f"[Web] Wikipedia 추출 실패 ({_title}): {e}")

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
                # 다중 셀렉터 시도 (네이버 지식백과 레이아웃 변경 대응)
                _candidates: list[tuple[str, str]] = []
                for sel_wrap, sel_title, sel_desc in [
                    (".info_area", ".subject", ".txt_area"),
                    (".word_list li", ".tit_area .subject", ".desc"),
                    (".search_list .list_item", ".word_tit", ".txt"),
                ]:
                    for item in soup.select(sel_wrap)[:3]:
                        t_el = item.select_one(sel_title)
                        d_el = item.select_one(sel_desc)
                        if t_el and d_el:
                            t = t_el.get_text(strip=True)
                            d = d_el.get_text(strip=True)
                            if len(d) > 50:
                                _candidates.append((t, d))
                    if _candidates:
                        break  # 첫 번째로 매칭된 셀렉터 세트 사용

                for t, d in _candidates[:2]:
                    results.append(RawDocument(
                        url=terms_url,
                        source_type=self.source_type,
                        raw_text=f"{t}\n{d}",
                        title=t,
                        extra={"theme": theme, "source": "naver_terms"},
                    ))
                log.info(f"[Web] 네이버 지식백과 {len(_candidates)}건")
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
