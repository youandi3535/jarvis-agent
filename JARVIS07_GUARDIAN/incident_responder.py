"""JARVIS07_GUARDIAN/incident_responder.py — 포스팅 실패 즉각 대응 루프 (Active Incident Responder)

포스팅 job 실패 감지 → 자동 수정 (canonical 2-tier) → 실패 플랫폼만 재발행 → 학습 기록.

★ 티어 정의는 architecture.py 단일 진실 소스. catch()→Tier 1(패턴·Bandit)→Tier 2(LLM).

흐름:
  1. TG: 🔧 [GUARDIAN] {job_id} 실패 감지 — 자동 대응 시작
  2. 오류 분류: code_bug | transient | unknown
     ★ 판정 목록을 이 모듈이 **소유하지 않는다** — 전부 `severity` 단일 진입점 위임
       (2026-07-25). 남은 정규식은 *구조 추출* 뿐(줄 앵커 + 길이 제한). 아래 `_classify` 참조.
  3. code_bug / unknown:
       Tier 1 — 패턴 자동 수정 (static 6 + learned + Contextual Bandit, ~5s)
       Tier 2 — LLM 자동 수정 (Claude Code SDK · Sonnet 5, ~10min) — Tier 1 실패 시만
     transient: 30초 대기 후 즉시 재시도 (코드 수정 없음)
  4. retry_fns 호출 (실패 플랫폼만)
  5. learned_patterns 자동 기록
  6. TG: 결과 보고

★ 자동 승인 — Telegram 인라인 버튼 없음. side_effect="internal" (jarvis-agent 폴더 내부).
"""
from __future__ import annotations

import builtins
import logging
import os
import re
import threading
import time
from typing import Callable

log = logging.getLogger("jarvis.guardian.incident")

# 동시 실행 방지 (같은 시간대 중복 incident 차단)
_active = threading.Lock()

_TG_MAX = 2000
_TRANSIENT_WAIT = 30  # 일시적 오류 재시도 전 대기(초)

# ── 오류 분류 — 판정 목록 0개 (★ 사용자 박제 2026-07-25) ──────────────────
#
# ★ 종전 결함: `_TRANSIENT_KEYWORDS` / `_CODE_BUG_TYPES` 두 *로컬 목록* 이
#   `severity.is_transient()` 위임을 앞뒤로 포위하고 있었다. 둘 다 **단어 경계 없는
#   부분문자열 검사** 를 로그 꼬리 3000자 전체에 던졌다 —
#     "uuid": "235059e1-...-a0a10a4403cb"  ← 이 UUID 안의 '403' 3글자가
#   발행 실패 대응 경로를 transient 로 확정시켰다(감사 재현 성공).
#   또 목록이 로컬이라 severity 가 아무리 정교해져도 물려받지 못했다(①단일 진입점 위반).
#
# ★ 현재: 판정은 전부 `JARVIS07_GUARDIAN.severity` 단일 진입점에 위임한다.
#   이 모듈에 남은 문자열 매칭은 *판정* 이 아니라 **구조 추출** 뿐이며(아래 두 정규식),
#   ① 줄 시작 앵커 + 단어 경계를 강제하고 ② 스캔 길이를 `_TYPE_SCAN_CHARS` 로 제한한다.
#   → UUID·해시·URL 안의 우연한 부분문자열은 구조상 매칭 불가.
#
# ★ severity 에 요청한 공개 API (있으면 자동 승계, 없으면 아래 폴백):
#     · `severity.is_code_bug_type(error_type) -> bool`   ← 코드버그 타입 가드(작업 중)
#     · `severity.detect_error_type(text) -> str`         ← 로그 텍스트 → error_type 추출
#   getattr 로 *런타임 조회* 하므로 severity 에 생기는 즉시(데몬 재시작 후) 반영된다.

# 파이썬 traceback 마지막 줄 형태만 매칭: "[pkg.mod.]ExcName: 메시지"
#   · `^` (re.M) 로 줄 시작 앵커 → UUID/URL 중간 토큰 매칭 불가
#   · 이름에 소문자 1자 이상 강제 → "INFO:" 같은 로그레벨 배제
_EXC_LINE_RE = re.compile(
    r"^[ \t]*(?:[A-Za-z_][\w.]*\.)?([A-Z][A-Za-z0-9_]*[a-z][A-Za-z0-9_]*)[ \t]*:",
    re.M,
)

