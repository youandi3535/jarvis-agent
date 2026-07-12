"""
naver_poster.py v12
━━━━━━━━━━━━━━━━━━━━━━━━━━━
100% 좌표 기반 pyautogui 입력
Selenium은 브라우저 열기/로그인/페이지이동만 담당
━━━━━━━━━━━━━━━━━━━━━━━━━━━
좌표 기준 (before_input.png, popup.png 스크린샷):
  제목       : (253, 337)
  본문       : (350, 465)
  발행버튼   : (1375, 81)   ← 에디터 우상단
  카테고리   : (1150, 152)  ← 팝업 드롭다운
  테마분류   : (1150, 387)  ← 드롭다운 4번째 항목
  태그입력   : (1150, 500)
  최종발행   : (1311, 677)  ← 팝업 우하단
"""
import os, re, time, random, sys, pickle, subprocess
from pathlib import Path
from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

load_dotenv()
NV_ID  = os.getenv("NV_USERNAME", "")
NV_PW  = os.getenv("NV_PASSWORD", "")

# 발행 직후 URL 캡처 — 외부에서 naver_poster._last_post_url 로 읽음
_last_post_url: str = ""


def _fetch_recent_naver_posts(n: int = 1) -> list:
    """네이버 블로그 RSS에서 최근 n개 글 제목+URL 반환"""
    import xml.etree.ElementTree as ET
    rss_url = f"https://rss.blog.naver.com/{NV_ID}.xml"
    try:
        import requests as _req
        res = _req.get(rss_url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            return []
        root = ET.fromstring(res.content)
        items = root.findall(".//item")
        result = []
        for item in items[:n * 2]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link",  "").strip()
            if title and link:
                result.append({"title": title, "url": link})
            if len(result) >= n:
                break
        return result
    except Exception:
        return []
IS_MAC = sys.platform == "darwin"

# ── ADR 008 Phase 2 (★ 사용자 박제 2026-05-17) — 경로 anchor ──
# 본 모듈이 JARVIS02_WRITER → JARVIS08_PUBLISH/platforms 로 이관됨에 따라
# *물리적으로 JARVIS02_WRITER 에 있는* 자원(쿠키·chrome_profile·logs)을 가리키도록
# 명시적 anchor 사용. naver_cookies.pkl 와 chrome_profile/ 은 이동 금지 (로그인 상태 보존).
_PROJECT_ROOT     = Path(__file__).resolve().parent.parent.parent  # JARVIS08_PUBLISH/platforms → root
_LEGACY_BASE_DIR  = _PROJECT_ROOT / "JARVIS02_WRITER"               # 옛 위치 — 쿠키·프로필 anchor

COOKIE_FILE   = _LEGACY_BASE_DIR / "naver_cookies.pkl"
BASE_DIR      = _LEGACY_BASE_DIR                                    # 옛 코드 호환 — chrome_profile 등
JARVIS06_BASE = _PROJECT_ROOT / "JARVIS06_IMAGE"                    # 이미지 단일 진입점 (CLAUDE.md 규정)
LOGS_DIR      = _LEGACY_BASE_DIR / "logs"
IMG_DIR       = JARVIS06_BASE / "output" / "screenshots"
IMG_EDITOR    = IMG_DIR / "editor"
IMG_PUBLISH   = IMG_DIR / "publish"
IMG_RESULT    = IMG_DIR / "result"
for d in [LOGS_DIR, IMG_EDITOR, IMG_PUBLISH, IMG_RESULT]:
    d.mkdir(parents=True, exist_ok=True)


def rand(a=0.5, b=1.5):
    time.sleep(random.uniform(a, b))





def _generate_smart_tags(title: str, body_text: str) -> list:
    """네이버 검색 최적화 태그 6개 생성.
    Returns: 6개짜리 태그 리스트 (항상 보장)
    """
    theme_name = title.split()[0] if title else '주식'
    try:
        from JARVIS02_WRITER import length_manager as _LM_naver
    except ImportError:
        import length_manager as _LM_naver
    snippet = body_text[:_LM_naver.BODY_SNIPPET_LEN] if body_text else ''
    try:
        from shared.llm import invoke_text as _inv_cli
        _raw = _inv_cli(
            "writer",
            f"네이버 블로그 검색 최적화 태그 6개를 쉼표로 구분해서 출력하세요.\n"
            f"규칙:\n"
            f"- 실제 네이버에서 검색할 법한 구체적 키워드 (예: 반도체관련주, 2차전지주식, HBM투자)\n"
            f"- 단독 '주식'·'투자' 금지. 반드시 테마명과 결합 (예: {theme_name}주식)\n"
            f"- 공백 없이 붙여쓰기\n"
            f"- 태그 6개만 출력, 다른 말 금지\n\n"
            f"제목: {title}\n"
            f"본문: {snippet}",
            timeout=60
        ) or ""
        parts = [p.strip() for p in _raw.strip().split(',') if p.strip()]
        fallbacks = [f'{theme_name}관련주', f'{theme_name}주식', f'{theme_name}테마주',
                     f'{theme_name}투자', f'{theme_name}종목', f'{theme_name}대장주']
        while len(parts) < 6:
            parts.append(fallbacks[len(parts) % len(fallbacks)])
        return list(dict.fromkeys(parts))[:6]
    except Exception:
        return [f'{theme_name}관련주', f'{theme_name}주식', f'{theme_name}테마주',
                f'{theme_name}투자', f'{theme_name}종목', f'{theme_name}대장주']

def html_to_naver_text(html: str) -> str:
    c = html
    # <body> 이전 모든 내용 제거
    body_match = re.search(r'<body[^>]*>', c, re.IGNORECASE)
    if body_match:
        c = c[body_match.end():]
    else:
        c = re.sub(r'<style[^>]*>.*?</style>', '', c, flags=re.DOTALL)
        c = re.sub(r'<script[^>]*>.*?</script>', '', c, flags=re.DOTALL)
        c = re.sub(r'<head[^>]*>.*?</head>', '', c, flags=re.DOTALL)

    # hero div 제거
    def remove_hero(h):
        s = re.search(r'<div[^>]*class="hero"[^>]*>', h)
        if not s: return h
        i, depth = s.end(), 1
        while i < len(h) and depth > 0:
            o, c2 = h.find('<div', i), h.find('</div>', i)
            if o != -1 and (c2 == -1 or o < c2): depth += 1; i = o + 4
            elif c2 != -1: depth -= 1; i = c2 + 6
            else: break
        return h[:s.start()] + h[i:]
    c = remove_hero(c)

    c = re.sub(r'<img[^>]+src="data:image/[^"]+base64,[^"]*"[^>]*/?>',  '', c)
    c = re.sub(r'<img[^>]+base64,[^>]+>', '', c)
    c = re.sub(r'<img[^>]+alt="([^"]+)"[^>]*/?>',  r'[이미지: \1]', c)
    c = re.sub(r'<img[^>]*/?>',  '', c)
    # h2/h3: 이미지 카드로 처리되므로 텍스트에서 완전 제거 (중복 방지)
    c = re.sub(r'<h[123][^>]*>.*?</h[123]>', '', c, flags=re.DOTALL)
    c = re.sub(r'<h[456][^>]*>(.*?)</h[456]>', r'\n■ \1\n', c, flags=re.DOTALL)
    # table: 이미지로 처리되므로 텍스트에서 완전 제거 (중복 방지)
    c = re.sub(r'<table[\s\S]*?</table>', '', c, flags=re.IGNORECASE)
    c = re.sub(r'<br\s*/?>', '\n', c)
    c = re.sub(r'</p>', '\n\n', c)
    c = re.sub(r'<p[^>]*>', '', c)
    c = re.sub(r'<li[^>]*>(.*?)</li>', r'• \1\n', c, flags=re.DOTALL)
    c = re.sub(r'<[uo]l[^>]*>|</[uo]l>', '\n', c)
    c = re.sub(r'<(strong|b)[^>]*>(.*?)</(strong|b)>', r'\2', c, flags=re.DOTALL)
    c = re.sub(r'<(em|i)[^>]*>(.*?)</(em|i)>', r'\2', c, flags=re.DOTALL)
    c = re.sub(r'<(s|del|strike)[^>]*>(.*?)</(s|del|strike)>', r'\2', c, flags=re.DOTALL)
    c = re.sub(r'<[^>]+>', '', c)
    for e, v in [('&amp;','&'),('&lt;','<'),('&gt;','>'),('&nbsp;',' '),
                 ('&quot;','"'),("&#39;","'"),("&hellip;","…"),("&mdash;","—")]:
        c = c.replace(e, v)
    c = re.sub(r'\n{4,}', '\n\n\n', c)
    c = re.sub(r'[ \t]{2,}', ' ', c)
    c = re.sub(r'~~(.+?)~~', r'\1', c)
    return c.strip()


def _kill_naver_chrome():
    """naver 프로필을 점유 중인 Chrome 프로세스를 종료 (프로필 충돌 방지)."""
    import subprocess as _sp
    _profile = str(_LEGACY_BASE_DIR / "chrome_profile" / "naver")
    result = _sp.run(
        ["pgrep", "-f", f"user-data-dir={_profile}"],
        capture_output=True, text=True
    )
    pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    if pids:
        print(f"  🔪 기존 Chrome(naver 프로필) 프로세스 {len(pids)}개 종료...")
        _sp.run(["kill"] + pids, capture_output=True)
        time.sleep(2)


def _get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    _kill_naver_chrome()   # 프로필 충돌 방지: 기존 Chrome 먼저 종료

    options = Options()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--window-size=1440,900")
    options.add_argument("--window-position=0,0")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--no-first-run")             # "세션 복원" 다이얼로그 방지
    options.add_argument("--no-default-browser-check") # 기본 브라우저 확인 팝업 방지
    # 영구 Chrome 프로필 — 네이버가 동일 기기로 인식해 세션 장기 유지
    _profile_dir = str(_LEGACY_BASE_DIR / "chrome_profile" / "naver")
    options.add_argument(f"--user-data-dir={_profile_dir}")
    options.add_argument("--profile-directory=Default")
    # 비밀번호 저장 팝업 완전 비활성화
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
    })
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"}
    )
    return driver


