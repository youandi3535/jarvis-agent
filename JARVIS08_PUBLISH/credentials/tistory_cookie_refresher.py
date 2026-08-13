"""
tistory_cookie_refresher.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
티스토리 TSSESSION 쿠키 자동 갱신
- 티스토리 작성 직전 자동 호출 (force=False — 유효 시 스킵, 만료 시 갱신)
- 현재 쿠키 유효성 체크
- 만료 시 카카오 ID/PW 자동 입력으로 로그인 후 쿠키 갱신
- .env 파일 자동 업데이트 (TS_COOKIE)
- 2FA·CAPTCHA·디바이스 인증 감지 → 텔레그램 즉시 알림
- 일시 장애 시 자동 재시도 (최대 3회)
- 성공/실패 결과 텔레그램 알림

사용법:
  python -m JARVIS08_PUBLISH.credentials.tistory_cookie_refresher          # 쿠키 체크
  python -m JARVIS08_PUBLISH.credentials.tistory_cookie_refresher --force   # 강제 갱신
  python  JARVIS08_PUBLISH/credentials/tistory_cookie_refresher.py --manual  # 사람이 직접 로그인
  from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run
  ok = run(force=False)                       # 일반 체크
  ok, driver = run(force=True, return_driver=True)   # 강제 + driver 재사용
"""

import os
import sys
import time
import socket
import subprocess
from pathlib import Path
from dotenv import load_dotenv


def _is_network_up() -> bool:
    """인터넷 연결 사전 확인 (Chrome 시작 전) — 판정 본체는 `login_manager.network_up()`.

    ★ 종전엔 이 본체가 여기와 `naver_cookie_refresher` 양쪽에 똑같이 복사돼 있었다(2벌).
      로그인 진입점이 하나이므로 그 전제 판정도 하나다 (LOGIN_SUPREME_LAW 단일 진입점).
    """
    from JARVIS08_PUBLISH.credentials.login_manager import network_up
    return network_up()

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

# ── 텔레그램 알림 ────────────────────────────────────
def _tg_notify(msg: str) -> None:
    """텔레그램 알림 (실패 무시). 쿠키 갱신 결과 사용자 즉시 통보용."""
    try:
        from shared.notify import send_tg
        send_tg(msg)
    except Exception:
        pass
# ─────────────────────────────────────────────────────

load_dotenv()

# ★ 발행 점검 (2026-05-17) — ADR 008 Phase 2-3 이관 후 경로 anchor 보강.
# 옛: BASE_DIR = Path(__file__).parent (JARVIS02_WRITER/) → ENV_FILE = ../env = 루트 .env
# 새: __file__ 는 JARVIS08_PUBLISH/credentials/ → ../../env 로 *루트* 까지 두 단계 위로.
BASE_DIR     = Path(__file__).parent
_PROJECT_ROOT = BASE_DIR.parent.parent       # credentials/ → JARVIS08_PUBLISH/ → root
ENV_FILE     = _PROJECT_ROOT / '.env'        # 루트 .env (공유 자원)
IS_MAC       = sys.platform == "darwin"

TS_URL       = os.getenv("TS_URL", "")
TS_BLOG      = TS_URL.replace("https://", "").replace("http://", "").split(".")[0] if TS_URL else ""
TS_USERNAME  = os.getenv("TS_USERNAME", "")
TS_PASSWORD  = os.getenv("TS_PASSWORD", "")

_RETRY_MAX = 3            # 일시 장애 자동 재시도 횟수
_RETRY_DELAY_SEC = 5      # 재시도 간 대기

# ══════════════════════════════════════════════════════════════════
# ★ 사람 호출·백오프·실패 사유 — 상태기는 `login_manager` 한 벌뿐 (2026-08-13, ③원칙)
# ══════════════════════════════════════════════════════════════════
#
# ★ 종전 이 파일에는 그런 경로가 **0곳** 이었다. 네이버만 백오프·사람 호출·실패 사유·
#   캡차 판정을 갖고 있어서, 티스토리는 쿠키가 만료돼 있어도 사람에게 갈 길이 없었다.
#   `verify_all_logins()` 는 ok=False 를 알고 있었는데 그 다음이 없었다(2026-08-13 실측).
# ★ **네이버 코드를 여기 복사하지 않는다**(①). 상태기는 `login_manager` 로 승격됐고
#   양쪽이 그 한 벌을 쓴다. 여기 남는 것은 *이 플랫폼 고유의 것* 뿐 —
#   어떤 사유가 사람을 필요로 하는가(`HUMAN_REQUIRED_REASONS`)와
#   이번 프로세스의 마지막 실패(`_LAST_FAILURE`) 두 가지다.
PLATFORM = "tistory"


def _lm():
    """로그인 상태기 단일 진입점 — **함수 지역 import**(모듈 초기화 순환 회피).

    `login_manager` 는 모듈 로드 시 네이버 refresher 에서 쿠키 경로를 받아온다.
    여기서 모듈 레벨로 붙잡으면 import 순서에 따라 부분 초기화 모듈을 잡을 수 있다 —
    같은 이유로 `_is_network_up()` 도 지역 import 다.
    """
    from JARVIS08_PUBLISH.credentials import login_manager as _m
    return _m


# ★ 이 사유들은 *사람이 화면 앞에 있어야만* 풀린다 — 코드 수정으로 해결 불가.
#   네이버의 `CAPTCHA_REASONS` 와 **동렬**이고, `login_manager.human_required_reasons()`
#   가 이 이름을 규약으로 읽어 `backoff` 를 얹는다(② 파생 — 목록을 두 벌로 만들지 않는다).
#   `credentials_missing`·`login_unconfirmed` 같은 *진짜 결함일 수 있는* 사유는 여기 없다 —
#   GUARDIAN Tier-2 가 계속 잡아야 사람이 알아챈다.
HUMAN_REQUIRED_REASONS = frozenset({"human_intervention", "human_timeout"})

