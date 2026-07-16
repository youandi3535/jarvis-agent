"""JARVIS06_IMAGE/html_screenshotter.py
HTML 파일 → 이미지 캡처 → JPG 파일 목록 반환.

공개 함수:
    screenshot_visual_blocks(html_path, out_dir) → list[str]   # 레거시: div.jarvis-visual 스크린샷
    screenshot_svg_blocks(html_content, out_dir) → list[str]   # 1-pass: inline SVG → JPG
"""
from __future__ import annotations

import re
import time
import tempfile
from pathlib import Path


# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

def screenshot_visual_blocks(html_path: str, out_dir: str) -> list:
    """
    HTML 파일을 headless Chrome으로 로드 후 div.jarvis-visual 요소를 개별 스크린샷.

    Args:
        html_path: 입력 HTML 파일 절대 경로
        out_dir:   JPG 저장 폴더 경로

    Returns:
        list[str]: 생성된 JPG(또는 PNG) 경로 목록 (삽입 순서)
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    html_path = Path(html_path).resolve()
    out_dir   = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not html_path.exists():
        print(f"  ❌ [스크린샷] HTML 파일 없음: {html_path}")
        return []

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=900,5000")
    options.add_argument("--force-device-scale-factor=2")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--disable-extensions")
    options.add_argument("--lang=ko-KR")

    driver = webdriver.Chrome(options=options)
    paths: list[str] = []

    try:
        driver.get(f"file://{html_path}")

        # 폰트·레이아웃 렌더 대기
        time.sleep(2.5)

        # .jarvis-visual 요소 탐색
        elements = driver.find_elements(By.CSS_SELECTOR, "div.jarvis-visual")
        if not elements:
            print(f"  ⚠️ [스크린샷] div.jarvis-visual 없음 — HTML 구조 확인 필요")
            return []

        print(f"  🖥️  [스크린샷] {len(elements)}개 visual 블록 캡처...")

        for i, el in enumerate(elements, 1):
            label = (el.get_attribute("data-label") or f"block{i:02d}")
            safe  = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:30]
            png_tmp = out_dir / f"visual_{i:02d}_{safe}.png"
            jpg_out = out_dir / f"visual_{i:02d}_{safe}.jpg"

            # 요소를 뷰포트 안으로 스크롤
            driver.execute_script("arguments[0].scrollIntoView(true);", el)
            time.sleep(0.3)

            # 요소 스크린샷 (PNG)
            try:
                el.screenshot(str(png_tmp))
            except Exception as e:
                print(f"  ⚠️ visual {i:02d} 스크린샷 실패: {e}")
                _g_report("image", e, module=__name__)
                continue

            if not png_tmp.exists() or png_tmp.stat().st_size < 500:
                print(f"  ⚠️ visual {i:02d}: 캡처 크기 미달")
                png_tmp.unlink(missing_ok=True)
                continue

            # PNG → JPG 변환 (용량 절감)
            try:
                from PIL import Image
                img = Image.open(png_tmp)
                img.convert("RGB").save(jpg_out, "JPEG", quality=92, optimize=True)
                png_tmp.unlink(missing_ok=True)
                target = jpg_out
            except Exception:
                # PIL 없으면 PNG 그대로 사용
                target = png_tmp

            size_kb = target.stat().st_size // 1024
            print(f"  ✅ visual {i:02d}: {target.name} ({size_kb}KB)")
            paths.append(str(target))

    except Exception as e:
        import traceback
        print(f"  ❌ [스크린샷] 오류: {e}")
        _g_report("image", e, module=__name__)
        traceback.print_exc()
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"  ✅ [스크린샷] 완료 — {len(paths)}개 이미지 저장")
    return paths


# ──────────────────────────────────────────────────────────────
#  신규: 1-pass inline SVG → JPG 캡처 (JARVIS06 관리)
# ──────────────────────────────────────────────────────────────

def _escape_svg_ampersand(svg: str) -> str:
    """SVG 텍스트 내 bare & → &amp; 이스케이프 (XML well-formed 보장)."""
    return re.sub(r'&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', svg)


def _dedupe_svg_attrs(svg: str) -> str:
    """LLM 생성 SVG 중복 속성 제거 — ParseError: duplicate attribute 차단.

    thumbnail_maker.py 와 동일 로직. 여는 태그 단위로 파싱 후 동일 키 첫 번째 값 유지.
    """
    _TAG_RE  = re.compile(r'<([a-zA-Z][\w-]*)([^>]*?)(/?)>', re.DOTALL)
    _ATTR_RE = re.compile(r'\s+([\w:][\w:.-]*)\s*=\s*(["\'])(.*?)\2', re.DOTALL)

    def _fix(m):
        name, raw_attrs, slash = m.group(1), m.group(2), m.group(3)
        if not raw_attrs or '=' not in raw_attrs:
            return m.group(0)
        seen: dict = {}
        order: list = []
        for am in _ATTR_RE.finditer(raw_attrs):
            k = am.group(1)
            if k not in seen:
                order.append(k)
            seen[k] = (am.group(2), am.group(3))
        if not order:
            return m.group(0)
        rebuilt = "".join(f' {k}={q}{v}{q}' for k, (q, v) in ((kk, seen[kk]) for kk in order))
        return f'<{name}{rebuilt}{"/" if slash else ""}>'

    return _TAG_RE.sub(_fix, svg)


def _expand_svg_viewbox(svg: str, pad: int = 30) -> str:
    """★ 차트 짤림 차단 (사용자 박제 2026-05-15) — viewBox 경계 padding 확장.

    LLM 이 viewBox 끝까지 텍스트를 박으면 cairosvg 가 가장자리 라벨 잘라냄.
    Python 단에서 viewBox 의 x/y/w/h 에 pad 만큼 여유 추가 → 잘림 방지.
    """
    import re as _r
    m = _r.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', svg)
    if m:
        parts = m.group(1).replace(',', ' ').split()
        if len(parts) == 4:
            try:
                x, y, w, h = (float(p) for p in parts)
                new_vb = f'{x - pad} {y - pad} {w + pad * 2} {h + pad * 2}'
                svg = svg.replace(m.group(0), f'viewBox="{new_vb}"')
            except ValueError:
                pass
    else:
        # viewBox 없으면 width/height 기준 추가
        wm = _r.search(r'<svg[^>]*\swidth\s*=\s*["\']([\d.]+)', svg)
        hm = _r.search(r'<svg[^>]*\sheight\s*=\s*["\']([\d.]+)', svg)
        if wm and hm:
            try:
                w, h = float(wm.group(1)), float(hm.group(1))
                vb = f'viewBox="{-pad} {-pad} {w + pad * 2} {h + pad * 2}"'
                svg = _r.sub(r'<svg', f'<svg {vb}', svg, count=1)
            except ValueError:
                pass
    return svg


def _svg_to_jpg_cairosvg(svg_str: str, out_path: Path) -> bool:
    """cairosvg로 SVG → PNG → JPG 변환. 성공 시 True."""
    try:
        import cairosvg
        svg_str = _escape_svg_ampersand(svg_str)
        # ★ LLM 중복 속성 제거 (사용자 박제 2026-05-17) — ParseError: duplicate attribute 차단
        svg_str = _dedupe_svg_attrs(svg_str)
        # ★ viewBox padding 확장 (30→50) — 차트 텍스트·라벨 잘림 차단
        svg_str = _expand_svg_viewbox(svg_str, pad=50)
        # ★ viewBox 확장 후 width/height 제거 — cairosvg 가 viewBox 기준으로 출력 크기 결정
        # width/height 를 그대로 두면 확장된 viewBox 와 불일치 → 비율 왜곡·클리핑 발생.
        svg_str = re.sub(r'(<svg\b[^>]*?)\s+width\s*=\s*"[^"]*"', r'\1', svg_str, flags=re.IGNORECASE)
        svg_str = re.sub(r'(<svg\b[^>]*?)\s+height\s*=\s*"[^"]*"', r'\1', svg_str, flags=re.IGNORECASE)
        svg_str = re.sub(r"(<svg\b[^>]*?)\s+width\s*=\s*'[^']*'", r'\1', svg_str, flags=re.IGNORECASE)
        svg_str = re.sub(r"(<svg\b[^>]*?)\s+height\s*=\s*'[^']*'", r'\1', svg_str, flags=re.IGNORECASE)
        png_bytes = cairosvg.svg2png(bytestring=svg_str.encode("utf-8"), scale=2.0)
        if not png_bytes:
            return False
        png_tmp = out_path.with_suffix(".png")
        png_tmp.write_bytes(png_bytes)
        try:
            from PIL import Image
            img = Image.open(png_tmp)
            img.convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
            png_tmp.unlink(missing_ok=True)
        except ImportError:
            # PIL 없으면 PNG 그대로
            png_tmp.rename(out_path.with_suffix(".png"))
            out_path.with_suffix(".png").rename(out_path)
        return out_path.exists() and out_path.stat().st_size > 500
    except Exception as e:
        print(f"  ⚠️ cairosvg 변환 실패: {e}")
        _g_report("image", e, module=__name__)
        return False


def _svg_to_jpg_selenium(svg_str: str, out_path: Path) -> bool:
    """Selenium headless Chrome으로 SVG → JPG 캡처 (폴백). 성공 시 True."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        # SVG를 독립 HTML로 래핑
        # viewBox에서 width/height 파싱
        vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg_str)
        w, h = (int(vb.group(1)), int(vb.group(2))) if vb else (800, 280)

        html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
