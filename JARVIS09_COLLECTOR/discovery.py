"""JARVIS09_COLLECTOR/discovery.py — 주제별 동적 소스 *발견* 엔진.

★ 사용자 박제 2026-07-01: "고정 카탈로그에서만 받으려 하지 말고, 주제가 선정되면 어떻게·어디서
   받아야 할지 설계하고, 없는 소스면 API든 설치든 해서 연결해 받아온다."

이 모듈은 *발견* 만 담당한다 — 검색어를 받아 *실제 데이터가 있을 법한 URL* 을 찾는다.
실제 fetch·파싱은 generic_fetch.py 가, 설계는 data_planner.py 가 담당(책임 분리).

3 백엔드 병행 (되는 것만 합침 — 하나 실패해도 나머지로 진행, fail-open):
  1. DuckDuckGo (duckduckgo_search/ddgs 자동설치, 키 불필요) — 글로벌·범용 웹검색
  2. Naver 검색 API (NAVER_CLIENT_ID/SECRET 보유) — 한국 웹문서·백과·전문자료(논문)·뉴스
  3. data.go.kr 공공데이터포털 — 정부 데이터셋 페이지

반환: list[dict] = {"url","title","snippet","domain","backend"} — 정부·통계·논문 도메인 우선 랭킹.
주제별 if-else 하드코딩 0 — 어떤 주제가 와도 동일 경로.
"""
from __future__ import annotations
import importlib
import logging
import os
import re
from urllib.parse import quote, urlparse

log = logging.getLogger("jarvis.collector.discovery")

# 서드파티 검색 라이브러리의 verbose 로그(ddgs 의 "response: ..." 등) 억제 — 발견 로그만 남김.
for _noisy in ("ddgs", "duckduckgo_search", "httpx", "httpcore", "primp", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

_HEADERS = {"User-Agent": "JarvisCollector/1.0 (educational; +https://github.com/jarvis-agent)"}
_TIMEOUT = 12

# ── 도메인 신뢰도 랭킹 — 수치 데이터가 있을 확률 순 (주제 무관, 소스 성격만) ──────────
#   ★ 하드코딩 주제 로직 아님 — "어떤 도메인이 데이터를 담을 가능성이 높은가" 의 일반 지식.
_TIER1_GOV = (          # 공식 통계·정부 (가장 신뢰) — 100
    "kosis.kr", "kostat.go.kr", "bok.or.kr", "index.go.kr", "data.go.kr",
    ".go.kr", "korea.kr", "molit.go.kr", "motie.go.kr", "moef.go.kr",
    "index.mois.go.kr", "narastat", "e-nara",
)
_TIER2_PUBLIC = (       # 공공기관·학술·논문 — 80
    ".or.kr", ".re.kr", ".ac.kr", "arxiv.org", "doi.org", "ncbi.nlm.nih.gov",
    "sciencedirect", "springer", "researchgate", "semanticscholar", "kci.go.kr",
    "riss.kr", ".edu", "worldbank.org", "oecd.org", "imf.org", "who.int",
    "statista.com", "tradingeconomics.com", "fred.stlouisfed.org",
)
_TIER3_REF = (          # 백과·참고 — 60
    "wikipedia.org", "terms.naver.com", "namu.wiki", "britannica.com",
)
_TIER5_LOW = (          # 블로그·커뮤니티 (수치 신뢰 낮음) — 20
    "blog.naver.com", "tistory.com", "cafe.naver.com", "dcinside", "clien",
    "reddit.com", "quora.com", "medium.com",
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _tier_score(url: str) -> int:
    d = _domain(url)
    if not d:
        return 30
    if any(t in d for t in _TIER1_GOV):
        return 100
    if any(t in d for t in _TIER2_PUBLIC):
        return 80
    if any(t in d for t in _TIER3_REF):
        return 60
    if any(t in d for t in _TIER5_LOW):
        return 20
    return 40                       # 일반 뉴스·기타


_NUM_RE = re.compile(r"\d[\d,.]*\s*(억|조|만|%|명|개|건|원|달러|년|위|배|천|톤|km|kg|가구|세대)")
_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z]{2,}|\d{2,}")


def _q_tokens(query: str) -> set[str]:
    """검색어에서 관련성 판정용 토큰(2자+ 한글·영문·숫자) 추출."""
    return {t.lower() for t in _TOKEN_RE.findall(query or "")}


def _rank_key(hit: dict, q_tokens: set[str]) -> int:
    """랭킹 점수 — ① 쿼리 관련성(최우선) ② 도메인 신뢰도 ③ 스니펫 수치.

    쿼리 토큰이 제목/스니펫에 얼마나 겹치는지를 *가장 크게* 반영 → 고tier 라도 무관한
    백과 정의(예: KTX 검색에 '자살률')가 상위를 먹지 않게 한다.
    """
    text = f"{hit.get('title', '')} {hit.get('snippet', '')}".lower()
    rel = sum(1 for t in q_tokens if t in text)
    score = rel * 40                              # 관련성 최우선
    score += _tier_score(hit.get("url", ""))      # 도메인 신뢰도
    if _NUM_RE.search(text):                       # 수치 있으면 데이터 페이지 확률↑
        score += 15
    return score


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").replace("&quot;", '"').replace("&amp;", "&").strip()


# ── 백엔드 1: DuckDuckGo (무료·키 불필요, 라이브러리 자동설치) ─────────────────────
def _get_ddgs():
    """DDGS 클래스 반환. ddgs(신)/duckduckgo_search(구) 순 시도, 없으면 자동설치."""
    for mod_name in ("ddgs", "duckduckgo_search"):
        try:
            return getattr(importlib.import_module(mod_name), "DDGS", None)
        except ImportError:
            continue
    try:
        from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
    except Exception:
        return None
    for mod_name, pip_name in (("ddgs", "ddgs"), ("duckduckgo_search", "duckduckgo-search")):
        mod = ensure_lib(mod_name, pip_name)
        if mod is not None:
            return getattr(mod, "DDGS", None)
    return None


def _ddg_search(query: str, n: int) -> list[dict]:
    DDGS = _get_ddgs()
    if DDGS is None:
        log.debug("[discovery] DuckDuckGo 사용 불가 (설치 실패)")
        return []
    out = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, region="kr-kr", max_results=n) or []:
                url = r.get("href") or r.get("url") or ""
                if not url:
                    continue
                out.append({
                    "url": url,
                    "title": _strip_tags(r.get("title", "")),
                    "snippet": _strip_tags(r.get("body", "")),
                    "domain": _domain(url),
                    "backend": "ddg",
                })
    except Exception as e:
        log.debug(f"[discovery] DuckDuckGo 검색 실패('{query}'): {e}")
    return out


