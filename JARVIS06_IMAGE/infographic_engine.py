"""JARVIS06_IMAGE/infographic_engine.py — 85점 품질 인포그래픽 생성 엔진 (단일 진입점).

★ 사용자 박제 2026-06-30 — "모든 글의 모든 이미지를 85점 품질로, 같은 디자인 아닌 무한
  다양성으로, 내용·수치에 맞게, 병렬로." 경제·테마·향후 모든 글종 공통.

설계 원칙 (★ 2026-07-05 전환 — design-generation 주 경로 + design-selection 폴백):
  1. ★ 디자인-생성(design-generation) 1순위: LLM 아트디렉터(_designgen)가 실데이터로
     *전문가급 완결 HTML/CSS/SVG 를 직접 저작* → Chromium 렌더. 손코딩 템플릿의 품질
     천장(옛 "matplotlib +1")을 넘기 위함(사용자 박제 — ERRORS [357]).
  2. 데이터 진실성 게이트: 슬롯 데이터는 slot_renderer.verify_slot 이 자비스09 원본과 이미
     대조 검증. LLM 이 없는 수치를 넣는 리스크는 _dg_verify_html(표시 텍스트 grounding)이
     차단 → 실패 시 폴백. 차트 좌표(attribute)는 검사 안 함.
  3. ★ 신뢰성 = 폴백 이중화: design-generation 실패·타임아웃·검증탈락·발행 데드라인 강등 시
     즉시 design-selection(_llm_design→render_spec, 작은 JSON 스펙+손코딩 렌더러)으로 폴백.
     폴백 엔진은 그라디언트·스파크라인·값배지·도넛·KPI 카드 등 챔피언 컴포넌트 내장(믿을 수
     있는 하한선). 킬스위치 INFOGRAPHIC_DESIGNGEN=0.
  4. 매번 다른 디자인: design-generation 은 아트디렉션 풀(_DG_ART)을 seed 로 회전 + LLM 창작 →
     글마다 구조·색·구성 전부 다름. 폴백 엔진도 layout·mood 매번 변주.
  5. 폭 1280 통일·세로 가변. 수치는 실데이터에서만(사실성 절대).

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
import os
import re
import subprocess
import sys
import base64
import logging
from pathlib import Path

log = logging.getLogger("jarvis")

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
    # ② 비시계열(category·kpi·ratio) 공통: 시점 무의미 → 최고/최저 *항목* + 항목명 라벨 교정.
    #   ★ 리뷰 확정 (ERRORS [312]): 2행(kpi-kind)·ratio 도 category 와 동일 — 항목 간
    #   '변화율'(첫↔끝 항목 비교)과 이질 항목 산술평균은 실존하지 않는 수치 (진실성 위반).
    if kind != "timeseries" and len(V) > 1:
        if m in ("latest", "max", "top", ""):
            top_l, top_v = max(zip(L, V), key=lambda x: x[1])
            return (_fmt(top_v), None, [], str(top_l)[:12])
        if m == "min":                            # ★ 최저는 최저 항목 (ERRORS [310])
            lo_l, lo_v = min(zip(L, V), key=lambda x: x[1])
            return (_fmt(lo_v), None, [], str(lo_l)[:12])
        if m == "change":
            return ("-", None, [], None)
        if m == "avg" and kind != "category":     # 이질 항목(매출액·영업이익) 평균 금지
            return ("-", None, [], None)          # (category = 동질 항목 비교라 평균 유의미)
    if kind != "timeseries" and m == "change":    # 단일값 포함 — 비시계열 변화율 전면 무효
        return ("-", None, [], None)
    # ③ timeseries / 단일값
    _spark = V if kind == "timeseries" else []   # 스파크라인은 시계열만 (항목 나열을 추세처럼 그리기 금지)
    if m == "change":
        c = ((V[-1] - V[0]) / abs(V[0]) * 100) if V[0] else 0.0
        return (f"{c:+.1f}%", round(c, 1), _spark, None)
    if m == "max":
        return (_fmt(max(V)), None, _spark, None)
    if m == "min":
        return (_fmt(min(V)), None, _spark, None)
    if m == "avg":
        return (_fmt(sum(V) / len(V)), None, _spark, None)
    if m == "count":
        return (str(len(V)), None, [], None)
    return (_fmt(V[-1]), None, _spark, None)  # latest (timeseries 최신값 — 올바름)


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
- **제목·라벨 표현은 데이터 단위와 일치**: 단위가 % 가 아니면 '비율/률' 표현 금지,
  금액(원)이면 '가격/금액' 으로. 제목 괄호 단위는 실제 단위와 같을 때만.

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
    # 헤더 제목은 실제 데이터셋 제목 사용 (없으면 caller title 이 generate_infographic 에서 채움)
    _ht = str((datasets[0].get("title") if datasets else "") or "").strip()[:24]
    return {"orientation": "portrait" if seed % 4 == 0 else "landscape",  # 가로형 75%
            "layout": layout, "mood": moods[(seed >> 3) % len(moods)],
            "header": {"title": _ht, "subtitle": "", "chip": "", "icon": "chart"},
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


# ── C1 배치 설계 (사용자 박제 2026-07-02) — 글당 1회 LLM 으로 pool 전체 개별 설계 ──────
#   차트마다 LLM 호출(N회) → 1회로 급감 (Max 구독 rate-limit 절감). 각 이미지는 여전히 LLM 이
#   *개별* 설계(배치 안에서 서로 다른 N개). 실패 시 generate_infographic 이 개별 _llm_design 폴백.
import threading as _ig_threading
_BATCH_DESIGN_CACHE: dict = {}          # run_id -> {ds_key: spec}
_BATCH_LOCK = _ig_threading.Lock()

_BATCH_DESIGN_PROMPT = """너는 세계적 통계 인포그래픽 아트디렉터다. 아래 여러 데이터 각각에
*서로 확연히 다른* 프리미엄 인포그래픽 설계도(JSON)를 만든다. N개 데이터 → N개 설계.
각 설계는 레이아웃·무드(색)·차트종류·강조가 *서로 달라야* 한다 (같은 글에 실림 — 중복 금지).