def _activate_window():
    if IS_MAC:
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to activate'],
            capture_output=True
        )
        time.sleep(0.8)


def _click(x: int, y: int, label: str = ""):
    """화면 절대좌표 클릭"""
    import pyautogui
    pyautogui.FAILSAFE = False
    _activate_window()
    pyautogui.moveTo(x, y, duration=0.3)
    time.sleep(0.2)
    pyautogui.click()
    time.sleep(0.5)
    if label:
        print(f"  🖱️  {label} ({x},{y})")


def _paste(text: str):
    import pyautogui, pyperclip
    pyperclip.copy(text)
    time.sleep(0.3)
    _activate_window()
    # 한글 입력기 초기화
    pyautogui.press('escape')
    time.sleep(0.1)
    if IS_MAC:
        pyautogui.hotkey('command', 'v')
    else:
        pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.8)


# 첫 번째 이미지 업로드 여부 추적
_first_image_uploaded = False

def _cgevent_paste():
    """
    CGEvent kCGHIDEventTap으로 Cmd+V 전송.
    kCGHIDEventTap은 HID(하드웨어 입력 장치) 레벨 — 어느 앱/창이 활성화돼 있든
    현재 키보드 포커스를 가진 필드에 직접 전달됨.
    pyautogui의 kCGSessionEventTap 방식과 달리 Finder 다이얼로그에도 도달함.
    """
    from Quartz import (CGEventCreateKeyboardEvent, CGEventPost,
                        CGEventSetFlags, kCGHIDEventTap, kCGEventFlagMaskCommand)
    V_KEY = 9  # 'v' keycode (ANSI)
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, V_KEY, down)
        CGEventSetFlags(ev, kCGEventFlagMaskCommand if down else 0)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.05)


def _upload_image(img_path: str, driver=None):
    """사진 아이콘 → Cmd+Shift+G → 전체 경로 직접 입력 → 파일 업로드
    (파일명 Spotlight 검색 방식 폐기 — 검색 결과 없을 때 무한 대기 버그 수정)
    """
    global _first_image_uploaded
    import pyautogui as _pg, pyperclip
    abs_path = str(Path(img_path).resolve())
    fname = Path(abs_path).name
    print(f"    📁 업로드: {fname}")

    # 1) 전체 경로를 클립보드에 복사 (다이얼로그 열기 전에 미리)
    pyperclip.copy(abs_path)
    time.sleep(0.3)

    # 2) 사진 아이콘 클릭 → Finder 다이얼로그 열릴 때까지 대기
    _click(34, 164, "사진 아이콘")
    time.sleep(3.0)

    # 3) Cmd+Shift+G → "폴더로 이동" 입력 시트 열기
    #    파일 경로를 붙여넣으면 해당 파일로 직접 이동 (Spotlight 검색 불필요)
    print(f"    📂 Cmd+Shift+G (폴더로 이동)...")
    _pg.hotkey('command', 'shift', 'g')
    time.sleep(1.0)

    # 4) 기존 내용 삭제 후 전체 경로 붙여넣기
    _pg.hotkey('command', 'a')
    time.sleep(0.2)
    print(f"    📋 전체 경로 붙여넣기 (CGEvent kCGHIDEventTap)...")
    try:
        _cgevent_paste()
        print(f"    ✅ 경로 붙여넣기 완료")
    except Exception as _e:
        print(f"    ⚠️ CGEvent 실패({_e}), pyautogui fallback")
        _g_report("writer", _e, module=__name__)
        _pg.hotkey('command', 'v')
    time.sleep(0.8)

    # 5) Enter → 해당 파일로 이동 (파일 경로이면 파일 선택됨)
    _pg.press('return')
    time.sleep(1.5)

    # 6) Enter 한 번 더 → 열기(업로드) 확인
    _pg.press('return')
    time.sleep(5.0)

    _first_image_uploaded = True

    # ★ 이미지 업로드 후 "사진 첨부 방식" 팝업 자동 닫기
    # 네이버 에디터가 이미지 붙여넣기 후 팝업을 자동 표시할 수 있음.
    # 이 팝업이 열려있으면 se-popup-dim 오버레이가 태그 입력·발행 버튼을 차단.
    if driver:
        try:
            _dismiss_naver_popup(driver)
        except Exception:
            pass


def _dismiss_naver_popup(driver) -> bool:
    """네이버 에디터 내 팝업(사진 첨부 방식 등) 감지 후 닫기.

    '사진 첨부 방식' 팝업 또는 기타 se-popup 이 열려있으면
    ESC 키 → X 버튼 순으로 닫기 시도.

    Returns:
        True: 팝업 닫힘 (또는 팝업 없음)
    """
    try:
        # se-popup-dim 또는 se-popup 활성화 여부 확인
        popup_open = driver.execute_script("""
            var dim = document.querySelector('.se-popup-dim:not(.se-popup-dim-transparent)');
            var popup = document.querySelector('.se-popup, .se-popup-container');
            if (dim && dim.offsetParent !== null) return 'dim';
            if (popup && popup.offsetParent !== null) {
                var closeBtn = popup.querySelector('button[data-action="close"], .se-popup-close, button.close');
                if (closeBtn) { closeBtn.click(); return 'close_btn'; }
                return 'popup';
            }
            return null;
        """)
        if popup_open:
            print(f"  ⚠️ [Naver] 팝업 감지({popup_open}) → ESC로 닫기")
            import pyautogui as _pg2
            _pg2.press('escape')
            time.sleep(0.8)
            # 한 번 더 확인
            still_open = driver.execute_script("""
                var dim = document.querySelector('.se-popup-dim:not(.se-popup-dim-transparent)');
                return dim && dim.offsetParent !== null;
            """)
            if still_open:
                _pg2.press('escape')
                time.sleep(0.5)
            return True
    except Exception as _e:
        pass
    return False


