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
import os, sys, time, json, subprocess, requests, threading
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

SCHEDULE_HOURS      = [21]   # ★ 테마 발행 시간 (표시용 — 실제 트리거는 DEFAULT_JOBS j01_theme_post_21). 16→21 (2026-07-05)
RADAR_CHECK_HOURS   = [9, 15]   # RADAR 파이프라인 확인: 오전 09:00 · 오후 15:00
MAX_RETRY           = 3
TG_TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID      = os.getenv("TELEGRAM_CHAT_ID", "")

_paused         = False
_shutdown       = False
_radar_auto     = False   # True: RADAR 추천 테마 자동 실행
_last_update_id = 0
_posting_lock   = threading.Lock()


# ══════════════════════════════════════════
#  포스팅 락 관리
# ══════════════════════════════════════════

def _harness_precondition_check(action_name: str) -> list[str]:
    """★ ADR 009 v2 Layer 1 — 발행 시작 *전* 사전 검증 (ERRORS [136]).

    검증 항목:
      - 핵심 환경변수 (NV_/TS_) 존재
      - 네이버 쿠키 파일 존재 (naver_cookies.pkl — _auto_refresh_cookies 후 재확인)
      - TS_COOKIE 환경변수 (tistory — 위 env 루프에서 이미 체크)
      - 핵심 모듈 import (collect_theme — 7시 사고 진원지)

    Returns:
        issues list — 빈 리스트면 통과. 비면 *발행 차단*.
    """
    issues: list[str] = []
    # 환경변수 (TS_COOKIE 포함 — 티스토리는 파일이 아닌 env 변수 방식)
    for _k in ("NV_USERNAME", "NV_PASSWORD",
               "TS_URL", "TS_USERNAME", "TS_PASSWORD", "TS_COOKIE",
               "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        if not os.environ.get(_k, "").strip():
            issues.append(f"환경변수 {_k} 누락")
    # 핵심 모듈 import (7시 사고 type 차단)
    try:
        import importlib
        importlib.import_module("JARVIS02_WRITER.collect_theme")
    except Exception as _e:
        issues.append(f"collect_theme import 실패: {type(_e).__name__}: {str(_e)[:80]}")
    # 네이버 쿠키 파일 — 실제 경로: JARVIS02_WRITER/naver_cookies.pkl
    _naver_cookie = BASE_DIR / "naver_cookies.pkl"
    if not _naver_cookie.exists():
        issues.append(f"네이버 쿠키 파일 누락: {_naver_cookie.name}")
    return issues


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


def _auto_refresh_cookies() -> dict:
    """쿠키 누락·만료 시 자동 갱신 — _harness_precondition_check 직전 호출.

    갱신 대상:
      - 네이버: naver_cookies.pkl 없거나 10시간 이상 경과 → refresh_naver_cookies()
      - 티스토리: TS_COOKIE env 없으면 → tistory_cookie_refresher.run()

    Returns:
        {"naver": True/False, "tistory": True/False}  — 갱신 시도 결과
    """
    import time as _time
    result = {"naver": True, "tistory": True}

    # ── 네이버 쿠키 ─────────────────────────────────────────────────────
    _naver_cookie = BASE_DIR / "naver_cookies.pkl"
    _naver_needs_refresh = not _naver_cookie.exists()
    if not _naver_needs_refresh and _naver_cookie.exists():
        _age_h = (_time.time() - _naver_cookie.stat().st_mtime) / 3600
        _naver_needs_refresh = _age_h >= 10

    if _naver_needs_refresh:
        log("🔄 네이버 쿠키 누락·만료 — 자동 갱신 시작")
        send_telegram("🔄 네이버 쿠키 자동 갱신 중...")
        try:
            from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import refresh_naver_cookies
            ok = refresh_naver_cookies(force=True)
            result["naver"] = bool(ok)
            if ok:
                log("✅ 네이버 쿠키 갱신 완료")
                send_telegram("✅ 네이버 쿠키 갱신 완료")
            else:
                log("❌ 네이버 쿠키 갱신 실패")
                send_telegram("❌ 네이버 쿠키 갱신 실패 — 수동 갱신 필요")
        except Exception as _e:
            log(f"❌ 네이버 쿠키 갱신 예외: {_e}")
            send_telegram(f"❌ 네이버 쿠키 갱신 예외: {type(_e).__name__}")
            result["naver"] = False

    # ── 티스토리 쿠키 (TS_COOKIE env 방식) ──────────────────────────────
    if not os.environ.get("TS_COOKIE", "").strip():
        log("🔄 TS_COOKIE 누락 — 티스토리 쿠키 자동 갱신 시작")
        send_telegram("🔄 티스토리 TS_COOKIE 자동 갱신 중...")
        try:
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _ts_run
            ok = _ts_run(force=True, notify=True)
            # run()은 (cookie_str, driver) 또는 bool 반환 — 문자열이면 성공
            if isinstance(ok, tuple):
                ok = bool(ok[0])
            result["tistory"] = bool(ok)
            if result["tistory"]:
                log("✅ 티스토리 TS_COOKIE 갱신 완료")
            else:
                log("❌ 티스토리 TS_COOKIE 갱신 실패")
                send_telegram("❌ 티스토리 TS_COOKIE 갱신 실패 — 수동 갱신 필요")
        except Exception as _e:
            log(f"❌ 티스토리 쿠키 갱신 예외: {_e}")
            send_telegram(f"❌ 티스토리 TS_COOKIE 갱신 예외: {type(_e).__name__}")
            result["tistory"] = False

    return result


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
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log(f"⚠️ 텔레그램 오류: {e}")


def get_telegram_updates():
    global _last_update_id
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": _last_update_id + 1, "timeout": 5},
            timeout=10,
        )
        if res.status_code != 200:
            return []
        updates = res.json().get("result", [])
        if updates:
            _last_update_id = updates[-1]["update_id"]
        return updates
    except Exception:
        return []


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


