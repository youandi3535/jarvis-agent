"""JARVIS06_IMAGE/injectors — 이미지 블록 삽입·재정렬·조립 단일 진입점.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — law_enforcer 이미지 함수 +
tistory_html_writer.assemble_blocks 이관.
이 폴더 외 위치에 이미지 삽입 함수 정의 금지.
"""
from JARVIS06_IMAGE.injectors.image_injectors import (  # noqa: F401
    enforce_paragraph_pair_image,
    enforce_image_between_paragraphs,
    compute_unused_image_pool,
    _is_h2_header,
)
from JARVIS06_IMAGE.injectors.block_assembler import assemble_blocks  # noqa: F401

__all__ = [
    "enforce_paragraph_pair_image",
    "enforce_image_between_paragraphs",
    "compute_unused_image_pool",
    "_is_h2_header",
    "assemble_blocks",
]
