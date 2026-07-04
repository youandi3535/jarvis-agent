# 016. 밴딧 arm = 유한 전략 — 오류지문 arm 폐기 + 오염 게이트 + 차원 상한

## 상태
확정 (사용자 박제 2026-07-04). [ADR 005](005-three-tier-learning.md) 의 *Tier 1 Contextual Bandit* 구성요소를 정밀 보완 — ADR 005 는 유효(대체 아님), 본 ADR 은 그 안의 밴딧 *arm 설계·오염 차단·차원 정책* 을 규정한다.

## 배경
JARVIS07 GUARDIAN 의 Tier 1 강화학습(Linear UCB Contextual Bandit, `bandit.py`)이 운영 축적 후 세 병리가 동시 발현:

- **파일 비대 402MB** — `bandit_state.json` 이 매 보상마다 통째 로드(≈8초)·재저장. arm 하나당 3~4MB(404×404 밀집행렬 × `indent=2` JSON). 저사양 Mac 에서 학습 1회 수십초, 계속 증식.
- **죽은 신호** — 89개 arm 전부 mean_reward≈0(-0.005). 좋은/나쁜 fixer 구분 불가. 원인: 적응형 사다리가 차원 v50=404D 까지 폭주했는데 arm당 관측은 1~25건 → ridge prior 가 신호를 압도(θ≈0). *설계 주석이 스스로 경고한 콜드스타트 파국*(관측≪차원)에 그대로 빠짐.
- **오염** — 89 arm 중 83개가 변경추적(GitCommit 31·ExternalEdit·PolicyChange…). 이들은 `_FIXER_REGISTRY` 에 없어 `_fix_from_learned` 가 절대 재적용 못 하는 *죽은 arm*. learned_patterns 126개 중 119개도 재적용 불가한 변경추적 이력(stored_patch 0개).

근본 원인: `bandit_arm_name()` 이 arm 이름을 *오류 지문(error_type::message)* 으로 만들고, `_get_verified_fixers`/`_get_new_fixers` 가 학습 패턴 하나하나를 밴딧 후보 arm 으로 펼침 → 오류·커밋마다 arm 신규 생성 → 무한 증식. 컨텍스추얼 밴딧의 전제(*소수 arm* + context 로 상황 구분)가 정면으로 뒤집힘. (당시 `record_sdk_fix` 주석엔 "밴딧 비대화"를 *목표* 로 적어둔 오개념까지 있었다.)

## 결정
**밴딧 arm 은 소수의 고정된 *fixer 전략* 이다 — 오류 지문이 아니다.**

1. `bandit._arm_key()` — 모든 입력 fixer 이름을 유한 전략 공간으로 접는 단일 초크포인트: 정적 6종 + `auto_patch`(그대로) / `verified:*`→`learned_verified` / `new:*`→`learned_new` / `llm_patch`→`llm` / 미상→`None`(arm 생성 안 함). arm ≈ 8개 상한, 호출자가 무엇을 넘겨도 밴딧이 스스로 방어.
2. `pattern_fixer.record_pattern_hit` 노이즈 게이트 4 — actionable(`_ACTIONABLE_FIXERS`) fixer 만 학습 등록. 변경추적·정책 이벤트(GitCommit·ExternalEdit·PolicyChange…)를 learned_patterns·밴딧에서 영구 차단. (error_log `status='manual'` 변경추적 기록은 유지 — 작업량 카드 불변.)
3. `try_pattern_fix` 재구성 — 학습 패턴을 개별 arm 으로 펼치지 않고 `_fix_from_learned` 단일 조회(정확→정규식→시맨틱 매칭) + 정적 6종만 밴딧 랭킹.
4. 차원 상한 `_MAX_PROJ=8` → 최대 28D(v3). arm 별 실관측 `n`·보상합 `rsum` 도입(정직 통계 — ‖A-λI‖_fro 허수 폐기). compact 저장(indent 제거·6자리 반올림).
5. 상태 초기화(402MB→45B) + learned_patterns 오염 프루닝(126→7, 백업 `JARVIS07_GUARDIAN/_refactor_backup/`).

## 이유
- 컨텍스추얼 밴딧은 arm 이 소수여야 arm당 관측이 차원을 감당한다. arm=지문이면 arm당 데이터가 항상 1~2건 → 학습 불가능. arm=전략이면 각 전략이 수백 관측을 축적 → 실제 분리 학습(good/bad fixer 구분).
- 변경추적은 *재발 개념이 없는 이력* 이라 강화학습·재적용 대상이 아님(ADR 005 취지 및 `error_collector._MANUAL_POLICY_TYPES` 와 정합). actionable 게이트 하나로 learned_patterns·밴딧 오염을 동시 차단.
- 차원은 데이터가 감당할 만큼만. arm 이 유한해진 지금 28D 면 충분하고, 404D 는 신호를 죽인다.

## 포기한 대안
- **per-pattern arm 유지 + 압축/상한만**: 근본(arm=지문)을 안 고치면 압축해도 신호는 여전히 죽어있고 arm 은 계속 증식. 기각.
- **밴딧 폐지 + 정적 순서 고정**: 학습 자체를 버림 — "스스로 똑똑해지는 에이전트" 비전에 역행. 기각.
- **raw 임베딩 384D 통짜 교체**: 이미 겪은 콜드스타트 파국의 재현. 기각.
- **기존 402MB 상태 마이그레이션 보존**: θ≈0 무신호라 보존 가치 0. 요약(arm 이름)만 백업하고 초기화.

## 결과
- **파일**: `JARVIS07_GUARDIAN/bandit.py`(전면 재구현 — `_arm_key`·n/rsum·28D 상한·compact), `JARVIS07_GUARDIAN/pattern_fixer.py`(노이즈 게이트 4·`try_pattern_fix` 재구성·`_get_verified/_get_new_fixers` deprecated 배너).
- **상태**: `bandit_state.json` 402MB→45B, `learned_patterns.json` 126→7(actionable 만). 백업 `JARVIS07_GUARDIAN/_refactor_backup/`.
- **검증**: 격리 시뮬레이션 8종(보상 분리·arm≤8·오류지문 미진입·파일<50KB) + 오염 게이트 5종 + end-to-end 스모크 + `precommit_check` 44종 0건.
- **CLAUDE.md**: 루트 "결정 사유·근거" 표에 ADR 016 행 추가. ADR 005 는 유효(본 ADR 이 그 Tier 1 밴딧 구성요소를 정밀화).
- **★ 회귀 금지**: `_get_verified_fixers`/`_get_new_fixers` 를 다시 밴딧 후보로 되돌리지 말 것 — 오염 재발. (코드 deprecated 배너 + 본 ADR 이 단일 근거.) 새 fixer 는 정적 6종처럼 *이름 자체* 를 arm 으로 등록.