def fetch_kor_counts(theme: str) -> dict:
    """각 플랫폼 실제 발행 URL 크롤링 → 한글 글자수 반환. {naver: N, tistory: N}"""
    import re as _re, sqlite3, requests as _req

    _headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    def _kor(text: str) -> int:
        return sum(1 for ch in text if "가" <= ch <= "힣")

    # DB에서 URL 조회
    from shared.db import DB_PATH as _jarvis_db
    con = sqlite3.connect(str(_jarvis_db))
    rows = con.execute(
        "SELECT platform, url FROM post_analysis "
        "WHERE theme=? ORDER BY created_at DESC LIMIT 6",
        (theme,)
    ).fetchall()
    con.close()

    url_map = {}
    for platform, url in rows:
        if platform not in url_map:
            url_map[platform] = url or ""

    counts = {}

    # 네이버 — 모바일 크롤링
    try:
        nv_url = url_map.get("naver", "").replace("blog.naver.com", "m.blog.naver.com").split("?")[0]
        if nv_url:
            r = _req.get(nv_url, headers=_headers, timeout=15)
            raw = _re.sub(r"<script.*?</script>", "", r.text, flags=_re.DOTALL)
            raw = _re.sub(r"<style.*?</style>", "", raw, flags=_re.DOTALL)
            raw = _re.sub(r"<[^>]+>", " ", raw)
            raw = _re.sub(r"\s+", " ", raw).strip()
            s = raw.find("이웃추가"); e = raw.find("댓글", s + 5 if s > 0 else 0)
            counts["naver"] = _kor(raw[s + 5:e] if s > 0 and e > 0 else raw)
    except Exception:
        pass

    # 티스토리 — tt_article_useless_p_margin
    try:
        ts_url = url_map.get("tistory", "")
        if ts_url:
            r = _req.get(ts_url, headers=_headers, timeout=15)
            m = _re.search(r'class="tt_article_useless_p_margin[^"]*"[^>]*>(.*?)</div>', r.text, _re.DOTALL)
            if m:
                counts["tistory"] = _kor(_re.sub(r"<[^>]+>", "", m.group(1)))
    except Exception:
        pass

    return counts


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


def get_status_text() -> str:
    themes = load_themes()
    p      = load_progress()
    idx    = p['index']
    next_t = themes[idx] if idx < len(themes) else "없음 (전체 완료)"
    processed = len(p['done']) + len(p['failed'])
    lines  = [
        "📊 Market Signal 현황",
        "━━━━━━━━━━━━━━━━━━",
        f"전체  : {len(themes)}개",
        f"처리됨: {processed}개 (완료 {len(p['done'])} + 실패 {len(p['failed'])})",
        f"남은  : {len(themes) - idx}개",
        f"다음  : {next_t}",
        f"상태  : {'⏸ 일시정지' if _paused else '▶ 실행 중'}",
    ]
    ps = p.get('platform_status', {})
    if ps:
        lines.append("\n[최근 3개 결과]")
        for theme, res in list(ps.items())[-3:]:
            nv = '✅' if res.get('naver')   else '❌'
            ts = '✅' if res.get('tistory') else '❌'
            lines.append(f"{theme}: 네이버{nv} 티스토리{ts}")
    if p.get('failed'):
        lines.append("\n[실패 목록]")
        for f in p['failed'][:5]:
            lines.append(f"  • {f}")
    return "\n".join(lines)




# ══════════════════════════════════════════
#  테마 전체 실행
# ══════════════════════════════════════════

