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

# ── 직접 실행(python <이 파일>) 대비 — 프로젝트 루트를 sys.path 에 올린다 (2026-08-10) ──
#   ★ 없으면 `from JARVIS00_INFRA...` 가 ModuleNotFoundError 로 죽고, 그것을 감싼 except 가
#     조용히 삼켜 **Layer 0 preflight 가 한 번도 안 도는** 상태가 된다 (실측: 진입점 16곳 중 8곳).
#     경고 한 줄만 찍히고 그대로 진행하므로, 안전장치가 있다고 착각하기 딱 좋다.
#   ★ 깊이를 숫자로 박지 않는다(②) — 파일이 폴더를 옮기면 조용히 깨진다(ADR 008 이관 전례).
#     루트는 유일한 진입점 `jarvis_daemon.py` 의 존재로 판별한다.
import sys as _sys
from pathlib import Path as _Path
for _anc in _Path(__file__).resolve().parents:
    if (_anc / "jarvis_daemon.py").exists():
        if str(_anc) not in _sys.path:
            _sys.path.insert(0, str(_anc))
        break
del _anc

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
# ★ 경로 사본 금지 (2026-08-11, ERRORS [615]) — 파일을 실제로 읽고 쓰는
#   naver_cookie_refresher.COOKIE_FILE 이 주인이다. 여기서 다시 조립하지 않는다.
#   실측: 같은 경로를 8곳이 각자 조립하고 있었고, 이번 사고에서 *지운 코드*와
#   *요구한 코드*가 둘 다 owner 밖 사본이었다.
from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (  # noqa: E402
    COOKIE_FILE as NAVER_COOKIE_PATH)
# ★ 티스토리 — 환경변수 방식 (파일 없음)
TS_COOKIE_ENV = "TS_COOKIE"

# 필수 환경변수 (verify_all_logins 검증)
_REQUIRED_ENV = {
    "naver":   ("NV_URL", "NV_USERNAME", "NV_PASSWORD"),
    "tistory": ("TS_URL", "TS_USERNAME", "TS_PASSWORD", "TS_COOKIE"),
}


# ══════════════════════════════════════════════════════════
# ★ 로그인 상태기 — 플랫폼 중립 승격 (2026-08-13, ③원칙 대칭)
# ══════════════════════════════════════════════════════════
#
# ★ 왜 올라왔나
#   백오프·사람 호출·실패 사유·캡차 판정은 **네이버 모듈 안에만** 있었다(6개 API).
#   그래서 티스토리는 쿠키가 만료된 채로도 *사람에게 갈 전용 경로가 0곳* 이었다 —
#   `verify_all_logins()` 는 ok=False 를 알고 있었는데 그 다음이 없었다(실측 2026-08-13).
#
# ★ 티스토리에 사본을 만들지 않는다(①). 네이버가 쓰던 한 벌을 *중립으로 승격* 하고
#   양쪽이 그 한 벌을 쓴다. 사본을 만들면 이번 사고를 그대로 재생산한다 —
#   `network_up()` 이 두 refresher 에 글자까지 같이 복사돼 있던 것과 같은 병이다.
#
# ★ 목록을 여기 박지 않는다(②).
#   · 플랫폼 목록 → `_REQUIRED_ENV` 에서 파생 (`platforms()`)
#   · 실패 사유 목록 → 각 플랫폼 모듈의 `HUMAN_REQUIRED_REASONS` 에서 파생
#   · 플랫폼 모듈 → **이름 규약** `{platform}_cookie_refresher` 로 파생
#   새 플랫폼 모듈을 규약대로 만들면 레지스트리를 고치지 않아도 자동으로 연결된다.

BACKOFF_REASON = "backoff"

# harness Issue 가 쓰는 로그인 무효 kind 의 접두사 — 두 writer(economic·theme)가 공유.
# ★ kind 에 플랫폼을 넣지 않는다: kind 의 용도는 "사람이 필요한가" 하나뿐이고,
#   플랫폼은 이미 `Issue.detail` 에 있다. 넣으면 severity 가 플랫폼×사유 곱집합을
#   알아야 한다.
_LOGIN_INVALID_KIND = "login_invalid"

# ★ 상태 파일은 **하나 + 플랫폼 키**. 플랫폼마다 파일을 늘리면 경로 파생이 두 벌이 되고
#   새 플랫폼마다 또 는다. 읽기/쓰기는 `_read_backoff()`/`_write_backoff()` 단독.
_BACKOFF_FILE = Path(__file__).resolve().parent / "login_backoff.json"

# 자동 로그인을 접어 두는 시간 / 사람이 캡차를 풀 때까지 기다릴 시간.
# ★ 새 숫자를 만들지 않는다 — 종전 네이버 상수의 기본값·환경변수를 그대로 승계하고
#   중립 이름을 우선순위로 얹는다(무배포 조정 가능, 기존 knob 도 계속 먹는다).
LOGIN_BACKOFF_SEC = int(os.getenv("LOGIN_BACKOFF_SEC")
                        or os.getenv("NAVER_CAPTCHA_BACKOFF_SEC") or 6 * 3600)
HUMAN_WAIT_SEC = int(os.getenv("LOGIN_HUMAN_WAIT_SEC")
                     or os.getenv("NAVER_CAPTCHA_WAIT_SEC") or 120)

# 로그인이 멈춘 화면을 남길 곳 — 네이버가 이미 증거를 쌓아 둔 위치를 그대로 쓴다
# (경로를 새로 조립하지 않는다 — 쿠키 파일 owner 에서 파생).
LOGIN_STUCK_DIR = NAVER_COOKIE_PATH.parent / "logs" / "login_stuck"


def platforms() -> tuple:
    """로그인 대상 플랫폼 — 목록의 주인은 `_REQUIRED_ENV` 하나다(② 파생)."""
    return tuple(_REQUIRED_ENV)


def _platform_module(platform: str):
    """`{platform}_cookie_refresher` 모듈 — **이름 규약**에서 파생. 없으면 None.

    ★ 어휘 레지스트리(플랫폼→모듈 dict)를 만들지 않는다(②). 레지스트리를 두면 새
      플랫폼을 추가할 때 *두 곳* 을 고쳐야 하고, 한쪽을 잊으면 조용히 빠진다.
    """
    try:
        import importlib                                  # noqa: PLC0415
        return importlib.import_module(
            f"JARVIS08_PUBLISH.credentials.{(platform or '').strip()}_cookie_refresher")
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login] 플랫폼 모듈 로드 실패({platform}): {type(e).__name__}: {e}")
        return None


# ── 백오프 상태 — 못 푸는 문을 계속 두드리지 않는다 (ERRORS [615]) ──
#
# ★ 상태를 파일에 두는 이유: 발행은 subprocess, 인시던트 재시도는 새 스레드다.
#   메모리 플래그는 그 경계를 못 넘는다(ERRORS [474] 와 같은 병).

