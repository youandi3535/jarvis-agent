"""JARVIS06_IMAGE/draft_processor.py — 대본 → 완성 블록 단일 진입점.

★ 사용자 박제 2026-05-31 — 이미지 생성 책임 단일화.

흐름:
  JARVIS02(대본 HTML) + JARVIS09(수집 자료·종목 데이터)
    → process_draft()
        ① [CHART_N]...[/CHART_N] 데이터 내장 슬롯 → infographic_engine 고퀄리티 인포그래픽
        ② [PHOTO_N] 플레이스홀더 → AI 사진 생성 (Pollinations.ai — Bing/HF 폐기 2026-06-07)
        ③ (옛 h2→섹션이미지 교체 동작 폐기 — 사용자 박제 2026-05-15 ↔ 2026-06-07)
        ④ SVG 캡처 → JPG
        ⑤ 썸네일 생성
        ⑥ assemble_blocks → (text, image) 블록 조립
    → {"blocks": [...], "thumbnail_path": "...", "title": "...", "html": "..."}

호출자 (JARVIS02 _build_blocks):
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(draft_html=html, collected=collected,
                           platform=platform, out_dir=img_dir)
    blocks         = result["blocks"]
    thumbnail_path = result["thumbnail_path"]

★ v2 (Step 6, 2026-07-05): 단일 인자 collected(CollectedData)로 통일. keyword/sector/
  category·검증정답·차트seed·이미지컨텍스트를 전부 collected 에서 파생. 카테고리 노브는
  CATEGORY_POLICY 레지스트리. 실패차트→AI사진 폴백 + min-N top-up + 썸네일 필수(누락0).
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
# prompt_translator(Sonnet 5 축약) 대비 디테일·스타일·negative cue 강화.

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
- ★ ALWAYS keep the SUBJECT literal and concrete — a real photograph of the actual thing/place/
  person. NEVER a metaphor, symbol, or abstract/surreal representation of the topic (no "a stone
  symbolizing stability", no conceptual objects). If the topic is a data trend, show a real scene
  from that domain, not an abstract visualization.
- End with quality boosters: "photorealistic, ultra detailed, 8k, cinematic lighting, depth of field, professional photography, sharp focus, real documentary photograph".
- Discourage failure modes by appending: "no text, no letters, no watermark, no logo, no distorted faces, no extra fingers, no deformed animals, no weird objects, realistic anatomy, not abstract, not surreal, not conceptual art, not a metaphor, not cartoon, not anime, not illustration"."""

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

    실패 시 빈 문자열 반환 — 호출자는 generate_photo()의 prompt_ko 경로(Sonnet 5 번역)
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
        log.warning(f"[photo_prompt] 영문 빌더 실패: {e} — Sonnet 5 번역 폴백")
        _g_report("image", e, module=__name__)
        return ""


# ── 사진 관련성 검증 (2026-07-02) — 생성 사진이 섹션과 무관해지는 것을 프롬프트
#    레벨에서 차단. 무거운 vision 호출 없음(JARVIS05_VISION은 트렌드 레지스트리라
#    캡션/CLIP 미보유). 신호 = 엔티티 carry-through: 섹션 facts의 실체(영문 고유명사)가
#    번역을 넘어 최종 프롬프트에 살아남았는가. 순수 정규식 → 레이턴시 0·루프 불가.
# ★ 순수 수치(연도·금액·%·건수)는 grounding 신호에서 제외 (2026-07-06 — 오탐 근본수정):
#    사진 프롬프트는 _PHOTO_PROMPT_SYSTEM 이 "no text, no letters" 를 강제하므로 숫자는
#    픽셀로 렌더될 수 없다(시각적 단서로만 번역). facts 는 뉴스 헤드라인이라 숫자 위주라,
#    숫자를 carry-through 실체로 세면 정상 장면 프롬프트도 "실체 N개 중 0개 반영" 오탐 →
#    무관 이미지 오판·prompt_en 폐기·GUARDIAN 노이즈. 사진의 피사체가 될 수 있는 영문
#    고유명사(회사·제품·지명)만 grounding 신호로 인정.
_ENTITY_RE = re.compile(r'\b[A-Z][A-Za-z0-9&.\-]{2,}\b')
_ENTITY_STOP = {"The", "This", "That", "With", "And", "For",
                "Korea", "Korean", "Business", "Finance"}


