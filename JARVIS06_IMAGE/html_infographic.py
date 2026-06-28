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
import re
import tempfile
import time
from pathlib import Path

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
    return "\n".join(lines) if lines else "(데이터 없음)"


# ══════════════════════════════════════════════════════════════════
#  3. 프롬프트 — Python 코드로 HTML 문자열 반환
# ══════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
당신은 세계 최고 데이터 시각화 디자이너이자 Python 전문가입니다.
아래 데이터를 바탕으로 **HTML 인포그래픽 문자열을 반환하는 Python 코드**를 작성하세요.

▣ 인포그래픽 정보
테마: {theme}
목적: {purpose}
기본색 HSL({H},{S}%,{L}%) — 강조색 HSL({H2},{S2}%,{L2}%)

▣ 사용 데이터 (이 수치를 차트에 그대로 사용)
{data_str}

▣ 요구 레이아웃 — 아래 요소를 데이터에 맞게 조합
  ① 원형 진행 게이지: <svg><circle stroke-dasharray="X 100" …> 방식
  ② CSS div 막대 차트: 연도별/항목별 높이 비례 (flex column-reverse)
  ③ KPI 숫자 카드: 큰 숫자 + 아이콘 + 설명 (2×2 또는 2×3 그리드)
  ④ 픽토그램 카드: 이모지(👤🏭📦⚡🌳🔬🏗️💊🚢) 반복으로 수량 표현
  ⑤ 타임라인 카드: 수평 선 + 연도별 마일스톤 원
  ⑥ 도넛 차트: SVG <circle> stroke-dasharray 비율 표현

▣ 디자인 규칙 (반드시 준수)
- 전체 너비: 900px, 배경: #f4f6f9
- 헤더: 기본색 배경 + 흰색 제목
- 6개 패널 → CSS Grid 2열 3행 (gap:16px, padding:20px)
- 패널: 흰 배경, border-radius:12px, box-shadow:0 2px 8px rgba(0,0,0,.10)
- 패널 상단: 컬러 dot(8px) + 소제목(14px bold)
- 최소 글자: 13px(라벨), 16px(본문), 24px(KPI 수치)
- 원형 게이지: SVG circle stroke-dasharray (pathLength="100" 기준)
- 막대 차트: div height:calc(value * 1px) flex column-reverse
- 픽토그램: 이모지 개수로 수량 표현 (예: "👤👤👤" × N)
- 실데이터 없는 항목: "(추정)" 표기
- 전체 최소 높이: 700px
- 웹폰트: @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
- font-family: 'Noto Sans KR', sans-serif

▣ Python 코드 구조 (반드시 이 형식)
```python
def build_infographic_html() -> str:
    # 여기에 HTML f-string 또는 문자열 조립 코드
    # inline <style> 포함, JavaScript 없음, 외부 이미지 src 없음
    html = \"\"\"<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8">
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700;900&display=swap');
/* ... 전체 CSS ... */
</style>
</head>
<body>
<!-- ... 전체 HTML ... -->
</body>
</html>\"\"\"
    return html

_RESULT_HTML = build_infographic_html()
```

★ 규칙:
- ```python 코드 블록 안에 완전한 Python 함수만 출력
- 마지막 줄: _RESULT_HTML = build_infographic_html()
- math, colorsys, random 은 이미 주입됨 (import 불필요)
- os/sys/subprocess/open() 사용 금지
- 파일 쓰기 금지 — 문자열 반환만
- 이미지 파일 참조 없음 (SVG·이모지만)
- 함수 안에서 import 금지 — 위 3개 모듈만 사용 가능

Python 코드만 출력 (```python 블록):
"""


# ══════════════════════════════════════════════════════════════════
#  4. 안전 exec
# ══════════════════════════════════════════════════════════════════

_FORBIDDEN = [
    r'\bos\s*\.\s*(system|popen|exec|remove|unlink|rename|mkdir)',
    r'\bsubprocess\b', r'__import__\s*\(',
    r'\bopen\s*\([^)]*["\']w["\']',  # write-mode
    r'import\s+os\b', r'import\s+sys\b', r'import\s+shutil\b',
    r'import\s+socket\b', r'import\s+urllib\b', r'import\s+requests\b',
]

_SAFE_BUILTINS = {
    "print": print, "range": range, "len": len, "int": int, "float": float,
    "str": str, "list": list, "dict": dict, "tuple": tuple, "set": set,
    "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
    "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
    "sorted": sorted, "any": any, "all": all, "type": type,
    "True": True, "False": False, "None": None,
    "repr": repr, "isinstance": isinstance, "hasattr": hasattr,
}


def _is_safe(code: str) -> bool:
    for pat in _FORBIDDEN:
        if re.search(pat, code):
            return False
    return True


def _strip_imports(code: str) -> str:
    """함수 내 import 구문 제거 (안전 모듈은 ns에 주입돼 있음)."""
    safe_mods = {"math", "colorsys", "random"}
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        # import math / from math import ... 형태 제거
        if re.match(r'^\s*import\s+(' + '|'.join(safe_mods) + r')\b', line):
            continue
        if re.match(r'^\s*from\s+(' + '|'.join(safe_mods) + r')\s+import\b', line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _exec_and_extract_html(code: str) -> str:
    """Python 코드 실행 → _RESULT_HTML 변수에서 HTML 문자열 추출."""
    import math as _math
    import colorsys as _colorsys
    import random as _random

    code = _strip_imports(code)

    ns: dict = {
        "__builtins__": _SAFE_BUILTINS,
        # 자주 쓰이는 안전 모듈 사전 주입
        "math": _math,
        "colorsys": _colorsys,
        "random": _random,
    }
    try:
        exec(code, ns)
        html = ns.get("_RESULT_HTML", "")
        if isinstance(html, str) and len(html) > 500 and "<html" in html.lower():
            return html
    except Exception as e:
        log.debug(f"[html_infographic] exec 오류: {e}")
    return ""


# ══════════════════════════════════════════════════════════════════
#  5. HTML → JPG via Selenium
# ══════════════════════════════════════════════════════════════════

def _html_to_jpg(html_str: str, out_path: Path, width: int = 920) -> bool:
    """Selenium headless Chrome으로 HTML → JPG 캡처."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", encoding="utf-8", delete=False
        ) as f:
            f.write(html_str)
            tmp_html = Path(f.name)

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument(f"--window-size={width},1400")
        options.add_argument("--force-device-scale-factor=2")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--lang=ko-KR")

        driver = webdriver.Chrome(options=options)
        try:
            driver.get(f"file://{tmp_html}")
            time.sleep(2.5)  # 웹폰트 로딩 대기

            # 콘텐츠 실제 높이 측정 (빈 공간 크롭용)
            content_h = driver.execute_script("""
                var els = document.body.querySelectorAll('*');
                var maxBottom = 0;
                for (var i = 0; i < els.length; i++) {
                    var r = els[i].getBoundingClientRect();
                    if (r.bottom > maxBottom) maxBottom = r.bottom;
                }
                return Math.ceil(maxBottom) + 80;
            """) or 900
            h = min(int(content_h), 3000)
            driver.set_window_size(width, h)
            time.sleep(0.3)

            png_tmp = out_path.with_suffix(".png")
            driver.save_screenshot(str(png_tmp))
            if not png_tmp.exists() or png_tmp.stat().st_size < 5000:
                return False

            try:
                from PIL import Image
                img = Image.open(png_tmp)
                # @2x 렌더링 → 절반 크기 다운스케일
                if img.width > width * 1.5:
                    img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
                img.convert("RGB").save(out_path, "JPEG", quality=93, optimize=True)
                png_tmp.unlink(missing_ok=True)
            except ImportError:
                png_tmp.rename(out_path)

            return out_path.exists() and out_path.stat().st_size > 5000

        finally:
            driver.quit()
            try:
                tmp_html.unlink()
            except Exception:
                pass

    except Exception as e:
        log.warning(f"[html_infographic] Selenium 오류: {e}")
        _g_report("image", e, module=__name__, func_name="_html_to_jpg")
        return False


