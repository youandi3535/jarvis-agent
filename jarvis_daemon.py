"""
JARVIS 마스터 통합 데몬 v2
────────────────────────────────────────────────────────────
JARVIS02 (Market Signal) + JARVIS03 (RADAR) 전체를 단일 프로세스로 관리.

포함 기능:
  1. JARVIS04_SCHEDULER — *모든* 시간 기반 잡 단일 컨트롤 타워 (DEFAULT_JOBS + 일일 브리핑)
  2. 통합 텔레그램 봇 — JARVIS02 명령어 + JARVIS01 ReAct + JARVIS03 인라인 승인 버튼 통합
  3. 파일 락      — fcntl LOCK_EX + PID 파일 이중 방어 (어떤 경우에도 단일 인스턴스 강제)

실행:
  cd ~/portfolio/jarvis-agent
  python jarvis_daemon.py

백그라운드 실행:
  nohup python jarvis_daemon.py > logs/daemon.log 2>&1 &
"""
from __future__ import annotations

import sys, os, time, threading, subprocess, logging, signal, importlib.util, requests, fcntl, atexit
from pathlib import Path
from datetime import datetime

# python jarvis_daemon.py 로 직접 실행 시 __main__ 으로 로드됨.
# 다른 모듈이 `import jarvis_daemon` 하면 __main__ 이 아닌 별도 모듈로 두 번 로드되어
# _daemon_shutdown 등 전역 상태가 분리됨 → set() 해도 main 루프가 깨어나지 않는 버그.
# 아래 한 줄로 __main__ 을 jarvis_daemon 이름으로도 등록 → 단일 모듈 보장.
if __name__ == "__main__":
    sys.modules.setdefault("jarvis_daemon", sys.modules["__main__"])

# ── 경로 설정 ─────────────────────────────────────────────────
JARVIS_ROOT = Path(__file__).parent
WRITER_DIR  = JARVIS_ROOT / "JARVIS02_WRITER"
RADAR_DIR   = JARVIS_ROOT / "JARVIS03_RADAR"
PLANS_DIR   = JARVIS_ROOT / "logs" / "pending_plans"  # 파일 기반 pending plan 저장소

# JARVIS02 모듈 임포트를 위해 경로 추가
sys.path.insert(0, str(JARVIS_ROOT))
sys.path.insert(0, str(WRITER_DIR))

from dotenv import load_dotenv
load_dotenv(JARVIS_ROOT / ".env")

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── 로그 설정 ─────────────────────────────────────────────────
LOG_DIR = JARVIS_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)-28s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "daemon.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("JARVIS-DAEMON")


def _report_daemon(e: Exception, func_name: str = "") -> None:
    """데몬 내 예외를 GUARDIAN 에 지연 import 로 안전하게 보고."""
    try:
        from JARVIS07_GUARDIAN.error_collector import report
        report("daemon", e, module="jarvis_daemon", func_name=func_name)
    except Exception:
        pass


# ── PID 파일 ─────────────────────────────────────────────────
PID_FILE = LOG_DIR / "daemon.pid"

# ── 종료 이벤트 (전체 스레드 공유) ──────────────────────────
_daemon_shutdown  = threading.Event()
_daemon_start_time = datetime.now()
_apscheduler      = None   # _start_scheduler() 에서 설정


# ════════════════════════════════════════════════════════════
# 중복 실행 방지 — OS 레벨 파일 락 (fcntl) + PID 파일 이중 방어
# ════════════════════════════════════════════════════════════
# fcntl.LOCK_EX|LOCK_NB: 비블로킹 배타적 락.
#   - 프로세스가 살아있는 한 OS가 락을 유지.
#   - SIGKILL 포함 어떤 종료에도 OS가 자동 해제 → stale 락 없음.
#   - 두 번째 실행 시 IOError 즉시 → sys.exit(1).

