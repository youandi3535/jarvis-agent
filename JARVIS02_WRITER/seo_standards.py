"""
플랫폼별 SEO 기준 — BLOG_SUPREME_LAW.md 제15조 순수 어댑터.

★ 모든 정책 텍스트는 이 파일에 박지 않는다.
  BLOG_SUPREME_LAW.md 제15조의 마커에서 동적 로드:
  - seo_prompt          : <!-- seo:platform:start/end -->
  - differentiation_angle: <!-- diff:platform:start/end -->
  - 메타(algorithm 등)  : <!-- seo-meta:platform:start/end --> (key:value)
  SEO 지침 수정 = BLOG_SUPREME_LAW.md 만 수정.

기술 상수(char_min · title_max_chars · image_min)는 length_manager.py 단일 진입점 유지.
주간 학습: run_seo_learning() (seo_learner.py → job_registry.py weekly_seo_learn 잡)
"""

import re as _re

LAST_UPDATED = "2026-05-17"
SEO_VERSION  = "v2.1"

try:
    from JARVIS02_WRITER import length_manager as _LM
    from JARVIS02_WRITER.law_enforcer import (
        parse_seo_block as _parse_seo,
        parse_diff_block as _parse_diff,
        parse_seo_meta as _parse_meta,
    )
except ImportError:
    import length_manager as _LM  # 같은 폴더 직접 실행 시
    try:
        from law_enforcer import (
            parse_seo_block as _parse_seo,
            parse_diff_block as _parse_diff,
            parse_seo_meta as _parse_meta,
        )
    except ImportError:
        def _parse_seo(p: str) -> str: return ""
        def _parse_diff(p: str) -> str: return ""
        def _parse_meta(p: str) -> dict: return {}

# ═══════════════════════════════════════════════════════════════
# 플랫폼별 SEO 기준
# ★ 모든 텍스트 필드는 BLOG_SUPREME_LAW.md 제15조에서 파싱.
#   이 파일은 parse 함수 호출과 기술 상수만 보유 — 수정 금지.
# ═══════════════════════════════════════════════════════════════

# 각 플랫폼 seo-meta 블록 파싱 (모듈 초기화 시 1회)
_naver_meta  = _parse_meta("naver")
_tist_meta   = _parse_meta("tistory")

PLATFORM_STANDARDS: dict = {

    # ── 네이버 ──────────────────────────────────────────────────
    # ★ 모든 텍스트 값 = BLOG_SUPREME_LAW.md 제15조-A <!-- seo-meta:naver --> 파싱
    # ★ 모든 수치 상수 = length_manager.py 단일 진입점 (직접 숫자 박지 말 것)
    "naver": {
        "algorithm":             _naver_meta.get("algorithm", "C-Rank + D.I.A.+"),
        "char_min":              _LM.TARGET_KOREAN,       # 1,500자 — SEO 최소 본문 길이
        "char_ideal":            _LM.SEO_CHAR_IDEAL,
        "char_max":              3000,
        "title_max_chars":       _LM.TITLE_PROMPT_MAX,    # 40자 — 네이버 SEO 제목 한도
        "image_min":             _LM.MIN_IMAGES,
        "heading_structure":     _naver_meta.get("heading_structure", ""),
        "keyword_density":       _naver_meta.get("keyword_density", ""),
        "keyword_in_title":      _naver_meta.get("keyword_in_title", ""),
        "keyword_in_body":       _naver_meta.get("keyword_in_body", ""),
        "hashtag_min":           _LM.NAVER_HASHTAG_MIN,   # 5개
        "hashtag_max":           _LM.NAVER_HASHTAG_MAX,   # 10개
        "internal_links":        0,                        # 네이버 — 내부 링크 SEO 기준 없음
        "differentiation_angle": _parse_diff("naver"),    # <!-- diff:naver -->
        "seo_prompt":            _parse_seo("naver"),     # <!-- seo:naver -->
        "forbidden":             _naver_meta.get("forbidden", []),
    },

    # ── 티스토리 ────────────────────────────────────────────────
    # ★ 모든 텍스트 값 = BLOG_SUPREME_LAW.md 제15조-B <!-- seo-meta:tistory --> 파싱
    # ★ 모든 수치 상수 = length_manager.py 단일 진입점 (직접 숫자 박지 말 것)
    "tistory": {
        "algorithm":             _tist_meta.get("algorithm", "Google SEO"),
        "char_min":              _LM.TARGET_KOREAN,            # 1,500자 — SEO 최소 본문 길이
        "char_ideal":            _LM.SEO_CHAR_IDEAL,
        "char_max":              3000,
        "title_max_chars":       _LM.TITLE_TISTORY_PROMPT_MAX, # 55자 — 티스토리 SEO 제목 한도
        "image_min":             _LM.MIN_IMAGES,
        "heading_structure":     _tist_meta.get("heading_structure", ""),
        "keyword_density":       _tist_meta.get("keyword_density", ""),
        "meta_desc_min_chars":   _LM.META_DESC_PROMPT_MAX,     # 140자 — 메타 설명 최솟값
        "meta_desc_max_chars":   _LM.META_DESC_MAX,            # 160자 — 메타 설명 최댓값
        "internal_links":        _LM.TISTORY_INTERNAL_LINKS,   # 1개
        "differentiation_angle": _parse_diff("tistory"),       # <!-- diff:tistory -->
        "seo_prompt":            _parse_seo("tistory"),        # <!-- seo:tistory -->
        "forbidden":             _tist_meta.get("forbidden", []),
    },
}


