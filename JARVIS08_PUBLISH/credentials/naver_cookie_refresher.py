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

# ★ 두 가지를 섞지 않는다 (2026-08-09 정정 — 내가 만든 회귀, ERRORS [595])
#   · "로그인이 끝나기를 기다리는 시간"  — 사람과 무관하다. 무인이어도 줘야 한다.
#   · "사람이 캡차를 푸는 시간"          — 무인이면 0 이 맞다.
#   종전 코드는 이 둘을 한 분기에 묶어 놓고, 판정을 **낱말**로 했다:
#       if "captcha" in src.lower() or "보안" in src or "기기" in src
#   실측(캡차 없는 평상시 로그인 페이지 19,620자): `captcha` 7회 · `보안` 2회.
#   **항상 참이다.** 그래서 그 분기는 "캡차 감지" 가 아니라 사실상 "15초 더 기다리기" 였고,
#   내가 그 대기를 무인일 때 0 으로 만들자 *느린 정상 로그인까지* 죽는 회귀가 됐다.
LOGIN_REDIRECT_WAIT_SEC = int(os.getenv("NAVER_LOGIN_WAIT_SEC", "60"))


# 로그인이 15초 안에 안 끝났을 때 화면 증거를 남길 곳 (ERRORS [606])
LOGIN_STUCK_DIR = _LEGACY_BASE_DIR / "logs" / "login_stuck"


def capture_login_stuck(driver, tag: str = "") -> str:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import capture_login_stuck as _f
    return _f(driver, "naver", tag)



def captcha_present(driver) -> "bool | None":
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import captcha_present as _f
    return _f(driver)



# ══════════════════════════════════════════════════════════════════
# '로그인 상태 유지' — 세션 쿠키만 받아 반나절 뒤 죽는 사슬의 *시작점*
# ══════════════════════════════════════════════════════════════════
#
# ★ 왜 (2026-08-13 실측): 저장된 pkl 의 NID_AUT·NID_SES 가 **expiry 없음(세션 쿠키)** 이다.
#   세션 쿠키는 Chrome 종료 시 프로필에서 증발한다 → 다음 회차 step0(프로필 세션 재사용)이
#   실패 → 폼로그인으로 하강 → 폼로그인 캡차율 실측 100%(login_stuck 캡처 10/10) →
#   백오프 → 미발행. step0 성공률 08-03~08-08 8/8 → 08-10~08-13 **0/6**.
#   그런데 폼로그인은 비밀번호를 치자마자 로그인 버튼을 눌렀다 — '로그인 상태 유지' 를
#   켜는 단계가 아예 없었다(`grep '로그인 상태 유지|keep_login|nvlong'` 저장소 전역 **0건**).
#   사용자가 12:26 에 **손으로** 로그인한 쿠키조차 세션이었다 → 수동 경로에도 켠다.
#
# ★ 셀렉터는 **검증되지 않은 후보** 다 — 이 저장소에 로그인 폼 마크업 실물이 0장이다.
#   `logs/login_stuck/*.html` 10장은 전부 캡차 화면(nidlogin.rcaptcha)이라 체크박스가 없고
#   `keep`·`nvlong`·`<input type=checkbox>` 히트도 0건이었다. 그래서
#   ① 후보를 여럿 두고 ② 못 찾으면 **조용히 넘어가고**(로그인은 계속된다)
#   ③ 못 찾은 사실을 화면으로 남겨 다음 세션이 추측을 실측으로 바꾸게 한다.
try:
    from selenium.webdriver.common.by import By as _By
except Exception:                                        # noqa: BLE001
    class _By:                                           # selenium 부재 환경에서도 import 가능
        # 값은 selenium 실제 값과 동일 — 진짜 드라이버에 그대로 넘어간다(사본 아님).
        CSS_SELECTOR = "css selector"
        XPATH = "xpath"

KEEP_LOGIN_CANDIDATES = (
    (_By.CSS_SELECTOR, "input#keep"),                                 # 전통적 id
    (_By.CSS_SELECTOR, "input[name='nvlong']"),                       # 전통적 name (value=on)
    (_By.CSS_SELECTOR, "input[type='checkbox'][id*='keep' i]"),
    (_By.CSS_SELECTOR, ".keep_check input[type='checkbox'], "
                       ".login_keep input[type='checkbox']"),
    (_By.XPATH, "//label[contains(normalize-space(.),'로그인 상태 유지')]"),
)

# 미발견 증거는 프로세스당 1회만 남긴다(알림 피로 방지).
# ★ '했다' 는 플래그가 아니라 **실제로 남긴 경로** 를 담는다 — 시도는 적용의 증거가 아니다.
#   저장에 실패하면(빈 문자열) 아무것도 담기지 않으므로 다음 기회에 다시 시도한다.
_KEEP_LOGIN_SHOTS: list = []


def _is_checked(el) -> bool:
    """체크 상태를 **읽어서** 확인한다 — 눌렀다는 사실은 켜졌다는 증거가 아니다."""
    try:
        return bool(el.is_selected())
    except Exception:                                    # noqa: BLE001
        pass
    try:
        return bool(el.get_attribute("checked"))
    except Exception:                                    # noqa: BLE001
        return False


