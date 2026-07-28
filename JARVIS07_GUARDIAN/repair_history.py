"""수리 이력 — "누가 어떤 오류를 어떻게 잡아 어떻게 고쳤고, 그 결과 무엇이 어떻게 바뀌었나".

사용자 박제 2026-07-23: 대시보드 오류 관리 탭이 *발생 현황* 만 보여주고 *수리 서사* 가 없었다.
"가디언이 캐치해서 수정했어요" 로는 아무것도 설명 못 한다 — 6하 원칙을 답할 수 있어야 한다.

3원칙 적용
  ① 단일 진입점 — 이력 조립은 이 모듈 하나. api_server·텔레그램·CLI 모두 `history()` 만 호출.
  ② 동적 설계   — **새 표를 만들지 않는다.** 이미 있는 세 진실 소스를 *런타임 조인* 해 파생:
        · `error_log`            (기계 기록: 언제·무엇·어디·상태·조치)
        · `ERRORS.md`            (서술 기록: 증상·원인·해결·검증·교훈)
        · `learned_patterns.json`(수리 수단: 어떤 fixer 가 몇 번 통했나)
     특히 **결과(정상 상태)** 는 저장하지 않고 *재발 여부로 파생* 한다. 저장하면 즉시 낡는다.
  ③ 모든 경로   — 자동(Tier-1 패턴 / Tier-2 LLM) · 수동(Claude) · 사용자 신고 · git 회고 ·
     harness 자가해소 **5 경로 전부** 같은 서식으로 조립. 한 경로만 채우면 나머지가 공백이 된다.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

_DIR = Path(__file__).resolve().parent
_ERRORS_MD = _DIR / "ERRORS.md"
# _PATTERNS 상수 제거 — 경로의 주인은 pattern_fixer (① 단일 진입점)

__all__ = [
    "history", "history_text", "parse_errors_md", "SLOT_LABELS",
    # ★ 사고 지식 검색 정문 (ERRORS [534]) — 호출자는 이것만 쓴다
    "search_incidents", "incidents_brief",
    "next_incident_no", "duplicate_incident_nos", "selfcheck",
]

# ── 서술 슬롯 ────────────────────────────────────────────────────────
# ERRORS.md 라벨은 자유형식(25종+)이라 표준 5슬롯으로 *수렴* 시킨다.
# 매핑에 없는 라벨도 버리지 않고 'other' 로 살려 표시 — 누락 0 이 원칙.
_SLOT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "symptom": ("증상", "발단", "현상", "조사", "재현"),
    "cause":   ("원인", "본질 진단", "진단", "결론", "근본원인", "근본 원인"),
    "action":  ("해결", "조치", "변경", "구현", "수정", "설계"),
    "verify":  ("검증", "회귀", "회귀 결과", "결과", "즉시 효과", "확인"),
    # ★ ERRORS [534] — '헛다리' 를 lesson 에서 **분리**한다 (2026-07-27).
    #   종전엔 교훈과 한 슬롯에 뭉쳐 있어 *"이미 틀린 것으로 판명난 가설"* 을 따로 꺼낼 수 없었다.
    #   업계 포스트모템 템플릿(Google SRE·Amazon CoE·Atlassian·PagerDuty) 어디에도 없는
    #   필드이고, 이 저장소의 가장 값진 자산(373건)이다. 뭉쳐두면 검색이 못 쓴다.
    "dead_end": ("헛다리", "헛다리 (전부 실측으로 기각 — 다시 시도 금지)", "시도했으나 실패", "오진"),
    "lesson":  ("교훈", "사용자 박제", "규정"),
    "files":   ("파일", "수정 파일", "모듈", "관련 파일"),
    "env":     ("환경",),
}
SLOT_LABELS: dict[str, str] = {
    "symptom":  "증상 — 무엇이 잘못 보였나",
    "cause":    "원인 — 진짜 이유",
    "dead_end": "헛다리 — 이미 틀린 것으로 판명 (다시 시도 금지)",
    "action":   "조치 — 어떻게 고쳤나",
    "verify":   "검증 — 정상임을 무엇으로 확인했나",
    "lesson":   "교훈",
    "files":    "파일",
    "env":      "환경",
    "other":    "기타",
}
_LABEL_TO_SLOT: dict[str, str] = {
    lab: slot for slot, labs in _SLOT_SYNONYMS.items() for lab in labs
}

_ENTRY_RE = re.compile(r"^#{2,3}\s*\[(\d+)\]\s*(.+?)\s*$", re.M)
_BULLET_RE = re.compile(r"^\s*[-·*]\s*\*\*(.+?)\*\*\s*[:：]?\s*(.*)$")
_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")
_FILE_RE = re.compile(r"[\w./-]+\.(?:py|tsx|ts|md|json|sh|yml)")
_COMMIT_RE = re.compile(r"\[([0-9a-f]{6,40})\]")


# ── ERRORS.md 파서 (동적) ────────────────────────────────────────────
_md_cache: dict[str, Any] = {"mtime": 0.0, "entries": []}


def parse_errors_md(path: Path | None = None) -> list[dict]:
    """ERRORS.md 를 항목 단위로 파싱. mtime 캐시 — 파일이 바뀌면 자동 재파싱."""
    p = path or _ERRORS_MD
    if not p.exists():
        return []
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return []
    if path is None and _md_cache["mtime"] == mtime:
        return _md_cache["entries"]

    text = p.read_text(encoding="utf-8", errors="replace")
    heads = list(_ENTRY_RE.finditer(text))
    entries: list[dict] = []
    for i, m in enumerate(heads):
        body = text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        title = m.group(2).strip()
        slots: dict[str, list[str]] = {}
        cur: Optional[str] = None
        for line in body.splitlines():
            b = _BULLET_RE.match(line)
            if b:
                label = b.group(1).strip()
                cur = _LABEL_TO_SLOT.get(label, "other")
                val = b.group(2).strip()
                prefix = "" if cur != "other" else f"{label}: "
                slots.setdefault(cur, []).append(prefix + val)
            elif cur and line.strip() and line.startswith((" ", "\t")):
                # 다음 줄 이어붙이기 (ERRORS.md 는 들여쓰기 연속행이 흔함)
                slots[cur][-1] = (slots[cur][-1] + " " + line.strip()).strip()
        dm = _DATE_RE.search(title) or _DATE_RE.search(body[:400])
        entries.append({
            "no": int(m.group(1)),
            "title": _DATE_RE.sub("", title).strip(" ()·-"),
            "date": dm.group(1) if dm else "",
            "slots": {k: [v for v in vs if v] for k, vs in slots.items()},
            "files": sorted({f for f in _FILE_RE.findall(body) if "/" in f or f.endswith(".md")}),
        })
    entries.sort(key=lambda e: -e["no"])
    if path is None:
        _md_cache.update(mtime=mtime, entries=entries)
    return entries


# ── learned_patterns 조회 (어떤 수단으로 고쳤나) ──────────────────────
def _pattern_index() -> dict[str, dict]:
    # ★ 조회는 pattern_fixer 단독 진입점 (① — 경로 사본·손상 격리 우회 제거)
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import all_patterns  # noqa: PLC0415
        items = all_patterns()
    except Exception:
        return {}
    idx: dict[str, dict] = {}
    for it in items:
        et = str(it.get("error_type") or "")
        if et:
            idx.setdefault(et, it)
    return idx


# ── 파생 1: 어떻게 잡았나 (탐지 경로) ─────────────────────────────────
# GUARDIAN catch() 의 6개 캐치 메커니즘 + 사람 경로를 source 로 역추적.
_DETECTOR_RULES: tuple[tuple[str, str], ...] = (
    ("harness",       "발행 검증 순환 — 송출 전 Layer 3 이 잡음"),
    ("git_audit",     "매일 03:30 git 회고 — 커밋된 변경을 사후 박제"),
    ("auto_repair",   "심층 감사(`j07_deep_audit`) — 코드 전수 점검 중 발견"),
    ("vscode_claude", "작업 기록 — Claude 가 편집하며 스스로 신고"),
    ("user_incident", "사용자 신고 — 사람이 눈으로 발견"),
    ("manual-",       "사람이 발견 — 작업 중 결함 포착"),
    ("log_scan",      "로그 스캐너 — 로그 파일에서 예외 문자열 검출"),
)


def _detector(row: dict) -> str:
    # ★ status='manual' 은 *사람이 찾아서 신고한* 기록이다. source 는 신고자가 붙인
    #   도메인 이름(guardian·writer…)일 뿐이므로 source 로만 판정하면 "런타임 자동 캐치"
    #   로 오분류된다 — 잡은 주체를 사실과 반대로 표시하게 된다.
    if str(row.get("status") or "") == "manual":
        m = _ACTOR_RE.match(str(row.get("resolution") or ""))
        actor = m.group(1) if m else ""
        if actor in _HUMAN_ACTORS:
            return f"{_ACTOR_LABEL[actor]} 가 작업 중 발견 — 런타임 예외가 아니라 사람이 포착"
    src = str(row.get("source") or "")
    for key, desc in _DETECTOR_RULES:
        if key in src:
            return desc
    return "런타임 자동 캐치 — 예외가 터진 순간 catch() 가 가로챔"


# ── 파생 2: 누가 고쳤나 (수리 주체) ───────────────────────────────────
_ACTOR_RE = re.compile(r"^\[([a-z_]+)\]")
_ACTOR_LABEL = {
    "claude":        "Claude (수동 수리)",
    "vscode_claude": "VS Code Claude",
    "external_user": "커밋 회고 (사람 작업 사후 박제)",
    "user":          "사용자",
    "auto_repair":   "GUARDIAN 심층 감사 (LLM)",
    "git_audit":     "커밋 회고",
    "guardian":      "GUARDIAN 자동 수리",
}
_HUMAN_ACTORS = frozenset({"claude", "vscode_claude", "external_user", "user"})


def _fixer(row: dict, pidx: dict) -> tuple[str, str]:
    """(주체, 수단) — 어떻게 고쳤는지까지 파생."""
    res = str(row.get("resolution") or "")
    status = str(row.get("status") or "")
    m = _ACTOR_RE.match(res)
    if m and m.group(1) in _HUMAN_ACTORS:
        return _ACTOR_LABEL[m.group(1)], "사람이 코드를 직접 고침"
    if m:
        return _ACTOR_LABEL.get(m.group(1), m.group(1)), "GUARDIAN 이 자동 적용"
    if str(row.get("source") or "") == "harness":
        return "harness 자가 해소", "같은 동작을 재시도해 검증 통과 — 코드 수정 없음"
    if status == "wontfix":
        return "GUARDIAN 자동 수리 (실패)", "패치를 적용했으나 검증에서 걸려 원상 복구"
    if status in ("fixed", "resolved"):
        p = pidx.get(str(row.get("error_type") or ""))
        if p:
            fx, tier = str(p.get("fixer") or ""), str(p.get("tier") or "")
            if fx and fx != "llm_patch":
                return "GUARDIAN Tier-1 (패턴)", f"학습된 패턴 `{fx}` 자동 적용 — LLM 호출 0"
            if tier == "llm" or fx == "llm_patch":
                return "GUARDIAN Tier-2 (LLM)", "Sonnet 5 가 패치를 생성해 적용"
        return "GUARDIAN 자동 수리", "패치 적용 후 문법·import 검증 통과"
    return "미수리", ""


# ── 파생 3: 고친 뒤 어떻게 됐나 (결과·정상 상태) ──────────────────────
def _outcome(con: sqlite3.Connection, row: dict) -> dict:
    """★ 저장하지 않고 파생한다 — '재발했는가' 가 정상 여부의 유일한 실증."""
    status = str(row.get("status") or "")
    if status == "ignored":
        return {"state": "n/a", "text": "코드 결함이 아님 — 수리 대상 밖으로 분류"}
    if status in ("new", "analyzing"):
        return {"state": "open", "text": "아직 수리 전"}
    if status == "wontfix":
        return {"state": "fail", "text": "자동 수리 실패 — 롤백됨 (수동 검토 필요)"}

    since = str(row.get("fixed_at") or row.get("timestamp") or "")
    try:
        r = con.execute(
            "SELECT COUNT(*) AS c, MAX(timestamp) AS last FROM error_log "
            "WHERE error_type=? AND IFNULL(module,'')=IFNULL(?,'') "
            "AND timestamp > ? AND id <> ?",
            (row.get("error_type"), row.get("module"), since, row.get("id")),
        ).fetchone()
        recur, last = int(r["c"] or 0), (r["last"] or "")
    except Exception:
        recur, last = 0, ""

    try:
        elapsed = datetime.now() - datetime.fromisoformat(since[:19])
    except Exception:
        elapsed = timedelta(0)
    span = _span_text(elapsed)

    if recur:
        return {"state": "recur", "recur": recur,
                "text": f"수정 뒤에도 같은 증상 {recur}회 재발 (마지막 {last[:16].replace('T',' ')}) — 근본 원인 미해결"}
    if elapsed < timedelta(hours=6):
        return {"state": "watch", "recur": 0,
                "text": f"수정 후 {span} 무재발 — 관측 시간이 짧아 아직 관측 중"}
    return {"state": "ok", "recur": 0,
            "text": f"수정 후 {span} 동안 같은 증상 0회 — 정상 동작 중"}


def _span_text(d: timedelta) -> str:
    s = max(0, int(d.total_seconds()))
    if s < 3600:
        return f"{s // 60}분"
    if s < 86400:
        return f"{s // 3600}시간"
    return f"{s // 86400}일"


# ── 파생 4: 증상 (사람이 읽는 현상) ───────────────────────────────────
def _symptom(row: dict) -> str:
    """harness 이슈는 context 의 detail 이 message 보다 훨씬 구체적이다."""
    ctx = row.get("context") or ""
    if ctx.startswith("{"):
        try:
            c = json.loads(ctx)
            detail = str(c.get("detail") or "").strip()
            step = str(c.get("step") or "").strip()
            if detail:
                return f"{detail}" + (f"  ({step} 단계)" if step else "")
        except Exception:
            pass
    return str(row.get("message") or "")[:400]


# ── 서술 매칭 (ERRORS.md ↔ error_log) ────────────────────────────────
_TOKEN_RE = re.compile(r"[가-힣A-Za-z_]{2,}")


def _tokens(s: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(s or "")}


def _match_narrative(row: dict, entries: list[dict], files: list[str],
                     summary: str = "") -> Optional[dict]:
    """파일 겹침 + 제목 낱말 겹침 + 날짜 근접으로 서술을 찾는다.

    ★ 억지 매칭 금지 — 파일만 겹치면 *같은 날 다른 작업* 을 엉뚱하게 붙인다.
      그래서 조치 문구(커밋 메시지 등)와 제목의 낱말 일치를 함께 요구한다. 없으면 None.
    """
    day = str(row.get("fixed_at") or row.get("timestamp") or "")[:10]
    fset = {f for f in files if f}
    bset = {f.rsplit("/", 1)[-1] for f in fset}
    stok = _tokens(summary)
    best, best_score = None, 0.0
    for e in entries:
        if not e["date"] or abs(_daydiff(e["date"], day)) > 2:
            continue
        etok = _tokens(e["title"])
        # 제목 낱말을 얼마나 덮는가 (개수가 아니라 *비율* — 긴 제목이 유리해지지 않게)
        ratio = (len(stok & etok) / len(etok)) if (stok and etok) else 0.0
        ebase = {f.rsplit("/", 1)[-1] for f in e["files"]}
        score = ratio * 12 + len(fset & set(e["files"])) * 3 + len(bset & ebase) * 2
        if e["date"] == day:
            score += 1
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 8 else None


def _same_text(a: str, b: str) -> bool:
    na, nb = re.sub(r"\s+", "", a or "")[:80], re.sub(r"\s+", "", b or "")[:80]
    return bool(na) and na == nb


def _daydiff(a: str, b: str) -> int:
    try:
        return (datetime.fromisoformat(a) - datetime.fromisoformat(b)).days
    except Exception:
        return 999


# ── 묶음 키 (한 번의 수리 = 한 줄) ───────────────────────────────────
def _group_key(row: dict) -> str:
    """커밋 1건이 파일 수만큼 행을 만든다 — 사람에겐 '수리 1건'. 묶어서 보여준다."""
    et = str(row.get("error_type") or "")
    res = str(row.get("resolution") or "")
    if et == "GitCommit":
        m = _COMMIT_RE.search(res)
        if m:
            return f"commit:{m.group(1)}"
    if et in ("ExternalEdit", "GitCommit"):
        return f"{et}:{str(row.get('source'))}:{str(row.get('fixed_at') or row.get('timestamp'))[:16]}"
    return f"id:{row.get('id')}"


def _clean_resolution(res: str) -> str:
    res = _ACTOR_RE.sub("", res).strip()
    res = _COMMIT_RE.sub("", res, count=1).strip()
    return res


# ── 메인 ─────────────────────────────────────────────────────────────
def history(days: int = 30, limit: int = 40, actor: str = "",
            db_path: str | Path | None = None) -> list[dict]:
    """수리 이력 조립 — 이 함수가 유일한 진입점.

    Args:
        days:  조회 기간
        limit: 반환 건수 (묶음 기준)
        actor: "auto"(자동 수리만) / "manual"(사람 수리만) / "" (전체)
    """
    if db_path is None:
        from shared.db import DB_PATH as db_path  # type: ignore
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, timestamp, source, module, func_name, error_type, message, context, "
            "       severity, status, resolution, fixed_file, fixed_at, seen_count "
            "FROM error_log "
            "WHERE status IN ('fixed','resolved','manual','wontfix') "
            "  AND timestamp >= datetime('now', ?, 'localtime') "
            "ORDER BY id DESC LIMIT 4000",
            (f"-{int(days)} days",),
        ).fetchall()]

        entries = parse_errors_md()
        pidx = _pattern_index()

        groups: dict[str, dict] = {}
        for row in rows:
            key = _group_key(row)
            g = groups.get(key)
            if g is None:
                who, method = _fixer(row, pidx)
                g = groups[key] = {
                    "key": key,
                    "ids": [],
                    "at": row["timestamp"],
                    "fixed_at": row.get("fixed_at") or "",
                    "severity": row.get("severity") or "low",
                    "error_type": row.get("error_type") or "",
                    "source": row.get("source") or "",
                    "status": row.get("status") or "",
                    "detected": _detector(row),
                    "who": who,
                    "method": method,
                    "symptom": _symptom(row),
                    "action": _clean_resolution(str(row.get("resolution") or "")),
                    "files": [],
                    "_row": row,
                }
            g["ids"].append(row["id"])
            for f in (row.get("fixed_file"), row.get("module")):
                if f and f not in g["files"] and ("/" in f or "." in f):
                    g["files"].append(f)
            if _sev_rank(row.get("severity")) > _sev_rank(g["severity"]):
                g["severity"] = row.get("severity")

        out: list[dict] = []
        for g in groups.values():
            row = g.pop("_row")
            nar = _match_narrative(row, entries, g["files"], g["action"])
            g["outcome"] = _outcome(con, row)
            g["count"] = len(g["ids"])
            g["elapsed"] = _fix_elapsed(g["at"], g["fixed_at"])
            g["auto"] = g["who"].startswith("GUARDIAN") or g["who"].startswith("harness")
            g["kind"] = _kind(g["error_type"])
            if nar:
                # ★ 서술로 사실을 *덮어쓰지 않는다*. 기계 기록(이 행의 사실)과 서술 기록(사람의
                #   설명)은 출처가 다르므로 각자 자리에 둔다 — 합치면 잘못 붙은 서술이 사실이 된다.
                g["narrative"] = {
                    "no": nar["no"], "title": nar["title"], "date": nar["date"],
                    "slots": nar["slots"],
                }
            # 수동 수리는 message 와 resolution 이 같은 문장이라 ②증상·④조치가 겹친다.
            # 겹치면 *출처를 밝히고* 서술 쪽 증상으로 보강 — 없으면 겹침 사실을 그대로 표기.
            if _same_text(g["symptom"], g["action"]):
                ns = (nar or {}).get("slots", {}).get("symptom") if nar else None
                if ns:
                    g["symptom"] = ns[0][:400]
                    g["symptom_from"] = f"ERRORS.md [{nar['no']}]"
                else:
                    g["symptom"] = ""
                    g["symptom_from"] = "별도 증상 기록 없음 — 조치 내용이 곧 설명"
            else:
                g["symptom_from"] = "error_log"
            out.append(g)

        out.sort(key=lambda g: (g["fixed_at"] or g["at"]), reverse=True)
        if actor == "auto":
            out = [g for g in out if g["auto"]]
        elif actor == "manual":
            out = [g for g in out if not g["auto"]]
        return out[:limit]
    finally:
        con.close()


def _kind(error_type: str) -> str:
    """수리(고장을 고친 것) vs 변경(정책·기능 작업 기록) 구분.

    ★ 목록을 여기 복사하지 않는다 — `error_collector._MANUAL_POLICY_TYPES` 가 이미
      "재발 개념이 없는 정책/기능 변경" 의 단일 진실 소스다. 거기서 파생한다.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import _MANUAL_POLICY_TYPES as _P
    except Exception:
        return "repair"
    return "change" if error_type in _P else "repair"


_SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def _sev_rank(s: Any) -> int:
    return _SEV_ORDER.get(str(s or "low"), 0)


def _fix_elapsed(at: str, fixed_at: str) -> str:
    try:
        d = datetime.fromisoformat(fixed_at[:19]) - datetime.fromisoformat(at[:19])
        return _span_text(d)
    except Exception:
        return ""


# ── 텔레그램·CLI 서식 (같은 데이터, 같은 진입점) ──────────────────────
def history_text(days: int = 7, limit: int = 10) -> str:
    items = history(days=days, limit=limit)
    if not items:
        return f"최근 {days}일 수리 이력 없음"
    lines = [f"🧾 수리 이력 — 최근 {days}일 {len(items)}건", ""]
    for g in items:
        lines.append(f"[{g['error_type']}] {g['symptom'][:60]}")
        lines.append(f"  잡은 경로 · {g['detected']}")
        lines.append(f"  수리 주체 · {g['who']} — {g['method']}")
        lines.append(f"  결과      · {g['outcome']['text']}")
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
#  사고 지식 검색 — ★ 정문 (ERRORS [534], 사용자 박제 2026-07-27)
# ═══════════════════════════════════════════════════════════════════
#
# ★ 왜 만들었나: ERRORS.md 는 9,855줄(1.2MB)·사고 186건·**헛다리 373건** 인데,
#   Tier-2 프롬프트는 `head -60` 으로 **앞 60줄(0.6%)** 만 읽고 있었다.
#   CLAUDE.md 는 "오류 나면 ERRORS.md 를 먼저 읽어라" 라고 못박았지만
#   실제로 도달하는 건 최신 1건뿐 — **규정이 코드에서 사실상 우회되고 있었다**
#   (log_scanner 70일 0건과 같은 병: 코드는 있는데 일을 안 함).
#
# ★ 왜 '전량 읽기' 가 답이 아닌가 (반직관 — 이게 중요):
#   Chroma 의 Context Rot 연구(프론티어 모델 18종 전수)는 **예외 없이 전부**
#   입력이 길어질수록 성능이 떨어짐을 보였다. 한도 *한참 전* 에 시작되고,
#   200K 창 모델이 50K 에서 이미 유의미하게 저하된다. 게다가
#   **잘 구조화된 일관된 입력이 뒤죽박죽보다 주의를 더 갉아먹는다** —
#   양식이 통일된 ERRORS.md 가 오히려 불리하다. 186건 중 1건을 찾을 때
#   나머지 185건은 순수한 방해 요소(distractor)다.
#   → 정답은 "전량"도 "앞 60줄"도 아닌 **조준 검색**이다.
#
# ★ 왜 ChromaDB 를 안 쓰나 (② 동적 설계):
#   505 항목 × 384차원 = 758KB — 메모리로 충분하다. 영구 색인을 따로 두면
#   그게 곧 *원본과 어긋날 수 있는 사본* 이다("복사본을 진실로 믿지 말 것").
#   여기서는 ERRORS.md 를 진실로 두고 **mtime 이 바뀌면 자동 재계산**한다.
#   색인 갱신 잡·백필·정합성 검사가 통째로 불필요해진다.
#
# ★ 왜 하이브리드(키워드+벡터)인가:
#   순수 벡터는 `NoneType`·`TypeError`·파일경로 같은 **정확 문자열**에서 실패하고,
#   순수 키워드는 "이미지가 연달아 붙는다" ↔ "figure 태그 연속" 같은
#   **표현 차이**를 못 넘는다. 사고 기록은 둘 다 필요한 전형적 케이스다.
#   임베딩 미가용 시에도 키워드만으로 degrade — 검색이 아예 죽지 않는다(fail-open).

