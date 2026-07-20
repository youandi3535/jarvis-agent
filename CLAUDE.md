@JARVIS02_WRITER/CLAUDE_WRITER.md
@JARVIS03_RADAR/CLAUDE_RADAR.md
@JARVIS00_INFRA/CLAUDE_INFRA.md
@JARVIS04_SCHEDULER/CLAUDE_SCHEDULER.md
@JARVIS08_PUBLISH/CLAUDE_PUBLISH.md

# JARVIS AGENT — 루트

## 결정 사유·근거 — `docs/decisions/` 단일 진실 소스

CLAUDE.md 는 *현재 적용 규칙* 만 박제한다. *왜 이 규칙인지* 의 역사·근거·포기한 대안은
[`docs/decisions/`](docs/decisions/README.md) 의 ADR 이 *단일 진실 소스*.

| 영역 | ADR |
|------|-----|
| 단일 진입점 원칙 | [ADR 001](docs/decisions/001-single-entry-point.md) |
| 모델 다층 분리 (Haiku / Sonnet 4.6 / Opus 4.6) | [ADR 002](docs/decisions/002-model-layering.md) |
| BLOG_SUPREME_LAW.md 14조 헌법화 | [ADR 003](docs/decisions/003-blog-supreme-law.md) |
| 텔레그램 승인 게이트 (외부 영향 단일 차단점) | [ADR 004](docs/decisions/004-telegram-approval-gate.md) |
| 자가 학습 — catch() 단일 진입점 + 2-Tier (패턴·Bandit → LLM) ★ 단일 진실 소스 `architecture.py` | [ADR 005](docs/decisions/005-three-tier-learning.md) |
| **★ 밴딧 arm = 유한 전략 (오류지문 arm 폐기·오염 게이트·28D 상한·402MB→45B) — ADR 005 밴딧 정밀보완 (사용자 박제 2026-07-04)** | [ADR 016](docs/decisions/016-bandit-finite-strategy-arms.md) |
| 사용자 박제 4원칙 | [ADR 006](docs/decisions/006-user-pinning-principles.md) |
| Self-Evolving Harness 비전 (Stage A 완료 + Stage B+ 진행) | [ADR 007](docs/decisions/007-self-evolving-harness.md) |
| **Domain Ownership Matrix (★ 2026-05-17 완료)** | [ADR 008](docs/decisions/008-domain-ownership-matrix.md) |
| **★ 하네스 5 Layer 게이트 + 순환 검증 (사용자 박제 2026-05-17)** | [ADR 009](docs/decisions/009-self-evolving-harness-gates.md) |
| **★ 이미지 사실성 — 차트는 JARVIS09 실데이터로만 + 무료 라이브러리 자동설치 화이트리스트 (사용자 박제 2026-06-29)** | [ADR 010](docs/decisions/010-image-factuality-real-data.md) |
| **★ 주제 적응형 동적 데이터 소싱 — 고정 카탈로그→웹 발견(discover)·범용 수집 (사용자 박제 2026-07-01)** | [ADR 011](docs/decisions/011-topic-adaptive-data-sourcing.md) |
| **★ 설계-우선 리서치 파이프라인 — 리서치 설계→근거팩(fact·출처·커버리지)→갭 재수집 + 3-패스 작성 (사용자 박제 2026-07-02)** | [ADR 012](docs/decisions/012-research-first-pipeline.md) |
| **★ 에이전트 파이프라인 정본 흐름 — 03(주제+프로필, 키워드 단독 전송 금지)→02·09 동시 제공→09 무제한 수집(신뢰순위 논문>API>뉴스>기사>웹)→02 매력 대본(수치만 하드 게이트)→06 이미지→08 발행 (사용자 박제 2026-07-03)** | [ADR 013](docs/decisions/013-agent-pipeline-flow.md) |
| **★ 글 품질 강화학습 폐쇄 루프 — 주입(UCB 선택+사용기록)→분석 보상 귀속→weight EMA 갱신→검증된 지침만 생존. 엔진 `JARVIS07_GUARDIAN/quality_learner.py` 단독 (사용자 박제 2026-07-03)** | [ADR 014](docs/decisions/014-writing-quality-reinforcement.md) |
| **★ 모델 단일 계층 통일 — Sonnet 5 하나로, Opus 4.8 폐지 (ADR 015 대체, 사용자 박제 2026-07-06)** | [ADR 017](docs/decisions/017-model-single-tier-sonnet5.md) |
| **★ 로그인·인증 단일 진입점 (사용자 박제 2026-05-17)** | `JARVIS08_PUBLISH/credentials/LOGIN_SUPREME_LAW.md` + `login_manager.py` |

신규 결정·번복은 [`docs/decisions/README.md`](docs/decisions/README.md) 의 형식·정책 따름.

## ★ 로그인·인증 규정 — `LOGIN_SUPREME_LAW.md` 단일 진입점 (사용자 박제 2026-05-17 — ERRORS [145])

**모든 블로그·플랫폼 로그인·인증·쿠키 관련 규정은 `JARVIS08_PUBLISH/credentials/LOGIN_SUPREME_LAW.md` 단독.**

다른 파일에 로그인 관련 규정 본문 발견 시 *즉시 이관 + 호출 형태로 교체*. 허용 호출:
- `from JARVIS08_PUBLISH.credentials.login_manager import get_naver_cookies, get_tistory_cookie, verify_all_logins, refresh_naver_cookies, refresh_tistory_cookies, auto_refresh_if_needed, job_pre_publish_check`

**금지**: `os.environ['NV_PASSWORD'|'TS_COOKIE'|...]` 직접 참조, 쿠키 파일 경로 하드코딩, `_auth_headers` 같은 함수 외부 정의.

**검증**: `python3 shared/precommit_check.py --category auth`


## ★ 도메인 단일 진입점 매트릭스 (ADR 008 완료 — 2026-05-17)

**한 사고 = 한 폴더 수정** 원칙. 새 사고 발생 시 *2곳 이상 수정 필요* 하면 그 자체가 분산 시그널 → ADR 008 매트릭스 재검토 트리거.

| 도메인 | Owner 폴더 | 책임 | precommit |
|--------|----------|------|-----------|
| 이미지 | `JARVIS06_IMAGE/` | 생성·검증·dedupe·삽입·재사용·정리·업로드 | `domain/image` ✅ |
| 발행 (플랫폼) | `JARVIS08_PUBLISH/platforms/` | 네이버·티스토리 Selenium | `domain/publish` ✅ |
| 카테고리 | `JARVIS08_PUBLISH/category/` | `ECONOMIC_CATEGORY` 등 카테고리 상수 | `domain/category` ✅ |
| 쿠키 | `JARVIS08_PUBLISH/credentials/` | 네이버·티스토리 쿠키 refresher | (publish 도메인 포함) |
| 분량 | `JARVIS02_WRITER/length_manager.py` | 문장·글자수 상수 + 헬퍼 | `domain/length` ✅ |
| 헌법 | `BLOG_SUPREME_LAW.md` + `law_enforcer.py` | 정책 본문 + 집행 함수 | `domain/constitution` ✅ |
| 스케줄 | `JARVIS04_SCHEDULER/` | 모든 cron·interval | `schedule` ✅ |
| 도구 | `shared/tools.py` + `agent_tools.py` | 라우터 도구 카탈로그 | `tools` ✅ |
| 오류·학습 | `JARVIS07_GUARDIAN/` | 오류 수집·자가 진단·학습 (도메인 분류 active) | (자기 도메인) |
| 인프라 | `JARVIS00_INFRA/` | 데몬·프로세스 제어 | `infra` ✅ |

**Backward-compat shim** — 옛 위치 (`JARVIS02_WRITER/{naver,tistory}_poster.py` 등) 는 `sys.modules[__name__] = _new_module` 패턴으로 외부 setattr 호환. 호출자는 `JARVIS08_PUBLISH.*` 직접 import 권장.

**Phase 6 최종 회귀 결과** — precommit_check 8 카테고리 (infra·length·blog·schedule·autocode·tools·image·domain) **전수 0건 통과**.

## ★ 새 도메인·에이전트 추가 표준 (자동 등록 — 사용자 박제 2026-05-17)

새 `JARVIS{NN}_*/` 폴더 추가 시 *반드시* 4 항목 갖춰야 데몬·텔레그램·hub 에 자동 노출:

| 항목 | 위치 | 검증 |
|------|------|------|
| 📄 `{name}_agent.py` | 폴더 안 | `agent_registration_check.py` 가 매번 검증 |
| ⚙️ `register(scheduler, bus)` | agent.py 내 | 데몬 부팅 시 `_autoregister_agents()` 자동 호출 |
| 📡 `declare(agent_id=..., status_fn=..., help_section=...)` | agent.py 내 모듈 레벨 | 텔레그램 `/status`·`/help`·hub 카드 자동 노출 |
| 📋 `AGENTS.md` 등록 행 | 루트 `AGENTS.md` | 검증 스크립트가 grep 으로 확인 |

**검증 1줄 명령**:
```bash
python shared/agent_registration_check.py    # 모든 폴더 4 항목 검증
```

GUARDIAN 이 발행 직전엔 Tier-1 자체수리 sweep(LLM-0), 매일 새벽 04:30 `job_deep_audit` 로 전체 코드 점검·수정 (backlog Tier-2 + 광범위 감사) + 누락 시 보강 안내. 데몬도 다중 `_agent.py` 지원 (한 폴더에 여러 agent 파일 OK — 예: JARVIS07 의 eval + guardian).

## 자동 검증 — `shared/precommit_check.py`

CLAUDE.md 박제 27종 grep 검증을 통합한 단일 진입점. git pre-commit 훅 + 데몬 부팅 + JARVIS07 Auditor 잡 3곳에서 자동 실행.

```bash
python3 shared/precommit_check.py            # 전체 검증
python3 shared/precommit_check.py --list     # 카테고리 목록
git config core.hooksPath .githooks          # pre-commit 훅 활성화 (1회)
export JARVIS_STRICT=1                       # 위반 발견 시 commit 차단 (기본은 경고)
```

## 에이전트 목록

| 폴더 | 이름 | 역할 |
|------|------|------|
| `JARVIS01_MASTER/`   | JARVIS01 CORE   | 마스터 라우터 (LangGraph) — 자유 문장 → 인텐트 분류 → 에이전트 디스패치 |
| `JARVIS02_WRITER/` | JARVIS02 WRITER | 블로그 자동화 (네이버·티스토리) |
| `JARVIS03_RADAR/`  | JARVIS03 RADAR  | 트렌드·키워드 수집·분석 (대시보드는 `dashboard/` Next.js :9199) |

## 통합 데몬 (유일한 진입점)

**`jarvis_daemon.py`** — 모든 에이전트를 단일 프로세스로 관리. 직접 실행만 허용.

```bash
python jarvis_daemon.py          # 전체 시작
pkill -f jarvis_daemon.py        # 전체 종료
```

| 역할 | 구현 위치 |
|------|-----------|
| JARVIS02 스케줄 (Market Signal·경제 브리핑·RADAR 파이프라인) | `JARVIS02_WRITER/scheduler.py` 모듈을 importlib 로드 → 스레드 실행 |
| JARVIS03 스케줄 (트렌드 수집·성과 수집·급등 알림·분석 fallback) | APScheduler (BackgroundScheduler) |
| 통합 텔레그램 봇 (명령어 + 인라인 버튼 승인) | 단일 polling 루프 내장 |
| PID 중복 실행 방지 | `logs/daemon.pid` |

**새 에이전트 추가 규칙** (JARVIS04 이후): `JARVIS{NN}_NAME/{name}_agent.py` 에 `register(scheduler, bus)` 정의 → 데몬 부팅 시 자동 감지. `jarvis_daemon.py` 수정 금지. 가이드: `AGENTS.md`. JARVIS02/02 는 레거시 통합 흐름 유지 (skip_dirs).

## 라이브러리 모듈 (직접 실행 금지)

