"""DART 전자공시 프로바이더 — OpenDART REST API (DART_API_KEY 필요).

수집 대상:
  - 테마 관련 기업 최근 공시 (사업보고서·반기·주요사항·발행공시)
  - 기업별 공시 제목·날짜 요약

API: https://opendart.fss.or.kr/api/
"""
from __future__ import annotations
import os
import httpx
from datetime import datetime, timedelta
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.dart")

_BASE = "https://opendart.fss.or.kr/api"


class DartProvider(BaseProvider):
    """DART 전자공시 — OpenDART REST API."""
    source_type = "dart"

    def __init__(self):
        self._api_key = os.getenv("DART_API_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _search_filings(self, corp_name: str, days: int = 180,
                        ptype: str = "A") -> list[dict]:
        """특정 기업 최근 공시 목록."""
        end_de   = datetime.now().strftime("%Y%m%d")
        bgn_de   = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        url = f"{_BASE}/list.json"
        try:
            wait_for(url)
            resp = httpx.get(url, params={
                "crtfc_key":  self._api_key,
                "corp_name":  corp_name,
                "bgn_de":     bgn_de,
                "end_de":     end_de,
                "pblntf_ty":  ptype,
                "page_count": "10",
                "sort":       "date",
                "sort_mthd":  "desc",
            }, timeout=10)
            data = resp.json()
            if data.get("status") == "000":
                return data.get("list", [])
        except Exception as e:
            log.debug(f"[DART] {corp_name} 공시 조회 실패: {e}")
        return []

    def collect(self, theme: str, sector: str = "", max_items: int = 10) -> list[RawDocument]:
        if not self._available:
            log.warning("[DART] DART_API_KEY 없음 — (https://opendart.fss.or.kr 무료 발급)")
            return []

        results: list[RawDocument] = []

        # 테마에서 기업명 후보 추출 (단어 분리)
        # "삼성전자 반도체" → ["삼성전자", "삼성", "반도체"]
        theme_words = []
        for w in theme.split():
            if len(w) >= 2:
                theme_words.append(w)
        # 원본 테마도 추가 (2글자 이상)
        if len(theme) >= 2 and theme not in theme_words:
            theme_words.insert(0, theme)

        # 각 키워드로 DART 검색 (중복 rno 제거)
        seen_rcept = set()
        all_filings: list[dict] = []
        for word in theme_words[:4]:
            # A(정기공시) + B(주요사항) 두 유형 검색
            # corp_name 검색 시 API 제한: 최대 3개월(90일)
            for ptype in ("A", "B"):
                filings = self._search_filings(word, days=90, ptype=ptype)
                for f in filings:
                    rcept = f.get("rcept_no", "")
                    if rcept and rcept not in seen_rcept:
                        seen_rcept.add(rcept)
                        all_filings.append(f)
            if len(all_filings) >= 20:
                break

        if not all_filings:
            log.info(f"[DART] '{theme}' 관련 공시 없음")
            return []

        # 기업별로 그룹핑해서 요약 문서 생성
        corp_map: dict[str, list[dict]] = {}
        for f in all_filings:
            corp = f.get("corp_name", "미상")
            corp_map.setdefault(corp, []).append(f)

        for corp_name, filings in list(corp_map.items())[:max_items]:
            lines = [f"[{corp_name} 전자공시 — {theme}]"]
            for f in filings[:5]:
                rdate = f.get("rcept_dt", "")
                title = f.get("report_nm", "")
                if rdate and title:
                    lines.append(f"• [{rdate}] {title}")

            if len(lines) > 1:
                corp_code = filings[0].get("corp_code", "")
                results.append(RawDocument(
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filings[0].get('rcept_no','')}",
                    source_type=self.source_type,
                    raw_text="\n".join(lines),
                    title=f"{corp_name} 전자공시",
                    extra={"theme": theme, "source": "dart", "corp": corp_name,
                           "corp_code": corp_code},
                ))

        log.info(f"[DART] '{theme}' 공시 {len(all_filings)}건 → {len(results)}개 기업 정리 완료")
        return results
