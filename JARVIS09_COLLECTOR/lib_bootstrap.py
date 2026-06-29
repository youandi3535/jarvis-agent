"""JARVIS09_COLLECTOR/lib_bootstrap.py — 데이터 수집 라이브러리 자동 부트스트랩.

★ 사용자 박제 2026-06-29 — "이미지 생성을 위해 데이터 요청이 들어오면, 그 데이터를 받기
  위해 새로 설치해야 한다면 *갯수 제한 없이* 자동 설치해야 한다. 무료면 승인 없이."

  헌법 예외조항 (ADR 010 / CLAUDE.md '데이터 라이브러리 자동설치'):
    - **갯수 제한 없음**. 필요한 무료 데이터 라이브러리는 *무제한* 자동 설치.
    - 고정 화이트리스트(캡)가 아니라 *안전 정책 게이트*로 통제:
        ① 데니리스트(`_DENYLIST`) 에 없을 것 — 명백히 위험·무관한 패키지 차단.
        ② PyPI 공식 저장소에 *실존* 할 것 — 오타·허위(typosquat 일부) 차단.
        ③ 라이선스가 *명백한 상용 전용* 이 아닐 것 — "무료" 원칙 (best-effort).
      ↑ 셋 다 통과하면 갯수 무관 설치.
    - side_effect = *internal 부트스트랩* (venv 내부 변경 — 외부 발행·과금 없음).
    - 텔레그램은 '승인 인라인 버튼'이 아니라 *설치 알림*만 송출.

  ★ run_bash(external, requires_approval) 도구 경로와 *무관*:
    ReAct 라우터/LLM 이 임의 셸을 돌리는 게 아니라, JARVIS09 수집 코드가 *안전 정책을
    통과한* 데이터 라이브러리를 직접 부트스트랩한다. 사용자 미인지 외부 행동이 아니므로
    승인 게이트의 보호 대상이 아니다.

  ★ `_KNOWN_DATA_LIBS` 는 *상한이 아니라* import명↔pip명 매핑 편의표일 뿐.
    여기 없어도 안전 정책만 통과하면 설치된다.

공개 API:
    ensure_lib(import_name, pip_name=None) -> module | None
"""
from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

log = logging.getLogger("jarvis.collector.bootstrap")

# ── import명 ↔ pip 패키지명 매핑 (편의표 — 상한 아님) ──────────────────────
# import 이름과 pip 이름이 다른 흔한 데이터 라이브러리만 등재. 없어도 설치 가능
# (그 경우 import_name 을 pip 이름으로 사용).
_KNOWN_DATA_LIBS: dict[str, str] = {
    "FinanceDataReader": "finance-datareader",
    "bs4":               "beautifulsoup4",
    "pandas_datareader": "pandas-datareader",
    "wbgapi":            "wbgapi",
    "yfinance":          "yfinance",
    "pykrx":             "pykrx",
    "pytrends":          "pytrends",
}

# ── 데니리스트 — 자동설치 *절대 금지* (안전 가드) ──────────────────────────
# 데이터 수집과 무관하거나 위험한 카테고리. 발견 시 확장.
_DENYLIST: frozenset[str] = frozenset({
    "os", "sys", "subprocess", "pip", "setuptools", "wheel",  # 시스템/메타
    "requests-malicious", "colourama", "python-sqlite",       # 알려진 typosquat 예시
})

# macOS Homebrew PATH — launchd/keeper 기동 시 PATH 최솟값 대비 *항상 prepend*
# (ERRORS [32][160][137] 박제 — 조건부 prepend 금지)
_EXTRA_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]

# 상용 전용으로 간주할 라이선스 키워드 (이것만 있으면 "무료 아님"으로 차단)
_PROPRIETARY_HINTS = ("proprietary", "commercial", "all rights reserved", "paid")

_lock = threading.Lock()
_attempted: set[str] = set()   # 같은 라이브러리 중복 설치 시도 방지


def _notify(text: str) -> None:
    """텔레그램 설치 알림 (best-effort) — 승인 아님."""
    try:
        from shared.notify import send_tg
        send_tg(text)
    except Exception:
        pass


def _pip_env() -> dict:
    env = os.environ.copy()
    env["PATH"] = ":".join(_EXTRA_PATHS) + ":" + env.get("PATH", "")
    return env


