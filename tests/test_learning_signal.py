"""블로그 품질 학습의 *신호* 무결성 — 골든 테스트 (2026-08-08).

★ 별도 파일 이유: 다른 세션이 `test_publish_golden.py` 를 동시 수정 중이다.
★ 기계 독립: 운영 DB 를 읽지 않는다. 합성 행으로 판정 로직만 검사한다(ERRORS [568]).
"""
from __future__ import annotations

import numpy as np
import pytest


# ══════════════════════════════════════════════════════════════════
# ① 관측되지 않은 것을 0 으로 채우지 않는다
# ══════════════════════════════════════════════════════════════════
def _row(platform, views, rank, trend=50.0, perf=1.0, fresh=1.0):
    return {"platform": platform, "actual_views": views, "naver_rank": rank,
            "trend_score": trend, "perf_boost": perf, "freshness": fresh}


def test_미관측_행은_0이_아니라_NaN이다():
    """★ 실사고: `float(r["actual_views"] or 0)` 이 학습기를 통째로 망가뜨렸다.

    실측 — 두 신호가 정확히 상보적이었다:
        naver   438행: actual_views 전부 0  · naver_rank 전부 관측
        tistory  42행: actual_views 전부 >0 · naver_rank 전부 NULL
    그런데 42 >= min_signal(20) 이라 조회수가 채택됐고, **네이버 438행(91%)이
    "조회수 0 = 나쁨" 으로 학습**됐다. 0 은 "나빴다" 이지 "모른다" 가 아니다.
    """
    from JARVIS03_RADAR.learning import build_target

    rows = ([_row("tistory", 10 + i, None) for i in range(25)]
            + [_row("naver", 0, None) for _ in range(30)])   # 네이버: 둘 다 미관측
    y, sig = build_target(rows, min_signal=20)

    assert "actual_views" in sig
    obs, miss = ~np.isnan(y), np.isnan(y)
    assert obs.sum() == 25, f"관측 25행이어야 하는데 {obs.sum()}"
    assert miss.sum() == 30, "미관측 30행이 NaN 으로 표시되어야 한다"
    assert not (y[obs] == 0).all(), "관측 행이 전부 0 — 백분위 정규화가 안 됐다"


def test_두_신호가_상보적이면_둘_다_쓴다():
    """플랫폼마다 측정 가능한 신호가 다르다 — 하나만 고르면 나머지가 '나쁜 사례' 가 된다."""
    from JARVIS03_RADAR.learning import build_target

    rows = ([_row("tistory", 10 + i, None) for i in range(25)]
            + [_row("naver", 0, 5 + i) for i in range(25)])
    y, sig = build_target(rows, min_signal=20)

    assert "actual_views" in sig and "naver_rank" in sig, f"신호가 하나만 쓰였다: {sig}"
    assert (~np.isnan(y)).sum() == 50, "두 신호 모두 관측된 50행 전부 학습에 쓰여야 한다"
    assert len(np.unique(y)) > 2, "정답값에 변별이 없다"


def test_표본이_부족하면_학습하지_않는다():
    """어느 신호도 최소 표본을 못 채우면 상수 y → 호출자가 보류한다."""
    from JARVIS03_RADAR.learning import build_target

    y, sig = build_target([_row("tistory", 5, None) for _ in range(3)], min_signal=20)
    assert sig == "none"
    assert len(np.unique(y)) < 2, "표본 부족인데 학습 가능한 y 가 나왔다"


# ══════════════════════════════════════════════════════════════════
# ② 가중치가 정렬 변별력을 죽이면 저장하지 않는다
# ══════════════════════════════════════════════════════════════════
def test_음수_계수를_0으로_자르지_않는다():
    """★ 종전 `max(0.0, v)` 가 지배 피처를 삭제해 정렬키를 상수로 만들었다.

    음수 계수는 *가정 위반* 이지 잡음이 아니다 — `freshness` 음수는 "갓 나온 키워드는
    아직 검색 노출이 없다" 는 **진짜 발견** 일 수 있다. 가정을 코드에 박으면
    데이터가 말하는 것을 못 듣는다.
    """
    import inspect

    from JARVIS03_RADAR import learning

    src = inspect.getsource(learning.train_weights)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())   # 주석 제외
    assert "max(0.0, v)" not in code and "max(0., v)" not in code, (
        "음수 절단이 되살아났다 — 지배 피처가 삭제되어 정렬키가 상수가 된다")


def test_변별력_판정이_상대기준이다():
    """기준을 절대값으로 박으면 입력이 원래 뭉쳐 있을 때 영원히 거부한다(원칙②)."""
    import inspect

    from JARVIS03_RADAR import learning

    src = inspect.getsource(learning.train_weights)
    assert "_in_ratio" in src and "_out_ratio" in src, "변별력 판정이 없다"
    assert "_in_ratio" in src.split("_out_ratio <")[1][:60], (
        "출력 변별력을 *입력 대비* 로 판정해야 한다 — 절대 임계는 원칙② 위반")
