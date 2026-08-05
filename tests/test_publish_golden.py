"""발행 파이프라인 골든 테스트 — **오늘 놓친 실패 경로를 덧받는다.**

★ 왜 이 테스트들인가 (2026-08-02 전수 감사 9위 — 사용자 승인)
  2026-07-29~30 하루에 내가 넣은 코드에서 결함 7건이 나왔다. 근본 원인은 하나였다:
  **성공 경로만 통과시키고 "완료" 를 보고했다.**
  · 발행 결손 감사 → "결손 0건" 만 확인. 결손이 *있는* 분기는 `TypeError` 로 죽었다.
  · 태그 관문 → 거부문 차단만 확인. 정상 응답(줄바꿈 구분)이 통째로 폐기되는 것은 몰랐다.
  · "4조합 통합" → 함수 정의 2곳만 세고 *소비처* 를 세지 않았다. 테마는 다른 경로였다.

  그래서 이 파일의 규칙은 하나다 — **감시·게이트 코드는 실패했을 때 도는 코드이므로,
  실패 분기를 실제로 밟는 테스트가 없으면 검증한 것이 아니다.**

★ 무엇을 테스트하지 *않는가*
  실제 발행(Selenium·네트워크)·LLM 호출은 하지 않는다. 순수 판정 로직만 본다.
  느리고 외부에 의존하는 테스트는 CI 에서 곧 꺼지고, 꺼진 테스트는 없는 것과 같다.
"""
from __future__ import annotations

import datetime as dt

import pytest


# ══════════════════════════════════════════════════════════════════
# 1) 발행 결손 감사 — ★ '결손 있음' 분기를 실제로 밟는다
# ══════════════════════════════════════════════════════════════════
def test_결손_없을때_통과():
    """정상 경로. 이것만 확인하고 완료를 보고한 것이 2026-07-29 사고였다."""
    from JARVIS08_PUBLISH.publish_ledger import slot_gaps

    res = slot_gaps()
    assert res is not None, "발행 슬롯을 파생하지 못했다 — DEFAULT_JOBS 발행 잡 확인"
    post_type, gaps, platforms = res
    assert post_type, "글 종류가 비었다"
    assert platforms, "기대 플랫폼이 0개 — platforms/ AST 파생 확인"
    assert isinstance(gaps, list)


def test_결손_보고_경로가_실제로_성립한다():
    """★ 핵심 — 결손 1건을 만들었을 때 GUARDIAN 보고 호출이 *성립* 하는가.

    2026-07-29 초판은 `report(..., error_type=...)` 를 불렀는데 그런 인자가 없어
    **첫 결손에서 TypeError 로 죽었다**. 결손 0건일 때만 정상 동작했으므로 커밋 검증을 통과했다.
    여기서는 실제 인자로 `signature.bind()` 를 시켜 그 사고를 재현 불가능하게 만든다.
    """
    import inspect

    from JARVIS07_GUARDIAN.error_collector import report
    from JARVIS08_PUBLISH.publish_ledger import publish_gap_error_type

    etype = publish_gap_error_type("economic", "tistory")
    assert etype == "PublishGapEconomicTistory", f"오류 타입 파생이 바뀌었다: {etype}"

    # 실제 호출부와 *같은 인자 모양* 으로 바인딩 — 어긋나면 여기서 터진다.
    inspect.signature(report).bind(
        etype, "publish",
        message="economic 글이 tistory 에 발행되지 않았다",
        module="publish_ledger", func_name="job_audit_publish_completeness",
        context={"post_type": "economic", "platform": "tistory"},
    )


def test_자정_넘긴_발행이_제_슬롯으로_계산된다():
    """★ 21:00 테마가 자정을 넘겨 끝나도 그 슬롯의 실적이어야 한다.

    초판은 `date(created_at)=오늘` 이라 ① 그날은 결손 오신고 ② 다음날엔 그 행이
    '오늘 실적' 으로 세어져 **진짜 실패가 초록불** 이 됐다(연속 장애일수록 탐지가 꺼짐).
    """
    from JARVIS08_PUBLISH.publish_ledger import current_slot, publish_slots

    slots = publish_slots()
    assert slots, "발행 슬롯 파생 실패"
    late_types = [pt for pt, h, _m in slots if h >= 12]
    if not late_types:
        pytest.skip("오후 발행 슬롯이 없어 자정 넘김 시나리오가 성립하지 않는다")

    pt, h, m = next((s for s in slots if s[1] >= 12), slots[0])
    audit_at = dt.datetime(2026, 7, 20, h, m) + dt.timedelta(hours=2, minutes=20)
    got = current_slot(audit_at)
    assert got is not None
    post_type, start, end = got
    assert post_type == pt
    # 슬롯 창의 끝이 *다음 발행 시각* 이라 자정을 넘어간다
    assert end > start
    assert end.date() > start.date(), "슬롯 창이 자정을 넘지 않는다 — 달력 날짜 판정으로 회귀"
    # 자정 직후 발행분이 이 창 안에 들어오는가
    after_midnight = start + dt.timedelta(hours=3, minutes=51)
    assert start <= after_midnight < end


def test_감사시각이_실제_발행소요보다_늦다():
    """감사가 너무 이르면 *성공한 발행* 을 결손으로 오신고한다(초판 50분 → 실측 32% 오탐)."""
    from JARVIS08_PUBLISH.publish_ledger import audit_lag_minutes

    lag = audit_lag_minutes(misfire_grace_sec=3600)
    assert lag >= 120, f"감사 지연 {lag}분 — 발행 소요 실측(최대 +246분) 대비 너무 이르다"


# ══════════════════════════════════════════════════════════════════
# 2) 태그 관문 — ★ 양방향으로 본다 (막아야 할 것 / 통과시켜야 할 것)
# ══════════════════════════════════════════════════════════════════
_NL = chr(10)

_TAG_ACCEPT = [
    ("쉼표 구분", "반도체관련주, 2차전지주식, HBM투자, 반도체대장주, AI반도체"),
    ("줄바꿈 구분", _NL.join(["반도체관련주", "2차전지주식", "HBM투자", "반도체대장주"])),
    ("경제 주제", "중위소득, 기준중위소득, 복지정책, 생계급여"),
]
_TAG_REJECT = [
    ("짧은 거부문", "죄송하지만 이 주제로는 적절한 태그를 만들 수 없습니다"),
    ("긴 거부 산문", "주식, 투자관련키워드제공불가" + _NL + _NL
                 + "**설명:** 이 게시글은 복지·통계 주제를 다루고 있으며 종목·투자 테마와는 무관합니다."),
    ("빈 응답", ""),
]


@pytest.mark.parametrize("name,raw", _TAG_ACCEPT, ids=[n for n, _ in _TAG_ACCEPT])
def test_정상_태그응답은_통과한다(name, raw):
    """★ 초판은 줄바꿈 구분을 통째로 폐기했다 — 정상 응답을 버리면 폴백이 상시화된다."""
    from JARVIS08_PUBLISH.tags import response_is_tag_shaped

    assert response_is_tag_shaped(raw, 5), f"정상 응답이 폐기됨: {name}"


@pytest.mark.parametrize("name,raw", _TAG_REJECT, ids=[n for n, _ in _TAG_REJECT])
def test_거부문은_통째로_폐기된다(name, raw):
    """실제 사고(2026-07-29 07:00): LLM 거부 산문이 공개 블로그 태그로 발행됐다."""
    from JARVIS08_PUBLISH.tags import response_is_tag_shaped

    assert not response_is_tag_shaped(raw, 5), f"거부문이 통과함: {name}"


def test_폴백태그에_쓰레기가_섞이지_않는다():
    """제목을 그냥 쪼개면 '내'·'3' 같은 조각이 공개 태그가 된다."""
    from JARVIS08_PUBLISH.tags import fallback_tags

    tags = fallback_tags("중위소득, 내 살림살이는 어디쯤 3 있을까요", 6)
    assert all(len(t) >= 2 for t in tags), f"1글자 태그 잔존: {tags}"
    assert all(not t.isdigit() for t in tags), f"숫자 단독 태그 잔존: {tags}"


