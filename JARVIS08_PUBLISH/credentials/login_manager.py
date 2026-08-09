"""JARVIS08_PUBLISH/credentials/login_manager.py — 로그인·인증 단일 진입점 (★ ERRORS [145]).

★ 사용자 박제 2026-05-17 — 모든 블로그·플랫폼 로그인·인증·쿠키 관련 단일 진실 소스.
규정 본문은 `LOGIN_SUPREME_LAW.md` — 본 파일은 *실행 진입점*.

★ 단일 진입점 원칙:
  다른 파일에 로그인·인증·쿠키 관련 코드 발견 시 *즉시* 이 파일로 이관 + 호출 형태로 교체.

★ 허용 호출 (외부 코드는 이것만):
  - `get_naver_cookies()`               — 네이버 쿠키 dict (selenium 호환)
  - `get_tistory_cookie()`              — 티스토리 TS_COOKIE 환경변수
  - `verify_all_logins()`               — 플랫폼 인증 상태 일괄 점검
  - `refresh_naver_cookies(force=...)`  — 네이버 쿠키 갱신
  - `refresh_tistory_cookies(force=..)` — 티스토리 쿠키 갱신
  - `auto_refresh_if_needed()`          — 만료 임박 시 자동 갱신 (모든 플랫폼)
  - `job_pre_publish_check(platform=)`  — cron 잡 진입점

★ 금지 (다른 파일):
  - `os.environ['NV_PASSWORD'|'TS_COOKIE'|...]` 직접 참조
  - 쿠키 파일 경로 하드코딩
  - `_auth_headers` 같은 함수 외부 정의
  - 로그인 URL 박제
"""
from __future__ import annotations

import logging
import os
import datetime as _dt
import pickle
from pathlib import Path
from typing import Any, Optional

# ★ `.env` 자가 로드 (2026-08-09, ERRORS [594] 후속)
#   이 모듈만 자가 로드가 없었다 — 형제인 `naver_cookie_refresher`·`tistory_cookie_refresher`
#   와 `shared/db.py` 는 전부 스스로 읽는다. 그런데 **인증 판정의 단일 진입점**
#   (`verify_all_logins`)이 여기 있어서, `.env` 를 안 읽은 프로세스(CLI·subprocess 자식)가
#   부르면 멀쩡한 자격증명을 "env 누락" 으로 오판한다. 실측으로 확인했다.
#   그 오판이 이제 **텔레그램 경보로 나가므로**(같은 커밋) 거짓 경보가 된다 —
#   거짓 경보는 진짜 경보만큼 게이트를 망친다.
try:                                                      # pragma: no cover
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except Exception:                                         # noqa: BLE001
    pass

log = logging.getLogger("jarvis")

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


def network_up(timeout: float = 3.0) -> bool:
    """인터넷 도달 가능한가 — 로그인 시도 *전* 판정의 단일 진입점.

    ★ 왜 여기인가: 종전 이 함수는 `naver_cookie_refresher` 와 `tistory_cookie_refresher`
      **양쪽에 글자까지 똑같이 복사**돼 있었다(2벌). 한쪽만 고치면 다른 쪽이 옛 동작을
      유지하는 전형적 사본 사고 자리다. 로그인 진입점이 하나이므로 그 전제 판정도 하나다.

    ★ 무엇에 쓰나: 쿠키 점검 실패의 원인을 가른다.
        네트워크 down → *일시적* (조금 뒤 다시 하면 된다)
        네트워크 up   → *영구적* (CAPTCHA·계정 문제 — 사람이 필요하다. 재시도는 낭비)
      이 구분이 없으면 둘 다 "오늘 발행 없음" 으로 끝난다 (2026-07-25 실제 사고).
    """
    import socket
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
            return True
    except OSError:
        return False


# 로그인 재시도 간격 — 무배포 조정 `COOKIE_RETRY_WAIT_SEC`.
# ★ 짧게 잡지 않는 이유: 쿠키 갱신은 Selenium 으로 *실제 로그인* 을 다시 하는 동작이다.
#   촘촘히 두드리면 네이버가 비정상 접근으로 볼 수 있다. 우리가 기다리는 대상(네트워크
#   회복)은 초 단위로 바뀌지 않으므로 넉넉한 간격이 맞다.
COOKIE_RETRY_WAIT_SEC = float(os.getenv("COOKIE_RETRY_WAIT_SEC", "180") or 180)


