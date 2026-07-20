"""shared/token_usage.py — LLM 토큰 사용량 계측·집계 *단일 진입점*.

배경 (ERRORS [456], 2026-07-20): 시스템이 토큰 사용량에 대해 완전히 눈이 멀어
있었다. `shared/llm.py` 에 집계 코드 0줄, `claude` CLI 에 조회 명령 없음,
Anthropic 이 보내는 `rate_limit_event` 페이로드는 sdk_compat 이 타입명만 찍고
*폐기*. 그 결과 "한도가 언제 얼마나 찼는지" 를 매번 추측해야 했다.

수집 경로 2종 (상호 보완):
  ① **라이브 계기** — SDK `ResultMessage.usage` 를 호출 시점에 DB 박제.
     alias/모델별 *귀속* 이 가능한 유일한 경로. 데몬 내부 호출만 잡힌다.
  ② **트랜스크립트 스캔** — `~/.claude/projects/**/*.jsonl` 집계.
     Claude Code 대화·서브에이전트까지 *전부* 포함하는 유일한 경로.
     단 alias 귀속 불가(누가 썼는지는 모르고 총량만 안다).

두 경로는 겹친다 — 합산하지 말 것. UI 는 ②를 총량, ①을 내역으로 쓴다.

import:
    from shared.token_usage import record_call, record_rate_limit, summary
"""
from __future__ import annotations

import json
import logging
import os
import glob
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.token_usage")

# 로컬 타임존 — 시스템에서 도출 (하드코딩 금지). 실패 시에만 KST 로 폴백.
try:
    KST = datetime.now().astimezone().tzinfo or timezone(timedelta(hours=9))
except Exception:
    KST = timezone(timedelta(hours=9))

# 트랜스크립트 루트 — Claude Code 가 세션 기록을 쓰는 곳
_TRANSCRIPT_ROOT = Path(os.path.expanduser("~/.claude/projects"))

# 스캔 캐시 (파일 1000+ 개라 매 요청 스캔 금지)
_scan_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_scan_lock = threading.Lock()
_SCAN_TTL_SEC = float(os.getenv("TOKEN_SCAN_TTL_SEC", "120") or "120")


# ── DB 박제 ────────────────────────────────────────────────────────────

