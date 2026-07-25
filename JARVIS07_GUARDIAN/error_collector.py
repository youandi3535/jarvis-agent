"""JARVIS07_GUARDIAN/error_collector.py — 전 에이전트 오류 수집기.

★ 단일 공개 진입점: catch(exc, source, ...)
  - 외부 에이전트는 이 함수 하나만 호출하면 됨
  - report = catch  (하위 호환 alias)
  - auto_catch 데코레이터/컨텍스트 매니저도 내부적으로 catch() 호출

내부 자동 배선 (install() 로 데몬 부팅 시 1회 설치):
  · sys.excepthook        → 메인 스레드 미처리 예외
  · threading.excepthook  → 백그라운드 스레드 미처리 예외
  · APScheduler listener  → 스케줄 잡 실패
  · log_scanner           → 모든 JARVIS*/logs/ ERROR/WARNING 줄

모든 경로의 종착점: _collect_error() → shared.db.save_error() + bus.publish(ERROR_DETECTED)
"""
from __future__ import annotations

import logging
import re
import sys
import threading
import traceback as _tb_mod
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.guardian.collector")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

# 쿨다운: 동일 오류 60초 내 재수집 방지 (메모리 캐시)
_cooldown_lock = threading.Lock()
_cooldown: dict[str, float] = {}   # key → last_seen epoch
_COOLDOWN_SECS = 60
# ★ 만료 항목 제거 — 상주 데몬에서 _cooldown 이 무한 증가하던 누수 차단 (2026-07-25)
_COOLDOWN_MAX_KEYS = 5000

# ── 킬스위치 (라이브 안전 — 코드 수정 없이 즉시 무효화) ──────────────────
_LOG_SCAN_ENABLED    = os.environ.get("GUARDIAN_LOG_SCAN", "1") != "0"
_LOG_SCAN_TAIL       = os.environ.get("GUARDIAN_LOG_SCAN_TAIL", "1") != "0"
# 인터프리터가 스스로 '무시했다' 고 선언한 예외(unraisable)까지 수집할지 — 기본 제외
_LOG_SCAN_UNRAISABLE = os.environ.get("GUARDIAN_LOG_SCAN_UNRAISABLE", "0") == "1"
# 쿨다운 키 정규화(_normalize_message 재사용) — 0 이면 종전 message[:80] 동작
_COOLDOWN_NORMALIZE  = os.environ.get("GUARDIAN_COOLDOWN_NORMALIZE", "1") != "0"

# ★★ 로그 스캐너 — 자연어 레벨 문구 스크래핑 폐기, *구조적 증거* 만 취한다 (2026-07-25)
#
# 폐기한 것: 종전 `_LOG_ERROR_PAT` 은 `[ERROR]`·`[WARNING]` 같은 *레벨 문구* 를 찾고
#   같은 줄에서 영문 예외 타입명을 요구했다. 두 가지 이유로 **70일간 매치 0건** 이었다.
#     ① 데몬 포매터가 `%(levelname)-8s` → 실제 출력은 `[ERROR   ]`(패딩) 인데 `\[ERROR\]` 요구
#     ② `etype` 그룹이 필수라 같은 줄에 영문 예외명이 없으면 전부 탈락 — 우리 로그는 한국어다
#   (그래서 `or "LogError"` 폴백은 도달 불가 코드였다.)
#
# 왜 패딩만 맞추지 않았나: 포맷을 맞추는 순간 `[WARNING ]` 4,894줄 + `[ERROR   ]` 819줄이
#   한꺼번에 깨어난다. 거기엔 *재시도로 이미 회복된 건* 이 대량 포함되어 실패로 오적재된다.
#   현업 표준도 로그 텍스트로 오류를 판별하지 않는다(SRE Workbook: 이벤트에 카운터를 올리고
#   그 값으로 알림). Sentry 가 ERROR 로그를 이벤트화하는 것도 `record.exc_info` **객체** 를
#   갖기 때문이지 텍스트 파싱이 아니다.
#
# 그래서: `Traceback (most recent call last):` **블록** 만 취한다. 이건 자연어가 아니라
#   *진짜 예외의 구조적 증거* 다 — 예외 타입·프레임이 그 안에 있고, 레벨 문구·언어와 무관하다.
#   부수효과로 tb_str 이 확보되어 기존 `_is_sandbox_traceback` 가 비로소 실제로 동작한다.
_TRACEBACK_PAT = re.compile(
    r"(?P<unraisable>^Exception ignored in:[^\n]*\n)?"
    r"^Traceback \(most recent call last\):\n"
    r"(?P<frames>(?:[ \t]+[^\n]*\n|[ \t]*\.\.\.[^\n]*\n)+)"
    r"(?P<exc>[A-Za-z_][\w\.]*(?:Error|Exception|Exit|Interrupt|Timeout|Warning)"
    r"(?::[^\n]*)?)",
    re.MULTILINE,
)
# 추가 가드: GUARDIAN 자체 수집 로그 (재귀 차단) + 오류 수집/스캔 정상 로그 줄 제외
_LOG_SKIP_PAT = re.compile(
    r"\[GUARDIAN\]\s*(?:오류 수집|로그 스캔|학습|패턴|fingerprint|hit_count)|"
    r"Job\s+\".+?\"\s*\(trigger:|"
    r"job_runs|"
    r"오류 수집\s*—\s*#\d+",
)

