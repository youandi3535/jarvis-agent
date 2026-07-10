"""JARVIS06_IMAGE/theme_charts.py — 테마주 차트·인포그래픽 생성 (collect_theme에서 이관)."""
from __future__ import annotations
# ★ yfinance 단일 진입점 → JARVIS09 (2026-05-31 이관)
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_ticker_history as _j09_hist,
    download_ticker as _j09_dl,
)
import io, base64, os, logging
import numpy as np
import matplotlib
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (3D 차트용)

# ★ 차트 스타일 단일 진입점 (style_engine.py)
from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE

log = logging.getLogger("jarvis")

try:
    from JARVIS02_WRITER import length_manager as _LM
except ImportError:
    try:
        import length_manager as _LM
    except ImportError:
        class _LM:  # type: ignore
            STOCK_COUNT_PER_POST = 5
            TERM_FORMULA_MAX = 20
            TERM_NAME_MAX = 10
            TERM_CRITERIA_MAX = 20
            LINE_BREAK_THRESHOLD = 18

CHART_STORE: dict = {}

_FONT_PATH = None
for _fp in [
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/System/Library/Fonts/AppleSDGothicNeo.ttc',
]:
    if os.path.exists(_fp):
        _FONT_PATH = _fp
        break

W = 'white'
PURPLE = '#667eea'
DARK   = '#1a1a2e'

_CAP_DESC = {
    'overview':     '전체 투자 포인트 요약 인포그래픽',
    'radar':        '5개 지표 레이더 차트',
    'factors':      '상승·하락 요인 분석',
    'timeline':     '투자 단계별 체크리스트',
    'mechanism':    '테마 작동 구조 도식',
    'usecase':      '주요 활용 분야',
    'history':      '발전 역사 타임라인',
    'keyword':      '핵심 키워드 모음',
    'terms':        '핵심 투자 용어 3가지',
    'profit_loss':  '흑자/적자 종목 현황',
    'mktcap':       '시가총액 비교',
    'per':          'PER 밸류에이션 비교',
    'profitability': '수익성 지표 비교',
    'revenue':      '매출·순이익 비교',
    'return3m':     '3개월 수익률 비교',
    'risk':         '종목별 투자 위험도',
    'portfolio':    '포트폴리오 전략',
    'principle':    '투자 원칙',
}


def _cap(key: str, t: str = '', **kw) -> str:
    """차트 캡션 LLM 동적 생성 — 매번 다른 표현."""
    try:
        from shared.llm import invoke_text as _llm
        desc = _CAP_DESC.get(key, key)
        if key == 'profit_loss' and kw:
            desc = f"흑자 {kw.get('p','?')}개/적자 {kw.get('l','?')}개 종목 현황"
        theme_ctx = f"'{t}' 테마 " if t else ""
        data_ctx = ', '.join(f'{k}={v}' for k, v in kw.items()) if kw and key != 'profit_loss' else ''
        extra = f" 데이터: {data_ctx}." if data_ctx else ""
        return _llm(
            "writer_fast",
            f"{theme_ctx}블로그 차트 캡션 1문장. 차트: {desc}.{extra} 25자 이내. 해요체. 문장만 출력.",
            max_tokens=40, temperature=0.8
        ) or f"{theme_ctx}{_CAP_DESC.get(key, key)}"
    except Exception:
        return f"{t} {_CAP_DESC.get(key, key)}"


def set_font() -> None:
    """★ Deprecated — setup_chart_defaults() 사용 권장."""
    setup_chart_defaults(_FONT_PATH)


def fig_to_b64(fig) -> str:
    setup_chart_defaults(_FONT_PATH)
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=CHART_STYLE["DPI"], bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


def wrap_img(b64: str, alt: str, caption: str = '') -> str:
    cap = (f'<p style="text-align:center;color:#888;font-size:12px;'
           f'margin:6px 0 2px;font-style:italic;">{caption}</p>') if caption else ''
    return (
        f'<div style="background:white;border-radius:16px;padding:18px 18px 10px;'
        f'margin:22px 0;box-shadow:0 3px 20px rgba(102,126,234,0.1);'
        f'border:1px solid #e8ecf0;">'
        f'<img src="data:image/png;base64,{b64}" '
        f'style="width:100%;max-width:760px;display:block;margin:0 auto;border-radius:8px;" '
        f'alt="{alt}"/>{cap}</div>'
    )


def make_theme_overview_chart(theme_name: str) -> str:
    """★ 동적 스타일 생성 — LLM이 매번 새로운 레이아웃·색상 창작."""
    from JARVIS06_IMAGE.style_engine import generate_style_spec, apply_style_to_chart, get_color_from_spec, hex_to_rgb

    setup_chart_defaults(_FONT_PATH)

    # 1단계: LLM이 스타일 창작
    style_spec = generate_style_spec("overview", theme_name)
    layout = style_spec.get("layout", "cards_4")
    primary_color = get_color_from_spec(style_spec, "primary_color")
    accent_color = get_color_from_spec(style_spec, "accent_color")
    shape_style = style_spec.get("shape_style", "rounded")

    labels = ['테마 개요', '투자 포인트', '시장 현황', '전망']
    icons  = ['📌', '💡', '📊', '🔮']
    descs  = [
        f'{theme_name} 테마는\n한국 주식시장에서\n주목받는 투자 테마입니다',
        '관련 기업들의\n실적과 성장성을\n종합 분석합니다',
        f'시가총액 상위\n{_LM.STOCK_COUNT_PER_POST}개 종목을\n심층 분석합니다',
        '테마 흐름과\n투자 전략을\n제시합니다',
    ]

    # 2단계: 레이아웃별 렌더링
    if layout in ['cards_4', 'grid_2x2', 'tiles_3', 'list_vertical', 'stacked']:
        fig = _render_overview_layout(layout, theme_name, labels, icons, descs,
                                      primary_color, accent_color, shape_style)
    else:
        # 폴백: 기본 4카드 레이아웃
        fig = _render_overview_layout('cards_4', theme_name, labels, icons, descs,
                                      primary_color, accent_color, shape_style)

    ax = fig.gca()
    apply_style_to_chart(fig, ax, style_spec)

    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 테마 개요', _cap('overview', t=theme_name))


def _render_overview_layout(layout: str, theme_name: str, labels: list, icons: list, descs: list,
                            primary: str, accent: str, shape: str):
    """overview 차트의 다양한 레이아웃 렌더링."""
    import matplotlib.patches as mpatches

    if layout == 'cards_4':
        # 가로 4개 카드
        fig, ax = plt.subplots(figsize=(13, 4.2), facecolor=W)
        ax.set_facecolor(W); ax.axis('off')

        # 색상 배열 (primary → accent로 그라디언트)
        colors = [primary, accent, _interpolate_color(primary, accent, 0.5), accent]

        for i, (col, lbl, icon, desc) in enumerate(zip(colors, labels, icons, descs)):
            x0 = i * 3.0 + 0.2
            boxstyle = f"round,pad=0.15" if shape == 'rounded' else "square,pad=0.1"
            rect = FancyBboxPatch((x0, 0.25), 2.55, 3.55, boxstyle=boxstyle,
                                   facecolor='#f8f9ff', edgecolor=col, linewidth=2.5)
            ax.add_patch(rect)
            rect2 = FancyBboxPatch((x0 + 0.1, 3.0), 2.35, 0.65, boxstyle=boxstyle,
                                    facecolor=col, edgecolor='none')
            ax.add_patch(rect2)
            ax.text(x0 + 1.28, 3.33, f'{icon} {lbl}', fontsize=11, fontweight='black',
                    ha='center', va='center', color=W)
            ax.text(x0 + 1.28, 1.75, desc, fontsize=9.5, ha='center', va='center',
                    color='#333', linespacing=1.7)

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.0)
        ax.set_title(f'{theme_name} 테마 완전 분석 가이드', fontsize=17,
                     fontweight='black', color=DARK, pad=16)
        return fig

    elif layout == 'grid_2x2':
        # 2x2 그리드 (4개 항목)
        fig, axes = plt.subplots(2, 2, figsize=(10, 8), facecolor=W)
        axes = axes.flatten()

        colors = [primary, accent, _interpolate_color(primary, accent, 0.5), accent]

        for idx, (ax, col, lbl, icon, desc) in enumerate(zip(axes, colors, labels, icons, descs)):
            ax.set_facecolor('white')
            ax.axis('off')

            # 각 셀에 카드 그리기
            boxstyle = f"round,pad=0.15" if shape == 'rounded' else "square,pad=0.1"
            rect = FancyBboxPatch((0.05, 0.1), 0.9, 0.85, boxstyle=boxstyle,
                                   facecolor='#f8f9ff', edgecolor=col, linewidth=2,
                                   transform=ax.transAxes)
            ax.add_patch(rect)

            ax.text(0.5, 0.8, f'{icon} {lbl}', fontsize=12, fontweight='bold',
                    ha='center', va='center', color=col, transform=ax.transAxes)
            ax.text(0.5, 0.45, desc, fontsize=9, ha='center', va='center',
                    color='#333', linespacing=1.6, transform=ax.transAxes)

        fig.suptitle(f'{theme_name} 테마 완전 분석 가이드', fontsize=16,
                     fontweight='black', color=DARK, y=0.98)
        return fig

    elif layout == 'list_vertical':
        # 세로 리스트 (위→아래)
        fig, ax = plt.subplots(figsize=(10, 8), facecolor=W)
        ax.set_facecolor(W); ax.axis('off')

        colors = [primary, accent, _interpolate_color(primary, accent, 0.5), accent]
        y_pos = 3.5

        for col, lbl, icon, desc in zip(colors, labels, icons, descs):
            boxstyle = f"round,pad=0.1" if shape == 'rounded' else "square,pad=0.05"
            rect = FancyBboxPatch((0.2, y_pos - 0.6), 9.6, 0.8, boxstyle=boxstyle,
                                   facecolor=col, alpha=0.1, edgecolor=col, linewidth=2)
            ax.add_patch(rect)

            ax.text(0.5, y_pos - 0.2, icon, fontsize=16, ha='left', va='center')
            ax.text(1.2, y_pos - 0.2, f'{lbl}', fontsize=12, fontweight='bold',
                    ha='left', va='center', color=col)
            ax.text(0.5, y_pos - 0.5, desc[:50], fontsize=9, ha='left', va='top',
                    color='#333')

            y_pos -= 1.1

        ax.set_xlim(0, 10); ax.set_ylim(-0.5, 4.5)
        ax.set_title(f'{theme_name} 테마 완전 분석 가이드', fontsize=17,
                     fontweight='black', color=DARK, pad=16)
        return fig

    else:  # stacked, tiles_3 등
        # 기본값: cards_4
        fig, ax = plt.subplots(figsize=(13, 4.2), facecolor=W)
        ax.set_facecolor(W); ax.axis('off')

        colors = [primary, accent, _interpolate_color(primary, accent, 0.5), accent]

        for i, (col, lbl, icon, desc) in enumerate(zip(colors, labels, icons, descs)):
            x0 = i * 3.0 + 0.2
            boxstyle = f"round,pad=0.15" if shape == 'rounded' else "square,pad=0.1"
            rect = FancyBboxPatch((x0, 0.25), 2.55, 3.55, boxstyle=boxstyle,
                                   facecolor='#f8f9ff', edgecolor=col, linewidth=2.5)
            ax.add_patch(rect)
            rect2 = FancyBboxPatch((x0 + 0.1, 3.0), 2.35, 0.65, boxstyle=boxstyle,
                                    facecolor=col, edgecolor='none')
            ax.add_patch(rect2)
            ax.text(x0 + 1.28, 3.33, f'{icon} {lbl}', fontsize=11, fontweight='black',
                    ha='center', va='center', color=W)
            ax.text(x0 + 1.28, 1.75, desc, fontsize=9.5, ha='center', va='center',
                    color='#333', linespacing=1.7)

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.0)
        ax.set_title(f'{theme_name} 테마 완전 분석 가이드', fontsize=17,
                     fontweight='black', color=DARK, pad=16)
        return fig


