# JARVIS05 VISION

## 기본 규칙
- 답변: **한국어**
- 새 기능 추가 시 → 이 파일 업데이트

---

## 역할
모든 JARVIS 에이전트의 데이터를 **수집 → 집계 → 시각화 공급** 하는 눈.

---

## 파일 맵

| 파일 | 역할 |
|------|------|
| `vision_agent.py` | 데몬 자동등록 진입점 `register(scheduler, bus)` |
| `registry.py` | 에이전트 레지스트리 + JARVIS00~04 어댑터 |
| `collector.py` | 30초 폴링 → `vision_agent_status` SQLite 저장 |
| `api_server.py` | FastAPI REST API (port 8505) |

---

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| 포트 | **8505** (VISION API) — 8500(hub.py)·8502(폐기) 와 충돌 없음 |
| 테이블 | `vision_agent_status` — VISION 전용. `shared/db._init_vision_tables()` 초기화 |
| 어댑터 추가 | `registry.py` 에 `BaseAgent` 상속 클래스 추가 → `bootstrap_builtin_adapters()` 에 등록 |
| 새 에이전트 | `shared/agent_base.BaseAgent` 상속 + `get_health()·get_metrics()` 구현 → `registry.register(인스턴스)` |
| LLM 호출 | **0** — 수집·저장·API 모두 Python 함수 호출만 |

---

## API 엔드포인트

```
GET  /api/health              VISION 자체 상태
GET  /api/agents              전체 에이전트 상태 + 메트릭
GET  /api/agents/{id}         특정 에이전트
GET  /api/metrics/summary     시스템 전체 KPI
GET  /api/registry            레지스트리 등록 목록
GET  /api/scheduler/jobs      JARVIS04 잡 목록
GET  /api/scheduler/history   잡 실행 이력
GET  /api/posts/summary       오늘 발행 현황
GET  /api/radar/trends        오늘 트렌드 상위
POST /api/collect             즉시 수집 트리거
```

Swagger UI: `http://127.0.0.1:8505/docs`

---

## 에이전트 수백 개 확장 방법

```python
# 1) shared/agent_base.BaseAgent 상속
class MyNewAgent(BaseAgent):
    agent_id     = "jarvis06_xxx"
    agent_name   = "JARVIS06 XXX"
    agent_domain = "xxx"

    def get_health(self): ...
    def get_metrics(self): ...

# 2) registry 에 등록
from JARVIS05_VISION.registry import get_registry
get_registry().register(MyNewAgent())
# → hub.py / API 에 자동 반영, 코드 수정 0
```
