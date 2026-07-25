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
import pickle
from pathlib import Path
from typing import Any, Optional

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
        result["naver"] = {"ok": not nv_issues, "issues": nv_issues, "cookie_age_h": cookie_age}

    # 티스토리
    if "tistory" in platforms:
        ts_issues: list[str] = []
        for k in _REQUIRED_ENV["tistory"]:
            if not os.environ.get(k, "").strip():
                ts_issues.append(f"env {k} 누락")
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
        if not get_tistory_cookie():
            log.info("[login_manager] 티스토리 TS_COOKIE 없음 — 갱신 시도")
            result["tistory"] = refresh_tistory_cookies(force=False)
    return result


# ══════════════════════════════════════════════════════════
# 6) cron 잡 단일 진입점
# ══════════════════════════════════════════════════════════

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
