"""JARVIS06_IMAGE/infographic_engine.py — 85점 품질 인포그래픽 생성 엔진 (단일 진입점).

★ 사용자 박제 2026-06-30 — "모든 글의 모든 이미지를 85점 품질로, 같은 디자인 아닌 무한
  다양성으로, 내용·수치에 맞게, 병렬로." 경제·테마·향후 모든 글종 공통.

설계 원칙 (LLM 디자인 디렉터 — 타임아웃 우회 + 매번 다른 디자인):
  1. 역할 분리: LLM 디렉터(_llm_design)가 *글+실데이터 보고 작은 JSON 설계* 만 결정
     (layout·mood·orientation·panels·kpis·insight) → 코드(render_spec)가 렌더.
     LLM 이 17KB HTML 전체를 쓰면 SDK 타임아웃 → 금지. 작은 JSON 은 안정 + 폴백 보장.
  2. 매번 다른 디자인: LLM 이 글 내용 보고 레이아웃 아키타입·무드·패널 구성을 *매번 다르게*
     연출. 같은 글은 없으므로 같은 디자인도 없음. (사용자 박제: 글마다 구조·구성·스타일 전부 다름)
  3. 85점+ 품질: 그라디언트·글로우 라인·값 배지·피크별·스파크라인·도넛·번호 배지·도트 텍스처·
     KPI 카드·인사이트 콜아웃 — 챔피언 컴포넌트 내장. 수치는 코드가 *실데이터로만* 채움(사실성).
  4. 신뢰성: 작은 LLM 호출(실패 시 _fallback_spec 규칙기반) + Playwright subprocess 렌더.
  5. 가로형 기본: orientation=landscape 디폴트(썸네일처럼). 세로형은 가끔. 폭 1280 통일·세로 가변.

데이터 입력 = JARVIS09 collect_chart_data 의 datasets:
  [{"title","viz_hint","unit","data":[{"label","value"}],"source":{...},"kind"?}, ...]
  kind 미지정 시 _infer_kind 로 timeseries/category/ratio/kpi 추론.

공개 API:
  generate_infographic(title, subtitle, datasets, *, run_id, slot_key, out_dir,
                       orientation=None, illustration=None, used=None) -> str(path) | ""
"""
from __future__ import annotations
import hashlib
import json
import math
import re
import subprocess
import sys
import base64
from pathlib import Path

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

FONT = "Noto Sans KR,sans-serif"
_RED = "#e8513a"; _BLUE_DN = "#1b78d6"

# ── 팔레트 (응집 색 스킴 — 다양성의 한 축) ────────────────────────────
PALETTES = [
    {"head": "#0d2150,#2f6fed 60%,#4f97ff", "c1": "#2f6fed", "c2": "#1b3a8f", "acc": "#e8513a", "soft": "#eef2f8", "dot": "#dde4ee"},
    {"head": "#0f5a3c,#1f9d6b", "c1": "#1f9d6b", "c2": "#0f5a3c", "acc": "#e08a1e", "soft": "#eaf3ee", "dot": "#d6e6dd"},
    {"head": "#2a1f6b,#7c5cff", "c1": "#7c5cff", "c2": "#3a2a7a", "acc": "#ff7aa8", "soft": "#f1eefb", "dot": "#e3dcf7"},
    {"head": "#7a4a00,#e08a1e", "c1": "#e08a1e", "c2": "#9a6b00", "acc": "#2f6fed", "soft": "#fbf4e8", "dot": "#efe2cf"},
    {"head": "#0b3a52,#1f88c2", "c1": "#1f88c2", "c2": "#0b3a52", "acc": "#f5a623", "soft": "#eaf3f8", "dot": "#d3e6ef"},
    {"head": "#5a1030,#c0395f", "c1": "#c0395f", "c2": "#7a1a3c", "acc": "#2bbd84", "soft": "#fbeef2", "dot": "#f1d6df"},
    {"head": "#143b4a,#16a39a", "c1": "#16a39a", "c2": "#0e5f59", "acc": "#e8513a", "soft": "#e9f5f3", "dot": "#cfe8e4"},
    {"head": "#2b2f3a,#465168", "c1": "#465168", "c2": "#2b2f3a", "acc": "#f5a623", "soft": "#eef0f4", "dot": "#dde1e9"},
]

# ── SVG 아이콘 ────────────────────────────────────────────────────────
def _ic(b): return f"<svg viewBox='0 0 24 24' width='22' height='22' fill='none' stroke='#fff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>{b}</svg>"
_ICONS = {
    "chart": _ic("<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>"),
    "flag":  _ic("<path d='M5 22V3M5 3h13l-2 4 2 4H5'/>"),
    "won":   _ic("<path d='M3 6l3 12 3-9 3 9 3-12'/><path d='M2 10h20M2 13.5h20'/>"),
    "globe": _ic("<circle cx='12' cy='12' r='9'/><path d='M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18'/>"),
    "bank":  _ic("<path d='M3 21h18M5 21V10M19 21V10M3 10l9-6 9 6M9 21v-6h6v6'/>"),
    "oil":   _ic("<path d='M6 22h12V9l-6-7-6 7zM9 13h6'/>"),
    "trend": _ic("<path d='M3 17l6-6 4 4 7-7M14 8h5v5'/>"),
}
_EMOJI = ["📊","📈","🌐","🏦","💱","🛢️","💹","🏭"]


# ── 유틸 ──────────────────────────────────────────────────────────────
def _seed_int(*parts) -> int:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()
    return int(h[:8], 16)

def _fmt(v):
    try:
        f = float(v)
        return f"{f:,.0f}" if abs(f) >= 100 or f == int(f) else f"{f:,.2f}"
    except (TypeError, ValueError):
        return str(v)

def _infer_kind(ds: dict) -> str:
    if ds.get("kind"):
        return ds["kind"]
    vh = (ds.get("viz_hint") or "").lower()
    if "line" in vh or "area" in vh:
        return "timeseries"
    if "pie" in vh or "donut" in vh:
        return "ratio"
    labels = [str(r.get("label", "")) for r in ds.get("data") or []]
    if labels and all(re.search(r"\d{2}[.\-/]?\d{0,2}|월|분기|Q|년", l) for l in labels):
        return "timeseries"
    n = len(ds.get("data") or [])
    return "kpi" if n <= 2 else "category"


