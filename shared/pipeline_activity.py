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

_lock = threading.Lock()
_active: dict[str, float] = {}  # edge_id → expires_at (monotonic)

DEFAULT_TTL = 40  # 기본 40초 (장시간 스텝도 커버)


def mark_active(edge_id: "str | list[str]", ttl: int = DEFAULT_TTL) -> None:
    """엣지를 TTL 초 동안 active 상태로 표시. fire-and-forget."""
    ids = [edge_id] if isinstance(edge_id, str) else list(edge_id)
    expires = time.monotonic() + ttl
    with _lock:
        for eid in ids:
            _active[eid] = expires


def get_active() -> list[str]:
    """현재 active 인 엣지 ID 목록 반환 (만료 항목 자동 정리)."""
    now = time.monotonic()
    with _lock:
        stale = [k for k, v in _active.items() if v < now]
        for k in stale:
            del _active[k]
        return list(_active.keys())
