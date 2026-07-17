"""JARVIS08_PUBLISH — 발행 도메인 에이전트 진입점.

ADR 008 Phase 2 신설된 JARVIS08_PUBLISH 패키지의 *에이전트 자동 등록* 진입점.
데몬이 `_autoregister_agents()` 로 스캔 → `register(scheduler, bus)` 호출 →
capability declare() 로 텔레그램·hub·infra 자동 노출.

★ 누락 발견 2026-05-17 — 사용자 지적: "새 도메인 생기면 자동 등록되야 하는 거 아냐?"
   ADR 008 Phase 2 에서 JARVIS08_PUBLISH 패키지만 신설하고 *agent 등록 진입점* 누락.
   본 파일이 그 누락 보강.
"""
from __future__ import annotations

import logging
import os

# JARVIS07 오류 보고 API
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 데몬 등록 진입점
# ──────────────────────────────────────────────────────────────

def register(scheduler, bus) -> None:
    """데몬 부팅 시 자동 호출 — capability 선언 + bus 구독.

    Args:
        scheduler: APScheduler BackgroundScheduler 인스턴스 (잡 등록용 — 현재 미사용)
        bus: shared.bus 이벤트 버스 (POST_PUBLISHED 등 구독)
    """
    _register_capability()
    _subscribe_bus(bus)
    log.info("✅ JARVIS08_PUBLISH 등록 완료")


# ──────────────────────────────────────────────────────────────
# Capability 선언 (텔레그램·hub·/status 자동 노출)
# ──────────────────────────────────────────────────────────────

def _register_capability() -> None:
    try:
        from shared.capabilities import declare
        declare(
            agent_id   = "jarvis08_publish",
            domain     = "publish",
            # ★ FIX[8] (전수감사 2026-07-17): publish.* 인텐트는 dispatchers SAFE/APPROVAL·router
            #   어디에도 미배선(vestigial)이라 자유문장이 매핑되면 DEFERRED→사용자 에러. 실제 발행은
            #   writer harness send 콜백(JARVIS08 platforms)으로 동작하므로 라우터 인텐트 불필요 → 제거.
            #   (향후 publish.* 를 직접 라우팅 타깃으로 둘지는 배선 시 재선언)
            intents    = [],
            tools      = [],
            requires_approval = [],
            cost_class = "high",   # 외부 발행 = high impact
            description= (
                "발행 도메인 단일 진입점 — 네이버·티스토리 Selenium. "
                "카테고리 상수 + 쿠키 refresher."
            ),
            tags       = [
                "publish", "naver", "tistory",
                "category", "cookie", "credentials", "selenium",
            ],
            help_section=(
                "📤 *발행 관리 (JARVIS08)*\n"
                "• 자유 문장으로 요청 (예: 오늘 트렌드로 티스토리에 글 써줘)\n"
                "• 수동 쿠키 갱신: `/refresh_naver` `/refresh_tistory`\n"
                "• 발행 함수는 사용자 텔레그램 ✅ 승인 후 실행"
            ),
            status_fn=_status_section,
        )
    except Exception as e:
        log.warning(f"⚠️ jarvis08_publish capability 등록 실패: {e}")
        _g_report("publish", e, module=__name__)


# ──────────────────────────────────────────────────────────────
# /status 카드 — JARVIS00_INFRA 가 호출
# ──────────────────────────────────────────────────────────────

def _status_section() -> str:
    """`/status` 텔레그램 응답에 포함될 JARVIS08 섹션."""
    try:
        # 쿠키 만료 여부 빠른 점검
        from pathlib import Path
        from datetime import datetime, timedelta
        _ROOT  = Path(__file__).resolve().parent.parent
        _legacy = _ROOT / "JARVIS02_WRITER"
        nv_cookie = _legacy / "naver_cookies.pkl"
        nv_age   = "?"
        if nv_cookie.exists():
            age = datetime.now() - datetime.fromtimestamp(nv_cookie.stat().st_mtime)
            nv_age = f"{age.total_seconds() / 3600:.1f}시간 전"
        # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
        from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
        ts_cookie_set = "✅" if get_tistory_cookie() else "❌"

        lines = [
            "📤 *JARVIS08 PUBLISH* — 발행 도메인",
            f"  • 네이버 쿠키 갱신: {nv_age}",
            f"  • 티스토리 TS_COOKIE 설정: {ts_cookie_set}",
            "  • 도구: post_to_naver · post_to_tistory",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"📤 *JARVIS08 PUBLISH* — status 조회 오류: {e}"


# ──────────────────────────────────────────────────────────────
# 이벤트 버스 구독 (옵션 — 향후 자동 후속 처리용)
# ──────────────────────────────────────────────────────────────

def _subscribe_bus(bus) -> None:
    """shared.bus 의 publish.* 이벤트 구독.

    현재는 발행 흐름이 *동기 함수 호출* 로 처리되므로 별도 구독 없음.
    향후 *발행 후 자동 검증·통계* 같은 비동기 후속 처리 추가 시 여기에 등록.
    """
    pass


__all__ = ["register"]


# 모듈 import 만으로도 capability 가 등록되도록 (다른 에이전트와 일관성)
_register_capability()
