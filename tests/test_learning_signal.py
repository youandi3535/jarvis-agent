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
    from JARVIS04_SCHEDULER.job_llm_priority import gate
    from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import human_wait_sec

    assert human_wait_sec() > 0, "대화형(잡 밖)에서는 사람을 기다려야 한다"

    seen = {}
    gate("j01_economic_post", lambda: seen.update(w=human_wait_sec()))()
    assert seen["w"] == 0, f"예약 잡 안(무인)인데 {seen['w']}초를 기다린다"


def test_로그인_실패_타입이_사유에서_파생된다():
    """중앙 매핑표를 만들지 않는다 — 새 사유가 생기면 타입이 자동으로 따라온다."""
    from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import naver_login_error_type

    assert naver_login_error_type("captcha_unattended") == "NaverLoginCaptchaUnattended"
    assert naver_login_error_type("login_no_redirect") == "NaverLoginLoginNoRedirect"
    # 미지 사유도 뭉개지 않고 이름을 만든다 (ERRORS [547] — 뭉뚱그린 타입 금지)
    assert naver_login_error_type("brand_new_reason") == "NaverLoginBrandNewReason"
    assert naver_login_error_type("") == "NaverLoginUnknown"


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
