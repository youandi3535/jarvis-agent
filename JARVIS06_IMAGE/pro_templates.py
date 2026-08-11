"""JARVIS06_IMAGE/pro_templates.py — 전문 디자이너급 인포그래픽 (결정론 템플릿, LLM 0회).

★ 사용자 박제 2026-07-05 (ERRORS [358]): design-gen(LLM 실시간 HTML 저작)은 SDK 스로틀 시
  이미지당 수 분 latency → 폐기(opt-in). 대신 *전문 디자인을 코드 템플릿에 박제* 하고 검증된
  실데이터만 꽂아 즉시(2~6초) 렌더. 수치는 코드가 실데이터로 채움 → 조작 원천 불가.

품질 요소(모든 템플릿 공통):
  - 딥컬러 히어로 밴드 + 그라디언트/도트 텍스처 + 아이브로우 칩
  - 디스플레이급 초대형 히어로 스탯(값+증감) + 스파크라인
  - 그라디언트 area·듀오톤 라인·값 배지·끝점 강조·주석 등 데이터-잉크
  - 번호칩·인라인 SVG 아이콘·구분선·출처 푸터 — 편집 완성도
  - 팔레트 5종 seed 회전 → 글마다 다른 무드

진입점: render_pro(title, subtitle, datasets, seed, out_path, src) -> (path, html) | ("","")
데이터 계약: datasets = [{"title","unit","data":[{"label","value"}],"viz_hint"?,"source"?}]
"""
from __future__ import annotations
import re
from pathlib import Path

from shared.numeric import safe_float

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

FONT = "'Noto Sans KR',sans-serif"

# ── 전문 팔레트 (seed 회전) ────────────────────────────────────────────────
PALETTES = [
    {"hero": ("#0a1730", "#16345f"), "ink": "#0f1b33", "a1": "#f5b829", "a1s": "#ffce54",
     "a2": "#22d3c5", "a2s": "#37d6cf", "soft": "#eef2f8", "muted": "#64748b",
     "eyebrow": "#ffd466", "grid": "#e9edf4"},
    {"hero": ("#1a1420", "#3a2140"), "ink": "#241a2b", "a1": "#ff6b5e", "a1s": "#ff8a7d",
     "a2": "#22b8a6", "a2s": "#3ad0be", "soft": "#f6f1f4", "muted": "#6b6472",
     "eyebrow": "#ffb3aa", "grid": "#efe8ee"},
    {"hero": ("#07211f", "#0f3b36"), "ink": "#0e2b28", "a1": "#f0a500", "a1s": "#ffbe2e",
     "a2": "#e05780", "a2s": "#f07aa0", "soft": "#eef5f3", "muted": "#5c6f6b",
     "eyebrow": "#ffd27a", "grid": "#e4eeeb"},
    {"hero": ("#12102e", "#2a2358"), "ink": "#1a1740", "a1": "#8b5cf6", "a1s": "#a78bfa",
     "a2": "#38bdf8", "a2s": "#5fcbfa", "soft": "#f0f0f8", "muted": "#635f7a",
     "eyebrow": "#c9b6f7", "grid": "#e8e8f2"},
    {"hero": ("#0c1f14", "#173a24"), "ink": "#123020", "a1": "#f97316", "a1s": "#fb923c",
     "a2": "#0ea5e9", "a2s": "#38bdf8", "soft": "#eef4ef", "muted": "#5a6b5f",
     "eyebrow": "#ffc27a", "grid": "#e3ede6"},
]

_ICON = {  # 인라인 SVG path (24x24, stroke)
    "trend": "<path d='M3 17l6-6 4 4 8-8'/><path d='M14 7h7v7'/>",
    "bar": "<path d='M12 20V10'/><path d='M18 20V4'/><path d='M6 20v-6'/>",
    "won": "<path d='M4 6l4 12 4-9 4 9 4-12'/><path d='M3 11h18'/>",
    "flag": "<path d='M4 21V4'/><path d='M4 4h13l-2 4 2 4H4'/>",
    "globe": "<circle cx='12' cy='12' r='9'/><path d='M3 12h18'/><path d='M12 3a15 15 0 010 18a15 15 0 010-18'/>",
    "chart": "<path d='M3 3v18h18'/><path d='M7 15l3-4 3 2 4-6'/>",
}


# ── 유틸 ──────────────────────────────────────────────────────────────────
def _num(v):
    """값 → 유한 float. 파싱 실패·NaN·Inf 는 None (단일 소스: shared.numeric.safe_float)."""
    return safe_float(v)


def _fmt(v):
    f = _num(v)
    if f is None:
        return str(v)
    if f == 0:
        return "0"
    if f == int(f):
        return f"{int(f):,}"
    # ★ 0.1 미만 소수: 소수2자리 (0.03 → "0.03", "0.0" 방지)
    if 0 < abs(f) < 0.1:
        return f"{f:.2f}"
    return f"{f:,.1f}" if abs(f) < 1000 else f"{f:,.0f}"


def _auto_scale(val, unit):
    """단위 자동 스케일: 백만원→조원/억원, 억원→조원, 원→조원/억원/만원."""
    if val is None:
        return val, unit
    u = (unit or "").strip()
    av = abs(val)
    if "백만" in u:                             # 백만원 / 백만
        if av >= 1_000_000:
            return round(val / 1_000_000, 1), "조원"
        if av >= 10_000:
            return round(val / 10_000, 1), "억원"
    elif "억" in u:                             # ★ 억원 단위 (신규) — 10,000억 = 1조
        if av >= 10_000:
            return round(val / 10_000, 1), "조원"
    elif u in ("원", "KRW"):
        if av >= 1_000_000_000_000:
            return round(val / 1_000_000_000_000, 1), "조원"
        if av >= 100_000_000:
            return round(val / 100_000_000, 1), "억원"
        if av >= 10_000:
            return round(val / 10_000, 1), "만원"
    return val, unit


