"""JARVIS02_WRITER/writer_agent.py — capability 선언 + 이벤트 구독.

★ 이 파일은 *capability 선언* + *이벤트 구독* 만. 잡은 JARVIS04_SCHEDULER 통합 관리.
register() 는 skip_dirs 로 호출되지 않으므로 모듈 레벨 _setup_subscriptions() 로 자동 구독.

이벤트 흐름:
- POST_ANALYZED  → 분석 완료 텔레그램 알림 (suggestion 있을 때만)
- TREND_DETECTED → 로그 기록 (테마 큐잉은 j01_radar_check cron 이 처리)
- POST_REVISED   → 수정 완료 알림
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.capabilities import declare
from datetime import datetime as _dt


def _status_section() -> str:
    import jarvis_daemon as _dm
    now = _dt.now()
    lines = ["📰 *JARVIS02 — WRITER*"]
    sched = _dm._sched
    if sched:
        lock_file = _dm.WRITER_DIR / ".posting.lock"
        if lock_file.exists():
            try:
                owner = lock_file.read_text(encoding="utf-8").split("\n")[0]
                lines.append(f"🔄 포스팅 중: {owner}")
            except Exception:
                lines.append("🔄 포스팅 중")
        else:
            paused = getattr(sched, "_paused", False)
            lines.append(f"{'⏸ 일시정지' if paused else '✅ 감시 중 (대기)'}")
        try:
            p      = sched.load_progress()
            themes = sched.load_themes()
            idx    = p.get("index", 0)
            done   = len(p.get("done", []))
            failed = len(p.get("failed", []))
            total  = len(themes)
            next_t = themes[idx] if idx < total else "전체 완료"
            sched_hours = getattr(sched, "SCHEDULE_HOURS", [16])
            next_h = next((h for h in sorted(sched_hours) if h > now.hour), sorted(sched_hours)[0])
            diff_h = (next_h - now.hour) % 24
            lines.append(f"📋 테마: {done}/{total}개 완료" + (f" | 실패 {failed}개" if failed else ""))
            lines.append(f"⏭ 다음: {next_t}")
            lines.append(f"⏰ 실행: 매일 {next_h:02d}:00 ({diff_h}시간 후)")
        except Exception:
            pass
        try:
            p  = sched.load_progress()
            ps = p.get("platform_status", {})
            if ps:
                lines.append("📌 최근 발행:")
                for theme, res in list(ps.items())[-2:]:
                    nv = "✅" if res.get("naver")   else "❌"
                    ts = "✅" if res.get("tistory") else "❌"
                    lines.append(f"  {theme[:18]}: N{nv} T{ts}")
        except Exception:
            pass
        eco_diff = (7 - now.hour) % 24
        lines.append(f"📊 경제 브리핑: 매일 07:00 ({eco_diff}시간 후)")
    else:
        lines.append("❌ 스케줄러 로드 실패")
    return "\n".join(lines)


CAPABILITIES = declare(
    agent_id="jarvis02_writer",
    domain="blog",
    intents=[
        "blog.theme_post.create",       # 테마글 작성·발행 (16시)
        "blog.economic_post.create",    # 경제 브리핑 글 작성·발행 (07시)
        "blog.post.revise",             # 발행물 사후 수정 (인라인 버튼 승인)
        # NOTE: blog.post.evaluate 는 JARVIS03_RADAR (분석 담당) 단독 — 중복 제거
    ],
    tools=[
        # 발행 도구 (Phase 2 에서 @register_tool 적용 예정)
        "naver_publish", "tistory_publish",
    ],
    requires_approval=[
        "blog.post.revise",  # 사후 수정은 사용자 승인 후 실행
    ],
    cost_class="medium",
    description="블로그 자동화 — 네이버·티스토리 발행 + 사전/사후 분석.",
    tags=["blog", "writer", "naver", "tistory"],
    help_section=(
        "📰 *블로그 발행 (JARVIS02)*\n"
        "/economic            경제 브리핑 즉시 발행 (전체)\n"
        "/economic\\_naver     네이버만\n"
        "/economic\\_tistory   티스토리만\n"
        "/next                다음 테마 즉시 실행\n"
        "/stop  ·  /resume    발행 일시정지 · 재개"
    ),
    status_fn=_status_section,
)


def _on_post_analyzed(payload: dict, source: str):
    """POST_ANALYZED 이벤트 수신 — 텔레그램 알림은 post_quality_analyzer가 담당하므로 여기선 로그만."""
    try:
        import logging
        _log = logging.getLogger("writer_agent")
        count    = payload.get("suggestion_count", 0)
        theme    = payload.get("theme", "?")
        platform = payload.get("platform", "?")
        aid      = payload.get("analysis_id", "?")
        _log.info(f"[writer_agent] POST_ANALYZED: {theme} ({platform}) 제안 {count}건 ID={aid}")
    except Exception:
        pass


def _on_trend_detected(payload: dict, source: str):
    """TREND_DETECTED → 로그만 기록 (테마 큐잉은 j01_radar_check cron 담당)."""
    import logging
    _log = logging.getLogger("writer_agent")
    top5 = payload.get("top_5", [])
    _log.info(f"📊 TREND_DETECTED: {len(top5)}개 키워드 — {top5[:3]}")


def _on_post_revised(payload: dict, source: str):
    """POST_REVISED → 수정 완료 텔레그램 알림."""
    try:
        import importlib
        dm = importlib.import_module("jarvis_daemon")
        send = getattr(dm, "_send_tg", None)
        if not send:
            return
        theme    = payload.get("theme", "?")
        platform = payload.get("platform", "?")
        url      = payload.get("url", "")
        msg = f"🎉 수정 완료: *{theme}* ({platform})"
        if url:
            msg += f"\n{url}"
        send(msg)
    except Exception:
        pass


_SUBSCRIBED_W = False

def _setup_subscriptions():
    global _SUBSCRIBED_W
    if _SUBSCRIBED_W:
        return
    try:
        from shared import bus as _bus
        _bus.subscribe(_bus.EventType.POST_ANALYZED,  _on_post_analyzed)
        _bus.subscribe(_bus.EventType.TREND_DETECTED, _on_trend_detected)
        _bus.subscribe(_bus.EventType.POST_REVISED,   _on_post_revised)
        _SUBSCRIBED_W = True
    except Exception as e:
        import logging
        logging.getLogger("writer_agent").warning(f"구독 등록 실패: {e}")


def register(scheduler, bus):
    _setup_subscriptions()


# 모듈 로드 시 자동 구독 (skip_dirs 로 register() 못 받아도 작동)
_setup_subscriptions()
