"""JARVIS03_RADAR/radar_agent.py — capability 선언 + 이벤트 구독.

★ 이 파일은 *capability 선언* + *이벤트 구독* 만. 잡 등록은 JARVIS04 통합.
register() 는 데몬 부팅 시 자동 호출 — bus.subscribe 로 이벤트 핸들러 등록.

이벤트 흐름:
- POST_PUBLISHED      → 즉시 품질 분석 트리거 (5분 폴링 fallback 보다 빠른 반응)
- PERFORMANCE_UPDATED → 즉시 keyword_performance 피드백 업데이트 트리거
- DAILY_REVIEW_DONE   → 로그 기록
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.capabilities import declare


def _status_section() -> str:
    lines = ["📡 *JARVIS03 — RADAR*"]
    try:
        from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
        from datetime import datetime as _dt
        apscheduler = get_apscheduler()
        now = _dt.now()
    except Exception:
        apscheduler = None
    if apscheduler:
        # ★ 잡 ID 는 DEFAULT_JOBS(SSOT)에서 파생 — 하드코딩 금지 (사용자 박제 2026-07-04).
        #   트렌드 잡이 추가/변경되면(예: 06시 신설) 자동 반영. 후보 중 가장 이른 것 표시.
        from JARVIS04_SCHEDULER.job_registry import job_ids as _jids
        job_map = {
            "트렌드 수집":   _jids("radar_trends"),
            "성과 수집":     _jids("radar_perf"),
            "분석 fallback": _jids("analyzer_fb"),
        }
        for jname, jids in job_map.items():
            cand = [apscheduler.get_job(j) for j in jids]
            cand = [j for j in cand if j and j.next_run_time]
            if cand:
                job  = min(cand, key=lambda j: j.next_run_time)
                nrt  = job.next_run_time.astimezone(now.astimezone().tzinfo)
                diff = nrt - now.astimezone()
                th, tr = divmod(int(diff.total_seconds()), 3600)
                tm = tr // 60
                t_str = f"{th}시간 {tm}분 후" if th else f"{tm}분 후"
                lines.append(f"✅ {jname}: {t_str}")
            else:
                lines.append(f"⚠️ {jname}: 스케줄 없음")
    else:
        lines.append("⚠️ APScheduler 미실행")
    try:
        from shared import db as _db
        pending = _db.get_pending_analysis(limit=99)
        cnt = len(pending)
        lines.append(f"🔍 품질분석 대기: {cnt}건" if cnt else "✅ 품질분석 대기 없음")
    except Exception:
        pass
    return "\n".join(lines)


CAPABILITIES = declare(
    agent_id="jarvis03_radar",
    domain="trend",
    intents=[
        "trend.collect",            # 트렌드 키워드 수집
        "trend.report",             # 트렌드 분석 리포트
        "blog.post.evaluate",       # 발행물 품질 분석 (post_quality_analyzer)
        "blog.daily_review",        # 일일 종합 분석
        "performance.collect",      # 조회수·rank 수집
        "learning.train",           # 가중치 학습
    ],
    tools=[
        # 미래: 임베딩 검색·DataLab 조회 등 도구로 노출
    ],
    requires_approval=[],
    cost_class="medium",
    description="트렌드 레이더 + 발행물 분석 + 학습 루프. 자비스01 의 학습 신호 공급.",
    tags=["radar", "trend", "analyzer", "learning", "performance"],
    help_section=(
        "📡 *트렌드 & 분석 (JARVIS03)*\n"
        "/trend   트렌드 TOP 10\n"
        "/radar   추천 테마 목록\n"
        "/report  성과 리포트"
    ),
    status_fn=_status_section,
)


def _on_post_published(payload: dict, source: str):
    """POST_PUBLISHED 이벤트 → 즉시 품질 분석 트리거.

    이전: 매 5분 analyzer_fb 폴링이 status='pending_analysis' 글 처리.
    개선: 발행 직후 *즉시* 분석 (analyzer_fallback subprocess 별도 스레드 트리거).
    폴링은 안전망으로 유지.
    """
    try:
        import threading
        # daemon 의 job_analyzer_fallback 함수를 lazy import (subprocess 트리거)
        import importlib
        try:
            mod = importlib.import_module("jarvis_daemon")
            fn = getattr(mod, "job_analyzer_fallback", None)
            if fn:
                threading.Thread(target=fn, daemon=True,
                                 name="evt_post_published_analyze").start()
        except Exception as e:
            # 부팅 환경에 따라 jarvis_daemon import 실패 가능 — 폴링 fallback 으로 처리됨
            print(f"  ⚠️ POST_PUBLISHED 즉시 트리거 실패 (5분 fallback 으로 처리): {e}")
            _g_report("radar", e, module=__name__)
    except Exception:
        pass


def register(scheduler, bus):
    """데몬 부팅 시 자동 호출 — 이벤트 구독 등록.

    잡 등록은 JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 통합 (skip_dirs 로
    register() 호출은 거치지 않지만 import 는 항상 됨).

    NOTE: legacy skip_dirs 에 JARVIS03_RADAR 가 포함되어 register() 미호출.
    구독은 *모듈 레벨* 에서 직접 처리 — 아래 _setup_subscriptions().
    """
    _setup_subscriptions(bus)


def _on_performance_updated(payload: dict, source: str):
    """PERFORMANCE_UPDATED → keyword_performance 피드백 즉시 업데이트."""
    import threading
    def _run():
        try:
            from JARVIS03_RADAR.learning import update_feedback_from_events
            result = update_feedback_from_events(days=1, verbose=False)
            import logging
            logging.getLogger("radar_agent").info(
                f"📈 PERFORMANCE_UPDATED 처리: keyword {result.get('updated',0)}개 업데이트"
            )
        except Exception as e:
            import logging
            logging.getLogger("radar_agent").warning(f"performance_updated 핸들러 오류: {e}")
    threading.Thread(target=_run, daemon=True, name="evt_perf_updated").start()


def _on_daily_review_done(payload: dict, source: str):
    """DAILY_REVIEW_DONE → 로그 기록."""
    import logging
    logging.getLogger("radar_agent").info(
        f"📋 DAILY_REVIEW_DONE: {payload.get('date','?')} — "
        f"분석 {payload.get('analyzed_count',0)}건"
    )


def _setup_subscriptions(bus_mod=None):
    """모듈 import 시점에 *한 번만* 구독 등록 (idempotent — 재로드 시 중복 X)."""
    global _SUBSCRIBED
    if globals().get("_SUBSCRIBED"):
        return
    try:
        from shared import bus as _bus
        _bus.subscribe(_bus.EventType.POST_PUBLISHED,      _on_post_published)
        _bus.subscribe(_bus.EventType.PERFORMANCE_UPDATED, _on_performance_updated)
        _bus.subscribe(_bus.EventType.DAILY_REVIEW_DONE,   _on_daily_review_done)
        _SUBSCRIBED = True
    except Exception as e:
        print(f"  ⚠️ JARVIS03 구독 등록 실패: {e}")
        _g_report("radar", e, module=__name__)


_SUBSCRIBED = False
# 모듈 레벨 자동 구독 (skip_dirs 로 register() 못 받아도 작동)
_setup_subscriptions()