def run_theme(theme: str) -> dict:
    """★ 통일 파이프라인 — trend_theme_writer.run_all_themes 직접 호출 (subprocess 폐기).

    경제 트렌드(trend_economic_writer)와 동일한 1-pass 블록 파이프라인.
    Phase 1 (2 플랫폼 draft 생성) + Phase 2 (Naver·Tistory Selenium 순차).
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

    # ── 1차: 통일 파이프라인 (run_all_themes) ────────────────
    log(f"  ▶ 1차 통합 실행 (trend_theme_writer.run_all_themes)")
    try:
        from JARVIS02_WRITER.trend_theme_writer import run_all_themes
        result = run_all_themes(theme)
        results = {
            "naver":   result.get("naver",   {}).get("success", False),
            "tistory": result.get("tistory", {}).get("success", False),
        }
        _result_data_empty = result.get("data_empty", False)
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

    log(f"  📋 1차 결과: 네이버={'✅' if results.get('naver') else '❌'} | "
        f"티스토리={'✅' if results.get('tistory') else '❌'}")

    # ── 2차 재시도 제거 (ERRORS [160] — harness 가 max_attempts 내부 재시도를 이미 소진;
    #   legacy run_naver/tistory_theme() 는 _legacy_publish_guard() 차단 대상) ──

    # ── 최종 결과 ────────────────────────────────────────────
    ok   = [k for k, v in results.items() if v]
    fail = [k for k, v in results.items() if not v]
    log("=" * 50)

    all_ok = all(results.values())
    kor_map = fetch_kor_counts(theme)
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
    if fail and not _result_data_empty:
        try:
            from JARVIS07_GUARDIAN.incident_responder import respond_in_background
            _err_ctx = (
                f"테마 포스팅 실패: theme={theme}, failed_platforms={fail} (harness max_attempts 소진)"
            )

            # ★ 재발행 retry_fn — 코드 수정 후 즉시 재발행 (harness 통과 보장)
            # theme runner 는 run_radar_top_theme() 를 reload 후 재호출 (harness 내장 함수)
            def _make_theme_retry():
                """수정된 코드로 즉시 재발행. importlib.reload → harness 5-Layer 통과."""
                _fail_platforms = list(fail)
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
                    result = run_radar_top_theme()
                    return bool(result)
                return _retry

            # 실패 플랫폼 수만큼 retry_fn 등록 (incident_responder 가 플랫폼별 호출)
            _retry_fns = {p: _make_theme_retry() for p in fail}
            respond_in_background("theme", fail, _err_ctx, _retry_fns, theme=theme)
            log(f"🛡️ GUARDIAN incident_responder 트리거됨: theme={theme}, fail={fail}")
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


def run_radar_top_theme():
    """오늘 RADAR 추천 중 미작성 최상위 테마 실행. 없으면 순차 실행(run_next) 폴백."""
    # ── ★ 인터프리터 종료 레이스 가드 (근본 원인 — ERRORS [362]) ──
    # 종료 중이면 테마 선정·발행·폴백 캐스케이드 전부 건너뜀 (헛된 실패 연쇄 차단).
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log("⏸ [RADAR] 인터프리터 종료 중(데몬 재시작) — 발행 연기, 재시작 후 재시도")
        return
    if _paused:
        send_telegram("⏸ 일시정지 상태입니다.\n재개하려면 /resume")
        return
    if _is_locked_externally():
        log("⚠️ 외부 포스팅 작업 진행 중 — 스케줄 실행 건너뜀")
        return

    # ★ 데몬 재시작 없이 최신 코드 보장 — 핵심 모듈 선행 reload (ERRORS [222][224] 박제)
    # 의존성 순서 정렬 필수: draft_writer → tistory_html_writer → theme_html_writer → ...
    # 순서 어기면 theme_html_writer reload 시 OLD tistory_html_writer 캐시에서 _stocks_text 못 찾아 실패
    try:
        import importlib as _il, sys as _sys
        _ordered_pre = [
            "draft_writer",
            "tistory_html_writer",
            "theme_html_writer",
            "draft_processor",
            "trend_theme_writer",
        ]
        for _kw in _ordered_pre:
            for _k in list(_sys.modules.keys()):
                if _kw in _k:
                    try:
                        _il.reload(_sys.modules[_k])
                    except Exception:
                        pass
    except Exception:
        pass

    try:
        from shared.db import get_todays_pipeline, update_pipeline_status
        candidates = get_todays_pipeline(limit=20)
    except Exception as e:
        log(f"⚠️ RADAR 파이프라인 조회 오류: {e} — 순차 실행")
        run_next()
        return

    if not candidates:
        log("📡 오늘 RADAR 추천 없음 → 순차 실행")
        run_next()
        return

    p          = load_progress()
    done_set   = set(p.get('done', []))
    failed_set = set(p.get('failed', []))

    # ★ 최근 30일 발행 테마 로드 — 유사 주제 중복 방지 (사용자 박제 2026-05-23)
    from shared.db import get_recent_published_themes
    recent_rows  = get_recent_published_themes(days=30)
    recent_themes = [r["theme"] for r in recent_rows]

    def _is_similar_theme(candidate: str) -> str | None:
        """이미 발행된 유사 테마 반환. 없으면 None.

        ★ ADR 012 (2026-07-02): 1차 판정 = 임베딩 의미 유사도 (shared/embeddings
        단일 진입점 — 고정 그룹이 못 잡는 '로봇 ↔ 휴머노이드' 류 커버).
        임베딩 미가용 시 종전 고정 그룹 폴백.
        """
        # 1차 — 임베딩 의미 유사도
        try:
            from shared.embeddings import embed_texts, cosine_sim, available
            if available() and recent_themes:
                _vecs = embed_texts([candidate] + recent_themes)
                for _ri, _rt in enumerate(recent_themes, 1):
                    if cosine_sim(_vecs[0], _vecs[_ri]) >= 0.80:
                        return _rt
        except Exception:
            pass
        # 2차 — 고정 그룹 폴백
        c = candidate.lower().replace(" ", "").replace("·", "")
        # 반도체 계열 키워드 그룹
        _SIMILAR_GROUPS = [
            {"반도체", "파운드리", "시스템반도체", "메모리반도체", "hbm", "tsmc", "칩"},
            {"부동산", "아파트", "재건축", "청약", "분양", "주택"},
            {"금리", "기준금리", "fed", "연준", "채권", "국채"},
            {"환율", "달러", "원달러", "외환"},
            {"인터넷", "플랫폼", "카카오", "네이버"},
            {"배터리", "2차전지", "전기차", "ev", "배터리셀"},
            {"바이오", "제약", "신약", "임상"},
            {"ai", "인공지능", "llm", "딥러닝"},
        ]
        for group in _SIMILAR_GROUPS:
            if any(k in c for k in group):
                # 같은 그룹에 속한 최근 발행 테마 있는지
                for rt in recent_themes:
                    rt_c = rt.lower().replace(" ", "").replace("·", "")
                    if any(k in rt_c for k in group):
                        return rt
        return None

    selected = None
    skipped  = []
    for item in candidates:
        theme = item['theme']
        if theme in done_set:
            skipped.append(f"{theme}(완료)")
            continue
        if theme in failed_set:
            skipped.append(f"{theme}(실패이력)")
            continue
        result = load_platform_result(theme)
        if all(result.values()):
            skipped.append(f"{theme}(완료)")
            continue
        # 최근 30일 유사 주제 중복 체크
        similar = _is_similar_theme(theme)
        if similar:
            skipped.append(f"{theme}(유사주제:'{similar}')")
            continue
        selected = item
        break

    if not selected:
        log("📡 오늘 RADAR 추천 전부 완료 → 순차 실행")
        send_telegram("📡 오늘 RADAR 추천 테마 전부 완료\n순차 테마로 전환합니다.")
        run_next()
        return

    theme = selected['theme']
    skip_msg = f"\n건너뜀: {', '.join(skipped)}" if skipped else ""
    log(f"📡 RADAR 선택: {theme} (기회점수 {selected['opportunity_score']:.0f}){skip_msg}")
    send_telegram(
        f"📡 RADAR 추천 테마 실행\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"테마: {theme}\n"
        f"섹터: {selected['sector']} | 기회점수: {selected['opportunity_score']:.0f}"
        + (f"\n건너뜀: {', '.join(skipped)}" if skipped else "")
    )

    update_pipeline_status(selected['id'], 'processing')

    # ★ data_empty 자동 전환 (ERRORS [168] 박제 2026-05-25 / [174] 개선 2026-05-26)
    # 원인: _fallback_candidates 에 완료·유사주제 테마가 포함되어 전부 필터 아웃됨.
    # 해결: 폴백 후보 선정 시 완료(done_set) + 유사주제 사전 필터링 후 최대 5개 확보.
    _tried = [selected]
    _fallback_candidates: list = []
    for _fc in candidates:
        if _fc['theme'] == theme:
            continue
        if _fc['theme'] in done_set:
            continue  # 이미 완료된 테마 폴백 제외 (중복 발행 방지)
        if _fc['theme'] in failed_set:
            continue  # 이미 실패(data_empty 등)한 테마 폴백 재선정 방지
        _load_res = load_platform_result(_fc['theme'])
        if all(_load_res.values()):
            continue  # platform_result 로도 완료 확인
        if _is_similar_theme(_fc['theme']):
            continue  # 유사주제 사전 필터
        _fallback_candidates.append(_fc)
        if len(_fallback_candidates) >= 5:
            break

    def _run_one_theme(item: dict) -> bool:
        """단일 테마 실행. 성공(최소 1 플랫폼) 시 True 반환."""
        _theme = item['theme']
        if not _lock_acquire(f"RADAR: {_theme}"):
            update_pipeline_status(item['id'], 'suggested')
            return False
        try:
            os.environ["JARVIS_SOURCE_KEYWORD"] = _theme
            os.environ["JARVIS_POST_TYPE"]      = "theme"
            _results = run_theme(_theme)

            # ★ 인터프리터 종료 레이스 (ERRORS [362]) — 발행 미시작(연기). 실패 오기록·폴백
            #   캐스케이드 금지: pipeline 은 'suggested'(재시도 대상)로 되돌리고 True 반환해
            #   상위 폴백 루프를 멈춘다 (죽어가는 인터프리터에서 다른 테마 시도 무의미).
            if _isd() and not any(_results.values()):
                log(f"⏸ [{_theme}] 발행 연기(데몬 재시작) — 실패 미기록·폴백 스킵, 재시작 후 재시도")
                update_pipeline_status(item['id'], 'suggested')
                return True

            # 결과 판정 — 최소 1 플랫폼 성공이면 OK
            _any_ok = any(_results.values())
            _all_ok = all(_results.values())
            update_pipeline_status(item['id'], 'done' if _any_ok else 'failed')

            try:
                from shared.bus import on_post_published
                if _any_ok:
                    on_post_published(_theme, "all", source="radar")
            except Exception as _bus_e:
                try:
                    from JARVIS07_GUARDIAN.error_collector import report as _gr
                    _gr("scheduler", _bus_e, module="scheduler", func_name="run_radar_top_theme.on_post_published")
                except Exception:
                    pass

            _p2 = load_progress()
            _p2.setdefault('platform_status', {})[_theme] = _results
            if _all_ok and _theme not in _p2.get('done', []):
                _p2['done'].append(_theme)
            elif not _all_ok and _theme not in _p2.get('failed', []):
                _p2['failed'].append(_theme)
            save_progress(_p2)
            return _any_ok
        except Exception as _e:
            log(f"⚠️ RADAR 테마 실행 오류 ({_theme}): {_e}")
            update_pipeline_status(item['id'], 'suggested')
            return False
        finally:
            os.environ.pop("JARVIS_SOURCE_KEYWORD", None)
            os.environ.pop("JARVIS_POST_TYPE", None)
            _lock_release()

    # 첫 번째 시도
    _ok = _run_one_theme(selected)

    # 실패 시 다음 후보 테마 자동 전환 (data_empty / 전체 플랫폼 실패)
    # 폴백 후보는 위에서 이미 완료·유사주제 필터 완료 — 추가 체크 불필요
    if not _ok:
        update_pipeline_status(selected['id'], 'failed')
        if not _fallback_candidates:
            log("⚠️ 폴백 테마 후보 없음 (모두 완료·유사주제·RADAR 미선정)")
        for _fb in _fallback_candidates:
            _fb_theme = _fb['theme']
            send_telegram(
                f"⚠️ '{theme}' 발행 실패\n"
                f"▶ 폴백 테마 자동 전환: {_fb_theme}"
            )
            log(f"🔄 폴백 테마 전환: {theme} → {_fb_theme}")
            _tried.append(_fb)
            _ok = _run_one_theme(_fb)
            if _ok:
                break
            update_pipeline_status(_fb['id'], 'failed')

        if not _ok:
            send_telegram(
                f"❌ 테마글 전체 실패\n"
                f"시도한 테마: {', '.join(c['theme'] for c in _tried)}\n"
                f"종목 수집 0개 — 순차 실행(run_next)으로 대체 발행 시도"
            )
            log(f"❌ 테마글 전체 실패: 모든 폴백 테마도 실패")
            # ★ 최종 폴백 — RADAR 전체 실패 시 순차 테마로 대체 발행 (ERRORS [245] 박제)
            log("🔄 RADAR 전체 실패 → 순차 실행(run_next) 최종 폴백 시도")
            run_next()




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
            f"Tier-1 자체수리 (LLM-0, 수초) → 발행 — 심층 LLM 감사는 새벽 04:30 분리"
        )
    except Exception:
        pass

    code_changed = 0
    try:
        # ★ 2026-06-28 사용자 박제 — 발행 직전엔 *LLM-0 Tier-1 sweep* 만 (수초, 발행 지연 0).
        #   미해결 오류 중 학습 패턴·정적 fixer·Bandit 로 즉시 고칠 수 있는 것만 소급 수리.
        #   비싼 LLM 심층 감사(backlog Tier-2 + 광범위 코드 감사)는 새벽 04:30 job_deep_audit 로 분리.
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
            _gr(source="scheduler", exc=_e,
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


def run_self_repair_then_economic():
    """★ 통합 callback (사용자 박제 2026-05-18 v2) — 07:00 진입점.

    *하나의 세트*: 쿠키 점검 → 자가진단 → 자동수정 → 경제 브리핑 발행. 시퀀스 보장.

    흐름:
      0) 쿠키 점검 (티스토리·네이버) — 만료 시 자동 갱신
      1) 발행 전 Tier-1 자체수리 (LLM-0) — 학습 패턴·Bandit 로 미해결 오류 즉시 소급 수리
         (비싼 LLM 심층 감사는 새벽 04:30 job_deep_audit 로 분리)
      2) [코드 변경 발생 시] 텔레그램 "데몬 재시작 권장" 알림 (이번 발행엔 무효)
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

    # ★ 중복 실행 차단 — 오늘 이미 경제 브리핑 발행 세트가 시작됐으면 스킵
    from datetime import datetime as _dt
    _today = _dt.now().strftime('%Y%m%d')
    _today_logs = list((BASE_DIR / 'logs').glob(f'economic_{_today}*.log'))
    if _today_logs:
        _msg = f"⛔ [경제 브리핑] 오늘 이미 실행됨 ({_today_logs[0].name}) — 중복 실행 차단"
        log(_msg)
        send_telegram(_msg)
        return

    # ─── Step 1: 이전 쿠키·캐시 전체 삭제 ──────────────────────
    _clear_all_cookies("경제 브리핑")

    # ─── Step 2: 쿠키 체크 — ★ 네이버만 (사용자 박제 2026-07-05, ERRORS [363]) ─────
    # 네이버가 첫 액션 → 네이버 쿠키만 지금 갱신. 티스토리 쿠키는 *티스토리 발행 직전*
    # (`economic_poster.post_to_tistory_economic`, force=True)에 강제 갱신 → 신선 세션.
    # 여기서 티스토리를 미리 로그인하면 네이버 발행 내내 카카오 세션이 방치·만료된다.
    # ★ TS_COOKIE 는 _clear_all_cookies 가 os.environ 에서 pop 했으므로, 티스토리 액션
    #   precondition(TS_COOKIE 존재 확인) 통과용으로 .env 값만 복원(로그인 없음).
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(override=True)
    _cookie_failed = []
    try:
        from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import job_pre_naver_check as _nv_ck
        if not _nv_ck():
            _cookie_failed.append("네이버")
    except Exception as _e:
        log(f"⚠️ [경제 브리핑] 네이버 쿠키 점검 예외: {_e}")
        _cookie_failed.append("네이버")
    if _cookie_failed:
        msg = f"🚨 네이버 쿠키 점검 실패 — 경제 브리핑 발행 건너뜀 (티스토리는 티스토리 차례에 갱신)"
        log(msg)
        send_telegram(msg)
        return

    # ─── Step 3: 전체 폴더 검증 (자가진단) ──────────────────────
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
    log(f"📤 [경제 브리핑] 발행 페이즈 진입 (자가진단 {_phase['elapsed_sec']}s 종료)")
    return run_economic_poster()


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

    # ─── Step 1: 이전 쿠키·캐시 전체 삭제 ──────────────────────
    _clear_all_cookies("테마글")

    # ─── Step 2: 쿠키 체크 — ★ 네이버만 (사용자 박제 2026-07-05, ERRORS [363]) ─────
    # 네이버가 첫 액션 → 네이버 쿠키만 지금 갱신. 티스토리 쿠키는 *티스토리 차례*
    # (`trend_theme_writer._step_ts_cookie`, 액션 2 시작)에 force 갱신 → 신선 세션.
    # 여기서 티스토리를 미리 로그인하면 네이버 발행 내내(10분+) 카카오 세션이 방치·만료된다
    # (선로그인 대기 사망, ERRORS [265]). "네이버 작성 타임엔 네이버 쿠키만".
    _cookie_failed = []
    try:
        from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import job_pre_naver_check as _nv_ck
        if not _nv_ck():
            _cookie_failed.append("네이버")
    except Exception as _e:
        log(f"⚠️ [테마글] 네이버 쿠키 점검 예외: {_e}")
        _cookie_failed.append("네이버")
    if _cookie_failed:
        msg = f"🚨 네이버 쿠키 점검 실패 — 테마글 발행 건너뜀 (티스토리는 티스토리 차례에 갱신)"
        log(msg)
        send_telegram(msg)
        return

    # ─── Step 3: 전체 폴더 검증 (자가진단) ──────────────────────
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
    log(f"📤 [테마글] 발행 페이즈 진입 (자가진단 {_phase['elapsed_sec']}s 종료)")
    return run_radar_top_theme()