_SEARCH_CACHE: dict[str, Any] = {"mtime": 0.0, "vecs": None, "rows": [], "idf": {}, "toks": []}

# 검색 튜닝 — ① 단일 진입점(여기 한 곳). ② `_flag`/env 로 무배포 조정.
_KW_WEIGHT   = 0.5    # 키워드 점수 가중
_VEC_WEIGHT  = 0.5    # 벡터 점수 가중
# 실측 점수 분포로 교정 (ERRORS [534]): 무관 질의 0.09~0.37 / 유관 질의 0.40~0.71.
# 경계를 유관 쪽 하한 바로 아래에 둔다 — 빈손이 오답보다 낫다.
# 실측 분리 (토큰 매칭 + IDF 후): 무관 0.099~0.398 / 유관 0.427~0.709.
# 경계를 그 사이에 둔다 — 빈손이 오답보다 낫다.
_MIN_SCORE   = 0.41

# ★ 2단계 리랭커 노브 (ERRORS [544]) — 값의 근거는 `golden_queries.json` 실측 스윕.
#   RERANK_POOL: 1단계에서 리랭커에 넘길 후보 수. 이게 recall 상한을 정한다
#     (실측 1단계 recall @20 36.4% / @100 63.6% / @200 82.7%, 100건 재점수 1.2초 CPU).
#   RERANK_MIN_SCORE: cross-encoder 로짓 임계 — **코사인 임계(_MIN_SCORE)와 스케일이 다르다.**
#     둘을 섞어 쓰면 안 된다. 값 변경 시 `selfcheck()` 의 골든셋 레그로 반드시 재측정할 것.
#   ★ 값 근거 — 골든셋 36질의/정답 110개 임계 스윕 실측 (2026-07-28):
#       임계 | recall@5 | 정밀도 | 빈손
#       없음 |   14.5%  |  16.7% | 26/36   ← 리랭커 도입 전 기준선
#        -5  |   21.8%  |  34.8% | 11/36
#        -1  |   17.3%  |  47.5% | 23/36   ← 채택
#         0  |   14.5%  |  53.3% | 25/36
#     -1 을 고른 이유: 세 지표가 **모두 기준선 이상**이면서, 이 저장소의 계약
#     *"오답 < 빈손"* 을 -5 보다 잘 지킨다(오답 45→21). 오답은 무관 사고의
#     "⛔ 헛다리" 를 LLM 프롬프트에 주입해 *능동적으로 오도* 하므로 빈손보다 비싸다.
#   ⚠️ 이건 **부분 개선이지 해결이 아니다** — 진짜 병목은 1단계 recall 이다
#     (1단계 recall@100 이 63.6% 라 리랭커가 볼 수 있는 상한 자체가 낮다).
#     다음 작업: 1단계 개선(질의 확장·키워드 레그 보정·인덱스 텍스트 재구성).
RERANK_POOL      = 100
RERANK_MIN_SCORE = -1.0