def _read_backoff() -> dict:
    """백오프 상태 — `{platform: {reason, at, until, alerted}}`. 못 읽으면 {}(fail-open).

    ★ 구 스키마 마이그레이션 (2026-08-13 도입 — 2026-09 이후 삭제):
      승격 전 파일은 네이버 전용 *평면* dict 였다 (`{"reason":.., "at":.., "until":..}`).
      백오프 창 한복판에 배포되면 이 3줄이 없을 때 창이 조용히 풀려 **캡차를 다시 부른다**.
    """
    try:
        from JARVIS07_GUARDIAN.json_store import read_json  # noqa: PLC0415
        d = read_json(_BACKOFF_FILE, default={}) or {}
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login] 백오프 상태 읽기 실패(무시): {type(e).__name__}: {e}")
        return {}
    if not isinstance(d, dict):
        return {}
    # ★ 판정 순서가 중요하다: **플랫폼 키를 먼저 본다.**
    #   아래 `_write_backoff()` 가 레거시 독자를 위해 네이버 창을 최상위에 *투영* 하므로
    #   파일에는 `until` 이 최상위에도 있을 수 있다. 평면 판정을 먼저 하면 그 파일 전체를
    #   네이버 것으로 읽어 **티스토리 창을 통째로 잃는다**.
    if any(k in d for k in _REQUIRED_ENV):
        return {k: v for k, v in d.items() if k in _REQUIRED_ENV and isinstance(v, dict)}
    if "until" in d:                                      # 구 평면 스키마 = 네이버 것
        return {"naver": d}
    return d


def _write_backoff(state: dict) -> None:
    """백오프 상태 저장 — `json_store` **정문**(원자 교체·락·백업) + 소유자 전용(0600).

    ★ 종전 네이버 코드는 `_js.dumps` 별칭으로 `symmetry/json-atomic` 검사를 *우회* 하고
      있었다. 우회는 검사가 없는 것과 같다 — 승격하면서 정문으로 교정한다.
    """
    try:
        from JARVIS07_GUARDIAN.json_store import write_json  # noqa: PLC0415
        # ★ 레거시 독자 호환 투영 (2026-08-13 도입 — **2026-09 이후 삭제**)
        #   `naver_cookie_refresher._login_backoff_state()` 가 아직 *평면* 스키마
        #   (`{"reason","at","until"}` 최상위)를 직접 읽는다. 승격 직후의 전이 구간에
        #   이 투영이 없으면 이런 일이 난다 — 티스토리가 사람 개입 실패를 만나
        #   상태 파일을 새 스키마로 다시 쓰는 순간, **활성 중이던 네이버 캡차 백오프가
        #   레거시 독자에게 보이지 않게 되어 조용히 풀린다.** 그러면 무인 재시도가
        #   재개되고 캡차를 더 부른다(ERRORS [615] 그 자체).
        #   진실은 어디까지나 플랫폼 키다 — 이것은 *쓰기 시점에 파생한 투영* 이고
        #   읽기(`_read_backoff`)는 절대 여기를 권위로 삼지 않는다.
        _payload = dict(state)
        _nv = state.get("naver") or {}
        if _nv:
            _payload.update({k: _nv[k] for k in ("reason", "at", "until") if k in _nv})
        write_json(_BACKOFF_FILE, _payload, backup=True)
        for _q in (_BACKOFF_FILE,
                   _BACKOFF_FILE.with_suffix(_BACKOFF_FILE.suffix + ".lock"),
                   _BACKOFF_FILE.with_suffix(_BACKOFF_FILE.suffix + ".bak")):
            try:
                if _q.exists():
                    os.chmod(_q, 0o600)
            except OSError:
                pass
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login] 백오프 상태 기록 실패(무시): {type(e).__name__}: {e}")


def _backoff_left(platform: str) -> tuple:
    """(raw reason, 남은 초). 없거나 만료면 ("", 0.0) — 파싱은 여기 한 곳뿐(①)."""
    import time as _t                                     # noqa: PLC0415
    ent = _read_backoff().get(platform) or {}
    left = float(ent.get("until") or 0) - _t.time()
    if left <= 0:
        return ("", 0.0)
    return (str(ent.get("reason") or "captcha"), left)


def mark_login_backoff(platform: str, reason: str, *, seconds=None) -> bool:
    """사람이 필요한 실패를 기록 — 다음 *무인* 시도를 멈춘다. 새 창을 열었으면 True.

    ★ 이미 열려 있는 창은 연장하지도, 사유를 덮어쓰지도 않는다.
      덮어쓰면 최초 근본 사유(`captcha_unattended`)가 나중 파생 사유(`backoff`)로
      바뀌어 보고가 뭉개지고, 매 시도가 창을 밀어 **영원히 안 풀린다**.
    """
    import time as _t                                     # noqa: PLC0415
    state = _read_backoff()
    cur = state.get(platform) or {}
    if float(cur.get("until") or 0) > _t.time():
        return False
    _sec = LOGIN_BACKOFF_SEC if seconds is None else seconds
    state[platform] = {
        "reason": str(reason or "unknown"),
        "at": _dt.datetime.now().isoformat(timespec="seconds"),
        "until": _t.time() + max(0, int(_sec)),
        "alerted": False,
    }
    _write_backoff(state)
    return True


def clear_login_backoff(platform: str) -> None:
    """로그인이 실제로 성공했으면 백오프를 푼다 — **성공이 유일한 해제 조건**이다.

    ★ 사용자에게 나가는 안내문(`unblock_hint`)이 "직접 로그인하면 즉시 해제" 라고
      말하는 근거가 바로 이 함수다 — 저장/갱신 성공 지점이 이것을 부른다.
    """
    state = _read_backoff()
    if platform in state:
        state.pop(platform, None)
        _write_backoff(state)


def login_backoff_active_reason(platform: str) -> str:
    """지금 백오프 중이면 raw 사유 — 없으면 "". *타입 파생용*(사람이 읽는 문장 아님)."""
    return _backoff_left(platform)[0]


def login_backoff_reason(platform: str) -> str:
    """지금 자동 로그인을 시도하면 안 되는 이유 — 없으면 "".

    ★ 사람이 직접 푸는 경로(`--manual`)는 이 판정을 보지 않는다.
      막는 것은 *무인 반복* 이지 사람의 복구가 아니다.
    """
    reason, left = _backoff_left(platform)
    if not reason:
        return ""
    return (f"{platform}: {reason} 로 자동 로그인 보류 중 "
            f"(남은 {left / 60:.0f}분) — {unblock_hint(platform)}")


def current_login_failure_reason(platform: str) -> str:
    """지금 시점의 실패 사유 — **백오프 파일을 `last_login_failure()` 보다 우선**한다.

    ★ 왜 (ERRORS [629]/[630]/[636]): `last_login_failure()` 는 *이번 프로세스* 가 실제로
      갱신을 시도해 실패했을 때만 채워진다. 쿠키 나이가 임계값 미만이면 그 호출 자체를
      건너뛰므로 같은 백오프 창 안에서도 값이 요동친다(07:08 은 Backoff, 07:37 은 bare).
      백오프 파일은 프로세스 재시작에도 살아남고 항상 최신이다.
    ★ process-local 사유를 얻는 경로도 *이름 규약* 파생이다 — 어휘 레지스트리 금지(②).
    """
    r = login_backoff_active_reason(platform)
    if r:
        return r
    mod = _platform_module(platform)
    if mod is None:
        return ""
    try:
        return str(mod.last_login_failure() or "")
    except Exception:                                     # noqa: BLE001
        return ""


# 재진입 가드 — 플랫폼 모듈이 이 함수로 되돌려 위임해도 무한루프가 되지 않게.
_HRR_RESOLVING: set = set()


