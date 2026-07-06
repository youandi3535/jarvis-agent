# 017. 모델 단일 계층 통일 — Sonnet 5 하나로 (Opus 4.8 폐지)

## 상태
확정 (사용자 박제 2026-07-06) — [ADR 015](015-model-tier-upgrade.md) (Sonnet 5 / Opus 4.8 2계층 단일화) 대체.

## 배경
ADR 015 (2026-07-04) 는 3계층(Haiku/Sonnet 4.6/Opus 4.6)을 2계층(Sonnet 5 글쓰기·라우팅 /
Opus 4.8 코드수정·진단·평가)으로 단순화했다. 그러나 이후:

- `JARVIS07_GUARDIAN/guardian_agent.py` 의 `job_retry_pending` (10분 간격, `JARVIS04_SCHEDULER`
  DEFAULT_JOBS 등록)이 `error_log` 에 'analyzing' 상태로 멈춰있던 오류 15건을 반복 재시도하며
  Tier 2 LLM 폴백(Opus 4.8, `error_analyzer.analyze_llm_only`)을 계속 호출 — 사용자가 인지하지
  못한 사이 토큰이 지속 소모되는 사고 발생 (Max 구독 $200/월인데도 이상 소모 감지로 발견).
- 사용자 판단: 2계층 구조 자체가 "이 작업엔 어느 모델을 쓰는가"라는 판단 지점을 늘려, 의도치
  않은 고비용 모델(Opus 4.8) 반복 호출의 여지를 만든다. 계층을 아예 없애고 시스템 전역 LLM
  호출을 Sonnet 5 하나로 통일하면 이런 종류의 "몰랐던 고비용 모델 소모"가 구조적으로 불가능해짐.
- Sonnet 5 자체가 이전 세대 대비 코드 수정·진단 품질도 이미 충분히 개선되어, 별도 고비용
  계층 유지의 실익이 ADR 015 도입 시점보다 더 작아짐.

## 결정
*모델 계층을 완전히 폐지하고 단일 모델로 통일*. `shared/llm.py` 의 `MODELS: dict[str, ModelSpec]`
7개 alias(coder·guardian·architect·diagnostic·learn_eval·fact_judge·engagement_judge) 전부
`model_id="claude-sonnet-5"` 로 통일. Opus 4.8 관련 alias 분리·설명 문구 전량 제거.

| 업무 성격 | 모델 |
|----------|------|
| 글 작성·감수·라우팅·비상 폴백 *및* 코드 수정·진단·자가학습 평가·헌법 정제·사실성/매력도 게이트 (전체) | `claude-sonnet-5` 단일 |

모든 호출은 여전히 `shared.llm.invoke_text(alias, ...)` 단일 함수 경유 — 직접 모델명 박는
행위 금지 (ADR 002/015 원칙 유지). alias 자체(coder/guardian/... )는 *용도 구분 라벨*로는
유지 — 어떤 호출이 무슨 목적인지 로그·디버깅에 필요하기 때문. 다만 alias 가 가리키는 실제
`model_id` 는 전부 동일.

## 이유
1. **고비용 모델 오호출 구조적 차단**: 2계층에서는 "이 작업은 Opus 급이 필요한가?" 판단이
   코드 곳곳(alias 매핑)에 분산돼 있어, 스케줄러 버그 하나가 의도치 않게 고비용 계층을
   반복 호출할 여지가 있었다(job_retry_pending 사고). 단일 모델이면 이 실패 모드 자체가 사라짐.
2. **세대/계층 이원화 유지비용 제거**: `MODELS` dict 를 한 번만 스캔해도 "지금 시스템이 어떤
   모델을 쓰는지" 전부 파악 가능 — 계층 간 불일치·문서 드리프트 위험 원천 차단.
3. **품질 격차 축소**: Sonnet 5 세대의 코드 수정·진단 성능이 ADR 015 시점 대비 이미
   Opus 4.8 급 작업에도 충분히 대응 가능하다는 사용자 판단.

## 포기한 대안
1. **Opus 4.8 유지 + job_retry_pending 만 패치**: 근본 원인(2계층 구조가 고비용 재호출 경로를
   구조적으로 허용)은 그대로 남아 유사 사고 재발 가능 — 사용자가 "모든 LLM을 Sonnet 5로
   통일"을 명시적으로 요구해 채택하지 않음.
2. **alias 자체 삭제(coder/guardian/... 통합)**: 호출 목적별 로그 라벨·향후 계층 재도입 시
   되돌리기 편의를 위해 alias 구조는 유지, model_id 값만 통일하는 쪽을 선택.
3. **ERRORS.md·구 ADR(002/005/007/015) 본문 재작성**: 역사적 사고 기록·과거 결정 배경 서술은
   재발 방지 학습 자산이자 저장소 컨벤션(ADR README 변경 정책)상 보존 대상 — 그대로 둠.
   ADR 015 는 상태 줄에 "대체됨" 주석만 추가, 배경·결정·이유·포기한 대안 섹션 원문 보존.

## 결과
- `shared/llm.py` `MODELS` dict 전체 7-alias `model_id="claude-sonnet-5"` 단일화 + 관련
  docstring(`pretty_model_id`/`invoke_text`) 갱신.
- `shared/precommit_check.py` `check_model()` 의 `valid_ids = {"claude-sonnet-5"}` (Opus 4.8 제거).
- 코드: `JARVIS07_GUARDIAN/{auto_repair,architecture,auditor,error_collector,pattern_fixer,
  eval_agent,incident_responder,error_analyzer}.py`, `JARVIS01_MASTER/agent_tools.py`,
  `shared/{db,claude_sdk_compat}.py`, `jarvis_daemon.py`, `hub.py`,
  `JARVIS03_RADAR/post_quality_analyzer.py` 의 "Opus 4.8" 문구·모델 리터럴 전수 교체 (2026-07-06).
- 문서: 루트 `CLAUDE.md`, `JARVIS02_WRITER/CLAUDE_WRITER.md`, `README.md` 의 "Opus 4.8" 언급
  전수 교체(2026-07-06).
- `docs/decisions/015-model-tier-upgrade.md` 상태를 `대체됨 by 017` 로 갱신, 배경·결정·이유·
  포기한 대안 서술은 역사 그대로 보존.
- `JARVIS07_GUARDIAN/ERRORS.md` 의 Opus 4.8 관련 과거 인시던트 기록은 변경하지 않음(역사 보존).
- ★ 별도 조치 — `job_retry_pending` 근본 원인(오류가 'analyzing' 상태로 멈춰 반복 재시도되며
  LLM 폴백을 계속 호출한 경위)은 `JARVIS07_GUARDIAN/guardian_agent.py` 자체의 안정성 보강
  대상으로 별도 처리(재발 방지책은 이 ADR 의 모델 통일과 별개로 진행).

## 변경 정책
모델 alias 추가·교체는 *반드시* 본 ADR 갱신 + `shared/llm.py` `MODELS` 수정. 신규 세대 모델
도입 또는 계층 재분리 시 이 ADR 을 "번복" 절차(README.md 변경 정책)로 대체 — 새 ADR 작성 +
본 ADR 상태를 `폐기 (날짜) — 대체됨 by NNN-...` 로 갱신.