def _resolve_checkbox(driver, el):
    """매치된 노드에서 실제 체크박스 input 을 끌어낸다(label 이 잡혔을 때)."""
    try:
        tag = (el.tag_name or "").lower()
    except Exception:                                    # noqa: BLE001
        return None
    if tag == "input":
        return el
    try:
        _for = el.get_attribute("for") or ""
    except Exception:                                    # noqa: BLE001
        _for = ""
    if _for:
        try:
            for node in driver.find_elements(_By.CSS_SELECTOR, f"input#{_for}"):
                return node
        except Exception:                                # noqa: BLE001
            pass
    try:
        for node in el.find_elements(_By.CSS_SELECTOR, "input[type='checkbox']"):
            return node
    except Exception:                                    # noqa: BLE001
        pass
    return el if tag else None


def _find_keep_login_input(driver):
    """후보를 순서대로 훑어 체크박스를 찾는다 → (element, 매치한 셀렉터) / (None, "").

    ★ `is_displayed()` 를 요구하지 않는다 — 네이버는 input 을 시각적으로 숨기고
      <label>·<span class="ico_check"> 로 그리는 관례다. 보이는 것만 찾으면 늘 '없음' 이 된다.
    """
    for by, sel in KEEP_LOGIN_CANDIDATES:
        try:
            els = driver.find_elements(by, sel)
        except Exception:                                # noqa: BLE001
            continue
        for el in els or ():
            node = _resolve_checkbox(driver, el)
            if node is not None:
                return node, sel
    return None, ""


def enable_keep_login(driver) -> "bool | None":
    """'로그인 상태 유지' 를 켠다 — **True/False/None 셋 다 로그인을 막지 않는다.**

    Returns:
        True  — checked 를 *읽어서* 확인함
        False — 요소는 찾았으나 켜지 못함
        None  — 요소 없음(DOM 변경 등) 또는 판정 불가

    ★ 계약(어기면 이 함수가 사고를 만든다): 예외를 올리지 않는다 · `_fail()` 을 부르지
      않는다 · 반환값이 **어떤 분기 조건에도 등장하지 않는다**. 여기서 로그인을 막으면
      지금까지 되던 로그인까지 죽는다 — 그게 세션 쿠키보다 큰 사고다.
    ★ 이미 켜져 있으면 다시 누르지 않는다 — 체크박스는 토글이라 누르면 꺼진다.
    """
    try:
        el, matched = _find_keep_login_input(driver)
        if el is None:
            print("  ℹ️  '로그인 상태 유지' 요소 없음 — 건너뜀 (네이버 DOM 변경 가능)")
            _capture_keep_login_missing(driver)
            return None
        if _is_checked(el):
            print(f"  🔒 '로그인 상태 유지' 이미 켜짐 ({matched}) — 다시 누르지 않는다(토글)")
            return True

        _eid = ""
        try:
            _eid = el.get_attribute("id") or ""
        except Exception:                                # noqa: BLE001
            _eid = ""

        def _click_label():
            if not _eid:
                raise LookupError("id 없음 — label[for] 경로 불가")
            for lb in driver.find_elements(_By.CSS_SELECTOR, f"label[for='{_eid}']"):
                lb.click()
                return
            raise LookupError("label[for] 없음")

        def _click_el():
            el.click()

        def _click_js():
            driver.execute_script("arguments[0].click()", el)

        def _assign_js():
            # 마지막 수단 — property 직접 대입. 대입만 하면 change 이벤트가 없어
            # 프레임워크 토글이 상태를 되돌릴 수 있으므로 이벤트를 함께 쏜다.
            driver.execute_script(
                "arguments[0].checked = true;"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));", el)

        for _how, _act in (("label", _click_label), ("click", _click_el),
                           ("js-click", _click_js), ("js-assign", _assign_js)):
            try:
                _act()
            except Exception as _e:                      # noqa: BLE001
                continue
            if _is_checked(el):
                # 매치한 셀렉터를 남긴다 — 다음 DOM 변경 때의 진단 재료다.
                print(f"  🔒 '로그인 상태 유지' 켜짐 (셀렉터={matched} / 방법={_how})")
                return True
        print(f"  ⚠️ '로그인 상태 유지' 를 켜지 못했다 (셀렉터={matched}) — 로그인은 계속한다")
        return False
    except Exception as e:                               # noqa: BLE001
        # 여기서 예외가 새면 로그인 자체가 죽는다 — 절대 올리지 않는다.
        print(f"  ⚠️ '로그인 상태 유지' 처리 중 예외(무시): {type(e).__name__}: {e}")
        return None


def _capture_keep_login_missing(driver) -> None:
    """체크박스를 못 찾은 화면을 1회 남긴다 — 지금 우리에겐 로그인 폼 실물이 0장이다."""
    if _KEEP_LOGIN_SHOTS:
        return
    try:
        shot = capture_login_stuck(driver, "keep_login_missing")
    except Exception:                                    # noqa: BLE001
        return
    if shot:
        _KEEP_LOGIN_SHOTS.append(shot)
        print(f"  📸 '로그인 상태 유지' 미발견 화면 저장: {shot}.(html|png) "
              f"— 다음 세션이 셀렉터를 실측으로 고칠 재료")


# ══════════════════════════════════════════════════════════════════
# 캡차 백오프 — 못 푸는 문을 계속 두드리지 않는다 (2026-08-11, ERRORS [615])
# ══════════════════════════════════════════════════════════════════
#
# ★ 왜 필요한가: 캡차는 무인으로 못 푼다. 그런데 시스템은 매 회차 같은 시도를 반복해
#   **스스로 캡차를 더 부르고 있었다** — 실측 08-10 8회 · 08-11 4회.
#   네이버 입장에선 짧은 시간에 실패한 로그인이 반복되는 것이라 의심도가 올라간다.
#   한 번 캡차를 만나면 일정 시간 자동 로그인을 멈추고 **사람을 부른다.**
# ★ 상태를 파일에 두는 이유: 발행은 subprocess, 인시던트 재시도는 새 스레드다.
#   메모리 플래그는 그 경계를 못 넘는다(ERRORS [474] 와 같은 병).
_BACKOFF_FILE = Path(__file__).resolve().parent / "login_backoff.json"
CAPTCHA_BACKOFF_SEC = int(os.getenv("NAVER_CAPTCHA_BACKOFF_SEC", str(6 * 3600)))


