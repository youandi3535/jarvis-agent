"""JARVIS06_IMAGE/plotly_renderer.py — Plotly 전문 차트 렌더러.

image_spec.generate_image_spec()이 반환한 설계서(spec) 기반으로
bar_chart / line_chart / pie_chart 등을 전문적인 PNG로 출력.

특징:
- 다크 테마 (#0d1117 배경) — 블로그 인라인 이미지 최적화
- 한국어 폰트 자동 감지 (NanumGothic → AppleGothic → 기본)
- 2x 레티나 해상도 (scale=2, 1200×720)
- 데이터 라벨·단위 자동 표시
- 핵심 항목 highlight 지원
"""
from __future__ import annotations

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

# ── 다크 테마 상수 ────────────────────────────────────────────────────
_BG        = "#0d1117"
_CARD      = "#161b22"
_GRID      = "#21262d"
_TEXT      = "#e6edf3"
_TEXT2     = "#8b949e"
_HIGHLIGHT = "#f0b429"   # 강조 항목 amber

# ── 라이트 테마 상수 ──────────────────────────────────────────────────
_BG_L        = "#ffffff"
_CARD_L      = "#f8f9fa"
_GRID_L      = "#e9ecef"
_TEXT_L      = "#212529"
_TEXT2_L     = "#6c757d"
_HIGHLIGHT_L = "#e67700"   # 라이트 테마 강조 (진한 amber)

# ── 동적 색상 생성 (매번 새로운 팔레트) ──────────────────────────────
def _get_dynamic_palette_plotly(topic: str = "chart", count: int = 5, theme: str = "dark") -> list[str]:
    """Plotly용 동적 색상 팔레트 생성."""
    try:
        from shared.llm import invoke_text
        mode = "light" if theme == "light" else "dark"
        prompt = f"For {mode} theme plotly chart about '{topic}', generate {count} distinguishable hex colors. Return JSON array: [\"#xxxxxx\", ...]"
        result = invoke_text("writer_fast", prompt, temperature=0.8)
        import json as _json
        return _json.loads(result)
    except Exception:
        # Fallback — 기본 색상 (seed로 다양화)
        import hashlib as _hash
        seed = int(_hash.md5(f"{topic}{theme}".encode()).hexdigest(), 16) % (2**32)
        import random as _rnd
        _rnd.seed(seed)
        base = {
            "light": ["#1565c0", "#2e7d32", "#e65100", "#c62828", "#4a148c", "#0097a7"],
            "dark": ["#58a6ff", "#3fb950", "#e3b341", "#f85149", "#a371f7", "#00d9ff"],
        }
        colors = base.get(theme, base["dark"])
        _rnd.shuffle(colors)
        return colors[:count]


def _theme_colors(theme: str, topic: str = "chart") -> dict:
    """테마별 색상 dict 반환 (동적 생성)."""
    palette = _get_dynamic_palette_plotly(topic, count=5, theme=theme)
    # 호환성: 기존 코드는 palettes["color_theme"]으로 접근. 이제 "dynamic" key에 새 팔레트
    palettes_compat = {
        "dynamic": palette,  # 새 동적 팔레트
        "blue": palette,     # 폴백 (같은 값)
        "green": palette,
        "red": palette,
        "orange": palette,
        "purple": palette,
        "mixed": palette,
    }
    if theme == "light":
        return {
            "bg": _BG_L, "card": _CARD_L, "grid": _GRID_L,
            "text": _TEXT_L, "text2": _TEXT2_L,
            "highlight": _HIGHLIGHT_L,
            "palettes": palettes_compat,
        }
    return {
        "bg": _BG, "card": _CARD, "grid": _GRID,
        "text": _TEXT, "text2": _TEXT2,
        "highlight": _HIGHLIGHT,
        "palettes": palettes_compat,
    }

