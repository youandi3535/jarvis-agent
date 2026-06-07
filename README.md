# 🤖 JARVIS Agent

**블로그 자동화부터 트렌드 분석, 자기 학습까지 — 단일 데몬으로 운영되는 멀티 에이전트 시스템**

> 텔레그램으로 명령하면 알아서 글을 쓰고, 이미지를 만들고, 발행하고, 오류가 나면 스스로 고칩니다.

---

## 주요 기능

| 기능 | 설명 |
|------|------|
| 📝 **블로그 자동 발행** | 경제 브리핑(매일 07:00) + 테마주 분석(매일 16:00) — 네이버·티스토리 동시 발행 |
| 🖼️ **AI 이미지 자동 생성** | Pollinations.ai 로 글마다 새 이미지 창작 |
| 📡 **트렌드 레이더** | Google Trends + 네이버 DataLab 실시간 수집 → 핫 키워드 자동 탐지 |
| 🛡️ **자가 진단·수정** | 오류 발생 시 학습 캐시 → RL 모델 → Claude Code SDK 3단계 자동 복구 |
| 🧠 **강화 학습 오류 수정** | SGDClassifier 온라인 학습 — 수정 성공/실패를 보상으로 가중치 즉시 갱신 |
| 📊 **통합 대시보드** | 허브(hub.py) 단일 진입점 — 발행 이력·오류 현황·학습 곡선 한눈에 |
| 💬 **텔레그램 인터페이스** | 자유 문장 → ReAct 라우터 → 에이전트 자동 디스패치 + 인라인 버튼 승인 |

---

## 아키텍처

```
텔레그램 / 스케줄러
       │
       ▼
 jarvis_daemon.py  ← 유일한 진입점 (단일 프로세스)
       │
       ├─ JARVIS01_MASTER  — 자유 문장 → ReAct 라우터 → 에이전트 디스패치
       ├─ JARVIS02_WRITER  — 블로그 글 작성 파이프라인 (경제·테마)
       ├─ JARVIS03_RADAR   — 트렌드 수집·분석 대시보드
       ├─ JARVIS04_SCHEDULER — 모든 APScheduler 잡 단일 컨트롤 타워
       ├─ JARVIS06_IMAGE   — 이미지 생성·검증·삽입 (AI 사진·SVG·썸네일)
       ├─ JARVIS07_GUARDIAN — 오류 수집·분석·자동 수정·RL 학습 엔진
       ├─ JARVIS08_PUBLISH — 네이버·티스토리 Selenium 발행자
       ├─ JARVIS09_COLLECTOR — 뉴스·블로그·금융 데이터 수집
       └─ JARVIS00_INFRA   — 인프라 (프로세스 제어·/status·/restart)

       shared/
         ├─ bus.py      — 에이전트 간 이벤트 버스 (유일한 통신 채널)
         ├─ db.py       — SQLite 공용 DB
         ├─ llm.py      — LLM 호출 단일 진입점 (invoke_text)
         └─ notify.py   — 텔레그램 알림 유틸
```

---

## 에이전트 목록

| 에이전트 | 폴더 | 역할 |
|---------|------|------|
| JARVIS00 INFRA | `JARVIS00_INFRA/` | 데몬 라이프사이클·시스템 상태·인프라 명령 |
| JARVIS01 MASTER | `JARVIS01_MASTER/` | 자유 문장 → 인텐트 분류 → 에이전트 디스패치 (LangGraph ReAct) |
| JARVIS02 WRITER | `JARVIS02_WRITER/` | 경제 브리핑·테마주 블로그 자동 작성 (BLOG_SUPREME_LAW.md 헌법 준수) |
| JARVIS03 RADAR | `JARVIS03_RADAR/` | Google Trends + 네이버 DataLab 트렌드 수집·분석·대시보드 |
| JARVIS04 SCHEDULER | `JARVIS04_SCHEDULER/` | APScheduler 단일 진입점 — 모든 잡 등록·조회·제어 |
| JARVIS06 IMAGE | `JARVIS06_IMAGE/` | AI 이미지 생성(폴백 체인)·SVG 차트·썸네일·dedupe·삽입 |
| JARVIS07 GUARDIAN | `JARVIS07_GUARDIAN/` | 오류 수집·3-Tier 자동 수정·RL 학습 엔진·자가 진단 |
| JARVIS08 PUBLISH | `JARVIS08_PUBLISH/` | 네이버·티스토리 Selenium 발행자·카테고리·쿠키 관리 |
| JARVIS09 COLLECTOR | `JARVIS09_COLLECTOR/` | 주제별 뉴스·블로그·금융 데이터 수집·정제 |

---

## 자동 발행 스케줄

| 시각 | 잡 | 내용 |
|------|----|------|
| 07:00 | 자가 진단 → 경제 브리핑 | 코드 전수 점검 후 → 경제 지표 기반 블로그 글 발행 |
| 16:00 | 자가 진단 → 테마주 분석 | 코드 전수 점검 후 → 트렌드 테마주 심층 분석 글 발행 |
| 03:30 | git 회고 | 전날 코드 변경 학습 자산화 |
| 04:30 | 헌법 감사 | 정책 위반·드리프트 검출 + 개선 제안 |
| 격주 월 04:00 | 파일 정리 | 오래된 로그·스크린샷·트렌드 데이터 자동 삭제 |