# ── 마지막 실패 사유 ────────────────────────────────────────────────
#
# ★ 왜 반환형(bool/str|None)을 안 바꾸나: 호출자를 전부 손대야 하고 하나라도 놓치면
#   조용히 깨진다. 사유는 옆문으로 노출한다 — 필요한 호출자만 읽는다(네이버와 동형).
_LAST_FAILURE: str = ""
_LAST_SHOT: str = ""      # 마지막 로그인 정지 화면(사람 호출에 동봉)


def _fail(reason: str, msg: str = "") -> None:
    """실패를 기록하고 None 을 돌려준다 — **사유를 잃지 않는 유일한 출구**.

    ★ 종전엔 `return None`·`return False` 가 열 곳 넘게 흩어져 있어 *무엇 때문에*
      실패했는지가 어디에도 남지 않았다. 그래서 티스토리 실패는 늘 "그냥 실패" 였다.
    ★ 사람이 필요한 실패는 여기 한 곳에서 백오프+호출을 처리한다(①) —
      분기마다 흩뿌리면 새 사유가 생길 때 또 샌다.
    """
    global _LAST_FAILURE
    _LAST_FAILURE = reason
    if msg:
        print(msg)
    try:
        lm = _lm()
        if reason in lm.human_required_reasons(PLATFORM):
            lm.mark_login_backoff(PLATFORM, reason)
            lm.alert_human_login_needed(PLATFORM, reason, _LAST_SHOT)
    except Exception as _e:                              # noqa: BLE001
        print(f"  ⚠️ 사람 호출/백오프 처리 실패: {_e}")
    return None


def last_login_failure() -> str:
    """직전 티스토리 로그인 실패 사유 (성공했거나 시도 전이면 "").

    `login_manager.current_login_failure_reason("tistory")` 가 이 이름을 규약으로 읽는다.
    """
    return _LAST_FAILURE


def _check_env_vars() -> tuple[bool, str]:
    """필수 .env 변수 점검. (ok, 누락 변수명) 반환."""
    missing = []
    if not TS_URL:      missing.append("TS_URL")
    if not TS_USERNAME: missing.append("TS_USERNAME")
    if not TS_PASSWORD: missing.append("TS_PASSWORD")
    if missing:
        return False, ", ".join(missing)
    return True, ""


# ══════════════════════════════════════════
#  드라이버
# ══════════════════════════════════════════

def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_argument("--window-position=0,0")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # 비밀번호 저장 팝업 비활성화
    opts.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    driver.implicitly_wait(5)
    return driver


def _chrome_focus():
    if IS_MAC:
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to activate'],
            capture_output=True
        )
        time.sleep(0.5)


def _s(sec=1.0):
    time.sleep(sec)


# ══════════════════════════════════════════════════════════════
#  ★ 내 블로그 강제 이동 헬퍼 (사용자 박제 2026-05-14) — ERRORS [94][95]
#  카카오 계정에 *다른 블로그* (the3rdfloor 등) 도 연결돼 있어
#  로그인 후 자동 리다이렉트 → Selenium 멈춤·발행 차단.
#  3-단계 방어: ① 즉시 navigate ② URL 검증 retry ③ 실패 시 텔레그램 SOS
# ══════════════════════════════════════════════════════════════

def force_my_blog(driver, *, max_retry: int = 3, wait_sec: float = 2.0,
                  timeout_sec: float = 10.0) -> bool:
    """현재 위치를 *내 블로그* (TS_BLOG.tistory.com) 로 강제 이동·검증.

    동작:
      1. 현재 URL 이 이미 내 블로그면 즉시 True
      2. driver.get(my_blog_url) → URL 검증 retry (최대 max_retry 회)
      3. 매 시도마다 page_load_timeout 으로 멈춤 차단
      4. 끝까지 실패 시 텔레그램 SOS + False

    Args:
        driver:       Selenium WebDriver
        max_retry:    재시도 횟수 (기본 3)
        wait_sec:     navigate 후 대기 (기본 2초)
        timeout_sec:  단일 navigate 페이지 로드 타임아웃 (기본 10초)

    Returns:
        bool — 내 블로그 도달 성공 여부
    """
    if not TS_BLOG:
        print("  ⚠️ TS_BLOG 미설정 — force_my_blog skip")
        return True   # 설정 없으면 검증 skip (실패 아님)

    my_url = f"https://{TS_BLOG}.tistory.com"
    try:
        cur = driver.current_url or ""
    except Exception:
        cur = ""

    if f"{TS_BLOG}.tistory.com" in cur:
        return True   # 이미 내 블로그

    print(f"  🔁 [force_my_blog] 강제 이동 시작 (현재 URL: {cur[:70]})")

    # page_load_timeout 적용 (멈춤 차단)
    try:
        driver.set_page_load_timeout(timeout_sec)
    except Exception:
        pass

    for attempt in range(1, max_retry + 1):
        try:
            driver.get(my_url)
            _s(wait_sec)
            cur = driver.current_url or ""
            if f"{TS_BLOG}.tistory.com" in cur and "the3rdfloor" not in cur:
                print(f"  ✅ [force_my_blog] 도달 성공 (시도 {attempt}/{max_retry})")
                return True
            print(f"  ⚠️ [force_my_blog] 시도 {attempt} — 여전히 다른 블로그: {cur[:70]}")
        except Exception as e:
            print(f"  ⚠️ [force_my_blog] 시도 {attempt} 예외: {e}")
            try:
                # 멈춤 회복 — 현재 페이지 강제 stop
                driver.execute_script("window.stop();")
            except Exception:
                pass
        _s(wait_sec)

    # 끝까지 실패 — 텔레그램 SOS
    final_url = ""
    try:
        final_url = driver.current_url or ""
    except Exception:
        pass
    print(f"  ❌ [force_my_blog] 최종 실패 — 도달 못 함 (final URL: {final_url[:70]})")
    _tg_notify(
        f"🚨 *티스토리 다른 블로그 잔류*\n"
        f"카카오 계정에 *다른 블로그* 도 연결돼 있어 `{TS_BLOG}` 도달 실패.\n"
        f"현재: `{final_url[:80]}`\n\n"
        f"*조치*: https://www.tistory.com/member 접속 → "
        f"`{TS_BLOG}` 를 *기본 블로그로 설정* 또는 다른 블로그 *연결 해제*."
    )
    return False


