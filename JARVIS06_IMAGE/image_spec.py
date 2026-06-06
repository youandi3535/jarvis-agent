"""JARVIS06_IMAGE/image_spec.py — 블로그 섹션 텍스트 → 이미지 설계서 생성.

핵심 원칙:
  - 섹션 전체 텍스트를 그대로 Claude에게 전달 (절대 자르지 않음)
  - 차트 타입, 데이터, 디자인 방향 모두 LLM이 결정
  - 코드는 렌더링만 담당

공개 API:
  generate_image_spec(section_text, keyword, sector, section_title) -> dict
  render_from_spec(spec, out_path) -> Path
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# ── 지원 시각화 타입 ──────────────────────────────────────────────────

# HTML/CSS 렌더러 (Playwright) — 카드·레이아웃 타입
HTML_TYPES = {
    "comparison_kpi",    # 다중 컬럼 KPI 비교 (img1 스타일)
    "status_checklist",  # 색상 배지 체크리스트 (img4 스타일)
    "timeline",          # 수평 타임라인 (img5 스타일)
    "decision_matrix",   # 상황 → 행동 매트릭스 (img8 스타일)
    "dark_summary",      # 다크 배경 요약 카드 (img9 스타일)
    "cost_breakdown",    # 비용 분해 테이블 (img10 스타일)
    "kpi_cards",         # KPI 숫자 카드 그리드
    "highlight_card",    # 한 줄 강조 카드
    "insight_card",      # 인사이트 카드
    "scenario_cards",    # 시나리오 3단 카드
    "comparison_table",  # 좌우 2열 비교 테이블
    "checklist",         # = status_checklist 별칭
    "infographic",       # 기본 인포그래픽 (kpi_cards 형태)
}

# Matplotlib 렌더러 — 데이터 차트 타입
CHART_TYPES = {
    "bar_chart",
    "horizontal_bar",
    "line_chart",
    "area_chart",
    "pie_chart",
    "stacked_bar",
    "stacked_combo",
    "grouped_bar",
}

# Plotly 렌더러 — 고급 차트 (matplotlib 실패 시 폴백)
PLOTLY_TYPES = {
    "bar_chart",
    "horizontal_bar",
    "line_chart",
    "area_chart",
    "pie_chart",
    "scatter_chart",
    "grouped_bar",
    "waterfall_chart",
    "gauge_chart",
    "dashboard",
}

# SVG 렌더러 — 최후 폴백
SVG_TYPES = {
    "kpi_cards",
    "comparison_table",
    "infographic",
    "timeline",
    "checklist",
    "scenario_cards",
    "flow_diagram",
    "highlight_card",
    "insight_card",
}

# 전체 유효 타입
ALL_TYPES = HTML_TYPES | CHART_TYPES | PLOTLY_TYPES | SVG_TYPES

# ── 설계서 생성 프롬프트 ──────────────────────────────────────────────
_SPEC_SYSTEM = """당신은 블로그 콘텐츠 전문 시각화 디자이너입니다.
주어진 섹션 본문을 꼼꼼히 읽고, 독자가 핵심 정보를 한눈에 파악할 수 있는
최적의 시각화 설계서를 작성합니다.

타입 선택 기준:
[HTML 카드·레이아웃 — 텍스트 중심]
- 두 제품/상황의 수치 비교 (이전→이후, A vs B) → comparison_kpi
- 추천/주의/대안 등 상태 배지가 붙는 목록 → status_checklist
- 날짜·사건 순서 나열 → timeline
- 상황 → 행동 매핑 표 → decision_matrix
- 강한 결론·요약 (다크 배경) → dark_summary
- 비용/가격 항목별 분해 → cost_breakdown
- 핵심 숫자 KPI 3~6개 → kpi_cards
- 시나리오 비교 3가지 → scenario_cards
- 한 줄 강조 메시지 → highlight_card / insight_card

[matplotlib 차트 — 수치 데이터]
- 카테고리별 수치 비교 막대 → bar_chart
- 수평 막대 (항목 많을 때) → horizontal_bar
- 시간 흐름 추이 → line_chart / area_chart
- 비율·구성 → pie_chart
- 누적 비교 → stacked_bar

데이터 추출 원칙:
- 본문에 있는 숫자·수치를 그대로 사용 (추측 금지)
- 단위 반드시 포함 (%, 억원, 만명 등)
- 라벨은 본문 맥락에 맞는 명사 2~6글자
- comparison_kpi는 data 항목마다 before/after 필드 사용"""

_SPEC_PROMPT_TEMPLATE = """섹션 제목: {section_title}
키워드: {keyword}
섹터: {sector}

