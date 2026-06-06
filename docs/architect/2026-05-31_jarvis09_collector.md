# JARVIS09_COLLECTOR — 완전 설계 기획서 (15단계 × 15소단계)

> 생성일: 2026-05-31 | 에이전트 ID: jarvis09_collector
> 판정: agent | 의도: JARVIS03 RADAR가 결정한 테마를 받아 인터넷 공개 데이터를 수집·정제하여 JARVIS02 WRITER에 전달

---
## ★ 읽는 법 (모든 단계 공통 원칙)
- ✅ YES → 명시된 다음 소단계로 즉시 진행
- ❌ NO → 명시된 롤백 동작 수행 후 복귀 소단계로 이동
- 🔄 ROLLBACK → 해당 소단계에서 생성된 파일·DB·상태를 원복 후 원인을 ERRORS.md 에 기록
- ⛔ ABORT → 이 기획서 무효. 사용자 재질의 필요. 진행 중 생성 파일 전부 삭제.
---

## Stage 1: 의도 & 요구사항 확정

### 1.1 사용자 원문 파악
- 작업: What=테마별 인터넷 공개 데이터 수집·정제, Why=WRITER 대본 품질 향상, Scope=agent, Constraint=robots.txt·이용약관 준수
- 검증: `echo "What/Why/Scope/Constraint 4종 추출 완료"`
- ✅ YES → 1.2 | ❌ NO → 보완 질의 생성 → ⛔ ABORT

### 1.2 기존 에이전트 역할 중복 확인
- 작업: AGENTS.md 8개 에이전트 중 "범용 웹 수집·정제" 전담 에이전트 없음 확인
- 검증: `grep -rn "collect.*web\|scrape\|crawl" JARVIS*/*_agent.py | grep -v __pycache__ | wc -l`
- ✅ YES (0건) → 1.3 | ❌ NO → 기존 에이전트 확장 권고 → ⛔ ABORT

### 1.3 신설 필요성 최종 판단
- 작업: 이벤트 버스 구독(THEME_QUEUED)→외부 수집→이벤트 발행(COLLECTION_READY) 지속 반응 사이클 — agent 필요
- 검증: `echo "schedule=event-driven, external_deps=6+, side_effect=external(HTTP), LLM=정제용 → agent 4축 중 3축 충족"`
- ✅ YES → 1.4 | ❌ NO → 대안 형태 기록 → Stage 15

### 1.4 에이전트 번호 결정
- 작업: JARVIS09 미사용 확인
- 검증: `ls -d JARVIS09_*/ 2>/dev/null | wc -l`
- ✅ YES (0) → 1.5 | ❌ NO → 번호 증가 후 1.4 재실행

### 1.5 에이전트 이름 확정
- 작업: JARVIS09_COLLECTOR — 역할을 정확히 반영하는 명칭
- 검증: `grep "jarvis09_collector" AGENTS.md | wc -l`
- ✅ YES (0) → 1.6 | ❌ NO → 이름 변경 후 1.5 재실행

### 1.6 성공 지표 정의
- 작업: ①THEME_QUEUED→COLLECTION_READY 전환율 90%+ ②수집 소스 3종+ per 테마 ③WRITER 대본 참조 데이터량 2배+ (기존 대비)
- 검증: `echo "3개 지표 확정"`
- ✅ YES → 1.7 | ❌ NO → 사용자 재질의 → ⛔ ABORT

### 1.7 단일 책임 원칙 확인
- 작업: "수집·정제" 도메인 단독 — 글쓰기(WRITER)·분석(RADAR)·발행(PUBLISH) 미침범
- 검증: `echo "도메인=collection, 타 도메인 침범 0"`
- ✅ YES → 1.8 | ❌ NO → 에이전트 분리 → ⛔ ABORT

### 1.8 외부 의존성 목록 확정
- 작업: httpx, beautifulsoup4, feedparser, yfinance, arxiv, urllib.robotparser (stdlib), readability-lxml
- 검증: `python3 -c "import httpx, bs4, feedparser, yfinance, arxiv" 2>&1 | grep -c Error`
- ✅ YES (0) → 1.9 | ❌ NO → pip install 후 1.8 재실행

### 1.9 데이터 흐름 방향 정의
- 작업: RADAR→bus(THEME_QUEUED)→COLLECTOR→bus(COLLECTION_READY)→WRITER. 부수: COLLECTOR→DB(collection_results)
- 검증: `echo "IN:THEME_QUEUED OUT:COLLECTION_READY+DB 확정"`
- ✅ YES → 1.10 | ❌ NO → 재확인 후 1.9 재실행

### 1.10 bus.py 이벤트 의존 확인
- 작업: 구독=THEME_QUEUED(기존), 발행=COLLECTION_READY(신규 EventType 필요)
- 검증: `grep "COLLECTION_READY" shared/bus.py | wc -l`
- ✅ YES (0=신규 필요 확인) → 1.11 | ❌ NO → Stage 5 신규 EventType 설계 마킹

### 1.11 shared/db.py 테이블 의존 확인
- 작업: 신규 테이블 collection_results 필요 (theme, source_type, url, raw_text, cleaned_text, collected_at)
- 검증: `grep "collection_results" shared/db.py | wc -l`
- ✅ YES (0=신규 필요) → 1.12 | ❌ NO → Stage 5 스키마 설계 마킹

### 1.12 발행 본문 한국어 포함 여부
- 작업: 수집·정제만 수행. 한국어 블로그 본문 생성 안 함 → LLM 호출 의무 OFF
- 검증: `echo "is_publishing_body=false → LLM 의무 OFF"`
- ✅ YES → 1.13 | ❌ NO → LLM 플래그 ON → 1.13

### 1.13 승인 게이트 필요 여부
- 작업: 외부 HTTP 요청(side_effect=external) — 단, 공개 데이터 읽기 전용이므로 requires_approval=False 허용 (비파괴적)
- 검증: `echo "side_effect=external, read-only, approval=False 허용"`
- ✅ YES → 1.14 | ❌ NO → Stage 7 강제 설계 마킹

### 1.14 ERRORS.md 헛다리 사전 점검
- 작업: [32][137] subprocess PATH 누락, [156] PNG 삭제 사고 — 수집 에이전트 subprocess 사용 시 PATH prepend 의무
- 검증: `grep -c "subprocess.*PATH\|env.*PATH" JARVIS07_GUARDIAN/ERRORS.md`
- ✅ YES → 1.15 | ❌ NO → 교훈 반영 후 재점검

### 1.15 Stage 1 완료 게이트
- 작업: 1.1~1.14 모두 YES 상태 체크리스트 확인
- 검증: `echo "Stage 1 미완 소단계 = 0"`
- ✅ YES → Stage 2 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 2: 도메인 소유권 & 단일 진입점 설계

### 2.1 ADR 008 도메인 매트릭스 현황 확인
- 작업: "수집(collection)" 도메인 현재 Owner 없음 확인 — WRITER가 부분 담당 중(investing.com, yfinance)
- 검증: `grep -i "수집\|collection" CLAUDE.md | grep -v "오류 수집" | wc -l`
- ✅ YES → 2.2 | ❌ NO → 담당 에이전트 확장 전환 → ⛔ ABORT

### 2.2 Owner 폴더 확정
- 작업: JARVIS09_COLLECTOR/ 단일 폴더가 수집·정제 도메인 전체 소유
- 검증: `echo "Owner=JARVIS09_COLLECTOR/ 확정"`
- ✅ YES → 2.3 | ❌ NO → 분산 이유 검토 후 단일화

### 2.3 WRITER 수집 로직 이관 대상 식별
- 작업: JARVIS02_WRITER 내 investing.com 크롤링, yfinance 호출, Naver Finance 수집 로직 목록화
- 검증: `grep -rn "yfinance\|investing.com\|naver.*finance" JARVIS02_WRITER/ --include="*.py" | wc -l`
- ✅ YES (이관 대상 식별) → 2.4 | ❌ NO → 파일 탐색 범위 확대 후 2.3 재실행

### 2.4 이관 범위 결정 (Phase 분리)
- 작업: Phase1=COLLECTOR 신규 수집 기능, Phase2=WRITER 기존 수집 로직 이관 (별도 기획)
- 검증: `echo "Phase1=신규, Phase2=이관 — 이 기획서는 Phase1만"`
- ✅ YES → 2.5 | ❌ NO → 범위 재조정 후 2.4 재실행

### 2.5 precommit_check 카테고리 신설 계획
- 작업: `domain/collection` 카테고리 추가 — 수집 로직이 JARVIS09 외 위치에 신규 작성 시 경고
- 검증: `grep "domain/collection" shared/precommit_check.py | wc -l`
- ✅ YES (0=신규) → 2.6 | ❌ NO → 기존 카테고리 재사용 검토

### 2.6 robots.txt 준수 모듈 단일 진입점
- 작업: JARVIS09_COLLECTOR/robots_guard.py — urllib.robotparser 래핑, 모든 크롤링 전 can_fetch() 의무 호출
- 검증: `echo "robots_guard.py 단일 진입점 확정"`
- ✅ YES → 2.7 | ❌ NO → 설계 재검토 후 2.6 재실행

### 2.7 소스 프로바이더 플러그인 구조 설계
- 작업: JARVIS09_COLLECTOR/providers/ 폴더 — 소스별 독립 모듈 (blog.py, news.py, academic.py, finance.py, web.py)
- 검증: `echo "providers/ 5개 모듈 구조 확정"`
- ✅ YES → 2.8 | ❌ NO → 프로바이더 분류 재검토 후 2.7 재실행

### 2.8 정제 파이프라인 단일 진입점
- 작업: JARVIS09_COLLECTOR/cleaner.py — 광고·네비게이션·중복·무관 내용 제거 + 원본 텍스트 1차 정제
- 검증: `echo "cleaner.py 단일 진입점 확정"`
- ✅ YES → 2.9 | ❌ NO → 정제 로직 분리 재설계

