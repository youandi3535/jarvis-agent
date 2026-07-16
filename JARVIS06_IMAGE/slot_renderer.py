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
    대조 — 일치하는 행만 렌더(±0.5% 허용), 전부 불일치면 슬롯 무효(구형식 강등 → 인포그래픽, 없으면 빈 슬롯).
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

_SLOT_OPEN_RE = re.compile(r"\[CHART_(\d+)\]")
_SLOT_CLOSE_RE = re.compile(r"^\[/CHART_(\d+)\]\s*$")
_FIELD_LINE_RE = re.compile(r"^(제목|단위|데이터|출처|종류|데이터셋)\s*:\s*(.*)$")
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
    """대본에서 데이터 내장 슬롯 추출 → [{idx, title, viz, unit, data, source, raw}].

    ★ 닫는 태그 인덱스 불일치·누락에도 관용적 매칭 (2026-07-11 — RuntimeError
    "[사실성] 출처·웹 모두 확인 불가: <라벨>=<값>" 사고 박제): LLM이 [CHART_2]...
    [/CHART_3] 처럼 인덱스를 틀리거나 닫는 태그를 아예 빠뜨리면 옛 정규식
    (`\\1` 백레퍼런스)이 매칭 실패 → 원본 카탈로그 문법(`데이터: 라벨=값`)이
    렌더 없이 본문에 그대로 새어나가 팩트체크 LLM이 이를 "주장"으로 오인 추출
    → 사실성 게이트가 무단위·비문장 조각을 검증 불가로 차단. 필드 라인
    (제목/단위/데이터/출처/종류) 또는 아무 인덱스나의 닫는 태그가 나오는 동안만
    소비 → 필드 아닌 라인을 만나면 즉시 블록 종료(본문 삼켜먹기 방지).
    """
    out: list[dict] = []
    text = text or ""
    for m in _SLOT_OPEN_RE.finditer(text):
        idx = int(m.group(1))
        start = m.end()
        consumed = 0
        fields: dict = {}
        for line in text[start:].splitlines(keepends=True):
            stripped = line.strip()
            if _SLOT_CLOSE_RE.match(stripped):
                consumed += len(line)
                break
            if not stripped:
                consumed += len(line)
                continue
            fm = _FIELD_LINE_RE.match(stripped)
            if not fm:
                break  # 필드 아닌 라인 → 닫는 태그 없어도 슬롯 블록 종료
            fields[fm.group(1)] = fm.group(2).strip()
            consumed += len(line)
        raw = text[m.start():start + consumed]
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
            "dataset_key": fields.get("데이터셋", "").strip(),  # "D2" 형태 직접 참조
            "viz": _VIZ_MAP.get(fields.get("종류", ""), _infer_viz_type(data, fields.get("단위", ""))),
            "unit": fields.get("단위", "").strip(),
            "data": data,
            "source_name": fields.get("출처", "").strip(),
            "raw": raw,
        })
    return out


def _norm_unit(u: str) -> str:
    return re.sub(r"\s+", "", str(u or ""))


_STOP_WORDS = frozenset(["의", "에", "는", "이", "가", "을", "를", "한", "하", "및",
                          "과", "와", "도", "로", "으로", "에서", "부터", "까지", "추이",
                          "현황", "비교", "분석", "현황", "동향", "변화", "증감"])


def _title_words(title: str) -> frozenset:
    return frozenset(w for w in re.sub(r"[^\w]", " ", title).split()
                     if w and w not in _STOP_WORDS and len(w) >= 2)


