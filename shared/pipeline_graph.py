"""JARVIS 파이프라인 연결 그래프 — 단일 진실 소스 (사용자 박제 2026-07-11).

대시보드 dashboard/app/page.tsx 가 /api/graph 로 이 데이터를 수신해
SVG 경로를 자동 계산한다. 파이프라인 연결이 바뀌면 이 파일만 수정하면
대시보드가 자동으로 반영된다.

route 값:
  (없음)      — 자동 판별: 같은 행→수평, 같은 열→수직
  "via_lane"  — 중간 레인(lane_y)을 경유하는 우회 (M fx,fy V lane_y H tx V ty)

dx : 수직 연결선 x 오프셋 — 같은 열 왕복 선의 겹침 방지
"""
from __future__ import annotations

PIPELINE_EDGES: list[dict] = [
    # ══ Row1 수평 주 파이프라인 ══════════════════════════════════
    {"id": "e1",  "from": "j03", "to": "j09", "label": "선수집",     "col": "#fbbf24", "dur": 2.0, "dots": 2, "wt": 2.0},
    {"id": "e2",  "from": "j09", "to": "j02", "label": "데이터",     "col": "#38bdf8", "dur": 2.2, "dots": 2, "wt": 2.0},
    {"id": "e3",  "from": "j02", "to": "j06", "label": "대본",       "col": "#e879f9", "dur": 1.5, "dots": 2, "wt": 2.0},
    {"id": "e6",  "from": "j06", "to": "j08", "label": "발행",       "col": "#22d3ee", "dur": 1.6, "dots": 2, "wt": 2.0},
    # ══ topic_pack: Row1 상단 레인 경유 (J03→J02 skip) ══════════
    {"id": "e5",  "from": "j03", "to": "j02", "label": "topic_pack", "col": "#fbbf24", "dur": 2.8, "dots": 1, "wt": 1.2,
     "route": "via_lane", "lane_y": 238},
    # ══ 수직: Row1↔Row2 ══════════════════════════════════════════
    {"id": "e7",  "from": "j02", "to": "j07", "label": None,         "col": "#f43f5e", "dur": 3.0, "dots": 1, "wt": 1.4, "dx": -3},
    {"id": "e8",  "from": "j07", "to": "j02", "label": "수정",       "col": "#f43f5e", "dur": 3.5, "dots": 1, "wt": 1.4, "dx": 3},
    {"id": "e9",  "from": "j09", "to": "j05", "label": None,         "col": "#38bdf8", "dur": 3.2, "dots": 1, "wt": 1.0},
    # ══ J05 헬스 리포트 ═══════════════════════════════════════════
    {"id": "e10", "from": "j05", "to": "j07", "label": "헬스",       "col": "#34d399", "dur": 5.5, "dots": 1, "wt": 1.2},
    # ══ Row0 인프라·라우팅 ════════════════════════════════════════
    {"id": "e11", "from": "j00", "to": "j01", "label": "인프라",     "col": "#4ade80", "dur": 5.0, "dots": 1, "wt": 1.0},
    {"id": "e12", "from": "j01", "to": "j02", "label": "라우팅",     "col": "#4f90d9", "dur": 2.5, "dots": 1, "wt": 1.0},
    # ══ J04 스케줄 트리거 (Row0 하단→Row1 상단 레인 경유) ════════
    {"id": "e13", "from": "j04", "to": "j03", "label": "트리거",     "col": "#fb923c", "dur": 4.5, "dots": 1, "wt": 1.0,
     "route": "via_lane", "lane_y": 234},
    {"id": "e14", "from": "j04", "to": "j02", "label": None,         "col": "#fb923c", "dur": 5.0, "dots": 1, "wt": 1.0,
     "route": "via_lane", "lane_y": 244},
]

# 범례 — 대시보드 우하단 (색상 그룹 단위)
LEGEND: list[dict] = [
    {"col": "#fbbf24", "label": "J03 topic_pack / 선수집"},
    {"col": "#38bdf8", "label": "J09 수집 데이터"},
    {"col": "#e879f9", "label": "J02→J06 대본"},
    {"col": "#22d3ee", "label": "J06→J08 발행"},
    {"col": "#f43f5e", "label": "J07 오류·수정"},
    {"col": "#34d399", "label": "J05 헬스 리포트"},
    {"col": "#fb923c", "label": "J04 잡 트리거"},
    {"col": "#4f90d9", "label": "J01 라우팅"},
    {"col": "#4ade80", "label": "J00 인프라"},
]
