# JARVIS08 로그인·인증 최상위 헌법

> **★★★ 단일 진입점 원칙 — 영구·불변 (사용자 박제 2026-05-17, ERRORS [145])**
>
> ### 사용자 원문 박제
>
> > *"로그인 관련 모든 규정은 이 파일에서만 관리된다. 혹시나 언제든 다른 파일에서 로그인 관련 규정이 발견되면, 발견 즉시 이 파일로 이관 + 그 위치는 호출 형태로 교체한다."*
>
> 이 원칙은 *영구*. 시간·작업자·세션 무관 *항상* 적용. precommit `auth` 카테고리가 *매 커밋·매 부팅·매 잡 실행* 자동 검증.
>
> ## 허용 호출 형태 (★ 외부 코드는 이것만 사용)
>
>   1. `JARVIS08_PUBLISH.credentials.login_manager.*` — *모든 로그인 진입점*
>      - `get_naver_cookies()` — 네이버 쿠키 dict (selenium add_cookie 용)
>      - `get_tistory_cookie()` — 티스토리 TS_COOKIE 환경변수
>      - `verify_all_logins()` — 2 플랫폼 인증 상태 일괄 점검
>      - `refresh_naver_cookies(force=False)` — 네이버 쿠키 갱신
>      - `refresh_tistory_cookies(force=False)` — 티스토리 쿠키 갱신
>      - `auto_refresh_if_needed()` — 만료 임박 시 자동 갱신
>      - `job_pre_publish_check()` — cron 잡 진입점 (발행 직전 사전 점검)
>      - `network_up()` — 인터넷 도달 가능 여부 (로그인 시도 전 전제 판정)
>      - `ensure_naver_ready(deadline=)` — 네이버 쿠키를 발행 가능 상태로. *일시적 실패에
>        한해* `deadline` 까지만 재시도 (제2-B조)
>      - **로그인 상태기 (플랫폼 중립 — 제2-C조)**: `mark_login_backoff(platform, reason)` /
>        `clear_login_backoff(platform)` / `login_backoff_reason(platform)` /
>        `login_backoff_active_reason(platform)` / `current_login_failure_reason(platform)` /
>        `human_required_reasons(platform)` / `login_error_type(platform, reason)` /
>        `login_invalid_kind(reason)` / `is_human_required_login_kind(kind)` /
>        `alert_human_login_needed(platform, reason, shot="")` / `human_action_hint(platform, reason)` /
>        `unblock_hint(platform)` / `recovery_command(platform)` / `human_wait_sec()` /
>        `captcha_present(driver)` / `human_challenge_present(driver)` /
>        `capture_login_stuck(driver, platform, tag="")` / `platforms()`
>
> ## 금지 (다른 파일 박제 금지 — 발견 시 즉시 이관)
>
>   - 환경변수 *직접* 참조 (`os.environ['NV_PASSWORD']` 등) — `login_manager` 위임
>   - 쿠키 파일 경로 *하드코딩* — `login_manager` 내부 상수만
>   - 로그인 URL 박제 — `login_manager` 내부만
>   - Selenium 로그인 sequence 본문 — credentials/*_cookie_refresher.py 안 단독
> >
> ## 이관 의무
>
>   - 다른 폴더 (JARVIS02_WRITER, shared, jarvis_main 등) 에서 로그인 관련 코드 발견 시 *즉시* `login_manager.py` 로 이관.
>   - 이관 후 호출자는 `from JARVIS08_PUBLISH.credentials.login_manager import ...` 만.
>   - precommit `auth` 카테고리가 자동 검증.

---

## 제1조 — 플랫폼별 인증 방식

### 1.1 네이버 블로그
- **방식**: 쿠키 파일 (`naver_cookies.pkl`) — Selenium 으로 수동 로그인 후 저장
- **환경변수**: `NV_URL` / `NV_USERNAME` / `NV_PASSWORD`
- **쿠키 파일**: `JARVIS02_WRITER/naver_cookies.pkl` (legacy anchor — 이동 금지)
- **핵심 쿠키**: `NID_AUT`, `NID_SES` (둘 다 필수)
- **만료**: 약 10시간 (사용자 활동 시 연장)
- **갱신 절차**: `refresh_naver_cookies()` — Chrome 자동 로그인 → CAPTCHA 시 사용자 개입 → 쿠키 저장
- **단일 진입점**:
  - `login_manager.get_naver_cookies()` — 쿠키 dict 반환 (selenium 호환)
  - `login_manager.refresh_naver_cookies(force=False)` — 갱신 트리거

### 1.3 티스토리
- **방식**: 환경변수 `TS_COOKIE` — Selenium 으로 갱신 후 `.env` 에 저장
- **환경변수**: `TS_URL` / `TS_USERNAME` / `TS_PASSWORD` / `TS_COOKIE`
- **쿠키 파일**: *없음* — 환경변수 방식
- **핵심**: 단일 쿠키 문자열 (전체 세션)
- **만료**: 약 1주일 (Kakao 보안 정책)
- **갱신 절차**: `refresh_tistory_cookies()` — Chrome 자동 로그인 → 쿠키 추출 → `.env` 갱신
- **단일 진입점**:
  - `login_manager.get_tistory_cookie()` — TS_COOKIE 문자열 반환
  - `login_manager.refresh_tistory_cookies(force=False)` — 갱신 트리거

### 1.4 Claude Code SDK (LLM 인증)
- **방식**: OAuth — Anthropic Max 구독 (외부 API 비용 0)
- **환경변수**: `ANTHROPIC_API_KEY` setdefault (LangChain 프로바이더 감지용 dummy — SDK subprocess 에는 `""` 오버라이드로 OAuth 모드 강제)
- **단일 진입점**: `shared/llm.py` → `invoke_text(alias, prompt)` (별도 위임 — 본 헌법 대상 외, 인증 자체가 OAuth 라 코드 박제 없음)

---

## 제2조 — 사전 점검 (Layer 1 precondition 위임)

발행 직전 모든 인증 점검:
- `login_manager.verify_all_logins()` — 플랫폼 인증 상태 dict 반환
  (플랫폼 목록은 `_REQUIRED_ENV` 에서 파생 = `login_manager.platforms()`. 개수를 문서에
  적지 않는다 — 종전 "3 플랫폼" 표기는 실제 2개와 어긋난 채 방치돼 있었다.)
- 한 곳이라도 실패 → `_harness_precondition_check()` (scheduler.py) 가 발행 차단

자동 갱신 (cron 잡) — ★ **발행 시각에서 파생** (2026-07-25 정정):
- 잡 ID: `j08_cookie_precheck_{발행잡ID}` — 발행 잡마다 1개씩 자동 생성
  · `j08_cookie_precheck_j01_economic_post` — 경제 발행(07:00) 30분 전 = 06:30
  · `j08_cookie_precheck_j01_theme_post_21` — 테마 발행(21:00) 30분 전 = 20:30
- 생성 위치: `JARVIS04_SCHEDULER/job_registry._build_cookie_precheck_jobs()`
  발행 cron 에서 `_COOKIE_PRECHECK_LEAD_MIN`(30분)을 빼서 파생 — **시각을 박지 않는다**.
  발행 시각을 옮기면 쿠키 점검도 자동으로 따라 이동한다.
- 모든 잡은 `login_manager.job_pre_publish_check()` 단일 callback 호출 (플랫폼 전체 일괄)

> ★ 2026-07-25 이전 이 문서는 `j02_*_pre_morning/afternoon`(06:30·15:30) 4개를 규정했으나
> **DEFAULT_JOBS 에 실제로는 하나도 등록돼 있지 않았고** `job_pre_publish_check` 는 호출자 0인
> 죽은 함수였다(문서가 진실이고 코드가 따라오지 않은 드리프트). 게다가 15:30 은 옛 16시 발행
> 기준이라 살아 있었어도 21:00 발행과 어긋났다. 지금은 발행 시각에서 파생하므로 드리프트 불가.

---

## 제2-B조 — 인증 실패의 두 종류와 재시도 경계 (★ 사용자 승인 2026-07-25)

**"네트워크가 끊겨서 못 한 것"과 "사람이 필요해서 못 한 것"은 다른 사건이다.**

| 판정 | 조건 | 행동 |
|------|------|------|
| **일시적** | 쿠키 점검 실패 **AND** `network_up()` == False | 창 안에서 재시도 (`COOKIE_RETRY_WAIT_SEC`, 기본 180초) |
| **영구적** | 쿠키 점검 실패 **AND** `network_up()` == True | 재시도 0 — 즉시 사용자 호출 (CAPTCHA·계정) |

- **판정 단일 진입점**: `login_manager.ensure_naver_ready(deadline=)`. 호출자는 판정 로직을
  복제하지 말 것. 종전 `_is_network_up()` 은 네이버·티스토리 refresher 양쪽에 **글자까지
  같은 사본 2벌**이었고, 지금은 `login_manager.network_up()` 단독이다.
- **★ 재시도 창 = 호출자가 넘긴 `deadline` 뿐. 로그인 계층이 시각을 만들지 않는다.**
  호출자(`JARVIS02_WRITER/scheduler._naver_cookie_ready`)가 **그 잡 자신의 misfire
  유예시간**에서 파생해 넘긴다 (`job_registry.job_window_deadline`). 07:00 잡 → 08:00,
  21:00 잡 → 22:00. **`deadline=None`이면 기다리지 않는다** — 창을 모르는 채로 미루는 것이
  곧 시간외 발행이기 때문(사용자 박제 "발행은 07시와 21시뿐").
- **왜 이 조항이 생겼나**: 2026-07-25 21:05, 네트워크 단절 순간 쿠키 점검이 한 번 실패했고
  그걸로 그날 테마글이 통째로 사라졌다. 두 실패를 구분하지 않아 *몇 분 뒤면 됐을 일* 이
  *사람이 필요한 일* 과 같은 취급을 받았다.

---

## 제2-C조 — 로그인 상태기는 **플랫폼 중립 한 벌** (★ 사용자 지시 2026-08-13, ③원칙)

**"네이버 전용 API 를 티스토리에 복사하지 마라. 플랫폼 중립으로 *승격* 하고 양쪽이 그 한 벌을 쓰라."**

### 왜 생겼나 (실측)
백오프·사람 호출·실패 사유·캡차 판정 6종이 **`naver_cookie_refresher` 안에만** 있었다.
그래서 티스토리는 쿠키가 만료돼 있어도 *사람에게 갈 전용 경로가 0곳* 이었다 —
`verify_all_logins()` 는 `ok=False` 를 알고 있었는데 **그 다음이 없었다.**
사본을 만들어 대칭을 맞추면 다음 사고가 한쪽에서만 고쳐진다(`network_up()` 이 두 refresher 에
글자까지 같이 복사돼 있던 것과 같은 병). 그래서 **복사가 아니라 승격**이다.

### 경계 — 무엇이 중립이고 무엇이 플랫폼 것인가

| 층 | 소유자 | 내용 |
|----|--------|------|
| **상태기(중립)** | `login_manager.py` | 백오프 상태·사람 호출·실패 사유 조회·kind/타입 파생·안내 문구·대기 예산·캡차/추가인증 *꼴* 판정·정지 화면 캡처 |
| **플랫폼 고유** | `{platform}_cookie_refresher.py` | 로그인 시퀀스, 쿠키 형식·경로·유효성 판정, `HUMAN_REQUIRED_REASONS`, `_LAST_FAILURE`/`_fail()`, `--manual` 진입점 |

### 플랫폼 모듈 규약 (★ 어휘 레지스트리 금지 — ②)
`login_manager` 는 플랫폼→모듈 매핑표를 갖지 않는다. **이름 규약**으로 찾는다:

- 모듈 경로: `JARVIS08_PUBLISH.credentials.{platform}_cookie_refresher`
- 플랫폼 목록: `_REQUIRED_ENV` 파생 (`platforms()`)
- 필수 노출 심볼: `HUMAN_REQUIRED_REASONS` (frozenset) · `last_login_failure() -> str`
- 권장: `--manual` CLI (`recovery_command()` 가 안내하는 문이 실제로 존재해야 한다)

새 플랫폼은 규약대로 모듈을 만들고 `_REQUIRED_ENV` 에 한 줄 추가하면 **자동으로** 백오프·
사람 호출·타입 파생·안내 문구에 연결된다. 상태기를 고칠 필요가 없다.

### kind 에 플랫폼을 넣지 않는다
`login_invalid_{reason}` 형태 유지. kind 의 용도는 "사람이 필요한가" 하나뿐이고 플랫폼은 이미
`Issue.detail` 에 있다. 넣으면 `severity` 가 플랫폼×사유 곱집합을 알아야 한다.
**대신 사유 어휘가 플랫폼 간 충돌하지 않을 것** — `human_required_reasons()` 합집합 테스트가 강제.

### 판정은 *낱말* 이 아니라 *꼴* 로 (②)
`_HUMAN_INTERVENTION_KEYWORDS`("인증번호·보안문자·captcha·2단계…") 같은 어휘 나열은 **금지**.
낱말 판정은 이미 한 번 무너졌다 — 캡차가 *없는* 평상시 네이버 로그인 페이지(19,620자)에
`captcha` 7회·`보안` 2회가 들어 있어 판정이 **항상 참**이었다(ERRORS [595]).
카카오·네이버가 문구를 바꾸면 새 거부문이 그대로 통과하고, 목록은 낡는다.
`autocomplete='one-time-code'` · `inputmode='numeric'` · `maxlength`(4~8) · QR 요소처럼
**접근성·모바일 키보드 요구에서 오는 속성**으로 판정한다 (`human_challenge_present()`).
반환은 **`True`(확실) / `None`(모름)** 3-상태 — `False`(아님)를 단정하지 않는다.

### 해제 조건을 문장에 박제한다 (★ 사용자 지시 2026-08-13)
백오프 안내는 "자동 재시도 N시간 보류" 에서 **끝나면 안 된다.** 그 문장만 받은 사용자는
"6시간 기다리면 되나" 로 읽고 실제로 기다린다. 해제 주체는 시간이 아니라
`clear_login_backoff()` — 즉 **성공한 로그인**이다.
그 문장의 단일 소유자는 `unblock_hint(platform)` 이고, 로그(`login_backoff_reason`)와
텔레그램(`alert_human_login_needed`·`human_action_hint`)이 **같은 한 문장**을 쓴다.
소비처에 사유별 dict(`{"captcha_unattended": …}`)를 두지 말 것 — 새 사유(`backoff`)가
매칭에 실패해 **안내문이 통째로 누락**된 실사고가 있다(2026-08-13 07:00, `사유: backoff` 한 줄).
분기는 어휘가 아니라 `human_required_reasons()` **집합 소속**으로 한다.

---

## 제2-D조 — 쿠키 **지속성** 은 경보이지 게이트가 아니다 (★ 2026-08-13)

**"지금 발행할 수 있는가"(현재형)와 "브라우저를 닫아도 살아 있는가"(미래형)는 다른 질문이다.**

| 함수 | 질문 | 성격 |
|------|------|------|
| `naver_cookie_refresher.has_publish_auth(cookies)` | 지금 이 묶음으로 발행 문이 열리는가 | **게이트** — 이름 존재 판정 단독 |
| `naver_cookie_refresher.auth_persistence(cookies)` | 종료 후에도 살아 있을 것인가 | **경보** — 어떤 분기 조건에도 등장하지 않는다 |

- `has_publish_auth()` 에 지속성을 **얹지 않는다.** 실측(2026-08-13) `NID_AUT`/`NID_SES` 는
  100% 세션 쿠키(`expiry` 없음)다. 조이는 순간 `check_cookie_valid` → `cookie_valid_http` →
  `verify_all_logins` → harness precondition → `_naver_cookie_ready` 가 **연쇄로 False** 가 되어
  네이버 경제·테마가 둘 다 서고, 복구 경로(refresh)는 캡차·백오프로 막혀 있어 자력 복귀조차 불가능하다.
- `verify_all_logins()` 는 지속성을 **`issues` 에 넣지 않는다.** 별도 optional 키
  (`cookie_durable: bool|None` · `session_only: tuple`)로만 싣는다. `issues` 에 넣으면 위와 같은 결과.
- `durable is None` 은 **판정 불가**다. '모름' 을 '아님' 으로 단정하지 않는다
  (`captcha_present()`·`cookie_valid_http()` 와 같은 3-상태 계약).
- 알림은 `login_manager._advise_persistence()` 단독. GUARDIAN 티켓을 내지 않는다
  (코드 결함이 아니라 계정·기기 신뢰 상태). 쿠키 파일 mtime 으로 dedupe —
  같은 묶음으로 두 번 말하지 않는다(알림 피로는 진짜 경보를 죽인다).

---

## 제3조 — 쿠키 파일 경로 단일 진실 소스

| 플랫폼 | 경로 | 형식 | 비고 |
|--------|------|------|------|
| 네이버 | `JARVIS02_WRITER/naver_cookies.pkl` | pickle dict | legacy anchor — 이동 금지 |
| 티스토리 | 환경변수 `TS_COOKIE` (파일 X) | string | `.env` 저장 |

이 경로들은 `login_manager.NAVER_COOKIE_PATH` / `TS_COOKIE_ENV` 상수로 박제. 다른 곳에서 직접 박제 금지.

### 3.1 상태 파일 (쿠키가 아니라 *로그인 상태*)

| 파일 | 소유자 | 내용 |
|------|--------|------|
| `JARVIS08_PUBLISH/credentials/login_backoff.json` | `login_manager._read_backoff`/`_write_backoff` | **플랫폼 키** dict — `{platform: {reason, at, until, alerted}}` |
| `JARVIS08_PUBLISH/credentials/cookie_watch.json` | `login_manager.record_cookie_sighting` | 네이버 쿠키 파일 관측 이력 + 지속성 안내 dedupe 스탬프 |

- **파일을 플랫폼마다 늘리지 않는다** — 상태의 *종류* 가 하나다. 늘리면 경로 파생이 두 벌이 되고
  새 플랫폼마다 또 는다. 읽기/쓰기는 위 두 함수 **단독**.
- 저장은 `JARVIS07_GUARDIAN.json_store.write_json(..., backup=True)` **정문**(원자 교체·락) +
  직후 `chmod 0600`. `json.dumps` 를 별칭으로 감싸 `symmetry/json-atomic` 검사를 우회하지 말 것.
- 구 *평면* 스키마(`{"reason":…,"until":…}` — 네이버 전용)는 `_read_backoff()` 가 `{"naver": d}`
  로 해석해 흡수한다. **2026-09 이후 삭제 가능.** 이 3줄이 없으면 백오프 창 한복판에 배포될 때
  창이 조용히 풀려 *캡차를 다시 부른다*.

---

## 제4조 — 인증 실패 시 행동

1. **Layer 1 precondition 실패 (발행 전)**:
   - 발행 자체 차단 (scheduler.py `_harness_precondition_check`)
   - GUARDIAN report (`source="harness"`, layer=1)
   - 텔레그램 알림 — "⚠️ {플랫폼} 인증 실패 — 발행 차단"

2. **발행 중 인증 실패** (예: 세션 만료):
   - `login_manager.auto_refresh_if_needed()` 즉시 호출
   - 갱신 성공 → 재시도
   - 갱신 실패 → 해당 플랫폼만 skip (다른 플랫폼은 발행 진행)
   - GUARDIAN report

3. **CAPTCHA / 2FA / 기기인증** (= `human_required_reasons(platform)` 소속 사유):
   - 자동 갱신 불가 — 사용자 수동 개입 필요. **무인이면 기다리지 않는다**(`human_wait_sec()` = 0).
   - `_fail(reason)` 한 곳에서 `mark_login_backoff()` + `alert_human_login_needed()` 처리 (양 플랫폼 동형).
   - 텔레그램 알림은 **해제 조건까지** 말한다 — `unblock_hint()` + `recovery_command()`.
   - 복구 명령은 **모듈 `__file__` 에서 파생**한다 (`recovery_command(platform)`).
     ★ 종전 이 자리에 적혀 있던
     `python -m …login_manager refresh naver --interactive` 는 **존재하지 않는 명령**이었다
     (`--interactive` 플래그 없음). 문서에 경로·플래그를 박으면 이렇게 조용히 거짓이 된다.
   - 재시도는 백오프 창 동안 하지 않는다 — 반복 실패가 캡차·추가인증을 *더* 부른다(ERRORS [615]).

---

## 제5조 — 보안 의무

1. **환경변수 보호**:
   - `.env` 절대 git commit 금지 (`.gitignore` 박혀있음 — `precommit_check`)
   - 로그·텔레그램 메시지에 *비밀번호 평문 출력 금지*
   - 쿠키 값 로그 시 *앞 8자 + 마지막 4자* 만 (예: `NID_AUT=eyJ...AbCd`)

2. **subprocess 격리**:
   - Claude Code SDK 호출 시 `ANTHROPIC_API_KEY` env 제거 (`shared/llm.py:377`) — ★ 2026-06-06 표기 통일
   - 다른 외부 subprocess 호출 시 동일 패턴 적용 (필요 시)

3. **쿠키 만료 시 — ★ 파일을 지우지 말고 *재발급* 하라** (2026-08-11 정정, ERRORS [615]):
   - 종전 이 조항은 "즉시 파일/env 제거" 였는데 그것은 **해롭고, 지금은 금지돼 있다**
     (`precommit_check --category auth` 의 `auth/cookie-delete` 가 차단).
   - 삭제는 갱신을 앞당기지 않는다 — 만료여도 `cookie_needs_refresh()` 는 이미 True 다.
     대신 발행 precondition 과 *복구 재료*(어떤 쿠키가 있었는가)를 없애 자력 복귀를 막는다.
   - 올바른 행동: `refresh_*_cookies()` 로 **재발급**. 사람이 필요하면 제4조 3항.

---

## 제6조 — 호환 정책

- 옛 호출자 (`from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import refresh_naver_cookies`) 는 *호환 alias* 유지
- 신규 코드는 `login_manager` 사용 권장
- 점진 마이그레이션 — 외부 영향 없는 변경

---

## 부속 — 다른 작업자 의무

신규 로그인·인증·쿠키 코드 추가 시 *반드시* 이 파일 먼저 검토.
- 신규 플랫폼 추가: 제1조에 새 항목 + `login_manager` API 추가
- 신규 환경변수: 제3조 표 갱신 + `_check_env_vars` 보강
- 신규 cron 잡: 제2조 표 갱신 + `job_registry.py` DEFAULT_JOBS 추가

precommit `auth` 카테고리가 *자동 검증*:
- 외부 파일의 `os.environ['NV_PASSWORD'|'TS_COOKIE'|...]` 직접 참조
- 쿠키 파일 경로 하드코딩
- `_auth_headers` 같은 함수 정의 외부 잔존
