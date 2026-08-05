"""JARVIS07_GUARDIAN/severity.py — 오류 심각도·성격 분류기 (단일 진입점).

심각도 기준:
  critical — DB 손상 / 데몬 종료 위험 / 자원 고갈 (메모리·재귀)
  high     — 핵심 모듈 ImportError / 데몬 스레드 크래시 / 인증 실패
  medium   — 특정 기능 실패 (블로그 발행 1건 등)
  low      — 경고 수준 / 재시도·환경 복구로 해결 가능

★ 이 파일의 구조 원칙 (2026-07-25 감사 후 재정비 — CLAUDE.md 3원칙)
  ① 단일 진입점 : 분류 어휘(taxonomy)는 파일 맨 위 `_CATEGORY_LABELS` **한 곳**.
      "코드 버그 타입인가" · "강등해도 되는 critical 인가" 는 전부 여기서 *파생*한다.
      환경·외부 사유 정규식도 `_NON_CODE_PATTERNS` 한 곳 — `_LOW_PATTERNS` 는 그것을 파생한다.
      (종전: 두 목록에 24개 토큰이 **양쪽 복사** 되어 있어 한쪽만 고치면 드리프트.)
  ② 동적 설계 : 타입 목록을 손으로 나열하지 않는다. 집합은 기존 집합의 합집합·여집합에서 파생.
      → `_CATEGORY_LABELS` 에 새 타입/새 라벨이 추가되면 판정이 자동으로 따라온다.
  ③ 모든 곳에 적용 : 판정 1순위는 *구조화 필드*(harness `kind`, `source`) — 문자열 문구가 아니다.
      문구로 걸면 네이버만 걸리고 티스토리에서 재발한다(4조합).

★ 회귀 방지: `selfcheck()` — 감사가 실증한 결함 1·2 가 되살아나면 위반 문자열을 반환한다.
  (CLAUDE.md `patch_effective()` 표준 — "코드 존재는 적용의 증거가 아니다".)
"""
from __future__ import annotations

import os
import re