def _filter_datasets_by_title(slot_title: str, ref_datasets: list) -> list:
    """슬롯 제목과 키워드가 겹치는 dataset만 반환 (Jaccard ≥ 0.10).

    ★ 핵심 보안 게이트 (2026-07-11 — ERRORS [421]): "코스피 거래대금" 슬롯이
    "삼성전자 PER=25" dataset 의 25 를 우연 매칭하는 것을 차단.
    슬롯과 주제가 다른 dataset 의 값은 검증 ref 에서 완전 배제.
    임계값 0.10 = 1개 단어 겹침으로 충분히 느슨 (완전히 무관한 경우만 제외).
    매칭 dataset 0개 → 빈 리스트 반환 → verify_slot 이 None 반환 → 차트 없음.
    실데이터 없으면 차트 없음 원칙 (ADR 010) — 폴백 없음.
    """
    if not slot_title or not ref_datasets:
        return []
    sw = _title_words(slot_title)
    if not sw:
        return []
    matched = []
    for ds in ref_datasets:
        dw = _title_words(ds.get("title", ""))
        if not dw:
            continue
        overlap = len(sw & dw)
        union = len(sw | dw)
        jaccard = overlap / union if union else 0.0
        if jaccard >= 0.10:
            matched.append(ds)
    return matched


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
        log.warning(f"[slot] CHART_{slot['idx']} 검증 재료 없음 — 슬롯 무효 (구형식 강등 → 인포그래픽, 없으면 빈 슬롯)")
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


def _path_to_img_html(path: str, alt: str = "") -> str:
    """인포그래픽 파일 경로 → <p><img> HTML 블록."""
    return (f'<p><img src="{path}" alt="{alt}" '
            f'style="width:100%;max-width:760px;border-radius:8px;'
            f'margin:16px auto;display:block;"></p>')


def render_slot(slot: dict, out_dir, run_id: str = "", theme: str = "") -> str:
    """검증된 슬롯 → 인포그래픽 렌더 (infographic_engine 위임). 실패 시 "".
    ★ 반환값은 <p><img> HTML — 파일 경로 아님 (draft HTML에 직접 삽입 가능)."""
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
        _path = generate_infographic(
            ds["title"], "수집 실데이터 기반", [ds],
            run_id=run_id, slot_key=f"slot{slot['idx']}", out_dir=out_dir,
            context=f"{theme} — {ds['title']}",
            src=f"데이터 출처: {ds['source']['name']}",
        ) or ""
        return _path_to_img_html(_path, ds["title"][:40]) if _path else ""
    except Exception as e:
        log.warning(f"[slot] CHART_{slot['idx']} 렌더 실패: {e}")
        _g_report("image", e, module=__name__, func_name="render_slot")
        return ""


def render_slots_in_text(text: str, ref_datasets: list | None, out_dir,
                         run_id: str = "", theme: str = "") -> tuple[str, int, int]:
    """대본 내 데이터 내장 슬롯 전부 처리 → (치환된 텍스트, 성공 수, 전체 슬롯 수).

    ★ 슬롯별 제목 매칭 검증 (2026-07-11 — ERRORS [418]): 슬롯 제목과 관련 없는
    dataset 의 수치가 우연 매칭되는 False Positive 를 차단하기 위해, 각 슬롯마다
    _filter_datasets_by_title() 로 주제 연관 dataset 만 ref 로 좁힌다.
    실패 슬롯은 플레이스홀더 `[CHART_N: <제목>]` (구형식) 으로 강등.
    """
    slots = parse_chart_slots(text)
    if not slots:
        return text, 0, 0
    ok = 0
    for slot in slots:
        slot_title = slot.get("title") or theme
        # ★ 핵심: 슬롯 제목과 연관된 dataset만 ref로 사용
        slot_ds = _filter_datasets_by_title(slot_title, ref_datasets or [])
        refs = _ref_value_units(slot_ds)
        verified = verify_slot(slot, refs)
        html = render_slot(verified, out_dir, run_id=run_id, theme=theme) if verified else ""
        if html:
            text = text.replace(slot["raw"], html, 1)
            ok += 1
            print(f"  ✅ [slot] CHART_{slot['idx']} 대본 내장 데이터로 렌더 완료")
        else:
            fallback = f"[CHART_{slot['idx']}: {slot.get('title') or theme}]"
            text = text.replace(slot["raw"], fallback, 1)
            print(f"  ⏭️ [slot] CHART_{slot['idx']} 무효(검증 실패/렌더 실패) → 실데이터 인포그래픽 위임")
    return text, ok, len(slots)


