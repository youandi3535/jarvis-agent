"""JARVIS07_GUARDIAN/guardian_agent.py — 자동 오류 처리 에이전트 메인.

register(scheduler, bus) — 데몬 부팅 시 자동 호출.

담당:
  - 전역 예외 훅 등록
  - APScheduler 잡 실패 리스너 등록
  - ERROR_DETECTED 이벤트 구독 → 자동 수집·분석·수정 오케스트레이터
  - job_scan_logs: 5분 간격 로그 파일 스캔
  - job_archive_errors: 격주 월요일 04:30 오래된 오류 아카이브

★ 자동 승인 — Telegram 인라인 버튼 없음. 검증 통과 시 즉시 적용.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger("jarvis.guardian")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 오케스트레이터 동시 실행 방지 락 (같은 오류 중복 처리 차단)
_fix_lock = threading.Lock()
# 처리 중인 error_id 집합 (중복 수정 방지)
_processing: set[int] = set()

# ── 아키텍처 설정 — 단일 진실 소스 (architecture.py) ────────────
#    티어·안전장치 값 변경은 architecture.py 한 곳만. 여기는 import 만.
from JARVIS07_GUARDIAN.architecture import (
    CB_MAX_HOUR as _CB_MAX_HOUR,
    ESCALATE_THRESHOLD as _ESCALATE_THRESHOLD,
    ESCALATE_WINDOW_SECS as _ESCALATE_WINDOW_SECS,
    DENY_FIX_PATHS as _DENY_FIX_PATHS,
)

# ── Circuit breaker 런타임 상태 (설정값은 architecture.CB_MAX_HOUR) ─
_CB_LOCK      = threading.Lock()
_cb_count     = 0
_cb_hour_ts   = 0.0


# ── capability 선언 + 텔레그램 /status 섹션 ─────────────────────

def _status_section() -> str:
    """텔레그램 /status + 웹 대시보드용 GUARDIAN 상태 요약."""
    lines = ["🛡️ *JARVIS07 — GUARDIAN*"]
    try:
        from shared import db as _db
        stats = _db.get_error_stats(days=7)
        total   = stats.get("total", 0)
        new_    = stats.get("by_status", {}).get("new", 0)
        fixed   = stats.get("by_status", {}).get("fixed", 0)
        wontfix = stats.get("by_status", {}).get("wontfix", 0)
        manual  = stats.get("by_status", {}).get("manual", 0)
        ignored = stats.get("by_status", {}).get("ignored", 0)
        lines.append(f"📊 최근 7일: 총 {total}건 (신규 {new_} · 자동수정 {fixed} · 수정불가 {wontfix} · 수동수정 {manual} · 무시됨 {ignored})")

        # 처리 중 오류 수
        if _processing:
            lines.append(f"⚙️ 현재 분석·수정 중: {len(_processing)}건")

        # 심각도별 분포
        by_sev = stats.get("by_severity", {})
        crit = by_sev.get("critical", 0)
        high = by_sev.get("high", 0)
        med  = by_sev.get("medium", 0)
        low  = by_sev.get("low", 0)
        from JARVIS07_GUARDIAN.architecture import tier_flow_for as _flow
        if crit:
            lines.append(f"🔴 CRITICAL {crit}건 — {_flow('critical')} · 수동 검토 필요")
        if high:
            lines.append(f"🟠 HIGH {high}건 — {_flow('high')} 자동 수정 중")
        if not crit and not high:
            lines.append("✅ 긴급 오류 없음")
        lines.append(f"🟡 MEDIUM {med}건 · ⚪ LOW {low}건")

        # Tier 1 Contextual Bandit 학습 상태 (실가동 RL — bandit.py)
        try:
            from JARVIS07_GUARDIAN.bandit import stats as _bandit_stats
            _bs = _bandit_stats()
            _arms = _bs.get("arm_count", 0)
            if _arms:
                lines.append(
                    f"🎰 Tier 1 Contextual Bandit — fixer {_arms}종 학습 "
                    f"(Linear UCB · {_bs.get('feature_dim', 0)}차원)"
                )
        except Exception:
            pass

        # ★ 글 품질 강화학습 상태 (ADR 014 — quality_learner.py)
        try:
            from JARVIS07_GUARDIAN.quality_learner import stats as _ql_stats
            _qs = _ql_stats()
            if _qs.get("active") or _qs.get("total_usage"):
                _avg = _qs.get("avg_reward")
                lines.append(
                    f"✍️ 글 품질 RL — 활성 지침 {_qs.get('active', 0)}개 · "
                    f"주입 {_qs.get('total_usage', 0)}회 · 검증 {_qs.get('total_rewards', 0)}회"
                    + (f" · 평균 보상 {_avg}" if _avg is not None else "")
                )
        except Exception:
            pass

        # 자동수정 정책 요약 — 단일 진실 소스(architecture.telegram_summary)
        from JARVIS07_GUARDIAN.architecture import telegram_summary as _arch_summary
        lines.append(_arch_summary())

        # Circuit breaker 현재 사용량
        import time as _t
        with _CB_LOCK:
            _age = _t.time() - _cb_hour_ts
            _remaining = max(0, _CB_MAX_HOUR - _cb_count)
        if _cb_count > 0:
            lines.append(f"⚡ Circuit breaker: 이번 시간 {_cb_count}/{_CB_MAX_HOUR}건 사용 (남은 {_remaining}건)")

        # 로그 스캔 다음 실행
        try:
            from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
            from datetime import datetime as _dt
            apscheduler = get_apscheduler()
            job = apscheduler.get_job("guardian_log_scan") if apscheduler else None
            if job and job.next_run_time:
                now = _dt.now()
                nrt = job.next_run_time.astimezone(now.astimezone().tzinfo)
                diff = int((nrt - now.astimezone()).total_seconds())
                lines.append(f"🔍 로그 스캔: {diff // 60}분 {diff % 60}초 후")
        except Exception:
            pass

    except Exception as e:
        lines.append(f"⚠️ 상태 조회 실패: {e}")
    return "\n".join(lines)


def _register_capability():
    """capability 레지스트리에 jarvis07_guardian 등록."""
    try:
        from shared.capabilities import declare
        declare(
            agent_id="jarvis07_guardian",
            domain="guardian",
            intents=[
                "error.list",       # 오류 목록 조회
                "error.stats",      # 오류 통계
                "error.ignore",     # 오류 무시 처리
            ],
            tools=[],
            requires_approval=[],
            cost_class="low",
            description="자동 오류 수집·분석·수정 에이전트. 전역 예외훅 + APScheduler 리스너 + 로그 스캔.",
            tags=["guardian", "error", "monitor", "auto-fix"],
            help_section=(
                "🛡️ *오류 관리 (JARVIS07)*\n"
                "/errors          최근 오류 목록\n"
                "/errors_stats    7일 오류 통계\n"
                "자유 문장: \"최근 오류 보여줘\""
            ),
            status_fn=_status_section,
        )
    except Exception as e:
        log.warning(f"[GUARDIAN] capability 등록 실패: {e}")


# ── 안전장치 헬퍼 ────────────────────────────────────────────────

def _circuit_breaker_ok() -> bool:
    """시간당 자동수정 횟수 초과 시 False — 더 이상 수정 안 함."""
    import time
    global _cb_count, _cb_hour_ts
    with _CB_LOCK:
        now = time.time()
        if now - _cb_hour_ts >= 3600:
            _cb_count = 0
            _cb_hour_ts = now
        if _cb_count >= _CB_MAX_HOUR:
            return False
        _cb_count += 1
        return True


def _is_deny_path(module: str) -> bool:
    """절대 자동수정 금지 파일인지 확인."""
    if not module:
        return False
    name = Path(module).name
    return name in _DENY_FIX_PATHS or ".env" in module


def _escalate_severity(error_record: dict) -> str:
    """1시간 내 동일 오류 N회 반복 시 severity 한 단계 자동 상향.

    low  → medium (3회+)
    medium → high (3회+)
    high / critical → 유지
    """
    base_sev   = error_record.get("severity", "medium")
    error_type = error_record.get("error_type", "")
    source     = error_record.get("source", "")
    message    = (error_record.get("message") or "")[:40]

    if base_sev in ("high", "critical"):
        return base_sev

    try:
        from shared.db import get_db
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(seconds=_ESCALATE_WINDOW_SECS)).isoformat()
        with get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*) FROM error_log
                   WHERE error_type = ? AND source = ?
                     AND SUBSTR(message, 1, 40) = ?
                     AND created_at >= ?""",
                (error_type, source, message, since),
            ).fetchone()
        count = row[0] if row else 0
        if count >= _ESCALATE_THRESHOLD:
            _NEXT = {"low": "medium", "medium": "high"}
            new_sev = _NEXT.get(base_sev, base_sev)
            if new_sev != base_sev:
                log.warning(
                    f"[GUARDIAN] 빈도 상향: {base_sev}→{new_sev} "
                    f"({count}회/{_ESCALATE_WINDOW_SECS//60}분) — {error_type}"
                )
            return new_sev
    except Exception:
        pass
    return base_sev


