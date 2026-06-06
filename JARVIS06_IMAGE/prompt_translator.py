"""JARVIS06_IMAGE/prompt_translator.py — 한국어 이미지 프롬프트 → 영어 변환."""
from __future__ import annotations
import logging

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")


def translate(text_ko: str) -> str:
    """한국어 프롬프트를 이미지 생성용 영어로 변환 (캐시 없음, 매번 신선)."""
    if not text_ko or not text_ko.strip():
        return ""
    # 이미 영어면 그대로
    if all(ord(c) < 128 or c in " ,.-" for c in text_ko):
        return text_ko
    try:
        from shared.llm import invoke_text
        result = invoke_text(
            "writer_fast",
            f"Translate the following Korean image prompt to concise English for image generation.\n"
            f"Korean: {text_ko}\n"
            "Rules: Output English only, no explanation, max 100 words, vivid and descriptive.",
            max_tokens=200,
            temperature=0.3,
        )
        en = (result or "").strip()
        # 마크다운 헤더 제거 (# Image Prompt, **English:**, 등)
        import re as _re_t
        en = _re_t.sub(r'^#+\s*[^\n]*\n+', '', en, flags=_re_t.MULTILINE)
        en = _re_t.sub(r'^\*{1,2}[A-Za-z ]+\*{1,2}:?\s*', '', en).strip().strip('"').strip("'").strip()
        if en:
            log.debug(f"[Translator] '{text_ko[:30]}' → '{en[:60]}'")
            return en
    except Exception as e:
        log.warning(f"[Translator] 번역 실패: {e} — 원문 사용")
        _g_report("image", e, module=__name__)
    return text_ko


__all__ = ["translate"]
