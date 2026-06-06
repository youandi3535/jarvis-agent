"""JARVIS06_IMAGE/infographic_charts.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
4종 프리미엄 인포그래픽 — 사용자 요청 2026-05-26

1. make_hub_spoke      — 허브-스포크 방사형 (아이콘 중심 + 위성 노드)
2. make_hex_node_map   — 헥사 노드 마인드맵 (중심 + 6방향 컬러 노드)
3. make_dashboard_card — 대시보드 카드 목업 (레이어드 카드 + 미니차트)
4. make_premium_timeline — 프리미엄 타임라인 (배경 스트립 + 마일스톤 원)

모두 동적 색상 (run_id/theme 해시) + LLM 콘텐츠 + wrap_img 반환.
"""
from __future__ import annotations
import io, base64, os, math, hashlib, logging, time
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, FancyArrowPatch
from matplotlib.path import Path
import matplotlib.patheffects as pe

from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE
from JARVIS06_IMAGE.theme_charts import fig_to_b64, wrap_img, _FONT_PATH, W, DARK

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

log = logging.getLogger("jarvis")


# ── 공통 유틸 ──────────────────────────────────────────────────────

def _dyn_palette(theme: str, run_id: str, n: int = 6) -> list[str]:
    """테마 + run_id 기반 동적 HSV 팔레트 n색 생성."""
    import colorsys
    seed_str = f"{theme}|{run_id or time.time_ns()}"
    h16 = hashlib.md5(seed_str.encode()).hexdigest()
    base_h = int(h16[:4], 16) / 0xFFFF
    sat    = 0.62 + int(h16[4:6], 16) / 255 * 0.22
    val    = 0.74 + int(h16[6:8], 16) / 255 * 0.16
    cols = []
    for i in range(n):
        h = (base_h + i / n) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, sat, val)
        cols.append(f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}")
    return cols


def _dark_bg(theme: str, run_id: str) -> tuple[str, str]:
    """다크 배경색 + 포인트색 (실행마다 다른 조합)."""
    import colorsys
    h16 = hashlib.md5(f"{theme}|bg|{run_id or time.time_ns()}".encode()).hexdigest()
    base_h = int(h16[:4], 16) / 0xFFFF
    r, g, b = colorsys.hsv_to_rgb(base_h, 0.55, 0.30)
    bg = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
    r2, g2, b2 = colorsys.hsv_to_rgb(base_h, 0.38, 0.95)
    accent = f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    return bg, accent


