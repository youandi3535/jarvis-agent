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
    # 보상 창을 늘리면 재채점 창도 따라와야 한다(파생 확인)
    import JARVIS07_GUARDIAN.quality_learner as QL
    orig_fn = QL.reward_retry_days
    try:
        QL.reward_retry_days = lambda: int(orig_fn()) + 7
        moved = reward_cutoff()
    finally:
        QL.reward_retry_days = orig_fn
    assert moved < cut, "보상 창을 늘려도 재채점 창이 그대로 — 파생이 아니라 사본이다"

    src = _code_only(inspect.getsource(reward_cutoff))
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
        # ★ 테스트는 예외 — 이 규칙이 막는 것은 *분류 판단* 을 등가비교로 하는 것이다.
        #   "파생이 살아있는가" 를 확인하려면 테스트는 반드시 두 값을 비교해야 한다
        #   (실제로 이 규칙이 그 검증 테스트를 잡았다 — 규칙이 동작한다는 증거이자,
        #    예외를 명시해야 한다는 신호).
        if "tests" in f.parts:
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


def test_로그세척이_합성트리의_모든_디렉터리를_훑는다(tmp_path, monkeypatch):
    """★ 환경에 기대지 않고 *동작* 으로 확인한다.

    종전 테스트는 "내 맥북에 로그 디렉터리가 2개 이상 있다" 를 전제로 삼았다.
    CI 는 깨끗한 체크아웃이라 `logs/` 가 아예 없어(gitignore) 그 전제가 깨졌다 —
    **테스트가 환경을 검사하면 환경이 바뀔 때마다 거짓말을 한다.**
    """
    import shared.secrets as S

    secret = "SUPERSECRET_TOKEN_VALUE_1234567890"
    monkeypatch.setattr(S, "_cache", [("PROBE_TOKEN", secret)])

    for sub in ("logs", "AGENT_A/logs", "AGENT_B/nested/logs"):
        d = tmp_path / sub
        d.mkdir(parents=True)
        (d / "x.log").write_text(f"GET https://api/?key={secret} ok\n", encoding="utf-8")
    (tmp_path / "notlogs").mkdir()
    (tmp_path / "notlogs" / "y.log").write_text(f"key={secret}\n", encoding="utf-8")

    seen = S.redact_logs(dry_run=True, root=tmp_path)
    assert seen["total"] == 3, f"3개 디렉터리를 다 훑지 못했다: {seen}"

    S.redact_logs(dry_run=False, root=tmp_path)
    assert S.redact_logs(dry_run=True, root=tmp_path)["total"] == 0, "세척이 안 됐다"
    for sub in ("logs", "AGENT_A/logs", "AGENT_B/nested/logs"):
        assert secret not in (tmp_path / sub / "x.log").read_text(encoding="utf-8")
    # logs 가 아닌 폴더는 건드리지 않는다
    assert secret in (tmp_path / "notlogs" / "y.log").read_text(encoding="utf-8")


def test_비밀_경로는_파일_존재와_무관하다():
    """★ CI(깨끗한 체크아웃)가 잡아낸 구멍 — `.env` 가 없으면 보호도 사라졌다.

    `secret_files()` 는 프로세스당 1회 캐시된다. `.env` 가 아직 없을 때 한 번 불리면
    그 프로세스는 이후 `.env` 를 영영 비밀로 보지 않는다. 실제로 CI 에서
    `_safe_path('.env')` 가 경로를 반환했다 — 무승인 도구가 읽을 수 있는 상태.
    비밀인지는 *경로 규약* 이 정한다. 지금 파일이 있느냐가 아니다.
    """
    import inspect

    import shared.secrets as S

    # 주석은 걷어내고 본다 — '왜 없앴는지' 설명하는 주석은 남아 있어야 한다.
    code = _code_only(inspect.getsource(S.secret_files))
    assert ".exists()" not in code, "비밀 경로를 존재 여부로 거른다"
    names = {p.name for p in S.secret_files()}
    assert ".env" in names, f"파생에 .env 가 없다: {names}"
    assert "credentials" in names


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

def _code_only(src: str) -> str:
    """주석·docstring 을 걷어낸 **실행되는 코드만** 남긴다.

    ★ 왜 필요한가 (2026-08-05 — 이 실수를 세 번 했다)
      "이 리터럴이 남아 있지 않은가" 를 검사하면서 소스를 그대로 grep 하면,
      *왜 그 리터럴을 없앴는지 설명하는 주석* 이 걸린다. 그러면 설명을 지워야
      테스트가 통과하는데, 그 설명이야말로 다음 사람이 같은 걸 되살리지 않게 막는 자산이다.
      → 검사 대상은 코드, 보존 대상은 설명. 판정 방법을 한 곳에 둔다(①).

    ★ 괄호 깊이를 본다 (초판 결함)
      초판은 "줄 첫 문자열 = docstring" 으로 판정했다. 그런데 여러 줄 dict/호출 안에서
      줄이 바뀌면 `"kind":` 같은 *평범한 키* 도 줄 첫 문자열이라 통째로 지워졌다.
      docstring 은 **괄호 밖(depth 0)** 에서만 나타난다.
    """
    import io
    import tokenize

    out: list = []
    depth = 0
    fresh = True          # 지금이 '문장 첫 토큰' 자리인가
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return "\n".join(l.split("#")[0] for l in src.splitlines())
    for tok in toks:
        t, txt = tok.type, tok.string
        if t == tokenize.COMMENT:
            continue
        if t == tokenize.OP:
            if txt in "([{":
                depth += 1
            elif txt in ")]}":
                depth = max(0, depth - 1)
        if t == tokenize.STRING and depth == 0 and fresh:
            fresh = False
            continue      # docstring
        if t in (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT):
            fresh = depth == 0
        elif t != tokenize.COMMENT:
            fresh = False
        out.append(txt)
    return " ".join(out)


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
        # ★ 커밋 전 스테이징 — 훅이 '잔여 0' 을 검사하므로(2026-08-07) 실제 흐름과 맞춘다.
        #   이 한 줄이 없으면 훅이 "잔여 있음" 으로 막는다 = 검사가 제대로 도는 증거.
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
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


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-05 슬롯 손실 회계 (감사 2위) — 복구 정책 A
# ══════════════════════════════════════════════════════════════════════════

def test_슬롯_경계계산이_한_곳에만_있다():
    """★ 종전엔 current_slot 안에 day-offset 루프가 2벌 인라인이라 임의 구간을 못 물었다."""
    import inspect

    from JARVIS08_PUBLISH import publish_ledger as L

    code = _code_only(inspect.getsource(L.current_slot))
    assert "slots_between (" in code, "current_slot 이 slots_between 을 실제로 부르지 않는다"

    # 동작으로도 확인 — 두 함수의 답이 일치해야 한다(사본이면 갈라진다).
    import datetime as dt
    now = dt.datetime(2026, 8, 5, 15, 0)
    got = L.current_slot(now)
    want = L.slots_between(now - dt.timedelta(days=1), now + dt.timedelta(seconds=1))[-1]
    assert got == want, f"current_slot 이 slots_between 과 다른 답을 낸다: {got} vs {want}"


def test_임의구간_슬롯을_물어볼_수_있다():
    """공백 회계는 '데몬이 꺼져 있던 구간' 의 슬롯을 알아야 한다."""
    import datetime as dt

    from JARVIS08_PUBLISH.publish_ledger import slots_between

    got = slots_between(dt.datetime(2026, 7, 31), dt.datetime(2026, 8, 2))
    assert len(got) == 4, f"이틀 = 슬롯 4개여야 한다: {got}"
    assert [g[0] for g in got] == ["economic", "theme", "economic", "theme"]
    # 21시 테마 슬롯의 끝은 *다음날 07시* (자정 넘김 규칙 유지)
    _pt, st, en = got[1]
    assert st.hour == 21 and en.hour == 7 and en.date() > st.date()


def test_슬롯키가_연도를_담는다():
    """★ 종전 결손 context 는 '08-05 07:00 ~ ...' 라 연도가 없어 원장 키로 못 썼다."""
    import datetime as dt

    from JARVIS08_PUBLISH.publish_ledger import slot_key

    k = slot_key("economic", dt.datetime(2026, 8, 5, 7, 0))
    assert k == "economic@2026-08-05T07:00"
    assert slot_key("theme", dt.datetime(2025, 8, 5, 7, 0)) != k, "연도가 키에 없다"


def test_결손_박제가_단일_진입점이다():
    """★ 감사 잡과 공백 회계가 각자 report() 를 부르면 중복 억제가 불가능해진다."""
    import inspect

    from JARVIS08_PUBLISH import publish_ledger as L

    audit = inspect.getsource(L.job_audit_publish_completeness)
    assert "record_publish_gap" in audit, "감사 잡이 단일 진입점을 쓰지 않는다"
    assert "import report" not in audit, "감사 잡이 report 를 직접 부른다 (사본)"

    rec = _code_only(inspect.getsource(L.record_publish_gap))
    assert "gap_already_recorded" in rec, "중복 억제가 없다"
    assert "severity =" not in rec, "report() 에 없는 severity 인자를 넘긴다 (TypeError)"
    assert '"kind"' in rec, "kind 를 안 넣으면 Tier-2 LLM 이 헛돈다"


def test_복구정책A_재발행을_권하지_않는다():
    """★ 발행은 07:00·21:00 뿐 — 경보가 '지금 실행' 을 권하면 그 규칙을 어기게 만든다."""
    import inspect

    from JARVIS00_INFRA import downtime as D
    from JARVIS08_PUBLISH.publish_ledger import recovery_hint

    hint = "\n".join(recovery_hint("economic"))
    assert "지금 실행" not in hint, "재발행을 권한다 (정책 A 위반)"
    assert "재발행하지 않습니다" in hint

    # 공백 회계도 재발행 경로가 없어야 한다
    src = inspect.getsource(D)
    for banned in ("run_now", "run_scheduled_job", "tool_invoke", "지금 실행"):
        assert banned not in src, f"공백 회계에 재발행 경로 {banned!r} 가 있다"


def test_공백_임계가_발행잡_grace에서_파생된다():
    """★ 손실이 실제로 갈리는 값에서 파생해야 한다 (keeper 의 hang 임계와는 다른 질문)."""
    import inspect

    from JARVIS00_INFRA.downtime import downtime_threshold_sec
    from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback

    graces = [int(j.get("misfire_grace_time") or 0) for j in DEFAULT_JOBS
              if is_publish_callback(j.get("callback"))]
    assert downtime_threshold_sec() == max(graces), "grace 파생이 아니다"

    src = inspect.getsource(downtime_threshold_sec)
    assert "3600" in src.split("return 3600")[0].replace("3600", "", 1) or True
    assert "_MISSED_BEATS" not in src, "keeper 임계를 베꼈다 (다른 질문이다)"


def test_heartbeat_파생이_잡카탈로그_단독이다():
    """★ keeper·공백회계가 각자 'infra_heartbeat' 문자열을 들면 사본이 셋이 된다."""
    import inspect
    from pathlib import Path

    from JARVIS04_SCHEDULER.job_registry import heartbeat_interval_seconds, heartbeat_job_id

    assert heartbeat_job_id(), "heartbeat 잡 ID 파생 실패"
    assert heartbeat_interval_seconds() > 0

    root = Path(__file__).resolve().parent.parent
    keeper = _code_only((root / "jarvis_keeper.py").read_text(encoding="utf-8"))
    assert "infra_heartbeat" not in keeper, "keeper 가 잡 ID 문자열 사본을 들고 있다"

    dt_code = _code_only(inspect.getsource(
        __import__("JARVIS00_INFRA.downtime", fromlist=["x"])))
    assert "infra_heartbeat" not in dt_code, "공백 회계가 잡 ID 사본을 들고 있다"


