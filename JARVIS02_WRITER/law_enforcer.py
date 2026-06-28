"""JARVIS02_WRITER/law_enforcer.py — BLOG_SUPREME_LAW.md 런타임 집행기

BLOG_SUPREME_LAW.md 의 8개 조항을 발행 직전 블록 단위로 검사·수정한다.
jarvis_main.py 와 economic_poster.py 의 enforce_text_between_images / _fix_consecutive_images
호출 직후에 이 함수를 호출해야 한다.

적용 조항:
  제0조 — 감성 도입부: AI식 팩트 오프닝 감지 → LLM으로 감성 도입부 자동 교체
  제0-B조 — 단락 2문장 한도: <p> 3문장 이상 → 자동 분리
  제2조 — 미완성 표현("추가 분석 필요" 등) 블록 제거
  제7조 — 금지 표현(©·이모지·LLM 지시문 누출·마켓시그널 등) 제거·치환

이미 다른 곳에서 집행 중인 조항 (중복 호출 금지):
  제3조 — law_enforcer._split_overlong_paragraphs + harness Layer 3 검증
  제4조 — jarvis_main.enforce_text_between_images / draft_fixer._fix_consecutive_images
         / economic_poster._fix_consecutive_images / image_validators._fix_any_consecutive_images
  제5조 — jarvis_main._safe_outro / _llm_disc
  제8조 — length_manager
"""
from __future__ import annotations

import logging
import re
from html import unescape
from typing import Sequence

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ── length_manager 단일 진입점 — 문장 수 상수 ──────────────────
try:
    from JARVIS02_WRITER.length_manager import MAX_P_SENTS as _L_MAX_P
    from JARVIS02_WRITER.length_manager import (
        HUMAN_INTRO_SENTS as _L_HUMAN_INTRO_SENTS,
        build_length_phrase as _L_phrase,
    )
except ImportError:
    from length_manager import MAX_P_SENTS as _L_MAX_P  # 동일 폴더 직접 실행 시
    from length_manager import (
        HUMAN_INTRO_SENTS as _L_HUMAN_INTRO_SENTS,
        build_length_phrase as _L_phrase,
    )

# ── ADR 008 Phase 1 (★ 사용자 박제 2026-05-17) — 이미지 함수 단일 진입점 ──
# law_enforcer 는 이미지 함수 *본체* 보유 금지. JARVIS06_IMAGE 위임.
from JARVIS06_IMAGE.validators.image_validators import (
    _is_heading_img_path,
    _validate_image_files,
    _dedupe_all_images,
    _dedupe_consecutive_images,
    _fix_any_consecutive_images,
)
from JARVIS06_IMAGE.injectors.image_injectors import (
    enforce_paragraph_pair_image,
    enforce_image_between_paragraphs,
    compute_unused_image_pool,
    _is_h2_header,
)

# ── 제0-B조: 단락 최대 2문장 분리 ──────────────────────────────
_SENT_SPLIT = re.compile(r'(?<=[다요니이]\.\s)|(?<=다\.\s)|(?<=요\.\s)|(?<=니다\.\s)')

def _split_overlong_paragraphs(html: str) -> tuple[str, int]:
    """<p> 태그 내 3문장 이상 → 2문장씩 분리. (위반 건수, 수정된 HTML) 반환."""
    violations = 0

    def _sentences(text: str) -> list[str]:
        parts = re.split(r'(?<=[다요니]\.) +|(?<=다\.) +', text.strip())
        return [p.strip() for p in parts if p.strip()]

    def _process_p(m: re.Match) -> str:
        nonlocal violations
        inner = m.group(1)
        if re.search(r'<[a-z]', inner):   # 하위 태그 포함 복잡한 p는 건드리지 않음
            return m.group(0)
        sents = _sentences(inner)
        if len(sents) <= 2:
            return m.group(0)
        violations += 1
        chunks = []
        for i in range(0, len(sents), 2):
            chunks.append(f'<p>{" ".join(sents[i:i+2])}</p>')
        return '\n'.join(chunks)

    result = re.sub(r'<p>(.*?)</p>', _process_p, html, flags=re.DOTALL)
    return result, violations


# ── 제0조: AI식 팩트 오프닝 감지 패턴 ──────────────────────────
_AI_OPENER = re.compile(
    r'^\s*(?:<[^>]+>\s*)*'       # leading HTML tags 허용
    r'(?:'
    r'\d[\d,.\s%]|'              # 숫자로 시작
    r'오늘의\s*(?:핵심|시장|지표|주요)|'
    r'이번\s*주\s*(?:핵심|지표|주요|일정)|'
    r'최근\s+(?:경제|시장|금리|환율|물가|증시)|'
    r'오늘\s+(?:코스피|코스닥|주요\s*지표|시장|경제)|'
    r'오늘\s+수집된|'
    r'간밤\s+글로벌\s+시장|'
    r'이번\s+주\s+발표'
    r')',
    re.IGNORECASE,
)

# ── 제2조: 미완성 표현 패턴 ─────────────────────────────────────
_UNFINISHED = re.compile(
    r'추가\s*분석\s*필요|데이터\s*없음|해당\s*없음|정보\s*없음|'
    r'\bTBD\b|\b미정\b|준비\s*중|업데이트\s*예정|내용\s*없음|'
    r'분석\s*불가|수집\s*실패|오류\s*발생',
    re.IGNORECASE,
)

# ── 제7조: 금지 표현 ────────────────────────────────────────────
# (a) 단순 제거 대상 (텍스트째 삭제)
_BANNED_EXACT = re.compile(
    r'©\s*\S+|All\s+rights\s+reserved|마켓시그널|'
    r'구독\s*해주세요|공감은\s*꾹|좋아요\s*꾹|구독\s*부탁',
    re.IGNORECASE,
)

# (b) LLM 작성 지시문 누출 패턴 (pre_revise sanitizer 와 동일 목록)
_LLM_LEAK = re.compile(
    r'등\s+더\s+구체적인\s+\S+\s*제시|마무리\s+후\s+추가\s*:|또는\s+[\'\"][^\'\"]+[\'\"]|'
    r'주어[–-]술어를\s+더|다음과\s+같이\s+수정|예\s*:\s*[가-힣]|'
    r'\(또는\s+[\'"][^)]+\)\s*\)|작성\s*지시문|프롬프트\s*내용',
    re.IGNORECASE,
)

# (c) 이모지 (텍스트 본문 내 유니코드 이모지)
_EMOJI = re.compile(
    r'[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0000FE00-\U0000FEFF'
    r'\U0001FA00-\U0001FA9F☀-⛿✀-➿]'
)

