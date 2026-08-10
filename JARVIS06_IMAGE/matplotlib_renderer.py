"""JARVIS06_IMAGE/matplotlib_renderer.py — 한글 폰트 실적용 검증.

★ 이 모듈에는 더 이상 렌더러가 없다 (사용자 박제 2026-08-10).
  종전의 `render(spec, out_path)` + `_render_*` 20여 개는 `image_spec.render_from_spec`
  (제2 렌더 파이프라인) 의 3순위 폴백 전용이었고, 그 파이프라인이 삭제되면서
  호출자가 0곳이 됐다. 살아있는 matplotlib 차트는 `theme_charts`·`section_title`·
  `thumbnail_maker` 가 `style_engine.setup_chart_defaults()` 로 직접 그린다.
  같은 그림을 짓는 두 벌을 남겨두면 한쪽만 고치는 사고가 난다 — 그래서 지웠다.

남은 이유는 단 하나 — `JARVIS00_INFRA/preflight.py` 의 Layer 0 폰트 점검이
`font_effective()` 를 부른다. "설정을 시도했다"가 아니라 "실제로 먹는다"를
동작으로 확인하는 스모크 테스트라, 폰트를 쓰는 쪽이 아니라 여기 한 곳에 둔다.

공개 API:
    font_effective() -> bool | None
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis")


# ── 폰트 설정 ────────────────────────────────────────────────────────
def _setup_font() -> str | None:
    """한글 폰트 설정 후 폰트 이름 반환. 실패 시 None.

    ★ 종전 결함 (2026-07-20 전수감사 확정 — ERRORS [459]):
      폰트 경로가 *다른 머신의 샌드박스 절대경로* 두 개로 박혀 있었다
      (`/sessions/dazzling-upbeat-hypatia/...`). 이 호스트엔 둘 다 없어
      `_setup_font()` 이 항상 None 을 반환했고, 호출부는 반환값을 *버렸다*.
      → 폴백 렌더 시 한글이 전부 두부(□□□)로 나오는데 예외도 로그도 없이
      "✅ 렌더링 완료" 로 발행됐다. 정확히 '복사본을 진실로 믿는' 병.

    ★ 수정: 규칙 14(JARVIS06_IMAGE/CLAUDE.md) 대로 스타일 단일 진입점
      `style_engine.setup_chart_defaults()` 에 위임한다. 자체 폰트 탐색 금지.
      단일 진입점이 실패할 때만 설치된 pykrx 패키지에서 *동적으로* 경로를
      해석한다(하드코딩 금지 — 패키지 위치는 venv 마다 다르다).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # ① 정규 경로 — 차트 스타일 단일 진입점 (규칙 14)
    try:
        from JARVIS06_IMAGE.style_engine import setup_chart_defaults
        setup_chart_defaults()
        fam = plt.rcParams.get("font.family")
        name = fam[0] if isinstance(fam, (list, tuple)) and fam else fam
        if name and str(name) != "sans-serif":
            plt.rcParams["axes.unicode_minus"] = False
            return str(name)
    except Exception as e:
        log.warning(f"[matplotlib_renderer] setup_chart_defaults 실패 → 동적 폴백: {e}")

    # ② 폴백 — 설치된 pykrx 패키지에서 번들 폰트를 *동적으로* 찾는다
    try:
        import os
        import pykrx
        from matplotlib import font_manager
        cand = os.path.join(os.path.dirname(pykrx.__file__), "NanumBarunGothic.ttf")
        if os.path.exists(cand):
            font_manager.fontManager.addfont(cand)
            plt.rcParams["font.family"] = "NanumBarunGothic"
            plt.rcParams["axes.unicode_minus"] = False
            return "NanumBarunGothic"
    except Exception:
        pass
    return None


def font_effective() -> bool | None:
    """한글 폰트가 *실제로* 적용되는지 동작으로 확인 (설정 시도가 아니라).

    선택된 폰트 파일의 charmap 에 한글 글리프(U+ACBD '경')가 있는지 본다.
    반환: True(정상) / False(한글 깨짐 — 즉시 수리) / None(판정 불가)
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager as fm
        from matplotlib.ft2font import FT2Font
    except Exception:
        return None
    try:
        _setup_font()
        fam = plt.rcParams.get("font.family")
        path = fm.findfont(fm.FontProperties(family=fam))
        return 0xACBD in FT2Font(path).get_charmap()
    except Exception:
        return None

__all__ = ["font_effective"]