def human_required_reasons(platform: str) -> frozenset:
    """이 플랫폼에서 *사람이 화면 앞에 있어야만* 풀리는 실패 사유 — 코드 수정으로 불가.

    ★ 목록의 주인은 각 플랫폼 모듈의 `HUMAN_REQUIRED_REASONS` 다(② 파생).
      여기서 다시 나열하면 사유가 늘 때마다 두 곳이 갈라진다.
      `BACKOFF_REASON` 만 공통으로 더한다 — 백오프는 *어떤 플랫폼에서도* "사람이
      풀어야 해서 자동 시도를 접은 상태" 라는 같은 뜻이다.
    """
    base = {BACKOFF_REASON}
    key = ("hrr", platform)
    if key in _HRR_RESOLVING:
        return frozenset(base)
    _HRR_RESOLVING.add(key)
    try:
        mod = _platform_module(platform)
        got = getattr(mod, "HUMAN_REQUIRED_REASONS", None) if mod is not None else None
        if got:
            base |= set(got)
    except Exception:                                     # noqa: BLE001
        pass
    finally:
        _HRR_RESOLVING.discard(key)
    return frozenset(base)


def _all_human_required_reasons() -> frozenset:
    """모든 플랫폼의 합집합 — kind 판별용(kind 에는 플랫폼이 없다).

    ★ 합집합이 성립하려면 사유 어휘가 플랫폼 간 충돌하지 않아야 한다.
      회귀 테스트가 그것을 강제한다(`tests/test_login_persistence.py`).
    """
    out: set = {BACKOFF_REASON}
    for _p in platforms():
        out |= set(human_required_reasons(_p))
    return frozenset(out)


def login_error_type(platform: str, reason: str) -> str:
    """실패 사유 → 오류 타입. *이미 있는 판단*(플랫폼·사유)에서 기계적으로 만든다.

    ★ 중앙 매핑표를 두지 않는다 (CLAUDE.md ERRORS [547] — 도메인이 파생).
      예: ("naver","captcha_unattended") → 'NaverLoginCaptchaUnattended'
          ("tistory","human_timeout")    → 'TistoryLoginHumanTimeout'
    """
    slug = "".join(w.capitalize() for w in (reason or "unknown").split("_"))
    return (platform or "").capitalize() + "Login" + slug


def login_invalid_kind(reason: str) -> str:
    """harness Issue.kind 생성 — *사람이 필요한 사유* 만 접미사를 붙인다(②).

    진짜 결함일 수 있는 사유(credentials_missing·login_button_click 등)는 접미사 없이
    두어 GUARDIAN Tier-2 를 계속 탄다 — human-required 사유만 갈라낸다.
    """
    r = (reason or "").strip()
    if r in _all_human_required_reasons():
        return f"{_LOGIN_INVALID_KIND}_{r}"
    return _LOGIN_INVALID_KIND


def is_human_required_login_kind(kind: str) -> bool:
    """이 harness kind 가 '사람이 로그인해야만' 풀리는가 — 판별 단일 진입점.

    `severity._harness_says_login_human()` 이 지연 import 로 여기에 위임한다.
    """
    k = (kind or "").strip()
    if not k.startswith(f"{_LOGIN_INVALID_KIND}_"):
        return False
    return k[len(_LOGIN_INVALID_KIND) + 1:] in _all_human_required_reasons()


def recovery_command(platform: str) -> str:
    """사람이 직접 로그인하는 명령 — **모듈 `__file__` 에서 파생**(문자열 박지 않음).

    경로를 문장에 박으면 파일이 옮겨갈 때 안내문만 옛 경로를 가리킨다(ADR 008 전례).
    """
    mod = _platform_module(platform)
    if mod is None or not getattr(mod, "__file__", ""):
        return ""
    try:
        rel = Path(mod.__file__).resolve().relative_to(_PROJECT_ROOT)
    except Exception:                                     # noqa: BLE001
        return ""
    return f"`.venv/bin/python {rel} --manual`"


def unblock_hint(platform: str) -> str:
    """"당신이 로그인하면 즉시 풀린다" — 이 문장의 **단일 소유자**.

    ★ 왜 함수인가 (사용자 지시 2026-08-13): 이 사실은 로그(`login_backoff_reason`)에만
      있고 텔레그램 알림은 "자동 재시도 N시간 보류" 까지만 말했다. 그래서 사용자가
      "6시간 기다리면 되나" 로 읽고 실제로 기다렸다. 해제 주체는 `clear_login_backoff()`
      — 즉 *성공한 로그인* 이지 시간이 아니다. 두 통로(로그·텔레그램)가 같은 문장을
      쓰게 하면 오독이 양쪽에서 동시에 사라진다(①).
    """
    return (f"⏱ 기다릴 필요 없습니다 — {platform.upper()} 에 **직접 로그인하면 그 즉시 해제**됩니다 "
            f"(로그인 성공이 유일한 해제 조건).")


def alert_human_login_needed(platform: str, reason: str, shot: str = "") -> None:
    """사람이 필요하다는 것을 **그 순간** 알린다 — 발행 시각까지 기다리지 않는다.

    ★ 08-11 실측: 06:30 에 캡차로 갱신이 실패했는데 07:00 발행까지 30분간 아무 말이 없었다.
      그 30분이면 사람이 로그인할 수 있었다. 실패를 *발견한 자리* 에서 부른다.
    ★ 한 백오프 창에 **1회만** — 07:00·21:00·사전점검마다 같은 말을 반복하면
      알림 피로로 진짜 경보까지 죽는다(`_alert_precheck` 주석의 교훈).
    """
    state = _read_backoff()
    ent = state.get(platform) or {}
    import time as _t                                     # noqa: PLC0415
    if ent.get("alerted") and float(ent.get("until") or 0) > _t.time():
        return
    _msg = (f"🔐 *{platform.upper()} 자동 로그인 불가 — 사람이 필요합니다*\n"
            f"사유: {reason}\n"
            f"자동 재시도는 {max(1, LOGIN_BACKOFF_SEC // 3600)}시간 보류합니다 "
            f"(계속 시도하면 캡차를 더 부릅니다).\n\n"
            f"{unblock_hint(platform)}\n"
            f"복구: 아래를 실행하고 브라우저에서 직접 로그인하세요.\n"
            f"{recovery_command(platform)}")
    if shot:
        _msg += f"\n정지 화면: {shot}"
    try:
        from shared.notify import send_tg as _tg          # noqa: PLC0415
        _tg(_msg)
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login] 사람 호출 알림 실패: {type(e).__name__}: {e}")
    if ent:
        ent["alerted"] = True
        state[platform] = ent
        _write_backoff(state)