_LOCK_FILE = LOG_DIR / "daemon.lock"
_lock_fd   = None   # 프로세스 생존 동안 열어 둬야 락이 유지됨


def _acquire_lock():
    """중복 실행 강제 차단. 어떤 경우에도 단일 인스턴스만 허용."""
    global _lock_fd
    _lock_fd = open(_LOCK_FILE, "a")  # "a": truncate 없이 열기 — 락만 사용
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        # 이미 다른 인스턴스가 락을 보유 중
        pid_hint = ""
        if PID_FILE.exists():
            try:
                pid_hint = f" (PID {PID_FILE.read_text().strip()})"
            except Exception:
                pass
        log.error(f"🚫 데몬 이미 실행 중{pid_hint}. 중복 실행 거부 — 종료합니다.")
        sys.exit(1)
    # 락 획득 성공 → PID 기록 (LOCK_FILE 은 현재 pid 만 유지 — 소유권 판별·무증식)
    _lock_fd.seek(0)
    _lock_fd.truncate()
    _lock_fd.write(str(os.getpid()))
    _lock_fd.flush()
    PID_FILE.write_text(str(os.getpid()))
    atexit.register(_release_lock)


def _release_lock():
    # ★ 데몬 이중 기동 레이스 방지 (사용자 박제 2026-07-04 — ERRORS [321]):
    #   느린 SIGINT/`/restart` 종료 도중 keeper 가 이미 새 데몬을 띄웠을 수 있다.
    #   뒤늦게 종료하는 구 데몬이 *새 데몬의* pid 파일을 지우면 keeper 가 또 중복 기동하는
    #   연쇄가 발생 → pid 파일은 *내 pid 일 때만* 제거(소유권 확인). LOCK_FILE 은 unlink
    #   하지 않는다(inode 안정 — 삭제 시 다른 데몬이 새 inode 로 별도 flock 획득 = 이중 락
    #   위험). fcntl 락은 OS 가 프로세스 종료 시 자동 해제하므로 이중 라이브는 _acquire_lock
    #   이 이미 차단.
    _me = str(os.getpid())
    try:
        if _lock_fd:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            _lock_fd.close()
    except Exception:
        pass
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == _me:
            PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _remove_pid():
    _release_lock()


# ════════════════════════════════════════════════════════════
# Streamlit 대시보드 (hub.py) 자식 프로세스
# ────────────────────────────────────────────────────────────
# 외부 launchd/cron 의 30초 재시도 루프를 폐기하고 daemon 이
# 직접 streamlit 을 자식으로 띄움. 죽으면 백오프 재시작,
# 5회 연속 실패 시 비활성화 + 텔레그램 경고.
# ════════════════════════════════════════════════════════════

_api_proc  = None   # FastAPI uvicorn (포트 9198)
_next_proc = None   # Next.js (포트 9199)

_api_last_start  = 0.0
_next_last_start = 0.0
_api_fail_count  = 0
_next_fail_count = 0
_api_disabled    = False
_next_disabled   = False
_HUB_MAX_FAIL    = 3   # ★ Hub 서버 자동재시작 최대 연속 실패 (★ 사용자 박제 2026-07-06: 재시작 어떤 경우라도 최대 3회)

API_PORT  = int(os.getenv("HUB_API_PORT", "9198"))
NEXT_PORT = int(os.getenv("HUB_PORT",     "9199"))
API_LOG   = LOG_DIR / "api_server.log"
NEXT_LOG  = LOG_DIR / "next_server.log"

# 하위 호환: 기존 ST_PORT (infra_agent 등 참조용)
ST_PORT = NEXT_PORT
ST_LOG  = NEXT_LOG


def _proc_alive(proc) -> bool:
    return proc is not None and proc.poll() is None


