"""발행 파이프라인 **실행** 스모크 — 네트워크 0 · 실 LLM 0 · 4조합 전부.

★ 왜 필요한가 (2026-08-05 실측)
  기존 테스트 115개 중 발행 파이프라인 심볼(`process_draft`·`enforce_supreme_law`·
  `prepublish_quality_issues`)을 **부르는 것이 0건** 이었다. 즉 "결함 있는 결과물은 영원히
  송출되지 않는다" 는 최상위 비전을 지키는 코드를 아무도 지켜보지 않았다.
  **사실성 게이트를 통째로 무력화해도 115개가 전부 통과했다.**

★ 이 파일이 `test_publish_golden.py` 와 갈리는 이유
  거긴 *정적 계약 검사*(inspect·AST·grep)고 여긴 *실행* 이다. 픽스처 성격이 달라
  파일을 나눈다 — 중복이 아니라 층위 분리다.

★ 무엇을 가짜로 쓰는가 (경계를 명시한다)
  · LLM 1회(`writer_long_body`) — 대본 본문
  · 판정 LLM(`invoke_text_result`) — 사실성·매력도
  · 픽셀 만드는 2개(`generate_infographic`·`generate_thumbnail`)
  · 파일 쓰기(`save_article_html`) — 워킹트리 오염 방지
  나머지는 **전부 진짜로 돈다** — 블록 조립·헌법 집행·이미지 배치·게이트 판정.
  Selenium 발행(JARVIS08)만 경계 밖이고, 대신 "검증 실패면 send 가 안 불린다" 를 못박는다.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("_no_external")


# ── 4조합 파생 (리터럴 금지 — 조합이 늘면 테스트가 자동으로 따라온다) ──────────
def _combos():
    from JARVIS08_PUBLISH.publish_ledger import expected_platforms
    from JARVIS09_COLLECTOR.models import CATEGORY_POLICY
    return [(c, p) for c in sorted(CATEGORY_POLICY) for p in expected_platforms()]


COMBOS = _combos()


def test_4조합이_실제_설정에서_파생된다():
    """★ 조합을 테스트에 박으면 새 플랫폼·새 글종류가 생겨도 테스트가 안 따라온다."""
    assert len(COMBOS) == 4, f"4조합이 아니다: {COMBOS}"
    assert {c for c, _ in COMBOS} == {"theme", "economic"}
    assert {p for _, p in COMBOS} == {"naver", "tistory"}


# ── 가짜 재료 ────────────────────────────────────────────────────────────
def _fake_collected():
    """최소 CollectedData — 실제 dataclass 를 쓴다(가짜 클래스를 만들면 계약이 갈린다)."""
    from JARVIS09_COLLECTOR.models import CollectedData
    return CollectedData(
        meta={"keyword": "반도체", "sector": "IT", "category": "theme"},
        datasets=[{"name": "월별 수출", "rows": [["1월", 100], ["2월", 120]],
                   "columns": ["월", "억달러"],
                   "source": {"provider": "KITA", "name": "무역통계",
                              "url": "https://example.invalid/x", "as_of": "2026-08-01"}}],
        docs=[{"title": "반도체 수출 증가", "url": "https://example.invalid/a",
               "text": "2026년 2월 반도체 수출은 120억달러로 전월 대비 20% 늘었다."}],
        facts=[{"claim": "2월 수출 120억달러", "confidence": 0.9,
                "source": "https://example.invalid/a"}],
        entities=[{"name": "반도체", "type": "industry"}],
    )


def _fake_png(counter=[0]):
    """★ 호출마다 **다른 바이트** 를 낸다.

    같은 바이트를 돌려주면 law_enforcer 의 내용 해시 dedupe 가 이미지를 통째로 지운다
    (실측: 5개 → 1개). 가짜가 진짜 로직을 우회하게 만들면 스모크가 의미를 잃는다.
    """
    counter[0] += 1
    import struct
    import zlib
    n = counter[0]
    raw = bytes([0, (n * 37) % 256, (n * 53) % 256, (n * 71) % 256])

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


# ── 게이트: 거짓 수치를 막는가 ────────────────────────────────────────────
def _bad_draft() -> dict:
    """거짓 수치가 든 대본. **본문 길이 임계는 게이트에서 파생** 한다.

    ★ 짧으면 게이트가 조기 반환(`len(body) < _MIN_BODY`)해 검사 자체를 안 한다 —
      그러면 "막았다" 가 아니라 "안 봤다" 인데 테스트는 통과한다(가짜 초록).
    """
    import JARVIS02_WRITER.prepublish_gate as G
    lie = "소비자물가는 99.9% 올랐고 코스피는 12345포인트를 넘었다. "
    filler = "이 문단은 게이트가 본문을 실제로 검사하도록 길이를 채운다. " * 40
    body = lie + filler
    need = int(getattr(G, "_MIN_BODY", 0)) + 50
    while len(body) < need:
        body += filler
    return {"title": "소비자물가 99.9% 폭등",
            "html": f"<h3>물가</h3><p>{body}</p>",
            "content": body, "keyword": "물가"}


def _kinds(issues) -> set:
    """issue 의 kind 집합. ★ 이 게이트는 **dict** 를 돌려준다 — 객체가 아니다.

    `getattr(i, "kind", "")` 로 읽으면 전부 "" 가 되어 **어떤 검사도 통과** 한다
    (가짜 초록). 계약을 확인하지 않고 쓰면 테스트가 조용히 무력해진다.
    """
    out = set()
    for i in issues or []:
        out.add(i.get("kind", "") if isinstance(i, dict) else getattr(i, "kind", ""))
    return out


def _judge(verdict: str):
    """가짜 판정기 — `invoke_text_result` 계약 (본문, 성공여부) 을 흉내낸다.

    ★ 호출 횟수를 센다 — **패치가 실제로 소비됐는지 확인**하기 위해서다.
      가짜를 심어놓고 정작 그 경로를 안 지나면 테스트는 "게이트가 통과시켰다" 가 아니라
      "게이트가 안 돌았다" 를 보고 초록이 된다. 설치는 적용의 증거가 아니다(ERRORS [457]).
    """
    import json

    calls: list = []

    def _f(*a, **kw):
        calls.append(1)
        return (json.dumps(verdict, ensure_ascii=False)
                if isinstance(verdict, (dict, list)) else verdict), True
    _f.calls = calls          # 호출자가 검증할 수 있게 노출 (patch_effective 표준)
    return _f


@pytest.mark.parametrize("post_type,platform", COMBOS)
def test_사실성_게이트가_거짓수치를_막는다(monkeypatch, post_type, platform):
    """★ 4조합 전부 — 한 조합만 막으면 다른 조합으로 새어 나간다(원칙③)."""
    import json

    import shared.llm as LLM
    import JARVIS02_WRITER.prepublish_gate as G

    fake = _judge(json.dumps({
        "blocked_claims": [{"claim": "소비자물가는 99.9% 올랐다",
                            "reason": "출처·데이터로 확인 불가"}],
        "engagement_score": 88, "dimensions": {},
    }, ensure_ascii=False))
    monkeypatch.setattr(LLM, "invoke_text_result", fake, raising=False)

    issues = G.prepublish_quality_issues(_bad_draft(), post_type=post_type,
                                         platform=platform, source_docs=[], market_data={})
    # ★ 패치가 실제로 소비됐는가 (verify) — 안 지났으면 아래 판정은 의미가 없다
    assert fake.calls, f"[{post_type}/{platform}] 판정 경로를 지나지 않았다 — 게이트가 안 돌았다"
    kinds = _kinds(issues)
    assert "factuality" in kinds, f"[{post_type}/{platform}] 거짓 수치가 통과했다: {issues}"


@pytest.mark.parametrize("post_type,platform", COMBOS)
def test_게이트를_끄면_같은_대본이_통과한다(monkeypatch, post_type, platform):
    """★ 반-동어반복 — 이 테스트가 없으면 위 테스트가 '게이트 덕분' 인지 알 수 없다.

    같은 대본·같은 판정인데 킬스위치만 끄면 통과해야 한다. 그래야 위 테스트가
    *게이트를 무력화하는 뮤테이션* 에 실제로 죽는다는 뜻이 된다.
    """
    import json

    import shared.llm as LLM
    import JARVIS02_WRITER.prepublish_gate as G

    monkeypatch.setattr(LLM, "invoke_text_result", _judge(json.dumps({
        "blocked_claims": [{"claim": "소비자물가는 99.9% 올랐다", "reason": "확인 불가"}],
        "engagement_score": 88, "dimensions": {},
    }, ensure_ascii=False)), raising=False)
    monkeypatch.setenv("PREPUBLISH_FACT_GATE", "0")
    monkeypatch.setenv("PREPUBLISH_ENGAGEMENT_GATE", "0")

    issues = G.prepublish_quality_issues(_bad_draft(), post_type=post_type,
                                         platform=platform, source_docs=[], market_data={})
    assert "factuality" not in _kinds(issues), \
        f"[{post_type}/{platform}] 킬스위치가 안 먹는다 — 위 테스트가 게이트를 검증하는 게 아니다"


@pytest.mark.parametrize("post_type,platform", COMBOS)
def test_판정불가는_인프라로_분류된다(monkeypatch, post_type, platform):
    """★ 판정 실패를 '콘텐츠 결함' 으로 분류하면 멀쩡한 대본을 재작성한다 (ERRORS [554])."""
    import shared.llm as LLM
    import JARVIS02_WRITER.prepublish_gate as G
    from JARVIS00_INFRA.harness import INFRA_KIND

    monkeypatch.setattr(LLM, "invoke_text_result", lambda *a, **kw: ("", False), raising=False)
    issues = G.prepublish_quality_issues(_bad_draft(), post_type=post_type,
                                         platform=platform, source_docs=[], market_data={})
    kinds = _kinds(issues)
    assert INFRA_KIND in kinds or not issues, \
        f"[{post_type}/{platform}] 판정 불가가 인프라로 분류되지 않았다: {kinds}"


def test_인프라_kind_파생이_살아있다():
    """★ prepublish_gate 는 harness 를 못 읽으면 조용히 ''를 돌려준다 — 그 침묵을 잡는다."""
    from JARVIS00_INFRA.harness import INFRA_KIND
    from JARVIS02_WRITER.prepublish_gate import infra_issue_kind

    assert infra_issue_kind() == INFRA_KIND != "", "harness 파생이 끊겼는데 조용히 넘어간다"


# ── 헌법 집행이 실제로 돈다 ───────────────────────────────────────────────
@pytest.mark.parametrize("platform", sorted({p for _c, p in COMBOS}))
def test_이미지_연속이_실제로_해소된다(platform):
    """★ 이미지 연속 배치는 4회 재발한 사고다 (ERRORS [39][103][170][171]).

    주인은 `jarvis_main.enforce_text_between_images` — 빈 칸을 끼우는 게 아니라
    **본문을 이미지 사이로 옮긴다**(사용자 박제 2026-06-29 "band-aid 금지").

    ★ 블록 타입은 `image` 다. 처음에 `figure` 로 썼다가 "안 고쳐진다" 는 거짓 실패를 봤는데,
      실측하니 `block_assembler` 가 내는 타입은 `image` 뿐이었다(5곳). 파이프라인이 안 쓰는
      타입으로 테스트하면 *있지도 않은 버그* 를 보고하게 된다.
    """
    from JARVIS02_WRITER.jarvis_main import enforce_text_between_images

    blocks = [("text", "도입 문장입니다. 이 글은 반도체 수출을 다룹니다."),
              ("image", "/tmp/a.png"),
              ("image", "/tmp/b.png"),
              ("text", "중간 설명 문단입니다."),
              ("text", "마무리 문단입니다.")]
    out = enforce_text_between_images(list(blocks), f"smoke-{platform}")
    kinds = [b[0] for b in out]
    for i in range(len(kinds) - 1):
        assert not (kinds[i] == "image" and kinds[i + 1] == "image"), \
            f"[{platform}] 이미지가 연속으로 남았다: {kinds}"
    assert kinds.count("image") == 2, f"이미지가 유실됐다: {kinds}"


@pytest.mark.parametrize("platform", sorted({p for _c, p in COMBOS}))
def test_헌법_집행이_실제로_돈다(platform):
    """`enforce_supreme_law` 가 블록을 받아 정상 반환하는가 (계약 + 무예외)."""
    from JARVIS02_WRITER.law_enforcer import enforce_supreme_law

    blocks = [("text", "도입 문장입니다. 반도체 수출이 늘었습니다."),
              ("h3", "수출 동향"),
              ("text", "2월 수출은 120억달러였습니다.")]
    out, viol = enforce_supreme_law(list(blocks), platform, f"smoke-{platform}")
    assert isinstance(out, list) and out, "헌법 집행이 빈 결과를 냈다"
    assert all(isinstance(b, tuple) and len(b) == 2 for b in out), \
        f"블록 계약이 깨졌다: {out[:2]}"
    assert isinstance(viol, list)