def _scale_rows_uniform(rows, unit):
    """rows 전체에 동일 스케일 적용 (차트 내 단위 통일).

    ★ 정밀도 적응형: 스케일 후 최솟값이 0.05 미만이면 소수2자리 — "0.03조" 를 "0" 으로 반올림하는 버그 방지.
    """
    if not rows:
        return rows, unit
    max_abs = max(abs(v) for _, v in rows)
    scaled_max, new_unit = _auto_scale(max_abs, unit)
    if new_unit == unit or max_abs == 0:
        return rows, unit
    ratio = scaled_max / max_abs
    min_nz = min((abs(v) * ratio for _, v in rows if v != 0), default=scaled_max)
    prec = 2 if min_nz < 0.05 else 1
    new_rows = [(lb, round(v * ratio, prec)) for lb, v in rows]
    return new_rows, new_unit


def _pairs(ds):
    """dataset → [(label, value)] (숫자 행만)."""
    out = []
    for r in ds.get("data") or []:
        v = _num(r.get("value"))
        if v is not None:
            out.append((str(r.get("label", "")), v))
    return out


def _is_timeseries(ds):
    """시계열 판정 — 구현은 `image_data_verifier.is_timeseries` 단독 (위임만 한다).

    ★ 여기 있던 *라벨 정규식* 판정 본체는 삭제됐다 (사용자 박제 2026-08-10):
      '라벨에 \\d{4}|\\d+일 … 이 60% 이상' 규칙은 ② 동적 설계 위반이었다 —
      진실(as_of)은 행에 실려 있는데 표시 산출물인 라벨 문자열을 읽고 있었다.
      그래서 '콜금리(1일)'·'통안증권 91일' 을 시간으로 오탐했고, 1차 수정이 라벨에
      as_of 를 심자 판정이 뒤집혀 8개 이종 금리가 꺾은선으로 이어졌다.
    """
    from JARVIS06_IMAGE.validators.image_data_verifier import is_timeseries
    return is_timeseries(ds)


def _icon(key, color, s=22):
    body = _ICON.get(key, _ICON["chart"])
    return (f"<svg width='{s}' height='{s}' viewBox='0 0 24 24' fill='none' stroke='{color}' "
            f"stroke-width='2.3' stroke-linecap='round' stroke-linejoin='round'>{body}</svg>")


def _pct_change(pts):
    if len(pts) < 2 or pts[0][1] == 0:
        return None
    return (pts[-1][1] - pts[0][1]) / abs(pts[0][1]) * 100.0


# ── 표시 파생값 단일 소유 (★ 사용자 박제 2026-08-10) ──────────────────────
#   차트는 원본값 말고도 *계산된 수치* 를 인쇄한다 — Y축 눈금, 최상위/차순위 배율.
#   종전 검증기는 이것들을 계산할 방법이 없어 `dmin*0.7 ~ dmax*1.3` 이라는 *범위 통과*
#   구멍으로 덮었고, 그 구멍 하나로 임의 수치의 80%가 무조건 통과했다.
#   → 그리는 쪽과 검증하는 쪽이 **같은 함수** 를 부르면 구멍이 필요 없다.
def axis_ticks(vals) -> list[float]:
    """선차트 Y축에 인쇄되는 눈금 값 (min/mid/max 3개). `_line_chart` 와 공통."""
    vs = [float(v) for v in vals if v is not None]
    if not vs:
        return []
    lo, hi = min(vs), max(vs)
    pad = (hi - lo) * 0.12 or (abs(hi) * 0.05 or 1)
    lo, hi = lo - pad, hi + pad
    return [hi - pad * 0.4, (hi + lo) / 2, lo + pad * 0.4]


def outlier_pair(rows) -> tuple | None:
    """분리형 막대가 비교하는 두 행 — ((1위 라벨, 값), (차순위 라벨, 값)). 성립 안 하면 None.

    ★ 규칙의 주인은 여기 하나다 (사용자 박제 2026-08-10 3차). 종전엔 *비율* 만 파생하고
      비교 대상은 표시 문구에 `'↑ 2위 대비'` 라고 **서수를 리터럴로 박아** 두었다.
      그 '2' 는 데이터가 준 수가 아니라서 grounding 게이트에 미근거 수치로 잡혔고,
      1위/2위 비율이 큰 테마 차트(핵융합·철도 관련주 등)가 전량 폐기됐다.
      → 비교 대상을 *데이터에서 파생* 해 그 행의 **이름** 을 인쇄한다. 이름은 데이터가
      준 문자열이므로 검증기가 이미 근거로 알고 있고, 독자에게도 서수보다 정확하다.
    rows 는 [(라벨, 값)] 또는 [값] 둘 다 받는다 (검증기는 값만 들고 온다).
    """
    pairs = []
    for r in rows or []:
        lb, v = (r[0], r[1]) if isinstance(r, (tuple, list)) and len(r) >= 2 else ("", r)
        v = _num(v)
        if v is not None:
            pairs.append((str(lb), v))
    pairs.sort(key=lambda p: -p[1])
    if len(pairs) < 2 or pairs[0][1] <= 0:
        return None
    rest_pos = [p for p in pairs[1:] if p[1] > 0]
    if not rest_pos:
        return None
    return pairs[0], rest_pos[0]


def outlier_ratio(vals) -> float | None:
    """분리형 막대가 인쇄하는 배율. 성립하지 않으면 None.
    `_bar_chart`(분기 판정)·`_bar_chart_outlier_split`(표시)·grounding 검증 공통.
    판정 규칙은 `outlier_pair` 단독 — 여기서 다시 정렬·필터하지 않는다."""
    pair = outlier_pair(vals)
    return None if pair is None else pair[0][1] / pair[1][1]


def _sparkline(pts, color, W=120, H=36):
    vals = [v for _, v in pts]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    xs = [i * W / (n - 1) for i in range(n)]
    ys = [H - 4 - (v - lo) / rng * (H - 8) for v in vals]
    pl = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return (f"<svg width='{W}' height='{H}' viewBox='0 0 {W} {H}' fill='none'>"
            f"<polyline points='{pl}' stroke='{color}' stroke-width='2.6' "
            f"stroke-linecap='round' stroke-linejoin='round'/>"
            f"<circle cx='{xs[-1]:.1f}' cy='{ys[-1]:.1f}' r='3.4' fill='{color}'/></svg>")


