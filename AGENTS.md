# 에이전트 추가 가이드

새 에이전트(JARVIS05 이후)를 데몬에 붙이는 방법. **`jarvis_daemon.py` 수정 없이** 폴더 추가만으로 자동 등록된다.

## 현재 등록된 에이전트

| agent_id | 폴더 | 역할 |
|---|---|---|
| jarvis00_infra | `JARVIS00_INFRA/` | 인프라 — 데몬 라이프사이클·/status·/restart |
| jarvis01_master | `JARVIS01_MASTER/` | 마스터 라우터 — 자유 문장 → ReAct 디스패치 |
| jarvis02_writer | `JARVIS02_WRITER/` | 블로그 자동화 — 네이버·티스토리 발행 |
| jarvis03_radar | `JARVIS03_RADAR/` | 트렌드·키워드 수집·분석 (대시보드는 `dashboard/` :9199) |
| jarvis04_scheduler | `JARVIS04_SCHEDULER/` | 단일 스케줄 진입점 — 모든 APScheduler 잡 관리 |
| jarvis05_vision | `JARVIS05_VISION/` | 비전 — 이미지 인식·캡션 (옵션) |
| jarvis06_image | `JARVIS06_IMAGE/` | 이미지 도메인 단일 진입점 — 생성·검증·dedupe·삽입·정리 |
| jarvis07_guardian  | `JARVIS07_GUARDIAN/`  | 자동 오류 처리 — 수집·DB저장·분석·자동수정 (승인 없음) |
| jarvis08_publish | `JARVIS08_PUBLISH/` | 발행 도메인 단일 진입점 — Naver/Tistory Selenium + 카테고리·쿠키 (ADR 008 완료) |
| jarvis09_collector | `JARVIS09_COLLECTOR/` | 수집 도메인 단일 진입점 — 주제별 blog·news·academic·finance·web 수집·정제 → WRITER 전달 |

## 규약

1. 루트에 폴더 생성: `JARVIS{NN}_NAME/` (예: `JARVIS09_TRADER/`)
2. 그 폴더 안에 `{name}_agent.py` 작성하고 `register(scheduler, bus)` + 모듈 레벨 `declare(...)` 정의
3. 새 잡은 `JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS` 에 dict 추가 (★ `scheduler.add_job()` 직접 호출 절대 금지 — 스케줄 단일 진입점 규정)
4. 데몬 재시작 — `_autoregister_agents()` 가 자동으로 스캔해서 `register()` 호출
5. `AGENTS.md` 표에 행 추가 + `python shared/agent_registration_check.py` 통과

## {name}_agent.py 템플릿

```python
"""JARVIS09 TRADER — 예시 에이전트"""

def _on_post_published(payload, source):
    # POST_PUBLISHED 이벤트 수신 핸들러
    # payload: {"theme":..., "platform":..., "url":..., ...}
    # source: "WRITER" 등
    print(f"published: {payload.get('theme')}")

def _status_section() -> str:
    return "📊 *JARVIS09 — TRADER*\n  • 상태: 정상"

# capability 등록 (모듈 레벨 — 데몬 capability 스캔 시 자동 실행)
try:
    from shared.capabilities import declare
    declare(
        agent_id="jarvis09_trader",
        status_fn=_status_section,
        help_section="/trader — 매매 신호 조회",
    )
except Exception:
    pass

def register(scheduler, bus):
    """데몬 부팅 시 자동 호출되는 진입점.

    scheduler: JARVIS04 가 생성한 APScheduler 인스턴스 (직접 add_job 금지)
    bus: shared.bus 모듈 (publish / subscribe / EventType 보유)
    """
    # 이벤트 구독만 OK — 스케줄 잡은 JARVIS04/job_registry.DEFAULT_JOBS 로 추가
    bus.subscribe(bus.EventType.POST_PUBLISHED, _on_post_published)
```

## 잡 추가 — `JARVIS04_SCHEDULER/job_registry.py`

`DEFAULT_JOBS` 리스트에 dict 항목 추가:

```python
{
    "job_id": "j09_trader_morning",
    "name": "트레이더 모닝잡",
    "trigger": "cron",
    "kwargs": {"hour": 9, "minute": 0},
    "callback": "JARVIS09_TRADER.trader_agent._job_morning",
    "misfire": 600,
    "owner": "jarvis09_trader",
},
```

## 이용 가능한 EventType

- `TREND_DETECTED` — RADAR 트렌드 감지
- `THEME_QUEUED` — RADAR → WRITER 파이프라인 적재
- `POST_PUBLISHED` — 발행 완료 (payload 에 theme/platform/url/analysis_id/source_keyword)
- `POST_FAILED` — 발행 실패
- `POST_ANALYZED` — 품질 분석 완료
- `POST_REVISE_APPROVED` — 사용자 수정 승인
- `POST_REVISED` — 재발행 완료
- `PERFORMANCE_UPDATED` — 조회수 갱신

## 주의사항

- 핸들러는 **idempotent** 해야 함 (재시작 시 같은 이벤트가 다시 들어올 수 있음)
- 핸들러에서 발생한 예외는 격리됨 (다른 핸들러나 데몬 멈추지 않음). 실패 시 자체 로깅 필수
- 스케줄 잡 `id` 는 데몬 전체에서 unique 해야 함 — 충돌 시 APScheduler 가 거부
- 공유 DB 사용 시 `from shared import db` — 같은 파일 잠금/WAL 정책 따름
- 외부 API 호출 시 timeout 명시 — 무한 블록 시 데몬 메인 루프가 영향받지 않지만 잡 자체는 정지
