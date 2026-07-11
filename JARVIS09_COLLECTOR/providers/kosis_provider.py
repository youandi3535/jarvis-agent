"""통계청 KOSIS API 프로바이더 — 국가통계 *실제 값* 수집 (KOSIS_API_KEY 필요).

★ 사용자 박제 2026-06-30 — "어떻게든 데이터를 받아와". KOSIS 는 표마다 항목(itmId)·분류(objL)
  코드가 달라 generic 호출로는 값이 안 온다. 그래서:
    1) KOSIS통합검색 → 주제 관련 통계표 목록
    2) 통계표설명(getMeta, 분류항목) → 표별 itmId·objL 코드 *동적 해석*
    3) 통계자료(statisticsParameterData) → 해석한 코드로 *실제 값* 조회
  무료 라이브러리 PublicDataReader 를 자동 설치(lib_bootstrap)해 KOSIS API 복잡성을 처리.

API: https://kosis.kr/openapi/
"""
from __future__ import annotations
import os
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.kosis")

# 조회 시점 타입 우선순위 (A=연간, M=월간, F=부정기/조사 — 가장 흔한 3종만; 속도) — 표마다 다름
_PRD_TRY = ("A", "M", "F")
_MAX_OBJ_CODES = 30   # 분류값 코드 과다 요청 방지


class _SilentApi:
    """PublicDataReader get_data() 래퍼 — 내부 print 억제, log.debug 로 전환."""
    def __init__(self, inner):
        self._inner = inner

    def get_data(self, *args, **kwargs):
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = self._inner.get_data(*args, **kwargs)
        msg = buf.getvalue().strip()
        if msg:
            log.debug(f"[KOSIS] {msg[:120]}")
        return result


