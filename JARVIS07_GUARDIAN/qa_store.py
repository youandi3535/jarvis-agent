"""JARVIS07_GUARDIAN/qa_store.py — Claude Code 대화 Q&A 지식 베이스.

기능:
  - ~/.claude/projects/ JSONL 트랜스크립트 파싱 → Q&A 쌍 추출
  - SQLite FTS5 인덱싱 (빠른 전문 검색)
  - 중복 제거: 동일 질문은 답변 업그레이드, hit_count 누적
  - 증분 수집: 새 세션 파일만 처리 (mtime 기반)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path

log = logging.getLogger("jarvis.qa_store")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
# Claude Code 는 프로젝트 절대경로의 "/" 를 "-" 로 치환해 세션 폴더명으로 씀.
# 하드코딩 시 폴더 이동하면 구 폴더만 스캔 → 신규 세션 미수집. _ROOT 에서 동적 도출.
_PROJECT_KEY = str(_ROOT).replace("/", "-")

# ★ Cowork (Claude Desktop App local-agent-mode) transcript 위치 — 사용자 박제 2026-05-25
#   Cowork 는 VS Code Claude Code 와 별도 채널. ingest_cowork_sessions() 가 이 폴더를 스캔.
_COWORK_BASE = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"

# Cowork 내부에서 transcript 가 아닌 파일들 (제외 대상) — outputs/uploads/.auto-memory 등
_COWORK_EXCLUDE_DIRS = {"outputs", "uploads", ".auto-memory", "node_modules", "__pycache__"}

# IDE 자동 주입 패턴 (질문 정규화 시 제거)
_IDE_PATTERNS = [
    re.compile(r"<ide_[^>]*>.*?</ide_[^>]*>", re.DOTALL),
    re.compile(r"\[Image:[^\]]*\]"),
    re.compile(r"<[a-zA-Z_][^>]*>"),
]


# ── DB 초기화 ──────────────────────────────────────────────────────────────

def _init_qa_tables(conn) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS qa_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            question_hash   TEXT UNIQUE NOT NULL,
            question_raw    TEXT NOT NULL,
            question_norm   TEXT NOT NULL,
            answer          TEXT NOT NULL,
            source          TEXT DEFAULT 'claude',
            session_id      TEXT DEFAULT '',
            hit_count       INTEGER DEFAULT 1,
            confidence      REAL DEFAULT 1.0,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            file_changes    TEXT DEFAULT '[]'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS qa_fts USING fts5(
            question_norm,
            answer,
            content=qa_entries,
            content_rowid=id
        );

        CREATE TABLE IF NOT EXISTS qa_ingested_sessions (
            session_file    TEXT PRIMARY KEY,
            file_mtime      REAL NOT NULL,
            ingested_at     TEXT DEFAULT (datetime('now')),
            qa_count        INTEGER DEFAULT 0
        );

        CREATE TRIGGER IF NOT EXISTS qa_ai AFTER INSERT ON qa_entries BEGIN
            INSERT INTO qa_fts(rowid, question_norm, answer)
            VALUES (new.id, new.question_norm, new.answer);
        END;

        CREATE TRIGGER IF NOT EXISTS qa_ad AFTER DELETE ON qa_entries BEGIN
            INSERT INTO qa_fts(qa_fts, rowid, question_norm, answer)
            VALUES ('delete', old.id, old.question_norm, old.answer);
        END;

        CREATE TRIGGER IF NOT EXISTS qa_au AFTER UPDATE ON qa_entries BEGIN
            INSERT INTO qa_fts(qa_fts, rowid, question_norm, answer)
            VALUES ('delete', old.id, old.question_norm, old.answer);
            INSERT INTO qa_fts(rowid, question_norm, answer)
            VALUES (new.id, new.question_norm, new.answer);
        END;
    """)
    conn.commit()


