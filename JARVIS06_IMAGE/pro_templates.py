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
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.1f}" if abs(f) < 1000 else f"{f:,.0f}"


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
def _line_chart(series, pal, W=980, H=340):
    """series: [{'name','pts','c','cs'}] 1~2개. 스케일 차이 크면 1지점=100 지수화 비교."""
    xL, xR, yT, yB = 84, W - 130, 46, H - 54
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
    # y labels (min/mid/max)
    for val in (hi - pad * 0.4, (hi + lo) / 2, lo + pad * 0.4):
        parts.append(f"<text x='{xL - 14}' y='{_y(val) + 5:.0f}' text-anchor='end' fill='{pal['muted']}' "
                     f"font-size='14' font-weight='600'>{_fmt(val)}</text>")

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
        # 끝점 값 배지
        raw_end = s["pts"][-1][1]
        parts.append(f"<rect x='{xR + 12}' y='{ey - 15:.0f}' width='100' height='30' rx='8' fill='{pal['ink']}'/>"
                     f"<text x='{xR + 62}' y='{ey + 5:.0f}' text-anchor='middle' fill='{s['cs']}' "
                     f"font-size='15' font-weight='800'>{_fmt(raw_end)}</text>")

    # x labels
    labs = series[0]["pts"]
    for i, (lb, _) in enumerate(labs):
        parts.append(f"<text x='{xs[i]:.0f}' y='{H - 20}' text-anchor='middle' fill='{pal['muted']}' "
                     f"font-size='15' font-weight='700'>{lb}</text>")
    parts.append("</svg>")
    note = " · 1월=100 지수화" if indexed else ""
    return "".join(parts), note


def _bar_chart(rows, pal, W=980):
    """가로 막대 랭킹 — 값 *내림차순 정렬 가정* (호출자가 실제값 desc 정렬).

    ★ 음수 처리 (사용자 박제 2026-07-06): 값에 음수가 있으면 0 기준선 발산형 —
    양수는 우측, 음수는 좌측으로. 순위·막대길이·1위 강조 모두 *절댓값 아닌 실제값* 기준
    (예: ROE -72.4% 는 1등이 아니라 꼴찌 · 좌측 막대). 값 라벨은 우측 정렬 컬럼.
    """
    rows = rows[:7]
    if not rows:
        return ""
    vals = [v for _, v in rows]
    vmax, vmin = max(vals), min(vals)
    _defs = ("<defs>"
             f"<linearGradient id='bg' x1='0' y1='0' x2='1' y2='0'>"
             f"<stop offset='0' stop-color='{pal['a1']}'/><stop offset='1' stop-color='{pal['a2']}'/></linearGradient></defs>")

    if vmin < 0:
        # ── 발산형(0 중앙): 항목명 중앙(0축) 위, 양수 우측 · 음수 좌측 ──
        L, R = 40, W - 40
        cx = (L + R) / 2.0                       # 0 기준선 = 중앙
        half = (R - L) / 2.0 - 55                 # 값 라벨용 여백 확보
        span = max(abs(vmax), abs(vmin)) or 1.0
        rowH, gap, barH = 62, 16, 26
        H = len(rows) * (rowH + gap) + 16
        parts = [f"<svg width='100%' viewBox='0 0 {W} {H}' fill='none' style='display:block'>", _defs,
                 f"<line x1='{cx:.0f}' y1='6' x2='{cx:.0f}' y2='{H - 10}' stroke='{pal['muted']}' stroke-width='1.6' opacity='.45'/>"]
        y = 12
        for i, (lb, v) in enumerate(rows):
            top = i == 0
            bl = abs(v) / span * half
            bx = cx if v >= 0 else cx - bl
            fill = "url(#bg)" if top else (pal['a2'] if v >= 0 else pal['muted'])
            parts.append(f"<text x='{cx:.0f}' y='{y + 15}' text-anchor='middle' fill='{pal['ink']}' "
                         f"font-size='16' font-weight='{800 if top else 700}'>{lb}</text>")
            parts.append(f"<rect x='{bx:.0f}' y='{y + 24}' width='{max(4, bl):.0f}' height='{barH}' rx='8' fill='{fill}'/>")
            if v >= 0:
                parts.append(f"<text x='{cx + bl + 10:.0f}' y='{y + 43}' fill='{pal['ink']}' "
                             f"font-size='17' font-weight='800'>{_fmt(v)}</text>")
            else:
                parts.append(f"<text x='{cx - bl - 10:.0f}' y='{y + 43}' text-anchor='end' fill='{pal['ink']}' "
                             f"font-size='17' font-weight='800'>{_fmt(v)}</text>")
            y += rowH + gap
        parts.append("</svg>")
        return "".join(parts)

    # ── 전부 동일 부호: 좌측 라벨 + 좌정렬 막대 + 우측 값 컬럼 ──
    labelX, trackX = 150, 168
    barMax = W - 300
    rowH, gap = 46, 20
    H = len(rows) * (rowH + gap) + 20
    valX = trackX + barMax + 12
    mx = vmax or 1.0
    parts = [f"<svg width='100%' viewBox='0 0 {W} {H}' fill='none' style='display:block'>", _defs]
    y = 10
    for i, (lb, v) in enumerate(rows):
        top = i == 0
        bw = max(8, v / mx * barMax)
        fill = "url(#bg)" if top else pal['a2']
        parts.append(f"<text x='{labelX}' y='{y + 20}' text-anchor='end' fill='{pal['ink']}' "
                     f"font-size='17' font-weight='{800 if top else 700}'>{lb}</text>")
        parts.append(f"<rect x='{trackX}' y='{y + 4}' width='{barMax}' height='{rowH - 20}' rx='9' fill='{pal['grid']}'/>")
        parts.append(f"<rect x='{trackX}' y='{y + 4}' width='{bw:.0f}' height='{rowH - 20}' rx='9' fill='{fill}'/>")
        parts.append(f"<text x='{valX:.0f}' y='{y + 20}' fill='{pal['ink']}' font-size='18' font-weight='800'>{_fmt(v)}</text>")
        y += rowH + gap
    parts.append("</svg>")
    return "".join(parts)


