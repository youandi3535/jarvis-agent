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


# ── 재시도해도 절대 낫지 않는 '결정론적' 오류 타입 (ERRORS [478]) ──────────
#
# ★ `_PATTERN_FIXABLE_TYPES` 와 **다른 질문** 이다. 혼동 금지:
#     · `_PATTERN_FIXABLE_TYPES` = "패턴으로 고칠 수 있나?"   (fixer 선택)
#     · 이 집합                  = "재시도해도 안 낫나?"      (수리 착수 시점)
#   그래서 TypeError·AttributeError·ValueError 는 전자에는 있지만 **여기엔 없다** —
#   `None` 이 와서 나는 경우가 많고, 그건 데이터가 아직 안 온 것이라 재시도하면 낫는다.
#   반대로 여기 있는 것들은 *환경·코드가 바뀌지 않는 한 100% 같게 실패* 한다.
#
# 용도: 재시도가 남은 '잠정' 실패라도 이 타입이면 Tier-1(패턴 수정, LLM 0회)을 *즉시* 허용.
#   기다려봐야 똑같이 실패하므로, 다음 시도가 살아나려면 지금 고쳐야 한다.
#   (Tier-2(LLM)는 이 타입이어도 여전히 재시도 종료까지 보류 — 비싸기 때문.)
DETERMINISTIC_CODE_ERROR_TYPES = frozenset({
    "SyntaxError",          # 문법 오류 — 코드를 고치지 않는 한 영원히 동일
    "IndentationError",     # 들여쓰기 오류 — 동일
    "TabError",
    "ImportError",          # 심볼 부재 — 재시도로 생기지 않음
    "ModuleNotFoundError",  # 모듈 부재 — 동일
    "NameError",            # 정의되지 않은 이름(오타) — 동일
})


def is_deterministic_code_error(error_type: str) -> bool:
    """재시도해도 100% 같게 실패하는 코드 오류인가 — 즉시 수리 착수 대상."""
    return (error_type or "") in DETERMINISTIC_CODE_ERROR_TYPES


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
    # ★ ERRORS [414] 박제 2026-07-19 — [413]의 killable-subprocess 짝인 non-killable
    # 컨텍스트(harness 가 데몬 본체 안에서 도는 경우, is_killable_subprocess()=False) 보고.
    # Watchdog 이 os._exit 대신 StuckError 를 던져 harness 가 "[harness:이름] attempt=N
    # step=전체: 데드라인 초과(블로킹)/데드라인 초과 Xs > Ys" 형태로 escalation 한다. traceback 은
    # 항상 NoneType(Watchdog 이 직접 생성) — 코드 결함 위치가 없어 Tier1/2 가 고칠 대상이 없다.
    # 근본 원인(macOS 절전으로 인한 elapsed 오산)은 watchdog.py `_absorb_sleep_gap()` 이 이미
    # 흡수하며, 잔여 재발은 데드라인 자체 상향([414] 트렌드 수집 5400s)으로 대응 — 둘 다 코드가
    # 아니라 운영 튜닝 영역이라 Tier2 낭비 호출 방지 목적으로 여기서 일반화.
    re.compile(r"데드라인 초과\(블로킹\)|step=전체:\s*데드라인 초과", re.I),
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
    # ★ ERRORS [455] 박제 2026-07-20 — pytrends trending_searches 가 code 404 를 반환하는
    # 것은 Google 이 해당 엔드포인트 자체를 폐기해서 발생 (본 저장소 코드 문제 아님, 코드로
    # 고칠 수 없음). google_collector 는 이미 RSS 를 1순위 폴백으로 두고 있어 실제 수집에는
    # 무관 — is_transient 미분류로 매 발생마다 GUARDIAN 이 헛다리 수정 세션을 반복하는 것 방지.
    re.compile(r"Google returned a response with code 404", re.I),
]


