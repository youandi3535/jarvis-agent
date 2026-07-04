"""JARVIS02_WRITER/theme_html_writer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
테마주 — 2-pass HTML 원고 생성기 (tistory_html_writer 패턴 그대로).

차이점: ①단계 입력만 다름.
  - 경제 트렌드: market = 시장 데이터 dict
  - 테마주:     stocks_data = {"theme", "stocks": [...], "summary": {...}}

재사용 (tistory_html_writer 에서 import):
  - save_article_html         HTML 저장 (플랫폼별 폴더)
  - screenshot_article        JARVIS06 SVG → JPG 캡처
  - assemble_blocks           블록 조립 (p/svg/h2 인터리빙)
  - extract_title             제목 추출
  - extract_text_content      텍스트 추출 (SVG 제거)

신설:
  - generate_theme_html(collected, supreme_block, platform)   # ★ Step 7 — collected 단일 소스
    → tistory_html_writer.generate_article_html 의 테마 버전
"""
from __future__ import annotations

import re
import sys
import hashlib
from datetime import date
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L

# tistory_html_writer 의 헬퍼 재사용 (저장·캡처·추출 — assemble_blocks 는 JARVIS06)
from JARVIS02_WRITER.tistory_html_writer import (
    save_article_html,
    screenshot_article,
    extract_title,
    extract_text_content,
    OUTPUT_IMG_DIR as _THEME_IMG_BASE,
    _generate_svg_pass2,
)
# Pass-1 대본 생성 단일 진입점 (draft_writer.py)
from JARVIS02_WRITER.draft_writer import (
    _PLATFORM_SPEC,
    _strip_html_wrapper,
    _fmt_marcap,
    _fmt_price,
    _fmt_pct,
    _stocks_text,
    _gen_hook_theme,
    generate_theme_draft as _generate_text_pass1_theme,
    _extract_chart_context,
)
# ADR 008 Phase 1 — 이미지 도메인 단일 진입점
from JARVIS06_IMAGE.injectors import assemble_blocks

_TODAY     = date.today()
_TODAY_KR  = _TODAY.strftime("%Y년 %m월 %d일")
_TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][_TODAY.weekday()]
_DATE_KEY  = _TODAY.strftime("%Y-%m-%d")


# _fmt_marcap, _fmt_price, _fmt_pct, _stocks_text → draft_writer.py 단일 진입점으로 이관됨


# _gen_hook_theme, _generate_text_pass1_theme → draft_writer.py 단일 진입점으로 이관됨
# generate_theme_draft as _generate_text_pass1_theme 으로 import (파일 상단 참조)