[데이터 목록 — 각 항목의 data key(k0) 만 참조]
__ITEMS__

[각 설계 규칙]
- orientation: 대부분 "landscape", 데이터 아주 많을 때만 가끔 "portrait".
- layout: dashboard | hero_feature | report_stack | kpi_hero | split_compare 중 하나 (항목마다 다르게).
- mood: navy|blue|green|purple|amber|ocean|teal|rose|slate 중 하나 (항목마다 다르게).
- header.icon: chart|won|globe|bank|oil|trend|flag 중 주제에 맞게.
- kpis 3~4개: {data,label,metric(latest|change|max|min|avg|top|count),icon}.
- panels 2~5개: {kind(area|bar|hbar|donut|stat),data,title,span(1|2)} — 데이터 종류에 맞춰.
- title/subtitle/insight 한국어. insight 에 수치를 지어내지 마라(코드가 실데이터로 채움).
- ★ 같은 항목의 같은 값 중복 표기 금지 (예: '삼성전자 100' 이 KPI 와 차트에 각각 = ✗.
  '삼성전자 100'과 'SK하이닉스 100'처럼 *항목이 다르면* 같은 값이어도 정상 = ○):
  kpis 와 panels 가 동일 항목의 동일 값을 반복 노출하지 않게 metric 을 분산
  (latest·change·max·avg·count)하고, 행이 1개뿐인 데이터는 KPI 또는 stat 패널 중 *한 곳* 에만.
- **제목·라벨 표현은 데이터 단위와 일치**: 단위가 % 가 아니면 '비율/률' 표현 금지,
  금액(원)이면 '가격/금액' 으로. 제목 괄호 단위는 실제 단위와 같을 때만.