def _streamlit_alive() -> bool:
    """Hub 서버(FastAPI + Next.js) 중 하나라도 살아있으면 True."""
    return _proc_alive(_api_proc) or _proc_alive(_next_proc)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_listeners(port: int, exclude_pid: int | None = None) -> list[int]:
    """해당 포트를 LISTEN 중인 PID 목록 (클라이언트 연결 제외)."""
    try:
        res = subprocess.run(
            ["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True,
        )
    except Exception as e:
        log.warning(f"_port_listeners lsof 오류 (port {port}): {e}")
        return []
    pids = []
    for raw in res.stdout.strip().split():
        try:
            pid = int(raw)
        except ValueError:
            continue
        if exclude_pid is not None and pid == exclude_pid:
            continue
        pids.append(pid)
    return pids


def _streamlit_listeners(exclude_pid: int | None = None) -> list[int]:
    """하위 호환: 포트 9199 LISTEN PID 목록."""
    return _port_listeners(NEXT_PORT, exclude_pid=exclude_pid)


def _kill_port(port: int, tag: str, our_proc=None):
    """포트 LISTEN 중인 이전 서버 확실히 종료 (SIGTERM → SIGKILL)."""
    import signal as _sig
    own_pid = our_proc.pid if our_proc and our_proc.poll() is None else None
    targets = _port_listeners(port, exclude_pid=own_pid)
    if not targets:
        return
    for pid in targets:
        try:
            os.kill(pid, _sig.SIGTERM)
            log.info(f"🧹 이전 {tag} PID {pid} 종료(SIGTERM)")
        except ProcessLookupError:
            pass
        except Exception as e:
            log.warning(f"{tag} 종료 오류 PID {pid}: {e}")
    for _ in range(20):
        if not any(_pid_alive(p) for p in targets):
            break
        time.sleep(0.4)
    for pid in targets:
        if _pid_alive(pid):
            try:
                os.kill(pid, _sig.SIGKILL)
                log.warning(f"🧹 SIGTERM 무응답 → SIGKILL {tag} PID {pid}")
            except Exception:
                pass
    for _ in range(12):
        if not _port_listeners(port, exclude_pid=own_pid):
            break
        time.sleep(0.3)
    if _port_listeners(port, exclude_pid=own_pid):
        log.warning(f"⚠️ 포트 {port} LISTEN 잔존 — {tag} 바인딩 실패 가능")


def _kill_orphan_streamlits():
    """Hub 서버 포트 고아 정리 — 하위 호환 함수명 유지."""
    _kill_port(API_PORT,  "FastAPI",  _api_proc)
    _kill_port(NEXT_PORT, "Next.js",  _next_proc)


def _build_env() -> dict:
    """자식 프로세스용 환경변수 — PATH prepend 포함."""
    _EXTRA = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]
    env = os.environ.copy()
    env["PATH"] = ":".join(_EXTRA) + ":" + env.get("PATH", "")
    return env


def _start_api():
    """FastAPI uvicorn 자식 프로세스 시작 (포트 9198)."""
    global _api_proc, _api_last_start
    if _api_disabled or _proc_alive(_api_proc):
        return
    _kill_port(API_PORT, "FastAPI", _api_proc)
    _api_last_start = time.time()
    uvicorn = JARVIS_ROOT / ".venv" / "bin" / "uvicorn"
    bin_ = str(uvicorn) if uvicorn.exists() else "uvicorn"
    cmd = [bin_, "api_server:app", "--host", "127.0.0.1", "--port", str(API_PORT), "--no-access-log"]
    try:
        log_f = open(API_LOG, "a")
        _api_proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=log_f,
            cwd=str(JARVIS_ROOT), start_new_session=True, env=_build_env(),
        )
        log.info(f"🖥  FastAPI 서버 시작 — PID {_api_proc.pid} (port {API_PORT})")
    except Exception as e:
        log.error(f"❌ FastAPI 시작 실패: {e}")
        _report_daemon(e, "_start_api")
        _api_proc = None


