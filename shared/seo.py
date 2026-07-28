"""shared/seo.py — SEO 측정 + 본문 정제 공용 헬퍼

마스터 비전 (범용 비서) 관점에서 도메인 무관 텍스트 분석 유틸.
블로그 외에도 메일·메모·리포트 등 미래 도메인에서 재사용 가능하도록 설계.

API:
    sanitize_body(html_or_text)             → 한글 본문만 (style/script 제거)
    count_korean(text)                      → 한글 글자수
    seo_score(title, body, keyword)         → 4원칙 점수 0~100 + 상세 dict
    sanitize_tag(s)                         → 태그 1개 — 한글·영문·숫자만, 특수기호 0
    sanitize_tags(list)                     → 태그 리스트 — 각 sanitize + 빈문자열·중복 제거
"""
from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import re

# ── 정규식 (모듈 로드 1회 컴파일) ───────────────────────────────
_STYLE_RE  = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_TAG_RE    = re.compile(r"<[^>]+>")
_WS_RE     = re.compile(r"\s+")
_KOR_RE    = re.compile(r"[가-힣]")


def sanitize_body(text: str) -> str:
    """HTML/스타일/스크립트 제거 후 본문 텍스트만 반환.

    style/script 블록은 *통째* 제거 (CSS 코드가 본문에 섞이는 사고 방지).
    """
    if not text:
        return ""
    s = _STYLE_RE.sub(" ", text)
    s = _SCRIPT_RE.sub(" ", s)
    s = _TAG_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def count_korean(text: str) -> int:
    """한글(가-힣) 글자수만 카운트. 영어/숫자/특수문자 제외."""
    if not text:
        return 0
    return len(_KOR_RE.findall(text))


# ── 태그 정제 (★ 사용자 박제 2026-05-15 — 특수기호 절대 금지) ──────
# 네이버·티스토리 태그 입력 시 특수기호(·, /, -, +, &, (, ), 공백 등) 그대로 들어가면
# 해시태그 인식 실패 + 검색 노출 저해. 한글·영문·숫자만 유지.
_TAG_SANITIZE_RE = re.compile(r'[^0-9A-Za-z가-힣]+')


def sanitize_tag(s: str) -> str:
    """태그 1개 정제 — 한글·영문·숫자만 남기고 모든 특수기호·공백 제거.

    예: 'GTX(수도권 광역급행철도)' → 'GTX수도권광역급행철도'
        '경제·브리핑'             → '경제브리핑'
        'AI/머신러닝'             → 'AI머신러닝'
    빈 결과는 '' 반환 — 호출자가 필터링.
    """
    if not s:
        return ""
    return _TAG_SANITIZE_RE.sub('', str(s)).strip()


def sanitize_tags(tags: list[str], max_count: int = 10) -> list[str]:
    """태그 리스트 정제 — 각 sanitize + 빈문자열·중복 제거 + max_count 컷.

    순서 보존 (먼저 등장한 것 우선). 중복 검사는 *정제 후* 결과 기준.
    """
    if not tags:
        return []
    seen = set()
    out: list[str] = []
    for t in tags:
        clean = sanitize_tag(t)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= max_count:
            break
    return out


def _hard_cut(plain: str, max_korean: int) -> str:
    """최후 fallback — 한글 max_korean자에서 다음 문장 끝까지만 살림.

    Claude 호출 실패 시에만 사용. 학습 인사이트로 *원치 않는 패치* 임을 표시.
    """
    kor_count = 0
    for i, ch in enumerate(plain):
        if _KOR_RE.match(ch):
            kor_count += 1
            if kor_count >= max_korean:
                rest = plain[i:i+200]
                m = re.search(r"[.!?。]", rest)
                end = i + (m.end() if m else 1)
                return plain[:end]
    return plain


def _emit_overflow_event(context: str, original_kor: int,
                         compressed_kor: int, method: str) -> None:
    """압축 발생을 events 테이블에 기록 → daily_review 가 학습 인사이트로 누적."""
    try:
        from shared import bus
        bus.publish("post_overflow_compressed", "WRITER", {
            "context":         context,
            "original_korean": original_kor,
            "compressed_korean": compressed_kor,
            "method":          method,  # "claude_summary" | "hard_cut_fallback"
        })
    except Exception:
        pass


