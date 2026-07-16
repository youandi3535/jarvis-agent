"""금융감독원(FSS) 금융통계 프로바이더 — FISIS 공개 통계 수집.

FSS 금융통계정보시스템(FISIS)의 공개 HTML 페이지에서
은행·보험·증권 등 금융권 핵심 지표를 스크래핑한다.

FSS_API_KEY 가 있으면 FISIS Open API 를 우선 사용.
없으면 FSS 보도자료 RSS 와 금융통계 요약 페이지 스크래핑.
"""
from __future__ import annotations
import logging
import os
import re

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.fss")

_TIMEOUT = 12
_HEADERS = {"User-Agent": "jarvis-research/1.0 (mailto:youandi3535@naver.com)"}

# FSS FISIS Open API 기본 URL (FSS_API_KEY 필요)
_FISIS_BASE = "https://fisis.fss.or.kr/openapi"

# FSS 보도자료 RSS (키 불필요)
_PRESS_RSS = "https://www.fss.or.kr/fss/kr/bbs/list.do?bbsid=1230064&menuld=4&type=5"

# 주요 공개 금융통계 JSON 엔드포인트
_PUBLIC_ENDPOINTS = [
    ("은행 대출잔액",  "https://fisis.fss.or.kr/fis/lifeFinance/lifeFinanceList.do?menuId=M300010"),
    ("금융시장 현황", "https://fisis.fss.or.kr/fis/statistics/statisticsList.do?menuId=M310000"),
]

# 금융주제 키워드 (이 주제일 때만 수집)
_RELEVANT_KEYWORDS = {"은행", "금융", "대출", "예금", "보험", "증권", "신용", "부채", "가계"}


def _is_relevant(theme: str, sector: str) -> bool:
    combined = (theme + " " + sector).lower()
    return any(kw in combined for kw in _RELEVANT_KEYWORDS)


class FssProvider(BaseProvider):
    """금융감독원 금융통계 — FISIS 스크래핑 + FSS_API_KEY 선택."""
    source_type = "fss"

    def __init__(self):
        self._api_key = os.getenv("FSS_API_KEY", "").strip()

    def collect(self, theme: str, sector: str = "", max_items: int = 3) -> list[RawDocument]:
        # 금융 무관 주제는 스킵 (다른 프로바이더가 더 적합)
        if not _is_relevant(theme, sector):
            log.debug(f"[FSS] '{theme}' — 금융 무관 주제, 스킵")
            return []

        results: list[RawDocument] = []

        # 1순위: FSS_API_KEY 있으면 FISIS Open API
        if self._api_key:
            docs = self._fetch_via_api(theme)
            results.extend(docs)

        # 2순위: FSS 보도자료 RSS 스크래핑
        if len(results) < max_items:
            docs = self._fetch_press_releases(theme, max_items - len(results))
            results.extend(docs)

        return results[:max_items]

    def _fetch_via_api(self, theme: str) -> list[RawDocument]:
        """FISIS Open API 호출 — bank 예금/대출 잔액."""
        try:
            import requests
            # 예금은행 대출잔액 최근 6개 (엔드포인트는 기본 제공 지표 조회)
            url = (f"{_FISIS_BASE}/bank/deposit_loan"
                   f"?auth={self._api_key}&output=json&lang=kor")
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []
            data = resp.json()
            rows = data.get("result", {}).get("list", [])
            if not rows:
                return []
            lines = ["[금융감독원 FISIS — 은행 예금·대출 공식 통계]", ""]
            for row in rows[:10]:
                name = row.get("name") or row.get("item_nm", "")
                val  = row.get("value") or row.get("data", "")
                unit = row.get("unit", "")
                dt   = row.get("date") or row.get("as_of", "")
                if name and val:
                    lines.append(f"• {name}: {val} {unit} ({dt})")
            if len(lines) < 3:
                return []
            lines.append("\n출처: 금융감독원 금융통계정보시스템(FISIS)")
            return [RawDocument(
                url=f"{_FISIS_BASE}",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title="금융감독원 FISIS 예금·대출 통계",
                extra={"theme": theme},
            )]
        except Exception as e:
            log.debug(f"[FSS] API 실패: {e}")
            return []

    def _fetch_press_releases(self, theme: str, limit: int) -> list[RawDocument]:
        """FSS 보도자료 목록 스크래핑."""
        try:
            import requests
            from xml.etree import ElementTree as ET

            # FSS 보도자료 RSS
            rss_url = "https://www.fss.or.kr/fss/kr/bbs/list.do?bbsid=1230064&menuld=4&type=rss"
            resp = requests.get(rss_url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []

            root = ET.fromstring(resp.content)
            ns = {"": ""}
            items = root.findall(".//item")
            results = []
            keywords = set((theme + " 금융 감독 FSS").split())
            for item in items[:20]:
                if len(results) >= limit:
                    break
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link")  or "").strip()
                desc  = (item.findtext("description") or "").strip()
                # 제목이나 설명에 관련 키워드 포함 여부
                combined = (title + desc).lower()
                if not any(kw in combined for kw in _RELEVANT_KEYWORDS):
                    continue
                text = f"[금융감독원 보도자료]\n제목: {title}\n내용: {desc}\n출처: FSS"
                results.append(RawDocument(
                    url=link or "https://www.fss.or.kr",
                    source_type=self.source_type,
                    raw_text=text,
                    title=f"FSS 보도자료 — {title}",
                    extra={"theme": theme},
                ))
        except Exception as e:
            log.debug(f"[FSS] 보도자료 스크래핑 실패: {e}")
            results = []

        return results


__all__ = ["FssProvider"]
