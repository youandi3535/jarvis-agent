"""
tistory_poster.py v5
━━━━━━━━━━━━━━━━━━━━━━━━━━━
실측 좌표:
  첨부버튼  : DIV#mceu_0
  제목      : #post-title-inp
  완료버튼  : (1294, 597)
  파일업로드: Cmd+Shift+G → 전체경로 붙여넣기 (CGEventPost HID 레벨)
"""

import os, time, sys, subprocess
from pathlib import Path
from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

# ── ADR 008 Phase 2 (★ 사용자 박제 2026-05-17) — 경로 anchor ──
# 본 모듈이 JARVIS02_WRITER → JARVIS08_PUBLISH/platforms 로 이관됨에 따라
# 루트 .env 와 *JARVIS02_WRITER 의* chrome_profile/screenshots 등 자원 경로를 정확히 가리키도록 anchor 명시.
_PROJECT_ROOT    = Path(__file__).resolve().parent.parent.parent  # JARVIS08_PUBLISH/platforms → root
_LEGACY_BASE_DIR = _PROJECT_ROOT / "JARVIS02_WRITER"               # 옛 위치 anchor
_ENV_FILE        = _PROJECT_ROOT / '.env'                          # 루트 .env
load_dotenv(dotenv_path=_ENV_FILE, override=True)

def _get_cookie() -> str:
    """항상 최신 TS_COOKIE 반환 — 갱신 후 재로드 보장."""
    load_dotenv(dotenv_path=_ENV_FILE, override=True)
    return os.getenv("TS_COOKIE", "").strip('"').strip("'")

_ts_url = os.getenv("TS_URL", "")
TS_BLOG = _ts_url.replace("https://","").replace("http://","").split(".")[0] if _ts_url else ""

# 발행 직후 URL 캡처 — 외부에서 tistory_poster._last_post_url 로 읽음
_last_post_url: str = ""


def _fetch_recent_tistory_posts(n: int = 1) -> list:
    """티스토리 블로그 RSS에서 최근 n개 글 제목+URL 반환"""
    import xml.etree.ElementTree as ET
    import requests as _req
    rss_url = f"https://{TS_BLOG}.tistory.com/rss"
    try:
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

BASE_DIR      = _LEGACY_BASE_DIR                                  # 옛 위치 anchor (chrome_profile 등)
JARVIS06_BASE = _PROJECT_ROOT / "JARVIS06_IMAGE"                  # 이미지 단일 진입점 (CLAUDE.md 규정)
SS_DIR        = JARVIS06_BASE / "output" / "screenshots" / "tistory"
SS_DIR.mkdir(parents=True, exist_ok=True)

import pyautogui as _pg
import pyperclip
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys




def _generate_smart_tags(title: str, body_text: str) -> list:
    """제목에서 2개 + 본문에서 2개, 총 4개 태그를 Claude API로 생성.
    Returns: 4개짜리 태그 리스트 (항상 보장)
    """
    theme_name = title.split()[0] if title else '주식'
    try:
        from JARVIS02_WRITER import length_manager as _LM_ts
    except ImportError:
        import length_manager as _LM_ts
    snippet = body_text[:_LM_ts.BODY_SNIPPET_LEN] if body_text else ''
    try:
        from shared.llm import invoke_text as _inv_cli
        _raw = _inv_cli(
            "writer",
            f"다음 제목과 본문을 보고 블로그 태그 4개를 쉼표로 구분해서 출력하세요.\n"
            f"- 제목에서 2개 (공백없이 붙여쓰기)\n"
            f"- 본문에서 2개 (공백없이 붙여쓰기)\n"
            f"태그만 출력하고 다른 말은 하지 마세요.\n\n"
            f"제목: {title}\n"
            f"본문: {snippet}",
            timeout=60
        ) or ""
        parts = [p.strip() for p in _raw.strip().split(',') if p.strip()]
        fallbacks = [theme_name, f'{theme_name}주식', f'{theme_name}투자', f'{theme_name}테마주']
        while len(parts) < 4:
            parts.append(fallbacks[len(parts) % len(fallbacks)])
        return list(dict.fromkeys(parts))[:4]
    except Exception:
        return [theme_name, f'{theme_name}주식', f'{theme_name}테마주', f'{theme_name}투자']

