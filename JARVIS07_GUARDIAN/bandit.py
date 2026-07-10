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

★ arm = *fixer 전략* 이다 — 오류 지문(fingerprint)이 아니다 (사용자 박제 2026-07-04):
  밴딧의 arm 은 *소수의 고정된 fixer 전략* (정적 6종 + learned + auto_patch + llm) 이어야 한다.
  오류 지문을 arm 으로 쓰면 오류마다 arm 이 새로 생겨 arm 이 무한 증식하고(파일 비대·죽은 신호),
  컨텍스추얼 밴딧의 전제(소수 arm + context 로 상황 구분)가 무너진다. 따라서 `_arm_key()` 가
  *모든* 입력 arm 이름을 유한한 전략 공간으로 접는다 (verified:*/new:* → learned_*, llm_patch → llm).
  이 정규화는 밴딧 스스로 방어하는 단일 초크포인트 — 호출자가 무엇을 넘기든 arm 은 유한하다.

특징:
  - 데이터 1건부터 즉시 작동 (온라인 학습)
  - 순수 numpy — GPU/PyTorch 불필요, 저사양 Mac 무리 없음
  - JSON 영구 저장 (compact — indent 없음, 반올림) → 재시작 후에도 학습 유지

★ 적응형 복잡도 (Graduated Complexity) — 데이터가 쌓일수록 차원 점진 확장 (상한 v3=28D):
  raw 고차원 통짜 교체는 cold-start 파국(관측≪차원 → ridge prior 가 신호 압도).
  대신 관측 수가 쌓일수록 자동 승급 — 차원을 데이터가 감당할 만큼만 점진 확장:
    v1 : 14D 수작업 (데이터 적어도 빠른 학습)
    v2 : 14 + 6 오류 프로토타입 코사인 = 20D
    v3 : 14 + 6 + 8 임베딩 투영 = 28D (상한 — arm 이 유한하므로 arm당 관측이 차원을 감당)
  승급 임계: 버전 v 진입에 관측 ≥ _OBS_PER_DIM × dim(v). 각 승급은 학습보존 블록확장
  (기존 차원 A/b 불변, 신규 차원만 λI/0). 임베딩 미가용 시 현재 버전 유지 (안전 폴백).
  ★ 상한을 28D 로 낮춘 이유: arm 이 유한(≈8)해 arm당 관측이 충분 → 차원 폭주(404D)로 인한
    ridge 신호 소멸(θ≈0, 모든 arm 무차별)을 원천 차단. (사용자 박제 2026-07-04)

Feature vector (적응형):
  [0-5]   error_type 6종 indicator
  [6-9]   module 4종 indicator (jarvis02 / jarvis07 / jarvis08 / shared)
  [10]    message 에 NoneType / None 포함 여부
  [11]    message 에 import / module 포함 여부
  [12]    message 길이 정규화 (0~1)
  [13]    traceback 깊이 정규화 (0~1)
  [14-19] (v2+) 오류 프로토타입 K=6종 코사인 유사도 → [0,1]
  [20-27] (v3)  임베딩 가우시안 투영 8차원 → 0.5(1+tanh) [0,1]
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
_D_BASE = 14                     # v1 수작업 feature 차원
_K_PROTO = 6                     # v2 오류 프로토타입 코사인 차원
_PROJ_STEP = 8                   # v3+ 매 단계 추가되는 임베딩 투영 차원
_MAX_PROJ = 8                    # ★ 투영 상한 8D → 최대 dim 28D (arm 유한 → 차원 폭주 방지)
_OBS_PER_DIM = 3                 # 승급 임계 계수: 버전 v 진입에 필요한 관측 ≈ 3 × dim(v)
_PROJ_SEED = 20260702            # 투영 행렬 고정 시드 (중첩 안정 → 마이그레이션 무손실)
_D      = _D_BASE                # 하위호환 alias (동적 계산은 _dim_for_version 사용)
_ALPHA  = 1.0                    # 탐색 강도 (높을수록 미탐색 arm 선호)
_WIN    = +1.0                   # 성공 보상
_LOSS   = -1.0                   # 실패 보상
_LAMBDA = 1.0                    # ridge prior (A 초기 = λI)
_ROUND  = 6                      # 직렬화 소수 자리 (파일 크기 절감)

