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
def test_학습자산이_백업에_동반된다(tmp_path):
    """`learned_patterns.json`·`bandit_state.json` 은 git 밖이라 사본이 0개였다.

    ★ **동작으로** 검사한다 — 소스 문자열 검사는 *이 테스트의 설명 주석* 에 속는다
      (실측: 초판이 그렇게 변이를 통과시켰다. 오늘 네 번째 같은 실수).
    ★ **합성 트리로** 검사한다 (2026-08-08 정정) — 초판은 실 저장소를 훑어
      `learned_patterns.json` 이 *내 맥북에 있다* 는 사실에 기댔다. 그 파일은
      `.gitignore` 대상이라 CI 엔 없고, 그래서 이 테스트는 GitHub Actions 에서
      깨졌다. 검사해야 할 것은 '내 컴퓨터에 파일이 있나' 가 아니라
      **'이 꼴의 파일을 백업 대상으로 집어내는가'** 다 (ERRORS [568] 과 같은 병).
    """
    from shared.db import learning_asset_files

    want = {
        "JARVIS07_GUARDIAN/learned_patterns.json",   # 자동수리 지문 원장
        "JARVIS07_GUARDIAN/bandit_state.json",       # 밴딧 학습 상태
        "JARVIS06_IMAGE/design_recipes.json",        # 이미지 디자인 학습
    }
    skip = {
        "JARVIS07_GUARDIAN/ERRORS.md",               # 학습 산출물이 아니다
        "JARVIS07_GUARDIAN/notes.txt",
        "JARVIS02_WRITER/learned_patterns.json",     # 소유 폴더 밖 — 대상 아님
    }
    for rel in want | skip:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")

    got = {str(q.relative_to(tmp_path)) for q in learning_asset_files(root=tmp_path)}
    assert want <= got, f"학습 자산이 백업 대상에서 빠졌다: {want - got}"
    assert not (skip & got), f"백업 대상이 아닌 것이 섞였다: {skip & got}"


def test_백업자산_탐색이_실저장소에서도_돈다():
    """합성 트리 검사가 *운영 경로* 까지 보장하지는 않는다 — 인자 없는 호출도 확인한다.

    파일 존재는 머신마다 다르므로 **존재를 단정하지 않는다.** 대신 규약만 본다:
    반환은 리스트, 모든 항목은 실존 파일, 소유 폴더 안, 패턴에 부합.
    """
    import fnmatch
    from pathlib import Path

    from shared.db import (LEARNING_ASSET_DIRS, LEARNING_ASSET_PATTERNS,
                           learning_asset_files)

    root = Path(__file__).resolve().parent.parent
    for q in learning_asset_files():
        assert q.is_file(), f"존재하지 않는 경로가 섞였다: {q}"
        assert q.parent.name in LEARNING_ASSET_DIRS, f"소유 폴더 밖: {q}"
        assert any(fnmatch.fnmatch(q.name, pat) for pat in LEARNING_ASSET_PATTERNS), \
            f"패턴에 없는 파일: {q.name}"
        assert q.is_relative_to(root), f"저장소 밖: {q}"


def test_백업잡이_자산목록을_실제로_부른다():
    """분리한 함수를 백업 잡이 안 부르면 파일은 여전히 사본 0개다."""
    import ast
    import inspect
    import textwrap

    from shared import db

    tree = ast.parse(textwrap.dedent(inspect.getsource(db.backup_db)))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "learning_asset_files" in called, "백업 잡이 학습 자산 목록을 부르지 않는다"


def test_폴백_경로에도_학습지침이_주입된다(monkeypatch):
    """주 경로가 실패해 폴백으로 떨어지면 **학습 0 상태로 발행** 되고 있었다.

    ★ 동작으로 검사 — 대역을 심어 그 값이 실제 프롬프트에 나타나는지 본다.
    """
    from JARVIS02_WRITER import draft_writer as dw

    MARK = "◇지침대역◇"
    monkeypatch.setattr(dw, "_load_learn_insights", lambda *a, **k: MARK)
    msg = dw._build_section_system_msg("[헌법]", "tistory")
    assert MARK in msg, "폴백 공통 system 에 학습 지침이 실리지 않는다"


# ══════════════════════════════════════════════════════════════════
# ⑧ 네이버 글별 조회수 — 관리자 통계 (2026-08-08)
# ══════════════════════════════════════════════════════════════════
def test_공개페이지_스크래핑으로_돌아가지_않는다():
    """★ 공개 페이지엔 조회수가 **없다** — 실측: m.blog 응답 10만자 안에
    `조회`·`visitorCount`·`viewCount` 각 **0회**. 패턴을 고쳐도 없는 값은 못 찾는다.
    ERRORS 가 두 번 기각한 길이므로 되돌아가면 안 된다.
    """
    import inspect

    from JARVIS03_RADAR import performance_collector as pc

    src = inspect.getsource(pc._collect_naver_views)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "PostView.naver" not in code and "m.blog.naver.com" not in code, (
        "공개 페이지 스크래핑으로 회귀했다 — 그 경로엔 조회수가 존재하지 않는다")
    assert "_NV_BATCH_CACHE" in code, "배치 캐시를 쓰지 않는다"


def test_조회수를_위치가_아니라_레이블로_읽는다():
    """통계 페이지엔 조회수·공감수·댓글수가 섞여 나온다.
    "숫자 N번째" 로 집으면 네이버가 항목 하나만 추가해도 **엉뚱한 값이 학습에 들어간다**.
    """
    from JARVIS03_RADAR.performance_collector import _NV_DAILY_ROW

    page = ("조회수\n날짜 조회수\n"
            "2026.08.08. (토) 0\n2026.08.07. (금) 1\n2026.08.06. (목) 4\n"
            "공감수\n0\n댓글수\n0\n단위 : 건\n")
    vals = [int(v.replace(",", "")) for v in _NV_DAILY_ROW.findall(page)]
    assert vals == [0, 1, 4], f"일별 행만 뽑아야 하는데 {vals}"
    assert sum(vals) == 5


def test_logNo_추출이_URL_형태에_흔들리지_않는다():
    """발행 URL 은 `?fromRss=true&trackingCode=rss` 가 붙어 온다."""
    from JARVIS03_RADAR.performance_collector import _naver_log_no

    assert _naver_log_no(
        "https://blog.naver.com/youandi3535/224371775209?fromRss=true") == "224371775209"
    assert _naver_log_no(
        "https://blog.naver.com/PostView.naver?blogId=x&logNo=224369275563") == "224369275563"
    assert _naver_log_no("https://blog.naver.com/youandi3535") == ""