def _start_next():
    """Next.js 대시보드 자식 프로세스 시작 (포트 9199)."""
    global _next_proc, _next_last_start
    if _next_disabled or _proc_alive(_next_proc):
        return
    _kill_port(NEXT_PORT, "Next.js", _next_proc)
    _next_last_start = time.time()
    dash_dir = JARVIS_ROOT / "dashboard"
    if not dash_dir.exists():
        log.warning("⚠️ dashboard/ 폴더 없음 — Next.js 스킵")
        return
    npm_candidates = ["/opt/homebrew/bin/npm", "/usr/local/bin/npm", "npm"]
    npm_bin = next((c for c in npm_candidates if Path(c).exists()), "npm")
    cmd = [npm_bin, "run", "dev"]
    try:
        log_f = open(NEXT_LOG, "a")
        _next_proc = subprocess.Popen(
            cmd, stdout=log_f, stderr=log_f,
            cwd=str(dash_dir), start_new_session=True, env=_build_env(),
        )
        log.info(f"🌐 Next.js dev 서버 시작 — PID {_next_proc.pid} (port {NEXT_PORT}, HMR 활성)")
    except Exception as e:
        log.error(f"❌ Next.js 시작 실패: {e}")
        _report_daemon(e, "_start_next")
        _next_proc = None


def _start_streamlit():
    """Hub 서버(FastAPI + Next.js) 시작 — 하위 호환 함수명 유지."""
    _start_api()
    _start_next()


def _stop_streamlit():
    """Hub 서버 종료 — 하위 호환 함수명 유지."""
    global _api_proc, _next_proc
    for proc, tag in [(_api_proc, "FastAPI"), (_next_proc, "Next.js")]:
        if not _proc_alive(proc):
            continue
        try:
            log.info(f"🛑 {tag} 종료 중…")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception as e:
            log.warning(f"{tag} 종료 오류: {e}")
    _api_proc  = None
    _next_proc = None


def _watch_streamlit():
    """메인 루프(60초 주기)에서 호출 — 죽었으면 백오프 재시작."""
    global _api_fail_count, _next_fail_count, _api_disabled, _next_disabled

    # FastAPI 감시
    if not _api_disabled:
        if _proc_alive(_api_proc):
            if time.time() - _api_last_start > 60:
                _api_fail_count = 0
        elif time.time() - _api_last_start >= 30:
            _api_fail_count += 1
            if _api_fail_count >= _HUB_MAX_FAIL:
                _api_disabled = True
                log.error(f"❌ FastAPI {_HUB_MAX_FAIL}회 연속 실패 — 자동 재시작 중단.")
                _report_daemon(RuntimeError(f"FastAPI {_HUB_MAX_FAIL}회 실패"), "_watch_streamlit")
                _send_tg(f"⚠️ FastAPI 서버 {_HUB_MAX_FAIL}회 연속 실패\nlogs/api_server.log 확인 후 daemon 재시작 필요")
            else:
                log.warning(f"⚠️ FastAPI 다운 감지 — 재시작 ({_api_fail_count}/{_HUB_MAX_FAIL})")
                _start_api()

    # Next.js 감시
    if not _next_disabled:
        if _proc_alive(_next_proc):
            if time.time() - _next_last_start > 60:
                _next_fail_count = 0
        elif time.time() - _next_last_start >= 30:
            _next_fail_count += 1
            if _next_fail_count >= _HUB_MAX_FAIL:
                _next_disabled = True
                log.error(f"❌ Next.js {_HUB_MAX_FAIL}회 연속 실패 — 자동 재시작 중단.")
                _report_daemon(RuntimeError(f"Next.js {_HUB_MAX_FAIL}회 실패"), "_watch_streamlit")
                _send_tg(f"⚠️ Next.js 대시보드 {_HUB_MAX_FAIL}회 연속 실패\nlogs/next_server.log 확인 후 daemon 재시작 필요")
            else:
                log.warning(f"⚠️ Next.js 다운 감지 — 재시작 ({_next_fail_count}/{_HUB_MAX_FAIL})")
                _start_next()


