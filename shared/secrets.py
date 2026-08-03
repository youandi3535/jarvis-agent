"""시크릿 마스킹 단일 진입점 — 기록·전송 전에 비밀값을 가린다.

★ 왜 만드나 (2026-07-30 전수 감사 3위 — 사용자 승인)
  실측: 텔레그램 **봇 토큰이 DB 119행에 평문** 으로 적재돼 있었다
  (`error_log.message` · `error_log.traceback` · `events.payload`).
  저장소 전체에 마스킹 함수 정의가 **0건** 이었다.

  유출 경로는 "누가 토큰을 로그에 찍었나" 가 아니었다 —
  `shared/notify.py:98` 이 토큰을 **URL 에 넣어** 텔레그램을 부르는데,
  `requests` 예외 문자열은 실패한 URL 을 통째로 담는다:
      HTTPSConnectionPool(host='api.telegram.org', port=443):
      Max retries exceeded with url: /bot<토큰>/getUpdates ...
  그 예외가 `error_collector.report()` 로 들어가 DB 에 그대로 박혔다.
  즉 **아무도 토큰을 기록하려 하지 않았는데 기록됐다.**

★ 설계 원칙
  ① **단일 진입점** — 마스킹은 이 모듈 하나. 생산자(예외를 만드는 곳)를 하나씩 쫓지 않는다.
     이번 사고의 생산자가 `notify.py` 가 아니라 *텔레그램 폴링 예외* 였다는 사실이 그 근거다.
     생산자를 열거하는 방식은 반드시 새는 곳이 생긴다 → **관문에서 한 번** 거른다.
  ② **동적 설계 — 가릴 값의 목록을 박지 않는다.**
     `.env` 에 실린 키 중 이름이 비밀을 뜻하는 것(TOKEN·KEY·SECRET·PASSWORD·COOKIE·PW)의
     *값* 을 런타임에 모아서 가린다. `.env` 에 새 비밀이 추가되면 **자동으로** 가려진다.
     값을 코드에 적으면 그 순간 저장소가 두 번째 유출 지점이 된다.
  ③ **가려도 추적은 가능해야 한다** — 통째로 지우지 않고 `<TELEGRAM_TOKEN:a1b2c3>` 처럼
     *어떤 키* 였는지와 해시 앞 6자를 남긴다. 사고 조사 때 "같은 값인가" 는 비교할 수 있고,
     값 자체는 복원되지 않는다.

★ 성능 — `error_collector.report()` 는 오류마다 부른다. 시크릿 목록은 **프로세스당 1회** 만
  만들고 캐시한다(`.env` 는 프로세스 수명 동안 바뀌지 않는다).
"""
from __future__ import annotations

import hashlib
import os
import re

__all__ = ["mask", "mask_obj", "secret_values", "reload_secrets", "selfcheck"]

# 키 *이름* 이 비밀을 뜻하는 패턴. 값 목록이 아니라 **이름 규칙** 이라 새 비밀에도 자동 적용된다.
_SECRET_KEY_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|_PW$|^PW_|COOKIE|API_KEY|_KEY$)", re.I)

# 너무 짧은 값은 가리지 않는다 — 흔한 단어와 충돌해 로그를 통째로 뭉개 버린다.
_MIN_SECRET_LEN = 12

_cache: list[tuple[str, str]] | None = None   # [(env_key, value), ...] 값이 긴 것부터


def _label(key: str, value: str) -> str:
    """가림 표식 — 어떤 키였는지 + 값 해시 앞 6자(같은 값인지 비교용, 복원 불가)."""
    return f"<{key}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:6]}>"


def secret_values() -> list[tuple[str, str]]:
    """가려야 할 (키이름, 값) 목록 — `os.environ` 에서 파생. 프로세스당 1회 계산."""
    global _cache
    if _cache is not None:
        return _cache
    out: list[tuple[str, str]] = []
    for k, v in os.environ.items():
        if not v or len(v) < _MIN_SECRET_LEN:
            continue
        if _SECRET_KEY_RE.search(k):
            out.append((k, v))
    # 긴 값부터 치환 — 짧은 값이 긴 값의 부분문자열일 때 앞서 잘라먹는 것을 막는다.
    out.sort(key=lambda kv: -len(kv[1]))
    _cache = out
    return _cache


