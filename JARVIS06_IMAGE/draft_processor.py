"""JARVIS06_IMAGE/draft_processor.py — 대본 → 완성 블록 단일 진입점.

★ 사용자 박제 2026-05-31 — 이미지 생성 책임 단일화.

흐름:
  JARVIS02(대본 HTML) + JARVIS09(수집 자료·종목 데이터)
    → process_draft()
        ① [CHART_N] 플레이스홀더 → matplotlib SVG 차트 생성
        ② [PHOTO_N] 플레이스홀더 → AI 사진 생성 (Pollinations.ai — Bing/HF 폐기 2026-06-07)
        ③ (옛 h2→섹션이미지 교체 동작 폐기 — 사용자 박제 2026-05-15 ↔ 2026-06-07)
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


# ── AI 사진 영문 프롬프트 빌더 (★ 사용자 박제 2026-06-07) ─────────
# Pollinations.ai (SDXL 계열) 친화 프롬프트 직접 생성 → translate() 우회.
# prompt_translator(Haiku 100자 축약) 대비 디테일·스타일·negative cue 강화.

_PHOTO_PROMPT_SYSTEM = """You are a professional photography art director for premium Korean finance/business blogs.
Craft vivid, specific English prompts optimized for Stable Diffusion XL (Pollinations.ai backend).

Strict rules:
- Output ONLY the English prompt — no preface, no markdown, no labels, no quotes.
- 60-120 words, comma-separated descriptors.
- Always include in order: SUBJECT, SCENE/ENVIRONMENT, LIGHTING, MOOD, CAMERA ANGLE, STYLE, QUALITY TAGS.
- Aesthetic: editorial, professional, modern, clean, premium, Korean business context where natural.
- ★ When "Real-world facts" are provided, GROUND the SUBJECT and SCENE in those concrete facts —
  translate the actual entities (companies, products, events, numbers) into specific visual cues.
  Do NOT just paint generic business imagery; reflect the news/story directly.
- End with quality boosters: "photorealistic, ultra detailed, 8k, cinematic lighting, depth of field, professional photography, sharp focus".
- Discourage failure modes by appending: "no text, no letters, no watermark, no logo, no distorted faces, no extra fingers, not cartoon, not anime, not illustration"."""

_PHOTO_PROMPT_TEMPLATE = """Generate the single best English image prompt for this Korean blog visual.

Theme: {theme}
Sector: {sector}
Section title: {section_title}
Korean description: {desc}{facts_block}

