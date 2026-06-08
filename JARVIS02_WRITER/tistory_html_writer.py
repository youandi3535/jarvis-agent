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
) -> str:
    """차트 1개 단일 진입점 — chart_generator 성공 시 img 태그 반환, 실패 시 "" (스킵).

    run_id: _generate_svg_pass2_and_replace 가 글 단위로 단 1회 생성해서 전달.
            일관된 run_id → chart_generator 내부 _used_types_by_run 스타일 중복 방지 작동.
    collection_docs: ★ 사용자 박제 2026-06-07 — JARVIS09 수집물.
            chart_generator 가 부족 시 delta 보강 요청에 활용.
    폴백 없음 — 실데이터 없으면 차트 스킵 (CLAUDE.md ★ 규정, ERRORS [44][70]...[182] 10회 박제).
    """
    from JARVIS06_IMAGE.chart_generator import generate_chart as _gen_chart

    _dir = Path(img_dir) if img_dir else (OUTPUT_IMG_DIR / "economic_tistory")
    _dir.mkdir(parents=True, exist_ok=True)

    jpg_path = _gen_chart(
        description=description,
        keyword=keyword,
        sector=sector,
        context_text=context_text,
        out_dir=_dir,
        chart_idx=chart_idx,
        run_id=run_id,
        collection_docs=collection_docs,
    )
    if jpg_path:
        alt = description[:40].replace('"', "'")
        return (f'<p><img src="{jpg_path}" alt="{alt}" '
                f'style="width:100%;max-width:760px;border-radius:8px;'
                f'margin:16px auto;display:block;"></p>')
    return ""


def _generate_complete_article_cli(
    keyword: str, sector: str, reason: str,
    supreme_block: str,
    platform: str = "tistory",
) -> str:
    """2-pass 원고 생성 오케스트레이터.

    Pass-1(sonnet): 텍스트 + [CHART_N: 설명] 플레이스홀더
    Pass-2(sonnet × 8 병렬): 각 플레이스홀더 → SVG 치환

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

    # ── Pass-2: _generate_svg_pass2_and_replace 단일 진입점 위임 ──
    # run_id 생성·스타일 중복 방지·차트 스킵 로직 모두 거기서 관리.
    content_final = _generate_svg_pass2_and_replace(content_part, keyword, sector, platform)
    return title_part + "CONTENT:" + content_final


_MIN_IMAGES = 8  # 썸네일 제외 본문 최소 이미지 수


def _ai_photo_html(path: "Path | str", alt: str) -> str:
    return (f'<p><img src="{path}" alt="{alt}" '
            f'style="width:100%;max-width:760px;border-radius:8px;'
            f'margin:16px auto;display:block;"></p>')


def _generate_ai_photo_for_slot(description: str, keyword: str, out_dir: "Path") -> str:
    """차트 실패 슬롯 → AI 사진 1장. 해당 슬롯 description 을 프롬프트로 사용."""
    try:
        from JARVIS06_IMAGE.image_agent import generate_photo as _gp
        import random as _rand, datetime as _dt
        # 날짜 + 랜덤 관점 seed → 같은 description이라도 매 발행마다 다른 이미지
        _perspectives = [
            "사실적인 사무실·도시 배경, 전문적",
            "데이터 센터·서버실 배경, 첨단 기술",
            "주식 트레이딩룸 배경, 활기찬 분위기",
            "글로벌 비즈니스 미팅, 차트 화면",
            "한국 도심 금융가, 저녁노을",
            "기업 연구개발 현장, 밝은 조명",
            "무역항·물류센터, 활발한 움직임",
        ]
        _today = _dt.date.today().isoformat()
        _perspective = _rand.choice(_perspectives)
        prompt_ko = f"{keyword} — {description} ({_today}, {_perspective})"
        path = _gp(prompt_ko=prompt_ko, out_dir=out_dir)
        if path:
            return _ai_photo_html(path, description[:40].replace('"', "'"))
    except Exception as e:
        print(f"  ⚠️ AI 사진(슬롯) 실패: {e}")
    return ""


def _generate_extra_ai_photos(keyword: str, sector: str, count: int, out_dir: "Path") -> list:
    """최소 이미지 수 충족 목적 추가 AI 사진. 글 주제에 맞는 다양한 관점으로 생성."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    try:
        from JARVIS06_IMAGE.image_agent import generate_photo as _gp
    except ImportError:
        return []

    import random as _rand2, datetime as _dt2
    _today2 = _dt2.date.today().isoformat()
    # 날짜를 seed에 포함시켜 매일 다른 프롬프트 조합 선택
    _rng = _rand2.Random(hash(f"{keyword}|{_today2}"))
    _base_prompts = [
        f"{keyword} 산업 현황과 미래 전망, 활기찬 비즈니스 현장",
        f"{keyword} 관련 주식 시장 분석, 트레이딩 화면과 차트",
        f"{sector} 섹터 투자 트렌드, 현대적인 오피스",
        f"{keyword} 기업 성장 동력, 연구개발 현장",
        f"{keyword} 글로벌 시장 경쟁, 항공사진 도시 전경",
        f"{sector} 혁신 기술 현장, 스마트 팩토리",
        f"{keyword} 공급망 현황, 물류 허브",
        f"{keyword} 투자 포인트, 한국 증권가 야경",
        f"{keyword} 미래 성장 산업, 첨단 기술 연구소",
        f"{sector} 시장 경쟁 구도, 도심 금융지구",
        f"{keyword} 소비자 트렌드, 활기찬 시장 거리",
        f"{keyword} 정책 변화 영향, 국회의사당·정부청사",
    ]
    _rng.shuffle(_base_prompts)
    prompts = _base_prompts[:count]
    results: dict[int, str] = {}

    # ★ JARVIS06 CLAUDE.md 규정: 외부 이미지 API 순차 실행 (max_workers=1 강제)
    # 병렬 실행(max_workers≥2) 시 Pollinations 429 오류 전부 실패 직결
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(_gp, prompt_ko=p, out_dir=out_dir): (i, p)
            for i, p in enumerate(prompts)
        }
        for future in as_completed(futures):
            idx, prompt = futures[future]
            try:
                path = future.result()
                if path:
                    results[idx] = _ai_photo_html(path, prompt[:40].replace('"', "'"))
                    print(f"  🖼️ 추가 AI 사진 {idx+1}/{count} 완료")
            except Exception as e:
                print(f"  ⚠️ 추가 AI 사진 {idx+1} 실패: {e}")

    return [results[i] for i in sorted(results)]