def _notify_all(error_record: dict, result: str, tier: int = 0, severity: str = ""):
    """모든 심각도에 텔레그램 알림 — 수정 성공·실패·불가 공통."""
    sev = severity or error_record.get("severity", "medium")
    etype   = error_record.get("error_type", "?")
    source  = error_record.get("source", "?")
    module  = error_record.get("module", "?")
    msg     = (error_record.get("message") or "")[:120]

    _ICONS = {
        "success": "✅", "failed": "❌", "critical_manual": "🔴",
        "circuit_open": "⚡", "deny_path": "🔒",
    }
    _SEV_TAG = {"low": "⚪LOW", "medium": "🟡MED", "high": "🟠HIGH", "critical": "🔴CRIT"}
    icon     = _ICONS.get(result, "ℹ️")
    sev_tag  = _SEV_TAG.get(sev, sev.upper())

    if result == "success":
        text = (
            f"{icon} *[GUARDIAN] 자동수정 완료*\n"
            f"심각도: {sev_tag}  Tier {tier}\n"
            f"소스: {source} / {module}\n"
            f"유형: {etype}\n"
            f"내용: {msg}"
        )
    elif result == "critical_manual":
        text = (
            f"{icon} *[GUARDIAN] CRITICAL — 수동 검토 필요*\n"
            f"Tier 1 패턴 없음 → LLM 수정 생략 (안전)\n"
            f"소스: {source} / {module}\n"
            f"유형: {etype}\n"
            f"내용: {msg}"
        )
    elif result == "circuit_open":
        text = (
            f"{icon} *[GUARDIAN] Circuit Breaker 발동*\n"
            f"시간당 {_CB_MAX_HOUR}건 한도 초과 → 수정 일시 중단\n"
            f"오류: {etype} @ {module}"
        )
    elif result == "deny_path":
        text = (
            f"{icon} *[GUARDIAN] 보안 파일 수정 차단*\n"
            f"자동수정 금지 파일: {module}\n"
            f"유형: {etype}\n"
            f"→ 수동 검토 필요"
        )
    else:  # failed
        text = (
            f"{icon} *[GUARDIAN] 자동수정 실패 — 수동 검토*\n"
            f"심각도: {sev_tag}  Tier 1·2 모두 실패\n"
            f"소스: {source} / {module}\n"
            f"유형: {etype}\n"
            f"내용: {msg}"
        )
    try:
        from shared.notify import send_tg
        send_tg(text)
    except Exception:
        pass