# ★ 코드 수정으로 해결 *불가* 한 harness 이슈 kind — Tier-2(LLM) 비대상 (ERRORS [475])
#
#   ★ `harness._INFRA_ISSUE_KINDS` 와 **직교하는 다른 질문** 이다. 혼동 금지:
#     · `_INFRA_ISSUE_KINDS` = "재작성으로 고칠 수 있나?"  (harness 재시도·backoff 정책)
#     · 이 집합              = "코드 수정으로 고칠 수 있나?" (GUARDIAN 학습 정책)
#     예) engagement(품질점수 미달)는 *재작성으론* 고쳐지므로 전자에 넣으면 안 되지만,
#         *코드 수정으론* 안 고쳐지므로 후자에는 들어간다.
#     (실제로 harness 주석이 "engagement 를 _INFRA_ISSUE_KINDS 에 넣지 말라" 고 못박고 있다 —
#      그래서 그 목록을 넓히는 방식은 틀렸고, 이렇게 별도 개념으로 둔다.)
#
#   ★ 왜 message 정규식이 아니라 kind 인가 (CLAUDE.md 3원칙 ③ '모든 글에 적용'):
#     kind 는 구조화된 필드라 네이버·티스토리 / 경제·테마 4조합에 자동으로 동일 적용된다.
#     메시지 문자열로 걸면 한 플랫폼 문구만 걸러지고 다른 쪽에서 재발한다.
#
#   실측 근거 (2026-07-22): Tier-2 가 시도한 131건 중 74건(56%)이 harness 래퍼 오류였고,
#   전부 files_fixed=0. 누적 3.8시간 낭비 + 그 시간 동안 발행이 LLM 을 못 씀.
#
#   ★ 의도적 *비*포함 (사용자 판단 2026-07-22 — 안(다)):
#     · stuck / abort (데드라인·freeze) — 반복되면 진짜 성능 결함일 수 있다.
#       게다가 2026-07-18 이후 발생 0건이라 지금 막을 실익이 없다. 늘어나면 그때 재검토.
#     · execution_error — 코드에서 실제로 난 예외. 반드시 Tier-2 유지.
#     · draft_invalid / data_empty / send_failure / login_invalid / factuality — 미승인.
NON_CODE_ISSUE_KINDS = frozenset({
    "engagement",     # 품질 점수 미달 — 글이 안 좋은 것이지 코드가 틀린 게 아니다
    "infra_throttle", # 스로틀·락 경합 — 순번이 밀린 것
    "draft_failed",   # 대본 생성 실패 (LLM 무응답·HTML 생성 실패)
    "empty_output",   # LLM 응답 빈값
    "sdk_error",      # SDK 실행 오류 (CLI 미발견·인증 등 운영 사유)
    "cli_error",      # CLI 오류 (한도 초과 등)
    "timeout",        # LLM/CLI 타임아웃 — 응답이 안 온 것
})


def kind_of(record: dict) -> str:
    """오류 레코드에서 harness 이슈 kind 추출 — context(JSON) 단일 경로."""
    if not isinstance(record, dict):
        return ""
    ctx = record.get("context")
    if isinstance(ctx, str):
        try:
            import json as _json
            ctx = _json.loads(ctx)
        except Exception:
            return ""
    if isinstance(ctx, dict):
        return str(ctx.get("kind") or "")
    return ""


def is_transient(error_type: str, message: str = "", source: str = "",
                 kind: str = "") -> bool:
    """일시적·외부·제어흐름 오류 여부 — True 면 자동수정 비대상(ignored 처리).

    ★ kind (ERRORS [475]): harness 이슈 kind 가 `NON_CODE_ISSUE_KINDS` 면 즉시 True.
      메시지 정규식보다 정확하고, 구조화 필드라 4조합(플랫폼×글종류)에 자동 적용된다.

    코드 패치로 해결 불가능한 부류만 True:
      네트워크·Selenium 환경·외부 API 할당량·포트 충돌·Claude CLI 운영 오류·
      정상 제어흐름(테마 교체)·외부 발행 실패(Layer 4).
    ImportError/NameError/KeyError/AttributeError/TypeError 같은 *코드 버그 타입은
    절대 transient 로 분류하지 않음* (오탐 방지).
    """
    if kind and kind in NON_CODE_ISSUE_KINDS:
        return True   # ★ 코드 수정으로 해결 불가한 harness 이슈 — Tier-2 낭비 차단
    et = error_type or ""
    msg = message or ""
    # ★ ERRORS [446][447][448] 박제 2026-07-17 — source="audit_test" 는 GUARDIAN
    # Tier1→Tier2→apply_fix 파이프라인이 실제로 완주하는지 검증하는 합성 자가진단 프로브
    # (traceback 없는 인위 생성 이벤트, 리포지토리 내 실사용처 0건 확인됨). 코드 버그가 아니므로
    # Tier1/2 낭비 분석·Telegram "자동수정 실패" 알림 없이 즉시 ignored 처리.
    if (source or "") == "audit_test":
        return True
    if et in _TRANSIENT_TYPES:
        return True
    return any(pat.search(msg) for pat in _TRANSIENT_PATTERNS)