# ── SVG 차트 빌더 ──────────────────────────────────────────────────────────
def _line_chart(series, pal, W=980, H=340, unit=""):
    """series: [{'name','pts','c','cs'}] 1~2개. 스케일 차이 크면 1지점=100 지수화 비교."""
    xL, xR, yT, yB = 120, W - 140, 46, H - 54  # xL 84→120: Y축 레이블 여백 확보
    n = max(len(s["pts"]) for s in series)
    if n < 2:
        return ""
    xs = [xL + i * (xR - xL) / (n - 1) for i in range(n)]

    indexed = False
    if len(series) == 2:
        m0 = max(abs(v) for _, v in series[0]["pts"]) or 1
        m1 = max(abs(v) for _, v in series[1]["pts"]) or 1
        if max(m0, m1) / max(min(m0, m1), 1e-9) > 3:
            indexed = True

    # 단위 자동 스케일 (indexed 아닐 때 — indexed면 지수화로 이미 스케일됨)
    disp_unit = unit
    if not indexed and unit:
        _all_raw = [(lb, v) for s in series for lb, v in s["pts"]]
        _, disp_unit = _scale_rows_uniform(_all_raw, unit)
        if disp_unit != unit:
            _max_abs = max(abs(v) for _, v in _all_raw) or 1
            _smax, _ = _auto_scale(_max_abs, unit)
            _ratio = _smax / _max_abs
            series = [dict(s, pts=[(lb, round(v * _ratio, 1)) for lb, v in s["pts"]]) for s in series]

    def _series_vals(s):
        vals = [v for _, v in s["pts"]]
        if indexed and vals and vals[0]:
            return [v / vals[0] * 100.0 for v in vals]
        return vals

    allv = [v for s in series for v in _series_vals(s)]
    _ticks = axis_ticks(allv)                 # ★ 눈금 파생 단일 소유 (검증기와 공통)
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.12 or (abs(hi) * 0.05 or 1)
    lo, hi = lo - pad, hi + pad
    rng = (hi - lo) or 1.0

    def _y(v):
        return yB - (v - lo) / rng * (yB - yT)

    parts = [f"<svg width='100%' viewBox='0 0 {W} {H}' fill='none' style='display:block'>", "<defs>"]
    for i, s in enumerate(series):
        parts.append(f"<linearGradient id='g{i}' x1='0' y1='0' x2='0' y2='1'>"
                     f"<stop offset='0' stop-color='{s['c']}' stop-opacity='.26'/>"
                     f"<stop offset='1' stop-color='{s['c']}' stop-opacity='0'/></linearGradient>")
    parts.append("</defs>")

    # gridlines (3)
    for gy in (yT + (yB - yT) * k / 3 for k in range(4)):
        parts.append(f"<line x1='{xL}' y1='{gy:.0f}' x2='{xR}' y2='{gy:.0f}' stroke='{pal['grid']}' stroke-width='1.4'/>")
    # y labels (min/mid/max) — 스케일된 값 + 단위 표시
    _y_unit_sfx = "" if indexed else (f" {disp_unit}" if disp_unit else "")
    for val in _ticks:
        parts.append(f"<text x='{xL - 10}' y='{_y(val) + 5:.0f}' text-anchor='end' fill='{pal['muted']}' "
                     f"font-size='13' font-weight='600'>{_fmt(val)}{_y_unit_sfx}</text>")

    for i, s in enumerate(series):
        vals = _series_vals(s)
        pts = list(zip(xs, [_y(v) for v in vals]))
        area = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{yB} L{pts[0][0]:.1f},{yB} Z"
        line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        parts.append(f"<path d='{area}' fill='url(#g{i})'/>")
        parts.append(f"<polyline points='{line}' stroke='{s['c']}' stroke-width='{3.8 - i*0.4:.1f}' "
                     f"stroke-linecap='round' stroke-linejoin='round'/>")
        ex, ey = pts[-1]
        parts.append(f"<circle cx='{ex:.1f}' cy='{ey:.1f}' r='6.5' fill='{s['c']}' stroke='#fff' stroke-width='3'/>")
        # 끝점 값 배지 — 스케일된 값 표시
        raw_end = s["pts"][-1][1]
        _badge_txt = _fmt(raw_end) + (f" {disp_unit}" if disp_unit and not indexed else "")
        _badge_w = max(100, len(_badge_txt) * 10 + 20)
        parts.append(f"<rect x='{xR + 12}' y='{ey - 15:.0f}' width='{_badge_w}' height='30' rx='8' fill='{pal['ink']}'/>"
                     f"<text x='{xR + 12 + _badge_w / 2:.0f}' y='{ey + 5:.0f}' text-anchor='middle' fill='{s['cs']}' "
                     f"font-size='14' font-weight='800'>{_badge_txt}</text>")

    # x labels
    labs = series[0]["pts"]
    for i, (lb, _) in enumerate(labs):
        parts.append(f"<text x='{xs[i]:.0f}' y='{H - 20}' text-anchor='middle' fill='{pal['muted']}' "
                     f"font-size='15' font-weight='700'>{lb}</text>")
    parts.append("</svg>")
    note = " · 1월=100 지수화" if indexed else ""
    return "".join(parts), note


_SKEW_SPLIT_RATIO = 10  # 1위/2위 비율 이상이면 분리형 레이아웃

# ── 표시 행 상한 (★ 단일 소유 — 사용자 박제 2026-08-10, D04/D07) ──────────
#   히어로 KPI 는 절단 전 전량(8)을 세고 막대는 rows[:7] 만 그려, 같은 이미지 안에서
#   '항목 수 8개 / 합계 27.2%' vs 막대 7개(합 24.7) 로 검산이 깨졌다.
#   이제 조립부(template_engine.view_rows)가 이 상수를 *조회* 해 히어로·차트가 같은 뷰를 쓴다.
BAR_MAX_ROWS = 7
DONUT_MAX_ROWS = 6
KPI_MAX_CARDS = 4


def _bar_defs(pal, grad_id="bg"):
    return (f"<defs><linearGradient id='{grad_id}' x1='0' y1='0' x2='1' y2='0'>"
            f"<stop offset='0' stop-color='{pal['a1']}'/>"
            f"<stop offset='1' stop-color='{pal['a2']}'/></linearGradient></defs>")