# ── 오케스트레이터 ────────────────────────────────────────────────

def _retry_original_job(error_record: dict) -> None:
    """수정 완료 후 원래 실패했던 잡 재시도 (모듈 reload → APScheduler run_job)."""
    source = error_record.get("source", "")
    module = error_record.get("module", "")

    # 모듈 reload 시도 (Python import 캐시 갱신)
    if module:
        try:
            import importlib
            mod_name = module.replace("/", ".").replace(".py", "")
            if mod_name in sys.modules:
                importlib.reload(sys.modules[mod_name])
                log.info(f"[GUARDIAN] 모듈 reload 성공: {mod_name}")
        except Exception as e:
            log.debug(f"[GUARDIAN] 모듈 reload 실패 (무시): {e}")

    # source → APScheduler job_id 매핑
    _SOURCE_JOB_MAP = {
        "writer": "j01_economic_post",
        "radar":  "j02_radar_collect",
        "infra":  None,
        "master": None,
    }
    job_id = _SOURCE_JOB_MAP.get(source)
    if not job_id:
        return

    try:
        from JARVIS04_SCHEDULER.job_catalog import get_apscheduler
        sched = get_apscheduler()
        if sched:
            sched.run_job(job_id)
            log.info(f"[GUARDIAN] 원래 잡 재시도 트리거: {job_id}")
            try:
                from shared.notify import send_tg
                send_tg(f"🔄 *[GUARDIAN] 작업 재시도*\n수정 완료 후 {job_id} 재시작했습니다.")
            except Exception:
                pass
    except Exception as e:
        log.debug(f"[GUARDIAN] 잡 재시도 실패: {e} — 다음 스케줄에 자동 실행됩니다.")


