"""JARVIS01_MASTER/dispatchers.py — 인텐트 → 실제 실행 디스패처.

SAFE    — 즉시 실행 (정보 조회·읽기 전용)
APPROVAL — 외부 영향 → 인라인 버튼 확인 후 실행
DEFERRED — 미구현 에이전트 도메인
"""
from __future__ import annotations

from pathlib import Path


# ── 모드 분류표 ─────────────────────────────────────────────────

SAFE_INTENTS: set[str] = {
    # core (JARVIS01 라우터)
    "core.list_agents",
    "core.chat",
    "core.unknown",
    "core.preview_route",
    # infra (데몬)
    "infra.status",
    "infra.daemon.start",
    # architect (설계타임 메타 — JARVIS00_INFRA)
    "architect.design",
    # trend / blog
    "trend.report",
    "blog.post.evaluate",
    # schedule (JARVIS04 — 잡 메타 조회)
    "schedule.job.list",
    "schedule.job.next",
    "schedule.history.query",
    "schedule.report.daily",
    # guardian (JARVIS07 — 오류 조회)
    "error.list",
    "error.stats",
    "error.ignore",
    # collector (JARVIS09 — 수집 현황 조회)
    "collect.status",
    "collect.history",
}

APPROVAL_INTENTS: set[str] = {
    "blog.theme_post.create",
    "blog.economic_post.create",
    "blog.post.revise",
    "infra.daemon.restart",
    "infra.daemon.shutdown",
    # schedule (JARVIS04 — 잡 변경)
    "schedule.job.pause",
    "schedule.job.resume",
    "schedule.job.run_now",
    "schedule.job.remove",
}

# 플랫폼 한글 라벨
_PLAT_LABEL: dict[str, str] = {"naver": "네이버", "tistory": "티스토리"}


def _safe_params(params) -> dict:
    """params 가 dict 가 아닐 때 (None·str·기타) 빈 dict 로 정규화."""
    return params if isinstance(params, dict) else {}


def _platforms_str(params: dict) -> str:
    """params.platforms → '네이버 · 티스토리' 같은 한글 표시.

    빈 리스트·None·미지정이면 '2개 플랫폼 전체'.
    """
    p = _safe_params(params)
    plats = p.get("platforms") or []
    if not isinstance(plats, list) or not plats or len(plats) == 2:
        return "네이버 · 티스토리 (전체)"
    return " · ".join(_PLAT_LABEL.get(x, x) for x in plats) + f" ({len(plats)}개)"


# 승인 요청 설명 (title, detail_fn(params, user_msg) → str)
_APPROVAL_META: dict[str, tuple[str, object]] = {
    "blog.theme_post.create": (
        "테마글 발행",
        lambda p, m: f"테마: *{p.get('theme_name', '다음 대기 테마')}*\n"
                     f"플랫폼: {_platforms_str(p)}",
    ),
    "blog.economic_post.create": (
        "경제 브리핑 글 발행",
        lambda p, m: f"오늘 경제 브리핑\n플랫폼: {_platforms_str(p)}",
    ),
    "blog.post.revise": (
        "발행글 수정",
        lambda p, m: "최근 승인 대기 발행글에 사전 수정을 실행합니다",
    ),
    "infra.daemon.restart": (
        "데몬 재시작",
        lambda p, m: "JARVIS 데몬 전체 재시작\n(Keeper가 10초 후 자동 재기동)",
    ),
    "infra.daemon.shutdown": (
        "데몬 종료",
        lambda p, m: "JARVIS 데몬 완전 종료\n⚠️ Keeper 자동 재시작도 일시 중단됩니다",
    ),
    # ── schedule.* (JARVIS04 잡 변경) ─────────────────────────
    "schedule.job.pause": (
        "잡 일시정지",
        lambda p, m: f"잡 ID: `{(_safe_params(p).get('job_id') or '?')}` — APScheduler pause_job (데몬 재시작 시 초기화)",
    ),
    "schedule.job.resume": (
        "잡 재개",
        lambda p, m: f"잡 ID: `{(_safe_params(p).get('job_id') or '?')}` — APScheduler resume_job",
    ),
    "schedule.job.run_now": (
        "잡 즉시 실행",
        lambda p, m: f"잡 ID: `{(_safe_params(p).get('job_id') or '?')}` — 별도 스레드 즉시 실행",
    ),
    "schedule.job.remove": (
        "잡 제거",
        lambda p, m: f"잡 ID: `{(_safe_params(p).get('job_id') or '?')}` — APScheduler remove_job (DEFAULT_JOBS 잡은 재시작 시 다시 등록)",
    ),
}


