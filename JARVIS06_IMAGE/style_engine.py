"""JARVIS06_IMAGE/style_engine.py — 차트 스타일 단일 진입점 (★ 사용자 박제 2026-05-25)

★ 규칙: 차트 폰트/여백/figsize 관련 숫자는 이 파일의 CHART_STYLE 만 수정. 다른 파일 직접 수정 금지.
★ 적용: setup_chart_defaults() 를 차트 함수 최상단에서 1회 호출하면 모든 설정 적용됨.

핵심 변경 (2026-05-25):
  - 폰트 2배 + 볼드 (가독성 개선)
  - tight_layout pad=0.2 (여백 최소화 → 내용이 전체 이미지를 채움)
  - figsize 표준화 (가로형 = STD, 정사각형 = SQUARE, 세로형 = TALL)

사용법:
    from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE
    setup_chart_defaults()  # 차트 함수 최상단 1회 호출
    fig, ax = plt.subplots(figsize=CHART_STYLE["FIGSIZE_STD"])
    ...
    plt.tight_layout(pad=CHART_STYLE["TIGHT_PAD"])
"""
from __future__ import annotations
import os

# ★ 차트 스타일 단일 진입점 — 여기만 수정하면 전체 차트에 적용
CHART_STYLE: dict = {
    # 폰트 크기 (2026-05-25: 2배 확대 + bold)
    "FONT_TITLE":      28,   # 차트 제목
    "FONT_LABEL":      22,   # 축 레이블, 카테고리명
    "FONT_VALUE":      20,   # 데이터 값 표시
    "FONT_TICK":       18,   # 축 눈금
    "FONT_CAPTION":    16,   # 캡션, 보조 텍스트
    "FONT_SMALL":      14,   # 최소 글씨 (14px 미만 금지)
    "FONT_WEIGHT":     "bold",  # 전체 굵기

    # figsize 표준 (단위: inch, dpi=150 기준)
    "FIGSIZE_STD":     (12, 5.5),   # 가로형 (일반 차트)
    "FIGSIZE_WIDE":    (13, 5.0),   # 넓은 가로형
    "FIGSIZE_SQUARE":  (9,  7.5),   # 정사각 근접 (레이더 등)
    "FIGSIZE_TALL":    (10, 8.0),   # 세로형
    "FIGSIZE_2COL":    (13, 5.5),   # 2열 서브플롯

    # 여백 (내용이 이미지 전체를 채우도록 최소화)
    "TIGHT_PAD":       0.3,   # plt.tight_layout(pad=)
    "DPI":             150,   # 저장 해상도

    # 선/막대 두께
    "LINE_WIDTH":      2.5,
    "BAR_WIDTH":       0.65,
}


def setup_chart_defaults(font_path: str | None = None) -> None:
    """★ 모든 차트 함수 최상단에서 1회 호출 — matplotlib 전역 설정 적용.

    이 함수 하나가 CHART_STYLE 값을 matplotlib rcParams 에 주입.
    개별 차트에서 fontsize= 를 직접 쓰면 이 설정이 무시되므로 금지.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm

    # 한글 폰트 설정
    _fp = font_path
    if _fp is None:
        for candidate in [
            '/System/Library/Fonts/AppleSDGothicNeo.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        ]:
            if os.path.exists(candidate):
                _fp = candidate
                break

    if _fp:
        plt.rcParams['font.family'] = fm.FontProperties(fname=_fp).get_name()

    s = CHART_STYLE
    plt.rcParams.update({
        'axes.unicode_minus':     False,
        'font.size':              s["FONT_LABEL"],
        'font.weight':            s["FONT_WEIGHT"],
        'axes.titlesize':         s["FONT_TITLE"],
        'axes.titleweight':       s["FONT_WEIGHT"],
        'axes.labelsize':         s["FONT_LABEL"],
        'axes.labelweight':       s["FONT_WEIGHT"],
        'xtick.labelsize':        s["FONT_TICK"],
        'ytick.labelsize':        s["FONT_TICK"],
        'legend.fontsize':        s["FONT_CAPTION"],
        'figure.titlesize':       s["FONT_TITLE"],
        'lines.linewidth':        s["LINE_WIDTH"],
    })
import json
import logging
from typing import Any

log = logging.getLogger("jarvis")


def generate_style_spec(chart_type: str, theme: str, context: str = "") -> dict:
    """LLM이 동적으로 차트 스타일을 창작.

    Args:
        chart_type: 차트 종류 (overview, radar, factors, timeline, mechanism, applications)
        theme:      테마명 (예: "AI 로봇")
        context:    추가 컨텍스트 (예: "금융" 섹터)

    Returns:
        {"layout": str, "primary_color": str, "accent_color": str,
         "style": str, "typography": dict, ...}
    """
    from shared.llm import invoke_text

    chart_desc = {
        "overview": "테마의 핵심 개요를 나타내는 차트",
        "radar": "5개 지표를 다차원으로 비교하는 차트",
        "factors": "상승/하락 요인을 대비시키는 차트",
        "timeline": "5단계 투자 프로세스를 보여주는 차트",
        "mechanism": "테마의 구조와 원리를 설명하는 차트",
        "applications": "주요 활용 분야를 소개하는 차트",
    }

    desc = chart_desc.get(chart_type, chart_type)
    context_str = f" ({context} 섹터)" if context else ""

    prompt = f"""'{theme}'{context_str} 테마의 {desc}를 그리는 **완전히 새로운** 스타일을 창작하세요.

