#!/usr/bin/env python3
"""JARVIS Hub — 통합 시스템 현황판 (port 9199)
탭: 홈 / 레이더 / 발행 관리 / 품질 관리 / 성과 / 스케줄러 / 시스템
"""
import sys, html as _html, json, subprocess, sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "shared" / "jarvis.sqlite"
sys.path.insert(0, str(BASE_DIR))

st.set_page_config(
    page_title="JARVIS Hub",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ══════════════════════════════════════════════════════════════════
# 디자인 토큰
# ══════════════════════════════════════════════════════════════════
C = {
    "primary": "#00aaff",
    "success": "#22c55e",
    "warn":    "#ffcc00",
    "danger":  "#ff3366",
    "muted":   "#9ab8d8",
}
N = {
    "bg":    "#0a0e1a",
    "card":  "#1a2840",
    "bdr":   "#3a5a7a",
    "text":  "#d0e0f8",
    "text2": "#8aaace",
    "text5": "#5a7a9a",
}
SECTOR_COLOR = {
    "경제·경기": "#00aaff", "주식·투자": "#22c55e", "기술·IT": "#aa44ff",
    "부동산": "#ffcc00",   "스포츠": "#ff6633",   "연예·문화": "#ff3366",
    "정치·사회": "#66ccff", "건강·의료": "#44ffaa", "기타": "#9ab8d8",
}
QA_STATUS_LABEL = {
    "pending_analysis": ("⏳ 분석 대기", "#5a7a9a"),
    "analyzed":         ("🔍 분석 완료", "#00aaff"),
    "pending_approval": ("🔔 승인 대기", "#ffcc00"),
    "approved":         ("✅ 승인됨",    "#22c55e"),
    "rejected":         ("❌ 건너뜀",    "#ff3366"),
    "revised":          ("🎉 수정 완료", "#aa44ff"),
    "revise_skipped":   ("⏭ 수정 생략", "#9ab8d8"),
}
QA_PLATFORM_EMOJI = {"naver": "🟢", "tistory": "🟠"}

def _alpha(hex6: str, a: float) -> str:
    h = hex6.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{a})"

def esc(s) -> str:
    return _html.escape(str(s) if s is not None else "", quote=True)

def md(body: str):
    st.markdown(body, unsafe_allow_html=True)

