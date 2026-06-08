"""JARVIS06_IMAGE/isometric_charts.py
아이소메트릭 3D 차트 렌더러 — matplotlib Polygon 패치 기반

make_iso_bar_chart(labels, values, title, keyword, sector, out_path, run_id='') → str
make_iso_area_chart(labels, values, title, keyword, sector, out_path, run_id='') → str
"""
from __future__ import annotations

import colorsys
import hashlib
import math
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon
from matplotlib.collections import PatchCollection
import matplotlib.font_manager as _fm

try:
    from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE as _CS
except ImportError:
    def setup_chart_defaults(*a, **kw): pass
    _CS = {"FONT_TITLE": 28, "FONT_LABEL": 22, "FONT_CAPTION": 16, "FONT_SMALL": 14}

# 한국어 폰트 자동 선택
_KR_FONT = next(
    (f.name for f in _fm.fontManager.ttflist
     if any(k in f.name for k in ['Apple SD Gothic', 'Nanum Gothic', 'AppleGothic'])),
    'DejaVu Sans'
)

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

# ── 상수 ─────────────────────────────────────────────────────────
_BG_POOL = ['#F6F8FF', '#FFF8F0', '#F0FFF8', '#FFF0F8', '#F8F0FF', '#F0F8FF', '#FFFBF0', '#F5F5FF']


def _iso_bg(run_id: str) -> str:
    if not run_id:
        return _BG_POOL[0]
    import hashlib as _hlib
    return _BG_POOL[int(_hlib.md5(run_id.encode()).hexdigest()[:4], 16) % len(_BG_POOL)]


_BG = _BG_POOL[0]  # 기본값

_PALETTE = [
    '#3A86FF', '#50A060', '#FF8C00', '#E05555', '#5BC0DE',
    '#2CA88A', '#F28B30', '#0077B6', '#70C94C', '#E07B29',
    '#2196F3', '#1A936F', '#FF6840', '#3D7EBF', '#C47A1E',
]
# ★ ERRORS [175] 2026-05-26: 보라/분홍/마젠타 계열 전부 제거
# → 파랑·초록·주황·청록·빨강 계열 전문 금융 배색 15색으로 교체
# #D45087(분홍) → #E07B29(앰버오렌지), #7B61FF(청보라) → #2196F3(Material파랑)

# ── 아이소메트릭 투영 ─────────────────────────────────────────────
_COS30 = math.cos(math.radians(30))
_SIN30 = math.sin(math.radians(30))


def _iso(x: float, y: float, z: float,
         sx: float = 1.0, sy: float = 0.55, sz: float = 1.0):
    """월드 좌표(x,y,z) → 스크린 좌표(px,py)"""
    px = (x - y) * _COS30 * sx
    py = (x + y) * _SIN30 * sy + z * sz
    return px, py


def _box_faces(x, y, z0, z1, dx=0.6, dy=0.6, sy=0.55, sz=1.0):
    """막대 박스 3면의 꼭짓점 목록 반환 (top, front, side)"""
    kw = dict(sy=sy, sz=sz)
    top = [
        _iso(x,      y,      z1, **kw),
        _iso(x + dx, y,      z1, **kw),
        _iso(x + dx, y + dy, z1, **kw),
        _iso(x,      y + dy, z1, **kw),
    ]
    front = [
        _iso(x,      y + dy, z0, **kw),
        _iso(x,      y + dy, z1, **kw),
        _iso(x + dx, y + dy, z1, **kw),
        _iso(x + dx, y + dy, z0, **kw),
    ]
    side = [
        _iso(x + dx, y,      z0, **kw),
        _iso(x + dx, y,      z1, **kw),
        _iso(x + dx, y + dy, z1, **kw),
        _iso(x + dx, y + dy, z0, **kw),
    ]
    return top, front, side


def _face_colors(hex_color: str):
    """베이스 hex에서 3면 RGB 생성 (top=밝음, front=중간, side=어둠)"""
    h = hex_color.lstrip('#')
    r, g, b = int(h[:2], 16)/255, int(h[2:4], 16)/255, int(h[4:6], 16)/255
    hue, sat, val = colorsys.rgb_to_hsv(r, g, b)

    def _c(h2, s2, v2):
        r2, g2, b2 = colorsys.hsv_to_rgb(h2 % 1.0, min(s2, 1.0), min(v2, 1.0))
        return r2, g2, b2

    return (
        _c(hue, sat * 0.65, min(val * 1.18, 1.0)),  # top (밝음)
        _c(hue, sat,        val),                     # front (중간)
        _c(hue, sat * 1.05, val * 0.72),              # side (어둠)
    )