### 2.9 출력 데이터 표준 포맷 정의
- 작업: CollectionResult dataclass — theme, source_type, url, title, raw_text, cleaned_text, metadata, collected_at
- 검증: `echo "CollectionResult 8필드 확정"`
- ✅ YES → 2.10 | ❌ NO → 필드 추가/삭제 후 2.9 재실행

### 2.10 WRITER 연동 인터페이스 정의
- 작업: COLLECTION_READY 이벤트 payload = {theme, results: List[CollectionResult], total_sources, summary}
- 검증: `echo "payload 4필드 확정"`
- ✅ YES → 2.11 | ❌ NO → WRITER 요구사항 재확인 후 2.10 재실행

### 2.11 수집 결과 저장 경로 확정
- 작업: DB=shared/jarvis.sqlite collection_results 테이블, 파일=JARVIS09_COLLECTOR/output/{date}_{theme_slug}.json
- 검증: `echo "DB+파일 이중 저장 확정"`
- ✅ YES → 2.12 | ❌ NO → 저장 전략 재검토

### 2.12 Rate Limiting 전략 확정
- 작업: JARVIS09_COLLECTOR/rate_limiter.py — 도메인별 요청 간격 준수 (기본 2초, robots.txt Crawl-delay 우선)
- 검증: `echo "rate_limiter.py 도메인별 간격 확정"`
- ✅ YES → 2.13 | ❌ NO → 간격 재조정 후 2.12 재실행

### 2.13 이용약관 차단 목록 관리
- 작업: JARVIS09_COLLECTOR/blocked_sites.json — 크롤링 금지 도메인 목록 (수동 관리)
- 검증: `echo "blocked_sites.json 정적 차단 목록 확정"`
- ✅ YES → 2.14 | ❌ NO → 차단 방식 재설계

### 2.14 CLAUDE.md 도메인 매트릭스 갱신 계획
- 작업: CLAUDE.md 도메인 매트릭스에 | 수집 | JARVIS09_COLLECTOR/ | 인터넷 공개 데이터 수집·정제 | domain/collection ✅ | 행 추가
- 검증: `echo "매트릭스 갱신 행 확정"`
- ✅ YES → 2.15 | ❌ NO → 행 내용 재검토

### 2.15 Stage 2 완료 게이트
- 작업: 2.1~2.14 전부 YES 확인
- 검증: `echo "Stage 2 미완 소단계 = 0"`
- ✅ YES → Stage 3 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 3: 에이전트 폴더 & 파일 구조 설계

### 3.1 루트 폴더 생성 계획
- 작업: `JARVIS09_COLLECTOR/` 폴더 생성
- 검증: `ls -d JARVIS09_COLLECTOR/ 2>/dev/null | wc -l`
- ✅ YES → 3.2 | ❌ NO → mkdir 후 3.1 재확인

### 3.2 collector_agent.py 설계
- 작업: 에이전트 진입점 — declare() + register(scheduler, bus) + _status_section() + _on_theme_queued()
- 검증: `echo "collector_agent.py 4함수 시그니처 확정"`
- ✅ YES → 3.3 | ❌ NO → 시그니처 재설계 후 3.2 재실행

### 3.3 collector_engine.py 설계
- 작업: 핵심 오케스트레이터 — collect_for_theme(theme, sector) → List[CollectionResult]
- 검증: `echo "collector_engine.py 메인 함수 확정"`
- ✅ YES → 3.4 | ❌ NO → 함수 분리 재설계

### 3.4 providers/__init__.py 설계
- 작업: BaseProvider ABC 정의 — search(query, max_results) → List[RawDocument]
- 검증: `echo "BaseProvider ABC 확정"`
- ✅ YES → 3.5 | ❌ NO → 인터페이스 재설계

### 3.5 providers/blog_provider.py 설계
- 작업: 네이버 블로그·티스토리·브런치 RSS/검색 — robots.txt 준수 필수
- 검증: `echo "blog_provider.py 3소스 확정"`
- ✅ YES → 3.6 | ❌ NO → 소스 재검토

### 3.6 providers/news_provider.py 설계
- 작업: 국내외 공개 RSS 피드 + 네이버 뉴스 검색 API
- 검증: `echo "news_provider.py RSS+API 확정"`
- ✅ YES → 3.7 | ❌ NO → 소스 재검토

### 3.7 providers/academic_provider.py 설계
- 작업: arXiv Open Access API + Google Scholar (robots.txt 허용 범위)
- 검증: `echo "academic_provider.py 2소스 확정"`
- ✅ YES → 3.8 | ❌ NO → 소스 재검토

### 3.8 providers/finance_provider.py 설계
- 작업: yfinance + 네이버 금융 공개 데이터 — WRITER 기존 로직 Phase2 이관 대상과 인터페이스 호환
- 검증: `echo "finance_provider.py yfinance+NaverFin 확정"`
- ✅ YES → 3.9 | ❌ NO → 소스 재검토

### 3.9 providers/web_provider.py 설계
- 작업: 범용 공개 웹사이트 크롤링 — robots.txt + blocked_sites.json 이중 검증
- 검증: `echo "web_provider.py 범용 크롤러 확정"`
- ✅ YES → 3.10 | ❌ NO → 크롤링 범위 재검토

### 3.10 cleaner.py 설계
- 작업: clean_document(raw_html) → cleaned_text. BeautifulSoup 기반 잡음 제거 (광고·nav·footer·script·style)
- 검증: `echo "cleaner.py clean_document 함수 확정"`
- ✅ YES → 3.11 | ❌ NO → 정제 로직 재설계

### 3.11 robots_guard.py 설계
- 작업: can_crawl(url) → bool. urllib.robotparser 캐시(도메인별 1시간 TTL)
- 검증: `echo "robots_guard.py can_crawl 함수 확정"`
- ✅ YES → 3.12 | ❌ NO → 캐시 전략 재설계

### 3.12 rate_limiter.py 설계
- 작업: async-safe 도메인별 요청 간격 관리 — wait_for(domain) 호출 후 요청
- 검증: `echo "rate_limiter.py wait_for 함수 확정"`
- ✅ YES → 3.13 | ❌ NO → 동시성 전략 재설계

### 3.13 models.py 설계
- 작업: CollectionResult, RawDocument dataclass 정의
- 검증: `echo "models.py 2 dataclass 확정"`
- ✅ YES → 3.14 | ❌ NO → 필드 재검토

### 3.14 CLAUDE_COLLECTOR.md 설계
- 작업: 비직관적 규칙 (robots.txt 의무, rate limiting, blocked_sites, 정제 단일 진입점)
- 검증: `echo "CLAUDE_COLLECTOR.md 비직관 규칙 4종 확정"`
- ✅ YES → 3.15 | ❌ NO → 규칙 재검토

### 3.15 Stage 3 완료 게이트
- 작업: 3.1~3.14 전부 YES 확인. 파일 목록 = collector_agent.py, collector_engine.py, providers/(5), cleaner.py, robots_guard.py, rate_limiter.py, models.py, CLAUDE_COLLECTOR.md, blocked_sites.json
- 검증: `echo "총 13파일 구조 확정"`
- ✅ YES → Stage 4 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 4: register() & declare() 등록 구조 설계

### 4.1 agent_id 확정
- 작업: agent_id="jarvis09_collector"
- 검증: `grep "jarvis09_collector" JARVIS*/*_agent.py 2>/dev/null | wc -l`
- ✅ YES (0) → 4.2 | ❌ NO → ID 변경 후 4.1 재실행

### 4.2 declare() 파라미터 설계
- 작업: declare(agent_id="jarvis09_collector", status_fn=_status_section, help_section="/collect — 테마별 데이터 수집 상태 조회")
- 검증: `echo "declare 3 파라미터 확정"`
- ✅ YES → 4.3 | ❌ NO → 파라미터 재설계

### 4.3 register(scheduler, bus) 시그니처
- 작업: bus.subscribe(EventType.THEME_QUEUED, _on_theme_queued) 등록
- 검증: `echo "register — THEME_QUEUED 구독 확정"`
- ✅ YES → 4.4 | ❌ NO → 구독 이벤트 재검토

### 4.4 _on_theme_queued 핸들러 설계
- 작업: payload에서 theme, sector 추출 → collector_engine.collect_for_theme() 호출 → COLLECTION_READY 발행
- 검증: `echo "_on_theme_queued 흐름 확정"`
- ✅ YES → 4.5 | ❌ NO → 핸들러 흐름 재설계

### 4.5 _status_section 반환값 설계
- 작업: "🔍 JARVIS09 — COLLECTOR\n • 최근 수집: {last_theme} ({count}건)\n • 소스: {sources}" 형태
- 검증: `echo "_status_section 포맷 확정"`
- ✅ YES → 4.6 | ❌ NO → 포맷 재설계

### 4.6 intents 목록 확정
- 작업: SAFE=["collect.status", "collect.history"], APPROVAL=[] (수집은 읽기 전용 — 승인 불필요)
- 검증: `echo "SAFE 2개, APPROVAL 0개 확정"`
- ✅ YES → 4.7 | ❌ NO → intent 분류 재검토

### 4.7 capability 도구 목록
- 작업: collect_for_theme(SAFE), collect_status(SAFE) — 외부 HTTP지만 읽기 전용이므로 side_effect="external", requires_approval=False
- 검증: `echo "도구 2개, 모두 SAFE 확정"`
- ✅ YES → 4.8 | ❌ NO → 도구 분류 재검토

### 4.8 텔레그램 /help 노출 텍스트
- 작업: "/collect — 테마별 인터넷 공개 데이터 수집 현황"
- 검증: `echo "help_section 텍스트 확정"`
- ✅ YES → 4.9 | ❌ NO → 텍스트 수정

### 4.9 hub.py 대시보드 카드 설계
- 작업: "수집 현황" 탭 — 최근 10건 수집 이력 + 소스별 건수 KPI
- 검증: `echo "hub 카드 설계 확정"`
- ✅ YES → 4.10 | ❌ NO → 카드 재설계