def _flag(name: str, default: bool = True) -> bool:
    """킬스위치 — *호출 시점* 조회(모듈 로드 시 캡처 금지: 복사본을 진실로 믿지 말 것)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


# ══════════════════════════════════════════════════════════════════
# 0. 분류 어휘 (taxonomy) — 이 파일의 단일 진실 소스
# ══════════════════════════════════════════════════════════════════
# error_type(예: "ValueError")만으로는 어떤 종류의 오류인지 한눈에 안 들어옴 →
# ERRORS.md·대시보드 표시 시 "값 불일치(ValueError)" 형태로 보여주기 위한 매핑.
# ★ DB에 별도 컬럼으로 저장하지 않음 — error_type 원본에서 표시 시점에 항상 파생
#   (루트 CLAUDE.md "복사본을 진실로 믿지 말 것" — 분류 기준이 바뀌면 과거 기록도 자동 갱신).
# ★ 2026-07-25 — 파일 맨 위로 이동. 표시용을 넘어 *판정의 근거* 가 되었기 때문:
#   아래 `CODE_BUG_TYPES` / `_DOWNGRADABLE_CRITICAL_TYPES` 가 이 표에서 파생된다.
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

# ★ "코드를 고쳐야 낫는 부류가 *아닌*" 카테고리 — 이것만 손으로 적고,
#   코드버그 카테고리는 **여집합으로 파생**한다(② 동적 설계).
#   → 나중에 `_CATEGORY_LABELS` 에 새 라벨(예: "동시성 오류")이 생기면 자동으로
#     *코드버그* 쪽에 들어간다. 이 방향이 fail-safe 다 —
#     진짜 코드 버그를 ignored 로 버리는 것(결함 1)이 그 반대보다 훨씬 위험하기 때문.
_NON_CODE_CATEGORIES = frozenset({
    "I/O 오류",     # 파일·네트워크 입출력 — 환경 사유가 대부분
    "환경 오류",     # Selenium/Chrome 등 외부 런타임
    "제어흐름",      # StopIteration 등 정상 흐름
    "시스템 종료",   # SystemExit/KeyboardInterrupt — 의도된 종료
    "자원 오류",     # 메모리·재귀 고갈 — 코드 패치가 아니라 자원/설계 문제
    _DEFAULT_CATEGORY,  # 미분류를 코드버그로 단정하지 않는다
})


def _short_type(error_type: str) -> str:
    """'selenium.common.exceptions.WebDriverException' → 'WebDriverException'."""
    return (error_type or "").strip().rsplit(".", 1)[-1]


# ══════════════════════════════════════════════════════════════════
# 1. 심각도별 타입·패턴
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# 2. 환경·외부·제어흐름 정규식 — **캐노니컬 단일 목록** (① 단일 진입점)
# ══════════════════════════════════════════════════════════════════
# ★ 2026-07-25 감사: 이 목록과 `_LOW_PATTERNS` 에 동일 토큰 **24개** 가 양쪽 복사되어
#   있었다(측정치). 한쪽만 고치면 드리프트 → 아래처럼 *한 곳을 진실* 로 삼고 파생시킨다.
#     · `_TRANSIENT_PATTERNS` = 이 목록 그대로            ("코드 버그가 아닌가?")
#     · `_LOW_PATTERNS`       = `_LOW_ONLY_PATTERNS` + 이 목록  ("등급이 낮은가?")
#   두 질문의 답은 같은 신호에서 나온다 — 환경·외부 사유면 코드버그도 아니고 등급도 낮다.
#
# ★ ERRORS [286] 박제 2026-06-28 — 이 부류는 wontfix(코드 결함 미해결)가 아니라 ignored.
#   네트워크·Selenium 환경·외부 API 할당량·정상 제어흐름(테마 교체)·외부 발행(Layer 4)·
#   Claude CLI 운영 오류는 코드 패치로 해결 불가 → 수동검토 큐 오염 방지.
_NON_CODE_PATTERNS = [
    # ── 네트워크 일시 오류 ──
    # (종전 `_LOW_PATTERNS` 의 'connection reset|connection refused' 를 흡수 — 부분집합이었음)
    re.compile(r"max retries|connection (reset|refused|aborted)|remote end closed|connection aborted", re.I),
    # DNS — 종전 `_LOW_PATTERNS` 에 telegram 한정 사본이 따로 있었다(더 좁음) → 넓은 쪽 1벌로 통합
    re.compile(r"failed to resolve|nodename nor servname|NameResolutionError|getaddrinfo", re.I),
    # ── Chrome / Selenium 환경 ──
    # (종전 `_LOW_PATTERNS` 에 거의 동일한 사본이 있었다 → 넓은 쪽 1벌로 통합)
    re.compile(r"ERR_INTERNET_DISCONNECTED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION_|ERR_NETWORK_CHANGED"
               r"|ERR_EMPTY_RESPONSE|ERR_TIMED_OUT|net::ERR_|chrome not reachable|browser has closed"
               r"|timed out receiving message from renderer", re.I),
    # ── 외부 API 할당량·rate limit ── (종전 `_LOW_PATTERNS` 사본 흡수)
    re.compile(r"rate limit|too many requests|hit your limit|resets \d+\s*(am|pm)", re.I),
    # ★ ERRORS [272] 박제 2026-06-08 — Pollinations 402 Queue full (IP 레벨 외부 제한)
    # 코드 버그 아님. 서킷 브레이커 + 폴백으로 graceful 처리됨 → Guardian 수정 불필요.
    re.compile(r"Queue full|Pollinations.*(402|재시도.*실패|일시 오류|비정상 응답)", re.I),
    # Claude CLI 운영 오류 (auto_repair — 코드 버그 아님)
    #   'You've hit your limit' 토큰은 위 rate-limit 행이 이미 덮는다 → 여기서 제거(중복 해소)
    re.compile(r"cli_not_found|CLI 타임아웃|Command failed with exit code|exitcode=-?\d"
               r"|REPAIR-SUMMARY.*(없음|빈 출력)|MessageParseError", re.I),
    # 정상 제어흐름 (데이터 없음 → 테마 교체) · 외부 발행(Layer 4) — 코드 패치 불가
    re.compile(r"종목 데이터 0개|다른 테마로|data_empty", re.I),
    #   종전 `_LOW_PATTERNS` 사본의 고유 토큰 2개(Naver 재시도·InvalidSessionId)를 여기로 흡수
    re.compile(r"\[Layer ?4\]|Layer ?4\)|step=송출|송출 \(Layer|발행 실패|발행 미완료|에디터 상태 유지"
               r"|Naver.*재시도 후에도|InvalidSessionId.*Exception", re.I),
    # ★ ERRORS [260] 박제 2026-06-07 — transient LLM 응답 형식 오류 (코드 버그 아님)
    re.compile(r"\[transient\]|transient_llm_format|LLM 응답.*(빈|JSON 형식 누락)", re.I),
    # harness 운영 보고 — auto-repair 가 이미 시도 후 포기한 메타 보고 (코드 버그 아님)
    re.compile(r"수정 불가.*(패턴 반복|건)|재생성해도 동일 결과", re.I),
    # 콘텐츠·데이터 생성 운영 실패 (재생성·다음 회차에 해소 — 코드 패치 불가)
    re.compile(r"HTML 생성 실패|트렌드 데이터 없음|키워드 .*등장|body 등장|카테고리 검색 실패|BrokenPipeError", re.I),
    # ★ ERRORS [405] 박제 2026-07-11 — topic_pack 생성 실패(트렌드·적합 후보·LLM 미가용)는
    # 코드 버그가 아니라 LLM rate-limit/회로차단으로 인한 일시적 자원 경합(topic_pack._profile_batch
    # 스로틀). Tier2 SDK 낭비 세션이 재시도의 LLM 슬롯과 경합해 재발을 야기하는 자기강화 루프 방지.
    re.compile(r"주제 패키지 없음", re.I),
    # 외부 이미지 모델 API (할당량 소진·모델 폐기 — 외부 제약)
    # ★ 2026-07-25 감사: 벤더 모델명 하드코딩(black-forest-labs|stabilityai|stable-diffusion-|FLUX\.\d)
    #   제거 — ② 위반이자 **죽은 문자열**. 해당 provider 는 ERRORS [263] 로 2026-06-07 삭제됐고
    #   실DB 마지막 적중도 2026-06-07(그 후 48일 0건). 벤더 이름은 늘어나기만 하므로
    #   *증상*(HTTP 코드·크레딧 소진·모델 폐기 문구)만 남긴다.
    re.compile(r"HTTP \d{3} —|depleted your.*credits|requested model.*(does not exist|deprecated)", re.I),
    # native provider 환경 (외부 키·런타임 — 코드 버그 아님)
    #   ※ ImportError 로 올라오면 결함 1 가드가 우선한다(코드버그 타입 > 문구). 의도된 순서 —
    #     '없는 심볼을 import 했다' 는 사실 자체는 코드가 고쳐야 할 일이기 때문.
    re.compile(r"Error importing native provider|OPENAI_API_KEY is required", re.I),
    # ★ ERRORS [387] 박제 2026-07-06 — jarvis_keeper 워치독 hang 감지/복구 알림
    # (ERRORS [318][385] 설계상 정상 동작 — heartbeat stale 시 강제 재시작하는 자가 치유).
    # 재시작 "완료" 보고에는 코드 결함 정보(파일·라인·traceback) 자체가 없어 Tier1/2 가
    # 고칠 대상이 없음 — LLM 낭비 호출 방지. 근본원인은 daemon_faulthandler.log 로 별도 추적.
    re.compile(r"데몬 HANG 감지|데몬 강제 재시작 완료|hang 복구", re.I),
    # ★ ERRORS [413] 박제 2026-07-11 — watchdog 이 killable subprocess 를 freeze/deadline
    # 감지로 os._exit(75) 강제 종료한 "정상 자가치유" 보고(jobs.py _run_script_checked).
    # traceback 은 NoneType — 코드 결함 위치 자체가 없고, 다음 예약 실행이 깨끗하게 재시도한다.
    re.compile(r"워치독 정지\(freeze/deadline\) 감지로 강제 종료", re.I),
    # ★ ERRORS [414] 박제 2026-07-19 — [413]의 non-killable 짝(harness 가 데몬 본체 안에서
    # 도는 경우). Watchdog 이 StuckError 를 던져 harness 가 escalation 한다. traceback 없음 →
    # 근본 원인은 watchdog `_absorb_sleep_gap()`·데드라인 튜닝 영역이라 LLM 낭비 방지.
    re.compile(r"데드라인 초과\(블로킹\)|step=전체:\s*데드라인 초과", re.I),
    # ★ 2026-07-12 — [413]과 동일 클래스의 stderr noise 꼬리(멀티프로세싱 세마포어 누수 경고).
    # os._exit(75) 는 정상 종료 훅을 건너뛰므로 resource_tracker 보고는 강제종료의 *부작용*.
    re.compile(r"실패 \(rc=75\).*resource_tracker.*leaked", re.I | re.S),
    # ★ 2026-07-12 — 구버전 포맷 보고는 stderr 꼬리 노이즈가 매번 다르다. 특정 문자열을 하나씩
    # 추가하는 대신 "rc=75 + watchdog 자체 킬 로그 마커(🛑)" 조합 하나로 일반화.
    re.compile(r"실패 \(rc=75\).*\[watchdog\] 🛑", re.I | re.S),
    # ★ 2026-07-17 — harness `kind="infra_throttle"` 이슈는 harness 가 이미 backoff·deferred 로
    # 처리하는 일시적 인프라 신호(코드 버그 아님).
    re.compile(r"인프라 스로틀", re.I),
    # ★ ERRORS [455] 박제 2026-07-20 — pytrends trending_searches 의 code 404 는 Google 이
    # 엔드포인트를 폐기해서 발생(본 저장소 코드 문제 아님). RSS 폴백이 이미 1순위라 수집 무관.
    re.compile(r"Google returned a response with code 404", re.I),
]

# ★ '등급' 판정에만 쓰는 추가 신호 — transient(코드버그 아님) 판정에는 **일부러 안 쓴다**.
#   · timeout/retry : 너무 넓다. 코드 버그 메시지에도 흔히 섞여 들어와(결함 2의 원인)
#     transient 로 쓰면 오탐이 난다. 등급 강등에만 쓴다.
#   · 포트 충돌     : ERRORS [274] — api_server 가 kill+retry 로 근본 처리. 실DB 메시지
#     적중 0건이라 transient 목록에 둘 실익이 없고(감사 지적 :192 죽은 정규식),
#     반대로 결함 2 의 "SystemExit 포트충돌만 강등" 을 위해 등급 쪽에는 반드시 필요하다.
_LOW_ONLY_PATTERNS = [
    re.compile(r"timeout|timed out", re.I),
    re.compile(r"retry", re.I),
    re.compile(r"address already in use|EADDRINUSE|bind on address", re.I),
]

# ── 파생 (① 단일 진입점: 아래 두 이름은 위 캐노니컬에서 *파생* 된다. 직접 편집 금지) ──
_TRANSIENT_PATTERNS = _NON_CODE_PATTERNS
_LOW_PATTERNS = _LOW_ONLY_PATTERNS + _NON_CODE_PATTERNS


# ══════════════════════════════════════════════════════════════════
# 3. 코드 버그 타입 — 결함 1 가드의 근거 (② 합집합에서 파생)
# ══════════════════════════════════════════════════════════════════

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


# ★ 코드 버그 타입 = "코드를 고쳐야 낫는 타입" — 세 근거의 **합집합에서 파생** (② 동적 설계)
#     ① `_PATTERN_FIXABLE_TYPES`        (패턴 fixer 가 고칠 수 있다고 이미 선언한 타입)
#     ② `DETERMINISTIC_CODE_ERROR_TYPES` (재시도해도 100% 같게 실패한다고 이미 선언한 타입)
#     ③ `_CATEGORY_LABELS` 중 *비*코드 카테고리(`_NON_CODE_CATEGORIES`)가 아닌 타입
#        → KeyError(조회 오류)·IndexError·JSONDecodeError 등이 손 나열 없이 자동 포함된다.
#   ①②③ 어디에 타입이 추가돼도 이 집합이 자동으로 따라온다 — 목록을 두 벌로 만들지 않는다.
CODE_BUG_TYPES = frozenset(
    set(_PATTERN_FIXABLE_TYPES)
    | set(DETERMINISTIC_CODE_ERROR_TYPES)
    | {t for t, label in _CATEGORY_LABELS.items() if label not in _NON_CODE_CATEGORIES}
)


def is_code_bug_type(error_type: str) -> bool:
    """이 error_type 은 '코드를 고쳐야 낫는' 부류인가.

    ★ 공개 API — `incident_responder` 등 다른 모듈이 로컬 목록을 만들지 않고 이걸 쓴다
      (① 단일 진입점). 점 표기 전체이름('json.decoder.JSONDecodeError')도 정규화해 받는다.
    """
    et = (error_type or "").strip()
    if not et:
        return False
    return et in CODE_BUG_TYPES or _short_type(et) in CODE_BUG_TYPES


def is_deterministic_code_error(error_type: str) -> bool:
    """재시도해도 100% 같게 실패하는 코드 오류인가 — 즉시 수리 착수 대상."""
    return (error_type or "") in DETERMINISTIC_CODE_ERROR_TYPES


# ★ '메시지 문구로 강등해도 되는' critical 타입 — `_CRITICAL_TYPES` 에서 **파생** (②)
#
#   결함 2 (2026-07-25 감사 실증):
#     classify('MemoryError',   '... — retry')   → low   ← 심각한데 강등됨
#     classify('RecursionError','... — timeout') → low   ← 동일
#   원인은 `_is_low` 가드가 *어떤 critical 타입이든* 무차별 강등한 것.
#   현업(Rollbar)의 원칙은 "노이즈만 제거하고 유의미한 신호는 보존" 이다.
#
#   그래서 강등 허용을 **'시스템 종료' 카테고리로 좁힌다**:
#     · SystemExit/KeyboardInterrupt = *의도된 종료 신호*. 포트 충돌(ERRORS [274])처럼
#       외부 사유로 나면 진짜 심각도는 낮다 → 강등 정당.
#     · MemoryError/RecursionError   = *자원 고갈*. 메시지에 'retry' 가 섞였다고 덜 심각해지지
#       않는다(오히려 재시도가 원인일 수 있다) → 메시지 무관하게 등급 유지.
#   목록을 손으로 적지 않고 `_CATEGORY_LABELS` 라벨로 파생하므로, 새 critical 타입이
#   추가돼도 그 성격(시스템 종료 vs 자원 고갈)에 따라 자동 분류된다.
_DOWNGRADABLE_CRITICAL_TYPES = frozenset(
    t for t in _CRITICAL_TYPES if _CATEGORY_LABELS.get(t) == "시스템 종료"
)


# ══════════════════════════════════════════════════════════════════
# 4. 등급 판정
# ══════════════════════════════════════════════════════════════════

def classify(
    error_type: str,
    message: str,
    source: str = "",
    module: str = "",
) -> str:
    """오류 심각도 반환: 'critical' | 'high' | 'medium' | 'low'

    판단 순서 (위가 우선):
      1) critical 타입 — 단, `_DOWNGRADABLE_CRITICAL_TYPES` 이고 운영 신호 메시지면 강등
      2) critical 메시지 패턴 (DB 손상 등)
      3) high 타입 — 단, *코드버그 타입이 아니면서* 운영 신호 메시지면 강등
         (OSError '[Errno 48] Address already in use' 를 SystemExit 포트충돌과 같게 다루기 위함.
          감사 지적: 같은 메시지인데 타입만 달라 high/low 로 갈렸다.)
      4) high 메시지 패턴 (인증 실패 등) — 강등 대상이어도 이건 통과시킨다. 인증 실패는
         재시도 문구가 섞여도 여전히 high 다.
      5) low 타입 → low 메시지 패턴 → 소스 보정 → medium

    킬스위치 `GUARDIAN_STRICT_SEVERITY=0` → 1·3 의 좁힌 강등 규칙을 끄고 종전 동작으로 복귀.
    """
    et = (error_type or "").strip()
    short = _short_type(et)
    msg = (message or "").lower()
    strict = _flag("GUARDIAN_STRICT_SEVERITY", True)

    # 운영(환경·외부·제어흐름) 신호가 메시지에 있는가 — 강등 후보 판단용
    _is_low = any(pat.search(msg) for pat in _LOW_PATTERNS)

    # 1) critical
    if et in _CRITICAL_TYPES or short in _CRITICAL_TYPES:
        _downgradable = (
            (short in _DOWNGRADABLE_CRITICAL_TYPES) if strict else True
        )
        if not (_is_low and _downgradable):
            return "critical"
    for pat in _CRITICAL_PATTERNS:
        if pat.search(msg):
            return "critical"

    # 3) high
    if et in _HIGH_TYPES or short in _HIGH_TYPES:
        _downgradable = strict and not is_code_bug_type(et)
        if not (_is_low and _downgradable):
            return "high"
    # 4) high 메시지 패턴 — 강등 경로에서도 인증·데몬 스레드 신호는 살린다
    for pat in _HIGH_PATTERNS:
        if pat.search(msg):
            return "high"

    # 5) low
    if et in _LOW_TYPES or short in _LOW_TYPES:
        return "low"
    if _is_low:
        return "low"

    # 소스별 보정
    if source in ("scheduler",) and "job" in msg:
        return "high"

    return "medium"


# ══════════════════════════════════════════════════════════════════
# 5. transient(자동수정 비대상) 판정
# ══════════════════════════════════════════════════════════════════

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


# ★ 코드 수정으로 해결 *불가* 한 harness 이슈 kind — Tier-2(LLM) 비대상 (ERRORS [475])
#
#   ★ `harness._INFRA_ISSUE_KINDS` 와 **직교하는 다른 질문** 이다. 혼동 금지:
#     · `_INFRA_ISSUE_KINDS` = "재작성으로 고칠 수 있나?"  (harness 재시도·backoff 정책)
#     · 이 집합              = "코드 수정으로 고칠 수 있나?" (GUARDIAN 학습 정책)
#     예) engagement(품질점수 미달)는 *재작성으론* 고쳐지므로 전자에 넣으면 안 되지만,
#         *코드 수정으론* 안 고쳐지므로 후자에는 들어간다.
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
#     · execution_error — 코드에서 실제로 난 예외. 반드시 Tier-2 유지.
#     · draft_invalid / data_empty / send_failure / login_invalid — 미승인(유지).
#
#   ★★ data_insufficient 는 2026-07-25 감사에서 **판단 누락** 이 드러나 승인 (사용자 판단).
#      `prepublish_gate.py` 가 생산하는데 이 집합에도, 위 '의도적 비포함' 목록에도 없었다
#      — 즉 넣을지 뺄지를 *결정한 적이 없는* 상태로 코드버그로 분류돼 Tier-2 를 태우고 있었다.
#      근거는 factuality·engagement 와 동일: 수집 datasets 이 부족한 것은 *데이터* 문제이지
#      코드가 틀린 게 아니다(생산 지점 주석 자신이 "재작성으로 못 고친다" 고 명시).
#      가시성은 유지된다 — 기록은 남고 반복되면 사람이 본다.
#
#   ★★ factuality 는 2026-07-22 '미승인' → 2026-07-25 **승인으로 번복** (사용자 판단).
#      사실성 차단은 *글 내용* 의 문제이지 코드의 문제가 아니다 — engagement 와 같은 부류.
#      (2026-07-25 경제 티스토리 사고: Tier-2 2회 소모 + 'TitleFabrication' 이라는 틀린 교훈을
#       학습 원장에 박제했다. 가시성은 유지된다 — 오류 기록 자체는 남고 반복되면 사람이 본다.)
#
#   ★ 2026-07-25 감사 — 'cli_error' 제거: 리포지토리 전체에서 **생산처 0건**
#     (`grep -rn 'kind="cli_error"'` → severity.py 자신뿐). 죽은 항목은 "덮고 있다" 는
#     착각만 준다. CLI 운영 오류는 ① kind='sdk_error' ② `_NON_CODE_PATTERNS` 의
#     'cli_not_found|CLI 타임아웃|Command failed with exit code' 행이 이미 덮는다.
#     검증: grep -rnoE 'kind\s*=\s*"[a-z_]+"' --include='*.py' . | sort -u
#
#   ★ 2026-07-25 P4 — `"infra_throttle"` 리터럴 제거 (① 위반 시정).
#     이 kind 의 *주인* 은 harness 다 — 같은 커밋이 `harness.INFRA_KIND` 를 SSOT 로 신설하고
#     `__all__` 에 공개했는데도 여기가 문자열을 한 벌 더 적고 있었다. harness 가 이름을
#     바꾸는 순간 이쪽만 옛 이름을 가리켜 게이트가 *조용히* 새는 형태(복사본 드리프트).
#     → 아래 `_harness_infra_kinds()` 로 **런타임 파생**한다(② 동적 설계).
#     여기 남은 6종은 severity 자신의 정책(“코드 수정으로 고칠 수 있나?”)이라 정당한 소유.
_OWN_NON_CODE_KINDS = frozenset({
    "engagement",     # 품질 점수 미달 — 글이 안 좋은 것이지 코드가 틀린 게 아니다
    "factuality",     # 사실성 차단 — 근거 미확인은 *글 내용* 문제 (2026-07-25 승인, 위 주석)
    "data_insufficient",  # 수집 datasets 부족 — *데이터* 문제 (2026-07-25 승인, 위 주석)
    "draft_failed",   # 대본 생성 실패 (LLM 무응답·HTML 생성 실패)
    "empty_output",   # LLM 응답 빈값
    "sdk_error",      # SDK 실행 오류 (CLI 미발견·인증 등 운영 사유)
    "timeout",        # LLM/CLI 타임아웃 — 응답이 안 온 것
    # ★ 2026-08-05 — 발행 회계 kind 2종. 둘 다 *코드로 못 고치는* 사건이다.
    #   등록하지 않으면 절전 한 번마다 Tier-2 LLM 세션이 열린다
    #   (`PublishGap*` 이 실제로 그렇게 됐다 — 자동수리가 고칠 수 없는 것에 토큰을 태웠다).
    "daemon_down",    # 데몬이 꺼져 있어 슬롯을 통째로 잃음 — 기계 상태이지 코드 결함 아님
    "job_missed",     # grace 를 넘겨 잡이 아예 실행되지 못함 (misfire) — 같은 이유
})

# last-known-good 캐시 — *성공한 파생만* 적재한다(실패값을 캐시하면 영구 degrade).
_INFRA_KINDS_CACHE: frozenset = frozenset()


def _harness_infra_kinds() -> frozenset:
    """harness 가 선언한 '인프라 미완결' kind 집합 — `harness.INFRA_KIND` 에서 파생.

    ★ 왜 모듈 로드가 아니라 *지연(호출 시점)* import 인가 — severity 는 최하위 leaf 다.
      severity 를 쓰는 `error_collector` 는 harness 실행 경로 *안* 에서 지연 import 된다
      (`harness.py:439` `report`, `:514` `report_manual_fix`). 여기서 severity 가 모듈
      로드 시점에 harness 를 끌어오면 harness 부분초기화 중 재진입 시 `INFRA_KIND`
      미정의(ImportError)로 severity 자체가 못 뜬다 — GUARDIAN 전체가 죽는 형태.
      지연 조회는 그 창(window)을 아예 만들지 않는다. (킬스위치 `_flag` 와 같은 원칙:
      *호출 시점* 조회 — 모듈 로드 시 캡처 금지.)

    실패 시 fail-open: 마지막 성공값(없으면 빈 집합). 빈 집합이면 `selfcheck()` 의
    [P4] 레그가 위반으로 잡아낸다 — 조용한 열화를 만들지 않는다.
    """
    global _INFRA_KINDS_CACHE
    try:
        from JARVIS00_INFRA.harness import INFRA_KIND  # noqa: PLC0415 (의도된 지연)
        got = frozenset({INFRA_KIND}) if INFRA_KIND else frozenset()
        if got:
            _INFRA_KINDS_CACHE = got
            return got
    except Exception:  # noqa: BLE001 — 파생 실패가 severity 를 죽이면 안 된다
        pass
    return _INFRA_KINDS_CACHE


def _harness_says_infra(kind: str) -> bool:
    """인프라 kind 판별을 **harness 에 위임** (2026-08-04 감사 6위).

    ★ 왜 집합 비교로는 안 되는가
      harness 가 사유별 kind(`infra_throttle_timeout` 등)를 내기 시작하면서, 집합
      등가비교는 그 kind 들을 **코드 결함으로 오분류** 한다 — 자동수리가 고칠 수 없는
      것(서버 스로틀·락 경합)을 붙잡고 LLM 을 태우게 된다.
      판별식을 여기 복제하면(`startswith`) 그게 곧 사본이고, harness 가 규칙을 바꾸는
      순간 또 갈라진다. 그래서 **주인에게 묻는다.**

    지연 import 이유는 `_harness_infra_kinds()` 와 동일(재진입 창 회피). 실패 시 False —
    그러면 위 집합 비교 결과만 쓰이므로 종전 동작으로 안전하게 되돌아간다.
    """
    try:
        from JARVIS00_INFRA.harness import is_infra_kind  # noqa: PLC0415 (의도된 지연)
        return bool(is_infra_kind(kind))
    except Exception:  # noqa: BLE001
        return False


def _env_extra_kinds() -> frozenset:
    """무배포 안전밸브 — `GUARDIAN_EXTRA_NON_CODE_KINDS=a,b` 로 kind 추가(호출 시점 조회).

    파생이 깨졌거나 신규 kind 를 급히 막아야 할 때 *리터럴을 되살리지 않고* 대응하는 통로.
    """
    raw = os.getenv("GUARDIAN_EXTRA_NON_CODE_KINDS") or ""
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def non_code_issue_kinds() -> frozenset:
    """★ 공개 API — 코드 수정 비대상 harness kind 집합(호출 시점 파생, 사본 없음)."""
    return _OWN_NON_CODE_KINDS | _harness_infra_kinds() | _env_extra_kinds()


def __getattr__(name: str):
    """`severity.NON_CODE_ISSUE_KINDS` 하위호환 — *스냅샷이 아니라* 매번 라이브 파생.

    모듈 상수로 두면 그 자체가 사본이 된다(로드 시점 고정 → env 밸브·harness 변경 미반영).
    PEP 562 모듈 `__getattr__` 로 접근 순간 파생하므로 `from ... import NON_CODE_ISSUE_KINDS`
    도 그대로 동작하면서 드리프트가 원천적으로 불가능하다.
    """
    if name == "NON_CODE_ISSUE_KINDS":
        return non_code_issue_kinds()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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

    ★★ 판단 순서와 그 근거 (2026-07-25 감사로 재정비 — 순서 자체가 정책이다)

      1) `kind` (구조화 필드)            → non_code_issue_kinds() 면 True
      2) `source == "audit_test"` (구조화 필드) → True
      3) **코드 버그 타입이면 즉시 False** ← ★ 결함 1 가드
      4) `_TRANSIENT_TYPES` (타입)       → True
      5) 메시지 정규식 `_NON_CODE_PATTERNS` → True

      · 1·2 가 3 보다 앞인 이유: kind·source 는 *생산자가 명시한 구조화 필드* 라
        추론(타입·문구)보다 권위가 높다. engagement 는 error_type 이 무엇이든 코드 버그가
        아니고(글 내용 문제), audit_test 는 애초에 합성 프로브다. 여기서 타입을 앞세우면
        4조합(플랫폼×글종류) 전부에서 게이트가 무너진다.
      · 3 이 4·5 보다 앞인 이유: 메시지는 *오염되기 쉽다*. 코드 버그의 메시지에 환경 문구가
        섞이는 일이 흔하다. 실물 증거(DB #582):
            ImportError: cannot import name 'HuggingFaceProvider' from ...
        이 진짜 코드 버그가 옛 provider 문자열 정규식에 걸려 `ignored` 로 버려졌다.
        타입은 예외 클래스 자체 = 메시지보다 신뢰도가 높은 신호다.

    코드 패치로 해결 불가능한 부류만 True:
      네트워크·Selenium 환경·외부 API 할당량·Claude CLI 운영 오류·
      정상 제어흐름(테마 교체)·외부 발행 실패(Layer 4).
    ImportError/NameError/KeyError/AttributeError/TypeError 같은 *코드 버그 타입은
    절대 transient 로 분류하지 않음* (오탐 방지) — 이제 docstring 이 아니라 3) 이 보장한다.

    킬스위치 `GUARDIAN_CODEBUG_GUARD=0` → 3) 만 비활성화(종전 동작 복귀).
    """
    if kind and (kind in non_code_issue_kinds() or _harness_says_infra(kind)):
        return True   # 1) 코드 수정으로 해결 불가한 harness 이슈 — Tier-2 낭비 차단

    # 2) ★ ERRORS [446][447][448] 박제 2026-07-17 — source="audit_test" 는 GUARDIAN
    # Tier1→Tier2→apply_fix 파이프라인이 실제로 완주하는지 검증하는 합성 자가진단 프로브
    # (traceback 없는 인위 생성 이벤트). 코드 버그가 아니므로 즉시 ignored 처리.
    if (source or "") == "audit_test":
        return True

    et = (error_type or "").strip()
    msg = message or ""

    # 3) ★ 결함 1 가드 — 코드 버그 타입은 메시지 정규식과 무관하게 transient 아님
    if _flag("GUARDIAN_CODEBUG_GUARD", True) and is_code_bug_type(et):
        return False

    # 4) 타입
    if et in _TRANSIENT_TYPES or _short_type(et) in _TRANSIENT_TYPES:
        return True

    # 5) 메시지
    return any(pat.search(msg) for pat in _NON_CODE_PATTERNS)


