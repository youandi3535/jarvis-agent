"""JARVIS06_IMAGE/cleaners/economic_image_cleaner.py — 경제 브리핑 이미지 디렉터리 정리.

ADR 008 Phase 1 (사용자 박제 2026-05-17) — economic_poster._cleanup_economic_images 이관.

원래 위치: JARVIS02_WRITER/economic_poster.py:81
이관 일자: 2026-05-17
이관 사유: 이미지 도메인 단일 진입점 (ADR 008) — 이미지 정리는 JARVIS06 책임.

호출자 위임:
  before — `from JARVIS02_WRITER.economic_poster import _cleanup_economic_images`
  after  — `from JARVIS06_IMAGE.cleaners import cleanup_economic_images`
  공개 이름은 `cleanup_economic_images` (밑줄 제거 — 다른 패키지 공개 API).
"""
from __future__ import annotations

from pathlib import Path

# JARVIS06_IMAGE 의 표준 출력 디렉터리 구조 (CLAUDE.md 규정 단일 진입점)
_JARVIS06_BASE = Path(__file__).resolve().parent.parent  # JARVIS06_IMAGE/

ECONOMIC_IMG_DIR_NAVER   = _JARVIS06_BASE / 'output' / 'images' / 'economic_naver'
ECONOMIC_IMG_DIR_TISTORY = _JARVIS06_BASE / 'output' / 'images' / 'economic_tistory'

# 디렉터리 자동 생성 (이관 전 economic_poster 모듈 로딩 시 보장하던 동작 유지)
for _d in (ECONOMIC_IMG_DIR_NAVER, ECONOMIC_IMG_DIR_TISTORY):
    _d.mkdir(parents=True, exist_ok=True)


def cleanup_economic_images(
    post_naver: bool = True,
    post_tistory: bool = True,
) -> int:
    """선택한 플랫폼의 이미지 폴더를 완전 초기화 (하위폴더 포함).

    shutil.rmtree() + 재생성으로 이전 회차 잔여물 100% 제거.

    Args:
        post_naver / post_tistory: 해당 플랫폼 폴더 정리 여부.

    Returns:
        삭제된 파일 총 개수.
    """
    import shutil

    dirs_to_clean = []
    if post_naver:
        dirs_to_clean.append((ECONOMIC_IMG_DIR_NAVER, "Naver"))
    if post_tistory:
        dirs_to_clean.append((ECONOMIC_IMG_DIR_TISTORY, "Tistory"))

    total_removed = 0

    for img_dir, platform in dirs_to_clean:
        if not img_dir.exists():
            img_dir.mkdir(parents=True, exist_ok=True)
            continue
        removed = 0
        for item in img_dir.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
                removed += 1
            elif item.is_dir():
                removed += sum(1 for _ in item.rglob("*") if _.is_file())
                shutil.rmtree(item)
        total_removed += removed
        if removed:
            print(f"  🧹 [{platform}] 이전 이미지 {removed}개 삭제 (폴더 유지)")

    if total_removed:
        print(f"  ✅ 경제 브리핑 이미지 완전 초기화: {total_removed}개")

    return total_removed
