"""shared/seo.py — SEO 측정 + 본문 정제 공용 헬퍼

마스터 비전 (범용 비서) 관점에서 도메인 무관 텍스트 분석 유틸.
블로그 외에도 메일·메모·리포트 등 미래 도메인에서 재사용 가능하도록 설계.

API:
    sanitize_body(html_or_text)             → 한글 본문만 (style/script 제거)
    count_korean(text)                      → 한글 글자수
    seo_score(title, body, keyword)         → 4원칙 점수 0~100 + 상세 dict
    compress_to_korean(text, max_korean)    → target_low~max 사이 자연 재작성 (LLM)
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


def compress_to_korean(text: str, max_korean: int = None,
                       context: str = "", emit_event: bool = True,
                       passthrough_ratio: float = 1.10) -> str:
    """한글 max_korean자 이내로 *자연스럽게 재작성* — 단순 자르기 절대 금지.

    원칙 (사용자 명령 2026-05-04 / 2026-05-05 강화):
    - 결과는 **하한선 92% (max*0.92) 이상** 이어야 함. 너무 짧으면 "끊긴 듯" 체감.
    - Claude Sonnet 에 "재작성" 요청 (압축 단어 회피 — 적극적 줄임 유발).
    - 결과 < target_low 면 재시도 1회. 두 번 다 미달이면 더 긴 쪽 채택.
    - Claude 호출 실패 시 단순 자르기 fallback (마지막 안전망).

    경계 처리: max_korean * passthrough_ratio 이내면 LLM 호출 없이 그대로 반환.
    passthrough_ratio=1.00 전달 시 max_korean 초과 즉시 LLM 압축.

    Args:
        text: 원본 본문
        max_korean: 한글 한도 — *호출자 도메인이 자체 정책 모듈에서 명시 전달 필수*.
                    None 이면 cap 적용 없이 그대로 반환 (도메인 무관 유틸 — 정책 박지 않음).
                    자비스01 은 JARVIS02_WRITER/length_manager.MAX_KOREAN 을 명시 전달.
        context: 글 식별 메타 — 이벤트 payload 포함
        emit_event: True면 압축 발생 시 bus 이벤트 발행
        passthrough_ratio: 이 비율 이내면 LLM 압축 없이 통과 (기본 1.10; 1.00 = 초과 즉시 cap)

    Returns: 한글 target_low ~ max_korean 사이의 자연스러운 본문
    """
    if not text:
        return ""
    if max_korean is None:
        # 도메인 정책 미지정 — cap 없이 그대로 통과 (호출자 책임)
        return sanitize_body(text)
    plain = sanitize_body(text)
    original_kor = count_korean(plain)
    if original_kor <= max_korean:
        return plain  # 이미 한도 이내

    target_low = int(max_korean * 0.92)  # 하한선 (기본 46문장(약 2300자))

    # ── 경계 처리: passthrough_ratio 이내는 LLM 안 거치고 그대로 ──
    if original_kor <= int(max_korean * passthrough_ratio):
        if emit_event:
            _emit_overflow_event(context, original_kor, original_kor, "passthrough_minor_overflow")
        return plain

    # ── 1단계: Claude 재작성 시도 (재시도 포함) ──────────────────
    best = ""
    best_kor = 0
    for attempt in (1, 2):
        out = _claude_compress(plain, max_korean, original_kor, target_low, attempt)
        if not out:
            continue
        out_kor = count_korean(out)
        # 더 긴 결과 보존 (하한선 미달 대비)
        if out_kor > best_kor:
            best, best_kor = out, out_kor
        if target_low <= out_kor <= max_korean:
            if emit_event:
                _emit_overflow_event(context, original_kor, out_kor, f"claude_summary_attempt{attempt}")
            return out
        # 1차 실패 시 2차 재시도 (프롬프트가 더 강하게 하한선 요구)

    # ── 2단계: 두 번 다 범위 밖 — 가장 긴 결과 사용 (or hard_cut) ─
    if best:
        if best_kor > max_korean:
            best = _hard_cut(best, max_korean)
            best_kor = count_korean(best)
        if emit_event:
            _emit_overflow_event(context, original_kor, best_kor, "claude_summary_below_target")
        return best

    # ── 3단계: Claude 완전 실패 시 hard_cut fallback ────────────
    if emit_event:
        _emit_overflow_event(context, original_kor, max_korean, "hard_cut_fallback")
    return _hard_cut(plain, max_korean)


def _claude_compress(plain: str, max_korean: int, original_kor: int,
                     target_low: int, attempt: int = 1) -> str:
    """Claude Sonnet 호출해 자연스럽게 재작성. 실패 시 빈 문자열.

    attempt=1: 표준 프롬프트. 2: 하한선 미달일 때 재시도 — 더 강하게 분량 요구.
    """
    try:
        from shared.llm import invoke_text as _inv_cli
        if attempt == 1:
            length_block = (
                f"[분량 — 가장 중요]\n"
                f"- 결과 한글 글자수는 **반드시 {target_low}자 이상 {max_korean}자 이하**.\n"
                f"- {target_low}자 미만은 '글이 끊긴 듯' 체감되어 사용자가 거부함.\n"
                f"- 짧게 만들기 위해 핵심을 빼지 말 것. 부가설명·예시·수치 디테일을 유지하면서 길이 맞춤.\n"
            )
        else:
            length_block = (
                f"[분량 — 재시도, 절대 명령]\n"
                f"- 1차 결과가 너무 짧았음. 이번엔 **반드시 {target_low}자 이상**.\n"
                f"- 부족하면 핵심 종목/수치/맥락을 *추가 설명* 으로 보강해서 채울 것.\n"
                f"- 상한 {max_korean}자만 지키면 됨. {target_low}자 미만은 실패로 간주.\n"
            )
        prompt = (
            f"다음 블로그 본문이 한글 {original_kor}자로 한도({max_korean}자)를 초과합니다. "
            f"전체 글을 **한글 {target_low}~{max_korean}자 사이의 완성된 글로 재작성** 해주세요. "
            f"단순 자르기·요약이 아니라 *제 길이의 본글*을 만드는 작업입니다.\n\n"
            f"{length_block}\n"
            "[작성 규칙]\n"
            "- 도입부·본론·마무리 모두 갖춘 완성된 글.\n"
            "- 핵심 키워드·주요 종목·수치는 반드시 보존.\n"
            "- 중복 표현·반복되는 마무리 문구만 정리. 정보는 보존.\n"
            "- 문장은 모두 마침표(.)로 완결. 미완 문장 금지.\n"
            "- 결과만 출력. 메타 설명('재작성한 결과:', '압축한 결과:' 등) 절대 금지.\n\n"
            f"원문:\n{plain}"
        )
        out = (_inv_cli("writer", prompt, timeout=300) or "").strip()
        # 메타 prefix 제거 (혹시 들어왔다면)
        out = re.sub(r"^(?:(?:압축|재작성|수정)[ ㄱ-ㅎ가-힣]*결과[:\s]*\n?)", "", out)
        return out
    except Exception as e:
        try:
            import sys
            print(f"  [seo] Claude 재작성 실패 (attempt={attempt}, fallback): {e}", file=sys.stderr)
        except Exception:
            pass
        return ""


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