def _init() -> None:
    """토큰 테이블 초기화 (idempotent)."""
    from shared.db import get_db
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_token_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ts              TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                source          TEXT NOT NULL DEFAULT 'daemon',
                alias           TEXT,
                model           TEXT,
                input_tokens    INTEGER DEFAULT 0,
                cache_create    INTEGER DEFAULT 0,
                cache_read      INTEGER DEFAULT 0,
                output_tokens   INTEGER DEFAULT 0,
                cost_usd        REAL    DEFAULT 0,
                duration_ms     INTEGER DEFAULT 0,
                num_turns       INTEGER DEFAULT 0,
                ok              INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_ltu_ts    ON llm_token_usage(ts);
            CREATE INDEX IF NOT EXISTS idx_ltu_alias ON llm_token_usage(alias);

            -- 전체 이력 일별 집계 캐시 — 지난 날짜는 불변이므로 재스캔 불필요.
            -- (전체 스캔 8900+ 파일 ≈ 5초 → 증분 스캔으로 상시 <1초)
            CREATE TABLE IF NOT EXISTS llm_usage_daily (
                date          TEXT PRIMARY KEY,
                output        INTEGER DEFAULT 0,
                input         INTEGER DEFAULT 0,
                cache_create  INTEGER DEFAULT 0,
                cache_read    INTEGER DEFAULT 0,
                calls         INTEGER DEFAULT 0,
                updated_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS llm_rate_limit_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                source      TEXT,
                payload     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_lrl_ts ON llm_rate_limit_events(ts);
        """)


def record_call(alias: str, model: str, usage: dict | None,
                cost_usd: float = 0.0, duration_ms: int = 0,
                num_turns: int = 0, ok: bool = True,
                source: str = "daemon") -> None:
    """LLM 호출 1건의 사용량 박제.

    ★ 절대 예외를 올리지 않는다 — 계측 실패가 LLM 호출을 깨면 안 된다.
    """
    try:
        u = usage or {}
        _init()
        from shared.db import get_db
        with get_db() as conn:
            conn.execute(
                "INSERT INTO llm_token_usage "
                "(source,alias,model,input_tokens,cache_create,cache_read,"
                " output_tokens,cost_usd,duration_ms,num_turns,ok) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (source, alias or "", model or "",
                 int(u.get("input_tokens") or 0),
                 int(u.get("cache_creation_input_tokens") or 0),
                 int(u.get("cache_read_input_tokens") or 0),
                 int(u.get("output_tokens") or 0),
                 float(cost_usd or 0), int(duration_ms or 0),
                 int(num_turns or 0), 1 if ok else 0),
            )
    except Exception as e:      # noqa: BLE001 — 계측은 절대 본류를 막지 않는다
        log.debug(f"[token_usage] record_call 실패(무시): {e}")


def record_rate_limit(payload: dict | None, source: str = "daemon") -> None:
    """Anthropic `rate_limit_event` 페이로드 박제.

    종전엔 sdk_compat 이 타입명만 로깅하고 버렸다 — 한도·리셋 정보가 여기 들어온다.
    스키마를 모르므로 *원문 JSON 통째로* 보존한다.
    """
    try:
        _init()
        from shared.db import get_db
        body = json.dumps(payload or {}, ensure_ascii=False)[:8000]
        with get_db() as conn:
            conn.execute(
                "INSERT INTO llm_rate_limit_events (source,payload) VALUES (?,?)",
                (source, body),
            )
        log.warning(f"[token_usage] ⚠️ rate_limit_event 수신: {body[:400]}")
    except Exception as e:      # noqa: BLE001
        log.debug(f"[token_usage] record_rate_limit 실패(무시): {e}")


# ── 트랜스크립트 스캔 (총량 — 대화·서브에이전트 포함) ──────────────────

def _scan_transcripts(days: int = 8) -> dict:
    """`~/.claude/projects/**/*.jsonl` 에서 사용량 집계.

    라이브 계기가 못 잡는 Claude Code 대화·서브에이전트까지 포함한 *총량*.
    파일 수천 개라 mtime 으로 1차 컷 후 usage 라인만 파싱.
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff = cutoff_dt.timestamp()
    daily: dict[str, dict] = {}
    hourly: dict[str, int] = {}
    by_project: dict[str, dict] = {}
    scanned = 0
    today = datetime.now(KST).strftime("%Y-%m-%d")

    if not _TRANSCRIPT_ROOT.exists():
        return {"available": False, "reason": "트랜스크립트 디렉터리 없음"}

    for f in glob.glob(str(_TRANSCRIPT_ROOT / "*" / "*.jsonl")):
        try:
            if os.path.getmtime(f) < cutoff:
                continue
        except OSError:
            continue
        scanned += 1
        proj = os.path.basename(os.path.dirname(f))
        try:
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                ts = d.get("timestamp")
                if not u or not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(KST)
                except Exception:
                    continue
                day = t.strftime("%Y-%m-%d")
                o  = int(u.get("output_tokens") or 0)
                i  = int(u.get("input_tokens") or 0)
                cc = int(u.get("cache_creation_input_tokens") or 0)
                cr = int(u.get("cache_read_input_tokens") or 0)
                a = daily.setdefault(day, {"output": 0, "input": 0, "cache_create": 0,
                                           "cache_read": 0, "calls": 0})
                a["output"] += o; a["input"] += i
                a["cache_create"] += cc; a["cache_read"] += cr; a["calls"] += 1
                if day == today:
                    hk = f"{t.hour:02d}"
                    hourly[hk] = hourly.get(hk, 0) + o
                    p = by_project.setdefault(proj, {"output": 0, "calls": 0})
                    p["output"] += o; p["calls"] += 1

    return {
        "available": True,
        "scanned_files": scanned,
        "daily": [{"date": k, **v} for k, v in sorted(daily.items())],
        "hourly_today": [{"hour": k, "output": hourly[k]} for k in sorted(hourly)],
        "by_project_today": [
            {"project": k, **v}
            for k, v in sorted(by_project.items(), key=lambda x: -x[1]["output"])
        ][:10],
    }


def history() -> list[dict]:
    """전체 이력 일별 집계 (에이전트 사용 시작일 ~ 오늘).

    DB 캐시 + *증분 스캔*: 지난 날짜는 변하지 않으므로 이미 집계된 날은 재스캔하지
    않는다. 최근 2일치 파일만 다시 훑어 갱신 → 상시 1초 미만.
    """
    try:
        _init()
        from shared.db import get_db
        import sqlite3 as _sq
        with get_db() as conn:
            conn.row_factory = _sq.Row
            rows = {r["date"]: dict(r) for r in
                    conn.execute("SELECT * FROM llm_usage_daily").fetchall()}

        # 재스캔 기준일 — 캐시된 마지막 날의 2일 전부터 (경계·지연 기록 대비)
        if rows:
            last = max(rows)
            try:
                base = datetime.strptime(last, "%Y-%m-%d").replace(tzinfo=KST) - timedelta(days=2)
            except Exception:
                base = datetime.now(KST) - timedelta(days=3650)
        else:
            base = datetime.now(KST) - timedelta(days=3650)   # 최초 1회 전체 스캔

        days = max(1, (datetime.now(KST) - base).days + 1)
        fresh = _scan_transcripts(days=days)
        if fresh.get("available"):
            with get_db() as conn:
                for d in fresh.get("daily", []):
                    conn.execute(
                        "INSERT INTO llm_usage_daily (date,output,input,cache_create,cache_read,calls,updated_at) "
                        "VALUES (?,?,?,?,?,?,datetime('now','localtime')) "
                        "ON CONFLICT(date) DO UPDATE SET "
                        "  output=excluded.output, input=excluded.input, "
                        "  cache_create=excluded.cache_create, cache_read=excluded.cache_read, "
                        "  calls=excluded.calls, updated_at=excluded.updated_at",
                        (d["date"], d["output"], d["input"], d["cache_create"],
                         d["cache_read"], d["calls"]),
                    )
                    rows[d["date"]] = d

        return [
            {"date": k,
             "output": rows[k].get("output", 0), "input": rows[k].get("input", 0),
             "cache_create": rows[k].get("cache_create", 0),
             "cache_read": rows[k].get("cache_read", 0),
             "calls": rows[k].get("calls", 0)}
            for k in sorted(rows)
        ]
    except Exception as e:      # noqa: BLE001
        log.debug(f"[token_usage] history 실패: {e}")
        return []


def _scan_cached(days: int = 8) -> dict:
    import time
    with _scan_lock:
        now = time.time()
        if _scan_cache["data"] is not None and now - _scan_cache["ts"] < _SCAN_TTL_SEC:
            return _scan_cache["data"]
    data = _scan_transcripts(days)      # 락 밖에서 스캔 (동시 요청은 중복 스캔 감수)
    with _scan_lock:
        _scan_cache["ts"] = time.time()
        _scan_cache["data"] = data
    return data


# ── 구독 잔여량 조회 (Anthropic /api/oauth/usage) ──────────────────────
#
# ★ 사용자 승인 2026-07-20: 잔여 토큰을 대시보드에 표시하기 위해 본인 머신의 본인
#   OAuth 토큰으로 본인 사용량을 조회한다.
#
# 주의 3가지 (관리자 인지 필요):
#   ① `/api/oauth/usage` 는 *비공개 내부 엔드포인트* — Anthropic 이 바꾸면 조용히 깨진다.
#      그래서 실패는 전부 흡수하고 None 을 반환, UI 는 기존 표시로 폴백한다.
#   ② 데몬은 launchd 아래에서 돌아 Keychain 접근이 거부될 수 있다(세션 분리).
#      거부 시에도 예외를 올리지 않는다.
#   ③ 토큰은 *메모리에서만* 다루고 로깅·DB 박제·반환값 포함 일체 금지.

_quota_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_QUOTA_TTL_SEC = float(os.getenv("TOKEN_QUOTA_TTL_SEC", "300") or "300")
_QUOTA_ENABLED = (os.getenv("TOKEN_QUOTA_LOOKUP", "1") or "1") != "0"


def _read_oauth_token() -> str | None:
    """macOS Keychain 에서 Claude Code OAuth 액세스 토큰 조회.

    반환값은 *절대 로깅하지 않는다*. 실패 시 None.
    """
    import subprocess, re as _re
    try:
        dump = subprocess.run(["security", "dump-keychain"],
                              capture_output=True, text=True, timeout=20)
        names = sorted(set(_re.findall(
            r'"svce"<blob>="(Claude Code-credentials[^"]*)"', dump.stdout or "")))
    except Exception:
        names = []
    names = names or ["Claude Code-credentials"]
    for n in names:
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", n, "-w"],
                               capture_output=True, text=True, timeout=20)
            if r.returncode != 0 or not (r.stdout or "").strip():
                continue
            d = json.loads(r.stdout)
            o = d.get("claudeAiOauth") or d
            t = o.get("accessToken") or o.get("access_token")
            if t:
                return t
        except Exception:
            continue
    return None


