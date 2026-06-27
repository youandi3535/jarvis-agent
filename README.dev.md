# JARVIS — 공동 개발 가이드 (페어 프로그래밍 · 브랜치 워크플로우)

> **개발 방식**: 두 개발자(김효중 HJ · 김나연 NY)가 **동일 macOS에서 페어 프로그래밍**으로 함께 작업합니다.  
> git 커밋은 단일 계정(`youandi3535`)으로 기록되지만, 설계·구현·검토는 두 사람이 함께 진행합니다.

---

## 0. 역할 분리 (먼저 이해)

| 역할 | 김효중 (HJ) | 김나연 (NY) |
|------|------------|------------|
| **주력 에이전트** | JARVIS00·01·04·07 · shared/ | JARVIS02·03·06·08·09 |
| **도메인** | 플랫폼·거버넌스·신뢰성 코어 | 콘텐츠·수집·발행 파이프라인 |
| 운영 데몬 실행 | ✅ 유일 실행 (HJ macOS) | ❌ 절대 실행 금지 |
| 발행 (네이버·티스토리 Selenium) | ✅ | ❌ 환경 불가 |
| APScheduler cron (06:30·16:00) | ✅ | ❌ |
| 학습 자산 누적 (`learned_patterns.json`·`ERRORS.md`) | ✅ 단일 소스 | ❌ 읽기만 |
| 코드 작성·수정 | ✅ | ✅ |
| 단위 테스트·py_compile | ✅ | ✅ |
| Git PR 작업 | ✅ | ✅ |

**핵심 원칙**: 운영 데몬은 HJ macOS 1곳에서만. 발행 사고·학습 자산 오염 차단.

---

## 1. 공동 개발 환경 (macOS)

### 1-1. 저장소 설정

```bash
git clone https://github.com/youandi3535/jarvis-agent.git
cd jarvis-agent
git config user.name "kimhyojung"
git config user.email "youandi3535@naver.com"
```

### 1-2. Python 가상환경

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r JARVIS02_WRITER/requirements.txt
pip install claude-code-sdk python-dotenv apscheduler streamlit scikit-learn numpy chromadb
```

### 1-3. Claude CLI 설치 (Claude Code SDK 호출용)

```bash
# npm 전역 설치
npm install -g @anthropic-ai/claude-code

# OAuth 로그인 (브라우저 자동 열림)
claude auth login

# 확인
claude --version
```

### 1-4. `.env` 설정

```bash
cp .env.example .env
# .env 파일을 열어서 실제 값 입력
```

### 1-5. 검증

```bash
# 정책 27종 검증 — 0건이어야 함
python shared/precommit_check.py

# 에이전트 등록 검증
python shared/agent_registration_check.py

# LLM 호출 테스트
python -c "from shared.llm import invoke_text; print(invoke_text('writer_fast', 'Say hi'))"

# Python syntax 전수 검사
find . -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" \
  | xargs python -m py_compile
```

---

## 2. 브랜치 워크플로우

| 브랜치 | 용도 | 커밋 권한 |
|--------|------|-----------|
| `main` | 운영 — 데몬이 실행하는 코드 | PR 머지만 (직접 push 금지) |
| `feat/hj` | 개발 통합 브랜치 | HJ · NY |
| `feature/<task>` | 개별 기능 작업 | HJ · NY |

**작업 흐름**:

```bash
# 1. 최신 상태로 시작
git checkout feat/hj && git pull

# 2. 기능 브랜치 생성
git checkout -b feature/<task-name>

# 3. 작업 + 커밋 (작게·자주)
git add <파일들>
git commit -m "hj : <변경 요약>"   # 또는 "ny : <변경 요약>"

# 4. 정책 검증
python shared/precommit_check.py   # 0건이어야 함

# 5. PR 생성
git push origin feature/<task-name>
# GitHub에서 PR → feat/hj (또는 main)
```

---

## 3. 운영 학습 자산 — 절대 편집 금지

아래 파일들은 `.gitignore`되어 있고 **운영 데몬(HJ macOS)에서만 갱신**됩니다:

| 파일 | 내용 | 규칙 |
|------|------|------|
| `JARVIS07_GUARDIAN/learned_patterns.json` | 학습 패턴 265개 | ❌ 편집 금지 |
| `JARVIS07_GUARDIAN/learned_incidents.json` | incident 학습 | ❌ 편집 금지 |
| `JARVIS07_GUARDIAN/project_audit_log.json` | 정책 작업 박제 | ❌ 편집 금지 |
| `JARVIS03_RADAR/data/` | 트렌드 수집·캐시 | ❌ 편집 금지 |
| `shared/jarvis.sqlite` | 공용 DB | ❌ 편집 금지 |
| `react_checkpoints.sqlite` | ReAct 체크포인트 51MB | ❌ 편집 금지 |

---

## 4. ERRORS.md 정책 — 정책 A (2026-06-07 박제)

`JARVIS07_GUARDIAN/ERRORS.md` — **읽기만 가능, 직접 편집 금지**.

| 행위 | HJ | NY |
|------|----|----|
| 읽기 (오류 참조) | ✅ 필수 | ✅ 필수 |
| 새 항목 추가 | ✅ `report_manual_fix()` 경유 | ❌ 직접 편집 금지 |
| PR에 ERRORS.md 변경 포함 | ❌ 머지 거부 | ❌ 머지 거부 |

**이유**: 운영 데몬이 `report_manual_fix()` 호출 시마다 자동 갱신 → 양쪽 동시 편집 시 merge conflict 폭발.

### pre-commit 가드

`.git/hooks/pre-commit`에 추가:

```bash
#!/bin/bash
if git diff --cached --name-only | grep -q "JARVIS07_GUARDIAN/ERRORS.md"; then
    echo "❌ ERRORS.md 직접 편집 금지 (README.dev.md 정책 A)"
    echo "   새 오류는 report_manual_fix() 호출로만 박제"
    exit 1