# ── JARVIS04 APPROVAL 인텐트 → 도구 호출 (daemon 의 _execute_j00_approval 가 호출) ──

def execute_schedule_change(intent: str, params: dict) -> str:
    """잡 변경 (pause/resume/run_now/remove) — 텔레그램 인라인 버튼 ✅ 후 호출."""
    p = _safe_params(params)
    job_id = (p.get("job_id") or "").strip()
    if not job_id:
        return f"⚠️ `{intent}` 실행 — `job_id` 가 없습니다."
    try:
        from JARVIS04_SCHEDULER import job_controller as _ctrl
    except Exception as e:
        return f"⚠️ JARVIS04 controller 로드 실패: {e}"
    if intent == "schedule.job.pause":
        r = _ctrl.pause_job(job_id)
    elif intent == "schedule.job.resume":
        r = _ctrl.resume_job(job_id)
    elif intent == "schedule.job.run_now":
        r = _ctrl.run_job_now(job_id)
    elif intent == "schedule.job.remove":
        r = _ctrl.remove_job(job_id)
    else:
        return f"⚠️ unknown schedule intent: {intent}"
    if r.get("ok"):
        return f"✅ `{job_id}` — {r.get('action','done')}\n{r.get('note','')}"
    return f"❌ `{job_id}` 실패: {r.get('error','unknown')}"


def get_dispatch_mode(intent: str) -> str:
    """SAFE / APPROVAL / DEFERRED 결정."""
    if intent in SAFE_INTENTS:
        return "SAFE"
    if intent in APPROVAL_INTENTS:
        return "APPROVAL"
    return "DEFERRED"


def describe_approval(intent: str, params: dict, user_msg: str) -> str:
    """승인 요청 텔레그램 메시지 생성."""
    if intent in _APPROVAL_META:
        title, detail_fn = _APPROVAL_META[intent]
        detail = detail_fn(params, user_msg)
        return f"🔔 *{title}* 실행 요청\n\n{detail}\n\n실행하시겠습니까?"
    return f"🔔 `{intent}` 실행 요청\n\n실행하시겠습니까?"


def build_j01_command(intent: str, params) -> str | list[str] | None:
    """승인 후 JARVIS02 handle_telegram_command 에 전달할 명령.

    Returns:
        str: 단일 명령 (예: "/run 반도체")
        list[str]: 여러 명령 순차 실행 (예: ["/naver 반도체", "/tistory 반도체"])
        None: daemon 이 직접 처리

    플랫폼 매핑:
        platforms=[] 또는 2개 → /run (전체)
        platforms=["naver"]   → /naver 테마명
        platforms=["tistory"] → /tistory 테마명
        platforms=2개         → 각 플랫폼 명령 순차
    """
    params = _safe_params(params)
    plats = params.get("platforms") or []
    if not isinstance(plats, list):
        plats = []

    if intent == "blog.theme_post.create":
        theme = (params.get("theme_name") or "").strip()
        # 플랫폼 분리 발행
        if plats and len(plats) < 2:
            cmds = []
            for p in plats:
                if p == "naver":
                    cmds.append(f"/naver {theme}" if theme else "/naver")
                elif p == "tistory":
                    cmds.append(f"/tistory {theme}" if theme else "/tistory")
            if cmds:
                return cmds[0] if len(cmds) == 1 else cmds
        # 전체 (기본)
        return f"/run {theme}" if theme else "/next"

    if intent == "blog.economic_post.create":
        if plats and len(plats) < 2:
            cmds = []
            for p in plats:
                if p == "naver":
                    cmds.append("/economic_naver")
                elif p == "tistory":
                    cmds.append("/economic_tistory")
            if cmds:
                return cmds[0] if len(cmds) == 1 else cmds
        return "/economic"

    return None