def _donut(rows, pal, size=240):
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
    donut = (f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}'>{''.join(segs)}"
             f"<text x='{cx}' y='{cy - 4}' text-anchor='middle' fill='{pal['ink']}' font-size='40' font-weight='900'>{_fmt(top_v)}</text>"
             f"<text x='{cx}' y='{cy + 26}' text-anchor='middle' fill='{pal['muted']}' font-size='16' font-weight='700'>{top_lb}</text></svg>")
    legend = "".join(
        f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:12px'>"
        f"<span style='width:14px;height:14px;border-radius:4px;background:{cols[i % len(cols)]}'></span>"
        f"<span style='font-size:16px;color:{pal['ink']};font-weight:700'>{lb}</span>"
        f"<span style='font-size:16px;color:{pal['muted']};margin-left:auto;font-weight:700'>{_fmt(v)}</span></div>"
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
    # ★ 학습된 레이아웃 템플릿 우선 — 임의 레이아웃 재현 (ERRORS [360]). 실패 시 기본 레이아웃 폴백.
    # ★ 단, 다중 슬롯 템플릿은 데이터셋 2개+ 일 때만 (사용자 박제 2026-07-05, ERRORS [365]):
    #   데이터 1개로 다중 슬롯 레이아웃을 채우면 빈 카드·우측 여백이 생김. 단일 데이터셋은
    #   기본 풀레이아웃(히어로+차트)이 프레임을 꽉 채운다 ("양쪽 공백 없이").
    _n_ds = len([d for d in (datasets or []) if d.get("data")])
    tmpl = pal.get("template")
    if tmpl and _n_ds >= 2:
        try:
            from JARVIS06_IMAGE.template_engine import render_layout, has_all_slots_resolved
            _h = render_layout(tmpl, title, subtitle, datasets, pal, src=src)
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
            big = (f"{'+' if chg >= 0 else ''}{chg:.1f}<span style='font-size:34px'>%</span>"
                   f"<span style='font-size:30px'> {'▲' if chg >= 0 else '▼'}</span>") if chg is not None else _fmt(pts[-1][1])
            sub = f"{pts[-1][0]} {_fmt(pts[-1][1])}{d.get('unit','')} · {pts[0][0]} {_fmt(pts[0][1])} 대비"
            c, cs = cols2[i % 2]
            hero_blocks.append(_hero_stat(pal, d.get("title", f"지표{i+1}"), big, sub, c, cs, _sparkline(pts, cs)))
        icon_key = "trend"
    elif cats:
        d = cats[0]
        _unit = d.get("unit", "")
        pts = sorted(_pairs(d), key=lambda kv: -kv[1])   # ★ 실제값 desc (절댓값 아님 — ROE 음수=꼴찌)
        if pts:
            top = pts[0]                                  # 최고 = 실제 최댓값
            hero_blocks.append(_hero_stat(pal, f"최고 · {d.get('title','')}",
                                          f"{_fmt(top[1])}<span style='font-size:30px'> {_unit}</span>",
                                          f"{top[0]}", pal['a1'], pal['a1s']))
            if len(pts) > 1:
                low = pts[-1]                             # ★ 최저 = 실제 최솟값 (꼴찌 명시 — 무의미한 합계 폐기)
                hero_blocks.append(_hero_stat(pal, f"최저 · {d.get('title','')}",
                                              f"{_fmt(low[1])}<span style='font-size:30px'> {_unit}</span>",
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
        for i, d in enumerate(ts[:2]):
            pts = _pairs(d)
            if pts:
                c, cs = cols2[i % 2]
                series.append({"name": d.get("title", ""), "pts": pts, "c": c, "cs": cs})
        if series:
            chart, note = _line_chart(series, pal)
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
        if "pie" in vh or "donut" in vh or (unit == "%" and 2 <= len(pts) <= 6 and abs(sum(v for _, v in pts) - 100) < 15):
            donut, legend = _donut(pts, pal)
            inner = (f"<div style='display:flex;align-items:center;gap:36px'>{donut}"
                     f"<div style='flex:1'>{legend}</div></div>")
        else:
            inner = _bar_chart(sorted(pts, key=lambda kv: -kv[1]), pal)   # ★ 실제값 desc
        body_cards.append(_card(pal, f"{n:02d}", d.get("title", ""), unit or "", inner, rad=rad))
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
            cards.append(_mini_card(pal, "chart", pal['soft'], pal['ink'],
                                    d.get("title", ""), _fmt(top[1]), d.get("unit", ""), rad=max(14, rad - 6)))
        if cards:
            mini = f"<div style='display:flex;gap:20px;margin-top:22px'>{''.join(cards)}</div>"

    if not body_cards and not mini:
        return ""

    eyebrow = chip or "데이터 인사이트"
    body_html = "".join(f"<div style='margin-top:22px'>{c}</div>" for c in body_cards)
    src_txt = src or "데이터 출처 · JARVIS"

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
