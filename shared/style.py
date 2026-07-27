"""shared/style.py — 임베딩 프로바이더 선택 + 벡터 직렬화.

★ 2026-07-27 — 이 파일은 원래 '브랜드 보이스' 인덱싱·검색 모듈이었다. 그 기능을 전부
  걷어냈다. 이유는 **읽는 코드가 하나도 없었기 때문** — `search_similar` /
  `build_few_shot_block` 호출자 0, `style_corpus` 200행을 매일 쌓기만 하고 아무 데도
  안 썼다. 게다가 tfidf(2048d)와 MiniLM(384d)이 섞여 색인돼 서로를 못 보는 상태였다
  (검색 시 차원 다른 행은 조용히 skip). 쌓지도, 찾지도 않는다.

남은 책임은 하나 — **임베딩 프로바이더를 고르고 벡터를 bytes 로 싸고 푸는 것**.
소비자는 `JARVIS03_RADAR/learning.py`(트렌드 키워드 임베딩) 하나다.

프로바이더 우선순위 (`_get_provider`): voyage(키 있을 때) > local_minilm > tfidf placeholder.
  · Claude 모델은 임베딩 API 를 제공하지 않는다 (텍스트 생성 전용).
  · 실제 운용은 local MiniLM 384d — 무료·CPU·API 키 0.
  · 모델명·차원의 진실은 `shared/embeddings.py` 의 EMBED_MODEL_NAME / EMBED_DIM.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # type: ignore
load_dotenv(ROOT / ".env")



# ════════════════════════════════════════════════════════════════════
#  Part 1 — 임베딩·인덱싱 (옛 style_indexer.py 내용)
# ════════════════════════════════════════════════════════════════════

def _get_provider():
    """우선순위 (provider_name, model_name, dim, fn) 반환 — voyage > local_minilm > tfidf.

    ★ 2026-07-02: VOYAGE 키 없을 때 TF-IDF(고전) 대신 로컬 MiniLM(shared.embeddings)
      384d 사용. 무료·CPU·API키 0. sentence_transformers 미설치 환경만 tfidf 최후 폴백.
    """
    if os.getenv("VOYAGE_API_KEY"):
        return ("voyage", "voyage-3-lite", 1024, _embed_voyage)
    try:
        from shared.embeddings import is_available, EMBED_MODEL_NAME, EMBED_DIM
        if is_available():
            return ("local_minilm", EMBED_MODEL_NAME, EMBED_DIM, _embed_local_minilm)
    except Exception:
        pass
    return ("tfidf", "tfidf-fallback", 0, _embed_tfidf_placeholder)


def _embed_voyage(texts: list[str]) -> np.ndarray:
    import requests
    key = os.environ["VOYAGE_API_KEY"]
    out = []
    BATCH = 32
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        r = requests.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "voyage-3-lite", "input": batch, "input_type": "document"},
            timeout=60,
        )
        r.raise_for_status()
        for d in r.json()["data"]:
            out.append(d["embedding"])
        time.sleep(0.1)
    return np.array(out, dtype=np.float32)


def _embed_local_minilm(texts: list[str]) -> np.ndarray:
    """로컬 MiniLM(paraphrase-multilingual-MiniLM-L12-v2, 384d) 재사용 — 무료·CPU·L2정규화.

    shared.embeddings 단일 진입점 위임 → vector_store(ChromaDB)와 동일 캐시 모델 공유.
    """
    from shared.embeddings import embed_texts
    return embed_texts(texts)


def _embed_tfidf_placeholder(texts: list[str]) -> np.ndarray:
    """TF-IDF 는 batch fit_transform 필요 — 단건 임베딩 불가."""
    raise RuntimeError(
        "TF-IDF mode requires batch fit_transform — use run_full_index() instead of single-text embed"
    )


# 텍스트 정규화
_HTML_BLOCK = re.compile(r"<(style|script|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_MULTI_WS = re.compile(r"\s+")


def _pack(vec: np.ndarray) -> bytes:
    """float32 array → bytes."""
    return vec.astype(np.float32).tobytes()


def unpack(blob: bytes, dim: int) -> np.ndarray:
    """bytes → float32 array."""
    return np.frombuffer(blob, dtype=np.float32).reshape(-1)


__all__ = [
    "_get_provider", "_embed_voyage", "_embed_local_minilm",
    "_embed_tfidf_placeholder", "_pack", "unpack",
]