def test_미수집을_조회0으로_단정하지_않는다():
    """★ 이번 감사의 핵심 교훈 — **0 은 '나빴다' 이지 '모른다' 가 아니다.**
    통계 페이지가 로그인 만료 등으로 안 열리면 캐시에 안 담기고,
    학습 쪽(`build_target`)이 그 결측을 제외한다.
    """
    import inspect

    from JARVIS03_RADAR import performance_collector as pc

    src = inspect.getsource(pc._collect_naver_stats_batch)
    assert '"조회수" not in text' in src or "'조회수' not in text" in src, (
        "페이지가 열리지 않았을 때 0 으로 단정하지 않는 가드가 없다")


# ══════════════════════════════════════════════════════════════════
# ⑨ 토큰 장부 — `model` 이 비면 모델 교체 전후 비교가 불가능해진다
# ══════════════════════════════════════════════════════════════════
def test_sdk_경로가_모델을_장부에_남긴다():
    """★ `sdk_query` 경로만 `model=""` 이 코드에 박혀 있었다 (ERRORS [592]).

    값이 없어서가 아니라 **안 넘겨서** 비었다 — `run_sdk_query` 는 같은 함수 안에서
    이미 `shared.llm.model_id()` 로 모델을 정하고 SDK 옵션엔 제대로 실어 보내면서
    장부에만 빈 문자열을 적었다. 그 경로가 전체 캐시 읽기의 절반을 쓴다.
    """
    import shared.claude_sdk_compat as sc
    import shared.token_usage as tu

    seen: dict = {}
    orig = tu.record_call
    tu.record_call = lambda **kw: seen.update(kw)
    try:
        sc._record_sdk_usage({"usage": {}, "cost": 0, "dur": 0, "turns": 3},
                             ok=True, model="test-model-xyz")
    finally:
        tu.record_call = orig
    assert seen.get("model") == "test-model-xyz", \
        f"모델이 장부까지 흘러가지 않는다: {seen.get('model')!r}"


def test_sdk_호출부가_모델을_실제로_넘긴다():
    """헬퍼가 받을 준비만 하고 호출부가 안 넘기면 여전히 빈다 — 배선을 AST 로 본다."""
    import ast
    import inspect

    import shared.claude_sdk_compat as sc

    tree = ast.parse(inspect.getsource(sc.run_sdk_query).lstrip())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_record_sdk_usage"]
    assert calls, "run_sdk_query 가 계측을 부르지 않는다"
    for c in calls:
        assert any(k.arg == "model" for k in c.keywords), \
            "계측 호출에 model 인자가 없다 — 장부의 model 이 빈 채로 쌓인다"


def test_소급분은_실측과_구분되어_기록된다():
    """소급 추정치를 실측처럼 적으면 다음 사람이 그것을 실측으로 믿는다.

    ★ 값이 아니라 **규약** 을 검사한다 — 로컬 DB 의 행 수를 세면 CI 에서 무의미해진다
      (ERRORS [587] 과 같은 병).
    """
    import ast
    import inspect

    import shared.token_usage as tu

    src = inspect.getsource(tu.backfill_missing_model)
    tree = ast.parse(src.lstrip())
    lits = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    joined = " ".join(lits)
    assert "model_origin" in joined, "소급 표시 컬럼을 쓰지 않는다"
    assert "backfill_git" in joined, "소급분에 출처 표시를 남기지 않는다"
    # dry_run 기본값이 True 여야 실수로 DB 를 고치지 않는다
    sig = inspect.signature(tu.backfill_missing_model)
    assert sig.parameters["dry_run"].default is True, "기본값이 dry_run 이 아니다 — 사고 위험"


# ══════════════════════════════════════════════════════════════════
# ⑩ 발행 전 쿠키 게이트 — 무인 실행에서 사람을 기다리지 않는다 (ERRORS [593])
# ══════════════════════════════════════════════════════════════════
def test_무인이면_캡차를_기다리지_않는다():
    """★ 2026-08-09 07:00 경제 브리핑 미발행의 직접 원인.

    네이버가 CAPTCHA/기기 인증을 요구했고 코드가 "화면에서 직접 풀어주세요" 라며
    120초를 기다렸다. 새벽 7시 예약 실행에 화면 앞에 사람이 있을 리 없다 —
    기다림은 발행 창만 먹고 결과를 바꾸지 못한다(잡 소요 163초 실측).

    ★ 판정은 새 플래그가 아니라 `current_job_id()` 에서 파생한다(② 동적 설계).
    """
    from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import human_wait_sec

    # ★ 2026-08-10 정정 — 종전 이 테스트는 `gate("j01_...", lambda: ...)()` 로 확인했다.
    #   `gate` 는 람다를 **호출한 그 스레드에서** 실행하므로 threading.local 문맥이
    #   유일하게 살아 있는 조건이다. 운영은 정반대다 — 발행은 subprocess, 인시던트
    #   재시도는 새 스레드라 문맥이 비어 '사람이 있다'로 오판했다.
    #   그래서 테스트는 초록인데 08-10 07:00 에 120초 대기를 4회(482초) 버렸다.
    #   이제 판정 근거가 TTY 이므로, *사고가 실제로 나는 경로* 로 검증한다.
    assert human_wait_sec() == 0, (
        "pytest 는 stdout 을 캡처한다(=TTY 아님) — 무인으로 판정해야 한다")

    # 사람이 붙어 있음을 명시하면 기다려야 한다 (양방향 검증)
    import os as _os
    _os.environ["JARVIS_VERBOSE"] = "1"
    try:
        assert human_wait_sec() > 0, "대화형인데 기다리지 않는다"
    finally:
        _os.environ.pop("JARVIS_VERBOSE", None)


