"""JARVIS01_MASTER/proactive_monitor.py — 능동형 시스템 자가진단 모니터

JARVIS01 브레인이 전체 시스템을 능동적으로 감시하고,
문제·개선점 발견 시 사용자 승인을 받아 처리한다.

체커 6종:
  1. IntegrityChecker      — 신규 에이전트 등록 완결성 (status·잡·인텐트·대시보드·문서)
  2. JobHealthMonitor      — 잡 건강성 (연속실패·misfire·핵심잡 실패 즉시 알림)
  3. EnvHealthChecker      — 환경 자원 (쿠키 만료·API키·디스크·DB 이상)
  4. ContentQualityMonitor — 콘텐츠 품질 추세 (발행실패율·점수하락·글자수미달·빈소제목)
  5. ErrorsPatternAnalyzer — ERRORS.md 반복 패턴 자가학습 + 근본 해결 제안
  6. MorningBriefing       — 08:30 일일 능동 브리핑

트리거 시점:
  - boot_check()      : 데몬 부팅 후 (IntegrityChecker + EnvHealthChecker)
  - hourly_check()    : 매시간 (JobHealthMonitor + ContentQualityMonitor + IntegrityChecker)
  - execute_fix(id)   : pm_yes 콜백 → 저장된 fix 실행

이상 감지 시 행동:
  - alert only  → _send_tg() 즉시 알림
  - actionable  → _send_tg_buttons() 승인 요청 + _PENDING_PM 저장
  - code change → ReAct create_plan 로 위임 (추가 승인 게이트 통과)
"""
from __future__ import annotations

import logging
import os
import re
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent

# ── 승인 대기 테이블 ─────────────────────────────────────────────
# fix_id → {desc, fix_fn, created_at, severity}
_PENDING_PM: dict = {}
_PENDING_LOCK = threading.Lock()

# 중복 알림 방지 (같은 finding_key → 마지막 알림 epoch)
_ALERTED: dict[str, float] = {}
_ALERT_COOLDOWN_SEC = 3600  # 1시간 내 동일 키 재알림 방지


# ── Finding 모델 ─────────────────────────────────────────────────

@dataclass
class Finding:
    key: str                           # 중복 방지용 유일 식별자
    severity: str                      # "critical" | "warning" | "info"
    title: str
    detail: str
    fix_fn: Optional[Callable] = None  # None=alert only, callable=fix 가능
    fix_label: str = "수정"


# ── 공통 유틸 ────────────────────────────────────────────────────

def _send_tg(text: str):
    """jarvis_daemon._send_tg lazy import (circular 방지)."""
    try:
        import jarvis_daemon as _dm
        _dm._send_tg(text)
    except Exception as e:
        log.warning(f"[PM] _send_tg 실패: {e}")
        _g_report("master", e, module=__name__)


def _send_tg_buttons(text: str, buttons: list):
    try:
        import jarvis_daemon as _dm
        _dm._send_tg_buttons(text, buttons)
    except Exception as e:
        log.warning(f"[PM] _send_tg_buttons 실패: {e}")
        _g_report("master", e, module=__name__)


def _cooldown_ok(key: str) -> bool:
    """쿨다운 내 동일 키 알림이면 False."""
    now = time.time()
    last = _ALERTED.get(key, 0)
    if now - last < _ALERT_COOLDOWN_SEC:
        return False
    _ALERTED[key] = now
    return True


def _dispatch_findings(findings: list[Finding], source: str):
    """finding 리스트를 텔레그램으로 단일 메시지 송출.

    모든 finding을 하나의 메시지로 묶어서 전송.
    actionable 항목은 항목별 개별 버튼 행 + 맨 아래 '모두' 행.
    alert-only 항목은 텍스트만.
    """
    if not findings:
        return

    actionable: list[Finding] = []
    alert_lines: list[str] = []

    for i, f in enumerate(findings, 1):
        if not _cooldown_ok(f.key):
            continue
        icon = "🔴" if f.severity == "critical" else ("🟡" if f.severity == "warning" else "ℹ️")
        if f.fix_fn is not None:
            actionable.append(f)
            alert_lines.append(f"{icon} *{i}. {f.title}*\n   {f.detail}")
        else:
            alert_lines.append(f"{icon} *{f.title}*\n   {f.detail}")

    if not alert_lines:
        return

    total = len(alert_lines)
    header = f"📋 *[JARVIS01 자가진단 — {source}]* ({total}건)\n{'━'*18}\n"
    body = header + "\n\n".join(alert_lines)

    if not actionable:
        _send_tg(body)
        return

    # 항목별 개별 버튼 행 + 맨 아래 '모두' 행
    buttons: list[list[dict]] = []
    item_fix_ids: list[str] = []
    with _PENDING_LOCK:
        for idx, f in enumerate(actionable, 1):
            fix_id = f"pm:{uuid.uuid4().hex[:8]}"
            item_fix_ids.append(fix_id)
            _PENDING_PM[fix_id] = {
                "desc": f.title,
                "detail": f.detail,
                "fix_fn": f.fix_fn,
                "created_at": time.time(),
                "severity": f.severity,
            }
            label = f.title[:16] + "…" if len(f.title) > 16 else f.title
            buttons.append([
                {"text": f"✅ {idx}. {label}", "callback_data": f"pm_yes:{fix_id}"},
                {"text": "❌ 무시",             "callback_data": f"pm_no:{fix_id}"},
            ])

        # 배치 항목 (모두 수정용)
        batch_id = f"pm_batch:{uuid.uuid4().hex[:8]}"
        _PENDING_PM[batch_id] = {
            "batch": True,
            "source": source,
            "items": [
                {"desc": f.title, "fix_fn": f.fix_fn, "severity": f.severity}
                for f in actionable
            ],
            "created_at": time.time(),
        }

    fix_count = len(actionable)
    buttons.append([
        {"text": f"✅ 모두 수정 ({fix_count}건)", "callback_data": f"pm_batch_yes:{batch_id}"},
        {"text": "❌ 모두 무시",                   "callback_data": f"pm_batch_no:{batch_id}"},
    ])
    _send_tg_buttons(body, buttons)


# ════════════════════════════════════════════════════════════════
# 1. IntegrityChecker — 에이전트 등록 완결성
# ════════════════════════════════════════════════════════════════

# 부팅 시 이미 존재했던 에이전트 (레거시) — 완결성 이미 확인됨
_LEGACY_AGENTS = {
    "jarvis00_infra", "jarvis01_master",
    "jarvis02_writer", "jarvis03_radar", "jarvis04_scheduler",
}

# ── 영구 seen 저장소 (재시작 후에도 유지) ────────────────────────
_SEEN_PATH = ROOT / "logs" / "integrity_seen.json"

def _load_seen() -> set[str]:
    try:
        import json as _j
        return set(_j.loads(_SEEN_PATH.read_text()))
    except Exception:
        return set()