# ══════════════════════════════════════════════════════════════════
# 6. 표시용 라벨
# ══════════════════════════════════════════════════════════════════

# ★ 도메인 접두사 → 라벨 (ERRORS [548]). `_CATEGORY_LABELS` 는 *파이썬 예외 이름* 표라
#   도메인 타입(HarnessFactuality 등)을 담을 자리가 아니다. 접두사만 등록하면
#   그 아래 세부 타입이 **몇 개 늘든 자동으로** 분류된다 — 타입마다 표에 줄을 추가하는
#   방식은 새 kind 가 생길 때마다 낡는다(원칙②).
#   ※ 접두사는 각 도메인의 파생 함수가 만드는 것과 짝: harness_error_type ·
#     watchdog_error_type · posting_error_type · draft_fix_error_type.
_DOMAIN_PREFIX_LABELS: tuple = (
    ("Harness",  "발행 검증"),      # harness Layer 3 판정 (사실성·매력도·분량·중단 등)
    ("Watchdog", "정지 감지"),      # freeze / 데드라인 초과
    ("Posting",  "발행 대응"),      # 실패 후 자동 대응 결과
    ("DraftFix", "대본 즉시수정"),   # Layer 3 inline 패치
)


def describe_category(error_type: str) -> str:
    """error_type → 한글 분류 라벨. 미등록 타입은 '기타' (판단 실패가 아니라 미분류 표시)."""
    et = (error_type or "").strip()
    if et in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[et]
    short = _short_type(et)  # "selenium.common.exceptions.WebDriverException" 대응
    if short in _CATEGORY_LABELS:
        return _CATEGORY_LABELS[short]
    # 도메인 접두사 파생 — 가장 긴 접두사 우선(겹칠 때 더 구체적인 쪽)
    for pref, label in sorted(_DOMAIN_PREFIX_LABELS, key=lambda x: -len(x[0])):
        if short.startswith(pref) and len(short) > len(pref):
            return label
    # ★ 오류가 아닌 *변경·정책 기록* (GitCommit·ExternalEdit 등) — 목록은 error_collector 소유.
    #   화면에서 "기타" 로 뭉뚱그려지면 오류 975건이 섞여 보인다 — 실제로 그랬다.
    if et in _policy_types() or short in _policy_types():
        return "변경 기록"
    return _DEFAULT_CATEGORY