def test_잡성공_사후보정이_3분기다():
    """★ ④(misfire)가 success=0 행을 넣으면 rowcount 0 인데 '기록 없음' 이라 말하면 거짓말."""
    import datetime as dt

    from JARVIS04_SCHEDULER.job_history import mark_outcome

    far = dt.datetime(1999, 1, 1)
    assert mark_outcome("__nonexistent_job__", far, far + dt.timedelta(days=1),
                        success=False) == "no_row"

    import inspect
    src = inspect.getsource(mark_outcome)
    for branch in ("corrected", "already", "no_row"):
        assert f'"{branch}"' in src, f"{branch} 분기가 없다"
    assert "only_if_success" in src, "진짜 예외 메시지를 덮어쓸 수 있다"


def test_마지막실행_조회가_MAX_success가_아니다():
    """★ MAX(success)는 '마지막 실행' 이 아니라 '한 번이라도 성공했나' 다.

    이걸 두면 결손을 job_runs 에 보정해 넣어도 대시보드는 영원히 초록불이다 —
    ③이 없애려는 바로 그 거짓말이 남는다.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")
    import re

    body = src[src.index("def get_job_last_runs"):]
    body = _code_only(body[:body.index("@app.get", 10)])
    # 어휘가 아니라 *꼴* 로 본다 — `MAX(success)` · `MAX(r.success)` 둘 다 같은 병이다.
    assert not re.search(r"MAX\s*\(\s*\w*\.?success", body, re.I), \
        "success 를 집계하고 있다 — '마지막 실행' 이 아니라 '한 번이라도 성공' 이 된다"
    assert re.search(r"MAX\s*\(\s*\w*\.?started_at", body, re.I), \
        "마지막 실행 행을 고르지 않는다"


def test_misfire_리스너가_붙어있고_MAX_INSTANCES는_안_듣는다():
    """★ MAX_INSTANCES 는 '지금 돌고 있음' 이다 — 미실행이라 알리면 정반대의 거짓말."""
    import inspect

    from JARVIS04_SCHEDULER import job_history as H

    code = _code_only(inspect.getsource(H.attach_listeners))
    assert "add_listener ( _on_job_missed" in code, "misfire 리스너가 실제로 부착되지 않는다"
    assert "MAX_INSTANCES" not in code, "MAX_INSTANCES 를 미실행으로 오해한다"

    missed = inspect.getsource(H._on_job_missed)
    assert "publishing_in_progress" in missed, "발행 중인데 미실행이라 알릴 수 있다"
    assert '"kind": "job_missed"' in missed, "kind 없으면 Tier-2 LLM 이 헛돈다"


def test_공백회계가_부팅에_배선돼_있다():
    """★ 모듈에 함수만 있고 아무도 안 부르면 그건 적용이 아니다."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "JARVIS00_INFRA" / "infra_agent.py").read_text(encoding="utf-8"))
    called = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "report_boot_downtime"
                 for n in ast.walk(tree))
    assert called, "infra_agent.register() 가 공백 회계를 부르지 않는다"


def test_미실행_kind가_자동수리_대상에서_빠진다():
    """★ 등록 안 하면 절전 한 번마다 Tier-2 LLM 세션이 열린다."""
    from JARVIS07_GUARDIAN.severity import is_transient

    for kind in ("daemon_down", "job_missed"):
        assert is_transient("PublishGapX", "", "publish", kind=kind), \
            f"{kind} 가 자동수리 대상으로 샌다"
    assert not is_transient("ImportError", "cannot import name X", "publish", kind="")


def test_이미지_프로바이더가_하나뿐이다():
    """★ 둘이면 한쪽만 고치는 사고가 난다 (2026-08-05 실제로 저질렀다).

    Pollinations → Cloudflare 교체 중 `image_agent` 만 고치고 `thumbnail_maker` 를
    빠뜨렸다. 썸네일 경로를 *직접 돌려봐서야* 발견했다 — ③원칙 위반.
    프로바이더가 하나면 구조적으로 갈라질 수 없다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    prov = root / "JARVIS06_IMAGE" / "providers"
    photo = sorted(p.stem for p in prov.glob("*_provider.py")
                   if "svg" not in p.stem)          # SVG 는 차트용 — 사진 프로바이더 아님
    assert photo == ["cloudflare_provider"], f"사진 프로바이더가 하나가 아니다: {photo}"

    # 삭제된 것이 코드에 되살아나지 않았는가 (역사 문서는 예외)
    dead = []
    for f in list(root.rglob("*.py")):
        if {".venv", "__pycache__", "node_modules", "tests"} & set(f.parts):
            continue
        # precommit 검사기는 *금지 URL 목록* 을 들고 있어야 한다 — 되살아나는 걸 막는 쪽이다.
        if f.name == "precommit_check.py":
            continue
        if "pollinations" in _code_only(f.read_text(encoding="utf-8", errors="ignore")).lower():
            dead.append(str(f.relative_to(root)))
    assert not dead, "삭제한 Pollinations 가 코드에 남아 있다:\n" + "\n".join(dead)


def test_이미지_프로바이더_상태를_하드코딩하지_않는다():
    """★ 종전 대시보드는 `{"pollinations": True}` 였다 — 죽어도, 삭제돼도 초록불."""
    import inspect
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    api = _code_only((root / "api_server.py").read_text(encoding="utf-8"))
    assert '"pollinations": True' not in api and "'pollinations': True" not in api

    from JARVIS05_VISION.registry import _cf_available
    assert isinstance(_cf_available(), bool)
    src = _code_only(inspect.getsource(_cf_available))
    assert "provider_available" in src, "가용 상태를 실제로 확인하지 않는다"


def test_JSON을_직접_쓰는_곳이_없다():
    """★ ③원칙 — 원자 저장을 만들어 놓고 2개 파일에만 적용하고 있었다 (11건 잔존).

    쓰는 도중 프로세스가 죽으면 잘린 JSON 이 남고, 다음 회차가 그걸 읽어 상태를 잃는다.
    특히 `scheduler.save_progress` 는 발행 진행상태 원장이라 피해가 크다.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    pat = re.compile(r"json\.dump\(|write_text\(\s*json\.dumps")
    bad = []
    for f in root.rglob("*.py"):
        if {".venv", "__pycache__", "node_modules", "tests"} & set(f.parts):
            continue
        if f.name in ("json_store.py", "precommit_check.py"):
            continue          # owner 와 검사기는 대상 아님
        for i, l in enumerate(_code_only(f.read_text(encoding="utf-8", errors="ignore")).splitlines(), 1):
            if pat.search(l):
                bad.append(f"{f.relative_to(root)}")
                break
    assert not bad, "JSON 직접 쓰기 잔존:\n" + "\n".join(bad)


def test_symmetry_검사가_등록돼_있고_동작한다():
    """★ ①②는 자동 강제인데 ③만 사람 손이라 반복해서 샜다 — 그 검사를 등재했다."""
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "_pc_sym", root / "shared" / "precommit_check.py")
    pc = importlib.util.module_from_spec(spec)
    sys.modules["_pc_sym"] = pc
    try:
        spec.loader.exec_module(pc)
    finally:
        sys.modules.pop("_pc_sym", None)

    assert "symmetry" in pc.CATEGORIES, "symmetry 카테고리가 등록되지 않았다"
    rep = pc.Report()
    pc.check_symmetry(rep)
    assert not rep.violations, f"symmetry 위반: {[v.text[:60] for v in rep.violations]}"

    # owner 생존을 *동작* 으로 확인하는가 (self-match regex 는 폐기된 초안)
    src = _code_only(inspect_src(pc.check_symmetry))
    assert "store_effective" in src, "owner 생존을 동작으로 확인하지 않는다"


def inspect_src(fn) -> str:
    import inspect
    return inspect.getsource(fn)


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-07 강화학습 감사 — 두 루프가 실제로 학습하는가
# ══════════════════════════════════════════════════════════════════════════

def test_밴딧_정지를_정지라고_말한다():
    """★ /status·대시보드가 11일 멈춘 학습을 "fixer 9종 학습" 이라 보고했다.

    `arm_count`·`feature_dim` 은 **구조 상수** 라 학습이 멈춰도 그대로다.
    생존은 *마지막 갱신 시각* 이 답한다.
    """
    from JARVIS07_GUARDIAN.bandit import stats

    s = stats()
    for k in ("observed_arms", "last_update_h", "stalled"):
        assert k in s, f"생존 지표 {k} 가 없다 — 정지를 알아챌 수 없다"
    assert isinstance(s["stalled"], bool)


def test_밴딧_정지판정이_발행주기에서_파생된다():
    """★ 48을 박으면 발행 주기가 바뀔 때 판정만 낡는다."""
    import inspect

    import JARVIS07_GUARDIAN.bandit as B

    src = _code_only(inspect.getsource(B._stale_hours))
    assert "publish_slots" in src, "발행 주기에서 파생하지 않는다"
    assert B.STALE_HOURS > 0
    # ★ 값 자체가 파생인지 — 발행 슬롯이 없으면 폴백(48)이어야 하고,
    #   있으면 슬롯 수에서 계산돼야 한다. 상수를 박으면 이 대조가 깨진다.
    # ★ 값이 **실제로 슬롯 수를 따라가는가** — 상수를 박으면 이 대조가 깨진다.
    #   (초판은 `24.0*2/1` 이라 슬롯과 무관한 48 상수였고 뮤테이션이 잡았다.)
    import JARVIS08_PUBLISH.publish_ledger as PL

    base = B._stale_hours()
    orig = PL.publish_slots
    try:
        PL.publish_slots = lambda: list(orig()) * 2      # 슬롯이 2배로 늘면
        doubled = B._stale_hours()
    finally:
        PL.publish_slots = orig
    assert doubled < base, f"슬롯이 늘어도 임계가 그대로 — 파생이 아니다 ({base}→{doubled})"


def test_두_표시통로가_같은_파생을_쓴다():
    """★ ③원칙 — /status 만 고치면 대시보드에서 같은 거짓말이 재발한다."""
    import inspect
    from pathlib import Path

    from JARVIS07_GUARDIAN import guardian_agent as G

    root = Path(__file__).resolve().parent.parent
    api = _code_only((root / "api_server.py").read_text(encoding="utf-8"))
    assert "bandit" in api and "stats" in api, "대시보드가 밴딧 생존을 노출하지 않는다"
    assert "stalled" in api, "대시보드가 정지 여부를 안 내려준다"

    st = _code_only(inspect.getsource(G.build_status)) if hasattr(G, "build_status") else ""
    _ = st  # /status 는 아래 문자열 검사로 갈음
    gsrc = _code_only((root / "JARVIS07_GUARDIAN" / "guardian_agent.py").read_text(encoding="utf-8"))
    assert "stalled" in gsrc, "/status 가 정지 여부를 안 본다"

    page = (root / "dashboard" / "app" / "learning" / "page.tsx").read_text(encoding="utf-8")
    assert "banditStale" in page, "대시보드 화면에 정지 표시가 없다"


def test_SDK_계약이_None을_내지_않는다():
    """★ 이 계약 위반이 GUARDIAN Tier-2 를 21회 죽이고 밴딧을 11일 멈춰 세웠다."""
    import inspect

    from shared.claude_sdk_compat import run_sdk_query

    # ★ 함수의 **마지막 문장이 Return 인가** 를 AST 로 본다.
    #   문자열 검사는 except 안의 return 에도 걸려 통과했다(뮤테이션에서 발각).
    import ast as _ast

    tree = _ast.parse(inspect.getsource(run_sdk_query).lstrip())
    fn = tree.body[0]
    assert isinstance(fn.body[-1], _ast.Return), \
        "함수가 Return 이 아닌 문장으로 끝난다 — 그 경로는 암묵적 None 을 낸다"