def _interpolate_color(hex1: str, hex2: str, ratio: float) -> str:
    """두 hex 색상 사이를 보간 (ratio=0.5면 중간색)."""
    def hex_to_rgb(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def rgb_to_hex(r, g, b):
        return f'#{int(r):02x}{int(g):02x}{int(b):02x}'

    r1, g1, b1 = hex_to_rgb(hex1)
    r2, g2, b2 = hex_to_rgb(hex2)

    r = int(r1 + (r2 - r1) * ratio)
    g = int(g1 + (g2 - g1) * ratio)
    b = int(b1 + (b2 - b1) * ratio)

    return rgb_to_hex(r, g, b)


def make_investment_radar_chart(theme_name: str, names: list, caps: list) -> str:
    """★ 동적 스타일 생성 — 레이더/거미줄/다각형 등 매번 다른 형태."""
    from JARVIS06_IMAGE.style_engine import generate_style_spec, apply_style_to_chart, get_color_from_spec

    setup_chart_defaults(_FONT_PATH)

    # 1단계: LLM이 스타일 창작
    style_spec = generate_style_spec("radar", theme_name)
    layout = style_spec.get("layout", "polar")
    primary_color = get_color_from_spec(style_spec, "primary_color")
    accent_color = get_color_from_spec(style_spec, "accent_color")

    categories = ['시총 규모', '성장성', '수익성', '안정성', '모멘텀']
    N = len(categories)
    angles = [n / float(N) * 2 * np.pi for n in range(N)]
    angles += angles[:1]
    values = [7, 8, 6, 7, 9]
    values += values[:1]

    # 2단계: 레이아웃별 렌더링
    if layout == 'polar':
        # 극좌표 (기존 방식)
        fig, ax = plt.subplots(figsize=(8, 6), facecolor=W, subplot_kw=dict(polar=True))
        ax.set_facecolor('#fafbff')
        ax.plot(angles, values, 'o-', linewidth=2.5, color=primary_color)
        ax.fill(angles, values, alpha=0.2, color=primary_color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=11, fontweight='bold', color='#333')
        ax.set_ylim(0, 10)
        ax.grid(color='#ddd', linewidth=0.8)
        ax.set_title(f'{theme_name} 테마 투자 매력도', fontsize=15,
                     fontweight='black', color=DARK, pad=20)

    elif layout == 'spider':
        # 거미줄 변형 (선 더 굵게, 다각형 더 뚜렷)
        fig, ax = plt.subplots(figsize=(8, 6), facecolor=W, subplot_kw=dict(polar=True))
        ax.set_facecolor('#fafbff')
        ax.plot(angles, values, 'o-', linewidth=3.5, color=primary_color, markersize=8)
        ax.fill(angles, values, alpha=0.15, color=accent_color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=11, fontweight='bold', color=primary_color)
        ax.set_ylim(0, 10)
        ax.grid(color='#ddd', linewidth=1.2)
        ax.set_title(f'{theme_name} 테마 투자 매력도', fontsize=15,
                     fontweight='black', color=DARK, pad=20)

    elif layout == 'hexagon':
        # 6각형 레이아웃 (값 하나 추가)
        fig, ax = plt.subplots(figsize=(8, 6), facecolor=W, subplot_kw=dict(polar=True))
        # 6개 카테고리로 확장
        categories_6 = categories + ['기술력']
        N = 6
        angles_6 = [n / float(N) * 2 * np.pi for n in range(N)]
        angles_6 += angles_6[:1]
        values_6 = values[:-1] + [8, 8]  # 6번째 값 추가

        ax.set_facecolor('#fafbff')
        ax.plot(angles_6, values_6, 'o-', linewidth=2.5, color=primary_color)
        ax.fill(angles_6, values_6, alpha=0.2, color=primary_color)
        ax.set_xticks(angles_6[:-1])
        ax.set_xticklabels(categories_6, fontsize=10, fontweight='bold', color='#333')
        ax.set_ylim(0, 10)
        ax.grid(color='#ddd', linewidth=0.8)
        ax.set_title(f'{theme_name} 테마 투자 매력도', fontsize=15,
                     fontweight='black', color=DARK, pad=20)

    else:  # circular, polygonal 등 폴백
        fig, ax = plt.subplots(figsize=(8, 6), facecolor=W, subplot_kw=dict(polar=True))
        ax.set_facecolor('#fafbff')
        ax.plot(angles, values, 'o-', linewidth=2.5, color=primary_color)
        ax.fill(angles, values, alpha=0.2, color=primary_color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, fontsize=11, fontweight='bold', color='#333')
        ax.set_ylim(0, 10)
        ax.grid(color='#ddd', linewidth=0.8)
        ax.set_title(f'{theme_name} 테마 투자 매력도', fontsize=15,
                     fontweight='black', color=DARK, pad=20)

    apply_style_to_chart(fig, ax, style_spec)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 투자 매력도', _cap('radar', t=theme_name))


def make_theme_factors_chart(theme_name: str) -> str:
    """★ 동적 스타일 생성 — 바/버블/게이지 등 다양한 형태."""
    from JARVIS06_IMAGE.style_engine import generate_style_spec, apply_style_to_chart, get_color_from_spec

    setup_chart_defaults(_FONT_PATH)

    # 1단계: LLM이 스타일 창작
    style_spec = generate_style_spec("factors", theme_name)
    layout = style_spec.get("layout", "bars_horizontal")
    primary_color = get_color_from_spec(style_spec, "primary_color")
    accent_color = get_color_from_spec(style_spec, "accent_color")

    up_factors = ['정책 지원 확대', '기술 혁신 가속', '글로벌 수요 증가', '실적 개선 기대', '시장 관심 집중']
    dn_factors = ['금리 인상 리스크', '경기 침체 우려', '공급 과잉 가능성', '규제 강화 리스크', '수익성 불확실']

    # 2단계: 레이아웃별 렌더링
    if layout == 'bars_horizontal':
        # 가로 바 차트 (좌우 대비)
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=W)

        for ax, (factors, col) in zip(axes, [
            (up_factors, primary_color),
            (dn_factors, accent_color),
        ]):
            ax.set_facecolor('#fafbff')
            ax.barh(range(len(factors)), [8, 7, 9, 6, 8] if col == primary_color else [5, 6, 4, 7, 5],
                    color=col, alpha=0.75, height=0.55, edgecolor=W, linewidth=2)
            ax.set_yticks(range(len(factors)))
            ax.set_yticklabels(factors, fontsize=10, color='#333')
            title = f'📈 {theme_name} 상승 요인' if col == primary_color else f'📉 {theme_name} 하락 요인'
            ax.set_title(title, fontsize=13, fontweight='black', color=col, pad=12)
            ax.set_xlim(0, 10)
            for s in ['top', 'right', 'bottom']:
                ax.spines[s].set_visible(False)
            ax.spines['left'].set_color('#ddd')
            ax.tick_params(bottom=False, labelbottom=False, colors='#888')

    elif layout == 'bars_vertical':
        # 세로 바 차트
        fig, ax = plt.subplots(figsize=(12, 6), facecolor=W)
        ax.set_facecolor('#fafbff')

        all_factors = up_factors + dn_factors
        x_pos = np.arange(len(all_factors))
        values = [8, 7, 9, 6, 8, 5, 6, 4, 7, 5]
        colors = [primary_color] * len(up_factors) + [accent_color] * len(dn_factors)

        ax.bar(x_pos, values, color=colors, alpha=0.75, edgecolor=W, linewidth=1.5)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(all_factors, fontsize=9, rotation=45, ha='right', color='#333')
        ax.set_ylabel('강도', fontsize=11, fontweight='bold')
        ax.set_title(f'{theme_name} 테마 상승/하락 요인', fontsize=14, fontweight='black', color=DARK, pad=12)
        ax.set_ylim(0, 10)
        ax.grid(axis='y', color='#f0f0f0', linewidth=0.8, alpha=0.5)

        for s in ['top', 'right']:
            ax.spines[s].set_visible(False)

    else:  # bubble, gauge 등 폴백
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), facecolor=W)

        for ax, (factors, col) in zip(axes, [
            (up_factors, primary_color),
            (dn_factors, accent_color),
        ]):
            ax.set_facecolor('#fafbff')
            ax.barh(range(len(factors)), [8, 7, 9, 6, 8] if col == primary_color else [5, 6, 4, 7, 5],
                    color=col, alpha=0.75, height=0.55, edgecolor=W, linewidth=2)
            ax.set_yticks(range(len(factors)))
            ax.set_yticklabels(factors, fontsize=10, color='#333')
            title = f'📈 {theme_name} 상승 요인' if col == primary_color else f'📉 {theme_name} 하락 요인'
            ax.set_title(title, fontsize=13, fontweight='black', color=col, pad=12)
            ax.set_xlim(0, 10)
            for s in ['top', 'right', 'bottom']:
                ax.spines[s].set_visible(False)
            ax.spines['left'].set_color('#ddd')
            ax.tick_params(bottom=False, labelbottom=False, colors='#888')

    apply_style_to_chart(fig, axes if layout == 'bars_vertical' else list(axes) if hasattr(axes, '__iter__') else axes, style_spec)
    plt.tight_layout(pad=2)
    return wrap_img(fig_to_b64(fig), f'{theme_name} 투자 요인', _cap('factors', t=theme_name))


