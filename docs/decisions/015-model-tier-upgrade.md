# 015. 모델 계층 상향 — Sonnet 5 / Opus 4.8 2계층 단일화 (Haiku 폐지)

## 상태
대체됨 (2026-07-06) by [ADR 017](017-model-single-tier-sonnet5.md) (Sonnet 5 단일 통일 — Opus 4.8 폐지).
원 확정일 2026-07-04 — [ADR 002](002-model-layering.md) (Haiku / Sonnet 4.6 / Opus 4.6 3계층) 대체.

## 배경
ADR 002 가 도입한 3계층 (Haiku 4.5 글쓰기 / Sonnet 4.6 라우팅 / Opus 4.6 코드 수정·진단) 은
2026-05 시점 모델 세대 기준의 비용·정확도 트레이드오프였다. 이후:
- Sonnet 5 / Opus 4.8 세대가 GA 되어 이전 세대(Sonnet 4.6·Opus 4.6·Haiku 4.5) 대비
  글쓰기·코드 수정 모두에서 더 나은 성능을 동일하거나 개선된 비용 구조로 제공.
- 시스템 전역에 3개 세대 모델 ID (`claude-haiku-4-5`, `claude-sonnet-4-6`, `claude-opus-4-6`)
  가 코드·주석·문서에 흩어져 있어, 신규 모델 도입 시마다 갱신 누락 위험 (실제로 이번
  마이그레이션에서 코드 6개 파일 + 문서 8개 파일에 잔존 발견).
- 사용자 판단: Haiku 계층을 유지할 만큼 "글쓰기 전용 저비용 모델"의 실익이 크지 않고,
  오히려 2계층(작성/분류용 Sonnet 5 vs 수정/진단·평가용 Opus 4.8)으로 단순화하는 편이
  운영·검증 복잡도를 낮춘다.

## 결정
*모델 계층을 2계층으로 단순화*. Haiku 전면 폐지. `shared/llm.py` 의 `MODELS: dict[str, ModelSpec]`
가 유일한 매핑 소스 — alias→(model_id, 설명) 단일 dict, `_ALIAS_MODEL`/`_DEFAULT_MODEL_ID` 는
이 dict 에서 import 시점에 자동 파생(더 이상 별도 리터럴 map 유지 안 함).

| 업무 성격 | 모델 | 비고 |
|----------|------|------|
| 글 작성·감수·라우팅·비상 폴백 | `claude-sonnet-5` | 옛 `writer_fast`/`writer_audit`/`router_main`/`fallback` (Haiku·Sonnet 4.6) 통합 |
| 코드 수정·진단·자가학습 평가·헌법 정제·사실성/매력도 게이트 | `claude-opus-4-8` | 옛 `coder`/`guardian`/`diagnostic`/`learn_eval`/`audit_refine`/`fact_judge`/`engagement_judge` (Opus 4.6) 승격 |

모든 호출은 여전히 `shared.llm.invoke_text(alias, ...)` 단일 함수 경유 — 직접 모델명 박는
행위 금지 (ADR 002 원칙 유지).

## 이유
1. **세대 일원화**: 시스템 전역이 항상 최신 GA 세대(현재 Sonnet 5 / Opus 4.8) 두 개만
   참조 — 신규 모델 세대 전환 시 `shared/llm.py` `MODELS` dict 한 곳만 갱신.
2. **혼란 제거**: 옛 세대 모델 ID·"Haiku" 문구가 코드·주석·문서에 잔존하면 다음 작업자가
   "지금도 쓰는 모델인가?" 재확인해야 함 — 완전 삭제로 이 비용을 0으로.
3. **비용/정확도 재트레이드오프 불필요**: Sonnet 5 자체가 이전 Haiku 대비 글쓰기 비용 대비
   품질이 개선되어, 별도 초저가 계층 유지 실익이 ADR 002 도입 시점보다 작아짐.
4. **단일 매핑 진입점 강화**: alias 정의를 `MODELS` dict 하나로 좁혀 `_ALIAS_MODEL`/
   `_DEFAULT_MODEL_ID` 이중 관리 위험 제거.

## 포기한 대안
1. **Haiku 계층 유지 + ID만 최신화**: "완전 삭제해야 혼란이 없다"는 사용자 판단과 충돌.
   저비용 계층의 실익 대비 유지보수 비용(3계층 동기화)이 더 크다고 판단해 포기.
2. **점진적 마이그레이션 (신규 코드만 신모델, 기존 코드는 그대로)**: 옛 모델 ID가 혼재하면
   "이 파일은 아직도 구세대를 쓰나?" 라는 매 코드리뷰 확인 비용 발생 — 전면 일괄 전환 채택.
3. **ERRORS.md 등 과거 기록까지 삭제**: 역사적 사고 기록·ADR 배경/결정/포기한 대안 서술은
   재발 방지 학습 자산이자 저장소 자체 컨벤션(ADR README 변경 정책)상 보존 대상 —
   `JARVIS07_GUARDIAN/ERRORS.md` 및 각 ADR 의 배경·결정·포기한 대안 섹션은 그대로 둠.

## 결과
- `shared/llm.py` `MODELS` dict 2-alias 그룹(Sonnet 5 / Opus 4.8) 단일 소스화 + `_ALIAS_MODEL`/
  `_DEFAULT_MODEL_ID` 자동 파생.
- `shared/precommit_check.py` `check_model()` 의 `valid_ids` = `{"claude-sonnet-5", "claude-opus-4-8"}`,
  `"model/haiku"` 위반 카테고리 추가 — Haiku 문자열 재유입 즉시 차단.
- 코드 6개 파일(`JARVIS07_GUARDIAN/auto_repair.py`·`architecture.py`, `shared/claude_sdk_compat.py`,
  `JARVIS01_MASTER/agent_tools.py`, `JARVIS03_RADAR/daily_review.py` 등) + 문서 8개 파일
  (루트 `CLAUDE.md`, `JARVIS02_WRITER/CLAUDE_WRITER.md`, `JARVIS00_INFRA/ARCHITECT_DESIGN.md`,
  `README.md`, ADR 005/002/README 등) 의 옛 모델 ID·Haiku 문구 전수 교체 (2026-07-04).
- `docs/decisions/002-model-layering.md` 상태를 `폐기 — 대체됨 by 015` 로 갱신, 배경·결정·이유·
  포기한 대안 서술은 역사 그대로 보존.
- `JARVIS07_GUARDIAN/ERRORS.md` 의 Haiku 관련 과거 인시던트 기록은 변경하지 않음(역사 보존).

## 변경 정책
모델 alias 추가·교체는 *반드시* 본 ADR 갱신 + `shared/llm.py` `MODELS` 수정. 신규 세대 모델
도입 시 이 ADR 을 "번복" 절차(README.md 변경 정책)로 대체 — 새 ADR 016 작성 + 본 ADR 상태를
`폐기 (날짜) — 대체됨 by 016-...`  로 갱신.
