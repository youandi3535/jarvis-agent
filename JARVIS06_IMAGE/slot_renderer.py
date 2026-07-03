"""JARVIS06_IMAGE/slot_renderer.py — 데이터 내장 차트 슬롯 파서·렌더 (★ 사용자 박제 2026-07-03).

"자비스02가 대본을 쓸 때 차트 슬롯 안에 차트를 만드는 *모든 수치 데이터까지* 넣는다.
 자비스09는 모든 자료를 자비스02에게만 준다 — 자비스06에게는 안 준다.
 자비스06은 대본을 통째로 받아 슬롯 데이터로 이미지만 생성한다."

슬롯 표준 (대본 내 블록):
    [CHART_1]
    제목: 고려아연 연간 실적
    종류: bar          (bar|line|area|pie|kpi 중 1)
    단위: 조원
    데이터: 매출액=16.59 | 영업이익=1.23
    출처: 데이터스캐너 (2025-12)
    [/CHART_1]

원칙:
  - 자비스06은 데이터를 수집·선택하지 않는다 — 슬롯에 적힌 값 그대로 렌더.
  - 진실성 검증: 슬롯 값은 대본 패키지에 동봉된 ref_datasets(자비스09 원본)와
    대조 — 일치하는 행만 렌더(±0.5% 허용), 전부 불일치면 슬롯 무효(AI 사진 폴백).
    "자비스09의 수집 정보로 검증 대조" — 검증 재료는 09→02→(대본 패키지)→06 으로 흐른다.
  - 렌더는 infographic_engine(85점 엔진) 위임 — 시간축 좌→우·스타일 다양성 그대로.
"""
from __future__ import annotations

import re
import logging

log = logging.getLogger("jarvis")

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw):
        pass
# ─────────────────────────────────────────────────────

_SLOT_RE = re.compile(r"\[CHART_(\d+)\]\s*(.*?)\s*\[/CHART_\1\]", re.DOTALL)
_VIZ_MAP = {"bar": "bar_chart", "line": "line_chart", "area": "area_chart",
            "pie": "pie_chart", "kpi": "kpi_cards"}


def parse_chart_slots(text: str) -> list[dict]:
    """대본에서 데이터 내장 슬롯 추출 → [{idx, title, viz, unit, data, source, raw}]."""
    out: list[dict] = []
    for m in _SLOT_RE.finditer(text or ""):
        idx = int(m.group(1))
        body = m.group(2)
        fields: dict = {}
        for line in body.splitlines():
            line = line.strip()
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
        data = []
        for pair in (fields.get("데이터", "") or "").split("|"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            lb, _, val = pair.rpartition("=")
            try:
                data.append({"label": lb.strip(),
                             "value": float(str(val).replace(",", "").replace("%", "").strip())})
            except (TypeError, ValueError):
                continue
        out.append({
            "idx": idx,
            "title": fields.get("제목", "").strip(),
            "viz": _VIZ_MAP.get((fields.get("종류", "bar") or "bar").strip().lower(), "bar_chart"),
            "unit": fields.get("단위", "").strip(),
            "data": data,
            "source_name": fields.get("출처", "").strip(),
            "raw": m.group(0),
        })
    return out


def _ref_values(ref_datasets: list | None) -> set[float]:
    """검증 대조용 — 자비스09 원본 데이터셋의 모든 수치 값 집합."""
    vals: set[float] = set()
    for d in ref_datasets or []:
        for r in (d.get("data") or []):
            try:
                vals.add(round(float(str(r.get("value")).replace(",", "")), 6))
            except (TypeError, ValueError):
                continue
    return vals


def verify_slot(slot: dict, ref_values: set[float]) -> dict | None:
    """슬롯 값 ↔ 자비스09 원본 값 대조 (±0.5%). 검증 행만 유지, 0행이면 None.

    ref 가 비어 있으면(검증 재료 미동봉) 보수적으로 슬롯 무효 — 거짓 차트 < 차트 없음.
    """
    if not slot.get("data"):
        return None
    if not ref_values:
        log.warning(f"[slot] CHART_{slot['idx']} 검증 재료 없음 — 슬롯 무효 (AI 사진 폴백)")
        return None
    kept = []
    for r in slot["data"]:
        v = r["value"]
        ok = any(abs(v - rv) <= max(abs(rv) * 0.005, 1e-9) for rv in ref_values)
        if ok:
            kept.append(r)
        else:
            log.warning(f"[slot] CHART_{slot['idx']} 값 {r['label']}={v} — 원본 불일치 → 행 제거")
    if not kept:
        return None
    return {**slot, "data": kept}


def render_slot(slot: dict, out_dir, run_id: str = "", theme: str = "") -> str:
    """검증된 슬롯 → 인포그래픽 렌더 (infographic_engine 위임). 실패 시 ""."""
    try:
        from JARVIS06_IMAGE.infographic_engine import generate_infographic
        ds = {
            "title": slot.get("title") or f"{theme} 핵심 수치",
            "viz_hint": slot.get("viz", "bar_chart"),
            "unit": slot.get("unit", ""),
            "data": slot["data"],
            "source": {"provider": "draft_slot",
                       "name": slot.get("source_name") or "자비스09 수집(대본 내장)",
                       "url": ""},
        }
        return generate_infographic(
            ds["title"], "수집 실데이터 기반", [ds],
            run_id=run_id, slot_key=f"slot{slot['idx']}", out_dir=out_dir,
            context=f"{theme} — {ds['title']}",
            src=f"데이터 출처: {ds['source']['name']}",
        ) or ""
    except Exception as e:
        log.warning(f"[slot] CHART_{slot['idx']} 렌더 실패: {e}")
        _g_report("image", e, module=__name__, func_name="render_slot")
        return ""


def render_slots_in_text(text: str, ref_datasets: list | None, out_dir,
                         run_id: str = "", theme: str = "") -> tuple[str, int, int]:
    """대본 내 데이터 내장 슬롯 전부 처리 → (치환된 텍스트, 성공 수, 전체 슬롯 수).

    실패 슬롯은 플레이스홀더 `[CHART_N: <제목>]` (구형식) 으로 강등 — 호출자의
    기존 AI 사진 폴백 경로가 이어받는다.
    """
    slots = parse_chart_slots(text)
    if not slots:
        return text, 0, 0
    refs = _ref_values(ref_datasets)
    ok = 0
    for slot in slots:
        verified = verify_slot(slot, refs)
        html = render_slot(verified, out_dir, run_id=run_id, theme=theme) if verified else ""
        if html:
            text = text.replace(slot["raw"], html, 1)
            ok += 1
            print(f"  ✅ [slot] CHART_{slot['idx']} 대본 내장 데이터로 렌더 완료")
        else:
            fallback = f"[CHART_{slot['idx']}: {slot.get('title') or theme}]"
            text = text.replace(slot["raw"], fallback, 1)
            print(f"  ⏭️ [slot] CHART_{slot['idx']} 무효(검증 실패/렌더 실패) → AI 사진 폴백 위임")
    return text, ok, len(slots)


__all__ = ["parse_chart_slots", "verify_slot", "render_slot", "render_slots_in_text"]