# ── 백엔드 2: Naver 검색 API (보유 키 — 웹문서·백과·전문자료·뉴스) ───────────────────
_NAVER_TYPES = ("webkr", "encyc", "doc", "news")   # 웹문서·백과·전문자료(논문)·뉴스
_NAVER_URL = "https://openapi.naver.com/v1/search/{t}.json"


def _naver_search(query: str, n: int) -> list[dict]:
    cid = os.getenv("NAVER_CLIENT_ID", "")
    csec = os.getenv("NAVER_CLIENT_SECRET", "")
    if not (cid and csec):
        log.debug("[discovery] NAVER_CLIENT_ID/SECRET 없음 — Naver 검색 건너뜀")
        return []
    try:
        import httpx
    except ImportError:
        return []
    from JARVIS09_COLLECTOR.rate_limiter import wait_for
    headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec, **_HEADERS}
    per = max(2, n // len(_NAVER_TYPES))
    out = []
    for t in _NAVER_TYPES:
        url = _NAVER_URL.format(t=t)
        try:
            wait_for(url)
            resp = httpx.get(url, headers=headers,
                             params={"query": query, "display": per, "sort": "sim"},
                             timeout=_TIMEOUT)
            if resp.status_code != 200:
                continue
            for it in resp.json().get("items", []):
                link = it.get("link", "") or it.get("originallink", "")
                if not link:
                    continue
                out.append({
                    "url": link,
                    "title": _strip_tags(it.get("title", "")),
                    "snippet": _strip_tags(it.get("description", "")),
                    "domain": _domain(link),
                    "backend": f"naver_{t}",
                })
        except Exception as e:
            log.debug(f"[discovery] Naver {t} 검색 실패('{query}'): {e}")
    return out


# ── 백엔드 3: data.go.kr 공공데이터포털 (정부 데이터셋 페이지 발견) ──────────────────
_DATAGO_SEARCH = "https://www.data.go.kr/tcs/dss/selectDataSetList.do"


def _datago_search(query: str, n: int) -> list[dict]:
    """공공데이터포털 검색 결과에서 데이터셋 상세 페이지 URL 수집(베스트에포트 스크레이프)."""
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    from JARVIS09_COLLECTOR.rate_limiter import wait_for
    out = []
    try:
        wait_for(_DATAGO_SEARCH)
        resp = httpx.get(_DATAGO_SEARCH, headers=_HEADERS,
                         params={"keyword": query, "dType": "FILE,API", "sort": "updtDt"},
                         timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a[href*='/data/'], a.title, .result-list a")[: n * 2]:
            href = a.get("href", "")
            title = a.get_text(strip=True)
            if not href or not title or len(title) < 4:
                continue
            if href.startswith("/"):
                href = "https://www.data.go.kr" + href
            if "data.go.kr" not in href:
                continue
            out.append({
                "url": href, "title": title, "snippet": "",
                "domain": "data.go.kr", "backend": "datago",
            })
            if len(out) >= n:
                break
    except Exception as e:
        log.debug(f"[discovery] data.go.kr 검색 실패('{query}'): {e}")
    return out


# ── 공개 API ─────────────────────────────────────────────────────────────────────
def web_search(query: str, max_results: int = 10) -> list[dict]:
    """주제 검색어 → 데이터가 있을 법한 URL 목록(정부·통계·논문 우선 랭킹).

    3 백엔드 병행 수집 → URL 중복 제거 → 도메인 신뢰도·수치 스니펫 기준 랭킹.
    되는 백엔드만 사용(fail-open) — 하나가 죽어도 나머지로 발견 지속.
    """
    query = (query or "").strip()
    if not query:
        return []
    hits: list[dict] = []
    for fn in (_ddg_search, _naver_search, _datago_search):
        try:
            hits.extend(fn(query, max_results) or [])
        except Exception as e:
            log.debug(f"[discovery] 백엔드 {fn.__name__} 예외: {e}")
            _g_report("collector", e, module=__name__, func_name=fn.__name__)

    # URL 정규화 후 중복 제거 (첫 등장 유지)
    seen, deduped = set(), []
    for h in hits:
        u = (h.get("url") or "").split("#")[0].rstrip("/")
        if not u or u in seen:
            continue
        seen.add(u)
        deduped.append(h)

    _qt = _q_tokens(query)
    deduped.sort(key=lambda h: _rank_key(h, _qt), reverse=True)
    log.info(f"[discovery] '{query}' → {len(deduped)}개 소스 발견 "
             f"(상위 도메인: {', '.join(dict.fromkeys(h['domain'] for h in deduped[:5]))})")
    return deduped[:max_results]


__all__ = ["web_search"]
