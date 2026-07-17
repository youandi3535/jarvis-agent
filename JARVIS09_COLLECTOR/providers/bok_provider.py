"""한국은행 ECOS API 프로바이더 — 공식 경제 통계 (무료, 키 필요).

★ 제공 지표 (각 지표는 최신값 + 최근 12개월 월별 시계열 동시 제공):
  - 기준금리 (722Y001 / 0101000) — 일별 → 월별 시계열
  - 달러/원 환율 매매기준율 (731Y001 / 0000001) — 일별 → 월별 시계열
  - 소비자물가지수 CPI (901Y009 / 0) — 월별 시계열

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
    """오늘 기준 검색 범위 반환. 일별=최근 365일, 월별=최근 12개월.

    ★ 라인차트 재료(시계열) 확보용 — 최신 1점이 아니라 최근 약 1년치를 요청한다.
    """
    today = date.today()
    if period == "D":
        start = today - timedelta(days=365)
        return start.strftime("%Y%m%d"), today.strftime("%Y%m%d")
    else:  # M
        # 월별: YYYYMM 형식 — 최근 12개월
        start = today.replace(day=1) - timedelta(days=365)
        return start.strftime("%Y%m"), today.strftime("%Y%m")


def _monthly_series(rows: list[dict]) -> list[dict]:
    """ECOS rows(일별/월별) → 월별 시계열 [{label:'YYYY-MM', value: float}].

    같은 달에 일별 관측이 여러 개면 그 달 마지막 관측값을 사용. 최근 12개월만 반환.
    최신 1점 붕괴를 막고 하류 chart_data 가 라인차트를 만들 재료를 제공한다.
    """
    by_month: dict[str, tuple[str, float]] = {}
    for r in rows:
        t = str(r.get("TIME", "")).strip()
        raw = str(r.get("DATA_VALUE", "")).replace(",", "").strip()
        if len(t) < 6 or not raw:
            continue
        ym = f"{t[:4]}-{t[4:6]}"
        try:
            val = float(raw)
        except ValueError:
            continue
        # rows 는 TIME 오름차순 — 같은 달이면 더 나중 관측이 덮어씀
        if ym not in by_month or t >= by_month[ym][0]:
            by_month[ym] = (t, val)
    series = [{"label": ym, "value": v} for ym, (_t, v) in sorted(by_month.items())]
    return series[-12:]


def fetch_indicators(key: str) -> dict[str, dict]:
    """BOK ECOS API 호출 → {이름: {value, unit, as_of}} 반환."""
    import requests
    result: dict[str, dict] = {}
    for name, stat_code, period, item_code, unit in _INDICATORS:
        try:
            start, end = _date_range(period)
            # 최근 약 1년치를 요청 (rows 오름차순 → rows[-1] 이 최신).
            # 500행 윈도우: 일별 1년치(≈250 영업일)도 최신값 누락 없이 포함.
            url = (
                f"{_BASE}/{key}/json/kr/1/500/{stat_code}/{period}"
                f"/{start}/{end}/{item_code}"
            )
            resp = requests.get(url, timeout=10)
            rows = resp.json().get("StatisticSearch", {}).get("row", [])
            if not rows:
                log.warning(f"[BOK] {name} 데이터 없음 (범위: {start}~{end})")
                continue
            series = _monthly_series(rows)   # ★ 시계열 보존 (최신 1점 붕괴 방지)
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
                "series": series,          # ★ 라인차트 재료 (연월·값) — 하위호환 추가 필드
            }
            log.info(f"[BOK] {name}: {val}{unit} ({as_of}) — 시계열 {len(series)}점")
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
            # ★ 최근 시계열(연월|값) 삽입 — 하류 chart_data 가 raw_text 에서 추출해
            #   라인차트를 만들 수 있게 한다 (최신 1점 스냅샷 붕괴 방지).
            series = info.get("series") or []
            if len(series) >= 2:
                lines.append(f"  [{name} 최근 시계열 (단위 {info['unit']})]")
                for pt in series:
                    lines.append(f"  {pt['label']}|{pt['value']}")
                lines.append("")
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
