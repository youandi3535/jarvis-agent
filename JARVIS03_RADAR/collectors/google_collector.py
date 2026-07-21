"""Google Trends 수집기 — RSS/pytrends/네이버 뉴스 멀티 fallback."""
import re
import time
import xml.etree.ElementTree as ET
import requests
import pandas as pd
from pytrends.request import TrendReq
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError

# 글자수 정책 — length_manager 단일 진입점이 빌드된 패턴 제공
from JARVIS02_WRITER import length_manager as _LM

def _max_attempts() -> int:
    """재시도 상한 — harness.DEFAULT_MAX_ATTEMPTS(SSOT) 파생 (사용자 박제 2026-07-21: 2회)."""
    try:
        from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS
        return max(1, int(DEFAULT_MAX_ATTEMPTS))
    except Exception:
        return 2

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_KW_PATTERN = _LM.RADAR_KW_PATTERN_FULL

# ── pytrends 429 (rate limit) 쿨다운 캐시 ──────────────────────
# Google 이 IP 차단 시 60분 동안 pytrends 호출 자체를 skip → 불필요 재시도/로그 폭주 방지.
# 데몬 프로세스 살아있는 동안 모듈 메모리에 유지.
_PYTRENDS_BLOCKED_UNTIL: float = 0.0       # epoch seconds
_PYTRENDS_BLOCK_LOGGED: bool   = False     # 같은 차단 구간 동안 로그 1회만
_BLOCK_COOLDOWN_SEC: int       = 3600      # 1 hour


# ── ★ 외부 SDK wall-clock 상한 헬퍼 (ERRORS [401]/[213] 동일 패턴 — pytrends
#   TrendReq 는 생성자 안에서 쿠키 조회 호출을 추가로 수행하고 자체 timeout=(10,30)+
#   retries=3 조합도 네트워크 상태에 따라 누적 지연될 수 있어, 지정한 timeout= 파라미터
#   만으론 단일 호출이 harness freeze 창(300초)을 넘겨 정지로 오판되는 것을 못 막는다.
#   ThreadPoolExecutor + fut.result(timeout=N) 으로 호출 자체에 강한 벽시계 상한을 건다.
#   내부 스레드는 leak 될 수 있으나 shutdown(wait=False)로 메인 루프를 블로킹하지 않음.
def _bounded(fn, *args, timeout: float = 90.0, default=None, **kwargs):
    exe = ThreadPoolExecutor(max_workers=1)
    try:
        fut = exe.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except _FutureTimeoutError:
            name = getattr(fn, "__name__", str(fn))
            print(f"[Google] {name} 응답 없음 {timeout:.0f}s 초과 — 스킵")
            return default
    finally:
        exe.shutdown(wait=False)


def _is_rate_limited_error(err: Exception) -> bool:
    """429 / 'too many requests' / 'rate' 키워드 감지."""
    msg = str(err).lower()
    return ("429" in msg) or ("too many" in msg) or ("rate limit" in msg)


def _pytrends_blocked() -> bool:
    """현재 쿨다운 중인지 확인."""
    return time.time() < _PYTRENDS_BLOCKED_UNTIL


def _mark_pytrends_blocked(reason: str = ""):
    """쿨다운 시작 — 1시간 동안 다시 호출 안 함."""
    global _PYTRENDS_BLOCKED_UNTIL, _PYTRENDS_BLOCK_LOGGED
    _PYTRENDS_BLOCKED_UNTIL = time.time() + _BLOCK_COOLDOWN_SEC
    if not _PYTRENDS_BLOCK_LOGGED:
        print(f"[Google IOT] ⚠️ Google rate limit 감지 — {_BLOCK_COOLDOWN_SEC//60}분간 호출 skip ({reason})")
        _PYTRENDS_BLOCK_LOGGED = True


def _reset_pytrends_block_log():
    """쿨다운 만료 시 로그 플래그 리셋 (다음 차단 발생 시 다시 1회 로깅)."""
    global _PYTRENDS_BLOCK_LOGGED
    if not _pytrends_blocked():
        _PYTRENDS_BLOCK_LOGGED = False