# ── Sandbox 경로 차단 ─────────────────────────────────────────
# Sandbox(Linux 컨테이너) 환경에서 발생한 traceback 은 호스트 데몬과 무관.
# 호스트 .venv 가 정상이어도 sandbox 가 system python3 사용해서 모듈 미인식 사고 다발.
# 이런 traceback 이 호스트 error_log 에 INSERT 되면 영구 잔존 → 사용자 혼란.
# traceback 첫 File 경로가 sandbox 마운트(/sessions/*/mnt/) 면 수집 skip.
_SANDBOX_PATH_PAT = re.compile(r'/sessions/[^/]+/mnt/')

# ── 스모크 테스트 표식 ────────────────────────────────────────
# 합성 입력을 실제 소비자 경로로 통과시키되 DB·통계에는 남기지 않기 위한 표식.
_SMOKE_MARK = "__smoke__"
_SMOKE_ID = -1          # 스모크 통과를 뜻하는 sentinel error_id (DB 미기록)


def _is_sandbox_traceback(tb_str: Optional[str]) -> bool:
    """traceback 첫 File 경로가 sandbox 마운트 경로면 True."""
    if not tb_str:
        return False
    m = re.search(r'File "([^"]+)"', tb_str)
    if not m:
        return False
    return bool(_SANDBOX_PATH_PAT.search(m.group(1)))


# ── 쿨다운 헬퍼 ─────────────────────────────────────────────────

def _cool_key(source, module, error_type, message: str) -> str:
    """쿨다운 키 — ★ 기존 `_normalize_message` 재사용 (신설 금지, ①단일 진입점).

    종전 키는 `message[:80]` 이었다. harness 가 만드는 메시지는
    `[harness:경제 브리핑 발행 — 티스토리] attempt=2 step=⑥ ...` 형태라
    **`attempt=N` 이 80자 안에 들어간다** → 재시도마다 키가 달라져 *쿨다운이 안 걸렸다*.
    같은 실패가 attempt 1·2 마다 새 행으로 적재된 이유다(DB 실측 395건).

    이미 `pattern_fixer._normalize_message()` 가 7종 placeholder 로 이걸 정확히
    처리한다 — 마지막 규칙 `\\b\\d+\\b → <N>` 이 `attempt=2` 를 `attempt=<N>` 으로 만든다.
    있는 도구를 안 쓰고 있던 것 자체가 ①위반이었으므로 *그 함수를 그대로 재사용* 한다.

    killswitch: GUARDIAN_COOLDOWN_NORMALIZE=0 → 종전 message[:80] 동작으로 즉시 복귀.
    """
    raw = message or ""
    if _COOLDOWN_NORMALIZE:
        try:
            from JARVIS07_GUARDIAN.pattern_fixer import _normalize_message
            norm = _normalize_message(raw)
        except Exception:
            norm = raw[:80]           # 정규화 불가 시 종전 동작 — 절대 막지 않는다
    else:
        norm = raw[:80]
    return f"{source}:{module}:{error_type}:{norm}"


def _in_cooldown(key: str) -> bool:
    import time
    now = time.time()
    with _cooldown_lock:
        last = _cooldown.get(key, 0)
        if now - last < _COOLDOWN_SECS:
            return True
        # ★ 만료 항목 제거 — 종전엔 삭제가 없어 상주 데몬에서 _cooldown 이 무한 증가했다.
        #   (키가 계속 새로 생기는 harness 메시지와 겹쳐 누수가 특히 빨랐다.)
        if len(_cooldown) >= _COOLDOWN_MAX_KEYS:
            cutoff = now - _COOLDOWN_SECS
            for k in [k for k, v in _cooldown.items() if v < cutoff]:
                _cooldown.pop(k, None)
        _cooldown[key] = now
        return False


# ── 핵심 수집 함수 (내부 전용) ──────────────────────────────────

def _collect_error(
    source: str,
    error_type: str,
    message: str,
    module: str = None,
    func_name: str = None,
    tb_str: str = None,
    context: str = None,
) -> Optional[int]:
    """오류를 DB에 저장하고 ERROR_DETECTED 이벤트를 publish.

    Returns:
        int | None: error_log.id (쿨다운 중이면 None, sandbox traceback skip 시 None)
    """
    # Sandbox 환경 traceback 차단 — 호스트 데몬과 무관한 사고는 적재 금지
    if _is_sandbox_traceback(tb_str):
        log.debug(f"[GUARDIAN] sandbox traceback skip — {error_type}: {(message or '')[:60]}")
        return None

    # ★ `__smoke__` 표식 — 스모크 테스트의 *합성 입력* 은 DB·통계·이벤트를 오염시키지 않는다.
    #   (shared/claude_sdk_compat.py 의 선례. 표식 없이 던지면 검사용 가짜 오류가 진짜처럼
    #    error_log 에 쌓여 관측 도구가 관측 대상을 더럽힌다.)
    #   경로는 여기까지 *전부 실제로* 통과했으므로 '살아있음' 판정 근거는 그대로다.
    if _SMOKE_MARK in f"{message or ''}{context or ''}{tb_str or ''}":
        log.debug("[GUARDIAN] __smoke__ 합성 입력 — DB/이벤트 기록 생략 (경로는 통과)")
        return _SMOKE_ID

    cool_key = _cool_key(source, module, error_type, message)
    if _in_cooldown(cool_key):
        return None

    try:
        from JARVIS07_GUARDIAN.severity import classify, is_auto_fixable
        sev = classify(error_type, message, source, module or "")
    except Exception:
        sev = "medium"

    try:
        from shared import db as _db
        error_id = _db.save_error(
            source=source,
            error_type=error_type,
            message=message,
            module=module,
            func_name=func_name,
            traceback=tb_str,
            context=context,
            severity=sev,
        )
    except Exception as e:
        log.error(f"[GUARDIAN] DB 저장 실패: {e}")
        return None

    # ERROR_DETECTED 이벤트 publish
    try:
        from shared import bus
        bus.publish(bus.EventType.ERROR_DETECTED, "GUARDIAN", {
            "error_id": error_id,
            "source": source,
            "module": module,
            "error_type": error_type,
            "message": (message or "")[:300],
            "severity": sev,
        })
    except Exception as e:
        log.warning(f"[GUARDIAN] 이벤트 publish 실패: {e}")

    log.info(f"[GUARDIAN] 오류 수집 — #{error_id} [{sev}] {error_type}: {(message or '')[:80]}")
    return error_id


