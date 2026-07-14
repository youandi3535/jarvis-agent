"""실시간 파이프라인 활동 트래커 (파일 기반 — 크로스 프로세스 공유).

파이프라인 각 단계에서 mark_active(edge_id) 를 호출하면
~/.jarvis/pipeline_activity.json 에 기록되고 API 서버가 이 파일을 읽는다.

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

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

_LOCK = threading.Lock()

# 공유 파일 — 데몬·API서버 모두 이 파일을 읽고 씀
_DATA_FILE = Path(os.environ.get(
    "JARVIS_PIPELINE_ACTIVITY",
    str(Path.home() / ".jarvis" / "pipeline_activity.json"),
))

_LOG_MAX = 60
DEFAULT_TTL = 40  # 기본 40초

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


# ── 내부 파일 I/O ────────────────────────────────────────────────
def _read() -> dict:
    try:
        return json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"active": {}, "log": []}


def _write(data: dict) -> None:
    _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(_DATA_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, str(_DATA_FILE))  # atomic


# ── 공개 API ─────────────────────────────────────────────────────
def mark_active(edge_id: "str | list[str]", ttl: int = DEFAULT_TTL) -> None:
    """엣지를 TTL 초 동안 active 로 표시. 신규 활성 시 현황 로그 자동 기록."""
    ids = [edge_id] if isinstance(edge_id, str) else list(edge_id)
    expires = time.time() + ttl
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOCK:
        data = _read()
        active: dict = data.get("active", {})
        log: list = data.get("log", [])
        prev_keys = set(active.keys())
        for eid in ids:
            active[eid] = expires
            if eid not in prev_keys:
                msg = _EDGE_LOG_MSGS.get(eid, f"{eid} 활성화")
                log.insert(0, {"ts": ts, "msg": msg})
        _write({"active": active, "log": log[:_LOG_MAX]})


def get_active() -> list[str]:
    """현재 active 인 엣지 ID 목록 반환 (만료 항목 자동 정리)."""
    now = time.time()
    with _LOCK:
        data = _read()
        active: dict = data.get("active", {})
        stale = [k for k, v in active.items() if v < now]
        if stale:
            for k in stale:
                del active[k]
            _write({"active": active, "log": data.get("log", [])})
        return list(active.keys())


def log_activity(msg: str) -> None:
    """파이프라인 현황 메시지를 수동으로 로그에 추가."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOCK:
        data = _read()
        log: list = data.get("log", [])
        log.insert(0, {"ts": ts, "msg": msg})
        _write({"active": data.get("active", {}), "log": log[:_LOG_MAX]})


def get_activity_log() -> list[dict]:
    """현황 로그 반환 (최신 먼저, 최대 60개)."""
    with _LOCK:
        return _read().get("log", [])