def _save_seen(seen: set[str]) -> None:
    try:
        import json as _j
        _SEEN_PATH.parent.mkdir(exist_ok=True)
        _SEEN_PATH.write_text(_j.dumps(sorted(seen)))
    except Exception:
        pass

# 체크 실행 시점에 확인된 에이전트 집합 (중복 알림 방지 — 재시작 후에도 유지)
_INTEGRITY_SEEN: set[str] = _load_seen()


class IntegrityChecker:
    """등록된 모든 에이전트의 통합 완결성 점검."""

    # ── 개별 체크 함수 ───────────────────────────────────────────

    @staticmethod
    def _in_status(agent_id: str) -> bool:
        """build_status() 에 에이전트 섹션 있나? (infra_agent.py 텍스트 분석)."""
        try:
            path = ROOT / "JARVIS00_INFRA" / "infra_agent.py"
            txt = path.read_text(encoding="utf-8")
            agent_upper = agent_id.upper()          # jarvis01_master → JARVIS01_MASTER
            # 번호 기반 표시명 추출: jarvis02_writer → JARVIS02
            m = re.search(r'jarvis(\d+)', agent_id)
            agent_display = f"JARVIS{m.group(1)}" if m else ""
            return (agent_id in txt) or (agent_upper in txt) or (agent_display and agent_display in txt)
        except Exception:
            return True  # 파일 읽기 실패 시 통과

    @staticmethod
    def _has_jobs(agent_id: str) -> bool:
        """DEFAULT_JOBS 에 owner=agent_id 인 잡이 최소 1개 있나?"""
        try:
            from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
            return any(j.get("owner") == agent_id for j in DEFAULT_JOBS)
        except Exception:
            return True

    @staticmethod
    def _has_intents(agent_id: str) -> bool:
        """SAFE_INTENTS | APPROVAL_INTENTS 에 이 에이전트 인텐트가 있나?"""
        try:
            from shared.capabilities import get as _get_cap
            cap = _get_cap(agent_id)
            if not cap or not cap.intents:
                return True  # 인텐트 없는 에이전트는 체크 스킵
            from JARVIS01_MASTER.dispatchers import SAFE_INTENTS, APPROVAL_INTENTS
            covered = SAFE_INTENTS | APPROVAL_INTENTS
            return bool(set(cap.intents) & covered)
        except Exception:
            return True

    @staticmethod
    def _in_dashboard(agent_id: str) -> bool:
        """hub.py 에 agent_id 언급 있나?"""
        try:
            txt = (ROOT / "hub.py").read_text(encoding="utf-8")
            return agent_id in txt
        except Exception:
            return True

    @staticmethod
    def _in_agents_md(agent_id: str) -> bool:
        """AGENTS.md 에 agent_id 언급 있나?"""
        try:
            p = ROOT / "AGENTS.md"
            if not p.exists():
                return True
            return agent_id in p.read_text(encoding="utf-8")
        except Exception:
            return True

    @staticmethod
    def _in_help(agent_id: str) -> bool:
        """데몬 /help 텍스트에 에이전트 언급 있나? (jarvis_daemon.py 의 _HELP_TEXT)."""
        try:
            txt = (ROOT / "jarvis_daemon.py").read_text(encoding="utf-8")
            # _HELP_TEXT 블록만 찾기
            m = re.search(r'_HELP_TEXT\s*=\s*["\']+(.*?)["\']+', txt, re.DOTALL)
            if m:
                return agent_id in m.group(1)
            return True  # 찾을 수 없으면 통과
        except Exception:
            return True

    def check(self) -> list[Finding]:
        findings = []
        try:
            from shared.capabilities import all_capabilities
            caps = all_capabilities()
        except Exception as e:
            log.warning(f"[Integrity] capabilities 로드 실패: {e}")
            _g_report("master", e, module=__name__)
            return findings

        for cap in caps:
            aid = cap.agent_id
            # 이미 체크 완료(통과 or 무시)한 에이전트는 스킵 — 재시작 후에도 유지
            if aid in _INTEGRITY_SEEN:
                continue
            _INTEGRITY_SEEN.add(aid)
            _save_seen(_INTEGRITY_SEEN)  # 즉시 파일 기록 — 재시작 후 중복 알림 방지

            label = aid.replace("jarvis", "JARVIS").upper()
            checks = [
                ("status",    self._in_status,    "/status 섹션"),
                ("jobs",      self._has_jobs,      "DEFAULT_JOBS 잡"),
                ("intents",   self._has_intents,   "dispatchers 인텐트"),
                ("dashboard", self._in_dashboard,  "웹 대시보드(hub.py)"),
                ("agents_md", self._in_agents_md,  "AGENTS.md 문서"),
            ]
            missing = []
            for cname, cfn, clabel in checks:
                try:
                    if not cfn(aid):
                        missing.append(clabel)
                except Exception:
                    pass

            if missing:
                detail = f"`{aid}` 가 다음 항목에 미등록:\n   • " + "\n   • ".join(missing)
                findings.append(Finding(
                    key=f"integrity:{aid}",
                    severity="warning",
                    title=f"{label} 등록 미완결 ({len(missing)}/{len(checks)})",
                    detail=detail,
                    fix_fn=lambda a=aid, m=missing: _request_integrity_fix(a, m),
                    fix_label="등록 완결 요청",
                ))
        return findings


def _request_integrity_fix(agent_id: str, missing: list[str]):
    """IntegrityChecker fix: ReAct에 수정 위임."""
    task = (
        f"{agent_id} 에이전트가 다음 항목에 미등록되어 있습니다: {', '.join(missing)}. "
        f"각 항목에 적절히 추가해주세요. "
        f"코드 변경이 필요한 경우 create_plan으로 단계별 수정 계획을 세운 후 실행하세요."
    )
    try:
        import jarvis_daemon as _dm
        _dm._run_react(task, max_steps=10, verbose=True)
    except Exception as e:
        _send_tg(f"⚠️ 등록 수정 ReAct 위임 실패: {e}\n\n수동으로 처리해주세요:\n{task}")


# ════════════════════════════════════════════════════════════════
# 2. JobHealthMonitor — 잡 실행 건강성
# ════════════════════════════════════════════════════════════════

_CRITICAL_JOBS = {"j01_economic_post", "j01_theme_post_16"}  # 실패 즉시 알림
_CONSEC_FAIL_THRESHOLD = 3   # 연속 실패 N회 이상이면 alert


