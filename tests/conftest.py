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
os.environ["JARVIS_TEST_MODE"] = "1"      # ★ setdefault 아님 — 밖에서 "0" 이 와도 켠다
# ★ 런타임 상태 파일도 임시 경로로 (2026-08-09 3차 적대적 검증)
#   `shared/pipeline_activity.py` 는 데몬·API서버(:9198)가 **동시에 읽고 쓰는** 파일을
#   `os.replace` 로 통째로 교체한다. 테스트가 그걸 건드려 라이브 대시보드에
#   가짜 발행 엣지(J06→J08)가 남았다. 경로는 모듈 로드 시점에 고정되므로
#   **여기(import 전)에서** 세워야 한다.
os.environ.setdefault("JARVIS_PIPELINE_ACTIVITY",
                      str(_TEST_DB_DIR / "pipeline_activity.json"))

import pytest  # noqa: E402


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """테스트 종료 후 임시 DB 폐기 — 흔적을 남기지 않는다."""
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def test_db_path() -> Path:
    """이 세션이 쓰는 임시 DB 경로 (테스트가 확인용으로 참조)."""
    return Path(os.environ["JARVIS_DB_PATH"])


# ── 실 LLM·실 네트워크 차단 (2026-08-05) ──────────────────────────────
#   ★ `JARVIS_TEST_MODE=1` 은 **아무도 읽지 않았다** (실측: 소비자 0곳).
#     설정하는 쪽만 있고 읽는 쪽이 없는 플래그는 "차단했다" 는 착각만 만든다.
#     그래서 실제로 막는다 — 부르면 터진다.
_BOOM_HITS: list = []


def _forbid(name):
    def _boom(*a, **kw):
        _BOOM_HITS.append(name)
        raise AssertionError(f"테스트가 실제 {name} 을 호출했다 — 가짜 주입이 빠진 경로가 있다")
    return _boom


@pytest.fixture
def _no_external(monkeypatch):
    """진짜 LLM 을 부르면 터진다. **삼켜져도** teardown 에서 반드시 드러난다.

    ★ 이 저장소는 `except Exception: log.warning` 이 도처에 있어 "부르면 터진다" 만으로는
      약하다 — 예외가 삼켜지고 테스트는 초록으로 통과한다. 그래서 호출 자체를 세고
      끝에서 0인지 확인한다.
    """
    import sys as _sys

    from pathlib import Path as _Path

    import shared.llm as _llm

    _BOOM_HITS.clear()
    for fn in ("invoke_text", "invoke_text_result"):
        orig = getattr(_llm, fn, None)
        if orig is None:
            continue
        boom = _forbid(f"shared.llm.{fn}")
        monkeypatch.setattr(_llm, fn, boom)
        # ★ 모듈 레벨로 미리 복사해 간 바인딩까지 교체 (draft_writer.py:42 — ERRORS [457])
        #   ※ **우리 저장소 모듈만** 훑는다. `sys.modules` 전체를 getattr 로 건드리면
        #     지연 로딩 라이브러리(transformers 등)의 `__getattr__` 이 발동해
        #     없는 의존(torchvision)을 import 하다 죽는다 (실측).
        _root = str(_Path(__file__).resolve().parent.parent)
        for mod in list(_sys.modules.values()):
            if mod is None or getattr(mod, "__name__", "") == "shared.llm":
                continue
            f = getattr(mod, "__file__", None)
            if not f or not str(f).startswith(_root) or "/.venv/" in str(f):
                continue
            try:
                if getattr(mod, fn, None) is orig:
                    monkeypatch.setattr(mod, fn, boom, raising=False)
            except Exception:
                continue
    yield
    assert not _BOOM_HITS, f"실 LLM 호출이 삼켜졌다: {sorted(set(_BOOM_HITS))}"


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


@pytest.fixture(autouse=True)
def _no_production_data_writes(tmp_path_factory, monkeypatch):
    """★ 운영 **데이터 파일** 도 DB 와 같은 원칙으로 막는다 (2026-08-09 적대적 검증).

    DB 는 위에서 막고 있었는데 파일은 아무도 안 막고 있었다. 실측: 트렌드 테스트가
    운영 `JARVIS03_RADAR/data/trends_<오늘>.json` 을 가짜 판(`combined_keywords: []`)으로
    덮었다가 `finally` 로 복원했다 — 바이트는 같게 돌아오지만 **그 사이 데몬이 같은
    파일을 읽는다**(발행·팩 빌드가 이 판을 먹는다). 그 폴더는 `.gitignore` 대상이라
    `git status` 로도 안 보여, 사고가 나도 흔적이 남지 않는다.

    ★ 검사가 아니라 **기본값 교체** 다 — "쓰면 실패" 로 만들면 데몬이 같은 창에
      쓸 때 헛경보가 난다. 아예 *임시 경로를 기본* 으로 주면 새로 쓰는 테스트도
      자동으로 안전하고, 운영 경로가 필요한 테스트만 명시적으로 되돌리면 된다.
      경로 상수의 주인은 각 모듈이므로 목록을 박지 않고 **모듈에서 파생**한다.
    """
    tmp = tmp_path_factory.mktemp("radar_data")
    (tmp / "data").mkdir(exist_ok=True)
    for mod_name, attr, value in (
        ("JARVIS03_RADAR.radar_main", "DATA_DIR", tmp / "data"),
        ("JARVIS03_RADAR.jobs", "_RADAR_DIR", tmp),
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
        except Exception:
            continue          # 그 모듈을 안 쓰는 테스트도 있다 — 없으면 넘어간다
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, value)
    yield