def _try_sdk_targeted_fix(error_id: int, error_record: dict) -> bool:
    """2순위 — Claude Code SDK targeted repair.

    자체 학습(1순위) 실패 시 호출.
    성공: status='fixed' + 학습 저장(auto_repair 내부) + 원래 잡 재시도.
    실패: status='wontfix' + TG 알림.
    """
    try:
        from JARVIS07_GUARDIAN.auto_repair import run_auto_repair_targeted
        from shared import db as _db

        error_text = (
            f"error_type: {error_record.get('error_type','?')}\n"
            f"source: {error_record.get('source','?')}\n"
            f"module: {error_record.get('module','?')}\n"
            f"func_name: {error_record.get('func_name','?')}\n"
            f"message: {error_record.get('message','?')}\n"
            f"severity: {error_record.get('severity','?')}\n"
            f"traceback:\n{(error_record.get('traceback', ''))[:2000]}"
        )

        log.info(f"[GUARDIAN] #{error_id} 2순위 Claude Code SDK 수정 시작 (최대 10분)")
        fixed = run_auto_repair_targeted(
            context=error_text,
            job_id=error_record.get("source", "unknown"),
            failed_platforms=[error_record.get("module", error_record.get("source", "unknown"))],
            error_record=error_record,   # ★ 밴딧 학습 브리지 — SDK 수정 → fingerprint llm_patch + 밴딧 보상
        )

        if fixed:
            _db.mark_error_status(error_id, "fixed")
            log.info(f"[GUARDIAN] #{error_id} SDK 수정 성공 → 학습 저장 완료, 작업 재시도 중")
            # 학습 저장: ① _record_repairs_to_guardian(external_change) ② record_sdk_fix(밴딧 보상 + llm_patch) — 둘 다 run_auto_repair_targeted 내부 자동
            _retry_original_job(error_record)
            return True
        else:
            _db.mark_error_status(error_id, "wontfix")
            log.warning(f"[GUARDIAN] #{error_id} SDK 수정 실패 → status=wontfix")
            # 알림은 _orchestrate()의 _notify_all()에서 통합 처리
            return False

    except Exception as e:
        log.warning(f"[GUARDIAN] SDK targeted 수정 예외: {e}")
        try:
            from shared import db as _db
            _db.mark_error_status(error_id, "wontfix")
        except Exception:
            pass
        return False


def _orchestrate(error_id: int):
    """오류 분석 → 자동 수정 오케스트레이터 (별도 스레드에서 실행).

    ★ 티어 정의는 architecture.py 단일 진실 소스. catch()→Tier 1(패턴·Bandit)→Tier 2(LLM).

    심각도별 처리 매트릭스:
      low      → Tier 1 → Tier 2 → 알림  (학습 → 다음엔 Tier 1 해결)
      medium   → Tier 1 → Tier 2 → 알림
      high     → Tier 1 → Tier 2 → 알림 (항상)
      critical → Tier 1 → 알림 (LLM 수정은 너무 위험 — 사람 검토)

    안전장치 (값은 architecture.py):
      · 빈도 기반 severity 자동 상향 (1시간 N회 반복 → 한 단계 상향)
      · Circuit breaker (시간당 최대 N건 자동수정)
      · 보안 파일 수정 절대 금지 (.env, 인증 파일, 데몬 코어)
      · 모든 심각도 수정 결과 텔레그램 알림
    """
    with _fix_lock:
        if error_id in _processing:
            return
        _processing.add(error_id)

    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix

        error_record = _db.get_error(error_id)
        if not error_record:
            return

        error_type = error_record.get("error_type", "")
        module     = error_record.get("module", "")

        # ── 안전장치 0: 일시적·외부·제어흐름 오류 → ignored (코드 버그 아님) ──
        #    ★ ERRORS [286] — 네트워크·Selenium 환경·외부 API 할당량·정상 제어흐름(테마 교체)·
        #    외부 발행(Layer 4)·Claude CLI 운영 오류는 wontfix 가 아니라 ignored.
        #    수동검토 큐 오염·알림 폭주 방지. 자동수정 파이프라인 진입 안 함.
        from JARVIS07_GUARDIAN.severity import is_transient
        if is_transient(error_type, error_record.get("message", ""), error_record.get("source", "")):
            log.info(f"[GUARDIAN] #{error_id} 일시적/외부/제어흐름 오류 — ignored (자동수정 비대상): {error_type}")
            _db.mark_error_status(error_id, "ignored")
            return

        # ── 안전장치 1: 보안 파일 수정 금지 ───────────────────────
        if _is_deny_path(module):
            log.warning(f"[GUARDIAN] #{error_id} 보안 파일 수정 차단 — {module}")
            _notify_all(error_record, "deny_path")
            _db.mark_error_status(error_id, "wontfix")
            return

        # ── 안전장치 2: Circuit breaker ───────────────────────────
        if not _circuit_breaker_ok():
            log.warning(f"[GUARDIAN] #{error_id} Circuit breaker 발동 — 시간당 한도 초과")
            _notify_all(error_record, "circuit_open")
            _db.mark_error_status(error_id, "new")  # 다음 retry_pending 에서 재처리
            return

        # ── 빈도 기반 severity 자동 상향 ─────────────────────────
        severity = _escalate_severity(error_record)
        error_record = {**error_record, "severity": severity}  # 상향된 값으로 갱신

        log.info(f"[GUARDIAN] 오케스트레이터 시작 — #{error_id} [{severity}] {error_type}")
        _db.mark_error_status(error_id, "analyzing")

        # ── Tier 1: 패턴 수정 — 모든 심각도 시도 (Bandit, LLM 없음, 안전) ─
        #    (Bandit 보상은 pattern_fixer/error_fixer 내부에서 자동 기록)
        analysis = analyze(error_record)
        success  = apply_fix(error_id, analysis, mark_wontfix=False)

        if success:
            log.info(f"[GUARDIAN] #{error_id} ✅ Tier 1 수정 완료")
            _notify_all(error_record, "success", tier=1, severity=severity)
            _retry_original_job(error_record)
            return

        # ── critical: Tier 2(LLM) 생략 — 패턴 없으면 사람에게 ───
        if severity == "critical":
            log.warning(f"[GUARDIAN] #{error_id} critical + Tier 1 실패 → 수동 검토")
            _notify_all(error_record, "critical_manual", severity=severity)
            _db.mark_error_status(error_id, "wontfix")
            return

        # ── Tier 2: LLM 수정 — low 포함 전 심각도 ────────────────
        # low도 Tier 2까지 진행 → 학습 데이터 축적 → 다음엔 Tier 1 해결
        log.info(f"[GUARDIAN] #{error_id} Tier 1 실패 → Tier 2 (LLM, {severity})")
        fixed = _try_sdk_targeted_fix(error_id, error_record)

        if fixed:
            _notify_all(error_record, "success", tier=2, severity=severity)
        else:
            _notify_all(error_record, "failed", severity=severity)

    except Exception as e:
        log.error(f"[GUARDIAN] 오케스트레이터 오류: {e}")
    finally:
        with _fix_lock:
            _processing.discard(error_id)


