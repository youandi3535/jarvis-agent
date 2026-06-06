"""JARVIS02_WRITER/collect_theme.py — backward-compat shim.

★ 단일 진입점 이관 (2026-05-31): 수집 로직 본체는 JARVIS09_COLLECTOR.collect_theme 으로 이동.
   호출자는 JARVIS09_COLLECTOR.collect_theme 직접 import 권장.
"""
import sys as _sys
from JARVIS09_COLLECTOR import collect_theme as _mod  # noqa: F401
_sys.modules[__name__] = _mod