def make_investment_timeline_chart(theme_name: str) -> str:
    """★ 동적 스타일 생성 — 선형/원형/트리 등 다양한 타임라인."""
    from JARVIS06_IMAGE.style_engine import generate_style_spec, apply_style_to_chart, get_color_from_spec

    setup_chart_defaults(_FONT_PATH)

    # 1단계: LLM이 스타일 창작
    style_spec = generate_style_spec("timeline", theme_name)
    layout = style_spec.get("layout", "linear")
    primary_color = get_color_from_spec(style_spec, "primary_color")
    accent_color = get_color_from_spec(style_spec, "accent_color")

    steps = [
        ('STEP 1', '테마 확인', f'관련주 {_LM.STOCK_COUNT_PER_POST}개\n종목 파악'),
        ('STEP 2', '재무 분석', 'PER·ROE·\n영업이익률 확인'),
        ('STEP 3', '대장주 선정', '시총 1~3위\n집중 분석'),
        ('STEP 4', '진입 타이밍', '거래량·모멘텀\n확인 후 진입'),
        ('STEP 5', '리스크 관리', '손절 라인\n사전 설정'),
    ]

    # 색상 배열 (primary → accent 그라디언트)
    step_colors = [
        primary_color,
        _interpolate_color(primary_color, accent_color, 0.25),
        _interpolate_color(primary_color, accent_color, 0.5),
        _interpolate_color(primary_color, accent_color, 0.75),
        accent_color,
    ]

    if layout in ['linear', 'flow', 'stepped']:
        # 선형 타임라인
        fig, ax = plt.subplots(figsize=(13, 4.5), facecolor=W)
        ax.set_facecolor('#fafbff'); ax.axis('off')

        ax.plot([0.5, 11.5], [2.0, 2.0], color='#cbd5e1', linewidth=3, zorder=1)
        xs = np.linspace(1.0, 11.0, len(steps))

        for i, (x0, (step, title, desc), col) in enumerate(zip(xs, steps, step_colors)):
            ax.scatter(x0, 2.0, s=250, color=col, zorder=3, edgecolors=W, linewidths=2.5)
            offset = 0.95 if i % 2 == 0 else -0.95
            ax.text(x0, 2.0 + offset, step, fontsize=10, fontweight='black',
                    ha='center', va='center', color=col)
            ax.text(x0, 2.0 + offset + (0.55 if offset > 0 else -0.55),
                    f'{title}\n{desc}', fontsize=8.5, ha='center', va='center',
                    color='#333', linespacing=1.5,
                    bbox=dict(boxstyle='round,pad=0.35', facecolor=W, edgecolor=col, linewidth=1.5))
            ax.plot([x0, x0], [2.0, 2.0 + (offset * 0.55)], color=col, linewidth=1.5, linestyle=':')

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.2)

    elif layout in ['circular', 'cycle']:
        # 원형 타임라인
        fig, ax = plt.subplots(figsize=(10, 10), facecolor=W)
        ax.set_facecolor('#fafbff'); ax.axis('off')

        n_steps = len(steps)
        angles = np.linspace(0, 2*np.pi, n_steps, endpoint=False)
        radius = 3.5

        center_x, center_y = 5, 5

        # 중심 원
        circle = plt.Circle((center_x, center_y), 0.8, color=primary_color, zorder=2)
        ax.add_patch(circle)
        ax.text(center_x, center_y, '시작', fontsize=11, fontweight='bold',
               ha='center', va='center', color=W, zorder=3)

        # 각 스텝
        for i, (angle, (step, title, desc), col) in enumerate(zip(angles, steps, step_colors)):
            x = center_x + radius * np.cos(angle)
            y = center_y + radius * np.sin(angle)

            # 스텝 원
            circle = plt.Circle((x, y), 0.6, color=col, zorder=2)
            ax.add_patch(circle)
            ax.text(x, y, f'{i+1}', fontsize=12, fontweight='bold',
                   ha='center', va='center', color=W, zorder=3)

            # 레이블
            label_radius = radius + 1.2
            label_x = center_x + label_radius * np.cos(angle)
            label_y = center_y + label_radius * np.sin(angle)
            ax.text(label_x, label_y, title, fontsize=10, fontweight='bold',
                   ha='center', va='center', color=col)

        ax.set_xlim(0, 10); ax.set_ylim(0, 10)

    else:  # tree, stepped 등 폴백
        fig, ax = plt.subplots(figsize=(13, 4.5), facecolor=W)
        ax.set_facecolor('#fafbff'); ax.axis('off')

        ax.plot([0.5, 11.5], [2.0, 2.0], color='#cbd5e1', linewidth=3, zorder=1)
        xs = np.linspace(1.0, 11.0, len(steps))

        for i, (x0, (step, title, desc), col) in enumerate(zip(xs, steps, step_colors)):
            ax.scatter(x0, 2.0, s=250, color=col, zorder=3, edgecolors=W, linewidths=2.5)
            offset = 0.95 if i % 2 == 0 else -0.95
            ax.text(x0, 2.0 + offset, step, fontsize=10, fontweight='black',
                    ha='center', va='center', color=col)
            ax.text(x0, 2.0 + offset + (0.55 if offset > 0 else -0.55),
                    f'{title}\n{desc}', fontsize=8.5, ha='center', va='center',
                    color='#333', linespacing=1.5,
                    bbox=dict(boxstyle='round,pad=0.35', facecolor=W, edgecolor=col, linewidth=1.5))
            ax.plot([x0, x0], [2.0, 2.0 + (offset * 0.55)], color=col, linewidth=1.5, linestyle=':')

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.2)

    ax.set_title(f'{theme_name} 테마 투자 5단계 전략', fontsize=17,
                 fontweight='black', color=DARK, pad=14)

    apply_style_to_chart(fig, ax, style_spec)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 투자 전략', _cap('timeline', t=theme_name))


def make_theme_mechanism_chart(theme_name: str) -> str:
    """★ 동적 스타일 생성 — 선형/피라미드/사이클 등 다양한 구조."""
    from shared.llm import invoke_text
    from JARVIS06_IMAGE.style_engine import generate_style_spec, apply_style_to_chart, get_color_from_spec

    setup_chart_defaults(_FONT_PATH)

    # 1단계: 테마 구조 정보 수집
    _raw = invoke_text(
        "writer",
        f"{theme_name} 테마의 핵심 구조를 5단계로 설명. 각각 '단계명|한줄설명' 형식, 5줄만.",
        timeout=60
    )
    lines = [l.strip() for l in (_raw or "").strip().splitlines() if '|' in l][:5]
    while len(lines) < 5:
        lines.append(f"단계{len(lines)+1}|{theme_name} 관련 단계")

    # 2단계: LLM이 스타일 창작
    style_spec = generate_style_spec("mechanism", theme_name)
    layout = style_spec.get("layout", "linear")
    primary_color = get_color_from_spec(style_spec, "primary_color")
    accent_color = get_color_from_spec(style_spec, "accent_color")

    # 색상 배열 (primary → accent)
    colors = [
        primary_color,
        _interpolate_color(primary_color, accent_color, 0.25),
        _interpolate_color(primary_color, accent_color, 0.5),
        _interpolate_color(primary_color, accent_color, 0.75),
        accent_color,
    ]

    # 3단계: 레이아웃별 렌더링 (선형만 구현, 나머지 폴백)
    if layout in ['linear', 'flow', 'pyramid']:
        fig, ax = plt.subplots(figsize=(13, 4.5), facecolor=W)
        ax.set_facecolor('#fafbff'); ax.axis('off')

        ax.plot([0.5, 11.5], [2.0, 2.0], color='#cbd5e1', linewidth=3, zorder=1)
        xs = np.linspace(1.0, 11.0, len(lines))

        for i, (x0, line) in enumerate(zip(xs, lines)):
            parts = line.split('|')
            step = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ''
            col = colors[i % len(colors)]

            ax.scatter(x0, 2.0, s=220, color=col, zorder=3, edgecolors=W, linewidths=2.5)
            offset = 0.95 if i % 2 == 0 else -0.95
            ax.text(x0, 2.0 + offset, step, fontsize=10, fontweight='black',
                    ha='center', va='center', color=col)
            ax.text(x0, 2.0 + offset + (0.55 if offset > 0 else -0.55), desc, fontsize=8.5,
                    ha='center', va='center', color='#333', linespacing=1.5,
                    bbox=dict(boxstyle='round,pad=0.35', facecolor=W, edgecolor=col, linewidth=1.5))
            ax.plot([x0, x0], [2.0, 2.0 + (offset * 0.55)], color=col, linewidth=1.5, linestyle=':')

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.2)

    else:  # cycle, tree 등 폴백
        fig, ax = plt.subplots(figsize=(13, 4.5), facecolor=W)
        ax.set_facecolor('#fafbff'); ax.axis('off')

        ax.plot([0.5, 11.5], [2.0, 2.0], color='#cbd5e1', linewidth=3, zorder=1)
        xs = np.linspace(1.0, 11.0, len(lines))

        for i, (x0, line) in enumerate(zip(xs, lines)):
            parts = line.split('|')
            step = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else ''
            col = colors[i % len(colors)]

            ax.scatter(x0, 2.0, s=220, color=col, zorder=3, edgecolors=W, linewidths=2.5)
            offset = 0.95 if i % 2 == 0 else -0.95
            ax.text(x0, 2.0 + offset, step, fontsize=10, fontweight='black',
                    ha='center', va='center', color=col)
            ax.text(x0, 2.0 + offset + (0.55 if offset > 0 else -0.55), desc, fontsize=8.5,
                    ha='center', va='center', color='#333', linespacing=1.5,
                    bbox=dict(boxstyle='round,pad=0.35', facecolor=W, edgecolor=col, linewidth=1.5))
            ax.plot([x0, x0], [2.0, 2.0 + (offset * 0.55)], color=col, linewidth=1.5, linestyle=':')

        ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.2)

    ax.set_title(f'{theme_name} 테마 구조 및 원리', fontsize=17, fontweight='black', color=DARK, pad=14)

    apply_style_to_chart(fig, ax, style_spec)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 원리', '')


