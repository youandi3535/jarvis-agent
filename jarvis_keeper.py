"""jarvis_keeper.py — JARVIS 데몬 24시 감시·자동 재시작 워치독.

launchd KeepAlive=true 로 macOS 부팅 시 자동 시작됨.
30초마다 jarvis_daemon.py 를 점검:
  ① 프로세스 꺼짐(PID 없음) → 재시작 (기존)
  ② ★ 프로세스는 살아있으나 hang (heartbeat stale) → SIGUSR1 스택덤프 후 강제
     재시작 (ERRORS [318] — 2026-07-04). 06:07 사고: 메인스레드 무한 파이썬 루프
     → GIL 기아 → 스케줄러 정지 → 06:30 경제 브리핑 미발화. PID 는 유효해 종전
     PID-only 검사가 재시작을 못 하고 오전 내내 방치됐다. 이를 막는 hang 워치독.
  ③ ★ macOS 시스템 절전(Maintenance Sleep/DarkWake) 오탐 방지 — 자기 루프 gap
     기반 재설계 (ERRORS [389]→재수정 2026-07-06). 절전 중엔 keeper 자신을 포함한
     전 프로세스가 그대로 멈춰 heartbeat 도 같이 정지 — 코드 hang 이 아닌데
     wall-clock 기준 staleness 만으로는 구분 불가.
     ★ 최초 수정(`sysctl kern.waketime` 로 마지막 wake 시각 대조)은 실전에서
     단 한 번도 발동하지 않음 확인(keeper.log 2개월치 전수 grep — 0건). 이
     머신은 수 분 간격으로 짧은 DarkWake/Maintenance Sleep 을 반복해 wake
     시각이 계속 갱신되고, keeper 자신도 그 짧은 깨어있는 창 밖에서는 함께
     멈춰 있어 wake_ts 와 keeper 재개 시각 사이 타이밍 경쟁으로 상시 실패했다.
     대체 방식: **keeper 자기 루프 간격(gap)** 을 직접 감시 — 30초마다 돌아야
     할 while 루프가 그보다 훨씬 크게 벌어졌다면(CHECK_INTERVAL 의 3배 초과)
     그 자체가 "이 프로세스가 방금까지 멈춰 있었다"는 직접 증거이고, 같은
     머신 위 데몬도 동일하게 멈췄을 것이므로 외부 syscall·wake 타임스탬프
     경쟁 없이 안전하게 판별된다. 감지 시 HANG_GRACE 동안 hang 판정을 유예.
"""
from __future__ import annotations

import fcntl
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
KEEPER_LOCK_FILE = JARVIS_DIR / "logs" / "keeper.lock"      # ★ keeper 자신의 중복 실행 방지
_keeper_lock_fd = None   # 프로세스 생존 동안 열어 둬야 락 유지 (GC 방지)
CHECK_INTERVAL = 30      # 초
MAX_RESTART_DELAY = 300  # 연속 실패 시 최대 5분 대기
HANG_THRESHOLD = 360     # heartbeat 이만큼(초) stale 이면 hang 판정 (6분 = 6 missed beats)
HANG_GRACE = 180         # (재)시작 직후 이 시간(초) 동안은 hang 검사 유예 (부팅 여유)
BOOT_TIMEOUT = 180       # ★ (2026-07-06) 스폰된 데몬이 PID_FILE 쓸 때까지 최대 대기 —
                         # crewai/langgraph/sentence-transformers 등 무거운 import + Layer 0
                         # preflight 로 실제 부팅(=_acquire_lock 도달)에 60~70초 소요. CHECK_INTERVAL
                         # (30초) 이 이보다 짧아, 부팅 중인 프로세스를 "꺼짐"으로 오판하고 또 스폰
                         # 하는 중복 기동 사고 발생(같은 시각대 최대 3개 인스턴스 동시 부팅 확인).

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
log = logging.getLogger("jarvis.keeper")


