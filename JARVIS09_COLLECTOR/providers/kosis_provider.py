"""통계청 KOSIS API 프로바이더 — 산업·고용·생산 통계 (KOSIS_API_KEY 필요).

수집 전략:
  1. 통계자료 검색(statisticsSearch.do) → 테마 관련 통계표 목록 수집
  2. 각 통계표 최근 데이터 조회 (statisticsParameterData.do)

API: https://kosis.kr/openapi/
"""
from __future__ import annotations
import os
import httpx
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.kosis")

_SEARCH_URL = "https://kosis.kr/openapi/statisticsSearch.do"
_DATA_URL   = "https://kosis.kr/openapi/Param/statisticsParameterData.do"

# 주제별 검색 키워드 (테마와 섹터를 조합해 관련 통계 탐색)
_STAT_QUERIES_BASE = [
    "생산지수", "실업률", "수출입", "설비투자", "소매판매",
]


class KosisProvider(BaseProvider):
    """통계청 KOSIS — 국가 산업·고용 공식 통계."""
    source_type = "kosis"

    def __init__(self):
        self._api_key = os.getenv("KOSIS_API_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _search_tables(self, keyword: str) -> list[dict]:
        """키워드로 통계표 검색 → 테이블 목록 반환."""
        try:
            wait_for(_SEARCH_URL)
            resp = httpx.get(_SEARCH_URL, params={
                "method":       "getList",
                "apiKey":       self._api_key,
                "vwCd":         "MT_ZTITLE",
                "parentListId": "MT_ZTITLE",
                "searchNm":     keyword,
                "format":       "json",
                "jsonVD":       "Y",
            }, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception as e:
            log.debug(f"[KOSIS] 테이블 검색 실패 ({keyword}): {e}")
        return []

    def _fetch_data(self, org_id: str, tbl_id: str,
                    itm_id: str = "T", obj_l1: str = "ALL") -> list[dict]:
        """통계표 최근 데이터 조회."""
        try:
            wait_for(_DATA_URL)
            resp = httpx.get(_DATA_URL, params={
                "method":       "getList",
                "apiKey":       self._api_key,
                "itmId":        itm_id,
                "objL1":        obj_l1,
                "format":       "json",
                "jsonVD":       "Y",
                "prdSe":        "M",
                "newEstPrdCnt": "6",
                "orgId":        org_id,
                "tblId":        tbl_id,
            }, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
        except Exception as e:
            log.debug(f"[KOSIS] 데이터 조회 실패 ({tbl_id}): {e}")
        return []

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._available:
            log.warning("[KOSIS] KOSIS_API_KEY 없음 — (https://kosis.kr/openapi 무료 발급)")
            return []

        results: list[RawDocument] = []
        all_lines: list[str] = [f"[통계청 KOSIS — {theme} 관련 통계]", ""]

        # 1단계: 테마 + 기본 통계 키워드로 관련 통계표 검색
        search_queries = [theme, sector] + _STAT_QUERIES_BASE[:3] if sector else [theme] + _STAT_QUERIES_BASE[:4]
        found_tables: list[dict] = []
        seen_tbl: set[str] = set()

        for q in search_queries[:5]:
            if not q or len(q) < 2:
                continue
            tables = self._search_tables(q)
            for t in tables[:3]:
                tbl_id = t.get("TBL_ID", "")
                if tbl_id and tbl_id not in seen_tbl:
                    seen_tbl.add(tbl_id)
                    found_tables.append(t)
            if len(found_tables) >= 8:
                break

        if not found_tables:
            log.info(f"[KOSIS] '{theme}' 관련 통계표 없음")
            return []

        # 2단계: 발견된 통계표 정보를 텍스트로 변환 + 가능한 것은 데이터도 조회
        stats_info: list[str] = []
        data_fetched = 0

        for table in found_tables[:6]:
            org_id = table.get("ORG_ID", "101")
            tbl_id = table.get("TBL_ID", "")
            tbl_nm = table.get("TBL_NM", "")
            stat_nm = table.get("STAT_NM", "")
            end_prd = table.get("END_PRD_DE", "")
            contents_snippet = (table.get("CONTENTS") or "")[:80]

            if not tbl_id:
                continue

            # 통계표 메타 정보 텍스트 추가 (데이터 조회 실패해도 이건 항상 추가)
            stats_info.append(f"[{tbl_nm}] ({stat_nm}, 최신: {end_prd})")
            if contents_snippet:
                stats_info.append(f"  → {contents_snippet}")

            # 실제 데이터 조회 시도 (최대 3개 테이블)
            if data_fetched < 3:
                rows = self._fetch_data(org_id, tbl_id)
                if rows:
                    data_lines = [f"\n[{tbl_nm} — 최근 데이터]"]
                    for row in rows[:6]:
                        prd  = row.get("PRD_DE", "")
                        itm  = row.get("ITM_NM", "")
                        val  = row.get("DT", "")
                        unit = row.get("UNIT_NAME", "")
                        if prd and val:
                            data_lines.append(f"  {prd} {itm}: {val} {unit}".strip())
                    if len(data_lines) > 1:
                        stats_info.extend(data_lines)
                        data_fetched += 1
                        log.info(f"[KOSIS] {tbl_nm} 데이터 {len(rows)}건 수집")

        if stats_info:
            all_lines.extend(stats_info)
            results.append(RawDocument(
                url="https://kosis.kr/",
                source_type=self.source_type,
                raw_text="\n".join(all_lines),
                title=f"통계청 KOSIS — {theme} 관련 통계 ({len(found_tables)}개 통계표)",
                extra={"theme": theme, "source": "kosis",
                       "tables_found": len(found_tables),
                       "data_fetched": data_fetched},
            ))
            log.info(f"[KOSIS] {len(found_tables)}개 통계표 발견, {data_fetched}개 실데이터 수집")

        return results
