"""금융 데이터 프로바이더 — yfinance 공식 API (허용)."""
from __future__ import annotations
import yfinance as yf
import requests
from requests.adapters import HTTPAdapter
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.finance")


class _TimeoutAdapter(HTTPAdapter):
    """yfinance HTTP 호출에 강제 타임아웃 적용 (ERRORS [401] — hang 방지)."""
    def __init__(self, timeout: int = 10, **kw):
        self._timeout = timeout
        super().__init__(**kw)

    def send(self, request, *args, **kwargs):
        kwargs.setdefault("timeout", self._timeout)
        return super().send(request, *args, **kwargs)


def _make_session(timeout: int = 10) -> requests.Session:
    sess = requests.Session()
    adapter = _TimeoutAdapter(timeout=timeout)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


class FinanceProvider(BaseProvider):
    source_type = "finance"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        """테마 관련 시장 데이터 수집. 주로 경제 브리핑용."""
        results = []
        _TICKERS = {
            "S&P500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI",
            "달러/원": "KRW=X", "금": "GC=F", "유가(WTI)": "CL=F", "미국채10년": "^TNX",
        }
        from datetime import datetime
        sess = _make_session(timeout=10)  # ★ 10초 타임아웃 (ERRORS [401])
        lines = [f"[시장 데이터 — {theme} / {datetime.now().strftime('%Y-%m-%d')}]", ""]
        for name, ticker in _TICKERS.items():
            try:
                hist = yf.Ticker(ticker, session=sess).history(period="2d")
                if len(hist) >= 2:
                    prev = hist["Close"].iloc[-2]
                    curr = hist["Close"].iloc[-1]
                    chg = (curr - prev) / prev * 100
                    direction = "상승" if chg > 0 else ("하락" if chg < 0 else "보합")
                    lines.append(f"• {name}: 현재 {curr:.2f}, 전일 대비 {chg:+.2f}% {direction}")
            except Exception:
                pass
        if len(lines) > 2:
            lines.append("")
            lines.append(f"총 {len(lines)-3}개 주요 지표 기준 시장 현황. 데이터 출처: yfinance 공식 API.")
            results.append(RawDocument(
                url="yfinance://market_data",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title=f"{theme} 시장 데이터",
                extra={"theme": theme},
            ))
        return results