# ── 컴포넌트 (모두 85점 품질) ─────────────────────────────────────────
def _spark(V, c, W=120, H=30):
    if not V: return ""
    vmax = max(V); vmin = min(V); rng = (vmax - vmin) or 1; st = W / (len(V) - 1) if len(V) > 1 else 0
    pts = [(st * i, (H - 4) - (v - vmin) / rng * (H - 8)) for i, v in enumerate(V)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    gid = "s" + c[1:]
    return (f"<svg viewBox='0 0 {W} {H}' width='{W}' height='{H}' style='display:block'>"
            f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{c}' stop-opacity='.3'/><stop offset='1' stop-color='{c}' stop-opacity='0'/></linearGradient></defs>"
            f"<polygon points='0,{H} {poly} {W},{H}' fill='url(#{gid})'/><polyline points='{poly}' fill='none' stroke='{c}' stroke-width='2.2' stroke-linejoin='round'/>"
            f"<circle cx='{pts[-1][0]:.1f}' cy='{pts[-1][1]:.1f}' r='3' fill='{c}'/></svg>")

def _iconc(svg, a, b, s=40):
    return (f"<div style='width:{s}px;height:{s}px;border-radius:12px;background:linear-gradient(135deg,{a},{b});"
            f"display:flex;align-items:center;justify-content:center;box-shadow:0 4px 10px {b}55'>{svg}</div>")

def _badge(n, a, b, s=30):
    return (f"<div style='width:{s}px;height:{s}px;border-radius:50%;background:linear-gradient(135deg,{a},{b});color:#fff;"
            f"font-size:14px;font-weight:900;display:flex;align-items:center;justify-content:center;box-shadow:0 3px 8px {b}66'>{n}</div>")

def kpi_card(icon_key, a, b, label, value, chg, sv):
    if chg is None:
        pill = ""
    else:
        up = chg >= 0; col = _RED if up else _BLUE_DN; bg = "#fdecea" if up else "#e8f1fb"
        pill = (f"<span style='display:inline-block;background:{bg};color:{col};font-size:12px;font-weight:800;"
                f"border-radius:20px;padding:2px 9px'>{'▲' if up else '▼'} {abs(chg):g}%</span>")
    return (f"<div style='flex:1;background:#fff;border-radius:18px;padding:15px 16px;box-shadow:0 8px 24px rgba(20,40,80,.07);border:1px solid #eef1f7'>"
            f"<div style='display:flex;align-items:center;gap:9px'>{_iconc(_ICONS.get(icon_key, _ICONS['chart']), a, b)}<div style='font-size:13.5px;color:#6b7686;font-weight:600'>{label}</div></div>"
            f"<div style='display:flex;justify-content:space-between;align-items:flex-end;margin-top:9px'>"
            f"<div><div style='font-size:26px;font-weight:900;color:#16202e;letter-spacing:-.8px'>{value}</div><div style='margin-top:3px'>{pill}</div></div>"
            f"<div>{_spark(sv, a)}</div></div></div>")

def area_chart(L, V, gid, c1, W=620, H=300, corner=None, mark_peak=True):
    pl, pr, pt, pb = 60, 46, 52, 34; pw, ph = W - pl - pr, H - pt - pb
    vmax = max(V); vmin = min(V); rng = (vmax - vmin) or 1; n = len(V); st = pw / (n - 1) if n > 1 else 0
    P = [(pl + st * i, pt + (1 - (v - vmin) / rng) * ph) for i, v in enumerate(V)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in P); imax = V.index(max(V)); last = n - 1
    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' height='auto' preserveAspectRatio='xMidYMid meet' style='display:block' font-family='{FONT}'>"]
    s.append(f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{c1}' stop-opacity='.36'/><stop offset='1' stop-color='{c1}' stop-opacity='.02'/></linearGradient><filter id='{gid}g'><feDropShadow dx='0' dy='4' stdDeviation='5' flood-color='{c1}' flood-opacity='.35'/></filter></defs>")
    for k in range(4):
        gy = pt + ph * k / 3; val = vmax - (vmax - vmin) * k / 3
        s.append(f"<line x1='{pl}' y1='{gy:.1f}' x2='{pl+pw}' y2='{gy:.1f}' stroke='#eceff5' stroke-dasharray='4 5'/><text x='{pl-9}' y='{gy+4:.1f}' font-size='11.5' fill='#aeb6c4' text-anchor='end'>{val:,.0f}</text>")
    s.append(f"<polygon points='{pl},{pt+ph} {poly} {pl+pw},{pt+ph}' fill='url(#{gid})'/><polyline points='{poly}' fill='none' stroke='{c1}' stroke-width='4' stroke-linejoin='round' stroke-linecap='round' filter='url(#{gid}g)'/>")
    for i, ((x, y), v, lab) in enumerate(zip(P, V, L)):
        anc = "start" if i == 0 else ("end" if i == last else "middle"); bw = 54; by = max(y - 33, 4)
        bxx = x - 4 if i == 0 else (x - bw + 4 if i == last else x - bw / 2)
        fill = c1 if i == last else "#fff"; tc = "#fff" if i == last else c1
        s.append(f"<rect x='{bxx:.1f}' y='{by:.1f}' width='{bw}' height='21' rx='10.5' fill='{fill}' stroke='{c1}' stroke-width='1.4'/><text x='{bxx+bw/2:.1f}' y='{by+14:.1f}' font-size='12.5' font-weight='800' fill='{tc}' text-anchor='middle'>{_fmt(v)}</text>")
        s.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='{6 if i==last else 4.5}' fill='#fff' stroke='{c1}' stroke-width='3'/><text x='{x:.1f}' y='{H-9}' font-size='12.5' fill='#8893a6' text-anchor='{anc}'>{lab}</text>")
    if mark_peak:
        mx, my = P[imax]
        sx = mx; sy = max(my - 48, 16); R = 11; rr = 4.6  # 값 배지(my-33~) 위로 올려 겹침 방지
        sp = []
        for k in range(10):
            ang = -math.pi / 2 + k * math.pi / 5
            rad = R if k % 2 == 0 else rr
            sp.append(f"{sx + rad * math.cos(ang):.1f},{sy + rad * math.sin(ang):.1f}")
        s.append(f"<polygon points='{' '.join(sp)}' fill='#f5b400'/>")
    if corner:
        s.append(f"<rect x='{pl}' y='10' rx='13' width='{20+len(corner)*9:.0f}' height='27' fill='{c1}'/><text x='{pl+13}' y='27.5' font-size='13.5' font-weight='800' fill='#fff'>{corner}</text>")
    s.append("</svg>"); return "".join(s)

def vbar_chart(L, V, gid, c1, acc, W=560, H=280, unit=""):
    pl, pr, pt, pb = 16, 16, 36, 58; pw, ph = W - pl - pr, H - pt - pb
    vmax = max(max(V), 0.0); vmin = min(min(V), 0.0); rng = (vmax - vmin) or 1
    zero = pt + (vmax / rng) * ph; n = len(V); slot = pw / n; bw = min(slot * 0.52, 54); last = n - 1
    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' height='auto' preserveAspectRatio='xMidYMid meet' style='display:block' font-family='{FONT}'>"]
    s.append(f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='{c1}'/><stop offset='1' stop-color='{c1}' stop-opacity='.65'/></linearGradient></defs>")
    s.append(f"<line x1='{pl}' y1='{zero:.1f}' x2='{W-pr}' y2='{zero:.1f}' stroke='#dde3ec'/>")
    for i, (lab, v) in enumerate(zip(L, V)):
        cx = pl + slot * i + slot / 2; bx = cx - bw / 2; h = abs(v) / rng * ph
        if v >= 0: by = zero - h; col = acc if i == last else f"url(#{gid})"; ty = max(by - 7, 12)
        else: by = zero; col = _BLUE_DN; ty = by + h + 15
        s.append(f"<rect x='{bx:.1f}' y='{by:.1f}' width='{bw:.1f}' height='{h:.1f}' rx='5' fill='{col}'/>")
        s.append(f"<text x='{cx:.1f}' y='{ty:.1f}' font-size='13' font-weight='800' fill='#16202e' text-anchor='middle'>{_fmt(v)}{unit}</text>")
        # 하단 라벨 — 길면 2줄 줄바꿈 (잘림 방지)
        _lab = str(lab); _maxc = max(5, int(slot / 13))
        if len(_lab) <= _maxc:
            _lines = [_lab]
        elif " " in _lab:
            _p = _lab.split(" "); _lines = [_p[0][:_maxc], " ".join(_p[1:])[:_maxc]]
        else:
            _lines = [_lab[:_maxc], _lab[_maxc:_maxc * 2]]
        for _li, _ln in enumerate(_lines[:2]):
            s.append(f"<text x='{cx:.1f}' y='{H-26+_li*14:.0f}' font-size='12' fill='#8893a6' text-anchor='middle'>{_ln}</text>")
    s.append("</svg>"); return "".join(s)

def hbar_chart(rows, c1, c2, unit="%", W=420):
    """라벨을 막대 *위*에 전체 폭으로 — 긴 한글 설문 응답도 안 잘림."""
    n = len(rows); rh = 54; H = n * rh + 10; pl = 22
    bw = W - pl - 96; vmax = max(abs(v) for _, v in rows) or 1
    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' height='auto' preserveAspectRatio='xMidYMid meet' style='display:block' font-family='{FONT}'><defs><linearGradient id='hb{c1[1:]}' x1='0' y1='0' x2='1' y2='0'><stop offset='0' stop-color='{c2}'/><stop offset='1' stop-color='{c1}'/></linearGradient></defs>"]
    for i, (lab, v) in enumerate(rows):
        y0 = 8 + i * rh
        w = max(abs(v) / vmax * bw, 4)
        s.append(f"<text x='{pl}' y='{y0+14:.0f}' font-size='14.5' font-weight='700' fill='#46505f'>{str(lab)[:34]}</text>")
        s.append(f"<rect x='{pl}' y='{y0+22:.0f}' width='{w:.1f}' height='19' rx='9.5' fill='url(#hb{c1[1:]})'/>")
        s.append(f"<text x='{pl+w+9:.1f}' y='{y0+37:.0f}' font-size='14' font-weight='800' fill='#16202e'>{_fmt(v)}{unit}</text>")
    s.append("</svg>"); return "".join(s)

def donut_chart(ring, disp, sub, gid, c1, c2, W=190, H=190):
    cx, cy, r = W / 2, H / 2, 66; circ = 2 * math.pi * r; dash = circ * min(ring, 100) / 100
    return (f"<svg viewBox='0 0 {W} {H}' width='100%' height='auto' preserveAspectRatio='xMidYMid meet' style='display:block' font-family='{FONT}'>"
            f"<defs><linearGradient id='{gid}' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{c1}'/><stop offset='1' stop-color='{c2}'/></linearGradient></defs>"
            f"<circle cx={cx} cy={cy} r={r} fill='none' stroke='#eef1f7' stroke-width='20'/><circle cx={cx} cy={cy} r={r} fill='none' stroke='url(#{gid})' stroke-width='20' stroke-linecap='round' stroke-dasharray='{dash:.1f} {circ:.1f}' transform='rotate(-90 {cx} {cy})'/>"
            f"<text x={cx} y={cy-1} font-size='32' font-weight='900' fill='#16202e' text-anchor='middle'>{disp}</text><text x={cx} y={cy+22} font-size='13' fill='#8893a6' text-anchor='middle'>{sub}</text></svg>")

_PIE_COLS = ["#2f6fed", "#e8513a", "#1f9d6b", "#7c5cff", "#e08a1e", "#16a39a", "#c0395f", "#465168", "#9aa3b2"]


def pie_chart(L, V, gid, pal, W=620, unit=""):
    """다중 세그먼트 도넛 — 분포 전체를 색상 세그먼트 + 범례(%)로. (단일값 도넛 버그 대체)"""
    rows = [(str(l), abs(float(v))) for l, v in zip(L, V) if str(v).replace(".", "").replace("-", "").isdigit() or True]
    rows = [(l, v) for l, v in zip([str(x) for x in L], [float(x) for x in V])]
    tot = sum(abs(v) for _, v in rows) or 1
    cx, cy, r = 155, 150, 120
    s = [f"<svg viewBox='0 0 {W} 300' width='100%' height='auto' preserveAspectRatio='xMidYMid meet' font-family='{FONT}'>"]
    ang = -90.0
    for i, (lab, v) in enumerate(rows):
        sweep = abs(v) / tot * 360
        if sweep <= 0:
            continue
        a1 = math.radians(ang); a2 = math.radians(ang + sweep)
        x1, y1 = cx + r * math.cos(a1), cy + r * math.sin(a1)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        large = 1 if sweep > 180 else 0
        col = _PIE_COLS[i % len(_PIE_COLS)]
        s.append(f"<path d='M{cx} {cy} L{x1:.1f} {y1:.1f} A{r} {r} 0 {large} 1 {x2:.1f} {y2:.1f} Z' fill='{col}'/>")
        ang += sweep
    s.append(f"<circle cx={cx} cy={cy} r='66' fill='#fff'/>")
    s.append(f"<text x={cx} y={cy-4} font-size='15' fill='#8893a6' text-anchor='middle'>합계</text>"
             f"<text x={cx} y={cy+22} font-size='24' font-weight='900' fill='#16202e' text-anchor='middle'>{_fmt(tot)}{unit}</text>")
    for i, (lab, v) in enumerate(rows[:8]):
        ly = 44 + i * 30
        col = _PIE_COLS[i % len(_PIE_COLS)]
        pct = abs(v) / tot * 100
        s.append(f"<rect x='330' y='{ly}' width='17' height='17' rx='4' fill='{col}'/>"
                 f"<text x='356' y='{ly+14}' font-size='14.5' fill='#46505f' font-weight='600'>{str(lab)[:38]}</text>"
                 f"<text x='{W-14}' y='{ly+14}' font-size='14.5' fill='#16202e' font-weight='800' text-anchor='end'>{_fmt(v)}{unit}</text>")
    s.append("</svg>")
    return "".join(s)


def stat_block(value, label, unit, c1, c2, H=260):
    return (f"<div style='display:flex;flex-direction:column;align-items:center;justify-content:center;height:{H}px;width:100%'>"
            f"<div style='font-size:14px;color:#8893a6;font-weight:600;margin-bottom:8px'>{label}</div>"
            f"<div style='font-size:72px;font-weight:900;line-height:1;background:linear-gradient(135deg,{c1},{c2});-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;color:{c1}'>{value}<span style='font-size:30px;font-weight:800'>{unit}</span></div>"
            f"<div style='margin-top:18px;height:9px;width:66%;border-radius:8px;background:linear-gradient(90deg,{c1},{c2})'></div></div>")


# ── 헤더 ──────────────────────────────────────────────────────────────
def _header(pal, title, subtitle, chip, icon_key="chart"):
    iconbox = (f"<div style='width:56px;height:56px;border-radius:16px;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);"
               f"display:flex;align-items:center;justify-content:center;flex:none'><div style='transform:scale(1.45)'>{_ICONS.get(icon_key, _ICONS['chart'])}</div></div>")
    chip_html = (f"<span style='display:inline-block;background:rgba(255,255,255,.18);border:1px solid rgba(255,255,255,.3);border-radius:30px;"
                 f"padding:5px 14px;font-size:13px;font-weight:700;margin-top:13px;position:relative'>{chip}</span>") if chip else ""
    return (f"<div style='position:relative;background:linear-gradient(120deg,{pal['head']});color:#fff;padding:28px 34px;overflow:hidden'>"
            f"<div style='position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.10) 1.5px,transparent 1.5px);background-size:20px 20px;opacity:.5'></div>"
            f"<div style='position:relative;display:flex;align-items:center;gap:16px'>{iconbox}"
            f"<div><h1 style='font-size:32px;font-weight:900;letter-spacing:-1px'>{title}</h1>"
            f"<p style='font-size:14.5px;opacity:.92;margin-top:4px'>{subtitle}</p></div></div>"
            f"{chip_html}</div>")

def _foot(src):
    return (f"<div style='padding:13px 32px;color:#9aa3b2;font-size:12.5px;display:flex;justify-content:space-between;border-top:1px solid #e6ebf3;background:#fff'>"
            f"<span>{src}</span></div>")

def _card_title(n, pal, title, unit):
    badge = _badge(n, pal['c1'], pal['c2']) if n is not None else (
        f"<div style='width:6px;height:24px;border-radius:3px;background:linear-gradient(180deg,{pal['c1']},{pal['c2']})'></div>")
    return (f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:10px'>{badge}"
            f"<div style='font-size:17px;font-weight:800;color:#16202e;flex:1'>{title}</div>"
            f"<div style='font-size:12.5px;color:#8893a6'>{unit}</div></div>")

# ── 데이터셋 → 차트 ────────────────────────────────────────────────────
def _chart_for(ds, pal, gid, w):
    kind = _infer_kind(ds)
    data = ds.get("data") or []
    L = [str(r.get("label", "")) for r in data]; V = []
    for r in data:
        try: V.append(float(str(r.get("value")).replace(",", "")))
        except (TypeError, ValueError): V.append(0.0)
    unit = ds.get("unit", "")
    if not V:
        return ""
    # 단일 수치 → 큰 숫자 스탯 카드 (깨진 1-막대/100% 도넛 방지)
    if len(V) == 1:
        return stat_block(_fmt(V[0]), L[0] if L else "", unit, pal["c1"], pal["c2"])
    if kind == "timeseries":
        return area_chart(L, V, gid, pal["c1"], W=w, H=300)
    if kind == "ratio":
        tot = sum(abs(x) for x in V) or 1
        return donut_chart(round(V[0] / tot * 100, 1), f"{V[0]:g}", L[0][:8] if L else "", gid, pal["c1"], pal["c2"])
    if kind == "category":
        if len(V) >= 4:
            rows = sorted(zip(L, V), key=lambda r: r[1], reverse=True)
            return hbar_chart(rows, pal["c1"], pal["acc"], unit=unit, W=w)
        return vbar_chart(L, V, gid, pal["c1"], pal["acc"], W=w, H=280, unit=unit)
    # kpi
    return vbar_chart(L, V, gid, pal["c1"], pal["acc"], W=w, H=240, unit=unit)


def select_design(seed, datasets, orientation=None, used=None):
    used = used or {}
    pal_n = len(PALETTES)
    pi = seed % pal_n
    # run 내 팔레트 중복 회피
    tries = 0
    while pi in (used.get("pal") or set()) and tries < pal_n:
        pi = (pi + 1) % pal_n; tries += 1
    if orientation is None:
        orientation = "portrait" if len(datasets) >= 4 and (seed >> 3) % 3 == 0 else "landscape"
    used.setdefault("pal", set()).add(pi)
    return {"palette": PALETTES[pi], "orientation": orientation}


def render_infographic(spec, datasets, title, subtitle, out_path, src="데이터 출처: 한국거래소 · Yahoo Finance", chip="", illustration_b64=None):
    pal = spec["palette"]; orient = spec["orientation"]
    valid = [ds for ds in (datasets[:6]) if ds.get("data")]
    if not valid:
        return ""
    n = len(valid)
    two_col = (orient == "landscape" and n >= 2)   # 가로=2열, 세로=1열 스택 (폭은 동일)
    W = 1280   # ★ 모든 인포그래픽 가로폭 통일 — 세로폭만 가변 (사용자 박제 2026-06-30)
    chart_w = 560 if two_col else 1180
    cards = []
    for i, ds in enumerate(valid):
        gid = f"g{i}"
        chart = _chart_for(ds, pal, gid, chart_w)
        if not chart:
            continue
        _cw = "flex:1 1 calc(50% - 8px);max-width:calc(50% - 8px)" if two_col else "flex:1 1 100%"
        # 단일 카드는 헤더가 제목을 담당 → 카드 제목 생략(중복 방지). 다중 카드만 번호 배지+제목.
        _title_html = _card_title(i + 1, pal, ds.get('title', ''), ds.get('unit', '')) if n > 1 else ""
        cards.append(f"<div style='{_cw};min-width:300px;background:#fff;border-radius:20px;box-shadow:0 8px 28px rgba(20,40,80,.08);border:1px solid #eef1f7;padding:20px'>"
                     f"{_title_html}"
                     f"<div style='display:flex;align-items:center;justify-content:center'>{chart}</div></div>")
    if not cards:
        return ""
    illus = ""
    if illustration_b64:
        illus = (f"<div style='flex:1 1 38%;min-width:300px;background:#fff;border-radius:20px;overflow:hidden;box-shadow:0 8px 28px rgba(20,40,80,.08);border:1px solid #eef1f7'>"
                 f"<img src='data:image/png;base64,{illustration_b64}' style='width:100%;display:block'/></div>")
    body = (f"<div style='background:{pal['soft']};background-image:radial-gradient({pal['dot']} 1.2px,transparent 1.2px);background-size:22px 22px;padding:22px'>"
            f"<div style='display:flex;flex-wrap:wrap;gap:16px'>{illus}{''.join(cards)}</div></div>")
    html = (f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8><style>"
            f"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700;800;900&display=swap');"
            f"*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:{FONT};width:{W}px;background:#fff}}"
            f"</style></head><body><div style='width:{W}px;background:#fff'>"
            f"{_header(pal, title, subtitle, chip)}{body}{_foot(src)}</div></body></html>")
    try:
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out_path), width=W)
        return str(out_path) if ok else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_infographic")
        return ""


# ════════════════════════════════════════════════════════════════════════
#  LLM 디자인 디렉터 + 스펙 렌더러 (★ 사용자 박제 2026-06-30)
#  — LLM 이 글+실데이터 보고 *매번 다른* 설계(JSON) 결정 → 코드가 85점+ 렌더.
#    수치는 코드가 실데이터로만 채움(사실성). 작은 JSON 출력 → 타임아웃 없음.
# ════════════════════════════════════════════════════════════════════════
_CARD = "background:#fff;border-radius:20px;box-shadow:0 8px 28px rgba(20,40,80,.08);border:1px solid #eef1f7"
_MOOD_IDX = {"navy": 0, "blue": 0, "green": 1, "purple": 2, "amber": 3, "ocean": 4, "teal": 6, "rose": 5, "slate": 7}


def _lv(ds):
    L = [str(r.get("label", "")) for r in (ds.get("data") or [])]
    V = []
    for r in (ds.get("data") or []):
        try:
            V.append(float(str(r.get("value")).replace(",", "")))
        except (TypeError, ValueError):
            V.append(0.0)
    return L, V


def _mood_palette(mood, seed):
    i = _MOOD_IDX.get(str(mood or "").lower().strip())
    if i is None:
        i = seed % len(PALETTES)
    return PALETTES[i % len(PALETTES)]


def _kpi_value(ds, metric, label=""):
    """KPI 값 계산. 반환 (value_str, change, spark_vals, label_override).

    ★ 오라벨링 방지 (사용자 박제 2026-06-30 — '삼성전자 현재가=468,500' 가짜 차단):
      - KPI 라벨이 데이터 항목명과 매칭되면 *그 항목* 값 사용 (엉뚱한 항목 표시 금지).
      - category(항목 비교) 데이터는 시점개념 없음 → latest/min 금지. 최고 항목 + 그 항목명을
        label_override 로 돌려줘 라벨을 실제 항목으로 교정.
    """
    L, V = _lv(ds)
    if not V:
        return ("-", None, [], None)
    kind = _infer_kind(ds)
    # ① 라벨 ↔ 데이터 항목 매칭 (예: "삼성전자 현재가" → 항목 '삼성전자' 값)
    if label:
        for l, v in zip(L, V):
            ls = str(l).strip()
            if ls and (ls in label or label.strip() in ls):
                return (_fmt(v), None, V if kind == "timeseries" else [], None)
    m = (metric or "latest").lower()
    # ② category: 시점 무의미 → 최고 항목 (항목명을 label 로 교정해 오라벨링 차단)
    if kind == "category" and m in ("latest", "min", "max", "top", ""):
        top_l, top_v = max(zip(L, V), key=lambda x: x[1])
        return (_fmt(top_v), None, [], str(top_l)[:12])
    # ③ timeseries / kpi
    if m == "change":
        c = ((V[-1] - V[0]) / abs(V[0]) * 100) if V[0] else 0.0
        return (f"{c:+.1f}%", round(c, 1), V, None)
    if m == "max":
        return (_fmt(max(V)), None, V, None)
    if m == "min":
        return (_fmt(min(V)), None, V, None)
    if m == "avg":
        return (_fmt(sum(V) / len(V)), None, V, None)
    if m == "count":
        return (str(len(V)), None, [], None)
    return (_fmt(V[-1]), None, V, None)  # latest (timeseries 최신값 — 올바름)


def _data_brief(datasets):
    lines = []
    for i, ds in enumerate(datasets):
        L, V = _lv(ds)
        if not V:
            continue
        lines.append(f'key=k{i} | 제목="{ds.get("title","")}" | 종류={_infer_kind(ds)} '
                     f'| 단위="{ds.get("unit","")}" | 점={len(V)}개 | 최신={_fmt(V[-1])} '
                     f'| 범위={_fmt(min(V))}~{_fmt(max(V))}')
    return "\n".join(lines)


_DESIGN_PROMPT = """너는 세계적 수준의 통계 인포그래픽 아트디렉터다. 아래 글 맥락과 *실데이터 시리즈*를 보고,
이 글에 가장 어울리는 **프리미엄 인포그래픽 1장의 설계도(JSON)**를 만든다.

[글 맥락]
__CONTEXT__

[사용 가능 실데이터 — panels/kpis 는 이 key 만 참조]
__BRIEF__

[설계 규칙]
- 매번 *구조·구성·강조가 달라야* 한다. 글 내용에 맞춰 레이아웃·무드·패널 구성을 창의적으로 결정.
- orientation: 기본 "landscape"(가로형 — 썸네일처럼 가로가 길고 컴팩트). "portrait"(세로형)는
  데이터가 아주 많을 때 *가끔만*. 한 포스팅엔 가로형이 세로형보다 많아야 한다 → 웬만하면 landscape.
- layout 하나: dashboard(KPI띠+그리드) | hero_feature(상단 대형 차트+하단 그리드) |
  report_stack(전폭 세로 스택 보고서) | kpi_hero(대형 KPI 강조+보조차트) | split_compare(2열 비교)
- mood: navy|blue|green|purple|amber|ocean|teal|rose|slate 중 글 톤에 맞는 하나.
- header.icon: chart|won|globe|bank|oil|trend|flag 중 주제에 맞는 것.
- kpis 3~4개: 각 {data,label,metric(latest|change|max|min|avg|top|count),icon}.
- panels 2~5개: 각 {kind(area|bar|hbar|donut|stat),data,title,span(1=절반,2=전폭)}.
  데이터 종류에 맞춰(시계열→area, 항목비교→hbar/bar, 단일값→stat, 비중→donut).
- title/subtitle/insight 한국어. **insight 에 수치를 지어내지 마라**(코드가 실데이터로 채움).

[출력] 설명 없이 JSON 객체 하나만:
{"orientation":"landscape","layout":"...","mood":"...","header":{"title":"...","subtitle":"...","chip":"...","icon":"..."},
"kpis":[{"data":"k0","label":"...","metric":"latest","icon":"chart"}],
"panels":[{"kind":"area","data":"k0","title":"...","span":2}],"insight":"..."}"""


def _extract_json(raw):
    if not raw:
        return None
    m = re.search(r"\{.*\}", str(raw), re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _fallback_spec(datasets, seed):
    """LLM 실패 시 규칙기반 설계 (신뢰성 보장 — seed 로 레이아웃·무드 변주)."""
    layouts = ["dashboard", "hero_feature", "report_stack", "kpi_hero", "split_compare"]
    moods = list(_MOOD_IDX.keys())
    layout = layouts[seed % len(layouts)]
    kpis, panels = [], []
    for i, ds in enumerate(datasets[:4]):
        k = _infer_kind(ds)
        kpis.append({"data": f"k{i}", "label": str(ds.get("title", ""))[:10],
                     "metric": "change" if k == "timeseries" else ("top" if k == "category" else "latest"),
                     "icon": "chart"})
    for i, ds in enumerate(datasets[:5]):
        k = _infer_kind(ds)
        kind = {"timeseries": "area", "category": "hbar", "ratio": "donut", "kpi": "stat"}.get(k, "bar")
        panels.append({"kind": kind, "data": f"k{i}", "title": str(ds.get("title", "")),
                       "span": 2 if (i == 0 and layout in ("hero_feature", "report_stack")) else 1})
    return {"orientation": "portrait" if seed % 4 == 0 else "landscape",  # 가로형 75%
            "layout": layout, "mood": moods[(seed >> 3) % len(moods)],
            "header": {"title": "데이터 인사이트", "subtitle": "실시간 데이터 종합", "chip": "", "icon": "chart"},
            "kpis": kpis, "panels": panels, "insight": ""}


def _llm_design(context, datasets, seed):
    brief = _data_brief(datasets)
    try:
        from shared.llm import invoke_text
        prompt = _DESIGN_PROMPT.replace("__CONTEXT__", str(context)[:1200]).replace("__BRIEF__", brief)
        raw = invoke_text("writer_fast", prompt, max_tokens=1600, timeout=90)
        spec = _extract_json(raw)
        if spec and spec.get("panels"):
            return spec, "llm"
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="_llm_design")
    return _fallback_spec(datasets, seed), "fallback"


def _callout_box(text, pal):
    if not text:
        return ""
    return (f"<div style='margin-top:16px;display:flex;align-items:center;gap:12px;background:#fff;border:1px solid #eef1f7;"
            f"border-left:5px solid {pal['c1']};border-radius:14px;padding:14px 18px;box-shadow:0 6px 18px rgba(20,40,80,.06)'>"
            f"<div style='width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,{pal['c1']},{pal['c2']});"
            f"display:flex;align-items:center;justify-content:center;flex:none'>{_ICONS['trend']}</div>"
            f"<div style='font-size:15px;color:#46505f;font-weight:700;line-height:1.4'>{text}</div></div>")


def _render_panel(p, dmap, pal, width, num=None, ch=300):
    ds = dmap.get(p.get("data"))
    if not ds:
        return ""
    L, V = _lv(ds)
    if not V:
        return ""
    kind = (p.get("kind") or "area").lower()
    unit = ds.get("unit", "")
    gid = "pn" + re.sub(r"\W", "", str(p.get("data", "p")))
    if len(V) == 1 and kind not in ("stat", "donut"):
        kind = "stat"
    if kind in ("area", "line"):
        chg = ((V[-1] - V[0]) / abs(V[0]) * 100) if V[0] else 0
        corner = f"{chg:+.0f}%" if len(V) >= 3 else None
        chart = area_chart(L, V, gid, pal["c1"], W=width, H=ch, corner=corner)
    elif kind == "hbar":
        chart = hbar_chart(sorted(zip(L, V), key=lambda x: x[1], reverse=True), pal["c1"], pal["acc"], unit=unit, W=width)
    elif kind == "bar":
        chart = vbar_chart(L, V, gid, pal["c1"], pal["acc"], W=width, H=ch - 20, unit=unit)
    elif kind == "donut":
        tot = sum(abs(x) for x in V) or 1
        chart = donut_chart(round(V[0] / tot * 100, 1), _fmt(V[0]), str(L[0])[:8] if L else "", gid, pal["c1"], pal["c2"])
    elif kind == "stat":
        chart = stat_block(_fmt(V[-1]), p.get("title", ds.get("title", "")), unit, pal["c1"], pal["c2"])
    else:
        chart = area_chart(L, V, gid, pal["c1"], W=width, H=300)
    title_html = "" if kind == "stat" else _card_title(num, pal, p.get("title", ds.get("title", "")), unit)
    return (f"<div style='{_CARD};padding:20px;height:100%'>{title_html}"
            f"<div style='display:flex;align-items:center;justify-content:center'>{chart}</div></div>")


def _grid(items):
    cells = []
    for span, h in items:
        w = "flex:1 1 100%" if span == 2 else "flex:1 1 calc(50% - 8px);max-width:calc(50% - 8px)"
        cells.append(f"<div style='{w};min-width:300px'>{h}</div>")
    return f"<div style='display:flex;flex-wrap:wrap;gap:16px'>{''.join(cells)}</div>"


def _arrange_layout(layout, kpi_html, items, pal):
    if not items:
        return kpi_html
    if layout == "hero_feature":
        return f"<div style='margin-bottom:16px'>{items[0][1]}</div>{kpi_html}{_grid(items[1:])}"
    if layout == "report_stack":
        stacked = "".join(f"<div style='margin-bottom:16px'>{h}</div>" for _, h in items)
        return f"{kpi_html}{stacked}"
    return f"{kpi_html}{_grid(items)}"  # dashboard | kpi_hero | split_compare


def render_spec(spec, datasets, out_path, seed=0, src="데이터 출처: 한국거래소 · Yahoo Finance"):
    try:
        dmap = {f"k{i}": ds for i, ds in enumerate(datasets)}
        pal = _mood_palette(spec.get("mood"), seed)
        # ★ 가로형(landscape) 기본 — 한 포스팅에 가로형이 세로형보다 많아야 함 (사용자 박제)
        orient = str(spec.get("orientation", "landscape")).lower()
        if orient not in ("landscape", "portrait"):
            orient = "landscape"
        layout = "dashboard" if orient == "landscape" else spec.get("layout", "dashboard")
        cap = 4 if orient == "landscape" else 6      # 가로형은 패널 적게 → 세로<가로
        ch = 230 if orient == "landscape" else 300   # 가로형은 차트 낮게
        hdr = spec.get("header") or {}
        kcards = []
        for k in (spec.get("kpis") or [])[:4]:
            ds = dmap.get(k.get("data"))
            if not ds:
                continue
            _metric = k.get("metric", "latest")
            _lbl = k.get("label", "")
            val, chg, sv, _ovr = _kpi_value(ds, _metric, _lbl)
            if _ovr:                # 오라벨링 교정 — 실제 항목명으로
                _lbl = _ovr
            _u = ds.get("unit", "")
            if _u and val != "-" and not str(val).endswith("%") and _metric != "count":
                val = f"{val}{_u}"   # 수치엔 반드시 단위 (사용자 박제)
            kcards.append(kpi_card(k.get("icon", "chart"), pal["c1"], pal["c2"], _lbl, val, chg, sv))
        kpi_html = f"<div style='display:flex;gap:14px;margin-bottom:16px'>{''.join(kcards)}</div>" if kcards else ""
        plist = (spec.get("panels") or [])[:cap]
        # 스팬: 가로형=전부 2열(절반), 세로형=spec/report 따름
        if orient == "landscape":
            spans = [1] * len(plist)
        else:
            spans = [2 if (str(p.get("span")) == "2" or layout == "report_stack") else 1 for p in plist]
        # 외톨이 반쪽 방지(절반 패널 홀수면 마지막을 전폭으로)
        if spans.count(1) % 2 == 1:
            for _i in range(len(spans) - 1, -1, -1):
                if spans[_i] == 1:
                    spans[_i] = 2
                    break
        items = []
        for j, (p, span) in enumerate(zip(plist, spans)):
            width = 1180 if span == 2 else 560
            html = _render_panel(p, dmap, pal, width, num=(j + 1) if len(plist) > 1 else None, ch=ch)
            if html:
                items.append((span, html))
        if not items and not kcards:
            return ""
        body_inner = _arrange_layout(layout, kpi_html, items, pal) + _callout_box(spec.get("insight", ""), pal)
        body = (f"<div style='background:{pal['soft']};background-image:radial-gradient({pal['dot']} 1.2px,transparent 1.2px);"
                f"background-size:22px 22px;padding:22px'>{body_inner}</div>")
        W = 1280
        html = (f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8><style>"
                f"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700;800;900&display=swap');"
                f"*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:{FONT};width:{W}px;background:#fff}}"
                f"</style></head><body><div style='width:{W}px;background:#fff'>"
                f"{_header(pal, hdr.get('title', '데이터 인사이트'), hdr.get('subtitle', ''), hdr.get('chip', ''), hdr.get('icon', 'chart'))}"
                f"{body}{_foot(src)}</div></body></html>")
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out_path), width=W)
        _p = Path(out_path)
        return str(out_path) if (ok and _p.exists() and _p.stat().st_size > 2000) else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_spec")
        return ""