class JobHealthMonitor:

    def check(self) -> list[Finding]:
        findings = []
        try:
            from shared import db as _db
            conn = _db.get_db()
        except Exception:
            return findings

        # ★ 누수 점검 (2026-05-17) — try/finally 로 conn 정리 보장.
        # 옛: conn = get_db() → 어디서도 conn.close() 안 함 → 매 check() 회마다 연결 누적.
        # `with conn:` 은 transaction context (commit/rollback) — 연결 닫지 않음. 별개.
        try:
            return self._check_impl(conn, findings)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _check_impl(self, conn, findings: list) -> list[Finding]:
        # ── 연속 실패 체크 ───────────────────────────────────────
        try:
            with conn:
                rows = conn.execute(
                    """
                    SELECT job_id, job_name,
                           SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS fail_streak,
                           COUNT(*) AS total
                    FROM (
                        SELECT job_id, job_name, success,
                               ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY id DESC) rn
                        FROM job_runs
                    ) t
                    WHERE rn <= ?
                    GROUP BY job_id, job_name
                    HAVING fail_streak >= ?
                    """,
                    (_CONSEC_FAIL_THRESHOLD, _CONSEC_FAIL_THRESHOLD),
                ).fetchall()
            for r in rows:
                jid, jname = r["job_id"], r["job_name"]
                streak = r["fail_streak"]
                sev = "critical" if jid in _CRITICAL_JOBS else "warning"
                findings.append(Finding(
                    key=f"job_fail:{jid}",
                    severity=sev,
                    title=f"잡 연속 실패 {streak}회: {jname}",
                    detail=f"`{jid}` — 마지막 {streak}회 연속 실패. 로그 확인 필요.",
                    fix_fn=lambda j=jid, n=jname: _request_job_diagnosis(j, n),
                    fix_label="원인 진단 요청",
                ))
        except Exception as e:
            log.warning(f"[JobHealth] 연속실패 쿼리 오류: {e}")
            _g_report("master", e, module=__name__)

        # ── misfire 체크 (예정 시간 30분 이상 초과) ──────────────
        try:
            from JARVIS04_SCHEDULER.job_catalog import list_jobs
            now = datetime.now().astimezone()
            for j in list_jobs():
                nr = j.get("next_run")
                if not nr or j.get("paused"):
                    continue
                try:
                    nrt = datetime.fromisoformat(nr)
                    if nrt.tzinfo is None:
                        from datetime import timezone
                        nrt = nrt.replace(tzinfo=timezone.utc)
                    diff = (nrt - now).total_seconds()
                    # next_run이 과거 30분+ 이면 misfire 의심
                    if diff < -1800:
                        findings.append(Finding(
                            key=f"misfire:{j['id']}",
                            severity="warning",
                            title=f"잡 misfire 의심: {j['name']}",
                            detail=f"`{j['id']}` — 예정 시간 {abs(int(diff//60))}분 초과. APScheduler 상태 확인 필요.",
                        ))
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"[JobHealth] misfire 체크 오류: {e}")
            _g_report("master", e, module=__name__)

        # ── 핵심 잡 오늘 실행 여부 ──────────────────────────────
        try:
            today_str = datetime.now().strftime("%Y-%m-%d")
            with conn:
                for jid in _CRITICAL_JOBS:
                    row = conn.execute(
                        "SELECT success FROM job_runs WHERE job_id=? "
                        "AND started_at >= ? ORDER BY id DESC LIMIT 1",
                        (jid, today_str),
                    ).fetchone()
                    now_h = datetime.now().hour
                    # 경제 브리핑은 07시 이후, 테마는 16시 이후 실행됐어야 함
                    expected_h = 7 if jid == "j01_economic_post" else 16
                    if now_h >= expected_h + 1 and row is None:
                        findings.append(Finding(
                            key=f"critical_missing:{jid}:{today_str}",
                            severity="critical",
                            title=f"핵심 잡 오늘 미실행: {jid}",
                            detail=f"오늘 {expected_h:02d}:00 이후 실행 기록 없음. 수동 실행 필요.",
                            fix_fn=lambda j=jid: _trigger_job_now(j),
                            fix_label="지금 즉시 실행",
                        ))
                    elif row and not row["success"]:
                        findings.append(Finding(
                            key=f"critical_fail:{jid}:{today_str}",
                            severity="critical",
                            title=f"핵심 잡 오늘 실패: {jid}",
                            detail=f"오늘 실행했지만 실패. 즉시 확인 필요.",
                            fix_fn=lambda j=jid: _trigger_job_now(j),
                            fix_label="재실행",
                        ))
        except Exception as e:
            log.warning(f"[JobHealth] 핵심잡 체크 오류: {e}")
            _g_report("master", e, module=__name__)

        return findings


def _request_job_diagnosis(job_id: str, job_name: str):
    task = (
        f"잡 `{job_id}` ({job_name}) 가 연속으로 {_CONSEC_FAIL_THRESHOLD}회 실패했습니다. "
        f"daemon.log 에서 관련 에러를 grep하고 ERRORS.md와 대조하여 원인을 파악해주세요."
    )
    try:
        import jarvis_daemon as _dm
        _dm._run_react(task, max_steps=8, verbose=True)
    except Exception as e:
        _send_tg(f"⚠️ 잡 진단 ReAct 위임 실패: {e}")


def _trigger_job_now(job_id: str):
    try:
        from JARVIS04_SCHEDULER.job_controller import run_job_now
        result = run_job_now(job_id)
        _send_tg(f"▶️ 잡 즉시 실행 요청: `{job_id}`\n{result.get('message', '')}")
    except Exception as e:
        _send_tg(f"⚠️ 잡 즉시 실행 실패 `{job_id}`: {e}")


# ════════════════════════════════════════════════════════════════
# 3. EnvHealthChecker — 환경·자원 건강성
# ════════════════════════════════════════════════════════════════

