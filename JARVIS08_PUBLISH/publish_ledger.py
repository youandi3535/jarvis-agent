"""발행 완결성 원장 — "오늘 나갔어야 할 글이 실제로 나갔는가" 단일 진입점.

★ 왜 만드나 (2026-07-29 전수 감사 1위 발견 — 사용자 승인)
  2026-07-12~28 실측: 기대 68건(4조합 × 17일) 중 **결손 18건**, 달성률 73.5%, 결손일 9일.
  같은 기간 `job_runs` 는 경제 23/23 · 테마 14/14 **전부 success=1** 이었다.

  왜 어긋나는가 — 발행 잡은 *자기가 예외 없이 끝까지 돌았는가* 만 안다.
  *글이 실제로 나갔는가* 는 아무도 묻지 않았다. 둘은 다른 질문이고,
  이 시스템에서 의미 있는 것은 **뒤엣것 하나뿐**이다(유일한 산출물이므로).

  감지 장치 실측: `EVENT_JOB_MISSED` 리스너가 저장소 전체에 **0건** 인데
  `logs/daemon.log` 의 `was missed by` 는 **101건**. 즉 산출물의 1/4이 매주 조용히
  사라지는 동안 계기판은 초록불이었고, 사용자는 그 사실 자체를 몰랐다.

★ 왜 종전 `log_monitor.py` 를 대체하는가
  그 파일은 로그 텍스트에 `"네이버"` 와 `"✅"` 가 각각 한 번이라도 있으면 성공으로 봤다.
  30KB 로그에서 이모지 하나로 판정하니 10일 표본에서 **위양성 2일·위음성 1일**.
  틀린 초록불은 없느니만 못하다 → **DB 사실(post_analysis 행 존재)** 로 판정한다.

★ 왜 기대 집합을 코드에 박지 않는가 (② 동적 설계)
  post_type  ← `DEFAULT_JOBS` 발행 잡의 callback 접미사 (`run_self_repair_then_<타입>`)
  platform   ← `JARVIS08_PUBLISH/platforms/` 의 `post_to_<플랫폼>` (AST 파생 — import 0)
  발행 잡이 늘거나 플랫폼이 추가되면 기대 집합이 **자동으로** 따라온다.
  `{"economic","theme"} × {"naver","tistory"}` 를 리터럴로 적으면 5번째 조합이 생긴 날
  그 조합만 조용히 감시 밖에 남는다 — 지금 고치는 병과 정확히 같은 병이다.

★ 왜 '지나간 슬롯 전부' 인가 (하루 1회가 아니라)
  07:50 감사는 경제만, 21:50 감사는 경제+테마를 본다. 아침 결손이 밤에 한 번 더
  보고되는 것은 중복이 아니라 **그날의 마감 요약** 이다. 데몬이 아침 내내 죽어 있어
  07:50 감사 자체가 안 돌았어도 밤에 잡힌다 — 감시자가 감시 밖에 있으면 안 된다.
"""
from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path

__all__ = [
    "expected_platforms",
    "expected_post_types",
    "due_post_types",
    "published_today",
    "publish_gaps",
    "publish_gap_error_type",
    "job_audit_publish_completeness",
]

_PLATFORM_DIR = Path(__file__).resolve().parent / "platforms"
_PUBLISH_CALLBACK_MARK = "run_self_repair_then_"


# ── 기대 집합 파생 ────────────────────────────────────────────────────────
def expected_platforms() -> list[str]:
    """`platforms/` 가 실제로 노출하는 발행 대상 — `post_to_<플랫폼>` 에서 파생.

    ★ import 하지 않고 AST 로 읽는다. `naver_poster` 를 import 하면 selenium·Quartz 까지
      끌려오는데, 감사 잡은 발행하지 않으므로 그 비용과 실패 위험을 질 이유가 없다.
    """
    out: set[str] = set()
    for py in _PLATFORM_DIR.glob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("post_to_"):
                name = node.name[len("post_to_"):]
                if name:
                    out.add(name)
    return sorted(out)


def _publish_jobs() -> list[tuple[str, int, int]]:
    """(post_type, 시, 분) — `DEFAULT_JOBS` 의 발행 잡에서 파생.

    callback 접미사가 곧 글 종류이고, 그 어휘는 `post_analysis.post_type` 과 같다.
    (`run_self_repair_then_economic` → `'economic'`)
    """
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS  # lazy — 순환 import 회피

    out: list[tuple[str, int, int]] = []
    for j in DEFAULT_JOBS:
        cb = j.get("callback") or ""
        if j.get("trigger") != "cron" or _PUBLISH_CALLBACK_MARK not in cb:
            continue
        post_type = cb.rsplit(_PUBLISH_CALLBACK_MARK, 1)[-1].strip()
        kw = j.get("kwargs") or {}
        h = kw.get("hour")
        if post_type and isinstance(h, int):
            out.append((post_type, h, int(kw.get("minute") or 0)))
    return out