def _on_error_detected(payload: dict, source: str):
    """ERROR_DETECTED 이벤트 핸들러."""
    error_id = payload.get("error_id")
    if not error_id:
        return
    # 별도 스레드에서 처리 (이벤트 루프 블로킹 방지)
    t = threading.Thread(
        target=_orchestrate, args=(error_id,),
        name=f"guardian_fix_{error_id}", daemon=True,
    )
    t.start()


# ── 자체수리 sweep · 심층 감사 backlog (★ 사용자 박제 2026-06-28) ──────
#
#  발행 전(LLM-0 Tier-1 sweep) vs 새벽(LLM Tier-2 backlog + 광범위 감사) 분리.
#  학습 자산(learned_patterns·Bandit)이 비대해질수록 미해결 오류 소급 자동수리율↑.
#  대상 status: 'new'(미처리) + 'wontfix'(과거 실패 — 패턴 성장 시 재수리 기회).

def _collect_unresolved(limit: int) -> list:
    """미해결 오류 수집 — status 'new' + 'wontfix' 병합·dedup (최신순)."""
    try:
        from shared import db as _db
    except Exception:
        return []
    seen: set = set()
    out: list = []
    for st in ("new", "wontfix"):
        try:
            for r in _db.list_errors(status=st, limit=limit):
                i = r.get("id")
                if i in seen:
                    continue
                seen.add(i)
                out.append(r)
        except Exception as e:
            log.debug(f"[GUARDIAN/unresolved] {st} 조회 실패: {e}")
    return out


def self_heal_known_errors(limit: int = 40) -> dict:
    """발행 전 Tier-1 자체수리 sweep — LLM 호출 0.

    미해결 오류(new·wontfix) 중 *학습 패턴·정적 fixer·Bandit 로 즉시 고칠 수 있는 것만* 수리.
    Tier 2(LLM) 절대 호출 안 함 — 못 고치면 그대로 남겨 새벽 심층 감사(job_deep_audit)로 위임.
    apply_fix 성공 시 *실제 오류 지문* 으로 record_pattern_hit + Bandit 양의 보상 자동 기록.

    Returns: {"fixed", "skipped", "ignored", "scanned"}
    """
    fixed = skipped = ignored = 0
    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix
        from JARVIS07_GUARDIAN.severity import is_transient
    except Exception as e:
        log.warning(f"[GUARDIAN/selfheal] import 실패: {e}")
        return {"fixed": 0, "skipped": 0, "ignored": 0, "scanned": 0}

    rows = _collect_unresolved(limit)
    for er in rows:
        eid = er.get("id")
        et  = er.get("error_type", "")
        try:
            if is_transient(et, er.get("message", ""), er.get("source", "")):
                _db.mark_error_status(eid, "ignored")
                ignored += 1
                continue
            analysis = analyze(er)  # Tier 1 전용 (패턴·Bandit·정적, LLM 0)
            if analysis.get("fixable") and apply_fix(eid, analysis, mark_wontfix=False):
                fixed += 1
            else:
                skipped += 1  # LLM 호출 안 함 — 새벽 심층 감사로 위임
        except Exception as e:
            log.debug(f"[GUARDIAN/selfheal] #{eid} 처리 예외: {e}")
            skipped += 1

    log.info(f"[GUARDIAN/selfheal] 발행 전 Tier-1 sweep — 수리 {fixed} / 보류 {skipped} / 무시 {ignored} (스캔 {len(rows)})")
    return {"fixed": fixed, "skipped": skipped, "ignored": ignored, "scanned": len(rows)}


