"""JARVIS06_IMAGE/html_infographic.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HTML+CSS 기반 프리미엄 인포그래픽 생성기.

흐름: LLM이 HTML 문자열을 반환하는 Python 코드 생성
      → 안전 exec → html_str 추출 → Selenium → JPG

matplotlib보다 압도적 장점:
- SVG stroke-dasharray 원형 진행 게이지
- CSS Grid/Flexbox 다중 패널 레이아웃
- 픽토그램·아이소타입 아이콘 (Unicode)
- 그라디언트·그림자 완벽 지원

★ 공개 API 는 없다 — 이 모듈은 `_html_to_jpg` 하나짜리 렌더 백엔드다 (2026-08-10).
  docstring 이 광고하던 `generate_html_infographic` 은 **코드에 존재하지 않았다**
  (실측 히트 = 이 문장 1건). 설계서(`_PROMPT_TEMPLATE`)와 팔레트 시드
  (`_PALETTE_SEEDS`) 도 그 함수와 함께 죽어 참조 0인 채 남아 있었다 — 문서·상수가
  코드보다 오래 살아남으면 다음 작업자가 없는 기능을 있다고 믿는다. 함께 삭제.

  HTML 저작은 `infographic_engine` 이 하고, 여기는 그 HTML 을 픽셀로 만든다.
  소비자: infographic_engine · pro_templates · design_learner.
"""
from __future__ import annotations
import logging
import os
import tempfile
from pathlib import Path


# ── 렌더 정지 방어 상수 (★ 2026-07-24 freeze 근본수정 — CHART 누적 렌더 300s freeze) ──
#   Chromium 렌더 subprocess 대기 중 watchdog.beat() 주기를 freeze 임계(FREEZE_LIMIT_SEC)
#   에서 *파생* — 하드코딩 대신 SSOT 파생(CLAUDE.md ② 동적 설계). 300s → 10s 마다 beat.
try:
    from JARVIS00_INFRA.watchdog import FREEZE_LIMIT_SEC as _FREEZE_LIMIT_SEC
except Exception:
    _FREEZE_LIMIT_SEC = 300.0
_RENDER_BEAT_POLL_SEC = max(2.0, float(_FREEZE_LIMIT_SEC) / 30.0)   # 렌더 대기 중 beat 주기(~10s)
_RENDER_HARD_TIMEOUT_SEC = 120.0    # 단일 차트 렌더 하드 상한(종전 subprocess.run timeout=120 계승)
_RENDER_GOTO_TIMEOUT_MS = 15000     # goto/폰트 대기 바운드 — 원격폰트 네트워크 열화 시 무한 블로킹 차단


log = logging.getLogger("jarvis")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


# ══════════════════════════════════════════════════════════════════
#  5. HTML → JPG via Selenium
# ══════════════════════════════════════════════════════════════════