| 파일 | 역할 | daemon 사용 방식 |
|------|------|-----------------|
| `JARVIS02_WRITER/scheduler.py` | JARVIS02 작업 로직 전체 | importlib 로드 → schedule_mode() 스레드 |
| `JARVIS03_RADAR/approval_bot.py` | 인라인 버튼 콜백 처리 | _handle_callback() 만 import |
| `JARVIS03_RADAR/post_quality_analyzer.py` | 발행 글 품질 분석 | subprocess Popen |
| `JARVIS02_WRITER/revise_adapter.py` | 승인 후 자동 재발행 | subprocess Popen |
| `JARVIS07_GUARDIAN/auto_repair.py` | 전체 코드 검토·수정 (새벽 04:30 `job_deep_audit`, Sonnet 5) | claude-code-sdk query |

## 공유 자원 (신경계)

| 항목 | 위치 | 설명 |
|------|------|------|
| `.env` | 루트 | API 키·토큰 (모든 에이전트 공유) |
| `.venv/` | 루트 | Python 가상환경 (공유) |
| `shared/db.py` | 루트 | SQLite 공용 DB |
| `shared/bus.py` | 루트 | 이벤트 버스 — 에이전트 간 유일한 통신 창구 |
| DB 파일 | `JARVIS_DB_PATH` (`.env`, 기본 `~/.jarvis/jarvis.sqlite`) | 실 DB. `shared/db.py` 가 `.env` 자가 로드 후 `DB_PATH` 로 단일 해석 — 직접 `connect('shared/jarvis.sqlite')` 금지, 항상 `from shared.db import DB_PATH` |
| `shared/llm.py` | 루트 | **★ LLM 호출 단일 진입점 (ERRORS 4회 반복 박제 2026-05-24)** — 모든 LLM 호출은 `invoke_text(alias, prompt)` 만. 직접 API 키·`anthropic.Anthropic()`·모델 문자열 하드코딩 금지. 모델 변경은 `MODELS` dict 한 곳만. |
| `shared/embeddings.py` | 루트 | **★ 임베딩(시맨틱 벡터) 단일 진입점 (사용자 박제 2026-07-02)** — 로컬 MiniLM(무료·CPU) 재사용. 오류매칭·밴딧·RADAR·QA검색 공용. `embed_texts / embed_text / encode / cosine_sim / available` 만. 모델명은 `EMBED_MODEL_NAME`·`EMBED_DIM` 두 상수 한 곳 (미래 bge-m3 = 두 줄 교체 + reindex). `vector_store` 도 이 상수 import. `sentence_transformers` 직접 로드·모델명 하드코딩 금지. |

## 루프 가드 규칙
- `post_analysis.is_revised=1` 인 글은 재분석 대상에서 자동 제외
- 네이버는 글 당 1회 수정 한도

## 파일 정리 규칙 (강제 — 격주 자동 실행)
- **자동 정리**: daemon APScheduler `job_file_cleanup` — 격주 월요일 04:00 자동 실행
- **정리 로직**: `shared/file_cleanup.py` — 규칙 변경 시 이 파일만 수정
- **보존 정책** (변경 시 file_cleanup.py `_RULES` 수정):

| 대상 | 보존 기간 |
|------|---------|
| `JARVIS02/logs/economic_*.log` | 7일 |
| `JARVIS02/logs/market_signal_*.txt` | 14일 |
| `JARVIS02/logs/report_*.txt` | 30일 |
| `JARVIS03/data/trends_*.json` | 30일 |
| `JARVIS02/screenshots/` | 30일 |
| `.DS_Store` / `.fuse_hidden*` | 즉시 삭제 |

- **수동 실행**: `python shared/file_cleanup.py`
- **결과 알림**: 텔레그램으로 삭제 통계 전송 (삭제 0건 시 조용히 패스)

## 오류·문제 기록 규정 (강제 — 모든 에이전트 공통)
- **단일 위치**: `JARVIS07_GUARDIAN/ERRORS.md` — 모든 오류 기록의 유일한 저장소. 루트에 없음.
- **오류 발생 시 첫 행동**: `JARVIS07_GUARDIAN/ERRORS.md` 를 **반드시 먼저 Read** 하고 동일·유사 증상 검색.
- 매칭되는 항목 있으면 기록된 해결책 적용. 헛다리 항목은 **절대 다시 시도 금지**.
- **수정 완료 후 즉시**: 어떤 에이전트(JARVIS02/02/03...)의 문제든 `JARVIS07_GUARDIAN/ERRORS.md` 에 항목 추가. 예외 없음.
- 사용자가 별도로 요청하지 않아도 자동 실행. 기록 안 하면 규정 위반.
- 양식: 증상 / 환경 / 원인 / 헛다리 / 해결 / 파일 / 교훈.

## ★★ 최우선 설계 원칙 — 복사본을 진실로 믿지 말 것 (사용자 박제 2026-07-20)

**진실은 한 곳에서 *읽어라*. 어딘가에 복사해두고 그 복사본을 믿지 마라.**

2026-07-20 하루에 같은 병이 5번 났다. 전부 *복사본을 진실로 착각* 한 사고:

| 무엇을 복사했나 | 원본이 바뀌자 | 사례 |
|---|---|---|
| **값** 을 코드에 | 노브 바꿔도 옛 값 표시 | 제안 엔진 "재시도 3회"·"잡 42개" |
| **스키마** 를 코드에 | 필드 추가되면 미표시 | 대시보드 `five_hour`/`seven_day` 키 |
| **사실** 을 문서에 | 코드 바뀌어도 문서 그대로 | `hub.py` 현행 기술 (ERRORS [456]) |
| **수정** 을 라이브러리에 | venv 재생성에 소멸 | pytrends venv 패치 (ERRORS [455]) |
| **함수** 를 미리 (`from X import f`) | 패치해도 옛 함수 호출 | sdk_compat 무력화 (ERRORS [457]) |
| **상태** 를 플래그에 | 실제로 안 먹어도 True | `_PATCH_INSTALLED = True` |

**실행 규칙**
- 표시·판단에 쓰는 수치·목록·스키마는 *런타임 조회* 로 파생한다. 문자열로 박지 말 것.
- 외부 라이브러리 비호환은 *저장소 코드에서 흡수* 한다. `.venv` 안을 고치는 규정을 만들지 말 것.
- monkey-patch 는 `sys.modules` 순회로 *모든 바인딩* 을 교체하고, **교체 개수를 로그로 남긴다**.
- **설치 플래그는 '시도' 의 기록이지 '적용' 의 증거가 아니다.** 패치·훅·등록에는 반드시
  *효과를 동작으로 확인하는* 스모크 테스트를 함께 둘 것 —
  `claude_sdk_compat.patch_effective()` / `pytrends_utils.retry_compat_effective()` 가 표준 형태.
  (가짜 입력을 *실제 소비자가 쓰는 참조* 로 한 번 통과시켜 예외 유무로 판정.)
- 외부 응답을 화면에 붙일 때 *구조화된 배열/목록 필드가 있으면 그것을 렌더* 한다.

**자동 강제**: `python3 shared/precommit_check.py --category copytruth`
— git 훅·데몬 부팅·GUARDIAN 잡 3곳에서 자동 실행. ① venv 내부 수정 *지시*
② 효과 검증 없는 monkey-patch ③ 효과 검증 없는 설치 플래그 를 커밋 단계에서 차단.

## ★ 작업 종료 절차 — 재시작·검증·커밋 (강제 — 사용자 박제 2026-07-20)

**순서 고정: ① 수정 → ② 데몬 재시작 → ③ *재시작된 프로세스로* 검증 → ④ 전부 커밋.**

- **② 재시작 의무**: 실행 중 데몬은 *수정 전 코드를 메모리에 보유* 한다 (Python import 캐시 — 상세 근거는 아래 자가학습 섹션). 따라서 코드를 고쳤으면 `./restart_daemon.sh` 로 반드시 재기동. 재시작 없이 "고쳤다" 고 보고하지 말 것.
- **③ 검증은 재시작 *후* 프로세스로**: `ps -o lstart= -p $(pgrep -f jarvis_daemon.py)` 시각이 수정 파일 mtime 보다 **나중** 인지 확인한 뒤 검증할 것. (2026-07-20 실제 사고: compat 패치를 고치고 12/12 성공까지 확인했으나 데몬은 4분 전에 뜬 *옛 코드* 였다 — 사용자 지적으로 발견.) 가능하면 패치 적용 여부를 로그로 남겨 확인 (예: `monkey-patch 설치 완료 (바인딩 참조 N곳 교체)`).
- **재시작은 `./restart_daemon.sh` 만**: keeper 를 먼저 unload → 좀비 정리 → 기동 → keeper 재등록 순서가 스크립트에 박혀 있다. 수동 `pkill` + `nohup` 조합은 중복 인스턴스·keeper 영구 정지를 유발.

## ★ 커밋 규정 (강제 — 사용자 박제 2026-07-20)
- **작업 종료 시 워킹트리를 깨끗이 비운다**: `git status` 잔여 0. *내가 수정하지 않은 파일* (데몬이 런타임에 갱신한 학습 산출물 — `design_learn_log.json`·`design_recipes.json`·`synonym_cache.json`·`learned_patterns.json` 등) 도 **함께 커밋**한다.
- **사유**: 이들은 에이전트가 누적한 *학습 자산* 이다. 미커밋으로 방치하면 ① `git checkout`·브랜치 이동 시 소실 ② 다음 작업자가 "누가 왜 바꿨나" 를 추적 불가 ③ 회고·롤백 불가. 내 변경분만 골라 커밋하는 것은 규정 위반.
- **절차**: 커밋 직전 `git status --short` 로 잔여 확인 → 전부 스테이징 (`git add -A`) → 커밋 메시지 본문에 *내 변경분* 을 쓰고, 말미에 *동반 커밋된 런타임 산출물* 을 한 줄로 명시.
- **예외**: `.gitignore` 대상·비밀정보·대용량 바이너리는 제외 (해당 시 `.gitignore` 에 추가하고 그 사실을 메시지에 남길 것).
- **검증**: 커밋 후 `git status --short` 결과가 비어 있어야 함.

## Claude Code 작업 효율 규정 (강제)
- **파일 읽기**: 이미 읽은 파일 재읽기 금지. 필요한 범위만 Read(offset+limit).
- **탐색**: 심볼 찾기는 Grep 직접. 파일 3개 이하는 직접 Read. Explore agent는 광범위 탐색만.
- **CLAUDE.md**: 코드에서 읽히는 내용 기재 금지. 비직관적 규칙·제약만. 각 파일 ≤30줄.
- **메모리**: 코드에 반영된 변경 이력 즉시 삭제. 파라미터·함수 시그니처 저장 금지.
- **응답**: 변경 완료 후 파일명 + 핵심 변경 1줄. 배경 설명·중간 과정 생략.

## 웹 대시보드 폰트 규정 (강제 — JARVIS03 RADAR + 모든 신규 에이전트)
- **최소 글자 크기**: **14px**. 캡션·라벨·메타 텍스트 등 *어떤 텍스트도* 14px 미만 금지.
- **짝수 단위만 사용**: 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 38, 42, 44, 68. 홀수·소수 금지.
- **폰트 위계 표준**:
  - 14px — 캡션, 메타 정보, 작은 라벨
  - 16px — 본문, 버튼, 일반 텍스트
  - 18px — 강조 본문, 섹션 헤더 작은 것
  - 20~22px — 섹션 헤더
  - 24~28px — 카드 KPI 숫자
  - 30~34px — Hero 타이틀, 큰 KPI
  - 38~44px — 페이지 메인 타이틀
- **검증 명령**: `grep -oE 'font-size:\s*[0-9.]+px' app.py | sort -u` — 모든 값이 짝수 + 14+ 인지 확인.
- **위반 시**: 즉시 일괄 변환. 새 코드 작성 시 *최소 14px / 짝수* 자동 준수.