def ensure_naver_ready(deadline=None) -> tuple:
    """네이버 쿠키를 발행 가능 상태로 만든다 — **일시적 실패에 한해** 창 안에서 기다린다.

    ★ 사용자 승인 2026-07-25. 2026-07-25 21:05 실제 사고: 네트워크가 끊긴 그 순간 쿠키 점검이
      한 번 실패했고, 그걸로 그날 테마글이 통째로 사라졌다. 실패 원인이 *네트워크* 인지
      *CAPTCHA·계정* 인지 구분이 없어 둘 다 "오늘 발행 없음" 으로 끝났기 때문이다.

    ★ 창을 넘겨 기다리지 않는다 (사용자 박제 "발행은 07시와 21시뿐"): `deadline` 은 호출자가
      **잡 자신의 misfire 유예시간에서 파생** 해 넘긴다. 여기서 "몇 분 더" 를 만들지 않는다.
      deadline 이 없으면(창을 모르면) 기다리지 않고 즉시 실패 — 모르는 채로 미루는 것이
      곧 시간외 발행이다.

    Returns: `(준비됨?, 사유)`
      · `(True,  "")`             바로 통과
      · `(True,  "recovered:N")`  N회차에 회복
      · `(False, "permanent")`    네트워크는 정상인데 실패 → 사람이 필요 (CAPTCHA·계정)
      · `(False, "deadline")`     네트워크 단절이 창 안에 회복되지 않음
    """
    import time
    from datetime import datetime, timedelta

    attempt = 0
    while True:
        attempt += 1
        try:
            from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import job_pre_naver_check
            if job_pre_naver_check():
                return (True, f"recovered:{attempt - 1}" if attempt > 1 else "")
        except Exception as e:
            log.warning(f"[login] 네이버 쿠키 점검 예외: {e}")
            _g_report("publish", e, module=__name__, func_name="ensure_naver_ready")

        # ★ 원인 판정 — 네트워크가 죽어 있으면 *일시적*, 살아 있으면 *사람이 필요*.
        if network_up():
            return (False, "permanent")
        # 다음 시도가 창을 넘어서면 기다릴 이유가 없다 — 지금 포기하고 알린다.
        if deadline is None or datetime.now() + timedelta(seconds=COOKIE_RETRY_WAIT_SEC) >= deadline:
            return (False, "deadline")
        log.info(f"[login] 네트워크 단절 — {COOKIE_RETRY_WAIT_SEC:.0f}초 뒤 재시도 "
                 f"(창 마감 {deadline:%H:%M}, 시도 {attempt})")
        time.sleep(COOKIE_RETRY_WAIT_SEC)


# ── 경로·환경변수 단일 진실 소스 (LOGIN_SUPREME_LAW.md 제3조) ──

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# ★ 네이버 쿠키 — legacy anchor (JARVIS02_WRITER, 이동 금지)
NAVER_COOKIE_PATH = _PROJECT_ROOT / "JARVIS02_WRITER" / "naver_cookies.pkl"
# ★ 티스토리 — 환경변수 방식 (파일 없음)
TS_COOKIE_ENV = "TS_COOKIE"

# 필수 환경변수 (verify_all_logins 검증)
_REQUIRED_ENV = {
    "naver":   ("NV_URL", "NV_USERNAME", "NV_PASSWORD"),
    "tistory": ("TS_URL", "TS_USERNAME", "TS_PASSWORD", "TS_COOKIE"),
}


# ══════════════════════════════════════════════════════════
# 1) 네이버·티스토리 사용자 정보
# ══════════════════════════════════════════════════════════

def get_naver_user() -> str:
    """네이버 블로그 ID (NV_USERNAME). 조회수·메타 조회 시 사용."""
    return os.environ.get("NV_USERNAME", "").strip()


def get_naver_password() -> str:
    """네이버 비밀번호."""
    return os.environ.get("NV_PASSWORD", "").strip()


def get_tistory_user() -> str:
    """티스토리 사용자명 (Kakao 계정)."""
    return os.environ.get("TS_USERNAME", "").strip()


def get_tistory_password() -> str:
    """티스토리 비밀번호."""
    return os.environ.get("TS_PASSWORD", "").strip()