def _trigger_economic_incident(
    failed: list, error_text: str, harness_issues: list | None = None
) -> None:
    """경제 브리핑 실패 플랫폼 → GUARDIAN incident_responder 백그라운드 트리거.

    ★ P0-② 패치 (사용자 박제 2026-05-18 — ADR 009 v2 우회 차단):
       현재: economic_poster.run(post_naver=..., post_tistory=...) 을 retry_fn 으로 전달
              → harness 5-Layer 통과 보장 (실패 시 escalation, 부분 실패도 검증 재진입).

    ★ ★ stale 모듈 캐시 수정 (ERRORS [210] 교훈):
       _econ_run 을 미리 import 해 클로저에 박으면, incident_responder 가 코드를 수정해도
       retry 시 구버전 코드가 실행됨. → 항상 importlib.reload 후 fresh import 사용.

    harness_issues: 하네스 abort 시 구조화된 이슈 목록 (경제글 EP_RESULT_FILE 에서 읽음).
    """
    try:
        from JARVIS07_GUARDIAN.incident_responder import respond_in_background

        # harness_issues 가 있으면 error_text 앞에 구조화 정보 추가
        if harness_issues:
            _structured = "\n".join(f"  • {s}" for s in harness_issues[:10])
            error_text = f"[하네스 검증 실패 상세]\n{_structured}\n\n[로그 끝 3000자]\n{error_text}"

        def _make_retry(*, post_naver=False, post_tistory=False):
            """★ 항상 fresh import — Claude Code SDK 가 코드 수정해도 즉시 반영."""
            _pn, _pt = post_naver, post_tistory
            def _retry():
                import importlib, sys as _sys
                # 수정된 코드 반영: economic_poster 관련 모듈 강제 재로드
                for _k in list(_sys.modules.keys()):
                    if "economic_poster" in _k or "trend_economic_writer" in _k:
                        try:
                            importlib.reload(_sys.modules[_k])
                        except Exception:
                            pass
                # 재로드 후 fresh import
                from JARVIS02_WRITER.economic_poster import run as _fresh_run
                _fresh_run(post_naver=_pn, post_tistory=_pt)
                return True
            return _retry

        _retry_fns = {}
        if "naver" in failed:
            _retry_fns["naver"] = _make_retry(post_naver=True)
        if "tistory" in failed:
            _retry_fns["tistory"] = _make_retry(post_tistory=True)
        respond_in_background("economic", failed, error_text, _retry_fns)
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

    # ★ 쿠키 자동 갱신 — harness precondition 직전 (누락·만료 시 갱신 후 재검증)
    if not extra_flags:
        _auto_refresh_cookies()
    # Layer 1 precondition 은 economic_poster.py ActionDefinition 내장 — 여기서 수동 체크 없음

    if not _lock_acquire(label):
        return
    log(f"⏰ {label} 실행 시작")

    import tempfile
    _res_fd, _res_path = tempfile.mkstemp(suffix=".json", prefix="ep_result_")
    os.close(_res_fd)
    _env = dict(os.environ)
    _env["JARVIS_EP_RESULT_FILE"] = _res_path
    # ★ 로그 유실 방지 (ERRORS [289] — 2026-07-03): 파일 리다이렉트 시 블록 버퍼링 →
    #   타임아웃 SIGKILL 시 마지막 수 분의 로그(발행 단계) 통째 유실. 무버퍼 강제.
    _env["PYTHONUNBUFFERED"] = "1"

    try:
        from datetime import datetime as _dt
        _ts = _dt.now().strftime('%Y%m%d_%H%M%S')
        _logpath = BASE_DIR / 'logs' / f'economic_{_ts}.log'
        cmd = [PYTHON, str(BASE_DIR / 'economic_poster.py'), '--scheduled'] + list(extra_flags)
        # ★ 부모 벽시계 backstop = 60분 (사용자 박제 2026-07-06: 5400→3600). 자식(harness)이
        #   블로그(네이버·티스토리) 액션당 30분 데드라인 + 300초 freeze 워치독으로 스스로 중단
        #   → 부모 timeout 은 그마저 안 될 때의 OS 최종 안전망(2블로그×30). 자식이 killable
        #   subprocess(--scheduled)라 freeze 시 os._exit → 부모는 대개 이 값에 안 닿음.
        with open(_logpath, 'w', encoding='utf-8') as _lf:
            result = subprocess.run(cmd, timeout=3600, stdout=_lf, stderr=subprocess.STDOUT, env=_env)

        # 플랫폼별 결과 읽기 (economic_poster.py 가 JARVIS_EP_RESULT_FILE 에 기록)
        _platform_results = {"naver": True, "tistory": True}
        try:
            _platform_results = json.loads(Path(_res_path).read_text(encoding="utf-8"))
        except Exception:
            if result.returncode != 0:
                _platform_results = {"naver": False, "tistory": False}

        _PLATFORM_KEYS = {"naver", "tistory"}
        failed = [k for k, v in _platform_results.items() if k in _PLATFORM_KEYS and not v]

        if result.returncode == 0 and not failed:
            log(f"✅ {label} 완료 (로그: {_logpath.name})")
        elif result.returncode == 0 and failed:
            log(f"⚠️ {label} 일부 플랫폼 실패: {failed} (로그: {_logpath.name})")
        else:
            log(f"❌ {label} 실패 (returncode={result.returncode}, 로그: {_logpath.name})")

        # GUARDIAN 자동 대응 — extra_flags 있으면 이미 재시도 모드이므로 비활성
        if failed and not extra_flags:
            try:
                _err_txt = Path(_logpath).read_text(encoding="utf-8", errors="ignore")[-3000:]
            except Exception:
                _err_txt = f"returncode={result.returncode}, failed_platforms={failed}"
            # ★ EP_RESULT_FILE 에서 하네스 이슈 구조화 데이터 추출
            _harness_issues: list[str] = []
            try:
                _full_result = json.loads(Path(_res_path).read_text(encoding="utf-8"))
                _harness_issues = _full_result.get("harness_issues") or []
            except Exception:
                pass
            _trigger_economic_incident(failed, _err_txt, harness_issues=_harness_issues)

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
            try:
                _pr = json.loads(Path(_res_path).read_text(encoding="utf-8"))
                _failed = [k for k in ("naver", "tistory") if not _pr.get(k)]
            except Exception:
                pass
            if _failed:
                _trigger_economic_incident(_failed, str(e))
            else:
                log("ℹ️ 예외 발생했으나 결과 파일상 양 플랫폼 발행 완료 — incident 생략")
    finally:
        _lock_release()
        try:
            Path(_res_path).unlink(missing_ok=True)
        except Exception:
            pass


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




