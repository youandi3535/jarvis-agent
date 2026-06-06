"""네이버 트렌드 수집기."""
import math
import os
import time
import requests
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent.parent / ".env")

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

_HEADERS = lambda: {
    "X-Naver-Client-Id":     NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    "Content-Type":          "application/json",
}


def get_datalab_trend(
    keywords: list[str],
    start_date: str = None,
    end_date: str   = None,
    time_unit: str  = "date",
    device: str     = "",
) -> dict:
    """
    네이버 DataLab 검색어 트렌드 API.
    반환: {"startDate":..., "endDate":..., "timeUnit":..., "results":[...]}
    """
    if not NAVER_CLIENT_ID:
        return {}
    if end_date   is None: end_date   = date.today().strftime("%Y-%m-%d")
    if start_date is None: start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    body = {
        "startDate":     start_date,
        "endDate":       end_date,
        "timeUnit":      time_unit,
        "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in keywords],
        "device":        device,
    }
    try:
        r = requests.post(
            "https://openapi.naver.com/v1/datalab/search",
            headers=_HEADERS(), json=body, timeout=10,
        )
        if r.status_code == 200:
            return r.json()
        print(f"[Naver DataLab] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[Naver DataLab] 오류: {e}")
        _g_report("radar", e, module=__name__)
    return {}


def get_shopping_trend(keywords: list[str], start_date: str = None, end_date: str = None) -> dict:
    """네이버 쇼핑 인사이트 — 분야별 트렌드."""
    if not NAVER_CLIENT_ID:
        return {}
    if end_date   is None: end_date   = date.today().strftime("%Y-%m-%d")
    if start_date is None: start_date = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    body = {
        "startDate": start_date,
        "endDate":   end_date,
        "timeUnit":  "date",
        "category":  [{"name": kw, "param": [kw]} for kw in keywords],
    }
    try:
        r = requests.post(
            "https://openapi.naver.com/v1/datalab/shopping/categories",
            headers=_HEADERS(), json=body, timeout=10,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[Naver Shopping] 오류: {e}")
        _g_report("radar", e, module=__name__)
    return {}


def get_autocomplete(query: str) -> list[str]:
    """네이버 자동완성 — 연관 키워드 (인증 불필요)."""
    try:
        r = requests.get(
            "https://ac.search.naver.com/nx/ac",
            params={"q": query, "st": "1", "r_format": "json",
                    "r_enc": "UTF-8", "q_enc": "UTF-8", "t_koreng": "1"},
            timeout=5,
        )
        if r.status_code == 200:
            items = r.json().get("items", [[]])[0]
            return [item[0] for item in items if item]
    except Exception as e:
        print(f"[Naver 자동완성] 오류: {e}")
        _g_report("radar", e, module=__name__)
    return []


def has_api_key() -> bool:
    return bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)


def get_batch_datalab(keywords: list[str], days: int = 30) -> dict[str, list[float]]:
    """DataLab 일별 ratio 배열 반환. 5개씩 배치. {kw: [ratio, ...]}"""
    if not has_api_key() or not keywords:
        return {}
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    result: dict[str, list[float]] = {}
    for i in range(0, len(keywords), 5):
        batch = keywords[i : i + 5]
        data  = get_datalab_trend(batch, start_date=start, end_date=end, time_unit="date")
        for r in data.get("results", []):
            kw = r["title"]
            result[kw] = [d.get("ratio", 0.0) for d in r.get("data", [])]
        if i + 5 < len(keywords):
            time.sleep(0.5)
    return result


def get_competition_score(keyword: str) -> float:
    """네이버 뉴스 검색량으로 경쟁 강도 추정 (0=블루오션, 100=레드오션)."""
    if not has_api_key():
        return 50.0
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id":     NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            params={"query": keyword, "display": 1, "sort": "sim"},
            timeout=5,
        )
        if r.status_code == 200:
            total = r.json().get("total", 0)
            # log10 스케일: 1→0, 100→40, 10000→80, 100000→100
            score = min(100.0, math.log10(max(1, total)) / math.log10(100000) * 100)
            return round(score, 1)
    except Exception:
        pass
    return 50.0
