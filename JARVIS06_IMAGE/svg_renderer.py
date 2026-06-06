"""JARVIS06_IMAGE/svg_renderer.py — 설계서 기반 SVG 인포그래픽 렌더러.

kpi_cards / comparison_table / infographic / timeline / checklist /
scenario_cards / flow_diagram / highlight_card / insight_card 처리.

전략:
1. 설계서(spec) 전체를 Claude 프롬프트에 담아 완성도 높은 SVG 생성
2. cairosvg로 PNG 변환 (실패 시 SVG 파일 그대로 반환)
3. 내용 잘림 방지: 설계서 기반이므로 데이터 100% 반영
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Any

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# ── 공통 색상 (다크 테마 통일) ────────────────────────────────────────
_COLORS = {
    "bg":       "#0d1117",
    "card":     "#161b22",
    "border":   "#30363d",
    "text":     "#e6edf3",
    "text2":    "#8b949e",
    "primary":  "#58a6ff",
    "success":  "#3fb950",
    "warning":  "#e3b341",
    "danger":   "#f85149",
    "purple":   "#a371f7",
    "highlight":"#f0b429",
}

# ── SVG 생성 시스템 프롬프트 ──────────────────────────────────────────
_SVG_SYSTEM = """당신은 전문 SVG 인포그래픽 디자이너입니다.
주어진 데이터 설계서를 바탕으로 블로그에 삽입할 고품질 SVG 인포그래픽을 생성합니다.

디자인 원칙:
1. 배경: #0d1117 (다크), 카드: #161b22, 테두리: #30363d
2. 본문 텍스트: #e6edf3, 보조 텍스트: #8b949e
3. 강조: #f0b429 (amber), 성공: #3fb950, 위험: #f85149, 주요: #58a6ff
4. 폰트: 'Apple SD Gothic Neo', 'NanumGothic', sans-serif
5. 모든 텍스트가 잘리지 않도록 충분한 여백
6. 그림자: drop-shadow(0 2px 8px rgba(0,0,0,0.4))
7. 반드시 viewBox="0 0 900 600" 또는 내용에 맞게 높이 조정
8. 설계서의 모든 데이터를 빠짐없이 표시