---

## 자가 학습 시스템

오류가 발생할수록 점점 똑똑해지는 3계층 학습 구조:

```
오류 발생
   │
   ├─ Tier 1: 학습 캐시 (learned_patterns.json — fingerprint 즉시 매칭, LLM 호출 0)
   ├─ Tier 1.5: RL 모델 (SGDClassifier — 온라인 학습, 보상 기반 가중치 즉시 갱신)
   └─ Tier 2: Claude Code SDK (자동 코드 수정 — 위 두 계층 실패 시)
         │
         └─ 수정 성공 → learned_patterns 자동 등록 → 다음엔 Tier 1에서 즉시 처리
```

- 현재 누적: **265개 패턴 / 870회 적중** (LLM 호출 절감)
- 자동 수정 후 원래 실패했던 잡 자동 재시도

---

## 빠른 시작

### 사전 요구사항

- Python 3.11+
- Chrome + ChromeDriver (Selenium 발행용)
- 텔레그램 봇 토큰 ([BotFather](https://t.me/BotFather))
- 네이버 블로그 계정 / 티스토리 블로그 계정

### 설치

```bash
git clone https://github.com/youandi3535/jarvis-agent.git
cd jarvis-agent

# 가상환경 생성
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r JARVIS02_WRITER/requirements.txt
pip install claude-code-sdk python-dotenv apscheduler streamlit scikit-learn numpy chromadb

# 환경변수 설정
cp .env.example .env
# .env 파일을 열어서 API 키·계정 정보 입력
```

### 환경변수 (.env)

`.env.example` 파일을 복사해서 아래 항목을 채워넣으세요:

| 항목 | 설명 | 발급처 |
|------|------|--------|
| (Claude 인증) | **Claude Code SDK** 의 `claude` CLI 가 OAuth (Max 구독) 로 자동 인증 — 별도 API 키 불필요 | `claude auth login` |
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 | [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | getUpdates API |
| `NV_USERNAME` / `NV_PASSWORD` | 네이버 계정 | [naver.com](https://naver.com) |
| `TS_USERNAME` / `TS_PASSWORD` | 티스토리 계정 | [tistory.com](https://tistory.com) |
| `NAVER_CLIENT_ID/SECRET` | 네이버 DataLab API | [developers.naver.com](https://developers.naver.com) |
| `GOOGLE_AI_API_KEY` | Gemini 이미지 생성 | [aistudio.google.com](https://aistudio.google.com) |
| `BOK_ECOS_KEY` | 한국은행 ECOS API | [ecos.bok.or.kr](https://ecos.bok.or.kr) |

### 실행

```bash
# 데몬 시작 (포그라운드)
python jarvis_daemon.py

# 백그라운드 실행
nohup python jarvis_daemon.py > logs/daemon.log 2>&1 &

# 종료
pkill -f jarvis_daemon.py
```

### 대시보드

```bash
streamlit run hub.py --server.port 9199
# http://localhost:9199 접속
```

---

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/status` | 전체 에이전트 상태 요약 |
| `/restart` | 데몬 재시작 |
| `/errors` | 최근 오류 목록 |
| `/jobs` | 스케줄 잡 목록 |
| 자유 문장 | "오늘 경제 브리핑 써줘", "AI 트렌드 분석해줘" 등 |

---

## 새 에이전트 추가

`jarvis_daemon.py` 수정 없이 폴더 추가만으로 자동 등록됩니다:

```
JARVIS10_NAME/
  └─ name_agent.py   ← register(scheduler, bus) + declare(...) 정의
```

자세한 규약은 [AGENTS.md](AGENTS.md) 참조.

---

## 프로젝트 규칙

- **단일 진입점 원칙**: 도메인별 책임 폴더 고정 (이미지→JARVIS06, 발행→JARVIS08, 스케줄→JARVIS04 등)
- **외부 영향 = 텔레그램 승인 필수**: 발행·파일 수정·잡 변경은 인라인 버튼 ✅ 후 실행
- **오류 기록 의무**: 모든 오류·수정 이력 `JARVIS07_GUARDIAN/ERRORS.md` 단일 저장소
- **LLM 호출 단일 진입점**: `shared/llm.py` 의 `invoke_text()` 만 사용

자세한 규정은 [CLAUDE.md](CLAUDE.md) 참조.

---

## 기술 스택

| 분류 | 사용 기술 |
|------|---------|
| LLM | Anthropic Claude (Sonnet 4.6 / Opus 4.6) |
| 에이전트 프레임워크 | LangGraph ReAct |
| 스케줄러 | APScheduler 3.x |
| 브라우저 자동화 | Selenium 4 + Chrome |
| 데이터베이스 | SQLite (WAL 모드) |
| 벡터 검색 | ChromaDB |
| RL 모델 | scikit-learn SGDClassifier (online learning) |
| 트렌드 수집 | pytrends (Google) + 네이버 DataLab API |
| 금융 데이터 | pykrx, yfinance, FinanceDataReader |
| 대시보드 | Streamlit |
| 알림 | Telegram Bot API |

---

## 라이선스

Private repository — 무단 배포 금지.