# ══════════════════════════════════════════════════════════
# 2) 네이버 쿠키 — 파일 기반
# ══════════════════════════════════════════════════════════

def get_naver_cookies() -> list[dict]:
    """네이버 쿠키 list (selenium add_cookie 호환).

    Returns:
        쿠키 dict list. 파일 없거나 비어있으면 [].
    """
    record_cookie_sighting()          # 파일을 만지는 순간이 곧 관측 기회다(ERRORS [594])
    if not NAVER_COOKIE_PATH.exists():
        return []
    try:
        with open(NAVER_COOKIE_PATH, "rb") as f:
            cookies = pickle.load(f)
        return cookies if isinstance(cookies, list) else []
    except Exception as e:
        log.warning(f"[login_manager] 네이버 쿠키 로드 실패: {e}")
        _g_report("publish", e, module=__name__)
        return []


# ══════════════════════════════════════════════════════════
# 쿠키 파일 소실 추적 — "언제 사라졌나" 를 알 수 있게 (2026-08-09, ERRORS [594])
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가
#   08-09 07:00 경제 브리핑 미발행의 근인은 CAPTCHA 였지만, *그 CAPTCHA 에 노출된 이유* 는
#   `naver_cookies.pkl` 이 사라져 **매 발행이 전체 로그인**이 됐기 때문이다.
#   그런데 파일이 없어진 사실도, 없어진 시각도 어디에도 남지 않아 원인을 추적할 수 없었다
#   (저장소에 그 파일을 지우는 코드는 0곳 — grep 실측).
#
# ★ ① 새 잡을 만들지 않는다. 쿠키를 *만지는 지점* 에서 기록한다 —
#   읽기(`get_naver_cookies`)·쓰기(`_save_cookies` 경유 갱신)·점검(`verify_all_logins`).
#   관측 빈도가 곧 해상도이고, 그 지점들은 이미 publish 도메인 안에 있다.
# ★ ② 저장은 `json_store` 정문(원자 교체 + 락 + 백업). 새 저장 방식을 만들지 않는다.
_COOKIE_WATCH = Path(__file__).resolve().parent / "cookie_watch.json"


def record_cookie_sighting() -> dict:
    """네이버 쿠키 파일의 지금 상태를 기록하고, *사라짐* 을 감지하면 그 사실을 담아 돌려준다.

    Returns: {"present", "mtime", "size", "vanished", "last_seen"}
      · `vanished=True` 는 **직전 관측엔 있었는데 지금 없다** 는 뜻 — 이때가 경보 시점이다.
    """
    now = _dt.datetime.now().isoformat(timespec="seconds")
    present = NAVER_COOKIE_PATH.exists()
    cur: dict = {"present": present, "at": now, "vanished": False, "last_seen": ""}
    try:
        if present:
            st = NAVER_COOKIE_PATH.stat()
            cur["mtime"] = _dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
            cur["size"] = st.st_size
    except Exception:                                    # noqa: BLE001
        pass
    try:
        from JARVIS07_GUARDIAN.json_store import read_json, write_json  # noqa: PLC0415
        prev = read_json(_COOKIE_WATCH, default={}) or {}
        cur["last_seen"] = (cur["at"] if present
                            else (prev.get("last_seen") or prev.get("at") or ""))
        cur["vanished"] = bool(prev.get("present")) and not present
        write_json(_COOKIE_WATCH, cur, backup=True)
        # ★ `credentials/` 안의 파일은 소유자 전용(0600) — 이 폴더 규칙이다.
        #   관측 파일 자체엔 비밀이 없지만 규칙에 예외를 두지 않는다(테스트가 강제한다).
        #   `.lock` 과 `.bak` 도 같은 폴더에 생기므로 함께 조인다.
        import os as _os                                  # noqa: PLC0415
        for _q in (_COOKIE_WATCH,
                   _COOKIE_WATCH.with_suffix(_COOKIE_WATCH.suffix + ".lock"),
                   _COOKIE_WATCH.with_suffix(_COOKIE_WATCH.suffix + ".bak")):
            try:
                if _q.exists():
                    _os.chmod(_q, 0o600)
            except OSError:
                pass
    except Exception as e:                               # noqa: BLE001
        log.warning(f"[login_manager] 쿠키 관측 기록 실패(무시): {type(e).__name__}: {e}")
    return cur


