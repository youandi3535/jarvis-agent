"""JARVIS06_IMAGE/providers/claude_svg_provider.py -- Claude SVG 차트 생성."""
from __future__ import annotations
import json, logging, re
from pathlib import Path
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# 한국어 폰트 후보 (cairosvg @font-face file URL)
_KOR_FONT_CANDIDATES = [
    '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
    '/System/Library/Fonts/AppleSDGothicNeo.ttc',
    '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
    '/opt/homebrew/share/fonts/noto-cjk/NotoSansCJKkr-Regular.otf',
]


def _find_kor_font() -> str | None:
    for p in _KOR_FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


_KOR_FONT_NAME = "Apple SD Gothic Neo"  # fontconfig에 등록된 한국어 폰트


def _sanitize_svg(svg: str) -> str:
    """LLM 생성 SVG XML 정제 + 한국어 폰트 교체."""
    # 마크다운 코드펜스 제거
    svg = re.sub(r'^```[a-z]*\n?', '', svg.strip(), flags=re.MULTILINE)
    svg = re.sub(r'\n?```$', '', svg.strip(), flags=re.MULTILINE)
    svg = svg.strip()

    # XML 주석 제거
    svg = re.sub(r'<!--[\s\S]*?-->', '', svg)

    # ① curly quote / 스마트 따옴표 → ASCII 정규화 (XML 1.0 불허)
    svg = svg.replace('‘', "'").replace('’', "'")  # left/right single curly quote
    svg = svg.replace('“', '"').replace('”', '"')  # left/right double curly quote
    svg = svg.replace('–', '-').replace('—', '--') # en dash / em dash

    # ② font-family 먼저 교체 (한글 속성 제거 전에)
    kor = _KOR_FONT_NAME
    svg = re.sub(r"font-family\s*=\s*'[^']*'", "font-family='" + kor + "'", svg)
    svg = re.sub(r'font-family\s*=\s*"[^"]*"', 'font-family="' + kor + '"', svg)
    svg = re.sub(r"font-family\s*:\s*[^;\"'<\}]+[;\"'<\}]",
                 lambda m: "font-family:'" + kor + "'" + m.group(0)[-1], svg)

    # ③ font-family 없는 <text>/<tspan> 에 폰트 강제 주입
    def _inject_font(m):
        tag = m.group(0)
        if 'font-family' not in tag:
            tag = tag.rstrip('>') + " font-family=\"" + kor + "\">"
        return tag
    svg = re.sub(r'<(?:text|tspan)\b[^>]*>', _inject_font, svg)

    # ④ 속성값의 한글 제거
    def _strip_korean_attr(m):
        if re.search(r'[가-힣]', m.group(2)):
            return ''
        return m.group(0)
    svg = re.sub(r'\s+([\w:-]+)="([^"]*)"', _strip_korean_attr, svg)

    # ⑤ <text>/<tspan> 순수 텍스트 → CDATA 래핑
    def _wrap_text_cdata(m):
        open_tag  = m.group(1)
        content   = m.group(2)
        close_tag = m.group(3)
        if '<![CDATA[' in content or '<' in content:
            return m.group(0)
        if re.search(r'[가-힣]', content):
            content = '<![CDATA[' + content + ']]>'
        return open_tag + content + close_tag
    svg = re.sub(
        r'(<(?:text|tspan)\b[^>]*>)([^<]*)(</(?:text|tspan)>)',
        _wrap_text_cdata, svg
    )

    # ⑥ CDATA 밖의 & 이스케이프
    parts = re.split(r'(<!\[CDATA\[.*?\]\]>)', svg, flags=re.DOTALL)
    fixed = []
    for part in parts:
        if part.startswith('<![CDATA['):
            fixed.append(part)
        else:
            part = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', part)
            fixed.append(part)
    svg = ''.join(fixed)

    return svg