def _search_env(name: str, default: float) -> float:
    """검색 노브 — *호출 시점* 조회 (모듈 로드 캡처 금지)."""
    import os as _os
    try:
        return float(_os.getenv(name) or default)
    except ValueError:
        return default


def _row_text(row: dict) -> str:
    """한 사고를 검색 대상 문자열로 — 제목 + 전 슬롯. 누락 0 이 원칙."""
    parts = [str(row.get("title") or "")]
    for vals in (row.get("slots") or {}).values():
        parts.extend(str(v) for v in (vals if isinstance(vals, list) else [vals]))
    parts.extend(row.get("files") or [])
    return "\n".join(p for p in parts if p)


def _index() -> tuple[list[dict], Any]:
    """검색 인덱스 — ERRORS.md 에서 *파생*. mtime 이 바뀌면 자동 재계산(사본 없음)."""
    rows = parse_errors_md()          # 이미 mtime 캐시 내장
    try:
        mtime = _ERRORS_MD.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _SEARCH_CACHE["mtime"] == mtime and _SEARCH_CACHE["rows"]:
        return _SEARCH_CACHE["rows"], _SEARCH_CACHE["vecs"]

    # ★ IDF — 문서에서 파생한다(사본 없음). 흔한 토큰일수록 가중 ↓
    import math as _math
    texts = [_row_text(r).lower() for r in rows]
    df: dict[str, int] = {}
    for t in texts:
        for tok in {x.lower() for x in _TOKEN_RE.findall(t)}:
            df[tok] = df.get(tok, 0) + 1
    n = len(texts) or 1
    idf = {tok: _math.log(n / (1 + c)) + 1.0 for tok, c in df.items()}
    toks = [{x.lower() for x in _TOKEN_RE.findall(t)} for t in texts]

    vecs = None
    try:
        from shared import embeddings as _emb
        if _emb.available() and rows:
            vecs = _emb.embed_texts([_row_text(r)[:2000] for r in rows])
    except Exception as e:  # noqa: BLE001 — 임베딩 실패해도 키워드로 degrade
        log.debug(f"[repair_history] 임베딩 인덱스 생략 — 키워드만 사용: {e}")
    _SEARCH_CACHE.update({"mtime": mtime, "rows": rows, "vecs": vecs, "idf": idf, "toks": toks})
    return rows, vecs


