"""JARVIS06_IMAGE/theme_charts.py — 테마주 차트·인포그래픽 생성 (collect_theme에서 이관)."""
from __future__ import annotations
import io, os, logging
from pathlib import Path
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
            "writer_short_visual",
            f"{theme_ctx}블로그 차트 캡션 1문장. 차트: {desc}.{extra} 25자 이내. 해요체. 문장만 출력.",
            max_tokens=40, temperature=0.8
        ) or f"{theme_ctx}{_CAP_DESC.get(key, key)}"
    except Exception:
        return f"{t} {_CAP_DESC.get(key, key)}"


def set_font() -> None:
    """★ Deprecated — setup_chart_defaults() 사용 권장."""
    setup_chart_defaults(_FONT_PATH)


# ══════════════════════════════════════════════════════════════
#  주가 차트 — ★ 픽셀을 낳는 유일한 반환 지점 (사용자 박제 2026-08-10)
#
#  왜 *한 벌* 만 남았는가 (①단일 진입점 · ③4조합 대칭):
#    ┌ 2026-08-10 1차: `make_leader_price_chart_from_data`(수집 dataset) 와
#    │ `make_leader_price_chart`(J09 시세를 이 파일이 직접 받아 조립) 가 *같은 일*
#    │ (matplotlib 주가 PNG → 본문 이미지)을 **두 벌** 구현하고 있었다. 픽셀 반환만
#    │ `_emit_price_chart` 로 모았을 뿐, 데이터 출처가 둘이라 사본은 그대로 남았다.
#    └ 2026-08-10 2차: 뒤의 한 벌을 **삭제**했다. 이유는 게이트가 아니라 *구조* 다 —
#      그 경로는 대조할 dataset 이 **원리적으로 존재하지 않는다**. 시세를 이 파일이
#      받아 그리고, 그린 값을 그대로 "내가 그린 값이 맞다" 고 제출하는 자기증명이라
#      `certify_image(code_drawn=True, datasets=None)` 의 `code_drawn:unaudited` 로
#      빠져나갔다. 경제 인포그래픽은 전부 grounding 대조를 받는데 **테마 2조합의
#      주가 차트만 무감사** 였던 ③원칙 비대칭이 바로 이 사본에서 나왔다.
#      게이트를 하나 더 다는 것은 답이 아니다(사본이 남으면 언제든 출구가 된다).
#    ★ 덤: 그 사본은 alt 티커 순회·5년 조회·기간 문자열 조립까지 J09
#      `stocks_to_datasets` 의 대장주 이력 블록과 같은 일을 하고 있었다 —
#      *수집 조립을 이미지 도메인이 한 벌 더* 갖고 있던 셈(CLAUDE.md 수집 단일 진입점).
#      그리고 사본답게 한쪽만 어긋나 있었다: 같은 제목 템플릿을 쓰면서 변동률을
#      전기간(`[-1]-[0]`)이 아니라 **전일 대비**(`[-1]-[-2]`)로 인쇄했다.
#
#  base64 인라인 분기 폐지: data URI 이미지는 *경로가 없어* provenance 등록도,
#    prepublish_gate 의 경로 조회도 원리적으로 불가능하다 — 검증 밖으로 나가는 문이다.
#    저장 경로를 안 받으면 이미지 도메인 기본 출력 폴더에서 *파생* 한다(②).
# ══════════════════════════════════════════════════════════════


def _default_price_path(name: str, tag: str) -> Path:
    """저장 경로 미지정 시 이미지 도메인 출력 폴더에서 파생 (경로 리터럴 금지)."""
    import hashlib
    from JARVIS06_IMAGE.image_agent import OUTPUT_DIR
    d = Path(OUTPUT_DIR) / "images" / "theme_price"
    d.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(f"{name}|{tag}".encode()).hexdigest()[:10]
    return d / f"price_{h}.png"