def cookie_loss_window() -> str:
    """쿠키가 사라진 구간을 사람이 읽을 수 있게 — 원인 추적의 출발점.

    ★ 창 안에 무엇이 돌았는지는 `job_runs` 가 이미 갖고 있다. 여기서 별도 기록을
      만들지 않고 그 원장을 조회한다(② 파생 — 사본을 만들지 않는다).
    """
    try:
        from JARVIS07_GUARDIAN.json_store import read_json  # noqa: PLC0415
        prev = read_json(_COOKIE_WATCH, default={}) or {}
    except Exception:                                    # noqa: BLE001
        return ""
    last = prev.get("last_seen") or ""
    if not last:
        return "마지막 관측 기록 없음 (추적 시작 전)"
    # ★ 파일이 살아 있으면 '사라졌다' 고 말하지 않는다 — 이 작업의 요점이 정직한 보고다.
    if NAVER_COOKIE_PATH.exists():
        return f"현재 존재 (마지막 관측 {last})"
    try:
        from shared.db import get_db                     # noqa: PLC0415
        with get_db() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM job_runs WHERE started_at >= ?", (last,)).fetchone()[0]
        return f"마지막 관측 {last} 이후 잡 {n}건 실행 — 그 구간에서 사라졌다"
    except Exception:                                    # noqa: BLE001
        return f"마지막 관측 {last} 이후 사라졌다"


def naver_cookie_age_hours() -> float:
    """네이버 쿠키 파일 mtime 기준 경과 시간 (시간)."""
    if not NAVER_COOKIE_PATH.exists():
        return float("inf")
    import time as _t
    return (_t.time() - NAVER_COOKIE_PATH.stat().st_mtime) / 3600


def refresh_naver_cookies(force: bool = False) -> bool:
    """네이버 쿠키 갱신 — credentials/naver_cookie_refresher.py 위임."""
    try:
        from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (
            refresh_naver_cookies as _refresh,
        )
        return bool(_refresh(force=force))
    except Exception as e:
        log.error(f"[login_manager] 네이버 쿠키 갱신 실패: {e}")
        _g_report("publish", e, module=__name__)
        return False


def check_naver_cookie_valid() -> bool:
    """네이버 쿠키 유효성 — credentials 위임."""
    try:
        from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (
            check_cookie_valid as _check,
        )
        return bool(_check())
    except Exception:
        return False


# ══════════════════════════════════════════════════════════
# 3) 티스토리 쿠키 — 환경변수 기반
# ══════════════════════════════════════════════════════════

def get_tistory_cookie() -> str:
    """티스토리 TS_COOKIE 환경변수 값."""
    return os.environ.get(TS_COOKIE_ENV, "").strip()


def refresh_tistory_cookies(force: bool = False) -> bool:
    """티스토리 쿠키 갱신 — credentials/tistory_cookie_refresher.py 위임."""
    try:
        from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _run
        result = _run(force=force, notify=True)
        return bool(result)
    except Exception as e:
        log.error(f"[login_manager] 티스토리 쿠키 갱신 실패: {e}")
        _g_report("publish", e, module=__name__)
        return False


# ══════════════════════════════════════════════════════════
# 4) 일괄 검증 — Layer 1 precondition 위임 진입점
# ══════════════════════════════════════════════════════════

