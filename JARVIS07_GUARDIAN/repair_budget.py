"""JARVIS07_GUARDIAN/repair_budget.py — 자율 SDK 수리 브레이크 (판정 단일 소유자).

★ 왜 생겼나 (사용자 박제 2026-08-12)
  사건이 접수될 때마다 `auto_repair.run_auto_repair_targeted` 가 Claude Code SDK 세션을
  띄웠는데(20~65턴·300~580초·건당 $2~4) **상한·중복방지·비용가드가 저장소 전역에 0건**이었다.
  실측: 최근 7일 LLM 지출의 약 절반이 이것 — 54회 $81.62. 8/10 하루에만 17회 $32.14.

  더 나쁜 것은 *고칠 수 없는 것* 에 썼다는 점이다. 네이버 로그인이 **캡차** 로 막힌 것은
  사람이 직접 로그인해야 풀리는데, 매 회차 10분씩 코드를 뒤졌다.

★ 왜 여기 한 곳인가 (①단일 진입점)
  호출 경로는 둘인데 한 함수로 모인다 —
    ① incident_responder.respond()      (발행 실패 구동)  ─┐
    ② guardian_agent._orchestrate()     (오류로그 구동)   ─┴→ run_auto_repair_targeted
  ②에만 `MAX_LLM_ATTEMPTS` 상한이 있고 ①은 무방비였다(③원칙 위반). 그래서 가드를
  호출자마다 붙이지 않고 **두 경로가 반드시 지나는 한 곳** 에 건다.
  통로마다 하나씩 막는 방식이 곧 이 사고의 형태였다 — `eb70afc` 가 네이버 로그인 한 통로만
  막았고, 그 커밋 스스로 "같은 백오프 창에서 반복되는 조사 낭비만 제거한다" 고 적었다.

★ 왜 SQLite 장부인가 (프로세스 경계)
  ①경로는 **subprocess** 다. 메모리 카운터(`guardian_agent._cb_count`)·파일 플래그는
  구조적으로 샌다 — CLAUDE.md 「프로세스 경계」 박제, 실례 [474]. 결정을 DB 에 남긴다.
  장부는 "코드가 있다" 가 아니라 **"행이 쌓인다"** 로 효과를 확인하는 근거이기도 하다.

★ ②동적 설계 — 이 파일에 새 숫자는 없다
  상한·창·쿨다운을 전부 기존 상수에서 파생한다:
    architecture.MAX_LLM_ATTEMPTS / ERROR_STATS_WINDOW_DAYS / SDK_REPAIR_DAILY_CALLS
    auto_repair._TARGETED_TIMEOUT · severity.is_transient() · guardian_agent.tier2_blocked_reason()
  금액 임계값은 **코드에 적지 않는다** — 비용은 `llm_token_usage` 에서 파생해 *보고* 만 하고,
  금액 상한이 필요하면 `GUARDIAN_SDK_DAILY_USD` 노브로만 켠다.

공개 API:
  sdk_repair_block_reason(error_record, *, context, job_id, caller) -> str | None
  record_attempt(...) -> int          # 결정 장부 기록 + 로그·TG·status (차단이 조용하지 않게)
  record_outcome(attempt_id, *, fixed, elapsed_sec) -> None
  budget_state() -> dict              # calls_24h·cap·cost_24h_usd·cooldown_left_sec·blocked_24h·by_reason
  status_line() -> str                # 텔레그램 /status 한 줄
  gate_enabled() -> bool
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

log = logging.getLogger("jarvis.guardian.repair_budget")

GATE_ENV = "GUARDIAN_SDK_REPAIR_GATE"
CALLS_ENV = "GUARDIAN_SDK_DAILY_CALLS"
USD_ENV = "GUARDIAN_SDK_DAILY_USD"
COOLDOWN_ENV = "GUARDIAN_SDK_REPAIR_COOLDOWN_SEC"

# 사유 앞머리 — TG 중복 억제 키와 status 매핑이 이 분류에서 파생한다(문자열 파싱 금지).
R_TIER2 = "tier2"        # L1 · 영구 → wontfix
R_HUMAN = "human"        # L2 · 영구 → ignored
R_FINGERPRINT = "fp"     # L3 · 영구 → wontfix
R_COOLDOWN = "cooldown"  # L4 · 일시 → status 손대지 않음
R_BUDGET = "budget"      # L5 · 일시 → status 손대지 않음

_PERMANENT = (R_TIER2, R_HUMAN, R_FINGERPRINT)

# ★ 예산 창 — `SDK_REPAIR_DAILY_CALLS` 의 '일일' 이 뜻하는 구간. 자정 리셋 버스트를 피하려
#   rolling 24h 를 쓴다. SQL 4곳에 `'-1 day'` 를 흩뿌리면 창을 바꿀 때 한 곳을 빠뜨린다(①).
_BUDGET_WINDOW = "-1 day"


def _q_failed() -> bool:
    """직전 장부 조회가 실패했는가 — 화면이 '건강한 0' 으로 거짓말하지 않게 하는 신호."""
    return bool(_LAST_Q_ERROR[0])


_LAST_Q_ERROR: list = [""]


# ── 장부 ──────────────────────────────────────────────────────────────────
def _db():
    from shared.db import get_db
    return get_db()


def _init() -> None:
    """장부 테이블 생성 (`shared/token_usage._init()` 선례 — 자기 테이블은 자기가 만든다)."""
    con = _db()
    con.executescript("""
