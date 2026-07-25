"""JARVIS07_GUARDIAN/json_store.py — ★ 학습 원장 영속화 단일 진입점 (사용자 박제 2026-07-25).

★ 왜 신설했나 (ERRORS [497] — 2026-07-25 감사)
  `pattern_fixer._save_learned` 와 `bandit._save` 가 **각자 `Path.write_text()`** 로 저장했다.
  이 호출은 *truncate-in-place* 다 — 파일을 0바이트로 자른 뒤 처음부터 다시 쓴다.
  그 사이(실측 7.7ms) 다른 프로세스가 읽으면 **잘린 JSON** 을 본다.

  두 파일의 로더는 파싱 실패를 **빈 구조로 삼켰다**(`except: return {...빈...}`).
  그래서 다음 저장이 그 빈 구조를 진실로 믿고 덮어쓴다:
      learned_patterns.json : 48패턴(409KB) → 1패턴(7.8KB)
      bandit_state.json     : 8 arm / obs 21,451 / feature_version 3
                            → 1 arm / obs 1 / feature_version 1 (28D→14D 퇴행)
  로그는 WARNING 한 줄. **조용한 전멸** — CLAUDE.md 가 가장 경계하는 형태다.

  ★ 왜 *지금* 위험해졌나 — 커밋 `c9c7c2b` 로 **테마도 subprocess** 가 되면서
    학습 원장에 쓰는 *교차 프로세스* writer 가 1개(경제) → 2개(경제+테마)가 됐다.
    종전의 `threading.Lock` 은 **같은 프로세스만** 방어한다(ERRORS [474] 와 동일한 병).

  ★ 정황 증거: `learned_patterns.json.bak`(443,445B) > 현재 원본(409,417B).
    이미 한 번 줄었을 가능성이 있다.

설계 (①단일 진입점 — 저장 로직은 여기 한 곳)
  - **원자 교체**: 같은 디렉토리에 임시파일로 쓰고 `os.fsync` 후 `os.replace()`.
    POSIX 에서 `rename` 은 원자적이라 **독자는 옛 파일 아니면 새 파일만 본다.**
    잘린 중간 상태가 관측될 창이 존재하지 않는다.
  - **교차 프로세스 락**: `fcntl.flock` (별도 `.lock` 파일).
    `threading.Lock` 은 프로세스 경계를 못 넘는다 — 그게 이 사고의 원인이었다.
  - **손상 시 조용히 비우지 않는다**: 파싱 실패면 손상본을 `.corrupt-<ts>` 로 보존하고
    `.bak` 승격을 시도한다. 실패를 빈 구조로 삼키는 것이 데이터 전멸의 방아쇠였다.

킬스위치 (호출 시점 조회 — 로드 시점 캡처 금지)
  GUARDIAN_ATOMIC_STORE=0   → 종전 동작(write_text). 되돌림용.
  GUARDIAN_STORE_LOCK=0     → 파일락만 끔(원자쓰기는 유지).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("jarvis.guardian.store")

__all__ = ["read_json", "write_json", "locked", "store_effective"]


def _flag(name: str, default: bool = True) -> bool:
    """킬스위치 — **호출 시점** 조회. 모듈 로드 시 캡처하면 재시작 없이 못 끈다."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


# 같은 프로세스가 이미 잡은 경로 — **재진입 자기 데드락 방지** (아래 주석 참조)
_HELD: dict[str, int] = {}
_HELD_GUARD = __import__("threading").Lock()