def verify_all_logins(
    platforms: tuple = ("naver", "tistory"),
) -> dict[str, dict[str, Any]]:
    """플랫폼 인증 상태 점검.

    Args:
        platforms: 점검할 플랫폼 튜플. 기본 전체. ("naver",) 전달 시 네이버만 점검.
    Returns:
        {
          "naver":   {"ok": bool, "issues": list[str], "cookie_age_h": float},
          "tistory": {"ok": bool, "issues": list[str]},
        }
    """
    result: dict[str, dict[str, Any]] = {}

    # 네이버
    if "naver" in platforms:
        nv_issues: list[str] = []
        for k in _REQUIRED_ENV["naver"]:
            if not os.environ.get(k, "").strip():
                nv_issues.append(f"env {k} 누락")
        cookies = get_naver_cookies()
        if not cookies:
            nv_issues.append("쿠키 파일 없음 또는 빈 list")
        cookie_age = naver_cookie_age_hours()
        if cookie_age > 10:
            nv_issues.append(f"쿠키 만료 임박 ({cookie_age:.1f}h > 10h)")
        # ★ '파일이 있고 신선한가' 는 '쓸 수 있는가' 가 아니다 (2026-08-09, ERRORS [597]).
        #   실측: 08-08 20:30 · 08-09 06:30 두 사전점검이 **둘 다 초록**이었는데
        #   30분 뒤 발행은 로그인 튕김(28초)·CAPTCHA(163초)로 실패했다.
        #   판정 함수는 이미 있었다 — 안 부르고 있었을 뿐이다.
        #   ★ 이 검사는 **쿠키가 신선할 때야말로** 필요하다. 나이 조건 안에 넣으면
        #     "신선하니 괜찮겠지" 라는 바로 그 가정을 다시 믿는 셈이다.
        #     실제로 한 번 그렇게 들여써진 적이 있다(두 세션이 같은 자리를 동시에 고치다
        #     스코프가 어긋났다) — 그때 판정 호출이 **0회** 였고 테스트가 잡았다.
        #     들여쓰기 한 칸이 검사를 통째로 끄는 자리다. 옮길 때 반드시 호출 여부를 재라.
        #   ★ `check_cookie_valid` 가 아니라 `cookie_valid_http` 를 쓰는 이유:
        #     전자는 *갱신 여부* 판단용이라 네트워크 오류에 **True** 를 돌려준다 —
        #     True 가 '유효' 와 '판정 불가' 를 함께 뜻한다. 건강진단은 그 둘을 구분해야
        #     하므로 3-상태(`bool | None`)를 쓴다. 티스토리와 **계약까지 대칭**(③).
        #   판정은 네이버 도메인이 소유한다 — 여기서 새로 만들지 않는다(①).
        if not nv_issues:
            try:
                from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (  # noqa: PLC0415
                    cookie_valid_http as _nv_valid)
                if _nv_valid() is False:
                    nv_issues.append("쿠키 만료 — 실제 요청이 로그아웃 상태를 보고")
                # None → 판정 불가(네트워크 등). '모른다' 를 '만료' 로 적지 않는다.
            except Exception as e:                       # noqa: BLE001
                log.warning(f"[login_manager] 네이버 유효성 판정 실패(무시): "
                            f"{type(e).__name__}: {e}")
        result["naver"] = {"ok": not nv_issues, "issues": nv_issues, "cookie_age_h": cookie_age}

    # 티스토리
    if "tistory" in platforms:
        ts_issues: list[str] = []
        for k in _REQUIRED_ENV["tistory"]:
            if not os.environ.get(k, "").strip():
                ts_issues.append(f"env {k} 누락")
        # ★ env '존재' 만으로 끝내지 않는다 (2026-08-09, ERRORS [596]).
        #   ※ 초판 주석은 "네이버와 대칭" 이라 적었는데 **그때는 거짓이었다** —
        #     네이버 분기도 실효 판정을 안 부르고 있었다(동시 세션 지적, ERRORS [597]).
        #     지금은 양쪽 다 부른다. 확인하지 않은 단정을 주석에 쓰지 말 것.
        #   종전엔 만료된 쿠키도 ✅ 였다. 그래서 08-08 20:30 사전점검이 초록인 채로
        #   21:00 테마 발행이 28초 만에 로그인 화면으로 튕겨 끝났다(실측).
        #   판정 자체는 티스토리 도메인이 소유한다 — 여기서 새로 만들지 않는다(①).
        if not ts_issues:
            try:
                from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import (  # noqa: PLC0415
                    cookie_valid_http)
                valid = cookie_valid_http()
                if valid is False:
                    ts_issues.append("쿠키 만료 — manage 접근이 로그인으로 리다이렉트")
                # valid is None → 판정 불가(네트워크 등). '모른다' 를 '만료' 로 적지 않는다.
            except Exception as e:                       # noqa: BLE001
                log.warning(f"[login_manager] 티스토리 유효성 판정 실패(무시): "
                            f"{type(e).__name__}: {e}")
        result["tistory"] = {"ok": not ts_issues, "issues": ts_issues}

    return result


# ══════════════════════════════════════════════════════════
# 5) 자동 갱신 — 만료 임박 시
# ══════════════════════════════════════════════════════════