class EnvHealthChecker:

    def check(self) -> list[Finding]:
        findings = []
        findings += self._check_api_keys()
        findings += self._check_cookie_freshness()
        findings += self._check_disk()
        findings += self._check_db()
        return findings

    @staticmethod
    def _check_api_keys() -> list[Finding]:
        findings = []
        # ★ 사용자 박제 2026-05-17 — Claude Code SDK 단일화: 외부 API 키 불필요.
        required = {
            "TELEGRAM_TOKEN":    "텔레그램 봇",
            "TELEGRAM_CHAT_ID":  "텔레그램 채팅",
        }
        env_path = ROOT / ".env"
        env_txt = ""
        try:
            env_txt = env_path.read_text(encoding="utf-8")
        except Exception:
            pass

        for key, label in required.items():
            val = os.environ.get(key) or ""
            if not val:
                # .env 파일에라도 있는지 확인
                if key not in env_txt:
                    findings.append(Finding(
                        key=f"env_missing:{key}",
                        severity="critical",
                        title=f"필수 환경변수 누락: {key}",
                        detail=f"`{label}` 동작 불가. .env 파일에 `{key}=...` 추가 필요.",
                    ))
        return findings

    @staticmethod
    def _check_cookie_freshness() -> list[Finding]:
        """마지막 네이버/티스토리 발행 성공일로 쿠키 만료 추정."""
        findings = []
        try:
            from shared import db as _db
            with _db.get_db() as conn:
                for platform, label in [("naver", "네이버"), ("tistory", "티스토리")]:
                    row = conn.execute(
                        f"SELECT MAX(published_at) last FROM posts WHERE platform=? AND status='published'",
                        (platform,),
                    ).fetchone()
                    if row and row["last"]:
                        try:
                            last_dt = datetime.fromisoformat(str(row["last"]))
                            age_days = (datetime.now() - last_dt).days
                            if age_days >= 25:
                                findings.append(Finding(
                                    key=f"cookie_old:{platform}",
                                    severity="warning",
                                    title=f"{label} 쿠키 만료 임박",
                                    detail=f"마지막 성공 발행 {age_days}일 전. 쿠키가 만료됐을 수 있습니다. 수동 갱신 필요.",
                                ))
                        except Exception:
                            pass
        except Exception as e:
            log.debug(f"[Env] 쿠키 체크 스킵: {e}")
        return findings

    @staticmethod
    def _check_disk() -> list[Finding]:
        findings = []
        try:
            import shutil
            usage = shutil.disk_usage(str(ROOT))
            pct_used = usage.used / usage.total * 100
            free_gb = usage.free / (1024 ** 3)
            if pct_used >= 90:
                findings.append(Finding(
                    key="disk_critical",
                    severity="critical",
                    title=f"디스크 공간 위험: {pct_used:.1f}% 사용 ({free_gb:.1f}GB 남음)",
                    detail="즉시 정리 필요. `python shared/file_cleanup.py` 실행 또는 수동 정리.",
                    fix_fn=lambda: _run_file_cleanup(),
                    fix_label="파일 정리 실행",
                ))
            elif pct_used >= 80:
                findings.append(Finding(
                    key="disk_warning",
                    severity="warning",
                    title=f"디스크 공간 경고: {pct_used:.1f}% 사용 ({free_gb:.1f}GB 남음)",
                    detail="조만간 정리가 필요합니다.",
                ))
        except Exception as e:
            log.debug(f"[Env] 디스크 체크 스킵: {e}")
        return findings

    @staticmethod
    def _check_db() -> list[Finding]:
        findings = []
        try:
            from shared.db import DB_PATH as db_path
            if db_path.exists():
                size_mb = db_path.stat().st_size / (1024 ** 2)
                if size_mb >= 500:
                    findings.append(Finding(
                        key="db_large",
                        severity="warning",
                        title=f"DB 파일 대용량: {size_mb:.0f}MB",
                        detail="오래된 events·job_runs 정리가 필요합니다.",
                        fix_fn=lambda: _run_db_cleanup(),
                        fix_label="DB 정리 실행",
                    ))
        except Exception as e:
            log.debug(f"[Env] DB 체크 스킵: {e}")
        return findings


def _run_file_cleanup():
    try:
        import subprocess
        r = subprocess.run(
            ["python", str(ROOT / "shared" / "file_cleanup.py")],
            capture_output=True, text=True, timeout=60
        )
        _send_tg(f"🧹 파일 정리 완료\n```\n{r.stdout[-800:]}\n```")
    except Exception as e:
        _send_tg(f"⚠️ 파일 정리 실패: {e}")


def _run_db_cleanup():
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            conn.execute("DELETE FROM events WHERE created_at < datetime('now', '-30 days')")
            conn.execute("DELETE FROM job_runs WHERE started_at < datetime('now', '-60 days')")
        _send_tg("🧹 DB 정리 완료 (30일+ events, 60일+ job_runs 삭제)")
    except Exception as e:
        _send_tg(f"⚠️ DB 정리 실패: {e}")


# ════════════════════════════════════════════════════════════════
# 4. ContentQualityMonitor — 콘텐츠 품질 추세
# ════════════════════════════════════════════════════════════════

class ContentQualityMonitor:

    def check(self) -> list[Finding]:
        findings = []
        findings += self._check_publish_failure_rate()
        findings += self._check_quality_score_trend()
        findings += self._check_char_count()
        findings += self._check_empty_headings()
        return findings

    @staticmethod
    def _check_publish_failure_rate() -> list[Finding]:
        """최근 10건 발행 시도 중 실패율 50% 이상이면 alert."""
        findings = []
        try:
            from shared import db as _db
            with _db.get_db() as conn:
                rows = conn.execute(
                    "SELECT status FROM posts ORDER BY id DESC LIMIT 10"
                ).fetchall()
            if len(rows) < 3:
                return findings
            fail_cnt = sum(1 for r in rows if r["status"] != "published")
            rate = fail_cnt / len(rows)
            if rate >= 0.5:
                findings.append(Finding(
                    key="publish_fail_rate",
                    severity="critical" if rate >= 0.7 else "warning",
                    title=f"발행 실패율 급등: {rate*100:.0f}% ({fail_cnt}/{len(rows)}건)",
                    detail="최근 발행 시도 절반 이상 실패. 네트워크·쿠키·API 상태 점검 필요.",
                    fix_fn=lambda: _send_tg("🔍 발행 실패 진단: `/react 최근 발행 실패 원인 분석해줘`"),
                ))
        except Exception as e:
            log.debug(f"[Content] 발행실패율 스킵: {e}")
        return findings

    @staticmethod
    def _check_quality_score_trend() -> list[Finding]:
        """최근 7일 품질 평균이 이전 7일 대비 15% 이상 하락하면 alert."""
        findings = []
        try:
            from shared import db as _db
            with _db.get_db() as conn:
                recent = conn.execute(
                    "SELECT AVG(quality_score) s FROM post_analysis "
                    "WHERE analyzed_at >= date('now','-7 days') AND quality_score IS NOT NULL"
                ).fetchone()
                prev = conn.execute(
                    "SELECT AVG(quality_score) s FROM post_analysis "
                    "WHERE analyzed_at BETWEEN date('now','-14 days') AND date('now','-7 days') "
                    "AND quality_score IS NOT NULL"
                ).fetchone()
            r_s = recent["s"] if recent else None
            p_s = prev["s"] if prev else None
            if r_s and p_s and p_s > 0:
                drop = (p_s - r_s) / p_s
                if drop >= 0.15:
                    findings.append(Finding(
                        key="quality_drop",
                        severity="warning",
                        title=f"품질 점수 하락: 이전 {p_s:.1f} → 최근 {r_s:.1f} ({drop*100:.0f}% 하락)",
                        detail="최근 7일 품질이 이전 7일 대비 눈에 띄게 하락했습니다. 원인 분석 필요.",
                    ))
        except Exception as e:
            log.debug(f"[Content] 품질추세 스킵: {e}")
        return findings

    @staticmethod
    def _check_char_count() -> list[Finding]:
        """최근 5건 중 3건 이상 글자수 미달이면 alert."""
        findings = []
        try:
            from shared import db as _db
            from JARVIS02_WRITER.length_manager import MIN_BODY_CHARS
            with _db.get_db() as conn:
                rows = conn.execute(
                    "SELECT char_count FROM post_analysis ORDER BY id DESC LIMIT 5"
                ).fetchall()
            if len(rows) < 3:
                return findings
            short = sum(1 for r in rows if r["char_count"] and r["char_count"] < MIN_BODY_CHARS)
            if short >= 3:
                findings.append(Finding(
                    key="char_count_short",
                    severity="warning",
                    title=f"글자수 미달 반복: 최근 5건 중 {short}건",
                    detail=f"기준 {MIN_BODY_CHARS}자 미달 발행이 반복되고 있습니다. length_manager 설정 점검 필요.",
                ))
        except Exception as e:
            log.debug(f"[Content] 글자수 스킵: {e}")
        return findings

    @staticmethod
    def _check_empty_headings() -> list[Finding]:
        """최근 5건 중 빈 소제목 발행글이 있으면 alert."""
        findings = []
        try:
            from shared import db as _db
            with _db.get_db() as conn:
                rows = conn.execute(
                    "SELECT id, original_html FROM post_analysis "
                    "WHERE original_html IS NOT NULL ORDER BY id DESC LIMIT 5"
                ).fetchall()
            bad = []
            for r in rows:
                html = r["original_html"] or ""
                empties = re.findall(
                    r'<h[1-6][^>]*>\s*</h[1-6]>|<h[1-6][^>]*>\s*<br\s*/?>\s*</h[1-6]>',
                    html, re.IGNORECASE,
                )
                if empties:
                    bad.append(r["id"])
            if bad:
                findings.append(Finding(
                    key=f"empty_heading:{','.join(map(str,bad))}",
                    severity="warning",
                    title=f"빈 소제목 발행 감지: post_analysis id {bad}",
                    detail="빈 헤더가 그대로 발행된 글이 있습니다. pre_revise 후처리 점검 필요.",
                ))
        except Exception as e:
            log.debug(f"[Content] 빈소제목 스킵: {e}")
        return findings


