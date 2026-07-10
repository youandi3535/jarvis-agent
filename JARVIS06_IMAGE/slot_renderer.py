"""JARVIS06_IMAGE/slot_renderer.py — 데이터 내장 차트 슬롯 파서·렌더 (★ 사용자 박제 2026-07-03).

"자비스02가 대본을 쓸 때 차트 슬롯 안에 차트를 만드는 *모든 수치 데이터까지* 넣는다.
 자비스09는 모든 자료를 자비스02에게만 준다 — 자비스06에게는 안 준다.
 자비스06은 대본을 통째로 받아 슬롯 데이터로 이미지만 생성한다."

슬롯 표준 (대본 내 블록):
    [CHART_1]
    제목: 고려아연 연간 실적
    단위: 조원
    데이터: 매출액=16.59 | 영업이익=1.23
    출처: 데이터스캐너 (2025-12)
    [/CHART_1]

    ★ 종류: 필드 없음 — 차트 종류는 JARVIS06이 데이터 성격 보고 자율 결정
      (시계열 라벨→line / 단일값→kpi / %단위+소수개→pie / 나머지→bar)

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

_TIME_LABEL_RE = re.compile(
    r"^(\d{4}[.\-/]\d{1,2}|\d{4}[년Q]\d?|Q[1-4]\s*\d{4}|\d{4}$)", re.IGNORECASE
)
_RATIO_KEYWORDS = ("%" , "퍼센트", "비율", "점유율", "비중", "분포")


def _infer_viz_type(data: list, unit: str = "") -> str:
    """데이터 성격으로 차트 종류 자동 추론 — 종류: 필드 대체."""
    if len(data) == 1:
        return "kpi_cards"
    if not data:
        return "bar_chart"
    labels = [str(d["label"]) for d in data]
    # 날짜/연도 라벨 70%+ → 시계열 라인
    time_count = sum(1 for lb in labels if _TIME_LABEL_RE.match(lb.strip()))
    if time_count >= len(labels) * 0.7:
        return "line_chart"
    # % 단위 + 5개 이하 → 파이 (비율)
    if any(kw in unit for kw in _RATIO_KEYWORDS) and len(data) <= 5:
        return "pie_chart"
    return "bar_chart"


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
            "viz": _VIZ_MAP.get(fields.get("종류", ""), _infer_viz_type(data, fields.get("단위", ""))),
            "unit": fields.get("단위", "").strip(),
            "data": data,
            "source_name": fields.get("출처", "").strip(),
            "raw": m.group(0),
        })
    return out


def _norm_unit(u: str) -> str:
    return re.sub(r"\s+", "", str(u or ""))


def _ref_value_units(ref_datasets: list | None) -> list[tuple[float, str]]:
    """검증 대조용 — 자비스09 원본의 (값, 단위) 짝 목록. 값·단위는 한 몸이다."""
    out: list[tuple[float, str]] = []
    for d in ref_datasets or []:
        u = _norm_unit(d.get("unit"))
        for r in (d.get("data") or []):
            try:
                out.append((round(float(str(r.get("value")).replace(",", "")), 6), u))
            except (TypeError, ValueError):
                continue
    return out


def verify_slot(slot: dict, ref_pairs: list[tuple[float, str]]) -> dict | None:
    """슬롯 (값+단위) ↔ 자비스09 원본 (값+단위) 짝 대조 (★ 통일 grounds: 올림/내림 표시 또는 ±5%).

    ★ 단위 검증 (사용자 박제 2026-07-03): "단위는 원이라고 해놓고 숫자는 %면?" —
      값만 맞고 단위가 다르면: 원본에서 그 값의 단위가 *유일* → 슬롯 단위 자동 교정,
      *복수(애매)* → 행 제거. 검증 행만 유지, 0행이면 None.
    ref 가 비어 있으면(검증 재료 미동봉) 보수적으로 슬롯 무효 — 거짓 차트 < 차트 없음.
    """
    from JARVIS09_COLLECTOR.models import grounds as _grounds   # ★ Step 8 단일 tolerance
    if not slot.get("data"):
        return None
    if not ref_pairs:
        log.warning(f"[slot] CHART_{slot['idx']} 검증 재료 없음 — 슬롯 무효 (AI 사진 폴백)")
        return None
    slot_unit = _norm_unit(slot.get("unit"))
    rows: list[tuple[dict, str]] = []   # (행, 해석 단위) — 행별 추적 (ERRORS [312])
    for r in slot["data"]:
        v = r["value"]
        matches = [(rv, ru) for rv, ru in ref_pairs if _grounds(v, rv)]
        if not matches:
            log.warning(f"[slot] CHART_{slot['idx']} 값 {r['label']}={v} — 원본 불일치 → 행 제거")
            continue
        real_units = {ru for _, ru in matches if ru}
        if not real_units:
            # ref 단위 미상('') — 값만 검증됨. 슬롯 단위 유지, 교정 금지 (테마 경로
            # ref 가 unit 없이 와도 슬롯의 진실 단위를 '' 로 지우는 사고 방지)
            rows.append((r, slot_unit))
        elif slot_unit in real_units:
            rows.append((r, slot_unit))
        elif len(real_units) == 1:
            _true_u = next(iter(real_units))
            log.warning(f"[slot] CHART_{slot['idx']} 단위 불일치 — "
                        f"'{slot_unit or '(없음)'}' → 원본 단위 '{_true_u}' 로 교정 ({r['label']}={v})")
            rows.append((r, _true_u))
        else:
            log.warning(f"[slot] CHART_{slot['idx']} 값 {r['label']}={v} — 단위 애매"
                        f"(원본 {real_units}, 슬롯 '{slot_unit}') → 행 제거")
            continue
    if not rows:
        return None
    # ★ 합의 단위 강제 (ERRORS [312] — 혼합 단위 뭉개기 금지): 한 차트 = 한 단위.
    #   행별 해석 단위가 갈리면 다수 단위(동률이면 슬롯 단위 우선)만 남기고 나머지 행 제거
    #   — '기준금리 2.5(%)' 가 '원' 차트에 섞여 렌더되는 사고 차단.
    _cnt: dict[str, int] = {}
    for _, u in rows:
        _cnt[u] = _cnt.get(u, 0) + 1
    best_unit = max(_cnt, key=lambda u: (_cnt[u], u == slot_unit))
    for r, u in rows:
        if u != best_unit:
            log.warning(f"[slot] CHART_{slot['idx']} 값 {r['label']}={r['value']} — "
                        f"단위 '{u}' ≠ 합의 단위 '{best_unit}' → 행 제거")
    kept = [r for r, u in rows if u == best_unit]
    if not kept:
        return None
    if best_unit != slot_unit:
        slot = {**slot, "unit": best_unit}
    # ★ 동일 수치 중복 제거 (사용자 박제 2026-07-03) — 시계열 평평 구간은 보존
    try:
        from JARVIS06_IMAGE.image_spec import dedupe_chart_rows
        kept = dedupe_chart_rows(kept)
    except Exception:
        pass
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
    refs = _ref_value_units(ref_datasets)
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