Output the prompt now (one line or comma-separated phrases, no preface)."""


def _build_photo_prompt_en(theme: str, desc: str, sector: str = "",
                            section_title: str = "",
                            facts: list[str] | None = None) -> str:
    """Sonnet으로 Pollinations 친화적 영문 프롬프트 생성.

    ★ 사용자 박제 2026-06-07 — facts 인자:
       JARVIS09 수집 자료에서 추출한 헤드라인·수치 라인 목록.
       프롬프트의 SUBJECT/SCENE 을 사실에 grounded 시킴 → 추상적 generic 이미지 방지.

    실패 시 빈 문자열 반환 — 호출자는 generate_photo()의 prompt_ko 경로(Haiku 번역)
    로 자동 폴백.
    """
    try:
        from shared.llm import invoke_text
        # facts 블록 — 있으면 템플릿에 추가
        facts_block = ""
        if facts:
            top = [str(f)[:160] for f in facts[:5] if f and str(f).strip()]
            if top:
                facts_block = "\nReal-world facts (ground the image in these):\n" + "\n".join(f"- {t}" for t in top)
        prompt = _PHOTO_PROMPT_TEMPLATE.format(
            theme=(theme or "")[:80],
            sector=sector or "general business",
            section_title=(section_title or "")[:60],
            desc=(desc or "")[:240],
            facts_block=facts_block,
        )
        raw = invoke_text(
            "analyzer", prompt, system=_PHOTO_PROMPT_SYSTEM,
            max_tokens=500, temperature=0.75,
        )
        en = (raw or "").strip()
        # 마크다운/라벨 잔존분 제거 (image_agent 에서도 한 번 더 정제하지만 방어)
        en = re.sub(r'^#+\s*[^\n]*\n+', '', en, flags=re.MULTILINE)
        en = re.sub(r'^\*{1,2}[A-Za-z ]+\*{1,2}:?\s*', '', en).strip().strip('"').strip("'").strip()
        # 너무 짧으면 무효 (Pollinations 가 빈약한 결과 반환할 위험)
        if len(en) < 40:
            return ""
        return en
    except Exception as e:
        log.warning(f"[photo_prompt] 영문 빌더 실패: {e} — Haiku 번역 폴백")
        _g_report("image", e, module=__name__)
        return ""


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
                     platform: str, out_dir: Path,
                     collection_docs: list | None = None) -> str:
    """[CHART_N: 설명] → matplotlib SVG 치환. 치환된 HTML 반환.

    ★ 사용자 박제 2026-06-07 — collection_docs 를 context_text 에 주입.
       chart_generator 가 또 다시 JARVIS09 호출하지 않도록 상위 docs 우선 사용.
       단, 부족 감지 시 chart_generator 가 자율로 _build_j09_context 호출 가능.
    """
    from JARVIS02_WRITER.tistory_html_writer import _generate_svg_pass2
    from JARVIS02_WRITER import draft_writer as _dw

    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    print(f"  🎨 [{platform}] [CHART] {len(placeholders)}개 생성 (matplotlib)...")
    run_id = uuid.uuid4().hex
    stocks_text = _dw._stocks_text(stocks_data) if hasattr(_dw, "_stocks_text") else ""

    # ★ collection_docs 의 수치 사실 라인을 차트 컨텍스트로 변환
    docs_facts_text = ""
    if collection_docs:
        try:
            from JARVIS06_IMAGE.collection_merger import facts_for_chart
            facts = facts_for_chart(collection_docs, max_n=12)
            if facts:
                docs_facts_text = "[수집 사실 라인]\n" + "\n".join(facts)
        except Exception as e:
            log.warning(f"facts_for_chart 실패: {e}")
            _g_report("image", e, module=__name__)

    items = [(pos, int(idx), desc.strip()) for pos, (idx, desc) in enumerate(placeholders, 1)]
    svg_map: dict[int, str] = {}

    with ThreadPoolExecutor(max_workers=4) as ex:
        def _ctx(orig_idx: int) -> str:
            para = _extract_chart_context(html, orig_idx)
            parts = []
            if para:
                parts.append(para)
            if stocks_text:
                parts.append(f"[종목 데이터]\n{stocks_text}")
            if docs_facts_text:
                parts.append(docs_facts_text)
            return "\n\n".join(parts) if parts else ""

        futs = {
            ex.submit(_generate_svg_pass2, pos, desc, theme, sector,
                      _ctx(orig_idx), out_dir, run_id, collection_docs): pos
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

def _generate_photos(html: str, theme: str, out_dir: Path,
                     sector: str = "",
                     collection_docs: list | None = None) -> str:
    """[PHOTO_N: 설명] → AI 사진 <img> 태그 치환. 치환된 HTML 반환.

    ★ 사용자 박제 2026-06-07 — collection_docs 의 헤드라인을 facts 로
       _build_photo_prompt_en 에 주입. 추상적 generic 이미지 방지.
       부족하면 collection_merger.request_more 로 JARVIS09 보강 요청.
    """
    placeholders = re.findall(r"\[PHOTO_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    print(f"  📸 [PHOTO] {len(placeholders)}개 AI 사진 생성...")
    from JARVIS06_IMAGE.image_agent import generate_photo

    # ★ facts 사전 추출 — 부족 시 JARVIS09 delta 보강
    facts: list[str] = []
    try:
        from JARVIS06_IMAGE.collection_merger import facts_for_photo, request_more
        facts = facts_for_photo(collection_docs or [], max_n=6)
        if len(facts) < 3 and theme:
            # 자율 보강: scene_context aspect 로 더 가져오기
            print(f"  🔄 [PHOTO] facts={len(facts)}개 부족 → JARVIS09 scene_context 보강 요청")
            merged = request_more(theme=theme, existing=collection_docs or [],
                                  sector=sector, aspect="scene_context")
            facts = facts_for_photo(merged, max_n=6)
            print(f"  ✅ [PHOTO] 보강 후 facts={len(facts)}개")
    except Exception as e:
        log.warning(f"facts_for_photo 실패 (무시): {e}")
        _g_report("image", e, module=__name__)

    photo_map: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {}
        for idx, desc in placeholders:
            desc_str = desc.strip()
            # ★ Sonnet 영문 프롬프트 빌더 (실패 시 prompt_ko 자동 폴백)
            prompt_en = _build_photo_prompt_en(theme=theme, desc=desc_str,
                                                sector=sector, facts=facts)
            kw = dict(prompt_ko=f"{theme}: {desc_str}", out_dir=str(out_dir))
            if prompt_en:
                kw["prompt_en"] = prompt_en
            futs[ex.submit(generate_photo, **kw)] = int(idx)
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


# ── 섹션 AI 사진 (h2 소제목 교체) — ★ 폐기 (사용자 박제 2026-05-15 ↔ 2026-06-07 정합) ──
#
# 옛 _inject_section_images 는 <h2> 를 <figure><img></figure> 로 *교체* 하여
# 소제목 텍스트가 사라지게 만들었다. 사용자 박제 2026-05-15 (trend_economic_writer.py:562)
# 에서 *옛 h2→이미지 교체 동작 폐기* 가 명시됐고, 2026-06-07 사용자 결정으로
# 테마글에도 동일 적용. h2 는 텍스트 소제목으로 유지하고, 본문 사이 이미지는
# [PHOTO_N] / [CHART_N] 플레이스홀더 단일 경로로만 진입.
#
# 따라서 _inject_section_images 와 _select_section_facts 모두 삭제. process_draft
# 흐름에서도 호출 제거 (① 차트 / ② 사진 / 썸네일 / 캡처 / 조립).


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

    # ① [CHART_N] → matplotlib SVG (★ collection_docs 의 수치 사실 컨텍스트 주입)
    html = _generate_charts(draft_html, theme, sector, stocks_data, platform, out_dir,
                            collection_docs=collection_docs)

    # ② [PHOTO_N] → AI 사진 (★ collection_docs 의 헤드라인 facts 주입)
    html = _generate_photos(html, theme, out_dir,
                            sector=sector,
                            collection_docs=collection_docs)

    # ③ (옛 h2→이미지 교체 폐기 — 사용자 박제 2026-05-15 ↔ 2026-06-07)
    #    h2 소제목은 텍스트로 유지. 본문 사이 이미지는 [CHART_N]/[PHOTO_N] 단일 경로만.

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