def _pypi_check(pip_name: str) -> tuple[bool, bool]:
    """PyPI 공식 저장소 조회. 반환 (실존?, 무료?).

    네트워크 실패 시 (True, True) — pip 가 알아서 실패시키도록 fail-open(실존 가정).
    명백한 상용 전용 라이선스만 무료=False 로 차단.
    """
    try:
        import requests
        r = requests.get(f"https://pypi.org/pypi/{pip_name}/json", timeout=8)
        if r.status_code == 404:
            return (False, False)   # PyPI 에 없음 — 오타/허위
        if r.status_code != 200:
            return (True, True)     # 조회 불가 — fail-open
        info = (r.json() or {}).get("info", {}) or {}
        lic = (info.get("license") or "")
        classifiers = " ".join(info.get("classifiers") or [])
        blob = f"{lic} {classifiers}".lower()
        # OSI 승인/오픈소스 분류가 있으면 무료 확정
        if "osi approved" in blob or "open source" in blob:
            return (True, True)
        # 명백한 상용 전용 표지만 차단, 그 외(메타데이터 빈약 포함)는 허용
        if any(h in blob for h in _PROPRIETARY_HINTS):
            return (True, False)
        return (True, True)
    except Exception:
        return (True, True)         # 네트워크 등 실패 — fail-open


def _pip_install(pip_name: str) -> bool:
    """현재 인터프리터의 pip 로 라이브러리 설치. 성공 시 True. (갯수 제한 없음)"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", pip_name],
            cwd=str(Path(__file__).resolve().parent.parent),
            env=_pip_env(),
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode == 0:
            return True
        log.warning(f"[bootstrap] pip install {pip_name} 실패(rc={proc.returncode}): "
                    f"{(proc.stderr or '')[:300]}")
        return False
    except Exception as e:
        log.warning(f"[bootstrap] pip install {pip_name} 예외: {e}")
        return False


def ensure_lib(import_name: str, pip_name: str | None = None):
    """데이터 라이브러리를 import. 없으면 *안전 정책 통과 시 갯수 제한 없이* 자동 설치.

    Args:
        import_name: import 할 모듈명 (예: "pykrx", "wbgapi").
        pip_name:    pip 패키지명 (import 명과 다를 때만. None 이면 매핑표/import명 사용).

    Returns:
        import 된 모듈. 데니리스트·미실존·상용전용·설치실패 시 None (graceful).
    """
    try:
        return importlib.import_module(import_name)
    except ImportError:
        pass

    pkg = pip_name or _KNOWN_DATA_LIBS.get(import_name, import_name)

    # ① 데니리스트 — 하드 차단
    if pkg.lower() in _DENYLIST or import_name.lower() in _DENYLIST:
        log.warning(f"[bootstrap] '{pkg}' 데니리스트 — 자동설치 금지")
        return None

    with _lock:
        if import_name in _attempted:
            try:
                return importlib.import_module(import_name)
            except ImportError:
                return None
        _attempted.add(import_name)

        # ② PyPI 실존 + ③ 무료 라이선스 검증
        exists, is_free = _pypi_check(pkg)
        if not exists:
            log.warning(f"[bootstrap] '{pkg}' PyPI 미실존 — 오타/허위 차단")
            _notify(f"⚠️ [JARVIS09] 라이브러리 자동설치 거부: `{pkg}` (PyPI 미실존 — 오타 의심)")
            return None
        if not is_free:
            log.warning(f"[bootstrap] '{pkg}' 상용 전용 라이선스 — 자동설치 금지(무료만)")
            _notify(f"⚠️ [JARVIS09] 라이브러리 자동설치 거부: `{pkg}` (상용 전용 — 무료만 허용)")
            return None

        log.info(f"[bootstrap] 데이터 라이브러리 자동 설치: {pkg}")
        if not _pip_install(pkg):
            _notify(f"⚠️ [JARVIS09] 라이브러리 자동설치 실패: `{pkg}` (수동 설치 필요할 수 있음)")
            return None

        importlib.invalidate_caches()
        try:
            mod = importlib.import_module(import_name)
            _notify(f"📦 [JARVIS09] 데이터 라이브러리 자동설치 완료: `{pkg}`\n"
                    f"(승인 불필요 — 무료 데이터 라이브러리, 갯수 제한 없음)")
            return mod
        except ImportError as e:
            log.warning(f"[bootstrap] {pkg} 설치 후에도 import 실패: {e}")
            return None


__all__ = ["ensure_lib"]