# ══════════════════════════════════════════════════════════════════
# 3) ★ 4조합 — '통합했다' 를 *소비처* 로 센다
# ══════════════════════════════════════════════════════════════════
def test_태그경로가_4조합_모두_단일_진입점을_지난다():
    """★ 2026-07-29 나는 함수 정의 2곳만 합치고 "4조합 통합" 이라 보고했다.
    테마는 `trend_theme_writer` 가 고정 템플릿을 `tags=` 로 직접 넘겨 그 함수를 **안 탔다**.
    정의가 아니라 **소비처** 를 세야 한다 — 이 테스트가 그걸 강제한다.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    targets = {
        "theme": root / "JARVIS02_WRITER" / "trend_theme_writer.py",
        "economic": root / "JARVIS02_WRITER" / "trend_economic_writer.py",
    }
    seen: set[tuple[str, str]] = set()
    for post_type, path in targets.items():
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"post_to_(naver|tistory)\s*\(", src):
            platform = m.group(1)
            before = src[max(0, m.start() - 1600):m.start()]
            # 직접 호출(generate_tags) 이거나, tags= 를 안 넘겨 발행자 shim 을 타거나 둘 중 하나여야 한다
            builds_own = re.search(r"\n\s+tags\s*=", before) is not None
            uses_single = ("generate_tags" in before) or ("_gen_tags" in before)
            assert uses_single or not builds_own, (
                f"{post_type}×{platform} 이 태그를 자체 생성한다 — "
                f"JARVIS08_PUBLISH.tags.generate_tags 단일 진입점 위반"
            )
            seen.add((post_type, platform))
    assert len(seen) == 4, f"4조합을 모두 확인하지 못했다: {sorted(seen)}"


def test_발행_결손감사가_4조합을_기대한다():
    """기대 집합이 리터럴이 아니라 파생인지 — 조합 수가 글종류×플랫폼이어야 한다."""
    from JARVIS08_PUBLISH.publish_ledger import expected_platforms, publish_slots

    types = {pt for pt, _h, _m in publish_slots()}
    platforms = set(expected_platforms())
    assert len(types) >= 2, f"글 종류 파생 실패: {types}"
    assert len(platforms) >= 2, f"플랫폼 파생 실패: {platforms}"
    assert len(types) * len(platforms) >= 4


# ══════════════════════════════════════════════════════════════════
# 4) 학습 지침 위생 — 오염이 프롬프트에 닿지 않는가
# ══════════════════════════════════════════════════════════════════
_DIRECTIVE_OK = [
    "제목 앞부분에 핵심 키워드를 배치하고 숫자를 하나 포함하라",
    "도입부는 독자의 상황을 묻는 문장으로 시작할 것",
]
_DIRECTIVE_BAD = [
    ("글 제목", "미국 환율보고서, 한국 2026년에도 환율 관찰대상국 재지정"),
    ("본문 서술", "두 종목 모두 흑자를 유지하며 안정적인 포지션을 이어가고 있어요."),
    ("이 글의 수치", "현대차 PER 4.7배, 카카오 32.3배 비교"),
    ("HTML 조각", "<p>본문을 문단으로 나누라</p>"),
]


@pytest.mark.parametrize("d", _DIRECTIVE_OK)
def test_정상_지침은_학습된다(d):
    from JARVIS07_GUARDIAN.quality_learner import directive_issues

    assert not directive_issues(d), f"정상 지침이 거부됨: {d}"


@pytest.mark.parametrize("name,d", _DIRECTIVE_BAD, ids=[n for n, _ in _DIRECTIVE_BAD])
def test_오염_지침은_거부된다(name, d):
    """실측 381건 중 378건이 이 유형이었고, 전부 '반드시 적용' 헤더로 주입되고 있었다."""
    from JARVIS07_GUARDIAN.quality_learner import directive_issues

    assert directive_issues(d), f"오염 지침이 통과함: {name}"


def test_지침_길이상한이_파생값이다():
    """프롬프트가 요구하는 길이와 게이트가 거부하는 길이가 어긋나면 매번 버려진다."""
    from JARVIS02_WRITER.length_manager import KOREAN_PER_SENTENCE
    from JARVIS07_GUARDIAN.quality_learner import DIRECTIVE_MAX_LEN

    assert DIRECTIVE_MAX_LEN == KOREAN_PER_SENTENCE * 2


# ══════════════════════════════════════════════════════════════════
# 5) 시크릿 마스킹 — 기록 관문이 실제로 막는가
# ══════════════════════════════════════════════════════════════════
def test_시크릿이_오류기록에_평문으로_남지_않는다():
    """실측: 봇 토큰이 DB 119행에 평문. 생산자는 *텔레그램 폴링 예외* 였다 —
    아무도 기록하려 하지 않았는데 기록됐다. 그래서 관문에서 거른다."""
    import os

    from shared.secrets import mask, reload_secrets

    os.environ["JARVIS_TEST_FAKE_TOKEN"] = "1234567890:AAFakeTokenForTestingOnly_abcdef"
    try:
        reload_secrets()
        fake = os.environ["JARVIS_TEST_FAKE_TOKEN"]
        probe = f"HTTPSConnectionPool url: /bot{fake}/getUpdates failed"
        out = mask(probe)
        assert fake not in out, "시크릿이 마스킹되지 않았다"
        assert "JARVIS_TEST_FAKE_TOKEN" in out, "어떤 키였는지 표식이 없다(추적 불가)"
    finally:
        os.environ.pop("JARVIS_TEST_FAKE_TOKEN", None)
        reload_secrets()


def test_마스킹_자체검사가_통과한다():
    """설치 플래그가 아니라 *동작* 으로 확인 (CLAUDE.md patch_effective 표준)."""
    from shared.secrets import selfcheck

    res = selfcheck()
    non_env = [i for i in res["issues"] if "미적재" not in i]
    assert not non_env, f"마스킹 자체검사 위반: {non_env}"


# ══════════════════════════════════════════════════════════════════
# 6) 학습 폐쇄루프 — 2026-08-03 감사에서 끊긴 곳을 못 박는다
# ══════════════════════════════════════════════════════════════════
def test_분석은_한_프로세스에서_순차_처리된다():
    """★ 슬롯당 2글 중 1글만 채점되던 근본 원인.

    종전엔 대기 글마다 subprocess 를 따로 띄웠다(2초 간격). LLM 크로스 프로세스 락 때문에
    뒤 프로세스가 45초 한도를 넘겨 포기 → **항상 뒤엣것만 채점을 잃었다**
    (실측 로그 `크로스 프로세스 잠금 45s 대기 초과`, 채점률 46%).
    2026-07-30 의 대기열 DESC→ASC 수정은 *누가 지는가* 만 바꿨다.
    """
    import inspect
    import re

    from JARVIS03_RADAR import jobs

    src = inspect.getsource(jobs.job_analyzer_fallback)
    # 대기 글마다 Popen 을 도는 루프가 있으면 회귀
    assert not re.search(r"for\s+record\s+in\s+pending", src), (
        "대기 글마다 프로세스를 띄우고 있다 — 락 경합으로 뒤엣것이 채점을 잃는다"
    )
    assert src.count("Popen(") == 1, f"분석 프로세스는 하나여야 한다 (현재 {src.count('Popen(')}개)"


def test_지침_미준수는_발행을_차단하지_않는다():
    """★ 2026-08-04 회귀 수정 — 품질 기준의 단일 진입점은 100점 루브릭이다.

    2026-08-03 에 나는 지침 위반을 *차단 사유* 로 만들었다. 그런데 주입 지침 8건이
    **전부** 루브릭 항목과 겹친다(H3→N3_h3_count, 도입부→B1_intro, H1→T3_h1_count …).
    검사는 이미 되고 있었고 — 다만 *감점* 이지 *차단* 이 아니었으며 그게 정상이다.
    실제 피해: 08-04 07:00 네이버 경제글이 루브릭 72.0·79.0·75.5·78.0 으로 네 번 모두
    기준(70)을 넘겼는데 그 게이트가 네 번 다 막아 **발행 0건**.
    """
    import inspect

    from JARVIS02_WRITER import prepublish_gate as G

    gate_src = inspect.getsource(G.prepublish_quality_issues)
    # 위반이 Issue(=차단 사유)로 나가면 회귀
    assert "[학습지침]" not in gate_src, (
        "지침 미준수가 다시 차단 사유가 됐다 — 루브릭과 이중 기준이 된다"
    )
    idx = gate_src.find("_violated")
    assert idx > 0, "지침 판정 자체가 사라졌다 — 관측은 남겨야 한다"
    assert "out.append" not in gate_src[idx:idx + 700], (
        "_violated 처리부가 Issue 를 생성한다 — 차단 경로 부활"
    )
    assert "관측용" in gate_src, "관측 로그가 사라졌다"


def test_지침_판정은_통합_1콜에_얹혀있다():
    """관측은 유지하되 *별도 LLM 호출* 을 만들지 않는다."""
    import inspect

    from JARVIS02_WRITER import prepublish_gate as G

    call_src = inspect.getsource(G._combined_quality_call)
    assert "## C. 학습 지침 준수" in call_src, "지침 축이 통합 판정에서 사라졌다"
    assert "active_directives" in call_src, "지침 목록을 quality_learner 에서 받지 않는다"
    # 통합 호출은 하나여야 한다 (재시도 1회 포함 최대 2회)
    assert call_src.count("_inv_r(") <= 2, "지침 판정용 LLM 호출이 따로 생겼다"


def test_지침조회는_사용기록을_남기지_않는다():
    """검사 때문에 usage 가 두 배로 늘면 보상 통계가 오염된다."""
    from shared.db import get_db
    from JARVIS07_GUARDIAN.quality_learner import active_directives

    with get_db() as con:
        before = con.execute("SELECT COUNT(*) FROM insight_usage").fetchone()[0]
    active_directives(scope="theme", limit=8)
    active_directives(scope="economic", limit=8)
    with get_db() as con:
        after = con.execute("SELECT COUNT(*) FROM insight_usage").fetchone()[0]
    assert before == after, f"조회가 사용 기록을 남겼다 ({before} → {after})"


def test_보상창이_발행스케줄에서_파생된다():
    """`18` 리터럴은 '24h 재발행 간격의 3/4' 이라는 계산을 주석에만 두고 있었다.

    발행 시각을 옮기면 주석이 조용히 거짓이 된다(keeper HANG_THRESHOLD 와 같은 병).
    """
    from JARVIS07_GUARDIAN import quality_learner as Q

    gap = Q._same_type_republish_gap_h()
    assert gap > 0, "재발행 간격 파생 실패"
    assert Q.attribution_window_h() == max(1, int(gap * Q._ATTRIB_SAFETY))
    # 다음 회차 글에 잘못 귀속되지 않으려면 창이 재발행 간격보다 짧아야 한다
    assert Q.attribution_window_h() < gap, "귀속 창이 재발행 간격 이상 — 다음 회차 글에 오귀속된다"

    # ★ 진짜 파생인가 — **입력을 바꿔서 따라오는지** 본다.
    #   현재 파생값이 우연히 옛 리터럴(18)과 같으므로, 값만 비교하면 리터럴로 되돌려도
    #   테스트가 통과한다(실측으로 확인된 변별력 부족).
    _orig = Q._same_type_republish_gap_h
    try:
        Q._same_type_republish_gap_h = lambda: 12          # 발행을 하루 2회로 옮겼다고 가정
        assert Q.attribution_window_h() == 9, (
            f"발행 간격을 바꿨는데 귀속 창이 따라오지 않는다 "
            f"({Q.attribution_window_h()}h) — 리터럴로 회귀했을 가능성"
        )
    finally:
        Q._same_type_republish_gap_h = _orig


def test_보상_재시도창이_선택기간과_같다():
    """선택 기간이 지난 지침은 어차피 안 뽑힌다 — 그때까지가 재시도의 값이다."""
    from JARVIS07_GUARDIAN.quality_learner import SELECTION_DAYS, reward_retry_days

    assert reward_retry_days() == SELECTION_DAYS
    assert reward_retry_days() >= 7, "재시도 창이 너무 짧다 — 채점 지연 시 보상이 영구 사장된다"


def test_지침블록_기본인자가_후보를_비우지_않는다():
    """`days` 기본값을 0 으로 두면 SQL 이 '0일' 로 읽어 후보가 통째로 사라진다.

    ★ 운영 데이터에 기대지 않는다 — 임시 DB 에 지침 하나를 심고 그것이 나오는지 본다.
      (운영 DB 를 읽는 테스트는 데이터가 바뀌면 이유 없이 깨지고, 곧 꺼진다.)
    """
    from shared.db import upsert_learning_insight
    from JARVIS07_GUARDIAN.quality_learner import build_insights_block

    from shared.db import get_db

    probe = "제목 앞부분에 핵심 키워드를 배치하고 숫자를 하나 포함하라"
    upsert_learning_insight(
        insight_key="golden_probe_title", insight_type="title",
        description="테스트용 지침", directive=probe, weight=1.0, scope="economic",
    )
    # ★ last_seen 을 이틀 전으로 — `days=0` 이 새 나가면 이 지침이 탈락한다.
    #   오늘 날짜로 두면 days=0 이어도 통과해 **변별력이 없다**(실측으로 확인).
    with get_db() as con:
        con.execute(
            "UPDATE learning_insights SET last_seen = date('now','localtime','-2 day') "
            "WHERE insight_key LIKE ?", ("%golden_probe_title",))
        con.commit()

    block = build_insights_block(scope="economic", limit=8)
    assert block, "지침 블록이 비었다 — days 기본값이 0 으로 새 나갔을 가능성"
    assert probe in block, f"심은 지침이 블록에 없다: {block[:120]}"


def test_점수보고가_최종판정을_말한다():
    """★ 2026-08-04 사용자 지적 — "모두 70점을 넘겼는데 왜 실패냐".

    종전 메시지는 `✅ 통과 (기준 70)` 라고만 적었는데 그건 **점수 항목 하나의 판정**이었다.
    실제로는 다른 검사가 막고 있었고 그 사실이 메시지에 없었다.
    계기판이 사실과 다르게 읽히면 없느니만 못하다.
    """
    import shared.notify as N
    from JARVIS02_WRITER import prepublish_gate as G

    sent: list[str] = []
    orig = N.send_tg
    N.send_tg = lambda text, **kw: sent.append(text)
    try:
        sr = {"total": 72.0, "passed": True, "sections": {}}

        # ① 점수 통과 + 다른 문제 없음 → 발행
        sent.clear()
        G.send_score_report(sr, "economic", "naver", "t", blocking=[])
        assert sent and "발행 진행" in sent[0], f"발행 판정이 없다: {sent[:1]}"

        # ② 점수는 통과인데 사실성이 막음 → **재작성** 이라고 말해야 한다
        sent.clear()
        G.send_score_report(sr, "economic", "naver", "t",
                            blocking=[{"kind": "factuality", "detail": "[사실성] 출처 없는 수치"}])
        assert sent, "메시지가 전송되지 않았다"
        assert "재작성" in sent[0], (
            "점수는 통과인데 다른 검사가 막았다 — 메시지가 '통과' 로만 보이면 어제 사고 재현"
        )
        assert "사실성" in sent[0], "차단 사유가 메시지에 없다"

        # ③ 점수 미달 → 사유 명시
        sent.clear()
        G.send_score_report({"total": 65.0, "passed": False, "sections": {}},
                            "economic", "naver", "t", blocking=[])
        assert "점수 미달" in sent[0] and "재작성" in sent[0]
    finally:
        N.send_tg = orig


# ══════════════════════════════════════════════════════════════════
# 7) 2026-08-04 설계 감사에서 나온 3건
# ══════════════════════════════════════════════════════════════════
def test_테마_재시도가_전체발행을_한번만_돈다():
    """★ 실측 피해 — 2026-07-20 21:00 슬롯이 네이버에 3건을 냈다.

    종전 `{p: _make_theme_retry() for p in _guardian_fail}` 은 실패 플랫폼 수만큼
    콜백을 등록했고 incident_responder 가 그걸 순회했다. 그런데 그 콜백은
    `run_all_themes()` 로 **두 플랫폼을 통째로 다시 발행** 한다(플랫폼 인자가 없다).
    → 두 플랫폼 동시 실패 시 전체 테마 발행이 2회.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "JARVIS02_WRITER" / "scheduler.py").read_text(encoding="utf-8")
    # 플랫폼마다 콜백을 만드는 dict comprehension 이 있으면 회귀
    assert not re.search(r"_retry_fns\s*=\s*\{\s*\w+\s*:\s*_make_theme_retry\(\)\s*for\s+", src), (
        "테마 재시도가 플랫폼 수만큼 등록된다 — 전체 발행이 여러 번 돈다"
    )


