"""JARVIS06_IMAGE/matplotlib_renderer.py — Matplotlib 인포그래픽 렌더러.

image_spec.py 의 설계서(dict) → 고품질 JPG 이미지 생성.
프리뷰 스타일(KPI 카드·바 차트·라인·타임라인·체크리스트·비교표 등) 완전 지원.
외부 API 의존 없이 로컬 완결.

공개 API:
    render(spec: dict, out_path: Path) -> Path
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# ── 폰트 설정 ────────────────────────────────────────────────────────
def _setup_font() -> str | None:
    """한글 폰트 설정 후 폰트 이름 반환. 실패 시 None.

    ★ 종전 결함 (2026-07-20 전수감사 확정 — ERRORS [459]):
      폰트 경로가 *다른 머신의 샌드박스 절대경로* 두 개로 박혀 있었다
      (`/sessions/dazzling-upbeat-hypatia/...`). 이 호스트엔 둘 다 없어
      `_setup_font()` 이 항상 None 을 반환했고, 호출부는 반환값을 *버렸다*.
      → 폴백 렌더 시 한글이 전부 두부(□□□)로 나오는데 예외도 로그도 없이
      "✅ 렌더링 완료" 로 발행됐다. 정확히 '복사본을 진실로 믿는' 병.

    ★ 수정: 규칙 14(JARVIS06_IMAGE/CLAUDE.md) 대로 스타일 단일 진입점
      `style_engine.setup_chart_defaults()` 에 위임한다. 자체 폰트 탐색 금지.
      단일 진입점이 실패할 때만 설치된 pykrx 패키지에서 *동적으로* 경로를
      해석한다(하드코딩 금지 — 패키지 위치는 venv 마다 다르다).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # ① 정규 경로 — 차트 스타일 단일 진입점 (규칙 14)
    try:
        from JARVIS06_IMAGE.style_engine import setup_chart_defaults
        setup_chart_defaults()
        fam = plt.rcParams.get("font.family")
        name = fam[0] if isinstance(fam, (list, tuple)) and fam else fam
        if name and str(name) != "sans-serif":
            plt.rcParams["axes.unicode_minus"] = False
            return str(name)
    except Exception as e:
        log.warning(f"[matplotlib_renderer] setup_chart_defaults 실패 → 동적 폴백: {e}")

    # ② 폴백 — 설치된 pykrx 패키지에서 번들 폰트를 *동적으로* 찾는다
    try:
        import os
        import pykrx
        from matplotlib import font_manager
        cand = os.path.join(os.path.dirname(pykrx.__file__), "NanumBarunGothic.ttf")
        if os.path.exists(cand):
            font_manager.fontManager.addfont(cand)
            plt.rcParams["font.family"] = "NanumBarunGothic"
            plt.rcParams["axes.unicode_minus"] = False
            return "NanumBarunGothic"
    except Exception:
        pass
    return None


