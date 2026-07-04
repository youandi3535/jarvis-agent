"""jarvis_keeper.py — JARVIS 데몬 24시 감시·자동 재시작 워치독.

launchd KeepAlive=true 로 macOS 부팅 시 자동 시작됨.
30초마다 jarvis_daemon.py 를 점검:
  ① 프로세스 꺼짐(PID 없음) → 재시작 (기존)
  ② ★ 프로세스는 살아있으나 hang (heartbeat stale) → SIGUSR1 스택덤프 후 강제
     재시작 (ERRORS [318] — 2026-07-04). 06:07 사고: 메인스레드 무한 파이썬 루프
     → GIL 기아 → 스케줄러 정지 → 06:30 경제 브리핑 미발화. PID 는 유효해 종전
     PID-only 검사가 재시작을 못 하고 오전 내내 방치됐다. 이를 막는 hang 워치독.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

JARVIS_DIR = Path(__file__).parent
DAEMON_SCRIPT = JARVIS_DIR / "jarvis_daemon.py"
PID_FILE = JARVIS_DIR / "logs" / "daemon.pid"
HEARTBEAT_FILE = JARVIS_DIR / "logs" / "daemon.heartbeat"   # 데몬 스케줄러 생존 신호
PYTHON = JARVIS_DIR / ".venv" / "bin" / "python"
CHECK_INTERVAL = 30      # 초
MAX_RESTART_DELAY = 300  # 연속 실패 시 최대 5분 대기
HANG_THRESHOLD = 360     # heartbeat 이만큼(초) stale 이면 hang 판정 (6분 = 6 missed beats)
HANG_GRACE = 180         # (재)시작 직후 이 시간(초) 동안은 hang 검사 유예 (부팅 여유)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("jarvis.keeper")


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def _is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _heartbeat_age() -> float | None:
    """heartbeat 파일이 마지막 갱신된 지 몇 초 지났나. 없으면 None."""
    try:
        return time.time() - HEARTBEAT_FILE.stat().st_mtime
    except Exception:
        return None


def _notify(msg: str) -> None:
    """워치독 이벤트 알림 — 로그 + 텔레그램 + GUARDIAN (모두 best-effort)."""
    log.warning(msg)
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass
    try:
        from JARVIS07_GUARDIAN.error_collector import report as _gr
        _gr("keeper", RuntimeError(msg), module="jarvis_keeper", func_name="watchdog")
    except Exception:
        pass


def _dump_and_kill(pid: int) -> None:
    """hang 데몬 → SIGUSR1(전 스레드 파이썬 스택 덤프) → SIGKILL → 사망 확인.

    SIGUSR1: 데몬의 faulthandler 가 무한루프의 정확한 파이썬 위치를
             logs/daemon_faulthandler.log 에 기록 (다음 디버깅 근거).
    SIGKILL: GIL 이 잠긴 hang 에는 SIGTERM(파이썬 핸들러 경유)이 무력 →
             OS 강제 종료. fcntl 락은 OS 가 자동 해제하므로 stale 락 없음.
    """
    try:
        os.kill(pid, signal.SIGUSR1)   # 스택 덤프 요청
        time.sleep(3)                   # faulthandler 기록 여유
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    # 사망(→ fcntl 락 해제) 확인 — 최대 10초. 이후 fresh 데몬이 락 획득 가능.
    for _ in range(20):
        if not _is_running(pid):
            break
        time.sleep(0.5)


def _start_daemon() -> int | None:
    log.info("🚀 jarvis_daemon.py 시작 중…")
    try:
        proc = subprocess.Popen(
            [str(PYTHON), str(DAEMON_SCRIPT)],
            cwd=str(JARVIS_DIR),
            stdout=open(JARVIS_DIR / "logs" / "daemon_stdout.log", "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(3)
        if proc.poll() is None:
            log.info(f"✅ 데몬 시작 완료 PID={proc.pid}")
            return proc.pid
        else:
            log.error(f"❌ 데몬 즉시 종료 (returncode={proc.returncode})")
            try:
                from JARVIS07_GUARDIAN.error_collector import report as _gr
                _gr("keeper", RuntimeError(f"데몬 즉시 종료 returncode={proc.returncode}"),
                    module="jarvis_keeper", func_name="start_daemon")
            except Exception:
                pass
            return None
    except Exception as e:
        log.error(f"❌ 데몬 시작 실패: {e}")
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _gr
            _gr("keeper", e, module="jarvis_keeper", func_name="start_daemon")
        except Exception:
            pass
        return None


def main() -> None:
    log.info("🛡️  JARVIS Keeper 시작 — 30초 간격 감시 (프로세스 + heartbeat 워치독)")
    fail_count = 0
    # 데몬이 이미 떠 있으면 그 시작 시각을 모름 → keeper 부팅 시각 기준으로 유예.
    daemon_start_ts = time.time()

    while True:
        pid = _read_pid()
        if _is_running(pid):
            fail_count = 0
            # ★ hang 워치독 — PID 는 살아있어도 heartbeat 가 stale 이면 강제 재시작.
            #   (재)시작 직후 HANG_GRACE 동안은 부팅 여유로 검사 유예.
            if time.time() - daemon_start_ts > HANG_GRACE:
                age = _heartbeat_age()
                if age is not None and age > HANG_THRESHOLD:
                    _notify(
                        f"🚨 데몬 HANG 감지 (PID={pid}) — heartbeat {int(age)}초 정체 "
                        f"(임계 {HANG_THRESHOLD}s). 스택 덤프(daemon_faulthandler.log) 후 강제 재시작."
                    )
                    _dump_and_kill(pid)
                    new_pid = _start_daemon()
                    daemon_start_ts = time.time()
                    if new_pid:
                        _notify(f"♻️ 데몬 강제 재시작 완료 PID={new_pid} (hang 복구)")
                        fail_count = 0
                    else:
                        fail_count += 1
                # age is None: heartbeat 파일 없음 = 아직 heartbeat 미탑재 데몬일 수
                # 있어 죽이지 않음 (오탐 방지). 이번 배포 재시작 후부터 유효.
        else:
            log.warning(f"⚠️  데몬 꺼짐 감지 (PID={pid}) — 재시작 시도 #{fail_count + 1}")
            delay = min(30 * (2 ** fail_count), MAX_RESTART_DELAY)
            if fail_count > 0:
                log.info(f"  연속 실패 — {delay}초 대기 후 재시도")
                time.sleep(delay)
            new_pid = _start_daemon()
            daemon_start_ts = time.time()
            if new_pid:
                fail_count = 0
            else:
                fail_count += 1

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # SIGTERM 수신 시 깔끔하게 종료
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