def _render_single(ds, title, subtitle, out_path, seed, src, chip="", slot=""):
    """단일 데이터셋 전용 렌더 — 분포에서 *서로 다른* KPI 4종 + 분포 차트 1개 (중복 0).
    ★ 사용자 박제 2026-06-30: 디렉터가 단일 데이터에 같은 KPI·패널을 양산하던 중복 차단.
    slot(글 내 순번)로 무드·차트종류를 *확실히 분산* → 9장이 전부 다른 디자인."""
    try:
        L, V = _lv(ds)
        if not V:
            return ""
        variant = int(slot) if str(slot).isdigit() else seed
        pal = PALETTES[variant % len(PALETTES)]
        unit = ds.get("unit", "")
        pairs = sorted(zip(L, V), key=lambda x: x[1], reverse=True)
        n = len(pairs)
        def _e(s):   # 엔티티 라벨 정리 (괄호 제거 + 짧게 — KPI 라벨 잘림 방지)
            return re.split(r"[(（]", str(s))[0].strip()[:8]
        # KPI 4종 — 라벨·값 모두 다름 (중복 금지)
        kc = []
        kc.append(kpi_card("flag", pal["c1"], pal["c2"], f"최다 · {_e(pairs[0][0])}", f"{_fmt(pairs[0][1])}{unit}", None, []))
        if n >= 2:
            kc.append(kpi_card("chart", pal["c1"], pal["c2"], f"2위 · {_e(pairs[1][0])}", f"{_fmt(pairs[1][1])}{unit}", None, []))
        if n >= 3:
            kc.append(kpi_card("trend", pal["c1"], pal["c2"], f"최저 · {_e(pairs[-1][0])}", f"{_fmt(pairs[-1][1])}{unit}", None, []))
        kc.append(kpi_card("won", pal["c1"], pal["c2"], "항목 수", str(n), None, []))
        kpi_html = f"<div style='display:flex;gap:14px;margin-bottom:16px'>{''.join(kc)}</div>"
        # 분포 차트 — slot 으로 bar/hbar/pie 변주 (슬롯마다 확실히 다름)
        kind = ["bar", "hbar", "pie"][variant % 3]
        if kind == "pie":
            chart = pie_chart(L, V, "m0", pal, W=1180, unit=unit)
        elif kind == "hbar":
            chart = hbar_chart(pairs, pal["c1"], pal["acc"], unit=unit, W=1180)
        else:
            chart = vbar_chart(L, V, "m0", pal["c1"], pal["acc"], W=1180, H=300, unit=unit)
        card_chart = (f"<div style='{_CARD};padding:22px'>"
                      f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px'>"
                      f"<div style='width:6px;height:24px;border-radius:3px;background:linear-gradient(180deg,{pal['c1']},{pal['c2']})'></div>"
                      f"<div style='font-size:18px;font-weight:800;color:#16202e'>{str(ds.get('title',''))[:36]}</div>"
                      f"<div style='flex:1'></div><div style='font-size:13px;color:#8893a6'>단위 {unit or '-'}</div></div>"
                      f"<div style='display:flex;align-items:center;justify-content:center'>{chart}</div></div>")
        insight = (f"{pairs[0][0]} 항목이 {_fmt(pairs[0][1])}{unit}로 가장 높고, "
                   f"{pairs[-1][0]}이(가) {_fmt(pairs[-1][1])}{unit}로 가장 낮습니다 (총 {n}개 항목).")
        body = (f"<div style='background:{pal['soft']};background-image:radial-gradient({pal['dot']} 1.2px,transparent 1.2px);"
                f"background-size:22px 22px;padding:22px'>{kpi_html}{card_chart}{_callout_box(insight, pal)}</div>")
        W = 1280
        html = (f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8><style>"
                f"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700;800;900&display=swap');"
                f"*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:{FONT};width:{W}px;background:#fff}}"
                f"</style></head><body><div style='width:{W}px;background:#fff'>"
                f"{_header(pal, title, subtitle, chip, 'chart')}{body}{_foot(src)}</div></body></html>")
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        _ok = _html_to_jpg(html, Path(out_path), width=W)
        _p = Path(out_path)
        return str(out_path) if (_ok and _p.exists() and _p.stat().st_size > 2000) else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="_render_single")
        return ""


