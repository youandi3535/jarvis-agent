"""JARVIS07_GUARDIAN/rl_fixer.py — RL 기반 오류 수정 전략 선택기.

역할: error_analyzer Tier 1 (pattern_fixer) 실패 시 → Tier 1.5 로 진입.
     어떤 fixer 를 시도할지 SGDClassifier 로 학습·예측.
     수정 성공/실패 결과를 보상으로 받아 가중치 온라인 업데이트 (진짜 RL).

아키텍처:
  State  : error_type(12) + domain(10) + message keywords(16) = 38차원 feature vector
  Action : fixer 선택 8종 (relative_import·none_slicing·... · llm_fallback)
  Reward : 성공 → 해당 fixer 방향으로 partial_fit / 실패 → llm_fallback 방향으로 partial_fit
  Policy : epsilon-greedy (ε=0.15) — 15% 확률 탐험, 85% 최선 선택

온라인 학습:
  - reward() 호출 시마다 즉시 partial_fit() → 가중치 실시간 갱신
  - rl_model.pkl 에 저장 → 데몬 재시작 후에도 학습 유지
  - bootstrap_from_patterns() 로 learned_patterns.json 기존 사례 선학습
"""
from __future__ import annotations

import logging
import pickle
import random
import re
import sys
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("jarvis.rl_fixer")

_ROOT      = Path(__file__).resolve().parents[1]
_MODEL_PATH = Path(__file__).parent / "rl_model.pkl"

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ── Actions (fixer 이름 ↔ 인덱스) ─────────────────────────────────────────
FIXERS = [
    "relative_import",   # 0
    "none_slicing",      # 1
    "name_typo",         # 2
    "none_attribute",    # 3
    "import_name",       # 4
    "unpack_mismatch",   # 5
    "auto_patch",        # 6
    "llm_fallback",      # 7  ← last resort
]
_FIXER_IDX = {f: i for i, f in enumerate(FIXERS)}
_N_ACTIONS  = len(FIXERS)
_LLM_IDX    = _FIXER_IDX["llm_fallback"]

# ── Feature 정의 ───────────────────────────────────────────────────────────
_ERROR_TYPES = [
    "ImportError", "AttributeError", "NameError", "TypeError",
    "ValueError",  "KeyError",       "SyntaxError", "RuntimeError",
    "ConnectionError", "PostingFailure", "ExternalEdit", "Other",
]
_DOMAINS = [
    "writer", "image", "publish", "guardian",
    "infra",  "schedule", "radar", "master", "shared", "unknown",
]
_KEYWORDS = [
    "none", "import", "module", "not found", "has no attribute",
    "index", "slice",  "unpack", "expected",  "missing",
    "undefined", "attribute", "key", "type",  "value", "syntax",
]
_N_FEATURES = len(_ERROR_TYPES) + len(_DOMAINS) + len(_KEYWORDS)  # 38

_EPSILON = 0.15   # 탐험 확률


# ── Feature 추출 ──────────────────────────────────────────────────────────
def _featurize(error_record: dict) -> np.ndarray:
    """error_record → 38차원 float32 벡터."""
    etype   = error_record.get("error_type", "Other")
    source  = (error_record.get("source", "") + " " +
               error_record.get("module", "")).lower()
    message = error_record.get("message", "").lower()

    feat: list[float] = []

    # error_type one-hot (12)
    for t in _ERROR_TYPES:
        feat.append(1.0 if t == etype else 0.0)

    # domain one-hot (10)
    for d in _DOMAINS:
        feat.append(1.0 if d in source else 0.0)

    # message keyword binary (16)
    for kw in _KEYWORDS:
        feat.append(1.0 if kw in message else 0.0)

    return np.array(feat, dtype=np.float32).reshape(1, -1)


# ── 모델 관리 ─────────────────────────────────────────────────────────────
def _new_model():
    """SGDClassifier 신규 생성 (log_loss = 확률 출력 지원)."""
    from sklearn.linear_model import SGDClassifier
    clf = SGDClassifier(
        loss="log_loss",
        max_iter=1,
        warm_start=True,
        random_state=42,
        n_jobs=1,
    )
    # classes 초기화 — partial_fit 첫 호출 전에 필요
    X_dummy = np.zeros((1, _N_FEATURES), dtype=np.float32)
    clf.partial_fit(X_dummy, [_LLM_IDX], classes=np.arange(_N_ACTIONS))
    return clf


def _load_model():
    """저장된 모델 로드. 없으면 새로 생성."""
    if _MODEL_PATH.exists():
        try:
            with open(_MODEL_PATH, "rb") as f:
                return pickle.load(f)
        except Exception as e:
            log.warning(f"[RLFixer] 모델 로드 실패 ({e}) — 신규 생성")
    return _new_model()


def _save_model(clf) -> None:
    try:
        with open(_MODEL_PATH, "wb") as f:
            pickle.dump(clf, f)
    except Exception as e:
        log.warning(f"[RLFixer] 모델 저장 실패: {e}")


# ── 싱글턴 모델 (모듈 로드 시 1회) ───────────────────────────────────────
_clf = _load_model()
_update_count = 0   # 보상 업데이트 횟수 추적


