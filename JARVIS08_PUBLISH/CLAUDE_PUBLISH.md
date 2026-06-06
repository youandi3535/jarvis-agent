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
