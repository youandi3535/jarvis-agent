`docs/architect/2026-05-31_jarvis09_collector.md` 에 JARVIS09_COLLECTOR 완전 설계 기획서를 저장했습니다.

**요약:**
- **15 Stage × 15 소단계 = 225개** 전수 작성 완료
- **핵심 구조**: THEME_QUEUED 이벤트 구독 → 5개 프로바이더 병렬 수집(blog/news/academic/finance/web) → cleaner 정제 → COLLECTION_READY 발행 → WRITER 전달
- **안전 설계**: robots_guard(robots.txt 전수 검증), blocked_sites.json, rate_limiter(도메인별 직렬화), SSRF 방지, PII 마스킹
- **기존 시스템 연동**: DEFAULT_JOBS 1개(j09_cleanup), SAFE_INTENTS 2개, dispatchers+intents+ROUTER_SYSTEM_PROMPT 3곳 동시 갱신, ERRORS [29][32][137] 헛다리 반영

## 부록 A: 일관성 검증 결과
| 검증 항목 | 결과 |
|-----------|------|
| 스케줄 단일 진입점 | ✅ |
| 승인 게이트 (external→approval) | ✅ |
| 한국어 하드코딩 (발행 본문) | ✅ |
| 인프라 단일 진입점 | ✅ |
| 3 곳 동시 갱신 | ✅ |