def _shuffled_palette(run_id: str, n: int) -> list[str]:
    import random
    seed = int(hashlib.md5(run_id.encode()).hexdigest()[:8], 16)
    pool = (_PALETTE * ((n // len(_PALETTE)) + 2))[:n * 2]
    random.Random(seed).shuffle(pool)
    return pool[:n]


# ── 아이소메트릭 막대 차트 ───────────────────────────────────────
def make_iso_bar_chart(
    labels: list, values: list,
    title: str, keyword: str, sector: str,
    out_path: str | Path,
    run_id: str = '',
) -> str:
    """아이소메트릭 3D 막대 차트 → PNG 저장 후 경로 반환."""
    try:
        setup_chart_defaults()  # ★ ERRORS [139][169][175] 3회 반복 박제 — 스타일 단일 진입점
        n = min(len(labels), len(values), 8)
        if n == 0:
            return ''
        labels, values = labels[:n], values[:n]

        rid = run_id or str(id(labels))
        colors = _shuffled_palette(rid, n)
        bg = _iso_bg(rid)

        vmax = max(abs(v) for v in values) or 1
        max_h = 4.5
        heights = [max(abs(v) / vmax * max_h, 0.08) for v in values]

        fig, ax = plt.subplots(figsize=(15, 9), dpi=160)
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        # ★ set_aspect('equal') 제거 — 아이소메트릭 X/Y 범위 불균형 시 대형 여백 생성
        ax.axis('off')

        bar_w, bar_d = 0.58, 0.58
        gap = 1.15
        sy_scale = 0.52

        # 뒤에서 앞 순서로 그려야 painter's algorithm 적용
        for i, (lbl, h, color) in enumerate(zip(labels, heights, colors)):
            x0 = i * gap
            y0 = 0.0
            top_c, front_c, side_c = _face_colors(color)
            top_v, front_v, side_v = _box_faces(
                x0, y0, 0, h, dx=bar_w, dy=bar_d, sy=sy_scale)

            # side → front → top 순 (뒤 면부터)
            for verts, fc in [(side_v, side_c), (front_v, front_c), (top_v, top_c)]:
                poly = Polygon(
                    verts, closed=True, facecolor=fc,
                    edgecolor='white', linewidth=1.2, zorder=3 + i * 0.05,
                )
                ax.add_patch(poly)

            # 값 라벨 — 단위 자동 변환 (≥10000→조, ≥1000→천단위쉼표, else→소수1자리)
            tx, ty = _iso(x0 + bar_w/2, y0 + bar_d/2, h + 0.22, sy=sy_scale)
            _v = values[i]
            if abs(_v) >= 10000:
                v_str = f'{_v/10000:.1f}조'
            elif abs(_v) >= 1000:
                v_str = f'{_v:,.0f}'
            elif abs(_v) >= 10:
                v_str = f'{_v:.1f}'
            else:
                v_str = f'{_v:.2f}'
            ax.text(tx, ty, v_str, ha='center', va='bottom',
                    fontsize=_CS["FONT_SMALL"], fontweight='bold', color='#1A2A4A',
                    fontfamily=_KR_FONT, zorder=12)

            # x축 레이블
            lx, ly = _iso(x0 + bar_w/2, y0 + bar_d/2, -0.45, sy=sy_scale)
            ax.text(lx, ly, lbl, ha='center', va='top',
                    fontsize=_CS["FONT_SMALL"], color='#4A5568', fontweight='bold',
                    fontfamily=_KR_FONT, zorder=12)

        # 바닥 그리드
        _draw_ground_grid(ax, n, gap, bar_w, bar_d, sy_scale)

        # 타이틀
        fig.text(0.5, 0.96, title, ha='center', fontsize=_CS["FONT_TITLE"],
                 fontweight='bold', color='#1A2A4A', fontfamily=_KR_FONT)
        fig.text(0.5, 0.91, f'{keyword}  ·  {sector}', ha='center',
                 fontsize=_CS["FONT_CAPTION"], color='#8898AA', fontfamily=_KR_FONT)
        fig.text(0.97, 0.96, datetime.now().strftime('%Y.%m'), ha='right',
                 fontsize=_CS["FONT_SMALL"], color='#8898AA', fontfamily=_KR_FONT)

        ax.autoscale_view()
        xl, yl = ax.get_xlim(), ax.get_ylim()
        # ★ 여백 최소화 — 차트가 figure 대비 너무 작아지는 문제 수정
        mx = (xl[1] - xl[0]) * 0.05
        my = (yl[1] - yl[0]) * 0.06
        ax.set_xlim(xl[0] - mx, xl[1] + mx)
        ax.set_ylim(yl[0] - my - 0.15, yl[1] + my + 0.3)

        plt.tight_layout(rect=[0.01, 0.02, 0.99, 0.87])
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), dpi=160, bbox_inches='tight',
                    facecolor=_BG, edgecolor='none')
        plt.close(fig)
        return str(out_path)

    except Exception as e:
        print(f'  ⚠️ [isometric] bar 생성 실패: {e}')
        _g_report('image', e, module=__name__)
        return ''


# ── 아이소메트릭 에어리어/라인 차트 ─────────────────────────────
def make_iso_area_chart(
    labels: list, values: list,
    title: str, keyword: str, sector: str,
    out_path: str | Path,
    run_id: str = '',
) -> str:
    """아이소메트릭 3D 에어리어 차트 (리본 스타일) → PNG 저장 후 경로 반환."""
    try:
        setup_chart_defaults()  # ★ ERRORS [139][169][175] 3회 반복 박제 — 스타일 단일 진입점
        n = min(len(labels), len(values), 13)
        if n < 3:
            return ''
        labels, values = labels[:n], values[:n]

        rid = run_id or str(id(labels))
        seed = int(hashlib.md5(rid.encode()).hexdigest()[:8], 16)
        import random
        color = _PALETTE[seed % len(_PALETTE)]
        top_c, front_c, _ = _face_colors(color)
        bg = _iso_bg(rid)

        vmax = max(abs(v) for v in values) or 1
        max_h = 3.8
        heights = [abs(v) / vmax * max_h for v in values]

        fig, ax = plt.subplots(figsize=(16, 9), dpi=160)
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        # ★ set_aspect('equal') 제거 — 아이소메트릭 X/Y 범위 불균형 시 대형 여백 생성
        ax.axis('off')

        gap   = 1.1
        depth = 0.45
        sy_s  = 0.50

        # 에어리어 면 (앞 리본)
        front_verts = []
        top_verts   = []
        for i, h in enumerate(heights):
            x = i * gap
            fx, fy = _iso(x, depth, h, sy=sy_s)
            bx, by = _iso(x, depth, 0, sy=sy_s)
            front_verts.append((fx, fy))
            # top ridge
            tx, ty = _iso(x, 0, h, sy=sy_s)
            top_verts.append((tx, ty))

        # 앞 리본 면
        ribbon_verts = []
        for i in range(n - 1):
            x0, x1 = i * gap, (i + 1) * gap
            h0, h1 = heights[i], heights[i+1]
            quad = [
                _iso(x0, depth, 0, sy=sy_s),
                _iso(x0, depth, h0, sy=sy_s),
                _iso(x1, depth, h1, sy=sy_s),
                _iso(x1, depth, 0, sy=sy_s),
            ]
            ribbon_verts.append(quad)
            col_blend = (
                (front_c[0] * (1 - i/n) + top_c[0] * i/n),
                (front_c[1] * (1 - i/n) + top_c[1] * i/n),
                (front_c[2] * (1 - i/n) + top_c[2] * i/n),
            )
            poly = Polygon(quad, closed=True,
                           facecolor=col_blend, alpha=0.85,
                           edgecolor='white', linewidth=0.8, zorder=4)
            ax.add_patch(poly)

        # 위 면 (top ridge)
        for i in range(n - 1):
            x0, x1 = i * gap, (i + 1) * gap
            h0, h1 = heights[i], heights[i+1]
            top_quad = [
                _iso(x0, 0, h0, sy=sy_s),
                _iso(x1, 0, h1, sy=sy_s),
                _iso(x1, depth, h1, sy=sy_s),
                _iso(x0, depth, h0, sy=sy_s),
            ]
            # gradient along height
            brightness = (h0 + h1) / 2 / max_h
            bc = (
                min(top_c[0] * (0.7 + 0.5 * brightness), 1.0),
                min(top_c[1] * (0.7 + 0.5 * brightness), 1.0),
                min(top_c[2] * (0.7 + 0.5 * brightness), 1.0),
            )
            poly = Polygon(top_quad, closed=True,
                           facecolor=bc, alpha=0.90,
                           edgecolor='white', linewidth=0.6, zorder=5)
            ax.add_patch(poly)

        # 라인 (크레스트)
        crest_x = [_iso(i * gap, depth, heights[i], sy=sy_s)[0] for i in range(n)]
        crest_y = [_iso(i * gap, depth, heights[i], sy=sy_s)[1] for i in range(n)]
        ax.plot(crest_x, crest_y, color='white', linewidth=2.5,
                zorder=8, solid_capstyle='round')

        # 점 마커
        for i in range(n):
            cx, cy = _iso(i * gap, depth, heights[i], sy=sy_s)
            ax.plot(cx, cy, 'o', color='white', markersize=8,
                    markeredgecolor=color, markeredgewidth=2.0, zorder=9)

        # X축 레이블 (간격 조절)
        step = max(1, n // 8)
        for i in range(0, n, step):
            lx, ly = _iso(i * gap, depth, -0.3, sy=sy_s)
            ax.text(lx, ly, labels[i], ha='center', va='top',
                    fontsize=_CS["FONT_SMALL"], color='#4A5568', fontweight='bold',
                    fontfamily=_KR_FONT, zorder=12)

        # Y축 눈금 (오른쪽 배경)
        for pct in [0.25, 0.5, 0.75, 1.0]:
            z_tick = pct * max_h
            v_tick = pct * vmax
            tx, ty = _iso(-0.4, depth, z_tick, sy=sy_s)
            v_str = (f'{int(v_tick):,}' if v_tick >= 100
                     else f'{v_tick:.1f}')
            ax.text(tx, ty, v_str, ha='right', va='center',
                    fontsize=_CS["FONT_SMALL"], color='#8898AA', fontfamily=_KR_FONT, zorder=12)
            # 수평 눈금선
            gx0, gy0 = _iso(0, depth, z_tick, sy=sy_s)
            gx1, gy1 = _iso((n-1)*gap, depth, z_tick, sy=sy_s)
            ax.plot([gx0, gx1], [gy0, gy1], '--',
                    color='#DDE3EE', lw=0.7, zorder=2)

        # 바닥
        _draw_ground_grid(ax, n, gap, 0, depth, sy_s, cols=1)

        # 타이틀
        fig.text(0.5, 0.96, title, ha='center', fontsize=_CS["FONT_TITLE"],
                 fontweight='bold', color='#1A2A4A', fontfamily=_KR_FONT)
        fig.text(0.5, 0.91, f'{keyword}  ·  {sector}', ha='center',
                 fontsize=_CS["FONT_CAPTION"], color='#8898AA', fontfamily=_KR_FONT)
        fig.text(0.97, 0.96, datetime.now().strftime('%Y.%m'), ha='right',
                 fontsize=_CS["FONT_SMALL"], color='#8898AA', fontfamily=_KR_FONT)

        ax.autoscale_view()
        xl, yl = ax.get_xlim(), ax.get_ylim()
        # ★ 여백 최소화 — 차트가 figure 대비 너무 작아지는 문제 수정
        # set_aspect('equal') 제거로 X/Y 독립 스케일 → 여백 비율만 조정
        mx = (xl[1] - xl[0]) * 0.04
        my = (yl[1] - yl[0]) * 0.06
        ax.set_xlim(xl[0] - mx - 0.3, xl[1] + mx)
        ax.set_ylim(yl[0] - my - 0.15, yl[1] + my + 0.3)

        # rect=[left, bottom, right, top] — 타이틀 공간 최소화해 차트 최대화
        plt.tight_layout(rect=[0.01, 0.02, 0.99, 0.87])
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), dpi=160, bbox_inches='tight',
                    facecolor=bg, edgecolor='none')
        plt.close(fig)
        return str(out_path)

    except Exception as e:
        print(f'  ⚠️ [isometric] area 생성 실패: {e}')
        _g_report('image', e, module=__name__)
        return ''


