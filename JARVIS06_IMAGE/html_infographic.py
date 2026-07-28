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

공개 API:
    generate_html_infographic(theme, purpose, data, run_id, slot_key) → str  # <img> 태그
"""
from __future__ import annotations
import base64
import hashlib
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


def _max_attempts() -> int:
    """재시도 상한 — harness.DEFAULT_MAX_ATTEMPTS(SSOT) 파생 (사용자 박제 2026-07-21: 2회)."""
    try:
        from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS
        return max(1, int(DEFAULT_MAX_ATTEMPTS))
    except Exception:
        return 2

log = logging.getLogger("jarvis")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


def _wrap_img(b64: str, alt: str) -> str:
    return (
        f'<div style="background:white;border-radius:16px;padding:18px 18px 10px;'
        f'margin:22px 0;box-shadow:0 3px 20px rgba(102,126,234,0.1);'
        f'border:1px solid #e8ecf0;">'
        f'<img src="data:image/jpeg;base64,{b64}" '
        f'style="width:100%;max-width:760px;display:block;margin:0 auto;border-radius:8px;" '
        f'alt="{alt}"/></div>'
    )


# ══════════════════════════════════════════════════════════════════
#  1. 동적 HSL 팔레트
# ══════════════════════════════════════════════════════════════════

_PALETTE_SEEDS = [
    (210, 70, 35), (200, 65, 38), (230, 60, 40), (170, 58, 35),
    (25,  72, 40), (280, 55, 38), (345, 65, 38), (145, 60, 35),
]


def _dyn_hsl(theme: str, run_id: str) -> tuple[int, int, int]:
    h16 = hashlib.md5(f"{theme}|{run_id}".encode()).hexdigest()
    idx = int(h16[:2], 16) % len(_PALETTE_SEEDS)
    return _PALETTE_SEEDS[idx]


# ══════════════════════════════════════════════════════════════════
#  2. 데이터 포매터
# ══════════════════════════════════════════════════════════════════

def _fmt_data(data: dict | None) -> str:
    if not data:
        return "(데이터 없음 — 주제에 맞는 합리적 수치 사용)"
    lines: list[str] = []
    stocks = data.get("stocks") or []
    if stocks:
        lines.append("■ 종목 데이터:")
        for s in stocks[:6]:
            cap = s.get("cap_억")
            cap_str = (f"{cap/10000:.1f}조" if cap and cap >= 10000 else f"{cap:,.0f}억") if cap else "N/A"
            lines.append(
                f"  {s.get('name','?')}: 시총={cap_str}, PER={s.get('per',0):.1f}배, "
                f"ROE={s.get('roe',0):.1f}%, 영업이익률={s.get('op_margin',0):.1f}%"
            )
    summary = data.get("summary") or {}
    if summary:
        lines.append("■ 요약:")
        for k, v in list(summary.items())[:8]:
            lines.append(f"  {k}: {v}")
    trends = data.get("trends") or []
    if trends:
        lines.append("■ 시계열:")
        for t in trends[:5]:
            lines.append(f"  {t}")
    kpis = data.get("kpis") or []
    if kpis:
        lines.append("■ KPI:")
        for k in kpis[:8]:
            lines.append(f"  {k.get('label','?')}: {k.get('value','?')} {k.get('unit','')}")
    # ★ 2026-06-29: 임의 키도 렌더 (이전엔 stocks/summary/trends/kpis 만 인식 →
    #   일반 dict 는 "(데이터 없음)" 으로 흘려보내 LLM 이 빈 차트를 그리던 버그)
    _known = {"stocks", "summary", "trends", "kpis"}
    for k, v in data.items():
        if k in _known:
            continue
        if isinstance(v, dict):
            inner = ", ".join(f"{ik}={iv}" for ik, iv in list(v.items())[:14])
            lines.append(f"■ {k}: {inner}")
        elif isinstance(v, (list, tuple)):
            lines.append(f"■ {k}: " + ", ".join(str(x) for x in list(v)[:14]))
        else:
            lines.append(f"■ {k}: {v}")
    return "\n".join(lines) if lines else "(데이터 없음)"


# ══════════════════════════════════════════════════════════════════
#  3. 프롬프트 — Python 코드로 HTML 문자열 반환
# ══════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
당신은 통계청·기업 연차보고서급 편집 인포그래픽 디자이너입니다.
아래 실데이터로 **완전한 HTML 문서 하나**를 ```html 코드블록으로 직접 출력하세요.

테마: {theme} / 목적: {purpose}
기본색 HSL({H},{S}%,{L}%) · 강조색 HSL({H2},{S2}%,{L2}%)

[실데이터 — 이 수치만 사용. 지어내기·빈차트·"데이터없음" 절대 금지. 없는 항목은 생략]
{data_str}

[형식] ★ 방향은 내용에 맞게 *직접 선택* — **기본은 가로형(landscape)**, 항목·패널이 많으면
 세로형(portrait)도 가능. 고정하지 말 것. body 에 디자인 의도에 맞는 *고정 폭* 지정
 (가로형 1120~1280px·높이는 폭보다 작게 / 세로형 760~880px·높이는 폭보다 크게).
 - 상단: 그라디언트 헤더(제목+부제+날짜 배지).
 - KPI 카드 행: 아이콘+큰 숫자+증감 pill(▲빨강/▼파랑)+미니 스파크라인.
 - 메인: 카드 여러 개를 가로형이면 *가로로*, 세로형이면 *세로로* 배치. 빈 공간 없이 균형.

[차트 어휘 — 데이터 성격에 맞게 *매번 다르게* 골라 조합]
 그라디언트 세로/가로 막대 · 그룹막대 · SVG 꺾은선(그리드라인+면적 그라디언트) ·
 도넛/파이+레전드 · 원형 진행 게이지 · 픽토그램(이모지 반복) · STEP 화살표 프로세스 ·
 A vs B 비교 막대 · 랭킹 가로막대 · 마일스톤 타임라인.
 → **모든 막대·점·게이지에 데이터 값 라벨 필수.** 라벨이 패널 밖으로 나가지 않게(양끝은 안쪽 정렬).

[품질] SVG defs linearGradient 채움, 둥근 카드(radius 14~18, box-shadow), 통일 팔레트,
 마지막/최신 값만 강조색, 캡션 1줄, 풍부한 이모지 아이콘. Noto Sans KR
 (@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;800;900&display=swap')).
 최소 글자 12px. 외부 이미지 src 없음(SVG·이모지만). JavaScript 없음.

[다양성] ★ 매번 *다른 레이아웃·색조합·차트종류·배치*. 고정 템플릿 재사용 금지.

설명·주석 없이 ```html 블록 *하나만* 출력하세요:
"""


# (4. 안전 exec 게이트 제거 — LLM 이 HTML 을 직접 반환하도록 변경되며 exec 경로 폐지,
#  _FORBIDDEN·_SAFE_BUILTINS·_is_safe·_strip_imports 호출 0: 전수감사 DELETE[16])


# ══════════════════════════════════════════════════════════════════
#  5. HTML → JPG via Selenium
# ══════════════════════════════════════════════════════════════════

def _html_to_jpg(html_str: str, out_path: Path, width: int = 980) -> bool:
    """Playwright(headless Chromium)로 HTML → JPG 풀페이지 캡처.

    ★ 2026-06-29: Selenium(chromedriver 미설치로 실패) → Playwright 로 교체.
       html_renderer._find_chromium() 의 작동하는 Chromium 경로 재사용 + full_page.
    """
    import subprocess, sys as _sys, signal
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
                [_sys.executable, "-c", render_code],
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


# ══════════════════════════════════════════════════════════════════
#  6. 공개 API
# ══════════════════════════════════════════════════════════════════

__all__: list[str] = []