# ════════════════════════════════════════════════════════════════
# 5. CodeQualityChecker — 동적 코딩 원칙 위반 자동 감지
# ════════════════════════════════════════════════════════════════

class CodeQualityChecker:
    """코드베이스에서 하드코딩·동적 누락 패턴을 자동 감지하고 보고."""

    # 스캔 대상 파일
    _TARGETS = [
        "JARVIS02_WRITER/jarvis_main.py",
        "JARVIS02_WRITER/economic_poster.py",
        "JARVIS02_WRITER/collect_theme.py",
        "hub.py",
        "JARVIS01_MASTER/proactive_monitor.py",
    ]

    # 모델명에 포함된 연도 패턴 — false positive 제외
    _MODEL_PAT = re.compile(r'claude-[^\s\'"]+|haiku-[^\s\'"]+|sonnet-[^\s\'"]+|opus-[^\s\'"]+')
    # 하드코딩 연도 패턴 (2023~2029)
    _YEAR_PAT  = re.compile(r'(?<!\d)(202[3-9])년')
    # 날짜 주입 증거 패턴
    _DATE_INJ  = re.compile(r'_TODAY_STR|TODAY_STR|datetime\.now|strftime|today_str')
    # LLM 호출 함수 패턴
    _LLM_CALL  = re.compile(r'messages\.create\(|invoke_text\(|get_client\(\)')

    def _scrub_model_names(self, line: str) -> str:
        """모델명·주석·import 라인 제거 후 반환 (false positive 방지)."""
        line = self._MODEL_PAT.sub('', line)
        line = re.sub(r'#.*$', '', line)
        return line

    def check(self) -> list[Finding]:
        findings = []
        current_year = str(datetime.now().year)

        for rel in self._TARGETS:
            path = ROOT / rel
            if not path.exists():
                continue
            try:
                src = path.read_text(encoding='utf-8')
                lines = src.splitlines()
            except Exception:
                continue

            # ── Check 1: 하드코딩된 연도가 현재 연도와 다름 ─────────────
            bad_year_lines = []
            for i, line in enumerate(lines, 1):
                clean = self._scrub_model_names(line)
                m = self._YEAR_PAT.search(clean)
                if m and m.group(1) != current_year:
                    bad_year_lines.append((i, m.group(1), line.strip()[:70]))

            if bad_year_lines:
                detail_parts = [f"L{ln}: {year}년 하드코딩 — `{ctx}`"
                                for ln, year, ctx in bad_year_lines[:3]]
                if len(bad_year_lines) > 3:
                    detail_parts.append(f"... 외 {len(bad_year_lines)-3}건")
                findings.append(Finding(
                    key=f"hardcoded_year:{rel}",
                    severity="warning",
                    title=f"하드코딩 연도 감지: `{rel}`",
                    detail="\n   ".join(detail_parts),
                ))

            # ── Check 2: LLM 프롬프트 함수에 날짜 주입 없음 ─────────────
            # 함수 단위로 스캔: def → 다음 def 사이에 LLM call 있는데 날짜 주입 없으면 경고
            fn_blocks = re.split(r'\ndef ', src)
            bad_fns = []
            for block in fn_blocks[1:]:  # 첫 블록은 모듈 레벨
                fn_name_m = re.match(r'(\w+)\s*\(', block)
                fn_name = fn_name_m.group(1) if fn_name_m else '?'
                if 'prompt' not in block:
                    continue
                has_llm  = bool(self._LLM_CALL.search(block))
                has_date = bool(self._DATE_INJ.search(block))
                if has_llm and not has_date:
                    # 날짜가 모듈 레벨에서 이미 주입됐으면 OK (글로벌 _TODAY_STR 참조)
                    if '_TODAY_STR' in src[:src.find('def ' + fn_name)] if fn_name != '?' else False:
                        continue
                    bad_fns.append(fn_name)

            if bad_fns:
                findings.append(Finding(
                    key=f"no_date_in_prompt:{rel}",
                    severity="warning",
                    title=f"LLM 프롬프트 날짜 미주입: `{rel}`",
                    detail=f"다음 함수에 날짜(TODAY_STR/datetime.now) 주입 없음:\n   " +
                           ", ".join(bad_fns[:5]),
                ))

        # ── Check 3: UI 레이블 에이전트 번호 불일치 ──────────────────────
        app_py = ROOT / "hub.py"
        if app_py.exists():
            try:
                app_src = app_py.read_text(encoding='utf-8')
                # hub.py 에서 "J03 레이더", "J04 스케줄러" 등 agent 카드 레이블 확인
                radar_m = re.findall(r'J(\d{2})\s*레이더', app_src)
                sched_m = re.findall(r'J(\d{2})\s*스케줄러', app_src)
                bad_labels = []
                for n in radar_m:
                    if n != '03':
                        bad_labels.append(f"레이더 레이블: J{n} (→ J03이어야 함)")
                for n in sched_m:
                    if n != '04':
                        bad_labels.append(f"스케줄러 레이블: J{n} (→ J04이어야 함)")
                if bad_labels:
                    findings.append(Finding(
                        key="ui_label_mismatch",
                        severity="warning",
                        title="대시보드 UI 에이전트 번호 불일치",
                        detail="\n   ".join(bad_labels),
                    ))
            except Exception:
                pass

        # ── Check 4: 완료 알림에 글자수 없음 ────────────────────────────
        for rel, fn_name, marker in [
            ("JARVIS02_WRITER/jarvis_main.py",     "run_posting_pipeline", "char_counts"),
            ("JARVIS02_WRITER/economic_poster.py",  "run_economic_poster",  "_wp_cn"),
        ]:
            path = ROOT / rel
            if not path.exists():
                continue
            try:
                src2 = path.read_text(encoding='utf-8')
                if '🎉' in src2 and '━━━' in src2 and marker not in src2:
                    findings.append(Finding(
                        key=f"no_charcount_notify:{rel}",
                        severity="warning",
                        title=f"완료 알림에 글자수 없음: `{rel}`",
                        detail=f"발행 완료 텔레그램 메시지에 플랫폼별 글자수가 빠져 있습니다.",
                    ))
            except Exception:
                pass

        return findings