_TRUSTED_PROVIDERS = {"krx", "yfinance", "ecos", "dart", "kosis", "bok", "web", "market"}


def _verify_dataset(ds) -> bool:
    """★ 수치 팩트체크 게이트 (사용자 박제 2026-06-30): 출처(provenance) 없는 데이터는 차단.
    신뢰 provider 또는 출처 URL 이 있어야 통과 → 검증 불가(LLM 지어낸) 수치 렌더 원천 차단."""
    data = ds.get("data") or []
    if not data:
        return False
    src = ds.get("source") or {}
    prov = str(src.get("provider", "")).lower().strip()
    url = str(src.get("url", "")).strip()
    if not (prov in _TRUSTED_PROVIDERS or url.startswith("http")):
        return False
    # 값이 숫자인 행이 하나라도 있어야
    for r in data:
        try:
            float(str(r.get("value")).replace(",", ""))
            return True
        except (TypeError, ValueError):
            continue
    return False


def generate_infographic(title, subtitle, datasets, *, run_id="", slot_key="",
                         out_dir=None, context="", orientation=None, illustration_b64=None,
                         used=None, chip="", src="데이터 출처: 한국거래소 · Yahoo Finance"):
    """LLM 디자인 디렉터 → 스펙 렌더러로 85점+ 인포그래픽 1장 생성 (단일 진입점).

    LLM 이 글 맥락(context)+실데이터 보고 *매번 다른* 설계(JSON) 결정 → 코드가 렌더.
    실패 시 규칙기반 설계 폴백(신뢰성). 데이터 없으면 "" 반환. 폭 1280 통일.
    ★ 검증: 출처 없는 dataset 은 _verify_dataset 가 제거(거짓 수치 차단).
    """
    datasets = [d for d in (datasets or []) if d.get("data") and _verify_dataset(d)]
    if not datasets:
        return ""
    out_dir = Path(out_dir) if out_dir else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_int(run_id, slot_key, title)
    _sk = re.sub(r"[^0-9A-Za-z]", "", str(slot_key))[:10] or "s"
    # ★ 단일 데이터셋은 전용 렌더러로 (디렉터 중복 KPI·패널 양산 차단) — 중복 0 + 분포 정확
    if len(datasets) == 1:
        _out1 = out_dir / f"infg_{_sk}_{seed % 100000000}.jpg"
        _r = _render_single(datasets[0], title, subtitle, _out1, seed, src, chip=chip, slot=slot_key)
        if _r:
            return _r
    spec, _origin = _llm_design(context or f"{title} — {subtitle}", datasets, seed)
    # ★ 슬롯마다 시각적 다양성 강제 (사용자 박제: 유사 데이터도 같은 디자인 금지)
    #   무드(팔레트)·레이아웃·차트종류를 seed 로 변주 → 9장이 전부 다르게 보이도록.
    _moods = list(_MOOD_IDX.keys())
    spec["mood"] = _moods[seed % len(_moods)]
    _layouts = ["dashboard", "hero_feature", "kpi_hero", "split_compare", "report_stack"]
    spec["layout"] = _layouts[(seed >> 4) % len(_layouts)]
    # category(분포) 패널의 차트종류도 변주 (bar/hbar/donut)
    _cat_kinds = ["bar", "hbar", "donut"]
    _ck = _cat_kinds[(seed >> 7) % len(_cat_kinds)]
    for p in (spec.get("panels") or []):
        if str(p.get("kind", "")).lower() in ("bar", "hbar", "donut"):
            p["kind"] = _ck
    spec.setdefault("header", {})
    spec["header"].setdefault("title", title)
    spec["header"].setdefault("subtitle", subtitle)
    if chip:
        spec["header"].setdefault("chip", chip)
    _sk = re.sub(r"[^0-9A-Za-z]", "", str(slot_key))[:10] or "s"
    out = out_dir / f"infg_{_sk}_{seed % 100000000}.jpg"
    return render_spec(spec, datasets, out, seed=seed, src=src)