# ── A. 전역 예외 훅 — install() 안 클로저로 설치 ─────────────────


# ── B. APScheduler 잡 실패 리스너 ────────────────────────────────

def make_scheduler_listener():
    """APScheduler EVENT_JOB_ERROR 콜백 함수 반환 — 내부에서 catch() 직접 호출."""
    def _on_job_error(event):
        try:
            exc = event.exception
            tb_str = "".join(_tb_mod.format_exception(
                type(exc), exc, exc.__traceback__
            )) if exc else None
            if exc:
                catch(exc, "scheduler",
                      module=f"job:{event.job_id}",
                      func_name=event.job_id,
                      context=f'{{"job_id": "{event.job_id}"}}',
                      tb_str=tb_str)
            else:
                catch("JobError", "scheduler",
                      message=f"Job {event.job_id} failed",
                      module=f"job:{event.job_id}",
                      func_name=event.job_id,
                      context=f'{{"job_id": "{event.job_id}"}}')
        except Exception as e:
            log.warning(f"[GUARDIAN] 스케줄러 리스너 오류: {e}")

    return _on_job_error


# ── C. 로그 파일 watchdog ─────────────────────────────────────────

class _LogFileHandler:
    """로그 파일에서 *Traceback 블록* (진짜 예외의 구조적 증거) 을 실시간 감지."""

    def __init__(self, log_dir: Path, tail: bool = None):
        self._log_dir = log_dir
        self._positions: dict[str, int] = {}
        # ★ tail=True: 처음 보는 파일은 *현재 끝* 에서 시작 (아래 _scan_file 주석 참조)
        self._tail = _LOG_SCAN_TAIL if tail is None else bool(tail)

    def scan(self):
        """로그 폴더 내 *.log 파일 스캔 — 신규 Traceback 블록 수집."""
        for log_file in self._log_dir.glob("*.log"):
            try:
                self._scan_file(log_file)
            except Exception as e:
                log.debug(f"[GUARDIAN] 로그 스캔 오류 ({log_file.name}): {e}")

    def _scan_file(self, log_file: Path):
        key  = str(log_file)
        size = log_file.stat().st_size

        # ★★ 과거분 홍수 차단 (2026-07-25 — 이 수정에서 함께 발견한 결함)
        #   `_positions` 는 메모리라 처음 보는 파일은 pos=0 → *파일 전체* 를 읽는다.
        #   스캐너를 되살리는 순간 과거 Traceback 1,126건이 한꺼번에 '신규 오류' 로
        #   적재된다 — 그중 1,104건은 **이미 폐기된 Streamlit** 의 죽은 로그다.
        #   로그 스캐너는 *지금 벌어지는 일* 을 보는 실시간 감지기지 과거 발굴기가 아니다.
        #   → 처음 보는 파일은 끝(EOF)에서 시작한다 (`tail -f` 의미론).
        #   과거분 백필이 필요하면 GUARDIAN_LOG_SCAN_TAIL=0.
        if key not in self._positions:
            self._positions[key] = size if self._tail else 0
            if self._tail:
                return

        pos = self._positions[key]
        if size <= pos:
            if size < pos:  # 파일 회전 — 처음부터 다시
                self._positions[key] = 0
                pos = 0
            else:
                return

        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            f.seek(pos)
            new_text = f.read()
            self._positions[key] = f.tell()

        for m in _TRACEBACK_PAT.finditer(new_text):
            block = m.group(0)
            # ★ 인터프리터가 스스로 '무시했다' 고 선언한 예외 — __del__·GC·종료 중 발생.
            #   전파되지 않으므로 기능 실패가 아니다. 자연어 노이즈 목록이 아니라
            #   CPython unraisable hook 의 *구조적 판정* 을 그대로 존중한다.
            if m.group("unraisable") and not _LOG_SCAN_UNRAISABLE:
                continue
            # ★ 재귀 차단 — GUARDIAN 자체 수집/스캔 로그는 수집 안 함
            if _LOG_SKIP_PAT.search(block):
                continue
            exc_line = m.group("exc").strip()
            dotted, _, msg = exc_line.partition(":")
            etype = dotted.strip().split(".")[-1] or "LogError"
            catch(
                etype,
                "log_file",
                message=(msg.strip() or exc_line)[:500],
                module=log_file.name,
                # ★ tb_str 전달 — 이제야 _is_sandbox_traceback 가 실제로 동작한다
                #   (종전 경로는 tb 를 안 넘겨 sandbox 차단이 무력했다)
                tb_str=block,
            )


# 다중 로그 스캐너 (에이전트별 로그 디렉토리 전부 감시)
_log_scanners: list[_LogFileHandler] = []


def _discover_log_dirs() -> list[Path]:
    """JARVIS 프로젝트 내 모든 logs/ 디렉토리를 자동 탐색.

    - _ROOT/logs/ (루트 daemon 로그)
    - _ROOT/JARVIS*/logs/ (각 에이전트 로그)
    → 새 에이전트 추가 시 자동 인식 — 하드코딩 불필요.
    """
    dirs: list[Path] = []
    root_log = _ROOT / "logs"
    if root_log.is_dir():
        dirs.append(root_log)
    for jarvis_dir in sorted(_ROOT.glob("JARVIS*")):
        if not jarvis_dir.is_dir():
            continue
        log_dir = jarvis_dir / "logs"
        if log_dir.is_dir():
            dirs.append(log_dir)
    return dirs


