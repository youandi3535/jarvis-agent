# 005. 3-tier 자가 학습 — 학습 캐시 → 정적 패턴 → LLM 폴백

## 상태
확정 (2026-05-13 박제, JARVIS07_GUARDIAN 업그레이드)

## 배경
JARVIS07 GUARDIAN 의 자동 수정 시스템 도입 초기에는 *모든 오류 → LLM 진단* 단일 경로였다. 운영 후:
- 동일 오류 (예: `NameError: name 'naver_post' is not defined` 같은 오타) 가 *매번 재발* → 매번 LLM 호출 → 비용 누적
- 패턴이 명확한 오류 (상대 import / NoneType slicing 등) 도 LLM 추론 거침 → 시간 낭비
- LLM 응답 시간 (Sonnet 4.6 ~10초) 가 데몬 실시간 처리에 부적합

*같은 오류를 반복 학습하지 못한다* — 이게 자가 학습 시스템의 근본 결함이었다.

## 결정
오류 자동 수정을 *3-tier 계층 구조* 로 재설계. 같은 fingerprint 재발 시 LLM 호출 0.

### Tier 1 — 학습 캐시 (learned_patterns.json)
- `error_collector.report()` → fingerprint 계산 (`error_type::normalized_message`)
- `learned_patterns.json` 에 hit 매칭 시 *즉시 fix* 적용 (LLM 호출 0)
- hit_count 자동 증가 → 시간 갈수록 LLM 절약 비율 상승

### Tier 2 — 정적 패턴 (pattern_fixer.py)
- 캐시 miss 시 5종 정적 fixer 시도:
  1. 상대 import → 절대 import
  2. NoneType slicing → 안전 가드
  3. NameError 오타 → fuzzy 매칭 자동 수정
  4. NoneType attribute → 안전 가드
  5. ImportError → 유사 심볼 대안 제시
- 성공 시 *learned_patterns 에 자동 등록* → 다음 회차부터 Tier 1 에서 처리

### Tier 3 — LLM 폴백 (error_analyzer.py)
- Tier 1·2 모두 실패 시 Sonnet 4.6 진단
- 성공 시 *learned_patterns 에 자동 등록* → 다음 회차부터 Tier 1 처리

## 이유
1. **재발 자산화**: 모든 수정 사례가 *영구 학습 자산* 으로 누적 → 시간 갈수록 시스템이 똑똑해짐.
2. **비용 비선형 감소**: Tier 1 hit 비율이 30% → 60% → 90% 로 진화하면서 *LLM 호출 횟수* 가 비선형 감소.
3. **응답 시간**: Tier 1 hit 은 ms 단위 처리. 데몬 실시간 처리 적합.
4. **노이즈 게이트**: `record_pattern_hit()` 안에 3종 노이즈 게이트 (fixer 없음 / message 빈 채로 / 정책 작업 타입) — 잘못된 학습 자산 누적 방지.
5. **메타 학습**: 5회+ 반복 error_type → 새 `_fix_*()` 자동 제안 (Tier 2 확장). Tier 3 → Tier 2 자동 승급.

## 포기한 대안
1. **단일 LLM 진단**: 매번 LLM. 비용·시간 미감당. 포기.
2. **정적 패턴만**: 새 유형 오류 대응 불가. 포기.
3. **사용자 수동 등록**: 자동화 의미 상실. 포기.
4. **벡터 임베딩 매칭** (의미 유사 검색): 임베딩 DB 운영 부담 + fingerprint 매칭이 정확도·성능 모두 우세. 포기.
5. **모든 fix 즉시 적용** (검증 없이): 잘못된 fix 가 학습 자산화되면 *연쇄 오류*. 학습 등록 전 검증 필요 → 이게 ADR 007 의 Eval Agent 분리 동기.

## 결과
- `JARVIS07_GUARDIAN/error_analyzer.py` — 3-tier 분기
- `JARVIS07_GUARDIAN/pattern_fixer.py` 938줄 — 정적 fixer 5종 + learned_patterns 관리
- `JARVIS07_GUARDIAN/learned_patterns.json` 72KB — 영구 학습 자산
- `JARVIS07_GUARDIAN/severity.py` — 자동 수정 가능 판정
- `hub.py` 대시보드 *학습 곡선* 카드 — `total_patterns` / `total_hits` / `llm_saved` 시계열
- `shared/db.py` `self_repair_runs` 테이블 — 회차별 메트릭 영구 박제

## 외부 변경 박제 3-layer (ADR 005 보강)
학습 자산이 *시스템 내부 수정* 만으로 누적되면 사용자·외부 도구 수정이 학습 누락. 3-layer 보강:

| Layer | 트리거 | 함수 |
|-------|--------|------|
| A | auto_repair Claude Code SDK 성공 시 즉시 | `_record_repairs_to_guardian()` |
| B | git daily 회고 (D-1 박제) 03:30 | `j07_git_audit` 잡 |
| C | Cowork Claude 코드 수정 시 의무 호출 | `report_manual_fix()` |

3-layer 가 모두 `record_pattern_hit()` 또는 `record_external_change()` 로 학습 자산화 → 어떤 경로의 변경도 누락 없이 캡처.

## 변경 정책
- 새 fixer 추가: `pattern_fixer.py` `_fix_<name>(error_record)` 함수 + `_PATTERN_FIXERS` + `_FIXER_REGISTRY` 갱신 + `severity._PATTERN_FIXABLE_TYPES` 갱신 + 가상 traceback 단위 테스트.
- learned_patterns 노이즈 게이트 변경은 *반드시* 본 ADR 갱신.
