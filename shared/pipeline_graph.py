"""JARVIS 파이프라인 연결 그래프 — 단일 진실 소스.

★ 새 에이전트·연결 추가 시 이 파일만 수정하면
  대시보드·현황 로그·잡 트리거 매핑이 자동 반영된다.

─────────────────────────────────────────────
AGENTS  : 에이전트 카드 정의 (ID·이름·색상·위치)
           x/y = 대시보드 픽셀 좌표
           big = True → 큰 카드 (BIG_W=210, BIG_H=215), 기본 158×170
PIPELINE_EDGES : 연결선 topology + 애니메이션 파라미터
LEGEND  : 대시보드 우하단 색상 범례 (PIPELINE_EDGES에서 자동 파생)
─────────────────────────────────────────────
route 값:
  (없음)      — 자동 판별: 같은 행→수평, 같은 열→수직
  "via_lane"  — 중간 레인(lane_y) 경유 우회 (M fx,fy V lane_y H tx V ty)

dx : 수직 연결선 x 오프셋 — 같은 열 왕복 선의 겹침 방지
"""
from __future__ import annotations

# ── 대시보드 레이아웃 상수 (page.tsx 와 동기화) ───────────────────
# 이 값만 바꾸면 대시보드 전체 레이아웃이 한 번에 바뀐다.
LAYOUT: dict = {
    "W": 1130, "H": 660,
    "CARD_W": 158, "CARD_H": 170,
    "BIG_W": 210,  "BIG_H": 215,
    "ROW0_Y": 16, "ROW1_Y": 252, "ROW2_Y": 462,
    "PIP_GAP": 76,   # Row1 카드 간격
}