_VH_MAP = {
    "line": "line_chart", "area": "line_chart", "step": "line_chart", "combo": "line_chart",
    "band_line": "line_chart", "iso_area": "line_chart",
    "bar": "bar_chart", "barh": "bar_chart", "iso_bar": "bar_chart",
    "pie": "donut", "donut": "donut",
}


def generate_chart_infographic(labels, values, chart_type, title, *, out_dir,
                               run_id="", slot_key="", unit="",
                               source_name="한국거래소 · Yahoo Finance"):
    """[CHART_N] 단일 데이터 차트를 85점 인포그래픽으로 렌더 (chart_generator 진입점).

    실데이터(labels/values)는 호출자가 수집. scatter/빈데이터/오류 시 "" 반환 →
    호출자가 기존 Plotly 폴백. 폭 1280 고정·팔레트/차트종류 seed 다양.
    """
    try:
        if chart_type == "scatter":
            return ""  # 산점도는 엔진 미지원 → Plotly 폴백
        data = []
        for l, v in zip(labels or [], values or []):
            try:
                data.append({"label": str(l), "value": float(str(v).replace(",", ""))})
            except (TypeError, ValueError):
                continue
        if len(data) < 1:
            return ""
        # 출처 박제 — chart_generator 가 넘기는 단일 시리즈는 yfinance/KRX 실데이터 (검증 통과)
        ds = {"title": title, "viz_hint": _VH_MAP.get(chart_type, "bar_chart"),
              "unit": unit, "data": data,
              "source": {"provider": "market", "name": source_name,
                         "url": "https://finance.yahoo.com"}}
        return generate_infographic(title, "실시간 데이터 기반", [ds],
                                    run_id=run_id, slot_key=slot_key, out_dir=out_dir,
                                    context=f"{title} — 단일 지표 시각화",
                                    src=f"데이터 출처: {source_name}")
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="generate_chart_infographic")
        return ""


