# 002. 모델 다층 분리 — Haiku / Sonnet 4.6 / Opus 4.6

## 상태
확정 (2026-05-14 박제, 메모리 `project_model_status.md`)

## 배경
JARVIS 의 LLM 호출은 *글 작성·코드 수정·아키텍처 진단* 세 가지 성격이 매우 다르다. 단일 모델로 처리할 경우:
- 상위 모델 (Opus·Sonnet 4.6) 만 사용 → 글 작성 비용 폭증 (블로그 한 편당 토큰 수만)
- 하위 모델 (Haiku) 만 사용 → 코드 수정·진단의 정확도 부족 → 자가 수정 실패율 상승
- 단일 모델 + 컨텍스트 프롬프트 보강 → 컨텍스트 비용으로 가격 절감 효과 상쇄

비용·정확도·응답 시간 트레이드오프를 단일 모델로 풀 수 없다.

## 결정
*업무 성격별로 모델을 다층 분리*. `shared/llm.py` 의 MODELS alias 8개로 단일 매핑 진입점 구축.

| 업무 성격 | 모델 | alias |
|----------|------|-------|
| 글 작성 (블로그 본문·메타) | Haiku 4.5 | `writer_fast` |
| 글 감수 (auditor·polish) | Haiku 4.5 | `writer_audit` |
| 코드 수정 (auto_repair) | Opus 4.6 | `coder`·`guardian` |
| 코드 진단 (auto_repair 3단계) | Opus 4.6 | `diagnostic` |
| 자가 학습 평가 (eval_agent — A모델 후) | Opus 4.6 | `learn_eval` |
| 헌법 정제 (auditor — A모델 후) | Opus 4.6 | `audit_refine` |
| 라우팅 분류 (router — ReAct) | Sonnet 4.6 | `router_main` |
| 비상 폴백 | Haiku 4.5 | `fallback` |

모든 호출은 `shared.llm.invoke_text(alias, ...)` 단일 함수 경유 — 직접 모델명 박는 행위 금지.

## 이유
1. **비용 최적화**: 블로그 발행 (하루 평균 5~7건) 의 토큰 80% 이상이 Haiku 로 처리 → 월 비용 대폭 절감.
2. **정확도 보장**: 코드 수정·진단은 Opus 4.6 (★ 사용자 박제 — 수정은 무엇이든 최고 성능 모델) → 자동 수정 성공률 (learned_patterns hit 누적과 함께 상승).
3. **아키텍처 의사결정 품질**: Opus 4.6 은 *헌법 정제·메타 학습 (5회+ 반복 → 새 _fix_*() 자동 제안)* 같은 *추론 깊이가 필요한 작업* 에만 사용 — 사용 빈도는 낮지만 정확도 결정적.
4. **단일 매핑 진입점**: alias 변경 시 한 곳만 수정 → 모델 교체·신규 모델 도입 즉시 전체 적용.

## 포기한 대안
1. **단일 Sonnet 4.6**: 정확도는 충분하나 글 작성 비용 미감당. 포기.
2. **단일 Haiku**: 코드 수정·진단 정확도 부족 — auto_repair 7-Layer 진단의 메타 학습 (5회+ 반복 패턴 → 새 fixer 자동 신설) 같은 작업은 Haiku 로 불가. 포기.
3. **모델 자동 선택 (자가 라우팅)**: LLM 이 *자기에게 적합한 모델을 자기가 결정* — 가격·정확도 우선순위 사용자가 명시 못 함. 포기.
4. **사용자 명시 모델 (호출자가 매번 지정)**: 호출자가 alias 외우는 부담. 사용 빈도·정확도 보장 어려움. shared/llm.py 단일 매핑이 더 안전. 포기.

## 결과
- `shared/llm.py` MODELS 8 alias 매핑 박제.
- CLAUDE.md `Claude 모델 사용 정책` 섹션 (직접 모델명 박는 행위 금지).
- 메모리 `project_model_status.md` 와 매일 동기화.
- 자가 학습 엔진의 *LLM 호출 절약* (learned_patterns hit → LLM 호출 0) 이 비용 추가 절감 — `hub.py` 대시보드 학습 곡선에 시각화.

## 변경 정책
모델 alias 추가·교체는 *반드시* 본 ADR 갱신 + `shared/llm.py` MODELS 수정 + 메모리 동기화. 직접 호출자 측에서 모델명 박는 행위는 검증 명령으로 차단 (precommit_check 미래 카테고리).
