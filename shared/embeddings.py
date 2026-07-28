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

# sentence-transformers 내부 httpcore/httpx DEBUG 노이즈 억제
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ★ 임베딩 모델 단일 진입점 상수 — 업그레이드 시 이 두 줄만 교체 (예: "BAAI/bge-m3", 1024)
EMBED_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # 무료·로컬·CPU·118MB
EMBED_DIM = 384

# ★ 리랭커(cross-encoder) 모델 — 2단계 검색의 2단계 (ERRORS [544]).
#
#   왜 필요한가 — 임베딩(bi-encoder)만으로는 못 잡는 실패가 있다:
#     질의와 문서를 **각각 따로** 벡터로 만들어 비교하므로 "단어가 겹친다" 에 약하다.
#     실측 — 질의 "이미지가 연달아 붙는다" 에서, 그 문구를 *예시로 인용한* 무관 사고 [534]가
#     0.548 로 1위가 되고 정답 [172](0.360)·[39][103][170][171] 이 전부 임계 아래로 탈락했다
#     (자기참조 오염). cross-encoder 는 질의와 문서를 **붙여서 한 번에** 읽으므로
#     "이건 인용이지 이 사고가 아니다" 를 구분한다 — 실측 재점수 [172] +0.651 / [534] −5.130.
#
#   왜 1단계로 안 쓰나: 문서 1건당 모델 1회라 전량에는 못 쓴다. 후보 N개에만 적용한다
#   (실측 6쌍 476ms, CPU). 그래서 "빠른 1차 → 정밀 2차" 2단계 구조가 표준이다.
#
#   ★ LangChain 경유가 아니다 — `sentence_transformers` 를 직접 쓴다. CLAUDE.md 상
#     그 직접 로드가 합법인 파일은 여기 하나뿐이라, 리랭커도 여기가 주인이다.
RERANK_MODEL_NAME = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"  # 무료·로컬·CPU·다국어(한국어 포함)

_model = None
_lock = threading.Lock()
_load_failed = False

_reranker = None
_rerank_lock = threading.Lock()
_rerank_failed = False


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


# ── 리랭커 (cross-encoder) — 2단계 검색의 2단계 ──────────────────────

def _get_reranker():
    """thread-safe lazy 싱글턴. 실패 1회 → 이후 즉시 None (재시도 폭주 방지).

    ★ 임베딩 모델과 **별개 싱글턴** — 리랭커를 안 쓰는 경로가 그 모델(470MB)을
      지불하지 않게 한다. 첫 호출 때만 내려받는다.
    """
    global _reranker, _rerank_failed
    if _reranker is not None:
        return _reranker
    if _rerank_failed:
        return None
    with _rerank_lock:
        if _reranker is not None:
            return _reranker
        if _rerank_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder
            _reranker = CrossEncoder(RERANK_MODEL_NAME, device="cpu", max_length=512)
            log.info("[embeddings] 리랭커 로드 완료: %s", RERANK_MODEL_NAME)
            return _reranker
        except Exception as e:  # noqa: BLE001
            _rerank_failed = True
            log.warning("[embeddings] 리랭커 로드 실패 — 재순위 비활성(fail-open): %s", e)
            return None


def rerank_available() -> bool:
    """리랭커 사용 가능 여부. 소비자는 진입 전 이 가드로 폴백 결정 (fail-open)."""
    return _get_reranker() is not None


def rerank(query: str, docs, top_k: int = 0) -> list:
    """(질의, 문서) 쌍을 cross-encoder 로 재점수 → `[(원본인덱스, 점수), …]` 내림차순.

    ★ 반환이 *인덱스* 인 이유: 호출자가 자기 자료구조(dict·dataclass 무엇이든)를 그대로
      들고 있게 한다. 여기서 Document 같은 타입으로 변환하면 호출자 코드가 그 타입에 묶인다.

    ★ fail-open: 모델이 없거나 실패하면 **빈 리스트** 를 돌려준다 — 호출자는 원래 순서를
      그대로 쓰면 된다. 검색이 리랭커 때문에 멈추는 일은 없어야 한다.

    ★ 점수 스케일이 임베딩 코사인과 **다르다**(로짓, 음수 가능). 절대 임계값을
      코사인 기준으로 재사용하지 말 것 — 호출자가 자기 임계를 따로 정해야 한다.
    """
    if not query or not docs:
        return []
    model = _get_reranker()
    if model is None:
        return []
    try:
        pairs = [(query, str(d) if not isinstance(d, str) else d) for d in docs]
        scores = model.predict(pairs)
        ranked = sorted(enumerate(float(s) for s in scores), key=lambda x: -x[1])
        return ranked[:top_k] if top_k and top_k > 0 else ranked
    except Exception as e:  # noqa: BLE001
        log.warning("[embeddings] rerank 실패 — 원순서 유지(fail-open): %s", e)
        return []


def rerank_effective() -> bool:
    """★ 리랭커가 *실제로 판별을 하는지* 동작으로 확인 (저장소 표준).

    로드 성공만으로는 부족하다 — 명백히 관련된 문서를 무관한 문서보다 위에 놓는지 본다.
    모델이 없으면 True (fail-open — 없는 것은 결함이 아니다).
    """
    if not rerank_available():
        return True
    r = rerank("이미지가 연달아 붙는다",
               ["이미지 연속 배치 재발 — 사진 두 장이 나란히 삽입되는 문제",
                "데이터베이스 백업 보존 기간을 30일로 조정"])
    return bool(r) and r[0][0] == 0


__all__ = [
    "EMBED_MODEL_NAME", "EMBED_DIM", "RERANK_MODEL_NAME",
    "available", "is_available",
    "embed_texts", "embed_text", "encode",
    "cosine_sim", "cosine",
    "rerank", "rerank_available", "rerank_effective",
]