def deep_audit_backlog(limit: int = 40, max_llm: int = 15) -> dict:
    """새벽 심층 감사 1부 — 미해결 오류 backlog 를 Tier 1 → Tier 2(LLM) 로 처리.

    ★ 핵심: Tier 2 수정도 apply_fix 경유 → *실제 오류 지문* 으로 record_pattern_hit + Bandit.
       (_try_sdk_targeted_fix 의 AutoRepairFix 합성 지문과 달리, 다음 발행 전 sweep 이 재사용 가능
        → 학습 루프가 실제로 조여짐.)
    max_llm: 1회 실행당 Tier 2(LLM) 시도 상한 (시간 폭주 방지).

    Returns: {"fixed_t1", "fixed_t2", "failed", "ignored", "scanned", "llm_used"}
    """
    fixed_t1 = fixed_t2 = failed = ignored = llm_used = 0
    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.error_analyzer import analyze, analyze_llm_only
        from JARVIS07_GUARDIAN.error_fixer import apply_fix
        from JARVIS07_GUARDIAN.severity import is_transient
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] import 실패: {e}")
        return {"fixed_t1": 0, "fixed_t2": 0, "failed": 0, "ignored": 0, "scanned": 0, "llm_used": 0}

    rows = _collect_unresolved(limit)
    for er in rows:
        eid = er.get("id")
        et  = er.get("error_type", "")
        try:
            if is_transient(et, er.get("message", ""), er.get("source", "")):
                _db.mark_error_status(eid, "ignored")
                ignored += 1
                continue
            a1 = analyze(er)  # Tier 1 먼저 (LLM 0)
            if a1.get("fixable") and apply_fix(eid, a1, mark_wontfix=False):
                fixed_t1 += 1
                continue
            if llm_used >= max_llm:
                failed += 1
                continue
            llm_used += 1
            a2 = analyze_llm_only(er)  # Tier 2 — apply_fix 경유 *실제 지문* 학습
            if a2.get("fixable") and apply_fix(eid, a2, mark_wontfix=True):
                fixed_t2 += 1
            else:
                failed += 1
        except Exception as e:
            log.debug(f"[GUARDIAN/deepaudit] #{eid} 처리 예외: {e}")
            failed += 1

    log.info(f"[GUARDIAN/deepaudit] backlog — T1 {fixed_t1} / T2(LLM {llm_used}) {fixed_t2} / 실패 {failed} / 무시 {ignored} (스캔 {len(rows)})")
    return {"fixed_t1": fixed_t1, "fixed_t2": fixed_t2, "failed": failed,
            "ignored": ignored, "scanned": len(rows), "llm_used": llm_used}


def job_deep_audit() -> None:
    """매일 04:30 — 심층 코드 감사 (DB 백업 03:00 이후, 발행과 분리).

    2부 구성:
      1) backlog 처리 (deep_audit_backlog) — 미해결 오류 Tier 1 → Tier 2(LLM), *실제 지문* 학습
      2) 광범위 코드 감사 (auto_repair.run_auto_repair) — 새 잠재 버그 발굴·수정

    ★ 발행 직전엔 LLM-0 Tier-1 sweep(self_heal_known_errors)만, 비싼 LLM 심층 감사는 한가한 새벽에.
      결과가 learned_patterns·Bandit 을 키워 다음 발행 전 sweep 자동수리율↑ (복리 학습 루프).
    """
    log.info("[GUARDIAN/deepaudit] 새벽 심층 감사 시작")
    try:
        b = deep_audit_backlog()
        log.info(f"[GUARDIAN/deepaudit] backlog 완료: {b}")
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] backlog 처리 예외: {e}")
    try:
        from JARVIS07_GUARDIAN.auto_repair import run_auto_repair
        run_auto_repair()
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] 광범위 감사 예외: {e}")


# ── 스케줄 잡 ─────────────────────────────────────────────────────

def job_scan_logs():
    """5분 간격 — 모든 등록 로그 디렉토리 오류 스캔."""
    try:
        from JARVIS07_GUARDIAN.error_collector import scan_all_logs
        scan_all_logs()
    except Exception as e:
        log.warning(f"[GUARDIAN] 로그 스캔 잡 오류: {e}")