def _llm_items(prompt: str, fallback: list, n: int) -> list[str]:
    """LLM에서 pipe-delimited 줄 추출. 실패 시 fallback."""
    try:
        from shared.llm import invoke_text
        raw = invoke_text("writer", prompt, timeout=60)
        lines = [l.strip() for l in (raw or "").strip().splitlines() if '|' in l][:n]
        while len(lines) < n:
            lines.append(fallback[len(lines) % len(fallback)])
        return lines
    except Exception:
        return (fallback * (n // len(fallback) + 1))[:n]


# ══════════════════════════════════════════════════════════════════
#  1. 허브-스포크 방사형 인포그래픽
#     중심 서버/아이콘 박스 + 8개 위성 노드 (다크 배경)
# ══════════════════════════════════════════════════════════════════

def make_hub_spoke(theme_name: str, run_id: str = '') -> str:
    """허브-스포크 방사형 다이어그램 (이미지 1 스타일)."""
    _fb = [
        "📊|데이터 수집|원시 데이터 확보",
        "🔍|분석 엔진|AI 기반 심층 분석",
        "📈|성과 측정|KPI 추적 및 평가",
        "🔒|보안 관리|데이터 암호화·접근제어",
        "☁️|클라우드|확장형 인프라 운영",
        "🔄|자동화|반복 작업 워크플로우",
        "📱|모바일|실시간 알림·접근",
        "🤖|AI 엔진|머신러닝 예측 모델",
    ]
    lines = _llm_items(
        f"{theme_name} 핵심 구성요소 8가지. 각각 '이모지|구성요소명|한줄설명' 형식, 8줄만.",
        _fb, 8
    )
    bg, accent = _dark_bg(theme_name, run_id)
    pal = _dyn_palette(theme_name, run_id, 8)

    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(14, 10), facecolor=bg)
    ax.set_facecolor(bg); ax.axis('off')
    ax.set_xlim(0, 14); ax.set_ylim(0, 10)

    cx, cy = 7.0, 5.0
    radius = 3.5
    angles = [math.pi / 2 + i * 2 * math.pi / 8 for i in range(8)]

    # ── 중심 허브 ────────────────────────────────────────────────
    # 글로우 효과 (반투명 원 겹치기)
    for r, a in [(1.05, 0.08), (0.85, 0.12), (0.65, 0.18)]:
        hub_glow = Circle((cx, cy), r, facecolor=accent, edgecolor='none', alpha=a, zorder=2)
        ax.add_patch(hub_glow)
    # 중심 본체
    hub = FancyBboxPatch((cx - 0.95, cy - 1.05), 1.90, 2.10,
                          boxstyle="round,pad=0.12",
                          facecolor=accent, edgecolor='white', linewidth=2, alpha=0.95, zorder=3)
    ax.add_patch(hub)
    # 서버 스택 레이어 (3단)
    import colorsys as _cs
    h16 = hashlib.md5(accent.encode()).hexdigest()
    _h, _s, _v = colorsys.rgb_to_hsv(
        int(accent[1:3], 16)/255, int(accent[3:5], 16)/255, int(accent[5:7], 16)/255)
    for j in range(3):
        yoff = j * 0.52
        r_, g_, b_ = _cs.hsv_to_rgb(_h, _s * 0.9, min(_v - 0.10 + j*0.06, 1.0))
        s_col = f"#{int(r_*255):02x}{int(g_*255):02x}{int(b_*255):02x}"
        rack = FancyBboxPatch((cx - 0.72, cy - 0.85 + yoff), 1.44, 0.43,
                               boxstyle="round,pad=0.04",
                               facecolor=s_col, edgecolor='white', linewidth=1.0, alpha=0.9, zorder=4)
        ax.add_patch(rack)
        ax.plot([cx - 0.55, cx + 0.55], [cy - 0.63 + yoff, cy - 0.63 + yoff],
                color='white', linewidth=0.5, alpha=0.6, zorder=5)
    ax.text(cx, cy + 1.25, theme_name[:8], fontsize=11, fontweight='black',
            ha='center', va='center', color='white', zorder=5)

    # ── 위성 노드 + 연결선 ────────────────────────────────────────
    for i, (angle, line) in enumerate(zip(angles, lines)):
        parts = line.split('|')
        icon = parts[0].strip() if len(parts) > 0 else '📌'
        kw   = parts[1].strip() if len(parts) > 1 else f'요소{i+1}'
        desc = parts[2].strip() if len(parts) > 2 else ''
        col  = pal[i % len(pal)]

        nx = cx + radius * math.cos(angle)
        ny = cy + radius * math.sin(angle)

        # 연결선 (중심 → 위성)
        ax.plot([cx, nx], [cy, ny], color='white', linewidth=0.7, alpha=0.25, zorder=1)
        # 중간 연결 점
        mid_x = cx + (radius * 0.55) * math.cos(angle)
        mid_y = cy + (radius * 0.55) * math.sin(angle)
        ax.scatter(mid_x, mid_y, s=18, color='white', alpha=0.35, zorder=2)

        # 위성 원 (외부 링 + 내부)
        outer = Circle((nx, ny), 0.60, facecolor=col, edgecolor='white', linewidth=2.0,
                        alpha=0.18, zorder=3)
        inner = Circle((nx, ny), 0.42, facecolor=col, edgecolor='white', linewidth=1.5,
                        alpha=0.35, zorder=3)
        core  = Circle((nx, ny), 0.28, facecolor=col, edgecolor='white', linewidth=1.5,
                        alpha=0.90, zorder=4)
        ax.add_patch(outer); ax.add_patch(inner); ax.add_patch(core)

        # 아이콘
        ax.text(nx, ny, icon, fontsize=15, ha='center', va='center', zorder=5)

        # 레이블 (노드 바깥쪽)
        label_dist = radius + 0.80
        lx = cx + label_dist * math.cos(angle)
        ly = cy + label_dist * math.sin(angle)
        ha_val = 'left' if math.cos(angle) > 0.1 else ('right' if math.cos(angle) < -0.1 else 'center')
        ax.text(lx, ly + 0.16, kw, fontsize=10, fontweight='bold',
                ha=ha_val, va='center', color='white', zorder=5)
        if desc:
            ax.text(lx, ly - 0.18, desc[:18], fontsize=8.5,
                    ha=ha_val, va='center', color='#cdd9e8', zorder=5)

    ax.set_title(f'{theme_name} 핵심 구성 요소 분석', fontsize=18, fontweight='black',
                 color='white', pad=20)
    plt.tight_layout(pad=1.5)
    return wrap_img(fig_to_b64(fig), f'{theme_name} 허브-스포크 다이어그램', '')


# ══════════════════════════════════════════════════════════════════
#  2. 헥사 노드 마인드맵 (이미지 3 스타일)
#     중심 원 + 6방향 컬러 노드 (아이콘 + 제목 + 설명)
# ══════════════════════════════════════════════════════════════════

def make_hex_node_map(theme_name: str, run_id: str = '') -> str:
    """헥사 노드 마인드맵 — 6방향 컬러 노드 (이미지 3 스타일)."""
    _fb = [
        "🏭|제조 데이터|생산 품질 관리",
        "💼|비즈니스 데이터|전략적 마케팅 분석",
        "💰|재무 데이터|위험 관리 및 예측",
        "🏥|의료 데이터|건강 예측 및 치료",
        "🏛|공공 데이터|정책 개선 및 행정",
        "⚽|스포츠 데이터|성과 및 전략 분석",
    ]
    lines = _llm_items(
        f"{theme_name} 관련 데이터·활용 분야 6가지. 각각 '이모지|분야명|한줄설명' 형식, 6줄만.",
        _fb, 6
    )
    pal = _dyn_palette(theme_name, run_id, 6)

    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(13, 10), facecolor='#f8f9ff')
    ax.set_facecolor('#f8f9ff'); ax.axis('off')
    ax.set_xlim(0, 13); ax.set_ylim(0, 10)

    cx, cy = 6.5, 5.0
    radius = 3.2
    # 6방향 (60° 간격, 위쪽부터)
    angles = [math.pi / 2 + i * math.pi / 3 for i in range(6)]

    # ── 외곽 안내 원 (연한 링) ────────────────────────────────────
    guide = Circle((cx, cy), radius, facecolor='none',
                   edgecolor='#dde3f0', linewidth=1.2, linestyle='--', alpha=0.7)
    ax.add_patch(guide)

    # ── 중심 노드 ────────────────────────────────────────────────
    # 중심 큰 원
    ax.add_patch(Circle((cx, cy), 1.10, facecolor='#1e293b', edgecolor='none', zorder=3))
    ax.add_patch(Circle((cx, cy), 0.92, facecolor='#334155', edgecolor='none', zorder=4))
    ax.text(cx, cy + 0.22, '🎓', fontsize=24, ha='center', va='center', zorder=5)
    ax.text(cx, cy - 0.45, theme_name[:6], fontsize=10, fontweight='black',
            ha='center', va='center', color='white', zorder=5)

    # ── 6개 노드 ─────────────────────────────────────────────────
    for i, (angle, line) in enumerate(zip(angles, lines)):
        parts = line.split('|')
        icon = parts[0].strip() if len(parts) > 0 else '📌'
        title = parts[1].strip() if len(parts) > 1 else f'노드{i+1}'
        desc  = parts[2].strip() if len(parts) > 2 else ''
        col   = pal[i]

        nx = cx + radius * math.cos(angle)
        ny = cy + radius * math.sin(angle)

        # 연결선 (안내 원 내부까지만)
        ax.plot([cx + 1.10 * math.cos(angle), nx - 0.95 * math.cos(angle)],
                [cy + 1.10 * math.sin(angle), ny - 0.95 * math.sin(angle)],
                color=col, linewidth=1.8, alpha=0.45, zorder=1)

        # ── 물방울/잎사귀 모양 노드 ──────────────────────────────
        # 외부 연한 원 (배경)
        bg_circle = Circle((nx, ny), 1.05,
                            facecolor=col, edgecolor='none', alpha=0.12, zorder=2)
        ax.add_patch(bg_circle)
        # 내부 원
        node_circle = Circle((nx, ny), 0.78,
                              facecolor=col, edgecolor='white', linewidth=2.5,
                              alpha=0.92, zorder=3)
        ax.add_patch(node_circle)
        # 아이콘
        ax.text(nx, ny + 0.16, icon, fontsize=20, ha='center', va='center', zorder=4)
        # 제목 (원 안)
        ax.text(nx, ny - 0.38, title[:6], fontsize=9.5, fontweight='black',
                ha='center', va='center', color='white', zorder=4)

        # 설명 (원 바깥)
        label_dist = radius + 1.15
        lx = cx + label_dist * math.cos(angle)
        ly = cy + label_dist * math.sin(angle)
        ha_val = 'left' if math.cos(angle) > 0.15 else ('right' if math.cos(angle) < -0.15 else 'center')
        if desc:
            ax.text(lx, ly, desc[:16], fontsize=9,
                    ha=ha_val, va='center', color='#475569',
                    bbox=dict(boxstyle='round,pad=0.35', facecolor=col + '22',
                              edgecolor=col, linewidth=1.2))

    ax.set_title(f'의사결정에서 {theme_name}의 역할', fontsize=18, fontweight='black',
                 color=DARK, pad=18)
    plt.tight_layout(pad=1.5)
    return wrap_img(fig_to_b64(fig), f'{theme_name} 헥사 노드 다이어그램', '')


