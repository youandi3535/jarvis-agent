"""JARVIS04_SCHEDULER — 모든 에이전트 스케줄 단일 진입점.

★ 시스템 내 *모든* APScheduler 잡 (JARVIS02·02·INFRA·신규 에이전트) 의
컨트롤 타워. 등록·조회·이력·제어 모두 여기서.

규약:
- 신규 잡 등록 → `job_registry.DEFAULT_JOBS` 또는 자기 도메인 register() 사용.
- 잡 실행 이력 → `job_runs` 테이블에 자동 적재 (EventListener).
- 잡 제어 (pause/resume/run_now/remove) → `job_controller`.
- 다른 폴더에서 `scheduler.add_job(...)` 직접 호출 금지 (CLAUDE.md 강제 규정).
"""
from __future__ import annotations