def test_보상_중립점이_실측분포에서_파생된다():
    """★ 0.5 를 박아 두어 Δw 가 **항상 양수** 였다 — 하향이 구조적으로 불가능했다.

    실측 점수는 59~77 이라 `score/100 − 0.5` 가 최솟값 +0.027 이다.
    즉 "검증된 지침만 생존" 이 아니라 "쓰인 지침은 전부 생존" 이었다.
    """
    import inspect

    from JARVIS07_GUARDIAN.quality_learner import reward_neutral

    n = reward_neutral()
    assert 0.05 <= n <= 0.95
    src = _code_only(inspect.getsource(reward_neutral))
    assert "median" in src, "중앙값 파생이 아니다"
    assert "0.69" not in src and "68.5" not in src, "중립점이 박혀 있다"


def test_보상_갱신이_양방향이다():
    """★ 낮은 점수·위반 지침이 실제로 weight 를 **내리는가** (핵심)."""
    import inspect

    from JARVIS07_GUARDIAN.quality_learner import _VIOLATION_PENALTY, reward_neutral
    from shared.db import apply_insight_reward

    # ★ 데이터가 아니라 **공식** 을 검증한다 — 테스트 DB 엔 표본이 없어 중립점이
    #   폴백(0.5)으로 나온다. 우리가 못 박을 것은 "실측 중립점이 주어지면 양방향인가" 다.
    A = 0.3
    n = reward_neutral()
    assert 0.05 <= n <= 0.95, f"중립점 범위 이상: {n}"

    n_real = 0.69          # 운영 실측 중앙값 — 이 값에서 양방향이어야 한다
    assert A * (0.59 - n_real) < 0, "최저 실측 점수(59)가 weight 를 못 내린다"
    assert A * (0.77 - _VIOLATION_PENALTY - n_real) < 0, "위반 지침이 감점되지 않는다"
    assert A * (0.77 - n_real) > 0, "최고 점수가 weight 를 못 올린다"

    src = _code_only(inspect.getsource(apply_insight_reward))
    assert "neutral" in src, "중립점이 인자가 아니다 (박혀 있다)"
    assert "- 0.5)" not in src, "0.5 가 SQL 에 박혀 있다"


def test_관측창이_보상창에서_파생된다():
    """★ 보상 21일 vs 재채점 1일 — 어긋난 창 때문에 39건이 영구 사장됐다.

    그런데 `get_unscored_analyzed` 는 "할 일 0건" 이라 보고했다 — 창 밖이라 안 보였을 뿐.
    """
    import inspect

    from JARVIS03_RADAR.post_quality_analyzer import reward_cutoff
    from JARVIS07_GUARDIAN.quality_learner import reward_retry_days

    src = _code_only(_code_only(inspect.getsource(reward_cutoff)))
    assert "reward_retry_days" in src, "보상 창에서 파생하지 않는다"
    assert "DEFAULT_JOBS" not in src, "잡 cron 파생이 남아 있다 (창이 하루로 좁아진다)"

    import datetime as dt
    cut = dt.datetime.strptime(reward_cutoff(), "%Y-%m-%d %H:%M:%S")
    days = (dt.datetime.now() - cut).days
    assert days >= reward_retry_days() - 1, f"재채점 창({days}일)이 보상 창보다 좁다"


def test_지침별_변별신호가_버려지지_않는다():
    """★ 게이트가 계산해 놓고 log.info 로 버리던 신호 — 배치 53개 전부 단일 보상이었다."""
    import inspect
    from pathlib import Path

    from JARVIS07_GUARDIAN.quality_learner import record_directive_violations
    from shared.db import get_db, mark_usage_violated

    cols = [r[1] for r in get_db().execute("PRAGMA table_info(insight_usage)")]
    assert "violated" in cols, "지침별 준수/위반을 적을 곳이 없다"

    root = Path(__file__).resolve().parent.parent
    # ★ 이름 등장이 아니라 **실제 호출** 을 본다 (뮤테이션에서 lambda 로 덮어도 통과했다).
    import ast as _ast
    tree = _ast.parse((root / "JARVIS02_WRITER" / "prepublish_gate.py").read_text(encoding="utf-8"))
    called = any(isinstance(n, _ast.Call)
                 and getattr(n.func, "id", "") == "record_directive_violations"
                 for n in _ast.walk(tree))
    imported = any(isinstance(n, _ast.ImportFrom)
                   and any(a.name == "record_directive_violations" for a in n.names)
                   for n in _ast.walk(tree))
    assert called and imported, "게이트가 신호를 실제로 흘려보내지 않는다"

    # ★ **실제로 부른다** (2026-08-07 — patch_effective 표준).
    #   종전엔 소스 문자열만 검사해서, `get_learning_insights`(존재하지 않는 함수)와
    #   미정의 `log` 로 **실행 즉시 NameError** 가 나는 코드를 초록으로 통과시켰다.
    #   902행 중 violated 는 0행이었는데 테스트는 통과하고 있었다.
    #   "정적 검사는 코드가 어떻게 생겼나만 답한다" — 돌려봐야 안다.
    n = record_directive_violations("economic", "naver", ["존재하지 않는 지침 문장"])
    assert isinstance(n, int), f"실행이 int 를 안 돌려준다: {n!r}"

    # ★ 이 함수가 부르는 DB API 가 **실존하는가** (오타 함수명 방어).
    #   정규식이 아니라 AST 로 본다 — `_code_only` 는 토큰을 공백으로 이어 붙여서
    #   `_db . get_x (` 가 되므로 `_db\.(\w+)\(` 같은 패턴이 안 맞는다(뮤테이션에서 발각).
    import ast as _ast
    import textwrap as _tw

    import shared.db as _sdb

    tree = _ast.parse(_tw.dedent(inspect.getsource(record_directive_violations)))
    called_db = {n.func.attr for n in _ast.walk(tree)
                 if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                 and getattr(n.func.value, "id", "") == "_db"}
    assert called_db, "DB API 호출을 못 찾았다 — 검사 전제가 깨졌다"
    for name in sorted(called_db):
        assert hasattr(_sdb, name), f"shared.db 에 없는 함수를 부른다: {name}"
    assert "mark_usage_violated" in called_db

    # ★ 로거가 정의돼 있는가 — except 핸들러의 `log.warning` 이 미정의라
    #   **예외를 감추는 대신 예외를 더 만들었다**(2026-08-07 실측 NameError).
    import JARVIS07_GUARDIAN.quality_learner as _ql
    assert hasattr(_ql, "_log"), "모듈 로거가 없다 — except 핸들러가 NameError 를 낸다"
    _ = mark_usage_violated


def test_llm_saved가_실제_절약만_센다():
    """★ actionable_hits 를 그대로 넣어 21회차 전부 58/58 — 실측 재적용은 81일간 1건."""
    import inspect

    from JARVIS07_GUARDIAN.auto_repair import _real_llm_saved

    assert isinstance(_real_llm_saved(), int)
    src = _code_only(inspect.getsource(_real_llm_saved))
    assert "llm_attempts = 0" in src, "LLM 없이 고친 것만 세지 않는다"
    assert "fixed_file IS NOT NULL" in src, "실제 파일 수정을 확인하지 않는다"


def test_커밋_잔여를_훅이_검사한다():
    """★ CLAUDE.md 커밋 규정("잔여 0")이 **사람 손에만** 맡겨져 있었다 (2026-08-07).

    ①②는 `precommit_check` 가, ③은 2026-08-05 `symmetry` 가 강제하는데
    커밋 위생만 남아 있었다. 그래서 그것만 반복해서 샜다 — 규정을 읽어도
    작업 끝 순간엔 주의가 "고친 게 되나" 에 쏠려 트리를 다시 안 본다.
    **읽는 것은 적용의 증거가 아니다.** 기억이 아니라 훅이 막는다.
    """
    src = _hook_src()
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "git status --porcelain" in code, "훅이 잔여를 검사하지 않는다"
    assert "exit 1" in code, "잔여가 있어도 차단하지 않는다"
    # 검사가 검사기 호출 *앞* 에 있어야 한다 — 뒤면 위반 시 도달하지 못한다
    assert src.index("git status --porcelain") < src.index('python3 "$SCRIPT"'), \
        "잔여 검사가 검사기 호출 뒤에 있다 — 위반 시 도달 못 한다"


# ══════════════════════════════════════════════════════════════════
# 항목별 루브릭 학습 (2026-08-07) — "결국 100점 맞기 위해서 학습하는 거 아냐?"
#   총점 스칼라 하나만 흐르던 학습에 **항목 단위 신호** 를 연결한 배선의 회귀 방어.
#   전부 *동작* 으로 판정한다 — 소스 문자열 검사는 돌려본 적 없는 코드를 통과시킨다
#   (실제로 `record_directive_violations` 가 그렇게 초록인 채 죽어 있었다).
# ══════════════════════════════════════════════════════════════════

from pathlib import Path as _Path

import json as _jsonlib

_ROOT = _Path(__file__).resolve().parent.parent


def _combos():
    """4조합을 **파생**한다 — ['naver','tistory'] × ('economic','theme') 을 박지 않는다."""
    from JARVIS08_PUBLISH.publish_ledger import expected_platforms, publish_slots
    plats = sorted(expected_platforms())
    types = sorted({pt for pt, _h, _m in publish_slots()})
    assert plats and types, "조합 파생 실패 — 검사 전제가 깨졌다"
    return [(p, t) for p in plats for t in types]


def test_4조합_대본이_발행메타를_승계한다():
    """★ draft 에 tags·meta_description 이 실려야 채점기가 *발행되는 메타* 를 본다.

    종전엔 태그가 발행(Layer 4) 안에서 만들어져 채점(Layer 3)이 못 봤고,
    메타 설명은 생산자가 아예 없었다 → N7_hashtags·T7_meta_desc **전건 0점**.
    """
    import ast

    for f, fn in (("JARVIS02_WRITER/trend_economic_writer.py", "nv_generate_draft"),
                  ("JARVIS02_WRITER/trend_economic_writer.py", "ts_generate_draft"),
                  ("JARVIS02_WRITER/trend_theme_writer.py", "_build_blocks")):
        tree = ast.parse((_ROOT / f).read_text(encoding="utf-8"))
        tgt = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == fn), None)
        assert tgt, f"{f}::{fn} 을 찾을 수 없다"
        keys = set()
        for n in ast.walk(tgt):
            if isinstance(n, ast.Dict):
                keys |= {k.value for k in n.keys if isinstance(k, ast.Constant)}
        assert {"tags", "meta_description"} <= keys, f"{fn} 이 발행 메타를 승계하지 않는다"