# ══════════════════════════════════════════════════════════════════
#  3. 대시보드 카드 목업 (이미지 2 스타일)
#     레이어드 카드 + 미니 파이·바·라인차트
# ══════════════════════════════════════════════════════════════════

def make_dashboard_card(theme_name: str, run_id: str = '') -> str:
    """대시보드 카드 목업 — 레이어드 카드 3장 + 미니차트 (이미지 2 스타일)."""
    import numpy as _np

    pal = _dyn_palette(theme_name, run_id, 6)
    seed = int(hashlib.md5(f"{theme_name}|dash|{run_id or time.time_ns()}".encode()).hexdigest()[:8], 16)
    rng  = _np.random.default_rng(seed)

    # LLM으로 지표명 3개 생성
    _fb_labels = [f'{theme_name} 지표', '시장 점유율', '성장률']
    try:
        from shared.llm import invoke_text
        raw = invoke_text("writer_fast",
                          f"{theme_name} 투자 핵심 지표 3가지를 한국어로 짧게 (각 6자 이내). 줄바꿈으로 구분, 3줄만.",
                          max_tokens=80, temperature=0.5)
        metric_names = [l.strip() for l in (raw or "").strip().splitlines() if l.strip()][:3]
        while len(metric_names) < 3:
            metric_names.append(_fb_labels[len(metric_names)])
    except Exception:
        metric_names = _fb_labels

    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(14, 9), facecolor='#f0f2f8')
    ax.set_facecolor('#f0f2f8'); ax.axis('off')
    ax.set_xlim(0, 14); ax.set_ylim(0, 9)

    def _shadow_card(ax, x, y, w, h, col, alpha=0.9, zorder=2):
        """그림자 + 카드."""
        shadow = FancyBboxPatch((x + 0.15, y - 0.15), w, h,
                                 boxstyle="round,pad=0.15",
                                 facecolor='#00000020', edgecolor='none', zorder=zorder)
        card = FancyBboxPatch((x, y), w, h,
                               boxstyle="round,pad=0.15",
                               facecolor=W, edgecolor=col, linewidth=2.5,
                               alpha=alpha, zorder=zorder + 1)
        ax.add_patch(shadow); ax.add_patch(card)

    def _mini_bar(ax, x, y, w, h, values, colors, title):
        """미니 바 차트."""
        n = len(values)
        bw = (w - 0.3) / n
        max_v = max(values) or 1
        for i, (v, c) in enumerate(zip(values, colors)):
            bh = (v / max_v) * (h - 0.55)
            bar = mpatches.Rectangle((x + 0.15 + i * bw, y + 0.1), bw * 0.75, bh,
                                      facecolor=c, edgecolor='none', alpha=0.85, zorder=5)
            ax.add_patch(bar)
        ax.text(x + w/2, y + h - 0.12, title, fontsize=8.5, fontweight='bold',
                ha='center', va='top', color='#333', zorder=6)

    def _mini_pie(ax, cx_, cy_, r, values, colors, title):
        """미니 파이 차트."""
        total = sum(values) or 1
        start = math.pi / 2
        for v, c in zip(values, colors):
            angle = 2 * math.pi * v / total
            theta = _np.linspace(start, start + angle, 30)
            xs = [cx_] + list(cx_ + r * _np.cos(theta)) + [cx_]
            ys = [cy_] + list(cy_ + r * _np.sin(theta)) + [cy_]
            ax.fill(xs, ys, facecolor=c, edgecolor=W, linewidth=1.2, alpha=0.88, zorder=5)
            start += angle
        ax.text(cx_, cy_ - r - 0.22, title, fontsize=8.5, fontweight='bold',
                ha='center', va='top', color='#333', zorder=6)

    def _mini_line(ax, x, y, w, h, values1, values2, c1, c2):
        """미니 라인 차트 (2개 시리즈)."""
        n = len(values1)
        xs = _np.linspace(x + 0.15, x + w - 0.15, n)
        max_v = max(max(values1), max(values2)) or 1
        ys1 = [y + 0.1 + (v / max_v) * (h - 0.25) for v in values1]
        ys2 = [y + 0.1 + (v / max_v) * (h - 0.25) for v in values2]
        ax.fill_between(xs, [y+0.1]*n, ys1, alpha=0.18, color=c1, zorder=4)
        ax.fill_between(xs, [y+0.1]*n, ys2, alpha=0.12, color=c2, zorder=4)
        ax.plot(xs, ys1, color=c1, linewidth=1.8, zorder=5)
        ax.plot(xs, ys2, color=c2, linewidth=1.5, linestyle='--', zorder=5)

    # ── 카드 배치 ─────────────────────────────────────────────────
    # 카드 1 (뒤 — 우상단)
    _shadow_card(ax, 5.8, 1.5, 7.8, 6.8, pal[0], zorder=2)
    # 카드 1 헤더
    hdr = mpatches.Rectangle((5.8, 7.8), 7.8, 0.55,
                               facecolor=pal[0], edgecolor='none', zorder=4)
    ax.add_patch(FancyBboxPatch((5.8, 7.65), 7.8, 0.65, boxstyle="round,pad=0.1",
                                  facecolor=pal[0], edgecolor='none', zorder=4))
    ax.text(6.25, 7.98, f'📊 {theme_name} 분석', fontsize=11, fontweight='bold',
            color=W, va='center', zorder=5)
    # 파이 차트 영역
    pie_vals = rng.dirichlet([1, 1, 1, 1]) * 100
    _mini_pie(ax, 9.8, 5.8, 1.0, pie_vals, pal[:4], metric_names[1] if len(metric_names) > 1 else '구성비')
    # 라인 차트 영역
    lv1 = rng.uniform(30, 90, 12).tolist()
    lv2 = rng.uniform(20, 70, 12).tolist()
    _mini_line(ax, 6.2, 4.0, 4.5, 2.8, lv1, lv2, pal[0], pal[2])
    ax.text(8.45, 6.92, metric_names[0], fontsize=9, fontweight='bold',
            ha='center', color='#333', zorder=5)

    # 카드 2 (중간 왼쪽 — 주황/파랑)
    _shadow_card(ax, 0.5, 2.2, 5.5, 4.5, pal[3], zorder=6)
    ax.add_patch(FancyBboxPatch((0.5, 6.2), 5.5, 0.55, boxstyle="round,pad=0.08",
                                 facecolor=pal[3], edgecolor='none', zorder=8))
    ax.text(0.9, 6.47, f'📉 {metric_names[2] if len(metric_names) > 2 else "추이 분석"}', fontsize=10,
            fontweight='bold', color=W, va='center', zorder=9)
    bv = rng.uniform(20, 100, 5).tolist()
    _mini_bar(ax, 0.7, 2.5, 5.0, 3.5, bv, (pal * 2)[:5], metric_names[0])

    # 카드 3 (중간 오른쪽 — 보라)
    _shadow_card(ax, 6.2, 2.0, 5.2, 4.2, pal[4], zorder=6)
    ax.add_patch(FancyBboxPatch((6.2, 5.7), 5.2, 0.55, boxstyle="round,pad=0.08",
                                 facecolor=pal[4], edgecolor='none', zorder=8))
    ax.text(6.55, 5.97, f'📊 {metric_names[1] if len(metric_names) > 1 else "시장 현황"}', fontsize=10,
            fontweight='bold', color=W, va='center', zorder=9)
    # 스택 바
    n_stack = 4
    stack_vals = [rng.uniform(10, 80, n_stack).tolist() for _ in range(3)]
    bw_s = 1.0
    for k, (sv, sc) in enumerate(zip(stack_vals, pal[:3])):
        bottom = 2.2
        for j, v in enumerate(sv):
            bh_s = (v / 100) * 2.5
            bar_s = mpatches.Rectangle((6.5 + j * 1.15, bottom), bw_s, bh_s,
                                        facecolor=sc, edgecolor=W, linewidth=0.8,
                                        alpha=0.80, zorder=7)
            ax.add_patch(bar_s)
            bottom += bh_s

    ax.set_title(f'{theme_name} 투자 데이터 대시보드', fontsize=18, fontweight='black',
                 color=DARK, pad=16)
    plt.tight_layout(pad=1.2)
    return wrap_img(fig_to_b64(fig), f'{theme_name} 대시보드 목업', '')


