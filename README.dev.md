# JARVIS — 팀원 개발 환경 셋업 (Windows + WSL2 Ubuntu)

이 문서는 *팀원* 개발 환경 가이드. **운영 데몬은 사용자 macOS 1곳에서만** 실행 — 팀원은 *코드 작업·테스트·PR 만*.

---

## 0. 역할 분리 (먼저 이해)

| 역할 | 사용자 mac | 팀원 Ubuntu |
|------|-----------|-------------|
| 운영 데몬 (`python jarvis_daemon.py`) | ✅ 유일 실행 | ❌ 절대 실행 금지 |
| 발행 (네이버·티스토리 Selenium) | ✅ | ❌ 환경 불가 |
| APScheduler cron (07:00·16:00) | ✅ | ❌ |
| 학습 자산 누적 (`learned_patterns.json`·`ERRORS.md`) | ✅ 단일 소스 | ❌ 읽기만 |
| 코드 작성·수정 | ✅ | ✅ |
| 단위 테스트·py_compile | ✅ | ✅ |
| LLM 호출 테스트 (Claude CLI) | ✅ | ✅ |
| Git PR 작업 | ✅ | ✅ |

**핵심 원칙:** 팀원은 *데몬을 절대 띄우지 않음*. 발행 사고·학습 자산 오염 차단.

---

## 1. 환경 셋업 (5단계)

### 1-1. 저장소 클론

```bash
git clone <repo-url> jarvis-agent
cd jarvis-agent
git checkout -b feature/<your-task-name>   # 절대 main에 직접 작업 금지
```

### 1-2. Python 가상환경 (Python 3.10.x)

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt   # 없으면 `pip freeze > requirements.txt` 를 사용자에게 요청
```

### 1-3. claude CLI 설치 (Claude Code SDK 호출용)

Anthropic Claude Code 설치 후 OAuth 인증:

```bash
# 설치 (npm 전역)
npm install -g @anthropic-ai/claude-code

# OAuth 로그인 (브라우저 자동 열림)
claude auth login

# 확인
claude --version
which claude   # /home/<user>/.npm-global/bin/claude 또는 /usr/local/bin/claude
```

### 1-4. `.env.local` 생성

`.env.example` 복사 후 *자기 토큰*으로 채움:

```bash
cp .env.example .env
nano .env   # 또는 vim/code
```

**중요:** 운영 토큰 (사용자 네이버·티스토리 쿠키, telegram bot) *복사·사용 금지*. 팀원 환경은 LLM 호출만 가능하면 충분.

### 1-5. 검증

```bash
python shared/precommit_check.py        # 정책 11카테고리 검증 — 0건이어야 함
python -m pytest tests/ -v              # 단위 테스트 (있는 경우)
python -c "from shared.llm import invoke_text; print(invoke_text('writer_fast', 'Say hi'))"
```

---

## 2. 운영 학습 자산 — 절대 편집 금지

**다음 파일들은 `.gitignore` 되어있고 *팀원 환경에서는 빈/구버전* 상태가 정상:**

- `JARVIS07_GUARDIAN/learned_patterns.json` — 패턴 학습 누적
- `JARVIS07_GUARDIAN/learned_incidents.json` — incident 학습
- `JARVIS07_GUARDIAN/project_audit_log.json` — 정책 작업 박제
- `JARVIS03_RADAR/data/` — 트렌드 수집·캐시
- `JARVIS02_WRITER/economic_used_tags.json` — 사용 태그 누적
- `JARVIS02_WRITER/scheduler_progress.json` — 스케줄러 상태
- `shared/jarvis.sqlite` — 공용 DB

팀원 환경에서 이 파일들이 *변경되거나 생성되어도 무시*. git이 이미 추적 안 함.

---

## 3. ERRORS.md 정책 — 정책 A (사용자 박제 2026-06-07)

`JARVIS07_GUARDIAN/ERRORS.md` 는 CLAUDE.md 규정상 **단일 진실 소스** — 팀원도 *읽어야* 하는 문서. 그래서 git 에 *유지*. 단:

### 팀원 규칙

- ✅ **읽기만 가능**. 새 오류·해결 추적할 때 *우선 검토 의무*.
- ❌ **직접 편집 금지**. 손으로 수정하지 말 것.
- ❌ **PR 에 `ERRORS.md` 변경 포함 금지**. 머지 거부 사유.
- ✅ 새 오류·해결책 박제 필요 시 — *사용자에게 전달*. 사용자가 운영 데몬 또는 Cowork Claude 에서 `report_manual_fix()` 호출로 추가.

### 왜 이 규칙인가

- 운영 데몬·Cowork Claude 가 `report_manual_fix()` 호출 시마다 자동 갱신 → 양쪽 동시 편집 시 *merge conflict 폭발*.
- 이 파일은 *학습 자산의 일부*. 팀원 수동 편집은 자가 학습 시스템 신뢰성 손상.

### pre-commit 가드 (권장)

`.git/hooks/pre-commit` 에 아래 추가:

```bash
#!/bin/bash
if git diff --cached --name-only | grep -q "JARVIS07_GUARDIAN/ERRORS.md"; then
    echo "❌ ERRORS.md 직접 편집 금지 (README.dev.md 정책 A)"
    echo "   새 오류는 사용자에게 전달 → report_manual_fix() 호출로만 박제"
    exit 1
