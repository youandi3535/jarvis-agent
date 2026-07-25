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
_PATTERNS = _DIR / "learned_patterns.json"

__all__ = [
    "history", "history_text", "parse_errors_md", "SLOT_LABELS",
]

# ── 서술 슬롯 ────────────────────────────────────────────────────────
# ERRORS.md 라벨은 자유형식(25종+)이라 표준 5슬롯으로 *수렴* 시킨다.
# 매핑에 없는 라벨도 버리지 않고 'other' 로 살려 표시 — 누락 0 이 원칙.
_SLOT_SYNONYMS: dict[str, tuple[str, ...]] = {
    "symptom": ("증상", "발단", "현상", "조사", "재현"),
    "cause":   ("원인", "본질 진단", "진단", "결론", "근본원인", "근본 원인"),
    "action":  ("해결", "조치", "변경", "구현", "수정", "설계"),
    "verify":  ("검증", "회귀", "회귀 결과", "결과", "즉시 효과", "확인"),
    "lesson":  ("교훈", "헛다리", "사용자 박제", "규정"),
    "files":   ("파일", "수정 파일", "모듈", "관련 파일"),
    "env":     ("환경",),
}
SLOT_LABELS: dict[str, str] = {
    "symptom": "증상 — 무엇이 잘못 보였나",
    "cause":   "원인 — 진짜 이유",
    "action":  "조치 — 어떻게 고쳤나",
    "verify":  "검증 — 정상임을 무엇으로 확인했나",
    "lesson":  "교훈",
    "files":   "파일",
    "env":     "환경",
    "other":   "기타",
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
    try:
        raw = json.loads(_PATTERNS.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = raw.get("patterns", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
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
    ("auto_repair",   "새벽 04:30 심층 감사 — 코드 전수 점검 중 발견"),
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


if __name__ == "__main__":  # pragma: no cover
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight()
    print(history_text(days=7, limit=8))
