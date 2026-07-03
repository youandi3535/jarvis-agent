# JARVIS03 RADAR

## 기본 규칙
- 답변: **한국어**
- 새 기능 추가 시 → 이 파일 업데이트

---

## 파일 맵

| 파일/폴더 | 역할 |
|-----------|------|
| `radar_main.py` | 데이터 수집 + 저장 오케스트레이터 (메인 로직) |
| `analyzer.py` | 섹터 분류 + 점수 계산 + 추천 생성 |
| `topic_pack.py` | **★ 주제 패키지 파이프라인 (사용자 박제 2026-07-03)** — 경제 주제+프로필 생성 → JARVIS09 직접 선수집 → 자비스02·09 동시 제공. 경제 브리핑 주제의 *유일한* 공급 경로 (02 자체 선정·수집 폐지, 폴백 없음) |
| `collectors/google_collector.py` | Google Trends (pytrends) 수집 |
| `collectors/naver_collector.py` | 네이버 DataLab + 자동완성 수집 |
| `data/trends_YYYY-MM-DD.json` | 날짜별 수집 데이터 캐시 |
| `data/topic_pack_YYYY-MM-DD.json` | 주제 패키지 (키워드·프로필·선수집 datasets/docs) |
| `app.py` | 레거시 대시보드 (폐기 — 수집/분석 로직 참조용으로만 보존) |

> **대시보드 단일 진입점**: 루트 `hub.py` (port 9199) — 모든 JARVIS 컴포넌트 통합 현황판.

---

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| **★★ 키워드 단독 전송 금지 (사용자 박제 2026-07-03 — 강제사항, 예외 없음)** | 자비스03이 트렌드 키워드를 *누구에게 보내든* 키워드만 딸랑 보내는 것 절대 금지 — **항상** 키워드를 설명하는 기본 정보(한줄 정의·관련어·엔티티 유형)를 동봉. 예: '배' 는 과일·선박·인체 중 무엇인지 프로필 없이는 하류가 판별 불가 ('은행나무' 사고 ERRORS [290]). 단일 진입점: `topic_pack.keyword_profile()` / 팩 후보는 프로필 필수 구조. 새 키워드 전달 경로 추가 시 이 헬퍼 경유 의무 |
| **★ 주제 패키지 (사용자 박제 2026-07-03)** | 자비스03→02·09 동시 제공 구조. `job_collect_trends` 말미 자동 생성 + 02 소비 시 부재면 즉석 `build_topic_pack()` (동일 단일 경로 — 별도 폴백 금지). `_ECON_SECTORS` 는 `trend_economic_writer` 와 동치 유지 (03→02 import 순환 금지라 로컬 보유) |
| 포트 | **9199** (hub.py 통합 대시보드) — 8500·8502 는 폐기 |
| pytrends 배치 | 5개씩 처리 + 1.5초 딜레이 (rate limit) |
| 데이터 캐시 | `data/trends_YYYY-MM-DD.json` — Streamlit `@cache_data(ttl=300)` |
| Naver DataLab | `.env`에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 필요 (없어도 Google만으로 동작) |

---

## .env 추가 항목
```
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```
→ 네이버 개발자 센터(developers.naver.com)에서 무료 발급