CREATE TABLE IF NOT EXISTS sdk_repair_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  caller TEXT, job_id TEXT, fingerprint TEXT, error_type TEXT,
  error_id INTEGER DEFAULT -1,
  decision TEXT NOT NULL, reason TEXT, outcome TEXT, elapsed_sec INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_sra_ts ON sdk_repair_attempts(ts);
CREATE INDEX IF NOT EXISTS idx_sra_fp ON sdk_repair_attempts(fingerprint, ts);
""")
    con.commit()


_INITED: list = [False]


def _q(sql: str, args: tuple = (), *, track: bool = True) -> list:
    """장부 조회. 실패는 삼키되 **기억한다**.

    ★ 왜 기억해야 하나 (2026-08-12 적대적 심사)
      DB 가 아프면 L3~L5 가 전부 통과(fail-open)한다. 그것만으로도 위험한데, 종전엔
      `budget_state()` 가 `{'calls_24h':0,'blocked_24h':0,'cost_24h_usd':0.0}` 이라는
      **건강한 숫자** 를 함께 보고했다 — 브레이크가 사라졌는데 화면이 정상이라고 말한다.
      조용한 무력화가 바로 이 프로젝트가 앓아 온 병이라, 실패 사실을 표면까지 올린다.
    """
    try:
        if not _INITED[0]:                       # C-8: 조회마다 CREATE TABLE 을 돌지 않는다
            _init()
            _INITED[0] = True
        return _db().execute(sql, args).fetchall()
    except sqlite3.Error as e:
        # ★ C-2 — 성공해도 **지우지 않는다.** 종전엔 성공 시 플래그를 비워, 앞 질의 실패가
        #   뒤 질의 성공으로 덮여 `budget_state()` 가 '건강한 0' 을 보고했다(실측 재현).
        #   한 판정 안에서 한 번이라도 실패했으면 그 판정 결과는 통째로 믿을 수 없다.
        #   플래그는 판정 시작 시점(`_reset_ledger_error`)에만 지운다.
        # ★ `track=False` 는 *남의 테이블* 조회(비용 집계 등) — 그 실패는 우리 장부가
        #   무효라는 뜻이 아니다. 우리 표에 대한 실패만 상태로 올린다(오탐 방지).
        if track:
            _LAST_Q_ERROR[0] = str(e)
            if "sdk_repair_attempts" in str(e):   # 우리 스키마 문제일 때만 재초기화
                _INITED[0] = False
        log.warning(f"[RepairBudget] 장부 조회 실패(track={track}): {e}")
        return []


def _reset_ledger_error() -> None:
    """판정·조회 한 묶음의 시작. 여기서만 실패 기억을 지운다(C-2)."""
    _LAST_Q_ERROR[0] = ""


# ── 노브 (전부 *함수 안에서* os.getenv — 무배포 조정·monkeypatch 가 먹어야 한다) ──
def gate_enabled() -> bool:
    try:
        from JARVIS07_GUARDIAN.guardian_agent import _flag
        return bool(_flag(GATE_ENV, True))
    except Exception:
        return (os.getenv(GATE_ENV) or "").strip().lower() not in ("0", "false", "off", "no")


def _daily_cap() -> int:
    from JARVIS07_GUARDIAN import architecture as _a
    raw = (os.getenv(CALLS_ENV) or "").strip()
    try:
        return max(1, int(raw)) if raw else int(_a.SDK_REPAIR_DAILY_CALLS)
    except ValueError:
        return int(_a.SDK_REPAIR_DAILY_CALLS)


def _session_cap_sec() -> int:
    """SDK 세션 하나가 점유할 수 있는 최대 시간 — **세션 길이의 주인** 에서 파생.

    쿨다운 노브(`GUARDIAN_SDK_REPAIR_COOLDOWN_SEC`)와 **분리** 한다. 종전엔 비용 귀속 창이
    쿨다운에서 파생돼, 노브를 낮추면 380초·$3.00 짜리 세션이 $0.00 으로 계상됐다(C-7 재발).
    """
    from JARVIS07_GUARDIAN.auto_repair import _TARGETED_TIMEOUT
    return int(_TARGETED_TIMEOUT)


def _cooldown_sec() -> int:
    raw = (os.getenv(COOLDOWN_ENV) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    from JARVIS07_GUARDIAN.auto_repair import _TARGETED_TIMEOUT
    return int(_TARGETED_TIMEOUT)


# ── 파생 조회 ─────────────────────────────────────────────────────────────
def _allowed_calls_24h() -> int:
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE decision='allowed' "
           "AND ts>=datetime('now','localtime',?)", (_BUDGET_WINDOW,))
    return int(r[0][0]) if r else 0


def _blocked_24h() -> int:
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE decision='blocked' "
           "AND ts>=datetime('now','localtime',?)", (_BUDGET_WINDOW,))
    return int(r[0][0]) if r else 0


def _by_reason_24h() -> dict:
    rows = _q("SELECT substr(reason,1,instr(reason||':',':')-1) k, COUNT(*) "
              "FROM sdk_repair_attempts WHERE decision='blocked' "
              "AND ts>=datetime('now','localtime',?) GROUP BY k", (_BUDGET_WINDOW,))
    return {str(a): int(b) for a, b in rows}


def _unfixed_fp_count(fp: str, days: int) -> int:
    """같은 지문으로 **허용됐는데 못 고친** 횟수. 성공은 세지 않는다 —
    고쳐진 지문이 상한을 갉아먹으면 다음 재발 때 손도 못 댄다."""
    if not fp:
        return 0
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE fingerprint=? AND decision='allowed' "
           "AND (outcome IS NULL OR outcome!='fixed') "
           "AND ts>=datetime('now','localtime',?)", (fp, f"-{int(days)} day"))
    return int(r[0][0]) if r else 0


def _allowed_fp_count(fp: str, days: int) -> int:
    if not fp:
        return 0
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE fingerprint=? AND decision='allowed' "
           "AND ts>=datetime('now','localtime',?)", (fp, f"-{int(days)} day"))
    return int(r[0][0]) if r else 0


def _transient_block_count(fp: str, days: int) -> int:
    """같은 지문이 **'일시적·사람개입'(L2) 사유로 차단된** 횟수."""
    if not fp:
        return 0
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE fingerprint=? AND decision='blocked' "
           "AND reason LIKE ? AND ts>=datetime('now','localtime',?)",
           (fp, f"{R_HUMAN}:%", f"-{int(days)} day"))
    return int(r[0][0]) if r else 0


def _persistent_unknown(fp: str, days: int, limit: int) -> bool:
    """L2 가 '일시적' 이라 했는데 **계속 돌아오는** 지문인가 (= 일시적이 아니다).

    ★ 왜 탈출구가 필요한가 (사용자 판단 2026-08-12)
      L2 는 분류 실패(`unknown`)를 '일시적' 으로 보고 막는다. 내용상 대개 맞다 —
      발행 실패의 다수는 로그인·캡차·네트워크이고, 진짜 코드 버그는 보통 실제 예외
      타입이 붙어 `unknown` 으로 오지 않는다. 재발행 재시도는 계속되므로 복구도 안 늦는다.

      그러나 **항상** 막으면 *아직 타입이 안 붙은 진짜 새 버그* 가 영원히 자동수리를 못 받는다.
      지문도 패턴도 학습 자산도 생기지 않아, 자가 학습 루프가 새 실패 유형에 대해
      **시작조차 못 한다.** 이 손실은 복리로 쌓이고 상한이 없다.
      반대로 잘못 허용하는 손실은 세션 1회($2~4)이고 L3 지문 상한이 곧바로 끊는다.

    ★ 판정 근거 — "일시적인 것은 반복되지 않는다."
      같은 지문이 상한만큼 '일시적' 으로 차단됐는데 **한 번도 허용된 적이 없으면**
      그것은 일시적이 아니라 *조사받지 못한 지속 결함* 이다. 딱 한 번 태운다.
      (허용 이력이 생기는 순간 이 조건은 거짓이 되고, 이후는 L3 가 관장한다 — 무한 탈출 없음.)
    """
    return (_transient_block_count(fp, days) >= limit
            and _allowed_fp_count(fp, days) == 0)


def _sec_since_last_allowed() -> float | None:
    """직전 세션 **종료** 이후 경과 초.

    ★ C-3 (2026-08-12) — 종전엔 `allowed` 행의 ts(= 세션 *시작*) 기준이었다. 그 행은 SDK
      호출 **앞** 에 찍히고 쿨다운 기본값이 세션 상한(600초)과 같아서, 600초를 꽉 채운 세션
      직후 실효 간격이 **0** 이었다. "세션 사이 최소 휴지" 가 성립하지 않았다.
      마감된 세션은 `elapsed_sec` 이 있으므로 종료 시각을 파생한다. 아직 도는 세션
      (outcome IS NULL)은 종료 시각을 모르므로 *지금* 으로 본다 — 도는 중엔 최대한 막는다.
    """
    # ★ 고아 행이 영구 차단을 만들지 않게 (2026-08-13 — C-3 수정이 낳은 결함, 실증)
    #   `outcome IS NULL` 을 무조건 "지금 도는 중" 으로 읽으면, 세션 도중 프로세스가 죽은 행이
    #   **영원히 NULL** 로 남아(`record_outcome` 은 finally — SIGKILL·재부팅엔 안 돈다)
    #   모든 자율 SDK 수리가 영구 차단된다. 실측: 3시간 전 고아 행 하나로 gap=0.19초.
    #   사유가 `cooldown`(비영구)이라 오류는 `new` 로 남아 10분마다 영원히 재시도되고,
    #   풀리려면 새 allowed 행이 필요한데 그 행이 바로 차단당한다 — 자기잠금이다.
    #   → **세션 상한을 넘긴 NULL 은 '도는 중' 일 수 없다.** 그때는 ts+상한을 종료로 본다.
    #     상한은 `_cooldown_sec()` 이 아니라 **세션 길이의 주인**(_TARGETED_TIMEOUT)에서 파생한다
    #     (쿨다운 노브를 낮추면 종료 판정이 함께 흔들리면 안 된다 — C-7 이 그 병이었다).
    _cap = _session_cap_sec()
    r = _q("SELECT (julianday('now','localtime') - julianday(CASE "
           "  WHEN outcome IS NOT NULL "
           "    THEN datetime(ts, '+' || COALESCE(elapsed_sec,0) || ' seconds') "
           "  WHEN julianday('now','localtime') - julianday(ts) > ? "
           "    THEN datetime(ts, '+' || ? || ' seconds') "
           "  ELSE datetime('now','localtime') END"
           "))*86400.0 AS gap "
           "FROM sdk_repair_attempts WHERE decision='allowed' ORDER BY ts DESC LIMIT 1",
           (_cap / 86400.0, int(_cap)))
    if not r or r[0][0] is None:
        return None
    return max(0.0, float(r[0][0]))


def _cost_24h(governed_only: bool = True) -> float:
    """최근 24h SDK 실지출(USD).

    ★ C-7 (2026-08-12) — 기본은 **가드가 다스리는 지출만** 센다.
      `llm_token_usage.source` 는 ① 사건 구동 targeted 수리 ② 주 1회 심층감사
      ③ 사용자 승인 도구를 전부 같은 태그로 적는다. 전량 합계를 임계값에 쓰면
      **가드가 막지도 않는 남의 지출로 자율 수리가 막힌다**(USD 노브를 켠 경우).
      그래서 장부에 `allowed` 로 남은 세션 구간과 겹치는 사용분만 귀속시킨다.
      `governed_only=False` 는 표시용 전량 합계(사용자가 총액을 볼 때).
    """
    from shared.claude_sdk_compat import USAGE_SOURCE      # ★ 태그의 주인에서 파생(D1)
    if not governed_only:
        r = _q("SELECT COALESCE(SUM(cost_usd),0) FROM llm_token_usage WHERE source=? "
               "AND ts>=datetime('now','localtime',?)", (USAGE_SOURCE, _BUDGET_WINDOW),
               track=False)
        return float(r[0][0]) if r else 0.0
    # 자율 수리 세션 = 장부 allowed 행. 그 시작~(시작+세션상한) 창에 기록된 사용분만 귀속.
    r = _q("SELECT COALESCE(SUM(u.cost_usd),0) FROM llm_token_usage u "
           "WHERE u.source=? AND u.ts>=datetime('now','localtime',?) AND EXISTS ("
           "  SELECT 1 FROM sdk_repair_attempts a WHERE a.decision='allowed' "
           "  AND u.ts>=a.ts AND u.ts<=datetime(a.ts, ?))",
           (USAGE_SOURCE, _BUDGET_WINDOW, f"+{_session_cap_sec()} seconds"), track=False)
    return float(r[0][0]) if r else 0.0


# ── 판정 — 이 시스템에서 "자율 SDK 수리를 태울지" 를 정하는 유일한 곳 ────────
def sdk_repair_block_reason(error_record: dict | None, *, context: str = "",
                            job_id: str = "", caller: str = "") -> str | None:
    """자율 SDK 수리를 태우면 안 되는 사유 — 없으면 None(통과).

    사유 문자열은 그대로 `resolution`·텔레그램에 쓰이므로 **사람이 읽을 수 있어야** 한다.
    앞머리(`R_*`)로 분류를 파생하므로 형식은 `"<분류>:<사람이 읽을 사유>"` 를 지킨다.
    레그 순서 = 첫 히트 승. **순서 자체가 정책** (싼 판정·영구 사유 먼저).
    """
    _reset_ledger_error()                # 이 판정에서 장부가 한 번이라도 실패하면 남긴다(C-2)
    if not gate_enabled():
        return None                      # L0 킬스위치 — 종전 동작 복귀
    rec: dict[str, Any] = dict(error_record or {})

    # ★ 아래 레그의 예외는 **debug 가 아니라 warning** 이다 (2026-08-12).
    #   내가 편집 중 남긴 `NameError` 를 `log.debug` 가 삼켜 **게이트가 조용히 통과** 했고,
    #   실측 전까지 아무도 몰랐다. 판정 불가는 fail-open 이지만(L3~L5 는 장부 기반이라 독립)
    #   *조용해서는 안 된다* — 이 저장소가 반복해 온 병이 정확히 그것이다.
    # L1 — Tier-2 금지 사유 (보안파일·critical 등). 판단의 주인은 guardian_agent.
    try:
        from JARVIS07_GUARDIAN.guardian_agent import tier2_blocked_reason
        if (r := tier2_blocked_reason(rec)):
            return f"{R_TIER2}:{r}"
    except Exception as e:
        log.warning("[RepairBudget] L1 판정 불가 → 통과: %s", e, exc_info=True)

    # 지문·창·상한은 L2/L3 가 함께 쓴다 — 한 번만 파생한다(①).
    try:
        from JARVIS07_GUARDIAN import architecture as _a
        from JARVIS07_GUARDIAN.pattern_fixer import fingerprint_of
        fp = fingerprint_of(rec)
        days, limit = int(_a.ERROR_STATS_WINDOW_DAYS), int(_a.MAX_LLM_ATTEMPTS)
    except Exception as e:                       # 지문을 못 만들면 상한도 탈출구도 없다
        # ★ 폴백에 숫자를 적지 않는다(D2) — 적는 순간 그것이 상수의 사본이 되고,
        #   원본을 낮춰도 이 줄만 옛 값으로 남는다. 지문이 없으면 L3·탈출구는 어차피
        #   동작하지 않으므로(fp=="" 가드) 창·상한 값 자체가 무의미하다 → 0 으로 둔다.
        log.warning("[RepairBudget] 지문 파생 실패 → 지문상한·탈출구 무효: %s", e, exc_info=True)
        fp, days, limit = "", 0, 0

    # L2 — 사람 개입 필요·비코드. eb70afc 가 '네이버 로그인' 한 통로에만 한 판정을
    #      여기 한 곳으로 모은다. 어휘 목록이 아니라 severity 의 *판정* 에 위임한다.
    try:
        from JARVIS07_GUARDIAN.severity import (companions_of, is_transient,
                                                kind_of, kinds_in_text)
        # ★ 구조화 kind 가 있으면 그것이 권위다 (2026-08-12 적대적 심사 B2)
        #   종전엔 무조건 `kinds_in_text(context)` 로 텍스트에서 다시 뽑았다. 두 가지가 깨졌다:
        #   ① guardian 경로의 context 는 `key: value` 블록이라 줄머리 앵커가 안 맞아 **항상 []**
        #      → 같은 레코드를 안전장치0(kind_of 사용)은 통과시키는데 게이트는 차단했다.
        #      2026-08-08 에 "kind 선언을 메시지 문구가 뒤집지 못한다"로 고친 결함의 부활이다.
        #   ② incident 경로는 `kinds[0]` **한 개**가 전체를 결정했다 — 로그 앞머리에
        #      login_invalid_backoff 한 줄만 있으면 뒤따르는 명백한 NameError 도 막혔다.
        #   자유 텍스트에서 긁은 '문서 순서상 첫 kind' 에는 생산자가 명시한 구조화 필드의
        #   권위가 없다. 레코드 우선, 텍스트는 레코드 없는 ①경로 폴백으로만 쓴다.
        # ★ `kind`(무엇이 났나)와 `companions`(같이 실린 실이슈 **개수**)는 **다른 축** 이다.
        #   2026-08-12: 처음엔 `list(companions_of(rec) or [])` 로 썼는데 그것은 int 라
        #   `list(2)` 가 TypeError 를 냈고, 아래 except 가 삼켜 **L2 가 통째로 건너뛰어졌다**
        #   (게이트가 조용히 통과). 선례는 guardian_agent:1213 — kind 와 companions 를 따로 넘긴다.
        _k = kind_of(rec) or ""
        _companions = companions_of(rec)          # int | None — 개수 신호, 그대로 전달
        _kinds = [_k] if _k else []
        if not _kinds:                            # 레코드에 구조화 kind 가 없을 때만 텍스트 폴백
            _kinds = kinds_in_text(context or "") or []
            _k = _kinds[0] if _kinds else ""
        # ★ 여러 kind 가 섞이면 **전부** 비코드일 때만 일시적으로 본다 —
        #   선례: incident_responder._classify 의 `all(is_transient(..., kind=k) for k in kinds)`.
        #   하나라도 진짜 코드 오류면 수리 대상이다(과차단 방지).
        _et = rec.get("error_type", "") or ""
        _msg = rec.get("message", "") or ""
        _src = rec.get("source", "") or ""
        _probe = _kinds or [""]        # kind 를 하나도 못 얻었으면 kind 없이 한 번 판정
        if all(is_transient(_et, _msg, _src, kind=k, companions=_companions)
               for k in _probe):
            # ★ 지속성 탈출구 — 계속 돌아오는 '일시적' 은 일시적이 아니다. 딱 한 번 태운다.
            if _persistent_unknown(fp, days, limit):
                log.warning("[RepairBudget] 지속 차단 지문 → 1회 조사 허용: fp=%s (%d회 차단, 허용 0)",
                            fp[:50], _transient_block_count(fp, days))
            else:
                return (f"{R_HUMAN}:코드로 못 고치는 사유(사람 개입·일시적) — "
                        f"{rec.get('error_type','?')}/{(_k or '-')}")
    except Exception as e:
        log.warning("[RepairBudget] L2 판정 불가 → 통과: %s", e, exc_info=True)

    # L3 — 지문 상한. ①경로에 없던 것을 여기서 부여(③원칙 핵심).
    n = _unfixed_fp_count(fp, days)
    if fp and n >= limit:
        return (f"{R_FINGERPRINT}:같은 지문 {n}회 시도(상한 {limit}, "
                f"최근 {days}일) — {fp[:60]}")

    # L4 — 쿨다운. 프로세스 경계를 넘는 유일한 직렬화.
    gap = _sec_since_last_allowed()
    cd = _cooldown_sec()
    if cd > 0 and gap is not None and gap < cd:
        return f"{R_COOLDOWN}:직전 SDK 세션 후 {int(gap)}초 — 최소 간격 {cd}초 미경과"

    # L5 — 일일 예산 (rolling 24h · 자정 리셋 버스트 방지)
    calls, cap = _allowed_calls_24h(), _daily_cap()
    if calls >= cap:
        return (f"{R_BUDGET}:24시간 자율 SDK 수리 {calls}/{cap}회 소진 "
                f"(실지출 ${_cost_24h():.2f})")
    usd = (os.getenv(USD_ENV) or "").strip()
    if usd:
        try:
            if (c := _cost_24h()) >= float(usd):
                return f"{R_BUDGET}:24시간 SDK 지출 ${c:.2f} ≥ ${float(usd):.2f}"
        except ValueError:
            pass
    return None


# ── 기록 — 차단이 *조용하지 않게* 하는 책임을 여기 모은다 ────────────────────
def _reason_class(reason: str) -> str:
    return (reason or "").split(":", 1)[0]


def _notify_once(reason: str, fp: str, caller: str, job_id: str, rec: dict,
                 *, exclude_id: int = -1) -> None:
    """같은 (분류, 지문)은 쿨다운 창에 1회만 — 창 길이도 파생(새 숫자 0).
    판정은 장부 조회라 프로세스 경계를 넘는다.

    ★ `exclude_id` 가 왜 필수인가 (2026-08-12 적대적 심사 B1 — 내가 만든 버그)
      `record_attempt` 는 차단 행을 INSERT·commit 한 **뒤** 이 함수를 부른다. 그런데 억제
      질의의 `max(ts)` 가 **방금 넣은 그 행을 포함** 해 gap≈0 이 되고, 항상 조기 return 했다.
      → 운영 설정(쿨다운 600초)에서 차단 알림이 **100% 안 나갔다**. 레이스가 아니라 결정론이다.
      테스트가 초록이던 이유는 픽스처가 `GUARDIAN_SDK_REPAIR_COOLDOWN_SEC=0` 을 박았기 때문 —
      이 저장소가 커밋 47b2574 로 이미 겪은 '노브 때문에 초록인 테스트' 다.
      그래서 방금 넣은 행을 **명시적으로 배제** 하고, 운영 쿨다운으로 도는 테스트를 따로 둔다.
    """
    cls = _reason_class(reason)
    # ★ C-5 — 억제 키가 (사유, **지문**) 뿐이면 백로그 20건이 한 sweep 에 들어올 때
    #   지문이 다 달라 최대 19통이 나간다. 사람이 받는 것은 '한 번의 사건' 이므로
    #   **사유 분류 단위** 창을 하나 더 건다. 지문 창은 같은 오류의 반복을, 분류 창은
    #   같은 원인의 무더기를 막는다 — 둘 다 창 길이는 쿨다운에서 파생(새 숫자 0).
    prev = _q("SELECT (julianday('now','localtime')-julianday(max(ts)))*86400.0 "
              "FROM sdk_repair_attempts WHERE decision='blocked' "
              "AND reason LIKE ? AND id<>?", (f"{cls}:%", int(exclude_id)))
    gap = prev[0][0] if prev and prev[0][0] is not None else None
    if gap is not None and gap < _cooldown_sec():
        log.info("[RepairBudget] 차단 알림 억제(같은 사유 %s, %.0f초 전) — fp=%s", cls, gap, fp[:40])
        return
    calls, cap = _allowed_calls_24h(), _daily_cap()
    body = (f"🛑 *[GUARDIAN] 자율 SDK 수리 차단*\n"
            f"사유: {reason.split(':', 1)[-1]}\n"
            f"경로: {caller} / job={job_id} / type={rec.get('error_type', '?')}\n"
            f"지문: {fp[:60]}\n"
            f"예산: 24h {calls}/{cap}회 · 실지출 ${_cost_24h():.2f}\n"
            f"→ 해제: {CALLS_ENV} 상향 또는 {GATE_ENV}=0")
    try:
        from shared.notify import send_tg          # ★ auto_repair._send_tg 는 *무음* 이라 쓰지 않는다
        send_tg(body)
    except Exception as e:
        log.warning(f"[RepairBudget] 차단 알림 전송 실패: {e}")


def record_attempt(*, error_record: dict | None, caller: str, job_id: str,
                   decision: str, reason: str = "") -> int:
    """결정을 장부에 남기고, 차단이면 로그·텔레그램·오류 status 까지 함께 처리한다.

    ★ 호출자가 '알림을 잊을' 수 없게 **한 함수 안** 에 모았다 — 조용한 차단은 조용한 낭비만큼 나쁘다.
    """
    rec = dict(error_record or {})
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import fingerprint_of
        fp = fingerprint_of(rec)
    except Exception:
        fp = ""
    aid = -1
    try:
        _init()
        con = _db()
        cur = con.execute(
            "INSERT INTO sdk_repair_attempts(caller,job_id,fingerprint,error_type,error_id,"
            "decision,reason) VALUES(?,?,?,?,?,?,?)",
            (caller, job_id, fp, rec.get("error_type", ""), int(rec.get("id", -1) or -1),
             decision, reason))
        con.commit()
        aid = int(cur.lastrowid or -1)
    except sqlite3.Error as e:
        log.warning(f"[RepairBudget] 장부 기록 실패: {e}")

    if decision != "blocked":
        return aid

    calls, cap = _allowed_calls_24h(), _daily_cap()
    log.warning("[RepairBudget] 차단(%s/%s): %s | fp=%s | 24h %d/%d회 $%.2f",
                caller, job_id, reason, fp[:50], calls, cap, _cost_24h())
    _notify_once(reason, fp, caller, job_id, rec, exclude_id=aid)

    # 오류 status — ②경로(실제 행이 있는 경우) + **영구 사유** 일 때만.
    #   일시적 사유(쿨다운·예산)는 손대지 않는다 — `new` 로 남아야 j07_retry_pending 이 다시 데려온다.
    eid = int(rec.get("id", -1) or -1)
    if eid > 0 and _reason_class(reason) in _PERMANENT:
        try:
            from shared.db import mark_error_status
            status = "ignored" if _reason_class(reason) == R_HUMAN else "wontfix"
            mark_error_status(eid, status, reason)
        except Exception as e:
            log.warning(f"[RepairBudget] status 갱신 실패(#{eid}): {e}")
    return aid


def record_outcome(attempt_id: int, *, fixed: bool, elapsed_sec: float = 0.0) -> None:
    """허용된 세션의 결과를 장부에 마감. 지문 상한이 이 값을 본다(성공은 상한에서 빠진다)."""
    if not attempt_id or attempt_id < 0:
        return
    try:
        _init()
        con = _db()
        con.execute("UPDATE sdk_repair_attempts SET outcome=?, elapsed_sec=? WHERE id=?",
                    ("fixed" if fixed else "nofix", int(elapsed_sec or 0), int(attempt_id)))
        con.commit()
    except sqlite3.Error as e:
        log.warning(f"[RepairBudget] 결과 기록 실패: {e}")


def void_attempt(attempt_id: int) -> None:
    """시작조차 못 한 시도를 장부에서 **무효화** 한다 (예산·지문 상한에서 제외).

    ★ 왜 삭제인가 (2026-08-12 적대적 심사 B3)
      `deferred` 는 LLM 순번을 못 잡아 **세션이 시작도 안 된** 경우다 — 지출 $0·턴 0.
      그런데 `allowed` 행은 SDK 호출 *앞* 에 찍히므로, 그대로 두면 쓰지도 않은 예산과
      지문 상한을 소진한다. 실측: deferred 3회로 일일 3/3 소진 → 4회차 지문 상한 차단.
      deferred 가 나오는 시점이 정확히 발행 창(부하 집중)이라, 사건이 몰릴수록 브레이크가
      *진짜 수리* 를 막는 역효과가 났다.
      `outcome='deferred'` 로 남기지 않고 지우는 이유: 모든 집계 질의(`_allowed_calls_24h`·
      `_allowed_fp_count`·`_sec_since_last_allowed`)가 `decision='allowed'` 만 보므로,
      행을 남기면 질의 **네 곳** 에 예외를 더해야 한다. 예외가 흩어지면 한 곳을 빠뜨린다(①).
    """
    if not attempt_id or attempt_id < 0:
        return
    try:
        _init()
        con = _db()
        con.execute("DELETE FROM sdk_repair_attempts WHERE id=? AND decision='allowed'",
                    (int(attempt_id),))
        con.commit()
        log.info("[RepairBudget] deferred — 시도 무효화(예산 미소모): id=%s", attempt_id)
    except sqlite3.Error as e:
        log.warning(f"[RepairBudget] 시도 무효화 실패: {e}")


def ledger_mark() -> str:
    """지금 시각 표식 — `session_ran_since()` 와 짝. 장부와 같은 시계를 쓴다(localtime)."""
    r = _q("SELECT datetime('now','localtime')")
    return str(r[0][0]) if r else ""


def session_ran_since(mark: str, error_record: dict | None) -> bool:
    """표식 이후 **이 오류에 대해** 자율 SDK 세션이 실제로 허용됐는가.

    ★ 왜 전역 카운터로 판정하면 안 되는가 (2026-08-12 적대적 검증 C-1 — 실증된 회귀)
      종전 `guardian_agent.sdk_session_ran` 은 장부의 **전역 blocked 증분** 으로 판정했다
      (`return not (b1 > b0)`). 내 호출과 무관한 남의 차단 1건이면 곧바로 False 다.
      실측: 세션이 580초 실제로 돌았는데도 타 스레드 쿨다운 차단 1건 때문에 False.
      우연이 아니라 구조적이다 — L4 쿨다운(600초)은 세션이 도는 **바로 그 창** 의 다른 시도를
      전부 차단하며 행을 쓰고, `j07_retry_pending` 은 10분마다 최대 20건을 별도 스레드로 띄운다.
      즉 **관측을 무너뜨리는 것이 그 관측이 지켜보는 쿨다운 레그 자신** 이었다.
      여파: `llm_attempts` 가 영영 안 올라 `MAX_LLM_ATTEMPTS` 가 무력화되고, 10분 세션을 태우고도
      "Tier-2 미실행 — 게이트 대기" 로 기록돼 재큐잉되며, 사용자에게 거짓 알림이 갔다.

    ★ 그래서 **지문(신원)** 으로 묻는다 — "내 것이 돌았나". 다른 오류는 지문이 다르므로
      동시 실행이 판정을 오염시키지 않는다. 창 노화(rolling 24h)도 무관하다 —
      개수가 아니라 *표식 이후의 행 존재* 를 보기 때문이다.
    ★ 판정 불가(표식 없음·조회 실패)면 **True** — 종전 동작(시도로 셈)을 유지하는 보수적
      기본값. 모를 때 상한을 슬그머니 늘리는 쪽으로 틀리지 않는다.
    """
    if not mark:
        return True
    _rec = dict(error_record or {})
    if not (_rec.get("error_type") or _rec.get("message")):
        return True                      # 신원을 만들 재료가 없다 = 판정 불가
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import fingerprint_of
        fp = fingerprint_of(_rec)
    except Exception:
        return True
    if not fp:
        return True
    r = _q("SELECT COUNT(*) FROM sdk_repair_attempts WHERE decision='allowed' "
           "AND fingerprint=? AND ts>=?", (fp, mark))
    if not r:
        return True                      # 조회 실패 = 판정 불가 → 보수적으로 '돌았다'
    return int(r[0][0]) > 0


# ── 관측 ──────────────────────────────────────────────────────────────────
def budget_state() -> dict:
    """현재 예산 상태 — 대시보드·/status·테스트 공용 (표시 계층이 값을 재계산하지 않게)."""
    _reset_ledger_error()                # 이 스냅샷 동안의 실패만 반영한다(C-2)
    gap = _sec_since_last_allowed()
    cd = _cooldown_sec()
    return {
        "calls_24h": _allowed_calls_24h(),
        "cap": _daily_cap(),
        "cost_24h_usd": round(_cost_24h(), 2),
        "cooldown_left_sec": int(max(0, cd - gap)) if (gap is not None and cd > 0) else 0,
        "blocked_24h": _blocked_24h(),
        "by_reason": _by_reason_24h(),
        "gate_enabled": gate_enabled(),
        # ★ 장부를 못 읽었으면 위 숫자는 '0' 이 아니라 '모름' 이다 — 소비자가 구분할 수 있게.
        "ledger_error": _LAST_Q_ERROR[0],
    }


def status_line() -> str:
    s = budget_state()
    tail = ""
    if s["blocked_24h"]:
        detail = "·".join(f"{k} {v}" for k, v in sorted(s["by_reason"].items()))
        tail = f" · 차단 {s['blocked_24h']}건({detail})"
    off = "" if s["gate_enabled"] else " · ⚠️게이트 꺼짐"
    if s.get("ledger_error"):
        return f"🧯 자율 SDK 수리: ⚠️장부 조회 실패 — 상한 무효 상태({s['ledger_error'][:40]}){off}"
    return (f"🧯 자율 SDK 수리: 24h {s['calls_24h']}/{s['cap']}회 · "
            f"${s['cost_24h_usd']:.2f}{tail}{off}")


def budget_effective() -> bool:
    """★ patch_effective 표준 — 가드가 *실제로 무는지* 동작으로 확인.

    가짜 지문을 상한 초과 상태로 만들어 차단이 나오는지 본다. 장부에 흔적을 남기지 않도록
    롤백한다. 예외·통과는 곧 '가드 무력' 이다(코드 존재는 적용의 증거가 아니다).
    """
    if not gate_enabled():
        return True                      # 의도적으로 꺼둔 상태는 '무력' 이 아니다
    try:
        from JARVIS07_GUARDIAN import architecture as _a
        _init()
        con = _db()
        rec = {"error_type": "__BudgetSmoke__", "message": "smoke", "source": "smoke", "id": -1}
        from JARVIS07_GUARDIAN.pattern_fixer import fingerprint_of
        fp = fingerprint_of(rec)
        n = int(_a.MAX_LLM_ATTEMPTS)
        con.executemany(
            "INSERT INTO sdk_repair_attempts(caller,job_id,fingerprint,error_type,decision)"
            " VALUES('smoke','smoke',?,'__BudgetSmoke__','allowed')", [(fp,)] * n)
        con.commit()
        try:
            # ★ C-4 — L3(지문 상한)만 밟히게 한다. 종전엔 L4·L5 도 성공으로 쳤는데,
            #   부팅 직전 600초 안에 실제 세션이 있었으면 L4 가 먼저 물어 **확인하려던
            #   L3 는 한 번도 안 밟힌 채 True** 가 나왔다. 스모크가 무엇을 봤는지 모르면
            #   그 통과는 증거가 아니다. 쿨다운·예산은 이 판정에서 잠시 비활성화한다.
            _prev = (os.environ.get(COOLDOWN_ENV), os.environ.get(CALLS_ENV))
            os.environ[COOLDOWN_ENV] = "0"
            os.environ[CALLS_ENV] = str(10 ** 6)
            try:
                why = sdk_repair_block_reason(rec, context="", job_id="smoke", caller="smoke")
            finally:
                for _k, _v in ((COOLDOWN_ENV, _prev[0]), (CALLS_ENV, _prev[1])):
                    if _v is None:
                        os.environ.pop(_k, None)
                    else:
                        os.environ[_k] = _v
        finally:
            con.execute("DELETE FROM sdk_repair_attempts WHERE caller='smoke'")
            con.commit()
        return bool(why) and _reason_class(why) == R_FINGERPRINT
    except Exception as e:
        log.warning(f"[RepairBudget] budget_effective 예외 → 무력 판정: {e}")
        return False


__all__ = [
    "sdk_repair_block_reason", "record_attempt", "record_outcome",
    "budget_state", "status_line", "gate_enabled", "budget_effective", "void_attempt",
    "ledger_mark", "session_ran_since",
    "GATE_ENV", "CALLS_ENV", "USD_ENV", "COOLDOWN_ENV",
    "R_TIER2", "R_HUMAN", "R_FINGERPRINT", "R_COOLDOWN", "R_BUDGET",
]
