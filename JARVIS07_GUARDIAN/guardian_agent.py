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
import os
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
    ERROR_STATS_WINDOW_DAYS as _ERROR_STATS_WINDOW_DAYS,
    MAX_LLM_ATTEMPTS as _MAX_LLM_ATTEMPTS,
)

# ── Circuit breaker 런타임 상태 (설정값은 architecture.CB_MAX_HOUR) ─
_CB_LOCK      = threading.Lock()
_cb_count     = 0
_cb_hour_ts   = 0.0


# ── 킬스위치 (무배포 즉시 무효화 — 값은 환경변수에서 매 호출 조회) ────────
#    ★ 모듈 로드 시점에 상수로 굳히지 않는다 (복사본을 진실로 믿지 말 것):
#      데몬이 떠 있는 상태에서 export 만 바꿔도 다음 호출부터 반영되어야 한다.
def _flag(name: str, default: bool = True) -> bool:
    """환경변수 킬스위치. '0'/'false'/'off' 면 꺼짐. 미설정이면 default."""
    import os
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() not in ("0", "false", "off", "no")


# ── 자율 SDK 수리 장부 *관측* (★ 2026-08-12 — 판정 아님) ────────────────────
#    자율 SDK 수리를 태울지 말지의 **판정은 `repair_budget` 단독**(①단일 진입점).
#    아래 두 함수는 그 판정을 흉내내지 않는다 — "방금 그 호출에서 SDK 세션이 *실제로*
#    돌았나" 만 장부(게이트 주인이 공개한 `budget_state()`)에서 **파생해서 읽는다**.
#
#    왜 필요한가 (겹치지 않게 하려고):
#      Tier-2 시도 상한(`MAX_LLM_ATTEMPTS`)은 *LLM 을 태운 횟수* 를 세는 카운터다.
#      촉점 게이트가 막아 0초 만에 돌아온 호출까지 시도로 세면 **LLM 0회로 상한이
#      소진**되고, 진짜 코드 버그가 단 한 번도 시도되지 못한 채 wontfix 로 죽는다.
#      같은 병을 backlog 통로에서 이미 한 번 고쳤다(`analyze_llm_only` 의 `deferred`).
#      → 상한 *판정* 은 종전 자리 그대로 두고(중복 금지), **세는 시점** 만 실행 뒤로 옮긴다.
def sdk_repair_ledger_mark() -> str:
    """자율 SDK 수리 장부 표식 — 값의 주인은 `repair_budget`. 여기서 만들지 않는다."""
    try:
        from JARVIS07_GUARDIAN.repair_budget import ledger_mark
        return ledger_mark()
    except Exception:
        return ""


def sdk_session_ran(mark: "str | None", error_record: "dict | None" = None) -> bool:
    """표식 이후 **이 오류에 대해** 자율 SDK 세션이 실제로 돌았는가 — `repair_budget` 위임.

    ★ 2026-08-12 (적대적 검증 C-1): 종전 구현은 장부의 **전역 blocked 증분** 으로 판정해,
      내 호출과 무관한 남의 차단 한 건이면 곧바로 False 였다(실측: 580초 세션이 돌았는데도).
      L4 쿨다운이 세션이 도는 그 창의 다른 시도를 전부 차단하며 행을 쓰므로 구조적 오판이다.
      판정을 owner(`repair_budget`)로 옮기고 **지문(신원)** 기준으로 묻는다 — 판단을 여기
      복제하지 않는다(①). 판정 불가면 True(보수적 기본값)는 그대로다.
    """
    try:
        from JARVIS07_GUARDIAN.repair_budget import session_ran_since
        return bool(session_ran_since(mark or "", error_record))
    except Exception:
        return True


# ── error_log 시간 컬럼 런타임 파생 (★ 결함1 재발 방지) ──────────────────
#    종전 코드는 `created_at` 이라는 *존재하지 않는* 컬럼을 SQL 에 박아두고
#    `except: pass` 로 예외를 삼켰다 → 빈도 상향 안전장치가 상시 무력(70일 무증상).
#    이제 스키마를 PRAGMA 로 *조회해서* 컬럼명을 파생한다. 스키마가 바뀌어도 따라가고,
#    후보가 하나도 없으면 조용히 통과하는 대신 시끄럽게 실패한다.
_TIME_COL_PREF  = ("timestamp", "created_at", "occurred_at")
_time_col_cache: str = ""


def _error_time_col(conn) -> str:
    """error_log 의 실제 시간 컬럼명을 런타임 조회로 파생 (캐시 1회)."""
    global _time_col_cache
    if _time_col_cache:
        return _time_col_cache
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(error_log)")}
    for cand in _TIME_COL_PREF:
        if cand in cols and cand.isidentifier():
            _time_col_cache = cand
            return cand
    raise RuntimeError(
        f"error_log 에 시간 컬럼 없음 (후보 {_TIME_COL_PREF}) — 실제 컬럼: {sorted(cols)}"
    )


# 안전장치 실패 관측 카운터 — 침묵 금지. /status 에 그대로 노출된다.
_SAFETY_FAILS: dict[str, dict] = {}


def _note_safety_fail(gate: str, exc: Exception) -> None:
    """안전장치 내부 실패를 *관측 가능하게* 기록 (로그 + /status 노출)."""
    d = _SAFETY_FAILS.setdefault(gate, {"count": 0, "last": ""})
    d["count"] += 1
    d["last"] = f"{type(exc).__name__}: {exc}"[:200]
    log.warning(f"[GUARDIAN] ⚠️ 안전장치 '{gate}' 실패({d['count']}회) — {d['last']}")


# ── capability 선언 + 텔레그램 /status 섹션 ─────────────────────

def _status_section() -> str:
    """텔레그램 /status + 웹 대시보드용 GUARDIAN 상태 요약."""
    lines = ["🛡️ *JARVIS07 — GUARDIAN*"]
    try:
        from shared import db as _db
        stats = _db.get_error_stats(days=_ERROR_STATS_WINDOW_DAYS)
        total   = stats.get("total", 0)
        new_    = stats.get("by_status", {}).get("new", 0)
        fixed   = stats.get("by_status", {}).get("fixed", 0)
        wontfix = stats.get("by_status", {}).get("wontfix", 0)
        manual  = stats.get("by_status", {}).get("manual", 0)
        ignored = stats.get("by_status", {}).get("ignored", 0)
        lines.append(f"📊 최근 {_ERROR_STATS_WINDOW_DAYS}일: 총 {total}건 (신규 {new_} · 자동수정 {fixed} · 수정불가 {wontfix} · 수동수정 {manual} · 무시됨 {ignored})")

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
                # ★ 구조 상수가 아니라 **생존 지표** 를 표시한다 (2026-08-07 감사).
                #   종전엔 arm_count·feature_dim 만 읽어 "fixer 9종 학습" 이라 했는데,
                #   그 값들은 학습이 11일 멈춰 있어도 그대로다 — 화면만 초록불이었다.
                _obs = _bs.get("observed_arms", 0)
                _h = _bs.get("last_update_h", -1)
                if _bs.get("stalled"):
                    _ago = f"{_h/24:.0f}일" if _h >= 24 else (f"{_h:.0f}시간" if _h >= 0 else "기록 없음")
                    lines.append(
                        f"🛑 Tier 1 Bandit **정지** — 마지막 학습 {_ago} 전 "
                        f"(관측된 arm {_obs}/{_arms})"
                    )
                else:
                    lines.append(
                        f"🎰 Tier 1 Bandit 학습 중 — 관측 arm {_obs}/{_arms}, "
                        f"{_h:.0f}시간 전 갱신"
                    )
        except Exception:
            pass

        # ★ SLO 3종 — **값만** 노출한다(경보 발효는 GUARDIAN_SLO_ALERT=1 로 사람이 켠다).
        #   여기 붙이는 이유: 이미 사람이 보는 화면이라 새 채널·새 카드가 필요 없다.
        try:
            lines += slo_lines()
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

        # ★ 격리 버킷 요약 (결함3) — 걸러낸 것을 *보이게* 한다. 기존 섹션에 편승.
        try:
            _ig = ignored_bucket_report()
            if _ig.get("total") or _ig.get("code_bug_ignored"):
                _r = _ig.get("by_reason") or {}
                _top = " · ".join(f"{k} {v}" for k, v in list(_r.items())[:3])
                _d = _ig.get("delta_pct")
                lines.append(
                    f"🧺 격리(ignored) {_ig['total']}건"
                    + (f" ({_d:+.1f}%)" if _d is not None else "")
                    + (f" — {_top}" if _top else "")
                )
                _fpl = _ig.get("code_bug_ignored") or []
                if _fpl:
                    lines.append(
                        f"🚨 격리 오탐 의심 {len(_fpl)}건 "
                        f"(코드결함 타입이 ignored 에 있음 · 스캔 {_ig.get('fp_scope','?')} "
                        f"· 창 내 {_ig.get('fp_in_window', 0)}건)"
                    )
                    lines.append("　　" + " / ".join(
                        f"#{i['id']} {i['error_type']}({i['reason']})" for i in _fpl[:3]))
                if _ig.get("no_resolution"):
                    lines.append(f"　⚠️ 무시 사유 미기록 {_ig['no_resolution']}건")
        except Exception:
            pass

        # ★ 안전장치 자체가 실패하면 *그 사실* 을 보여준다 (침묵 금지 — 결함1 재발 방지)
        if _SAFETY_FAILS:
            for _g, _d in _SAFETY_FAILS.items():
                lines.append(f"⚠️ 안전장치 '{_g}' 실패 {_d['count']}회 — {_d['last'][:80]}")

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

        # ★ 자율 SDK 수리 예산 — 게이트 주인(repair_budget)이 만든 한 줄을 *그대로* 싣는다.
        #   문구를 여기서 조립하면 사본이 된다. 게이트 미배포면 조용히 생략.
        try:
            from JARVIS07_GUARDIAN.repair_budget import status_line as _rb_status_line
            _rb_line = (_rb_status_line() or "").strip()
            if _rb_line:
                lines.append(_rb_line)
        except Exception:
            pass

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
                f"/errors_stats    {_ERROR_STATS_WINDOW_DAYS}일 오류 통계\n"
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


# 알림을 이미 보낸 시간 버킷의 시작 시각 — 버킷당 1통만 나가게 하는 유일한 상태.
_cb_notified_ts = -1.0


