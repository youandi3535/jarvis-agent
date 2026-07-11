"""shared/pytrends_utils.py — pytrends 공용 유틸리티.

google_collector 와 trend_detector 모두 동일한 pytrends 세션 패치 + payload 빌드
로직이 필요해서 공유 모듈로 분리.

import:
    from shared.pytrends_utils import disable_proxy, build_payload_with_fallback
"""
from __future__ import annotations


def disable_proxy(pt) -> None:
    """pytrends 객체의 시스템 프록시를 안전하게 우회.

    pytrends 4.x 와 5.x 가 내부 session 을 다른 속성명으로 노출:
      - 4.9.x: pt.requests
      - 5.x:   세션 속성 자체 제거됨
    어느 쪽이든 *발견된 경우에만* trust_env/proxies 설정.
    """
    for attr in ("requests", "session", "_session"):
        sess = getattr(pt, attr, None)
        if sess is None:
            continue
        try:
            sess.trust_env = False
            sess.proxies = {}
            return
        except Exception:
            continue


def build_payload_with_fallback(pt, kw_list: list, timeframe: str,
                                 geo: str = "KR", cat: int = 0) -> str:
    """build_payload 호출 시 timeframe 거절 시 안전 형식으로 자동 fallback.

    시도 순서: 원본 → now 1-d → today 1-m. 모두 실패 시 마지막 예외 재발생.
    반환: 성공한 timeframe 문자열.
    """
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op

    candidates = [timeframe, "now 1-d", "today 1-m"]
    seen: set = set()
    last_err = None
    for tf in candidates:
        if tf in seen:
            continue
        seen.add(tf)
        # ★ 후보 timeframe 단위 진행 신호 — pytrends 재시도(timeout=30s×retries=3)가
        #   후보 3개 누적되면 5분 freeze 상한을 넘겨 워치독 오탐-강제킬(rc=75)될 수 있음
        _wd_beat()
        try:
            pt.build_payload(kw_list, cat=cat, timeframe=tf, geo=geo)
            return tf
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    return timeframe


__all__ = ["disable_proxy", "build_payload_with_fallback"]