[출력] 설명 없이 JSON 배열 하나만. 각 원소에 "idx"(데이터 번호) 포함:
[{"idx":0,"orientation":"landscape","layout":"dashboard","mood":"navy","header":{"title":"...","subtitle":"...","chip":"","icon":"chart"},"kpis":[{"data":"k0","label":"...","metric":"latest","icon":"chart"}],"panels":[{"kind":"area","data":"k0","title":"...","span":2}],"insight":"..."}]"""


def _normalize_ds(ds):
    """시간축 좌→우 + 동일 항목·값 중복 제거 — 렌더·배치프라임 *공통* 정규화 (ERRORS [312]).

    캐시 키(_ds_key)는 값 해시를 포함하므로, 프라임과 렌더가 같은 정규화를 거쳐야
    키가 일치한다 (한쪽만 정규화하면 캐시 미스 → 차트마다 개별 LLM 설계 = rate-limit 악화)."""
    try:
        from JARVIS06_IMAGE.image_spec import enforce_time_axis_ltr as _ltr, dedupe_chart_rows as _ddr
        _fixed = _ltr(ds.get("data") or [])
        if _fixed is not ds.get("data"):
            print("  ⏩ [시간축] 인포그래픽 데이터 시간 순서 교정 — 과거→최근 (좌→우)")
            ds["data"] = _fixed
        _dd = _ddr(ds.get("data") or [])
        if len(_dd) != len(ds.get("data") or []):
            ds["data"] = _dd
    except Exception:
        pass
    return ds


def _ds_key(ds):
    """데이터셋 식별 키 — title + 상위 라벨 + 단위 + 값 해시.

    ★ 값·단위 포함 의무 (ERRORS [308] — 2026-07-03): 같은 종목 목록에 지표만 다른
    데이터셋(등락률 % vs 주가 원)이 title+라벨만으로 충돌 → 한 설계를 공유 →
    디자인 균일 + '(원)' 제목에 % 값 렌더 사고. 값이 다르면 반드시 다른 키.
    ★ 호출 전 _normalize_ds 필수 (프라임·렌더 동일 정규화 — 키 정합)."""
    L = [str(r.get("label", "")) for r in (ds.get("data") or [])][:4]
    V = ",".join(str(r.get("value", "")) for r in (ds.get("data") or [])[:8])
    vh = hashlib.md5(V.encode()).hexdigest()[:8]
    return f"{str(ds.get('title', ''))}|{'|'.join(L)}|{str(ds.get('unit', ''))}|{vh}"


def _extract_json_array(raw):
    if not raw:
        return None
    m = re.search(r"\[.*\]", str(raw), re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def prime_batch_designs(run_id, pool, context=""):
    """글당 1회 — pool 의 모든 데이터셋을 *한 번의 LLM 호출* 로 각각 개별 설계 → 캐시.
    idempotent(run_id 당 1회). 락을 배치 완료까지 보유 → 동시 차트 스레드는 대기 후 캐시 사용.
    실패해도 빈 캐시 등록(개별 폴백) — 재시도 안 함."""
    if not run_id or not pool:
        return
    with _BATCH_LOCK:
        if run_id in _BATCH_DESIGN_CACHE:
            return                       # 이미 다른 스레드가 배치 완료
        # ★ 렌더와 동일 정규화 (사본) — 캐시 키 정합 (ERRORS [312])
        _use = [_normalize_ds({**d, "data": list(d.get("data") or [])}) for d in list(pool)[:16]]
        cache: dict = {}
        try:
            items = "\n".join(f"[{i}] {_data_brief([ds])}" for i, ds in enumerate(_use))
            prompt = _BATCH_DESIGN_PROMPT.replace("__ITEMS__", items)
            from shared.llm import invoke_text
            raw = invoke_text("writer_fast", prompt, timeout=150)
            for spec in (_extract_json_array(raw) or []):
                if not isinstance(spec, dict):
                    continue
                idx = spec.get("idx")
                if isinstance(idx, int) and 0 <= idx < len(_use) and spec.get("panels"):
                    cache[_ds_key(_use[idx])] = spec
            print(f"  🎨 [배치설계] {len(cache)}/{len(_use)}개 인포그래픽 LLM 설계 완료 (호출 1회)")
        except Exception as e:
            _g_report("image", e, module=__name__, func_name="prime_batch_designs")
        _BATCH_DESIGN_CACHE[run_id] = cache


def _callout_box(text, pal):
    if not text:
        return ""
    return (f"<div style='margin-top:16px;display:flex;align-items:center;gap:12px;background:#fff;border:1px solid #eef1f7;"
            f"border-left:5px solid {pal['c1']};border-radius:14px;padding:14px 18px;box-shadow:0 6px 18px rgba(20,40,80,.06)'>"
            f"<div style='width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,{pal['c1']},{pal['c2']});"
            f"display:flex;align-items:center;justify-content:center;flex:none'>{_ICONS['trend']}</div>"
            f"<div style='font-size:15px;color:#46505f;font-weight:700;line-height:1.4'>{text}</div></div>")


_UNIT_PAREN_RE = re.compile(
    r"\(\s*(%p|%|퍼센트|원|천원|만원|억원|조원|조|억|만|달러|USD|KRW|엔|유로|배|건|개|명|호|톤|포인트|pt|bp)\s*\)",
    re.IGNORECASE)


def _reconcile_title_unit(title, unit):
    """제목 속 괄호 단위 ↔ 데이터셋 단위 모순 제거 (ERRORS [310][312]).

    '가격 비교 (원)' 제목에 % 데이터가 붙는 사고 차단 — 괄호 단위가 실제 단위와
    다르면 괄호를 벗긴다 (실제 단위는 _card_title/축이 별도 표기).
    비교는 공백 제거 정규화 ('조 원' == '조원' — 정당한 괄호 오삭제 방지)."""
    t = str(title or "")
    m = _UNIT_PAREN_RE.search(t)
    _norm = lambda s: re.sub(r"\s+", "", str(s or "")).lower()
    if m and _norm(m.group(1)) != _norm(unit):
        return _UNIT_PAREN_RE.sub("", t).strip()
    return t


def _item_ident(ds, lbl):
    """라벨이 가리키는 실제 데이터 항목명 — 중복 판정의 '항목' 정체 (ERRORS [312]).

    '최고 — 케일럼'/'삼성전자 현재가' → 해당 행 라벨. 지표 라벨(평균·항목 수)은 ""."""
    s = str(lbl or "").strip()
    if " — " in s:
        s = s.split(" — ", 1)[1].strip()
    if not s:
        return ""
    for l in (ds.get("data") or []):
        ls = str(l.get("label", "")).strip()
        if ls and (ls in s or s in ls or ls.startswith(s)):
            return ls
    return ""


def _render_panel(p, dmap, pal, width, num=None, ch=300, used_vals=None):
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
        # ★ 값의 정체를 라벨에 명시 (ERRORS [310][312] — '가격 분포 요약 1,141원' 같은
        #   정체불명 수치 금지): 비시계열은 순위 항목 + 항목명, 시계열은 지표 명시.
        #   KPI 카드에 이미 나온 (항목,값) 은 반복하지 않는다 ([307]) — 전 분기 공통.
        #   대체 진실값도 전부 소진되면 패널 드롭 (중복 < 없음).
        _pt = _reconcile_title_unit(p.get("title", ds.get("title", "")), unit)
        _uv = used_vals if used_vals is not None else set()
        _dkey = p.get("data")
        chart = None
        if _infer_kind(ds) != "timeseries" and len(V) > 1:
            for _rank, (_sl, _sv) in enumerate(sorted(zip(L, V), key=lambda x: x[1], reverse=True)):
                _ident = str(_sl).strip()
                if (_dkey, _ident, _fmt(_sv)) not in _uv:
                    _rl = "최고" if _rank == 0 else f"{_rank + 1}위"
                    _uv.add((_dkey, _ident, _fmt(_sv)))
                    chart = stat_block(_fmt(_sv), f"{_rl} — {str(_sl)[:12]}", unit, pal["c1"], pal["c2"])
                    break
            if chart is None:        # 전 항목이 KPI 에 노출 — 파생 진실값(최고/최저 격차)으로
                _mx, _mn = max(V), min(V)
                if _mn > 0 and _mx != _mn and (_dkey, "", f"{_mx / _mn:,.1f}") not in _uv:
                    _uv.add((_dkey, "", f"{_mx / _mn:,.1f}"))
                    chart = stat_block(f"{_mx / _mn:,.1f}", "최고/최저 격차", "배", pal["c1"], pal["c2"])
                elif (_infer_kind(ds) == "category"
                        and (_dkey, "", _fmt(sum(V) / len(V))) not in _uv):
                    _uv.add((_dkey, "", _fmt(sum(V) / len(V))))
                    chart = stat_block(_fmt(sum(V) / len(V)), "평균", unit, pal["c1"], pal["c2"])
        elif _infer_kind(ds) == "timeseries" and len(V) > 1:
            _cands = [(f"{_pt} (최신)", V[-1]), ("기간 최고", max(V)),
                      ("기간 최저", min(V)), ("평균", sum(V) / len(V))]
            for _cl, _cv in _cands:
                if (_dkey, "", _fmt(_cv)) not in _uv:
                    _uv.add((_dkey, "", _fmt(_cv)))
                    chart = stat_block(_fmt(_cv), _cl, unit, pal["c1"], pal["c2"])
                    break
        else:                        # 단일값 — 항목명 명시 + 중복이면 드롭
            _ident = str(L[0]).strip() if L else ""
            if (_dkey, _ident, _fmt(V[-1])) not in _uv:
                _uv.add((_dkey, _ident, _fmt(V[-1])))
                _sl_lbl = f"{_pt} — {_ident}" if _ident and _ident not in _pt else _pt
                chart = stat_block(_fmt(V[-1]), _sl_lbl, unit, pal["c1"], pal["c2"])
        if chart is None:
            return ""                # 모든 진실 표현이 이미 노출 — 중복 렌더 금지
    else:
        chart = area_chart(L, V, gid, pal["c1"], W=width, H=300)
    title_html = "" if kind == "stat" else _card_title(
        num, pal, _reconcile_title_unit(p.get("title", ds.get("title", "")), unit), unit)
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
        # ★ LLM 레이아웃 존중 (사용자 박제 2026-07-02). 단 가로형(landscape)에서 전폭 세로스택
        #   report_stack 은 부적합 → dashboard 로만 대체. 나머지 4종은 LLM 선택 그대로.
        _ll = str(spec.get("layout", "dashboard")).lower()
        if _ll not in ("dashboard", "hero_feature", "report_stack", "kpi_hero", "split_compare"):
            _ll = "dashboard"
        layout = "dashboard" if (orient == "landscape" and _ll == "report_stack") else _ll
        cap = 4 if orient == "landscape" else 6      # 가로형은 패널 적게 → 세로<가로
        ch = 230 if orient == "landscape" else 300   # 가로형은 차트 낮게
        hdr = spec.get("header") or {}
        kcards = []
        _kpi_seen = set()            # ★ 같은 항목·같은 값 KPI 카드 반복 금지 (ERRORS [308][310])
        _kpi_raw_seen = set()        # (data_key, 항목, 단위 없는 값) — stat 패널이 KPI 값 재탕 금지

        def _unit_suffix(_v, _m, _ds):
            _u = _ds.get("unit", "")
            if _u and _v != "-" and not str(_v).endswith("%") and _m != "count":
                return f"{_v}{_u}"   # 수치엔 반드시 단위 (사용자 박제)
            return _v

        def _alt_metrics(_kind):
            # ★ 리뷰 확정 (ERRORS [312]): 대체 지표는 데이터 종류별 — 비시계열에
            #   change(가짜 변화율)·이질 avg 를 후보에서 원천 배제
            if _kind == "timeseries":
                return (("min", "기간 최저"), ("max", "기간 최고"), ("avg", "평균"),
                        ("change", "변화율"), ("count", "데이터 수"))
            if _kind == "category":
                return (("min", "최저"), ("avg", "평균"), ("count", "항목 수"))
            return (("min", "최저"), ("count", "항목 수"))   # kpi/ratio — min 은 항목 라벨 동반

        for k in (spec.get("kpis") or [])[:4]:
            ds = dmap.get(k.get("data"))
            if not ds:
                continue
            _kind0 = _infer_kind(ds)
            _metric = k.get("metric", "latest")
            _lbl = k.get("label", "")
            val, chg, sv, _ovr = _kpi_value(ds, _metric, _lbl)
            if _ovr:                # 오라벨링 교정 — 실제 항목명 + 지표 의미 명시
                _sem = "최저" if (_metric or "").lower() == "min" else "최고"
                _lbl = f"{_sem} — {_ovr}"
            val = _unit_suffix(val, _metric, ds)
            # ★ 항목 정체 포함 키 (ERRORS [312] — 값-단독 키는 '삼성전자 100/SK하이닉스 100'
            #   같은 *정당한* 동률을 오차단): 같은 항목+같은 값만 중복
            _dk = (k.get("data"), _item_ident(ds, _lbl), str(val))
            if val == "-" or _dk in _kpi_seen:
                # ★ 중복·무효 카드는 버리지 않고 *다른 진실 지표* 로 교체 (ERRORS [310])
                for _am, _al in _alt_metrics(_kind0):
                    if _am == _metric:
                        continue
                    v2, c2, s2, o2 = _kpi_value(ds, _am, "")
                    v2 = _unit_suffix(v2, _am, ds)
                    _l2 = f"{_al} — {o2}" if o2 else _al   # 예: '최저 — 케일럼'
                    dk2 = (k.get("data"), _item_ident(ds, _l2), str(v2))
                    if v2 != "-" and dk2 not in _kpi_seen:
                        val, chg, sv, _metric, _dk, _lbl = v2, c2, s2, _am, dk2, _l2
                        break
                else:
                    continue         # 전 지표 동률 — 교체 불가 시에만 카드 드롭
            _kpi_seen.add(_dk)
            _u0 = ds.get("unit", "")
            _raw = str(val)[:-len(_u0)] if _u0 and str(val).endswith(_u0) else str(val)
            _kpi_raw_seen.add((k.get("data"), _dk[1], _raw))   # (data, 항목, 원형값)
            kcards.append(kpi_card(k.get("icon", "chart"), pal["c1"], pal["c2"], _lbl, val, chg, sv))
        kpi_html = f"<div style='display:flex;gap:14px;margin-bottom:16px'>{''.join(kcards)}</div>" if kcards else ""
        plist = (spec.get("panels") or [])[:cap]
        # 스팬: 가로형 hero_feature=첫 패널 히어로(전폭)+나머지 2열, 그 외 가로형=2열 그리드,
        #       세로형=spec/report 따름
        if orient == "landscape":
            spans = ([2 if i == 0 else 1 for i in range(len(plist))]
                     if layout == "hero_feature" else [1] * len(plist))
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
            html = _render_panel(p, dmap, pal, width, num=(j + 1) if len(plist) > 1 else None, ch=ch,
                                 used_vals=_kpi_raw_seen)
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
                f"{body}{_foot(_src_label(datasets[0], src) if datasets else src)}</div></body></html>")
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
        pairs = pairs[:8]   # ★ 가독성 — 한 차트 최대 8개 막대 (30셀 막대벽·뭐가뭔지모름 방지)
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
                f"{_header(pal, title, subtitle, chip, 'chart')}{body}{_foot(_src_label(ds, src))}</div></body></html>")
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        _ok = _html_to_jpg(html, Path(out_path), width=W)
        _p = Path(out_path)
        return str(out_path) if (_ok and _p.exists() and _p.stat().st_size > 2000) else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="_render_single")
        return ""


_TRUSTED_PROVIDERS = {"krx", "yfinance", "ecos", "dart", "kosis", "bok", "web", "market"}

# provider → 사람이 읽는 출처 표기 (footer). 데이터셋마다 *진짜* 출처를 명시 (mislabel 방지).
_PROVIDER_LABEL = {
    "kosis": "통계청 KOSIS", "krx": "한국거래소(KRX)", "yfinance": "Yahoo Finance",
    "ecos": "한국은행 ECOS", "bok": "한국은행", "dart": "금융감독원 DART",
    "naver_news": "언론 보도", "news": "언론 보도", "kor_econ": "경제 뉴스",
    "academic": "학술 논문", "web": "웹 공개자료", "market": "시장 데이터",
}


def _src_label(ds, fallback: str = "") -> str:
    """데이터셋의 실제 source → 'データ 출처: …' footer 문자열. 하드코딩 출처 mislabel 차단."""
    src = ds.get("source") or {}
    prov = str(src.get("provider", "")).lower().strip()
    disp = _PROVIDER_LABEL.get(prov, "")
    if not disp:
        disp = str(src.get("name", "")).strip()[:40]
    if not disp:
        return fallback or "데이터 출처: 공개 통계"
    return f"데이터 출처: {disp}"


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


# ══════════════════════════════════════════════════════════════════════════
# ★ 디자인-생성(design-generation) — LLM 이 전문가급 HTML/CSS/SVG 직접 저작
#   (사용자 박제 2026-07-05 — ERRORS [357]). design-selection(render_spec)은 폴백.
#   데이터는 slot_renderer.verify_slot 가 자비스09 원본과 이미 대조 검증 → 신뢰.
#   추가 리스크(LLM 이 없는 수치 삽입)는 _dg_verify_html 게이트가 차단(→ 폴백).
# ══════════════════════════════════════════════════════════════════════════
# ★ LLM 실시간 HTML 저작은 SDK 스로틀 시 이미지당 수 분 latency → 기본 OFF (opt-in).
#   기본 경로는 pro_templates(결정론 전문 템플릿, LLM 0회, 2~6초). ERRORS [358].
_DESIGNGEN_ON = os.getenv("INFOGRAPHIC_DESIGNGEN", "0") == "1"

_DG_ART = [
    "프리미엄 금융 매거진 에디토리얼 — 딥네이비 히어로 밴드 + 골드/민트 듀오톤, 좌측 초대형 히어로 스탯. 고급·신뢰.",
    "밝은 K-블로그 프리미엄 — 크림/화이트 배경 + 코랄·틸 그라디언트, 큼직한 라운드 카드와 곡선 모티프. 친근하지만 정교.",
    "모던 데이터 저널리즘 — 화이트 배경 + 단일 딥컬러 강조, 굵은 타이포 위계와 얇은 헤어라인, 절제된 미니멀.",
    "다크 대시보드 프리미엄 — 차콜/딥블루 배경 + 네온 액센트 1색, 글래스 카드와 발광 포인트, 미래적.",
    "웜 파스텔 인포그래픽 — 아이보리/피치 배경 + 딥틸·머스타드, 둥근 기하 모티프와 친근한 인라인 아이콘.",
]

_DG_FEWSHOT = """<!-- 참고 구조 예시 (품질·구성 수준의 하한선. 그대로 베끼지 말고 이 수준 이상으로) -->
<div style="width:1280px;background:#eef2f8;font-family:'Noto Sans KR',sans-serif">
  <div style="padding:52px 64px;background:linear-gradient(135deg,#0a1730,#16345f);position:relative;overflow:hidden">
    <div style="display:inline-flex;gap:9px;padding:8px 16px;border:1px solid rgba(245,184,41,.4);border-radius:999px;color:#ffd466;font-size:15px;font-weight:700">● 리포트 라벨</div>
    <h1 style="margin:20px 0 10px;color:#fff;font-size:56px;font-weight:900;letter-spacing:-.02em">임팩트 있는 제목</h1>
    <div style="color:#a9bad6;font-size:19px">부제 · 기간</div>
    <div style="display:flex;gap:26px;margin-top:38px">
      <div style="flex:1;padding:26px 28px;border-radius:20px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09)">
        <div style="color:#cdd8ec;font-weight:700">항목 A</div>
        <div style="font-size:76px;font-weight:900;color:#ffce54">＋10.2<span style="font-size:40px">%</span></div>
        <div style="color:#9fb0cc">실제값 맥락</div>
      </div>
      <div style="flex:1;padding:26px 28px;border-radius:20px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09)">
        <div style="color:#cdd8ec;font-weight:700">항목 B</div>
        <div style="font-size:76px;font-weight:900;color:#37d6cf">＋7.1<span style="font-size:40px">%</span></div>
      </div>
    </div>
  </div>
  <div style="padding:40px 64px">
    <div style="background:#fff;border-radius:24px;padding:34px 38px;box-shadow:0 18px 50px rgba(18,42,83,.10)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:14px;align-items:center"><div style="width:34px;height:34px;border-radius:10px;background:#0f1b33;color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center">01</div><h2 style="font-size:24px;font-weight:800;color:#0f1b33">차트 제목</h2></div>
        <div style="font-size:15px;color:#37476a;font-weight:700">범례 · 축 설명</div>
      </div>
      <svg width="100%" viewBox="0 0 960 330"><!-- 인라인 SVG 차트: 그라디언트 area + 라인 + 축라벨 + 끝점 강조 + 주석 --></svg>
    </div>
    <div style="display:flex;gap:20px;margin-top:22px">
      <div style="flex:1;background:#fff;border-radius:18px;padding:22px 24px;border:1px solid #e7ecf5"><div style="color:#64748b;font-size:15px">라벨</div><div style="font-size:30px;font-weight:900;color:#0f1b33">값</div></div>
    </div>
  </div>
  <div style="padding:20px 64px 30px;display:flex;justify-content:space-between;color:#8b98af;font-size:14px"><span>데이터 출처 · ...</span><span style="font-weight:800;color:#0f1b33">JARVIS · 데이터 인사이트</span></div>