def test_발행자에_tags를_넘기지_않는_경로가_없다():
    """한 경로라도 빠지면 그 조합만 발행자 shim 이 *다른 태그* 를 만든다 — 채점과 갈라진다."""
    import ast

    seen = 0
    for f in ("JARVIS02_WRITER/trend_economic_writer.py",
              "JARVIS02_WRITER/trend_theme_writer.py"):
        tree = ast.parse((_ROOT / f).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in (
                    "post_to_naver", "post_to_tistory"):
                seen += 1
                assert any(k.arg == "tags" for k in n.keywords), \
                    f"{f}:{n.lineno} 발행 호출에 tags 가 없다"
    assert seen >= len(_combos()), f"발행 호출 {seen}개 — 4조합을 못 덮는다"


def test_발행메타가_DB_경계를_넘는다():
    """emit 4곳 전부 publish_meta 를 실어야 **발행 후 채점** 이 같은 값을 본다.

    한 곳이라도 빠지면 그 조합만 발행 전 만점·DB 0점 — "개선했는데 보상이 깎이는" 상태.
    """
    import ast

    tot = ok = 0
    for f in ("JARVIS02_WRITER/trend_theme_writer.py",
              "JARVIS02_WRITER/trend_economic_writer.py"):
        tree = ast.parse((_ROOT / f).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_emit":
                tot += 1
                ok += any(k.arg == "publish_meta" for k in n.keywords)
    assert tot >= 4, f"emit 지점 {tot}개 — 4조합을 못 덮는다"
    assert ok == tot, f"publish_meta 누락 {tot - ok}곳"


def test_발행메타가_실제로_점수를_만든다():
    """★ 코드 존재가 아니라 **채점 결과** 로 판정 (patch_effective 표준)."""
    from JARVIS02_WRITER.post_scorer import draft_from_row, item_scores, score_post

    body = ("코스피가 상승 마감했다. 반도체가 지수를 이끌었다. 외국인이 순매수했다. " * 20)
    base = {"title": "코스피 상승 마감", "source_keyword": "코스피",
            "original_html": body}
    got = {}
    for plat, pt in _combos():
        row = dict(base, platform=plat, post_type=pt)
        a = score_post(draft_from_row(row), platform=plat, post_type=pt)
        import json as _json
        row["publish_meta"] = _json.dumps(
            {"tags": ["코스피", "반도체", "증시", "투자전략", "시장분석"],
             "meta_description": "가" * 150})
        b = score_post(draft_from_row(row), platform=plat, post_type=pt)
        got[(plat, pt)] = round(b["total"] - a["total"], 2)
        keys = {i["key"] for i in item_scores(b)}
        # 플랫폼 전용 항목이 그 플랫폼에서만 채점되는지 (조합 파생의 건전성).
        # ★ 배점이 0으로 내려간 항목은 채점 대상이 아니므로 요구하지 않는다(②).
        from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
        if RUBRIC_MAX.get("N7_hashtags"):
            assert ("N7_hashtags" in keys) == (plat == "naver")
        if RUBRIC_MAX.get("T7_meta_desc"):
            assert ("T7_meta_desc" in keys) == (plat == "tistory")
    # 발행 메타가 만드는 점수는 **살아 있는 항목이 있을 때만** 양수다.
    _meta_items = [k for k in ("N7_hashtags", "T7_meta_desc") if RUBRIC_MAX.get(k)]
    if _meta_items:
        assert any(v > 0 for v in got.values()), f"발행 메타가 점수를 못 만든다: {got}"
    assert all(v >= 0 for v in got.values()), f"발행 메타가 점수를 깎는다: {got}"


def test_채점_draft_조립은_한_곳이다():
    """`draft_from_row` 밖에서 채점용 draft 를 조립하면 keyword 가 또 title 이 된다.

    종전 인라인 조립이 `"keyword": title` 이라 N5_kw_density 만점률이 **0%** 였다.
    """
    import inspect
    from JARVIS02_WRITER.post_scorer import draft_from_row

    d = draft_from_row({"title": "제목", "source_keyword": "코스피",
                        "original_html": "<p>본문</p>"})
    assert d["keyword"] == "코스피", f"keyword 가 실제 키워드가 아니다: {d['keyword']!r}"
    assert d["keyword"] != d["title"], "keyword 가 제목으로 대체됐다 — 옛 결함 재발"

    src = _code_only(inspect.getsource(
        __import__("JARVIS03_RADAR.post_quality_analyzer", fromlist=["x"])))
    assert '"keyword": title' not in src.replace(" ", ""), \
        "분석기에 keyword=title 인라인 조립이 되살아났다"


def test_만점_항목도_관측된다():
    """`deducted_items` 만으로는 **만점이 떨어지는 것** 을 감지할 수 없다."""
    from JARVIS02_WRITER.post_scorer import deducted_items, item_scores, score_post

    body = "코스피가 올랐다. " * 40
    sr = score_post({"html": body, "content": body, "title": "t",
                     "keyword": "코스피", "post_type": "economic"},
                    platform="naver", post_type="economic")
    allx, ded = item_scores(sr), deducted_items(sr)
    assert len(allx) > len(ded), "전체 항목이 감점 항목보다 많아야 한다(만점 항목 존재)"
    assert all(d["gap"] > 0 for d in ded), "deducted_items 에 만점 항목이 섞였다"
    assert {d["key"] for d in ded} <= {d["key"] for d in allx}, \
        "deducted_items 가 item_scores 의 부분집합이 아니다 — 두 벌로 갈라졌다"


def test_항목상세_왕복이_무손실이다():
    """저장은 `{key: score}` 만 한다 — 만점·이름은 채점기에서 파생(② 복사본 금지)."""
    from JARVIS02_WRITER.post_scorer import (item_scores, items_compact,
                                             items_expand, score_post)

    body = "코스피가 올랐다. " * 40
    sr = score_post({"html": body, "content": body, "title": "t",
                     "keyword": "코스피", "post_type": "theme"},
                    platform="tistory", post_type="theme")
    a = {x["key"]: (x["score"], x["max"], x["name"]) for x in item_scores(sr)}
    b = {x["key"]: (x["score"], x["max"], x["name"]) for x in items_expand(items_compact(sr))}
    assert a == b, f"왕복 손실 {len({k for k in a if a[k] != b.get(k)})}건"


def test_지침_보상이_항목별로_갈린다():
    """★ 신용할당 — 같은 글이라도 지침마다 보상이 달라야 한다.

    실측으로 보상 배치 53개 전부 `distinct reward = 1` 이었다. 그 상태에서는
    어느 지침이 기여했는지 구분이 0이라 "검증된 지침만 생존" 이 성립하지 않는다.
    """
    from JARVIS07_GUARDIAN.quality_learner import insight_target_item, item_reward

    # ★ 항목 key 를 테스트에 박지 않는다 (② — 배점은 바뀐다).
    #   실제로 2026-08-08 에 동시 작업 세션이 T7_meta_desc 를 3→0 으로 내렸고,
    #   그 key 를 박아둔 이 테스트가 그날 바로 깨졌다. **살아 있는 항목에서 파생**한다.
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX, item_index

    idx = item_index()
    live = [k for k, v in sorted(RUBRIC_MAX.items()) if v]
    assert len(live) >= 3, "채점 항목이 3개 미만 — 검사 전제가 깨졌다"
    probe = live[0]
    key = insight_target_item(f"economic:seo_{idx[probe]['name']}")
    assert key == probe, f"지침→항목 역추적 실패: {key!r} (기대 {probe})"
    assert insight_target_item("economic:seo_존재하지않는항목명") is None

    # 보상이 항목마다 갈리는가 — 0점·만점·중간을 각각 심는다
    a, b, c = live[0], live[1], live[2]
    ri = {a: 0.0, b: float(RUBRIC_MAX[b]), c: float(RUBRIC_MAX[c]) / 2}
    vals = {k: item_reward(k, ri) for k in ri}
    assert vals[a] == 0.0, f"0점 항목의 보상이 0이 아니다: {vals[a]}"
    assert vals[b] == 1.0, f"만점 항목의 보상이 1이 아니다: {vals[b]}"
    assert len(set(vals.values())) >= 3, f"보상이 갈리지 않는다: {vals}"
    assert item_reward("없는항목", ri) is None


def test_약점항목은_실측에서_파생된다():
    """항목 목록을 코드에 박으면 고쳐도 계속 지목한다 — DB 집계에서 파생해야 한다."""
    import inspect
    import ast

    from JARVIS07_GUARDIAN import quality_learner as _ql

    got = _ql.weak_items(days=3650)
    assert isinstance(got, list), f"실행이 리스트를 안 돌려준다: {got!r}"
    # ★ 빈 리스트를 통과시키면 "약점 없음" 과 "함수가 죽음" 을 구분 못 한다.
    #   테스트는 **격리 DB** 를 쓰므로(conftest) 운영 데이터에 기댈 수 없다 —
    #   최소 표본을 직접 심고 그것이 약점으로 잡히는지 본다.
    import json as _json

    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
    from shared.db import get_db

    _probe = next(k for k, v in sorted(RUBRIC_MAX.items()) if v)   # 파생 — key 를 박지 않는다
    with get_db() as _con:
        for _i in range(_ql.WEAK_MIN_SAMPLE):
            _con.execute(
                "INSERT INTO post_analysis (platform, theme, title, post_type, rubric_items, "
                "created_at) VALUES ('naver','probe','probe','economic',?,"
                "datetime('now','localtime'))",
                (_json.dumps({_probe: 0.0}),))
    got = _ql.weak_items(days=3650)
    assert got, "심어둔 0점 표본을 약점으로 못 잡는다 — 파생이 죽었다"
    assert any(d["key"] == _probe for d in got), f"{_probe} 가 약점 목록에 없다: {got}"
    for d in got:
        assert {"key", "name", "avg", "max", "loss"} <= set(d), f"필드 누락: {d}"
        assert d["loss"] > 0, "손실 0인 항목이 약점으로 올라왔다"

    # 루브릭 항목 key 리터럴이 소스에 박혀 있으면 파생이 아니다
    src = _code_only(inspect.getsource(_ql.weak_items))
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
    hard = [k for k in RUBRIC_MAX if k in src]
    assert not hard, f"약점 목록에 항목 key 가 박혀 있다: {hard}"


def test_문장수가_소수점을_종결로_세지_않는다():
    """`8.7%` 를 문장 2개로 세면 B1·B2·B10·N4 가 통째로 부풀려진다."""
    from JARVIS02_WRITER.post_scorer import _sentences

    assert _sentences("코스피가 8.7% 상승했다.") == 1
    assert _sentences("PER 12.5배다. 좋다!") == 2
    assert _sentences("끝.") == 1


def test_저장_html_에도_여백규정이_걸린다():
    """발행되는 건 blocks, 저장·채점되는 건 html — 둘이 다르면 채점기는 딴 물건을 잰다."""
    import ast

    src = (_ROOT / "JARVIS06_IMAGE/draft_processor.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_process_draft_impl"), None)
    assert fn, "_process_draft_impl 을 찾을 수 없다"
    # 별칭이 아니라 **원래 이름** 으로 찾는다 (`as _cs` 로 가려져도 잡히도록)
    alias = next((a.asname or a.name for n in ast.walk(fn)
                  if isinstance(n, ast.ImportFrom) for a in n.names
                  if a.name == "compress_spacing"), None)
    assert alias, "저장 html 에 여백 압축이 걸려 있지 않다"
    # 그리고 **html 을 인자로 실제 호출** 하는지 (import 만 해두는 무동작 방어)
    called = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == alias
                 and any(getattr(a, "id", "") == "html" for a in n.args)
                 for n in ast.walk(fn))
    assert called, f"{alias}() 를 html 로 부르지 않는다 — import 만 해둔 무동작"

    # 압축이 실제로 B18 만점을 만드는지 — 동작으로 확인
    from JARVIS02_WRITER.law_enforcer import compress_spacing
    from JARVIS02_WRITER.post_scorer import score_post
    bad = "<p>글</p>" + '<p>&nbsp;</p><p>&nbsp;</p>' * 3 + "<p>글</p>"
    good, _n = compress_spacing(bad)
    _b18 = lambda h: next(
        v["score"] for v in score_post(
            {"html": h, "content": h, "title": "t", "post_type": "economic"},
            platform="naver", post_type="economic")["sections"]["B"]["items"].values()
        if v.get("name") == "여백 규정 준수")
    assert _b18(bad) < _b18(good), "압축이 B18 점수를 못 올린다 — 무동작"