def _split_into_paragraphs(text: str) -> list:
    """누적 length_manager.PARAGRAPH_SPLIT_KOREAN 초과 후 문장 끝에서 단락 분리."""
    import re
    try:
        from JARVIS02_WRITER import length_manager as _LM
    except ImportError:
        import length_manager as _LM
    threshold = _LM.PARAGRAPH_SPLIT_KOREAN
    # 문장 단위로 분리 (마침표/느낌표/물음표 뒤)
    sentences = re.split(r'(?<!\d)\.\s+|[!?]\s+', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    paragraphs = []
    current = ''
    for sent in sentences:
        current += sent + ' '
        if len(current.strip()) >= threshold:
            paragraphs.append(current.strip())
            current = ''
    if current.strip():
        paragraphs.append(current.strip())
    return paragraphs if paragraphs else [text.strip()]

def _s(sec=1.0): time.sleep(sec)
def _ss(name):   _pg.screenshot(str(SS_DIR / f"{name}.png"))

def _capslock_reset():
    for _ in range(3):
        _pg.press('capslock'); _s(0.1)

def _cgevent_paste():
    """HID 레벨 Cmd+V — 한국어 IME·포커스 문제 완전 우회 (CGEventPost kCGHIDEventTap)"""
    try:
        from Quartz import (CGEventCreateKeyboardEvent, CGEventPost,
                            CGEventSetFlags, kCGHIDEventTap, kCGEventFlagMaskCommand)
        V_KEY = 9  # 'v' keycode
        for down in (True, False):
            ev = CGEventCreateKeyboardEvent(None, V_KEY, down)
            CGEventSetFlags(ev, kCGEventFlagMaskCommand if down else 0)
            CGEventPost(kCGHIDEventTap, ev)
            time.sleep(0.05)
    except Exception:
        # fallback: osascript
        subprocess.run(['osascript', '-e',
            'tell application "System Events" to keystroke "v" using {command down}'],
            capture_output=True)

def _chrome_focus():
    subprocess.run(['osascript', '-e',
        'tell application "Google Chrome" to activate'])
    _s(0.5)


def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--start-maximized")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=opts)
    driver.implicitly_wait(3)
    return driver


def _login(driver) -> bool:
    cookie = _get_cookie()  # 항상 최신 쿠키 읽기
    driver.get("https://www.tistory.com")
    _s(2)
    driver.delete_all_cookies()
    driver.add_cookie({
        "name": "TSSESSION", "value": cookie,
        "domain": ".tistory.com", "path": "/",
    })
    driver.refresh()
    _s(2)
    _cur_url = driver.current_url or ""
    if "login" not in _cur_url:
        # ★ 강제 이동 (검증 retry + 멈춤 차단 + SOS) — 사용자 박제 2026-05-14
        # tistory_cookie_refresher.force_my_blog() 위임 — 단일 진입점.
        try:
            from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import force_my_blog as _force
            _force(driver)
        except Exception as _e:
            print(f"  ⚠️ force_my_blog 위임 실패 (무시): {_e}")
            # fallback — 직접 navigate
            if TS_BLOG and f"{TS_BLOG}.tistory.com" not in _cur_url:
                try:
                    driver.get(f"https://{TS_BLOG}.tistory.com")
                    _s(2)
                except Exception:
                    pass
        print("  ✅ 쿠키 로그인 성공")
        return True
    print("  ❌ TSSESSION 만료 — .env의 TS_COOKIE 갱신 필요")
    return False


def _focus_editor(driver):
    # 이미지 업로드 후 Finder 다이얼로그 시퀀스로 Chrome이 frontmost를 잃을 수 있음
    # → osascript로 Chrome 먼저 활성화(OS 레벨) → Selenium click으로 TinyMCE 포커스 복구
    # (순서: Chrome 활성화 → iframe body click → default_content 복귀)
    _chrome_focus()   # osascript: Chrome 창을 OS 레벨 frontmost로 가져옴
    _s(0.3)           # Chrome 활성화 완료 대기
    try:
        frame = driver.find_element(By.ID, 'editor-tistory_ifr')
        driver.switch_to.frame(frame)
        driver.find_element(By.TAG_NAME, 'body').click()  # TinyMCE 내부 포커스 복구
        driver.switch_to.default_content()
        _s(0.5)
    except Exception as e:
        print(f"  ⚠️ 에디터 포커스 실패: {e}")
        _g_report("writer", e, module=__name__)
        driver.switch_to.default_content()
        _s(0.3)


def _upload_image(img_path: str, driver=None, after_newline: bool = True):
    img_path = str(Path(img_path).resolve())
    filename = Path(img_path).name
    print(f"    🖼️  이미지: {filename}")

    _chrome_focus()

    # 1) 첨부 버튼 클릭 — WebDriverWait으로 TinyMCE 초기화 완료 보장
    # ★ ERRORS [136] — timeout 15→45초 + visibility_of_element_located (사용자 박제 2026-05-17)
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    try:
        attach = WebDriverWait(driver, 45).until(
            EC.visibility_of_element_located((By.ID, 'mceu_0'))
        )
        # 추가: clickable 도 보장
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.ID, 'mceu_0')))
    except Exception:
        # mceu_0 없으면 다른 첨부 버튼 후보 탐색
        attach = driver.find_element(By.ID, 'mceu_0')
    ActionChains(driver).click(attach).perform()
    _s(1.2)

    # 2) 사진 메뉴 클릭 전 — input[type="file"]에 JS 인터셉터 설치
    #    TinyMCE가 input.click()을 호출하는 순간을 가로채 native 다이얼로그 억제
    #    (스크린 잠금/슬립 상태에서도 동작하는 핵심 패치)
    driver.execute_script("""
        window.__tistoryFileInputReady = null;
        var _origClick = HTMLInputElement.prototype.click;
        HTMLInputElement.prototype.click = function() {
            if (this.type === 'file') {
                window.__tistoryFileInputReady = this;
                /* native 다이얼로그 열지 않음 — send_keys로 직접 경로 주입 */
                return;
            }
            return _origClick.call(this);
        };
    """)

    # 2) 사진 메뉴 클릭 — TinyMCE 4(.mce-menu-item) / 5(.tox-collection__item) 모두 지원
    clicked_menu = driver.execute_script("""
        var selectors = ['.mce-menu-item', '.tox-collection__item', '[role="menuitem"]'];
        for (var sel of selectors) {
            var items = document.querySelectorAll(sel);
            for (var i of items) {
                var txt = (i.innerText || i.textContent || '').trim();
                if (txt.includes('사진') || txt.includes('이미지') || txt.includes('Image')) {
                    i.click();
                    return '클릭: ' + sel + ' / ' + txt;
                }
            }
        }
        return null;
    """)
    if clicked_menu:
        print(f"    ✅ 사진 메뉴: {clicked_menu}")
    else:
        print("    ⚠️ 사진 메뉴 못찾음")
    _s(1.0)

    # 3) input[type="file"]에 직접 경로 주입 (screen lock 무관, native 다이얼로그 불필요)
    file_sent = False
    from selenium.webdriver.support.ui import WebDriverWait
    try:
        # 인터셉터로 잡힌 file input 우선 시도
        file_input = driver.execute_script("return window.__tistoryFileInputReady;")
        if not file_input:
            # fallback: DOM 전체에서 file input 탐색
            file_input = driver.execute_script(
                "return document.querySelector('input[type=\"file\"]');"
            )
        if file_input:
            driver.execute_script(
                "arguments[0].style.cssText='display:block!important;"
                "visibility:visible!important;opacity:1!important;"
                "width:1px;height:1px;position:fixed;top:0;left:0;';",
                file_input
            )
            file_input.send_keys(img_path)
            _s(4)  # 업로드 완료 대기
            file_sent = True
            print(f"    ✅ 파일 직접 주입 성공")
    except Exception as e:
        print(f"    ⚠️ 파일 주입 실패: {e}")
        _g_report("writer", e, module=__name__)

    if not file_sent:
        # 4) Fallback: 기존 Cmd+Shift+G (스크린 활성 상태 전용)
        print("    ⚠️ Cmd+Shift+G fallback 시도")
        pyperclip.copy(img_path)
        _s(0.3)
        _chrome_focus()
        _s(0.3)
        subprocess.run(['osascript', '-e',
            'tell application "System Events" to keystroke "g" using {command down, shift down}'],
            capture_output=True)
        _s(0.8)
        _cgevent_paste()
        _s(0.5)
        subprocess.run(['osascript', '-e',
            'tell application "System Events" to key code 36'],
            capture_output=True)
        _s(1.5)
        subprocess.run(['osascript', '-e',
            'tell application "System Events" to key code 36'],
            capture_output=True)
        _s(5)

    # 5) iframe 안에서 삽입된 이미지를 Selenium으로 직접 클릭 → 아래 화살표 5번 → Enter
    try:
        frame = driver.find_element(By.ID, 'editor-tistory_ifr')
        driver.switch_to.frame(frame)
        _s(1)
        # 마지막으로 삽입된 img 태그를 Selenium으로 직접 클릭 (좌표 의존 없음)
        imgs = driver.find_elements(By.TAG_NAME, 'img')
        if imgs:
            print(f"    ✅ 이미지 삽입 확인 ({len(imgs)}개)")
            # JS 클릭 — 뷰포트 위치 무관하게 TinyMCE에서 이미지 선택
            driver.execute_script("arguments[0].click();", imgs[-1])
            _s(0.5)
        else:
            print("    ⚠️ 이미지 삽입 미확인 — 업로드 실패 가능성")
        # 이미지 선택 상태에서 아래 화살표 5번 → 이미지 아래 단락으로 커서 이동
        for _ in range(5):
            ActionChains(driver).send_keys(Keys.ARROW_DOWN).perform()
            _s(0.2)
        _s(0.3)
        if after_newline:
            ActionChains(driver).send_keys(Keys.RETURN).perform()
            _s(0.3)
        print("    ✅ 본문 커서 활성화")
        driver.switch_to.default_content()
    except Exception as e:
        print(f"    ⚠️ 커서 이동 실패: {e}")
        _g_report("writer", e, module=__name__)
        driver.switch_to.default_content()
    _s(0.3)


