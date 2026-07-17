"""JARVIS07_GUARDIAN/severity.py — 오류 심각도 분류기.

심각도 기준:
  critical — DB 손상 / 데몬 종료 위험 / 핵심 공유 모듈 파괴
  high     — 핵심 모듈 ImportError / 데몬 스레드 크래시 / 인증 실패
  medium   — 특정 기능 실패 (블로그 발행 1건 등)
  low      — 경고 수준 / 재시도로 해결 가능
"""
from __future__ import annotations

import re

# ── 심각도별 패턴 ─────────────────────────────────────────────

_CRITICAL_TYPES = frozenset({
    "SystemExit", "KeyboardInterrupt",
    "MemoryError", "RecursionError",
})

_CRITICAL_PATTERNS = [
    re.compile(r"database disk image is malformed", re.I),
    re.compile(r"unable to open database", re.I),
    re.compile(r"jarvis\.sqlite.*locked", re.I),
    re.compile(r"daemon.*shutting down", re.I),
]

_HIGH_TYPES = frozenset({
    "ImportError", "ModuleNotFoundError",
    "PermissionError", "OSError",
})

_HIGH_PATTERNS = [
    re.compile(r"(shared|jarvis_daemon).*import", re.I),
    re.compile(r"no module named", re.I),
    re.compile(r"authentication.*failed|token.*invalid|api.?key", re.I),
    re.compile(r"thread.*crashed|daemon thread", re.I),
]

_LOW_TYPES = frozenset({
    "TimeoutError", "ConnectionError", "HTTPError",
    "StopIteration", "GeneratorExit",
    # ★ ERRORS [285] 박제 2026-06-27 — Selenium/Chrome 네트워크 오류는 코드 버그 아님
    # WebDriverException 은 환경 오류(인터넷 끊김·DNS·Chrome 충돌) → 자동 수정 불가 분류
    "WebDriverException", "selenium.common.exceptions.WebDriverException",
})

_LOW_PATTERNS = [
    re.compile(r"timeout|timed out", re.I),
    re.compile(r"connection reset|connection refused", re.I),
    re.compile(r"rate limit|too many requests", re.I),
    re.compile(r"retry", re.I),
    # ★ ERRORS [260] 박제 2026-06-07 — transient LLM 응답 형식 오류 (코드 버그 아님)
    re.compile(r"\[transient\]|transient_llm_format|LLM 응답.*(빈|JSON 형식 누락)", re.I),
    # ★ ERRORS [265] 박제 2026-06-07 — 폐기된 provider 잔존 호출 (Bing/HuggingFace 삭제됨)
    # 데몬 미재시작 시 옛 모듈 메모리 잔존 → 호출 → 인증/네트워크 실패 → 알림 폭주 방지
    re.compile(r"Bing 인증 실패|BingProvider|HuggingFaceProvider|api-inference\.huggingface|_U 쿠키 만료", re.I),
    # ★ ERRORS [272] 박제 2026-06-08 — Pollinations 402 Queue full (IP 레벨 외부 제한)
    # 코드 버그 아님. 서킷 브레이커 + matplotlib 폴백으로 graceful 처리됨 → Guardian 수정 불필요.
    re.compile(r"Queue full for IP|Pollinations.*Queue full|Pollinations.*402|queue full.*max.*1", re.I),
    # ★ ERRORS [274] 박제 2026-06-08 — 포트 충돌 (데몬 재시작 시 이전 프로세스 잔존)
    # api_server.py 에서 kill+retry 로 근본 처리됨 → Guardian 수정 불필요.
    re.compile(r"address already in use|EADDRINUSE|bind on address", re.I),
    # ★ ERRORS [274] — Wi-Fi 미연결 상태 데몬 기동 시 텔레그램 DNS 오류 (일시적)
    re.compile(r"Failed to resolve.*telegram|nodename nor servname|NameResolutionError.*telegram", re.I),
    # ★ ERRORS [279] 박제 2026-06-08 — harness Layer4 발행 실패 (Selenium 런타임 — 코드 패치 불가)
    # verify 버그로 발행 성공인데 실패 판정 → 근본 수정은 _verify_naver_published 개선 (ERRORS [279])
    # harness escalation → Guardian 수정 시도 → 수정 불가 → "자동 수정 실패" 알림 폭주 방지.
    re.compile(r"\[Layer4\].*발행 실패|harness.*발행 실패|발행 미완료|에디터 상태 유지|Naver.*재시도 후에도|InvalidSessionId.*Exception", re.I),
    # ★ ERRORS [285] 박제 2026-06-27 — Chrome/Selenium 네트워크 환경 오류 (코드 수정 불가)
    # ERR_INTERNET_DISCONNECTED : 인터넷 연결 없음 — 코드 패치로 해결 불가, 재연결 필요
    # ERR_NAME_NOT_RESOLVED     : DNS 실패
    # ERR_CONNECTION_REFUSED    : 서버/네트워크 거부
    # ERR_NETWORK_CHANGED       : 네트워크 전환 (Wi-Fi ↔ 이더넷 등)
    # chrome not reachable      : Chrome 프로세스 비정상 종료
    re.compile(
        r"ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_REFUSED"
        r"|ERR_NETWORK_CHANGED|ERR_EMPTY_RESPONSE|ERR_TIMED_OUT"
        r"|chrome not reachable|net::ERR_|browser has closed",
        re.I,
    ),
]


