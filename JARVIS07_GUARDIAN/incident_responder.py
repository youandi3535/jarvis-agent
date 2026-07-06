"""JARVIS07_GUARDIAN/incident_responder.py — 포스팅 실패 즉각 대응 루프 (Active Incident Responder)

포스팅 job 실패 감지 → 자동 수정 (canonical 2-tier) → 실패 플랫폼만 재발행 → 학습 기록.

★ 티어 정의는 architecture.py 단일 진실 소스. catch()→Tier 1(패턴·Bandit)→Tier 2(LLM).

흐름:
  1. TG: 🔧 [GUARDIAN] {job_id} 실패 감지 — 자동 대응 시작
  2. 오류 분류: code_bug (ImportError 등) | transient (네트워크·쿠키·셀레니움) | unknown
  3. code_bug / unknown:
       Tier 1 — 패턴 자동 수정 (static 6 + learned + Contextual Bandit, ~5s)
       Tier 2 — LLM 자동 수정 (Claude Code SDK · Sonnet 5, ~10min) — Tier 1 실패 시만
     transient: 30초 대기 후 즉시 재시도 (코드 수정 없음)
  4. retry_fns 호출 (실패 플랫폼만)
  5. learned_patterns 자동 기록
  6. TG: 결과 보고

★ 자동 승인 — Telegram 인라인 버튼 없음. side_effect="internal" (jarvis-agent 폴더 내부).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable

log = logging.getLogger("jarvis.guardian.incident")

# 동시 실행 방지 (같은 시간대 중복 incident 차단)
_active = threading.Lock()

_TRANSIENT_KEYWORDS = [
    "timeout", "TimeoutException", "connection refused",
    "WebDriverException", "NoSuchElement", "StaleElement",
    "쿠키", "cookie", "TS_COOKIE", "NID_SES",
    "HTTP Error", "503", "502", "rate limit", "403",
    "ConnectionError", "ReadTimeout", "SSLError", "network",
]

_CODE_BUG_TYPES = [
    "ImportError", "ModuleNotFoundError", "AttributeError",
    "NameError", "TypeError", "SyntaxError", "KeyError",
    "IndentationError", "ValueError", "RecursionError",
]

_TG_MAX = 2000
_TRANSIENT_WAIT = 30  # 일시적 오류 재시도 전 대기(초)


def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg[:_TG_MAX])
    except Exception:
        pass


def _classify(error_text: str) -> str:
    """오류 유형 분류: 'code_bug' | 'transient' | 'unknown'"""
    for kw in _CODE_BUG_TYPES:
        if kw in error_text:
            return "code_bug"
    for kw in _TRANSIENT_KEYWORDS:
        if kw.lower() in error_text.lower():
            return "transient"
    return "unknown"


def _make_error_record(error_text: str, job_id: str) -> dict:
    """pattern_fixer / error_analyzer 에 전달할 synthetic error_record."""
    # 첫 번째 에러 타입 추출
    m = re.search(r'\b(' + '|'.join(_CODE_BUG_TYPES) + r')\b', error_text)
    detected_type = m.group(1) if m else "PostingFailure"

    # traceback 에서 모듈 경로 추출
    mod_m = re.search(r'File "([^"]+\.py)"', error_text)
    module = mod_m.group(1) if mod_m else f"{job_id}_pipeline"

    return {
        "id": -1,
        "source": "incident_responder",
        "error_type": detected_type,
        "message": error_text[:500],
        "module": module,
        "func_name": "posting_pipeline",
        "severity": "high",
        "traceback": error_text[:2000],
    }


def _try_fast_fix(error_record: dict, job_id: str) -> bool:
    """Tier 1 (패턴 자동 수정 — static 6 + learned + Contextual Bandit). ~1-10초.

    ★ analyze() 경유 — Bandit 학습 포함. try_pattern_fix 직접 호출 금지 (Bandit 우회됨).
    """
    try:
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix

        result = analyze(error_record)
        if result and result.get("fixable"):
            success = apply_fix(-1, result)
            # Bandit 보상은 pattern_fixer/error_fixer 내부에서 자동 기록
            if success:
                log.info(f"[Incident] fast_fix 성공: {result.get('source')} @ {result.get('target_file')}")
                return True
    except Exception as e:
        log.warning(f"[Incident] fast_fix 오류: {e}")
    return False


def _try_sdk_targeted_fix(
    error_text: str,
    job_id: str,
    failed_platforms: list[str],
    theme: str,
    error_record: dict | None = None,
) -> bool:
    """Tier 2: Claude Code SDK targeted 수정 (최대 10분). Tier 1 실패 시만.

    ★ error_record 전달 시 SDK 수정이 밴딧 arm 으로 학습됨 (record_sdk_fix).
    """
    try:
        from JARVIS07_GUARDIAN.auto_repair import run_auto_repair_targeted
        return run_auto_repair_targeted(
            context=error_text,
            job_id=job_id,
            failed_platforms=failed_platforms,
            theme=theme,
            error_record=error_record,   # ★ 밴딧 학습 브리지
        )
    except Exception as e:
        log.warning(f"[Incident] sdk_targeted_fix 오류: {e}")
    return False


def _call_retry_fn(fn: Callable) -> bool:
    """retry_fn 호출. True/False 또는 dict(success=...) 모두 정규화."""
    try:
        result = fn()
        if isinstance(result, dict):
            return bool(result.get("success", False))
        return bool(result)
    except Exception as e:
        log.error(f"[Incident] retry_fn 예외: {e}")
        return False


def respond(
    job_id: str,
    failed_platforms: list[str],
    error_text: str,
    retry_fns: dict[str, Callable],
    theme: str = "",
) -> dict:
    """포스팅 실패 즉각 대응 메인 로직 (블로킹).

    Args:
        job_id: "economic" | "theme"
        failed_platforms: 실패 플랫폼 목록 ["naver", "tistory"] 등
        error_text: 로그·예외 텍스트 (원인 파악용)
        retry_fns: {platform: callable} — 재시도 함수 (실패 플랫폼만)
        theme: 테마주 이름 (theme job 시)

    Returns:
        {"fixed": bool, "retried": list, "succeeded": list}
    """
    label = f"[{job_id}] 플랫폼={failed_platforms}"
    log.info(f"[Incident] ★ 대응 시작: {label}")
    _tg(
        f"🔧 *[GUARDIAN]* {label} 실패 감지\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"자동 수정·재발행 시작 중..."
    )

    error_class = _classify(error_text)
    log.info(f"[Incident] 오류 분류: {error_class}")
    fix_applied = False

    if error_class in ("code_bug", "unknown"):
        _tg(f"🔍 [GUARDIAN] 오류 분석 중 ({error_class})...")
        error_record = _make_error_record(error_text, job_id)

        # Tier 1: learned_patterns + 정적 패턴 + Contextual Bandit
        fix_applied = _try_fast_fix(error_record, job_id)

        if not fix_applied:
            # Tier 2: Claude Code SDK targeted (Tier 1 실패 시 직행 — ★ 사용자 박제 2026-05-31)
            _tg(f"⚙️ [GUARDIAN] Claude Code SDK targeted 수정 시작 (최대 10분)...")
            fix_applied = _try_sdk_targeted_fix(error_text, job_id, failed_platforms, theme, error_record)
    else:
        # transient: 코드 수정 없이 대기 후 재시도
        _tg(f"⏳ [GUARDIAN] 일시적 오류({error_class}) — {_TRANSIENT_WAIT}초 대기 후 재시도")
        time.sleep(_TRANSIENT_WAIT)

    # ── 재발행 (실패 플랫폼만) ──────────────────────────────────────────
    if fix_applied:
        _tg(f"✅ [GUARDIAN] 수정 완료! 재발행 시작: {failed_platforms}")
    else:
        _tg(f"🔄 [GUARDIAN] 재발행 시도 (수정 미적용): {failed_platforms}")

    succeeded = []
    for platform, retry_fn in retry_fns.items():
        _tg(f"📤 [GUARDIAN] {platform} 재발행 중...")
        ok = _call_retry_fn(retry_fn)
        if ok:
            succeeded.append(platform)
            _tg(f"✅ [GUARDIAN] {platform} 재발행 성공!")
        else:
            _tg(f"❌ [GUARDIAN] {platform} 재발행 실패")

    # ── 최종 보고 ──────────────────────────────────────────────────────
    failed_after = [p for p in failed_platforms if p not in succeeded]
    status_icon = "🎉" if not failed_after else "⚠️"
    _tg(
        f"{status_icon} *[GUARDIAN] {job_id} 자동 대응 완료*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 복구 성공: {', '.join(succeeded) if succeeded else '없음'}\n"
        f"❌ 미복구: {', '.join(failed_after) if failed_after else '없음'}\n"
        f"🔧 코드 수정: {'적용됨' if fix_applied else '없음 (재시도만)'}"
    )

    # ── 수동 수정 기록 (GUARDIAN 자기 작업도 박제 대상) ────────────────
    try:
        from JARVIS07_GUARDIAN.error_collector import report_manual_fix
        report_manual_fix(
            source="incident_responder",
            fixed_file=f"{job_id}_pipeline",
            description=(
                f"포스팅 실패 자동 대응: {failed_platforms} → 복구 {succeeded} | "
                f"코드 수정={'적용' if fix_applied else '없음'}"
            ),
            error_type="PostingFailure",
            severity="high",
            actor="guardian",
        )
    except Exception:
        pass

    return {
        "fixed": fix_applied,
        "retried": list(retry_fns.keys()),
        "succeeded": succeeded,
    }


def respond_in_background(
    job_id: str,
    failed_platforms: list[str],
    error_text: str,
    retry_fns: dict[str, Callable],
    theme: str = "",
) -> None:
    """백그라운드 스레드에서 respond() 실행 (호출 즉시 반환).

    이미 대응 중이면 스킵 (중복 실행 방지).
    """
    if not _active.acquire(blocking=False):
        log.warning("[Incident] 이미 다른 incident 처리 중 — 스킵")
        _tg(
            f"⚠️ [GUARDIAN] {job_id} 대응 요청 수신\n"
            f"이미 진행 중인 incident 있음 — 완료 후 확인 요망"
        )
        return

    def _worker():
        try:
            respond(job_id, failed_platforms, error_text, retry_fns, theme)
        except Exception as e:
            log.error(f"[Incident] 대응 워커 예외: {e}")
        finally:
            _active.release()

    t = threading.Thread(
        target=_worker,
        name=f"incident_{job_id}",
        daemon=True,
    )
    t.start()
    log.info(f"[Incident] 백그라운드 대응 스레드 시작: incident_{job_id}")


__all__ = ["respond", "respond_in_background"]
