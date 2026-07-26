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
| `theme_picker.py` | **★ 테마 주제 선정 (사용자 박제 2026-07-18 — 역할 02→03 이관)** — 네이버 금융 공식 테마 카탈로그(JARVIS09 경유)에서 미사용 테마 고정우선→random 선정. `theme_catalog`/`available_themes(exclude)`/`pick_theme(candidates,pinned)`/`select_theme`. 발행 상태(published/done)는 호출자(02)가 `exclude` 로 넘김(03→02 역참조 회피). 경제(topic_pack)와 동렬 — 주제 선정은 RADAR 영역 |
| `collectors/google_collector.py` | Google Trends (pytrends) 수집 |
| `collectors/naver_collector.py` | 네이버 DataLab + 자동완성 수집 |
| `data/trends_YYYY-MM-DD.json` | 날짜별 수집 데이터 캐시 |
| `data/topic_pack_YYYY-MM-DD.json` | 주제 패키지 (키워드·프로필·선수집 datasets/docs) |
| `app.py` | 레거시 대시보드 (폐기 — 수집/분석 로직 참조용으로만 보존) |

> **대시보드 단일 진입점**: `dashboard/` Next.js (port **9199**) + `api_server.py` FastAPI (port **9198**) — 모든 JARVIS 컴포넌트 통합 현황판.
> 둘 다 `jarvis_daemon.py` 가 *자식 프로세스* 로 스폰 (`_start_next` / `_start_api`) — 데몬이 떠야 대시보드가 열린다.
> 옛 `hub.py` (Streamlit) 는 커밋 `0be08d9` 에서 **삭제·폐기**. Streamlit 은 코드베이스에서 완전 제거됨.

---

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| **★★ 키워드 단독 전송 금지 (사용자 박제 2026-07-03 — 강제사항, 예외 없음)** | 자비스03이 트렌드 키워드를 *누구에게 보내든* 키워드만 딸랑 보내는 것 절대 금지 — **항상** 키워드를 설명하는 기본 정보(한줄 정의·관련어·엔티티 유형)를 동봉. 예: '배' 는 과일·선박·인체 중 무엇인지 프로필 없이는 하류가 판별 불가 ('은행나무' 사고 ERRORS [290]). 단일 진입점: `topic_pack.keyword_profile()` / 팩 후보는 프로필 필수 구조. 새 키워드 전달 경로 추가 시 이 헬퍼 경유 의무 |
| **★ 주제 패키지 (사용자 박제 2026-07-03 · 빌드 시점 2026-07-26 개정)** | 자비스03→02·09 동시 제공 구조. **팩 빌드는 아침 잡 1회만** — `job_collect_trends_morning` → `build_topic_pack_once()`. 09/12/15 트렌드 수집은 **수집만** 하고 팩을 다시 만들지 않는다(그 판을 먹는 소비자가 없다: 경제 발행 07:00 은 06시 판을 이미 먹었고, 테마 21:00 은 `theme_picker` 를 쓴다 — 토큰 절감). 팩 부재·소진 시 복구는 종전대로 `pick_slot_candidate()` 안의 즉석 `build_topic_pack()` (동일 단일 경로 — 별도 폴백 금지). `_ECON_SECTORS` 는 `trend_economic_writer` 와 동치 유지 (03→02 import 순환 금지라 로컬 보유) |
| **★ 경제 중복회피 원장 = DB 파생 (2026-07-23 정정)** | `topic_pack._used_keywords(days)` 는 `post_analysis` 에서 `post_type='economic'` 최근 N일 `source_keyword` 를 조회한다. 종전엔 `data/used_economic_keywords.json` 사본을 읽었는데, 그 파일을 적재하던 유일한 코드(02 레거시 `run_naver/run_tistory`)가 harness 전환으로 죽으면서 **파일이 존재조차 하지 않았다** → 7일 중복 제외가 *조용히 무력화*(항상 빈 집합)된 채 돌고 있었다. 원장 사본을 다시 만들지 말 것 |
| 포트 | **9199** (Next.js 대시보드) · **9198** (FastAPI API) — 8500·8502·hub.py 는 폐기 |
| pytrends 배치 | 5개씩 처리 + 1.5초 딜레이 (rate limit) |
| 데이터 캐시 | `data/trends_YYYY-MM-DD.json` — 날짜별 파일 캐시. 대시보드는 `/api/trends` 경유 SWR 로 조회 |
| Naver DataLab | `.env`에 `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` 필요 (없어도 Google만으로 동작) |

---

## .env 추가 항목
```
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
```
→ 네이버 개발자 센터(developers.naver.com)에서 무료 발급