# harness 이슈 줄(economic_poster 가 기록하는 구조화 포맷): "[naver] step: kind: detail"
#   kind 는 구조화 필드라 네이버·티스토리 × 경제·테마 4조합에 동일 적용된다(③).

_TYPE_SCAN_CHARS = 1500   # 타입 추출 스캔 상한 (로그 꼬리 3000자 전체 스캔 금지)


def _tg(msg: str) -> None:
    try:
        from shared.notify import send_tg
        send_tg(msg[:_TG_MAX])
    except Exception:
        pass


# ── severity 위임 헬퍼 (모듈 객체는 매 호출 조회 — 함수 참조 선캡처 금지) ──
def _severity():
    try:
        from JARVIS07_GUARDIAN import severity as _sev
        return _sev
    except Exception as e:            # pragma: no cover — severity 부재는 설치 이상
        log.warning(f"[Incident] severity 로드 실패(분류 위임 불가): {e}")
        return None


def _is_exception_name(name: str) -> bool:
    """이름이 *실제 파이썬 예외 클래스* 인가 — builtins 런타임 조회(② 동적 설계).

    종전 `_CODE_BUG_TYPES` 10종 하드코딩을 대체. 목록을 손으로 나열하지 않고
    언어 자체에서 파생하므로 IndexError·ZeroDivisionError 등도 자동 포함된다.
    Warning 계열은 제외 (경고는 실패 원인이 아님).
    """
    obj = getattr(builtins, (name or "").strip(), None)
    return (
        isinstance(obj, type)
        and issubclass(obj, Exception)
        and not issubclass(obj, Warning)
    )


def _is_code_bug_type(etype: str) -> bool:
    """코드 버그 타입인가 — severity 단일 진입점 위임 (로컬 목록 0)."""
    if not etype:
        return False
    sev = _severity()
    if sev is not None:
        # ① severity 가 코드버그 타입 가드를 공개하면 그것이 단일 진실 소스
        for probe in ("is_code_bug_type", "is_code_error_type"):
            fn = getattr(sev, probe, None)
            if callable(fn):
                try:
                    return bool(fn(etype))
                except Exception:
                    pass
        # ② 폴백 — severity 가 *이미 공개한 판정* 만 조합
        try:
            if sev.is_transient(etype, ""):
                return False          # severity 가 일시적이라 한 타입은 코드버그 아님
            if sev.is_deterministic_code_error(etype):
                return True
        except Exception:
            pass
    return _is_exception_name(etype)


def _harness_kinds(text: str) -> list[str]:
    """harness 이슈 kind 목록 추출 — **severity 에 위임**(구조 추출, 판정 아님).

    ★ 2026-08-12: 정규식·상한 사본을 여기서 **삭제** 했다. severity 가 kind 의 주인이고
      뽑기와 해석이 갈라지면 드리프트다(①). 종전엔 severity 로 '이관 선언' 만 하고 원본을
      안 지워 바이트 동일한 두 벌이 공존했다 — CLAUDE_INFRA 「이관 완전성」 위반이었다.
    """
    from JARVIS07_GUARDIAN.severity import kinds_in_text
    return kinds_in_text(text)


def _detect_error_type(error_text: str) -> str:
    """로그 텍스트에서 error_type 추출 — 줄 앵커 + 길이 제한.

    킬스위치 `GUARDIAN_INCIDENT_TYPE_SCAN=0` → 추출 생략(텍스트 위임만 사용).
    """
    text = error_text or ""
    if not text or os.getenv("GUARDIAN_INCIDENT_TYPE_SCAN", "1") == "0":
        return ""
    sev = _severity()
    fn = getattr(sev, "detect_error_type", None) if sev is not None else None
    if callable(fn):                  # ★ severity 신설 시 자동 승계
        try:
            return str(fn(text) or "")
        except Exception:
            pass
    tail = text[-_TYPE_SCAN_CHARS:]
    names = _EXC_LINE_RE.findall(tail)
    for name in reversed(names):      # traceback 마지막 줄이 진짜 원인
        if _is_exception_name(name):
            return name
        if name.endswith(("Error", "Exception")):
            return name               # 서드파티 예외(TimeoutException 등) — 형태로 인정
        if sev is not None:
            try:
                if sev.is_transient(name, ""):
                    return name       # severity 가 아는 타입(ReadTimeout 등)
            except Exception:
                pass
    return ""


