# 008. Domain Ownership Matrix — 도메인 단일 진입점 강제

## 상태
**완료 (2026-05-17)** — Phase 0~6 모든 단계 통과. precommit 8 카테고리 ZERO 위반 (infra·length·blog·schedule·autocode·tools·image·domain). 16곳 분산 → 7 단일 진입점.

## 배경

ADR 001 (단일 진입점 원칙) 박제 후 *코드는 분산 그대로*. 박제와 코드 정합성 불일치가 누적 — 사용자 박제 원칙 2 (박제 후 *실제 grep 확인*) 미준수.

### 사용자 진단 (2026-05-17)

> "지금 문제가 이미지만의 문제가 아니야. ... 사공이 많으면 배가 산으로 간다. ... 문제 하나 해결에 코드 파일 몇 개를 수정해야 한다는 거야. 이거 너무 비효율 아니냐고. 이건 업무 분담이 잘못되어 있다는 얘기야."

[008-A 인벤토리](008-A-domain-inventory.md) 결과:
- 이미지 사고 1건 → **7곳** 점검 필요
- 카테고리 사고 → **5곳**
- 발행 흐름 사고 → **4곳**

증상별 안전망 추가가 *코드 더 분산* 시켰음. 근본 원인 미해결.

## 결정

**Domain Ownership Matrix** — 각 도메인의 *물리적 단일 진입점* 폴더를 강제. owner 외 위치에 해당 도메인 코드 추가 시 *pre-commit 단계 자동 차단*.

### 매트릭스

| 도메인 | Owner 폴더 | 책임 범위 | 호출자 권한 |
|--------|----------|---------|----------|
| 이미지 | `JARVIS06_IMAGE/` | 생성·검증·dedupe·삽입·재사용·정리·업로드 | `from JARVIS06_IMAGE import ...` 만 |
| 발행 (플랫폼·카테고리·쿠키) | `JARVIS08_PUBLISH/` (신설) | 네이버·티스토리 발행 추상화 + 카테고리 검색 + 쿠키 갱신 | `from JARVIS08_PUBLISH import publish` |
| 분량 | `JARVIS02_WRITER/length_manager.py` ✅ | 글자·문장·단락 상수 + 헬퍼 | `from .length_manager import _L` |
| 발행 헌법 | `JARVIS02_WRITER/BLOG_SUPREME_LAW.md` + `law_enforcer.py` | 정책 본문 + 집행 함수 (이미지 함수 제외) | `from .law_enforcer import enforce_supreme_law` |
| 스케줄 | `JARVIS04_SCHEDULER/` ✅ | 모든 cron·interval·polling | `DEFAULT_JOBS` dict 추가만 |
| 도구 등록 | `shared/tools.py` + `JARVIS01_MASTER/agent_tools.py` ✅ | 라우터 도구 카탈로그 | `@register_tool` 데코레이터 |
| 오류·학습 | `JARVIS07_GUARDIAN/` ✅ | 오류 수집·자가 진단·학습·평가·감사 | `from JARVIS07_GUARDIAN.error_collector import report` |
| 인프라 | `JARVIS00_INFRA/` ✅ | 데몬·프로세스 제어 | `from JARVIS00_INFRA.infra_agent import ...` |

✅ = 현재 단일 진입점 유지 중 (정상)
신설/이관 필요 = JARVIS06_IMAGE 보강 + JARVIS08_PUBLISH 신설

### 단일 진입점 강제 메커니즘

`shared/precommit_check.py` 에 신규 카테고리 `domain_diffusion` 추가. 각 도메인별 *금지 패턴* 박제 → owner 외 폴더에서 해당 패턴 발견 시 *commit 차단*.

예시 (이미지 도메인):
```python
DOMAIN_OWNERSHIP = {
    "image": {
        "owner_dirs": ["JARVIS06_IMAGE/"],
        "forbidden_patterns": [
            (r"^def\s+(_dedupe_|_validate_image|_is_heading_img|assemble_blocks|enforce_image|enforce_paragraph_pair_image)",
             "이미지 함수 정의 — JARVIS06_IMAGE 외부 금지"),
            (r"matplotlib\.pyplot|from\s+PIL", "이미지 라이브러리 직접 사용 — JARVIS06_IMAGE 외부 금지"),
        ],
    },
}
```

## 이유

1. **사고 추적 가능**: 사고 발생 시 owner 1곳만 점검. *7곳 추적* 의 비효율 제거.
2. **회귀 위험 0**: 1곳만 수정 → 다른 폴더 영향 없음.
3. **학습 효율**: learned_patterns 가 *도메인 카테고리* 단위 누적 가능 → 같은 종류 사고 재발 자동 차단.
4. **박제와 코드 정합성**: precommit_check 가 *분산 자체* 검출 → 박제만 하고 실제 분산 잔존하는 사고 차단.
5. **신규 작업자 학습 용이**: "이미지 작업은 JARVIS06 만 보면 끝".

## 포기한 대안