def font_effective() -> bool | None:
    """한글 폰트가 *실제로* 적용되는지 동작으로 확인 (설정 시도가 아니라).

    선택된 폰트 파일의 charmap 에 한글 글리프(U+ACBD '경')가 있는지 본다.
    반환: True(정상) / False(한글 깨짐 — 즉시 수리) / None(판정 불가)
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager as fm
        from matplotlib.ft2font import FT2Font
    except Exception:
        return None
    try:
        _setup_font()
        fam = plt.rcParams.get("font.family")
        path = fm.findfont(fm.FontProperties(family=fam))
        return 0xACBD in FT2Font(path).get_charmap()
    except Exception:
        return None


# ── 동적 색상 생성 (매번 새로운 팔레트) ──────────────────────────────
def _get_dynamic_colors(topic: str, count: int = 5) -> list[str]:
    """LLM으로 매번 새로운 색상 팔레트 생성. 같은 스타일 반복 금지."""
    try:
        from shared.llm import invoke_text
        prompt = f"Generate {count} harmonious hex colors for a data visualization about '{topic}'. Return ONLY a JSON array: [\"#xxxxxx\", ...]. NO explanation."
        result = invoke_text("writer_fast", prompt, temperature=0.8)
        import json
        colors = json.loads(result)
        return colors if len(colors) >= count else colors + ["#808080"] * (count - len(colors))
    except Exception:
        # Fallback — 색상 생성 실패 시 기본 6색 (여전히 충분히 다양)
        import random as _rnd
        _rnd.seed(hash(topic) % (2**32))  # 토픽별 reproducible하지만, 다른 토픽은 다른 색
        base = ["#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#98D8C8", "#F7DC6F"]
        _rnd.shuffle(base)
        return base[:count]

_ACCENT  = "#ffd740"   # 강조 노랑
_BG      = "#f8f9fa"   # 배경
_CARD    = "#ffffff"   # 카드 배경
_BORDER  = "#e0e0e0"   # 테두리
_TEXT    = "#212121"   # 주 텍스트
_TEXT2   = "#757575"   # 보조 텍스트
_DARK_BG = "#0d1b2a"   # 다크 배경


# ══════════════════════════════════════════════════════════════════════
# 공개 API
# ══════════════════════════════════════════════════════════════════════

def _strip_emoji(text: str) -> str:
    """이모지 제거 — NanumBarunGothic 미지원 글리프 경고 방지.

    주의: \U000024C2-\U0001F251 범위는 한글(U+AC00-U+D7AF)을 포함하므로 절대 사용 금지.
    안전한 이모지 범위만 제거한다.
    """
    import re
    return re.sub(
        r'[\U0001F300-\U0001FAFF'   # Misc Symbols / Emoticons (이모지 평면)
        r'\U00002702-\U000027B0]+', # Dingbats (한글 U+AC00 이전 — 안전)
        '', text
    ).strip()


def render(spec: dict[str, Any], out_path: Path) -> Path:
    """설계서 → JPG 이미지 저장 후 Path 반환."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _setup_font()
    out_path = Path(out_path)
    # 텍스트 필드에서 이모지 제거 (NanumBarunGothic 미지원)
    for field in ("title", "subtitle", "key_message"):
        if spec.get(field):
            spec = {**spec, field: _strip_emoji(spec[field])}
    out_path.parent.mkdir(parents=True, exist_ok=True)

    viz = spec.get("viz_type", "infographic")
    _fn = _DISPATCH.get(viz, _render_infographic)

    try:
        fig = _fn(spec)
        fig.savefig(str(out_path), dpi=250, bbox_inches="tight",
                    facecolor=fig.get_facecolor(), pad_inches=0.1)
        plt.close(fig)
        log.info(f"[mpl] ✅ {viz} → {out_path.name}")
        return out_path
    except Exception as e:
        log.warning(f"[mpl] {viz} 렌더링 실패 ({e}) → fallback_card")
        plt.close("all")
        try:
            fig = _render_fallback_card(spec)
            fig.savefig(str(out_path), dpi=250, bbox_inches="tight",
                        facecolor=fig.get_facecolor(), pad_inches=0.1)
            plt.close(fig)
        except Exception as e2:
            log.error(f"[mpl] fallback_card도 실패: {e2}")
            _g_report("image", e, module=__name__)
        return out_path


# ══════════════════════════════════════════════════════════════════════
# 내부 헬퍼
# ══════════════════════════════════════════════════════════════════════

def _pal(spec: dict) -> list[str]:
    """동적 색상 팔레트 생성 (매번 새로운 색상)."""
    topic = spec.get("title", spec.get("subtitle", "chart"))
    return _get_dynamic_colors(topic, count=5)