def test_로그인_실패_타입이_사유에서_파생된다():
    """중앙 매핑표를 만들지 않는다 — 새 사유가 생기면 타입이 자동으로 따라온다."""
    from JARVIS08_PUBLISH.credentials.login_manager import login_error_type
    from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import naver_login_error_type

    assert naver_login_error_type("captcha_unattended") == "NaverLoginCaptchaUnattended"
    assert naver_login_error_type("login_no_redirect") == "NaverLoginLoginNoRedirect"
    # 미지 사유도 뭉개지 않고 이름을 만든다 (ERRORS [547] — 뭉뚱그린 타입 금지)
    assert naver_login_error_type("brand_new_reason") == "NaverLoginBrandNewReason"
    assert naver_login_error_type("") == "NaverLoginUnknown"

    # ★ 승격된 플랫폼 중립 이름도 같은 값을 만든다 — 갈라지면 GUARDIAN 분류가 둘로 쪼개진다.
    #   (네이버 전용 이름은 severity·기존 테스트가 잡고 있어 *위임으로* 남아야 한다 — R7)
    for r in ("captcha_unattended", "brand_new_reason", ""):
        assert login_error_type("naver", r) == naver_login_error_type(r)
    # ③ 4조합 — 티스토리도 같은 규칙으로 파생된다(사본 없음)
    assert login_error_type("tistory", "human_timeout") == "TistoryLoginHumanTimeout"


def test_캡차_분기가_사유를_남긴다():
    """실패가 bool 로만 돌아오면 호출자는 '왜' 를 모른다 — 08-09 사고가 그랬다.

    ★ 반환형(bool)은 바꾸지 않는다: 호출자가 13곳이라 하나라도 놓치면 조용히 깨진다.
      사유는 옆문(`last_login_failure`)으로 노출한다.
    """
    import ast
    import inspect

    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    src = inspect.getsource(nc.refresh_naver_cookies)
    tree = ast.parse(src.lstrip())
    reasons = {n.args[0].value for n in ast.walk(tree)
               if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_fail"
               and n.args and isinstance(n.args[0], ast.Constant)}
    assert "captcha_unattended" in reasons, "무인 CAPTCHA 사유를 남기지 않는다"
    # 사유 없이 그냥 False 로 빠지는 출구가 남아 있으면 다음에 또 '재현해야 안다'
    # ★ 허용치를 두지 않는다 — 처음엔 `<= 2` 로 뒀다가 변이 시험에서 **한 곳을 되살려도
    #   통과**하는 것을 봤다. 느슨한 허용치는 회귀를 그만큼 통과시킨다.
    bare = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Constant) and n.value.value is False]
    assert not bare, (f"사유 없이 빠지는 출구가 {len(bare)}곳({bare}) — `_fail(사유)` 로 바꿀 것. "
                      f"사유가 없으면 다음에도 '재현해 봐야 아는' 사고가 된다")


