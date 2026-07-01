"""JARVIS09_COLLECTOR/generic_fetch.py — 발견된 임의 소스에서 *실제 데이터* 범용 fetch·파싱.

★ 사용자 박제 2026-07-01: "받아와야 할 곳에서 데이터를 받아온다. 없는 카탈로그면 설치든 뭐든 해서
   연결하고 받아라." — discovery.web_search 가 찾은 URL 을 받아, HTML 표/JSON/프로즈에서 수치를
   뽑아 RawDocument 로 반환한다.

반환된 RawDocument 는 chart_data._extract_series_from_docs(기존 LLM 추출)가 그대로 숫자+출처(URL)
dataset 으로 변환 → 재사용 극대화. 표는 사람이 읽는 형태(라벨 | 값)로 렌더해 추출 정확도를 높인다.

주제별 하드코딩 0. robots_guard·rate_limiter 준수. pandas(표)·bs4(프로즈)·lxml 자동설치.
"""
from __future__ import annotations
import io
import json
import logging
import re

from .models import RawDocument

log = logging.getLogger("jarvis.collector.fetch")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

_HEADERS = {
    "User-Agent": "JarvisCollector/1.0 (educational; +https://github.com/jarvis-agent)",
    "Accept-Language": "ko,en;q=0.8",
}
_TIMEOUT = 12
_NUM_RE = re.compile(r"\d")
_MEANINGFUL_NUM = re.compile(r"\d[\d,.]*")


def _ensure_parse_libs() -> None:
    """표·HTML 파싱 라이브러리 자동설치 (갯수 제한 없는 lib_bootstrap 위임)."""
    try:
        from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
        for imp, pip in (("bs4", "beautifulsoup4"), ("lxml", "lxml"),
                         ("pandas", "pandas"), ("html5lib", "html5lib")):
            ensure_lib(imp, pip)
    except Exception as e:
        log.debug(f"[fetch] 파싱 라이브러리 자동설치 스킵: {e}")


def _fetch(url: str):
    """robots·rate-limit 준수 후 URL fetch. (resp, content_type) 또는 (None, '')."""
    try:
        from .robots_guard import can_crawl
        from .rate_limiter import wait_for
        import httpx
    except ImportError as e:
        log.debug(f"[fetch] httpx 미가용: {e}")
        return None, ""
    try:
        if not can_crawl(url):
            log.debug(f"[fetch] robots.txt 금지 — 스킵: {url}")
            return None, ""
    except Exception:
        pass                                    # robots 확인 실패 시 fail-open
    try:
        wait_for(url)
        resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code != 200:
            return None, ""
        return resp, resp.headers.get("content-type", "").lower()
    except Exception as e:
        log.debug(f"[fetch] 실패({url}): {e}")
        return None, ""


# ── HTML 표 → 사람이 읽는 텍스트 (라벨 | 값) ───────────────────────────────────────
def _df_to_text(df, max_rows: int = 12, max_cols: int = 4) -> str:
    """DataFrame 을 컴팩트 텍스트로. 수치가 있는 표만(없으면 '')."""
    try:
        import pandas as pd
    except ImportError:
        return ""
    if df is None or df.empty or df.shape[0] < 2:
        return ""
    df = df.iloc[:max_rows, :max_cols]
    # 헤더 평탄화(MultiIndex 방지)
    cols = [" ".join(str(c) for c in col) if isinstance(col, tuple) else str(col)
            for col in df.columns]
    cols = [re.sub(r"\s+", " ", c).strip()[:20] for c in cols]
    # 수치 셀이 하나도 없으면 데이터 표 아님
    body = df.astype(str)
    if not body.apply(lambda s: s.str.contains(_MEANINGFUL_NUM, na=False)).any().any():
        return ""
    lines = [" | ".join(cols)]
    for _, row in body.iterrows():
        cells = [re.sub(r"\s+", " ", str(v)).strip()[:24] for v in row.tolist()]
        line = " | ".join(cells)
        if line.strip(" |"):
            lines.append(line)
    return "\n".join(lines) if len(lines) >= 3 else ""


