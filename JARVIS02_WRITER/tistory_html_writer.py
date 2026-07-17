"""JARVIS02_WRITER/tistory_html_writer.py
티스토리 경제 브리핑 — 2-pass 원고 HTML 생성기.

파이프라인:
  Pass-1.  sonnet CLI → 텍스트 + [CHART_N: 설명] 플레이스홀더 (SVG 없음, 빠름)
  Pass-2.  sonnet CLI × 8 병렬 → 플레이스홀더마다 SVG 1개 생성 → 치환
  Save.    output/html/{slug}/article.html 저장
  Capture. JARVIS06 → HTML에서 <svg> 추출 → JPG 캡처 → output/images/{slug}/
  Assemble. HTML(p/svg/h2) 순서 파싱 → svg를 JPG로 치환 → post_to_tistory blocks

공개 함수:
    generate_article_html(keyword, sector, reason, supreme_block) → str
    save_article_html(html, keyword) → (html_path, img_dir)
    screenshot_article(html_path, img_dir) → list[str]
    assemble_blocks(html, visual_paths) → list[tuple]
    extract_title(html, keyword) → str
    extract_text_content(html) → str  ← SVG 제거한 텍스트 body (post_to_tistory html_content용)
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
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# SVG Pass-2 전용 (Pass-1은 draft_writer 위임)
from JARVIS02_WRITER.draft_writer import (
    PLATFORM_SPEC as _PLATFORM_SPEC,
    strip_html_wrapper as _strip_html_wrapper,
    _gen_hook,
    _inject_missing_charts,
    _build_section_system_msg,
    _gen_economic_ts_nv as _generate_text_pass1,
    _gen_section_call1 as _generate_text_pass1_section_call1,
    _gen_section_call2 as _generate_text_pass1_section_call2,
    _gen_section_call3 as _generate_text_pass1_section_call3,
    _gen_economic_ts_nv_parallel as _generate_text_pass1_parallel,
    generate_economic_draft as _ts_gen_draft,
    _extract_chart_context,
    _stocks_text,  # backward-compat re-export (ERRORS [218][219][222])
)

try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 직접 실행 시

_TODAY     = date.today()
_TODAY_KR  = _TODAY.strftime("%Y년 %m월 %d일")
_TODAY_DOW = ["월", "화", "수", "목", "금", "토", "일"][_TODAY.weekday()]
_DATE_KEY  = _TODAY.strftime("%Y-%m-%d")

OUTPUT_HTML_DIR = _ROOT / "output" / "html"
OUTPUT_IMG_DIR  = _ROOT / "JARVIS06_IMAGE" / "output" / "images"   # JARVIS06 단일 진입점 (CLAUDE.md 규정)

# _PLATFORM_SPEC / _strip_html_wrapper / _gen_hook / Pass-1 함수들 →
# draft_writer.py 단일 진입점으로 이관 (위 import 참조)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. HTML 생성 (2-pass: Pass1은 draft_writer / Pass2 SVG×8 병렬)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ★ SVG 디자인 규정 = BLOG_SUPREME_LAW.md 제11조 <!-- svg:tistory --> 동적 로드
# 이 파일에서 수정 금지 — BLOG_SUPREME_LAW.md 만 수정
try:
    from JARVIS02_WRITER.law_enforcer import parse_svg_rules as _parse_svg
except ImportError:
    from law_enforcer import parse_svg_rules as _parse_svg

_SVG_DESIGN_RULES = "SVG 디자인 규칙:\n" + _parse_svg("tistory")


# _generate_text_pass1 → draft_writer._gen_economic_ts_nv 으로 이관됨
# 파일 상단 import 에서 _gen_economic_ts_nv as _generate_text_pass1 로 위임


_CHART_TYPE_POOL = [
    "가로 막대 차트 (horizontal bar)",
    "히트맵 격자 (heatmap grid)",
    "레이더·거미줄 차트 (radar/spider)",
    "누적 영역 차트 (stacked area)",
    "워터폴 차트 (waterfall)",
    "범프 차트 (bump/rank change)",
    "슬로프 차트 (slope graph)",
    "도넛 차트 + 범례 테이블",
    "타일 맵 / 트리맵 (treemap)",
    "점 플롯 + 오차막대 (dot plot)",
    "군집 막대 차트 (grouped bar)",
    "스텝 라인 차트 (step line)",
    "간트 바 타임라인 (Gantt bar)",
]


_PALETTE_POOL = [
    "blue-cyan navy (#1e3a5f / #3b82f6 / #06b6d4 accent)",
    "warm earth (terracotta #c2410c / amber #d97706 / cream #fef3c7)",
    "forest moss (#065f46 / #10b981 / #84cc16 accent)",
    "rose-burgundy (#9f1239 / #e11d48 / #fca5a5 accent)",
    "purple-violet (#5b21b6 / #8b5cf6 / #c4b5fd accent)",
    "slate monochrome (#0f172a / #475569 / #cbd5e1 accent)",
    "teal-emerald (#115e59 / #14b8a6 / #f0fdfa accent)",
    "amber-orange (#92400e / #f59e0b / #fef3c7 accent)",
    "indigo-sky (#312e81 / #6366f1 / #bae6fd accent)",
    "coral-peach (#9a3412 / #fb923c / #ffedd5 accent)",
]
_LAYOUT_POOL = [
    "horizontal split — left chart, right legend/table",
    "stacked vertical — title top, chart middle, summary bottom",
    "central focal — chart center, labels radiating outward",
    "grid 2x2 — quadrant comparison",
    "left-aligned title, right-aligned chart with floating labels",
    "card layout — chart inside rounded card with shadow",
    "circular composition — radial arrangement of data points",
    "asymmetric — large chart left 65%, narrow info column right 35%",
]
_VISUAL_TONE_POOL = [
    "minimalist clean — thin strokes, generous white space, sans-serif elegant",
    "data-dense editorial — Bloomberg/FT style, precise grid lines",
    "infographic playful — bold colors, rounded shapes, friendly icons",
    "documentary serious — muted palette, refined typography, subtle shadows",
    "high-contrast modern — sharp edges, vibrant accent, geometric purity",
    "soft pastel — rounded corners, gentle gradients, dreamy atmosphere",
]


def _generate_svg_pass2(
    chart_idx: int,
    description: str,
    keyword: str,
    sector: str,
    context_text: str = "",
    img_dir: "Path | str | None" = None,
    run_id: str = "",
    collection_docs: list | None = None,
    seed_datasets: list | None = None,
) -> str:
    """★ 구버전 Plotly 경로 폐기 (사용자 박제 2026-07-05 — ERRORS [355]).

    신형식 [CHART_N]...[/CHART_N] 슬롯은 slot_renderer → infographic_engine 이 처리.
    구형식 [CHART_N: text] 잔존 슬롯은 렌더 실패 시 빈 슬롯으로 남긴다.
    이 함수는 호환성을 위해 시그니처를 유지하나 항상 "" 반환.
    """
    return ""


def _generate_complete_article_cli(
    keyword: str, sector: str, reason: str,
    supreme_block: str,
    platform: str = "tistory",
    pass2: bool = True,
) -> str:
    """2-pass 원고 생성 오케스트레이터.

    Pass-1(sonnet): 텍스트 + [CHART_N: 설명] 플레이스홀더
    Pass-2(sonnet × 8 병렬): 각 플레이스홀더 → SVG 치환
    ★ pass2=False (경제 placeholder 모드): Pass-2 스킵 → 플레이스홀더 유지.

    Returns:
        str: "TITLE: ...\\nCONTENT: ..." 형식 원시 텍스트. 실패 시 빈 문자열.
    """
    # ── Pass-1: 텍스트 생성 ──────────────────────────────────
    raw1 = _generate_text_pass1(keyword, sector, reason, supreme_block, platform)
    if not raw1:
        print(f"  ❌ [Pass-1/{platform}] 텍스트 생성 실패")
        return ""

    # CONTENT 부분만 분리
    if "CONTENT:" in raw1:
        title_part, _, content_part = raw1.partition("CONTENT:")
    else:
        title_part = ""
        content_part = raw1

    # ── Pass-2: _generate_svg_pass2_and_replace 단일 진입점 위임 (pass2=False 면 스킵) ──
    content_final = content_part
    if pass2:
        content_final = _generate_svg_pass2_and_replace(content_part, keyword, sector, platform)
    return title_part + "CONTENT:" + content_final


def _generate_svg_pass2_and_replace(
    content: str,
    keyword: str,
    sector: str,
    platform: str = "tistory",
    collection_docs: list | None = None,
    ref_datasets: list | None = None,
) -> str:
    """Pass-2: 차트 슬롯 → 이미지 생성 + 치환.

    ★ 데이터 내장 슬롯 우선 (사용자 박제 2026-07-03): 자비스02 가 대본에
    [CHART_N]...[/CHART_N] 블록으로 차트 데이터 전체를 박아 옴 → 자비스06 은
    ref_datasets(자비스09 원본 — 검증 대조용) 대조 후 렌더만. 검증·렌더 실패
    슬롯은 구형식 [CHART_N: 제목] 으로 강등 → 빈 슬롯으로 남김
    (★ 본문 AI 사진 전면 폐기 2026-07-06 — 거짓/무관 이미지 < 이미지 없음).

    (구형식 [CHART_N: 설명] 슬롯 = 세션풀 기반 종전 경로 — 렌더 실패 시 빈 슬롯)

    Returns:
        str: 슬롯이 실제 이미지로 치환된 content (구형식 실패 슬롯은 빈 슬롯)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _img_dir_slot = OUTPUT_IMG_DIR / (f"economic_{platform}" if platform in ("tistory", "naver") else "economic_tistory")
    _img_dir_slot.mkdir(parents=True, exist_ok=True)

    # ── 0단계: ★ 데이터 내장 슬롯 렌더 (자비스06 = 렌더러) ─────────────────
    try:
        from JARVIS06_IMAGE.slot_renderer import render_slots_in_text
        import uuid as _uuid_s
        content, _slot_ok, _slot_total = render_slots_in_text(
            content, ref_datasets, _img_dir_slot,
            run_id=_uuid_s.uuid4().hex[:8], theme=keyword)
        if _slot_total:
            print(f"  🎨 [Pass-2/{platform}] 데이터 내장 슬롯 {_slot_ok}/{_slot_total}개 렌더")
    except Exception as _sre:
        print(f"  ⚠️ [Pass-2/{platform}] 내장 슬롯 처리 스킵: {_sre}")

    # 구형식 [CHART_N: 설명] 잔존 슬롯 — _generate_svg_pass2는 "" 반환(폐기), 렌더 실패 시 빈 슬롯
    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", content)
    if not placeholders:
        return content

    print(f"  ⚠️ [Pass-2/{platform}] 구형식 슬롯 {len(placeholders)}개 → 빈 슬롯 처리")
    _img_dir2 = OUTPUT_IMG_DIR / (f"economic_{platform}" if platform in ("tistory", "naver") else "economic_tistory")
    import uuid as _uuid2
    _run_id2 = _uuid2.uuid4().hex[:8]

    # ── 1단계: _generate_svg_pass2 호출 (현재 항상 "" 반환 — Plotly 경로 폐기) ──────────
    _items = [(pos, int(idx), desc.strip()) for pos, (idx, desc) in enumerate(placeholders, 1)]
    svg_map: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                _generate_svg_pass2,
                pos, desc, keyword, sector,
                _extract_chart_context(content, orig_idx),
                _img_dir2,
                _run_id2,
                collection_docs,
            ): pos
            for pos, orig_idx, desc in _items
        }
        for future in as_completed(futures):
            pos = futures[future]
            try:
                chart_html = future.result()
                svg_map[pos] = chart_html if chart_html else ""
                status = "✅" if chart_html else "⏭️"
                print(f"  {status} CHART_pos{pos} {'차트 완료' if chart_html else '차트 실패(데이터 없음)'}")
            except Exception as e:
                print(f"  ⚠️ CHART_pos{pos} 스레드 오류: {e}")
                svg_map[pos] = ""

    # ── 2단계: 차트 실패 슬롯 → 빈 슬롯 (★ 본문 AI 사진 전면 폐기 2026-07-06) ──
    #   렌더 실패 구형식 슬롯은 AI 사진으로 대체하지 않고 빈 슬롯으로 남긴다.
    #   본문 시각물은 인포그래픽 디자인만 사용 (거짓/무관 이미지 < 이미지 없음).

    # ── 플레이스홀더 치환 ─────────────────────────────────────────────────────
    _replace_pos = [0]

    def _replace_placeholder(m: re.Match) -> str:
        pos = _replace_pos[0] + 1
        _replace_pos[0] += 1
        return svg_map.get(pos, "")

    content_final = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace_placeholder, content)

    total_ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [Pass-2/{platform}] 이미지 {total_ok}/{len(placeholders)}개 치환 (구형식 실패 슬롯은 빈 슬롯)")

    return content_final