def _header_text(ax_h, title: str, subtitle: str = "", key_msg: str = "",
                 fig=None) -> None:
    """상단 헤더 영역 — ax_h.text() + rcParams 폰트만 사용.

    주의: FontProperties(fname=..., weight='bold') 는 단일 TTF 에서 합성 볼드를
    시도하며 CJK 글리프를 깨뜨린다. fontsize/fontweight kwargs 만 사용할 것.
    """
    ax_h.axis("off")
    ax_h.text(0.02, 0.85, title, color=_TEXT, va="top",
              transform=ax_h.transAxes, clip_on=False,
              fontsize=15, fontweight="bold")
    if subtitle:
        ax_h.text(0.02, 0.28, subtitle, color=_TEXT2, va="top",
                  transform=ax_h.transAxes, clip_on=False, fontsize=10)
    if key_msg:
        ax_h.text(0.98, 0.85, f"▶ {key_msg}", color="#1565c0",
                  va="top", ha="right", style="italic",
                  transform=ax_h.transAxes, clip_on=False, fontsize=9)


def _watermark(fig) -> None:
    pass  # ★ 사용자 박제 2026-05-19 — 워터마크 제거


# ══════════════════════════════════════════════════════════════════════
# 차트 렌더러 — bar_chart
# ══════════════════════════════════════════════════════════════════════

def _render_bar_chart(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp
    import numpy as np

    data    = spec.get("data") or []
    title   = spec.get("title", "")
    sub     = spec.get("subtitle", "")
    km      = spec.get("key_message", "")
    pal     = _pal(spec)

    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [float(d.get("value", 0)) for d in data]
    units  = [d.get("unit", "") for d in data]
    hi_idx = spec.get("highlight_index")
    colors = [pal[0] if (hi_idx is not None and i == hi_idx) else pal[min(i, len(pal)-1)]
              for i in range(len(data))]

    fig = plt.figure(figsize=(10, 6), facecolor=_BG)
    gs  = fig.add_gridspec(5, 1, hspace=0.05, left=0.08, right=0.95,
                           top=0.86, bottom=0.12)
    ax_h = fig.add_subplot(gs[0])
    ax   = fig.add_subplot(gs[1:])

    _header_text(ax_h, title, sub, km, fig=fig)
    ax.set_facecolor(_CARD)

    x = np.arange(len(labels))
    bars = ax.bar(x, values, color=colors, width=0.6, edgecolor="white", linewidth=1.5,
                  zorder=3)

    # 값 레이블
    for bar, val, unit in zip(bars, values, units):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + max(values) * 0.01,
                f"{val:,.1f}{unit}", ha="center", va="bottom",
                fontsize=9, fontweight="bold", color=_TEXT, zorder=4)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel(spec.get("y_label", ""), fontsize=9, color=_TEXT2)
    ax.yaxis.set_tick_params(labelsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4, zorder=0)
    ax.spines[:].set_visible(False)
    ax.tick_params(bottom=False)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(_BORDER)

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 차트 렌더러 — horizontal_bar
# ══════════════════════════════════════════════════════════════════════

