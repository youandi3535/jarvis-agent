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

진입점: render_pro(title, subtitle, datasets, seed, out_path, src) -> path | ""
데이터 계약: datasets = [{"title","unit","data":[{"label","value"}],"viz_hint"?,"source"?}]
"""
from __future__ import annotations
import re
from pathlib import Path

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

_MONTHS = tuple(f"{i}월" for i in range(1, 13))
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
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


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
    labs = [str(r.get("label", "")) for r in (ds.get("data") or [])]
    if len(labs) < 3:
        return False
    hits = sum(1 for l in labs if l in _MONTHS or re.search(r"\d{4}|\d+분기|Q[1-4]|\d+월|\d+일", l))
    return hits >= max(3, len(labs) * 0.6)


def _icon(key, color, s=22):
    body = _ICON.get(key, _ICON["chart"])
    return (f"<svg width='{s}' height='{s}' viewBox='0 0 24 24' fill='none' stroke='{color}' "
            f"stroke-width='2.3' stroke-linecap='round' stroke-linejoin='round'>{body}</svg>")


def _pct_change(pts):
    if len(pts) < 2 or pts[0][1] == 0:
        return None
    return (pts[-1][1] - pts[0][1]) / abs(pts[0][1]) * 100.0


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
    for val in (hi - pad * 0.4, (hi + lo) / 2, lo + pad * 0.4):
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

    1위/2위 비율 >= _SKEW_SPLIT_RATIO 일 때 호출 (rows는 이미 scale 완료, desc 정렬).
    """
    _u = unit if unit == "%" else (f" {unit}" if unit else "")
    top_lb, top_v = rows[0]
    rest = rows[1:]
    rest_pos = [v for _, v in rest if v > 0]
    ratio_txt = f"{top_v / rest_pos[0]:.0f}배" if rest_pos else ""

    trackX, barMax = 228, W - 470
    valX = trackX + barMax + 12
    hero_h = 80

    hero_parts = [
        f"<svg width='100%' viewBox='0 0 {W} {hero_h}' fill='none' style='display:block'>",
        _bar_defs(pal, "bg_hero"),
        # 라벨
        f"<text x='210' y='38' text-anchor='end' fill='{pal['ink']}' font-size='17' font-weight='800'>{top_lb}</text>",
        # 배경 트랙 + 꽉 찬 그라디언트 막대
        f"<rect x='{trackX}' y='20' width='{barMax}' height='26' rx='9' fill='{pal['grid']}'/>",
        f"<rect x='{trackX}' y='20' width='{barMax}' height='26' rx='9' fill='url(#bg_hero)'/>",
        # 값 라벨
        f"<text x='{valX}' y='38' fill='{pal['ink']}' font-size='18' font-weight='800'>{_fmt(top_v)}{_u}</text>",
    ]
    if ratio_txt:
        hero_parts.append(
            f"<text x='{valX}' y='62' fill='{pal['muted']}' font-size='13' font-weight='600'>"
            f"↑ 2위 대비 {ratio_txt}</text>"
        )
    hero_parts.append("</svg>")
    hero_svg = "".join(hero_parts)

    divider = (
        f"<div style='display:flex;align-items:center;gap:10px;margin:10px 0 6px'>"
        f"<span style='flex:1;height:1px;background:{pal['grid']}'></span>"
        f"<span style='font-size:13px;color:{pal['muted']};white-space:nowrap'>나머지 종목 (별도 스케일)</span>"
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
    rows = rows[:7]
    if not rows:
        return ""
    rows, unit = _scale_rows_uniform(rows, unit)
    vals = [v for _, v in rows]
    vmin = min(vals)

    if vmin < 0:
        return _bar_chart_diverging(rows, pal, W, unit)

    # ★ 극단 skew 감지 — 1위/2위 비율 >= threshold
    if len(vals) >= 2 and vals[1] > 0 and vals[0] / vals[1] >= _SKEW_SPLIT_RATIO:
        return _bar_chart_outlier_split(rows, pal, W, unit)

    return _bar_chart_linear(rows, pal, W, unit)


def _donut(rows, pal, size=240, unit=""):
    rows = rows[:6]
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


# ── 메인 렌더 ──────────────────────────────────────────────────────────────
def build_html(title, subtitle, datasets, seed, src, chip="", recipe=None):
    pal = recipe or _pick_palette(seed)

    # 제목·카드 제목 내 'N종목' LLM 추정치 → 실데이터 실제 개수로 교정 (전 경로 공통)
    def _fix_n(t, n):
        return re.sub(r'\d+종목', f'{n}종목', t) if n > 0 and t else t
    datasets = [{**d, "title": _fix_n(d.get("title", ""), len(_pairs(d)))} for d in (datasets or [])]
    if datasets:
        title = _fix_n(title, len(_pairs(datasets[0])))

    # ★ 학습된 레이아웃 템플릿 우선 — 임의 레이아웃 재현 (ERRORS [360]). 실패 시 기본 레이아웃 폴백.
    # 데이터셋 1개+ 이면 템플릿 시도 — 빈 슬롯은 Playwright CSS card:empty{display:none} 으로 자동 숨김.
    _n_ds = len([d for d in (datasets or []) if d.get("data")])
    tmpl = pal.get("template")
    if tmpl and _n_ds >= 1:
        try:
            from JARVIS06_IMAGE.template_engine import render_layout, has_all_slots_resolved
            _h = render_layout(tmpl, title, subtitle, datasets, pal, src=src, chip=chip)
            if _h and has_all_slots_resolved(_h):
                return _h
        except Exception:
            pass
    tex = pal.get("hero_texture", "grid")
    rad = int(pal.get("card_radius", 24))
    W = 1280
    ts = [d for d in datasets if _is_timeseries(d)]
    cats = [d for d in datasets if not _is_timeseries(d)]

    # ── 히어로 스탯 (최대 2) ──
    hero_blocks = []
    icon_key = "won"
    if ts:
        cols2 = [(pal['a1'], pal['a1s']), (pal['a2'], pal['a2s'])]
        for i, d in enumerate(ts[:2]):
            pts = _pairs(d)
            if not pts:
                continue
            chg = _pct_change(pts)
            _ts_unit = d.get("unit", "")
            _last_v, _last_u = _auto_scale(pts[-1][1], _ts_unit)
            _first_v, _first_u = _auto_scale(pts[0][1], _ts_unit)
            big = (f"{'+' if chg >= 0 else ''}{chg:.1f}<span style='font-size:34px'>%</span>"
                   f"<span style='font-size:30px'> {'▲' if chg >= 0 else '▼'}</span>") if chg is not None else (
                       f"{_fmt(_last_v)}<span style='font-size:30px'> {_last_u}</span>" if _ts_unit else _fmt(pts[-1][1]))
            sub = f"{pts[-1][0]} {_fmt(_last_v)}{_last_u} · {pts[0][0]} {_fmt(_first_v)}{_first_u} 대비"
            c, cs = cols2[i % 2]
            hero_blocks.append(_hero_stat(pal, d.get("title", f"지표{i+1}"), big, sub, c, cs, _sparkline(pts, cs)))
        icon_key = "trend"
    elif cats:
        d = cats[0]
        _unit = d.get("unit", "")
        pts = sorted(_pairs(d), key=lambda kv: -kv[1])   # ★ 실제값 desc (절댓값 아님 — ROE 음수=꼴찌)
        if pts:
            top = pts[0]                                  # 최고 = 실제 최댓값
            _top_v, _top_u = _auto_scale(top[1], _unit)
            hero_blocks.append(_hero_stat(pal, f"최고 · {d.get('title','')}",
                                          f"{_fmt(_top_v)}<span style='font-size:30px'> {_top_u}</span>",
                                          f"{top[0]}", pal['a1'], pal['a1s']))
            if len(pts) > 1:
                low = pts[-1]                             # ★ 최저 = 실제 최솟값 (꼴찌 명시 — 무의미한 합계 폐기)
                _low_v, _low_u = _auto_scale(low[1], _unit)
                hero_blocks.append(_hero_stat(pal, f"최저 · {d.get('title','')}",
                                              f"{_fmt(_low_v)}<span style='font-size:30px'> {_low_u}</span>",
                                              f"{low[0]}", pal['a2'], pal['a2s']))
        icon_key = "bar"

    hero_stats = (f"<div style='display:flex;gap:24px;margin-top:36px'>{''.join(hero_blocks)}</div>"
                  if hero_blocks else "")

    # ── 메인 차트 ──
    body_cards = []
    note = ""
    if ts:
        cols2 = [(pal['a1'], pal['a1s']), (pal['a2'], pal['a2s'])]
        series = []
        _ts_chart_unit = ts[0].get("unit", "") if ts else ""
        for i, d in enumerate(ts[:2]):
            pts = _pairs(d)
            if pts:
                c, cs = cols2[i % 2]
                series.append({"name": d.get("title", ""), "pts": pts, "c": c, "cs": cs})
        if series:
            chart, note = _line_chart(series, pal, unit=_ts_chart_unit)
            legend = "".join(
                f"<span style='display:inline-flex;align-items:center;gap:8px;margin-left:18px;font-size:15px;"
                f"font-weight:700;color:{pal['ink']}'><i style='width:22px;height:6px;border-radius:3px;"
                f"background:{s['c']};display:inline-block'></i>{s['name']}</span>" for s in series)
            body_cards.append(_card(pal, "01", ts[0].get("title", "지수 추이") if len(series) == 1 else "지수 추이 비교",
                                    legend + f"<span style='color:{pal['muted']};margin-left:10px'>{note}</span>", chart, rad=rad))
        rest = cats
    else:
        rest = cats

    # 카테고리/비중 카드
    n = len(body_cards) + 1
    for d in rest[:2]:
        pts = _pairs(d)
        if not pts:
            continue
        vh = (d.get("viz_hint") or "").lower()
        unit = d.get("unit", "")
        _display_unit = unit
        if "pie" in vh or "donut" in vh or (unit == "%" and 2 <= len(pts) <= 6 and abs(sum(v for _, v in pts) - 100) < 15):
            donut, legend = _donut(pts, pal, unit=unit)
            inner = (f"<div style='display:flex;align-items:center;gap:36px'>{donut}"
                     f"<div style='flex:1'>{legend}</div></div>")
        else:
            sorted_pts = sorted(pts, key=lambda kv: -kv[1])
            inner = _bar_chart(sorted_pts, pal, unit=unit)   # ★ 실제값 desc; _bar_chart 내부서 단위 스케일
            # 카드 헤더 단위도 스케일된 단위로 표시
            _display_unit = _scale_rows_uniform(sorted_pts, unit)[1] if sorted_pts else unit
        body_cards.append(_card(pal, f"{n:02d}", d.get("title", ""), _display_unit or "", inner, rad=rad))
        n += 1

    # ── 보조 미니카드 (히어로가 비었을 때 통계 요약) ──
    mini = ""
    if not hero_blocks and datasets:
        cards = []
        for d in datasets[:3]:
            pts = _pairs(d)
            if not pts:
                continue
            top = max(pts, key=lambda kv: abs(kv[1]))
            _mv, _mu = _auto_scale(top[1], d.get("unit", ""))
            cards.append(_mini_card(pal, "chart", pal['soft'], pal['ink'],
                                    d.get("title", ""), _fmt(_mv), _mu, rad=max(14, rad - 6)))
        if cards:
            mini = f"<div style='display:flex;gap:20px;margin-top:22px'>{''.join(cards)}</div>"

    if not body_cards and not mini:
        return ""

    eyebrow = chip or "데이터 인사이트"
    body_html = "".join(f"<div style='margin-top:22px'>{c}</div>" for c in body_cards)
    # ★ 데이터 기간 표시: 각 dataset source 의 as_of 를 수집해 출처에 병기
    _as_of_parts = []
    for _d in datasets:
        _s = _d.get("source") or {}
        if isinstance(_s, dict):
            _ao = _s.get("as_of", "")
            if _ao and _ao not in _as_of_parts:
                _as_of_parts.append(_ao)
    _period_str = (" · ".join(_as_of_parts[:2]) + " 기준") if _as_of_parts else ""
    src_txt = src or "데이터 출처 · JARVIS"
    if _period_str:
        src_txt = f"{src_txt}  ({_period_str})"

    return f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;800;900&display=swap');
*{{margin:0;padding:0;box-sizing:border-box;font-family:{FONT}}}
</style></head><body>
<div style="width:{W}px;background:{pal['soft']}">
  <div style="position:relative;overflow:hidden;padding:52px 60px 56px;background:linear-gradient(135deg,{pal['hero'][0]},{pal['hero'][1]})">
    <div style="position:absolute;right:-120px;top:-150px;width:480px;height:480px;border-radius:50%;background:radial-gradient(circle,{pal['a1']}22,transparent 62%)"></div>
    {_hero_texture(tex, pal)}
    <div style="position:relative;display:inline-flex;align-items:center;gap:9px;padding:8px 16px;border:1px solid {pal['eyebrow']}66;border-radius:999px;color:{pal['eyebrow']};font-size:15px;font-weight:700">
      {_icon(icon_key, pal['eyebrow'], 17)}{eyebrow}</div>
    <h1 style="position:relative;margin:18px 0 10px;color:#fff;font-size:52px;font-weight:900;letter-spacing:-.02em;line-height:1.1">{title}</h1>
    <div style="position:relative;color:#a9bad6;font-size:19px">{subtitle}</div>
    {hero_stats}
  </div>
  <div style="padding:36px 60px 8px">{body_html}{mini}</div>
  <div style="padding:18px 60px 28px;display:flex;align-items:center;justify-content:space-between;color:{pal['muted']};font-size:14px">
    <span>{src_txt}</span><span style="font-weight:800;color:{pal['ink']}">JARVIS · 데이터 인사이트</span>
  </div>
</div></body></html>"""


def render_pro(title, subtitle, datasets, seed, out_path, src="", chip="") -> str:
    """결정론 전문 템플릿 렌더 (LLM 0회). 성공 시 경로, 실패 시 "" (→ render_spec 폴백)."""
    try:
        datasets = [d for d in (datasets or []) if _pairs(d)]
        if not datasets:
            return ""
        html = build_html(title, subtitle, datasets, seed, src, chip=chip)
        if not html:
            return ""
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out_path), width=1280)
        p = Path(out_path)
        return str(out_path) if (ok and p.exists() and p.stat().st_size > 3000) else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_pro")
        return ""


__all__ = ["render_pro", "build_html", "PALETTES"]