def make_theme_applications_chart(theme_name: str) -> str:
    from shared.llm import invoke_text
    from JARVIS06_IMAGE.style_engine import generate_style_spec, hex_to_rgb

    # ★ 동적 스타일 생성 (매번 다른 디자인)
    style_spec = generate_style_spec("applications", theme_name)
    primary_color = style_spec.get("primary_color", "#7c3aed")
    accent_color = style_spec.get("accent_color", "#0891b2")

    _raw = invoke_text(
        "writer",
        f"{theme_name} 테마 활용분야 4가지. 각각 '분야명|설명1|설명2|영문3자' 형식으로 4줄만 출력. "
        f"설명1과 설명2는 각각 {_LM.TERM_FORMULA_MAX}자 이내로 2줄 설명.",
        timeout=60
    )
    lines = [l.strip() for l in (_raw or "").strip().splitlines() if '|' in l][:4]
    while len(lines) < 4:
        lines.append(f"분야{len(lines)+1}|{theme_name} 관련|핵심 분야|APP")
    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(12, 4.2), facecolor=W)
    ax.set_facecolor(W); ax.axis('off')

    # ★ 동적 색상 배열 생성 (primary → accent로 그라데이션, 매번 다름)
    colors = [_interpolate_color(primary_color, accent_color, i / 3.0) for i in range(4)]

    for i, line in enumerate(lines):
        parts = line.split('|')
        name  = parts[0].strip()
        desc1 = parts[1].strip() if len(parts) > 1 else ''
        desc2 = parts[2].strip() if len(parts) > 2 else ''
        icon  = parts[3].strip()[:3] if len(parts) > 3 else 'APP'
        col = colors[i % len(colors)]; x0 = i * 2.9 + 0.2
        ax.add_patch(FancyBboxPatch((x0, 0.25), 2.55, 3.55, boxstyle="round,pad=0.15",
                                     facecolor='#f8f9ff', edgecolor=col, linewidth=2.5))
        ax.add_patch(Circle((x0 + 1.28, 3.27), 0.42, facecolor=col, edgecolor=W, linewidth=2.5))
        ax.text(x0 + 1.28, 3.27, icon, fontsize=9.5, fontweight='black',
                ha='center', va='center', color=W)
        ax.text(x0 + 1.28, 2.58, name, fontsize=12, fontweight='black',
                ha='center', va='center', color=col)
        ax.plot([x0 + 0.35, x0 + 2.2], [2.22, 2.22], color=col, linewidth=1, alpha=0.35)
        ax.text(x0 + 1.28, 1.65, desc1, fontsize=9.5, ha='center', va='center', color='#444')
        ax.text(x0 + 1.28, 1.10, desc2, fontsize=9.5, ha='center', va='center', color='#444')
    ax.set_xlim(0, 12); ax.set_ylim(0, 4.0)
    ax.set_title(f'{theme_name} 테마 주요 활용분야 4가지', fontsize=17,
                 fontweight='black', color=style_spec.get("primary_color", DARK), pad=16)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 활용분야', '')


def make_theme_timeline_chart(theme_name: str) -> str:
    from shared.llm import invoke_text
    _raw = invoke_text(
        "writer",
        f"{theme_name} 테마 역사적 사건 5가지를 연도순으로. 각각 '연도|사건명' 형식, 5줄만.",
        timeout=60
    )
    lines = [l.strip() for l in (_raw or "").strip().splitlines() if '|' in l][:5]
    while len(lines) < 5:
        lines.append(f"202{len(lines)}|{theme_name} 주요 사건")
    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(12, 4), facecolor=W)
    ax.set_facecolor('#fafbff'); ax.axis('off')
    colors = ['#94a3b8', '#7c3aed', '#0891b2', '#f59e0b', '#ef4444']
    ax.plot([0.5, 11.5], [2.0, 2.0], color='#cbd5e1', linewidth=3, zorder=1)
    xs = np.linspace(1.0, 11.0, len(lines))
    for i, (x0, line) in enumerate(zip(xs, lines)):
        parts = line.split('|')
        year = parts[0].strip(); label = parts[1].strip() if len(parts) > 1 else ''
        col = colors[i % len(colors)]
        ax.scatter(x0, 2.0, s=180, color=col, zorder=3, edgecolors=W, linewidths=2)
        offset = 0.9 if i % 2 == 0 else -0.9
        ax.text(x0, 2.0 + offset, year, fontsize=11, fontweight='black',
                ha='center', va='center', color=col)
        ax.text(x0, 2.0 + offset + (0.52 if offset > 0 else -0.52), label, fontsize=9,
                ha='center', va='center', color='#333', linespacing=1.5,
                bbox=dict(boxstyle='round,pad=0.35', facecolor=W, edgecolor=col, linewidth=1.5))
        ax.plot([x0, x0], [2.0, 2.0 + (offset * 0.55)], color=col, linewidth=1.5, linestyle=':')
    ax.set_xlim(0, 12.5); ax.set_ylim(0, 4.0)
    ax.set_title(f'{theme_name} 테마 역사 타임라인', fontsize=17, fontweight='black', color=DARK, pad=14)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 타임라인', '')


def make_theme_concept_chart(theme_name: str) -> str:
    import re as _re
    from shared.llm import invoke_text
    _raw = invoke_text(
        "writer",
        f"{theme_name} 테마 핵심 키워드 4가지. 각각 '키워드|한줄설명|이모지' 형식, 4줄만.",
        timeout=60
    )
    lines = [l.strip() for l in (_raw or "").strip().splitlines() if '|' in l][:4]
    while len(lines) < 4:
        lines.append(f"핵심{len(lines)+1}|{theme_name} 핵심 내용|📌")
    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(12, 4.2), facecolor=W)
    ax.set_facecolor(W); ax.axis('off')
    # ★ ERRORS [172] 2026-05-26: 고정 팔레트 → 실행마다 다른 동적 색상
    import colorsys as _cs, hashlib as _hlib, time as _time
    _seed_hex = _hlib.md5(f"{theme_name}_{_time.time_ns()}".encode()).hexdigest()
    _base_hue = int(_seed_hex[:4], 16) / 0xFFFF
    def _dyn(offset, sat=0.70, val=0.78):
        r, g, b = _cs.hsv_to_rgb((_base_hue + offset) % 1.0, sat, val)
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
    colors = [_dyn(0), _dyn(0.25), _dyn(0.50), _dyn(0.75)]
    for i, line in enumerate(lines):
        parts = line.split('|')
        kw = parts[0].strip()
        desc = parts[1].strip() if len(parts) > 1 else ''
        icon = parts[2].strip() if len(parts) > 2 else '📌'
        col = colors[i % len(colors)]; x0 = i * 2.9 + 0.2
        ax.add_patch(FancyBboxPatch((x0, 0.25), 2.55, 3.55, boxstyle="round,pad=0.15",
                                     facecolor='#f8f9ff', edgecolor=col, linewidth=2.5))
        ax.add_patch(FancyBboxPatch((x0 + 0.1, 3.0), 2.35, 0.65, boxstyle="round,pad=0.1",
                                     facecolor=col, edgecolor='none'))
        ax.text(x0 + 1.28, 3.33, f'{icon} {kw}', fontsize=10, fontweight='black',
                ha='center', va='center', color=W)
        if len(desc) > _LM.TERM_FORMULA_MAX:
            mid = len(desc) // 2
            for j in range(mid, min(mid + 8, len(desc))):
                if desc[j] == ' ':
                    desc = desc[:j] + '\n' + desc[j + 1:]
                    break
        ax.text(x0 + 1.28, 1.75, desc, fontsize=9, ha='center', va='center',
                color='#333', linespacing=1.6, multialignment='center')
    ax.set_xlim(0, 12); ax.set_ylim(0, 4.0)
    ax.set_title(f'{theme_name} 테마 핵심 키워드 4선', fontsize=17, fontweight='black', color=DARK, pad=16)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), f'{theme_name} 핵심 키워드', '')


