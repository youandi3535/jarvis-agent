"""KRX 한국거래소 프로바이더 — pykrx (API 키 불필요).

수집 전략:
  - 주요 KOSPI/KOSDAQ 종목 고정 리스트 + get_market_ohlcv_by_date (안정적 API 사용)
  - 테마 키워드와 종목명 매칭 → 관련 종목 시세 반환
  - get_market_ticker_list / get_market_cap_by_ticker 는 날짜 의존성으로 불안정 → 사용 안 함
"""
from __future__ import annotations
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.krx")

# 주요 KOSPI/KOSDAQ 종목 (티커: 종목명) — 시가총액 상위권 대표 종목
_MAJOR_STOCKS: dict[str, str] = {
    # 반도체·IT
    "005930": "삼성전자",      "000660": "SK하이닉스",    "042700": "한미반도체",
    "009150": "삼성전기",      "018260": "삼성에스디에스", "035420": "NAVER",
    "035720": "카카오",        "030200": "KT",
    # 자동차·부품
    "005380": "현대차",        "000270": "기아",          "012330": "현대모비스",
    "094280": "현대글로비스",
    # 배터리·소재
    "006400": "삼성SDI",       "051910": "LG화학",        "003670": "포스코퓨처엠",
    "010130": "고려아연",
    # 바이오·헬스
    "207940": "삼성바이오로직스", "068270": "셀트리온",    "000100": "유한양행",
    "326030": "SK바이오사이언스",
    # 조선·방산
    "009540": "HD한국조선해양", "012450": "한화에어로스페이스", "267250": "HD현대",
    # 금융·보험
    "055550": "신한지주",      "105560": "KB금융",        "086790": "하나금융지주",
    "032830": "삼성생명",      "000810": "삼성화재",
    # 철강·에너지
    "005490": "POSCO홀딩스",   "096770": "SK이노베이션",  "034730": "SK",
    "028260": "삼성물산",
    # 항공·물류
    "003490": "대한항공",      "020560": "아시아나항공",
    # 엔터·게임
    "251270": "넷마블",        "112040": "위메이드",      "041510": "에스엠",
    # KOSDAQ 대표
    "247540": "에코프로비엠",  "086520": "에코프로",      "373220": "LG에너지솔루션",
    "011200": "HMM",
}

# 한글 숫자 포함 등락률 포맷
def _fmt_chg(v) -> str:
    try:
        return f"{float(v):+.1f}%"
    except Exception:
        return "N/A"


def _recent_dates() -> tuple[str, str]:
    """최근 7영업일 범위 (start, end)."""
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


class KrxProvider(BaseProvider):
    """KRX 공식 데이터 — pykrx 안정 API 사용."""
    source_type = "krx"

    def collect(self, theme: str, sector: str = "", max_items: int = 8) -> list[RawDocument]:
        results: list[RawDocument] = []
        try:
            from pykrx import stock as krx_stock
        except ImportError:
            log.warning("[KRX] pykrx 미설치: pip install pykrx")
            return []

        start_dt, end_dt = _recent_dates()
        theme_words = [w for w in theme.split() if len(w) >= 2]
        if not theme_words and len(theme) >= 2:
            theme_words = [theme]

        # ── 1. 테마 관련 종목 시세 ───────────────────────────────────────
        matched_tickers = {
            ticker: name
            for ticker, name in _MAJOR_STOCKS.items()
            if any(w in name for w in theme_words)
        }

        if matched_tickers:
            lines = [f"['{theme}' 관련 종목 최근 시세]",
                     "종목명(티커)|최신종가|등락률|거래량"]
            ok = 0
            for ticker, name in list(matched_tickers.items())[:8]:
                try:
                    df = krx_stock.get_market_ohlcv_by_date(start_dt, end_dt, ticker)
                    if df is not None and not df.empty and "종가" in df.columns:
                        row   = df.iloc[-1]
                        price = int(row["종가"])
                        vol   = int(row.get("거래량", 0))
                        chg   = _fmt_chg(row.get("등락률", 0))
                        date  = df.index[-1].strftime("%Y-%m-%d")
                        lines.append(f"{name}({ticker})|{date} {price:,}원|{chg}|{vol:,}주")
                        ok += 1
                except Exception as e:
                    log.debug(f"[KRX] {name} 시세 실패: {e}")

            if ok > 0:
                results.append(RawDocument(
                    url=f"krx://theme_{theme}",
                    source_type=self.source_type,
                    raw_text="\n".join(lines),
                    title=f"KRX '{theme}' 관련 종목 시세",
                    extra={"theme": theme, "source": "krx_theme"},
                ))
                log.info(f"[KRX] '{theme}' 관련 {ok}종목 시세 수집")

        # ── 2. 주요 종목 시세 요약 (테마 매칭 없을 때 대표 종목) ──────────
        _REPRESENTATIVE = ["005930", "000660", "005380", "006400", "068270"]
        lines = [f"[KRX 주요 종목 최근 시세 ({end_dt})]",
                 "종목명(티커)|최신종가|등락률|거래량"]
        ok = 0
        for ticker in _REPRESENTATIVE:
            name = _MAJOR_STOCKS.get(ticker, ticker)
            try:
                df = krx_stock.get_market_ohlcv_by_date(start_dt, end_dt, ticker)
                if df is not None and not df.empty and "종가" in df.columns:
                    row   = df.iloc[-1]
                    price = int(row["종가"])
                    vol   = int(row.get("거래량", 0))
                    chg   = _fmt_chg(row.get("등락률", 0))
                    date  = df.index[-1].strftime("%Y-%m-%d")
                    lines.append(f"{name}({ticker})|{date} {price:,}원|{chg}|{vol:,}주")
                    ok += 1
            except Exception as e:
                log.debug(f"[KRX] 대표종목 {name} 실패: {e}")

        if ok > 0:
            results.append(RawDocument(
                url="krx://major_stocks",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title=f"KRX 주요 종목 시세 ({end_dt})",
                extra={"theme": theme, "source": "krx_major"},
            ))
            log.info(f"[KRX] 주요 {ok}종목 시세 수집")

        log.info(f"[KRX] 총 {len(results)}건 수집 완료")
        return results[:max_items]