### 4.10 WRITER 이벤트 수신측 설계
- 작업: JARVIS02_WRITER가 COLLECTION_READY 구독 → 수집 데이터를 대본 작성 컨텍스트로 활용
- 검증: `echo "WRITER 수신 설계 확정"`
- ✅ YES → 4.11 | ❌ NO → WRITER 연동 재설계

### 4.11 에러 핸들링 위임 구조
- 작업: 모든 try/except에서 JARVIS07_GUARDIAN.error_collector.report("collector", exc, ...) 호출
- 검증: `echo "GUARDIAN report 위임 확정"`
- ✅ YES → 4.12 | ❌ NO → 예외 경로 재설계

### 4.12 로깅 구조 확정
- 작업: logging.getLogger("collector") — JARVIS09_COLLECTOR/logs/ 폴더에 일자별 로그
- 검증: `echo "로그 구조 확정"`
- ✅ YES → 4.13 | ❌ NO → 로그 경로 재설계

### 4.13 동시성 모델 확정
- 작업: ThreadPoolExecutor(max_workers=3) — 프로바이더별 병렬 수집, rate_limiter가 도메인별 직렬화
- 검증: `echo "ThreadPool max_workers=3 확정"`
- ✅ YES → 4.14 | ❌ NO → 동시성 재설계

### 4.14 타임아웃 정책 확정
- 작업: 개별 요청 30초, 테마 전체 수집 5분 상한
- 검증: `echo "timeout 30s/300s 확정"`
- ✅ YES → 4.15 | ❌ NO → 타임아웃 재조정

### 4.15 Stage 4 완료 게이트
- 작업: 4.1~4.14 전부 YES 확인
- 검증: `echo "Stage 4 미완 소단계 = 0"`
- ✅ YES → Stage 5 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 5: 데이터 흐름 & 인터페이스 설계

### 5.1 THEME_QUEUED 페이로드 확인
- 작업: 기존 bus.py의 THEME_QUEUED payload = {theme, sector, ...} 구조 확인
- 검증: `grep -A5 "THEME_QUEUED" shared/bus.py | head -10`
- ✅ YES → 5.2 | ❌ NO → payload 구조 파악 후 5.1 재실행

### 5.2 COLLECTION_READY EventType 신설
- 작업: shared/bus.py EventType 클래스에 COLLECTION_READY = "collection_ready" 추가
- 검증: `grep "COLLECTION_READY" shared/bus.py | wc -l`
- ✅ YES (추가 후 1) → 5.3 | ❌ NO → bus.py 수정 후 5.2 재실행

### 5.3 COLLECTION_READY 페이로드 스키마
- 작업: {theme: str, sector: str, results: List[dict], total_sources: int, collected_at: str}
- 검증: `echo "payload 5필드 스키마 확정"`
- ✅ YES → 5.4 | ❌ NO → 필드 재검토

### 5.4 collection_results DB 테이블 DDL
- 작업: CREATE TABLE collection_results (id INTEGER PK, theme TEXT, sector TEXT, source_type TEXT, url TEXT, title TEXT, cleaned_text TEXT, metadata JSON, collected_at TEXT DEFAULT datetime('now','localtime'))
- 검증: `echo "DDL 8컬럼 확정"`
- ✅ YES → 5.5 | ❌ NO → 컬럼 재검토

### 5.5 shared/db.py init_db 갱신 계획
- 작업: init_db()의 executescript에 collection_results CREATE TABLE 추가
- 검증: `grep "collection_results" shared/db.py | wc -l`
- ✅ YES (추가 후 1+) → 5.6 | ❌ NO → DDL 추가 후 5.5 재실행

### 5.6 save_collection_result 함수 설계
- 작업: shared/db.py에 save_collection_result(theme, source_type, url, title, cleaned_text, metadata) 추가
- 검증: `echo "save_collection_result 함수 시그니처 확정"`
- ✅ YES → 5.7 | ❌ NO → 함수 시그니처 재설계

### 5.7 get_collection_results 함수 설계
- 작업: shared/db.py에 get_collection_results(theme, limit=50) → List[Row] 추가
- 검증: `echo "get_collection_results 쿼리 함수 확정"`
- ✅ YES → 5.8 | ❌ NO → 쿼리 재설계

### 5.8 RawDocument dataclass 필드 확정
- 작업: url, title, raw_html, source_type, fetched_at, metadata(dict)
- 검증: `echo "RawDocument 6필드 확정"`
- ✅ YES → 5.9 | ❌ NO → 필드 재검토

### 5.9 CollectionResult dataclass 필드 확정
- 작업: theme, source_type, url, title, raw_text, cleaned_text, word_count, metadata, collected_at
- 검증: `echo "CollectionResult 9필드 확정"`
- ✅ YES → 5.10 | ❌ NO → 필드 재검토

### 5.10 프로바이더→엔진 반환 인터페이스
- 작업: 각 Provider.search(query, max_results=10) → List[RawDocument]
- 검증: `echo "Provider.search 인터페이스 확정"`
- ✅ YES → 5.11 | ❌ NO → 인터페이스 재설계

### 5.11 엔진→정제 파이프라인 인터페이스
- 작업: cleaner.clean_document(raw_doc: RawDocument) → CollectionResult
- 검증: `echo "clean_document 인터페이스 확정"`
- ✅ YES → 5.12 | ❌ NO → 인터페이스 재설계

### 5.12 중복 제거 전략
- 작업: URL 기반 dedup — 같은 theme+url 조합 DB에 이미 존재하면 skip
- 검증: `echo "URL dedup 전략 확정"`
- ✅ YES → 5.13 | ❌ NO → dedup 전략 재설계

### 5.13 수집 결과 파일 저장 포맷
- 작업: JARVIS09_COLLECTOR/output/{date}_{theme_slug}.json — JSON Lines, 각 줄 = CollectionResult dict
- 검증: `echo "JSONL 포맷 확정"`
- ✅ YES → 5.14 | ❌ NO → 포맷 재검토

### 5.14 WRITER 수신 데이터 활용 인터페이스
- 작업: WRITER가 db.get_collection_results(theme) 호출 또는 COLLECTION_READY payload.results 직접 사용
- 검증: `echo "WRITER 수신 2경로 확정"`
- ✅ YES → 5.15 | ❌ NO → 수신 경로 재설계

### 5.15 Stage 5 완료 게이트
- 작업: 5.1~5.14 전부 YES 확인
- 검증: `echo "Stage 5 미완 소단계 = 0"`
- ✅ YES → Stage 6 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 6: 핵심 로직 & 알고리즘 설계

### 6.1 collect_for_theme 메인 오케스트레이터
- 작업: theme+sector 입력 → 검색 쿼리 생성 → 프로바이더 병렬 호출 → 정제 → dedup → DB 저장 → COLLECTION_READY 발행
- 검증: `echo "collect_for_theme 7단계 흐름 확정"`
- ✅ YES → 6.2 | ❌ NO → 흐름 재설계

### 6.2 검색 쿼리 생성 전략
- 작업: theme을 소스별 검색 쿼리로 변환 — 뉴스(theme+" 뉴스"), 블로그(theme+" 분석"), 학술(theme 영문 번역), 금융(종목코드 추출)
- 검증: `echo "소스별 쿼리 변환 4종 확정"`
- ✅ YES → 6.3 | ❌ NO → 쿼리 전략 재설계

### 6.3 블로그 프로바이더 수집 로직
- 작업: 네이버 블로그 RSS 검색 → robots_guard.can_crawl() → httpx.get() → BeautifulSoup 파싱
- 검증: `echo "blog_provider 4단계 확정"`
- ✅ YES → 6.4 | ❌ NO → 블로그 수집 로직 재설계

### 6.4 뉴스 프로바이더 수집 로직
- 작업: RSS 피드 URL 목록 (연합뉴스, 한경 등 공개 RSS) → feedparser.parse() → 본문 링크 추출 → 본문 크롤링
- 검증: `echo "news_provider RSS→본문 확정"`
- ✅ YES → 6.5 | ❌ NO → 뉴스 수집 로직 재설계

### 6.5 학술 프로바이더 수집 로직
- 작업: arxiv 패키지로 검색 → abstract + PDF 링크 반환 (전문 크롤링 안 함 — abstract만)
- 검증: `echo "academic_provider arXiv abstract 확정"`
- ✅ YES → 6.6 | ❌ NO → 학술 수집 로직 재설계

### 6.6 금융 프로바이더 수집 로직
- 작업: yfinance로 종목 데이터 + 네이버 금융 공개 페이지 크롤링 (robots.txt 허용 범위)
- 검증: `echo "finance_provider yfinance+NaverFin 확정"`
- ✅ YES → 6.7 | ❌ NO → 금융 수집 로직 재설계

### 6.7 범용 웹 프로바이더 수집 로직
- 작업: 공식 API 제공 사이트 우선 → 없으면 robots.txt+blocked_sites 이중 검증 후 httpx 크롤링
- 검증: `echo "web_provider API우선+이중검증 확정"`
- ✅ YES → 6.8 | ❌ NO → 범용 크롤링 재설계

### 6.8 정제 파이프라인 상세
- 작업: ①script/style 태그 제거 ②nav/footer/sidebar 제거 ③광고 div 패턴 제거 ④공백 정규화 ⑤최소 100자 미만 결과 폐기
- 검증: `echo "cleaner 5단계 정제 확정"`
- ✅ YES → 6.9 | ❌ NO → 정제 단계 재설계

### 6.9 중복 문서 감지 로직
- 작업: URL exact match + cleaned_text simhash(선택적) — Phase1은 URL dedup만
- 검증: `echo "Phase1 URL dedup 확정"`
- ✅ YES → 6.10 | ❌ NO → dedup 로직 재설계

