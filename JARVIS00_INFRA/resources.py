"""JARVIS00_INFRA/resources.py — ★ harness state 밖 *살아있는 핸들* 단일 진입점 (ERRORS [543]).

**state 에는 문자열 키만. 살아있는 객체는 여기에.**

★ 왜 필요한가 — 두 가지가 동시에 깨지고 있었다
  ① **직렬화 불가**: harness state 에 Selenium WebDriver 가 들어 있었다
     (`economic_poster._step_ts_cookie` / `trend_theme_writer._step_ts_cookie`).
     실측 — msgpack `TypeError: Type is not msgpack serializable`.
     state 를 어딘가에 저장·재개·전송하려는 *모든* 시도가 이 객체 하나에서 즉사한다.
  ② **생명주기 미아** (이게 실제 피해): state 는 액션이 끝나면 그냥 버려진다.
     `quit()` 을 불러줄 주인이 없다. 경제 브리핑은 티스토리 driver 를 만들어 state 에 넣고
     **소비처가 0**이며 `quit()` 은 *실패 분기에만* 있다 → **성공할 때마다 Chrome 프로세스가 남았다.**

★ 계약
  - `put()` 이 돌려주는 **키(문자열)만** state 에 넣는다. 핸들은 이 모듈이 들고 있다.
  - 액션이 끝나면 harness 가 `close_scope(action_name)` 을 부른다 — **호출자가 잊어도 닫힌다.**
  - 프로세스 지역이다. 발행은 subprocess 라 그 프로세스가 곧 스코프이고, 프로세스가 죽으면
    핸들도 같이 죽는다(고아 없음). *크로스 프로세스 공유 용도가 아니다.*

★ 왜 harness.py 안이 아닌가: `harness.py` 는 이미 1,157줄이고 precommit `harness/symbol-*`
  두 레그가 그 파일의 심볼 집합을 고정하고 있다. 리소스 생명주기는 *별개 관심사* 라 분리한다.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

__all__ = [
    "put", "get", "close", "close_scope", "open_keys",
    "STATE_KEY_SUFFIX", "LIVE_HANDLE_SUFFIXES", "selfcheck",
]

# state 에 넣는 키의 접미사 — 이 접미사가 붙은 값은 *키 문자열* 이어야 한다.
STATE_KEY_SUFFIX = "_key"

# ★ "살아있는 핸들" 로 간주하는 state 키 접미사 — precommit `cache`/`harness` 계열이
#   이 목록을 파싱해 쓴다. 목록의 주인은 여기 한 곳 (원칙①).
#   새 종류의 핸들(예: DB 커넥션)을 state 에 두려는 시도가 생기면 여기에 추가하지 말고
#   `put()` 을 쓰게 할 것 — 이 목록은 *금지 대상* 이지 허용 목록이 아니다.
LIVE_HANDLE_SUFFIXES = ("_driver", "_browser", "_session", "_conn", "_socket")

_LOCK = threading.RLock()
# key -> (scope, handle, closer)
_REG: dict[str, tuple[str, Any, Optional[Callable[[Any], None]]]] = {}
_SEQ = [0]


def _default_closer(handle: Any) -> None:
    """Selenium·requests·sqlite 어느 쪽이든 흔한 종료 메서드를 순서대로 시도."""
    for meth in ("quit", "close", "disconnect", "shutdown"):
        fn = getattr(handle, meth, None)
        if callable(fn):
            fn()
            return


def put(scope: str, name: str, handle: Any,
        closer: Optional[Callable[[Any], None]] = None) -> str:
    """핸들을 등록하고 **state 에 넣을 키**를 돌려준다.

    scope: 보통 harness 액션 이름 — `close_scope(scope)` 로 일괄 정리된다.
    """
    if handle is None:
        return ""
    with _LOCK:
        _SEQ[0] += 1
        key = f"{scope}::{name}::{_SEQ[0]}"
        _REG[key] = (scope, handle, closer)
    return key


def get(key: str) -> Any:
    """키로 핸들 조회. 없으면 None (닫혔거나 다른 프로세스)."""
    if not key:
        return None
    with _LOCK:
        entry = _REG.get(key)
    return entry[1] if entry else None


def close(key: str) -> bool:
    """핸들 하나 정리. 이미 없으면 False. 예외는 삼킨다(정리가 본 작업을 막지 않는다)."""
    if not key:
        return False
    with _LOCK:
        entry = _REG.pop(key, None)
    if not entry:
        return False
    _, handle, closer = entry
    try:
        (closer or _default_closer)(handle)
    except Exception:
        pass
    return True


def close_scope(scope: str) -> int:
    """스코프의 모든 핸들 정리 — harness 가 액션 종료 시 호출. 반환: 정리 개수."""
    with _LOCK:
        keys = [k for k, (s, _, _) in _REG.items() if s == scope]
    return sum(1 for k in keys if close(k))


def open_keys(scope: str = "") -> list[str]:
    """열려 있는 키 목록 (진단용). scope 지정 시 그 스코프만."""
    with _LOCK:
        return [k for k, (s, _, _) in _REG.items() if not scope or s == scope]


def selfcheck() -> bool:
    """★ 등록→조회→정리가 *실제로* 도는지 동작으로 확인.

    (저장소 표준 — 설치 플래그는 '시도' 의 기록이지 '적용' 의 증거가 아니다.)
    """
    closed = {"n": 0}

    class _Fake:
        def quit(self):
            closed["n"] += 1

    k = put("__selfcheck__", "fake", _Fake())
    ok_put = bool(k) and get(k) is not None
    ok_scope = close_scope("__selfcheck__") == 1
    ok_gone = get(k) is None and closed["n"] == 1
    return ok_put and ok_scope and ok_gone
