"""
JARVIS02 — 사전 대본 수정 (Pre-Revise)

발행 전 단계에서 Claude 분석 → 자동 패치 적용으로 한 번에 완벽한 글 발행.
selenium 사후 수정 의존도 제거 → UI 변경 리스크 0.

사용:
    from pre_revise import pre_revise_blocks, pre_revise_html

    new_title, new_blocks, applied = pre_revise_blocks("naver", title, blocks)
    new_title, new_html,  applied = pre_revise_html("tistory", title, html)

공통 동작:
    1. 입력 텍스트 추출 (blocks→평문 / html→평문)
    2. JARVIS03 post_quality_analyzer._analyze_with_claude() 재사용 → suggestions 도출
       (사전·사후 분석기 통일 → 일관성 유지)
    3. type별 적용 (title/cta/intro/seo/readability/keyword/structure)
    4. 분석/적용 실패 시 원본 그대로 반환 (안전장치)

2개 플랫폼 공통 — naver / tistory.
"""
from __future__ import annotations
import sys
import re
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
JARVIS_ROOT = BASE_DIR.parent
sys.path.insert(0, str(JARVIS_ROOT))

# 분석기 공개 인터페이스 — analyze_post_quality (JARVIS03 품질 분석 단일 진입점)
try:
    from JARVIS03_RADAR.post_quality_analyzer import analyze_post_quality as _analyze_with_claude
except Exception as _e:
    _analyze_with_claude = None
    print(f"  ⚠️ pre_revise: 분석기 import 실패 (사전 수정 비활성화): {_e}")
    _g_report("writer", _e, module=__name__)

_TAG_RE     = re.compile(r'<[^>]+>')
_WHITESPACE = re.compile(r'\s+')
# 한글 카운트는 length_manager 단일 진입점 — 여기서 별도 정의 금지

# pre_revise 가 cta/intro/seo 패치를 적용하면 본문이 길어질 수 있다.
# 글자수 정책은 length_manager 단일 진입점 — 한도 변경 시 거기만 수정.
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 같은 폴더 직접 실행 시

def _cap_content_pr(text, max_korean=_L.MAX_KOREAN, context="pre_revise"):
    """legacy alias → length_manager.compress."""
    return _L.compress(text, context=context, max_korean=max_korean)

def _kor_pr(text):
    """legacy alias → length_manager.count."""
    return _L.count(text)

# ─────────────────────────────────────────────────────────────
# 메타 패턴 sanitizer — Claude 가 'after' 에 작성 지시문을 넣었을 때
# 본문 누출을 막는 최후 방어선. 프롬프트 강화와 별개로 반드시 동작해야 함.
# ─────────────────────────────────────────────────────────────
_META_PATTERNS = [
    # "~등 ~ 제시/추가/권장/보강/필요"
    re.compile(r'등\s+(더\s+)?\S{0,8}(제시|추가|권장|보강|필요|도입)'),
    # "마무리 후 추가:", "글 마지막에 ~ 추가"
    re.compile(r'(마무리|마지막|끝|결론).{0,8}(추가|덧붙)'),
    # "또는 ~ 제시/링크 제시"
    re.compile(r'또는\s+\S+\s+(링크|제시)'),
    # 괄호 안에 "또는/예/예시" 로 시작하는 대안 제시 — 작성자에게 보내는 지시문
    re.compile(r'\(\s*(또는|예|예시)[\s:：]'),
    # 괄호 안에 따옴표 인용된 예시 — 정상 본문에 거의 나오지 않는 패턴
    re.compile(r"""\([^)]*["'][^"'\)]{6,}["'][^)]*\)"""),
    # "예: ~", "예시: ~", "다음과 같이"
    re.compile(r'(^|[\s。.!?])(예|예시)\s*[:：]'),
    re.compile(r'다음과\s+같이'),
    # 큰따옴표·작은따옴표로 시작해 ' 등 ~' 으로 끝나는 인용 예시
    re.compile(r"""["'].{2,80}["']\s*등\s"""),
    # 괄호 안 작성 지시문: (주어-술어를 ~), (~로 변경), (~하게)
    re.compile(r'\((주어|술어|문장|어조|톤|간결|보강|수정|변경|추가)[^)]{0,40}\)'),
    # "구체적인 실행 단계 제시", "행동 지침 추가"
    re.compile(r'구체적인?\s+(실행|행동|지침|예시).{0,12}(제시|추가|작성)'),
]