### 6.10 수집 실패 폴백 전략
- 작업: 프로바이더 A 실패 → 다음 프로바이더 계속 → 전체 실패 시 GUARDIAN report + TG 알림 + 빈 결과 반환
- 검증: `echo "폴백 전략 확정"`
- ✅ YES → 6.11 | ❌ NO → 폴백 전략 재설계

### 6.11 수집량 상한 설정
- 작업: 프로바이더당 max_results=10, 테마 전체 max_total=30 — 과도한 수집 방지
- 검증: `echo "상한 10/30 확정"`
- ✅ YES → 6.12 | ❌ NO → 상한 재조정

### 6.12 LLM 호출 위치 (정제 보조)
- 작업: 정제 결과 품질이 낮을 때만 LLM 호출 (잡음 비율 50%+ 추정 시) — shared/llm.py invoke_text("collector_fast", ...) 사용
- 검증: `echo "LLM 조건부 호출 확정"`
- ✅ YES → 6.13 | ❌ NO → LLM 호출 전략 재설계

### 6.13 수집 결과 요약 생성
- 작업: 수집 완료 후 소스별 건수, 총 글자수, 주요 키워드를 1줄 summary로 생성 → COLLECTION_READY payload.summary
- 검증: `echo "summary 생성 로직 확정"`
- ✅ YES → 6.14 | ❌ NO → 요약 로직 재설계

### 6.14 개인정보 필터링
- 작업: 정제 단계에서 전화번호·이메일·주민번호 패턴 자동 마스킹 (regex 기반)
- 검증: `echo "PII 마스킹 3패턴 확정"`
- ✅ YES → 6.15 | ❌ NO → 필터링 패턴 보강

### 6.15 Stage 6 완료 게이트
- 작업: 6.1~6.14 전부 YES 확인
- 검증: `echo "Stage 6 미완 소단계 = 0"`
- ✅ YES → Stage 7 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 7: 도구 카탈로그 & 승인 게이트 설계

### 7.1 도구 전수 목록
- 작업: collect_for_theme(external, approval=False), collect_status(none, approval=False) — 2개
- 검증: `echo "도구 2개 확정"`
- ✅ YES → 7.2 | ❌ NO → 도구 재분류

### 7.2 collect_for_theme ToolMeta
- 작업: domain="collection", side_effect="external", rollback="none(읽기전용)", cost="low", requires_approval=False
- 검증: `echo "collect_for_theme ToolMeta 5필드 확정"`
- ✅ YES → 7.3 | ❌ NO → ToolMeta 재설계 → 7.2

### 7.3 external + approval=False 정당성 확인
- 작업: 외부 HTTP 요청이지만 GET 읽기 전용 — 대상 시스템에 변경 0 — CLAUDE.md 규정상 "비파괴적 외부 읽기"는 approval=False 허용
- 검증: `echo "비파괴적 읽기 전용 → approval=False 정당"`
- ✅ YES → 7.4 | ❌ NO → approval=True로 변경 → 7.2

### 7.4 collect_status ToolMeta
- 작업: domain="collection", side_effect="none", rollback="n/a", cost="zero", requires_approval=False
- 검증: `echo "collect_status ToolMeta 확정"`
- ✅ YES → 7.5 | ❌ NO → ToolMeta 재설계

### 7.5 agent_tools.py 등록 계획
- 작업: JARVIS01_MASTER/agent_tools.py에 @register_tool로 2개 도구 추가
- 검증: `grep "collect_for_theme\|collect_status" JARVIS01_MASTER/agent_tools.py | wc -l`
- ✅ YES (추가 후 2) → 7.6 | ❌ NO → 등록 코드 추가 후 7.5 재확인

### 7.6 core_agent.py CAPABILITIES.tools 갱신 계획
- 작업: CAPABILITIES.tools에 "collect_for_theme", "collect_status" 추가
- 검증: `echo "CAPABILITIES.tools 갱신 계획 확정"`
- ✅ YES → 7.7 | ❌ NO → 갱신 누락 수정

### 7.7 ensure_loaded() expected set 갱신
- 작업: expected set에 2개 도구명 추가
- 검증: `echo "ensure_loaded 갱신 계획 확정"`
- ✅ YES → 7.8 | ❌ NO → 갱신 누락 수정

### 7.8 REACT_SYSTEM_PROMPT 도구 사용 원칙 추가
- 작업: router.py의 REACT_SYSTEM_PROMPT에 "collect_for_theme — 테마별 데이터 수집 (읽기 전용)" 추가
- 검증: `echo "REACT_SYSTEM_PROMPT 갱신 계획 확정"`
- ✅ YES → 7.9 | ❌ NO → PROMPT 수정 계획 보완

### 7.9 텔레그램 진행 표시 호환
- 작업: collect_for_theme이 SAFE이므로 _run_tool_with_heartbeat 불필요 — 직접 실행 OK
- 검증: `echo "SAFE 도구 → heartbeat 불필요 확정"`
- ✅ YES → 7.10 | ❌ NO → heartbeat 추가 계획

### 7.10 도구 실행 타임아웃
- 작업: collect_for_theme 최대 5분 — 초과 시 부분 결과 반환 + TG 알림
- 검증: `echo "도구 타임아웃 300s 확정"`
- ✅ YES → 7.11 | ❌ NO → 타임아웃 재조정

### 7.11 도구 반환값 포맷
- 작업: collect_for_theme → {"status":"ok","theme":...,"total":N,"sources":{"blog":n,"news":n,...}}
- 검증: `echo "반환값 JSON 포맷 확정"`
- ✅ YES → 7.12 | ❌ NO → 포맷 재설계

### 7.12 도구 에러 반환 포맷
- 작업: {"status":"error","message":"...","partial_results":N} — 부분 성공도 반환
- 검증: `echo "에러 반환 포맷 확정"`
- ✅ YES → 7.13 | ❌ NO → 에러 포맷 재설계

### 7.13 도구 중복 호출 방지
- 작업: 같은 theme에 대해 10분 내 재호출 시 DB 캐시 결과 반환 (불필요한 외부 요청 방지)
- 검증: `echo "10분 캐시 확정"`
- ✅ YES → 7.14 | ❌ NO → 캐시 정책 재설계

### 7.14 기존 도구 카탈로그 충돌 확인
- 작업: shared/tools.py 기존 등록 도구와 이름 충돌 없는지 확인
- 검증: `grep "collect_for_theme\|collect_status" shared/tools.py | wc -l`
- ✅ YES (0) → 7.15 | ❌ NO → 이름 변경 후 7.1 재실행

### 7.15 Stage 7 완료 게이트
- 작업: 7.1~7.14 전부 YES 확인
- 검증: `echo "Stage 7 미완 소단계 = 0"`
- ✅ YES → Stage 8 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 8: 인텐트 & 라우터 연결 설계

### 8.1 SAFE 인텐트 목록 확정
- 작업: "collect.status", "collect.history" — 수집 현황 조회 + 이력 조회
- 검증: `echo "SAFE 인텐트 2개 확정"`
- ✅ YES → 8.2 | ❌ NO → 인텐트 재분류

### 8.2 APPROVAL 인텐트 목록 확정
- 작업: 없음 — 수집은 비파괴적 읽기 전용이므로 승인 필요 인텐트 0개
- 검증: `echo "APPROVAL 인텐트 0개 확정"`
- ✅ YES → 8.3 | ❌ NO → 인텐트 재분류

### 8.3 dispatchers.py SAFE_INTENTS 갱신 계획
- 작업: SAFE_INTENTS set에 "collect.status", "collect.history" 추가
- 검증: `grep "collect\." JARVIS01_MASTER/dispatchers.py | wc -l`
- ✅ YES (추가 후 2+) → 8.4 | ❌ NO → 갱신 후 8.3 재확인

### 8.4 dispatchers.py execute_safe 분기 추가 계획
- 작업: execute_safe()에 "collect.status" → collector_agent.handle_status(), "collect.history" → collector_agent.handle_history() 분기
- 검증: `echo "execute_safe 2분기 확정"`
- ✅ YES → 8.5 | ❌ NO → 분기 재설계

### 8.5 ROUTER_SYSTEM_PROMPT 갱신 계획 (★ ERRORS [29] 방지)
- 작업: intents.py ROUTER_SYSTEM_PROMPT에 "수집 현황 → collect.status", "수집 이력 → collect.history" 매핑 추가
- 검증: `grep "collect\." JARVIS01_MASTER/intents.py | wc -l`
- ✅ YES (추가 후 2+) → 8.6 | ❌ NO → PROMPT 갱신 후 8.5 재확인

### 8.6 3곳 동시 갱신 체크리스트
- 작업: ①dispatchers.py SAFE_INTENTS ②dispatchers.py execute_safe ③intents.py ROUTER_SYSTEM_PROMPT — 3곳 모두 갱신 계획 있는지 확인
- 검증: `echo "3곳 동시 갱신 계획 확정"`
- ✅ YES → 8.7 | ❌ NO → 누락 위치 보완 → 8.3

### 8.7 자유 문장 매핑 키워드 정의
- 작업: "수집 현황/상태" → collect.status, "수집 이력/결과/히스토리" → collect.history
- 검증: `echo "키워드→인텐트 매핑 확정"`
- ✅ YES → 8.8 | ❌ NO → 키워드 재정의

### 8.8 fallback 1-step 분류기 호환
- 작업: fallback 분류기에서도 collect.* 인텐트 정상 분류되는지 확인 — ROUTER_SYSTEM_PROMPT에 명시되면 자동 호환
- 검증: `echo "fallback 호환 확정"`
- ✅ YES → 8.9 | ❌ NO → fallback 분기 추가 계획

### 8.9 handle_status 함수 시그니처
- 작업: handle_status(intent, params) → {"last_theme":..., "count":..., "sources":...} 반환
- 검증: `echo "handle_status 시그니처 확정"`
- ✅ YES → 8.10 | ❌ NO → 시그니처 재설계