def _ascii_entities(*texts: str) -> set:
    """theme/desc/facts 에서 번역을 넘어 살아남는 실체 토큰(영문 고유명사·수치) 추출."""
    ents: set = set()
    for t in texts:
        for tok in _ENTITY_RE.findall(t or ""):
            if tok in _ENTITY_STOP:
                continue
            ents.add(tok.lower())
    return ents


try:
    from JARVIS00_INFRA.verification import register_check as _reg_prompt_check

    @_reg_prompt_check("generate_photo_prompt", "프롬프트 사실 grounding 소실", severity="block")
    def _chk_prompt_grounding(output, ctx):
        """섹션 실체가 ≥2개 있는데 프롬프트에 0개 반영 → 무관 이미지 위험(드리프트)."""
        prompt_en = (output or "").strip()
        if not prompt_en:
            return ""   # grounded 빌더 실패 → theme+desc 폴백(주제 보장) → 면제(warn 처리)
        ents = _ascii_entities(ctx.get("theme", ""), ctx.get("desc", ""),
                               *(ctx.get("facts") or []))
        if len(ents) < 2:
            return ""   # 대조할 실체 부족(순한글 주제 등) → 면제(오탐 방지)
        low = prompt_en.lower()
        if any(e in low for e in ents):
            return ""   # 실체 하나라도 살아남음 → grounded
        return f"섹션 실체 {len(ents)}개 중 프롬프트 반영 0개 — 무관 이미지 위험"

    @_reg_prompt_check("generate_photo_prompt", "프롬프트 grounding 약함", severity="warn")
    def _chk_prompt_generic(output, ctx):
        """facts는 있는데 grounded 빌더가 빈 값 반환(generic 번역 폴백) → 경고만."""
        if (output or "").strip():
            return ""
        return "grounded 빌더 실패 → generic 번역 폴백(사실 미주입)" if (ctx.get("facts") or []) else ""
except Exception:
    pass


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


def _stock_numbers(stocks_data: dict) -> list[float]:
    """테마 내장 슬롯 검증 ref — 종목 실데이터의 모든 수치 값 (재귀 수집)."""
    vals: list[float] = []

    def _walk(o):
        if isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                _walk(v)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            vals.append(float(o))
        elif isinstance(o, str):
            s = o.replace(",", "").strip().rstrip("%")
            try:
                vals.append(float(s))
            except ValueError:
                pass
    _walk(stocks_data or {})
    return vals


# ══════════════════════════════════════════════════════════════════════════
# ★ process_draft v2 헬퍼 (Step 6, UNIFIED_PIPELINE_SPEC 2026-07-05)
#   실패차트→AI사진 폴백 · min-N top-up · 로컬 썸네일 폴백 · collected 검증 ref.
#   (경제 embed-first 래퍼 tistory_html_writer 의 검증된 로직을 단일 소스로 이식)
# ══════════════════════════════════════════════════════════════════════════

# 썸네일 하단 카테고리 라벨 (category → 라벨). 미매칭 시 섹터/키워드 폴백.
_TAG_BY_CATEGORY = {"economic": "경제 브리핑", "theme": "테마 분석"}

def _slot_ref_datasets(collected) -> list:
    """CollectedData → slot_renderer 검증 ref (datasets + 단위별 all_numbers 그룹).
    entities 수치까지 포함해 슬롯 진실성 게이트가 굶지 않게 함."""
    ref = list(collected.datasets or [])
    by_unit: dict = {}
    for v, u in collected.all_numbers():
        by_unit.setdefault(u, []).append({"value": v})
    for u, rows in by_unit.items():
        ref.append({"unit": u, "data": rows})
    return ref


def _entities_text(entities) -> str:
    """CollectedData.entities → 차트 컨텍스트용 종목 텍스트 (표시단위 부착)."""
    if not entities:
        return ""
    try:
        from JARVIS09_COLLECTOR.models import ATTR_UNITS
    except Exception:
        ATTR_UNITS = {}
    lines = []
    for e in entities[:12]:
        name = e.get("name", "")
        attrs = e.get("attrs") or {}
        parts = []
        for k, v in attrs.items():
            val = v.get("value") if isinstance(v, dict) else v
            unit = (v.get("unit") if isinstance(v, dict) else "") or ATTR_UNITS.get(k, "")
            parts.append(f"{k} {val}{unit}")
        if name and parts:
            lines.append(f"- {name}: {', '.join(parts)}")
    return "\n".join(lines)