# ── 정규화 / 해시 ───────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """질문 정규화: IDE 메타 제거 → 소문자 → 공백 압축."""
    for pat in _IDE_PATTERNS:
        text = pat.sub("", text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text[:600]


def _hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()[:20]


# ── 검색 ───────────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 5) -> list[dict]:
    """FTS5 전문 검색. 유사 Q&A 반환 (score 오름차순 = 관련도 높은 순)."""
    from shared import db as _db

    norm = _normalize(query)
    # 한글·영문·숫자·밑줄 추출 → FTS 쿼리 단어
    words = re.findall(r"[가-힣a-zA-Z0-9_\.]{2,}", norm)[:8]
    if not words:
        return []

    # FTS5: 각 단어 OR 검색 (prefix 매칭)
    fts_query = " OR ".join(f'"{w}"' for w in words[:6])

    try:
        with _db.get_db() as conn:
            _init_qa_tables(conn)
            rows = conn.execute(
                """
                SELECT e.id, e.question_raw, e.answer, e.source,
                       e.hit_count, e.confidence, e.updated_at,
                       rank AS score
                FROM qa_fts f
                JOIN qa_entries e ON e.id = f.rowid
                WHERE qa_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, top_k),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.debug(f"[QAStore] 검색 실패: {e}")
        return []


# ── 삽입 / 업데이트 ─────────────────────────────────────────────────────────

def upsert(
    question_raw: str,
    answer: str,
    source: str = "claude",
    session_id: str = "",
    confidence: float = 1.0,
    file_changes: list | None = None,
) -> str:
    """Q&A 삽입 또는 업데이트.

    동일 question_hash:
      - 새 답변이 더 길거나 신뢰도 높으면 → answer 업그레이드
      - 항상 hit_count + 1, updated_at 갱신
    Returns: 'inserted' | 'updated' | 'skipped'
    """
    from shared import db as _db

    norm = _normalize(question_raw)
    if not norm or len(norm) < 10:
        return "skipped"

    qhash = _hash(norm)
    fc_json = json.dumps(file_changes or [], ensure_ascii=False)

    with _db.get_db() as conn:
        _init_qa_tables(conn)
        existing = conn.execute(
            "SELECT id, answer, hit_count, confidence FROM qa_entries WHERE question_hash=?",
            (qhash,),
        ).fetchone()

        if existing:
            # 새 답변이 30% 이상 길거나 신뢰도 높으면 교체
            new_better = (
                len(answer) > len(existing["answer"]) * 1.3
                or confidence > existing["confidence"] + 0.1
            )
            conn.execute(
                """
                UPDATE qa_entries SET
                    hit_count  = hit_count + 1,
                    updated_at = datetime('now'),
                    answer     = CASE WHEN ? THEN ? ELSE answer END,
                    confidence = CASE WHEN ? THEN ? ELSE confidence END,
                    source     = CASE WHEN ? THEN ? ELSE source END
                WHERE question_hash = ?
                """,
                (
                    new_better, answer,
                    new_better, confidence,
                    new_better, source,
                    qhash,
                ),
            )
            conn.commit()
            return "updated"
        else:
            conn.execute(
                """
                INSERT INTO qa_entries
                    (question_hash, question_raw, question_norm, answer, source,
                     session_id, confidence, file_changes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qhash,
                    question_raw[:1000],
                    norm,
                    answer,
                    source,
                    session_id,
                    confidence,
                    fc_json,
                ),
            )
            conn.commit()
            return "inserted"


# ── JSONL 파싱 ─────────────────────────────────────────────────────────────

def _extract_qa_pairs(jsonl_path: Path) -> list[tuple[str, str]]:
    """JSONL 세션 파일 → (question, answer) 쌍 리스트."""
    pairs: list[tuple[str, str]] = []
    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="ignore").strip().split("\n")
        messages: list[tuple[str, str]] = []  # (role, text)

        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
            else:
                text = str(content).strip()

            if role in ("user", "assistant") and len(text) > 20:
                messages.append((role, text))

        # user → assistant 연속 쌍 추출
        i = 0
        while i < len(messages) - 1:
            if messages[i][0] == "user" and messages[i + 1][0] == "assistant":
                q, a = messages[i][1], messages[i + 1][1]
                # IDE 메타만 있는 질문 제외
                q_clean = _normalize(q)
                if len(q_clean) > 15 and len(a) > 30:
                    pairs.append((q, a))
                i += 2
            else:
                i += 1
    except Exception as e:
        log.debug(f"[QAStore] {jsonl_path.name} 파싱 오류: {e}")
    return pairs