def _classify(error_text: str, returncode: int | None = None) -> str:
    """오류 유형 분류: 'code_bug' | 'transient' | 'unknown'

    ★ 판정 위임 순서 (전부 severity 소유 — 이 모듈에 판정 목록 0개):
      0) returncode == WATCHDOG_KILL_RC       → transient  (구조 신호 최우선)
      1) harness 이슈 kind 전부 NON_CODE      → transient  (구조화 필드 — 4조합 자동)
      2) severity 가 코드버그 타입이라 판정   → code_bug
      3) severity.is_transient(type, text)    → transient
      4) 그 외                                 → unknown   (code_bug 와 동일 경로)

    ★ 2026-07-24 구조적 신호 최우선: 발행 subprocess 가 watchdog freeze 로 강제종료된
      경우(returncode == WATCHDOG_KILL_RC) 는 *일시적 정지* 이지 코드버그가 아니다.
      오류 텍스트의 자연어 패턴에 의존하지 않고 종료코드로 확정 → Tier-2 SDK 낭비 차단.
      (severity.NON_CODE_ISSUE_KINDS 는 건드리지 않는다 — 사용자 박제 2026-07-22:
       freeze/stuck 은 반복 시 진짜 성능결함일 수 있어 *가시성* 은 유지. 여기서는
       incident 자동수리 경로의 분류만 교정.)

    킬스위치 `GUARDIAN_INCIDENT_CLASSIFY=0` → 항상 'unknown'(수정 시도 후 재발행).
    """
    text = error_text or ""
    if os.getenv("GUARDIAN_INCIDENT_CLASSIFY", "1") == "0":
        return "unknown"

    # 0) 구조 신호 — watchdog 강제종료
    if returncode is not None:
        try:
            from JARVIS00_INFRA.watchdog import WATCHDOG_KILL_RC
            if int(returncode) == int(WATCHDOG_KILL_RC):
                return "transient"
        except Exception:
            pass

    sev = _severity()

    # 1) harness 이슈 kind — severity.NON_CODE_ISSUE_KINDS 위임 (구조화 필드)
    #    factuality·engagement·infra_throttle 등은 *글 내용/운영* 문제라 코드 수정 대상이
    #    아니다(ERRORS [475], error_log id=4142 오학습 사고). 하나라도 코드 kind 가 섞이면
    #    수정 경로를 유지 — 보수적.
    kinds = _harness_kinds(text)
    if sev is not None and kinds:
        try:
            if all(sev.is_transient("", "", kind=k) for k in kinds):
                return "transient"
        except Exception:
            pass

    etype = _detect_error_type(text)

    # 2) 코드버그 타입 — severity 위임
    if _is_code_bug_type(etype):
        return "code_bug"

    # 3) 일시적 판정 — severity 단일 진입점 (정규식은 severity 가 소유)
    if sev is not None:
        try:
            if sev.is_transient(etype, text, source="incident_responder"):
                return "transient"
        except Exception:
            pass

    return "unknown"


def _make_error_record(error_text: str, job_id: str) -> dict:
    """pattern_fixer / error_analyzer 에 전달할 synthetic error_record."""
    # 에러 타입 추출 — `_detect_error_type` 단일 경로 (종전 `_CODE_BUG_TYPES` 정규식 폐기)
    detected_type = _detect_error_type(error_text) or "PostingFailure"

    # traceback 에서 모듈 경로 추출
    mod_m = re.search(r'File "([^"]+\.py)"', error_text)
    module = mod_m.group(1) if mod_m else f"{job_id}_pipeline"

    return {
        "id": -1,
        "source": "incident_responder",
        "error_type": detected_type,
        "message": error_text[:500],
        "module": module,
        "func_name": "posting_pipeline",
        "severity": "high",
        "traceback": error_text[:2000],
    }


def _try_fast_fix(error_record: dict, job_id: str) -> bool:
    """Tier 1 (패턴 자동 수정 — static 6 + learned + Contextual Bandit). ~1-10초.

    ★ analyze() 경유 — Bandit 학습 포함. try_pattern_fix 직접 호출 금지 (Bandit 우회됨).
    """
    try:
        from JARVIS07_GUARDIAN.error_analyzer import analyze
        from JARVIS07_GUARDIAN.error_fixer import apply_fix

        result = analyze(error_record)
        if result and result.get("fixable"):
            success = apply_fix(-1, result)
            # Bandit 보상은 pattern_fixer/error_fixer 내부에서 자동 기록
            if success:
                log.info(f"[Incident] fast_fix 성공: {result.get('source')} @ {result.get('target_file')}")
                return True
    except Exception as e:
        log.warning(f"[Incident] fast_fix 오류: {e}")
    return False