## 웹 대시보드 색상 토큰 규정 (강제 — JARVIS03 RADAR + 모든 신규 에이전트)
- **단일 진실 소스**: `JARVIS03_RADAR/tokens.py` 의 `COLOR` 5색 + `NEUTRAL` 만 사용.
- **5색**: primary(파랑) / success(초록) / warn(앰버) / danger(빨강) / muted(슬레이트). 추가 금지.
- **인라인 hex 금지**: `#abcdef` 직접 작성 금지. `from tokens import COLOR; COLOR["primary"]` 만.
- **그라디언트·네온 글로우·shadow 남발 금지**: 카드 border-top 1색 / 보더 1색 끝.
- **카드 렌더**: `components.py` 의 `kpi_card / action_card / insight_card / status_chip / empty_state / error_state / section_header` 만 호출. 인라인 `<div style="...">` 작성 금지.
- **velocity·level·status 매핑**: `tokens.py` 의 `VELOCITY_COLOR / LEVEL_COLOR / DIFFICULTY_COLOR` 만 사용.
- **검증 명령**: `grep -oE '#[0-9a-fA-F]{6}' app.py | sort -u` — 결과가 토큰 5색 + neutral 외에 거의 없어야 함 (섹터 색은 예외).

## 인프라 관리 규정 (강제 — 절대 — 모든 인프라 책임 단일 진입점)
- **단일 진입점**: `JARVIS00_INFRA/infra_agent.py`. 데몬 프로세스 라이프사이클·시스템 상태 종합 빌드(`build_status`)·텔레그램 시스템 관리 명령(/status·/restart·/quit) 핸들러·infra.* 인텐트 처리 모두 여기.
- **다른 파일 금지**: `jarvis_daemon.py` 는 *프로세스 부트스트랩 + APScheduler/봇 polling 루프* 만. *시스템 상태 빌드·시스템 관리 명령 분기·infra capability 선언* 박지 말 것. 모두 `JARVIS00_INFRA.infra_agent` 위임.
- **다른 폴더 금지**: `JARVIS01_MASTER`·`JARVIS02_WRITER`·`JARVIS03_RADAR`·`shared/` 어디에도 인프라 코드 (데몬 제어·시스템 상태·프로세스 관리·대시보드 자식 프로세스 관리(FastAPI :9198 · Next.js :9199)·Keeper 통신) 박지 말 것.
- **발견 즉시 이관**: 인프라 관련 신규 코드를 다른 파일에서 발견하면 *즉시* `JARVIS00_INFRA/infra_agent.py` 로 이관. 미루지 말 것.
- **이관 절차**: ① `JARVIS00_INFRA/infra_agent.py` 에 함수/핸들러 추가 → `__all__` 업데이트 → ② 호출자 (jarvis_daemon 등) 는 `from JARVIS00_INFRA.infra_agent import ...` 로 위임 → ③ 호출자에 fallback 인프라 로직 두지 말 것.
- **★ 이관 완전성 (헌법 박제 2026-05-15 — 3회 반복 교훈)**: `import` 추가만으로는 불충분 — *반드시 구 함수 본체를 삭제*. Python last-def override 로 인해 구 정의가 새 정의를 덮어쓸 위험. 이관 완료 후 `grep -rn "^def <함수명>" --include="*.py" .` 으로 중복 정의 잔존 여부 반드시 확인.
- **infra 인텐트 추가**: ① `JARVIS00_INFRA/infra_agent.py` 의 `register_capability()` 의 intents 목록 + `handle_safe_intent` / `execute_approval` 분기 추가 → ② `JARVIS01_MASTER/dispatchers.py` 의 `SAFE_INTENTS` / `APPROVAL_INTENTS` 동시 추가 → ③ `JARVIS00_INFRA/CLAUDE.md` 의 비직관 규칙 표 갱신.
- **검증 명령** (본체 로직 잔존 — 위임 형태와 카탈로그 매핑은 정당):
  ```bash
  # ① jarvis00_infra capability 본체 declare (JARVIS00_INFRA/infra_agent.py 만 합법)
  grep -rnE 'declare\([^)]*agent_id[^)]*=[^)]*"jarvis00_infra"' --include='*.py' . \
    | grep -v 'JARVIS00_INFRA/' | grep -v __pycache__ | grep -v '\.venv/'
  # ② build_status 본체 정의 (JARVIS00_INFRA/infra_agent.py 의 def build_status 만 합법)
  grep -rnE '^def build_status|^def _build_status\b' --include='*.py' . \
    | grep -v 'JARVIS00_INFRA/' | grep -v 'from JARVIS00_INFRA' | grep -v __pycache__
  # ③ 시스템 명령 처리 본체 (handle_command / handle_safe_intent / execute_approval 의 def)
  grep -rnE '^def handle_command|^def handle_safe_intent|^def execute_approval' --include='*.py' . \
    | grep -v 'JARVIS00_INFRA/' | grep -v __pycache__
  ```
  세 명령 모두 결과 0행이어야 함. *카탈로그 매핑* (예: `dispatchers.py` 의 `"infra.daemon.restart"` set 항목) 과 *데몬 부트스트랩* (예: `jarvis_daemon.py` 의 `_daemon_shutdown.set()`) 은 *정당한 잔존* — 본체 로직 아님.
- **위반 시**: 인프라 책임 분산은 데몬 부팅 흐름 깨짐·이중 capability 등록·시스템 상태 표시 불일치의 원인. 발견 즉시 이관, 예외 없음.
- **다음 작업자에게 전파**: 신규 인프라 코드 추가 시 *반드시* 이 규정 먼저 읽고 시작. 데몬 직접 수정 본능적으로 가지 말 것 — 항상 `JARVIS00_INFRA/infra_agent.py` 부터 검토.

## 블로그 본문 분량 — 기술 단일 진입점 (강제)
- **정책 본문**: `JARVIS02_WRITER/BLOG_SUPREME_LAW.md` 제8조(분량 **25문장 + 이미지 5+α 동적**, ★ 사용자 박제 2026-05-17 · 이미지 8→5 정정 2026-07-05) + **제8-B조(분량 표기 표준 — 1문장 ≈ 약 50자)** 단일 진실 소스.
- **★ 분량 표기 표준 — 사용자 박제 2026-05-14**: 모든 분량은 *문장과 글자수 둘 다* 기록. `length_manager.build_length_phrase(min, max=None)` 헬퍼 사용. 예: `5~6문장(약 250~300자)` / `3문장(약 150자)`. 한쪽만 적기 절대 금지 (제8-B조 참조).
- **기술 단일 진입점**: `JARVIS02_WRITER/length_manager.py` 가 *모든* 글자수·문장수 상수·prompt 빌더·압축 함수를 단독 보유. `KOREAN_PER_SENTENCE = 50` + `build_length_phrase()` 헬퍼.
- **다른 파일 금지**: `JARVIS02_WRITER/*`·`JARVIS03_RADAR/*`·`shared/*`·루트 `*.py` 어디에도 분량 관련 상수·하드코딩 숫자·`[가-힣]` 직접 정규식·`{N,M}` 양화 표현·자연어 분량 표현·`text[:N]` 본문 자르기 박지 말 것.
- **prompt 분량 표현**: `length_manager.build_length_phrase()` / `build_prompt_length_block()` / `build_short_length_phrase()` / `{_L.XXX}` 직접 보간만 허용. raw triple-quote 안에서는 `__PLACEHOLDER__` + `.replace(..., str(_L.XXX))` 패턴.
- **`shared/seo.py` 의 utility default 도 None**: 도메인 무관 유틸 default 인자 `max_korean=None`. 호출자가 length_manager 상수를 명시 전달. shared 에 정책 박지 말 것.
- **검증 명령** (5종 — 모두 0행):
  ```bash
  # 1) [가-힣] 정규식 직접 (length_manager·shared/seo 제외)
  grep -rnE '\[가-힣\]' --include='*.py' JARVIS02_WRITER shared JARVIS03_RADAR | grep -v length_manager.py | grep -v shared/seo.py | grep -v '한다\|된다\|있다\|없다\|크다'
  # 2) 자연어 분량 표현
  grep -rnE '[0-9]+자\s*(이내|이하|초과|미만|이상|전후|범위|기준|정도|내외)|[0-9]+\s*~\s*[0-9]+자' --include='*.py' JARVIS02_WRITER shared JARVIS03_RADAR | grep -v length_manager.py | grep -v shared/seo.py
  # 3) compress / cap / count 직접 호출 (정의·_L 위임 제외)
  grep -rnE 'compress_to_korean\(|cap_content\(|count_korean\(|sanitize_body\(' --include='*.py' . | grep -v length_manager.py | grep -v shared/seo.py | grep -v 'def _cap\|return _L\.compress\|__all__'
  # 4) 글자수 한도 후보 숫자 (블로그 본문 맥락만)
  grep -rnE '\(2500|\(2200|\(1500|\b2500자|\b2200자|\b1500자' --include='*.py' . | grep -v length_manager.py | grep -v shared/seo.py | grep -v shared/precommit_check.py
  # 5) 검증 게이트
  grep -rnE '\b2500\b|\b2200\b|\b1500자|len\(re\.findall\(r..\[가-힣\]' JARVIS02_WRITER/*.py shared/*.py JARVIS03_RADAR/*.py | grep -v length_manager.py
  ```

## 블로그 글·이미지·소제목 — BLOG_SUPREME_LAW.md 위임 (강제)
**정책 본문 0줄 — 본 CLAUDE.md 는 정책을 박지 않음. 모든 정책은 BLOG_SUPREME_LAW.md 단일 진실 소스.**

- **제4조 배치 규정** (허용 패턴 4가지·이미지 연속 금지·문단 3개+ 연속 금지): 집행 코드 = `law_enforcer.enforce_supreme_law()`. 발행 직전 의무 호출. 위반 검증: ERRORS [39].
- **★ 이미지 연속 방지 파이프라인 6곳 (ERRORS [39][103][170][171] 4회 반복 박제 — 2026-05-27)**: 이미지 연속 배치 버그 수정 시 *반드시* 6곳 전수 점검 — ① `JARVIS06_IMAGE/injectors/block_assembler.py:assemble_blocks` (figure/table 블록 분류) ② `JARVIS02_WRITER/jarvis_main.py:enforce_text_between_images` (spacer 리셋 조건: `btype not in (divider, spacer)`) ③ `JARVIS06_IMAGE/validators/image_validators.py:_fix_any_consecutive_images` (deferred 플러시 + `_is_content` 에 `"html"` 타입 포함) ④ `JARVIS02_WRITER/law_enforcer.py:_dedupe_consecutive → enforce_spacing` ⑤ `JARVIS08_PUBLISH/platforms/naver_poster.py` (모든 블록 타입 핸들러 — `html` 포함) ⑥ `JARVIS08_PUBLISH/platforms/tistory_poster.py` (동일). 한 곳만 고치면 다른 경로에서 재발. **블록 타입 추가 시 발행자 양쪽(naver+tistory) 동시 갱신 필수.** 검증: `grep -n 'def assemble_blocks\|def enforce_text_between_images\|def _fix_any_consecutive_images\|_dedupe_consecutive' JARVIS06_IMAGE/injectors/block_assembler.py JARVIS02_WRITER/jarvis_main.py JARVIS06_IMAGE/validators/image_validators.py JARVIS02_WRITER/law_enforcer.py` → 4곳 존재 + `grep -n "elif btype == .html" JARVIS08_PUBLISH/platforms/naver_poster.py JARVIS08_PUBLISH/platforms/tistory_poster.py` → 양쪽 존재해야 함.
- **제3조 구조 완결성** (헤더 아래 1문장 이상): 임계값 = `length_manager.MIN_SENTENCES_PER_HEADING / MIN_KOREAN_PER_HEADING`. 집행 = `law_enforcer` + `jarvis_main._check_empty_headers`. 후처리 빈 헤더 검출 시 텔레그램 경고 + 폴백 자동 삽입.
- **제1-B조 동적 생성** (모든 한국어 문장·이미지 LLM 매번 생성): 허용 호출 = `shared.llm.invoke_text("writer_fast", ...)` + 1줄 비상 폴백만. 금지 = `_CTA_POOL` / `_FALLBACK_OUTRO` / `FALLBACK_TEXT` / `STYLE_ZONES` / `THEME_PALETTES` 고정 풀·고정 템플릿.
- **제9조 여백** (글↔글 1행·소제목 앞 2행): 집행 = `law_enforcer.enforce_spacing()`. 발행 직전 자동.
- **검증 명령** (3종 — 모두 0행):
  ```bash
  # ① 발행 후 이미지 연속 (제4조 위반)
  python3 -c "import sqlite3,re; from shared.db import DB_PATH; con=sqlite3.connect(str(DB_PATH)); rows=con.execute('SELECT id, original_html FROM post_analysis ORDER BY id DESC LIMIT 5').fetchall(); pat=re.compile(r'</figure>\s*(<[^/][^>]*>\s*)*<figure'); [print(f'⚠️ ID={r[0]}') for r in rows if r[1] and pat.search(r[1])]"
  # ② 빈 헤더 (제3조 위반)
  python3 -c "import sqlite3,re; from shared.db import DB_PATH; con=sqlite3.connect(str(DB_PATH)); [print(r[0], h) for r in con.execute('SELECT id,original_html FROM post_analysis ORDER BY id DESC LIMIT 5') for h in re.findall(r'<h[1-6][^>]*>(.+?)</h[1-6]>\s*(?:<[^>]+>\s*)*(?:</?[^h][^>]*>)?', r[1] or '') if not h.strip()]"
  # ③ 고정 한국어 풀·폴백 상수 (제1-B조 위반)
  grep -rnE 'FALLBACK_TEXT\s*=\s*["\(]|FALLBACK_HTML\s*=\s*["\(]|_CTA_POOL\s*=' --include='*.py' JARVIS02_WRITER | grep -v __pycache__
  ```