def _bar_chart_diverging(rows, pal, W, unit):
    """발산형(0 중앙): 음수 있을 때 양수 우측·음수 좌측."""
    _u = unit if unit == "%" else (f" {unit}" if unit else "")
    vals = [v for _, v in rows]
    vmax, vmin = max(vals), min(vals)
    L, R = 40, W - 40
    cx = (L + R) / 2.0
    half = (R - L) / 2.0 - 55
    span = max(abs(vmax), abs(vmin)) or 1.0
    rowH, gap, barH = 62, 16, 26
    H = len(rows) * (rowH + gap) + 16
    parts = [f"<svg width='100%' viewBox='0 0 {W} {H}' fill='none' style='display:block'>",
             _bar_defs(pal),
             f"<line x1='{cx:.0f}' y1='6' x2='{cx:.0f}' y2='{H - 10}' stroke='{pal['muted']}' "
             f"stroke-width='1.6' opacity='.45'/>"]
    y = 12
    for i, (lb, v) in enumerate(rows):
        top = i == 0
        bl = abs(v) / span * half
        bx = cx if v >= 0 else cx - bl
        fill = "url(#bg)" if top else (pal['a2'] if v >= 0 else pal['muted'])
        parts.append(f"<text x='{cx:.0f}' y='{y + 15}' text-anchor='middle' fill='{pal['ink']}' "
                     f"font-size='16' font-weight='{800 if top else 700}'>{lb}</text>")
        parts.append(f"<rect x='{bx:.0f}' y='{y + 24}' width='{max(4, bl):.0f}' height='{barH}' rx='8' fill='{fill}'/>")
        _val_txt = f"{_fmt(v)}{_u}"
        if v >= 0:
            parts.append(f"<text x='{cx + bl + 10:.0f}' y='{y + 43}' fill='{pal['ink']}' "
                         f"font-size='17' font-weight='800'>{_val_txt}</text>")
        else:
            parts.append(f"<text x='{cx - bl - 10:.0f}' y='{y + 43}' text-anchor='end' fill='{pal['ink']}' "
                         f"font-size='17' font-weight='800'>{_val_txt}</text>")
        y += rowH + gap
    parts.append("</svg>")
    return "".join(parts)


def _bar_chart_linear(rows, pal, W, unit):
    """일반 선형 막대: 좌측 라벨 + 좌정렬 막대 + 우측 값 컬럼."""
    _u = unit if unit == "%" else (f" {unit}" if unit else "")
    vals = [v for _, v in rows]
    vmax = max(vals)
    labelX, trackX = 210, 228
    barMax = W - 470
    rowH, gap = 46, 20
    H = len(rows) * (rowH + gap) + 20
    valX = trackX + barMax + 12
    mx = vmax or 1.0
    parts = [f"<svg width='100%' viewBox='0 0 {W} {H}' fill='none' style='display:block'>",
             _bar_defs(pal)]
    y = 10
    for i, (lb, v) in enumerate(rows):
        top = i == 0
        bw = max(8, v / mx * barMax)
        fill = "url(#bg)" if top else pal['a2']
        parts.append(f"<text x='{labelX}' y='{y + 20}' text-anchor='end' fill='{pal['ink']}' "
                     f"font-size='17' font-weight='{800 if top else 700}'>{lb}</text>")
        parts.append(f"<rect x='{trackX}' y='{y + 4}' width='{barMax}' height='{rowH - 20}' rx='9' fill='{pal['grid']}'/>")
        parts.append(f"<rect x='{trackX}' y='{y + 4}' width='{bw:.0f}' height='{rowH - 20}' rx='9' fill='{fill}'/>")
        parts.append(f"<text x='{valX:.0f}' y='{y + 20}' fill='{pal['ink']}' font-size='18' font-weight='800'>{_fmt(v)}{_u}</text>")
        y += rowH + gap
    parts.append("</svg>")
    return "".join(parts)


