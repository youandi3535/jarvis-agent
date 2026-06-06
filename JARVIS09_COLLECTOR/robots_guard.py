"""JARVIS09_COLLECTOR/robots_guard.py — robots.txt 준수 체크."""
from __future__ import annotations
import time
import urllib.robotparser
from urllib.parse import urlparse

_cache: dict[str, tuple[urllib.robotparser.RobotFileParser, float]] = {}
_CACHE_TTL = 3600  # 1시간 캐시
_UA = "JarvisCollector/1.0"


def can_crawl(url: str) -> bool:
    """url 크롤링 허용 여부. robots.txt Disallow 시 False."""
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        now = time.time()
        if base in _cache:
            rp, ts = _cache[base]
            if now - ts < _CACHE_TTL:
                return rp.can_fetch(_UA, url)
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        rp.read()
        _cache[base] = (rp, now)
        return rp.can_fetch(_UA, url)
    except Exception:
        return True  # 파싱 실패 시 허용으로 간주 (보수적 기본값)