def scan_all_logs():
    """등록된 모든 로그 디렉토리 스캔 (job_scan_logs 에서 호출)."""
    if not _LOG_SCAN_ENABLED:          # 킬스위치 GUARDIAN_LOG_SCAN=0
        return
    for scanner in _log_scanners:
        try:
            scanner.scan()
        except Exception as e:
            log.debug(f"[GUARDIAN] 스캐너 오류: {e}")


def register_log_dir(log_dir: Path):
    """신규 에이전트 로그 디렉토리 추가 등록."""
    if not log_dir.exists():
        log.warning(f"[GUARDIAN] 로그 폴더 없음 (건너뜀): {log_dir}")
        return
    existing = {s._log_dir for s in _log_scanners}
    if log_dir in existing:
        return
    _log_scanners.append(_LogFileHandler(log_dir))
    log.info(f"[GUARDIAN] 로그 감시 등록 — {log_dir}")


def init_log_scanner(log_dir: Path = None):
    """로그 스캐너 초기화. guardian_agent.register()에서 호출.

    log_dir 지정 시 해당 디렉토리만 추가.
    미지정 시 _discover_log_dirs() 로 전체 JARVIS*/logs/ 자동 탐색.
    """
    if log_dir is not None:
        register_log_dir(log_dir)
        return
    for d in _discover_log_dirs():
        register_log_dir(d)
    log.info(f"[GUARDIAN] 로그 스캐너 초기화 완료 — {len(_log_scanners)}개 디렉토리")


# ── D. auto_catch — try/except 없이도 오류 자동 수집 ─────────────

import functools

class auto_catch:
    """데코레이터 + 컨텍스트 매니저 겸용 — 예외를 자동으로 guardian 에 보고.

    데코레이터:
        @auto_catch("publisher")
        def post_to_naver(...): ...

    컨텍스트 매니저:
        with auto_catch("collector"):
            collect_stocks_data(theme)

    reraise=True(기본): 예외 재발생 — caller 도 실패를 인지.
    reraise=False: 예외 삼킴 — 오류 보고만 하고 계속 진행.
    """
    def __init__(self, source: str, reraise: bool = True):
        self._source  = source
        self._reraise = reraise

    # ── 데코레이터 모드 ──────────────────────────────────────────
    def __call__(self, fn):
        src = self._source
        rr  = self._reraise
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                try:
                    catch(exc, src,
                          module=getattr(fn, '__module__', src) or src,
                          func_name=getattr(fn, '__qualname__', fn.__name__))
                except Exception:
                    pass
                if rr:
                    raise
        return wrapper

    # ── 컨텍스트 매니저 모드 ─────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val is not None:
            try:
                catch(exc_val, self._source,
                      module=self._source,
                      func_name="<context>")
            except Exception:
                pass
        return not self._reraise   # True → 예외 삼킴, False → 재발생


# ── E. 단일 공개 진입점 ──────────────────────────────────────────

def catch(
    exc_or_type,
    source: str,
    *,
    message: str = None,
    module: str = None,
    func_name: str = None,
    context=None,
    tb_str: str = None,
    attempt: int = 0,
    max_attempts: int = 0,
) -> Optional[int]:
    """★ 단일 오류 캐치 진입점 — sys.excepthook·threading·APScheduler·log_scanner·외부 모두 여기로.

    ★ attempt / max_attempts (ERRORS [477] — 사용자 지시 2026-07-22):
      **재시도 루프 안에서 부르는 경우 반드시 전달할 것.**
      `attempt < max_attempts` 면 *잠정 실패* 로 기록해 Tier-2(LLM 수십 분) 판정을 보류한다.
      1회 실패는 오류가 아니다 — 재시도가 다 끝나야 '진짜 실패' 인지 알 수 있다.
      기록 자체는 즉시 남으므로 대시보드 관측성은 그대로다. 미루는 것은 LLM 착수뿐.
      (harness 는 자체 경로로 동일 처리 — ERRORS [476])

    두 가지 호출 형태:
        # Exception 객체 (외부 에이전트, auto_catch, 각종 훅)
        catch(exc, "writer", module=__name__)

        # 문자열 error_type (log_scanner 등 Exception 객체 없는 경우)
        catch("ValueError", "log_file", message="파일 없음", module="foo.log")

    context: str / dict / list 모두 허용 (내부에서 JSON 직렬화)
    tb_str:  traceback 문자열 직접 전달 (훅 내부에서 미리 포맷한 경우)
    """
    # ★ 2026-07-03 (ERRORS [298]) — 하위 호환 자동 교정: 구 report(source, exc) 역순 호출.
    #   report=catch 별칭 도입 시 문서화된 구 시그니처(report("writer", e))의 기존 호출
    #   314곳이 조용히 무음 no-op 이 되어 있었음 (source 에 Exception 바인딩 실패).
    #   단일 진입점에서 순서 감지·교정 — 양 형태 모두 정상 동작.
    if isinstance(source, BaseException) and not isinstance(exc_or_type, BaseException):
        exc_or_type, source = source, str(exc_or_type)

    if context is not None and not isinstance(context, str):
        try:
            import json as _json
            context = _json.dumps(context, ensure_ascii=False, default=str)
        except Exception:
            context = str(context)[:1000]

    if isinstance(exc_or_type, BaseException):
        error_type = type(exc_or_type).__name__
        msg        = str(exc_or_type)[:500]
        tb         = tb_str or _tb_mod.format_exc()
    else:
        error_type = str(exc_or_type)
        msg        = (message or "")[:500]
        tb         = tb_str

    # e7 (J02→J07 오류 보고) — J02 소속 에이전트가 보고하는 경우
    _J02_SRCS = ("writer", "economic", "theme", "draft", "law_enforcer", "seo",
                 "poster", "revise", "scheduler", "trend_economic", "trend_theme")
    _src_lower = str(source or "").lower()
    if any(s in _src_lower for s in _J02_SRCS):
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e7")
        except Exception:
            pass

    _eid = _collect_error(
        source=source,
        error_type=error_type,
        message=msg,
        module=module,
        func_name=func_name,
        tb_str=tb,
        context=context,
    )

    # ★ 재시도가 아직 남은 '잠정' 실패 → Tier-2 판정 보류 (ERRORS [477])
    #   1회 실패는 오류가 아니다. 재시도가 다 끝나야 진짜 실패인지 알 수 있다.
    #   기록은 이미 남았으므로 대시보드에는 그대로 보인다 — 미루는 건 LLM 착수뿐.
    if _eid and _eid != _SMOKE_ID and max_attempts and attempt and attempt < max_attempts:
        try:
            from shared.db import mark_error_provisional
            mark_error_provisional(int(_eid), True)
        except Exception:
            pass
    return _eid


