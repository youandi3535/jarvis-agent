"""JARVIS06_IMAGE/economic_charts.py — 경제 브리핑 이미지 생성 단일 진입점.

★ 2026-06-01 전면 교체: matplotlib 구버전 전부 삭제.
  - 썸네일    → image_agent.generate_thumbnail (AI 사진 + 텍스트 오버레이)
  - 필러 차트 → chart_generator.generate_chart (Plotly + LLM 동적 스타일)
  - 인사이트 카드 → section_title.make_section_title_image (소제목 배너)
  - 테이블 이미지 → matplotlib (표 레이아웃은 mpl이 적합, 그대로 유지)
"""
from __future__ import annotations
import logging
import textwrap
from datetime import datetime
from pathlib import Path

# ★ yfinance 단일 진입점 → JARVIS09
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_ticker_history as _j09_hist,
)

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

log = logging.getLogger("jarvis")

_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
_DOW_KR = ['월', '화', '수', '목', '금', '토', '일']


def _now():
    return datetime.now()


def _today_str():
    return _now().strftime("%Y년 %m월 %d일")


def _today_dow():
    return _DOW_KR[_now().weekday()]


def _out(out_dir) -> Path:
    p = Path(out_dir) if out_dir else _OUTPUT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mpl_setup():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.family'] = ['AppleGothic', 'Apple SD Gothic Neo', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False


# ══════════════════════════════════════════════════════════════
# 1. 썸네일 — AI 사진 + 텍스트 오버레이 (테마주 동일 방식)
# ══════════════════════════════════════════════════════════════

def generate_thumbnail(market: dict, out_dir=None, body_text: str = "") -> str:
    """경제 브리핑 썸네일 — image_agent.generate_thumbnail (AI 사진 기반)."""
    from JARVIS06_IMAGE.image_agent import generate_thumbnail as _gen
    today = _today_str()
    dow   = _today_dow()
    title = f"경제 브리핑 {today} ({dow})"
    dest  = _out(out_dir)
    return _gen(title=title, keyword="경제 브리핑", platform="naver", out_dir=str(dest))


# ══════════════════════════════════════════════════════════════
# 2. 필러 차트 — Plotly chart_generator (테마주 동일 방식)
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
# 3. 인사이트 카드 — section_title 소제목 배너 (테마주 동일 방식)
# ══════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════
# 4. 테이블 이미지 — matplotlib (표 레이아웃은 mpl 유지)
# ══════════════════════════════════════════════════════════════

def render_html_table_as_image(table_html: str, idx: int, out_dir=None):
    """HTML 테이블 하나를 PNG 이미지로 렌더링 — 동적 스타일 적용."""
    import re as _re2
    from bs4 import BeautifulSoup
    from JARVIS06_IMAGE.style_engine import generate_style_spec, _interpolate_color

    style_spec   = generate_style_spec("factors", f"경제_테이블_{idx}")
    header_color = style_spec.get("primary_color", "#1565c0")
    accent_color = style_spec.get("accent_color", "#0891b2")

    _mpl_setup()
    import matplotlib.pyplot as plt

    soup = BeautifulSoup(table_html, 'html.parser')
    all_rows_el = soup.find_all('tr')
    if not all_rows_el:
        return None

    def cell_text(el):
        text = el.get_text(separator=' ', strip=True)
        text = text.replace('⭐', '★').replace('\U0001F31F', '★')
        text = _re2.sub(r'[\U0001F000-\U0001FFFF]', '', text)
        text = _re2.sub(r'[\U00002702-\U000027B0]', '', text)
        return text.strip()

    def cell_color(el):
        import re as _re
        style = el.get('style', '')
        m = _re.search(r'color\s*:\s*(#[0-9a-fA-F]{3,6})', style)
        return m.group(1) if m else None

    first_row = all_rows_el[0]
    if first_row.find('th'):
        headers = [cell_text(c) for c in first_row.find_all(['th', 'td'])]
        data_rows_el = all_rows_el[1:]
    else:
        headers = [cell_text(c) for c in first_row.find_all('td')]
        data_rows_el = all_rows_el[1:]

    rows = []
    cell_colors_map = {}
    for ri, tr in enumerate(data_rows_el):
        cells = tr.find_all(['td', 'th'])
        row = [cell_text(c) for c in cells]
        if not any(row):
            continue
        for ci, c in enumerate(cells):
            col = cell_color(c)
            if col:
                cell_colors_map[(ri, ci)] = col
            txt = cell_text(c)
            if '▲' in txt or ('+' in txt and '%' in txt):
                cell_colors_map[(ri, ci)] = accent_color
            elif '▼' in txt or (len(txt) > 1 and txt.startswith('-') and '%' in txt):
                cell_colors_map[(ri, ci)] = _interpolate_color(header_color, accent_color, 0.5)
        rows.append(row)

    if not rows:
        return None

    n_cols = max(len(headers), max(len(r) for r in rows))
    headers = (headers + [''] * n_cols)[:n_cols]
    rows = [(r + [''] * n_cols)[:n_cols] for r in rows]

    WRAP_WIDTH = 18

    def wrap_cell(text):
        if len(text) <= WRAP_WIDTH:
            return text
        return '\n'.join(textwrap.wrap(text, width=WRAP_WIDTH))

    headers_wrapped = [wrap_cell(h) for h in headers]
    rows_wrapped    = [[wrap_cell(c) for c in r] for r in rows]

    def max_lines(row):
        return max((c.count('\n') + 1) for c in row) if row else 1

    row_line_counts = [max_lines(r) for r in rows_wrapped]
    header_lines    = max_lines(headers_wrapped)

    ROW_H = 0.55
    fig_h = (sum(row_line_counts) + header_lines) * ROW_H + 0.8
    fig_w = max(12, n_cols * 3.2)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis('off')

    row_colors = [
        [_interpolate_color(accent_color, 'ffffff', 0.3) if i % 2 == 0 else 'white'] * n_cols
        for i in range(len(rows_wrapped))
    ]

    tbl = ax.table(
        cellText=rows_wrapped,
        colLabels=headers_wrapped,
        cellLoc='center',
        loc='center',
        cellColours=row_colors,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)

    n_total_rows = len(rows_wrapped) + 1
    for row_i in range(n_total_rows):
        lines = header_lines if row_i == 0 else row_line_counts[row_i - 1]
        h = (ROW_H * lines) / fig_h
        for col_j in range(n_cols):
            tbl[(row_i, col_j)].set_height(h)

    for j in range(n_cols):
        tbl[(0, j)].set_facecolor(header_color)
        tbl[(0, j)].set_text_props(color='white', fontweight='bold')

    for (ri, ci), color in cell_colors_map.items():
        if ri < len(rows_wrapped):
            tbl[(ri + 1, ci)].set_text_props(color=color, fontweight='bold')

    plt.tight_layout()
    path = str(_out(out_dir) / f'economic_table_{idx}.png')
    plt.savefig(path, dpi=250, bbox_inches='tight', facecolor='white')
    plt.close()
    log.info(f"[EcoCharts] 테이블 이미지 [{idx}]: {path}")
    return path



__all__ = [
    "generate_thumbnail",
    "generate_filler_image",
    "generate_insight_card",
    "render_html_table_as_image",
    "render_market_table",
    "render_calendar_table",
]