# ── 한국어 폰트 자동 감지 ──────────────────────────────────────────────
def _detect_korean_font() -> str:
    from pathlib import Path as _P
    candidates = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    ]
    for c in candidates:
        if _P(c).exists():
            # Plotly는 font family 이름을 씀
            if "Nanum" in c:
                return "NanumGothic"
            if "Noto" in c:
                return "Noto Sans CJK KR"
            if "AppleSDGothic" in c:
                return "Apple SD Gothic Neo"
            return "AppleGothic"
    return "sans-serif"

_KO_FONT = _detect_korean_font()

# ── 공통 레이아웃 ─────────────────────────────────────────────────────
def _base_layout(title: str, subtitle: str = "", theme: str = "light") -> dict:
    tc = _theme_colors(theme)
    title_text = title
    if subtitle:
        title_text += f"<br><sup style='color:{tc['text2']}'>{subtitle}</sup>"
    return dict(
        title=dict(
            text=title_text,
            font=dict(family=_KO_FONT, size=26, color=tc["text"]),
            x=0.5, xanchor="center",
            y=0.97, yanchor="top",
        ),
        paper_bgcolor=tc["bg"],
        plot_bgcolor=tc["card"],
        font=dict(family=_KO_FONT, size=16, color=tc["text"]),
        margin=dict(l=60, r=40, t=90, b=60),
        showlegend=True,
        legend=dict(
            font=dict(size=14, color=tc["text2"]),
            bgcolor="rgba(0,0,0,0)",
            bordercolor=tc["grid"],
        ),
        xaxis=dict(
            showgrid=True, gridcolor=tc["grid"], gridwidth=1,
            linecolor=tc["grid"], tickfont=dict(size=14, color=tc["text2"]),
            tickangle=-20,
        ),
        yaxis=dict(
            showgrid=True, gridcolor=tc["grid"], gridwidth=1,
            linecolor=tc["grid"], tickfont=dict(size=14, color=tc["text2"]),
            zeroline=False,
        ),
    )


def _label_with_unit(val: float, unit: str) -> str:
    """값 + 단위 문자열 포맷."""
    if val == int(val):
        s = f"{int(val):,}"
    else:
        s = f"{val:,.1f}"
    return f"{s}{unit}" if unit else s


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 렌더러별 구현
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_bar(spec: dict, fig_obj, tc: dict | None = None) -> None:
    import plotly.graph_objects as go
    if tc is None: tc = _theme_colors("light")
    data   = spec.get("data") or []
    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [d.get("value", 0) for d in data]
    units  = [d.get("unit", "") for d in data]
    hi     = spec.get("highlight_index")
    palette = tc["palettes"].get(spec.get("color_theme", "blue"), tc["palettes"]["blue"])

    colors = []
    for i, d in enumerate(data):
        if d.get("highlight") or i == hi:
            colors.append(tc["highlight"])
        else:
            colors.append(palette[i % len(palette)])

    text_labels = [_label_with_unit(v, u) for v, u in zip(values, units)]

    fig_obj.add_trace(go.Bar(
        x=labels, y=values,
        marker_color=colors,
        marker=dict(color=colors, line=dict(color="rgba(0,0,0,0.08)", width=1)),
        text=text_labels,
        textposition="outside",
        textfont=dict(size=16, color=tc["text"]),
        width=0.6,
    ))
    fig_obj.update_layout(
        xaxis_title=spec.get("x_label", ""),
        yaxis_title=spec.get("y_label", ""),
        bargap=0.35,
    )


def _render_horizontal_bar(spec: dict, fig_obj, tc: dict | None = None) -> None:
    import plotly.graph_objects as go
    if tc is None: tc = _theme_colors("light")
    data   = spec.get("data") or []
    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [d.get("value", 0) for d in data]
    units  = [d.get("unit", "") for d in data]
    palette = tc["palettes"].get(spec.get("color_theme", "blue"), tc["palettes"]["blue"])

    colors = [
        tc["highlight"] if (d.get("highlight") or i == spec.get("highlight_index"))
        else palette[i % len(palette)]
        for i, d in enumerate(data)
    ]
    text_labels = [_label_with_unit(v, u) for v, u in zip(values, units)]

    fig_obj.add_trace(go.Bar(
        x=values, y=labels,
        orientation="h",
        marker=dict(color=colors, line=dict(color="rgba(0,0,0,0.06)", width=1)),
        text=text_labels,
        textposition="outside",
        textfont=dict(size=16, color=tc["text"]),
    ))
    fig_obj.update_layout(
        xaxis_title=spec.get("x_label", ""),
        yaxis=dict(autorange="reversed", tickfont=dict(size=16, color=tc["text"])),
        bargap=0.3,
    )


