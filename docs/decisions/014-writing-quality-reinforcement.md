# ADR 014 — 글 품질 강화학습 폐쇄 루프 (Writing-Quality Reinforcement Loop)

- **상태**: 적용 (2026-07-03)
- **결정자**: 사용자 박제 2026-07-03 ("블로그 작성이 되면 이것도 자동 학습이 되어서
  다음번엔 더 글을 잘 쓸 수 있도록 계속 개선되도록. 오류 자동 캐치 수정도 강화학습이
  되어야 하고, 글 품질에 대한 부분도 *별도로* 강화학습이 되어야 한다.")
- **관련**: ADR 005 (오류 2-Tier 학습 — 대칭 구조), ADR 012 (3-패스 작성), ERRORS [166]

## 배경 — 종전 상태는 "누적"이지 "강화"가 아니었다

| 구성요소 | 종전 (있었음) | 갭 (없었음) |
|----------|--------------|------------|
| 인사이트 생산 | daily_review 22:00 (LLM 분석) + auto_approve (승인 제안 승격) | — |
| 저장 | `learning_insights` (weight·occurrences·시간감쇠) | 보상 누적 컬럼 |
| 소비 | 작성 3곳 주입 (jarvis_main·economic_poster·trend_economic_writer) | **어떤 인사이트가 어느 글에 들어갔는지 기록 없음** |
| 피드백 | 시간 감쇠 + occurrences++ | **글 결과(성과)가 weight 에 전혀 반영 안 됨** |

즉 지침이 *쌓이기만* 하고, 실제로 글을 좋게 만들었는지 *검증·도태* 가 없었다.
무효·유해 지침도 재발견만 되면 영원히 주입되는 구조.

## 결정 — 주입→관측→보상→갱신 폐쇄 루프 (오류 Bandit 과 대칭)

```
[작성 시점]  quality_learner.build_insights_block(scope, theme)
   ├─ UCB 랭킹: effective_weight + 0.35·√(ln(1+총사용)/(1+개별사용))
   │            → 신규 지침도 탐색 기회 확보 (exploration)
   └─ insight_usage 에 배치 기록 (보상 귀속 대기)
        │  (발행 → post_quality_analyzer 분석 → post_analysis.suggestions)
[매일 23:45] j07_quality_learn (DEFAULT_JOBS)
   ├─ 사용 기록 ↔ 분석 글 매칭 (scope=post_type · platform · 18h 창)
   ├─ 보상 = 1 − Σ(제안 페널티: high .25 / medium .12 / low .05)  ∈ [0,1]
   ├─ weight ← clamp(0.05, 3.0, weight + 0.3·(보상 − 0.5))   [EMA]
   └─ 저성과 가속 감쇠 (검증 5회+ & 평균보상 < 0.35 → weight ×0.5)
        │
[다음 글]   갱신된 weight 로 재선택 — 검증된 지침만 생존
```

## 단일 진입점

| 책임 | 위치 |
|------|------|
| 엔진 (선택·보상·갱신·잡) | `JARVIS07_GUARDIAN/quality_learner.py` ★ 단독 |
| SQL 헬퍼 | `shared/db.py` (`insight_usage` 테이블 + `get_ranked_learning_insights` / `record_insight_usage` / `get_unrewarded_usage` / `apply_insight_reward`) |
| 스케줄 | `JARVIS04_SCHEDULER/job_registry.py` `j07_quality_learn` 23:45 |
| 소비 (3곳 — 위임만) | `jarvis_main.py` · `economic_poster.py` · `trend_economic_writer._load_learn_insights` → `build_insights_block()` 호출 1줄 |

생산 측 (daily_review·auto_approve·decay) 은 **변경 없음** — 후보 공급원 그대로.
`post_quality_analyzer._build_learning_block` (분석기 컨텍스트용) 도 그대로.

## 원칙

1. **LLM 호출 0** — 순수 통계. 발행 경로 지연·비용 0.
2. **실패 시 항상 "" 반환** — 학습 계층 장애가 글 작성을 절대 막지 않음.
3. **배치 공유 보상** — 같은 글에 주입된 지침은 같은 보상 (v1 단순화; 글이 쌓이면
   배치 조합이 달라져 개별 기여도가 자연 분리됨 — bandit 표준 가정).
4. **삭제는 기존 경로 재사용** — weight<0.05 삭제는 `decay_learning_insights` 가 계속 담당.

## 포기한 대안

- **인사이트별 A/B 홀드아웃**: 하루 2글 리듬에서 통계적 검정력 부족 — UCB 로 대체.
- **조회수 기반 보상 (v1)**: 조회수는 주제·시점 교란이 커서 지침 귀속이 부정확.
  v2 후보로 보류 (naver_rank·judge_engagement 점수 합성).
- **LLM 재채점 보상**: 비용·rate-limit (ERRORS [288]) — 결정론 보상으로 충분.

## 검증 (2026-07-03 스크래치 DB e2e)

- 좋은 글 (low 1건, 보상 0.95): weight 1.2→1.335 ↑ ✅
- 나쁜 글 (high3+med2, 보상 0.01): weight 1.335→1.188 ↓ ✅
- 2회차 주입 블록에 `검증 보상 0.95` 태그 표기 ✅
- `job_quality_learn()` 예외 없이 완료 ✅
