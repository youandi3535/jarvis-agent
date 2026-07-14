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
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    result: dict[str, list[float]] = {}
    for i in range(0, len(keywords), 5):
        _wd_beat()   # ★ 배치 단위 진행 신호 — freeze 오탐 방지
        batch = keywords[i : i + 5]
        data  = get_datalab_trend(batch, start_date=start, end_date=end, time_unit="date")
        for r in data.get("results", []):
            kw = r["title"]
            result[kw] = [d.get("ratio", 0.0) for d in r.get("data", [])]
        if i + 5 < len(keywords):
            time.sleep(0.5)
    return result


def get_naver_trending(limit: int = 20) -> list[dict]:
    """네이버 뉴스 헤드라인 빈도 기반 트렌딩 키워드 (독립 소스).

    2021년 실시간 검색어 폐지 이후 최선: 뉴스 RSS 5개 섹션 + 검색 API 헤드라인에서
    키워드 출현 빈도를 집계해 순위 산출.

    반환: [{"keyword": str, "rank": int, "score": float}, ...]  (score: 0~1)
    """
    import re, xml.etree.ElementTree as ET
    from collections import Counter

    KW_PAT  = re.compile(r"[가-힣]{3,8}")   # 최소 3글자 한글 — 단음절·이음절 제외
    BAD_END = re.compile(r"(으로|에서|에게|부터|까지|하여|하고|이고|이며|겠다|ㄴ다|는다|었다|았다|했다|된다|한다|이다|지만|라며|라고|면서|으며|이며)$")
    STOP = {
        # 뉴스 메타
        "기자", "뉴스", "오늘", "이슈", "화제", "최신", "단독", "속보", "긴급",
        "보도", "취재", "기사", "논평", "사설", "헤드라인", "특보", "방송",
        # 조사·접속사·어미
        "에요", "이에요", "한다", "된다", "있다", "없다", "위해", "위한", "통해",
        "따라", "대한", "관한", "이후", "이전", "하지만", "그러나", "그리고",
        "하면서", "이지만", "에서", "으로", "에게", "부터", "까지", "라서",
        "이라", "한테", "하는", "이다", "하여", "하고", "이고", "이며",
        # 일반 동사·형용사 명사화
        "개최", "지원", "확대", "강화", "추진", "발표", "시행", "도입", "운영",
        "진행", "완료", "결정", "논의", "검토", "분석", "예상", "전망", "평가",
        "인터뷰", "회견", "간담회", "설명", "언급", "주장", "발언", "강조",
        "증가", "감소", "상승", "하락", "급등", "급락", "변화", "변동",
        "문제", "사건", "사고", "이유", "원인", "결과", "영향", "효과",
        "계획", "목표", "방안", "방침", "대책", "조치", "방법", "방식",
        "관련", "이후", "동안", "현재", "앞으로", "지난해", "올해", "내년",
        "가운데", "상황", "과정", "위기", "논란", "갈등", "협력", "협의",
        "참가", "참여", "행사", "대회", "경기", "시합", "선발", "선정",
        "공개", "공식", "발매", "출시", "출범", "개막", "폐막", "오픈",
        "속도감", "가능성", "필요성", "중요성", "의미", "역할", "특성",
    }

    freq: Counter = Counter()
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR,ko;q=0.9"})

    # ── 소스 1: 네이버 뉴스 RSS 5개 섹션 ──────────────────────────
    rss_feeds = [
        ("https://news.naver.com/section/rss/105", "IT"),
        ("https://news.naver.com/section/rss/101", "경제"),
        ("https://news.naver.com/section/rss/103", "사회"),
        ("https://news.naver.com/section/rss/100", "정치"),
        ("https://news.naver.com/section/rss/106", "생활문화"),
    ]
    for feed_url, section in rss_feeds:
        try:
            r = sess.get(feed_url, timeout=8)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:20]:
                el = item.find("title")
                if el is None or not el.text:
                    continue
                text = re.sub(r"<!\[CDATA\[|\]\]>", "", el.text).strip()
                for w in KW_PAT.findall(text):
                    if w not in STOP and not BAD_END.search(w):
                        freq[w] += 1
        except Exception as e:
            print(f"[Naver트렌딩] RSS 오류 ({section}): {e}")
            _g_report("radar", e, module=__name__)

    # ── 소스 2: 네이버 검색 API — 폭넓은 쿼리로 헤드라인 수집 ───────
    if has_api_key():
        broad_queries = ["사회", "경제", "정치", "IT", "문화", "스포츠"]
        for q in broad_queries:
            try:
                r = sess.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers={
                        "X-Naver-Client-Id":     NAVER_CLIENT_ID,
                        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                    },
                    params={"query": q, "display": 20, "sort": "date"},
                    timeout=8,
                )
                if r.status_code != 200:
                    continue
                for item in r.json().get("items", []):
                    title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                    for w in KW_PAT.findall(title):
                        if w not in STOP and not BAD_END.search(w):
                            freq[w] += 1
                time.sleep(0.15)
            except Exception as e:
                print(f"[Naver트렌딩] API 오류 ({q}): {e}")
                _g_report("radar", e, module=__name__)

    if not freq:
        return []

    ranked = freq.most_common(limit)
    max_cnt = ranked[0][1] if ranked else 1
    result = [
        {"keyword": kw, "rank": i + 1, "score": round(cnt / max_cnt, 3)}
        for i, (kw, cnt) in enumerate(ranked)
    ]
    print(f"[Naver트렌딩] 키워드 {len(result)}개 (최고빈도 {max_cnt}회)")
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