# ── SAFE 인텐트 실행 (J01/J02 호출 필요한 것만) ────────────────

def execute_safe(intent: str, params: dict, user_msg: str) -> str | None:
    """SAFE 인텐트 중 J01/J02/J03 호출이 필요한 것을 처리.

    core.* 인텐트는 daemon이 직접 처리하므로 None 반환.
    """
    if intent == "trend.report":
        return _trend_report()
    if intent == "blog.post.evaluate":
        return _trigger_quality_analysis()
    # ── architect.* (JARVIS00_INFRA — 설계타임 메타) ──────────
    if intent == "architect.design":
        return _architect_design(params, user_msg)
    # ── schedule.* (JARVIS04) ─────────────────────────────────
    if intent == "schedule.job.list":
        return _schedule_job_list()
    if intent == "schedule.job.next":
        return _schedule_next_runs(params)
    if intent == "schedule.history.query":
        return _schedule_history(params)
    if intent == "schedule.report.daily":
        return _schedule_daily_briefing()
    # ── error.* (JARVIS07 GUARDIAN) ───────────────────────────
    if intent == "error.list":
        return _guardian_error_list(params)
    if intent == "error.stats":
        return _guardian_error_stats()
    if intent == "error.ignore":
        return _guardian_error_ignore(params)
    # collector (JARVIS09)
    if intent == "collect.status":
        return _collect_status()
    if intent == "collect.history":
        return _collect_history(params)
    return None  # core.* → daemon이 처리


# ── architect SAFE 인텐트 처리 (JARVIS00_INFRA 위임) ──────────────

def _architect_design(params, user_msg: str) -> str:
    """architect.design — 설계 기획서 산출. JARVIS00_INFRA.architect 단일 진입점."""
    p = _safe_params(params)
    user_intent = (p.get("user_intent") or p.get("intent_text") or user_msg or "").strip()
    scope = (p.get("scope") or "agent").strip()
    if not user_intent:
        return "⚠️ ARCHITECT — 사용자 의도가 비어 있습니다. 예: \"가계부 자동화 에이전트 만들고 싶어\""
    try:
        from JARVIS00_INFRA.architect import design_new_agent
    except Exception as e:
        return f"⚠️ ARCHITECT 모듈 로드 실패: {e}"
    r = design_new_agent(user_intent=user_intent, scope=scope)
    if not r.get("ok"):
        return f"❌ ARCHITECT 산출 실패: {r.get('error', 'unknown')}"
    summary    = r.get("summary", "")
    spec_path  = r.get("spec_path", "")
    warnings   = r.get("warnings") or []
    high_risks = [x for x in (r.get("errors_risk") or []) if x.get("risk") == "high"]
    n_steps    = len(r.get("next_plan_steps") or [])
    lines = [
        "🏛 *ARCHITECT — 설계 기획서 산출*",
        "",
        summary,
        "",
        f"📄 기획서: `{spec_path}`",
    ]
    if warnings:
        lines.append(f"\n⚠️ *경고 {len(warnings)}건*:")
        for w in warnings[:5]:
            lines.append(f"  • {w[:120]}")
    if high_risks:
        lines.append(f"\n🚨 *ERRORS 재현 위험 {len(high_risks)}건*:")
        for x in high_risks[:5]:
            lines.append(f"  • {x['id']}: {x['note'][:100]}")
    lines.append(f"\n💡 다음: 기획서 검토 후 `create_plan` 위임 ({n_steps}단계) → 인라인 버튼 ✅")
    return "\n".join(lines)


# ── JARVIS04 SAFE 인텐트 처리 (도구 직접 호출) ──────────────────

def _schedule_job_list() -> str:
    try:
        from JARVIS04_SCHEDULER.job_catalog import list_jobs
        jobs = list_jobs()
    except Exception as e:
        return f"⚠️ 잡 카탈로그 조회 실패: {e}"
    if not jobs:
        return "📅 등록된 잡이 없습니다."
    lines = [f"📅 *등록된 잡 {len(jobs)}개*\n"]
    for j in jobs[:30]:
        nr = j.get("next_run") or "(paused)"
        lines.append(f"• `{j['id']}` — {j.get('name','')}\n  ⏰ 다음: {nr}  · owner: {j.get('owner','-')}")
    if len(jobs) > 30:
        lines.append(f"\n... 외 {len(jobs)-30}개")
    return "\n".join(lines)