# ════════════════════════════════════════════════════════════════
# 6. ErrorsPatternAnalyzer — ERRORS.md 반복 패턴 자가학습
# ════════════════════════════════════════════════════════════════

class ErrorsPatternAnalyzer:

    def check(self) -> list[Finding]:
        findings = []
        try:
            path = ROOT / "JARVIS07_GUARDIAN" / "ERRORS.md"
            if not path.exists():
                return findings
            txt = path.read_text(encoding="utf-8")
            # 증상 섹션 파싱 (## [NN] 형식)
            sections = re.split(r'\n## \[\d+\]', txt)
            # 증상 키워드 추출 후 빈도 카운트
            symptom_keywords: dict[str, list[str]] = {}
            for sec in sections:
                # 증상 라인 추출
                m = re.search(r'\*\*증상\*\*[:\s]*(.+)', sec)
                if not m:
                    continue
                symptom = m.group(1).strip()[:80]
                # 핵심 키워드 2~3개 추출 (에러명·함수명·모듈명)
                keywords = re.findall(r'[A-Za-z_]+Error|[A-Za-z_]{4,}(?:\.py|Error|Exception)', symptom)
                key = keywords[0] if keywords else symptom[:30]
                symptom_keywords.setdefault(key, []).append(symptom)

            for key, symptoms in symptom_keywords.items():
                if len(symptoms) >= 3:
                    findings.append(Finding(
                        key=f"errors_repeat:{key}",
                        severity="warning",
                        title=f"ERRORS.md 반복 패턴 감지: `{key}` ({len(symptoms)}회)",
                        detail=f"동일 오류가 {len(symptoms)}번 기록됐습니다. 근본 해결이 필요합니다.",
                        fix_fn=lambda k=key, s=symptoms: _request_root_cause_fix(k, s),
                        fix_label="근본 원인 분석 요청",
                    ))
        except Exception as e:
            log.warning(f"[Errors] 패턴 분석 실패: {e}")
            _g_report("master", e, module=__name__)
        return findings


def _request_root_cause_fix(key: str, symptoms: list[str]):
    task = (
        f"ERRORS.md에서 `{key}` 관련 오류가 {len(symptoms)}번 반복 기록됐습니다. "
        f"증상 예시: {symptoms[0][:100]}. "
        f"코드베이스를 grep하여 근본 원인을 찾고 create_plan으로 영구 수정 계획을 세워주세요."
    )
    try:
        import jarvis_daemon as _dm
        _dm._run_react(task, max_steps=10, verbose=True)
    except Exception as e:
        _send_tg(f"⚠️ 근본원인 분석 ReAct 실패: {e}")


# ════════════════════════════════════════════════════════════════
# 6. LLMCodeAuditor — Claude가 코드를 직접 읽고 스스로 이슈 발견
# ════════════════════════════════════════════════════════════════

