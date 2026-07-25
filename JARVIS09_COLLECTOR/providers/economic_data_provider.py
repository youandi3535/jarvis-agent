"""경제 데이터 프로바이더 — yfinance + investing.com 경제 캘린더.

★ 단일 진입점 이관 (2026-05-31):
   JARVIS02_WRITER/economic_poster.py 의 get_market_data / get_economic_calendar 본체 이관.
   호출자는 이 모듈만 import.
"""
from __future__ import annotations
import concurrent.futures as _cf
import logging
from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.economic")


# ★ yfinance 1.x 는 curl_cffi 세션만 지원 — requests.Session 주입 금지 (ERRORS [407])
# 타임아웃은 ThreadPoolExecutor + future.result(timeout=N) 로 대체.
def _yf_with_timeout(fn, timeout: int = 15):
    """yfinance 호출을 별도 스레드로 실행, timeout 초 내 완료 보장."""
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        return fut.result(timeout=timeout)


# ── 시장 데이터 (yfinance) ─────────────────────────────────────────
_MARKET_TICKERS = {
    # ★ 2-1 (2026-07-02): 국내 지수(코스피·코스닥) 추가 — 경제 브리핑이 '국내 증시·실생활
    #   영향'을 다루는데 대조할 국내 지표가 없던 갭 해소.
    "코스피":     "^KS11",
    "코스닥":     "^KQ11",
    "S&P500":     "^GSPC",
    "NASDAQ":     "^IXIC",
    "DOW":        "^DJI",
    "달러/원":    "KRW=X",
    "금":         "GC=F",
    "유가(WTI)":  "CL=F",
    "미국채10년": "^TNX",
}


def get_market_data() -> dict:
    """yfinance로 주요 시장 데이터 수집 (JARVIS09 단일 진입점).

    ★ 결측 판정은 `models.is_finite_num` 단일 소스 (NaN/Inf = 값 없음. 0 으로 채우지 않는다).

    ★ 2-1 (2026-07-02): 각 지표에 as_of(실제 종가 기준일) 부착 — 06:30 발행 시 미국
      지수는 전일 종가인데 '오늘'처럼 서술되던 시점 오류를 사실성 게이트가 검증 가능하게.
    """
    import yfinance as yf
    from ..models import is_finite_num as _finite   # 결측 판정 단일 소스
    result = {}
    for name, ticker in _MARKET_TICKERS.items():
        try:
            # ★ session= 제거 (ERRORS [407]): yfinance 1.x는 curl_cffi 세션만 허용
            #   타임아웃은 futures로 대체 (ERRORS [401] hang 방지 유지)
            # ★ NaN 발생원 차단 (사용자 박제 2026-07-25 — 경제 티스토리 발행 실패 근본원인):
            #   야후는 *비거래일·미체결 구간에도 행을 붙여* 주고 그 행의 Close 는 NaN 이다.
            #   종전 코드는 `period="2d"` 의 `iloc[-1]` 을 무조건 집어서, 토요일 새벽 선계산에선
            #   코스피·코스닥이 NaN 으로 수집됐다(로그: '12개 지표 수집 완료' — 실패로도 안 잡힘).
            #   그 NaN 이 ① 검증 gt → grounds() math.floor(NaN) ValueError → 게이트가 예외를
            #   삼켜 *진짜 사실을 '출처 미확인' 차단* ② 이미지 int(NaN) 크래시 로 터졌다.
            #   해법: 결측 행을 *버리고* 마지막 **유효 종가** 를 쓴다 → 지표를 잃지도, NaN 을
            #   만들지도 않는다. 창을 5d 로 넓혀 연휴에도 직전 거래일 대비 등락을 확보.
            def _fetch(t=ticker):
                return yf.Ticker(t).history(period="5d")
            hist = _yf_with_timeout(_fetch, timeout=15)
            closes = hist["Close"].dropna() if len(hist) else hist
            if len(closes) >= 2:
                prev = float(closes.iloc[-2])
                curr = float(closes.iloc[-1])
                as_of = closes.index[-1].strftime("%Y-%m-%d")
                chg = ((curr - prev) / prev * 100) if prev else 0.0
                result[name] = {"value": round(curr, 2), "change": round(chg, 2),
                                "as_of": as_of}
            elif len(closes) == 1:
                curr = float(closes.iloc[-1])
                as_of = closes.index[-1].strftime("%Y-%m-%d")
                result[name] = {"value": round(curr, 2), "change": 0.0, "as_of": as_of}
            else:
                log.warning(f"[EconData] {name} 유효 종가 0건 — 지표 제외(0 채움 금지)")
        except Exception as e:
            log.warning(f"[EconData] {name} 수집 실패: {e}")
    # ★ 한국은행 공식 지표 병합 (기준금리·달러원·CPI) — BOK_ECOS_KEY 미설정 시 자동 스킵
    try:
        from .bok_provider import get_bok_indicators
        for name, info in get_bok_indicators().items():
            # ★ NaN 은 truthy — 종전 `if info["value"] else 0.0` 를 그대로 통과해 결측이 샜다.
            if not _finite(info.get("value")):
                log.warning(f"[EconData] BOK {name} 결측 — 지표 제외")
                continue
            result[name] = {
                "value": float(info["value"]),
                "change": None,           # BOK 지표는 전일비 미제공
                "as_of": info["as_of"],
                "unit": info["unit"],
                "source": info["source"],
            }
    except Exception as e:
        log.warning(f"[EconData] BOK 지표 병합 실패: {e}")

    log.info(f"[EconData] 시장 데이터 수집 완료: {len(result)}개 지표")
    return result