# ══════════════════════════════════════════
#  쿠키 유효성 체크
# ══════════════════════════════════════════

def is_login_redirect(url: str) -> bool:
    """이 URL 이 '로그인으로 튕겼다' 는 뜻인가 — **판정 규칙의 단일 소유자**.

    ★ ERRORS [292] 가 확립한 진실: *공개 블로그 홈* 은 비로그인에도 열리므로
      "블로그명이 페이지에 있나" 같은 휴리스틱은 만료 쿠키를 유효로 오판한다.
      **manage 진입이 로그인으로 리다이렉트되는가** 가 유일한 진실이다.
    ★ 이 함수를 만든 이유(2026-08-09, ERRORS [596]): 같은 판정이 곧 두 곳에서 필요해졌다 —
      셀레니움 경로(`check_cookie_valid`)와 **브라우저 없는 경로**(`cookie_valid_http`).
      규칙을 복제하면 한쪽만 고쳐질 때 재발한다(① 단일 진입점).
    """
    u = (url or "").lower()
    return "/auth/login" in u or "accounts.kakao.com" in u


def cookie_valid_http(timeout: float = 8.0, *, detail: bool = False) -> "bool | None | tuple[bool | None, str]":
    """브라우저 없이 TSSESSION 유효성 판정 — 네이버 `check_cookie_valid()` 와 **대칭**.

    ★ 왜 필요한가 (2026-08-09 실사고, ERRORS [596])
      `verify_all_logins()` 는 티스토리를 **env 변수 '존재' 로만** 판정했다. 그래서
      08-08 21:00 테마 발행 직전 사전점검이 초록이었는데 실제 쿠키는 만료돼 있었고,
      발행은 28초 만에 로그인 화면으로 튕겨 끝났다(실측). 네이버는 requests 로 실검증을
      하는데 티스토리만 안 했다 — **원칙③(4조합 전부) 위반이 여기 있었다.**
      브라우저를 띄우면 상태점검·텔레그램 `/status` 까지 무거워지므로 HTTP 로 판정한다.

    Returns: True(유효) / False(만료) / None(판정 불가)
      · **None 을 False 로 뭉개지 않는다.** '모른다' 를 '만료' 로 적으면 거짓 경보가 된다.
      · `detail=True` 면 `(verdict, reason)` — reason 은 '모름' 의 **종류** 다.
        ★ 왜 종류가 필요한가 (2026-08-13): `None` 에는 성격이 다른 둘이 섞여 있다.
          - `"network"` — 순단·타임아웃. **아무 것도 하면 안 된다.** 순단마다 로그인하면
            그게 캡차를 부른다(네이버가 그렇게 무너졌다).
          - `"indeterminate"` — 응답은 정상인데 **이 방식으로는 원리적으로 못 가린다**
            (유효 쿠키도 로그인 리다이렉트. 엔드포인트 6종·리다이렉트 추적·도메인 쿠키
             실측 전부 유효=무효 동일). 이때는 *정확한 판정자*(브라우저)에게 물어야 한다.
          둘을 뭉개면 ① 순단마다 로그인(캡차 유발) 또는 ② 만료를 영영 못 잡음 중 하나가 된다.
    """
    def _r(v, why):
        return (v, why) if detail else v
    ck = os.getenv("TS_COOKIE", "").strip('"').strip("'")
    if not ck:
        return _r(False, "empty")
    try:
        import requests as _req                          # noqa: PLC0415
        r = _req.get(f"https://{TS_BLOG}.tistory.com/manage/newpost/",
                     cookies={"TSSESSION": ck}, timeout=timeout,
                     allow_redirects=False)
        loc = r.headers.get("Location", "")
        if r.status_code in (301, 302, 303, 307, 308):
            if not is_login_redirect(loc):
                return _r(True, "ok")
            # ★ 로그인 리다이렉트를 **만료로 단정하지 않는다** (2026-08-13 실측 정정)
            #   같은 쿠키를 브라우저(`check_cookie_valid`)는 정상으로 본다. 실측:
            #     최근 3일 이 판정이 "만료" 6회 → 그 직후 발행 **6회 전부 성공**
            #     (08-10 07:24 · 21:30 / 08-11 21:32 / 08-12 07:27 · 21:39 / 08-13 07:28)
            #   직접 요청해 보니 브라우저 UA 를 붙여도 302 → /auth/login 이었다.
            #   즉 `TSSESSION` 단독 HTTP 로는 manage 에 **도달할 수 없는 것이 정상** 이고,
            #   이 경로로는 유효/만료를 가릴 수 없다. 그런데 그 무능을 '만료' 로 적어
            #   하루 2회 거짓 경보(`PrecheckTistoryCookieExpired`)를 냈다.
            #   → 이 함수 자신의 계약("'모른다' 를 '만료' 로 적으면 거짓 경보가 된다")대로 None.
            #   판정이 필요하면 소비자와 같은 방식인 `check_cookie_valid(driver)` 를 쓴다(①).
            return _r(None, "indeterminate")
        return _r(r.status_code < 400, "ok" if r.status_code < 400 else "http_error")
    except Exception as e:                               # noqa: BLE001
        print(f"  ⚠️ 티스토리 쿠키 HTTP 판정 불가: {type(e).__name__}: {e}")
        return _r(None, "network")


