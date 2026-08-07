"""중복 발행 최종 방어선 — 골든 테스트 (2026-08-07).

★ 왜 `test_publish_golden.py` 가 아니라 별도 파일인가
  같은 저장소를 다른 세션이 동시에 작업 중이고 그 파일을 수정하고 있었다.
  한 파일에 양쪽이 붙으면 충돌하거나, 더 나쁘게는 한쪽 변경이 조용히 덮인다.
  주제(중복 발행 방지)가 독립적이므로 파일을 나눈다 — pytest 는 그대로 수집한다.

★ 기계 독립 (ERRORS [568] 교훈)
  이 파일은 **로컬 DB 를 들여다보지 않는다**. 어휘는 `publish_slots()`(DEFAULT_JOBS 파생)
  에서, 동작은 `published_in_slot` 을 대역으로 갈아끼워 검사한다. CI 에는 발행 이력이
  없으므로, DB 를 읽는 테스트는 "내 맥북에서만 통과" 가 된다.
"""
from __future__ import annotations

import ast
import datetime as dt
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEND_FILES = (
    "JARVIS02_WRITER/economic_poster.py",
    "JARVIS02_WRITER/trend_theme_writer.py",
)


# ══════════════════════════════════════════════════════════════════
# 1) 배선 — 4조합 전부에 가드가 붙어 있는가 (원칙③)
# ══════════════════════════════════════════════════════════════════
def test_가드가_두_발행자_모두에_배선됐다():
    """경제·테마 두 발행자 = 네이버·티스토리 각 2 → 4조합.

    한쪽만 고치면 다른 쪽에서 재발한다는 것이 이 저장소가 반복해서 겪은 사고다
    (CLAUDE.md 원칙③). 실제로 2026-07-20 중복 3건은 **테마에서만** 났다.
    """
    for rel in SEND_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "already_published_this_slot" in src, (
            f"{rel} 에 중복 발행 가드가 없다 — 이 경로로 중복이 새어나간다")


def test_가드는_발행_시도_전에_걸린다():
    """가드가 *발행 호출보다 앞* 에 있어야 의미가 있다.

    뒤에 있으면 이미 글이 올라간 뒤라 막을 게 없다. 소스 순서로 확인한다.
    """
    for rel in SEND_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        guard = src.index("already_published_this_slot")
        # 시도 플래그를 세우는 지점(= 실제 발행 직전)보다 앞이어야 한다
        set_flag = src.index("state[attempted_key] = True")
        assert guard < set_flag, (
            f"{rel}: 가드가 발행 시도 뒤에 있다 (guard={guard} > flag={set_flag})")


def test_가드_고장이_발행을_막지_않는다():
    """fail-open — 가드 자체가 터져도 발행은 계속된다.

    중복 1건은 지우면 되지만 미발행은 그 회차가 영영 없다. 판정 불가일 때
    어느 쪽으로 틀릴지는 [553](정상 글을 막은 게이트)에서 이미 값을 치렀다.
    """
    for rel in SEND_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        i = src.index("already_published_this_slot")
        # 호출 앞뒤 좁은 구간에 try/except 가 감싸고 있어야 한다
        seg = src[max(0, i - 400): i + 500]
        assert "try:" in seg and "except Exception" in seg, (
            f"{rel}: 중복 가드가 try/except 로 감싸이지 않았다 — 가드 고장이 발행을 막는다")


# ══════════════════════════════════════════════════════════════════
# 2) 어휘 — 가드가 쓰는 post_type 이 스케줄의 글종류와 같은가
# ══════════════════════════════════════════════════════════════════
def _guard_post_types() -> set[str]:
    """각 발행자의 `_pt = ...` 에서 문자열 리터럴만 AST 로 뽑는다.

    소스 텍스트 정규식은 `.get("post_type")` 같은 *다른* 문자열에 속는다(실측).
    """
    out: set[str] = set()
    for rel in SEND_FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "_pt" for t in node.targets):
                continue
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    # dict 키('post_type')는 값이 아니다 — 글종류 어휘만 남긴다
                    if sub.value != "post_type":
                        out.add(sub.value)
    return out