_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[가-힣]{2,}|[\w./-]+\.\w+")


def _kw_score(q_tokens: set[str], doc_tokens: set[str], idf: dict[str, float]) -> float:
    """IDF 가중 키워드 겹침 — 정확 식별자(NoneType·파일경로)가 벡터보다 강한 구간 담당.

    ★ **토큰 경계 매칭이다. 부분문자열(`in`) 이 아니다** (ERRORS [534]).
      종전 `t in text` 는 "사진" 이 "AI사진생성" 안에 걸리는 식으로 오탐을 냈다 —
      어제 `incident_responder` 에서 고친 것과 **같은 병**(경계 없는 부분문자열).
      실측: 부분문자열이면 무관 질의 kw 0.79 / 유관 0.72 로 **역전**했고,
      토큰 매칭으로 바꾸자 무관 ≤0.56 / 유관 ≥0.72 로 갈렸다.

    ★ IDF: 흔한 토큰일수록 가중 ↓ (BM25 와 같은 발상). 미등장 토큰은 희귀로 간주(3.0)해
      "질의에만 있고 문서엔 없는 단어" 가 분모를 키우도록 한다 — 무관 질의가 눌린다.
    """
    if not q_tokens:
        return 0.0
    num = sum(idf.get(t, 3.0) for t in q_tokens if t in doc_tokens)
    den = sum(idf.get(t, 3.0) for t in q_tokens) or 1.0
    return num / den


