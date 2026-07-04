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

# 값 매칭 — dv==0 zero-guard 절대바닥. 그 외 tolerance 는 통일 grounds() 위임 (Step 8)
_ABS_TOL = 0.5


def _to_float(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ★ 1-a (2026-07-02): 텍스트 필드(제목·key_message·items 등)에 담긴 수치도 검증 대상.
#   이전엔 spec["data"] 배열만 검사 → highlight_card/insight_card 가 조작 수치를
#   key_message·items 에 담으면 rows=[] → '텍스트 카드 면제'로 거짓 수치가 통과했음.
_UNIT_HINT = re.compile(r'\s*(?:%|퍼센트|원|억|조|만|천|배|명|건|개|포인트|달러|위|㎡|㎞|kg|톤|년|월|일)')
_TEXT_FIELDS = ("title", "subtitle", "key_message", "text", "caption", "summary", "headline")


def _text_data_numbers(spec: dict) -> list[float]:
    """텍스트 필드에서 '데이터성 수치' 추출. 단순 개수·서수(작은 정수)는 제외 —
    단위 동반 / 소수 / 3자리+ 만 데이터 주장으로 간주 (오탐 최소화)."""
    parts: list[str] = []
    for k in _TEXT_FIELDS:
        v = spec.get(k)
        if isinstance(v, str):
            parts.append(v)
    items = spec.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                parts.extend(str(vv) for vv in it.values() if isinstance(vv, (str, int, float)))
    text = " ".join(parts)
    nums: list[float] = []
    for m in re.finditer(r'-?\d[\d,]*(?:\.\d+)?', text):
        raw = m.group(0)
        val = _to_float(raw)
        if val is None:
            continue
        has_unit = bool(_UNIT_HINT.match(text[m.end():m.end() + 4]))
        is_dataish = abs(val) >= 100 or ('.' in raw)
        if has_unit or is_dataish:
            nums.append(val)
    return nums


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
    from JARVIS09_COLLECTOR.models import grounds   # ★ Step 8 단일 tolerance (올림/내림 or ±5%)
    if dv == 0:
        return abs(v) <= _ABS_TOL
    return grounds(v, dv)


def _match_row(label: str, value: float, dataset_rows) -> tuple[dict, float] | tuple[None, None]:
    """spec 행이 실데이터 행과 (값 근접 + 라벨 호환) 일치하면 (source, 실데이터값) 반환.
    ★ 1-b (2026-07-02): 실데이터 값(dv)도 반환 → 재구성 시 LLM 근사값 대신 실값 표시."""
    lt = _tokens(label)
    # 1차: 값 근접 + 라벨 토큰 겹침
    for dlabel, dv, src in dataset_rows:
        if _value_match(value, dv) and (lt & _tokens(dlabel)):
            return src, dv
    # 2차: 라벨 정보가 빈약할 때 값만 근접해도 인정 (실데이터 풀 안의 값)
    if not lt:
        for dlabel, dv, src in dataset_rows:
            if _value_match(value, dv):
                return src, dv
    return None, None


def has_provenance(spec: dict) -> bool:
    """spec 이 검증된 출처를 가지는지 (또는 수치 없는 텍스트 카드인지)."""
    if not isinstance(spec, dict):
        return False
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return True
    # 수치가 전혀 없으면 텍스트 카드 — 사실성 검증 대상 아님 (출처 불필요).
    # ★ 1-a: data 배열뿐 아니라 텍스트 필드(제목·key_message·items)의 데이터성 수치도
    #   없어야 진짜 텍스트 카드. 텍스트에 수치가 있으면 검증 필요 → provenance 없음(False).
    return not _spec_numeric_rows(spec) and not _text_data_numbers(spec)


def spec_chart_values(spec: dict) -> list[dict]:
    """차트 spec → 본문↔차트 교차대조용 라벨드 수치 [{label,value,unit}].

    ★ 2-4 (2026-07-02): provenance 레지스트리에 이 값을 박제해 두면 prepublish_gate
      _crosscheck_leg 가 '본문의 같은 지표 수치'와 대조할 수 있다. data 배열의 *명시
      라벨 행* 만 추출 — 텍스트필드(제목·key_message) 수치는 라벨 불명확 → 오탐 유발이라 제외.
    """
    if not isinstance(spec, dict):
        return []
    default_unit = str(spec.get("unit", "")).strip()
    out: list[dict] = []
    for d in spec.get("data") or []:
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        v = _to_float(d.get("value"))
        if label and v is not None:
            out.append({"label": label, "value": v,
                        "unit": str(d.get("unit", "") or default_unit).strip()})
    return out


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
    text_nums = _text_data_numbers(spec)   # ★ 1-a: 텍스트 필드 수치도 검증 대상

    # ① 진짜 텍스트 카드 (data 행·텍스트 데이터성 수치 모두 없음) — 검증 면제
    if not rows and not text_nums:
        return spec

    # ② 이미 dataset 기반(검증됨) — 통과
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return spec

    datasets = datasets or []
    dataset_rows = _all_dataset_rows(datasets)
    viz = spec.get("viz_type", "")
    min_rows = _MIN_ROWS.get(viz, _DEFAULT_MIN)

    # ★ 1-a: data 행이 없는 '텍스트 카드'인데 텍스트 필드에 데이터성 수치가 있는 경우.
    #   그 수치가 전부 실데이터로 뒷받침되면 통과, 하나라도 미검증이면 대체/스킵(거짓<없음).
    if not rows and text_nums:
        unbacked = [v for v in text_nums
                    if not any(_value_match(v, dv) for _, dv, _ in dataset_rows)]
        if not unbacked:
            return spec
        if datasets:
            chosen = next((d for d in datasets if d.get("viz_hint") == viz), datasets[0])
            log.info(f"[dataverify] 텍스트 수치 미검증({len(unbacked)}개) → 실데이터 대체: "
                     f"'{chosen.get('title','')}'")
            return _dataset_to_spec(chosen, spec)
        log.warning(f"[dataverify] 🚫 텍스트 카드 '{spec.get('title','')}' 미검증 수치 "
                    f"{unbacked[:3]} — 실데이터 없음 → 스킵(거짓 데이터 방지)")
        return None

    # ③ LLM 본문 추출 수치(data 행) — 행별 대조
    verified: list[tuple[int, float]] = []   # (idx, 실데이터값)
    matched_src: dict | None = None
    for idx, label, value in rows:
        src, dv = _match_row(label, value, dataset_rows)
        if src is not None:
            verified.append((idx, dv))
            matched_src = matched_src or src

    if len(verified) >= min_rows:
        # ★ 1-b: 검증된 행만 남기되 값은 실데이터 값(dv)으로 치환 — LLM 근사값 금지
        real_val = {i: dv for i, dv in verified}
        new_data = []
        for i, d in enumerate(spec.get("data") or []):
            if i in real_val:
                d2 = dict(d) if isinstance(d, dict) else {"label": "", "value": real_val[i]}
                d2["value"] = real_val[i]
                new_data.append(d2)
        spec["data"] = new_data
        spec["_provenance"] = {"verified": True, "source": matched_src or {},
                               "method": "verified_subset"}
        cap = source_caption(matched_src or {})
        if cap and not spec.get("source"):
            spec["source"] = cap
        log.info(f"[dataverify] 검증 통과(부분) {len(new_data)}/{len(rows)}행 "
                 f"viz={viz} title='{spec.get('title','')}' (실데이터값 치환)")
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
           "record_provenance", "lookup_provenance", "spec_chart_values"]