def format_error_label(error_type: str) -> str:
    """표시용 조합 라벨: '값 불일치(ValueError)'. ERRORS.md·대시보드 공통 사용."""
    et = (error_type or "?").strip() or "?"
    return f"{describe_category(et)}({et})"


# ══════════════════════════════════════════════════════════════════
# 7. 자동 수정 시도 가능 여부
# ══════════════════════════════════════════════════════════════════

def is_auto_fixable(severity: str, error_type: str) -> bool:
    """자동 수정 시도 가능 여부.

    원칙:
      - critical 은 사람 판단 (DB 손상·데몬 종료 등)
      - SystemExit/MemoryError 류는 코드 수정 불가
      - ★ 2026-07-25 추가 — `_TRANSIENT_TYPES`(네트워크·Selenium 환경)도 코드 수정 불가.
        종전엔 is_auto_fixable('high','WebDriverException') 이 **True** 였다(감사 실증).
        같은 파일의 `is_transient()`·`_LOW_TYPES` 가 "환경 오류 = 자동수정 비대상" 이라고
        이미 선언해 놓았는데 이 함수만 그 선언을 안 물려받고 있었다(① 위반).
      - 패턴 기반 fixer 가 처리 가능한 type 은 *severity 무관* 자동 시도
        (high·medium 자동 처리 확대 — '진짜 어려운 거 빼곤 자동' 원칙)
      - 나머지는 high·medium 만 LLM fallback

    ※ 메시지를 받지 않는 시그니처(하위호환 유지)라 *타입 레벨* 판정만 한다.
      메시지까지 보는 완전한 판정은 `is_transient()` 를 함께 쓸 것.
    킬스위치 `GUARDIAN_TRANSIENT_AUTOFIX_GUARD=0` → 환경 타입 차단만 비활성화.
    """
    et = (error_type or "").strip()
    short = _short_type(et)
    if severity == "critical":
        return False
    if et in _CRITICAL_TYPES or short in _CRITICAL_TYPES:
        return False
    if _flag("GUARDIAN_TRANSIENT_AUTOFIX_GUARD", True) and (
        et in _TRANSIENT_TYPES or short in _TRANSIENT_TYPES
    ):
        return False
    # 패턴 기반 fixer 가 처리 가능한 type 은 high 도 자동 시도
    if et in _PATTERN_FIXABLE_TYPES or short in _PATTERN_FIXABLE_TYPES:
        return True
    return severity in ("high", "medium")