def _verify_naver_published(driver) -> bool:
    """발행 후 에디터 이탈 여부로 실제 발행 확인.

    ★ ERRORS [278] 수정 2026-06-08 — URL 패턴 누락 + DOM 예외 침묵 근본 수정
    발행 성공 패턴:
      1. URL: blog.naver.com 에 logNo= 파라미터 존재 (Redirect=Log 등 모든 형태 포괄)
      2. URL: blog.naver.com/ID/숫자 — 발행 직후 리다이렉트
      3. DOM 기반 — 글 보기 페이지 요소 (URL 복사·통계·se-viewer 등)
    발행 실패 패턴:
      - 3회 재확인 후에도 에디터 URL 유지 + DOM 시그널 없음

    Returns:
        True: 발행 확인됨
        False: 에디터 상태 유지 (발행 미완료)
    """
    import time as _time
    import re as _re

    # ★ ERRORS [278][279] — 3회 × 4초 = 최대 ~12초 추가 대기 (SPA 전환 충분 보장)
    # ★ 사용자 박제 2026-07-06 — 재시도 상한 전역 3회 통일 (기존 4회 × 4초)
    for _attempt in range(3):
        try:
            current_url = driver.current_url
            print(f"  [verify] attempt {_attempt+1}/3 — URL: {current_url[:120]}")

            # ── URL 기반 체크 ──────────────────────────────────────
            # ★ ERRORS [278] 핵심 수정: blog.naver.com 에 logNo= 있으면 발행 성공
            if "blog.naver.com" in current_url and "logNo=" in current_url:
                print(f"  [verify] URL logNo 패턴 확인 → 발행 성공")
                return True

            # blog.naver.com/ID/숫자 — 직접 경로 형태
            if _re.search(r'blog\.naver\.com/\w+/\d+', current_url):
                print(f"  [verify] URL path 패턴 확인 → 발행 성공")
                return True

            # ★ ERRORS [279] — 에디터 이탈 자체가 발행 성공 시그널
            # blog.naver.com 에 있고 에디터(/postwrite) 가 아니면 발행 완료 페이지
            if "blog.naver.com" in current_url and "/postwrite" not in current_url and "/login" not in current_url:
                print(f"  [verify] 에디터 이탈 확인 ({current_url[:80]}) → 발행 성공")
                return True

            # 에디터 URL (write/postwrite 에 logNo 없음) — 아직 전환 안 됨
            if "/postwrite" in current_url and "logNo=" not in current_url:
                print(f"  [verify] 에디터 URL 유지 (logNo 없음) → 재확인 대기")
                if _attempt < 2:
                    _time.sleep(4)
                continue

        except Exception as _url_err:
            print(f"  [verify] URL 체크 예외: {_url_err}")

        # ── DOM 기반 체크 ──────────────────────────────────────
        try:
            published_signal = driver.execute_script("""
                // 글 보기 페이지에만 존재하는 요소
                if (document.querySelector('.se-viewer')) return 'se-viewer';
                if (document.querySelector('.blog-post')) return 'blog-post';
                if (document.querySelector('.post_ct')) return 'post_ct';
                if (document.querySelector('[class*="PostView"]')) return 'PostView';
                // ★ ERRORS [279] — children 필터 제거: 버튼 내 아이콘 자식 요소 있어도 검색
                var allElems = document.querySelectorAll('*');
                for (var i = 0; i < Math.min(allElems.length, 5000); i++) {
                    var e = allElems[i];
                    var t = (e.innerText || e.textContent || '').trim();
                    if (t === 'URL 복사' || t.includes('URL 복사') || t === '통계' || t.endsWith('통계'))
                        return 'elem:' + t.substring(0, 30);
                }
                // body 전체 텍스트에서 발행 완료 시그널 (태그 무관 — 최종 fallback)
                var bodyText = document.body ? (document.body.innerText || '') : '';
                if (bodyText.includes('URL 복사') || bodyText.includes('통계'))
                    return 'body:text_found';
                return null;
            """)
            if published_signal:
                print(f"  [verify] DOM 기반 발행 확인 (attempt {_attempt+1}): {published_signal}")
                return True
            else:
                print(f"  [verify] DOM 시그널 없음 (attempt {_attempt+1})")
        except Exception as _dom_err:
            print(f"  [verify] DOM 체크 예외 (attempt {_attempt+1}): {_dom_err}")

        # 아직 전환 안 됨 → 4초 대기 후 재확인
        if _attempt < 2:
            _time.sleep(4)

    print("  [verify] 3회 시도 모두 실패 → 발행 미완료 판정")
    return False


def _get_browser_offset(driver) -> tuple:
    """브라우저 콘텐츠 영역의 화면 좌상단 좌표"""
    pos = driver.execute_script("""
        return {
            x: window.screenX + (window.outerWidth - window.innerWidth) / 2,
            y: window.screenY + (window.outerHeight - window.innerHeight)
        }
    """)
    return int(pos['x']), int(pos['y'])


def _click_in_browser(driver, rel_x: int, rel_y: int, label: str = ""):
    """브라우저 내 상대좌표 → 화면 절대좌표로 변환 후 클릭"""
    bx, by = _get_browser_offset(driver)
    _click(bx + rel_x, by + rel_y, label)


def _find_publish_btn_el(driver):
    """발행 팝업 내 최종 '발행/등록' 버튼 WebElement 탐색 (우측 하단 팝업 영역)."""
    from selenium.webdriver.common.by import By
    try:
        for b in driver.find_elements(By.TAG_NAME, "button"):
            try:
                txt = (b.text or "").strip()
                if txt not in ("발행", "등록"):
                    continue
                rect = b.rect or {}
                if rect.get("x", 0) > 900 and rect.get("y", 0) > 500 and b.is_displayed():
                    return b
            except Exception:
                continue
    except Exception:
        pass
    return None


def _click_publish_btn(driver, label: str = "최종발행") -> bool:
    """발행 팝업 내 최종 발행 버튼 클릭 — Selenium 신뢰 이벤트 (ERRORS [293] — 2026-07-03).

    ★ 종전 OS 물리 클릭(고정/DOM 좌표 CGEvent)은 화면·윈도우 전면 상태 의존 —
    주간(사용자 기기 사용 중)·in-daemon 실행에서 클릭이 버튼을 빗맞혀 팝업만 닫는
    실패가 결정론적으로 재현 (새벽 subprocess 런은 전부 성공, 06-04~07-03 6회 대조).
    ActionChains 는 CDP 신뢰 이벤트(isTrusted=true)라 OS 포커스·화면 좌표 무관 —
    같은 팝업의 태그 입력이 이미 동일 방식으로 성공 중인 것이 실증.
    ActionChains 실패 시에만 물리 클릭 폴백. 버튼 미발견 시 False (호출자가 재오픈 처리).
    """
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.common.exceptions import ElementClickInterceptedException
    _dim_js = ("document.querySelectorAll('.se-popup-dim:not(.se-popup-dim-transparent)')"
               ".forEach(function(d) { d.remove(); });")
    # ★ 사용자 박제 2026-07-06 — 재시도 상한 전역 3회 통일 (기존 2회)
    for _try in range(3):
        try:
            driver.execute_script(_dim_js)   # dim 오버레이 선제 제거 (ERRORS [247])
        except Exception:
            pass
        btn = _find_publish_btn_el(driver)
        if btn is None:
            return False
        txt = (btn.text or "").strip()
        try:
            ActionChains(driver).move_to_element(btn).pause(0.3).click(btn).perform()
            print(f"  🖱️ {label}({txt}) ActionChains 클릭 완료")
            return True
        except ElementClickInterceptedException:
            print(f"  ⚠️ {label} 클릭 가로챔 — dim 재제거 후 재시도")
            continue
        except Exception as _ce:
            print(f"  ⚠️ {label} ActionChains 실패({_ce}) — 물리 클릭 폴백")
            try:
                r = btn.rect or {}
                _click_in_browser(driver,
                                  int(r.get("x", 0) + r.get("width", 0) / 2),
                                  int(r.get("y", 0) + r.get("height", 0) / 2),
                                  f"{label}(물리 폴백)")
                return True
            except Exception:
                return False
    return False


