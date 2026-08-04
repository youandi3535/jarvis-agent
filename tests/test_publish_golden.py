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
