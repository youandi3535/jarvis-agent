"""JARVIS06_IMAGE/theme_charts.py — 테마주 차트·인포그래픽 생성 (collect_theme에서 이관)."""
from __future__ import annotations
# ★ yfinance 단일 진입점 → JARVIS09 (2026-05-31 이관)
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_ticker_history as _j09_hist,
    download_ticker as _j09_dl,
)
import io, base64, os, logging
import matplotlib
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

matplotlib.use('Agg')
import matplotlib.pyplot as plt

# ★ 차트 스타일 단일 진입점 (style_engine.py)
from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE

log = logging.getLogger("jarvis")

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

_CAP_DESC = {
    'overview':      '전체 투자 포인트 요약 인포그래픽',
    'radar':         '5개 지표 레이더 차트',
    'factors':       '상승·하락 요인 분석',
    'timeline':      '투자 단계별 체크리스트',
    'mechanism':     '테마 작동 구조 도식',
    'usecase':       '주요 활용 분야',
    'history':       '발전 역사 타임라인',
    'keyword':       '핵심 키워드 모음',
    'terms':         '핵심 투자 용어 3가지',
    'profit_loss':   '흑자/적자 종목 현황',
    'mktcap':        '시가총액 비교',
    'per':           'PER 밸류에이션 비교',
    'profitability': '수익성 지표 비교',
    'revenue':       '매출·순이익 비교',
    'return3m':      '3개월 수익률 비교',
    'risk':          '종목별 투자 위험도',
    'portfolio':     '포트폴리오 전략',
    'principle':     '투자 원칙',
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

        step = max(len(dates) // 6, 1)
        ax1.set_xticks(dates[::step])
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
    alt_tickers = [yf_ticker]
    if yf_ticker.endswith('.KQ'):
        alt_tickers += [yf_ticker.replace('.KQ', '.KS'), yf_ticker.split('.')[0]]
    elif yf_ticker.endswith('.KS'):
        alt_tickers += [yf_ticker.replace('.KS', '.KQ'), yf_ticker.split('.')[0]]

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

        ax2.set_facecolor('#161b22')
        vcols = ['#00d4aa' if c >= o else '#ff6b6b'
                 for c, o in zip(df['Close'], df['Open'])]
        ax2.bar(dates, volume, color=vcols, width=0.8, alpha=0.8)
        ax2.set_ylabel('거래량', color='#8b949e', fontsize=8)
        ax2.tick_params(colors='#8b949e', labelsize=7)
        for s in ax2.spines.values():
            s.set_visible(False)
        ax2.grid(axis='y', color='#21262d', linewidth=0.5)

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
    "make_leader_price_chart", "make_leader_price_chart_from_data",
]