def _try_sdk_targeted_fix(
    error_text: str,
    job_id: str,
    failed_platforms: list[str],
    theme: str,
    error_record: dict | None = None,
) -> bool:
    """Tier 2: Claude Code SDK targeted 수정 (최대 10분). Tier 1 실패 시만.

    ★ error_record 전달 시 SDK 수정이 밴딧 arm 으로 학습됨 (record_sdk_fix).
    """
    try:
        from JARVIS07_GUARDIAN.auto_repair import run_auto_repair_targeted
        return run_auto_repair_targeted(
            context=error_text,
            job_id=job_id,
            failed_platforms=failed_platforms,
            theme=theme,
            error_record=error_record,   # ★ 밴딧 학습 브리지
        )
    except Exception as e:
        log.warning(f"[Incident] sdk_targeted_fix 오류: {e}")
    return False


# ── 자율 SDK 수리 게이트 — *관측만* 위임 (★ 2026-08-12) ──────────────────
#   태울지 말지의 판정은 촉점(`auto_repair.run_auto_repair_targeted` → `repair_budget`)
#   단독이다. 이 모듈은 그 판정을 **다시 하지 않는다** — 게이트를 흉내내는 순간
#   문이 두 벌이 되고, 그게 지금 고치고 있는 병이다(①).
#   여기서 필요한 것은 딱 하나: "방금 그 호출이 *실제로* SDK 세션을 태웠나?"
#   그래야 ① 사용자에게 '왜 수리를 건너뛰었는지' 를 말해주고 ② 학습 기록에 남긴다.
#   관측 함수도 사본을 만들지 않고 `guardian_agent` 의 한 벌을 쓴다.
def _sdk_ledger_mark() -> str:
    """자율 SDK 수리 장부 표식 — 계산은 guardian_agent(→repair_budget) 한 벌."""
    try:
        from JARVIS07_GUARDIAN.guardian_agent import sdk_repair_ledger_mark
        return sdk_repair_ledger_mark()
    except Exception:
        return ""


def _sdk_session_ran(mark, error_record=None) -> bool:
    """표식 이후 **이 오류에 대해** SDK 세션이 돌았는가 (판정 불가면 True — 보수적).

    ★ 2026-08-12 C-1: 종전은 전역 blocked 증분이라 남의 차단 한 건에 오판했다.
      ③원칙 — 같은 병이 ①경로(여기)에도 있었으므로 함께 고친다.
    """
    try:
        from JARVIS07_GUARDIAN.guardian_agent import sdk_session_ran
        return bool(sdk_session_ran(mark, error_record))
    except Exception:
        return True


def _budget_brief() -> str:
    """예산 현황 한 줄 — 문구의 주인은 게이트(repair_budget). 여기서 조립하지 않는다."""
    try:
        from JARVIS07_GUARDIAN.repair_budget import status_line
        return (status_line() or "").strip()
    except Exception:
        return ""


def _call_retry_fn(fn: Callable) -> bool:
    """retry_fn 호출. True/False 또는 dict(success=...) 모두 정규화."""
    try:
        result = fn()
        if isinstance(result, dict):
            return bool(result.get("success", False))
        return bool(result)
    except Exception as e:
        log.error(f"[Incident] retry_fn 예외: {e}")
        return False


def posting_error_type(error_class: str, recovered: bool, *,
                       repair_gated: bool = False) -> str:
    """발행 실패 분류 + 복구 여부 → 세분화된 error_type (ERRORS [547]).

    `Posting` + PascalCase(분류) + [`Gated`] + (`Recovered`|`Unrecovered`) 로 **파생**.
    `_classify()` 가 돌려주는 값이 늘면 타입도 자동으로 따라온다(원칙②).
      code_bug + 복구            → PostingCodeBugRecovered
      transient + 실패           → PostingTransientUnrecovered
      unknown + 수리차단 + 실패  → PostingUnknownGatedUnrecovered

    ★ `repair_gated` (2026-08-12): 자율 SDK 수리가 **게이트에 막혀 아예 안 돌았다** 는
      뜻. '고치려다 실패' 와 '고칠 기회를 안 준 것' 은 사후 대응이 정반대라 뭉뚱그리면
      안 된다 — 후자가 쌓이면 사람이 손봐야 한다는 신호다(예산·사람개입·지문 상한).
    """
    import re as _re_p
    parts = [x for x in _re_p.split(r"[_\-\s]+", (error_class or "unknown").strip()) if x]
    body = "".join(x[:1].upper() + x[1:] for x in parts)
    gated = "Gated" if repair_gated else ""
    return f"Posting{body}{gated}{'Recovered' if recovered else 'Unrecovered'}"


