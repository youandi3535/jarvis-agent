"""JARVIS06_IMAGE/injectors/block_assembler.py — 1-pass HTML → 이미지 블록 조립.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — tistory_html_writer.assemble_blocks 이관.

원래 위치: JARVIS02_WRITER/tistory_html_writer.py:749
이관 일자: 2026-05-17
이관 사유: 이미지 도메인 단일 진입점 (ADR 008)

assemble_blocks 는 HTML body 에서 p/svg/h2 를 순서대로 파싱하여 *이미지 블록* 으로 치환하는
*이미지 도메인* 의 핵심 조립기. visual_paths (JARVIS06 관리 JPG) 와 HTML 의 SVG 위치를 매핑.
"""
from __future__ import annotations

import re


def assemble_blocks(html: str, visual_paths: list, out_dir=None) -> list:
    """1-pass HTML + JARVIS06 관리 JPG 경로 → post_to_tistory blocks 조립.

    HTML body에서 p/svg/h2를 순서대로 파싱:
    - p     → text 블록
    - svg   → image 블록 (visual_paths에서 순서대로 치환). 소진 시 1줄 여백.
    - h2    → 2줄 여백 + h2 text 블록 (제9조)
    - table → image 블록 (render_html_table_as_image 변환; 실패 시 text 폴백)

    제4조: p→jpg→p→jpg→p 교차 — SVG 위치가 이미지 슬롯이므로 자동 보장.
    제9조: h2 앞 2줄 여백(<p>&nbsp;</p><p>&nbsp;</p>) 자동 삽입.
    제0조: 첫 <p>(감성 오프닝)이 항상 첫 블록 — svg가 항상 p 뒤에 오므로 자동.

    Args:
        out_dir: 표 이미지 저장 폴더 (None 시 JARVIS06_IMAGE/output/ 기본값 사용)

    Returns:
        list[tuple]: [('image', path) | ('text', html), ...]
    """
    body_m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    inner = body_m.group(1) if body_m else html

    # p, h1~h6, svg, figure, table 태그를 문서 순서대로 추출
    # ★ ERRORS [170] 2026-05-26: figure·table 누락 → 연속 이미지 발생 — 추가
    elements = re.findall(
        r"(<svg[\s\S]*?</svg>"
        r"|<figure[^>]*>[\s\S]*?</figure>"
        r"|<table[^>]*>[\s\S]*?</table>"
        r"|<h[1-6][^>]*>[\s\S]*?</h[1-6]>"
        r"|<p[^>]*>[\s\S]*?</p>)",
        inner,
        re.IGNORECASE,
    )

    blocks: list = []
    img_idx = 0
    table_idx = 0

    for elem in elements:
        tag_m = re.match(r"<(svg|figure|table|h[1-6]|p)", elem, re.IGNORECASE)
        if not tag_m:
            continue
        tag = tag_m.group(1).lower()

        if tag == "svg":
            if img_idx < len(visual_paths):
                blocks.append(("image", visual_paths[img_idx]))
                img_idx += 1
            # SVG 소진 시 무시 (법집행자가 별도 처리)
        elif tag == "figure":
            # <figure> 안 img → image 블록 (없으면 text로 보존)
            inner_img = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', elem, re.IGNORECASE)
            if inner_img:
                blocks.append(("image", inner_img.group(1)))
            else:
                blocks.append(("text", elem))
        elif tag == "table":
            # <table> → *인포그래픽 스타일* 이미지 (사용자 박제: 모든 이미지는 인포그래픽).
            #   표 내용 그대로 보존(수치 변형 0). 실패 시 기존 plain 표 렌더러 → text 순 폴백.
            try:
                img_path = ""
                try:
                    from JARVIS06_IMAGE.infographic_engine import render_table_infographic as _tbl_infg
                    img_path = _tbl_infg(elem, table_idx, out_dir)
                except Exception:
                    img_path = ""
                if not img_path:
                    from JARVIS06_IMAGE.economic_charts import render_html_table_as_image as _tbl_img
                    img_path = _tbl_img(elem, table_idx, out_dir)
                if img_path:
                    blocks.append(("image", img_path))
                    table_idx += 1
                else:
                    blocks.append(("text", elem))
            except Exception:
                blocks.append(("text", elem))
        elif tag.startswith("h"):
            # 제9조 여백은 law_enforcer.enforce_supreme_law() 단독 담당 — 여기서 추가 금지
            blocks.append(("text", elem))
        else:  # <p>
            # <p> 안에 SVG만 있는 경우 (LLM이 placeholder를 <p>로 감싼 잔존) → 이미지 블록
            inner_svg = re.search(r'<svg[\s\S]*?</svg>', elem, re.IGNORECASE)
            # <p> 안에 matplotlib img만 있는 경우 (chart_generator 전환 후) → 이미지 블록
            inner_img = re.search(r'<img[^>]+src=[\'"]([^\'"]+)[\'"]', elem, re.IGNORECASE)
            plain_text = re.sub(r'<[^>]+>', '', elem).strip()
            if inner_svg and not plain_text:
                if img_idx < len(visual_paths):
                    blocks.append(("image", visual_paths[img_idx]))
                    img_idx += 1
            elif inner_img and not plain_text:
                # matplotlib 차트 img 태그 → 로컬 파일 경로로 image 블록 생성
                blocks.append(("image", inner_img.group(1)))
            else:
                blocks.append(("text", elem))

    return blocks