def _render_horizontal_bar(spec: dict):
    import matplotlib.pyplot as plt
    import numpy as np

    data   = spec.get("data") or []
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)

    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [float(d.get("value", 0)) for d in data]
    units  = [d.get("unit", "") for d in data]
    hi_idx = spec.get("highlight_index")
    colors = [pal[0] if (hi_idx is not None and i == hi_idx) else pal[min(i, len(pal)-1)]
              for i in range(len(data))]

    n = len(labels)
    fig = plt.figure(figsize=(10, max(4, n * 0.7 + 2)), facecolor=_BG)
    gs  = fig.add_gridspec(5, 1, hspace=0.05, left=0.08, right=0.95,
                           top=0.88, bottom=0.05)
    ax_h = fig.add_subplot(gs[0])
    ax   = fig.add_subplot(gs[1:])
    _header_text(ax_h, title, sub, km, fig=fig)
    ax.set_facecolor(_CARD)

    y     = np.arange(n)
    max_v = max(values) if values else 1
    bars  = ax.barh(y, values, color=colors, height=0.55,
                    edgecolor="white", linewidth=1.2, zorder=3)

    for bar, val, unit in zip(bars, values, units):
        ax.text(bar.get_width() + max_v * 0.01, bar.get_y() + bar.get_height() / 2,
                f"{val:,.1f}{unit}", va="center", fontsize=9,
                fontweight="bold", color=_TEXT)

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlabel(spec.get("x_label", ""), fontsize=9, color=_TEXT2)
    ax.xaxis.set_tick_params(labelsize=9)
    ax.grid(axis="x", linestyle="--", alpha=0.4, zorder=0)
    ax.invert_yaxis()
    for spine in ["top", "right", "bottom"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_visible(True)
    ax.spines["left"].set_color(_BORDER)
    ax.tick_params(left=False)

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 차트 렌더러 — line_chart / area_chart
# ══════════════════════════════════════════════════════════════════════

def _render_line_chart(spec: dict):
    import matplotlib.pyplot as plt
    import numpy as np

    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)
    series = spec.get("series") or []
    data   = spec.get("data") or []
    is_area = spec.get("viz_type") == "area_chart"

    fig = plt.figure(figsize=(10, 6), facecolor=_BG)
    gs  = fig.add_gridspec(5, 1, hspace=0.05, left=0.09, right=0.95,
                           top=0.86, bottom=0.12)
    ax_h = fig.add_subplot(gs[0])
    ax   = fig.add_subplot(gs[1:])
    _header_text(ax_h, title, sub, km, fig=fig)
    ax.set_facecolor(_CARD)

    # series 형식 우선, 없으면 data → 단일 시리즈
    if not series and data:
        cats = spec.get("categories") or [d.get("label", str(i)) for i, d in enumerate(data)]
        vals = [float(d.get("value", 0)) for d in data]
        series = [{"name": title, "values": vals, "labels": cats}]

    for ci, s in enumerate(series):
        vals   = [float(v) for v in s.get("values", [])]
        labels = s.get("labels") or [str(i) for i in range(len(vals))]
        color  = pal[ci % len(pal)]
        x = range(len(vals))
        ax.plot(x, vals, color=color, lw=2.5, marker="o", markersize=5,
                label=s.get("name", ""), zorder=4)
        if is_area:
            ax.fill_between(x, vals, alpha=0.12, color=color, zorder=2)

        # 마지막 값 레이블
        if vals:
            ax.annotate(f"{vals[-1]:,.1f}", xy=(len(vals) - 1, vals[-1]),
                        xytext=(6, 0), textcoords="offset points",
                        fontsize=8, color=color, va="center")

    if series:
        ax.set_xticks(range(len(series[0].get("labels") or [])))
        ax.set_xticklabels(series[0].get("labels") or [], fontsize=9, rotation=30, ha="right")

    ax.set_ylabel(spec.get("y_label", ""), fontsize=9, color=_TEXT2)
    ax.grid(axis="y", linestyle="--", alpha=0.4, zorder=0)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(_BORDER)
    ax.spines["bottom"].set_color(_BORDER)
    if len(series) > 1:
        ax.legend(fontsize=9, loc="upper left", framealpha=0.7)

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — kpi_cards
# ══════════════════════════════════════════════════════════════════════

