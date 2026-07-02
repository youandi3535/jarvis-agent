"""shared/embeddings.py — 로컬 MiniLM 임베딩 단일 진입점.

시스템 전체(QA 시맨틱 검색·오류 매칭·밴딧 시맨틱 신호·RADAR 키워드)가 이 모듈의
embed_* / cosine_* 를 공유한다. JARVIS07 vector_store(ChromaDB)와 *동일* 로컬 캐시
모델(무료·CPU·118MB·dim384)을 재사용 — 새 모델·API 키 다운로드 0.

★ 모델명은 EMBED_MODEL_NAME / EMBED_DIM 단일 상수.
   미래 bge-m3(1024d) 업그레이드는 이 파일 *두 줄만* 교체 + 전 코퍼스 reindex 로 완결.

정책:
  - lazy 싱글턴 (프로세스당 1회 로드). 로드 실패 시 _load_failed 캐시 → 재시도 폭주 방지.
  - 모델 미가용 환경(sentence_transformers 미설치 등) = fail-open:
      embed_texts → (N, 0) 빈 배열 / encode → None / cosine → 0.0.
    호출자는 available() 사전 가드 후 임베딩 경로를 조용히 건너뛰고 기존 로직 유지.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Optional

import numpy as np

log = logging.getLogger("jarvis.embeddings")

# ★ 임베딩 모델 단일 진입점 상수 — 업그레이드 시 이 두 줄만 교체 (예: "BAAI/bge-m3", 1024)
EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # 무료·로컬·CPU·118MB
EMBED_DIM = 384

_model = None
_lock = threading.Lock()
_load_failed = False


def _get_model():
    """thread-safe lazy 싱글턴. 실패 1회 → 이후 즉시 None (재시도 폭주 방지)."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    with _lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            _model = SentenceTransformer(EMBED_MODEL_NAME, device="cpu")
            log.info("[embeddings] 로드 완료: %s dim=%d", EMBED_MODEL_NAME, EMBED_DIM)
            return _model
        except Exception as e:  # noqa: BLE001
            _load_failed = True
            log.warning("[embeddings] 모델 로드 실패 — 임베딩 비활성(fail-open): %s", e)
            return None


def available() -> bool:
    """모델 로드 가능 여부. 소비자는 임베딩 경로 진입 전 이 가드로 폴백 결정."""
    return _get_model() is not None


# RADAR(shared/style.py) 호환 alias
is_available = available


def embed_texts(texts) -> np.ndarray:
    """list[str] → (N, EMBED_DIM) float32, L2-정규화 (cosine == dot product).

    모델 미가용 시 (N, 0) 빈 배열 반환 — 호출자는 available()/shape[1] 로 가드.
    """
    if texts is None:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    if isinstance(texts, str):
        texts = [texts]
    texts = [t if isinstance(t, str) else "" for t in texts]
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    m = _get_model()
    if m is None:
        return np.zeros((len(texts), 0), dtype=np.float32)
    try:
        v = m.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return np.asarray(v, dtype=np.float32)
    except Exception as e:  # noqa: BLE001
        log.warning("[embeddings] encode 실패: %s", e)
        return np.zeros((len(texts), 0), dtype=np.float32)


def embed_text(text: str) -> np.ndarray:
    """단일 텍스트 → (EMBED_DIM,) float32. 미가용 시 (0,)."""
    v = embed_texts([text])
    return v[0] if len(v) and v.shape[1] > 0 else np.zeros(0, dtype=np.float32)


@lru_cache(maxsize=1024)
def encode(text: str) -> Optional[tuple]:
    """캐시된 단일 임베딩 → tuple[float] | None.

    오류 매칭 재사용용 — 같은 수정 시도 내 재encode 방지. tuple 이라 hashable·불변.
    미가용/빈 텍스트 → None (호출자 `if vec:` 가드).
    """
    if not text or not text.strip():
        return None
    v = embed_text(text.strip())
    return tuple(float(x) for x in v) if v.size else None


def cosine_sim(a, b) -> float:
    """코사인 유사도. 빈 벡터·차원 불일치 시 0.0 (안전 재정규화)."""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# pattern_fixer 호환 alias (list/tuple 입력도 cosine_sim 이 처리)
cosine = cosine_sim


__all__ = [
    "EMBED_MODEL_NAME", "EMBED_DIM",
    "available", "is_available",
    "embed_texts", "embed_text", "encode",
    "cosine_sim", "cosine",
]