# 번역 방지
st.markdown("""
<meta name="google" content="notranslate">
<script>(function(){
  document.documentElement.setAttribute('translate','no');
  document.documentElement.setAttribute('lang','ko');
})();</script>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# 전역 CSS
# ══════════════════════════════════════════════════════════════════
st.markdown(f"""<style>
  [data-testid="stAppViewContainer"] {{
    background:{N['bg']}; color:{N['text']};
    font-family:'Inter','Noto Sans KR',sans-serif; font-size:16px;
  }}
  [data-testid="stAppViewContainer"]>section.main {{ padding:16px 28px 40px; }}
  section[data-testid="stSidebar"] {{ display:none; }}
  .stTabs [data-baseweb="tab-list"] {{
    background:{N['card']}; border-radius:10px; padding:4px 8px; gap:4px;
  }}
  .stTabs [data-baseweb="tab"] {{
    border-radius:8px; font-size:16px; font-weight:600;
    padding:8px 20px; color:{N['text2']};
  }}
  .stTabs [aria-selected="true"] {{
    background:{_alpha(C['primary'],0.18)}; color:{C['primary']};
  }}
  .stTabs [data-baseweb="tab-panel"] {{ padding-top:20px; }}
  hr {{ border-color:{N['bdr']}; margin:12px 0; }}
  /* KPI 카드 hover 효과 */
  div[style*="border-top:3px solid"] {{
    transition: transform 0.18s ease, box-shadow 0.18s ease;
  }}
  div[style*="border-top:3px solid"]:hover {{
    transform: translateY(-2px);
    box-shadow: 0 6px 24px rgba(0,170,255,0.14);
  }}
  /* 티커 테이프 애니메이션 */
  @keyframes _ticker {{
    from {{ transform: translateX(0); }}
    to   {{ transform: translateX(-50%); }}
  }}
  /* 펄스 — 긴급 승인 대기 */
  @keyframes _pulse {{
    0%,100% {{ box-shadow: 0 0 0 0 rgba(255,204,0,0.45); }}
    50%      {{ box-shadow: 0 0 0 6px rgba(255,204,0,0); }}
  }}
  /* 라이브 도트 — 에이전트 온라인 인디케이터 */
  @keyframes _liveDot {{
    0%,100% {{ opacity: 1; transform: scale(1); }}
    50%      {{ opacity: 0.4; transform: scale(0.7); }}
  }}
  .live-dot {{
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    animation: _liveDot 1.6s ease-in-out infinite;
    vertical-align: middle;
    margin-right: 4px;
  }}
</style>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# 컴포넌트
# ══════════════════════════════════════════════════════════════════
def kpi(label, value, *, color="primary", sub="") -> str:
    c = C.get(color, C["primary"])
    sub_html = (f'<div style="font-size:14px;color:{N["text5"]};margin-top:6px">'
                f'{esc(sub)}</div>') if sub else ""
    return (
        f'<div style="background:{_alpha(c,.05)};border:1px solid {_alpha(c,.25)};'
        f'border-top:3px solid {c};border-radius:12px;padding:16px 16px 12px">'
        f'<div style="font-size:14px;color:{N["text2"]};letter-spacing:1px;margin-bottom:6px">'
        f'{esc(label)}</div>'
        f'<div style="font-size:28px;font-weight:800;color:{c};line-height:1">'
        f'{esc(str(value))}</div>'
        f'{sub_html}</div>'
    )

def agent_card(name, status, desc, *, color="primary") -> str:
    c = C.get(color, C["primary"])
    if status == "online":
        dot_html = f'<span class="live-dot" style="background:{C["success"]}"></span>'
        status_label = f'<span style="font-size:14px;color:{C["success"]};font-weight:600">LIVE</span>'
    elif status == "warn":
        dot_html = f'<span class="live-dot" style="background:{C["warn"]}"></span>'
        status_label = f'<span style="font-size:14px;color:{C["warn"]};font-weight:600">경고</span>'
    else:
        dot_html = f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{C["danger"]};vertical-align:middle;margin-right:4px"></span>'
        status_label = f'<span style="font-size:14px;color:{C["danger"]};font-weight:600">정지</span>'
    return (
        f'<div style="background:{N["card"]};border:1px solid {N["bdr"]};'
        f'border-left:4px solid {c};border-radius:10px;padding:16px 18px">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
        f'<span style="font-size:16px;font-weight:700;color:{N["text"]}">{esc(name)}</span>'
        f'<span>{dot_html}{status_label}</span></div>'
        f'<div style="font-size:14px;color:{N["text2"]};line-height:1.7">{desc}</div>'
        f'</div>'
    )

def table(headers, rows, max_rows=25) -> str:
    th = "".join(
        f'<th style="font-size:14px;color:{N["text2"]};font-weight:600;padding:8px 12px;'
        f'text-align:left;border-bottom:1px solid {N["bdr"]};white-space:nowrap">{esc(h)}</th>'
        for h in headers
    )
    trs = ""
    for i, row in enumerate(rows[:max_rows]):
        bg = f"background:{_alpha('#ffffff',.02)};" if i % 2 == 0 else ""
        tds = "".join(
            f'<td style="font-size:14px;color:{N["text"]};padding:8px 12px;'
            f'border-bottom:1px solid {_alpha(N["bdr"],.4)}">{cell}</td>'
            for cell in row
        )
        trs += f'<tr style="{bg}">{tds}</tr>'
    return (
        f'<div style="overflow-x:auto;background:{N["card"]};border:1px solid {N["bdr"]};'
        f'border-radius:10px;margin-top:8px">'
        f'<table style="width:100%;border-collapse:collapse">'
        f'<thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'
    )

def section(title, color="primary"):
    c = C.get(color, C["primary"])
    md(f'<div style="font-size:20px;font-weight:700;color:{c};margin:24px 0 12px;'
       f'border-left:4px solid {c};padding-left:12px">{esc(title)}</div>')

def badge(text, color="primary") -> str:
    c = C.get(color, C["primary"])
    return (
        f'<span style="background:{_alpha(c,.15)};color:{c};font-size:14px;font-weight:600;'
        f'padding:2px 10px;border-radius:9999px;border:1px solid {_alpha(c,.35)}">'
        f'{esc(text)}</span>'
    )

def ok_badge(v) -> str:
    return badge("성공", "success") if v else badge("실패", "danger")

def sector_badge(sector) -> str:
    c = SECTOR_COLOR.get(sector, C["muted"])
    return (f'<span style="background:{_alpha(c,.15)};color:{c};font-size:14px;'
            f'padding:2px 8px;border-radius:6px">{esc(sector)}</span>')

def qa_status_badge(status: str) -> str:
    label, color = QA_STATUS_LABEL.get(status, ("?", "#5a7a9a"))
    return (f'<span style="background:{color}22;color:{color};font-size:14px;'
            f'font-weight:600;padding:2px 8px;border-radius:4px">{esc(label)}</span>')

def ticker_tape(keywords: list) -> str:
    """상위 키워드 라이브 티커 테이프."""
    if not keywords:
        return ""
    items = ""
    for k in keywords[:20]:
        score = k.get("opportunity_score", 0) or 0
        sector = k.get("sector", "기타")
        c = SECTOR_COLOR.get(sector, C["muted"])
        items += (
            f'<span style="font-size:14px;font-weight:600;color:{N["text"]};padding:0 16px">'
            f'<span style="color:{c}">▪</span> {esc(k.get("keyword",""))} '
            f'<span style="color:{N["text5"]};font-size:14px">({score:.0f})</span></span>'
            f'<span style="color:{N["bdr"]}">·</span>'
        )
    items = items * 2  # 루프 연결용 2배 복제
    return (
        f'<div style="height:36px;overflow:hidden;background:{_alpha("#000814",.6)};'
        f'border:1px solid {N["bdr"]};border-radius:8px;display:flex;align-items:center;'
        f'margin-bottom:16px;backdrop-filter:blur(8px)">'
        f'<div style="flex-shrink:0;font-size:14px;font-weight:800;color:{C["primary"]};'
        f'padding:0 12px;border-right:1px solid {N["bdr"]};letter-spacing:1px;white-space:nowrap">'
        f'LIVE</div>'
        f'<div style="overflow:hidden;flex:1">'
        f'<div style="display:inline-flex;align-items:center;white-space:nowrap;'
        f'animation:_ticker 50s linear infinite">{items}</div>'
        f'</div></div>'
    )


# ══════════════════════════════════════════════════════════════════
# 에이전트 사무실 뷰 (홈 탭 메인 비주얼)
# 주의: Streamlit unsafe_allow_html 은 url(#id) 참조를 처리 못함
#       → gradient/filter/pattern 전부 제거, solid color + stroke 만 사용
# ══════════════════════════════════════════════════════════════════
def _office_view_html(status_map: dict, info_map: dict) -> str:  # noqa: C901
    """9개 에이전트 — 탑-다운 사무실 v5 (실제 연결 31개 검증 + 사무실 배경 + 파티클).
    viewBox 860x510 · url(#id) 0 — Streamlit solid SVG 전용.
    연결: 코드 grep 검증 (cross-agent import 31개 실측).
    """
    import math

    def _sc(s):
        return {"online": "#22c55e", "warn": "#ffcc00", "offline": "#ff3366"}.get(s, "#9ab8d8")

    # ── 에이전트 배치 ─────────────────────────────────────────────
    #  J03 RADAR  |  J00 INFRA  |  J02 WRITER  |  J09 COLLECTOR
    #  J05 VISION |  J01 MASTER |  J04 SCHED
    #  J06 IMAGE  |  J07 GUARD  |  J08 PUBLISH
    # 세로: 위로 당겨 배치 / 열: 265/390/515/640 / 행: 130/238/345
    AGENTS = [
        ("j03", "J03 RADAR",   "트렌드 레이더", "#ffcc00", "📡",  265, 130),
        ("j00", "J00 INFRA",   "인프라 관리자", "#22c55e", "⚙️",  390, 130),
        ("j02", "J02 WRITER",  "블로그 라이터", "#aa44ff", "✍️",  515, 130),
        ("j09", "J09 COLLECTOR","수집 정제",    "#00ddbb", "🕸️",  640, 130),
        ("j05", "J05 VISION",  "비전 모니터",   "#44ffaa", "👁️",  265, 238),
        ("j01", "J01 MASTER",  "마스터 라우터", "#00aaff", "🧠",  390, 238),
        ("j04", "J04 SCHED",   "작업 스케줄러", "#ff6633", "⏰",  515, 238),
        ("j06", "J06 IMAGE",   "이미지 생성",   "#ff88cc", "🎨",  265, 345),
        ("j07", "J07 GUARD",   "오류 수호자",   "#ff9900", "🛡️",  390, 345),
        ("j08", "J08 PUBLISH", "발행 관리자",   "#00ccff", "🚀",  515, 345),
    ]
    # ── 실제 연결 (코드 grep 검증 31개) ──────────────────────────
    # (from, to, color, type, opacity, stroke_w)
    # 라우팅: 같은열=수직직선, 같은행=수평직선, 교차=V→H→V 3단꺾기
    CONNS = [
        # ─── Master dispatch (j01→) 파란색
        ("j01","j00","#00aaff","dispatch",0.70,2.0),
        ("j01","j02","#00aaff","dispatch",0.72,2.0),
        ("j01","j04","#00aaff","dispatch",0.70,2.0),
        ("j01","j07","#00aaff","dispatch",0.50,1.6),
        # ─── Infra (j00→) 초록
        ("j00","j01","#22c55e","infra",0.60,1.8),
        ("j00","j03","#22c55e","infra",0.58,1.6),
        ("j00","j04","#22c55e","infra",0.55,1.6),
        ("j00","j07","#22c55e","infra",0.38,1.2),
        # ─── Writer pipeline (j02→) 보라
        ("j02","j00","#aa44ff","pipeline",0.50,1.4),
        ("j02","j01","#aa44ff","pipeline",0.55,1.6),
        ("j02","j03","#aa44ff","pipeline",0.52,1.6),
        ("j02","j06","#aa44ff","pipeline",0.58,1.8),
        ("j02","j07","#aa44ff","pipeline",0.38,1.2),
        ("j02","j08","#aa44ff","pipeline",0.62,1.8),
        # ─── Radar (j03→) 노랑
        ("j03","j00","#ffcc00","radar",0.52,1.6),
        ("j03","j02","#ffcc00","radar",0.50,1.6),
        ("j03","j04","#ffcc00","radar",0.45,1.4),
        ("j03","j07","#ffcc00","radar",0.36,1.2),
        ("j03","j08","#ffcc00","radar",0.42,1.4),
        # ─── Scheduler (j04→) 주황
        ("j04","j07","#ff6633","sched",0.58,1.8),
        # ─── Vision (j05→) 민트
        ("j05","j01","#44ffaa","monitor",0.48,1.4),
        ("j05","j03","#44ffaa","monitor",0.44,1.4),
        ("j05","j04","#44ffaa","monitor",0.44,1.4),
        ("j05","j07","#44ffaa","monitor",0.34,1.2),
        # ─── Image (j06→) 핑크
        ("j06","j02","#ff88cc","image",0.56,1.8),
        ("j06","j07","#ff88cc","image",0.40,1.2),
        # ─── Guardian fix (j07→) 주황빨
        ("j07","j00","#ff9900","fix",0.48,1.4),
        ("j07","j04","#ff9900","fix",0.46,1.4),
        # ─── Publish feedback (j08→) 시안
        ("j08","j00","#00ccff","publish",0.46,1.4),
        ("j08","j02","#00ccff","publish",0.50,1.6),
        ("j08","j07","#00ccff","publish",0.40,1.2),
        # ─── Collector (j09→) 민트그린
        ("j03","j09","#00ddbb","collect",0.60,1.8),   # RADAR → COLLECTOR 주제 전달
        ("j09","j02","#00ddbb","collect",0.62,1.8),   # COLLECTOR → WRITER 수집 결과
        ("j09","j07","#00ddbb","collect",0.38,1.2),   # 오류 보고
    ]
    pos = {aid: (cx, cy) for aid, _, _, _, _, cx, cy in AGENTS}

    # ── 직선 라우팅 레인 자동 계산 ────────────────────────────────
    # 경유 채널: row1↔row2 갭 중심 y=184, row2↔row3 갭 중심 y=292
    HC12, HC23 = 184, 292
    from collections import defaultdict
    _v_grp   = defaultdict(list)   # x    → [idx]  (같은 열)
    _h_grp   = defaultdict(list)   # y    → [idx]  (같은 행)
    _lhc_grp = defaultdict(list)   # hc_y → [idx]  (L자 교차)
    _conn_hc = {}
    for _i, (_fid, _tid, *_) in enumerate(CONNS):
        _fx, _fy = pos[_fid]; _tx, _ty = pos[_tid]
        if abs(_fx - _tx) < 2:
            _v_grp[round(_fx)].append(_i)
        elif abs(_fy - _ty) < 2:
            _h_grp[round(_fy)].append(_i)
        else:
            _hc = HC12 if _fy <= 130 else (HC23 if _fy >= 345 else (HC12 if _fy > _ty else HC23))
            if _fy == 238 and _ty < _fy: _hc = HC12   # row2→row1
            if _fy == 238 and _ty > _fy: _hc = HC23   # row2→row3
            if _fy == 345: _hc = HC23                  # row3→위
            _conn_hc[_i] = _hc
            _lhc_grp[_hc].append(_i)
    _v_off = {}; _h_off = {}; _hc_off = {}
    for _x, _idxs in _v_grp.items():
        _n = len(_idxs)
        for _j, _i in enumerate(_idxs): _v_off[_i]  = round((_j-(_n-1)/2)*3)
    for _y, _idxs in _h_grp.items():
        _n = len(_idxs)
        for _j, _i in enumerate(_idxs): _h_off[_i]  = round((_j-(_n-1)/2)*3)
    for _hcy, _idxs in _lhc_grp.items():
        _n = len(_idxs)
        for _j, _i in enumerate(_idxs): _hc_off[_i] = round((_j-(_n-1)/2)*1.5)

    p = []

    # ── SVG 시작 ──────────────────────────────────────────────────
    p.append(
        '<div style="width:100%;overflow:hidden;border-radius:12px;'
        'box-shadow:0 8px 40px rgba(0,0,0,0.85)">'
        '<svg viewBox="0 0 860 510" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;display:block;border-radius:12px">'
    )
    # ── 연결선 클립패스: 에이전트 박스 내부는 선 미노출 ───────────────
    _cp_d = "M0,0 H860 V510 H0 Z"
    for _, _, _, _, _, _acx, _acy in AGENTS:
        _cp_d += f" M{_acx-35},{_acy-18} H{_acx+35} V{_acy+60} H{_acx-35} Z"
    p.append(f'<defs><clipPath id="conn-clip"><path fill-rule="evenodd" d="{_cp_d}"/></clipPath></defs>')

    # ── 사무실 배경 (밝은 오피스 톤) ────────────────────────────────
    # 기본 배경 — 중간 톤 네이비
    p.append('<rect width="860" height="510" fill="#101c30" rx="12"/>')

    # 천장 — 밝은 회청색
    p.append('<rect x="0" y="0" width="860" height="44" fill="#1e3050" rx="12"/>')
    p.append('<rect x="0" y="34" width="860" height="10" fill="#1e3050"/>')

    # 천장 조명 패널 3개 — 눈에 띄는 밝은 아이보리
    for lx in [90, 360, 630]:
        p.append(
            f'<rect x="{lx}" y="5" width="140" height="22" rx="5" fill="#2a4870" stroke="#4a78a0" stroke-width="1"/>'
            f'<rect x="{lx+5}" y="8" width="130" height="16" rx="3" fill="#e8f0ff" opacity="0.35"/>'
            f'<rect x="{lx+5}" y="8" width="130" height="16" rx="3" fill="#aad0ff" opacity="0.20"/>'
        )
        # 조명 아래 반사광 — 더 밝고 넓게
        for bx, ex in [(lx+20, lx+8), (lx+70, lx+70), (lx+120, lx+132)]:
            p.append(f'<line x1="{bx}" y1="28" x2="{ex}" y2="100" stroke="#90c8f0" stroke-width="1.2" opacity="0.14"/>')

    # 바닥 영역 (하단 65px) — 따뜻한 목재 마루
    p.append('<rect x="0" y="445" width="860" height="65" fill="#1a1208"/>')
    for fy in range(450, 508, 6):
        opac = 0.4 + (fy - 450) * 0.015
        p.append(f'<line x1="0" y1="{fy}" x2="860" y2="{fy}" stroke="#3a2818" stroke-width="0.9" opacity="{opac:.2f}"/>')
    # 마루 세로 이음새
    for fx in range(80, 860, 120):
        p.append(f'<line x1="{fx}" y1="445" x2="{fx+10}" y2="510" stroke="#2a1e0e" stroke-width="0.6" opacity="0.5"/>')

    # 메인 작업 영역 — 구역별 미묘한 색 차이 (격자/선 없음)
    # 열 중간: 377, 482 / 행 중간: (130+238)/2=184, (238+345)/2=292
    _zone_cols = ["#121e30", "#111d2e", "#131f32"]
    for _row, (zy1, zy2) in enumerate([(44, 184), (184, 292), (292, 447)]):
        for _col, (zx1, zx2) in enumerate([(0, 377), (377, 482), (482, 860)]):
            _zc = _zone_cols[_col]
            p.append(f'<rect x="{zx1}" y="{zy1}" width="{zx2-zx1}" height="{zy2-zy1}" fill="{_zc}"/>')

    # 좌측 창문 — 밝은 하늘색 (눈에 띄게)
    p.append('<rect x="0" y="50" width="10" height="260" fill="#1a3860" stroke="#2a5898" stroke-width="1"/>')
    for wi in range(8):
        wy = 56 + wi * 30
        p.append(
            f'<rect x="1" y="{wy}" width="8" height="22" rx="1" fill="#5090d0" opacity="{0.30 - wi*0.02:.2f}"/>'
            f'<rect x="1" y="{wy}" width="8" height="22" rx="1" fill="#90c8ff" opacity="{0.15 - wi*0.01:.2f}"/>'
        )
    # 창문 빛 기둥
    for wi in range(3):
        p.append(f'<rect x="8" y="{70+wi*80}" width="{20+wi*5}" height="15" fill="#4080c0" opacity="0.07"/>')

    # 우측 서버 랙 — 크고 눈에 띄는
    p.append(
        '<rect x="842" y="46" width="18" height="135" rx="2" fill="#1a2c44" stroke="#3a5878" stroke-width="1.2"/>'
        '<rect x="844" y="48" width="14" height="131" rx="1" fill="#0e1e30"/>'
    )
    for ri in range(12):
        ry = 50 + ri * 11
        dot_col = ["#22c55e","#ffcc00","#22c55e","#ff3366","#22c55e","#ffcc00",
                   "#22c55e","#22c55e","#ffcc00","#22c55e","#ff3366","#22c55e"][ri]
        p.append(
            f'<rect x="845" y="{ry}" width="10" height="7" rx="1" fill="#162030" stroke="#2a3e58" stroke-width="0.4"/>'
            f'<circle cx="850" cy="{ry+3}" r="2" fill="{dot_col}" opacity="0.9"/>'
        )

    # ── 헤더 바 ───────────────────────────────────────────────────
    p.append(
        '<rect width="860" height="36" fill="#182e4a" rx="12"/>'
        '<rect y="26" width="860" height="10" fill="#182e4a"/>'
        '<circle cx="18" cy="18" r="5" fill="#ff3366"/>'
        '<circle cx="36" cy="18" r="5" fill="#ffcc00"/>'
        '<circle cx="54" cy="18" r="5" fill="#22c55e"/>'
        '<text x="430" y="23" text-anchor="middle" '
        'font-family="Inter,AppleSDGothicNeo,sans-serif" '
        'font-size="14" font-weight="700" fill="#c8daf0">'
        '🏢 JARVIS 에이전트 사무실</text>'
        '<text x="828" y="23" text-anchor="end" '
        'font-family="Inter,sans-serif" font-size="10" font-weight="600" fill="#22c55e">LIVE</text>'
        '<circle cx="840" cy="19" r="4" fill="#22c55e">'
        '<animate attributeName="opacity" values="1;0.25;1" dur="2s" repeatCount="indefinite"/>'
        '</circle>'
    )

    # ── 미션 보드 ────────────────────────────────────────────────
    p.append(
        '<rect x="330" y="40" width="200" height="40" rx="4" fill="#0d1e30" stroke="#1a2e48" stroke-width="1"/>'
        '<text x="430" y="56" text-anchor="middle" '
        'font-family="Inter,AppleSDGothicNeo,sans-serif" '
        'font-size="9" font-weight="700" fill="#7aa8d0">📋 JARVIS MISSION BOARD</text>'
        '<line x1="345" y1="61" x2="515" y2="61" stroke="#182c44" stroke-width="0.7"/>'
        '<text x="430" y="72" text-anchor="middle" '
        'font-family="Inter,AppleSDGothicNeo,sans-serif" '
        'font-size="7.5" fill="#6090b0">자동화 · 트렌드 · 자가학습 · Self-Evolving v3</text>'
    )

    # ── 직선 연결선 렌더 (L자 라우팅 — 사선 없음) ──────────────────
    def _seg_path(i, fid, tid):
        """경로·화살촉 시작점 반환. 같은열=수직, 같은행=수평, 교차=V→H→V."""
        fx, fy = pos[fid]; tx, ty = pos[tid]
        if abs(fx - tx) < 2:                           # 같은 열 → 수직
            x = fx + _v_off.get(i, 0)
            return (f"M{x:.0f},{fy:.0f} L{x:.0f},{ty:.0f}",
                    x, (fy+ty)/2, x, ty)
        if abs(fy - ty) < 2:                           # 같은 행 → 수평
            y = fy + _h_off.get(i, 0)
            return (f"M{fx:.0f},{y:.0f} L{tx:.0f},{y:.0f}",
                    (fx+tx)/2, y, tx, y)
        # 교차 → V↓ 경유채널 → H → V↓ 목적지
        hc_y = _conn_hc.get(i, HC12) + _hc_off.get(i, 0)
        path = (f"M{fx:.0f},{fy:.0f} "
                f"L{fx:.0f},{hc_y:.0f} "
                f"L{tx:.0f},{hc_y:.0f} "
                f"L{tx:.0f},{ty:.0f}")
        return path, tx, (hc_y + ty) / 2, tx, ty

    def _arw(sx, sy, ex, ey, color, opacity):
        """직선 방향 화살촉."""
        dx, dy = ex - sx, ey - sy
        d = math.sqrt(dx*dx + dy*dy) or 1
        ux, uy = dx/d, dy/d
        px, py = -uy, ux
        bx, by = ex - ux*8, ey - uy*8
        pts = (f"{ex:.1f},{ey:.1f} "
               f"{bx+px*4:.1f},{by+py*4:.1f} "
               f"{bx-px*4:.1f},{by-py*4:.1f}")
        return f'<polygon points="{pts}" fill="{color}" opacity="{opacity:.2f}"/>'

    _SPEED = {"dispatch":1.3,"infra":2.0,"pipeline":1.6,"radar":2.2,
              "sched":1.5,"monitor":2.8,"image":1.8,"fix":2.1,"publish":1.9}

    # ── 연결선 (클립 적용 — 박스 내부 미노출) ─────────────────────
    p.append('<g clip-path="url(#conn-clip)">')
    for i, (fid, tid, col, ctype, opac, sw) in enumerate(CONNS):
        path, asx, asy, aex, aey = _seg_path(i, fid, tid)
        dur   = f"{_SPEED.get(ctype, 1.8) + (i % 3) * 0.2:.1f}s"
        begin = f"{(i * 0.17) % 2.5:.2f}s"
        p.append(f'<path d="{path}" fill="none" stroke="{col}" '
                 f'stroke-width="{sw*2.5:.1f}" opacity="{opac*0.10:.2f}"/>')
        p.append(f'<path d="{path}" fill="none" stroke="{col}" '
                 f'stroke-width="{sw:.1f}" opacity="{opac:.2f}"/>')
        p.append(f'<circle r="2.5" fill="{col}" opacity="{min(opac+0.2, 0.95):.2f}">'
                 f'<animateMotion path="{path}" dur="{dur}" begin="{begin}" '
                 f'repeatCount="indefinite"/></circle>')
    p.append('</g>')
    # ── 박스 벽 접속 도트 + 화살촉 (클립 제외 — 항상 표시) ──────────
    for i, (fid, tid, col, ctype, opac, sw) in enumerate(CONNS):
        path, asx, asy, aex, aey = _seg_path(i, fid, tid)
        fx, fy = pos[fid]; tx, ty = pos[tid]
        # 목적지 박스 벽 접속점
        if abs(fx - tx) < 2:   # 같은 열 → 수직
            wx, wy = tx + _v_off.get(i, 0), (ty - 18 if fy < ty else ty + 60)
        elif abs(fy - ty) < 2: # 같은 행 → 수평
            wx, wy = (tx - 35 if fx > tx else tx + 35), ty + _h_off.get(i, 0)
        else:                   # L자 → 마지막 구간 수직
            _hcy = _conn_hc.get(i, HC12) + _hc_off.get(i, 0)
            wx, wy = tx, (ty - 18 if _hcy < ty else ty + 60)
        p.append(f'<circle cx="{wx:.0f}" cy="{wy:.0f}" r="3.5" fill="{col}" opacity="{opac*0.85:.2f}"/>')
        p.append(f'<circle cx="{wx:.0f}" cy="{wy:.0f}" r="6" fill="{col}" opacity="{opac*0.15:.2f}"/>')
        p.append(_arw(asx, asy, aex, aey, col, opac * 1.1))

    # ── 에이전트 데스크 + 로봇 ──────────────────────────────────
    for aid, name, role, color, emoji, cx, cy in AGENTS:
        stat  = status_map.get(aid, "online")
        scol  = _sc(stat)
        l1    = info_map.get(aid, {}).get("line1", "")
        pulse = "2s" if stat == "online" else "0.5s"

        # ── 카드 (솔리드 배경으로 연결선 차단) ────────────────────────
        p.append(
            # 솔리드 배경 (연결선 비침 방지)
            f'<rect x="{cx-35}" y="{cy-18}" width="70" height="78" rx="4" fill="#0d1b2e"/>'
            # 컬러 오버레이
            f'<rect x="{cx-35}" y="{cy-18}" width="70" height="78" rx="4" '
            f'fill="{color}" opacity="0.08"/>'
            # 테두리
            f'<rect x="{cx-35}" y="{cy-18}" width="70" height="78" rx="4" '
            f'fill="none" stroke="{color}" stroke-width="1.5" opacity="0.90"/>'
            # 상단 컬러 바
            f'<rect x="{cx-35}" y="{cy-18}" width="70" height="4" rx="3" '
            f'fill="{color}" opacity="0.60"/>'
        )
        # 상태 LED (우상단)
        p.append(
            f'<circle cx="{cx+30}" cy="{cy-14}" r="3" fill="{scol}">'
            f'<animate attributeName="opacity" values="1;0.35;1" dur="{pulse}" repeatCount="indefinite"/>'
            f'</circle>'
            f'<circle cx="{cx+30}" cy="{cy-14}" r="5" fill="{scol}" opacity="0.14"/>'
        )

        # ── 컴퓨터 (수직 고정, 우측 배치) ───────────────────────────
        # 모니터 (수직 직사각형 — 회전 없음)
        p.append(
            f'<rect x="{cx+10}" y="{cy-2}" width="20" height="16" rx="2" '
            f'fill="#060d18" stroke="{color}" stroke-width="1.2"/>'
            f'<rect x="{cx+12}" y="{cy}" width="16" height="12" rx="1.5" fill="#09162a"/>'
        )
        # 화면 콘텐츠 라인 (수평)
        for sly, slw, slop in [(2,12,0.80),(5,9,0.55),(8,12,0.65),(11,7,0.40)]:
            p.append(f'<line x1="{cx+14}" y1="{cy+sly}" x2="{cx+14+slw}" y2="{cy+sly}" stroke="{color}" stroke-width="0.7" opacity="{slop}"/>')
        # 모니터 스탠드
        p.append(
            f'<rect x="{cx+18}" y="{cy+14}" width="3" height="5" fill="{color}" opacity="0.40"/>'
            f'<rect x="{cx+13}" y="{cy+19}" width="13" height="2" rx="1" fill="{color}" opacity="0.30"/>'
        )
        # 책상 면 (모니터+키보드 아래)
        p.append(
            f'<rect x="{cx-10}" y="{cy+21}" width="38" height="7" rx="2" '
            f'fill="#1c2d42" stroke="{color}" stroke-width="0.8" opacity="0.75"/>'
            f'<rect x="{cx-10}" y="{cy+21}" width="38" height="2" rx="1" '
            f'fill="{color}" opacity="0.25"/>'
        )
        # 키보드 (책상 위, 로봇 팔과 동일 높이)
        p.append(
            f'<rect x="{cx-8}" y="{cy+11}" width="20" height="10" rx="1.5" '
            f'fill="#0c1825" stroke="{color}" stroke-width="0.8"/>'
            f'<line x1="{cx-5}" y1="{cy+14}" x2="{cx+9}" y2="{cy+14}" stroke="{color}" stroke-width="0.5" opacity="0.65"/>'
            f'<line x1="{cx-5}" y1="{cy+17}" x2="{cx+9}" y2="{cy+17}" stroke="{color}" stroke-width="0.5" opacity="0.65"/>'
        )

        # ── AI 로봇 (좌측, 두 팔 모두 키보드 위) ────────────────────
        rx0 = cx - 20   # 로봇 수평 중심

        # 안테나 (우상단 — 모니터 방향으로 기울임)
        p.append(
            f'<line x1="{rx0+1}" y1="{cy-1}" x2="{rx0+5}" y2="{cy-8}" stroke="{color}" stroke-width="1" opacity="0.9"/>'
            f'<circle cx="{rx0+5}" cy="{cy-9}" r="1.5" fill="{color}"/>'
            f'<circle cx="{rx0+5}" cy="{cy-9}" r="2.5" fill="{color}" opacity="0.2"/>'
        )
        # 머리 (10×8)
        p.append(
            f'<rect x="{rx0-5}" y="{cy-1}" width="10" height="8" rx="2.5" '
            f'fill="#0a1520" stroke="{color}" stroke-width="1.3"/>'
        )
        # 눈 (오른쪽 치우침 — 모니터 응시)
        p.append(
            f'<circle cx="{rx0}" cy="{cy+3}" r="1.3" fill="{color}"/>'
            f'<circle cx="{rx0+3}" cy="{cy+3}" r="1.3" fill="{color}"/>'
        )
        # 입 (집중 미소)
        p.append(
            f'<path d="M{rx0-0.5:.0f},{cy+6} Q{rx0+1.5:.0f},{cy+7.5:.0f} {rx0+3:.0f},{cy+6}" '
            f'stroke="{color}" stroke-width="0.7" fill="none" opacity="0.65"/>'
        )
        # 목
        p.append(f'<rect x="{rx0-1.5:.0f}" y="{cy+7}" width="3" height="3" rx="1" fill="#162030" stroke="{color}" stroke-width="0.5"/>')
        # 몸통 (12×10)
        p.append(f'<rect x="{rx0-6}" y="{cy+10}" width="12" height="10" rx="2.5" fill="#1a2c42" stroke="{color}" stroke-width="1.3"/>')
        # 가슴 LED
        p.append(
            f'<circle cx="{rx0}" cy="{cy+15}" r="1.5" fill="{scol}"/>'
            f'<circle cx="{rx0}" cy="{cy+15}" r="2.5" fill="{scol}" opacity="0.2"/>'
        )
        # ★ 두 팔 모두 키보드 방향으로 뻗음 (키보드 y=cy+11~cy+21)
        p.append(
            # 윗팔 — 키보드 상단 행에 닿음
            f'<rect x="{rx0+6}" y="{cy+11}" width="14" height="3" rx="1.5" fill="#182840" stroke="{color}" stroke-width="0.9"/>'
            # 아랫팔 — 키보드 하단 행에 닿음
            f'<rect x="{rx0+6}" y="{cy+17}" width="14" height="3" rx="1.5" fill="#182840" stroke="{color}" stroke-width="0.9"/>'
        )
        # 손 (키보드 위 두 점)
        p.append(
            f'<circle cx="{rx0+20}" cy="{cy+12.5:.0f}" r="2.2" fill="{color}" opacity="0.65"/>'
            f'<circle cx="{rx0+20}" cy="{cy+18.5:.0f}" r="2.2" fill="{color}" opacity="0.65"/>'
        )

        # ── 이름 배지 ──────────────────────────────────────────────
        by = cy + 40
        p.append(
            f'<rect x="{cx-30}" y="{by-9}" width="60" height="12" rx="6" '
            f'fill="{color}" opacity="0.18"/>'
            f'<rect x="{cx-30}" y="{by-9}" width="60" height="12" rx="6" '
            f'fill="none" stroke="{color}" stroke-width="0.9"/>'
            f'<text x="{cx}" y="{by}" text-anchor="middle" '
            f'font-family="Inter,AppleSDGothicNeo,sans-serif" '
            f'font-size="8" font-weight="800" fill="{color}">{emoji} {name}</text>'
        )
        p.append(
            f'<text x="{cx}" y="{by+9}" text-anchor="middle" '
            f'font-family="Inter,AppleSDGothicNeo,sans-serif" '
            f'font-size="7.5" fill="#e0eaf5">{role}</text>'
        )
        if l1:
            p.append(
                f'<text x="{cx}" y="{by+18}" text-anchor="middle" '
                f'font-family="Inter,sans-serif" font-size="7" fill="#c8d8e8">{l1}</text>'
            )

    # ── 장식 요소 ────────────────────────────────────────────────
    # 좌상단: 보안 카메라 + 모니터링 패널 (밝게)
    p.append(
        '<rect x="12" y="40" width="64" height="42" rx="3" fill="#1e3450" stroke="#3a5880" stroke-width="1.2"/>'
        '<text x="44" y="53" text-anchor="middle" font-family="sans-serif" font-size="9" font-weight="700" fill="#7ab0e0">CCTV</text>'
        '<rect x="15" y="57" width="58" height="22" rx="2" fill="#0e2038" stroke="#2a4868" stroke-width="0.8"/>'
        '<circle cx="28" cy="68" r="8" fill="#142030" stroke="#2a4060" stroke-width="0.8"/>'
        '<circle cx="28" cy="68" r="5" fill="#1a2c40"/>'
        '<circle cx="28" cy="68" r="2" fill="#ff3366" opacity="0.95"/>'
        '<line x1="40" y1="62" x2="68" y2="62" stroke="#3a6090" stroke-width="0.9"/>'
        '<line x1="40" y1="66" x2="64" y2="66" stroke="#3a6090" stroke-width="0.9"/>'
        '<line x1="40" y1="70" x2="68" y2="70" stroke="#3a6090" stroke-width="0.9"/>'
        '<line x1="40" y1="74" x2="60" y2="74" stroke="#3a6090" stroke-width="0.9"/>'
    )
    # 우상단: 시계 (밝게)
    p.append(
        '<circle cx="832" cy="62" r="17" fill="#1a3050" stroke="#3a6090" stroke-width="1.5"/>'
        '<circle cx="832" cy="62" r="13" fill="#0e2038" stroke="#2a4870" stroke-width="1"/>'
        '<line x1="832" y1="62" x2="832" y2="51" stroke="#60a0d0" stroke-width="1.8" stroke-linecap="round"/>'
        '<line x1="832" y1="62" x2="841" y2="62" stroke="#60a0d0" stroke-width="1.4" stroke-linecap="round"/>'
        '<circle cx="832" cy="62" r="2.5" fill="#80c0e8"/>'
    )
    # 커피 머신 (row2~3 오른쪽 빈 공간 x=590, y=302 — 에이전트 박스 외부)
    p.append(
        '<rect x="590" y="302" width="24" height="30" rx="3" fill="#1e3450" stroke="#3a5880" stroke-width="1"/>'
        '<rect x="593" y="305" width="18" height="12" rx="2" fill="#0e2038"/>'
        '<circle cx="602" cy="323" r="5" fill="#0e2038" stroke="#2a4870" stroke-width="1"/>'
        '<circle cx="602" cy="323" r="2.5" fill="#1a2e48"/>'
        '<text x="602" y="338" text-anchor="middle" font-family="sans-serif" font-size="9" fill="#5090b8">☕</text>'
    )
    # 좌하단 화분 (큰 — 선명한 초록)
    p.append(
        '<rect x="10" y="452" width="22" height="15" rx="3" fill="#2a3c50" stroke="#3a5878" stroke-width="1"/>'
        '<circle cx="21" cy="449" r="11" fill="#1a5a20"/>'
        '<circle cx="13" cy="443" r="8" fill="#1e6824"/>'
        '<circle cx="29" cy="444" r="7" fill="#186020"/>'
        '<ellipse cx="21" cy="447" rx="6" ry="4" fill="#28782e" opacity="0.6"/>'
    )
    # 우하단 화분 (선명한 초록)
    p.append(
        '<rect x="828" y="452" width="22" height="15" rx="3" fill="#2a3c50" stroke="#3a5878" stroke-width="1"/>'
        '<circle cx="839" cy="449" r="11" fill="#1a5a20"/>'
        '<circle cx="831" cy="443" r="8" fill="#1e6824"/>'
        '<circle cx="847" cy="444" r="7" fill="#186020"/>'
        '<ellipse cx="839" cy="447" rx="6" ry="4" fill="#28782e" opacity="0.6"/>'
    )
    # 좌중단 화분 (소 — 선명)
    p.append(
        '<rect x="10" y="298" width="14" height="11" rx="2" fill="#263850" stroke="#3a5878" stroke-width="0.8"/>'
        '<circle cx="17" cy="294" r="7" fill="#1a5a20"/>'
        '<circle cx="12" cy="289" r="5" fill="#1e6824"/>'
        '<circle cx="22" cy="290" r="5" fill="#186020"/>'
    )
    # ── 범례 (우측 빈 공간 — rows 2~3 오른쪽 x=650~840) ─────────
    # row1에 J09(cx=640)만 있고 rows 2-3 우측(x=640+)은 완전 비어있음
    _lx, _ly = 742, 278   # 박스 중앙 x, 상단 y
    _lw, _lh = 168, 114   # 10항목 (2열×5행) 수용
    p.append(
        f'<rect x="{_lx-_lw//2}" y="{_ly}" width="{_lw}" height="{_lh}" rx="5" '
        f'fill="#0e1e30" stroke="#2a4060" stroke-width="1" opacity="0.97"/>'
        f'<text x="{_lx}" y="{_ly+13}" text-anchor="middle" '
        f'font-family="Inter,AppleSDGothicNeo,sans-serif" '
        f'font-size="8.5" font-weight="700" fill="#6090b8">연결 범례</text>'
        f'<line x1="{_lx-_lw//2+8}" y1="{_ly+18}" x2="{_lx+_lw//2-8}" y2="{_ly+18}" '
        f'stroke="#1e3050" stroke-width="0.8"/>'
    )
    _legend = [
        ("#00aaff","Master"),    ("#22c55e","Infra"),
        ("#aa44ff","Writer"),    ("#ffcc00","Radar"),
        ("#ff6633","Sched"),     ("#00ddbb","Collector"),
        ("#ff88cc","Image"),     ("#00ccff","Publish"),
        ("#44ffaa","Vision"),    ("#ff9900","Guardian"),
    ]
    _col_w = _lw // 2
    for _li, (_lc, _lt) in enumerate(_legend):
        _lbx = (_lx - _lw // 2) + 10 + (_li % 2) * _col_w
        _lby = _ly + 26 + (_li // 2) * 16
        p.append(
            f'<line x1="{_lbx}" y1="{_lby}" x2="{_lbx+12}" y2="{_lby}" stroke="{_lc}" stroke-width="2.5" opacity="0.9"/>'
            f'<circle cx="{_lbx+6}" cy="{_lby}" r="2" fill="{_lc}"/>'
            f'<text x="{_lbx+16}" y="{_lby+4}" font-family="Inter,sans-serif" font-size="8.5" fill="#a0c4e0">{_lt}</text>'
        )

    p.append('</svg></div>')
    return "".join(p)

def _render_suggestion_diff(suggestions: list):
    """BEFORE / AFTER 개선 제안 diff 렌더."""
    if not suggestions:
        md(f'<div style="font-size:14px;color:{N["text5"]};padding:6px 0">제안 없음</div>')
        return
    for s in suggestions:
        p = s.get("priority", "low")
        p_color = C["danger"] if p == "high" else (C["warn"] if p == "medium" else C["success"])
        before_txt = esc(str(s.get("before", ""))[:200])
        after_txt  = esc(str(s.get("after",  ""))[:200])
        field_txt  = esc(s.get("field",  "?"))
        issue_txt  = esc(s.get("issue",  ""))
        md(
            f'<div style="margin:8px 0;padding:14px 16px;background:{_alpha(p_color,.04)};'
            f'border-left:3px solid {p_color};border-radius:8px">'
            f'<div style="font-size:16px;font-weight:700;color:{p_color};margin-bottom:6px">'
            f'{field_txt} '
            f'<span style="font-size:14px;background:{_alpha(p_color,.15)};'
            f'border:1px solid {_alpha(p_color,.35)};border-radius:4px;padding:2px 6px">'
            f'{esc(p.upper())}</span></div>'
            f'<div style="font-size:14px;color:{N["text2"]};margin-bottom:10px">{issue_txt}</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
            f'<div style="background:{_alpha(C["danger"],.06)};border:1px solid {_alpha(C["danger"],.2)};'
            f'border-radius:6px;padding:10px">'
            f'<div style="font-size:14px;color:{C["danger"]};font-weight:700;margin-bottom:5px">BEFORE</div>'
            f'<div style="font-size:14px;color:{N["text2"]};line-height:1.6">{before_txt}</div></div>'
            f'<div style="background:{_alpha(C["success"],.06)};border:1px solid {_alpha(C["success"],.2)};'
            f'border-radius:6px;padding:10px">'
            f'<div style="font-size:14px;color:{C["success"]};font-weight:700;margin-bottom:5px">AFTER</div>'
            f'<div style="font-size:14px;color:{N["text2"]};line-height:1.6">{after_txt}</div></div>'
            f'</div></div>'
        )

def empty_state(msg: str, sub: str = "") -> str:
    return (
        f'<div style="text-align:center;padding:48px 24px;color:{N["text5"]};font-size:16px">'
        f'{esc(msg)}'
        f'{"<div style=" + chr(39) + "font-size:14px;margin-top:8px;color:" + N["text5"] + chr(39) + ">" + esc(sub) + "</div>" if sub else ""}'
        f'</div>'
    )

# ══════════════════════════════════════════════════════════════════
# DB 헬퍼
# ══════════════════════════════════════════════════════════════════
def _db():
    if not DB_PATH.exists():
        return None
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con

def _fmt(s) -> str:
    if not s: return "—"
    try: return datetime.fromisoformat(str(s)).strftime("%m/%d %H:%M")
    except: return str(s)[:16]

OWNER_LABEL = {
    "jarvis00_infra":    "J00",
    "jarvis01_master":   "J01",
    "jarvis02_writer":   "J02",
    "jarvis03_radar":    "J03",
    "jarvis04_scheduler":"J04",
    "jarvis08_publish":  "J08",
    "jarvis09_collector":"J09",
}
PLAT_COLOR = {"naver": "success", "tistory": "warn"}

# ══════════════════════════════════════════════════════════════════
# 데이터 로딩 (캐시)
# ══════════════════════════════════════════════════════════════════
@st.cache_data(ttl=30)
def load_daemon():
    pid_file = BASE_DIR / "logs" / "daemon.pid"
    r = {"alive": False, "pid": None, "uptime": "—"}
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().split("\n")[0].strip())
            r["pid"] = pid
            ps = subprocess.run(["ps", "-p", str(pid), "-o", "pid,etime="],
                                capture_output=True, text=True)
            if ps.returncode == 0:
                r["alive"] = True
                lines = ps.stdout.strip().splitlines()
                if len(lines) >= 2:
                    r["uptime"] = lines[-1].strip().split()[-1]
        except Exception:
            pass
    return r

@st.cache_data(ttl=30)
def load_posts_stats():
    con = _db()
    if not con: return {}
    today     = datetime.now().strftime("%Y-%m-%d")
    week_ago  = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    r = {
        "today": con.execute("SELECT COUNT(*) FROM posts WHERE date(created_at)=?", (today,)).fetchone()[0],
        "week":  con.execute("SELECT COUNT(*) FROM posts WHERE date(created_at)>=?", (week_ago,)).fetchone()[0],
        "month": con.execute("SELECT COUNT(*) FROM posts WHERE date(created_at)>=?", (month_ago,)).fetchone()[0],
        "by_platform": {
            row["platform"]: row["n"]
            for row in con.execute(
                "SELECT platform,COUNT(*) as n FROM posts WHERE date(created_at)=? GROUP BY platform",
                (today,)
            ).fetchall()
        },
    }
    con.close()
    return r

@st.cache_data(ttl=30)
def load_pipeline():
    con = _db()
    if not con: return {}
    today = datetime.now().strftime("%Y-%m-%d")
    rows    = con.execute("SELECT status,COUNT(*) as n FROM pipeline WHERE date(created_at)=? GROUP BY status", (today,)).fetchall()
    all_r   = con.execute("SELECT status,COUNT(*) as n FROM pipeline GROUP BY status").fetchall()
    recent  = con.execute("SELECT theme,status,created_at FROM pipeline ORDER BY created_at DESC LIMIT 10").fetchall()
    con.close()
    return {
        "today":  {r["status"]: r["n"] for r in rows},
        "all":    {r["status"]: r["n"] for r in all_r},
        "recent": [dict(r) for r in recent],
    }

@st.cache_data(ttl=30)
def load_trends():
    con = _db()
    if not con: return {"today": 0, "top": [], "sectors": {}}
    today = datetime.now().strftime("%Y-%m-%d")
    count   = con.execute("SELECT COUNT(*) FROM trends WHERE date=?", (today,)).fetchone()[0]
    top     = con.execute(
        "SELECT keyword,sector,score,opportunity_score,source FROM trends "
        "WHERE date=? ORDER BY opportunity_score DESC LIMIT 15", (today,)
    ).fetchall()
    sectors = con.execute(
        "SELECT sector,COUNT(*) as n FROM trends WHERE date=? GROUP BY sector ORDER BY n DESC", (today,)
    ).fetchall()
    con.close()
    return {
        "today":   count,
        "top":     [dict(r) for r in top],
        "sectors": {r["sector"]: r["n"] for r in sectors},
    }

@st.cache_data(ttl=30)
def load_quality_stats():
    """post_analysis 상태별 집계 (발행 관리 탭용)."""
    con = _db()
    if not con: return {"by_status": {}, "recent": []}
    by_status = {r["status"]: r["n"] for r in con.execute(
        "SELECT status,COUNT(*) as n FROM post_analysis GROUP BY status"
    ).fetchall()}
    recent = con.execute(
        "SELECT platform,title,status,created_at,current_views FROM post_analysis "
        "ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    con.close()
    return {"by_status": by_status, "recent": [dict(r) for r in recent]}

@st.cache_data(ttl=30)
def load_analysis_history(limit=150):
    """품질 관리 탭 — 분석 이력 전체 (shared.db 위임)."""
    try:
        from shared import db as _sdb
        rows = _sdb.get_analysis_history(limit=limit) or []
        return rows
    except Exception:
        # fallback: SQLite 직접 조회
        con = _db()
        if not con: return []
        rows = con.execute(
            "SELECT id,platform,theme,title,url,status,suggestions,"
            "analyzed_at,created_at,current_views,naver_rank "
            "FROM post_analysis ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_performance():
    con = _db()
    if not con: return {"total_views": 0, "top_posts": [], "platform_views": {}, "naver_ranked": []}
    try:
        total    = con.execute("SELECT COALESCE(SUM(current_views),0) FROM post_analysis").fetchone()[0]
        # naver_rank 컬럼이 없는 구 DB 호환: 없으면 NULL 대체
        try:
            top = con.execute(
                "SELECT platform,title,current_views,naver_rank,created_at FROM post_analysis "
                "WHERE current_views>0 ORDER BY current_views DESC LIMIT 15"
            ).fetchall()
        except Exception:
            top = con.execute(
                "SELECT platform,title,current_views,NULL as naver_rank,created_at FROM post_analysis "
                "WHERE current_views>0 ORDER BY current_views DESC LIMIT 15"
            ).fetchall()
        by_plat  = con.execute(
            "SELECT platform,COALESCE(SUM(current_views),0) as views FROM post_analysis GROUP BY platform"
        ).fetchall()
        try:
            naver_r = con.execute(
                "SELECT title,naver_rank,current_views,created_at FROM post_analysis "
                "WHERE naver_rank IS NOT NULL ORDER BY naver_rank ASC LIMIT 10"
            ).fetchall()
        except Exception:
            naver_r = []
        # 7일 추세 (일별 합산)
        hist = con.execute(
            "SELECT date(created_at) as d, COALESCE(SUM(current_views),0) as v "
            "FROM post_analysis WHERE date(created_at) >= date('now','-7 days') "
            "GROUP BY d ORDER BY d"
        ).fetchall()
        con.close()
        return {
            "total_views":   total,
            "top_posts":     [dict(r) for r in top],
            "platform_views":{r["platform"]: r["views"] for r in by_plat},
            "naver_ranked":  [dict(r) for r in naver_r],
            "history":       [dict(r) for r in hist],
        }
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return {"total_views": 0, "top_posts": [], "platform_views": {}, "naver_ranked": [], "history": []}

@st.cache_data(ttl=30)
def load_job_runs(owner=None, days=1, limit=30):
    con = _db()
    if not con: return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    if owner:
        rows = con.execute(
            "SELECT * FROM job_runs WHERE owner_agent=? AND started_at>=? "
            "ORDER BY started_at DESC LIMIT ?", (owner, cutoff, limit)
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM job_runs WHERE started_at>=? ORDER BY started_at DESC LIMIT ?",
            (cutoff, limit)
        ).fetchall()
    con.close()
    return [dict(r) for r in rows]

@st.cache_data(ttl=60)
def load_job_last_runs():
    con = _db()
    if not con: return {}
    rows = con.execute(
        "SELECT job_id,MAX(started_at) as last_run,MAX(success) as success "
        "FROM job_runs GROUP BY job_id"
    ).fetchall()
    con.close()
    return {r["job_id"]: dict(r) for r in rows}

@st.cache_data(ttl=30)
def load_failed_jobs(days=7):
    con = _db()
    if not con: return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute(
        "SELECT job_id,job_name,started_at,error,owner_agent FROM job_runs "
        "WHERE success=0 AND started_at>=? ORDER BY started_at DESC LIMIT 20", (cutoff,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

@st.cache_data(ttl=30)
def load_tool_stats():
    con = _db()
    if not con: return []
    cutoff = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute(
        "SELECT tool_name,domain,COUNT(*) as calls,"
        "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as ok,AVG(duration_ms) as avg_ms "
        "FROM tool_runs WHERE ran_at>=? GROUP BY tool_name ORDER BY calls DESC LIMIT 20", (cutoff,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

@st.cache_data(ttl=30)
def load_recent_events(limit=10):
    con = _db()
    if not con: return []
    rows = con.execute(
        "SELECT event_type,source,created_at FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]

@st.cache_data(ttl=300)
def load_daily_review(days=7):
    """daily_review 테이블 — 시스템 자기분석 결과."""
    con = _db()
    if not con: return []
    try:
        rows = con.execute(
            "SELECT review_date,posts_count,avg_views,quality_score,"
            "sector_dist,common_issues,insights,next_directives,reviewed_at "
            "FROM daily_review ORDER BY review_date DESC LIMIT ?", (days,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        con.close()
        return []

@st.cache_data(ttl=120)
def load_keyword_performance(limit=30):
    """keyword_performance 테이블 — 키워드별 누적 성과."""
    con = _db()
    if not con: return []
    try:
        rows = con.execute(
            "SELECT keyword,avg_views,best_views,best_rank,avg_rank,composite_score,"
            "post_count AS total_posts,last_used AS last_seen "
            "FROM keyword_performance ORDER BY composite_score DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        con.close()
        return []

@st.cache_data(ttl=120)
def load_learning_status():
    """AI 자기학습 현황 — learned_weights, backtest_history, learning_insights, learn_log 통계."""
    con = _db()
    if not con: return {}
    r: dict = {}
    try:
        # 최신 가중치 — 실제 컬럼: w_trend,w_perf,w_fresh,w_velocity,w_competition,intercept,n_samples,r2,mse,learned_at
        w = con.execute(
            "SELECT id,w_trend,w_perf,w_fresh,w_velocity,w_competition,"
            "intercept,n_samples,r2,mse,learned_at "
            "FROM learned_weights ORDER BY id DESC LIMIT 3"
        ).fetchall()
        # 대시보드가 weight_type/weights_json/trained_at/backtest_score 를 기대하므로 호환 변환
        r["weights"] = [
            {
                "weight_type":    "ridge",
                "weights_json":   json.dumps({
                    "w_trend": x["w_trend"], "w_perf": x["w_perf"],
                    "w_fresh": x["w_fresh"], "w_velocity": x["w_velocity"],
                    "w_competition": x["w_competition"], "intercept": x["intercept"],
                }, ensure_ascii=False),
                "trained_at":     x["learned_at"],
                "backtest_score": x["r2"],
            }
            for x in w
        ]
    except Exception:
        r["weights"] = []
    try:
        # 백테스트 이력 — 실제 컬럼: n_samples,r2,mse,mape,tested_at
        bt = con.execute(
            "SELECT tested_at,n_samples,r2,mse,mape "
            "FROM backtest_history ORDER BY tested_at DESC LIMIT 14"
        ).fetchall()
        # 대시보드가 backtest_type/score/details 를 기대하므로 호환 변환
        r["backtest"] = [
            {
                "tested_at":     x["tested_at"],
                "backtest_type": "regression",
                "score":         x["r2"],
                "details":       f"n={x['n_samples']}, MSE={x['mse']:.3f}, MAPE={x['mape']:.3f}" if x["mape"] else f"n={x['n_samples']}, MSE={x['mse']:.3f}",
            }
            for x in bt
        ]
    except Exception:
        r["backtest"] = []
    try:
        # 학습 인사이트
        ins = con.execute(
            "SELECT insight_key,insight_type,description,directive,weight,"
            "scope,occurrences,last_seen "
            "FROM learning_insights ORDER BY occurrences DESC LIMIT 20"
        ).fetchall()
        r["insights"] = [dict(x) for x in ins]
    except Exception:
        r["insights"] = []
    try:
        # learn_log 예측 오차 통계 (최근 30건)
        ll = con.execute(
            "SELECT COUNT(*) as cnt, AVG(ABS(actual_views - predicted_opp)) as mae "
            "FROM learn_log"
        ).fetchone()
        r["learn_log"] = {"cnt": ll["cnt"] if ll else 0, "mae": ll["mae"] if ll else None}
    except Exception:
        r["learn_log"] = {"cnt": 0, "mae": None}
    con.close()
    return r

@st.cache_data(ttl=120)
def load_feedback_penalty(limit=20):
    """feedback_penalty 테이블 — 사용자 거부 패턴 누적."""
    con = _db()
    if not con: return []
    try:
        rows = con.execute(
            "SELECT * FROM feedback_penalty ORDER BY penalty_score DESC LIMIT ?", (limit,)
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        con.close()
        return []

@st.cache_data(ttl=60)
def load_capabilities():
    try:
        from shared import capabilities as _caps
        return [{"agent_id": c.agent_id, "intents": getattr(c, "intents", [])}
                for c in _caps.all_capabilities()]
    except: return []

@st.cache_data(ttl=60)
def load_default_jobs():
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        return DEFAULT_JOBS
    except: return []

@st.cache_data(ttl=30)
def load_vision_agents() -> list[dict]:
    """JARVIS05 VISION API (8505) 에서 에이전트 상태 + 메트릭 조회."""
    try:
        import requests as _req
        r = _req.get("http://127.0.0.1:8505/api/agents", timeout=3)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return []

@st.cache_data(ttl=30)
def load_vision_summary() -> dict:
    """JARVIS05 VISION API (8505) 시스템 KPI 요약."""
    try:
        import requests as _req
        r = _req.get("http://127.0.0.1:8505/api/metrics/summary", timeout=3)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}

@st.cache_data(ttl=60)
def load_image_stats() -> dict:
    """JARVIS06 이미지 생성 통계 — output 디렉토리 스캔 + 프로바이더 가용성 체크."""
    import re as _re2
    out_dir = BASE_DIR / "JARVIS06_IMAGE" / "output"
    total = 0
    by_type: dict = {}
    recent: list = []
    total_size_mb = 0.0
    if out_dir.exists():
        files = sorted(
            (f for f in out_dir.iterdir()
             if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for f in files:
            total += 1
            ext = f.suffix.lower().lstrip(".")
            by_type[ext] = by_type.get(ext, 0) + 1
            total_size_mb += f.stat().st_size / 1024 / 1024
        recent = [
            {
                "name":    f.name,
                "mtime":   datetime.fromtimestamp(f.stat().st_mtime).strftime("%m/%d %H:%M"),
                "size_kb": round(f.stat().st_size / 1024, 1),
                "type":    f.suffix.lower().lstrip("."),
            }
            for f in files[:10]
        ]

    # ★ 사용자 박제 2026-06-07 — Bing / HuggingFace 완전 삭제 (ERRORS [263])
    # 단일 폴백: Pollinations.ai (키 불필요)
    return {
        "total":         total,
        "by_type":       by_type,
        "total_size_mb": round(total_size_mb, 1),
        "recent":        recent,
        "providers": {
            "pollinations": True,   # 항상 가용 (무키 폴백)
        },
    }

@st.cache_data(ttl=60)
def load_publish_stats() -> dict:
    """JARVIS08 발행 도메인 현황 — 쿠키·자격증명 상태 + 플랫폼별 발행 수."""
    import re as _re3
    from pathlib import Path as _Path
    _root = BASE_DIR
    _legacy = _root / "JARVIS02_WRITER"

    # 네이버 쿠키 파일 나이
    nv_cookie = _legacy / "naver_cookies.pkl"
    nv_age_h: float | None = None
    nv_ok = nv_cookie.exists()
    if nv_ok:
        from datetime import datetime as _dt
        nv_age_h = round((_dt.now().timestamp() - nv_cookie.stat().st_mtime) / 3600, 1)

    # .env 에서 자격증명 존재 여부
    env_file = _root / ".env"
    ts_ok = False
    try:
        if env_file.exists():
            _et = env_file.read_text(encoding="utf-8")
            ts_ok = bool(_re3.search(r"^TS_COOKIE\s*=\s*\S+", _et, _re3.MULTILINE))
    except Exception:
        pass

    # 플랫폼별 7일 발행 수
    plat_counts: dict = {}
    con = _db()
    if con:
        try:
            rows = con.execute(
                "SELECT platform, COUNT(*) as n FROM posts "
                "WHERE date(created_at) >= date('now', '-7 days', 'localtime') "
                "GROUP BY platform"
            ).fetchall()
            for r in rows:
                plat_counts[r["platform"]] = r["n"]
        except Exception:
            pass
        con.close()

    return {
        "naver_cookie_ok":  nv_ok,
        "naver_cookie_age": nv_age_h,
        "ts_cookie_ok":     ts_ok,
        "plat_7d":          plat_counts,
    }

@st.cache_data(ttl=30)
def load_guardian_stats() -> dict:
    """JARVIS07 오류 현황 — error_log 테이블 조회."""
    try:
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        con.row_factory = sqlite3.Row
        stats_row = con.execute("""
            SELECT
                SUM(CASE WHEN status IN ('new','analyzing','fixed','resolved','wontfix','ignored','manual') THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN status='new'       THEN 1 ELSE 0 END) AS new_cnt,
                SUM(CASE WHEN status='analyzing' THEN 1 ELSE 0 END) AS analyzing_cnt,
                SUM(CASE WHEN status IN ('fixed','resolved') THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status='wontfix'   THEN 1 ELSE 0 END) AS wontfix_cnt,
                SUM(CASE WHEN status='ignored'   THEN 1 ELSE 0 END) AS ignored_cnt,
                SUM(CASE WHEN status='manual'    THEN 1 ELSE 0 END) AS manual_cnt,
                SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit_cnt,
                SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high_cnt,
                SUM(CASE WHEN severity='medium'   THEN 1 ELSE 0 END) AS med_cnt,
                SUM(CASE WHEN severity='low'      THEN 1 ELSE 0 END) AS low_cnt
            FROM error_log
            WHERE timestamp >= datetime('now', '-7 days')
        """).fetchone()
        recent = [dict(r) for r in con.execute("""
            SELECT id, timestamp, severity, status, error_type, module, message
            FROM error_log ORDER BY id DESC LIMIT 10
        """).fetchall()]
        con.close()
        return {
            "total":     stats_row["total"] or 0,
            "new":       stats_row["new_cnt"] or 0,
            "analyzing": stats_row["analyzing_cnt"] or 0,
            "fixed":     stats_row["fixed_cnt"] or 0,
            "wontfix":   stats_row["wontfix_cnt"] or 0,
            "ignored":   stats_row["ignored_cnt"] or 0,
            "manual":    stats_row["manual_cnt"] or 0,
            "critical":  stats_row["crit_cnt"] or 0,
            "high":      stats_row["high_cnt"] or 0,
            "medium":    stats_row["med_cnt"] or 0,
            "low":       stats_row["low_cnt"] or 0,
            "recent":    recent,
        }
    except Exception:
        return {"total": 0, "new": 0, "analyzing": 0, "fixed": 0,
                "wontfix": 0, "ignored": 0, "manual": 0, "critical": 0, "high": 0,
                "medium": 0, "low": 0, "recent": []}

@st.cache_data(ttl=60)
def load_guardian_stats_alltime() -> dict:
    """JARVIS07 오류 현황 — 전체 누적 (영구 보존 정책 2026-05-25)."""
    try:
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        con.row_factory = sqlite3.Row
        r = con.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status='new'                    THEN 1 ELSE 0 END) AS new_cnt,
                SUM(CASE WHEN status IN ('fixed','resolved')  THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status='manual'                 THEN 1 ELSE 0 END) AS manual_cnt,
                SUM(CASE WHEN status='wontfix'                THEN 1 ELSE 0 END) AS wontfix_cnt,
                SUM(CASE WHEN status='ignored'                THEN 1 ELSE 0 END) AS ignored_cnt,
                MIN(timestamp) AS first_seen
            FROM error_log
        """).fetchone()
        con.close()
        first = (r["first_seen"] or "")[:10]
        return {
            "total":   r["total"]     or 0,
            "new":     r["new_cnt"]   or 0,
            "fixed":   r["fixed_cnt"] or 0,
            "manual":  r["manual_cnt"]or 0,
            "wontfix": r["wontfix_cnt"]or 0,
            "ignored": r["ignored_cnt"]or 0,
            "first":   first,
        }
    except Exception:
        return {"total": 0, "new": 0, "fixed": 0, "manual": 0,
                "wontfix": 0, "ignored": 0, "first": ""}


@st.cache_data(ttl=15)
def load_guardian_errors(status: str = None, severity: str = None,
                          days: int = 30, limit: int = 200) -> list[dict]:
    """오류 목록 조회 — status/severity/days 필터 지원."""
    try:
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        con.row_factory = sqlite3.Row
        where = [f"timestamp >= datetime('now', '-{days} days', 'localtime')"]
        params: list = []
        if status:
            where.append("status = ?"); params.append(status)
        if severity:
            where.append("severity = ?"); params.append(severity)
        w = " AND ".join(where)
        rows = con.execute(
            f"SELECT id, timestamp, source, module, func_name, error_type, "
            f"message, traceback, severity, status, resolution, fixed_file, "
            f"fixed_at, seen_count FROM error_log WHERE {w} "
            f"ORDER BY id DESC LIMIT {limit}",
            params,
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

@st.cache_data(ttl=30)
def load_guardian_trend(days: int = 14) -> list[dict]:
    """일별 오류 발생 추이 — 최근 N일."""
    try:
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        rows = con.execute(f"""
            SELECT DATE(timestamp, 'localtime') AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high,
                   SUM(CASE WHEN status='fixed'      THEN 1 ELSE 0 END) AS fixed
            FROM error_log
            WHERE timestamp >= datetime('now', '-{days} days', 'localtime')
            GROUP BY day ORDER BY day
        """).fetchall()
        con.close()
        return [{"day": r[0], "total": r[1], "crit": r[2],
                 "high": r[3], "fixed": r[4]} for r in rows]
    except Exception:
        return []

@st.cache_data(ttl=15)
def load_guardian_source_stats(days: int = 7) -> list[dict]:
    """에이전트(source)별 오류 통계."""
    try:
        con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        rows = con.execute(f"""
            SELECT source,
                   COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN status='fixed'      THEN 1 ELSE 0 END) AS fixed,
                   SUM(CASE WHEN status='new'        THEN 1 ELSE 0 END) AS new_cnt
            FROM error_log
            WHERE timestamp >= datetime('now', '-{days} days', 'localtime')
            GROUP BY source ORDER BY total DESC LIMIT 10
        """).fetchall()
        con.close()
        return [{"source": r[0], "total": r[1], "crit": r[2],
                 "fixed": r[3], "new": r[4]} for r in rows]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════
# 헤더 + 새로고침
# ══════════════════════════════════════════════════════════════════
now_str = datetime.now().strftime("%Y년 %m월 %d일  %H:%M")
hd_left, hd_right = st.columns([6, 1])
with hd_left:
    md(f"""
    <div style="margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid {N['bdr']}">
      <div style="font-size:32px;font-weight:900;color:{C['primary']}">JARVIS Hub</div>
      <div style="font-size:16px;color:{N['text2']};margin-top:2px">전체 시스템 통합 현황판</div>
    </div>
    """)
with hd_right:
    st.markdown("<div style='padding-top:8px'></div>", unsafe_allow_html=True)
    if st.button("새로고침", key="_refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    md(f'<div style="font-size:14px;color:{N["text5"]};text-align:right;margin-top:4px">'
       f'{esc(now_str)}</div>')

# ══════════════════════════════════════════════════════════════════
# 탭 (7개) — 승인 대기 건수를 탭 레이블에 표시
# ══════════════════════════════════════════════════════════════════
_qa_pending_count = 0
try:
    _con_tmp = _db()
    if _con_tmp:
        _qa_pending_count = _con_tmp.execute(
            "SELECT COUNT(*) FROM post_analysis WHERE status='pending_approval'"
        ).fetchone()[0]
        _con_tmp.close()
except Exception:
    pass

_qa_tab_label = f"품질 관리 🔔{_qa_pending_count}" if _qa_pending_count > 0 else "품질 관리"

# 오류 탭 레이블 — 미처리 critical/high 건수 표시
_err_new_count = 0
try:
    _con_err = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _err_new_count = _con_err.execute(
        "SELECT COUNT(*) FROM error_log WHERE status='new' "
        "AND severity IN ('critical','high')"
    ).fetchone()[0]
    _con_err.close()
except Exception:
    pass
_err_tab_label = f"오류 관리 🚨{_err_new_count}" if _err_new_count > 0 else "오류 관리"

t_home, t_radar, t_pub, t_qa, t_perf, t_ai, t_err, t_sched, t_sys = st.tabs([
    "홈", "레이더", "발행 관리", _qa_tab_label, "성과", "AI 학습",
    _err_tab_label, "스케줄러", "시스템",
])

# ──────────────────────────────────────────────────────────────────
# 홈
# ──────────────────────────────────────────────────────────────────
with t_home:
    daemon     = load_daemon()
    posts      = load_posts_stats()
    pipeline   = load_pipeline()
    trends     = load_trends()
    today_jobs = load_job_runs(days=1, limit=50)
    perf       = load_performance()
    caps       = load_capabilities()
    dj         = load_default_jobs()

    n_intents = sum(len(c.get("intents", [])) for c in caps)
    job_ok    = sum(1 for j in today_jobs if j.get("success"))

    # ── 에이전트 사무실 뷰 ────────────────────────────────────────────
    _img_s   = load_image_stats()
    _prov_ok = sum(1 for v in _img_s["providers"].values() if v)
    _gd      = load_guardian_stats()
    _gnew, _gfix = _gd.get("new", 0), _gd.get("fixed", 0)
    _gurgent = _gd.get("critical", 0) + _gd.get("high", 0)
    _pd      = load_publish_stats()
    _nv_ok   = _pd.get("naver_cookie_ok", False)
    _ts_ok   = _pd.get("ts_cookie_ok", False)
    _nv_age  = _pd.get("naver_cookie_age")
    _nv_age_txt = f"{_nv_age}시간 전" if _nv_age is not None else "파일 없음"
    _cred_all = _nv_ok and _ts_ok
    _job_fail = len(today_jobs) - job_ok
    _j05_ok  = bool(load_vision_agents())
    _pend    = pipeline.get("today", {}).get("suggested", 0) + pipeline.get("today", {}).get("pending", 0)

    _con_qa = _db()
    _qa_pending = 0
    if _con_qa:
        try:
            _qa_pending = _con_qa.execute(
                "SELECT COUNT(*) FROM post_analysis WHERE status='pending_approval'"
            ).fetchone()[0]
        except Exception:
            pass
        _con_qa.close()

    try:
        from shared import db as _sdb
        _j09_stats = _sdb.get_collection_stats()
    except Exception:
        _j09_stats = {"total": 0, "today": 0}

    _status_map = {
        "j00": "online" if daemon["alive"] else "offline",
        "j01": "online" if caps else "warn",
        "j02": "online",
        "j03": "warn" if _qa_pending > 3 else "online",
        "j04": "warn" if _job_fail > 2 else "online",
        "j05": "online" if _j05_ok else "warn",
        "j06": "online" if _prov_ok > 0 else "warn",
        "j07": "warn" if _gurgent > 0 else "online",
        "j08": "online" if _cred_all else "warn",
        "j09": "online",
    }
    _info_map = {
        "j00": {"line1": f"PID {daemon['pid'] or '—'} · {daemon['uptime']}"},
        "j01": {"line1": f"에이전트 {len(caps)}개 · 인텐트 {n_intents}개"},
        "j02": {"line1": f"오늘 {posts.get('today', 0)}건 · 대기 {_pend}건"},
        "j03": {"line1": f"트렌드 {trends['today']}개 · 승인대기 {_qa_pending}건"},
        "j04": {"line1": f"잡 {len(today_jobs)}건 · 성공 {job_ok} / 실패 {_job_fail}"},
        "j05": {"line1": "VISION API :8505" if _j05_ok else "API 연결 대기"},
        "j06": {"line1": f"이미지 {_img_s['total']}개 · 프로바이더 {_prov_ok}/1"},
        "j07": {"line1": f"신규 {_gnew}건 · 수정 {_gfix}건 · CRIT {_gurgent}건"},
        "j08": {"line1": f"네이버 {'✅' if _nv_ok else '❌'} 티스토리 {'✅' if _ts_ok else '❌'} · {_nv_age_txt}"},
        "j09": {"line1": f"수집 누적 {_j09_stats['total']}건 · 오늘 {_j09_stats['today']}건"},
    }

    md(_office_view_html(_status_map, _info_map))

    st.markdown("<br>", unsafe_allow_html=True)
    section("오늘 KPI")
    k0, k1, k2, k3, k4 = st.columns(5)
    p_today = pipeline.get("today", {})
    pend_cnt = p_today.get("suggested", 0) + p_today.get("pending", 0)
    with k0: md(kpi("오늘 발행 글",    posts.get("today", 0),  color="success",  sub="posts 테이블"))
    with k1: md(kpi("파이프라인 대기", pend_cnt, color="warn" if pend_cnt > 0 else "muted", sub=f"완료 {p_today.get('done',0)}건"))
    with k2: md(kpi("오늘 트렌드",     trends["today"],         color="primary",  sub="Google·Naver"))
    with k3: md(kpi("오늘 잡 실행",    len(today_jobs),         color="success",  sub=f"성공 {job_ok}건"))
    with k4: md(kpi("누적 블로그 뷰",  f"{perf['total_views']:,}", color="primary", sub="post_analysis"))

    # 일일 리뷰 — 시스템 자기분석 최신 결과
    reviews = load_daily_review(days=1)
    if reviews:
        rev = reviews[0]
        section("📋 오늘의 시스템 자기분석 (Daily Review)")
        rev_cols = st.columns([1, 1, 2])
        with rev_cols[0]:
            md(kpi("오늘 발행 글", rev.get("posts_count", 0), color="success",
                   sub=f"품질점수 {rev.get('quality_score', '—'):.1f}" if rev.get("quality_score") else ""))
        with rev_cols[1]:
            md(kpi("평균 조회수", f'{rev.get("avg_views", 0):.0f}', color="primary", sub="오늘 발행 기준"))
        with rev_cols[2]:
            insights_text = rev.get("insights") or ""
            if insights_text:
                md(f'<div style="background:{_alpha(C["primary"],.05)};border:1px solid {_alpha(C["primary"],.2)};'
                   f'border-left:4px solid {C["primary"]};border-radius:10px;padding:14px 16px">'
                   f'<div style="font-size:14px;font-weight:700;color:{C["primary"]};margin-bottom:8px">💡 AI 인사이트</div>'
                   f'<div style="font-size:14px;color:{N["text2"]};line-height:1.8">{esc(insights_text[:300])}{"…" if len(insights_text)>300 else ""}</div>'
                   f'</div>')
        # common_issues
        try:
            issues = json.loads(rev.get("common_issues") or "[]")
            if issues:
                def _issue_label(iss) -> str:
                    if isinstance(iss, dict):
                        t = iss.get("type") or iss.get("issue") or str(iss)
                        c = iss.get("count")
                        return f'{t} ({c}건)' if c else t
                    return str(iss)
                issues_html = " ".join(
                    f'<span style="background:{_alpha(C["warn"],.12)};color:{C["warn"]};font-size:14px;'
                    f'padding:2px 10px;border-radius:9999px;margin:2px">{esc(_issue_label(iss))}</span>'
                    for iss in issues[:6]
                )
                md(f'<div style="margin-top:10px"><span style="font-size:14px;color:{N["text5"]};margin-right:8px">반복 이슈:</span>'
                   f'{issues_html}</div>')
        except Exception:
            pass

    section("최근 이벤트")
    events = load_recent_events(10)
    if events:
        md(table(["이벤트 타입", "소스", "시각"],
                 [[esc(e["event_type"]), esc(e["source"]), _fmt(e["created_at"])]
                  for e in events]))
    else:
        md(empty_state("이벤트 없음", "시스템이 조용히 동작 중입니다"))

    if today_jobs:
        section("오늘 잡 실행 이력")
        md(table(
            ["잡 이름", "소유", "시작", "결과", "소요(ms)"],
            [[esc(j.get("job_name", "—")),
              badge(OWNER_LABEL.get(j.get("owner_agent", ""), j.get("owner_agent", "?")), "muted"),
              _fmt(j.get("started_at", "")),
              ok_badge(j.get("success")),
              str(int(j.get("duration_ms") or 0))]
             for j in today_jobs[:15]]
        ))

# ──────────────────────────────────────────────────────────────────
# 레이더
# ──────────────────────────────────────────────────────────────────
with t_radar:
    trends = load_trends()

    section("오늘 트렌드 현황")
    r0, r1, r2 = st.columns(3)
    top1 = trends["top"][0] if trends["top"] else {}
    with r0: md(kpi("수집 키워드",   trends["today"],                              color="primary", sub="Google·Naver 합산"))
    with r1: md(kpi("섹터 수",       len(trends["sectors"]),                        color="success", sub="오늘 등장 섹터"))
    with r2: md(kpi("최고 기회점수", f'{top1.get("opportunity_score",0):.1f}' if top1 else "—",
                    color="warn", sub=top1.get("keyword", "—")[:20] if top1 else ""))

    # 수집 트리거 — KPI 아래 별도 행 (버튼과 KPI 혼재 방지)
    btn_col, _ = st.columns([1, 3])
    with btn_col:
        if st.button("📡 지금 수집", key="_collect_now", use_container_width=True):
            try:
                subprocess.Popen(
                    [sys.executable, "-c",
                     "import sys; sys.path.insert(0,'JARVIS03_RADAR'); "
                     "from radar_main import collect_today, save, push_to_shared; "
                     "d=collect_today(); save(d); push_to_shared(d)"],
                    cwd=str(BASE_DIR)
                )
                st.success("수집 시작! 30초 후 새로고침하세요.")
            except Exception as e:
                st.error(f"수집 실패: {e}")

    # 티커 테이프
    if trends["top"]:
        md(ticker_tape(trends["top"]))

    # 기회점수 TOP 키워드 테이블
    if trends["top"]:
        section("발행 기회 키워드 TOP 15 (기회점수 순)")
        rows = []
        max_score = max((t.get("opportunity_score", 0) or 0) for t in trends["top"]) or 1
        for i, t in enumerate(trends["top"], 1):
            score = t.get("opportunity_score", 0) or 0
            bar_w = int(score / max_score * 120)
            bar = (
                f'<div style="display:flex;align-items:center;gap:8px">'
                f'<div style="width:{bar_w}px;max-width:120px;height:8px;border-radius:4px;'
                f'background:{_alpha(C["warn"],.8)}"></div>'
                f'<span style="font-size:14px;color:{N["text2"]}">{score:.1f}</span></div>'
            )
            rows.append([
                f'<span style="font-size:14px;color:{N["text5"]};font-weight:700">{i}</span>',
                f'<b style="color:{N["text"]}">{esc(t.get("keyword", "—"))}</b>',
                sector_badge(t.get("sector", "기타")),
                bar,
                f'<span style="font-size:14px;color:{N["text2"]}">{t.get("score", 0)}</span>',
                badge(t.get("source", "—"), "muted"),
            ])
        md(table(["#", "키워드", "섹터", "기회점수", "트렌드점수", "소스"], rows, max_rows=15))
    else:
        md(empty_state(
            "오늘 트렌드 데이터가 없습니다",
            "'지금 수집' 버튼을 눌러 즉시 수집하거나, 자동 수집 스케줄을 확인하세요"
        ))

    # 섹터 분포
    if trends["sectors"]:
        section("섹터 분포")
        total_kw = sum(trends["sectors"].values())
        max_cnt  = max(trends["sectors"].values(), default=1) or 1
        n_cols = min(len(trends["sectors"]), 4)
        cols = st.columns(n_cols)
        for i, (sector, cnt) in enumerate(list(trends["sectors"].items())[:8]):
            col = cols[i % n_cols]
            c_hex = SECTOR_COLOR.get(sector, C["muted"])
            pct = int(cnt / total_kw * 100) if total_kw else 0
            bar_w = int(cnt / max_cnt * 100)
            with col:
                md(
                    f'<div style="background:{_alpha(c_hex,.06)};border:1px solid {_alpha(c_hex,.25)};'
                    f'border-top:3px solid {c_hex};border-radius:10px;padding:12px 16px;margin-bottom:12px">'
                    f'<div style="font-size:14px;color:{N["text2"]};margin-bottom:4px">{esc(sector)}</div>'
                    f'<div style="font-size:24px;font-weight:800;color:{c_hex};line-height:1.1">{cnt}개</div>'
                    f'<div style="margin:8px 0 4px;height:6px;border-radius:3px;background:{_alpha(c_hex,.15)}">'
                    f'<div style="width:{bar_w}%;height:100%;border-radius:3px;background:{c_hex}"></div></div>'
                    f'<div style="font-size:14px;color:{N["text5"]};font-weight:600">{pct}%</div>'
                    f'</div>'
                )

# ──────────────────────────────────────────────────────────────────
# 발행 관리
# ──────────────────────────────────────────────────────────────────
with t_pub:
    posts    = load_posts_stats()
    pipeline = load_pipeline()
    quality  = load_quality_stats()

    section("오늘 발행 현황")
    p0, p1, p2, p3, p4 = st.columns(5)
    p_today = pipeline.get("today", {})
    pend = p_today.get("suggested", 0) + p_today.get("pending", 0)
    with p0: md(kpi("오늘 발행",       posts.get("today", 0),      color="success"))
    with p1: md(kpi("이번 주",         posts.get("week", 0),        color="primary"))
    with p2: md(kpi("이번 달",         posts.get("month", 0),       color="primary"))
    with p3: md(kpi("파이프라인 대기", pend,  color="warn" if pend > 0 else "muted"))
    with p4: md(kpi("파이프라인 완료", p_today.get("done", 0),      color="success"))

    section("플랫폼별 오늘 발행")
    by_p = posts.get("by_platform", {})
    pp0, pp1 = st.columns(2)
    for col, (plat, label, color) in zip(
        [pp0, pp1],
        [("naver", "네이버", "success"), ("tistory", "티스토리", "warn")]
    ):
        with col: md(kpi(label, by_p.get(plat, 0), color=color, sub=plat))

    # 발행 상태 요약
    by_status = quality.get("by_status", {})
    section("분석 상태 요약")
    s0, s1, s2, s3, s4 = st.columns(5)
    with s0: md(kpi("승인 대기",  by_status.get("pending_approval", 0), color="warn",    sub="품질 관리 탭에서 처리"))
    with s1: md(kpi("분석 대기",  by_status.get("pending_analysis", 0), color="primary"))
    with s2: md(kpi("분석 완료",  by_status.get("analyzed", 0),         color="primary"))
    with s3: md(kpi("수정 완료",  by_status.get("revised", 0),          color="success"))
    with s4: md(kpi("건너뜀",     by_status.get("revise_skipped", 0) + by_status.get("rejected", 0), color="muted"))

    # 최근 발행 목록
    section("최근 발행 목록")
    recent = quality.get("recent", [])
    STATUS_COLOR = {
        "revised": "success", "approved": "warn", "revise_skipped": "muted",
        "analyzing": "primary", "done": "success", "pending_approval": "warn",
    }
    if recent:
        md(table(
            ["플랫폼", "제목", "상태", "발행일", "뷰"],
            [[badge(r["platform"], PLAT_COLOR.get(r["platform"], "muted")),
              (f'<a href="{esc(r["url"])}" target="_blank" '
               f'style="color:{N["text"]};text-decoration:none;font-size:14px">'
               f'{esc((r.get("title") or "")[:40])}{"…" if len(r.get("title",""))>40 else ""}</a>'
               if r.get("url") else
               f'<span style="font-size:14px">{esc((r.get("title") or "")[:40])}{"…" if len(r.get("title",""))>40 else ""}</span>'),
              qa_status_badge(r.get("status", "")),
              _fmt(r["created_at"]),
              str(r.get("current_views", 0) or 0)]
             for r in recent]
        ))
    else:
        md(empty_state("발행 이력이 없습니다", "첫 글이 발행되면 자동으로 채워집니다"))

    # 파이프라인 최근 테마
    pip_recent = pipeline.get("recent", [])
    if pip_recent:
        section("파이프라인 최근 테마")
        md(table(
            ["테마", "상태", "등록일"],
            [[esc(r["theme"]),
              badge(r["status"], "success" if r["status"] == "done" else "muted"),
              _fmt(r["created_at"])]
             for r in pip_recent]
        ))

# ──────────────────────────────────────────────────────────────────
# 품질 관리
# ──────────────────────────────────────────────────────────────────
with t_qa:
    analysis_all = load_analysis_history(150)

    # 상단 KPI
    _kpi_total   = len(analysis_all)
    _kpi_pending = sum(1 for r in analysis_all if r.get("status") == "pending_approval")
    _kpi_revised = sum(1 for r in analysis_all if r.get("status") == "revised")
    _kpi_reject  = sum(1 for r in analysis_all if r.get("status") == "rejected")
    _kpi_wait    = sum(1 for r in analysis_all if r.get("status") == "pending_analysis")

    qa_k0, qa_k1, qa_k2, qa_k3, qa_k4 = st.columns(5)
    with qa_k0: md(kpi("전체 분석",    _kpi_total,   color="primary"))
    with qa_k1:
        pulse_style = "animation:_pulse 1.5s ease-in-out infinite;" if _kpi_pending > 0 else ""
        md(f'<div style="{pulse_style}">{kpi("승인 대기", _kpi_pending, color="warn" if _kpi_pending > 0 else "muted", sub="즉시 처리 필요" if _kpi_pending > 0 else "")}</div>')
    with qa_k2: md(kpi("분석 대기",    _kpi_wait,    color="primary"))
    with qa_k3: md(kpi("수정 완료",    _kpi_revised, color="success"))
    with qa_k4: md(kpi("건너뜀",       _kpi_reject,  color="muted"))

    st.markdown("<br>", unsafe_allow_html=True)

    # 검색 / 필터 (filtered 는 expander 바깥에서 선언 — with...else 는 Python 문법 오류)
    qa_q      = st.session_state.get("qa_q", "")
    qa_plat   = st.session_state.get("qa_plat", "전체")
    qa_status = st.session_state.get("qa_status", "전체")

    with st.expander("검색 / 필터", expanded=False):
        f0, f1, f2 = st.columns([2, 1, 1])
        with f0: qa_q      = st.text_input("키워드 검색", key="qa_q", placeholder="제목·테마에서 검색")
        with f1: qa_plat   = st.selectbox("플랫폼", ["전체", "naver", "tistory"], key="qa_plat")
        with f2: qa_status = st.selectbox("상태", ["전체"] + list(QA_STATUS_LABEL.keys()), key="qa_status")

    filtered = analysis_all
    if qa_q:
        ql = qa_q.lower()
        filtered = [r for r in filtered if ql in (r.get("title") or "").lower() or ql in (r.get("theme") or "").lower()]
    if qa_plat != "전체":
        filtered = [r for r in filtered if r.get("platform") == qa_plat]
    if qa_status != "전체":
        filtered = [r for r in filtered if r.get("status") == qa_status]
    md(f'<div style="font-size:14px;color:{N["text5"]};margin-bottom:8px">'
       f'필터 결과: <b style="color:{C["primary"]}">{len(filtered)}</b>건 / 전체 {len(analysis_all)}건</div>')

    # 서브 탭
    qa_t1, qa_t2, qa_t3 = st.tabs(["승인 대기", "처리 완료", "전체 이력"])

    # ── 승인 대기 ────────────────────────────────────────────────
    with qa_t1:
        pending_list = [r for r in filtered if r.get("status") == "pending_approval"]
        if not pending_list:
            md(empty_state(
                "대기 중인 개선 제안이 없습니다",
                "발행된 글이 자동 분석되면 여기에 나타납니다 (매 5분 주기)"
            ))
        else:
            for row in pending_list:
                sugg = []
                try:
                    sugg = json.loads(row.get("suggestions") or "[]")
                except Exception:
                    pass
                high = sum(1 for s in sugg if s.get("priority") == "high")
                mid  = sum(1 for s in sugg if s.get("priority") == "medium")
                plat_em = QA_PLATFORM_EMOJI.get(row.get("platform", ""), "📝")
                with st.expander(
                    f"{plat_em} [{row.get('platform','').upper()}] "
                    f"{row.get('theme', row.get('title', '?'))}"
                    f" — 제안 {len(sugg)}개 (🔴{high} 🟡{mid})",
                    expanded=True
                ):
                    col_info, col_act = st.columns([3, 1])
                    with col_info:
                        if row.get("title"):
                            st.markdown(f"**제목:** {row['title']}")
                        if row.get("url"):
                            md(f'<a href="{esc(row["url"])}" target="_blank" '
                               f'style="font-size:14px;color:{C["primary"]};text-decoration:none">'
                               f'🔗 {esc(row["url"][:70])}{"…" if len(row["url"])>70 else ""}</a>')
                        md(f'<div style="font-size:14px;color:{N["text5"]};margin-top:4px">'
                           f'분석 시각: {esc(str(row.get("analyzed_at", "—")))} · {esc(row.get("platform", "").upper())}</div>')
                    with col_act:
                        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
                        if st.button("✅ 승인 + 자동수정", key=f"qa_ok_{row['id']}", type="primary", use_container_width=True):
                            try:
                                from shared import db as _sdb
                                _sdb.approve_analysis(row["id"], {"suggestions": sugg, "mode": "all"})
                                revise_py = BASE_DIR / "JARVIS02_WRITER" / "revise_adapter.py"
                                if revise_py.exists():
                                    subprocess.Popen([sys.executable, str(revise_py), str(row["id"])])
                                st.success("승인 완료! 자동 수정 진행 중...")
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"승인 오류: {e}")
                        if st.button("❌ 건너뜀", key=f"qa_skip_{row['id']}", use_container_width=True):
                            try:
                                from shared import db as _sdb
                                _sdb.reject_analysis(row["id"])
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"건너뜀 오류: {e}")
                    _render_suggestion_diff(sugg)

    # ── 처리 완료 ────────────────────────────────────────────────
    with qa_t2:
        done_list = [r for r in filtered if r.get("status") in ("approved", "rejected", "revised", "revise_skipped")]
        if not done_list:
            md(empty_state("처리된 항목이 없습니다"))
        else:
            for row in done_list[:30]:
                label, color = QA_STATUS_LABEL.get(row.get("status", ""), ("?", N["text5"]))
                plat_em = QA_PLATFORM_EMOJI.get(row.get("platform", ""), "📝")
                sugg = []
                try:
                    sugg = json.loads(row.get("suggestions") or "[]")
                except Exception:
                    pass
                with st.expander(
                    f"{plat_em} [{row.get('platform','').upper()}] "
                    f"{row.get('theme', row.get('title', '?'))} — {label}",
                    expanded=False
                ):
                    title_str = row.get("title", "")
                    decided   = row.get("decided_at", "—")
                    st.markdown(f"**제목:** {title_str} | **결정:** {decided}")
                    if row.get("status") == "revised":
                        st.success(f"수정 완료: {row.get('revised_at', '')}")
                    _render_suggestion_diff(sugg)

    # ── 전체 이력 ─────────────────────────────────────────────────
    with qa_t3:
        if not filtered:
            md(empty_state("분석 이력 없음", "글이 발행되면 post_quality_analyzer가 자동으로 분석합니다"))
        else:
            hist_rows = []
            for row in filtered[:80]:
                plat_em = QA_PLATFORM_EMOJI.get(row.get("platform", ""), "·")
                title_s = (row.get("title") or row.get("theme") or "—")[:45]
                title_cell = (
                    f'<a href="{esc(row["url"])}" target="_blank" '
                    f'style="color:{N["text"]};text-decoration:none;font-size:14px">'
                    f'{esc(title_s)}{"…" if len(row.get("title",""))>45 else ""}</a>'
                    if row.get("url") else
                    f'<span style="font-size:14px">{esc(title_s)}{"…" if len(row.get("title",""))>45 else ""}</span>'
                )
                hist_rows.append([
                    f'{plat_em} {badge(row.get("platform",""), PLAT_COLOR.get(row.get("platform",""), "muted"))}',
                    title_cell,
                    qa_status_badge(row.get("status", "")),
                    _fmt(row.get("created_at", "")),
                    str(row.get("current_views", 0) or 0),
                ])
            md(table(["플랫폼", "제목", "상태", "발행일", "뷰"], hist_rows, max_rows=80))

# ──────────────────────────────────────────────────────────────────
# 성과
# ──────────────────────────────────────────────────────────────────
with t_perf:
    perf     = load_performance()
    kw_perf  = load_keyword_performance(30)

    # 상단 KPI — 서브탭 위
    pf0, pf1, pf2, pf3 = st.columns(4)
    pv = perf.get("platform_views", {})
    with pf0: md(kpi("누적 총 뷰",  f"{perf['total_views']:,}",     color="primary", sub="전 플랫폼 합산"))
    with pf1: md(kpi("네이버 뷰",   f"{pv.get('naver',0):,}",        color="success"))
    with pf2: md(kpi("티스토리 뷰", f"{pv.get('tistory',0):,}",      color="warn"))
    _ = pf3  # 빈 컬럼 (4열 레이아웃 유지)

    st.markdown("<br>", unsafe_allow_html=True)
    perf_t1, perf_t2, perf_t3 = st.tabs(["📈 추세 & 상위 포스트", "🔑 키워드 성과", "📅 일일 리뷰 히스토리"])

    # ── 추세 & 상위 포스트 ────────────────────────────────────────
    with perf_t1:
        hist = perf.get("history", [])
        if hist:
            section("7일 뷰 추세")
            max_v = max((h["v"] for h in hist), default=1) or 1
            bar_html = '<div style="display:flex;align-items:flex-end;gap:6px;height:88px;margin:12px 0">'
            for h in hist:
                bar_h = max(6, int(h["v"] / max_v * 72))
                bar_html += (
                    f'<div style="display:flex;flex-direction:column;align-items:center;flex:1;gap:4px">'
                    f'<div style="font-size:14px;color:{N["text5"]}">{h["v"]}</div>'
                    f'<div style="height:{bar_h}px;width:100%;background:{_alpha(C["primary"],.6)};'
                    f'border-radius:4px 4px 0 0;transition:height 0.3s"></div>'
                    f'<div style="font-size:14px;color:{N["text5"]}">{h["d"][5:]}</div>'
                    f'</div>'
                )
            bar_html += '</div>'
            md(bar_html)

        top_posts = perf.get("top_posts", [])
        if top_posts:
            section("뷰 상위 포스트")
            md(table(
                ["플랫폼", "제목", "뷰", "네이버 순위", "발행일"],
                [[badge(r["platform"], PLAT_COLOR.get(r["platform"], "muted")),
                  (f'<a href="{esc(r.get("url",""))}" target="_blank" style="color:{N["text"]};text-decoration:none;font-size:14px">'
                   f'{esc((r.get("title") or "")[:45])}{"…" if len(r.get("title",""))>45 else ""}</a>'
                   if r.get("url") else
                   f'<span style="font-size:14px">{esc((r.get("title") or "")[:45])}{"…" if len(r.get("title",""))>45 else ""}</span>'),
                  f'<b style="color:{C["success"]}">{r.get("current_views",0):,}</b>',
                  f'<b style="color:{C["warn"]}">{r["naver_rank"]}위</b>' if r.get("naver_rank") else "—",
                  _fmt(r["created_at"])]
                 for r in top_posts]
            ))

        naver_ranked = perf.get("naver_ranked", [])
        if naver_ranked:
            section("네이버 순위 포스트")
            md(table(
                ["순위", "제목", "뷰", "발행일"],
                [[f'<b style="color:{C["warn"]}">{r["naver_rank"]}위</b>',
                  f'<span style="font-size:14px">{esc((r.get("title") or "")[:45])}</span>',
                  str(r.get("current_views", 0) or 0),
                  _fmt(r["created_at"])]
                 for r in naver_ranked]
            ))

        if not hist and not top_posts:
            md(empty_state("아직 뷰 데이터가 없습니다", "성과 수집 잡(23:00) 실행 후 채워집니다"))

    # ── 키워드 성과 ───────────────────────────────────────────────
    with perf_t2:
        if kw_perf:
            section("키워드별 누적 성과 (composite_score 순)")
            kp_max = max((r.get("composite_score") or 0 for r in kw_perf), default=1) or 1
            kw_rows = []
            for i, r in enumerate(kw_perf, 1):
                cs = r.get("composite_score") or 0
                bar_w = int(cs / kp_max * 100)
                bar_cell = (
                    f'<div style="display:flex;align-items:center;gap:8px">'
                    f'<div style="width:{bar_w}px;max-width:100px;height:6px;border-radius:3px;'
                    f'background:{_alpha(C["primary"],.7)}"></div>'
                    f'<span style="font-size:14px;color:{N["text2"]}">{cs:.1f}</span></div>'
                )
                best_rank = r.get("best_rank")
                rank_cell = (f'<b style="color:{C["warn"]}">{best_rank}위</b>'
                             if best_rank else f'<span style="color:{N["text5"]};font-size:14px">—</span>')
                kw_rows.append([
                    f'<span style="font-size:14px;color:{N["text5"]};font-weight:700">{i}</span>',
                    f'<b style="color:{N["text"]};font-size:14px">{esc(r.get("keyword","—"))}</b>',
                    f'<span style="font-size:14px;color:{C["success"]}">{int(r.get("avg_views",0)):,}</span>',
                    f'<span style="font-size:14px">{int(r.get("best_views",0)):,}</span>',
                    rank_cell,
                    bar_cell,
                    f'<span style="font-size:14px;color:{N["text5"]}">{r.get("total_posts",0) or 0}건</span>',
                ])
            md(table(["#", "키워드", "평균 뷰", "최고 뷰", "최고 네이버 순위", "종합점수", "발행 수"], kw_rows, max_rows=30))
        else:
            md(empty_state("키워드 성과 데이터 없음", "performance_collector가 수집한 후 채워집니다"))

    # ── 일일 리뷰 히스토리 ───────────────────────────────────────
    with perf_t3:
        reviews = load_daily_review(days=7)
        if reviews:
            for rev in reviews:
                q_score = rev.get("quality_score")
                q_color = C["success"] if (q_score or 0) >= 70 else (C["warn"] if (q_score or 0) >= 40 else C["danger"])
                with st.expander(
                    f"📅 {rev.get('review_date','—')} — 발행 {rev.get('posts_count',0)}건"
                    f" | 품질점수 {q_score:.1f}" if q_score else f"📅 {rev.get('review_date','—')}",
                    expanded=(reviews.index(rev) == 0)
                ):
                    rc0, rc1, rc2 = st.columns(3)
                    with rc0: md(kpi("발행 글", rev.get("posts_count", 0), color="success"))
                    with rc1: md(kpi("평균 뷰", f'{rev.get("avg_views",0):.0f}', color="primary"))
                    with rc2:
                        if q_score:
                            md(kpi("품질 점수", f"{q_score:.1f}", color="success" if q_score >= 70 else ("warn" if q_score >= 40 else "danger")))
                    insights_text = rev.get("insights") or ""
                    if insights_text:
                        md(f'<div style="background:{_alpha(C["primary"],.04)};border-left:3px solid {C["primary"]};'
                           f'border-radius:6px;padding:12px 16px;margin:10px 0">'
                           f'<div style="font-size:14px;font-weight:700;color:{C["primary"]};margin-bottom:6px">💡 인사이트</div>'
                           f'<div style="font-size:14px;color:{N["text2"]};line-height:1.8">{esc(insights_text)}</div></div>')
                    try:
                        directives = json.loads(rev.get("next_directives") or "[]")
                        if directives:
                            dir_html = "".join(
                                f'<div style="font-size:14px;color:{N["text"]};padding:4px 0;border-bottom:1px solid {_alpha(N["bdr"],.4)}">'
                                f'→ {esc(str(d))}</div>' for d in directives[:5]
                            )
                            md(f'<div style="margin-top:10px">'
                               f'<div style="font-size:14px;font-weight:700;color:{C["success"]};margin-bottom:6px">▶ 다음 지시사항</div>'
                               f'{dir_html}</div>')
                    except Exception:
                        pass
        else:
            md(empty_state("일일 리뷰 없음", "daily_review 잡(22:00) 실행 후 채워집니다"))

# ──────────────────────────────────────────────────────────────────
# AI 학습
# ──────────────────────────────────────────────────────────────────
with t_ai:
    learn  = load_learning_status()
    fbpen  = load_feedback_penalty(20)

    # KPI
    w_list  = learn.get("weights", [])
    bt_list = learn.get("backtest", [])
    ins_list= learn.get("insights", [])
    ll_stat = learn.get("learn_log", {})

    latest_bt_score = bt_list[0].get("score") if bt_list else None
    ai_k0, ai_k1, ai_k2, ai_k3 = st.columns(4)
    with ai_k0: md(kpi("학습 세션",   len(w_list),            color="primary", sub="learned_weights"))
    with ai_k1: md(kpi("학습 인사이트", len(ins_list),          color="success", sub="누적 학습 결과"))
    with ai_k2: md(kpi("예측 데이터",  ll_stat.get("cnt", 0),  color="primary", sub="learn_log 누적"))
    with ai_k3:
        mae = ll_stat.get("mae")
        md(kpi("예측 오차(MAE)",
               f"{mae:.1f}" if mae is not None else "—",
               color="success" if (mae or 0) < 50 else "warn",
               sub="뷰 수 기준"))

    st.markdown("<br>", unsafe_allow_html=True)
    ai_t1, ai_t2, ai_t3, ai_t4 = st.tabs(["⚖️ 학습된 가중치", "📊 백테스트 이력", "💡 학습 인사이트", "🚫 피드백 패턴"])

    # ── 학습된 가중치 ─────────────────────────────────────────────
    with ai_t1:
        if w_list:
            section("AI 학습 가중치 (최신)")
            for w in w_list:
                try:
                    wdata = json.loads(w.get("weights_json") or "{}")
                except Exception:
                    wdata = {}
                bs = w.get("backtest_score")
                bs_color = C["success"] if (bs or 0) >= 0.7 else (C["warn"] if (bs or 0) >= 0.5 else C["danger"])
                with st.expander(
                    f"{'[' + w.get('weight_type','?') + ']'} 학습 {_fmt(w.get('trained_at',''))} "
                    f"| 백테스트 {f'{bs:.3f}' if bs else '—'}",
                    expanded=(w_list.index(w) == 0)
                ):
                    if wdata:
                        w_cols = st.columns(min(len(wdata), 4))
                        weight_names = {
                            "w_trend": "트렌드 가중치", "w_perf": "성과 부스트",
                            "w_fresh": "신선도", "w_velocity": "속도",
                            "w_competition": "경쟁 패널티", "intercept": "편향(절편)"
                        }
                        for ci, (k, v) in enumerate(wdata.items()):
                            with w_cols[ci % min(len(wdata), 4)]:
                                v_color = C["danger"] if v < 0 else C["success"]
                                md(kpi(weight_names.get(k, k), f"{v:.3f}",
                                       color="danger" if v < 0 else "success"))
                    if bs:
                        md(f'<div style="margin-top:10px;font-size:14px;color:{bs_color};font-weight:700">'
                           f'백테스트 점수: {bs:.3f} '
                           f'({"우수" if bs>=0.7 else "보통" if bs>=0.5 else "개선 필요"})</div>')
        else:
            md(empty_state("학습된 가중치 없음", "train_weights 잡(매일 04:00) 실행 후 채워집니다"))

    # ── 백테스트 이력 ────────────────────────────────────────────
    with ai_t2:
        if bt_list:
            section("백테스트 이력 (최근 14건)")
            max_score = max((r.get("score") or 0 for r in bt_list), default=1) or 1
            bt_rows = []
            for r in bt_list:
                sc = r.get("score") or 0
                bar_w = int(sc / max(max_score, 1) * 100)
                sc_color = C["success"] if sc >= 0.7 else (C["warn"] if sc >= 0.5 else C["danger"])
                bar_cell = (
                    f'<div style="display:flex;align-items:center;gap:8px">'
                    f'<div style="width:{bar_w}px;max-width:100px;height:6px;border-radius:3px;background:{sc_color}"></div>'
                    f'<span style="font-size:14px;color:{sc_color};font-weight:700">{sc:.3f}</span></div>'
                )
                bt_rows.append([
                    _fmt(r.get("tested_at", "")),
                    f'<span style="font-size:14px">{esc(r.get("backtest_type","—"))}</span>',
                    bar_cell,
                ])
            md(table(["시각", "유형", "점수"], bt_rows, max_rows=14))
        else:
            md(empty_state("백테스트 이력 없음", "train_weights 잡 실행 후 채워집니다"))

    # ── 학습 인사이트 ────────────────────────────────────────────
    with ai_t3:
        if ins_list:
            section("누적 학습 인사이트 (발생 빈도 순)")
            for ins in ins_list[:15]:
                i_type  = ins.get("insight_type", "")
                i_scope = ins.get("scope", "")
                i_w     = ins.get("weight") or 0
                i_color = C["success"] if i_w > 0 else (C["danger"] if i_w < 0 else C["primary"])
                md(
                    f'<div style="background:{_alpha(i_color,.04)};border:1px solid {_alpha(i_color,.18)};'
                    f'border-left:3px solid {i_color};border-radius:8px;padding:12px 16px;margin:6px 0">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px">'
                    f'<span style="font-size:14px;font-weight:700;color:{i_color}">{esc(i_type)}</span>'
                    f'<div style="display:flex;gap:6px">'
                    f'<span style="font-size:14px;color:{N["text5"]};background:{N["card"]};padding:2px 8px;border-radius:4px">{esc(i_scope)}</span>'
                    f'<span style="font-size:14px;color:{i_color};font-weight:700">발생 {ins.get("occurrences",0)}회</span>'
                    f'</div></div>'
                    f'<div style="font-size:14px;color:{N["text"]};margin-bottom:6px">{esc(ins.get("description",""))}</div>'
                    f'<div style="font-size:14px;color:{N["text2"]};font-style:italic">▶ {esc(ins.get("directive",""))}</div>'
                    f'</div>'
                )
        else:
            md(empty_state("학습 인사이트 없음", "시스템이 충분한 데이터를 모으면 자동 생성됩니다"))

    # ── 피드백 패턴 ──────────────────────────────────────────────
    with ai_t4:
        if fbpen:
            section("사용자 거부 누적 패턴 (penalty_score 순)")
            fp_rows = []
            for r in fbpen[:20]:
                ps = r.get("penalty_score") or 0
                ps_color = C["danger"] if ps >= 3 else (C["warn"] if ps >= 1 else C["muted"])
                fp_rows.append([
                    f'<b style="color:{N["text"]};font-size:14px">{esc(str(r.get("theme") or r.get("keyword","—")))}</b>',
                    f'<span style="font-size:14px;color:{ps_color};font-weight:700">{ps:.1f}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{int(r.get("penalty_count",0) or 0)}회</span>',
                ])
            md(table(["테마/키워드", "패널티 점수", "거부 횟수"], fp_rows, max_rows=20))
            md(f'<div style="font-size:14px;color:{N["text5"]};margin-top:8px">'
               f'⚠️ 패널티 높은 항목은 opportunity_score 계산 시 자동 차감됩니다.</div>')
        else:
            md(empty_state("피드백 패턴 없음", "사용자가 개선 제안을 거부하면 자동으로 기록됩니다"))

# ──────────────────────────────────────────────────────────────────
# 오류 관리 (JARVIS07 GUARDIAN)
# ──────────────────────────────────────────────────────────────────
with t_err:
    gd       = load_guardian_stats()
    gd_all   = load_guardian_stats_alltime()
    g_trend  = load_guardian_trend(days=14)
    g_source = load_guardian_source_stats(days=7)

    # ── 누적 전체 KPI (영구 보존) ────────────────────────────────
    section("🗄️ 누적 전체 (영구 보존)")
    a0, a1, a2, a3, a4, a5, a6 = st.columns(7)
    _auto_fix_all = gd_all["fixed"] + gd_all["manual"]
    _fix_rate     = int(_auto_fix_all / gd_all["total"] * 100) if gd_all["total"] > 0 else 0
    with a0: md(kpi("총 누적 오류", gd_all["total"],   color="primary",
                    sub=f"최초 수집: {gd_all['first']}"))
    with a1: md(kpi("자동수정 ✅",  gd_all["fixed"],   color="success", sub="GUARDIAN 누적"))
    with a2: md(kpi("수동수정",      gd_all["manual"],  color="success", sub="사람 누적"))
    with a3: md(kpi("수정률",        f"{_fix_rate}%",   color="success" if _fix_rate >= 50 else "warn",
                    sub="(자동+수동) / 전체"))
    with a4: md(kpi("미해결",        gd_all["new"],     color="danger" if gd_all["new"] > 0 else "muted",
                    sub="처리 대기"))
    with a5: md(kpi("수정불가",      gd_all["wontfix"], color="warn"   if gd_all["wontfix"] > 0 else "muted"))
    with a6: md(kpi("무시됨",        gd_all["ignored"], color="muted"))

    st.divider()

    # ── 상단 KPI (7일) ───────────────────────────────────────────
    section("📅 최근 7일")
    e0, e1, e2, e3, e4, e5, e6 = st.columns(7)
    with e0: md(kpi("총 오류 (7일)", gd["total"], color="primary", sub="수집 건수"))
    with e1: md(kpi("신규 🆕",     gd["new"],
                    color="danger" if gd["new"] > 0 else "success",
                    sub="처리 대기"))
    with e2: md(kpi("분석 중",     gd["analyzing"], color="primary"))
    with e3: md(kpi("자동 수정 불가", gd["wontfix"],
                    color="warn" if gd["wontfix"] > 0 else "muted",
                    sub="시스템 판정"))
    with e4: md(kpi("무시됨",      gd["ignored"],   color="muted"))
    with e5: md(kpi("자동수정 ✅", gd["fixed"],     color="success", sub="GUARDIAN 자동"))
    with e6: md(kpi("수동수정",    gd["manual"],
                    color="success" if gd["manual"] > 0 else "muted",
                    sub="사람이 수정 완료"))

    # ── 서브탭 ───────────────────────────────────────────────────
    _new_label  = f"🆕 신규 ({gd['new']})" if gd["new"] > 0 else "🆕 신규"
    # 수동검토 탭 카운트: wontfix 전체 + (new/analyzing 중 critical/high)
    _crit_unresolved = con_for_count = None
    try:
        import sqlite3 as _s3
        _c = _s3.connect(str(DB_PATH))
        _crit_unresolved = _c.execute("""
            SELECT COUNT(*) FROM error_log
            WHERE (status = 'wontfix'
                   OR (status IN ('new','analyzing') AND severity IN ('critical','high')))
              AND timestamp >= datetime('now','-60 days')
        """).fetchone()[0]
        _c.close()
    except Exception:
        _crit_unresolved = gd["new"] + gd["wontfix"]  # fallback
    _crit_label = f"⚠️ 수동검토 ({_crit_unresolved})" if _crit_unresolved > 0 else "⚠️ 수동검토"
    et1, et2, et3, et4, et5 = st.tabs([
        "📊 현황", _new_label, "✅ 자동수정 완료", _crit_label, "📋 전체 이력",
    ])

    # ── [현황] ────────────────────────────────────────────────────
    with et1:
        # 심각도별 분포
        section("심각도별 분포 (7일)")
        sv0, sv1, sv2 = st.columns(3)
        sev_items = [
            ("HIGH",     gd["high"],     "warn"),
            ("MEDIUM",   gd["medium"],   "primary"),
            ("LOW",      gd["low"],      "muted"),
        ]
        for col, (label, cnt, color) in zip([sv0, sv1, sv2], sev_items):
            pct = int(cnt / gd["total"] * 100) if gd["total"] > 0 else 0
            with col:
                md(kpi(label, cnt, color=color, sub=f"전체의 {pct}%"))

        # 일별 추이
        if g_trend:
            section("일별 발생 추이 (최근 14일)")
            max_total = max((r["total"] for r in g_trend), default=1) or 1
            bar_rows = []
            for r in g_trend:
                day  = (r["day"] or "")[-5:]   # MM-DD
                tot  = r["total"]
                bar_w = max(2, int(tot / max_total * 40))
                crit = r["crit"]
                fix  = r["fixed"]
                bar_html = (
                    f'<div style="display:flex;align-items:center;gap:8px">'
                    f'<div style="width:{bar_w * 6}px;max-width:200px;height:14px;'
                    f'background:{C["primary"]};border-radius:3px;opacity:0.8"></div>'
                    f'<span style="font-size:14px;color:{N["text2"]}">{tot}</span>'
                    f'</div>'
                )
                bar_rows.append([
                    f'<span style="font-size:14px;color:{N["text2"]}">{day}</span>',
                    bar_html,
                    badge(str(crit), "danger") if crit else f'<span style="color:{N["text5"]}">—</span>',
                    badge(str(fix),  "success") if fix  else f'<span style="color:{N["text5"]}">—</span>',
                ])
            md(table(["날짜", "발생 건수", "CRITICAL", "자동수정"], bar_rows, max_rows=14))
        else:
            md(empty_state("추이 데이터 없음", "오류가 수집되면 일별 추이가 표시됩니다"))

        # ★ 자가 진단 학습 곡선 카드 (사용자 박제 2026-05-15) — Opus 회차별 누적 추이
        try:
            import sqlite3 as _s3
            _c = _s3.connect(str(DB_PATH))
            _c.row_factory = _s3.Row
            _srr = _c.execute("""
                SELECT id, ran_at, total_fixed, patterns_count, hits_total,
                       score_quality, score_learning, score_vision,
                       next_suggestion, elapsed_sec
                FROM self_repair_runs
                ORDER BY id DESC LIMIT 10
            """).fetchall()
            _c.close()
            if _srr:
                section("🤖 자가 진단 학습 곡선 (최근 10회 — Opus 4.6)")
                # KPI — 최신 / 누적 / 추세
                _latest = _srr[0]
                _oldest = _srr[-1] if len(_srr) > 1 else _latest
                _total_runs = len(_srr)
                _avg_fixed = sum(r["total_fixed"] for r in _srr) / max(1, _total_runs)

                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    md(kpi("총 회차", _total_runs, color="primary",
                           sub="최근 10회 누적"))
                with sc2:
                    # actionable_hits = 실제 LLM 절약 (learned_patterns stats 에서)
                    try:
                        from JARVIS07_GUARDIAN.pattern_fixer import stats as _pf_s
                        _real_llm_saved = _pf_s().get("actionable_hits", 0)
                    except Exception:
                        _real_llm_saved = _latest["hits_total"]
                    md(kpi("실제 LLM 절약",
                           _real_llm_saved,
                           color="success" if _real_llm_saved > 0 else "muted",
                           sub="auto_patch+static+llm hit 횟수"))
                with sc3:
                    _pat_growth = _latest["patterns_count"] - _oldest["patterns_count"]
                    md(kpi("패턴 증가",
                           f"+{_pat_growth}" if _pat_growth >= 0 else str(_pat_growth),
                           color="success" if _pat_growth > 0 else "muted",
                           sub=f"학습 곡선 (현재 {_latest['patterns_count']}건)"))
                with sc4:
                    md(kpi("평균 수정/회",
                           f"{_avg_fixed:.1f}",
                           color="primary", sub="회차당 수정 건수"))

                # 회차별 상세 테이블
                _srr_rows = []
                for r in _srr[:10]:
                    _ts = (r["ran_at"] or "")[:16].replace("T", " ")
                    _qual = r["score_quality"] or 0
                    _learn = r["score_learning"] or 0
                    _vision = r["score_vision"] or 0
                    _suggest = (r["next_suggestion"] or "")[:50]
                    _srr_rows.append([
                        f'<span style="font-size:14px">#{r["id"]}</span>',
                        f'<span style="font-size:14px;color:{N["text2"]}">{_ts}</span>',
                        badge(str(r["total_fixed"] or 0),
                              "success" if r["total_fixed"] else "muted"),
                        f'<span style="font-size:14px;color:{N["text2"]}">'
                        f'{r["patterns_count"]}</span>',
                        f'<span style="font-size:14px;color:{N["text2"]}">'
                        f'{r["hits_total"]}</span>',
                        f'<span style="font-size:14px;color:{N["text2"]}">'
                        f'{_qual}/{_learn}/{_vision}</span>',
                        f'<span style="font-size:14px;color:{N["text5"]}">'
                        f'{_suggest}</span>',
                    ])
                md(table(
                    ["#", "시각", "수정", "패턴", "누적hits", "점수(품질/학습/비전)", "다음 회차"],
                    _srr_rows, max_rows=10,
                ))
            else:
                md(empty_state(
                    "자가 진단 회차 데이터 없음",
                    "다음 08:30 / 18:00 자가 진단 후 학습 곡선이 표시됩니다",
                ))
        except Exception as _e:
            md(empty_state(f"자가 진단 통계 로드 실패: {_e}"))

        # ★ 학습 효과 카드 (사용자 박제 2026-05-15) — pattern_fixer 누적 학습 통계
        try:
            from JARVIS07_GUARDIAN.pattern_fixer import stats as _pf_stats
            _ls = _pf_stats()
            section("🧠 학습 시스템 — 자동 수정 능력")
            lc1, lc2, lc3, lc4 = st.columns(4)
            with lc1:
                md(kpi("전체 패턴", _ls.get("total_patterns", 0),
                       color="muted", sub="등록된 fingerprint"))
            with lc2:
                _act = _ls.get("actionable", 0)
                md(kpi("자동수정 가능", _act,
                       color="success" if _act > 0 else "muted",
                       sub="static + llm + auto_patch"))
            with lc3:
                _real_hits = _ls.get("actionable_hits", 0)
                md(kpi("실제 LLM 절약", _real_hits,
                       color="success" if _real_hits > 0 else "muted",
                       sub="자동수정 가능 패턴 hit 횟수"))
            with lc4:
                _by_tier = _ls.get("by_tier", {}) or {}
                _tier_str = " / ".join(
                    f"{t[0].upper()}{v}" for t, v in
                    sorted(_by_tier.items()) if v > 0
                )
                md(kpi("tier 분포",
                       _tier_str or "—",
                       color="primary",
                       sub="A=auto_patch L=llm M=manual S=static"))
            # Top 5 패턴
            _top5 = _ls.get("top5", [])[:5]
            if _top5:
                _top_rows = []
                for p in _top5:
                    _et  = p.get("error_type", "?")
                    _hit = p.get("hit_count", 0)
                    _fx  = p.get("fixer", "—")
                    _fp  = (p.get("fingerprint", "") or "")[:60]
                    _top_rows.append([
                        f'<span style="font-size:14px">{_et}</span>',
                        badge(str(_hit), "success" if _hit > 1 else "muted"),
                        f'<span style="font-size:14px;color:{N["text2"]}">{_fx}</span>',
                        f'<span style="font-size:14px;color:{N["text5"]};'
                        f'font-family:monospace">{_fp}</span>',
                    ])
                md(table(["오류 타입", "hit", "fixer", "fingerprint"],
                          _top_rows, max_rows=5))

            # ★ ADR 008 Phase 4 (사용자 박제 2026-05-17) — 도메인별 학습 곡선
            _by_dom  = _ls.get("by_domain", {}) or {}
            _by_dom_h = _ls.get("by_domain_hits", {}) or {}
            if _by_dom:
                section("🌐 도메인별 학습 분포 (ADR 008)")
                # 도메인별 색상 매핑 (5색 + neutral)
                _dom_color = {
                    "image":        "primary",  "publish":     "success",
                    "category":     "warn",     "credentials": "warn",
                    "length":       "primary",  "constitution": "danger",
                    "schedule":     "muted",    "tools":       "muted",
                    "guardian":     "danger",   "infra":       "muted",
                    "master":       "muted",    "radar":       "primary",
                    "writer":       "success",  "unknown":     "muted",
                }
                _skew_threshold = 25  # ADR 008 Phase 4 임계값
                _dom_rows = []
                for _d, _n in sorted(_by_dom.items(), key=lambda x: -x[1]):
                    _h = _by_dom_h.get(_d, 0)
                    _col = _dom_color.get(_d, "muted")
                    # skew 경고 표시
                    _skew_badge = ""
                    if _d != "unknown" and _n >= _skew_threshold:
                        _skew_badge = badge(f"⚠️ skew ≥{_skew_threshold}", "danger")
                    _dom_rows.append([
                        badge(_d, _col),
                        f'<span style="font-size:16px;font-weight:bold">{_n}</span>',
                        f'<span style="font-size:14px;color:{N["text2"]}">{_h}</span>',
                        _skew_badge,
                    ])
                md(table(
                    ["도메인", "패턴 수", "총 hit", "skew 신호"],
                    _dom_rows, max_rows=20,
                ))
                # skew 검출 시 안내
                _skewed = [d for d, n in _by_dom.items()
                           if d != "unknown" and n >= _skew_threshold]
                if _skewed:
                    md(empty_state(
                        f"⚠️ 도메인 skew 검출: {', '.join(_skewed)} — "
                        f"단순 학습 누적 한계, 근본 리팩터 검토 필요 (ADR 008 매트릭스 재검토 트리거)",
                        "danger",
                    ))
        except Exception as _e:
            md(empty_state(f"학습 통계 로드 실패: {_e}"))

        # ★ RL 모델 카드 (사용자 박제 2026-06-07 — ERRORS [258])
        try:
            from JARVIS07_GUARDIAN.rl_fixer import rl_stats as _rl_stats
            _rs = _rl_stats()
            if not _rs.get("error"):
                section("🎯 RL 학습 모델 — Tier 1.5 (SGDClassifier · ε=0.15)")
                rc1, rc2, rc3, rc4 = st.columns(4)
                with rc1:
                    _exists = _rs.get("model_exists", False)
                    md(kpi("모델 파일", "OK" if _exists else "—",
                           color="success" if _exists else "danger",
                           sub="rl_model.pkl"))
                with rc2:
                    _uc = _rs.get("update_count", 0)
                    md(kpi("보상 업데이트", _uc,
                           color="success" if _uc > 0 else "muted",
                           sub="누적 partial_fit 호출"))
                with rc3:
                    _cn = _rs.get("coef_norm", 0.0)
                    md(kpi("가중치 norm", f"{_cn:.2f}",
                           color="primary" if _cn > 5.0 else "muted",
                           sub="모델 학습 강도"))
                with rc4:
                    _na = _rs.get("n_actions", 0)
                    md(kpi("액션 수", f"{_na}종",
                           color="primary",
                           sub=f"{_rs.get('n_features', 0)}차원 feature"))
        except ImportError:
            pass  # sklearn 미설치 — Tier 1.5 비활성
        except Exception as _e:
            md(empty_state(f"RL 통계 로드 실패: {_e}"))

        # 에이전트별 현황
        if g_source:
            section("에이전트별 오류 현황 (7일)")
            src_rows = []
            src_color_map = {
                "writer": "success", "radar": "primary", "infra": "warn",
                "master": "primary", "scheduler": "muted", "vision": "muted",
                "image": "muted", "daemon": "danger", "publish": "success",
            }
            for s in g_source:
                src = s["source"] or "—"
                c   = src_color_map.get(src, "muted")
                src_rows.append([
                    badge(src, c),
                    str(s["total"]),
                    badge(str(s["crit"]), "danger")  if s["crit"] else "—",
                    badge(str(s["new"]),  "warn")     if s["new"]  else "—",
                    badge(str(s["fixed"]),"success")  if s["fixed"] else "—",
                ])
            md(table(["에이전트", "총 오류", "CRITICAL", "신규", "자동수정"], src_rows))
        else:
            md(empty_state("에이전트별 데이터 없음"))

    # ── [신규] ─────────────────────────────────────────────────────
    with et2:
        new_errors = load_guardian_errors(status="new", days=30, limit=100)
        if new_errors:
            section(f"신규 오류 — {len(new_errors)}건 (최근 30일)")
            sev_color = {"critical": "danger", "high": "warn",
                         "medium": "primary", "low": "muted"}
            rows = []
            for r in new_errors:
                sev = r.get("severity", "medium")
                rows.append([
                    f'<span style="font-size:14px">{r["id"]}</span>',
                    badge(sev, sev_color.get(sev, "muted")),
                    f'<span style="font-size:14px;color:{N["text"]}">{esc((r.get("error_type") or "")[:35])}</span>',
                    badge(r.get("source","—"), "muted"),
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("module") or "")[:28])}</span>',
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("message") or "")[:70])}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{(r.get("timestamp") or "")[:16]}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{r.get("seen_count",1)}회</span>',
                ])
            md(table(["#", "심각도", "타입", "에이전트", "모듈", "메시지", "발생 시각", "횟수"],
                     rows, max_rows=50))

            # 최신 CRITICAL traceback 표시
            crits = [r for r in new_errors if r.get("severity") == "critical" and r.get("traceback")]
            if crits:
                section("CRITICAL 상세 (최신 3건)", color="danger")
                for r in crits[:3]:
                    with st.expander(f"#{r['id']} {r.get('error_type','')} — {(r.get('message',''))[:80]}"):
                        st.code(r.get("traceback", ""), language="python")
                        if r.get("context"):
                            st.caption(f"Context: {r['context'][:300]}")
        else:
            md(empty_state("신규 오류 없음 🎉", "현재 미처리 오류가 없습니다"))

    # ── [자동수정 완료] ───────────────────────────────────────────
    with et3:
        fixed_errors = load_guardian_errors(status="fixed", days=30, limit=100)
        if fixed_errors:
            section(f"자동수정 완료 — {len(fixed_errors)}건 (최근 30일)")
            sev_color = {"critical": "danger", "high": "warn",
                         "medium": "primary", "low": "muted"}
            rows = []
            for r in fixed_errors:
                sev = r.get("severity", "medium")
                rows.append([
                    f'<span style="font-size:14px">{r["id"]}</span>',
                    badge(sev, sev_color.get(sev, "muted")),
                    f'<span style="font-size:14px;color:{N["text"]}">{esc((r.get("error_type") or "")[:35])}</span>',
                    badge(r.get("source","—"), "muted"),
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("module") or "")[:28])}</span>',
                    f'<span style="font-size:14px;color:{C["success"]}">'
                    f'{esc((r.get("fixed_file") or "—")[:35])}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">'
                    f'{(r.get("fixed_at") or r.get("timestamp",""))[:16]}</span>',
                ])
            md(table(["#", "심각도", "타입", "에이전트", "모듈", "수정된 파일", "수정 시각"],
                     rows, max_rows=50))

            # 해결책 상세
            with_res = [r for r in fixed_errors if r.get("resolution")]
            if with_res:
                section("수정 상세 (최신 5건)")
                for r in with_res[:5]:
                    with st.expander(f"#{r['id']} {r.get('error_type','') or ''} → {(r.get('fixed_file') or '')[:40]}"):
                        st.markdown(f"**해결책:** {(r.get('resolution') or '')[:500]}")
                        if r.get("traceback"):
                            st.code(r.get("traceback",""), language="python")
        else:
            md(empty_state("자동수정 완료 오류 없음", "자동 수정된 오류가 아직 없습니다"))

    # ── [수동검토 필요] ───────────────────────────────────────────
    # 표시 조건: wontfix(자동수정 불가 전체) + new/analyzing 중 critical/high
    with et4:
        manual_errors = []
        # wontfix: 자동수정 포기 판정 → 수동 검토 대상 전체
        manual_errors.extend(load_guardian_errors(status="wontfix", severity=None, days=60, limit=200))
        # critical/high 미처리
        for s in ["new", "analyzing"]:
            for sev in ["critical", "high"]:
                chunk = load_guardian_errors(status=s, severity=sev, days=60, limit=50)
                manual_errors.extend(chunk)
        # 중복 제거 + 최신순
        seen_ids: set = set()
        unique_manual = []
        for r in sorted(manual_errors, key=lambda x: x["id"], reverse=True):
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"]); unique_manual.append(r)

        if unique_manual:
            _wontfix_cnt = sum(1 for r in unique_manual if r.get("status") == "wontfix")
            _crithigh_cnt = len(unique_manual) - _wontfix_cnt
            section(f"수동 검토 필요 — {len(unique_manual)}건 (자동수정 불가 {_wontfix_cnt} · CRITICAL/HIGH {_crithigh_cnt})")
            st.markdown(
                f'<div style="background:{_alpha(C["warn"],.08)};border:1px solid {_alpha(C["warn"],.3)};'
                f'border-radius:8px;padding:12px 16px;margin-bottom:16px;font-size:14px;color:{C["warn"]}">'
                f'⚠️ 자동수정 포기(wontfix) 판정 오류와 CRITICAL/HIGH 미처리 오류입니다. '
                f'수동으로 확인 후 수정하거나 무시(ignored) 처리하세요.</div>',
                unsafe_allow_html=True,
            )
            sev_color = {"critical": "danger", "high": "warn",
                         "medium": "primary", "low": "muted"}
            st_color  = {"new": "warn", "analyzing": "primary",
                         "wontfix": "danger", "fixed": "success", "ignored": "muted"}
            rows = []
            for r in unique_manual[:60]:
                sev = r.get("severity", "medium")
                st_ = r.get("status", "new")
                rows.append([
                    f'<span style="font-size:14px">{r["id"]}</span>',
                    badge(sev, sev_color.get(sev, "muted")),
                    badge(st_, st_color.get(st_, "muted")),
                    f'<span style="font-size:14px;color:{N["text"]}">{esc((r.get("error_type") or "")[:35])}</span>',
                    badge(r.get("source","—"), "muted"),
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("module") or "")[:28])}</span>',
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("message") or "")[:65])}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{(r.get("timestamp") or "")[:16]}</span>',
                ])
            md(table(["#", "심각도", "상태", "타입", "에이전트", "모듈", "메시지", "시각"],
                     rows, max_rows=60))

            # traceback 전개
            section("스택 트레이스 (최신 5건)")
            for r in unique_manual[:5]:
                if r.get("traceback"):
                    with st.expander(f"#{r['id']} [{r.get('severity','').upper()}] {r.get('error_type','')} — {(r.get('message',''))[:60]}"):
                        st.code(r.get("traceback",""), language="python")
                        if r.get("context"):
                            st.caption(f"Context: {r['context'][:300]}")
        else:
            md(empty_state("수동 검토 필요 오류 없음 🎉", "CRITICAL/HIGH 미처리 오류가 없습니다"))

    # ── [전체 이력] ───────────────────────────────────────────────
    with et5:
        section("전체 오류 이력 (최근 30일)")
        _f_col1, _f_col2, _f_col3 = st.columns(3)
        with _f_col1:
            f_sev = st.selectbox("심각도 필터", ["전체", "critical", "high", "medium", "low"],
                                 key="_err_f_sev")
        with _f_col2:
            f_st  = st.selectbox("상태 필터",
                                 ["전체", "new", "analyzing", "fixed", "wontfix", "ignored"],
                                 key="_err_f_st")
        with _f_col3:
            f_days = st.selectbox("기간", [7, 14, 30, 60, 90], index=2, key="_err_f_days")

        all_errors = load_guardian_errors(
            status   = None if f_st  == "전체" else f_st,
            severity = None if f_sev == "전체" else f_sev,
            days     = f_days,
            limit    = 200,
        )

        if all_errors:
            sev_color = {"critical": "danger", "high": "warn",
                         "medium": "primary", "low": "muted"}
            st_color  = {"new": "warn", "analyzing": "primary",
                         "fixed": "success", "wontfix": "danger", "ignored": "muted"}
            rows = []
            for r in all_errors:
                sev = r.get("severity", "medium")
                st_ = r.get("status", "new")
                rows.append([
                    f'<span style="font-size:14px">{r["id"]}</span>',
                    badge(sev, sev_color.get(sev, "muted")),
                    badge(st_, st_color.get(st_, "muted")),
                    f'<span style="font-size:14px;color:{N["text"]}">{esc((r.get("error_type") or "")[:30])}</span>',
                    badge(r.get("source","—"), "muted"),
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("module") or "")[:25])}</span>',
                    f'<span style="font-size:14px;color:{N["text2"]}">{esc((r.get("message") or "")[:60])}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{(r.get("timestamp") or "")[:16]}</span>',
                    f'<span style="font-size:14px;color:{N["text5"]}">{r.get("seen_count",1)}</span>',
                ])
            st.caption(f"총 {len(all_errors)}건 표시 (최대 200건)")
            md(table(["#", "심각도", "상태", "타입", "에이전트", "모듈", "메시지", "시각", "횟수"],
                     rows, max_rows=100))
        else:
            md(empty_state("해당 조건의 오류 없음", "필터를 조정해 보세요"))

# ──────────────────────────────────────────────────────────────────
# 스케줄러
# ──────────────────────────────────────────────────────────────────
with t_sched:
    dj         = load_default_jobs()
    last_runs  = load_job_last_runs()
    failed     = load_failed_jobs(days=7)
    today_jobs = load_job_runs(days=1, limit=50)

    section("스케줄러 현황")
    sc0, sc1, sc2, sc3 = st.columns(4)
    job_ok = sum(1 for j in today_jobs if j.get("success"))
    rate   = int(job_ok / len(today_jobs) * 100) if today_jobs else 100
    with sc0: md(kpi("등록 잡",     len(dj),          color="primary", sub="DEFAULT_JOBS"))
    with sc1: md(kpi("오늘 실행",   len(today_jobs),   color="success", sub=f"성공 {job_ok}건"))
    with sc2: md(kpi("성공률",      f"{rate}%",        color="success" if rate >= 90 else ("warn" if rate >= 70 else "danger")))
    with sc3: md(kpi("실패 (7일)", len(failed),        color="danger" if failed else "muted"))

    # 다음 실행 시각 — APScheduler에서 가져오기
    _next_run_map: dict = {}
    try:
        from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
        _aps = get_apscheduler()
        if _aps:
            for _j in _aps.get_jobs():
                nrt = getattr(_j, "next_run_time", None)
                if nrt:
                    _next_run_map[_j.id] = nrt.strftime("%m/%d %H:%M")
    except Exception:
        pass

    # 잡 카탈로그
    section("잡 카탈로그 (전체)")
    if dj:
        job_rows = []
        for j in dj:
            jid   = j.get("id", "—")
            last  = last_runs.get(jid, {})
            lr    = _fmt(last.get("last_run", ""))
            ok    = last.get("success")
            status_html = ok_badge(ok) if last else f'<span style="color:{N["text5"]};font-size:14px">미실행</span>'
            owner = j.get("owner", "—")
            exec_tag = badge("프로세스풀", "warn") if j.get("executor") == "processpool" else ""
            next_r = _next_run_map.get(jid, "")
            next_html = (f'<span style="font-size:14px;color:{C["success"]}">{esc(next_r)}</span>'
                         if next_r else f'<span style="color:{N["text5"]};font-size:14px">—</span>')
            job_rows.append([
                esc(j.get("name", jid)),
                badge(OWNER_LABEL.get(owner, owner), "muted"),
                esc(j.get("trigger", "—")),
                lr, next_html, status_html, exec_tag,
            ])
        md(table(["잡 이름", "소유", "트리거", "마지막 실행", "다음 실행", "결과", "실행 환경"], job_rows, max_rows=40))

    # 에이전트별 잡 분포
    section("에이전트별 잡 분포")
    owner_cnt: dict = {}
    for j in dj:
        o = j.get("owner", "unknown")
        owner_cnt[o] = owner_cnt.get(o, 0) + 1
    md(table(
        ["에이전트", "agent_id", "잡 수"],
        [[badge(OWNER_LABEL.get(o, o), "primary"), esc(o), str(n)]
         for o, n in sorted(owner_cnt.items(), key=lambda x: -x[1])]
    ))

    # 실패 잡 목록
    if failed:
        section("실패 잡 (최근 7일)", "danger")
        md(table(
            ["잡 이름", "소유", "발생 시각", "오류"],
            [[esc(j.get("job_name", "—")),
              badge(OWNER_LABEL.get(j.get("owner_agent", ""), j.get("owner_agent", "?")), "muted"),
              _fmt(j.get("started_at", "")),
              f'<span style="font-size:14px;color:{C["danger"]}">{esc(str(j.get("error",""))[:80])}</span>']
             for j in failed]
        ))

# ──────────────────────────────────────────────────────────────────
# 시스템
# ──────────────────────────────────────────────────────────────────
with t_sys:
    daemon     = load_daemon()
    caps       = load_capabilities()
    tool_stats = load_tool_stats()

    section("J00 인프라 — 데몬 상태")
    d0, d1, d2, d3 = st.columns(4)
    with d0: md(kpi("데몬",     "가동 중" if daemon["alive"] else "정지",
                    color="success" if daemon["alive"] else "danger",
                    sub=f"PID {daemon['pid'] or '—'}"))
    with d1: md(kpi("가동시간", daemon["uptime"], color="primary"))
    with d2:
        db_mb = DB_PATH.stat().st_size / 1024 / 1024 if DB_PATH.exists() else 0
        md(kpi("DB 용량", f"{db_mb:.1f} MB", color="muted", sub="jarvis.sqlite"))
    with d3:
        # hub.py 자신의 포트 상태 확인
        try:
            lsof = subprocess.run(["lsof", "-ti", "TCP:9199"], capture_output=True, text=True, timeout=3)
            hub_alive = lsof.returncode == 0 and bool(lsof.stdout.strip())
        except: hub_alive = True  # 실행 중이므로 True
        md(kpi("대시보드 9199", "가동 중", color="success", sub="JARVIS Hub (이 화면)"))

    # 인프라 잡 최근 실행
    infra_jobs = load_job_runs(owner="jarvis00_infra", days=7, limit=8)
    if infra_jobs:
        section("인프라 잡 최근 실행")
        md(table(
            ["잡 이름", "시작", "결과", "소요(ms)"],
            [[esc(j.get("job_name", "—")), _fmt(j.get("started_at", "")),
              ok_badge(j.get("success")), str(int(j.get("duration_ms") or 0))]
             for j in infra_jobs]
        ))

    section("J01 마스터 — 라우터 & 에이전트")
    m0, m1, m2 = st.columns(3)
    n_intents  = sum(len(c.get("intents", [])) for c in caps)
    tool_calls = sum(t.get("calls", 0) for t in tool_stats)
    tool_ok_s  = sum(t.get("ok", 0) for t in tool_stats)
    with m0: md(kpi("등록 에이전트", len(caps),       color="primary"))
    with m1: md(kpi("총 인텐트",     n_intents,        color="success"))
    with m2: md(kpi("도구 호출 (24h)", tool_calls,    color="warn", sub=f"성공 {tool_ok_s}건"))

    if caps:
        section("등록된 에이전트 & 인텐트")
        cap_rows = []
        for c in caps:
            intents = c.get("intents", [])
            preview = ", ".join(intents[:5])
            if len(intents) > 5: preview += f" +{len(intents)-5}개"
            cap_rows.append([
                badge(c.get("agent_id", "—"), "primary"),
                str(len(intents)),
                f'<span style="font-size:14px;color:{N["text2"]}">{esc(preview)}</span>',
            ])
        md(table(["에이전트 ID", "인텐트 수", "인텐트 목록"], cap_rows))

    if tool_stats:
        section("도구 실행 통계 (최근 24시간)")
        md(table(
            ["도구명", "도메인", "호출 수", "성공률", "평균(ms)"],
            [[esc(t.get("tool_name", "—")), esc(t.get("domain", "—")),
              str(t.get("calls", 0)),
              f'{int(t["ok"] / t["calls"] * 100) if t.get("calls") else 0}%',
              f'{int(t.get("avg_ms") or 0)}']
             for t in tool_stats]
        ))

    # 마스터 잡
    master_jobs = load_job_runs(owner="jarvis01_master", days=3, limit=8)
    if master_jobs:
        section("마스터 잡 최근 실행")
        md(table(
            ["잡 이름", "시작", "결과", "소요(ms)"],
            [[esc(j.get("job_name", "—")), _fmt(j.get("started_at", "")),
              ok_badge(j.get("success")), str(int(j.get("duration_ms") or 0))]
             for j in master_jobs]
        ))

    # VISION 에이전트 상태 (JARVIS05 → 8505 API)
    section("J05 VISION — 에이전트 실시간 상태")
    v_agents  = load_vision_agents()
    v_summary = load_vision_summary()
    if v_summary:
        va0, va1, va2, va3 = st.columns(4)
        total   = v_summary.get("total", 0)
        online  = v_summary.get("online", 0)
        warn    = v_summary.get("warn", 0)
        offline = v_summary.get("offline", 0)
        hpct    = v_summary.get("health_pct", 0)
        with va0: md(kpi("전체 에이전트", total,   color="primary"))
        with va1: md(kpi("정상 (ONLINE)", online,  color="success"))
        with va2: md(kpi("경고 (WARN)",   warn,    color="warn"))
        with va3: md(kpi("건강도",        f"{hpct}%", color="success" if hpct >= 80 else "warn" if hpct >= 50 else "danger"))
    if v_agents:
        cols = st.columns(2)
        for i, a in enumerate(v_agents):
            name      = a.get("agent_name", a.get("agent_id", "—"))
            status    = a.get("status", "offline")
            domain    = a.get("agent_domain", "—")
            last_seen = (a.get("last_seen") or "—")[:16]
            metrics   = a.get("metrics", {})
            # 대표 메트릭 2개 추출
            mkeys = [k for k in metrics if not isinstance(metrics[k], dict)][:3]
            mline = "  |  ".join(f"{k}: {metrics[k]}" for k in mkeys)
            desc  = f"도메인: {domain}  |  수집: {last_seen}"
            if mline:
                desc += f"<br>{mline}"
            color = "success" if status == "online" else "warn" if status == "warn" else "danger"
            with cols[i % 2]:
                md(agent_card(name, status, desc, color=color))
    else:
        md(empty_state("VISION API 연결 불가", "데몬이 실행 중이면 30초 후 자동 연결됩니다"))

    # ── J06 IMAGE — 이미지 생성 현황 ─────────────────────────────────
    section("J06 IMAGE — 이미지 생성 현황")
    img_s  = load_image_stats()
    prov   = img_s["providers"]
    prov_ok = sum(1 for v in prov.values() if v)

    ig0, ig1, ig2, ig3, ig4 = st.columns(5)
    with ig0: md(kpi("생성 이미지 총계",  img_s["total"],                  color="primary",  sub="output/ 디렉토리"))
    with ig1: md(kpi("PNG 파일",          img_s["by_type"].get("png", 0),   color="success"))
    with ig2: md(kpi("SVG 파일",          img_s["by_type"].get("svg", 0),   color="primary"))
    with ig3: md(kpi("총 용량",           f'{img_s["total_size_mb"]} MB',   color="muted",    sub="output/ 합계"))
    with ig4: md(kpi("가용 프로바이더",   f"{prov_ok}/1",
                     color="success" if prov_ok >= 1 else "warn",
                     sub="Pollinations.ai (★ Bing/HF 폐기 — ERRORS [263])"))

    # 프로바이더 상태 카드 — Pollinations 단독
    md(agent_card(
        "Pollinations.ai (단일 프로바이더)",
        "online",
        "무료 사진 생성 — 항상 가용<br>키 불필요 · 해상도 제한 있음<br>"
        "(★ 2026-06-07 — Bing/HuggingFace 폐기. 쿠키 만료·DNS 차단 등으로 전멸)",
        color="success",
    ))

    # 최근 생성 이미지 목록
    if img_s["recent"]:
        section("최근 생성 이미지 (최신 10개)")
        img_type_color = {"png": "success", "svg": "primary", "jpg": "warn",
                          "jpeg": "warn", "webp": "muted"}
        md(table(
            ["파일명", "유형", "생성 시각", "크기"],
            [[f'<span style="font-size:14px;color:{N["text"]}">{esc(r["name"][:55])}'
              f'{"…" if len(r["name"]) > 55 else ""}</span>',
              badge(r["type"], img_type_color.get(r["type"], "muted")),
              r["mtime"],
              f'<span style="font-size:14px;color:{N["text2"]}">{r["size_kb"]} KB</span>']
             for r in img_s["recent"]],
        ))
    else:
        md(empty_state(
            "아직 생성된 이미지가 없습니다",
            "블로그 글 발행 시 JARVIS06_IMAGE 가 자동으로 이미지를 생성합니다",
        ))

    # ── J07 GUARDIAN — 요약 (상세는 '오류 관리' 탭 참조) ─────────────────
    section("J07 GUARDIAN — 오류 수집·수정 요약")
    _gd_sys = load_guardian_stats()
    _gs0, _gs1, _gs2, _gs3 = st.columns(4)
    with _gs0: md(kpi("총 오류 (7일)",   _gd_sys["total"],    color="primary"))
    with _gs1: md(kpi("신규",            _gd_sys["new"],
                       color="danger" if _gd_sys["new"] > 0 else "success"))
    with _gs2: md(kpi("자동수정 완료",   _gd_sys["fixed"],    color="success"))
    with _gs3: md(kpi("CRITICAL",        _gd_sys["critical"] + _gd_sys["high"],
                       color="danger" if (_gd_sys["critical"] + _gd_sys["high"]) > 0 else "muted"))
    md(f'<div style="font-size:14px;color:{N["text2"]};margin-top:8px">'
       f'📋 상세 오류 목록·추이·에이전트별 현황은 <b>오류 관리</b> 탭에서 확인하세요.</div>')

    # ── J08 PUBLISH — 발행 도메인 자격증명·플랫폼 현황 ──────────────────
    section("J08 PUBLISH — 발행 도메인 현황")
    _p8 = load_publish_stats()
    _p8_nv  = _p8.get("naver_cookie_ok", False)
    _p8_ts  = _p8.get("ts_cookie_ok", False)
    _p8_age = _p8.get("naver_cookie_age")
    _p8_age_txt = f"{_p8_age}시간 전" if _p8_age is not None else "파일 없음"
    _p8_7d  = _p8.get("plat_7d", {})
    p8c0, p8c1, p8c2 = st.columns(3)
    with p8c0: md(kpi("네이버 쿠키", "갱신됨" if _p8_nv else "없음",
                       color="success" if _p8_nv else "danger",
                       sub=_p8_age_txt))
    with p8c1: md(kpi("티스토리 TS_COOKIE", "설정됨" if _p8_ts else "미설정",
                       color="success" if _p8_ts else "danger"))
    with p8c2: md(kpi("7일 총 발행", sum(_p8_7d.values()), color="primary",
                       sub="전 플랫폼 합계"))
    # 플랫폼별 카드
    _p8_pr0, _p8_pr1 = st.columns(2)
    with _p8_pr0:
        md(agent_card(
            "네이버 블로그",
            "online" if _p8_nv else "warn",
            f"7일 발행 {_p8_7d.get('naver', 0)}건<br>Selenium 발행자<br>쿠키: {_p8_age_txt}",
            color="success" if _p8_nv else "warn",
        ))
    with _p8_pr1:
        md(agent_card(
            "티스토리",
            "online" if _p8_ts else "warn",
            f"7일 발행 {_p8_7d.get('tistory', 0)}건<br>Selenium 발행자<br>TS_COOKIE 환경변수",
            color="warn",
        ))

    # ── J09 COLLECTOR — 수집 도메인 현황 ─────────────────────────────
    section("J09 COLLECTOR — 수집 도메인 현황")
    try:
        from shared import db as _j9db
        _j9s = _j9db.get_collection_stats()
        _j9c0, _j9c1 = st.columns(2)
        with _j9c0: md(kpi("누적 수집 레코드", _j9s["total"], color="primary", sub="7일 캐시 유지"))
        with _j9c1: md(kpi("오늘 수집", _j9s["today"], color="success", sub="blog·news·academic·finance·web"))
        md(agent_card(
            "JARVIS09 COLLECTOR",
            "online",
            "THEME_QUEUED 구독 → 5종 프로바이더 병렬 수집<br>robots.txt 준수 · 공식 API 우선<br>정제 원본 → COLLECTION_READY 발행",
            color="primary",
        ))
    except Exception as _j9e:
        md(empty_state(f"COLLECTOR 현황 조회 실패: {_j9e}"))

    # 최근 이벤트
    section("최근 이벤트")
    events = load_recent_events(15)
    if events:
        md(table(
            ["이벤트 타입", "소스", "시각"],
            [[esc(e["event_type"]), esc(e["source"]), _fmt(e["created_at"])]
             for e in events]
        ))
    else:
        md(empty_state("이벤트 없음"))