# ════════════════════════════════════════════════════════════
# JARVIS02 scheduler 모듈 로드
# ════════════════════════════════════════════════════════════

_sched = None  # scheduler 모듈 레퍼런스

def _load_jarvis01_scheduler():
    global _sched
    try:
        spec = importlib.util.spec_from_file_location(
            "jarvis2_scheduler", WRITER_DIR / "scheduler.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["jarvis2_scheduler"] = mod
        spec.loader.exec_module(mod)
        _sched = mod
        log.info("✅ JARVIS02 scheduler 로드 완료")
        return True
    except Exception as e:
        log.error(f"❌ JARVIS02 scheduler 로드 실패: {e}")
        _report_daemon(e, "_load_jarvis01_scheduler")
        return False


# ════════════════════════════════════════════════════════════
# 에이전트 자동 등록 — JARVIS{NN}_NAME/*_agent.py 스캔
# ════════════════════════════════════════════════════════════
#
# 새 에이전트를 추가하려면:
#   1) JARVIS_ROOT 아래에 폴더 생성: 예) JARVIS03_NEWBOT/
#   2) 그 안에 {name}_agent.py 생성하고 register(scheduler, bus) 함수 정의
#        def register(scheduler, bus):
#            scheduler.add_job(my_job, "cron", hour=9, id="newbot_job",
#                              name="새봇 잡", misfire_grace_time=600)
#            bus.subscribe(bus.EventType.POST_PUBLISHED, on_published)
#   3) 데몬 재시작 — 자동 감지·등록. jarvis_daemon.py 수정 0줄.
#
# JARVIS02/03 는 별도 통합 흐름 (scheduler.py importlib + APScheduler 직접) 으로
# 이미 동작하므로 자동등록 대상에서 제외 (skip_dirs).

def _autoregister_agents(scheduler):
    """JARVIS{NN}_*/*_agent.py 를 스캔해 register(scheduler, bus) 호출."""
    try:
        from shared import bus as _bus
    except Exception as e:
        log.error(f"❌ shared.bus 로드 실패 — 자동등록 건너뜀: {e}")
        _report_daemon(e, "_autoregister_agents.bus_load")
        return 0

    # skip_dirs: register() 호출만 건너뜀 (legacy 통합 흐름 보호).
    # 단 *모듈 import 는 항상* — capability declare() 가 모듈 레벨에서 등록됨.
    skip_dirs = {"JARVIS02_WRITER", "JARVIS03_RADAR"}
    n = 0
    cap_loaded = 0
    try:
        for p in sorted(JARVIS_ROOT.iterdir()):
            if not p.is_dir():
                continue
            if not p.name.startswith("JARVIS"):
                continue
            # ★ 누수 점검 (2026-05-17) — 옛: `agent_files[0]` 으로 *알파벳 첫 번째* 만 로드.
            # JARVIS07_GUARDIAN 처럼 폴더에 *여러 *_agent.py* 가 있는 경우 (eval_agent + guardian_agent)
            # register() 가 있는 파일 (guardian_agent.py) 이 누락. 모든 *_agent.py 전수 로드 + register() 보유 시 호출.
            agent_files = sorted(p.glob("*_agent.py"))
            if not agent_files:
                continue
            for agent_py in agent_files:
                try:
                    mod_name = f"agent_{p.name.lower()}_{agent_py.stem}"
                    spec = importlib.util.spec_from_file_location(mod_name, agent_py)
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)
                    cap_loaded += 1
                    # legacy 흐름은 register() 호출 skip
                    if p.name in skip_dirs:
                        log.info(f"  📋 capability 만 로드 (legacy register skip): {p.name}/{agent_py.name}")
                        continue
                    if not hasattr(mod, "register"):
                        # 여러 _agent.py 중 register 없는 파일은 *capability 만 로드* 정상.
                        # 예: JARVIS07/eval_agent.py 는 평가 헬퍼 — guardian_agent.py 가 register 보유.
                        continue
                    mod.register(scheduler, _bus)
                    log.info(f"🔌 에이전트 자동등록: {p.name}/{agent_py.name}")
                    n += 1
                except Exception as e:
                    log.error(f"❌ {p.name}/{agent_py.name} 등록 실패: {e}")
                    _report_daemon(e, f"_autoregister_agents.{p.name}")
    except Exception as e:
        log.error(f"❌ _autoregister_agents 스캔 실패: {e}")
        _report_daemon(e, "_autoregister_agents")
    log.info(f"🔌 자동등록 에이전트: {n}개 (capability 로드: {cap_loaded}개)")
    return n


