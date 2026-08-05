"""데몬 공백(다운타임) 회계 — "내가 꺼져 있던 동안 무엇을 잃었나".

★ 왜 이게 필요한가 (2026-08-05 실측)
  최근 30일 발행 손실의 **27%(8일)** 가 *맥북이 꺼져 있어서* 였다. 그런데 시스템 안에는
  그 사실을 알려줄 수단이 하나도 없었다 — 알려줄 주체가 함께 죽으니까.
  32일 전수 조사: 18분 초과 공백 124회 · 총 202시간. 그중 **발행 슬롯이 공백에 든 것 17건**.
  더 나쁜 건, 그중 8건은 *슬롯 시각과 감사 시각이 둘 다 공백 안* 이라
  **어떤 경보도 울린 적이 없다.** APScheduler 메모리 잡스토어는 지나간 cron 을 재생하지 않는다.

★ 무엇을 하고, 무엇을 안 하는가 (복구 정책 A — 사용자 결정 2026-08-05)
  · 한다  — 공백 구간 계산 · 그 구간에 든 슬롯의 실제 결손 확인 · 원장 박제 · 1회 보고
  · 안 한다 — **재발행.** 발행은 07:00·21:00 두 번뿐이다. 놓친 것은 손실로 둔다.
    경보의 목적은 되살리기가 아니라 *무엇을 왜 잃었는지 알게 하는 것* 이다.

★ 한계 (정직하게)
  외부 감시 서비스를 쓰지 않기로 했으므로 **실시간 다운 알림은 물리적으로 불가능하다.**
  죽은 기계는 자기가 죽었다고 말할 수 없고 텔레그램도 함께 죽는다.
  이 모듈이 할 수 있는 최선은 *복귀 직후의 정확한 사후 보고* 다.

★ 주인 경계 (원칙①)
  · 시각을 다루면 여기(INFRA) — 데몬 생애주기는 인프라 소유
  · 슬롯을 다루면 `JARVIS08_PUBLISH/publish_ledger` — 슬롯·결손 지식은 발행 도메인 소유
  이 파일은 슬롯 시각을 스스로 계산하지 않고, ledger 는 데몬이 살았는지 모른다.
"""
from __future__ import annotations

import datetime as _dt
import os

__all__ = ["last_alive", "downtime_window", "downtime_threshold_sec",
           "report_boot_downtime", "selfcheck"]

# ★ 모듈 로드 시각을 캡처한다 (검토 지적 ②).
#   `last_alive()` 의 기준을 `now()` 로 두면, 부팅 절차가 heartbeat 주기(180초)를 넘길 때
#   **자기 자신이 방금 찍은 heartbeat** 를 '직전 생존' 으로 읽어 공백이 0 으로 접힌다.
#   그러면 아무 보고도 없이 조용히 지나간다 — 가장 나쁜 실패 형태.
_IMPORT_TS = _dt.datetime.now()


