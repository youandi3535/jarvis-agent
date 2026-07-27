#!/usr/bin/env python3
"""
Market Signal 자동 스케줄러 v4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- 원고 1번 생성 → 3개 플랫폼 순서대로 발행
- result_{theme}.json 으로 플랫폼별 성공여부 추적
- 실패한 플랫폼만 즉시 재시도 (최대 3회)
- 3회 실패 시 텔레그램 알림 후 다음 플랫폼으로
- 타임아웃 없음
- 텔레그램 양방향 제어

[텔레그램 명령어]
  /status           진행 현황 확인
  /next             다음 테마 즉시 실행
  /stop             스케줄러 일시정지
  /resume           스케줄러 재개
  /quit             스케줄러 완전 종료 (프로세스 종료)
  /run 테마명       특정 테마 즉시 실행
  /failed           실패 목록 확인
  /retry            실패 목록 전체 재시도
  /success          실패 목록 전체를 성공으로 표시
  /success 테마명   특정 테마를 성공으로 표시
  (/help는 watchdog.py 에서 처리)

[터미널 사용법]
  python scheduler.py               # 스케줄 모드
  python scheduler.py --next        # 다음 테마 즉시 실행
  python scheduler.py --status      # 진행 현황 확인
  python scheduler.py --run 반도체  # 특정 테마 실행
  python scheduler.py --reset       # 진행 상황 초기화
"""
import os, sys, time, json, subprocess, threading
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR      = Path(__file__).parent
LOGS_DIR      = BASE_DIR / 'logs'
THEME_FILE    = BASE_DIR / 'theme_list.txt'
PROGRESS_FILE = BASE_DIR / 'scheduler_progress.json'
LOG_FILE      = BASE_DIR / 'logs' / 'scheduler.log'
LOCK_FILE     = BASE_DIR / '.posting.lock'
PYTHON        = sys.executable

sys.path.insert(0, str(BASE_DIR.parent))  # shared/ 접근


def _parent_subproc_timeout() -> int:
    """경제 발행 subprocess 부모 백스톱 — 자식 guard_main(2*BLOG+600) 보다 *크게* 파생.

    ★ 2026-07-24 P4: 종전 하드코딩 3600(60분) 은 자식 guard_main 백스톱(5400) 보다 작아
      ① 자식의 협조종료(guard_main→os._exit·chrome 정리·GUARDIAN 보고) 전에 부모가 먼저 SIGKILL
      ② 2 플랫폼×BLOG(4800) > 3600 이라 2번째 플랫폼을 발행 도중 강제종료 — 계층 역전 버그였다.
      부모>자식 으로 복원하되 파생(하드코딩 금지, ② 동적설계) — BLOG 를 낮추면(P5) 자동 동반 하락.
      ★ 이 값은 정상 완료 시간을 늘리지 않는다 — 자식 자체 가드(freeze 300s·플랫폼 BLOG·guard_main
      2*BLOG+600)가 먼저 발화하는 '절대 안 터지는 최후 안전판'일 뿐. 정상 30분 수렴은 P1·P2 담당.
    """
    from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC as _b
    return 2 * _b + 900   # 자식 guard_main(2*BLOG+600) + 300 여유

SCHEDULE_HOURS      = [21]   # ★ 테마 발행 시간 (표시용 — 실제 트리거는 DEFAULT_JOBS j01_theme_post_21). 16→21 (2026-07-05)
MAX_RETRY           = 3
TG_TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")

_paused         = False
_shutdown       = False
_posting_lock   = threading.Lock()


# ══════════════════════════════════════════
#  포스팅 락 관리
# ══════════════════════════════════════════

def _clear_all_cookies(label: str) -> None:
    """발행 전 기존 쿠키·캐시 전체 초기화 — 매번 새 로그인으로 신선한 쿠키 보장.

    삭제 대상:
      - naver_cookies.pkl (쿠키 파일)
      - TS_COOKIE 환경변수 (메모리 초기화, .env는 갱신 시 자동 업데이트)
      - Chrome 캐시 폴더 (Cache / Code Cache / GPUCache / Service Worker)
        → 로그인 데이터(Cookies, Login Data) 는 보존
    """
    import os as _os, shutil as _shutil

    cleared = []

    # 1) 네이버 쿠키 파일 삭제
    _naver_cookie = BASE_DIR / "naver_cookies.pkl"
    if _naver_cookie.exists():
        try:
            _naver_cookie.unlink()
            cleared.append("네이버 쿠키 파일")
        except Exception as _e:
            log(f"⚠️ [{label}] 네이버 쿠키 파일 삭제 실패: {_e}")

    # 2) 티스토리 TS_COOKIE 환경변수 초기화 (.env 보존 — 갱신 성공 시 자동 업데이트)
    if _os.environ.get("TS_COOKIE"):
        _os.environ.pop("TS_COOKIE", None)
        cleared.append("TS_COOKIE 환경변수")

    # 3) 네이버 Chrome 캐시 폴더 삭제 (로그인·세션 데이터는 보존)
    _chrome_cache_dirs = [
        BASE_DIR / "chrome_profile" / "naver" / "Default" / "Cache",
        BASE_DIR / "chrome_profile" / "naver" / "Default" / "Code Cache",
        BASE_DIR / "chrome_profile" / "naver" / "Default" / "GPUCache",
        BASE_DIR / "chrome_profile" / "naver" / "Default" / "Service Worker",
    ]
    for _cdir in _chrome_cache_dirs:
        if _cdir.exists():
            try:
                _shutil.rmtree(_cdir)
                cleared.append(f"Chrome:{_cdir.name}")
            except Exception as _e:
                log(f"⚠️ [{label}] Chrome 캐시 삭제 실패 ({_cdir.name}): {_e}")

    if cleared:
        log(f"🗑️ [{label}] 쿠키·캐시 초기화: {', '.join(cleared)}")
    else:
        log(f"ℹ️ [{label}] 삭제할 쿠키·캐시 없음")


def _lock_acquire(who: str) -> bool:
    """포스팅 잠금 획득. 이미 진행 중이면 False 반환.

    ★ ERRORS [136] 사용자 박제 2026-05-17 — cross-process 락 누수 차단:
    - 기존: threading.Lock 만 검사 → 호스트 직접 호출 시 새 프로세스 = 새 Lock = 항상 acquire 성공
    - 수정: 1) 외부 프로세스 락 *먼저* 검사 2) LOCK_FILE 은 O_EXCL atomic 생성
    """
    # ★ 1단계: 외부 프로세스 락 우선 확인 (다른 Python 프로세스가 발행 중인지)
    if _is_locked_externally():
        try:
            owner = LOCK_FILE.read_text(encoding='utf-8').split('\n')[0]
        except Exception:
            owner = "외부 프로세스"
        log(f"⚠️ 잠금 실패 [{who}]: 외부 프로세스 [{owner}] 진행 중 → 건너뜀")
        send_telegram(f"⚠️ [{who}] 건너뜀\n외부 프로세스 [{owner}] 진행 중.")
        return False

    # ★ 2단계: 같은 프로세스 내 threading.Lock
    if not _posting_lock.acquire(blocking=False):
        try:
            owner = LOCK_FILE.read_text(encoding='utf-8').split('\n')[0]
        except Exception:
            owner = "다른 작업"
        log(f"⚠️ 잠금 실패 [{who}]: [{owner}] 진행 중 → 건너뜀")
        send_telegram(f"⚠️ [{who}] 건너뜀\n현재 [{owner}] 진행 중입니다.")
        return False

    # ★ 3단계: LOCK_FILE atomic 생성 (O_CREAT|O_EXCL — 이미 있으면 실패).
    #          위 _is_locked_externally() 와 사이 race condition 차단.
    _content = f"{who}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\nPID:{os.getpid()}"
    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(_content)
    except FileExistsError:
        # race: 두 프로세스가 동시에 _is_locked_externally() 통과 → 한쪽만 file 생성 성공
        try:
            _posting_lock.release()
        except RuntimeError:
            pass
        try:
            owner = LOCK_FILE.read_text(encoding='utf-8').split('\n')[0]
        except Exception:
            owner = "race condition"
        log(f"⚠️ 잠금 실패 [{who}]: race 감지 — [{owner}] 진행 중 → 건너뜀")
        send_telegram(f"⚠️ [{who}] 건너뜀\nrace condition — [{owner}] 진행 중.")
        return False
    return True