def _html_to_jpg(html_str: str, out_path: Path, width: int = 980) -> bool:
    """Playwright(headless Chromium)로 HTML → JPG 풀페이지 캡처.

    ★ 2026-06-29: Selenium(chromedriver 미설치로 실패) → Playwright 로 교체.
       html_renderer._find_chromium() 의 작동하는 Chromium 경로 재사용 + full_page.
    """
    import subprocess, sys as _sys, signal
    # ★ `sys.executable` 금지 (2026-08-08, ERRORS EvalEnvBroken #5386/#5389) — macOS Framework
    #   Python 재기동 시 venv 밖으로 떨어질 위험. `.venv/bin/python3` 를 경로로 직접 지정
    #   (auto_repair.py 와 동일 패턴 — 단일 진실).
    _venv_py = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "python3"
    _subproc_py = str(_venv_py) if _venv_py.exists() else _sys.executable
    try:
        from JARVIS06_IMAGE.html_renderer import _find_chromium
        chromium = _find_chromium()
        png_tmp = out_path.with_suffix(".png")

        # HTML 을 임시 파일로 — subprocess Playwright 가 file:// 로 로드
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html_str)
            html_file = f.name

        # ★ 2026-06-29: LLM 호출(claude SDK=asyncio/anyio)이 메인 프로세스 이벤트 루프를
        #   닫아 in-process Playwright 가 "Event loop is closed" 로 실패 → 렌더를 *완전히
        #   분리된 subprocess* 에서 실행해 asyncio 오염을 원천 차단.
        render_code = (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            f"    b = p.chromium.launch(executable_path={chromium!r}, "
            "args=['--no-sandbox','--disable-dev-shm-usage','--lang=ko-KR'])\n"
            # ★ 뷰포트 폭 = 내용 폭(width 파라미터) — body 가 뷰포트까지 늘어나 우측 여백이
            #   생기던 버그 수정(사용자 박제 2026-07-06). 1560 고정 → 캡처가 내용보다 넓어 우측 공백.
            f"    pg = b.new_page(viewport={{'width':{max(int(width), 320)},'height':1100}}, device_scale_factor=2)\n"
            # ★ 2026-07-24 freeze 근본수정: wait_until 'networkidle'→'load'.
            #   'networkidle' 은 원격 웹폰트(Google Fonts) 요청이 네트워크 열화로 trickle 될 때
            #   페이지당 최대 goto-timeout 까지 블로킹 → 8차트 누적이 freeze 임계(300s)를 넘겼다.
            #   'load' + 바운드 goto-timeout 으로 무한 대기 차단(로컬 file:// 는 DOM load 로 충분).
            f"    pg.set_default_timeout({_RENDER_GOTO_TIMEOUT_MS})\n"
            "    try:\n"
            f"        pg.goto({('file://'+html_file)!r}, wait_until='load', timeout={_RENDER_GOTO_TIMEOUT_MS})\n"
            "    except Exception:\n"
            "        pass\n"
            "    try:\n"
            "        pg.evaluate('document.fonts && document.fonts.ready')\n"
            "    except Exception:\n"
            "        pass\n"
            "    pg.wait_for_timeout(900)\n"
            # ★ 방향 고정 안 함: 디자인의 실제 박스(body)를 캡처 → 가로/세로 무엇이든 딱 맞게
            "    el = pg.query_selector('body')\n"
            f"    el.screenshot(path={str(png_tmp)!r}) if el else pg.screenshot(path={str(png_tmp)!r}, full_page=True)\n"
            "    b.close()\n"
        )
        # ★ 2026-07-24 freeze 근본수정: subprocess.run(총소요 상한 없이 블로킹) →
        #   Popen + 짧은 주기 communicate 폴링. 대기 중 watchdog.beat() 를 주기적으로
        #   찍어 렌더가 진행 중임을 알린다(무진전 freeze 오탐 차단). 하드 상한 도달 시
        #   프로세스 그룹째 SIGKILL(start_new_session 으로 별도 그룹) — 좀비 자식 방지.
        try:
            from JARVIS00_INFRA.watchdog import beat as _wd_beat
        except Exception:
            def _wd_beat() -> None: pass  # watchdog 부재 시 no-op
        try:
            proc = subprocess.Popen(
                [_subproc_py, "-c", render_code],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
            _stderr = ""
            _elapsed = 0.0
            while True:
                try:
                    _, _stderr = proc.communicate(timeout=_RENDER_BEAT_POLL_SEC)
                    break
                except subprocess.TimeoutExpired:
                    _wd_beat()   # ★ 렌더 대기 중 진행 신호 — freeze 오탐 방지
                    _elapsed += _RENDER_BEAT_POLL_SEC
                    if _elapsed >= _RENDER_HARD_TIMEOUT_SEC:
                        try:
                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                        except Exception:
                            proc.kill()
                        try:
                            _, _stderr = proc.communicate(timeout=5)
                        except Exception:
                            pass
                        log.warning(f"[html_infographic] 렌더 하드타임아웃 {_RENDER_HARD_TIMEOUT_SEC:.0f}s 초과 — 강제 종료")
                        break
            if proc.returncode not in (0, None):
                log.warning(f"[html_infographic] subprocess 렌더 실패: {(_stderr or '')[:300]}")
        finally:
            try:
                Path(html_file).unlink(missing_ok=True)
            except Exception:
                pass

        if not png_tmp.exists() or png_tmp.stat().st_size < 5000:
            return False
        try:
            from PIL import Image
            img = Image.open(png_tmp)
            img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)  # 항상 @2x → 절반
            img.convert("RGB").save(out_path, "JPEG", quality=93, optimize=True)
            png_tmp.unlink(missing_ok=True)
        except ImportError:
            png_tmp.rename(out_path)
        return out_path.exists() and out_path.stat().st_size > 5000

    except Exception as e:
        log.warning(f"[html_infographic] 렌더 오류: {e}")
        _g_report("image", e, module=__name__, func_name="_html_to_jpg")
        return False


__all__: list[str] = []   # 공개 API 없음 — 소비자는 `_html_to_jpg` 를 직접 가져간다