def downtime_threshold_sec() -> int:
    """이만큼 이상 끊기면 '공백' 으로 본다 — **발행 손실이 갈리는 임계에서 파생**.

    ★ 왜 misfire_grace 인가 (검토 지적 ⑥)
      heartbeat 주기 × N (keeper 의 hang 판정) 을 쓰고 싶어지지만 그건 *다른 질문* 이다.
      keeper 는 "프로세스가 멈췄나" 를 묻고, 여기는 "슬롯을 잃었나" 를 묻는다.
      슬롯 손실이 실제로 갈리는 값은 발행 잡의 `misfire_grace_time` 이다 —
      그 안에 데몬이 돌아오면 APScheduler 가 늦게라도 실행한다(실측 08-02 13:40 일괄 실행).
      무배포 조정: `JARVIS_DOWNTIME_THRESHOLD_SEC`.
    """
    env = os.getenv("JARVIS_DOWNTIME_THRESHOLD_SEC", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback
        graces = [int(j.get("misfire_grace_time") or 0) for j in DEFAULT_JOBS
                  if is_publish_callback(j.get("callback"))]
        if graces and max(graces) > 0:
            return max(graces)
    except Exception:
        pass
    return 3600


def last_alive(before: _dt.datetime | None = None) -> _dt.datetime | None:
    """직전 화신이 마지막으로 살아 있던 시각 — `job_runs` 의 heartbeat 행에서.

    ★ 왜 heartbeat *파일* 이 아닌가
      `logs/daemon.heartbeat` 는 데몬이 부팅 중에 덮어쓴다. 이 함수가 불릴 때는
      이미 현재 시각으로 갱신돼 있어 **직전 생존 시각이 소실**된다.
      `job_runs` 는 append-only 라 과거가 남는다(보존 60일).

    Args:
        before: 이 시각 *이전* 의 마지막 beat 만 본다. 기본은 모듈 로드 시각 —
            내가 방금 찍은 beat 를 직전 생존으로 착각하지 않기 위해서다.
    """
    cutoff = (before or _IMPORT_TS).strftime("%Y-%m-%d %H:%M:%S")
    try:
        from JARVIS04_SCHEDULER.job_registry import heartbeat_job_id
        from shared.db import get_db
        jid = heartbeat_job_id()
        if not jid:
            # fail-closed — 조용히 넘어가지 않는다 (검토 지적 ③)
            print("  ⚠️ [downtime] heartbeat 잡 ID 를 파생하지 못함 — 공백 회계 불가")
            return None
        with get_db() as con:
            row = con.execute(
                "SELECT MAX(started_at) FROM job_runs WHERE job_id=? AND started_at < ?",
                (jid, cutoff)).fetchone()
        if not row or not row[0]:
            return None
        return _dt.datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        print(f"  ⚠️ [downtime] 직전 생존 시각 조회 실패: {e}")
        return None


def downtime_window(now: _dt.datetime | None = None) -> tuple | None:
    """(공백 시작, 공백 끝) — 임계 미만이면 None(정상 재시작).

    공백 시작은 '마지막 beat' 가 아니라 **그 다음 beat 가 있었어야 할 시각** 이다.
    마지막 beat 직후 1주기 동안은 아직 살아 있었을 수 있기 때문이다.
    """
    now = now or _IMPORT_TS
    last = last_alive(now)
    if last is None:
        return None
    try:
        from JARVIS04_SCHEDULER.job_registry import heartbeat_interval_seconds
        step = heartbeat_interval_seconds() or 180
    except Exception:
        step = 180
    start = last + _dt.timedelta(seconds=step)
    if (now - start).total_seconds() < downtime_threshold_sec():
        return None
    return start, now


def report_boot_downtime(now: _dt.datetime | None = None,
                         *, dry_run: bool = False) -> dict:
    """부팅 1회 — 공백 구간의 슬롯 손실을 원장에 박제하고 사용자에게 보고.

    ★ 재발행하지 않는다 (정책 A). 원장 박제 + 텔레그램 1건이 전부다.
    ★ 중복 억제는 `record_publish_gap` 이 `slot_key` 로 한다 — 감사 잡이 나중에
      같은 슬롯을 또 알리지 않는다.
    """
    win = downtime_window(now)
    if win is None:
        return {"downtime": False}
    start, end = win
    hours = (end - start).total_seconds() / 3600.0

    from JARVIS08_PUBLISH.publish_ledger import (missed_slots, publishing_in_progress,
                                                 record_publish_gap)

    losses = missed_slots(start, end)
    # 마지막 슬롯이 아직 진행 중이면 손실로 확정하지 않는다 (검토 지적 ⑤).
    if losses and publishing_in_progress():
        losses = losses[:-1]

    out = {"downtime": True, "hours": round(hours, 1),
           "from": start.isoformat(timespec="minutes"),
           "to": end.isoformat(timespec="minutes"),
           "slots": [l["key"] for l in losses], "recorded": 0}
    if dry_run:
        return out

    for l in losses:
        for pf in l["missing"]:
            if record_publish_gap(l["post_type"], pf, l["start"], l["end"],
                                  reason="daemon_down"):
                out["recorded"] += 1

    print(f"  📉 [downtime] 공백 {hours:.1f}h — 슬롯 손실 {len(losses)}건 "
          f"· 신규 박제 {out['recorded']}건")
    if out["recorded"]:
        try:
            from shared.notify import send_tg
            lines = [
                f"📉 *시스템 공백* — {hours:.1f}시간",
                "",
                f"{start:%m/%d %H:%M} ~ {end:%m/%d %H:%M} 동안 데몬이 꺼져 있었습니다.",
                f"그 사이 발행 슬롯 *{len(losses)}개* 를 놓쳤습니다:",
                "",
                *[f"  ❌ {l['post_type']} {l['start']:%m/%d %H:%M} → {'·'.join(l['missing'])}"
                  for l in losses],
                "",
                "_재발행하지 않습니다 (복구 정책 A — 발행은 정규 시각에만)._",
                "원인 조사: `docs/RUNBOOK.md` §2 (데몬이 살아 있는가)",
            ]
            send_tg("\n".join(lines))
        except Exception as e:
            print(f"  ⚠️ [downtime] 보고 전송 실패: {e}")
    return out


def selfcheck() -> dict:
    """파생이 실제로 되는지 — 코드 존재는 적용의 증거가 아니다."""
    issues = []
    try:
        from JARVIS04_SCHEDULER.job_registry import (heartbeat_interval_seconds,
                                                     heartbeat_job_id)
        if not heartbeat_job_id():
            issues.append("heartbeat 잡 ID 파생 실패")
        if heartbeat_interval_seconds() <= 0:
            issues.append("heartbeat 주기 파생 실패")
    except Exception as e:
        issues.append(f"job_registry 파생 불가: {e}")
    if downtime_threshold_sec() <= 0:
        issues.append("공백 임계 파생 실패")
    return {"threshold_sec": downtime_threshold_sec(),
            "last_alive": (last_alive() or "").__str__(), "issues": issues}