class KosisProvider(BaseProvider):
    """통계청 KOSIS — 국가 공식 통계의 *실제 값*."""
    source_type = "kosis"

    def __init__(self):
        self._api_key = os.getenv("KOSIS_API_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _api(self):
        """PublicDataReader Kosis 인스턴스 (없으면 자동 설치). _SilentApi 래퍼로 감싸 print 억제."""
        try:
            from JARVIS09_COLLECTOR.lib_bootstrap import ensure_lib
            ensure_lib("PublicDataReader", "PublicDataReader")
        except Exception as e:
            log.warning(f"[KOSIS] PublicDataReader 자동설치 스킵: {e}")
        from PublicDataReader import Kosis
        return _SilentApi(Kosis(self._api_key))

    # ── 표별 코드 동적 해석 (메타) ────────────────────────────────────────
    def _resolve_codes(self, api, org_id: str, tbl_id: str):
        """통계표설명(분류항목) → (itmId, {objL1:codes, objL2:codes, ...}). 실패 시 (None, {})."""
        try:
            meta = api.get_data("통계표설명", detail_service_name="분류항목",
                                orgId=org_id, tblId=tbl_id)
        except Exception as e:
            log.debug(f"[KOSIS] 메타 실패 {tbl_id}: {e}")
            return None, {}
        if meta is None or getattr(meta, "empty", True):
            return None, {}
        # 컬럼명: translate=True → '분류ID','분류값ID' (raw fallback: CD_ID/ITM_ID)
        col_cls = "분류ID" if "분류ID" in meta.columns else ("CD_ID" if "CD_ID" in meta.columns else None)
        col_val = "분류값ID" if "분류값ID" in meta.columns else ("ITM_ID" if "ITM_ID" in meta.columns else None)
        if not col_cls or not col_val:
            return None, {}
        items, obj_levels = [], {}
        for cid in meta[col_cls].dropna().unique():
            codes = meta[meta[col_cls] == cid][col_val].dropna().astype(str).tolist()
            codes = [c for c in codes if c]
            if not codes:
                continue
            if str(cid).upper() == "ITEM":
                items = codes
            else:
                obj_levels[str(cid)] = codes[:_MAX_OBJ_CODES]
        itm_id = "+".join(items[:15]) if items else "ALL"
        obj_params = {}
        for i, cid in enumerate(sorted(obj_levels.keys()), 1):
            obj_params[f"objL{i}"] = "+".join(obj_levels[cid])
        if not obj_params:
            obj_params["objL1"] = "ALL"
        return itm_id, obj_params

    def _fetch_values(self, api, org_id: str, tbl_id: str):
        """해석한 코드로 통계자료 값 조회. 성공 시 DataFrame, 실패 시 None."""
        itm_id, obj_params = self._resolve_codes(api, org_id, tbl_id)
        if itm_id is None:
            return None
        for prdse in _PRD_TRY:
            try:
                df = api.get_data("통계자료", orgId=org_id, tblId=tbl_id,
                                  itmId=itm_id, prdSe=prdse, newEstPrdCnt="9", **obj_params)
            except Exception:
                df = None
            if df is not None and not getattr(df, "empty", True):
                return df
        return None

    @staticmethod
    def _df_to_text(tbl_nm: str, df) -> str:
        """값 DataFrame → 깨끗한 텍스트 (라벨: 값 단위) — 실행기 LLM 추출이 차트화하기 쉽게.

        ★ 단위 행별 개별 처리 (사용자 박제 2026-07-11 — ERRORS: 자산총계에 "개" 붙는 버그):
          첫 번째 행 단위를 전체에 적용하던 방식 → 각 행의 단위명 컬럼을 개별 읽어 출력.
          이로써 회사수(개)와 자산총계(백만원)가 섞인 표에서 각각 올바른 단위 표시.
        """
        c_v1 = "분류값명1" if "분류값명1" in df.columns else None
        c_v2 = "분류값명2" if "분류값명2" in df.columns else None
        c_prd = "수록시점" if "수록시점" in df.columns else ("PRD_DE" if "PRD_DE" in df.columns else None)
        c_dt = "수치값" if "수치값" in df.columns else ("DT" if "DT" in df.columns else None)
        c_un = "단위명" if "단위명" in df.columns else ("UNIT_NM" if "UNIT_NM" in df.columns else None)
        if not c_dt:
            return ""
        # 헤더용 대표 단위 (가장 빈도 높은 것)
        _header_unit = ""
        try:
            if c_un:
                _counts = df[c_un].dropna().value_counts()
                _header_unit = str(_counts.index[0]) if not _counts.empty else ""
        except Exception:
            _header_unit = ""
        lines = [f"[KOSIS 통계표: {tbl_nm}{(' (단위: ' + _header_unit + ')') if _header_unit else ''}]"]
        for _, row in df.head(40).iterrows():
            lab_parts = []
            if c_v1 and str(row.get(c_v1, "")).strip():
                lab_parts.append(str(row.get(c_v1)).strip())
            if c_v2 and str(row.get(c_v2, "")).strip():
                lab_parts.append(str(row.get(c_v2)).strip())
            if c_prd and str(row.get(c_prd, "")).strip():
                lab_parts.append(str(row.get(c_prd)).strip())
            val = str(row.get(c_dt, "")).strip()
            if not val or val in ("-", "nan"):
                continue
            # ★ 행별 단위 개별 읽기
            row_unit = ""
            try:
                row_unit = str(row.get(c_un, "") or "").strip() if c_un else ""
            except Exception:
                row_unit = _header_unit
            lines.append(f"  {' · '.join(lab_parts)}: {val} {row_unit}".rstrip())
        return "\n".join(lines) if len(lines) > 1 else ""

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        if not self._available:
            log.warning("[KOSIS] KOSIS_API_KEY 없음 — (https://kosis.kr/openapi 무료 발급)")
            return []
        try:
            api = self._api()
        except Exception as e:
            log.warning(f"[KOSIS] PublicDataReader 사용 불가: {e}")
            return []

        # 1) 통합검색 → 주제 관련 통계표
        try:
            tbls = api.get_data("KOSIS통합검색", searchNm=theme)
        except Exception as e:
            log.warning(f"[KOSIS] 검색 실패 '{theme}': {e}")
            return []
        if tbls is None or getattr(tbls, "empty", True):
            log.info(f"[KOSIS] '{theme}' 관련 통계표 없음")
            return []

        col_org = "기관ID" if "기관ID" in tbls.columns else "ORG_ID"
        col_tbl = "통계표ID" if "통계표ID" in tbls.columns else "TBL_ID"
        col_nm = "통계표명" if "통계표명" in tbls.columns else ("TBL_NM" if "TBL_NM" in tbls.columns else col_tbl)
        col_url = "통계표조회URL" if "통계표조회URL" in tbls.columns else ("TBL_VIEW_URL" if "TBL_VIEW_URL" in tbls.columns else None)

        results: list[RawDocument] = []
        for _, t in tbls.head(10).iterrows():           # 표 후보 (풍부 vs 속도 균형)
            if len(results) >= max(4, min(max_items, 8)):
                break
            org_id = str(t.get(col_org, "") or "").strip()
            tbl_id = str(t.get(col_tbl, "") or "").strip()
            tbl_nm = str(t.get(col_nm, "") or "").strip() or tbl_id
            if not org_id or not tbl_id:
                continue
            df = self._fetch_values(api, org_id, tbl_id)
            if df is None:
                continue
            text = self._df_to_text(tbl_nm, df)
            if not text:
                continue
            url = ""
            if col_url and str(t.get(col_url, "")).strip():
                url = str(t.get(col_url)).strip()
            if not url:
                url = f"https://kosis.kr/statHtml/statHtml.do?orgId={org_id}&tblId={tbl_id}"
            results.append(RawDocument(
                url=url, source_type=self.source_type, raw_text=text,
                title=f"KOSIS 통계청 — {tbl_nm}",
                extra={"theme": theme, "source": "kosis", "org_id": org_id, "tbl_id": tbl_id},
            ))
            log.info(f"[KOSIS] '{tbl_nm}' 실제 값 {text.count(chr(10))}행 수집")

        if not results:
            log.info(f"[KOSIS] '{theme}' 값 조회 0 (표는 있으나 값 미해석)")
        return results