# 하위 호환 alias — 기존 report() 호출 코드 즉시 수정 불필요
report = catch

# ★ 2026-07-03: .claude/hooks/guardian_error_hook.py 가 kwargs 형태
#   collect_error(source=, error_type=, message=, module=, context=) 로 호출하나
#   공개 심볼이 없어 훅이 조용히 죽어 있었음 → 공개 별칭 제공 (catch 6메커니즘 중 외부 훅 경로 복구)
collect_error = _collect_error


def install() -> None:
    """★ 단일 설치 함수 — 데몬 부팅 시 1회 호출.

    아래 모든 자동 배선을 한 번에 설치:
      · sys.excepthook        (메인 스레드 미처리 예외)
      · threading.excepthook  (백그라운드 스레드 미처리 예외)
      · log_scanner           (모든 JARVIS*/logs/ 자동 탐색 + 감시)

    훅 로직이 모두 catch() 를 직접 호출 — 별도 함수 없음.

    APScheduler 리스너는 JARVIS04_SCHEDULER.job_history.attach_listeners() 에서
    make_scheduler_listener() 콜백을 받아 등록 — JARVIS04 단일 진입점 규정 준수.
    """
    _orig = sys.excepthook

    # 1) sys.excepthook — catch() 직접 호출
    def _main_exc_hook(exc_type, exc_val, exc_tb):
        _orig(exc_type, exc_val, exc_tb)
        try:
            tb_str = "".join(_tb_mod.format_exception(exc_type, exc_val, exc_tb))
            module = func = None
            if exc_tb:
                frames = _tb_mod.extract_tb(exc_tb)
                if frames:
                    last = frames[-1]
                    module = Path(last.filename).name if last.filename else None
                    func   = last.name
            catch(exc_val, "daemon", module=module, func_name=func, tb_str=tb_str)
        except Exception:
            pass

    sys.excepthook = _main_exc_hook

    # 2) threading.excepthook — catch() 직접 호출
    import threading as _t
    def _thread_exc_hook(args):
        try:
            tb_str = "".join(_tb_mod.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback
            ))
            thread_name = getattr(args.thread, "name", "unknown_thread")
            catch(args.exc_value, "thread", module=thread_name, tb_str=tb_str)
        except Exception:
            pass

    _t.excepthook = _thread_exc_hook

    # 3) 로그 스캐너 — 전체 JARVIS*/logs/ 자동 탐색
    for d in _discover_log_dirs():
        register_log_dir(d)

    log.info(
        f"[GUARDIAN] install() 완료 — "
        f"sys.excepthook ✅ threading.excepthook ✅ "
        f"log_scanner {len(_log_scanners)}개 디렉토리 ✅"
    )


# 하위 호환 alias
register_global_hook = install


# ── F. 스모크 테스트 — 캐치 경로가 *실제로* 살아있는지 동작으로 확인 ──────────