def expected_post_types() -> list[str]:
    """오늘 발행되어야 할 글 종류 전체."""
    return sorted({pt for pt, _, _ in _publish_jobs()})


def due_post_types(now: _dt.datetime | None = None) -> list[str]:
    """지금 시점에서 *이미 발행 시각이 지난* 글 종류 — 아직 안 온 슬롯을 결손으로 오인하지 않는다."""
    now = now or _dt.datetime.now()
    cur = now.hour * 60 + now.minute
    return sorted({pt for pt, h, m in _publish_jobs() if h * 60 + m <= cur})


# ── 실제 발행 조회 ────────────────────────────────────────────────────────
def published_today(day: _dt.date | None = None) -> set[tuple[str, str]]:
    """오늘 실제로 발행된 (post_type, platform) 집합.

    ★ `created_at` 을 쓴다 — 실측 244/244 채워져 있고 발행 시각이다.
      `analyzed_at` 은 234/244 뿐이라(분석이 안 돈 글이 있다) 결손 오탐을 만든다.
    """
    from shared.db import get_db

    day = day or _dt.date.today()
    with get_db() as con:
        rows = con.execute(
            "SELECT DISTINCT post_type, platform FROM post_analysis WHERE date(created_at) = ?",
            (day.isoformat(),),
        ).fetchall()
    return {(r[0], r[1]) for r in rows if r[0] and r[1]}


def publish_gaps(now: _dt.datetime | None = None) -> list[tuple[str, str]]:
    """지금까지 나갔어야 하는데 안 나간 (post_type, platform) — 기대 ∖ 실제."""
    now = now or _dt.datetime.now()
    platforms = expected_platforms()
    expected = {(pt, pf) for pt in due_post_types(now) for pf in platforms}
    return sorted(expected - published_today(now.date()))


# ── 오류 타입 파생 (CLAUDE.md 오류 세분화 규정 — ERRORS [547]) ─────────────
def publish_gap_error_type(post_type: str, platform: str) -> str:
    """결손 1건의 오류 타입 — *이미 있는 판단*(글종류·플랫폼)에서 기계적으로 만든다.

    중앙 매핑표를 두지 않는다. 새 글종류·새 플랫폼이 생기면 타입이 자동으로 따라온다.
    예: ('economic','tistory') → 'PublishGapEconomicTistory'
    """
    return "PublishGap" + post_type.capitalize() + platform.capitalize()


# ── 잡 콜백 ───────────────────────────────────────────────────────────────
def job_audit_publish_completeness() -> dict:
    """발행 완결성 감사 — 결손이 있으면 텔레그램 + GUARDIAN 박제.

    결손 0건이면 조용히 통과한다(알림 피로 방지). 시스템이 자기 상태를 실제보다
    좋게 보고하는 것을 막는 게 목적이지, 매일 초록불을 보고하는 게 목적이 아니다.
    """
    now = _dt.datetime.now()
    gaps = publish_gaps(now)
    due = due_post_types(now)
    platforms = expected_platforms()
    total = len(due) * len(platforms)

    result = {
        "checked_at": now.isoformat(timespec="seconds"),
        "due_post_types": due,
        "platforms": platforms,
        "expected": total,
        "published": total - len(gaps),
        "gaps": [f"{pt}/{pf}" for pt, pf in gaps],
    }
    if not gaps:
        print(f"  ✅ 발행 완결성 — {total}/{total} 정상 ({', '.join(due) or '해당 없음'})")
        return result

    from JARVIS07_GUARDIAN.error_collector import report

    for pt, pf in gaps:
        report(
            "publish",
            RuntimeError(f"{pt} 글이 {pf} 에 발행되지 않았다 (감사 {now:%H:%M})"),
            module=__name__,
            func_name="job_audit_publish_completeness",
            error_type=publish_gap_error_type(pt, pf),
            context={"post_type": pt, "platform": pf, "checked_at": result["checked_at"]},
        )

    lines = [
        f"🚨 *발행 결손 {len(gaps)}건* — {now:%m/%d %H:%M} 감사",
        "",
        f"기대 {total}건 중 *{total - len(gaps)}건* 발행 · *{len(gaps)}건 누락*",
        "",
        *[f"  ❌ {pt} → {pf}" for pt, pf in gaps],
        "",
        "_잡은 성공으로 기록됐지만 글이 나가지 않았습니다._",
    ]
    try:
        from shared.notify import send_tg

        send_tg("\n".join(lines))
    except Exception as e:  # 알림 실패가 감사 자체를 죽이지 않게
        print(f"  ⚠️ 발행 결손 알림 전송 실패: {e}")

    print(f"  🚨 발행 결손 {len(gaps)}건: {result['gaps']}")
    return result