1. **점진적 분산 허용 + 검증 강화**: 분산은 유지하면서 *검증 룰* 만 늘림. *근본 미해결*. 채택 ❌.
2. **거대 단일 파일 (god module)**: 모든 책임 하나의 파일에. 가독성·확장성 모두 저해. ❌.
3. **모놀리식 → 마이크로서비스**: 운영 부담 폭증, JARVIS 규모 부적합. ❌.

## Phase 마스터 plan

### Phase 0 — 안전망 (즉시, 1~2일)
- **0-1**: `precommit_check.py` 에 `domain_diffusion` 카테고리 신설 — 분산 코드 추가 *자체* 차단
- **0-2**: `JARVIS07_GUARDIAN/error_collector.py` 에 `report_user_observed_incident()` API 신설 — 사용자 발견 사고 즉시 학습 데이터화
- **0-3**: 본 ADR 008 + 008-A 박제 (이 단계)

### Phase 1 — 이미지 도메인 통합 (3~5일)
- **1-1**: `JARVIS06_IMAGE/` 하위 구조 정리 (`validators/`, `injectors/`, `cleaners/`)
- **1-2**: `law_enforcer.py` 의 이미지 함수 6개 → `JARVIS06_IMAGE/validators/` + `injectors/`
- **1-3**: `tistory_html_writer.assemble_blocks` → `JARVIS06_IMAGE/injectors/block_assembler.py`
- **1-4**: `economic_poster._cleanup_economic_images` → `JARVIS06_IMAGE/cleaners/`
- **1-5**: `_LEGACY_*_UNUSED` 함수 완전 삭제
- **1-6**: 호출자 12곳 import 경로 변경
- **1-7**: 회귀 단위 테스트 + Phase 0 검증 통과

### Phase 2 — 발행·카테고리·쿠키 통합 (5~7일)
- **2-1**: `JARVIS08_PUBLISH/` 신설 — `platforms/` + `category/` + `credentials/`
- **2-2**: `naver_poster.py` + `tistory_poster.py` → `JARVIS08_PUBLISH/platforms/`
- **2-3**: 카테고리 검색 로직 → `JARVIS08_PUBLISH/category/category_resolver.py`
- **2-4**: `ECONOMIC_CATEGORY` / `WP_CAT_ID` / `WP_CATEGORY_ID` *단일 변수* 통합
- **2-5**: 쿠키 refresher 2개 → `JARVIS08_PUBLISH/credentials/`
- **2-6**: 발행 플랫폼 추상화 → `JARVIS08_PUBLISH/platforms/`
- **2-7**: 호출자 import 경로 변경 + 회귀

### Phase 3 — 분량·헌법 잔존 분산 정리 (1~2일)
- **3-1**: `precommit_check` length 잔존 4건 해결
- **3-2**: `tistory_html_writer` / `law_enforcer` 의 *자연어 헌법 인용* 패턴 → BLOG_SUPREME_LAW 동적 로드로 대체

### Phase 4 — 학습 시스템 카테고리화 (3~5일)
- **4-1**: `learned_patterns.json` 의 entry 에 `domain` 필드 추가 (image/category/length/...)
- **4-2**: `JARVIS07_GUARDIAN/auditor.py` 가 도메인 단위 분석 추가
- **4-3**: `hub.py` 대시보드 — 도메인별 학습 곡선 카드
- **4-4**: `auto_repair` Layer 4 가 도메인 카테고리 단위 자동 검증 규칙 신설

### Phase 5 — JARVIS02_WRITER 슬림화 (3일)
- **5-1**: `JARVIS02_WRITER/` 안에 *콘텐츠 생성* 만 남기고 이미지·발행·검증 위임
- **5-2**: 작성자 함수 시그니처 정리 — 외부 의존 최소화

### Phase 6 — 회귀 검증 + 최종 박제 (1~2일)
- **6-1**: 전체 발행 흐름 end-to-end 단위 테스트
- **6-2**: precommit_check 27→N종 검증 통과
- **6-3**: ERRORS.md 본 ADR 008 사고 박제
- **6-4**: 메모리·CLAUDE.md 최종 동기

## 결과

- 본 ADR + 008-A 인벤토리
- `shared/precommit_check.py` 의 `domain_diffusion` 카테고리 (Phase 0)
- `JARVIS07_GUARDIAN/error_collector.report_user_observed_incident()` (Phase 0)
- Phase 1~6 완료 시 사고 점검 *1곳*

## 변경 정책

각 Phase 진행 시:
1. *이관 전* — 인벤토리 재확인 (분산 누락 없는지)
2. *이관 중* — 본체 이동 + 호출자 import 경로 변경 + grep 으로 잔재 0 확인
3. *이관 후* — precommit_check 의 domain_diffusion 카테고리 새 owner 반영 + 단위 테스트 + JARVIS07 박제

Phase 순서 *불변* — 의존 관계 (Phase 2 의 category 가 Phase 1 의 이미지 이관 후 수월).

## 영구 원칙

"한 사고 = 한 폴더 수정" 원칙. 새 사고 발생 시 *2개 이상 폴더 수정 필요* 하면 *그 자체가 분산 시그널* → 본 ADR 매트릭스 재검토 트리거.