출력: 완성된 SVG 코드만. <svg ...> 태그로 시작해서 </svg>로 끝납니다."""

# ── 타입별 SVG 생성 프롬프트 ─────────────────────────────────────────
_PROMPTS: dict[str, str] = {

"kpi_cards": """다음 설계서로 KPI 카드 인포그래픽 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 상단 제목 바 (키워드 + 핵심 메시지)
- data 배열의 각 항목을 카드 그리드로 배치 (최대 4개, 2x2 또는 1x4)
- 각 카드: 큰 숫자(value+unit, 56px, 강조색), 라벨(22px, 보조색), 카드 배경 #161b22
- highlight=true 항목은 amber 테두리 + 배경 강조
- 하단 key_message 텍스트 박스
- viewBox 높이는 데이터 수에 따라 조정""",

"comparison_table": """다음 설계서로 좌우 비교 인포그래픽 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 상단 제목 바
- 좌측 패널 (left_label, left_items): 초록 테마 (#3fb950)
- 우측 패널 (right_label, right_items): 빨강 테마 (#f85149)
- 각 항목은 아이콘(✓/✕) + 텍스트, 텍스트 줄바꿈 지원
- items가 없으면 data 배열을 반반 분할해서 표시
- 하단 key_message
- 충분한 높이 (항목 수에 비례)""",

"infographic": """다음 설계서로 데이터 인포그래픽 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 제목 + 서브타이틀 상단 배치
- data 배열: 수평 막대 + 수치 레이블로 표현 (Plotly 없이 SVG로 직접 그리기)
- 막대 길이는 최댓값 대비 비율로 계산
- 각 항목 라벨 + 값 + 단위 모두 표시
- highlight 항목은 amber 색상
- key_message 박스
- design_notes 있으면 각주로 추가""",

"timeline": """다음 설계서로 타임라인 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 세로 또는 가로 타임라인
- items 또는 data 배열의 각 항목을 시간 순서대로 표시
- 각 노드: 원형 마커 + 제목 + 설명 텍스트
- 연결선 (점선 또는 실선)
- 현재/강조 노드는 크게 + amber 색상
- 충분한 세로 높이""",

"checklist": """다음 설계서로 체크리스트 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 제목 + 서브타이틀
- items 또는 data 배열 각 항목을 체크리스트 형태로
- 각 항목: 체크 아이콘(✓) + 텍스트 (줄바꿈 지원, 텍스트 잘림 금지)
- highlight 항목은 amber 배경 + 별표 강조
- 번호 또는 카테고리 그룹핑 (있는 경우)
- key_message 하단 배치""",

"scenario_cards": """다음 설계서로 시나리오 카드 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 제목 상단
- items 또는 data 배열 각 항목을 독립 카드로 (가로 배치 또는 세로 배치)
- 각 카드: 시나리오 번호/이름 + 내용 + 수치 (있는 경우)
- 긍정 시나리오: 초록 테두리, 부정: 빨강, 중립: 파랑
- key_message 박스""",

"flow_diagram": """다음 설계서로 흐름도 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 제목 상단
- items 또는 data 배열을 순서 흐름으로 표현
- 각 단계: 둥근 박스 + 텍스트 + 화살표
- 주요 단계는 amber 강조
- 최대 6단계 권장
- key_message 하단""",

"highlight_card": """다음 설계서로 핵심 인용 카드 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 대형 인용 스타일 (왼쪽 굵은 세로선 + 텍스트)
- key_message 또는 items[0]을 크게 (28~32px) 중앙 배치
- 배경 그라디언트 (subtle) + 배경 패턴
- 하단 출처/부제 (작은 텍스트)
- viewBox 높이 400 이하 (콤팩트 카드)""",

"insight_card": """다음 설계서로 인사이트 카드 SVG를 만드세요.
설계서: {spec_json}

요구사항:
- 상단 레이블 (예: "📊 핵심 인사이트")
- key_message 크게 표시 (24px, #e6edf3)
- data 있으면 작은 KPI 숫자들 하단 배치
- 좌측 amber 세로 강조선
- 깔끔하고 미니멀한 디자인
- viewBox 높이 350~450""",

"section_banner": """블로그 섹션 구분 배너 SVG를 만드세요.

정보:
{spec_json}

디자인 요구사항:
- viewBox="0 0 900 280" 고정 (가로형 배너)
- 좌측 패널(0~280px): 배경 #0D1B2A
  - 상단 amber(#F0B429) 가로선 8px
  - "S E C T I O N" 텍스트 (14px, amber, 자간 넓게)
  - 섹션 번호 2자리(예: 01) 대형 흰색(60px, bold)
  - 하단 키워드 텍스트 (14px, amber, bold)
- 중앙 구분선: x=286, 전체 높이, amber 6px
- 우측 패널(286~900px): 배경 #F8F9FC
  - 섹터 태그: 둥근 박스(amber 배경 15% opacity, amber 테두리), 16px bold amber
  - 섹션 제목: 굵게(bold), 제목 길이에 따라 30~38px, 색상 #0D1B2A
    (10자 이하 38px / 14자 이하 34px / 20자 이하 30px / 초과 26px)
  - 제목 아래 amber 밑줄 accent (높이 6px)
  - 하단 날짜 + "· 트렌드 분석" 텍스트 (14px, #4A5568)
- 폰트: 'Apple SD Gothic Neo', 'NanumGothic', 'Malgun Gothic', sans-serif
- 우측 하단 amber 반투명 가로선 accent
- 텍스트 잘림 없도록 충분한 여백

출력: 완성된 SVG 코드만. <svg> 태그로 시작, </svg>로 끝.""",

"content_infographic": """블로그 본문 삽입용 콘텐츠 인포그래픽 SVG를 만드세요.

정보:
{spec_json}

디자인 요구사항:
- viewBox="0 0 900 500" (내용에 따라 높이 조정 가능, 최대 700)
- 다크 테마: 배경 #0d1117, 카드 #161b22, 테두리 #30363d
- 텍스트: #e6edf3 (본문), #8b949e (보조), #f0b429 (강조)
- 상단 제목 바: 키워드/섹션 제목 + 핵심 메시지
- 본문 데이터를 가장 적합한 형태로 시각화:
  * 수치 비교 → 수평 바 차트
  * 순서/흐름 → 타임라인 또는 단계 카드
  * 핵심 포인트 → KPI 카드 그리드
  * 체크리스트 → 아이콘 + 텍스트 리스트
- data 배열의 모든 항목 빠짐없이 표시
- highlight=true 항목 amber 강조
- 하단 key_message 박스
- 폰트: 'Apple SD Gothic Neo', 'NanumGothic', sans-serif
- 그림자: drop-shadow(0 2px 8px rgba(0,0,0,0.4))

출력: 완성된 SVG 코드만. <svg> 태그로 시작, </svg>로 끝.""",
}

# fallback 프롬프트 (타입 매핑 없을 때)
_FALLBACK_PROMPT = _PROMPTS["infographic"]


def render(spec: dict[str, Any], out_path: Path) -> Path:
    """설계서 → SVG/PNG 파일 생성.

    Args:
        spec:     generate_image_spec() 반환 설계서
        out_path: 저장 경로 (.png 또는 .svg)

    Returns:
        생성된 이미지 Path (PNG 우선, 실패 시 SVG).
    """
    viz   = spec.get("viz_type", "infographic")
    title = spec.get("title", "")
    log.info(f"[svg_renderer] '{viz}' 생성 시작: '{title}'")

    # 설계서 JSON 직렬화 (프롬프트에 삽입)
    spec_json = json.dumps(spec, ensure_ascii=False, indent=2)

    # 타입별 프롬프트 선택
    prompt_template = _PROMPTS.get(viz, _FALLBACK_PROMPT)
    prompt = prompt_template.format(spec_json=spec_json)

    svg_code = _generate_svg(prompt)
    if not svg_code:
        log.warning(f"[svg_renderer] SVG 생성 실패, 기본 SVG 생성")
        svg_code = _make_fallback_svg(spec)

    # 파일 저장
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    svg_path = out_path.with_suffix(".svg")
    svg_path.write_text(svg_code, encoding="utf-8")

    # PNG 변환 시도
    png_path = out_path.with_suffix(".png")
    converted = _svg_to_png(svg_path, png_path)
    if converted:
        log.info(f"[svg_renderer] ✅ PNG 변환 완료: {png_path.name}")
        return png_path

    # PNG 변환 실패 → SVG 파일 반환
    log.info(f"[svg_renderer] ✅ SVG 반환: {svg_path.name}")
    return svg_path


def _generate_svg(prompt: str) -> str:
    """Claude LLM으로 SVG 코드 생성."""
    try:
        from shared.llm import invoke_text as _inv
        raw = _inv("analyzer", prompt, system=_SVG_SYSTEM, max_tokens=3000, temperature=0.3)
        if not raw:
            return ""
        # SVG 블록 추출
        m = re.search(r'<svg[\s\S]*?</svg>', raw, re.IGNORECASE)
        if m:
            return m.group(0)
        # 전체가 SVG인 경우
        stripped = raw.strip()
        if stripped.startswith("<svg"):
            return stripped
        return ""
    except Exception as e:
        log.warning(f"[svg_renderer] LLM SVG 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def _svg_to_png(svg_path: Path, png_path: Path) -> bool:
    """cairosvg로 SVG → PNG 변환."""
    try:
        import cairosvg
        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=1200,
            dpi=150,
        )
        return png_path.exists()
    except ImportError:
        pass
    except Exception as e:
        log.warning(f"[svg_renderer] cairosvg 변환 실패: {e}")
        _g_report("image", e, module=__name__)

    # Pillow + cairosvg 없을 때 rsvg-convert 시도
    try:
        import subprocess
        result = subprocess.run(
            ["rsvg-convert", "-w", "1200", "-o", str(png_path), str(svg_path)],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0 and png_path.exists()
    except Exception:
        pass

    return False


def _make_fallback_svg(spec: dict) -> str:
    """LLM 실패 시 규칙 기반 기본 SVG 생성 (데이터 수평 바차트)."""
    title   = spec.get("title", "데이터 분석")
    km      = spec.get("key_message", "")
    data    = spec.get("data") or []
    keyword = spec.get("keyword", "")

    C  = _COLORS
    W  = 900
    row_h = 70
    pad   = 60
    H  = pad + 80 + len(data) * row_h + (60 if km else 0) + pad

    maxval = max((d.get("value", 0) for d in data), default=1) or 1
    bar_max_w = W - 320

    rows_svg = ""
    for i, d in enumerate(data):
        y      = pad + 80 + i * row_h
        val    = d.get("value", 0)
        unit   = d.get("unit", "")
        label  = d.get("label", f"항목{i+1}")
        bw     = max(8, int(val / maxval * bar_max_w))
        color  = C["highlight"] if d.get("highlight") else C["primary"]
        val_str = f"{int(val):,}" if val == int(val) else f"{val:,.1f}"
        rows_svg += f"""
  <text x="140" y="{y+20}" text-anchor="end" font-size="18" fill="{C['text2']}"
        font-family="'Apple SD Gothic Neo','NanumGothic',sans-serif">{label}</text>
  <rect x="150" y="{y}" width="{bw}" height="34" rx="5"
        fill="{color}" opacity="0.85"/>
  <text x="{150+bw+10}" y="{y+22}" font-size="18" fill="{C['text']}"
        font-family="'Apple SD Gothic Neo','NanumGothic',sans-serif"
        font-weight="bold">{val_str}{unit}</text>"""

    km_svg = ""
    if km:
        km_y = H - pad - 10
        km_svg = f"""
  <rect x="30" y="{km_y - 32}" width="{W-60}" height="44" rx="8"
        fill="{C['highlight']}18" stroke="{C['highlight']}" stroke-width="1"/>
  <text x="{W//2}" y="{km_y}" text-anchor="middle" font-size="17"
        fill="{C['highlight']}"
        font-family="'Apple SD Gothic Neo','NanumGothic',sans-serif">💡 {km}</text>"""

    return f"""<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
  <rect width="{W}" height="{H}" fill="{C['bg']}"/>
  <rect width="{W}" height="56" fill="{C['card']}"/>
  <line x1="0" y1="56" x2="{W}" y2="56" stroke="{C['primary']}" stroke-width="2"/>
  <text x="{W//2}" y="36" text-anchor="middle" font-size="24" font-weight="bold"
        fill="{C['text']}" font-family="'Apple SD Gothic Neo','NanumGothic',sans-serif"
        >{title}</text>
  {rows_svg}
  {km_svg}
</svg>"""
