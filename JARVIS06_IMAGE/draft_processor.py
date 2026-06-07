"""JARVIS06_IMAGE/draft_processor.py — 대본 → 완성 블록 단일 진입점.

★ 사용자 박제 2026-05-31 — 이미지 생성 책임 단일화.

흐름:
  JARVIS02(대본 HTML) + JARVIS09(수집 자료·종목 데이터)
    → process_draft()
        ① [CHART_N] 플레이스홀더 → matplotlib SVG 차트 생성
        ② [PHOTO_N] 플레이스홀더 → AI 사진 생성 (Pollinations.ai — Bing/HF 폐기 2026-06-07)
        ③ h2 소제목 → 섹션 배경 AI 사진 교체
        ④ SVG 캡처 → JPG
        ⑤ 썸네일 생성
        ⑥ assemble_blocks → (text, image) 블록 조립
    → {"blocks": [...], "thumbnail_path": "...", "title": "...", "html": "..."}

호출자 (JARVIS02 _build_blocks):
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(
        draft_html=html,
        theme=theme, sector=sector,
        stocks_data=stocks_data,
        collection_docs=collection_docs,
        platform=platform,
        out_dir=img_dir,
    )
    blocks         = result["blocks"]
    thumbnail_path = result["thumbnail_path"]
"""
from __future__ import annotations

import re
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger("jarvis.image.draft_processor")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


# ── 차트 생성 (Pass-2) ────────────────────────────────────────────

def _extract_chart_context(html: str, chart_idx: int) -> str:
    """[CHART_N] 앞뒤 <p> 텍스트 추출 — 차트 설명 컨텍스트."""
    pattern = re.compile(
        r"(<p[^>]*>[\s\S]*?</p>)\s*(?:[^\[]*?)\[CHART_" + str(chart_idx) + r":",
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""


def _generate_charts(html: str, theme: str, sector: str, stocks_data: dict,
                     platform: str, out_dir: Path) -> str:
    """[CHART_N: 설명] → matplotlib SVG 치환. 치환된 HTML 반환."""
    from JARVIS02_WRITER.tistory_html_writer import _generate_svg_pass2
    from JARVIS02_WRITER import draft_writer as _dw

    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    print(f"  🎨 [{platform}] [CHART] {len(placeholders)}개 생성 (matplotlib)...")
    run_id = uuid.uuid4().hex
    stocks_text = _dw._stocks_text(stocks_data) if hasattr(_dw, "_stocks_text") else ""

    items = [(pos, int(idx), desc.strip()) for pos, (idx, desc) in enumerate(placeholders, 1)]
    svg_map: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        def _ctx(orig_idx: int) -> str:
            para = _extract_chart_context(html, orig_idx)
            return f"{para}\n\n[종목 데이터]\n{stocks_text}" if para else f"[종목 데이터]\n{stocks_text}"

        futs = {
            ex.submit(_generate_svg_pass2, pos, desc, theme, sector, _ctx(orig_idx), out_dir, run_id): pos
            for pos, orig_idx, desc in items
        }
        for f in as_completed(futs):
            pos_key = futs[f]
            try:
                chart_html = f.result()
                svg_map[pos_key] = chart_html or ""
                print(f"  {'✅' if chart_html else '❌'} CHART_pos{pos_key} {'완료' if chart_html else '실패'}")
            except Exception as e:
                log.warning(f"CHART_pos{pos_key} 오류: {e}")
                _g_report("image", e, module=__name__)
                svg_map[pos_key] = ""

    _pos = [0]

    def _replace(m: re.Match) -> str:
        _pos[0] += 1
        chunk = svg_map.get(_pos[0], "")
        if not chunk:
            print(f"  ⚠️ CHART_pos{_pos[0]} 차트 실패 — 제거")
        return chunk

    result = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace, html)
    ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [{platform}] [CHART] {ok}/{len(placeholders)}개 치환 완료")
    return result


# ── AI 사진 생성 (PHOTO 플레이스홀더) ────────────────────────────