def _x(col: int, *, row: int = 1) -> int:
    """열 인덱스 → 픽셀 x. row=0 특수 배치."""
    L = LAYOUT
    if row == 0:
        # 0=좌단, 1=중앙(BIG), 2=우단
        return [18, (L["W"] - L["BIG_W"]) // 2, L["W"] - 18 - L["CARD_W"]][col]
    return 18 + col * (L["CARD_W"] + L["PIP_GAP"])

# ── 에이전트 정의 — 이 목록이 대시보드 카드 렌더의 단일 진실 소스 ──
# 새 에이전트 추가: 이 목록에 dict 추가 → 대시보드 자동 반영
AGENTS: list[dict] = [
    # ── 상단 관리층 (Row 0) ──────────────────────────────────────
    # J04(좌) → J03(파이프라인 시작) 바로 위 = 트리거선 수직 정렬. J00(우).
    {"id":"j04","num":"04","label":"J04 SCHED",   "sub":"작업 스케줄러",   "color":"#fb923c",
     "x":_x(0,row=0), "y":LAYOUT["ROW0_Y"]},
    {"id":"j01","num":"01","label":"J01 MASTER",  "sub":"마스터 라우터",   "color":"#4f90d9",
     "x":_x(1,row=0), "y":LAYOUT["ROW0_Y"], "big":True},
    {"id":"j00","num":"00","label":"J00 INFRA",   "sub":"인프라 관리자",   "color":"#4ade80",
     "x":_x(2,row=0), "y":LAYOUT["ROW0_Y"]},
    # ── 파이프라인 (Row 1) ────────────────────────────────────────
    {"id":"j03","num":"03","label":"J03 RADAR",   "sub":"트렌드 레이더",   "color":"#fbbf24",
     "x":_x(0), "y":LAYOUT["ROW1_Y"]},
    {"id":"j09","num":"09","label":"J09 COLLECT", "sub":"데이터 수집기",   "color":"#38bdf8",
     "x":_x(1), "y":LAYOUT["ROW1_Y"]},
    {"id":"j02","num":"02","label":"J02 WRITER",  "sub":"블로그 라이터",   "color":"#a78bfa",
     "x":_x(2), "y":LAYOUT["ROW1_Y"]},
    {"id":"j06","num":"06","label":"J06 IMAGE",   "sub":"이미지 생성",     "color":"#e879f9",
     "x":_x(3), "y":LAYOUT["ROW1_Y"]},
    {"id":"j08","num":"08","label":"J08 PUBLISH", "sub":"발행 관리자",     "color":"#22d3ee",
     "x":_x(4), "y":LAYOUT["ROW1_Y"]},
    # ── 하단 감시층 (Row 2) ──────────────────────────────────────
    {"id":"j05","num":"05","label":"J05 VISION",  "sub":"메트릭 모니터링", "color":"#34d399",
     "x":_x(1), "y":LAYOUT["ROW2_Y"]},
    {"id":"j07","num":"07","label":"J07 GUARD",   "sub":"오류 수호자",     "color":"#f43f5e",
     "x":_x(2), "y":LAYOUT["ROW2_Y"]},
]

# ── 모든 연결은 양방향 2줄 (정방향=라벨+오프셋, 역방향=무라벨 반대 오프셋) ──
#    수평선: dy 오프셋 / 수직선: dx 오프셋 / 레인선: lane_y 차이 로 두 줄 분리.
PIPELINE_EDGES: list[dict] = [
    # ══ Row1 수평 주 파이프라인 — 양방향 ══════════════════════════
    {"id": "e1",  "from": "j03", "to": "j09", "label": "선수집",     "col": "#fbbf24", "dur": 2.0, "dots": 2, "wt": 2.0, "dy": -5},
    {"id": "e1r", "from": "j09", "to": "j03", "label": None,         "col": "#fbbf24", "dur": 2.4, "dots": 1, "wt": 1.2, "dy": 5},
    {"id": "e2",  "from": "j09", "to": "j02", "label": "데이터",     "col": "#38bdf8", "dur": 2.2, "dots": 2, "wt": 2.0, "dy": -5},
    {"id": "e2r", "from": "j02", "to": "j09", "label": None,         "col": "#38bdf8", "dur": 2.6, "dots": 1, "wt": 1.2, "dy": 5},
    {"id": "e3",  "from": "j02", "to": "j06", "label": "대본",       "col": "#e879f9", "dur": 1.5, "dots": 2, "wt": 2.0, "dy": -5},
    {"id": "e3r", "from": "j06", "to": "j02", "label": None,         "col": "#e879f9", "dur": 1.9, "dots": 1, "wt": 1.2, "dy": 5},
    {"id": "e6",  "from": "j06", "to": "j08", "label": "발행",       "col": "#22d3ee", "dur": 1.6, "dots": 2, "wt": 2.0, "dy": -5},
    {"id": "e6r", "from": "j08", "to": "j06", "label": None,         "col": "#22d3ee", "dur": 2.0, "dots": 1, "wt": 1.2, "dy": 5},
    # (topic_pack J03→J02 직결선 제거 — 사용자 박제 2026-07-19: J09 경유 경로로만 표시)
    # ══ 수직: Row1↔Row2 — 양방향 ═════════════════════════════════
    {"id": "e7",  "from": "j02", "to": "j07", "label": None,         "col": "#f43f5e", "dur": 3.0, "dots": 1, "wt": 1.4, "dx": -4},
    {"id": "e8",  "from": "j07", "to": "j02", "label": "수정",       "col": "#f43f5e", "dur": 3.5, "dots": 1, "wt": 1.4, "dx": 4},
    {"id": "e9",  "from": "j09", "to": "j05", "label": None,         "col": "#38bdf8", "dur": 3.2, "dots": 1, "wt": 1.0, "dx": -4},
    {"id": "e9r", "from": "j05", "to": "j09", "label": None,         "col": "#38bdf8", "dur": 3.6, "dots": 1, "wt": 1.0, "dx": 4},
    # ══ J05 헬스 리포트 — 양방향 ══════════════════════════════════
    {"id": "e10", "from": "j05", "to": "j07", "label": "헬스",       "col": "#34d399", "dur": 5.5, "dots": 1, "wt": 1.2, "dy": -5},
    {"id": "e10r","from": "j07", "to": "j05", "label": None,         "col": "#34d399", "dur": 5.9, "dots": 1, "wt": 1.0, "dy": 5},
    # ══ Row0 인프라·라우팅 — 양방향 ══════════════════════════════
    {"id": "e11", "from": "j00", "to": "j01", "label": "인프라",     "col": "#4ade80", "dur": 5.0, "dots": 1, "wt": 1.0, "dy": -5},
    {"id": "e11r","from": "j01", "to": "j00", "label": None,         "col": "#4ade80", "dur": 5.4, "dots": 1, "wt": 1.0, "dy": 5},
    {"id": "e12", "from": "j01", "to": "j02", "label": "라우팅",     "col": "#4f90d9", "dur": 2.5, "dots": 1, "wt": 1.0, "dx": -4},
    {"id": "e12r","from": "j02", "to": "j01", "label": None,         "col": "#4f90d9", "dur": 2.9, "dots": 1, "wt": 1.0, "dx": 4},
    # ══ J04 스케줄 트리거 (J03 바로 위 = 수직) — 양방향 ══════════
    {"id": "e13", "from": "j04", "to": "j03", "label": "트리거",     "col": "#fb923c", "dur": 4.5, "dots": 1, "wt": 1.0, "dx": -4},
    {"id": "e13r","from": "j03", "to": "j04", "label": None,         "col": "#fb923c", "dur": 4.9, "dots": 1, "wt": 1.0, "dx": 4},
    # ══ J04 ↔ J01 (스케줄러 ↔ 마스터, Row0 수평) — 양방향 ════════
    {"id": "e15", "from": "j04", "to": "j01", "label": "스케줄",     "col": "#fb923c", "dur": 4.2, "dots": 1, "wt": 1.0, "dy": -5},
    {"id": "e15r","from": "j01", "to": "j04", "label": None,         "col": "#fb923c", "dur": 4.6, "dots": 1, "wt": 1.0, "dy": 5},
]

# ── 범례 — AGENTS 색상에서 자동 파생 ────────────────────────────
# 새 에이전트 추가 시 범례도 자동 갱신됨 (하드코딩 금지)
def _build_legend() -> list[dict]:
    seen: dict[str, str] = {}
    for a in AGENTS:
        col = a["color"]
        if col not in seen:
            seen[col] = a["label"]
    return [{"col": col, "label": label} for col, label in seen.items()]

LEGEND: list[dict] = _build_legend()