def search_incidents(query: str, top_k: int = 3, min_score: float | None = None) -> list[dict]:
    """★ 사고 지식 검색 정문 — "이 증상 겪은 적 있나?" 한 줄로 묻는다.

    하이브리드(키워드 + 시맨틱)로 ERRORS.md 전체를 조준 검색한다.
    호출자가 파서·임베딩·점수 조합을 알 필요가 없다 (① 단일 진입점).

    Args:
        query:     증상·오류 메시지·traceback 등 자유 문장
        top_k:     반환할 사고 수 (기본 3 — Context Rot 고려한 소수 정예)
        min_score: 이 점수 미만은 버림 (None 이면 기본값)

    Returns:
        [{no, title, date, score, symptom, cause, dead_end, action, lesson, files}, ...]
        관련 사고가 없으면 **빈 리스트** — 억지로 채우지 않는다(오답 < 빈손).
    """
    q = (query or "").strip()
    if not q:
        return []
    thr = _MIN_SCORE if min_score is None else min_score
    kw_w = _search_env("GUARDIAN_SEARCH_KW_WEIGHT", _KW_WEIGHT)
    vec_w = _search_env("GUARDIAN_SEARCH_VEC_WEIGHT", _VEC_WEIGHT)

    try:
        rows, vecs = _index()
    except Exception as e:  # noqa: BLE001 — 검색 실패가 수리 흐름을 막으면 안 된다
        log.warning(f"[repair_history] 사고 인덱스 실패: {e}")
        return []
    if not rows:
        return []

    q_tokens = {t.lower() for t in _TOKEN_RE.findall(q)}
    _idf = _SEARCH_CACHE.get("idf") or {}
    _toks = _SEARCH_CACHE.get("toks") or []
    kw = [_kw_score(q_tokens, _toks[i] if i < len(_toks) else set(), _idf)
          for i in range(len(rows))]

    vec = [0.0] * len(rows)
    if vecs is not None:
        try:
            from shared import embeddings as _emb
            import numpy as _np
            qv = _emb.embed_text(q[:2000])
            sims = _np.asarray(vecs) @ _np.asarray(qv)
            vec = [max(0.0, float(s)) for s in sims]
        except Exception as e:  # noqa: BLE001
            log.debug(f"[repair_history] 벡터 점수 생략: {e}")

    scored = [(kw_w * k + vec_w * v, r) for r, k, v in zip(rows, kw, vec)]
    scored.sort(key=lambda x: -x[0])

    # ── 2단계: 리랭커(cross-encoder) 재점수 (ERRORS [544]) ──────────────
    #
    # ★ 왜 필요했나 — 골든셋 36질의/정답 110개 실측으로 1단계 단독 성능이 드러났다:
    #     recall@5 **14.5%** · 빈손율 **72.2%** · 임계 통과분의 정밀도 **16.7%**(정답4/오답20).
    #   즉 CLAUDE.md 가 "통독 말고 조준 검색하라" 고 규정한 도구가 10번 중 7번 빈손이었고,
    #   뭔가 나올 때도 5번 중 4번이 오답이었다.
    #
    # ★ 왜 리랭커로 고쳐지나 — 1단계는 질의와 문서를 *각각 따로* 벡터로 만들어 비교하므로
    #   "단어가 겹친다" 에 속는다(자기참조 오염: 그 문구를 예시로 인용한 무관 사고가 1위).
    #   cross-encoder 는 둘을 **붙여서 한 번에** 읽어 "인용이지 이 사고가 아니다" 를 구분한다.
    #
    # ★ 왜 풀을 넓히나 — 리랭커는 *후보에 올라온 것만* 재정렬한다. 1단계 recall 실측:
    #     @5 14.5% / @20 36.4% / @100 **63.6%** / @200 82.7%
    #   → 상한을 사려면 풀을 넓혀야 한다. 100건 재점수 실측 1.2초(CPU) 로 감당 가능하고,
    #     이 함수는 발행 임계경로가 아니다(GUARDIAN Tier-2·auto_repair 진단 경로).
    #
    # ★ fail-open: 모델이 없거나 실패하면 1단계 결과를 종전 임계로 그대로 쓴다.
    #   검색이 리랭커 때문에 멈추는 일은 없어야 한다.
    import os as _os_rr
    _use_rr = _os_rr.getenv("GUARDIAN_RERANK", "1") != "0"
    _rr_hits: list = []
    if _use_rr and scored:
        try:
            from shared import embeddings as _emb2
            pool = scored[:RERANK_POOL]
            _rr_hits = _emb2.rerank(q, [_row_text(r)[:2000] for _, r in pool])
        except Exception as e:  # noqa: BLE001
            log.debug(f"[repair_history] 리랭크 생략: {e}")
            _rr_hits = []

    if _rr_hits:
        rr_thr = _search_env("GUARDIAN_RERANK_MIN", RERANK_MIN_SCORE)
        pool = scored[:RERANK_POOL]
        # ★ 점수 의미가 바뀐다 — 로짓(음수 가능). 코사인 임계(_MIN_SCORE)를 재사용하면 안 된다.
        final = [(rs, pool[i][1], pool[i][0]) for i, rs in _rr_hits if rs >= rr_thr]
    else:
        final = [(s, r, s) for s, r in scored if s >= thr]

    out = []
    for s, r, _stage1 in final[:top_k]:
        slots = r.get("slots") or {}
        first = lambda key: (slots.get(key) or [""])[0] if slots.get(key) else ""   # noqa: E731
        out.append({
            "no": r.get("no"), "title": r.get("title"), "date": r.get("date"),
            "score": round(s, 4),
            "symptom":  first("symptom"),
            "cause":    first("cause"),
            "dead_end": " / ".join(slots.get("dead_end") or []),   # ★ 가장 값진 필드
            "action":   first("action"),
            "lesson":   first("lesson"),
            "files":    r.get("files") or [],
        })
    return out


