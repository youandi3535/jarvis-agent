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
  post_type  ← `job_llm_priority.publish_post_type()` (마커 소유자가 접미사에서 파생)
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
    "owning_slot",
    "already_published_this_slot",
    "slot_gaps",
    "publishing_in_progress",
    "publish_gap_error_type",
    "job_audit_publish_completeness",
]

_PLATFORM_DIR = Path(__file__).resolve().parent / "platforms"

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

    발행 잡 판별과 글 종류 파생 모두 마커 소유자(`job_llm_priority`) 단독 —
    여기에 마커 문자열 사본을 두지 않는다.
    """
    # lazy — 순환 import 회피. 판별·글종류 파생은 마커 소유자(job_llm_priority) 단독.
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback, publish_post_type

    out: list[tuple[str, int, int]] = []
    for j in DEFAULT_JOBS:
        if j.get("trigger") != "cron" or not is_publish_callback(j.get("callback")):
            continue
        post_type = publish_post_type(j.get("callback"))
        kw = j.get("kwargs") or {}
        h = kw.get("hour")
        if post_type and isinstance(h, int):
            out.append((post_type, h, int(kw.get("minute") or 0)))
    return out


def slots_between(start: _dt.datetime, end: _dt.datetime) -> list:
    """[start, end) 안에 **발행 시각이 든** 슬롯 전부 — [(post_type, 슬롯시작, 슬롯끝), ...].

    ★ 슬롯 경계 계산의 **단일 소스** (2026-08-05). `current_slot()` 도 여기서 파생한다.
      종전엔 `current_slot()` 안에 시작용·끝용 day-offset 루프가 2벌 인라인이었고,
      임의 구간(= 데몬이 꺼져 있던 구간)을 물어볼 길이 아예 없었다.
      슬롯 끝은 언제나 *다음 발행 시각* — 자정을 넘긴 21시 테마가 제 슬롯으로 계산되는
      v2 규칙이 여기 한 곳에만 산다.
    """
    slots = publish_slots()
    if not slots or end <= start:
        return []
    # 경계(다음 슬롯 시각)를 얻으려면 구간 앞뒤로 하루씩 여유가 필요하다.
    span: list = []
    d = (start - _dt.timedelta(days=1)).date()
    last = (end + _dt.timedelta(days=1)).date()
    while d <= last:
        for pt, h, m in slots:
            span.append((_dt.datetime.combine(d, _dt.time(h, m)), pt))
        d += _dt.timedelta(days=1)
    span.sort()
    out = []
    for i, (st, pt) in enumerate(span):
        en = span[i + 1][0] if i + 1 < len(span) else st + _dt.timedelta(days=1)
        if start <= st < end:
            out.append((pt, st, en))
    return out


def slot_key(post_type: str, slot_start: _dt.datetime) -> str:
    """슬롯 1개의 전역 유일 식별자 — `economic@2026-08-05T07:00`.

    ★ 왜 필요한가: 종전 결손 박제의 context 는 `"08-05 07:00 ~ 08-05 21:00"` 이라
      **연도가 없어** 원장 키로 쓸 수 없었다. 같은 슬롯을 두 번 보고해도 막을 수단이 없다.
    """
    return f"{post_type}@{slot_start:%Y-%m-%dT%H:%M}"


def current_slot(now: _dt.datetime | None = None) -> tuple | None:
    """지금 감사해야 할 슬롯 — (post_type, 슬롯 시작, 슬롯 끝). 동작 불변.

    ★ '오늘 날짜' 가 아니라 **가장 최근에 시작된 발행 슬롯** 을 고르고, 그 창의 끝은
      *다음 발행 슬롯 시각* 이다. 경계 계산은 `slots_between()` 단독(사본 0).
    """
    now = now or _dt.datetime.now()
    cands = slots_between(now - _dt.timedelta(days=1), now + _dt.timedelta(seconds=1))
    return cands[-1] if cands else None


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
def published_in_slot(start: _dt.datetime, end: _dt.datetime,
                      post_type: str = "") -> set[str]:
    """슬롯 창 안에 **그 글종류로** 실제 발행된 플랫폼 집합.

    ★ `created_at` 을 쓴다 — 실측 244/244 채워져 있고 발행 시각이다.
      `analyzed_at` 은 234/244 뿐이라(분석이 안 돈 글이 있다) 결손 오탐을 만든다.
    ★ `post_type` 필터 (2026-08-04 추가 — 내 초판 결함):
      종전엔 창 안의 *모든* 글을 셌다. `post_analysis.post_type` 컬럼이 있는데도 안 썼다.
      테마 슬롯 창은 21:00~다음날 07:00 이라, 그 안에 다른 종류 글이 한 건이라도
      떨어지면 **테마 결손이 조용히 사라진다**. 지금 스케줄에선 경계가 맞물려 실피해가
      적지만, GUARDIAN 재발행이 창을 넘나들면 바로 발현한다.
      결손 감사는 *놓치는 쪽* 이 가장 나쁘다 — 감시가 꺼진 줄도 모르게 된다.
      (빈 문자열이면 종전처럼 전체 — 하위호환)
    """
    from shared.db import get_db

    sql = ("SELECT DISTINCT platform FROM post_analysis "
           "WHERE created_at >= ? AND created_at < ?")
    args = [start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")]
    if post_type:
        sql += " AND post_type = ?"
        args.append(post_type)
    with get_db() as con:
        rows = con.execute(sql, tuple(args)).fetchall()
    return {r[0] for r in rows if r[0]}


# ── 중복 발행 최종 방어선 (2026-08-07) ────────────────────────────────────
def owning_slot(post_type: str,
                now: _dt.datetime | None = None) -> tuple | None:
    """이 발행 시도가 속한 **그 글종류의** 슬롯 창. 창 밖이면 None.

    ★ `current_slot()` 과 다르다 — 헷갈리면 어제 글로 오늘 발행을 막는다.
      `current_slot` 은 *"지금 감사할 슬롯"* (시각 기준, 글종류 무관) 이라
      06:30 에 물으면 **직전 테마 창** 을 답한다. 실측으로 경제 잡이 06:30 에
      돈 날이 여러 날 있다(스케줄이 06:30→07:00 으로 바뀌기 전). 그 시각에
      "경제가 이미 나갔나" 를 시각 기준 창으로 물으면 *어제 경제 글* 이 잡혀
      **오늘 발행을 영구히 막는다** — 중복보다 나쁜 오탐이다.

    ★ 창 밖이면 None(=억제 안 함) 인 이유: 판정 불가일 때 어느 쪽으로 틀릴지의
      문제다. 중복 1건은 지우면 되지만 미발행은 그 회차가 영영 없다([553] 과 같은 축 —
      *정상 산출물을 막는 게 가장 나쁘다*).
    """
    now = now or _dt.datetime.now()
    # ★ `+1초` — 슬롯이 *정각에 시작* 하는 순간을 포함시킨다. `slots_between` 은 끝 경계를
    #   배타적으로 보므로 이게 없으면 07:00:00 정각에 "창 밖" 이 나온다(실측). 발행 잡이
    #   정각 기동이라 하필 가드가 가장 필요한 순간에 꺼진다. `current_slot` 도 같은 처리.
    window_end = now + _dt.timedelta(seconds=1)
    for pt, s, e in reversed(slots_between(now - _dt.timedelta(days=2), window_end)):
        if pt == post_type and s <= now < e:
            return (pt, s, e)
    return None


def already_published_this_slot(post_type: str, platform: str,
                                now: _dt.datetime | None = None) -> bool:
    """이번 회차에 이 (글종류·플랫폼) 글이 **DB 에 이미 있는가** — 발행 직전 최종 확인.

    ★ 왜 메모리 플래그로는 부족했나 (원칙① — 판단이 두 벌이었다)
      종전 중복 방지는 harness state 의 `__nv_send_attempted__` 류 불리언 **4개**가
      전부였고, 그 로직이 `economic_poster._send_platform` 과
      `trend_theme_writer._send_theme_platform` 에 **똑같이 두 벌** 적혀 있었다.
      플래그는 *한 프로세스·한 액션* 안에서만 산다. 실측으로 그것들이 못 막은 경로:

        · 같은 슬롯에 발행 잡이 2회 기동 — 최근 90일 **12회** (`job_runs`)
        · 재시도 콜백이 발행 전체를 다시 돌림 — 2026-07-20 21:00 네이버 **3건**
          (서로 다른 테마 3개. 그 *생성기* 는 커밋 bb436a9 에서 제거됐다)

      둘 다 **새 state 로 들어오기 때문에** 플래그가 구조적으로 못 본다.
      개별 재시도 경로를 하나씩 올바르게 고치는 방식은 다음 경로가 생기면 또 샌다 —
      마지막 방어선은 프로세스 밖(DB)에 있어야 한다.

    ★ 이건 '완벽한 exactly-once' 가 아니다 — 정직하게 적어 둔다.
      글이 플랫폼에 올라갔는데 발행자가 실패로 보고하면(ack 유실) `post_analysis`
      행이 안 생기므로 여기서도 못 잡는다. 그 창을 닫으려면 플랫폼 사실 조회가
      필요하다(네이버는 `_fetch_recent_naver_posts` 가 이미 있다). 별건.
    """
    slot = owning_slot(post_type, now)
    if not slot:
        return False
    _, s, e = slot
    return platform in published_in_slot(s, e, post_type)


def scoring_gaps(start: _dt.datetime, end: _dt.datetime,
                 post_type: str = "") -> list[tuple[int, str]]:
    """슬롯 창 안에 **발행은 됐는데 채점이 비어 있는** 글 — (id, platform).

    ★ 왜 원장이 이걸 보는가 (감사와 수리를 나눈다)
      원장이 답하는 질문은 하나다: *"이 슬롯은 완결됐는가?"* 발행만 되고 채점이 비면
      ADR 014 보상 신호가 안 생기므로 슬롯은 완결된 게 아니다. 그래서 여기서 *본다*.
      다만 **고치지는 않는다** — 재채점은 루브릭 주인(`post_quality_analyzer`) 일이다.
      감사가 수리까지 하면 두 도메인이 한 파일에 엉킨다.

    실측 배경(08-02~08-04): 티스토리 4건 중 3건이 `quality_score IS NULL` 로 남았고
    아무도 몰랐다 — 발행은 성공했으니 어떤 경보도 울리지 않았다.
    """
    from shared.db import get_db
    sql = ("SELECT id, platform FROM post_analysis "
           "WHERE created_at >= ? AND created_at < ? AND is_revised=0 "
           "  AND quality_score IS NULL")
    args: list = [start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")]
    if post_type:
        sql += " AND post_type = ?"
        args.append(post_type)
    with get_db() as conn:
        return [(int(r[0]), str(r[1])) for r in conn.execute(sql, args)]


def slot_gaps(now: _dt.datetime | None = None) -> tuple[str, list[str], list[str]] | None:
    """(post_type, 결손 플랫폼, 기대 플랫폼) — 이번 슬롯 기준. 슬롯이 없으면 None."""
    slot = current_slot(now)
    if slot is None:
        return None
    post_type, start, end = slot
    platforms = expected_platforms()
    done = published_in_slot(start, end, post_type)
    return post_type, sorted(set(platforms) - done), platforms


def publishing_in_progress() -> bool:
    """발행이 *아직 돌고 있는가* — 락 파일 기준.

    아직 진행 중인 것을 '결손' 이라 부르면 안 된다. 지연과 실패는 다른 사건이고,
    다르게 알려야 사용자가 다르게 행동한다.
    """
    # ★ read-only 조회만 한다 (2026-08-04 — 감사가 대상을 건드리던 결함).
    #   종전엔 `scheduler._is_locked_externally()` 를 불렀는데, 그 함수는 3시간 넘은 락 파일을
    #   **지운다**(scheduler.py:188-191 `LOCK_FILE.unlink`). 즉 *감사 잡이 도는 것만으로*
    #   살아 있는 발행 락이 삭제될 수 있었다 — 실측 최대 발행 지연 4.1시간 > 3시간.
    #   감시는 대상을 건드리지 않는다. 파일 존재 여부만 본다.
    try:
        import time as _t

        from JARVIS02_WRITER.scheduler import LOCK_FILE as _LF, publish_lock_stale_sec as _stale
        if not _LF.exists():
            return False
        # ★ 신선도까지 본다 (2026-08-04 2차 — 같은 날 오전 수정의 부작용 교정).
        #   오전에 '감사가 락을 지우던' 결함을 read-only 로 고쳤는데, 존재 여부만 보면
        #   **반대 방향으로 샌다**: 비정상 종료로 새어 남은 락(os._exit(75)·keeper SIGKILL)이
        #   영원히 '발행 진행 중' 으로 읽혀 그 슬롯 결손을 **영구히 놓친다**.
        #   스테일 청소는 *다음 발행* 의 `_lock_acquire` 때만 도므로 감사는 스스로 판정해야 한다.
        #   상한은 scheduler 가 소유한 값에서 가져온다 — 사본을 두면 한쪽만 바뀐다(원칙①).
        return (_t.time() - _LF.stat().st_mtime) < _stale()
    except Exception:
        return False


# ── 오류 타입 파생 (CLAUDE.md 오류 세분화 규정 — ERRORS [547]) ─────────────
def publish_gap_error_type(post_type: str, platform: str) -> str:
    """결손 1건의 오류 타입 — *이미 있는 판단*(글종류·플랫폼)에서 기계적으로 만든다.

    중앙 매핑표를 두지 않는다. 새 글종류·새 플랫폼이 생기면 타입이 자동으로 따라온다.
    예: ('economic','tistory') → 'PublishGapEconomicTistory'
    """
    return "PublishGap" + post_type.capitalize() + platform.capitalize()


def publish_job_id(post_type: str) -> str:
    """이 글종류의 발행 잡 ID — `DEFAULT_JOBS` 에서 파생 (리터럴 금지).

    ★ 종전엔 `recovery_hint()` 안에 인라인이었는데 잡 이력 보정도 같은 값이 필요해졌다.
      두 번째 소비자가 생기는 순간이 함수로 꺼낼 때다(①).
    """
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback, publish_post_type
        for j in DEFAULT_JOBS:
            if is_publish_callback(j.get("callback")) and publish_post_type(j.get("callback")) == post_type:
                return str(j.get("id") or "")
    except Exception:
        pass
    return ""


def recovery_hint(post_type: str) -> list[str]:
    """결손 1건을 사람이 *지금 손으로* 되살리는 데 필요한 최소 정보.

    ★ 왜 알림에 이걸 넣는가 (2026-08-04 감사 5위)
      종전 경보는 "무엇이 실패했는가" 만 말했다. 받은 사람은 그 다음에 무엇을 해야
      하는지 몰라서 **터미널을 열고 잡 이름부터 찾아야** 했다. 새벽 2시에 그건
      경보가 아니라 숙제다. 경보는 *다음 한 걸음* 까지 말해야 한다.

    ★ 세 값 전부 파생 — 리터럴 0 (② 동적 설계)
      · 잡 ID   ← `DEFAULT_JOBS` 에서 이 글종류의 발행 잡을 찾아서
      · 로그    ← **지금 이 프로세스가 실제로 쓰고 있는** 파일 핸들러 경로
                  (경로를 박으면 핸들러가 바뀔 때 알림만 옛 경로를 가리킨다)
      · 복구 도구 ← JARVIS04 가 등록한 잡 실행 도구 이름 (승인 버튼 필요)
    """
    out: list[str] = []

    job_id = publish_job_id(post_type)
    if job_id:
        out.append(f"잡 ID: `{job_id}`")

    try:
        import logging as _lg
        paths = [h.baseFilename for h in _lg.getLogger().handlers
                 if getattr(h, "baseFilename", None)]
        if paths:
            root = str(Path(__file__).resolve().parent.parent)
            shown = [pp[len(root) + 1:] if pp.startswith(root) else pp for pp in paths]
            out.append("로그: " + " · ".join(f"`{x}`" for x in shown))
    except Exception:
        pass

    # ★ 재발행을 권하지 않는다 — 복구 정책 A (사용자 결정 2026-08-05).
    #
    #   종전엔 «{job_id} 지금 실행» 을 권했다. 그런데 발행 시각은 **07:00 과 21:00
    #   두 번뿐** 이고(사용자 박제), 잡을 지금 돌리면 그 규칙을 어긴 시각에 글이 나간다.
    #   놓친 슬롯은 **손실로 둔다.** 경보의 목적은 되살리기가 아니라
    #   *무엇을 왜 잃었는지 알게 하는 것* 이다.
    #
    #   그래서 안내도 '실행' 이 아니라 '조사' 를 향한다. 다음 정규 시각은 파생한다.
    nxt = ""
    try:
        _c = current_slot()
        if _c:
            nxt = f"{_c[2]:%m/%d %H:%M}"
    except Exception:
        pass
    out.append("↳ *재발행하지 않습니다* (복구 정책 A — 발행은 정규 시각에만)")
    if nxt:
        out.append(f"다음 정규 발행: {nxt}")
    out.append("원인 조사: `docs/RUNBOOK.md` §5 (잡은 돌았는데 글이 안 나갔다)")
    return out


# ── 결손 원장 — 박제·중복억제 단일 진입점 (2026-08-05) ────────────────────
def gap_already_recorded(key: str) -> bool:
    """이 슬롯 결손이 이미 원장에 있는가 — 중복 보고·중복 학습 차단.

    ★ 왜 필요한가: 같은 슬롯을 두 경로가 발견할 수 있다.
      ① 감사 잡(발행 +N분)  ② 공백 회계(데몬이 복귀할 때)
      둘 다 보고하면 원장에 같은 사건이 2건 쌓이고 사용자에게 알림도 2번 간다.
      `slot_key` 가 연도까지 담은 안정 키라 이 판정이 성립한다.
    """
    try:
        from shared.db import get_db
        with get_db() as con:
            return con.execute(
                "SELECT 1 FROM error_log WHERE error_type LIKE 'PublishGap%' "
                "AND context LIKE ? LIMIT 1", (f'%{key}%',)).fetchone() is not None
    except Exception:
        return False   # 조회 실패 시 보고를 막지 않는다(누락보다 중복이 낫다)


def record_publish_gap(post_type: str, platform: str,
                       start: _dt.datetime, end: _dt.datetime,
                       *, reason: str = "audit") -> bool:
    """결손 1건 박제 — **유일한 진입점**. 이미 기록됐으면 False(아무 것도 안 함).

    Args:
        reason: 'audit'(감사 잡이 발견) | 'daemon_down'(데몬이 꺼져 있어 통째로 잃음)

    ★ `kind` 를 context 에 넣는 이유: GUARDIAN 이 이걸 읽어 *코드로 못 고치는 사건* 으로
      분류한다(`severity.is_transient`). 안 넣으면 절전 한 번마다 Tier-2 LLM 세션이 열린다.
      ★ `severity=` 인자는 `report()` 에 **없다** — 심각도는 severity 모듈이 단독 결정한다.
    """
    key = slot_key(post_type, start)
    if gap_already_recorded(key):
        return False
    label = f"{start:%Y-%m-%d %H:%M} ~ {end:%m-%d %H:%M}"
    why = ("데몬이 꺼져 있어 슬롯을 통째로 잃음" if reason == "daemon_down"
           else "잡은 성공으로 기록됐지만 글이 나가지 않음")
    try:
        from JARVIS07_GUARDIAN.error_collector import report
        report(
            publish_gap_error_type(post_type, platform),
            "publish",
            message=f"{post_type} 글이 {platform} 에 발행되지 않았다 ({why}, 슬롯 {label})",
            module=__name__,
            func_name="record_publish_gap",
            context={"post_type": post_type, "platform": platform,
                     "slot": label, "slot_key": key, "reason": reason,
                     # 코드 결함이 아님 → Tier-2 자동수리 대상에서 제외
                     "kind": "daemon_down" if reason == "daemon_down" else "publish_gap"},
        )
        return True
    except Exception as e:
        print(f"  ⚠️ 결손 박제 실패({post_type}/{platform}): {e}")
        return False


def missed_slots(start: _dt.datetime, end: _dt.datetime) -> list:
    """[start,end) 안 각 슬롯의 결손 — 기대 플랫폼 − 실제 발행 플랫폼.

    ★ '공백에 걸렸다' 는 이유만으로 결손이라 부르지 않는다. 반드시 DB 로 한 번 더
      확인한다 — 공백 직전에 이미 나간 글을 손실로 세면 회계가 거짓이 된다.
    """
    out = []
    expected = expected_platforms()
    for pt, st, en in slots_between(start, end):
        done = published_in_slot(st, en, pt)
        missing = sorted(set(expected) - done)
        if missing:
            out.append({"post_type": pt, "start": st, "end": en,
                        "expected": expected, "missing": missing,
                        "key": slot_key(pt, st)})
    return out


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
        "unscored": [f"#{i}/{pf}" for i, pf in scoring_gaps(start, end, post_type)],
    }

    if result["unscored"] and not in_progress:
        # 채점 결손은 *발행 결손과 별개* — 발행은 성공했으므로 🚨 가 아니라 경고다.
        # 재채점은 analyzer_fb 잡이 한가할 때 자동으로 채운다(여기서 고치지 않는다).
        print(f"  ⚠️ 채점 결손 {len(result['unscored'])}건: {result['unscored']}")
        try:
            from shared.notify import send_tg
            send_tg("⚠️ *채점 결손* — " + " · ".join(result["unscored"])
                    + f"\n보상 신호(ADR 014)가 비었습니다. 재채점 대기열에 자동 편입 — "
                      f"다음 `analyzer_fb` 한가한 회차에 채워집니다.")
        except Exception as e:
            print(f"  ⚠️ 채점 결손 알림 실패: {e}")

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

    # ★ 결손의 *사유* 를 발견 순서가 아니라 **그 시간의 기계 상태**에서 파생한다
    #   (사용자 박제 2026-08-07). 이 시스템은 개인 노트북에서 돈다 — 사용자가 다른
    #   일을 하다 노트북을 끄면 그 회차는 당연히 안 나간다. **그건 결함이 아니라 사실**이다.
    #   종전엔 감사 잡이 무조건 `reason="audit"`(=진짜 실패)로 박아, 전원을 끈 날에도
    #   🚨 가 울리고 GUARDIAN 이 고칠 것 없는 일에 Tier-2 를 열었다. 같은 원인이
    #   *누가 먼저 발견했는가* 로 갈리던 레이스도 이걸로 없어진다.
    #   판정 본체는 생존 신호의 주인(`downtime`) — 여기서 heartbeat 를 직접 읽지 않는다(원칙①).
    #   ★ 창은 **발행이 실제로 일어나야 하는 구간** 으로 좁힌다 (원칙② — 이미 있는
    #     `audit_lag_minutes()`(misfire_grace + 플랫폼수 × 액션 데드라인)에서 파생).
    #     슬롯 창 전체(경제는 07:00~21:00, 14시간)를 보면 **낮에 노트북을 닫은 것만으로
    #     아침의 진짜 실패가 '전원 오프' 로 덮인다** — 진짜 고장을 전원 탓으로 돌리는
    #     이 방향의 오판이 반대(알림 한 번 더)보다 훨씬 나쁘다.
    _reason, _worst = "audit", 0
    try:
        from JARVIS00_INFRA.downtime import downtime_in_window
        from JARVIS04_SCHEDULER.job_registry import misfire_grace_for
        _lag = audit_lag_minutes(misfire_grace_for(publish_job_id(post_type)))
        _pub_end = min(end, start + _dt.timedelta(minutes=_lag))
        _was_down, _worst = downtime_in_window(start, _pub_end)
        if _was_down:
            _reason = "daemon_down"
    except Exception as e:
        print(f"  ⚠️ 정지 구간 판정 실패(진짜 실패로 간주): {e}")
    if _reason == "daemon_down":
        print(f"  💤 슬롯 창에 정지 구간 {_worst // 60}분 — 전원 오프로 기록(결함 아님)")

    # ★ 박제는 단일 진입점으로 (2026-08-05). 공백 회계도 같은 함수를 쓴다.
    #   반환값을 모아 **전부 이미 기록된 것이면 알림도 생략** — 복귀 회계가 먼저 알린
    #   슬롯을 감사 잡이 몇 시간 뒤 다시 🚨 로 알리는 중복을 막는다.
    fresh = [pf for pf in gaps
             if record_publish_gap(post_type, pf, start, end, reason=_reason)]
    result["newly_recorded"] = len(fresh)
    result["reason"] = _reason
    result["downtime_sec"] = _worst

    # ★ 전원 오프는 **조용히** 기록만 하고 끝낸다 — 알림도, 잡 이력 보정도 하지 않는다.
    #   "내가 껐다" 를 실패로 계상하면 완결률·성공률이 기계 사용 습관을 뒤쫓게 되고,
    #   진짜 고장이 그 소음에 묻힌다. `severity` 는 `daemon_down` 을 이미
    #   *코드 결함 아님* 으로 분류하므로 GUARDIAN Tier-2 도 열리지 않는다.
    if _reason == "daemon_down":
        result["job_row"] = "skipped(daemon_down)"
        return result

    # ★ 잡 이력을 진실로 되돌린다 (2026-08-05). 판정은 여기(발행 도메인),
    #   쓰기는 job_history — 서로의 영역을 넘지 않는다.
    #   재시도를 유발하지 않는다: UPDATE 일 뿐 예외를 던지지 않는다(정책 A).
    result["job_row"] = "skipped"
    try:
        from JARVIS04_SCHEDULER.job_history import mark_outcome
        jid = publish_job_id(post_type)
        if jid:
            result["job_row"] = mark_outcome(
                jid, start, end, success=False,
                error=f"발행 결손 {len(gaps)}건: {'·'.join(gaps)} (완결성 감사)")
    except Exception as e:
        print(f"  ⚠️ 잡 이력 보정 실패: {e}")
    if not fresh:
        print(f"  🔁 결손 {len(gaps)}건 — 이미 원장에 있음(중복 알림 생략)")
        return result

    lines = [
        f"🚨 *발행 결손 {len(gaps)}건* — {post_type} ({now:%m/%d %H:%M} 감사)",
        "",
        f"슬롯 {result['slot']}",
        f"기대 {len(platforms)}건 중 *{len(platforms) - len(gaps)}건* 발행 · *{len(gaps)}건 누락*",
        "",
        *[f"  ❌ {post_type} → {pf}" for pf in gaps],
        "",
        "_잡은 성공으로 기록됐지만 글이 나가지 않았습니다._",
        "",
        *recovery_hint(post_type),
    ]
    try:
        from shared.notify import send_tg
        send_tg("\n".join(lines))
    except Exception as e:
        print(f"  ⚠️ 발행 결손 알림 전송 실패: {e}")

    print(f"  🚨 발행 결손 {len(gaps)}건 ({post_type}): {gaps}")
    return result