창작해야 할 스타일 요소:
1. layout: 어떻게 배치할 것인가?
   - overview: 'cards_4' / 'tiles_3' / 'grid_2x2' / 'list_vertical' / 'stacked' 중 1개 (매번 다르게)
   - radar: 'polar' / 'spider' / 'hexagon' / 'circular' / 'polygonal' 중 1개
   - factors: 'bars_horizontal' / 'bars_vertical' / 'split' / 'bubble' / 'gauge' 중 1개
   - timeline: 'linear' / 'circular' / 'tree' / 'flow' / 'stepped' 중 1개
   - mechanism: 'pyramid' / 'flow' / 'cycle' / 'tree' / 'linear' 중 1개
   - applications: 'grid' / 'cards' / 'list' / 'carousel' / 'hexagon' 중 1개

2. primary_color: 주색상 (hex, #로 시작, 대비 높은 색상)
   - 어떤 색상이 '{theme}' 테마를 가장 잘 표현할까?

3. accent_color: 보조색상 (hex, 주색상과 조화로운)

4. bg_style: 배경 스타일
   - 'minimal' / 'light_gradient' / 'dark' / 'pattern' / 'subtle' 중 1개

5. font_style: 텍스트 스타일
   - weight: 'normal' / 'bold' / 'black' 중 1개
   - size_ratio: 0.8 ~ 1.2 (기본값 1.0 대비)

6. shape_style: 도형 스타일 (overview/applications/timeline 등)
   - 'rounded' / 'sharp' / 'organic' / 'minimal' 중 1개

7. shadow_depth: 그림자 강도
   - 'none' / 'subtle' / 'medium' / 'bold' 중 1개

응답 형식 (JSON만, 설명 없음):
{{
  "layout": "...",
  "primary_color": "#...",
  "accent_color": "#...",
  "bg_style": "...",
  "font_weight": "...",
  "font_size_ratio": 1.0,
  "shape_style": "...",
  "shadow_depth": "...",
  "notes": "이 스타일이 나타내는 느낌 (1문장)"
}}
"""

    try:
        response = invoke_text(
            "writer_fast",
            prompt,
            temperature=0.8,  # 높은 창의성
            max_tokens=300
        )

        # JSON 추출 (마크다운 코드블록 제거)
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1].lstrip("json").strip()

        spec = json.loads(response)
        log.info(f"[STYLE] {chart_type}({theme}): layout={spec.get('layout')}, "
                f"color={spec.get('primary_color')}")
        return spec

    except Exception as e:
        log.warning(f"[STYLE] 스타일 생성 실패: {e}, 폴백 사용")
        return _get_fallback_style(chart_type)


def _get_fallback_style(chart_type: str) -> dict:
    """LLM 실패 시 기본 스타일 (매번 다르지는 않음, 하지만 유효함)."""
    fallbacks = {
        "overview": {
            "layout": "cards_4", "primary_color": "#4f46e5", "accent_color": "#0891b2",
            "bg_style": "light_gradient", "font_weight": "bold", "font_size_ratio": 1.0,
            "shape_style": "rounded", "shadow_depth": "subtle"
        },
        "radar": {
            "layout": "polar", "primary_color": "#4f46e5", "accent_color": "#059669",
            "bg_style": "minimal", "font_weight": "normal", "font_size_ratio": 1.0,
            "shape_style": "minimal", "shadow_depth": "none"
        },
        "factors": {
            "layout": "bars_horizontal", "primary_color": "#22c55e", "accent_color": "#ef4444",
            "bg_style": "subtle", "font_weight": "normal", "font_size_ratio": 1.0,
            "shape_style": "sharp", "shadow_depth": "subtle"
        },
        "timeline": {
            "layout": "linear", "primary_color": "#4f46e5", "accent_color": "#d97706",
            "bg_style": "minimal", "font_weight": "bold", "font_size_ratio": 1.0,
            "shape_style": "rounded", "shadow_depth": "medium"
        },
        "mechanism": {
            "layout": "flow", "primary_color": "#7c3aed", "accent_color": "#0891b2",
            "bg_style": "subtle", "font_weight": "normal", "font_size_ratio": 1.0,
            "shape_style": "organic", "shadow_depth": "subtle"
        },
        "applications": {
            "layout": "grid", "primary_color": "#7c3aed", "accent_color": "#059669",
            "bg_style": "light_gradient", "font_weight": "bold", "font_size_ratio": 1.0,
            "shape_style": "rounded", "shadow_depth": "medium"
        },
    }
    return fallbacks.get(chart_type, fallbacks["overview"])


def apply_style_to_chart(fig, ax, spec: dict) -> None:
    """matplotlib figure/axes에 스타일 spec 적용.

    Args:
        fig:  matplotlib.figure.Figure
        ax:   matplotlib.axes.Axes 또는 list[Axes]
        spec: generate_style_spec() 반환값
    """
    try:
        # 단일 axes vs 복수 axes 처리
        axes_list = ax if isinstance(ax, list) else [ax]

        # 배경색 결정
        bg_color = _get_bg_color(spec.get("bg_style", "minimal"))
        fig.patch.set_facecolor(bg_color)

        for ax_item in axes_list:
            if ax_item is None:
                continue

            # axes 배경색
            ax_item.set_facecolor(bg_color)

            # 테두리 (그림자)
            shadow = spec.get("shadow_depth", "subtle")
            for spine in ax_item.spines.values():
                spine.set_visible(shadow != "none")
                if shadow == "bold":
                    spine.set_linewidth(2)
                elif shadow == "medium":
                    spine.set_linewidth(1.5)
                else:
                    spine.set_linewidth(0.8)

        log.debug(f"[STYLE] 적용: bg={spec.get('bg_style')}, "
                 f"shadow={spec.get('shadow_depth')}")

    except Exception as e:
        log.warning(f"[STYLE] 스타일 적용 중 오류: {e} (무시됨)")


def _get_bg_color(bg_style: str) -> str:
    """bg_style에 해당하는 배경색 반환."""
    colors = {
        "minimal": "#ffffff",
        "light_gradient": "#fafbff",
        "dark": "#0d1b2a",
        "pattern": "#f8f9fc",
        "subtle": "#f5f7fa",
    }
    return colors.get(bg_style, "#ffffff")


def hex_to_rgb(hex_color: str) -> tuple:
    """hex 색상을 RGB 튜플로 변환."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def get_color_from_spec(spec: dict, color_key: str = "primary_color") -> str:
    """spec에서 색상 추출."""
    return spec.get(color_key, "#4f46e5")


def generate_sector_colors(sector: str, keyword: str = "") -> dict:
    """섹터/키워드에 맞는 동적 색상 팔레트 생성. 매번 다른 색상."""
    try:
        from shared.llm import invoke_text
        prompt = f"""Generate a color palette for trend analysis.
Sector: {sector}
Theme: {keyword}

Return JSON (colors array, primary_color, accent_color, up_color, down_color):
{{
  "primary_color": "#...",
  "accent_color": "#...",
  "up_color": "#...",
  "down_color": "#...",
  "neutral_color": "#...",
  "bg_color": "#...",
  "text_color": "#...",
  "border_color": "#..."
}}"""
        response = invoke_text("writer_fast", prompt, temperature=0.8, max_tokens=200)
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1].lstrip("json").strip()

        palette = {}
        try:
            import json
            palette = json.loads(response)
        except:
            pass

        # 폴백: 기본 팔레트
        if not palette or "primary_color" not in palette:
            palette = {
                "primary_color": "#4f46e5",
                "accent_color": "#0891b2",
                "up_color": "#10b981",
                "down_color": "#ef4444",
                "neutral_color": "#6b7280",
                "bg_color": "#ffffff",
                "text_color": "#111827",
                "border_color": "#e5e7eb"
            }

        return palette
    except Exception as e:
        log.warning(f"[STYLE] 섹터 색상 생성 실패: {e}, 폴백 사용")
        return {
            "primary_color": "#4f46e5",
            "accent_color": "#0891b2",
            "up_color": "#10b981",
            "down_color": "#ef4444",
            "neutral_color": "#6b7280",
            "bg_color": "#ffffff",
            "text_color": "#111827",
            "border_color": "#e5e7eb"
        }


def _interpolate_color(hex1: str, hex2: str, ratio: float) -> str:
    """두 hex 색상을 보간하여 중간색 반환. ratio=0 → hex1, ratio=1 → hex2."""
    def _hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(r, g, b):
        return f'#{int(r):02x}{int(g):02x}{int(b):02x}'

    try:
        r1, g1, b1 = _hex_to_rgb(hex1)
        r2, g2, b2 = _hex_to_rgb(hex2)
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        return _rgb_to_hex(r, g, b)
    except:
        return hex1


__all__ = [
    "generate_style_spec",
    "apply_style_to_chart",
    "hex_to_rgb",
    "get_color_from_spec",
    "generate_sector_colors",
    "_interpolate_color",
]