def _disable_pytrends_proxy(pt) -> None:
    """shared/pytrends_utils.disable_proxy 위임 — 하위 호환 alias."""
    try:
        from shared.pytrends_utils import disable_proxy
        disable_proxy(pt)
    except Exception:
        pass


def _safe_timeframe(days: int) -> str:
    """Google Trends 가 살아있는 timeframe 형식으로 매핑.

    ERRORS.md [19] — 2026-04-30 진단 결과 Google 이 `today N-d` 형식을 거절 (400).
    `now N-d` (실시간) 와 `today N-m` (월 단위) 는 정상.
      - 1~7일  → 'now {days}-d' (실시간 단위 — 시간별 데이터)
      - 8~30일 → 'today 1-m'  (월 단위 — 일별 데이터)
      - 31~90일 → 'today 3-m'
      - 그 외 → 'today 12-m' (1년)
    """
    days = max(1, int(days))
    if days <= 7:
        return f"now {days}-d"
    if days <= 30:
        return "today 1-m"
    if days <= 90:
        return "today 3-m"
    return "today 12-m"


def _build_payload_with_fallback(pt, kw_list: list, timeframe: str,
                                  geo: str = "KR", cat: int = 0) -> str:
    """shared/pytrends_utils.build_payload_with_fallback 위임 — 하위 호환 alias."""
    try:
        from shared.pytrends_utils import build_payload_with_fallback
        return build_payload_with_fallback(pt, kw_list, timeframe, geo=geo, cat=cat)
    except ImportError:
        # shared 미가용 시 인라인 fallback
        candidates = [timeframe, "now 1-d", "today 1-m"]
        last_err = None
        for tf in candidates:
            try:
                pt.build_payload(kw_list, cat=cat, timeframe=tf, geo=geo)
                return tf
            except Exception as e:
                last_err = e
        if last_err:
            raise last_err
        return timeframe

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
_RSS_URLS = [
    "https://trends.google.com/trending/rss?geo=KR",
    "https://trends.google.com/trending/rss?geo=KR&hl=ko",
]
_NS = {"ht": "https://trends.google.com/trending/rss"}


def _no_proxy_session() -> requests.Session:
    """시스템 프록시를 우회하는 requests 세션."""
    s = requests.Session()
    s.trust_env = False  # 시스템 환경변수의 proxy 설정 무시
    s.headers.update(_HEADERS)
    return s


# ── 방법 1: Google Trends RSS ──────────────────────────────────

def _fetch_google_rss(limit: int) -> list[str]:
    for url in _RSS_URLS:
        try:
            sess = _no_proxy_session()
            r = sess.get(url, timeout=15)
            r.raise_for_status()
            root  = ET.fromstring(r.text)
            items = root.findall(".//item")

            keywords = []
            for item in items:
                title = item.find("title")
                if title is not None and title.text:
                    keywords.append(title.text.strip())

            if len(keywords) < limit:
                for item in items:
                    for ni in item.findall("ht:news_item", _NS):
                        ni_title = ni.find("ht:news_item_title", _NS)
                        if ni_title is not None and ni_title.text:
                            words = re.findall(r"[가-힣a-zA-Z]{2,10}", ni_title.text)
                            for w in words[:2]:
                                if w not in keywords:
                                    keywords.append(w)
                    if len(keywords) >= limit:
                        break

            if keywords:
                print(f"[Google] RSS 수집 성공: {len(keywords)}개")
                return keywords[:limit]
        except Exception as e:
            print(f"[Google] RSS 오류 ({url}): {e}")
            _g_report("radar", e, module=__name__)
    return []


# ── 방법 2: pytrends trending_searches (proxy 비활성화) ────────

