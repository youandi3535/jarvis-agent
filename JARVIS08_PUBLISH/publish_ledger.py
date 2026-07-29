"""발행 완결성 원장 — "이번 슬롯에 나갔어야 할 글이 실제로 나갔는가" 단일 진입점.

★ 왜 만드나 (2026-07-29 전수 감사 1위 — 사용자 승인)
  2026-07-12~28 실측: 기대 68건(4조합 × 17일) 중 **결손 18건**, 달성률 73.5%, 결손일 9일.
  같은 기간 `job_runs` 는 경제 23/23 · 테마 14/14 **전부 success=1** 이었다.
  발행 잡은 *자기가 끝까지 돌았는가* 만 안다. *글이 나갔는가* 는 아무도 묻지 않았다.
  감지 장치 실측 — `EVENT_JOB_MISSED` 리스너 **0건** 인데 `daemon.log` `was missed` **101건**.

★ 종전 `log_monitor.py` 를 폐기한 이유
  로그 텍스트에 `"네이버"` 와 `"✅"` 가 각각 한 번이라도 있으면 성공으로 봤다.
  30KB 로그를 이모지 하나로 판정하니 10일 표본 **위양성 2일·위음성 1일**.
  틀린 초록불은 없느니만 못하다 → **DB 사실(`post_analysis` 행 존재)** 로 판정한다.

★★ v2 — 초판(같은 날 커밋 54b9558)의 결함 3건을 고친 판 (2026-07-29)
  초판을 재감사에 걸었더니 **결손이 0건일 때만 정상 동작** 했다. 셋 다 실측으로 확인:

  ① **결손을 발견하면 그 순간 죽었다.** `report(..., error_type=...)` 를 불렀는데
     `error_collector.report` 에 그런 인자가 없다(`sig.bind()` → TypeError).
     루프가 try 로 감싸여 있지 않고 텔레그램 경보는 그 **뒤에** 있어서,
     *결손을 알리려고 만든 코드가 결손이 났을 때만 침묵* 했다.
     → `report(<타입문자열>, "publish", message=...)` 로 교정(첫 인자가 문자열이면 그게 곧
       error_type — `error_collector.report:51`). 루프를 try 로 감싸 박제 실패가 경보를 못 막게 한다.
  ② **달력 날짜로 판정했다.** `WHERE date(created_at) = 오늘` 이라, 21:00 테마가 자정을 넘겨
     끝나면(실측 07-21 00:51·01:06 — 07-20 슬롯의 산출물) 그날은 결손으로 **오신고** 되고
     다음날엔 그 두 행이 '오늘 실적' 으로 세어져 **진짜 실패가 초록불** 이 된다.
     연속 장애일수록 탐지가 꺼지는 최악의 방향이었다.
     → 판정 단위를 '날짜' 가 아니라 **슬롯 창**(이번 발행 시각 ~ 다음 발행 시각)으로 바꿨다.
       자정 넘김이 구조적으로 사라진다.
  ③ **감사 시각이 너무 일렀다.** 초판은 발행 +50분 고정. 실측 59건 중 **19건(32%)** 이
     +50분을 넘겼다(최대 +246분). 그대로 뒀으면 폐기한 `log_monitor` 의 위양성을
     *방향만 바꿔 재도입* 하는 꼴이었다.
     → 지연을 **이미 있는 판단에서 파생**: `misfire_grace_time`(잡이 늦게 시작될 수 있는 상한)
       + 플랫폼 수 × `BLOG_ACTION_DEADLINE_SEC`(플랫폼당 상한, watchdog SSOT).
       추가로 **발행 락이 잡혀 있으면 '결손' 이 아니라 '지연'** 으로 분류한다 —
       아직 돌고 있는 것을 실패라 부르지 않는다.

★ 기대 집합을 코드에 박지 않는 이유 (② 동적 설계)
  post_type  ← `DEFAULT_JOBS` 발행 잡의 callback 접미사 (`run_self_repair_then_<타입>`)
  platform   ← `JARVIS08_PUBLISH/platforms/` 의 `post_to_<플랫폼>` (AST 파생 — import 0)
  발행 잡·플랫폼이 늘면 기대 집합이 자동으로 따라온다. `{"economic","theme"} ×
  {"naver","tistory"}` 를 리터럴로 적으면 5번째 조합이 생긴 날 그 조합만 감시 밖에 남는다.
"""
from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path

__all__ = [
    "expected_platforms",
    "publish_slots",
    "current_slot",
    "audit_lag_minutes",
    "published_in_slot",
    "slot_gaps",
    "publishing_in_progress",
    "publish_gap_error_type",
    "job_audit_publish_completeness",
]

