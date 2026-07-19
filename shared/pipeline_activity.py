"""실시간 파이프라인 활동 트래커 (파일 기반 — 크로스 프로세스 공유).

파이프라인 각 단계에서 mark_active(edge_id) 를 호출하면
~/.jarvis/pipeline_activity.json 에 기록되고 API 서버가 이 파일을 읽는다.

★ 쓰기 규칙 (근본 수정 2026-07-16 — busy 신호 드랍 사고):
  - 모든 writer 는 "전체 dict 읽기 → 필요한 키만 갱신 → 전체 dict 쓰기".
    부분 dict 재구성(_write({"active":..., "log":...}))은 다른 키(busy 등)를
    통째로 드랍시키므로 절대 금지. 미래에 키가 추가돼도 이 패턴이면 재발 불가.
  - 읽기 함수(get_active/get_busy_agents)는 파일에 쓰지 않는다 (읽기 전용).
    만료 항목은 메모리에서 필터해 반환만 하고, 물리적 삭제는 쓰기 시점
    (_purge_expired 헬퍼)에만 수행 — API 서버(별도 프로세스)가 writer 가 되어
    데몬과 read-modify-write 경합하는 사고 차단.
  - read-modify-write 구간은 fcntl.flock 파일 락으로 크로스 프로세스 직렬화
    (threading.Lock 은 프로세스 간 무력). 락 실패 시 락 없이 진행 — 가용성 우선.

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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

try:
    import fcntl  # darwin/linux — 크로스 프로세스 파일 락
except ImportError:  # 비 POSIX 환경 — 파일 락 없이 진행 (가용성 우선)
    fcntl = None

_LOCK = threading.Lock()

# 공유 파일 — 데몬·API서버 모두 이 파일을 읽고 씀
_DATA_FILE = Path(os.environ.get(
    "JARVIS_PIPELINE_ACTIVITY",
    str(Path.home() / ".jarvis" / "pipeline_activity.json"),
))

_LOG_MAX = 60
DEFAULT_TTL = 40  # 기본 40초

# ── 엣지 로그 메시지 — pipeline_graph.py 에서 자동 파생 (하드코딩 금지) ──
# pipeline_graph.py 에 엣지·에이전트를 추가하면 이 메시지도 자동 갱신됨.
def _build_edge_log_msgs() -> dict[str, str]:
    try:
        from shared.pipeline_graph import PIPELINE_EDGES, AGENTS
        _name = {a["id"]: a["label"] for a in AGENTS}
        msgs: dict[str, str] = {}
        for e in PIPELINE_EDGES:
            f = _name.get(e["from"], e["from"].upper())
            t = _name.get(e["to"],   e["to"].upper())
            lbl = (e.get("label") or "").strip()
            msgs[e["id"]] = f"{f} → {t}  {lbl}".rstrip() if lbl else f"{f} → {t}"
        return msgs
    except Exception:
        return {}

_EDGE_LOG_MSGS: dict[str, str] = _build_edge_log_msgs()


def _agent_name(aid: str) -> str:
    """에이전트 id → 표시 라벨 (예: 'j07' → 'J07 GUARD')."""
    try:
        from shared.pipeline_graph import AGENTS
        return {a["id"]: a["label"] for a in AGENTS}.get(aid, str(aid).upper())
    except Exception:
        return str(aid).upper()


def module_to_agent(module: str) -> str:
    """모듈 경로/소스 → 에이전트 id (예: 'JARVIS06_IMAGE/draft_processor.py' → 'j06').

    ★ 동적 flow (사용자 박제 2026-07-19): GUARDIAN 자동수정 등 *대상이 가변* 인 상호작용에서
    실제 대상 에이전트를 판별해 정확한 노드·선을 활성화하기 위한 매핑.
    """
    import re as _re
    m = _re.search(r'JARVIS(\d\d)', str(module or ""))
    return f"j{m.group(1)}" if m else ""


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


@contextmanager
def _file_lock():
    """fcntl.flock 기반 크로스 프로세스 파일 락 — read-modify-write 경합 방지.

    데몬·API서버 등 여러 프로세스가 같은 파일을 갱신할 수 있으므로
    threading.Lock(_LOCK) 만으로는 부족. _LOCK 안에서 이 락을 함께 잡는다.
    락 획득 실패(파일시스템 문제 등) 시 락 없이 진행 — 가용성 우선.
    """
    fd = None
    if fcntl is not None:
        try:
            _DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd = open(str(_DATA_FILE) + ".lock", "w")
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        except Exception:
            if fd is not None:
                try:
                    fd.close()
                except Exception:
                    pass
            fd = None
    try:
        yield
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fd.close()
            except Exception:
                pass


def _expires_of(v) -> float:
    """항목 만료 시각 추출 — active(float)·busy(dict / 숫자 레거시) 공용."""
    try:
        return float(v.get("expires", 0)) if isinstance(v, dict) else float(v)
    except Exception:
        return 0.0


def _purge_expired(data: dict, now: float | None = None) -> None:
    """만료된 active(엣지)·busy(에이전트) 항목을 dict 에서 제거.

    ★ 쓰기 함수(mark_active/mark_busy/clear_busy/log_activity) 안에서만 호출.
    읽기 함수는 파일에 쓰지 않으므로 물리적 삭제는 전부 여기(쓰기 시점)로 모은다.
    """
    now = time.time() if now is None else now
    for key in ("active", "busy", "flows"):   # ★ flows(동적 쌍) 만료 정리 포함
        items = data.get(key) or {}
        for k in [k for k, v in items.items() if _expires_of(v) < now]:
            del items[k]


# ── 공개 API ─────────────────────────────────────────────────────
def mark_active(edge_id: "str | list[str]", ttl: int = DEFAULT_TTL) -> None:
    """엣지를 TTL 초 동안 active 로 표시. 신규 활성 시 현황 로그 자동 기록."""
    ids = [edge_id] if isinstance(edge_id, str) else list(edge_id)
    expires = time.time() + ttl
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOCK, _file_lock():
        # ★ 전체 dict 보존형 쓰기 — active·log 외 키(busy 등) 절대 드랍 금지
        data = _read()
        _purge_expired(data)
        active: dict = data.setdefault("active", {})
        log: list = data.get("log") or []
        prev_keys = set(active.keys())
        for eid in ids:
            active[eid] = expires
            if eid not in prev_keys:
                msg = _EDGE_LOG_MSGS.get(eid, f"{eid} 활성화")
                log.insert(0, {"ts": ts, "msg": msg})
        data["log"] = log[:_LOG_MAX]
        _write(data)


def get_active() -> list[str]:
    """현재 active 인 엣지 ID 목록 반환 (읽기 전용 — 만료 항목은 메모리 필터만)."""
    now = time.time()
    with _LOCK:
        data = _read()
    active: dict = data.get("active") or {}
    return [k for k, v in active.items() if _expires_of(v) >= now]


def mark_flow(from_id: str, to_id: str, label: str = "", ttl: int = DEFAULT_TTL) -> None:
    """★ 동적 flow (사용자 박제 2026-07-19): *실제로 상호작용하는 에이전트 쌍*(from→to)과 라벨을
    정확히 활성화·로그. 고정 엣지(mark_active) 로는 표현 못 하는 가변 대상(예: GUARDIAN 이 J06 을
    고치면 J07→J06)을 추가 엣지 없이 노드 위치 사이 선을 동적으로 그려 표시한다.

    프론트는 get_active_flows() 로 활성 flow 를 받아 ① 두 노드(from·to)를 active 모션으로 ②
    그 사이 선을 전달 모션으로 그린다. 실시간 로그에는 정확한 'J{from} → J{to} {label}' 를 남긴다.
    """
    if not from_id or not to_id:
        return
    key = f"{from_id}>{to_id}"
    expires = time.time() + ttl
    ts = datetime.now().strftime("%H:%M:%S")
    lbl = (label or "").strip()
    fn, tn = _agent_name(from_id), _agent_name(to_id)
    msg = f"{fn} → {tn}  {lbl}".rstrip() if lbl else f"{fn} → {tn}"
    with _LOCK, _file_lock():
        # ★ 전체 dict 보존형 쓰기 — flows·log 외 키(active·busy 등) 절대 드랍 금지
        data = _read()
        _purge_expired(data)
        flows: dict = data.setdefault("flows", {})
        new = key not in flows
        flows[key] = {"from": from_id, "to": to_id, "label": lbl, "expires": expires}
        if new:
            log: list = data.get("log") or []
            log.insert(0, {"ts": ts, "msg": msg})
            data["log"] = log[:_LOG_MAX]
        _write(data)


def get_active_flows() -> list[dict]:
    """현재 active 인 동적 flow 목록 [{from,to,label}] 반환 (읽기 전용)."""
    now = time.time()
    with _LOCK:
        data = _read()
    flows: dict = data.get("flows") or {}
    return [{"from": v.get("from"), "to": v.get("to"), "label": v.get("label", "")}
            for v in flows.values() if _expires_of(v) >= now and v.get("from") and v.get("to")]


def log_activity(msg: str) -> None:
    """파이프라인 현황 메시지를 수동으로 로그에 추가."""
    ts = datetime.now().strftime("%H:%M:%S")
    with _LOCK, _file_lock():
        # ★ 전체 dict 보존형 쓰기 — log 만 갱신, 나머지 키 그대로 유지
        data = _read()
        _purge_expired(data)
        log: list = data.get("log") or []
        log.insert(0, {"ts": ts, "msg": msg})
        data["log"] = log[:_LOG_MAX]
        _write(data)


def get_activity_log() -> list[dict]:
    """현황 로그 반환 (최신 먼저, 최대 60개 — 읽기 전용)."""
    with _LOCK:
        return _read().get("log", [])


def mark_busy(agent_id: str, task: str = "", ttl: int = 120) -> None:
    """에이전트 작업 진행 표시 (TTL초 후 자동 해제 — TTL 은 안전망).

    대시보드 isBusy 애니메이션 전용 — mark_active(엣지 데이터전달)와 독립 신호.
    에이전트가 실제 작업(수집·작성·이미지·발행)을 시작할 때 호출하고,
    작업 종료(성공·실패) 시 clear_busy() 로 즉시 해제한다.
    """
    expires = time.time() + ttl
    with _LOCK, _file_lock():
        # ★ 전체 dict 보존형 쓰기 — busy 만 갱신, 나머지 키 그대로 유지
        data = _read()
        _purge_expired(data)
        busy: dict = data.setdefault("busy", {})
        busy[agent_id] = {"expires": expires, "task": task}
        _write(data)


def clear_busy(agent_id: str) -> None:
    """에이전트 busy 즉시 해제 — 작업 종료 시 평상시 복귀.

    해당 항목이 없어도 조용히 무시 (호출자는 finally 에서 무조건 불러도 안전).
    """
    with _LOCK, _file_lock():
        data = _read()
        _purge_expired(data)
        busy: dict = data.setdefault("busy", {})
        busy.pop(agent_id, None)
        _write(data)


def get_busy_agents() -> dict[str, str]:
    """현재 작업 중인 에이전트 {id: task} 반환 (읽기 전용 — 만료 항목은 메모리 필터만)."""
    now = time.time()
    with _LOCK:
        data = _read()
    busy: dict = data.get("busy") or {}
    return {k: (v.get("task", "") if isinstance(v, dict) else "")
            for k, v in busy.items() if _expires_of(v) >= now}
