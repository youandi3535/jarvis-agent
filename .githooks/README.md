# JARVIS Git Hooks

CLAUDE.md 박제 규정 자동 검증 — `shared/precommit_check.py` 의 27종 grep 명령을 commit 시점에 자동 차단.

## 설치 (1회)

```bash
git config core.hooksPath .githooks
```

## 작동 모드

| 모드 | 환경변수 | 동작 |
|------|----------|------|
| **경고** (기본) | 미설정 | 위반 발견 시 stderr 출력, commit 통과 |
| **차단** | `JARVIS_STRICT=1` | 위반 발견 시 exit 1, commit 거부 |

## 권장 도입 순서

1. **1주차** — `core.hooksPath` 설정만. 경고 모드로 누적 위반 가시화.
2. **2주차** — 잔존 위반 0건 도달 후 `export JARVIS_STRICT=1` 박제.
3. **3주차 이후** — `JARVIS_STRICT=1` 영구화 (`.zshrc` 또는 `.bashrc`).

## 우회

긴급 hotfix 등에서 검증 우회가 필요한 경우:

```bash
git commit --no-verify -m "..."
```

단, 우회 사실은 ERRORS.md 에 박제할 것.

## 검증 카테고리

```bash
python3 shared/precommit_check.py --list
```

- `infra` — 인프라 단일 진입점 (3종)
- `length` — 분량 표기 단일 진입점 (5종)
- `blog` — 블로그 헌법 위반 (1종)
- `schedule` — 스케줄 단일 진입점 (7종)
- `autocode` — 자율 코드 자가수정 (3종)
- `tools` — 자율 에이전트 도구 (3종)
- `image` — 이미지 생성 단일 진입점 (2종)

특정 카테고리만 검증:

```bash
python3 shared/precommit_check.py --category schedule --category infra
```

## 관련 정책

- 검증 명령 원본: `CLAUDE.md` 각 단일 진입점 섹션
- 학습 시스템 연동: `JARVIS07_GUARDIAN/auto_repair.py` 7-Layer 자가 진단이 매일 08:30/18:00 동일 검증 수행
