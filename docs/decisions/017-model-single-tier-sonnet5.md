# 017. 모델 단일 계층 통일 — 시스템 전역 한 모델

## 상태
확정 (사용자 박제 2026-07-06) — 종전의 모델 다계층 ADR 2건(002·015)을 대체.
**2026-07-24 개정**: 폐기된 모델 이름을 저장소 전역에서 일소하면서 본 ADR 본문도
*살아있는 모델만 지칭*하도록 재작성 (ADR 002·015 파일은 삭제). 근거는 ERRORS [491].

## 배경
초기 설계는 업무 성격별로 모델을 나눴다 — 글쓰기는 값싼 하위 모델, 코드 수정·진단·평가는
비싼 상위 모델. 이 다계층 구조에서 실제 사고가 났다:

- `JARVIS07_GUARDIAN/guardian_agent.py` 의 `job_retry_pending` (10분 간격)이 `error_log` 에
  'analyzing' 상태로 멈춰 있던 오류 15건을 반복 재시도하며 Tier 2 LLM 폴백(당시 상위 계층
  모델)을 계속 호출 — 사용자가 인지하지 못한 사이 토큰이 지속 소모됐다.
- 사용자 판단: 계층 구조 자체가 "이 작업엔 어느 모델을 쓰는가"라는 *판단 지점* 을 코드
  곳곳에 늘려, 의도치 않은 고비용 모델 반복 호출의 여지를 만든다.

## 결정
**모델 계층을 완전히 폐지하고 시스템 전역을 단일 모델로 통일한다.**

- `shared/llm.py` 의 `MODELS: dict[str, ModelSpec]` 이 **모델 ID 리터럴의 유일한 소유자**.
  모든 alias 가 같은 `model_id` 를 가리킨다.
- 다른 파일은 ID 문자열을 쓰지 않고 **`from shared.llm import model_id` 로 파생**한다
  (`model_id("guardian")`). 표시용 이름은 `model_label(alias)`.
- alias(coder·guardian·architect·diagnostic·learn_eval·fact_judge·engagement_judge ...) 자체는
  *용도 구분 라벨* 로 유지 — 어떤 호출이 무슨 목적인지 로그·디버깅에 필요하다. 가리키는
  실제 모델 ID 만 전부 동일.
- 모든 호출은 `shared.llm.invoke_text(alias, ...)` 단일 함수 경유 (ADR 001 단일 진입점).

| 업무 성격 | 모델 |
|----------|------|
| 글 작성·감수·라우팅·비상 폴백 *및* 코드 수정·진단·자가학습 평가·헌법 정제·사실성/매력도 게이트 (전체) | `MODELS` 단일 ID |

## 이유
1. **고비용 모델 오호출 구조적 차단** — 계층이 있으면 "이 작업은 상위 모델이 필요한가?"
   판단이 alias 매핑에 분산돼, 스케줄러 버그 하나가 고비용 계층을 반복 호출할 수 있다.
   단일 모델이면 이 실패 모드 자체가 사라진다.
2. **이원화 유지비용 제거** — `MODELS` 한 곳만 보면 "지금 시스템이 무슨 모델을 쓰는지"가
   전부 드러난다. 계층 간 불일치·문서 드리프트 위험이 원천 차단된다.
3. **품질 격차 축소** — 현행 세대는 코드 수정·진단에도 충분하다는 사용자 판단.

## 포기한 대안
1. **계층 유지 + 사고 잡(job_retry_pending)만 패치** — 근본 원인(계층 구조가 고비용 재호출
   경로를 허용)이 남아 유사 사고 재발 가능. 사용자가 전역 통일을 명시 요구해 채택 안 함.
2. **alias 자체 삭제** — 호출 목적별 로그 라벨과, 향후 계층 재도입 시 되돌리기 편의를 위해
   alias 구조는 유지하고 `model_id` 값만 통일.
3. **폐기 모델 이름을 문서에 역사로 보존** — 2026-07-06 개정판은 그렇게 했다. 그러나
   *이름이 남아 있으면 다음 작업자·자가수정 LLM 의 손이 그리로 간다*. 실제로 폐기된 모델
   ID 가 프로젝트 설정 파일에 핀으로 살아남아 있었다(ERRORS [491]). 그래서 2026-07-24
   개정에서 **저장소 전역 일소** 로 전환 — 역사는 "구세대 다계층" 같은 *성격* 으로 서술한다.

## 결과 (2026-07-24 개정 기준)
- `shared/llm.py` 에 **`model_id(alias)` / `live_model_ids()` 공개 파생 API** 신설, `__all__` 등재.
- 저장소 내 모델 ID 리터럴 사본 6곳 제거 → 전부 `model_id()` 파생
  (`JARVIS07_GUARDIAN/auto_repair.py` `_MODEL`, `JARVIS01_MASTER/agent_tools.py` 3곳,
  `shared/claude_sdk_compat.py` `run_sdk_query(model=None)` 지연 파생 + docstring).
- `shared/precommit_check.py` `check_model()` 전면 재작성 — 유효 ID 목록을 **박지 않고**
  `shared/llm.py` 원문을 매 실행 파싱해 파생, 못 읽으면 통과가 아니라 `model/self-check`
  위반(fail-closed). 검사 대상에 **`.md` 문서 포함** (흔적은 주석·문서에 더 오래 남는다).
- 폐기 모델만 다루던 ADR 002·015 **파일 삭제**, `docs/decisions/README.md` 색인 정리.
- `.claude/settings.json` 의 죽은 모델 핀 제거 + 전역 `~/.claude/settings.json` 의 죽은 모델
  permission 항목 제거.

## 변경 정책
모델 교체·계층 재분리는 *반드시* 본 ADR 갱신 + `shared/llm.py` `MODELS` 수정 두 곳만으로
끝나야 한다. 코드·문서 어디에도 모델 ID 사본을 만들지 말 것 — `precommit --category model`
이 커밋·부팅·GUARDIAN 잡 3곳에서 이를 강제한다.