# (d) 프롬프트/마크다운 누설 — 발행 직전 최종 차단 (사용자 박제 2026-05-14)
#    LLM 가 *프롬프트 지시문* 또는 *마크다운 fence* 를 본문에 그대로 출력하는 사고
_PROMPT_LEAK = re.compile(
    r'```html?|'                                      # 코드블럭 fence 잔존
    r'\(?\s*제[0-9][\-A-Z]*조[^\)]*\)?|'              # "(제0-B조)", "제2조·제7조"
    r'정확히\s*\d+문장|'                              # "정확히 2문장"
    r'이모지\s*없음|미완성\s*표현\s*없음|'             # 지시문
    r'섹션\s*구성\s*:\s*\d+문장[^.\n]{0,80}|'         # "섹션 구성: 8문장 (..)"
    r'\d+개\s*차트\s*플레이스홀더\s*포함[^.\n]{0,40}|'  # 지시문
    r'소제목\s*앞\s*\d+행\s*여백\s*필수[^.\n]{0,40}|'  # 지시문
    r'\[CHART_\d+\][^.\n]{0,80}',                     # 미치환 placeholder 잔존
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    """단일 텍스트 블록에 7조 규정 적용 + 프롬프트 누설 제거."""
    t = _BANNED_EXACT.sub('', text)
    t = _LLM_LEAK.sub('', t)
    t = _PROMPT_LEAK.sub('', t)             # ★ 발행 직전 최종 차단
    t = _EMOJI.sub('', t)
    # 마크다운 강조 **text** → text (인라인)
    t = re.sub(r'\*\*([^\*\n]+?)\*\*', r'\1', t)
    # 마크다운 헤더 단독 줄 (HTML <h2> 와 충돌)
    t = re.sub(r'^\s*#{1,6}\s+[^\n]*\n', '', t, flags=re.MULTILINE)
    # 연속 공백·줄바꿈 정리
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = re.sub(r'  +', ' ', t)
    return t.strip()


def _clean_html(html: str) -> str:
    """HTML 블록에 7조 규정 적용 (태그 보존, 텍스트 노드만 치환)."""
    def _sub_text_node(m: re.Match) -> str:
        return _clean_text(m.group(0))
    # 태그 밖 텍스트 노드만 치환 (간이 방식: 태그 제거 후 텍스트 영역만 처리)
    result = re.sub(r'(?<=>)([^<]+)(?=<)', lambda m: _clean_text(m.group(1)), html)
    return result


def _block_has_unfinished(data: str) -> bool:
    """제2조: 블록 내 미완성 표현 존재 여부."""
    return bool(_UNFINISHED.search(unescape(data)))


# ── ADR 008 Phase 1: enforce_paragraph_pair_image, _dedupe_all_images,
#    _validate_image_files, _dedupe_consecutive_images 본체는 JARVIS06_IMAGE 로 이관됨
#    (위 import 블록에서 가져옴 — 동일 호출 시그니처).


# ── LLM 출력 형식 예시 placeholder 검출 (★ 사용자 박제 2026-05-16) ────────
# 사고: 사용자가 네이버 경제 브리핑 글에서 "섹션3-5. 섹션3-6." "마무리1. 마무리2."
#       "면책1. 면책2." 같은 *프롬프트 예시 형식* 이 발행물에 그대로 누수된 사실 보고.
# 원인: tistory_html_writer.py 의 LLM 프롬프트 안 출력 형식 예시가 LLM 응답에 그대로 복사됨.
# 안전망: 발행 직전 검출 → 해당 블록 제거 + 텔레그램 상세 경고.

# ★ 사용자 박제 2026-05-17 — 패턴 strict 화 (false positive 0 목표)
# 첫 버전은 너무 광범위 ("마무리 1단계" "면책 1조" "(섹션 3-5 참고)" 같은 정상 본문 5건 false positive).
# 정상 본문에 *부분 일치* 가능한 단독 패턴 제거 → *연속 placeholder* (점으로 이어진 2개+) 만 매칭.
# 그 외 단독 placeholder 는 "블록 전체가 placeholder 만 있는 짧은 블록" 휴리스틱으로 감지.

_PLACEHOLDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "섹션3-5. 섹션3-6." 형태 — 정확히 *N-M. 섹션N-M* 연속
    ("섹션 placeholder",    re.compile(r"섹션\s*\d+\s*[-－–~]\s*\d+\s*\.\s*섹션\s*\d+\s*[-－–~]\s*\d+")),
    # "마무리1. 마무리2." 연속 — 단독 "마무리1" 제외
    ("마무리 placeholder",  re.compile(r"마무리\s*\d+\s*\.\s*마무리\s*\d+")),
    # "면책1. 면책2." 연속
    ("면책 placeholder",    re.compile(r"면책\s*\d+\s*\.\s*면책\s*\d+")),
    # "오프닝1. 오프닝2." 연속
    ("오프닝 placeholder",  re.compile(r"오프닝\s*\d+\s*\.\s*오프닝\s*\d+")),
    # CHART 미치환 마커 — 정상 본문에 거의 안 나오므로 단독 매칭 OK
    ("CHART 미치환 마커",   re.compile(r"\[CHART_\d+\s*:")),
    ("IMAGE 미치환 마커",   re.compile(r"\[IMAGE_\d+\s*:")),
]

# 단독 placeholder 잡기용 — 블록 전체가 짧고 placeholder 만 있는 경우
_LONE_PLACEHOLDER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("섹션 단독",    re.compile(r"^\s*섹션\s*\d+\s*[-－–~]\s*\d+\s*\.?\s*$")),
    ("마무리 단독",  re.compile(r"^\s*마무리\s*\d+\s*\.?\s*$")),
    ("면책 단독",    re.compile(r"^\s*면책\s*\d+\s*\.?\s*$")),
    ("오프닝 단독",  re.compile(r"^\s*오프닝\s*\d+\s*\.?\s*$")),
]


def enforce_no_placeholders(
    blocks: list[tuple[str, str]],
    source: str = "",
) -> tuple[list[tuple[str, str]], int]:
    """발행 직전 LLM 출력 형식 예시 placeholder 검출 + 블록 제거.

    검출 시:
      - 해당 블록 전체 제거 (placeholder 그대로 발행보다 분량 축소가 안전)
      - 텔레그램 *상세* 경고 (어떤 패턴·어떤 블록 내용 일부)
      - ERRORS.md 자동 항목 추가는 호출 측 (auto_repair) 에서 처리

    Returns:
        (cleaned_blocks, removed_count)
    """
    if not blocks:
        return blocks, 0

    cleaned: list[tuple[str, str]] = []
    removed = 0
    found: list[str] = []  # (pattern_name, snippet) 기록

    for btype, bdata in blocks:
        if btype not in ('text', 'html'):
            cleaned.append((btype, bdata))
            continue
        if not isinstance(bdata, str):
            cleaned.append((btype, bdata))
            continue

        # 1) 연속 placeholder 패턴 (안전 — false positive 0)
        hit_name: str | None = None
        hit_snippet: str = ""
        for name, pat in _PLACEHOLDER_PATTERNS:
            m = pat.search(bdata)
            if m:
                hit_name = name
                start = max(0, m.start() - 10)
                end = min(len(bdata), m.end() + 20)
                hit_snippet = bdata[start:end].replace('\n', ' ').strip()
                break

        # 2) 단독 placeholder — *블록 전체가 짧고 placeholder 만* 있는 경우만
        if not hit_name:
            # HTML 태그 제거 후 plain text 추출
            plain = re.sub(r'<[^>]+>', '', bdata).strip()
            # 짧은 블록 (40자 미만) 에서만 단독 매칭 시도 (false positive 방지)
            if len(plain) < 40:
                for name, pat in _LONE_PLACEHOLDER_PATTERNS:
                    if pat.match(plain):
                        hit_name = name
                        hit_snippet = plain[:60]
                        break

        if hit_name:
            removed += 1
            found.append(f"{hit_name}: «{hit_snippet[:60]}»")
            log.warning(f"[LawEnforcer/placeholder] 블록 제거 — {hit_name} | {hit_snippet[:80]}")
            continue  # 블록 제거

        cleaned.append((btype, bdata))

    if removed:
        # ★ 차단 마커: placeholder 3개 초과 = 본문 LLM 생성 심각 실패
        if removed > 2:
            found.insert(0, f"★차단★ placeholder {removed}개 제거 — 본문 LLM 생성 실패 심각")

        # 텔레그램 상세 경고
        msg_lines = [
            f"⚠️ *발행 직전 placeholder 검출 — {removed}개 블록 제거*",
            f"위치: {source or 'unknown'}",
            "",
            "*검출 내역*:",
        ]
        for f in found[:5]:
            msg_lines.append(f"  • {f}")
        if len(found) > 5:
            msg_lines.append(f"  • … (+{len(found) - 5}건 더)")
        msg_lines.append("")
        msg_lines.append("LLM 작성 실패 — 분량 부족 가능, 재발행 검토.")
        msg = "\n".join(msg_lines)

        try:
            import os as _os
            import requests as _req
            token = _os.getenv('TELEGRAM_TOKEN', '')
            chat = _os.getenv('TELEGRAM_CHAT_ID', '')
            if token and chat:
                _req.post(
                    f'https://api.telegram.org/bot{token}/sendMessage',
                    json={'chat_id': chat, 'text': msg, 'parse_mode': 'Markdown'},
                    timeout=5,
                )
        except Exception:
            pass

    return cleaned, removed


# ── ADR 008 Phase 1: _is_heading_img_path, compute_unused_image_pool, _is_h2_header,
#    enforce_image_between_paragraphs 본체는 JARVIS06_IMAGE 로 이관됨 (위 import 블록).


def enforce_supreme_law(
    blocks: list[tuple[str, str]],
    platform: str = "",
    source: str = "",
    image_pool: list[str] | None = None,
    tags: list[str] | None = None,
    source_data: str = "",
    post_type: str = "",
) -> tuple[list[tuple[str, str]], list[str]]:
    """BLOG_SUPREME_LAW.md 제2조·제7조·제9조·제14조 런타임 집행.

    Args:
        blocks: [(type, data), ...] 형식의 블록 리스트
        platform: "naver" | "tistory" (로깅용)
        source: 호출 지점 설명 (로깅용)
        tags: 태그 리스트 — naver/tistory 플랫폼일 때 제14조 자동 정제

    Returns:
        (cleaned_blocks, violations)
        violations: 발견된 위반 설명 리스트 (텔레그램 알림용)
    """
    cleaned: list[tuple[str, str]] = []
    violations: list[str] = []
    tag = f"[LawEnforcer/{platform or '?'}]"

    # ── placeholder 검출 (★ 사용자 박제 2026-05-16) — 가장 먼저 ────
    # LLM 출력 형식 예시 누수 ("섹션3-5", "마무리1", "면책1" 등) 발견 시 블록 제거
    blocks, pholder_cnt = enforce_no_placeholders(blocks, source=f"{source}|{platform}")
    if pholder_cnt:
        msg = f"placeholder 검출 — {pholder_cnt}개 블록 제거 (LLM 출력 형식 예시 누수)"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 제0조: AI식 오프닝 감지 → LLM 자동 교체 ──────────────
    blocks, intro_warns = fix_human_intro(blocks, platform=platform, source=source)
    violations.extend(intro_warns)

    for btype, bdata in blocks:
        if not isinstance(bdata, str):
            cleaned.append((btype, bdata))
            continue

        # ── 제2조: 미완성 표현 블록 통째 제거 ──────────────────
        if btype in ('text', 'html') and _block_has_unfinished(bdata):
            hit = _UNFINISHED.search(unescape(bdata))
            msg = f"제2조 위반 제거 [{btype}] '{hit.group()[:20]}…'"
            violations.append(msg)
            log.warning(f"{tag} {msg}")
            continue  # 블록 제거

        # ── 제7조: 금지 표현 치환 ────────────────────────────────
        if btype == 'text':
            cleaned_data = _clean_text(bdata)
            if cleaned_data != bdata:
                violations.append(f"제7조 금지표현 정리 [{btype}]")
                log.info(f"{tag} 제7조 text 정리")
            if cleaned_data:
                cleaned.append((btype, cleaned_data))
            # 빈 텍스트는 버림
        elif btype == 'html':
            cleaned_data = _clean_html(bdata)
            if cleaned_data != bdata:
                violations.append(f"제7조 금지표현 정리 [html]")
                log.info(f"{tag} 제7조 html 정리")
            # ── 제0-B조: 3문장 이상 단락 자동 분리 ──────────────
            cleaned_data, para_cnt = _split_overlong_paragraphs(cleaned_data)
            if para_cnt:
                msg = f"제0-B조 단락 분리 [html] {para_cnt}건"
                violations.append(msg)
                log.info(f"{tag} {msg}")
            cleaned.append((btype, cleaned_data))
        else:
            cleaned.append((btype, bdata))

    # ── 제4조 strict: 같은 이미지 연속 dedupe (★ 사용자 박제 2026-05-14) ────
    cleaned, dupe_cnt = _dedupe_consecutive_images(cleaned)
    if dupe_cnt:
        msg = f"제4조 strict — 연속 동일 이미지 {dupe_cnt}건 제거"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 제4조 strict: 다른 이미지 연속도 차단 — 다음 텍스트 이후로 이동 ──
    cleaned, any_consec_cnt = _fix_any_consecutive_images(cleaned)
    if any_consec_cnt:
        msg = f"제4조 strict — 다른 이미지 연속 {any_consec_cnt}건 다음 텍스트 이후로 이동"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 이미지 *전역 dedupe* (★ 사용자 박제 2026-05-17) — 비연속 중복 제거 ──
    cleaned, gdupe_cnt = _dedupe_all_images(cleaned)
    if gdupe_cnt:
        msg = f"이미지 전역 dedupe — 비연속 중복 {gdupe_cnt}건 제거"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 이미지 *내용 해시* dedupe (★ ERRORS [136] 사용자 박제 2026-05-17) ──
    # 경로는 달라도 *파일 내용 동일* 한 이미지 (AI 생성기 캐시·fallback 반복 등) 차단
    from JARVIS06_IMAGE.validators.image_validators import _dedupe_by_content_hash
    cleaned, hdupe_cnt = _dedupe_by_content_hash(cleaned)
    if hdupe_cnt:
        msg = f"이미지 내용 해시 dedupe — 다른 경로·같은 내용 {hdupe_cnt}건 제거"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 이미지 파일 존재·크기 검증 (★ 사용자 박제 2026-05-17) — 깨짐 차단 ──
    cleaned, missing_cnt = _validate_image_files(cleaned)
    if missing_cnt:
        msg = f"이미지 파일 검증 — 누락·빈 파일 {missing_cnt}건 제거 (발행 시 깨짐 방지)"
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # enforce_paragraph_pair_image 호출 제거 — 제4조 2026-05-19 개정으로 패턴3·4 허용
    # (문단+문단+이미지 및 이미지+문단+문단 패턴이 허용 패턴이 됨)

    # ── 제4조 금지: 글 3+ 연속 + 이미지 부재 검출 (★ 사용자 박제 2026-05-16) ──
    cleaned, run_cnt, ins_cnt = enforce_image_between_paragraphs(
        cleaned, image_pool=image_pool, source=f"{source}|{platform}",
    )
    if run_cnt:
        msg = (
            f"제4조 패턴3 — 글 연속+이미지부재 {run_cnt}개 섹션 검출 "
            f"(자동 삽입 {ins_cnt}개)"
        )
        violations.append(msg)
        log.warning(f"{tag} {msg}")

    # ── 제9조: 요소 간 여백 적용 ─────────────────────────────────
    cleaned, spacing_cnt = enforce_spacing(cleaned, platform=platform)
    if spacing_cnt:
        msg = f"제9조 여백 {spacing_cnt}건 자동 삽입"
        violations.append(msg)
        log.info(f"{tag} {msg}")

    # ── 제14조: 태그 특수기호 정제 (naver·tistory — tags 전달 시) ─────────
    if tags is not None and platform in ("naver", "tistory"):
        try:
            from shared.seo import sanitize_tags as _stg
            _orig = list(tags)
            tags[:] = _stg(tags, max_count=10)
            if tags != _orig:
                msg = f"제14조 태그 정제 [{platform}] {_orig} → {tags}"
                violations.append(msg)
                log.info(f"{tag} {msg}")
        except Exception as _te:
            log.warning(f"{tag} 제14조 태그 정제 실패: {_te}")

    # ── 제2조: 진실성 감사 — 발행 직전 수치·통계 스캔 ──────────────
    # (★ 사용자 박제 2026-05-19 — 차단 아님, 경고 + 텔레그램 알림)
    try:
        full_html = "\n".join(d for t, d in cleaned if t in ('text', 'html') and isinstance(d, str))
        if full_html:
            fact_result = audit_factuality(
                full_html,
                source_data=source_data,
                post_type=post_type,
                notify=True,
            )
            if not fact_result["passed"]:
                msg = f"제2조 진실성 감사 — 의심 수치 {fact_result['count']}개 (발행 계속, 수동 검토 권장)"
                violations.append(msg)
                log.warning(f"{tag} {msg}")
    except Exception as _fe:
        log.warning(f"{tag} 제2조 진실성 감사 실패 (무시): {_fe}")

    if violations:
        summary = "\n".join(f"  • {v}" for v in violations[:10])
        log.warning(f"{tag} {source} 위반 {len(violations)}건:\n{summary}")

    return cleaned, violations


def check_human_intro(
    blocks: list[tuple[str, str]],
    platform: str = "",
) -> list[str]:
    """제0조: 첫 텍스트 블록의 AI식 팩트 오프닝 감지 → 경고 반환 (검사 전용).

    수정 없이 위반 여부만 반환. enforce_supreme_law 내부에서는 fix_human_intro() 사용.
    """
    from JARVIS02_WRITER.length_manager import HUMAN_INTRO_CHARS
    warnings: list[str] = []
    for btype, bdata in blocks:
        if btype not in ('text', 'html'):
            continue
        plain = re.sub(r'<[^>]+>', '', bdata).strip()[:HUMAN_INTRO_CHARS]
        if _AI_OPENER.match(plain):
            snippet = plain[:60].replace('\n', ' ')
            warnings.append(
                f"제0조 위반 [{platform}]: AI식 팩트 오프닝 감지 — '{snippet}...'"
            )
        break
    return warnings


def _generate_human_intro(keyword: str, platform: str) -> str:
    """LLM으로 감성 도입부 생성 (제0조 교체용). 실패 시 빈 문자열 반환."""
    _PLATFORM_HINT = {
        "naver":   "네이버 이웃에게 친근하게 건네는 생활 밀착 블로그",
        "tistory": "실용적 정보를 찾는 독자를 위한 간결한 블로그",
    }
    hint = _PLATFORM_HINT.get(platform, "블로그")
    # 제0조 분량 박제 — build_length_phrase 로 단일 진입점
    prompt = (
        f"다음 조건으로 블로그 감성 도입부 {_L_phrase(_L_HUMAN_INTRO_SENTS)}을 작성하세요.\n"
        f"- 주제: {keyword}\n"
        f"- 블로그 성격: {hint}\n"
        f"- 조건: 숫자·지표·데이터로 시작 금지. 개인 일상 관찰, 공감 질문, 계절·분위기 연상, "
        f"최근 경험담, 독자에게 건네는 말투 중 하나로 시작.\n"
        f"- 출력: 문장만. 따옴표·설명·번호 없이."
    )
    try:
        from shared.llm import invoke_text
        result = invoke_text("writer", prompt, timeout=60)
        if result:
            # HTML 태그 제거 후 순수 텍스트로
            return re.sub(r'<[^>]+>', '', result).strip()
    except Exception as e:
        log.warning(f"[LawEnforcer] 제0조 LLM 생성 실패: {e}")
        _g_report("writer", e, module=__name__)
    return ""


def fix_human_intro(
    blocks: list[tuple[str, str]],
    platform: str = "",
    source: str = "",
) -> tuple[list[tuple[str, str]], list[str]]:
    """제0조: AI식 팩트 오프닝 감지 → LLM 감성 도입부로 자동 교체.

    - AI식 오프닝이 아닌 경우: 블록 그대로 반환, violations 빈 리스트
    - AI식 오프닝인 경우: LLM 생성 감성 도입 텍스트를 첫 블록 앞에 삽입
      LLM 실패 시: 경고만 반환 (발행 차단하지 않음)
    """
    from JARVIS02_WRITER.length_manager import HUMAN_INTRO_CHARS
    violations: list[str] = []
    tag = f"[LawEnforcer/제0조/{platform or '?'}]"

    # 첫 텍스트 블록 위치 탐색
    first_text_idx: int | None = None
    for idx, (btype, bdata) in enumerate(blocks):
        if btype not in ('text', 'html'):
            continue
        plain = re.sub(r'<[^>]+>', '', bdata).strip()[:HUMAN_INTRO_CHARS]
        if _AI_OPENER.match(plain):
            first_text_idx = idx
            snippet = plain[:60].replace('\n', ' ')
            violations.append(
                f"제0조 위반 [{platform}]: AI식 오프닝 감지 → 자동 교체 — '{snippet}...'"
            )
            log.warning(f"{tag} AI식 오프닝 감지: '{snippet}...'")
        break  # 첫 텍스트 블록만

    if first_text_idx is None:
        return blocks, violations  # 위반 없음

    # 키워드 추출 (블록 전체에서 첫 명사 추출 시도)
    all_text = " ".join(
        re.sub(r'<[^>]+>', '', d) for t, d in blocks if t in ('text', 'html')
    )
    # 간이 키워드: 블록에서 가장 많이 등장하는 2~5글자 한글 단어
    words = re.findall(r'[가-힣]{2,5}', all_text)
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    keyword = max(freq, key=freq.get) if freq else (source or "오늘의 이슈")

    new_intro = _generate_human_intro(keyword, platform)
    if not new_intro:
        log.warning(f"{tag} LLM 생성 실패 — 차단 마커 추가")
        # ★ 차단 마커: AI식 오프닝 그대로 발행 불가 → is_blocking() 이 감지해 발행 차단
        violations.append("★차단★ 제0조 LLM 생성 실패 — AI식 오프닝 발행 불가")
        return blocks, violations

    # 감성 도입부 블록을 첫 텍스트 블록 앞에 삽입
    intro_block = ('text', new_intro)
    new_blocks = list(blocks)
    new_blocks.insert(first_text_idx, intro_block)
    log.info(f"{tag} 감성 도입부 자동 삽입: '{new_intro[:40]}...'")
    violations.append(f"제0조 자동 수정 [{platform}]: 감성 도입부 삽입 완료")
    return new_blocks, violations


# ── 제9조: 요소 간 여백 ─────────────────────────────────────────
# ★ 사용자 박제 2026-05-14 — TinyMCE·티스토리 자동 정리로 빈 <p>&nbsp;</p> 압축 사고
# 발생. style 속성 명시로 *발행 후에도 시각 여백 유지* 보장.
# ★ ERRORS [136] 사용자 박제 2026-05-17 — block 내부 *연속 빈 p / 연속 br* 누수 차단.
_SECTION_IMG_PAT = re.compile(r'heading_|section_|economic_h2_', re.IGNORECASE)
_HEADING_TAG_PAT = re.compile(r'^\s*<h[1-6][\s>]', re.IGNORECASE)
_SPACER_1 = '<p style="margin:0 0 1em 0;line-height:1.8;">&nbsp;</p>'
_SPACER_2 = '<p style="margin:0 0 1em 0;line-height:1.8;">&nbsp;</p><p style="margin:0 0 1em 0;line-height:1.8;">&nbsp;</p>'

# ★ 누수 차단 정규식 — block 내부에 LLM/플랫폼 자동 생성 빈 p / 다중 br 잔존 시 압축.
#   2개 이상 연속 빈 <p>&nbsp;</p> → 1개 (_SPACER_1) 로 압축 (★ 사용자 박제 2026-05-19).
#   2개 이상 연속 <br> → 1개로 압축.
_EXCESSIVE_EMPTY_P = re.compile(
    r'(?:<p[^>]*>\s*(?:&nbsp;| |\s)*</p>\s*){2,}',
    re.IGNORECASE,
)
_EXCESSIVE_BR = re.compile(r'(?:<br\s*/?>\s*){2,}', re.IGNORECASE)


def _compress_excessive_whitespace(html: str) -> tuple[str, int]:
    """block 내부 HTML 의 *2개 이상 연속 빈 p* 또는 *2개 이상 연속 br* 압축.

    ★ 사용자 박제 2026-05-19 — 문단↔문단, 문단↔이미지, 이미지↔문단 사이 1칸(1행) 고정.
    2칸, 3칸 금지. 2개 이상 연속 빈 p/br → 1개로 압축.
    """
    if not html:
        return html, 0
    fix_count = 0
    new_html, n1 = _EXCESSIVE_EMPTY_P.subn(_SPACER_1 + ' ', html)
    fix_count += n1
    new_html, n2 = _EXCESSIVE_BR.subn('<br>', new_html)
    fix_count += n2
    return new_html, fix_count


def _is_section_header(btype: str, bdata: str) -> bool:
    """소제목 블록 여부.

    해당하는 경우:
    - image 블록 + 파일명에 heading_/section_/economic_h2_ 포함 (소제목 이미지)
    - html/text 블록 + <h1>~<h6> 태그로 시작 (HTML 헤더)
    """
    if btype == 'image':
        return bool(_SECTION_IMG_PAT.search(str(bdata)))
    if btype in ('html', 'text'):
        return bool(_HEADING_TAG_PAT.match(bdata))
    return False


_BLANK_TEXT_PAT = re.compile(
    r'^\s*(?:<p[^>]*>\s*(?:&nbsp;|\xa0|\s)*</p>\s*|<br\s*/?>\s*)*$',
    re.IGNORECASE,
)

# ★ 블록 끝 빈 줄 제거 정규식 (사용자 박제 2026-05-25 — 간격 단일 진입점)
# LLM이 텍스트 블록 끝에 <p><br></p>, <p>&nbsp;</p>, <br> 를 추가하면
# enforce_spacing 이 그 뒤에 spacer 를 또 추가 → 2~5줄 여백 사고.
# 블록 끝의 빈 줄 HTML 을 제거하면 spacer 1줄만 남음.
_TRAILING_BLANK_RE = re.compile(
    r'(\s*(?:<p[^>]*>\s*(?:<br\s*/?>|&nbsp;|\xa0|\s)*</p>|<br\s*/?>)\s*)+$',
    re.IGNORECASE,
)


def _strip_trailing_blank(html: str) -> str:
    """★ 블록 끝의 빈 줄 HTML 제거 — enforce_spacing 전처리 필수.

    <p><br></p>, <p>&nbsp;</p>, <br> 등이 블록 끝에 있으면 제거.
    이렇게 해야 enforce_spacing 이 삽입하는 spacer 1줄과 합쳐져도 총 1줄만 됨.
    """
    if not html:
        return html
    return _TRAILING_BLANK_RE.sub('', html).rstrip()


def _is_blank_text(btype: str, bdata: str) -> bool:
    """&nbsp; 전용 빈 텍스트 블록 여부 — spacer 와 동일 취급."""
    if btype not in ('text', 'html'):
        return False
    stripped = re.sub(r'<[^>]+>', '', str(bdata)).replace('&nbsp;', '').replace('\xa0', '').strip()
    return not stripped