- **신규 작성 코드 추가 시**: `law_enforcer.build_writing_rules_block()` 가 BLOG_SUPREME_LAW.md 를 매 호출 동적 로드 → supreme_block 반환 → 작성 prompt 상단에 주입. 작성 prompt 안에 ★ 제N조 자연어 인용 박지 말 것. `(헌법 제N조 적용)` 형태 *짧은 참조* 만 허용.

## 스케줄 관리 규정 (강제 — 절대 — 모든 시간 기반 자동 실행)
- **★ 절대 단일 진입점**: 시스템 내 *모든* 시간 기반 자동 실행 (cron·interval·polling) 은 `JARVIS04_SCHEDULER` 가 단독 관리. 등록·조회·이력·제어·인스턴스 생성·EventListener 부착 전부 여기서.
- **default 잡 카탈로그**: `JARVIS04_SCHEDULER/job_registry.py` 의 `DEFAULT_JOBS` 리스트가 *단일 진실 소스*. 새 default 잡은 거기에 dict 추가 (잡 ID·name·trigger·callback path·misfire·owner). *_agent.py register() 안 add_job 도 폐기 — DEFAULT_JOBS 로 통합. (예외: 정말 동적 등록이 불가피한 경우만 — 사용자 자연어 임시 잡)
- **APScheduler 인스턴스 생성**: `JARVIS04_SCHEDULER.job_catalog.create_scheduler()` 만 합법. 다른 위치에서 `BackgroundScheduler(...)` / `BlockingScheduler(...)` 직접 생성 금지. 데몬도 이 함수 사용 (이미 적용됨).
- **APScheduler 인스턴스 접근**: `JARVIS04_SCHEDULER.job_catalog.get_apscheduler()` 만 합법. 다른 폴더에서 데몬의 `_apscheduler` 직접 참조 금지.
- **EventListener 단일 부착**: `JARVIS04_SCHEDULER.job_history.attach_listeners(scheduler)` — 데몬 부팅 시 1회. 다른 위치에서 EventListener 추가 부착 금지 (job_runs 중복 적재 사고).
- **`schedule` 라이브러리 사용 금지**: `import schedule` / `schedule.every()` / `schedule.run_pending()` 일체 금지. JARVIS02 의 legacy `schedule_mode()` 는 이미 폐기 (stub 만 잔존). 신규 도입 절대 금지.
- **`while True + datetime.now().hour == N` 폴링 패턴 금지**: 시간 기반 자동 실행은 반드시 APScheduler cron/interval trigger 사용. `current_hour == N` / `now().hour == N` 형태의 시간 분기 폴링 코드 발견 즉시 DEFAULT_JOBS 로 이관.
- **`threading.Timer` 주기 실행 금지**: 일회성 지연 실행은 OK (예: 60초 후 cleanup). 주기 반복은 APScheduler interval trigger 사용.
- **잡 변경 게이트**: pause/resume/run_now/remove 4개 도구 = `requires_approval=True`. JARVIS01 ReAct 라우터가 호출 시 *반드시* 텔레그램 인라인 버튼 ✅/❌ 통과 후만 실행.
- **이관 의무 (★ 즉시·예외 없음)**: 다른 폴더에서 ① `scheduler.add_job` 직접 호출 ② `BackgroundScheduler(...)` 인스턴스 생성 ③ `schedule.every(...)` 라이브러리 사용 ④ `current_hour ==` 폴링 ⑤ `threading.Timer(...).start()` 주기 패턴 — 발견 즉시 `JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS` 로 이관. 미루지 말 것.
- **검증 명령** (7종 — 모두 결과 0행):
  ```bash
  # ① add_job 외부 호출 (JARVIS04 외, 주석 제외)
  grep -rnE 'scheduler\.add_job\(|\.add_job\(' --include='*.py' . \
    | grep -v 'JARVIS04_SCHEDULER/' | grep -v __pycache__ | grep -v '\.venv/' \
    | grep -vE ':[0-9]+:#'
  # ② BackgroundScheduler / BlockingScheduler 직접 생성 (JARVIS04 외)
  grep -rnE 'BackgroundScheduler\(|BlockingScheduler\(' --include='*.py' . \
    | grep -v 'JARVIS04_SCHEDULER/' | grep -v __pycache__ | grep -v '\.venv/'
  # ③ apscheduler import 외부 (JARVIS04 외)
  grep -rnE 'from apscheduler|import apscheduler' --include='*.py' . \
    | grep -v 'JARVIS04_SCHEDULER/' | grep -v __pycache__ | grep -v '\.venv/'
  # ④ add_listener 외부 호출 (job_history.py 외)
  grep -rnE '\.add_listener\(' --include='*.py' . \
    | grep -v 'JARVIS04_SCHEDULER/job_history.py' | grep -v __pycache__ | grep -v '\.venv/'
  # ⑤ _apscheduler 글로벌 직접 참조 (단어 경계 — jarvis_daemon·JARVIS04 외)
  grep -rnE '\b_apscheduler\b' --include='*.py' . \
    | grep -v 'jarvis_daemon.py' | grep -v 'JARVIS04_SCHEDULER/' | grep -v __pycache__ | grep -v '\.venv/'
  # ⑥ schedule 라이브러리 사용 금지
  grep -rnE 'schedule\.every\(|schedule\.run_pending|^import schedule\b|^from schedule\b' --include='*.py' . \
    | grep -v __pycache__ | grep -v '\.venv/'
  # ⑦ 시간 폴링 패턴 (current_hour == N · now().hour == N)
  grep -rnE 'current_hour\s*==|current_hour\s*in\s*\[|now\(\)\.hour\s*==' --include='*.py' . \
    | grep -v 'JARVIS04_SCHEDULER/' | grep -v 'JARVIS02_WRITER/scheduler.py:\s*#' \
    | grep -v __pycache__ | grep -v '\.venv/'
  ```
  일곱 명령 모두 결과 0행이어야 함.
- **위반 시**: 스케줄 분산은 이중 실행·누락·미관찰 사고 직결. PR/커밋 잔존 시 머지 불가. 발견 즉시 이관, 예외 없음.
- **다음 작업자에게 전파**: 신규 시간 기반 자동 실행 추가하는 모든 작업은 *반드시* 이 규정 먼저 읽고 시작. JARVIS04 외 위치에 add_job·schedule.every·current_hour 폴링 본능적으로 가지 말 것.

## 자율 코드 자가수정 규정 (강제 — Phase 3 이후 자율 에이전트 코드·셸·구조 변경)
- **★ 최우선 — 인라인 버튼 ✅/❌ 우회 *절대* 금지 (사용자 직접 박제 — 영구)**: 외부 영향 도구·계획·위임은 *언제 어디서 누가 호출하든* 텔레그램 인라인 버튼이 *반드시* 송출되어야 함. 그 후 사용자 ✅ 통과 후에만 실행. *어떤 길로도 우회 0*. 자비스의 자율 판단은 *어떤 도구 쓸지·어떤 계획 만들지* 만 결정 — *실행 여부* 는 *항상 사용자*.
  - 검증 명령:
    ```bash
    # APPROVAL 도구가 daemon 콜백 외에서 직접 호출되면 위반
    grep -rnE 'tool_invoke\(' --include='*.py' . \
      | grep -v 'jarvis_daemon\.py:.*_run_tool_with_heartbeat\|_execute_plan\|_execute_j00_react_approval' \
      | grep -v 'shared/tools\.py' | grep -v __pycache__ | grep -v '\.venv/'
    # approved_context() 미사용 호출 (PermissionError 자동 차단되지만 회귀 방지)
    grep -rnE 'with approved_context' --include='*.py' . \
      | grep -v 'shared/tools\.py' | grep -v __pycache__ | wc -l   # 최소 2 (react·plan)
    ```
- **★ 진행 표시 의무 (사용자 직접 박제 — 영구)**: 외부 영향 도구·계획·위임이 실행되는 *모든 경로* 는 *반드시* 텔레그램에 진행 상황 표시.
  - 단발 도구 (delegate·call_jarvis* 등): `_run_tool_with_heartbeat()` 사용 — *60초마다 ⏳ N분 경과* + *완료 시 🎉 N분 N초 소요*. 15분 timeout 가드.
  - 계획 (create_plan): `_execute_plan()` — 단계마다 *⚙️ [i/N] 도구명 (소요시간)* + *✅ 완료 / ❌ 실패*. 마지막 *🎉 계획 완료 — N/M*.
  - 새 도구·콜백 추가 시 *반드시* 둘 중 하나 패턴 사용. 단순 `tool_invoke` 직접 호출 + 결과 한 번에 송출 금지.
  - 검증 명령:
    ```bash
    # 새 콜백이 _run_tool_with_heartbeat 또는 _execute_plan 통과하는지
    grep -rnE 'tool_invoke\(' --include='*.py' jarvis_daemon.py \
      | grep -v '_run_tool_with_heartbeat\|_execute_plan'
    # → 결과 0행 (모든 호출이 두 패턴 안에서만 일어나야)
    ```