def _acquire_keeper_lock() -> None:
    """keeper 자신의 중복 실행 차단 (fcntl 비블로킹 배타 락).

    ★ (2026-07-12) launchd KeepAlive 경로 밖에서 keeper 가 우발적으로 두 번째
    스폰되면(수동 실행·wake 레이스 등), 두 keeper 가 각자 독립적으로 데몬 다운을
    감지해 거의 동시에 jarvis_daemon.py 를 중복 스폰 → crewai/langgraph 등 무거운
    import 를 두 벌 동시 로딩 → 메모리 압박으로 OS 가 한쪽을 SIGKILL(-9) →
    "데몬 즉시 종료 returncode=-9" 오탐 반복. jarvis_daemon.py 는 이미 동일 패턴의
    자체 락(`_acquire_lock`)을 갖고 있으나 감시자인 keeper 자신에는 없어 발생한 사고.
    """
    global _keeper_lock_fd
    _keeper_lock_fd = open(KEEPER_LOCK_FILE, "a")
    try:
        fcntl.flock(_keeper_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        log.error("🚫 jarvis_keeper 이미 실행 중 — 중복 인스턴스 종료합니다.")
        sys.exit(1)
    _keeper_lock_fd.seek(0)
    _keeper_lock_fd.truncate()
    _keeper_lock_fd.write(str(os.getpid()))
    _keeper_lock_fd.flush()


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


def _notify(msg: str, *, report_error: bool = True) -> None:
    """워치독 이벤트 알림 — 로그 + 텔레그램 (항상) + GUARDIAN 오류 보고 (report_error=True 인 경우만).

    hang 감지처럼 *실제 문제* 신호만 report_error=True(기본) 로 GUARDIAN 오류 학습
    대상화한다. 재시작 완료 같은 *정상 복구 확인* 메시지는 report_error=False 로
    호출해야 함 — 성공 메시지를 RuntimeError 로 포장해 error_collector 에 넘기면
    복구가 성공할 때마다 "오류"가 기록되어 학습 데이터를 오염시킨다.
    """
    log.warning(msg)
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass
    if not report_error:
        return
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


def _start_daemon() -> subprocess.Popen | None:
    """새 데몬 프로세스 스폰. 반환값은 Popen 객체 — 호출자가 `.poll()` 으로 부팅 중
    프로세스의 생사를 계속 추적해 중복 스폰을 막을 수 있어야 하므로 pid 정수가 아닌
    객체 자체를 돌려준다 (2026-07-06 — 중복 기동 버그 수정).
    """
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
            log.info(f"✅ 데몬 프로세스 기동 확인 PID={proc.pid} (preflight/import 부팅 진행 중)")
            return proc
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
    # ★ (2026-07-06) 부팅 중인(아직 PID_FILE 미기록) 스폰 인스턴스 추적 — 중복 기동 방지.
    pending_proc: subprocess.Popen | None = None
    pending_since: float = 0.0
    # ★ (재수정 2026-07-06) 자기 루프 gap 기반 절전 감지 — docstring ③ 참조.
    last_loop_ts: float = 0.0
    sleep_grace_until: float = 0.0

    while True:
        _now = time.time()
        _gap = (_now - last_loop_ts) if last_loop_ts else 0.0
        last_loop_ts = _now
        if _gap > CHECK_INTERVAL * 3:
            sleep_grace_until = _now + HANG_GRACE
            log.info(
                f"💤 keeper 루프 간격 {int(_gap)}초(기대 {CHECK_INTERVAL}초) — "
                f"시스템 절전 감지, {HANG_GRACE}초 유예 시작."
            )

        pid = _read_pid()
        if _is_running(pid):
            fail_count = 0
            pending_proc = None  # PID_FILE 반영 = 부팅 완료, 추적 종료
            # ★ hang 워치독 — PID 는 살아있어도 heartbeat 가 stale 이면 강제 재시작.
            #   (재)시작 직후 HANG_GRACE 동안은 부팅 여유로 검사 유예.
            if time.time() - daemon_start_ts > HANG_GRACE:
                age = _heartbeat_age()
                if age is not None and age > HANG_THRESHOLD:
                    if time.time() < sleep_grace_until:
                        # 직전 루프에서 시스템 절전을 감지해 유예 구간 진입 —
                        # 코드 hang 아님. 유예 종료 후에도 heartbeat 미회복
                        # 시에만 다음 루프에서 진짜 hang 으로 재판정
                        # (조용히 유예, 텔레그램 알림 없음).
                        log.info(
                            f"💤 heartbeat {int(age)}초 정체 — 절전 유예 구간"
                            f"(남은 {int(sleep_grace_until - time.time())}초) — 재시작 유예."
                        )
                    else:
                        _notify(
                            f"🚨 데몬 HANG 감지 (PID={pid}) — heartbeat {int(age)}초 정체 "
                            f"(임계 {HANG_THRESHOLD}s). 스택 덤프(daemon_faulthandler.log) 후 강제 재시작."
                        )
                        _dump_and_kill(pid)
                        new_proc = _start_daemon()
                        daemon_start_ts = time.time()
                        if new_proc:
                            pending_proc = new_proc
                            pending_since = time.time()
                            _notify(
                                f"♻️ 데몬 강제 재시작 완료 PID={new_proc.pid} (hang 복구)",
                                report_error=False,
                            )
                            fail_count = 0
                        else:
                            pending_proc = None
                            fail_count += 1
                # age is None: heartbeat 파일 없음 = 아직 heartbeat 미탑재 데몬일 수
                # 있어 죽이지 않음 (오탐 방지). 이번 배포 재시작 후부터 유효.
        else:
            # ★ (2026-07-06) 직전에 스폰한 인스턴스가 아직 살아서 부팅(무거운 import +
            # Layer 0 preflight) 진행 중일 수 있음 — PID_FILE 은 그 과정을 통과해
            # _acquire_lock() 에 도달해야만 써진다. 이 상태에서 또 스폰하면 동일 데몬이
            # 여러 벌 동시 부팅 → CPU/메모리 낭비 + 뒤늦게 락 획득 실패로 자멸(exit).
            if pending_proc is not None and pending_proc.poll() is None:
                elapsed = time.time() - pending_since
                if elapsed <= BOOT_TIMEOUT:
                    log.info(f"⏳ 데몬 부팅 중(PID={pending_proc.pid}, {int(elapsed)}초 경과) — 재시작 보류")
                    time.sleep(CHECK_INTERVAL)
                    continue
                log.warning(f"⏱️ 부팅 {int(elapsed)}초 초과(PID_FILE 미기록) — 정지로 간주, 강제 종료 후 재시도")
                try:
                    pending_proc.kill()
                except Exception:
                    pass
                pending_proc = None

            log.warning(f"⚠️  데몬 꺼짐 감지 (PID={pid}) — 재시작 시도 #{fail_count + 1}")
            delay = min(30 * (2 ** fail_count), MAX_RESTART_DELAY)
            if fail_count > 0:
                log.info(f"  연속 실패 — {delay}초 대기 후 재시도")
                time.sleep(delay)
            new_proc = _start_daemon()
            daemon_start_ts = time.time()
            if new_proc:
                fail_count = 0
                pending_proc = new_proc
                pending_since = time.time()
            else:
                fail_count += 1
                pending_proc = None

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # SIGTERM 수신 시 깔끔하게 종료
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    _acquire_keeper_lock()
    main()
