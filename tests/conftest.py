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


# ── 저장소 스캔 대상 판정 — **단일 소유자** (사용자 박제 2026-08-10) ──────────
#
# ★ 왜 여기인가 — 저장소를 훑는 테스트(`ROOT.rglob("*.py")`)가 8곳인데 제외 목록이 제각각
#   복사돼 있었다. 표기까지 갈렸다: `".venv" in str(p)` / `".venv" in p.parts` / `"/.venv/" in sp`.
#   같은 판단이 여덟 벌이면 한 곳만 고쳐도 나머지가 남는다(원칙①).
#
# ★ 무엇이 터졌나 — `.claude/worktrees/<id>/` 에 git worktree(= 저장소 **통째 체크아웃**)가
#   생기자 스캐너가 *옛 커밋의 모든 파일* 을 현재 소스로 착각해 테스트 2건이 깨졌다.
#   어느 목록에도 `.claude` 가 없었다. 워크트리·벤더 체크아웃은 앞으로도 생긴다.
#
# ★ ② 동적 설계 — 디렉터리 이름을 세지 않는다. **git 이 추적하는가** 로 판정한다.
#   이름 목록(`.venv`·`.claude`·…)은 새 도구가 생길 때마다 낡지만, 추적 여부는 저장소
#   자신이 답을 갖고 있다. 부수효과로 `.githooks/pre-commit` 같은 *점으로 시작하지만
#   추적되는* 소스는 그대로 스캔 대상으로 남는다 — 이름 기반 규칙이면 잘못 빠졌을 것이다.
#   구체적으로 두 가지를 *실물에서* 알아낸다:
#     · **중첩 체크아웃** — 그 디렉터리에 `.git` 이 있다(워크트리는 `.git` *파일*, 클론은 폴더).
#     · **가상환경**     — 그 디렉터리에 `pyvenv.cfg` 가 있다(이름이 `.venv` 든 `env` 든 무관).
#   이름 목록(`.venv`·`.claude`·…)은 새 도구가 생길 때마다 낡는다. 성질은 낡지 않는다.
#   부수효과 ①: `.githooks/pre-commit` 같은 *점으로 시작하지만 추적되는* 소스는 그대로 스캔된다.
#   부수효과 ②: **아직 커밋 안 된 새 파일도 스캔된다** — 갓 만든 파일이야말로 검사가 필요하다
#              (git 추적 여부로 판정하면 신규 파일이 조용히 빠져나간다).
_DIR_VERDICT: dict = {}


def _is_foreign_dir(d: "Path") -> bool:
    """이 디렉터리가 *남의 트리* 인가 — 중첩 체크아웃 또는 가상환경. 결과는 캐시."""
    key = str(d)
    if key not in _DIR_VERDICT:
        try:
            _DIR_VERDICT[key] = (d / ".git").exists() or (d / "pyvenv.cfg").exists()
        except Exception:
            _DIR_VERDICT[key] = False
    return _DIR_VERDICT[key]


def is_scannable_source(path, root=None) -> bool:
    """저장소 *자기 소스* 인가 — 스캔 대상 판정의 단일 진입점.

    제외: 중첩 git 체크아웃(워크트리·벤더 클론) / 가상환경 / 의존성·컴파일 산출물.
    포함: 추적 여부와 무관한 저장소 자신의 소스(신규 미커밋 파일 포함).
    """
    _root = (Path(root) if root else Path(__file__).resolve().parent.parent).resolve()
    p = Path(path)
    p = p if p.is_absolute() else (_root / p)
    try:
        rel = p.resolve().relative_to(_root)
    except Exception:
        return True                     # root 밖 — 판단 불가면 넓게(조용한 0건 방지)
    if {"node_modules", "__pycache__"} & set(rel.parts):
        return False
    cur = _root
    for seg in rel.parts[:-1]:          # 파일명 제외한 디렉터리 성분만 훑는다
        cur = cur / seg
        if _is_foreign_dir(cur):
            return False
    return True