def _lock_release():
    """포스팅 잠금 해제."""
    LOCK_FILE.unlink(missing_ok=True)
    try:
        _posting_lock.release()
    except RuntimeError:
        pass


def _is_locked_externally() -> bool:
    """외부 프로세스(수동 실행 등)가 락을 점유 중인지 확인."""
    if not LOCK_FILE.exists():
        return False
    # 3시간 이상 된 락은 비정상 종료로 간주 → 자동 제거
    if time.time() - LOCK_FILE.stat().st_mtime > 10800:
        LOCK_FILE.unlink(missing_ok=True)
        return False
    try:
        content = LOCK_FILE.read_text(encoding='utf-8')
        pid_line = [l for l in content.splitlines() if l.startswith('PID:')]
        if pid_line:
            pid = int(pid_line[0].replace('PID:', '').strip())
            if pid == os.getpid():
                return False  # 나 자신이 소유한 락
            # ★ 소유 PID 생존 확인 — 죽은 프로세스의 스테일 락 즉시 제거.
            #   (비정상 종료/강제 kill 시 mtime 3h 룰만으론 최대 3시간 발행이 막히는 결함 차단)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                LOCK_FILE.unlink(missing_ok=True)
                return False
            except PermissionError:
                pass  # 살아있으나 다른 소유자 — 점유로 간주
    except Exception:
        pass
    return True


# ══════════════════════════════════════════
#  로그
# ══════════════════════════════════════════

def log(msg: str):
    ts   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ══════════════════════════════════════════
#  텔레그램
# ══════════════════════════════════════════

def send_telegram(msg: str):
    from shared.notify import send_tg
    send_tg(msg)


# ★ 텔레그램 폴링(get_telegram_updates)은 폐지 — 호출자 0인 죽은 코드였고, 02 안의 마지막
#   `requests` 사용처였다. 봇 폴링은 데몬 단일 루프(jarvis_daemon)가 단독 수행한다.


# ══════════════════════════════════════════
#  진행 상황
# ══════════════════════════════════════════

def load_themes():
    with open(THEME_FILE, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {'index': 0, 'done': [], 'failed': [], 'platform_status': {}}


def save_progress(p: dict):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(p, f, ensure_ascii=False, indent=2)


def get_result_path(theme: str) -> Path:
    safe = theme.replace("/", "_").replace(" ", "_")
    return LOGS_DIR / f"result_{safe}.json"


# ★ 발행 글 글자수 크롤링은 09 (사용자 박제 2026-07-23) — 밖에 나가 HTML 을 받아오면
#   그건 수집이다. `JARVIS09_COLLECTOR.published_post_kor_counts(theme)` 단독.


def load_platform_result(theme: str) -> dict:
    path = get_result_path(theme)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {"naver": False, "tistory": False}



def clear_theme_cache(theme: str):
    """테마 원고 캐시 + 결과 파일 삭제"""
    import glob
    safe = theme.replace("/", "_").replace(" ", "_")
    for f in glob.glob(str(LOGS_DIR / f"report_{safe}_*.txt")):
        try:
            os.remove(f)
        except Exception as _e:
            try:
                from JARVIS07_GUARDIAN.error_collector import report as _gr
                _gr("scheduler", _e, module="scheduler", func_name="clear_theme_cache")
            except Exception:
                pass
    result_path = get_result_path(theme)
    if result_path.exists():
        result_path.unlink()


# ══════════════════════════════════════════
#  테마 전체 실행
# ══════════════════════════════════════════

def _spawn_publisher(label: str, cmd: list, log_stem: str,
                     *, extra_env: dict | None = None) -> tuple:
    """★ 발행 스크립트 subprocess 실행 — **경제·테마 공통 단일 경로** (사용자 박제 2026-07-25).

    **왜 subprocess 로 통일했나 (실행모델 통일)**
      종전엔 경제=subprocess, 테마=데몬 내부 직접호출로 *두 실행모델* 이었다. 그 결과
        · watchdog `os._exit(WATCHDOG_KILL_RC)` 강제종료가 테마에선 무력 (파이썬은 스레드를
          안전하게 죽일 수 없다) → 테마가 멈추면 데몬째 재시작 외에 방법이 없었다
        · 자가수정한 코드가 테마에선 *데몬 재시작 전까지 무효* (import 캐시)
        · 크로스커팅 관심사(LLM 우선권·락·관측성)를 두 번 구현해야 했고, 실제로 한쪽만
          새는 사고가 반복됐다 (배경 LLM 차단이 경제 발행 중에만 무력화된 건 등)
      발행은 오래 걸리고(플랫폼당 ~40분) 불안정한 외부 자원(Chrome/Selenium)을 쓰는 작업이라
      업계 표준(워커 프로세스 격리)대로 **격리 쪽으로 통일** 한다.

    Returns: (returncode, result_dict|None, logpath)
      result_dict 는 자식이 `JARVIS_EP_RESULT_FILE` 에 남긴 JSON (없으면 None).
    """
    import tempfile
    import sys as _sys
    from datetime import datetime as _dt

    _res_fd, _res_path = tempfile.mkstemp(suffix=".json", prefix="ep_result_")
    os.close(_res_fd)
    _env = dict(os.environ)
    _env["JARVIS_EP_RESULT_FILE"] = _res_path
    # ★ 로그 유실 방지 (ERRORS [289]): 파일 리다이렉트 시 블록 버퍼링 → SIGKILL 시 마지막
    #   수 분(발행 단계) 로그 통째 유실. 무버퍼 강제.
    _env["PYTHONUNBUFFERED"] = "1"
    if extra_env:
        _env.update({k: str(v) for k, v in extra_env.items()})

    _ts = _dt.now().strftime('%Y%m%d_%H%M%S')
    _logpath = BASE_DIR / 'logs' / f'{log_stem}_{_ts}.log'
    _logpath.parent.mkdir(parents=True, exist_ok=True)
    _is_tty = _sys.stdout.isatty() or bool(os.environ.get("JARVIS_VERBOSE"))

    rc = -1
    try:
        with open(_logpath, 'w', encoding='utf-8') as _lf:
            if _is_tty:
                # 터미널 직접 실행 — 로그파일 + 터미널 동시 출력
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=_env)
                try:
                    for _line in proc.stdout:
                        _decoded = _line.decode("utf-8", errors="replace")
                        _sys.stdout.write(_decoded)
                        _sys.stdout.flush()
                        _lf.write(_decoded)
                    proc.wait(timeout=_parent_subproc_timeout())
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                rc = proc.returncode
            else:
                rc = subprocess.run(cmd, timeout=_parent_subproc_timeout(), stdout=_lf,
                                    stderr=subprocess.STDOUT, env=_env).returncode
    except subprocess.TimeoutExpired:
        log(f"⏱ {label} 부모 타임아웃 — 자식 종료 (로그: {_logpath.name})")
        rc = -9

    result = None
    try:
        result = json.loads(Path(_res_path).read_text(encoding="utf-8"))
    except Exception:
        pass
    finally:
        try:
            Path(_res_path).unlink(missing_ok=True)
        except Exception:
            pass
    return rc, result, _logpath