def generate_article_html(
    keyword: str,
    sector: str,
    reason: str,
    supreme_block: str,
    platform: str = "tistory",
    collection_docs: list | None = None,
    ref_datasets: list | None = None,
    gate_feedback: list | None = None,
    pass2: bool = True,
) -> str:
    """2-pass Claude Code SDK → 텍스트 + inline SVG 완성 원고 HTML.

    Pass-1(sonnet): 텍스트 + 플레이스홀더
    Pass-2(sonnet × 병렬): SVG 치환
    platform: "tistory" | "naver"

    ★ Step 9 (2026-07-05): pass2=False 면 Pass-2 스킵 → 플레이스홀더 HTML 반환
      (경제도 JARVIS06 process_draft 단일 이미지 경로 사용 — placeholder-first).

    Returns:
        str: 완전한 HTML 문서. 실패 시 빈 문자열.
    """
    # ★ 게이트 차단 사유 주입 (ERRORS [311]) — supreme_block 합류로 병렬·CLI 폴백
    #   모든 Pass-1 변형이 자동 상속 (재작성 시 같은 창작 수치 재생산 방지)
    if gate_feedback:
        from JARVIS02_WRITER.draft_writer import build_gate_feedback_block as _gfb
        supreme_block = (supreme_block or "") + _gfb(gate_feedback)
        print(f"  🔁 [Pass-1/{platform}] 직전 차단 사유 {len(gate_feedback)}건 주입 — 재작성")

    # Pass-1: 1회 단일 호출(설계-우선) 기본, 실패 시 3섹션 순차 폴백
    raw = _generate_text_pass1(keyword, sector, reason, supreme_block, platform, ref_datasets)
    # ★ 스로틀 인지 (전수감사 FIX[3]): pass1 이 인프라 사유(스로틀 절단/hang/회로 open)로 빈
    #   문자열이면 폴백 체인(parallel 최대 6콜 + CLI, writer 는 회로 면제라 open 중에도 spawn)이
    #   같은 스로틀 창에 rate-limit 을 자가증폭한다. 인프라면 즉시 '' 반환 → 상류가 *신선한* 신호로
    #   infra_throttle 태깅 → harness defer/backoff. 콘텐츠 결함(신호 없음)일 때만 폴백 유지.
    if not raw:
        from shared.llm import last_call_infra_incomplete as _infra, circuit_is_open as _copen
        if _infra() or _copen():
            print("  ⏸ [Pass-1 단일] 인프라 스로틀 감지 → 폴백 체인 스킵(자가증폭 차단), 상류 defer 위임")
            return ""
    if not raw:
        print("  ⚠️ [Pass-1 단일] 실패 → 3섹션 순차 재시도...")
        raw = _generate_text_pass1_parallel(keyword, sector, reason, supreme_block, platform, ref_datasets)
    if not raw:
        raw = _generate_complete_article_cli(keyword, sector, reason, supreme_block, platform, pass2=pass2)
    if not raw:
        print("  ❌ [2-pass] 원고 생성 실패")
        return ""

    # TITLE / CONTENT 파싱
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
                f"{keyword}, 지금 왜 이렇게 주목받는 건가요?"

    # ★ Pass-2: [CHART_N: ...] 플레이스홀더 → SVG 치환. pass2=False 면 스킵(placeholder 유지).
    if pass2:
        content = _generate_svg_pass2_and_replace(content, keyword, sector, platform,
                                                  collection_docs=collection_docs,
                                                  ref_datasets=ref_datasets)

    kc = _L.count(content)
    svg_count = len(re.findall(r"<svg[\s>]", content, re.IGNORECASE))
    p_tags = re.findall(r"<p[^>]*>.*?</p>", content, re.DOTALL | re.IGNORECASE)
    sent_count = sum(
        len(re.findall(r'[.!?。]\s*(?=[^<]|$)', re.sub(r"<[^>]+>", "", p)))
        for p in p_tags
    )
    print(f"  ✅ [2-pass] 완성 원고 ({kc}자, SVG {svg_count}개, 약 {sent_count}문장)")

    # ── 문장수 미달 시 1회 재시도 (SVG는 Pass-2 보장이므로 미포함) ──────────
    #   ★ pass2=False(경제 placeholder 모드)에서는 스킵 — _generate_complete_article_cli 가
    #     Pass-2 를 내부 수행하므로 placeholder 계약 위반. 문장 미달은 harness verify 루프가 처리.
    if sent_count < _L.MIN_SENTENCES_THRESHOLD and pass2:
        print(f"  ⚠️ [검증 실패] 문장 {sent_count}<{_L.MIN_SENTENCES_THRESHOLD} — 재생성 시도...")
        try:
            from shared.notify import send_tg as _tg_warn
            _tg_warn(f"⚠️ [{platform}] 원고 문장 미달 ({sent_count}개) → 재생성 중...")
        except Exception:
            pass
        # ★ 재생성 전 이전 차트 파일 정리 (Pass-2가 또 실행되므로 누적 방지)
        _img_dir_regen = OUTPUT_IMG_DIR / (f"economic_{platform}" if platform in ("tistory", "naver") else "economic_tistory")
        _removed_regen = 0
        for _ext in ("*.png", "*.jpg", "*.svg"):
            for _f in _img_dir_regen.glob(_ext):
                try:
                    _f.unlink(missing_ok=True)
                    _removed_regen += 1
                except (OSError, PermissionError):
                    pass
        if _removed_regen:
            print(f"  🧹 [재생성] 이전 차트 {_removed_regen}개 정리 완료")
        raw2 = _generate_complete_article_cli(keyword, sector, reason, supreme_block, platform)
        if raw2:
            parts2 = raw2.split("CONTENT:", 1)
            content2 = parts2[1].strip() if len(parts2) > 1 else raw2
            kc2 = _L.count(content2)
            svg2 = len(re.findall(r"<svg[\s>]", content2, re.IGNORECASE))
            p2   = re.findall(r"<p[^>]*>.*?</p>", content2, re.DOTALL | re.IGNORECASE)
            sc2  = sum(len(re.findall(r'[.!?。]\s*(?=[^<]|$)', re.sub(r"<[^>]+>", "", p))) for p in p2)
            print(f"  🔄 [재생성] {kc2}자, SVG {svg2}개, 약 {sc2}문장")
            if sc2 > sent_count:
                content = content2
                kc, svg_count, sent_count = kc2, svg2, sc2
                title_m = re.search(r"TITLE:\s*(.+)", raw2)
                if title_m:
                    title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
                print(f"  ✅ [재생성 채택] {kc}자, SVG {svg_count}개, 약 {sent_count}문장")
            else:
                print(f"  ⚠️ [재생성 미채택] 원본 유지")

    # ── ★ 최종 구조 게이트 (ERRORS [381] 보강) — 스로틀 절단 응답 차단 ──
    #   비어있지 않아도 <p>/<h*> 구조·본문 길이가 없으면 하류에서 '텍스트 블록 없음/본문 0자'
    #   3중 오류를 낸다. 여기서 생성 실패로 판정 → 호출자 draft_failed 로 재생성.
    from JARVIS02_WRITER.draft_writer import has_publishable_body
    if not has_publishable_body(content):
        print(f"  ❌ [HTML Writer/{platform}] Pass-1 본문 구조 미달(스로틀 절단 추정) — 생성 실패로 재생성 위임")
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
svg {{ display: block; margin: 16px 0; border-radius: 8px; }}
</style>
</head>
<body>
{content}
</body>
</html>"""

    print(f"  ✅ [HTML Writer] HTML 완성 — 제목: {title}")
    return html


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. HTML 저장
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def save_article_html(html: str, keyword: str, platform: str = "") -> tuple:
    """HTML 파일 저장.

    Args:
        html: 원고 HTML
        keyword: 키워드 (slug 생성용)
        platform: "tistory" | "naver" (플랫폼별 이미지 폴더 사용)

    Returns:
        (html_path: str, img_dir: str)
    """
    # slug는 ASCII-only (한글 포함 시 ChromeDriver File not found 오류)
    kw_hash  = hashlib.md5(keyword.encode()).hexdigest()[:8]
    slug     = f"{_DATE_KEY}_{kw_hash}"
    html_dir = OUTPUT_HTML_DIR / slug

    # 플랫폼별 이미지 폴더 사용 (새 폴더 생성 X)
    if platform == "tistory":
        from JARVIS06_IMAGE import image_agent
        img_dir = image_agent.OUTPUT_DIR / 'images' / 'economic_tistory'
    elif platform == "naver":
        from JARVIS06_IMAGE import image_agent
        img_dir = image_agent.OUTPUT_DIR / 'images' / 'economic_naver'
    else:
        img_dir = OUTPUT_IMG_DIR / slug

    html_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)

    # ★ 발행 도중 이미지 삭제 금지 (ERRORS [291] — 2026-07-03): 인포그래픽 엔진(2026-06-30)이
    #   차트를 .jpg 로 출력하면서, 옛 "JPG=SVG 스크린샷" 가정의 일괄 삭제가 Pass-2 인포그래픽
    #   전량을 렌더 직후 파괴 → image-validate 누락 → 제4조 위반 순환. 폴더 리셋은 draft 시작 시
    #   _cleanup_*_images() 가 담당 — 여기서는 *본문이 참조하지 않는* 잔재만 제거.
    for old_f in img_dir.glob("*.jpg"):
        if old_f.name in html or str(old_f) in html:
            continue   # 본문 참조 이미지 — 삭제 금지
        old_f.unlink(missing_ok=True)
    old_html = html_dir / "article.html"
    if old_html.exists():
        old_html.unlink()

    html_path = html_dir / "article.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  💾 HTML 저장: {html_path}")
    return str(html_path), str(img_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. SVG 캡처 → JPG (JARVIS06 단일 진입점)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def screenshot_article(html_path: str, img_dir: str) -> list:
    """완성 원고 HTML에서 inline SVG만 추출 → JARVIS06이 JPG로 캡처·저장.

    1-pass HTML에 포함된 <svg> 블록을 JARVIS06에 위임.
    cairosvg(1순위) → Selenium headless(폴백) 체인으로 JPG 변환.
    저장 폴더: output/images/{slug}/ (JARVIS06 관리)

    Args:
        html_path: save_article_html()이 반환한 HTML 파일 경로
        img_dir:   JARVIS06이 관리하는 이미지 저장 폴더

    Returns:
        list[str]: JPG 경로 목록 (HTML내 SVG 출현 순서)
    """
    html_content = Path(html_path).read_text(encoding="utf-8")
    from JARVIS06_IMAGE.html_screenshotter import screenshot_svg_blocks
    return screenshot_svg_blocks(html_content, img_dir)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 텍스트 콘텐츠 추출 (post_to_tistory html_content용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_text_content(html: str) -> str:
    """1-pass HTML body에서 SVG 제거 → 텍스트 전용 HTML 반환.

    post_to_tistory(html_content=...) 파라미터용.
    """
    body_m = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
    inner = body_m.group(1) if body_m else html
    return re.sub(r"<svg[\s\S]*?</svg>", "", inner, flags=re.IGNORECASE).strip()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. 블록 조립 — ADR 008 Phase 1 (사용자 박제 2026-05-17)
#     본체는 JARVIS06_IMAGE/injectors/block_assembler.py 단일 진입점.
#     호출자는 `from JARVIS06_IMAGE.injectors import assemble_blocks` 권장.
#     아래 import 는 backward-compat 만 유지 — 신규 호출 금지.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from JARVIS06_IMAGE.injectors.block_assembler import assemble_blocks  # noqa: F401


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. 제목 추출 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_title(html: str, keyword: str) -> str:
    """HTML의 <title> 또는 첫 <h1>/<h2>에서 제목 추출."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if m:
        t = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if t and len(t) < 60:
            return t

    m2 = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL | re.IGNORECASE)
    if m2:
        t = re.sub(r"<[^>]+>", "", m2.group(1)).strip()
        if t:
            return t

    return f"{keyword}, 지금 왜 이렇게 주목받는 건가요?"


# Pass-1 함수들은 draft_writer.py 단일 진입점으로 이관됨
# _inject_missing_charts, _build_section_system_msg,
# _generate_text_pass1_section_call1/2/3, _generate_text_pass1_parallel
# → draft_writer.py 에서 import (파일 상단 참조)
