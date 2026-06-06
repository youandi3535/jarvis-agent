"""
JARVIS 이벤트 버스
에이전트 간 통신의 유일한 창구. 직접 DB를 건드리지 말고 여기서 publish/subscribe.

사용법:
  # 발행
  from shared.bus import publish, EventType
  publish(EventType.TREND_DETECTED, "RADAR", {"date": "2026-04-22", "top": [...]})

  # 구독 (에이전트 register 함수 안에서 한 번만 호출)
  from shared.bus import subscribe, EventType
  def on_published(payload, source):
      ...
  subscribe(EventType.POST_PUBLISHED, on_published)

데몬은 dispatch_pending() 을 1분마다 돌려 events 테이블의 신규 행을
구독자에게 전달한다. 핸들러가 발생시킨 예외는 격리되어 다른 핸들러나
데몬을 멈추지 않는다.
"""
from __future__ import annotations
import logging
import queue as _queue
import threading as _threading
from typing import Callable, Dict, List
from shared import db

_log = logging.getLogger("bus")

# event_type → [handler(payload, source), ...]
_subscribers: Dict[str, List[Callable]] = {}
# 마지막으로 dispatch 한 events.id
_dispatch_cursor: int = 0
_cursor_lock = _threading.Lock()

# ── 빠른 인프로세스 디스패치 ──────────────────────────────────────
_fast_queue: _queue.Queue = _queue.Queue(maxsize=2000)
_fast_thread: _threading.Thread | None = None


def _fast_dispatch_loop() -> None:
    """백그라운드 스레드 — Queue에서 이벤트를 즉시 핸들러에 전달."""
    global _dispatch_cursor
    while True:
        try:
            item = _fast_queue.get(timeout=5)
        except _queue.Empty:
            continue
        if item is None:  # shutdown sentinel
            break
        ev_id, ev, payload, source = item
        for h in _subscribers.get(ev, []):
            try:
                h(payload, source)
            except Exception as e:
                _log.error(f"❌ fast handler {ev} → {h.__name__}: {e}")
                try:
                    from JARVIS07_GUARDIAN.error_collector import report as _gr
                    _gr("bus", e, module="shared.bus", func_name=f"fast_dispatch.{h.__name__}")
                except Exception:
                    pass
        if ev_id:
            with _cursor_lock:
                if ev_id > _dispatch_cursor:
                    _dispatch_cursor = ev_id


def start_fast_dispatch() -> None:
    """데몬 시작 시 1회 호출 — 빠른 이벤트 디스패치 스레드 시작."""
    global _fast_thread
    if _fast_thread and _fast_thread.is_alive():
        return
    _fast_thread = _threading.Thread(
        target=_fast_dispatch_loop, daemon=True, name="BusFastDispatch"
    )
    _fast_thread.start()
    _log.info("⚡ 이벤트 버스 빠른 디스패치 스레드 시작")


def stop_fast_dispatch() -> None:
    """데몬 종료 시 호출 — 스레드 정상 종료."""
    global _fast_thread
    if _fast_thread and _fast_thread.is_alive():
        _fast_queue.put(None)
        _fast_thread.join(timeout=3)
    _fast_thread = None


def subscribe(event_type: str, handler: Callable):
    """이벤트 핸들러 등록. handler(payload: dict, source: str) 시그니처.

    핸들러는 idempotent 해야 한다 (같은 이벤트가 여러 번 들어올 수 있음 — 재시작 등).
    """
    _subscribers.setdefault(event_type, []).append(handler)
    _log.info(f"📥 subscribe: {event_type} → {handler.__module__}.{handler.__name__}")


def init_dispatch_cursor() -> int:
    """데몬 시작 시 1회 호출 — 누적 이벤트 폭주 방지.
    기존 events 테이블 max(id) 까지 점프 → 신규 이벤트만 dispatch.
    """
    global _dispatch_cursor
    try:
        with db.get_db() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
            val = int(row["m"] if row else 0)
    except Exception as e:
        _log.warning(f"init_dispatch_cursor 실패 (cursor=0): {e}")
        val = 0
    with _cursor_lock:
        _dispatch_cursor = val
    return _dispatch_cursor