def auto_refresh_if_needed(
    naver_threshold_h: float = 10.0,
    platforms: tuple = ("naver", "tistory"),
) -> dict[str, bool]:
    """만료 임박 플랫폼만 자동 갱신.

    Args:
        platforms: 갱신 대상 플랫폼 튜플. 기본 전체. ("naver",) 전달 시 네이버만.
    Returns:
        {"naver": refreshed?, "tistory": refreshed?}
    """
    result: dict[str, bool] = {"naver": False, "tistory": False}
    if "naver" in platforms:
        age = naver_cookie_age_hours()
        if age > naver_threshold_h:
            log.info(f"[login_manager] 네이버 쿠키 {age:.1f}h — 갱신 시도")
            result["naver"] = refresh_naver_cookies(force=False)
    if "tistory" in platforms:
        # ★ '없음' 만 보면 절반만 고친다 (2026-08-09, ERRORS [596]).
        #   08-08 21:00 테마 실패 때 TS_COOKIE 는 **있었다**(40자). 다만 만료였다.
        #   그래서 여기서 아무 일도 하지 않았고, 발행은 28초 만에 로그인으로 튕겨 끝났다.
        #   판정은 티스토리 도메인이 소유한 것을 그대로 쓴다 — 새 규칙을 만들지 않는다(①).
        _need = False
        if not get_tistory_cookie():
            log.info("[login_manager] 티스토리 TS_COOKIE 없음 — 갱신 시도")
            _need = True
        else:
            try:
                from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import (  # noqa: PLC0415
                    cookie_valid_http)
                if cookie_valid_http() is False:          # None(판정 불가)은 건드리지 않는다
                    log.info("[login_manager] 티스토리 TS_COOKIE 만료 — 갱신 시도")
                    _need = True
            except Exception as e:                        # noqa: BLE001
                log.warning(f"[login_manager] 티스토리 유효성 판정 실패(갱신 보류): "
                            f"{type(e).__name__}: {e}")
        if _need:
            result["tistory"] = refresh_tistory_cookies(force=False)
    return result


# ══════════════════════════════════════════════════════════
# 6) cron 잡 단일 진입점
# ══════════════════════════════════════════════════════════

def precheck_error_type(platform: str, issues: list) -> str:
    """사전점검 실패 → 오류 타입. *이미 있는 판단*(플랫폼·이슈)에서 기계적으로 만든다.

    ★ 중앙 매핑표를 두지 않는다 (CLAUDE.md ERRORS [547]). 새 이슈 문구가 생기면
      타입이 자동으로 따라온다. 뭉뚱그린 `RuntimeError` 로 적으면 타입 기반 게이트와
      Tier-1 지문 매칭이 변별력을 잃는다.
    """
    txt = " ".join(issues or [])
    kind = ("CookieMissing" if "쿠키 파일 없음" in txt
            else "CookieStale" if "만료 임박" in txt
            else "EnvMissing" if "env " in txt
            else "Unknown")
    return "Precheck" + platform.capitalize() + kind


def _alert_precheck(platform: str, info: dict) -> None:
    """사전점검 실패를 **사람과 원장 양쪽에** 알린다 (2026-08-09, ERRORS [594]).

    ★ 왜 (실사고): 종전엔 `log.warning` 한 줄이 전부였다. 그래서
      `naver_cookies.pkl` 이 통째로 사라진 상태로 **두 번의 발행 회차가 조용히 지나갔다**
      (08-08 21:00 테마 실패 28초 · 08-09 07:00 경제 실패 163초).
      쿠키 파일이 없으면 매 발행이 *전체 로그인* 이 되고, 그때마다 CAPTCHA 확률에 노출된다.
      즉 이 경고는 "곧 발행이 깨진다" 는 예고인데 아무도 듣지 못했다.
    """
    issues = list(info.get("issues") or [])
    try:
        extra = ""
        if platform == "naver":
            # ★ 이슈 *문구* 가 아니라 **지금 관측** 을 권위로 삼는다.
            #   문구는 다른 시점의 판단이라, 그대로 믿으면 "파일 없음" 과 "현재 존재" 가
            #   한 알림에 같이 실린다(실측으로 확인). 어긋나는 경보는 신뢰를 깎는다.
            _seen = record_cookie_sighting()
            if not _seen.get("present"):
                extra = "\n" + cookie_loss_window()
        msg = (f"⚠️ *[발행 前 점검] {platform.upper()} 인증 이상*\n"
               + "\n".join(f"· {i}" for i in issues) + extra
               + "\n\n이대로면 발행 시각에 *전체 로그인* 을 시도하고, CAPTCHA 가 뜨면 건너뜁니다.")
        from shared.notify import send_tg                 # noqa: PLC0415
        send_tg(msg)
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login_manager/pre_check] 알림 실패: {type(e).__name__}: {e}")
    try:
        from JARVIS07_GUARDIAN.error_collector import report  # noqa: PLC0415
        report(precheck_error_type(platform, issues), "publish", module=__name__,
               func_name="job_pre_publish_check",
               message=f"[발행 前 점검] {platform}: {'; '.join(issues)}")
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login_manager/pre_check] 박제 실패: {type(e).__name__}: {e}")