# ═══════════════════════════════════════════════════════════════
# 공개 API — 프롬프트 주입용
# ═══════════════════════════════════════════════════════════════


def heading_plan(platform: str) -> dict:
    """플랫폼별 **헤딩 골격** — BLOG_SUPREME_LAW 의 `heading_structure` 에서 파생.

    반환: {"section_tag": "h3"|"h2", "h1_required": bool, "sec_min": int, "sec_max": int}

    ★ 왜 필요한가 (2026-08-04 전수 감사 2위)
      헌법은 플랫폼별 헤딩을 명확히 규정한다:
        네이버   — `H3 소제목 3~4개 (각 소제목 아래 2~3문장)`
        티스토리 — `H1 1개 + H2 3~5개 + H3 0~3개/H2`
      그런데 `draft_writer` 골격은 `<h2>` 를 **21곳에 박아** 두고 `<h3>` 는 **0회** 였다.
      두 지시가 충돌하면 LLM 은 추상 규정보다 *구체적 골격 예시* 를 따른다.
      실측 발행본(07-20 이후 24편): h3 평균 **0.0~0.8** · h1 평균 **0.0~0.2**.
      그 결과 채점표에서 네이버 5점(`N3_h3_count` 3 + `N4_section_sents` 2) ·
      티스토리 2점(`T3_h1_count`)이 **매 글 죽어 있었다**.
      → 채점표가 옳고 생성기가 헌법을 안 지킨 것이다. 골격이 헌법에서 파생하게 만든다.

    ★ 파싱은 *구조* 만 본다 — 태그와 개수만 뽑고 문장은 해석하지 않는다.
      헌법 문구가 다듬어져도 `H3 ... 3~4개` 꼴만 유지되면 계속 동작한다.
    """
    std = PLATFORM_STANDARDS.get((platform or "").lower(), {})
    raw = str(std.get("heading_structure", "") or "")
    found: dict[int, tuple] = {}
    for m in _re.finditer(r"H([1-6])[^0-9]{0,12}(\d+)\s*(?:~\s*(\d+))?\s*개", raw, _re.I):
        lvl = int(m.group(1))
        lo = int(m.group(2))
        hi = int(m.group(3)) if m.group(3) else lo
        found[lvl] = (lo, hi)
    # 섹션 태그 = *가장 많이 요구되는* 헤딩 레벨 (h1 은 문서 제목이라 제외)
    body = {lvl: v for lvl, v in found.items() if lvl >= 2}
    if body:
        sec_lvl = max(body, key=lambda l: body[l][1])
        sec_min, sec_max = body[sec_lvl]
    else:                       # 헌법을 못 읽으면 종전 동작(h2) 유지 — fail-safe
        sec_lvl, sec_min, sec_max = 2, 3, 5
    return {
        "section_tag": f"h{sec_lvl}",
        "h1_required": (found.get(1) or (0, 0))[1] > 0,
        "sec_min": sec_min,
        "sec_max": sec_max,
        "source": raw,
    }

