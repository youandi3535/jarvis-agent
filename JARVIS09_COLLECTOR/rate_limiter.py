"""JARVIS09_COLLECTOR/rate_limiter.py — 도메인별 요청 속도 제한."""
from __future__ import annotations
import time
from urllib.parse import urlparse

_last_hit: dict[str, float] = {}
_MIN_INTERVAL = 2.0  # 도메인당 최소 2초 간격


def wait_for(url: str) -> None:
    """도메인별 2초 최소 간격 보장. 필요 시 sleep."""
    try:
        domain = urlparse(url).netloc
    except Exception:
        domain = url
    now = time.time()
    last = _last_hit.get(domain, 0.0)
    gap = now - last
    if gap < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - gap)
    _last_hit[domain] = time.time()