def _login_backoff_state() -> tuple:
    """백오프 파일을 파싱해 (raw reason, 남은 초) 반환. 없거나 만료면 ("", 0.0).

    ★ 파싱은 여기 한 곳에서만 한다(① 단일 진입점) — `login_backoff_reason()`(사람이
      읽는 문장)과 `login_backoff_active_reason()`(타입 파생용 raw reason) 이 함께 쓴다.
    """
    try:
        import json as _js                                # noqa: PLC0415
        _d = _js.loads(_BACKOFF_FILE.read_text(encoding="utf-8"))
        _until = float(_d.get("until") or 0)
    except Exception:                                     # noqa: BLE001
        return ("", 0.0)                                  # 못 읽으면 막지 않는다(fail-open)
    _left = _until - time.time()
    if _left <= 0:
        return ("", 0.0)
    return (str(_d.get("reason") or "captcha"), _left)


def login_backoff_reason() -> str:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import login_backoff_reason as _f
    return _f("naver")



def login_backoff_active_reason() -> str:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import login_backoff_active_reason as _f
    return _f("naver")



def current_login_failure_reason() -> str:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import current_login_failure_reason as _f
    return _f("naver")



def mark_login_backoff(reason: str) -> None:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import mark_login_backoff as _f
    _f("naver", reason)



def clear_login_backoff() -> None:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import clear_login_backoff as _f
    _f("naver")



def alert_human_login_needed(reason: str, shot: str = "") -> None:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import alert_human_login_needed as _f
    _f("naver", reason, shot)



def human_wait_sec() -> int:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import human_wait_sec as _f
    return _f()



# ── 마지막 실패 사유 ────────────────────────────────────────────────
#
# ★ 왜 반환형(bool)을 안 바꾸나: `refresh_naver_cookies` 호출자가 13곳이다.
#   반환형을 넓히면 그 전부를 손대야 하고, 하나라도 놓치면 조용히 깨진다.
#   사유는 옆문으로 노출한다 — 필요한 호출자만 읽는다.
_LAST_FAILURE: str = ""
_LAST_SHOT: str = ""      # 마지막 로그인 정지 화면(사람 호출에 동봉)


def _fail(reason: str, msg: str = "") -> bool:
    """실패를 기록하고 False 를 돌려준다 — 사유를 잃지 않는 유일한 출구."""
    global _LAST_FAILURE
    _LAST_FAILURE = reason
    if msg:
        print(msg)
    # ★ 캡차·기기인증처럼 *사람이 필요한* 실패는 여기 한 곳에서 처리한다(①).
    #   분기마다 호출을 흩뿌리면 새 사유가 생길 때 또 샌다.
    #   판정 목록은 이미 있는 CAPTCHA_REASONS 에서 파생 — 새 목록을 만들지 않는다(②).
    if reason in CAPTCHA_REASONS:
        try:
            mark_login_backoff(reason)
            alert_human_login_needed(reason, _LAST_SHOT)
        except Exception as _e:                          # noqa: BLE001
            print(f"  ⚠️ 사람 호출/백오프 처리 실패: {_e}")
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


# ★ 이 `_fail()` 사유 중 *사람이 화면 앞에 있어야만* 풀리는 것 — 코드 수정으로 해결 불가.
#   (captcha_present() 가 실제 CAPTCHA 요소를 찾은 뒤에만 나는 사유 — 사람이 로그인해야
#   사라진다.) network_down·credentials_missing·login_button_click 등 나머지 사유는
#   진짜 코드/설정 결함일 수 있어 여기 넣지 않는다 — GUARDIAN Tier-2 가 계속 잡아야
#   사람이 알아챈다. 이 상수가 "무엇이 CAPTCHA 인가"의 단일 진실 소스 — GUARDIAN 쪽은
#   여기서 파생만 한다(JARVIS07_GUARDIAN/severity.py `_login_human_required_types`).
CAPTCHA_REASONS = frozenset({"captcha_unattended", "captcha_timeout"})

# ★ 백오프로 인한 즉시-거절도 사람이 필요한 것과 같은 결과다 (2026-08-11, ERRORS [615] 후속).
#   백오프 중엔 `_fail(BACKOFF_REASON, ...)` 로 실제 로그인 시도조차 하지 않고 즉시
#   실패한다 — 근본 사유는 여전히 "CAPTCHA 를 사람이 풀어야 함" 이지 코드 결함이 아니다.
BACKOFF_REASON = "backoff"
HUMAN_REQUIRED_REASONS = CAPTCHA_REASONS | {BACKOFF_REASON}

# harness Issue 가 쓰는 로그인 무효 kind 의 접두사 — 두 writer(economic·theme)가 공유.
_LOGIN_INVALID_KIND = "login_invalid"


def login_invalid_kind(reason: str) -> str:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import login_invalid_kind as _f
    return _f(reason)