def quota() -> dict | None:
    """구독 잔여량·리셋 시각. 조회 불가 시 None (UI 는 폴백).

    응답 스키마가 비공개라 *원문을 그대로* raw 에 담아 반환한다 —
    구조가 바뀌어도 화면에서 확인은 가능하게.
    """
    if not _QUOTA_ENABLED:
        return None
    import time
    with _scan_lock:
        if (_quota_cache["data"] is not None
                and time.time() - _quota_cache["ts"] < _QUOTA_TTL_SEC):
            return _quota_cache["data"]

    result: dict | None = None
    try:
        tok = _read_oauth_token()
        if tok:
            import urllib.request
            req = urllib.request.Request(
                "https://api.anthropic.com/api/oauth/usage",
                headers={"Authorization": f"Bearer {tok}",
                         "anthropic-beta": "oauth-2025-04-20",
                         "User-Agent": "claude-cli/2.0.0 (external)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = json.loads(r.read().decode())
            result = {"available": True, "raw": raw,
                      "fetched_at": datetime.now(KST).isoformat(timespec="seconds")}
            del tok      # 메모리 참조 조기 해제
    except Exception as e:      # noqa: BLE001 — 조회 실패는 기능 저하일 뿐
        log.debug(f"[token_usage] quota 조회 실패(무시): {type(e).__name__}")
        result = None

    with _scan_lock:
        _quota_cache["ts"] = time.time()
        _quota_cache["data"] = result
    return result


# ── 한도 이벤트 사람이 읽는 형태로 변환 ────────────────────────────────
#
# 화면에 원문 JSON 을 그대로 뿌리면 사람이 해석할 수 없다(사용자 지적 2026-07-20).
# 코드값 → 한국어 라벨, epoch → 로컬 시각, 중복(uuid) 제거까지 여기서 처리한다.

_RL_STATUS = {
    "allowed":        ("정상", "제한 없이 통과"),
    "allowed_warning": ("주의", "한도 임박 경고"),
    "rejected":       ("차단", "한도 초과로 거부됨"),
    "blocked":        ("차단", "한도 초과로 거부됨"),
}
_RL_WINDOW = {
    "five_hour":     "5시간 창",
    "seven_day":     "7일 창",
    "weekly_all":    "7일 창",
    "session":       "5시간 창",
    "weekly_scoped": "모델별 주간",
}
_RL_OVERAGE_REASON = {
    "org_level_disabled": "조직 정책으로 비활성",
    "user_disabled":      "사용자가 비활성",
    "not_eligible":       "대상 아님",
}


def _humanize_rate_limits(rows: list[dict]) -> list[dict]:
    """rate_limit_event 원문 → 화면용 구조. uuid 기준 중복 제거."""
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        try:
            p = json.loads(r.get("payload") or "{}")
        except Exception:
            p = {}
        info = p.get("rate_limit_info") or {}
        uid = p.get("uuid")
        if uid and uid in seen:
            continue        # llm·sdk_compat 양쪽이 같은 이벤트를 기록하던 잔존분 흡수
        if uid:
            seen.add(uid)

        status = str(info.get("status") or "")
        label, desc = _RL_STATUS.get(status, (status or "알 수 없음", ""))
        window = _RL_WINDOW.get(str(info.get("rateLimitType") or ""),
                                info.get("rateLimitType") or "—")

        resets = info.get("resetsAt")
        reset_txt = None
        if isinstance(resets, (int, float)) and resets > 0:
            try:
                reset_txt = datetime.fromtimestamp(resets, KST).strftime("%m/%d %H:%M")
            except Exception:
                reset_txt = None

        ov_status = str(info.get("overageStatus") or "")
        if info.get("isUsingOverage"):
            overage = "초과분 사용 중"
        elif ov_status in ("rejected", "disabled"):
            reason = _RL_OVERAGE_REASON.get(
                str(info.get("overageDisabledReason") or ""), None)
            overage = f"불가 ({reason})" if reason else "불가"
        elif ov_status:
            overage = ov_status
        else:
            overage = None

        out.append({
            "ts": r.get("ts"),
            "status": label,                 # 정상 / 주의 / 차단
            "status_desc": desc,
            "ok": status in ("allowed", "allowed_warning"),
            "window": window,                # 5시간 창 / 7일 창 …
            "reset": reset_txt,              # 07/20 17:00
            "overage": overage,              # 초과사용 가능 여부
            "raw": r.get("payload"),         # 접어둔 원문 (디버깅용)
        })
    return out[:20]


# ── 집계 API ───────────────────────────────────────────────────────────

def summary(days: int = 8) -> dict:
    """대시보드용 종합 현황.

    반환 키:
      totals        — 트랜스크립트 기준 총량 (오늘·7일·시간대별·프로젝트별)
      by_alias      — 라이브 계기 기준 alias(용도)별 내역 — 오늘
      recent_calls  — 최근 호출 50건
      rate_limits   — rate_limit_event 이력 + 최근 페이로드
      health        — 최근 1시간 빈 응답률(스로틀 지표)
    """
    out: dict[str, Any] = {"generated_at": datetime.now(KST).isoformat(timespec="seconds")}

    # ① 트랜스크립트 총량
    try:
        out["totals"] = _scan_cached(days)
    except Exception as e:      # noqa: BLE001
        out["totals"] = {"available": False, "reason": str(e)[:200]}

    # ①-b 전체 이력 (선 차트용) — DB 캐시 + 증분 스캔
    try:
        out["history"] = history()
    except Exception:           # noqa: BLE001
        out["history"] = []

    # ①-d 구독 잔여량 (조회 가능할 때만 — 실패 시 None, UI 폴백)
    try:
        out["quota"] = quota()
    except Exception:           # noqa: BLE001
        out["quota"] = None

    # ①-c 절감·한도 회피 제안 (관리자 판단용)
    try:
        out["suggestions"] = suggestions()
    except Exception:           # noqa: BLE001
        out["suggestions"] = []

    # ② 라이브 계기 내역
    try:
        _init()
        from shared.db import get_db
        today = datetime.now(KST).strftime("%Y-%m-%d")
        with get_db() as conn:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT alias, model, COUNT(*) calls, "
                "  SUM(output_tokens) output, SUM(input_tokens) input, "
                "  SUM(cache_create) cache_create, SUM(cache_read) cache_read, "
                "  SUM(cost_usd) cost, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failed "
                "FROM llm_token_usage WHERE substr(ts,1,10)=? "
                "GROUP BY alias, model ORDER BY output DESC",
                (today,),
            ).fetchall()
            out["by_alias"] = [dict(r) for r in rows]

            rec = conn.execute(
                "SELECT ts, alias, model, output_tokens, input_tokens, "
                "  cache_read, duration_ms, num_turns, ok "
                "FROM llm_token_usage ORDER BY id DESC LIMIT 50"
            ).fetchall()
            out["recent_calls"] = [dict(r) for r in rec]

            rl = conn.execute(
                "SELECT ts, source, payload FROM llm_rate_limit_events "
                "ORDER BY id DESC LIMIT 60"
            ).fetchall()
            out["rate_limits"] = _humanize_rate_limits([dict(r) for r in rl])

            # 최근 1시간 빈 응답률 = 스로틀 체감 지표
            h = conn.execute(
                "SELECT COUNT(*) n, SUM(CASE WHEN output_tokens=0 THEN 1 ELSE 0 END) empty "
                "FROM llm_token_usage WHERE ts >= datetime('now','localtime','-1 hour')"
            ).fetchone()
            n = (h["n"] if h else 0) or 0
            empty = (h["empty"] if h else 0) or 0
            out["health"] = {
                "calls_1h": n,
                "empty_1h": empty,
                "empty_rate": round(empty / n, 3) if n else None,
                "state": ("정상" if n and empty / n < 0.2
                          else "스로틀 의심" if n and empty / n < 0.6
                          else "스로틀/한도" if n else "호출 없음"),
            }
    except Exception as e:      # noqa: BLE001
        out["by_alias"] = []
        out["recent_calls"] = []
        out["rate_limits"] = []
        out["health"] = {"state": "집계 실패", "reason": str(e)[:200]}

    return out


# ── 제안 엔진 ──────────────────────────────────────────────────────────
#
# 관리자가 *판단* 할 수 있도록 — 근거(실측), 조치(구체적 노브), 예상효과를 함께 낸다.
# 원칙: ① 추정은 추정이라고 명시 ② 조치는 파일·환경변수까지 특정 ③ 품질을 깎는
# 제안은 트레이드오프를 반드시 병기.

def _fmt(n: float) -> str:
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


def _live_config() -> dict:
    """제안에 인용할 설정값을 *런타임에서* 읽는다.

    ★ 하드코딩 금지 (2026-07-20 자체감사): 초기 구현은 "재시도 3회"·"잡 42개" 같은
      값을 문자열로 박아 관리자가 노브를 바꿔도 현황판이 옛 값을 말하는 *문서 드리프트*
      를 그대로 재현했다. 모든 인용 수치는 여기서 실시간 조회한다.
    """
    cfg: dict[str, Any] = {}
    try:
        import shared.llm as _L
        cfg["circuit_threshold"] = _L._CIRCUIT_THRESHOLD
        cfg["circuit_cooldown"]  = _L._CIRCUIT_COOLDOWN_SEC
        cfg["circuit_exempt"]    = sorted(_L._CIRCUIT_EXEMPT_ALIASES)
        cfg["bg_aliases"]        = sorted(_L._BG_ALIASES)
        cfg["max_concurrency"]   = _L._LLM_MAX_CONCURRENCY
        cfg["alias_count"]       = len(_L.MODELS)
        import inspect as _insp, re as _re
        m = _re.search(r"retries\s*=\s*max\(\s*1\s*,\s*min\(\s*(\d+)",
                       _insp.getsource(_L.invoke_text))
        cfg["llm_retry_cap"] = int(m.group(1)) if m else None
    except Exception:
        pass
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        cfg["job_total"] = len(DEFAULT_JOBS)
        iv = [j for j in DEFAULT_JOBS if j.get("trigger") == "interval"]
        cfg["job_interval"] = len(iv)
        cfg["job_interval_names"] = [j.get("name") or j.get("id") for j in iv][:6]
    except Exception:
        pass
    try:
        import inspect as _insp2, re as _re2
        import JARVIS00_INFRA.harness as _H
        m = _re2.search(r"max_attempts[^=]*=\s*(\d+)", _insp2.getsource(_H))
        cfg["harness_max_attempts"] = int(m.group(1)) if m else None
    except Exception:
        pass
    return cfg


def suggestions() -> list[dict]:
    """토큰 절감·한도 회피 제안 — 실측 데이터 + *실시간 설정값* 기반.

    반환 항목: {id, title, severity, finding, action, effect, tradeoff, knob}
    severity: high(즉시 검토) / medium(권장) / low(선택) / good(양호 — 유지)
    """
    out: list[dict] = []
    cfg = _live_config()
    try:
        hist = history()
    except Exception:
        hist = []

    # ── 1. 사용 추세·한도 소진 속도 ────────────────────────────────
    recent = [d for d in hist[-7:]] if hist else []
    if recent:
        wk_out = sum(d["output"] for d in recent)
        avg = wk_out / len(recent)
        peak = max(recent, key=lambda d: d["output"])
        out.append({
            "id": "trend",
            "title": "주간 소비 속도 — 한도 소진의 주원인",
            "severity": "high" if avg >= 3_000_000 else "medium" if avg >= 1_000_000 else "good",
            "finding": (f"최근 {len(recent)}일 출력 {_fmt(wk_out)} 토큰, 일평균 {_fmt(avg)}. "
                        f"최대일 {peak['date']} {_fmt(peak['output'])}."),
            "action": ("일평균이 한도의 1/7 을 넘으면 주간 한도를 며칠 만에 소진한다. "
                       "아래 항목들로 상시 소비를 먼저 줄이고, 그래도 부족하면 발행 빈도"
                       "(하루 2회 → 1회)를 조정하는 것이 가장 확실한 절감이다."),
            "effect": "발행 1회 감축 시 일 소비의 상당 부분 절감 (파이프라인이 최대 소비원)",
            "tradeoff": "발행량 감소 = 콘텐츠 산출 감소. 품질에는 영향 없음.",
            "knob": "JARVIS04_SCHEDULER/job_registry.py — j01_economic_post / 테마 발행 cron",
        })

    # ── 2. 재시도 증폭 ─────────────────────────────────────────────
    rcap = cfg.get("llm_retry_cap")
    hmax = cfg.get("harness_max_attempts")
    amp = (rcap * hmax) if (rcap and hmax) else None
    cth = cfg.get("circuit_threshold")
    out.append({
        "id": "retry_amp",
        "title": (f"재시도 증폭 — 실패 1건이 최대 {amp}배로 불어난다" if amp
                  else "재시도 증폭 — LLM 재시도 × harness 순환"),
        "severity": "high" if (amp or 0) >= 6 else "medium",
        "finding": (f"현재 설정: LLM 재시도 상한 {rcap}회 × harness 검증 순환 "
                    f"max_attempts={hmax} → 발행 1스텝이 최악 {amp}회 호출. "
                    "스로틀로 빈 응답이 날 때도 재시도가 그대로 돌아 "
                    "*한도가 없을 때 한도를 더 태운다*."
                    if amp else
                    "LLM 재시도와 harness 검증 순환이 곱해져 호출이 증폭된다 (설정값 조회 실패)."),
        "action": (f"① 스로틀(num_turns=0) 실패는 재시도 대신 *즉시 defer* 로 분리 "
                   f"(`_LAST_CALL.throttled` 신호가 이미 있어 분기만 추가하면 된다). "
                   f"② 회로 임계값을 현재 {cth} 에서 한 단계 낮춰 더 빨리 차단."),
        "effect": "스로틀 구간의 낭비 호출 감소 (효과 크기는 실측 필요 — 추정치 제시 안 함)",
        "tradeoff": "일시적 스로틀이었다면 발행이 다음 회차로 밀린다. 품질 저하는 없음.",
        "knob": f"LLM_CIRCUIT_THRESHOLD (현재 {cth}) / shared/llm.py invoke_text 재시도 분기",
    })

    # ── 3. 회로차단기 면제 alias ───────────────────────────────────
    ex = cfg.get("circuit_exempt") or []
    keep = [a for a in ex if a in ("writer", "fact_judge")]
    drop = [a for a in ex if a not in ("writer", "fact_judge")]
    if ex:
        out.append({
            "id": "circuit_exempt",
            "title": f"회로차단기 면제 alias {len(ex)}종이 한도 소진 시에도 계속 호출",
            "severity": "medium" if drop else "good",
            "finding": (f"현재 면제 목록: {', '.join(ex)} — 회로가 열려도 실시도한다. "
                        f"발행 품질을 지키려는 설계지만 *이미 한도가 찬 상태* 에서는 "
                        f"성공 가능성이 낮은 호출을 반복하게 된다. "
                        f"(회로 임계 {cth}회 연속 스로틀, 쿨다운 {cfg.get('circuit_cooldown')}초)"),
            "action": (f"품질 필수인 {', '.join(keep) or '없음'} 는 유지하고, "
                       f"보조 성격인 {', '.join(drop)} 를 면제 목록에서 빼는 것을 검토."
                       if drop else "현재 면제 목록이 최소 구성이다 — 유지 권장."),
            "effect": "스로틀 구간 호출 수 감소",
            "tradeoff": ("engagement 게이트가 fail-open 되어 매력도 검증이 건너뛰어질 수 있음 "
                         "(PREPUBLISH_ENGAGEMENT_GATE 정책과 함께 판단)." if drop else "없음"),
            "knob": f"LLM_CIRCUIT_EXEMPT (현재 {','.join(ex)})",
        })

    # ── 4. 캐시 효율 (실측) ────────────────────────────────────────
    if hist:
        today = hist[-1]
        cr, cc, o = today["cache_read"], today["cache_create"], today["output"]
        ratio = (cr / (cr + cc)) if (cr + cc) else 0
        out.append({
            "id": "cache",
            "title": "프롬프트 캐시 효율",
            "severity": "good" if ratio >= 0.85 else "medium",
            "finding": (f"오늘 캐시읽기 {_fmt(cr)} / 캐시생성 {_fmt(cc)} → 재사용률 {ratio*100:.0f}%. "
                        f"출력 {_fmt(o)}."),
            "action": ("재사용률이 높으면 캐시는 잘 동작 중 — 건드리지 말 것. "
                       "낮다면 프롬프트 앞부분(헌법·규칙 블록)이 매번 바뀌고 있다는 뜻이므로 "
                       "가변 부분을 프롬프트 *뒤쪽* 으로 몰아야 한다."),
            "effect": "캐시 읽기는 신규 입력 대비 훨씬 저렴 — 재사용률 유지가 최대 절감 수단",
            "tradeoff": "없음 (순수 최적화)",
            "knob": "law_enforcer.build_writing_rules_block() 등 프롬프트 조립 순서",
        })

    # ── 5. 상시 주기 잡 ────────────────────────────────────────────
    jt, ji = cfg.get("job_total"), cfg.get("job_interval")
    if jt:
        names = cfg.get("job_interval_names") or []
        out.append({
            "id": "interval_jobs",
            "title": f"상시 주기 잡 {ji}개 — 유휴 시간에도 계속 돈다",
            "severity": "medium" if (ji or 0) >= 5 else "low",
            "finding": (f"등록 잡 {jt}개 중 interval 트리거 {ji}개가 24시간 반복된다"
                        + (f" (예: {', '.join(str(n) for n in names[:4])})" if names else "")
                        + ". 개별 비용은 작아도 하루 누적 호출 수가 baseline 을 만든다."),
            "action": ("① 그중 *LLM 을 실제 호출하는* 잡만 골라 간격을 늘린다. "
                       "② 발행이 없는 시간대에는 중단하는 조건을 추가한다. "
                       "어떤 잡이 LLM 을 쓰는지는 위 '용도별 내역' 이 쌓이면 정확히 지목 가능."),
            "effect": "상시 baseline 소비 감소 — 발행창에 쓸 여유 확보",
            "tradeoff": "학습 흡수·오류 감지가 느려짐 (실시간성 ↓, 정확도는 동일).",
            "knob": "JARVIS04_SCHEDULER/job_registry.py DEFAULT_JOBS 의 interval",
        })

    # ── 6. 발행창 보호 ─────────────────────────────────────────────
    out.append({
        "id": "publish_window",
        "title": "발행창 우선 예약 — 한도를 발행에 몰아주기",
        "severity": "high",
        "finding": ("오늘 실패의 실제 형태가 이것이다. 새벽 GUARDIAN 심층감사(04:30)와 상시 잡이 "
                    "한도를 쓴 뒤 06:00~07:00 발행창에서 topic_pack 프로필 LLM 이 빈 응답을 받아 "
                    "fail-closed 로 발행이 차단됐다."),
        "action": (f"① 발행 전 일정 시간을 *보호 구간* 으로 지정해 background alias"
                   f"({'·'.join(cfg.get('bg_aliases') or [])})를 아예 차단. "
                   f"이미 `mark_publishing()` + `_BG_ALIASES` 강등 로직이 있으므로 "
                   f"*시간 기반 확장* 만 하면 된다. "
                   f"② GUARDIAN 심층감사 잡을 발행 이후 시간대로 이동."),
        "effect": "발행 성공률 직접 개선 — 한도 부족 시에도 발행이 우선 확보됨",
        "tradeoff": "자가수리·학습이 뒤로 밀림. 발행이 더 중요하다면 명백한 이득.",
        "knob": "shared/llm.py mark_publishing / JARVIS04 j07_deep_audit cron",
    })

    # ── 7. 관측 공백 ───────────────────────────────────────────────
    try:
        _init()
        from shared.db import get_db
        with get_db() as conn:
            n = conn.execute("SELECT COUNT(*) FROM llm_token_usage").fetchone()[0]
            nrl = conn.execute("SELECT COUNT(*) FROM llm_rate_limit_events").fetchone()[0]
    except Exception:
        n = nrl = 0
    out.append({
        "id": "observability",
        "title": "한도 값 자체는 아직 미관측",
        "severity": "low" if nrl else "medium",
        "finding": (f"라이브 계기 {n}건, rate_limit_event {nrl}건 수집됨. "
                    "Anthropic 이 내려주는 한도·리셋 값은 rate_limit_event 페이로드에만 들어있어, "
                    "이벤트가 쌓이기 전에는 '잔여 토큰'을 정확히 알 수 없다."),
        "action": ("이벤트가 쌓이면 이 현황판 '한도 이벤트' 카드에 원문이 표시된다. "
                   "그때 페이로드 구조를 보고 잔여량·리셋시각을 파싱해 KPI 로 승격할 것. "
                   "정확한 현재 한도는 Claude Code 에서 `/usage` 로 확인."),
        "effect": "추측 대신 실측 기반 운영 가능",
        "tradeoff": "없음",
        "knob": "shared/token_usage.record_rate_limit (이미 수집 중)",
    })

    # ── 8. 호환 패치 실효성 (설치 플래그가 아니라 *동작* 으로 확인) ─────
    #   ★ 2026-07-20: 같은 monkey-patch 무력화 사고가 하루에 두 번 났다.
    #     플래그는 내내 True 였고 아무도 몰랐다 → 상시 감시 항목으로 승격.
    probes: list[tuple[str, Any]] = []
    try:
        from shared.claude_sdk_compat import patch_effective
        probes.append(("SDK 메시지 파서(rate_limit_event 흡수)", patch_effective()))
    except Exception:
        probes.append(("SDK 메시지 파서(rate_limit_event 흡수)", None))
    try:
        from shared.pytrends_utils import retry_compat_effective
        probes.append(("pytrends urllib3 호환", retry_compat_effective()))
    except Exception:
        probes.append(("pytrends urllib3 호환", None))

    dead = [n for n, v in probes if v is False]
    unknown = [n for n, v in probes if v is None]
    out.append({
        "id": "patch_health",
        "title": (f"호환 패치 {len(dead)}개 무력화 — 즉시 수리" if dead
                  else "호환 패치 실효성 — 정상" if not unknown
                  else "호환 패치 일부 판정 불가"),
        "severity": "high" if dead else ("low" if unknown else "good"),
        "finding": " / ".join(
            f"{n}: {'✅ 유효' if v else '❌ 무력' if v is False else '판정 불가'}"
            for n, v in probes),
        "action": ("무력 판정된 패치를 즉시 수리할 것. monkey-patch 는 `sys.modules` 순회로 "
                   "*모든 바인딩* 을 교체해야 한다 — `from X import f` 로 미리 복사된 참조는 "
                   "모듈 속성만 바꿔서는 안 바뀐다."
                   if dead else
                   "현재 정상. 새 패치·훅을 추가할 때는 반드시 효과 확인 함수를 함께 둘 것 "
                   "(precommit `copytruth` 카테고리가 커밋 단계에서 강제한다)."),
        "effect": ("무력화를 몇 초 만에 발견 — 종전엔 수일간 빈 응답으로만 드러났다"
                   if dead else "회귀 즉시 감지"),
        "tradeoff": "없음 (검사 자체가 수 밀리초)",
        "knob": "shared/claude_sdk_compat.patch_effective / shared/pytrends_utils.retry_compat_effective",
    })

    order = {"high": 0, "medium": 1, "low": 2, "good": 3}
    out.sort(key=lambda x: order.get(x["severity"], 9))
    return out


__all__ = ["record_call", "record_rate_limit", "summary", "history",
           "suggestions", "quota"]