def job_radar_pipeline_check():
    """매일 09·15시 — RADAR 추천 파이프라인 자동실행 체크 (JARVIS04 잡).

    조건: _radar_auto + 일시정지 아님 + 외부 lock 아님.
    매칭되면 RADAR 최상위 미작성 테마 자동 실행, 아니면 텔레그램 알림.
    """
    try:
        from shared.db import get_pending_pipeline, update_pipeline_status
        items = get_pending_pipeline(limit=1)
        if not items:
            return
        item = items[0]
        if _radar_auto and not _paused and not _is_locked_externally():
            log(f"📡 RADAR 자동실행: {item['theme']} (기회점수 {item['opportunity_score']:.0f})")
            update_pipeline_status(item["id"], "processing")
            def _run_radar_theme(t=item["theme"], iid=item["id"]):
                if not _lock_acquire(f"RADAR: {t}"):
                    update_pipeline_status(iid, "suggested")
                    return
                try:
                    os.environ["JARVIS_SOURCE_KEYWORD"] = t
                    os.environ["JARVIS_POST_TYPE"]      = "theme"
                    run_theme(t)
                    update_pipeline_status(iid, "done")
                    try:
                        from shared.bus import on_post_published
                        on_post_published(t, "all", source="radar")
                    except Exception:
                        pass
                finally:
                    os.environ.pop("JARVIS_SOURCE_KEYWORD", None)
                    os.environ.pop("JARVIS_POST_TYPE", None)
                    _lock_release()
            threading.Thread(target=_run_radar_theme, daemon=True).start()
    except Exception as e:
        log(f"⚠️ RADAR 파이프라인 확인 오류: {e}")