def _insert_extra_photos(content: str, photos: list) -> str:
    """추가 AI 사진을 이미지 없는 h2/h3 섹션에 배포하고 나머지는 말미에 추가."""
    if not photos:
        return content

    h_matches = list(re.finditer(r'<h[23][^>]*>.*?</h[23]>', content, re.IGNORECASE | re.DOTALL))
    if not h_matches:
        return content + "\n" + "\n".join(photos)

    # 이미지 없는 섹션 끝 위치 수집
    img_free_ends: list[int] = []
    for i, m in enumerate(h_matches):
        end = h_matches[i + 1].start() if i + 1 < len(h_matches) else len(content)
        section = content[m.start():end]
        if "<img" not in section and "<figure" not in section and "<svg" not in section:
            img_free_ends.append(end)

    # 역순으로 삽입 (뒤에서 앞으로 — 오프셋 변화 영향 없음)
    result = content
    slots_used = min(len(img_free_ends), len(photos))
    for pos, photo in zip(reversed(img_free_ends[:slots_used]), reversed(photos[:slots_used])):
        result = result[:pos] + "\n" + photo + "\n" + result[pos:]

    # 남은 사진은 말미에 추가
    remaining = photos[slots_used:]
    if remaining:
        result += "\n" + "\n".join(remaining)

    return result