# ══════════════════════════════════════════════════════════════════
#  6. 공개 API
# ══════════════════════════════════════════════════════════════════

def generate_html_infographic(
    theme: str,
    purpose: str = "",
    data: dict | None = None,
    run_id: str = "",
    slot_key: str = "",
    max_retries: int = 2,
    out_dir: str | Path | None = None,
) -> str:
    """
    HTML+CSS 기반 프리미엄 인포그래픽 생성.

    Returns:
        HTML <img> 문자열. 실패 시 빈 문자열.
    """
    from shared.llm import invoke_text

    _rid = run_id or hashlib.md5(
        f"{theme}|{purpose}|{time.time_ns()}".encode()
    ).hexdigest()[:16]
    _slot = slot_key or purpose[:12] or "infog"

    H, S, L = _dyn_hsl(theme, _rid)
    H2 = (H + 60) % 360
    S2, L2 = max(S - 5, 40), min(L + 10, 55)

    _data_str = _fmt_data(data)
    _purpose = purpose or f"{theme} 데이터 시각화"

    prompt = _PROMPT_TEMPLATE.format(
        theme=theme, purpose=_purpose,
        H=H, S=S, L=L, H2=H2, S2=S2, L2=L2,
        data_str=_data_str,
    )

    _out_dir = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    _out_dir.mkdir(parents=True, exist_ok=True)
    _out = _out_dir / f"html_infog_{_rid[:12]}_{_slot[:6]}.jpg"

    for attempt in range(max_retries):
        try:
            _prompt_a = prompt if attempt == 0 else (
                prompt + f"\n\n[재시도{attempt+1}: 원형 게이지·픽토그램 반드시 포함, 더 풍부하게]"
            )

            raw = invoke_text("writer", _prompt_a, timeout=300)
            if not raw:
                continue

            # Python 코드 블록 추출
            m = re.search(r'```python\s*\n([\s\S]*?)```', raw)
            if not m:
                m = re.search(r'```\s*\n([\s\S]*?)```', raw)
            code = m.group(1).strip() if m else ""
            if not code:
                # _RESULT_HTML 직접 정의된 경우
                if "_RESULT_HTML" in raw and "def build_infographic_html" in raw:
                    code = raw
                else:
                    log.debug(f"[html_infographic] 코드 블록 없음 (시도 {attempt+1})")
                    continue

            if not _is_safe(code):
                log.debug(f"[html_infographic] 안전검사 실패 (시도 {attempt+1})")
                continue

            html = _exec_and_extract_html(code)
            if not html:
                log.debug(f"[html_infographic] HTML 추출 실패 (시도 {attempt+1})")
                continue

            if _out.exists():
                _out.unlink(missing_ok=True)

            if _html_to_jpg(html, _out):
                log.info(f"[html_infographic] ✅ {theme}/{_slot} (시도 {attempt+1})")
                with open(_out, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                return _wrap_img(b64, f"{theme} — {_purpose}")

            log.debug(f"[html_infographic] Selenium 캡처 실패 (시도 {attempt+1})")

        except Exception as e:
            log.debug(f"[html_infographic] 시도 {attempt+1} 예외: {e}")
            _g_report("image", e, module=__name__, func_name="generate_html_infographic")

    log.warning(f"[html_infographic] {max_retries}회 실패: {theme}/{_slot}")
    return ""


__all__ = ["generate_html_infographic"]