def incidents_brief(query: str, top_k: int = 3) -> str:
    """검색 결과 → **프롬프트 주입용** 한국어 블록. 관련 사고 없으면 "" (빈손).

    ★ Tier-2 프롬프트가 `head -60` 대신 이걸 쓴다. 왜 문자열까지 여기서 만드나 —
      포맷이 호출자마다 갈리면 그게 곧 사본이다(① 단일 진입점).
    """
    hits = search_incidents(query, top_k=top_k)
    if not hits:
        return ""
    lines = ["", "─" * 30,
             f"📚 *과거 유사 사고 {len(hits)}건* — ERRORS.md 조준 검색 결과", ""]
    for h in hits:
        lines.append(f"### [{h['no']}] {h['title']}  ({h['date']}, 유사도 {h['score']})")
        if h["symptom"]:
            lines.append(f"- 증상: {h['symptom'][:300]}")
        if h["cause"]:
            lines.append(f"- 원인: {h['cause'][:300]}")
        if h["dead_end"]:
            # ★ 헛다리를 맨 앞·강조로 — 업계 템플릿에 없는 이 저장소의 핵심 자산이다.
            lines.append(f"- ⛔ **헛다리(다시 시도 금지)**: {h['dead_end'][:400]}")
        if h["action"]:
            lines.append(f"- 해결: {h['action'][:300]}")
        if h["files"]:
            lines.append(f"- 파일: {', '.join(h['files'][:6])}")
        lines.append("")
    return "\n".join(lines)


# ── 사고 번호 — ★ 파일에서 파생 (② 동적 설계) ──────────────────────
def next_incident_no() -> int:
    """다음에 쓸 사고 번호 — ERRORS.md 최대값 + 1.

    ★ 왜: 사람이 눈으로 세다가 **중복 ID 12개**가 생겼다([402][403][404][437]
      [453]~[456] 등). CLAUDE.md 가 `ERRORS [474]` 처럼 번호로 참조하는데
      한 번호가 두 곳을 가리키면 상호참조가 흔들린다. 숫자를 손으로 정하지 않는다.
    """
    try:
        rows = parse_errors_md()
        return max((int(r.get("no") or 0) for r in rows), default=0) + 1
    except Exception:  # noqa: BLE001
        return 0