def test_결손조회가_글종류를_구분한다():
    """슬롯 창 안의 *다른 종류* 글이 결손을 지우면 감시가 조용히 꺼진다."""
    import datetime as dt
    import inspect

    from JARVIS08_PUBLISH.publish_ledger import published_in_slot, slot_gaps

    sig = inspect.signature(published_in_slot)
    assert "post_type" in sig.parameters, "글종류 필터 인자가 없다"
    # 호출부가 실제로 넘기는가 (인자만 있고 안 넘기면 무의미)
    assert "published_in_slot(start, end, post_type)" in inspect.getsource(slot_gaps), (
        "slot_gaps 가 post_type 을 넘기지 않는다 — 인자만 있고 안 쓰임"
    )
    # SQL 이 실제로 필터를 붙이는가
    src = inspect.getsource(published_in_slot)
    assert "post_type = ?" in src, "SQL 에 글종류 조건이 없다"


def test_감사는_발행락을_지우지_않는다():
    """★ 감시는 대상을 건드리지 않는다.

    종전 `publishing_in_progress()` 는 `scheduler._is_locked_externally()` 를 불렀는데
    그 함수는 3시간 넘은 락 파일을 **unlink 한다**. 실측 최대 발행 지연 4.1시간 —
    즉 감사 잡이 도는 것만으로 살아 있는 발행 락이 지워질 수 있었다.
    """
    import ast
    import inspect
    import textwrap

    from JARVIS08_PUBLISH.publish_ledger import publishing_in_progress

    # ★ 소스 텍스트가 아니라 **AST** 로 본다 — 주석·독스트링에 적힌 함수 이름을
    #   호출로 오인하면(초판이 그랬다) 테스트가 스스로 거짓 실패한다.
    tree = ast.parse(textwrap.dedent(inspect.getsource(publishing_in_progress)))
    imported = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_is_locked_externally" not in imported | called, (
        "감사가 락을 *삭제하는* 함수를 부른다 — read-only 조회여야 한다"
    )
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "exists" in attrs, "락 존재 여부 조회가 아니다"
    # 실제로 호출해도 락 파일이 사라지지 않는가
    from JARVIS02_WRITER.scheduler import LOCK_FILE
    before = LOCK_FILE.exists()
    publishing_in_progress()
    assert LOCK_FILE.exists() == before, "조회가 락 파일 상태를 바꿨다"