def _circuit_open_is_new() -> bool:
    """브레이커가 *이번 시간 버킷에서 처음* 열렸으면 True.

    ★ 왜 필요한가 (2026-08-14 사고)
      종전엔 차단이 **발동할 때마다** 텔레그램을 보냈다. 억제가 0줄이라 같은 문장이
      6시간 동안 74통 나갔다(실측 07:18~13:10, 오류 6건 × 10분 스윕). 같은 파일의
      형제 게이트(`repair_budget._notify_once`)는 이미 쿨다운으로 57건 중 51건을
      억제하고 있었다 — **알림 억제가 한쪽 경로에만 있었던 비대칭**이 원인이다.

      브레이커의 상태는 *시간 버킷* 하나로 정의된다(`_cb_hour_ts`). 사람이 알아야 할
      사건은 "이 시간에 브레이커가 열렸다" 지 "몇 번째 오류가 막혔다" 가 아니다.
      → 쿨다운 숫자를 새로 만들지 않고 **버킷 자체를 억제 키로 쓴다**(②).
        버킷이 리셋되면(다음 시간) 다시 한 통 — 상황이 계속되면 시간당 1통으로 알려준다.
    """
    global _cb_notified_ts
    with _CB_LOCK:
        if _cb_notified_ts == _cb_hour_ts:
            return False
        _cb_notified_ts = _cb_hour_ts
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
    if not _flag("GUARDIAN_ESCALATE"):
        return base_sev

    try:
        from shared.db import get_db
        from datetime import datetime, timedelta
        since = (datetime.now() - timedelta(seconds=_ESCALATE_WINDOW_SECS)).isoformat()
        with get_db() as conn:
            # ★ 결함 수정 2026-07-25 — 종전 `created_at` 은 error_log 에 *없는 컬럼* 이라
            #   이 SELECT 가 매번 OperationalError 를 던졌고 아래 `except: pass` 가 삼켜
            #   "1시간 3회 반복 → severity 상향" 안전장치가 **상시 무력** 이었다.
            #   컬럼명을 박지 않고 PRAGMA 로 파생한다 (② 동적 설계).
            tcol = _error_time_col(conn)
            row = conn.execute(
                f"""SELECT COUNT(*) FROM error_log
                    WHERE error_type = ? AND source = ?
                      AND SUBSTR(message, 1, 40) = ?
                      AND {tcol} >= ?""",
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
    except Exception as e:
        # ★ 침묵 금지 — 이 except 가 70일짜리 무증상 열화를 만든 바로 그 지점이다.
        #   실패해도 발행/수리를 막지 않되(보수적 폴백=기본 severity), 반드시 보이게 남긴다.
        _note_safety_fail("escalate_severity", e)
    return base_sev


#: 반복 발화가 구조적으로 가능한 알림 — 같은 오류에 같은 결론이 계속 나오는 부류.
#  ★ 2026-08-14 (P2) — **우리가 만든 회귀**를 막는다.
#    `_reopen_if_recurred`(재발 시 wontfix→new)와 `try_claim_error` 기본 선점 집합의
#    `wontfix` 가 겹치면서, *시도 상한에 도달해 종결된* 오류가 **재발할 때마다**
#    오케스트레이터를 끝까지 돌고 `llm_cap_reached` 를 쐈다. 종전에는 선점이 실패해
#    조용히 return 했으므로 이 폭주는 P1/T3 변경이 새로 만든 것이다.
#    억제는 **이미 있는 창** (`error_collector._in_cooldown`)만 쓴다 — 새 창·새 채널 금지.
#    성공/실패 같은 '상태가 바뀐' 알림은 여기 넣지 않는다(억제하면 진짜 신호가 사라진다).
_REPEATABLE_NOTIFY = ("llm_cap_reached", "not_auto_fixable", "deny_path")


def _notify_suppressed(error_record: dict, result: str) -> bool:
    """이 알림을 이번엔 보내지 않아도 되는가 — 반복 발화 억제(기존 쿨다운 재사용)."""
    if result not in _REPEATABLE_NOTIFY:
        return False
    try:
        from JARVIS07_GUARDIAN.error_collector import _cool_key, _in_cooldown
        key = "notify:" + result + ":" + _cool_key(
            str(error_record.get("source") or ""),
            str(error_record.get("module") or ""),
            str(error_record.get("error_type") or ""),
            str(error_record.get("message") or ""))
        return _in_cooldown(key)
    except Exception:       # noqa: BLE001 — 억제 판정 실패 시 보내는 쪽이 안전
        return False


def _notify_all(error_record: dict, result: str, tier: int = 0, severity: str = ""):
    """모든 심각도에 텔레그램 알림 — 수정 성공·실패·불가 공통."""
    if _notify_suppressed(error_record, result):
        log.info("[GUARDIAN] 알림 억제(%s) — 같은 오류에 같은 결론이 쿨다운 안에서 반복됨: %s",
                 result, (error_record.get("error_type") or "?"))
        return
    sev = severity or error_record.get("severity", "medium")
    etype   = error_record.get("error_type", "?")
    source  = error_record.get("source", "?")
    module  = error_record.get("module", "?")
    msg     = (error_record.get("message") or "")[:120]

    _ICONS = {
        "success": "✅", "failed": "❌", "critical_manual": "🔴",
        "circuit_open": "⚡", "deny_path": "🔒", "llm_cap_reached": "🛑",
        "not_auto_fixable": "🚫",
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
    elif result == "not_auto_fixable":
        text = (
            f"{icon} *[GUARDIAN] 자동수정 비적격 — Tier 2 생략*\n"
            f"심각도: {sev_tag}  판정: severity.is_auto_fixable() = False\n"
            f"소스: {source} / {module}\n"
            f"유형: {etype}\n"
            f"내용: {msg}\n"
            f"→ 패턴 대상 아님 + 저위험 → LLM 비용 대신 수동 검토"
        )
    elif result == "llm_cap_reached":
        text = (
            f"{icon} *[GUARDIAN] Tier 2(LLM) 재시도 상한 도달 — 자동 종결*\n"
            f"이 오류는 이미 {_MAX_LLM_ATTEMPTS}회 LLM 수정을 시도했습니다 → 더 이상 재시도 안 함\n"
            f"소스: {source} / {module}\n"
            f"유형: {etype}\n"
            f"내용: {msg}\n"
            f"→ wontfix 로 표시, 수동 검토 필요"
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
    # ★ FIX[6] (전수감사 2026-07-17): 코드 수정은 데몬 재시작 후에만 발효(Python import 캐시)라
    #   같은 프로세스에서 *발행* 잡을 즉시 재트리거하면 구 코드로 재실행 + 중복 발행 위험 →
    #   writer 는 재트리거 안 함(None; 다음 스케줄이 새 코드로 실행). 수집(radar) 잡만 즉시
    #   재실행 안전. 'j02_radar_collect' 는 DEFAULT_JOBS 미존재 stale id → 실제 radar_trends_06 교정.
    _SOURCE_JOB_MAP = {
        "writer": None,
        "radar":  "radar_trends_06",
        "infra":  None,
        "master": None,
    }
    job_id = _SOURCE_JOB_MAP.get(source)
    if not job_id:
        return

    try:
        # ★ FIX[6]: raw BackgroundScheduler 엔 run_job 메서드 없음(AttributeError→broad except
        #   삼킴, 재트리거 항상 no-op) → job_controller.run_job_now 단일 진입점 사용.
        from JARVIS04_SCHEDULER.job_controller import run_job_now
        _res = run_job_now(job_id)
        if _res.get("ok"):
            log.info(f"[GUARDIAN] 원래 잡 재시도 트리거: {job_id}")
            try:
                from shared.notify import send_tg
                send_tg(f"🔄 *[GUARDIAN] 작업 재시도*\n수정 완료 후 {job_id} 재시작했습니다.")
            except Exception:
                pass
        else:
            log.debug(f"[GUARDIAN] 잡 재시도 스킵: {job_id} — {_res.get('error','?')}")
    except Exception as e:
        log.debug(f"[GUARDIAN] 잡 재시도 실패: {e} — 다음 스케줄에 자동 실행됩니다.")


# ══════════════════════════════════════════════════════════════════
# ★ 종결(`fixed`) 상태의 **단일 출구** — `close_error()` (2026-08-14)
# ══════════════════════════════════════════════════════════════════
#
# 왜 출구를 하나로 모았나 (ADR 018 `infographic_engine._emit` 과 같은 형태)
#   검사를 늘리는 대신 **나가는 문을 줄인다**. 종전엔 Tier-2 성공 경로가
#   `mark_error_status(error_id, "fixed", "Tier-2 SDK 수정 + 재현검증 통과")` 라는
#   **리터럴**을 직접 찍었다. 그 문장은 참일 수도 거짓일 수도 있는데, 무엇이든
#   똑같이 찍혔다 — 실측상 트래픽 대부분은 `unverifiable`(재현 프로브가 도는 오류
#   타입이 6종뿐이라 `RuntimeError` 계열은 검증 대상이 아니다)인데도 DB 에는
#   "재현검증 통과" 라고 남았다. 라벨이 증거와 무관하면 그 라벨은 정보가 아니다.
#
# 규칙 3가지
#   ① 사유는 **리터럴이 아니라 검증 상태에서 파생** 한다(`error_fixer` 가 형식의 주인).
#   ② 판정이 "여전히 재현됨" 이면 **fixed 로 닫지 않는다**(fail-closed).
#   ③ 킬스위치 `GUARDIAN_CLOSE_STRICT=0` 이면 종전 문구로 복귀(무배포 롤백).

def close_error(error_id: int, *, verification: str = "", detail: str = "",
                tier: int = 2) -> str:
    """오류를 `fixed` 로 닫는다. 반환: 실제로 기록한 resolution (닫지 않았으면 "").

    Args:
        verification: `error_fixer` 정본 검증 상태 문자열. 빈 값이면 '검증 미실행'.
        detail:       사람이 읽을 부연(검증기가 준 사유 등).
        tier:         어느 티어가 고쳤는지 — 사유 문장에 파생 삽입.
    """
    from shared import db as _db
    from JARVIS07_GUARDIAN.error_fixer import (VERIFY_REPRODUCES, verification_phrase,
                                               verification_tag)

    _state = (verification or "").strip()

    # ② 판정이 '재현됨' 이면 종결하지 않는다 — 출구가 판정을 존중한다(fail-closed).
    if _state == VERIFY_REPRODUCES:
        log.warning(f"[GUARDIAN] #{error_id} close_error 거부 — 검증 판정이 "
                    f"{VERIFY_REPRODUCES} 다. fixed 로 닫지 않는다")
        return ""

    # ③ 킬스위치는 **함수 안에서** 읽는다 — 데몬 무재시작 토글·monkeypatch 가 실제로 먹는다.
    if os.getenv("GUARDIAN_CLOSE_STRICT", "1") == "0":
        _res = f"Tier-{tier} SDK 수정 + 재현검증 통과"      # ← 종전 문구(킬스위치 복귀 전용)
        _db.mark_error_status(error_id, "fixed", _res)
        return _res

    # ① 파생 — 태그(기계 판독) + 문장(사람) + 부연. 형식·표현의 주인은 error_fixer 다.
    _res = f"{verification_tag(_state)}Tier-{tier} — {verification_phrase(_state)}"
    if detail:
        _res = f"{_res}: {str(detail)[:300]}"
    _db.mark_error_status(error_id, "fixed", _res)
    return _res


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
            f"traceback:\n{(error_record.get('traceback', '') or '')[:2000]}"
        )

        log.info(f"[GUARDIAN] #{error_id} 2순위 Claude Code SDK 수정 시작 (최대 10분)")
        # ★ 촉점 게이트(repair_budget)가 막으면 이 호출은 LLM 0회로 즉시 False 를 준다.
        #   '고치려 했지만 실패' 와 '아예 시도하지 못함' 은 뒤처리가 다르므로 구분한다.
        _ledger_mark = sdk_repair_ledger_mark()
        # ★ `_repair` — Tier-2 안에서 난 **검증 판정을 받아 오는 그릇** (2026-08-14).
        #   이것이 없으면 `_verify_sdk_fix` 의 결론이 함수 경계에서 증발한다(종전 상태).
        _repair: dict = {}
        fixed = run_auto_repair_targeted(
            context=error_text,
            job_id=error_record.get("source", "unknown"),
            failed_platforms=[error_record.get("module", error_record.get("source", "unknown"))],
            error_record=error_record,   # ★ 밴딧 학습 브리지 — SDK 수정 → fingerprint llm_patch + 밴딧 보상
            out=_repair,
        )
        _ran = sdk_session_ran(_ledger_mark, error_record)   # False = 게이트가 막아 세션 자체가 없었음

        if fixed:
            # ★ 근거를 남긴다 (2026-08-08) — `fixed` 가 세 뜻을 말하지 않게.
            #   ※ 2026-08-08 19시경 자동 수정이 이 인자를 **주석만 남기고 지웠다**
            #     (근거 없이 fixed 를 찍는 상태로 되돌아감). 골든 테스트가 잡았다.
            # ★ 2026-08-14 — 그 '근거' 가 **리터럴**이었던 것이 다음 결함이었다.
            #   `run_auto_repair_targeted` 는 True 를 `reproduced_gone` 과
            #   `unverifiable` 둘 다에 준다. 종전 문구는 그 둘을 구분 없이
            #   "재현검증 통과" 라고 말했다 — 대부분이 후자였다.
            #   이제 판정(`_repair["state"]`)에서 사유를 **파생** 하고, 종결 기록은
            #   `close_error` 단일 출구를 지난다.
            _res = close_error(error_id, verification=_repair.get("state", ""),
                               detail=_repair.get("why", ""), tier=2)
            log.info(f"[GUARDIAN] #{error_id} SDK 수정 성공 → 학습 저장 완료, "
                     f"작업 재시도 중 — {_res}")
            # 학습 저장: ① _record_repairs_to_guardian(external_change) ② record_sdk_fix(밴딧 보상 + llm_patch) — 둘 다 run_auto_repair_targeted 내부 자동
            _retry_original_job(error_record)
            return True
        else:
            # ★ 롤백된 오류는 **여전히 살아 있다** (2026-08-08 재검증).
            #   종전엔 `wontfix` 로 찍었는데, `j07_retry_pending`(10분)은 `status='new'`
            #   만 보므로 그 순간 재시도 루프에서 빠지고 다음 Tier-2 는 최대 7일 뒤다.
            #   `MAX_LLM_ATTEMPTS` 상한이 이미 있어 무한 재시도 위험은 없다 —
            #   시도 여유가 남아 있는데 스스로 문을 닫을 이유가 없다.
            #   실측 wontfix 61건 전부 `resolution` NULL 이었다 — '왜' 가 공백이었다.
            # ★ 게이트가 이미 종결 사유를 남겼으면 **덮어쓰지 않는다** (2026-08-12).
            #   자율 SDK 수리 게이트는 영구 사유(사람 개입·지문 상한·보안 파일)일 때
            #   status 를 ignored/wontfix + resolution 으로 찍는다. 여기서 관성적으로
            #   "재시도 대기" 를 덮어쓰면 **사유가 지워지고** 10분마다 같은 문을 다시
            #   두드린다. 새 플래그를 만들지 않고 *DB 상태에서 파생* 한다(②).
            #   어휘의 주인은 architecture 단독 — 여기 리터럴로 다시 적지 않는다(② · P2).
            from JARVIS07_GUARDIAN.architecture import PARKED_STATUSES as _PARKED
            _cur = (_db.get_error(error_id) or {}).get("status", "")
            if _cur in _PARKED:
                log.info(f"[GUARDIAN] #{error_id} 게이트 종결 상태({_cur}) 유지 — 덮어쓰지 않음")
                return False
            if not _ran:
                # 일시적 차단(쿨다운·일일 예산) — *시도한 적이 없으므로* 상한을 쓰지 않는다.
                _db.mark_error_status(
                    error_id, "new",
                    "Tier-2 미실행 — 자율 SDK 수리 게이트 대기(쿨다운·예산). 재시도 대기")
                log.info(f"[GUARDIAN] #{error_id} Tier-2 미실행(게이트 대기) → new 유지")
                return False
            _attempts = int(error_record.get("llm_attempts") or 0)
            if _attempts >= _MAX_LLM_ATTEMPTS:
                _db.mark_error_status(
                    error_id, "wontfix",
                    f"Tier-2 {_attempts}회 시도 후에도 미해결 (상한 도달)")
                log.warning(f"[GUARDIAN] #{error_id} SDK 수정 실패 · 상한 도달 → wontfix")
            else:
                _db.mark_error_status(
                    error_id, "new",
                    f"Tier-2 수정 실패/롤백 — 재시도 대기 ({_attempts}/{_MAX_LLM_ATTEMPTS})")
                log.warning(f"[GUARDIAN] #{error_id} SDK 수정 실패 → new (재시도 여유 "
                            f"{_MAX_LLM_ATTEMPTS - _attempts}회)")
            # 알림은 _orchestrate()의 _notify_all()에서 통합 처리
            return False

    except Exception as e:
        # ★ 삼키지 않는다 (2026-08-07 감사 — 이 except 가 밴딧을 11일 멈춰 세웠다).
        #
        #   종전엔 `log.warning(f"...: {e}")` 한 줄이었다. 그래서 전체 로그에 21회 터진
        #   `'NoneType' object is not subscriptable` 가 **어느 줄인지 아무도 몰랐고**,
        #   `llm` arm 의 유일한 보상 경로가 조용히 막힌 채 status 만 wontfix 로 쌓였다
        #   (07-27 이후 llm_attempts>0 17건 중 16건 wontfix).
        #   → traceback 을 남기고 GUARDIAN 에 박제한다. 자기 자신의 고장도 학습 대상이다.
        import traceback as _tb
        _trace = _tb.format_exc()
        log.error(f"[GUARDIAN] SDK targeted 수정 예외 (#{error_id}): "
                  f"{type(e).__name__}: {e}\n{_trace}")
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _rep
            _rep("GuardianTier2BridgeCrash", "guardian",
                 message=f"Tier-2 SDK 브리지가 터져 밴딧 보상 경로가 막힘: {type(e).__name__}: {e}",
                 module=__name__, func_name="_try_sdk_targeted_fix",
                 tb_str=_trace, context={"error_id": error_id})
        except Exception:
            pass
        try:
            from shared import db as _db
            _db.mark_error_status(error_id, "wontfix",
                                  "Tier-2 SDK 브리지 예외 — 수동 검토 필요")
        except Exception:
            pass
        return False


#  하트비트 주기 — 수확기 판정(stuck_minutes)보다 훨씬 촘촘해야 오탐이 없다.
HEARTBEAT_SEC: int = 60


def _start_heartbeat(error_id: int) -> threading.Event:
    """처리 중인 오류에 주기적으로 '살아있음' 신호를 보낸다 (★ ERRORS [473]).

    ★ 왜 스레드인가: Tier-2 는 `run_sdk_query()` 한 번에 수십 분을 블로킹한다.
      그 안에는 신호를 심을 지점이 없다. 별도 스레드가 밖에서 찍으면
      *무엇이 얼마나 오래 걸리든* 상관없이 살아있음이 보장된다.
    Returns: stop 이벤트 — set() 하면 즉시 종료 (daemon 스레드라 누수 없음)
    """
    stop = threading.Event()

    def _beat():
        from shared import db as _db
        while not stop.wait(HEARTBEAT_SEC):
            try:
                if not _db.heartbeat_error(error_id):
                    return   # 이미 analyzing 이 아님 (완료·회수됨) → 신호 중단
            except Exception:
                pass         # 신호 실패는 치명적이지 않다 — 다음 주기에 재시도

    threading.Thread(target=_beat, daemon=True,
                     name=f"guardian-hb-{error_id}").start()
    return stop


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

    _hb_stop = None   # 하트비트 핸들 — 선점 성공 후에만 설정됨 (finally 가 참조)

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
        from JARVIS07_GUARDIAN.severity import is_deterministic_code_error
        # ★ 버킷 결정은 `_park_non_code` 단독 (2026-08-14 — ①·③).
        #   `companions` 전달(봉투 신호가 유일한 신호면 삼키지 않기)도 그 안에 있다.
        _parked = _park_non_code(error_id, error_record)
        if _parked:
            log.info(f"[GUARDIAN] #{error_id} 수리 비대상 → {_parked}: {error_type}")
            return

        # ── 안전장치 1: 보안 파일 수정 금지 ───────────────────────
        #   ★ 판단은 `tier2_blocked_reason` 단독 (2026-08-09) — backlog 통로와 **같은 문**.
        #     여기만 고치면 저쪽이 열린 채로 남는다(실제로 그랬다).
        if _is_deny_path(module):
            log.warning(f"[GUARDIAN] #{error_id} 보안 파일 수정 차단 — {module}")
            _notify_all(error_record, "deny_path")
            _db.mark_error_status(error_id, "wontfix",
                                  tier2_blocked_reason(error_record)
                                  or f"보안 파일이라 자동수정 금지 — {module}")
            return

        # ── 안전장치 2: Circuit breaker ───────────────────────────
        #    ★ 순서 정정 2026-07-25 (결함4 검토): 종전엔 여기서 토큰을 *먼저 소모* 했는데,
        #      이 아래에 "발행 중 보류"·"DB 선점 실패"·"잠정 실패 보류" 처럼 **아무것도
        #      고치지 않고 되돌아가는 경로가 3개** 있다. 특히 발행 중 오류는 무더기로
        #      들어오므로 CB_MAX_HOUR(10)을 *수리 0건인 채로* 통째로 태워버릴 수 있고,
        #      그러면 정작 발행이 끝난 뒤 진짜 수리가 차단된다(정확히 거꾸로 된 보호).
        #      → 토큰은 *실제로 파일을 건드리기 직전*(Tier 1 진입 직전)에 소모한다.
        #      보호 강도는 동일하다: 수리는 반드시 토큰을 통과해야만 일어난다.
        #    킬스위치 GUARDIAN_CB_LATE=0 → 종전 위치(여기서 소모)로 즉시 복귀.
        _cb_late = _flag("GUARDIAN_CB_LATE")

        def _circuit_blocked() -> bool:
            """토큰 소모 + 초과 시 보고·상태 되돌림. True 면 호출자는 즉시 return.

            ★ 터미널 상태는 덮어쓰지 않는다 (ERRORS [632] 후속) — 종전엔 무조건
              status='new' 로 되돌려 다음 retry_pending 이 재청구했다. 다른 세션이
              이미 wontfix/ignored/fixed 로 닫은 레코드까지 구분 없이 되살아나
              같은 결론(예: 캡차 백오프 — 사람 개입 필요)을 세션마다 다시 태워
              확인하는 낭비가 반복됐다([625][627][628][629][631][632][636][637]
              전부 이 반복의 사례). 위 585행 `_try_sdk_targeted_fix` 가 이미 쓰는
              "게이트 종결 상태 유지" 패턴을 여기도 동일 적용(① 단일 진입점 —
              사본 아니라 같은 판단을 circuit breaker 교차점에도 배선).
            """
            if _circuit_breaker_ok():
                return False
            _cur = (_db.get_error(error_id) or {}).get("status", "")
            # 어휘의 주인은 architecture 단독 — 여기 리터럴로 다시 적지 않는다(②).
            from JARVIS07_GUARDIAN.architecture import CLOSED_STATUSES as _CLOSED
            if _cur in _CLOSED:
                log.info(f"[GUARDIAN] #{error_id} Circuit breaker 발동 — "
                         f"이미 종결 상태({_cur})라 유지, 재청구 안 함")
                return True
            # 알림은 *버킷당 1통* — 발동마다 보내면 같은 문장이 수십 통 나간다(위 함수 주석).
            if _circuit_open_is_new():
                log.warning(f"[GUARDIAN] #{error_id} Circuit breaker 발동 — 시간당 한도 초과")
                _notify_all(error_record, "circuit_open")
            else:
                log.info(f"[GUARDIAN] #{error_id} Circuit breaker 발동 — "
                         f"이 시간 버킷에서 이미 알림, 알림 억제")
            _db.mark_error_status(error_id, "new")  # 다음 retry_pending 에서 재처리
            return True

        if not _cb_late and _circuit_blocked():
            return

        # ── 빈도 기반 severity 자동 상향 ─────────────────────────
        severity = _escalate_severity(error_record)
        error_record = {**error_record, "severity": severity}  # 상향된 값으로 갱신

        # ── ★★ 발행 중에는 *기록만* — 자동수정 전면 보류 (사용자 박제 2026-07-25) ──────
        #    왜 (셋 다 실측 근거):
        #      ① 효과 0 — 파이썬 import 캐시 탓에 지금 고친 코드는 *현재 데몬 프로세스에
        #         반영되지 않는다*(CLAUDE.md 박제). 즉 발행 중 수리는 그 발행을 못 구한다.
        #         비용만 내고 효과는 다음 재시작 이후 = 새벽에 고쳐도 결과가 같다.
        #      ② 자원 경합 — Tier-2 는 Claude Code SDK 세션(최대 10분)을 잡는다. 발행 대본·
        #         게이트·이미지가 같은 LLM 한도를 쓰므로 발행이 자기 오류 수리에 한도를 빼앗긴다.
        #      ③ 오진단 위험 — 2026-07-25 실증: 발행 중 급히 돈 Tier-2 가 사실성 *오탐* 을
        #         'LLM 이 제목 수치를 날조' 로 오진단하고 프롬프트까지 잘못 고쳤다(363f5c2).
        #    보류해도 유실 없음: status='new' 를 *그대로 둔 채* 반환하므로(선점 전에 빠진다)
        #    발행 종료 후 `j07_retry_pending`(10분 간격)이 자동 회수한다 — 새 잡·새 큐 신설 0.
        #    critical 만 예외: 매트릭스상 Tier-1(LLM-0)+알림 뿐이라 한도를 뺏지 않고,
        #    retry_pending 은 critical 을 건너뛰므로 여기서 막으면 영영 안 알려진다.
        if severity != "critical":
            try:
                from shared.llm import is_publishing as _is_pub
                if _is_pub():
                    log.info(f"[GUARDIAN] #{error_id} [{severity}] 발행 중 — 자동수정 보류"
                             f"(기록만 유지, 발행 종료 후 retry_pending 이 회수)")
                    return
            except ImportError:
                pass   # is_publishing 미가용 — 종전대로 진행(보류는 최적화이지 필수 아님)

        # ── 안전장치 2.5: DB 레벨 원자적 선점 (★ 프로세스 간·중복 디스패치 경쟁 차단) ──
        #    in-memory _processing 은 같은 프로세스 내 스레드만 방어한다. bus 재전달
        #    (dispatch_pending 폴백)과 job_retry_pending 스윕이 겹치면 서로 다른 스레드가
        #    거의 동시에 이 지점까지 통과할 수 있다 (관찰: #3773 동일 오류에 Tier-2 세션
        #    2개가 2.5초 간격으로 중복 기동 — LLM_MAX_CONCURRENCY=1 락 경합을 스스로 악화).
        #    UPDATE...WHERE 조건부 갱신은 SQLite 가 직렬화하므로 두 번째 호출은 반드시 실패.
        if not _db.try_claim_error(error_id, claim_status="analyzing"):
            log.info(f"[GUARDIAN] #{error_id} 이미 처리 착수됨(DB 선점 실패) — 중복 오케스트레이션 skip")
            return

        # ── 안전장치 2.6: 살아있음 신호(하트비트) 시작 (★ ERRORS [473]) ──────────
        #    선점만으로는 부족하다. 수확기(job_retry_pending)가 오래 묶인 analyzing 을
        #    죽은 세션으로 보고 new 로 되돌리는데, 그 판정이 '오류 기록 시각' 기준이라
        #    *살아 있는 세션* 도 리셋됐다 (2026-07-18 #3435: 82분 세션이 75분에 리셋되어
        #    두 번째 Tier-2 세션 중복 기동). 이제 처리 중에는 주기적으로 신호를 보내고,
        #    수확기는 '마지막 신호 이후 경과' 로 판정한다 → 작업이 아무리 길어도 안전.
        _hb_stop = _start_heartbeat(error_id)

        log.info(f"[GUARDIAN] 오케스트레이터 시작 — #{error_id} [{severity}] {error_type}")

        # ── 안전장치 2.6: 잠정 실패는 Tier 1 도 보류 — 단, 결정론적 타입은 예외 ──
        #    (★ ERRORS [478] — 사용자 판단 2026-07-22, 안(다))
        #    "1회 실패는 오류가 아니다" 는 Tier 1 에도 적용된다. Tier 1 은 LLM 을 안 쓰지만
        #    **파일을 수정한다**(.bak 백업 후 패치). 일시적 실패에 코드를 건드리면
        #    멀쩡한 코드를 고치는 것이다.
        #    다만 `SyntaxError`·`ImportError`·`NameError` 처럼 **재시도해도 100% 같게
        #    실패하는** 타입은 기다릴 이유가 없다 — 지금 고쳐야 다음 시도가 산다.
        #    → 결정론적 타입만 즉시 Tier 1 허용. (Tier 2 는 비싸므로 이 타입도 뒤에서 보류)
        if error_record.get("provisional") and not is_deterministic_code_error(error_type):
            log.info(f"[GUARDIAN] #{error_id} 잠정 실패(재시도 남음) + 비결정론적({error_type}) "
                     f"— Tier 1·2 모두 보류. 재시도 끝난 뒤 판정")
            _db.mark_error_status(error_id, "new")
            return

        # ── 안전장치 2: Circuit breaker (새 위치 — 실제 수리 직전) ────────
        if _cb_late and _circuit_blocked():
            return

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
            _db.mark_error_status(error_id, "wontfix",
                                  tier2_blocked_reason(error_record)
                                  or "critical — Tier-2 생략하고 수동 검토로")
            return

        # ── 안전장치 2.65: 잠정 실패는 Tier 2 보류 (★ ERRORS [476]) ────────────
        #    harness 는 실패하면 재시도한다. attempt=1 실패 시점엔 그것이 *일시적* 인지
        #    (재시도로 해결) *결정론적* 인지(진짜 코드 버그) 알 수 없다. 결과가 나오기 전에
        #    LLM 수십 분을 태우는 것은 도박이다.
        #    실측 2026-07-22: Tier-2 를 태운 harness 오류 74건 중 **57건(77%)이 attempt=1**.
        #    액션이 최종 성공하면 `_resolve_attempt_errors` 가 해소 처리하므로 영영 안 온다
        #    (= 애초에 문제가 아니었던 것). 최종 실패해야 `_finalize_attempt_errors` 가
        #    잠정 표시를 풀어 여기로 돌아온다 (= 비로소 볼 만한 것).
        if error_record.get("provisional"):
            log.info(f"[GUARDIAN] #{error_id} 잠정 실패(재시도 남음) — Tier 2 보류. "
                     f"액션 종료 후 재판정 (성공하면 자동 해소)")
            _db.mark_error_status(error_id, "new")
            return

        # ── 안전장치 2.7: 발행 우선 — 발행 중·직전이면 Tier 2 를 미룬다 (★ ERRORS [474]) ──
        #    Tier 2 는 한 세션이 10분 이상 LLM 차선을 점유한다. 발행이 도는 중에 이걸
        #    시작하면 발행이 LLM 을 못 잡아 lock_contention 으로 밀리고, 품질 게이트가
        #    통째로 스킵되는 사고로 이어진다 (2026-07-22 07:24 실제 발생).
        #    자가수리는 급하지 않다 — 발행이 끝난 뒤 retry_pending 이 다시 집어간다.
        try:
            from shared.llm import bg_defer_reason as _bg_defer
            _defer_why = _bg_defer()
        except Exception:
            _defer_why = ""
        if _defer_why:
            log.info(f"[GUARDIAN] #{error_id} Tier 2 보류 — {_defer_why} (발행 우선). "
                     f"status=new 로 되돌려 다음 기회에 재처리")
            _db.mark_error_status(error_id, "new")
            return

        # ── 안전장치 2.8: 자동수정 적격 판정 — `severity.is_auto_fixable()` (★ 결함2 배선) ──
        #    루트 CLAUDE.md 는 "심각도 분류 단일 진입점 = severity.classify()/is_auto_fixable()
        #    만 사용" 이라 박제해 두었는데, `is_auto_fixable` 은 **호출자가 0** 이었다
        #    (정의 1곳 + error_collector 의 미사용 import 1줄). 박제한 게이트가 실재하지 않았다.
        #    → 여기서 배선한다. *Tier 2 진입 게이트* 로 두는 이유:
        #      · Tier 1(LLM 0)은 architecture.SEVERITY_MATRIX 가 "critical 포함 전 심각도 시도"
        #        라고 규정한다. Tier 1 앞에 걸면 critical 이 Tier 1 조차 못 받아 매트릭스와 충돌.
        #      · 함수의 실제 의미도 "패턴으로 못 고치는 걸 LLM 까지 태울 값어치가 있나" 다
        #        (docstring: "나머지는 medium 만 LLM fallback").
        #    잠정·발행보류 게이트보다 *뒤* 에 둔다 — 저 둘은 'new 로 되돌림'(재판정 여지)인데
        #    이건 종결(wontfix)이라, 되돌릴 것을 먼저 되돌리고 나서 종결해야 한다.
        #
        #    ★ 2026-07-25 2차 — 영향도 재계산 (1차 보고의 모집단 표기가 틀려 기각됨).
        #      · 1차 보고: "최근30일 48건 중 1건(2%)" ← 모집단을 `status!='manual'` 이라 *적어놓고*
        #        실제로는 ignored 까지 뺀 더 좁은 집합을 셌다.
        #      · 명시 모집단 그대로(`status!='manual'`) 재측정: 최근30일 **324건 중 87건(26.9%)**,
        #        전기간 **751건 중 174건(23.2%)**. 차단분의 압도적 다수가 low/ConnectionError(30일 75건).
        #      · 그러나 이 게이트에 *실제로 도달하는* 모집단은 그게 아니다 — 위 '안전장치 0'
        #        (`is_transient` → ignored, 즉시 return)이 네트워크·Selenium 부류를 여기 오기 전에
        #        전부 걷어낸다. 그 걸 반영해 재측정하면 최근30일 **50건 중 1건(2.0%)**,
        #        전기간 **258건 중 3건(1.2%)** — 게다가 차단분 중 코드버그 타입은 **0건**.
        #        (차단 3건 = low/ExternalEditTest·low/RuntimeError·critical/SystemExit,
        #         critical 은 애초에 위 critical 분기에서 끝나므로 게이트와 무관.)
        #      · severity 상향(_escalate_severity)은 low→medium→high 방향뿐이라 게이트를 *느슨하게*
        #        만들 뿐 차단을 늘리지 않는다 → 상호작용도 안전한 방향.
        #      ⇒ 결론(영향 미미, 기본 ON) 유지. 단 근거 수치는 위 두 모집단 모두 명시한다.
        #
        #    ★ 잠재 사각지대 하나를 함께 막는다: `is_auto_fixable` 은 *타입 레벨* 판정이라
        #      low + 비(非)패턴 코드버그 타입(예: low/KeyError, low/JSONDecodeError)이면
        #      **진짜 코드 버그인데 Tier 2 를 못 받고 wontfix 로 종결**된다. 이건 이번 감사가
        #      쫓는 결함(진짜 버그를 조용히 버림)과 정확히 같은 부류다. severity.py 자신도
        #      "코드버그를 버리는 쪽이 그 반대보다 훨씬 위험" 이라고 박아 두었다.
        #      → 코드버그 타입(`severity.CODE_BUG_TYPES` 파생)은 게이트를 통과시킨다.
        #      실측 영향 0건(위 차단분 중 코드버그 타입 0) — 오늘 동작은 바뀌지 않고,
        #      미래에 low/KeyError 가 들어와도 조용히 버려지지 않는다.
        #      킬스위치 GUARDIAN_AUTOFIX_GATE_CODEBUG_PASS=0 → 이 예외만 끔.
        if _flag("GUARDIAN_AUTOFIX_GATE"):
            from JARVIS07_GUARDIAN.severity import is_auto_fixable, is_code_bug_type
            _codebug_pass = (_flag("GUARDIAN_AUTOFIX_GATE_CODEBUG_PASS")
                             and is_code_bug_type(error_type))
            if not is_auto_fixable(severity, error_type) and not _codebug_pass:
                log.info(f"[GUARDIAN] #{error_id} 자동수정 비적격 "
                         f"(severity={severity}, type={error_type}) — Tier 2 생략, 수동 검토")
                _notify_all(error_record, "not_auto_fixable", severity=severity)
                _db.mark_error_status(error_id, "wontfix")
                return
            if _codebug_pass and not is_auto_fixable(severity, error_type):
                log.info(f"[GUARDIAN] #{error_id} 게이트 예외 통과 — 코드버그 타입"
                         f"({error_type}, severity={severity})은 조용히 버리지 않는다")

        # ── 안전장치 3: Tier 2(LLM) 재시도 횟수 상한 (★ 사용자 박제 2026-07-06) ──
        #    'analyzing' 상태로 멈춘 오류가 job_retry_pending 에 의해 재투입될 때마다
        #    Tier 2(LLM) 를 무제한 재호출하는 사고(조용한 토큰 소모) 재발 방지.
        #    같은 error_id 가 이미 MAX_LLM_ATTEMPTS 회 시도됐으면 재시도 없이 종결.
        #
        #    ★ 2026-08-12 — **세는 시점** 을 실제 실행 뒤로 옮긴다 (판정은 그대로 여기 한 곳).
        #      촉점 게이트(`repair_budget`)가 막으면 LLM 은 0회 도는데, 호출 *앞* 에서 세면
        #      그 0회가 상한을 갉아먹어 진짜 버그가 한 번도 시도되지 못한 채 wontfix 로
        #      죽는다. 같은 병을 backlog 통로가 `deferred` 검사로 이미 고쳤다.
        #      상한 판정 자체는 새 게이트와 **겹치지 않는다** — 게이트는 지문·예산·쿨다운을,
        #      여기는 *이 error_id 를 몇 번 태웠나* 를 본다(축이 다르다).
        _attempts_prev = int((_db.get_error(error_id) or error_record).get("llm_attempts") or 0)
        if _attempts_prev >= _MAX_LLM_ATTEMPTS:
            log.warning(f"[GUARDIAN] #{error_id} Tier 2 시도 {_attempts_prev}회 — 상한({_MAX_LLM_ATTEMPTS}) 도달, 재시도 중단")
            # ★ 2026-08-14 (P2) — 알림은 **결론이 새로 났을 때만** 보낸다.
            #   들어올 때 이미 파킹 버킷(`wontfix`/`ignored`)이었다면 이 결론은 앞서
            #   전달된 것이고, 지금은 재발로 한 번 더 들여다본 것뿐이다. 매번 쏘면
            #   같은 문장이 재발 수만큼 나가고, 그러면 진짜 신호가 왔을 때 아무도 안 본다.
            #   판정 근거는 **입장 시 상태** — 새 플래그를 만들지 않고 DB 에서 파생한다(②).
            from JARVIS07_GUARDIAN.architecture import PARKED_STATUSES as _PARKED_ON_CAP
            _entry_status = str(error_record.get("status") or "")
            if _entry_status in _PARKED_ON_CAP:
                log.info(f"[GUARDIAN] #{error_id} 상한 결론은 이미 전달됨"
                         f"(입장 상태 {_entry_status}) — 알림 생략, 상태만 유지")
            else:
                _notify_all(error_record, "llm_cap_reached", severity=severity)
            _db.mark_error_status(error_id, "wontfix",
                                  f"Tier-2 {_attempts_prev}회 시도 후 상한 도달 "
                                  f"({_MAX_LLM_ATTEMPTS})")
            return

        # ── Tier 2: LLM 수정 — low 포함 전 심각도 ────────────────
        # low도 Tier 2까지 진행 → 학습 데이터 축적 → 다음엔 Tier 1 해결
        log.info(f"[GUARDIAN] #{error_id} Tier 1 실패 → Tier 2 (LLM, {severity}, "
                 f"시도 {_attempts_prev + 1}/{_MAX_LLM_ATTEMPTS})")
        _ledger_mark = sdk_repair_ledger_mark()
        fixed = _try_sdk_targeted_fix(error_id, error_record)
        _ran  = sdk_session_ran(_ledger_mark, error_record)
        attempts = _db.bump_llm_attempts(error_id) if _ran else _attempts_prev

        if fixed:
            _notify_all(error_record, "success", tier=2, severity=severity)
        elif _ran:
            _notify_all(error_record, "failed", severity=severity)
        else:
            # 게이트가 막은 것은 '수정 실패' 가 아니다 — 사유·예산·다음 가능 시각은
            # 게이트가 이미 텔레그램·장부에 남겼다. 여기서 '실패' 알림을 겹쳐 보내면
            # 사람이 원인을 코드에서 찾게 된다. 로그로만 흔적을 남긴다.
            log.info(f"[GUARDIAN] #{error_id} 자율 SDK 수리 게이트에 막힘 — 시도 미소모 "
                     f"({attempts}/{_MAX_LLM_ATTEMPTS}). 사유는 게이트 알림 참조")

    except Exception as e:
        log.error(f"[GUARDIAN] 오케스트레이터 오류: {e}")
    finally:
        if _hb_stop is not None:
            _hb_stop.set()   # ★ 하트비트 종료 — 이후 신호 끊기면 수확기가 정상 회수
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

# ── 수리 비대상 오류의 종착 버킷 — 세 통로가 쓰는 **한 문** (2026-08-14) ────────
#
# ★ 왜 함수인가 (①)
#   `_orchestrate` · `self_heal_known_errors` · `deep_audit_backlog` 세 곳이 각자
#   `if is_transient(...): mark_error_status(id, "ignored")` 를 적고 있었다. 한 곳만
#   고치면 나머지 두 통로로 그대로 샌다 — 이 저장소가 반복해서 겪은 형태다(③).
#
# ★ 무엇이 달라지나
#   종전엔 "코드로 못 고침" 하나로 `ignored`(재수집 집합 밖) 를 결정했다. 이제
#   `severity.is_dismissable()` 이 "사람도 볼 필요 없는가" 까지 확인하고, 갈리는
#   구간(캡차·발행결손)은 `wontfix` 로 남긴다 — `UNRESOLVED_STATUSES` 안이라
#   sweep 이 계속 집어 보고, `_reopen_if_recurred` 도 재발을 되살릴 수 있다.
#   두 버킷 모두 Tier-1·Tier-2 를 **태우지 않는다**(호출자가 즉시 continue).
def _park_non_code(error_id: int, error_record: dict) -> str:
    """수리 비대상이면 버킷에 넣고 그 상태명을 돌려준다. 수리 대상이면 "" (계속 진행).

    반환: "ignored" (자동 종결) / "wontfix" (사람이 봐야 함 — 재조회 유지) / "" (수리 대상)
    """
    from shared import db as _db
    from JARVIS07_GUARDIAN.severity import (companions_of, is_dismissable, is_transient,
                                            kind_of)
    et  = str(error_record.get("error_type") or "")
    msg = str(error_record.get("message") or "")
    src = str(error_record.get("source") or "")
    k   = kind_of(error_record)
    comp = companions_of(error_record)
    if not is_transient(et, msg, src, kind=k, companions=comp):
        return ""
    if is_dismissable(et, msg, src, kind=k, companions=comp):
        _db.mark_error_status(error_id, "ignored",
                              f"일시적/외부/제어흐름 오류 — 자동수정 비대상 ({et})")
        return "ignored"
    # 코드로는 못 고치지만 **사람이 봐야 한다** — 종결하지 않고 재조회 대상에 남긴다.
    _db.mark_error_status(error_id, "wontfix",
                          f"코드 수정 불가 · 사람 개입 필요 — 재조회 대상 유지 ({et})")
    return "wontfix"


def tier2_blocked_reason(error_record: dict) -> "str | None":
    """이 오류에 **Tier-2(LLM)를 태우면 안 되는 사유** — 없으면 None.

    ★ 왜 함수로 뺐나 (2026-08-09 3차 적대적 검증 — ①·③)
      `_orchestrate` 는 Tier-2 앞에 세 개의 문을 세워 뒀는데(critical 은 Tier-1 까지만 ·
      보안 파일 차단 · 재시도 남은 잠정실패는 보류), `deep_audit_backlog` 루프에는
      **셋 다 없었다**. 실측 재현: `_orchestrate` 가 LLM 0회로 돌려보낸 바로 그 3행이
      backlog 통로에서는 전원 `invoke_text("guardian")` 을 열었다.
      2차 수정 때 `bump_llm_attempts` 한 줄만 옮겨 "상한을 전 통로에 걸었다" 고 했지만,
      옮긴 것은 *숫자* 였고 **상한 앞의 판단** 은 여전히 한쪽에만 있었다.
      판단이 두 벌이면 반드시 갈라진다 — 그래서 판단을 하나로 만든다.

    사유 문자열은 그대로 `resolution` 에 쓰이므로 사람이 읽을 수 있어야 한다.
    """
    if not isinstance(error_record, dict):
        return None
    module = error_record.get("module", "") or ""
    if _is_deny_path(module):
        return f"보안 파일이라 자동수정 금지 — {module}"
    if (error_record.get("severity") or "") == "critical":
        return "critical — Tier-2 생략하고 수동 검토로 (architecture.SEVERITY_MATRIX)"
    try:
        from JARVIS07_GUARDIAN.severity import is_deterministic_code_error as _det
    except Exception:
        _det = lambda _t: False        # noqa: E731 — 파생 실패 시 보수적으로 '비결정론'
    if error_record.get("provisional") and not _det(error_record.get("error_type", "")):
        return "잠정 실패(재시도 남음) + 비결정론 — 재시도가 끝난 뒤 판정"
    return None


def _collect_unresolved(limit: int) -> list:
    """미해결 오류 수집 — **공개 단일 질의** `db.list_unresolved()` 위임 (최신순).

    ★ **시도 상한을 존중한다** (2026-08-09 2차 적대적 검증)
      `MAX_LLM_ATTEMPTS` 로 시도를 막아 놓고, 상한에 도달해 `wontfix` 가 된 오류를
      여기서 **다시 끌어와** 심층 감사마다 Tier-2 를 또 열고 있었다 — 상한이 무력이다.
      실측: 미해결 64건 전원이 `wontfix` 이고 그중 8월 이전이 46건. 7/13 워치독 freeze
      같은 *지나간 사건* 은 코드 패치로 되살아나지 않는데 매 회차 LLM 세션을 태웠다.

    ★ **필터를 여기서 하지 않는다** (2026-08-14 — ③원칙)
      종전엔 `list_errors` 로 상태별로 긁은 뒤 파이썬에서 상한을 걸렀다. 두 가지가 샜다:
        ① LIMIT 이 필터보다 먼저라, 앞쪽이 상한 도달분으로 차면 고칠 수 있는 행이
           창 밖으로 밀려 **아무도 못 본다**.
        ② 같은 필터를 다른 소비자(`job_retry_pending`)는 안 걸고 있었다 — 한 통로만
           고치면 다른 통로로 새는 그 형태다.
      → 상태 목록·상한·백오프는 전부 문(`db.list_unresolved`) 안에서 SQL 로 처리한다.
        여기에는 숫자도 상태 문자열도 적지 않는다.

    ★ **실패를 '수리 0건' 으로 위장하지 않는다** (2026-08-14 P2)
      이 함수는 이번 변경으로 `self_heal_known_errors` · `deep_audit_backlog`
      **두 통로의 유일한 문** 이 됐다. 종전엔 모든 예외를 삼키고 `[]` 를 `log.debug` 로만
      남겼다 — 데몬 로그 레벨이 INFO 이상이면 **어디에도 안 남는다**.
      게다가 안쪽 `db.list_unresolved` 는 `next_eligible_at` 컬럼을 WHERE 에서 직접
      참조하는데, 그 컬럼 추가도 마이그레이션의 무음 `try/except` 안에 있었다.
      두 무음이 겹치면 "미해결 0건 — 고칠 게 없음" 이라는 **정반대 결론** 이 조용히 선다.
      → 이제 `log.warning` + GUARDIAN 오류 기록으로 올린다. 기록 자체가 실패해도
        (DB 가 죽은 상황) 로그는 남는다.
    """
    try:
        from shared import db as _db
        return _db.list_unresolved(limit)
    except Exception as e:
        log.warning("[GUARDIAN/unresolved] 미해결 조회 실패 — 이번 회차 수리 대상이 "
                    "0건으로 보인다(실제로 없는 것이 아니다): %s: %s", type(e).__name__, e)
        try:
            from JARVIS07_GUARDIAN.error_collector import report
            report("guardian", e, module="JARVIS07_GUARDIAN/guardian_agent.py",
                   func_name="_collect_unresolved",
                   error_type="GuardianUnresolvedQueryFailed")
        except Exception as rec_err:  # noqa: BLE001 — 기록 실패가 수리를 막지 않는다
            log.warning("[GUARDIAN/unresolved] 조회 실패를 기록하지도 못했다: %s", rec_err)
        return []


def selfheal_budget_sec() -> float:
    """발행 前 자체수리 페이즈의 **절대 시간 상한**(초) — 기존 예산에서 파생한다(②).

    ★ 왜 필요한가 (2026-08-14 2차): 이 페이즈는 발행(07:00·21:00) **직전 동기** 실행이고
      텔레그램에 스스로 '수초' 라고 광고하는데, 실제로는 **건수 상한(limit)만 있고 시간
      상한이 없었다**. 건당 비용은 고정이 아니다 — import 검증·재현검증이 각각 상한까지
      갈 수 있어서, 수리 대상이 늘수록(= 학습이 쌓일수록) 발행 앞의 지연이 함께 자란다.
      상한이 없으면 "수초" 는 약속이 아니라 희망이다.
    ★ 숫자를 박지 않는다: 이 페이즈가 앞서는 것이 *플랫폼당 발행 액션*
      (`watchdog.BLOG_ACTION_DEADLINE_SEC`)이므로 그 예산의 5% 를 넘지 않는다 —
      전주곡이 본편을 잡아먹지 않는다. 데드라인이 바뀌면 자동 추종.
    ★ 파생이 끊기면 드러난다 (2026-08-17): 종전 폴백 `120.0` 은 정상 파생값
      (2400/20)과 **같은 숫자** 라, `BLOG_ACTION_DEADLINE_SEC` 가 개명·이동해도 값이
      그대로여서 파생 단절을 구별할 수 없었다. 이제 `severity.derived_or` 가 드러낸다.
      값은 보수적으로 유지한다 — 여기서 예산을 줄이면 발행 前 자체수리가 조용히
      일을 덜 하게 되고(수리 누락), 늘리면 발행을 지연시킨다. 둘 다 무음 열화다.
    무배포 조정: `GUARDIAN_SELFHEAL_BUDGET`.
    """
    from JARVIS07_GUARDIAN.severity import derived_or

    env = (os.getenv("GUARDIAN_SELFHEAL_BUDGET") or "").strip()
    if env:
        try:
            return max(5.0, float(env))
        except ValueError:
            pass

    def _derive() -> float:
        from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC as _d
        return max(30.0, float(_d) / 20)

    return derived_or("selfheal/watchdog.BLOG_ACTION_DEADLINE_SEC", _derive, 120.0)


def self_heal_known_errors(limit: int = 40) -> dict:
    """발행 전 Tier-1 자체수리 sweep — LLM 호출 0.

    미해결 오류(new·wontfix) 중 *학습 패턴·정적 fixer·Bandit 로 즉시 고칠 수 있는 것만* 수리.
    Tier 2(LLM) 절대 호출 안 함 — 못 고치면 그대로 남겨 새벽 심층 감사(job_deep_audit)로 위임.
    apply_fix 성공 시 *실제 오류 지문* 으로 record_pattern_hit + Bandit 양의 보상 자동 기록.

    ★ 시간 상한 `selfheal_budget_sec()` — 넘기면 남은 건은 **손대지 않고** 심층 감사로 넘긴다.
      여기서 더 붙잡는 것은 발행을 미루는 것과 같다(이 페이즈는 발행 직전 동기 실행이다).

    Returns: {"fixed", "skipped", "ignored", "human", "scanned", "deferred"}
    """
    try:
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j07", "Tier-1 자체수리", ttl=300)
    except Exception:
        pass
    import time as _time
    fixed = skipped = ignored = human = 0
    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix
    except Exception as e:
        log.warning(f"[GUARDIAN/selfheal] import 실패: {e}")
        return {"fixed": 0, "skipped": 0, "ignored": 0, "human": 0, "scanned": 0}

    rows = _collect_unresolved(limit)
    _budget  = selfheal_budget_sec()
    _t0      = _time.time()
    deferred = 0
    for er in rows:
        eid = er.get("id")
        et  = er.get("error_type", "")
        if _time.time() - _t0 >= _budget:
            # ★ 남은 건은 *손대지 않는다*. 오류는 그대로 남아 심층 감사·다음 스윕이 데려간다.
            deferred += 1
            continue
        try:
            # ★ 수리 비대상 판정·버킷 배정은 `_park_non_code` 단독 (①·③).
            _bucket = _park_non_code(eid, er)
            if _bucket:
                ignored += 1
                human += 1 if _bucket == "wontfix" else 0
                continue
            analysis = analyze(er)  # Tier 1 전용 (패턴·Bandit·정적, LLM 0)
            if analysis.get("fixable") and apply_fix(eid, analysis, mark_wontfix=False):
                fixed += 1
            else:
                skipped += 1  # LLM 호출 안 함 — 새벽 심층 감사로 위임
        except Exception as e:
            log.debug(f"[GUARDIAN/selfheal] #{eid} 처리 예외: {e}")
            skipped += 1

    # ★ '무시' 와 '사람 확인 대기' 를 한 숫자로 뭉개지 않는다 — 후자는 아직 살아 있는 일이다.
    log.info(f"[GUARDIAN/selfheal] 발행 전 Tier-1 sweep — 수리 {fixed} / 보류 {skipped} / "
             f"수리비대상 {ignored}(사람확인 {human}) (스캔 {len(rows)}"
             + (f", 예산 {_budget:.0f}s 초과로 미처리 {deferred}" if deferred else "") + ")")
    if deferred:
        log.warning(f"[GUARDIAN/selfheal] 시간 예산 {_budget:.0f}s 초과 — {deferred}건을 "
                    f"심층 감사(j07_deep_audit)로 넘긴다 (발행을 미루지 않는다)")
    try:
        from shared.pipeline_activity import clear_busy as _cb
        _cb("j07")
    except Exception:
        pass
    return {"fixed": fixed, "skipped": skipped, "ignored": ignored,
            "human": human, "scanned": len(rows), "deferred": deferred}


def deep_audit_backlog(limit: int = 40, max_llm: int = 15) -> dict:
    """새벽 심층 감사 1부 — 미해결 오류 backlog 를 Tier 1 → Tier 2(LLM) 로 처리.

    ★ 핵심: Tier 2 수정도 apply_fix 경유 → *실제 오류 지문* 으로 record_pattern_hit + Bandit.
       (_try_sdk_targeted_fix 의 AutoRepairFix 합성 지문과 달리, 다음 발행 전 sweep 이 재사용 가능
        → 학습 루프가 실제로 조여짐.)
    max_llm: 1회 실행당 Tier 2(LLM) 시도 상한 (시간 폭주 방지).

    Returns: {"fixed_t1", "fixed_t2", "failed", "ignored", "scanned", "llm_used"}
    """
    fixed_t1 = fixed_t2 = failed = ignored = human = llm_used = 0
    try:
        from shared import db as _db
        from JARVIS07_GUARDIAN.error_analyzer import analyze, analyze_llm_only
        from JARVIS07_GUARDIAN.error_fixer import apply_fix
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] import 실패: {e}")
        return {"fixed_t1": 0, "fixed_t2": 0, "failed": 0, "ignored": 0, "human": 0,
                "scanned": 0, "llm_used": 0}

    rows = _collect_unresolved(limit)
    for er in rows:
        eid = er.get("id")
        et  = er.get("error_type", "")
        try:
            # ★ 수리 비대상 판정·버킷 배정은 `_park_non_code` 단독 (①·③).
            _bucket = _park_non_code(eid, er)
            if _bucket:
                ignored += 1
                human += 1 if _bucket == "wontfix" else 0
                continue
            a1 = analyze(er)  # Tier 1 먼저 (LLM 0)
            if a1.get("fixable") and apply_fix(eid, a1, mark_wontfix=False):
                fixed_t1 += 1
                continue
            # ★ Tier-2 앞 판단은 `_orchestrate` 와 **같은 함수** 를 쓴다 (①·③).
            #   종전엔 이 루프에 critical·보안파일·잠정실패 문이 하나도 없어,
            #   `_orchestrate` 가 LLM 0회로 돌려보낸 오류가 여기선 전부 세션을 열었다.
            _blocked = tier2_blocked_reason(er)
            if _blocked:
                log.info(f"[GUARDIAN/deepaudit] #{eid} Tier-2 비대상 — {_blocked}")
                _db.mark_error_status(eid, "wontfix", _blocked)
                ignored += 1
                continue
            if llm_used >= max_llm:
                failed += 1
                continue
            a2 = analyze_llm_only(er)  # Tier 2 — apply_fix 경유 *실제 지문* 학습
            # ★ **LLM 이 실제로 돌았을 때만 센다** (2026-08-09 3차 적대적 검증)
            #   종전엔 호출 *앞* 에서 셌는데, `analyze_llm_only` 는 발행 중이면 맨 앞에서
            #   LLM 없이 돌아온다("다음 심층 감사로 위임"). 그러면 **실제 호출 0회로**
            #   상한이 소진되고 `_collect_unresolved` 가 그 행을 영구 제외한다 —
            #   고칠 기회를 한 번도 안 주고 버리는 셈이다. API 예외도 같은 결과였다.
            if not (a2 or {}).get("deferred"):
                llm_used += 1
                _db.bump_llm_attempts(eid)
            else:
                log.info(f"[GUARDIAN/deepaudit] #{eid} LLM 미실행(위임) — 시도로 세지 않는다")
                continue
            if a2.get("fixable") and apply_fix(eid, a2, mark_wontfix=True):
                fixed_t2 += 1
            else:
                failed += 1
        except Exception as e:
            log.debug(f"[GUARDIAN/deepaudit] #{eid} 처리 예외: {e}")
            failed += 1

    log.info(f"[GUARDIAN/deepaudit] backlog — T1 {fixed_t1} / T2(LLM {llm_used}) {fixed_t2} / 실패 {failed} / 무시 {ignored} (스캔 {len(rows)})")
    return {"fixed_t1": fixed_t1, "fixed_t2": fixed_t2, "failed": failed,
            "ignored": ignored, "human": human, "scanned": len(rows), "llm_used": llm_used}


# ── ★ 격리 버킷(ignored) 집계·추세 보고 (결함3 — 2026-07-25) ─────────────
#
#  현업 표준: 격리한 것은 *별도 네임스페이스로 계량* 한다 (Envoy `retry.*`,
#  Sentry crash-free rate). 우리는 격리(`ignored`)만 하고 아무도 안 봤다 —
#  실측 440건, 그중 절반은 왜 무시됐는지 기록조차 없었다.
#  → 걸러낸 것을 *세어서* 보고한다. 특히 **정규식에 걸린 코드버그 타입** 은
#    오탐(진짜 버그를 조용히 버림)의 조기경보다.
#
#  ② 동적 설계: 무시 사유 목록을 여기에 나열하지 않는다. `severity.is_transient()`
#     **공개 함수를 인자별로 프로빙** 해서 어느 필터가 걸었는지 런타임 파생한다
#     (severity.py 의 필터가 늘거나 바뀌면 이 보고가 자동으로 따라온다).
#  ① 단일 진입점: 새 잡·새 알림 경로를 만들지 않는다 — 기존 `job_deep_audit`
#     말미 + 기존 `send_tg` + 기존 `/status` 섹션에 얹는다.

def _ignore_reason(rec: dict) -> str:
    """이 오류를 *어느 필터* 가 걸렀는지 — 공개 API 프로빙으로 런타임 파생."""
    try:
        from JARVIS07_GUARDIAN.severity import companions_of, is_transient, kind_of
    except Exception:
        return "미분류"
    et  = rec.get("error_type") or ""
    msg = rec.get("message") or ""
    src = rec.get("source") or ""
    k   = kind_of(rec)
    # ★ **먼저 실제 결정을 그대로 묻는다** (2026-08-09 2차 적대적 검증)
    #   종전엔 다리를 하나씩 물어(`한 인자만`) 어느 필터가 걸렀는지 추정했는데,
    #   그 방식은 `is_transient` 의 **조기 return 우선순위를 무시** 한다.
    #   실측: 봉투 kind + companions 없음 → 실제 판정은 *격리 안 함*(봉투 분기가
    #   먼저 return) 인데, 설명기는 그 분기를 건너뛰고 메시지 정규식 다리가 True 인 것을
    #   보고 "정규식:메시지" 라 답했다 — 격리 버킷 보고 **91행 전부**가 그랬다.
    #   설명이 결정과 어긋나면 사람이 엉뚱한 곳을 고친다.
    _comp = companions_of(rec)
    if not is_transient(et, msg, src, kind=k, companions=_comp):
        return "격리 대상 아님(현행 규칙)"
    if k and is_transient("", "", "", kind=k, companions=_comp):
        return f"kind:{k}"
    if src and is_transient("", "", src, ""):
        return f"source:{src}"
    if et and is_transient(et, "", "", ""):
        return f"타입:{et}"
    if msg and is_transient("", msg, "", ""):
        return "정규식:메시지"
    if rec.get("provisional"):
        return "잠정만료"          # job_retry_pending 1-B 경로
    return "기타(수동·구경로)"


def _looks_like_code_bug(error_type: str) -> bool:
    """코드 결함 타입인가 — `severity.CODE_BUG_TYPES` 파생 집합에서 *그대로* 가져온다.

    ★ 2026-07-25 2차 수정 — 종전 구현은 `is_deterministic_code_error(et) or
      is_auto_fixable("low", et)` 라는 *간접 프로빙* 이었다. 그 조합은
      `_PATTERN_FIXABLE_TYPES ∪ DETERMINISTIC_CODE_ERROR_TYPES` 까지만 답을 주고,
      1차 수정이 신설한 세 번째 근거(`_CATEGORY_LABELS` 여집합 — KeyError·IndexError·
      JSONDecodeError·ZeroDivisionError 등)를 **빠뜨린다**. 즉 같은 질문에 답이 두 벌이었다(① 위반).
      → 이제 severity 가 이미 파생해 둔 단일 집합의 공개 판정자(`is_code_bug_type`,
        내부적으로 `CODE_BUG_TYPES` 조회 + 점표기 정규화)만 쓴다. severity 에 타입이
        늘면 이 경보가 자동으로 따라온다(② 동적 설계).
    """
    try:
        from JARVIS07_GUARDIAN.severity import is_code_bug_type
    except Exception:
        return False
    return bool((error_type or "").strip()) and is_code_bug_type(error_type)


def ignored_bucket_report(days: int = 0, limit: int = 3000) -> dict:
    """격리 버킷 집계 — 사유별 분포 · 추세 · 오탐 조기경보.

    Returns: {"window_days","total","prev_total","delta_pct","by_reason",
              "code_bug_ignored","regex_code_bug"(별칭),"fp_scope","fp_in_window",
              "no_resolution","top_types","lines"}

    ★ 2026-07-25 2차 수정 — 경보의 사각지대 2개를 닫는다.
      (a) **버킷 한정 해제**: 종전엔 `reason == "정규식:메시지"` 일 때만 교차검사했다.
          그런데 1차 수정이 severity 의 옛 provider 정규식을 삭제하면서 대표 물증 #582
          (`ImportError: cannot import name 'HuggingFaceProvider'`)가 `기타` 버킷으로
          이동 → **경보에서 사라졌다**. 오탐의 본질은 '어느 필터가 걸렀나' 가 아니라
          '코드버그가 ignored 에 있다' 는 사실이므로 **전 버킷** 을 검사한다.
          (걸러낸 필터 이름은 버리지 않고 각 항목의 `reason` 으로 함께 보고한다 — 진단용.)
      (b) **창 한정 해제**: 조용히 버려진 코드버그는 시간이 지난다고 해결되지 않는다.
          추세 집계는 `win` 일 창을 유지하되, 오탐 스캔은 격리 버킷 *전체* 를 훑는다
          (#582 는 55일 전 — 30일 창에서는 영영 안 보인다). 실측 전기간 격리 440건 중
          코드버그 타입은 5건뿐이라 비용·소음 모두 무시할 수준.
          킬스위치 `GUARDIAN_IGNORED_FP_ALLTIME=0` → 종전처럼 창 안만 검사.
    """
    from collections import Counter
    win = days or _ERROR_STATS_WINDOW_DAYS
    out = {"window_days": win, "total": 0, "prev_total": 0, "delta_pct": None,
           "by_reason": {}, "code_bug_ignored": [], "regex_code_bug": [],
           "fp_scope": "", "fp_in_window": 0, "no_resolution": 0,
           "top_types": [], "lines": []}
    _fp_alltime = _flag("GUARDIAN_IGNORED_FP_ALLTIME")
    try:
        from shared.db import get_db
        with get_db() as conn:
            tcol = _error_time_col(conn)          # ② 스키마 런타임 파생
            cols = ["id", "source", "module", "error_type", "message",
                    "context", "severity", "resolution", "provisional", tcol]
            sel  = ", ".join(cols)
            rows = conn.execute(
                f"SELECT {sel} FROM error_log WHERE status = 'ignored' "
                f"AND {tcol} >= datetime('now', ?) ORDER BY {tcol} DESC LIMIT ?",
                (f"-{win} days", limit),
            ).fetchall()
            prev = conn.execute(
                f"SELECT COUNT(*) FROM error_log WHERE status = 'ignored' "
                f"AND {tcol} >= datetime('now', ?) AND {tcol} < datetime('now', ?)",
                (f"-{win * 2} days", f"-{win} days"),
            ).fetchone()[0]
            # 오탐 스캔 대상 — 기본은 격리 버킷 전체(창 무관), 킬스위치 시 창 안만
            fp_rows = rows if not _fp_alltime else conn.execute(
                f"SELECT {sel} FROM error_log WHERE status = 'ignored' "
                f"ORDER BY {tcol} DESC LIMIT ?", (limit,),
            ).fetchall()
    except Exception as e:
        _note_safety_fail("ignored_report", e)
        return out

    reasons = Counter()
    types   = Counter()
    no_res  = 0
    win_ids: set = set()
    for r in rows:
        rec = dict(zip(cols, r))
        win_ids.add(rec.get("id"))
        reasons[_ignore_reason(rec)] += 1
        types[rec.get("error_type") or "?"] += 1
        if not (rec.get("resolution") or "").strip():
            no_res += 1

    # ★ 오탐 조기경보 — **전 버킷**. 어느 필터가 걸렀든 코드버그 타입이면 경보다.
    fp: list[dict] = []
    in_win = 0
    for r in fp_rows:
        rec = dict(zip(cols, r))
        if not _looks_like_code_bug(rec.get("error_type")):
            continue
        recent = rec.get("id") in win_ids
        in_win += 1 if recent else 0
        fp.append({"id": rec.get("id"), "error_type": rec.get("error_type"),
                   "module": rec.get("module"), "reason": _ignore_reason(rec),
                   "at": str(rec.get(tcol) or "")[:10], "in_window": recent,
                   "message": (rec.get("message") or "")[:100]})

    total = len(rows)
    out.update(total=total, prev_total=prev, by_reason=dict(reasons.most_common()),
               code_bug_ignored=fp, regex_code_bug=fp,   # 별칭 — 기존 소비자 호환
               fp_scope=("전기간" if _fp_alltime else f"{win}일"),
               fp_in_window=in_win, no_resolution=no_res,
               top_types=types.most_common(5))
    if prev:
        out["delta_pct"] = round((total - prev) * 100.0 / prev, 1)

    trend = ("추세 비교 불가(이전 창 0건)" if not prev
             else f"이전 {win}일 {prev}건 → {'▲' if total > prev else '▼' if total < prev else '='}"
                  f" {out['delta_pct']:+.1f}%")
    lines = [f"🧺 *[GUARDIAN] 격리 버킷(ignored) {win}일 집계* — {total}건",
             f"　추세: {trend}"]
    if reasons:
        lines.append("　사유별: " + " · ".join(f"{k} {v}" for k, v in reasons.most_common(6)))
    if no_res:
        lines.append(f"　⚠️ 무시 사유 미기록(resolution NULL): {no_res}건 "
                     f"— 왜 버렸는지 추적 불가")
    if fp:
        lines.append(f"　🚨 *오탐 의심 {len(fp)}건* (스캔범위 {out['fp_scope']}, "
                     f"창 내 신규 {in_win}건) — 격리됐는데 타입은 코드 결함:")
        for it in fp[:5]:
            lines.append(f"　　#{it['id']} [{it['at']}] {it['error_type']} @ {it['module']} "
                         f"— 사유 '{it['reason']}' · {it['message'][:50]}")
        if len(fp) > 5:
            lines.append(f"　　… 외 {len(fp) - 5}건")
    else:
        lines.append(f"　✅ 격리분({out['fp_scope']})에 코드결함 타입 혼입 없음")
    out["lines"] = lines
    log.info(f"[GUARDIAN/ignored] {win}일 {total}건 / 사유 {dict(reasons.most_common(5))} "
             f"/ 오탐의심 {len(fp)}건(범위 {out['fp_scope']}, 창내 {in_win}) / 사유미기록 {no_res}")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  GUARDIAN SLO — 지표 3개. **경보 없이 값만** (2026-08-14)
#
#  ★ 왜 3개뿐인가
#    지표가 많으면 아무도 안 본다. "자가수리가 실제로 일하고 있는가" 는 셋이면 답이 난다:
#      ① 검증된 코드수정 건수/주 — 고쳤다고 *증명된* 것만 (T1 의 `[verification=...]` 파생)
#      ② 재발률                  — 고친 것이 다시 나는가 (고쳤다는 말의 반증)
#      ③ 밴딧 마지막 갱신 경과시간 — 학습이 살아 있는가
#
#  ★ 임계값을 리터럴로 박지 않는다 (선례: `pattern_fixer.calibrate_semantic_threshold`)
#    "주 3건 미만이면 경보" 같은 숫자는 첫날부터 낡는다. 최근 창의 **실측 분포 백분위**
#    에서 파생한다 — 표본이 모자라면 임계를 내지 않는다(모르면 모른다고 한다).
#
#  ★ 처음에는 **경보를 켜지 않는다**
#    타이트하게 잡으면 즉시 상시 경보가 되고, 상시 경보는 무시된다 — 이 저장소가 이미
#    겪은 패턴이다. 기본은 값과 임계 *후보* 만 노출하고, 발효는 사람이 켠다:
#      `GUARDIAN_SLO_ALERT=1` → `alerts` 배열이 채워진다(그전엔 항상 빈 배열).
# ══════════════════════════════════════════════════════════════════════════════

SLO_ALERT_ENV = "GUARDIAN_SLO_ALERT"
_SLO_MIN_SAMPLES = 4          # 백분위를 낼 최소 관측 수(주 단위 창) — 그 아래면 임계 없음


def _pct(values: list, q: float) -> "float | None":
    """표본의 q 백분위 (0~100). 표본이 모자라면 None — 없는 임계를 지어내지 않는다."""
    vals = sorted(float(v) for v in values if v is not None)
    if len(vals) < _SLO_MIN_SAMPLES:
        return None
    import statistics as _st
    try:
        # 100분위 → 그 경계값. statistics 는 n=100 컷포인트 99개를 준다.
        cuts = _st.quantiles(vals, n=100, method="inclusive")
        i = min(max(int(round(q)) - 1, 0), len(cuts) - 1)
        return round(cuts[i], 3)
    except Exception:
        return None


def _slo_fingerprint(row: dict) -> str:
    """재발 판정용 지문 — 정규화는 `pattern_fixer._normalize_message` 재사용(①)."""
    et = str(row.get("error_type") or "")
    msg = str(row.get("message") or "")
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import _normalize_message
        msg = _normalize_message(msg)
    except Exception:
        msg = msg[:80]
    return f"{et}::{msg}"


def guardian_slo(days: int = 30) -> dict:
    """자가수리 SLO 3종 — 값 + 실측 분포에서 파생한 임계 후보. 기본은 경보 없음.

    반환:
      {"window_days", "verified_fixes_week", "recurrence_rate", "bandit_stale_h",
       "thresholds": {...}, "alerts": [...], "measured": bool}
    """
    import os as _os
    out: dict = {"window_days": int(days), "alerts": [], "measured": False}
    try:
        from shared import db as _db
    except Exception as e:  # noqa: BLE001
        out["error"] = f"db 미가용: {e}"
        return out

    # ── ① 검증된 코드수정 — 'fixed' 가 아니라 *검증 태그* 에서 파생한다(T1).
    #    상태 문자열을 여기 적지 않는다: 정본 목록은 error_fixer 소유.
    try:
        from JARVIS07_GUARDIAN.error_fixer import VERIFY_GONE
        _tag = f"[verification={VERIFY_GONE}]%"
    except Exception:
        _tag = None
    # ★ 관측용 합성 행 배제 (2026-08-14 P2) — `verification_tag_effective()` 프로브가
    #   `error_log` 에 넣었다 지우는 행이 바로 이 지표(`[verification=gone]` 태그)를
    #   **그대로 부풀렸다**. 프로브가 자기 성적표를 쓰는 형태. 조건의 주인은 shared.db.
    _excl = _db.synthetic_exclusion_sql()
    _and = _db.and_sql
    weekly_ok: list = []
    if _tag:
        try:
            with _db.get_db() as conn:
                rows = conn.execute(
                    "SELECT strftime('%Y-%W', COALESCE(fixed_at, timestamp)) wk, COUNT(*) n "
                    "FROM error_log WHERE " + _and(
                        "resolution LIKE ?",
                        f"COALESCE(fixed_at, timestamp) >= date('now','-{int(days)} days')",
                        _excl) + " "
                    "GROUP BY wk ORDER BY wk", (_tag,)).fetchall()
            weekly_ok = [int(r["n"]) for r in rows]
        except Exception as e:  # noqa: BLE001
            out["verified_error"] = str(e)[:120]
    out["verified_fixes_week"] = weekly_ok[-1] if weekly_ok else 0
    out["verified_weeks_observed"] = len(weekly_ok)

    # ── ② 재발률 — 고쳤다고 종결한 지문이 그 뒤에 다시 들어왔는가.
    recurred = total_fixed = 0
    weekly_rec: list = []
    try:
        from JARVIS07_GUARDIAN.architecture import FIXED_STATUSES
        _in = ",".join("?" for _ in FIXED_STATUSES)
        with _db.get_db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, error_type, message, status, timestamp, fixed_at FROM error_log "
                "WHERE " + _and(f"timestamp >= date('now','-{int(days)} days')", _excl)
                + " ORDER BY id").fetchall()]
            fixed_rows = [r for r in rows if (r.get("status") or "") in FIXED_STATUSES]
        later: dict = {}
        for r in rows:                       # 지문별 *마지막* 발생 시각
            later[_slo_fingerprint(r)] = max(
                later.get(_slo_fingerprint(r), ""), str(r.get("timestamp") or ""))
        _wk: dict = {}                       # 주 → [재발수, 전체수] — 백분위용 분포
        for r in fixed_rows:
            total_fixed += 1
            closed = str(r.get("fixed_at") or r.get("timestamp") or "")
            hit = bool(closed and later.get(_slo_fingerprint(r), "") > closed)
            recurred += 1 if hit else 0
            k = closed[:7] + "-" + str((int(closed[8:10] or 1) - 1) // 7)   # 월-주차
            b = _wk.setdefault(k, [0, 0])
            b[0] += 1 if hit else 0
            b[1] += 1
        weekly_rec = [round(a / b, 3) for a, b in _wk.values() if b]
    except Exception as e:  # noqa: BLE001
        out["recurrence_error"] = str(e)[:120]
    out["recurrence_rate"] = round(recurred / total_fixed, 3) if total_fixed else None
    out["recurrence_base"] = total_fixed

    # ── ③ 밴딧 생존 — 판정은 `bandit.stats()` 단독(사본 금지).
    try:
        from JARVIS07_GUARDIAN.bandit import stats as _bstats
        _b = _bstats()
        out["bandit_stale_h"] = round(float(_b.get("last_update_h", -1)), 1)
    except Exception as e:  # noqa: BLE001
        out["bandit_stale_h"] = None
        out["bandit_error"] = str(e)[:120]

    # ── 임계 *후보* — 최근 창 실측 분포에서 파생. 표본 부족이면 None.
    #    ① 은 '적으면 이상' 이라 하위 백분위, ② 는 '많으면 이상' 이라 상위 백분위.
    #    ③ 의 분포는 **밴딧을 실제로 먹이는 사건** 의 간격이다 — `apply_fix` 성공, 즉
    #       파일을 고치고 종결된 행. 종전 초안은 `fixed_at IS NOT NULL` 전체를 썼는데
    #       그 4,700건은 대부분 `manual`(변경 기록)이라 간격 p90 이 0.25시간으로 나왔다.
    #       상관 없는 사건으로 만든 임계는 리터럴보다 나쁘다(측정한 척을 한다).
    gaps_h: list = []
    try:
        from JARVIS07_GUARDIAN.architecture import FIXED_STATUSES as _FS
        _fin = ",".join("?" for _ in _FS)
        with _db.get_db() as conn:
            ts = [str(r[0]) for r in conn.execute(
                "SELECT fixed_at FROM error_log WHERE " + _and(
                    "fixed_at IS NOT NULL", "fixed_file IS NOT NULL",
                    f"status IN ({_fin})",
                    f"fixed_at >= date('now','-{int(days)} days')", _excl)
                + " ORDER BY fixed_at",
                tuple(_FS)).fetchall()]
        import datetime as _dt
        prev = None
        for t in ts:
            try:
                cur = _dt.datetime.fromisoformat(t.replace(" ", "T")[:19])
            except Exception:
                continue
            if prev is not None:
                gaps_h.append((cur - prev).total_seconds() / 3600.0)
            prev = cur
    except Exception:
        pass
    out["thresholds"] = {
        "verified_fixes_week_min": _pct(weekly_ok, 25),
        "recurrence_rate_max":     _pct(weekly_rec, 75),
        "bandit_stale_h_max":      _pct(gaps_h, 90),
    }
    out["measured"] = any(v is not None for v in out["thresholds"].values())

    # ── 경보는 사람이 켠다. 기본 OFF.
    if (_os.getenv(SLO_ALERT_ENV) or "").strip().lower() in ("1", "true", "on", "yes"):
        th = out["thresholds"]
        if th["verified_fixes_week_min"] is not None and \
                out["verified_fixes_week"] < th["verified_fixes_week_min"]:
            out["alerts"].append(
                f"검증된 코드수정 {out['verified_fixes_week']}건/주 "
                f"< 임계 {th['verified_fixes_week_min']}")
        if th["recurrence_rate_max"] is not None and out["recurrence_rate"] is not None and \
                out["recurrence_rate"] > th["recurrence_rate_max"]:
            out["alerts"].append(
                f"재발률 {out['recurrence_rate']:.1%} > 임계 {th['recurrence_rate_max']:.1%}")
        if th["bandit_stale_h_max"] is not None and (out.get("bandit_stale_h") or 0) > th["bandit_stale_h_max"]:
            out["alerts"].append(
                f"밴딧 정지 {out['bandit_stale_h']}시간 > 임계 {th['bandit_stale_h_max']}시간")
    return out


def slo_lines() -> list:
    """`/status` 에 붙일 표시 줄 — **값만**. 임계는 '후보' 라고 명시한다."""
    try:
        s = guardian_slo()
    except Exception as e:  # noqa: BLE001
        return [f"📐 SLO 산출 실패: {str(e)[:80]}"]
    th = s.get("thresholds") or {}
    _fmt = lambda v, suf="": ("—" if v is None else f"{v}{suf}")
    _cand = lambda v, suf="": ("" if v is None else f" (임계후보 {v}{suf})")
    rr = s.get("recurrence_rate")
    lines = [
        f"📐 *SLO* (최근 {s.get('window_days')}일 · 경보 미발효)",
        f"　① 검증된 코드수정 {s.get('verified_fixes_week', 0)}건/주"
        + _cand(th.get("verified_fixes_week_min"), "건"),
        f"　② 재발률 {'—' if rr is None else f'{rr:.1%}'} (표본 {s.get('recurrence_base', 0)}건)"
        + ("" if th.get("recurrence_rate_max") is None else f" (임계후보 {th['recurrence_rate_max']:.1%})"),
        f"　③ 밴딧 마지막 갱신 {_fmt(s.get('bandit_stale_h'), '시간 전')}"
        + _cand(th.get("bandit_stale_h_max"), "시간"),
    ]
    if s.get("alerts"):
        lines += [f"　⚠️ {a}" for a in s["alerts"]]
    return lines


def report_ignored_bucket() -> dict:
    """집계 → 기존 텔레그램 경로(send_tg)로 송출. 킬스위치 GUARDIAN_IGNORED_REPORT=0."""
    rep = ignored_bucket_report()
    if not _flag("GUARDIAN_IGNORED_REPORT"):
        log.info("[GUARDIAN/ignored] 보고 비활성(GUARDIAN_IGNORED_REPORT=0) — 집계만 수행")
        return rep
    if not rep.get("lines"):
        return rep
    # 조용한 날엔 침묵: 0건이고 오탐도 없으면 알림 생략(알림 피로 방지)
    if not rep.get("total") and not rep.get("code_bug_ignored"):
        return rep
    try:
        from shared.notify import send_tg
        send_tg("\n".join(rep["lines"]))
    except Exception as e:
        _note_safety_fail("ignored_report_send", e)
    return rep



def _repair_watermark() -> int:
    """지금까지 기록된 자가진단 회차의 최대 id — **이번 회차를 가려내는 기준선**.

    ★ 왜 필요한가 (2026-08-08 재검증)
      종전 `_last_repair_returncode()` 는 `ORDER BY id DESC LIMIT 1` 로 **테이블 최신 행**
      을 봤다. 그건 '이번 회차' 가 아니다 — 다른 경로가 그 사이 회차를 적었거나
      이번 회차가 아예 기록을 못 남긴 경우, 남의 결과를 이번 판정에 쓴다.
      실행 전 워터마크를 찍어 두고 그보다 뒤에 생긴 행만 본다.
    """
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM self_repair_runs").fetchone()
        return int(row[0] or 0)
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] 워터마크 조회 실패: {e}")
        return 0


