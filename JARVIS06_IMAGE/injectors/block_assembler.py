"""JARVIS06_IMAGE/injectors/block_assembler.py — 1-pass HTML → 이미지 블록 조립.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — tistory_html_writer.assemble_blocks 이관.

원래 위치: JARVIS02_WRITER/tistory_html_writer.py:749
이관 일자: 2026-05-17
이관 사유: 이미지 도메인 단일 진입점 (ADR 008)

assemble_blocks 는 HTML body 에서 p/svg/h2 를 순서대로 파싱하여 *이미지 블록* 으로 치환하는
*이미지 도메인* 의 핵심 조립기. visual_paths (JARVIS06 관리 JPG) 와 HTML 의 SVG 위치를 매핑.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis")


def _table_image(table_html: str, idx: int, out_dir, datasets) -> str:
    """대본 <table> → 인포그래픽 이미지. **인증을 통과한 것만** 경로를 돌려준다.

    ★ 왜 인증을 거치는가 (사용자 박제 2026-08-10 — 초크포인트 우회로 폐쇄):
      표 안의 숫자는 *LLM 이 쓴 것* 이다. 그런데 이미지가 되는 순간 텍스트 사실성
      게이트의 시야에서도 사라져, 검증을 한 번도 안 받은 수치가 본문 한가운데
      그림으로 남았다 — 표 이미지 경로는 `certify_image` 도, `DATA_IMAGE_ATTR`
      표식도 지나지 않는 완전한 사각지대였다.
    ★ 실패 시 이미지를 만들지 않는다. 표는 **텍스트로 남아** 사실성 게이트가 계속
      본다 — 지우는 것보다 낫고, 거짓 그림보다 낫다.
    ★ 검증 재료(datasets)가 없으면 아예 이미지로 바꾸지 않는다 (fail-closed).
    ★ `kind` 를 넘기지 않는다 — 무엇으로 볼지는 인증기가 데이터에서 파생한다(②).
    ★ 인증은 `render_table_infographic` **안에서 한 번뿐** 이다 (①단일 진입점).
      여기서 재인증하지 않는다 — 게이트를 두 개 두면 언제나 느슨한 쪽으로 물이 샌다.
      대신 **대조군을 안으로 넘긴다**. 종전엔 이 호출이 `datasets` 를 빠뜨려
      안쪽 인증기가 대조군 0으로 판정했고, 그 결과 *수치가 든 표는 영원히
      이미지가 되지 못했다* (숫자 없는 표만 통과 — 기능이 100% 죽은 상태였다).
      실측: 표와 완전히 일치하는 dataset 을 줘도 `grounding 실패 [3.5, 2.8]` → 폐기.
    """
    if not datasets:
        return ""
    try:
        from JARVIS06_IMAGE.infographic_engine import render_table_infographic
        path = render_table_infographic(table_html, idx, out_dir,
                                        datasets=list(datasets))
    except Exception as e:
        log.warning(f"[blocks] 표 인포그래픽 렌더 실패 → 텍스트 유지: {e}")
        return ""
    # 인증 미통과분은 안쪽이 이미 폐기하고 "" 를 돌려준다 (사유는 그쪽 로그에 남는다).
    if not path:
        return ""
    return str(path)


def assemble_blocks(html: str, visual_paths: list, out_dir=None, datasets=None) -> list:
    """1-pass HTML + JARVIS06 관리 JPG 경로 → post_to_tistory blocks 조립.

    HTML body에서 p/svg/h2를 순서대로 파싱:
    - p     → text 블록
    - svg   → image 블록 (visual_paths에서 순서대로 치환). 소진 시 1줄 여백.
    - h2    → 2줄 여백 + h2 text 블록 (제9조)
    - table → image 블록 (인증 통과 시에만; 실패·미인증이면 text 유지)

    제4조: p→jpg→p→jpg→p 교차 — SVG 위치가 이미지 슬롯이므로 자동 보장.
    제9조: h2 앞 2줄 여백(<p>&nbsp;</p><p>&nbsp;</p>) 자동 삽입.
    제0조: 첫 <p>(감성 오프닝)이 항상 첫 블록 — svg가 항상 p 뒤에 오므로 자동.

    Args:
        out_dir:  표 이미지 저장 폴더 (None 시 JARVIS06_IMAGE/output/ 기본값 사용)
        datasets: 실데이터 dataset 목록 (표 이미지 수치 대조군). 비면 표는 텍스트로 유지

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
            #   ★ 렌더러는 하나다 (①): 종전엔 실패 시 `economic_charts.render_html_table_as_image`
            #     (matplotlib) 로 내려가는 2순위 사본이 있었고, 그 사본은 인증을 지나지 않아
            #     '검증 없이 나가는 길' 그 자체였다 — 사본을 지우고 인증을 문에 걸었다.
            img_path = _table_image(elem, table_idx, out_dir, datasets)
            if img_path:
                blocks.append(("image", img_path))
                table_idx += 1
            else:
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
