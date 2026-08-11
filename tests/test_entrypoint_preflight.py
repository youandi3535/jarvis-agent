"""진입점 Layer 0 — `ensure_preflight()` 가 **실제로 도는가** (2026-08-10 회귀 방지).

사고: 코드에 `ensure_preflight()` 가 쓰여 있는데 **한 번도 안 도는** 진입점이 16곳 중 8곳이었다.
  · 하위 폴더 스크립트를 `python <파일>` 로 직접 실행하면 sys.path[0] 이 그 폴더라
    `from JARVIS00_INFRA...` 가 ModuleNotFoundError 로 죽는다.
  · 그것을 감싼 `except Exception` 이 경고 한 줄만 찍고 삼켰다.
  · 경고는 stdout 으로만 나가는데 데몬 stdout 은 /dev/null 이라 **어디에도 안 남았다.**
사용자가 `--manual` 을 직접 돌려 `⚠️ preflight 호출 실패` 를 눈으로 보고서야 드러났다.

★ 이 파일은 '코드가 어떻게 생겼나' 가 아니라 **실행해서** 검증한다 —
  바로 그 구분을 못 해서 8곳이 조용히 죽어 있었다.
"""
import ast
import io
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

from conftest import is_scannable_source  # noqa: E402  (제외 규칙 단일 소유자)


def _entrypoints():
    """`ensure_preflight` 를 부르는 **하위 폴더** 진입점 — 목록을 박지 않고 실물에서 파생(②)."""
    out = []
    for p in ROOT.rglob("*.py"):
        # 제외 판정은 conftest 단독 (원칙①) — 중첩 워크트리·가상환경을 *성질* 로 뺀다
        if not is_scannable_source(p, ROOT):
            continue
        rel = p.relative_to(ROOT)
        if len(rel.parts) < 2 or rel.parts[0] == "tests":
            continue
        try:
            t = io.open(p, encoding="utf-8").read()
        except Exception:
            continue
        if "ensure_preflight" in t and "def ensure_preflight" not in t:
            out.append(rel.as_posix())
    return sorted(out)


ENTRYPOINTS = _entrypoints()


def test_진입점이_하나라도_잡힌다():
    """파생이 0건이면 아래 테스트들이 전부 '검사하지 않고 통과' 가 된다."""
    assert len(ENTRYPOINTS) >= 8, f"진입점 파생 실패: {ENTRYPOINTS}"


@pytest.mark.parametrize("rel", ENTRYPOINTS)
def test_직접_실행해도_preflight_를_import_할_수_있다(rel):
    """★ 정적 검사가 아니라 **자식 프로세스에서 실제로** import 해 본다.

    sys.path[0] 을 그 파일의 폴더로 만들어(=`python <파일>` 과 같은 조건) 부트스트랩이
    루트를 올리는지 본다. 올리지 못하면 운영에서 preflight 가 조용히 건너뛰어진다.
    """
    target = ROOT / rel
    code = (
        "import sys, runpy;"
        f"sys.path.insert(0, {str(target.parent)!r});"          # 직접 실행과 동일 조건
        f"runpy.run_path({str(target)!r}, run_name='__probe__');"
        "from JARVIS00_INFRA.preflight import ensure_preflight;"
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(target.parent), timeout=180)
    assert "OK" in r.stdout, (
        f"{rel} 를 직접 실행하면 preflight 를 import 할 수 없다 — "
        f"상단 sys.path 부트스트랩을 확인하라.\n{r.stderr[-400:]}")


@pytest.mark.parametrize("rel", ENTRYPOINTS)
def test_preflight_호출이_삼켜지지_않는다(rel):
    """try/except 로 감싸면 실패가 침묵이 된다 — 그게 8곳을 조용히 죽인 방식이다."""
    tree = ast.parse(io.open(ROOT / rel, encoding="utf-8").read())
    src = io.open(ROOT / rel, encoding="utf-8").read()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        seg = ast.get_source_segment(src, node) or ""
        if "ensure_preflight" not in seg:
            continue
        # 예외를 다시 올리면 삼킴이 아니다
        swallows = any(not any(isinstance(st, ast.Raise) for st in h.body)
                       for h in node.handlers)
        assert not swallows, (
            f"{rel}:{node.lineno} preflight 호출이 try/except 로 삼켜진다 — "
            f"실패해도 진행하므로 '안전장치가 있다' 는 착각만 남는다")


def test_실제_진입점이_preflight_를_돌린다():
    """★ E2E — 사용자가 실제로 치는 명령으로 Layer 0 가 도는지 확인한다.

    `--check` 는 쿠키 유효성만 보고 브라우저를 열지 않아 부작용이 없다.
    사고 당시 이 자리에 `⚠️ preflight 호출 실패: No module named 'JARVIS00_INFRA'` 가 찍혔다.
    """
    r = subprocess.run(
        [sys.executable, "JARVIS08_PUBLISH/credentials/naver_cookie_refresher.py", "--check"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    _out = r.stdout + r.stderr
    assert "preflight 호출 실패" not in _out, "preflight 가 여전히 건너뛰어진다"
    # ★ stdout 한정 금지 (2026-08-10, CI 적색) — 이 테스트가 묻는 것은 "preflight 가 **돌았는가**"
    #   이지 "통과했는가" 가 아니다. 런타임 의존성이 없는 환경(CI 의 최소 설치)에서는 Layer 0 이
    #   *정당하게 실패* 하고, 그 보고는 stderr 로 나간다("🚨 LAYER 0 PREFLIGHT — 부팅 차단").
    #   stdout 만 보면 그 환경에서 영구 적색이 되고, 그러면 진짜 회귀와 구별할 수 없다.
    #   대소문자도 보지 않는다 — 성공 경로는 소문자('✅ Layer 0 preflight 통과'), 실패 경로는 대문자다.
    assert "preflight" in _out.lower(), f"preflight 흔적이 없다: {_out[:300]}"