def catch_path_effective() -> Optional[bool]:
    """★ 캐치 경로 스모크 — `patch_effective()` 표준 (CLAUDE.md '복사본을 진실로 믿지 말 것').

    **왜 필요한가**: 로그 스캐너는 70일간 매치 0건이었는데 아무도 몰랐다. 코드는 멀쩡히
    존재했고, 잡도 정상 실행됐고, 로그에도 오류가 없었다. *작동하는지 확인하는 장치가
    없었기* 때문이다. 코드 존재는 적용의 증거가 아니다 — 그래서 여기서는 가짜 오류를
    **실제 소비자가 쓰는 경로 그대로** 통과시켜 예외/결과 유무로 판정한다.

    검사하는 두 다리:
      ① 로그 스캐너: 임시 로그 파일에 진짜 형태의 Traceback 을 쓰고
         `_LogFileHandler._scan_file`(실 소비자) 로 스캔 → catch() 까지 도달하는가
      ② 쿨다운: harness 형식 메시지의 attempt 만 바꿔 두 번 호출 → 2회차가 막히는가

    ★ 합성 입력에는 `__smoke__` 표식이 박혀 있어 DB·이벤트·통계를 오염시키지 않는다.

    반환: True(유효) / False(무력 — 즉시 수리 필요) / None(판정 불가)
    """
    import tempfile

    try:
        # ── ① 로그 스캐너 다리 ────────────────────────────────────────
        seen: list[tuple] = []
        _orig_catch = globals().get("catch")
        if _orig_catch is None:
            return None

        def _spy(exc_or_type, source, **kw):
            seen.append((exc_or_type, source, kw.get("message", "")))
            return _orig_catch(exc_or_type, source, **kw)

        synthetic = (
            'Traceback (most recent call last):\n'
            '  File "/does/not/exist/__smoke__probe.py", line 1, in <module>\n'
            '    raise RuntimeError("__smoke__ guardian catch-path probe")\n'
            'RuntimeError: __smoke__ guardian catch-path probe\n'
        )

        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            logf = tmpdir / "smoke_probe.log"
            logf.write_text("정상 동작 중\n", encoding="utf-8")
            # tail=False → 합성 파일은 처음부터 읽어야 검사가 성립
            handler = _LogFileHandler(tmpdir, tail=False)
            globals()["catch"] = _spy
            try:
                handler._scan_file(logf)                  # 기준선(예외 없음)
                base = len(seen)
                with open(logf, "a", encoding="utf-8") as f:
                    f.write(synthetic)
                handler._scan_file(logf)                  # ★ 실 소비자 경로
            finally:
                globals()["catch"] = _orig_catch

        scanner_ok = len(seen) > base and any(
            s[0] == "RuntimeError" and s[1] == "log_file" for s in seen
        )

        # ── ② 쿨다운 다리 (harness 형식 — attempt 만 다름) ─────────────
        h1 = "[harness:__smoke__ 발행] attempt=1 step=③ 대본 생성: 실패"
        h2 = "[harness:__smoke__ 발행] attempt=2 step=③ 대본 생성: 실패"
        k1 = _cool_key("smoke", "probe", "RuntimeError", h1)
        k2 = _cool_key("smoke", "probe", "RuntimeError", h2)
        cooldown_ok = (k1 == k2)          # attempt 가 정규화되어 같은 키여야 한다

        ok = bool(scanner_ok and cooldown_ok)
        if not ok:
            log.error(
                f"[GUARDIAN] ★ 캐치 경로 스모크 실패 — "
                f"로그스캐너={'OK' if scanner_ok else '무력'} / "
                f"쿨다운={'OK' if cooldown_ok else '무력'}"
            )
        else:
            log.info("[GUARDIAN] 캐치 경로 스모크 통과 — 로그스캐너 ✅ 쿨다운 ✅")
        return ok
    except Exception as e:
        log.warning(f"[GUARDIAN] 캐치 경로 스모크 판정 불가: {e}")
        return None


def record_external_change(
    source: str,
    fixed_file: str,
    description: str,
    error_type: str = "ExternalEdit",
    severity: str = "low",
    actor: str = "external",
    commit_hash: str = "",
    patch: str = "",
    target_file: str = "",
) -> Optional[int]:
    """외부 도구(VS Code Claude Code·git·사용자 직접 편집)에서 발생한 코드 변경을 박제.

    `report_manual_fix` 의 *외부 변경 전용* 래퍼. 차이점:
      - severity 기본값 'low' (외부 변경은 의도적 — 오류가 아님)
      - actor='external' / 'vscode' / 'git-audit' / 'auto_repair' 등 식별자
      - commit_hash 옵션 (git 회고 시 추적)

    학습 시스템 자동 연동: pattern_fixer.record_pattern_hit() 자동 호출.

    Args:
        source:      "auto_repair" / "vscode_claude" / "git_audit" / "user_edit"
        fixed_file:  변경된 파일 경로 (jarvis-agent 상대)
        description: 변경 내용 1~3문장
        error_type:  분류 (예: "AutoRepairFix", "GitCommit", "VSCodeEdit")
        severity:    "low" (기본 — 외부는 보통 정상 작업) | "medium" 등
        actor:       수정 주체
        commit_hash: git commit hash (선택)

    Returns:
        int | None: error_log.id
    """
    desc = description if not commit_hash else f"[{commit_hash[:8]}] {description}"
    return report_manual_fix(
        source=source,
        fixed_file=fixed_file,
        description=desc,
        error_type=error_type,
        severity=severity,
        actor=actor,
        patch=patch,               # ★ diff 확보 시 actionable llm_patch 경로 진입
        target_file=target_file,
    )


# ★ 정책/기능 변경 타입 (2026-07-02) — '재발할 오류'가 아니라 의도적 변경 → actionable
#   llm_patch 학습 대상 아님(오탐·헛보상 차단). recurrable=None(자동) 판정에서만 적용;
#   recurrable=True 명시 opt-in 은 이 목록과 무관하게 actionable.
_MANUAL_POLICY_TYPES = frozenset({
    "PromptLeak", "RuleConsolidation", "RuleAddition", "FlowDefect",
    "DashboardFilter", "AgentAddition", "AutoFixCapability", "ManualFixTracking",
    "ExternalEdit", "GitCommit", "VSCodeEdit", "ModelInconsistency",
    "ModelCatalogUpgrade", "HardcodedPath", "ManualFix",
})


