"""shared/pytrends_utils.py — pytrends 공용 유틸리티.

google_collector 와 trend_detector 모두 동일한 pytrends 세션 패치 + payload 빌드
로직이 필요해서 공유 모듈로 분리.

import:
    from shared.pytrends_utils import disable_proxy, build_payload_with_fallback
"""
from __future__ import annotations

_RETRY_COMPAT_DONE = False


def ensure_retry_compat() -> None:
    """pytrends 4.9.2 ↔ urllib3 2.x 호환을 *런타임* 에서 흡수.

    pytrends 4.9.2 는 `Retry(method_whitelist=...)` 를 쓰는데 이 인자는
    urllib3 2.0 에서 `allowed_methods` 로 개명·제거됐다. 그대로 두면
    `TrendReq(..., retries>0)` 의 첫 요청이 TypeError 로 죽는다.

    ★ venv 안 site-packages 직접 수정 금지 (2026-07-19 폴더 이동 사고, ERRORS [454]):
      종전 규정은 `.venv/.../pytrends/request.py` 를 손으로 고치는 것이었으나,
      venv 를 재생성하면 패치가 소실되고 *예외가 조용히 삼켜져*(google_collector
      `except Exception: return []`) pytrends 경로만 죽은 채 RSS 폴백으로
      연명하는 무증상 열화가 발생한다. → 코드 레벨에서 영구 흡수한다.

    idempotent. urllib3 1.x(원래 인자 지원) 면 아무것도 하지 않는다.
    """
    global _RETRY_COMPAT_DONE
    if _RETRY_COMPAT_DONE:
        return
    try:
        import inspect
        from urllib3.util.retry import Retry   # pytrends 가 쓰는 것과 동일 클래스 객체
    except Exception:
        return
    try:
        params = inspect.signature(Retry.__init__).parameters
    except (TypeError, ValueError):
        return
    if "method_whitelist" in params or "allowed_methods" not in params:
        # urllib3 1.x — 원래 인자를 지원(또는 개명 전)하므로 패치 불필요
        _RETRY_COMPAT_DONE = True
        return
    _orig_init = Retry.__init__

    def _compat_init(self, *args, **kwargs):
        if "method_whitelist" in kwargs:
            kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
        return _orig_init(self, *args, **kwargs)

    Retry.__init__ = _compat_init
    _RETRY_COMPAT_DONE = True


def retry_compat_effective() -> bool | None:
    """호환 패치가 *실제로 먹는지* 동작으로 확인 (플래그가 아니라).

    `_RETRY_COMPAT_DONE = True` 는 "시도했다" 는 뜻일 뿐이다. 실제로 pytrends 가
    쓰는 형태(`Retry(method_whitelist=...)`)를 한 번 만들어 봐서 예외가 없어야
    유효. (ERRORS [455][457] — 같은 병이 하루에 두 번 났다.)

    반환: True(유효) / False(무력) / None(판정 불가)
    """
    try:
        from urllib3.util.retry import Retry
    except Exception:
        return None
    try:
        Retry(total=1, method_whitelist=frozenset(["GET", "POST"]))
        return True
    except TypeError:
        return False
    except Exception:
        return None


# import 시점 자동 적용 — 호출자가 잊어도 보호된다.
# (google_collector 는 TrendReq 생성 직후 disable_proxy 를 호출하므로
#  실제 HTTP 요청이 나가기 *전* 에 이 모듈이 로드되어 패치가 선다.)
ensure_retry_compat()


def disable_proxy(pt) -> None:
    """pytrends 객체의 시스템 프록시를 안전하게 우회.

    pytrends 4.x 와 5.x 가 내부 session 을 다른 속성명으로 노출:
      - 4.9.x: pt.requests
      - 5.x:   세션 속성 자체 제거됨
    어느 쪽이든 *발견된 경우에만* trust_env/proxies 설정.

    ★ google_collector 는 TrendReq 생성 직후 이 함수를 부르므로, 여기서
      호환 패치를 한 번 더 보장한다 (모듈 재로드·부분 import 대비).
    """
    ensure_retry_compat()
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


__all__ = ["ensure_retry_compat", "disable_proxy", "build_payload_with_fallback"]