def _render_line(spec: dict, fig_obj, fill: bool = False, tc: dict | None = None) -> None:
    import plotly.graph_objects as go
    if tc is None: tc = _theme_colors("light")
    palette = tc["palettes"].get(spec.get("color_theme", "blue"), tc["palettes"]["blue"])
    series  = spec.get("series") or []

    if series:
        for si, s in enumerate(series):
            color = palette[si % len(palette)]
            vals  = s.get("values", [])
            cats  = s.get("labels") or spec.get("categories") or [str(i+1) for i in range(len(vals))]
            units = spec.get("data", [{}] * len(vals))
            text_labels = [
                _label_with_unit(v, (units[i].get("unit", "") if i < len(units) else ""))
                for i, v in enumerate(vals)
            ]
            fig_obj.add_trace(go.Scatter(
                x=cats, y=vals,
                mode="lines+markers+text",
                name=s.get("name", f"시리즈{si+1}"),
                line=dict(color=color, width=3),
                marker=dict(size=10, color=color,
                            line=dict(color=tc["bg"], width=2)),
                text=text_labels,
                textposition="top center",
                textfont=dict(size=14, color=color),
                fill="tozeroy" if fill else "none",
                fillcolor=f"rgba({_hex_to_rgb(color)},0.12)" if fill else None,
            ))
    else:
        data  = spec.get("data") or []
        cats  = spec.get("categories") or [d.get("label", str(i+1)) for i, d in enumerate(data)]
        vals  = [d.get("value", 0) for d in data]
        units = [d.get("unit", "") for d in data]
        color = palette[0]
        text_labels = [_label_with_unit(v, u) for v, u in zip(vals, units)]
        fig_obj.add_trace(go.Scatter(
            x=cats, y=vals,
            mode="lines+markers+text",
            name=spec.get("title", keyword_from_spec(spec)),
            line=dict(color=color, width=3),
            marker=dict(size=10, color=color,
                        line=dict(color=_BG, width=2)),
            text=text_labels,
            textposition="top center",
            textfont=dict(size=14, color=color),
            fill="tozeroy" if fill else "none",
            fillcolor=f"rgba({_hex_to_rgb(color)},0.12)" if fill else None,
        ))

    fig_obj.update_layout(
        xaxis_title=spec.get("x_label", ""),
        yaxis_title=spec.get("y_label", ""),
    )


def _render_pie(spec: dict, fig_obj, tc: dict | None = None) -> None:
    import plotly.graph_objects as go
    if tc is None: tc = _theme_colors("light")
    data    = spec.get("data") or []
    labels  = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values  = [d.get("value", 0) for d in data]
    units   = [d.get("unit", "") for d in data]
    palette = tc["palettes"].get(spec.get("color_theme", "mixed"), tc["palettes"]["mixed"])

    text_labels = [_label_with_unit(v, u) for v, u in zip(values, units)]

    fig_obj.add_trace(go.Pie(
        labels=labels,
        values=values,
        hole=0.38,
        marker=dict(
            colors=palette[:len(data)],
            line=dict(color=tc["bg"], width=2),
        ),
        textinfo="label+percent",
        textfont=dict(size=15, color=tc["text"]),
        hovertemplate="%{label}: %{customdata}<extra></extra>",
        customdata=text_labels,
        pull=[0.06 if d.get("highlight") else 0 for d in data],
    ))
    fig_obj.update_layout(showlegend=True)


