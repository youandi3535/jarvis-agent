"""JARVIS04_SCHEDULER/briefing.py — 잡 종합 리포트 빌더.

사용자가 자연어로 "오늘 잡 실행 어땠어?" → SAFE 도구 get_today_briefing.
또는 /jobs_report 명령어로 수동 조회.
"""
from __future__ import annotations

from JARVIS04_SCHEDULER.job_history import summarize_recent
from JARVIS04_SCHEDULER.job_catalog import next_runs


def build_briefing_text(hours: int = 24) -> str:
    """일일 잡 종합 리포트 — 컴팩트 버전 (~10줄).

    이전: ~23줄 (헤더+요약 2줄·에이전트별 4줄·실패 11줄·예정 6줄).
    현재: ~10줄 (헤더 1줄·에이전트 1줄·실패 3줄·예정 3줄).
    """
    s = summarize_recent(hours)
    if "error" in s:
        return f"⚠️ 잡 통계 조회 실패: {s['error']}"

    total = s.get("total", 0)
    success = s.get("success", 0)
    fail = s.get("fail", 0)

    # 1) 헤더 + 요약 (1줄)
    lines = [f"🗓 *잡 리포트 {hours}h* — ✅{success} / ❌{fail} (총 {total}회)"]

    # 2) 에이전트별 (1줄, 'j00:34 · j01:10 · j02:820 · infra:2')
    by_owner = s.get("by_owner") or {}
    if by_owner:
        import re as _re
        def _short(o: str) -> str:
            if not o:
                return "?"
            # 'jarvisNN_xxx' → 'jNN' (예: jarvis02_writer → j01)
            m = _re.match(r'jarvis(\d+)_', o)
            if m:
                return f"j{m.group(1)}"
            # 'jarvis_X' → 'X' (예: jarvis00_infra → infra)
            if o.startswith("jarvis_"):
                return o[7:]
            return o
        agg = " · ".join(
            f"{_short(o)}:{st['total']}"
            for o, st in sorted(by_owner.items())
        )
        lines.append(agg)

    # 3) 실패 잡 — 최대 3개, 1줄씩 (에러 50자)
    if fail and s.get("fail_jobs"):
        lines.append("")
        lines.append("❌ *실패*")
        for r in s["fail_jobs"][:3]:
            err = (r.get("error") or "")[:50]
            lines.append(f"  `{r['job_id']}` — {err}")

    # 4) 다음 예정 — 헤더 + 3개 (ID 만)
    nxt = next_runs(limit=3)
    if nxt:
        lines.append("")
        lines.append("⏰ *다음 예정*")
        for j in nxt:
            lines.append(f"  {j['next_run']} `{j['id']}`")

    return "\n".join(lines)


__all__ = ["build_briefing_text"]
