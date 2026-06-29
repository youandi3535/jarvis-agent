"""JARVIS06_IMAGE/validators/image_data_verifier.py — 차트/인포그래픽 *데이터 사실성* 검증.

★ 사용자 박제 2026-06-29 — "데이터가 들어가는 이미지는 절대 거짓된 데이터로 만들면 안 됨."

  텍스트(대본)는 prepublish_gate 가 검수하지만, *이미지 안의 수치* 는 별도 검증이 없었다.
  이 모듈이 그 갭을 막는다: 차트 spec 의 모든 숫자가 JARVIS09 실데이터(출처 보유)로
  뒷받침되는지 검증한다.

정책 (사용자 선택 — "검증분만 재구성 후 스킵"):
  ① 텍스트 카드(숫자 없는 인포그래픽)   → 검증 면제, 그대로 통과.
  ② 이미 dataset 로 만들어진 spec       → _provenance.verified=True 신뢰, 통과.
  ③ LLM 본문 추출 수치 spec            → 각 값을 실데이터와 대조:
       - 검증된 행만 남겨 재구성 (검증 행 ≥ 최소 개수면 통과)
       - 0개 검증 + 관련 실데이터 dataset 존재 → 그 dataset 으로 *대체* (실데이터 차트)
       - 0개 검증 + dataset 없음          → None 반환 (호출자는 차트 스킵: return "")

공개 API:
  verify_chart_spec(spec, datasets) -> spec | None
  has_provenance(spec) -> bool
  source_caption(source) -> str
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("jarvis.image.dataverify")

# ── 출처 레지스트리 (트립와이어) ─────────────────────────────────────────
# render_from_spec 가 생성한 이미지 path → provenance 매핑. prepublish_gate 가
# "검증 안 된 수치 차트가 발행에 섞였는지" 최종 확인하는 근거 (process-local).
_PROV_REGISTRY: dict[str, dict] = {}


def record_provenance(image_path, provenance: dict) -> None:
    """생성된 이미지의 출처/검증 결과를 등록 (render_from_spec 가 호출)."""
    try:
        _PROV_REGISTRY[str(Path(image_path).resolve())] = dict(provenance or {})
    except Exception:
        pass


def lookup_provenance(image_path) -> dict | None:
    """이미지 path 의 등록된 provenance 조회. 미등록이면 None."""
    try:
        return _PROV_REGISTRY.get(str(Path(image_path).resolve()))
    except Exception:
        return None

# 수치 차트 최소 데이터 개수 (kpi 는 1, 그 외 2)
_MIN_ROWS = {"kpi_cards": 1, "comparison_kpi": 1, "highlight_card": 1, "insight_card": 1}
_DEFAULT_MIN = 2

# 값 매칭 허용 오차
_REL_TOL = 0.02   # ±2%
_ABS_TOL = 0.5


def _to_float(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _spec_numeric_rows(spec: dict) -> list[tuple[int, str, float]]:
    """spec["data"] 에서 (index, label, value) 수치 행 추출. value/before/after 모두 검사."""
    out = []
    for i, d in enumerate(spec.get("data") or []):
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        for key in ("value", "after", "before"):
            v = _to_float(d.get(key))
            if v is not None:
                out.append((i, label, v))
                break
    return out


def _all_dataset_rows(datasets: list[dict]) -> list[tuple[str, float, dict]]:
    """모든 dataset 의 (label, value, source) 평탄화."""
    rows = []
    for ds in datasets or []:
        src = ds.get("source") or {}
        for r in ds.get("data") or []:
            v = _to_float(r.get("value"))
            if v is not None:
                rows.append((str(r.get("label", "")).strip(), v, src))
    return rows


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", (s or "").lower()))


def _value_match(v: float, dv: float) -> bool:
    if dv == 0:
        return abs(v) <= _ABS_TOL
    return abs(v - dv) <= max(abs(dv) * _REL_TOL, _ABS_TOL)


def _match_row(label: str, value: float, dataset_rows) -> dict | None:
    """spec 행이 실데이터 행과 (값 근접 + 라벨 호환) 일치하면 그 source 반환."""
    lt = _tokens(label)
    # 1차: 값 근접 + 라벨 토큰 겹침
    for dlabel, dv, src in dataset_rows:
        if _value_match(value, dv) and (lt & _tokens(dlabel)):
            return src
    # 2차: 라벨 정보가 빈약할 때 값만 근접해도 인정 (실데이터 풀 안의 값)
    if not lt:
        for dlabel, dv, src in dataset_rows:
            if _value_match(value, dv):
                return src
    return None


def has_provenance(spec: dict) -> bool:
    """spec 이 검증된 출처를 가지는지 (또는 수치 없는 텍스트 카드인지)."""
    if not isinstance(spec, dict):
        return False
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return True
    # 수치가 전혀 없으면 텍스트 카드 — 사실성 검증 대상 아님 (출처 불필요)
    return not _spec_numeric_rows(spec)


def source_caption(source: dict) -> str:
    """출처 dict → 이미지에 박을 한 줄 캡션."""
    if not source:
        return ""
    name = source.get("name") or source.get("provider") or ""
    as_of = source.get("as_of") or ""
    if name and as_of:
        return f"출처: {name} ({as_of})"
    return f"출처: {name}" if name else ""


def _dataset_to_spec(dataset: dict, base: dict) -> dict:
    """실데이터 dataset → 렌더 가능한 spec (대체용)."""
    src = dataset.get("source") or {}
    return {
        "viz_type": dataset.get("viz_hint") or "bar_chart",
        "title": dataset.get("title") or base.get("title", ""),
        "subtitle": base.get("subtitle", ""),
        "unit": dataset.get("unit", ""),
        "data": [{"label": r["label"], "value": r["value"],
                  "unit": dataset.get("unit", "")} for r in dataset.get("data", [])],
        "color_theme": base.get("color_theme", "blue"),
        "source": source_caption(src),
        "keyword": base.get("keyword", ""),
        "sector": base.get("sector", ""),
        "_provenance": {"verified": True, "source": src, "method": "dataset_substitution"},
    }


def verify_chart_spec(spec: dict, datasets: list[dict] | None):
    """차트 spec 의 데이터 사실성 검증. 검증분 재구성 / 대체 / 스킵.

    Returns:
        - 검증/재구성된 spec (텍스트 카드는 그대로)
        - None  → 수치 차트인데 실데이터 뒷받침 0 → 호출자가 차트 스킵해야 함
    """
    if not isinstance(spec, dict):
        return spec

    rows = _spec_numeric_rows(spec)
    # ① 텍스트 카드 — 검증 면제
    if not rows:
        return spec

    # ② 이미 dataset 기반(검증됨) — 통과
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return spec

    datasets = datasets or []
    dataset_rows = _all_dataset_rows(datasets)
    viz = spec.get("viz_type", "")
    min_rows = _MIN_ROWS.get(viz, _DEFAULT_MIN)

    # ③ LLM 본문 추출 수치 — 행별 대조
    verified_idx: list[int] = []
    matched_src: dict | None = None
    for idx, label, value in rows:
        src = _match_row(label, value, dataset_rows)
        if src is not None:
            verified_idx.append(idx)
            matched_src = matched_src or src

    if len(verified_idx) >= min_rows:
        # 검증된 행만 남겨 재구성
        keep = set(verified_idx)
        new_data = [d for i, d in enumerate(spec.get("data") or []) if i in keep]
        spec["data"] = new_data
        spec["_provenance"] = {"verified": True, "source": matched_src or {},
                               "method": "verified_subset"}
        cap = source_caption(matched_src or {})
        if cap and not spec.get("source"):
            spec["source"] = cap
        log.info(f"[dataverify] 검증 통과(부분) {len(new_data)}/{len(rows)}행 "
                 f"viz={viz} title='{spec.get('title','')}'")
        return spec

    # 0개 검증 — 관련 실데이터로 대체 시도
    if datasets:
        # 본문이 원한 viz_type 과 가까운 dataset 우선, 없으면 첫 dataset
        chosen = next((d for d in datasets if d.get("viz_hint") == viz), datasets[0])
        log.info(f"[dataverify] 본문 수치 미검증 → 실데이터 dataset 으로 대체: "
                 f"'{chosen.get('title','')}'")
        return _dataset_to_spec(chosen, spec)

    # 실데이터도 없음 — 거짓 차트 방지, 스킵
    log.warning(f"[dataverify] 🚫 수치 차트 '{spec.get('title','')}' 실데이터 뒷받침 0 → 스킵")
    return None


__all__ = ["verify_chart_spec", "has_provenance", "source_caption",
           "record_provenance", "lookup_provenance"]