# ── 경제 브리핑 통합 — 실데이터 인포그래픽 N개 병렬 생성·본문 삽입 ──────
def _economic_datasets():
    """JARVIS09 실데이터로 경제 인포그래픽 데이터셋 풀 구성. 2개 묶음 반환(증시 / 환율·금리·원자재)."""
    from JARVIS09_COLLECTOR import get_market_data, get_ticker_history
    M = get_market_data() or {}

    def hist(t, n=7):
        try:
            h = get_ticker_history(t, period="1y", interval="1mo")
            if h is None or getattr(h, "empty", True):
                return []
            c = h["Close"].dropna()
            return [{"label": i.strftime("%y.%m"), "value": round(float(v), 2)} for i, v in list(c.items())[-n:]]
        except Exception:
            return []

    def chg(*names):
        rows = []
        for n in names:
            d = M.get(n)
            if d and isinstance(d, dict) and "change" in d:
                rows.append({"label": n, "value": round(float(d["change"]), 2)})
        return rows

    def val(name):
        d = M.get(name)
        return float(d["value"]) if d and isinstance(d, dict) and "value" in d else 0.0

    src = {"name": "한국거래소 · Yahoo Finance"}
    A = [d for d in [
        {"title": "코스피 6개월 추이", "viz_hint": "line_chart", "unit": "", "data": hist("^KS11"), "source": src},
        {"title": "글로벌 증시 등락률", "viz_hint": "bar_chart", "unit": "%", "data": chg("NASDAQ", "S&P500", "DOW", "달러/원", "미국채10년"), "source": src},
        {"title": "나스닥 6개월 추이", "viz_hint": "line_chart", "unit": "", "data": hist("^IXIC"), "source": src},
        {"title": "S&P500 6개월 추이", "viz_hint": "line_chart", "unit": "", "data": hist("^GSPC"), "source": src},
    ] if d["data"]]
    B = [d for d in [
        {"title": "원/달러 환율 추이", "viz_hint": "line_chart", "unit": "", "data": hist("KRW=X"), "source": src},
        {"title": "미국채 10년 금리", "viz_hint": "kpi", "unit": "%", "data": [{"label": "10년물 국채금리", "value": val("미국채10년")}], "source": src},
        {"title": "국제 금값 추이", "viz_hint": "line_chart", "unit": "", "data": hist("GC=F"), "source": src},
        {"title": "WTI 유가 추이", "viz_hint": "line_chart", "unit": "", "data": hist("CL=F"), "source": src},
    ] if d["data"]]
    return [b for b in [A, B] if b]