def _load_cookies_to_browser(driver):
    """
    쿠키 파일을 브라우저에 적용.
    1) www.naver.com: 모든 .naver.com 쿠키 추가 (expired 제외)
    2) 브라우저에 실제로 로드된 쿠키 확인 → NID_AUT/SES 없으면 실패 리포트
    """
    if not COOKIE_FILE.exists():
        return False

    import time as _t
    raw_cookies = pickle.load(open(COOKIE_FILE, "rb"))
    now = _t.time()

    # www.naver.com에서 전체 쿠키 적용
    driver.get("https://www.naver.com")
    time.sleep(2)
    driver.delete_all_cookies()
    time.sleep(0.3)

    added, skipped_expired, failed = 0, 0, 0
    for c in raw_cookies:
        # 만료된 쿠키(expiry가 현재 시간보다 과거)는 Chrome이 거부하므로 건너뜀
        expiry = c.get("expiry")
        if expiry and expiry < now:
            skipped_expired += 1
            continue
        c_copy = {k: v for k, v in c.items() if k not in ("sameSite",)}
        # session 쿠키(expiry 없음)는 expiry 키 자체를 제거해야 Chrome이 수락
        c_copy.pop("expiry", None)
        try:
            driver.add_cookie(c_copy)
            added += 1
        except Exception as e:
            failed += 1

    # 실제로 브라우저에 로드된 쿠키 확인
    loaded = {c["name"] for c in driver.get_cookies()}
    has_auth = "NID_AUT" in loaded and "NID_SES" in loaded
    print(f"  🍪 쿠키 로드: {added}개 추가 / {skipped_expired}개 만료 스킵 / {failed}개 실패")
    print(f"  🍪 브라우저 확인: {loaded}")
    if has_auth:
        print(f"  ✅ NID_AUT/NID_SES 정상 로드됨")
    else:
        print(f"  ❌ NID_AUT/NID_SES 로드 실패 — pkl 쿠키가 불완전하거나 세션 만료됨")
    return True


def _check_blog_login(driver) -> bool:
    """블로그 글쓰기 URL 접근 → 로그인 페이지 리다이렉트 여부로 로그인 상태 확인."""
    driver.get(f"https://blog.naver.com/{NV_ID}/postwrite")
    time.sleep(5)
    cur = driver.current_url
    return "nidlogin" not in cur and "login" not in cur


def _ensure_logged_in(driver) -> bool:
    # 0) 프로필 기존 세션 먼저 확인 (쿠키 덮어쓰기 없이)
    #    → 수동 로그인 후 생성된 유효 세션이 있으면 그대로 사용
    if _check_blog_login(driver):
        print("  ✅ 프로필 세션 유효 → 쿠키 덮어쓰기 생략")
        return True

    # 1) 프로필 세션 만료 → pkl 쿠키 적용 후 재확인
    if _load_cookies_to_browser(driver):
        if _check_blog_login(driver):
            print("  ✅ pkl 쿠키 로그인 유지")
            return True
        print("  ⚠️ 쿠키 브라우저 적용 실패 → 강제 갱신...")
    else:
        print("  ⚠️ 쿠키 파일 없음 → 강제 갱신...")

    # 2) pkl 쿠키도 실패 → 상위 함수(post_to_naver)에서 driver를 닫고 naver 프로필로 재로그인 처리
    # (이 시점에 refresh_naver_cookies를 호출하면 poster Chrome과 naver 프로필 충돌 발생)
    print("  ❌ 로그인 실패 — post_to_naver에서 드라이버 재시작 후 재시도 예정")
    return False


def _pre_check_and_refresh_cookie():
    """
    브라우저 열기 전에 쿠키 유효성을 HTTP 요청으로 확인.
    만료됐으면 pyautogui 타이핑 방식으로 미리 갱신.
    → post_to_naver() 진입 시 항상 호출됨.
    """
    try:
        from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import check_cookie_valid, refresh_naver_cookies
        print("  🔍 네이버 쿠키 사전 확인 중...")
        if not check_cookie_valid():
            print("  🔄 쿠키 만료 확인 → 갱신 시작...")
            ok = refresh_naver_cookies(force=True)
            if ok:
                print("  ✅ 쿠키 갱신 완료 → 포스팅 진행")
            else:
                print("  ❌ 쿠키 갱신 실패 — 포스팅 시도는 계속합니다")
        else:
            print("  ✅ 쿠키 유효 → 포스팅 진행")
    except Exception as e:
        print(f"  ⚠️ 쿠키 사전 확인 오류: {e}")
        _g_report("writer", e, module=__name__)


