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
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("jarvis.guardian.store")

__all__ = ["read_json", "write_json", "locked", "held_exclusive", "store_effective"]


def _flag(name: str, default: bool = True) -> bool:
    """킬스위치 — **호출 시점** 조회. 모듈 로드 시 캡처하면 재시작 없이 못 끈다."""
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "off", "no")


# 이 **스레드** 가 이미 잡은 경로 — 재진입 자기 데드락 방지 (아래 주석 참조)
#   ★ 키가 경로뿐이면 안 된다 (2026-08-14 — flock 만으로는 스레드가 안 막힌다)
#     종전 키는 `경로` 였다. 그래서 스레드 A 가 보유 중일 때 **같은 프로세스의 스레드 B** 가
#     `depth>0` 을 보고 "내가 이미 갖고 있다" 고 판단해 **락 없이 그대로 통과** 했다.
#     flock 은 *프로세스* 경계를 막으라고 있는 것이고, 스레드는 애초에 같은 fd 를 공유하므로
#     flock 으로는 못 막는다 — 즉 이 파일에는 스레드 배타가 **하나도 없었다**.
#     (실측 계기: `error_fixer.apply_patchset` 에 같은 파일을 겨눈 5스레드가 동시 도달.)
#     ★ 값이 깊이 하나면 안 된다 (2026-08-14 2차) — 재진입 분기가 "이미 갖고 있다" 는 이유로
#     `yield True` 를 무조건 줬다. 바깥이 **배타를 못 얻은 채** 진행 중이어도 안쪽은
#     "얻었다" 고 답한 셈이라, 소비자(`error_fixer.apply_patchset`)의 REJ_LOCK 보류가
#     중첩 호출에서만 조용히 무력해진다. → 값에 *바깥이 실제로 얻었는가* 를 함께 담는다.
_HELD: dict[tuple[int, str], tuple[int, bool]] = {}
# 경로 → 같은 프로세스 스레드끼리의 배타. flock 과 **다른 질문** 이라 둘 다 필요하다.
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_HELD_GUARD = threading.Lock()


def _release_held(thread_id: int, key: str) -> None:
    """보유 깊이 1 감소 — 감소 로직을 한 곳에만 둔다(①). 0 이면 항목 자체를 지운다."""
    with _HELD_GUARD:
        depth, exclusive = _HELD.get((thread_id, key), (1, False))
        depth -= 1
        if depth > 0:
            _HELD[(thread_id, key)] = (depth, exclusive)
        else:
            _HELD.pop((thread_id, key), None)


def held_exclusive(path: Path) -> "bool | None":
    """이 스레드가 지금 `path` 락을 *배타로* 들고 있는가. 미보유면 None.

    보유 상태를 밖에서 조회할 유일한 창구(①) — 테스트·자기점검이 내부 dict 를
    직접 들여다보지 않게 한다.
    """
    with _HELD_GUARD:
        got = _HELD.get((threading.get_ident(), str(Path(path).resolve())))
    return None if got is None else bool(got[1])