def _render_kpi_cards(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    data   = spec.get("data") or []
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)
    n      = len(data)
    if n == 0:
        return _render_fallback_card(spec)

    cols = min(4, n)
    rows = math.ceil(n / cols)
    fig_h = 2.8 + rows * 2.2
    fig   = plt.figure(figsize=(12, fig_h), facecolor=_BG)

    # 헤더
    ax_h = fig.add_axes([0.02, (fig_h - 1.0) / fig_h, 0.96, 0.9 / fig_h])
    _header_text(ax_h, title, sub, km, fig=fig)

    card_w = 0.92 / cols
    card_h = 0.8 / rows
    margin_x = 0.04 / (cols + 1)
    margin_y = 0.05 / (rows + 1)
    y_start  = (fig_h - 1.3) / fig_h

    for i, d in enumerate(data):
        row = i // cols
        col = i % cols
        x0 = 0.04 + col * (card_w + margin_x)
        y0 = y_start - (row + 1) * (card_h + margin_y)

        ax_c = fig.add_axes([x0, y0, card_w, card_h])
        ax_c.set_facecolor(_CARD)
        ax_c.axis("off")

        color = pal[i % len(pal)]
        # 컬러 상단 바
        rect = mp.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.02",
                                 facecolor=_CARD, edgecolor=color,
                                 linewidth=2.5, transform=ax_c.transAxes,
                                 clip_on=False)
        ax_c.add_patch(rect)

        label = d.get("label", f"항목{i+1}")
        val   = d.get("value", 0)
        unit  = d.get("unit", "")
        hi    = d.get("highlight", False)

        ax_c.text(0.5, 0.80, label, fontsize=10, color=_TEXT2,
                  ha="center", va="top", transform=ax_c.transAxes)
        ax_c.text(0.5, 0.42, f"{val:,.1f}", fontsize=22 if n <= 4 else 18,
                  fontweight="bold", color=color if hi else _TEXT,
                  ha="center", va="center", transform=ax_c.transAxes)
        ax_c.text(0.5, 0.13, unit, fontsize=9, color=_TEXT2,
                  ha="center", va="bottom", transform=ax_c.transAxes)

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — checklist
# ══════════════════════════════════════════════════════════════════════

def _render_checklist(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    items  = spec.get("items") or [d.get("label", "") for d in (spec.get("data") or [])]
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)

    n   = len(items)
    fig = plt.figure(figsize=(9, max(4, n * 0.72 + 2.5)), facecolor=_BG)
    ax  = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 9)
    ax.set_ylim(0, fig.get_size_inches()[1])
    ax.axis("off")

    fh = fig.get_size_inches()[1]
    # 헤더
    ax.text(0.4, fh - 0.45, title, fontsize=15, fontweight="bold",
            color=_TEXT, va="top")
    if sub:
        ax.text(0.4, fh - 0.90, sub, fontsize=9, color=_TEXT2, va="top")
    if km:
        ax.text(8.6, fh - 0.65, f"💡 {km}", fontsize=8.5, color="#1565c0",
                va="center", ha="right", style="italic")

    # 구분선
    ax.plot([0.3, 8.7], [fh - 1.1, fh - 1.1], color=_BORDER, lw=1)

    y = fh - 1.55
    for i, item in enumerate(items):
        color = pal[i % len(pal)]
        # 체크 원
        circ = plt.Circle((0.75, y), 0.22, color=color, zorder=3)
        ax.add_patch(circ)
        ax.text(0.75, y, "v", fontsize=8.5, color="white",
                ha="center", va="center", fontweight="bold", zorder=4)
        # 배경 바
        rect = mp.FancyBboxPatch((1.05, y - 0.3), 7.5, 0.60,
                                  boxstyle="round,pad=0.04",
                                  facecolor="#f0f4ff" if i % 2 == 0 else _CARD,
                                  edgecolor=_BORDER, linewidth=0.7)
        ax.add_patch(rect)
        ax.text(1.35, y, str(item), fontsize=10.5, color=_TEXT, va="center")
        y -= 0.72

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — timeline
# ══════════════════════════════════════════════════════════════════════