def _count_images(html: str) -> int:
    return len(re.findall(r"<img\b", html, re.I)) + len(re.findall(r"<svg\b", html, re.I))


def _ai_photo_html(path, alt: str) -> str:
    return (f'<p><img src="{path}" alt="{alt}" '
            f'style="width:100%;max-width:760px;border-radius:8px;'
            f'margin:16px auto;display:block;"></p>')


def _photo_for_failed_slot(description: str, keyword: str, out_dir: Path) -> str:
    """실패 [CHART_N] 슬롯 → 주제 실사진 1장 (사용자 박제 2026-07-01 — 은유·추상 금지)."""
    try:
        from JARVIS06_IMAGE.image_agent import generate_photo as _gp
        import random as _rand, datetime as _dt
        _angles = ["넓은 전경, 자연광", "현장에서 일하는 사람들",
                   "제품·설비 중심 클로즈업", "도시·건물 배경, 낮", "실내 현장, 밝은 조명"]
        _today = _dt.date.today().isoformat()
        prompt_ko = (f"{keyword} 를 직접 보여주는 실제 다큐멘터리 사진, "
                     f"{_rand.choice(_angles)}, 주제와 관련된 구체적 사물·현장·사람, 사실적 ({_today})")
        path = _gp(prompt_ko=prompt_ko, out_dir=out_dir)
        if path:
            return _ai_photo_html(path, (keyword or description)[:40].replace('"', "'"))
    except Exception as e:
        print(f"  ⚠️ AI 사진(슬롯) 실패: {e}")
    return ""


def _extra_photos(keyword: str, sector: str, count: int, out_dir: Path) -> list:
    """최소 이미지 수 충족용 추가 AI 사진 — 주제 앵커 실사 (max_workers=1 순차)."""
    if count <= 0:
        return []
    try:
        from JARVIS06_IMAGE.image_agent import generate_photo as _gp
    except ImportError:
        return []
    import random as _rand2, datetime as _dt2
    _today2 = _dt2.date.today().isoformat()
    _rng = _rand2.Random(hash(f"{keyword}|{_today2}"))
    _base_prompts = [
        f"{keyword} 를 직접 보여주는 실제 다큐멘터리 사진, 넓은 전경, 자연광",
        f"{keyword} 관련 제품·설비 중심 클로즈업, 실제 현장, 사실적",
        f"{keyword} 현장에서 일하는 사람들, 실제 작업 장면, 자연광",
        f"{keyword} 관련 실제 산업 현장 내부, 밝은 조명, 사실적",
        f"{keyword} 관련 실제 장소·건물 외관, 도시 배경, 낮, 실사",
        f"{keyword} 관련 물류·운송 현장, 실제 차량·설비, 사실적",
        f"{keyword} 관련 연구개발 현장, 실제 실험실 장비와 연구원",
        f"{keyword} 자료를 검토하는 사람, 실제 사무실, 모니터 화면, 자연광",
        f"{keyword} 관련 생산 라인, 실제 공장 내부, 사실적",
        f"{keyword} 관련 매장·거래 현장, 실제 사람들, 낮, 실사",
    ]
    _rng.shuffle(_base_prompts)
    prompts = _base_prompts[:count]
    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=1) as ex:   # ★ 순차 강제 (Pollinations 429 방지)
        futs = {ex.submit(_gp, prompt_ko=p, out_dir=out_dir): (i, p)
                for i, p in enumerate(prompts)}
        for fut in as_completed(futs):
            idx, prompt = futs[fut]
            try:
                path = fut.result()
                if path:
                    results[idx] = _ai_photo_html(path, prompt[:40].replace('"', "'"))
                    print(f"  🖼️ 추가 AI 사진 {idx+1}/{count} 완료")
            except Exception as e:
                print(f"  ⚠️ 추가 AI 사진 {idx+1} 실패: {e}")
    return [results[i] for i in sorted(results)]