# ══════════════════════════════════════════════════════════════════
# 8) 2026-08-04 전수 감사 1·8위
# ══════════════════════════════════════════════════════════════════
def test_모든_종료경로가_같은_best_so_far_판정을_쓴다():
    """★ 발행/미발행이 '지문이 흔들렸는가' 라는 우연으로 갈리면 안 된다.

    실측 2026-08-04 07:00 — 같은 원인인데 네이버는 지문이 안 흔들려 abort(미발행),
    티스토리는 흔들려 best-so-far(발행). 판정이 max_attempts 경로에만 있고
    abort 경로는 그 앞에서 먼저 return 했기 때문이다.
    게다가 CLAUDE_WRITER 는 'issue detail 에 변동값 금지' 를 박제해 뒀으므로
    **규정을 잘 지킬수록 지문이 안정돼 abort 에 걸린다** — 정확히 거꾸로였다.
    """
    import inspect

    from JARVIS00_INFRA import harness as H

    assert hasattr(H, "_best_so_far_eligible"), "자격 판정 함수가 없다"
    assert hasattr(H, "_try_best_so_far"), "송출 헬퍼가 없다"

    body = inspect.getsource(H._run_action_locked)
    n = body.count("_try_best_so_far(")
    assert n >= 3, f"종료 경로 중 일부가 best-so-far 를 안 본다 (호출 {n}곳, 3 이상이어야)"
    # 조건을 손으로 다시 쓴 사본이 있으면 회귀
    assert 'all(i.kind == "engagement"' not in body, (
        "종료 경로에 자격 조건 사본이 남아 있다 — 헬퍼 하나만 쓸 것"
    )


def test_best_so_far_는_품질점수만_남았을때만():
    """사실성·구조 결함이 섞이면 절대 내보내지 않는다 — 거짓 발행 금지."""
    from JARVIS00_INFRA.harness import Issue, _best_so_far_eligible

    assert _best_so_far_eligible([Issue(step="s", kind="engagement", detail="[품질점수] 68/100")])
    assert not _best_so_far_eligible([
        Issue(step="s", kind="engagement", detail="a"),
        Issue(step="s", kind="factuality", detail="출처 없는 수치"),
    ]), "사실성 결함이 섞였는데 발행 자격을 줬다"
    assert not _best_so_far_eligible([Issue(step="s", kind="draft_quality", detail="구조")])
    assert not _best_so_far_eligible([]), "남은 게 없는데 자격을 줬다"


def test_결손감사가_스테일_락에_영원히_속지_않는다():
    """★ 오전 수정(락 read-only)의 반대 방향 누수.

    존재 여부만 보면 비정상 종료로 새어 남은 락이 영원히 '발행 중' 으로 읽혀
    그 슬롯 결손을 **영구히 놓친다**. 스테일 청소는 *다음 발행* 때만 돌기 때문이다.
    """
    import inspect

    from JARVIS08_PUBLISH.publish_ledger import publishing_in_progress

    src = inspect.getsource(publishing_in_progress)
    assert "publish_lock_stale_sec" in src, "신선도 상한을 보지 않는다 — 스테일 락에 영원히 속는다"
    assert "st_mtime" in src, "락 갱신 시각을 보지 않는다"


def test_스테일_상한이_최악_발행소요보다_크다():
    """상한이 너무 짧으면 *진행 중인 발행* 을 죽은 락으로 오판한다."""
    from JARVIS02_WRITER.scheduler import publish_lock_stale_sec
    from JARVIS08_PUBLISH.publish_ledger import audit_lag_minutes

    worst_min = audit_lag_minutes(3600)          # misfire_grace + 플랫폼수 × 플랫폼당 상한
    assert publish_lock_stale_sec() // 60 > worst_min, (
        f"스테일 상한 {publish_lock_stale_sec()//60}분 ≤ 최악 발행소요 {worst_min}분 — "
        f"살아 있는 발행을 죽은 락으로 오판한다"
    )


def test_스테일_상수의_주인은_한곳():
    """publish_ledger 가 상수를 복사하면 한쪽만 바뀌어 어긋난다."""
    import inspect

    from JARVIS08_PUBLISH import publish_ledger as L

    assert "10800" not in inspect.getsource(L.publishing_in_progress), (
        "스테일 초를 복사했다 — scheduler.publish_lock_stale_sec() 에서 파생할 것"
    )


# ══════════════════════════════════════════════════════════════════
# 9) 2026-08-04 전수 감사 3위 — 무승인 도구의 시크릿 접근
# ══════════════════════════════════════════════════════════════════
def test_무승인_도구가_시크릿_파일을_못_읽는다():
    """★ `run_bash("cat .env")` 는 승인 버튼에 막히는데 `read_file(".env")` 는 통과했다.

    같은 행위인데 통로에 따라 게이트가 달랐다. `_DENY_DIRS` 가 디렉터리 접두어만
    비교했기 때문이다. 이 관문 하나가 read_file·glob_files·grep_code 를 동시에 덮는다.
    """
    from pathlib import Path

    from JARVIS01_MASTER.agent_tools import _safe_path

    for blocked in (".env", "JARVIS02_WRITER/naver_cookies.pkl",
                    "JARVIS08_PUBLISH/credentials/login_manager.py"):
        assert _safe_path(blocked) is None, f"시크릿이 뚫린다: {blocked}"
    # 과잉차단 검사 — 일반 코드·예시 파일은 계속 읽혀야 한다
    for allowed in ("shared/db.py", "README.md", ".env.example"):
        if (Path(__file__).resolve().parent.parent / allowed).exists():
            assert _safe_path(allowed) is not None, f"정상 파일이 막힌다: {allowed}"


def test_시크릿_목록이_파생이다():
    """목록을 박으면 새 자격증명이 생겼을 때 조용히 새어 나간다."""
    import inspect

    from shared import secrets as S

    assert hasattr(S, "secret_files") and hasattr(S, "is_secret_file")
    src = inspect.getsource(S.secret_files)
    assert "login_manager" in src, "쿠키 경로를 주인(login_manager)에게 묻지 않는다"
    files = {p.name for p in S.secret_files()}
    assert ".env" in files, f"파생 결과에 .env 가 없다: {files}"


def test_web_fetch_가_내부주소를_거부한다():
    """이 도구는 무승인이다. 내부 API 는 인증이 없어 시크릿 파일을 막아도 우회가 남는다."""
    from JARVIS01_MASTER.agent_tools import web_fetch

    for u in ("http://127.0.0.1:9198/api/errors", "http://localhost:9199/",
              "http://192.168.0.1/", "http://169.254.169.254/latest/meta-data/"):
        r = web_fetch(u)
        assert not r.get("ok"), f"내부 주소가 통과했다: {u}"


