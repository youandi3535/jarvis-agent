"""shared/style.py — 브랜드 보이스 학습 통합 모듈 (★ 사용자 박제 2026-05-18 — Phase 2 정리).

이전 (2026-05-18 이전): `shared/style_indexer.py` (인덱싱) + `shared/style_retriever.py` (검색) *2 파일 분리*.
현재: 단일 모듈 — 임베딩·인덱싱·검색·few-shot 빌더 모두 여기.

## 책임
1. **인덱싱** — 과거 발행 글 (post_analysis.original_content) → 임베딩 → style_corpus 저장.
2. **검색** — 질의 → cosine top-K → few-shot 프롬프트 블록 빌드.

## 임베딩 프로바이더 우선순위
- Claude 모델은 임베딩 API 제공 안 함 (텍스트 생성 전용).
- 1순위: `VOYAGE_API_KEY` → voyage-3-lite (한국어 강함)
- 2순위: sklearn TF-IDF 폴백

## CLI
```bash
python -m shared.style                # 미인덱스 글 모두 처리
python -m shared.style --reindex      # 코퍼스 비우고 전체 재인덱싱
python -m shared.style search "아파트 청약 1순위"   # 검색 sanity check
```

## 호환성 (옛 파일 제거 후)
- 옛 호출: `from shared.style_indexer import ...` / `from shared.style_retriever import ...`
- 신 호출: `from shared.style import ...`
- 옛 파일 2종은 `_deleted_2026-05-18/shared/` 보관 (복구 가능).
"""
from __future__ import annotations

import argparse
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

from shared import db as _db


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


def clean_text(raw: str) -> str:
    """본문 정규화 — <style>/<script> 블록 통째 제거 + HTML 태그·주석 제거 + 공백 정리."""
    if not raw:
        return ""
    txt = _HTML_BLOCK.sub(" ", raw)
    txt = _HTML_COMMENT.sub(" ", txt)
    txt = _HTML_TAG.sub(" ", txt)
    txt = _MULTI_WS.sub(" ", txt).strip()
    return txt


def make_excerpt(text: str, n: int = 800) -> str:
    """few-shot 주입용 발췌 (앞 N자)."""
    text = clean_text(text)
    return text[:n] + ("…" if len(text) > n else "")


def _pack(vec: np.ndarray) -> bytes:
    """float32 array → bytes."""
    return vec.astype(np.float32).tobytes()


def unpack(blob: bytes, dim: int) -> np.ndarray:
    """bytes → float32 array."""
    return np.frombuffer(blob, dtype=np.float32).reshape(-1)