- **★ 절대 — 모든 변경은 사용자 승인 후만**: write_file·edit_file·run_bash·register_new_*·create_new_agent 모든 *external* 도구는 텔레그램 인라인 버튼 ✅ 통과 후만 실행. LLM 의 *직접* 호출 금지 — 반드시 `create_plan` 으로 계획 수립 후 한 번에 사용자 승인.
- **계획 우선 패턴 강제**: 코드 수정·셸 실행 같은 큰 작업은 *반드시* `create_plan(goal, steps)` 통해서. ReAct 가 직접 write_file 호출 금지 (REACT_SYSTEM_PROMPT 의 "큰 작업 — 계획 우선 패턴" 규칙 준수).
- **파일 안전 박스**: 모든 파일 도구는 `_safe_path()` 통과 — jarvis-agent 폴더 안만, `..` 탈출·심볼릭링크 거부, deny dirs (.venv·.git·__pycache__·shared/backups·chrome_profile) 차단.
- **셸 안전 박스**: `run_bash` 는 ① 화이트리스트 (python·pytest·pip·git·ls·cat·head·tail·wc·grep·find·echo·pwd·npm·node) 첫 토큰만 ② 위험 패턴 (rm -rf·sudo·>/·curl|sh·chmod 777 등) 정규식 차단 ③ 30초 timeout (최대 120) ④ cwd=jarvis-agent 강제. 우회 (sh wrapper·./script) 거부.
- **백업 의무**: write_file·edit_file 은 기존 파일을 `.bak` 로 자동 백업. 수정 후 syntax 오류 발생 시 자동 rollback.
- **자기 등록 안전망**: `register_new_*`·`create_new_agent` 는 ① 충돌 검사 (이미 있는 ID 거부) ② 자동 .bak ③ 추가 후 `ast.parse` 검증 → 실패 시 rollback. 데몬 재시작은 *수동* — 자동 재시작 없음.
- **검증 명령** (4종 — 모두 결과 0행):
  ```bash
  # ① 화이트리스트 외부 (run_bash 도구가 _BASH_WHITELIST 외 정의)
  grep -rnE '_BASH_WHITELIST' --include='*.py' . \
    | grep -v 'JARVIS01_MASTER/agent_tools.py' | grep -v __pycache__ | grep -v '\.venv/'
  # ② _safe_path 우회 — Path(...).read 직접 (agent_tools 외)
  grep -rnE 'Path\([^)]*\)\.(read_text|read_bytes|write_text|write_bytes)\(' --include='*.py' . \
    | grep -v 'JARVIS01_MASTER/agent_tools.py' | grep -v 'JARVIS04_SCHEDULER/' | grep -v __pycache__ | grep -v '\.venv/'
  # ③ subprocess.run 외부 (raw shell — 위험)
  grep -rnE 'subprocess\.(run|Popen|call)' --include='*.py' . \
    | grep -v 'JARVIS01_MASTER/agent_tools.py' | grep -v 'jarvis_daemon.py' \
    | grep -v 'performance_collector\|approval_bot\|radar_main\|post_quality\|revise_adapter\|auto_repair' \
    | grep -v __pycache__ | grep -v '\.venv/'
  # ④ create_plan 우회 — write_file/edit_file/run_bash 가 LLM 응답에 *직접* 등장
  # (REACT_SYSTEM_PROMPT 검증 — 위 도구는 plan steps 안에서만 호출되어야 함)
  grep -rnE 'create_plan' JARVIS01_MASTER/router.py
  ```
- **위반 시**: 자율 에이전트가 사용자 미인지 변경 → 신뢰 즉시 붕괴, 데이터 손실 위험. 발견 즉시 차단·롤백, 예외 없음.
- **다음 작업자에게 전파**: 새 *external* 도구 추가하는 모든 작업은 *반드시* 이 규정 먼저 읽고 시작. 안전 박스·승인 게이트·rollback 모두 갖춰야 머지 가능.

## 자율 에이전트 도구·승인 게이트 규정 (강제 — Phase 2-B 이후 모든 LLM 도구 호출)
- **★ 최우선 — 사용자 승인 게이트 절대 우회 금지**: 외부 영향 (`side_effect="external"`) 도구는 *언제 어디서 누가 호출하든* 사용자 텔레그램 인라인 버튼 ✅/❌ 통과 후에만 실행. 자유 문장 자동 라우팅·ReAct·슬래시 명령·subprocess·CLI — *모든 진입점* 동일. 이 규정은 자율 에이전트 시스템의 *근본 원칙* — 사용자가 모르는 사이 외부 발행·과금·복구 불가 행동 발생 시 신뢰 즉시 붕괴.
- **도구 단일 등록 진입점**: 라우터(JARVIS01) 가 호출 가능한 모든 도구는 `shared/tools.py` 의 `@register_tool` 데코레이터로만 등록. 직접 LLM `bind_tools` 에 raw 함수 박지 말 것. ToolMeta (domain·side_effect·rollback·cost·requires_approval) 누락 금지.
- **JARVIS01 도구 카탈로그 단일 파일**: 마스터 라우터용 도구는 `JARVIS01_MASTER/agent_tools.py` 에 *모두* 박힘. 새 도구 추가 시 ① 거기에 `@register_tool` ② `core_agent.py` CAPABILITIES.tools 에 이름 추가 ③ `ensure_loaded()` expected set 갱신.
- **side_effect 필수 분류**: 도구는 셋 중 하나 — `none` (조회) / `internal` (DB 쓰기) / `external` (외부 API·발행·메일·결제). external 은 *반드시* `requires_approval=True`. 누락 시 라우터가 무방비로 발행을 시작 → 사고.
- **APPROVAL 도구 호출 경로 단일화**: APPROVAL 도구는 `react_handle()` 의 `pending_approvals` 로만 노출 → `_PENDING_J00_REACT` 보관 → 텔레그램 인라인 버튼 (`j00r_yes` / `j00r_no`) 콜백 → `_execute_j00_react_approval()` 에서 `tool_invoke()` 실행. *어떤 다른 경로로도* APPROVAL 도구 자동 실행 금지. `auto_approve=True` 는 *테스트 전용*, 운영 코드에 박지 말 것.
- **max_steps 한도**: `react_handle(max_steps=N)` 는 폭주 방지 안전망. 기본 4. *절대 무한·생략 금지*. 같은 도구 반복 호출 의심 시 prompt 보강 (REACT_SYSTEM_PROMPT) — max_steps 늘려서 회피 금지.
- **사용자 자연어 승인 — 텔레그램이 유일한 게이트**: 데몬 외부에서 APPROVAL 도구 직접 호출 금지. CLI·subprocess 도 동일. 사용자가 *볼 수 없는* 경로로 외부 영향 발생하면 자율성 통제 상실.
- **검증 명령** (3종 — 모두 0행이어야 함):
  ```bash
  # ① register_tool 외부 정의 (shared/tools.py·JARVIS01_MASTER/agent_tools.py 만 합법)
  grep -rnE '@register_tool\(' --include='*.py' . \
    | grep -v 'shared/tools.py' | grep -v 'JARVIS01_MASTER/agent_tools.py' | grep -v __pycache__
  # ② external side_effect 인데 requires_approval 누락
  grep -rnE 'side_effect="external"' --include='*.py' . -A 3 \
    | grep -B 1 'requires_approval=False' | grep -v __pycache__
  # ③ auto_approve=True 운영 잔존 (테스트 파일 제외)
  grep -rnE 'auto_approve=True' --include='*.py' . \
    | grep -v 'test_' | grep -v __pycache__
  ```
- **새 도구 도입 절차**: ① side_effect/requires_approval 결정 → ② agent_tools.py 에 추가 → ③ ensure_loaded() 갱신 → ④ core_agent.py CAPABILITIES.tools 갱신 → ⑤ REACT_SYSTEM_PROMPT (`router.py`) 의 도구 사용 원칙에 추가 → ⑥ 본 CLAUDE.md 의 검증 명령 통과 확인. *한 단계라도 빠지면 도구 미등록·승인 게이트 우회 위험*.
- **★ 새 capability/intent 추가 절차 (강제 — ERRORS [29] 재발 방지)**:
  1. `*_agent.py` 에 `declare(intents=[...])` 박기.
  2. `JARVIS01_MASTER/dispatchers.py` 의 `SAFE_INTENTS` 또는 `APPROVAL_INTENTS` 에 추가.
  3. SAFE 면 `execute_safe()` 에 분기 + 처리 함수 신설.
  4. APPROVAL 면 `_APPROVAL_META` 에 (title, detail_fn) 추가 + `_execute_j00_approval` 또는 전용 execute 함수 (예: `execute_schedule_change`) 분기.
  5. `JARVIS01_MASTER/intents.py ROUTER_SYSTEM_PROMPT` 에 자유 문장 → intent 매핑 규칙 명시 (자연어 키워드 → params 추출 가이드).
  6. 검증: 텔레그램에 자유 문장 보내서 ReAct 도 정상·fallback 도 정상인지 *둘 다* 확인. 한 경로만 OK 면 위반.
  ★ *fallback 1-step 분류기는 ReAct 미가용 시 fallback 으로 호출되는 항상 가용 경로*. 모든 capability 가 *주 경로와 동등* 하게 fallback 에서도 처리 가능해야 함. 한 경로라도 DEFERRED 처리되면 사용자 에러 직결.
- **위반 시**: APPROVAL 도구의 자동 호출은 사용자 의도 무시·과금·복구 불가능한 외부 발행 직결. PR/커밋 잔존 시 머지 불가. 발견 즉시 게이트 복원, 예외 없음.
- **다음 작업자에게 전파**: 신규 도구 추가하는 모든 작업은 *반드시* 이 규정 먼저 읽고 시작. side_effect 분류 누락은 규정 위반.



## 이미지 생성 권한 규정 (강제 — 절대 — 모든 이미지 생성 단일 진입점)
- **★ 절대 단일 진입점**: 시스템 내 *모든* 이미지 생성 (AI 사진·SVG·썸네일·소제목 배너·matplotlib 차트) 은 `JARVIS06_IMAGE` 가 단독 관리. 직접 URL 호출·`urllib.request.urlretrieve(https://image.pollinations.ai/...)`·`requests.get(https://image.pollinations.ai/...)` 는 *어떤 파일에서도* 절대 금지.
- **이미지 종류별 구현 규칙 (★ 신규 코드 추가 시 반드시 준수)**:

  | 구분 | 방식 | 동적 여부 |
  |------|------|---------|
  | **대표 썸네일** (테마글·경제·트렌드) | Claude가 배경 프롬프트 창작 → `generate_photo()` AI 사진 + Claude SVG 오버레이 | ★ 매번 다름 |
  | **본문 AI 사진** (섹션 사진 등) | `generate_photo(prompt_ko=...)` | ★ 매번 다름 |
  | **소제목 배너** (section_title.py) | matplotlib 텍스트 렌더링 — AI 불필요 | 고정 스타일 OK |
  | **섹션 구분 배너** (make_section_image) | matplotlib 텍스트 렌더링 — AI 불필요 | 고정 스타일 OK |
  | **데이터 차트** (경제·주식·시장 차트) | matplotlib 데이터 시각화 — AI 불필요 | 데이터 기반 |

- **AI 사진 폴백 체인 (고정 순서 — 변경 금지)**: Nanobana(Gemini)(1순위) → Pollinations.ai(2순위). 모든 `generate_photo()` 호출은 이 체인을 자동 사용. (★ Bing/HuggingFace 완전 삭제 — ERRORS [263] 사용자 박제 2026-06-07. 본 행은 2026-07-03 코드 실상과 동기화 — 문서 드리프트 수정)
- **동적 생성 원칙 (★ 썸네일 고정 스타일 절대 금지)**: 썸네일·AI 사진 생성 시 *고정 스타일 풀·hardcoded 레이아웃·STYLE_ZONES·THEME_PALETTES* 일체 금지. Claude LLM이 매번 배경 프롬프트와 SVG 오버레이를 새로 창작.
- **다른 파일 금지**: `JARVIS02_WRITER/*`, `JARVIS03_RADAR/*`, `JARVIS01_MASTER/*`, `shared/*`, 루트 `*.py` 어디에도 이미지 생성 *URL 호출·직접 provider import·새 matplotlib 이미지 함수* 를 추가하지 말 것. 예외 없음.
- **출력 경로 단일 진입점**: 모든 이미지는 `JARVIS06_IMAGE/output/` 하위에 저장됨. 기본 경로는 `image_agent.OUTPUT_DIR`. 용도별 하위 폴더:
  - `JARVIS06_IMAGE/output/images/economic_naver/` — 경제 브리핑 네이버
  - `JARVIS06_IMAGE/output/images/economic_tistory/` — 경제 브리핑 티스토리
  - `JARVIS06_IMAGE/output/images/theme_naver/` — 테마글 네이버
  - `JARVIS06_IMAGE/output/images/theme_tistory/` — 테마글 티스토리
  - `JARVIS06_IMAGE/output/images/theme_temp/` — 테마글 임시 (prepare_images 작업용)
  - `JARVIS06_IMAGE/output/screenshots/` — 네이버·티스토리 발행 스크린샷
  - `JARVIS06_IMAGE/output/thumbnails/` — 대표 썸네일
  - `JARVIS06_IMAGE/output/naver_images/` — 네이버 업로드 이미지