def _generate_svg_pass2_and_replace(
    content: str,
    keyword: str,
    sector: str,
    platform: str = "tistory",
    collection_docs: list | None = None,
) -> str:
    """Pass-2: [CHART_N: 설명] 플레이스홀더 → 이미지 병렬 생성 + 치환.

    1단계: 데이터 기반 차트 생성 (병렬)
    2단계: 차트 실패 슬롯 → AI 사진으로 대체 (병렬)
    3단계: 총 이미지 < _MIN_IMAGES → 추가 AI 사진 생성 후 이미지 없는 섹션에 배포

    Args:
        content: Pass-1 텍스트 ([CHART_N: ...] 플레이스홀더 포함)
        keyword: 글 주제
        sector: 섹터
        platform: 플랫폼 (tistory/naver)

    Returns:
        str: 플레이스홀더가 실제 이미지로 치환된 content (최소 _MIN_IMAGES 장 보장 시도)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # [CHART_N: description] 플레이스홀더 찾기
    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", content)
    if not placeholders:
        return content

    print(f"  🎨 [Pass-2/{platform}] 차트 {len(placeholders)}개 생성 시도...")
    _img_dir2 = OUTPUT_IMG_DIR / (f"economic_{platform}" if platform in ("tistory", "naver") else "economic_tistory")

    # ★ run_id 단일 생성소 — 이 함수에서 1회만 생성, 모든 차트에 동일 값 전달.
    # 동일 run_id → chart_generator._used_types_by_run 이 글 전체에서 스타일 중복 방지.
    import uuid as _uuid2
    _run_id2 = _uuid2.uuid4().hex[:8]

    # ── 1단계: 차트 생성 (병렬) ──────────────────────────────────────────────
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

    # ── 2단계: 차트 실패 슬롯 → AI 사진 대체 (병렬) ─────────────────────────
    failed_slots = [(pos, desc.strip()) for pos, (_idx, desc) in zip(
        [p for p, _, _ in _items], placeholders
    ) if not svg_map.get(pos)]

    if failed_slots:
        print(f"  📸 [Pass-2] 차트 실패 {len(failed_slots)}개 슬롯 → AI 사진 대체...")
        # ★ JARVIS06 CLAUDE.md 규정: 외부 이미지 API 순차 실행 (max_workers=1 강제)
        with ThreadPoolExecutor(max_workers=1) as executor:
            ai_futures = {
                executor.submit(_generate_ai_photo_for_slot, desc, keyword, _img_dir2): pos
                for pos, desc in failed_slots
            }
            for future in as_completed(ai_futures):
                pos = ai_futures[future]
                try:
                    ai_html = future.result()
                    if ai_html:
                        svg_map[pos] = ai_html
                        print(f"  🖼️ CHART_pos{pos} AI 사진으로 대체 완료")
                    else:
                        print(f"  ⏭️ CHART_pos{pos} AI 사진도 실패 — 슬롯 제거")
                except Exception as e:
                    print(f"  ⚠️ CHART_pos{pos} AI 사진 오류: {e}")

    # ── 플레이스홀더 치환 ─────────────────────────────────────────────────────
    _replace_pos = [0]

    def _replace_placeholder(m: re.Match) -> str:
        pos = _replace_pos[0] + 1
        _replace_pos[0] += 1
        return svg_map.get(pos, "")

    content_final = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace_placeholder, content)

    # ── 3단계: 최소 이미지 수 확인 → 부족분 AI 사진 추가 ──────────────────────
    total_ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [Pass-2/{platform}] 이미지 {total_ok}/{len(placeholders)}개 치환 (차트+AI사진)")

    if total_ok < _MIN_IMAGES:
        needed = _MIN_IMAGES - total_ok
        print(f"  📸 [Pass-2] 이미지 {total_ok}개 < 최소 {_MIN_IMAGES}개 → AI 사진 {needed}개 추가 생성...")
        extra = _generate_extra_ai_photos(keyword, sector, needed, _img_dir2)
        if extra:
            content_final = _insert_extra_photos(content_final, extra)
            print(f"  ✅ [Pass-2] 추가 AI 사진 {len(extra)}개 삽입 → 총 {total_ok + len(extra)}개")
        else:
            print(f"  ⚠️ [Pass-2] 추가 AI 사진 생성 실패")
    else:
        print(f"  ✅ [Pass-2] 최소 이미지 수 {_MIN_IMAGES}장 충족")

    return content_final


def generate_article_html(
    keyword: str,
    sector: str,
    reason: str,
    supreme_block: str,
    platform: str = "tistory",
    collection_docs: list | None = None,
) -> str:
    """2-pass Claude Code SDK → 텍스트 + inline SVG 완성 원고 HTML.

    Pass-1(sonnet): 텍스트 + 플레이스홀더
    Pass-2(sonnet × 병렬): SVG 치환
    platform: "tistory" | "naver"

    Returns:
        str: 완전한 HTML 문서. 실패 시 빈 문자열.
    """
    # Pass-1 선택: 기존(느림) vs 섹션별 병렬(빠름)
    # 기본: 섹션별 병렬로 생성하고, 오류 시 기존 방식 폴백
    raw = _generate_text_pass1_parallel(keyword, sector, reason, supreme_block, platform)
    if not raw:
        print("  ⚠️ [Pass-1 병렬] 실패 → 기존 방식 재시도...")
        raw = _generate_complete_article_cli(keyword, sector, reason, supreme_block, platform)
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

    # ★ Pass-2: [CHART_N: ...] 플레이스홀더 → SVG 치환 (필수!)
    content = _generate_svg_pass2_and_replace(content, keyword, sector, platform, collection_docs=collection_docs)

    kc = _L.count(content)
    svg_count = len(re.findall(r"<svg[\s>]", content, re.IGNORECASE))
    p_tags = re.findall(r"<p[^>]*>.*?</p>", content, re.DOTALL | re.IGNORECASE)
    sent_count = sum(
        len(re.findall(r'[.!?。]\s*(?=[^<]|$)', re.sub(r"<[^>]+>", "", p)))
        for p in p_tags
    )
    print(f"  ✅ [2-pass] 완성 원고 ({kc}자, SVG {svg_count}개, 약 {sent_count}문장)")

    # ── 문장수 미달 시 1회 재시도 (SVG는 Pass-2 보장이므로 미포함) ──────────
    if sent_count < _L.MIN_SENTENCES_THRESHOLD:
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

    # JPG(SVG 스크린샷) 만 삭제 — PNG(matplotlib 차트)는 Pass-2 에서 이미 생성됨
    for old_f in img_dir.glob("*.jpg"):
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
