"""블로그 품질 학습의 *신호* 무결성 — 골든 테스트 (2026-08-08).

★ 별도 파일 이유: 다른 세션이 `test_publish_golden.py` 를 동시 수정 중이다.
★ 기계 독립: 운영 DB 를 읽지 않는다. 합성 행으로 판정 로직만 검사한다(ERRORS [568]).
"""
from __future__ import annotations

import ast
import inspect
import textwrap

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


# ══════════════════════════════════════════════════════════════════
# ③ 반복이 보상받지 않는다 — 상투구 감점
# ══════════════════════════════════════════════════════════════════
def test_판박이_도입부는_감점되고_신선한_것은_아니다():
    """★ 실측: 본문 첫 문단 감성 상투구 32.9% → 70.1%.

    기존 `_AI_OPEN` 은 `퇴근길에…` `요즘…` 을 전부 통과시키고 'AI 회피' 보너스를 줬다 —
    **반복이 보상받는 구조** 였다.
    """
    from JARVIS02_WRITER.post_scorer import repetition_penalty

    recent = ["요즘 뉴스만 틀면 이 종목 얘기가 나옵니다.",
              "요즘 증시가 출렁이면서 관심이 쏠립니다.",
              "퇴근길에 문득 시세를 확인했습니다.",
              "퇴근길 지하철에서 무심코 뉴스를 봤습니다.",
              "출근길에 라디오에서 들었습니다.",
              "장마철 습도가 공장 가동률을 바꿉니다."]
    stale = repetition_penalty("요즘 이 테마가 다시 움직이고 있습니다.", recent)
    fresh = repetition_penalty("작년 겨울 전력 수요가 기록을 갈아치웠습니다.", recent)
    assert stale > fresh, f"판박이({stale:.2f})가 신선한 것({fresh:.2f})보다 감점이 크지 않다"
    assert stale > 0, "판박이인데 감점 0"
    assert fresh == 0, f"신선한 도입부가 감점됐다 ({fresh:.2f})"


def test_상투구_목록을_코드에_박지_않는다():
    """금지어를 박으면 다음 상투구를 찾아낼 뿐이다 — 최근 글에서 파생해야 한다(원칙②)."""
    import inspect

    from JARVIS02_WRITER import post_scorer as ps

    # ★ AST 로 본다 — 주석만 걷어내면 **docstring 의 설명 문구**가 걸린다.
    #   이 저장소에서 같은 실수가 오늘만 세 번 났다(주석·지역변수·토큰공백).
    tree = ast.parse(textwrap.dedent(inspect.getsource(ps.repetition_penalty)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]                      # docstring 제거
    literals = {n.value for n in ast.walk(ast.Module(body=fn.body, type_ignores=[]))
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    joined = " ".join(literals)
    for w in ("퇴근길", "출근길", "주말", "장마"):
        assert w not in joined, f"상투구 '{w}' 가 코드 리터럴에 박혔다 — 두더지 잡기가 된다"


def test_기준선을_못_얻으면_감점하지_않는다():
    """기준선 부재는 글의 잘못이 아니다 — fail-open."""
    from JARVIS02_WRITER.post_scorer import repetition_penalty

    assert repetition_penalty("아무 문장", None) == 0.0
    assert repetition_penalty("아무 문장", []) == 0.0


# ══════════════════════════════════════════════════════════════════
# ④ 게이트 항등식 — 구조적 실패는 통과시키지 않는다
# ══════════════════════════════════════════════════════════════════
def test_구조적_실패는_학습을_통과시키지_않는다():
    """★ 실측: 학습 패턴 54개 중 49개(91%)가 LLM 판정 없이 등록됐고,
    사유 1위가 `No module named 'dotenv'` **19건** — LLM 실패가 아니라 환경 결함이다.
    재시도해도 같으므로 통과시키면 안 되고 드러내야 한다.
    """
    from JARVIS07_GUARDIAN.eval_agent import _conservative_pass

    broken = _conservative_pass("LLM 호출 실패", "No module named 'dotenv'")
    assert broken.should_register is False, "환경 결함인데 학습을 통과시켰다"
    assert broken.score == 0


def test_일시적_실패는_종전대로_통과한다():
    """LLM 스로틀·타임아웃으로 학습을 멈추면 그게 더 나쁘다 — 원래 의도를 지킨다."""
    from JARVIS07_GUARDIAN.eval_agent import _conservative_pass

    transient = _conservative_pass("LLM 판정 불가 (ok=False)", "")
    assert transient.should_register is True, "일시적 실패까지 막으면 학습이 멈춘다"


# ══════════════════════════════════════════════════════════════════
# ⑤ 잡 유예 — 주기에서 파생하되 발행은 건드리지 않는다
# ══════════════════════════════════════════════════════════════════
def test_주1회_학습잡의_유예가_주기에서_파생된다():
    """★ 실측: 주 1회 잡 9개가 전부 유예 1~2시간이었다. 2026-08-02(일) 02~06시
    잡 0건(노트북 수면)으로 3개가 통째로 유실됐고, 다음 기회가 **일주일 뒤** 였다
    (마지막 실행 07-26 — 13일 정지).
    """
    from JARVIS04_SCHEDULER.job_prereq import effective_grace

    for jid in ("train_weights", "auditor_weekly", "j07_vector_backfill"):
        g = effective_grace(jid)
        assert g >= 24 * 3600, f"{jid} 유예 {g}s — 주 1회 잡인데 하루도 안 된다"


def test_발행잡_유예는_늘어나지_않는다():
    """발행은 *시각이 계약* 이다 — 정책 A(놓친 슬롯 재발행 금지)와 정면 충돌한다."""
    from JARVIS04_SCHEDULER.job_prereq import effective_grace
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS

    for j in DEFAULT_JOBS:
        if j["id"] in ("j01_economic_post", "j01_theme_post_21"):
            assert effective_grace(j["id"]) <= 2 * 3600, (
                f"{j['id']} 유예가 늘었다 — 정규 시각이 아닌 때 발행될 수 있다")


# ══════════════════════════════════════════════════════════════════
# ⑥⑦ 학습 자산 백업 · 폴백 경로 주입
# ══════════════════════════════════════════════════════════════════
def test_학습자산이_백업에_동반된다():
    """`learned_patterns.json`·`bandit_state.json` 은 git 밖이라 사본이 0개였다."""
    import inspect

    from shared import db

    src = inspect.getsource(db.backup_db)
    assert "learned_*.json" in src and "_state.json" in src, (
        "학습 자산 JSON 이 백업 대상에서 빠졌다 — 파일 하나 날아가면 끝이다")
    assert "assets_" in src, "자산 백업 폴더 규칙이 없다"


def test_폴백_경로에도_학습지침이_주입된다():
    """주 경로가 실패해 폴백으로 떨어지면 **학습 0 상태로 발행** 되고 있었다."""
    from JARVIS02_WRITER.draft_writer import _build_section_system_msg

    msg = _build_section_system_msg("[헌법]", "tistory")
    import inspect

    from JARVIS02_WRITER import draft_writer as dw

    src = inspect.getsource(dw._build_section_system_msg)
    assert "_load_learn_insights" in src, (
        "폴백 공통 system 에 학습 지침 조달이 없다 — 4개 함수가 전부 학습 0 이 된다")
    assert "{_insights}" in src or "_insights" in msg or len(msg) > 0