# v2 오류 프로토타입 대표 문장 (부팅 1회 임베딩·캐시). error_type 계열과 1:1 정렬.
_PROTO_SENTENCES = [
    "NoneType object has no attribute 값이 None 인데 속성이나 인덱스에 접근했습니다",
    "ModuleNotFoundError ImportError 모듈을 찾을 수 없거나 임포트 경로가 잘못되었습니다",
    "TypeError 함수에 잘못된 타입이나 개수의 인자를 전달했습니다",
    "AttributeError 객체에 존재하지 않는 속성이나 메서드를 호출했습니다",
    "SyntaxError IndentationError 들여쓰기 괄호 문법 오류로 코드를 파싱할 수 없습니다",
    "ValueError 형식이나 범위에 맞지 않는 잘못된 값이 들어왔습니다",
]
_PROTO_CACHE = None   # (K, dim) 정규화 임베딩 or None
_PROJ_CACHE = None    # (_MAX_PROJ, dim) 고정 시드 가우시안 투영 or None

# 알려진 error_type (feature 인코딩용)
_KNOWN_ERROR_TYPES = [
    "TypeError", "NameError", "AttributeError",
    "ImportError", "ModuleNotFoundError", "ValueError",
]

# ── ★ arm 전략 공간 (유한) — _arm_key 가 모든 입력을 이 공간으로 접는다 ──────────
#   정적 fixer 6종 + auto_patch (모두 pattern_fixer._FIXER_REGISTRY 와 정합) +
#   learned_verified / learned_new (학습 캐시 조회) + llm (LLM 폴백).
#   이 집합 밖의 이름은 _arm_key 가 규칙적으로 접거나(prefix) 그대로 통과(정적)시킨다.
_STATIC_FIXER_ARMS = frozenset({
    "relative_import", "none_slicing", "name_typo",
    "none_attribute", "import_name", "unpack_mismatch", "auto_patch",
})


def _arm_key(name: str) -> Optional[str]:
    """★ 임의의 fixer 이름 → 유한한 전략 arm 키 (밴딧 무한 증식 방지 단일 초크포인트).

    - verified:<fingerprint>  → "learned_verified"   (검증된 학습 캐시 전략)
    - new:<fingerprint>       → "learned_new"         (신규 학습 캐시 전략)
    - "llm_patch"             → "llm"                 (LLM 폴백 전략)
    - 정적 6종 + auto_patch   → 그대로                (고정 전략)
    - "learned"               → "learned_verified"    (통합 학습 조회 후보)
    - 그 외(빈 값/미상)       → None                  (arm 생성 안 함 = 보상/랭킹 제외)

    이 규칙 덕분에 오류 지문(GitCommit/ExternalEdit/…)이 arm 으로 새는 일이 원천 차단된다.
    """
    if not name or not str(name).strip():
        return None
    n = str(name)
    if n.startswith("verified:"):
        return "learned_verified"
    if n.startswith("new:"):
        return "learned_new"
    if n in ("llm_patch", "llm"):
        return "llm"
    if n == "learned":
        return "learned_verified"
    if n in _STATIC_FIXER_ARMS:
        return n
    # 미지의 이름 — 전략으로 인정하지 않음(오염 방지). 정적 등록 경로만 arm 이 된다.
    return None


# ── 적응형 사다리 (데이터 커질수록 차원↑, 상한 v3=28D) ───────────────

def _proj_dims_for_version(version: int) -> int:
    """버전 v 의 임베딩 투영 차원 수 (v<3 은 0, 상한 _MAX_PROJ)."""
    if version < 3:
        return 0
    return min(_MAX_PROJ, _PROJ_STEP * (version - 2))


def _dim_for_version(version: int) -> int:
    if version < 2:
        return _D_BASE
    return _D_BASE + _K_PROTO + _proj_dims_for_version(version)


def _threshold_for_version(version: int) -> int:
    """버전 v 도달(진입)에 필요한 최소 총 관측 수. v1=0."""
    if version < 2:
        return 0
    return _OBS_PER_DIM * _dim_for_version(version)