def _render_timeline(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    items  = spec.get("items") or []
    data   = spec.get("data") or []
    # items 없으면 data에서 생성
    if not items and data:
        items = [f"{d.get('label','')}: {d.get('value','')}{d.get('unit','')}"
                 for d in data]
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)

    n   = max(len(items), 1)
    fig = plt.figure(figsize=(10, max(4.5, n * 0.9 + 2.5)), facecolor=_BG)
    ax  = fig.add_axes([0, 0, 1, 1])
    fh  = fig.get_size_inches()[1]
    ax.set_xlim(0, 10)
    ax.set_ylim(0, fh)
    ax.axis("off")

    ax.text(0.5, fh - 0.45, title, fontsize=15, fontweight="bold",
            color=_TEXT, va="top")
    if sub:
        ax.text(0.5, fh - 0.92, sub, fontsize=9, color=_TEXT2, va="top")
    if km:
        ax.text(9.5, fh - 0.68, f"💡 {km}", fontsize=8.5, color="#1565c0",
                va="center", ha="right", style="italic")

    # 세로 중앙선
    cx = 3.0
    ax.plot([cx, cx], [fh - 1.35, fh - 1.35 - (n - 1) * 0.95 - 0.4],
            color=_BORDER, lw=2, zorder=1)

    y = fh - 1.55
    for i, item in enumerate(items):
        color = pal[i % len(pal)]
        circ  = plt.Circle((cx, y), 0.22, color=color, zorder=4)
        ax.add_patch(circ)
        # 카드
        rect = mp.FancyBboxPatch((cx + 0.35, y - 0.31), 5.8, 0.62,
                                  boxstyle="round,pad=0.04",
                                  facecolor=_CARD, edgecolor=color, linewidth=1.5,
                                  zorder=3)
        ax.add_patch(rect)
        ax.text(cx + 0.65, y, str(item), fontsize=10, color=_TEXT,
                va="center", zorder=5)
        y -= 0.95

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — comparison_table
# ══════════════════════════════════════════════════════════════════════

def _render_comparison_table(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    left_items  = spec.get("left_items") or []
    right_items = spec.get("right_items") or []
    left_label  = spec.get("left_label", "비교 A")
    right_label = spec.get("right_label", "비교 B")
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)

    # data 형식도 지원
    if not left_items and not right_items:
        data = spec.get("data") or []
        half = len(data) // 2
        left_items  = [f"{d.get('label','')}: {d.get('value','')}{d.get('unit','')}" for d in data[:half]]
        right_items = [f"{d.get('label','')}: {d.get('value','')}{d.get('unit','')}" for d in data[half:]]

    n   = max(len(left_items), len(right_items), 1)
    fig = plt.figure(figsize=(11, max(5, n * 0.8 + 3)), facecolor=_BG)
    ax  = fig.add_axes([0, 0, 1, 1])
    fh  = fig.get_size_inches()[1]
    ax.set_xlim(0, 11)
    ax.set_ylim(0, fh)
    ax.axis("off")

    ax.text(0.5, fh - 0.45, title, fontsize=15, fontweight="bold",
            color=_TEXT, va="top")
    if sub:
        ax.text(0.5, fh - 0.92, sub, fontsize=9, color=_TEXT2, va="top")

    # 왼쪽 헤더
    rect_l = mp.FancyBboxPatch((0.3, fh - 1.55), 4.7, 0.5,
                                boxstyle="round,pad=0.04",
                                facecolor=pal[0], edgecolor="none")
    ax.add_patch(rect_l)
    ax.text(2.65, fh - 1.30, left_label, fontsize=12, fontweight="bold",
            color="white", ha="center", va="center")

    # 오른쪽 헤더
    rect_r = mp.FancyBboxPatch((5.8, fh - 1.55), 4.7, 0.5,
                                boxstyle="round,pad=0.04",
                                facecolor=pal[1] if len(pal) > 1 else pal[0], edgecolor="none")
    ax.add_patch(rect_r)
    ax.text(8.15, fh - 1.30, right_label, fontsize=12, fontweight="bold",
            color="white", ha="center", va="center")

    y = fh - 2.05
    for i in range(n):
        bg = "#f0f4ff" if i % 2 == 0 else _CARD
        # 왼쪽 행
        if i < len(left_items):
            rect = mp.FancyBboxPatch((0.3, y - 0.30), 4.7, 0.58,
                                      boxstyle="round,pad=0.03",
                                      facecolor=bg, edgecolor=_BORDER, linewidth=0.6)
            ax.add_patch(rect)
            ax.text(0.60, y, str(left_items[i]), fontsize=10, color=_TEXT,
                    va="center")
        # 오른쪽 행
        if i < len(right_items):
            rect = mp.FancyBboxPatch((5.8, y - 0.30), 4.7, 0.58,
                                      boxstyle="round,pad=0.03",
                                      facecolor=bg, edgecolor=_BORDER, linewidth=0.6)
            ax.add_patch(rect)
            ax.text(6.10, y, str(right_items[i]), fontsize=10, color=_TEXT,
                    va="center")
        y -= 0.72

    if km:
        ax.text(5.5, 0.30, f"💡 {km}", fontsize=9, color="#1565c0",
                ha="center", va="bottom", style="italic")

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — scenario_cards / highlight_card / insight_card
# ══════════════════════════════════════════════════════════════════════