class ClaudeSVGProvider:
    PROVIDER_ID = "claude_svg"

    def generate(self, data: dict[str, Any], chart_type: str,
                 title: str, out_dir: Path, width: int = 800, height: int = 500) -> Path:
        """Claude LLM으로 SVG 차트 생성 → PNG 변환 후 경로 반환.

        chart_type: bar | line | pie | radar | table | custom
        data: {"labels": [...], "values": [...], ...}
        """
        from shared.llm import invoke_text
        out_dir.mkdir(parents=True, exist_ok=True)

        # KPI 카드(stats 구조) vs 일반 차트 프롬프트 분기
        if "stats" in data:
            prompt = (
                f"다음 KPI 카드 데이터로 인포그래픽 SVG를 생성하세요.\n"
                f"제목: {title}\n"
                f"크기: {width}x{height}px\n"
                f"데이터: {json.dumps(data, ensure_ascii=False)}\n\n"
                "규칙:\n"
                "1. <svg> 태그만 출력 (다른 텍스트 없음)\n"
                f"2. viewBox='0 0 {width} {height}' 포함, font-family='sans-serif'\n"
                "3. 배경 #0D1B2E, 카드 배경 #162240, 강조색 #2563EB(파랑)\n"
                "4. 2×2 그리드로 stats 배열 각 항목을 KPI 카드로 배치\n"
                "5. 각 카드: val+unit 을 큰 숫자(font-size 최소 64px bold 파랑), 바로 아래 label(font-size 최소 32px 회색)\n"
                "6. 상단 제목(font-size 최소 38px bold 흰색)\n"
                "7. 모든 텍스트 최소 28px 이상 -- 14px 이하 절대 금지\n"
                "8. 텍스트 내 특수문자(&, <, >) 는 반드시 XML 이스케이프(&amp; &lt; &gt;) 처리\n"
            )
        else:
            prompt = (
                f"다음 데이터로 {chart_type} 차트 SVG를 생성하세요.\n"
                f"제목: {title}\n"
                f"크기: {width}x{height}px\n"
                f"데이터: {json.dumps(data, ensure_ascii=False)}\n\n"
                "규칙:\n"
                "1. <svg> 태그만 출력 (다른 텍스트 없음)\n"
                "2. viewBox 포함, font-family='sans-serif'\n"
                "3. 색상은 파랑(#2563EB) 계열 위주, 모든 텍스트 최소 28px\n"
                "4. 범례 포함\n"
                "5. 텍스트 내 특수문자(&, <, >) 는 반드시 XML 이스케이프(&amp; &lt; &gt;) 처리\n"
            )
        svg_text = invoke_text("writer_fast", prompt, max_tokens=4000, temperature=0.3)
        if not svg_text:
            raise RuntimeError("Claude SVG 생성 실패 (빈 응답)")

        # SVG 추출 + 정제 + 한국어 폰트 주입
        m = re.search(r"(<svg[\s\S]*?</svg>)", svg_text, re.IGNORECASE)
        svg = _sanitize_svg(m.group(1) if m else svg_text)

        # 데이터 내용 기반 해시 → 섹션마다 다른 데이터면 반드시 다른 파일
        import hashlib as _hl
        _data_sig = _hl.md5(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:10]
        fname_base = f"svg_{chart_type}_{abs(hash(title)) & 0xFFFFFF:06x}_{_data_sig}"
        svg_path = out_dir / f"{fname_base}.svg"
        svg_path.write_text(svg, encoding="utf-8")

        # PNG 변환 (cairosvg)
        try:
            import cairosvg  # type: ignore
            png_path = out_dir / f"{fname_base}.png"
            cairosvg.svg2png(url=str(svg_path), write_to=str(png_path),
                             output_width=width, output_height=height)
            svg_path.unlink(missing_ok=True)  # PNG 변환 성공 → SVG 중간 파일 삭제
            log.info(f"[ClaudeSVG] PNG 변환 완료: {png_path}")
            return png_path
        except ImportError:
            log.info("[ClaudeSVG] cairosvg 없음 -- SVG 반환")
            return svg_path
        except Exception as e:
            log.warning(f"[ClaudeSVG] PNG 변환 실패: {e} -- SVG 반환")
            _g_report("image", e, module=__name__)
            return svg_path


__all__ = ["ClaudeSVGProvider"]