def _bar_chart_outlier_split(rows, pal, W, unit):
    """극단 skew 분리형: 1위 outlier 히어로 + 나머지 별도 스케일 서브차트.

    최상위/차순위 비율 >= _SKEW_SPLIT_RATIO 일 때 호출 (rows는 이미 scale 완료, desc 정렬).
    """
    _u = unit if unit == "%" else (f" {unit}" if unit else "")
    top_lb, top_v = rows[0]
    rest = rows[1:]
    # ★ 배지에 인쇄되는 것은 전부 데이터다 — 비교 대상의 *이름* 과 그 배율뿐이고,
    #   관계는 글자가 아니라 기호(↑·×)로 말한다 (사용자 박제 2026-08-10 3차).
    #   종전엔 `'↑ 2위 대비'` 였다. 두 가지가 한꺼번에 잘못돼 있었다 —
    #     ① 서수 '2' 는 데이터가 준 수가 아니라 grounding 게이트에 미근거 수치로 잡혔고
    #        (1위/2위 격차가 큰 테마 차트가 전량 폐기), ② '2위'·'대비' 는 코드가 지어낸
    #        표시 문구라 `precommit --category image` 의 `display-literal` 레그가 막는다.
    #   이름은 데이터가 준 문자열이라 검증기가 이미 근거로 알고 있고, 서수보다 정확하다.
    _pair = outlier_pair(rows)
    ratio_txt = f"×{_pair[0][1] / _pair[1][1]:.0f}" if _pair else ""
    ref_lb = _pair[1][0] if _pair else ""

    trackX, barMax = 228, W - 470
    valX = trackX + barMax + 12
    hero_h = 80
    barY, barH, barR = 20, 26, 9

    # ★ 히어로 막대 길이는 *값에서* 나온다 (사용자 박제 2026-08-10 최종리뷰 #1).
    #   종전엔 `width={barMax}` 로 **값과 무관하게 트랙 전폭** 을 칠했다. 아래 서브차트도
    #   자기 최대값이 전폭이므로, 같은 이미지 안에서 49.3조(히어로)와 0.8조(서브 1위)가
    #   **똑같은 길이의 막대** 로 인쇄됐다 — 막대 길이가 값을 배신하는 것은 데이터 시각화의
    #   근본 오류다(실측 558렌더 중 8건).
    #   이제 히어로는 *서브차트와 같은 스케일* 로 그린다: 길이 = top/차순위 × 트랙.
    #   그 길이는 분리형 진입 조건(비율 >= _SKEW_SPLIT_RATIO) 상 항상 트랙을 넘으므로
    #   트랙 끝에서 **잘린다** — 잘렸다는 사실은 톱니(축 파단) 모서리로 표시하고,
    #   서브차트의 최대값이 이 스케일에서 어디에 오는지를 눈금 하나로 찍는다.
    #   둘 다 기하(geometry)일 뿐 문구를 지어내지 않으며, 배율 ×N 은 이미 데이터 파생이다.
    _ref_v = _pair[1][1] if _pair else 0
    _full_w = (top_v / _ref_v * barMax) if _ref_v else barMax
    bar_w = min(barMax, _full_w)
    clipped = _full_w > barMax + 0.5
    # 서브차트 최대값(=차순위)이 히어로 스케일에서 차지하는 위치 — 축척 차이의 시각 근거
    ref_x = (trackX + barMax * _ref_v / top_v) if (clipped and top_v) else None

    if clipped:
        xR = trackX + bar_w
        zw = 12  # 파단 톱니 폭
        _d = (f"M{trackX + barR},{barY} H{xR - zw} "
              f"L{xR},{barY + barH * 0.25:.0f} L{xR - zw},{barY + barH * 0.5:.0f} "
              f"L{xR},{barY + barH * 0.75:.0f} L{xR - zw},{barY + barH} "
              f"H{trackX + barR} A{barR},{barR} 0 0 1 {trackX},{barY + barH - barR} "
              f"V{barY + barR} A{barR},{barR} 0 0 1 {trackX + barR},{barY} Z")
        bar_svg = f"<path d='{_d}' fill='url(#bg_hero)'/>"
    else:
        bar_svg = (f"<rect x='{trackX}' y='{barY}' width='{bar_w:.0f}' height='{barH}' "
                   f"rx='{barR}' fill='url(#bg_hero)'/>")

    hero_parts = [
        f"<svg width='100%' viewBox='0 0 {W} {hero_h}' fill='none' style='display:block'>",
        _bar_defs(pal, "bg_hero"),
        # 라벨
        f"<text x='210' y='38' text-anchor='end' fill='{pal['ink']}' font-size='17' font-weight='800'>{top_lb}</text>",
        # 배경 트랙 + 값에 비례하는 막대(넘치면 파단 표시)
        f"<rect x='{trackX}' y='{barY}' width='{barMax}' height='{barH}' rx='{barR}' fill='{pal['grid']}'/>",
        bar_svg,
        # 값 라벨
        f"<text x='{valX}' y='38' fill='{pal['ink']}' font-size='18' font-weight='800'>{_fmt(top_v)}{_u}</text>",
    ]
    if ref_x is not None:
        # 차순위 값이 이 스케일에서 서는 자리 — 아래 서브차트가 몇 배 확대인지의 시각 근거
        hero_parts.append(
            f"<line x1='{ref_x:.1f}' y1='{barY - 4}' x2='{ref_x:.1f}' y2='{barY + barH + 4}' "
            f"stroke='{pal['muted']}' stroke-width='2' opacity='.75'/>")
    if ratio_txt and ref_lb:
        hero_parts.append(
            f"<text x='{valX}' y='62' fill='{pal['muted']}' font-size='13' font-weight='600'>"
            f"↑ {ref_lb} {ratio_txt}</text>"
        )
    hero_parts.append("</svg>")
    hero_svg = "".join(hero_parts)

    # ★ 구분선에도 지어낸 문구를 두지 않는다 ('나머지 종목 (별도 스케일)').
    #   축척이 바뀐다는 사실은 글로 주장하지 않아도 아래 막대마다 값·단위가 붙어 읽힌다.
    #   대신 기호 하나로 '여기서 끊긴다' 만 표시한다 — 기호는 데이터를 주장하지 않는다.
    divider = (
        f"<div style='display:flex;align-items:center;gap:10px;margin:10px 0 6px'>"
        f"<span style='flex:1;height:1px;background:{pal['grid']}'></span>"
        f"<span style='font-size:13px;color:{pal['muted']};white-space:nowrap'>⌄</span>"
        f"<span style='flex:1;height:1px;background:{pal['grid']}'></span>"
        f"</div>"
    )

    # 나머지: 자체 max 기준으로 막대 비율 재계산 (별도 스케일 적용)
    sub_svg = _bar_chart_linear(rest, pal, W, unit) if rest else ""
    return hero_svg + divider + sub_svg


def _bar_chart(rows, pal, W=980, unit=""):
    """가로 막대 랭킹 — 값 *내림차순 정렬 가정* (호출자가 실제값 desc 정렬).

    ★ 음수 처리 (사용자 박제 2026-07-06): 음수 있으면 발산형.
    ★ 극단 skew (2026-07-13): 1위/2위 비율 >= _SKEW_SPLIT_RATIO 이면 분리형.
    """
    rows = rows[:BAR_MAX_ROWS]
    if not rows:
        return ""
    rows, unit = _scale_rows_uniform(rows, unit)
    vals = [v for _, v in rows]
    vmin = min(vals)

    if vmin < 0:
        return _bar_chart_diverging(rows, pal, W, unit)

    # ★ 극단 skew 감지 — 1위/2위 비율 >= threshold
    _sk = outlier_ratio(vals)
    if _sk is not None and _sk >= _SKEW_SPLIT_RATIO:
        return _bar_chart_outlier_split(rows, pal, W, unit)

    return _bar_chart_linear(rows, pal, W, unit)


