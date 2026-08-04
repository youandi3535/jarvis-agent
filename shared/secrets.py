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
import logging as _logging
import os
import re
from pathlib import Path

__all__ = ["mask", "mask_obj", "secret_values", "reload_secrets", "selfcheck",
           "install_log_masking", "masking_effective", "masking_filter_attached",
           "secret_files", "is_secret_file", "backfill_db", "redact_logs"]

# 키 *이름* 이 비밀을 뜻하는 패턴. 값 목록이 아니라 **이름 규칙** 이라 새 비밀에도 자동 적용된다.
_SECRET_KEY_RE = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|_PW$|^PW_|COOKIE|API_KEY|_KEY$)", re.I)

# 너무 짧은 값은 가리지 않는다 — 흔한 단어와 충돌해 로그를 통째로 뭉개 버린다.
_MIN_SECRET_LEN = 12

_cache: list[tuple[str, str]] | None = None   # [(env_key, value), ...] 값이 긴 것부터


def _label(key: str, value: str) -> str:
    """가림 표식 — 어떤 키였는지 + 값 해시 앞 6자(같은 값인지 비교용, 복원 불가)."""
    return f"<{key}:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:6]}>"


def _ensure_env() -> None:
    """`.env` 를 스스로 적재한다 — 호출자의 import 순서에 기대지 않는다.

    ★ 왜 (2026-08-04 실측)
      `.venv/bin/python -c "from shared.secrets import redact_logs"` 로 부르면
      `secret_values()` 가 **0개** 를 돌려줬다. 가릴 값이 0이면 `mask()` 는
      *아무 것도 안 가리면서 성공* 한다 — 가장 나쁜 실패 형태(조용한 fail-open).
      `shared/db.py` 가 `DB_PATH` 를 정하려고 이미 쓰는 규약(.env 자가 적재)을 그대로 쓴다.
      대상 키 목록은 여전히 `_SECRET_KEY_RE` 가 환경변수에서 *파생* 한다 — 목록을 박지 않는다.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def secret_values() -> list[tuple[str, str]]:
    """가려야 할 (키이름, 값) 목록 — `os.environ` 에서 파생. 프로세스당 1회 계산."""
    global _cache
    # ★ `is not None` 이 아니라 truthy 검사 (2026-08-04 감사 9위).
    #   .env 가 아직 적재되기 전에 한 번 호출되면 **빈 목록이 영구 고정** 되어
    #   그 프로세스는 이후 아무것도 가리지 못한다. 빈 결과는 캐시하지 않는다.
    if _cache:
        return _cache
    _ensure_env()
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


class _MaskingFilter(_logging.Filter):
    """로그 레코드의 메시지·인자에서 비밀값을 가린다.

    ★ 왜 루트 로거인가 (2026-08-04 감사 9위)
      DB 관문 2곳(`error_collector.report` · `db.log_event`)만 덮고 있어서
      **로그 파일에는 평문이 3,006회** 남아 있었다(daemon_stdout 2,962 · daemon 44).
      발생원은 우리 코드가 아니라 `httpx` 의 INFO 로그다 —
      `GET https://ecos.bok.or.kr/api/.../<KEY>/...` 처럼 **URL 에 키가 들어간다**.
      즉 생산자를 쫓는 방식으로는 못 막는다(외부 라이브러리다).
      → 핸들러가 아니라 **루트 로거**에 건다. 파일·stdout·앞으로 추가될 핸들러까지 한 번에.
    """

    def filter(self, record) -> bool:      # noqa: A003
        try:
            if isinstance(record.msg, str) and record.msg:
                record.msg = mask(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: mask(v) if isinstance(v, str) else v
                                   for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(mask(a) if isinstance(a, str) else a
                                        for a in record.args)
        except Exception:
            pass          # 마스킹 실패가 로그 자체를 죽이지 않는다
        return True


def install_log_masking() -> dict:
    """루트 로거에 마스킹 필터를 건다 — **데몬 부팅 1곳에서만** 호출.

    ★ 효과를 동작으로 확인한다(설치 플래그는 적용의 증거가 아니다).
      추가로 키를 URL 에 싣는 HTTP 클라이언트 로거의 레벨을 낮춘다 —
      가려도 남을 이유가 없는 잡음이고, 실측 3,006회 중 2,657회가 여기서 나왔다.
    """
    root = _logging.getLogger()
    already = any(isinstance(f, _MaskingFilter) for f in root.filters)
    if not already:
        root.addFilter(_MaskingFilter())
    quieted = []
    for name in ("httpx", "httpcore", "urllib3"):
        lg = _logging.getLogger(name)
        if lg.level < _logging.WARNING:
            lg.setLevel(_logging.WARNING)
            quieted.append(name)
    return {"filter_installed": not already, "secrets": len(secret_values()),
            "quieted": quieted, "effective": masking_effective()}


def masking_filter_attached() -> bool:
    """루트 로거에 마스킹 필터가 붙어 있는지 (부착 여부만 — 효과는 별개)."""
    return any(isinstance(f, _MaskingFilter) for f in _logging.getLogger().filters)


def masking_effective() -> bool:
    """필터가 *실제로* 먹는지 가짜 레코드 한 건으로 확인 (patch_effective 표준)."""
    vals = secret_values()
    if not vals:
        return False
    _k, v = vals[0]
    rec = _logging.LogRecord("probe", _logging.INFO, __file__, 0,
                             "GET https://x/api/%s/data", (v,), None)
    for f in _logging.getLogger().filters:
        if isinstance(f, _MaskingFilter):
            f.filter(rec)
            return v not in (rec.getMessage() or "")
    return False


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


# ══════════════════════════════════════════════════════════════════
# 시크릿 *파일* — 자율 도구가 읽으면 안 되는 것들
# ══════════════════════════════════════════════════════════════════
# ★ 왜 필요한가 (2026-08-04 전수 감사 3위 — 사용자 승인)
#   `agent_tools._DENY_DIRS` 는 **디렉터리 접두어만** 비교한다. 그래서 실측상
#     `_safe_path('.env')` → 허용 · `_safe_path('JARVIS02_WRITER/naver_cookies.pkl')` → 허용
#   이었고, `read_file`·`glob_files`·`grep_code`·`web_fetch` 는 전부
#   `requires_approval=False` 다. 즉 **승인 버튼 없이 자격증명을 읽어 외부로 보낼 수 있었다.**
#   대조: `run_bash("cat .env")` 는 `requires_approval=True` 라 버튼에 막힌다 —
#   같은 행위인데 통로에 따라 게이트가 달랐다.
#
# ★ 목록을 박지 않는다 (원칙②): 경로의 *주인* 에게 물어서 만든다.
#   .env  ← 저장소 루트 규약(shared/db.py 가 쓰는 그 파일)
#   쿠키   ← `login_manager` 가 소유한 경로 상수
#   자격증명 폴더 ← `credentials/` 실물
#   새 자격증명이 그 소유자에 추가되면 여기 손대지 않아도 자동으로 막힌다.
_SECRET_FILES_CACHE: "set | None" = None


def secret_files() -> set:
    """자율 도구가 접근하면 안 되는 파일·디렉터리의 절대경로 집합 (해석된 형태)."""
    global _SECRET_FILES_CACHE
    if _SECRET_FILES_CACHE is not None:
        return _SECRET_FILES_CACHE
    root = Path(__file__).resolve().parent.parent
    out: set = set()
    # ① .env — 값의 원본
    for name in (".env",):
        f = root / name
        if f.exists():
            out.add(f.resolve())
    # ② 쿠키·자격증명 — 경로의 주인에게 묻는다
    try:
        from JARVIS08_PUBLISH.credentials import login_manager as _lm
        for attr in dir(_lm):
            if "COOKIE" in attr.upper() and "PATH" in attr.upper():
                v = getattr(_lm, attr, None)
                if isinstance(v, Path):
                    out.add(v.resolve())
    except Exception:
        # 소유자를 못 읽으면 *알려진 위치* 로 최소 방어 (fail-closed)
        legacy = root / "JARVIS02_WRITER"
        for f in legacy.glob("*_cookies.pkl"):
            out.add(f.resolve())
    # ③ 자격증명 폴더 전체
    cred = root / "JARVIS08_PUBLISH" / "credentials"
    if cred.exists():
        out.add(cred.resolve())
    _SECRET_FILES_CACHE = out
    return out


def is_secret_file(p) -> bool:
    """해석된 경로가 시크릿 파일이거나 시크릿 디렉터리 *안* 인가."""
    try:
        rp = Path(p).resolve()
    except Exception:
        return False
    for sf in secret_files():
        if rp == sf:
            return True
        try:
            rp.relative_to(sf)      # 디렉터리면 그 안까지
            return True
        except ValueError:
            continue
    return False


def redact_logs(dry_run: bool = True) -> dict:
    """이미 기록된 로그 파일의 평문 시크릿을 *제자리에서* 가린다.

    ★ 왜 필요한가 — 필터는 미래만 막는다
      루트 로거 필터를 걸어도 **이미 파일에 쓰인 평문은 그대로 남는다**. 실측
      2026-08-04: `logs/` 에 평문 API 키 3,006회(daemon_stdout 2,962 · daemon 44).
      키가 살아 있는 한 이건 지금 이 순간의 노출이다.

    ★ 왜 삭제가 아니라 치환인가
      로그는 사고 조사의 유일한 1차 자료다. 지우면 노출은 끝나지만 조사도 끝난다.
      같은 내용을 `mask()` 로 통과시키면 **역사는 남고 비밀만 사라진다.**

    ★ 대상 목록을 박지 않는다 (② 동적 설계) — **디렉터리도 박지 않는다**
      초판은 `root/"logs"` 한 곳만 훑었다. 그런데 실물 로그 디렉터리는 5개였고
      (`logs` · `JARVIS02_WRITER/logs` · `JARVIS03_RADAR/logs` · `JARVIS07_GUARDIAN/logs` …)
      **하필 평문 토큰 26회가 있는 곳이 사각지대**였다. "3,006 → 0" 이라는 보고가
      사실은 "내가 본 한 곳에서 0" 이었다 — 범위를 박으면 보고까지 거짓이 된다.
      → 이름이 `logs` 인 디렉터리를 실물로 찾아서 전부 훑는다. 새 에이전트가
        자기 로그 폴더를 만들어도 자동으로 대상이 된다.
      바이너리·회전 백업(.gz)은 텍스트가 아니므로 건너뛴다.

    Args:
        dry_run: True 면 세지만 하고 쓰지 않는다.

    Returns:
        {"files": [(경로, 치환건수)], "total": N, "written": bool}
    """
    root = Path(__file__).resolve().parent.parent
    vals = [v for _k, v in secret_values()]
    out: list[tuple[str, int]] = []
    total = 0
    if not vals:
        return {"files": [], "total": 0, "written": False}
    targets: list[Path] = []
    for d in sorted(root.rglob("logs")):
        if not d.is_dir() or ".venv" in d.parts or ".git" in d.parts or "node_modules" in d.parts:
            continue
        targets.extend(sorted(d.rglob("*")))
    for f in targets:
        if not f.is_file() or f.suffix in (".gz", ".zip", ".pkl", ".png", ".jpg"):
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hits = sum(raw.count(v) for v in vals)
        if not hits:
            continue
        total += hits
        out.append((str(f.relative_to(root)), hits))
        if not dry_run:
            try:
                f.write_text(mask(raw), encoding="utf-8")
            except Exception:
                pass
    return {"files": out, "total": total, "written": not dry_run}


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