def classify(
    error_type: str,
    message: str,
    source: str = "",
    module: str = "",
) -> str:
    """오류 심각도 반환: 'critical' | 'high' | 'medium' | 'low'"""
    et = error_type or ""
    msg = (message or "").lower()

    # critical (단, _LOW_PATTERNS 매칭 시 제외 — 포트 충돌 SystemExit 등)
    _is_low = any(pat.search(msg) for pat in _LOW_PATTERNS)
    if not _is_low and et in _CRITICAL_TYPES:
        return "critical"
    for pat in _CRITICAL_PATTERNS:
        if pat.search(msg):
            return "critical"

    # high
    if et in _HIGH_TYPES:
        return "high"
    for pat in _HIGH_PATTERNS:
        if pat.search(msg):
            return "high"

    # low
    if et in _LOW_TYPES:
        return "low"
    for pat in _LOW_PATTERNS:
        if pat.search(msg):
            return "low"

    # 소스별 보정
    if source in ("scheduler",) and "job" in msg:
        return "high"

    return "medium"


# 패턴 기반 fixer 가 명확히 처리 가능한 error_type
# pattern_fixer.py 의 6종 패턴과 일치 — 자동 시도 우선
# ★ 사용자 박제 2026-05-16 — ValueError 추가 (ERRORS [111]) — tuple unpack mismatch 자동 fix
_PATTERN_FIXABLE_TYPES = frozenset({
    "ModuleNotFoundError",  # 상대 import → 절대 import 자동 변환
    "ImportError",          # cannot import name → 유사 심볼 자동 교정
    "TypeError",            # NoneType subscriptable → (x or "")[:N]
    "NameError",            # 오타 → 유사 식별자 교정
    "AttributeError",       # NoneType has no attribute → None 가드 삽입
    "ValueError",           # ★ NEW 2026-05-16 — tuple unpack mismatch (3→5 같은 시그니처 변경)
})


# ── 일시적·외부·제어흐름 오류 (코드 버그 아님 — 자동수정 비대상 → ignored) ──
# ★ ERRORS [286] 박제 2026-06-28 — 이 부류는 wontfix(코드 결함 미해결)가 아니라 ignored.
#   네트워크·Selenium 환경·외부 API 할당량·정상 제어흐름(테마 교체)·외부 발행(Layer 4)·
#   Claude CLI 운영 오류는 코드 패치로 해결 불가 → 수동검토 큐 오염 방지.
_TRANSIENT_TYPES = frozenset({
    # 네트워크
    "ConnectionError", "ConnectionResetError", "ConnectionAbortedError",
    "TimeoutError", "TimeoutException", "ReadTimeout", "ReadTimeoutError",
    "HTTPError", "MaxRetryError", "NewConnectionError", "ProtocolError",
    "ChunkedEncodingError", "SSLError", "RemoteDisconnected",
    # Selenium / Chrome 환경
    "WebDriverException", "SessionNotCreatedException", "InvalidSessionIdException",
    "StaleElementReferenceException", "ElementClickInterceptedException",
    "NoSuchWindowException",
})