# ── 예측 (Action 선택) ────────────────────────────────────────────────────
def predict(error_record: dict) -> tuple[str, float]:
    """RL 모델로 최적 fixer 예측.

    Returns:
        (fixer_name, confidence)  — confidence: 0.0~1.0
        llm_fallback 반환 시 confidence < 0.4 이면 호출자가 LLM 직행 가능.
    """
    global _clf
    try:
        X = _featurize(error_record)

        # epsilon-greedy 탐험
        if random.random() < _EPSILON:
            idx  = random.randint(0, _N_ACTIONS - 2)  # llm_fallback 제외 랜덤
            conf = 0.0
            log.debug(f"[RLFixer] 탐험 선택: {FIXERS[idx]}")
            return FIXERS[idx], conf

        # 최선 선택 (확률 기반)
        proba = _clf.predict_proba(X)[0]
        idx   = int(np.argmax(proba))
        conf  = float(proba[idx])
        fixer = FIXERS[idx]
        log.debug(f"[RLFixer] 예측: {fixer} (conf={conf:.2f})")
        return fixer, conf

    except Exception as e:
        log.warning(f"[RLFixer] predict 오류: {e}")
        return "llm_fallback", 0.0


# ── 보상 업데이트 (핵심 — 진짜 RL) ──────────────────────────────────────
def reward(error_record: dict, fixer_name: str, success: bool) -> None:
    """수정 결과를 보상으로 받아 모델 가중치 즉시 업데이트.

    성공: 해당 fixer 방향으로 partial_fit → 다음에 같은 패턴에서 이 fixer 선택 확률 ↑
    실패: llm_fallback 방향으로 partial_fit → 틀린 fixer 선택 확률 ↓
    """
    global _clf, _update_count
    try:
        X = _featurize(error_record)
        if success:
            y = np.array([_FIXER_IDX.get(fixer_name, _LLM_IDX)])
            log.info(f"[RLFixer] ✅ 보상 +1: {fixer_name}")
        else:
            y = np.array([_LLM_IDX])  # 실패 → llm_fallback 으로 학습
            log.info(f"[RLFixer] ❌ 보상 -1: {fixer_name} → llm_fallback 방향 학습")

        _clf.partial_fit(X, y)
        _update_count += 1

        # ★ 5회 업데이트마다 저장 (사용자 박제 2026-06-07 — 데몬 종료 시 손실 최소화)
        if _update_count % 5 == 0:
            _save_model(_clf)
            log.info(f"[RLFixer] 모델 저장 (업데이트 {_update_count}회)")

    except Exception as e:
        log.warning(f"[RLFixer] reward 업데이트 오류: {e}")


# ── ★ 외부 호출용 flush — atexit hook 등 종료 시 호출 (사용자 박제 2026-06-07) ─────
def flush_model() -> bool:
    """현재 메모리 모델을 디스크에 즉시 저장. 데몬 종료 hook·수동 트리거용.

    Returns: True 성공 / False 실패 또는 변경 없음
    """
    global _clf, _update_count
    try:
        if _update_count == 0:
            return False  # 변경 없으면 skip
        _save_model(_clf)
        log.info(f"[RLFixer] 종료 시 모델 flush 완료 ({_update_count}회 학습)")
        return True
    except Exception as e:
        log.warning(f"[RLFixer] flush 오류: {e}")
        return False


# ── 부트스트랩 — learned_patterns.json 선학습 ────────────────────────────
def bootstrap_from_patterns() -> int:
    """learned_patterns.json 의 기존 성공 사례로 초기 학습.

    fixer 매핑이 있는 패턴만 사용. 이미 부트스트랩된 경우 skip.
    Returns: 학습한 샘플 수
    """
    global _clf, _update_count

    _PATTERNS_FILE = Path(__file__).parent / "learned_patterns.json"
    _BOOTSTRAP_FLAG = Path(__file__).parent / ".rl_bootstrapped"

    if _BOOTSTRAP_FLAG.exists():
        log.debug("[RLFixer] 부트스트랩 이미 완료 — skip")
        return 0

    if not _PATTERNS_FILE.exists():
        return 0

    import json
    try:
        data = json.loads(_PATTERNS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[RLFixer] learned_patterns 로드 실패: {e}")
        return 0

    patterns = data.get("patterns", [])
    trained = 0

    for p in patterns:
        fixer = p.get("fixer", "")
        if not fixer or fixer not in _FIXER_IDX:
            continue
        if fixer in ("externaledit", "null"):
            continue

        error_record = {
            "error_type": p.get("error_type", "Other"),
            "source":     p.get("domain", "unknown"),
            "module":     p.get("domain", "unknown"),
            "message":    p.get("message_pattern", ""),
        }

        X = _featurize(error_record)
        y = np.array([_FIXER_IDX[fixer]])
        hit = p.get("hit_count", 1)

        # hit_count 만큼 반복 학습 (많이 쓰인 패턴 = 더 강한 신호)
        repeats = min(hit, 5)
        for _ in range(repeats):
            _clf.partial_fit(X, y)
        trained += 1

    _save_model(_clf)
    _BOOTSTRAP_FLAG.write_text("done")
    log.info(f"[RLFixer] 부트스트랩 완료: {trained}개 패턴 선학습")
    return trained


# ── 통계 ──────────────────────────────────────────────────────────────────
def rl_stats() -> dict:
    """RL 모델 현재 상태 통계."""
    try:
        coef_norm = float(np.linalg.norm(_clf.coef_)) if hasattr(_clf, "coef_") else 0.0
        return {
            "model_path":    str(_MODEL_PATH),
            "model_exists":  _MODEL_PATH.exists(),
            "update_count":  _update_count,
            "n_features":    _N_FEATURES,
            "n_actions":     _N_ACTIONS,
            "fixers":        FIXERS,
            "epsilon":       _EPSILON,
            "coef_norm":     round(coef_norm, 4),
        }
    except Exception as e:
        return {"error": str(e)}


# ── 직접 실행 시 부트스트랩 ───────────────────────────────────────────────
if __name__ == "__main__":
    import json
    n = bootstrap_from_patterns()
    print(f"부트스트랩: {n}개 패턴 학습")
    print(json.dumps(rl_stats(), ensure_ascii=False, indent=2))
