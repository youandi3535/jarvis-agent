# JARVIS08_PUBLISH — 발행 도메인 단일 진입점

ADR 008 Phase 2~6 완료 (사용자 박제 2026-05-17) — ADR 008 *완전 종료*. precommit 8 카테고리 ZERO 위반.

## 비직관 규칙
- **단일 진입점 강제** — 발행 함수 본체·카테고리 상수·쿠키 갱신 코드 본체는 *이 폴더 안* 에서만 정의.
- 호출자는 항상 `from JARVIS08_PUBLISH.{platforms,category,credentials} import ...`.
- precommit_check 의 `domain/publish` + `domain/category` 카테고리가 owner 외부 정의 자동 차단.

## 서브 구조
| 폴더 | 책임 |
|------|------|
| `platforms/` | 플랫폼별 발행자 (`post_to_naver`/`post_to_tistory`) |
| `category/`  | 카테고리 상수 (`ECONOMIC_CATEGORY`) + 검색 로직 |
| `credentials/` | 네이버·티스토리 쿠키 refresher |
| `tags.py` ★ | **발행 태그 생성·검증 단일 진입점** (2026-07-29 신설) |
| `publish_ledger.py` ★ | **발행 완결성 감사** — 슬롯 창 기준 결손 판정 (2026-07-29 신설) |

### `tags.py` — 비직관 규칙
- **4조합 전부 `generate_tags()` 하나를 지난다.** 경제는 `tags=` 미전달 → 발행자
  `_generate_smart_tags` shim 경유, 테마는 `trend_theme_writer` 가 `seed_tags=[테마,섹터]` 로 직접 호출.
  ★ 종전엔 발행자 2벌 + 테마 writer 의 고정 템플릿 `[theme, sector, '테마주','주식','투자']`
  까지 **3벌**이라, 2026-07-29 07:00 경제 태그 사고를 고쳐도 테마엔 닿지 않았다.
- **검증은 어휘가 아니라 *꼴*** — 거부 표현 목록을 박으면 새 거부문이 그대로 통과하고
  모델이 바뀌면 낡는다. 쉼표·줄바꿈 둘 다 구분자로 보고 조각 길이·비율로 판정한다.
- **길이·개수는 `length_manager` 파생** (`TAG_MAX`·`NAVER_HASHTAG_MIN`). 여기 박지 말 것.

### `publish_ledger.py` — 비직관 규칙
- **판정 단위는 '날짜' 가 아니라 '슬롯 창'** (이번 발행 시각 ~ 다음 발행 시각).
  달력 날짜로 하면 자정 넘긴 테마가 다음날 실적으로 계상돼 *연속 장애일수록 탐지가 꺼진다*.
- **감사 시각·기대집합·오류타입 전부 파생** — 시각은 `misfire_grace + 플랫폼수 ×
  BLOG_ACTION_DEADLINE_SEC`, 글종류는 `job_llm_priority.publish_post_type()`,
  플랫폼은 `platforms/` 의 `post_to_*` AST. 리터럴을 추가하지 말 것.
- **발행 락이 잡혀 있으면 '결손' 이 아니라 '지연'** — 아직 돌고 있는 것을 실패라 부르지 않는다.

## Backward compat shim
옛 위치 (`JARVIS02_WRITER/{naver,tistory}_poster.py` 등) 는 *본체 삭제 후* import shim 만 남김:
```python
# JARVIS02_WRITER/naver_poster.py
import sys as _sys
from JARVIS08_PUBLISH.platforms import naver_poster as _new_module
_sys.modules[__name__] = _new_module
```
**핵심 패턴** — `sys.modules[__name__] = _new_module` 로 옛 모듈 객체를 새 모듈로 교체.
외부 setattr (`tp.TS_COOKIE = ...`), attribute 접근, `from JARVIS02_WRITER.tistory_poster import ...` 모두 동일하게 새 모듈에서 처리.

## 경로 anchor 주의
새 위치의 발행자 모듈은 `JARVIS02_WRITER/` 의 chrome_profile·cookies 등 *물리적 자원* 을 참조해야 함.
이를 위해 `_PROJECT_ROOT`/`_LEGACY_BASE_DIR` anchor 를 명시:
```python
_PROJECT_ROOT    = Path(__file__).resolve().parent.parent.parent  # → root
_LEGACY_BASE_DIR = _PROJECT_ROOT / "JARVIS02_WRITER"               # 옛 위치 anchor
COOKIE_FILE      = _LEGACY_BASE_DIR / "naver_cookies.pkl"          # 옛 위치 보존
```

## 학습 시스템 — 도메인 분류 (Phase 4)
publish/category/credentials 도메인 사고 발생 시 `JARVIS07_GUARDIAN/learned_patterns.json` 의
`domain` 필드로 자동 분류. ADR 008 의 *피드백 루프* 가시성 확보.

도메인 skew 임계값: **25개** (한 도메인에 패턴 25+ 누적 시 근본 리팩터 검토 트리거).
