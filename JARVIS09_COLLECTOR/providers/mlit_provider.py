"""국토교통부 부동산 통계 프로바이더.

부동산 관련 주제에서만 활성화.
수집 전략:
  1순위: KOSIS API — 아파트 매매·전세 가격지수 (KOSIS_API_KEY)
  2순위: data.go.kr 아파트 실거래가 요약 (PUBLIC_DATA_KEY + LAWD_CD 주요 지역)

PUBLIC_DATA_KEY: data.go.kr 공공데이터포털 인증키
  발급: https://data.go.kr → 마이페이지 → 인증키 관리 (무료)
"""
from __future__ import annotations
import logging
import os
from datetime import date, timedelta

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.mlit")

_TIMEOUT = 12
_HEADERS = {"User-Agent": "jarvis-research/1.0 (mailto:youandi3535@naver.com)"}
_APT_TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

# 주요 지역 법정동 코드 (서울 주요 구)
_MAJOR_LAWD = [
    ("서울 강남구", "11680"), ("서울 서초구", "11650"),
    ("서울 송파구", "11710"), ("서울 마포구", "11440"),
]

# 부동산 관련 주제 키워드
_REALESTATE_KEYWORDS = {"아파트", "부동산", "주택", "전세", "매매", "임대", "분양",
                        "청약", "재건축", "재개발", "집값", "토지"}

# KOSIS 검색 키워드 — 부동산 전담
_KOSIS_KEYWORDS = ["아파트매매가격지수", "주택가격", "부동산가격지수"]


def _is_relevant(theme: str, sector: str) -> bool:
    combined = (theme + " " + sector).lower()
    return any(kw in combined for kw in _REALESTATE_KEYWORDS)


class MlitProvider(BaseProvider):
    """국토교통부 부동산 통계 — KOSIS 가격지수 + 실거래가 요약."""
    source_type = "mlit"

    def __init__(self):
        from .kosis_provider import KosisProvider
        self._kosis      = KosisProvider()
        self._public_key = os.getenv("PUBLIC_DATA_KEY", "").strip()

    def collect(self, theme: str, sector: str = "", max_items: int = 3) -> list[RawDocument]:
        if not _is_relevant(theme, sector):
            log.debug(f"[MLIT] '{theme}' — 부동산 무관, 스킵")
            return []

        results: list[RawDocument] = []

        # 1순위: KOSIS 아파트 가격지수 (위임)
        if self._kosis._available:
            for kw in _KOSIS_KEYWORDS:
                if len(results) >= max_items:
                    break
                docs = self._kosis.collect(kw, sector, max_items=2)
                for d in docs:
                    d.source_type = self.source_type
                    d.title = d.title.replace("KOSIS 통계청", "국토교통부·한국부동산원")
                results.extend(docs)

        # 2순위: 실거래가 API (PUBLIC_DATA_KEY + LAWD_CD)
        if len(results) < max_items and self._public_key:
            docs = self._fetch_trade_summary(theme, max_items - len(results))
            results.extend(docs)

        if not results:
            log.info(f"[MLIT] '{theme}' — 데이터 없음 (키 확인: KOSIS={self._kosis._available}, PUBLIC={bool(self._public_key)})")

        return results[:max_items]

    def _fetch_trade_summary(self, theme: str, limit: int) -> list[RawDocument]:
        """data.go.kr 아파트 실거래가 — 주요 지역 최근 거래 요약."""
        try:
            import requests

            today = date.today()
            # 당월 데이터는 집계 중일 수 있으므로 전전월 기준
            deal_ym = (today.replace(day=1) - timedelta(days=32)).strftime("%Y%m")

            all_lines = [f"[국토교통부 아파트 실거래가 — {deal_ym}]", ""]
            total_count = 0

            for area_name, lawd_cd in _MAJOR_LAWD[:limit]:
                params = {
                    "serviceKey": self._public_key,
                    "pageNo": "1",
                    "numOfRows": "10",
                    "DEAL_YMD": deal_ym,
                    "LAWD_CD": lawd_cd,
                }
                try:
                    resp = requests.get(_APT_TRADE_URL, params=params,
                                        headers=_HEADERS, timeout=_TIMEOUT)
                    from xml.etree import ElementTree as ET
                    root = ET.fromstring(resp.content)
                    items = root.findall(".//item")
                    if not items:
                        continue
                    all_lines.append(f"[{area_name}]")
                    for item in items:   # ★ 3건컷 폐지 2026-07-17 (실거래 API 반환분 전량 기록)
                        name  = (item.findtext("아파트") or "").strip()
                        area  = (item.findtext("전용면적") or "").strip()
                        price = (item.findtext("거래금액") or "").strip().replace(",", "")
                        floor = (item.findtext("층") or "").strip()
                        if name and price:
                            all_lines.append(f"  • {name} ({area}㎡ {floor}층): {price}만원")
                            total_count += 1
                except Exception as e:
                    log.debug(f"[MLIT] {area_name} 실패: {e}")

            if total_count == 0:
                return []

            all_lines.append("\n출처: 국토교통부 실거래가 공개시스템(rt.molit.go.kr)")
            return [RawDocument(
                url="https://rt.molit.go.kr",
                source_type=self.source_type,
                raw_text="\n".join(all_lines),
                title=f"국토교통부 아파트 실거래가 ({deal_ym})",
                extra={"theme": theme, "deal_ym": deal_ym},
            )]
        except Exception as e:
            log.debug(f"[MLIT] 실거래가 실패: {e}")
            return []


__all__ = ["MlitProvider"]