def human_action_hint(platform: str, reason: str) -> str:
    """사용자에게 붙일 행동 안내 — **사유별 dict 를 두지 않는다**(②).

    ★ 왜 (사용자 지시 2026-08-13): `scheduler.py` 는 `{"captcha_unattended": …,
      "captcha_timeout": …}` 어휘 dict 로 안내문을 골랐다. 백오프 창에서는 사유가
      `"backoff"` 가 되므로 **매칭 실패 → 안내문 통째 누락**, 사용자는 `사유: backoff`
      한 줄만 받았다(2026-08-13 07:00 실측 — 06:30 캡차 알림과 같은 사고인 줄 모른다).
      어휘가 아니라 **집합 소속**으로 분기하면 새 사유가 생겨도 안내문이 자동으로 따라온다.
    """
    if reason not in human_required_reasons(platform):
        return ""                                         # 코드·네트워크 결함은 GUARDIAN 몫
    # ★ "즉시 해제" 문장은 **정확히 한 번만** 나온다 — `login_backoff_reason()` 이 이미
    #   `unblock_hint()` 를 품고 있어서, 둘을 나란히 붙이면 같은 말이 두 번 찍힌다.
    #   경보 문구의 중복은 그 자체로 신뢰를 깎는다.
    head = (login_backoff_reason(platform) if login_backoff_active_reason(platform)
            else unblock_hint(platform))
    return "\n".join(p for p in (head, recovery_command(platform)) if p)


def human_wait_sec() -> int:
    """사람이 캡차/추가인증을 **직접 풀 때까지** 기다릴 초 — 화면 앞에 사람이 없으면 0.

    ★ 왜 잡 문맥이 아니라 TTY 로 판정하나 (2026-08-10 정정)
      종전엔 `shared.llm.current_job_id()` 로 판정했는데 그 값의 출처가 `threading.local`
      이라 프로세스·스레드 경계를 넘지 못한다 — 발행은 subprocess, GUARDIAN 재시도는
      새 스레드다. 실측(08-10 07:00): 무인인데 120초 대기를 4회, 합 482초를 버렸다.
    ★ 플랫폼 무관 — '사람이 있는가' 는 네이버·티스토리를 가리지 않는다.
    """
    try:
        attended = _sys.stdout.isatty() or bool(os.environ.get("JARVIS_VERBOSE"))
    except Exception:                                     # noqa: BLE001
        attended = False                                  # 판정 불가면 기다리지 않는다
    return max(0, HUMAN_WAIT_SEC) if attended else 0


def capture_login_stuck(driver, platform: str, tag: str = "") -> str:
    """로그인이 멈춘 화면을 **증거로 남긴다** — HTML + 스크린샷. 파일명에 플랫폼 태그.

    ★ 왜 (ERRORS [606]): 캡차 판정 선택자를 *본 적 없는 화면을 추측해서* 만들었다가
      실제 캡차를 놓쳤다(미탐). 추측을 고치려면 실물이 필요하다.
    ★ 티스토리는 현재 증거가 **0장** 이다 — 그래서 카카오 차단 화면의 실제 꼴을 아무도
      모른다. 승격하면서 티스토리에도 같은 눈을 달아 준다(③).
    """
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = "_".join(x for x in (stamp, platform, tag) if x)
    base = LOGIN_STUCK_DIR / name
    try:
        LOGIN_STUCK_DIR.mkdir(parents=True, exist_ok=True)
        try:
            base.with_suffix(".html").write_text(driver.page_source, encoding="utf-8")
        except Exception:                                 # noqa: BLE001
            pass
        try:
            driver.save_screenshot(str(base.with_suffix(".png")))
        except Exception:                                 # noqa: BLE001
            pass
        return str(base)
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login] 로그인 정지 화면 저장 실패: {type(e).__name__}: {e}")
        return ""


def captcha_present(driver) -> "bool | None":
    """화면에 캡차가 있는가 — **True(확실) / None(모름)**. False 는 돌려주지 않는다.

    ★ 왜 False 가 없나 (ERRORS [606]): 초판은 선택자에 안 걸리면 `False`(= 캡차 아님)를
      단정했다. 그 선택자는 실제 캡차 화면을 한 번도 보지 않고 추측으로 만든 것이었고,
      진짜 캡차가 떴을 때 놓쳤다(미탐). 그런데 로그에는 `캡차 요소 없음` 이라는
      *확신에 찬 거짓* 이 남았다. **모르는 것을 모른다고 말하는 것이 최소 조건이다.**

    ★ 호출자 계약:
      · True → 캡차 확실. 무인이면 즉시 포기하고 사람을 부른다.
      · None → 모름. 로그인이 느린 것일 수도 있으니 기다려 본다(단정하지 않는다).
    """
    try:
        from selenium.webdriver.common.by import By       # noqa: PLC0415
        # 아래 목록은 *확실한 양성* 만 담는다. 여기 없다고 캡차가 아닌 것은 아니다.
        sel = ("img[id*='captcha' i], img[src*='captcha' i], "
               "input[id*='captcha' i], input[name*='captcha' i], "
               "#captchaimg, #chptchaimg, iframe[src*='recaptcha' i], "
               "img[alt*='보안' i], img[alt*='자동입력' i]")
        if any(e.is_displayed() for e in driver.find_elements(By.CSS_SELECTOR, sel)):
            return True
    except Exception:                                     # noqa: BLE001
        pass
    return None


