"""JARVIS03 RADAR — 테마 주제 선정 (네이버 금융 공식 테마 카탈로그).

★ 역할 이관 (사용자 박제 2026-07-18): 테마 주제 선정을 JARVIS02(WRITER)→JARVIS03(RADAR)로 이관.
경제 주제(topic_pack.pick_candidate)와 동렬 — *주제·키워드 선정은 RADAR 의 영역*. 카탈로그
데이터는 JARVIS09 수집 단일 진입점에서 가져오고, 미사용 테마 선정(고정 우선 → random)은 여기서
한다. 발행 오케스트레이션(JARVIS02 run_radar_top_theme)은 이 함수로 *테마만* 받아 쓴다.

★ 결합 회피: 발행 상태(published/done/failed)는 JARVIS02 의 것이므로 RADAR 가 직접 알지 않는다 —
호출자가 `exclude` 집합으로 넘긴다(03→02 역참조 금지 규정 준수).
"""
from __future__ import annotations

import logging
import random

log = logging.getLogger("jarvis.radar.theme")


def theme_catalog() -> dict:
    """네이버 금융 공식 테마 카탈로그 (JARVIS09 수집 단일 진입점 경유). 실패 시 빈 dict."""
    try:
        from JARVIS09_COLLECTOR.collect_theme import _fetch_naver_theme_catalog
        return _fetch_naver_theme_catalog() or {}
    except Exception as e:
        log.warning(f"[theme_picker] 카탈로그 로드 실패: {e}")
        return {}


def available_themes(exclude: set | None = None, catalog: dict | None = None) -> list:
    """미사용(호출자 제공 exclude 제외) 테마 목록. catalog 미지정 시 새로 로드.

    exclude = 이미 쓴 테마(발행완료·시도) — RADAR 는 이 상태를 직접 조회하지 않고 호출자가 넘긴다.
    """
    cat = catalog if catalog is not None else theme_catalog()
    if not cat:
        return []
    ex = exclude or set()
    return [t for t in cat if t not in ex]


def pick_theme(candidates: list, pinned: str | None = None) -> str | None:
    """후보 목록에서 1개 선정 — pinned(선계산 고정 테마)이 후보에 있으면 우선, 아니면 random."""
    if not candidates:
        return None
    if pinned and pinned in candidates:
        return pinned
    return random.choice(candidates)


def select_theme(exclude: set | None = None, pinned: str | None = None) -> str | None:
    """미사용 테마 1개 선정(단발) — available_themes → pick_theme 편의 결합. 선계산 잡용.

    발행 오케스트레이터의 반복(재선정) 경로는 available_themes + pick_theme 를 직접 조합해
    카탈로그 재로드를 피한다.
    """
    return pick_theme(available_themes(exclude), pinned)


__all__ = ["theme_catalog", "available_themes", "pick_theme", "select_theme"]