def test_web_fetch_주소판정이_목록이_아니다():
    """사설 대역 목록을 박으면 IPv6·새 대역에서 샌다 — 표준 분류에 위임할 것."""
    import inspect

    from JARVIS01_MASTER.agent_tools import web_fetch

    src = inspect.getsource(web_fetch)
    assert "ipaddress" in src, "주소 분류를 표준 라이브러리에 위임하지 않는다"
    assert "is_private" in src and "is_loopback" in src


def test_쿠키_저장이_단일진입점이고_권한을_고정한다():
    """저장이 여러 곳이면 한 곳만 고쳐도 다른 경로에서 다시 0644 로 쓰인다."""
    import inspect

    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as R

    src = inspect.getsource(R)
    # 단일 진입점 밖에서 직접 dump 하면 회귀
    assert src.count('pickle.dump(cookies, open(') == 0, "쿠키를 직접 dump 하는 경로가 남아 있다"
    assert hasattr(R, "_save_cookies"), "저장 단일 진입점이 없다"
    assert "0o600" in inspect.getsource(R._save_cookies), "저장 후 권한을 고정하지 않는다"


def test_시크릿_파일_권한이_소유자전용():
    """쿠키는 비밀번호와 같다 — 있으면 로그인 없이 그 계정이 된다."""
    import stat
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for rel in (".env", "JARVIS02_WRITER/naver_cookies.pkl"):
        f = root / rel
        if not f.exists():
            continue
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode & 0o077 == 0, f"{rel} 권한 {oct(mode)} — 소유자 외 접근 가능"


# ══════════════════════════════════════════════════════════════════
# 7) ★ 판정 불가가 대본을 재생성시키지 않는가 (ERRORS [549] · 2026-08-04)
# ══════════════════════════════════════════════════════════════════
#   실사고: 21:00 테마 발행에서 판정기(fact_judge)가 서버 거절(turns=0)로 죽자
#   harness 가 **대본을 통째로 재생성**했다. 대본은 멀쩡했는데도.
#   그 재생성이 3만 토큰을 먹어 5시간 창을 소진 → 다음 판정도 거절 → 악순환.
#   한 발행에 writer_long_body 4회 116,345 토큰(창의 86%), 판정 몫은 5.6% 였다.
def _mk_action():
    from JARVIS00_INFRA.harness import ActionDefinition, ActionStep
    steps = [ActionStep(name=n, fn=lambda s: {}) for n in
             ("① 규정 로드", "② 종목 수집", "③ 네이버 대본 생성", "⑤ 티스토리 대본 생성")]
    return ActionDefinition(name="theme-publish-x-tistory", steps=steps,
                            verify=lambda s: [], send=lambda s: None)


def test_판정불가는_대본을_재생성시키지_않는다():
    """★ 핵심 — 인프라 사유 이슈에 *대본 step 이름* 이 붙어 와도 재생성 금지.

    게이트는 `Issue(step=step_name, kind=...)` 로 보내므로 step 은 실제 대본 step 이다.
    판정은 `kind` 로 해야 한다 — step 이름으로 하면 이 사고가 그대로 재발한다.
    """
    from JARVIS00_INFRA.harness import Issue, _find_resume_step, VERIFY_ONLY, INFRA_KIND

    iss = [Issue(step="⑤ 티스토리 대본 생성", kind=INFRA_KIND,
                 detail="[사실성] 판정 불가 — LLM 미가용(일시적)")]
    assert _find_resume_step(_mk_action(), iss) == VERIFY_ONLY, (
        "판정 불가(인프라)인데 대본 재생성으로 갔다 — 2026-08-04 사고 재발")


def test_진짜_대본결함은_여전히_재생성한다():
    """반대 방향 — 인프라를 걸러내다 *진짜 결함* 까지 통과시키면 안 된다."""
    from JARVIS00_INFRA.harness import Issue, _find_resume_step, VERIFY_ONLY

    for kind in ("draft_failed", "factuality", "engagement"):
        iss = [Issue(step="⑤ 티스토리 대본 생성", kind=kind, detail="결함")]
        got = _find_resume_step(_mk_action(), iss)
        assert got == "⑤ 티스토리 대본 생성", f"kind={kind} 가 재생성을 건너뛴다 (got={got})"
        assert got != VERIFY_ONLY


def test_판정_상한이_실측_최대보다_충분히_크다():
    """90초 하드코딩 회귀 방지 — 실측 성공 최대 86.1s 대비 2배 이상."""
    from shared.llm import judge_timeout

    t = judge_timeout()
    assert t >= 172, f"판정 상한 {t}s — 실측 최대 86.1s 대비 여유 부족(2배 미만)"


def test_판정_alias_는_회로_면제를_유지한다():
    """`_nonessential` 이 `_CIRCUIT_EXEMPT_ALIASES` 를 무력화하던 충돌 회귀 방지."""
    from shared.llm import _CIRCUIT_EXEMPT_ALIASES
    import inspect
    import shared.llm as _m

    for a in ("fact_judge", "engagement_judge"):
        assert a in _CIRCUIT_EXEMPT_ALIASES, f"{a} 가 회로 면제 목록에서 빠졌다"
    src = inspect.getsource(_m.invoke_text_result)
    i = src.find("if _nonessential:")
    assert i >= 0, "_nonessential 분기를 찾지 못함 — 테스트를 갱신할 것"
    seg = src[i:i + 900]
    assert "_CIRCUIT_EXEMPT_ALIASES" in seg, (
        "_nonessential 분기가 면제 목록을 보지 않는다 — 게이트가 즉사하는 경로 재발")


# ══════════════════════════════════════════════════════════════════
# 10) 회로 면제를 플래그가 앞질러 무력화하는 병 — 구조로 차단
# ══════════════════════════════════════════════════════════════════
def test_회로면제_alias_에_nonessential_을_붙이지_않는다():
    """★ 같은 병이 세 번 났다 — 이제 사람 기억이 아니라 테스트가 막는다.

    `shared/llm.py` 평가 순서:
        if _nonessential:      → open/probe 면 SDK 미호출 즉시 폴백
        elif _gate == "open":  → _essential or 면제 alias 면 1회 실시도
    즉 `_nonessential=True` 는 **면제 분기에 도달조차 못 하게** 만든다.
    시스템이 "이 호출은 회로가 열려도 살려라" 고 판정해 둔 alias 에 이 플래그를 붙이면
    그 판정이 통째로 무력화된다.

    실측 피해:
      · engagement_judge — 네이버 글 3주간 전량 미채점(2026-08-01 수정, 622063b)
      · fact_judge       — 2026-08-04 24회 중 8회 `ok=0, 0ms`(SDK 미호출) →
                           판정 불가 → fail-closed → 21:00 테마 티스토리 **발행 0건**
    """
    import re
    from pathlib import Path

    import shared.llm as L

    exempt = set(getattr(L, "_CIRCUIT_EXEMPT_ALIASES", set()))
    assert exempt, "회로 면제 목록을 읽지 못했다 — 검사가 무의미해진다"

    root = Path(__file__).resolve().parent.parent
    bad: list[str] = []
    for f in root.rglob("*.py"):
        sp = str(f)
        if "__pycache__" in sp or "/.venv/" in sp or "/tests/" in sp:
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        # 같은 호출 안에서 alias 문자열과 _nonessential=True 가 함께 나오는지
        for m in re.finditer(r'["\'](\w+)["\'][^)]{0,400}?_nonessential\s*=\s*True', src, re.S):
            if m.group(1) in exempt:
                bad.append(f"{f.relative_to(root)}: {m.group(1)}")
    assert not bad, (
        "회로 면제 alias 에 _nonessential=True 가 붙어 면제가 무력화된다:\n  " + "\n  ".join(bad)
    )


def test_사실성_판정은_필수호출이다():
    """실패하면 fail-closed 로 **발행 자체가 막힌다** — 이보다 필수인 호출은 없다."""
    import inspect

    from JARVIS02_WRITER import prepublish_gate as G

    src = inspect.getsource(G._combined_quality_call)
    assert "_essential=True" in src, "사실성 판정이 필수 호출로 표시돼 있지 않다"
    assert "_nonessential=True" not in src, (
        "사실성 판정에 _nonessential 이 남아 있다 — 회로 면제가 무력화된다"
    )