def respond(
    job_id: str,
    failed_platforms: list[str],
    error_text: str,
    retry_fns: dict[str, Callable],
    theme: str = "",
    returncode: int | None = None,
) -> dict:
    """포스팅 실패 즉각 대응 메인 로직 (블로킹).

    Args:
        job_id: "economic" | "theme"
        failed_platforms: 실패 플랫폼 목록 ["naver", "tistory"] 등
        error_text: 로그·예외 텍스트 (원인 파악용)
        retry_fns: {platform: callable} — 재시도 함수 (실패 플랫폼만)
        theme: 테마주 이름 (theme job 시)
        returncode: 발행 subprocess 종료코드 (있으면 분류 최우선 신호 — freeze 강제종료 판별)

    Returns:
        {"fixed": bool, "retried": list, "succeeded": list}
    """
    label = f"[{job_id}] 플랫폼={failed_platforms}"
    log.info(f"[Incident] ★ 대응 시작: {label}")
    _tg(
        f"🔧 *[GUARDIAN]* {label} 실패 감지\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"자동 수정·재발행 시작 중..."
    )

    error_class = _classify(error_text, returncode)
    log.info(f"[Incident] 오류 분류: {error_class}")
    fix_applied = False
    sdk_gated   = False     # 자율 SDK 수리가 게이트에 막혀 *아예 안 돌았나*

    # ★ 'unknown' 을 여기서 좁히지 않는다 (2026-08-12 재확인 — 계약 결정 그대로).
    #   분류 실패는 *모른다* 는 뜻이지 *코드 버그가 아니다* 라는 뜻이 아니다. 이 튜플이
    #   **아직 타입이 안 붙은 진짜 새 버그** 가 고쳐지는 유일한 통로다(실측: 14일간
    #   PostingUnknownUnrecovered 1건 — 낭비의 주범이 아니다). 낭비는 분류를 좁혀서가
    #   아니라 촉점 게이트(`repair_budget`)가 사람개입·지문상한·쿨다운·예산으로 막는다.
    #   여기에 조건을 하나 더 다는 것이 곧 '문을 두 벌로 만드는' ①원칙 위반이다.
    if error_class in ("code_bug", "unknown"):
        _tg(f"🔍 [GUARDIAN] 오류 분석 중 ({error_class})...")
        error_record = _make_error_record(error_text, job_id)

        # Tier 1: learned_patterns + 정적 패턴 + Contextual Bandit
        fix_applied = _try_fast_fix(error_record, job_id)

        if not fix_applied:
            # Tier 2: Claude Code SDK targeted (Tier 1 실패 시 직행 — ★ 사용자 박제 2026-05-31)
            #   ★ 촉점 게이트가 막을 수 있다. 막히면 이 호출은 LLM 0회로 즉시 False —
            #     '수정 실패' 가 아니라 '시도 자체가 없었음' 이므로 아래에서 구분해 알린다.
            _tg(f"⚙️ [GUARDIAN] Claude Code SDK targeted 수정 시작 (최대 10분)...")
            _ledger_mark = _sdk_ledger_mark()
            fix_applied = _try_sdk_targeted_fix(error_text, job_id, failed_platforms, theme, error_record)
            sdk_gated = (not fix_applied) and (not _sdk_session_ran(_ledger_mark, error_record))
    else:
        # transient: 코드 수정 없이 대기 후 재시도
        _tg(f"⏳ [GUARDIAN] 일시적 오류({error_class}) — {_TRANSIENT_WAIT}초 대기 후 재시도")
        time.sleep(_TRANSIENT_WAIT)

    # ── 재발행 (실패 플랫폼만) ──────────────────────────────────────────
    #   ★ 수리 여부와 **무관하게** 재발행은 항상 돈다. 재시도는 싸고, 수리를 못 했다고
    #     복구까지 포기하면 사람이 손댈 때까지 글이 안 나간다.
    if fix_applied:
        _tg(f"✅ [GUARDIAN] 수정 완료! 재발행 시작: {failed_platforms}")
    elif sdk_gated:
        # 조용히 건너뛰지 않는다 — *왜* 건너뛰었는지가 사람이 손봐야 할 신호다.
        # 정확한 사유·다음 가능 시각은 게이트가 직접 보낸 알림에 있다(사본 금지).
        _brief = _budget_brief()
        _tg(
            f"🛑 [GUARDIAN] 자율 SDK 수리 **건너뜀** (게이트 차단 — 상세 사유는 직전 게이트 알림)\n"
            + (f"{_brief}\n" if _brief else "")
            + f"🔄 재발행은 그대로 진행합니다: {failed_platforms}"
        )
    else:
        _tg(f"🔄 [GUARDIAN] 재발행 시도 (수정 미적용): {failed_platforms}")

    succeeded = []
    for platform, retry_fn in retry_fns.items():
        _tg(f"📤 [GUARDIAN] {platform} 재발행 중...")
        ok = _call_retry_fn(retry_fn)
        if ok:
            succeeded.append(platform)
            _tg(f"✅ [GUARDIAN] {platform} 재발행 성공!")
        else:
            _tg(f"❌ [GUARDIAN] {platform} 재발행 실패")

    # ── 최종 보고 ──────────────────────────────────────────────────────
    failed_after = [p for p in failed_platforms if p not in succeeded]
    status_icon = "🎉" if not failed_after else "⚠️"
    _tg(
        f"{status_icon} *[GUARDIAN] {job_id} 자동 대응 완료*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ 복구 성공: {', '.join(succeeded) if succeeded else '없음'}\n"
        f"❌ 미복구: {', '.join(failed_after) if failed_after else '없음'}\n"
        f"🔧 코드 수정: "
        + ("적용됨" if fix_applied
           else ("없음 — 자율 SDK 수리 차단(게이트), 재시도만" if sdk_gated
                 else "없음 (재시도만)"))
    )

    # ── 수동 수정 기록 (GUARDIAN 자기 작업도 박제 대상) ────────────────
    try:
        from JARVIS07_GUARDIAN.error_collector import report_manual_fix
        report_manual_fix(
            source="incident_responder",
            fixed_file=f"{job_id}_pipeline",
            description=(
                f"포스팅 실패 자동 대응: {failed_platforms} → 복구 {succeeded} | "
                f"코드 수정={'적용' if fix_applied else '없음'}"
                # ★ 차단도 학습 자산이다 (2026-08-12) — '고치려다 실패' 와 '기회를 안 준
                #   것' 을 같은 기록으로 남기면 회고에서 구분이 불가능하다.
                + (" | 자율 SDK 수리=게이트 차단(미실행)" if sdk_gated else "")
            ),
            # ★ 세분화 (ERRORS [547]) — 코드버그/일시적/미상은 대응이 전혀 다르다.
            #   판정은 이미 _classify 가 했다 — 그 결과에서 파생(재분류 금지, 원칙①).
            error_type=posting_error_type(error_class, bool(succeeded),
                                          repair_gated=sdk_gated),
            severity="high",
            actor="guardian",
        )
    except Exception:
        pass

    return {
        "fixed": fix_applied,
        "retried": list(retry_fns.keys()),
        "succeeded": succeeded,
    }