# ══════════════════════════════════════════════════════════════════
#  4. 프리미엄 타임라인 (이미지 4 스타일)
#     배경 스트립 + 컬러 원 마일스톤 + 교대 텍스트
# ══════════════════════════════════════════════════════════════════

def make_premium_timeline(theme_name: str, run_id: str = '') -> str:
    """프리미엄 타임라인 인포그래픽 (이미지 4 스타일)."""
    _fb = [
        "2018|기초 기술 개발|연구 시작 단계",
        "2019|상용화 첫 시도|파일럿 프로젝트",
        "2020|시장 진입|초기 투자 유치",
        "2021|성장 가속|주요 기업 참여",
        "2022|대중화 시작|일반 소비자 확산",
        "2023|글로벌 확장|해외 시장 진출",
    ]
    lines = _llm_items(
        f"{theme_name} 테마의 발전 역사 6단계. 각각 '연도|단계명|한줄설명' 형식, 6줄만.",
        _fb, 6
    )
    pal = _dyn_palette(theme_name, run_id, 6)
    bg, accent = _dark_bg(theme_name, run_id)

    # 배경을 밝은 파란-회색 계열로
    import colorsys as _cs2
    h16 = hashlib.md5(f"{theme_name}|tl|{run_id or time.time_ns()}".encode()).hexdigest()
    _bh = int(h16[:4], 16) / 0xFFFF
    rb, gb, bb = _cs2.hsv_to_rgb(_bh, 0.18, 0.90)
    tl_bg = f"#{int(rb*255):02x}{int(gb*255):02x}{int(bb*255):02x}"

    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(15, 7.5), facecolor=tl_bg)
    ax.set_facecolor(tl_bg); ax.axis('off')
    ax.set_xlim(0, 15); ax.set_ylim(0, 7.5)

    n = len(lines)
    x_margin = 1.5
    x_end    = 13.5
    y_mid    = 3.75
    xs       = [x_margin + i * (x_end - x_margin) / (n - 1) for i in range(n)]

    # ── 배경 스트립 ───────────────────────────────────────────────
    strip = FancyBboxPatch((0.8, y_mid - 0.30), 13.4, 0.60,
                            boxstyle="round,pad=0.25",
                            facecolor='#dce8f0', edgecolor='none', alpha=0.85, zorder=1)
    ax.add_patch(strip)

    # 스트립 그라디언트 효과 (연한 선들)
    for xi in range(22):
        ax.plot([0.8 + xi * 0.65, 0.8 + xi * 0.65 + 0.35],
                [y_mid - 0.24, y_mid + 0.24],
                color='white', linewidth=3, alpha=0.15, zorder=1)

    # 메인 라인
    ax.plot([x_margin, x_end], [y_mid, y_mid],
            color='#93b4cc', linewidth=2.5, solid_capstyle='round', zorder=2)

    # ── 마일스톤 ────────────────────────────────────────────────
    EMOJIS = ['🚀', '⚡', '🔬', '📈', '🌐', '🏆']
    for i, (xn, line) in enumerate(zip(xs, lines)):
        parts = line.split('|')
        year  = parts[0].strip() if len(parts) > 0 else f'202{i}'
        name  = parts[1].strip() if len(parts) > 1 else f'단계{i+1}'
        desc  = parts[2].strip() if len(parts) > 2 else ''
        col   = pal[i % len(pal)]
        emoji = EMOJIS[i % len(EMOJIS)]

        # 수직 점선 (교대 위/아래)
        is_up = (i % 2 == 0)
        stem_y = y_mid + (1.5 if is_up else -1.5)
        ax.plot([xn, xn], [y_mid, stem_y], color=col,
                linewidth=1.4, linestyle=':', alpha=0.7, zorder=2)

        # 마일스톤 원 (외부 링 + 내부)
        ring  = Circle((xn, y_mid), 0.42, facecolor=tl_bg, edgecolor=col,
                        linewidth=3.0, zorder=4)
        inner = Circle((xn, y_mid), 0.30, facecolor=col, edgecolor='none',
                        alpha=0.92, zorder=5)
        ax.add_patch(ring); ax.add_patch(inner)
        ax.text(xn, y_mid, year[-2:], fontsize=9.5, fontweight='black',
                ha='center', va='center', color='white', zorder=6)

        # 이모지 + 제목 박스
        text_y = y_mid + (2.0 if is_up else -2.0)
        box_col = col + '22'
        ax.text(xn, text_y + (0.32 if is_up else -0.32), emoji,
                fontsize=16, ha='center', va='center', zorder=6)
        ax.text(xn, text_y + (-0.24 if is_up else 0.24), name[:8],
                fontsize=9.5, fontweight='bold', ha='center', va='center',
                color=col, zorder=6,
                bbox=dict(boxstyle='round,pad=0.3', facecolor=box_col,
                          edgecolor=col, linewidth=1.2))
        if desc:
            ax.text(xn, text_y + (-0.72 if is_up else 0.72), desc[:14],
                    fontsize=8.5, ha='center', va='center', color='#555', zorder=6)

    # ── 끝 장식 (인물 이모지) ─────────────────────────────────────
    ax.text(0.3, y_mid - 0.65, '🚶', fontsize=26, ha='center', va='center', alpha=0.6)
    ax.text(14.6, y_mid + 0.5, '🏅', fontsize=26, ha='center', va='center', alpha=0.7)

    # 연도 전체 범위 표시
    years = [l.split('|')[0].strip() for l in lines]
    if len(years) >= 2:
        ax.text(x_margin, y_mid - 0.62, years[0], fontsize=9, fontweight='bold',
                ha='center', color='#777')
        ax.text(x_end, y_mid - 0.62, years[-1], fontsize=9, fontweight='bold',
                ha='center', color='#777')

    ax.set_title(f'{theme_name} 발전 타임라인', fontsize=19, fontweight='black',
                 color=DARK, pad=18)
    plt.tight_layout(pad=1.5)
    return wrap_img(fig_to_b64(fig), f'{theme_name} 프리미엄 타임라인', '')


__all__ = [
    'make_hub_spoke',
    'make_hex_node_map',
    'make_dashboard_card',
    'make_premium_timeline',
]