def _donut(rows, pal, size=240, unit=""):
    rows = rows[:DONUT_MAX_ROWS]
    tot = sum(abs(v) for _, v in rows) or 1
    cx = cy = size / 2
    r = size / 2 - 18
    import math
    cols = [pal['a1'], pal['a2'], pal['a1s'], pal['a2s'], pal['muted'], pal['ink']]
    ang = -90.0
    segs = []
    for i, (lb, v) in enumerate(rows):
        frac = abs(v) / tot
        a2 = ang + frac * 360
        large = 1 if frac > 0.5 else 0
        x1 = cx + r * math.cos(math.radians(ang)); y1 = cy + r * math.sin(math.radians(ang))
        x2 = cx + r * math.cos(math.radians(a2)); y2 = cy + r * math.sin(math.radians(a2))
        segs.append(f"<path d='M{x1:.1f},{y1:.1f} A{r},{r} 0 {large} 1 {x2:.1f},{y2:.1f}' "
                    f"stroke='{cols[i % len(cols)]}' stroke-width='30' fill='none' stroke-linecap='butt'/>")
        ang = a2
    top_lb, top_v = max(rows, key=lambda kv: abs(kv[1]))
    # ★ 도넛 중앙값·범례값도 단위 자동 스케일 (ERRORS [424] 교훈)
    top_sv, top_su = _auto_scale(top_v, unit)
    _u_sfx = f" {top_su}" if top_su and top_su != unit else (f" {unit}" if unit else "")
    donut = (f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}'>{''.join(segs)}"
             f"<text x='{cx}' y='{cy - 4}' text-anchor='middle' fill='{pal['ink']}' font-size='34' font-weight='900'>{_fmt(top_sv)}{_u_sfx}</text>"
             f"<text x='{cx}' y='{cy + 24}' text-anchor='middle' fill='{pal['muted']}' font-size='15' font-weight='700'>{top_lb}</text></svg>")
    # 범례값도 스케일된 값 표시
    def _leg_val(v):
        sv, su = _auto_scale(v, unit)
        sfx = f" {su}" if su and su != unit else (f" {unit}" if unit else "")
        return f"{_fmt(sv)}{sfx}"
    legend = "".join(
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:12px'>"
        f"<span style='width:14px;height:14px;border-radius:4px;background:{cols[i % len(cols)]}'></span>"
        f"<span style='font-size:16px;color:{pal['ink']};font-weight:700'>{lb}</span>"
        f"<span style='font-size:16px;color:{pal['muted']};margin-left:auto;font-weight:700'>{_leg_val(v)}</span></div>"
        for i, (lb, v) in enumerate(rows))
    return donut, legend


def _kpi_cards(rows, pal, W=980, unit=""):
    """단일값·소수 항목 → 대형 수치 카드 (막대 금지).

    ★ 왜 막대가 아닌가 (사용자 박제 2026-08-10 — D13/D21): 행이 1개면
      `_bar_chart_linear` 의 v/vmax 정규화 때문에 막대가 트랙 100% 를 채운다 —
      비교 대상이 없는데 '만점' 처럼 보이는 정보량 0의 인코딩이다.
      2026-08-10 경제 slot2·5·7 + 네이버 dg7 이 정확히 그 꼴이었다.
    시그니처·단위 스케일 처리는 형제(_bar_chart/_donut)와 동일하게 맞춘다.
    """
    rows = rows[:KPI_MAX_CARDS]
    if not rows:
        return ""
    rows, unit = _scale_rows_uniform(rows, unit)
    _u = unit if unit == "%" else (f" {unit}" if unit else "")
    cards = []
    for i, (lb, v) in enumerate(rows):
        c = pal['a1'] if i % 2 == 0 else pal['a2']
        cs = pal['a1s'] if i % 2 == 0 else pal['a2s']
        cards.append(
            f"<div style='flex:1;min-width:0;background:{pal['soft']};border-radius:20px;"
            f"padding:34px 32px;border:1px solid {pal['grid']};border-top:5px solid {c}'>"
            f"<div style='font-size:17px;font-weight:700;color:{pal['muted']};"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{lb}</div>"
            f"<div style='font-size:60px;font-weight:900;letter-spacing:-.03em;line-height:1.1;"
            f"color:{pal['ink']};margin-top:10px'>{_fmt(v)}"
            f"<span style='font-size:26px;font-weight:800;color:{cs}'>{_u}</span></div></div>")
    return f"<div style='display:flex;gap:20px'>{''.join(cards)}</div>"


# ── 조립 요소 ──────────────────────────────────────────────────────────────
def _hero_stat(pal, label, big, sub, color, colors, spark=""):
    return (f"<div style='flex:1;position:relative;padding:26px 28px;border-radius:20px;"
            f"background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.10)'>"
            f"<div style='display:flex;align-items:center;gap:10px;font-size:16px;font-weight:700;color:#cdd8ec'>"
            f"<span style='width:13px;height:13px;border-radius:4px;background:{color}'></span>{label}</div>"
            f"<div style='font-size:64px;font-weight:900;letter-spacing:-.03em;line-height:1.05;color:{colors};margin-top:6px'>{big}</div>"
            f"<div style='font-size:16px;color:#9fb0cc;margin-top:8px'>{sub}</div>"
            f"<div style='position:absolute;right:22px;top:22px'>{spark}</div></div>")


def _mini_card(pal, icon, ic_bg, ic_col, label, value, unit="", rad=18):
    return (f"<div style='flex:1;background:#fff;border-radius:{rad}px;padding:22px 24px;border:1px solid {pal['grid']};"
            f"box-shadow:0 8px 24px rgba(18,42,83,.05)'>"
            f"<div style='width:40px;height:40px;border-radius:12px;background:{ic_bg};display:flex;align-items:center;"
            f"justify-content:center;margin-bottom:10px'>{_icon(icon, ic_col, 22)}</div>"
            f"<div style='font-size:15px;color:{pal['muted']};font-weight:600'>{label}</div>"
            f"<div style='font-size:28px;font-weight:900;color:{pal['ink']};letter-spacing:-.02em;margin-top:2px'>{value}"
            f"<span style='font-size:15px;color:{pal['muted']};font-weight:700'> {unit}</span></div></div>")


def _card(pal, num, title, right, inner, rad=24):
    return (f"<div style='background:#fff;border-radius:{rad}px;padding:32px 36px;box-shadow:0 18px 50px rgba(18,42,83,.10);"
            f"border:1px solid {pal['grid']}'>"
            f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:10px'>"
            f"<div style='display:flex;align-items:center;gap:14px'>"
            f"<div style='width:34px;height:34px;border-radius:10px;background:{pal['ink']};color:#fff;font-weight:800;"
            f"font-size:16px;display:flex;align-items:center;justify-content:center'>{num}</div>"
            f"<h2 style='font-size:23px;font-weight:800;color:{pal['ink']};letter-spacing:-.01em'>{title}</h2></div>"
            f"<div style='font-size:15px;color:{pal['muted']};font-weight:700'>{right}</div></div>{inner}</div>")


# ── 레시피/텍스처 ──────────────────────────────────────────────────────────
def _pick_palette(seed):
    """디자인 레시피 레지스트리(기본 + 나이틀리 학습)에서 seed 로 선택. 실패 시 내장 팔레트."""
    try:
        from JARVIS06_IMAGE.design_learner import get_recipes
        recs = get_recipes()
        if recs:
            return recs[seed % len(recs)]
    except Exception:
        pass
    return PALETTES[seed % len(PALETTES)]


