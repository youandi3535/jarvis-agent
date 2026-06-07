"""JARVIS06_IMAGE/chart_advisor.py — LLM 차트 타입 어드바이저.

이미지 생성 전(썸네일 제외) 모든 경로에서 호출.
LLM이 데이터·컨텍스트를 보고 "어떤 시각화가 가장 적합한가" 판단.
키워드 매칭·하드코딩 규칙 0 — 순수 LLM 추론.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.chart_advisor")

# ── 차트 타입별 설명 (LLM 에게 보여주는 팔레트) ───────────────────────────
_CHART_TYPES: dict[str, str] = {
    "band_line":  "시계열 라인 + 역사적 구간 배경 밴드 — 값이 어느 구간(저/중/고)에 위치하는지 맥락 제공. flat 데이터에 특히 강력.",
    "line":       "시계열 추이 라인 — 값이 오르내리는 흐름 강조. 변동이 있는 데이터에 적합.",
    "area":       "시계열 추이 + 면적 강조 — 크기감과 누적 흐름을 동시에 표현.",
    "step":       "단계적 이산 변화 — 값이 특정 시점에 갑자기 바뀌는 정책·단계 데이터에 적합.",
    "iso_area":   "3D 아이소메트릭 면적 — 시각적 임팩트가 필요한 시계열.",
    "bar":        "범주별 수직 막대 — 항목 크기 비교. 수직 방향이 자연스러운 경우.",
    "barh":       "범주별 수평 막대 — 순위·랭킹·이름이 긴 항목 비교에 적합.",
    "iso_bar":    "3D 아이소메트릭 막대 — 시각적 임팩트가 필요한 범주 비교.",
    "pie":        "구성 비율 파이 — 전체 중 각 항목의 비중 표현.",
    "donut":      "구성 비율 도넛 — 파이와 동일하나 가운데 핵심 수치 강조 가능.",
    "scatter":    "2차원 분포 — X-Y 상관관계 (PER-ROE, 리스크-수익 등).",
    "combo":      "복합 차트 — 막대(규모) + 라인(추세)를 한 화면에 표현.",
}

# viz_type 팔레트 (make_smart_section_image / SVG 인포그래픽 경로)
_VIZ_TYPES: dict[str, str] = {
    "kpi_cards":        "핵심 수치 카드 — 숫자·지표·KPI 중심 섹션.",
    "comparison_table": "항목 비교표 — 여러 대상을 속성별로 비교.",
    "checklist":        "체크리스트 — 단계·항목·조건 나열.",
    "timeline":         "타임라인 — 시간 순서 사건·단계 흐름.",
    "scenario_cards":   "시나리오 카드 — 가능성·경우의 수 비교.",
    "flow_diagram":     "흐름도 — 원인→결과·프로세스 표현.",
    "highlight_card":   "강조 카드 — 핵심 인용·단일 메시지 강조.",
    "infographic":      "종합 인포그래픽 — 위 어느 것도 아닐 때 범용.",
}


def advise_chart_type(
    description: str,
    keyword: str,
    sector: str,
    context_text: str = "",
    used_types: list[str] | None = None,
) -> str:
    """LLM이 데이터·설명을 보고 최적 chart_type 반환.

    Returns: _CHART_TYPES 키 중 하나. 판단 실패 시 "" (호출자가 fallback 처리).
    """
    try:
        from shared.llm import invoke_text

        used_str = ", ".join(used_types) if used_types else "없음"
        types_str = "\n".join(f"- {k}: {v}" for k, v in _CHART_TYPES.items())

        # 중복 금지 타입 명시
        forbidden = set(used_types or [])

        prompt = (
            f"[차트 설명]\n{description}\n\n"
            f"[키워드: {keyword} / 섹터: {sector}]\n\n"
            f"[데이터·컨텍스트 (요약)]\n{context_text[:700] if context_text else '없음'}\n\n"
            f"[이 글에서 이미 사용한 타입 — 중복 금지]\n{used_str}\n\n"
            f"[선택 가능한 차트 타입]\n{types_str}\n\n"
            "위 데이터와 설명을 분석해 독자에게 가장 직관적이고 정보가 풍부한 차트 타입 1개를 선택하라.\n"
            "중복 금지 타입은 절대 선택하지 말 것.\n"
            "근거를 1문장으로 쓰고, 마지막 줄에 타입명만 출력하라.\n\n"
            "예시:\n"
            "근거: 금리가 2.5%로 flat하므로 역사적 구간 배경이 데이터에 맥락을 부여한다.\n"
            "선택: band_line"
        )

        raw = invoke_text("writer_fast", prompt, max_tokens=80, temperature=0.1)
        if not raw:
            return ""

        chosen = _parse_choice(raw, set(_CHART_TYPES.keys()) - forbidden)
        if chosen:
            log.info(f"[ChartAdvisor] '{keyword}' → {chosen}")
        return chosen

    except Exception as e:
        log.debug(f"[ChartAdvisor] 실패: {e}")
        return ""


def advise_viz_type(
    section_text: str,
    section_title: str,
    keyword: str,
    sector: str,
) -> str:
    """LLM이 섹션 텍스트를 보고 최적 viz_type 반환 (SVG 인포그래픽 경로).

    Returns: _VIZ_TYPES 키 중 하나. 실패 시 "".
    """
    try:
        from shared.llm import invoke_text

        types_str = "\n".join(f"- {k}: {v}" for k, v in _VIZ_TYPES.items())

        prompt = (
            f"[섹션 제목]\n{section_title}\n\n"
            f"[키워드: {keyword} / 섹터: {sector}]\n\n"
            f"[섹션 본문 (요약)]\n{section_text[:600]}\n\n"
            f"[선택 가능한 시각화 유형]\n{types_str}\n\n"
            "이 섹션 내용을 가장 잘 표현하는 시각화 유형 1개를 선택하라.\n"
            "근거를 1문장으로 쓰고, 마지막 줄에 타입명만 출력하라.\n\n"
            "예시:\n"
            "근거: 3개 투자 시나리오를 비교하므로 시나리오 카드가 가장 직관적이다.\n"
            "선택: scenario_cards"
        )

        raw = invoke_text("writer_fast", prompt, max_tokens=80, temperature=0.1)
        if not raw:
            return ""

        chosen = _parse_choice(raw, set(_VIZ_TYPES.keys()))
        if chosen:
            log.info(f"[ChartAdvisor/viz] '{keyword}' → {chosen}")
        return chosen

    except Exception as e:
        log.debug(f"[ChartAdvisor/viz] 실패: {e}")
        return ""


def _parse_choice(raw: str, valid: set[str]) -> str:
    """LLM 응답에서 선택된 타입명 파싱."""
    for line in reversed(raw.strip().split("\n")):
        line = line.strip()
        # "선택: band_line" 형태
        m = re.match(r"선택\s*:\s*(\S+)", line)
        if m and m.group(1) in valid:
            return m.group(1)
        # 마지막 줄이 타입명 단독
        if line in valid:
            return line
    # 전체 텍스트에서 유효 타입명 탐색 (마지막 매칭)
    found = [w for w in re.findall(r"\b\w+\b", raw) if w in valid]
    return found[-1] if found else ""


__all__ = ["advise_chart_type", "advise_viz_type"]