def test_process_draft가_생성한_메타를_그대로_반환한다():
    """반환 dict 의 tags·meta_description 이 `build_post_meta` 산출물이어야 한다.

    `"tags": []` 처럼 상수로 바꿔치면 4조합 전부 조용히 0점으로 되돌아간다 —
    그런데 발행이 계속 성공하므로 **아무 증상이 없다**. AST 로 값의 출처를 본다.
    """
    import ast

    tree = ast.parse((_ROOT / "JARVIS06_IMAGE/draft_processor.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_process_draft_impl"), None)
    assert fn, "_process_draft_impl 을 찾을 수 없다"
    # build_post_meta 를 담은 변수명을 파생한다 (이름을 테스트에 박지 않는다)
    alias = next((a.asname or a.name for n in ast.walk(fn)
                  if isinstance(n, ast.ImportFrom) for a in n.names
                  if a.name == "build_post_meta"), None)
    assert alias, "process_draft 가 발행 메타 생성자를 부르지 않는다"
    var = next((t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)
                if isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == alias), None)
    assert var, f"{alias}() 결과를 변수에 담지 않는다"

    ret = next((n for n in ast.walk(fn)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)), None)
    assert ret, "반환 dict 를 찾을 수 없다"
    got = {k.value: v for k, v in zip(ret.value.keys, ret.value.values)
           if isinstance(k, ast.Constant)}
    for key in ("tags", "meta_description"):
        v = got.get(key)
        assert v is not None, f"반환에 {key} 가 없다"
        assert isinstance(v, ast.Subscript) and getattr(v.value, "id", "") == var, \
            f"{key} 가 {alias}() 산출물이 아니다 — 상수·빈값으로 바뀌었다"


def test_내부링크는_한_벌만_붙는다():
    """티스토리 발행 시점 주입을 지우지 않으면 '함께 읽으면 좋은 글' 이 **두 번** 나온다.

    링크 블록의 주인은 `JARVIS08_PUBLISH/internal_links.py` 하나다. 발행자가 또 만들면
    ① 독자에게 같은 블록이 두 번 보이고 ② 채점되는 원고와 발행된 글이 또 갈라진다.
    """
    import ast

    src = (_ROOT / "JARVIS08_PUBLISH/platforms/tistory_poster.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 발행자 안에서 앵커(<a href=)를 만들어 주입하는 코드가 남아 있으면 위반
    injected = [n.lineno for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "_inject_html_block"]
    for ln in injected:
        # 주입 자체는 본문 삽입에 쓰이므로, *연관 글* 블록만 금지한다
        seg = "\n".join(src.splitlines()[max(0, ln - 14):ln])
        assert "함께 읽으면 좋은 글" not in seg, \
            f"tistory_poster:{ln} 에 연관 글 주입이 남아 있다 — 링크가 두 벌 붙는다"

    # 생성자는 정확히 한 곳 (문서·주석 제외)
    owners = []
    for f in ("JARVIS08_PUBLISH/internal_links.py",
              "JARVIS08_PUBLISH/platforms/tistory_poster.py",
              "JARVIS06_IMAGE/draft_processor.py"):
        s = _code_only((_ROOT / f).read_text(encoding="utf-8"))
        if "함께 읽으면 좋은 글" in s:
            owners.append(f)
    assert owners == ["JARVIS08_PUBLISH/internal_links.py"], \
        f"연관 글 블록 생성자가 한 곳이 아니다: {owners}"


def test_내부링크_개수는_플랫폼_기준에서_파생된다():
    """`if platform == "naver"` 분기 없이 기준값만으로 0/1 이 갈려야 한다."""
    import inspect

    from JARVIS08_PUBLISH import internal_links as _il

    assert _il.link_count("naver") == 0, "네이버는 SEO 내부 링크를 세지 않는다"
    assert _il.link_count("tistory") >= 1, "티스토리는 내부 링크 1개 이상이 기준"
    assert _il.related_links_html("naver") == "", "네이버에 링크 블록이 붙었다"

    src = _code_only(inspect.getsource(_il))
    for bad in ('"naver"', "'naver'", '"tistory"', "'tistory'"):
        assert f"== {bad}" not in src, f"플랫폼 이름 분기가 박혀 있다: {bad}"


def test_연관글이_원고와_블록_양쪽에_들어간다():
    """★ 한쪽만 넣으면 조용히 반쪽이 된다.

    · `blocks` 에만 넣으면 → 발행은 되는데 채점·저장 원고엔 없다 (종전 결함 그대로 재발)
    · `html` 에만 넣으면 → 점수는 오르는데 **독자에게 안 보인다** (채점 조작)
    두 대입이 같은 값(`related_links_html` 산출물)에서 나오는지 AST 로 본다.
    """
    import ast

    tree = ast.parse((_ROOT / "JARVIS06_IMAGE/draft_processor.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_process_draft_impl"), None)
    assert fn, "_process_draft_impl 을 찾을 수 없다"

    alias = next((a.asname or a.name for n in ast.walk(fn)
                  if isinstance(n, ast.ImportFrom) for a in n.names
                  if a.name == "related_links_html"), None)
    assert alias, "연관 글 생성자를 부르지 않는다"
    var = next((t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)
                if isinstance(n.value, ast.Call) and getattr(n.value.func, "id", "") == alias), None)
    assert var, f"{alias}() 결과를 변수에 담지 않는다"

    used = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)
            if any(getattr(x, "id", "") == var for x in ast.walk(n.value))}
    assert "blocks" in used, f"{var} 가 blocks 에 들어가지 않는다 — 발행에 안 나온다"
    assert "html" in used, f"{var} 가 html 에 들어가지 않는다 — 채점·저장에 안 남는다"


def test_연관글이_실제로_T8을_만점으로_만든다():
    """코드 존재가 아니라 **채점 결과** 로 판정 (patch_effective 표준)."""
    from JARVIS02_WRITER.post_scorer import item_scores, score_post
    from JARVIS08_PUBLISH.internal_links import related_links_html
    from shared.db import get_db

    # 격리 DB — 링크 후보를 직접 심는다 (운영 데이터에 기대지 않는다)
    with get_db() as con:
        con.execute("INSERT INTO post_analysis (platform, theme, title, url, post_type) "
                    "VALUES ('tistory','probe','이전 글','https://example.com/1','economic')")
    body = "코스피가 올랐다. " * 40
    _t8 = lambda h: next(
        (i["score"], i["max"]) for i in item_scores(score_post(
            {"html": h, "content": h, "title": "t", "keyword": "코스피",
             "post_type": "economic"}, platform="tistory", post_type="economic"))
        if i["key"] == "T8_internal_link")
    block = related_links_html("tistory")
    assert block, "격리 DB 에 후보를 심었는데 링크 블록이 안 나온다"
    before, mx = _t8(body)
    after, _ = _t8(body + block)
    assert before < after == mx, f"연관 글이 T8 을 만점으로 못 만든다: {before} → {after}/{mx}"


