"""금융감독원(FSS) 금융통계 프로바이더.

FSS 보도자료 페이지 HTML 스크래핑 + FSS_API_KEY 로 FISIS 통계 조회.
금융·은행 관련 주제에서만 활성화.
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

# 금융 관련 주제 키워드
_RELEVANT_KEYWORDS = {"은행", "금융", "대출", "예금", "보험", "증권", "신용", "부채",
                      "가계부채", "금감원", "저축", "이자", "금리"}


def _is_relevant(theme: str, sector: str) -> bool:
    combined = (theme + " " + sector).lower()
    return any(kw in combined for kw in _RELEVANT_KEYWORDS)


class FssProvider(BaseProvider):
    """금융감독원 금융통계 — FSS 보도자료 스크래핑 + FISIS API (선택)."""
    source_type = "fss"

    def __init__(self):
        self._api_key = os.getenv("FSS_API_KEY", "").strip()

    def collect(self, theme: str, sector: str = "", max_items: int = 3) -> list[RawDocument]:
        if not _is_relevant(theme, sector):
            log.debug(f"[FSS] '{theme}' — 금융 무관, 스킵")
            return []

        results: list[RawDocument] = []

        # 1순위: FISIS API (FSS_API_KEY 있을 때)
        if self._api_key:
            docs = self._fetch_fisis(theme)
            results.extend(docs)

        # 2순위: FSS 보도자료 HTML 스크래핑
        if len(results) < max_items:
            docs = self._scrape_press(theme, max_items - len(results))
            results.extend(docs)

        return results[:max_items]

    def _fetch_fisis(self, theme: str) -> list[RawDocument]:
        """FISIS Open API — 주요 금융통계 조회."""
        try:
            import requests
            # FISIS 금융통계 주요 지표 (은행 예금·대출 총액)
            url = (f"https://fisis.fss.or.kr/openapi/fr-stat/data"
                   f"?auth={self._api_key}&lang=kor&output=json")
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                log.debug(f"[FSS] FISIS API {resp.status_code}")
                return []
            data = resp.json()
            # 응답 형태는 API 버전마다 다를 수 있음 — 최대한 유연하게 파싱
            items = (data.get("result", {}).get("list")
                     or data.get("data", {}).get("list")
                     or data.get("list", []))
            if not items:
                return []
            lines = ["[금융감독원 FISIS 금융통계]", ""]
            for item in items:   # ★ 10건컷 폐지 2026-07-17 (FISIS 통계 항목 전량 기록)
                nm  = item.get("name") or item.get("item_nm") or item.get("nm", "")
                val = item.get("value") or item.get("data") or item.get("val", "")
                unit = item.get("unit", "")
                dt   = item.get("date") or item.get("as_of", "")
                if nm and val:
                    lines.append(f"• {nm}: {val} {unit} ({dt})".strip())
            if len(lines) < 3:
                return []
            lines.append("\n출처: 금융감독원 금융통계정보시스템(FISIS)")
            return [RawDocument(
                url="https://fisis.fss.or.kr",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title="금융감독원 FISIS 금융통계",
                extra={"theme": theme},
            )]
        except Exception as e:
            log.debug(f"[FSS] FISIS API 실패: {e}")
            return []

    def _scrape_press(self, theme: str, limit: int) -> list[RawDocument]:
        """FSS 보도자료 목록 스크래핑 — 제목 + 날짜 추출."""
        try:
            import requests
            url = "https://www.fss.or.kr/fss/bbs/B0000188/list.do?menuNo=200218&pageIndex=1"
            resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []

            # 제목 태그 추출 (FSS 보도자료 목록 구조)
            text = resp.text
            # <td class="title"> 또는 <a> 태그에서 제목 추출
            titles = re.findall(
                r'<td[^>]*class="[^"]*title[^"]*"[^>]*>.*?<a[^>]*>([^<]{5,})</a>',
                text, re.DOTALL
            )
            if not titles:
                # 대안: href에 bbsId 포함 링크 텍스트
                titles = re.findall(
                    r'href="[^"]*bbsId=B0000188[^"]*"[^>]*>([^<]{5,100})</a>',
                    text
                )

            # 날짜 추출
            dates = re.findall(r'(\d{4}[-./]\d{2}[-./]\d{2})', text)

            results = []
            keywords = (theme + " " + " ".join(_RELEVANT_KEYWORDS)).split()
            for i, title in enumerate(titles[:20]):
                title = title.strip()
                if not title or len(title) < 5:
                    continue
                # 관련 키워드 포함 여부 확인
                if not any(kw in title for kw in _RELEVANT_KEYWORDS):
                    continue
                date_str = dates[i] if i < len(dates) else ""
                text_body = f"[금융감독원 보도자료]\n제목: {title}\n날짜: {date_str}\n출처: 금융감독원(FSS)"
                results.append(RawDocument(
                    url="https://www.fss.or.kr",
                    source_type=self.source_type,
                    raw_text=text_body,
                    title=f"FSS 보도자료 — {title[:40]}",
                    extra={"theme": theme},
                ))
                if len(results) >= limit:
                    break

            log.info(f"[FSS] 보도자료 {len(results)}건 수집")
            return results
        except Exception as e:
            log.debug(f"[FSS] 스크래핑 실패: {e}")
            return []


__all__ = ["FssProvider"]