def _repair_returncode_since(watermark: int) -> "int | None":
    """워터마크 **이후** 기록된 회차의 returncode. 기록이 없으면 None.

    여러 건이면 하나라도 실패면 실패로 본다(가장 나쁜 것을 보고한다).
    """
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            rows = conn.execute(
                "SELECT returncode FROM self_repair_runs WHERE id > ? ORDER BY id",
                (int(watermark),)).fetchall()
        if not rows:
            return None
        codes = [int(r["returncode"]) for r in rows]
        bad = [c for c in codes if c != 0]
        return bad[0] if bad else 0
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] returncode 조회 실패: {e}")
        return None

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
        from shared.pipeline_activity import mark_busy as _mb
        _mb("j07", "심층 코드 감사", ttl=3600)
    except Exception:
        pass
    try:
        b = deep_audit_backlog()
        log.info(f"[GUARDIAN/deepaudit] backlog 완료: {b}")
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] backlog 처리 예외: {e}")
    _audit_rc = None
    _wm = _repair_watermark()
    try:
        from JARVIS07_GUARDIAN.auto_repair import run_auto_repair
        run_auto_repair()
        _audit_rc = _repair_returncode_since(_wm)
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] 광범위 감사 예외: {e}")
        _audit_rc = -1
    # ★ 실패한 회차를 **성공으로 기록하지 않는다** (2026-08-08 재검증으로 재작성).
    #
    #   1차 시도는 콜백 안에서 `mark_outcome` 으로 사후 보정하려 했는데 **항상 no_row**
    #   였다 — `job_runs` 행은 `_on_job_executed`(콜백 *종료 후*)에만 INSERT 되므로
    #   콜백 본체에서는 보정할 행이 아직 없다. 실측 j07_deep_audit 39/39 success=1 그대로.
    #   더 나쁜 건 6시간 창에 이전 행이 있으면 **직전의 진짜 성공 회차를 실패로 뒤집는다**.
    #
    #   그래서 보정을 하지 않는다 — **애초에 실패로 적히게** 한다.
    #   `run_auto_repair` 가 rc 로만 남기는 실패를 여기서 예외로 올리면
    #   APScheduler 의 `EVENT_JOB_ERROR` → `job_history._on_job_error` 가
    #   `success=False` 로 **정규 경로에서** 적재한다. 새 보정 경로를 만들지 않는다(①).
    #   ※ 예외는 이 잡을 끝내는 것뿐 — 스케줄러도 다른 잡도 영향받지 않는다.
    # ★ **정리를 먼저 하고 올린다** (2026-08-09 3차 적대적 검증)
    #   종전엔 `raise` 가 3부보다 **앞** 에 있었다. 그래서 심층 감사가 실패한 회차엔
    #   ① 격리 버킷 주간 보고(오탐 조기경보 포함)가 통째로 안 나가고
    #   ② `mark_busy("j07", ttl=3600)` 이 해제되지 않아 파이프라인 활동 표시가
    #      최대 1시간 거짓으로 남았다.
    #   격리분을 가장 봐야 할 회차가 정확히 실패한 회차인데 그때 보고가 꺼졌다.
    #   실측: `self_repair_runs` 106회 중 9회(8.5%)가 비0 rc 이고 **최근 5회 중 2회** —
    #   가상의 경로가 아니다.
    # 3부: 격리 버킷 집계·추세 보고 (★ 결함3) — 새 잡 신설 없이 기존 일일 잡에 편승
    try:
        report_ignored_bucket()
    except Exception as e:
        log.warning(f"[GUARDIAN/deepaudit] 격리 버킷 보고 예외: {e}")
    finally:
        try:
            from shared.pipeline_activity import clear_busy as _cb
            _cb("j07")
        except Exception:
            pass
    if _audit_rc not in (None, 0):
        raise RuntimeError(f"심층 감사 실패 — auto_repair returncode={_audit_rc}")


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
            # ★ 판정 기준 = `claimed_at`(마지막 살아있음 신호) — ERRORS [473] (2026-07-22)
            #   종전엔 `timestamp`(오류가 *기록된* 시각)로 판정했다. 그 값은 작업 진행과
            #   아무 상관이 없어 **살아 있는 세션도 리셋**했다 (2026-07-18 실측: #3435 의
            #   82분 Tier-2 세션이 75분 시점에 리셋 → 같은 오류에 두 번째 세션 중복 기동
            #   → LLM 단일 차선 경합). 이제 작업자가 60초마다 claimed_at 을 갱신하므로
            #   '마지막 신호 이후 N분 무응답' = 진짜 죽음. 작업이 아무리 길어도 안전하다.
            #   claimed_at 이 없는 과거 행은 종전대로 timestamp 로 폴백.
            ts = r.get("claimed_at") or r.get("timestamp") or ""
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00").split("+")[0])
            except Exception:
                continue
            if dt < threshold:
                _db.mark_error_status(int(r["id"]), "new")
                reset_n += 1
    except Exception as e:
        log.debug(f"[GUARDIAN/retry_pending] analyzing 리셋 예외: {e}")

    # 1-B) 오래된 '잠정' 실패 정리 → ignored (★ ERRORS [477])
    #      harness 는 최종 실패 시 `_finalize_attempt_errors` 로 잠정을 풀지만,
    #      일반 재시도 루프(썸네일·수집 등)에는 그런 종결자가 없다. 재시도가 결국
    #      성공했으면 앞선 시도 실패는 *애초에 문제가 아니었던 것* 이라 영영 잠정으로 남는다.
    #      → 일정 시간이 지나도 잠정이면 '지나간 일' 로 보고 ignored 처리(스윕 재투입 중단).
    prov_n = 0
    try:
        from datetime import datetime, timedelta
        _thr = datetime.now() - timedelta(minutes=stuck_minutes)
        for r in _db.list_errors(status="new", limit=max_per_run):
            if not r.get("provisional"):
                continue
            _ts = r.get("claimed_at") or r.get("timestamp") or ""
            try:
                _dt2 = datetime.fromisoformat(str(_ts).replace("Z", "+00:00").split("+")[0])
            except Exception:
                continue
            if _dt2 < _thr:
                _db.mark_error_status(int(r["id"]), "ignored")
                prov_n += 1
    except Exception as e:
        log.debug(f"[GUARDIAN/retry_pending] 잠정 정리 예외: {e}")
    if prov_n:
        log.info(f"[GUARDIAN/retry_pending] 오래된 잠정 실패 {prov_n}건 정리 → ignored "
                 f"(재시도가 지나갔거나 종결자 없음)")

    # 2) new → _orchestrate 재투입
    #    ★ 2026-08-14 — **`deep_audit_backlog` 와 같은 문을 쓴다** (③원칙, ERRORS [474] 형태).
    #      종전은 `list_errors(status="new")` 라 게이트가 남긴 재시도 백오프를 몰랐고,
    #      아직 풀릴 수 없는 것을 10분마다 다시 집어 같은 판정을 반복했다
    #      (실측: 지문 1종이 6시간 10분간 허용 3 / 차단 64).
    #
    #    ★ 단 **시도 상한은 여기서 거르지 않는다** (`max_attempts=None`) — 비직관적이라 박제.
    #      이 스윕은 재시도 통로이자 **배수구** 다. Tier-2 마지막 세션이 실패하면
    #      `_try_sdk_targeted_fix` 는 *직전 스냅샷* 기준으로 아직 여유가 있다고 보고
    #      status 를 `new` 로 둔다(그 뒤에 `bump_llm_attempts` 가 상한을 채운다).
    #      그 행을 닫아 주는 것이 바로 다음 회차의 이 루프다 — `_orchestrate` 가
    #      상한을 보고 LLM 0회로 `wontfix` 로 종결한다.
    #      여기서 상한으로 걸러 버리면 그 행은 **영원히 `new`** 로 남는다: 심층 감사도
    #      상한으로 제외하므로 아무도 안 보고, 대시보드 '신규' 만 영구히 부풀고,
    #      종결 사유도 안 남는다. 조용한 누수는 이 저장소가 반복해 온 병이다.
    #      → LLM 낭비 방지는 `_orchestrate` 의 상한 문이 이미 한다(축이 다르다).
    retry_n = 0
    try:
        from JARVIS07_GUARDIAN.architecture import STATUS_NEW as _st_new
        new_rows = _db.list_unresolved(max_per_run, statuses=(_st_new,), max_attempts=None)
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