def _generate_photos(html: str, theme: str, out_dir: Path) -> str:
    """[PHOTO_N: 설명] → AI 사진 <img> 태그 치환. 치환된 HTML 반환."""
    placeholders = re.findall(r"\[PHOTO_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    print(f"  📸 [PHOTO] {len(placeholders)}개 AI 사진 생성...")
    from JARVIS06_IMAGE.image_agent import generate_photo

    photo_map: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {
            ex.submit(generate_photo, prompt_ko=f"{theme}: {desc.strip()}", out_dir=str(out_dir)): int(idx)
            for idx, desc in placeholders
        }
        for f in as_completed(futs):
            idx = futs[f]
            try:
                path = f.result()
                photo_map[idx] = path or ""
                print(f"  {'✅' if path else '❌'} PHOTO_{idx} {'완료' if path else '실패'}")
            except Exception as e:
                log.warning(f"PHOTO_{idx} 오류: {e}")
                photo_map[idx] = ""

    def _replace(m: re.Match) -> str:
        idx = int(m.group(1))
        path = photo_map.get(idx, "")
        if path:
            return f'<figure><img src="{path}" alt="{theme}" style="width:100%;border-radius:8px;"></figure>'
        return ""

    return re.sub(r"\[PHOTO_(\d+):[^\]]+\]", _replace, html)


# ── 섹션 AI 사진 (h2 소제목 교체) ────────────────────────────────

def _inject_section_images(html: str, theme: str, sector: str,
                            platform: str, out_dir: Path) -> str:
    """h2 소제목 → AI 배경 사진으로 교체."""
    from JARVIS06_IMAGE.image_agent import generate_photo

    h2_matches = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", html, re.DOTALL))
    if not h2_matches:
        return html

    print(f"  🖼️ [{platform}] 섹션 이미지 {len(h2_matches)}개 생성...")
    section_dir = out_dir
    section_dir.mkdir(parents=True, exist_ok=True)

    section_imgs: dict[int, str | None] = {}
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {
            ex.submit(
                generate_photo,
                prompt_ko=f"{theme}: {re.sub(r'<[^>]+>', '', m.group(1)).strip()} 섹션 배경",
                out_dir=str(section_dir),
            ): i
            for i, m in enumerate(h2_matches)
        }
        for f in as_completed(futs):
            i = futs[f]
            try:
                section_imgs[i] = f.result()
            except Exception as e:
                log.warning(f"섹션{i} 이미지 실패: {e}")
                section_imgs[i] = None

    result = html
    for i, m in enumerate(reversed(h2_matches)):
        idx = len(h2_matches) - 1 - i
        img_path = section_imgs.get(idx)
        h2_text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if img_path:
            replacement = (
                f'<figure style="margin:24px 0 8px;">'
                f'<img src="{img_path}" alt="{h2_text}" style="width:100%;border-radius:8px;">'
                f'</figure>'
            )
        else:
            replacement = m.group(0)
        result = result[:m.start()] + replacement + result[m.end():]

    return result


# ── 공개 API ──────────────────────────────────────────────────────

def process_draft(
    draft_html: str,
    theme: str,
    sector: str,
    stocks_data: dict,
    collection_docs: list | None,
    platform: str,
    out_dir: Path,
) -> dict:
    """대본 HTML + 수집 자료 → 완성 블록 (JARVIS08 발행 준비 완료).

    Args:
        draft_html:      generate_theme_html() 반환값 (플레이스홀더 포함)
        theme:           테마명
        sector:          섹터
        stocks_data:     JARVIS09 collect_stocks_data() 반환값
        collection_docs: JARVIS09 collect_for_theme() 반환값
        platform:        "tistory" | "naver"
        out_dir:         이미지 저장 폴더

    Returns:
        {
            "blocks":         list[tuple],  # JARVIS08이 바로 발행 가능한 완성본
            "thumbnail_path": str | None,
            "title":          str,
            "html":           str,          # 최종 HTML (DB 저장용)
        }
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ① [CHART_N] → matplotlib SVG
    html = _generate_charts(draft_html, theme, sector, stocks_data, platform, out_dir)

    # ② [PHOTO_N] → AI 사진
    html = _generate_photos(html, theme, out_dir)

    # ③ h2 소제목 → 섹션 AI 사진
    html = _inject_section_images(html, theme, sector, platform, out_dir)

    # ④ 제목 추출
    from JARVIS02_WRITER.tistory_html_writer import extract_title, save_article_html, screenshot_article
    title = extract_title(html, theme)

    # ⑤ HTML 저장
    html_path, _ = save_article_html(html, theme, platform=platform)

    # ⑥ SVG 캡처 → JPG
    print(f"  📸 [{platform}] SVG 캡처...")
    visual_paths = screenshot_article(html_path, str(out_dir))
    if not visual_paths:
        print(f"  ⚠️ [{platform}] 스크린샷 0개 — 텍스트 전용 진행")

    # ⑦ 썸네일 생성 (백그라운드)
    thumbnail_path = None
    try:
        from JARVIS06_IMAGE.image_agent import generate_thumbnail
        body_text = re.sub(r"<[^>]+>", "", html)[:400]
        thumbnail_path = generate_thumbnail(
            title=title, keyword=theme, sector=sector,
            platform=platform, out_dir=out_dir,
            body_text=body_text,
        )
        print(f"  🖼️ [{platform}] 썸네일 생성 완료")
    except Exception as e:
        log.warning(f"썸네일 실패: {e}")
        _g_report("image", e, module=__name__)

    # ⑧ assemble_blocks → 완성 블록
    from JARVIS06_IMAGE.injectors import assemble_blocks
    blocks = assemble_blocks(html, visual_paths, out_dir=out_dir)

    print(f"  ✅ [{platform}] process_draft 완료 — 블록 {len(blocks)}개")
    return {
        "blocks":         blocks,
        "thumbnail_path": thumbnail_path,
        "title":          title,
        "html":           html,
    }