def _is_meta_after(after: str) -> tuple[bool, str]:
    """after 가 메타 작성 지시문인지 판정. (is_meta, matched_pattern)"""
    if not after:
        return False, ""
    for pat in _META_PATTERNS:
        m = pat.search(after)
        if m:
            return True, m.group(0)
    return False, ""


def _notify_meta_skip(mode: str, skipped: list):
    """메타 패턴으로 차단된 suggestion 들을 텔레그램으로 알림 — 학습 신호."""
    try:
        from shared.notify import send_tg
        lines = [f"🚫 *pre_revise sanitizer 차단* ({mode}, {len(skipped)}건)"]
        for i, s in enumerate(skipped, 1):
            lines.append(f"  [{i}] type={s['type']} / 패턴 hit: `{s['hit']}`")
            lines.append(f"      after: {s['after']}")
        lines.append("\n→ Claude 가 메타 지시문을 반환했지만 sanitizer가 막음. 본문 누출 없음.")
        send_tg("\n".join(lines))
    except Exception:
        pass


def _html_to_text(html: str) -> str:
    if not html:
        return ""
    txt = _TAG_RE.sub(' ', html)
    return _WHITESPACE.sub(' ', txt).strip()


def _blocks_to_text(blocks: list) -> str:
    """blocks 구조 → 평문 (분석용)."""
    if not blocks:
        return ""
    parts = []
    for tup in blocks:
        if not tup or len(tup) < 2:
            continue
        t, d = tup[0], tup[1]
        if t in ("text", "html"):
            parts.append(_html_to_text(d or ""))
        elif t in ("heading_h2", "heading_h3", "heading2"):
            parts.append((d or "").strip())
    return "\n\n".join(p for p in parts if p)


def _analyze(platform: str, title: str, content_text: str,
              post_type: str = "") -> list:
    """post_type: 'economic' / 'theme' 등. learning_insights scope 매칭에 사용.

    빈 문자열이면 환경변수 JARVIS_POST_TYPE fallback (subprocess 경계 통과용).
    """
    if not _analyze_with_claude or not content_text:
        return []
    if not post_type:
        import os as _os
        post_type = _os.environ.get("JARVIS_POST_TYPE", "").strip()
    try:
        return _analyze_with_claude(platform, title, content_text, post_type=post_type) or []
    except Exception as e:
        print(f"  ⚠️ pre_revise 분석 실패 (원본 유지): {e}")
        _g_report("writer", e, module=__name__)
        return []


def _apply_to_blocks(title: str, blocks: list, suggestions: list) -> tuple:
    new_title  = title
    new_blocks = list(blocks or [])
    applied    = []
    skipped_meta = []

    for s in suggestions or []:
        stype    = s.get("type", "")
        before_t = (s.get("before") or "").strip()
        after_t  = (s.get("after") or "").strip()
        if not after_t:
            continue

        # 메타 작성 지시문 sanitizer — 본문에 작성 지시 누출 방지
        is_meta, hit = _is_meta_after(after_t)
        if is_meta:
            skipped_meta.append({"type": stype, "field": s.get("field",""), "after": after_t[:80], "hit": hit})
            print(f"  🚫 pre_revise sanitizer skip [{stype}/{s.get('field','')}]: meta pattern '{hit}' in after")
            continue

        if stype == "title":
            new_title = after_t[:100]
            applied.append(s)

        elif stype == "cta":
            cta_text = after_t
            already = False
            for tup in new_blocks:
                if not tup or len(tup) < 2:
                    continue
                t_, d = tup[0], tup[1]
                if t_ in ("text", "html") and cta_text in (d or ""):
                    already = True
                    break
            if not already:
                new_blocks.append(("text", cta_text))
                applied.append(s)

        elif stype in ("intro", "readability", "keyword", "seo", "structure"):
            if not before_t:
                continue
            replaced = False
            for i, tup in enumerate(new_blocks):
                if not tup or len(tup) < 2:
                    continue
                t_, d = tup[0], tup[1]
                if t_ in ("text", "html") and d and before_t in d:
                    new_blocks[i] = (t_, d.replace(before_t, after_t, 1))
                    replaced = True
                    break
            if replaced:
                applied.append(s)

    if skipped_meta:
        _notify_meta_skip("blocks", skipped_meta)

    # 한글 한도 cap 안전망 — length_manager.cap_blocks 단일 호출
    try:
        new_blocks, _capped = _L.cap_blocks(new_blocks, context="pre_revise")
        if _capped:
            print(f"  ✂️ pre_revise blocks cap: 합산 한글 {_L.MAX_KOREAN}자 초과 → 후미 텍스트 잘라냄")
    except Exception as _e_cap:
        print(f"  ⚠️ pre_revise blocks cap 실패 (원본 유지): {_e_cap}")
        _g_report("writer", _e_cap, module=__name__)

    return new_title, new_blocks, applied


