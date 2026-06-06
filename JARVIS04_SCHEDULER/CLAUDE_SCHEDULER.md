# JARVIS04_SCHEDULER

## 역할
모든 에이전트의 APScheduler 잡 *단일 진입점* — 등록·조회·이력·제어.

## 비직관적 파일 역할
- `scheduler_agent.py` — 진입점. capability·도구 9개·register(scheduler, bus).
- `job_registry.py` — DEFAULT_JOBS 카탈로그 (단일 진실 소스). 모든 default 잡 정의.
- `job_history.py` — APScheduler EventListener → job_runs 자동 적재.
- `job_catalog.py` — APScheduler 인스턴스 wrap (`set_apscheduler` / `get_apscheduler`).
- `job_controller.py` — pause/resume/run_now/remove (모두 APPROVAL).
- `briefing.py` — 일일 잡 리포트 빌드.

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| 새 default 잡 추가 | `job_registry.DEFAULT_JOBS` 에 dict 추가. 다른 파일 add_job 금지 |
| APScheduler 인스턴스 접근 | `job_catalog.get_apscheduler()` 만. 다른 폴더에서 `_apscheduler` 직접 참조 금지 |
| 잡 변경 (pause/resume/run/remove) | 도구 9개 중 APPROVAL 4개 — 텔레그램 인라인 버튼 통과 후만 |
| EventListener | `job_history.attach_listeners(scheduler)` — 데몬 부팅 시 1회. 중복 attach 금지 |
| 잡 ID 충돌 | `replace_existing=True` 기본 — 같은 ID 재등록 시 덮어쓰기 |
| 데몬 재시작 후 상태 | 메모리 잡스토어 — pause/remove 모두 초기화. 영구 변경은 DEFAULT_JOBS 수정 |
| legacy JARVIS02 schedule 라이브러리 잡 | EventListener 못 잡음. events 테이블의 post_published 로 우회 추적 |
