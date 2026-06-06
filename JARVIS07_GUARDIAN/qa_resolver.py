"""JARVIS07_GUARDIAN/qa_resolver.py — 3-tier Q&A 자가 해결 엔진.

Tier 1:   로컬 FTS5 캐시 (hit_count ≥ 3, 정확/overlap 게이트)
Tier 1.5: ChromaDB 시맨틱 벡터 검색 (5중 검증: L1~L5)
Tier 2:   Claude API fallback (해결 후 학습 누적)
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("jarvis.qa_resolver")

_HIT_COUNT_THRESHOLD = 3   # 캐시 직접 반환 임계값

# ★ 사용자 박제 2026-05-25 (ERRORS [161]) — 캐시 오매칭 차단 게이트
#   기존 결함: FTS5 OR 검색이 단어 1개만 매칭되어도 hit_count≥3 + conf=1.00 으로 반환
#   → "자체학습 시스템" 질문이 "trends 수집" 답변과 매칭되어 Claude 응답 차단 사고.
#   새 게이트: 정규화 hash 정확 일치 OR (공통 단어 비율 ≥ MIN_OVERLAP_RATIO AND
#             FTS bm25 |score| ≥ MIN_FTS_SCORE_ABS). 둘 다 통과 못 하면 캐시 미사용.
_MIN_OVERLAP_RATIO = 0.6       # 공통 단어 비율 최소 (질문 단어 기준)
_MIN_FTS_SCORE_ABS = 2.0       # FTS5 bm25 score 절대값 최소
_MIN_CACHE_CONFIDENCE = 0.85   # 캐시 답변 최종 신뢰도 임계값
_MIN_QUESTION_WORDS = 2        # 최소 의미 단어 수 (이 미만은 캐시 사용 안 함)


def _word_set(text: str) -> set[str]:
    """질문에서 의미 있는 단어 추출 (2글자 이상 한글/영문/숫자)."""
    return set(re.findall(r"[가-힣a-zA-Z0-9]{2,}", text.lower()))


# ── Tier 1: 로컬 캐시 ──────────────────────────────────────────────────────

def _local_cache_resolve(question: str) -> dict | None:
    """FTS5 검색 → 다중 게이트 통과 시만 캐시 답변 반환.

    게이트 (모두 통과해야 resolved=True):
      1. hit_count ≥ _HIT_COUNT_THRESHOLD
      2. 정규화 hash 정확 일치 OR (공통 단어 비율 ≥ _MIN_OVERLAP_RATIO
                                   AND FTS |score| ≥ _MIN_FTS_SCORE_ABS)
      3. 최종 confidence ≥ _MIN_CACHE_CONFIDENCE

    노이즈 차단: 질문 의미 단어가 _MIN_QUESTION_WORDS 미만이면 캐시 미사용.
    """
    from JARVIS07_GUARDIAN.qa_store import _hash, _normalize, search

    norm_q = _normalize(question)
    hash_q = _hash(norm_q)
    q_words = _word_set(norm_q)

    # 게이트 0: 너무 짧은 질문 (예: "안녕", "ok") 은 캐시 사용 안 함
    if len(q_words) < _MIN_QUESTION_WORDS:
        return None

    results = search(question, top_k=5)
    if not results:
        return None

    # 각 후보에 정규화·overlap·exact 계산 부착
    for r in results:
        r_norm = _normalize(r.get("question_raw", ""))
        r["_norm"] = r_norm
        r["_hash"] = _hash(r_norm)
        r["_words"] = _word_set(r_norm)
        r["_overlap"] = (
            len(q_words & r["_words"]) / len(q_words) if q_words else 0.0
        )
        r["_exact"] = (r["_hash"] == hash_q)

    # 정렬: 정확 일치 > overlap > hit_count > FTS score 절대값
    results.sort(
        key=lambda r: (
            r["_exact"],
            r["_overlap"],
            r.get("hit_count", 0),
            -abs(r.get("score", 0) or 0),
        ),
        reverse=True,
    )
    best = results[0]

    hit = best.get("hit_count", 0)
    score_abs = abs(best.get("score", 0) or 0)
    overlap = best["_overlap"]

    # 게이트 1: hit_count
    if hit < _HIT_COUNT_THRESHOLD:
        return {
            "resolved": False, "answer": "", "source": "local_cache",
            "confidence": 0.0, "similar_qa": results,
        }

    # 게이트 2: 정확 일치 OR (overlap + FTS score 둘 다 통과)
    if best["_exact"]:
        confidence = min(1.0, best.get("confidence", 1.0))
    elif overlap >= _MIN_OVERLAP_RATIO and score_abs >= _MIN_FTS_SCORE_ABS:
        confidence = min(1.0, best.get("confidence", 1.0) * overlap)
    else:
        # 게이트 통과 못 함 → 캐시 사용 안 함 (Claude 정상 응답으로 통과)
        log.debug(
            f"[QAResolver] 캐시 게이트 차단: overlap={overlap:.2f} "
            f"score_abs={score_abs:.2f} exact={best['_exact']}"
        )
        return {
            "resolved": False, "answer": "", "source": "local_cache",
            "confidence": 0.0, "similar_qa": results,
        }

    # 게이트 3: 최종 confidence 임계값
    if confidence < _MIN_CACHE_CONFIDENCE:
        return {
            "resolved": False, "answer": "", "source": "local_cache",
            "confidence": confidence, "similar_qa": results,
        }

    return {
        "resolved": True,
        "answer": best["answer"],
        "source": "local_cache",
        "confidence": confidence,
        "similar_qa": results,
    }


# ── 메인 resolve ───────────────────────────────────────────────────────────

def resolve(
    question: str,
    fast: bool = False,
) -> dict[str, Any]:
    """2-tier 자가 해결.

    Args:
        question: 사용자 질문 원문
        fast: True면 hook 경로 (짧은 timeout)

    Returns:
        {
            resolved: bool,
            answer: str,
            source: 'local_cache' | 'vector_cache' | 'none',
            confidence: float,
            similar_qa: list[dict],
        }
    """
    _default = {"resolved": False, "answer": "", "source": "none", "confidence": 0.0, "similar_qa": []}

    if not question or len(question.strip()) < 5:
        return _default

    # Tier 1: 로컬 FTS5 캐시
    try:
        cache_result = _local_cache_resolve(question)
        if cache_result and cache_result["resolved"]:
            log.info(f"[QAResolver] ✅ Tier1 FTS5 캐시 히트 (hit_count≥{_HIT_COUNT_THRESHOLD})")
            return cache_result
        similar_qa = (cache_result or {}).get("similar_qa", [])
    except Exception as e:
        log.debug(f"[QAResolver] Tier1 오류: {e}")
        similar_qa = []

    # Tier 1.5: ChromaDB 시맨틱 벡터 검색 (5중 검증 내장)
    try:
        from JARVIS07_GUARDIAN.vector_store import search_vector
        vec_candidates = search_vector(question, top_k=3)
        if vec_candidates:
            best = vec_candidates[0]
            log.info(
                f"[QAResolver] ✅ Tier1.5 벡터 히트 "
                f"(sim={best['similarity']:.3f} conf={best['confidence']:.3f} "
                f"hit={best['hit_count']})"
            )
            return {
                "resolved":   True,
                "answer":     best["answer"],
                "source":     "vector_cache",
                "confidence": best["confidence"],
                "similar_qa": similar_qa,
            }
    except Exception as e:
        log.debug(f"[QAResolver] Tier1.5 오류: {e}")

    # Tier 2: 해결 실패 → Claude에게 위임 (similar_qa 는 컨텍스트로 전달)
    result = _default.copy()
    result["similar_qa"] = similar_qa
    return result


# ── Claude 답변 학습 ────────────────────────────────────────────────────────

def learn_from_claude(
    question: str,
    claude_answer: str,
    session_id: str = "",
    file_changes: list | None = None,
) -> str:
    """Claude가 해결한 Q&A를 로컬 캐시에 학습 누적.

    Returns: 'inserted' | 'updated' | 'skipped'
    """
    from JARVIS07_GUARDIAN.qa_store import upsert

    if not question or not claude_answer:
        return "skipped"
    if len(claude_answer.strip()) < 30:
        return "skipped"

    status = upsert(
        question_raw=question,
        answer=claude_answer,
        source="claude",
        session_id=session_id,
        confidence=1.0,
        file_changes=file_changes,
    )
    log.info(f"[QAResolver] 학습 누적: {status} (answer={len(claude_answer)}자)")

    # 벡터 스토어 동기화 (새 항목만 실시간 임베딩, 실패해도 계속)
    if status in ("inserted", "updated"):
        try:
            from JARVIS07_GUARDIAN.qa_store import _hash, _normalize
            from JARVIS07_GUARDIAN.vector_store import upsert_vector
            norm  = _normalize(question)
            qhash = _hash(norm)
            # qa_id 조회 (방금 upsert 된 레코드)
            from shared import db as _db
            with _db.get_db() as conn:
                row = conn.execute(
                    "SELECT id, hit_count FROM qa_entries WHERE question_hash = ?",
                    (qhash,),
                ).fetchone()
            if row:
                upsert_vector(
                    qa_id=row["id"],
                    question_hash=qhash,
                    question_norm=norm,
                    answer=claude_answer,
                    source="claude",
                    hit_count=row["hit_count"],
                    confidence=1.0,
                )
                log.debug(f"[QAResolver] 벡터 동기화 완료 qa_id={row['id']}")
        except Exception as e:
            log.debug(f"[QAResolver] 벡터 동기화 실패 (무시): {e}")

    return status


# ── 통계 ───────────────────────────────────────────────────────────────────

def resolver_stats() -> dict:
    """현재 리졸버 상태 반환 (FTS5 + 벡터 스토어 포함)."""
    from JARVIS07_GUARDIAN.qa_store import stats as qa_stats

    qa = qa_stats()
    vec = {}
    try:
        from JARVIS07_GUARDIAN.vector_store import vector_stats
        vec = vector_stats()
    except Exception:
        vec = {"available": False}

    return {
        "hit_count_threshold": _HIT_COUNT_THRESHOLD,
        "vector_store":        vec,
        **qa,
    }


__all__ = ["resolve", "learn_from_claude", "resolver_stats"]