def job_git_audit():
    """매일 03:30 — git log --since=24h 분석 → 외부 변경 자동 박제.

    대상: VS Code Claude Code·사용자 직접 편집·외부 도구 등 *jarvis 외부* 코드 변경.
    daemon 의 report_manual_fix 가 호출되지 않은 변경을 회고적으로 학습 자산화.

    절차:
      1. git log --since="24 hours ago" --name-only --pretty=format:"%H|%ai|%s"
      2. 각 커밋의 변경 파일 + 메시지 → record_external_change 호출
      3. 학습 시스템(learned_patterns) 자동 갱신
    """
    import subprocess
    from pathlib import Path
    from JARVIS07_GUARDIAN.error_collector import record_external_change

    root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "log", "--since=24 hours ago",
             "--name-only", "--pretty=format:===%H|%ai|%s===", "--no-merges"],
            cwd=str(root), capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.info(f"[GUARDIAN/git_audit] git log 실패 (returncode={result.returncode}) — skip")
            return
    except FileNotFoundError:
        log.info("[GUARDIAN/git_audit] git CLI 없음 — skip")
        return
    except Exception as e:
        log.warning(f"[GUARDIAN/git_audit] git log 예외: {e}")
        return

    output = result.stdout or ""
    if not output.strip():
        log.info("[GUARDIAN/git_audit] 최근 24시간 신규 커밋 없음")
        return

    # 커밋 블록 파싱: ===HASH|DATE|MESSAGE===\nfile1\nfile2\n
    commits = re.split(r'===([^=|]+)\|([^=|]+)\|([^=]*)===\n', output)
    # split 결과: ['', hash1, date1, msg1, files_text1, hash2, ...]
    ok = 0
    seen_files = set()
    for i in range(1, len(commits), 4):
        try:
            commit_hash = commits[i].strip()
            commit_date = commits[i + 1].strip()
            commit_msg  = commits[i + 2].strip()
            files_text  = commits[i + 3] if i + 3 < len(commits) else ""
        except IndexError:
            continue

        files = [f.strip() for f in files_text.splitlines() if f.strip()]
        # *.py / *.md / *.json 만 박제 (의미 있는 변경)
        files = [f for f in files
                 if any(f.endswith(ext) for ext in (".py", ".md", ".json", ".yml", ".yaml"))
                 and "__pycache__" not in f
                 and ".venv" not in f]

        for f in files:
            # 같은 파일 24시간 내 중복 박제 회피
            key = (f, commit_hash)
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                eid = record_external_change(
                    source="git_audit",
                    fixed_file=f,
                    description=f"{commit_msg[:200]} ({commit_date[:10]})",
                    error_type="GitCommit",
                    severity="low",
                    actor="external_user",
                    commit_hash=commit_hash,
                )
                if eid:
                    ok += 1
            except Exception as e:
                log.debug(f"[GUARDIAN/git_audit] 박제 실패 ({f}): {e}")

    if ok:
        log.info(f"[GUARDIAN/git_audit] 외부 변경 박제 완료 — {ok}건")


# 모듈 레벨 re import (job_git_audit 내부 사용)
import re


def job_retry_pending(*, max_per_run: int = 20, stuck_minutes: int = 30):
    """★ 사용자 박제 2026-05-15 — 10분 간격: status='new' / 'analyzing' 항목 자동 재처리.

    동작:
      1. status='new' 항목 → _orchestrate() 큐에 재투입 (분석·자동수정 시도)
      2. status='analyzing' 항목이 stuck_minutes 분 이상 묶여있으면 → 'new' 로 리셋 후 재시도
      3. 한 번에 최대 max_per_run 건만 처리 (rate-limit)

    이유: GUARDIAN 은 *오류 발생 이벤트* 시점에만 _orchestrate 호출.
    데몬 재시작 / 분석 도중 크래시 / critical 후 사용자 검토 대기 등으로
    *new / analyzing* 상태로 잔류한 항목이 *자동으로 재처리 안 됨*.
    이 잡이 *주기적 sweep* 으로 누락 항목 자동 해소.

    UI 효과: 대시보드 '신규' + '분석 중' 카운트 자동 감소 → '자동수정' / 'wontfix' 로 이동.
    """
    try:
        from shared import db as _db
        from datetime import datetime, timedelta
    except Exception as e:
        log.warning(f"[GUARDIAN/retry_pending] import 실패: {e}")
        return

    # 1) 멈춘 analyzing → new 리셋 (분석 도중 크래시·timeout 케이스)
    reset_n = 0
    try:
        stuck_rows = _db.list_errors(status="analyzing", limit=max_per_run)
        threshold = datetime.now() - timedelta(minutes=stuck_minutes)
        for r in stuck_rows:
            ts = r.get("created_at") or r.get("detected_at") or ""
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").split("+")[0])
            except Exception:
                continue
            if dt < threshold:
                _db.mark_error_status(int(r["id"]), "new")
                reset_n += 1
    except Exception as e:
        log.debug(f"[GUARDIAN/retry_pending] analyzing 리셋 예외: {e}")

    # 2) new → _orchestrate 재투입
    retry_n = 0
    try:
        new_rows = _db.list_errors(status="new", limit=max_per_run)
        for r in new_rows:
            eid = int(r["id"])
            sev = (r.get("severity") or "").lower()
            # critical 은 사용자 검토 대기 — skip
            if sev == "critical":
                continue
            with _fix_lock:
                if eid in _processing:
                    continue
            t = threading.Thread(
                target=_orchestrate, args=(eid,),
                name=f"guardian_retry_{eid}", daemon=True,
            )
            t.start()
            retry_n += 1
    except Exception as e:
        log.warning(f"[GUARDIAN/retry_pending] new 재투입 예외: {e}")

    if reset_n or retry_n:
        log.info(f"[GUARDIAN/retry_pending] analyzing→new 리셋 {reset_n}건 / new 재처리 큐 {retry_n}건")