def job_pre_publish_check(platform: Optional[str] = None) -> None:
    """cron 잡 — 발행 직전 사전 점검.

    Args:
        platform: None(전체) / "naver" / "tistory"
    """
    if platform in (None, "all"):
        verify = verify_all_logins()
        for plat, info in verify.items():
            if not info["ok"]:
                log.warning(f"[login_manager/pre_check] {plat}: {info['issues']}")
                _alert_precheck(plat, info)               # ★ 로그로만 끝내지 않는다
        # 자동 갱신
        auto_refresh_if_needed()
    elif platform == "naver":
        if naver_cookie_age_hours() > 10:
            refresh_naver_cookies(force=False)
    elif platform == "tistory":
        if not get_tistory_cookie():
            refresh_tistory_cookies(force=False)


# ══════════════════════════════════════════════════════════
# CLI 진단 진입점
# ══════════════════════════════════════════════════════════

def _cli_status() -> int:
    """python -m JARVIS08_PUBLISH.credentials.login_manager status."""
    print("=== 로그인 상태 일괄 점검 ===\n")
    verify = verify_all_logins()
    all_ok = True
    for plat, info in verify.items():
        symbol = "✅" if info["ok"] else "❌"
        print(f"{symbol} {plat.upper()}")
        if "cookie_age_h" in info:
            print(f"   쿠키 경과: {info['cookie_age_h']:.1f}h")
        if not info["ok"]:
            all_ok = False
            for iss in info["issues"]:
                print(f"   • {iss}")
        print()
    return 0 if all_ok else 1


def _cli_refresh(platform: str, force: bool = False) -> int:
    """python -m JARVIS08_PUBLISH.credentials.login_manager refresh <platform>."""
    if platform == "naver":
        ok = refresh_naver_cookies(force=force)
    elif platform == "tistory":
        ok = refresh_tistory_cookies(force=force)
    elif platform == "all":
        ok_n = refresh_naver_cookies(force=force)
        ok_t = refresh_tistory_cookies(force=force)
        ok = ok_n and ok_t
    else:
        print(f"❌ 알 수 없는 플랫폼: {platform}")
        return 2
    print(f"{'✅' if ok else '❌'} {platform} 갱신 {'성공' if ok else '실패'}")
    return 0 if ok else 1


__all__ = [
    "get_naver_user",
    "get_naver_password",
    "get_tistory_user",
    "get_tistory_password",
    # 네이버
    "get_naver_cookies",
    "naver_cookie_age_hours",
    "refresh_naver_cookies",
    "check_naver_cookie_valid",
    "NAVER_COOKIE_PATH",
    # 티스토리
    "get_tistory_cookie",
    "refresh_tistory_cookies",
    "TS_COOKIE_ENV",
    # 일괄
    "verify_all_logins",
    "auto_refresh_if_needed",
    "job_pre_publish_check",
    "network_up",
    "ensure_naver_ready",
]


if __name__ == "__main__":
    import sys
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — 인증 직접 실행 시 환경 검증
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    if len(sys.argv) < 2:
        sys.exit(_cli_status())
    cmd = sys.argv[1]
    if cmd == "status":
        sys.exit(_cli_status())
    elif cmd == "refresh" and len(sys.argv) >= 3:
        force = "--force" in sys.argv
        sys.exit(_cli_refresh(sys.argv[2], force=force))
    else:
        print("사용: python -m JARVIS08_PUBLISH.credentials.login_manager [status|refresh <naver|tistory|all> [--force]]")
        sys.exit(2)