* {{margin:0;padding:0;background:#ffffff;}}
svg {{display:block;width:{w}px;height:{h}px;}}
text, tspan {{font-family:"Apple SD Gothic Neo","Malgun Gothic","NanumGothic","Noto Sans KR",sans-serif !important;}}
</style>
</head><body>{svg_str}</body></html>"""

        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", encoding="utf-8", delete=False) as f:
            f.write(html)
            tmp_html = Path(f.name)

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--force-device-scale-factor=2")
        options.add_argument("--hide-scrollbars")
        options.add_argument("--lang=ko-KR")

        driver = webdriver.Chrome(options=options)
        try:
            driver.get(f"file://{tmp_html}")
            time.sleep(1.5)
            # 실제 콘텐츠 BBox로 viewBox 자동 확장 — 클리핑 방지
            driver.execute_script("""
                var svg = document.querySelector('svg');
                if (!svg) return;
                try {
                    var bbox = svg.getBBox();
                    var pad = 24;
                    var vx = Math.floor(bbox.x - pad);
                    var vy = Math.floor(bbox.y - pad);
                    var vw = Math.ceil(bbox.width + pad * 2);
                    var vh = Math.ceil(bbox.height + pad * 2);
                    svg.setAttribute('viewBox', vx + ' ' + vy + ' ' + vw + ' ' + vh);
                    svg.setAttribute('width', vw);
                    svg.setAttribute('height', vh);
                } catch(e) {}
            """)
            time.sleep(0.3)
            from selenium.webdriver.common.by import By
            el = driver.find_element(By.TAG_NAME, "svg")
            png_tmp = out_path.with_suffix(".png")
            el.screenshot(str(png_tmp))
            if png_tmp.exists() and png_tmp.stat().st_size > 500:
                try:
                    from PIL import Image
                    Image.open(png_tmp).convert("RGB").save(out_path, "JPEG", quality=92, optimize=True)
                    png_tmp.unlink(missing_ok=True)
                except ImportError:
                    png_tmp.rename(out_path)
                return out_path.exists()
        finally:
            driver.quit()
            tmp_html.unlink(missing_ok=True)
    except Exception as e:
        print(f"  ⚠️ Selenium SVG 캡처 실패: {e}")
        _g_report("image", e, module=__name__)
    return False


def screenshot_svg_blocks(html_content: str, out_dir: str) -> list:
    """1-pass Claude Code SDK HTML에서 inline SVG를 추출 → JPG 파일로 저장.

    각 <svg> 블록을 cairosvg(1순위) → Selenium headless(폴백) 으로 캡처.
    JARVIS06이 관리하는 output/images/{slug}/ 폴더에 저장.

    Args:
        html_content: generate_article_html() 반환 HTML 문자열
        out_dir:      JPG 저장 폴더 경로 (save_article_html이 생성한 img_dir)

    Returns:
        list[str]: 저장된 JPG 경로 목록 (순서 = HTML내 SVG 출현 순서)
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # HTML에서 <svg ...>...</svg> 블록 전체 추출 (중첩 미지원, 단순 greedy)
    svg_blocks = re.findall(
        r"(<svg[\s\S]*?</svg>)",
        html_content,
        re.IGNORECASE,
    )

    if not svg_blocks:
        print("  ℹ️ [JARVIS06] 인라인 SVG 0개 — 캡처 대상 없음 (슬롯 렌더 경로에선 정상)")
        return []

    print(f"  🖼️  [JARVIS06] inline SVG {len(svg_blocks)}개 캡처 시작...")

    from concurrent.futures import ThreadPoolExecutor as _SvgEx, as_completed as _svg_ac

    def _capture_one(args: tuple) -> tuple[int, str | None]:
        _i, _svg = args
        _out = out_dir / f"svg_{_i:02d}.jpg"
        _ok = _svg_to_jpg_cairosvg(_svg, _out)
        if not _ok:
            print(f"  🔄 svg_{_i:02d}: cairosvg 실패 → Selenium 폴백")
            _ok = _svg_to_jpg_selenium(_svg, _out)
        if _ok:
            _kb = _out.stat().st_size // 1024
            print(f"  ✅ svg_{_i:02d}.jpg ({_kb}KB)")
            return _i, str(_out)
        print(f"  ❌ svg_{_i:02d}: 캡처 완전 실패 — 건너뜀")
        return _i, None

    _results: dict[int, str | None] = {}
    _workers = min(4, len(svg_blocks))
    with _SvgEx(max_workers=_workers) as _ex:
        _futs = {_ex.submit(_capture_one, (i, s)): i for i, s in enumerate(svg_blocks, 1)}
        for _f in _svg_ac(_futs):
            try:
                _idx, _p = _f.result()
                _results[_idx] = _p
            except Exception as _ce:
                print(f"  ⚠️ SVG 캡처 스레드 오류: {_ce}")
                _g_report("image", _ce, module=__name__)

    paths = [_results[i] for i in sorted(_results) if _results.get(i)]
    print(f"  ✅ [JARVIS06] SVG 캡처 완료 — {len(paths)}/{len(svg_blocks)}개 성공")
    return paths