━━━ 섹션 전체 본문 ━━━
{section_text}
━━━━━━━━━━━━━━━━━━━━━

위 본문을 분석해서 가장 효과적인 시각화 설계서를 JSON으로 작성하세요.

지원 타입 (viz_type):
[HTML 카드] comparison_kpi, status_checklist, timeline, decision_matrix, dark_summary, cost_breakdown, kpi_cards, scenario_cards, highlight_card, insight_card, comparison_table
[matplotlib 차트] bar_chart, horizontal_bar, line_chart, area_chart, pie_chart, stacked_bar, grouped_bar

출력 형식 (JSON만, 설명 없이):
{{
  "viz_type": "타입명",
  "title": "이미지 제목 (25자 이내)",
  "subtitle": "부제목 또는 기준일 (선택)",
  "key_message": "핵심 1문장 (40자 이내, 선택)",
  "color_theme": "blue | green | red | orange | purple | mixed",
  "source": "출처 표기 (선택)",
  "data": [
    {{"label": "항목명", "value": 숫자또는문자열, "unit": "단위",
      "before": "이전값(comparison_kpi)", "after": "이후값(comparison_kpi)",
      "highlight": true, "diff": "+3.2%", "diff_positive": true}}
  ],
  "items": ["텍스트1", "텍스트2"],
  "items (status_checklist)": [{{"text": "내용", "status": "추천|대기 고려|대안"}}],
  "items (timeline)": [{{"date": "2025.01", "label": "이벤트명", "highlight": false}}],
  "items (decision_matrix)": [{{"situation": "상황", "action": "행동", "status": "추천|대기|대안"}}],
  "items (dark_summary)": [{{"label": "카테고리", "text": "내용"}}],
  "left_label": "왼쪽 헤더 (comparison_kpi/comparison_table)",
  "right_label": "오른쪽 헤더",
  "left_items": ["항목1"],
  "right_items": ["항목1"],
  "total": "합계 금액 (cost_breakdown)",
  "series": [{{"name": "시리즈명", "values": [숫자], "labels": ["항목"]}}],
  "x_label": "X축 설명",
  "y_label": "Y축 설명"
}}

