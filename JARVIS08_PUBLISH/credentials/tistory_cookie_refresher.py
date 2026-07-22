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
  python -m JARVIS08_PUBLISH.credentials.tistory_cookie_refresher --force  # 강제 갱신
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
    """인터넷 연결 사전 확인 (Chrome 시작 전). 3초 timeout."""
    try:
        socket.setdefaulttimeout(3)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53))
        return True
    except OSError:
        return False

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

# 자동 로그인 흐름이 막힐 신호 — 페이지 텍스트에 등장하면 수동 개입 필요
_HUMAN_INTERVENTION_KEYWORDS = (
    "인증번호", "보안문자", "보안 문자", "captcha", "CAPTCHA",
    "기기 등록", "디바이스 등록", "새로운 기기", "본인 확인",
    "추가 인증", "2단계", "2차 인증", "OTP", "휴대전화 인증",
    "이메일 인증", "QR 코드", "QR코드",
)

_RETRY_MAX = 3            # 일시 장애 자동 재시도 횟수
_RETRY_DELAY_SEC = 5      # 재시도 간 대기


def _check_env_vars() -> tuple[bool, str]:
    """필수 .env 변수 점검. (ok, 누락 변수명) 반환."""
    missing = []
    if not TS_URL:      missing.append("TS_URL")
    if not TS_USERNAME: missing.append("TS_USERNAME")
    if not TS_PASSWORD: missing.append("TS_PASSWORD")
    if missing:
        return False, ", ".join(missing)
    return True, ""


def _detect_human_intervention(driver) -> str | None:
    """페이지 텍스트에서 수동 개입 요구 신호 감지. 발견 시 키워드 반환."""
    try:
        page = (driver.page_source or "")[:50000]
        for kw in _HUMAN_INTERVENTION_KEYWORDS:
            if kw in page:
                return kw
    except Exception:
        pass
    return None


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

            if "/auth/login" in _cur or "accounts.kakao.com" in _cur:
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
        print("  ❌ .env에 TS_USERNAME 또는 TS_PASSWORD가 없습니다.")
        return None

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
            print(f"  ❌ 이메일 입력란 못 찾음: {e}")
            _g_report("writer", e, module=__name__,
                      attempt=attempt, max_attempts=_RETRY_MAX)
            return None

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
            print(f"  ❌ 비밀번호 입력란 못 찾음: {e}")
            _g_report("writer", e, module=__name__)
            return None

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
        _blocker_alerted = False
        for _ in range(15):
            url = driver.current_url
            if "tistory.com" in url and "accounts.kakao" not in url:
                print("  ✅ 티스토리로 이동 완료")
                break
            # ★ 2FA·CAPTCHA·디바이스 인증 감지 — 텔레그램 SOS + 3분 대기
            blocker = _detect_human_intervention(driver)
            if blocker and not _blocker_alerted:
                _blocker_alerted = True
                print(f"  🚨 수동 개입 필요 — 감지 키워드: '{blocker}'. Chrome 창에서 직접 완료 (최대 3분 대기).")
                _tg_notify(
                    f"🚨 티스토리 쿠키 갱신 차단\n"
                    f"카카오가 '{blocker}' 요구합니다.\n"
                    f"Chrome 창에서 인증을 직접 완료하면 자동으로 이어집니다 (3분 대기).\n"
                    f"또는 /refresh_tistory 명령으로 나중에 재시도 가능합니다."
                )
                # 최대 3분(36 * 5초) 대기 — Chrome 창 열려 있으므로 사용자 직접 완료 가능
                for _w in range(36):
                    _s(5)
                    _cur = driver.current_url
                    if "tistory.com" in _cur and "accounts.kakao" not in _cur:
                        print("  ✅ 사용자가 추가 인증 완료 — 티스토리로 이동 확인")
                        break
                else:
                    print("  ❌ 3분 내 인증 완료 안 됨 — 포기")
                    return None
                break  # outer loop 탈출 (tistory 이동 확인됨)
            _s(2)

        if "accounts.kakao" in driver.current_url:
            print("  ❌ 로그인 실패 — 카카오 페이지에 머물러 있음")
            blocker = _detect_human_intervention(driver)
            if blocker:
                _tg_notify(f"🚨 티스토리 쿠키 갱신 실패 — {blocker} 요구")
            return None

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

        print("  ❌ TSSESSION 쿠키를 찾지 못함")
        return None

    except Exception as e:
        print(f"  ❌ 로그인 오류: {e}")
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        return None


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
            driver.quit()
            return True, None
        else:
            print("\n❌ .env 업데이트 실패")
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

    # ── 모든 재시도 실패 ─────────────────────────────────────────
    err_str = f": {last_error}" if last_error else ""
    print(f"\n❌ 쿠키 갱신 {_RETRY_MAX}회 모두 실패{err_str}")
    if notify:
        _tg_notify(
            f"🚨 티스토리 쿠키 갱신 실패 ({_RETRY_MAX}회 재시도 모두 실패)\n"
            f"카카오 로그인, CAPTCHA, 2FA 확인 필요합니다.\n"
            f"/refresh_tistory 수동 재시도 또는 .env 의 TS_COOKIE 수동 갱신 가능합니다."
        )
    return (False, None) if return_driver else False


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — Selenium 로그인 직전 환경 검증
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    force   = "--force" in sys.argv
    # ★ 정지 방어 (사용자 박제 2026-07-06) — 일회성 쿠키 갱신 작업을 watchdog 로 감싼다.
    #   freeze(무진전) 300초 / deadline 600초 초과 시 GUARDIAN 보고 후 안전 종료.
    from JARVIS00_INFRA.watchdog import guard_main
    with guard_main("티스토리 쿠키갱신", deadline_sec=600):
        success = run(force=force)
    sys.exit(0 if success else 1)


__all__ = ["run", "check_cookie_valid", "refresh_cookie", "update_env_cookie"]