def _render_grouped_bar(spec: dict, fig_obj, tc: dict | None = None) -> None:
    import plotly.graph_objects as go
    if tc is None: tc = _theme_colors("light")
    series   = spec.get("series") or []
    cats     = spec.get("categories") or []
    palette  = tc["palettes"].get(spec.get("color_theme", "blue"), tc["palettes"]["blue"])

    for si, s in enumerate(series):
        color = palette[si % len(palette)]
        vals  = s.get("values", [])
        units = [spec.get("data", [{}] * len(vals))[i].get("unit", "")
                 if i < len(spec.get("data", [])) else "" for i in range(len(vals))]
        text_labels = [_label_with_unit(v, u) for v, u in zip(vals, units)]
        fig_obj.add_trace(go.Bar(
            name=s.get("name", f"시리즈{si+1}"),
            x=cats,
            y=vals,
            marker_color=color,
            text=text_labels,
            textposition="outside",
            textfont=dict(size=14, color=tc["text"]),
        ))
    fig_obj.update_layout(barmode="group", bargap=0.2, bargroupgap=0.05)


def _render_scatter(spec: dict, fig_obj) -> None:
    import plotly.graph_objects as go
    data    = spec.get("data") or []
    # 동적 색상 생성 (BLOG_SUPREME_LAW 제11조)
    palette = _get_dynamic_palette_plotly(spec.get("title", "scatter"), count=1, theme="dark")
    color   = palette[0] if palette else "#58a6ff"  # fallback
    xs      = [d.get("x", d.get("value", i)) for i, d in enumerate(data)]
    ys      = [d.get("y", d.get("value", 0)) for d in data]
    labels  = [d.get("label", "") for d in data]
    sizes   = [max(12, min(40, abs(d.get("size", 14)))) for d in data]

    fig_obj.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="markers+text",
        text=labels,
        textposition="top center",
        textfont=dict(size=14, color=_TEXT),
        marker=dict(
            size=sizes,
            color=color,
            line=dict(color=_BG, width=1.5),
            opacity=0.85,
        ),
    ))
    fig_obj.update_layout(
        xaxis_title=spec.get("x_label", ""),
        yaxis_title=spec.get("y_label", ""),
    )


def _render_waterfall(spec: dict, fig_obj) -> None:
    import plotly.graph_objects as go
    data     = spec.get("data") or []
    # 동적 색상 생성 (BLOG_SUPREME_LAW 제11조)
    palette = _get_dynamic_palette_plotly(spec.get("title", "waterfall"), count=3, theme="dark")
    inc_color = palette[0] if len(palette) > 0 else "#3fb950"  # increasing
    dec_color = palette[1] if len(palette) > 1 else "#f85149"  # decreasing
    tot_color = palette[2] if len(palette) > 2 else "#58a6ff"  # totals

    labels   = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values   = [d.get("value", 0) for d in data]
    units    = [d.get("unit", "") for d in data]
    measures = [d.get("measure", "relative") for d in data]
    text_labels = [_label_with_unit(v, u) for v, u in zip(values, units)]

    fig_obj.add_trace(go.Waterfall(
        x=labels, y=values,
        measure=measures,
        text=text_labels,
        textposition="outside",
        textfont=dict(size=16, color=_TEXT),
        increasing=dict(marker_color=inc_color),
        decreasing=dict(marker_color=dec_color),
        totals=dict(marker_color=tot_color),
        connector=dict(line=dict(color=_GRID, width=1)),
    ))
    fig_obj.update_layout(
        yaxis_title=spec.get("y_label", ""),
    )