_PLATFORM_DIR = Path(__file__).resolve().parent / "platforms"
_PUBLISH_CALLBACK_MARK = "run_self_repair_then_"

# 파생에 실패했을 때만 쓰는 폴백(초). 실측 최악치(+246분)를 덮는 값 —
# 감사가 *너무 이르면* 성공 발행을 결손으로 오신고하므로, 모르면 늦게 보는 쪽이 안전하다.
_LAG_FALLBACK_SEC = 4 * 3600


# ── 기대 집합 파생 ────────────────────────────────────────────────────────
def expected_platforms() -> list[str]:
    """`platforms/` 가 실제로 노출하는 발행 대상 — `post_to_<플랫폼>` 에서 파생.

    ★ import 하지 않고 AST 로 읽는다. `naver_poster` 를 import 하면 selenium·Quartz 까지
      끌려오는데, 감사 잡은 발행하지 않으므로 그 비용과 실패 위험을 질 이유가 없다.
      (job_registry 가 *부팅 중* 이 함수를 부르므로 순환 import 도 피해야 한다.)
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


def publish_slots() -> list[tuple[str, int, int]]:
    """(post_type, 시, 분) — `DEFAULT_JOBS` 의 발행 잡에서 파생.

    callback 접미사가 곧 글 종류이고 그 어휘는 `post_analysis.post_type` 과 같다.
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


def current_slot(now: _dt.datetime | None = None) -> tuple[str, _dt.datetime, _dt.datetime] | None:
    """지금 감사해야 할 슬롯 — (post_type, 슬롯 시작, 슬롯 끝).

    ★ '오늘 날짜' 가 아니라 **가장 최근에 시작된 발행 슬롯** 을 고르고, 그 창의 끝은
      *다음 발행 슬롯 시각* 이다. 21:00 테마 슬롯의 창은 다음날 07:00 까지이므로
      자정을 넘겨 끝난 발행도 제 슬롯의 실적으로 세어진다(v2 결함② 수정).
    """
    now = now or _dt.datetime.now()
    slots = publish_slots()
    if not slots:
        return None

    started = []
    for pt, h, m in slots:
        for d in (0, -1):
            st = (now + _dt.timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0)
            if st <= now:
                started.append((st, pt))
    if not started:
        return None
    start, post_type = max(started)

    ends = []
    for _pt, h, m in slots:
        for d in (0, 1):
            en = (start + _dt.timedelta(days=d)).replace(hour=h, minute=m, second=0, microsecond=0)
            if en > start:
                ends.append(en)
    end = min(ends) if ends else start + _dt.timedelta(days=1)
    return post_type, start, end


