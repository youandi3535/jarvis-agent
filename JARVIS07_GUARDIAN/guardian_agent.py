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

        # 심각도별 new 오류 (CRITICAL + HIGH 합침 → CRITICAL로 표시)
        by_sev = stats.get("by_severity", {})
        crit = by_sev.get("critical", 0)
        high = by_sev.get("high", 0)
        total_urgent = crit + high
        if total_urgent:
            lines.append(f"🔴 CRITICAL: {total_urgent}건")
        else:
            lines.append("✅ 긴급 오류 없음")

        # 스캔 잡 상태
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
        )

        if fixed:
            _db.mark_error_status(error_id, "fixed")
            log.info(f"[GUARDIAN] #{error_id} SDK 수정 성공 → 학습 저장 완료, 작업 재시도 중")
            # 학습 저장은 run_auto_repair_targeted → _record_repairs_to_guardian 에서 자동 처리
            _retry_original_job(error_record)
            return True
        else:
            _db.mark_error_status(error_id, "wontfix")
            log.warning(f"[GUARDIAN] #{error_id} SDK 수정 실패 → status=wontfix")
            try:
                from shared.notify import send_tg
                send_tg(
                    f"⚠️ *[GUARDIAN] 자동 수정 실패*\n"
                    f"자체 학습·Claude Code 모두 수정 불가\n"
                    f"오류: {error_record.get('error_type','?')} @ {error_record.get('module','?')}\n"
                    f"내용: {(error_record.get('message',''))[:150]}\n"
                    f"→ 수동 검토 필요"
                )
            except Exception:
                pass
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

    1순위: 자체 학습 (패턴형 3회+ 반복 + 저장된 학습 전부, LLM 호출 0)
    2순위: Claude Code SDK — 성공 시 학습 저장 + 원래 작업 재시도
    """
    with _fix_lock:
        if error_id in _processing:
            return
        _processing.add(error_id)

    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.severity import is_auto_fixable
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix

        error_record = _db.get_error(error_id)
        if not error_record:
            return

        severity   = error_record.get("severity", "medium")
        error_type = error_record.get("error_type", "")

        log.info(f"[GUARDIAN] 오케스트레이터 시작 — #{error_id} [{severity}] {error_type}")

        if severity == "critical":
            _notify_critical(error_record)
            return

        if not is_auto_fixable(severity, error_type):
            log.info(f"[GUARDIAN] #{error_id} 자동 수정 불가 — wontfix 마킹")
            _notify_medium(error_record)
            _db.mark_error_status(error_id, "wontfix")
            return

        if severity == "low":
            return

        _db.mark_error_status(error_id, "analyzing")

        # Tier 1·1.5: 자체 학습 (fingerprint) + RL 모델
        analysis = analyze(error_record)
        success = apply_fix(error_id, analysis, mark_wontfix=False)
        # RL 보상 — apply_fix 실제 결과(ast.parse + import 검증) 기반
        try:
            from JARVIS07_GUARDIAN.incident_responder import _send_rl_reward
            _send_rl_reward(error_record, analysis, success)
        except Exception:
            pass
        if success:
            log.info(f"[GUARDIAN] #{error_id} ✅ Tier1/1.5 수정 완료")
            _retry_original_job(error_record)
            return

        # 2순위: Claude Code SDK
        log.info(f"[GUARDIAN] #{error_id} 자체 학습 실패 → 2순위 Claude Code SDK")
        _try_sdk_targeted_fix(error_id, error_record)

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

    # 5) ★ RL 모델 부트스트랩 자동 호출 (사용자 박제 2026-06-07 — ERRORS [259])
    #    — 모델 파일 손상·플래그 삭제·새 venv 재구성 시 자동 복구.
    #    .rl_bootstrapped 플래그가 있으면 즉시 skip → 부팅 비용 0.
    try:
        from JARVIS07_GUARDIAN.rl_fixer import bootstrap_from_patterns
        n = bootstrap_from_patterns()
        if n > 0:
            log.info(f"[GUARDIAN] RL 부트스트랩 완료: {n}개 패턴 선학습")
    except ImportError:
        log.warning("[GUARDIAN] rl_fixer 미사용 (sklearn 미설치 추정) — Tier 1.5 비활성")
    except Exception as e:
        log.warning(f"[GUARDIAN] RL 부트스트랩 실패: {e}")

    # 6) ★ RL 모델 atexit 저장 hook (사용자 박제 2026-06-07)
    #    — 데몬 종료 시 (SIGTERM·SIGINT) 마지막 학습 데이터 손실 방지.
    try:
        import atexit
        from JARVIS07_GUARDIAN.rl_fixer import flush_model
        atexit.register(flush_model)
        log.info("[GUARDIAN] RL 모델 atexit 저장 hook 등록")
    except Exception as e:
        log.debug(f"[GUARDIAN] RL atexit hook 등록 실패 (정상 — rl_fixer 미사용 시): {e}")

    # 7) 스케줄 잡 등록 — JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 에서 관리 (이관 완료)
    # guardian_log_scan / guardian_archive / j07_git_audit / j07_retry_pending

    log.info("✅ [GUARDIAN] JARVIS07_GUARDIAN 등록 완료 — 자동 오류 수집·수정 활성화")
