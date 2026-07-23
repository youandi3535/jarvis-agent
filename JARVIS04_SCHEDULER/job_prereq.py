"""JARVIS04 — 잡 선행조건 게이트 (★ 사용자 박제 2026-07-23).

**왜 필요한가 (실제 사고 2026-07-23)**
맥이 19:16→20:44 잠들었다 깨자 APScheduler 가 두 잡의 운명을 갈랐다.
  · `j02_theme_precollect`(20:00, 유예 1200s) — 44분 지각 → *폐기*
  · `j01_theme_post_21`  (21:00, 유예 3600s) —  6분 지각 → *실행*
선행 잡의 유예가 후행보다 짧아, 늦게 깨면 **선행만 골라 버려지고 후행은 살아남는다**.
그 결과 테마가 random 재선정되고 수집이 발행창 안에서 일어나 `[planner] 발행창 —
LLM 설계 스킵, 결정론 스캐폴드` 로 열화됐다. 경제(06:00→07:00)도 같은 비대칭.

**원인의 본질** — 의존관계가 *주석* 과 *"20시 다음이 21시"라는 시간 우연* 으로만 존재했다.
코드가 "선행이 됐는가" 를 한 번도 묻지 않았다.

**설계 (3원칙)**
① 단일 진입점 — 선언은 `job_registry.DEFAULT_JOBS` 의 `requires` 필드 한 곳,
   집행은 `register_default_jobs` 가 씌우는 `gate()` 래퍼 한 곳. 각 콜백(JARVIS02)은
   손대지 않는다. 새 의존관계는 `requires` 한 줄 추가로 끝.
② 동적 설계 — "1시간" 을 박지 않는다. 회복 갭은 *선행·후행 cron 시각의 차이* 로 파생하고
   (20:00→21:00=1h, 06:00→07:00=1h), 재시도 상한은 `harness.DEFAULT_MAX_ATTEMPTS` 에서,
   "선행이 됐는가" 는 파일 존재가 아니라 `job_runs` 오늘자 성공 기록의 *런타임 조회* 로 판정.
   선행 시각을 19:30 으로 옮기면 회복 갭도 자동으로 90분이 된다.
③ 모든 조합 — 경제(radar_trends_06→j01_economic_post)·테마(j02_theme_precollect→
   j01_theme_post_21) 둘 다. 두 콜백이 각각 네이버·티스토리를 직렬 수행하므로 4조합 전부.

**동작 (사용자 지정 2026-07-23)**
발행 시각에 선행이 미충족이면 → *발행하지 않고* ① 선행을 지금 즉시 실행 ② 자신을
`지금 + 회복갭` 으로 1회 재예약. 21:30 에 걸리면 21:30 선행 → 22:30 발행.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

__all__ = [
    "requirements", "recovery_gap_sec", "last_success_at", "readiness",
    "gate", "effective_grace", "deadline_sec", "DEFERRED_SUFFIX",
]

DEFERRED_SUFFIX = "__deferred"

# 연기 상한 — 하드코딩 금지. 시스템 전역 재시도 상한과 같은 상수에서 파생한다.
try:
    from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS as _MAX_ATTEMPTS
except Exception:                                    # pragma: no cover
    _MAX_ATTEMPTS = 2


def _log(msg: str) -> None:
    print(f"  [prereq] {msg}")


def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass


def _job(job_id: str) -> Optional[dict]:
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    base = job_id.split(DEFERRED_SUFFIX)[0]
    for j in DEFAULT_JOBS:
        if j["id"] == base:
            return j
    return None


# ── ① 무엇이 선행인가 — 선언은 DEFAULT_JOBS 한 곳 ──────────────────────
def requirements(job_id: str) -> list[str]:
    """이 잡이 요구하는 선행 잡 id 목록 (DEFAULT_JOBS `requires` 에서 파생)."""
    j = _job(job_id)
    return list(j.get("requires") or []) if j else []


# ── ② 회복 갭 — cron 시각 차이에서 파생 (숫자 박지 않음) ────────────────
def _cron_minutes(job: dict) -> Optional[int]:
    """cron 잡의 하루 중 실행 시각을 분 단위로. cron 이 아니거나 hour 없으면 None."""
    if str(job.get("trigger")) != "cron":
        return None
    kw = job.get("kwargs") or {}
    if "hour" not in kw:
        return None
    try:
        return int(kw["hour"]) * 60 + int(kw.get("minute", 0))
    except (TypeError, ValueError):
        return None


def recovery_gap_sec(job_id: str, prereq_id: str) -> int:
    """선행 → 후행 사이에 설계된 회복 갭(초). 두 잡의 cron 시각 차이로 *파생*.

    20:00 선계산 → 21:00 발행 = 3600. 선행을 19:30 으로 옮기면 자동으로 5400 이 된다.
    한쪽이 cron 이 아니거나 시각을 못 읽으면 0 (= 선행 직후 바로 진행).
    """
    dep, pre = _job(job_id), _job(prereq_id)
    if not dep or not pre:
        return 0
    a, b = _cron_minutes(dep), _cron_minutes(pre)
    if a is None or b is None:
        return 0
    return max(0, (a - b)) * 60


# ── ③ 선행이 실제로 됐는가 — 저장값이 아니라 실행 기록의 런타임 조회 ────
_SENTINEL = object()


def last_success_at(job_id: str, *, db_path: Optional[str] = None) -> Any:
    """오늘(로컬 날짜) 이 잡이 *성공* 실행된 시작 시각. 없으면 None.

    ★ 파일·플래그가 아니라 `job_runs` 를 읽는다 — 산출물 캐시는 지워질 수 있고,
      플래그는 '시도' 의 기록이지 '완료' 의 증거가 아니다 (루트 헌법: 복사본 금지).
      연기분(`__deferred`)도 같은 선행의 실행으로 인정한다.
      조회 자체가 실패하면 `_SENTINEL` — 게이트 장애가 발행을 영구히 막지 않도록 통과시킨다.
    """
    try:
        from shared.db import DB_PATH
        con = sqlite3.connect(str(db_path or DB_PATH))
        try:
            row = con.execute(
                "SELECT started_at FROM job_runs WHERE (job_id=? OR job_id=?)"
                " AND success=1 AND date(started_at)=date('now','localtime')"
                " ORDER BY started_at DESC LIMIT 1",
                (job_id, job_id + DEFERRED_SUFFIX),
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        return datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        _log(f"job_runs 조회 실패({e}) — 선행 충족으로 간주(fail-open)")
        return _SENTINEL


def readiness(job_id: str, *, db_path: Optional[str] = None) -> dict:
    """발행해도 되는가 — 하나의 불변식으로 판정.

    ★ **발행은 선행 *시작* 후 회복 갭이 지난 뒤에만** (사용자 지정 2026-07-23).
      "선행이 돌았나" 만 보면, 늦게 깨어 선행이 20:44 에 돌고 발행이 21:06 에 도는
      경우 갭이 22분으로 쪼그라든 채 통과해버린다. 두 경우를 한 규칙으로 덮는다.

    Returns: {"ok": bool, "missing": [선행이 아예 없는 잡], "ready_at": datetime|None}
    """
    missing: list[str] = []
    ready_at: Optional[datetime] = None
    for r in requirements(job_id):
        started = last_success_at(r, db_path=db_path)
        if started is _SENTINEL:
            continue                                  # fail-open
        gap = recovery_gap_sec(job_id, r)
        if started is None:
            missing.append(r)
            continue
        due = started + timedelta(seconds=gap)
        if due > datetime.now() and (ready_at is None or due > ready_at):
            ready_at = due
    return {"ok": not missing and ready_at is None, "missing": missing, "ready_at": ready_at}


# ── ④ 유예 비대칭 제거 — 선행 유예는 후행보다 짧을 수 없다 ─────────────
def effective_grace(job_id: str) -> int:
    """이 잡의 misfire 유예 — *자기 값과 자신을 요구하는 후행들의 값 중 최대*.

    ★ 근본 결함(2026-07-23): 선행 1200s < 후행 3600s 라 늦게 깨면 선행만 폐기됐다.
      숫자를 손으로 맞추면 다음에 또 어긋난다 → 등록 시점에 *파생* 한다.
    """
    j = _job(job_id)
    own = int((j or {}).get("misfire_grace_time", 600) or 600)
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    deps = [int(d.get("misfire_grace_time", 600) or 600)
            for d in DEFAULT_JOBS if job_id in (d.get("requires") or [])]
    return max([own] + deps)


# ── ⑤ 선행이 쓸 수 있는 시간 — 후행 실행 시각에서 파생 ──────────────────
def _job_next_run(sched_id: str) -> Optional[datetime]:
    """스케줄러에 등록된 잡의 다음 실행 시각 (tz 제거). 없으면 None."""
    try:
        from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
        sch = get_apscheduler()
        j = sch.get_job(sched_id) if sch else None
        nrt = getattr(j, "next_run_time", None)
        return nrt.replace(tzinfo=None) if nrt else None
    except Exception:
        return None


def _next_cron_fire(job_id: str) -> Optional[datetime]:
    """이 잡의 *다음* 정규 실행 시각 — 연기분이 다음 회차를 침범하는지 판정용."""
    return _job_next_run(job_id.split(DEFERRED_SUFFIX)[0])


def deadline_sec(prereq_id: str, *, margin_sec: int = 120) -> Optional[int]:
    """이 선행이 *지금부터* 쓸 수 있는 시간(초) — 후행 발행 `margin_sec` 전까지.

    ★ 종전엔 선행 쪽이 "20:58 까지" / "06:58 까지" 를 각자 박아뒀다. 그러면 회복 실행
      (예: 21:30 에 선행 → 22:30 발행) 때 목표 시각이 이미 지나 있어 눈먼 기본값(25분)
      으로 떨어진다 — 실제로는 58분을 쓸 수 있는데 25분 만에 잘려 실패할 수 있다.
      후행의 *실제 다음 실행 시각* 에서 파생하면 정규·회복 어느 쪽이든 저절로 맞는다.
      연기분(`__deferred`)이 있으면 그것이 이번 회차의 발행 시각이다.
    None 이면 후행이 없거나 시각을 못 읽은 것 — 호출자가 자기 기본값을 쓴다.
    """
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    best: Optional[datetime] = None
    for d in DEFAULT_JOBS:
        if prereq_id not in (d.get("requires") or []):
            continue
        # 연기분이 있으면 그것이 이번 회차의 발행 시각 — 정규 cron 보다 우선.
        t = _job_next_run(d["id"] + DEFERRED_SUFFIX) or _job_next_run(d["id"])
        if t and (best is None or t < best):
            best = t
    if best is None:
        return None
    left = int((best - datetime.now()).total_seconds()) - margin_sec
    return left if left > 0 else None


def _run_prereq_now(prereq_id: str) -> None:
    """선행을 *지금* 실행 — 인라인 호출이 아니라 APScheduler 에 즉시 잡으로 띄운다.

    ★ 인라인 호출은 `job_runs` 에 남지 않는다 (job_history 는 스케줄러 이벤트만 듣는다).
      기록이 없으면 회복 갭 뒤 재실행 때 readiness 가 여전히 "선행 없음" 으로 읽어
      발행이 취소된다 — *판정 근거를 남기는 경로로만* 실행해야 하는 이유.
      부수 효과로 발행 스레드를 선행이 끝날 때까지 붙잡지 않는다.
    """
    j = _job(prereq_id)
    if not j:
        return
    from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
    from JARVIS04_SCHEDULER.job_registry import _resolve_callback
    fn = _resolve_callback(j["callback"])
    sch = get_apscheduler()
    if sch is None:                                   # 스케줄러 없으면 최선 노력으로 인라인
        _log(f"APScheduler 미초기화 — {prereq_id} 인라인 실행")
        fn()
        return
    kw = {"executor": j["executor"]} if j.get("executor") else {}
    sch.add_job(
        fn, "date", run_date=datetime.now(),
        id=prereq_id + DEFERRED_SUFFIX, name=f"{j['name']} (선행 회복)",
        misfire_grace_time=effective_grace(prereq_id), replace_existing=True, **kw,
    )
    _log(f"선행 실행 예약: {prereq_id}")


def _defer(job_id: str, fn: Callable, next_attempt: int, when: datetime,
           missing: list[str]) -> bool:
    """지정 시각으로 1회 재예약. add_job 은 JARVIS04 안에서만 — 여기가 그 안이다.

    `next_attempt` 는 *연기분이 달고 갈* 시도 번호. 선행을 새로 돌렸을 때만 올라간다
    (단순 대기는 아무것도 재실행하지 않으므로 시도를 소모시키지 않는다).
    Returns: 재예약했으면 True. False 면 이번 회차는 건너뛴 것 — 선행을 돌릴 이유도 없다.
    """
    from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
    sch = get_apscheduler()
    if sch is None:
        _log("APScheduler 미초기화 — 연기 불가")
        return False
    nxt = _next_cron_fire(job_id)
    if nxt and when >= nxt:
        msg = (f"⛔ *{job_id}* 연기 취소\n선행 {', '.join(missing) or '(회복 갭)'} 때문에 뒤로 미루면 "
               f"다음 정규 실행({nxt:%m-%d %H:%M})을 넘어섭니다. 이번 회차는 건너뜁니다.")
        _log(msg.replace("*", ""))
        _tg(msg)
        return False
    base = job_id.split(DEFERRED_SUFFIX)[0]
    sch.add_job(
        gate(base, fn, attempt=next_attempt), "date", run_date=when,
        id=base + DEFERRED_SUFFIX, name=f"{base} (선행 회복 후 재실행)",
        misfire_grace_time=600, replace_existing=True,
    )
    _log(f"{base} → {when:%H:%M} 재예약 (시도 {next_attempt}/{_MAX_ATTEMPTS})")
    return True


def gate(job_id: str, fn: Callable, *, attempt: int = 1) -> Callable:
    """선행조건을 강제하는 콜백 래퍼.

    선행이 충족돼 있으면 원 콜백을 그대로 호출한다(오버헤드 = job_runs 조회 1회).
    미충족이면 *원 콜백을 실행하지 않고* 선행을 즉시 돌린 뒤 회복 갭만큼 뒤로 재예약한다.
    """
    reqs = requirements(job_id)
    if not reqs:
        return fn                                    # 선행 없는 잡은 래핑조차 하지 않는다

    def _wrapped(*args: Any, **kwargs: Any):
        st = readiness(job_id)
        if st["ok"]:
            return fn(*args, **kwargs)

        missing = st["missing"]

        if not missing:
            # 선행은 돌았고 회복 갭만 남았다 — 아무것도 재실행하지 않으므로 시도를 소모하지 않는다.
            when = st["ready_at"]
            _tg(f"⏸ *{job_id}* 보류 — 선행은 완료됐으나 회복 갭 미경과\n"
                f"*{when:%H:%M}* 에 발행합니다.")
            _log(f"회복 갭 미경과 → {when:%H:%M} 재예약")
            _defer(job_id, fn, attempt, when, missing)
            return None

        names = ", ".join(missing)
        if attempt >= _MAX_ATTEMPTS:
            msg = (f"⛔ *{job_id}* 실행 취소\n선행 {names} 이(가) {attempt}회 시도 후에도 "
                   f"충족되지 않았습니다. 이번 회차는 발행하지 않습니다.")
            _log(msg.replace("*", ""))
            _tg(msg)
            return None

        # 회복 갭은 *선행 시작 시각* 부터 센다 (사용자 지정: 21:30 선행 → 22:30 발행)
        gap = max(recovery_gap_sec(job_id, m) for m in missing)
        when = datetime.now() + timedelta(seconds=gap)

        # ★ 순서 주의 — *재예약을 먼저* 한다. 선행은 `deadline_sec()` 로 "발행 몇 분 전까지"
        #   를 읽어 자기 예산을 정하는데, 그 발행 시각이 바로 이 연기분이다. 선행을 먼저
        #   띄우면 아직 없는 연기분을 읽어 눈먼 기본값으로 떨어진다.
        #   연기 자체가 불가능하면(다음 정규 실행 침범) 선행을 돌릴 이유도 없다.
        if not _defer(job_id, fn, attempt + 1, when, missing):
            return None

        _tg(f"⏸ *{job_id}* 보류 — 선행 미완료\n"
            f"선행: {names}\n"
            f"지금 선행을 실행하고 *{when:%H:%M}* 에 발행합니다 (회복 갭 {gap // 60}분).")
        _log(f"선행 {names} 미충족 → 즉시 실행 후 {when:%H:%M} 재예약")

        for m in missing:
            try:
                _run_prereq_now(m)
            except Exception as e:
                _log(f"선행 {m} 실행 예약 실패: {e}")
                try:
                    from JARVIS07_GUARDIAN.error_collector import report
                    report("scheduler", e, module=__name__, func_name=f"prereq:{m}")
                except Exception:
                    pass
        return None

    _wrapped.__name__ = getattr(fn, "__name__", job_id)
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped
