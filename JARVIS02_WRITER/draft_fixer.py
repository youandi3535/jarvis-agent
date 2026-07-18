"""JARVIS02_WRITER/draft_fixer.py
하네스 Layer 3 검증 실패 → 즉시 수정 + GUARDIAN 자가 학습 기록.

설계 원칙:
  - 수정 가능 항목: state 직접 패치 → 재검증 통과 → Issue 0 반환 → Layer 4 진입
  - 수정 불가 항목: 그대로 반환 → harness retry (최대 5회) 또는 abort
  - 모든 수정: report_manual_fix + record_pattern_hit 으로 GUARDIAN 영구 기록
  - 자가 학습: 동일 fingerprint 재발 시 LLM 0 호출, 즉시 패치

수정 가능 분류:
  ✅ 빈 헤더 (제3조)            → HTML regex 제거
  ✅ 이미지 연속 (제4조)        → enforce_text_between_images
  ✅ 헌법 종합 위반 (제3·9조)   → enforce_supreme_law 재실행
  ✅ 연속 빈 p / 연속 br        → _compress_excessive_whitespace (full_html + blocks)
  ✅ 분량 상한 초과              → 말미 <p> 블록 순차 제거 (문장수 상한 내로)
  ✅ 이미지 최소 미달 (제8조)    → AI 사진 추가 (_fix_image_count_underflow)
수정 불가 분류 (재생성 필요):
  ❌ draft success=False         → Layer 2 전체 재실행
  ❌ 본문 한글 200자 미만        → 새 draft 필요
  ❌ 이미지/텍스트 블록 없음     → 새 draft 필요
  ❌ 로그인 만료                 → 사용자 개입 필요
"""
from __future__ import annotations

import re
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  개별 inline 패치 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fix_empty_headers(draft: dict) -> bool:
    """제3조 위반 — 빈 헤더 태그 제거."""
    html = draft.get("html", "")
    if not html:
        return False
    fixed = re.sub(r'<h[1-6][^>]*>\s*</h[1-6]>', '', html)
    if fixed == html:
        return False
    draft["html"] = fixed
    # content 도 동기화 (html_content 기반 발행)
    try:
        from JARVIS02_WRITER.theme_html_writer import extract_text_content
        draft["content"] = extract_text_content(fixed)
    except Exception:
        pass
    return True


def _fix_consecutive_images(draft: dict, platform: str) -> bool:
    """제4조 위반 — 이미지 연속 배치 교정 (텍스트 삽입)."""
    blocks = draft.get("blocks") or []
    if not blocks:
        return False
    try:
        from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
        new_blocks = enforce_text_between_images(
            blocks, source=f"DRAFT-FIX-{platform.upper()}"
        )
        if new_blocks != blocks:
            draft["blocks"] = new_blocks
            return True
        return False
    except Exception as e:
        _log.warning(f"[draft_fixer] enforce_text_between_images 오류: {e}")
        return False


def _fix_law_violations(draft: dict, platform: str) -> bool:
    """제3·4·9조 종합 — enforce_supreme_law 재실행."""
    blocks = draft.get("blocks") or []
    if not blocks:
        return False
    try:
        from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
        new_blocks, viols = enforce_supreme_law(
            blocks, platform, f"DRAFT-FIX-{platform.upper()}"
        )
        notify_violations(viols, platform, f"DRAFT-FIX-{platform.upper()}")
        if new_blocks != blocks or viols:
            draft["blocks"] = new_blocks
            return True
        return False
    except Exception as e:
        _log.warning(f"[draft_fixer] enforce_supreme_law 오류: {e}")
        return False


def _fix_sentence_overflow(draft: dict, issue_str: str) -> bool:
    """분량 상한 초과 — draft["blocks"] 말미 text/html 블록 순차 제거 (문장수 상한까지).

    이슈 문자열 예: "분량 상한 초과: 50문장 > 30문장 (post_type=economic)"

    ★ 2026-07-18 근본 수정 (blocks 를 단일 진실로): 검증(economic_poster/
    trend_theme_writer 의 _layer3_verify_draft)이 draft["blocks"](실제 발행 콘텐츠)
    기준으로 문장수를 세도록 대칭화했으므로, 수정도 동일하게 draft["blocks"] 를
    직접 카운트·제거한다. 예전에는 draft["html"](law_enforcer 정제 *이전* 원본)에서
    말미 <p> 를 제거한 뒤 draft["blocks"](enforce_supreme_law._clean_text 정제
    *이후*)에서 같은 텍스트를 찾아 동기화했는데, 금칙어·이모지·마크다운·공백 정리로
    텍스트가 달라져 "제거 문단 미발견"으로 매번 수정이 무산되고 값비싼 전체 재생성만
    반복되는 문제가 있었다(ERRORS [445] 안전장치가 상시 발동). blocks 를 카운트·제거
    양쪽의 유일한 소스로 삼으면 애초에 동기화 대상이 사라진다.
    보수 원칙: 최소 10문장 이하로는 절대 내려가지 않음 (내용 훼손 방지).
    """
    m = re.search(r'>\s*(\d+)문장', issue_str)
    if not m:
        return False
    max_sents = int(m.group(1))
    if max_sents < 10:
        return False  # 너무 낮으면 안전 거부

    blocks = draft.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return False

    def _block_sents(bdata) -> int:
        if not isinstance(bdata, str):
            return 0
        text = re.sub(r'<[^>]+>', '', bdata)
        return len(re.findall(r'[.!?。]\s*(?=[^<]|$)', text))

    total = sum(_block_sents(bd) for bt, bd in blocks if bt in ("text", "html"))
    if total <= max_sents:
        return False  # 이미 정상

    # 뒤에서부터 text/html 블록 통째로 제거 (최소 10문장 보장)
    new_blocks = list(blocks)
    removed_n = 0
    for i in range(len(new_blocks) - 1, -1, -1):
        if total <= max_sents:
            break
        btype, bdata = new_blocks[i][0], new_blocks[i][1]
        if btype not in ("text", "html"):
            continue
        b_sents = _block_sents(bdata)
        if b_sents == 0:
            continue
        if total - b_sents < 10:
            break
        del new_blocks[i]
        total -= b_sents
        removed_n += 1

    if not removed_n:
        return False

    before = sum(_block_sents(bd) for bt, bd in blocks if bt in ("text", "html"))
    draft["blocks"] = new_blocks

    # draft["html"] 는 검증에 더 이상 쓰이지 않지만(prepublish_gate 사실성 검사 등이
    # 참고 텍스트로 읽을 수 있어) 말미 <p>/<h> 를 같은 개수만큼 best-effort 로 제거해
    # 실제 발행 blocks 와 크게 어긋나지 않도록만 정리 — 실패해도 수정 자체는 유효.
    html = draft.get("html", "")
    if html:
        elems = list(re.finditer(r'<p[^>]*>.*?</p>|<h[1-6][^>]*>.*?</h[1-6]>', html, re.DOTALL | re.IGNORECASE))
        new_html = html
        for match in list(reversed(elems))[:removed_n]:
            new_html = new_html[:match.start()] + new_html[match.end():]
        draft["html"] = new_html

    _log.info(f"[draft_fixer] 분량 상한 초과 수정: blocks {before}문장 → {total}문장 (상한 {max_sents}, 블록 {removed_n}개 제거)")
    return True


def _fix_excessive_empty_p(draft: dict) -> bool:
    """② 연속 빈 p / 연속 br 압축 — full_html + blocks 양쪽 정리.

    검출: _layer3_verify_draft "② full_html 3+ 연속 빈 p 검출" / "② full_html 3+ 연속 br 검출"
    수정: law_enforcer._compress_excessive_whitespace 재사용.
    """
    try:
        from JARVIS02_WRITER.law_enforcer import _compress_excessive_whitespace
    except ImportError as e:
        _log.warning(f"[draft_fixer] _compress_excessive_whitespace import 오류: {e}")
        return False

    changed = False

    # ① full_html 직접 압축
    full_html = draft.get("full_html") or ""
    if full_html:
        new_html, cnt = _compress_excessive_whitespace(full_html)
        if cnt:
            draft["full_html"] = new_html
            changed = True

    # ② blocks 내부 각 html/text 블록도 압축 (재검증 시 full_html 재조립 전 선제 정리)
    blocks = draft.get("blocks") or []
    new_blocks = list(blocks)
    for idx, (btype, bdata) in enumerate(blocks):
        if btype == "spacer" or not isinstance(bdata, str):
            continue
        new_bdata, cnt = _compress_excessive_whitespace(bdata)
        if cnt:
            new_blocks[idx] = (btype, new_bdata)
            changed = True
    if changed:
        draft["blocks"] = new_blocks

    return changed


def _fix_image_count_underflow(draft: dict, platform: str) -> bool:
    """제8조 위반 — 썸네일 제외 이미지 최소 5장(5+α) 미달 시 AI 사진 추가.

    ★ 사용자 박제 2026-06-01 → 2026-07-05 정정 8→5: 5장은 디폴트가 아닌 절대 최솟값.
    blocks에서 content 이미지(heading_ 제외)를 세어 MIN_IMAGES 미달이면
    render_from_spec으로 AI 인포그래픽을 생성하여 text 블록 사이에 삽입.
    """
    from pathlib import Path as _P
    from JARVIS02_WRITER.length_manager import MIN_IMAGES as _MIN

    blocks = draft.get("blocks") or []
    if not blocks:
        return False

    def _is_content_img(btype, bdata) -> bool:
        if btype != "image":
            return False
        fname = str(bdata)
        return not any(k in fname for k in ("heading_", "section_title", "thumbnail_", "economic_h2_"))

    current_count = sum(1 for btype, bdata in blocks if _is_content_img(btype, bdata))
    if current_count >= _MIN:
        return False

    needed = _MIN - current_count
    _log.info(f"[draft_fixer] 이미지 {current_count}/{_MIN}장 — {needed}장 추가")

    keyword = draft.get("theme") or draft.get("keyword") or "경제"
    sector = draft.get("sector") or ""
    _plat_dirs = {
        "naver":   _P(__file__).parent.parent / "JARVIS06_IMAGE" / "output" / "images" / "economic_naver",
        "tistory": _P(__file__).parent.parent / "JARVIS06_IMAGE" / "output" / "images" / "economic_tistory",
    }
    img_dir = _plat_dirs.get(platform, _plat_dirs["tistory"])
    img_dir.mkdir(parents=True, exist_ok=True)

    added = 0
    # text 블록 사이 빈 자리를 찾아 이미지 삽입 (마지막 text 제외)
    new_blocks = list(blocks)
    for i in range(len(new_blocks) - 1, 0, -1):
        if added >= needed:
            break
        btype, bdata = new_blocks[i]
        if btype != "text":
            continue
        prev_type = new_blocks[i - 1][0]
        if prev_type == "image":
            continue  # 이미 이미지 있음
        try:
            from JARVIS06_IMAGE.image_spec import generate_image_spec as _gi, render_from_spec as _ri
            import hashlib as _hsh
            _h = _hsh.md5(f"{bdata[:60]}|{keyword}|{i}".encode()).hexdigest()[:8]
            _dest = img_dir / f"fix_img_{i:04d}_{_h}.png"
            spec = _gi(section_text=str(bdata)[:400], keyword=keyword, sector=sector)
            rendered = _ri(spec, _dest)
            if rendered and rendered.exists():
                new_blocks.insert(i, ("image", str(rendered)))
                added += 1
        except Exception as e:
            _log.warning(f"[draft_fixer] 이미지 추가 실패 idx={i}: {e}")

    if added:
        draft["blocks"] = new_blocks
        _log.info(f"[draft_fixer] 이미지 {added}장 추가 완료 (총 {current_count + added}장)")
        return True
    return False


# ── 이슈 문자열 → 수정 함수 라우터 ─────────────────────────

def _route_fix(issue_str: str, draft: dict, platform: str) -> bool:
    """issue_str 키워드 분류 → 적절한 패치 함수 호출."""
    s = issue_str.lower()

    if "빈 헤더" in issue_str or "empty header" in s:
        return _fix_empty_headers(draft)

    if "이미지" in issue_str and ("연속" in issue_str or "consecutive" in s):
        return _fix_consecutive_images(draft, platform)

    # ★ ERRORS [145] — "② full_html 3+ 연속 빈 p 검출" / "연속 br 검출" 수정 분기 추가
    if ("연속 빈 p" in issue_str or "연속 br" in issue_str
            or "consecutive empty p" in s or "consecutive br" in s):
        return _fix_excessive_empty_p(draft)

    # ★ Layer 6 — 분량 상한 초과 (LLM이 5회 재시도해도 수렴 안 하는 패턴 해결)
    if "분량 상한 초과" in issue_str or "sentence overflow" in s:
        return _fix_sentence_overflow(draft, issue_str)

    if any(k in issue_str for k in ("제3조", "제4조", "제9조", "헌법 위반", "enforce")):
        return _fix_law_violations(draft, platform)

    # ★ 사용자 박제 2026-06-01 → 2026-07-05 정정: 이미지 최소 5장 미달 (제8조 5+α)
    if "이미지 최소 미달" in issue_str or "image underflow" in s or "이미지 부족" in issue_str:
        return _fix_image_count_underflow(draft, platform)

    # 수정 불가 — 재생성 필요
    return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  GUARDIAN 기록 — 즉시수정 이력 + 자가 학습
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _record_to_guardian(issue_str: str, platform: str, action_name: str) -> None:
    """수정 성공 → report_manual_fix + record_pattern_hit 2단 박제."""

    # ① 수정 이력 박제 (오류 관리 탭 + ERRORS.md 연동)
    try:
        from JARVIS07_GUARDIAN.error_collector import report_manual_fix
        report_manual_fix(
            source=f"harness/{action_name}",
            fixed_file="JARVIS02_WRITER/draft_fixer.py",
            description=(
                f"[Layer3 즉시수정] [{platform}] {issue_str[:120]}\n"
                f"하네스 검증 실패 항목을 발행 전 inline 패치 완료."
            ),
            error_type="DraftQualityViolation",
            severity="low",
            actor="harness_auto_fix",
        )
    except Exception as e:
        _log.warning(f"[draft_fixer] report_manual_fix 실패 (무시): {e}")

    # ② learned_patterns 자가 학습 등록 — 같은 fingerprint 재발 시 LLM 0 호출
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import record_pattern_hit
        _err_rec = {
            "error_type": "DraftQualityViolation",
            "module": f"harness.{action_name}.{platform}",
            "message": issue_str,
            "source": f"harness/{action_name}",
        }
        record_pattern_hit(
            _err_rec,
            fixer_name=f"harness_inline_{platform}",   # 필수 — 노이즈 게이트 통과
            fixed_file="JARVIS02_WRITER/draft_fixer.py",
            source="harness_auto_fix",
        )
    except Exception as e:
        _log.warning(f"[draft_fixer] record_pattern_hit 실패 (무시): {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  공개 API — verify_all 에서 호출
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fix_and_learn(
    state: dict,
    draft_key: str,     # "wp_draft" / "ts_draft" / "nv_draft"
    platform: str,      # "tistory" / "naver"
    raw_issues: list[str],
    action_name: str = "harness",
) -> tuple[list[str], list[str]]:
    """Layer 3 검증 실패 이슈 리스트를 즉시 수정 시도.

    Args:
        state:       하네스 state dict (draft in-place 수정)
        draft_key:   state 에서 꺼낼 draft 키
        platform:    "tistory" / "naver"
        raw_issues:  _layer3_verify_draft 반환 메시지 리스트
        action_name: 로깅·기록용 식별자 ("economic" / "theme")

    Returns:
        (fixed_issues, unfixed_issues)
        - fixed:   inline 패치 성공 → Issue 미생성 → Layer 4 직진
        - unfixed: 수정 불가 → Issue 생성 → harness retry
    """
    draft = state.get(draft_key) or {}
    if not draft.get("success"):
        # 생성 자체 실패 — inline 수정 불가
        return [], raw_issues

    fixed, unfixed = [], []

    for iss in raw_issues:
        ok = _route_fix(iss, draft, platform)
        if ok:
            # state 갱신 (draft 객체 참조이므로 dict 재할당 보장)
            state[draft_key] = draft
            _record_to_guardian(iss, platform, action_name)
            fixed.append(iss)
            print(f"  🔧 [Layer3 즉시수정] [{platform}] {iss[:80]}")
        else:
            unfixed.append(iss)
            print(f"  ⚠️ [Layer3 수정불가] [{platform}] {iss[:80]} → 재생성 필요")

    if fixed:
        print(
            f"  ✅ [draft_fixer] [{platform}] {len(fixed)}건 수정 완료 "
            f"/ {len(unfixed)}건 재생성 필요 → GUARDIAN 학습 등록"
        )

    return fixed, unfixed


__all__ = ["fix_and_learn"]