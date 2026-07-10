"""JARVIS06_IMAGE/injectors/image_injectors.py — 이미지 블록 삽입·재정렬.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — law_enforcer.py 의 이미지 함수 이관.

이관 함수 (4개 — `_is_h2_header` 헬퍼 + 3개 공개):
  - enforce_paragraph_pair_image       (문단+문단+이미지 → 문단+이미지+문단 재정렬)
  - enforce_image_between_paragraphs   (글 N+ 연속 + 이미지 부재 시 자동 삽입)
  - compute_unused_image_pool          (이미 사용된 path 제외)
  - _is_h2_header                      (섹션 경계 헤더 판별 — 위 두 함수 의존)

원래 위치: JARVIS02_WRITER/law_enforcer.py
이관 일자: 2026-05-17
"""
from __future__ import annotations

import logging
import re

from JARVIS06_IMAGE.validators.image_validators import _is_heading_img_path

# ── length_manager 단일 진입점 — 임계값 상수 ──────────────────────
try:
    from JARVIS02_WRITER.length_manager import (
        MAX_CONSECUTIVE_PARAGRAPHS_WITHOUT_IMAGE as _L_MAX_PARA_NO_IMG,
    )
except ImportError:
    # 동일 폴더 실행 시 대비 — 임계값 기본 2
    _L_MAX_PARA_NO_IMG = 2

log = logging.getLogger(__name__)


# ── 소제목·헤더 식별 헬퍼 ─────────────────────────────────────
_SECTION_IMG_PAT = re.compile(r'heading_|section_|economic_h2_', re.IGNORECASE)
_HEADING_TAG_PAT = re.compile(r'^\s*<h[1-6][\s>]', re.IGNORECASE)


def _is_section_header(btype: str, bdata: str) -> bool:
    """소제목 블록 여부.

    해당하는 경우:
    - image 블록 + 파일명에 heading_/section_/economic_h2_ 포함 (소제목 이미지)
    - html/text 블록 + <h1>~<h6> 태그로 시작 (HTML 헤더)

    NOTE: `enforce_spacing` 도 사용하는 헬퍼이지만 enforce_paragraph_pair_image 가
    바로 의존하므로 image_injectors 안에 둠. law_enforcer.enforce_spacing 은
    별도 정의를 사용 (도메인 분리: 여백은 헌법 도메인, 헤더 판별은 이미지 도메인).
    """
    if btype == 'image':
        return bool(_SECTION_IMG_PAT.search(str(bdata)))
    if btype in ('html', 'text'):
        return bool(_HEADING_TAG_PAT.match(bdata))
    return False


def _is_h2_header(btype: str, bdata: str) -> bool:
    """섹션 경계 헤더 (h2/h3) 여부 — 블록 타입 또는 HTML 안 h2/h3 태그."""
    if btype in ('heading_h2', 'heading_h3', 'heading'):
        return True
    if btype == 'image' and _is_heading_img_path(bdata):
        return True
    if btype == 'html' and isinstance(bdata, str) and re.search(r'<h[23]\b', bdata, re.IGNORECASE):
        return True
    return False