def _tfidf_fit_transform(texts: list[str]):
    """TF-IDF 폴백 — sklearn 사용. 한국어는 char ngram 으로 처리."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(2, 4),
        max_features=2048,
        sublinear_tf=True,
    )
    M = vec.fit_transform(texts).astype(np.float32).toarray()
    norms = np.linalg.norm(M, axis=1, keepdims=True) + 1e-9
    M = M / norms
    save_dir = ROOT / "shared"
    import pickle
    pickle.dump(vec, open(save_dir / ".tfidf_vec.pkl", "wb"))
    return M, "tfidf-char-2-4", M.shape[1]


def run_full_index(reindex: bool = False, verbose: bool = True) -> dict:
    """미인덱스(또는 전체) post 처리 → style_corpus 적재."""
    if reindex:
        n = _db.style_corpus_clear()
        if verbose:
            print(f"  🗑️  코퍼스 초기화: {n}건 삭제")

    pids = _db.style_corpus_unindexed_post_ids(min_chars=200)
    if not pids:
        if verbose:
            print("  ✅ 인덱싱할 새 글 없음")
        return {"indexed": 0, "skipped": 0, "provider": ""}

    provider, model, dim, fn = _get_provider()
    if verbose:
        print(f"  🧠 임베딩 프로바이더: {provider} ({model})")
        print(f"  📚 대상: {len(pids)}편")

    posts = []
    for pid in pids:
        p = _db.style_corpus_fetch_post(pid)
        if not p:
            continue
        try:
            from JARVIS02_WRITER import length_manager as _LM_si
        except ImportError:
            _LM_si = None
        body = clean_text(p.get("original_content") or "")
        if _LM_si and len(body) < _LM_si.INDEXER_BODY_MIN:
            continue
        posts.append((p, body))

    if not posts:
        return {"indexed": 0, "skipped": len(pids), "provider": provider}

    if provider == "tfidf":
        vecs, model, dim = _tfidf_fit_transform([b for _, b in posts])
    else:
        try:
            vecs = fn([b[:_LM_si.INDEXER_EMBED_MAX] for _, b in posts])
        except Exception as e:
            print(f"  ⚠️ {provider} 호출 실패 → TF-IDF 폴백: {e}")
            vecs, model, dim = _tfidf_fit_transform([b for _, b in posts])
            provider = "tfidf"

    indexed = 0
    for (p, body), vec in zip(posts, vecs):
        try:
            _db.style_corpus_upsert(
                source_id=int(p["id"]),
                platform=p.get("platform") or "",
                title=p.get("title") or p.get("theme") or "",
                content=body[:_LM_si.INDEXER_BODY_MAX],
                excerpt=make_excerpt(body, _LM_si.BODY_SNIPPET_LEN),
                embedding_bytes=_pack(vec),
                embed_model=model,
                embed_dim=int(dim),
                char_count=len(body),
                published_at=p.get("created_at") or "",
                views=int(p.get("current_views") or 0),
            )
            indexed += 1
        except Exception as e:
            print(f"  ⚠️ id={p['id']} 저장 실패: {e}")

    if verbose:
        print(f"  ✅ 인덱싱 완료: {indexed}편")
    return {"indexed": indexed, "skipped": len(pids) - indexed, "provider": provider}


def embed_query(text: str) -> tuple[np.ndarray, str, int]:
    """질의 시점 임베딩 — 코퍼스에 사용된 모델과 같은 모델 사용."""
    if not text or not text.strip():
        return np.zeros(0, dtype=np.float32), "", 0

    rows = _db.style_corpus_all_embeddings()
    if not rows:
        provider, model, dim, fn = _get_provider()
    else:
        model = rows[0]["embed_model"]
        dim = rows[0]["embed_dim"]
        # 저장 코퍼스 모델로 provider 역추론 (질의·저장 동일 모델 = cosine 공간 일치 전제)
        from shared.embeddings import EMBED_MODEL_NAME
        if "voyage" in model:
            provider = "voyage"
        elif model == EMBED_MODEL_NAME:
            provider = "local_minilm"
        else:
            provider = "tfidf"

    text = clean_text(text)[:8000]

    if provider == "voyage":
        v = _embed_voyage([text])[0]
    elif provider == "local_minilm":
        v = _embed_local_minilm([text])[0]
    else:
        import pickle
        try:
            vec = pickle.load(open(ROOT / "shared/.tfidf_vec.pkl", "rb"))
            arr = vec.transform([text]).toarray().astype(np.float32)[0]
            n = np.linalg.norm(arr) + 1e-9
            v = arr / n
        except Exception:
            return np.zeros(dim or 1, dtype=np.float32), model, dim
    return v, model, len(v)


# ════════════════════════════════════════════════════════════════════
#  Part 2 — 검색·few-shot 빌더 (옛 style_retriever.py 내용)
# ════════════════════════════════════════════════════════════════════

def search_similar(query: str, k: int = 3, min_sim: float = 0.05) -> list[dict]:
    """질의 → 코퍼스 cosine top-K. 결과: [{sim, title, platform, excerpt, ...}]"""
    if not query or not query.strip():
        return []

    rows = _db.style_corpus_all_embeddings()
    if not rows:
        return []

    qv, _, _ = embed_query(query)
    if qv.size == 0:
        return []

    scored = []
    for r in rows:
        try:
            cv = unpack(r["embedding"], r["embed_dim"])
            if cv.shape != qv.shape:
                continue
            sim = float(np.dot(qv, cv))
        except Exception:
            continue
        if sim < min_sim:
            continue
        scored.append({
            "sim": sim,
            "source_id": r.get("source_id"),
            "platform": r.get("platform") or "",
            "title": r.get("title") or "",
            "excerpt": r.get("excerpt") or "",
            "char_count": r.get("char_count") or 0,
            "views": r.get("views") or 0,
        })

    scored.sort(key=lambda x: x["sim"], reverse=True)

    seen_src = set()
    seen_title = set()
    uniq = []
    for s in scored:
        sid = s.get("source_id")
        title = s.get("title")
        if sid in seen_src or title in seen_title:
            continue
        seen_src.add(sid)
        seen_title.add(title)
        uniq.append(s)
        if len(uniq) >= k:
            break
    return uniq


def build_few_shot_block(query: str, k: int = 2, max_chars: int = 600,
                         min_sim: float = 0.05) -> str:
    """top-K 발췌를 Claude system prompt 에 주입할 형태로 포맷."""
    hits = search_similar(query, k=k, min_sim=min_sim)
    if not hits:
        return ""
    parts = []
    for i, h in enumerate(hits, 1):
        excerpt = (h["excerpt"] or "")[:max_chars]
        if not excerpt:
            continue
        parts.append(
            f"[샘플 {i} — {h['platform']} / sim={h['sim']:.3f}]\n"
            f"제목: {h['title']}\n"
            f"본문 발췌:\n{excerpt}"
        )
    if not parts:
        return ""
    header = (
        "아래는 같은 운영자가 과거에 발행한 유사 주제의 글 발췌입니다. "
        "어휘 선택·문장 길이·문단 구성·말투(존댓말/반말 일관성)·이모지 사용 빈도·"
        "강조 표현(굵게·인용·리스트) 패턴을 참고해 새 글의 톤을 맞추세요. "
        "내용을 표절하지는 마세요.\n"
    )
    return header + "\n\n---\n\n".join(parts)


__all__ = [
    # Indexer (Part 1)
    "_get_provider", "_embed_voyage", "_embed_local_minilm", "_embed_tfidf_placeholder",
    "clean_text", "make_excerpt",
    "_pack", "unpack", "_tfidf_fit_transform",
    "run_full_index", "embed_query",
    # Retriever (Part 2)
    "search_similar", "build_few_shot_block",
]


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="브랜드 보이스 학습 — 통합 모듈")
    sub = ap.add_subparsers(dest="cmd", help="명령")

    p_idx = sub.add_parser("index", help="미인덱스 글 인덱싱 (기본 명령 — cmd 없을 시 자동)")
    p_idx.add_argument("--reindex", action="store_true", help="기존 코퍼스 비우고 전체 재인덱싱")

    p_search = sub.add_parser("search", help="유사 글 검색 sanity check")
    p_search.add_argument("query", help="검색 질의")
    p_search.add_argument("--k", type=int, default=3)

    # 기본값 — cmd 없으면 index --reindex 옵션만 받음 (옛 CLI 호환)
    ap.add_argument("--reindex", action="store_true",
                    help="기존 코퍼스 비우고 전체 재인덱싱 (cmd 없을 때만 적용)")
    args = ap.parse_args()

    if args.cmd == "search":
        hits = search_similar(args.query, k=args.k)
        if not hits:
            print("(매칭 없음)")
            sys.exit(0)
        for h in hits:
            print(f"sim={h['sim']:+.4f}  [{h['platform']:7s}]  {h['title']}")
        print("\n--- few-shot block ---\n")
        print(build_few_shot_block(args.query, k=args.k))
    else:
        reindex = (args.cmd == "index" and args.reindex) or (args.cmd is None and args.reindex)
        print(f"\n🎙️  브랜드 보이스 학습 — 인덱싱 시작")
        res = run_full_index(reindex=reindex)
        print(f"\n✅ 인덱싱 완료: {res['indexed']}편 (provider={res['provider']})")