def report_manual_fix(
    source: str,
    fixed_file: str,
    description: str,
    error_type: str = "ManualFix",
    severity: str = "medium",
    actor: str = "claude",
    patch: str = "",
    target_file: str = "",
    error_message: str = "",
    recurrable: Optional[bool] = None,
    symptom: str = "",
) -> Optional[int]:
    """Claude 또는 사용자가 *발견·수정한* 결함을 회고적으로 박제하는 API.

    이 함수는 *런타임 오류* 가 아니라 *코드 결함 발견·수정 작업* 을 기록한다.
    예: BLOG_SUPREME_LAW 누수 정리, hub.py NoneType 슬라이싱 안전화 등.

    error_log INSERT 후 즉시 status='manual' 마킹 → 수동수정 카드에 카운트.
    쿨다운 적용 안 함 (의도적 기록).

    Args:
        source:      소속 에이전트 ("writer" / "guardian" / "infra" 등)
        fixed_file:  수정한 파일 경로 (jarvis-agent 상대)
        description: 무엇을·왜 수정했는지 1~3문장
        error_type:  분류 (예: "RelativeImport", "NoneSlicing", "PromptLeak")
        severity:    "low" | "medium" | "high"
        actor:       "claude" | "user" — 수정 주체
        symptom:     ★ *고치기 전에 무엇이 잘못 보였는지* (증상). 생략하면 description 이
            증상 자리에도 들어가 수리 이력의 "②증상" 과 "④조치" 가 같은 문장이 된다
            (사용자 박제 2026-07-23). 재현 가능한 현상을 한 문장으로 적을 것.

    Returns:
        int | None: error_log.id

    Example:
        from JARVIS07_GUARDIAN.error_collector import report_manual_fix
        report_manual_fix(
            source="writer",
            fixed_file="JARVIS02_WRITER/economic_poster.py",
            description="★ 제0조 자연어 인용 7곳 → (헌법 제0조 적용) 짧은 참조로 통일. BLOG_SUPREME_LAW.md 단일 진입점 누수 차단.",
            error_type="PromptLeak",
            severity="medium",
            actor="claude",
        )
    """
    try:
        from shared import db as _db
        from datetime import datetime
        # error_log INSERT — 쿨다운 우회 (의도적 기록)
        error_id = _db.save_error(
            source=source,
            error_type=error_type,
            message=(symptom or description)[:500],
            module=fixed_file,
            func_name=None,
            traceback=None,
            context=f"actor={actor}",
            severity=severity,
        )
        # 즉시 manual 마킹 + resolution + fixed_file 기록
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        with _db.get_db() as conn:
            conn.execute(
                """UPDATE error_log
                   SET status='manual', resolution=?, fixed_file=?, fixed_at=?
                   WHERE id=?""",
                (f"[{actor}] {description[:500]}", fixed_file, now, error_id),
            )
        # ★ 학습 등록 (2026-07-02) — 오류수정(재발가능)은 actionable llm_patch(+밴딧 보상)로,
        #   정책/기능 변경·diff 없음은 legacy change-tracking 으로 분기.
        #   actionable 이면: record_sdk_fix → eval(Sonnet 5) 게이트 → stored_patch 저장 →
        #   hits>0 시 bandit.reward → *강화학습 모델(Bandit)이 실제로 학습을 시작*.
        try:
            from JARVIS07_GUARDIAN.pattern_fixer import record_pattern_hit
            # actionable 3-state opt-in: True=명시 오류수정 / False=명시 제외 / None=자동(실오류타입+diff)
            if recurrable is True:
                _actionable = bool(patch)
            elif recurrable is False:
                _actionable = False
            else:
                _actionable = bool(patch) and error_type not in _MANUAL_POLICY_TYPES

            _learned = False
            if _actionable:
                from JARVIS07_GUARDIAN.pattern_fixer import record_sdk_fix
                _n = record_sdk_fix(
                    {"error_type": error_type,
                     "message": error_message or description,   # ★ 재발 fingerprint = 실오류 메시지
                     "module": fixed_file},
                    {(target_file or fixed_file): patch},
                    source=f"manual-{actor}",
                )
                _learned = bool(_n)
                log.info(f"[GUARDIAN] manual_fix "
                         f"{'actionable 학습+밴딧 보상 발화' if _n else 'diff 있으나 eval 게이트 미통과→change-tracking'}"
                         f" — #{error_id}")
            if not _learned:
                # 정책/기능 변경 / diff 없음 / eval 미통과 → change-tracking (재발 개념 없음)
                record_pattern_hit(
                    {"error_type": error_type, "message": description},
                    fixer_name=error_type.lower().replace("error", "").replace("warning", "") or "manual",
                    fixed_file=fixed_file,
                    source=f"manual-{actor}",
                )
        except Exception as e:
            log.debug(f"[GUARDIAN/learned] manual_fix 학습 등록 실패: {e}")
        log.info(f"[GUARDIAN] manual_fix 박제 — #{error_id} [{actor}] {fixed_file}")
        return error_id
    except Exception as e:
        log.error(f"[GUARDIAN] report_manual_fix 실패: {e}")
        return None


# ────────────────────────────────────────────────────────────────────────
# 사용자 관찰 사고 학습 API (★ ADR 008 / 사용자 박제 2026-05-17)
# ────────────────────────────────────────────────────────────────────────

_LEARNED_INCIDENTS_PATH = Path(__file__).resolve().parent / "learned_incidents.json"
_INCIDENTS_LOCK = threading.Lock() if "threading" in dir() else None  # 안전 가드

# domain 화이트리스트 (ADR 008 매트릭스와 동기)
_VALID_DOMAINS = {
    "image", "publish", "category", "length", "constitution",
    "schedule", "tools", "infra", "learning", "other",
}