- **허용 호출 패턴** (다른 에이전트에서 이미지가 필요한 경우):
  ```python
  # AI 사진 (Bing → HuggingFace → Pollinations 폴백 자동)
  from JARVIS06_IMAGE.image_agent import generate_photo
  path = generate_photo(prompt_ko="한국어 설명", out_dir=img_dir)
  # 이미 영어 프롬프트가 있는 경우: prompt_en= 파라미터 사용

  # SVG/차트
  from JARVIS06_IMAGE.image_agent import generate_chart
  path = generate_chart(data={...}, chart_type="bar", title="제목")

  # 썸네일 (sector는 선택, out_dir 미지정 시 JARVIS06_IMAGE/output/ 사용)
  from JARVIS06_IMAGE.image_agent import generate_thumbnail
  path = generate_thumbnail(title="...", keyword="...", sector="", platform="naver")
  ```
- **발견 즉시 이관 — 의무**: 이미지 생성 코드를 `JARVIS06_IMAGE` 외 어떤 파일에서든 발견하면 *그 자리에서 즉시* JARVIS06 으로 이관. 미루지 말 것.
- **신규 이미지 기능 추가 절차**: ① `JARVIS06_IMAGE/` 안에 함수/파일 추가 → ② `image_agent.py` `__all__` 업데이트 → ③ 호출자는 위 허용 패턴으로 import.
- **검증 명령** (2종 — 모두 0행이어야 함):
  ```bash
  # ① Pollinations URL 직접 호출 (JARVIS06_IMAGE 외)
  grep -rnE 'https://image\.pollinations\.ai' --include='*.py' . \
    | grep -v 'JARVIS06_IMAGE/' | grep -v __pycache__ | grep -v '\.venv/'
  # ② Gemini ImageGenerationModel 직접 사용 (JARVIS06_IMAGE 외)
  grep -rnE 'ImageGenerationModel\(|imagen-[0-9]' --include='*.py' . \
    | grep -v 'JARVIS06_IMAGE/' | grep -v __pycache__ | grep -v '\.venv/'
  ```
- **위반 시**: 이미지 생성 분산은 폴백 체인 미작동·고정 스타일 반복·할당량 중복 소모 직결. 발견 즉시 이관, 예외 없음.
- **다음 작업자에게 전파**: 이미지를 생성하는 모든 신규 코드는 *반드시* 이 규정 먼저 읽고 `JARVIS06_IMAGE` 부터 시작.

## ★ 수집 단일 진입점 — JARVIS09_COLLECTOR (강제 — 절대 — 모든 데이터 수집)

**시스템 내 모든 데이터 수집은 `JARVIS09_COLLECTOR` 단독. 다른 에이전트는 직접 수집 금지.**

예외: JARVIS03 RADAR 자체 트렌드 수집 (pytrends·네이버 DataLab — RADAR 고유 영역).

**허용 호출 패턴 (다른 에이전트에서 수집이 필요한 경우):**
```python
from JARVIS09_COLLECTOR import (
    collect_research,       # ★ 설계-우선 리서치 수집 (ADR 012) — 근거팩+문서 반환. 발행 파이프라인 기본
    collect_for_theme,      # 주제 관련 텍스트 자료 (뉴스·블로그·학술·금융기사) — 광역 스윕
    collect_stocks_data,    # 테마 종목 데이터 (시세·재무)
    collect_chart_data,     # 차트용 실데이터 (ADR 010/011)
    evidence_brief,         # 근거팩 → 대본 프롬프트 브리프 (ADR 012)
    as_source_docs,         # 근거팩 → 사실성 게이트 대조군 어댑터 (ADR 012)
    get_market_data,        # 글로벌 시장 지표 (yfinance)
    get_economic_calendar,  # 경제 일정 (investing.com)
)
```
**★ 리서치 설계-우선 (ADR 012 — 사용자 박제 2026-07-02)**: 발행용 텍스트 수집은
`collect_research` 가 기본 — 설계(research_planner)→조준 수집→근거팩(evidence_pack)→
커버리지 갭 재수집 순환. 키 필요 소스 누락은 `source_onboarding` 이 감지·텔레그램 안내.
킬스위치 `RESEARCH_FIRST=0`.

**절대 금지 (다른 에이전트 내부):**
- `import yfinance`, `import pykrx`, `import FinanceDataReader` 직접 사용
- `requests.get(...)` / `urllib` 로 외부 데이터 수집 (이미지 다운로드 제외)
- `pytrends` 직접 사용 (JARVIS03 제외)
- 새 수집 로직을 JARVIS09 외부에 신설

**신규 수집 기능 추가 절차:**
① `JARVIS09_COLLECTOR/providers/` 에 새 Provider 추가 또는 기존 Provider 확장
② `JARVIS09_COLLECTOR/__init__.py` `__all__` 갱신
③ 호출자는 위 허용 패턴으로 import

**검증 명령** (모두 0행이어야 함):
```bash
# ① yfinance 직접 import (JARVIS09 외)
grep -rnE '^import yfinance|^from yfinance' --include='*.py' . \
  | grep -v 'JARVIS09_COLLECTOR/' | grep -v __pycache__ | grep -v .venv
# ② pykrx / FinanceDataReader 직접 import (JARVIS09 외)
grep -rnE 'import pykrx|import FinanceDataReader' --include='*.py' . \
  | grep -v 'JARVIS09_COLLECTOR/' | grep -v __pycache__ | grep -v .venv
# ③ requests.get 수집 목적 직접 호출 (이미지 다운로드·발행·쿠키 제외)
grep -rnE 'requests\.get\(' --include='*.py' . \
  | grep -v 'JARVIS09_COLLECTOR/' | grep -v 'JARVIS06_IMAGE/providers/' \
  | grep -v 'JARVIS08_PUBLISH/' | grep -v 'JARVIS00_INFRA/' \
  | grep -v 'tools/' | grep -v __pycache__ | grep -v .venv
```

### ★ 차트용 실데이터 + 무료 라이브러리 자동설치 (사용자 박제 2026-06-29 — ADR 010)

**차트/인포그래픽 수치는 JARVIS09 `collect_chart_data(theme, sector, description)` 가 수집한 실데이터로만.** JARVIS06 은 "데이터 줘"만 요청하고 JARVIS09 가 provider 선택·파싱·**출처(provenance) 박제**까지 한다. dataset 은 반드시 `source={provider,name,url,as_of}` 보유 — 사실성 검증의 근거.

- **무료 라이브러리 자동설치 단일 진입점**: `JARVIS09_COLLECTOR/lib_bootstrap.py` `ensure_lib(import_name, pip_name=None)`. **갯수 제한 없이** 필요한 무료 데이터 라이브러리를 승인 없이 `pip install` (internal 부트스트랩, 텔레그램은 *설치 알림*만). 고정 화이트리스트(캡)가 아니라 **안전 정책 게이트**로 통제 — ① 데니리스트(`_DENYLIST`) 아님 ② PyPI 공식 저장소 실존 ③ 상용 전용 라이선스 아님. 셋 다 통과하면 갯수 무관 설치.
- `_KNOWN_DATA_LIBS` 는 import명↔pip명 매핑 *편의표*(상한 아님 — 없어도 정책 통과 시 설치). 위험·무관 패키지는 `_DENYLIST` 로만 차단. 다른 곳에서 `subprocess pip install` 금지.
- precommit `autocode/subprocess` 허용 목록에 `JARVIS09_COLLECTOR/lib_bootstrap.py` 등재됨(예외조항).

## ★ 이미지 사실성 규정 (강제 — 절대 — ADR 010 / 사용자 박제 2026-06-29)

**"데이터가 들어가는 이미지는 절대 거짓된 데이터로 만들면 안 됨."** 대본은 `prepublish_gate` 사실성 게이트가, *차트 안의 수치* 는 이 규정이 막는다.

- **검증 단일 진입점**: `JARVIS06_IMAGE/validators/image_data_verifier.py` `verify_chart_spec(spec, datasets)`. 다른 파일에 차트 데이터 사실성 로직 박지 말 것.
- **정책 (검증분 재구성 → 대체 → 스킵)**: ① 텍스트 카드(숫자 없음)=면제 ② dataset 기반 spec=신뢰 ③ LLM 본문 추출 수치 = 실데이터 대조 → 검증 행만 재구성 / 0개면 실데이터로 대체 / 그것도 없으면 숫자 없는 카드로 폴백. *거짓 차트 < 차트 없음.*
- **트립와이어**: `render_from_spec` 이 모든 생성 이미지의 검증 결과를 provenance 레지스트리에 기록 (`record_provenance`). 수치 차트가 `verified` 없이 렌더되면 `verified=False`.
- **발행 전 이미지 게이트**: `prepublish_gate._image_factuality_leg` 가 `verified=False` 차트를 `kind="factuality"` 로 차단 → 재작성 순환. 킬스위치 `PREPUBLISH_IMAGE_GATE=0`.
- **검증 명령** (모두 0행 — owner 외부 정의 차단):
  ```bash
  # ① 차트 데이터 검증 로직 외부 정의 (image_data_verifier.py 만 합법)
  grep -rnE 'def verify_chart_spec' --include='*.py' . \
    | grep -v 'JARVIS06_IMAGE/validators/image_data_verifier.py' | grep -v __pycache__
  # ② collect_chart_data 본체 외부 정의 (JARVIS09 chart_data.py 만 합법)
  grep -rnE 'def collect_chart_data' --include='*.py' . \
    | grep -v 'JARVIS09_COLLECTOR/chart_data.py' | grep -v __pycache__
  ```

## ★ 블로그 글 규정 — BLOG_SUPREME_LAW.md 단일 진입점

**모든 블로그 글 작성·발행 관련 규정은 `JARVIS02_WRITER/BLOG_SUPREME_LAW.md` 에서 단독 관리된다.**

이곳에 블로그 관련 규정을 두지 않음. BLOG_SUPREME_LAW.md가 유일한 진실 소스:
- 제0조 ~ 제10조: 기본 규칙 (감성 도입부, 문단 구조, 독창성, 진실성, 플랫폼 적합성 등)
- 제11조: 차트/그래프 디자인 동적 생성
- 제12조: 같은 글 내 시각화 스타일 중복 금지

## 오류 관리 규정 (강제 — JARVIS07_GUARDIAN 도입 후)
- **★ 절대 단일 진입점**: 오류 수집·DB 저장·분석·자동 수정은 `JARVIS07_GUARDIAN` 단독 관리. 다른 에이전트에서 직접 DB 저장·파일 수정 금지.
- **수집 API**: 다른 에이전트 try/except 블록에서 `from JARVIS07_GUARDIAN.error_collector import report; report("agent_name", exc, module=..., func_name=...)` 호출 — 이것만 허용. 직접 `shared.db.save_error()` 호출 금지.
- **★ 수동 수정 기록 의무 (Claude·사용자 공통)**: Claude 또는 사용자가 *발견·수정한 결함* 은 *반드시* `from JARVIS07_GUARDIAN.error_collector import report_manual_fix` 로 박제. *런타임 오류* 가 아닌 *코드 결함 발견 작업* 도 기록. 이렇게 해야 수동수정 카드가 *진정한 작업량* 을 반영. 박제 안 하면 사용자 신뢰 손상 + 회고 불가.
  - 호출 예: `report_manual_fix(source="writer", fixed_file="JARVIS02_WRITER/foo.py", description="...", error_type="PromptLeak", severity="medium", actor="claude")`
  - 적용 대상: 프롬프트 누수·import 경로·NoneType 가드·정책 위반·코드 결함 등 *어떤 수동 수정도 예외 없이* 박제.
  - **★ 재발 가능한 런타임 오류 수정은 diff 동반 (2026-07-02 — 강화학습 사슬 연결)**: 실제 런타임 오류(NoneType·TypeError·import 깨짐 등)를 고쳤으면 `patch=<unified diff>`, `error_message=<실제 오류 메시지>`, `recurrable=True` 를 함께 넘긴다. 그러면 change-tracking 이 아니라 **actionable `llm_patch`** 로 등록(eval_agent Sonnet 5 게이트) → **Bandit(강화학습) 보상 발화** → 재발 시 LLM-0 재적용. diff 없이 부르면 종전대로 change-tracking(재발 개념 없는 정책/기능 변경). `_MANUAL_POLICY_TYPES`(PromptLeak·ExternalEdit·GitCommit 등)는 recurrable=None 자동 판정에서 actionable 제외.