def human_challenge_present(driver) -> "bool | None":
    """사람 개입이 필요한 화면인가 — **True(확실) / None(모름)**. False 는 없다.

    ★ 왜 낱말이 아니라 *꼴* 인가 (②, 그리고 실측)
      종전 티스토리는 `_HUMAN_INTERVENTION_KEYWORDS`(“인증번호·보안문자·captcha·2단계…”)
      **낱말 나열**로 판정했다. 낱말 판정은 이미 한 번 무너졌다 — 캡차가 *없는* 평상시
      네이버 로그인 페이지(19,620자)에 `captcha` 7회·`보안` 2회가 들어 있어 판정이
      **항상 참**이었다(ERRORS [595]). 게다가 카카오가 문구를 바꾸면 새 거부문이 그대로
      통과하고, 목록은 낡는다.
      `autocomplete='one-time-code'` · `inputmode='numeric'` · `maxlength` 는
      접근성·모바일 키보드 요구라서 문구보다 훨씬 안정적이다.

    양성 근거(전부 '확실한 양성' 만):
      1. `captcha_present(driver) is True`
      2. 일회용 코드 입력의 꼴 (OTP·인증번호)
      3. QR 코드
    """
    if captcha_present(driver) is True:
        return True
    try:
        from selenium.webdriver.common.by import By       # noqa: PLC0415
        sel = ("input[autocomplete='one-time-code'], "
               "input[name*='otp' i], input[id*='otp' i], "
               "input[name*='verif' i], input[id*='verif' i], "
               "img[src*='qr' i], canvas[id*='qr' i], img[alt*='QR' i]")
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            if el.is_displayed():
                return True
        # 짧은 숫자 입력칸 = 인증코드의 꼴 (전화·이메일 인증 공통)
        for el in driver.find_elements(
                By.CSS_SELECTOR, "input[inputmode='numeric'][maxlength], "
                                 "input[type='tel'][maxlength]"):
            try:
                ml = int(el.get_attribute("maxlength") or 0)
            except (TypeError, ValueError):
                continue
            if 4 <= ml <= 8 and el.is_displayed():
                return True
    except Exception:                                     # noqa: BLE001
        pass
    return None


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
        # ★ 이 파일은 매 관측마다 통째로 덮어쓰인다 — 다른 소비자가 얹어 둔 필드를
        #   함께 옮기지 않으면 조용히 사라진다. `_advise_persistence()` 의 dedupe
        #   스탬프가 그 첫 사례다(새 파일을 만들지 않는다 — 상태의 종류가 같다).
        cur["persist_notified_mtime"] = str(prev.get("persist_notified_mtime") or "")
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
    """티스토리 TS_COOKIE — **항상 최신값** (갱신 직후에도 옛 값을 주지 않는다).

    ★ 왜 파일을 먼저 보나 (2026-08-10)
      쿠키 갱신은 `.env` **파일** 에 쓴다. 그런데 이 함수가 `os.environ` 만 보면
      *프로세스가 시작할 때 로드된 옛 값* 을 계속 준다. 그래서 호출자마다
      `load_dotenv(override=True)` 를 앞세워 환경을 통째로 덮고 있었다 — 실측 4곳
      (`trend_theme_writer` 2 · `economic_poster` 1 · `performance_collector` 1)
      에 `tistory_poster` 모듈 로드 1곳까지 5곳.
      그 부작용으로 호출자가 세워 둔 *무관한* 값까지 .env 값으로 되돌아갔다.
      실측 피해: 테스트가 격리해 둔 `JARVIS_DB_PATH` 가 운영 경로로 복귀해
      pytest 112건이 "테스트가 운영 DB 를 잡았다" 로 터졌다.
      **최신값을 아는 책임을 소비처 한 곳에 모으면 호출자는 아무것도 안 해도 된다**(①).
    """
    from dotenv import dotenv_values                     # noqa: PLC0415
    _v = None
    try:
        _v = dotenv_values(_PROJECT_ROOT / ".env").get(TS_COOKIE_ENV)
    except Exception:                                    # noqa: BLE001
        pass                                             # 파일을 못 읽으면 환경변수로 폴백
    # 따옴표 제거도 여기서 한 번 — 호출자마다 `.strip('"').strip("'")` 를 복사하던 것을 흡수.
    return (_v or os.environ.get(TS_COOKIE_ENV, "")).strip().strip('"').strip("'")


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
        # ★ 나이만으로 "만료" 를 단정하지 않는다 (2026-08-09, PrecheckNaverCookieStale
        #   반복 오경보 — [398]의 거울상). `naver_cookie_refresher.cookie_needs_refresh()`
        #   는 age>10h 여도 실유효성(`check_cookie_valid`)을 먼저 재확인해 valid 면
        #   mtime 만 touch 하고 재로그인을 생략한다 — 즉 "나이만 많고 실제론 멀쩡한" 쿠키는
        #   시스템 자신도 문제 삼지 않는다. 그런데 종전 이 함수는 age>10h 를 보자마자(옛
        #   `if cookie_age > 10: nv_issues.append(...)` 가 여기 있었다) 실유효성 확인(바로
        #   아래 블록) 없이 곧장 "만료 임박" 을 확정해 버렸다 — 그 블록이 `if not nv_issues:`
        #   로 게이트돼 있어 age 로 이미 이슈가 쌓이면 실유효성 체크 자체가 건너뛰어졌기
        #   때문이다. 몇 줄 뒤 `job_pre_publish_check()` 의 `auto_refresh_if_needed()` 가
        #   같은 쿠키를 "사실 유효했다" 며 mtime 만 되돌리는 동안, 경보와 GUARDIAN 오류
        #   보고(`_alert_precheck`)는 이미 나가버린 뒤였다 — 자기모순적 이중판정.
        #   age 자체를 이슈로 적지 않고 `cookie_age_h` 로만 보고 — "만료" 판정은 실유효성
        #   (`cookie_valid_http`) 결과 단독 근거로 통일한다(①, 판정 로직 복제 금지).
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
        # ★★ 지속성은 **경보이지 게이트가 아니다** (2026-08-13, 사용자 지시 ②의 안전판).
        #   실측 pkl 의 `NID_AUT`/`NID_SES` 는 지금 100% 세션 쿠키(expiry 없음)다.
        #   이것을 `issues` 에 넣는 순간 ok=False → harness precondition 차단 →
        #   `_naver_cookie_ready` False → **네이버 경제·테마 둘 다 미발행**이 되고,
        #   복구 경로(refresh)는 캡차·백오프로 막혀 있어 자력 복귀조차 불가능하다.
        #   "지금 발행할 수 있는가"(현재형)와 "내일도 살아 있는가"(미래형)는 다른 질문이다.
        #   판정 본체는 네이버 도메인이 소유한다 — 여기서 만들지 않는다(①).
        try:
            from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (  # noqa: PLC0415
                auth_persistence as _nv_persist)
            _pers = _nv_persist(cookies) or {}
            result["naver"]["cookie_durable"] = _pers.get("durable")
            result["naver"]["session_only"] = tuple(_pers.get("session_only") or ())
        except Exception as e:                           # noqa: BLE001
            # 아직 없으면(도메인 쪽 미배포) 키를 얹지 않는다 — 없는 키는 optional 이다.
            log.debug(f"[login_manager] 쿠키 지속성 판정 생략: {type(e).__name__}: {e}")

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
    naver_threshold_h=None,
    platforms: tuple = ("naver", "tistory"),
) -> dict[str, bool]:
    """만료 임박 플랫폼만 자동 갱신.

    Args:
        naver_threshold_h: 이 시간 이상 된 쿠키를 갱신 대상으로 본다.
            **None(기본) 이면 호출 시점에 네이버 도메인의 `COOKIE_MAX_AGE_HOURS` 를 조회**.
            ★ 종전 기본값은 `10.0` 리터럴이었고, 같은 값이
              `naver_cookie_refresher.COOKIE_MAX_AGE_HOURS` 에도 박혀 있었다(①위반 — 2벌).
              한쪽만 바꾸면 "갱신 대상" 과 "갱신 판단" 이 어긋난다. 모듈 로드 시점에
              받아두면 그것도 사본이 되므로 **호출 시점에** 조회한다.
        platforms: 갱신 대상 플랫폼 튜플. 기본 전체. ("naver",) 전달 시 네이버만.
    Returns:
        {"naver": refreshed?, "tistory": refreshed?}
    """
    result: dict[str, bool] = {"naver": False, "tistory": False}
    if "naver" in platforms:
        _thr = naver_threshold_h
        if _thr is None:
            try:
                from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (  # noqa: PLC0415
                    COOKIE_MAX_AGE_HOURS as _thr_owner)
                _thr = float(_thr_owner)
            except Exception:                            # noqa: BLE001
                _thr = float("inf")                      # 못 읽으면 나이로 갱신을 부르지 않는다
        age = naver_cookie_age_hours()
        if age > _thr:
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
                # ★ '모름' 두 종류를 구분한다 (2026-08-13)
                #   network  = 순단·타임아웃 → **아무 것도 안 한다.** 순단마다 로그인하면
                #              그게 캡차를 부른다(네이버가 그렇게 무너졌다).
                #   indeterminate = 응답은 정상인데 이 방식으로는 원리적으로 못 가림
                #              (유효 쿠키도 로그인 리다이렉트 — 엔드포인트 6종 실측)
                #              → 정확한 판정자(브라우저 `check_cookie_valid`)에게 묻는다.
                #                유효하면 그 안에서 "쿠키 정상 — 갱신 불필요" no-op 이다.
                #   둘을 뭉개면 ① 순단마다 로그인(캡차) 또는 ② 만료를 영영 못 잡음 이 된다.
                _v, _why = cookie_valid_http(detail=True)
                if _v is False:
                    log.info("[login_manager] 티스토리 TS_COOKIE 만료 — 갱신 시도")
                    _need = True
                elif _v is None and _why == "indeterminate":
                    log.info("[login_manager] 티스토리 HTTP 판정 불가(원리적) — 브라우저 실확인 위임")
                    _need = True
                elif _v is None:
                    log.info(f"[login_manager] 티스토리 판정 보류({_why}) — 건드리지 않는다")
            except Exception as e:                        # noqa: BLE001
                log.warning(f"[login_manager] 티스토리 유효성 판정 실패(갱신 보류): "
                            f"{type(e).__name__}: {e}")
        if _need:
            result["tistory"] = refresh_tistory_cookies(force=False)
    return result


