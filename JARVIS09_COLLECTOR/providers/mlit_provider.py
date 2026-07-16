"""국토교통부 부동산 통계 프로바이더.

부동산 관련 주제에서만 활성화.
수집 전략:
  1순위: KOSIS API — 아파트 매매·전세 가격지수 (KOSIS_API_KEY)
  2순위: data.go.kr 아파트 실거래가 요약 (PUBLIC_DATA_KEY)

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

# 아파트 매매 실거래가 API (data.go.kr)
_APT_TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

# 부동산 관련 주제 키워드
_REALESTATE_KEYWORDS = {"아파트", "부동산", "주택", "전세", "매매", "임대", "분양",
                        "청약", "재건축", "재개발", "집값", "토지"}


def _is_relevant(theme: str, sector: str) -> bool:
    combined = (theme + " " + sector).lower()
    return any(kw in combined for kw in _REALESTATE_KEYWORDS)


class MlitProvider(BaseProvider):
    """국토교통부 부동산 통계 — KOSIS 가격지수 + 실거래가 요약."""
    source_type = "mlit"

    def __init__(self):
        self._kosis_key  = os.getenv("KOSIS_API_KEY", "").strip()
        self._public_key = os.getenv("PUBLIC_DATA_KEY", "").strip()

    def collect(self, theme: str, sector: str = "", max_items: int = 3) -> list[RawDocument]:
        # 부동산 무관 주제는 스킵
        if not _is_relevant(theme, sector):
            log.debug(f"[MLIT] '{theme}' — 부동산 무관, 스킵")
            return []

        results: list[RawDocument] = []

        # 1순위: KOSIS 아파트 가격지수
        if self._kosis_key:
            docs = self._fetch_kosis(theme)
            results.extend(docs)

        # 2순위: 실거래가 API 요약
        if len(results) < max_items and self._public_key:
            docs = self._fetch_trade_summary(theme, max_items - len(results))
            results.extend(docs)

        if not results:
            log.info(f"[MLIT] '{theme}' — KOSIS_API_KEY 또는 PUBLIC_DATA_KEY 없음, 스킵")

        return results[:max_items]

    def _fetch_kosis(self, theme: str) -> list[RawDocument]:
        """KOSIS 에서 아파트 가격지수 검색."""
        try:
            from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
            ensure_lib("PublicDataReader", "PublicDataReader")
        except Exception:
            pass
        try:
            import io, sys
            from PublicDataReader import Kosis

            class _S:
                def __init__(self, i): self._i = i
                def get_data(self, *a, **kw):
                    buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
                    try:    return self._i.get_data(*a, **kw)
                    finally: sys.stdout = old

            api = _S(Kosis(self._kosis_key))
            tbls = api.get_data("KOSIS통합검색", searchNm="아파트매매가격지수")
            if tbls is None or getattr(tbls, "empty", True):
                tbls = api.get_data("KOSIS통합검색", searchNm="주택가격")
            if tbls is None or getattr(tbls, "empty", True):
                return []

            col_org = "기관ID" if "기관ID" in tbls.columns else "ORG_ID"
            col_tbl = "통계표ID" if "통계표ID" in tbls.columns else "TBL_ID"
            col_nm  = "통계표명" if "통계표명" in tbls.columns else "TBL_NM"

            results = []
            for _, t in tbls.head(5).iterrows():
                org_id = str(t.get(col_org, "") or "").strip()
                tbl_id = str(t.get(col_tbl, "") or "").strip()
                tbl_nm = str(t.get(col_nm,  "") or "").strip() or tbl_id
                if not org_id or not tbl_id:
                    continue
                try:
                    df = api.get_data("통계자료", orgId=org_id, tblId=tbl_id,
                                      prdSe="M", newEstPrdCnt="9", objL1="ALL")
                except Exception:
                    continue
                if df is None or getattr(df, "empty", True):
                    continue
                c_val = next((c for c in ["수치", "DT"] if c in df.columns), None)
                c_nm  = next((c for c in ["분류값명1", "ITM_NM"] if c in df.columns), None)
                c_prd = next((c for c in ["시점", "PRD_DE"] if c in df.columns), None)
                c_unit= next((c for c in ["단위명", "UNIT_NM"] if c in df.columns), None)
                if not c_val:
                    continue
                lines = [f"[국토교통부 부동산 통계 — {tbl_nm}]", ""]
                for _, row in df.tail(15).iterrows():
                    label = str(row[c_nm]).strip()  if c_nm  else ""
                    val   = str(row[c_val]).strip()
                    prd   = str(row[c_prd]).strip() if c_prd else ""
                    unit  = str(row[c_unit]).strip() if c_unit else ""
                    if not val or val == "nan":
                        continue
                    lines.append(f"• {prd} {label}: {val} {unit}".strip())
                if len(lines) < 4:
                    continue
                url = f"https://kosis.kr/statHtml/statHtml.do?orgId={org_id}&tblId={tbl_id}"
                results.append(RawDocument(
                    url=url, source_type=self.source_type,
                    raw_text="\n".join(lines),
                    title=f"국토교통부 부동산 통계 — {tbl_nm}",
                    extra={"theme": theme},
                ))
                log.info(f"[MLIT] KOSIS '{tbl_nm}' 수집")
                if len(results) >= 2:
                    break
            return results
        except Exception as e:
            log.debug(f"[MLIT] KOSIS 실패: {e}")
            return []

    def _fetch_trade_summary(self, theme: str, limit: int) -> list[RawDocument]:
        """data.go.kr 아파트 실거래가 — 최근 월 전국 거래 요약."""
        try:
            import requests

            today = date.today()
            deal_ym = today.replace(day=1).strftime("%Y%m")
            # 이전 달도 시도 (당월 데이터 미집계일 수 있음)
            params = {
                "serviceKey": self._public_key,
                "pageNo": "1",
                "numOfRows": "50",
                "DEAL_YMD": deal_ym,
            }
            resp = requests.get(_APT_TRADE_URL, params=params,
                                headers=_HEADERS, timeout=_TIMEOUT)

            if resp.status_code != 200:
                # 이전 달 재시도
                prev = today.replace(day=1) - timedelta(days=1)
                params["DEAL_YMD"] = prev.strftime("%Y%m")
                resp = requests.get(_APT_TRADE_URL, params=params,
                                    headers=_HEADERS, timeout=_TIMEOUT)
                if resp.status_code != 200:
                    return []

            from xml.etree import ElementTree as ET
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            if not items:
                return []

            lines = [f"[국토교통부 아파트 매매 실거래가 — {params['DEAL_YMD']}]", ""]
            count = 0
            total_val = 0.0
            for item in items[:limit * 5]:
                name  = (item.findtext("아파트") or "").strip()
                area  = (item.findtext("전용면적") or "").strip()
                price = (item.findtext("거래금액") or "").strip().replace(",", "")
                loc   = (item.findtext("법정동") or "").strip()
                floor = (item.findtext("층") or "").strip()
                if not name or not price:
                    continue
                try:
                    total_val += float(price)
                    count += 1
                except ValueError:
                    pass
                lines.append(f"• {loc} {name} ({area}㎡, {floor}층): {price}만원")
                if count >= 10:
                    break

            if count == 0:
                return []
            avg = int(total_val / count) if count else 0
            lines += ["", f"※ 위 {count}건 평균 거래가: {avg:,}만원", "출처: 국토교통부 실거래가 공개시스템"]

            return [RawDocument(
                url="https://rt.molit.go.kr",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title=f"국토교통부 아파트 실거래가 ({params['DEAL_YMD']})",
                extra={"theme": theme, "deal_ym": params["DEAL_YMD"]},
            )]
        except Exception as e:
            log.debug(f"[MLIT] 실거래가 API 실패: {e}")
            return []


__all__ = ["MlitProvider"]