def audit_lag_minutes(misfire_grace_sec: int = 0) -> int:
    """발행 시각 이후 이만큼 지나서 감사한다 — **이미 있는 판단에서 파생**.

    최악 = 잡이 늦게 시작될 수 있는 상한(`misfire_grace_time`)
         + 플랫폼 수 × 플랫폼당 상한(`watchdog.BLOG_ACTION_DEADLINE_SEC`)
    고정 리터럴(초판의 50분)로 두면 실측 32% 가 그 창을 넘겨 **성공을 결손으로 오신고** 한다.
    """
    try:
        from JARVIS00_INFRA.watchdog import BLOG_ACTION_DEADLINE_SEC as _deadline
        total = int(misfire_grace_sec) + len(expected_platforms()) * int(_deadline)
    except Exception:
        total = _LAG_FALLBACK_SEC
    return max(1, total // 60)


# ── 실제 발행 조회 ────────────────────────────────────────────────────────
def published_in_slot(start: _dt.datetime, end: _dt.datetime) -> set[str]:
    """슬롯 창 안에 실제로 발행된 플랫폼 집합.

    ★ `created_at` 을 쓴다 — 실측 244/244 채워져 있고 발행 시각이다.
      `analyzed_at` 은 234/244 뿐이라(분석이 안 돈 글이 있다) 결손 오탐을 만든다.
    """
    from shared.db import get_db

    with get_db() as con:
        rows = con.execute(
            "SELECT DISTINCT platform FROM post_analysis "
            "WHERE created_at >= ? AND created_at < ?",
            (start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()
    return {r[0] for r in rows if r[0]}


def slot_gaps(now: _dt.datetime | None = None) -> tuple[str, list[str], list[str]] | None:
    """(post_type, 결손 플랫폼, 기대 플랫폼) — 이번 슬롯 기준. 슬롯이 없으면 None."""
    slot = current_slot(now)
    if slot is None:
        return None
    post_type, start, end = slot
    platforms = expected_platforms()
    done = published_in_slot(start, end)
    return post_type, sorted(set(platforms) - done), platforms


def publishing_in_progress() -> bool:
    """발행이 *아직 돌고 있는가* — 락 파일 기준.

    아직 진행 중인 것을 '결손' 이라 부르면 안 된다. 지연과 실패는 다른 사건이고,
    다르게 알려야 사용자가 다르게 행동한다.
    """
    try:
        from JARVIS02_WRITER.scheduler import _is_locked_externally
        return bool(_is_locked_externally())
    except Exception:
        return False


# ── 오류 타입 파생 (CLAUDE.md 오류 세분화 규정 — ERRORS [547]) ─────────────
def publish_gap_error_type(post_type: str, platform: str) -> str:
    """결손 1건의 오류 타입 — *이미 있는 판단*(글종류·플랫폼)에서 기계적으로 만든다.

    중앙 매핑표를 두지 않는다. 새 글종류·새 플랫폼이 생기면 타입이 자동으로 따라온다.
    예: ('economic','tistory') → 'PublishGapEconomicTistory'
    """
    return "PublishGap" + post_type.capitalize() + platform.capitalize()


# ── 잡 콜백 ───────────────────────────────────────────────────────────────
def job_audit_publish_completeness() -> dict:
    """이번 슬롯의 발행 완결성 감사 — 결손이면 텔레그램 + GUARDIAN 박제.

    결손 0건이면 조용히 통과한다(알림 피로 방지). 목적은 시스템이 자기 상태를 실제보다
    좋게 보고하는 것을 막는 것이지, 매일 초록불을 보고하는 것이 아니다.
    """
    now = _dt.datetime.now()
    res = slot_gaps(now)
    if res is None:
        print("  ⚠️ 발행 슬롯을 파생하지 못했다 — 감사 생략")
        return {"checked_at": now.isoformat(timespec="seconds"), "error": "no_slot"}

    post_type, gaps, platforms = res
    _pt, start, end = current_slot(now)
    in_progress = publishing_in_progress()
    result = {
        "checked_at": now.isoformat(timespec="seconds"),
        "post_type": post_type,
        "slot": f"{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M}",
        "expected": len(platforms),
        "published": len(platforms) - len(gaps),
        "gaps": gaps,
        "in_progress": in_progress,
    }

    if not gaps:
        print(f"  ✅ 발행 완결성 — {post_type} {len(platforms)}/{len(platforms)} 정상 ({result['slot']})")
        return result

    if in_progress:
        # 아직 돌고 있다 — 실패가 아니라 지연. 박제하지 않는다(오탐이 학습을 오염시킨다).
        print(f"  ⏳ {post_type} {len(gaps)}건 미완 — 발행 진행 중(지연): {gaps}")
        try:
            from shared.notify import send_tg
            send_tg(f"⏳ *발행 지연* — {post_type} {'·'.join(gaps)} 아직 진행 중 ({now:%H:%M})")
        except Exception as e:
            print(f"  ⚠️ 지연 알림 전송 실패: {e}")
        return result

    for pf in gaps:
        try:
            # ★ 첫 인자에 문자열을 주면 그것이 곧 error_type (error_collector.report:51).
            #   `error_type=` 키워드는 존재하지 않는다 — 초판이 그걸 불러 여기서 죽었다.
            from JARVIS07_GUARDIAN.error_collector import report
            report(
                publish_gap_error_type(post_type, pf),
                "publish",
                message=f"{post_type} 글이 {pf} 에 발행되지 않았다 (슬롯 {result['slot']})",
                module=__name__,
                func_name="job_audit_publish_completeness",
                context={"post_type": post_type, "platform": pf, "slot": result["slot"]},
            )
        except Exception as e:
            # 박제 실패가 사용자 경보까지 죽이면 안 된다 — 초판의 진짜 사고 원인.
            print(f"  ⚠️ 결손 박제 실패({post_type}/{pf}): {e}")

    lines = [
        f"🚨 *발행 결손 {len(gaps)}건* — {post_type} ({now:%m/%d %H:%M} 감사)",
        "",
        f"슬롯 {result['slot']}",
        f"기대 {len(platforms)}건 중 *{len(platforms) - len(gaps)}건* 발행 · *{len(gaps)}건 누락*",
        "",
        *[f"  ❌ {post_type} → {pf}" for pf in gaps],
        "",
        "_잡은 성공으로 기록됐지만 글이 나가지 않았습니다._",
    ]
    try:
        from shared.notify import send_tg
        send_tg("\n".join(lines))
    except Exception as e:
        print(f"  ⚠️ 발행 결손 알림 전송 실패: {e}")

    print(f"  🚨 발행 결손 {len(gaps)}건 ({post_type}): {gaps}")
    return result