# ══════════════════════════════════════════════════════════
# 6) cron 잡 단일 진입점
# ══════════════════════════════════════════════════════════

def precheck_error_type(platform: str, issues: list, backoff_reason: str = "") -> str:
    """사전점검 실패 → 오류 타입. *이미 있는 판단*(플랫폼·이슈·백오프 사유)에서 기계적으로 만든다.

    ★ 중앙 매핑표를 두지 않는다 (CLAUDE.md ERRORS [547]). 새 이슈 문구가 생기면
      타입이 자동으로 따라온다. 뭉뚱그린 `RuntimeError` 로 적으면 타입 기반 게이트와
      Tier-1 지문 매칭이 변별력을 잃는다.

    ★ `CookieExpired` (2026-08-09, PrecheckTistoryUnknown 사고): `verify_all_logins()`
      가 나이 추정("만료 임박")이 아니라 *실유효성 판정*(`cookie_valid_http`)으로
      옮겨가면서(위 386·411행) 실제 발생 문구가 "쿠키 만료 — ..." 로 바뀌었는데, 이
      분류기는 옛 문구("만료 임박")만 보고 있어 새 문구가 전부 `Unknown` 으로 떨어졌다
      — 판정 로직과 분류 로직이 같은 파일 안에서도 따로 놀면 샌다는 실례. "만료 임박"
      을 먼저 검사해 레거시 문구(테스트 고정값)는 그대로 `CookieStale` 유지하고,
      실유효성 판정 문구는 새 kind 로 갈라 *어떤 근거로 만료를 판정했는지* 도 보존한다.

    ★ `backoff_reason` (2026-08-12, ERRORS [623]~[629] 후속): harness precondition
      (`economic_poster._verify_platform`)과 `_naver_cookie_ready`(scheduler.py) 는
      이미 `naver_login_error_type()` 으로 위임해 캡차·백오프를 "사람이 필요한 사유"로
      세분화하는데(2026-08-11 커밋 eb70afc), 이 precheck 경로만 이슈 *문구* 만 보고
      "쿠키 파일 없음" → `CookieMissing` 으로 뭉뚱그려 매 백오프 창마다 동일한 오탐
      리페어 티켓을 새로 냈다. 같은 판정을 복제하지 않고 도메인(naver_cookie_refresher)
      에 위임 — 백오프 중이면 이슈 문구와 무관하게 `naver_login_error_type()` 결과를
      그대로 쓴다(①③).
    """
    # ★ `if platform == "naver"` 게이트를 걷어냈다 (2026-08-13, ③원칙).
    #   백오프·사람 호출이 네이버 전용이던 동안 이 가드가 정당했지만, 이제 티스토리도
    #   같은 상태기를 쓴다. 가드를 남기면 티스토리 백오프 창마다 다시
    #   `PrecheckTistoryEnvMissing` 같은 *코드 버그처럼 보이는* 타입으로 오탐 티켓이 난다.
    if backoff_reason:
        return login_error_type(platform, backoff_reason)
    txt = " ".join(issues or [])
    kind = ("CookieMissing" if "쿠키 파일 없음" in txt
            else "CookieStale" if "만료 임박" in txt
            else "CookieExpired" if "쿠키 만료" in txt
            else "EnvMissing" if "env " in txt
            else "Unknown")
    return "Precheck" + platform.capitalize() + kind


def refresh_failed_error_type(platform: str, backoff_reason: str = "") -> str:
    """자동 갱신 시도 후에도 여전히 실패 → 오류 타입. *이미 있는 판단*(플랫폼·사유)에서 파생.

    ★ 중앙 매핑표를 두지 않는다 (CLAUDE.md ERRORS [547]).
    ★ `backoff_reason` — `precheck_error_type` 과 동일 이유(위 참조)로 갱신 시도 *후* 에도
      사람이 필요한 사유(백오프·CAPTCHA)면 `naver_login_error_type()` 결과로 위임한다.
    """
    if backoff_reason:
        return login_error_type(platform, backoff_reason)
    return "Precheck" + platform.capitalize() + "AutoRefreshFailed"


def precheck_detection_error_types() -> frozenset:
    """`_alert_precheck()` 가 낼 수 있는 *감지 단계* 타입 전체 — severity.py Tier-2 판정 위임처.

    ★ 왜 필요한가 (2026-08-12, ERRORS [619]/[626]와 동일 패턴이 08-11 06:30·20:30·
      08-12 06:30·20:30 네 차례 반복된 뒤 — [625]가 남긴 "같은 wontfix 결론이 반복
      조사를 유발하면 *결론을 캐싱하는 코드* 자체가 다음 fix 대상" 교훈 적용):
      이 타입들은 *같은 `job_pre_publish_check()` 호출 안에서 곧바로
      `auto_refresh_if_needed()` 가 뒤따르는 예비 경보*([606]에서 확립)라 대부분
      그 자리에서 자동 회복된다. 회복 여부와 무관하게 매번 GUARDIAN 리페어 큐에
      들어가 "코드 결함 아님"을 사람/LLM이 반복 재확인해야 했다.
      진짜 지속 실패는 *다른* 타입(`refresh_failed_error_type()` 의
      `...AutoRefreshFailed` 또는 백오프 중 `naver_login_error_type()` 파생 CAPTCHA
      타입)으로 별도 보고되므로 이 집합과 겹치지 않는다 — 그 타입들은 그대로 Tier-2
      대상으로 남는다. 가시성도 그대로다: 텔레그램 경보(`_alert_precheck` 의
      `send_tg`)는 이 분류와 무관하게 항상 나간다. 여기서 나오는 것은 *GUARDIAN
      자동수정 큐 진입 여부* 뿐이다.
    ★ 새 issue 문구가 추가돼도 `precheck_error_type()` 자체에서 자동으로 따라온다
      (② 동적 설계 — 리터럴 목록을 두 벌로 만들지 않는다).
    """
    _sample_texts = (
        "쿠키 파일 없음 또는 빈 list",
        "만료 임박",
        "쿠키 만료 — manage 접근이 로그인으로 리다이렉트",
        "env NV_ID 누락",
        "",  # 위 어느 것도 매치 안 됨 → Unknown
    )
    return frozenset(
        precheck_error_type(plat, [txt] if txt else [])
        for plat in platforms()                       # ★ 목록을 박지 않는다(②)
        for txt in _sample_texts
    )