@contextmanager
def locked(path: Path, timeout: float = 10.0):
    """교차 프로세스 배타 락 — `<path>.lock` 에 `fcntl.flock`.

    ★ `threading.Lock` 과 **다른 질문**이다. 혼동 금지:
        · threading.Lock = 같은 프로세스의 스레드끼리
        · flock          = **다른 프로세스끼리** (경제·테마 subprocess 가 여기 해당)

    ★ **재진입 필수** (비직관 — 안 하면 자기 자신과 데드락):
      `flock` 은 *open file description* 단위다. 같은 프로세스라도 `open()` 을 또 하면
      다른 description 이라 **두 번째 flock 이 자기 첫 번째 락에 막힌다.**
      read-modify-write 를 `locked()` 로 감싸고 그 안에서 `write_json()` 을 부르면
      (write_json 도 락을 잡으므로) 정확히 그 상황이 된다 → `_HELD` 로 깊이를 세어
      이미 보유 중이면 재획득 없이 통과시킨다.

    타임아웃이면 락 없이 진행한다(fail-open) — 학습 저장이 발행을 막으면 안 된다.
    단 그 사실을 로그로 남긴다(조용한 열화 금지).
    """
    if not _flag("GUARDIAN_STORE_LOCK"):
        yield False
        return
    try:
        import fcntl  # noqa: PLC0415 — POSIX 전용, 지연 import
    except Exception:            # pragma: no cover — 비 POSIX
        yield False
        return

    key = str(Path(path).resolve())
    with _HELD_GUARD:
        depth = _HELD.get(key, 0)
        if depth:                       # ★ 이미 이 프로세스가 보유 — 재획득하면 자기 데드락
            _HELD[key] = depth + 1
            reentrant = True
        else:
            reentrant = False
    if reentrant:
        try:
            yield True
        finally:
            with _HELD_GUARD:
                _HELD[key] = max(0, _HELD.get(key, 1) - 1)
                if not _HELD[key]:
                    _HELD.pop(key, None)
        return

    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = None
    got = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")          # noqa: SIM115 — finally 에서 닫는다
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                break
            except OSError:
                if time.time() >= deadline:
                    log.warning(
                        "[GUARDIAN/store] 파일락 타임아웃 %.0fs — 락 없이 진행: %s",
                        timeout, path.name,
                    )
                    break
                time.sleep(0.02)
        if got:
            with _HELD_GUARD:
                _HELD[key] = _HELD.get(key, 0) + 1
        yield got
    finally:
        if got:
            with _HELD_GUARD:
                _HELD[key] = max(0, _HELD.get(key, 1) - 1)
                if not _HELD[key]:
                    _HELD.pop(key, None)
        if fh is not None:
            try:
                if got:
                    import fcntl as _f
                    _f.flock(fh.fileno(), _f.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass


def read_json(path: Path, default: Any = None, *, quarantine: bool = True) -> Any:
    """JSON 로드. 손상 시 **조용히 비우지 않고** 격리 + `.bak` 승격을 시도한다.

    Returns: 파싱된 값 / 복구값 / `default`.
    """
    p = Path(path)
    if not p.exists():
        return default
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.warning("[GUARDIAN/store] 읽기 실패 %s: %s", p.name, e)
        return default

    try:
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        # ★ 여기가 데이터 전멸의 방아쇠였다 — 빈 구조를 반환하면 다음 저장이 그걸 덮어쓴다.
        log.error("[GUARDIAN/store] ⚠️ 손상 감지 %s (%dB): %s", p.name, len(raw), e)
        if quarantine:
            try:
                stamp = time.strftime("%Y%m%d-%H%M%S")
                p.replace(p.with_suffix(p.suffix + f".corrupt-{stamp}"))
                log.error("[GUARDIAN/store] 손상본 격리 → %s.corrupt-%s", p.name, stamp)
            except Exception:  # noqa: BLE001
                pass
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.exists():
            try:
                recovered = json.loads(bak.read_text(encoding="utf-8"))
                log.error("[GUARDIAN/store] 🔁 .bak 승격 복구: %s", bak.name)
                return recovered
            except Exception:  # noqa: BLE001
                log.error("[GUARDIAN/store] .bak 도 손상 — 복구 실패: %s", bak.name)
        return default


def write_json(path: Path, data: Any, *, indent: Optional[int] = 2,
               compact: bool = False, backup: bool = False) -> bool:
    """★ 원자적 JSON 저장 — 임시파일 → fsync → `os.replace`.

    독자는 *옛 파일 아니면 새 파일* 만 본다. 잘린 중간 상태가 관측될 창이 없다.
    Returns: 성공 여부.
    """
    p = Path(path)
    if not _flag("GUARDIAN_ATOMIC_STORE"):
        try:  # 종전 동작 (되돌림용)
            p.write_text(_dumps(data, indent, compact), encoding="utf-8")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("[GUARDIAN/store] 저장 실패 %s: %s", p.name, e)
            return False

    text = _dumps(data, indent, compact)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with locked(p):
            if backup and p.exists():
                try:
                    p.replace(p.with_suffix(p.suffix + ".bak"))
                except Exception:  # noqa: BLE001
                    pass
            # ★ 같은 디렉토리에 만들어야 os.replace 가 같은 파일시스템에서 원자적이다.
            fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                    fh.flush()
                    os.fsync(fh.fileno())      # 메타데이터까지 디스크로 — 전원 손실 대비
                os.replace(tmp, str(p))        # ★ 원자 교체
                tmp = None
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except Exception:  # noqa: BLE001
                        pass
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("[GUARDIAN/store] 원자 저장 실패 %s: %s", p.name, e)
        return False


def _dumps(data: Any, indent: Optional[int], compact: bool) -> str:
    if compact:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, ensure_ascii=False, indent=indent)


def store_effective() -> bool | None:
    """★ 스모크 — 원자쓰기가 *실제로 먹는지* 동작으로 확인 (설치 플래그가 아니라).

    CLAUDE.md `patch_effective()` 표준. 임시 경로에 왕복시켜 예외 유무로 판정한다.
    Returns: True(유효) / False(무력) / None(판정 불가)
    """
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "probe.json"
            payload = {"__smoke__": True, "n": 42}   # ★ 표식 — 실데이터로 오인 방지
            if not write_json(p, payload):
                return False
            return read_json(p) == payload
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":       # pragma: no cover
    print("store_effective():", store_effective())