def _render_scenario_cards(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    data   = spec.get("data") or []
    items  = spec.get("items") or [d.get("label", "") for d in data]
    title  = spec.get("title", "")
    sub    = spec.get("subtitle", "")
    km     = spec.get("key_message", "")
    pal    = _pal(spec)

    n = max(len(items), 1)
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig = plt.figure(figsize=(12, 3.5 + rows * 2.0), facecolor=_BG)
    ax_h = fig.add_axes([0.02, 0.80, 0.96, 0.18])
    _header_text(ax_h, title, sub, km, fig=fig)

    card_w = 0.88 / cols
    for i, item in enumerate(items):
        row = i // cols
        col = i % cols
        x0  = 0.06 + col * (card_w + 0.02)
        y0  = 0.72 - row * 0.38
        ax_c = fig.add_axes([x0, y0, card_w - 0.02, 0.35])
        ax_c.set_facecolor(_CARD)
        ax_c.axis("off")
        color = pal[i % len(pal)]
        rect = mp.FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0.03",
                                  facecolor=_CARD, edgecolor=color, linewidth=2.5,
                                  transform=ax_c.transAxes)
        ax_c.add_patch(rect)
        ax_c.text(0.5, 0.88, f"Case {i+1}", fontsize=9, color=color,
                  ha="center", va="top", fontweight="bold",
                  transform=ax_c.transAxes)
        ax_c.text(0.5, 0.45, str(item), fontsize=9.5, color=_TEXT,
                  ha="center", va="center", transform=ax_c.transAxes,
                  wrap=True)

    _watermark(fig)
    return fig