def check_cookie_valid(driver) -> bool:
    """현재 TSSESSION 쿠키로 로그인 유지되는지 확인"""
    ts_cookie = os.getenv("TS_COOKIE", "")
    if not ts_cookie:
        print("  ⚠️ TS_COOKIE가 .env에 없음")
        return False

    try:
        driver.get("https://www.tistory.com")
        _s(3)
        driver.delete_all_cookies()
        driver.add_cookie({
            "name": "TSSESSION",
            "value": ts_cookie,
            "domain": ".tistory.com",
            "path": "/",
        })
        driver.refresh()
        _s(3)
        # ★ 강제 이동 — 다른 블로그(the3rdfloor) 잔류·멈춤 완전 차단 (사용자 박제)
        force_my_blog(driver)

        # ★ 로그인 *필수* 페이지로 판정 (ERRORS [292] — 2026-07-03): 종전 'TS_BLOG in page'
        #   휴리스틱은 *공개 블로그 홈* 검사라 비로그인에도 블로그명이 항상 포함 → 만료
        #   쿠키가 유효 판정되는 오탐. manage 진입 시 /auth/login 리다이렉트 여부가 진실.
        try:
            from selenium.common.exceptions import UnexpectedAlertPresentException

            def _dismiss_alert_if_any():
                """열려 있는 alert (임시저장 팝업 등) 를 dismiss. 없으면 no-op."""
                try:
                    driver.switch_to.alert.dismiss()
                    _s(1)
                except Exception:
                    pass

            try:
                driver.get(f"https://{TS_BLOG}.tistory.com/manage/newpost/")
                _s(3)
            except UnexpectedAlertPresentException:
                _dismiss_alert_if_any()

            # ★ current_url 접근 시에도 alert 잔류 가능 (편집기 임시저장 confirm dialog)
            _cur = ""
            try:
                _cur = (driver.current_url or "").lower()
            except UnexpectedAlertPresentException:
                _dismiss_alert_if_any()
                try:
                    _cur = (driver.current_url or "").lower()
                except Exception:
                    _cur = ""

            if is_login_redirect(_cur):        # 규칙은 한 곳(is_login_redirect)만 소유
                print("  ❌ 쿠키 만료 — manage 진입이 로그인으로 리다이렉트")
                return False
            print("  ✅ 쿠키 유효 — manage 페이지 접근 정상")
            return True
        except Exception as _me:
            print(f"  ⚠️ manage 판정 오류({_me}) — 보수적으로 만료 처리")
            return False
    except Exception as e:
        print(f"  ❌ 쿠키 체크 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False


# ══════════════════════════════════════════
#  카카오 ID/PW 로그인 → 쿠키 추출
# ══════════════════════════════════════════

def refresh_cookie(driver) -> str | None:
    """카카오 ID/PW 자동 입력으로 로그인 후 TSSESSION 쿠키 추출"""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys

    print("  🔄 카카오 로그인 시작...")

    if not TS_USERNAME or not TS_PASSWORD:
        return _fail("credentials_missing",
                     "  ❌ .env에 TS_USERNAME 또는 TS_PASSWORD가 없습니다.")

    try:
        # 1. 티스토리 메인 접속 (쿠키 초기화)
        driver.get("https://www.tistory.com")
        driver.delete_all_cookies()
        driver.refresh()
        _s(3)

        # 2. 카카오계정으로 시작하기 버튼 클릭 (우상단)
        print("  🖱️ 카카오계정으로 시작하기 클릭...")
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(text(),'카카오계정으로 시작하기')]"
                ))
            )
            driver.execute_script("arguments[0].click()", btn)
        except:
            driver.execute_script("""
                var els = document.querySelectorAll('a, button');
                for (var e of els) {
                    if (e.innerText && e.innerText.includes('카카오계정으로 시작하기')) {
                        e.click(); break;
                    }
                }
            """)
        _s(3)

        # 3. 팝업에서 카카오계정으로 로그인 버튼 클릭
        print("  🖱️ 카카오계정으로 로그인 클릭...")
        try:
            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH,
                    "//*[contains(text(),'카카오계정으로 로그인')]"
                ))
            )
            driver.execute_script("arguments[0].click()", login_btn)
        except:
            driver.execute_script("""
                var els = document.querySelectorAll('a, button');
                for (var e of els) {
                    if (e.innerText && e.innerText.includes('카카오계정으로 로그인')) {
                        e.click(); break;
                    }
                }
            """)
        _s(4)

        # 4. 카카오 로그인 페이지 — ID/PW 입력
        print(f"  ✏️ 카카오 ID 입력: {TS_USERNAME}")
        _chrome_focus()

        # 이메일 입력란 찾기
        try:
            email_input = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR,
                    "input#loginId, input[name='loginId'], input[type='email'], input[placeholder*='이메일'], input[placeholder*='아이디']"
                ))
            )
            email_input.clear()
            email_input.send_keys(TS_USERNAME)
            _s(0.5)
            print("  ✅ 이메일 입력 완료")
        except Exception as e:
            # ★ 2026-08-13: 종전 이 자리는 `attempt=attempt` 를 넘겼는데 `attempt` 는
            #   `run()` 의 지역변수라 이 함수에는 없다 — 예외 처리 도중 NameError 가 나서
            #   *원래 오류가 통째로 가려졌다*. 실패를 삼키는 실패였다.
            _g_report("writer", e, module=__name__, max_attempts=_RETRY_MAX)
            return _fail("login_form_email_missing", f"  ❌ 이메일 입력란 못 찾음: {e}")

        # 비밀번호 입력란 찾기
        try:
            pw_input = driver.find_element(By.CSS_SELECTOR,
                "input#password, input[name='password'], input[type='password']"
            )
            pw_input.clear()
            pw_input.send_keys(TS_PASSWORD)
            _s(0.5)
            print("  ✅ 비밀번호 입력 완료")
        except Exception as e:
            _g_report("writer", e, module=__name__)
            return _fail("login_form_password_missing", f"  ❌ 비밀번호 입력란 못 찾음: {e}")

        # 5. 로그인 버튼 클릭
        print("  🖱️ 로그인 버튼 클릭...")
        try:
            submit_btn = driver.find_element(By.CSS_SELECTOR,
                "button[type='submit'], button.btn_g.highlight, .btn_login"
            )
            driver.execute_script("arguments[0].click()", submit_btn)
        except:
            pw_input.send_keys(Keys.RETURN)
        _s(5)

        # 6. "아이디/비밀번호 저장" 팝업 닫기
        print("  🔍 저장 팝업 확인 중...")
        for _ in range(5):
            try:
                # "나중에 할게요" 또는 "안함" 버튼 클릭
                dismiss_btn = driver.find_element(By.XPATH,
                    "//*[contains(text(),'나중에') or contains(text(),'안함') or "
                    "contains(text(),'하지 않음') or contains(text(),'취소')]"
                )
                driver.execute_script("arguments[0].click()", dismiss_btn)
                print("  ✅ 저장 팝업 닫기 완료")
                _s(1)
                break
            except:
                _s(1)

        # 7. 로그인 후 티스토리로 리다이렉트 대기 (+ 2FA/CAPTCHA 감지)
        print(f"  🔍 현재 URL: {driver.current_url[:60]}")
        for _ in range(15):
            url = driver.current_url
            if "tistory.com" in url and "accounts.kakao" not in url:
                print("  ✅ 티스토리로 이동 완료")
                break
            # ★ 2FA·기기인증·캡차 감지 — **낱말이 아니라 꼴** 로 판정한다(② / ERRORS [595]).
            #   종전엔 `_HUMAN_INTERVENTION_KEYWORDS` 낱말 나열이었다. 낱말 판정은 이미
            #   한 번 무너졌다 — 캡차 *없는* 평상시 로그인 페이지에도 `captcha`·`보안` 이
            #   들어 있어 판정이 항상 참이었다. 판정 본체는 `login_manager` 단독(①).
            #   `is True` 로만 받는다 — '모름'(None)을 '차단' 으로 단정하지 않는다.
            if _lm().human_challenge_present(driver) is True:
                # ★ 티스토리 정지 화면 증거는 지금까지 **0장** 이었다. 추측을 실측으로
                #   바꿀 유일한 재료라 반드시 남긴다(네이버가 그렇게 선택자를 고쳤다).
                _shot = _lm().capture_login_stuck(driver, PLATFORM, "human_challenge")
                globals()["_LAST_SHOT"] = _shot or ""
                # ★ 3분(36×5초) 하드코딩 폐지 — 무인이면 0초다(플랫폼 무관 파생).
                #   화면 앞에 아무도 없는데 3분을 버리는 것은 네이버가 482초를 버린
                #   것과 같은 낭비다(ERRORS [615]). 무인이면 즉시 사람을 부른다.
                _wait = _lm().human_wait_sec()
                if _wait <= 0:
                    return _fail("human_intervention",
                                 "  ❌ 추가 인증 화면 확인 — 무인 실행이라 대기하지 않는다 "
                                 "(사람이 직접 로그인해야 함)")
                print(f"  🚨 추가 인증 필요 — Chrome 창에서 직접 완료해 주세요 (최대 {_wait}초 대기)")
                _deadline = time.time() + _wait
                while time.time() < _deadline:
                    _s(5)
                    _cur = driver.current_url
                    if "tistory.com" in _cur and "accounts.kakao" not in _cur:
                        print("  ✅ 사용자가 추가 인증 완료 — 티스토리로 이동 확인")
                        break
                else:
                    return _fail("human_timeout", f"  ❌ {_wait}초 내 인증 완료 안 됨 — 종료")
                break  # outer loop 탈출 (tistory 이동 확인됨)
            _s(2)

        if "accounts.kakao" in driver.current_url:
            _shot = _lm().capture_login_stuck(driver, PLATFORM, "kakao_stuck")
            globals()["_LAST_SHOT"] = _shot or ""
            if _lm().human_challenge_present(driver) is True:
                return _fail("human_intervention",
                             "  ❌ 로그인 실패 — 카카오가 추가 인증을 요구한다")
            # ★ '모름' 을 '사람 필요' 로 단정하지 않는다 — 자격증명·DOM 변경일 수도 있어
            #   GUARDIAN Tier-2 가 계속 봐야 한다. 대신 화면을 남겨 판정을 고칠 재료로 둔다.
            return _fail("login_unconfirmed",
                         "  ❌ 로그인 실패 — 카카오 페이지에 머물러 있음 "
                         f"(추가 인증 여부 판정 불가, 저장된 화면: {_shot or '저장 실패'})")

        print("  ✅ 로그인 완료!")
        _s(3)

        # ★ 강제 이동 (검증 retry + 멈춤 차단 + SOS) — 사용자 박제 2026-05-14
        # 카카오 계정에 다른 블로그(the3rdfloor) 연결 시 자동 리다이렉트 발생.
        force_my_blog(driver)

        # 8. TSSESSION 쿠키 추출 (현재 페이지)
        cookies = driver.get_cookies()
        for cookie in cookies:
            if cookie['name'] == 'TSSESSION':
                new_cookie = cookie['value']
                print(f"  ✅ TSSESSION 추출 성공: {new_cookie[:20]}...")
                return new_cookie

        # tistory.com으로 직접 이동 후 재시도
        print("  🔄 tistory.com으로 이동 후 쿠키 재시도...")
        driver.get("https://www.tistory.com")
        _s(3)
        cookies = driver.get_cookies()
        for cookie in cookies:
            if cookie['name'] == 'TSSESSION':
                new_cookie = cookie['value']
                print(f"  ✅ TSSESSION 추출 성공 (2차): {new_cookie[:20]}...")
                return new_cookie

        # 전체 쿠키 목록 출력 (디버깅용)
        print("  ⚠️ 전체 쿠키 목록:")
        for c in cookies:
            print(f"    - {c['name']}: {str(c['value'])[:30]}")

        return _fail("cookie_extract_failed", "  ❌ TSSESSION 쿠키를 찾지 못함")

    except Exception as e:
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        return _fail(f"exception_{type(e).__name__}", f"  ❌ 로그인 오류: {e}")