# ══════════════════════════════════════════════════════════════════
# 11) 전수감사 2위 — 채점표가 요구하는 헤딩을 생성기가 만들지 않았다
# ══════════════════════════════════════════════════════════════════
def test_헤딩골격이_헌법에서_파생된다():
    """★ 채점표가 옳고 생성기가 헌법을 안 지키고 있었다.

    헌법(BLOG_SUPREME_LAW 제15조): 네이버 `H3 소제목 3~4개` /
    티스토리 `H1 1개 + H2 3~5개 + H3 0~3개`.
    그런데 `draft_writer` 골격은 `<h2>` 를 21곳에 박고 `<h3>` 는 0회였다.
    두 지시가 충돌하면 LLM 은 *구체적 골격 예시* 를 따른다 —
    실측 발행본 24편 h3 평균 0.0~0.8 · h1 평균 0.0~0.2.
    결과: 네이버 5점(N3+N4) · 티스토리 2점(T3)이 **매 글 죽었다**.
    """
    from JARVIS02_WRITER.seo_standards import heading_plan

    nv, ts = heading_plan("naver"), heading_plan("tistory")
    assert nv["section_tag"] == "h3", f"네이버 섹션 태그가 헌법과 다르다: {nv}"
    assert ts["section_tag"] == "h2", f"티스토리 섹션 태그가 헌법과 다르다: {ts}"
    assert ts["h1_required"] is True, "티스토리 H1 요구가 반영되지 않았다"
    assert nv["h1_required"] is False, "네이버는 H1 을 요구하지 않는다"
    # 파생인지 확인 — 헌법 원문이 함께 나와야 한다
    assert "H3" in nv["source"] and "H1" in ts["source"], "헌법 원문에서 파생하지 않는다"


def test_섹션_프롬프트가_플랫폼별_헤딩을_쓴다():
    """골격에 태그를 박으면 헌법을 바꿔도 따라오지 않는다."""
    import re

    import JARVIS02_WRITER.draft_writer as D

    seen = {}
    orig = D.invoke_text
    D.invoke_text = lambda alias, user_msg, **kw: seen.setdefault("msg", user_msg) or ""
    try:
        for pf, want in (("naver", "h3"), ("tistory", "h2")):
            seen.clear()
            try:
                D._gen_section_call1("k", "s", "r", "(헌법)", platform=pf, datasets=None)
            except Exception:
                pass
            msg = seen.get("msg", "")
            assert msg, f"{pf}: 프롬프트를 못 잡았다"
            assert "__SECTAG__" not in msg, f"{pf}: 플레이스홀더가 치환되지 않았다"
            tags = set(re.findall(r"<(h[1-6])>소제목", msg))
            assert tags == {want}, f"{pf}: 섹션 헤딩이 {tags} (기대 {want})"
    finally:
        D.invoke_text = orig


def test_섹션_회수정규식이_골격과_같은_태그를_쓴다():
    """골격과 회수 정규식이 어긋나면 섹션 2·3 이 통째로 유실된다."""
    import inspect

    import JARVIS02_WRITER.draft_writer as D

    src = inspect.getsource(D._gen_economic_ts_nv_parallel)
    assert "_heading_tags(platform)" in src, "회수 정규식이 파생 태그를 쓰지 않는다"
    assert 'r"<h2>소제목' not in src, "회수 정규식에 h2 가 박혀 있다"


def test_골격_플레이스홀더가_모두_치환된다():
    """치환을 빠뜨리면 프롬프트에 `<__SECTAG__>` 가 그대로 나간다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "JARVIS02_WRITER" / "draft_writer.py").read_text(encoding="utf-8")
    used = src.count("__SECTAG__>")                    # 골격 안 등장 횟수
    replaced = src.count('replace("__SECTAG__"')       # 치환 호출 횟수
    assert used == 0 or replaced >= 1, "플레이스홀더가 있는데 치환 호출이 없다"
    assert used == replaced or replaced * 2 >= used, (
        f"플레이스홀더 {used}곳 대비 치환 {replaced}곳 — 일부 경로가 치환되지 않는다"
    )


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-04 전수 감사 4·5·6·7·9위 — 회귀 못
# ══════════════════════════════════════════════════════════════════════════

def test_시크릿모듈이_env를_스스로_적재한다():
    """★ 가릴 값이 0이면 `mask()` 는 *아무 것도 안 가리면서 성공* 한다 (조용한 fail-open).

    실측: `.venv/bin/python -c "from shared.secrets import ..."` 로 부르면 0개였다.
    호출자의 import 순서에 기대면 언젠가 반드시 어긋난다.
    """
    from shared.secrets import secret_values

    assert len(secret_values()) > 0, "시크릿 0개 — .env 자가 적재가 깨졌다"


def test_마스킹이_실제로_먹는다():
    """설치 플래그가 아니라 *동작* 으로 확인 (CLAUDE.md patch_effective 표준)."""
    from shared.secrets import install_log_masking, mask, secret_values

    info = install_log_masking()
    assert info["effective"] is True, f"필터가 안 먹는다: {info}"
    _k, v = secret_values()[0]
    assert v not in mask(f"GET https://api/x?key={v}"), "평문이 그대로 통과"


def test_빈_시크릿목록은_캐시되지_않는다():
    """빈 결과를 캐시하면 그 프로세스는 영영 아무것도 못 가린다."""
    import inspect

    import shared.secrets as S

    src = inspect.getsource(S.secret_values)
    assert "if _cache:" in src, "truthy 검사가 아니면 빈 목록이 영구 고정된다"
    assert "if _cache is not None:" not in src


def test_preflight에_마스킹_검사가_등재돼_있다():
    """부팅 시점에 안 먹으면 그 뒤 모든 로그가 오염된다 — Layer 0 게이트."""
    from JARVIS00_INFRA.preflight import _CHECKERS

    assert "secret_masking" in [c for c, _ in _CHECKERS], "Layer 0 에 마스킹 검사 없음"


def test_로그회전이_적용돼_있다():
    """★ 무한 증가 로그는 사고 조사 때 '열리지 않는 파일' 이 된다 (실측 18MB·30MB)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "jarvis_daemon.py").read_text(encoding="utf-8")
    assert "RotatingFileHandler" in src, "회전 핸들러가 없다"
    assert "maxBytes" in src and "backupCount" in src