def run_theme(theme: str, gate_feedback: dict | None = None) -> dict:
    """테마 발행 — ★ subprocess 실행 (경제와 동일 실행모델, 사용자 박제 2026-07-25).

    종전엔 데몬 안에서 `run_all_themes` 를 직접 호출했다(=경제와 다른 모델). 통일 사유는
    `_spawn_publisher` docstring 참조 — watchdog 강제종료·최신코드 반영·장애 격리.
    """
    log(f"▶ 테마 시작: {theme}")
    log("=" * 50)

    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 데몬 재시작으로 인터프리터가 종료 단계면 발행을 *시작하지 않고* 연기.
    # (호출자 run_next/_run_one_theme 도 종료 중이면 진행상태를 실패로 기록하지 않음)
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log(f"⏸ [{theme}] 인터프리터 종료 중(데몬 재시작) — 발행 연기, 재시작 후 재시도")
        return {"naver": False, "tistory": False}

    # 캐시 초기화 (새 테마 시작 시)
    clear_theme_cache(theme)

    send_telegram(f"🚀 [{theme}] 작성 시작\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── 발행 실행 (subprocess — 경제와 동일 경로) ────────────────
    log(f"  ▶ 테마 발행 subprocess 실행 (trend_theme_writer)")
    try:
        _cmd = [PYTHON, str(BASE_DIR / 'trend_theme_writer.py'), theme]
        _extra = {}
        if gate_feedback:
            # 재시도 이어받기 — 직전 차단사유를 자식에게 전달 (프로세스 경계를 넘는 매체 = env)
            _extra["JARVIS_GATE_FEEDBACK"] = json.dumps(gate_feedback, ensure_ascii=False)
        _rc, result, _logpath = _spawn_publisher(f"테마 발행 [{theme}]", _cmd,
                                                 "theme", extra_env=_extra)
        if result is None:
            # 결과 파일이 없다 = 자식이 결과를 못 남기고 죽음 (freeze kill·크래시)
            log(f"  ❌ 테마 결과 파일 없음 (returncode={_rc}, 로그: {_logpath.name})")
            result = {"naver": {"success": False}, "tistory": {"success": False},
                      "data_empty": False}
        results = {
            "naver":   result.get("naver",   {}).get("success", False),
            "tistory": result.get("tistory", {}).get("success", False),
        }
        _result_data_empty = result.get("data_empty", False)
        # ★ 인프라 스로틀 지속(rank8 deferred) — 코드 결함 아님, 다음 회차 자연 재시도.
        #   harness 가 이미 판정을 내려 텔레그램까지 보냈는데(run_all_themes 내부) 여기서
        #   "success=False" 로만 뭉개면 GUARDIAN 이 이 구분을 잃고 불필요한 Tier-2 SDK
        #   세션(최대 10분)을 낭비한다 — 아래 GUARDIAN 트리거 조건에서 제외 대상으로 사용.
        _result_deferred = {"naver": result.get("naver_deferred", False),
                             "tistory": result.get("tistory_deferred", False)}
        # ★ 차단사유 — GUARDIAN 재시도가 같은 테마로 보완할 때 물려줄 근거 (2026-07-25)
        _result_issues = result.get("issues") or {}
        # ★ 인터프리터 종료 레이스 (ERRORS [362]) — 발행이 시작조차 못 함(연기).
        #   "글자수 실패" 텔레그램·GUARDIAN·실패 오기록 전부 스킵하고 즉시 반환 → 재시작 후 재시도.
        if result.get("shutdown_deferred"):
            log(f"⏸ [{theme}] 발행 연기(데몬 재시작) — 보고·GUARDIAN·진행기록 스킵, 재시작 후 재시도")
            return {"naver": False, "tistory": False}
    except Exception as _tw_e:
        log(f"  ❌ trend_theme_writer 실행 예외: {_tw_e}")
        import traceback; traceback.print_exc()
        results = {"naver": False, "tistory": False}
        _result_data_empty = False
        _result_deferred = {"naver": False, "tistory": False}
        _result_issues = {}

    log(f"  📋 1차 결과: 네이버={'✅' if results.get('naver') else '❌'} | "
        f"티스토리={'✅' if results.get('tistory') else '❌'}")

    # ── 2차 재시도 제거 (ERRORS [160] — harness 가 max_attempts 내부 재시도를 이미 소진.
    #   그때 재시도가 호출하던 legacy run_naver/tistory_theme() 는 2026-07-23 **삭제됨**) ──

    # ── 최종 결과 ────────────────────────────────────────────
    ok   = [k for k, v in results.items() if v]
    fail = [k for k, v in results.items() if not v]
    log("=" * 50)

    all_ok = all(results.values())
    from JARVIS09_COLLECTOR import published_post_kor_counts
    kor_map = published_post_kor_counts(theme)
    def _fmt(key):
        n = kor_map.get(key, 0)
        return f"{n:,}자" if (results.get(key) and n > 0) else ("-" if results.get(key) else "실패")
    send_telegram(
        f"{'🎉' if all_ok else '⚠️'} [{theme}] 완료\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 성공: {', '.join(ok) if ok else '없음'}\n"
        f"❌ 실패: {', '.join(fail) if fail else '없음'}\n"
        f"📝 네이버 글자수: {_fmt('naver')}\n"
        f"📝 티스토리 글자수: {_fmt('tistory')}\n"
        f"시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # ── GUARDIAN 자동 대응 — harness 소진 후 코드 수정 + 재발행 ──────
    # ★ data_empty 시 GUARDIAN 스킵 (ERRORS [168][174] 반복 박제 — 동일 테마 재시도 = 동일 실패 반복)
    # 종목 데이터 0개는 코드 버그가 아닌 데이터 부재 → 테마 교체가 정답, 코드 수정 불필요
    # ★ 인프라 스로틀 지속(deferred) 플랫폼도 GUARDIAN 트리거에서 제외 — harness 가 이미
    #   "코드 결함 아님, 다음 회차 자연 재시도"로 판정한 건이다. 여기서 걸러내지 않으면
    #   incident_responder._classify() 가 일반 텍스트("harness max_attempts 소진")만 보고
    #   transient 로 인식 못 해(로컬 _TRANSIENT_KEYWORDS 에 "인프라 스로틀" 없음) code_bug/
    #   unknown 경로로 빠져 불필요한 Tier-2 SDK 세션(최대 10분)을 매번 낭비한다.
    _guardian_fail = [k for k in fail if not _result_deferred.get(k, False)]
    if _result_deferred.get("naver") or _result_deferred.get("tistory"):
        _deferred_list = [k for k in fail if _result_deferred.get(k, False)]
        log(f"⏸ [THEME] 인프라 스로틀 지속 플랫폼 GUARDIAN 스킵(다음 회차 자연 재시도): {_deferred_list}")
    if _guardian_fail and not _result_data_empty:
        try:
            from JARVIS07_GUARDIAN.incident_responder import respond_in_background
            _err_ctx = (
                f"테마 포스팅 실패: theme={theme}, failed_platforms={_guardian_fail} (harness max_attempts 소진)"
            )

            # ★ 재발행 retry_fn — 코드 수정 후 즉시 재발행 (harness 통과 보장)
            # theme runner 는 run_radar_top_theme() 를 reload 후 재호출 (harness 내장 함수)
            # ★ 재시도 이어받기 (2026-07-25 — 경제와 동일 규약, ③ 모든 글 적용):
            #   막힌 *같은 테마* + 직전 차단사유를 물려준다. 종전엔 카탈로그에서 임의 재선정.
            _resume_fb = {p: list((_result_issues or {}).get(p) or []) for p in _guardian_fail}

            def _make_theme_retry():
                """수정된 코드로 즉시 재발행. importlib.reload → harness 5-Layer 통과."""
                _fail_platforms = list(_guardian_fail)
                _rt, _rf = theme, dict(_resume_fb)
                def _retry():
                    import importlib, sys as _sys
                    # ★ 의존성 순서 정렬 (ERRORS [222][224] 박제)
                    # draft_writer → tistory_html_writer → theme_html_writer/draft_processor → trend_theme_writer
                    # 순서 어기면 theme_html_writer reload 시 OLD tistory_html_writer 캐시에서 _stocks_text 못 찾아 실패
                    _ordered_reload = [
                        "draft_writer",
                        "tistory_html_writer",
                        "theme_html_writer",
                        "draft_processor",
                        "economic_poster",
                        "trend_theme_writer",
                    ]
                    for _kw in _ordered_reload:
                        for _k in list(_sys.modules.keys()):
                            if _kw in _k:
                                try:
                                    importlib.reload(_sys.modules[_k])
                                except Exception:
                                    pass
                    # run_radar_top_theme 은 harness run_action() 래핑 → 검증 순환 보장
                    result = run_radar_top_theme(resume_theme=_rt, resume_feedback=_rf)
                    return bool(result)
                return _retry

            # 실패 플랫폼 수만큼 retry_fn 등록 (incident_responder 가 플랫폼별 호출)
            _retry_fns = {p: _make_theme_retry() for p in _guardian_fail}
            respond_in_background("theme", _guardian_fail, _err_ctx, _retry_fns, theme=theme)
            log(f"🛡️ GUARDIAN incident_responder 트리거됨: theme={theme}, fail={_guardian_fail}")
        except Exception as _ire:
            log(f"⚠️ GUARDIAN 트리거 실패: {_ire}")
    elif fail and _result_data_empty:
        log(f"⚠️ [THEME] 종목 데이터 없음 — GUARDIAN 스킵 (테마 교체로 대응 필요): theme={theme}")

    return results


# ══════════════════════════════════════════
#  다음 테마 실행
# ══════════════════════════════════════════

def run_next():
    if _paused:
        send_telegram("⏸ 일시정지 상태입니다.\n재개하려면 /resume")
        return

    # 외부 프로세스(수동 실행)가 락을 점유 중인지 먼저 확인
    if _is_locked_externally():
        log("⚠️ 외부 포스팅 작업 진행 중 — 스케줄 실행 건너뜀")
        return

    themes = load_themes()
    p      = load_progress()
    idx    = p['index']

    if idx >= len(themes):
        log("🎉 모든 테마 완료!")
        send_telegram("🎉 Market Signal\n전체 테마 완료!\n처음부터 다시 시작합니다.")
        p['index'] = 0
        save_progress(p)
        return

    theme = themes[idx]

    # 이미 모든 플랫폼 성공한 테마면 건너뜀 (수동 실행으로 완료된 경우 대비)
    existing = load_platform_result(theme)
    if all(existing.values()):
        log(f"⏭️ [{theme}] 이미 완료 — 건너뜀 (수동 실행 완료)")
        p['index'] = idx + 1
        if theme not in p.get('done', []):
            p['done'].append(theme)
        save_progress(p)
        return

    if not _lock_acquire(f"테마: {theme}"):
        return

    try:
        log(f"📋 [{idx+1}/{len(themes)}] {theme}")
        results = run_theme(theme)

        # ★ 인터프리터 종료 레이스 (ERRORS [362]) — 발행 미시작(연기).
        #   index 전진·done/failed 기록 금지 → 재시작 후 같은 테마 재시도 보장.
        from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
        if _isd() and not any(results.values()):
            log(f"⏸ [{theme}] 발행 연기(데몬 재시작) — 진행상태 미기록, 재시작 후 재시도")
            return

        p['index'] = idx + 1
        if 'platform_status' not in p:
            p['platform_status'] = {}
        p['platform_status'][theme] = results

        if all(results.values()):
            p['done'].append(theme)
        else:
            p['failed'].append(theme)

        save_progress(p)
        log(f"📊 진행: {idx+1}/{len(themes)} | 완료: {len(p['done'])} | 실패: {len(p['failed'])}")
    finally:
        _lock_release()


def _theme_exclude() -> set:
    """테마 선정 제외 집합 = JARVIS02 발행 상태(발행완료 365일 + progress done).

    ★ 선정 자체는 JARVIS03(RADAR) theme_picker 가 하고(역할 이관 2026-07-18), 발행 상태는
    JARVIS02 의 것이므로 여기서 만들어 03 에 넘긴다(03→02 역참조 회피).
    """
    from shared.db import get_recent_published_themes
    published = {r["theme"] for r in get_recent_published_themes(days=365)}
    return published | set(load_progress().get("done", []))


def select_top_theme() -> str | None:
    """★ 테마 선정 — JARVIS03(RADAR) theme_picker 위임(역할 이관 2026-07-18). 선계산 잡(20:00)용.
    선계산이 이 결과를 pin → 21:00 발행(run_radar_top_theme)이 그 pin 을 우선 사용(캐시 히트)."""
    from JARVIS03_RADAR.theme_picker import select_theme
    return select_theme(exclude=_theme_exclude())


# ★ 선계산(precollect) 잡은 02 에 없다 (사용자 박제 2026-07-23) —
#   `JARVIS09_COLLECTOR.precollect.job_precollect_{theme,economic}` 이 잡 진입점.
#   02 는 `select_top_theme()` 로 *어떤 테마가 미발행인지* 만 알려준다 (발행 상태 = 02 소유).


def run_radar_top_theme(resume_theme: str = "", resume_feedback: dict | None = None):
    """★ 네이버 금융 공식 테마 카탈로그 → 미발행 테마 임의 선정 → 발행.

    resume_theme/resume_feedback: ★ GUARDIAN 재시도용 — *막힌 테마를 이어받아 보완*
      (사용자 박제 2026-07-25, 경제 `economic_poster.run(resume=)` 과 동일 규약).
      종전엔 재시도가 카탈로그에서 *임의 재선정* 했다 — 차단된 테마는 published/done 에
      안 들어가 후보에 남지만 200여 개 중 random 이라 같은 테마를 다시 잡을 확률이 사실상 0.
      수집·대본을 통째로 버리고, 무엇이 부족했는지도 함께 버려졌다.

    테마주 주제 선정 원칙: 오늘 트렌드 키워드(RADAR)가 아니라 네이버 금융 공식 테마
    목록을 전부 로드한 뒤, 지금껏 발행하지 않은 테마를 임의로 하나 골라 발행한다.
    실패 시 최대 3개 후보까지 재선정.
    """
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log("⏸ [CATALOG] 인터프리터 종료 중(데몬 재시작) — 발행 연기, 재시작 후 재시도")
        return
    if _paused:
        send_telegram("⏸ 일시정지 상태입니다.\n재개하려면 /resume")
        return
    if _is_locked_externally():
        log("⚠️ 외부 포스팅 작업 진행 중 — 스케줄 실행 건너뜀")
        return

    # ── 1. 테마 카탈로그 로드 — JARVIS03(RADAR) theme_picker (선정 역할 이관 2026-07-18) ──
    from JARVIS03_RADAR.theme_picker import theme_catalog, available_themes, pick_theme
    catalog = theme_catalog()
    if not catalog:
        log("⚠️ 네이버 금융 테마 카탈로그 비어있음/로드 실패 — 순차 실행 폴백")
        run_next()
        return

    # ── 2. 기발행 테마 조회 (DB 전체 이력 기준, 365일) — JARVIS02 발행 상태 ──
    from shared.db import get_recent_published_themes
    published = {r["theme"] for r in get_recent_published_themes(days=365)}

    p        = load_progress()
    done_set = set(p.get("done", []))

    # ── 3. 미발행 공식 테마 필터링 — 선정(필터)은 JARVIS03, 제외집합만 JARVIS02 제공 ──
    available = available_themes(exclude=published | done_set, catalog=catalog)

    if not available:
        # ★ 전체 소진 → done/failed 리셋 후 그 시점 카탈로그 전체로 재시작 (동적 — 하드코딩 없음)
        log(f"🎉 네이버 금융 공식 테마 전체 발행 완료 ({len(catalog)}개) — 처음부터 다시 시작")
        send_telegram(
            f"🎉 네이버 금융 공식 테마 전체 발행 완료!\n"
            f"({len(catalog)}개 소진) 처음부터 다시 시작합니다."
        )
        p["done"] = []
        p["failed"] = []
        save_progress(p)
        available = list(catalog.keys())   # 재로드 시점 카탈로그 반영 (265개든 270개든)

    log(f"📊 카탈로그 현황: 전체 {len(catalog)}개 · 미발행 {len(available)}개")

    # ── 4. 임의 선정 → 최대 3회 폴백 ────────────────────────────────────
    # ★ 선계산 고정 테마 우선 (사용자 박제 2026-07-18): 20:00 선계산 잡이 고정·선수집한 테마가
    #   있으면 첫 시도에 그 테마를 써서 캐시 히트(발행창 추출 LLM 0회). 없으면 기존 random 선정.
    _pinned = None
    # ★ 재시도 이어받기가 최우선 — 선계산 고정보다 앞선다 (막힌 테마를 보완해야 하므로).
    #   고정 장치는 기존 `pick_theme(pinned=)` 하나뿐 — 새 선정 경로를 만들지 않는다(①).
    if resume_theme:
        _pinned = resume_theme
        if _pinned not in available:
            available = [_pinned] + available   # 차단분은 미발행이라 보통 남아있지만 안전망
        log(f"🔁 [재시도] 직전 차단 테마 이어받음: {_pinned}")
    else:
        try:
            from JARVIS09_COLLECTOR import load_pinned_theme
            _pinned = load_pinned_theme()
            if _pinned and _pinned in available:
                log(f"⚡ [선계산] 고정 테마 우선 사용: {_pinned}")
        except Exception:
            pass

    tried: list[str] = []
    result_any_ok   = False

    for attempt in range(min(3, len(available))):
        remaining = [t for t in available if t not in tried]
        if not remaining:
            break
        # ★ 선정(고정 우선 → random)은 JARVIS03 theme_picker 위임. 고정 테마는 첫 시도만.
        theme = pick_theme(remaining, pinned=(_pinned if attempt == 0 else None))
        if not theme:
            break
        tried.append(theme)

        log(f"📋 카탈로그 선정 (시도 {attempt + 1}/3): {theme}")
        if attempt == 0:
            send_telegram(
                f"📋 네이버 금융 테마 선정\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"테마: {theme}\n"
                f"미발행 {len(available)}/{len(catalog)}개 중 임의 선정"
            )
        else:
            send_telegram(f"⚠️ 이전 테마 실패 → 재선정: {theme}")

        if not _lock_acquire(f"카탈로그: {theme}"):
            continue
        try:
            os.environ["JARVIS_SOURCE_KEYWORD"] = theme
            os.environ["JARVIS_POST_TYPE"]      = "theme"
            # 재시도로 이어받은 테마의 첫 시도에만 직전 차단사유 주입 (보완 재작성)
            results = run_theme(
                theme,
                gate_feedback=(resume_feedback if (resume_theme and theme == resume_theme
                                                   and attempt == 0) else None),
            )

            if _isd() and not any(results.values()):
                log(f"⏸ [{theme}] 발행 연기(데몬 재시작) — 재시작 후 재시도")
                return

            _any_ok = any(results.values())

            _p2 = load_progress()
            _p2.setdefault("platform_status", {})[theme] = results
            if _any_ok and theme not in _p2.get("done", []):
                _p2["done"].append(theme)
            elif not _any_ok and theme not in _p2.get("failed", []):
                _p2["failed"].append(theme)
            save_progress(_p2)

            if _any_ok:
                result_any_ok = True
                try:
                    from shared.bus import on_post_published
                    on_post_published(theme, "all", source="catalog")
                except Exception:
                    pass
                break
            else:
                log(f"🔄 '{theme}' 발행 실패 — 다음 후보로")
        except Exception as _e:
            log(f"⚠️ 카탈로그 테마 실행 오류 ({theme}): {_e}")
        finally:
            os.environ.pop("JARVIS_SOURCE_KEYWORD", None)
            os.environ.pop("JARVIS_POST_TYPE", None)
            _lock_release()

    if not result_any_ok:
        send_telegram(
            f"❌ 테마글 전체 실패\n"
            f"시도한 테마: {', '.join(tried)}\n"
            f"종목 수집 0개 또는 발행 오류"
        )
        log(f"❌ 테마글 전체 실패: {tried}")




# ══════════════════════════════════════════
#  스케줄 모드
# ══════════════════════════════════════════

def _run_self_repair_phase(label: str) -> dict:
    """★ 사용자 박제 2026-05-18 v2 — 발행 직전 자가진단·자동수정 페이즈.

    "자가진단 → 자동수정 → 발행" *하나의 세트* 의 *전반부*. JARVIS07 auto_repair 호출 후
    결과 메타 반환 (호출자가 발행 단계 진입 전 텔레그램 보고용).

    ★ 한계 (Python import 캐시):
      - 비코드 효과 (learned_patterns 등록·DB 박제·정책 검증·헌법 갱신) → 다음 발행 호출에
        *즉시* 반영됨 (학습 자산은 매 호출 시 디스크에서 다시 읽힘).
      - 코드 수정 효과 → *현재 데몬 프로세스* 의 import 캐시 때문에 무효. 다음 데몬 재시작
        후 발효. auto_repair 가 텔레그램으로 "데몬 재시작 권장" 자동 알림.
      - subprocess 예외 시에도 ok=True 반환 → 발행은 항상 진행 (자가진단은 차단 사유 없음).

    Returns:
        {"ok": bool, "elapsed_sec": int, "code_changed": int, "skip_reason": str}
    """
    import time as _time
    t0 = _time.time()

    log(f"🔧 [{label}] 발행 전 자체수리(Tier-1, LLM-0) 시작")
    try:
        send_telegram(
            f"🔧 *[{label}] 발행 전 자체수리 시작*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Tier-1 자체수리 (LLM-0, 수초) → 발행 — 심층 LLM 감사는 별도 잡(j07_deep_audit)"
        )
    except Exception:
        pass

    code_changed = 0
    try:
        # ★ 2026-06-28 사용자 박제 — 발행 직전엔 *LLM-0 Tier-1 sweep* 만 (수초, 발행 지연 0).
        #   미해결 오류 중 학습 패턴·정적 fixer·Bandit 로 즉시 고칠 수 있는 것만 소급 수리.
        #   비싼 LLM 심층 감사(backlog Tier-2 + 광범위 코드 감사)는 `j07_deep_audit` 로 분리(시각은 DEFAULT_JOBS).
        from JARVIS07_GUARDIAN.guardian_agent import self_heal_known_errors as _sweep
        _res = _sweep()
        code_changed = int(_res.get("fixed", 0))  # 코드 수정 건수 → 데몬 재시작 권장 판단
        elapsed = int(_time.time() - t0)
        log(f"✅ [{label}] 발행 전 자체수리(Tier-1) 완료 ({elapsed}s, "
            f"수리 {_res.get('fixed', 0)} / 보류 {_res.get('skipped', 0)} / 무시 {_res.get('ignored', 0)})")
        return {"ok": True, "elapsed_sec": elapsed, "code_changed": code_changed, "skip_reason": ""}
    except Exception as _e:
        elapsed = int(_time.time() - t0)
        log(f"⚠️ [{label}] 자가진단 페이즈 예외 (발행은 진행): {_e}")
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _gr
            _gr(_e, source="scheduler",   # ★ FIX[5]: exc 첫 위치인자 (exc= 키워드 미존재)
                module="JARVIS02_WRITER.scheduler._run_self_repair_phase",
                func_name="_run_self_repair_phase",
                context={"label": label, "elapsed": elapsed})
        except Exception:
            pass
        try:
            send_telegram(
                f"⚠️ *[{label}] 자가진단 subprocess 예외 — 발행은 진행*\n"
                f"사유: {type(_e).__name__}: {str(_e)[:120]}"
            )
        except Exception:
            pass
        return {"ok": True, "elapsed_sec": elapsed, "code_changed": 0,
                "skip_reason": f"{type(_e).__name__}: {str(_e)[:80]}"}


def _naver_cookie_ready(label: str) -> bool:
    """네이버 쿠키 전제조건 — 경제·테마 **공통 단일 지점** (사용자 승인 2026-07-25).

    ★ 왜 만들었나: 2026-07-25 21:05, 네트워크가 끊긴 그 순간 쿠키 점검이 한 번 실패했고
      그걸로 그날 테마글이 통째로 사라졌다. 원인이 *네트워크(곧 회복)* 인지
      *CAPTCHA·계정(사람 필요)* 인지 구분이 없어 둘 다 "오늘 발행 없음" 으로 끝났다.

    ★ ① 단일 진입점: 종전엔 이 블록이 경제·테마 두 콜백에 **똑같이 복사**돼 있었다.
      한쪽만 고치면 다른 쪽이 옛 동작을 유지하는 자리라 함수 하나로 합쳤다.
    ★ ② 동적 설계: 재시도 창을 여기서 만들지 않는다. **잡 자신의 misfire 유예시간**에서
      파생한다(`job_window_deadline`). 지금 실행 중인 잡 ID 도 문맥에서 조회한다 —
      코드에 박으면 JARVIS04 에서 ID·시각을 바꿔도 여기만 옛 값을 가리킨다.
    ★ 발행 시각 원칙 (사용자 박제 "발행은 07시와 21시뿐"): 창을 넘기면 **기다리지 않는다.**
      창을 모르면(파생 실패) 아예 기다리지 않는다 — 모르는 채로 미루는 것이 곧 시간외 발행이다.
    """
    deadline = None
    try:
        from shared.llm import current_job_id
        from JARVIS04_SCHEDULER.job_registry import job_window_deadline
        deadline = job_window_deadline(current_job_id())
    except Exception as _e:
        log(f"⚠️ [{label}] 발행 창 파생 실패 — 재시도 없이 1회만 점검: {_e}")

    from JARVIS08_PUBLISH.credentials.login_manager import ensure_naver_ready
    ok, why = ensure_naver_ready(deadline=deadline)

    if ok:
        if why.startswith("recovered"):
            m = (f"✅ *[{label}] 네이버 쿠키 회복* — 발행 계속\n"
                 f"네트워크 단절로 {why.split(':')[1]}회 재시도 후 통과했습니다.")
            log(m.replace("*", ""))
            send_telegram(m)
        return True

    if why == "permanent":
        m = (f"🚨 *[{label}] 네이버 쿠키 점검 실패 — 발행 건너뜀*\n"
             f"네트워크는 정상입니다. CAPTCHA·계정 문제로 보이며 *직접 로그인* 이 필요합니다.")
    else:
        _until = f" (창 마감 {deadline:%H:%M})" if deadline else ""
        m = (f"🚨 *[{label}] 네이버 쿠키 점검 실패 — 오늘 발행 포기*\n"
             f"네트워크 단절이 발행 창 안에 회복되지 않았습니다{_until}.")
    log(m.replace("*", ""))
    send_telegram(m)
    return False


def run_self_repair_then_economic():
    """★ 통합 callback (사용자 박제 2026-05-18 v2) — 07:00 진입점.

    *하나의 세트*: 자가진단 → 쿠키 점검(네이버만) → 경제 브리핑 발행. 시퀀스 보장.

    흐름:
      1) Tier-1 자체수리 (LLM-0) — 학습 패턴·Bandit 로 미해결 오류 소급 수리
      2) 쿠키·캐시 초기화 → 네이버 쿠키 갱신 (티스토리는 티스토리 액션 시작 시 갱신)
      3) economic_poster.run() — harness 5-Layer 경유 발행

    쿠키 점검 실패 시 발행 건너뜀. 자가진단은 결과 무관 발행 진행.
    """
    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 데몬 재시작 시 misfire 유예로 뒤늦게 실행되는 07:00 잡이 죽어가는 인터프리터에서
    # 돌면 수집 ThreadPoolExecutor 크래시 → 헛된 실패. 종료 중이면 세트 자체를 건너뜀
    # (쿠키·자가수리·발행 전부). keeper 재기동 새 프로세스가 misfire 재실행 → 정상 발행.
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log("⏸ [경제 브리핑] 인터프리터 종료 중(데몬 재시작) — 발행 세트 연기, 재시작 후 재시도")
        return

    # ★ 중복 실행 차단 — 오늘 경제 브리핑이 실제로 발행 성공한 경우에만 스킵
    # (로그 파일 존재 여부가 아닌 DB 발행 이벤트 기준 — 실패 시 재실행 허용)
    try:
        import sqlite3 as _sql, json as _json
        from shared.db import DB_PATH as _dbp
        _con = _sql.connect(str(_dbp))
        _row = _con.execute(
            "SELECT id FROM events WHERE event_type='post_published' AND source='WRITER'"
            " AND json_extract(payload,'$.post_type')='economic'"
            " AND date(created_at)=date('now','localtime') LIMIT 1"
        ).fetchone()
        _con.close()
        if _row:
            _msg = "⛔ [경제 브리핑] 오늘 발행 성공 기록 있음 — 중복 발행 차단"
            log(_msg)
            send_telegram(_msg)
            return
    except Exception as _e:
        log(f"⚠️ [경제 브리핑] 중복 체크 실패 ({_e}) — 안전하게 진행")

    # ─── Step 1: 자체수리 먼저 (사용자 박제 2026-07-12) ──────────────────────
    # 쿠키 관련 코드가 수정될 수 있으므로 자가진단을 쿠키 확인보다 먼저.
    _phase = _run_self_repair_phase("경제 브리핑")
    try:
        if _phase["code_changed"] > 0:
            send_telegram(
                f"🔁 *데몬 재시작 권장*\n"
                f"자가진단이 코드 {_phase['code_changed']}건 수정 → Python import 캐시 때문에 *이번 발행엔 무효*.\n"
                f"이번 경제 브리핑 끝나고 `pkill -f jarvis_daemon.py && python jarvis_daemon.py` 권장."
            )
    except Exception:
        pass

    # ─── Step 2: 이전 쿠키·캐시 전체 삭제 ──────────────────────
    _clear_all_cookies("경제 브리핑")

    # ─── Step 3: 쿠키 체크 — ★ 네이버만 (사용자 박제 2026-07-12) ─────
    # 네이버가 첫 액션 → 네이버 쿠키만 지금 갱신. 티스토리 쿠키는 *티스토리 발행 직전*
    # (_step_ts_cookie, force=True)에 강제 갱신 → 신선 세션.
    if not _naver_cookie_ready("경제 브리핑"):
        return

    log(f"📤 [경제 브리핑] 발행 페이즈 진입 (자가진단 {_phase['elapsed_sec']}s 종료)")
    # busy 마킹은 실제 작업 진입점(J09 수집·J08 발행 등)에서 직접 수행 — 고정 TTL 일괄 사전 마킹 폐지 (2026-07-16)
    return run_economic_poster()


# ★ job_startup_recovery / _theme_publish_hour 삭제 (사용자 박제 2026-07-22 — ERRORS [469])
#   부팅 시 자동 재발행 폐지. 발행은 DEFAULT_JOBS cron 정각에만.

def run_self_repair_then_theme():
    """★ 통합 callback (사용자 박제 2026-05-18 v2) — 16:00 진입점.

    *하나의 세트*: 쿠키 점검 → 자가진단 → 자동수정 → 테마 발행. 시퀀스 보장.
    """
    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 데몬 재시작 시 misfire 유예로 뒤늦게 실행되는 16:00 잡이 죽어가는 인터프리터에서
    # 돌면 수집 ThreadPoolExecutor 크래시 → 헛된 "글자수 실패". 종료 중이면 세트 자체 건너뜀.
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log("⏸ [테마글] 인터프리터 종료 중(데몬 재시작) — 발행 세트 연기, 재시작 후 재시도")
        return

    # ★ 중복 실행 차단 — 테마 발행이 현재 진행 중이면 세트 전체 스킵
    if _is_locked_externally():
        _msg = "⛔ [테마글] 발행 세트 이미 진행 중 — 중복 실행 차단"
        log(_msg)
        send_telegram(_msg)
        return

    # ─── Step 1: 자가 치유 먼저 — 쿠키 관련 코드가 수정될 수 있으므로 ──────
    _phase = _run_self_repair_phase("테마글")
    try:
        if _phase["code_changed"] > 0:
            send_telegram(
                f"🔁 *데몬 재시작 권장*\n"
                f"자가진단이 코드 {_phase['code_changed']}건 수정 → 이번 발행엔 무효 (Python import 캐시).\n"
                f"이번 테마 발행 끝나고 `pkill -f jarvis_daemon.py && python jarvis_daemon.py` 권장."
            )
    except Exception:
        pass
    # ─── Step 2: 이전 쿠키·캐시 전체 삭제 ──────────────────────
    _clear_all_cookies("테마글")

    # ─── Step 3: 쿠키 체크 — ★ 네이버만 (사용자 박제 2026-07-05, ERRORS [363]) ─────
    # 네이버가 첫 액션 → 네이버 쿠키만 지금 갱신. 티스토리 쿠키는 *티스토리 차례*
    # (`trend_theme_writer._step_ts_cookie`, 액션 2 시작)에 force 갱신 → 신선 세션.
    # 여기서 티스토리를 미리 로그인하면 네이버 발행 내내(10분+) 카카오 세션이 방치·만료된다
    # (선로그인 대기 사망, ERRORS [265]). "네이버 작성 타임엔 네이버 쿠키만".
    if not _naver_cookie_ready("테마글"):
        return

    log(f"📤 [테마글] 발행 페이즈 진입 (자가진단 {_phase['elapsed_sec']}s 종료)")
    # busy 마킹은 실제 작업 진입점(J09 수집·J08 발행 등)에서 직접 수행 — 고정 TTL 일괄 사전 마킹 폐지 (2026-07-16)
    return run_radar_top_theme()


def _trigger_economic_incident(
    failed: list, error_text: str, harness_issues: list | None = None,
    returncode: int | None = None, keywords: dict | None = None,
) -> None:
    """경제 브리핑 실패 플랫폼 → GUARDIAN incident_responder 백그라운드 트리거.

    ★ P0-② 패치 (사용자 박제 2026-05-18 — ADR 009 v2 우회 차단):
       현재: economic_poster.run(post_naver=..., post_tistory=...) 을 retry_fn 으로 전달
              → harness 5-Layer 통과 보장 (실패 시 escalation, 부분 실패도 검증 재진입).

    ★ ★ stale 모듈 캐시 수정 (ERRORS [210] 교훈):
       _econ_run 을 미리 import 해 클로저에 박으면, incident_responder 가 코드를 수정해도
       retry 시 구버전 코드가 실행됨. → 항상 importlib.reload 후 fresh import 사용.

    harness_issues: 하네스 abort 시 구조화된 이슈 목록 (경제글 EP_RESULT_FILE 에서 읽음).
    returncode: 발행 subprocess 종료코드. WATCHDOG_KILL_RC(freeze 강제종료)면
      incident_responder 가 transient 로 확정 → Tier-2 SDK 낭비 없이 in-process 재시도.
    """
    try:
        from JARVIS07_GUARDIAN.incident_responder import respond_in_background

        # harness_issues 가 있으면 error_text 앞에 구조화 정보 추가
        if harness_issues:
            _structured = "\n".join(f"  • {s}" for s in harness_issues[:10])
            error_text = f"[하네스 검증 실패 상세]\n{_structured}\n\n[로그 끝 3000자]\n{error_text}"

        # ★ 재시도 = *같은 주제 이어받아 보완* (사용자 박제 2026-07-25).
        #   종전엔 주제 없이 재진입해 새 주제를 뽑았다 — 방금 발행된 키워드가 중복회피
        #   원장(post_analysis)에 '사용됨'으로 잡혀 밀려나기 때문(네이버 반도체 발행 →
        #   티스토리 재시도가 액화천연가스로 갈아탐). 수집 15분을 버리고, 정작 *무엇이
        #   부족했는지* 도 함께 버려졌다. 이제 주제 + 직전 차단사유를 물려준다.
        _kws = keywords or {}

        def _resume_for(platform: str) -> dict:
            kw = (_kws.get(platform) or "").strip()
            fb = [s for s in (harness_issues or []) if s.startswith(f"[{platform}]")]
            if not (kw or fb):
                return {}
            return {platform: {"keyword": kw, "feedback": fb}}

        def _make_retry(*, post_naver=False, post_tistory=False, resume=None):
            """★ 항상 fresh import — Claude Code SDK 가 코드 수정해도 즉시 반영."""
            _pn, _pt, _rs = post_naver, post_tistory, resume or {}
            def _retry():
                import importlib, sys as _sys
                # 수정된 코드 반영: economic_poster 관련 모듈 강제 재로드
                for _k in list(_sys.modules.keys()):
                    if "economic_poster" in _k or "trend_economic_writer" in _k:
                        try:
                            importlib.reload(_sys.modules[_k])
                        except Exception:
                            pass
                # 재로드 후 fresh import — ★ 반환값 실제 성공 여부 반영 (ERRORS [427])
                from JARVIS02_WRITER.economic_poster import run as _fresh_run
                return bool(_fresh_run(post_naver=_pn, post_tistory=_pt, resume=_rs))
            return _retry

        _retry_fns = {}
        if "naver" in failed:
            _retry_fns["naver"] = _make_retry(post_naver=True, resume=_resume_for("naver"))
        if "tistory" in failed:
            _retry_fns["tistory"] = _make_retry(post_tistory=True, resume=_resume_for("tistory"))
        respond_in_background("economic", failed, error_text, _retry_fns, returncode=returncode)
        log(f"🛡️ GUARDIAN incident_responder 트리거됨 (harness 경로): {failed}")
    except Exception as _ie:
        log(f"⚠️ GUARDIAN 트리거 실패: {_ie}")


def handle_telegram_command(cmd: str) -> None:
    """텔레그램 슬래시 명령 실행 계층 (JARVIS02) — ★ 사용자 박제 2026-06-28: 유실 디스패처 복원.

    호출 경로:
      ① bot.py 승인 콜백 — 외부 발행(/economic*·/next)은 *인라인 버튼 ✅ 통과 후* 호출.
      ② bot.py 직접 — /stop·/resume 내부 제어 (승인 불필요).
      ③ JARVIS01 ReAct delegate — APPROVAL 게이트 통과 후 호출.
    외부 발행은 *별도 스레드* 로 띄워 즉시 리턴 (호출자 블로킹 방지 — agent_tools 가정).
    """
    global _paused
    import threading as _th
    c = (cmd or "").strip().split()[0].lower() if (cmd and cmd.strip()) else ""

    def _bg(fn, *a):
        _th.Thread(target=fn, args=a, daemon=True, name=f"j02cmd_{c.lstrip('/')}").start()

    if c == "/economic":
        _bg(run_economic_poster)
    elif c == "/economic_naver":
        _bg(run_economic_poster, "--naver-only")
    elif c == "/economic_tistory":
        _bg(run_economic_poster, "--tistory-only")
    elif c == "/next":
        _bg(run_next)
    elif c == "/stop":
        _paused = True
        send_telegram("⏸ 스케줄러 일시정지됨. 재개하려면 /resume")
    elif c == "/resume":
        _paused = False
        send_telegram("▶ 스케줄러 재개됨.")
    else:
        send_telegram(f"❓ 알 수 없는 명령: {c}\n/help 로 명령어를 확인하세요.")


def run_economic_poster(*extra_flags):
    """경제 브리핑 포스팅 (전체 또는 플랫폼 단독)"""
    label = "경제 브리핑 포스터"
    if extra_flags:
        label += f" ({' '.join(extra_flags)})"

    # Layer 1 precondition 은 economic_poster.py ActionDefinition 내장 — 여기서 수동 체크 없음
    if not _lock_acquire(label):
        return
    log(f"⏰ {label} 실행 시작")

    _full: dict | None = None      # 예외 경로에서도 참조되므로 선초기화
    try:
        # ★ 실행모델 통일 (2026-07-25): 경제·테마가 **같은 헬퍼** 로 subprocess 를 띄운다.
        #   종전엔 이 자리에 tempfile·env·tty분기·timeout 처리가 통째로 복사돼 있었고
        #   테마는 아예 직접호출이라 두 모델이 공존했다.
        cmd = [PYTHON, str(BASE_DIR / 'economic_poster.py'), '--scheduled'] + list(extra_flags)
        _rc, _full, _logpath = _spawn_publisher(label, cmd, "economic")

        class _R:
            returncode = _rc
        result = _R()

        # 플랫폼별 결과 (economic_poster.py 가 JARVIS_EP_RESULT_FILE 에 기록)
        if _full is not None:
            _platform_results = _full
        else:
            _platform_results = ({"naver": True, "tistory": True} if _rc == 0
                                 else {"naver": False, "tistory": False})

        _PLATFORM_KEYS = {"naver", "tistory"}
        failed = [k for k, v in _platform_results.items() if k in _PLATFORM_KEYS and not v]
        # ★ ERRORS [459] 동일 클래스(경제 경로) — 인프라 스로틀 지속(deferred) 플랫폼은
        #   harness 가 이미 "코드 결함 아님, 다음 회차 자연 재시도"로 판정한 건이다.
        #   여기서 걸러내지 않으면 incident_responder 가 불필요한 Tier-2 SDK 세션을 낭비한다.
        _deferred = {
            "naver": bool(_platform_results.get("naver_deferred")),
            "tistory": bool(_platform_results.get("tistory_deferred")),
        }
        _guardian_failed = [k for k in failed if not _deferred.get(k, False)]
        if any(_deferred.get(k) for k in failed):
            log(f"⏸ [ECONOMIC] 인프라 스로틀 지속 플랫폼 GUARDIAN 스킵(다음 회차 자연 재시도): "
                f"{[k for k in failed if _deferred.get(k)]}")

        if result.returncode == 0 and not failed:
            log(f"✅ {label} 완료 (로그: {_logpath.name})")
        elif result.returncode == 0 and failed:
            log(f"⚠️ {label} 일부 플랫폼 실패: {failed} (로그: {_logpath.name})")
        else:
            log(f"❌ {label} 실패 (returncode={result.returncode}, 로그: {_logpath.name})")

        # GUARDIAN 자동 대응 — extra_flags 있으면 이미 재시도 모드이므로 비활성
        if _guardian_failed and not extra_flags:
            try:
                _err_txt = Path(_logpath).read_text(encoding="utf-8", errors="ignore")[-3000:]
            except Exception:
                _err_txt = f"returncode={result.returncode}, failed_platforms={_guardian_failed}"
            # ★ EP_RESULT_FILE 에서 하네스 이슈 구조화 데이터 추출
            _full_result = _full or {}
            _harness_issues = _full_result.get("harness_issues") or []
            # ★ 재시도 이어받기 (2026-07-25): 막힌 주제를 그대로 물려준다.
            _failed_keywords = _full_result.get("keywords") or {}
            _trigger_economic_incident(_guardian_failed, _err_txt, harness_issues=_harness_issues,
                                       returncode=result.returncode,
                                       keywords=_failed_keywords)

    except Exception as e:
        log(f"❌ {label} 예외: {e}")
        # ★ 타임아웃 SIGKILL 시 손자 프로세스(Chrome)가 편집창 연 채 방치 (ERRORS [289])
        #   — 자동화 프로필 Chrome 만 정리 (사용자 개인 Chrome 은 프로필 경로 불일치로 안전).
        try:
            import subprocess as _sp2
            _prof_root = str(BASE_DIR / "chrome_profile")
            _pg = _sp2.run(["pgrep", "-f", f"user-data-dir={_prof_root}"],
                           capture_output=True, text=True)
            _pids = [p.strip() for p in _pg.stdout.splitlines() if p.strip()]
            if _pids:
                _sp2.run(["kill"] + _pids, capture_output=True)
                log(f"🔪 방치된 자동화 Chrome {len(_pids)}개 정리 (timeout 잔존)")
        except Exception:
            pass
        if not extra_flags:
            # ★ 리뷰 확정 수정 (2026-07-03): 타임아웃 kill 이어도 결과 파일(증분 기록)을
            #   읽어 *이미 성공한 플랫폼은 재발행 제외* (플랫폼 직렬화 이중 발행 차단).
            _failed = ["naver", "tistory"]
            _pr = _full if isinstance(_full, dict) else None
            if _pr:
                _failed = [k for k in ("naver", "tistory") if not _pr.get(k)]
            if _failed:
                _trigger_economic_incident(_failed, str(e))
            else:
                log("ℹ️ 예외 발생했으나 결과 파일상 양 플랫폼 발행 완료 — incident 생략")
    finally:
        _lock_release()   # 결과 임시파일 정리는 _spawn_publisher 가 담당


def cleanup_screenshots():
    """screenshots/ 폴더 내 파일 전체 삭제 (폴더 구조는 유지)"""
    import shutil
    ss_dir = BASE_DIR.parent / 'JARVIS06_IMAGE' / 'output' / 'screenshots'
    deleted = 0
    for sub in ss_dir.iterdir():
        if sub.is_dir():
            for f in sub.iterdir():
                if f.is_file():
                    f.unlink()
                    deleted += 1
    size_mb = sum(f.stat().st_size for f in ss_dir.rglob('*') if f.is_file()) / 1024 / 1024
    log(f"🧹 스크린샷 정리 완료: {deleted}개 삭제 (남은 용량: {size_mb:.1f}MB)")
    send_telegram(f"🧹 스크린샷 주간 정리 완료\n삭제: {deleted}개 파일")




# ══════════════════════════════════════════
#  JARVIS03 → JARVIS02 연결 방식
#  즉시 실행(버스 구독) 방식은 사용하지 않음.
#  ★★ 발행 시각은 **07:00 · 21:00 딱 둘뿐** (사용자 박제 2026-07-25).
#     JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 가 단독 관리:
#       07:00  j01_economic_post   → run_economic_poster()   (경제 브리핑)
#       21:00  j01_theme_post_21   → run_radar_top_theme()   (테마주)
#     JARVIS03 는 06/09/12/15시에 *트렌드를 수집* 할 뿐 — 그 시각에 발행하지 않는다.
#
#  ★ 삭제됨 (2026-07-25): `job_radar_pipeline_check`(09·15시) + `_radar_auto` +
#    jobs `j01_radar_check_09`/`j01_radar_check_15`.
#    RADAR 추천 대기열을 09·15시에 꺼내 `run_theme()` 로 **자동 발행** 하던 경로였다.
#    `_radar_auto` 기본 False 라 실제로 돌지는 않았지만, 스위치 하나로 발행이 07/21시 밖에서
#    일어나는 *잠재 경로* 였고 else 분기조차 없어 그 외엔 아무 일도 하지 않는 함수였다.
#    사용자 지시: "발행은 07시와 21시뿐. 다른 시간 발행 로직은 흔적조차 남기지 말 것."
# ══════════════════════════════════════════




# ══════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════


# ── 진입점 제거됨 ────────────────────────────────────────────
# 이 모듈은 jarvis_daemon.py 가 importlib 으로 로드해 사용합니다.
# 직접 실행 시:  python jarvis_daemon.py  (루트 디렉토리)
if __name__ == '__main__':
    import sys
    from pathlib import Path
    _daemon = Path(__file__).resolve().parent.parent / "jarvis_daemon.py"
    print("⚠️  scheduler.py 는 라이브러리 모듈입니다. 직접 실행하지 마세요.")
    print(f"   통합 데몬 실행:  python {_daemon}")
    sys.exit(0)
