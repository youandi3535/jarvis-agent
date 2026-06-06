"""금융 데이터 프로바이더 — yfinance 공식 API (허용)."""
from __future__ import annotations
import yfinance as yf
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.finance")


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
        lines = [f"[시장 데이터 — {theme} / {datetime.now().strftime('%Y-%m-%d')}]", ""]
        for name, ticker in _TICKERS.items():
            try:
                hist = yf.Ticker(ticker).history(period="2d")
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