def job_archive_errors():
    """격주 월요일 04:30 — 30일 초과 해결 오류 아카이브."""
    try:
        from shared import db as _db
        deleted = _db.archive_old_errors(days=30)
        if deleted > 0:
            log.info(f"[GUARDIAN] 오래된 오류 {deleted}건 아카이브 완료")
    except Exception as e:
        log.warning(f"[GUARDIAN] 아카이브 잡 오류: {e}")


# ── 텔레그램 알림 헬퍼 (비활성 — 사용자 박제) ──────────────────

def _notify_critical(error_record: dict):
    log.warning(
        f"[GUARDIAN] CRITICAL — {error_record.get('error_type','')} "
        f"#{error_record.get('id','')} {(error_record.get('message',''))[:100]}"
    )
    try:
        from shared.notify import send_tg
        send_tg(
            f"🔴 *[GUARDIAN] CRITICAL 오류 발생*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"소스: {error_record.get('source','?')}\n"
            f"유형: {error_record.get('error_type','?')}\n"
            f"내용: {(error_record.get('message',''))[:200]}\n"
            f"→ 자동 수정 불가 — 수동 검토 필요"
        )
    except Exception:
        pass


def _notify_medium(error_record: dict):
    sev = error_record.get("severity", "medium")
    log.info(
        f"[GUARDIAN] {sev.upper()} — {error_record.get('error_type','')} "
        f"{(error_record.get('message',''))[:100]}"
    )
    if sev in ("high",):
        try:
            from shared.notify import send_tg
            send_tg(
                f"🟠 *[GUARDIAN] HIGH 오류 자동수정 불가*\n"
                f"소스: {error_record.get('source','?')}\n"
                f"유형: {error_record.get('error_type','?')}\n"
                f"내용: {(error_record.get('message',''))[:150]}"
            )
        except Exception:
            pass


# ── 공개 도구 API ─────────────────────────────────────────────────

def list_errors(status: str = "new", limit: int = 20) -> list:
    from shared import db as _db
    return _db.list_errors(status=status, limit=limit)


def get_stats(days: int = 7) -> dict:
    from shared import db as _db
    return _db.get_error_stats(days=days)


def mark_ignored(error_id: int) -> bool:
    from shared import db as _db
    _db.mark_error_status(error_id, "ignored")
    return True


# ── register() — 데몬 진입점 ────────────────────────────────────

def register(scheduler, bus):
    """데몬 부팅 시 자동 호출.

    1) 전역 예외 훅 등록
    2) APScheduler 잡 실패 리스너 등록
    3) ERROR_DETECTED 이벤트 구독
    4) 로그 스캐너 초기화
    5) 스케줄 잡 2개 등록 (DEFAULT_JOBS 위임 예정 — 현재 직접 등록)
    """
    log.info("[GUARDIAN] 등록 시작...")

    # 0) capability 등록 (텔레그램 /status + 웹 대시보드 자동 포함)
    _register_capability()

    # 1) 전역 예외 훅
    try:
        from JARVIS07_GUARDIAN.error_collector import register_global_hook
        register_global_hook()
    except Exception as e:
        log.warning(f"[GUARDIAN] 전역 훅 등록 실패: {e}")

    # 2) APScheduler 잡 실패 리스너 — JARVIS04.job_history.attach_listeners 에서 통합 부착
    #    (apscheduler import 단일 진입점 규정 — JARVIS04_SCHEDULER 외 add_listener 금지)

    # 3) ERROR_DETECTED 이벤트 구독
    try:
        bus.subscribe(bus.EventType.ERROR_DETECTED, _on_error_detected)
        log.info("[GUARDIAN] ERROR_DETECTED 이벤트 구독 완료")
    except Exception as e:
        log.warning(f"[GUARDIAN] 이벤트 구독 실패: {e}")

    # 4) 로그 스캐너 초기화
    try:
        from JARVIS07_GUARDIAN.error_collector import init_log_scanner
        init_log_scanner()
    except Exception as e:
        log.warning(f"[GUARDIAN] 로그 스캐너 초기화 실패: {e}")

    # 5) 스케줄 잡 등록 — JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 에서 관리 (이관 완료)
    # guardian_log_scan / guardian_archive / j07_git_audit / j07_retry_pending

    log.info("✅ [GUARDIAN] JARVIS07_GUARDIAN 등록 완료 — 자동 오류 수집·수정 활성화")