def _next_data_infographic(collected, out_dir: Path, run_id: str, used_titles: set,
                           platform: str = "", html_so_far: str = "") -> str:
    """수집 실데이터에서 *아직 안 쓴* dataset 1개 → 결정론(LLM 0회) 인포그래픽 <p><img></p>.

    ★ 사용자 박제 2026-07-05 (ERRORS [364]): "실데이터(API+텍스트)는 항상 있다 →
    인포그래픽 무조건 생성". collected.datasets = stocks_to_datasets + facts_to_datasets
    (compose_collected 가 출처 박제 조립). used_titles 로 슬롯·top-up 간 중복 방지.
    거짓 차트 금지(규정 12): generate_infographic 내부 _verify_dataset 가 출처 없는 dataset 제거.
    없으면 "" 반환(→ 호출자가 AI 사진 폴백).
    """
    try:
        from JARVIS06_IMAGE.infographic_engine import generate_infographic
    except ImportError:
        return ""
    theme = (getattr(collected, "meta", None) or {}).get("keyword", "")
    for ds in (getattr(collected, "datasets", None) or []):
        if not ds.get("data"):
            continue
        _title = (ds.get("title") or f"{theme} 핵심 수치").strip()
        if _title in used_titles or (html_so_far and _title and _title in html_so_far):
            continue
        used_titles.add(_title)   # 성공·실패 무관 1회만 시도 (중복·무한 방지)
        try:
            _src = (ds.get("source") or {}).get("name") or "자비스09 수집"
            # ★ generate_infographic 은 렌더된 jpg *경로* 반환 → 사진과 동일하게
            #   <p><img></p> 로 감싸야 assemble_blocks 가 image 블록으로 인식(경로만은 누락).
            _path = generate_infographic(
                _title, "수집 실데이터 기반", [ds],
                run_id=run_id or theme, slot_key=f"dg{len(used_titles)}", out_dir=out_dir,
                context=f"{theme} — {_title}", src=f"데이터 출처: {_src}",
            )
            if _path:
                print(f"  📊 [{platform}] 실데이터 인포그래픽: {_title[:30]}")
                return _ai_photo_html(_path, _title[:40].replace('"', "'"))
        except Exception as e:
            print(f"  ⚠️ [{platform}] 실데이터 인포그래픽 실패({_title[:20]}): {e}")
    return ""


def _extra_infographics(collected, out_dir: Path, count: int,
                        run_id: str = "", platform: str = "",
                        html_so_far: str = "", used_titles: set = None) -> list:
    """★ min-N top-up 을 *실데이터 인포그래픽* 으로 채운다 (사용자 박제 2026-07-05, ERRORS [364]).

    데이터 있으면 무조건 인포그래픽 — AI 사진 폴백 *이전*. datasets 소진 시 빈 리스트.
    """
    if count <= 0:
        return []
    used = used_titles if used_titles is not None else set()
    out: list = []
    while len(out) < count:
        fig = _next_data_infographic(collected, out_dir, run_id, used, platform, html_so_far)
        if not fig:
            break
        out.append(fig)
    return out


def _insert_extra_photos(content: str, photos: list) -> str:
    """추가 AI 사진을 이미지 없는 h2/h3 섹션에 배포 + 나머지는 말미."""
    if not photos:
        return content
    h_matches = list(re.finditer(r'<h[23][^>]*>.*?</h[23]>', content, re.IGNORECASE | re.DOTALL))
    if not h_matches:
        return content + "\n" + "\n".join(photos)
    img_free_ends: list[int] = []
    for i, m in enumerate(h_matches):
        end = h_matches[i + 1].start() if i + 1 < len(h_matches) else len(content)
        section = content[m.start():end]
        if "<img" not in section and "<figure" not in section and "<svg" not in section:
            img_free_ends.append(end)
    result = content
    slots_used = min(len(img_free_ends), len(photos))
    for pos, photo in zip(reversed(img_free_ends[:slots_used]), reversed(photos[:slots_used])):
        result = result[:pos] + "\n" + photo + "\n" + result[pos:]
    remaining = photos[slots_used:]
    if remaining:
        result += "\n" + "\n".join(remaining)
    return result


