# JARVIS09 COLLECTOR

## 역할
JARVIS03 RADAR가 결정한 주제를 받아 인터넷 공개 데이터(블로그·뉴스·논문·금융·웹)를 수집·정제하여 JARVIS02 WRITER에 전달.

## 핵심 원칙 (절대 준수)
- **robots.txt 준수**: can_crawl() 항상 확인. Disallow 경로 접근 절대 금지.
- **공식 API 우선**: yfinance, arXiv API, Wikipedia API, RSS Feed — 직접 HTML 크롤링 최후 수단.
- **속도 제한**: 도메인당 2초 간격 (rate_limiter.wait_for()).
- **요약 금지**: 잡음만 제거한 원본 텍스트 전달. 내용 요약·변형 금지.
- **개인정보 마스킹**: 전화·이메일·주민번호 마스킹 후 전달.

## 이벤트 버스 연동
- **구독**: `bus.EventType.THEME_QUEUED` (JARVIS03 발행)
- **발행**: `bus.EventType.COLLECTION_READY` (JARVIS02 수신)

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| 수집 결과 0건 | COLLECTION_READY 발행 안 함 (JARVIS02 빈 데이터 처리 불필요) |
| 단어 30개 미만 문서 | 제외 (너무 짧은 조각 → 노이즈) |
| 병렬 수집 workers | max 3 (과도한 동시 요청 방지) |
| DB 캐시 보존 기간 | 7일 (j09_cleanup 잡 주 1회) |
| blocked_sites.json | 크롤링 금지 도메인 목록 — 수정 시 이 파일만 |
| ★ 차트용 실데이터 (ADR 010) | `chart_data.collect_chart_data(theme, sector, description)` — 주제 연관 수치 데이터를 *출처(provenance) 박제* 와 함께 반환. JARVIS06 이 "데이터 줘"만 요청, JARVIS09 가 provider선택·파싱·출처까지. dataset 은 반드시 `source={provider,name,url,as_of}` 보유 |
| ★ 무료 라이브러리 자동설치 (ADR 010) | `lib_bootstrap.ensure_lib(import_name)` — **갯수 제한 없이** 무료 데이터 라이브러리 승인 없이 pip 설치. 안전 정책 게이트로 통제: ① `_DENYLIST` 아님 ② PyPI 실존 ③ 상용 전용 라이선스 아님. `_KNOWN_DATA_LIBS` 는 import↔pip 매핑 편의표(상한 아님) |