# ══════════════════════════════════════════════════════════════════
# 8. 자기검증 스모크 — 회귀 방지 (CLAUDE.md `patch_effective()` 표준)
# ══════════════════════════════════════════════════════════════════

def selfcheck() -> list[str]:
    """가짜 입력을 *실제 판정 함수* 에 통과시켜 결함 1·2 재발을 잡는다.

    반환: 위반 문자열 리스트. **빈 리스트 = 정상**.
    "코드 존재는 적용의 증거가 아니다" — 상수를 읽어 비교하지 않고, 소비자가 부르는 것과
    똑같은 공개 함수(`is_transient`/`classify`/`is_auto_fixable`)를 실제로 호출해 판정한다.

    호출: python3 -c "from JARVIS07_GUARDIAN.severity import selfcheck; print(selfcheck())"
    """
    bad: list[str] = []

    # ── 결함 1: 코드버그 타입 + transient 유발 메시지 → 절대 True 이면 안 됨 ──
    # 메시지는 실제로 오탐을 냈던 문구들(실DB #582 포함)
    lures = [
        "connection reset by peer",
        "max retries exceeded",
        "rate limit",
        "timed out receiving message from renderer",
        "data_empty — 다른 테마로",
        "발행 실패",
        "cannot import name 'HuggingFaceProvider'",   # 실DB #582 재현
        "[transient] LLM 응답 빈 값",
    ]
    probes = ["ImportError", "NameError", "SyntaxError", "KeyError",
              "TypeError", "AttributeError", "ValueError", "ModuleNotFoundError"]
    for et in probes:
        for m in lures:
            if is_transient(et, f"{et}: something — {m}"):
                bad.append(f"[결함1] is_transient({et!r}, ...{m!r}) == True — 코드버그가 ignored 로 버려진다")
                break
    if not is_code_bug_type("json.decoder.JSONDecodeError"):
        bad.append("[결함1] is_code_bug_type 이 점 표기 전체이름을 정규화하지 못한다")

    # ── 회귀: 구조화 필드(kind/source)는 타입보다 우선 — 여전히 True 여야 ──
    for k in sorted(non_code_issue_kinds()):
        if not is_transient("ImportError", "cannot import name X", kind=k):
            bad.append(f"[회귀] kind={k!r} 인데 transient 가 아니다 — kind 우선순위가 깨졌다")

    # ── P4: infra kind 는 harness 에서 *파생* 되는가 (리터럴 부활·조용한 파생실패 검출) ──
    try:
        from JARVIS00_INFRA.harness import INFRA_KIND as _hk
    except Exception as _e:  # noqa: BLE001
        bad.append(f"[P4] harness.INFRA_KIND 를 읽을 수 없다({_e}) — 파생 원본 소실")
    else:
        if _hk not in non_code_issue_kinds():
            bad.append(f"[P4] harness.INFRA_KIND={_hk!r} 가 non_code_issue_kinds() 에 없다 — 파생 실패")
        if _hk in _OWN_NON_CODE_KINDS:
            bad.append(f"[P4] {_hk!r} 리터럴이 severity 에 부활했다 — ① 단일 진입점 위반")
        if not is_transient("ImportError", "cannot import name X", kind=_hk):
            bad.append(f"[P4] kind={_hk!r} 가 transient 가 아니다 — 인프라 스로틀이 Tier-2 로 샌다")
    if not is_transient("ImportError", "cannot import name X", source="audit_test"):
        bad.append("[회귀] source='audit_test' 합성 프로브가 transient 가 아니다")

    # ── 회귀: 진짜 환경 오류는 여전히 transient 여야 ──
    for et, m in (
        ("ConnectionError", "max retries exceeded"),
        ("WebDriverException", "chrome not reachable"),
        ("", "종목 데이터 0개 — 다른 테마로"),
        ("Exception", "[Layer4] 발행 실패"),
        ("", "인프라 스로틀"),
    ):
        if not is_transient(et, m):
            bad.append(f"[회귀] 환경 오류 is_transient({et!r}, {m!r}) == False — 수동검토 큐 오염")

    # ── 결함 2: 자원 고갈 critical 은 메시지 문구로 강등되면 안 됨 ──
    for et in ("MemoryError", "RecursionError"):
        for m in ("메모리 부족 — retry", "maximum recursion depth — timeout"):
            got = classify(et, m)
            if got != "critical":
                bad.append(f"[결함2] classify({et!r}, {m!r}) == {got!r} — 자원 고갈이 강등됐다")
    # 의도된 강등은 유지 (포트 충돌 — ERRORS [274])
    if classify("SystemExit", "address already in use") != "low":
        bad.append("[결함2] 포트충돌 SystemExit 강등이 사라졌다 — 알림 폭주 재발")
    # 같은 메시지면 타입이 달라도 같은 등급 (감사 지적: OSError 만 high 로 갈렸다)
    if classify("OSError", "[Errno 48] Address already in use") != "low":
        bad.append("[결함2] OSError 포트충돌이 SystemExit 과 다른 등급 — 메시지 동일한데 불일치")

    # ── 결함 3: 파생 무결성 (①단일 진입점이 실제로 파생인지) ──
    if _TRANSIENT_PATTERNS is not _NON_CODE_PATTERNS:
        bad.append("[결함3] _TRANSIENT_PATTERNS 가 캐노니컬에서 파생되지 않는다(사본 부활)")
    if not all(p in _LOW_PATTERNS for p in _NON_CODE_PATTERNS):
        bad.append("[결함3] _LOW_PATTERNS 가 캐노니컬을 포함하지 않는다(드리프트)")
    if not is_code_bug_type("KeyError"):
        bad.append("[결함3] CODE_BUG_TYPES 파생이 _CATEGORY_LABELS 를 반영하지 못한다")

    # ── is_auto_fixable: 환경 타입은 자동수정 비대상 ──
    if is_auto_fixable("high", "WebDriverException"):
        bad.append("[결함3] is_auto_fixable(high, WebDriverException) == True — 환경 오류에 LLM 낭비")

    # ── 결함4: 오류 타입이 뭉뚱그려져 있지 않은가 (사용자 박제 2026-07-29) ──
    bad.extend(type_granularity_issues())

    return bad