- **★ 자동 수정 범위 — catch() 단일 진입점 + 2-Tier (★ 사용자 박제 2026-06-28 — 티어 정의 단일 진실 소스 = `JARVIS07_GUARDIAN/architecture.py`)**:
  - **catch() 단일 진입점**: 6개 메커니즘 (sys.excepthook·threading.excepthook·APScheduler·log_scanner·auto_catch·report) 으로 모든 오류 *직접* 캐치.
  1. **Tier 1 — 패턴 자동 수정** (`pattern_fixer.py`, LLM 호출 0): static 코어 6종 + 학습 패턴(`learned_patterns.json`, hit_count 누적) + **Contextual Bandit (Linear UCB, `bandit.py`)** 가 시도 순서 랭킹. Group 1(static 6 + hit≥3) · Group 2(신규 hit 1~2).
  2. **Tier 2 — LLM 폴백** (`error_analyzer.py` / `auto_repair.py`, Sonnet 5) — Tier 1 실패 시만. critical 은 Tier 2 생략(수동 검토).
  - 티어 번호는 *정수, 1부터*. Tier 0·Tier 1.5·Tier 2.5 표기 금지.
- **★ 자가 학습 — 모든 수정 사례 영구 자산화**: `error_fixer.apply_fix` 성공 후 + `report_manual_fix` 호출 시 → `pattern_fixer.record_pattern_hit()` 자동 호출. fingerprint = `error_type::normalized_message`. 새 패턴이면 등록, 기존이면 hit_count++. 시간 갈수록 자동 수정 비율 증가, LLM 호출 감소.
- **★ 외부 변경 자동 박제 (3-layer)**: jarvis-agent 외부에서 발생한 코드 변경도 학습 자산화 의무.
  1. **Layer A — auto_repair (즉시)**: `JARVIS07_GUARDIAN/auto_repair.py` Claude Code SDK 자가수정 성공 시 `_record_repairs_to_guardian()` 가 `---REPAIR-SUMMARY---` 블록 파싱 → 각 수정 항목별 `record_external_change()` 자동 호출. `source="auto_repair"`.
  2. **Layer B — git daily 회고 (D-1 박제)**: `j07_git_audit` 잡 매일 03:30 — `git log --since=24h` 의 커밋 변경 파일 (.py/.md/.json/.yml) → `record_external_change()` 자동 박제. VS Code Claude Code·사용자 직접 편집·외부 도구 변경 모두 captured.
  3. **Layer C — Cowork Claude (의무 호출)**: Cowork 환경에서 Claude 가 코드 수정 시 *반드시* `report_manual_fix()` 명시적 호출. 잊으면 학습 누락 → 동일 사고 재발 시 자동 처리 불가.
- **`record_external_change()` API**: `from JARVIS07_GUARDIAN.error_collector import record_external_change`. severity 기본 'low' (외부 변경은 정상 작업). actor 식별자(`vscode_claude`/`git_audit`/`auto_repair`/`user_edit`). commit_hash 옵션 (git 추적).
- **새 패턴 추가 절차**: `pattern_fixer.py` 의 `_fix_<name>(error_record)` 함수 + `_PATTERN_FIXERS` + `_FIXER_REGISTRY` 갱신 + `severity._PATTERN_FIXABLE_TYPES` 갱신. 가상 traceback 단위 테스트 후 머지.
- **학습 상태 조회**: `from JARVIS07_GUARDIAN.pattern_fixer import stats; stats()` → `total_patterns`·`total_hits`·`by_fixer`·`top5`.
- **자동 승인 설계**: `guardian.apply_fix()` 는 Telegram 인라인 버튼 없이 자동 실행. 사유: 파일 변경이 `jarvis-agent` 폴더 내부(`side_effect="internal"`)이므로 CLAUDE.md 자율 코드 자가수정 규정의 외부 영향 게이트 적용 제외. **패치 크기 무제한으로 모든 오류 자동 수정** (critical 심각도 오류 실패 시에만 Telegram 알림).
- **ERRORS.md 병행 — 자동**: `error_fixer.apply_fix()` 성공/실패 시 자동으로 ERRORS.md 에 항목 추가. 사용자·다른 에이전트가 수동 추가하는 것도 허용 (중복 기록 무방). ERRORS.md 선행 읽기 의무(루트 규정)는 여전히 유효 — guardian 자동 기록이 수동 검토를 대체하지 않음.
- **심각도 분류 단일 진입점**: `JARVIS07_GUARDIAN/severity.py` 의 `classify()` / `is_auto_fixable()` 만 사용. 다른 파일에 severity 판단 로직 박지 말 것.
- **쿨다운 정책**: 동일 오류(`source:module:error_type:message[:80]` 키) 60초 내 재수집 방지 — 메모리 캐시 (`error_collector._cooldown`). DB 레벨 dedup 은 1시간 (같은 type+module 조합).
- **로그 스캐너 감시 대상**: 기본 `JARVIS02_WRITER/logs/*.log`. 신규 에이전트가 로그 파일을 생성하면 `init_log_scanner(log_dir=Path(...))` 로 추가 등록.
- **자동 수정 안전 박스**: ① `_DENY_DIRS` (.venv/.git/__pycache__/shared/backups/chrome_profile/logs) 경로 차단 ② `.py` 파일만 `ast.parse` 검증 ③ `.bak` 자동 백업 ④ `importlib` import 테스트 → 실패 시 자동 롤백 ⑤ 패치 크기 무제한.
- **검증 명령**:
  ```bash
  # ① save_error 직접 호출 (error_collector 외)
  grep -rnE 'db\.save_error\(' --include='*.py' . \
    | grep -v 'JARVIS07_GUARDIAN/' | grep -v 'shared/db.py' | grep -v __pycache__ | grep -v '\.venv/'
  # ② severity 판단 로직 외부 잔존
  grep -rnE '"critical"\s*\|\|\s*"high"|severity\s*==\s*"critical"' --include='*.py' . \
    | grep -v 'JARVIS07_GUARDIAN/' | grep -v __pycache__ | grep -v '\.venv/'
  ```
- **위반 시**: 오류 수집 분산 → DB 중복·자동 수정 충돌·ERRORS.md 불일치. 발견 즉시 이관, 예외 없음.
- **다음 작업자에게 전파**: 신규 에이전트에서 예외 처리 시 *반드시* `report()` API 사용. `try/except: pass` 또는 `log.error()` 만으로 끝내지 말 것.

## 자가 학습 엔진 — 세상에서 가장 똑똑한 에이전트 비전 (★ 사용자 박제 2026-05-15)

**비전**: 시간이 지날수록 *스스로 똑똑해지는* 에이전트. 매 회차 진단·수정 결과가 *영구 자산*으로 누적되어 다음 회차에 활용되는 *폐쇄 학습 루프* 구축.

### ★ 단일 진입점 — JARVIS07 GUARDIAN 이 모든 자가 학습 책임 (사용자 박제 2026-05-15)

**자가 학습 관련 모든 코드·데이터·로직은 `JARVIS07_GUARDIAN/` 폴더 안에서 단독 관리**. 다른 에이전트가 자가 학습 로직 새로 추가 절대 금지 — 발견 즉시 JARVIS07 으로 이관.

| 파일 | 역할 |
|------|------|
| `JARVIS07_GUARDIAN/auto_repair.py` | 자가 진단·수정 엔진 (Claude Code SDK Sonnet 5) |
| `JARVIS07_GUARDIAN/eval_agent.py` ★ | **A모델 분리** — 수정 결과 평가 + learned_patterns 등록 게이트 (정적 자동/llm Sonnet 5) |
| `JARVIS07_GUARDIAN/auditor.py` ★ | **A모델 분리** — 헌법 위반·드리프트 검출 + Refine Rules 제안 (주 1회) |
| `JARVIS07_GUARDIAN/guardian_agent.py` | GUARDIAN 데몬 통합 진입점·잡 등록 |
| `JARVIS07_GUARDIAN/error_collector.py` | 오류 수집·DB 저장 API |
| `JARVIS07_GUARDIAN/error_analyzer.py` | 2-Tier 분석 (Tier 1 패턴·Bandit → Tier 2 LLM) |
| `JARVIS07_GUARDIAN/error_fixer.py` | 자동 수정 실행·안전 박스 |
| `JARVIS07_GUARDIAN/pattern_fixer.py` | 정적 fixer + learned_patterns 학습 누적 (eval_agent 게이트 통과 후 등록) |
| `JARVIS07_GUARDIAN/severity.py` | 심각도 분류·자동수정 가능 판정 |
| `JARVIS07_GUARDIAN/quality_learner.py` ★ | **글 품질 강화학습 단일 진입점 (ADR 014)** — UCB 선택·사용기록·보상 귀속·weight EMA 갱신. 작성기는 `build_insights_block()` 만 호출 |
| `JARVIS07_GUARDIAN/learned_patterns.json` | 학습된 fingerprint 영구 보관 (각 entry 에 `eval_meta` 점수 박제) |
| `JARVIS07_GUARDIAN/project_audit_log.json` | 정책 작업 박제 (학습 대상 아님) |
| `JARVIS07_GUARDIAN/ERRORS.md` | 오류 기록·교훈 단일 진실 소스 |

**공용 자원** (다른 에이전트와 공유):
- `shared/db.py` — `self_repair_runs` / `error_log` 테이블 (공용 DB)
- `JARVIS04_SCHEDULER/job_registry.py` — 잡 등록 (callback 경로: `JARVIS07_GUARDIAN.auto_repair.job_auto_repair`)
- `api_server.py` (:9198) — `/api/errors`·`/api/guardian/*`·`/api/learning`·`/api/repairs` 라우트
- `dashboard/app/{errors,learning}/` (:9199) — 오류·학습 페이지 (Next.js)

**역사적 위치 (이관 완료)**:
- 옛: `JARVIS01_MASTER/auto_repair.py` → **삭제** (2026-05-15)
- 옛 callback: `JARVIS01_MASTER.auto_repair.*` → `JARVIS07_GUARDIAN.auto_repair.*`

### A모델 분리 — 진단↔평가↔감사 3단 (2026-05-16 박제)

ADR 007 [Self-Evolving Harness 비전](docs/decisions/007-self-evolving-harness.md) 의 *진화 단계 A 적용*. 책임 경계:

| 컴포넌트 | 책임 | 주기 |
|---------|------|------|
| `pattern_fixer` (Tier-1 sweep) | 발행 전 미해결 오류 LLM-0 소급 수리 *만* | 발행 직전 (06:30·16:00 callback) |
| `auto_repair` + backlog Tier-2 | 광범위 코드 감사 + backlog 진단·수정 | 새벽 04:30 `job_deep_audit` (★ 사용자 박제 2026-06-28 — 발행과 분리) |
| `eval_agent` ★ | 수정 결과 평가 + learned_patterns 등록 *게이트* | 수정마다 즉시 |
| `auditor` ★ | 헌법 위반·드리프트 검출 + Refine Rules 제안 | 주 1회 (일 04:30) |

게이트 의무: `pattern_fixer.record_pattern_hit()` 는 *반드시* `eval_agent.evaluate()` 통과 후만 등록. 정적 fixer 5종은 자동 통과(score=95, tier=static), `llm_patch` 는 Sonnet 5 (learn_eval alias) 으로 안전성·정확성·재사용 가치 3축 채점 후 80+ 만 통과. 거부 시 텔레그램 알림.