# ══════════════════════════════════════════
#  JARVIS03 → JARVIS02 연결 방식
#  즉시 실행(버스 구독) 방식은 사용하지 않음.
#  발행 스케줄은 JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 가 단독 관리:
#    07:00  j01_economic_post   → run_economic_poster()  (경제 브리핑, 3개 블로그 각각 다르게)
#    21:00  j01_theme_post_21   → run_radar_top_theme()  (테마주, RADAR 최상위 키워드 → 3개 블로그 다르게)
#  JARVIS03 는 09/12/15시 트렌드 수집 후 DB 파이프라인에 적재.
#  16시 잡이 DB에서 당일 최고 점수 테마를 꺼내 실행.
# ══════════════════════════════════════════




# ══════════════════════════════════════════
#  진입점
# ══════════════════════════════════════════


# ── 진입점 제거됨 ────────────────────────────────────────────
# 이 모듈은 jarvis_daemon.py 가 importlib 으로 로드해 사용합니다.
# 직접 실행 시:  python jarvis_daemon.py  (루트 디렉토리)
if __name__ == '__main__':
    print("⚠️  scheduler.py 는 라이브러리 모듈입니다. 직접 실행하지 마세요.")
    print("   통합 데몬 실행:  python ~/portfolio/jarvis-agent/jarvis_daemon.py")
    import sys; sys.exit(0)
