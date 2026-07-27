"""JARVIS07_GUARDIAN/vector_store.py — 시맨틱 Q&A 검색 (SQLite BLOB + numpy).

★ 2026-07-27 ChromaDB 제거 (ERRORS [537]) — **공개 API 는 그대로**.
  `upsert_vector` / `search_vector` / `backfill_from_db` / `vector_stats` /
  `job_build_vector_index` 시그니처·반환 형식 불변. 호출자(qa_resolver·잡)는 수정 0.

왜 바꿨나 — 실측 3가지
  ① **용량**: chroma_db 164MB 인데 순수 벡터는 18.7MB. 팽창의 정체는 벡터가 아니라
     *텍스트 색인* 이었다 — trigram 전문검색 52.8MB(우리가 안 쓰는 기능인데 끌 수 없음) +
     `answer_preview` 메타데이터 색인 50.1MB(문자열 전체를 색인 키로 복사).
  ② **고아 벡터**: 컬렉션 v1(3,860) + v2(9,007) = 12,867 인데 `qa_entries` 는 9,042행.
     원본이 지워져도 벡터가 남았다. 세대가 섞이면 유사도 점수가 서로 비교 불가라
     **오류도 경고도 없이 결과만 조용히 나빠진다.**
  ③ **속도는 이유가 아니다**: 9,042개 브루트포스 실측 **0.49ms**. ANN(HNSW) 인덱스가
     필요한 규모가 아니다(보통 10만+ 부터). 얻는 것 없이 의존성만 졌던 셈.

설계 — 3원칙
  ① 단일 진입점: 벡터가 `qa_entries.embedding` 컬럼에 산다. 원본 텍스트와 **같은 행·같은 파일**.
     별도 저장소가 없으므로 "어느 쪽이 진실인가" 질문 자체가 사라진다.
  ② 사본 금지 / 고아 불가: `DELETE FROM qa_entries` 하나로 벡터도 함께 사라진다.
     ★ 이게 ChromaDB 대비 가장 큰 구조적 이득 — 고아가 **생길 수 없다**.
     인덱스는 DB 에서 *파생* 하고 (행수, MAX(updated_at), 모델명) 으로 변경을 감지해 자동 재적재.
  ③ 모든 경로: qa_resolver 검색·저장, 백필 잡 전부 이 모듈만 거친다.

저장 형식
  float32 × EMBED_DIM 을 **L2 정규화해서** BLOB 로 저장 → 검색 시 내적 = 코사인 유사도.
  ★ 정규화 저장이 전제다. 임계값(_L1_SIM_MIN 0.55 / _L5_CONF_MIN 0.62)이 코사인 기준으로
    튜닝돼 있어서, 정규화를 빠뜨리면 **예외 없이 점수만 틀어진다**.
  `embedding_model` 컬럼에 모델명을 함께 적어 모델 교체를 감지한다(차원·의미공간이 바뀌면 재색인).

5중 검증 레이어는 **종전 그대로 보존** (아래 상수·순서 동일):
  L1 유사도 → L2 소스 → L3 답변 길이 → L4 키워드 겹침 → L5 최종 신뢰도
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger("jarvis.vector_store")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ★ 임베딩 모델명·차원 단일 진입점 — shared.embeddings 가 소유 (미래 bge-m3 = 한 곳만 교체)
from shared.embeddings import EMBED_DIM as _EMBED_DIM            # noqa: E402
from shared.embeddings import EMBED_MODEL_NAME as _EMBED_MODEL_NAME  # noqa: E402

# 5중 검증 임계값 (다국어 모델 기준) — ChromaDB 시절 값 그대로 유지.
# 주의: paraphrase-multilingual-MiniLM-L12-v2 는 짧은 한국어 명령형 문장들을
# 비슷한 임베딩 공간에 뭉쳐두어 L1만으로는 false positive 차단 불가.
# L4 키워드 겹침 검증이 핵심 누수 방지 레이어.
_L1_SIM_MIN     = 0.55        # L1: cosine similarity 최소
_L2_SOURCES     = {"claude", "cowork"}  # L2: 허용 소스
_L3_ANSWER_MIN  = 50          # L3: 답변 최소 길이 (chars)
_L4_OVERLAP_MIN = 0.20        # L4: ★ 키워드 겹침 최소 비율 (query 기준)
_L4_HIT_BONUS   = 0.01        # L4: hit_count 당 신뢰도 보너스
_L4_BONUS_CAP   = 0.10        # L4: 보너스 상한
_L5_CONF_MIN    = 0.62        # L5: 최종 신뢰도 임계값 (L4 overlap 이 핵심 차단 레이어)

# ★ L0 (암묵적 레이어) — 유사도 상위 몇 개까지만 후보로 볼 것인가.
#   ChromaDB 시절 `n_results` 가 하던 역할. 이 모델은 무관 문장에도 0.92 를 주므로
#   **이 게이트가 없으면 L1~L5 만으로 노이즈를 못 막는다**(실증: ERRORS [537]).
#   top_k 와 분리해 고정한다 — 붙이면 같은 질의가 top_k 에 따라 다른 답을 낸다.
_POOL = 10

_lock = threading.Lock()
# 인덱스 캐시 — DB 에서 *파생*. 사본을 파일로 굳히지 않는다(② 동적 설계).
_index: dict = {"sig": None, "mat": None, "meta": []}


# ── 저장 형식 ────────────────────────────────────────────────────
def _to_blob(vec) -> bytes:
    """float32 L2 정규화 → BLOB. 검색 시 내적이 곧 코사인이 되게 한다."""
    v = np.asarray(vec, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    if n > 1e-9:
        v = v / n
    return v.astype(np.float32).tobytes()


def _from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float32)


def _db_signature(conn) -> tuple:
    """인덱스 무효화 신호 — (임베딩 보유 행수, 최신 갱신시각, 모델명).

    ★ 파일 mtime 이 없는 DB 테이블이라 *내용에서* 변경을 파생한다.
      모델명을 포함시키는 이유: 모델을 바꾸면 차원·의미공간이 달라져 옛 벡터가 무의미해진다.
    """
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(updated_at),''), COALESCE(MAX(embedding_model),'') "
        "FROM qa_entries WHERE embedding IS NOT NULL"
    ).fetchone()
    return tuple(row) if row else (0, "", "")


def _load_index():
    """DB → (행렬, 메타) 파생. 시그니처가 그대로면 캐시 재사용."""
    from shared import db as _db
    try:
        with _db.get_db() as conn:
            sig = _db_signature(conn)
            with _lock:
                if _index["sig"] == sig and _index["mat"] is not None:
                    return _index["mat"], _index["meta"]
            rows = conn.execute(
                """SELECT id, question_hash, question_norm, answer, source,
                          hit_count, confidence, embedding
                   FROM qa_entries
                   WHERE embedding IS NOT NULL AND source IN ('claude','cowork')"""
            ).fetchall()
    except Exception as e:  # noqa: BLE001 — 검색 실패가 호출자를 막지 않게
        log.warning(f"[VectorStore] 인덱스 로드 실패: {e}")
        return None, []

    if not rows:
        with _lock:
            _index.update(sig=sig, mat=None, meta=[])
        return None, []

    mats, meta = [], []
    for r in rows:
        try:
            v = _from_blob(r["embedding"])
            if v.size != _EMBED_DIM:      # 모델 교체 등으로 차원이 다른 옛 벡터는 건너뛴다
                continue
        except Exception:  # noqa: BLE001
            continue
        mats.append(v)
        meta.append({
            "qa_id": int(r["id"]), "question_hash": r["question_hash"],
            "doc": r["question_norm"] or "", "answer": r["answer"] or "",
            "source": r["source"] or "", "hit_count": int(r["hit_count"] or 1),
            "confidence": float(r["confidence"] or 1.0),
        })
    mat = np.vstack(mats).astype(np.float32) if mats else None
    with _lock:
        _index.update(sig=sig, mat=mat, meta=meta)
    log.info(f"[VectorStore] 인덱스 적재 {len(meta):,}건 (DB 파생)")
    return mat, meta


# ── 공개 API (시그니처 불변) ──────────────────────────────────────
def upsert_vector(
    qa_id: int,
    question_hash: str,
    question_norm: str,
    answer: str,
    source: str,
    hit_count: int = 1,
    confidence: float = 1.0,
) -> bool:
    """Q&A 레코드 → 벡터 임베딩 upsert (해당 행의 `embedding` 컬럼에 기록).

    Returns: True if successful.
    """
    if not question_norm or len(question_norm.strip()) < 5:
        return False
    if source not in _L2_SOURCES:
        return False  # L2 선제 필터: 노이즈 소스 임베딩 안 함

    try:
        from shared import db as _db
        from shared import embeddings as _emb
        blob = _to_blob(_emb.embed_text(question_norm[:2000]))
        with _db.get_db() as conn:
            conn.execute(
                "UPDATE qa_entries SET embedding=?, embedding_model=? WHERE id=?",
                (blob, _EMBED_MODEL_NAME, int(qa_id)),
            )
            conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning(f"[VectorStore] upsert 실패 qa_id={qa_id}: {e}")
        return False


def _kw_set(text: str) -> set[str]:
    """의미 있는 키워드 추출 (2글자 이상 한글/영문/숫자)."""
    return set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", (text or "").lower()))


def search_vector(query: str, top_k: int = 5) -> list[dict]:
    """시맨틱 유사도 검색 → 5중 검증 통과 후보 반환.

    벡터는 L2 정규화 저장이므로 **내적 = cosine similarity**.

    5중 검증 (종전과 동일):
      L1. cosine similarity ≥ _L1_SIM_MIN
      L2. source in _L2_SOURCES
      L3. answer_len ≥ _L3_ANSWER_MIN
      L4. ★ keyword overlap (query ∩ document) ≥ _L4_OVERLAP_MIN  — 핵심 noise 차단
      L5. final confidence ≥ _L5_CONF_MIN

    Returns: list of dicts (qa_id, question_hash, similarity, confidence, source, hit_count, answer)
    """
    q = (query or "").strip()
    if not q:
        return []
    mat, meta = _load_index()
    if mat is None or not len(meta):
        return []

    try:
        from shared import embeddings as _emb
        qv = np.asarray(_emb.embed_text(q[:2000]), dtype=np.float32).ravel()
        n = float(np.linalg.norm(qv))
        if n > 1e-9:
            qv = qv / n
        sims = mat @ qv                       # 정규화돼 있으므로 내적 = 코사인
    except Exception as e:  # noqa: BLE001
        log.debug(f"[VectorStore] 쿼리 임베딩 실패: {e}")
        return []

    # ★ 후보 풀 = 유사도 상위 _POOL 개 **고정** (ERRORS [537] — 자체 검증이 두 번 잡아냈다)
    #
    #   1차 시도: `k = top_k * 8` → **같은 질의도 top_k 에 따라 결과가 달랐다**
    #            (top_k=10 은 찾고 top_k=1 은 0건). 단조성이 깨진 결함.
    #   2차 시도: 전수 스캔(자르지 않음) → **무관 질의가 통과**했다.
    #            "오늘 점심 뭐 먹지" 가 2건, "축구 경기 결과" 가 2건 매칭.
    #
    #   ★ 왜 전수 스캔이 위험한가 (이 모델의 성질 — 실측):
    #     `paraphrase-multilingual-MiniLM-L12-v2` 는 **무관한 한국어 짧은 문장에도
    #     유사도 0.92~0.94** 를 준다("오늘 점심 뭐 먹지" ↔ "wontfix 마킹이 뭐야" = 0.925).
    #     즉 L1(0.55)은 사실상 아무것도 못 거른다. 9,042건을 전부 훑으면 그중 하나쯤은
    #     '오늘' 같은 흔한 단어가 겹쳐 L4(0.20)까지 통과해 버린다.
    #     **상위 N 게이트가 실제 방어선이었다** — 임계값들이 그 전제 위에서 튜닝됐다.
    #
    #   → 풀을 고정 크기로 둔다: 단조성 확보(top_k 무관) + 종전 보정 체계 유지.
    #     ChromaDB 시절 `n_results=top_k` 의 역할을 대신한다.
    pool = min(len(meta), _POOL)
    order = np.argpartition(-sims, pool - 1)[:pool] if pool < len(sims) else np.arange(len(sims))
    order = order[np.argsort(-sims[order])]

    q_words = _kw_set(q)
    candidates: list[dict] = []
    for i in order:
        similarity = max(0.0, float(sims[i]))
        if similarity < _L1_SIM_MIN:          # L1 (내림차순이라 이후는 볼 필요 없음)
            break
        m = meta[i]

        if m["source"] not in _L2_SOURCES:    # L2
            continue
        if len(m["answer"]) < _L3_ANSWER_MIN:  # L3
            continue

        doc_words = _kw_set(m["doc"])          # L4 — 한국어 짧은 문장 false positive 차단
        if q_words and doc_words:
            overlap = len(q_words & doc_words) / max(len(q_words), 1)
        elif not q_words:
            overlap = 1.0                      # 키워드 없는 쿼리는 통과 (희귀 케이스)
        else:
            overlap = 0.0
        if overlap < _L4_OVERLAP_MIN:
            continue

        hit_bonus = min(_L4_BONUS_CAP, m["hit_count"] * _L4_HIT_BONUS)
        final_confidence = min(1.0, similarity * m["confidence"] + hit_bonus)
        if final_confidence < _L5_CONF_MIN:    # L5
            continue

        candidates.append({
            "qa_id":         m["qa_id"],
            "question_hash": m["question_hash"],
            "similarity":    round(similarity, 4),
            "confidence":    round(final_confidence, 4),
            "source":        m["source"],
            "hit_count":     m["hit_count"],
            "overlap":       round(overlap, 3),
            "answer":        m["answer"],
        })
        if len(candidates) >= top_k:
            break

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    log.info(f"[VectorStore] 검색 완료: 후보={len(candidates)} query={q[:40]!r}")
    return candidates


def backfill_from_db(batch_size: int = 500) -> dict:
    """`qa_entries` 중 임베딩이 없거나 모델이 바뀐 행을 채운다.

    ★ 종전엔 "SQLite → ChromaDB 로 복사" 였다. 이제 벡터가 같은 행에 살므로
      *복사가 아니라 결측 보충* 이다 — 옮길 곳이 없으니 드리프트도 없다.
    Returns: {total, success, skipped, failed}
    """
    from shared import db as _db
    from shared import embeddings as _emb

    out = {"total": 0, "success": 0, "skipped": 0, "failed": 0}
    try:
        with _db.get_db() as conn:
            rows = conn.execute(
                """SELECT id, question_norm FROM qa_entries
                   WHERE source IN ('claude','cowork')
                     AND question_norm IS NOT NULL AND LENGTH(question_norm) >= 5
                     AND (embedding IS NULL OR COALESCE(embedding_model,'') != ?)""",
                (_EMBED_MODEL_NAME,),
            ).fetchall()
            out["total"] = len(rows)
            for i in range(0, len(rows), batch_size):
                chunk = rows[i:i + batch_size]
                try:
                    vecs = _emb.embed_texts([r["question_norm"][:2000] for r in chunk])
                    conn.executemany(
                        "UPDATE qa_entries SET embedding=?, embedding_model=? WHERE id=?",
                        [(_to_blob(vecs[j]), _EMBED_MODEL_NAME, chunk[j]["id"])
                         for j in range(len(chunk))],
                    )
                    conn.commit()
                    out["success"] += len(chunk)
                except Exception as e:  # noqa: BLE001
                    out["failed"] += len(chunk)
                    log.warning(f"[VectorStore] 백필 배치 실패: {e}")
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
        log.warning(f"[VectorStore] 백필 실패: {e}")
    log.info(f"[VectorStore] 백필: {out}")
    return out


def vector_stats() -> dict:
    """벡터 스토어 현황."""
    from shared import db as _db
    try:
        with _db.get_db() as conn:
            n, sz = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(embedding)),0) "
                "FROM qa_entries WHERE embedding IS NOT NULL"
            ).fetchone()
        return {
            "available":  True,
            "count":      int(n),
            "backend":    "sqlite-blob+numpy",
            "store":      f"{_db.DB_PATH}::qa_entries.embedding",
            "bytes":      int(sz),
            "model":      _EMBED_MODEL_NAME,
            "dim":        _EMBED_DIM,
            "thresholds": {
                "L1_similarity_min": _L1_SIM_MIN,
                "L5_confidence_min": _L5_CONF_MIN,
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "count": 0, "error": str(e)}


def job_build_vector_index() -> None:
    """벡터 인덱스 백필 잡 진입점 (JARVIS04_SCHEDULER 에서 호출)."""
    result = backfill_from_db()
    log.info(f"[VectorStore] 백필 잡 완료: {result}")
    try:
        from shared.bus import publish
        publish("vector_store.backfill_complete", result)
    except Exception:  # noqa: BLE001
        pass


def selfcheck() -> list[str]:
    """★ 검색이 *실제로 동작하는지* 확인 (존재가 아니라 동작으로).

    벡터 검색 실패는 예외를 안 던지고 **결과 품질만 조용히 나빠진다** — 그래서 필요하다.
    """
    issues: list[str] = []
    try:
        st = vector_stats()
        if not st.get("available"):
            issues.append(f"[V0] 스토어 접근 불가: {st.get('error')}")
            return issues
        if st["count"] == 0:
            issues.append("[V1] 임베딩 보유 행 0 — backfill_from_db() 필요")
            return issues
        mat, meta = _load_index()
        if mat is None:
            issues.append("[V2] 인덱스 적재 실패")
            return issues
        if mat.shape[1] != _EMBED_DIM:
            issues.append(f"[V3] 차원 불일치 {mat.shape[1]} != {_EMBED_DIM}")
        norms = np.linalg.norm(mat, axis=1)
        if not np.allclose(norms, 1.0, atol=1e-3):
            issues.append(f"[V4] 정규화 안 된 벡터 존재 (norm {norms.min():.3f}~{norms.max():.3f})"
                          " — 임계값이 코사인 전제다")
        probe = (meta[0]["doc"] or "")[:150]
        if probe and not search_vector(probe, top_k=1):
            issues.append("[V5] 자기 문서 질의에도 0건 — 임계값 과다 또는 검색 경로 파손")
    except Exception as e:  # noqa: BLE001
        issues.append(f"[V0] selfcheck 실패: {type(e).__name__}: {e}")
    return issues


__all__ = [
    "upsert_vector", "search_vector", "backfill_from_db",
    "vector_stats", "job_build_vector_index", "selfcheck",
]
