"""JARVIS06_IMAGE/html_renderer.py — Chromium 실행 파일 탐색 단일 진입점.

★ 이 모듈에는 더 이상 렌더러가 없다 (사용자 박제 2026-08-10).
  종전의 `render(spec, out_path)` + `_tpl_*` 템플릿 11종 + `_screenshot()` 은
  `image_spec.render_from_spec` (제2 렌더 파이프라인) 의 1순위 렌더러 전용이었고,
  그 파이프라인이 삭제되면서 호출자가 0곳이 됐다. 살아있는 HTML 인포그래픽은
  `infographic_engine` → `html_infographic._html_to_jpg` 한 경로뿐이다.
  카드 템플릿을 두 벌 남겨두면 "이미 있으니 쓰자" 로 초크포인트가 다시 우회된다.

남은 이유는 단 하나 — Chromium 실행 파일 경로를 *어디서 찾는가* 는 한 곳에만
있어야 한다. 현재 소비자 2곳이 여기서 가져간다:
    JARVIS06_IMAGE/html_infographic.py  (_html_to_jpg — 본문 인포그래픽 렌더)
    JARVIS06_IMAGE/design_learner.py    (레퍼런스 캡처)

공개 API:
    _find_chromium() -> str
"""
from __future__ import annotations

import os

# Playwright Chromium 경로 탐색 순서
_CHROMIUM_CANDIDATES = [
    # sandbox 환경 (개발)
    os.path.expanduser("~/.cache/ms-playwright/chromium-1217/chrome-linux/chrome"),
    os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1217/chrome-linux/headless_shell"),
    # Mac 프로덕션 환경
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]


def _find_chromium() -> str:
    """사용 가능한 Chromium 실행 파일 경로 반환. 없으면 RuntimeError."""
    for c in _CHROMIUM_CANDIDATES:
        if os.path.isfile(c):
            return c
    # Playwright 기본 경로 자동 탐색
    try:
        from playwright._impl._driver import compute_driver_executable
        import subprocess
        subprocess.run(
            [str(compute_driver_executable()), "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass
    raise RuntimeError(
        "Chromium 실행 파일을 찾을 수 없습니다. "
        "python -m playwright install chromium 을 실행하세요."
    )


__all__ = ["_find_chromium"]