@contextmanager
def locked(path: Path, timeout: float = 10.0):
    """배타 락 — **스레드락 ∧ 파일락(flock)** 을 함께 잡는다.

    ★ 둘은 **다른 질문**이다. 하나만으로는 배타가 아니다:
        · threading.Lock = 같은 프로세스의 스레드끼리
        · flock          = **다른 프로세스끼리** (경제·테마 subprocess 가 여기 해당)
      2026-08-14 까지 이 함수는 flock 만 잡았고, 스레드는 재진입 판정에 걸려 *통과* 했다.
      그래서 `yield` 값은 이제 "둘 다 얻었는가" 다 — 소비자(예: `error_fixer.apply_patchset`)
      가 그 값으로 *진행할지 멈출지* 를 정한다.

    ★ **재진입 필수** (비직관 — 안 하면 자기 자신과 데드락):
      `flock` 은 *open file description* 단위다. 같은 프로세스라도 `open()` 을 또 하면
      다른 description 이라 **두 번째 flock 이 자기 첫 번째 락에 막힌다.**
      read-modify-write 를 `locked()` 로 감싸고 그 안에서 `write_json()` 을 부르면
      (write_json 도 락을 잡으므로) 정확히 그 상황이 된다 → `_HELD` 로 깊이를 세어
      이미 보유 중이면 재획득 없이 통과시킨다.

    타임아웃이면 락 없이 진행한다(fail-open) — 학습 저장이 발행을 막으면 안 된다.
    단 그 사실을 로그로 남긴다(조용한 열화 금지).

    ★★ `timeout` 은 **두 대기의 합** 이다 (2026-08-14 2차 — 실측 timeout=1.0 인데 2.02초)
      종전엔 스레드락에 `timeout` 만큼 기다려 실패한 *뒤에* flock 을 또 `timeout` 만큼
      기다렸다. 즉 호출자가 건 상한이 조용히 두 배가 됐다. 상한을 시간 예산에서 파생하는
      소비자(`error_fixer._patch_lock_timeout`)에게는 그 두 배가 곧 임계경로 지연이다.
      → 진입 시 **절대 데드라인 하나** 를 만들고 두 대기가 그것을 나눠 쓴다.

    ★★ `<path>.lock` 파일은 **일부러 남긴다. 지우지 말 것** (2026-08-09 박제)
      0바이트이고 `.gitignore` 대상이라 비용이 없다. 반면 지우면 *진짜 경합* 이 생긴다:
        ① A 가 foo.lock 에 flock 보유 → ② B 가 foo.lock 을 unlink →
        ③ C 가 foo.lock 을 새로 만들어 flock 획득 → **A 와 C 가 동시에 락을 가졌다고 믿는다**
      flock 은 *inode* 에 걸리는데 unlink 는 이름만 떼어내기 때문이다.
      "쓰고 남은 찌꺼기" 처럼 보여 청소하고 싶어지는 자리라 여기 박아둔다.
      실측(2026-08-09): 저장소에 10개가 남아 있고 전부 0바이트·git 무시 대상이며,
      `shared/file_cleanup.py` 의 규칙(`trends_*.json` 등)은 `.lock` 을 잡지 않는다.
      **정리 규칙에 `*.lock` 을 추가하지 말 것.**
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
    me = threading.get_ident()
    deadline = time.time() + max(0.0, float(timeout))   # ★ 두 대기가 **나눠 쓰는** 하나의 상한
    with _HELD_GUARD:
        depth, outer_exclusive = _HELD.get((me, key), (0, False))
        if depth:                       # ★ 이미 **이 스레드** 가 보유 — 재획득하면 자기 데드락
            _HELD[(me, key)] = (depth + 1, outer_exclusive)
            reentrant = True
        else:
            reentrant = False
            tlock = _THREAD_LOCKS.setdefault(key, threading.Lock())
    if reentrant:
        try:
            # ★ 바깥이 배타를 못 얻은 채 진행 중이면 안쪽도 배타가 아니다. 여기서 True 를
            #   주면 소비자의 '보류' 판단이 중첩 호출에서만 조용히 뒤집힌다.
            yield outer_exclusive
        finally:
            _release_held(me, key)
        return

    # ① 같은 프로세스의 다른 스레드 배제 — flock 은 이걸 못 한다(같은 fd 를 공유하므로).
    t_got = tlock.acquire(timeout=max(0.0, deadline - time.time()))
    if not t_got:
        log.warning("[GUARDIAN/store] 스레드락 타임아웃 %.0fs — 락 없이 진행: %s",
                    timeout, path.name)
    # ★ 보유 등록은 flock 성공 여부와 무관하게 **지금** 한다. 안 하면 같은 스레드의
    #   중첩 호출이 재진입으로 인식되지 않아 `tlock`(비재귀) 에 자기가 자기를 막는다.
    #   (배타 여부는 flock 판정 후 아래에서 확정한다 — 지금은 스레드락 결과만 담는다.)
    with _HELD_GUARD:
        _d, _ = _HELD.get((me, key), (0, False))
        _HELD[(me, key)] = (_d + 1, t_got)

    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = None
    got = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")          # noqa: SIM115 — finally 에서 닫는다
        # ★ 새 데드라인을 만들지 않는다 — 위에서 만든 것을 그대로 쓴다(누적 대기 금지).
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
        # ② 배타를 **실제로** 얻었는가 = 스레드락 ∧ 파일락. 소비자가 이 값으로 판단한다.
        exclusive = bool(got and t_got)
        with _HELD_GUARD:                   # 중첩 호출이 같은 답을 하도록 확정값을 박아둔다
            _d, _ = _HELD.get((me, key), (1, False))
            _HELD[(me, key)] = (_d, exclusive)
        yield exclusive
    finally:
        _release_held(me, key)
        if t_got:
            try:
                tlock.release()
            except Exception:  # noqa: BLE001
                pass
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