def _generate_svg_pass2_and_replace_theme(
    content: str,
    theme: str,
    sector: str,
    stocks_data: dict,
    platform: str = "tistory",
) -> str:
    """Pass-2: [CHART_N: ...] SVG 병렬 생성 + 치환.

    모든 시각 자료는 SVG 차트 (그래프 종류는 LLM 이 본문 컨텍스트 보고 자유 선택).
    AI 사진은 *대표 썸네일·도입부* 용으로 jarvis_main 단계에서 별도 처리.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stocks_text = _stocks_text(stocks_data)

    chart_placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", content)
    if not chart_placeholders:
        return content

    print(f"  🎨 [Theme/Pass-2/{platform}] 차트 {len(chart_placeholders)}개 생성 (matplotlib)...")
    _timg = _THEME_IMG_BASE / f"theme_{platform}"

    # 글 1건당 run_id 1회 생성 — 같은 글의 차트는 색상 계열 통일, 다음 글은 달라짐
    import uuid as _uuid
    _run_id = _uuid.uuid4().hex

    # ★ ERRORS [171] 2026-05-26: 위치 기반 pos 사용 — LLM 중복 idx 덮어쓰기 방지
    _items_t = [(pos, int(idx), desc.strip()) for pos, (idx, desc) in enumerate(chart_placeholders, 1)]
    svg_map: dict[int, str] = {}  # key = pos
    with ThreadPoolExecutor(max_workers=4) as ex:
        def _ctx(orig_idx: int) -> str:
            para = _extract_chart_context(content, orig_idx)
            return f"{para}\n\n[종목 데이터]\n{stocks_text}" if para else f"[종목 데이터]\n{stocks_text}"
        futs = {
            ex.submit(_generate_svg_pass2, pos, desc, theme, sector, _ctx(orig_idx), _timg, _run_id): pos
            for pos, orig_idx, desc in _items_t
        }
        for f in as_completed(futs):
            pos_key = futs[f]
            try:
                chart_html = f.result()
                svg_map[pos_key] = chart_html if chart_html else ""
                print(f"  {'✅' if chart_html else '❌'} CHART_pos{pos_key} matplotlib {'완료' if chart_html else '실패'}")
            except Exception as e:
                print(f"  ⚠️ CHART_pos{pos_key} 스레드 오류: {e}")
                _g_report("writer", e, module=__name__)
                svg_map[pos_key] = ""

    _replace_pos_t = [0]

    def _replace_chart(m: re.Match) -> str:
        pos_key = _replace_pos_t[0] + 1
        _replace_pos_t[0] += 1
        html_chunk = svg_map.get(pos_key, "")
        if html_chunk:
            return html_chunk
        print(f"  ⚠️ CHART_pos{pos_key} 차트 실패 — placeholder 제거")
        return ""

    content_final = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace_chart, content)
    chart_ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [Theme/Pass-2/{platform}] 차트 {chart_ok}/{len(chart_placeholders)}개 치환")
    return content_final


def _inject_theme_section_images(html: str, theme: str, sector: str, platform: str = 'tistory') -> str:
    """테마주 h2 소제목을 섹션 배경 이미지로 교체.

    경제 브리핑의 _inject_section_images 패턴을 테마주에 적용.
    각 섹션 제목별로 AI 배경 이미지 생성 → h2 교체 (이미지가 소제목 역할).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def make_section_ai_image(section_title: str, section_num: int, keyword: str) -> str:
        """섹션 배경 AI 이미지 생성."""
        try:
            from JARVIS06_IMAGE.image_agent import generate_photo
            # 섹션 제목을 바탕으로 AI 배경 이미지 생성
            prompt_ko = f"{keyword}: {section_title} 섹션 배경 이미지"
            out_dir = Path(__file__).parent.parent / 'JARVIS06_IMAGE' / 'output' / 'images' / 'theme_section'
            out_dir.mkdir(parents=True, exist_ok=True)
            img_path = generate_photo(prompt_ko=prompt_ko, out_dir=str(out_dir))
            return img_path
        except Exception as e:
            print(f"  ⚠️ 섹션 '{section_title}' 이미지 생성 실패: {e}")
            return None

    # h2 수집
    h2_matches = list(re.finditer(r'<h2[^>]*>(.*?)</h2>', html, re.DOTALL))
    if not h2_matches:
        return html

    print(f"  🖼️ [Theme/{platform}] 섹션 이미지 {len(h2_matches)}개 순차 생성 시작...")

    section_imgs = {}
    # 순차 이미지 생성 — 외부 API rate limit 방지 (사용자 박제 2026-05-18)
    # generate_photo → Pollinations.ai (★ Bing/HF 폐기 2026-06-07 — ERRORS [263]).
    with ThreadPoolExecutor(max_workers=1) as ex:
        futs = {
            ex.submit(make_section_ai_image,
                     re.sub(r'<[^>]+>', '', m.group(1)).strip(),
                     i+1, theme): i
            for i, m in enumerate(h2_matches)
        }
        for f in as_completed(futs):
            i = futs[f]
            try:
                img_path = f.result()
                if img_path and Path(img_path).exists():
                    section_imgs[i] = img_path
                    print(f"  ✅ 섹션 이미지 {i+1}: {Path(img_path).name}")
            except Exception as e:
                print(f"  ⚠️ 섹션 {i+1} 이미지 오류: {e}")

    # h2 → 이미지 교체 (이미지가 소제목 역할 — h2 태그 제거)
    h2_idx = [0]
    def replace_h2(match):
        idx = h2_idx[0]
        h2_idx[0] += 1
        if idx in section_imgs:
            title_text = re.sub(r'<[^>]+>', '', match.group(1)).strip()
            img_path = section_imgs[idx]
            # 이전 섹션과 구분: 2줄 여백 추가 (CLAUDE.md 소제목 간격 규정)
            return (f'<p>&nbsp;</p><p>&nbsp;</p>'
                   f'<figure><img src="{img_path}" alt="{title_text}" /></figure>')
        return match.group(0)

    result = re.sub(r'<h2[^>]*>(.*?)</h2>', replace_h2, html, flags=re.DOTALL)
    print(f"  ✅ [Theme/{platform}] 섹션 이미지 {len(section_imgs)}개 치환 완료")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 진입점 — generate_theme_html (tistory_html_writer.generate_article_html 의 테마 버전)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_theme_html(
    collected,
    supreme_block: str,
    platform: str = "tistory",
    gate_feedback: list | None = None,
) -> str:
    """테마주 Pass-1 대본 생성 — 텍스트 + [CHART_N]/[PHOTO_N] 플레이스홀더.

    ★ Step 7 (2026-07-05): collected(CollectedData) 단일 소스. 프롬프트 빌더가
      쓰는 종목 dict 는 collected.meta['raw_stocks'] 에서 무손실 복원(품질 유지).
    ★ 이미지 생성(Pass-2)은 JARVIS06.draft_processor.process_draft 단독 담당.
      이 함수는 플레이스홀더가 포함된 HTML만 반환.

    Returns:
        str: 플레이스홀더 포함 HTML. 실패 시 빈 문자열.
    """
    theme = collected.meta.get("keyword", "")
    sector = collected.meta.get("sector", "")
    stocks_data = collected.meta.get("raw_stocks") or {}
    collection_docs = list(collected.docs or [])
    evidence_pack = ({"facts": collected.facts, "plan": {},
                      "created_at": collected.meta.get("as_of", ""), "theme": theme}
                     if collected.facts else None)
    raw = _generate_text_pass1_theme(platform, theme, sector, stocks_data, supreme_block,
                                     collection_docs=collection_docs or [],
                                     evidence_pack=evidence_pack,
                                     gate_feedback=gate_feedback)
    if not raw:
        print(f"  ❌ [Theme/{platform}] Pass-1 텍스트 생성 실패")
        return ""

    title = ""
    content = raw
    if "TITLE:" in raw:
        parts = raw.split("CONTENT:", 1)
        title_part = parts[0].replace("TITLE:", "").strip()
        title = re.sub(r"<[^>]+>", "", title_part).strip()
        content = parts[1].strip() if len(parts) > 1 else raw
    if not title:
        h2_m = re.search(r"<h2[^>]*>(.*?)</h2>", content, re.DOTALL | re.IGNORECASE)
        title = re.sub(r"<[^>]+>", "", h2_m.group(1)).strip() if h2_m else \
                f"{theme}, 지금 왜 주목해야 할까요?"

    # ── 문장수 검증·재시도 ─────────────────────────────────
    p_tags = re.findall(r"<p[^>]*>.*?</p>", content, re.DOTALL | re.IGNORECASE)
    sent_count = sum(
        len(re.findall(r'[.!?。]\s*(?=[^<]|$)', re.sub(r"<[^>]+>", "", p)))
        for p in p_tags
    )
    kc = _L.count(content)
    print(f"  ✅ [Theme/{platform}] Pass-1 완성 ({kc}자, ~{sent_count}문장, 플레이스홀더 포함)")

    if sent_count < _L.MIN_SENTENCES_THRESHOLD:
        print(f"  ⚠️ [Theme/{platform}] 문장 {sent_count}<{_L.MIN_SENTENCES_THRESHOLD} — 재생성 1회 시도")
        try:
            raw2 = _generate_text_pass1_theme(platform, theme, sector, stocks_data, supreme_block,
                                              collection_docs=collection_docs or [],
                                              evidence_pack=evidence_pack,
                                              gate_feedback=gate_feedback)
            if raw2:
                parts2 = raw2.split("CONTENT:", 1)
                content2 = parts2[1].strip() if len(parts2) > 1 else raw2
                p2 = re.findall(r"<p[^>]*>.*?</p>", content2, re.DOTALL | re.IGNORECASE)
                sc2 = sum(len(re.findall(r'[.!?。]\s*(?=[^<]|$)', re.sub(r"<[^>]+>", "", p))) for p in p2)
                if sc2 > sent_count:
                    content = content2
                    print(f"  ✅ [Theme/{platform}] 재생성 채택: ~{sc2}문장")
        except Exception as e:
            print(f"  ⚠️ 재생성 실패: {e}")
            _g_report("writer", e, module=__name__)

    # ── ★ 자기비평 1패스 (ADR 012) — 구조 보존 가드 내장, 실패 시 원본 유지 ──
    try:
        from JARVIS02_WRITER.draft_writer import critique_and_refine, _build_evidence_block
        content = critique_and_refine(
            content, platform,
            evidence_block=_build_evidence_block(evidence_pack),
            post_type="theme",
        )
    except Exception as e:
        print(f"  ⚠️ [Theme/{platform}] 비평 패스 스킵: {e}")
        _g_report("writer", e, module=__name__)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ max-width: 800px; background: #fff;
       font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', 'NanumGothic', sans-serif;
       padding: 20px; color: #1a1a2e; line-height: 1.8; }}
h2 {{ font-size: 20px; margin: 28px 0 12px; color: #1a1a2e; }}
p  {{ font-size: 16px; margin-bottom: 12px; }}
</style>
</head>
<body>
{content}
</body>
</html>"""

    print(f"  ✅ [Theme/{platform}] Pass-1 HTML — 제목: {title}")
    return html


__all__ = [
    "generate_theme_html",
    "save_article_html",       # tistory_html_writer 위임 (재export)
    "screenshot_article",
    "assemble_blocks",         # JARVIS06_IMAGE.injectors 위임 (재export — backward compat)
    "extract_title",
    "extract_text_content",
]
