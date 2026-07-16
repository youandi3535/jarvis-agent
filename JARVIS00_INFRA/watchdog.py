"""JARVIS00_INFRA/watchdog.py — 정지(freeze/livelock) 감지 단일 진입점 (사용자 박제 2026-07-06).

두 불변식 — 어떤 경우라도:
  · 멈춤(freeze): 최대 300초(5분) 무진전 → 정지.
  · 재시도/재시작: 최대 3회 (shared/llm.py·harness DEFAULT_MAX_ATTEMPTS·재시작 카운터에서 강제).
작업 전체 데드라인(예: 블로그 발행 액션당 30분)은 호출자가 deadline_sec 로 지정.

정지 감지 시: on_stuck 콜백(GUARDIAN 보고) 후,
  · killable subprocess(발행 등 독립 작업)면 os._exit → 다음 예약이 깨끗하게 재시도(원인은 GUARDIAN 진단).
  · 데몬 본체 등 kill 금지 프로세스면 *보고만* + 협조적 데드라인(check)으로 중단.

다른 파일에 freeze/데드라인 감지 로직 신설 금지 — 이 모듈 경유 (인프라 단일 진입점, JARVIS00_INFRA).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("jarvis.watchdog")

# ── 불변식 상수 (SSOT) ──
FREEZE_LIMIT_SEC = 300      # 멈춤 상한 — 어떤 경우라도 5분 무진전이면 정지
MAX_RETRIES = 3             # 재시도·재시작 상한 — 어떤 경우라도 3회
WATCHDOG_KILL_RC = 75       # EX_TEMPFAIL — 이 코드로 종료된 subprocess는 워치독 강제킬(원인은 GUARDIAN 진단, stderr 무관)

# ── ★ 전역 하트비트 (진행 신호 단일 진입점) ──
#   LLM 호출·수집 라운드·Selenium 액션 등 *모든 진행*이 이걸 갱신 → "정말 아무 진행도
#   없을 때만" freeze 판정. (오래 걸리는 정상 스텝을 오탐하지 않음 — 사용자 박제 2026-07-06)
#   빠짐없이 적용 대상: shared/llm.py(invoke_text)·JARVIS09 수집·발행 Selenium 등.
_GLOBAL_BEAT = [0.0]


def beat() -> None:
    """전역 진행 신호 — 어떤 오래 걸리는 작업이든 진척 시 호출. freeze 카운터 리셋."""
    _GLOBAL_BEAT[0] = time.time()


def last_global_beat() -> float:
    return _GLOBAL_BEAT[0]

# 작업별 전체 데드라인 (★ 2026-07-16: 이벤트 루프 오염 버그 수정으로 LLM hang 근본 원인 제거 → 30분 복원)
BLOG_ACTION_DEADLINE_SEC = 1800     # 30분 — 경제/테마 발행 액션(네이버·티스토리 각각)
DEFAULT_ACTION_DEADLINE_SEC = 3600  # 60분 — 그 외 액션(auto_repair 심층감사 등) 넉넉한 안전망


def is_killable_subprocess() -> bool:
    """이 프로세스가 정지 시 강제 종료(os._exit)해도 되는 *독립 작업 subprocess* 인가.

    데몬(jarvis_daemon)·keeper 본체는 절대 kill 금지 — 전체 시스템 다운.
    스케줄 발행·분석 등 --scheduled 로 뜬 독립 스크립트만 killable.
    """
    if os.environ.get("JARVIS_KILLABLE_SUBPROCESS") == "1":
        return True
    if os.environ.get("JARVIS_NO_WATCHDOG_KILL") == "1":
        return False
    argv = " ".join(sys.argv)
    # 데몬/keeper 본체는 제외
    if "jarvis_daemon.py" in argv or "jarvis_keeper.py" in argv:
        return False
    return any(flag in argv for flag in ("--scheduled", "--naver-only", "--tistory-only"))


class StuckError(RuntimeError):
    """데드라인/멈춤 감지 — 협조적 check() 가 발생시킴."""


class Watchdog:
    """작업을 감싸 멈춤(freeze)·데드라인을 감시. context manager.

    사용:
        with Watchdog("경제 발행 — 네이버", deadline_sec=1800, on_stuck=_report) as wd:
            for step in steps:
                wd.beat()      # 진행 신호 (freeze 카운터 리셋)
                wd.check()     # 데드라인 초과면 StuckError (협조적)
                step()
    """

    def __init__(self, name: str, deadline_sec: float | None = None,
                 freeze_sec: float = FREEZE_LIMIT_SEC, on_stuck=None,
                 kill_on_freeze: bool | None = None, poll_sec: float = 15.0):
        self.name = name
        self.deadline_sec = float(deadline_sec) if deadline_sec else None
        self.freeze_sec = max(30.0, float(freeze_sec or FREEZE_LIMIT_SEC))
        self.on_stuck = on_stuck
        self.kill_on_freeze = is_killable_subprocess() if kill_on_freeze is None else bool(kill_on_freeze)
        self.poll_sec = max(1.0, float(poll_sec))
        self._start = 0.0
        self._last_beat = 0.0
        self._last_tick = 0.0
        self._stop = threading.Event()
        self._thr: threading.Thread | None = None
        self.stuck_reason: str | None = None

    # ── 진행 신호 ──
    def beat(self) -> None:
        """진행 중임을 알림 — freeze(무진전) 카운터 리셋. 스텝마다 호출."""
        self._last_beat = time.time()

    def elapsed(self) -> float:
        """실질 경과 시간. 절전 구간은 _absorb_sleep_gap() 이 감지 시 self._start 를
        함께 미뤄 자동 제외한다 (ERRORS [396][414] 후속)."""
        return time.time() - self._start if self._start else 0.0

    # ── 절전(sleep) gap 흡수 — check()·_monitor() 양쪽이 공유 (★ ERRORS [414] 후속 레이스 수정) ──
    def _absorb_sleep_gap(self) -> bool:
        """직전 관측 시각 대비 비정상적으로 큰 시간차가 있으면 OS 절전(맥 Maintenance
        Sleep 등)으로 보고 self._start/_last_beat 를 함께 밀어 elapsed() 를 실제 진행
        시간 기준으로 복구한다. gap 흡수 시 True.

        ★ 배경 — [414]는 이 보정을 _monitor() 백그라운드 스레드의 poll_sec(15s) 틱
        안에서만 수행했다. 그런데 협조적 check() 는 메인 스레드에서 attempt 경계마다
        *즉시* 호출된다 — subprocess.run() 등 장시간 블로킹 호출이 절전으로 함께
        멈췄다가 깨어난 직후, monitor 스레드가 다음 틱을 돌기 *전에* check() 가 먼저
        실행되면 아직 보정 안 된 self._start 로 elapsed() 를 계산해 "데드라인 초과"가
        재발한다(트렌드 수집 harness가 check() 안에서 StuckError 를 던진 사고가 이 경로).
        두 호출자가 동일한 절전 판정 로직을 공유해야 어느 쪽이 먼저 깨어나든 즉시 흡수된다.
        """
        now = time.time()
        last = self._last_tick or self._start
        gap = now - last
        self._last_tick = now
        if last and gap > self.poll_sec * 3:
            log.info(f"[watchdog] 💤 '{self.name}' 관측 간격 {gap:.0f}s(기대 {self.poll_sec:.0f}s) "
                     f"— 시스템 절전 감지, self._start 보정")
            self._start += gap
            self._last_beat = now
            return True
        return False

    # ── 협조적 데드라인 체크 (스텝/시도 사이) ──
    def check(self) -> None:
        """데드라인 초과면 StuckError. 스텝·시도 사이에서 호출 (안전한 중단점)."""
        if self.deadline_sec and self._start:
            self._absorb_sleep_gap()
            if self.elapsed() > self.deadline_sec:
                self.stuck_reason = (f"데드라인 초과 {self.elapsed():.0f}s > {self.deadline_sec:.0f}s "
                                     f"({self.deadline_sec/60:.0f}분)")
                raise StuckError(self.stuck_reason)

    # ── 백그라운드 감시 (freeze — 협조적 체크가 못 도는 진짜 얼어붙음) ──
    def _monitor(self) -> None:
        while not self._stop.wait(self.poll_sec):
            if self._absorb_sleep_gap():
                continue   # 이번 틱은 절전 보정만 — freeze/데드라인 판정은 다음 틱부터
            now = time.time()
            # freeze = 로컬 beat(스텝 사이) 와 전역 beat(LLM·수집 등 진행) 중 *최근* 것 기준
            #   → 오래 걸리는 정상 작업(LLM 반복 호출 등)은 전역 beat 로 살아있음 = 오탐 방지
            last = max(self._last_beat, _GLOBAL_BEAT[0])
            frozen = (now - last) if last else 0.0
            elapsed = self.elapsed()
            reason = None
            if frozen > self.freeze_sec:
                reason = f"멈춤(freeze) {frozen:.0f}s > {self.freeze_sec:.0f}s 무진전"
            elif self.deadline_sec and elapsed > (self.deadline_sec + self.poll_sec):
                # 협조적 check 가 안 도는(스텝 내부 블로킹) 데드라인 초과도 감시 스레드가 잡음
                reason = f"데드라인 초과(블로킹) {elapsed:.0f}s > {self.deadline_sec:.0f}s"
            if reason:
                self.stuck_reason = reason
                log.error(f"[watchdog] 🛑 '{self.name}': {reason}")
                try:
                    if self.on_stuck:
                        self.on_stuck(self.name, reason)
                except Exception as e:
                    log.warning(f"[watchdog] on_stuck 콜백 실패: {e}")
                if self.kill_on_freeze:
                    log.error(f"[watchdog] '{self.name}' 강제 종료(os._exit 75) — killable subprocess. "
                              f"원인은 GUARDIAN 진단, 다음 예약이 깨끗하게 재시도.")
                    os._exit(WATCHDOG_KILL_RC)   # EX_TEMPFAIL
                return             # kill 금지 프로세스 — 1회 보고 후 감시 종료(재보고 방지)

    def __enter__(self) -> "Watchdog":
        self._start = time.time()
        self._last_beat = self._start
        self._last_tick = self._start
        self._thr = threading.Thread(target=self._monitor, name=f"wd-{self.name[:20]}", daemon=True)
        self._thr.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._stop.set()
        return False


import contextlib


def _default_on_stuck(name: str, reason: str) -> None:
    """정지 시 GUARDIAN 보고 (독립 스크립트 공용)."""
    try:
        from JARVIS07_GUARDIAN.error_collector import report
        report("watchdog", RuntimeError(f"정지 감지 — {name}: {reason}"),
               module="JARVIS00_INFRA.watchdog", func_name=name)
    except Exception:
        pass


@contextlib.contextmanager
def guard_main(name: str, deadline_sec: float | None = None,
               freeze_sec: float = FREEZE_LIMIT_SEC, kill_on_freeze: bool = True):
    """독립 스크립트 __main__ 의 *일회성 작업* 정지 방어 (사용자 박제 2026-07-06).

        from JARVIS00_INFRA.watchdog import guard_main
        with guard_main("경제 발행", deadline_sec=1800):
            run()

    kill_on_freeze=True 기본 — 워커 스크립트는 정지 시 os._exit(다음 예약 재시도).
    ★ 무한 폴링 데몬(--watch 등)의 *전체 루프* 는 감싸지 말 것 — 각 폴 처리 단위만 감쌀 것.
    """
    with Watchdog(name, deadline_sec=deadline_sec, freeze_sec=freeze_sec,
                  on_stuck=_default_on_stuck, kill_on_freeze=kill_on_freeze):
        yield


__all__ = ["Watchdog", "StuckError", "guard_main", "beat", "last_global_beat",
           "is_killable_subprocess",
           "FREEZE_LIMIT_SEC", "MAX_RETRIES", "WATCHDOG_KILL_RC",
           "BLOG_ACTION_DEADLINE_SEC", "DEFAULT_ACTION_DEADLINE_SEC"]