def _alert_refresh_failed(still_failing: dict[str, dict[str, Any]]) -> None:
    """사전점검의 *자동 갱신 시도까지* 실패했을 때 — 발행 시각 전에 미리 알린다.

    ★ 왜 필요한가 (2026-08-11, ERRORS [605][606][612]의 연장선)
      `job_pre_publish_check` 는 발행 `_COOKIE_PRECHECK_LEAD_MIN`분 전에 돌며
      `_alert_precheck()` 로 "이대로면 CAPTCHA 뜨면 건너뜁니다" *경고* 를 먼저 보낸 뒤,
      바로 이어서 `auto_refresh_if_needed()` 로 **실제 재로그인을 이미 시도**한다.
      그런데 그 시도의 성공/실패가 어디에도 남지 않았다 — CAPTCHA·계정 문제로
      실패해도 `refresh_naver_cookies`/`refresh_tistory_cookies` 는 `False` 를 조용히
      돌려줄 뿐(예외가 아니므로 `_g_report` 도 안 탄다). 그래서 사용자는 "경고만 받고
      끝났으니 알아서 복구됐겠지" 라고 오해한 채 발행 시각까지 기다리다, 그제야
      (2026-08-11 07:00:44 실측) 같은 실패를 다시 보고 CAPTCHA 를 풀 시간을
      `_COOKIE_PRECHECK_LEAD_MIN`분만큼 이미 날린 뒤였다.
    ★ 여기서 새 판정을 만들지 않는다(① 단일 진입점) — `job_pre_publish_check` 가
      이미 부른 `auto_refresh_if_needed()` 직후 `verify_all_logins()` 로 *재확인* 만 한다.
    """
    for plat, info in still_failing.items():
        issues = list(info.get("issues") or [])
        # ★ `last_login_failure()` 는 process-local 이라 요동친다(2026-08-13, ERRORS
        #   [636] — harness precondition 두 소비처에서 실측 확인된 결함이 이 세 번째
        #   소비처에도 그대로 있었다). 이 함수 호출 직전 `auto_refresh_if_needed()` 가
        #   refresh 를 *시도했다면* 갱신되지만, 백오프 창 안에서 나이 임계값(10h)
        #   미만이라 시도 자체를 건너뛴 경우엔 이전 호출이 남긴 값(또는 빈 값)을 그대로
        #   본다. 백오프 파일을 우선하는 [630]/[636] 의 단일 진입점을 그대로 재사용
        #   한다(① — 판정을 복제하지 않는다).
        #   ★ `if plat == "naver"` 게이트를 걷어냈다 (2026-08-13, ③원칙) — 티스토리도
        #     같은 상태기를 쓰므로 같은 사유·같은 안내를 받아야 한다. 지금까지 티스토리는
        #     만료돼 있어도 사유가 늘 빈 문자열이었다.
        _reason = current_login_failure_reason(plat)
        try:
            _hint = human_action_hint(plat, _reason)
            msg = (f"🚨 [발행 前 점검] {plat.upper()} 자동 갱신도 실패 — 직접 로그인 필요\n"
                   + "\n".join(f"· {i}" for i in issues)
                   + (f"\n· 사유: {_reason}" if _reason else "")
                   + "\n\n자동 재로그인을 이미 시도했으나 실패했습니다. "
                     "발행 시각 전에 지금 직접 로그인해 주세요."
                   + (f"\n\n{_hint}" if _hint else ""))
            from shared.notify import send_tg                 # noqa: PLC0415
            send_tg(msg)
        except Exception as e:                                # noqa: BLE001
            log.warning(f"[login_manager/pre_check] 갱신실패 알림 실패: {type(e).__name__}: {e}")
        try:
            from JARVIS07_GUARDIAN.error_collector import report  # noqa: PLC0415
            report(refresh_failed_error_type(plat, _reason), "publish", module=__name__,
                   func_name="job_pre_publish_check",
                   message=f"[발행 前 점검] {plat}: 자동 갱신 시도 후에도 실패 — {'; '.join(issues)}")
        except Exception as e:                                # noqa: BLE001
            log.warning(f"[login_manager/pre_check] 갱신실패 박제 실패: {type(e).__name__}: {e}")


def _advise_persistence(info: dict) -> None:
    """"지금은 되지만 내일은 아니다" 를 알린다 — **경보이지 게이트가 아니다**.

    ★ 왜 (2026-08-13 실측): 사용자가 12:26 에 *직접* 로그인했는데도 `NID_AUT`/`NID_SES`
      가 여전히 세션 쿠키(expiry 없음)였다. 세션 쿠키는 Chrome 종료와 함께 프로필에서
      증발한다 → step0(프로필 세션 재사용) 실패 → 폼 로그인 하강 → 폼 로그인은 캡차율
      실측 100%(login_stuck 캡처 10/10) → 백오프 → 자력 복귀 불가.
      즉 *수동 복구조차 반나절 뒤 같은 자리에서 죽는다*. 그 사실을 아무도 몰랐다.
    ★ 그런데 이것으로 발행을 막지는 않는다 — 막으면 오늘 당장 4조합이 전부 선다.
      `ok=True` 인 경우에만 말한다(실패한 것은 `_alert_precheck` 가 이미 말했다).
    ★ GUARDIAN 티켓을 내지 않는다 — 코드 결함이 아니라 *계정·기기 신뢰 상태* 다.
      [625][626] 이 남긴 "같은 wontfix 결론을 반복 조사하게 만들지 말 것" 교훈.
    ★ 같은 쿠키 묶음으로 두 번 말하지 않는다 — 알림 피로는 진짜 경보를 죽인다.
      dedupe 키는 쿠키 파일 mtime, 저장은 기존 `cookie_watch.json`(새 파일 금지).
    """
    if not info or not info.get("ok"):
        return
    if info.get("cookie_durable") is not False:
        return                    # True(영속) / None(판정 불가) — '모름' 을 '아님' 으로 안 읽는다
    try:
        mtime = str(NAVER_COOKIE_PATH.stat().st_mtime) if NAVER_COOKIE_PATH.exists() else ""
    except OSError:
        mtime = ""
    prev: dict = {}
    try:
        from JARVIS07_GUARDIAN.json_store import read_json  # noqa: PLC0415
        prev = read_json(_COOKIE_WATCH, default={}) or {}
    except Exception:                                     # noqa: BLE001
        prev = {}
    if mtime and str(prev.get("persist_notified_mtime") or "") == mtime:
        return
    _names = ", ".join(info.get("session_only") or ()) or "인증 쿠키"
    try:
        from shared.notify import send_tg                 # noqa: PLC0415
        send_tg("⏳ *네이버 쿠키가 세션 쿠키입니다 — 지금은 발행되지만 오래 못 갑니다*\n"
                f"· 만료시각 없는 쿠키: {_names}\n"
                "· 브라우저가 닫히면 프로필에서 사라지고, 다음 발행은 *전체 로그인* 이 됩니다.\n"
                "· 전체 로그인은 캡차를 부르고, 캡차는 무인으로 못 풉니다.\n\n"
                "다음 로그인 때 *로그인 상태 유지* 를 켜 두면 만료시각이 붙습니다.\n"
                f"{recovery_command('naver')}")
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login_manager] 지속성 안내 실패: {type(e).__name__}: {e}")
        return
    try:
        from JARVIS07_GUARDIAN.json_store import write_json  # noqa: PLC0415
        prev["persist_notified_mtime"] = mtime
        write_json(_COOKIE_WATCH, prev, backup=True)
        for _q in (_COOKIE_WATCH,
                   _COOKIE_WATCH.with_suffix(_COOKIE_WATCH.suffix + ".lock"),
                   _COOKIE_WATCH.with_suffix(_COOKIE_WATCH.suffix + ".bak")):
            try:
                if _q.exists():
                    os.chmod(_q, 0o600)
            except OSError:
                pass
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login_manager] 지속성 안내 dedupe 기록 실패: {type(e).__name__}: {e}")