def is_human_required_login_kind(kind: str) -> bool:
    """★ 2026-08-13 — 판정 본체는 `login_manager` 단독(플랫폼 중립 승격). 여기는 위임만.
    사본을 남기면 한쪽만 고쳐진다 — 실제로 `clear_login_backoff` 가 공유 파일을 통째
    삭제해 **네이버 로그인 성공이 티스토리 백오프까지 지우는** 버그가 났다.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import is_human_required_login_kind as _f
    return _f(kind)



# ══════════════════════════════════════════════════════════════════
# 세션이 실제로 필요한 곳 = 발행이 여는 화면 (판정·수집·발행 공통 단일 소스)
# ══════════════════════════════════════════════════════════════════
#
# ★ 왜 URL 을 여기 한 곳에 두는가 (2026-08-10 — 그날 07:00 경제 네이버 미발행)
#   판정과 소비가 **서로 다른 문**을 두드리고 있었다. 판정은 `www.naver.com` HTML 에
#   "로그아웃" 글자가 있으면 유효라 했는데, 정작 발행은 `blog.naver.com/{ID}/postwrite`
#   를 연다. 네이버는 두 화면의 권한을 따로 본다.
#   실측(08-10 07:00, 같은 쿠키): 포털 200 정상 / 글쓰기 nidlogin 바운스(565바이트 JS 스텁).
#   그래서 로그에 "✅ 쿠키 유효" 바로 다음 줄이 "⚠️ 쿠키 브라우저 적용 실패" 였다.
#   판정·수집·발행이 같은 문을 쓰게 URL 을 단일 소스로 둔다(①②).
def blog_write_url() -> str:
    """발행이 실제로 여는 글쓰기 화면 — 판정·발행 공통 단일 소스."""
    return f"https://blog.naver.com/{NV_ID}/postwrite"


def blog_home_url() -> str:
    """블로그 홈 — *쿠키 수집용*.

    글쓰기 화면은 로그인이 없으면 즉시 nidlogin 으로 튕겨 blog 도메인 쿠키를 남기지
    않는다. 수집은 홈으로 들러야 JSESSIONID·BA_DEVICE 가 잡힌다.
    """
    return f"https://blog.naver.com/{NV_ID}"


# 발행에 필요한 인증 쿠키 — 이름을 여기저기 박지 않는다(②).
AUTH_COOKIE_NAMES = ("NID_AUT", "NID_SES")


def has_publish_auth(cookies) -> bool:
    """이 쿠키 묶음으로 발행이 가능한가 — 이름 판정의 **단일 지점**.

    selenium `get_cookies()` 결과와 pkl 로드 결과를 모두 받는다(둘 다 dict 리스트).
    종전엔 `{"NID_AUT","NID_SES"} <= names` 가 네 곳에 복사돼 있었다.
    """
    try:
        names = {c["name"] for c in cookies}
    except (TypeError, KeyError):
        return False
    return set(AUTH_COOKIE_NAMES) <= names


def auth_persistence(cookies, *, now: float | None = None) -> dict:
    """이 묶음이 **브라우저 종료 뒤에도** 살아 있을 것인가 — 미래형 질문. **게이트 아님.**

    ★ 두 질문을 함수로 가른다 (2026-08-13, 사용자 지시):
      · `has_publish_auth()` — "지금 이 묶음으로 발행 문을 열 수 있는가" (현재형, 게이트)
      · `auth_persistence()` — "내일도 열 수 있는가"                    (미래형, 경보)
      **하나로 합치면 발행이 즉시 전면 중단된다.** 실측: 지금 저장된 pkl 의
      NID_AUT·NID_SES 가 둘 다 세션 쿠키다. `has_publish_auth` 를 조이는 순간
      `check_cookie_valid` → `cookie_valid_http` → `verify_all_logins(ok=False)` →
      harness precondition(경제·테마) 차단 → `_naver_cookie_ready` False 로 연쇄한다.
      게다가 복구 경로(`refresh_naver_cookies`)는 캡차·백오프로 막혀 있어 자력 복귀가
      불가능하다. 그래서 이 함수의 결과는 **어떤 분기 조건에도 등장하지 않는다** —
      기록·알림 전용이다(회귀 테스트로 못 박혀 있다).

    ★ '모름' 을 '아님' 으로 단정하지 않는다 — `captcha_present()`·`cookie_valid_http()`
      가 이미 쓰는 3-상태 계약을 그대로 따른다(커밋 4e09141 의 교훈).
      `durable is None` ⇔ `has_publish_auth(cookies) is False`(판정 불가).

    ★ 임계값·이름을 새로 만들지 않는다(②) — 대상은 기존 `AUTH_COOKIE_NAMES`,
      기준 시각은 `time.time()`.

    Returns:
        {"durable":      True(전부 미래 expiry) / False(하나라도 세션·과거) / None(판정 불가),
         "session_only": AUTH_COOKIE_NAMES 중 expiry 없음·과거인 이름(정렬된 tuple),
         "min_expiry_h": 최소 잔여 시간(h). session_only 가 있거나 판정 불가면 None}
    """
    _now = time.time() if now is None else float(now)
    if not has_publish_auth(cookies):
        return {"durable": None, "session_only": (), "min_expiry_h": None}

    # 같은 이름이 도메인별로 여러 개 존재할 수 있다(실측 pkl 에 중복 이름 있음).
    # 하나라도 미래 expiry 가 있으면 그 이름은 지속된다 → 이름별 **최댓값** 으로 본다.
    best: dict = {}
    for c in cookies:
        try:
            name = c["name"]
        except (TypeError, KeyError):
            continue
        if name not in AUTH_COOKIE_NAMES:
            continue
        try:
            exp = float(c.get("expiry") or 0)
        except (TypeError, ValueError):
            exp = 0.0
        if exp > best.get(name, 0.0):
            best[name] = exp

    session_only = tuple(sorted(n for n in AUTH_COOKIE_NAMES if best.get(n, 0.0) <= _now))
    if session_only:
        return {"durable": False, "session_only": session_only, "min_expiry_h": None}
    return {"durable": True, "session_only": (),
            "min_expiry_h": min(best[n] - _now for n in AUTH_COOKIE_NAMES) / 3600}


def _warn_if_session_only(cookies) -> None:
    """세션 전용 쿠키로 저장됐음을 **기록·알림** 한다 — 흐름은 바꾸지 않는다.

    ★ 여기가 '로그인 상태 유지'(`enable_keep_login`) 가 실제로 먹었는지 재는 **유일한
      계측점** 이다. 저장이 `_save_cookies` 하나를 지나므로 폼로그인·수동로그인 두 경로가
      모두 계측된다. 코드에 체크 단계가 있다는 사실은 적용의 증거가 아니다 —
      결과(expiry)를 읽어서 확인한다(CLAUDE.md '설치 플래그는 적용의 증거가 아니다').
    ★ 로그인은 **성공** 이다 — 반환·흐름을 건드리지 않는다(R1: 게이트가 되면 전면 중단).
    """
    try:
        p = auth_persistence(cookies)
        if p["durable"] is not False:
            return
        names = "/".join(p["session_only"])
        print(f"  ⚠️ 세션 전용 쿠키로 저장됨: {names} (expiry 없음) — "
              f"브라우저 종료 시 프로필에서 증발한다")
        # 오류 타입은 이미 있는 판단(사유)에서 파생한다 — 중앙 매핑표를 두지 않는다(②).
        _g_report(naver_login_error_type("session_only_cookies"), "writer",
                  message=(f"네이버 인증 쿠키가 세션 전용({names}) — 브라우저 종료 시 증발. "
                           f"'로그인 상태 유지' 미적용 가능성"),
                  module=__name__, func_name="_save_cookies")
        try:
            from shared.notify import send_tg as _tg     # noqa: PLC0415
            _tg("🔓 *네이버 쿠키가 세션 전용으로 저장됐습니다*\n"
                f"대상: {names} (만료시각 없음)\n"
                "지금은 발행되지만 브라우저를 닫으면 증발합니다 — "
                "반나절 뒤 전체 재로그인(=캡차)으로 떨어집니다.\n"
                "로그인 화면의 '로그인 상태 유지' 가 켜졌는지 확인하세요.")
        except Exception as e:                           # noqa: BLE001
            print(f"  ⚠️ 지속성 알림 전송 실패: {type(e).__name__}: {e}")
    except Exception as e:                               # noqa: BLE001
        # 계측이 저장을 죽이면 안 된다.
        print(f"  ⚠️ 쿠키 지속성 계측 실패(무시): {type(e).__name__}: {e}")


def session_urls() -> tuple:
    """네이버 세션이 걸쳐 있는 도메인 — **수집과 주입이 같은 목록을 쓴다**(①).

    ★ 왜 공개인가 (2026-08-11): 수집만 3도메인을 돌고 주입은 www 한 곳에서만 해서,
      19개를 모아 놓고 브라우저엔 10개만 들어갔다(실측 로그 "10개 추가 / 7개 실패").
      Chrome 은 www.naver.com 문서에서 blog·nid 도메인 쿠키를 거부한다.
      거두는 곳과 넣는 곳이 어긋나면 반쪽 세션이 된다 — 목록을 한 곳에서 준다.
    """
    return ("https://www.naver.com", "https://nid.naver.com", blog_home_url())


def _harvest_urls() -> tuple:
    """하위 호환 별칭 — 내부 호출자용. 목록의 주인은 session_urls()."""
    return session_urls()


def _harvest_cookies(driver) -> list:
    """네이버 쿠키를 **도메인을 순회하며 누적** 수집한다 — 수집 단일 진입점.

    ★ 왜 (2026-08-10 — 07:00 경제 네이버 미발행의 근본 원인)
      selenium `driver.get_cookies()` 는 *현재 문서에서 접근 가능한* 쿠키만 준다.
      한 도메인에서 한 번 부르면 다른 도메인 쿠키는 통째로 빠진다.
      그런데 수집이 **3벌**로 흩어져 서로 다른 곳을 들렀다:
        · 이미-로그인 경로 : www→nid 방문 후 `get_cookies()` 로 **덮어씀**(누적 아님)
        · 폼로그인 성공 경로: www 만 보고 `get_cookies()` — nid·blog 를 **안 들름**
        · 수동 경로        : www→nid→blog 순회 **누적** (셋 중 유일하게 옳았다)
      실측 사고: 08-09 23:23 로그인은 성공했는데 두 번째 경로로 저장돼
      blog 도메인 쿠키(BA_DEVICE·JSESSIONID)가 **0개**인 pkl 이 만들어졌다.
      그 pkl 은 포털 판정을 통과하고 글쓰기에서 튕긴다 → 매 발행이 전체 로그인 →
      무인 CAPTCHA → 스스로 못 빠져나오는 단방향 고장. 수집을 한 곳으로 모은다(①).

    ★ 키가 이름+도메인인 이유: 같은 이름이 도메인별로 따로 존재한다
      (실측 pkl 에 `NM_media_current` 2개). 이름만 키로 쓰면 하나가 조용히 사라진다.
    """
    merged: dict = {}
    for url in _harvest_urls():
        try:
            driver.get(url)
            time.sleep(random.uniform(1.2, 2.0))
            for c in driver.get_cookies():
                merged[(c["name"], c.get("domain", ""))] = c
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠️ 쿠키 수집 — {url} 방문 실패, 건너뜀: {type(e).__name__}")
    return list(merged.values())


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
    # 저장에 성공했다 = 로그인이 실제로 됐다 → 사람 호출 상태를 푼다(단일 지점).
    try:
        clear_login_backoff()
    except Exception:                                    # noqa: BLE001
        pass
    try:
        _os.chmod(COOKIE_FILE, 0o600)
    except OSError:
        pass
    # ★ 지속성 계측 — 저장이 여기 하나를 지나므로 두 로그인 경로가 모두 걸린다(①).
    #   판정은 하되 **막지 않는다**: 반환·흐름 불변(R1).
    _warn_if_session_only(cookies)


# ★ 쿠키 나이 임계값의 **단일 진실 소스** — 네이버 도메인이 소유한다.
#   `login_manager.auto_refresh_if_needed()` 는 이 값을 *호출 시점에 조회* 해 파생한다
#   (모듈 로드 시 받아두면 사본이 되어 여기를 고쳐도 저쪽은 옛 값을 쓴다).
#   같은 숫자를 다른 파일에 다시 적지 말 것(①).
COOKIE_MAX_AGE_HOURS = 10   # 이 시간 이상 된 쿠키는 갱신


_UA_FOR_CHECK = (                       # 세션 판정용 UA — 두 판정 함수가 공유(② 값 복제 금지)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def _session_alive_http(jar: dict, timeout: float) -> "bool | None":
    """발행이 여는 화면으로 세션을 판정한다 — **판정 규칙의 단일 지점**.

    ★ 왜 포털이 아니라 글쓰기 화면인가 (2026-08-10)
      종전 두 판정 함수는 `www.naver.com` HTML 에 "로그아웃" 글자가 있는지로 봤다.
      그런데 네이버는 포털과 블로그 글쓰기의 권한을 **따로** 본다. 실측(08-10 07:00):
      같은 쿠키로 포털은 200 정상, 글쓰기는 nidlogin 바운스.
      그래서 게이트가 "유효" 라 통과시킨 쿠키로 발행자가 튕겨 전체 로그인 →
      무인 CAPTCHA 로 떨어졌다. **판정은 소비처와 같은 문을 두드려야 한다.**

    Returns: True(유효) / False(만료) / None(판정 불가 — 네트워크 등)
    """
    try:
        import requests as _req                           # noqa: PLC0415
        res = _req.get(blog_write_url(), cookies=jar, timeout=timeout,
                       headers={"User-Agent": _UA_FOR_CHECK,
                                "Accept-Language": "ko-KR,ko;q=0.9"},
                       allow_redirects=True)
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 네이버 세션 판정 불가: {type(e).__name__}: {e}")
        return None
    # 로그인이 없으면 네이버는 에디터 대신 nidlogin 으로 보내는 JS 스텁을 준다.
    # (실측: 미인증 응답 565바이트, 본문에 nidlogin.login 리다이렉트 한 줄)
    return "nidlogin" not in res.text


def check_cookie_valid() -> bool:
    """
    저장된 쿠키로 **발행이 여는 글쓰기 화면**에 실제 HTTP 요청을 보내 로그인 상태 확인.
    브라우저 없이 requests만 사용 → 빠름 (1~2초).

    ★ 만료 *시각* 은 보지 않는다 — 종전 docstring 은 "NID_AUT/NID_SES 만료 시간을
      확인" 한다고 적었으나 본문에 그런 코드가 없었다(문서-코드 드리프트, 2026-08-10 정정).
      세션 생사는 시각 계산이 아니라 실제 접근으로 판정한다.
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

    # ── 발행에 필요한 인증 쿠키가 있는가 (이름 판정은 has_publish_auth 단독) ──
    if not has_publish_auth(raw_cookies):
        _have = sorted({c["name"] for c in raw_cookies})
        print(f"  ❌ {'/'.join(AUTH_COOKIE_NAMES)} 쿠키 없음 → 브라우저 로그인 불가 (보유: {_have})")
        return False

    jar = {c["name"]: c["value"] for c in raw_cookies}
    alive = _session_alive_http(jar, timeout=8)
    if alive is None:
        # 네트워크 오류는 만료로 보지 않음 → True 반환해서 갱신 시도 막음
        return True
    print("  ✅ 쿠키 유효 (글쓰기 화면 접근 확인)" if alive
          else "  ❌ 쿠키 만료 (글쓰기 화면이 로그인으로 바운스)")
    return alive


