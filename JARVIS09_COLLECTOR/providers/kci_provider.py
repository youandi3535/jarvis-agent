"""국내 학술논문(KCI) 프로바이더 — 한국학술지인용색인 + 무료 폴백.

★ 사용자 박제 2026-07-01 — "KCI(한국학술지) 같은 국내 논문 출처를 추가하라. 논문은
  사실 데이터의 핵심 출처다." 키 없이도 동작해야 한다.

수집 전략 (우선순위, 모두 무료):
  ① KCI Open API   — KCI_API_KEY 있을 때만 (국립중앙도서관/한국연구재단 발급). 키 없으면 스킵.
  ② Crossref       — 키 불필요. 한국 학술지(저널명 한글 포함) 우선 정렬.
  ③ Semantic Scholar Graph API — 키 불필요(rate-limit 有). abstract 보강.

원칙: *실제(팩트) 논문만*. 지어내기 절대 금지 — 못 찾으면 빈 리스트.
"""
from __future__ import annotations

import os
import re
import logging
from xml.etree import ElementTree as ET

import requests

from ..models import RawDocument
from . import BaseProvider

log = logging.getLogger("jarvis.collector.kci")

_HEADERS = {"User-Agent": "jarvis-research/1.0 (mailto:youandi3535@naver.com)"}
_TIMEOUT = 15

_KCI_API_URL = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"
_CROSSREF_URL = "https://api.crossref.org/works"
_SEMANTIC_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

_HANGUL_RE = re.compile(r"[가-힣]")
_TAG_RE = re.compile(r"<[^>]+>")          # JATS/HTML 태그 제거
_WS_RE = re.compile(r"\s+")