def _schedule_next_runs(params) -> str:
    p = _safe_params(params)
    limit = int(p.get("limit") or 10)
    try:
        from JARVIS04_SCHEDULER.job_catalog import next_runs
        jobs = next_runs(limit=limit)
    except Exception as e:
        return f"⚠️ 다음 실행 조회 실패: {e}"
    if not jobs:
        return "⏰ 예정된 잡이 없습니다."
    lines = [f"⏰ *다음 실행 예정 {len(jobs)}개*\n"]
    for j in jobs:
        lines.append(f"• {j['next_run']} — `{j['id']}` ({j.get('name','')})")
    return "\n".join(lines)


def _schedule_history(params) -> str:
    p = _safe_params(params)
    limit       = int(p.get("limit") or 20)
    job_id      = p.get("job_id")
    owner_agent = p.get("owner_agent") or p.get("owner")
    success     = p.get("success")
    since_hours = p.get("since_hours") or 24
    try:
        from JARVIS04_SCHEDULER.job_history import query_runs
        runs = query_runs(
            limit=limit, job_id=job_id, owner_agent=owner_agent,
            success=success, since_hours=since_hours,
        )
    except Exception as e:
        return f"⚠️ 잡 이력 조회 실패: {e}"
    if not runs:
        return f"📜 최근 {since_hours}h 이력 없음."
    lines = [f"📜 *잡 실행 이력 (최근 {since_hours}h, {len(runs)}건)*\n"]
    for r in runs[:20]:
        mark = "✅" if r.get("success") else "❌"
        dur  = r.get("duration_ms")
        dur_str = f"{dur/1000:.1f}s" if dur else "?"
        lines.append(f"{mark} `{r['job_id']}` @ {r['started_at']} ({dur_str})")
        if not r.get("success") and r.get("error"):
            lines.append(f"   └ {(r['error'] or '')[:100]}")
    return "\n".join(lines)


def _schedule_daily_briefing() -> str:
    try:
        from JARVIS04_SCHEDULER.briefing import build_briefing_text
        return build_briefing_text(hours=24)
    except Exception as e:
        return f"⚠️ 일일 브리핑 빌드 실패: {e}"


def _trend_report() -> str:
    """trends 테이블 — 가장 최근 수집일의 TOP 10 (opportunity_score 순)."""
    try:
        import sqlite3
        from shared.db import DB_PATH as _DB
        con = sqlite3.connect(str(_DB))
        rows = con.execute("""
            SELECT keyword, opportunity_score, score, sector, source
            FROM trends
            WHERE date = (SELECT MAX(date) FROM trends)
            ORDER BY opportunity_score DESC, score DESC
            LIMIT 10
        """).fetchall()
        latest_date = con.execute("SELECT MAX(date) FROM trends").fetchone()[0]
        con.close()
        if not rows:
            return (
                "📊 아직 수집된 트렌드 데이터가 없습니다.\n"
                "JARVIS03 RADAR 수집을 먼저 실행해주세요."
            )
        lines = [f"📊 *현재 상위 트렌드 — {latest_date} (기회점수 순)*\n"]
        for kw, opp, raw, sec, src in rows:
            o = opp or 0
            r = raw or 0
            lines.append(f"• *{kw}*  [{sec or '기타'}]  기회 {o:.0f} / 빈도 {r:.0f}  ({src or '?'})")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 트렌드 조회 실패: {e}"