def _fetch_pytrends_trending(limit: int) -> list[str]:
    _reset_pytrends_block_log()
    if _pytrends_blocked():
        return []

    def _do():
        pt = TrendReq(
            hl="ko", tz=540,
            timeout=(10, 30), retries=_max_attempts(),
            requests_args={"verify": True},
        )
        _disable_pytrends_proxy(pt)
        df = pt.trending_searches(pn="south_korea")
        return df[0].tolist() if df is not None and not df.empty else []

    try:
        keywords = _bounded(_do, timeout=90.0, default=[])
        if keywords:
            print(f"[Google] pytrends trending_searches 성공: {len(keywords)}개")
            return keywords[:limit]
    except Exception as e:
        if _is_rate_limited_error(e):
            _mark_pytrends_blocked(reason="trending_searches")
        else:
            print(f"[Google] pytrends trending_searches 오류: {e}")
            _g_report("radar", e, module=__name__)
    return []


# ── 방법 3: pytrends realtime_trending_searches ───────────────

def _fetch_pytrends_realtime(limit: int) -> list[str]:
    _reset_pytrends_block_log()
    if _pytrends_blocked():
        return []

    def _do():
        pt = TrendReq(
            hl="ko", tz=540,
            timeout=(10, 30), retries=_max_attempts(),
            requests_args={"verify": True},
        )
        _disable_pytrends_proxy(pt)
        df = pt.realtime_trending_searches(pn="KR")
        if df is None or df.empty:
            return []
        col = "title" if "title" in df.columns else df.columns[0]
        return df[col].tolist()

    try:
        keywords = _bounded(_do, timeout=90.0, default=[])
        if keywords:
            print(f"[Google] pytrends realtime 성공: {len(keywords)}개")
            return keywords[:limit]
    except Exception as e:
        if _is_rate_limited_error(e):
            _mark_pytrends_blocked(reason="realtime_trending")
        else:
            print(f"[Google] pytrends realtime 오류: {e}")
            _g_report("radar", e, module=__name__)
    return []


# ── 방법 4: 네이버 뉴스 RSS 키워드 추출 ───────────────────────

def _fetch_naver_news_keywords(limit: int) -> list[str]:
    """네이버 뉴스 RSS → 헤드라인 핵심어 추출 (인증 불필요)."""
    # 네이버 현행 RSS 경로
    rss_feeds = [
        "https://news.naver.com/section/rss/105",   # IT
        "https://news.naver.com/section/rss/101",   # 경제
        "https://news.naver.com/section/rss/103",   # 사회
        "https://news.naver.com/section/rss/100",   # 정치
        "https://news.naver.com/section/rss/106",   # 생활문화
    ]
    keywords = []
    sess = _no_proxy_session()
    for feed_url in rss_feeds:
        try:
            r = sess.get(feed_url, timeout=10)
            if r.status_code != 200:
                continue
            root  = ET.fromstring(r.content)
            items = root.findall(".//item")
            for item in items[:15]:
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    # CDATA unwrap
                    text = re.sub(r"<!\[CDATA\[|\]\]>", "", title_el.text).strip()
                    words = re.findall(_KW_PATTERN, text)
                    for w in words[:3]:
                        if w not in keywords:
                            keywords.append(w)
            if len(keywords) >= limit:
                break
        except Exception as e:
            print(f"[Naver뉴스] RSS 오류 ({feed_url}): {e}")
            _g_report("radar", e, module=__name__)
            continue

    if keywords:
        print(f"[Naver뉴스] 키워드 추출 성공: {len(keywords)}개")
    return keywords[:limit]


# ── 방법 5: 네이버 검색 API — 뉴스 최신순 ─────────────────────

def _fetch_naver_api_keywords(limit: int) -> list[str]:
    """네이버 검색 API (뉴스)로 오늘 최신 헤드라인 키워드 추출."""
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    client_id     = os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return []

    broad_queries = ["오늘", "화제", "최신", "이슈", "뉴스"]
    keywords = []
    sess = _no_proxy_session()
    for q in broad_queries:
        try:
            r = sess.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers={
                    "X-Naver-Client-Id":     client_id,
                    "X-Naver-Client-Secret": client_secret,
                },
                params={"query": q, "display": 20, "sort": "date"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            items = r.json().get("items", [])
            for item in items:
                title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                words = re.findall(_KW_PATTERN, title)
                for w in words[:3]:
                    if w not in keywords:
                        keywords.append(w)
            if len(keywords) >= limit:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"[Naver API] 오류 ({q}): {e}")
            _g_report("radar", e, module=__name__)

    if keywords:
        print(f"[Naver API] 키워드 추출 성공: {len(keywords)}개")
    return keywords[:limit]