def make_terms_chart(theme_name: str = '') -> str:
    import textwrap, re as _re, json

    def _get_dynamic_term_palette(theme: str = "") -> list:
        """투자 용어 차트용 동적 팔레트 생성 (BLOG_SUPREME_LAW 제11조)."""
        try:
            from shared.llm import invoke_text
            prompt = f"For '{theme or 'general'}' stocks theme term chart, generate 3 harmonious color pairs (bright + light-bg). Each used for one card. Return JSON: {{\"palette\": [[\"#xxxxxx\", \"#xxxxxx\"], ...]}}"
            result = invoke_text("writer_fast", prompt, temperature=0.7, max_tokens=100)
            data = json.loads(result)
            pal = data.get("palette", [])
            # 튜플로 변환 + 최소 3개 보장
            palette = [tuple(p) if isinstance(p, list) else p for p in pal]
            while len(palette) < 3:
                palette.append(('#4f46e5', '#eef2ff'))  # 폴백
            return palette[:3]
        except Exception as e:
            log.warning(f"[terms_chart] 동적 팔레트 생성 실패: {e}")
            return [('#4f46e5', '#eef2ff'), ('#0891b2', '#e0f7ff'), ('#059669', '#d1fae5')]

    palette = _get_dynamic_term_palette(theme_name)
    fallback_terms = [
        ('PER\n주가수익비율', '"주가 / 주당순이익"\n낮을수록 저평가\n예) 20배 = 이익의 20배 가격'),
        ('ROE\n자기자본이익률', '"순이익 / 자기자본 × 100"\n10% 이상 우량\n예) ROE 15% = 효율적 경영'),
        ('영업이익률', '"영업이익 / 매출 × 100"\n본업 수익성 지표\n예) 20% = 100원에 20원 이익'),
    ]
    terms_data = fallback_terms

    if theme_name:
        try:
            from shared.llm import invoke_text as _inv_cli
            raw = _inv_cli(
                "writer",
                f"{theme_name} 테마 주식 투자 시 반드시 알아야 할 핵심 용어 3개.\n\n"
                f"출력 형식 (정확히 아래 형식으로만):\n"
                f"용어명|계산법 또는 정의|투자 시 판단 기준\n\n"
                f"규칙: 정확히 3줄만. 번호 없이. "
                f"용어명 {_LM.TERM_NAME_MAX}자 이내, 계산법 {_LM.TERM_FORMULA_MAX}자 이내, "
                f"판단기준 {_LM.TERM_CRITERIA_MAX}자 이내. "
                f"{theme_name} 산업 특화 용어.",
                timeout=60
            ) or ""
            parsed = []
            for line in raw.splitlines():
                line = _re.sub(r'^\*+\d*\.?\s*|\*+|\d+\.\s+', '', line).strip()
                if line.count('|') >= 2:
                    parts = line.split('|', 2)
                    name    = _re.sub(r'^\*+|\*+$', '', parts[0]).strip()
                    formula = parts[1].strip()
                    meaning = parts[2].strip()
                    if name and formula and meaning:
                        parsed.append((name, f'"{formula}"\n{meaning}'))
            if len(parsed) >= 3:
                terms_data = parsed[:3]
        except Exception as e:
            log.warning(f"[terms_chart] API 실패: {e}")
            _g_report("image", e, module=__name__)

    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=(12, 4.2), facecolor=W)
    ax.axis('off')

    for i, (title, desc) in enumerate(terms_data):
        col, bg = palette[i]
        _wrap_w = _LM.LINE_BREAK_THRESHOLD - 2
        desc_lines = []
        for raw_line in desc.split('\n'):
            if len(raw_line) > _wrap_w:
                import textwrap as _tw
                desc_lines.append(_tw.fill(raw_line, width=_wrap_w))
            else:
                desc_lines.append(raw_line)
        desc_final = '\n'.join(desc_lines)

        x0 = i * 3.95 + 0.2
        ax.add_patch(FancyBboxPatch((x0, 0.3), 3.55, 3.7, boxstyle="round,pad=0.15",
                                     facecolor=bg, edgecolor=col, linewidth=2.5))
        ax.add_patch(FancyBboxPatch((x0 + 0.1, 2.9), 3.35, 0.9, boxstyle="round,pad=0.1",
                                     facecolor=col, edgecolor='none', linewidth=0))
        ax.text(x0 + 1.78, 3.35, title, fontsize=11.5, fontweight='black',
                ha='center', va='center', color=W)
        ax.text(x0 + 1.78, 1.75, desc_final, fontsize=9.2, ha='center', va='center',
                color='#333', linespacing=1.65, multialignment='center')

    ax.set_xlim(0, 12.1); ax.set_ylim(0, 4.2)
    title_text = f'{theme_name} 핵심 용어 3분 완성' if theme_name else '투자 핵심 용어 3분 완성'
    ax.set_title(title_text, fontsize=17, fontweight='black', color=DARK, pad=14)
    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), '투자 용어 설명', '')


def make_profit_donut(profit: int, loss: int) -> str:
    """★ 도넛 차트 — 실제 흑자/적자 종목 수 기반."""
    setup_chart_defaults(_FONT_PATH)
    fig, ax = plt.subplots(figsize=CHART_STYLE["FIGSIZE_SQUARE"], facecolor=W)
    wedges, _, ats = ax.pie(
        [profit, loss], colors=['#22c55e', '#ef4444'],
        autopct='%1.0f%%', startangle=90, explode=(0.05, 0.05),
        wedgeprops=dict(width=0.58, edgecolor=W, linewidth=4),
        textprops=dict(fontsize=CHART_STYLE["FONT_VALUE"], fontweight='bold')
    )
    for at in ats:
        at.set_fontsize(CHART_STYLE["FONT_VALUE"])
        at.set_fontweight('black')
        at.set_color(W)
    ax.text(0, 0, f'{profit+loss}개\n종목', ha='center', va='center',
            fontsize=CHART_STYLE["FONT_LABEL"], fontweight='black', color=DARK)
    legend_els = [
        mpatches.Patch(facecolor='#22c55e', label=f'흑자 기업 {profit}개'),
        mpatches.Patch(facecolor='#ef4444', label=f'적자 기업 {loss}개'),
    ]
    ax.legend(handles=legend_els, loc='lower center', bbox_to_anchor=(0.5, -0.06),
              fontsize=CHART_STYLE["FONT_LABEL"], frameon=False, ncol=2)
    ax.set_title('테마 내 흑자 / 적자 종목 비율',
                 fontsize=CHART_STYLE["FONT_TITLE"], fontweight='black', color=DARK, pad=16)
    plt.tight_layout(pad=CHART_STYLE["TIGHT_PAD"])
    return wrap_img(fig_to_b64(fig), '흑자적자 비율', _cap('profit_loss', p=profit, l=loss))