def _trigger_quality_analysis() -> str:
    try:
        import subprocess
        _ROOT = Path(__file__).parent.parent
        python = _ROOT / ".venv" / "bin" / "python"
        script = _ROOT / "JARVIS03_RADAR" / "post_quality_analyzer.py"
        subprocess.Popen(
            [str(python), str(script)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return "🔍 품질 분석 시작! 완료되면 텔레그램으로 결과가 전송됩니다."
    except Exception as e:
        return f"⚠️ 품질 분석 트리거 실패: {e}"


# ── JARVIS07 GUARDIAN 인텐트 처리 ─────────────────────────────────

def _guardian_error_list(params: dict) -> str:
    p = _safe_params(params)
    status = p.get("status", "new")
    limit  = int(p.get("limit", 10))
    try:
        from shared import db as _db
        errors = _db.list_errors(status=status, limit=limit)
        if not errors:
            return f"✅ [{status}] 오류 없음"
        sev_icon = {"critical": "🚨", "high": "⚠️", "medium": "ℹ️", "low": "🔵"}
        lines = [f"🛡️ *GUARDIAN 오류 [{status}]* {len(errors)}건"]
        for e in errors:
            icon = sev_icon.get(e.get("severity", "medium"), "ℹ️")
            msg  = (e.get("message") or "")[:80]
            lines.append(
                f"{icon} `#{e['id']}` [{e.get('severity','?')}] {e.get('error_type','?')}\n"
                f"   {(e.get('timestamp') or '')[:16]} | {e.get('module','?')}\n"
                f"   {msg}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        return f"⚠️ 오류 목록 조회 실패: {e}"


def _guardian_error_stats() -> str:
    try:
        from shared import db as _db
        s = _db.get_error_stats(days=7)
        by_status = s.get("by_status", {})
        by_sev    = s.get("by_severity", {})
        return (
            f"🛡️ *GUARDIAN 오류 통계 (최근 7일)*\n"
            f"총계: *{s.get('total', 0)}건*\n\n"
            f"📋 상태별\n"
            f"  신규: {by_status.get('new', 0)} · 분석 중: {by_status.get('analyzing', 0)}\n"
            f"  자동수정 완료: {by_status.get('fixed', 0)} · 수정 불가: {by_status.get('wontfix', 0)}\n\n"
            f"🔥 심각도별\n"
            f"  🚨 CRITICAL: {by_sev.get('critical', 0)} · ⚠️ HIGH: {by_sev.get('high', 0)}\n"
            f"  ℹ️ MEDIUM: {by_sev.get('medium', 0)} · 🔵 LOW: {by_sev.get('low', 0)}"
        )
    except Exception as e:
        return f"⚠️ 통계 조회 실패: {e}"


def _guardian_error_ignore(params: dict) -> str:
    p = _safe_params(params)
    error_id = p.get("error_id")
    if not error_id:
        return "⚠️ error_id 파라미터가 필요합니다. 예: \"오류 #42 무시해줘\""
    try:
        from JARVIS07_GUARDIAN.guardian_agent import mark_ignored
        mark_ignored(int(error_id))
        return f"✅ 오류 #{error_id} 무시 처리 완료"
    except Exception as e:
        return f"⚠️ 무시 처리 실패: {e}"


def _collect_status() -> str:
    """collect.status — JARVIS09 수집 현황."""
    try:
        from shared import db as _db
        stats = _db.get_collection_stats()
        return (
            f"📦 *JARVIS09 COLLECTOR 현황*\n"
            f"• 누적 수집 레코드: {stats['total']}건\n"
            f"• 오늘 수집: {stats['today']}건\n"
            f"• 프로바이더: blog·news·academic·finance·web"
        )
    except Exception as e:
        return f"⚠️ COLLECTOR 현황 조회 실패: {e}"


def _collect_history(params: dict) -> str:
    """collect.history — 테마별 최근 수집 이력."""
    p = _safe_params(params)
    theme = (p.get("theme") or "").strip()
    try:
        from shared import db as _db
        rows = _db.get_collection_results(theme or "%", limit=10) if theme else []
        if not rows:
            return f"📦 테마 '{theme}' 수집 이력 없음" if theme else "📦 테마를 지정해주세요"
        lines = [f"📦 *{theme}* 수집 이력 (최근 {len(rows)}건)"]
        for r in rows[:5]:
            lines.append(f"  • [{r['source_type']}] {r['title'][:40]} ({r['collected_at'][:10]})")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 수집 이력 조회 실패: {e}"


__all__ = [
    "SAFE_INTENTS", "APPROVAL_INTENTS",
    "get_dispatch_mode", "describe_approval",
    "build_j01_command", "execute_safe",
    "execute_schedule_change",
]