</div>"""

_DG_RUBRIC = """너는 세계 최정상급 편집 인포그래픽 아트디렉터다 (Bloomberg Graphics / 뉴욕타임스 그래픽 / Information is Beautiful 수준).
아래 *실데이터* 로 전문 디자이너가 만든 프리미엄 인포그래픽 1장을 완결 HTML 로 저작한다.

[품질 기준 — 전부 충족]
1. 컨셉: 데이터를 카드에 나열만 하지 말 것. 하나의 시각적 스토리(히어로 스탯→근거→맥락).
2. 타이포 위계: 디스플레이급 초대형 숫자(80px+)·굵기 대비·아이브로우 라벨. 숫자가 디자인 요소.
3. 색 시스템: 단색 flat 금지. 주색1+강조1~2+그라디언트/듀오톤. 여러 시리즈는 서로 다른 색. 배경도 미묘한 그라디언트.
4. 구도: 비대칭 균형·명확한 포컬포인트·의도적 여백. 죽은 여백 금지.
5. 데이터-잉크: 차트에 직접 라벨·시작/끝점 강조·핵심 주석(annotation)·비교 프레이밍. 범례 의존 최소.
6. 장식: 주제 연관 인라인 SVG 아이콘·기하 모티프·번호칩·구분선 등 일관 장식 언어. 과하지 않게.
7. 깊이: 레이어링·부드러운 그림자·카드 elevation·유리질감 절제.
8. 편집 완성도: 출처 푸터·일관 spacing·정렬 규율.