def inject_economic_infographics(blocks, keyword="", out_dir=None, *, run_id="", n=2):
    """경제 브리핑 blocks 에 85점 실데이터 인포그래픽 N개를 병렬 생성·본문 분산 삽입.

    ★ 안전 계약: 어떤 오류·데이터 없음이든 *원본 blocks 그대로 반환* (발행 절대 안 깨짐).
    호출자(writer)는 try/except 1줄로 감싸면 됨.
    """
    try:
        bundles = _economic_datasets()
        if not bundles:
            return blocks
        out_dir = Path(out_dir) if out_dir else Path(".")
        titles = ["오늘의 증시 한눈에", "환율·금리·원자재 한눈에", "글로벌 마켓 종합"]
        subs = ["국내·글로벌 지수와 등락률", "원/달러·미국채·금·유가 핵심 지표", "주요 자산 시계열 비교"]
        used = {}
        jobs = [(i, b) for i, b in enumerate(bundles[:n])]
        results = [None] * len(jobs)
        from concurrent.futures import ThreadPoolExecutor

        def _one(idx, bundle):
            return generate_infographic(
                titles[idx % len(titles)], subs[idx % len(subs)], bundle,
                run_id=str(run_id) + keyword, slot_key=f"eco{idx}", out_dir=out_dir,
                used=used, chip="실시간 시장 데이터",
                orientation=("landscape" if idx % 2 == 0 else "portrait"),
            )
        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as ex:
            futs = {ex.submit(_one, i, b): i for i, b in jobs}
            for f in futs:
                i = futs[f]
                try:
                    results[i] = f.result(timeout=120)
                except Exception:
                    results[i] = ""
        imgs = [("image", p) for p in results if p and Path(p).exists()]
        if not imgs:
            return blocks
        text_idx = [j for j, b in enumerate(blocks) if b and b[0] == "text"]
        if not text_idx:
            return blocks + imgs
        out = list(blocks)
        n_img = len(imgs)
        spots = []
        for k in range(n_img):
            ti = text_idx[min(len(text_idx) - 1, int((k + 1) * len(text_idx) / (n_img + 1)))]
            spots.append(ti + 1)
        for img, pos in sorted(zip(imgs, spots), key=lambda z: z[1], reverse=True):
            out.insert(min(pos, len(out)), img)
        return out
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="inject_economic_infographics")
        return blocks


__all__ = ["generate_infographic", "render_infographic", "select_design",
           "generate_chart_infographic", "inject_economic_infographics", "PALETTES"]
