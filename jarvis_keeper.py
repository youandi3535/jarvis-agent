"""jarvis_keeper.py — JARVIS 데몬 24시 감시·자동 재시작 워치독.

launchd KeepAlive=true 로 macOS 부팅 시 자동 시작됨.
30초마다 jarvis_daemon.py 프로세스를 점검하고, 꺼져 있으면 재시작.
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
PYTHON = JARVIS_DIR / ".venv" / "bin" / "python"
CHECK_INTERVAL = 30   # 초
MAX_RESTART_DELAY = 300  # 연속 실패 시 최대 5분 대기

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
    log.info("🛡️  JARVIS Keeper 시작 — 30초 간격 감시")
    fail_count = 0

    while True:
        pid = _read_pid()
        if _is_running(pid):
            fail_count = 0
        else:
            log.warning(f"⚠️  데몬 꺼짐 감지 (PID={pid}) — 재시작 시도 #{fail_count + 1}")
            delay = min(30 * (2 ** fail_count), MAX_RESTART_DELAY)
            if fail_count > 0:
                log.info(f"  연속 실패 — {delay}초 대기 후 재시도")
                time.sleep(delay)
            new_pid = _start_daemon()
            if new_pid:
                fail_count = 0
            else:
                fail_count += 1

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # SIGTERM 수신 시 깔끔하게 종료
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    main()