def _emit_price_chart(fig, *, name: str, alt: str, datasets: list,
                      printed_rows: list, out_path=None,
                      engine: str = "theme_price") -> str:
    """완성된 figure → PNG 저장 → **대조 인증** → 본문 <img> 블록. 미인증이면 "".

    ★ `datasets` 는 선택 인자가 아니다 (사용자 박제 2026-08-10 — ③원칙):
      비어 있으면 `certify_image` 가 대조 없이 `code_drawn:unaudited` 로 통과시킨다.
      그 통과는 "검증했다" 가 아니라 "검증할 것이 없었다" 는 뜻인데, 호출자는
      `verified is True` 만 보므로 구분이 사라진다. 그래서 여기서 **먼저 막는다** —
      대조군 없는 주가 차트는 만들지 않는다.
    ★ `printed_rows` 는 *그림에 글자로 인쇄되는 수치 전부* 다. 꺾은선 점만 넘기면
      제목의 현재가·변동률이 감사 밖에 남는다 — 2026-08-10 환율 사고(실제 -8.7% 를
      +8.6% 로 인쇄)가 정확히 '인쇄되는 파생값' 계층에서 났다. 인쇄하는 함수
      (`_price_title`)가 자기가 인쇄한 값을 돌려주게 해서 사본을 만들지 않는다(①).
    ★ 실패를 삼키지 않는다: 등록 실패는 '검증 안 함' 과 같은 말이다 — 드러내고 버린다.
    ★ <img> 마크업은 만들지 않고 `data_image_html` 에 위임한다 (①·표식 배선):
      그 함수가 수치 이미지 표식(`DATA_IMAGE_ATTR`)을 붙이는 **단일 생산자** 다.
    """
    if not datasets:
        log.error(f"[LeaderChart] {name} 대조군(datasets) 없음 → 차트 생성 안 함 "
                  f"(무감사 통과 경로 폐쇄 — ADR 010: 거짓 차트 < 차트 없음)")
        plt.close(fig)
        return ''

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=140, bbox_inches='tight',
                facecolor='#0d1117', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    _p = Path(out_path) if out_path is not None else _default_price_path(name, alt)
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_bytes(buf.read())

    try:
        from JARVIS06_IMAGE.infographic_engine import emit_certified, data_image_html
        # ★ 인증은 이미지 도메인 초크포인트 한 문으로만 (①): 종전엔 이 함수가
        #   `certify_image` 를 직접 불러 *자기 인증* 을 한 벌 더 갖고 있었고, 그
        #   한 벌만 datasets 를 안 넘겨 `code_drawn:unaudited` 로 통과했다.
        got = emit_certified(_p, engine=engine,
                             datasets=list(datasets),      # 대조군: J09 원본 dataset
                             rows=list(printed_rows),      # 그림이 인쇄하는 수치 전부
                             code_drawn=True)              # matplotlib PNG — LLM 글자 0
    except Exception as e:
        log.error(f"[LeaderChart] {name} 인증 실패 → 이미지 폐기: {e}")
        _g_report("image", e, module=__name__, func_name="_emit_price_chart")
        return ''
    if not got:      # 미검증 사유는 초크포인트가 이미 로그에 남긴다
        log.warning(f"[LeaderChart] {name} 검증 미통과 → 이미지 폐기")
        return ''
    return data_image_html(got, alt)


def _price_axes():
    """주가 패널 공통 축 골격 — 스타일 사본 방지."""
    fig, ax1 = plt.subplots(figsize=(10, 4.5), facecolor='#0d1117')
    ax1.set_facecolor('#161b22')
    ax1.set_ylabel('주가 (원)', color='#8b949e', fontsize=CHART_STYLE["FONT_PANEL_NOTE"])
    ax1.tick_params(colors='#8b949e', labelsize=8)
    for s in ax1.spines.values():
        s.set_visible(False)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{int(x):,}'))
    ax1.grid(axis='y', color='#21262d', linewidth=0.7, zorder=1)
    return fig, ax1


