<div align="center">

# 🤖 JARVIS Agent

**트렌드 감지 → 수집 → 글 생성 → 이미지 → 발행 → 자가학습까지 스스로 도는 10-모듈 멀티에이전트 시스템**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Claude](https://img.shields.io/badge/Anthropic-Claude%20Sonnet%204.6%20%2F%20Opus%204.6-D97757?style=flat-square&logo=anthropic&logoColor=white)](https://anthropic.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-ReAct%20Orchestration-FF6B35?style=flat-square)](https://langchain-ai.github.io/langgraph/)
[![Selenium](https://img.shields.io/badge/Selenium-4.x-43B02A?style=flat-square&logo=selenium&logoColor=white)](https://selenium.dev)
[![APScheduler](https://img.shields.io/badge/APScheduler-3.x-4DABF7?style=flat-square)](https://apscheduler.readthedocs.io)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-RL%20Model-F7931E?style=flat-square&logo=scikitlearn&logoColor=white)](https://scikit-learn.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io)
[![SQLite](https://img.shields.io/badge/SQLite-WAL%20Mode-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Team](https://img.shields.io/badge/Team-2인%20공동개발-00C851?style=flat-square)](#-팀--역할)
[![Platform](https://img.shields.io/badge/Platform-macOS-000000?style=flat-square&logo=apple&logoColor=white)](https://apple.com/macos)

> 텔레그램으로 명령하면 알아서 글을 쓰고, 이미지를 만들고, 발행하고, 오류가 나면 스스로 고칩니다.

</div>

---

## 📊 프로젝트 수치

<div align="center">

| 🗂️ 에이전트 모듈 | 📝 Python 코드 | 📄 파일 수 | 🔧 등록 도구 | 🛡️ 정책 검증 항목 | 🧠 학습 패턴 누적 |
|:-:|:-:|:-:|:-:|:-:|:-:|
| **10개** | **68,308 LOC** | **169개** | **42개** | **27종** | **265개 / 870회 적중** |

</div>

---

## ✨ 핵심 기능

| 기능 | 설명 |
|------|------|
| 📝 **블로그 자동 발행** | 경제 브리핑(매일 06:30) + 테마주 분석(매일 16:00) — 네이버·티스토리 동시 발행 |
| 🖼️ **AI 이미지 자동 생성** | 글 키워드 기반 Pollinations.ai → 매 글마다 새로운 이미지 창작 (dedupe 포함) |
| 📡 **트렌드 레이더** | Google Trends + 네이버 DataLab 실시간 수집 → 핫 키워드 자동 탐지 |
| 🛡️ **자동 캐치·수정 시스템** | `catch()` 단일 진입점 → Tier 2(패턴·Bandit) → Tier 3(Opus 4.6) — 전 심각도 자동 복구 |
| 🔒 **보안 전문가급 안전장치** | Circuit breaker · 빈도 기반 severity 자동 상향(3회) · 보안 파일 수정 절대 금지 |
| 🏛️ **헌법형 거버넌스** | `precommit_check.py` 947줄 — 27종 정책을 pre-commit·부팅·주간감사 3중 강제 |
| 📊 **통합 대시보드** | hub.py 단일 진입점(port 9199) — 발행 이력·오류 현황·학습 곡선 한눈에 |
| 💬 **텔레그램 인터페이스** | 자유 문장 → ReAct 라우터 → 에이전트 디스패치 + 인라인 버튼 HITL 승인 |

---

## 🏗️ 시스템 아키텍처

```mermaid
flowchart TD
    A(["💬 텔레그램 / ⏰ cron 트리거"]) --> B

    subgraph CORE["🧠 코어 레이어"]
        B["JARVIS01 MASTER\nLangGraph ReAct 라우터\nHITL 승인 게이트"]
    end

    B --> C["JARVIS03 RADAR\n트렌드 감지·학습\nGoogle Trends + 네이버 DataLab"]
    C --> D["JARVIS09 COLLECTOR\n멀티소스 수집·정제\n뉴스·블로그·금융·학술"]
    D --> E["JARVIS02 WRITER\n글 생성 + 헌법 검증\nBLOG_SUPREME_LAW.md"]
    E --> F["JARVIS06 IMAGE\nAI 차트·인포그래픽·썸네일\nPollinations.ai 폴백 체인"]
    F --> G["JARVIS08 PUBLISH\n네이버·티스토리 Selenium\n발행 검증 + 스크린샷"]
    G --> H(["📤 성과 수집 → 학습 가중치 갱신"])
    H -. "📈 학습 루프" .-> C

    subgraph COMMON["⚙️ 공통 레이어"]
        I["JARVIS07 GUARDIAN\ncatch() 단일 진입점 → Tier 2(패턴) → Tier 3(Opus)\n전 심각도 자동수정 · Circuit breaker · 학습 루프"]
        J["JARVIS00 INFRA\npreflight · harness · event bus\n/status · /restart"]
        K["JARVIS04 SCHEDULER\nAPScheduler 단일 진입점\n모든 cron 잡 관리"]
    end

    E -. "⚠️ 오류" .-> I
    G -. "⚠️ 오류" .-> I
    I -. "🔧 자동 수정" .-> E

    subgraph SHARED["🔗 공유 레이어"]
        L["shared/bus.py\n에이전트 간 이벤트 버스"]
        M["shared/llm.py\nLLM 호출 단일 진입점"]
        N["shared/db.py\nSQLite 공용 DB"]
    end

    style CORE fill:#1a1a2e,stroke:#7c83fd,color:#fff
    style COMMON fill:#16213e,stroke:#e94560,color:#fff
    style SHARED fill:#0f3460,stroke:#4dabf7,color:#fff
```

---

## 📦 에이전트 모듈

| 에이전트 | 폴더 | 역할 | 개발자 |
|---------|------|------|--------|
| **JARVIS00** INFRA | `JARVIS00_INFRA/` | 데몬 라이프사이클·시스템 상태·검증 하니스 | HJ |
| **JARVIS01** MASTER | `JARVIS01_MASTER/` | 자유 문장 → 인텐트 분류 → ReAct 디스패치 (LangGraph) | HJ |
| **JARVIS02** WRITER | `JARVIS02_WRITER/` | 경제 브리핑·테마주 블로그 자동 작성 (헌법 준수) | NY |
| **JARVIS03** RADAR | `JARVIS03_RADAR/` | Google Trends + 네이버 DataLab 트렌드 수집·분석 | NY |
| **JARVIS04** SCHEDULER | `JARVIS04_SCHEDULER/` | APScheduler 단일 진입점 — 모든 잡 등록·조회·제어 | HJ |
| **JARVIS06** IMAGE | `JARVIS06_IMAGE/` | AI 이미지 생성(폴백 체인)·SVG 차트·썸네일·dedupe | NY |
| **JARVIS07** GUARDIAN | `JARVIS07_GUARDIAN/` | 오류 수집·3-Tier 자동 수정·RL 학습 엔진·자가 진단 | HJ |
| **JARVIS08** PUBLISH | `JARVIS08_PUBLISH/` | 네이버·티스토리 Selenium 발행자·카테고리·쿠키 관리 | NY |
| **JARVIS09** COLLECTOR | `JARVIS09_COLLECTOR/` | 주제별 뉴스·블로그·금융 데이터 수집·정제 | NY |

> **HJ** = 김효중 (주도 개발) &nbsp;|&nbsp; **NY** = 김나연 (공동 개발)

---

## 📅 자동 발행 파이프라인

```mermaid
gantt
    title 일일 자동 발행 파이프라인
    dateFormat HH:mm
    axisFormat %H:%M

    section 새벽 유지보수
        git 커밋 회고 & 학습 자산화     :done, 03:30, 15m
        헌법 감사 (드리프트 검출)        :done, 04:30, 30m

    section 오전 발행 세트 (06:30)
        자가 진단 & 코드 수정           :active, 06:15, 15m
        경제 지표 수집 (JARVIS09)       :active, 06:30, 10m
        경제 브리핑 글 생성 (WRITER)    :active, 06:40, 15m
        AI 이미지 생성 (IMAGE)          :active, 06:55, 10m
        네이버·티스토리 발행 (PUBLISH)  :active, 07:05, 10m

    section 오후 발행 세트 (16:00)
        자가 진단 & 코드 수정           :16:00, 15m
        테마 트렌드 수집 (RADAR+J09)    :16:15, 10m
        테마주 분석 글 생성 (WRITER)    :16:25, 15m
        AI 이미지 생성 (IMAGE)          :16:40, 10m
        네이버·티스토리 발행 (PUBLISH)  :16:50, 10m
```

| 시각 | 잡 이름 | 내용 |
|------|---------|------|
| **06:30** | 경제 브리핑 세트 | 자가 진단 → 경제 지표 수집 → 글 작성 → 이미지 → 발행 |
| **16:00** | 테마주 분석 세트 | 자가 진단 → 트렌드 테마 선정 → 글 작성 → 이미지 → 발행 |
| **03:30** | git 회고 | 전날 코드 변경 D-1 학습 자산화 |
| **04:30** | 헌법 감사 | 정책 위반·드리프트 검출 + 개선 제안 |
| **격주 월 04:00** | 파일 정리 | 오래된 로그·스크린샷·트렌드 캐시 자동 삭제 |

---

## 🧠 자가 학습 시스템

오류가 발생할수록 점점 똑똑해지는 폐쇄 학습 루프:

```mermaid
flowchart LR
    subgraph CATCH["📡 Tier 1 — 자동 캐치 (catch() 단일 진입점)"]
        H1["sys.excepthook\n메인 스레드 미처리 예외"]
        H2["threading.excepthook\n백그라운드 스레드 예외"]
        H3["APScheduler listener\n스케줄 잡 실패"]
        H4["log_scanner\n5분마다 JARVIS*/logs 스캔"]
        H5["auto_catch 데코레이터\n명시적 보호 함수"]
    end

    H1 & H2 & H3 & H4 & H5 --> C["catch()\n단일 진입점\n쿨다운 · sandbox 차단"]

    C --> SAFE["안전장치\nCircuit breaker\n빈도 상향(3회)\n보안 파일 차단"]

    SAFE --> T2

    subgraph TIERS["자동 수정"]
        T2{"Tier 2\n패턴·Bandit\nGroup1(hit≥3)+Group2(신규)\nLLM 호출 0"}
        T3{"Tier 3\nClaude Opus 4.6\nAST 검증 + .bak 롤백"}
    end

    T2 -->|"✅ 수정"| OK
    T2 -->|"패턴 없음"| T3
    T3 -->|"✅ 수정"| OK

    OK["수정 성공\n잡 재시도\n텔레그램 알림"] --> LEARN["learned_patterns.json\nfingerprint 자동 등록\nhit_count 누적"]
    LEARN -. "다음엔 Tier 2에서 즉시 처리" .-> T2

    T3 -->|"❌ 실패"| ESC["텔레그램 알림\n수동 검토 요청"]

    style CATCH fill:#0d1b2a,stroke:#4a9eff,color:#fff
    style TIERS fill:#1a1a2e,stroke:#7c83fd,color:#fff
```

**심각도별 처리 매트릭스:**

| 심각도 | Tier 2 (패턴) | Tier 3 (LLM) | 텔레그램 알림 |
|--------|:---:|:---:|:---:|
| ⚪ LOW | ✅ | ✅ → 학습 저장 | ✅ |
| 🟡 MEDIUM | ✅ | ✅ | ✅ |
| 🟠 HIGH | ✅ | ✅ | ✅ |
| 🔴 CRITICAL | ✅ | ❌ (LLM 생략 — 안전) | ✅ 항상 |

| 지표 | 현재 값 | 의미 |
|------|---------|------|
| 누적 패턴 | **265개** | fingerprint 즉시 매칭 가능 오류 유형 |
| 총 적중 수 | **870회** | LLM 호출 없이 자동 처리된 횟수 |
| 오류 기록 | **285건 / 5,945줄** | `JARVIS07_GUARDIAN/ERRORS.md` 구조화 회고 |
| 체크포인트 | **51MB** | `react_checkpoints.sqlite` (ReAct 실가동 증거) |

---

## 🔒 거버넌스 & 안전 설계

```
외부 영향 도구 (발행·파일 수정·잡 변경)
              │
              ▼
  텔레그램 인라인 버튼 ✅/❌
     (HITL Human-in-the-Loop)
              │
        승인 후에만 실행
              │
              ▼
  _safe_path 3중 방어  ───  bash 화이트리스트
  (경로탈출/심볼릭/deny dir)   (14개 deny 패턴)
              │
              ▼
     .bak 자동 백업 + AST 검증
         실패 시 자동 롤백
```

| 보호 레이어 | 구현 | 역할 |
|------------|------|------|
| HITL 승인 게이트 | `approved_context` / `PermissionError` | 외부 영향 도구 100% 차단 |
| 정책 정적 강제 | `precommit_check.py` 947줄 | 27종 위반 자동 감지 |
| 파일 안전 박스 | `_safe_path()` | 경로 탈출·심볼릭·deny dir 차단 |
| 셸 안전 박스 | `_BASH_WHITELIST` | 화이트리스트 외 명령 차단 |
| 변경 안전망 | `.bak` 백업 + AST 검증 | 코드 수정 실패 시 자동 롤백 |

---

## 💬 텔레그램 인터페이스

| 명령어 | 설명 | 권한 |
|--------|------|------|
| `/status` | 전체 에이전트 상태 요약 | 조회 |
| `/jobs` | 스케줄 잡 목록 + 다음 실행 시각 | 조회 |
| `/errors` | 최근 오류 목록 | 조회 |
| `/restart` | 데몬 재시작 | ✅ 승인 필요 |
| `"경제 브리핑 써줘"` | ReAct 라우터 → WRITER 디스패치 | ✅ 승인 필요 |
| `"AI 트렌드 분석해줘"` | ReAct 라우터 → RADAR 디스패치 | ✅ 승인 필요 |
| `"에러 수정해줘"` | ReAct 라우터 → GUARDIAN 디스패치 | ✅ 승인 필요 |

> 모든 **외부 영향** 명령은 텔레그램 인라인 버튼 ✅/❌ 통과 후에만 실행됩니다.

---

## 🚀 빠른 시작

### 사전 요구사항

- Python 3.11+
- Chrome + ChromeDriver (Selenium 발행용)
- 텔레그램 봇 토큰 ([BotFather](https://t.me/BotFather))
- 네이버 블로그 계정 / 티스토리 블로그 계정
- Anthropic Claude Max 구독 (OAuth 자동 인증 — API 키 불필요)

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

# Claude 인증 (OAuth)
claude auth login

# 환경변수 설정
cp .env.example .env
# .env 파일을 열어서 API 키·계정 정보 입력
```

### 환경변수 (.env)

| 항목 | 설명 | 발급처 |
|------|------|--------|
| *(Claude 인증)* | Claude Code SDK OAuth — `claude auth login` 으로 자동 처리 | `claude auth login` |
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 | [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | getUpdates API |
| `NV_USERNAME` / `NV_PASSWORD` | 네이버 계정 | [naver.com](https://naver.com) |
| `TS_USERNAME` / `TS_PASSWORD` | 티스토리 계정 | [tistory.com](https://tistory.com) |
| `NAVER_CLIENT_ID` / `SECRET` | 네이버 DataLab API | [developers.naver.com](https://developers.naver.com) |
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

### 통합 대시보드

```bash
streamlit run hub.py --server.port 9199
# http://localhost:9199 접속
```

---

## 🔧 기술 스택

| 분류 | 사용 기술 | 역할 |
|------|----------|------|
| **LLM** | Anthropic Claude Sonnet 4.6 / Opus 4.6 | 글 생성·오류 분석·자가 수정 |
| **에이전트 프레임워크** | LangGraph ReAct + SqliteSaver | 멀티스텝 추론·체크포인트 |
| **스케줄러** | APScheduler 3.x | cron·interval 단일 진입점 |
| **브라우저 자동화** | Selenium 4 + Chrome | 네이버·티스토리 발행 |
| **데이터베이스** | SQLite (WAL 모드) | 공용 DB·체크포인트 |
| **벡터 검색** | ChromaDB | 수집 자료 유사도 검색 |
| **RL 모델** | scikit-learn SGDClassifier | 온라인 학습 오류 분류기 |
| **트렌드 수집** | pytrends (Google) + 네이버 DataLab API | 실시간 키워드 분석 |
| **금융 데이터** | pykrx · yfinance · FinanceDataReader | 주가·지표 수집 |
| **이미지 생성** | Pollinations.ai (AI 사진) + matplotlib (차트) | 글별 맞춤 이미지 |
| **대시보드** | Streamlit | 통합 현황 모니터링 |
| **알림** | Telegram Bot API | 실시간 승인·보고 |

---

## 👥 팀 & 역할

**2인 팀 · 전 과정 페어 프로그래밍으로 공동 개발.**  
두 개발자가 **개발자(김효중) macOS 한 대에서 함께 작업**했습니다.  
git 커밋은 단일 계정(`youandi3535`)으로 기록되지만, 설계·구현 전 과정을 두 사람이 함께 진행했습니다.

```
┌─────────────────────────────────┐  ┌──────────────────────────────────┐
│      김효중 (HJ) · 주도 개발     │  │      김나연 (NY) · 공동 개발      │
│   에이전트 플랫폼 · 신뢰성 코어   │  │   콘텐츠 · 수집 · 발행 파이프라인  │
│                                 │  │                                  │
│  · JARVIS01 (LangGraph ReAct)   │  │  · JARVIS02 (블로그 글 생성)      │
│  · JARVIS00 (데몬·검증 하니스)   │  │  · JARVIS03 (트렌드 분석)        │
│  · JARVIS07 (3-Tier RL 학습)    │  │  · JARVIS06 (AI 이미지 생성)     │
│  · JARVIS04 (APScheduler)       │  │  · JARVIS08 (네이버·티스토리)    │
│  · shared/ · 거버넌스            │  │  · JARVIS09 (데이터 수집·정제)   │
└─────────────────────────────────┘  └──────────────────────────────────┘
              ↑                                     ↑
              └──────────────┬──────────────────────┘
                     공동 개발 (같은 macOS)
                   git commit: youandi3535
```

| 멤버 | 역할 | 주력 에이전트 |
|------|------|-------------|
| **김효중** (HJ) | 주도 개발 · 에이전트 플랫폼 · 신뢰성 코어 | JARVIS00·01·04·07 · shared/ |
| **김나연** (NY) | 공동 개발 · 콘텐츠 · 수집 · 발행 파이프라인 | JARVIS02·03·06·08·09 |

> 운영 데몬은 발행 사고·학습 자산 오염 방지를 위해 개발자 macOS 1곳에서만 상시 실행합니다.

---

## 🔌 새 에이전트 추가

`jarvis_daemon.py` 수정 없이 폴더 추가만으로 자동 등록됩니다:

```
JARVIS10_NAME/
  └─ name_agent.py   ← register(scheduler, bus) + declare(...) 정의
```

| 필수 항목 | 위치 | 역할 |
|-----------|------|------|
| `{name}_agent.py` | 폴더 안 | 에이전트 진입점 |
| `register(scheduler, bus)` | agent.py 내 | 데몬 자동 등록 |
| `declare(agent_id=..., ...)` | agent.py 모듈 레벨 | 텔레그램·허브 자동 노출 |
| `AGENTS.md` 등록 행 | 루트 | 등록 검증 |

```bash
# 등록 검증
python shared/agent_registration_check.py
```

자세한 규약은 [AGENTS.md](AGENTS.md) 참조.

---

## 📐 프로젝트 원칙

| 원칙 | 내용 |
|------|------|
| **단일 진입점** | 도메인별 책임 폴더 고정 (이미지→J06·발행→J08·스케줄→J04·LLM→shared/llm.py) |
| **HITL 승인** | 외부 영향 도구는 텔레그램 인라인 버튼 ✅ 후에만 실행 |
| **오류 기록 의무** | 모든 오류·수정 이력 `JARVIS07_GUARDIAN/ERRORS.md` 단일 저장소 |
| **정적 강제** | `precommit_check.py` — pre-commit + 부팅 + 주간감사 3중 검증 |
| **학습 루프** | 오류 수정 사례 자동 자산화 → 다음 오류는 LLM 0 즉시 처리 |

자세한 규정은 [CLAUDE.md](CLAUDE.md) 참조.

---

## 📄 라이선스

Private repository — 무단 배포 금지.