# ── 텔레그램 알림 헬퍼 (비활성 — 사용자 박제) ──────────────────

# ── 공개 도구 API ─────────────────────────────────────────────────

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

    # 4-B) ★ 캐치 경로 스모크 — **설치했다는 것은 동작한다는 증거가 아니다** (2026-08-08).
    #   `catch_path_effective()` 는 정확히 이 목적으로 만들어졌는데 **호출자가 0곳**이었다
    #   (정의 1행뿐). 그 사이 로그 스캐너는 71일간 수확 0건이었고 아무도 몰랐다.
    #   CLAUDE.md 가 `patch_effective` 표준으로 박아둔 셋 중 이것만 배선이 빠져 있었다.
    #   실패해도 부팅을 막지 않는다 — 관측이 가용성을 해치면 안 된다. 대신 오류로 남긴다.
    try:
        from JARVIS07_GUARDIAN.error_collector import catch_path_effective
        _smoke = catch_path_effective()
        if _smoke is False:
            log.error("[GUARDIAN] ★ 캐치 경로 무력 — 오류가 수집되지 않는다")
            try:
                from JARVIS07_GUARDIAN.error_collector import report as _rep
                # ★ 타입을 error_type 자리로 (2026-08-08) — 위 두 곳과 같은 이유.
                _rep("CatchPathDead", "guardian",
                     message="캐치 경로 스모크 실패 — 오류 수집 무력",
                     module=__name__, func_name="register")
            except Exception:
                pass
        elif _smoke is None:
            log.info("[GUARDIAN] 캐치 경로 스모크 판정 불가")
    except Exception as e:
        log.warning(f"[GUARDIAN] 캐치 경로 스모크 실행 실패: {e}")

    # 5) 스케줄 잡 등록 — JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 에서 관리 (이관 완료)
    # guardian_log_scan / guardian_archive / j07_git_audit / j07_retry_pending

    # 6) ★ 부팅 시 자동 재발행 없음 (사용자 박제 2026-07-22 — ERRORS [469])
    #    종전엔 부팅 180초 뒤 job_startup_recovery 가 "오늘 테마글 0건" 이면 자동 발행했다.
    #    그러나 ① 복구 창(21:00~02:59)이 자정을 걸치는데 기준일은 자정에 바뀌어
    #    새벽 재시작마다 *정상 발행된 글을 중복 발행* 했고 ② 애초에 "결과물 0건" 을
    #    "중단됨" 으로 판단해 데몬이 꺼져 있던 경우와 구분하지 못했다.
    #    → **발행은 정해진 시각(DEFAULT_JOBS cron)에만.** 부팅이 발행을 유발하지 않는다.
    #    재발행이 필요하면 사용자가 텔레그램으로 명시 지시한다.

    log.info("✅ [GUARDIAN] JARVIS07_GUARDIAN 등록 완료 — 자동 오류 수집·수정 활성화")
