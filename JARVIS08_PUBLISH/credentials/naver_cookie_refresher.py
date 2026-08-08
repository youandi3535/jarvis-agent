#!/usr/bin/env python3
"""
naver_cookie_refresher.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
네이버 로그인 쿠키를 pyautogui(사람처럼 타이핑) 방식으로 갱신.
JS 인젝션 방식은 CAPTCHA를 유발하므로 사용하지 않음.
CGEventKeyboardSetUnicodeString으로 실제 키보드 입력 시뮬레이션.
"""
import os, sys, time, random, pickle, socket
from pathlib import Path
from dotenv import load_dotenv


def _is_network_up() -> bool:
    """인터넷 연결 사전 확인 (Chrome 시작 전) — 판정 본체는 `login_manager.network_up()`.

    ★ 종전엔 이 본체가 여기와 `tistory_cookie_refresher` 양쪽에 똑같이 복사돼 있었다(2벌).
      로그인 진입점이 하나이므로 그 전제 판정도 하나다 (LOGIN_SUPREME_LAW 단일 진입점).
      이 이름은 모듈 내부 호출자(167행)를 위한 얇은 위임으로만 남긴다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import network_up
    return network_up()

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

load_dotenv()
NV_ID       = os.getenv("NV_USERNAME", "")
NV_PW       = os.getenv("NV_PASSWORD", "")
# ★ anchor: 쿠키 파일은 JARVIS02_WRITER/ 옛 위치 보존 (JARVIS08/CLAUDE.md 규정)
_PROJECT_ROOT    = Path(__file__).resolve().parent.parent.parent  # → root
_LEGACY_BASE_DIR = _PROJECT_ROOT / "JARVIS02_WRITER"
COOKIE_FILE = _LEGACY_BASE_DIR / "naver_cookies.pkl"


# ── CAPTCHA·기기인증: 사람을 기다릴 것인가 ──────────────────────────
#
# ★ 왜 이 판단이 필요한가 (2026-08-09 실사고 — ERRORS [593])
#   08-09 07:00 경제 브리핑이 발행되지 않았다. 네이버가 CAPTCHA/기기 인증을 요구했고
#   이 코드가 **"화면에서 직접 풀어주세요" 라며 120초를 기다렸다.** 새벽 7시 예약 실행에
#   화면 앞에 사람이 있을 리 없다. 120초를 버리고 실패했다(잡 소요 163초 = sweep 2s +
#   대기 120s + 부대). 기다림은 발행 창만 먹고 결과를 바꾸지 못했다.
#
# ★ ② 동적 설계 — '사람이 있는가' 를 새 플래그로 만들지 않는다.
#   이미 있는 판단에서 파생한다: `shared.llm.current_job_id()` 는 예약 잡 안에서만
#   잡 ID 를 돌려준다(밖에서는 ""). 잡 안 = 무인이다. 실측으로 확인했다.
#   무인이면 0초 — 즉시 포기하고 사람을 부른다. 그게 더 빠른 복구다.
CAPTCHA_WAIT_SEC = int(os.getenv("NAVER_CAPTCHA_WAIT_SEC", "120"))   # 대화형에서만 쓰인다


def human_wait_sec() -> int:
    """CAPTCHA·기기인증을 사람이 풀 때까지 기다릴 초 — 무인 실행이면 0."""
    try:
        from shared.llm import current_job_id            # noqa: PLC0415 — 순환 방지
        if current_job_id():
            return 0                                     # 예약 잡 = 무인
    except Exception:                                    # noqa: BLE001
        pass
    return max(0, CAPTCHA_WAIT_SEC)


# ── 마지막 실패 사유 ────────────────────────────────────────────────
#
# ★ 왜 반환형(bool)을 안 바꾸나: `refresh_naver_cookies` 호출자가 13곳이다.
#   반환형을 넓히면 그 전부를 손대야 하고, 하나라도 놓치면 조용히 깨진다.
#   사유는 옆문으로 노출한다 — 필요한 호출자만 읽는다.
_LAST_FAILURE: str = ""


def _fail(reason: str, msg: str = "") -> bool:
    """실패를 기록하고 False 를 돌려준다 — 사유를 잃지 않는 유일한 출구."""
    global _LAST_FAILURE
    _LAST_FAILURE = reason
    if msg:
        print(msg)
    return False


def last_login_failure() -> str:
    """직전 네이버 로그인 실패 사유 (성공했거나 시도 전이면 "")."""
    return _LAST_FAILURE


def naver_login_error_type(reason: str) -> str:
    """실패 사유 → 오류 타입. *이미 있는 판단*(사유)에서 기계적으로 만든다.

    ★ 중앙 매핑표를 두지 않는다 (CLAUDE.md ERRORS [547] — 도메인이 파생).
      새 사유가 생기면 타입이 자동으로 따라온다.
      예: 'captcha_unattended' → 'NaverLoginCaptchaUnattended'
    """
    slug = "".join(w.capitalize() for w in (reason or "unknown").split("_"))
    return "NaverLogin" + slug


def _save_cookies(cookies) -> None:
    """쿠키 저장 **단일 진입점** — 저장 직후 권한을 소유자 전용(0600)으로 고정한다.

    ★ 왜 (2026-08-04 전수 감사 3위): 실측 `naver_cookies.pkl` 권한이 **0644** 였다
      (대조군 `.env` 0600 · `chrome_profile/` 0700 · `credentials/` 0700 — 이것만 열려 있었다).
      상위 디렉터리도 0755 라 같은 머신의 다른 사용자가 그대로 읽을 수 있다.
      쿠키는 비밀번호와 같은 값이다 — 있으면 로그인 없이 그 계정이 된다.
    ★ 저장이 3곳(:234·:325·:402)에 흩어져 있었다. 한 곳만 고치면 다른 경로에서 다시
      0644 로 쓰인다 — 그래서 저장 자체를 여기 하나로 모은다(원칙①).
    """
    import os as _os
    import pickle as _pk
    with open(COOKIE_FILE, "wb") as _f:
        _pk.dump(cookies, _f)
    try:
        _os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass
COOKIE_MAX_AGE_HOURS = 10   # 이 시간 이상 된 쿠키는 갱신


def check_cookie_valid() -> bool:
    """
    저장된 쿠키로 네이버에 실제 HTTP 요청을 보내 로그인 상태 확인.
    브라우저 없이 requests만 사용 → 빠름 (1~2초).
    추가로 NID_AUT / NID_SES 만료 시간을 확인해 브라우저 사용 불가 상태도 감지.
    Returns: True = 쿠키 유효(로그인 상태), False = 만료 또는 파일 없음
    """
    if not COOKIE_FILE.exists():
        print("  ℹ️  쿠키 파일 없음")
        return False

    import requests as _req

    # pkl 쿠키 → requests용 dict로 변환
    try:
        raw_cookies = pickle.load(open(COOKIE_FILE, "rb"))
    except Exception as e:
        print(f"  ⚠️ 쿠키 파일 읽기 실패: {e}")
        _g_report("writer", e, module=__name__)
        return False

    # ── 핵심 쿠키 존재 여부 먼저 확인 ──────────────────────────────
    # NID_AUT, NID_SES가 없으면 브라우저 로그인 불가
    key_names = {c["name"] for c in raw_cookies}
    for key in ("NID_AUT", "NID_SES"):
        if key not in key_names:
            print(f"  ❌ {key} 쿠키 없음 → 브라우저 로그인 불가")
            return False

    jar = {c["name"]: c["value"] for c in raw_cookies}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9",
    }

    try:
        # 네이버 메인 → 로그인 상태면 NV_ID 또는 '로그아웃' 텍스트 포함
        res = _req.get(
            "https://www.naver.com",
            cookies=jar,
            headers=headers,
            timeout=8,
            allow_redirects=True,
        )
        logged_in = ("로그아웃" in res.text) or (NV_ID and NV_ID in res.text)
        if logged_in:
            print("  ✅ 쿠키 유효 (로그인 상태 확인됨)")
        else:
            print("  ❌ 쿠키 만료 (로그아웃 상태)")
        return logged_in
    except Exception as e:
        print(f"  ⚠️ 쿠키 유효성 확인 요청 실패: {e}")
        _g_report("writer", e, module=__name__)
        # 네트워크 오류는 만료로 보지 않음 → True 반환해서 갱신 시도 막음
        return True


def cookie_needs_refresh() -> bool:
    """
    쿠키 갱신이 필요한지 판단:
    1) 파일이 없으면 → True
    2) 파일 나이가 COOKIE_MAX_AGE_HOURS 이상 → 실제 유효성 확인 (유효하면 mtime 리셋)
    3) 파일 나이가 짧아도 실제 확인 결과 만료 → True
    """
    if not COOKIE_FILE.exists():
        return True
    age_hours = (time.time() - COOKIE_FILE.stat().st_mtime) / 3600
    if age_hours < COOKIE_MAX_AGE_HOURS:
        # 파일이 최신이어도 실제 확인
        return not check_cookie_valid()
    # 파일이 오래됐으면 실제 확인 — 여전히 유효하면 나이 판정 기준(mtime)을 리셋.
    # 리셋 안 하면 전체 재로그인(Selenium) 없이는 age>10h 판정이 매 호출마다 반복돼
    # login_manager.verify_all_logins()/harness precondition 이 살아있는 세션을
    # 계속 "쿠키 만료 임박/세션 무효"로 오판 (재발 원인).
    still_valid = check_cookie_valid()
    if still_valid:
        try:
            COOKIE_FILE.touch()
        except OSError as e:
            print(f"  ⚠️ 쿠키 mtime 리셋 실패: {e}")
    return not still_valid


def _type_string_cgevent(text: str, delay_min=0.04, delay_max=0.10):
    """CGEventKeyboardSetUnicodeString으로 문자열 타이핑 (한글 IME 완전 우회)"""
    try:
        from Quartz import (CGEventCreateKeyboardEvent, CGEventPost,
                            CGEventKeyboardSetUnicodeString, kCGHIDEventTap)
        for ch in text:
            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 0, down)
                CGEventKeyboardSetUnicodeString(ev, 1, ch)
                CGEventPost(kCGHIDEventTap, ev)
                time.sleep(0.025)
            time.sleep(random.uniform(delay_min, delay_max))
        return True
    except Exception as e:
        print(f"  ⚠️ CGEvent 타이핑 실패: {e}")
        _g_report("writer", e, module=__name__)
        return False


def _activate_chrome():
    import subprocess
    subprocess.run(
        ["osascript", "-e", 'tell application "Google Chrome" to activate'],
        capture_output=True
    )
    time.sleep(0.8)


def refresh_naver_cookies(force: bool = False) -> bool:
    """
    pyautogui 기반 사람처럼 타이핑으로 네이버 로그인 후 쿠키 저장.
    force=True 이면 쿠키 나이와 상관없이 갱신.
    """
    if not force and not cookie_needs_refresh():
        print(f"  ✅ 쿠키 유효 (갱신 불필요)")
        return True

    # ── ★ 네트워크 연결 사전 확인 (ERRORS [285] 2026-06-27)
    # ERR_INTERNET_DISCONNECTED 는 코드 버그 아님 → Chrome 시작 전 차단
    if not _is_network_up():
        print("  ⚠️ 네트워크 연결 없음 — 네이버 쿠키 갱신 스킵")
        try:
            from shared.notify import send_tg as _notify  # ★ 2026-07-03: 'send' 미존재 — 조용히 죽던 알림 복구
            _notify("⚠️ *네이버 쿠키 갱신 스킵*\n인터넷 연결을 확인하세요.\n(자동 수정 대상 아님 — 네트워크 복구 후 자동 재시도)")
        except Exception:
            pass
        return _fail("network_down")

    if not NV_ID or not NV_PW:
        return _fail("credentials_missing", "  ❌ NV_USERNAME / NV_PASSWORD 환경변수 없음")

    print(f"  🔄 네이버 쿠키 갱신 시작 (ID: {NV_ID[:3]}***)")

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import pyautogui

    pyautogui.FAILSAFE = False

    # Poster와 동일한 프로필 사용 — 로그인 세션이 프로필에 저장되어 poster가 바로 재사용 가능
    # (poster가 동시에 실행 중이면 안 되지만, refresher는 poster 실행 전에만 호출됨)
    _profile_dir = str(Path(COOKIE_FILE).parent / "chrome_profile" / "naver")

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1200,800")
    options.add_argument("--window-position=0,0")
    options.add_argument(f"--user-data-dir={_profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )

    try:
        # ── 1단계: 이미 로그인돼 있으면 쿠키만 추출 (CAPTCHA 유발 방지) ──
        driver.get("https://www.naver.com")
        time.sleep(random.uniform(2, 3))
        src = driver.page_source
        already_logged = "로그아웃" in src or (NV_ID and NV_ID in src)
        if already_logged:
            # nid.naver.com도 방문해서 NID_AUT / NID_SES 쿠키까지 수집
            driver.get("https://nid.naver.com")
            time.sleep(random.uniform(1, 2))
            cookies = driver.get_cookies()
            key_names = {c["name"] for c in cookies}
            if "NID_AUT" not in key_names or "NID_SES" not in key_names:
                # blog.naver.com도 방문 후 재수집
                driver.get(f"https://blog.naver.com/{NV_ID}")
                time.sleep(random.uniform(1.5, 2.5))
                cookies = driver.get_cookies()
                key_names = {c["name"] for c in cookies}
            if cookies and ("NID_AUT" in key_names and "NID_SES" in key_names):
                _save_cookies(cookies)
                print(f"  ✅ 프로필 세션 유효 — 쿠키 추출 완료 ({len(cookies)}개, NID_AUT/SES 포함)")
                return True
            print(f"  ⚠️ 로그인 확인됐으나 NID_AUT/SES 없음 (보유: {key_names}) — 재로그인 시도")

        # ── 2단계: CGEvent 타이핑으로 로그인 (자동화 감지 우회) ─────
        # send_keys는 네이버가 자동화로 감지 → CAPTCHA 유발
        # CGEventKeyboardSetUnicodeString = HID 레벨 입력 → 사람처럼 인식
        driver.get("https://nid.naver.com/nidlogin.login")
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.ID, "id"))
            )
        except Exception:
            return _fail("login_form_timeout", "  ❌ 로그인 폼 로드 타임아웃")

        time.sleep(random.uniform(1.5, 2.5))
        _activate_chrome()

        # 아이디 입력 — pyautogui 좌표 클릭 후 CGEvent 타이핑
        print("  ⌨️  아이디 CGEvent 입력 중...")
        id_field = driver.find_element(By.ID, "id")
        rect = id_field.rect
        bx = driver.execute_script("return window.screenX + (window.outerWidth - window.innerWidth)/2")
        by = driver.execute_script("return window.screenY + (window.outerHeight - window.innerHeight)")
        import pyautogui as _pg
        _pg.moveTo(bx + rect["x"] + rect["width"]//2, by + rect["y"] + rect["height"]//2, duration=0.3)
        _pg.click()
        time.sleep(0.5)
        _type_string_cgevent(NV_ID)
        time.sleep(random.uniform(0.6, 1.0))

        # 비밀번호 입력
        print("  ⌨️  비밀번호 CGEvent 입력 중...")
        pw_field = driver.find_element(By.ID, "pw")
        rect = pw_field.rect
        _pg.moveTo(bx + rect["x"] + rect["width"]//2, by + rect["y"] + rect["height"]//2, duration=0.3)
        _pg.click()
        time.sleep(0.5)
        _type_string_cgevent(NV_PW)
        time.sleep(random.uniform(0.8, 1.5))

        # 로그인 버튼 — pyautogui 클릭
        # ★ 네이버가 id="log.login" 을 폐기하고 반응형 레이아웃 변형(loginBtn_row/
        #   loginBtn_column) 둘 중 하나만 display:none 없이 노출한다 (ERRORS 참조:
        #   NoSuchElementException [log.login]). 둘 다 후보로 두고 실제 보이는 것을 쓴다.
        try:
            btn = None
            for candidate_id in ("loginBtn_row", "loginBtn_column"):
                for el in driver.find_elements(By.ID, candidate_id):
                    if el.is_displayed():
                        btn = el
                        break
                if btn is not None:
                    break
            if btn is None:
                btn = driver.find_element(
                    By.XPATH,
                    "//button[contains(@class,'btn_done') and .//span[text()='로그인']]",
                )
            rect = btn.rect
            _pg.moveTo(bx + rect["x"] + rect["width"]//2, by + rect["y"] + rect["height"]//2, duration=random.uniform(0.3, 0.6))
            time.sleep(0.2)
            _pg.click()
        except Exception as e:
            print(f"  ⚠️ 로그인 버튼 클릭 실패: {e}")
            _g_report("writer", e, module=__name__)
            return _fail("login_button_click")

        # 로그인 완료 대기
        try:
            WebDriverWait(driver, 15).until(
                lambda d: "nidlogin" not in d.current_url
            )
            print("  ✅ 로그인 완료")
        except Exception:
            src = driver.page_source
            if "captcha" in src.lower() or "보안" in src or "기기" in src:
                _wait = human_wait_sec()
                if _wait <= 0:
                    # 무인 실행 — 화면 앞에 아무도 없다. 기다려도 결과가 같으니 즉시 부른다.
                    return _fail("captcha_unattended",
                                 "  ❌ CAPTCHA / 기기 인증 요구 — 무인 실행이라 대기하지 않는다 "
                                 "(사람이 직접 로그인해야 함)")
                print(f"  ⚠️  CAPTCHA / 기기 인증 감지 — 화면에서 직접 풀어주세요 (최대 {_wait}초 대기)")
                try:
                    WebDriverWait(driver, _wait).until(
                        lambda d: "nidlogin" not in d.current_url
                    )
                    print("  ✅ 수동 인증 완료")
                except Exception:
                    return _fail("captcha_timeout", f"  ❌ {_wait}초 내 인증 미완료 — 종료")
            else:
                return _fail("login_no_redirect", "  ❌ 로그인 후 URL 전환 없음")

        time.sleep(random.uniform(2, 3))

        # ── 로그인 확인 ───────────────────────────────────────
        src = driver.page_source
        logged = "로그아웃" in src or NV_ID in src
        if not logged:
            # naver.com 메인으로 이동해서 재확인
            driver.get("https://www.naver.com")
            time.sleep(2)
            src = driver.page_source
            logged = "로그아웃" in src or NV_ID in src

        if logged:
            cookies = driver.get_cookies()
            _save_cookies(cookies)
            print(f"  ✅ 쿠키 갱신 완료 ({len(cookies)}개 저장)")
            return True
        else:
            return _fail("login_unconfirmed", "  ❌ 로그인 확인 실패")

    except Exception as e:
        print(f"  ❌ 쿠키 갱신 오류: {e}")
        _g_report("writer", e, module=__name__)
        return _fail(f"exception_{type(e).__name__}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def manual_login_and_save():
    """
    브라우저를 열어 사용자가 직접 로그인하면 쿠키를 자동 저장.
    CAPTCHA / 기기인증 상황에서 사용.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_argument("--window-size=1200,800")
    options.add_argument("--window-position=0,0")
    _profile_dir = str(Path(COOKIE_FILE).parent / "chrome_profile" / "naver")
    options.add_argument(f"--user-data-dir={_profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )

    print("\n  🌐 브라우저가 열립니다. 네이버에 직접 로그인해 주세요.")
    print("  로그인 완료 후 Enter를 누르면 쿠키가 자동 저장됩니다.")
    driver.get("https://nid.naver.com/nidlogin.login")

    try:
        input("\n  ✅ 로그인 완료 후 여기서 Enter: ")
        # 로그인 확인
        driver.get("https://www.naver.com")
        time.sleep(2)
        src = driver.page_source
        logged = "로그아웃" in src or (NV_ID and NV_ID in src)
        if logged:
            # 여러 도메인 방문하여 모든 쿠키 수집 (BA_DEVICE, JSESSIONID 등 포함)
            all_cookies: dict = {}
            for c in driver.get_cookies():
                all_cookies[c["name"]] = c  # www.naver.com 쿠키

            driver.get("https://nid.naver.com")
            time.sleep(1.5)
            for c in driver.get_cookies():
                all_cookies[c["name"]] = c  # nid.naver.com 쿠키 (BA_DEVICE 등)

            driver.get(f"https://blog.naver.com/{NV_ID}")
            time.sleep(2)
            for c in driver.get_cookies():
                all_cookies[c["name"]] = c  # blog.naver.com 쿠키 (JSESSIONID 등)

            cookies = list(all_cookies.values())
            _save_cookies(cookies)
            names = {c["name"] for c in cookies}
            print(f"  ✅ 쿠키 저장 완료 ({len(cookies)}개): {names}")
            now = time.time()
            for c in cookies:
                if c['name'] in ('NID_AUT', 'NID_SES', 'BA_DEVICE'):
                    exp = c.get('expiry')
                    if exp:
                        remaining = (exp - now) / 3600
                        print(f"  {c['name']}: 만료까지 {remaining:.1f}시간")
                    else:
                        print(f"  {c['name']}: session 쿠키 (브라우저 종료 시 만료)")
            return True
        else:
            print("  ❌ 로그인 상태 미확인 — 다시 시도하세요.")
            return False
    except KeyboardInterrupt:
        print("\n  ⛔ 취소됨")
        return False
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def job_pre_naver_check() -> bool:
    """발행 직전 네이버 쿠키 유효성 검사·갱신. True=쿠키 정상, False=갱신 실패.

    ★ 사용자 박제 2026-05-30 — bool 반환 추가 (실패 시 발행 콜백 조기 종료용).
    """
    print(f"\n🍪 [쿠키 점검] 네이버 쿠키 유효성 검사")
    try:
        if not cookie_needs_refresh():
            print("  ✅ 네이버 쿠키 유효 — 갱신 불필요")
            return True
        ok = refresh_naver_cookies(force=True)
        if ok:
            print("  ✅ 네이버 쿠키 갱신 완료")
        else:
            print("  ❌ 네이버 쿠키 갱신 실패")
        return bool(ok)
    except Exception as e:
        print(f"  ❌ 네이버 쿠키 점검 예외: {e}")
        _g_report("writer", e, module=__name__)
        return False


if __name__ == "__main__":
    import sys
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — Selenium 로그인 직전 환경 검증
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    if "--check" in sys.argv:
        # 쿠키 유효성만 확인 (갱신 안 함)
        valid = check_cookie_valid()
        if valid:
            age_h = (time.time() - COOKIE_FILE.stat().st_mtime) / 3600 if COOKIE_FILE.exists() else 0
            print(f"  📋 쿠키 파일 나이: {age_h:.1f}시간")
        # 핵심 쿠키 만료 시간 추가 출력
        if COOKIE_FILE.exists():
            try:
                now = time.time()
                cookies = pickle.load(open(COOKIE_FILE, "rb"))
                for c in cookies:
                    if c['name'] in ('NID_AUT', 'NID_SES'):
                        exp = c.get('expiry', 0)
                        remaining = (exp - now) / 3600 if exp else 0
                        print(f"  {c['name']}: 만료까지 {remaining:.1f}시간")
            except Exception:
                pass
        sys.exit(0 if valid else 1)

    if "--manual" in sys.argv:
        # 수동 로그인 모드 (CAPTCHA 상황)
        # ※ watchdog 미적용(보수적 스킵) — manual_login_and_save() 는 input() 으로 사람의
        #    수동 로그인을 무한 대기하는 *대화형* 경로(무인 일회성 작업 아님). guard_main
        #    (freeze 300s 무진전·deadline 초과 시 os._exit)으로 감싸면 사람이 CAPTCHA/기기인증을
        #    푸는 도중 세션이 강제 종료됨 → 기존 동작(무한 대기) 위반이므로 감싸지 않음.
        success = manual_login_and_save()
        sys.exit(0 if success else 1)

    force = "--force" in sys.argv
    # ── 정지 방어: --force/기본 자동 갱신은 무인 일회성 Selenium 작업 → guard_main 래핑
    #    (freeze 300s 무진전 또는 deadline 600s 초과 시 GUARDIAN 보고 후 os._exit → 다음 예약 재시도)
    from JARVIS00_INFRA.watchdog import guard_main  # 지역 import (순환 방지)
    with guard_main("네이버 쿠키갱신", deadline_sec=600):
        success = refresh_naver_cookies(force=force)
    sys.exit(0 if success else 1)
