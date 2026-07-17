"""한국은행 ECOS API 프로바이더 — 거시경제 통계 (BOK_ECOS_KEY 필요).

수집 대상:
  - 기준금리 (722Y001)
  - 소비자물가지수 CPI (901Y009)
  - GDP 성장률 (200Y001)
  - 환율 (731Y004)
  - 수출입 동향 (403Y001)
  - 실업률 (901Y028)

API 문서: https://ecos.bok.or.kr/api/#/
"""
from __future__ import annotations
import os
import httpx
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.ecos")

_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (통계코드, 아이템코드, 이름, 주기)
_STAT_ITEMS = [
    ("722Y001", "0101000", "기준금리",        "M"),  # 월별
    ("901Y009", "0",       "소비자물가(CPI)",  "M"),
    ("731Y001", "0000001", "원/달러 환율(매매기준율)", "D"),
    ("403Y001", "X",       "수출금액(달러)",   "M"),
    ("901Y028", "1813000", "실업률",           "M"),
]
_PERIOD = 36   # ★ 6→36 상향 2026-07-17 (거시지표 최근 N개월 — 더 긴 시계열 수집)


class EcosProvider(BaseProvider):
    """한국은행 ECOS — 거시경제 공식 통계."""
    source_type = "ecos"

    def __init__(self):
        self._api_key = os.getenv("BOK_ECOS_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _fetch_stat(self, stat_code: str, item_code: str, name: str, period: str) -> str:
        """통계 1개 조회 → 텍스트 요약."""
        from datetime import datetime, timedelta
        now = datetime.now()
        # 최근 N개월 범위 계산
        end_ym = now.strftime("%Y%m")
        start = now - timedelta(days=_PERIOD * 31)
        start_ym = start.strftime("%Y%m")

        url = (f"{_BASE}/{self._api_key}/json/kr/1/{_PERIOD + 2}/"
               f"{stat_code}/{period}/{start_ym}/{end_ym}/{item_code}")
        try:
            wait_for(url)
            resp = httpx.get(url, timeout=10)
            if resp.status_code != 200:
                return ""
            rows = resp.json().get("StatisticSearch", {}).get("row", [])
            if not rows:
                return ""
            # 최신 N개 수치 요약
            lines = [f"[{name} — 최근 {len(rows)}개월]"]
            for r in rows[-_PERIOD:]:
                ym   = r.get("TIME", "")
                val  = r.get("DATA_VALUE", "")
                unit = r.get("UNIT_NAME", "")
                if ym and val:
                    lines.append(f"  {ym}: {val} {unit}")
            return "\n".join(lines)
        except Exception as e:
            log.debug(f"[ECOS] {name} 조회 실패: {e}")
            return ""

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._available:
            log.warning("[ECOS] BOK_ECOS_KEY 없음 — 건너뜀 (https://ecos.bok.or.kr 무료 발급)")
            return []

        results: list[RawDocument] = []
        all_lines = [f"[한국은행 ECOS 거시경제 지표 — {theme} 관련]", ""]

        for stat_code, item_code, name, period in _STAT_ITEMS:
            text = self._fetch_stat(stat_code, item_code, name, period)
            if text:
                all_lines.append(text)
                all_lines.append("")
                log.info(f"[ECOS] {name} 수집 완료")

        if len(all_lines) > 3:
            combined = "\n".join(all_lines)
            results.append(RawDocument(
                url="https://ecos.bok.or.kr/",
                source_type=self.source_type,
                raw_text=combined,
                title=f"한국은행 ECOS 거시경제 지표 ({theme})",
                extra={"theme": theme, "source": "bok_ecos"},
            ))
            log.info(f"[ECOS] 거시경제 지표 수집 완료 ({len(all_lines)}줄)")

        return results
