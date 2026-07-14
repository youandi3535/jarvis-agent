"""실시간 파이프라인 활동 트래커 (사용자 박제 2026-07-11).

파이프라인 각 단계에서 mark_active(edge_id) 를 호출하면 해당 엣지가
TTL 초 동안 'active' 상태로 유지된다. 대시보드가 /api/pipeline/activity 를
2초마다 폴링해서 활성 엣지를 시각적으로 강조한다.

엣지 ID → 파이프라인 단계 매핑:
  e1  J03→J09  선수집 요청
  e2  J09→J02  데이터 전달
  e3  J02→J06  대본 전달
  e5  J03→J02  topic_pack 전달
  e6  J06→J08  발행
  e7  J02→J07  오류 보고
  e8  J07→J02  코드 수정
  e9  J09→J05  수집 완료 (헬스)
  e10 J05→J07  헬스 리포트
  e11 J00→J01  인프라 상태
  e12 J01→J02  라우팅
  e13 J04→J03  스케줄 트리거
  e14 J04→J02  스케줄 트리거
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

_lock = threading.Lock()
_active: dict[str, float] = {}  # edge_id → expires_at (monotonic)

DEFAULT_TTL = 40  # 기본 40초 (장시간 스텝도 커버)

# ── 실시간 현황 로그 ──────────────────────────────────────
_LOG_MAX = 60
_activity_log: list[dict] = []   # [{ts, msg}] 최신이 앞
_prev_active: set[str] = set()   # 직전 활성 엣지 (신규 감지용)

_EDGE_LOG_MSGS: dict[str, str] = {
    "e1":  "J03 RADAR → J09 COLLECT  선수집 시작",
    "e2":  "J09 COLLECT → J02 WRITER  데이터 전달",
    "e3":  "J02 WRITER → J06 IMAGE  대본 전달",
    "e5":  "J03 RADAR → J02 WRITER  주제 패키지 전달",
    "e6":  "J06 IMAGE → J08 PUBLISH  발행 시작",
    "e7":  "J02 WRITER → J07 GUARD  오류 보고",
    "e8":  "J07 GUARD → J02 WRITER  코드 수정 완료",
    "e9":  "J09 COLLECT → J05 VISION  수집 완료",
    "e10": "J05 VISION → J07 GUARD  헬스 리포트",
    "e11": "J00 INFRA → J01 MASTER  인프라 상태 전달",
    "e12": "J01 MASTER → J02 WRITER  라우팅",
    "e13": "J04 SCHED → J03 RADAR  수집 트리거",
    "e14": "J04 SCHED → J02 WRITER  발행 트리거",
}


def log_activity(msg: str) -> None:
    """파이프라인 현황 메시지를 수동으로 로그에 추가."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        _activity_log.insert(0, {"ts": ts, "msg": msg})
        if len(_activity_log) > _LOG_MAX:
            _activity_log.pop()


def get_activity_log() -> list[dict]:
    """현황 로그 반환 (최신 먼저)."""
    with _lock:
        return list(_activity_log)


def mark_active(edge_id: "str | list[str]", ttl: int = DEFAULT_TTL) -> None:
    """엣지를 TTL 초 동안 active 상태로 표시. 신규 활성 시 로그 자동 기록."""
    global _prev_active
    ids = [edge_id] if isinstance(edge_id, str) else list(edge_id)
    expires = time.monotonic() + ttl
    ts = datetime.now().strftime("%H:%M:%S")
    with _lock:
        for eid in ids:
            _active[eid] = expires
        # 새로 활성화된 엣지만 로그
        new_ids = [eid for eid in ids if eid not in _prev_active]
        for eid in new_ids:
            msg = _EDGE_LOG_MSGS.get(eid, f"{eid} 활성화")
            _activity_log.insert(0, {"ts": ts, "msg": msg})
        if len(_activity_log) > _LOG_MAX:
            _activity_log[:] = _activity_log[:_LOG_MAX]
        _prev_active = set(_active.keys())


def get_active() -> list[str]:
    """현재 active 인 엣지 ID 목록 반환 (만료 항목 자동 정리)."""
    global _prev_active
    now = time.monotonic()
    with _lock:
        stale = [k for k, v in _active.items() if v < now]
        for k in stale:
            del _active[k]
        _prev_active = set(_active.keys())
        return list(_active.keys())
