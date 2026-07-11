"""한국은행 ECOS API 프로바이더 — 공식 경제 통계 (무료, 키 필요).

★ 제공 지표:
  - 기준금리 (722Y001 / 0101000) — 일별 최신값
  - 달러/원 환율 매매기준율 (731Y001 / 0000001) — 일별 최신값
  - 소비자물가지수 CPI (901Y009 / 0) — 월별 최신값

★ 환경변수: BOK_ECOS_KEY (.env)
  미설정 시 프로바이더 자동 스킵 (경고만).
"""
from __future__ import annotations
import logging
import os
from datetime import date, timedelta

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.bok")

_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (이름, 통계표코드, 주기, 항목코드, 단위설명)
_INDICATORS = [
    ("한국은행 기준금리", "722Y001", "D", "0101000", "%"),
    ("달러/원 환율(매매기준율)", "731Y001", "D", "0000001", "원"),
    ("소비자물가지수(CPI)", "901Y009", "M", "0", "(2020=100)"),
]


def _date_range(period: str) -> tuple[str, str]:
    """오늘 기준 검색 범위 반환. 일별=최근 60일, 월별=최근 6개월."""
    today = date.today()
    if period == "D":
        start = today - timedelta(days=60)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    else:  # M
        # 월별: YYYYMM 형식
        start = today.replace(day=1) - timedelta(days=180)
        return start.strftime("%Y%m"), today.strftime("%Y%m")


def fetch_indicators(key: str) -> dict[str, dict]:
    """BOK ECOS API 호출 → {이름: {value, unit, as_of}} 반환."""
    import requests
    result: dict[str, dict] = {}
    for name, stat_code, period, item_code, unit in _INDICATORS:
        try:
            start, end = _date_range(period)
            # 최근 1건만 — 종료일 기준으로 역방향 정렬이 안 되므로 충분한 범위 요청 후 마지막 row 사용
            url = (
                f"{_BASE}/{key}/json/kr/1/100/{stat_code}/{period}"
                f"/{start}/{end}/{item_code}"
            )
            resp = requests.get(url, timeout=10)
            rows = resp.json().get("StatisticSearch", {}).get("row", [])
            if not rows:
                log.warning(f"[BOK] {name} 데이터 없음 (범위: {start}~{end})")
                continue
            latest = rows[-1]
            val = latest.get("DATA_VALUE", "")
            time_str = latest.get("TIME", "")
            # 날짜 파싱: YYYYMMDD → YYYY-MM-DD, YYYYMM → YYYY-MM
            if len(time_str) == 8:
                as_of = f"{time_str[:4]}-{time_str[4:6]}-{time_str[6:]}"
            elif len(time_str) == 6:
                as_of = f"{time_str[:4]}-{time_str[4:]}"
            else:
                as_of = time_str
            result[name] = {
                "value": val,
                "unit": unit,
                "as_of": as_of,
                "source": "한국은행 ECOS",
            }
            log.info(f"[BOK] {name}: {val}{unit} ({as_of})")
        except Exception as e:
            log.warning(f"[BOK] {name} 수집 실패: {e}")
    return result


def get_bok_indicators() -> dict[str, dict]:
    """환경변수 자동 로드 후 지표 반환. 키 미설정 시 빈 dict."""
    key = os.getenv("BOK_ECOS_KEY", "").strip()
    if not key:
        log.info("[BOK] BOK_ECOS_KEY 미설정 — 스킵")
        return {}
    return fetch_indicators(key)


class BokProvider(BaseProvider):
    source_type = "bok_official"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        """경제 브리핑용 한국은행 공식 지표 수집."""
        indicators = get_bok_indicators()
        if not indicators:
            return []
        lines = ["[한국은행 공식 경제 지표]", ""]
        for name, info in indicators.items():
            lines.append(f"• {name}: {info['value']}{info['unit']} (기준일: {info['as_of']})")
        lines.append("")
        lines.append("출처: 한국은행 경제통계시스템(ECOS) 공식 데이터")
        return [RawDocument(
            url="https://ecos.bok.or.kr",
            source_type=self.source_type,
            raw_text="\n".join(lines),
            title="한국은행 공식 경제 지표",
            extra={"indicators": indicators, "theme": theme},
        )]


__all__ = ["BokProvider", "get_bok_indicators", "fetch_indicators"]