def cookie_valid_http(timeout: float = 8.0) -> "bool | None":
    """브라우저 없이 네이버 세션 유효성 판정 — 티스토리 `cookie_valid_http()` 와 **대칭**.

    ★ 왜 따로 두는가 (2026-08-09, ERRORS [596] 후속)
      `check_cookie_valid()` 는 *갱신 여부를 정하는* 함수라 네트워크 오류에 **True** 를
      돌려준다("모르면 갱신하지 말자"). 그 의미는 그 목적엔 맞지만, 건강진단
      (`verify_all_logins`)이 그대로 쓰면 **네트워크 끊김이 '정상' 으로 보고** 된다 —
      '모른다' 를 '정상' 으로 적는 셈이다. 오늘 실측만 봐도 RADAR 실패 264건 중 263건이
      DNS 이름풀이 실패였으니 실제로 밟는 경로다.
      그래서 *판정* 은 3-상태로 따로 노출하고, 판정 규칙 자체는 복제하지 않는다(①).

    Returns: True(유효) / False(만료·쿠키없음) / None(판정 불가 — 네트워크 등)
    """
    if not COOKIE_FILE.exists():
        return False
    try:
        import pickle as _pk                              # noqa: PLC0415
        import requests as _req                           # noqa: PLC0415
        with open(COOKIE_FILE, "rb") as _f:
            raw = _pk.load(_f)
        if not has_publish_auth(raw):
            return False                                  # 핵심 쿠키 부재 = 확실한 만료
        jar = {c["name"]: c["value"] for c in raw}
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 네이버 쿠키 파일 판정 불가: {type(e).__name__}: {e}")
        return None
    # 판정 규칙은 `_session_alive_http` 단독 — 여기서 복제하지 않는다(①).
    return _session_alive_http(jar, timeout=timeout)


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
    # ★ 백오프 확인 — 기록만 하고 안 읽으면 무의미하다 (2026-08-11, ERRORS [615]).
    #   실제로 `cookie_watch.json` 의 vanished 가 그렇게 죽어 있었다(소비처 0곳).
    #   캡차를 만난 뒤 무인 반복 시도를 멈추는 것이 이 판정의 전부다.
    #   사람이 직접 푸는 `manual_login_and_save` 는 이 경로를 지나지 않으므로 영향 없다.
    _blk = login_backoff_reason()
    if _blk:
        return _fail(BACKOFF_REASON, f"  ⏸ {_blk}")

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
            # ★ 도메인 순회 누적은 `_harvest_cookies` 단독 (①) — 여기서 재구현하지 않는다.
            cookies = _harvest_cookies(driver)
            if has_publish_auth(cookies):
                _save_cookies(cookies)
                print(f"  ✅ 프로필 세션 유효 — 쿠키 추출 완료 ({len(cookies)}개, NID_AUT/SES 포함)")
                return True
            key_names = {c["name"] for c in cookies}
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

        # ── '로그인 상태 유지' — 반드시 **로그인 버튼을 누르기 전** ─────────────
        # 이게 꺼진 채 로그인하면 NID_AUT/NID_SES 가 세션 쿠키로 발급되고, Chrome 종료
        # 시 프로필에서 증발해 다음 회차 step0 가 실패 → 폼로그인 → 캡차 → 백오프 →
        # 미발행으로 이어진다(08-10~08-13 실측 사슬). 종전엔 이 단계가 아예 없었다.
        # ★ 반환값을 분기에 쓰지 않는다 — 못 켜도 로그인은 그대로 진행한다.
        enable_keep_login(driver)

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
            # ★ 판정은 요소로 하되 **모를 수 있다** (ERRORS [600]·[606]).
            #   낱말 판정은 캡차 없는 페이지에서도 항상 참이었고(오탐),
            #   추측으로 만든 요소 선택자는 진짜 캡차를 놓쳤다(미탐).
            #   여기서 증거를 남겨 다음 판정을 고칠 재료로 삼는다.
            _shot = capture_login_stuck(driver, "redirect_timeout")
            globals()["_LAST_SHOT"] = _shot or ""
            if _shot:
                print(f"  📸 로그인 정지 화면 저장: {_shot}.(html|png)")
            if captcha_present(driver) is True:
                _wait = human_wait_sec()
                if _wait <= 0:
                    # 무인 — 화면 앞에 아무도 없다. 기다려도 결과가 같으니 즉시 부른다.
                    return _fail("captcha_unattended",
                                 "  ❌ CAPTCHA 요소 확인 — 무인 실행이라 대기하지 않는다 "
                                 "(사람이 직접 로그인해야 함)")
                print(f"  ⚠️  CAPTCHA 감지 — 화면에서 직접 풀어주세요 (최대 {_wait}초 대기)")
                try:
                    WebDriverWait(driver, _wait).until(
                        lambda d: "nidlogin" not in d.current_url)
                    print("  ✅ 수동 인증 완료")
                except Exception:
                    return _fail("captcha_timeout", f"  ❌ {_wait}초 내 인증 미완료 — 종료")
            else:
                # 캡차인지 그냥 느린 것인지 **모른다**. 기다려 본다 —
                # 느린 로그인이면 성공하고, 캡차면 어차피 시간이 지나야 알 수 있다.
                # ★ "캡차 요소 없음" 이라고 단정하지 않는다(ERRORS [606] — 그 문구가 거짓이었다).
                print(f"  ⏳ 로그인 전환 대기 ({LOGIN_REDIRECT_WAIT_SEC}초) — 캡차 여부 판정 불가")
                try:
                    WebDriverWait(driver, LOGIN_REDIRECT_WAIT_SEC).until(
                        lambda d: "nidlogin" not in d.current_url)
                    print("  ✅ 로그인 완료 (지연) — 사람이 캡차를 풀었을 수도 있다")
                except Exception:
                    return _fail("login_stuck_unknown",
                                 f"  ❌ {15 + LOGIN_REDIRECT_WAIT_SEC}초 내 로그인 전환 없음 "
                                 f"— 캡차일 수 있음(판정 불가). 저장된 화면 확인: {_shot or '저장 실패'}")

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
            # ★ 종전엔 `driver.get_cookies()` 한 번 — 그 순간 문서가 www.naver.com 이라
            #   blog·nid 쿠키가 통째로 빠진 pkl 이 저장됐다(08-09 23:23 실측: blog 쿠키 0개).
            #   그 pkl 이 포털 판정만 통과하고 글쓰기에서 튕겨 08-10 미발행으로 이어졌다.
            cookies = _harvest_cookies(driver)
            if not has_publish_auth(cookies):
                return _fail("cookie_harvest_incomplete",
                             f"  ❌ 로그인은 됐으나 인증 쿠키 수집 실패 "
                             f"(보유: {sorted({c['name'] for c in cookies})})")
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
    print("  ☑️  로그인 화면의 '로그인 상태 유지' 를 **켠 채** 로그인해 주세요 "
          "(꺼져 있으면 브라우저 종료 시 세션이 증발합니다).")
    print("  로그인 완료 후 Enter를 누르면 쿠키가 자동 저장됩니다.")
    driver.get("https://nid.naver.com/nidlogin.login")
    # ★ 수동 경로에도 켠다 — 2026-08-13 사용자가 **손으로** 로그인한 쿠키조차 세션이었다.
    #   사람이 체크를 잊어도 여기서 켜지면 다음 회차가 산다(못 찾으면 위 안내문이 대신한다).
    time.sleep(2)
    enable_keep_login(driver)

    try:
        input("\n  ✅ 로그인 완료 후 여기서 Enter: ")
        # 로그인 확인
        driver.get("https://www.naver.com")
        time.sleep(2)
        src = driver.page_source
        logged = "로그아웃" in src or (NV_ID and NV_ID in src)
        if logged:
            # 여러 도메인 방문하여 모든 쿠키 수집 (BA_DEVICE, JSESSIONID 등 포함)
            # ★ 셋 중 유일하게 옳았던 순회 누적 — 이제 `_harvest_cookies` 가 단독 소유(①).
            cookies = _harvest_cookies(driver)
            _save_cookies(cookies)
            names = {c["name"] for c in cookies}
            print(f"  ✅ 쿠키 저장 완료 ({len(cookies)}개): {names}")
            # ★ 판정 사본 제거 (2026-08-13): 종전엔 여기서 세션 쿠키를 **직접 판별**해
            #   출력만 하고 True 를 돌려줬다 — 호출자(CLI·사람)는 "성공" 만 보고, 반나절
            #   뒤 같은 자리에서 죽는 것을 알 길이 없었다. 판정은 `auth_persistence` 단독(①),
            #   사실 전달은 `_save_cookies` → `_warn_if_session_only`(GUARDIAN 기록+텔레그램).
            # ★ 반환값은 True 를 유지한다 — 사람이 실제로 성공시킨 로그인을 실패로 적으면
            #   CLI exit code 가 거짓말이 된다(`--manual` 은 성공 시 0 이어야 한다).
            _p = auth_persistence(cookies)
            if _p["durable"] is False:
                print(f"  ⚠️ 세션 전용 쿠키: {'/'.join(_p['session_only'])} — "
                      f"브라우저 종료 시 증발한다('로그인 상태 유지' 미적용)")
            elif _p["durable"] is True:
                print(f"  🔒 지속 쿠키 확인 — 최소 잔여 {_p['min_expiry_h']:.1f}시간")
            else:
                print("  ⚠️ 인증 쿠키가 없어 지속성 판정 불가")
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
    # ★ try/except 로 감싸지 않는다 (2026-08-10) — 감싸는 순간 ImportError 가 삼켜져
    #   "preflight 가 있다" 는 착각만 남고 **실제로는 한 번도 안 도는** 상태가 된다.
    #   실측(2026-08-10): 진입점 16곳 중 8곳이 그 상태였고, 경고는 stdout 으로만 나가는데
    #   데몬 stdout 은 /dev/null 이라 어디에도 안 남았다 — 완전한 침묵이었다.
    #   루트 경로는 파일 상단 부트스트랩이 보장한다. 여기서 실패하면 진짜 환경 문제다(fail-closed).
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight(strict=True)

    if "--check" in sys.argv:
        # 쿠키 유효성만 확인 (갱신 안 함)
        valid = check_cookie_valid()
        if valid:
            age_h = (time.time() - COOKIE_FILE.stat().st_mtime) / 3600 if COOKIE_FILE.exists() else 0
            print(f"  📋 쿠키 파일 나이: {age_h:.1f}시간")
        # 핵심 쿠키의 **지속성** 출력 — 판정은 `auth_persistence` 단독(①).
        # ★ 종전엔 여기에 판별 사본이 있었고, expiry 가 없는 세션 쿠키를
        #   `만료까지 0.0시간` 으로 찍어 *만료된 쿠키와 구별이 안 됐다*.
        #   지금 pkl 이 정확히 그 상태다 — 진단 명령이 진단을 못 하고 있었다.
        if COOKIE_FILE.exists():
            try:
                _p = auth_persistence(pickle.load(open(COOKIE_FILE, "rb")))
                if _p["durable"] is True:
                    print(f"  🔒 지속 쿠키 — 최소 잔여 {_p['min_expiry_h']:.1f}시간")
                elif _p["durable"] is False:
                    print(f"  ⚠️ 세션 전용 쿠키: {'/'.join(_p['session_only'])} "
                          f"(만료시각 없음) — 브라우저 종료 시 증발")
                else:
                    print(f"  ⚠️ {'/'.join(AUTH_COOKIE_NAMES)} 부재 — 지속성 판정 불가")
            except Exception as _e:                      # noqa: BLE001
                print(f"  ⚠️ 지속성 판정 실패: {type(_e).__name__}: {_e}")
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