# ── 표시용 한글 분류 라벨 (사용자 박제 2026-07-21) ─────────────────
# error_type(예: "ValueError")만으로는 어떤 종류의 오류인지 한눈에 안 들어옴 →
# ERRORS.md·대시보드 표시 시 "값 불일치(ValueError)" 형태로 보여주기 위한 매핑.
# ★ DB에 별도 컬럼으로 저장하지 않음 — error_type 원본에서 표시 시점에 항상 파생
#   (루트 CLAUDE.md "복사본을 진실로 믿지 말 것" — 분류 기준이 바뀌면 과거 기록도 자동 갱신).
_CATEGORY_LABELS: dict[str, str] = {
    "AttributeError": "참조 오류",
    "KeyError": "조회 오류",
    "IndexError": "조회 오류",
    "LookupError": "조회 오류",
    "TypeError": "타입 불일치",
    "ValueError": "값 불일치",
    "UnicodeDecodeError": "값 불일치",
    "UnicodeEncodeError": "값 불일치",
    "JSONDecodeError": "값 불일치",
    "ZeroDivisionError": "연산 오류",
    "OverflowError": "연산 오류",
    "ArithmeticError": "연산 오류",
    "FileNotFoundError": "I/O 오류",
    "IsADirectoryError": "I/O 오류",
    "NotADirectoryError": "I/O 오류",
    "PermissionError": "I/O 오류",
    "OSError": "I/O 오류",
    "IOError": "I/O 오류",
    "ConnectionError": "I/O 오류",
    "ConnectionResetError": "I/O 오류",
    "ConnectionAbortedError": "I/O 오류",
    "TimeoutError": "I/O 오류",
    "HTTPError": "I/O 오류",
    "SSLError": "I/O 오류",
    "MemoryError": "자원 오류",
    "RecursionError": "자원 오류",
    "ImportError": "임포트 오류",
    "ModuleNotFoundError": "임포트 오류",
    "NameError": "이름 오류",
    "UnboundLocalError": "이름 오류",
    "StopIteration": "제어흐름",
    "GeneratorExit": "제어흐름",
    "SystemExit": "시스템 종료",
    "KeyboardInterrupt": "시스템 종료",
    "WebDriverException": "환경 오류",
    "SessionNotCreatedException": "환경 오류",
    "InvalidSessionIdException": "환경 오류",
    "StaleElementReferenceException": "환경 오류",
    "ElementClickInterceptedException": "환경 오류",
    "NoSuchWindowException": "환경 오류",
}

_DEFAULT_CATEGORY = "기타"


def describe_category(error_type: str) -> str:
    """error_type → 한글 분류 라벨. 미등록 타입은 '기타' (판단 실패가 아니라 미분류 표시)."""
    et = (error_type or "").strip()
    if et in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[et]
    short = et.rsplit(".", 1)[-1]  # "selenium.common.exceptions.WebDriverException" 대응
    return _CATEGORY_LABELS.get(short, _DEFAULT_CATEGORY)


def format_error_label(error_type: str) -> str:
    """표시용 조합 라벨: '값 불일치(ValueError)'. ERRORS.md·대시보드 공통 사용."""
    et = (error_type or "?").strip() or "?"
    return f"{describe_category(et)}({et})"


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