def _max_version() -> int:
    """투영이 상한(_MAX_PROJ)에 도달하는 최종 버전 (그 이상 승급 없음)."""
    return 2 + -(-_MAX_PROJ // _PROJ_STEP)   # ceil(_MAX_PROJ/_PROJ_STEP) → 2+1 = 3


def _proto_matrix():
    """오류 프로토타입 임베딩 행렬 (K, dim). 부팅 1회 캐시. 임베딩 불가 시 None."""
    global _PROTO_CACHE
    if _PROTO_CACHE is not None:
        return _PROTO_CACHE
    try:
        from shared import embeddings as _emb
        if not _emb.available():
            return None
        mat = _emb.embed_texts(_PROTO_SENTENCES)   # (K, dim) or (K, 0)
        if mat.ndim == 2 and mat.shape[0] == _K_PROTO and mat.shape[1] == _emb.EMBED_DIM:
            _PROTO_CACHE = mat.astype(np.float64)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[BANDIT] 프로토타입 임베딩 실패: {e}")
    return _PROTO_CACHE


def _proj_matrix():
    """고정 시드 가우시안 투영 행렬 (_MAX_PROJ, dim). 부팅 1회 캐시.

    ★ 중첩 안정성: 시드 고정 → R[:n] 이 버전 무관하게 동일 → 상위 버전이 하위 버전의
      투영 차원을 그대로 포함 (nested) → 블록확장 마이그레이션 무손실의 전제.
      JL 스케일 1/√dim 로 투영 성분을 O(1) 유지 (A 조건수 안정).
    """
    global _PROJ_CACHE
    if _PROJ_CACHE is not None:
        return _PROJ_CACHE
    try:
        from shared import embeddings as _emb
        if not _emb.available():
            return None
        rng = np.random.default_rng(_PROJ_SEED)
        _PROJ_CACHE = rng.standard_normal((_MAX_PROJ, _emb.EMBED_DIM)) / np.sqrt(_emb.EMBED_DIM)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[BANDIT] 투영 행렬 생성 실패: {e}")
    return _PROJ_CACHE


# ── Feature 추출 ──────────────────────────────────────────────────

def _extract_base(error_record: dict) -> np.ndarray:
    """error_record → 14차원 수작업 context vector (v1). 모든 feature 0~1 정규화."""
    et  = error_record.get("error_type", "") or ""
    msg = (error_record.get("message",   "") or "").lower()
    mod = (error_record.get("module",    "") or "").lower()
    tb  = error_record.get("traceback",  "") or ""

    x = np.zeros(_D_BASE, dtype=np.float64)

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


def _extract_features(error_record: dict, version: int = 1) -> np.ndarray:
    """버전 인지 feature 추출 — 적응형 사다리.

    v1 = 14D base / v2 = +K 프로토타입 코사인 / v3 = +8 임베딩 투영.
    ★ 하위 버전의 차원 값은 상위 버전에서도 *동일* (base 무관·프로토타입 고정·투영 중첩)
      → 블록확장 마이그레이션 무손실의 전제. (encode 느리므로 _LOCK 밖에서 호출.)
    """
    base = _extract_base(error_record)
    if version < 2:
        return base

    pdims = _proj_dims_for_version(version)

    # 임베딩 1회 계산 (프로토타입·투영 공용)
    emb_vec = None
    text = ((error_record.get("message", "") or "") + " " +
            (error_record.get("error_type", "") or "") + " " +
            (error_record.get("traceback", "") or "")[:400]).strip() or "unknown error"
    try:
        from shared import embeddings as _emb
        e = _emb.embed_texts([text])   # (1, dim) or (1, 0)
        if e.ndim == 2 and e.shape[0] >= 1 and e.shape[1] == _emb.EMBED_DIM:
            emb_vec = e[0].astype(np.float64)
    except Exception:  # noqa: BLE001
        emb_vec = None

    # 프로토타입 코사인 블록 (K)
    proto = _proto_matrix()
    if emb_vec is not None and proto is not None:
        sims = (proto @ emb_vec + 1.0) / 2.0        # [-1,1] → [0,1]
    else:
        sims = np.zeros(_K_PROTO, dtype=np.float64)  # 런타임 불가 → 중립 패딩
    blocks = [base, sims]

    # 임베딩 투영 블록 (v3, 8차원)
    if pdims > 0:
        R = _proj_matrix()
        if emb_vec is not None and R is not None:
            proj = 0.5 * (1.0 + np.tanh(R[:pdims] @ emb_vec))   # (pdims,) → [0,1]
        else:
            proj = np.zeros(pdims, dtype=np.float64)
        blocks.append(proj)

    return np.concatenate(blocks)


def _fit_x(x: np.ndarray, dim: int) -> np.ndarray:
    """x 를 arm 차원 dim 에 맞춤 (승급 경계 1틱 방어 — 학습 무해)."""
    if x.shape[0] == dim:
        return x
    xx = np.zeros(dim, dtype=np.float64)
    n = min(dim, x.shape[0])
    xx[:n] = x[:n]
    return xx


# ── 상태 직렬화 ───────────────────────────────────────────────────

def _arm_to_dict(A: np.ndarray, b: np.ndarray, n: int = 0, rsum: float = 0.0) -> dict:
    """arm 상태 → JSON dict. 파일 크기 절감 위해 반올림.

    n    : 실제 pull(시도) 횟수 — 정직한 통계용 (Frobenius 추정치 대체)
    rsum : 보상 누적합 — 평균 보상 = rsum / n (θ 평균 희석 문제 회피)
    """
    return {
        "A":    [[round(float(v), _ROUND) for v in row] for row in A.tolist()],
        "b":    [round(float(v), _ROUND) for v in b.tolist()],
        "n":    int(n),
        "rsum": round(float(rsum), _ROUND),
    }


def _arm_from_dict(d: dict) -> tuple[np.ndarray, np.ndarray]:
    return np.array(d["A"], dtype=np.float64), np.array(d["b"], dtype=np.float64)


def _new_arm(dim: int = _D_BASE) -> tuple[np.ndarray, np.ndarray]:
    """미탐색 arm 초기 상태 — A = λI, b = 0."""
    return _LAMBDA * np.eye(dim, dtype=np.float64), np.zeros(dim, dtype=np.float64)


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
    """compact 저장 — indent 없음(separators). arm 유한 + 반올림 → 파일 수십 KB."""
    try:
        _BANDIT_FILE.write_text(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[BANDIT] 상태 저장 실패: {e}")


def _read_state() -> dict:
    """정규화 로드 → {feature_version, obs_count, arms}. 구 flat 포맷 무손실 하위호환."""
    raw = _load()
    if "arms" in raw or "feature_version" in raw:
        raw.setdefault("feature_version", 1)
        raw.setdefault("obs_count", 0)
        raw.setdefault("arms", {})
        return raw
    # 구 flat 포맷: 최상위 키 전부가 arm 이었음
    return {"feature_version": 1, "obs_count": 0, "arms": dict(raw)}


def _write_state(state: dict) -> None:
    _save(state)


def _migrate_arms_to_version(state: dict, target_version: int) -> None:
    """학습보존 블록확장: A(d0×d0)→A'(dT×dT) 좌상=기존·우하=λI·off=0, b→앞d0 유지·뒤=0.

    수학: LinUCB posterior A=λI+Σxxᵀ. 신규 차원은 관측 0 → 사후=ridge prior(λI,0),
    구·신 차원 joint 관측 없음 → 교차공분산 블록=0 이 정확. 블록대각이라 θ=A⁻¹b 가 분리
    → θ_old 완전 불변(기존 차원 학습 100% 보존) + θ_new=0·A_new=I 로 UCB 불확실성 최대
    → 신규 시맨틱 차원 자동 탐색. 리셋(학습 폐기) 금지 — 블록확장이 유일한 무손실 확장.
    n/rsum(정직한 통계) 는 차원과 무관 → 그대로 보존.
    """
    target_dim = _dim_for_version(target_version)
    for name, arm in list(state["arms"].items()):
        A_old, b_old = _arm_from_dict(arm)
        d0 = A_old.shape[0]
        if d0 >= target_dim:
            continue
        A_new = _LAMBDA * np.eye(target_dim, dtype=np.float64)
        A_new[:d0, :d0] = A_old
        b_new = np.zeros(target_dim, dtype=np.float64)
        b_new[:d0] = b_old
        state["arms"][name] = _arm_to_dict(
            A_new, b_new,
            n=int(arm.get("n", 0)), rsum=float(arm.get("rsum", 0.0)),
        )
    state["feature_version"] = target_version


def _maybe_upgrade_features(state: dict) -> None:
    """관측이 임계를 넘는 만큼 *연속* 자동 승급 (호출자가 _LOCK 보유).

    데이터가 커질수록 계속 승급 (상한 v3). 임베딩 인프라 미가용 시 현재 버전 유지 (안전 폴백).
    """
    version = int(state.get("feature_version", 1))
    obs = int(state.get("obs_count", 0))
    max_v = _max_version()
    if version >= max_v:
        return
    try:
        from shared import embeddings as _emb
        if not _emb.available() or _proto_matrix() is None:
            return   # 임베딩 불가 → 현재 버전 유지 안전 폴백
    except Exception:  # noqa: BLE001
        return

    target = version
    while target < max_v and obs >= _threshold_for_version(target + 1):
        target += 1
    if target == version:
        return

    old_dim = _dim_for_version(version)
    _migrate_arms_to_version(state, target)
    new_dim = _dim_for_version(target)
    log.info(f"[BANDIT] 적응형 승급 v{version}→v{target} "
             f"({old_dim}D→{new_dim}D) @obs={obs}")
    try:
        from shared.notify import send_tg
        send_tg(
            f"\U0001F9E0 Bandit 적응형 복잡도 전환: v{version}→v{target} ({old_dim}D→{new_dim}D)\n"
            f"관측 {obs}건 도달 → 시맨틱 임베딩 차원 확장 (총 {new_dim}D)\n"
            f"(기존 학습 100% 보존 · 상한 v{max_v}={_dim_for_version(max_v)}D · 내부 변경이라 통보만)"
        )
    except Exception:  # noqa: BLE001
        pass


# ── UCB 점수 계산 ─────────────────────────────────────────────────

def _ucb_score(A: np.ndarray, b: np.ndarray, x: np.ndarray) -> float:
    """Linear UCB 점수.

    A = λI (초기) 이면 A_inv = (1/λ)I → uncertainty term 최대.
    즉 한 번도 안 시도한 arm 은 uncertainty 가 커서 자동으로 탐색됨.
    """
    try:
        A_inv = np.linalg.inv(A)
        theta = A_inv @ b
        exploit = float(theta @ x)
        explore = _ALPHA * float(np.sqrt(max(0.0, x @ A_inv @ x)))
        return exploit + explore
    except np.linalg.LinAlgError:
        return float("inf")   # A 역행렬 실패 → 탐색 강제


# ── 공개 API ──────────────────────────────────────────────────────

def rank_fixers(error_record: dict, fixer_names: list[str]) -> list[str]:
    """Linear UCB 점수 기준으로 fixer 우선순위 정렬.

    ★ arm 은 _arm_key 로 접힌 *전략* — 입력 이름은 실행용으로 그대로 반환하되, 점수는
      전략 arm 으로 계산한다 (learned 후보 여러 개가 같은 전략 점수를 공유해도 무방).

    Args:
        error_record : error_log 레코드 전체 (context)
        fixer_names  : 시도 가능한 fixer 이름 리스트 (실행 식별자)

    Returns:
        UCB 내림차순 정렬된 *입력 이름* 리스트.
        미탐색 arm → uncertainty term 크므로 자동으로 앞에 배치 (탐색).
    """
    if not fixer_names:
        return fixer_names

    version = _read_state().get("feature_version", 1)
    x = _extract_features(error_record, version)   # 느린 encode 는 락 밖

    with _LOCK:
        state = _read_state()
        arms = state["arms"]
        cur_dim = _dim_for_version(state["feature_version"])
        # 전략 arm 점수 캐시 (같은 전략 후보가 여럿이면 1회만 계산)
        score_cache: dict[str, float] = {}
        scored: list[tuple[str, float]] = []
        for name in fixer_names:
            key = _arm_key(name)
            if key is None:
                # 전략으로 인정 안 되는 이름 — 맨 뒤로 (점수 -inf)
                scored.append((name, float("-inf")))
                continue
            if key not in score_cache:
                arm_data = arms.get(key)
                if arm_data:
                    A, b = _arm_from_dict(arm_data)
                else:
                    A, b = _new_arm(cur_dim)
                score_cache[key] = _ucb_score(A, b, _fit_x(x, A.shape[0]))
            scored.append((name, score_cache[key]))

    scored.sort(key=lambda t: -t[1])
    log.debug(
        "[BANDIT] Linear UCB 순서: "
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
    n_a += 1 ; rsum_a += r   (정직한 통계)

    ★ arm 은 _arm_key(fixer_name) 로 접힌 전략 — 오류 지문이 arm 으로 새지 않는다.

    Args:
        error_type   : 오류 타입 (로그용)
        fixer_name   : 사용한 fixer 이름 (전략으로 접힘)
        success      : 실제 파일 수정 성공 여부
        error_record : context (없으면 feature = 0 벡터)
    """
    key = _arm_key(fixer_name)
    if key is None:
        # 전략으로 인정 안 되는 이름(변경추적·미상) → 밴딧 오염 방지: 보상 무시
        log.debug(f"[BANDIT] arm 아님 — 보상 무시 (fixer={fixer_name}, et={error_type})")
        return

    version = _read_state().get("feature_version", 1)
    x = _extract_features(error_record or {}, version)   # 느린 encode 는 락 밖
    r = _WIN if success else _LOSS

    with _LOCK:
        state = _read_state()
        arms = state["arms"]
        arm_data = arms.get(key)
        if arm_data:
            A, b = _arm_from_dict(arm_data)
            n_prev = int(arm_data.get("n", 0))
            rsum_prev = float(arm_data.get("rsum", 0.0))
        else:
            A, b = _new_arm(_dim_for_version(state["feature_version"]))
            n_prev, rsum_prev = 0, 0.0

        xv = _fit_x(x, A.shape[0])   # 승급 경계 race 방어
        A = A + np.outer(xv, xv)
        b = b + r * xv

        arms[key] = _arm_to_dict(A, b, n=n_prev + 1, rsum=rsum_prev + r)
        state["obs_count"] = state.get("obs_count", 0) + 1
        _maybe_upgrade_features(state)   # 임계 도달 시 블록확장 승급
        _write_state(state)

    log.info(
        f"[BANDIT] {'✅' if success else '❌'} {error_type}/{fixer_name}→arm={key} r={r:+.1f}"
    )


# ── 통계 / 대시보드 ───────────────────────────────────────────────

def _arm_avg_reward(arm_data: dict) -> float:
    """arm 의 실제 평균 보상 = rsum / n (정직한 지표, θ 평균 희석 회피)."""
    n = int(arm_data.get("n", 0))
    if n <= 0:
        return 0.0
    return float(arm_data.get("rsum", 0.0)) / n


def stats() -> dict:
    """전체 bandit 학습 상태 요약 — 대시보드/텔레그램 표시용."""
    state = _read_state()
    arm_summaries = {}
    for name, arm_data in state["arms"].items():
        n = int(arm_data.get("n", 0))
        arm_summaries[name] = {
            "pulls_est":   n,                                   # ★ 실제 pull 수 (정직)
            "mean_reward": round(_arm_avg_reward(arm_data), 3),  # ★ rsum/n
        }

    return {
        "model":           "Linear UCB Contextual Bandit",
        "feature_dim":     _dim_for_version(state["feature_version"]),
        "feature_version": state["feature_version"],
        "obs_count":       state.get("obs_count", 0),
        "alpha":           _ALPHA,
        "arm_count":       len(arm_summaries),
        "arms":            arm_summaries,
    }


def top_fixers(n: int = 5) -> list[dict]:
    """평균 보상 Top N fixer — 대시보드 표시용 (pull ≥1 인 arm 만)."""
    state = _read_state()
    rows: list[dict] = []
    for name, arm_data in state["arms"].items():
        if int(arm_data.get("n", 0)) <= 0:
            continue
        rows.append({"fixer": name, "mean_reward": round(_arm_avg_reward(arm_data), 3)})
    rows.sort(key=lambda x: -x["mean_reward"])
    return rows[:n]


