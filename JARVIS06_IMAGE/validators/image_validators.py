"""JARVIS06_IMAGE/validators/image_validators.py — 이미지 블록 검증·dedupe.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — law_enforcer.py 의 이미지 함수 이관.

이관 함수 (4개):
  - _is_heading_img_path  (소제목 배너 path 판별)
  - _validate_image_files (이미지 파일 존재·크기 검증)
  - _dedupe_all_images    (이미지 전역 dedupe — 비연속 중복 제거)
  - _dedupe_consecutive_images (연속 동일 이미지 1개 제거)

원래 위치: JARVIS02_WRITER/law_enforcer.py
이관 일자: 2026-05-17
이관 사유: 도메인 단일 진입점 — 이미지 사고 1건 → 점검 1곳 (ADR 008)
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# ★ ERRORS [170] 2026-05-26: 다중 deferred 이미지 EOF 플러시 시 연속 배치 방지
# 크로스 모듈 import 없이 로컬 정의 (law_enforcer._SPACER_1 과 동일 내용)
_IMG_SEP_SPACER = '<p style="margin:0 0 1em 0;line-height:1.8;">&nbsp;</p>'


def _is_heading_img_path(bdata: str) -> bool:
    """소제목 배너·썸네일 여부 — 본문 이미지 교차 삽입 대상에서 제외할 경로."""
    s = str(bdata)
    return (
        ('heading_' in s)
        or ('economic_h2_' in s)
        or ('section_title' in s)
        or ('thumbnail_' in s)   # 썸네일 — Tier 2 fallback 재삽입 금지
    )


def _validate_image_files(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """이미지 블록의 파일 경로 *실제 존재* 검증.

    ★ 사용자 박제 2026-05-17 — *이미지 깨짐* 사고:
       발행 시점에 이미지 파일이 *없거나* 0 bytes 면 네이버·티스토리 업로드 실패
       → 깨진 이미지 마크 표시. 발행 전 검증 + 누락 블록 제거 + 경고.

    검증 항목:
      - 파일 경로가 실제로 존재 (Path.exists())
      - 파일 크기 > 0 (빈 파일 아님)
      - 외부 URL (http://·https://) 은 검증 생략 (네트워크 비용)
    """
    if not blocks:
        return blocks, 0
    from pathlib import Path as _P
    out: list[tuple[str, str]] = []
    removed = 0
    for btype, bdata in blocks:
        if btype != "image":
            out.append((btype, bdata))
            continue
        path = str(bdata)
        if path.startswith(("http://", "https://", "data:")):
            out.append((btype, bdata))
            continue
        try:
            p = _P(path)
            if not p.exists():
                log.warning(f"[image-validate] 누락 — {path}")
                removed += 1
                continue
            if p.stat().st_size <= 0:
                log.warning(f"[image-validate] 0 bytes — {path}")
                removed += 1
                continue
        except Exception as e:
            log.warning(f"[image-validate] 경로 검증 예외: {path} ({e})")
            removed += 1
            continue
        out.append((btype, bdata))
    # ★ 다건 누락 = 파이프라인 파괴 시그널 (ERRORS [291] — 2026-07-03): 렌더 성공 로그 후
    #   파일 부재(발행 도중 삭제 등)는 조용한 블록 드롭으로 끝내면 GUARDIAN 학습 루프에
    #   안 잡힘. 2건 이상 누락 시 보고 (쿨다운·dedup 은 error_collector 내장).
    if removed >= 2:
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _g_rep
            _g_rep("image",
                   RuntimeError(f"이미지 파일 누락·빈 파일 {removed}건 — 렌더 후 파일 소실 의심"),
                   module=__name__, func_name="_validate_image_files")
        except Exception:
            pass
    return out, removed


def _dedupe_all_images(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """이미지 *전역 dedupe* — 같은 경로가 *어디서든 두 번 이상* 등장 시 후속 제거.

    ★ 사용자 박제 2026-05-17 — 사용자 보고 사고:
       네이버 경제 브리핑 글에 *같은 이미지가 비연속으로 여러 번* 나타나는 사고.
       기존 `_dedupe_consecutive_images()` 는 *연속만* 잡고 *비연속 중복* 미검출.

    소제목 배너 (heading_* / economic_h2_ / section_title) 는 예외 — 각 섹션마다
    *서로 다른* 헤더 이미지여야 정상. 일반 본문 이미지(차트·사진) 만 dedupe.
    """
    if not blocks:
        return blocks, 0
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    removed = 0
    for btype, bdata in blocks:
        if btype == "image":
            path = str(bdata)
            if _is_heading_img_path(path):
                # 헤더 배너는 dedupe 제외 (섹션별 다른 배너 정상)
                out.append((btype, bdata))
                continue
            if path in seen:
                removed += 1
                continue  # 비연속 중복 제거
            seen.add(path)
        out.append((btype, bdata))
    return out, removed


def _dedupe_by_content_hash(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """이미지 *내용 해시* dedupe — 다른 경로·같은 내용 차단.

    ★ ERRORS [136] 사용자 박제 2026-05-17 — 경로 기반 dedupe 만으로는 부족:
    AI 사진 생성기 캐시 / fallback 그라디언트 반복 / 같은 파일 다른 이름 저장 등
    *경로는 달라도 파일 내용 동일* 인 사고가 잔존. MD5 해시 기반으로 차단.

    헤더 배너 (heading_*/economic_h2_/section_title) 는 예외 (섹션별 다른 정상).
    """
    if not blocks:
        return blocks, 0
    import hashlib
    seen_hashes: set[str] = set()
    out: list[tuple[str, str]] = []
    removed = 0
    for btype, bdata in blocks:
        if btype == "image":
            path = str(bdata)
            if _is_heading_img_path(path):
                out.append((btype, bdata))
                continue
            try:
                with open(path, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()
            except Exception:
                # 파일 못 읽으면 경로 기반 fallback
                h = path
            if h in seen_hashes:
                removed += 1
                log.info(f"[image-validate] 내용 해시 중복 제거: {path}")
                continue
            seen_hashes.add(h)
        out.append((btype, bdata))
    return out, removed


def _dedupe_consecutive_images(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """같은 이미지 경로가 연속으로 등장하면 후속 1개 제거 (사용자 박제 2026-05-14).

    Pass-2 SVG 생성 실패 → 동일 SVG 가 2번 들어가는 사고 + assemble_blocks 의
    인덱스 중복 사고 모두 차단. 제4조(이미지 연속 금지) 의 *strict 버전*.
    """
    if not blocks:
        return blocks, 0
    out: list[tuple[str, str]] = []
    removed = 0
    last_img: str | None = None
    for btype, bdata in blocks:
        if btype == "image":
            cur = str(bdata)
            if cur == last_img:
                removed += 1
                continue  # 후속 동일 이미지 1개 제거
            last_img = cur
        else:
            # spacer 도 이미지 비교 흐름 유지 (spacer 1개 정도는 같은 이미지 사이에 끼어들어도 중복으로 봄)
            if btype != "spacer":
                last_img = None
        out.append((btype, bdata))
    return out, removed


def _fix_any_consecutive_images(
    blocks: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], int]:
    """서로 다른 이미지라도 연속으로 나오는 경우를 수정한다 (제4조 이미지 연속 금지).

    처리 전략: 연속 이미지 중 두 번째 이미지를 다음 텍스트/heading 블록 바로 뒤로 이동.
    다음 텍스트 블록이 없으면 마지막에 붙임 (단, spacer 1개 삽입 후).

    ★ ERRORS [172] 2026-05-28 — 두 가지 구조 버그 수정:
    1. heading 이미지(소제목 배너)를 섹션 구분자로 인식 — heading 뒤 이미지를 연속으로 판정하지 않음.
       (기존: heading 도 image 타입이라 last_real="image" → 그 뒤 이미지가 무조건 deferred)
    2. deferred 다수를 한 content 블록에서 한꺼번에 방출 → [content, img2, spacer, img3] 연속.
       (수정: content 만날 때 1개씩만 방출 → 남은 deferred 는 다음 content 블록에서 처리)
    """
    if not blocks:
        return blocks, 0

    fixed = 0
    result: list[tuple[str, str]] = []
    deferred: list[tuple[str, str]] = []  # 위치 이동 대기 이미지

    def _is_content(btype: str, bdata=None) -> bool:
        if btype == "heading":
            return True
        if btype in ("text", "html"):
            # &nbsp; 전용 spacer는 실질 콘텐츠로 취급 안 함
            plain = re.sub(r'<[^>]+>', '', str(bdata or '')).replace('\xa0', '').replace('&nbsp;', '').strip()
            return bool(plain)
        return False

    def _last_is_content_image() -> bool:
        """result 에서 마지막 의미있는 블록이 content 이미지인지 확인.

        spacer/divider 건너뜀. heading 이미지 = 섹션 구분자 → False 반환 (연속 아님).
        """
        for t, d in reversed(result):
            if _is_content(t, d):
                return False  # 마지막 실질 = text → 연속 아님
            if t == "image":
                if _is_heading_img_path(str(d)):
                    return False  # heading 이미지 = 섹션 구분자 → 연속 아님
                return True  # content 이미지 → 연속!
        return False

    for btype, bdata in blocks:
        # content 블록 만나면 deferred 에서 1개만 방출 (★ 다중 동시 방출 버그 수정)
        if deferred and _is_content(btype, bdata):
            result.append((btype, bdata))
            result.append(deferred.pop(0))
            continue

        if btype == "image":
            if _is_heading_img_path(str(bdata)):
                # heading 이미지 — 섹션 구분자, 연속 판정 제외, 그대로 삽입
                result.append((btype, bdata))
                continue
            if _last_is_content_image():
                # content 이미지 연속 — deferred 큐로 이동
                deferred.append((btype, bdata))
                fixed += 1
                log.warning(
                    "[image_validators] 제4조 — 다른 이미지 연속 감지: 다음 텍스트 이후로 이동"
                )
                continue

        result.append((btype, bdata))

    # 남은 deferred: 문서 끝에 spacer 삽입 후 순서대로 붙임
    if deferred:
        result.append(("spacer", _IMG_SEP_SPACER))
        for i, item in enumerate(deferred):
            if i > 0:
                result.append(("spacer", _IMG_SEP_SPACER))
            result.append(item)

    return result, fixed