def _render_gauge(spec: dict, fig_obj) -> None:
    import plotly.graph_objects as go
    data   = spec.get("data") or [{}]
    d0     = data[0] if data else {}
    value  = d0.get("value", 0)
    unit   = d0.get("unit", "")
    maxval = max(value * 1.5, 100)
    # 동적 색상 생성 (BLOG_SUPREME_LAW 제11조)
    palette = _get_dynamic_palette_plotly(spec.get("title", "gauge"), count=3, theme="dark")
    bar_color = palette[0] if len(palette) > 0 else "#58a6ff"
    step_mid_rgb = _hex_to_rgb(palette[1] if len(palette) > 1 else "#fbbf24")
    step_high_rgb = _hex_to_rgb(palette[2] if len(palette) > 2 else "#f87171")

    fig_obj.add_trace(go.Indicator(
        mode="gauge+number+delta",
        value=value,
        number=dict(suffix=unit, font=dict(size=36, color=_TEXT)),
        gauge=dict(
            axis=dict(range=[0, maxval], tickcolor=_TEXT2,
                      tickfont=dict(size=14, color=_TEXT2)),
            bar=dict(color=bar_color),
            bgcolor=_CARD,
            bordercolor=_GRID,
            steps=[
                dict(range=[0, maxval * 0.5], color=f"rgba({step_high_rgb},0.2)"),
                dict(range=[maxval * 0.5, maxval * 0.8], color=f"rgba({step_mid_rgb},0.3)"),
            ],
            threshold=dict(
                line=dict(color=_HIGHLIGHT, width=3),
                thickness=0.8,
                value=value,
            ),
        ),
        title=dict(text=d0.get("label", spec.get("title", "")),
                   font=dict(size=18, color=_TEXT2)),
    ))
    fig_obj.update_layout(paper_bgcolor=_BG)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 핵심 메시지 어노테이션
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _add_key_message(fig, key_message: str, tc: dict | None = None) -> None:
    """차트 하단에 핵심 메시지 어노테이션 추가."""
    if not key_message:
        return
    if tc is None: tc = _theme_colors("light")
    hl = tc["highlight"]
    fig.add_annotation(
        text=f"▶ {key_message}",
        xref="paper", yref="paper",
        x=0.5, y=-0.13,
        showarrow=False,
        font=dict(size=15, color=hl, family=_KO_FONT),
        align="center",
        bgcolor=f"rgba({_hex_to_rgb(hl)},0.08)",
        bordercolor=hl,
        borderwidth=1,
        borderpad=8,
    )
    fig.update_layout(margin=dict(b=90))