_TRANSIENT_PATTERNS = [
    # 네트워크 일시 오류
    re.compile(r"max retries|connection (reset|refused|aborted)|remote end closed|connection aborted", re.I),
    re.compile(r"failed to resolve|nodename nor servname|NameResolutionError|getaddrinfo", re.I),
    # Chrome / Selenium 환경
    re.compile(r"ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_|ERR_NETWORK_CHANGED"
               r"|ERR_EMPTY_RESPONSE|ERR_TIMED_OUT|net::ERR_|chrome not reachable|browser has closed"
               r"|timed out receiving message from renderer", re.I),
    # 외부 API 할당량·rate limit
    re.compile(r"rate limit|too many requests|hit your limit|resets \d+\s*(am|pm)", re.I),
    re.compile(r"Queue full|Pollinations.*(402|재시도.*실패|일시 오류|비정상 응답)", re.I),
    re.compile(r"Bing 인증 실패|BingProvider|HuggingFaceProvider|api-inference\.huggingface", re.I),
    # 포트 충돌 (데몬 재시작)
    re.compile(r"address already in use|EADDRINUSE|bind on address", re.I),
    # Claude CLI 운영 오류 (auto_repair — 코드 버그 아님)
    re.compile(r"cli_not_found|CLI 타임아웃|Command failed with exit code|exitcode=-?\d"
               r"|REPAIR-SUMMARY.*(없음|빈 출력)|MessageParseError|You've hit your limit", re.I),
    # 정상 제어흐름 (데이터 없음 → 테마 교체) · 외부 발행(Layer 4) — 코드 패치 불가
    re.compile(r"종목 데이터 0개|다른 테마로|data_empty", re.I),
    re.compile(r"\[Layer ?4\]|Layer ?4\)|step=송출|송출 \(Layer|발행 실패|발행 미완료|에디터 상태 유지", re.I),
    # transient LLM 응답 형식
    re.compile(r"\[transient\]|transient_llm_format|LLM 응답.*(빈|JSON 형식 누락)", re.I),
    # harness 운영 보고 — auto-repair 가 이미 시도 후 포기한 메타 보고 (코드 버그 아님)
    re.compile(r"수정 불가.*(패턴 반복|건)|재생성해도 동일 결과", re.I),
    # 콘텐츠·데이터 생성 운영 실패 (재생성·다음 회차에 해소 — 코드 패치 불가)
    re.compile(r"HTML 생성 실패|트렌드 데이터 없음|키워드 .*등장|body 등장|카테고리 검색 실패|BrokenPipeError", re.I),
    # ★ ERRORS [405] 박제 2026-07-11 — topic_pack 생성 실패(트렌드·적합 후보·LLM 미가용)는
    # 코드 버그가 아니라 LLM rate-limit/회로차단으로 인한 일시적 자원 경합(topic_pack._profile_batch
    # 스로틀). Tier2 SDK 낭비 세션이 재시도의 LLM 슬롯과 경합해 재발을 야기하는 자기강화 루프 방지.
    re.compile(r"주제 패키지 없음", re.I),
    # 외부 이미지 모델 API (HuggingFace 폐기 모델·할당량 소진 — 외부 제약)
    re.compile(r"HTTP \d{3} —|depleted your.*credits|requested model.*(does not exist|deprecated)"
               r"|black-forest-labs|stabilityai|stable-diffusion-|FLUX\.\d", re.I),
    # crewai/native provider 환경 (외부 키·런타임 — 코드 버그 아님)
    re.compile(r"Error importing native provider|OPENAI_API_KEY is required", re.I),
    # ★ ERRORS [387] 박제 2026-07-06 — jarvis_keeper 워치독 hang 감지/복구 알림
    # (ERRORS [318][385] 설계상 정상 동작 — heartbeat stale 시 강제 재시작하는 자가 치유).
    # 재시작 "완료" 보고에는 코드 결함 정보(파일·라인·traceback) 자체가 없어 Tier1/2 가
    # 고칠 대상이 없음 — Sonnet 5 낭비 호출 방지. hang 의 근본원인은 daemon_faulthandler.log 로 별도 추적.
    re.compile(r"데몬 HANG 감지|데몬 강제 재시작 완료|hang 복구", re.I),
    # ★ ERRORS [413] 박제 2026-07-11 — [213]/[396]과 동일 클래스: watchdog 이 killable
    # subprocess(트렌드·성과 수집 등)를 freeze/deadline 감지로 os._exit(75) 강제 종료한
    # "정상 자가치유" 보고(jobs.py _run_script_checked). traceback 은 NoneType — 코드 결함
    # 위치 정보 자체가 없어 Tier1/2 가 고칠 대상이 없고, 다음 예약 실행이 깨끗하게 재시도한다.
    re.compile(r"워치독 정지\(freeze/deadline\) 감지로 강제 종료", re.I),
    # ★ 2026-07-12 — [413]과 동일 클래스의 다른 stderr noise 꼬리(멀티프로세싱 세마포어
    # 누수 경고). os._exit(75) 는 정상 인터프리터 종료 훅(atexit/multiprocessing cleanup)을
    # 건너뛰므로 resource_tracker 가 "leaked semaphore" 를 보고하는 건 강제종료의 *부작용*이지
    # 원인이 아니다. EX_TEMPFAIL 문구가 없는 구버전 in-memory 프로세스가 만든 보고(일반
    # branch 텍스트)도 rc=75 + resource_tracker 조합만으로 동일하게 transient 판별.
    re.compile(r"실패 \(rc=75\).*resource_tracker.*leaked", re.I | re.S),
    # ★ 2026-07-12 — [403][413][415] 동일 클래스의 일반화. jobs.py 의 EX_TEMPFAIL
    # 재분류(★[413])가 배포되기 *이전* 코드로 실행 중이던 프로세스(데몬 미재시작)가 만든
    # 구버전 포맷 보고는 stderr 꼬리 노이즈가 매번 다를 수 있다(RequestsDependencyWarning·
    # resource_tracker 등 이번까지 최소 2종 확인). 특정 노이즈 문자열을 하나씩 추가하는 대신
    # "rc=75 + watchdog 자체 킬 로그 마커(🛑)가 함께 있음" 하나로 일반화 — 이 조합이 존재하면
    # stderr 꼬리 내용과 무관하게 watchdog 강제종료가 원인임이 확정적이다.
    re.compile(r"실패 \(rc=75\).*\[watchdog\] 🛑", re.I | re.S),
    # ★ 2026-07-17 — harness `kind="infra_throttle"` 이슈(JARVIS00_INFRA/harness.py
    # `_INFRA_ISSUE_KINDS`)는 harness 자체가 이미 backoff·deferred(다음 회차 재시도)로
    # 처리하는 일시적 인프라 신호다(코드 버그 아님). 메시지 패턴을 여기서도 인식하지 않으면
    # [405]/[406]과 동일하게 GUARDIAN 이 Tier2 SDK 세션을 낭비해 harness.py 를 잘못 "수정"할
    # 위험이 있다.
    re.compile(r"인프라 스로틀", re.I),
]