def dispatch_pending(limit: int = 200) -> int:
    """events 테이블에서 _dispatch_cursor 보다 큰 id 를 모두 핸들러에 전달.

    fast dispatch 스레드가 실행 중이면 대부분 0건 (이미 처리됨).
    데몬 다운 중 발생한 이벤트 복구 / fallback 용도.
    """
    global _dispatch_cursor
    with _cursor_lock:
        cursor_snap = _dispatch_cursor

    if not _subscribers:
        try:
            with db.get_db() as conn:
                row = conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM events").fetchone()
                new_max = int(row["m"] if row else cursor_snap)
            with _cursor_lock:
                _dispatch_cursor = max(_dispatch_cursor, new_max)
        except Exception:
            pass
        return 0

    n = 0
    last_id = cursor_snap
    try:
        import json as _json
        with db.get_db() as conn:
            rows = conn.execute(
                """SELECT id, event_type, source, payload
                   FROM events
                   WHERE id > ?
                   ORDER BY id ASC LIMIT ?""",
                (cursor_snap, limit),
            ).fetchall()
        for r in rows:
            ev = r["event_type"]
            payload_raw = r["payload"] or "{}"
            try:
                payload = _json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
            except Exception:
                payload = {"_raw": str(payload_raw)[:200]}
            for h in _subscribers.get(ev, []):
                try:
                    h(payload, r["source"] or "")
                except Exception as e:
                    _log.error(f"❌ handler 예외 {ev} → {h.__name__}: {e}")
                    try:
                        from JARVIS07_GUARDIAN.error_collector import report as _gr
                        _gr("bus", e, module="shared.bus", func_name=f"dispatch_pending.{h.__name__}")
                    except Exception:
                        pass
            last_id = max(last_id, int(r["id"]))
            n += 1
        with _cursor_lock:
            _dispatch_cursor = max(_dispatch_cursor, last_id)
    except Exception as e:
        _log.error(f"dispatch_pending 실패: {e}")
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _gr
            _gr("bus", e, module="shared.bus", func_name="dispatch_pending")
        except Exception:
            pass
    return n


class EventType:
    TREND_DETECTED      = "trend_detected"       # RADAR → 전체
    THEME_QUEUED        = "theme_queued"          # RADAR → WRITER
    POST_PUBLISHED      = "post_published"        # WRITER → 전체
    POST_FAILED         = "post_failed"           # WRITER → 전체
    PERFORMANCE_UPDATED = "performance_updated"   # WRITER → RADAR(ANALYST)
    POST_ANALYZED       = "post_analyzed"         # RADAR → 전체 (분석 완료)
    POST_REVISE_APPROVED= "post_revise_approved"  # 사용자 승인 → WRITER
    POST_REVISED        = "post_revised"          # WRITER → 전체 (재발행 완료)
    DAILY_REVIEW_DONE   = "daily_review_completed" # RADAR → 전체 (일일 분석 완료)
    ERROR_DETECTED      = "error_detected"         # GUARDIAN → 전체 (오류 감지)
    COLLECTION_READY    = "collection_ready"       # COLLECTOR → WRITER (수집·정제 완료)


def publish(event_type: str, source: str, payload: dict = None):
    """기존 publish — payload 에 correlation_id 자동 박힘 (v2).

    호출자가 payload 에 _corr 미지정 시 tracing 컨텍스트에서 자동 발급.
    기존 시그니처·동작 호환 유지.
    """
    payload = dict(payload or {})
    # tracing 컨텍스트의 correlation_id 자동 박힘 (lazy import — 순환 회피)
    if "_corr" not in payload:
        try:
            from shared import tracing
            cid = tracing.current_correlation_id()
            if cid:
                payload["_corr"] = cid
            cause = tracing.current_causation_id()
            if cause:
                payload["_cause"] = cause
        except Exception:
            pass
    ev_id = db.log_event(event_type, source, payload)
    # fast path: in-process 즉시 dispatch (60초 폴링 대기 없음)
    try:
        _fast_queue.put_nowait((ev_id, event_type, payload, source))
    except _queue.Full:
        _log.warning(f"fast_queue full — {event_type} will dispatch via DB poll")


def publish_event(event) -> None:
    """v2 — CoreEvent 스키마 객체를 publish.

    Args:
        event: shared.schemas.CoreEvent 또는 그 서브클래스 인스턴스.
               pydantic 미설치 환경에서는 dict 처럼 동작 (fallback).

    payload 에 모든 메타 (correlation_id·causation_id·domain·schema_version)
    가 포함되어 events 테이블에 저장됨. 기존 publish() 와 동일한 row 형식이므로
    구독자 (subscribe) 영향 없음.
    """
    try:
        if hasattr(event, "to_legacy_dict"):
            full = event.to_legacy_dict()
        else:
            full = dict(event)
    except Exception:
        full = {"_error": "event serialization failed"}

    event_type = full.get("event_type", "unknown")
    source = full.get("source_agent", "unknown")
    # publish() 경유 → fast queue 자동 포함, _corr 중복 주입 방지 (_corr 이미 있으면 skip)
    publish(event_type, source, full)