def _price_title(ax1, name: str, last: float, period_label: str, chg: float) -> list[dict]:
    """제목·변동률 배지를 인쇄하고 **인쇄한 수치를 돌려준다**.

    ★ 반환이 인증 재료다 (사용자 박제 2026-08-10): '무엇을 인쇄했는가' 를 인쇄하는
      곳에서만 파생한다. 호출자가 따로 목록을 만들면 그것이 사본이고, 사본은
      제목 포맷이 바뀔 때 한쪽만 고쳐져 감사 밖 수치를 남긴다.
    """
    title_str = f'{name}   ₩{last:,.0f}'
    if period_label:
        title_str += f'   [{period_label}]'
    ax1.set_title(title_str, color='#e6edf3',
                  fontsize=CHART_STYLE["FONT_LABEL"],
                  fontweight='bold', pad=12, loc='left', x=0.02)
    ax1.annotate(f'{"+" if chg >= 0 else ""}{chg:.1f}%',
                 xy=(0.98, 0.90), xycoords='axes fraction',
                 color='#00d4aa' if chg >= 0 else '#ff6b6b',
                 fontsize=CHART_STYLE["FONT_PANEL_ACCENT"], fontweight='bold', ha='right')
    return [{"label": f"{name} 현재가", "value": float(last), "unit": "원"},
            {"label": f"{name} 변동률", "value": float(chg), "unit": "%"}]


def make_leader_price_chart(ds: dict, out_path=None) -> str:
    """대장주·부대장주 주가 이력 dataset → **인증된** 주가 차트 HTML. 실패 시 ''.

    입력은 JARVIS09 `stocks_to_datasets` 가 출처(provenance)까지 박아 낸
    `viz_hint="stock_price"` dataset 한 건 — 이 파일은 수집하지 않는다(CLAUDE.md
    수집 단일 진입점). 그 dataset 이 그대로 **대조군** 이 되므로, 그리면서 생긴
    수치(현재가·변동률)가 원본에서 파생되지 않으면 이미지가 폐기된다.
    """
    rows = list((ds or {}).get("data") or [])
    name = str((ds or {}).get("name") or (ds or {}).get("title") or "")
    period = str((ds or {}).get("period") or "")
    if len(rows) < 4:
        return ''
    try:
        import pandas as pd
        setup_chart_defaults(_FONT_PATH)

        labels = [r["label"] for r in rows]
        values = [float(r["value"]) for r in rows]
        dates  = pd.to_datetime(
            [f"{lb.replace('.', '-')}-01" for lb in labels], errors="coerce"
        )

        color = '#00d4aa' if values[-1] >= values[0] else '#ff6b6b'
        fig, ax1 = _price_axes()
        ax1.plot(dates, values, color=color, linewidth=2.2, marker='o',
                 markersize=4, zorder=3)
        ax1.fill_between(dates, values, min(values) * 0.99,
                         alpha=0.18, color=color, zorder=2)

        chg = (values[-1] - values[0]) / values[0] * 100 if values[0] else 0
        printed = _price_title(ax1, name, values[-1],
                               f"최근 {period}" if period else "", chg)

        step = max(len(dates) // 6, 1)
        ax1.set_xticks(dates[::step])
        ax1.set_xticklabels([lb[:4] + "년" for lb in labels[::step]],
                            color='#8b949e', fontsize=CHART_STYLE["FONT_PANEL_MICRO_SM"], rotation=0)
        fig.tight_layout(pad=1.2)

        _alt = f"{name} 주가 {('최근 ' + period) if period else ''}".strip()
        # 인증 재료 = 꺾은선이 찍는 점 + 제목이 인쇄한 파생값 (그림에 글자로 나가는 전부)
        _series = [{"label": str(lb), "value": float(v), "unit": "원"}
                   for lb, v in zip(labels, values)]
        return _emit_price_chart(fig, name=name, alt=_alt,
                                 datasets=[ds], printed_rows=_series + printed,
                                 out_path=out_path)
    except Exception as e:
        log.error(f"[LeaderChart] {name} 오류: {e}")
        _g_report("image", e, module=__name__)
        return ''


__all__ = [
    "_cap", "set_font", "CHART_STORE", "make_leader_price_chart",
]