def _is_content_block(btype: str) -> bool:
    """텍스트·HTML·이미지 등 실질 콘텐츠 블록 여부."""
    return btype in ('text', 'html', 'image')


def enforce_spacing(
    blocks: list[tuple[str, str]],
    platform: str = "",
) -> tuple[list[tuple[str, str]], int]:
    """제9조: 요소 간 여백 규정 적용 (★ 사용자 박제 2026-05-15 — 누수 보강).

    원칙: 직전이 콘텐츠 블록이고 현재도 콘텐츠 블록이면 *무조건* 여백.
    - 글↔글 / 글↔이미지 / 이미지↔글 / 이미지↔이미지: spacer 1줄
    - 소제목(h1~h6 또는 heading_/section_/economic_h2_) 앞: spacer 2줄
    Returns (new_blocks, fix_count)
    """
    if not blocks:
        return blocks, 0

    fix_count = 0

    # ── 전처리: &nbsp; 전용 빈 text 블록 → spacer 흡수 ──────────────
    # LLM 이 <p>&nbsp;</p> 를 text 블록으로 생성하면 enforce_spacing 이
    # 그 앞뒤에 spacer 를 추가해 2~4줄 여백이 되는 사고 차단.
    # ★ 추가(2026-05-25): 텍스트 블록 끝의 빈 줄(<p><br></p> 등) 제거.
    merged: list[tuple[str, str]] = []
    for btype, bdata in blocks:
        # ★ 블록 끝 빈 줄 strip (법집행 전 필수 — 사용자 박제 2026-05-25)
        if btype in ('text', 'html') and isinstance(bdata, str):
            stripped = _strip_trailing_blank(bdata)
            if stripped != bdata:
                fix_count += 1
            bdata = stripped
        if _is_blank_text(btype, bdata):
            # 직전이 이미 spacer 면 그냥 버림, 아니면 spacer 로 교체
            if merged and merged[-1][0] == 'spacer':
                fix_count += 1  # 중복 제거
            else:
                merged.append(('spacer', _SPACER_1))
                fix_count += 1
        else:
            merged.append((btype, bdata))

    # ── 본처리: 콘텐츠 블록 사이 spacer 삽입 ───────────────────────
    result: list[tuple[str, str]] = []

    for i, (btype, bdata) in enumerate(merged):
        if i == 0:
            result.append((btype, bdata))
            continue

        prev_type, prev_data = merged[i - 1]

        # 직전이 spacer → 소제목 앞이면 2행으로 업그레이드, 아니면 그냥 통과
        if prev_type == 'spacer':
            if _is_section_header(btype, bdata) and result and result[-1][0] == 'spacer':
                if str(result[-1][1]).count('<p ') < 2:
                    result[-1] = ('spacer', _SPACER_2)
                    fix_count += 1
            result.append((btype, bdata))
            continue

        # 직전이 실질 콘텐츠 블록일 때만 여백 처리
        if not _is_content_block(prev_type):
            result.append((btype, bdata))
            continue

        # 현재가 소제목 → 2줄
        if _is_section_header(btype, bdata):
            result.append(('spacer', _SPACER_2))
            fix_count += 1
        # 현재가 일반 콘텐츠 → 1줄
        elif _is_content_block(btype):
            result.append(('spacer', _SPACER_1))
            fix_count += 1

        result.append((btype, bdata))

    # ── 후처리: 연속 spacer 병합 → 최대 2줄(_SPACER_2) ─────────────
    # 전처리 후에도 spacer 가 연속으로 남아있으면 1개로 병합.
    deduped: list[tuple[str, str]] = []
    for btype, bdata in result:
        if btype == 'spacer' and deduped and deduped[-1][0] == 'spacer':
            # 둘 중 더 큰 쪽(2줄짜리) 유지
            prev_lines = str(deduped[-1][1]).count('<p ')
            cur_lines = str(bdata).count('<p ')
            if cur_lines > prev_lines:
                deduped[-1] = (btype, bdata)
            fix_count += 1
        else:
            # 블록 내부 연속 빈 p/br 압축
            if btype != 'spacer' and isinstance(bdata, str):
                bdata, _cnt = _compress_excessive_whitespace(bdata)
                fix_count += _cnt
            deduped.append((btype, bdata))

    return deduped, fix_count


def is_blocking(violations: list[str]) -> tuple[bool, list[str]]:
    """★차단★ 마커가 있는 위반 항목 감지 → 발행 차단 여부 반환.

    Returns:
        (should_block, blocking_messages)
        should_block=True 이면 호출부에서 해당 플랫폼 발행을 건너뛰어야 함.
    """
    critical = [v for v in violations if "★차단★" in v]
    return bool(critical), critical


def notify_violations(violations: list[str], platform: str, source: str = "") -> None:
    """위반 항목 로그만 기록 — 텔레그램 알림 제거 (사용자 요청 2026-05-26)."""
    if not violations:
        return
    for v in violations[:8]:
        log.debug(f"[LawEnforcer] 헌법 위반 자동 수정 [{platform}] {source}: {v}")


_SUPREME_LAW_PATH = None  # 지연 import — 모듈 import 시 파일 부재로 깨지지 않도록

# ★ ADR 008 Phase 3 (사용자 박제 2026-05-17) — 헌법 본문 복제 제거.
# 옛 fallback 은 BLOG_SUPREME_LAW.md 의 자연어 인용 *전체 복제* → drift 위험.
# 단일 진실 소스 원칙: 헌법 본문은 BLOG_SUPREME_LAW.md 가 유일. fallback 은 *최소 비상 알림* 만.
# ★ ERRORS [142] 사용자 박제 2026-05-17 — 분량은 length_manager 위임. fallback도 직박제 X.
def _build_law_fallback_block() -> str:
    """헌법 로드 실패 시 비상 fallback — 분량 숫자는 length_manager 위임 (직박제 금지)."""
    try:
        from JARVIS02_WRITER import length_manager as _L
        _target_sents = _L.TARGET_SENTENCES
        _target_kor = _L.TARGET_KOREAN
        _disc_sents = _L.DISCLAIMER_SENTS
        _disc_kor = _L.DISCLAIMER_KOREAN
    except Exception:
        _target_sents, _target_kor, _disc_sents, _disc_kor = 25, 1250, 1, 50
    return (
        "[헌법 로드 실패 — 비상 모드]\n"
        "BLOG_SUPREME_LAW.md 파일을 읽지 못했습니다. *안전 모드* 로 작성:\n"
        "- 감성·자연스러운 한국어 문장만 사용. AI식 팩트 오프닝·이모지·마크다운 금지.\n"
        "- 미완성 표현('미정'·'TBD'·'데이터 없음') 절대 금지. 실제 데이터 근거 글만.\n"
        "- 문단-이미지 배치: 이미지 연속 금지, 문단 3개+ 연속 금지 (제4조 허용 패턴 4가지). 빈 소제목 금지.\n"
        f"- 한 <p> 태그 최대 2문장. 분량 약 {_target_sents}문장(약 {_target_kor}자) 목표.\n"
        f"- 금융·투자 글은 마무리에 면책 {_disc_sents}문장(약 {_disc_kor}자) (정보 제공 목적·투자 권유 아님 — 표현은 매번 변형).\n"
        "→ 운영자: shared/precommit_check.py + BLOG_SUPREME_LAW.md 무결성 확인 필요.\n"
    )

# 호환 alias — 기존 호출자 (_LAW_FALLBACK_BLOCK 참조) 가 함수 호출 결과 받음
_LAW_FALLBACK_BLOCK = _build_law_fallback_block()