def build_seo_block(platform: str, theme: str = "") -> str:
    """플랫폼별 SEO 핵심 지침 문자열 반환 — LLM 프롬프트 직접 주입.

    ★ 채점 기준 자동 파생 (ERRORS [463] — 2026-07-21):
      종전엔 서술형 `seo_prompt` 만 내보내, `PLATFORM_STANDARDS` 가 이미 보유한
      *수치 기준*(제목 55자·메타 140~160자 등)이 작성자에게 전달되지 않았다.
      그런데 `post_scorer` 는 바로 그 수치로 채점한다 →
      **"채점은 하는데 알려주지는 않는" 항목이 생겨** 점수가 구조적으로 깎였다
      (실측: A축 engagement 7점·usefulness 5점, C축 T1 2점·T7 2점 = 16점 미지시).

      이제 기준 dict 에서 *자동 파생* 한다 — 값을 바꾸면 프롬프트가 따라온다.
      하드코딩 금지(CLAUDE.md '복사본을 진실로 믿지 말 것').
    """
    std = PLATFORM_STANDARDS.get(platform.lower(), {})
    block = std.get("seo_prompt", "")
    if theme and "{theme}" in block:
        block = block.replace("{theme}", theme)

    # ── 채점되는 수치 기준을 기준 dict 에서 파생해 명시 ──
    lines: list[str] = []
    _t_max = std.get("title_max_chars")
    if _t_max:
        lines.append(f"- 제목: {_t_max}자 이내 (초과 시 감점) + 핵심 키워드를 앞부분에 배치")
    _m_min, _m_max = std.get("meta_desc_min_chars"), std.get("meta_desc_max_chars")
    if _m_min and _m_max:
        lines.append(f"- 메타 설명: {_m_min}~{_m_max}자 (이 범위를 벗어나면 감점)")
    _links = std.get("internal_links")
    if _links:
        lines.append(f"- 내부 링크 {_links}개 이상 (맥락에 맞게)")
    _img = std.get("image_min")
    if _img:
        lines.append(f"- 본문 이미지 최소 {_img}장")
    if lines:
        block += "\n\n▶ 채점되는 정량 기준 (반드시 충족):\n" + "\n".join(lines)
    return block


def build_differentiation_block() -> str:
    """2플랫폼 콘텐츠 차별화 지침 — 중복 콘텐츠 SEO 페널티 방지."""
    naver  = PLATFORM_STANDARDS["naver"]["differentiation_angle"]
    tist   = PLATFORM_STANDARDS["tistory"]["differentiation_angle"]
    return (
        "[★ 플랫폼별 차별화 각도 — 중복 콘텐츠 절대 방지 ★]\n"
        "2개 플랫폼은 동일 데이터를 바탕으로 하되, 완전히 다른 각도·구성·표현으로 작성:\n"
        f"- 네이버: {naver}\n"
        f"- 티스토리: {tist}\n"
        "같은 문장·단락을 2개 이상 플랫폼에서 재사용하는 것은 절대 금지."
    )


def build_platform_seo_section(active_pfxs: list, theme: str = "") -> str:
    """활성 플랫폼별 SEO 지침 블록 조합 반환 (generate_triple_articles 용)."""
    pfx_to_key = {"NAVER": "naver", "TISTORY": "tistory"}
    pfx_to_name = {"NAVER": "네이버", "TISTORY": "티스토리"}
    blocks = []
    for pfx, _ in active_pfxs:
        key  = pfx_to_key.get(pfx, pfx.lower())
        name = pfx_to_name.get(pfx, pfx)
        blk  = build_seo_block(key, theme)
        if blk:
            blocks.append(blk)
    diff = build_differentiation_block() if len(active_pfxs) > 1 else ""
    parts = [b for b in blocks if b]
    if diff:
        parts.append(diff)
    return "\n\n".join(parts)


def get_all_standards_summary() -> str:
    """주간 학습 비교용 — 현재 기준 전체 요약 텍스트 반환."""
    lines = [
        f"# JARVIS SEO 기준 현황",
        f"버전: {SEO_VERSION} | 업데이트: {LAST_UPDATED}\n",
    ]
    for pf, std in PLATFORM_STANDARDS.items():
        lines.append(f"## {pf.upper()} ({std['algorithm']})")
        # ★ 문장수 메인 표기 (사용자 박제 2026-05-14)
        _mn = std['char_min'] // _LM.KOREAN_PER_SENTENCE
        _id = std['char_ideal'] // _LM.KOREAN_PER_SENTENCE
        lines.append(f"- 권장 분량: {_LM.build_length_phrase(_mn, _id)} (이상적 {_id}문장)")
        lines.append(f"- 제목 최대: {std['title_max_chars']}자")
        lines.append(f"- 헤딩 구조: {std['heading_structure']}")
        lines.append(f"- 키워드 밀도: {std['keyword_density']}")
        lines.append(f"- 차별화 앵글: {std['differentiation_angle']}")
        lines.append(f"- 금지 사항: {', '.join(std.get('forbidden', []))}")
        lines.append("")
    return "\n".join(lines)
