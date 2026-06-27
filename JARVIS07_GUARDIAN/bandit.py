"""JARVIS07_GUARDIAN/bandit.py — Contextual Bandit (Linear UCB) 기반 fixer 선택 강화학습.

Regular Bandit vs Contextual Bandit:
  Regular Bandit   : error_type 만 보고 결정 → 6가지 state
  Contextual Bandit: error_type + module + message + traceback 등 맥락 전체 → 선형 모델로 결정

Linear UCB 공식 (arm a, context vector x):
  score(a) = θ_a^T · x  +  α · sqrt(x^T · A_a^{-1} · x)
  ├── θ_a^T · x        : 선형 모델 예상 보상 (exploitation)
  └── α · sqrt(...)    : 불확실성 보너스 (exploration) — pulls 많을수록 ↓

업데이트 (arm a, context x, reward r):
  A_a ← A_a + x · x^T        (d×d)
  b_a ← b_a + r · x          (d)
  θ_a = A_a^{-1} · b_a       (ridge regression 해)

특징:
  - 데이터 1건부터 즉시 작동 (온라인 학습)
  - 순수 numpy — GPU/PyTorch 불필요, 저사양 Mac 무리 없음
  - JSON 영구 저장 → 재시작 후에도 학습 유지
  - Regular Bandit 대비: 같은 error_type 이라도 module·message 맥락이 다르면 다른 fixer 선택

REINFORCE/Policy Gradient 과의 차이:
  Contextual Bandit (Linear UCB)  — 지금 구현
    · 선형 모델 (A, b 행렬만) — 파라미터 = d×d + d per arm
    · 1건부터 안정적 학습, 분산 낮음
    · 탐색/활용 균형 자동 (UCB)
    · 이 문제 규모(arm 6개)에 최적

  REINFORCE (Policy Gradient)
    · 신경망 (입력 → 히든 → 출력), 파라미터 수십 배
    · 분산 높음 → 수백~수천 건 쌓여야 안정
    · 데이터 증가 후 Contextual Bandit 성능 포화되면 그때 전환 권장
    · 지금 전환하면 적은 데이터에 과적합·노이즈만 추가됨

Feature vector (d=14):
  [0-5]  error_type 6종 indicator
  [6-9]  module 4종 indicator (jarvis02 / jarvis07 / jarvis08 / shared)
  [10]   message 에 NoneType / None 포함 여부
  [11]   message 에 import / module 포함 여부
  [12]   message 길이 정규화 (0~1)
  [13]   traceback 깊이 정규화 (0~1)
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("jarvis.guardian.bandit")

_BANDIT_FILE = Path(__file__).resolve().parent / "bandit_state.json"
_LOCK = threading.Lock()

# ── 하이퍼파라미터 ────────────────────────────────────────────────
_D     = 14     # feature 차원
_ALPHA = 1.0    # 탐색 강도 (높을수록 미탐색 arm 선호)
_WIN   = +1.0   # 성공 보상
_LOSS  = -1.0   # 실패 보상

# 알려진 error_type (feature 인코딩용)
_KNOWN_ERROR_TYPES = [
    "TypeError", "NameError", "AttributeError",
    "ImportError", "ModuleNotFoundError", "ValueError",
]


# ── Feature 추출 ──────────────────────────────────────────────────

def _extract_features(error_record: dict) -> np.ndarray:
    """error_record → 14차원 context vector.

    모든 feature 는 0~1 범위로 정규화.
    """
    et  = error_record.get("error_type", "") or ""
    msg = (error_record.get("message",   "") or "").lower()
    mod = (error_record.get("module",    "") or "").lower()
    tb  = error_record.get("traceback",  "") or ""

    x = np.zeros(_D, dtype=np.float64)

    # [0-5] error_type 6종 indicator
    for i, known in enumerate(_KNOWN_ERROR_TYPES):
        x[i] = 1.0 if known in et else 0.0

    # [6-9] module 4종 indicator
    x[6] = 1.0 if "jarvis02" in mod else 0.0
    x[7] = 1.0 if ("jarvis07" in mod or "guardian" in mod) else 0.0
    x[8] = 1.0 if ("jarvis08" in mod or "publish" in mod)  else 0.0
    x[9] = 1.0 if "shared" in mod else 0.0

    # [10] NoneType/None 관련 메시지
    x[10] = 1.0 if ("nonetype" in msg or "'none'" in msg) else 0.0

    # [11] import/module 관련 메시지
    x[11] = 1.0 if ("import" in msg or "module" in msg or "cannot" in msg) else 0.0

    # [12] 메시지 길이 정규화 (200자 기준)
    x[12] = min(len(msg) / 200.0, 1.0)

    # [13] traceback 깊이 정규화 (20줄 기준)
    x[13] = min(tb.count("\n") / 20.0, 1.0)

    return x


# ── 상태 직렬화 ───────────────────────────────────────────────────

def _arm_to_dict(A: np.ndarray, b: np.ndarray) -> dict:
    return {"A": A.tolist(), "b": b.tolist()}


def _arm_from_dict(d: dict) -> tuple[np.ndarray, np.ndarray]:
    return np.array(d["A"]), np.array(d["b"])


def _new_arm() -> tuple[np.ndarray, np.ndarray]:
    """미탐색 arm 초기 상태 — A = I, b = 0."""
    return np.eye(_D, dtype=np.float64), np.zeros(_D, dtype=np.float64)


# ── 영속성 ────────────────────────────────────────────────────────

def _load() -> dict:
    """bandit_state.json 로드. 없거나 손상이면 빈 dict."""
    try:
        if _BANDIT_FILE.exists():
            return json.loads(_BANDIT_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[BANDIT] 상태 로드 실패: {e}")
    return {}


def _save(state: dict) -> None:
    try:
        _BANDIT_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[BANDIT] 상태 저장 실패: {e}")


# ── UCB 점수 계산 ─────────────────────────────────────────────────

def _ucb_score(A: np.ndarray, b: np.ndarray, x: np.ndarray) -> float:
    """Linear UCB 점수.

    A = I (초기) 이면 A_inv = I → x^T x = |x|^2 → uncertainty term 최대.
    즉 한 번도 안 시도한 arm 은 uncertainty 가 커서 자동으로 탐색됨.
    """
    try:
        A_inv = np.linalg.inv(A)
        theta = A_inv @ b
        exploit = float(theta @ x)
        explore = _ALPHA * float(np.sqrt(x @ A_inv @ x))
        return exploit + explore
    except np.linalg.LinAlgError:
        return float("inf")   # A 역행렬 실패 → 탐색 강제


# ── 공개 API ──────────────────────────────────────────────────────

def rank_fixers(error_record: dict, fixer_names: list[str]) -> list[str]:
    """Linear UCB 점수 기준으로 fixer 우선순위 정렬.

    Args:
        error_record : error_log 레코드 전체 (context)
        fixer_names  : 시도 가능한 fixer 이름 리스트

    Returns:
        UCB 내림차순 정렬 리스트.
        미탐색 arm → uncertainty term 크므로 자동으로 앞에 배치 (탐색).
    """
    if not fixer_names:
        return fixer_names

    x = _extract_features(error_record)

    with _LOCK:
        state = _load()
        scored: list[tuple[str, float]] = []

        for name in fixer_names:
            arm_data = state.get(name)
            if arm_data:
                A, b = _arm_from_dict(arm_data)
            else:
                A, b = _new_arm()
            score = _ucb_score(A, b, x)
            scored.append((name, score))

    scored.sort(key=lambda t: -t[1])
    log.debug(
        f"[BANDIT] Linear UCB 순서: "
        + ", ".join(f"{n}({s:.3f})" for n, s in scored)
    )
    return [n for n, _ in scored]


def reward(
    error_type: str,
    fixer_name: str,
    success: bool,
    error_record: Optional[dict] = None,
) -> None:
    """수정 시도 결과를 arm 상태에 반영 (online update).

    A_a ← A_a + x · x^T
    b_a ← b_a + r · x

    Args:
        error_type   : 오류 타입 (로그용)
        fixer_name   : 사용한 fixer 이름
        success      : 실제 파일 수정 성공 여부
        error_record : context (없으면 feature = 0 벡터)
    """
    x = _extract_features(error_record or {})
    r = _WIN if success else _LOSS

    with _LOCK:
        state = _load()
        arm_data = state.get(fixer_name)
        if arm_data:
            A, b = _arm_from_dict(arm_data)
        else:
            A, b = _new_arm()

        A = A + np.outer(x, x)
        b = b + r * x

        state[fixer_name] = _arm_to_dict(A, b)
        _save(state)

    log.info(
        f"[BANDIT] {'✅' if success else '❌'} {error_type}/{fixer_name} r={r:+.1f}"
    )


def negative_reward_for_skipped(
    error_type: str,
    fixer_names: list[str],
    error_record: Optional[dict] = None,
) -> None:
    """결과를 생성하지 못한 fixer들에 일괄 음의 보상."""
    for name in fixer_names:
        reward(error_type, name, success=False, error_record=error_record)


# ── 통계 / 대시보드 ───────────────────────────────────────────────

def _arm_win_rate(name: str, state: dict) -> Optional[float]:
    """arm 의 예상 보상 (θ 의 평균값, -1~+1)."""
    arm_data = state.get(name)
    if not arm_data:
        return None
    A, b = _arm_from_dict(arm_data)
    try:
        theta = np.linalg.inv(A) @ b
        return float(np.mean(theta))
    except np.linalg.LinAlgError:
        return None


def stats() -> dict:
    """전체 bandit 학습 상태 요약 — 대시보드/텔레그램 표시용."""
    state = _load()
    arm_summaries = {}
    for name, arm_data in state.items():
        A, b = _arm_from_dict(arm_data)
        # pulls 추정: A - I 의 Frobenius norm (각 pull마다 x x^T 가 더해짐)
        pulls_est = max(0, int(round(np.linalg.norm(A - np.eye(_D), "fro"))))
        try:
            theta = np.linalg.inv(A) @ b
            mean_reward = float(np.mean(theta))
        except np.linalg.LinAlgError:
            mean_reward = 0.0
        arm_summaries[name] = {
            "pulls_est":   pulls_est,
            "mean_reward": round(mean_reward, 3),
        }

    return {
        "model":         "Linear UCB Contextual Bandit",
        "feature_dim":   _D,
        "alpha":         _ALPHA,
        "arm_count":     len(state),
        "arms":          arm_summaries,
    }


def top_fixers(n: int = 5) -> list[dict]:
    """예상 보상 Top N fixer — 대시보드 표시용."""
    state = _load()
    rows: list[dict] = []
    for name in state:
        wr = _arm_win_rate(name, state)
        if wr is None:
            continue
        rows.append({"fixer": name, "mean_reward": round(wr, 3)})
    rows.sort(key=lambda x: -x["mean_reward"])
    return rows[:n]


def learning_summary() -> str:
    """텔레그램 알림용 한 줄 요약."""
    s = stats()
    if not s["arms"]:
        return "🎰 Contextual Bandit: 아직 학습 없음"
    best = max(s["arms"].items(), key=lambda x: x[1]["mean_reward"], default=(None, {}))
    return (
        f"🎰 Contextual Bandit ({_D}D Linear UCB): "
        f"{s['arm_count']}개 arm 학습 중 "
        f"/ 현재 최우선 fixer: {best[0]} (mean_reward={best[1].get('mean_reward', '?')})"
    )