def enforce_paragraph_pair_image(
    blocks: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """★ 2026-05-19 비활성 — 제4조 개정으로 패턴3·4(문단+문단+이미지, 이미지+문단+문단)가 허용됨.

    하위 호환 시그니처 유지 (law_enforcer import 참조). 블록 변경 없이 0 반환.
    """
    return blocks, 0  # no-op: 제4조 2026-05-19 개정으로 문단+문단+이미지 패턴 허용


def compute_unused_image_pool(
    blocks: list[tuple[str, str]],
    all_paths: list[str] | None,
) -> list[str]:
    """blocks 에서 *이미 사용된 이미지 path* 를 제외하고 *남은 path* 반환.

    ★ 사용자 박제 2026-05-17 — 호출자가 enforce_supreme_law(image_pool=...) 로 *진짜 외부 풀* 만
    전달하기 위한 helper.

    작성 단계 (assemble_blocks) 가 *모든 visual_paths 를 blocks 에 매핑* 한다면 남은 풀은 0.
    *HTML SVG 위치 < visual_paths 길이* 인 경우만 남는 풀 존재 → 이 경우 enforce_image_between_paragraphs
    가 *Tier 1 외부 풀* 사용. 없으면 자연스럽게 Tier 2 (블록 내 재사용) 으로 fallback.

    Args:
        blocks: 발행 직전 블록 리스트
        all_paths: 작성 단계에서 만들어진 *전체* 이미지 경로 리스트 (visual_paths / jpg_paths)

    Returns:
        blocks 에 *아직 사용 안 된* path 리스트 (외부 풀). 비어있을 수 있음.
    """
    if not all_paths:
        return []
    used = {str(b[1]) for b in blocks if b[0] == 'image'}
    return [p for p in all_paths if str(p) not in used]


def enforce_image_between_paragraphs(
    blocks: list[tuple[str, str]],
    image_pool: list[str] | None = None,
    source: str = "",
) -> tuple[list[tuple[str, str]], int, int]:
    """제4조 금지 패턴 3 — 글 N+ 연속 + 이미지 부재 검출.

    *같은 섹션 (h2/h3 ~ 다음 h2)* 안에서 text/html 단락이 *임계값 초과*
    연속이고 그 사이에 image (소제목 배너 제외) 가 0개면 위반.

    임계값: length_manager.MAX_CONSECUTIVE_PARAGRAPHS_WITHOUT_IMAGE 단일 진입점.
    (현재 값 2 — 즉 3 이상 연속이면 위반. 정책 변경 시 length_manager 1곳만 수정.)

    동작 2-tier:
      Tier 1 — image_pool 제공 시: 풀에서 순서대로 가져와 *글 사이마다* 삽입
        (글 N개 연속 → 이미지 N-1개 삽입). 풀이 모자라면 가능한 만큼.
      Tier 2 — image_pool 미제공 or 소진: 검출 + 텔레그램 경고, 블록 변경 없음.
        (★ 사용자 박제 2026-05-18 — 기존 이미지 재사용·중복 삽입 절대 금지)

    Returns:
        (new_blocks, violations_detected, images_inserted)

    ★ 사용자 박제 2026-05-16·17 — 결론·요약·투자자 관점 섹션이 글만 4문단 연속되던 사고.
    ★ 사용자 박제 2026-05-17 — 임계값 하드코딩 → length_manager 단일 진입점화.
    ★ 사용자 박제 2026-05-18 — 중복 삽입 절대 금지 (구 Tier 2 재사용 제거).
    """
    if not blocks or len(blocks) < 3:
        return blocks, 0, 0

    # 임계값 — *이미지 없이 글 연속 허용 최대 단락 수*
    threshold = _L_MAX_PARA_NO_IMG + 1  # 변수 N → N+1 단락 이상 연속이면 위반

    # 1단계: 섹션 경계로 블록을 그룹핑 (h2/h3 헤더 기준)
    sections: list[list[int]] = [[]]  # 각 섹션의 블록 인덱스 리스트
    for i, (btype, bdata) in enumerate(blocks):
        if _is_h2_header(btype, bdata):
            sections.append([i])  # 헤더가 새 섹션 시작
        else:
            sections[-1].append(i)

    # 2단계: 각 섹션에서 *content 블록* (spacer/divider/heading 제외) 만 봄
    # → text/html 연속 + 이미지 부재 검출
    violations = 0
    insertions: list[tuple[int, str]] = []  # (insert_after_index, image_path)
    pool_idx = 0
    pool = list(image_pool) if image_pool else []

    for sect in sections:
        # 섹션 내 *본문* 블록만 추출 (헤더·spacer·divider 제외)
        body_indices = []
        for i in sect:
            btype, bdata = blocks[i]
            if btype in ('spacer', 'divider'):
                continue
            if _is_h2_header(btype, bdata):
                continue
            body_indices.append(i)

        # 섹션 내 text/html 단락 연속 카운트 + 사이 이미지 카운트
        text_run: list[int] = []  # 현재 연속 중인 text/html 블록 인덱스
        text_runs: list[list[int]] = []  # 검출된 연속 그룹

        for i in body_indices:
            btype, bdata = blocks[i]
            if btype in ('text', 'html'):
                text_run.append(i)
            elif btype == 'image':
                # 소제목 배너 외 본문 이미지 → 연속 차단
                if not _is_heading_img_path(bdata):
                    if len(text_run) >= threshold:
                        text_runs.append(text_run)
                    text_run = []
                # 헤더 배너는 카운트 무시 (연속 유지)
            else:
                if len(text_run) >= threshold:
                    text_runs.append(text_run)
                text_run = []
        if len(text_run) >= threshold:
            text_runs.append(text_run)

        # 검출된 연속 그룹마다 처리
        # Tier 1: image_pool 제공 시 삽입 → Tier 2: 풀 없거나 소진 → 경고만 (중복 삽입 금지)
        for run in text_runs:
            violations += 1
            for idx, blk_i in enumerate(run[:-1]):
                # Tier 1 — 외부 풀에서 미사용 이미지 삽입
                if pool_idx < len(pool):
                    insertions.append((blk_i, pool[pool_idx]))
                    pool_idx += 1
                    continue
                # Tier 2 — 풀 소진 or 미제공 → 삽입 중단 (중복 삽입 절대 금지)

    if violations == 0:
        return blocks, 0, 0

    # 3단계: 삽입 적용 (뒤에서 앞으로 — 인덱스 안정성)
    inserted = 0
    if insertions:
        # 뒤에서부터 정렬
        insertions.sort(key=lambda x: -x[0])
        new_blocks = list(blocks)
        for after_idx, img_path in insertions:
            new_blocks.insert(after_idx + 1, ('image', img_path))
            inserted += 1
        result_blocks = new_blocks
    else:
        result_blocks = blocks

    # 4단계: 텔레그램 경고 — *위반 검출* 알림 (자동 삽입 여부와 무관)
    if violations:
        if inserted:
            insert_note = f"자동 삽입 {inserted}개 (외부 풀 {len(pool)}개 중)"
        else:
            insert_note = f"삽입 불가 — image_pool 미제공 또는 소진 (위반 {violations}개 섹션)"
        msg_parts = [
            f"⚠️ *제4조 금지 패턴 3* — 글 연속 + 이미지 부재",
            f"위치: {source or 'unknown'}",
            f"검출: {violations}개 섹션 (임계값 {threshold}단락 이상)",
            insert_note,
        ]
        msg = "\n".join(msg_parts)
        log.warning(f"[image-injector/제4조-3] {msg}")
        try:
            from shared.notify import send_tg
            send_tg(msg)
        except Exception:
            pass

    return result_blocks, violations, inserted