def _local_text_thumbnail(title: str, keyword: str, out_dir: Path):
    """최후 로컬 썸네일 폴백 — 외부 API 없이 타이틀 카드 (누락 0 보장)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import hashlib
        try:
            from JARVIS06_IMAGE.style_engine import setup_chart_defaults
            setup_chart_defaults()
        except Exception:
            pass
        h = hashlib.md5((keyword or title or "x").encode("utf-8")).hexdigest()
        accent = "#" + h[:6]
        fig = plt.figure(figsize=(12.8, 6.72), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, color="#1a2230"))
        ax.add_patch(plt.Rectangle((0, 0), 0.018, 1, color=accent))
        ax.text(0.06, 0.5, (title or keyword or "")[:38], fontsize=34,
                color="white", va="center", ha="left")
        out = Path(out_dir) / f"thumb_local_{h[:8]}.png"
        fig.savefig(str(out), facecolor="#1a2230")
        plt.close(fig)
        return str(out)
    except Exception as e:
        log.warning(f"로컬 썸네일 폴백 실패: {e}")
        return None


def _mandatory_thumbnail(title: str, keyword: str, sector: str, platform: str,
                         out_dir: Path, body_text: str, tag_line: str = ""):
    """★ 썸네일 필수 생성 (사용자 박제 2026-07-05) — 재시도 2회 + 로컬 폴백 → 누락 0."""
    from JARVIS06_IMAGE.image_agent import generate_thumbnail
    for attempt in range(2):
        try:
            p = generate_thumbnail(title=title, keyword=keyword, sector=sector,
                                   platform=platform, out_dir=out_dir, body_text=body_text,
                                   tag_line=tag_line)
            if p:
                return p
        except Exception as e:
            log.warning(f"썸네일 시도 {attempt+1} 실패: {e}")
            _g_report("image", e, module=__name__)
    return _local_text_thumbnail(title, keyword, out_dir)   # 최후 로컬 카드


def _generate_charts(html: str, theme: str, sector: str, collected,
                     platform: str, out_dir: Path,
                     chart_ai_fallback: bool = True,
                     context_docs: list | None = None,
                     run_id: str = "", used_titles: set = None) -> str:
    """[CHART_N]...[/CHART_N] 데이터 내장 슬롯 → infographic_engine 고퀄리티 인포그래픽.

    구형식 [CHART_N: 설명] 잔존 슬롯 = 데이터 없음 → AI 사진 직행 (거짓 차트 금지).
    검증 ref = CollectedData(datasets+facts+entities) 단일 소스.
    """
    # ── 0단계: ★ 데이터 내장 슬롯 렌더 (자비스06 = 렌더러) ──
    #   신형식 [CHART_N]...[/CHART_N] → slot_renderer → infographic_engine
    try:
        from JARVIS06_IMAGE.slot_renderer import render_slots_in_text
        _ref_ds = _slot_ref_datasets(collected)
        html, _s_ok, _s_total = render_slots_in_text(
            html, _ref_ds, out_dir, run_id=uuid.uuid4().hex[:8], theme=theme)
        if _s_total:
            print(f"  🎨 [{platform}] 데이터 내장 슬롯 {_s_ok}/{_s_total}개 렌더 (infographic_engine)")
    except Exception as _sre:
        print(f"  ⚠️ [{platform}] 내장 슬롯 처리 스킵: {_sre}")

    # ── 1단계: 구형식 [CHART_N: text] 잔존 슬롯 → AI 사진 직행 ──
    # 구형식 = 구조화 데이터 없음 → 거짓 차트 생성 금지 (JARVIS06 CLAUDE.md 규정 12)
    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    # ★ 실데이터 인포그래픽 우선 (사용자 박제 2026-07-05, ERRORS [364]): 구형식/실패 슬롯도
    #   LLM 이 "차트 자리"라 판단한 곳 → 수집 실데이터로 인포그래픽을 먼저 채우고, 데이터
    #   소진 시에만 AI 사진. ("데이터 있으면 무조건 인포그래픽" — top-up 과 동일 원칙·중복방지)
    print(f"  ⚠️ [{platform}] 구형식 슬롯 {len(placeholders)}개 발견 → 실데이터 인포그래픽 우선 (소진 시 AI 사진)")
    _desc_by_pos = {i + 1: desc.strip() for i, (_, desc) in enumerate(placeholders)}
    _used = used_titles if used_titles is not None else set()
    svg_map: dict[int, str] = {}

    for pos, desc in _desc_by_pos.items():
        _info = _next_data_infographic(collected, out_dir, run_id, _used, platform, html)
        if _info:
            svg_map[pos] = _info
        elif chart_ai_fallback:
            svg_map[pos] = _photo_for_failed_slot(desc, theme, out_dir)
        else:
            svg_map[pos] = ""

    _pos = [0]

    def _replace(m: re.Match) -> str:
        _pos[0] += 1
        chunk = svg_map.get(_pos[0], "")
        if not chunk:
            print(f"  ⚠️ CHART_pos{_pos[0]} AI 사진도 실패 — 슬롯 제거")
        return chunk

    result = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace, html)
    ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [{platform}] 구형식 슬롯 {ok}/{len(placeholders)}개 AI 사진 치환")
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
            # ★ 관련성 게이트 (2026-07-02): 그라운딩 소실 프롬프트는 발행 흐름으로
            #   넘기지 않음 → 폐기 후 theme+desc 기반 안전 프롬프트(prompt_ko)로 생성.
            #   재생성·LLM 재호출 없음(문자열 판정) → 레이턴시 0·루프 불가.
            try:
                from JARVIS00_INFRA.verification import verify_output, has_blocking
                _rel = verify_output("generate_photo_prompt", prompt_en or "",
                                     {"desc": desc_str, "theme": theme,
                                      "sector": sector, "facts": facts})
                if has_blocking(_rel):
                    _why = "; ".join(r.detail for r in _rel if r.severity == "block")
                    print(f"  ⚠️ PHOTO_{idx} 프롬프트 관련성 차단 → theme+desc 안전 폴백 ({_why})")
                    _g_report("image", RuntimeError(f"photo_prompt_relevance: {_why}"),
                              module=__name__)
                    prompt_en = ""   # 드리프트 프롬프트 폐기 → generate_photo가 prompt_ko 사용
            except Exception as _ve:
                log.debug(f"[PHOTO] 관련성 검증 스킵: {_ve}")
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

def process_draft(draft_html: str, collected, platform: str = "tistory",
                  out_dir: Path = None) -> dict:
    """★ v2 (Step 6, 2026-07-05) — 대본 HTML + CollectedData → 완성 블록.

    전 카테고리 공통 이미지 오케스트레이터. keyword/sector/category·검증 정답·
    차트 seed·이미지 컨텍스트를 *모두* CollectedData 단일 상자에서 파생.
    카테고리별 노브(min_images·chart_ai_fallback·thumbnail_body_chars)는
    CATEGORY_POLICY[category] 레지스트리에서 조회.

    Args:
        draft_html:  Pass-1 대본 (플레이스홀더 [CHART_N]/[PHOTO_N] 포함)
        collected:   JARVIS09 CollectedData (meta+datasets+docs+facts+entities) — 필수
        platform:    "tistory" | "naver"
        out_dir:     이미지 저장 폴더

    Returns:
        {"blocks": list[tuple], "thumbnail_path": str|None, "title": str, "html": str, "html_path": str}
    """
    from JARVIS09_COLLECTOR.models import policy_for
    if collected is None or not hasattr(collected, "all_numbers"):
        raise TypeError("process_draft: collected(CollectedData) 필수 — "
                        "호출자는 compose_collected/cand_collected 로 조립해 전달")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = collected.meta or {}
    theme = meta.get("keyword", "")
    sector = meta.get("sector", "")
    category = meta.get("category", "theme")
    policy = policy_for(category)
    min_images = int(policy.get("min_images", 5))
    chart_ai_fallback = bool(policy.get("chart_ai_fallback", True))
    thumb_chars = int(policy.get("thumbnail_body_chars", 3000))

    # 이미지 컨텍스트 = 근거 fact(어댑터) + 수집 문서 (fact-grounding 보존).
    #   facts_for_chart/facts_for_photo 가 그대로 소비 → 차트 수치·사진 장면 접지.
    context_docs = list(collected.docs or [])
    try:
        from JARVIS09_COLLECTOR.evidence_pack import as_source_docs
        _fact_docs = as_source_docs(collected.facts)
        if _fact_docs:
            context_docs = _fact_docs + context_docs
            print(f"  🧾 [{platform}] 근거 fact {len(_fact_docs)}개 → 이미지 컨텍스트 합류")
    except Exception as e:
        log.warning(f"근거 fact 합류 실패(무시): {e}")

    # ① [CHART_N] → 인포그래픽 (실패/구형식 슬롯도 실데이터 인포그래픽 우선, 소진 시 AI 사진)
    #   used_titles: 슬롯·top-up 이 *같은 dataset 을 중복* 시각화하지 않도록 공유 (ERRORS [364])
    _run_id = str(meta.get("run_id") or theme)
    _used_titles: set = set()
    html = _generate_charts(draft_html, theme, sector, collected, platform, out_dir,
                            chart_ai_fallback=chart_ai_fallback, context_docs=context_docs,
                            run_id=_run_id, used_titles=_used_titles)

    # ② [PHOTO_N] → AI 사진
    html = _generate_photos(html, theme, out_dir, sector=sector,
                            collection_docs=context_docs)

    # ★ min-N top-up 안전망 — 실데이터 인포그래픽 *우선* (사용자 박제 2026-07-05, ERRORS [364]):
    #   "실데이터(API+텍스트)는 항상 있다 → 인포그래픽 무조건 생성". collected.datasets
    #   (stocks_to_datasets+facts_to_datasets, 출처 박제)로 결정론 인포그래픽을 먼저 채우고,
    #   *데이터가 정말 소진됐을 때만* AI 사진 폴백. 경제·테마 등 전 카테고리 공통 경로.
    n_img = _count_images(html)
    if n_img < min_images:
        need = min_images - n_img
        print(f"  🖼️ [{platform}] 본문 이미지 {n_img} < 최소 {min_images} → 실데이터 인포그래픽 {need}개 우선 보충")
        infos = _extra_infographics(collected, out_dir, need, run_id=_run_id,
                                    platform=platform, html_so_far=html,
                                    used_titles=_used_titles)
        if infos:
            html = _insert_extra_photos(html, infos)
            n_img = _count_images(html)
            print(f"  📊 [{platform}] 실데이터 인포그래픽 {len(infos)}개 보충 완료 → 본문 이미지 {n_img}/{min_images}")
        # 실데이터가 *정말* 소진됐을 때만 AI 사진 (극단적 경우 — 데이터 0)
        if n_img < min_images:
            need2 = min_images - n_img
            print(f"  📸 [{platform}] 실데이터 소진 → AI 사진 {need2}개 보충 (최후)")
            extra = _extra_photos(theme, sector, need2, out_dir)
            if extra:
                html = _insert_extra_photos(html, extra)

    # ③ (옛 h2→이미지 교체 폐기) — 본문 이미지는 [CHART_N]/[PHOTO_N] 단일 경로만.

    # ④ 제목
    from JARVIS02_WRITER.tistory_html_writer import extract_title, save_article_html, screenshot_article
    title = extract_title(html, theme)

    # ⑤ HTML 저장
    html_path, _ = save_article_html(html, theme, platform=platform)

    # ⑥ SVG 캡처 → JPG
    print(f"  📸 [{platform}] SVG 캡처...")
    visual_paths = screenshot_article(html_path, str(out_dir))
    if not visual_paths:
        print(f"  ⚠️ [{platform}] 스크린샷 0개 — 텍스트 전용 진행")

    # ⑦ 썸네일 필수 (body 3000, 재시도 + 로컬 폴백 → 누락 0)
    body_text = re.sub(r"<[^>]+>", "", html)[:thumb_chars]
    # 하단 카테고리 라벨 — 카테고리별 고정 라벨, 미매칭 시 섹터/키워드
    _tag_line = _TAG_BY_CATEGORY.get((category or "").strip().lower()) or (sector or theme)
    thumbnail_path = _mandatory_thumbnail(title, theme, sector, platform, out_dir,
                                          body_text, tag_line=_tag_line)
    if thumbnail_path:
        print(f"  🖼️ [{platform}] 썸네일 생성 완료")
    else:
        print(f"  ⛔ [{platform}] 썸네일 최종 실패 (로컬 폴백까지 실패)")

    # ⑧ assemble_blocks → 완성 블록
    from JARVIS06_IMAGE.injectors import assemble_blocks
    blocks = assemble_blocks(html, visual_paths, out_dir=out_dir)

    print(f"  ✅ [{platform}] process_draft 완료 — 블록 {len(blocks)}개")
    return {
        "blocks":         blocks,
        "thumbnail_path": thumbnail_path,
        "title":          title,
        "html":           html,
        "html_path":      str(html_path),   # ★ Step 9: 경제 반환 계약 호환 (재저장 금지)
    }