def _hero_texture(tex, pal):
    """히어로 밴드 배경 텍스처 (학습 레시피 노브)."""
    if tex == "dots":
        return (f"<div style='position:absolute;inset:0;background-image:radial-gradient(#ffffff14 1.4px,transparent 1.4px);"
                f"background-size:26px 26px'></div>")
    if tex == "diagonal":
        return (f"<div style='position:absolute;inset:0;background-image:repeating-linear-gradient(45deg,#ffffff0d 0 1px,transparent 1px 16px)'></div>")
    if tex == "glow":
        return (f"<div style='position:absolute;left:-80px;bottom:-140px;width:420px;height:420px;border-radius:50%;"
                f"background:radial-gradient(circle,{pal['a2']}22,transparent 60%)'></div>")
    if tex == "none":
        return ""
    # grid (기본)
    return (f"<div style='position:absolute;inset:0;background-image:linear-gradient(#ffffff08 1px,transparent 1px),"
            f"linear-gradient(90deg,#ffffff08 1px,transparent 1px);background-size:44px 44px'></div>")


def _style_pool() -> list[dict]:
    """★ 스타일(골격) 풀 = 정적 큐레이션 라이브러리 + 나이틀리 학습된 새 스타일 (사용자 박제 2026-07-18).

    layout_library.LAYOUTS(정적 10종) + design_recipes.json 의 나이틀리 학습 recipe.template
    (비전/LLM 이 실제 인포그래픽에서 창작한 *새 스타일* — dashboard-grid·magazine-feature 등)을
    합쳐 render_layout 호환({{...}}) 골격만 dedup. → 나이틀리 학습이 스타일 풀을 매일 키운다.

    ★ 렌더 후보 편입 전 고정 표시문구 검사 (사용자 박제 2026-08-10 — D19):
      생성 게이트(design_learner._validate_recipe)만으로는 부족하다 — 실제로 이미지에 인쇄된
      오염 4건은 design_recipes.json 에 직접 커밋된 seed-layout 이라 그 게이트를 *거친 적이
      없다*. 들어오는 문(생성)과 나가는 문(렌더 편입) 양쪽에서 본다.
    """
    from JARVIS06_IMAGE.template_engine import template_literals
    pool: list[dict] = []
    seen: set = set()

    def _add(pid: str, html: str) -> None:
        if not html or html in seen:
            return
        lits = template_literals(html)
        if lits:
            _g_report("image", ValueError(f"레이아웃 '{pid}' 고정 표시문구 {lits[:3]}"),
                      module=__name__, func_name="_style_pool")
            return
        seen.add(html)
        pool.append({"id": pid, "html": html})

    try:
        from JARVIS06_IMAGE.layout_library import LAYOUTS
        for l in (LAYOUTS or []):
            _add(str(l.get("id", "lib")), l.get("html", ""))
    except Exception:
        pass
    try:  # 함수-로컬 lazy import (design_learner → pro_templates 역참조 순환 회피)
        from JARVIS06_IMAGE.design_learner import get_recipes
        for r in (get_recipes() or []):
            t = r.get("template")
            if isinstance(t, str) and t.strip() and ("{{CHART_1}}" in t or "{{TITLE}}" in t):
                _add(str(r.get("id", "learned")), t)
    except Exception:
        pass
    return pool


