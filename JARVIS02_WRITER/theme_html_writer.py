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

    # ── ★ 최종 구조 게이트 (ERRORS [381] 보강) — 스로틀 절단 응답 차단 ──
    #   비어있지 않아도 <p>/<h*> 구조·본문 길이가 없으면 하류에서 '텍스트 블록 없음/본문 0자'
    #   3중 오류(#2120-2122)를 낸다. 여기서 생성 실패로 판정 → 호출자 draft_failed 로 재생성.
    from JARVIS02_WRITER.draft_writer import has_publishable_body
    if not has_publishable_body(content):
        print(f"  ❌ [Theme/{platform}] Pass-1 본문 구조 미달(스로틀 절단 추정) — 생성 실패로 재생성 위임")
        return ""

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
