"""JARVIS07_GUARDIAN/vector_store.py — ChromaDB 벡터 스토어 (시맨틱 Q&A 검색).

기능:
  - ChromaDB 영구 컬렉션 (paraphrase-multilingual-MiniLM-L12-v2 한국어 지원)
  - upsert_vector(): Q&A 레코드 → 벡터 임베딩 저장
  - search_vector(): 5중 검증 시맨틱 검색
  - backfill_from_db(): 기존 qa_entries 전수 백필

5중 검증 레이어 (search_vector 내부):
  L1. 유사도 임계값 (cosine similarity ≥ 0.55)
  L2. 소스 검증 (claude/cowork 만 허용)
  L3. 답변 품질 (길이 ≥ 50자)
  L4. ★ 키워드 겹침 검증 (query ∩ document ≥ 20%) — 짧은 한국어 noise 차단 핵심
  L5. 최종 신뢰도 임계값 (≥ 0.62)
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path

log = logging.getLogger("jarvis.vector_store")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CHROMA_DIR      = Path(__file__).resolve().parent / "chroma_db"
# v2: paraphrase-multilingual-MiniLM-L12-v2 (한국어 지원) — 컬렉션명 변경으로 자동 재구축
_COLLECTION_NAME  = "jarvis_qa_vectors_v2"
# ★ 임베딩 모델명 단일 진입점 — shared.embeddings.EMBED_MODEL_NAME (미래 bge-m3 = 한 곳만 교체)
#   값은 동일 → ChromaDB EF 재계산·재인덱싱 불필요 (기존 v2 컬렉션 그대로 사용)
from shared.embeddings import EMBED_MODEL_NAME as _EMBED_MODEL_NAME

# 5중 검증 임계값 (다국어 모델 기준)
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

_ANSWER_META_LIMIT = 2000     # ChromaDB metadata 답변 미리보기 한계

_lock = threading.Lock()
_client = None
_collection = None


def _get_collection():
    """ChromaDB 컬렉션 싱글톤 (lazy init, thread-safe)."""
    global _client, _collection
    if _collection is not None:
        return _collection
    with _lock:
        if _collection is not None:
            return _collection
        try:
            import chromadb
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
            _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
            ef = SentenceTransformerEmbeddingFunction(
                model_name=_EMBED_MODEL_NAME,
                device="cpu",
                normalize_embeddings=True,
            )
            _collection = _client.get_or_create_collection(
                name=_COLLECTION_NAME,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            log.info(
                f"[VectorStore] 준비 완료 (count={_collection.count()}, "
                f"path={_CHROMA_DIR})"
            )
            return _collection
        except Exception as e:
            log.warning(f"[VectorStore] ChromaDB 초기화 실패 — 벡터 검색 비활성: {e}")
            return None


def upsert_vector(
    qa_id: int,
    question_hash: str,
    question_norm: str,
    answer: str,
    source: str,
    hit_count: int = 1,
    confidence: float = 1.0,
) -> bool:
    """Q&A 레코드 → 벡터 임베딩 upsert.

    Returns: True if successful.
    """
    if not question_norm or len(question_norm.strip()) < 5:
        return False
    if source not in _L2_SOURCES:
        return False  # L2 선제 필터: 노이즈 소스 임베딩 안 함

    col = _get_collection()
    if col is None:
        return False

    try:
        col.upsert(
            ids=[question_hash],
            documents=[question_norm],
            metadatas=[{
                "qa_id":          int(qa_id),
                "source":         str(source),
                "hit_count":      int(hit_count),
                "confidence":     float(confidence),
                "answer_preview": (answer or "")[:_ANSWER_META_LIMIT],
            }],
        )
        return True
    except Exception as e:
        log.warning(f"[VectorStore] upsert 실패 qa_id={qa_id}: {e}")
        return False


def _fetch_full_answer(qa_id: int) -> str:
    """SQLite에서 전체 답변 조회 (preview 가 잘린 경우 사용)."""
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            row = conn.execute(
                "SELECT answer FROM qa_entries WHERE id = ?", (qa_id,)
            ).fetchone()
            return row["answer"] if row else ""
    except Exception as e:
        log.debug(f"[VectorStore] 전체 답변 조회 실패 qa_id={qa_id}: {e}")
        return ""


def _kw_set(text: str) -> set[str]:
    """의미 있는 키워드 추출 (2글자 이상 한글/영문/숫자)."""
    return set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", text.lower()))


def search_vector(query: str, top_k: int = 5) -> list[dict]:
    """시맨틱 유사도 검색 → 5중 검증 통과 후보 반환.

    ChromaDB cosine 공간: distance = 1 - cosine_similarity → similarity = 1 - distance

    5중 검증:
      L1. cosine similarity ≥ _L1_SIM_MIN
      L2. source in _L2_SOURCES
      L3. answer_len ≥ _L3_ANSWER_MIN
      L4. ★ keyword overlap (query ∩ document) ≥ _L4_OVERLAP_MIN  — 핵심 noise 차단
      L5. final confidence ≥ _L5_CONF_MIN

    Returns: list of dicts (qa_id, question_hash, similarity, confidence, source, hit_count, answer)
    """
    col = _get_collection()
    if col is None:
        return []

    count = col.count()
    if count == 0:
        return []

    try:
        results = col.query(
            query_texts=[query],
            n_results=min(top_k, count),
            include=["distances", "metadatas", "documents"],
        )
    except Exception as e:
        log.debug(f"[VectorStore] 쿼리 실패: {e}")
        return []

    ids       = results.get("ids",       [[]])[0]
    distances = results.get("distances", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    documents = results.get("documents", [[]])[0]  # = stored question_norm

    if not ids:
        return []

    # L4 키워드 겹침: 쿼리 단어 집합 (정규화)
    q_words = _kw_set(query)

    candidates = []
    for qhash, distance, meta, doc in zip(ids, distances, metadatas, documents):
        similarity = max(0.0, 1.0 - float(distance))

        # L1: 유사도 임계값
        if similarity < _L1_SIM_MIN:
            log.debug(f"[VectorStore] L1 차단: sim={similarity:.3f}")
            continue

        source = str(meta.get("source", ""))
        # L2: 소스 검증
        if source not in _L2_SOURCES:
            log.debug(f"[VectorStore] L2 차단: source={source}")
            continue

        answer_preview = str(meta.get("answer_preview", ""))
        # L3: 답변 품질
        if len(answer_preview) < _L3_ANSWER_MIN:
            log.debug(f"[VectorStore] L3 차단: answer_len={len(answer_preview)}")
            continue

        # L4: ★ 키워드 겹침 검증 — 한국어 짧은 문장 false positive 차단
        doc_words = _kw_set(doc or "")
        if q_words and doc_words:
            overlap = len(q_words & doc_words) / max(len(q_words), 1)
        elif not q_words:
            overlap = 1.0  # 키워드 없는 쿼리는 통과 (희귀 케이스)
        else:
            overlap = 0.0
        if overlap < _L4_OVERLAP_MIN:
            log.debug(
                f"[VectorStore] L4 차단: overlap={overlap:.2f} "
                f"(q={list(q_words)[:4]} doc={list(doc_words)[:4]})"
            )
            continue

        hit_count       = int(meta.get("hit_count", 1))
        base_confidence = float(meta.get("confidence", 1.0))
        qa_id           = int(meta.get("qa_id", 0))

        # L5 사전 계산: 신뢰도 합산 (유사도 × 기본신뢰도 + hit_count 보너스)
        hit_bonus        = min(_L4_BONUS_CAP, hit_count * _L4_HIT_BONUS)
        final_confidence = min(1.0, similarity * base_confidence + hit_bonus)

        # L5: 최종 임계값
        if final_confidence < _L5_CONF_MIN:
            log.debug(
                f"[VectorStore] L5 차단: conf={final_confidence:.3f} "
                f"(sim={similarity:.3f} hit={hit_count})"
            )
            continue

        # 전체 답변 조회 (preview 가 잘린 경우 SQLite 에서 가져옴)
        if len(answer_preview) >= _ANSWER_META_LIMIT - 10:
            answer = _fetch_full_answer(qa_id) or answer_preview
        else:
            answer = answer_preview

        candidates.append({
            "qa_id":         qa_id,
            "question_hash": qhash,
            "similarity":    round(similarity, 4),
            "confidence":    round(final_confidence, 4),
            "source":        source,
            "hit_count":     hit_count,
            "overlap":       round(overlap, 3),
            "answer":        answer,
        })

    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    log.info(
        f"[VectorStore] 검색 완료: 후보={len(candidates)}/{len(ids)} "
        f"query={query[:40]!r}"
    )
    return candidates


def backfill_from_db(batch_size: int = 500) -> dict:
    """기존 qa_entries → ChromaDB 전수 백필.

    이미 존재하는 question_hash 는 upsert (덮어쓰기, 안전).
    claude/cowork 소스만 임베딩 (노이즈 소스 제외).
    Returns: {total, success, skipped, failed}
    """
    from shared import db as _db

    col = _get_collection()
    if col is None:
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0,
                "error": "ChromaDB 비활성"}

    try:
        with _db.get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, question_hash, question_norm, answer,
                       source, hit_count, confidence
                FROM qa_entries
                WHERE source IN ('claude', 'cowork')
                ORDER BY id
                """
            ).fetchall()
    except Exception as e:
        log.error(f"[VectorStore] DB 조회 실패: {e}")
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0, "error": str(e)}

    total   = len(rows)
    success = skipped = failed = 0
    log.info(f"[VectorStore] 백필 시작: {total}개 레코드")

    for i in range(0, total, batch_size):
        batch       = rows[i:i + batch_size]
        batch_ids   = []
        batch_docs  = []
        batch_metas = []

        for row in batch:
            norm = (row["question_norm"] or "").strip()
            if len(norm) < 5:
                skipped += 1
                continue
            batch_ids.append(row["question_hash"])
            batch_docs.append(norm)
            batch_metas.append({
                "qa_id":          int(row["id"]),
                "source":         str(row["source"]),
                "hit_count":      int(row["hit_count"]),
                "confidence":     float(row["confidence"]),
                "answer_preview": (row["answer"] or "")[:_ANSWER_META_LIMIT],
            })

        if not batch_ids:
            continue

        try:
            col.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            success += len(batch_ids)
            log.info(f"[VectorStore] 백필 진행: {i + len(batch)}/{total}")
        except Exception as e:
            log.warning(f"[VectorStore] 배치 upsert 실패: {e}")
            failed += len(batch_ids)

    log.info(
        f"[VectorStore] 백필 완료: total={total} "
        f"success={success} skip={skipped} fail={failed}"
    )
    return {"total": total, "success": success, "skipped": skipped, "failed": failed}


def vector_stats() -> dict:
    """ChromaDB 벡터 스토어 현황 반환."""
    col = _get_collection()
    if col is None:
        return {"available": False, "count": 0}
    try:
        count = col.count()
        return {
            "available":  True,
            "count":      count,
            "chroma_dir": str(_CHROMA_DIR),
            "collection": _COLLECTION_NAME,
            "thresholds": {
                "L1_similarity_min": _L1_SIM_MIN,
                "L5_confidence_min": _L5_CONF_MIN,
            },
        }
    except Exception as e:
        return {"available": False, "count": 0, "error": str(e)}


def job_build_vector_index() -> None:
    """벡터 인덱스 전수 백필 잡 진입점 (JARVIS04_SCHEDULER 에서 호출)."""
    result = backfill_from_db()
    log.info(f"[VectorStore] 백필 잡 완료: {result}")
    try:
        from shared.bus import publish
        publish("vector_store.backfill_complete", result)
    except Exception:
        pass


__all__ = [
    "upsert_vector", "search_vector", "backfill_from_db",
    "vector_stats", "job_build_vector_index",
]