def is_transient(error_type: str, message: str = "", source: str = "") -> bool:
    """일시적·외부·제어흐름 오류 여부 — True 면 자동수정 비대상(ignored 처리).

    코드 패치로 해결 불가능한 부류만 True:
      네트워크·Selenium 환경·외부 API 할당량·포트 충돌·Claude CLI 운영 오류·
      정상 제어흐름(테마 교체)·외부 발행 실패(Layer 4).
    ImportError/NameError/KeyError/AttributeError/TypeError 같은 *코드 버그 타입은
    절대 transient 로 분류하지 않음* (오탐 방지).
    """
    et = error_type or ""
    msg = message or ""
    if et in _TRANSIENT_TYPES:
        return True
    return any(pat.search(msg) for pat in _TRANSIENT_PATTERNS)


def is_auto_fixable(severity: str, error_type: str) -> bool:
    """자동 수정 시도 가능 여부.

    원칙:
      - critical 은 사람 판단 (DB 손상·데몬 종료 등)
      - SystemExit/MemoryError 류는 코드 수정 불가
      - 패턴 기반 fixer 가 처리 가능한 type 은 *severity 무관* 자동 시도
        (high·medium 자동 처리 확대 — '진짜 어려운 거 빼곤 자동' 원칙)
      - 나머지는 medium 만 LLM fallback
    """
    if severity == "critical":
        return False
    if error_type in _CRITICAL_TYPES:
        return False
    # 패턴 기반 fixer 가 처리 가능한 type 은 high 도 자동 시도
    if error_type in _PATTERN_FIXABLE_TYPES:
        return True
    return severity in ("high", "medium")