def _tables_from_html(html: str) -> list[str]:
    """HTML 내 모든 표를 pandas 로 파싱 → 수치 있는 표만 텍스트 목록으로."""
    try:
        import pandas as pd
    except ImportError:
        return []
    try:
        dfs = pd.read_html(io.StringIO(html))       # lxml/html5lib 자동 사용
    except Exception:
        return []
    out = []
    for df in dfs[:8]:
        txt = _df_to_text(df)
        if txt:
            out.append(txt)
    return out


def _prose_from_html(html: str, max_chars: int = 2500) -> str:
    """스크립트·스타일 제거 후 본문 텍스트 — 수치가 포함된 문장 위주."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", text)
    # 수치가 든 문장 우선 추출 (데이터 밀도↑)
    sents = re.split(r"(?<=[.!?。])\s+|\n", text)
    numeric = [s for s in sents if _MEANINGFUL_NUM.search(s) and len(s) > 15]
    picked = " ".join(numeric[:20]) if numeric else text
    return picked[:max_chars]


def _json_to_text(data, max_chars: int = 2000) -> str:
    """JSON 응답에서 (라벨, 숫자) 쌍을 평탄화 텍스트로."""
    rows = []

    def _walk(obj, key=""):
        if len(rows) > 60:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                _walk(v, str(k))
        elif isinstance(obj, list):
            for it in obj[:40]:
                _walk(it, key)
        else:
            if isinstance(obj, (int, float)) or (isinstance(obj, str) and _MEANINGFUL_NUM.search(obj or "")):
                if key:
                    rows.append(f"{key}: {obj}")
    try:
        _walk(data)
    except Exception:
        return ""
    return "\n".join(rows)[:max_chars]


def fetch_documents(hits: list[dict], theme: str = "", max_docs: int = 6,
                    max_per_domain: int = 2) -> list[RawDocument]:
    """발견 hit 목록 → 데이터가 든 RawDocument 목록.

    각 URL 을 fetch 해서 ① HTML 표(수치) ② JSON(수치) ③ 프로즈(수치 문장) 순으로 추출.
    표는 표별로 별도 문서(각기 다른 차트 series 가 될 수 있게). 출처 URL 박제.
    """
    if not hits:
        return []
    _ensure_parse_libs()
    docs: list[RawDocument] = []
    per_domain: dict[str, int] = {}
    fetched = 0

    for h in hits:
        if fetched >= max_docs:
            break
        url = h.get("url", "")
        dom = h.get("domain", "")
        if not url or per_domain.get(dom, 0) >= max_per_domain:
            continue
        resp, ctype = _fetch(url)
        if resp is None:
            continue
        fetched += 1
        per_domain[dom] = per_domain.get(dom, 0) + 1
        title = (h.get("title") or dom or "web")[:80]

        made = 0
        try:
            if "json" in ctype:
                txt = _json_to_text(resp.json())
                if txt:
                    docs.append(RawDocument(url=url, source_type="web_data",
                                            raw_text=f"{title}\n{txt}", title=title,
                                            extra={"theme": theme, "kind": "json", "domain": dom}))
                    made += 1
            else:
                html = resp.text
                # ① 표 (가장 신뢰 — 구조화 수치)
                for i, tbl in enumerate(_tables_from_html(html)):
                    docs.append(RawDocument(
                        url=url, source_type="web_data",
                        raw_text=f"[표] {title}\n{tbl}", title=f"{title} (표{i + 1})",
                        extra={"theme": theme, "kind": "table", "domain": dom}))
                    made += 1
                    if made >= 3:
                        break
                # ② 표가 없으면 프로즈(수치 문장)
                if made == 0:
                    prose = _prose_from_html(html)
                    # 스니펫도 합쳐 수치 밀도↑ (뉴스 요약에 핵심 수치가 있는 경우)
                    snip = h.get("snippet", "")
                    body = (snip + " " + prose).strip()
                    if _MEANINGFUL_NUM.search(body) and len(body) > 30:
                        docs.append(RawDocument(
                            url=url, source_type="web",
                            raw_text=f"{title}\n{body}", title=title,
                            extra={"theme": theme, "kind": "prose", "domain": dom}))
                        made += 1
        except Exception as e:
            log.debug(f"[fetch] 파싱 실패({url}): {e}")
            _g_report("collector", e, module=__name__, func_name="fetch_documents")

    log.info(f"[fetch] {fetched}개 URL fetch → {len(docs)}개 데이터 문서 추출")
    return docs


__all__ = ["fetch_documents"]