def test_데몬이_부팅에서_마스킹을_건다():
    """모듈에 함수만 있고 아무도 안 부르면 그건 적용이 아니다."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "jarvis_daemon.py").read_text(encoding="utf-8"))
    called = any(
        isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_install_mask"
        for n in ast.walk(tree)
    )
    assert called, "jarvis_daemon 이 install_log_masking 을 호출하지 않는다"


def test_결손알림_복구안내가_전부_파생된다():
    """★ 잡 ID 를 알림에 박으면 잡 이름이 바뀔 때 알림만 옛 이름을 가리킨다."""
    import inspect

    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    from JARVIS08_PUBLISH.publish_ledger import recovery_hint

    real_ids = {str(j.get("id")) for j in DEFAULT_JOBS}
    for pt in ("economic", "theme"):
        hint = "\n".join(recovery_hint(pt))
        assert "RUNBOOK" in hint, f"{pt}: 절차 문서 링크 없음"
        found = [i for i in real_ids if i in hint]
        assert found, f"{pt}: 실제 잡 ID 가 안내에 없다"

    src = inspect.getsource(recovery_hint)
    for lit in ("j01_economic_post", "j01_theme_post_21", "logs/daemon.log"):
        assert lit not in src, f"복구 안내에 리터럴 {lit!r} 이 박혀 있다"


def test_런북이_존재하고_명령을_담고_있다():
    from pathlib import Path

    rb = Path(__file__).resolve().parent.parent / "docs" / "RUNBOOK.md"
    assert rb.exists(), "docs/RUNBOOK.md 없음"
    body = rb.read_text(encoding="utf-8")
    assert "job_audit_publish_completeness" in body
    assert "restart_daemon.sh" in body


def test_채점결손을_슬롯감사가_잡는다():
    """★ 발행은 성공했으므로 어떤 경보도 안 울리던 사각지대 (실측 08-02~04 티스토리 3건)."""
    import datetime as dt
    import inspect

    from JARVIS08_PUBLISH.publish_ledger import job_audit_publish_completeness, scoring_gaps

    # 미래 창 → 결과는 비지만 쿼리 자체가 성립해야 한다
    s = dt.datetime.now() + dt.timedelta(days=400)
    assert scoring_gaps(s, s + dt.timedelta(hours=1), "theme") == []

    src = inspect.getsource(job_audit_publish_completeness)
    assert "scoring_gaps" in src, "슬롯 감사가 채점 결손을 보지 않는다"
    assert "unscored" in src


def test_재채점_창이_보상잡_일정에서_파생된다():
    """★ 보상을 이미 회수해 간 글을 다시 채점해봐야 보상은 발화하지 않는다."""
    import inspect

    from JARVIS03_RADAR.post_quality_analyzer import reward_cutoff

    cut = reward_cutoff()
    assert len(cut) == 19 and cut[4] == "-", f"시각 형식 이상: {cut}"

    # ★ 문자열이 아니라 *동작* 으로 확인한다 — 잡 시각을 바꾸면 창도 따라와야 한다.
    #   (뮤테이션 검증에서 `"DEFAULT_JOBS" in src` 만 보는 테스트가 가짜 통과를 냈다.)
    from JARVIS04_SCHEDULER import job_registry as JR

    target = next(j for j in JR.DEFAULT_JOBS if "quality_learner" in str(j.get("callback", "")))
    orig = dict(target.get("kwargs") or {})
    try:
        target["kwargs"] = {"hour": (int(orig.get("hour", 23)) + 5) % 24,
                            "minute": int(orig.get("minute", 0))}
        moved = reward_cutoff()
    finally:
        target["kwargs"] = orig
    assert moved != cut, "잡 시각을 바꿔도 재채점 창이 그대로 — 파생이 아니라 사본이다"

    src = inspect.getsource(reward_cutoff)
    assert "23:45" not in src, "시각이 박혀 있다"


def test_재채점은_점수만_쓴다():
    """제안·상태를 건드리면 같은 글이 사용자에게 두 번 간다."""
    import inspect

    from shared.db import save_quality_score

    src = inspect.getsource(save_quality_score)
    assert "quality_score IS NULL" in src, "이미 채점된 글을 덮어쓸 수 있다"
    for forbidden in ("suggestions=", "status=", "analyzed_at="):
        assert forbidden not in src, f"재채점이 {forbidden} 를 건드린다"


def test_재채점이_기존_잡에_배선돼_있다():
    """새 잡을 만들면 같은 LLM 락을 두 잡이 다툰다 (원칙①)."""
    import inspect

    from JARVIS03_RADAR import jobs as J

    assert "rescore_unscored" in inspect.getsource(J.job_analyzer_fallback)


def test_인프라_사유마다_오류타입이_갈라진다():
    """★ 4종이 전부 `HarnessInfraThrottle` 한 타입이면 로그만 보고 대응을 못 정한다.

    서버가 거절한 것(기다림)과 우리끼리 락을 다툰 것(동시 실행 구조)은 대응이 정반대다.
    """
    from JARVIS00_INFRA.harness import harness_error_type, infra_kind

    types = {harness_error_type(infra_kind(r))
             for r in ("timeout", "truncated", "throttle", "lock_contention")}
    assert len(types) == 4, f"타입이 뭉개졌다: {types}"
    assert all(t.startswith("HarnessInfraThrottle") for t in types)


def test_사유별_kind도_인프라로_판정된다():
    """판정이 접두사가 아니면 새 kind 가 '코드 결함' 으로 오분류돼 LLM 을 태운다."""
    from JARVIS00_INFRA.harness import Issue, _is_infra_issue, infra_kind, is_infra_kind

    for r in ("", "timeout", "truncated", "throttle", "lock_contention"):
        k = infra_kind(r)
        assert is_infra_kind(k), f"{k} 가 인프라로 안 잡힌다"
        assert _is_infra_issue(Issue(step="s", kind=k, detail=""))
    assert not is_infra_kind("draft_quality")
    assert not is_infra_kind("")


def test_사유별_kind가_자동수리_대상에서_빠진다():
    """★ ③원칙 — harness 만 고치고 severity 를 놔두면 게이트가 조용히 샌다."""
    from JARVIS00_INFRA.harness import infra_kind
    from JARVIS07_GUARDIAN.severity import is_transient

    for r in ("", "timeout", "truncated", "throttle", "lock_contention"):
        assert is_transient("HarnessX", "", "harness", kind=infra_kind(r)), \
            f"{infra_kind(r)} 가 자동수리 대상으로 샌다"
    # 진짜 코드 버그는 여전히 잡혀야 한다
    assert not is_transient("ImportError", "cannot import name X", "harness",
                            kind="draft_quality")


def test_인프라_kind_판별을_아무도_등가비교하지_않는다():
    """★ 재발 방지 — 이 병이 바로 severity 에서 났다 (집합 등가비교가 새 kind 를 놓침).

    저장소 전역에서 `INFRA_KIND` 를 == 나 집합 원소로 쓰는 코드를 금지한다.
    판별은 `harness.is_infra_kind()` 한 곳.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    owner = root / "JARVIS00_INFRA" / "harness.py"
    bad = []
    for f in root.rglob("*.py"):
        if ".venv" in f.parts or "__pycache__" in f.parts or f == owner:
            continue
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in re.finditer(r"(==\s*INFRA_KIND|INFRA_KIND\s*==|kind\s+in\s+\{[^}]*INFRA_KIND)", src):
            bad.append(f"{f.relative_to(root)}: {m.group(0)}")
    assert not bad, "INFRA_KIND 등가비교 잔존 — is_infra_kind() 로 물어야 한다:\n" + "\n".join(bad)


def test_provisional_해제가_단일_출구에서만_일어난다():
    """★ abort 경로가 provisional 을 안 풀면 성공한 시도의 오류가 영구 잔류한다."""
    import inspect

    import JARVIS00_INFRA.harness as H

    whole = inspect.getsource(H)
    assert whole.count("_finalize_attempt_errors(action_def.name)") == 1, \
        "provisional 정리 호출이 여러 곳 — finally 단일 출구여야 한다"

    src = inspect.getsource(H.run_action)
    tail = src[src.rindex("finally:"):]
    assert "_finalize_attempt_errors(" in tail, "정리가 finally 밖에 있다 — abort 시 누락"
    assert "_resolve_attempt_errors(" in tail, "송출 성공 시 무효화가 finally 밖에 있다"


def test_잡_선행검사_grace가_파생값이다():
    """★ 리터럴 600 은 실제 grace(3600)와 어긋나 연기된 발행이 소멸했다."""
    import inspect

    from JARVIS04_SCHEDULER import job_prereq as JP

    src = inspect.getsource(JP)
    assert "misfire_grace_time=effective_grace(" in src, "grace 가 파생값이 아니다"
    assert "misfire_grace_time=600" not in src, "리터럴 600 잔존"


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-05 프로덕션 감사 — 어젯밤 수정의 미비점 3건
# ══════════════════════════════════════════════════════════════════════════

def test_로그세척이_모든_로그디렉터리를_훑는다():
    """★ "3,006 → 0" 이 사실은 "내가 본 한 곳에서 0" 이었다.

    실물 로그 디렉터리는 5개인데 초판 `redact_logs` 는 `root/"logs"` 하나만 훑었고,
    하필 평문 봇 토큰 26회가 있던 `JARVIS02_WRITER/logs/scheduler.log` 가 사각지대였다.
    범위를 박으면 보고까지 거짓이 된다.
    """
    import inspect
    from pathlib import Path

    from shared.secrets import redact_logs

    src = inspect.getsource(redact_logs)
    assert 'root / "logs"' not in src, "로그 디렉터리가 한 곳으로 박혀 있다"
    assert 'rglob("logs")' in src, "로그 디렉터리를 실물에서 파생하지 않는다"

    root = Path(__file__).resolve().parent.parent
    real = {d for d in root.rglob("logs")
            if d.is_dir() and not {".venv", ".git", "node_modules"} & set(d.parts)}
    assert len(real) >= 2, f"로그 디렉터리가 {len(real)}개 — 이 테스트의 전제가 깨졌다"


def test_preflight가_마스킹을_검사만_하지_않고_건다():
    """★ 발행·분석은 subprocess 로 돈다 — 부모의 루트 필터가 안 닿는다.

    `ensure_preflight()` 는 모든 `__main__` 진입점의 의무 호출이므로,
    거기서 걸어야 자식 프로세스까지 덮인다.
    """
    import inspect

    from JARVIS00_INFRA import preflight as P

    src = inspect.getsource(P._check_secret_masking)
    assert "install_log_masking()" in src, "preflight 가 마스킹을 걸지 않고 검사만 한다"