def respond_in_background(
    job_id: str,
    failed_platforms: list[str],
    error_text: str,
    retry_fns: dict[str, Callable],
    theme: str = "",
    returncode: int | None = None,
) -> None:
    """백그라운드 스레드에서 respond() 실행 (호출 즉시 반환).

    이미 대응 중이면 스킵 (중복 실행 방지).
    returncode: 발행 subprocess 종료코드 (freeze 강제종료 판별용 — respond 로 전달).
    """
    if not _active.acquire(blocking=False):
        log.warning("[Incident] 이미 다른 incident 처리 중 — 스킵")
        _tg(
            f"⚠️ [GUARDIAN] {job_id} 대응 요청 수신\n"
            f"이미 진행 중인 incident 있음 — 완료 후 확인 요망"
        )
        return

    def _worker():
        try:
            respond(job_id, failed_platforms, error_text, retry_fns, theme, returncode)
        except Exception as e:
            log.error(f"[Incident] 대응 워커 예외: {e}")
        finally:
            _active.release()

    t = threading.Thread(
        target=_worker,
        name=f"incident_{job_id}",
        daemon=True,
    )
    t.start()
    log.info(f"[Incident] 백그라운드 대응 스레드 시작: incident_{job_id}")


__all__ = ["respond", "respond_in_background"]