### 학습 엔진 3계층

#### 계층 1 — 자가 진단·수정 (발행 전 Tier-1 sweep + 새벽 심층 감사 — ★ 사용자 박제 2026-06-28)
- **발행 전 (LLM-0)**: `guardian_agent.self_heal_known_errors()` — 미해결 오류(`new`·`wontfix`) 중 학습 패턴·정적 fixer·Bandit 로 *즉시 고칠 수 있는 것만* 소급 수리. *외부 LLM 호출 0*. 못 고치면 새벽 심층 감사로 위임.
  - Callback: `JARVIS02_WRITER.scheduler.run_self_repair_then_economic` / `run_self_repair_then_theme` → `_run_self_repair_phase` 안에서 호출.
  - 흐름: ① 쿠키 점검 → 실패 시 발행 건너뜀 ② Tier-1 sweep (수초) → ③ 코드 변경 시 "데몬 재시작 권장" TG 알림 → ④ 발행 진입
- **새벽 04:30 심층 감사 (LLM)**: `guardian_agent.job_deep_audit` (DEFAULT_JOBS `j07_deep_audit`). 발행과 분리 → 발행 지연 0.
  - ① `deep_audit_backlog()` — 미해결 오류 Tier 1 → Tier 2(LLM). ★ Tier 2 도 `apply_fix` 경유 *실제 오류 지문* 으로 학습 (AutoRepairFix 합성 지문 아님) → 다음 sweep 이 재사용 → 밴딧 학습 (복리 루프).
  - ② `auto_repair.run_auto_repair()` — 광범위 코드 감사 (새 잠재 버그 발굴·수정).
- **즉시 반영 vs 데몬 재시작**: 코드 수정은 Python import 캐시 때문에 *현재 데몬 프로세스 무효* → 다음 데몬 재시작 후 발효.
- **심층 감사 모델**: Sonnet 5 (`auto_repair._MODEL = "claude-sonnet-5"` — ★ 사용자 박제 2026-07-06 (ADR 017): 모든 LLM 호출 Sonnet 5 단일 통일, ADR 015(Opus 4.8 2계층) 폐지. ERRORS [184]: 정확한 모델 ID 명시, alias 금지 원칙은 모델 무관 유지)
- **전체 코드 검토 3단계** (auto_repair.py `_BASE_PROMPT` — ★ 2026-05-30 8 Layer 폐지 → 단순화):
  1. **Syntax 전수 검사** — `find . -name "*.py" | xargs python -m py_compile` 전체 파일
  2. **핵심 규정 위반 grep** — APScheduler 외부 사용 / schedule 라이브러리 / 글자수 하드코딩 / 폐기 model ID
  3. **파일별 정밀 검토** — import 깨짐 / 명백한 버그 (NoneType 슬라이싱·중복 함수) / dead code

#### 계층 2 — 학습 자산 누적 (learned_patterns.json + DB)
- **learned_patterns.json**: 자동/수동 수정 fingerprint 영구 보관
  - 노이즈 게이트 3종 (`record_pattern_hit` 안) — `fixer_name` 없음 / message 빈 채로 / 정책 작업 타입
  - `_normalize_message()` 7종 placeholder (ADDR / TIMESTAMP / DATE / TIME / TMP_PATH / PATH / BIGINT)
  - hit_count 누적 → 같은 fingerprint 재발 시 *LLM 호출 0 즉시 fix*
- **self_repair_runs DB 테이블** (shared/db.py): 회차별 메트릭 영구 박제
  - 수정 카운트 (syntax_fixed / rules_fixed 등 — 호환 컬럼 유지)
  - 학습 누적 (patterns_count / hits_total / llm_saved)
  - 시계열 추적 → 학습 곡선 정량 지표

#### 계층 3 — 학습 가시화 (`dashboard/` Next.js :9199)
- **`/errors`·`/learning` 페이지** 에 2개 카드:
  1. **🤖 자가 진단 학습 곡선** (최근 10회) — 회차/시각/수정/패턴/절약/점수/다음 회차 테이블 + KPI 4종 (총 회차 / LLM 절약 추세 / 패턴 증가 / 평균 수정)
  2. **🧠 학습 시스템 — LLM 호출 절약 효과** — 학습 패턴 / LLM 호출 절약 / 정적·LLM 분포 + Top 5 패턴

### 폐쇄 학습 루프

```
[오류 발생] → [catch() 단일 진입점] → [Tier 1 패턴·Bandit] → [Tier 2 LLM]
                                      ↓
                            [fix 성공] → [learned_patterns 자동 등록]
                                              ↓
                          [다음 회차 자가 진단 시 pattern_fixer 점검]
                                              ↓
                  [노이즈 정리 / 반복 패턴 → 새 _fix_*() 자동 신설]
                                              ↓
                          [LLM 호출 0 — 즉시 fix 가능 영역 확장]
                                              ↓
                          [self_repair_runs 메트릭 누적]
                                              ↓
                     [대시보드(:9199) /learning 학습 곡선 표시]
                                              ↓
                          [사용자가 학습 효과 *눈으로* 확인]
```

### 신규 작업자 의무

- **자가 진단 prompt 변경**: `auto_repair._BASE_PROMPT` 의 3단계 구조 유지. 검토 범위 추가 시 3단계 안에서 확장.
- **learned_patterns 신규 등록 경로**: 반드시 `record_pattern_hit(error_record, fixer_name=...)` 사용. *fixer_name 없는 등록 절대 금지* (3종 노이즈 게이트 차단됨).
- **메트릭 신규 컬럼**: `self_repair_runs` 테이블 변경 시 `_save_run_to_db` 의 INSERT + `api_server.py` `/api/repairs` 의 SELECT 동시 갱신.
- **검증 명령**: 자가 진단 회차 후 텔레그램에 *학습 추세* 자동 표시 (`_learning_trend_brief`) — 점수 하락 시 사용자 즉시 인지.

### 위반 시
- 전체 코드 검토 범위 축소 / 회차 메트릭 누락 / 학습 데이터 노이즈 잔존 → *학습 효과 정체*.
- 발견 즉시 이 헌법으로 회귀 점검 후 보완.

## ★ 하네스 게이트 시스템 — 검증 순환 → 송출 (모든 동작 공통) (사용자 박제 2026-05-17 v2 — ADR 009)

**비전 (불변 원칙)**:
1. **송출 = 완료 표시**. 외부 도달까지 *포함*된 단일 종착 상태.
2. **결함 있는 결과물은 *영원히 송출되지 않는다***. 검증 순환 안에서만 수정.
3. **송출 후 "실패"라는 개념은 존재하지 않는다**. 외부 응답 실패 = 송출 미완료 = 검증 순환 재진입.
4. **모든 명령·트리거·동작에 동일 적용** (블로그·영상·텔레그램·자유 문장·API — 트리거 무관).

[ADR 009 v2](docs/decisions/009-self-evolving-harness-gates.md) 단일 진실 소스 — 본 섹션은 *적용 규칙* 만 박제.

### ★ "하네스" 어휘 두 층위 (혼동 방지)

| 어휘 | 의미 | 위치 |
|------|------|------|
| "Self-Evolving Harness 비전" (광의) | 전체 자가 학습 시스템 — *우산* | ADR 007 |
| `harness.py` (협의) | Layer 1~4 흐름 엔진 | `JARVIS00_INFRA/harness.py` |

**부분품 구성**: 광의 비전 = `JARVIS00_INFRA/{preflight.py, harness.py}` (흐름 *골격*) + `JARVIS07_GUARDIAN/` (학습 *두뇌*). 두 부분품은 *수직 협력*: harness 의 Layer 3 검증 실패 시 `error_collector.report()` 자동 호출 → GUARDIAN 2-Tier 수정 → 재실행. *직접 GUARDIAN 호출 금지* — harness 가 자동 위임.


### 5 Layer 구조 — 모든 동작 공통

| Layer | 책임 | 구현 단일 진입점 | 실패 시 |
|-------|------|----------------|--------|
| **0** | 시스템 부팅·환경 검증 (도메인 무관, 부팅 1회) | `JARVIS00_INFRA/preflight.py` | GUARDIAN report + 텔레그램 + `sys.exit(1)` |
| **1** | 동작 입력·전제조건 (의도·권한·리소스) | `harness.py` `precondition` hook | 검증 순환 진입 |
| **2** | 수행 단계 실행 (산출물 생성, 동작별 N개 단계) | `harness.py` `@action_step` 시퀀스 | 검증 순환 진입 |
| **3** | **★ 결과 전체 검증 순환** (문제 0까지 반복) | `harness.py` `verify_loop` (★ 핵심) | max_attempts 도달 시 GUARDIAN escalation + 송출 안 함 |
| **4** | 송출 — 외부 도달까지 *포함* | `harness.py` `send()` 콜백 (검증 통과 후만 호출) | 외부 응답 실패 시 *송출 미완료* — Layer 3 재진입 |

★ Layer 5 *없음* — 송출이 종착. *송출 후 실패 개념 부재*.

### 동작 종류별 인스턴스 예시

| 동작 | 트리거 | Layer 2 (수행 단계) | Layer 4 (송출) |
|------|--------|--------------------|----------------|
| 블로그 발행 | Cron 07:00 | 데이터→글→이미지→헌법→DB 마커 | 플랫폼 발행 (네이버·티스토리) |
| 영상 제작 | 자유 문장 | 스크립트→렌더→압축→자막 | 유튜브 업로드 |
| 자유 문장 ("대시보드 추가") | 텔레그램 | 의도→코드→테스트→diff | 파일 적용 + 응답 |
| 텔레그램 /status | 명령 | 분류→권한→상태수집→포맷 | 메시지 전송 |
| 로그인 확인 | 사용자 | 쿠키→유효성→실로그인→분석 | 보고서 전송 |

### 신규 작업자 의무

- **새 동작 추가 시 *반드시* `harness.py` 표준 사용**: `@action_step` 데코레이터 + `ActionDefinition` + `run_action()`. 직접 송출 행위 (외부 API·파일 쓰기·텔레그램 전송) 호출 금지 — *반드시* Layer 4 `send` 콜백 통과.
- **Layer 0 (preflight)**: `JARVIS00_INFRA/preflight.py` 단독 관리. 새 필수 import / 외부 의존 / 환경변수 / DB 테이블 추가 시 preflight 항목 동시 갱신.
- **Layer 3 검증 실패는 GUARDIAN 진입점 단일화**: `error_collector.report(source="harness", context={"layer":3, "action":..., "step":..., "issue":...})`. 직접 DB / 로그 / 텔레그램으로 끝내지 말 것.
- **무한 루프 방지**: `max_attempts` 박제 (기본 3회 — ★ 사용자 박제 2026-07-06: 어떤 재시도도 최대 3회). max 도달 시 GUARDIAN escalation + 사용자 텔레그램 + *송출 절대 안 함*.

### 마이그레이션 (Phase 2 — 기존 동작 → harness 표준)
- *Phase 1 (즉시 적용)*: `harness.py` 표준 인프라 신설 ✅
- *Phase 2 (phased — 각 동작별 사용자 승인)*: 블로그 발행 → 자유 문장 ReAct → 텔레그램 명령 → 이미지·로그인 등
- 마이그레이션 *전* 의 기존 코드 호환을 위해 `precommit_check --category harness` 는 *경고 수준* 시작. 마이그레이션 진행 후 *strict 전환*.

### 검증 명령
```bash
python3 shared/precommit_check.py --category preflight   # Layer 0 외부 정의 + main() 호출
python3 shared/precommit_check.py --category harness     # harness 외부 정의 차단 + 송출 우회 검출
```

### 위반 시
- 검증 순환 우회 코드 (검증 안 거치고 외부 영향 직접 호출) → *송출 후 실패 개념 도입* = 사용자 비전 정면 위반.
- 발견 즉시 `harness.py` `send` 콜백으로 이관, 예외 없음.