# ── 공개 API: 메인 함수 ────────────────────────────────────────

def get_trending_searches(limit: int = 30) -> list[str]:
    """
    한국 인기 검색어 수집 — 5단계 fallback:
    1) Google Trends RSS (프록시 우회)
    2) pytrends trending_searches (프록시 우회)
    3) pytrends realtime_trending_searches
    4) 네이버 뉴스 RSS
    5) 네이버 검색 API (보유 키 활용)
    """
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)

    steps = [
        ("Google Trends RSS",           lambda: _fetch_google_rss(limit)),
        ("pytrends trending_searches",  lambda: _fetch_pytrends_trending(limit)),
        ("pytrends realtime",           lambda: _fetch_pytrends_realtime(limit)),
        ("네이버 뉴스 RSS",              lambda: _fetch_naver_news_keywords(limit)),
        ("네이버 검색 API",              lambda: _fetch_naver_api_keywords(limit)),
    ]

    for name, fn in steps:
        _wd_beat()   # ★ fallback 단계 단위 진행 신호 — pytrends 재시도로 장시간 소요 가능 (freeze 오탐 방지)
        try:
            result = fn()
            if len(result) >= 10:
                return result
            if result:
                print(f"[RADAR] {name}: {len(result)}개 (부족 → 다음 시도)")
        except Exception as e:
            print(f"[RADAR] {name} 오류: {e}")
            _g_report("radar", e, module=__name__)

    print("[RADAR] 모든 수집 방법 실패 — 빈 결과 반환")
    return []


def get_interest_over_time(keywords: list[str], days: int = 30) -> dict[str, list[float]]:
    """
    pytrends interest_over_time으로 30일 검색 관심도 수집.
    DataLab 없을 때 velocity 계산 fallback으로 사용.
    반환: {keyword: [ratio...]} (0~100 정규화)
    """
    result: dict[str, list[float]] = {}

    # 429 쿨다운 중이면 호출 자체 skip — 불필요 재시도/로그 폭주 방지
    _reset_pytrends_block_log()
    if _pytrends_blocked():
        print(f"[Google IOT] 쿨다운 중 — skip ({len(keywords)}개 키워드)")
        return result

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)

    batches = [keywords[i:i+5] for i in range(0, min(len(keywords), 20), 5)]
    timeframe = _safe_timeframe(days)
    for batch in batches:
        _wd_beat()   # ★ 배치 단위 진행 신호 — freeze 오탐 방지 (pytrends 재시도로 장시간 소요 가능)

        def _do(_batch=batch):
            pt = TrendReq(hl="ko", tz=540, timeout=(10, 30), retries=_max_attempts())
            _disable_pytrends_proxy(pt)
            _build_payload_with_fallback(pt, _batch, timeframe, geo="KR", cat=0)
            return pt.interest_over_time()

        try:
            df = _bounded(_do, timeout=90.0, default=None)
            if df is None or df.empty:
                continue
            for kw in batch:
                if kw in df.columns:
                    vals = df[kw].tolist()
                    result[kw] = [float(v) for v in vals]
            time.sleep(1.2)
        except Exception as e:
            if _is_rate_limited_error(e):
                _mark_pytrends_blocked(reason="interest_over_time")
                break  # 남은 배치도 모두 skip
            print(f"[Google IOT] 배치 오류 {batch}: {e}")
            _g_report("radar", e, module=__name__)
            time.sleep(2.0)

    print(f"[Google IOT] interest_over_time 수집: {len(result)}개 키워드")
    return result


