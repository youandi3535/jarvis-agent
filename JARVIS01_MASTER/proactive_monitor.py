"""JARVIS01_MASTER/proactive_monitor.py — 능동형 시스템 자가진단 모니터

자가진단 finding 의 텔레그램 송출 + 승인-후 수정 실행 진입점.
(★ 옛 능동형 체커 클러스터(자가진단 6종·부팅/시간별 체크)는 죽은 레거시라 삭제 — 2026-07-06.
seo_learner·bot 이 쓰는 알림/수정 헬퍼만 존속.)

제공 API:
  - Finding / _dispatch_findings() : finding → 텔레그램 (버튼 승인 포함)
  - _send_tg() / _send_tg_buttons() : 알림 송출
  - execute_fix(id) / execute_batch_fix(id) : pm_yes/pm_batch_yes 콜백 → 저장된 fix 실행
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
    "Finding", "_send_tg", "_send_tg_buttons", "_dispatch_findings",
    "execute_fix", "execute_batch_fix", "_PENDING_PM",
]