### 8.10 handle_history 함수 시그니처
- 작업: handle_history(intent, params) → {"recent_10": [...], "total_themes":N} 반환
- 검증: `echo "handle_history 시그니처 확정"`
- ✅ YES → 8.11 | ❌ NO → 시그니처 재설계

### 8.11 params 파라미터 추출 규칙
- 작업: "어제 수집 이력" → params={"date":"yesterday"}, "AI 관련 수집" → params={"theme":"AI"}
- 검증: `echo "params 추출 규칙 확정"`
- ✅ YES → 8.12 | ❌ NO → 추출 규칙 재설계

### 8.12 텔레그램 응답 포맷
- 작업: collect.status → "🔍 수집 현황\n최근: {theme} ({N}건)\n소스: 블로그{n} 뉴스{n} ..."
- 검증: `echo "TG 응답 포맷 확정"`
- ✅ YES → 8.13 | ❌ NO → 포맷 재설계

### 8.13 ReAct 경로 테스트 계획
- 작업: "수집 현황 알려줘" → ReAct → collect.status 정상 분류 테스트
- 검증: `echo "ReAct 테스트 계획 확정"`
- ✅ YES → 8.14 | ❌ NO → 테스트 계획 보완

### 8.14 직접 명령 테스트 계획
- 작업: 텔레그램 /collect → 상태 표시 (help_section 연동)
- 검증: `echo "직접 명령 테스트 계획 확정"`
- ✅ YES → 8.15 | ❌ NO → 테스트 계획 보완

### 8.15 Stage 8 완료 게이트
- 작업: 8.1~8.14 전부 YES 확인
- 검증: `echo "Stage 8 미완 소단계 = 0"`
- ✅ YES → Stage 9 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 9: 스케줄 & JARVIS04 잡 설계

### 9.1 스케줄 필요성 판단
- 작업: COLLECTOR는 이벤트 트리거 기반(THEME_QUEUED) — 주기적 cron 잡 불필요
- 검증: `echo "schedule_required=false 확정"`
- ✅ YES → 9.2 | ❌ NO → 잡 설계로 전환

### 9.2 정기 캐시 정리 잡 설계
- 작업: 7일 이상 된 수집 결과 파일 자동 정리 — DEFAULT_JOBS에 추가
- 검증: `echo "j09_cleanup 잡 ID 확정"`
- ✅ YES → 9.3 | ❌ NO → 정리 전략 재설계

### 9.3 DEFAULT_JOBS dict 작성
- 작업: {"id":"j09_cleanup", "name":"수집 캐시 정리", "trigger":"cron", "kwargs":{"day_of_week":"sun","hour":3,"minute":0}, "callback":"JARVIS09_COLLECTOR.collector_agent.job_cleanup_cache", "misfire_grace_time":3600, "owner":"jarvis09_collector"}
- 검증: `echo "j09_cleanup dict 확정"`
- ✅ YES → 9.4 | ❌ NO → dict 재설계

### 9.4 잡 ID 충돌 확인
- 작업: 기존 DEFAULT_JOBS에 "j09_" 접두사 잡 없음 확인
- 검증: `grep '"j09_' JARVIS04_SCHEDULER/job_registry.py | wc -l`
- ✅ YES (0) → 9.5 | ❌ NO → ID 변경 후 9.4 재확인

### 9.5 job_cleanup_cache 함수 설계
- 작업: JARVIS09_COLLECTOR/output/ 내 7일+ 된 .json 파일 삭제 + DB collection_results 30일+ 행 삭제
- 검증: `echo "cleanup 로직 확정"`
- ✅ YES → 9.6 | ❌ NO → 보존 기간 재조정

### 9.6 schedule.every 패턴 금지 확인
- 작업: collector_agent.py에 schedule.every() 또는 while True 패턴 사용 계획 없음 확인
- 검증: `echo "schedule.every/while True 미사용 확정"`
- ✅ YES → 9.7 | ❌ NO → DEFAULT_JOBS 형태로 재설계 → 9.3

### 9.7 threading.Timer 금지 확인
- 작업: 주기 반복에 threading.Timer 사용 계획 없음
- 검증: `echo "threading.Timer 미사용 확정"`
- ✅ YES → 9.8 | ❌ NO → APScheduler interval로 재설계

### 9.8 이벤트 트리거 흐름 최종 확인
- 작업: THEME_QUEUED → _on_theme_queued (bus subscribe) — APScheduler 잡 아닌 이벤트 콜백
- 검증: `echo "이벤트 콜백 방식 확정"`
- ✅ YES → 9.9 | ❌ NO → 트리거 방식 재설계

### 9.9 misfire 정책 확인
- 작업: j09_cleanup — misfire_grace_time=3600 (1시간 유예)
- 검증: `echo "misfire 3600 확정"`
- ✅ YES → 9.10 | ❌ NO → 정책 재조정

### 9.10 executor 지정
- 작업: j09_cleanup — 기본 executor (threadpool) 사용, processpool 불필요
- 검증: `echo "executor=default 확정"`
- ✅ YES → 9.11 | ❌ NO → executor 재지정

### 9.11 잡 총수 갱신 확인
- 작업: 기존 32개 + 1개 = 33개
- 검증: `grep -c '"id"' JARVIS04_SCHEDULER/job_registry.py`
- ✅ YES (추가 후 33) → 9.12 | ❌ NO → 잡 수 불일치 확인

### 9.12 잡 등록 검증 명령
- 작업: 데몬 재시작 후 j09_cleanup 잡 존재 확인
- 검증: `echo "잡 등록 검증 명령 확정"`
- ✅ YES → 9.13 | ❌ NO → 검증 방법 재설계

