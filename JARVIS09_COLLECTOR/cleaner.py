"""JARVIS09_COLLECTOR/cleaner.py — 원본 HTML → 잡음 제거 텍스트 정제 (요약 아님)."""
from __future__ import annotations
import re
from .models import RawDocument, CollectionResult

_PII_PATTERNS = [
    (re.compile(r"\b\d{2,3}-\d{3,4}-\d{4}\b"), "[전화번호]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[이메일]"),
    (re.compile(r"\b\d{6}-\d{7}\b"), "[주민번호]"),
]

_NOISE_PATTERNS = [
    re.compile(r"<!--[\s\S]*?-->"),          # HTML 주석
    re.compile(r"<script[\s\S]*?</script>", re.I),
    re.compile(r"<style[\s\S]*?</style>", re.I),
    re.compile(r"<[^>]+>"),                  # 나머지 태그
    re.compile(r"\s{3,}", re.M),             # 과도한 공백
]


def _strip_html(html: str) -> str:
    """readability 우선, 폴백 시 regex 태그 제거."""
    if not html:
        return ""
    try:
        from readability import Document
        doc = Document(html)
        text = doc.summary()
        # readability 결과에서도 태그 제거
        for pat in _NOISE_PATTERNS[1:]:
            text = pat.sub(" ", text)
        return text.strip()
    except Exception:
        text = html
        for pat in _NOISE_PATTERNS:
            text = pat.sub(" ", text)
        return text.strip()


def mask_pii(text: str) -> str:
    """개인정보(전화·이메일·주민번호) 마스킹."""
    for pat, repl in _PII_PATTERNS:
        text = pat.sub(repl, text)
    return text


def clean_document(raw: RawDocument) -> CollectionResult:
    """RawDocument → 잡음 제거 원본 텍스트 CollectionResult (요약 아님)."""
    text = _strip_html(raw.raw_html) if raw.raw_html else raw.raw_text
    text = mask_pii(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return CollectionResult(
        theme=raw.extra.get("theme", ""),
        source_type=raw.source_type,
        url=raw.url,
        title=raw.title,
        cleaned_text=text,
        word_count=len(text.split()),
        collected_at=raw.collected_at,
        meta=raw.extra,
    )