def make_cap_bar(names, caps) -> str:
    """★ 3D 막대 차트 — 실제 시가총액 데이터 기반."""
    setup_chart_defaults(_FONT_PATH)
    data = [(n, c / 1e8) for n, c in zip(names, caps) if c > 0][:5]
    if not data:
        return ''
    nms = [d[0] for d in data]
    vals = [d[1] for d in data]
    colors_bar = ['#4f46e5', '#7c3aed', '#0891b2', '#059669', '#d97706']

    fig = plt.figure(figsize=CHART_STYLE["FIGSIZE_STD"], facecolor=W)
    ax = fig.add_subplot(111, projection='3d')
    ax.set_facecolor('#fafbff')

    xs = np.arange(len(nms))
    dx = dy = 0.6
    for i, (val, col) in enumerate(zip(vals, colors_bar[:len(vals)])):
        ax.bar3d(i - dx / 2, 0, 0, dx, dy, val, color=col, alpha=0.88, shade=True)
        ax.text(i, dy + 0.05, val + max(vals) * 0.03,
                f'{val:,.0f}억',
                ha='center', va='bottom',
                fontsize=CHART_STYLE["FONT_VALUE"],
                fontweight=CHART_STYLE["FONT_WEIGHT"], color='#222')

    ax.set_xticks(xs)
    ax.set_xticklabels(nms, fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_yticks([])
    ax.set_zlabel('시가총액 (억원)', fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_title('시가총액 TOP 5 비교', fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight=CHART_STYLE["FONT_WEIGHT"], color=DARK, pad=16)
    ax.view_init(elev=22, azim=-55)
    ax.grid(True, alpha=0.25)
    ax.set_zlim(0, max(vals) * 1.18)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.90, bottom=0.06)
    return wrap_img(fig_to_b64(fig), '시총 TOP5', _cap('mktcap'))


def make_per_bar(names, pers) -> str:
    """★ 3D 막대 차트 — 실제 PER 데이터 기반 (낮을수록 저평가 색상 강조)."""
    setup_chart_defaults(_FONT_PATH)
    data = [(n, p) for n, p in zip(names, pers) if p and 0 < p < 500]
    if len(data) < 2:
        return ''
    nms = [d[0] for d in data]
    vals = [d[1] for d in data]
    bcols = ['#22c55e' if v < 30 else '#f59e0b' if v < 80 else '#ef4444' for v in vals]

    fig = plt.figure(figsize=CHART_STYLE["FIGSIZE_STD"], facecolor=W)
    ax = fig.add_subplot(111, projection='3d')
    ax.set_facecolor('#fafbff')

    xs = np.arange(len(nms))
    dx = dy = 0.55
    for i, (val, col) in enumerate(zip(vals, bcols)):
        ax.bar3d(i - dx / 2, 0, 0, dx, dy, val, color=col, alpha=0.88, shade=True)
        ax.text(i, dy + 0.05, val + max(vals) * 0.04,
                f'{val:.1f}배',
                ha='center', va='bottom',
                fontsize=CHART_STYLE["FONT_VALUE"],
                fontweight=CHART_STYLE["FONT_WEIGHT"], color='#222')

    # 기준선 평면
    ax.plot([xs[0] - 0.5, xs[-1] + 0.5], [0, 0], [20, 20],
            color='#22c55e', linewidth=2.5, linestyle='--', alpha=0.8, label='기준 20배')

    ax.set_xticks(xs)
    ax.set_xticklabels(nms, fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_yticks([])
    ax.set_zlabel('PER (배)', fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_title('흑자 종목 PER 비교 (낮을수록 저평가)',
                 fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight=CHART_STYLE["FONT_WEIGHT"], color=DARK, pad=16)
    ax.view_init(elev=22, azim=-55)
    ax.grid(True, alpha=0.25)
    ax.set_zlim(0, max(vals) * 1.2)

    legend_els = [
        mpatches.Patch(facecolor='#22c55e', label='저평가 (30배 미만)'),
        mpatches.Patch(facecolor='#f59e0b', label='보통 (30~80배)'),
        mpatches.Patch(facecolor='#ef4444', label='고평가 (80배 초과)'),
    ]
    ax.legend(handles=legend_els, loc='upper right',
              fontsize=CHART_STYLE["FONT_CAPTION"], frameon=True)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.90, bottom=0.06)
    return wrap_img(fig_to_b64(fig), 'PER 비교', _cap('per'))


def make_profitability_chart(names, op_margins, roes) -> str:
    """★ 3D 그룹 막대 차트 — 실제 영업이익률·ROE 데이터 기반."""
    setup_chart_defaults(_FONT_PATH)
    data = [(n, om, ro) for n, om, ro in zip(names, op_margins, roes)
            if om is not None and ro is not None]
    if len(data) < 2:
        return ''
    nms = [d[0] for d in data]
    oms = [d[1] for d in data]
    ros = [d[2] for d in data]

    fig = plt.figure(figsize=CHART_STYLE["FIGSIZE_WIDE"], facecolor=W)
    ax = fig.add_subplot(111, projection='3d')
    ax.set_facecolor('#fafbff')

    xs = np.arange(len(nms))
    dx = 0.35
    dy = 0.5

    for i, (om, ro) in enumerate(zip(oms, ros)):
        # 영업이익률 (앞)
        ax.bar3d(i - dx / 2, 0, 0, dx, dy, om if om >= 0 else 0,
                 color='#4f46e5', alpha=0.88, shade=True)
        if om < 0:
            ax.bar3d(i - dx / 2, 0, om, dx, dy, -om,
                     color='#ef4444', alpha=0.65, shade=True)
        # ROE (뒤)
        ax.bar3d(i - dx / 2, dy + 0.08, 0, dx, dy, ro if ro >= 0 else 0,
                 color='#0891b2', alpha=0.88, shade=True)
        if ro < 0:
            ax.bar3d(i - dx / 2, dy + 0.08, ro, dx, dy, -ro,
                     color='#ef4444', alpha=0.65, shade=True)
        # 값 레이블
        max_val = max(abs(om), abs(ro), 0.1)
        ax.text(i, dy * 0.5, max(om, 0) + max_val * 0.06,
                f'{om:.1f}%', ha='center', fontsize=CHART_STYLE["FONT_SMALL"],
                fontweight=CHART_STYLE["FONT_WEIGHT"], color='#333')
        ax.text(i, dy * 1.6, max(ro, 0) + max_val * 0.06,
                f'{ro:.1f}%', ha='center', fontsize=CHART_STYLE["FONT_SMALL"],
                fontweight=CHART_STYLE["FONT_WEIGHT"], color='#333')

    ax.set_xticks(xs)
    ax.set_xticklabels(nms, fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_yticks([dy * 0.5, dy * 1.6])
    ax.set_yticklabels(['영업이익률', 'ROE'], fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_zlabel('비율 (%)', fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_title('종목별 수익성 비교 (영업이익률 vs ROE)',
                 fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight=CHART_STYLE["FONT_WEIGHT"], color=DARK, pad=16)
    ax.view_init(elev=22, azim=-60)
    ax.grid(True, alpha=0.25)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.90, bottom=0.06)
    return wrap_img(fig_to_b64(fig), '수익성 비교', _cap('profitability'))


def make_revenue_chart(names, revenues, net_incomes) -> str:
    """★ 3D 그룹 막대 차트 — 실제 매출액·순이익 데이터 기반."""
    setup_chart_defaults(_FONT_PATH)
    data = [(n, rv / 1e8, ni / 1e8) for n, rv, ni in zip(names, revenues, net_incomes)
            if rv and rv > 0][:6]
    if not data:
        return ''
    nms = [d[0] for d in data]
    rvs = [d[1] for d in data]
    nis = [d[2] for d in data]

    fig = plt.figure(figsize=CHART_STYLE["FIGSIZE_WIDE"], facecolor=W)
    ax = fig.add_subplot(111, projection='3d')
    ax.set_facecolor('#fafbff')

    xs = np.arange(len(nms))
    dx = 0.38
    dy = 0.5

    for i, (rv, ni) in enumerate(zip(rvs, nis)):
        # 매출액 (앞)
        ax.bar3d(i - dx / 2, 0, 0, dx, dy, rv,
                 color='#94a3b8', alpha=0.88, shade=True)
        # 순이익/손실 (뒤)
        ni_col = '#22c55e' if ni >= 0 else '#ef4444'
        ax.bar3d(i - dx / 2, dy + 0.08, 0, dx, dy, ni if ni >= 0 else 0,
                 color=ni_col, alpha=0.88, shade=True)
        if ni < 0:
            ax.bar3d(i - dx / 2, dy + 0.08, ni, dx, dy, -ni,
                     color='#ef4444', alpha=0.65, shade=True)
        # 값 레이블
        ax.text(i, dy * 0.5, rv + max(rvs) * 0.04,
                f'{rv:,.0f}억', ha='center', fontsize=CHART_STYLE["FONT_SMALL"],
                fontweight=CHART_STYLE["FONT_WEIGHT"], color='#333')
        ax.text(i, dy * 1.6, max(ni, 0) + max(rvs) * 0.04,
                f'{ni:+,.0f}억', ha='center', fontsize=CHART_STYLE["FONT_SMALL"],
                fontweight=CHART_STYLE["FONT_WEIGHT"],
                color='#22c55e' if ni >= 0 else '#ef4444')

    ax.set_xticks(xs)
    ax.set_xticklabels(nms, fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_yticks([dy * 0.5, dy * 1.6])
    ax.set_yticklabels(['매출액', '순이익'], fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_zlabel('금액 (억원)', fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_title('종목별 매출액 vs 순이익 비교',
                 fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight=CHART_STYLE["FONT_WEIGHT"], color=DARK, pad=16)
    ax.view_init(elev=22, azim=-60)
    ax.grid(True, alpha=0.25)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.90, bottom=0.06)
    return wrap_img(fig_to_b64(fig), '매출 순이익', _cap('revenue'))


def make_theme_return_chart(names, tickers) -> str:
    """★ 3D 막대 차트 — yfinance 실제 3개월 수익률 데이터 기반."""
    setup_chart_defaults(_FONT_PATH)
    returns: list[float] = []
    valid_names: list[str] = []
    for name, ticker in zip(names[:8], tickers[:8]):
        try:
            df = _j09_hist(ticker, period="3mo")
            if not df.empty and len(df) > 5:
                r = (df['Close'].iloc[-1] - df['Close'].iloc[0]) / df['Close'].iloc[0] * 100
                returns.append(round(r, 1))
                valid_names.append(name)
        except Exception:
            pass
    if len(returns) < 3:
        return ''

    fig = plt.figure(figsize=CHART_STYLE["FIGSIZE_TALL"], facecolor=W)
    ax = fig.add_subplot(111, projection='3d')
    ax.set_facecolor('#fafbff')

    ys = np.arange(len(valid_names))
    dx = 0.5
    dy = 0.5
    abs_max = max(abs(r) for r in returns) or 1.0

    for i, (nm, r) in enumerate(zip(valid_names, returns)):
        col = '#22c55e' if r >= 0 else '#ef4444'
        # 양수: 0에서 r까지, 음수: r에서 0까지
        z0 = min(r, 0)
        dz = abs(r)
        ax.bar3d(0, i - dy / 2, z0, dx, dy, dz, color=col, alpha=0.88, shade=True)
        ax.text(dx + abs_max * 0.05, i, z0 + dz / 2,
                f'{r:+.1f}%', va='center',
                fontsize=CHART_STYLE["FONT_VALUE"],
                fontweight=CHART_STYLE["FONT_WEIGHT"],
                color=col)

    # 0선
    ax.plot([0, 0], [ys[0] - 0.4, ys[-1] + 0.4], [0, 0],
            color='#333', linewidth=2, zorder=5)

    ax.set_yticks(ys)
    ax.set_yticklabels(valid_names, fontsize=CHART_STYLE["FONT_TICK"],
                       fontweight=CHART_STYLE["FONT_WEIGHT"])
    ax.set_xticks([])
    ax.set_zlabel('수익률 (%)', fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight=CHART_STYLE["FONT_WEIGHT"])
    title_nm = valid_names[0] if valid_names else ''
    ax.set_title(f'{title_nm} 외 {len(valid_names)-1}개 종목 3개월 실제 수익률',
                 fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight=CHART_STYLE["FONT_WEIGHT"], color=DARK, pad=16)
    ax.view_init(elev=18, azim=210)
    ax.grid(True, alpha=0.25)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.90, bottom=0.06)
    return wrap_img(fig_to_b64(fig), '3개월 수익률', _cap('return3m'))


def make_risk_chart(names, is_profits, caps, pers) -> str:
    """★ 위험도 카드 차트 — 실제 흑자여부·시총·PER 기반 점수."""
    setup_chart_defaults(_FONT_PATH)

    def score(isp, cap, per):
        s = 0
        if not isp: s += 40
        if cap < 1e11: s += 30
        elif cap < 5e11: s += 15
        if per and per > 100: s += 20
        elif per and per > 50: s += 10
        return min(s, 100)

    scores = [score(p, c, pr) for p, c, pr in zip(is_profits, caps, pers)]
    data = list(zip(names, scores))[:10]
    n = len(data)
    card_w = min(1.12, 11.5 / n)

    fig, ax = plt.subplots(figsize=CHART_STYLE["FIGSIZE_WIDE"], facecolor=W)
    ax.set_facecolor(W); ax.axis('off')
    for i, (nm, sc) in enumerate(data):
        x0 = i * card_w + 0.05
        if sc <= 25:   bg, tc, lbl = '#dcfce7', '#166534', '낮음'
        elif sc <= 55: bg, tc, lbl = '#fef3c7', '#92400e', '보통'
        else:          bg, tc, lbl = '#fee2e2', '#991b1b', '높음'
        rect = FancyBboxPatch((x0, 0.4), card_w - 0.1, 2.8, boxstyle="round,pad=0.1",
                               facecolor=bg, edgecolor=tc, linewidth=2.5)
        ax.add_patch(rect)
        ax.text(x0 + (card_w - 0.1) / 2, 2.78, nm,
                fontsize=CHART_STYLE["FONT_SMALL"], fontweight='bold',
                ha='center', va='center', color=DARK)
        ax.text(x0 + (card_w - 0.1) / 2, 1.9, f'{sc}점',
                fontsize=CHART_STYLE["FONT_TITLE"], fontweight='black',
                ha='center', va='center', color=tc)
        ax.text(x0 + (card_w - 0.1) / 2, 0.95, f'위험 {lbl}',
                fontsize=CHART_STYLE["FONT_SMALL"], fontweight='bold',
                ha='center', va='center', color=tc)
    ax.set_xlim(0, n * card_w); ax.set_ylim(0, 3.6)
    ax.set_title('종목별 투자 위험도 (100점 = 최고위험)',
                 fontsize=CHART_STYLE["FONT_TITLE"],
                 fontweight='black', color=DARK, pad=14, y=1.0)
    legend_els = [
        mpatches.Patch(facecolor='#dcfce7', edgecolor='#166534', linewidth=2, label='안전 (0~25점)'),
        mpatches.Patch(facecolor='#fef3c7', edgecolor='#92400e', linewidth=2, label='주의 (26~55점)'),
        mpatches.Patch(facecolor='#fee2e2', edgecolor='#991b1b', linewidth=2, label='위험 (56~100점)'),
    ]
    ax.legend(handles=legend_els, loc='lower center', bbox_to_anchor=(0.5, -0.14),
              fontsize=CHART_STYLE["FONT_CAPTION"], frameon=False, ncol=3)
    plt.tight_layout(pad=CHART_STYLE["TIGHT_PAD"])
    return wrap_img(fig_to_b64(fig), '투자위험도', _cap('risk'))


def make_portfolio_chart(names=None) -> str:
    setup_chart_defaults(_FONT_PATH)
    fig, axes = plt.subplots(1, 3, figsize=CHART_STYLE["FIGSIZE_2COL"], facecolor=W)
    n = names or ['1위종목', '2위종목', '3위종목', '4위종목', '5위종목']
    s1 = n[0] if len(n) > 0 else '대장주'
    s2 = n[1] if len(n) > 1 else '2위'
    s3 = n[2] if len(n) > 2 else '3위'
    s4 = n[3] if len(n) > 3 else '4위'
    s5 = n[4] if len(n) > 4 else '5위'
    portfolios = [
        ('안전형\n(리스크 최소화)',  [(s1, 60, '#059669'), (s2, 40, '#0891b2')], '#059669'),
        ('균형형\n(수익-안정 균형)', [(s1, 40, '#4f46e5'), (s2, 30, '#059669'), (s3, 30, '#0891b2')], '#4f46e5'),
        ('공격형\n(고위험 고수익)',  [(s3, 60, '#ef4444'), (s4, 20, '#f59e0b'), (s5, 20, '#94a3b8')], '#ef4444'),
    ]
    for ax, (title, items, bc) in zip(axes, portfolios):
        sizes = [d[1] for d in items]; cols = [d[2] for d in items]
        _, _, ats = ax.pie(sizes, colors=cols, autopct='%1.0f%%', startangle=90,
                           wedgeprops=dict(edgecolor=W, linewidth=3.5),
                           textprops=dict(fontsize=CHART_STYLE["FONT_LABEL"],
                                          fontweight='bold'))
        for at in ats:
            at.set_fontsize(CHART_STYLE["FONT_LABEL"])
            at.set_fontweight('black')
            at.set_color(W)
        ax.set_title(title, fontsize=CHART_STYLE["FONT_LABEL"],
                     fontweight='black', color=bc, pad=10)
        ax.legend([d[0] for d in items], loc='lower center',
                  bbox_to_anchor=(0.5, -0.22), fontsize=CHART_STYLE["FONT_CAPTION"],
                  frameon=False, ncol=len(items))
    fig.suptitle('투자 성향별 포트폴리오 추천',
                 fontsize=CHART_STYLE["FONT_TITLE"], fontweight='black', color=DARK, y=1.02)
    plt.tight_layout(pad=CHART_STYLE["TIGHT_PAD"])
    return wrap_img(fig_to_b64(fig), '포트폴리오', _cap('portfolio'))


def make_checklist_chart() -> str:
    """테마주 투자 6대 원칙 — 매 실행마다 다른 레이아웃/색상 조합."""
    import random
    setup_chart_defaults(_FONT_PATH)

    PRINCIPLES = [
        ('흑자 기업 우선 투자',  '매출이 있고 순이익이 플러스인 기업부터 접근하세요'),
        ('PER 30배 이하 확인',   '지나치게 고평가된 종목은 조정 위험이 있어요'),
        ('10~20% 손절 라인 설정', '테마주는 급락도 빠릅니다. 손절 원칙을 지키세요'),
        ('2~3개 종목 분산 투자', '한 종목에 몰빵하면 전체 손실로 이어질 수 있어요'),
        ('뉴스보다 실적으로 판단', '기대감 뉴스보다 실제 매출/이익 변화를 보세요'),
        ('여유 자금만 투자',     '생활비나 급전으로 투자하면 실수가 생겨요'),
    ]
    PALETTES = [
        ['#22c55e', '#0891b2', '#f59e0b', '#7c3aed', '#059669', '#ef4444'],
        ['#4f46e5', '#0891b2', '#22c55e', '#ea580c', '#be185d', '#0369a1'],
        ['#dc2626', '#2563eb', '#16a34a', '#ca8a04', '#9333ea', '#0f766e'],
        ['#0d9488', '#7c3aed', '#ea580c', '#1d4ed8', '#15803d', '#b45309'],
        ['#be185d', '#1e40af', '#065f46', '#b45309', '#6d28d9', '#b91c1c'],
        ['#0284c7', '#d97706', '#15803d', '#7e22ce', '#dc2626', '#0f766e'],
    ]
    colors = random.choice(PALETTES)
    variant = random.randint(0, 2)

    if variant == 0:
        fig, ax = plt.subplots(figsize=(10, 6.2), facecolor=W)
        ax.set_facecolor(W); ax.axis('off')
        for i, (title, desc) in enumerate(PRINCIPLES):
            col = colors[i]
            row, col_idx = divmod(i, 2)
            x0 = col_idx * 5.5 + 0.3; y0 = 4.1 - row * 1.65
            ax.add_patch(FancyBboxPatch((x0, y0 - 0.55), 5.0, 1.28, boxstyle="round,pad=0.1",
                                         facecolor=f'{col}18', edgecolor=col, linewidth=2))
            ax.add_patch(Circle((x0 + 0.42, y0 + 0.08), 0.28, facecolor=col, edgecolor=W, linewidth=2))
            ax.text(x0 + 0.42, y0 + 0.08, str(i + 1), fontsize=10, fontweight='black',
                    ha='center', va='center', color=W)
            ax.text(x0 + 0.92, y0 + 0.2,  title, fontsize=11, fontweight='black',
                    ha='left', va='center', color=col)
            ax.text(x0 + 0.92, y0 - 0.22, desc,  fontsize=9, ha='left', va='center', color='#555')
        ax.set_xlim(0, 11.5); ax.set_ylim(-0.3, 5.2)
        ax.set_title('테마주 투자 6대 원칙', fontsize=17, fontweight='black', color=DARK, pad=14)
    elif variant == 1:
        BG_D = '#0f172a'
        fig, ax = plt.subplots(figsize=(10, 6.2), facecolor=BG_D)
        ax.set_facecolor(BG_D); ax.axis('off')
        for i, (title, desc) in enumerate(PRINCIPLES):
            col = colors[i]
            row, col_idx = divmod(i, 2)
            x0 = col_idx * 5.5 + 0.3; y0 = 4.1 - row * 1.65
            ax.add_patch(FancyBboxPatch((x0, y0 - 0.55), 5.0, 1.28, boxstyle="round,pad=0.08",
                                         facecolor='#1e293b', edgecolor=col, linewidth=2.5))
            ax.add_patch(FancyBboxPatch((x0, y0 - 0.55), 0.22, 1.28, boxstyle="round,pad=0.0",
                                         facecolor=col, edgecolor='none'))
            ax.text(x0 + 0.11, y0 + 0.08, str(i + 1), fontsize=9, fontweight='black',
                    ha='center', va='center', color=W)
            ax.text(x0 + 0.42, y0 + 0.2,  title, fontsize=11, fontweight='black',
                    ha='left', va='center', color=col)
            ax.text(x0 + 0.42, y0 - 0.22, desc,  fontsize=9, ha='left', va='center', color='#94a3b8')
        ax.set_xlim(0, 11.5); ax.set_ylim(-0.3, 5.2)
        ax.set_title('테마주 투자 6대 원칙', fontsize=17, fontweight='black', color='#f1f5f9', pad=14)
        fig.set_facecolor(BG_D)
    else:
        fig, ax = plt.subplots(figsize=(10, 6.0), facecolor=W)
        ax.set_facecolor(W); ax.axis('off')
        for i, (title, desc) in enumerate(PRINCIPLES):
            col = colors[i]; y0 = 5.2 - i * 0.88
            ax.add_patch(FancyBboxPatch((0.3, y0 - 0.3), 10.0, 0.76, boxstyle="round,pad=0.05",
                                         facecolor=f'{col}10', edgecolor='none'))
            ax.add_patch(plt.Rectangle((0.3, y0 - 0.3), 0.12, 0.76, facecolor=col, edgecolor='none'))
            ax.add_patch(Circle((0.72, y0 + 0.08), 0.22, facecolor=col, edgecolor=W, linewidth=1.5))
            ax.text(0.72, y0 + 0.08, str(i + 1), fontsize=9, fontweight='black',
                    ha='center', va='center', color=W)
            ax.text(1.05, y0 + 0.18, title, fontsize=12, fontweight='black',
                    ha='left', va='center', color=col)
            ax.text(1.05, y0 - 0.14, desc,  fontsize=9.5, ha='left', va='center', color='#444')
        ax.set_xlim(0, 10.8); ax.set_ylim(0, 6.0)
        ax.set_title('테마주 투자 6대 원칙', fontsize=17, fontweight='black', color=DARK, pad=12)

    plt.tight_layout()
    return wrap_img(fig_to_b64(fig), '투자 원칙', '')


def make_stock_chart(yf_ticker, name) -> str:
    try:
        df = _j09_hist(yf_ticker, period="3mo")
        if df.empty or len(df) < 5:
            alt_tickers = []
            if yf_ticker.endswith('.KQ'):
                alt_tickers = [yf_ticker.replace('.KQ', '.KS'), yf_ticker.split('.')[0]]
            elif yf_ticker.endswith('.KS'):
                alt_tickers = [yf_ticker.replace('.KS', '.KQ'), yf_ticker.split('.')[0]]
            for alt in alt_tickers:
                df = _j09_hist(alt, period="3mo")
                if not df.empty and len(df) >= 5:
                    break
            else:
                log.warning(f"[StockChart] {name}: 주가 데이터 없음 ({yf_ticker})")
                return ''

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 4.5),
                                        gridspec_kw={'height_ratios': [3, 1]}, facecolor='#0d1117')
        fig.subplots_adjust(hspace=0.04)
        close, volume, dates = df['Close'], df['Volume'], df.index
        color = '#00d4aa' if close.iloc[-1] >= close.iloc[0] else '#ff6b6b'
        ax1.set_facecolor('#161b22')
        ax1.plot(dates, close, color=color, linewidth=2.2, zorder=3)
        ax1.fill_between(dates, close, close.min() * 0.995, alpha=0.18, color=color, zorder=2)
        ax1.set_ylabel('주가 (원)', color='#8b949e', fontsize=9)
        ax1.tick_params(colors='#8b949e', labelsize=8)
        for s in ax1.spines.values():
            s.set_visible(False)
        ax1.set_xticklabels([])
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x):,}'))
        ax1.grid(axis='y', color='#21262d', linewidth=0.7, zorder=1)
        current = close.iloc[-1]
        chg = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100 if len(close) > 1 else 0
        setup_chart_defaults(_FONT_PATH)
        ax1.set_title(f'{name}   W{current:,.0f}', color=W,
                      fontsize=CHART_STYLE["FONT_LABEL"],
                      fontweight='bold', pad=12, loc='left', x=0.02)
        ax1.annotate(f'{"+" if chg >= 0 else ""}{chg:.2f}%', xy=(0.98, 0.90),
                     xycoords='axes fraction',
                     color='#00d4aa' if chg >= 0 else '#ff6b6b',
                     fontsize=11, fontweight='bold', ha='right')
        ax2.set_facecolor('#161b22')
        vcols = ['#00d4aa' if c >= o else '#ff6b6b' for c, o in zip(df['Close'], df['Open'])]
        ax2.bar(dates, volume, color=vcols, width=0.8, alpha=0.8)
        ax2.set_ylabel('거래량', color='#8b949e', fontsize=8)
        ax2.tick_params(colors='#8b949e', labelsize=7)
        for s in ax2.spines.values():
            s.set_visible(False)
        ax2.grid(axis='y', color='#21262d', linewidth=0.5)
        step = max(len(dates) // 4, 1)
        ax2.set_xticks(dates[::step])
        ax2.set_xticklabels([d.strftime('%m/%d') for d in dates[::step]],
                            color='#8b949e', fontsize=7)
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight',
                    facecolor='#0d1117', edgecolor='none')
        plt.close(fig); buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('utf-8')
        html = (f'<div style="background:linear-gradient(135deg,#0d1117,#161b22);padding:20px;'
                f'border-radius:16px;border:1px solid #30363d;margin:20px 0;'
                f'box-shadow:0 4px 24px rgba(0,0,0,0.4);">'
                f'<img src="data:image/png;base64,{b64}" '
                f'style="width:100%;max-width:720px;display:block;margin:0 auto;border-radius:8px;" '
                f'alt="{name} 주가차트"/></div>')
        CHART_STORE[name] = html
        return f"{{CHART:{name}}}"
    except Exception as e:
        log.error(f"[StockChart] {name} 오류: {e}")
        _g_report("image", e, module=__name__)
        return ''


def make_leader_price_chart_from_data(rows: list, name: str, period: str = "") -> str:
    """JARVIS09 분기별 주가 이력 rows → 주가 차트 HTML.

    rows: [{"label": "2021.Q1", "value": 80000}, ...] (stocks_to_datasets 출력)
    실데이터 없으면 '' (ADR 010). draft_processor._inject_leader_price_charts 주 경로.
    """
    if not rows or len(rows) < 4:
        return ''
    try:
        import pandas as pd
        setup_chart_defaults(_FONT_PATH)

        labels = [r["label"] for r in rows]
        values = [float(r["value"]) for r in rows]
        # 라벨 형식: "2021.01" (월별) — "YYYY.MM-01" 로 파싱
        dates  = pd.to_datetime(
            [f"{lb.replace('.', '-')}-01" for lb in labels], errors="coerce"
        )

        is_up = values[-1] >= values[0]
        color = '#00d4aa' if is_up else '#ff6b6b'
        period_label = f"최근 {period}" if period else ""

        fig, ax1 = plt.subplots(figsize=(10, 4.5), facecolor='#0d1117')
        ax1.set_facecolor('#161b22')
        ax1.plot(dates, values, color=color, linewidth=2.2, marker='o',
                 markersize=4, zorder=3)
        ax1.fill_between(dates, values, min(values) * 0.99,
                         alpha=0.18, color=color, zorder=2)
        ax1.set_ylabel('주가 (원)', color='#8b949e', fontsize=9)
        ax1.tick_params(colors='#8b949e', labelsize=8)
        for s in ax1.spines.values():
            s.set_visible(False)
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x):,}'))
        ax1.grid(axis='y', color='#21262d', linewidth=0.7, zorder=1)

        chg = (values[-1] - values[0]) / values[0] * 100 if values[0] else 0
        title_str = f'{name}   ₩{values[-1]:,.0f}'
        if period_label:
            title_str += f'   [{period_label}]'
        ax1.set_title(title_str, color='#e6edf3',
                      fontsize=CHART_STYLE["FONT_LABEL"],
                      fontweight='bold', pad=12, loc='left', x=0.02)
        ax1.annotate(f'{"+" if chg >= 0 else ""}{chg:.1f}%',
                     xy=(0.98, 0.90), xycoords='axes fraction',
                     color='#00d4aa' if chg >= 0 else '#ff6b6b',
                     fontsize=11, fontweight='bold', ha='right')

        # 월별 60개 포인트 → 12개월(1년) 단위로 x축 표시
        step = max(len(dates) // 6, 1)
        ax1.set_xticks(dates[::step])
        # 라벨: "2021.01" → "2021년" (연도만 표시, 겹침 방지)
        ax1.set_xticklabels(
            [lb[:4] + "년" for lb in labels[::step]],
            color='#8b949e', fontsize=7, rotation=0,
        )
        fig.tight_layout(pad=1.2)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight',
                    facecolor='#0d1117', edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('utf-8')
        return (
            f'<div style="background:linear-gradient(135deg,#0d1117,#161b22);'
            f'padding:20px;border-radius:16px;border:1px solid #30363d;'
            f'margin:20px 0;box-shadow:0 4px 24px rgba(0,0,0,0.4);">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;max-width:760px;display:block;margin:0 auto;border-radius:8px;" '
            f'alt="{name} 주가 흐름"/></div>'
        )
    except Exception as e:
        log.error(f"[LeaderChartData] {name} 오류: {e}")
        _g_report("image", e, module=__name__)
        return ''


def make_leader_price_chart(yf_ticker: str, name: str) -> str:
    """대장주·부대장주 전용 주가 차트 HTML (최대 5년, 있는 만큼 사용).

    직접 HTML 반환 (CHART_STORE 미사용). 실데이터 없으면 '' (ADR 010).
    수집 데이터 없을 때의 폴백 경로 — 주 경로는 make_leader_price_chart_from_data().
    """
    # alt 티커 목록 (KQ↔KS 폴백)
    alt_tickers = [yf_ticker]
    if yf_ticker.endswith('.KQ'):
        alt_tickers += [yf_ticker.replace('.KQ', '.KS'), yf_ticker.split('.')[0]]
    elif yf_ticker.endswith('.KS'):
        alt_tickers += [yf_ticker.replace('.KS', '.KQ'), yf_ticker.split('.')[0]]

    # 최신 시점 기준 최대 5년 — yfinance가 상장 이후 있는 만큼만 반환
    import pandas as pd
    df = pd.DataFrame()
    for ticker in alt_tickers:
        df = _j09_hist(ticker, period="5y")
        if not df.empty and len(df) >= 6:
            break

    if df.empty:
        log.warning(f"[LeaderChart] {name}: 주가 데이터 없음 ({yf_ticker})")
        return ''

    try:
        setup_chart_defaults(_FONT_PATH)

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5),
                                        gridspec_kw={'height_ratios': [3, 1]},
                                        facecolor='#0d1117')
        fig.subplots_adjust(hspace=0.04)
        close, volume, dates = df['Close'], df['Volume'], df.index

        color = '#00d4aa' if close.iloc[-1] >= close.iloc[0] else '#ff6b6b'

        # 상단: 주가 라인
        ax1.set_facecolor('#161b22')
        ax1.plot(dates, close, color=color, linewidth=2.0, zorder=3)
        ax1.fill_between(dates, close, close.min() * 0.99, alpha=0.18, color=color, zorder=2)
        ax1.set_ylabel('주가 (원)', color='#8b949e', fontsize=9)
        ax1.tick_params(colors='#8b949e', labelsize=8)
        for s in ax1.spines.values():
            s.set_visible(False)
        ax1.set_xticklabels([])
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x):,}'))
        ax1.grid(axis='y', color='#21262d', linewidth=0.7, zorder=1)

        current = close.iloc[-1]
        chg = (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100 if len(close) > 1 else 0
        # 실제 기간 계산
        n_months = max(round((dates[-1] - dates[0]).days / 30), 1)
        n_years, n_rem = divmod(n_months, 12)
        if n_years >= 1 and n_rem > 0:
            period_label = f"최근 {n_years}년 {n_rem}개월"
        elif n_years >= 1:
            period_label = f"최근 {n_years}년"
        else:
            period_label = f"최근 {n_months}개월"

        ax1.set_title(f'{name}   ₩{current:,.0f}   [{period_label}]',
                      color='#e6edf3',
                      fontsize=CHART_STYLE["FONT_LABEL"],
                      fontweight='bold', pad=12, loc='left', x=0.02)
        ax1.annotate(f'{"+" if chg >= 0 else ""}{chg:.2f}%',
                     xy=(0.98, 0.90), xycoords='axes fraction',
                     color='#00d4aa' if chg >= 0 else '#ff6b6b',
                     fontsize=11, fontweight='bold', ha='right')

        # 하단: 거래량
        ax2.set_facecolor('#161b22')
        vcols = ['#00d4aa' if c >= o else '#ff6b6b'
                 for c, o in zip(df['Close'], df['Open'])]
        ax2.bar(dates, volume, color=vcols, width=0.8, alpha=0.8)
        ax2.set_ylabel('거래량', color='#8b949e', fontsize=8)
        ax2.tick_params(colors='#8b949e', labelsize=7)
        for s in ax2.spines.values():
            s.set_visible(False)
        ax2.grid(axis='y', color='#21262d', linewidth=0.5)

        # x축: 1년 이상이면 연도, 1년 미만이면 연월
        step = max(len(dates) // 5, 1)
        date_fmt = '%Y' if n_years >= 1 else '%Y/%m'
        ax2.set_xticks(dates[::step])
        ax2.set_xticklabels([d.strftime(date_fmt) for d in dates[::step]],
                            color='#8b949e', fontsize=7)

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=140, bbox_inches='tight',
                    facecolor='#0d1117', edgecolor='none')
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('utf-8')

        return (
            f'<div style="background:linear-gradient(135deg,#0d1117,#161b22);'
            f'padding:20px;border-radius:16px;border:1px solid #30363d;'
            f'margin:20px 0;box-shadow:0 4px 24px rgba(0,0,0,0.4);">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;max-width:760px;display:block;margin:0 auto;border-radius:8px;" '
            f'alt="{name} 주가차트 ({period_label})"/></div>'
        )
    except Exception as e:
        log.error(f"[LeaderChart] {name} 오류: {e}")
        _g_report("image", e, module=__name__)
        return ''


__all__ = [
    "_cap", "set_font", "fig_to_b64", "wrap_img", "CHART_STORE",
    "make_theme_overview_chart", "make_investment_radar_chart",
    "make_theme_factors_chart", "make_investment_timeline_chart",
    "make_theme_mechanism_chart", "make_theme_applications_chart",
    "make_theme_timeline_chart", "make_theme_concept_chart",
    "make_terms_chart", "make_profit_donut", "make_cap_bar", "make_per_bar",
    "make_profitability_chart", "make_revenue_chart", "make_theme_return_chart",
    "make_risk_chart", "make_portfolio_chart", "make_checklist_chart",
    "make_stock_chart", "make_leader_price_chart", "make_leader_price_chart_from_data",
]