def parse_seo_block(platform: str) -> str:
    """BLOG_SUPREME_LAW.md 제15조에서 플랫폼별 SEO 지침 텍스트 추출.

    마커: <!-- seo:platform:start --> ... <!-- seo:platform:end -->
    seo_standards.py 가 PLATFORM_STANDARDS["seo_prompt"] 에 주입하기 위해 호출.
    캐시 없음 — 헌법 수정 즉시 반영.
    """
    from pathlib import Path
    law_path = Path(__file__).parent / "BLOG_SUPREME_LAW.md"
    try:
        text = law_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[LawEnforcer] parse_seo_block 실패: {e}")
        return ""
    m = re.search(
        rf'<!--\s*seo:{re.escape(platform)}:start\s*-->\n(.*?)<!--\s*seo:{re.escape(platform)}:end\s*-->',
        text, re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def parse_diff_block(platform: str) -> str:
    """BLOG_SUPREME_LAW.md 제15조에서 플랫폼별 차별화 앵글 텍스트 추출.

    마커: <!-- diff:platform:start --> ... <!-- diff:platform:end -->
    seo_standards.py 가 PLATFORM_STANDARDS["differentiation_angle"] 에 주입하기 위해 호출.
    캐시 없음 — 헌법 수정 즉시 반영.
    """
    from pathlib import Path
    law_path = Path(__file__).parent / "BLOG_SUPREME_LAW.md"
    try:
        text = law_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[LawEnforcer] parse_diff_block 실패: {e}")
        return ""
    m = re.search(
        rf'<!--\s*diff:{re.escape(platform)}:start\s*-->\n(.*?)<!--\s*diff:{re.escape(platform)}:end\s*-->',
        text, re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def parse_seo_meta(platform: str) -> dict:
    """BLOG_SUPREME_LAW.md 제15조에서 플랫폼별 SEO 메타 필드(key:value) 파싱.

    마커: <!-- seo-meta:platform:start --> ... <!-- seo-meta:platform:end -->
    반환: {"algorithm": "...", "heading_structure": "...", "forbidden": [...], ...}
    캐시 없음 — 헌법 수정 즉시 반영.
    """
    from pathlib import Path
    law_path = Path(__file__).parent / "BLOG_SUPREME_LAW.md"
    try:
        text = law_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[LawEnforcer] parse_seo_meta 실패: {e}")
        return {}
    m = re.search(
        rf'<!--\s*seo-meta:{re.escape(platform)}:start\s*-->\n(.*?)<!--\s*seo-meta:{re.escape(platform)}:end\s*-->',
        text, re.DOTALL,
    )
    if not m:
        return {}
    result: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            continue
        if key == "forbidden":
            result[key] = [v.strip() for v in val.split(",") if v.strip()]
        else:
            result[key] = val
    return result


def parse_svg_rules(platform: str) -> str:
    """BLOG_SUPREME_LAW.md 제11조에서 플랫폼별 SVG 디자인 규정 텍스트 추출.

    마커: <!-- svg:platform:start --> ... <!-- svg:platform:end -->
    html_writer 파일들의 _SVG_DESIGN_RULES 변수가 이 함수로 로드.
    캐시 없음 — 헌법 수정 즉시 반영.
    """
    from pathlib import Path
    law_path = Path(__file__).parent / "BLOG_SUPREME_LAW.md"
    try:
        text = law_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[LawEnforcer] parse_svg_rules 실패: {e}")
        return ""
    m = re.search(
        rf'<!--\s*svg:{re.escape(platform)}:start\s*-->\n(.*?)<!--\s*svg:{re.escape(platform)}:end\s*-->',
        text, re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def build_writing_rules_block() -> str:
    """BLOG_SUPREME_LAW.md 를 매 호출마다 동적 로드 → 조항 핵심 내용 추출.

    ★ 캐시 없음. 헌법 수정 즉시 모든 작성 프롬프트에 반영 — CLAUDE_WRITER.md 명세 준수.
    LLM 프롬프트에 주입되는 supreme_block 단일 진입점. 각 작성 프롬프트는
    ★ 제N조 자연어 인용을 직접 박지 말고 이 함수가 반환한 블록을 그대로 사용.

    추출 전략 (조항당 최대 600자):
        ① 첫 bold **...** 선언 (주제 원칙)
        ② 첫 번호 항목 1·2·3 (핵심 세부 규칙 — 최대 3개)
        ③ bold·번호 없으면 첫 평문 줄 2개

    프롬프트 주입 제외 조항 (코드 규정이라 LLM 프롬프트에 불필요):
        제10조 — DB 저장 의무 (발행 흐름 코드)
        제11조 — 차트/그래프 디자인 동적 (이미지 생성 코드)
        제12조 — 같은 글 내 시각화 중복 금지 (이미지 생성 코드)
    """
    from pathlib import Path
    law_path = Path(__file__).parent / "BLOG_SUPREME_LAW.md"
    SKIP_ARTICLES = {"제10조", "제11조", "제12조", "제15조", "제16조"}

    try:
        text = law_path.read_text(encoding="utf-8")
    except Exception as e:
        log.warning(f"[LawEnforcer] BLOG_SUPREME_LAW.md 로드 실패: {e} — 폴백 블록 사용")
        return _LAW_FALLBACK_BLOCK

    # 조항 헤더 매치: "## 제0조 — 감성 도입부 (★ 최상위 ...)"
    sections = list(re.finditer(
        r'^##\s+(제[\d\-A-Z]+조)\s*—\s*(.+?)\s*$',
        text, re.MULTILINE,
    ))
    if not sections:
        log.warning("[LawEnforcer] BLOG_SUPREME_LAW.md 조항 헤더 파싱 실패 — 폴백 블록 사용")
        return _LAW_FALLBACK_BLOCK

    def _extract_article_content(body: str) -> str:
        """조항 본문 → 핵심 내용 추출 (bold 선언 + 번호 항목 3개까지)."""
        parts: list[str] = []

        # ① 첫 bold **...** 선언 (주제 원칙 — 15자 이상)
        bold = re.search(r'\*\*\s*([^*\n]{15,})\*\*', body)
        if bold:
            parts.append(re.sub(r'\*+', '', bold.group(1)).strip())

        # ② 번호 항목 1·2·3 (실질 세부 규칙)
        numbered = re.findall(r'^\s*(\d+)\.\s+\**\s*([^\n]+)', body, re.MULTILINE)
        added = 0
        for num, content in numbered:
            if int(num) > 6 or added >= 3:
                break
            cleaned = re.sub(r'\*+', '', content).strip()
            cleaned = re.sub(r'`([^`]+)`', r'\1', cleaned)
            # 너무 긴 항목은 100자에서 자름 (번호 항목은 압축)
            if len(cleaned) > 100:
                cleaned = cleaned[:97] + "..."
            parts.append(f"  {num}. {cleaned}")
            added += 1

        # ③ bold·번호 없으면 첫 평문 줄 2개
        if not parts:
            plain_count = 0
            for line in body.splitlines():
                s = line.strip()
                if not s or s.startswith(('|', '-', '*', '#', '`', '>')):
                    continue
                if re.match(r'[가-힣A-Z]', s) and len(s) >= 10:
                    cleaned = re.sub(r'`([^`]+)`', r'\1', s)
                    cleaned = re.sub(r'\*+', '', cleaned).strip()
                    parts.append(cleaned)
                    plain_count += 1
                    if plain_count >= 2:
                        break

        result = "\n".join(parts)
        # 조항당 최대 600자
        if len(result) > 600:
            result = result[:597] + "..."
        return result

    out: list[str] = [
        "[BLOG_SUPREME_LAW.md — 블로그 글쓰기 최상위 헌법 (단일 진실 소스)]",
        "이 헌법은 모든 발행 글에 무조건 적용된다. 충돌 시 이 헌법이 우선.",
        "",
    ]
    for i, m in enumerate(sections):
        article = m.group(1)
        if article in SKIP_ARTICLES:
            continue
        # 부제(괄호) 제거: "감성 도입부 (★ 최상위 ...)" → "감성 도입부"
        title = re.sub(r'\s*\(.*?\)\s*$', '', m.group(2)).strip()
        # 본문 영역: 현재 헤더 끝 ~ 다음 헤더 시작
        start = m.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        body = text[start:end]

        content = _extract_article_content(body)
        if not content:
            continue
        out.append(f"{article} ({title}):")
        out.append(content)
        out.append("")

    out.append("위 헌법 위반 시 발행 차단 또는 자동 수정.")
    return "\n".join(out)


def wrap_prompt_with_law(prompt: str) -> str:
    """원고 생성 프롬프트에 BLOG_SUPREME_LAW를 자동 주입하는 래퍼.

    CLAUDE_WRITER.md 의무 2단계 규정 1단계를 단일 함수로 보장.
    모든 원고 생성 함수에서 직접 `build_writing_rules_block()` + f-string 대신
    이 함수를 사용하면 규정 누락 불가.

    Usage:
        prompt = wrap_prompt_with_law(f"[원고 작성 요청] ...")
        raw = invoke_text("writer", prompt, ...)

    Note:
        이미 supreme_block이 앞에 붙어 있는 경우(중복 주입 방지):
        '블로그 글쓰기 최상위 헌법' 문자열이 있으면 그대로 반환.
    """
    if "블로그 글쓰기 최상위 헌법" in prompt:
        return prompt  # 이미 주입됨 — 중복 방지
    supreme_block = build_writing_rules_block()
    return f"{supreme_block}\n\n{prompt}"


def audit_factuality(
    html: str,
    source_data: str = "",
    post_type: str = "",
    notify: bool = True,
) -> dict:
    """제2조 진실성 감사 — 발행 직전 수치·통계 패턴 스캔.

    ★ 사용자 박제 2026-05-19 — 팩트가 생명. 거짓 정보·허위 통계 절대 금지.

    동작:
      1. 본문에서 구체적 수치 패턴 추출 (%, 억, 조, 지수 등)
      2. source_data 제공 시: LLM으로 수치가 출처 데이터에 있는지 교차 검증
      3. 의심 항목 발견 시: 텔레그램 경고 전송 (notify=True)

    Args:
        html:        발행 직전 HTML 본문
        source_data: 글 작성에 사용된 원본 데이터 (시장 데이터, API 응답 등)
        post_type:   "economic" / "theme" / "" (경제글은 실제 데이터 있음, 테마글은 없음)
        notify:      텔레그램 경고 전송 여부

    Returns:
        {"suspicious": [str], "count": int, "passed": bool}
    """
    import re

    # ── 수치 패턴 추출 ─────────────────────────────────────────────
    # 구체적 숫자 패턴: 47.3%, 2,340억, 1.23배, 상위 5% 등
    num_patterns = re.findall(
        r'\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*%|\s*억|\s*조|\s*배|\s*원|\s*달러|\s*엔|\s*위안|\s*p\b|\s*bp\b)',
        html,
        re.IGNORECASE,
    )
    # 소수점 포함 수치 (47.3 같은 구체적 숫자 — 정수는 제외, 너무 흔함)
    decimal_nums = re.findall(r'\b\d{1,3}\.\d+\b', html)

    suspicious: list[str] = []

    # 테마글 — 실제 출처 데이터가 거의 없으므로 구체적 수치는 의심
    if post_type == "theme" and num_patterns:
        for n in num_patterns[:10]:  # 최대 10개
            suspicious.append(f"테마글 수치 (출처 미확인): {n.strip()}")

    # 출처 데이터와 교차 검증 (제공된 경우)
    if source_data and num_patterns:
        try:
            from shared.llm import invoke_text
            nums_sample = ", ".join(num_patterns[:15])
            prompt = f"""아래 [출처 데이터]에 존재하지 않는 수치를 [본문 수치 목록]에서 찾아라.

[출처 데이터]
{source_data[:2000]}

[본문 수치 목록]
{nums_sample}

출처에 없는 수치만 JSON 배열로 반환. 없으면 [].
예: ["47.3%", "2,340억"]"""
            result = invoke_text("writer_fast", prompt, temperature=0.1)
            import json as _json
            try:
                unverified = _json.loads(result.strip())
                if isinstance(unverified, list):
                    suspicious.extend([f"출처 미확인 수치: {v}" for v in unverified[:5]])
            except Exception:
                pass
        except Exception:
            pass

    passed = len(suspicious) == 0

    # ── 텔레그램 경고 ─────────────────────────────────────────────
    if not passed and notify:
        try:
            import os, requests as _req
            token = os.getenv('TELEGRAM_TOKEN', '')
            chat  = os.getenv('TELEGRAM_CHAT_ID', '')
            if token and chat:
                lines = "\n".join(f"  • {s}" for s in suspicious[:8])
                msg = (
                    f"⚠️ *제2조 진실성 감사 — 의심 수치 발견*\n"
                    f"포스트 타입: {post_type or '미지정'}\n"
                    f"의심 항목 {len(suspicious)}개:\n{lines}\n"
                    f"발행 계속 진행 (차단 아님) — 수동 검토 권장."
                )
                _req.post(
                    f'https://api.telegram.org/bot{token}/sendMessage',
                    json={'chat_id': chat, 'text': msg, 'parse_mode': 'Markdown'},
                    timeout=5,
                )
        except Exception:
            pass

    if not passed:
        log.warning(f"[factuality] 제2조 의심 수치 {len(suspicious)}개: {suspicious[:3]}")

    return {"suspicious": suspicious, "count": len(suspicious), "passed": passed}


# ══════════════════════════════════════════════════════════════════════
#  발행 전 사실성 게이트 — factuality_issues (★ 사용자 박제 2026-06-28)
#
#  audit_factuality(수치·비차단 경고)의 *차단 게이트* 버전.
#  제2조 "팩트가 생명" 을 발행 *전* harness Layer 3 차단 게이트로 승격하기 위한
#  핵심 로직. 모든 검증 가능한 사실 주장(수치·고유명사·날짜)을 ① 수집 출처
#  코퍼스로 1차 grounding → ② 미확인 주장만 웹 재검증(JARVIS09 web_verify).
#
#  ★ 두 정책 (사용자 박제 2026-06-28):
#   1. 테마글 완화 — 출처 코퍼스가 약하면(테마글·빈 코퍼스) 웹 재검증을
#      1차 근거로 사용, "웹에서도 확인 불가한 것만 차단".
#   2. 검증 실패 분리 — 사실 판정 LLM 실패 = 차단(fail-closed),
#      웹 인프라 실패(타임아웃·전송오류) = 통과(fail-open).
#
#  반환은 dict(harness 비의존). harness Layer 3 연결(PR5)에서 blocked 를
#  Issue(step=WRITER) 로 변환한다. LLM 호출은 invoke_text("fact_judge") 단일 진입점.
#  내부 LLM 헬퍼(_extract_claims/_ground_unsupported/_web_confirms)는 모듈
#  레벨이라 테스트에서 monkeypatch 가능.
# ══════════════════════════════════════════════════════════════════════

class FactJudgeError(Exception):
    """사실 판정 LLM 실패 — 응답 없음·JSON 파싱 실패 등. 게이트는 fail-closed(차단)."""


_FACT_MIN_SOURCE_CHARS = 200    # 이 미만이면 출처 약함 → 웹 1차 근거 (테마글 완화)
_FACT_MAX_CLAIMS = 25           # 검사 주장 상한 (latency 가드)
_FACT_MAX_WEB_CHECKS = 8        # 웹 재검증 호출 상한 (발행 임계경로 stall 방지)
_FACT_SOURCE_CORPUS_CAP = 12000  # 출처 코퍼스 길이 상한 (도메인 무관 — 블로그 분량 아님)


def _build_source_corpus(source_docs, market_data=None) -> str:
    """수집 문서 리스트 + 시장 데이터를 grounding 코퍼스 문자열로 합친다.

    source_docs 원소는 JARVIS09 문서객체(.cleaned_text/.raw_text), str, dict 모두 허용.
    """
    import json as _json
    parts: list[str] = []
    for d in (source_docs or []):
        if isinstance(d, str):
            parts.append(d)
        elif isinstance(d, dict):
            parts.append(str(d.get("cleaned_text") or d.get("raw_text")
                            or d.get("text") or d.get("snippet") or ""))
        else:
            t = (getattr(d, "cleaned_text", None) or getattr(d, "raw_text", None)
                 or getattr(d, "text", None))
            if t:
                parts.append(str(t))
    if market_data:
        md = (market_data if isinstance(market_data, str)
              else _json.dumps(market_data, ensure_ascii=False, default=str))
        parts.append("[시장 데이터]\n" + md)
    corpus = "\n\n".join(p for p in parts if p and p.strip())
    return corpus[:_FACT_SOURCE_CORPUS_CAP]


def _fact_strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _fact_parse_json_list(text: str) -> list:
    """LLM 응답에서 JSON 배열 추출. 없거나 파싱 실패 시 FactJudgeError (fail-closed 트리거)."""
    import json as _json
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        raise FactJudgeError("LLM 응답에 JSON 배열 없음 (호출 실패 가능성)")
    try:
        v = _json.loads(m.group())
    except Exception as e:
        raise FactJudgeError(f"JSON 파싱 실패: {e}")
    if not isinstance(v, list):
        raise FactJudgeError("JSON 최상위가 배열 아님")
    return v


def _extract_claims(html: str) -> list[dict]:
    """본문에서 검증 가능한 사실 주장 추출·분류. (fail-closed: LLM 실패 시 FactJudgeError)"""
    body = _fact_strip_html(html).strip()
    if not body:
        return []
    from shared.llm import invoke_text
    prompt = (
        "다음 블로그 본문에서 *검증 가능한 사실 주장* 만 추출하라. "
        "수치·통계, 고유명사(기업·인물·기관·제품), 날짜·기간, 인과/단정 서술을 대상으로 한다. "
        "주관적 감상·일반론·의견은 제외한다.\n"
        "각 주장을 JSON 배열로 반환: "
        '[{"text":"주장 원문(본문에서 그대로)","type":"numeric|entity|date|causal","key":true/false}]. '
        'key 는 글의 핵심 정보면 true. 없으면 []. JSON 외 다른 말 금지.\n\n'
        "[본문]\n" + body
    )
    resp = invoke_text("fact_judge", prompt)
    out: list[dict] = []
    for it in _fact_parse_json_list(resp)[:_FACT_MAX_CLAIMS]:
        if isinstance(it, dict) and str(it.get("text", "")).strip():
            out.append({
                "text": str(it["text"]).strip(),
                "type": str(it.get("type", "fact")),
                "key": bool(it.get("key", False)),
            })
        elif isinstance(it, str) and it.strip():
            out.append({"text": it.strip(), "type": "fact", "key": False})
    return out


def _ground_unsupported(claims: list[dict], corpus: str) -> list[str]:
    """출처 코퍼스가 뒷받침하지 *못하는* 주장 텍스트 목록. (fail-closed: LLM 실패 시 FactJudgeError)"""
    from shared.llm import invoke_text
    claim_lines = "\n".join(f"- {c['text']}" for c in claims)
    prompt = (
        "[출처]에 근거가 *없는* 주장만 골라라. [출처]에 직접 등장하거나 "
        "명백히 추론되는 주장은 제외한다. "
        "근거 없는 주장의 원문을 [주장 목록] 그대로 JSON 배열로 반환. 모두 근거 있으면 []. "
        "JSON 외 다른 말 금지.\n\n"
        "[출처]\n" + (corpus or "(없음)") + "\n\n[주장 목록]\n" + claim_lines
    )
    resp = invoke_text("fact_judge", prompt)
    return [str(x).strip() for x in _fact_parse_json_list(resp) if str(x).strip()]


def _web_confirms(claim: str, evidence: list[dict]) -> bool:
    """웹 근거가 주장을 뒷받침하는지 판정. 근거 없으면 False(=확인 불가).

    (fail-closed: 판정 LLM 자체 실패 시 FactJudgeError → 호출자가 차단)
    """
    import json as _json
    ev = "\n".join(
        f"- {e.get('title','')}: {e.get('snippet','')}"
        for e in (evidence or []) if isinstance(e, dict)
    ).strip()
    if not ev:
        return False  # 웹은 됐으나 근거 못 찾음 → 확인 불가
    from shared.llm import invoke_text
    prompt = (
        "아래 [웹 근거]가 [주장]을 사실로 뒷받침하는가? "
        'JSON 한 줄로만 답: {"confirmed": true/false}. '
        "근거가 주장과 무관하거나 모순되면 false.\n\n"
        "[주장]\n" + claim + "\n\n[웹 근거]\n" + ev
    )
    resp = invoke_text("fact_judge", prompt)
    m = re.search(r"\{.*\}", resp or "", re.DOTALL)
    if not m:
        raise FactJudgeError("웹 근거 판정 응답 파싱 실패")
    try:
        return bool(_json.loads(m.group()).get("confirmed", False))
    except Exception as e:
        raise FactJudgeError(f"웹 근거 판정 JSON 실패: {e}")


def factuality_issues(
    html: str,
    source_docs=None,
    post_type: str = "",
    web_verify_fn=None,
    market_data=None,
) -> dict:
    """발행 전 사실성 차단 게이트 — 출처 대조 + 웹 재검증.

    Args:
        html:          발행 직전 HTML 본문
        source_docs:   수집 출처 (JARVIS09 문서 리스트 / str / dict 혼용 허용)
        post_type:     "economic" / "theme" / "" — theme 은 출처 약함 → 웹 1차 근거
        web_verify_fn: 웹 재검증 함수 (보통 JARVIS09.web_verify). 호출 시 예외를
                       던지면 *웹 인프라 실패* 로 보고 fail-open(미차단).
        market_data:   글 작성에 쓴 구조화 수치(시장지표 등) — 신뢰 가능한 ground truth.

    Returns:
        {
          "passed": bool,                # 차단 0 이면 True
          "blocked": [{"claim","type","reason"}],
          "checked": int,                # 검사한 주장 수
          "source_weak": bool,
          "policy_notes": [str],         # fail-open 적용 등 정책 기록
        }

    정책:
      - 사실 판정 LLM(_extract/_ground/_web_confirms) 실패 → FactJudgeError → 차단(fail-closed).
      - web_verify_fn 예외(타임아웃·전송오류·자격증명없음) → 미차단(fail-open).
      - 테마글/약한 출처 → 웹 재검증을 1차 근거로 사용, 웹에서도 확인 불가만 차단.
    """
    corpus = _build_source_corpus(source_docs, market_data)
    source_weak = (post_type == "theme") or (len(corpus.strip()) < _FACT_MIN_SOURCE_CHARS)

    def _blocked(reason: str) -> dict:
        return {"passed": False,
                "blocked": [{"claim": "(전체)", "type": "judge_error", "reason": reason}],
                "checked": 0, "source_weak": source_weak, "policy_notes": [reason]}

    # ── 1. 주장 추출 (fail-closed) ──────────────────────────────
    try:
        claims = _extract_claims(html)
    except FactJudgeError as e:
        log.warning(f"[factuality_gate] 주장 추출 실패 → 차단(fail-closed): {e}")
        return _blocked(f"사실성 판정 실패(주장 추출) — 안전 차단: {e}")

    if not claims:
        return {"passed": True, "blocked": [], "checked": 0,
                "source_weak": source_weak, "policy_notes": []}

    # ── 2. 출처 grounding (fail-closed) ─────────────────────────
    if corpus.strip():
        try:
            unsupported = set(_ground_unsupported(claims, corpus))
        except FactJudgeError as e:
            log.warning(f"[factuality_gate] 출처 대조 실패 → 차단(fail-closed): {e}")
            return _blocked(f"사실성 판정 실패(출처 대조) — 안전 차단: {e}")
    else:
        # 출처 코퍼스 없음(테마글 등) → 전 주장이 출처 미확인 → 웹으로 위임
        unsupported = {c["text"] for c in claims}

    pending = [c for c in claims if c["text"] in unsupported]

    # ── 3. 웹 재검증 + 정책 분기 ────────────────────────────────
    blocked: list[dict] = []
    policy_notes: list[str] = []
    web_checks = 0

    for c in pending:
        claim = c["text"]
        if web_verify_fn is not None and web_checks < _FACT_MAX_WEB_CHECKS:
            web_checks += 1
            try:
                evidence = web_verify_fn(claim)            # 인프라 실패 시 예외
            except Exception as e:                         # noqa: BLE001 — fail-open 의도
                policy_notes.append(f"웹 검증 불가 → 미차단(fail-open): {claim} [{type(e).__name__}]")
                continue
            try:
                confirmed = _web_confirms(claim, evidence)  # 판정 LLM 실패 시 fail-closed
            except FactJudgeError as e:
                blocked.append({"claim": claim, "type": c["type"],
                                "reason": f"웹 근거 판정 실패 — 안전 차단(fail-closed): {e}"})
                continue
            if confirmed:
                continue                                    # 웹 확인 → 통과
            blocked.append({"claim": claim, "type": c["type"],
                            "reason": "출처·웹 모두 확인 불가"})
        else:
            # 웹 미가용
            if source_weak:
                # 약한 출처 + 웹 미가용 → 검증 수단 없음 → 대량 차단 방지 fail-open
                policy_notes.append(f"출처 약함·웹 미가용 → 미차단(fail-open): {claim}")
            else:
                # 강한 출처(경제글) + 출처 미확인 → 차단(strict)
                blocked.append({"claim": claim, "type": c["type"],
                                "reason": "출처 미확인(웹 미가용)"})

    return {
        "passed": len(blocked) == 0,
        "blocked": blocked,
        "checked": len(claims),
        "source_weak": source_weak,
        "policy_notes": policy_notes,
    }


__all__ = [
    "enforce_supreme_law", "enforce_spacing", "check_human_intro",
    "fix_human_intro", "notify_violations", "is_blocking",
    "_split_overlong_paragraphs",
    "build_writing_rules_block", "wrap_prompt_with_law",
    "parse_seo_block", "parse_diff_block",
    "parse_seo_meta", "parse_svg_rules",
    "audit_factuality",
    "factuality_issues", "FactJudgeError",
]
# NOTE: ADR 008 Phase 1 — 이미지 함수는 JARVIS06_IMAGE 단일 진입점 강제.
#   enforce_paragraph_pair_image / enforce_image_between_paragraphs / compute_unused_image_pool /
#   _dedupe_* / _validate_image_files / _is_heading_img_path / _is_h2_header
#   → 직접 호출은 `from JARVIS06_IMAGE.validators import ...`
#                또는 `from JARVIS06_IMAGE.injectors import ...` 사용.