def _strip_tags(text: str) -> str:
    """JATS XML / HTML 태그 제거 후 공백 정규화."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                .replace("&quot;", '"').replace("&apos;", "'").replace("&#39;", "'"))
    return _WS_RE.sub(" ", text).strip()


def _has_hangul(text: str) -> bool:
    return bool(_HANGUL_RE.search(text or ""))


class KciProvider(BaseProvider):
    """국내 학술논문(KCI) — 한국연구재단 KCI Open API + Crossref/Semantic Scholar 폴백."""

    source_type = "kci"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        query = f"{theme} {sector}".strip()
        results: list[RawDocument] = []

        # ① KCI Open API (키 있을 때만)
        try:
            results = self._collect_kci(query, theme, max_items)
        except Exception as e:
            log.warning(f"[KCI] KCI Open API 수집 실패: {e}")

        # ② Crossref 폴백 (키 불필요)
        if not results:
            try:
                results = self._collect_crossref(query, theme, max_items)
            except Exception as e:
                log.warning(f"[KCI] Crossref 수집 실패: {e}")

        # ③ Semantic Scholar 폴백 (키 불필요)
        if not results:
            try:
                results = self._collect_semantic(query, theme, max_items)
            except Exception as e:
                log.warning(f"[KCI] Semantic Scholar 수집 실패: {e}")

        log.info(f"[KCI] '{query}' → {len(results)}건 수집 완료")
        return results[:max_items]

    # ── ① KCI Open API ───────────────────────────────────────────────
    def _collect_kci(self, query: str, theme: str, max_items: int) -> list[RawDocument]:
        api_key = os.getenv("KCI_API_KEY", "")
        if not api_key:
            log.info("[KCI] KCI_API_KEY 없음 — KCI Open API 건너뜀(폴백 사용)")
            return []

        params = {
            "apiCode": "articleSearch",
            "key": api_key,
            "title": query,
            "displayCount": str(max_items),
        }
        resp = requests.get(_KCI_API_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning(f"[KCI] KCI API 오류 {resp.status_code}: {resp.text[:200]}")
            return []

        results: list[RawDocument] = []
        root = ET.fromstring(resp.content)
        for rec in root.iter("record"):
            def _txt(tag: str) -> str:
                el = rec.find(f".//{tag}")
                return _strip_tags(el.text) if el is not None and el.text else ""

            title = _txt("title-group") or _txt("article-title") or _txt("title")
            abstract = _txt("abstract-group") or _txt("abstract")
            journal = _txt("journal-title") or _txt("journal-name")
            year = _txt("pub-year") or _txt("year")
            doi = _txt("doi")
            url = (f"https://doi.org/{doi}" if doi else "") or _txt("url")

            authors = [_strip_tags(a.text) for a in rec.iter("author-name") if a.text][:3]

            raw_text = abstract or " · ".join(p for p in (title, journal, year) if p)
            if not (title and url and raw_text):
                continue

            results.append(RawDocument(
                url=url,
                source_type=self.source_type,
                raw_text=raw_text,
                title=title,
                published_at=year,
                extra={"theme": theme, "authors": authors, "venue": journal, "via": "kci"},
            ))
        return results

    # ── ② Crossref REST API (키 불필요) ──────────────────────────────
    def _collect_crossref(self, query: str, theme: str, max_items: int) -> list[RawDocument]:
        params = {
            "query.bibliographic": query,
            # 한국 학술지를 더 넓게 잡기 위해 여유분을 받아 한글 저널 우선 정렬
            "rows": str(max(max_items * 3, max_items)),
            "select": "title,DOI,abstract,author,published,container-title",
        }
        resp = requests.get(_CROSSREF_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning(f"[KCI] Crossref 오류 {resp.status_code}: {resp.text[:200]}")
            return []

        items = ((resp.json() or {}).get("message", {}) or {}).get("items", []) or []
        candidates: list[tuple[int, RawDocument]] = []

        for it in items:
            title = _strip_tags(" ".join(it.get("title") or []))
            doi = it.get("DOI") or ""
            url = f"https://doi.org/{doi}" if doi else ""
            journal = _strip_tags(" ".join(it.get("container-title") or []))
            abstract = _strip_tags(it.get("abstract") or "")

            year = ""
            published = it.get("published") or {}
            parts = (published.get("date-parts") or [[]])
            if parts and parts[0]:
                year = str(parts[0][0])

            authors = []
            for a in (it.get("author") or [])[:3]:
                name = " ".join(p for p in (a.get("given", ""), a.get("family", "")) if p).strip()
                if name:
                    authors.append(name)

            raw_text = abstract or " · ".join(p for p in (title, journal, year) if p)
            if not (title and url and raw_text):
                continue

            doc = RawDocument(
                url=url,
                source_type=self.source_type,
                raw_text=raw_text,
                title=title,
                published_at=year,
                extra={"theme": theme, "authors": authors, "venue": journal, "via": "crossref"},
            )
            # 한국 학술지/한국어 논문 우선 (저널·제목·초록에 한글 포함 시 가산)
            score = 0
            if _has_hangul(journal):
                score += 4
            if _has_hangul(title):
                score += 2
            if _has_hangul(abstract):
                score += 1
            if abstract:
                score += 1   # 초록 있는 논문 우대
            candidates.append((score, doc))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [doc for _, doc in candidates[:max_items]]

    # ── ③ Semantic Scholar Graph API (키 불필요) ─────────────────────
    def _collect_semantic(self, query: str, theme: str, max_items: int) -> list[RawDocument]:
        params = {
            "query": query,
            "fields": "title,abstract,url,year,venue,authors",
            "limit": str(max_items),
        }
        resp = requests.get(_SEMANTIC_URL, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            log.warning(f"[KCI] Semantic Scholar 오류 {resp.status_code}: {resp.text[:200]}")
            return []

        data = (resp.json() or {}).get("data", []) or []
        results: list[RawDocument] = []
        for it in data:
            title = _strip_tags(it.get("title") or "")
            url = it.get("url") or ""
            abstract = _strip_tags(it.get("abstract") or "")
            venue = _strip_tags(it.get("venue") or "")
            year = str(it.get("year") or "")
            authors = [a.get("name", "") for a in (it.get("authors") or [])[:3] if a.get("name")]

            raw_text = abstract or " · ".join(p for p in (title, venue, year) if p)
            if not (title and url and raw_text):
                continue

            results.append(RawDocument(
                url=url,
                source_type=self.source_type,
                raw_text=raw_text,
                title=title,
                published_at=year,
                extra={"theme": theme, "authors": authors, "venue": venue, "via": "semantic_scholar"},
            ))
        return results
