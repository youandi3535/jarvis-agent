"""금융투자협회(KOFIA) 채권 수익률 프로바이더.

BOK ECOS API 를 통해 국고채·회사채·CD 금리를 수집한다.
채권 수익률은 금리·투자 관련 주제에 핵심 지표이므로 항상 유용하다.

BOK ECOS 계열 코드:
  817Y002 — 채권유통수익률(장내, 평균)
    · 010101000 = 국고채 1년
    · 010102000 = 국고채 3년
    · 010103000 = 국고채 5년
    · 010104000 = 국고채 10년
    · 010301000 = 회사채(AA-) 3년
    · 010501000 = CD(91일)
"""
from __future__ import annotations
import logging
import os
from datetime import date, timedelta

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.kofia")

_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# 817Y002 = 시장금리(일별) — 주기 "D", 아이템코드는 ECOS 실검증값
_BOND_SERIES = [
    ("콜금리(1일)",     "817Y002", "D", "010102000", "연%"),
    ("국고채(3년)",     "817Y002", "D", "010200000", "연%"),
    ("회사채(AA-, 3년)", "817Y002", "D", "010300000", "연%"),
    ("통안증권(91일)",  "817Y002", "D", "010400000", "연%"),
]


class KofiaProvider(BaseProvider):
    """채권 수익률 — BOK ECOS 국고채·회사채·CD 금리 (BOK_ECOS_KEY 필요)."""
    source_type = "kofia"

    def __init__(self):
        self._key = os.getenv("BOK_ECOS_KEY", "").strip()

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._key:
            log.info("[KOFIA] BOK_ECOS_KEY 없음 — 스킵")
            return []

        import requests
        today = date.today()
        start_d = (today - timedelta(days=10)).strftime("%Y%m%d")
        end_d   = today.strftime("%Y%m%d")

        lines = ["[채권 수익률 — 한국은행 ECOS 공식 데이터]", ""]
        fetched = 0
        for name, stat, period, item, unit in _BOND_SERIES:
            try:
                url = (f"{_BASE}/{self._key}/json/kr/1/5/"
                       f"{stat}/{period}/{start_d}/{end_d}/{item}")
                resp = requests.get(url, timeout=10)
                rows = resp.json().get("StatisticSearch", {}).get("row", [])
                if not rows:
                    continue
                latest = rows[-1]
                val    = latest.get("DATA_VALUE", "")
                time_s = latest.get("TIME", "")
                # 일별: YYYYMMDD → YYYY-MM-DD
                as_of  = f"{time_s[:4]}-{time_s[4:6]}-{time_s[6:]}" if len(time_s) == 8 else time_s
                lines.append(f"• {name}: {val}{unit} ({as_of})")
                fetched += 1
            except Exception as e:
                log.debug(f"[KOFIA] {name} 실패: {e}")

        if fetched == 0:
            log.info("[KOFIA] 채권 데이터 없음")
            return []

        lines += ["", "출처: 한국은행 ECOS / 금융투자협회 채권 수익률"]
        return [RawDocument(
            url="https://www.kofiabond.or.kr",
            source_type=self.source_type,
            raw_text="\n".join(lines),
            title="채권 수익률 (국고채·회사채·CD)",
            extra={"theme": theme},
        )]


__all__ = ["KofiaProvider"]