def reload_secrets() -> int:
    """`.env` 재적재 후 캐시를 새로 만든다. 반환: 가리는 값 개수."""
    global _cache
    _cache = None
    return len(secret_values())


def mask(text) -> str:
    """문자열에서 모든 비밀값을 표식으로 치환. 비문자열은 str() 후 처리.

    실패해도 절대 예외를 던지지 않는다 — 마스킹이 오류 기록 자체를 죽이면 안 된다.
    """
    if text is None:
        return text
    try:
        s = text if isinstance(text, str) else str(text)
        if not s:
            return s
        for k, v in secret_values():
            if v in s:
                s = s.replace(v, _label(k, v))
        return s
    except Exception:
        return text if isinstance(text, str) else str(text)


def mask_obj(obj):
    """dict/list/str 을 재귀로 훑어 마스킹. `context`·`payload` 처럼 구조가 있는 값용."""
    try:
        if isinstance(obj, str):
            return mask(obj)
        if isinstance(obj, dict):
            return {k: mask_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            t = [mask_obj(v) for v in obj]
            return tuple(t) if isinstance(obj, tuple) else t
        return obj
    except Exception:
        return obj


def selfcheck() -> dict:
    """★ 효과를 *동작* 으로 확인 (CLAUDE.md `patch_effective()` 표준).

    설치 플래그는 '시도' 의 기록이지 '적용' 의 증거가 아니다.
    실제 비밀값 하나를 가짜 문장에 넣어 통과시켜 보고, 남아 있으면 위반으로 보고한다.
    """
    vals = secret_values()
    issues: list[str] = []
    if not vals:
        issues.append("가릴 시크릿이 0개 — .env 미적재 의심")
    for k, v in vals[:5]:
        probe = f"HTTPSConnectionPool url: /bot{v}/getUpdates failed"
        if v in mask(probe):
            issues.append(f"{k}: 마스킹 미적용")
    return {"secret_count": len(vals), "keys": [k for k, _ in vals], "issues": issues}


def backfill_db(dry_run: bool = True) -> dict:
    """이미 적재된 행의 비밀값을 소급 마스킹 — **재실행 가능**.

    관문(`error_collector.report` · `db.log_event`)은 *앞으로* 들어올 것만 막는다.
    이미 박힌 것은 여기서 지운다. 관문에 구멍이 생겨 다시 쌓이면 이 명령을 또 돌리면 된다.

    대상 컬럼은 실측으로 확인된 3곳 — 자유 텍스트가 들어가는 곳이다.
    (구조화 컬럼(error_type·module 등)에는 비밀이 들어갈 자리가 없다.)
    """
    from shared.db import get_db

    targets = [("error_log", "message"), ("error_log", "traceback"), ("events", "payload")]
    found, changed = 0, 0
    detail: dict[str, int] = {}
    with get_db() as con:
        for key, val in secret_values():
            for tbl, col in targets:
                rows = con.execute(
                    f"SELECT rowid, {col} FROM {tbl} WHERE {col} LIKE ?", (f"%{val}%",)
                ).fetchall()
                if not rows:
                    continue
                found += len(rows)
                detail[f"{key}:{tbl}.{col}"] = len(rows)
                if dry_run:
                    continue
                for rid, text in rows:
                    con.execute(
                        f"UPDATE {tbl} SET {col} = ? WHERE rowid = ?", (mask(text), rid)
                    )
                    changed += 1
        if not dry_run:
            con.commit()
    return {"dry_run": dry_run, "found": found, "masked": changed, "detail": detail}


if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path as _P

    # 직접 실행 부트스트랩 — `python3 shared/secrets.py` 는 저장소 루트가 sys.path 에 없고
    # `.env` 도 적재돼 있지 않다(가릴 값이 0개가 되어 조용히 아무것도 안 하게 된다).
    _root = _P(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    try:
        from dotenv import load_dotenv as _ld
        _ld(_root / ".env")
        reload_secrets()
    except Exception as _e:
        print(f"⚠️ .env 적재 실패 — 가릴 값이 없을 수 있음: {_e}")

    if "--backfill" in sys.argv:
        dry = "--apply" not in sys.argv
        res = backfill_db(dry_run=dry)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if dry:
            print("\n※ 미리보기입니다. 실제 적용: python3 shared/secrets.py --backfill --apply")
    else:
        print(json.dumps(selfcheck(), ensure_ascii=False, indent=2))