fi
```

---

## 4. Branch · PR Workflow

| 브랜치 | 용도 | 푸시 권한 |
|--------|------|-----------|
| `main` | 운영 — 사용자 데몬이 실행하는 코드 | 사용자만 (PR 머지) |
| `dev` | 통합 테스트 | 사용자 |
| `feature/<task>` | 팀원 개별 작업 | 팀원·사용자 |

**팀원 작업 흐름:**

1. `git checkout main && git pull` (최신 상태)
2. `git checkout -b feature/<task>` (새 브랜치)
3. 작업 + commit (작게·자주)
4. `python shared/precommit_check.py` 통과 확인
5. `git push origin feature/<task>`
6. GitHub 에서 PR → main (또는 dev)
7. 사용자 검토 → 머지

**PR 체크리스트 (팀원 자가 점검):**

- [ ] `git status` 에 `learned_patterns.json` · `ERRORS.md` 등 운영 자산 변경 없음
- [ ] `python shared/precommit_check.py` 0건 통과
- [ ] `python -m py_compile <변경 파일들>` 통과
- [ ] CLAUDE.md 의 *해당 도메인* 규정 점검 (예: 이미지 → `JARVIS06_IMAGE/CLAUDE.md`)
- [ ] 변경 의도 PR description 에 명시

---

## 5. 작업 가능·불가능 명확화

### ✅ 팀원이 할 수 있는 작업

- 코드 수정·리팩터·테스트 (모든 JARVIS0N_*/)
- 단위 테스트 작성·실행
- LLM 호출 테스트 (`shared/llm.invoke_text()` — claude CLI 경유)
- 차트 생성 테스트 (`JARVIS06_IMAGE.chart_generator`)
- 정책 검증 (`precommit_check.py`)
- 문서 작업 (CLAUDE.md·docs/architect/·ADR)

### ❌ 팀원이 할 수 없는 작업

- `python jarvis_daemon.py` 실행 (운영 데몬)
- 네이버·티스토리 발행 (Selenium Chrome 환경 다름)
- Telegram 봇 polling
- 운영 데이터 수집 (RADAR trends·경쟁사 캐시)
- `JARVIS07_GUARDIAN/auto_repair.py` 직접 실행
- `JARVIS07_GUARDIAN/ERRORS.md` 직접 편집
- `learned_patterns.json` 등 학습 자산 편집

---

## 6. 자주 쓰는 점검 명령

```bash
# 정책 11카테고리 검증 (가장 중요)
python shared/precommit_check.py

# 특정 카테고리만
python shared/precommit_check.py --category harness
python shared/precommit_check.py --category image

# 카테고리 목록
python shared/precommit_check.py --list

# Python syntax 전수 검사
find . -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" \
  | xargs python -m py_compile

# 에이전트 등록 검증 (새 _agent.py 추가 시)
python shared/agent_registration_check.py
```

---

## 7. 막힐 때

- **CLAUDE.md** (루트) → 시스템 전체 정책
- **`docs/decisions/`** → 결정 사유·ADR
- **`JARVIS07_GUARDIAN/ERRORS.md`** → 과거 오류 사례 (read-only)
- 운영 데몬 상태 질문 → 사용자에게 전달
- claude CLI 인증 실패 → `claude auth status` → `claude auth login` 재시도

---

## 8. 핵심 한 줄

> **팀원은 *코드 작가*, 사용자는 *운영 책임자*. 학습 자산·발행·데몬은 운영 책임자의 영역.**
