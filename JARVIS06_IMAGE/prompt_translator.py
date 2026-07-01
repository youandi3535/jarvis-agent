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
        # ★ 사용자 박제 2026-06-07 — 단순 번역 → 이미지 생성 특화 프롬프트 변환.
        # Pollinations.ai (SDXL/Flux 계열) 가 잘 받아들이는 스타일 키워드 자동 첨가.
        system_msg = (
            "You are an image prompt engineer for Stable Diffusion / Flux models "
            "(Pollinations.ai backend). Convert Korean descriptions into rich, "
            "image-generation-optimized English prompts.\n\n"
            "Strict rules:\n"
            "- Output ONLY the English prompt — no preface, no markdown, no labels, no quotes.\n"
            "- 50-100 words, comma-separated descriptors.\n"
            "- Always include: subject, scene, lighting (e.g. 'cinematic lighting', "
            "'golden hour', 'soft natural light'), camera (e.g. 'f/1.8', 'shallow depth of field', "
            "'wide angle'), style (e.g. 'photorealistic', 'editorial photography', 'modern minimalist').\n"
            "- End with quality boosters: 'photorealistic, ultra detailed, 8k, sharp focus, "
            "professional photography, real documentary photograph'.\n"
            "- ★ Keep the SUBJECT literal and concrete — depict the actual real-world thing/place/"
            "person, NEVER a metaphor or symbol for it.\n"
            "- Append negative cues: 'no text, no letters, no watermark, no logo, "
            "no distorted faces, no deformed animals, no weird objects, realistic anatomy, "
            "not abstract, not surreal, not conceptual art, not a metaphor, "
            "not cartoon, not anime, not illustration'.\n"
            "- Aesthetic: premium Korean business/finance editorial."
        )
        result = invoke_text(
            "writer_fast",
            f"Korean description: {text_ko}\n\n"
            "Output the optimized English image prompt now.",
            system=system_msg,
            max_tokens=400,
            temperature=0.6,
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