def test_쿠키게이트_실패가_오류원장에_박제된다(monkeypatch):
    """★ 종전엔 로그·텔레그램뿐이라 **자동수리·학습·감사 어디에도 안 들어갔다.**
    08-09 07:00 사고의 error_log 가 0건이었던 이유다(실측).

    경제·테마 공통 지점 하나만 검사한다 — 그 함수가 4조합 전부를 덮기 때문(③).
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_sched_t", root / "JARVIS02_WRITER" / "scheduler.py")
    sched = importlib.util.module_from_spec(spec)
    sys.modules["_sched_t"] = spec.name and sched
    spec.loader.exec_module(sched)

    import JARVIS08_PUBLISH.credentials.login_manager as lm
    import JARVIS08_PUBLISH.credentials.naver_cookie_refresher as nc
    from JARVIS07_GUARDIAN import error_collector as ec

    reported: list = []
    monkeypatch.setattr(ec, "report", lambda *a, **k: reported.append((a, k)))
    monkeypatch.setattr(lm, "ensure_naver_ready", lambda deadline=None: (False, "permanent"))
    monkeypatch.setattr(nc, "_LAST_FAILURE", "captcha_unattended", raising=False)
    monkeypatch.setattr(sched, "send_telegram", lambda *a, **k: None)

    assert sched._naver_cookie_ready("경제 브리핑") is False, "실패인데 발행을 진행한다"
    assert reported, "게이트 실패가 오류 원장에 박제되지 않는다"
    assert reported[0][0][0] == "NaverLoginCaptchaUnattended", \
        f"뭉뚱그린 타입으로 박제됐다: {reported[0][0][0]}"


# ══════════════════════════════════════════════════════════════════
# ⑪ 쿠키 파일 부재 — 조용히 지나가지 않는다 (ERRORS [594])
# ══════════════════════════════════════════════════════════════════
def test_사전점검_실패가_사람과_원장_양쪽에_간다(monkeypatch, tmp_path):
    """★ 종전엔 `log.warning` 한 줄이 전부였다.

    그래서 `naver_cookies.pkl` 이 사라진 채로 **두 회차가 조용히 지나갔다**
    (08-08 21:00 테마 실패 28초 · 08-09 07:00 경제 실패 163초).
    쿠키가 없으면 매 발행이 *전체 로그인* 이 되고 그때마다 CAPTCHA 확률에 노출된다 —
    이 경고는 "곧 발행이 깨진다" 는 예고인데 아무도 듣지 못했다.
    """
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    import JARVIS08_PUBLISH.credentials.naver_cookie_refresher as nc
    import shared.notify as notify
    from JARVIS07_GUARDIAN import error_collector as ec

    sent: list = []
    reported: list = []
    monkeypatch.setattr(notify, "send_tg", lambda m, **k: sent.append(m))
    monkeypatch.setattr(ec, "report", lambda *a, **k: reported.append(a))
    monkeypatch.setattr(lm, "NAVER_COOKIE_PATH", tmp_path / "none.pkl")
    monkeypatch.setattr(lm, "_COOKIE_WATCH", tmp_path / "watch.json")
    monkeypatch.setattr(lm, "auto_refresh_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(lm, "verify_all_logins", lambda platforms=("naver", "tistory"): {
        "naver": {"ok": False, "issues": ["쿠키 파일 없음 또는 빈 list"], "cookie_age_h": 1e9}})
    # ★ 이 테스트는 "백오프와 무관하게 쿠키가 없는" 시나리오를 검증한다 — 저장소의
    #   *실제* login_backoff.json(캡차로 활성 상태일 수 있음)이 새어 들어오지 않도록
    #   명시적으로 "백오프 없음"을 고정한다(2026-08-12, [630] 후속 — 격리 결함 수정).
    # ★ 패치 대상은 **상태의 주인**이다 (2026-08-13 플랫폼 중립 승격 후 정정).
    #   종전엔 `nc.login_backoff_active_reason` 를 패치했는데, 백오프 상태기가
    #   `login_manager` 로 승격된 뒤로 그 패치는 *아무 데도 걸리지 않았다* —
    #   저장소에 백오프 파일이 없는 동안만 우연히 초록이었다(무력한 monkeypatch).
    monkeypatch.setattr(lm, "login_backoff_active_reason", lambda *_a, **_k: "")
    assert lm.login_backoff_active_reason("naver") == "", "패치가 먹지 않았다"

    lm.job_pre_publish_check()

    assert sent, "사전점검 실패가 사람에게 안 간다"
    assert reported, "사전점검 실패가 오류 원장에 안 남는다"
    assert reported[0][0] == "PrecheckNaverCookieMissing", \
        f"뭉뚱그린 타입으로 박제됐다: {reported[0][0]}"


def test_사전점검_백오프_중이면_사람필요_타입으로_분류된다(monkeypatch, tmp_path):
    """★ [630] — precheck 경로가 harness·scheduler 두 경로와 달리 백오프를 몰라서

    캡차 백오프 창마다 `PrecheckNaverCookieMissing`(코드 버그처럼 보이는 타입)으로
    오탐 리페어 티켓을 냈다. 이슈 문구가 "쿠키 파일 없음" 이어도 백오프 중이면
    `naver_login_error_type()` 파생 타입(사람이 필요함을 나타냄)으로 나가야 한다.
    """
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    import JARVIS08_PUBLISH.credentials.naver_cookie_refresher as nc
    import shared.notify as notify
    from JARVIS07_GUARDIAN import error_collector as ec
    from JARVIS07_GUARDIAN.severity import is_transient

    reported: list = []
    monkeypatch.setattr(notify, "send_tg", lambda m, **k: None)
    monkeypatch.setattr(ec, "report", lambda *a, **k: reported.append(a))
    monkeypatch.setattr(lm, "NAVER_COOKIE_PATH", tmp_path / "none.pkl")
    monkeypatch.setattr(lm, "_COOKIE_WATCH", tmp_path / "watch.json")
    monkeypatch.setattr(lm, "auto_refresh_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(lm, "verify_all_logins", lambda platforms=("naver", "tistory"): {
        "naver": {"ok": False, "issues": ["쿠키 파일 없음 또는 빈 list"], "cookie_age_h": 1e9}})
    # ★ 패치 대상은 **상태의 주인** (2026-08-13 — 백오프 상태기가 login_manager 로 승격).
    #   승격 후에도 `nc.login_backoff_active_reason` 를 패치하면 실제 판정 경로에는
    #   닿지 않는다 — 그 무력한 패치가 이 테스트를 빨갛게 만든 원인이었다(R6).
    monkeypatch.setattr(lm, "login_backoff_active_reason",
                        lambda *_a, **_k: "captcha_unattended")
    assert lm.login_backoff_active_reason("naver") == "captcha_unattended", "패치가 먹지 않았다"

    lm.job_pre_publish_check()

    assert reported, "사전점검 실패가 오류 원장에 안 남는다"
    et = reported[0][0]
    assert et == "NaverLoginCaptchaUnattended", f"백오프 중인데 뭉뚱그린 타입: {et}"
    assert is_transient(et, source="publish") is True, \
        "사람이 필요한 타입인데 Tier-2 낭비 대상으로 남는다"

    # ★ ③ 4조합 — 같은 상황이 티스토리에서도 사람 필요 타입으로 나가야 한다.
    #   (플랫폼 가드가 되살아나면 티스토리만 다시 코드-버그 타입으로 떨어진다)
    #   (GUARDIAN 쪽 분류 대칭은 tests/test_login_notice_contract.py 가 따로 본다)
    ts_type = lm.precheck_error_type("tistory", ["env TS_COOKIE 없음"], "captcha_unattended")
    assert ts_type == "TistoryLoginCaptchaUnattended", \
        f"티스토리 백오프가 사람 필요 타입으로 안 나간다: {ts_type}"


def test_사전점검_정상이면_조용하다(monkeypatch, tmp_path):
    """경보가 늘 울리면 아무도 안 듣는다 — 정상일 때는 침묵해야 한다."""
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    import shared.notify as notify
    from JARVIS07_GUARDIAN import error_collector as ec

    sent: list = []
    monkeypatch.setattr(notify, "send_tg", lambda m, **k: sent.append(m))
    monkeypatch.setattr(ec, "report", lambda *a, **k: sent.append(("report",) + a))
    monkeypatch.setattr(lm, "auto_refresh_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(lm, "verify_all_logins", lambda platforms=("naver", "tistory"): {
        "naver": {"ok": True, "issues": [], "cookie_age_h": 1.0},
        "tistory": {"ok": True, "issues": []}})

    lm.job_pre_publish_check()
    assert not sent, f"정상인데 경보가 울린다: {sent}"


def test_소실_추적이_사라진_시점을_좁힌다(monkeypatch, tmp_path):
    """"언제 사라졌나" 를 못 말하면 원인 추적이 불가능하다 — 08-09 사고가 그랬다."""
    import JARVIS08_PUBLISH.credentials.login_manager as lm

    ck = tmp_path / "naver_cookies.pkl"
    ck.write_bytes(b"x")
    monkeypatch.setattr(lm, "NAVER_COOKIE_PATH", ck)
    monkeypatch.setattr(lm, "_COOKIE_WATCH", tmp_path / "watch.json")

    first = lm.record_cookie_sighting()
    assert first["present"] and not first["vanished"]
    assert "현재 존재" in lm.cookie_loss_window(), "있는데 사라졌다고 말한다"

    ck.unlink()                                   # 사라짐
    second = lm.record_cookie_sighting()
    assert second["vanished"], "사라진 것을 감지하지 못한다"
    assert second["last_seen"] == first["at"], "마지막 관측 시각을 잃어버린다"
    assert "사라졌다" in lm.cookie_loss_window(), "사라짐을 알리지 않는다"


def test_부재_경보의_타입이_이슈에서_파생된다():
    """중앙 매핑표를 만들지 않는다 — 새 이슈가 생기면 타입이 자동으로 따라온다."""
    from JARVIS08_PUBLISH.credentials.login_manager import precheck_error_type

    assert precheck_error_type("naver", ["쿠키 파일 없음 또는 빈 list"]) == "PrecheckNaverCookieMissing"
    assert precheck_error_type("naver", ["쿠키 만료 임박 (12.0h > 10h)"]) == "PrecheckNaverCookieStale"
    assert precheck_error_type("tistory", ["env TS_COOKIE 누락"]) == "PrecheckTistoryEnvMissing"
    # ★ 실유효성 판정(cookie_valid_http) 문구 — PrecheckTistoryUnknown 회귀 방지
    #   (2026-08-09, "만료 임박" 이 아니라 "쿠키 만료" 로 갈아탄 뒤 Unknown 으로 떨어졌던 사고).
    assert precheck_error_type(
        "naver", ["쿠키 만료 — 실제 요청이 로그아웃 상태를 보고"]) == "PrecheckNaverCookieExpired"
    assert precheck_error_type(
        "tistory", ["쿠키 만료 — manage 접근이 로그인으로 리다이렉트"]) == "PrecheckTistoryCookieExpired"


# ══════════════════════════════════════════════════════════════════
# ⑫ 티스토리도 네이버와 대칭으로 검증한다 (ERRORS [596] — 원칙③)
# ══════════════════════════════════════════════════════════════════
def test_로그인_리다이렉트_규칙이_한곳이다():
    """★ 같은 판정이 두 곳(셀레니움·HTTP)에 필요해졌다 — 규칙을 복제하면 한쪽만 고쳐진다."""
    import ast
    import inspect

    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    assert tc.is_login_redirect("https://www.tistory.com/auth/login?x")
    assert tc.is_login_redirect("https://accounts.kakao.com/login")
    assert not tc.is_login_redirect("https://blog.tistory.com/manage/newpost/")

    # 셀레니움 경로가 규칙을 *베끼지 않고* 이 함수를 부르는가
    src = inspect.getsource(tc.check_cookie_valid)
    tree = ast.parse(src.lstrip())
    calls = {getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "is_login_redirect" in calls, "셀레니움 경로가 판정 규칙을 따로 갖고 있다"
    lits = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "/auth/login" not in lits, "규칙 문자열 사본이 남아 있다"


def test_티스토리_만료를_사전점검이_잡는다(monkeypatch):
    """★ 종전엔 env '존재' 만 봐서 **만료돼도 ✅** 였다.

    그래서 08-08 20:30 사전점검이 초록인 채로 21:00 테마 발행이 28초 만에
    로그인 화면으로 튕겨 끝났다(실측).

    ★ 정정 (적대적 검증) — 초판 서술 "네이버는 실검증을 하는데 티스토리만 안 했다" 는
      **거짓이었다.** 그때는 네이버 분기도 HTTP 요청이 0회였다(env·파일·mtime 셋만).
      비대칭이 아니라 **양쪽 다 존재 확인**이었고, 네이버는 별도 커밋에서 고쳐졌다.
      확인하지 않은 단정을 도크스트링에 쓰지 말 것.
    """
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    monkeypatch.setenv("TS_URL", "https://x.tistory.com")
    monkeypatch.setenv("TS_USERNAME", "u")
    monkeypatch.setenv("TS_PASSWORD", "p")
    monkeypatch.setenv("TS_COOKIE", "z" * 40)

    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (False, "empty") if detail else False)
    v = lm.verify_all_logins(platforms=("tistory",))
    assert not v["tistory"]["ok"], "만료된 쿠키인데 사전점검이 통과시킨다"

    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (True, "ok") if detail else True)
    assert lm.verify_all_logins(platforms=("tistory",))["tistory"]["ok"]

    # ★ '모른다'(None)를 '만료'로 적지 않는다 — 거짓 경보 금지
    # ★ 순단(network) — 아무 것도 하면 안 된다. 순단마다 로그인하면 캡차를 부른다.
    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (None, "network") if detail else None)
    assert lm.verify_all_logins(platforms=("tistory",))["tistory"]["ok"], \
        "판정 불가를 만료로 단정한다 — 네트워크 순단마다 거짓 경보가 된다"


def test_티스토리_만료면_자동갱신이_돈다(monkeypatch):
    """★ 종전엔 '쿠키 없음' 일 때만 갱신했다 — 있는데 만료면 아무 일도 안 했다.

    08-08 21:00 실패 때 TS_COOKIE 는 있었고(40자) 만료였다. 절반만 고친 조건이었다.
    """
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    calls: list = []
    monkeypatch.setattr(lm, "refresh_tistory_cookies", lambda force=False: calls.append(force) or True)
    monkeypatch.setenv("TS_COOKIE", "z" * 40)

    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (True, "ok") if detail else True)
    lm.auto_refresh_if_needed(platforms=("tistory",))
    assert not calls, "유효한데 갱신을 시도한다 — 불필요한 로그인은 CAPTCHA 위험을 부른다"

    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (False, "empty") if detail else False)
    lm.auto_refresh_if_needed(platforms=("tistory",))
    assert calls, "만료인데 갱신하지 않는다 — 발행 시각에 그대로 튕긴다"

    calls.clear()
    # ★ 순단(network) — 아무 것도 하면 안 된다. 순단마다 로그인하면 캡차를 부른다.
    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda timeout=8.0, detail=False: (None, "network") if detail else None)
    lm.auto_refresh_if_needed(platforms=("tistory",))
    assert not calls, "판정 불가인데 갱신한다 — 네트워크 순단마다 로그인하면 CAPTCHA 를 부른다"


def test_캡차_판정이_낱말이_아니라_요소다():
    """★ 오늘 내가 만든 회귀를 막는다 (ERRORS [595]).

    종전 판정: `"captcha" in src.lower() or "보안" in src or "기기" in src`.
    실측 — 캡차가 **없는** 평상시 로그인 페이지(19,620자)에 `captcha` 7회 · `보안` 2회.
    **항상 참이다.** 그래서 그 분기는 '캡차 감지' 가 아니라 사실상 '더 기다리기' 였고,
    내가 그 대기를 무인일 때 0 으로 만들자 *느린 정상 로그인* 까지 죽는 회귀가 됐다.
    """
    import ast
    import inspect

    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    src = inspect.getsource(nc.refresh_naver_cookies)
    tree = ast.parse(src.lstrip())
    calls = {getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "captcha_present" in calls, "캡차 판정을 요소로 하지 않는다"

    # 낱말 판정이 되살아났는지 — 주석은 제외하고 *실행되는 문자열* 만 본다
    lits = {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for bad in ("captcha", "보안", "기기"):
        assert bad not in lits, (
            f"낱말 판정 문자열 {bad!r} 이 실행 경로에 있다 — 캡차 없는 페이지에도 매칭된다")

    # ★ 판정은 3-상태여야 한다 (ERRORS [606]) — `is True` 로만 '캡차 확실' 을 받는다.
    #   초판은 truthy 검사라 None(모름)까지 '캡차' 로 읽었다… 가 아니라 그 반대였다:
    #   선택자에 안 걸리면 False(=캡차 아님)를 **단정** 해, 진짜 캡차를 놓치고도
    #   로그에 "캡차 요소 없음" 이라는 거짓을 남겼다. 모름은 모름으로 다뤄야 한다.
    assert "captcha_present(driver) is True" in src, \
        "캡차 판정을 3-상태로 받지 않는다 — '모름' 을 '아님' 으로 단정하게 된다"

    # 판정이 멈춘 화면을 증거로 남기는가 — 추측을 고칠 재료가 없으면 같은 미탐이 반복된다
    assert "capture_login_stuck" in calls, "로그인 정지 화면을 저장하지 않는다"


def test_캡차_판정이_모름을_아님으로_단정하지_않는다():
    """★ 2026-08-09 실사고 — 진짜 캡차가 떴는데 감지기가 놓쳤다(미탐).

    선택자를 **실제 캡차 화면을 한 번도 보지 않고 추측** 으로 만든 탓이다.
    그런데 로그에는 `캡차 요소 없음` 이라는 *확신에 찬 거짓* 이 남았다 —
    사용자가 손으로 풀어 성공한 것을 시스템이 자기 판정 덕이라 오해하게 만든다.
    """
    import inspect

    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    # 아무것도 못 찾는 드라이버 대역 → False 가 아니라 None 이어야 한다
    class _Blind:
        def find_elements(self, *a, **k):
            return []

    assert nc.captcha_present(_Blind()) is None, \
        "못 찾았는데 '캡차 아님' 을 단정한다 — 미탐을 확신으로 보고하게 된다"

    # 예외가 나도 마찬가지
    class _Broken:
        def find_elements(self, *a, **k):
            raise RuntimeError("driver dead")

    assert nc.captcha_present(_Broken()) is None, "판정 실패를 '아님' 으로 단정한다"

    src = inspect.getsource(nc.captcha_present)
    assert "return False" not in src, "False(=캡차 아님)를 단정하는 경로가 남아 있다"


def test_캡차가_아니면_무인도_기다린다():
    """'사람이 필요한 시간' 과 '로그인이 끝나는 시간' 은 다른 것이다 — 섞으면 회귀가 난다."""
    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    assert nc.LOGIN_REDIRECT_WAIT_SEC > 0, "무인 로그인 대기 예산이 0 이면 느린 로그인이 죽는다"

    assert nc.human_wait_sec() == 0, "무인인데 사람을 기다린다"
    # 캡차가 아닐 때 쓰는 예산은 무인 여부와 무관해야 한다
    assert nc.LOGIN_REDIRECT_WAIT_SEC == nc.LOGIN_REDIRECT_WAIT_SEC


def test_네이버도_실효를_검증한다(monkeypatch):
    """★ 내가 "네이버와 대칭" 이라 주석에 썼는데 **그때는 거짓이었다** (ERRORS [597]).

    네이버 분기도 '파일이 있고 신선한가' 만 봤다. 실측 근거 — 두 사전점검이 둘 다 초록:
      08-08 20:30 precheck ok=1  → 21:00 테마  ok=0 (28초, 로그인 튕김)
      08-09 06:30 precheck ok=1  → 07:00 경제  ok=0 (163초, CAPTCHA)
    `check_cookie_valid()` 는 이미 있었다 — 안 부르고 있었을 뿐이다.
    """
    import JARVIS08_PUBLISH.credentials.login_manager as lm
    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    # ★ `.env` 에 기대지 않는다 — 없는 트리(CI·새 체크아웃)에선 env 누락으로 막혀
    #   검증 자체가 안 돌고, 그러면 이 테스트가 '내 맥북에서만' 이 된다 (ERRORS [587]).
    for k in lm._REQUIRED_ENV["naver"]:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(lm, "get_naver_cookies", lambda: [{"name": "NID_AUT"}])
    monkeypatch.setattr(lm, "naver_cookie_age_hours", lambda: 1.0)

    # ★ 판정 함수가 `check_cookie_valid`(2-상태) → `cookie_valid_http`(3-상태) 로 바뀌었다
    #   (2026-08-09, 동시 세션 합의). 전자는 *갱신 여부* 판단용이라 네트워크 오류에 True 를
    #   돌려줘 '유효' 와 '판정 불가' 가 섞인다. 건강진단은 그 둘을 구분해야 한다.
    monkeypatch.setattr(nc, "cookie_valid_http", lambda *a, **k: True)
    assert lm.verify_all_logins(platforms=("naver",))["naver"]["ok"]

    monkeypatch.setattr(nc, "cookie_valid_http", lambda *a, **k: False)
    r = lm.verify_all_logins(platforms=("naver",))["naver"]
    assert not r["ok"], "실제 요청이 로그인 상태를 부정하는데 사전점검이 통과시킨다"
    assert any("만료" in i for i in r["issues"])

    # ★ '모른다'(None)를 '만료' 로 적지 않는다 — DNS 순단마다 거짓 경보가 나면 안 된다
    #   (오늘 실측: RADAR 실패 264건 중 263건이 DNS 이름풀이 실패).
    monkeypatch.setattr(nc, "cookie_valid_http", lambda *a, **k: None)
    r2 = lm.verify_all_logins(platforms=("naver",))["naver"]
    assert not any("만료" in i for i in r2["issues"]), \
        f"판정 불가를 만료로 적는다 — 거짓 경보: {r2['issues']}"


def test_네트워크_문제를_만료로_적지_않는다(monkeypatch):
    """DNS 순단(오늘 263건)마다 '쿠키 만료' 경보가 나면 아무도 안 듣게 된다.

    ★ 네이버·티스토리가 '판정 불가' 를 다르게 인코딩한다 — 네이버는 True, 티스토리는 None.
      인코딩은 달라도 **'모른다' 를 '만료' 로 적지 않는다** 는 규칙은 같아야 한다.
    """
    import ast
    import inspect

    import JARVIS08_PUBLISH.credentials.login_manager as lm
    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    # ★ 대역으로 갈아끼우기 **전에** 원본 소스를 잡는다 — 안 그러면 가짜 함수를 뜯어본다
    #   (초판이 정확히 그 실수를 했고, 테스트가 자기 대역을 검사하며 실패했다).
    # ★ 대역으로 갈아끼우기 **전에** 원본 소스를 잡는다 — 안 그러면 가짜 함수를 뜯어본다.
    #   그리고 **건강진단이 실제로 부르는 함수** 를 갈아야 한다. 초판은 `check_cookie_valid`
    #   를 갈았는데 코드는 `cookie_valid_http` 를 부르므로 단언이 **공허하게 통과**했다
    #   (오늘 세 번째 같은 실수 — 대역이 소비자와 다른 심볼을 겨눈 경우).
    real_src = inspect.getsource(nc.cookie_valid_http)

    for k in lm._REQUIRED_ENV["naver"]:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(lm, "get_naver_cookies", lambda: [{"name": "NID_AUT"}])
    monkeypatch.setattr(lm, "naver_cookie_age_hours", lambda: 1.0)

    def _boom(*a, **k):
        raise RuntimeError("DNS 실패")

    monkeypatch.setattr(nc, "cookie_valid_http", _boom)
    assert lm.verify_all_logins(platforms=("naver",))["naver"]["ok"], \
        "판정이 예외로 죽었는데 '만료' 로 단정한다"

    # ★ 3-상태 계약 — 네트워크 예외에서 **None**(판정 불가)이어야 한다.
    #   False(만료)로 돌려주면 DNS 순단마다 거짓 경보가 나고, 더 나쁘게는 불필요한
    #   재로그인을 유발해 CAPTCHA 위험을 부른다(ERRORS [595]).
    tree = ast.parse(real_src.lstrip())
    rets = [n.value.value for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler)
            for n in ast.walk(h) if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)]
    assert None in rets, f"네트워크 예외에서 None(판정 불가)을 돌려주지 않는다: {rets}"
    assert False not in rets, f"네트워크 예외를 '만료' 로 단정한다: {rets}"


# ══════════════════════════════════════════════════════════════════
# ⑬ 발행 전 정리가 **쓸 수 있는 쿠키를 지우면 안 된다** (ERRORS [605])
# ══════════════════════════════════════════════════════════════════
def test_유효한_쿠키를_발행전에_지우지_않는다(monkeypatch, tmp_path):
    """★ 2026-08-09 21:00 테마 미발행의 직접 원인.

    `_clear_all_cookies` 가 *"매번 새 로그인으로 신선한 쿠키 보장"* 이라는 옛 전제로
    네이버 쿠키 파일을 **무조건 삭제**했다. 그 전제는 뒤집혔다 — 네이버가 반복 로그인에
    CAPTCHA 를 걸기 때문이다. 로그가 그대로 말한다:
        21:00:02 🗑️ 쿠키·캐시 초기화: **네이버 쿠키 파일**, ...
        21:00:42 🚨 네이버 쿠키 점검 실패 — 발행 건너뜀
    시스템이 자기 발밑을 팠다.
    """
    import importlib.util
    import os
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_sched_clear", root / "JARVIS02_WRITER" / "scheduler.py")
    sc = importlib.util.module_from_spec(spec)
    sys.modules["_sched_clear"] = sc
    spec.loader.exec_module(sc)

    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc
    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    monkeypatch.setattr(sc, "BASE_DIR", tmp_path)
    monkeypatch.setattr(sc, "log", lambda *a, **k: None)
    ck = tmp_path / "naver_cookies.pkl"

    def run(valid):
        ck.write_bytes(b"x")
        monkeypatch.setenv("TS_COOKIE", "z" * 40)
        monkeypatch.setattr(nc, "cookie_valid_http", lambda *a, **k: valid)
        monkeypatch.setattr(tc, "cookie_valid_http", lambda *a, **k: valid)
        sc._clear_all_cookies("시험")
        return ck.exists(), bool(os.environ.get("TS_COOKIE"))

    assert run(True) == (True, True), "유효한 쿠키를 지운다 — 매 발행이 전체 로그인이 되어 CAPTCHA 를 부른다"
    assert run(None) == (True, True), "판정 불가를 만료로 단정해 지운다 — 네트워크 순단이 강제 재로그인이 된다"
    # ★ 2026-08-11 (ERRORS [615]) — 단언을 **뒤집는다**. 종전엔 만료 시 삭제를 요구했고
    #   그 근거는 "남기면 갱신 기회를 놓친다" 였는데 **거짓 전제** 였다:
    #   `cookie_needs_refresh()` 는 파일이 있어도 만료면 True 를 돌려준다
    #   (`return not check_cookie_valid()`). 남겨도 갱신은 정상적으로 일어난다.
    #   반면 지우면 즉시 잃는다 — ① harness Layer1 precondition 차단(attempts=0)
    #   ② 그 차단이 `_nv_collect_failed` 로 번져 티스토리까지 미발행
    #   ③ 재로그인이 캡차로 막히면 복구 재료마저 소멸.
    #   실측 08-11 07:00:02 이 자리에서 삭제 → 경제 네이버·티스토리 0/2.
    assert run(False)[0] is True, "만료됐다고 쿠키를 지운다 — 발행 전제조건과 복구 재료를 함께 없앤다"


def test_쿠키_삭제가_유효성에서_파생된다():
    """★ 이름은 역사다 — 지금 강제하는 것은 '쿠키 파일을 아예 지우지 않는다' 이다(ERRORS [615])."""
    import ast
    import inspect
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("_sched_clear2", root / "JARVIS02_WRITER" / "scheduler.py")
    sc = importlib.util.module_from_spec(spec)
    sys.modules["_sched_clear2"] = sc
    spec.loader.exec_module(sc)

    # ★ 2026-08-11 (ERRORS [615]) — 강제 방향을 **뒤집는다**.
    #   종전엔 "유효성 판정 뒤에 삭제하라" 를 강제했는데, 이번 사고로 *삭제 자체* 가
    #   해롭다는 것이 확정됐다. 이제 강제할 것은 **쿠키 파일을 지우지 않는다** 이다.
    src = inspect.getsource(sc._clear_all_cookies)
    tree = ast.parse(src.lstrip())
    _dels = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") in ("unlink", "remove")]
    _cookie_dels = [n for n in _dels
                    if "cookie" in ast.unparse(n).lower()]
    assert not _cookie_dels, (
        f"_clear_all_cookies 가 쿠키 파일을 지운다({[ast.unparse(n) for n in _cookie_dels]}) — "
        "삭제는 갱신을 앞당기지 않고(만료여도 cookie_needs_refresh 가 True) "
        "발행 precondition·티스토리·복구재료만 없앤다")
    # Chrome *캐시* 정리는 남아 있어야 한다 (로그인 데이터가 아니다)
    assert "rmtree" in src, "Chrome 캐시 정리까지 사라졌다 — 그건 지워도 되는 것이다"


# ══════════════════════════════════════════════════════════════════
# ⑥ opportunity_score 배치 변별력 붕괴 → topic_pack 후보 0개 (2026-08-14)
# ══════════════════════════════════════════════════════════════════
def test_학습가중치_붕괴시_opportunity_score가_뭉치지_않는다(monkeypatch):
    """★ 실사고 (2026-08-14, radar/TopicPackNoCandidate): 학습된 가중치가 이번 배치에서
    (예: freshness 가 전원 20.0 로 동률인데 w_fresh 가 음수로 학습됨) opportunity_score
    를 전원 0.0 으로 뭉갰다. `_candidates()`(topic_pack.py) 는 이 점수로 내림차순 정렬하는데
    전원 동점이면 정렬이 사실상 원본 순서 그대로라 경제와 무관한 잡음 키워드가 앞으로 오고,
    LLM 적합성 판정에서 전부 걸러져 "적합 후보 0개" → build_topic_pack()=None →
    TopicPackNoCandidate 로 이어졌다. `enrich_with_opportunity()` 는 학습 시점(train_weights)
    과 같은 판정(`is_ranking_degenerate`)을 적용 시점 배치에도 돌려 붕괴를 감지하면
    DEFAULT_WEIGHTS 로 즉시 재계산해야 한다.
    """
    import time
    import JARVIS03_RADAR.analyzer as az

    # freshness 전원 동률(신규 키워드 배치) + 붕괴된 학습 가중치(w_fresh 음수 지배)
    monkeypatch.setitem(az._WEIGHTS_CACHE, "data", {
        "w_trend": -0.001, "w_perf": 0.0, "w_fresh": -50.0,
        "w_velocity": 0.0, "w_competition": 0.0, "intercept": 0.0,
    })
    monkeypatch.setitem(az._WEIGHTS_CACHE, "ts", time.time())

    scored = [
        {"keyword": f"kw{i}", "score": 10.0 + i, "velocity_score": 0.0,
         "competition": 50.0, "sector": "기타"}
        for i in range(10)
    ]
    out = az.enrich_with_opportunity(scored)
    vals = [round(o["opportunity_score"], 6) for o in out]

    assert len(set(vals)) > 1, (
        f"opportunity_score 가 전원 동일값으로 뭉쳤다({vals}) — 정렬키 붕괴가 감지·복구되지 않았다"
    )


def test_기본가중치는_사본이_아니라_주인에서_파생한다():
    """★ 원칙① — 기본 가중치의 **주인은 `shared.db.DEFAULT_WEIGHTS` 한 곳**이다.

    종전 이 테스트는 `analyzer.DEFAULT_WEIGHTS` 에 값을 그대로 다시 적고 "단일 진실
    소스" 라고 단언했다. 그런데 같은 값이 `shared/db.py` 에 이미 있었다(학습 행이
    없을 때 `learned_weights_latest()` 가 돌려주는 값) — 즉 **테스트가 사본을
    진실이라고 박제**하고 있었다. 값을 비교하지 말고 *주인을 참조하는지* 를 본다.
    """
    from JARVIS03_RADAR.analyzer import default_weights
    from shared.db import DEFAULT_WEIGHTS as OWNER

    d = default_weights()
    for k in ("w_trend", "w_perf", "w_fresh", "w_velocity", "w_competition", "intercept"):
        assert d[k] == OWNER[k], f"{k} 가 주인(shared.db.DEFAULT_WEIGHTS)과 다르다 — 사본이 생겼다"
    # 값을 여기 적지 않는다: 주인이 바뀌면 이 테스트는 따라 바뀌어야 하고, 그게 정상이다.
    assert d is not OWNER, "주인 dict 를 그대로 돌려주면 호출자가 원본을 오염시킬 수 있다"


def test_채점식은_analyzer_apply_weights_단독이다():
    """★ 원칙① — 학습(변별력 가드)과 적용(opportunity_score)이 *같은 식* 을 써야 한다.

    2026-08-14 사고의 근본: 학습은 절편 포함 아핀 모델을 적합·저장했는데 적용식엔
    절편 항이 없었다. 두 곳에 따로 박힌 식은 반드시 어긋난다.
    """
    from JARVIS03_RADAR.analyzer import apply_weights

    w = {"w_trend": 1.0, "w_perf": 2.0, "w_fresh": 3.0, "intercept": 7.0}
    got = apply_weights(w, trend_score=10.0, perf=1.0, fresh=2.0)
    assert got == 10.0 * 1.0 + 1.0 * 2.0 + 2.0 * 3.0 + 7.0, "절편이 채점식에서 빠졌다"

    # 키가 없으면 기본값에서 파생 — 여기에 숫자를 적지 않는다(②).
    from JARVIS03_RADAR.analyzer import default_weights
    d = default_weights()
    assert apply_weights({}, trend_score=10.0, perf=0.0, fresh=0.0) == 10.0 * d["w_trend"]


def test_적용형상에서_정렬키를_죽이는_가중치는_학습이_거부한다():
    """★ 실사고 (2026-08-14) — 학습 표본과 적용 배치의 특징공간이 겹치지 않는다.

    학습 표본 = 이미 발행된 글(perf>0, freshness 낮음)
    적용 배치 = 오늘의 후보(perf=0, freshness 최고점 — 전원 동일)
    실측: (perf=0, freshness=20) 조합은 학습 표본 496행 중 **0행**. 그래서 학습 표본
    위에서만 보는 가드는 통과했는데 적용 배치에서는 전원 0.0 으로 뭉갰다.
    → 학습 관문은 *적용 시점 형상* 으로도 채점해 봐야 한다.
    """
    from JARVIS03_RADAR.analyzer import apply_weights
    from JARVIS03_RADAR.learning import distinct_ratio

    rotten = {"w_trend": 0.0011, "w_perf": 0.1125, "w_fresh": -0.0689, "intercept": 0.2422}
    trend = [float(t) for t in range(10, 101, 2)]
    probe = [max(0.0, apply_weights(rotten, trend_score=t, perf=0.0, fresh=20.0)) for t in trend]

    assert distinct_ratio(probe) < distinct_ratio(trend) * 0.5, (
        "실제 사고를 만든 가중치가 적용형상 프로브를 통과했다 — 관문이 무력하다"
    )
    assert max(probe) == 0.0, "이 가중치는 후보 배치를 전원 0.0 으로 만든다(사고 재현 조건)"
