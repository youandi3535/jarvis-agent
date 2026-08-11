"""정적 검사·테스트가 *수집 전용 의존성* 없이도 도는가 — 'CI 만 빨간' 병의 재발 방지.

★ 왜 필요한가 (사용자 박제 2026-08-10)
  precommit `image/self-check` 가 CI 에서 `No module named 'feedparser'` 로 죽어
  **"검증기가 조작 수치를 통과시킨다"** 로 오판돼 머지가 막혔다. 로컬은 `.venv` 에
  전부 깔려 있어 초록이었다. 원인은 `JARVIS09_COLLECTOR/__init__.py` 가 파사드 36개를
  import 시점에 전부 끌어와, *데이터 모델 한 줄* 을 쓰려 해도 수집 스택이 통째로
  로드된 것이다.

  이 저장소는 같은 병을 **의존성을 하나씩 더 깔며** 넘겨 왔다 — `requirements-test.txt`
  주석이 그 이력이다("pandas 만 넣고 재검하니 다음 줄의 pytrends 에서 또 멈췄다").
  깔아서 넘기면 다음 소스를 추가할 때 또 터진다. 여기서는 *무게 자체* 를 검사한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 수집을 *실제로 할 때만* 필요해야 하는 라이브러리 — 목록의 주인은 여기가 아니라
# 각 provider 다. 여기 적은 것은 '가벼워야 할 import 에서 나오면 안 되는 것' 의 표본이다.
_HEAVY = ("feedparser", "pandas", "pytrends", "yfinance", "pykrx", "FinanceDataReader")

# 정적 검사·테스트가 실제로 하는 import (가벼워야 한다)
_LIGHT_IMPORTS = (
    "from JARVIS09_COLLECTOR.models import grounds, policy_for, trust_rank",
    "from JARVIS09_COLLECTOR import evidence_pack",
    "from JARVIS09_COLLECTOR.source_registry import SOURCES",
    "from JARVIS06_IMAGE.validators.image_data_verifier import verifier_effective",
)


@pytest.mark.parametrize("stmt", _LIGHT_IMPORTS)
def test_가벼운_import_가_수집_의존성을_끌어오지_않는다(stmt: str) -> None:
    """import 후 `sys.modules` 에 수집 전용 라이브러리가 올라오면 실패.

    ★ 설치 여부로 판정하지 않는다 — 로컬엔 다 깔려 있어 '없어서 통과' 가 되면
      검사가 무의미해진다. **로드됐는지** 를 본다(설치돼 있어도 잡힌다).
    """
    code = (
        f"import sys\n{stmt}\n"
        f"loaded = [m for m in {_HEAVY!r} if m in sys.modules]\n"
        "print('|'.join(loaded))\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"import 자체가 실패했다:\n{r.stderr[-1500:]}"
    loaded = [x for x in r.stdout.strip().split("|") if x]
    assert not loaded, (
        f"`{stmt}` 가 수집 전용 의존성 {loaded} 을 끌어왔다.\n"
        "  → JARVIS09_COLLECTOR 파사드는 지연 로드(PEP 562 __getattr__)여야 한다.\n"
        "  → 의존성을 CI 에 추가해 넘기지 말 것. 다음 provider 에서 또 터진다."
    )


def test_검증기_스모크가_수집_의존성_없이_동작한다() -> None:
    """`verifier_effective()` 는 정적 검사(precommit)가 부르는 함수다.

    수집 라이브러리가 없는 환경(=CI 의 precommit 잡)에서 False 를 돌려주면
    `image/self-check` 가 '검증기 무력' 으로 오판해 **머지가 영구히 막힌다**.
    """
    code = (
        "import sys\n"
        "from JARVIS06_IMAGE.validators.image_data_verifier import verifier_effective\n"
        "print('OK' if verifier_effective() else 'FALSE')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"스모크 실행 실패:\n{r.stderr[-1500:]}"
    assert r.stdout.strip() == "OK", (
        "verifier_effective() 가 False — 검증기가 조작 수치를 통과시키거나, "
        "실행 환경에서 예외가 났다(=CI 에서 머지 차단)."
    )


def test_파사드_이름표와_해석표가_어긋나지_않는다() -> None:
    """`__all__`(precommit 계약)과 `_LAZY`(실제 해석표)의 드리프트 차단.

    두 벌인 이유는 precommit `collect` 가 `__all__` 을 *소스에서 정규식으로* 읽고
    못 읽으면 검사 무력으로 보기 때문이다. 두 벌이면 반드시 갈리므로 기계가 본다.
    """
    import JARVIS09_COLLECTOR as J
    assert set(J.__all__) == set(J._LAZY), (
        f"__all__ 에만: {sorted(set(J.__all__) - set(J._LAZY))} / "
        f"_LAZY 에만: {sorted(set(J._LAZY) - set(J.__all__))}"
    )