fi
```

---

## 5. 커밋 컨벤션

```
<개발자 이니셜> : <변경 내용 요약>

예:
  hj : JARVIS07 RL 모델 임계값 조정
  ny : 경제 브리핑 이미지 생성 keyword 연결
  hj : [280] 경제 브리핑 차트 collection_docs 파이프라인 연결
```

- **이니셜**: `hj` (김효중) · `ny` (김나연)
- **버그 수정**: `[이슈번호]` 접두사 권장
- **커밋 전 반드시**: `python shared/precommit_check.py` 0건 통과

---

## 6. PR 체크리스트 (자가 점검)

```
- [ ] git status에 learned_patterns.json · ERRORS.md 등 운영 자산 변경 없음
- [ ] python shared/precommit_check.py 0건 통과
- [ ] find . -name "*.py" ... | xargs python -m py_compile 통과
- [ ] CLAUDE.md의 해당 도메인 규정 점검
- [ ] 변경 의도 PR description에 명시
- [ ] 이미지 관련 → JARVIS06_IMAGE/ 단일 진입점 확인
- [ ] 스케줄 관련 → JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 등록 확인
- [ ] LLM 호출 → shared/llm.py invoke_text() 경유 확인
```

---

## 7. 작업 가능·불가능 명확화

### ✅ 두 개발자가 함께 할 수 있는 작업

- 코드 수정·리팩터·테스트 (모든 JARVIS0N_*/)
- 단위 테스트 작성·실행
- LLM 호출 테스트 (`shared/llm.invoke_text()`)
- 차트·이미지 생성 테스트 (`JARVIS06_IMAGE.chart_generator`)
- 정책 검증 (`precommit_check.py`)
- 문서 작업 (CLAUDE.md · docs/architect/ · ADR)

### ❌ HJ(운영 담당)만 실행 가능한 작업

- `python jarvis_daemon.py` 실행 (운영 데몬)
- 네이버·티스토리 발행 (Selenium Chrome 환경)
- Telegram 봇 polling
- 운영 데이터 수집 (RADAR trends)
- `JARVIS07_GUARDIAN/auto_repair.py` 직접 실행
- `JARVIS07_GUARDIAN/ERRORS.md` 직접 편집
- `learned_patterns.json` 등 학습 자산 편집

---

## 8. 자주 쓰는 검증 명령

```bash
# 전체 정책 검증 (가장 중요 — 0건이어야 함)
python shared/precommit_check.py

# 특정 카테고리만
python shared/precommit_check.py --category harness
python shared/precommit_check.py --category image
python shared/precommit_check.py --category schedule

# 카테고리 목록
python shared/precommit_check.py --list

# Python syntax 전수 검사
find . -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" \
  | xargs python -m py_compile

# 에이전트 등록 검증 (새 _agent.py 추가 시)
python shared/agent_registration_check.py

# 도메인 단일 진입점 검증 (이미지)
grep -rnE 'https://image\.pollinations\.ai' --include='*.py' . \
  | grep -v 'JARVIS06_IMAGE/' | grep -v __pycache__
```

---

## 9. 막힐 때

| 상황 | 참고 자료 |
|------|---------|
| 시스템 전체 정책 | [`CLAUDE.md`](CLAUDE.md) |
| 결정 사유·ADR | [`docs/decisions/`](docs/decisions/README.md) |
| 과거 오류 사례 | [`JARVIS07_GUARDIAN/ERRORS.md`](JARVIS07_GUARDIAN/ERRORS.md) (read-only) |
| 에이전트 추가 규약 | [`AGENTS.md`](AGENTS.md) |
| Claude CLI 인증 실패 | `claude auth status` → `claude auth login` 재시도 |

---

## 10. 핵심 한 줄

> **두 개발자가 같은 Mac에서 함께 설계하고 구현합니다. 운영(데몬·발행·학습자산)은 HJ 담당, 코드 작업은 함께.**