class LLMCodeAuditor:
    """규칙 없이 Claude API가 시니어 개발자처럼 코드를 읽고 이슈를 직접 판단한다.

    - 하드코딩·누락·불일치·날짜 오류 등 패턴에 구애받지 않고 자유롭게 감지
    - 발견 시 텔레그램 인라인 버튼으로 수정 승인 요청
    - 격일 모닝 브리핑 + 데몬 부팅 시 실행
    """

    # 감사 그룹: 관련 파일들을 묶어 한 번에 Claude에게 전달
    _AUDIT_GROUPS: list[dict] = [
        {
            "name": "WRITER 발행 파이프라인",
            "files": [
                "JARVIS02_WRITER/jarvis_main.py",
                "JARVIS02_WRITER/economic_poster.py",
            ],
            "focus": (
                "발행 완료 텔레그램 알림 형식 일관성, 플랫폼별 글자수 포함 여부, "
                "날짜/연도 처리(LLM 프롬프트에 현재 날짜 주입 여부), "
                "3개 플랫폼간 기능 대칭성"
            ),
        },
        {
            "name": "통합 대시보드 UI",
            "files": ["hub.py"],
            "focus": (
                "에이전트 번호·이름 레이블이 실제 폴더 구조(J00~J04)와 일치하는지, "
                "하드코딩된 숫자·날짜·URL, UI 표시 일관성, 포트 번호(8500)"
            ),
        },
        {
            "name": "스케줄러 & 잡 레지스트리",
            "files": [
                "JARVIS04_SCHEDULER/job_registry.py",
                "JARVIS04_SCHEDULER/job_catalog.py",
            ],
            "focus": (
                "callback 경로가 실제 파일·함수와 일치하는지, "
                "executor 설정 누락, 잡 ID 중복, misfire_grace_time 비정상값"
            ),
        },
        {
            "name": "공유 신경계",
            "files": [
                "shared/bus.py",
                "shared/notify.py",
                "shared/db.py",
            ],
            "focus": (
                "이벤트 publish 후 구독자 누락, DB 스키마 불일치, "
                "에러 무시 패턴, 동기/비동기 혼용 위험"
            ),
        },
    ]

    _MAX_CHARS = 10_000   # 파일당 최대 전달 글자 (앞 6000 + 뒤 4000)
    _COOLDOWN  = 86_400   # 동일 이슈 재알림 최소 간격 24h

    def _read_file(self, rel: str) -> str:
        path = ROOT / rel
        if not path.exists():
            return ""
        try:
            src = path.read_text(encoding="utf-8")
            if len(src) <= self._MAX_CHARS:
                return src
            head = src[: self._MAX_CHARS * 6 // 10]
            tail = src[-(self._MAX_CHARS * 4 // 10):]
            return head + "\n\n... [중략 — 파일 중간부 생략] ...\n\n" + tail
        except Exception:
            return ""

    def _audit_group(self, _client_unused, group: dict) -> list[Finding]:
        import json as _json

        blocks = []
        for rel in group["files"]:
            content = self._read_file(rel)
            if content:
                blocks.append(f"=== {rel} ===\n{content}")
        if not blocks:
            return []

        today_str = datetime.now().strftime("%Y년 %m월 %d일")
        combined  = "\n\n".join(blocks)

        prompt = f"""당신은 JARVIS 블로그 자동화 멀티에이전트 시스템의 수석 코드 감사자입니다.
오늘 날짜: {today_str}

[시스템 컨텍스트]
- JARVIS00_INFRA: 데몬 관리·시스템 상태
- JARVIS01_MASTER: 자연어→인텐트 라우팅 (LangGraph ReAct)
- JARVIS02_WRITER: 블로그 자동 발행 (네이버·티스토리)
- JARVIS03_RADAR: 트렌드 수집·키워드 분석 (대시보드는 hub.py :8500)
- JARVIS04_SCHEDULER: APScheduler 잡 단일 진입점

[감사 대상: {group['name']}]
감사 초점: {group['focus']}

[코드]
{combined}

시니어 개발자처럼 코드를 읽고, 실제로 문제가 되거나 개선이 필요한 부분을 찾아주세요.
찾아야 할 것:
- 하드코딩된 값이 동적이어야 하는 경우
- 한 파일엔 있는 기능이 다른 파일엔 빠진 경우 (비대칭)
- UI 레이블·이름이 실제 코드와 불일치
- 날짜·연도가 LLM 프롬프트에 주입되지 않아 오래된 정보를 쓸 가능성
- 에러 무시, 알림 누락, 데이터 손실 위험
- 기타 사용자가 알아야 할 문제

실제 문제만 반환하세요 (nitpick·스타일·성능 제외). 문제 없으면 빈 배열 [].

JSON 배열만 출력 (다른 텍스트 없이):
[{{"severity":"warning"|"critical","title":"40자 이내 제목","detail":"파일명·줄번호 포함 구체적 설명 150자 이내","file":"파일경로"}}]"""

        try:
            from shared.llm import invoke_text as _inv_cli
            raw = (_inv_cli("writer", prompt, timeout=120) or "").strip()
            if not raw:
                return []
            # JSON 블록 추출 (마크다운 코드펜스 감싸인 경우 처리)
            if "```" in raw:
                raw = re.sub(r"```[^\n]*\n?", "", raw).strip()
            if not raw:
                return []
            issues: list[dict] = _json.loads(raw)
            if not isinstance(issues, list):
                return []
        except Exception as e:
            log.warning(f"[LLMAudit:{group['name']}] 응답 파싱 실패: {e}")
            _g_report("master", e, module=__name__)
            return []

        findings = []
        for issue in issues[:6]:
            try:
                key = f"llm_audit:{issue.get('file','')}:{issue.get('title','')[:30]}"
                title  = str(issue.get("title", "?"))[:50]
                detail = str(issue.get("detail", ""))[:200]
                sev    = issue.get("severity", "warning")
                findings.append(Finding(
                    key=key,
                    severity=sev,
                    title=f"🤖 {title}",
                    detail=detail,
                    fix_fn=lambda t=title, d=detail: _request_llm_fix(t, d),
                    fix_label="ReAct로 수정 계획 수립",
                ))
            except Exception:
                continue
        return findings

    def check(self) -> list[Finding]:
        findings = []
        for group in self._AUDIT_GROUPS:
            try:
                found = self._audit_group(None, group)
                findings += found
                log.info(f"[LLMAudit] {group['name']}: {len(found)}건 발견")
            except Exception as e:
                log.warning(f"[LLMAudit] {group['name']} 감사 실패: {e}")
                _g_report("master", e, module=__name__)

        return findings


def _request_llm_fix(title: str, detail: str):
    """LLMAudit이 발견한 이슈를 ReAct create_plan으로 수정 위임."""
    task = (
        f"자율 코드 감사에서 발견된 이슈를 수정해주세요.\n"
        f"제목: {title}\n"
        f"상세: {detail}\n\n"
        f"관련 파일을 읽고 create_plan으로 수정 계획을 수립한 뒤 사용자 승인을 받아 실행해주세요."
    )
    try:
        import jarvis_daemon as _dm
        _dm._run_react(task, max_steps=10, verbose=True)
    except Exception as e:
        _send_tg(f"⚠️ LLMAudit 수정 ReAct 실패: {e}")


# ════════════════════════════════════════════════════════════════
# 7. MorningBriefing — 08:30 일일 능동 브리핑
# ════════════════════════════════════════════════════════════════

class MorningBriefing:

    def run(self) -> str:
        now = datetime.now()
        lines = [f"🌅 *JARVIS 모닝 브리핑 — {now.strftime('%m/%d (%a) %H:%M')}*", "━" * 18]

        # 1) 오늘 잡 계획
        lines.append("📅 *오늘 예정 잡*")
        try:
            from JARVIS04_SCHEDULER.job_catalog import list_jobs
            today_jobs = []
            for j in list_jobs():
                nr = j.get("next_run")
                if nr and nr.startswith(now.strftime("%Y-%m-%d")):
                    today_jobs.append((nr[11:16], j["name"]))
            today_jobs.sort()
            if today_jobs:
                for t, n in today_jobs[:8]:
                    lines.append(f"   {t} {n}")
            else:
                lines.append("   (오늘 예정 잡 없음)")
        except Exception as e:
            lines.append(f"   ⚠️ 잡 조회 실패: {e}")

        lines.append("━" * 18)

        # 2) 어제 성과 요약
        lines.append("📊 *어제 성과*")
        try:
            from shared import db as _db
            yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
            with _db.get_db() as conn:
                pub = conn.execute(
                    "SELECT COUNT(*) c, platform FROM posts "
                    "WHERE DATE(published_at)=? AND status='published' GROUP BY platform",
                    (yesterday,),
                ).fetchall()
                if pub:
                    for r in pub:
                        lines.append(f"   {r['platform']}: {r['c']}건 발행")
                else:
                    lines.append("   어제 발행 없음")

                # 품질 평균
                qa = conn.execute(
                    "SELECT AVG(quality_score) s, COUNT(*) c FROM post_analysis "
                    "WHERE DATE(analyzed_at)=?",
                    (yesterday,),
                ).fetchone()
                if qa and qa["s"]:
                    lines.append(f"   품질 평균: {qa['s']:.1f}점 ({qa['c']}건 분석)")
        except Exception as e:
            lines.append(f"   ⚠️ 성과 조회 실패: {e}")

        lines.append("━" * 18)

        # 3) 24h 잡 실행 통계
        lines.append("⚙️ *24h 잡 실행*")
        try:
            from JARVIS04_SCHEDULER.job_history import summarize_recent
            stats = summarize_recent(hours=24)
            total = stats.get("total", 0)
            succ = stats.get("success", 0)
            fail = stats.get("fail", 0)
            lines.append(f"   총 {total}회 | ✅ {succ} | ❌ {fail}")
            if stats.get("fail_jobs"):
                lines.append("   실패 잡:")
                for fj in stats["fail_jobs"][:3]:
                    lines.append(f"   • {fj.get('job_name','?')}: {str(fj.get('error',''))[:60]}")
        except Exception as e:
            lines.append(f"   ⚠️ 잡 통계 실패: {e}")

        lines.append("━" * 18)

        # 4) 시스템 이상 요약 (EnvHealthChecker + IntegrityChecker + 주간 CodeQuality)
        lines.append("🔍 *시스템 자가진단*")
        all_findings: list[Finding] = []
        try:
            all_findings += EnvHealthChecker().check()
            all_findings += IntegrityChecker().check()
        except Exception:
            pass
        # 규칙 기반 코드 품질 감사 — 매일
        try:
            all_findings += CodeQualityChecker().check()
        except Exception:
            pass
        # LLM 자율 코드 감사 — 격일 (짝수일): Claude가 직접 코드 읽고 판단
        if now.day % 2 == 0:
            try:
                all_findings += LLMCodeAuditor().check()
            except Exception:
                pass

        criticals = [f for f in all_findings if f.severity == "critical"]
        warnings  = [f for f in all_findings if f.severity == "warning"]
        if not all_findings:
            lines.append("   ✅ 이상 없음")
        else:
            if criticals:
                lines.append(f"   🔴 위험 {len(criticals)}건: " + ", ".join(f.title[:30] for f in criticals[:2]))
            if warnings:
                lines.append(f"   🟡 경고 {len(warnings)}건: " + ", ".join(f.title[:30] for f in warnings[:2]))

        lines.append("━" * 18)
        lines.append("/status · /jobs · /help")
        msg = "\n".join(lines)
        _send_tg(msg)

        # actionable findings 별도 승인 요청
        actionable = [f for f in all_findings if f.fix_fn is not None]
        if actionable:
            _dispatch_findings(actionable, "모닝 브리핑")

        return msg


# ════════════════════════════════════════════════════════════════
# 공개 진입점
# ════════════════════════════════════════════════════════════════

def boot_check():
    """데몬 부팅 후 호출 — 백그라운드 스레드에서 실행.

    LLMCodeAuditor 완전 제거 (외부 API 비용 발생 경로).
    코드 자가 진단·수정은 JARVIS07_GUARDIAN/auto_repair.py 가 담당:
      08:30 / 18:00 (job_registry auto_repair_morning / auto_repair_evening 잡, Sonnet 4.6).
    """
    def _run():
        time.sleep(15)  # 모든 에이전트 등록 완료 대기
        log.info("[PM] boot_check 시작 (LLM감사=OFF — auto_repair 잡으로 대체)")
        findings: list[Finding] = []
        try:
            findings += IntegrityChecker().check()
        except Exception as e:
            log.warning(f"[PM] IntegrityChecker 오류: {e}")
            _g_report("master", e, module=__name__)
        try:
            findings += EnvHealthChecker().check()
        except Exception as e:
            log.warning(f"[PM] EnvHealthChecker 오류: {e}")
            _g_report("master", e, module=__name__)
        try:
            findings += CodeQualityChecker().check()
        except Exception as e:
            log.warning(f"[PM] CodeQualityChecker 오류: {e}")
            _g_report("master", e, module=__name__)
        try:
            findings += ErrorsPatternAnalyzer().check()
        except Exception as e:
            log.warning(f"[PM] ErrorsPatternAnalyzer 오류: {e}")
            _g_report("master", e, module=__name__)

        if findings:
            _dispatch_findings(findings, "부팅 자가진단")
            log.info(f"[PM] boot_check 완료: finding {len(findings)}건")
        else:
            log.info("[PM] boot_check 완료: 이상 없음")

    t = threading.Thread(target=_run, daemon=True, name="PM_boot_check")
    t.start()


def hourly_check():
    """매시간 호출 — APScheduler 잡에서 실행."""
    log.info("[PM] hourly_check 시작")
    findings: list[Finding] = []
    try:
        findings += JobHealthMonitor().check()
    except Exception as e:
        log.warning(f"[PM] JobHealthMonitor 오류: {e}")
        _g_report("master", e, module=__name__)
    try:
        findings += ContentQualityMonitor().check()
    except Exception as e:
        log.warning(f"[PM] ContentQualityMonitor 오류: {e}")
        _g_report("master", e, module=__name__)
    try:
        findings += IntegrityChecker().check()
    except Exception as e:
        log.warning(f"[PM] IntegrityChecker(hourly) 오류: {e}")
        _g_report("master", e, module=__name__)

    if findings:
        _dispatch_findings(findings, "시간당 자가진단")
    log.info(f"[PM] hourly_check 완료: finding {len(findings)}건")


def execute_fix(fix_id: str):
    """pm_yes 콜백에서 호출 — 저장된 단일 fix_fn 실행 (레거시 호환)."""
    with _PENDING_LOCK:
        entry = _PENDING_PM.pop(fix_id, None)
    if not entry:
        _send_tg("⚠️ 만료되었거나 이미 처리된 요청입니다.")
        return
    fn = entry.get("fix_fn")
    desc = entry.get("desc", "?")
    if fn is None:
        _send_tg(f"ℹ️ `{desc}` — 자동 수정 함수 없음. 수동 처리 필요.")
        return
    _send_tg(f"🔧 수정 시작: *{desc}*")
    try:
        fn()
    except Exception as e:
        _send_tg(f"❌ 수정 실패: {e}")


def execute_batch_fix(batch_id: str):
    """pm_batch_yes 콜백에서 호출 — 배치 내 모든 fix_fn 순차 실행."""
    with _PENDING_LOCK:
        entry = _PENDING_PM.pop(batch_id, None)
    if not entry or not entry.get("batch"):
        _send_tg("⚠️ 만료되었거나 이미 처리된 배치입니다.")
        return
    items = entry.get("items", [])
    source = entry.get("source", "자가진단")
    total = len(items)
    ok = 0
    _send_tg(f"🔧 *[{source}]* 수정 시작 — {total}건")
    for item in items:
        fn   = item.get("fix_fn")
        desc = item.get("desc", "?")
        if fn is None:
            _send_tg(f"  ℹ️ `{desc}` — 수동 처리 필요")
            continue
        try:
            fn()
            ok += 1
            _send_tg(f"  ✅ {desc}")
        except Exception as e:
            _send_tg(f"  ❌ {desc}: {e}")
    _send_tg(f"🎉 수정 완료 — {ok}/{total}건 성공")


__all__ = [
    "boot_check", "hourly_check",
    "execute_fix", "execute_batch_fix",
    "_PENDING_PM",
    "IntegrityChecker", "JobHealthMonitor", "EnvHealthChecker",
    "ContentQualityMonitor", "ErrorsPatternAnalyzer", "MorningBriefing",
]