def _alert_precheck(platform: str, info: dict) -> None:
    """사전점검 실패를 **사람과 원장 양쪽에** 알린다 (2026-08-09, ERRORS [594]).

    ★ 왜 (실사고): 종전엔 `log.warning` 한 줄이 전부였다. 그래서
      `naver_cookies.pkl` 이 통째로 사라진 상태로 **두 번의 발행 회차가 조용히 지나갔다**
      (08-08 21:00 테마 실패 28초 · 08-09 07:00 경제 실패 163초).
      쿠키 파일이 없으면 매 발행이 *전체 로그인* 이 되고, 그때마다 CAPTCHA 확률에 노출된다.
      즉 이 경고는 "곧 발행이 깨진다" 는 예고인데 아무도 듣지 못했다.
    """
    issues = list(info.get("issues") or [])
    # ★ 갱신 시도 *전* 이라 `last_login_failure()`(이번 프로세스 내 상태)는 아직 비어
    #   있을 수 있다 — 백오프는 파일에 먼저 있으므로 `login_backoff_active_reason()` 으로
    #   직접 확인한다(2026-08-12, ERRORS [623]~[629] 후속).
    #   ★ 백오프 상태기는 이제 플랫폼 중립이다 — `if platform == "naver"` 게이트를
    #     걷어냈다(2026-08-13, ③원칙). 판정은 여기서 만들지 않고 상태기에 묻는다(①).
    _backoff_reason = login_backoff_active_reason(platform)
    try:
        extra = ""
        if platform == "naver":
            # ★ 이슈 *문구* 가 아니라 **지금 관측** 을 권위로 삼는다.
            #   문구는 다른 시점의 판단이라, 그대로 믿으면 "파일 없음" 과 "현재 존재" 가
            #   한 알림에 같이 실린다(실측으로 확인). 어긋나는 경보는 신뢰를 깎는다.
            _seen = record_cookie_sighting()
            if not _seen.get("present"):
                extra = "\n" + cookie_loss_window()
        # ★ 사람이 풀어야 하는 상태면 **해제 조건까지** 적는다 (사용자 지시 2026-08-13).
        #   종전 문구는 "자동 재시도 N시간 보류" 에서 끝나 사용자가 "기다리면 되나" 로
        #   읽었다. 해제 주체는 시간이 아니라 `clear_login_backoff()` — 즉 성공한 로그인이다.
        _hint = human_action_hint(platform, _backoff_reason)
        msg = (f"⚠️ *[발행 前 점검] {platform.upper()} 인증 이상*\n"
               + "\n".join(f"· {i}" for i in issues) + extra
               + (f"\n· 사유: {_backoff_reason}" if _backoff_reason else "")
               + "\n\n이대로면 발행 시각에 *전체 로그인* 을 시도하고, CAPTCHA 가 뜨면 건너뜁니다."
               + (f"\n\n{_hint}" if _hint else ""))
        from shared.notify import send_tg                 # noqa: PLC0415
        send_tg(msg)
    except Exception as e:                                # noqa: BLE001
        log.warning(f"[login_manager/pre_check] 알림 실패: {type(e).__name__}: {e}")
    try:
        from JARVIS07_GUARDIAN.error_collector import report  # noqa: PLC0415
        report(precheck_error_type(platform, issues, _backoff_reason), "publish", module=__name__,
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
        failing = [plat for plat, info in verify.items() if not info["ok"]]
        for plat in failing:
            log.warning(f"[login_manager/pre_check] {plat}: {verify[plat]['issues']}")
            _alert_precheck(plat, verify[plat])            # ★ 로그로만 끝내지 않는다
        # ★ 통과했어도 *오래 못 갈* 상태면 미리 말한다 (게이트 아님 — 위 주석 참조).
        _advise_persistence(verify.get("naver") or {})
        # 자동 갱신
        auto_refresh_if_needed()
        # ★ 갱신 시도 후 재확인 — 실패가 발행 시각까지 조용히 묻히지 않게 한다
        #   (2026-08-11, [605][606][612] 연장선 — 상세는 _alert_refresh_failed 참조).
        if failing:
            recheck = verify_all_logins(platforms=tuple(failing))
            still_failing = {p: i for p, i in recheck.items() if not i["ok"]}
            if still_failing:
                _alert_refresh_failed(still_failing)
    elif platform in ("naver", "tistory"):
        # ★ 2026-08-10 — ERRORS [596][597]과 같은 병이 이 분기에도 있었다: 나이/존재만
        #   보고 "만료됐지만 값은 남아있는" 쿠키를 놓쳤다(오늘 실제 사고: TS_COOKIE 는
        #   있었지만 manage 접근이 로그인으로 리다이렉트). 실유효성 판정은
        #   `auto_refresh_if_needed()` 가 이미 단독 소유하고 있다 — 여기서 판정 로직을
        #   복제하지 않고 그 단일 진입점에 위임한다(① 단일 진입점).
        auto_refresh_if_needed(platforms=(platform,))


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
        if info.get("cookie_durable") is False:
            print(f"   ⏳ 세션 쿠키(브라우저 종료 시 증발): "
                  f"{', '.join(info.get('session_only') or ()) or '?'}")
        _bo = login_backoff_reason(plat)
        if _bo:
            print(f"   ⏸ {_bo}")
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
    "precheck_detection_error_types",
    "network_up",
    "ensure_naver_ready",
    # ── 로그인 상태기 (플랫폼 중립 — 네이버·티스토리 공용, 2026-08-13 승격) ──
    "platforms",
    "BACKOFF_REASON",
    "LOGIN_BACKOFF_SEC",
    "HUMAN_WAIT_SEC",
    "LOGIN_STUCK_DIR",
    "mark_login_backoff",
    "clear_login_backoff",
    "login_backoff_active_reason",
    "login_backoff_reason",
    "current_login_failure_reason",
    "human_required_reasons",
    "login_error_type",
    "login_invalid_kind",
    "is_human_required_login_kind",
    "alert_human_login_needed",
    "unblock_hint",
    "recovery_command",
    "human_action_hint",
    "human_wait_sec",
    "captcha_present",
    "human_challenge_present",
    "capture_login_stuck",
]


if __name__ == "__main__":
    import sys
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — 인증 직접 실행 시 환경 검증
    # ★ try/except 로 감싸지 않는다 (2026-08-10) — 감싸는 순간 ImportError 가 삼켜져
    #   "preflight 가 있다" 는 착각만 남고 **실제로는 한 번도 안 도는** 상태가 된다.
    #   실측(2026-08-10): 진입점 16곳 중 8곳이 그 상태였고, 경고는 stdout 으로만 나가는데
    #   데몬 stdout 은 /dev/null 이라 어디에도 안 남았다 — 완전한 침묵이었다.
    #   루트 경로는 파일 상단 부트스트랩이 보장한다. 여기서 실패하면 진짜 환경 문제다(fail-closed).
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight(strict=True)

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
