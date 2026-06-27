"""JARVIS07_GUARDIAN/bandit.py — Multi-Armed Bandit 기반 fixer 선택 강화학습.

강화학습 구조:
  State  : error_type (예: "TypeError", "NameError")
  Arms   : 정적 fixer 6종 (relative_import / none_slicing / name_typo / ...)
  Reward : 실제 파일 수정 성공(+1) / 실패(-1)
  Policy : UCB1 (Upper Confidence Bound 1)

UCB1 공식:
  score(arm) = mean_reward(arm) + C * sqrt(ln(total_pulls) / pulls(arm))

  - 한 번도 안 시도한 arm → score = ∞ (탐색 강제)
  - pulls 많아질수록 confidence term ↓ → exploitation 지배
  - C = 1.4 (√2) : 이론적 최적값

파인튜닝(모델 가중치 변경) 아님 — 성공/실패 카운터 기반 온라인 학습.
데이터 1건부터 작동, 시간이 지날수록 성공률 높은 fixer 자동 우선화.
순수 Python — GPU/sklearn 불필요, 저사양 Mac 무리 없음.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.guardian.bandit")

_BANDIT_FILE = Path(__file__).resolve().parent / "bandit_state.json"
_LOCK = threading.Lock()

# UCB1 탐색 강도 — sqrt(2) ≈ 1.414 : 이론적 최적값
_UCB_C = 1.414

# 보상 정의
_WIN  = +1.0
_LOSS = -1.0


# ── 내부 유틸 ──────────────────────────────────────────────────────────

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


def _ucb1(wins: float, losses: float, pulls: int, total_pulls: int) -> float:
    """UCB1 점수. pulls=0 이면 ∞ (탐색 강제)."""
    if pulls == 0:
        return float("inf")
    mean = (wins - losses) / pulls          # -1 ~ +1 범위
    explore = _UCB_C * math.sqrt(math.log(max(total_pulls, 1)) / pulls)
    return mean + explore


# ── 공개 API ──────────────────────────────────────────────────────────

def rank_fixers(error_type: str, fixer_names: list[str]) -> list[str]:
    """UCB1 점수 기준으로 fixer 우선순위 정렬.

    Args:
        error_type : 오류 타입 (예: "TypeError")
        fixer_names: 시도 가능한 fixer 이름 리스트

    Returns:
        UCB1 내림차순 정렬 리스트.
        한 번도 안 시도한 fixer → 항상 앞에 배치 (탐색 우선).
    """
    if not fixer_names:
        return fixer_names

    with _LOCK:
        state = _load()
        arms = state.get(error_type, {})
        total_pulls = sum(a.get("pulls", 0) for a in arms.values())

        scored: list[tuple[str, float]] = []
        for name in fixer_names:
            arm = arms.get(name, {})
            score = _ucb1(
                wins=arm.get("wins", 0.0),
                losses=arm.get("losses", 0.0),
                pulls=arm.get("pulls", 0),
                total_pulls=total_pulls,
            )
            scored.append((name, score))

    scored.sort(key=lambda x: -x[1])

    # 디버그 로그 (inf는 "∞"로 표시)
    log.debug(
        f"[BANDIT] {error_type} 순서: "
        + ", ".join(
            f"{n}(∞)" if s == float("inf") else f"{n}({s:.3f})"
            for n, s in scored
        )
    )
    return [n for n, _ in scored]


def reward(error_type: str, fixer_name: str, success: bool) -> None:
    """수정 시도 결과를 bandit 상태에 반영 (보상 신호).

    Args:
        error_type : 오류 타입
        fixer_name : 사용한 fixer 이름
        success    : 실제 파일 수정 성공 여부
    """
    with _LOCK:
        state = _load()
        arm = state.setdefault(error_type, {}).setdefault(
            fixer_name, {"wins": 0.0, "losses": 0.0, "pulls": 0}
        )
        arm["pulls"] += 1
        if success:
            arm["wins"]   += abs(_WIN)
        else:
            arm["losses"] += abs(_LOSS)
        _save(state)

    win_rate = arm["wins"] / arm["pulls"]
    log.info(
        f"[BANDIT] {'✅' if success else '❌'} {error_type}/{fixer_name} "
        f"pulls={arm['pulls']} win_rate={win_rate:.1%}"
    )


def negative_reward_for_skipped(error_type: str, fixer_names: list[str]) -> None:
    """특정 fixer들이 결과를 생성하지 못했을 때 일괄 음의 보상.

    try_pattern_fix() 에서 정적 fixer 가 None 반환 시 호출.
    """
    for name in fixer_names:
        reward(error_type, name, success=False)


def stats() -> dict:
    """전체 bandit 학습 상태 요약 — 대시보드/텔레그램 표시용."""
    state = _load()
    total_pulls = 0
    total_wins  = 0
    by_type: dict[str, dict] = {}

    for et, arms in state.items():
        t_pulls = sum(a.get("pulls", 0) for a in arms.values())
        t_wins  = sum(a.get("wins",  0) for a in arms.values())
        total_pulls += t_pulls
        total_wins  += t_wins

        best = max(
            arms.items(),
            key=lambda x: x[1].get("wins", 0) / max(x[1].get("pulls", 1), 1),
            default=(None, {}),
        )
        by_type[et] = {
            "pulls":      t_pulls,
            "win_rate":   round(t_wins / max(t_pulls, 1), 3),
            "best_fixer": best[0],
            "arm_count":  len(arms),
        }

    return {
        "total_pulls":     total_pulls,
        "total_wins":      total_wins,
        "global_win_rate": round(total_wins / max(total_pulls, 1), 3),
        "by_type":         by_type,
    }


def top_fixers(n: int = 5) -> list[dict]:
    """성공률 Top N fixer — 대시보드 표시용."""
    state = _load()
    rows: list[dict] = []
    for et, arms in state.items():
        for fname, arm in arms.items():
            pulls = arm.get("pulls", 0)
            if pulls == 0:
                continue
            rows.append({
                "error_type": et,
                "fixer":      fname,
                "wins":       int(arm.get("wins",   0)),
                "losses":     int(arm.get("losses", 0)),
                "pulls":      pulls,
                "win_rate":   round(arm.get("wins", 0) / pulls, 3),
            })
    rows.sort(key=lambda x: (-x["win_rate"], -x["pulls"]))
    return rows[:n]


def worst_fixers(n: int = 3) -> list[dict]:
    """성공률 최하위 N fixer — 패턴 추가 후보 식별용."""
    state = _load()
    rows: list[dict] = []
    for et, arms in state.items():
        for fname, arm in arms.items():
            pulls = arm.get("pulls", 0)
            if pulls < 3:   # 3회 미만은 노이즈
                continue
            rows.append({
                "error_type": et,
                "fixer":      fname,
                "pulls":      pulls,
                "win_rate":   round(arm.get("wins", 0) / pulls, 3),
            })
    rows.sort(key=lambda x: (x["win_rate"], -x["pulls"]))
    return rows[:n]


def learning_summary() -> str:
    """텔레그램 알림용 한 줄 요약."""
    s = stats()
    if s["total_pulls"] == 0:
        return "🎰 Bandit: 아직 학습 없음"
    return (
        f"🎰 Bandit: {s['total_pulls']}회 시도 "
        f"/ 성공률 {s['global_win_rate']:.1%} "
        f"/ {len(s['by_type'])}개 오류 타입 학습 중"
    )