# ── 공통 헬퍼 ────────────────────────────────────────────────────
def _draw_ground_grid(ax, n, gap, bar_w, bar_d, sy_scale, cols=None):
    """바닥 그리드 선"""
    cols = cols if cols is not None else n
    rows = 3
    for xi in range(cols + 1):
        p0 = _iso(xi * gap, 0, 0, sy=sy_scale)
        p1 = _iso(xi * gap, bar_d * rows, 0, sy=sy_scale)
        ax.plot([p0[0], p1[0]], [p0[1], p1[1]],
                color='#DDE3EE', lw=0.5, zorder=1)
    for yi in range(rows + 1):
        p0 = _iso(0, yi * bar_d, 0, sy=sy_scale)
        p1 = _iso(cols * gap, yi * bar_d, 0, sy=sy_scale)
        ax.plot([p0[0], p1[0]], [p0[1], p1[1]],
                color='#DDE3EE', lw=0.5, zorder=1)


def make_band_line_chart(
    labels: list, values: list,
    title: str, keyword: str, sector: str,
    out_path: str | Path,
    run_id: str = '',
) -> str:
    """구간 밴드 + 라인 차트 (Style-5) — 시계열 데이터의 현재 위치 맥락 시각화."""
    try:
        setup_chart_defaults()
        n = min(len(labels), len(values), 13)
        if n < 2:
            return ''
        labels, values = labels[:n], values[:n]

        # ── 동적 색상 (run_id + keyword 기반) ──
        rid = run_id or str(id(labels))
        seed = int(hashlib.md5(f"{rid}|{keyword}".encode()).hexdigest()[:8], 16)
        line_color = _PALETTE[seed % len(_PALETTE)]
        dot_color  = _PALETTE[(seed + 5) % len(_PALETTE)]

        y_min_data = min(values)
        y_max_data = max(values)
        y_span = y_max_data - y_min_data

        # 기준금리/정책금리 → 역사적 맥락 고정 구간
        _rate_keywords = ('기준금리', '정책금리', '금리')
        if any(k in keyword for k in _rate_keywords):
            y_lo, y_hi = 0.0, 4.5
            band_defs = [
                (0.0, 1.0, '#66BB6A', '저금리 (0~1%)'),
                (1.0, 2.5, '#FFA726', '중금리 (1~2.5%)'),
                (2.5, 4.5, '#EF5350', '고금리 (2.5~4.5%)'),
            ]
        else:
            # 그 외 — y 범위 자동 계산 + 3등분
            if y_span < y_max_data * 0.05 or y_span < 0.01:
                y_lo = 0.0
                y_hi = y_max_data * 1.8 if y_max_data > 0 else 10.0
            else:
                y_lo = max(0.0, y_min_data - y_span * 0.3)
                y_hi = y_max_data + y_span * 0.3
            y_range = y_hi - y_lo
            b1 = y_lo + y_range * 0.33
            b2 = y_lo + y_range * 0.66
            band_defs = [
                (y_lo, b1,  '#66BB6A', f'하위 (~{b1:.1f})'),
                (b1,   b2,  '#FFA726', f'중위 ({b1:.1f}~{b2:.1f})'),
                (b2,   y_hi,'#EF5350', f'상위 ({b2:.1f}~)'),
            ]
        y_range = y_hi - y_lo

        fig, ax = plt.subplots(figsize=(16, 9))
        fig.patch.set_facecolor('#FAFAFA')
        ax.set_facecolor('#FAFAFA')

        # 구간 배경
        for lo, hi, col, lbl in band_defs:
            ax.axhspan(lo, hi, alpha=0.07, color=col, label=lbl)

        # 라인 + 점
        x = list(range(n))
        ax.plot(x, values, color=line_color, linewidth=4, zorder=3, solid_capstyle='round')
        ax.scatter(x, values, s=160, color=dot_color, zorder=4,
                   edgecolors='white', linewidths=2)

        # 라벨: 점 바로 아래 (오프셋 = y_range의 5%)
        label_offset = y_range * 0.05
        for i, (lbl, v) in enumerate(zip(labels, values)):
            v_str = f'{v:.1f}%' if abs(v) < 100 else f'{v:,.0f}'
            ax.text(i, v - label_offset,
                    f'{lbl}\n{v_str}',
                    ha='center', va='top',
                    fontsize=_CS["FONT_SMALL"], color=line_color,
                    fontweight='bold', fontfamily=_KR_FONT, zorder=5)

        ax.set_xlim(-0.5, n - 0.5)
        ax.set_ylim(y_lo, y_hi)
        # x축 눈금 숨김 — 포인트 라벨이 대신함
        ax.set_xticks([])
        ax.yaxis.set_tick_params(labelsize=_CS["FONT_SMALL"])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', alpha=0.2)

        # 범례
        ax.legend(fontsize=_CS["FONT_SMALL"], loc='upper right', framealpha=0.75)

        # 제목
        fig.text(0.06, 0.95, title, fontsize=_CS["FONT_TITLE"],
                 fontweight='bold', ha='left', fontfamily=_KR_FONT,
                 transform=fig.transFigure)
        fig.text(0.06, 0.90, f'{keyword} · {sector}',
                 fontsize=_CS["FONT_CAPTION"], color='#888888',
                 ha='left', fontfamily=_KR_FONT, transform=fig.transFigure)
        fig.text(0.96, 0.97,
                 datetime.now().strftime('%Y.%m'),
                 fontsize=_CS["FONT_SMALL"] - 2, color='#bbbbbb',
                 ha='right', transform=fig.transFigure)

        plt.tight_layout(rect=[0, 0, 1, 0.88])

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_path), dpi=160, bbox_inches='tight', facecolor='#FAFAFA')
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        _g_report('isometric_charts', e, module='make_band_line_chart')
        return ''


__all__ = ['make_iso_bar_chart', 'make_iso_area_chart', 'make_band_line_chart']