def _html_escape(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _tinymce_insert(html: str, driver) -> bool:
    """TinyMCE execCommand로 HTML 직접 삽입. OS 포커스 불필요. 성공 여부 반환."""
    try:
        driver.switch_to.default_content()
        driver.execute_script("""
            if (typeof tinyMCE !== 'undefined' && tinyMCE.activeEditor) {
                tinyMCE.activeEditor.execCommand('mceInsertContent', false, arguments[0]);
            }
        """, html)
        _s(0.3)
        return True
    except Exception as e:
        print(f"    ⚠️ JS 삽입 실패: {e}")
        _g_report("writer", e, module=__name__)
        return False


def _input_text(text: str, driver=None):
    """텍스트 단락을 TinyMCE JavaScript API로 삽입 — OS 포커스 의존성 없음.
    HID Cmd+V 방식은 _inject_html_block/_chrome_focus() 호출 후 포커스가
    URL바로 이동하는 간헐적 버그를 유발하므로 JS 방식으로 교체."""
    import re as _re_it
    if not text.strip() or driver is None:
        return

    _num_pat = _re_it.compile(
        r'^[①②③④⑤⑥⑦⑧⑨]|^\d+\.\s|^(첫째|둘째|셋째|넷째|다섯째)[,，]?\s'
    )

    raw_paras = [p.strip() for p in text.split('\n\n') if p.strip()]
    html_parts = []

    for para in raw_paras:
        flat = ' '.join(l.strip() for l in para.split('\n') if l.strip())
        if not flat:
            continue

        if _num_pat.match(flat):
            # ★ 간격 수정 2026-05-27: trailing <p>&nbsp;</p> 제거 — spacer 블록이 간격 담당
            html_parts.append(f'<p>{_html_escape(flat)}</p>')
        else:
            sentences = _split_into_paragraphs(flat)
            for sent in sentences:
                html_parts.append(f'<p>{_html_escape(sent)}</p>')
            # ★ 간격 수정 2026-05-27: trailing <p>&nbsp;</p> 제거 — spacer 블록이 간격 담당

    if not html_parts:
        return

    combined = ''.join(html_parts)
    if not _tinymce_insert(combined, driver):
        # fallback: HID paste (포커스 상태 의존, 실패할 수 있음)
        pyperclip.copy(text)
        _s(0.4)
        _cgevent_paste()
        _s(0.6)
        _pg.press('return')
        _s(0.3)


def _inject_html_block(html_str: str, driver):
    """TinyMCE 에디터에 HTML 블록 직접 삽입 — JS API 사용, OS 포커스 불필요."""
    # _chrome_focus() 제거 — osascript activate가 TinyMCE 포커스를 URL바로 리셋하는 버그
    # 이후 _input_text(HID 방식)가 에디터 밖에 붙여넣는 원인이었음
    if not _tinymce_insert(html_str, driver):
        import re, html as html_module
        # Tistory 폴백: 간단한 HTML 제거 (naver_poster 불필요)
        text = html_module.unescape(re.sub(r'<[^>]+>', '', html_str)).strip()
        if text:
            _input_text(text, driver=driver)
        return
    print("    ✅ HTML 블록 삽입")


# ── 팝업 자동 제거 ──────────────────────────────────────────────

_CLOSE_SELECTORS = [
    # 범용 닫기 버튼
    'button[aria-label="닫기"]', 'button[aria-label="close"]',
    'button[aria-label="Close"]',
    '.modal-close', '.popup-close', '.layer-close',
    '.btn-close', '.close-btn', '[data-dismiss="modal"]',
    # 티스토리 자체 팝업
    '.layer_popup .btn_close', '.wrap_popup .btn_cancel',
    '.dimmed + .popup .btn_close',
    # 카카오 계열
    '.kakao_modal .close', '.kf-close',
    # 쿠키/공지 배너
    '#cookie-close', '.cookie-close', '.gdpr-close',
    # 일반 × 패턴
    'button.close', '[class*="close"][role="button"]',
    '[class*="Close"][role="button"]', '[class*="dismiss"]',
]

_OVERLAY_SELECTORS = (
    '.modal', '.popup', '.layer_popup', '.dimmed',
    '.modal-backdrop', '[class*="overlay"]', '[class*="Overlay"]',
    '[class*="backdrop"]', '[class*="Backdrop"]', '[class*="dialog"]',
)


def _has_visible_overlay(driver) -> bool:
    """화면에 보이는 오버레이/모달이 존재하면 True."""
    try:
        sel = ', '.join(_OVERLAY_SELECTORS)
        return driver.execute_script(f"""
            var els = document.querySelectorAll('{sel}');
            for (var el of els) {{
                var s = window.getComputedStyle(el);
                if (s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0'
                        && el.offsetWidth > 0 && el.offsetHeight > 0) {{
                    return true;
                }}
            }}
            return false;
        """)
    except Exception:
        return False


def _dismiss_any_popup(driver) -> bool:
    """
    블록 삽입 직전 호출 — 어떤 팝업이든 자동으로 닫는 범용 함수.
    ① 브라우저 Alert  → dismiss()
    ② 오버레이 감지 시: ESC 키 → DOM 닫기 버튼 → JS 강제 제거
    반환: 팝업을 하나라도 처리했으면 True
    """
    dismissed = False

    # ① 브라우저 Alert/Confirm/Prompt
    try:
        alert = driver.switch_to.alert
        print(f"  🚨 Alert 감지 → dismiss: {alert.text[:40]}")
        alert.dismiss()
        _s(0.5)
        dismissed = True
    except Exception:
        pass

    # ② 오버레이가 없으면 나머지 단계 생략 (성능 최적화)
    if not _has_visible_overlay(driver):
        return dismissed

    print("  🚨 팝업 감지 → 자동 제거 시도")

    # ③ ESC 키 (대부분의 모달은 ESC로 닫힘)
    try:
        driver.switch_to.default_content()
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        _s(0.4)
        if not _has_visible_overlay(driver):
            print("  ✅ ESC로 팝업 제거")
            return True
    except Exception:
        pass

    # ④ DOM 닫기 버튼 순서대로 시도
    for sel in _CLOSE_SELECTORS:
        try:
            for btn in driver.find_elements(By.CSS_SELECTOR, sel):
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    print(f"  ✅ 팝업 닫기 버튼 클릭: {sel}")
                    _s(0.4)
                    dismissed = True
                    if not _has_visible_overlay(driver):
                        return True
        except Exception:
            pass

    # ⑤ JS 강제 제거 (위 방법이 모두 실패한 경우 최후 수단)
    try:
        removed = driver.execute_script(f"""
            var sel = '{", ".join(_OVERLAY_SELECTORS)}';
            var removed = 0;
            document.querySelectorAll(sel).forEach(function(el) {{
                var s = window.getComputedStyle(el);
                if (s.display !== 'none' && s.visibility !== 'hidden'
                        && el.offsetWidth > 0 && el.offsetHeight > 0) {{
                    el.style.display = 'none';
                    removed++;
                }}
            }});
            return removed;
        """)
        if removed:
            print(f"  ✅ 오버레이 {removed}개 JS 강제 제거")
            _s(0.3)
            dismissed = True
    except Exception:
        pass

    return dismissed


def post_to_tistory(
    title: str,
    html_content: str,
    img_dir: str = None,
    blocks: list = None,
    category: str = "테마분류",
    tags: list = None,
    preloaded_driver=None,   # 쿠키 갱신 후 재사용할 driver (None이면 새로 생성)
    related_posts: list = None,
    edit_post_id: str = "",   # 수정 모드 — 기존 글 ID
) -> bool:

    print(f"  📝 티스토리: {TS_BLOG}.tistory.com")
    _external_driver = preloaded_driver is not None
    driver = preloaded_driver if _external_driver else _make_driver()

    try:
        if _external_driver:
            # 이미 로그인된 driver — 로그인 생략, 바로 에디터로
            print("  ✅ 쿠키 로그인 성공 (갱신 driver 재사용)")
        else:
            # 1. 로그인
            if not _login(driver):
                return False

        # 2. 글쓰기 페이지 (수정 모드면 해당 글 편집 URL)
        if edit_post_id:
            edit_url = f"https://{TS_BLOG}.tistory.com/manage/post/{edit_post_id}/edit"
            print(f"  ✏️  수정 모드 진입: post_id={edit_post_id}")
            driver.get(edit_url)
            _s(12)  # 기존 본문 로딩 더 대기
        else:
            print("  🌐 글쓰기 페이지...")
            driver.get(f"https://{TS_BLOG}.tistory.com/manage/newpost")
            _s(10)  # 로딩 충분히 대기

        # ★ 편집창 활성화 — Chrome 윈도우를 OS 최전면으로 (사용자가 볼 수 있도록)
        _chrome_focus()
        _s(0.5)

        # 2.5 임시저장 팝업 먼저 처리 (페이지 로딩 직후 바로 뜰 수 있음)
        for _ in range(3):
            try:
                alert = driver.switch_to.alert
                print(f"  ✅ Alert 닫기: {alert.text[:30]}")
                alert.dismiss()  # 취소 (새 글 작성)
                _s(1)
            except:
                break

        # 2.6 페이지 로딩 확인 — 로그인 페이지로 튕겼으면 재로그인
        try:
            current = driver.current_url
        except:
            # Alert 때문에 current_url 못읽으면 alert 한번 더 닫기
            try:
                driver.switch_to.alert.dismiss()
                _s(1)
            except:
                pass
            try:
                current = driver.current_url
            except Exception as _e_session:
                # ★ 세션 자체가 무효 (Chrome 크래시 등) — 새 driver 로 복구
                print(f"  ⚠️ 세션 무효 — 새 driver 생성: {_e_session}")
                try:
                    driver.quit()
                except:
                    pass
                driver = _make_driver()
                if not _login(driver):
                    return False
                if edit_post_id:
                    driver.get(f"https://{TS_BLOG}.tistory.com/manage/post/{edit_post_id}/edit")
                else:
                    driver.get(f"https://{TS_BLOG}.tistory.com/manage/newpost")
                _s(12)
                current = driver.current_url

        print(f"  🔍 현재 URL: {current[:60]}")
        if 'login' in current or 'tistory.com' not in current:
            print("  ⚠️ 로그인 페이지로 튕김 — 재로그인 시도")
            if not _login(driver):
                return False
            if edit_post_id:
                driver.get(f"https://{TS_BLOG}.tistory.com/manage/post/{edit_post_id}/edit")
                _s(12)
            else:
                driver.get(f"https://{TS_BLOG}.tistory.com/manage/newpost")
                _s(12)  # 재로그인 후 로딩 더 충분히 대기
            # 재로그인 후 팝업 처리
            for _ in range(3):
                try:
                    alert = driver.switch_to.alert
                    alert.dismiss()
                    _s(1)
                except:
                    break
            # ★ 재로그인 후에도 URL 재확인 — 2차 실패 시 즉시 종료
            _relogin_url = driver.current_url
            print(f"  🔍 재로그인 후 URL: {_relogin_url[:60]}")
            if 'login' in _relogin_url or 'tistory.com' not in _relogin_url:
                print("  ❌ 재로그인 후에도 로그인 페이지 — 쿠키 갱신 시도...")
                try:
                    from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _refresh_cookie
                    _ok_refresh = _refresh_cookie(force=True, notify=False)
                    if _ok_refresh:
                        print("  ✅ 쿠키 갱신 완료! 재발행 시도...")
                        driver.quit()
                        print("  🔄 새 드라이버로 재시도 중...")
                        driver = _make_driver()
                        # ★ 쿠키 갱신 후 새 드라이버에 반드시 _login() 호출 — 미호출 시 쿠키 없이 manage/newpost 진입 → 로그인 페이지 튕김
                        if not _login(driver):
                            print("  ❌ 새 드라이버 로그인 실패")
                            return False
                        driver.get(f"https://{TS_BLOG}.tistory.com/manage/newpost")
                        _s(12)  # 재로그인 후 로딩 충분히 대기 (3→12초)
                        # 재로그인 후 팝업 처리
                        for _ in range(3):
                            try:
                                alert = driver.switch_to.alert
                                alert.dismiss()
                                _s(1)
                            except:
                                break
                        print("  ✅ 재시도 준비 완료 — 계속 진행")
                    else:
                        print("  ❌ 쿠키 갱신 실패")
                        return False
                except Exception as _e_refresh:
                    print(f"  ⚠️ 쿠키 갱신 중 오류: {_e_refresh}")
                    return False

        # 3.5 카테고리 선택 — 3-tier 매칭 + 선택 검증
        print(f"  📂 카테고리 선택: {category}")
        _s(3)
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            cat_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, 'category-btn'))
            )
            driver.execute_script("arguments[0].click()", cat_btn)

            # category-list 나타날 때까지 대기 (최대 5초, 폴백 _s(2))
            try:
                WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((By.ID, 'category-list'))
                )
            except Exception:
                _s(2)

            cat_clicked = driver.execute_script("""
                var cat = arguments[0];
                var list = document.getElementById('category-list');
                if (!list) return 'category-list 없음';

                // 1순위: button/a 리프 요소 — 정확히 일치
                var leaves = list.querySelectorAll('button, a');
                for (var el of leaves) {
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t === cat) { el.click(); return '정확: ' + t; }
                }

                // 2순위: button/a 리프 요소 — startsWith (숫자 카운트 suffix 허용)
                for (var el of leaves) {
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t && t.startsWith(cat)) { el.click(); return 'starts: ' + t; }
                }

                // 3순위: 모든 요소 — includes + 길이 제한 (부모 컨테이너 혼합 텍스트 방지)
                var items = list.querySelectorAll('li, a, div[role], button');
                for (var el of items) {
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t && t.includes(cat) && t.length <= cat.length + 10) {
                        el.click(); return 'includes: ' + t;
                    }
                }

                // 디버그: 리프 목록 출력
                var all = [];
                leaves.forEach(function(el) {
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t) all.push('"' + t + '"');
                });
                return '목록: ' + all.join(', ');
            """, category)
            print(f"  ✅ 카테고리 클릭: {cat_clicked}")
            _s(0.5)

            # ★ 검증: category-btn 텍스트가 원하는 카테고리로 변경됐는지 확인
            try:
                actual_cat = driver.execute_script(
                    "var b=document.getElementById('category-btn');"
                    "return b?(b.innerText||b.textContent||'').trim():'';"
                )
                if category in actual_cat:
                    print(f"  ✅ 카테고리 확인: {actual_cat}")
                else:
                    print(f"  ⚠️ 카테고리 불일치 (버튼: '{actual_cat}') — 재시도")
                    driver.execute_script(
                        "arguments[0].click()",
                        driver.find_element(By.ID, 'category-btn')
                    )
                    _s(1)
                    driver.execute_script("""
                        var cat=arguments[0], list=document.getElementById('category-list');
                        if(!list) return;
                        var els=list.querySelectorAll('button,a');
                        for(var el of els){
                            var t=(el.innerText||el.textContent||'').trim();
                            if(t&&(t===cat||t.startsWith(cat))){el.click();return;}
                        }
                    """, category)
                    _s(0.5)
            except Exception as ve:
                print(f"  ⚠️ 카테고리 검증 생략: {ve}")

        except Exception as e:
            print(f"  ⚠️ 카테고리 선택 오류: {e}")
            _g_report("writer", e, module=__name__)
        _s(0.5)

        # 4. 제목 입력 (JavaScript 직접 입력 — 화면 밝기 무관)
        print("  📌 제목 입력...")
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        print(f"  🔍 제목 입력 전 URL: {driver.current_url[:60]}")

        # post-title-inp 찾기 (★ ERRORS [136] — 30→45초 + visibility, 사용자 박제 2026-05-17)
        try:
            title_el = WebDriverWait(driver, 45).until(
                EC.visibility_of_element_located((By.ID, 'post-title-inp'))
            )
        except:
            print("  ⚠️ 제목 요소 못찾음 — 페이지 새로고침 후 재시도")
            driver.refresh()
            _s(8)
            try:
                driver.switch_to.alert.dismiss()
            except:
                pass
            title_el = WebDriverWait(driver, 45).until(
                EC.visibility_of_element_located((By.ID, 'post-title-inp'))
            )

        # JavaScript로 직접 제목 입력 (textarea — execCommand 방식)
        driver.execute_script("arguments[0].focus();", title_el)
        _s(0.3)
        driver.execute_script("""
            var el = arguments[0];
            var txt = arguments[1];
            el.focus();
            el.select();
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, txt);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        """, title_el, title)
        _s(0.5)
        # 확인
        val = driver.execute_script("return arguments[0].value;", title_el)
        print(f"  ✅ 제목 완료: {val[:30]}...")

        # 5. 에디터 포커스 (JavaScript로)
        try:
            frame = driver.find_element(By.ID, 'editor-tistory_ifr')
            driver.switch_to.frame(frame)
            driver.execute_script("document.body.focus();")
            # 수정 모드 — 기존 본문 전부 삭제 (iframe body innerHTML 비우기)
            if edit_post_id:
                driver.execute_script(
                    "document.body.innerHTML = '<p><br></p>';"
                    "document.body.focus();"
                )
                print("  🗑️  기존 본문 삭제 완료 (수정 모드)")
            driver.switch_to.default_content()
            _s(0.5)
        except Exception as e:
            print(f"  ⚠️ 에디터 포커스: {e}")
            _g_report("writer", e, module=__name__)
            driver.switch_to.default_content()

        # 6. 블록 입력
        DIVIDER = "　" * 15 + "─" * 20 + "　" * 15  # 구분선 (가운데 정렬)

        def input_divider():
            _input_text(DIVIDER, driver=driver)

        if blocks:
            print(f"  📦 {len(blocks)}개 블록 입력...")

            # 썸네일(첫 번째 이미지) 먼저 입력
            if blocks[0][0] == 'image':
                print(f"  [썸네일] {str(blocks[0][1])[:50]}")
                _upload_image(str(blocks[0][1]), driver=driver)
                # 썸네일 업로드 후 TinyMCE iframe에 OS 레벨 키보드 포커스 복구
                # JS ed.focus()는 TinyMCE 내부 포커스만 설정하고 OS 레벨 포커스는 iframe에 미전달
                # → ActionChains.click(body)로 실제 클릭 이벤트 전송해야 OS 레벨 포커스 획득
                _focus_editor(driver)
                _s(0.5)
                print("  ✅ 에디터 포커스 복구 (body.click)")
                remaining = blocks[1:]
            else:
                remaining = blocks

            # ★ 사용자 박제 2026-05-20 — spacer + text 블록 병합 전처리.
            # TinyMCE mceInsertContent 는 커서가 빈 <p> 안에 있으면 해당 단락을 교체(replace).
            # spacer 삽입 후 text 를 별도로 삽입하면 spacer 가 삭제됨 → 단락 간 1행 여백 소실.
            # 해결: spacer 뒤에 text 블록이 오면 spacer HTML 을 text 앞에 합쳐서 1회 삽입.
            # 이렇게 하면 커서가 비어있지 않은 단락(text1)에 삽입되므로 split 동작 → spacer 보존.
            _merged: list = []
            _skip = set()
            for _bi, (_bt, _bd) in enumerate(remaining):
                if _bi in _skip:
                    continue
                if (_bt == 'spacer'
                        and _bi + 1 < len(remaining)
                        and remaining[_bi + 1][0] in ('text', 'html')):
                    # spacer + text 병합
                    # ★ 간격 통일 2026-05-27: 항상 1칸 (spacer_1/2 구분 불필요)
                    _spacer_prefix = '<p><br></p>'
                    _next_bt, _next_bd = remaining[_bi + 1]
                    _merged.append((_next_bt, _spacer_prefix + str(_next_bd)))
                    _skip.add(_bi + 1)
                else:
                    _merged.append((_bt, _bd))

            # 나머지 블록: divider 블록에서 구분선 삽입
            for bi, (btype, bdata) in enumerate(_merged):
                print(f"  [{bi+1}/{len(_merged)}] {btype}: {str(bdata)[:60]}")
                _dismiss_any_popup(driver)  # 팝업이 있으면 먼저 제거
                if btype == 'divider':
                    input_divider()
                elif btype == 'heading2':
                    # h2 섹션 제목: 구분선 + ▶ 굵게 (앞 간격은 spacer 블록이 처리)
                    _inject_html_block(
                        f'<hr/>'
                        f'<p><strong>▶ {bdata}</strong></p>',
                        driver=driver
                    )
                elif btype == 'heading':
                    # h3 소제목: ◆ 굵게 (앞 간격은 spacer 블록이 처리)
                    _inject_html_block(
                        f'<p><strong>◆ {bdata}</strong></p>',
                        driver=driver
                    )
                elif btype == 'spacer':
                    # image / heading 앞 spacer (text 앞 spacer 는 위 병합에서 처리됨)
                    # ★ 간격 통일 2026-05-27: 항상 1칸 — spacer_1/2 구분 불필요
                    _tinymce_insert('<p><br></p>', driver)
                elif btype == 'text':
                    _inject_html_block(str(bdata), driver=driver)
                elif btype == 'html':
                    _inject_html_block(str(bdata), driver=driver)
                elif btype == 'image':
                    # ★ 간격 수정 2026-05-27: after_newline=False (모든 이미지) — 이미지 뒤 여백은 spacer 블록이 처리
                    # spacer 앞 2행 여백은 enforce_spacing()이 spacer 블록으로 이미 삽입 — 여기서 중복 삽입 금지
                    _upload_image(str(bdata), driver=driver, after_newline=False)
                    _dismiss_any_popup(driver)  # 이미지 업로드 후 팝업 체크
                    # _upload_image() 내부에서 이미 커서를 이미지 아래로 이동 완료
                    # _focus_editor() 추가하면 body.click()이 커서를 중간으로 밀어버릴 수 있음
        elif html_content:
            # HTML 직접 주입 (경제 브리핑 등 이미지 없는 HTML 포스트)
            print("  📄 HTML 본문 직접 주입...")
            try:
                frame = driver.find_element(By.ID, 'editor-tistory_ifr')
                driver.switch_to.frame(frame)
                driver.execute_script("document.body.innerHTML = arguments[0];", html_content)
                driver.switch_to.default_content()
                print("  ✅ HTML 주입 완료")
            except Exception as e:
                print(f"  ⚠️ HTML 주입 실패, 텍스트로 대체: {e}")
                _g_report("writer", e, module=__name__)
                driver.switch_to.default_content()
                import re, html as html_module
                plain = html_module.unescape(re.sub(r'<[^>]+>', '', html_content)).strip()
                _input_text(plain, driver=driver)
        else:
            img_folder = Path(img_dir) if img_dir else JARVIS06_BASE / "output" / "naver_images"
            images = sorted(img_folder.glob("*.png"))
            for img in images:
                _upload_image(str(img), driver=driver)

        # ── 연관 글 ──────────────────────────────────
        _posts = related_posts if related_posts is not None else _fetch_recent_tistory_posts(1)
        if _posts:
            related_html = (
                '<hr/>'
                '<div style="background:#f8f9fa;border-left:4px solid #2563eb;'
                'padding:14px 18px;margin:20px 0;border-radius:4px;">'
                '<p style="font-weight:700;margin:0 0 8px;">[함께 읽으면 좋은 글]</p>'
                '<ul style="margin:0;padding-left:18px;line-height:1.9;">'
            )
            for rp in _posts:
                related_html += f'<li><a href="{rp["url"]}" style="color:#2563eb;">{rp["title"]}</a></li>'
            related_html += '</ul></div>'
            _inject_html_block(related_html, driver=driver)
            print("  ✅ 연관 글 삽입 완료")

        # 7. 태그 입력 (완료 버튼 전에)
        print("  🏷️  태그 입력...")

        if tags is None:
            # 본문 전체 텍스트 추출
            body_text = ' '.join(str(bdata) for btype, bdata in (blocks or []) if btype == 'text')
            print("  🔍 태그 생성 중 (제목 2개 + 본문 2개)...")
            tags = _generate_smart_tags(title, body_text)

        # 화면 50% 스크롤 다운 후 태그란 클릭
        _chrome_focus()
        screen_h = _pg.size().height
        _pg.scroll(-int(screen_h * 0.5), x=694, y=400)
        _s(1)

        # tagText - Selenium ActionChains로 직접 클릭 (좌표 불필요)
        tag_el = driver.find_element(By.ID, 'tagText')
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", tag_el)
        _s(0.5)
        _chrome_focus()
        ActionChains(driver).click(tag_el).perform()
        _s(0.8)

        for tag in tags:
            # ActionChains로 태그 입력 + Enter
            ActionChains(driver).click(tag_el).send_keys(tag).send_keys(Keys.RETURN).perform()
            _s(0.5)
        print(f"  ✅ 태그: {tags}")

        # 8. 완료 버튼 (태그 입력 후 발행 팝업 열기)
        print("  🚀 완료 버튼...")
        _chrome_focus()
        done_btn = driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button'));
            for (var b of btns) {
                if (b.id === 'publish-layer-btn' || b.innerText.trim() === '완료') {
                    b.click(); return '완료 클릭: ' + b.id;
                }
            }
            return null;
        """)
        if done_btn:
            print(f"  ✅ {done_btn}")
        else:
            _pg.click(1294, 597)
        _s(2)

        # 9. 최종 발행 - 공개 전환 후 공개발행
        print("  ✅ 최종 발행...")

        # 비공개 → 공개 전환 (라디오버튼 또는 버튼)
        pub_set = driver.execute_script("""
            // 공개 라디오버튼 찾기
            var radios = document.querySelectorAll('input[type="radio"]');
            for (var r of radios) {
                if (r.value === '3' || r.value === 'public') {
                    r.click(); return '공개 라디오 클릭: ' + r.value;
                }
            }
            // 공개 텍스트 버튼 찾기
            var btns = document.querySelectorAll('button, label');
            for (var b of btns) {
                var t = b.innerText.trim();
                if (t === '공개' || t === '전체공개') {
                    b.click(); return '공개 버튼 클릭: ' + t;
                }
            }
            return null;
        """)
        if pub_set:
            print(f"  ✅ {pub_set}")
        _s(0.5)

        # 공개발행 버튼 클릭
        _s(0.5)
        published = driver.execute_script("""
            var btns = Array.from(document.querySelectorAll('button'));
            for (var b of btns) {
                var t = b.innerText.trim();
                var r = b.getBoundingClientRect();
                // 모든 발행 관련 버튼 후보 출력
                if (r.width > 0 && r.height > 0 && t) {
                    console.log(t + ' @(' + Math.round(r.x) + ',' + Math.round(r.y) + ')');
                }
                if (t==='공개발행' || t==='발행하기' || t==='발행') {
                    b.click(); return '클릭: '+t+' @('+Math.round(r.x)+','+Math.round(r.y)+')';
                }
            }
            // id로 찾기
            var pub = document.querySelector('#publish-btn, .btn-publish, [data-btn="publish"]');
            if (pub) { pub.click(); return '클릭(id): ' + pub.id; }
            return null;
        """)
        if published:
            print(f"  ✅ {published}")
        else:
            print("  ⚠️ 발행 버튼 못찾음 - 로그 확인 필요")
        _s(4)

        print("  🎉 티스토리 포스팅 완료!")

        # ── 발행 URL 캡처 (RSS 기반) ──────────────────────────
        global _last_post_url
        try:
            _s(3)  # RSS 갱신 대기
            posts = _fetch_recent_tistory_posts(1)
            _last_post_url = posts[0]["url"] if posts else ""
            if _last_post_url:
                print(f"  📎 발행 URL: {_last_post_url}")
                # 발행된 글 페이지로 자동 이동
                driver.get(_last_post_url)
                _s(2)
                print(f"  ✅ 발행된 글 페이지로 이동")
        except Exception as _e:
            _last_post_url = ""
            print(f"  ⚠️ URL 캡처 실패: {_e}")
            _g_report("writer", _e, module=__name__)

        # ★ 발행 성공 확인 (2026-07-02): 클릭=성공으로 간주하던 갭 수정.
        #   버튼도 못 찾고(published=None) RSS URL 도 못 잡으면 실제 발행 실패.
        #   버튼 미발견 = 클릭 안 됨 = 발행 안 됨 → False 여도 이중발행 위험 없음.
        if not published and not _last_post_url:
            print("  ❌ 티스토리 발행 미확인 — 발행 버튼 미발견 + RSS URL 미포착 → 실패 처리")
            _g_report("writer", RuntimeError("티스토리 발행 미확인(버튼·URL 모두 없음)"),
                      module=__name__)
            return False
        return True

    except Exception as e:
        print(f"  ❌ 오류: {e}")
        _g_report("writer", e, module=__name__)
        import traceback; traceback.print_exc()
        return False

    finally:
        _s(2)
        driver.quit()  # 외부/내부 driver 모두 여기서 종료