def test_CI가_테스트_의존을_이_파일에서_설치한다():
    """★ CI 가 '빠진 의존' 으로 빨개지면 사람은 CI 를 안 보게 된다 — 게이트가 죽는 길.

    실측 2026-08-04: `pip install pytest python-dotenv` 로 고정돼 있어
    `requests` 를 건드린 커밋이 CI 를 `2 failed` 로 만들었다(로컬은 초록).
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    dep_file = root / "requirements-test.txt"
    assert dep_file.exists(), "requirements-test.txt 없음"

    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "requirements-test.txt" in ci, "CI 가 테스트 의존 파일을 쓰지 않는다"
    assert "pip install pytest python-dotenv" not in ci, "CI 에 의존 목록이 박혀 있다"

    pkgs = {l.strip() for l in dep_file.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")}
    assert "pytest" in pkgs


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-05 훅 정상화 — 2달간 죽어 있던 것을 잡았어야 할 테스트
# ══════════════════════════════════════════════════════════════════════════

def _hook_src() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent
            / ".githooks" / "pre-commit").read_text(encoding="utf-8")


def test_훅에_set_e가_없다():
    """★ `set -e` 는 '실패 즉시 멈춰라' 라 *실패를 받아서 판단하는* 코드와 양립 불가.

    이 한 줄 때문에 경고 모드·JARVIS_STRICT·3원칙 안내가 2달간 전부 도달 불가였다.
    """
    lines = [l.strip() for l in _hook_src().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert "set -e" not in lines, "set -e 가 되살아났다 — 아래 판단 코드가 전부 죽는다"


def test_3원칙_안내가_검사기_호출보다_앞에_있다():
    """★ 뒤에 있으면 *위반이 잡힌 바로 그 순간에만* 안 보인다 — 의도와 정반대."""
    src = _hook_src()
    assert src.index("3원칙 자가점검") < src.index('python3 "$SCRIPT"'), \
        "3원칙 안내가 검사기 호출 뒤에 있다 — 위반 시 사라진다"


def test_죽은_JARVIS_STRICT가_저장소에서_사라졌다():
    """★ 값을 읽는 코드가 0곳인데 문서 5곳에 등장하던 유령 스위치.

    '언급' 이 아니라 **실제 사용**만 잡는다 — 왜 없앴는지 설명하는 주석·문서는
    남아 있어야 한다(그게 없으면 다음 사람이 또 되살린다).
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    # 값을 *읽거나 세팅* 하는 형태만 위반. 산문 속 이름 언급은 정상.
    USE = re.compile(
        r"\$\{?JARVIS_STRICT"                      # 셸에서 값 읽기
        r"|getenv\(\s*[\"']JARVIS_STRICT"          # 파이썬 os.getenv
        r"|environ(?:\.get\(|\[)\s*[\"']JARVIS_STRICT"  # os.environ
        r"|^\s*(?:export\s+)?JARVIS_STRICT\s*[:=]"  # 셸/yaml 세팅
    )
    alive = []
    for f in list(root.rglob("*.py")) + list(root.rglob("*.md")) \
            + list(root.rglob("*.sh")) + list(root.rglob("*.yml")) \
            + [root / ".githooks" / "pre-commit"]:
        if not f.is_file() or {".venv", "__pycache__", ".git", "node_modules"} & set(f.parts):
            continue
        for i, l in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if USE.search(l):
                alive.append(f"{f.relative_to(root)}:{i}: {l.strip()[:70]}")
    assert not alive, "존재하지 않는 스위치를 실제로 쓰는 곳:\n" + "\n".join(alive)


def test_훅이_실제로_막고_안내한다(tmp_path):
    """★ 이 테스트가 2달 전에 있었으면 즉시 잡혔다 — 실제 git 저장소에서 훅을 돌린다.

    진짜 저장소를 건드리지 않으려고 임시 저장소에 훅과 *가짜 검사기* 를 심는다.
    검사기가 실패(1)를 내면 커밋이 막히고 안내가 나오는지, 성공(0)이면 통과하는지 본다.
    """
    import subprocess
    from pathlib import Path

    repo = tmp_path / "probe"
    (repo / ".githooks").mkdir(parents=True)
    (repo / "shared").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=repo, check=True)

    hook = repo / ".githooks" / "pre-commit"
    hook.write_text(_hook_src(), encoding="utf-8")
    hook.chmod(0o755)
    checker = repo / "shared" / "precommit_check.py"
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    def _commit(msg):
        return subprocess.run(["git", "commit", "-m", msg], cwd=repo,
                              capture_output=True, text=True)

    # ① 위반 있음 → 차단 + 안내
    checker.write_text('import sys\nprint("가짜 위반 1건")\nsys.exit(1)\n', encoding="utf-8")
    r = _commit("blocked")
    out = r.stdout + r.stderr
    assert r.returncode != 0, "위반이 있는데 커밋이 통과했다"
    assert "3원칙 자가점검" in out, "위반 시 3원칙 안내가 안 나온다 (종전 버그)"
    assert "--no-verify" in out, "우회 방법 안내가 없다"

    # ② 검사기 부재 → fail-closed (통과시키지 않는다)
    checker.unlink()
    assert _commit("no-checker").returncode != 0, "검사기가 없는데 커밋이 통과했다"

    # ③ 위반 없음 → 통과
    checker.write_text('import sys\nsys.exit(0)\n', encoding="utf-8")
    r = _commit("ok")
    assert r.returncode == 0, f"정상인데 막혔다: {r.stdout + r.stderr}"


def test_검사_개수를_손으로_세지_않는다():
    """★ 손으로 더한 숫자는 검사를 늘려도 안 따라와 조용히 거짓말을 한다."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent
           / "shared" / "precommit_check.py").read_text(encoding="utf-8")
    m = re.search(r'print\(f"✅ JARVIS pre-commit 통과 — \{([^}]+)\}', src)
    assert m, "통과 문구를 찾지 못했다"
    assert "checks_run" not in m.group(1), "요약 문구가 손으로 더한 값을 쓴다"
    assert "rep.ran" in m.group(1), "실행한 카테고리에서 파생하지 않는다"


def test_crossproc_검사가_조용히_사라지지_않는다():
    """★ 이 검사는 sys.path 때문에 쓰인 이래 한 번도 실행된 적이 없었다.

    `except: pass` 가 ModuleNotFoundError 를 삼켰고 화면엔 계속 '위반 0건' 이 떴다.

    문자열이 아니라 **동작** 으로 확인한다 — 검사기 안의 selfcheck 가 *실제로 불렸는지*
      기록해서 본다. 소스에 문구가 있는 것과 그 코드가 실행되는 것은 다르다
      (뮤테이션 검증에서 구조 검사가 가짜 통과를 냈다).
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    spec = importlib.util.spec_from_file_location(
        "_pc_probe", root / "shared" / "precommit_check.py")
    pc = importlib.util.module_from_spec(spec)
    # @dataclass 가 sys.modules 를 조회하므로 exec 전에 등록해야 한다.
    sys.modules["_pc_probe"] = pc
    try:
        spec.loader.exec_module(pc)
    finally:
        sys.modules.pop("_pc_probe", None)

    from JARVIS04_SCHEDULER import job_llm_priority as jlp

    called = []
    orig = jlp.selfcheck
    jlp.selfcheck = lambda *a, **k: (called.append(1), orig(*a, **k))[1]
    try:
        rep = pc.Report()
        pc.check_crossproc(rep)
    finally:
        jlp.selfcheck = orig

    assert called, "crossproc 이 job_llm_priority.selfcheck 를 부르지 않는다 — 검사가 죽어 있다"
    assert not [v for v in rep.violations if "self-check" in v.check_id], \
        f"crossproc 자가검사 실패: {[v.text for v in rep.violations]}"


def test_낡은_검사_개수_표기가_남아있지_않다():
    """★ 검사는 27→60 으로 늘었는데 문서 9곳이 '27종' 이라고 적혀 있었다."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    stale = []
    for f in list(root.rglob("*.md")) + list(root.rglob("*.py")):
        if not f.is_file() or {".venv", "__pycache__", ".git", "node_modules"} & set(f.parts):
            continue
        if f.name == "ERRORS.md" or "decisions" in f.parts or "tests" in f.parts:
            continue   # 사고 기록·ADR·테스트 설명문은 역사다 — 보존한다
        for i, l in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if "27종" in l and "precommit" in l.lower():
                stale.append(f"{f.relative_to(root)}:{i}")
    assert not stale, "낡은 개수 표기 잔존:\n" + "\n".join(stale)
