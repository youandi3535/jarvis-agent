"""JARVIS09_COLLECTOR/run_context.py — 모든 포스트 생성의 단일 진입점.

사용:
    from JARVIS09_COLLECTOR.run_context import new_run
    ctx = new_run("SK그룹")   # ← 이 한 줄이 전체 메모리 상태 초기화

new_run() 이 하는 일:
  1. RunContext 인스턴스 생성 (theme, run_id, 빈 dict들)
  2. collect_theme.INFOG_STORE    → ctx.infog_store 로 모듈 속성 교체
  3. collect_theme.COLLECTED_DATA → ctx.collected_data 로 교체
  4. collect_theme.CHART_STORE    → ctx.chart_store 로 교체
  5. theme_charts.CHART_STORE     → ctx.chart_store 로 교체
  6. _active_ctx 모듈 전역 교체  → 이후 호출자가 run_id 조회 가능

이미지 출력 폴더는 기존 4개 고정 폴더를 그대로 사용:
  JARVIS06_IMAGE/output/images/economic_naver/
  JARVIS06_IMAGE/output/images/economic_tistory/
  JARVIS06_IMAGE/output/images/theme_naver/
  JARVIS06_IMAGE/output/images/theme_tistory/
포스트 시작 전 해당 폴더를 비우고, 새 이미지를 넣는다.

(기존 코드 변경 없이 단일 진입점 효과 — 모듈 속성 rebinding 패턴)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class RunContext:
    """한 번의 블로그 글 생성에 대한 완전한 상태 컨테이너."""
    theme: str
    platform: str = "naver"
    post_type: str = "theme"  # "theme" | "economic" (메타데이터 용도)
    run_id: str = field(default_factory=lambda: uuid4().hex)
    chart_store: dict = field(default_factory=dict)
    infog_store: dict = field(default_factory=dict)
    collected_data: dict = field(default_factory=dict)


# ── 모듈 전역 활성 컨텍스트 ────────────────────────────────────────────────────
_active_ctx: RunContext | None = None


def new_run(theme: str, platform: str = "naver", post_type: str = "theme") -> RunContext:
    """새 글 생성 단일 진입점.

    이 함수 하나만 호출하면:
    - 이전 글의 CHART_STORE·INFOG_STORE·COLLECTED_DATA 완전 제거 (메모리)
    - 새 run_id 부여 (차트 타입 셔플·색상 해시 재시드)
    - 모든 모듈 전역을 새 컨텍스트의 dict 로 교체

    이미지 파일 정리는 각 포스터(run_tistory/run_naver/jarvis_main)가
    기존 4개 고정 폴더를 rmtree 후 재생성하는 방식으로 처리.

    Args:
        theme: 주제명 (예: "SK그룹", "반도체", "경제브리핑")
        platform: 플랫폼 ("naver" | "tistory"), 기본 "naver"
        post_type: 포스트 종류 ("theme" | "economic"), 기본 "theme"

    Returns:
        RunContext — 이번 글 생성의 모든 상태를 담는 컨테이너
    """
    global _active_ctx
    ctx = RunContext(theme=theme, platform=platform, post_type=post_type)
    _active_ctx = ctx
    _rebind_globals(ctx)
    return ctx


def _rebind_globals(ctx: RunContext) -> None:
    """모든 관련 모듈의 전역 상태를 ctx 의 dict 로 교체 (lazy import)."""
    try:
        import JARVIS09_COLLECTOR.collect_theme as _ct
        _ct.INFOG_STORE    = ctx.infog_store
        _ct.COLLECTED_DATA = ctx.collected_data
        _ct.CHART_STORE    = ctx.chart_store
    except Exception:
        pass

    try:
        import JARVIS06_IMAGE.theme_charts as _tc
        _tc.CHART_STORE = ctx.chart_store
    except Exception:
        pass
