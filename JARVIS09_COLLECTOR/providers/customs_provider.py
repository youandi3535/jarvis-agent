"""관세청 수출입 통계 프로바이더 — KOSIS API 를 통한 무역 데이터 수집.

KOSIS(통계청)는 관세청 수출입 통계를 공식 등재하므로
KOSIS_API_KEY 하나로 관세청 수출입 테이블을 조회할 수 있다.

★ 수출입 통계는 주제와 무관하게 항상 유용한 경제 맥락 지표이므로
  theme 키워드를 "수출입무역" 로 고정해 도메인 특화 검색을 수행.
"""
from __future__ import annotations
import logging
import os

from . import BaseProvider
from ..models import RawDocument

log = logging.getLogger("jarvis.collector.customs")

# 관세청 전용 KOSIS 검색 키워드 — 주제와 무관하게 고정
_SEARCH_KEYWORDS = ["수출입무역통계", "수출금액", "수입금액", "무역수지"]


class CustomsProvider(BaseProvider):
    """관세청 수출입 통계 — KOSIS API 경유 (KOSIS_API_KEY 필요)."""
    source_type = "customs"

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
        from PublicDataReader import Kosis

        class _Silent:
            def __init__(self, inner):
                self._i = inner
            def get_data(self, *a, **kw):
                import io, sys
                buf = io.StringIO()
                old = sys.stdout
                sys.stdout = buf
                try:
                    return self._i.get_data(*a, **kw)
                finally:
                    sys.stdout = old

        return _Silent(Kosis(self._api_key))

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._available:
            log.info("[Customs] KOSIS_API_KEY 없음 — 스킵")
            return []

        try:
            api = self._api()
        except Exception as e:
            log.warning(f"[Customs] API 초기화 실패: {e}")
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

                for _, t in tbls.head(6).iterrows():
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
                    # 텍스트 직렬화
                    c_nm  = next((c for c in ["분류값명1", "ITM_NM", "ITM_NM_ENG"] if c in df.columns), None)
                    c_val = next((c for c in ["수치", "DT"] if c in df.columns), None)
                    c_prd = next((c for c in ["시점", "PRD_DE"] if c in df.columns), None)
                    c_unit = next((c for c in ["단위명", "UNIT_NM"] if c in df.columns), None)
                    if not c_val:
                        continue
                    lines = [f"[관세청 수출입통계 — {tbl_nm}]", ""]
                    for _, row in df.tail(20).iterrows():
                        label = str(row[c_nm]).strip() if c_nm else ""
                        val   = str(row[c_val]).strip()
                        prd   = str(row[c_prd]).strip() if c_prd else ""
                        unit  = str(row[c_unit]).strip() if c_unit else ""
                        if not val or val in ("nan", ""):
                            continue
                        lines.append(f"• {prd} {label}: {val} {unit}".strip())
                    text = "\n".join(lines)
                    if len(lines) < 4:
                        continue
                    url = f"https://kosis.kr/statHtml/statHtml.do?orgId={org_id}&tblId={tbl_id}"
                    results.append(RawDocument(
                        url=url,
                        source_type=self.source_type,
                        raw_text=text,
                        title=f"관세청 수출입통계 — {tbl_nm}",
                        extra={"theme": theme, "keyword": keyword},
                    ))
                    log.info(f"[Customs] '{tbl_nm}' {len(lines)}행 수집")
            except Exception as e:
                log.debug(f"[Customs] '{keyword}' 검색 실패: {e}")

        return results


__all__ = ["CustomsProvider"]
