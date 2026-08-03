"""pytest 공통 설정 — **테스트는 운영 DB 를 절대 건드리지 않는다.**

★ 왜 필요한가 (2026-08-02 전수 감사 9위 — 사용자 승인)
  `tests/test_routing.py` 가 `shared.db.get_db()` 로 **진짜 운영 DB** 에 INSERT 하고 있었다.
  실측: `~/.jarvis/jarvis.sqlite` 의 `events` 테이블에 `source='test_suite'` 행 **22건**.
  회계 연습을 진짜 장부에 한 셈이다. 지금은 22줄이라 무해하지만 테스트를 늘릴수록 커지고,
  무엇보다 **테스트를 무서워서 못 늘리게 된다** — 그게 진짜 손해다.

★ 어떻게 막는가 (② 동적 설계)
  `shared/db.py` 는 import 시점에 `JARVIS_DB_PATH` 환경변수로 경로를 정한다.
  conftest 는 pytest 가 테스트 모듈을 import 하기 *전에* 로드되므로, 여기서 환경변수를
  임시 경로로 바꿔 두면 **어떤 테스트가 무엇을 import 하든** 임시 DB 를 쓴다.
  테스트마다 monkeypatch 를 흩지 않는다 — 잊는 테스트가 반드시 생긴다(원칙①).

  ※ 경로를 코드에 박지 않는다. `shared/db.py` 가 이미 가진 규칙(환경변수 우선)을 그대로 쓴다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# ── 반드시 shared.db import 보다 먼저 실행되어야 한다 (모듈 레벨) ──────────
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="jarvis_test_db_"))
os.environ["JARVIS_DB_PATH"] = str(_TEST_DB_DIR / "test.sqlite")

# 텔레그램·외부 전송 차단 — 테스트가 실제로 메시지를 보내면 안 된다.
os.environ.setdefault("JARVIS_TEST_MODE", "1")

import pytest  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """테스트 종료 후 임시 DB 폐기 — 흔적을 남기지 않는다."""
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def test_db_path() -> Path:
    """이 세션이 쓰는 임시 DB 경로 (테스트가 확인용으로 참조)."""
    return Path(os.environ["JARVIS_DB_PATH"])


@pytest.fixture(autouse=True)
def _assert_not_production_db():
    """★ 모든 테스트에 자동 적용 — 운영 DB 를 잡았으면 즉시 실패시킨다.

    설정이 '있다' 는 것과 '먹었다' 는 것은 다르다(CLAUDE.md `patch_effective` 표준).
    import 순서가 어긋나 운영 경로를 잡는 순간, 조용히 지나가지 않고 여기서 터진다.
    """
    from shared.db import DB_PATH

    prod = Path.home() / ".jarvis" / "jarvis.sqlite"
    assert Path(DB_PATH).resolve() != prod.resolve(), (
        f"테스트가 운영 DB 를 잡았다: {DB_PATH}\n"
        f"conftest 가 shared.db 보다 늦게 로드됐을 가능성 — import 순서를 확인하라."
    )
    yield