def report_user_observed_incident(
    domain: str,
    symptom: str,
    expected: str = "",
    actual: str = "",
    detection: str = "user_visual",
    source_files: Optional[list[str]] = None,
    severity: str = "medium",
) -> Optional[int]:
    """사용자가 *발견* 한 사고를 도메인 카테고리화해서 학습 데이터로 박제.

    런타임 예외와 별개 — Python 예외 발생 안 했지만 *사용자가 시각/관찰로 확인* 한
    사고를 *학습 데이터* 화. JARVIS07 auditor 가 도메인 단위 N회 반복 시 자동 검증
    규칙 신설 트리거.

    Args:
        domain:        ADR 008 Domain Ownership Matrix 의 도메인 키
                       ("image" | "publish" | "category" | "length" |
                        "constitution" | "schedule" | "tools" | "infra" |
                        "learning" | "other")
        symptom:       사용자가 본 증상 (1줄)
        expected:      기대했던 동작
        actual:        실제 본 동작
        detection:     발견 방법 ("user_visual" | "user_report" | "user_audit")
        source_files:  의심 파일 경로 리스트 (선택)
        severity:      "low" | "medium" | "high" | "critical"

    Returns:
        int | None: error_log.id (DB INSERT 결과)

    학습 등록 fingerprint: `domain::normalized(symptom)` — 같은 도메인+유사 증상이
    N회 반복되면 *카테고리 단위 자동 fixer 신설 후보* 가 됨.

    Example:
        report_user_observed_incident(
            domain="category",
            symptom="네이버 발행이 '주식-테마분류' 카테고리로 잘못 지정",
            expected="'경제 브리핑' 카테고리",
            actual="첫 번째 항목으로 fallback",
            detection="user_visual",
            source_files=["JARVIS02_WRITER/naver_poster.py"],
            severity="high",
        )
    """
    if domain not in _VALID_DOMAINS:
        log.warning(f"[GUARDIAN/incident] 알 수 없는 domain={domain} → 'other' 로 분류")
        domain = "other"

    # 1) DB error_log 박제 (런타임 오류와 통합 추적)
    desc_lines = [f"[USER_OBSERVED][{domain}] {symptom}"]
    if expected:
        desc_lines.append(f"기대: {expected}")
    if actual:
        desc_lines.append(f"실제: {actual}")
    if source_files:
        desc_lines.append(f"의심 파일: {', '.join(source_files[:5])}")
    description = " | ".join(desc_lines)

    error_id = report_manual_fix(
        source=f"user_incident/{domain}",
        fixed_file=(source_files[0] if source_files else "(unknown)"),
        description=description,
        error_type=f"UserObserved_{domain}",
        severity=severity,
        actor="user",
    )

    # 2) learned_incidents.json 별도 박제 (도메인 카테고리 단위 누적)
    try:
        import json
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        # 정규화된 증상 — 숫자·날짜·경로 제거 후 fingerprint 계산
        norm = re.sub(r"\d+", "N", symptom or "")
        norm = re.sub(r"\s+", " ", norm).strip().lower()
        fingerprint = f"{domain}::{norm[:120]}"

        if _LEARNED_INCIDENTS_PATH.exists():
            data = json.loads(_LEARNED_INCIDENTS_PATH.read_text(encoding="utf-8"))
        else:
            data = {"incidents": []}

        # 동일 fingerprint 매칭 시 hit_count 누적
        found = False
        for inc in data.get("incidents", []):
            if inc.get("fingerprint") == fingerprint:
                inc["hit_count"] = int(inc.get("hit_count", 0)) + 1
                inc["last_seen"] = now
                inc.setdefault("examples", []).append({
                    "symptom": symptom[:120],
                    "actual": actual[:120],
                    "ts": now,
                })
                if len(inc["examples"]) > 10:
                    inc["examples"] = inc["examples"][-10:]
                found = True
                break

        if not found:
            data.setdefault("incidents", []).append({
                "fingerprint": fingerprint,
                "domain":      domain,
                "symptom":     symptom[:200],
                "expected":    expected[:200],
                "actual":      actual[:200],
                "detection":   detection,
                "source_files": list(source_files or [])[:5],
                "severity":    severity,
                "hit_count":   1,
                "first_seen":  now,
                "last_seen":   now,
                "examples":    [],
            })
            log.info(f"[GUARDIAN/incident] ★ 신규 — domain={domain} fp='{fingerprint[:60]}'")

        # 도메인 카운트 누적 (자동 fixer 신설 트리거용)
        domain_counts = data.setdefault("_domain_totals", {})
        domain_counts[domain] = domain_counts.get(domain, 0) + 1

        _LEARNED_INCIDENTS_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[GUARDIAN/incident] learned_incidents 박제 실패: {e}")

    return error_id


def incident_stats() -> dict:
    """learned_incidents 통계 — 도메인별 누적 + 핫스팟 Top.

    Returns:
        {
            "total_incidents":  int,
            "by_domain":        {domain: hit_count},
            "hotspots":         [{"domain", "symptom", "hit_count"}, ...] Top 10,
        }
    """
    try:
        import json
        if not _LEARNED_INCIDENTS_PATH.exists():
            return {"total_incidents": 0, "by_domain": {}, "hotspots": []}
        data = json.loads(_LEARNED_INCIDENTS_PATH.read_text(encoding="utf-8"))
        incs = data.get("incidents", [])
        by_domain: dict[str, int] = {}
        for inc in incs:
            d = inc.get("domain", "other")
            by_domain[d] = by_domain.get(d, 0) + int(inc.get("hit_count", 0))
        sorted_incs = sorted(incs, key=lambda x: -int(x.get("hit_count", 0)))
        return {
            "total_incidents": len(incs),
            "by_domain":       by_domain,
            "hotspots": [
                {
                    "domain": i.get("domain"),
                    "symptom": i.get("symptom", "")[:80],
                    "hit_count": int(i.get("hit_count", 0)),
                }
                for i in sorted_incs[:10]
            ],
        }
    except Exception as e:
        log.error(f"[GUARDIAN/incident] stats 실패: {e}")
        return {"total_incidents": 0, "by_domain": {}, "hotspots": []}