주의:
- 본문에 없는 데이터 절대 금지
- bar_chart/line_chart 등 차트의 data.value는 반드시 float
- comparison_kpi의 data.value는 문자열 허용 (before/after 필드 우선)"""


def generate_image_spec(
    section_text: str,
    keyword: str,
    sector: str = "",
    section_title: str = "",
) -> dict[str, Any]:
    """섹션 전체 텍스트 → 이미지 설계서(dict) 생성.

    Args:
        section_text:  섹션 전체 본문 (자르지 않고 전달)
        keyword:       블로그 키워드
        sector:        섹터 (예: '경제·경기')
        section_title: 섹션 소제목

    Returns:
        설계서 dict. 실패 시 fallback 설계서 반환 (절대 None 반환 안 함).
    """
    prompt = _SPEC_PROMPT_TEMPLATE.format(
        section_title=section_title or keyword,
        keyword=keyword,
        sector=sector or "기타",
        section_text=section_text,
    )

    try:
        from shared.llm import invoke_text as _inv
        raw = _inv("analyzer", prompt, system=_SPEC_SYSTEM, max_tokens=800, temperature=0.2)
        if raw:
            # JSON 블록 추출
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                spec = json.loads(m.group(0))
                # viz_type 검증
                vt = spec.get("viz_type", "")
                if vt not in ALL_TYPES:
                    log.warning(f"[image_spec] 알 수 없는 viz_type '{vt}' → infographic으로 대체")
                    spec["viz_type"] = "infographic"
                # data 배열 value 타입 안전 변환
                for item in spec.get("data") or []:
                    try:
                        item["value"] = float(str(item.get("value", 0)).replace(",", ""))
                    except (ValueError, TypeError):
                        item["value"] = 0.0
                spec.setdefault("keyword", keyword)
                spec.setdefault("sector", sector)
                log.info(f"[image_spec] ✅ '{keyword}' → {spec['viz_type']} / '{spec.get('title','')}'")
                return spec
    except Exception as e:
        log.warning(f"[image_spec] LLM 설계서 생성 실패: {e}")
        _g_report("image", e, module=__name__)

    return _fallback_spec(section_text, keyword, sector, section_title)


def _fallback_spec(
    section_text: str,
    keyword: str,
    sector: str,
    section_title: str,
) -> dict[str, Any]:
    """LLM 실패 시 규칙 기반 기본 설계서."""
    # 숫자 패턴 추출 시도
    nums = re.findall(
        r'([가-힣]{1,6})\s*[:은는이가]\s*(\d[\d,]*\.?\d*)\s*(%|억|조|만|명|개|배|위|년|원)?',
        section_text,
    )
    data = []
    seen: set = set()
    for label, val, unit in nums[:6]:
        if val in seen:
            continue
        seen.add(val)
        try:
            data.append({
                "label": label[:6],
                "value": float(val.replace(",", "")),
                "unit": unit or "",
                "highlight": False,
            })
        except ValueError:
            pass

    viz_type = "bar_chart" if len(data) >= 3 else "kpi_cards" if data else "infographic"

    return {
        "viz_type": viz_type,
        "title": f"{keyword} 핵심 지표",
        "subtitle": "",
        "key_message": f"{keyword} 관련 핵심 정보를 확인하세요.",
        "data": data,
        "x_label": "",
        "y_label": "",
        "color_theme": "blue",
        "design_notes": "",
        "keyword": keyword,
        "sector": sector,
        "_fallback": True,
    }


def render_from_spec(spec: dict[str, Any], out_path: Path) -> Path:
    """설계서 → 이미지 파일 생성.

    렌더링 우선순위 (★ 사용자 박제 2026-05-19 — Plotly 최우선):
      [카드·레이아웃 타입] HTML_TYPES
        1순위: html_renderer  (Playwright HTML/CSS → 고품질 preview 수준)
        2순위: plotly_renderer
        3순위: matplotlib_renderer (최후 폴백)
      [데이터 차트 타입] CHART_TYPES / 공통
        1순위: plotly_renderer  (scale=3, 4200px급 — 최고 품질)
        2순위: matplotlib_renderer (DPI=250 폴백)
      [공통 최후 폴백]
        3순위: svg_renderer

    Args:
        spec:     generate_image_spec()이 반환한 설계서 dict
        out_path: 저장할 파일 경로 (.jpg 권장)

    Returns:
        생성된 이미지 파일 Path.
    Raises:
        RuntimeError: 렌더링 완전 실패 시.
    """
    viz_type = spec.get("viz_type", "infographic")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 카드·레이아웃 타입: html_renderer 우선 ───────────────────────
    if viz_type in HTML_TYPES:
        try:
            from JARVIS06_IMAGE.html_renderer import render as _html_render
            result = _html_render(spec, out_path)
            log.info(f"[image_spec] ✅ HTML 렌더링 완료: {result.name}")
            return result
        except Exception as e:
            log.warning(f"[image_spec] HTML 렌더링 실패 ({e}) → Plotly 폴백")
            _g_report("image", e, module=__name__)

    # ── 1순위: Plotly 렌더러 (★ 사용자 박제 2026-05-19 — 최고 품질) ──
    try:
        from JARVIS06_IMAGE.plotly_renderer import render as _plotly_render
        result = _plotly_render(spec, out_path)
        log.info(f"[image_spec] ✅ Plotly 렌더링 완료: {result.name}")
        return result
    except Exception as e:
        log.warning(f"[image_spec] Plotly 렌더링 실패 ({e}) → matplotlib 폴백")
        _g_report("image", e, module=__name__)

    # ── 2순위: matplotlib 렌더러 (폴백) ─────────────────────────────
    try:
        from JARVIS06_IMAGE.matplotlib_renderer import render as _mpl_render
        result = _mpl_render(spec, out_path)
        log.info(f"[image_spec] ✅ matplotlib 렌더링 완료: {result.name}")
        return result
    except Exception as e:
        log.warning(f"[image_spec] matplotlib 렌더링 실패 ({e}) → SVG 폴백")
        _g_report("image", e, module=__name__)

    # ── 최후 폴백: SVG 렌더러 ────────────────────────────────────────
    try:
        from JARVIS06_IMAGE.svg_renderer import render as _svg_render
        return _svg_render(spec, out_path)
    except Exception as e:
        log.error(f"[image_spec] SVG 렌더링도 실패: {e}")
        _g_report("image", e, module=__name__)
        raise RuntimeError(f"render_from_spec 완전 실패: {e}") from e