# ── 오류 타입 세분화 감시 (사용자 박제 2026-07-29 — ERRORS [547]) ────────
#
# **규정: 오류는 세분화해서 기록·매칭·보고한다.**
#   "RuntimeError" 처럼 뭉뚱그린 타입은 기록만 보고 무슨 오류인지 알 수 없고,
#   타입 기반 게이트(_PATTERN_FIXABLE_TYPES·_TRANSIENT_TYPES·DETERMINISTIC_CODE_ERROR_TYPES)
#   와 Tier 1 지문 매칭이 **전부 무력화**된다. 실측(2026-07-29): 전체 4,506건 중
#   타입만으로 분류 가능한 것이 **7.1%** 뿐이었다.
#
# ★ 왜 "금지 타입 목록" 을 만들지 않는가 (원칙②): 목록은 낡는다. 대신 **데이터에서 파생**한다 —
#   *한 소스가 충분히 많은 오류를 내면서 고유 타입이 1개* 면 그 타입은 변별력이 0이다.
#   새 소스가 생겨도 자동으로 검사 대상이 된다.
#
# ★ 면제: `error_collector._MANUAL_POLICY_TYPES` (GitCommit·ExternalEdit 등)는
#   *오류가 아니라 변경·정책 기록* 이라 단일 타입이 정상이다. 그 목록도 파생한다(사본 금지).

