"""
JARVIS05_VISION/jobs.py — VISION 자동화 잡 콜백.

DEFAULT_JOBS 에 등록된 잡:
  j05_sla_monitor   — 15분마다 스케줄 잡 SLA 점검 → 누락 시 텔레그램 경고
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import requests as _req

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis.vision.jobs")

_TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _tg(msg: str) -> None:
    if not (_TG_TOKEN and _TG_CHAT_ID):
        return
    try:
        _req.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT_ID, "text": msg},
            timeout=8,
        )
    except Exception:
        pass


# ── SLA 정의 ────────────────────────────────────────────────────
# job_id → 허용 최대 미실행 시간(분). None = SLA 미적용.
# 매일 1회 잡은 25시간(데몬 재시작 등 여유), interval 잡은 주기×3 으로 설정.
_SLA: dict[str, int] = {
    # interval 잡 — 주기×4 로 여유 확보
    "analyzer_fb":             20,    # 5분 interval → 20분
    "auto_approve":            130,   # 30분 interval → 130분

    # 하루 1회 cron 잡 — 25시간(=25h): 당일 실행 후 다음날 실행 전까지 경고 없음
    "radar_perf":              1500,  # 23:00 1회
    "j01_daily_report":        1500,  # 23:55 1회
    "j01_economic_post":       1500,  # 07:00 1회
    "j01_theme_post_21":       1500,  # 21:00 1회
"db_backup":               1500,  # 03:00 1회

    # 하루 3회 cron 잡 — 최대 간격(18h 야간) + 여유 = 1100분
    "radar_trends_09":         1100,  # 09/12/15시 → 야간 최대 18h 공백
    "radar_trends_12":         1100,
    "radar_trends_15":         1100,
}

# 이미 알림 보낸 잡 추적 (데몬 재시작 전까지 중복 알림 방지)
_alerted: set[str] = set()


# ── 잡 1: SLA 모니터 — 비활성화 (2026-05-09 사용자 요청: 경고 완전 제거) ──

def job_sla_monitor() -> None:
    """비활성화됨 — 텔레그램 SLA 경고 영구 제거."""
    pass