def _mark_ingested(filename: str, mtime: float, qa_count: int) -> None:
    from shared import db as _db

    with _db.get_db() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO qa_ingested_sessions (session_file, file_mtime, qa_count)
            VALUES (?, ?, ?)
            """,
            (filename, mtime, qa_count),
        )
        conn.commit()


# ── 메인 ingest ────────────────────────────────────────────────────────────

def ingest_sessions(project_key: str = _PROJECT_KEY, force: bool = False) -> dict:
    """Claude Code JSONL 트랜스크립트 → qa_entries 증분 저장.

    이미 처리된 파일(mtime 변경 없음)은 skip.
    Returns: {processed, inserted, updated, skipped_files}
    """
    from shared import db as _db

    project_dir = _CLAUDE_PROJECTS / project_key
    if not project_dir.exists():
        log.warning(f"[QAStore] 프로젝트 폴더 없음: {project_dir}")
        return {"processed": 0, "inserted": 0, "updated": 0, "skipped_files": 0}

    jsonl_files = sorted(project_dir.glob("*.jsonl"))
    log.info(f"[QAStore] 총 {len(jsonl_files)}개 세션 파일")

    # 기처리 파일 목록 로드
    with _db.get_db() as conn:
        _init_qa_tables(conn)
        processed_map: dict[str, float] = {
            r["session_file"]: r["file_mtime"]
            for r in conn.execute(
                "SELECT session_file, file_mtime FROM qa_ingested_sessions"
            ).fetchall()
        }

    processed = inserted = updated = skipped_files = 0

    for jf in jsonl_files:
        try:
            mtime = jf.stat().st_mtime
        except OSError:
            continue

        key = jf.name
        # 변경 없으면 skip (force=True 면 재처리)
        if not force and key in processed_map and abs(processed_map[key] - mtime) < 1:
            skipped_files += 1
            continue

        qa_pairs = _extract_qa_pairs(jf)
        session_id = jf.stem
        n_ins = n_upd = 0

        for q, a in qa_pairs:
            status = upsert(q, a, source="claude", session_id=session_id)
            if status == "inserted":
                n_ins += 1
            elif status == "updated":
                n_upd += 1

        _mark_ingested(key, mtime, len(qa_pairs))
        processed += 1
        inserted += n_ins
        updated += n_upd

        if processed % 200 == 0:
            log.info(f"[QAStore] {processed}개 처리 중... +{inserted}ins +{updated}upd")

    log.info(
        f"[QAStore] 완료: 처리={processed} 신규={inserted} 업뎃={updated} 스킵={skipped_files}"
    )
    return {
        "processed": processed,
        "inserted": inserted,
        "updated": updated,
        "skipped_files": skipped_files,
    }


# ── Cowork (Claude Desktop App) 학습 흡수 ─────────────────────────────────
# ★ 사용자 박제 2026-05-25 — Cowork 채널 매 Q&A 자동 학습 (ERRORS [167])
#   Cowork 에는 VS Code Claude Code 의 hook 메커니즘이 없으므로 *데몬 5분 잡* 으로
#   주기 흡수. 거의 실시간 (최대 5분 지연).

# 도구 호출 마커 — 학습 가치 없음 (예: "(called Edit)", "(called mcp__...)")
_TOOL_CALL_ONLY = re.compile(r"^\s*\(called\s+[\w_:]+\)\s*$")
# [role] 마커 위치 탐지 — re.split 대신 finditer 로 더 robust 분할
_ROLE_MARKER = re.compile(r"\[(user|assistant|system)\]\s*")


def _split_cowork_messages(text: str) -> list[tuple[str, str]]:
    """텍스트의 [role] 마커 위치를 finditer 로 찾아 (role, body) 리스트 반환.

    re.split 보다 robust — 마커 사이의 body 길이 무관, 빈 줄 무관.
    도구 호출만 있는 assistant 메시지는 자동 제외.
    """
    messages: list[tuple[str, str]] = []
    matches = list(_ROLE_MARKER.finditer(text))
    if not matches:
        return messages
    for i, m in enumerate(matches):
        role = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # 도구 호출만 있는 assistant 메시지 제외
        if role == "assistant" and _TOOL_CALL_ONLY.match(body):
            continue
        if role in ("user", "assistant") and len(body) >= 10:
            messages.append((role, body))
    return messages


def _extract_qa_from_cowork_text(text: str) -> list[tuple[str, str]]:
    """Cowork text 형식 transcript ('[user] ...\\n[assistant] ...') 파싱.

    user 직후 *모든 assistant 메시지* 중 도구 호출만 있는 줄은 제외하고,
    실제 텍스트 응답 (가장 마지막의 substantive 답변) 만 추출.
    """
    pairs: list[tuple[str, str]] = []
    if not text or "[user]" not in text or "[assistant]" not in text:
        return pairs

    messages = _split_cowork_messages(text)

    # user → 그 user 직후 *모든 텍스트 assistant 답변 중 가장 긴 것* 매칭
    # (도구 호출 후 실제 답변이 가장 substantive)
    i = 0
    while i < len(messages):
        if messages[i][0] != "user":
            i += 1
            continue
        q = messages[i][1]
        j = i + 1
        best_a: str | None = None
        while j < len(messages) and messages[j][0] == "assistant":
            if best_a is None or len(messages[j][1]) > len(best_a):
                best_a = messages[j][1]
            j += 1
        if best_a is not None:
            q_clean = _normalize(q)
            # 질문 임계값 완화: 8자 이상 (예: "데몬 재시작 코드 주세요" 14자도 학습)
            if len(q_clean) >= 8 and len(best_a) >= 30:
                pairs.append((q, best_a))
        i = j if j > i else i + 1
    return pairs


def _extract_qa_from_cowork_jsonl(path: Path) -> list[tuple[str, str]]:
    """Cowork JSONL transcript 파싱 — VS Code Claude Code 와 동일 형식 가정."""
    # 동일 로직 재사용
    return _extract_qa_pairs(path)


def _extract_qa_from_cowork_json(path: Path) -> list[tuple[str, str]]:
    """Cowork JSON (단일 객체) transcript 파싱."""
    pairs: list[tuple[str, str]] = []
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return pairs

    # 후보 키: messages, conversation, history, transcript
    msgs = None
    for key in ("messages", "conversation", "history", "transcript"):
        v = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(v, list):
            msgs = v
            break
    if msgs is None and isinstance(obj, list):
        msgs = obj
    if not msgs:
        return pairs

    extracted: list[tuple[str, str]] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("type", "")
        content = m.get("content") or m.get("text", "")
        if isinstance(content, list):
            text = " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") in ("text", "")
            ).strip()
        else:
            text = str(content).strip()
        if role in ("user", "assistant") and len(text) > 20:
            extracted.append((role, text))

    i = 0
    while i < len(extracted) - 1:
        if extracted[i][0] == "user" and extracted[i + 1][0] == "assistant":
            q, a = extracted[i][1], extracted[i + 1][1]
            q_clean = _normalize(q)
            if len(q_clean) > 15 and len(a) > 30:
                pairs.append((q, a))
            i += 2
        else:
            i += 1
    return pairs


def _scan_cowork_transcript_files() -> list[Path]:
    """Cowork local-agent-mode-sessions 폴더에서 transcript 후보 파일 수집.

    탐지 대상: *.jsonl / *.json / messages.* / transcript.*
    제외: outputs/uploads/.auto-memory 폴더 + 명백히 transcript 아닌 파일.
    """
    if not _COWORK_BASE.exists():
        return []

    candidates: list[Path] = []
    for ext in ("*.jsonl", "*.json"):
        for p in _COWORK_BASE.rglob(ext):
            # 제외 폴더 체크
            if any(part in _COWORK_EXCLUDE_DIRS for part in p.parts):
                continue
            # 매우 작은 파일 (빈 transcript 의심) 제외
            try:
                if p.stat().st_size < 100:
                    continue
            except OSError:
                continue
            candidates.append(p)
    return sorted(set(candidates))


def ingest_cowork_sessions(force: bool = False) -> dict:
    """Cowork (Claude Desktop App) transcript → qa_entries 증분 저장.

    데몬 5분 잡으로 호출. mtime 기반 증분 — 변경 없으면 skip.

    Returns: {processed, inserted, updated, skipped_files, channel: "cowork"}
    """
    from shared import db as _db

    if not _COWORK_BASE.exists():
        log.debug(f"[QAStore/Cowork] 폴더 없음: {_COWORK_BASE}")
        return {
            "processed": 0, "inserted": 0, "updated": 0,
            "skipped_files": 0, "channel": "cowork",
            "note": "Cowork 폴더 미존재 (Claude Desktop App 미설치 또는 미사용)",
        }

    files = _scan_cowork_transcript_files()
    log.info(f"[QAStore/Cowork] 후보 파일 {len(files)}개 발견")

    with _db.get_db() as conn:
        _init_qa_tables(conn)
        processed_map: dict[str, float] = {
            r["session_file"]: r["file_mtime"]
            for r in conn.execute(
                "SELECT session_file, file_mtime FROM qa_ingested_sessions"
            ).fetchall()
        }

    processed = inserted = updated = skipped_files = 0

    for fp in files:
        try:
            mtime = fp.stat().st_mtime
        except OSError:
            continue

        # session_file 키는 Cowork 구분 prefix 추가 (VS Code 와 충돌 방지)
        key = f"cowork::{fp.relative_to(_COWORK_BASE)}"
        if not force and key in processed_map and abs(processed_map[key] - mtime) < 1:
            skipped_files += 1
            continue

        # 파일 형식별 추출
        try:
            if fp.suffix == ".jsonl":
                qa_pairs = _extract_qa_from_cowork_jsonl(fp)
            elif fp.suffix == ".json":
                qa_pairs = _extract_qa_from_cowork_json(fp)
            else:
                qa_pairs = _extract_qa_from_cowork_text(fp.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            log.debug(f"[QAStore/Cowork] {fp.name} 파싱 실패: {e}")
            qa_pairs = []

        session_id = f"cowork:{fp.parent.name}"
        n_ins = n_upd = 0
        for q, a in qa_pairs:
            status = upsert(q, a, source="cowork", session_id=session_id)
            if status == "inserted":
                n_ins += 1
            elif status == "updated":
                n_upd += 1

        _mark_ingested(key, mtime, len(qa_pairs))
        processed += 1
        inserted += n_ins
        updated += n_upd

    log.info(
        f"[QAStore/Cowork] 완료: 처리={processed} 신규={inserted} "
        f"업뎃={updated} 스킵={skipped_files}"
    )
    return {
        "processed": processed,
        "inserted": inserted,
        "updated": updated,
        "skipped_files": skipped_files,
        "channel": "cowork",
    }


# ── 통계 ───────────────────────────────────────────────────────────────────

def stats() -> dict:
    from shared import db as _db

    with _db.get_db() as conn:
        _init_qa_tables(conn)
        total = conn.execute("SELECT COUNT(*) FROM qa_entries").fetchone()[0]
        by_source = {
            r["source"]: r["cnt"]
            for r in conn.execute(
                "SELECT source, COUNT(*) as cnt FROM qa_entries GROUP BY source"
            ).fetchall()
        }
        top_hits = conn.execute(
            "SELECT question_raw, hit_count FROM qa_entries ORDER BY hit_count DESC LIMIT 5"
        ).fetchall()
        ingested = conn.execute(
            "SELECT COUNT(*) FROM qa_ingested_sessions"
        ).fetchone()[0]

    return {
        "total_qa": total,
        "by_source": by_source,
        "ingested_sessions": ingested,
        "top_hits": [
            {"q": r["question_raw"][:70], "hits": r["hit_count"]}
            for r in top_hits
        ],
    }


# ── job 진입점 (JARVIS04 스케줄러용) ────────────────────────────────────────

def job_ingest_sessions() -> None:
    """매일 새 Claude Code 세션 증분 학습."""
    result = ingest_sessions()
    try:
        from shared.bus import publish
        publish("qa_store.ingested", result)
    except Exception:
        pass
    log.info(f"[QAStore] 잡 완료: {result}")


def job_ingest_cowork_sessions() -> None:
    """5분 간격 Cowork (Claude Desktop App) 대화 학습 흡수.

    ★ 사용자 박제 2026-05-25 (ERRORS [167]) — Cowork 채널 매 Q&A 자동 누적.
    """
    result = ingest_cowork_sessions()
    try:
        from shared.bus import publish
        publish("qa_store.cowork_ingested", result)
    except Exception:
        pass
    # 신규/업뎃이 있을 때만 INFO, 0건이면 DEBUG (5분 간격이라 로그 폭주 방지)
    if result.get("inserted", 0) + result.get("updated", 0) > 0:
        log.info(f"[QAStore/Cowork] 잡 완료: {result}")
    else:
        log.debug(f"[QAStore/Cowork] 잡 완료 (변경 없음): {result}")


def vector_search(query: str, top_k: int = 5) -> list[dict]:
    """시맨틱 벡터 검색 — ChromaDB 5중 검증 통과 후보 반환.

    FTS5 키워드 검색과 달리 의미 유사도 기반.
    Returns: list of dicts (vector_store.search_vector 형식)
    """
    try:
        from JARVIS07_GUARDIAN.vector_store import search_vector
        return search_vector(query, top_k=top_k)
    except Exception as e:
        log.debug(f"[QAStore] 벡터 검색 실패 (fallback to FTS only): {e}")
        return []


__all__ = [
    "search", "vector_search", "upsert", "stats",
    "ingest_sessions", "job_ingest_sessions",
    "ingest_cowork_sessions", "job_ingest_cowork_sessions",
]