# ════════════════════════════════════════════════════════════
# JARVIS00_INFRA — 인프라 에이전트 등록
# ════════════════════════════════════════════════════════════
from JARVIS00_INFRA.infra_agent import register_capability as _infra_register
_infra_register()


# ════════════════════════════════════════════════════════════
# JARVIS03 APScheduler 작업
# ════════════════════════════════════════════════════════════

# job_* 함수는 JARVIS03_RADAR/jobs.py 및 JARVIS00_INFRA/infra_agent.py 로 이관.
# JARVIS04_SCHEDULER/job_registry.py 의 DEFAULT_JOBS 에서 새 경로로 참조.


def _start_scheduler():
    # ★ APScheduler 인스턴스 생성은 JARVIS04 단일 진입점.
    # 데몬은 BackgroundScheduler 직접 생성 금지 (CLAUDE.md 강제 규정).
    try:
        from JARVIS04_SCHEDULER.job_catalog import create_scheduler
        from JARVIS04_SCHEDULER.job_history import attach_listeners
    except ImportError as e:
        log.error(f"⚠️ JARVIS04_SCHEDULER 로드 실패: {e}")
        _report_daemon(e, "_start_scheduler")
        return None

    scheduler = create_scheduler(timezone="Asia/Seoul")
    if scheduler is None:
        log.warning("⚠️ apscheduler 없음 — pip install apscheduler 후 재실행")
        return None

    attach_listeners(scheduler)
    log.info("📅 JARVIS04 scheduler 인스턴스 생성 + listener 부착 완료")

    scheduler.start()
    global _apscheduler
    _apscheduler = scheduler
    log.info("⏰ APScheduler 시작 — 잡 카탈로그는 JARVIS04 register() 가 출력")
    return scheduler


# ════════════════════════════════════════════════════════════
# 시작 시 즉시 실행
# ════════════════════════════════════════════════════════════

def _run_startup_jobs():
    from JARVIS03_RADAR.jobs import job_collect_trends, job_analyzer_fallback
    today = datetime.now().strftime("%Y-%m-%d")
    trend_file = RADAR_DIR / "data" / f"trends_{today}.json"
    if not trend_file.exists():
        log.info("📡 오늘 RADAR 트렌드 수집 없음 → 즉시 시작")
        threading.Thread(target=job_collect_trends, daemon=True).start()
    else:
        log.info(f"✅ 오늘 RADAR 트렌드 이미 수집됨")

    # 시작 즉시 미분석 글 처리 (5분 fallback 기다릴 필요 없음)
    threading.Thread(target=job_analyzer_fallback, daemon=True).start()


# ════════════════════════════════════════════════════════════
# 통합 텔레그램 봇 — JARVIS00_INFRA/bot.py 로 이관
# ════════════════════════════════════════════════════════════
# 모든 봇 함수·승인 딕셔너리·polling 루프는 JARVIS00_INFRA/bot.py 단일 진입점.
# 데몬은 run_bot_polling(shutdown_event) 호출만 담당.
from JARVIS00_INFRA.bot import (
    run_bot_polling, _send_tg, _send_tg_buttons, _answer_callback,
    _PENDING_J00, _PENDING_J00_REACT, _PENDING_J00_PLAN,
)





