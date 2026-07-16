"""DART 전자공시 프로바이더 — OpenDART REST API (DART_API_KEY 필요).

수집 대상:
  - 테마 관련 기업 최근 공시 (사업보고서·반기·주요사항·발행공시)
  - 기업별 공시 제목·날짜 요약

API: https://opendart.fss.or.kr/api/
"""
from __future__ import annotations
import os
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
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

    def _fetch_company_info(self, corp_code: str) -> dict:
        """기업 기본 정보 조회 — 대표자·상장구분·소재지·설립일 (DART company.json)."""
        try:
            wait_for(f"{_BASE}/company.json")
            resp = httpx.get(f"{_BASE}/company.json", params={
                "crtfc_key": self._api_key,
                "corp_code": corp_code,
            }, timeout=8)
            data = resp.json()
            if data.get("status") == "000":
                return data
        except Exception as e:
            log.debug(f"[DART] company.json 실패 ({corp_code}): {e}")
        return {}

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

        _cls_label = {"Y": "코스피(유가증권)", "K": "코스닥", "N": "코넥스", "E": "기타"}

        # 기업 개요 병렬 prefetch (company.json — LLM 호출 0, 순수 HTTP)
        target_corps = list(corp_map.items())[:max_items]
        corp_codes = {
            filings[0].get("corp_code", ""): corp_name
            for corp_name, filings in target_corps
            if filings[0].get("corp_code", "")
        }
        company_info_cache: dict[str, dict] = {}
        if corp_codes:
            with ThreadPoolExecutor(max_workers=min(len(corp_codes), 8)) as pool:
                future_to_code = {
                    pool.submit(self._fetch_company_info, code): code
                    for code in corp_codes
                }
                for future in as_completed(future_to_code):
                    code = future_to_code[future]
                    try:
                        info = future.result()
                        if info:
                            company_info_cache[code] = info
                    except Exception as e:
                        log.debug(f"[DART] company.json 병렬 실패 ({code}): {e}")
            log.info(f"[DART] company.json 병렬 fetch 완료 — {len(company_info_cache)}/{len(corp_codes)}건")

        for corp_name, filings in target_corps:
            lines = [f"[{corp_name} 전자공시 — {theme}]", "[최근 공시]"]
            for f in filings[:5]:
                rdate = f.get("rcept_dt", "")
                title = f.get("report_nm", "")
                if rdate and title:
                    lines.append(f"• [{rdate}] {title}")

            corp_code = filings[0].get("corp_code", "")

            # 기업 개요 — 병렬 prefetch 결과 사용
            info = company_info_cache.get(corp_code, {})
            if info:
                lines.append("")
                lines.append("[기업 개요]")
                if info.get("ceo_nm"):
                    lines.append(f"• 대표자: {info['ceo_nm']}")
                if info.get("corp_cls"):
                    lines.append(f"• 시장: {_cls_label.get(info['corp_cls'], info['corp_cls'])}")
                if info.get("adres"):
                    lines.append(f"• 소재지: {info['adres']}")
                est = info.get("est_dt", "")
                if est and len(est) == 8:
                    lines.append(f"• 설립일: {est[:4]}년 {int(est[4:6])}월 {int(est[6:])}일")
                if info.get("hm_url"):
                    lines.append(f"• 홈페이지: {info['hm_url']}")

            if len(lines) > 2:
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