def duplicate_incident_nos() -> list[int]:
    """중복 사용된 사고 번호 — 회귀 감시용(0건이어야 정상)."""
    try:
        from collections import Counter
        c = Counter(int(r.get("no") or 0) for r in parse_errors_md())
        return sorted(n for n, k in c.items() if k > 1 and n)
    except Exception:  # noqa: BLE001
        return []


def selfcheck() -> list[str]:
    """★ 검색이 *실제로 동작하는지* 확인 (존재가 아니라 동작으로).

    `head -60` 이 0.6%만 읽으면서도 아무도 몰랐던 것과 같은 무증상 열화를 막는다.
    """
    issues: list[str] = []
    try:
        rows, vecs = _index()
        if not rows:
            issues.append("[S1] ERRORS.md 파싱 0건 — 파서 또는 파일 경로 확인")
            return issues
        if vecs is None:
            issues.append("[S2] 임베딩 인덱스 없음 — 키워드 전용으로 degrade 중"
                          " (shared.embeddings.available() 확인)")
        # 자기 자신을 질의해 top-1 로 돌아오는지 (검색이 실제로 먹는가)
        probe = rows[0]
        hits = search_incidents((probe.get("title") or "")[:120], top_k=1)
        if not hits:
            issues.append("[S3] 자기 제목으로 검색해도 0건 — 점수 임계값이 과하게 높음")
        elif hits[0]["no"] != probe.get("no"):
            issues.append(f"[S3] 자기 제목 검색 top-1 불일치 "
                          f"(기대 {probe.get('no')} / 실제 {hits[0]['no']})")
        dups = duplicate_incident_nos()
        if dups:
            issues.append(f"[S4] 중복 사고 번호 {len(dups)}개: {dups[:12]}"
                          " — next_incident_no() 로 발급할 것")
        # [S5] 골든셋 회귀 감시 (ERRORS [544])
        issues.extend(golden_check())
    except Exception as e:  # noqa: BLE001
        issues.append(f"[S0] selfcheck 실패: {type(e).__name__}: {e}")
    return issues


# ── 골든셋 회귀 감시 ────────────────────────────────────────────────

GOLDEN_PATH = Path(__file__).parent / "golden_queries.json"

# ★ 회귀 임계 — 아래로 떨어지면 알린다. 값 근거는 2026-07-28 실측 (RERANK_MIN_SCORE 주석 표).
#   여유를 5%p 둔 이유: 리랭커·임베딩 모델은 미세하게 비결정적이고, ERRORS.md 가 자라면
#   후보 경쟁이 달라진다. 진짜 열화만 잡고 잡음에는 안 울리게 한다.
GOLDEN_MIN_RECALL   = 0.12      # 실측 0.173
GOLDEN_MAX_EMPTY    = 0.72      # 실측 0.639 (23/36)


def golden_check(sample: int = 0) -> list[str]:
    """★ 골든셋으로 검색 품질을 *실측* 한다 — 조준 검색의 무증상 열화 감시.

    왜 필요한가: 이 검색은 **틀려도 예외를 안 던진다.** 빈손이나 오답을 조용히 돌려줄 뿐이라
      코드를 읽어선 열화를 못 본다. 실제로 도입 시점 측정에서 recall@5 **14.5%** ·
      빈손 **72%** 였는데 아무도 몰랐다. 정답이 적힌 시험지로 재는 수밖에 없다.

    sample: 0 이면 전량. 양수면 앞에서 그만큼만 (빠른 점검용 — 부팅 경로 등).
    반환: 위반 문자열 목록 (비면 정상). 골든셋 파일이 없으면 그 사실 자체가 위반.
    """
    out: list[str] = []
    try:
        if not GOLDEN_PATH.exists():
            return [f"[S5] 골든셋 없음: {GOLDEN_PATH.name} — 검색 품질을 잴 수단이 없다"]
        pairs = (json.loads(GOLDEN_PATH.read_text(encoding="utf-8")) or {}).get("pairs") or []
        if not pairs:
            return ["[S5] 골든셋이 비어 있음 — 측정 불가"]
        if sample and sample > 0:
            pairs = pairs[:sample]
        tot = sum(len(p.get("expect") or []) for p in pairs) or 1
        hit = empty = 0
        for p in pairs:
            nos = [h["no"] for h in (search_incidents(p["query"], top_k=5) or [])]
            if not nos:
                empty += 1
            hit += len(set(p.get("expect") or []) & set(nos))
        recall, empty_rate = hit / tot, empty / len(pairs)
        if recall < GOLDEN_MIN_RECALL:
            out.append(f"[S5] 골든셋 recall@5 {recall:.1%} < 기준 {GOLDEN_MIN_RECALL:.0%} "
                       f"— 검색 열화 (질의 {len(pairs)}개/정답 {tot}개)")
        if empty_rate > GOLDEN_MAX_EMPTY:
            out.append(f"[S5] 골든셋 빈손율 {empty_rate:.1%} > 기준 {GOLDEN_MAX_EMPTY:.0%} "
                       f"— '과거 사례 없음' 오답이 늘었다")
    except Exception as e:  # noqa: BLE001
        out.append(f"[S5] 골든셋 점검 실패: {type(e).__name__}: {e}")
    return out


if __name__ == "__main__":  # pragma: no cover
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight()
    print(history_text(days=7, limit=8))
    print("\nselfcheck():", selfcheck() or "OK")
