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
- **환경변수**: `ANTHROPIC_API_KEY` setdefault (CrewAI native init 우회용 dummy — SDK subprocess 에는 `""` 오버라이드)
- **단일 진입점**: `shared/llm.py` → `invoke_text(alias, prompt)` (별도 위임 — 본 헌법 대상 외, 인증 자체가 OAuth 라 코드 박제 없음)

---

## 제2조 — 사전 점검 (Layer 1 precondition 위임)

발행 직전 모든 인증 점검:
- `login_manager.verify_all_logins()` — 3 플랫폼 인증 상태 dict 반환
- 한 곳이라도 실패 → `_harness_precondition_check()` (scheduler.py) 가 발행 차단

자동 갱신 (cron 잡):
- `j02_naver_cookie_pre_morning` (06:30) — 네이버 쿠키 만료 임박 시 자동 갱신
- `j02_naver_cookie_pre_afternoon` (15:30) — 네이버 쿠키 만료 임박 시 자동 갱신
- `j02_tistory_cookie_pre_morning` (06:30) — 티스토리 쿠키 만료 임박 시 자동 갱신
- `j02_tistory_cookie_pre_afternoon` (15:30) — 티스토리 쿠키 만료 임박 시 자동 갱신
- 모든 잡은 `login_manager.job_pre_publish_check(platform=...)` 단일 callback 호출

---

## 제3조 — 쿠키 파일 경로 단일 진실 소스

| 플랫폼 | 경로 | 형식 | 비고 |
|--------|------|------|------|
| 네이버 | `JARVIS02_WRITER/naver_cookies.pkl` | pickle dict | legacy anchor — 이동 금지 |
| 티스토리 | 환경변수 `TS_COOKIE` (파일 X) | string | `.env` 저장 |

이 경로들은 `login_manager.NAVER_COOKIE_PATH` / `TS_COOKIE_ENV` 상수로 박제. 다른 곳에서 직접 박제 금지.

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

3. **CAPTCHA / 2FA**:
   - 자동 갱신 불가 — 사용자 수동 개입 필요
   - 텔레그램 알림 — "🔐 {플랫폼} CAPTCHA 감지 — 수동 로그인 필요"
   - 호스트 명령 안내: `python -m JARVIS08_PUBLISH.credentials.login_manager refresh naver --interactive`

---

## 제5조 — 보안 의무

1. **환경변수 보호**:
   - `.env` 절대 git commit 금지 (`.gitignore` 박혀있음 — `precommit_check`)
   - 로그·텔레그램 메시지에 *비밀번호 평문 출력 금지*
   - 쿠키 값 로그 시 *앞 8자 + 마지막 4자* 만 (예: `NID_AUT=eyJ...AbCd`)

2. **subprocess 격리**:
   - Claude Code SDK 호출 시 `ANTHROPIC_API_KEY` env 제거 (`shared/llm.py:377`) — ★ 2026-06-06 표기 통일
   - 다른 외부 subprocess 호출 시 동일 패턴 적용 (필요 시)

3. **쿠키 만료 시 즉시 폐기**:
   - 검증 실패 쿠키는 *즉시 파일/env 제거* 또는 *재발급 강제*
   - 만료 쿠키 잔존 → 발행 중 사고 원인

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