# [JARVIS02 schedule_mode 스레드 폐기 — 모든 잡이 JARVIS04 으로 이관됨]
# 콜백 함수는 jarvis2_scheduler 모듈에 보존, JARVIS04 가 cron 으로 직접 호출.


# ════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════

def main():
    log.info("=" * 60)
    log.info("🚀 JARVIS 마스터 통합 데몬 v2 시작")
    log.info(f"   루트: {JARVIS_ROOT}")
    log.info(f"   시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    # -1. ★ hang 포렌식 (ERRORS [318] — 2026-07-04): faulthandler + SIGUSR1 스택 덤프.
    #     06:07 사고(메인스레드 무한 파이썬 루프 → GIL 기아 → 스케줄러 정지) 재발 시
    #     keeper 가 SIGUSR1 로 무한루프의 정확한 파이썬 위치를 자동 기록하도록 준비.
    #     가장 먼저 활성화 — 이후 어떤 hang 도 스택 덤프 대상.
    try:
        from JARVIS00_INFRA.infra_agent import enable_hang_forensics
        enable_hang_forensics()
    except Exception as _e_fh:
        log.warning(f"⚠️ hang 포렌식 활성화 실패: {_e_fh}")

    # 0. ★ Layer 0 preflight — 사용자 박제 2026-05-17 (ADR 009)
    #    부팅·환경 검증. 실패 시 sys.exit(1) 으로 *다른 어떤 코드 도달 전* 차단.
    #    7시 사고 (ImportError on collect_theme) 같은 type 영구 차단. 위임 형태 (CLAUDE.md
    #    헌법 — 인프라 단일 진입점). 구현 본체는 JARVIS00_INFRA/preflight.py.
    from JARVIS00_INFRA.preflight import run_preflight
    run_preflight()   # strict=True 기본 — 실패 시 즉시 종료

    # 1. 중복 실행 방지 (OS 레벨 파일 락 — 어떤 경우에도 단일 인스턴스 강제)
    _acquire_lock()

    # 2. JARVIS02 스케줄러 모듈 로드
    _load_jarvis01_scheduler()

    # 3. 통합 텔레그램 봇 스레드 (JARVIS00_INFRA/bot.py)
    bot_thread = threading.Thread(
        target=run_bot_polling, args=(_daemon_shutdown,), daemon=True, name="UnifiedBot"
    )
    bot_thread.start()

    # 4. [JARVIS02 schedule_mode 스레드 폐기 — JARVIS04 가 모든 잡 통합 관리]

    # 5. JARVIS03 APScheduler
    scheduler = _start_scheduler()

    # 5.1 ★ 부팅 즉시 heartbeat 각인 (ERRORS [318] — 2026-07-04): keeper 워치독이
    #     부팅 직후 stale heartbeat 로 오탐·강제킬 하지 않도록 신선한 신호를 먼저 남김.
    #     이후 infra_heartbeat interval 잡(60초)이 지속 갱신.
    try:
        from JARVIS00_INFRA.infra_agent import touch_heartbeat, quiet_heartbeat_logs
        touch_heartbeat()
        quiet_heartbeat_logs()   # heartbeat 잡 실행 로그(60초 주기) 억제 — daemon.log 오염 방지
    except Exception as _e_hb:
        log.warning(f"⚠️ heartbeat 초기화 실패: {_e_hb}")

    # 5.5 Streamlit 대시보드 — 고아 정리 후 시작
    _kill_orphan_streamlits()
    _start_streamlit()

    # 5.7 이벤트 버스 dispatch cursor 초기화 + 빠른 디스패치 스레드 + 에이전트 자동 등록
    try:
        from shared import bus
        bus.init_dispatch_cursor()
        bus.start_fast_dispatch()
        _autoregister_agents(scheduler)
    except Exception as e:
        log.error(f"⚠️ 이벤트 버스/에이전트 자동등록 실패: {e}")
        _report_daemon(e, "main.bus_autoregister")

    # 5.8 ProactiveMonitor 부팅 자가진단 — 제거됨.
    # 코드 자가 진단·수정은 JARVIS07_GUARDIAN/auto_repair.py 담당 (Sonnet 5):
    #   발행 세트 callback 내 선행 실행 — 06:30 run_self_repair_then_economic / 16:00 run_self_repair_then_theme

    # 6. 시작 시 즉시 실행 (오늘 트렌드 없으면)
    _run_startup_jobs()

    # 7. 텔레그램 시작 알림 — ★ 스케줄은 DEFAULT_JOBS(SSOT)에서 파생 (하드코딩 금지,
    #    사용자 박제 2026-07-04): DEFAULT_JOBS 를 바꾸면 이 메시지가 자동으로 따라온다.
    from JARVIS04_SCHEDULER.job_registry import cron_times as _cron_times
    _econ   = " · ".join(_cron_times(job_id_prefix="j01_economic_post")) or "?"
    _trends = " · ".join(_cron_times(job_id_prefix="radar_trends"))       or "?"
    _perf   = " · ".join(_cron_times(job_id_prefix="radar_perf"))         or "?"
    _send_tg(
        f"🚀 *JARVIS 통합 데몬 v2 시작*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📰 JARVIS02 Market Signal: {_sched.SCHEDULE_HOURS if _sched else '?'}시\n"
        f"📰 경제 브리핑: 매일 {_econ}\n"
        f"📡 JARVIS03 트렌드 수집: 매일 {_trends}\n"
        f"📊 성과 수집: 매일 {_perf}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"명령어: /help"
    )

    log.info("\n✅ 모든 컴포넌트 시작 완료. Ctrl+C로 종료.")
    log.info("   - 통합 텔레그램 봇 (JARVIS01 ReAct + JARVIS02 명령어 + JARVIS03 인라인 버튼)")
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS as _dj
        _n_jobs = f"{len(_dj)}개"
    except Exception:
        _n_jobs = "DEFAULT_JOBS"
    log.info(f"   - JARVIS04 SCHEDULER — 모든 잡 통합 관리 ({_n_jobs} default + 일일 브리핑)")

    # 8. 메인 루프 — 스레드 감시 + 종료 이벤트 대기
    try:
        while not _daemon_shutdown.is_set():
            # 봇 스레드 재시작 감시
            if not bot_thread.is_alive() and not _daemon_shutdown.is_set():
                log.warning("⚠️ 통합 봇 스레드 종료 감지 → 재시작")
                bot_thread = threading.Thread(
                    target=run_bot_polling, args=(_daemon_shutdown,), daemon=True, name="UnifiedBot"
                )
                bot_thread.start()
            # Streamlit 자식 프로세스 감시
            _watch_streamlit()
            # 이벤트 버스 fallback dispatch — fast dispatch 스레드 누락분 복구 (5분 주기)
            try:
                from shared import bus
                _n = bus.dispatch_pending(limit=200)
                if _n:
                    log.debug(f"bus fallback dispatch: {_n}건")
            except Exception as _e_disp:
                log.error(f"bus dispatch 오류: {_e_disp}")
                _report_daemon(_e_disp, "main_loop.bus_dispatch")
            _daemon_shutdown.wait(timeout=300)
    except KeyboardInterrupt:
        log.info("\n🛑 Ctrl+C — JARVIS 데몬 종료")
    finally:
        _daemon_shutdown.set()
        if _sched:
            _sched._shutdown = True
        if scheduler:
            scheduler.shutdown(wait=False)
        try:
            from shared import bus
            bus.stop_fast_dispatch()
        except Exception:
            pass
        _stop_streamlit()
        _remove_pid()
        _send_tg("🛑 JARVIS 통합 데몬 종료")
        log.info("🛑 JARVIS 통합 데몬 종료 완료")


if __name__ == "__main__":
    main()