# ── 편의 함수 ──────────────────────────────────────────────────

def on_trend_detected(date: str, top_keywords: list, recommendations: list):
    publish(EventType.TREND_DETECTED, "RADAR", {
        "date":            date,
        "keyword_count":   len(top_keywords),
        "top_5":           top_keywords[:5],
        "recommendations": [r["theme"] for r in recommendations[:3]],
    })


def on_theme_queued(theme: str, sector: str, score: float):
    publish(EventType.THEME_QUEUED, "RADAR", {
        "theme": theme, "sector": sector, "opportunity_score": score,
    })


def on_post_published(theme: str, platform: str, source: str = "scheduled"):
    publish(EventType.POST_PUBLISHED, "WRITER", {"theme": theme, "platform": platform})
    db.save_post(theme, platform, "published", source)


def on_post_failed(theme: str, platform: str):
    publish(EventType.POST_FAILED, "WRITER", {"theme": theme, "platform": platform})
    db.save_post(theme, platform, "failed", "scheduled")


def on_post_published_detail(theme: str, platform: str, title: str,
                              url: str = "",
                              content: str = "", html: str = "",
                              source_keyword: str = "",
                              post_type: str = "",
                              image_paths: list = None) -> int:
    """발행 직후 호출 — DB에 분석 대기 레코드 생성 후 이벤트 발행. 반환: analysis_id.

    source_keyword: RADAR pipeline 에서 발행 트리거 시 trends.keyword 와 동일한
                    raw 키워드. 환경변수 JARVIS_SOURCE_KEYWORD fallback.
    post_type:      글 종류 식별자 ('economic' / 'theme' / 자유문자열). 환경변수
                    JARVIS_POST_TYPE fallback. daily_review 분리 학습 + pre_revise
                    scope 매칭의 핵심 키.
    """
    import os as _os
    if not source_keyword:
        source_keyword = _os.environ.get("JARVIS_SOURCE_KEYWORD", "").strip()
    if not post_type:
        post_type = _os.environ.get("JARVIS_POST_TYPE", "").strip()

    # 글자수 정책은 *호출자 도메인* 의 책임. shared/bus 는 도메인 무관.
    # 자비스01 은 JARVIS02_WRITER/length_manager.py 가 발행 직전 cap 적용.
    # 다른 도메인(미래) 은 자체 정책 모듈에서 cap 후 bus 에 넘긴다.

    import json as _json
    analysis_id = db.save_post_for_analysis(
        platform=platform, theme=theme, title=title,
        url=url,
        original_content=content, original_html=html,
        source_keyword=source_keyword,
        post_type=post_type,
        image_paths=_json.dumps(image_paths or []),
    )
    publish(EventType.POST_PUBLISHED, "WRITER", {
        "theme": theme, "platform": platform, "title": title,
        "url": url, "analysis_id": analysis_id,
        "source_keyword": source_keyword or "",
        "post_type": post_type or "",
    })
    db.save_post(theme, platform, "published", "scheduled")
    return analysis_id


def on_post_analyzed(analysis_id: int, platform: str, theme: str, suggestion_count: int):
    publish(EventType.POST_ANALYZED, "RADAR", {
        "analysis_id": analysis_id, "platform": platform,
        "theme": theme, "suggestion_count": suggestion_count,
    })


def on_post_revise_approved(analysis_id: int, platform: str, theme: str):
    publish(EventType.POST_REVISE_APPROVED, "USER", {
        "analysis_id": analysis_id, "platform": platform, "theme": theme,
    })


def on_post_revised(analysis_id: int, platform: str, theme: str, url: str):
    publish(EventType.POST_REVISED, "WRITER", {
        "analysis_id": analysis_id, "platform": platform, "theme": theme, "url": url,
    })


def on_performance_updated(date: str, naver: int, tistory: int):
    publish(EventType.PERFORMANCE_UPDATED, "WRITER", {
        "date": date, "naver": naver, "tistory": tistory,
    })
    db.save_performance(date, naver, tistory)
    db.update_keyword_views(date)
