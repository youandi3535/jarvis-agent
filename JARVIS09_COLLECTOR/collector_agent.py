"""JARVIS09_COLLECTOR/collector_agent.py — 에이전트 진입점.

흐름:
  JARVIS03 → bus(THEME_QUEUED) → _on_theme_queued()
    → collect_for_theme()
    → bus(COLLECTION_READY, payload={theme, results})
    → JARVIS02 WRITER 수신
"""
from __future__ import annotations
import logging

log = logging.getLogger("jarvis.collector")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

# ★ 검증 레지스트리 등록 (2026-07-02): collect_for_theme 산출물 체크포인트.
try:
    from JARVIS00_INFRA.verification import register_check as _rc

    @_rc("collect_for_theme", "수집 결과 비공백", severity="block")
    def _chk_collect_nonempty(output, ctx):
        return "수집 결과 0건(무성 실패)" if not output else ""
except Exception:
    pass


def _on_theme_queued(payload: dict, source: str) -> None:
    """THEME_QUEUED 이벤트 수신 → 수집·정제 → COLLECTION_READY 발행."""
    theme = (payload.get("theme") or "").strip()
    sector = (payload.get("sector") or "").strip()
    if not theme:
        log.warning("[Collector] THEME_QUEUED payload에 theme 없음 — 스킵")
        return

    log.info(f"[Collector] 수집 시작: {theme} / {sector}")
    try:
        from JARVIS09_COLLECTOR.collector_engine import collect_for_theme
        from shared.bus import publish, EventType

        results = collect_for_theme(theme, sector)
        if not results:
            log.warning(f"[Collector] 수집 결과 0건: {theme}")
            # ★ 무성 실패 신호 (2026-07-02): 조용히 return 하지 않고 GUARDIAN 박제 → 가시화
            _g_report("collector", RuntimeError(f"수집 0건(무성 실패): {theme}"),
                      module=__name__, func_name="_on_theme_queued")
            return

        # COLLECTION_READY 발행 — JARVIS02가 구독
        publish(
            EventType.COLLECTION_READY,
            "COLLECTOR",
            {
                "theme": theme,
                "sector": sector,
                "results": [
                    {
                        "source_type": r.source_type,
                        "url": r.url,
                        "title": r.title,
                        "cleaned_text": r.cleaned_text,
                        "word_count": r.word_count,
                    }
                    for r in results
                ],
                "total": len(results),
            },
        )
        log.info(f"[Collector] COLLECTION_READY 발행 완료: {len(results)}건")

        # DB 저장
        try:
            from shared import db as _db
            for r in results:
                _db.save_collection_result(theme, r.source_type, r.url, r.title, r.cleaned_text)
        except Exception as e:
            log.warning(f"[Collector] DB 저장 실패: {e}")

    except Exception as e:
        log.error(f"[Collector] 수집 파이프라인 오류: {e}")
        _g_report("collector", e, module=__name__, func_name="_on_theme_queued")


def _status_section() -> str:
    """텔레그램 /status 노출용 상태 문자열."""
    lines = ["📦 *JARVIS09 COLLECTOR*"]
    try:
        from shared import db as _db
        con = _db.get_db()
        total = (con.execute("SELECT COUNT(*) FROM collection_results").fetchone() or (0,))[0]
        today = (con.execute(
            "SELECT COUNT(*) FROM collection_results "
            "WHERE collected_at >= date('now')"
        ).fetchone() or (0,))[0]
        con.close()
        lines.append(f"📊 DB 수집 레코드: 총 {total}건 · 오늘 {today}건")
    except Exception as _e:
        lines.append(f"⚠️ DB 조회 실패: {_e}")
    lines.append("🔌 프로바이더: NaverNews · GoogleNews · KorEcon · KRX · Blog · Web · DART · ECOS · KOSIS · yfinance · arXiv (11종)")
    lines.append("📡 THEME_QUEUED → 병렬 수집 → COLLECTION_READY")
    return "\n".join(lines)


def job_cleanup_cache() -> None:
    """오래된 수집 캐시 정리 (j09_cleanup 잡)."""
    try:
        from shared import db as _db
        con = _db.get_db()
        con.execute(
            "DELETE FROM collection_results WHERE collected_at < datetime('now', '-7 days')"
        )
        con.commit()
        con.close()
        log.info("[Collector] 7일 이전 캐시 정리 완료")
    except Exception as e:
        log.warning(f"[Collector] 캐시 정리 실패: {e}")


# ── capability 등록 (모듈 레벨) ─────────────────────────────────
try:
    from shared.capabilities import declare
    declare(
        agent_id="jarvis09_collector",
        status_fn=_status_section,
        help_section=(
            "🕸️ *수집 관리 (JARVIS09)*\n"
            "예: 수집 현황 보여줘 / 반도체 수집 이력\n"
            "• blog·news·academic·finance·web 자동 수집\n"
            "• 정제 원본 WRITER 전달 · robots.txt 준수"
        ),
        intents=["collect.status", "collect.history"],
        domain="collection",
        description="주제별 인터넷 공개 데이터 수집·정제 (blog/news/academic/finance/web)",
    )
except Exception:
    pass


def register(scheduler, bus) -> None:
    """데몬 부팅 시 자동 호출 — THEME_QUEUED 구독."""
    bus.subscribe(bus.EventType.THEME_QUEUED, _on_theme_queued)
    log.info("[Collector] THEME_QUEUED 구독 완료")