def _apply_to_html(title: str, html: str, suggestions: list) -> tuple:
    new_title = title
    new_html  = html or ""
    applied   = []
    skipped_meta = []

    for s in suggestions or []:
        stype    = s.get("type", "")
        before_t = (s.get("before") or "").strip()
        after_t  = (s.get("after") or "").strip()
        if not after_t:
            continue

        # 메타 작성 지시문 sanitizer — 본문에 작성 지시 누출 방지
        is_meta, hit = _is_meta_after(after_t)
        if is_meta:
            skipped_meta.append({"type": stype, "field": s.get("field",""), "after": after_t[:80], "hit": hit})
            print(f"  🚫 pre_revise sanitizer skip [{stype}/{s.get('field','')}]: meta pattern '{hit}' in after")
            continue

        if stype == "title":
            new_title = after_t[:100]
            applied.append(s)

        elif stype == "cta":
            cta_html = (
                f'<p style="text-align:center;margin-top:30px;padding:15px;'
                f'background:#f8f9fa;border-radius:8px;">'
                f'<strong>{after_t}</strong></p>'
            )
            if cta_html not in new_html:
                if "</body>" in new_html:
                    new_html = new_html.replace("</body>", cta_html + "</body>")
                else:
                    new_html += cta_html
                applied.append(s)

        elif stype in ("intro", "readability", "keyword", "seo", "structure"):
            if before_t and before_t in new_html:
                new_html = new_html.replace(before_t, after_t, 1)
                applied.append(s)

    if skipped_meta:
        _notify_meta_skip("html", skipped_meta)

    # 한글 한도 cap 안전망 — pre_revise 적용 후 길이 점검 (실제 발행 cap 은 호출자 책임).
    try:
        kor_after = _L.count(_html_to_text(new_html))
        if kor_after > _L.MAX_KOREAN:
            print(f"  ⚠️ pre_revise html: 적용 후 한글 {kor_after}자 > {_L.MAX_KOREAN} (호출자가 발행 직전 cap 적용 필요)")
    except Exception:
        pass

    return new_title, new_html, applied


def pre_revise_blocks(platform: str, title: str, blocks: list,
                       post_type: str = "") -> tuple:
    """blocks 단계 사전 수정. 분석 실패 시 원본 그대로 반환.

    post_type: 'economic'/'theme' 등 — learning_insights scope 매칭.
              빈 문자열이면 환경변수 JARVIS_POST_TYPE fallback.

    Returns:
        (new_title, new_blocks, applied_suggestions)
    """
    try:
        text = _blocks_to_text(blocks)
        suggestions = _analyze(platform, title, text, post_type=post_type)
        if not suggestions:
            return title, blocks, []
        nt, nb, applied = _apply_to_blocks(title, blocks, suggestions)
        return nt, nb, applied
    except Exception as e:
        print(f"  ⚠️ pre_revise_blocks 예외 (원본 유지): {e}")
        _g_report("writer", e, module=__name__)
        return title, blocks, []


def pre_revise_html(platform: str, title: str, html: str,
                     post_type: str = "") -> tuple:
    """HTML 단계 사전 수정. 분석 실패 시 원본 그대로 반환.

    post_type: 'economic'/'theme' 등 — learning_insights scope 매칭.
              빈 문자열이면 환경변수 JARVIS_POST_TYPE fallback.

    Returns:
        (new_title, new_html, applied_suggestions)
    """
    try:
        text = _html_to_text(html)
        suggestions = _analyze(platform, title, text, post_type=post_type)
        if not suggestions:
            return title, html, []
        nt, nh, applied = _apply_to_html(title, html, suggestions)
        return nt, nh, applied
    except Exception as e:
        print(f"  ⚠️ pre_revise_html 예외 (원본 유지): {e}")
        _g_report("writer", e, module=__name__)
        return title, html, []
