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
| `collectors/google_collector.py` | Google Trends (pytrends) 수집 |
| `collectors/naver_collector.py` | 네이버 DataLab + 자동완성 수집 |
| `data/trends_YYYY-MM-DD.json` | 날짜별 수집 데이터 캐시 |
| `app.py` | 레거시 대시보드 (폐기 — 수집/분석 로직 참조용으로만 보존) |

> **대시보드 단일 진입점**: 루트 `hub.py` (port 9199) — 모든 JARVIS 컴포넌트 통합 현황판.

---

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
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