def _render_highlight_card(spec: dict):
    """단일 강조 카드 — 텍스트 메시지 중심."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    title = spec.get("title", "")
    km    = spec.get("key_message", "") or spec.get("design_notes", "")
    items = spec.get("items") or [d.get("label", "") for d in (spec.get("data") or [])]
    pal   = _pal(spec)

    fig = plt.figure(figsize=(10, 5), facecolor=_BG)
    ax  = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.set_facecolor(_CARD)
    ax.axis("off")

    # 상단 색 바
    rect = mp.FancyBboxPatch((0.0, 0.88), 1.0, 0.12,
                              boxstyle="square,pad=0",
                              facecolor=pal[0], edgecolor="none",
                              transform=ax.transAxes)
    ax.add_patch(rect)
    ax.text(0.5, 0.94, title, fontsize=14, fontweight="bold",
            color="white", ha="center", va="center",
            transform=ax.transAxes)

    if km:
        ax.text(0.5, 0.76, km, fontsize=11, color="#1565c0",
                ha="center", va="center", style="italic",
                transform=ax.transAxes)

    y = 0.60
    for i, item in enumerate(items[:6]):
        ax.text(0.06, y, f"• {item}", fontsize=10.5, color=_TEXT,
                va="top", transform=ax.transAxes)
        y -= 0.13

    _watermark(fig)
    return fig


def _render_insight_card(spec: dict):
    return _render_highlight_card(spec)


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — pie_chart
# ══════════════════════════════════════════════════════════════════════

def _render_pie_chart(spec: dict):
    import matplotlib.pyplot as plt

    data  = spec.get("data") or []
    title = spec.get("title", "")
    sub   = spec.get("subtitle", "")
    km    = spec.get("key_message", "")
    pal   = _pal(spec)

    def _sv(v):
        try: return max(float(v), 0.001)
        except (TypeError, ValueError): return 0.001
    labels = [d.get("label", f"항목{i+1}") for i, d in enumerate(data)]
    values = [_sv(d.get("value", 0)) for d in data]
    hi_idx = spec.get("highlight_index")
    explode = [0.05 if i == hi_idx else 0 for i in range(len(data))]
    colors  = [pal[i % len(pal)] for i in range(len(data))]

    fig = plt.figure(figsize=(10, 6), facecolor=_BG)
    gs  = fig.add_gridspec(5, 1, hspace=0.05, left=0.05, right=0.95,
                           top=0.88, bottom=0.05)
    ax_h = fig.add_subplot(gs[0])
    ax   = fig.add_subplot(gs[1:])
    _header_text(ax_h, title, sub, km, fig=fig)
    ax.set_facecolor(_CARD)

    wedges, texts, autotexts = ax.pie(
        values, labels=labels, colors=colors, explode=explode,
        autopct="%1.1f%%", startangle=90,
        textprops={"fontsize": 9},
        wedgeprops={"edgecolor": "white", "linewidth": 2},
    )
    for at in autotexts:
        at.set_fontsize(8)
        at.set_color("white")
        at.set_fontweight("bold")

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 인포그래픽 — 일반 infographic (텍스트 + 바)
# ══════════════════════════════════════════════════════════════════════

def _render_infographic(spec: dict):
    """범용 인포그래픽 — data 있으면 horizontal_bar, 없으면 checklist 스타일."""
    data  = spec.get("data") or []
    items = spec.get("items") or []

    if data and all(isinstance(d.get("value"), (int, float)) for d in data):
        return _render_horizontal_bar(spec)
    if items:
        return _render_checklist(spec)
    return _render_highlight_card(spec)


# ══════════════════════════════════════════════════════════════════════
# 폴백 카드
# ══════════════════════════════════════════════════════════════════════

def _render_fallback_card(spec: dict):
    import matplotlib.pyplot as plt
    import matplotlib.patches as mp

    title = spec.get("title", spec.get("keyword", "데이터"))
    km    = spec.get("key_message", "")
    pal   = _pal(spec)

    fig = plt.figure(figsize=(9, 4.5), facecolor=_BG)
    ax  = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.set_facecolor(_CARD)
    ax.axis("off")

    rect = mp.FancyBboxPatch((0, 0.78), 1, 0.22,
                              boxstyle="square,pad=0",
                              facecolor=pal[0], edgecolor="none",
                              transform=ax.transAxes)
    ax.add_patch(rect)
    ax.text(0.5, 0.89, title, fontsize=14, fontweight="bold",
            color="white", ha="center", va="center",
            transform=ax.transAxes)
    if km:
        ax.text(0.5, 0.55, km, fontsize=10.5, color="#1565c0",
                ha="center", va="center", style="italic",
                transform=ax.transAxes)

    _watermark(fig)
    return fig


# ══════════════════════════════════════════════════════════════════════
# 라우팅 테이블
# ══════════════════════════════════════════════════════════════════════

_DISPATCH: dict[str, Any] = {
    "bar_chart":        _render_bar_chart,
    "horizontal_bar":   _render_horizontal_bar,
    "line_chart":       _render_line_chart,
    "area_chart":       _render_line_chart,
    "pie_chart":        _render_pie_chart,
    "scatter_chart":    _render_bar_chart,      # fallback
    "grouped_bar":      _render_bar_chart,      # fallback
    "waterfall_chart":  _render_bar_chart,      # fallback
    "gauge_chart":      _render_kpi_cards,      # fallback
    "kpi_cards":        _render_kpi_cards,
    "comparison_table": _render_comparison_table,
    "infographic":      _render_infographic,
    "timeline":         _render_timeline,
    "checklist":        _render_checklist,
    "scenario_cards":   _render_scenario_cards,
    "flow_diagram":     _render_checklist,      # fallback
    "highlight_card":   _render_highlight_card,
    "insight_card":     _render_insight_card,
    "dashboard":        _render_bar_chart,      # fallback
}