def _pick_layout_template(datasets, seed) -> str | None:
    """★ 데이터 형태로 레이아웃 골격 선택 (사용자 박제 2026-07-18) — '색만 바뀐 같은 골격' 해소.

    스타일 풀(_style_pool: 라이브러리 10 + 나이틀리 학습본, 매일 성장)에서 데이터 형태 시그니처로
    어울리는 후보군을 파생하고 seed 로 회전 선택. 하드코딩 매핑이 아니라 실데이터(_is_timeseries·
    개수)에서 파생(동적 설계 원칙). LLM 0회. 색(팔레트)은 recipe, 스타일(골격)은 이 풀이 담당.
    """
    pool = _style_pool()
    ds = [d for d in (datasets or []) if d.get("data")]
    if not pool or not ds:
        return None
    n = len(ds)
    ts = [d for d in ds if _is_timeseries(d)]
    # 데이터 형태 → 어울리는 골격 후보(id 부분일치, 라이브러리·학습 스타일 공통). 없으면 전체.
    if n == 1 and ts:
        prefer = ("panoramic", "center", "split", "big-number", "magazine")   # 단일 시계열 → 넓은 메인차트
    elif n == 1:
        prefer = ("center", "minimal", "report", "big-number")                # 단일값 → 중앙집중·미니멀
    elif n >= 4:
        prefer = ("mosaic", "kpi", "sidebar", "dashboard", "grid")            # 다수 → 모자이크·대시보드
    else:
        prefer = ()                                                           # 2~3개 → 전체
    cands = [p for p in pool if any(k in p["id"] for k in prefer)] if prefer else list(pool)
    if len(cands) < 3:   # 후보 부족 → 전체 풀 (다양성 보장)
        cands = list(pool)
    try:
        pick = cands[(int(seed) // 7) % len(cands)]   # //7 로 색 seed 회전과 위상 분리
    except Exception:
        pick = cands[0]
    return pick.get("html")


# ── 폴백 레이아웃 (★ 사용자 박제 2026-08-10 — D03/D12 뿌리1) ──────────────
#   종전엔 여기에 *조립 구현 한 벌이 통째로* 있었다(히어로 KPI·차트형 3분기·최고 판정).
#   template_engine.render_layout 에 같은 일을 하는 사본이 있었고, 2026-07-06 에
#   '무의미한 합계 폐기' 수정이 이쪽에만 걸리자 그 코드는 도달 불가 폴백이 되고
#   안 고쳐진 사본이 상시 실행됐다 — 4조합 전부에서 합계가 재발했다(③위반).
#   → 조립 구현은 render_layout 한 벌만 남기고, 폴백은 *같은 경로에 먹이는 골격 문자열* 로 둔다.
#     ("폴백 제거" 가 아니라 "폴백을 같은 경로로 태우기" 가 목표다.)
#   표시 리터럴 0 — 텍스트는 전부 슬롯 토큰에서 온다(precommit image/recipe-literal 대상).
_FALLBACK_LAYOUT = """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.hero{position:relative;overflow:hidden;padding:52px 60px 56px;background:linear-gradient(135deg,var(--hero0),var(--hero1))}
.hero::after{content:"";position:absolute;right:-120px;top:-150px;width:480px;height:480px;border-radius:50%;background:radial-gradient(circle,var(--a1),transparent 62%);opacity:.16}
.eb{position:relative;display:inline-flex;align-items:center;gap:9px;padding:8px 16px;border:1px solid var(--eyebrow);border-radius:999px;color:var(--eyebrow);font-size:15px;font-weight:700}
h1{position:relative;color:#fff;font-size:52px;font-weight:900;letter-spacing:-.02em;line-height:1.1;margin:18px 0 10px}
.sub{position:relative;color:#a9bad6;font-size:19px}
.hs{position:relative;margin-top:36px}
.hs:empty{display:none}
.body{padding:36px 60px 8px}
section{background:#fff;border-radius:24px;padding:32px 36px;border:1px solid var(--grid);box-shadow:0 18px 50px rgba(18,42,83,.10);margin-bottom:22px}
section:has([data-jarvis-empty]){display:none}
.mc{display:flex;gap:20px;padding:0 60px}
.mc:empty{display:none}
footer{padding:18px 60px 28px;display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body>
<div class="pg">
  <div class="hero">
    <div class="eb">{{EYEBROW}}</div>
    <h1>{{TITLE}}</h1>
    <div class="sub">{{SUBTITLE}}</div>
    <div class="hs">{{HERO_STATS}}</div>
  </div>
  <div class="body">
    <section>{{CHART_1}}</section>
    <section>{{CHART_2}}</section>
    <section>{{CHART_3}}</section>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>"""


# ── 메인 렌더 ──────────────────────────────────────────────────────────────
def build_html(title, subtitle, datasets, seed, src, chip="", recipe=None):
    """골격(레이아웃 템플릿) + 실데이터 → 완성 HTML. 조립은 render_layout 단일 구현.

    ★ 시그니처 불변 (재현 하네스가 이것을 직접 부른다). 반환은 HTML 문자열.
    ★ `src` 는 *의도적으로 소비하지 않는다* (사용자 박제 2026-08-10 — D20):
      출처 문자열은 `template_engine.source_label` 이 데이터에서만 파생한다.
      인자를 남긴 것은 호출자 시그니처 호환 때문이며, 값을 넣어도 출처는 바뀌지 않는다.
    """
    pal = recipe or _pick_palette(seed)

    # 제목·카드 제목 내 'N종목' LLM 추정치 → 실데이터 실제 개수로 교정 (전 경로 공통)
    def _fix_n(t, n):
        return re.sub(r'\d+종목', f'{n}종목', t) if n > 0 and t else t
    datasets = [{**d, "title": _fix_n(d.get("title", ""), len(_pairs(d)))} for d in (datasets or [])]

    # ★ 시계열은 그리기 전에 시점 오름차순으로 세운다 (사용자 박제 2026-08-10 — 신규거짓 #1).
    #   정렬 구현은 `image_spec.enforce_time_axis_ltr` 단독 — 여기선 부르기만 한다.
    #   왜 여기인가: 히어로 증감(_pct_change)·선차트·검증(rendered_view)이 *모두* 이
    #   datasets 를 보고 파생하므로, 한 곳에서 세워야 세 곳이 같은 순서를 본다.
    #   (라이브 경로는 infographic_engine._normalize_ds 가 이미 같은 함수를 부른다 — 멱등)
    try:
        from JARVIS06_IMAGE.image_spec import enforce_time_axis_ltr as _ltr
        datasets = [{**d, "data": _ltr(d.get("data") or [])} for d in datasets]
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="build_html")
    if datasets:
        title = _fix_n(title, len(_pairs(datasets[0])))

    _n_ds = len([d for d in (datasets or []) if d.get("data")])
    if _n_ds < 1:
        return ""

    try:
        from JARVIS06_IMAGE.template_engine import render_layout, has_all_slots_resolved
    except Exception as e:            # 조립부 부재 = 렌더 불가 (거짓 이미지보다 없는 게 낫다)
        _g_report("image", e, module=__name__, func_name="build_html")
        return ""

    # ★ 스타일(골격)=layout_library 10종 + 나이틀리 학습본, 색(팔레트)=recipe.
    #   골격 후보가 하나도 없거나 슬롯 미해결이면 _FALLBACK_LAYOUT 을 *같은 조립부* 에 먹인다.
    for tmpl in (_pick_layout_template(datasets, seed), pal.get("template"), _FALLBACK_LAYOUT):
        if not tmpl:
            continue
        try:
            _h = render_layout(tmpl, title, subtitle, datasets, pal, chip=chip)
        except Exception as e:
            _g_report("image", e, module=__name__, func_name="build_html")
            continue
        if _h and has_all_slots_resolved(_h):
            return _h
    return ""



def render_pro(title, subtitle, datasets, seed, out_path, src="", chip="") -> tuple[str, str]:
    """결정론 전문 템플릿 렌더 (LLM 0회). 반환 (경로, 렌더된 HTML). 실패 시 ("", "").

    ★ HTML 을 함께 돌려주는 이유 (사용자 박제 2026-08-10): 렌더 산출물의 *표시 텍스트* 를
      검증하려면 초크포인트(`infographic_engine._emit`)까지 HTML 이 올라와야 한다.
      종전엔 경로만 돌려줘 검증할 재료가 호출자에게 없었고, 그래서 이 경로로 나간 8장이
      전부 무검증·provenance 미등록으로 발행됐다.
    """
    try:
        datasets = [d for d in (datasets or []) if _pairs(d)]
        if not datasets:
            return "", ""
        html = build_html(title, subtitle, datasets, seed, src, chip=chip)
        if not html:
            return "", ""
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out_path), width=1280)
        p = Path(out_path)
        if ok and p.exists() and p.stat().st_size > 3000:
            return str(out_path), html
        return "", ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_pro")
        return "", ""


__all__ = ["render_pro", "build_html", "PALETTES", "axis_ticks",
           "outlier_ratio", "outlier_pair",
           "BAR_MAX_ROWS", "DONUT_MAX_ROWS", "KPI_MAX_CARDS"]