def render_slots_from_collected(text: str, collected_datasets: list, out_dir,
                                run_id: str = "", theme: str = "") -> tuple[str, int, int]:
    """슬롯 제목 → collected.datasets 매칭 → 실데이터 직접 렌더 (LLM 수치 완전 무시).

    ★ 사용자 박제 2026-07-11: LLM이 차트 수치를 임의로 기입하는 것을 원천 차단.
      슬롯에 LLM이 뭘 써도 무시 — 항상 collected.datasets 의 실데이터로 렌더.
      슬롯 제목 키워드 매칭(Jaccard ≥ 0.10)으로 연관 dataset 을 찾아 그대로 사용.
      매칭 없으면 [CHART_N: 제목] 구형식 강등 → _next_data_infographic 경로 이어받음.
    """
    try:
        from JARVIS06_IMAGE.infographic_engine import generate_infographic
    except ImportError:
        return text, 0, 0

    slots = parse_chart_slots(text)
    if not slots:
        return text, 0, 0

    ok = 0
    for slot in slots:
        slot_title = slot.get("title") or theme
        dataset_key = slot.get("dataset_key", "").upper()  # "D2" 형태

        # ★ 1순위: D번호 직접 참조 (정확, 오류 없음)
        best = None
        if dataset_key and dataset_key.startswith("D") and dataset_key[1:].isdigit():
            didx = int(dataset_key[1:]) - 1  # D1 → 0, D2 → 1
            if 0 <= didx < len(collected_datasets or []):
                best = collected_datasets[didx]

        # ★ 2순위 폴백: 제목 Jaccard 매칭 (D번호 없거나 범위 초과 시)
        if best is None:
            matched = _filter_datasets_by_title(slot_title, collected_datasets or [])
            if not matched:
                fallback = f"[CHART_{slot['idx']}: {slot_title}]"
                text = text.replace(slot["raw"], fallback, 1)
                print(f"  ⏭️ [slot] CHART_{slot['idx']} 실데이터 없음 → 구형식 강등")
                continue
            best = max(matched,
                       key=lambda d: len(_title_words(d.get("title", "")) & _title_words(slot_title)))
        render_ds = {
            "title": slot_title or best.get("title", ""),
            "viz_hint": _infer_viz_type(best.get("data", []), best.get("unit", "")),
            "unit": best.get("unit", ""),
            "data": best.get("data", []),
            "source": best.get("source") or {"provider": "jarvis09",
                                              "name": "자비스09 수집", "url": ""},
        }
        if not render_ds["data"]:
            fallback = f"[CHART_{slot['idx']}: {slot_title}]"
            text = text.replace(slot["raw"], fallback, 1)
            print(f"  ⏭️ [slot] CHART_{slot['idx']} dataset 데이터 행 없음 → 강등")
            continue

        try:
            _img_path = generate_infographic(
                render_ds["title"], "수집 실데이터 기반", [render_ds],
                run_id=run_id, slot_key=f"slot{slot['idx']}", out_dir=out_dir,
                context=f"{theme} — {render_ds['title']}",
                src=f"데이터 출처: {render_ds['source'].get('name', '자비스09')}",
            ) or ""
        except Exception as e:
            log.warning(f"[slot] CHART_{slot['idx']} 렌더 실패: {e}")
            _g_report("image", e, module=__name__, func_name="render_slots_from_collected")
            _img_path = ""

        if _img_path:
            img_html = _path_to_img_html(_img_path, slot_title[:40])
            text = text.replace(slot["raw"], img_html, 1)
            ok += 1
            print(f"  ✅ [slot] CHART_{slot['idx']} 실데이터 렌더 완료 [{best.get('title','')}]")
        else:
            fallback = f"[CHART_{slot['idx']}: {slot_title}]"
            text = text.replace(slot["raw"], fallback, 1)
            print(f"  ⚠️ [slot] CHART_{slot['idx']} 렌더 실패 → 강등")

    return text, ok, len(slots)


__all__ = ["parse_chart_slots", "verify_slot", "render_slot", "render_slots_in_text",
           "render_slots_from_collected"]