def test_가드_어휘가_스케줄_글종류와_일치한다():
    """드리프트 방지 — 여기가 어긋나면 가드가 **조용히** 무력해진다.

    기대집합은 `publish_slots()`(DEFAULT_JOBS 파생)에서 얻는다. 목록을 여기 박으면
    글종류가 늘 때 이 테스트가 낡아서 못 잡는다(원칙②).
    """
    from JARVIS08_PUBLISH.publish_ledger import publish_slots

    known = {pt for pt, _h, _m in publish_slots()}
    guard = _guard_post_types()
    assert guard, "가드에서 글종류 리터럴을 찾지 못했다 — 테스트를 갱신할 것"
    assert guard <= known, (
        f"가드가 스케줄에 없는 글종류를 쓴다: {guard - known} (스케줄={known}). "
        f"이 값으로는 DB 행을 영영 못 찾아 가드가 항상 통과한다")


# ══════════════════════════════════════════════════════════════════
# 3) 슬롯 창 — 경계와 최악 오탐
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("hhmm,post_type,inside", [
    ("07:00", "economic", True),    # ★ 정각 포함 — 발행 잡이 정각 기동한다
    ("06:59", "economic", False),
    ("20:59", "economic", True),
    ("21:00", "economic", False),
    ("21:00", "theme",    True),
    ("20:59", "theme",    False),
    ("02:00", "theme",    True),    # 자정 넘긴 재시도
    ("06:59", "theme",    True),
    ("07:00", "theme",    False),
])
def test_owning_slot_경계(hhmm, post_type, inside):
    """슬롯 창 경계. 정각이 빠지면 **가드가 가장 필요한 순간에** 꺼진다."""
    from JARVIS08_PUBLISH.publish_ledger import owning_slot

    h, m = (int(x) for x in hhmm.split(":"))
    now = dt.datetime(2026, 8, 7, h, m)
    assert (owning_slot(post_type, now) is not None) is inside, (
        f"{hhmm} {post_type}: 창 판정이 기대({inside})와 다르다")


def test_어제_글이_오늘_발행을_막지_않는다(monkeypatch):
    """★ 최악의 오탐 — 이게 나면 그 글종류가 **영구히** 발행되지 않는다.

    `current_slot()` 은 *시각* 기준이라 06:30 에 물으면 직전 테마 창을 답한다.
    그걸 그대로 쓰면 06:30 에 기동한 경제 잡이 *어제 경제 글* 에 걸려 막힌다
    (실측: 스케줄이 07:00 이 되기 전 경제 잡은 06:30 에 돌았다).
    """
    import JARVIS08_PUBLISH.publish_ledger as L

    # 어제도 오늘도 모든 창에 글이 있다고 가정 — 가장 가혹한 조건
    monkeypatch.setattr(L, "published_in_slot", lambda s, e, pt="": {"naver", "tistory"})

    early = dt.datetime(2026, 8, 7, 6, 30)          # 경제 슬롯(07:00) 전
    assert L.owning_slot("economic", early) is None, "06:30 은 경제 창 밖이어야 한다"
    for pf in ("naver", "tistory"):
        assert L.already_published_this_slot("economic", pf, early) is False, (
            "창 밖인데 억제됐다 — 어제 글로 오늘 발행을 막는다(최악 오탐)")


def test_같은_창의_글은_억제된다(monkeypatch):
    """양성 — 창 안에 이미 그 플랫폼 글이 있으면 막는다."""
    import JARVIS08_PUBLISH.publish_ledger as L

    monkeypatch.setattr(L, "published_in_slot", lambda s, e, pt="": {"naver"})
    inside = dt.datetime(2026, 8, 7, 9, 0)          # 경제 창 안
    assert L.already_published_this_slot("economic", "naver", inside) is True
    # 아직 안 나간 플랫폼은 통과해야 한다 (한쪽 성공이 다른 쪽을 막으면 안 된다)
    assert L.already_published_this_slot("economic", "tistory", inside) is False


def test_글종류가_다르면_서로_막지_않는다(monkeypatch):
    """테마 창 안에 경제 글이 떨어져도 테마 발행이 막히면 안 된다."""
    import JARVIS08_PUBLISH.publish_ledger as L

    seen = {}

    def _fake(s, e, pt=""):
        seen["pt"] = pt
        return {"naver"} if pt == "economic" else set()

    monkeypatch.setattr(L, "published_in_slot", _fake)
    night = dt.datetime(2026, 8, 7, 22, 0)          # 테마 창 안
    assert L.already_published_this_slot("theme", "naver", night) is False
    assert seen["pt"] == "theme", "글종류 필터가 전달되지 않았다 — 남의 글에 막힌다"