# ── 경제 캘린더 (investing.com 공개 API) ────────────────────────────
def get_economic_calendar() -> list:
    """investing.com 경제 지표 일정 수집 (JARVIS09 단일 진입점)."""
    import requests
    from bs4 import BeautifulSoup
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://kr.investing.com/economic-calendar/",
    }
    try:
        res = requests.post(
            "https://kr.investing.com/economic-calendar/Service/getCalendarFilteredData",
            headers=headers,
            data={
                "country[]":     ["5", "72", "37"],
                "importance[]":  ["2", "3"],
                "timeZone":      "88",
                "timeFilter":    "timeRemain",
                "currentTab":    "today",
                "submitFilters": 1,
            },
            timeout=15,
        )
        if not res.ok or not res.content.strip():
            log.debug(f"[EconData] 경제 캘린더 응답 없음 (status={res.status_code}) — 건너뜀")
            return []
        try:
            payload = res.json()
        except Exception:
            log.debug("[EconData] 경제 캘린더 응답이 JSON이 아님 (Cloudflare 차단 추정) — 건너뜀")
            return []
        html = payload.get("data", "")
        soup = BeautifulSoup(html, "html.parser")
        events = []
        for row in soup.select("tr[id^='eventRowId']"):
            try:
                time_td  = row.select_one("td.first")
                name_td  = row.select_one("td.event a")
                actual   = row.select_one("td.act")
                forecast = row.select_one("td.fore")
                previous = row.select_one("td.prev")
                if not name_td:
                    continue
                events.append({
                    "time":     time_td.text.strip() if time_td else "",
                    "name":     name_td.text.strip(),
                    "actual":   actual.text.strip()   if actual   else "-",
                    "forecast": forecast.text.strip() if forecast else "-",
                    "previous": previous.text.strip() if previous else "-",
                })
            except Exception:
                continue
        log.info(f"[EconData] 경제 캘린더 수집 완료: {len(events[:8])}건")
        return events[:8]
    except Exception as e:
        log.info(f"[EconData] 경제 캘린더 수집 실패 (네트워크/차단): {type(e).__name__}")
        return []


# ── yfinance 티커 히스토리 (JARVIS06 차트용 공통 함수) ────────────────
def get_ticker_history(ticker: str, period: str = "2d", interval: str = "1d"):
    """단일 티커 히스토리 — JARVIS06_IMAGE 차트 생성 시 호출.

    직접 yfinance 사용 대신 이 함수를 통해 단일 진입점 준수.
    """
    import yfinance as yf
    try:
        def _fetch():
            return yf.Ticker(ticker).history(period=period, interval=interval)
        return _yf_with_timeout(_fetch, timeout=15)
    except Exception as e:
        log.warning(f"[EconData] 티커 히스토리 실패 ({ticker}): {e}")
        return None


def download_ticker(ticker: str, start: str, end: str = None, interval: str = "1d"):
    """yfinance.download 래퍼 — JARVIS06_IMAGE 차트 생성 시 호출."""
    import yfinance as yf
    try:
        def _fetch():
            kwargs = {"start": start, "interval": interval}
            if end:
                kwargs["end"] = end
            return yf.download(ticker, **kwargs)
        return _yf_with_timeout(_fetch, timeout=15)
    except Exception as e:
        log.warning(f"[EconData] download 실패 ({ticker}): {e}")
        return None


# ── SEO 문서 수집 (seo_learner 위임) ──────────────────────────────────
_SEO_SOURCES = {
    "Google 검색 가이드": "https://developers.google.com/search/docs/fundamentals/seo-starter-guide",
    "네이버 서치어드바이저 가이드": "https://searchadvisor.naver.com/guide/seo-help",
}

_FETCH_TIMEOUT = 10


def fetch_seo_docs() -> str:
    """SEO 가이드라인 문서 수집 — seo_learner 위임용 (JARVIS09 단일 진입점)."""
    import urllib.request
    blocks = []
    for name, url in _SEO_SOURCES.items():
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (JarvisCollector; educational)"},
            )
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            from bs4 import BeautifulSoup
            text = BeautifulSoup(raw, "html.parser").get_text(separator="\n")
            text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
            blocks.append(f"[{name}]\n{text[:3000]}")
            log.info(f"[EconData] SEO 문서 수집: {name} ({len(text)}자)")
        except Exception as e:
            log.warning(f"[EconData] SEO 문서 수집 실패 ({name}): {e}")
    return "\n\n".join(blocks)