def post_to_naver(title: str, html_content: str, img_dir: str = None, blocks: list = None,
                  category: str = "테마분류", tags: list = None,
                  related_posts: list = None,
                  edit_log_no: str = "") -> bool:
    """edit_log_no 가 주어지면 해당 글 수정 모드 진입 (검증된 발행 흐름 그대로 재사용)."""
    import pyautogui
    pyautogui.FAILSAFE = False

    # ── 쿠키 사전 확인 & 갱신 (브라우저 열기 전) ──────────────
    _pre_check_and_refresh_cookie()

    print("  🔄 HTML → 텍스트 변환 중...")
    naver_text = html_to_naver_text(html_content)

    # blocks 가 있으면 blocks 텍스트 기준 글자수 측정 (length_manager 위임)
    try:
        from JARVIS02_WRITER import length_manager as _L_np
    except ImportError:
        import length_manager as _L_np  # 같은 폴더 직접 실행 시
    if blocks:
        _blocks_text = '\n\n'.join(str(d) for t, d in blocks if t == 'text')
        kor_count = _L_np.count(_blocks_text)
        print(f"  ✅ 블록 기준 한글 {kor_count:,}자")
    else:
        kor_count = _L_np.count(naver_text)
        print(f"  ✅ 변환 완료 ({len(naver_text):,}자 / 한글 {kor_count:,}자)")


    driver = None
    try:
        driver = _get_driver()

        if not _ensure_logged_in(driver):
            # 1차 실패: driver를 닫고 refresher가 naver 프로필(신뢰 기기)로 재로그인
            # naver 프로필은 네이버가 "등록된 기기"로 인식 → CAPTCHA 없이 로그인 가능
            print("  🔄 드라이버 재시작 & 프로필 재로그인 시도...")
            try: driver.quit()
            except Exception: pass
            driver = None
            try:
                from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import refresh_naver_cookies
                refreshed = refresh_naver_cookies(force=True)  # naver 프로필 사용 (driver 닫은 후)
            except Exception as _re:
                print(f"  ⚠️ 쿠키 갱신 오류: {_re}")
                _g_report("writer", _re, module=__name__)
                refreshed = False
            if refreshed:
                driver = _get_driver()
                if _check_blog_login(driver):
                    print("  ✅ 프로필 재로그인 후 로그인 성공")
                    # _check_blog_login이 이미 글쓰기 URL로 이동해둠 — 아래 5초 대기로 이어짐
                else:
                    print("  ❌ 재로그인 후에도 블로그 접근 실패"); return False
            else:
                print("  ❌ 로그인 실패"); return False

        # _ensure_logged_in() / _check_blog_login()이 이미 글쓰기 URL로 이동해둠
        # 수정 모드면 그 위에 logNo 파라미터로 다시 이동 → SmartEditor 가 기존 본문 채워서 열림
        if edit_log_no:
            edit_url = f"https://blog.naver.com/{NV_ID}/postwrite?logNo={edit_log_no}&redirect=Update"
            print(f"  ✏️  수정 모드 진입: logNo={edit_log_no}")
            driver.get(edit_url)
            time.sleep(8)
        print("  📝 글쓰기 페이지 로드 대기...")
        time.sleep(5)

        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        if 'nidlogin' in driver.current_url:
            # JS 인젝션 로그인 사용 금지 (CAPTCHA 유발) — 쿠키 갱신 후 재시도
            print("  ⚠️ 글쓰기 페이지 접근 시 로그인 필요 → 쿠키 갱신 재시도")
            try:
                from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import refresh_naver_cookies
                if refresh_naver_cookies(force=True):
                    driver.get("https://www.naver.com")
                    time.sleep(2)
                    for c in pickle.load(open(COOKIE_FILE, "rb")):
                        c.pop("sameSite", None)
                        try: driver.add_cookie(c)
                        except Exception: pass
                    driver.get(f"https://blog.naver.com/{NV_ID}/postwrite")
                    time.sleep(8)
                    if 'nidlogin' in driver.current_url:
                        print("  ❌ 쿠키 갱신 후에도 로그인 실패")
                        return False
                else:
                    print("  ❌ 쿠키 갱신 실패")
                    return False
            except Exception as _le:
                print(f"  ❌ 쿠키 갱신 오류: {_le}")
                _g_report("writer", _le, module=__name__)
                return False

        # ── 임시저장 팝업 → 취소 ──────────────────────
        print("  🔔 임시저장 팝업 확인...")
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='취소']"))
            )
            driver.execute_script("arguments[0].click()", btn)
            print("  ✅ 취소 클릭 (새 글)")
            time.sleep(2)
        except:
            print("  ℹ️  팝업 없음")

        # ── 도움말 닫기 ────────────────────────────────
        for _ in range(10):
            try:
                btn = driver.find_element(By.CSS_SELECTOR, "button.se-help-panel-close-button")
                driver.execute_script("arguments[0].click()", btn)
                time.sleep(1)
            except:
                break
        time.sleep(2)

        win_w = driver.execute_script("return window.innerWidth")
        win_h = driver.execute_script("return window.innerHeight")
        print(f"  📐 브라우저 내부 크기: {win_w}x{win_h}")

        driver.save_screenshot(str(IMG_EDITOR / "before_input.png"))
        print("  ✅ 에디터 준비 완료")

        # ── 제목 입력 ── (★ 선택자 기반 포커스 — 고정좌표·OS포커스 취약성 근본수정, ERRORS [365])
        #   좌표(283,336) 클릭이 창 위치·툴바 높이 변화로 제목칸을 빗나가면 제목 미입력→발행 실패
        #   (에디터 URL 유지·logNo 없음). SmartEditor ONE 제목 = *최상단* contenteditable.
        #   본문 포커스(_focus_editor_body)와 동일한 CDP 방식으로 안정 포커스.
        print("  ✏️  제목 입력...")
        driver.execute_script("window.scrollTo(0,0)")
        time.sleep(0.5)
        import pyautogui as _pg2
        import pyperclip

        _TITLE_FOCUS_JS = """
        var t = document.querySelector(
          '.se-documentTitle [contenteditable="true"], .se-section-documentTitle [contenteditable="true"],'
          + ' .se-title-text, .se-documentTitle .se-text-paragraph');
        if(!t){
          // 폴백: 최상단(top 최소) contenteditable 단락 = 제목 (본문은 그 아래)
          var all = Array.from(document.querySelectorAll('[contenteditable="true"]'));
          t = all.filter(function(el){var r=el.getBoundingClientRect(); return r.width>100 && r.top>=0;})
                 .sort(function(a,b){return a.getBoundingClientRect().top - b.getBoundingClientRect().top;})[0]||null;
        }
        if(t){ try{t.scrollIntoView({block:'center'});}catch(e){} t.click(); t.focus(); return true; }
        return false;
        """
        _TITLE_READ_JS = """
        var t = document.querySelector(
          '.se-documentTitle [contenteditable="true"], .se-section-documentTitle [contenteditable="true"],'
          + ' .se-title-text, .se-documentTitle .se-text-paragraph');
        if(!t){
          var all = Array.from(document.querySelectorAll('[contenteditable="true"]'));
          t = all.filter(function(el){var r=el.getBoundingClientRect(); return r.width>100 && r.top>=0;})
                 .sort(function(a,b){return a.getBoundingClientRect().top - b.getBoundingClientRect().top;})[0]||null;
        }
        return t ? (t.innerText||t.textContent||'').trim() : '__NOSEL__';
        """

        def _focus_title():
            try:
                return bool(driver.execute_script(_TITLE_FOCUS_JS))
            except Exception:
                return False

        def _paste_title():
            # ★ 순서 (사용자 박제 2026-07-06): 윈도우 활성화 → 제목칸 캐럿(JS focus) → Cmd+V.
            #   기존엔 focus 후 Escape/윈도우전환으로 캐럿이 blur → OS 붙여넣기가 허공에 감(제목 미입력).
            # ★ pyautogui→ActionChains 교체 (ERRORS [402]): pyautogui HID Cmd+V 는 OS-level focus
            #   (SmartEditor ONE 이 자동포커스한 본문)로 전달 → 제목이 본문에 입력되는 버그.
            #   ActionChains CDP Cmd+V 는 JS t.focus() 로 잡은 DOM-focus(제목 칸) 직접 전달.
            _activate_window()
            _focus_title()
            time.sleep(0.2)
            pyperclip.copy(title)
            time.sleep(0.3)
            from selenium.webdriver.common.action_chains import ActionChains as _ACv
            from selenium.webdriver.common.keys import Keys as _Kv
            _ACv(driver).key_down(_Kv.COMMAND if IS_MAC else _Kv.CONTROL).send_keys('v') \
                        .key_up(_Kv.COMMAND if IS_MAC else _Kv.CONTROL).perform()
            time.sleep(0.8)

        _want = (title or "").strip()

        def _title_ok(rb):
            """읽은 값이 *실제 제목*인지. ★ 빈칸/플레이스홀더('제목')/불일치는 실패 (ERRORS [365] 보강).
            검증이 ''(빈칸)만 봐서 플레이스홀더 '제목'을 성공으로 오판하던 구멍을 막는다."""
            rb = (rb or "").strip()
            if not rb or rb == "제목" or rb == "__NOSEL__":
                return False
            _w = _want.replace(" ", ""); _r = rb.replace(" ", "")
            return _r == _w or (len(_w) >= 6 and _w[:6] in _r)

        # 초기 포커스 + 팝업 정리 (이후 _paste_title 가 매번 재포커스)
        if not _focus_title():
            print("  ⚠️ 제목 선택자 미발견 → 좌표 폴백")
            _click(283, 336, "제목 클릭")
        time.sleep(0.4)
        _activate_window()
        _pg2.press('escape')
        time.sleep(0.2)
        if edit_log_no:   # 수정 모드면 기존 제목 비우기
            _focus_title(); _activate_window()
            _pg2.hotkey('command', 'a') if IS_MAC else _pg2.hotkey('ctrl', 'a')
            time.sleep(0.3); _pg2.press('delete'); time.sleep(0.3)

        # ★ 제목 입력 — element.send_keys 직접 전달 (클립보드·OS포커스 무관)
        #   move_to_element().click() → 제목칸 포커스 이동 확실화 (문제1: 본문 입력 방지)
        #   Ctrl+A + Delete → 매 시도 전 초기화 (문제2: 제목 3회 반복 방지)
        from selenium.webdriver.common.by import By as _By2
        from selenium.webdriver.common.action_chains import ActionChains as _AC2
        from selenium.webdriver.common.keys import Keys as _K2

        def _find_title_el():
            for _sel in [
                '.se-documentTitle [contenteditable="true"]',
                '.se-section-documentTitle [contenteditable="true"]',
                '.se-title-text',
                '.se-documentTitle .se-text-paragraph',
            ]:
                try:
                    _els = driver.find_elements(_By2.CSS_SELECTOR, _sel)
                    if _els:
                        return _els[0]
                except Exception:
                    pass
            try:
                _all = driver.find_elements(_By2.CSS_SELECTOR, '[contenteditable="true"]')
                _vis = [e for e in _all if e.is_displayed() and e.size.get('width', 0) > 100]
                return _vis[0] if _vis else None
            except Exception:
                return None

        _fin = ""
        _title_el = _find_title_el()
        for _attempt in range(3):
            try:
                if _title_el:
                    # ① move_to_element().click() — 제목칸으로 커서 이동 (본문 포커스 해제)
                    _AC2(driver).move_to_element(_title_el).click().perform()
                    time.sleep(0.3)
                    # ② 기존 내용 전체 선택 후 삭제 — 재시도 시 제목 누적 방지
                    _title_el.send_keys((_K2.COMMAND if IS_MAC else _K2.CONTROL) + 'a')
                    time.sleep(0.1)
                    _title_el.send_keys(_K2.DELETE)
                    time.sleep(0.1)
                    # ③ 제목 직접 입력 — DOM element 직접 전달, OS포커스 무관
                    _title_el.send_keys(_want)
                    time.sleep(0.5)
                else:
                    _paste_title()   # element 미발견 시 기존 클립보드 폴백
            except Exception as _te:
                print(f"  ⚠️ 제목 입력 시도 {_attempt + 1} 실패: {_te}")
                try:
                    _paste_title()   # 예외 시 클립보드 폴백
                except Exception:
                    pass
            try:
                _fin = driver.execute_script(_TITLE_READ_JS) or ""
            except Exception:
                _fin = ""
            if _title_ok(_fin):
                break
            print(f"  ⚠️ 제목 미확인(읽음='{_fin[:20]}') → 재시도 {_attempt + 1}/3")

        if _title_ok(_fin):
            print(f"  ✅ 제목 입력 완료 — {_fin[:30]}")
        else:
            print(f"  ❌ 제목 입력 실패 — 읽음='{_fin[:30]}' (발행 블로커)")
        time.sleep(1)

        # ── 본문 입력 (텍스트 + 이미지 블록 순서대로) ──
        print("  ✏️  본문 입력...")
        import pyautogui as _pg
        from selenium.webdriver.common.action_chains import ActionChains as _AC
        from selenium.webdriver.common.keys import Keys as _Keys
        from selenium.webdriver.common.by import By as _By

        def _focus_editor_body():
            """SmartEditor body contenteditable에 Selenium element.click() (CDP 기반).
            ★ height > 100 필터 제거 (ERRORS [183] 원인) — 빈 에디터는 height ≈ 24px.
            OS 키보드 포커스 불필요 — Finder 다이얼로그 후에도 안정 작동."""
            focused = False
            # 1) SmartEditor One 텍스트 단락 contenteditable 셀렉터 시도
            for sel in [
                'p.se-text-paragraph[contenteditable="true"]',
                '[contenteditable="true"].se-text-paragraph',
                '.se-module-text [contenteditable="true"]',
                '.se-section-text [contenteditable="true"]',
            ]:
                try:
                    els = driver.find_elements(_By.CSS_SELECTOR, sel)
                    if els:
                        # 가장 아래쪽 보이는 단락에 클릭 (커서 끝)
                        for el in reversed(els):
                            try:
                                rect = driver.execute_script(
                                    "var r=arguments[0].getBoundingClientRect();"
                                    "return {top:r.top,w:r.width};", el)
                                if rect['top'] > 50 and rect['w'] > 100:
                                    el.click()
                                    focused = True
                                    break
                            except Exception:
                                pass
                    if focused:
                        break
                except Exception:
                    pass
            if not focused:
                # Fallback: width 기준으로 가장 큰 contenteditable (height 무시)
                driver.execute_script("""
                    var all = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                    var body = all.filter(function(el){
                        var r=el.getBoundingClientRect();
                        return r.top > 50 && r.width > 100;
                    }).sort(function(a,b){
                        return b.getBoundingClientRect().width - a.getBoundingClientRect().width;
                    })[0] || null;
                    if(body){
                        body.click();
                        body.focus();
                    }
                """)
            time.sleep(0.3)

        def _enter():
            """엔터(단락 분리) — Selenium ActionChains CDP 키보드 이벤트 (OS focus 불필요)"""
            _AC(driver).send_keys(_Keys.RETURN).perform()
            time.sleep(0.15)

        def _bold_toggle():
            """굵게 토글 — Selenium ActionChains Cmd+B (CDP 기반)"""
            _AC(driver).key_down(_Keys.COMMAND).send_keys('b').key_up(_Keys.COMMAND).perform()
            time.sleep(0.15)

        _focus_editor_body()
        time.sleep(0.5)
        if edit_log_no:
            # 수정 모드 — 기존 본문 전체 선택 → 삭제
            _AC(driver).key_down(_Keys.COMMAND).send_keys('a').key_up(_Keys.COMMAND).perform()
            time.sleep(0.3)
            _AC(driver).send_keys(_Keys.DELETE).perform()
            time.sleep(0.5)

        # blocks가 있으면 순서대로, 없으면 텍스트 전체
        DIVIDER = "　" * 15 + "─" * 20 + "　" * 15  # 구분선 (가운데 정렬)

        def _paste_text(t):
            """텍스트 삽입 — pyperclip(OS 클립보드) + Selenium ActionChains Cmd+V (CDP 기반).
            Chrome find bar 열림 여부·OS 포커스 무관 (ERRORS [183])."""
            if not t.strip():
                return
            import pyperclip
            pyperclip.copy(t.strip())
            time.sleep(0.15)
            _AC(driver).key_down(_Keys.COMMAND).send_keys('v').key_up(_Keys.COMMAND).perform()
            time.sleep(0.25)

        def input_text_block(text):
            """
            텍스트 블록 입력:
            - 최대 2문장씩 이어서 쓴 후 빈 줄 1개 (Enter × 2)
            - 단락과 단락 사이 빈 줄 2개 (Enter × 3)
            - 구분선 없음
            """
            if not text.strip():
                return

            import re as _re_tb

            # 1단계: \n\n 기준 단락 분리
            raw_paras = [p.strip() for p in text.split('\n\n') if p.strip()]

            for pi, para in enumerate(raw_paras):
                # 단락 내 줄들을 한 줄로 합치기
                flat = ' '.join(l.strip() for l in para.split('\n') if l.strip())
                if not flat:
                    continue

                # 번호 항목(①②③ / 1. 2. 3. / 첫째 둘째)은 문장 분리·묶음 없이 그대로 1줄 입력
                if _re_tb.match(r'^[①②③④⑤⑥⑦⑧⑨]|^\d+\.\s|^(첫째|둘째|셋째|넷째|다섯째)[,，]?\s', flat):
                    _paste_text(flat)
                    _enter()
                    if pi < len(raw_paras) - 1:
                        _enter()
                    continue

                # 문장 분리 (한국어/영어 마침표·느낌표·물음표)
                sents = _re_tb.split(r'(?<=[.!?。！？])\s+', flat)
                sents = [s.strip() for s in sents if s.strip()]
                if not sents:
                    sents = [flat]

                # 2문장씩 그룹지어 입력
                groups = [' '.join(sents[i:i+2]) for i in range(0, len(sents), 2)]
                for gi, group in enumerate(groups):
                    _paste_text(group)
                    _enter()
                    if gi < len(groups) - 1:
                        _enter()

                # ★ 2026-05-15 제9조 여백 규정: 글↔글 1행 여백 (Enter 1번만)
                if pi < len(raw_paras) - 1:
                    _enter()

            # ★ 간격 수정 2026-05-27: 블록 끝 trailing Enter 제거 — spacer 블록이 간격 담당
            pass

        if blocks:
            heading_cnt = sum(1 for b in blocks if b[0] == 'heading')
            print(f"  📦 {len(blocks)}개 블록 입력 (이미지 포함 소제목 반영)...")

            def _insert_image_with_gap(img_path):
                """이미지 업로드 후 행 처리.
                - 소제목 이미지(heading_*.png, economic_h2_*.png): Enter 없음 (다음 블록이 바로 이어짐)
                - 일반 이미지: Enter 1번 (이미지 다음 줄, 빈 줄 없음)
                ★ _upload_image() 는 사진 아이콘 클릭에 pyautogui 사용 → Chrome이 앞에 있어야 함.
                Finder 다이얼로그 후 Chrome 복귀 → Selenium CDP click으로 editor 포커스 재설정.
                """
                # 사진 아이콘 클릭 전 Chrome을 front로
                _activate_window()
                time.sleep(0.8)
                _upload_image(img_path, driver=driver)
                time.sleep(0.8)
                # ★ 이미지 업로드 후 열리는 라이브러리 패널 닫기 (우측 상단 X 버튼 — JS 위치 탐색)
                _lib_result = driver.execute_script("""
                    var btns = Array.from(document.querySelectorAll('button'));
                    // 우측 상단 소형 버튼(닫기 X) 탐색: x>700, y<90, 너비<35
                    var closeBtn = btns.find(function(btn) {
                        var r = btn.getBoundingClientRect();
                        return r.right > 700 && r.top < 90 && r.top > 30 && r.width < 35;
                    });
                    if (closeBtn) { closeBtn.click(); return 'closed'; }
                    return 'no-panel';
                """)
                if _lib_result == 'closed':
                    time.sleep(0.3)
                # ★ Finder 다이얼로그 후 에디터 포커스 복구 (Selenium CDP click — OS focus 불필요)
                _focus_editor_body()
                fname = str(Path(img_path).name)
                is_heading_img = 'heading_' in fname or 'economic_h2_' in fname
                if not is_heading_img:
                    _enter()  # ActionChains Enter (CDP 기반)

            # 썸네일(첫 번째 이미지) 먼저 입력
            if blocks[0][0] == 'image':
                print(f"  [썸네일] {str(blocks[0][1])[:50]}")
                _insert_image_with_gap(blocks[0][1])
                remaining = blocks[1:]
            else:
                remaining = blocks

            # 썸네일만 있고 본문 블록이 없으면 텍스트 폴백
            if not remaining:
                print("  ⚠️ 본문 블록 없음 → 텍스트 붙여넣기 모드로 전환")
                _paste(naver_text)

            # 나머지 블록: divider 블록에서 구분선 삽입
            for bi, (btype, bdata) in enumerate(remaining):
                print(f"  [{bi+1}/{len(remaining)}] {btype}: {str(bdata)[:50]}")
                if btype == 'divider':
                    pass  # 구분선 제거 — 소제목 이미지로 대체됨
                elif btype == 'heading2':
                    # h2 섹션 제목: 구분선 + 굵게 + ▶ 기호 (앞 간격은 spacer 블록이 처리)
                    _paste_text('─' * 25)
                    _enter()
                    _bold_toggle()   # 굵게 ON
                    _paste_text('▶ ' + str(bdata))
                    _bold_toggle()   # 굵게 OFF
                    _enter()
                elif btype == 'heading':
                    # h3 소소제목: 굵게 + ◆ 기호 (앞 간격은 spacer 블록이 처리)
                    _bold_toggle()   # 굵게 ON
                    _paste_text('◆ ' + str(bdata))
                    _bold_toggle()   # 굵게 OFF
                    _enter()
                elif btype == 'spacer':
                    # ★ 간격 통일 2026-05-27: trailing Enter 제거로 spacer_1/2 구분 불필요 — 항상 1칸
                    _enter()
                elif btype == 'text':
                    import re as _re_blk
                    _bdata_s = str(bdata).strip()
                    # ★ h2/h3를 text 블록으로 받은 경우 heading 처리 (assemble_blocks → text 타입)
                    _h2m = _re_blk.match(r'\s*<h2[^>]*>([\s\S]*?)</h2>\s*$', _bdata_s)
                    _h3m = _re_blk.match(r'\s*<h3[^>]*>([\s\S]*?)</h3>\s*$', _bdata_s)
                    if _h2m:
                        _htxt = _re_blk.sub(r'<[^>]+>', '', _h2m.group(1)).strip()
                        if _htxt:
                            _paste_text('─' * 25)
                            _enter()
                            _bold_toggle()
                            _paste_text('▶ ' + _htxt)
                            _bold_toggle()
                            _enter()
                    elif _h3m:
                        _htxt = _re_blk.sub(r'<[^>]+>', '', _h3m.group(1)).strip()
                        if _htxt:
                            _bold_toggle()
                            _paste_text('◆ ' + _htxt)
                            _bold_toggle()
                            _enter()
                    else:
                        driver.save_screenshot(str(IMG_EDITOR / f"text_{bi:02d}_before.png"))
                        _plain = html_to_naver_text(_bdata_s)
                        if _plain.strip():
                            input_text_block(_plain)
                elif btype == 'html':
                    # ★ ERRORS [171] 2026-05-27: html 블록 핸들러 누락 → 묵묵히 스킵 → 이미지 연속 배치
                    _plain = html_to_naver_text(str(bdata))
                    if _plain.strip():
                        input_text_block(_plain)
                elif btype == 'image':
                    _insert_image_with_gap(bdata)
                else:
                    # ★ 미지 블록 타입 무음 유실 방지 (ADR 012 — 2026-07-02, ERRORS [171] 계열)
                    #   새 블록 타입 추가 시 양 발행자 동시 갱신 규정 위반을 즉시 가시화.
                    print(f"  ⚠️ 미지 블록 타입 '{btype}' — 텍스트 폴백 렌더 (양 발행자 핸들러 추가 필요)")
                    try:
                        from JARVIS07_GUARDIAN.error_collector import report as _g_rep
                        _g_rep("publish", RuntimeError(f"naver 미지 블록 타입: {btype}"),
                               module=__name__, func_name="post_to_naver")
                    except Exception:
                        pass
                    _plain = html_to_naver_text(str(bdata)) if bdata else ""
                    if _plain.strip():
                        input_text_block(_plain)
        else:
            _paste(naver_text)

        # ── 연관 글 ──────────────────────────────────
        _posts = related_posts if related_posts is not None else _fetch_recent_naver_posts(1)
        if _posts:
            _enter()
            _enter()
            _paste_text('─' * 25)
            _enter()
            _bold_toggle()
            _paste_text('[함께 읽으면 좋은 글]')
            _bold_toggle()
            _enter()
            for rp in _posts:
                _paste_text(f">> {rp['title']}")
                _enter()
                _paste_text(rp['url'])
                _enter()
            print("  ✅ 연관 글 삽입 완료")

        print("  ✅ 본문 입력 완료")
        time.sleep(2)

        driver.save_screenshot(str(IMG_EDITOR / "after_input.png"))

        # ── 발행 버튼 (JS) ────────────────────────────
        print("  🔍 발행 버튼 클릭...")
        driver.execute_script("window.scrollTo(0,0)")
        time.sleep(0.5)
        clicked = driver.execute_script("""
            var btns = document.querySelectorAll('button');
            for (var b of btns) {
                if (b.innerText.trim() === '발행' && b.offsetWidth > 0) {
                    b.click();
                    return '발행버튼 클릭: ' + b.className;
                }
            }
            return '발행버튼 없음';
        """)
        print(f"  ✅ {clicked}")
        time.sleep(3)

        driver.save_screenshot(str(IMG_PUBLISH / "popup.png"))
        print("  📸 발행 팝업 스크린샷")

        # ── 카테고리 (★ 2026-06-01 v6 — React 커스텀 드롭다운 — native <select> 없음)
        # 구조: button[aria-label="카테고리 목록 버튼"] 클릭 → div[role="menu"] 내 label 클릭
        # 진단: ERRORS [214] <select> 방식 → 완전 실패 (select 요소 자체 없음) → v6 교체
        print(f"  📂 카테고리 선택: {category}")
        time.sleep(4.0)  # 발행 팝업 + React 렌더링 안정화

        from selenium.webdriver.support.ui import WebDriverWait
        category_clicked = False

        # 1단계: 카테고리 드롭다운 버튼 클릭 (열기)
        _open_r = driver.execute_script("""
            var btn = document.querySelector('button[aria-label="카테고리 목록 버튼"]') ||
                      document.querySelector('[data-click-area="tpb*i.category"]');
            if (!btn) return 'btn_not_found';
            btn.click();
            return 'opened:' + btn.getAttribute('aria-expanded');
        """)
        print(f"  🔍 드롭다운 열기: {_open_r}")

        if _open_r and 'btn_not_found' not in str(_open_r):
            # aria-expanded=true 대기 (최대 5초)
            try:
                WebDriverWait(driver, 5).until(lambda d: d.execute_script(
                    'var b=document.querySelector(\'button[aria-label="카테고리 목록 버튼"]\');'
                    'return b && b.getAttribute("aria-expanded")==="true";'
                ))
            except Exception:
                time.sleep(1.5)

            # 2단계: [role="menu"] 내 label[role="button"] 텍스트 매칭 클릭
            _sel_r = driver.execute_script(f"""
                var target = '{category}';
                var menu = document.querySelector('[role="menu"]');
                if (!menu) return 'menu_not_found';
                var labels = Array.from(menu.querySelectorAll('label[role="button"]'));
                var match = labels.find(l => l.textContent.trim() === target) ||
                            labels.find(l => l.textContent.includes(target));
                if (match) {{ match.click(); return 'selected:' + match.textContent.trim(); }}
                return 'no_match:' + labels.map(l => l.textContent.trim()).join('|');
            """)
            print(f"  🔍 옵션 선택: {str(_sel_r)[:100]}")
            if _sel_r and str(_sel_r).startswith('selected:'):
                category_clicked = True
                print(f"  ✅ 카테고리 선택: {category}")
                time.sleep(0.5)

        if not category_clicked:
            print(f"  ❌ '{category}' 카테고리 선택 실패")
            _g_report("writer", RuntimeError(f"네이버 카테고리 선택 실패: {category}"),
                      module=__name__, func_name="post_to_naver",
                      context={"category": category, "open_result": str(_open_r)})
            print(f"  ⚠️ 카테고리 미설정으로 발행 진행")

        driver.save_screenshot(str(IMG_PUBLISH / "category_selected.png"))
        print(f"  ✅ 카테고리 완료 — '{category}' 선택")

        # ── 태그 ──────────────────────────────────────
        print("  🏷️  태그 입력...")

        if tags is None:
            # 본문 전체 텍스트 추출
            body_text = ' '.join(str(bdata) for btype, bdata in (blocks or []) if btype == 'text')
            print("  🔍 태그 생성 중 (제목 2개 + 본문 2개)...")
            tags = _generate_smart_tags(title, body_text)

        # ★ 태그 입력 — Selenium element.click() + ActionChains Cmd+V (CDP 기반, OS focus 불필요)
        import pyperclip
        _tag_input = None
        # 태그 입력란 찾기: placeholder 텍스트 또는 class로 탐색
        for _sel in [
            'input[placeholder*="태그"]',
            'input[placeholder*="tag" i]',
            'input[id*="tag" i]',
            '.se-publish-tag input',
            '.se-tag-input input',
        ]:
            try:
                _els = driver.find_elements(_By.CSS_SELECTOR, _sel)
                _vis = [e for e in _els if e.is_displayed()]
                if _vis:
                    _tag_input = _vis[0]
                    print(f"  ✅ 태그 입력란 발견: {_sel}")
                    break
            except Exception:
                pass

        if _tag_input:
            for tag in tags:
                try:
                    pyperclip.copy(tag)
                    time.sleep(0.15)
                    _tag_input.click()
                    time.sleep(0.2)
                    _AC(driver).key_down(_Keys.COMMAND).send_keys('v').key_up(_Keys.COMMAND).perform()
                    time.sleep(0.4)
                    _AC(driver).send_keys(_Keys.RETURN).perform()
                    time.sleep(0.4)
                except Exception as _te:
                    print(f"  ⚠️ 태그 입력 실패: {tag} — {_te}")
        else:
            # Fallback: 좌표 기반 (태그 입력란을 못 찾은 경우)
            print("  ⚠️ 태그 입력란 미발견 — 좌표 fallback")
            _activate_window()
            time.sleep(0.5)
            _pg.click(1150, 500)
            time.sleep(1.0)
            for tag in tags:
                pyperclip.copy(tag)
                time.sleep(0.3)
                _pg.hotkey('command', 'v')
                time.sleep(0.5)
                _pg.press('return')
                time.sleep(0.4)

        print(f"  ✅ 태그 완료: {tags}")

        # ── 발행 전 팝업 해제 (사진 첨부 방식 팝업 등) ──────────────
        _dismiss_naver_popup(driver)
        time.sleep(0.5)

        # ── 최종 발행 ─────────────────────────────────
        print("  ✅ 최종 발행 클릭...")
        driver.save_screenshot(str(IMG_PUBLISH / "before_publish.png"))
        # ★ ActionChains 신뢰 이벤트 클릭 (ERRORS [293]) — OS 물리 클릭은 주간/in-daemon
        #   실행에서 버튼 빗맞음 → 팝업만 닫힘. 물리 클릭은 헬퍼 내부 폴백 전용.
        if not _click_publish_btn(driver, "최종발행"):
            print("  ⚠️ 최종발행 버튼 미발견 — 검증 단계에서 재시도")
        # ★ ERRORS [273] — 4초 부족 (네이버 리다이렉트 > 4초 소요 사례). 8초로 확대.
        time.sleep(8)

        driver.save_screenshot(str(IMG_RESULT / "done.png"))

        # ★ 발행 성공 검증 — 에디터 이탈 여부 확인
        if not _verify_naver_published(driver):
            # 팝업이 남아있어 발행이 안 된 경우 → 재발행 시도
            print("  ⚠️ [Naver] 발행 후 에디터 상태 유지 → 재발행 시도")
            # ★ ESC 금지 (발행 팝업 닫힘 방지) — dim 레이어만 JS로 제거
            driver.execute_script("""
                document.querySelectorAll('.se-popup-dim:not(.se-popup-dim-transparent)').forEach(function(d) { d.remove(); });
            """)
            time.sleep(1.0)

            # ★ 재발행 — ActionChains 신뢰 이벤트 (ERRORS [293])
            if not _click_publish_btn(driver, "재발행"):
                # 발행 팝업이 닫힌 경우 — 툴바 '발행' 버튼으로 재오픈 후 카테고리 재선택
                print("  ⚠️ 재발행 버튼 미발견 → 발행 팝업 재오픈")
                driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var b of btns) {
                        if (b.innerText.trim() === '발행' && b.offsetWidth > 0) { b.click(); return; }
                    }
                """)
                time.sleep(4.0)
                # 카테고리 재선택
                driver.execute_script("""
                    var btn = document.querySelector('button[aria-label="카테고리 목록 버튼"]') ||
                              document.querySelector('[data-click-area="tpb*i.category"]');
                    if (btn) btn.click();
                """)
                time.sleep(1.5)
                driver.execute_script(f"""
                    var target = '{category}';
                    var menu = document.querySelector('[role="menu"]');
                    if (menu) {{
                        var labels = Array.from(menu.querySelectorAll('label[role="button"]'));
                        var match = labels.find(function(l) {{ return l.textContent.trim() === target; }}) ||
                                    labels.find(function(l) {{ return l.textContent.includes(target); }});
                        if (match) match.click();
                    }}
                """)
                time.sleep(0.5)
                # ★ 재오픈 후 최종 발행 — ActionChains (ERRORS [293]).
                #   구 좌표 폴백 (1452, 604) 는 viewport(1440px) 밖 — 제거.
                if not _click_publish_btn(driver, "재발행2"):
                    print("  ⚠️ 재오픈 후에도 발행 버튼 미발견")

            # ★ ERRORS [274] 재발 — 재발행 경로도 8초 대기 필수 (SPA 전환 지연)
            time.sleep(8)
            driver.save_screenshot(str(IMG_RESULT / "done_retry.png"))

            if not _verify_naver_published(driver):
                print("  ❌ [Naver] 재시도 후에도 발행 미완료 — 발행 실패 처리")
                return False

        print("  🎉 네이버 블로그 포스팅 완료!")

        # ── 발행 URL 캡처 (RSS 기반) ──────────────────────────
        global _last_post_url
        try:
            time.sleep(3)  # RSS 갱신 대기
            posts = _fetch_recent_naver_posts(1)
            _last_post_url = posts[0]["url"] if posts else ""
            if _last_post_url:
                print(f"  📎 발행 URL: {_last_post_url}")
        except Exception as _e:
            _last_post_url = ""
            print(f"  ⚠️ URL 캡처 실패: {_e}")
            _g_report("writer", _e, module=__name__)

        return True

    except Exception as e:
        print(f"  ❌ 오류: {e}")
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        if driver:
            driver.save_screenshot(str(IMG_EDITOR / "error.png"))
        return False
    finally:
        if driver:
            time.sleep(2)
            driver.quit()


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — 직접 실행 시 Selenium 발행 차단
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    from JARVIS00_INFRA.watchdog import guard_main
    with guard_main("네이버 발행 테스트", deadline_sec=1800):
        ok = post_to_naver(
            "[마켓시그널] 테스트 테마 완전 정복 리포트",
            "<h1>테스트</h1><p>pyautogui 좌표 기반 테스트입니다.</p>",
        )
    print("결과:", "✅ 성공" if ok else "❌ 실패")
