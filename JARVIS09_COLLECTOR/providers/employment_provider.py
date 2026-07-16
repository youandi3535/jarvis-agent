"""고용노동부 고용통계 프로바이더 — KOSIS API 경유.

고용통계는 통계청(경제활동인구조사)이 공식 관리하므로
KOSIS_API_KEY 를 통해 고용·취업·실업 관련 테이블을 조회한다.

수집 항목:
  - 경제활동인구조사 (취업자수, 실업자수, 실업률, 고용률)
  - 산업별·연령별 취업 동향 (주제 관련성 있을 때)
"""
from __future__ import annotations
import logging
import os

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.employment")

# 고용 도메인 전용 KOSIS 검색 키워드
_SEARCH_KEYWORDS = ["경제활동인구", "취업자", "고용률", "실업률"]


class EmploymentProvider(BaseProvider):
    """고용 통계 — KOSIS API 경제활동인구조사 (KOSIS_API_KEY 필요)."""
    source_type = "employment"

    def __init__(self):
        self._api_key = os.getenv("KOSIS_API_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _api(self):
        try:
            from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
            ensure_lib("PublicDataReader", "PublicDataReader")
        except Exception:
            pass
        import io, sys
        from PublicDataReader import Kosis

        class _Silent:
            def __init__(self, inner): self._i = inner
            def get_data(self, *a, **kw):
                buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
                try:    return self._i.get_data(*a, **kw)
                finally: sys.stdout = old

        return _Silent(Kosis(self._api_key))

    def collect(self, theme: str, sector: str = "", max_items: int = 4) -> list[RawDocument]:
        if not self._available:
            log.info("[Employment] KOSIS_API_KEY 없음 — 스킵")
            return []

        try:
            api = self._api()
        except Exception as e:
            log.warning(f"[Employment] API 초기화 실패: {e}")
            return []

        results: list[RawDocument] = []
        for keyword in _SEARCH_KEYWORDS:
            if len(results) >= max_items:
                break
            try:
                tbls = api.get_data("KOSIS통합검색", searchNm=keyword)
                if tbls is None or getattr(tbls, "empty", True):
                    continue

                col_org = "기관ID" if "기관ID" in tbls.columns else "ORG_ID"
                col_tbl = "통계표ID" if "통계표ID" in tbls.columns else "TBL_ID"
                col_nm  = "통계표명" if "통계표명" in tbls.columns else "TBL_NM"

                for _, t in tbls.head(5).iterrows():
                    if len(results) >= max_items:
                        break
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

                    c_val  = next((c for c in ["수치", "DT"] if c in df.columns), None)
                    c_nm   = next((c for c in ["분류값명1", "ITM_NM"] if c in df.columns), None)
                    c_prd  = next((c for c in ["시점", "PRD_DE"] if c in df.columns), None)
                    c_unit = next((c for c in ["단위명", "UNIT_NM"] if c in df.columns), None)
                    if not c_val:
                        continue

                    lines = [f"[고용 통계 — {tbl_nm}]", ""]
                    for _, row in df.tail(20).iterrows():
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
                        title=f"고용 통계 (통계청) — {tbl_nm}",
                        extra={"theme": theme, "keyword": keyword},
                    ))
                    log.info(f"[Employment] '{tbl_nm}' {len(lines)}행 수집")

            except Exception as e:
                log.debug(f"[Employment] '{keyword}' 검색 실패: {e}")

        return results


__all__ = ["EmploymentProvider"]