### 9.13 file_cleanup.py 보존 정책 연동
- 작업: shared/file_cleanup.py _RULES에 JARVIS09_COLLECTOR/output/*.json 7일 보존 추가 계획
- 검증: `echo "file_cleanup 보존 규칙 추가 계획 확정"`
- ✅ YES → 9.14 | ❌ NO → 보존 규칙 재설계

### 9.14 스케줄 단일 진입점 규정 최종 준수 확인
- 작업: JARVIS04 외 위치에 add_job/BackgroundScheduler/schedule.every 사용 계획 없음
- 검증: `echo "스케줄 단일 진입점 준수 확정"`
- ✅ YES → 9.15 | ❌ NO → 위반 코드 제거 계획 추가

### 9.15 Stage 9 완료 게이트
- 작업: 9.1~9.14 전부 YES 확인
- 검증: `echo "Stage 9 미완 소단계 = 0"`
- ✅ YES → Stage 10 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 10: 오류 감지 & GUARDIAN 연동 설계

### 10.1 오류 분류 체계 확정
- 작업: NetworkError(HTTP실패), RobotsBlocked(robots.txt 거부), ParseError(HTML파싱실패), TimeoutError(수집초과), RateLimitError(429)
- 검증: `echo "오류 5종 분류 확정"`
- ✅ YES → 10.2 | ❌ NO → 오류 분류 재설계

### 10.2 collector_engine.py 예외 처리
- 작업: collect_for_theme 전체를 try/except로 감싸고 report("collector", exc, module="collector_engine", func_name="collect_for_theme")
- 검증: `echo "engine 최상위 report 확정"`
- ✅ YES → 10.3 | ❌ NO → 예외 처리 보완

### 10.3 프로바이더별 예외 처리
- 작업: 각 Provider.search()에서 개별 try/except → report("collector", exc, module=f"providers.{name}") — 하나 실패해도 나머지 계속
- 검증: `echo "프로바이더별 독립 예외 처리 확정"`
- ✅ YES → 10.4 | ❌ NO → 예외 격리 재설계

### 10.4 HTTP 요청별 예외 처리
- 작업: httpx.get()마다 try/except — ConnectTimeout, ReadTimeout, HTTPStatusError 분기
- 검증: `echo "HTTP 요청별 예외 3분기 확정"`
- ✅ YES → 10.5 | ❌ NO → HTTP 예외 보완

### 10.5 robots.txt 파싱 실패 처리
- 작업: robots.txt 로드 실패 시 안전하게 차단 (허용 아님) — can_crawl() = False 반환
- 검증: `echo "robots.txt 파싱 실패 → 차단 확정"`
- ✅ YES → 10.6 | ❌ NO → 정책 재검토

### 10.6 GUARDIAN 자동 수정 대상 판단
- 작업: 코드 버그(ImportError, NameError) → 자동 수정 대상. 외부 실패(HTTP 4xx/5xx) → 자동 수정 불가
- 검증: `echo "자동 수정 대상 2종 확정"`
- ✅ YES → 10.7 | ❌ NO → 수정 대상 재분류

### 10.7 텔레그램 알림 레벨 설계
- 작업: 전체 수집 실패(0건) → 즉시 TG 알림, 부분 실패(일부 소스) → 로그만, 성공 → 무알림
- 검증: `echo "TG 알림 3레벨 확정"`
- ✅ YES → 10.8 | ❌ NO → 알림 레벨 재설계

### 10.8 ERRORS.md 자동 기록 연동
- 작업: GUARDIAN error_fixer 성공/실패 시 자동 ERRORS.md 기록 (기존 인프라 활용)
- 검증: `echo "ERRORS.md 자동 기록 기존 인프라 활용 확정"`
- ✅ YES → 10.9 | ❌ NO → 기록 경로 재확인

### 10.9 severity 분류 연동
- 작업: JARVIS07_GUARDIAN/severity.py classify() 사용 — 직접 severity 판단 금지
- 검증: `echo "severity.py 단일 진입점 사용 확정"`
- ✅ YES → 10.10 | ❌ NO → severity 외부 정의 제거 계획

### 10.10 쿨다운 정책 준수
- 작업: 동일 오류 60초 내 재수집 방지 — error_collector._cooldown 기존 메커니즘 활용
- 검증: `echo "쿨다운 60초 기존 메커니즘 활용 확정"`
- ✅ YES → 10.11 | ❌ NO → 쿨다운 정책 확인

### 10.11 로그 스캐너 등록 계획
- 작업: JARVIS09_COLLECTOR/logs/ 폴더를 GUARDIAN 로그 스캐너 감시 대상에 추가
- 검증: `echo "로그 스캐너 등록 계획 확정"`
- ✅ YES → 10.12 | ❌ NO → 등록 계획 보완

### 10.12 429 Rate Limit 자동 재시도
- 작업: HTTP 429 → Retry-After 헤더 존재 시 대기 후 1회 재시도, 없으면 60초 대기 후 재시도
- 검증: `echo "429 재시도 1회 확정"`
- ✅ YES → 10.13 | ❌ NO → 재시도 전략 재설계

### 10.13 DNS 해석 실패 처리
- 작업: httpx.ConnectError → 해당 도메인 30분 블랙리스트 + 다음 프로바이더 계속
- 검증: `echo "DNS 실패 30분 블랙리스트 확정"`
- ✅ YES → 10.14 | ❌ NO → 블랙리스트 정책 재설계

### 10.14 try/except pass 패턴 금지 확인
- 작업: 모든 except 블록에 최소 report() 또는 log.warning 존재 확인
- 검증: `echo "try/except pass 0건 확정"`
- ✅ YES → 10.15 | ❌ NO → pass 블록에 report 추가

### 10.15 Stage 10 완료 게이트
- 작업: 10.1~10.14 전부 YES 확인
- 검증: `echo "Stage 10 미완 소단계 = 0"`
- ✅ YES → Stage 11 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 11: 보안 & 파일 안전 경계 설계

### 11.1 URL 입력 검증
- 작업: 모든 크롤링 대상 URL에 scheme 검증 (https/http만), IP 직접 접근 차단, localhost/내부망 차단
- 검증: `echo "URL 검증 3규칙 확정"`
- ✅ YES → 11.2 | ❌ NO → 검증 규칙 보강

### 11.2 SSRF 방지
- 작업: 크롤링 URL이 private IP (10.x, 172.16-31.x, 192.168.x, 127.x) 해석 시 차단
- 검증: `echo "SSRF private IP 차단 확정"`
- ✅ YES → 11.3 | ❌ NO → SSRF 방지 보강

### 11.3 robots.txt 의무 준수 설계
- 작업: 모든 httpx.get 호출 전 robots_guard.can_crawl(url) 통과 필수 — 내부 함수도 예외 없음
- 검증: `echo "robots.txt 전수 검증 확정"`
- ✅ YES → 11.4 | ❌ NO → 검증 누락 경로 추가

### 11.4 blocked_sites.json 검증
- 작업: 요청 전 도메인이 blocked_sites.json에 있으면 즉시 차단 — robots.txt 이전 단계
- 검증: `echo "blocked_sites 사전 차단 확정"`
- ✅ YES → 11.5 | ❌ NO → 차단 순서 재설계

### 11.5 파일 경로 안전 박스
- 작업: output 저장 경로 = JARVIS09_COLLECTOR/output/ 안만 허용 — ../ 탈출 방지
- 검증: `echo "output 경로 안전 박스 확정"`
- ✅ YES → 11.6 | ❌ NO → _safe_path 적용

### 11.6 subprocess env PATH prepend (★ ERRORS [32][137])
- 작업: subprocess 사용 시 _EXTRA_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"] 항상 prepend
- 검증: `echo "PATH prepend 의무 확정"`
- ✅ YES → 11.7 | ❌ NO → PATH prepend 코드 추가

### 11.7 인증 정보 처리
- 작업: API 키 (NAVER_CLIENT_ID 등) → .env + os.environ 참조. 하드코딩 금지
- 검증: `echo "API 키 .env 참조 확정"`
- ✅ YES → 11.8 | ❌ NO → 하드코딩 제거

### 11.8 개인정보 필터링 (PII)
- 작업: 수집된 텍스트에서 전화번호(010-XXXX-XXXX), 이메일, 주민번호 패턴 마스킹
- 검증: `echo "PII 마스킹 3패턴 확정"`
- ✅ YES → 11.9 | ❌ NO → 패턴 보강

### 11.9 저작권 보호 콘텐츠 감지
- 작업: 수집 텍스트에 "무단 전재 금지", "Copyright" 등 명시적 금지 문구 포함 시 해당 결과 폐기
- 검증: `echo "저작권 감지 패턴 확정"`
- ✅ YES → 11.10 | ❌ NO → 감지 패턴 보강

### 11.10 User-Agent 설정
- 작업: httpx 기본 User-Agent를 "JarvisCollector/1.0 (research bot; +https://github.com/...)" 형태로 설정 — 투명한 봇 식별
- 검증: `echo "User-Agent 설정 확정"`
- ✅ YES → 11.11 | ❌ NO → User-Agent 재설계

### 11.11 동시 요청 수 제한
- 작업: 동일 도메인 동시 요청 1개 (rate_limiter 직렬화), 전체 동시 요청 3개 (ThreadPool)
- 검증: `echo "동시 요청 1/3 제한 확정"`
- ✅ YES → 11.12 | ❌ NO → 제한 재조정

### 11.12 응답 크기 제한
- 작업: HTTP 응답 body 최대 5MB — 초과 시 읽기 중단
- 검증: `echo "응답 5MB 상한 확정"`
- ✅ YES → 11.13 | ❌ NO → 상한 재조정

### 11.13 리다이렉트 제한
- 작업: httpx max_redirects=5 — 무한 리다이렉트 방지
- 검증: `echo "max_redirects=5 확정"`
- ✅ YES → 11.14 | ❌ NO → 리다이렉트 정책 재설계

### 11.14 로그인·페이월 감지
- 작업: 응답에 login form / paywall 키워드 감지 시 해당 페이지 수집 포기
- 검증: `echo "로그인/페이월 감지 확정"`
- ✅ YES → 11.15 | ❌ NO → 감지 로직 보강

### 11.15 Stage 11 완료 게이트
- 작업: 11.1~11.14 전부 YES 확인
- 검증: `echo "Stage 11 미완 소단계 = 0"`
- ✅ YES → Stage 12 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 12: 하네스(Harness) 연동 & Layer 설계

### 12.1 하네스 적용 범위 결정
- 작업: collect_for_theme 호출 전체를 harness ActionDefinition으로 래핑 — Layer 1~4 적용
- 검증: `echo "harness 적용 범위 확정"`
- ✅ YES → 12.2 | ❌ NO → 적용 범위 재설계

### 12.2 Layer 0 (preflight) 연동
- 작업: 데몬 부팅 시 JARVIS00_INFRA/preflight.py 자동 실행 — COLLECTOR 전용 항목 추가 불필요 (httpx import는 런타임)
- 검증: `echo "Layer 0 기존 preflight 활용 확정"`
- ✅ YES → 12.3 | ❌ NO → preflight 항목 추가

### 12.3 Layer 1 (precondition) 설계
- 작업: ①THEME_QUEUED payload 유효성 (theme 비어있지 않음) ②네트워크 연결 확인 (httpx.head google.com)
- 검증: `echo "precondition 2항목 확정"`
- ✅ YES → 12.4 | ❌ NO → precondition 재설계

### 12.4 Layer 2 Step 1: 검색 쿼리 생성
- 작업: @action_step — theme+sector → 소스별 검색 쿼리 리스트 생성
- 검증: `echo "Step 1 쿼리 생성 확정"`
- ✅ YES → 12.5 | ❌ NO → 스텝 재설계

### 12.5 Layer 2 Step 2: 프로바이더 병렬 수집
- 작업: @action_step — 5개 프로바이더 ThreadPoolExecutor 병렬 호출
- 검증: `echo "Step 2 병렬 수집 확정"`
- ✅ YES → 12.6 | ❌ NO → 스텝 재설계

### 12.6 Layer 2 Step 3: 정제 파이프라인
- 작업: @action_step — RawDocument → cleaner.clean_document() → CollectionResult
- 검증: `echo "Step 3 정제 확정"`
- ✅ YES → 12.7 | ❌ NO → 스텝 재설계

### 12.7 Layer 2 Step 4: 중복 제거
- 작업: @action_step — URL dedup + DB 기존 결과 확인
- 검증: `echo "Step 4 dedup 확정"`
- ✅ YES → 12.8 | ❌ NO → 스텝 재설계

### 12.8 Layer 2 Step 5: DB 저장
- 작업: @action_step — collection_results 테이블 + output/ 파일 저장
- 검증: `echo "Step 5 저장 확정"`
- ✅ YES → 12.9 | ❌ NO → 스텝 재설계

### 12.9 Layer 3 (verify_loop) 설계
- 작업: ①수집 결과 1건+ 존재 ②cleaned_text 100자+ ③robots.txt 위반 0건 — max_attempts=3
- 검증: `echo "verify_loop 3조건 max_attempts=3 확정"`
- ✅ YES → 12.10 | ❌ NO → 검증 조건 재설계

### 12.10 Layer 3 실패 시 GUARDIAN escalation
- 작업: max_attempts 도달 → error_collector.report() + TG 알림 + 빈 결과 반환 (송출 안 함)
- 검증: `echo "verify 실패 → GUARDIAN escalation 확정"`
- ✅ YES → 12.11 | ❌ NO → escalation 경로 재설계

### 12.11 Layer 4 (send) 설계
- 작업: 검증 통과 후 bus.publish(EventType.COLLECTION_READY, "COLLECTOR", payload) — harness send 콜백
- 검증: `echo "Layer 4 send = bus.publish 확정"`
- ✅ YES → 12.12 | ❌ NO → send 콜백 재설계

### 12.12 Layer 4 외부 응답 실패 처리
- 작업: bus.publish 실패(DB 쓰기 실패 등) → Layer 3 재진입 (송출 미완료)
- 검증: `echo "send 실패 → Layer 3 재진입 확정"`
- ✅ YES → 12.13 | ❌ NO → 재진입 경로 재설계

### 12.13 __patch_applied__ sentinel 해당 여부
- 작업: 수집은 비결정론적(매번 다른 결과) — sentinel 불필요
- 검증: `echo "sentinel 불필요 확정"`
- ✅ YES → 12.14 | ❌ NO → sentinel 추가 계획

### 12.14 harness 외부 직접 호출 금지 확인
- 작업: bus.publish를 Layer 4 send 콜백 안에서만 호출 — 엔진 내부에서 직접 publish 금지
- 검증: `echo "직접 publish 금지 확정"`
- ✅ YES → 12.15 | ❌ NO → 직접 호출 제거 계획

### 12.15 Stage 12 완료 게이트
- 작업: 12.1~12.14 전부 YES 확인
- 검증: `echo "Stage 12 미완 소단계 = 0"`
- ✅ YES → Stage 13 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 13: CLAUDE.md 규정 & 검증 명령 설계

### 13.1 CLAUDE_COLLECTOR.md 비직관 규칙 목록
- 작업: ①robots.txt 전수 검증 의무 ②blocked_sites.json 사전 차단 ③rate_limiter 도메인별 직렬화 ④정제 단일 진입점=cleaner.py ⑤output/ 경로 안전 박스
- 검증: `echo "비직관 규칙 5종 확정"`
- ✅ YES → 13.2 | ❌ NO → 규칙 보강

### 13.2 수집 코드 외부 분산 금지 규칙
- 작업: "JARVIS09_COLLECTOR 외 위치에서 httpx/requests로 범용 크롤링 코드 신규 추가 금지"
- 검증: `echo "외부 분산 금지 규칙 확정"`
- ✅ YES → 13.3 | ❌ NO → 규칙 표현 재검토

### 13.3 검증 명령 1: robots.txt 우회 크롤링 탐지
- 작업: `grep -rnE 'httpx\.get\(|requests\.get\(' JARVIS09_COLLECTOR/ --include='*.py' | grep -v 'can_crawl' | grep -v 'robots_guard' | grep -v __pycache__`
- 검증: `echo "검증 명령 1 확정"`
- ✅ YES → 13.4 | ❌ NO → grep 패턴 수정

### 13.4 검증 명령 2: blocked_sites 우회 탐지
- 작업: `grep -rnE 'httpx\.(get|post)\(' JARVIS09_COLLECTOR/providers/ --include='*.py' | grep -v 'is_blocked' | grep -v __pycache__`
- 검증: `echo "검증 명령 2 확정"`
- ✅ YES → 13.5 | ❌ NO → grep 패턴 수정

### 13.5 검증 명령 3: 수집 코드 외부 신규 작성 탐지
- 작업: `grep -rnE 'httpx\.get\(.*search|feedparser\.parse|arxiv\.Search' --include='*.py' . | grep -v 'JARVIS09_COLLECTOR/' | grep -v __pycache__ | grep -v '.venv/'`
- 검증: `echo "검증 명령 3 확정"`
- ✅ YES → 13.6 | ❌ NO → grep 패턴 수정

### 13.6 검증 명령 4: PII 마스킹 누락 탐지
- 작업: `grep -rnE 'cleaned_text.*=|clean_document' JARVIS09_COLLECTOR/ --include='*.py' | grep -v 'mask_pii' | grep -v __pycache__`
- 검증: `echo "검증 명령 4 확정"`
- ✅ YES → 13.7 | ❌ NO → grep 패턴 수정

### 13.7 검증 명령 5: User-Agent 하드코딩 탐지
- 작업: `grep -rnE 'User-Agent|user-agent' JARVIS09_COLLECTOR/ --include='*.py' | grep -v '_DEFAULT_UA' | grep -v __pycache__`
- 검증: `echo "검증 명령 5 확정"`
- ✅ YES → 13.8 | ❌ NO → grep 패턴 수정

### 13.8 CLAUDE.md 루트 도메인 매트릭스 갱신 계획
- 작업: | 수집 | `JARVIS09_COLLECTOR/` | 인터넷 공개 데이터 수집·정제 | `domain/collection` ✅ | 행 추가
- 검증: `echo "매트릭스 행 확정"`
- ✅ YES → 13.9 | ❌ NO → 행 재작성

### 13.9 CLAUDE.md 에이전트 목록 갱신 계획
- 작업: | `JARVIS09_COLLECTOR/` | JARVIS09 COLLECTOR | 테마별 인터넷 공개 데이터 수집·정제 | 행 추가
- 검증: `echo "에이전트 목록 행 확정"`
- ✅ YES → 13.10 | ❌ NO → 행 재작성

### 13.10 precommit_check.py 카테고리 추가 계획
- 작업: domain/collection 카테고리 — 5개 검증 명령 통합
- 검증: `echo "precommit_check 카테고리 추가 계획 확정"`
- ✅ YES → 13.11 | ❌ NO → 카테고리 재설계

### 13.11 CLAUDE_COLLECTOR.md @참조 계획
- 작업: 루트 CLAUDE.md에 @JARVIS09_COLLECTOR/CLAUDE_COLLECTOR.md 추가
- 검증: `echo "@참조 추가 계획 확정"`
- ✅ YES → 13.12 | ❌ NO → 참조 위치 재확인

### 13.12 파일 정리 규칙 CLAUDE.md 갱신
- 작업: 보존 정책 표에 | `JARVIS09_COLLECTOR/output/*.json` | 7일 | 행 추가
- 검증: `echo "보존 정책 행 확정"`
- ✅ YES → 13.13 | ❌ NO → 보존 기간 재조정

### 13.13 라이브러리 모듈 표 갱신 계획
- 작업: | `JARVIS09_COLLECTOR/collector_engine.py` | 수집 오케스트레이터 | bus subscribe 콜백 | 행 추가 불필요 (agent 자체 진입점이므로)
- 검증: `echo "라이브러리 모듈 표 갱신 불필요 확정"`
- ✅ YES → 13.14 | ❌ NO → 표 갱신 추가

### 13.14 공유 자원 표 갱신 확인
- 작업: shared/bus.py COLLECTION_READY 추가 — 공유 자원 표에 신규 이벤트 타입 명시
- 검증: `echo "공유 자원 표 갱신 계획 확정"`
- ✅ YES → 13.15 | ❌ NO → 갱신 계획 추가

### 13.15 Stage 13 완료 게이트
- 작업: 13.1~13.14 전부 YES 확인
- 검증: `echo "Stage 13 미완 소단계 = 0"`
- ✅ YES → Stage 14 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 14: 구현 계획 (create_plan 인자 형태)

### 14.1 신규 파일 생성 순서 확정
- 작업: ①models.py ②robots_guard.py ③rate_limiter.py ④cleaner.py ⑤providers/__init__.py ⑥providers/blog_provider.py ⑦providers/news_provider.py ⑧providers/academic_provider.py ⑨providers/finance_provider.py ⑩providers/web_provider.py ⑪collector_engine.py ⑫collector_agent.py ⑬blocked_sites.json ⑭CLAUDE_COLLECTOR.md
- 검증: `echo "14파일 생성 순서 확정"`
- ✅ YES → 14.2 | ❌ NO → 순서 재조정

### 14.2 write_file: JARVIS09_COLLECTOR/__init__.py
- 작업: 패키지 초기화 파일 — 빈 파일 또는 minimal docstring
- 검증: `python3 -c "import JARVIS09_COLLECTOR" 2>&1 | grep -c Error`
- ✅ YES (0) → 14.3 | ❌ NO → __init__.py 수정 후 재확인

### 14.3 write_file: JARVIS09_COLLECTOR/models.py
- 작업: CollectionResult, RawDocument dataclass 정의
- 검증: `python3 -c "from JARVIS09_COLLECTOR.models import CollectionResult, RawDocument"`
- ✅ YES → 14.4 | ❌ NO → models.py 수정 후 재확인

### 14.4 write_file: JARVIS09_COLLECTOR/robots_guard.py
- 작업: can_crawl(url) + _cache(도메인별 1시간 TTL)
- 검증: `python3 -c "from JARVIS09_COLLECTOR.robots_guard import can_crawl"`
- ✅ YES → 14.5 | ❌ NO → robots_guard.py 수정 후 재확인

### 14.5 write_file: JARVIS09_COLLECTOR/rate_limiter.py
- 작업: wait_for(domain) — 도메인별 2초 간격 보장
- 검증: `python3 -c "from JARVIS09_COLLECTOR.rate_limiter import wait_for"`
- ✅ YES → 14.6 | ❌ NO → rate_limiter.py 수정 후 재확인

### 14.6 write_file: JARVIS09_COLLECTOR/cleaner.py
- 작업: clean_document(raw_doc) → CollectionResult + mask_pii(text)
- 검증: `python3 -c "from JARVIS09_COLLECTOR.cleaner import clean_document"`
- ✅ YES → 14.7 | ❌ NO → cleaner.py 수정 후 재확인

### 14.7 write_file: JARVIS09_COLLECTOR/providers/ (5개 프로바이더)
- 작업: __init__.py(BaseProvider ABC) + blog_provider.py + news_provider.py + academic_provider.py + finance_provider.py + web_provider.py
- 검증: `python3 -c "from JARVIS09_COLLECTOR.providers import BlogProvider, NewsProvider, AcademicProvider, FinanceProvider, WebProvider"`
- ✅ YES → 14.8 | ❌ NO → 프로바이더 수정 후 재확인

### 14.8 write_file: JARVIS09_COLLECTOR/collector_engine.py
- 작업: collect_for_theme(theme, sector) 오케스트레이터
- 검증: `python3 -c "from JARVIS09_COLLECTOR.collector_engine import collect_for_theme"`
- ✅ YES → 14.9 | ❌ NO → engine 수정 후 재확인

### 14.9 write_file: JARVIS09_COLLECTOR/collector_agent.py
- 작업: declare() + register() + _on_theme_queued() + _status_section() + job_cleanup_cache()
- 검증: `python3 -c "from JARVIS09_COLLECTOR.collector_agent import register"`
- ✅ YES → 14.10 | ❌ NO → agent 수정 후 재확인

### 14.10 edit_file: shared/bus.py — COLLECTION_READY EventType 추가
- 작업: EventType 클래스에 COLLECTION_READY = "collection_ready" 행 추가
- 검증: `grep "COLLECTION_READY" shared/bus.py | wc -l`
- ✅ YES (1) → 14.11 | ❌ NO → bus.py 재수정

### 14.11 edit_file: shared/db.py — collection_results 테이블 + 함수 추가
- 작업: init_db() DDL + save_collection_result() + get_collection_results() 추가
- 검증: `grep "collection_results" shared/db.py | wc -l`
- ✅ YES (3+) → 14.12 | ❌ NO → db.py 재수정

### 14.12 edit_file: JARVIS04_SCHEDULER/job_registry.py — j09_cleanup 잡 추가
- 작업: DEFAULT_JOBS에 j09_cleanup dict 추가
- 검증: `grep "j09_cleanup" JARVIS04_SCHEDULER/job_registry.py | wc -l`
- ✅ YES (1) → 14.13 | ❌ NO → job_registry.py 재수정

### 14.13 edit_file: JARVIS01_MASTER/dispatchers.py — SAFE_INTENTS 추가
- 작업: SAFE_INTENTS에 "collect.status", "collect.history" + execute_safe 분기 추가
- 검증: `grep "collect\." JARVIS01_MASTER/dispatchers.py | wc -l`
- ✅ YES (2+) → 14.14 | ❌ NO → dispatchers.py 재수정

### 14.14 edit_file: JARVIS01_MASTER/intents.py — ROUTER_SYSTEM_PROMPT 추가 + AGENTS.md 갱신
- 작업: ROUTER_SYSTEM_PROMPT에 collect.* 매핑 + AGENTS.md에 jarvis09_collector 행 추가
- 검증: `grep "jarvis09_collector" AGENTS.md | wc -l`
- ✅ YES (1) → 14.15 | ❌ NO → 갱신 후 재확인

### 14.15 Stage 14 완료 게이트
- 작업: 14.1~14.14 전부 YES 확인 — 실제 파일 경로·목적 상세 기재 완료
- 검증: `echo "Stage 14 미완 소단계 = 0"`
- ✅ YES → Stage 15 진입 | ❌ NO → 미완 소단계로 🔄 ROLLBACK

---

## Stage 15: 완료 검증 & 데몬 재시작 계획

### 15.1 agent_registration_check.py 통과
- 작업: 4항목 검증 (collector_agent.py 존재, register 함수, declare 호출, AGENTS.md 행)
- 검증: `python shared/agent_registration_check.py`
- ✅ YES (0건 오류) → 15.2 | ❌ NO → 누락 항목 → Stage 4 🔄 ROLLBACK

### 15.2 precommit_check.py 전체 통과
- 작업: 전 카테고리 0건 위반
- 검증: `python3 shared/precommit_check.py`
- ✅ YES → 15.3 | ❌ NO → 위반 카테고리 → 해당 Stage 🔄 ROLLBACK

### 15.3 import 검증
- 작업: JARVIS09_COLLECTOR 전체 import 성공
- 검증: `.venv/bin/python -c "import JARVIS09_COLLECTOR"`
- ✅ YES → 15.4 | ❌ NO → ImportError → Stage 3 🔄 ROLLBACK

### 15.4 AGENTS.md 등록 행 존재
- 작업: jarvis09_collector 행 존재 확인
- 검증: `grep "jarvis09_collector" AGENTS.md | wc -l`
- ✅ YES (1+) → 15.5 | ❌ NO → AGENTS.md 갱신 후 재확인

### 15.5 EventType.COLLECTION_READY 존재
- 작업: bus.py에 COLLECTION_READY 등록 확인
- 검증: `grep "COLLECTION_READY" shared/bus.py | wc -l`
- ✅ YES (1+) → 15.6 | ❌ NO → bus.py → Stage 5 🔄 ROLLBACK

### 15.6 collection_results 테이블 생성 확인
- 작업: DB init 후 테이블 존재 확인
- 검증: `python3 -c "from shared.db import init_db; init_db(); from shared.db import get_db; print(get_db().execute('SELECT name FROM sqlite_master WHERE name=\"collection_results\"').fetchone())"`
- ✅ YES (not None) → 15.7 | ❌ NO → db.py → Stage 5 🔄 ROLLBACK

### 15.7 robots_guard.can_crawl 기본 동작 확인
- 작업: Google robots.txt 로 can_crawl 테스트
- 검증: `python3 -c "from JARVIS09_COLLECTOR.robots_guard import can_crawl; print(can_crawl('https://www.google.com/'))"`
- ✅ YES (True/False 반환) → 15.8 | ❌ NO → robots_guard → Stage 3 🔄 ROLLBACK

### 15.8 프로바이더 import 전수 확인
- 작업: 5개 프로바이더 모두 import 성공
- 검증: `python3 -c "from JARVIS09_COLLECTOR.providers import BlogProvider, NewsProvider, AcademicProvider, FinanceProvider, WebProvider; print('OK')"`
- ✅ YES → 15.9 | ❌ NO → 프로바이더 → Stage 3 🔄 ROLLBACK

### 15.9 collector_engine 단위 테스트 (dry-run)
- 작업: 빈 테마로 collect_for_theme 호출 — 에러 없이 빈 결과 반환 확인
- 검증: `python3 -c "from JARVIS09_COLLECTOR.collector_engine import collect_for_theme; r=collect_for_theme('__test__','test'); print(f'결과: {len(r)}건')"`
- ✅ YES → 15.10 | ❌ NO → engine → Stage 6 🔄 ROLLBACK

### 15.10 SAFE_INTENTS 등록 확인
- 작업: dispatchers.py에 collect.status, collect.history 존재
- 검증: `grep -c "collect\." JARVIS01_MASTER/dispatchers.py`
- ✅ YES (2+) → 15.11 | ❌ NO → dispatchers → Stage 8 🔄 ROLLBACK

### 15.11 ROUTER_SYSTEM_PROMPT 갱신 확인
- 작업: intents.py에 collect.* 매핑 존재
- 검증: `grep "collect\." JARVIS01_MASTER/intents.py | wc -l`
- ✅ YES (1+) → 15.12 | ❌ NO → intents → Stage 8 🔄 ROLLBACK

### 15.12 j09_cleanup 잡 등록 확인
- 작업: DEFAULT_JOBS에 j09_cleanup 존재
- 검증: `grep "j09_cleanup" JARVIS04_SCHEDULER/job_registry.py | wc -l`
- ✅ YES (1) → 15.13 | ❌ NO → job_registry → Stage 9 🔄 ROLLBACK

### 15.13 스케줄 단일 진입점 7종 검증
- 작업: CLAUDE.md 스케줄 검증 명령 7종 전부 0건
- 검증: `grep -rnE 'scheduler\.add_job\(' --include='*.py' JARVIS09_COLLECTOR/ | grep -v __pycache__ | wc -l`
- ✅ YES (0) → 15.14 | ❌ NO → 위반 코드 제거 → Stage 9 🔄 ROLLBACK

### 15.14 CLAUDE_COLLECTOR.md 존재 + 규칙 5종 확인
- 작업: CLAUDE_COLLECTOR.md에 비직관 규칙 5종 존재
- 검증: `wc -l JARVIS09_COLLECTOR/CLAUDE_COLLECTOR.md`
- ✅ YES (10줄+) → 15.15 | ❌ NO → CLAUDE_COLLECTOR.md 보완

### 15.15 데몬 재시작 안내 & 완료 선언
- 작업: 모든 검증 통과 → "데몬 재시작 필요" 안내
- 검증: `echo "pkill -f jarvis_daemon.py && python jarvis_daemon.py"`
- ✅ YES → 🎉 기획서 완료. docs/architect/2026-05-31_jarvis09_collector.md 저장.
- ❌ NO → 미통과 항목 → 해당 Stage 🔄 ROLLBACK

---

## 부록 A: 일관성 검증 결과

| 검증 항목 | 결과 |
|-----------|------|
| 스케줄 단일 진입점 | ✅ DEFAULT_JOBS dict 형태만 사용, schedule.every/while True 미사용 |
| 승인 게이트 | ✅ 수집은 비파괴적 읽기 전용 — approval=False 정당 |
| 한국어 하드코딩 | ✅ 발행 본문 없음 — LLM 호출 의무 해당 없음 |
| 인프라 단일 진입점 | ✅ 인프라 코드 미포함 — JARVIS00_INFRA 침범 없음 |
| 3곳 동시 갱신 | ✅ dispatchers.py SAFE_INTENTS + execute_safe + intents.py ROUTER_SYSTEM_PROMPT |
| subprocess PATH | ✅ subprocess 사용 시 _EXTRA_PATHS 항상 prepend (ERRORS [32][137]) |
| robots.txt 준수 | ✅ 모든 HTTP GET 전 can_crawl() 의무 호출 |
| 도메인 단일 진입점 | ✅ 수집 도메인 = JARVIS09_COLLECTOR 단독 소유 |

## 부록 B: ERRORS.md 헛다리 위험 평가

| 오류 번호 | 위험도 | 비고 |
|-----------|--------|------|
| [32][137] | 높음 | subprocess env PATH 누락 — COLLECTOR에서 subprocess 사용 시 반드시 _EXTRA_PATHS prepend |
| [29] | 높음 | 인텐트 3곳 동시 갱신 누락 — dispatchers+intents+ROUTER_SYSTEM_PROMPT 동시 갱신 의무 |
| [156] | 낮음 | 파일 삭제 사고 — output/ 파일 정리 시 패턴 매칭 정확성 확인 필요 |
| [168] | 중간 | 검증 커맨드 파일 누락 — grep 검증 명령에 실제 파일 경로 하드코딩 시 위치 먼저 확인 |