def _render_dashboard(spec: dict, out_path, tc: dict) -> Path:
    """대시보드 타입 — 2~4개 패널을 subplot 그리드로 배치."""
    from plotly.subplots import make_subplots
    import plotly.graph_objects as go

    panels = spec.get("panels") or []
    if not panels:
        # panels 없으면 단일 bar_chart 폴백
        return render({**spec, "viz_type": "bar_chart"}, out_path)

    n = min(len(panels), 4)
    panels = panels[:n]
    cols = 2
    rows = (n + 1) // 2

    # subplot 타입 결정
    specs_grid = []
    for r in range(rows):
        row_spec = []
        for c in range(cols):
            idx = r * cols + c
            if idx < n:
                vt = panels[idx].get("viz_type", "bar_chart")
                row_spec.append({"type": "domain"} if vt == "pie_chart" else {"type": "xy"})
            else:
                row_spec.append({"type": "xy"})
        specs_grid.append(row_spec)

    subtitles = [panels[i].get("title", f"패널{i+1}") for i in range(n)]
    # 빈 패널 자리는 빈 문자열
    while len(subtitles) < rows * cols:
        subtitles.append("")

    fig = make_subplots(
        rows=rows, cols=cols,
        subplot_titles=subtitles,
        specs=specs_grid,
        horizontal_spacing=0.10,
        vertical_spacing=0.15,
    )

    for i, panel in enumerate(panels):
        row = i // cols + 1
        col = i % cols + 1
        vt  = panel.get("viz_type", "bar_chart")
        sub_tc = tc.copy()
        sub_tc["palettes"] = tc["palettes"]

        # 임시 Figure 에 트레이스 생성 후 main fig 로 이관
        tmp = go.Figure()
        if vt == "bar_chart":
            _render_bar(panel, tmp, sub_tc)
        elif vt == "horizontal_bar":
            _render_horizontal_bar(panel, tmp, sub_tc)
        elif vt in ("line_chart", "area_chart"):
            _render_line(panel, tmp, fill=(vt == "area_chart"), tc=sub_tc)
        elif vt == "pie_chart":
            _render_pie(panel, tmp, sub_tc)
        elif vt == "grouped_bar":
            _render_grouped_bar(panel, tmp, sub_tc)
        else:
            _render_bar(panel, tmp, sub_tc)

        for trace in tmp.data:
            fig.add_trace(trace, row=row, col=col)

    # 전체 레이아웃
    dash_title = spec.get("title", "")
    fig.update_layout(
        title=dict(
            text=dash_title,
            font=dict(family=_KO_FONT, size=26, color=tc["text"]),
            x=0.5, xanchor="center",
        ),
        paper_bgcolor=tc["bg"],
        plot_bgcolor=tc["card"],
        font=dict(family=_KO_FONT, size=14, color=tc["text"]),
        showlegend=False,
        margin=dict(l=50, r=50, t=100, b=60),
    )
    # 모든 subplot 축 스타일
    fig.update_xaxes(
        showgrid=True, gridcolor=tc["grid"], linecolor=tc["grid"],
        tickfont=dict(size=13, color=tc["text2"]),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=tc["grid"], linecolor=tc["grid"],
        tickfont=dict(size=13, color=tc["text2"]),
    )
    # subplot 제목 폰트
    for ann in fig.layout.annotations:
        ann.font.update(size=16, color=tc["text"], family=_KO_FONT)

    # 핵심 메시지
    if spec.get("key_message"):
        _add_key_message(fig, spec["key_message"], tc)

    out_path = Path(out_path).with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fig.write_image(str(out_path), width=1600, height=900 * rows // 1, scale=3)
        log.info(f"[plotly_renderer] ✅ dashboard {n}패널 → {out_path.name}")
        return out_path
    except Exception as e:
        log.warning(f"[plotly_renderer] dashboard kaleido 실패: {e}")
        _g_report("image", e, module=__name__)
        return _render_matplotlib_fallback(spec, out_path)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Matplotlib 폴백 렌더러 (Chrome 없이도 항상 동작)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _setup_mpl_korean_font() -> str | None:
    """Matplotlib 한국어 폰트 설정. 사용 가능한 폰트 이름 반환."""
    import matplotlib.font_manager as fm
    candidates = [
        ("NanumGothic",      ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf"]),
        ("Noto Sans CJK KR", ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                               "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"]),
        ("Apple SD Gothic Neo", ["/System/Library/Fonts/AppleSDGothicNeo.ttc"]),
        ("AppleGothic",      ["/System/Library/Fonts/Supplemental/AppleGothic.ttf"]),
    ]
    for name, paths in candidates:
        for p in paths:
            if Path(p).exists():
                fm.fontManager.addfont(p)
                return name
    return None


def _render_matplotlib_fallback(spec: dict, out_path: Path) -> Path:
    """Matplotlib 폴백 차트 — kaleido 실패 시. 라이트/다크 테마 지원."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import numpy as np

    viz       = spec.get("viz_type", "bar_chart")
    title     = spec.get("title", spec.get("keyword", ""))
    subtitle  = spec.get("subtitle", "")
    km        = spec.get("key_message", "")
    data      = spec.get("data") or []
    theme     = spec.get("theme", "light")
    tc        = _theme_colors(theme)
    palette   = tc["palettes"].get(spec.get("color_theme", "blue"), tc["palettes"]["blue"])

    plt.rcParams.update({
        "figure.facecolor": tc["bg"],
        "axes.facecolor":   tc["card"],
        "axes.edgecolor":   tc["grid"],
        "axes.labelcolor":  tc["text2"],
        "xtick.color":      tc["text2"],
        "ytick.color":      tc["text2"],
        "text.color":       tc["text"],
        "grid.color":       tc["grid"],
        "grid.linewidth":   0.8,
    })

    ko_font = _setup_mpl_korean_font()
    if ko_font:
        plt.rcParams["font.family"] = ko_font
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(12, 7.2), facecolor=tc["bg"])
    ax.set_facecolor(tc["card"])

    def _sv(v):
        try: return max(float(v), 0.001)
        except (TypeError, ValueError): return 0.001
    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [_sv(d.get("value", 0.001)) for d in data]
    units  = [d.get("unit", "") for d in data]
    hi     = [bool(d.get("highlight", False)) for d in data]
    colors = [tc["highlight"] if h else palette[i % len(palette)] for i, h in enumerate(hi)]

    if not labels:
        ax.text(0.5, 0.5, title or "데이터 없음",
                ha="center", va="center", fontsize=20, color=tc["text2"],
                transform=ax.transAxes)
    elif viz == "pie_chart":
        wedge_colors = [palette[i % len(palette)] for i in range(len(labels))]
        wedges, texts, autotexts = ax.pie(
            values, labels=labels, colors=wedge_colors,
            autopct="%1.1f%%", startangle=90,
            textprops=dict(color=tc["text"], fontsize=13),
            wedgeprops=dict(edgecolor=tc["bg"], linewidth=2),
        )
        for at in autotexts:
            at.set_color(tc["bg"])
            at.set_fontsize(12)
        ax.set_facecolor(tc["bg"])
    elif viz in ("line_chart", "area_chart"):
        series = spec.get("series") or []
        if series:
            cats = spec.get("categories") or [str(i) for i in range(len(series[0].get("values", [])))]
            for si, s in enumerate(series):
                vals = s.get("values", [])
                color = palette[si % len(palette)]
                ax.plot(cats, vals, marker="o", color=color,
                        linewidth=2.5, markersize=6, label=s.get("name", ""))
                if viz == "area_chart":
                    ax.fill_between(cats, vals, alpha=0.15, color=color)
        elif values:
            cats = labels
            ax.plot(cats, values, marker="o", color=palette[0],
                    linewidth=2.5, markersize=6)
            if viz == "area_chart":
                ax.fill_between(cats, values, alpha=0.15, color=palette[0])
        ax.legend(facecolor=tc["card"], edgecolor=tc["grid"],
                  labelcolor=tc["text"], fontsize=12)
        ax.grid(True, axis="y")
    elif viz == "horizontal_bar":
        y_pos = np.arange(len(labels))
        bars = ax.barh(y_pos, values, color=colors, height=0.6,
                       edgecolor=tc["bg"], linewidth=0.5)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels, fontsize=13)
        ax.invert_yaxis()
        ax.grid(True, axis="x", alpha=0.4)
        for bar, v, u in zip(bars, values, units):
            label_str = _label_with_unit(v, u)
            ax.text(bar.get_width() + max(values) * 0.01,
                    bar.get_y() + bar.get_height() / 2,
                    label_str, va="center", fontsize=12, color=tc["text"])
    else:
        x_pos = np.arange(len(labels))
        bars = ax.bar(x_pos, values, color=colors, width=0.6,
                      edgecolor=tc["bg"], linewidth=0.5)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(labels, fontsize=13, rotation=20, ha="right")
        ax.grid(True, axis="y", alpha=0.4)
        for bar, v, u in zip(bars, values, units):
            label_str = _label_with_unit(v, u)
            ax.text(bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + max(values, default=1) * 0.01,
                    label_str, ha="center", va="bottom", fontsize=12, color=tc["text"])

    x_label = spec.get("x_label", "")
    y_label = spec.get("y_label", "")
    if x_label: ax.set_xlabel(x_label, fontsize=13, color=tc["text2"])
    if y_label: ax.set_ylabel(y_label, fontsize=13, color=tc["text2"])

    full_title = title + (f"\n{subtitle}" if subtitle else "")
    fig.suptitle(full_title, fontsize=20, color=tc["text"], y=0.97, fontweight="bold")

    if km:
        hl = tc["highlight"]
        fig.text(0.5, 0.01, f"▶ {km}", ha="center", va="bottom",
                 fontsize=13, color=hl,
                 bbox=dict(facecolor=f"{hl}20", edgecolor=hl,
                           boxstyle="round,pad=0.4", linewidth=1))
        plt.subplots_adjust(bottom=0.12)

    plt.tight_layout(rect=[0, 0.05 if km else 0, 1, 0.95])
    out_path = Path(out_path).with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight",
                facecolor=tc["bg"], edgecolor="none")
    plt.close(fig)
    log.info(f"[plotly_renderer] ✅ matplotlib 폴백 {viz} → {out_path.name}")
    return out_path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 공개 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def render(spec: dict[str, Any], out_path: Path) -> Path:
    """Plotly 차트 렌더링 → PNG 저장.

    Args:
        spec:     generate_image_spec() 반환 설계서
        out_path: 저장 경로 (.png)

    Returns:
        저장된 이미지 Path.
    Raises:
        RuntimeError: 렌더링 실패 시.
    """
    import plotly.graph_objects as go

    viz   = spec.get("viz_type", "bar_chart")
    title = spec.get("title", spec.get("keyword", ""))
    sub   = spec.get("subtitle", "")
    theme = spec.get("theme", "light")   # 기본값: 라이트 테마
    tc    = _theme_colors(theme)

    # ── 대시보드 타입 (멀티 패널) ────────────────────────────────
    if viz == "dashboard":
        return _render_dashboard(spec, out_path, tc)

    layout = _base_layout(title, sub, theme)
    fig    = go.Figure(layout=layout)

    # viz_type → 렌더러 디스패치
    if viz == "bar_chart":
        _render_bar(spec, fig, tc)
        fig.update_layout(showlegend=False)
    elif viz == "horizontal_bar":
        _render_horizontal_bar(spec, fig, tc)
        fig.update_layout(showlegend=False)
    elif viz in ("line_chart", "area_chart"):
        _render_line(spec, fig, fill=(viz == "area_chart"), tc=tc)
    elif viz == "pie_chart":
        _render_pie(spec, fig, tc)
        fig.update_layout(plot_bgcolor="rgba(0,0,0,0)")
    elif viz == "grouped_bar":
        _render_grouped_bar(spec, fig, tc)
    elif viz == "scatter_chart":
        _render_scatter(spec, fig)
    elif viz == "waterfall_chart":
        _render_waterfall(spec, fig)
    elif viz == "gauge_chart":
        _render_gauge(spec, fig)
    else:
        log.warning(f"[plotly_renderer] 알 수 없는 viz_type '{viz}' → bar_chart 폴백")
        _render_bar(spec, fig, tc)

    # 핵심 메시지 어노테이션
    _add_key_message(fig, spec.get("key_message", ""), tc)

    # 디자인 노트 → 추가 주석
    design_notes = spec.get("design_notes", "")
    if design_notes:
        fig.add_annotation(
            text=design_notes[:60],
            xref="paper", yref="paper",
            x=1.0, y=1.02,
            showarrow=False,
            font=dict(size=13, color=tc["text2"], family=_KO_FONT),
            align="right",
        )

    out_path = Path(out_path).with_suffix(".png")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        fig.write_image(str(out_path), width=1400, height=840, scale=3)
        log.info(f"[plotly_renderer] ✅ kaleido {viz} → {out_path.name}")
        return out_path
    except Exception as e:
        log.warning(f"[plotly_renderer] kaleido 실패 ({e.__class__.__name__}), Matplotlib 폴백")
        _g_report("image", e, module=__name__)

    # ── Matplotlib 폴백 (항상 동작) ───────────────────────────────
    return _render_matplotlib_fallback(spec, out_path)


# ── 유틸 ─────────────────────────────────────────────────────────────

def keyword_from_spec(spec: dict) -> str:
    return spec.get("keyword") or spec.get("title") or "데이터"


def _hex_to_rgb(hex_color: str) -> str:
    """'#rrggbb' → 'r,g,b' 문자열."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"{r},{g},{b}"
    return "88,166,255"