GRANULARITY_MIN_SAMPLES = 20      # 이 미만은 표본 부족 — 판정 보류
# ★ 최근 N일만 본다 (ERRORS [547]) — 과거 데이터로는 절대 안 꺼진다.
#   이 감시는 *지금 코드가 뭉뚱그려 기록하고 있는가* 를 묻는다. 이미 쌓인 옛 기록까지
#   세면 코드를 고쳐도 알람이 영구히 울리고, 그러면 진짜 신호가 왔을 때 아무도 안 본다.
GRANULARITY_WINDOW_DAYS = 14


def _policy_types() -> frozenset:
    """오류가 아닌 '기록' 타입 — error_collector 단독 소유. 사본을 만들지 않는다."""
    try:
        from JARVIS07_GUARDIAN.error_collector import _MANUAL_POLICY_TYPES  # noqa: PLC0415
        return frozenset(_MANUAL_POLICY_TYPES)
    except Exception:
        return frozenset()


def type_granularity_issues(min_samples: int = GRANULARITY_MIN_SAMPLES,
                            window_days: int = GRANULARITY_WINDOW_DAYS) -> list:
    """소스별로 오류 타입이 뭉뚱그려져 있으면 알린다. DB 미가용 시 조용히 [].

    판정: 최근 `window_days` 일 안에서 한 소스가 `min_samples` 이상 오류를 냈는데
          **고유 타입이 1개** → 세분화 필요.
    """
    out: list = []
    try:
        from shared.db import get_db  # noqa: PLC0415
        policy = _policy_types()
        with get_db() as conn:
            rows = conn.execute(
                "SELECT source, COUNT(*) n, COUNT(DISTINCT error_type) d, "
                "       MIN(error_type) t FROM error_log "
                "WHERE source IS NOT NULL AND source <> '' "
                "  AND ts_ok GROUP BY source".replace(
                    "ts_ok",
                    f"timestamp >= datetime('now','localtime','-{int(window_days)} day')")
            ).fetchall()
        for r in rows:
            src, n, d, t = r[0], int(r[1] or 0), int(r[2] or 0), str(r[3] or "")
            if n < min_samples or d != 1 or t in policy:
                continue
            out.append(f"[결함4] source='{src}' {n}건이 전부 '{t}' 한 타입 — "
                       f"타입만으로 무슨 오류인지 알 수 없다(세분화 필요)")
    except Exception:
        pass          # 진단이 본 판정을 막지 않는다 (fail-open)
    return out


if __name__ == "__main__":  # pragma: no cover — 수동 점검용 (읽기 전용·외부 영향 없음)
    # `python3 JARVIS07_GUARDIAN/severity.py` 로 직접 실행하면 sys.path[0] 이 *이 폴더* 라
    # 저장소 루트가 안 잡힌다 → `JARVIS00_INFRA.harness` 파생 원본을 못 읽어 P4 레그가
    # 위양성(false positive)을 낸다. 실행 방식 때문에 검사가 틀리면 안 되므로 루트를 붙인다.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

    _v = selfcheck()
    print("severity.selfcheck():", "OK (위반 0)" if not _v else f"위반 {len(_v)}건")
    for _x in _v:
        print("  -", _x)