[아트디렉션] __ART__

[데이터 정확성 — 절대]
- 아래 데이터의 수치만 사용. 어떤 숫자도 새로 지어내지 말 것. (증감률·합계·평균·최대/최소는 이 데이터로 산출 가능한 것만.)
- 차트 선/막대/도넛의 길이·각도·좌표는 실제 값에 비례. 시간축은 과거→최근 좌→우.

[기술 규격]
- 출력: 완결 HTML 하나만. 설명·마크다운·코드펜스 금지. <!DOCTYPE html> 로 시작 </html> 로 끝.
- 루트 컨테이너 width 정확히 1280px, 배경 흰색. 높이는 내용에 맞게.
- 폰트: @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;800;900&display=swap'); font-family:'Noto Sans KR'. 무게 900 사용 가능.
- 차트는 인라인 SVG 로 직접 그려라 (외부 라이브러리·이미지·JS 금지). CSS 는 인라인/<style> 만. 전부 self-contained (아이콘=인라인 SVG path).

[제목] __TITLE__
[부제] __SUB__
[글 맥락] __CTX__

[데이터 — 아래 수치만 사용]
__DATA__

__FEWSHOT__

이제 위 기준을 전부 충족하는 완결 HTML 을 저작하라. HTML 만 출력."""


def _dg_data_block(datasets) -> str:
    lines = []
    for i, ds in enumerate(datasets):
        kind = _infer_kind(ds)
        unit = ds.get("unit", "")
        title = ds.get("title", f"시리즈{i+1}")
        pairs = " | ".join(f"{r.get('label')}={_fmt(r.get('value'))}"
                           for r in (ds.get("data") or []) if r.get("label") is not None)
        lines.append(f'· "{title}" (단위:{unit or "—"}, 유형:{kind}): {pairs}')
    return "\n".join(lines)


def _dg_allowed(datasets):
    """실데이터 원본값 + 파생값(최소·최대·합·평균·증감·증감률·쌍차) 집합 — grounding 대조군."""
    vals, raw = set(), []
    for ds in datasets:
        nums = []
        for r in ds.get("data") or []:
            try:
                nums.append(float(str(r.get("value")).replace(",", "")))
            except (TypeError, ValueError):
                continue
        raw += nums
        if nums:
            vals.update([min(nums), max(nums), sum(nums), sum(nums) / len(nums),
                         float(len(nums)), nums[0], nums[-1], nums[-1] - nums[0]])
            if nums[0]:
                vals.add((nums[-1] - nums[0]) / abs(nums[0]) * 100.0)
            for a in nums:
                vals.add(a)
                for b in nums:
                    vals.add(abs(a - b))
    return vals, raw


def _dg_verify_html(html, datasets) -> bool:
    """LLM 저작 HTML 의 *표시 텍스트* 수치가 실데이터·파생값에 grounding 되는지 검증.

    좌표(attribute)는 검사 안 함 — '>텍스트<' 노드(SVG <text> 포함)의 수치만 대상.
    스캐폴딩(0~100 정수·연도·데이터 범위 내 축눈금)은 허용. 조작 과다 시 False → 폴백.

    ★ <style>/<script> 블록은 표시 텍스트가 아니므로 스캔에서 제외 (사용자 박제 2026-07-05):
      template_engine.render_layout 이 팔레트 hex 를 <style>:root{--hero0:#14171c;…}</style>
      로 주입하는데, 이 CSS hex(#14171c→14171 등)가 '>…<' 노드로 잡혀 데이터로 오인되면
      *모든* 레이아웃 템플릿이 SAFE=False → design_learner._test_render 가 전량 폐기(feature dead).
    """
    from JARVIS09_COLLECTOR.models import grounds as _grounds
    allowed, raw = _dg_allowed(datasets)
    dmin = min(raw) if raw else 0.0
    dmax = max(raw) if raw else 0.0
    scan = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    nums = []
    for t in re.findall(r">([^<]+)<", scan):
        for tok in re.findall(r"-?\d[\d,]*\.?\d*", t):
            try:
                nums.append(float(tok.replace(",", "")))
            except ValueError:
                continue
    if not nums:
        return True
    def _ok(n):
        an = abs(n)
        if n == int(n) and 0 <= int(n) <= 100:
            return True                                    # 축·스케일·퍼센트·카운트 스캐폴딩
        if n == int(n) and 2000 <= n <= 2100:
            return True                                    # 연도
        if dmax > 0 and dmin * 0.7 <= an <= dmax * 1.3:
            return True                                    # 데이터 범위 내 축 눈금
        return any(_grounds(n, a) for a in allowed)
    bad = [n for n in nums if not _ok(n)]
    if bad:
        log.warning(f"[designgen] grounding 실패 수치 {bad[:6]} (총 {len(nums)}개 중 {len(bad)})")
    return len(bad) <= 2 and (len(bad) / len(nums)) <= 0.20


def _designgen(title, subtitle, datasets, out_path, context, seed) -> str:
    """LLM design-generation → 수치 검증 → Chromium 렌더. 성공 시 경로, 실패 시 "" (→ render_spec 폴백)."""
    if not _DESIGNGEN_ON or not datasets:
        return ""
    try:
        from shared.llm import invoke_text
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
    except Exception:
        return ""
    art = _DG_ART[seed % len(_DG_ART)]
    prompt = (_DG_RUBRIC.replace("__ART__", art)
              .replace("__TITLE__", str(title))
              .replace("__SUB__", str(subtitle))
              .replace("__CTX__", str(context)[:600])
              .replace("__DATA__", _dg_data_block(datasets))
              .replace("__FEWSHOT__", _DG_FEWSHOT))
    # ★ 하드 예산(fast-fail) — SDK 스로틀 시 재시도 지옥(4×200s) 대신 즉시 폴백.
    #   짧은 timeout + _retries=3(재시도 상한 통일 — 사용자 박제 2026-07-06, 원래는
    #   단일 시도(_retries=1)로 낮춰 latency 를 아꼈으나 재시도 상한 3회 통일 원칙 적용).
    #   invoke_text 회로차단기가 연속 스로틀 시 "" 즉시 반환 → 발행 지연 0.
    #   실패는 곧 render_spec(안전 폴백)이라 손해 없음.
    try:
        raw = invoke_text("writer", prompt, max_tokens=7000, timeout=110, _retries=3)
        if not raw:
            log.info("[designgen] LLM 저작 미수신(스로틀/타임아웃) → render_spec 폴백")
            return ""
        m = (re.search(r"(<!DOCTYPE html>.*?</html>)", raw, re.S | re.I)
             or re.search(r"(<html.*?</html>)", raw, re.S | re.I))
        if not m:
            log.info("[designgen] HTML 추출 실패 → render_spec 폴백")
            return ""
        html = m.group(1)
        if not _dg_verify_html(html, datasets):
            log.warning("[designgen] 수치 검증 실패 → render_spec 폴백")
            return ""
        ok = _html_to_jpg(html, Path(out_path), width=1280)
        _p = Path(out_path)
        if ok and _p.exists() and _p.stat().st_size > 3000:
            log.info(f"[designgen] 전문가급 인포그래픽 저작 완료 (art={seed % len(_DG_ART)})")
            return str(out_path)
    except Exception as e:
        log.warning(f"[designgen] 실패 → render_spec 폴백: {e}")
        _g_report("image", e, module=__name__, func_name="_designgen")
    return ""


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
    # ★ 시간축 좌→우 강제 + 동일 수치 중복 제거 (사용자 박제 2026-07-03)
    #   prime_batch_designs 와 *동일 정규화* (_normalize_ds) — 캐시 키 정합 (ERRORS [312])
    for _d in datasets:
        _normalize_ds(_d)
    out_dir = Path(out_dir) if out_dir else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_int(run_id, slot_key, title)
    _sk = re.sub(r"[^0-9A-Za-z]", "", str(slot_key))[:10] or "s"
    out = out_dir / f"infg_{_sk}_{seed % 100000000}.jpg"

    # ★ 1순위: pro_templates (결정론 전문 템플릿, LLM 0회, 2~6초) — happy path 는 LLM 스펙조차
    #   호출 안 함(수 분 latency 제거, ERRORS [358]). 데이터만 꽂아 전문가급 즉시 렌더.
    try:
        from JARVIS06_IMAGE.pro_templates import render_pro
        _pt = render_pro(title, subtitle, datasets, seed, out, src=src, chip=chip)
        if _pt:
            return _pt
    except Exception as _pte:
        _g_report("image", _pte, module=__name__, func_name="generate_infographic")

    # 2순위(opt-in): design-generation (LLM HTML 저작 — INFOGRAPHIC_DESIGNGEN=1, 기본 OFF)
    if _DESIGNGEN_ON:
        _dg = _designgen(title, subtitle, datasets, out, context or f"{title} — {subtitle}", seed)
        if _dg:
            return _dg

    # 3순위 폴백: design-selection (작은 JSON 스펙 → 손코딩 렌더러). 여기서만 _llm_design 호출.
    spec = None
    if run_id:
        _bc = _BATCH_DESIGN_CACHE.get(run_id)
        if _bc:
            _cached = _bc.get(_ds_key(datasets[0]))
            if _cached:
                spec = dict(_cached)        # 캐시 원본 보호
    if spec is None:
        spec, _origin = _llm_design(context or f"{title} — {subtitle}", datasets, seed)
    _moods = list(_MOOD_IDX.keys())
    spec["mood"] = _moods[seed % len(_moods)]
    spec.setdefault("header", {})
    if not spec["header"].get("title"):
        spec["header"]["title"] = title
    if not spec["header"].get("subtitle"):
        spec["header"]["subtitle"] = subtitle
    if chip and not spec["header"].get("chip"):
        spec["header"]["chip"] = chip
    _r = render_spec(spec, datasets, out, seed=seed, src=src)
    if _r:
        return _r
    # 렌더 실패(레이아웃/데이터 이슈) 시에만 단일 데이터셋 규칙 폴백 (신뢰성 보장)
    if len(datasets) == 1:
        return _render_single(datasets[0], title, subtitle, out, seed, src, chip=chip, slot=slot_key)
    return ""


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


def render_table_infographic(table_html, idx=0, out_dir=None, *, title="", run_id=""):
    """HTML 표 → *인포그래픽 스타일* 이미지 (사용자 박제 2026-07-02: 모든 이미지는 인포그래픽).

    ★ 표 내용을 *그대로 보존* — 수치·라벨 변형 0 → 사실성 안전(새 수치 생성 아님 → _verify_dataset
      게이트 대상 아님). matplotlib plain 표 대신 팔레트 헤더바·라운드 카드·교차행·출처 푸터.
    실패 시 "" 반환 → 호출자(block_assembler)가 기존 plain 표 렌더러로 폴백.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    try:
        soup = BeautifulSoup(str(table_html), "html.parser")
        trs = soup.find_all("tr")
        if not trs:
            return ""

        def _ct(el):
            t = el.get_text(separator=" ", strip=True)
            t = re.sub(r"[\U0001F000-\U0001FFFF]", "", t).replace("⭐", "★").replace("\U0001F31F", "★")
            return t.strip()

        first = trs[0]
        headers = [_ct(c) for c in first.find_all(["th", "td"])]
        body_trs = trs[1:]
        rows = []
        for tr in body_trs:
            cells = tr.find_all(["td", "th"])
            row = []
            for c in cells:
                txt = _ct(c)
                col = None
                if "▲" in txt or ("+" in txt and "%" in txt):
                    col = "#e8513a"          # 상승 — 빨강 계열
                elif "▼" in txt or (txt.startswith("-") and "%" in txt):
                    col = "#1b78d6"          # 하락 — 파랑 계열
                row.append((txt, col))
            if any(t for t, _ in row):
                rows.append(row)
        if not rows or not headers:
            return ""
        ncol = max(len(headers), max(len(r) for r in rows))
        headers = (headers + [""] * ncol)[:ncol]
        rows = [(r + [("", None)] * ncol)[:ncol] for r in rows]

        seed = _seed_int(run_id, str(idx), title or (headers[0] if headers else "table"))
        pal = PALETTES[seed % len(PALETTES)]
        th = "".join(
            f"<th style='padding:14px 16px;text-align:{'left' if j == 0 else 'center'};"
            f"font-size:16px;font-weight:800;color:#fff;white-space:nowrap'>{h}</th>"
            for j, h in enumerate(headers))
        body_rows = []
        for i, row in enumerate(rows):
            bg = pal["soft"] if i % 2 == 0 else "#ffffff"
            tds = "".join(
                f"<td style='padding:13px 16px;text-align:{'left' if j == 0 else 'center'};"
                f"font-size:16px;font-weight:{'800' if j == 0 else '600'};"
                f"color:{col or ('#16202e' if j == 0 else '#46505f')};white-space:nowrap'>{txt}</td>"
                for j, (txt, col) in enumerate(row))
            body_rows.append(f"<tr style='background:{bg}'>{tds}</tr>")
        card = (f"<div style='{_CARD};padding:8px;overflow:hidden'>"
                f"<table style='width:100%;border-collapse:collapse;font-family:{FONT}'>"
                f"<thead><tr style='background:linear-gradient(135deg,{pal['c1']},{pal['c2']})'>{th}</tr></thead>"
                f"<tbody>{''.join(body_rows)}</tbody></table></div>")
        inner = (f"<div style='background:{pal['soft']};background-image:radial-gradient({pal['dot']} 1.2px,transparent 1.2px);"
                 f"background-size:22px 22px;padding:22px'>{card}</div>")
        W = 1280
        _t = str(title or (headers[0] if headers else "데이터 표"))[:24]
        html = (f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8><style>"
                f"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700;800;900&display=swap');"
                f"*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:{FONT};width:{W}px;background:#fff}}"
                f"</style></head><body><div style='width:{W}px;background:#fff'>"
                f"{_header(pal, _t, '', '', 'chart')}{inner}{_foot('데이터 출처: 본문 표 기준')}</div></body></html>")
        out_dir = Path(out_dir) if out_dir else Path(".")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"tableinfg_{idx}_{seed % 100000000}.jpg"
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out), width=W)
        _p = Path(out)
        return str(out) if (ok and _p.exists() and _p.stat().st_size > 2000) else ""
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_table_infographic")
        return ""


__all__ = ["generate_infographic", "render_infographic", "select_design",
           "generate_chart_infographic", "inject_economic_infographics",
           "render_table_infographic", "prime_batch_designs", "PALETTES"]