# ══════════════════════════════════════════
#  .env 업데이트
# ══════════════════════════════════════════

def update_env_cookie(new_cookie: str) -> bool:
    """TS_COOKIE 값을 .env 파일에서 업데이트"""
    try:
        if not ENV_FILE.exists():
            print(f"  ❌ .env 파일 없음: {ENV_FILE}")
            return False

        content = ENV_FILE.read_text(encoding='utf-8')

        if 'TS_COOKIE=' in content:
            lines     = content.splitlines()
            new_lines = []
            for line in lines:
                if line.startswith('TS_COOKIE='):
                    new_lines.append(f'TS_COOKIE={new_cookie}')
                else:
                    new_lines.append(line)
            new_content = '\n'.join(new_lines)
        else:
            new_content = content.rstrip() + f'\nTS_COOKIE={new_cookie}\n'

        ENV_FILE.write_text(new_content, encoding='utf-8')
        os.environ['TS_COOKIE'] = new_cookie
        # ★ 저장에 성공했다 = 로그인이 실제로 됐다 → 사람 호출 상태를 푼다(단일 지점).
        #   네이버는 `_save_cookies()` 가 같은 일을 한다 — 해제 조건도 대칭이다(③).
        #   이 한 줄이 안내문의 "직접 로그인하면 즉시 해제" 를 사실로 만든다.
        try:
            _lm().clear_login_backoff(PLATFORM)
        except Exception:                                # noqa: BLE001
            pass
        print(f"  ✅ .env 업데이트 완료")
        return True

    except Exception as e:
        print(f"  ❌ .env 업데이트 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False


# ══════════════════════════════════════════
#  메인
# ══════════════════════════════════════════

def _attempt_once(force: bool, return_driver: bool):
    """1회 시도 — run() 의 단일 시도 내부 헬퍼. (ok, driver_or_None) 반환."""
    driver = None
    try:
        driver = _make_driver()

        if not force:
            print("\n🔍 현재 쿠키 유효성 확인 중...")
            if check_cookie_valid(driver):
                print("  ✅ 쿠키 정상 — 갱신 불필요")
                try:                                     # 유효 = 사람이 필요 없다 → 해제
                    _lm().clear_login_backoff(PLATFORM)
                except Exception:                        # noqa: BLE001
                    pass
                try: driver.quit()
                except Exception: pass
                driver = None
                return True, None   # 포스터가 TS_COOKIE 직접 사용
            print("  ⚠️ 쿠키 만료 — 자동 갱신 시작")
        else:
            print("  🔄 강제 갱신 모드")

        new_cookie = refresh_cookie(driver)
        if not new_cookie:
            try: driver.quit()
            except Exception: pass
            driver = None
            return False, None

        if update_env_cookie(new_cookie):
            print("\n✅ 티스토리 쿠키 갱신 완료!")
            if return_driver:
                return True, driver
            # ★ 다른 3개 quit() 호출과 대칭 — 여기만 무가드였다 (2026-08-10).
            #   .env 는 이미 갱신됐는데 quit() 이 던지면(예: chromedriver 가 이미 죽어
            #   "Connection refused") 바깥 except 가 이걸 "시도 실패" 로 오판 → 이미 성공한
            #   갱신을 또 재시도(카카오 재로그인, CAPTCHA 위험) + 거짓 실패 알림.
            try:
                driver.quit()
            except Exception:
                pass
            driver = None
            return True, None
        else:
            _fail("env_update_failed", "\n❌ .env 업데이트 실패")
            return False, None

    except Exception as e:
        print(f"  ❌ 시도 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False, None
    finally:
        if driver and not return_driver:
            try:
                _s(1)
                driver.quit()
            except Exception:
                pass


# ── ★ 멱등성 가드 (사용자 박제 2026-06-07 — ERRORS [264])
# 동시 발행 잡 + harness 재시도 + incident_responder 가 같은 60초 안에 run() 다중 호출 →
# 텔레그램 "✅ 티스토리 쿠키 갱신 성공" 알림 N회 중복 발송. 60초 안에 이미 성공했으면 skip.
import threading as _threading
_LAST_REFRESH_TS: float = 0.0
_REFRESH_LOCK = _threading.Lock()
_REFRESH_COOLDOWN_SEC = 60   # 1분 안에 재호출 시 skip


def run(force: bool = False, return_driver: bool = False, notify: bool = True):
    """
    쿠키 확인 및 갱신 — 자동 재시도 (최대 _RETRY_MAX=3회) + 텔레그램 알림.

    Args:
        force:         True 면 유효성 체크 건너뛰고 강제 갱신
        return_driver: True 면 (ok, driver) 튜플 반환 — 호출자가 driver 재사용 후 quit()
        notify:        True 면 성공/실패 텔레그램 알림 (cron 잡은 True, 수동 호출은 False 가능)

    return_driver=True:
      - 쿠키 유효 / 갱신 성공: (True, driver|None) 반환
      - 실패: (False, None)
    return_driver=False: bool

    ★ 멱등성 가드 — 60초 안에 이미 성공했으면 즉시 True 반환 (텔레그램 알림 중복 차단).
    """
    global _LAST_REFRESH_TS
    import time as _time

    # ── ★ 멱등성 게이트 (모든 force/return_driver 조합 적용 — ERRORS [262] 박제)
    # 이전 게이트는 force=True 또는 return_driver=True 시 우회 → 3중 갱신 사고.
    # 수정: cooldown 안에 이미 성공한 갱신이 있으면 항상 skip.
    # return_driver=True 호출자는 (True, None) 을 수신하며, None driver 처리는 호출자 책임.
    with _REFRESH_LOCK:
        _since = _time.time() - _LAST_REFRESH_TS
        if _since < _REFRESH_COOLDOWN_SEC:
            print(f"  ⏭️ 티스토리 쿠키 갱신 — {int(_since)}초 전 이미 성공 (cooldown {_REFRESH_COOLDOWN_SEC}초, force={force})")
            return (True, None) if return_driver else True

    print("\n" + "=" * 50)
    print("  🍪 티스토리 쿠키 갱신 체크")
    print("=" * 50)

    # ── ★ 네트워크 연결 사전 확인 (ERRORS [285] 2026-06-27)
    # ERR_INTERNET_DISCONNECTED 는 코드 버그 아님 → Chrome 시작 전 차단
    if not _is_network_up():
        msg = "🌐 네트워크 연결 없음 — 티스토리 쿠키 갱신 스킵"
        print(f"  ⚠️ {msg}")
        if notify:
            _tg_notify(f"⚠️ *티스토리 쿠키 갱신 스킵*\n인터넷 연결을 확인하세요.\n(자동 수정 대상 아님 — 네트워크 복구 후 자동 재시도)")
        return (False, None) if return_driver else False

    # ── 환경변수 점검 ───────────────────────────────────────────
    env_ok, missing = _check_env_vars()
    if not env_ok:
        msg = f"❌ .env 누락: {missing} — 쿠키 갱신 불가"
        print(f"  {msg}")
        if notify:
            _tg_notify(f"🚨 *티스토리 쿠키 갱신 실패*\n{msg}\n`.env` 파일에 누락 변수 추가 후 재시도.")
        return (False, None) if return_driver else False

    # ── ★ 백오프 게이트 — 못 푸는 문을 계속 두드리지 않는다 (2026-08-13, ③ 대칭)
    #   네이버 `refresh_naver_cookies()` 가 같은 자리에서 같은 판정을 한다.
    #   ★ 다만 **쿠키가 아직 살아 있으면 막지 않는다** — 막힌 것은 *재로그인* 이지
    #     발행이 아니다. 여기서 무조건 False 를 돌려주면 백오프 창 동안 티스토리
    #     2조합(경제·테마)이 통째로 서 버린다. 판정은 도메인이 이미 가진 것을 쓴다(①).
    _blk = _lm().login_backoff_reason(PLATFORM)
    if _blk:
        if not force and cookie_valid_http() is True:
            print(f"  ⏸ {_blk}\n  ✅ 다만 현재 쿠키는 유효 — 갱신 없이 진행")
            return (True, None) if return_driver else True
        _fail(_lm().BACKOFF_REASON, f"  ⏸ {_blk}")
        if notify:
            _tg_notify(f"⏸ *티스토리 자동 로그인 보류 중*\n{_blk}")
        return (False, None) if return_driver else False

    # ── 재시도 루프 ─────────────────────────────────────────────
    last_error = None
    for attempt in range(1, _RETRY_MAX + 1):
        if attempt > 1:
            print(f"\n🔁 재시도 {attempt}/{_RETRY_MAX} ({_RETRY_DELAY_SEC}초 대기)...")
            time.sleep(_RETRY_DELAY_SEC)

        try:
            ok, drv = _attempt_once(force, return_driver)
            if ok:
                # ★ 성공 시 마지막 갱신 시각 박제 (멱등성 가드용)
                with _REFRESH_LOCK:
                    _LAST_REFRESH_TS = _time.time()
                if notify and attempt > 1:
                    _tg_notify(f"✅ 티스토리 쿠키 갱신 성공 (시도 {attempt}/{_RETRY_MAX})")
                elif notify:
                    # 첫 시도 성공 시에도 cron 잡은 알림 (사용자 가시성)
                    if force or attempt > 1:
                        _tg_notify(f"✅ 티스토리 쿠키 갱신 성공")
                return (True, drv) if return_driver else True
        except Exception as e:
            last_error = e
            print(f"  ⚠️ 시도 {attempt} 예외: {e}")
            _g_report("writer", e, module=__name__,
                      attempt=attempt, max_attempts=_RETRY_MAX)
        # ★ 사람이 있어야 풀리는 실패면 재시도는 낭비를 넘어 *해롭다* — 반복 실패는
        #   카카오 쪽 의심도를 올려 추가 인증을 더 부른다(네이버 캡차와 같은 구조).
        #   `_fail()` 이 이미 백오프를 걸었으므로 그것을 근거로 즉시 중단한다(①).
        if _lm().login_backoff_active_reason(PLATFORM):
            print("  ⏸ 사람이 필요한 실패 — 남은 재시도를 건너뛴다")
            break

    # ── 모든 재시도 실패 ─────────────────────────────────────────
    err_str = f": {last_error}" if last_error else ""
    print(f"\n❌ 쿠키 갱신 재시도 소진{err_str}")
    # ★ 사유를 잃지 않는다 — `_fail()` 이 이미 구체적 사유를 적었으면 덮어쓰지 않는다.
    if not last_login_failure():
        _fail("refresh_exhausted")
    if notify:
        _reason = last_login_failure()
        _hint = _lm().human_action_hint(PLATFORM, _reason)
        _tg_notify(
            f"🚨 티스토리 쿠키 갱신 실패 (사유: {_reason or '불명'})\n"
            f"{_lm().recovery_command(PLATFORM)}\n"
            + (f"\n{_hint}" if _hint else
               "/refresh_tistory 수동 재시도 또는 .env 의 TS_COOKIE 수동 갱신 가능합니다.")
        )
    return (False, None) if return_driver else False


# ══════════════════════════════════════════
#  ★ 수동 로그인 — 사람이 직접 푸는 경로 (2026-08-13, ③ 대칭)
# ══════════════════════════════════════════

def manual_login_and_save() -> bool:
    """브라우저를 열어 사용자가 직접 카카오 로그인하면 TSSESSION 을 자동 저장.

    ★ 왜 필요한가: 추가 인증·캡차 상황에서 **사람만이 복구할 수 있다.** 그런데 네이버에는
      이 문(`--manual`)이 있었고 티스토리에는 **없었다** — 그래서 티스토리가 만료되면
      사용자는 `.env` 의 `TS_COOKIE` 를 손으로 붙여넣는 수밖에 없었다.
      `alert_human_login_needed()` 가 안내하는 복구 명령(`recovery_command()`)이
      *실제로 존재하는 문* 이어야 안내가 거짓말이 되지 않는다.
    ★ 백오프를 보지 않는다 — 막는 것은 *무인 반복* 이지 사람의 복구가 아니다.
      성공하면 `update_env_cookie()` 가 백오프를 푼다(해제의 단일 지점).
    """
    driver = None
    try:
        driver = _make_driver()
        print("\n  🌐 브라우저가 열립니다. 카카오 계정으로 티스토리에 직접 로그인해 주세요.")
        print("  로그인 완료 후 Enter를 누르면 쿠키가 자동 저장됩니다.")
        driver.get("https://www.tistory.com/auth/login")
        try:
            input("\n  ✅ 로그인 완료 후 여기서 Enter: ")
        except KeyboardInterrupt:
            print("\n  ⛔ 취소됨")
            return False
        # 내 블로그로 이동해야 TSSESSION 이 확실히 잡힌다(다른 블로그 잔류 차단 포함)
        force_my_blog(driver)
        _s(2)
        for c in driver.get_cookies():
            if c.get("name") == "TSSESSION" and c.get("value"):
                if update_env_cookie(c["value"]):
                    print(f"  ✅ TSSESSION 저장 완료: {str(c['value'])[:20]}...")
                    return True
                print("  ❌ .env 업데이트 실패")
                return False
        print("  ❌ TSSESSION 쿠키를 찾지 못함 — 로그인이 끝나지 않았을 수 있습니다.")
        return False
    except Exception as e:                               # noqa: BLE001
        print(f"  ❌ 수동 로그인 오류: {e}")
        _g_report("writer", e, module=__name__)
        return False
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:                            # noqa: BLE001
                pass


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — Selenium 로그인 직전 환경 검증
    # ★ try/except 로 감싸지 않는다 (2026-08-10) — 감싸는 순간 ImportError 가 삼켜져
    #   "preflight 가 있다" 는 착각만 남고 **실제로는 한 번도 안 도는** 상태가 된다.
    #   실측(2026-08-10): 진입점 16곳 중 8곳이 그 상태였고, 경고는 stdout 으로만 나가는데
    #   데몬 stdout 은 /dev/null 이라 어디에도 안 남았다 — 완전한 침묵이었다.
    #   루트 경로는 파일 상단 부트스트랩이 보장한다. 여기서 실패하면 진짜 환경 문제다(fail-closed).
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight(strict=True)

    if "--manual" in sys.argv:
        # ※ watchdog 미적용 — `manual_login_and_save()` 는 input() 으로 사람의 수동
        #    로그인을 기다리는 *대화형* 경로다(무인 일회성 작업이 아니다). guard_main
        #    (freeze 300s 무진전·deadline 초과 시 os._exit)으로 감싸면 사람이 추가 인증을
        #    푸는 도중 세션이 강제 종료된다. 네이버 `--manual` 과 **같은 사유·같은 판단**(③).
        sys.exit(0 if manual_login_and_save() else 1)

    force   = "--force" in sys.argv
    # ★ 정지 방어 (사용자 박제 2026-07-06) — 일회성 쿠키 갱신 작업을 watchdog 로 감싼다.
    #   freeze(무진전) 300초 / deadline 600초 초과 시 GUARDIAN 보고 후 안전 종료.
    from JARVIS00_INFRA.watchdog import guard_main
    with guard_main("티스토리 쿠키갱신", deadline_sec=600):
        success = run(force=force)
    sys.exit(0 if success else 1)


__all__ = [
    "run", "check_cookie_valid", "refresh_cookie", "update_env_cookie",
    "cookie_valid_http", "is_login_redirect", "force_my_blog",
    # ★ 로그인 상태기 규약 — `login_manager` 가 *이름으로* 찾아 쓴다(② 파생).
    #   상태기 본체는 여기 없다. 여기 있는 것은 이 플랫폼 고유의 두 가지뿐이다.
    "PLATFORM", "HUMAN_REQUIRED_REASONS", "last_login_failure",
    "manual_login_and_save",
]