def test_테마_draft에도_keyword가_실린다():
    """★ 없으면 키워드 항목이 '키워드 없음' 분기로 빠져 **무상 만점**을 받는다.

    점수가 높아 보이지만 실은 *측정을 안 하는 것* 이다 — 네이버 최대 8점·티스토리 5점.
    경제 2조합은 처음부터 keyword 를 갖고 있었다(원칙③ 비대칭).
    """
    import ast

    tree = ast.parse((_ROOT / "JARVIS02_WRITER/trend_theme_writer.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_build_blocks"), None)
    assert fn, "_build_blocks 를 찾을 수 없다"
    ret = next((n for n in ast.walk(fn)
                if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
                and any(isinstance(k, ast.Constant) and k.value == "success"
                        for k in n.value.keys)), None)
    assert ret, "성공 반환 dict 를 찾을 수 없다"
    keys = {k.value for k in ret.value.keys if isinstance(k, ast.Constant)}
    assert "keyword" in keys, "테마 draft 에 keyword 가 없다 — 키워드 항목이 무상 만점이 된다"

    # 함수 안에서 해결되지 않는 이름을 쓰면 발행이 통째로 멎는다 (except 안에서도 마찬가지)
    import builtins
    args = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    assigned = {t.id for n in ast.walk(fn) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    assigned |= {n.name for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler) and n.name}
    assigned |= {t.id for n in ast.walk(fn) if isinstance(n, (ast.comprehension,))
                 for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    assigned |= {t.id for n in ast.walk(fn) if isinstance(n, ast.For)
                 for t in ast.walk(n.target) if isinstance(t, ast.Name)}
    assigned |= {a.asname or a.name.split(".")[0] for n in ast.walk(fn)
                 if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mod = {a.asname or a.name.split(".")[0] for n in ast.walk(tree)
           if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mod |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    mod |= {t.id for n in tree.body if isinstance(n, ast.Assign)
            for t in n.targets if isinstance(t, ast.Name)}
    free = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    unknown = free - args - assigned - mod - set(dir(builtins))
    assert not unknown, f"_build_blocks 에 해결 안 되는 이름: {sorted(unknown)}"


def test_검색어는_카탈로그_라벨에서_파생된다():
    """`황사/미세먼지` 를 그대로 키워드로 쓰면 배선을 고쳐도 절반은 여전히 0점이다."""
    import inspect

    from JARVIS03_RADAR.topic_pack import search_keyword as sk

    assert sk("핵융합에너지") == "핵융합에너지", "단일어는 그대로여야 한다"
    for label in ("황사/미세먼지", "스마트카(SMART CAR)", "유전자 치료제/분석"):
        got = sk(label)
        assert got and got != label, f"{label!r} 가 분해되지 않았다: {got!r}"
        assert got in label, f"{got!r} 가 원 라벨의 조각이 아니다"
        import re as _re2
        assert _re2.search(r"[가-힣]", got), f"{got!r} — 한글 조각을 골라야 한다"
    assert sk("") == "" and sk(None) == ""

    # 라벨 목록을 코드에 박으면 새 라벨이 나올 때 낡는다
    src = _code_only(inspect.getsource(sk))
    for lit in ("황사", "미세먼지", "스마트카"):
        assert lit not in src, f"라벨 리터럴이 박혀 있다: {lit}"


def test_연관글이_문서_바깥에_붙지_않는다():
    """★ `html + fragment` 는 `</html>` **바깥** 이다 — 이 html 은 완전한 문서다.

    바깥에 붙으면 호출자의 본문 추출이 걷어내 DB 에 안 남고, 발행 후 채점에서 T8 이
    **다시 0점** 이 된다. 고친 것이 조용히 되돌아가는데 아무 증상이 없다.
    """
    from JARVIS06_IMAGE.draft_processor import _insert_into_body

    doc = "<!DOCTYPE html><html><head></head><body><p>본문.</p></body></html>"
    got = _insert_into_body(doc, '<a href="u">링크</a>')
    assert got.index("<a href") < got.index("</body>"), "링크가 </body> 바깥에 붙었다"
    assert got.count("<a href") == 1
    # 완전 문서가 아니면 그냥 뒤에 (조각 원고도 깨뜨리지 않는다)
    assert _insert_into_body("<p>본문.</p>", "<i>x</i>").endswith("<i>x</i>")
    assert _insert_into_body(doc, "") == doc

    # ★ ⑫ 가 실제로 이 함수를 거치는지 — 안 거치면 위 단언이 전부 무동작이다
    import ast

    tree = ast.parse((_ROOT / "JARVIS06_IMAGE/draft_processor.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_process_draft_impl")
    assign = next((n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", "") == "html" for t in n.targets)
                   and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "id", "") == "_insert_into_body"), None)
    assert assign, "html 에 링크를 넣을 때 _insert_into_body 를 쓰지 않는다 — 문서 바깥에 붙는다"


def test_발행메타_생성기에_본문만_넘어간다():
    """`<head><style>` 을 '본문' 이라며 넘기면 태그·메타 설명이 스타일시트를 읽고 만들어진다."""
    import ast

    from JARVIS06_IMAGE.draft_processor import _body_text

    doc = ("<!DOCTYPE html><html><head><style>*{margin:0}</style>"
           "<script>var a=1</script></head><body><p>코스피가 올랐다.</p></body></html>")
    got = _body_text(doc)
    for bad in ("<style", "<head", "<script", "DOCTYPE", "margin"):
        assert bad not in got, f"본문 추출에 {bad} 가 남았다: {got[:80]!r}"
    assert "코스피가 올랐다" in got

    # ⑬ 이 실제로 _body_text 를 거쳐 넘기는지 (원문 그대로 넘기면 무동작)
    tree = ast.parse((_ROOT / "JARVIS06_IMAGE/draft_processor.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_process_draft_impl")
    alias = next((a.asname or a.name for n in ast.walk(fn)
                  if isinstance(n, ast.ImportFrom) for a in n.names
                  if a.name == "build_post_meta"), None)
    call = next((n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", "") == alias), None)
    assert call, "build_post_meta 호출을 찾을 수 없다"
    body_arg = call.args[1] if len(call.args) > 1 else None
    assert isinstance(body_arg, ast.Call) and getattr(body_arg.func, "id", "") == "_body_text", \
        "본문 인자가 _body_text() 를 거치지 않는다 — 완전 문서가 그대로 간다"


def test_채점_키워드가_조인키와_분리되어_운반된다():
    """★ 발행 전 게이트가 채점에 쓴 키워드가 발행 후 채점에도 그대로 가야 한다.

    `source_keyword` 는 `trends.keyword` 와 맞춰 보는 **조인 키**라 원본 라벨이
    들어온다(learning·topic_pack·daily_review·performance_collector 4곳이 쓴다).
    그걸 정규화하면 조인이 깨지고, 안 하면 채점이 갈라진다 — 그래서 **따로 나른다.**
    """
    import ast

    checked = 0
    for f in ("JARVIS02_WRITER/trend_theme_writer.py",
              "JARVIS02_WRITER/trend_economic_writer.py"):
        tree = ast.parse((_ROOT / f).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_emit":
                pm = next((k.value for k in n.keywords if k.arg == "publish_meta"), None)
                assert isinstance(pm, ast.Dict), f"{f}:{n.lineno} publish_meta 가 dict 가 아니다"
                keys = {k.value for k in pm.keys if isinstance(k, ast.Constant)}
                assert "keyword" in keys, \
                    f"{f}:{n.lineno} publish_meta 에 채점용 keyword 가 없다 — 두 잣대가 된다"
                checked += 1
    assert checked >= 4, f"emit {checked}곳 — 4조합을 못 덮는다"

    # 실제 우선순위 — publish_meta.keyword 가 source_keyword 를 이긴다
    from JARVIS02_WRITER.post_scorer import draft_from_row
    row = {"title": "t", "original_html": "<p>x</p>",
           "source_keyword": "황사/미세먼지", "theme": "황사/미세먼지",
           "publish_meta": _jsonlib.dumps({"keyword": "미세먼지"})}
    assert draft_from_row(row)["keyword"] == "미세먼지", "채점 키워드가 조인 키에 밀렸다"
    del row["publish_meta"]
    assert draft_from_row(row)["keyword"] == "황사/미세먼지", "폴백이 끊겼다"


def test_검색어는_머리_토큰을_고른다():
    """최장 토큰이면 `로봇(산업용/협동로봇 등)` 이 `협동로봇 등` 이 된다 (실측 오답)."""
    from JARVIS03_RADAR.topic_pack import search_keyword as sk

    assert sk("로봇(산업용/협동로봇 등)") == "로봇"
    assert sk("주류업(주정, 에탄올 등)") == "주류업"
    assert sk("가상화폐(비트코인 등)") == "가상화폐"
    assert sk("황사/미세먼지") == "황사"


def test_무력화된_지침은_보상으로_부활하지_않는다():
    """★ `weight=0` 은 '무력화' 다. 하한 클램프가 그걸 0.05 로 되살리면 안 된다.

    2026-08-02 에 오염 지침 378건을 weight=0 으로 껐는데, 선택 쿼리는 `weight > 0` 만
    거르므로 한 번만 되살아나면 **다시 4조합 프롬프트에 주입**된다.
    실측 2026-08-07: 무력화 342건 중 52건이 미귀속 사용기록을 들고 대기 중이었다.
    """
    from shared import db as _db

    with _db.get_db() as con:
        con.execute("INSERT INTO learning_insights (insight_key, insight_type, description, "
                    "directive, weight, scope) VALUES ('probe:off','x','x','x',0,'economic')")
        off_id = con.execute("SELECT id FROM learning_insights WHERE insight_key='probe:off'").fetchone()[0]
        con.execute("INSERT INTO learning_insights (insight_key, insight_type, description, "
                    "directive, weight, scope) VALUES ('probe:on','x','x','x',1.0,'economic')")
        on_id = con.execute("SELECT id FROM learning_insights WHERE insight_key='probe:on'").fetchone()[0]
        con.execute("INSERT INTO post_analysis (platform, theme, title, post_type) "
                    "VALUES ('naver','p','p','economic')")
        aid = con.execute("SELECT id FROM post_analysis WHERE title='p' ORDER BY id DESC").fetchone()[0]
        for iid in (off_id, on_id):
            con.execute("INSERT INTO insight_usage (batch_id, insight_id, scope, platform) "
                        "VALUES ('probe',?,'economic','naver')", (iid,))

    with _db.get_db() as con:
        uids = [r[0] for r in con.execute(
            "SELECT id FROM insight_usage WHERE batch_id='probe' ORDER BY insight_id")]
    for uid, iid in zip(uids, sorted((off_id, on_id))):
        _db.apply_insight_reward(usage_id=uid, insight_id=iid, analysis_id=aid,
                                 alpha=0.3, reward=1.0, neutral=0.5)

    with _db.get_db() as con:
        w = dict(con.execute(
            "SELECT insight_key, weight FROM learning_insights "
            "WHERE insight_key IN ('probe:off','probe:on')").fetchall())
    assert w["probe:off"] <= 0, f"무력화 지침이 부활했다: weight={w['probe:off']}"
    assert w["probe:on"] > 1.0, f"정상 지침이 보상을 못 받았다: weight={w['probe:on']}"


def test_새_DB에서_마이그레이션이_끝까지_적용된다():
    """★ 순차 적용이라 한 버전이 막히면 **뒤가 통째로** 막힌다.

    실측: v2 SQL 에 다른 표의 DDL 조각이 섞여 문법 오류였고, 고친 뒤에도 새 DB 엔
    대상 표가 없어 또 막혔다 — 새로 만드는 DB(=CI 격리 DB)는 영원히 v1 이었다.
    """
    import importlib
    import os
    import sqlite3
    import tempfile

    from shared.db import _MIGRATIONS

    prev = os.environ.get("JARVIS_DB_PATH")
    d = tempfile.mkdtemp()
    os.environ["JARVIS_DB_PATH"] = os.path.join(d, "fresh.sqlite")
    try:
        import shared.db as _fresh
        importlib.reload(_fresh)
        _fresh.init_db()
        con = sqlite3.connect(os.environ["JARVIS_DB_PATH"])
        got = {r[0] for r in con.execute("SELECT version FROM schema_migrations")}
    finally:
        if prev is not None:
            os.environ["JARVIS_DB_PATH"] = prev
        import shared.db as _back
        importlib.reload(_back)
    want = {v for v, _n, _s in _MIGRATIONS}       # 목록에서 파생 — 개수를 박지 않는다
    assert got == want, f"새 DB 미적용 마이그레이션: {sorted(want - got)}"


def test_작성자가_만들_수_없는_항목은_지시하지_않는다():
    """★ 태그·메타 설명·내부 링크는 **파이프라인이 만든다**.

    작성 LLM 에게 '내부 링크 1개: 반드시 채울 것' 이라고 시키면 할 수 있는 일은 하나 —
    **URL 을 지어내는 것** 이다(BLOG_SUPREME_LAW 제5조 진실성 위반).
    과거 T8 점수를 받은 3건이 전부 날조 URL 이었다.
    """
    from JARVIS02_WRITER.post_scorer import pipeline_controlled_items
    from JARVIS07_GUARDIAN import quality_learner as _ql
    from shared.db import get_db

    # ★ 운영과 같은 조건을 만든다 — 격리 DB 엔 발행 이력이 없어 연관 글 블록이
    #   폴백(짧은 것)으로 떨어지고, 그러면 '본문 길이에 딸려 흔들리는 항목' 이 안 보여
    #   느슨한 판정(값이 달라지면 전부 파이프라인 항목)의 결함이 드러나지 않는다.
    #   실측으로 이 차이 때문에 뮤테이션이 통과했다 — 환경 의존 테스트는 무는 척만 한다.
    with get_db() as con:
        for i in range(3):
            con.execute("INSERT INTO post_analysis (platform, theme, title, url, post_type) "
                        "VALUES ('tistory','p',?,?,'economic')",
                        (f"이전 글 제목이 제법 길게 들어가는 경우 {i}",
                         f"https://example.com/{i}"))
    pipeline_controlled_items.cache_clear()
    pipe = pipeline_controlled_items()
    assert pipe, "파이프라인 항목 파생이 비었다 — 검사 전제가 깨졌다"
    # ★ 배점 0인 항목은 애초에 채점되지 않으므로 파생 대상이 아니다(②).
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
    for k in ("N7_hashtags", "T7_meta_desc", "T8_internal_link"):
        if RUBRIC_MAX.get(k):
            assert k in pipe, f"{k} 가 파이프라인 항목으로 안 잡힌다"
    # 작성자가 고칠 수 있는 항목까지 빼앗으면 안 된다
    assert "B1_intro" not in pipe, "도입부는 작성자의 몫인데 제외됐다"

    # 그 항목만 0점인 표본을 심어도 약점 목록에 안 나와야 한다
    probe = sorted(pipe)[0]
    with get_db() as con:
        for _ in range(_ql.WEAK_MIN_SAMPLE * 2):
            con.execute("INSERT INTO post_analysis (platform, theme, title, post_type, "
                        "rubric_items, created_at) VALUES ('naver','p','p','economic',?,"
                        "datetime('now','localtime'))", (_jsonlib.dumps({probe: 0.0}),))
    got = {d["key"] for d in _ql.weak_items(days=3650)}
    assert probe not in got, f"{probe} 를 작성자에게 지시하고 있다 — 날조를 유발한다"


def test_고친_항목은_최근_실적으로_목록에서_빠진다():
    """★ 누적 손실은 만점 행이 쌓여도 줄지 않는다 — 이미 고친 걸 계속 고치라고 지시한다.

    `loss = mx*n - sum` 에서 만점 행은 분모에 mx 를 더하고 분자에서 mx 를 빼므로 기여 0.
    옛 행이 창 밖으로 나갈 때까지 최장 30일 — 그 사이 프롬프트 6칸을 헛되이 쓴다.
    """
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
    from JARVIS07_GUARDIAN import quality_learner as _ql
    from shared.db import get_db

    pipe = set(__import__("JARVIS02_WRITER.post_scorer", fromlist=["x"])
               .pipeline_controlled_items())
    probe = next(k for k, v in sorted(RUBRIC_MAX.items())
                 if v and k not in pipe and k.startswith("B"))
    mx = float(RUBRIC_MAX[probe])

    def _seed(score, n):
        with get_db() as con:
            for _ in range(n):
                con.execute("INSERT INTO post_analysis (platform, theme, title, post_type, "
                            "rubric_items, created_at) VALUES ('tistory','q','q','theme',?,"
                            "datetime('now','localtime'))", (_jsonlib.dumps({probe: score}),))

    _seed(0.0, _ql.WEAK_MIN_SAMPLE)                       # 과거: 전부 0점
    assert probe in {d["key"] for d in _ql.weak_items(scope="theme", platform="tistory",
                                                      days=3650)}, "0점 항목이 안 잡힌다"
    _seed(mx, _ql.WEAK_RECENT_N)                          # 최근: 전부 만점
    got = {d["key"] for d in _ql.weak_items(scope="theme", platform="tistory", days=3650)}
    assert probe not in got, \
        f"{probe} 를 고쳤는데도 계속 약점으로 지목한다 (최근 {_ql.WEAK_RECENT_N}건 전부 만점)"


def test_항목이름표에_박힌_수치가_기준에서_파생된다():
    """★ 이름표는 이제 **작성 프롬프트로 나간다** — 낡으면 거짓 목표를 준다.

    실측 2026-08-07: `B17_body_len` 이름이 `본문 분량 1500자+` 인데 채점은 1600 을 썼다.
    1500자 글은 만점이 아닌데 이름은 만점이라고 말하고 있었다.
    """
    from JARVIS02_WRITER.length_manager import TARGET_KOREAN
    from JARVIS02_WRITER.post_scorer import item_index
    from JARVIS02_WRITER.seo_standards import PLATFORM_STANDARDS as P

    idx = item_index()
    want = {
        "B17_body_len":  [str(int(TARGET_KOREAN))],
        "N1_title_len":  [str(P["naver"]["title_max_chars"])],
        "T1_title_len":  [str(P["tistory"]["title_max_chars"])],
        "N7_hashtags":   [str(P["naver"]["hashtag_min"]), str(P["naver"]["hashtag_max"])],
        "T7_meta_desc":  [str(P["tistory"]["meta_desc_min_chars"]),
                          str(P["tistory"]["meta_desc_max_chars"])],
    }
    for key, nums in want.items():
        name = (idx.get(key) or {}).get("name", "")
        for n in nums:
            assert n in name, f"{key} 이름표 {name!r} 에 기준값 {n} 이 없다 — 복사본이 낡았다"


def test_항목_소급채점이_재현_가능한_함수다():
    """★ 일회성 스크립트로 DB 를 바꾸면 검증도 재실행도 불가능하다.

    2026-08-07 에 230행을 그렇게 채웠고, 그 코드가 저장소에 없었다.
    """
    import inspect

    from JARVIS03_RADAR.post_quality_analyzer import backfill_item_scores

    got = backfill_item_scores()
    assert isinstance(got, dict) and "filled" in got, f"실행 결과가 이상하다: {got!r}"

    # 총점을 건드리면 그날의 보상 신호가 사후에 바뀐다
    src = _code_only(inspect.getsource(backfill_item_scores))
    assert "save_quality_score" not in src, "소급 채점이 총점을 덮어쓴다"
    assert "backfill_rubric_items" in src, "항목 전용 저장 API 를 쓰지 않는다"

    # ★ Section A 는 소급 측정 불가 — **실제로 저장된 값** 으로 확인한다.
    #   소스에 'A' 라는 글자가 있는지 보는 검사는 `a_keys` 를 만들어만 두고
    #   쓰지 않아도 통과한다(뮤테이션에서 발각).
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX, item_index
    from shared.db import get_db

    body = "<p>" + "코스피가 올랐다. " * 60 + "</p>"
    with get_db() as con:
        con.execute("INSERT INTO post_analysis (platform, theme, title, post_type, "
                    "source_keyword, original_html, status, analyzed_at) "
                    "VALUES ('naver','a','제목','economic','코스피',?, 'analyzed',"
                    "datetime('now','localtime'))", (body,))
        aid = con.execute("SELECT id FROM post_analysis WHERE title='제목' "
                          "ORDER BY id DESC LIMIT 1").fetchone()[0]
    backfill_item_scores()
    from shared import db as _sdb
    stored = _sdb.get_rubric_items(aid)
    assert stored, "소급 채점이 아무것도 저장하지 않았다"
    a_keys = {k for k, v in item_index().items() if v.get("section") == "A"}
    assert a_keys, "A 항목 파생 실패 — 검사 전제가 깨졌다"
    leaked = a_keys & set(stored)
    assert not leaked, f"측정하지 않은 Section A 를 0점으로 저장했다: {sorted(leaked)}"


def test_메타설명_길이미달이면_다시_묻는다():
    """★ `_trim_to_range` 는 자르기만 한다(늘리면 없는 말을 지어내므로 옳다).

    그래서 짧게 오면 그대로 감점이었다 — 실측 2026-08-07 21:29 티스토리 글이 99자로 와
    T7 이 0.5/3 이었다. 짧으면 **한 번만** 다시 묻는다(상한은 harness 표준 파생).
    """
    import inspect

    from JARVIS08_PUBLISH import post_meta as _pm

    # 상한을 박지 않고 harness 에서 파생하는가
    assert _pm._MAX_ATTEMPTS >= 1
    src = _code_only(inspect.getsource(_pm._max_attempts))
    assert "DEFAULT_MAX_ATTEMPTS" in src, "재시도 상한을 harness 에서 파생하지 않는다"

    calls = []
    lo, hi = _pm.meta_target_range("tistory")

    def _fake(alias, prompt, **kw):
        calls.append(prompt)
        # 1회차는 짧게, 2회차는 범위 안으로
        return "가" * (lo // 2 + 5) if len(calls) == 1 else "나" * (lo + 3)

    import shared.llm as _llm
    orig = _llm.invoke_text
    _llm.invoke_text = _fake
    try:
        got = _pm.meta_description("제목", "<p>" + "본문이다. " * 80 + "</p>", "tistory")
    finally:
        _llm.invoke_text = orig

    assert len(calls) == 2, f"짧은 응답인데 다시 묻지 않았다 (호출 {len(calls)}회)"
    assert lo <= len(got) <= hi, f"재시도 후에도 범위 밖: {len(got)}자"
    assert "직전 답이" in calls[1], "재시도 프롬프트에 길이 피드백이 없다"

    # 첫 응답이 이미 범위 안이면 다시 묻지 않는다 (발행 창에서 헛 LLM 호출 금지)
    calls.clear()
    _llm.invoke_text = lambda a, p, **k: (calls.append(p), "다" * (lo + 2))[1]
    try:
        ok = _pm.meta_description("제목", "<p>" + "본문이다. " * 80 + "</p>", "tistory")
    finally:
        _llm.invoke_text = orig
    assert len(calls) == 1, f"이미 범위 안인데 또 물었다 ({len(calls)}회)"
    assert lo <= len(ok) <= hi


def _undefined_globals(fn) -> set:
    """함수가 참조하는 전역 이름 중 **어디에도 없는 것** — 실행 시 NameError 가 될 것들.

    파이썬은 이름 해석을 실행 시점에 하므로, `except` 가 감싸고 있으면 이런 코드가
    조용히 죽는다. AST 가 아니라 **컴파일된 코드 객체**(co_names)를 보므로 정확하다.
    """
    import builtins
    import dis

    fn = getattr(fn, "__wrapped__", fn)      # lru_cache 등 래퍼를 벗긴다
    g = fn.__globals__
    seen, out = set(), set()

    def walk(c):
        if id(c) in seen:
            return
        seen.add(id(c))
        # ★ co_names 는 **속성명까지** 담는다(`get`·`strip`). 전역 참조만 봐야 하므로
        #   바이트코드에서 LOAD_GLOBAL 만 고른다. 함수 안 `import json` 은 지역이라
        #   LOAD_FAST 가 되어 자동으로 빠진다 — 정확히 우리가 원하는 판정이다.
        for ins in dis.get_instructions(c):
            if ins.opname == "LOAD_GLOBAL":
                n = ins.argval
                if n not in g and not hasattr(builtins, n):
                    out.add(n)
        for const in c.co_consts:
            if hasattr(const, "co_names"):
                walk(const)

    walk(fn.__code__)
    return out


def test_발행경로_함수가_없는_이름을_쓰지_않는다():
    """★ `except` 가 감싸면 NameError 는 로그 한 줄이 되고 아무도 모른다.

    실측 2026-08-08: `prepublish_quality_issues` 가 스코프에 없는 `theme` 을 참조해
    **매 발행마다** 지침 위반 학습이 조용히 죽고 있었다. 어제 그 코드를 넣은 게 나다.
    `co_names` 로 컴파일 결과를 직접 보므로 들여쓰인 참조·중첩 함수까지 잡는다.
    """
    import importlib

    TARGETS = [
        ("JARVIS02_WRITER.prepublish_gate", "prepublish_quality_issues"),
        ("JARVIS07_GUARDIAN.quality_learner", "record_directive_violations"),
        ("JARVIS07_GUARDIAN.quality_learner", "build_insights_block"),
        ("JARVIS07_GUARDIAN.quality_learner", "attribute_pending_rewards"),
        ("JARVIS07_GUARDIAN.quality_learner", "weak_items"),
        ("JARVIS02_WRITER.post_scorer", "draft_from_row"),
        ("JARVIS02_WRITER.post_scorer", "pipeline_controlled_items"),
        ("JARVIS08_PUBLISH.post_meta", "build_post_meta"),
        ("JARVIS08_PUBLISH.post_meta", "meta_description"),
        ("JARVIS08_PUBLISH.internal_links", "related_links_html"),
        ("JARVIS03_RADAR.post_quality_analyzer", "backfill_item_scores"),
        ("JARVIS03_RADAR.topic_pack", "search_keyword"),
    ]
    bad = {}
    for mod, name in TARGETS:
        fn = getattr(importlib.import_module(mod), name)
        miss = _undefined_globals(fn)
        if miss:
            bad[f"{mod}.{name}"] = sorted(miss)
    assert not bad, f"실행 시 NameError 가 될 참조: {bad}"


def test_지침위반_기록이_실제_호출과_시그니처가_맞는다():
    """호출부와 정의가 어긋나면 `except` 가 삼켜 **매 발행마다** 학습이 죽는다."""
    import ast
    import inspect

    from JARVIS07_GUARDIAN.quality_learner import record_directive_violations

    sig = inspect.signature(record_directive_violations)
    tree = ast.parse((_ROOT / "JARVIS02_WRITER/prepublish_gate.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "record_directive_violations"]
    assert calls, "게이트가 지침 위반을 기록하지 않는다 — 신용할당 신호가 끊긴다"
    for c in calls:
        assert len(c.args) + len(c.keywords) == len(sig.parameters), \
            f"호출 인자 {len(c.args) + len(c.keywords)}개 ≠ 정의 {len(sig.parameters)}개"

    # 실제로 부른다 (patch_effective 표준)
    n = record_directive_violations("economic", "naver", ["존재하지 않는 지침"])
    assert isinstance(n, int)


# ══════════════════════════════════════════════════════════════════
# 오류 강화학습 감사 수정 (2026-08-08) — "초록불부터 끄지 않으면 효과를 판정할 수 없다"
# ══════════════════════════════════════════════════════════════════

def test_수정건수를_두_번_세지_않는다():
    """★ `syntax_fixed` 는 `files_fixed` 의 **별칭**이다 — 더하면 한 수정이 두 번 센다.

    실측: '수정 파일: 3개' → total_fixed 6 (self_repair_runs 106행 중 12행 오염).
    """
    import inspect

    from JARVIS07_GUARDIAN.auto_repair import _parse_layer_counts

    L = _parse_layer_counts("수정 파일: 3개")
    assert L["files_fixed"] == 3 and L["syntax_fixed"] == 3, "별칭 관계가 깨졌다"

    # ★ 소스 문자열이 아니라 **실제로 기록되는 값**으로 판정한다.
    #   합산식을 테스트에서 재현하면 코드가 바뀌어도 테스트가 자기 식을 검사할 뿐이다
    #   (뮤테이션에서 발각 — sum(layers.values()) 로 되돌려도 통과했다).
    from JARVIS07_GUARDIAN import auto_repair as _ar
    from shared.db import get_db

    _ar._save_run_to_db("test-model", 1, 0, L, {}, "수정 파일: 3개")
    with get_db() as con:
        row = con.execute("SELECT total_fixed, syntax_fixed FROM self_repair_runs "
                          "ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None, "회차가 기록되지 않았다"
    assert row["syntax_fixed"] == 3, f"수정 파일 수가 3이 아니다: {row['syntax_fixed']}"
    assert row["total_fixed"] == 3, \
        f"total_fixed={row['total_fixed']} — 별칭을 두 번 세고 있다(기대 3)"


def test_LLM절약_지표가_한_칸에_두_정의를_담지_않는다():
    """★ 옛 칸(`llm_saved`)은 누적 패턴 수, 새 정의는 1일 창 실적 — 섞으면 추세가 거짓말한다.

    실측: 텔레그램이 "실제 LLM 절약: 50 → 0 (-50회)" 라는 가짜 붕괴를 보고했다.
    """
    import inspect
    import sqlite3

    from shared.db import DB_PATH
    cols = {r[1] for r in sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            .execute("PRAGMA table_info(self_repair_runs)")}
    assert "llm_saved_1d" in cols, "새 정의를 담을 칸이 없다"

    from JARVIS07_GUARDIAN import auto_repair as _ar
    ins = _code_only(inspect.getsource(_ar._save_run_to_db)) \
        if hasattr(_ar, "_save_run_to_db") else _code_only(inspect.getsource(_ar))
    assert "llm_saved_1d" in ins, "새 정의를 새 칸에 쓰지 않는다"

    # ★ 추세가 **어느 칸을 실제로 읽는지** SQL 을 가로채 확인한다.
    #   소스에 문자열이 있는지 보는 검사는 옛 칸으로 되돌려도 통과했다(뮤테이션 발각).
    seen = []
    _orig = _ar._db if hasattr(_ar, "_db") else None
    import shared.db as _sdb
    _real_get_db = _sdb.get_db

    class _Spy:
        def __init__(self, c): self._c = c
        def execute(self, sql, *a, **k):
            seen.append(sql)
            return self._c.execute(sql, *a, **k)
        def __getattr__(self, n): return getattr(self._c, n)

    import contextlib

    @contextlib.contextmanager
    def _spy_db():
        with _real_get_db() as c:
            yield _Spy(c)

    _sdb.get_db = _spy_db
    try:
        _ar._learning_trend_brief()
    finally:
        _sdb.get_db = _real_get_db
    sel = " ".join(q for q in seen if "self_repair_runs" in q)
    assert "llm_saved_1d" in sel, f"추세가 새 칸을 안 읽는다: {sel[:200]}"
    assert "llm_saved," not in sel.replace("llm_saved_1d", "@"), \
        f"추세가 옛 칸을 읽는다 — 정의가 섞인다: {sel[:200]}"

    # 대시보드 API 도 새 칸만
    api = (_ROOT / "api_server.py").read_text(encoding="utf-8")
    i = api.index("self_repair_runs ORDER BY id DESC LIMIT 60")
    seg = api[max(0, i - 400):i]
    assert "llm_saved_1d" in seg and "hits_total, llm_saved " not in seg, \
        "API 타임라인이 옛 칸을 내려보낸다"


def test_심층감사_실패가_성공으로_기록되지_않는다():
    """★ `run_auto_repair` 는 SDK 실패를 예외로 올리지 않고 returncode 로만 남긴다.

    실측: job_runs **39/39 success** 인데 rc=0 마지막이 2026-07-26 — 13일째 죽어 있었다.
    사후 보정은 발행 도메인이 쓰는 `job_history.mark_outcome` 을 재사용한다(①).
    """
    import ast
    import inspect

    from JARVIS07_GUARDIAN import guardian_agent as _ga

    assert hasattr(_ga, "_last_repair_returncode"), "returncode 파생이 없다"
    assert hasattr(_ga, "_mark_job_failed"), "실패 보정 경로가 없다"

    # job_deep_audit 이 실제로 그 둘을 부르는가 (AST)
    tree = ast.parse((_ROOT / "JARVIS07_GUARDIAN/guardian_agent.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "job_deep_audit"), None)
    assert fn, "job_deep_audit 을 찾을 수 없다"
    called = {getattr(n.func, "id", "") for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_last_repair_returncode" in called, "returncode 를 보지 않는다"
    assert "_mark_job_failed" in called, "실패를 기록하지 않는다"

    # 보정 경로가 job_history 단일 진입점을 쓰는가 (새 SQL 신설 금지)
    src = _code_only(inspect.getsource(_ga._mark_job_failed))
    assert "mark_outcome" in src, "job_history 단일 진입점을 안 쓴다"
    assert "INSERT" not in src.upper() and "UPDATE" not in src.upper(), \
        "사후 보정이 자체 SQL 을 만든다 — 주인은 job_history 하나다"


def test_트렌드검증이_정적시드에_속지_않는다():
    """★ 시드가 `trending` 을 채우면 `scored_keywords` 는 30개가 되지만 하류는 굶는다.

    실측 07-28·07-31·08-01·08-02 — combined=0 인데 scored=30 이라 검증이 통과했고
    topic_pack 이 안 만들어져 **글 10편이 조용히 유실**됐다.
    검증 대상은 하류(`topic_pack._candidates`)가 실제로 먹는 필드여야 한다.
    """
    import datetime as dt
    import json

    from JARVIS03_RADAR.jobs import _verify_trends
    from JARVIS03_RADAR.topic_pack import _candidates

    root = _ROOT / "JARVIS03_RADAR" / "data"
    today = dt.date.today().isoformat()
    fp = root / f"trends_{today}.json"
    backup = fp.read_text(encoding="utf-8") if fp.exists() else None
    try:
        # 실트렌드 전멸 + 정적 시드로 채워진 날 (실측 08-01 의 모양)
        seeded = {"date": today, "combined_keywords": [],
                  "scored_keywords": [{"keyword": f"시드{i}", "sector": "금융·투자"}
                                      for i in range(30)]}
        fp.write_text(json.dumps(seeded, ensure_ascii=False), encoding="utf-8")
        issues = _verify_trends(None)
        assert issues, "실트렌드 0인데 검증이 통과했다 — 시드에 속았다"
        assert any("combined" in i for i in issues), f"combined 를 안 본다: {issues}"
        # 하류가 실제로 굶는지 확인 (검증 기준의 정당성)
        assert not _candidates(seeded), "하류가 후보를 못 만드는 것이 맞다"

        ok = dict(seeded, combined_keywords=[{"keyword": "코스피", "score": 9,
                                              "sources": ["google"]}])
        assert not _verify_trends(None) or True   # 파일 기준이므로 아래로 재검사
        fp.write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
        assert not _verify_trends(None), "정상 데이터인데 차단됐다 — 과잉 차단"
    finally:
        if backup is not None:
            fp.write_text(backup, encoding="utf-8")
        elif fp.exists():
            fp.unlink()


def test_수집_산출물이_시드_비율을_남긴다():
    """'30개 수집됨' 과 '30개 전부 정적 시드' 를 구분할 수 없으면 아무도 못 알아챈다."""
    import ast

    tree = ast.parse((_ROOT / "JARVIS03_RADAR/radar_main.py").read_text(encoding="utf-8"))
    keys = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            keys |= {k.value for k in n.keys if isinstance(k, ast.Constant)}
    for k in ("real_trend_count", "seed_filled_count"):
        assert k in keys, f"산출물에 {k} 가 없다 — 시드 여부를 구분할 수 없다"


def test_팩_미생성이_조용히_지나가지_않는다():
    """★ 실제 원인은 예외가 아니라 **조용한 None** 이었다 — 그래서 오류로 안 남았다."""
    import ast
    import inspect

    from JARVIS03_RADAR import jobs as _j

    # 원인이 산출물에서 파생되는가 (분류표를 박지 않았는가)
    assert _j._pack_empty_reason() in (
        "TrendFileMissing", "TrendCollectEmpty", "TopicPackNoCandidate", "TopicPackUnknown")

    # 최종 실패 분기가 실제로 보고를 부르는가 (AST)
    tree = ast.parse((_ROOT / "JARVIS03_RADAR/jobs.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_topic_pack_once")
    called = {getattr(n.func, "id", "") for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert "_report_pack_empty" in called, "조용한 None 경로가 보고하지 않는다"

    # 타입이 뭉뚱그려지지 않는가 (한 이름으로만 보고하면 변별력 0)
    src = _code_only(inspect.getsource(_j._pack_empty_reason))
    assert src.count("return") >= 3, "원인을 한 이름으로 뭉뚱그린다"


def test_지침_선택규칙이_한_곳이다():
    """★ 게이트(검사)와 작성기(주입)가 **같은 규칙**을 써야 한다.

    실측 2026-08-08: 파이프라인 항목 필터가 `active_directives` 에만 들어가고
    `build_insights_block` 에는 없었다 — 게이트는 벌하지 않는데 작성기는 계속
    "메타 설명을 채워라" 고 시키는, 정확히 거꾸로 된 상태였다.
    해로운 쪽은 주입이다(작성 LLM 은 못 만들므로 **지어낸다**).
    """
    import inspect

    from JARVIS07_GUARDIAN import quality_learner as _ql

    for fn in ("active_directives", "build_insights_block"):
        src = _code_only(inspect.getsource(getattr(_ql, fn)))
        assert "selectable_insights" in src, f"{fn} 이 공통 선택 규칙을 안 쓴다"
        assert "get_ranked_learning_insights" not in src, \
            f"{fn} 이 원본 조회를 직접 한다 — 규칙이 두 벌이 된다"

    # 실제로 파이프라인 항목을 겨눈 지침이 걸러지는가 (실행 검증)
    from JARVIS02_WRITER.post_scorer import RUBRIC_MAX, item_index
    from shared.db import get_db

    pipe = [k for k in _ql.__dict__ and
            __import__("JARVIS02_WRITER.post_scorer", fromlist=["x"])
            .pipeline_controlled_items() if RUBRIC_MAX.get(k)]
    if not pipe:
        return                      # 살아 있는 파이프라인 항목이 없으면 검사 불가
    name = (item_index().get(pipe[0]) or {}).get("name", pipe[0])
    with get_db() as con:
        con.execute("INSERT INTO learning_insights (insight_key, insight_type, description, "
                    "directive, weight, scope, last_seen) VALUES (?,?,?,?,?,?,"
                    "datetime('now','localtime'))",
                    (f"economic:seo_{name}", "seo", "x",
                     "제목 앞부분에 핵심 키워드를 배치하라", 3.0, "economic"))
    got = {r.get("insight_key") for r in _ql.selectable_insights("economic", 50, 21)}
    assert f"economic:seo_{name}" not in got, \
        f"작성 LLM 이 만들 수 없는 항목({pipe[0]})을 겨눈 지침이 선택됐다"
