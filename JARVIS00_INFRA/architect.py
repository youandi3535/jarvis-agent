"""JARVIS00_INFRA/architect.py — 에이전트 설계 기획서 산출 모듈.

★ 핵심 원칙 — 설계·기획만. 실행은 절대 안 함. 출력은 *기획서 마크다운 단일 파일*.
실제 코드 수정·잡 등록은 기존 `create_plan` 도구 통해서만 → 인라인 버튼 ✅ 거쳐서.

5 핵심 책임:
1. 의도 파악 — 사용자 자유 문장 → 정형 스펙
2. 설계 산출 — 15단계 × 15소단계 YES/NO 롤백 완전 설계도 마크다운
3. 일관성 검증 — CLAUDE.md 검증 명령 5종 시뮬레이션
4. 대안 제시 — agent / skill / tool / job / unnecessary 판단
5. 계획 위임 — create_plan 인자 형태 반환

5 박제 의무:
1. Knowledge base 동적 로드 — 매 호출마다 새로. 캐시 0.
2. 출력 표준화 — SPEC_TEMPLATE 15단계×15소단계 강제.
3. Anti-pattern 자동 경고 — _verify_against_rules 5종.
4. 범위 제한 — v1 = scope="agent" 만.
5. 자기참조 안전망 — scope="meta" 재귀 깊이 1 제한.

상세 설계: ARCHITECT_DESIGN.md
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")

# 루트 sys.path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ══════════════════════════════════════════════════════════════
# 표준 양식 (12 섹션) — LLM prompt 박제
# ══════════════════════════════════════════════════════════════

_EXEC_PLAN_SYSTEM = """\
당신은 Python 에이전트 스켈레톤 코드 생성기입니다.
입력으로 구현 계획 §12 텍스트를 받아, 신규 파일 write_file 스텝 목록을 JSON 으로만 출력합니다.

[출력 형식 — 오직 JSON 배열만, 다른 텍스트 금지]
[
  {
    "tool": "write_file",
    "args": {
      "path": "JARVISNN_NAME/filename.py",
      "content": "코드 내용 (\\n 이스케이프 필수)"
    },
    "note": "1줄 설명"
  },
  ...
  {
    "tool": "run_bash",
    "args": {"command": ".venv/bin/python -c \\"import JARVISNN_NAME\\""},
    "note": "import 검증"
  }
]

[엄격한 규칙]
1. write_file 만 사용. 기존 파일 수정(edit_file) 금지.
2. content 는 *최대 20줄* 짧은 스켈레톤만. 실제 구현 로직 금지 (TODO 주석 또는 raise NotImplementedError).
3. 경로는 jarvis-agent/ 루트 기준 상대경로.
4. JSON \\n 이스케이프 필수 (raw 줄바꿈 금지).
5. 마지막 스텝은 반드시 run_bash import 검증: {"command": ".venv/bin/python -c \"import JARVISNN_NAME\""} (cd 사용 금지, cwd=jarvis-agent 고정).
6. 마크다운 코드블록(```) 절대 금지 — raw JSON 배열만.
"""

SPEC_TEMPLATE = """# JARVIS{NN}_{NAME} — 완전 설계 기획서 (15단계 × 15소단계)

> 생성일: {날짜} | 에이전트 ID: jarvis{nn}_{name_lower}
> 판정: {agent|skill|tool|job|unnecessary} | 의도 1줄: {의도 요약}

---
## ★ 읽는 법 (모든 단계 공통 원칙)
- ✅ YES → 명시된 다음 소단계로 즉시 진행
- ❌ NO → 명시된 롤백 동작 수행 후 복귀 소단계로 이동
- 🔄 ROLLBACK → 해당 소단계에서 생성된 파일·DB·상태를 원복 후 원인을 ERRORS.md 에 기록
- ⛔ ABORT → 이 기획서 무효. 사용자 재질의 필요. 진행 중 생성 파일 전부 삭제.
---

## Stage 1: 의도 & 요구사항 확정
> 전제조건: 없음 (최초 진입점)
> 완료 기준: 핵심 동작·범위·금지사항·성공 지표 4종 모두 확정
> 롤백 전략: 사용자 재질의 → Stage 1 전체 재시작

### 1.1 사용자 원문 파악
- 작업: 자유 문장에서 핵심 동작(What)·이유(Why)·범위(Scope)·제약(Constraint) 추출
- ✅ YES → 1.2
- ❌ NO (모호·상충) → 보완 질의 3개 생성 → ⛔ ABORT
- 검증: 핵심 동작 1개 이상 추출 가능한가?

### 1.2 기존 에이전트 역할 중복 확인
- 작업: AGENTS.md + capability declares 전수 비교
- 검증: `grep -rn "<핵심역할>" JARVIS*/*_agent.py | grep -v __pycache__` → 0건
- ✅ YES (중복 없음) → 1.3
- ❌ NO (중복 존재) → 기존 에이전트 확장 권고 기록 → ⛔ ABORT

### 1.3 신설 필요성 최종 판단 (agent / skill / tool / job)
- 작업: 스케줄 필요 여부·외부 의존성·side_effect·LLM 호출 여부 4축 평가
- ✅ YES (agent 필요) → 1.4
- ❌ NO (skill/tool/job으로 충분) → 대안 형태 기록 후 Stage 15 이동 (간소 완료)
- 검증: 4축 중 2개 이상 agent 조건 충족?

### 1.4 에이전트 번호 결정
- 작업: `ls -d JARVIS*/` 출력 후 다음 빈 NN 번호 확정
- 검증: `ls -d JARVIS{NN}_*/ 2>/dev/null | wc -l` → 0 (비어 있어야 함)
- ✅ YES (번호 미사용) → 1.5
- ❌ NO (충돌) → 다음 번호로 증가 후 1.4 재검증

### 1.5 에이전트 이름 확정 (영문 대문자 단어)
- 작업: 역할을 1-2 단어 영문으로 명명. 예: COLLECTOR, VISION, GUARDIAN
- 검증: `grep "jarvis{nn}_{name_lower}" AGENTS.md | wc -l` → 0
- ✅ YES → 1.6
- ❌ NO (이름 충돌) → 이름 변경 후 1.5 재실행

### 1.6 성공 지표(Success Metric) 정의
- 작업: "이 에이전트가 완성되면 무엇이 달라지는가" — 측정 가능한 지표 3개 이상
- ✅ YES (지표 확정) → 1.7
- ❌ NO (지표 불명확) → 사용자에게 기대 효과 재질의 → ⛔ ABORT

### 1.7 단일 책임 원칙 확인
- 작업: 이 에이전트가 하나의 도메인만 책임지는지 확인 (ADR 008 매트릭스 참조)
- ✅ YES (단일 도메인) → 1.8
- ❌ NO (2개 이상 도메인) → 에이전트 분리 권고 → 각각 별도 기획서 → ⛔ ABORT

### 1.8 외부 의존성 목록 확정
- 작업: API·CLI·라이브러리·크롤링 대상·인증 정보 전수 열거
- ✅ YES → 1.9
- ❌ NO (의존성 불명확) → 기술 사전 검증 필요 항목 목록화 후 1.8 재실행

### 1.9 데이터 흐름 방향 정의 (IN/OUT)
- 작업: 입력 데이터 소스와 출력 데이터 목적지를 화살표 다이어그램으로 명시
- ✅ YES → 1.10
- ❌ NO → 소스·목적지 재확인 후 1.9 재실행

### 1.10 shared/bus.py 이벤트 의존 확인
- 작업: 구독(subscribe) 또는 발행(publish) 해야 할 EventType 목록 확정
- 검증: `grep "EventType\\." shared/bus.py | grep -v __pycache__` 로 가용 이벤트 확인
- ✅ YES → 1.11
- ❌ NO (신규 이벤트 필요) → Stage 5 에서 신규 EventType 설계 후 반영

### 1.11 shared/db.py 테이블 의존 확인
- 작업: 읽기·쓰기가 필요한 DB 테이블 목록 확정. 신규 테이블 필요 여부 판단.
- ✅ YES (기존 테이블로 충분) → 1.12
- ❌ NO (신규 테이블 필요) → Stage 5 에서 스키마 설계 필수 마킹 후 1.11 계속

### 1.12 발행 본문 한국어 포함 여부 확인
- 작업: 이 에이전트가 블로그·메시지 등 사용자에게 보이는 한국어 텍스트를 생성하는가?
- ✅ YES (한국어 생성) → LLM 호출 의무 플래그 ON → 1.13
- ❌ NO → 1.13

### 1.13 승인 게이트 필요 여부 확인
- 작업: side_effect="external" 도구 존재 시 requires_approval=True 강제 확인
- ✅ YES (게이트 설계 계획됨) → 1.14
- ❌ NO (external인데 게이트 미계획) → Stage 7에서 강제 설계 마킹 후 1.13 재확인

### 1.14 ERRORS.md 헛다리 사전 점검 (최근 30건)
- 작업: ERRORS.md 최근 30건 중 이 에이전트 영역과 겹치는 교훈 항목 추출
- ✅ YES (관련 교훈 없음 또는 mitigation 명시) → 1.15
- ❌ NO (관련 교훈 있고 mitigation 미계획) → 해당 교훈을 각 Stage 에 반영 후 재점검

### 1.15 Stage 1 완료 게이트
- 작업: 1.1~1.14 모두 YES 상태인지 체크리스트 확인
- 검증: 미완 소단계 수 = 0
- ✅ YES → Stage 2 진입
- ❌ NO (미완 소단계 존재) → 해당 소단계로 🔄 ROLLBACK

---

## Stage 2: 도메인 소유권 & 단일 진입점 설계
> 전제조건: Stage 1 완료
> 완료 기준: 도메인 매트릭스 갱신 계획 + 단일 진입점 파일 확정
> 롤백 전략: 도메인 중복 발견 시 → ADR 008 재검토 후 Stage 1.3 재판단

### 2.1 ADR 008 도메인 매트릭스 현황 확인
- 작업: CLAUDE.md 도메인 소유권 매트릭스 전수 조회 — 이 에이전트가 담당할 도메인 빈 칸 확인
- ✅ YES (빈 도메인 있음) → 2.2
- ❌ NO (이미 담당자 있음) → 담당 에이전트 확장으로 전환 권고 → ⛔ ABORT

### 2.2 Owner 폴더 = JARVIS{NN}_{NAME}/ 단일 진입점 확정
- 작업: 모든 도메인 로직이 이 폴더 안에만 존재하는 설계인지 확인
- ✅ YES → 2.3
- ❌ NO (로직 분산 설계) → 분산 이유 검토 후 단일화 재설계

### 2.3 ~ 2.15 [에이전트별 도메인 소유권 세부 설계 15개 소단계]
- 작업: [각 소단계 에이전트 특성에 맞게 구체화]
- ✅ YES → 다음 소단계
- ❌ NO → 🔄 ROLLBACK → 해당 소단계 재실행

---

## Stage 3: 에이전트 폴더 & 파일 구조 설계
> 전제조건: Stage 2 완료
> 완료 기준: 폴더·파일 목록 확정 + 각 파일 역할 정의
> 롤백 전략: 파일 생성 실패 시 생성된 파일 삭제 후 Stage 3 재진입

### 3.1 ~ 3.15 [파일 구조 설계 15개 소단계]
- 작업: {name}_agent.py / 핵심 로직 파일 / 유틸리티 / 테스트 파일 설계
- ✅ YES → 다음 소단계
- ❌ NO → 🔄 ROLLBACK → 해당 파일 삭제 후 재설계

---

## Stage 4: register() & declare() 등록 구조 설계
> 전제조건: Stage 3 완료
> 완료 기준: register(scheduler, bus) + declare(agent_id, status_fn, help_section) 시그니처 확정
> 롤백 전략: import 실패 시 → 의존 모듈 확인 후 Stage 4 재실행

### 4.1 ~ 4.15 [에이전트 등록 구조 15개 소단계]
- 작업: declare() 파라미터 / register() 내부 흐름 / EventType 구독 목록 설계
- ✅ YES → 다음 소단계
- ❌ NO → 🔄 ROLLBACK → 의존 에이전트 확인 후 재설계

---

## Stage 5: 데이터 흐름 & 인터페이스 설계
> 전제조건: Stage 4 완료
> 완료 기준: IN/OUT 데이터 스키마 + DB 테이블 스키마 + 이벤트 페이로드 확정
> 롤백 전략: 스키마 충돌 시 → 기존 테이블 호환 방안 재설계

### 5.1 ~ 5.15 [데이터 인터페이스 15개 소단계]
- 작업: 입력 스키마 / 출력 스키마 / DB 테이블 DDL / EventType 페이로드 설계
- ✅ YES → 다음 소단계
- ❌ NO → 🔄 ROLLBACK → 스키마 재검토

---

## Stage 6: 핵심 로직 & 알고리즘 설계
> 전제조건: Stage 5 완료
> 완료 기준: 핵심 함수 시그니처·처리 흐름·예외 경로 전부 확정
> 롤백 전략: 로직 불완전 시 → 해당 함수 재설계 후 Stage 6 재실행

### 6.1 ~ 6.15 [핵심 로직 15개 소단계]
- 작업: 주요 함수 설계 / 처리 순서 / 예외 처리 / LLM 호출 위치 설계
- ✅ YES → 다음 소단계
- ❌ NO → 🔄 ROLLBACK → 해당 함수 재설계

---

## Stage 7: 도구 카탈로그 & 승인 게이트 설계
> 전제조건: Stage 6 완료
> 완료 기준: 모든 도구 ToolMeta 완전 정의 (side_effect·requires_approval 빠짐없음)
> 롤백 전략: external 도구 누락 발견 시 → 해당 도구 ToolMeta 즉시 수정

### 7.1 ~ 7.15 [도구 카탈로그 15개 소단계]
- 작업: 도구별 side_effect 분류 / requires_approval 판정 / rollback 가능 여부 설계
- ✅ YES → 다음 소단계
- ❌ NO (external + approval=False) → 즉시 approval=True 로 수정 → 🔄 ROLLBACK 후 재확인

---

## Stage 8: 인텐트 & 라우터 연결 설계
> 전제조건: Stage 7 완료
> 완료 기준: SAFE_INTENTS·APPROVAL_INTENTS·ROUTER_SYSTEM_PROMPT 3곳 동시 갱신 계획 확정
> 롤백 전략: intent 누락 시 → ERRORS [29] 패턴 회피 체크 후 재설계

### 8.1 ~ 8.15 [인텐트 라우터 연결 15개 소단계]
- 작업: intent 이름 확정 / dispatchers.py SAFE/APPROVAL 분류 / ROUTER_SYSTEM_PROMPT 추가분 설계
- ✅ YES → 다음 소단계
- ❌ NO (3곳 미동시 설계) → 🔄 ROLLBACK → 누락 위치 보완 후 재확인

---

## Stage 9: 스케줄 & JARVIS04 잡 설계
> 전제조건: Stage 8 완료
> 완료 기준: 모든 시간 기반 실행이 DEFAULT_JOBS dict 형태로 확정
> 롤백 전략: 잡 ID 충돌 시 → 기존 잡 목록 재확인 후 ID 변경

### 9.1 ~ 9.15 [스케줄 잡 설계 15개 소단계]
- 작업: 잡 ID / trigger / callback 경로 / misfire 정책 / owner 에이전트 설계
- ✅ YES → 다음 소단계
- ❌ NO (schedule.every 또는 while True 패턴 발견) → DEFAULT_JOBS 형태로 재설계 후 재확인

---

## Stage 10: 오류 감지 & GUARDIAN 연동 설계
> 전제조건: Stage 9 완료
> 완료 기준: 모든 try/except 블록에 report() 호출 계획 확정
> 롤백 전략: 누락된 오류 경로 발견 시 → 해당 함수 예외 처리 보완

### 10.1 ~ 10.15 [오류·GUARDIAN 연동 15개 소단계]
- 작업: 오류 분류 / report() 호출 위치 / GUARDIAN 자동 수정 대상 판단 / TG 알림 레벨 설계
- ✅ YES → 다음 소단계
- ❌ NO (try/except pass 패턴 존재) → 해당 블록 report() 추가 계획 삽입 후 재확인

---

## Stage 11: 보안 & 파일 안전 경계 설계
> 전제조건: Stage 10 완료
> 완료 기준: 모든 외부 입력에 검증 + 파일 경로 안전 박스 확인
> 롤백 전략: 경계 미설계 발견 시 → _safe_path() 적용 계획 추가

### 11.1 ~ 11.15 [보안·경계 설계 15개 소단계]
- 작업: 입력 검증 위치 / 파일 경로 안전 박스 / subprocess env PATH 보강 / 인증 정보 처리
- ✅ YES → 다음 소단계
- ❌ NO (os.environ 직접 참조 또는 hardcoded 경로) → 즉시 안전 설계로 교체

---

## Stage 12: 하네스(Harness) 연동 & Layer 설계
> 전제조건: Stage 11 완료
> 완료 기준: 5 Layer 게이트 구조 적용 범위·순서 확정
> 롤백 전략: Layer 우회 코드 발견 시 → harness.send() 콜백 통과 형태로 재설계

### 12.1 ~ 12.15 [하네스 연동 15개 소단계]
- 작업: Layer0(preflight) / Layer1(precondition) / Layer2(steps) / Layer3(verify_loop) / Layer4(send) 설계
- ✅ YES → 다음 소단계
- ❌ NO (직접 외부 호출 설계) → harness Layer 4 send() 콜백으로 이관 후 재확인

---

## Stage 13: CLAUDE.md 규정 & 검증 명령 설계
> 전제조건: Stage 12 완료
> 완료 기준: 비직관 규칙 목록 + grep 검증 명령 5종 이상 확정
> 롤백 전략: 검증 명령 실행 실패 시 → grep 패턴 수정 후 재실행

### 13.1 ~ 13.15 [CLAUDE.md & 검증 명령 15개 소단계]
- 작업: 단일 진입점 규칙 / 금지 패턴 / 검증 grep 명령 설계 / precommit_check 연동
- ✅ YES → 다음 소단계
- ❌ NO (검증 명령 미작성) → 해당 도메인 규칙별 grep 명령 추가 후 재확인

---

## Stage 14: 구현 계획 (create_plan 인자 형태)
> 전제조건: Stage 13 완료
> 완료 기준: write_file·edit_file·run_bash 단계 순서·경로·목적 전부 확정
> 롤백 전략: 파일 write 실패 시 → .bak 복원 + 해당 단계 재실행

### 14.1 ~ 14.15 [구현 계획 15개 소단계]
- 작업: 신규 파일 목록 / 기존 파일 수정 목록 / 실행 순서 / 검증 명령 / AGENTS.md 갱신
- ✅ YES → 다음 소단계
- ❌ NO (순서 오류 또는 경로 누락) → 해당 단계 재설계 후 재확인

### 14.N 구현 단계 목록 (순서대로)
1. write_file: JARVIS{NN}_{NAME}/{name}_agent.py — 에이전트 진입점 스켈레톤
2. write_file: JARVIS{NN}_{NAME}/[핵심로직파일].py — 핵심 로직
3. edit_file: JARVIS04_SCHEDULER/job_registry.py — DEFAULT_JOBS 항목 추가
4. edit_file: JARVIS01_MASTER/dispatchers.py — SAFE/APPROVAL_INTENTS 추가
5. edit_file: JARVIS01_MASTER/intents.py — ROUTER_SYSTEM_PROMPT 추가
6. edit_file: AGENTS.md — 에이전트 등록 행 추가
7. run_bash: python shared/agent_registration_check.py — 등록 검증
8. run_bash: python3 shared/precommit_check.py — 전체 규정 검증
...

---

## Stage 15: 완료 검증 & 데몬 재시작 계획
> 전제조건: Stage 14 완료 (모든 파일 생성·수정 완료)
> 완료 기준: 4종 검증 전부 통과 + 데몬 재시작 안내 준비
> 롤백 전략: 검증 실패 시 → 실패 검증 항목 → 해당 Stage로 🔄 ROLLBACK

### 15.1 agent_registration_check.py 통과
- 검증: `python shared/agent_registration_check.py` → 0건 오류
- ✅ YES → 15.2
- ❌ NO → 누락 항목 확인 → Stage 4 🔄 ROLLBACK

### 15.2 precommit_check.py 전체 통과
- 검증: `python3 shared/precommit_check.py` → 전 카테고리 0건 위반
- ✅ YES → 15.3
- ❌ NO → 위반 카테고리 확인 → 해당 Stage 🔄 ROLLBACK

### 15.3 import 검증
- 검증: `.venv/bin/python -c "import JARVIS{NN}_{NAME}"` → 오류 없음
- ✅ YES → 15.4
- ❌ NO → ImportError 원인 추적 → Stage 3·4 🔄 ROLLBACK

### 15.4 AGENTS.md 등록 행 존재 확인
- 검증: `grep "jarvis{nn}_{name_lower}" AGENTS.md | wc -l` → 1 이상
- ✅ YES → 15.5
- ❌ NO → AGENTS.md 갱신 후 재확인

### 15.5 ~ 15.14 [에이전트별 기능 동작 검증 10개 소단계]
- 작업: 핵심 함수 단위 테스트 / 이벤트 수신 테스트 / 스케줄 잡 등록 확인 / TG 상태 노출 확인
- ✅ YES → 다음 소단계
- ❌ NO → 실패 항목 추적 → 해당 Stage 🔄 ROLLBACK

### 15.15 데몬 재시작 안내 & 완료 선언
- 작업: 모든 검증 통과 → 텔레그램으로 "재시작 필요" 안내 메시지 전송
- 안내 내용: `pkill -f jarvis_daemon.py && python jarvis_daemon.py`
- ✅ YES → 🎉 기획서 완료. docs/architect/{날짜}_{slug}.md 저장.
- ❌ NO (검증 미통과 잔존) → 미통과 항목 목록 → 해당 Stage 🔄 ROLLBACK

---

## 부록 A: 일관성 검증 결과 (자동 삽입)
| 검증 항목 | 결과 |
|-----------|------|
| 스케줄 단일 진입점 | ✅ / ⚠️ <설명> |
| 승인 게이트 | ✅ / ⚠️ |
| 한국어 하드코딩 | ✅ / ⚠️ |
| 인프라 단일 진입점 | ✅ / ⚠️ |
| 3 곳 동시 갱신 | ✅ / ⚠️ |

## 부록 B: ERRORS.md 헛다리 위험 평가
| 오류 번호 | 위험도 | 비고 |
|-----------|--------|------|
"""


_SPEC_SYSTEM_PROMPT = """\
당신은 자비스 에이전트 시스템 설계 전문가입니다. 사용자의 자유 문장 의도를 받아,
*기존 시스템 일관성을 완벽히 유지*하는 완전 설계 기획서를 마크다운으로 산출합니다.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[구조 절대 원칙 — 위반 시 출력 전체 무효]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. **15단계 × 15소단계 강제**: Stage 1 ~ Stage 15 를 반드시 출력. 각 Stage 는 N.1 ~ N.15 소단계를 모두 포함.
   - Stage 1·2·7·8·9·14·15 는 Template 의 상세 내용 유지 (수정 가능하지만 소단계 수 = 15 불변).
   - Stage 3·4·5·6·10·11·12·13 은 에이전트 특성에 맞게 소단계 15개를 창작해서 채움.
   - "3.1 ~ 3.15 [에이전트별 ...]" 같은 요약 표현 절대 금지 — 모든 소단계를 개별 작성.

2. **YES/NO 결정 구조 강제**: 모든 소단계(N.M)에 반드시 아래 형식 포함:
   ```
   ### N.M [소단계명]
   - 작업: [구체적 수행 내용 1-2줄]
   - 검증: `[실행 가능한 shell 명령 또는 확인 기준]`
   - ✅ YES → [다음 소단계 번호 또는 "다음 Stage 진입"]
   - ❌ NO → [롤백 동작 설명] → [복귀 소단계 번호 또는 ⛔ ABORT]
   ```

3. **롤백 경로 완전성**: 모든 ❌ NO 경로는 반드시:
   - 롤백 동작(삭제·원복·재설계 등) 명시
   - 복귀할 소단계 번호 명시 (순환 없이 이전 단계로만)
   - 복귀 불가 시 ⛔ ABORT 명시 + 이유 1줄

4. **검증 명령 실행 가능성**: 모든 `검증:` 항목은 shell 에서 실제 실행 가능한 명령이어야 함.
   - 추상적 설명("확인한다") 금지. `grep`·`wc -l`·`python -c`·`ls` 형태 사용.

5. **빈 소단계 금지**: "TBD", "미정", "에이전트별 구체화", "N.X ~ N.15 동일 패턴" 금지.
   모든 225개 소단계를 에이전트 특성에 맞게 개별 작성.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[내용 절대 원칙]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A. ERRORS.md [27]~[32] 헛다리 패턴 재현 위험 → 각 해당 Stage 소단계 검증 항목에 반영.
B. 발행 본문 한국어 문장 존재 시 → Stage 6 에서 "LLM 호출 의무" 소단계 필수.
C. external side_effect 도구 → Stage 7 에서 requires_approval=True 검증 소단계 필수.
D. 새 cron 잡 → Stage 9 에서 DEFAULT_JOBS dict 형태로만 설계. schedule.every·while-True 금지.
E. 새 intent → Stage 8 에서 dispatchers.py SAFE/APPROVAL + ROUTER_SYSTEM_PROMPT 3곳 동시 갱신 소단계 필수 (ERRORS [29]).
F. subprocess 사용 시 → Stage 11 에서 env PATH prepend 소단계 필수 (ERRORS [32]).
G. Stage 14.N 구현 단계 목록은 실제 파일 경로·목적을 상세히 기재. 추상 표현 금지.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[현재 시스템 컨텍스트]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{ctx_block}

[사용자 의도]
{user_intent}

[scope]
{scope}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
위 원칙과 SPEC_TEMPLATE 양식에 따라 15단계 × 15소단계 완전 설계 기획서를 마크다운으로 산출하세요.
다른 텍스트·코드블록 래핑 금지 — 마크다운 본문만.

★ 간결성 강제: 각 소단계(N.M)는 아래 형식으로 정확히 4줄 이내. 넘지 말 것.
```
### N.M 소단계명
- 작업: [1줄]
- 검증: `[명령]`
- ✅ YES → N.(M+1) | ❌ NO → [롤백 1줄] → N.(M-1)
```
4줄 초과 금지. 완전성 > 분량. 8000토큰 이내 완성.
"""


_PARSE_SYSTEM_PROMPT = """\
사용자 자유 문장에서 정형 스펙을 JSON 으로 추출하세요. 다른 텍스트 금지.

스키마:
{
  "name": "<NAME 후보 — 영문 대문자 단어 1-2개>",
  "role": "<역할 1줄 한국어>",
  "side_effect": "none|internal|external",
  "external_deps": ["<API/CLI/라이브러리 이름>", ...],
  "schedule_required": true|false,
  "schedule_hint": "<있으면 cron 표현 또는 자연어>",
  "is_publishing_body": true|false,
  "is_publishing_body_reason": "<발행 본문에 한국어 문장 포함 여부>",
  "data_deps": ["<shared/db.py 테이블 또는 shared/bus.py 이벤트>", ...],
  "verdict_hint": "agent|skill|tool|job|unnecessary",
  "verdict_reason": "<왜 이 형태가 적절한가>"
}
"""


# ══════════════════════════════════════════════════════════════
# Knowledge base 동적 로드 — 캐시 금지
# ══════════════════════════════════════════════════════════════

def _read_text(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return default


def _tail_errors_md(path: Path, n_entries: int = 30) -> str:
    """ERRORS.md 의 최근 N 항목 추출. `### [번호]` 헤더 기준."""
    txt = _read_text(path)
    if not txt:
        return ""
    # `### [번호]` 패턴으로 분할 — 가장 최근 30개 (역순)
    parts = re.split(r"(?=^### \[\d+\])", txt, flags=re.MULTILINE)
    parts = [p.strip() for p in parts if p.strip().startswith("### [")]
    parts.reverse()  # 최신 우선
    return "\n\n".join(parts[:n_entries])


def _scan_capability_declares(root: Path) -> list[dict]:
    """모든 *_agent.py / core_agent.py 의 declare(...) 블록 메타 추출."""
    results = []
    try:
        from shared import capabilities as _caps
        for c in _caps.all_capabilities():
            results.append({
                "agent_id": c.agent_id,
                "domain": c.domain,
                "intents": list(c.intents),
                "tools": list(c.tools),
                "requires_approval": list(c.requires_approval),
                "description": c.description,
            })
    except Exception as e:
        log.warning(f"⚠️ architect: capability scan 실패: {e}")
        _g_report("infra", e, module=__name__)
    return results


def _list_tools_meta() -> list[dict]:
    """shared.tools._TOOLS 메타 요약."""
    try:
        from shared.tools import _TOOLS
        return [
            {
                "name": m.name,
                "domain": m.domain,
                "side_effect": m.side_effect,
                "requires_approval": m.requires_approval,
                "description": (m.description or "")[:120],
            }
            for m in _TOOLS.values()
        ]
    except Exception as e:
        log.warning(f"⚠️ architect: tools scan 실패: {e}")
        _g_report("infra", e, module=__name__)
        return []


def _summarize_default_jobs() -> list[dict]:
    """JARVIS04_SCHEDULER.job_registry.DEFAULT_JOBS 요약."""
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        return [
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "trigger": str(j.get("trigger") or ""),
                "owner": j.get("owner_agent") or j.get("owner") or "",
            }
            for j in DEFAULT_JOBS
        ]
    except Exception as e:
        log.warning(f"⚠️ architect: DEFAULT_JOBS scan 실패: {e}")
        _g_report("infra", e, module=__name__)
        return []


def _list_agent_folders(root: Path) -> list[str]:
    """JARVIS{NN}_NAME 폴더명 리스트 (점진 번호 결정용)."""
    return sorted([
        d.name for d in root.iterdir()
        if d.is_dir() and re.match(r"^JARVIS\d{2}_", d.name)
    ])


def _load_context() -> dict:
    """매 호출마다 새로 로드. 캐시 0 — 코드 변경 즉시 반영 의무."""
    return {
        "claude_md": _read_text(_ROOT / "CLAUDE.md"),
        "claude_writer": _read_text(_ROOT / "JARVIS02_WRITER" / "CLAUDE_WRITER.md"),
        "claude_radar": _read_text(_ROOT / "JARVIS03_RADAR" / "CLAUDE_RADAR.md"),
        "claude_infra": _read_text(_ROOT / "JARVIS00_INFRA" / "CLAUDE_INFRA.md"),
        "errors_recent": _tail_errors_md(_ROOT / "JARVIS07_GUARDIAN" / "ERRORS.md", n_entries=30),
        "agent_declares": _scan_capability_declares(_ROOT),
        "tools_catalog": _list_tools_meta(),
        "default_jobs": _summarize_default_jobs(),
        "existing_agents": _list_agent_folders(_ROOT),
    }


def _build_ctx_block(ctx: dict) -> str:
    """LLM prompt 안에 박을 컨텍스트 블록 — 길이 제한 있어 핵심만 추림."""
    lines = []

    # 기존 에이전트 목록
    lines.append("### 기존 에이전트 폴더")
    for f in ctx.get("existing_agents", []):
        lines.append(f"- {f}")

    # capability declares (intents 포함)
    lines.append("\n### 등록된 capability")
    for c in ctx.get("agent_declares", []):
        intents = ", ".join((c.get("intents") or [])[:8])
        lines.append(f"- {c['agent_id']} (domain={c['domain']}): intents=[{intents}]")

    # 도구 카탈로그 요약
    lines.append("\n### 등록된 도구 카탈로그")
    tools = ctx.get("tools_catalog", [])
    safe = [t for t in tools if not t["requires_approval"]]
    appr = [t for t in tools if t["requires_approval"]]
    lines.append(f"- SAFE ({len(safe)}): " + ", ".join(t["name"] for t in safe))
    lines.append(f"- APPROVAL ({len(appr)}): " + ", ".join(t["name"] for t in appr))

    # DEFAULT_JOBS 요약
    lines.append("\n### JARVIS04 DEFAULT_JOBS")
    jobs = ctx.get("default_jobs", [])
    lines.append(f"- 총 {len(jobs)}개. 새 잡은 *반드시* DEFAULT_JOBS 에 추가.")

    # CLAUDE.md 강제 규정 요약 (앞 8000자)
    cm = ctx.get("claude_md", "")
    if cm:
        lines.append("\n### CLAUDE.md (앞부분 요약)")
        lines.append(cm[:6000])

    # ERRORS 최근 항목 (앞 8000자)
    err = ctx.get("errors_recent", "")
    if err:
        lines.append("\n### ERRORS.md 최근 항목 (헛다리·교훈)")
        lines.append(err[:8000])

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# 의도 파싱 (Sonnet)
# ══════════════════════════════════════════════════════════════

def _parse_intent(user_intent: str) -> dict:
    """사용자 자유 문장 → 정형 스펙 dict (JSON)."""
    try:
        from shared.llm import invoke_text
        text = invoke_text(
            "writer_fast",
            f"사용자 의도:\n{user_intent}\n\nJSON 만 출력하세요.",
            system=_PARSE_SYSTEM_PROMPT,
            max_tokens=800,
            temperature=0.1,
        )
        # JSON 추출 (```json ... ``` 또는 raw)
        m = re.search(r"\{[\s\S]*\}", text or "")
        if not m:
            return {"_raw": text or "", "_parse_failed": True}
        return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"⚠️ architect._parse_intent 실패: {e}")
        _g_report("infra", e, module=__name__)
        return {"_parse_failed": True, "_error": str(e)}


# ══════════════════════════════════════════════════════════════
# 기획서 산출 (Sonnet)
# ══════════════════════════════════════════════════════════════

def _generate_spec(user_intent: str, scope: str, ctx: dict, parsed: dict) -> str:
    """12 섹션 마크다운 기획서 산출."""
    ctx_block = _build_ctx_block(ctx)
    parsed_summary = json.dumps(parsed, ensure_ascii=False, indent=2)

    sys_prompt = _SPEC_SYSTEM_PROMPT.format(
        ctx_block=ctx_block,
        user_intent=user_intent,
        scope=scope,
    )
    user_msg = (
        f"파싱된 정형 스펙 (참고용):\n{parsed_summary}\n\n"
        f"양식 (15단계×15소단계 — 모든 소단계를 에이전트 특성에 맞게 채워서 산출):\n{SPEC_TEMPLATE}\n\n"
        "★ 필수: Stage 1~15 각각 소단계 15개 전부 작성. YES/NO 분기·롤백 경로·검증 명령 빠짐없이. "
        "요약·축약·TBD 금지. 마크다운 본문만."
    )
    try:
        from shared.llm import invoke_text
        md = invoke_text(
            "coder",
            user_msg,
            system=sys_prompt,
            timeout=600,
        )
        return md or ""
    except Exception as e:
        log.error(f"❌ architect._generate_spec 실패: {e}")
        _g_report("infra", e, module=__name__)
        return ""


# ══════════════════════════════════════════════════════════════
# 검증 5종 — anti-pattern 정적 점검
# ══════════════════════════════════════════════════════════════

# (검증 항목명, 위반 검출 정규식 또는 콜백, 위반 시 메시지)
_RULE_CHECKS = [
    # 1) 스케줄 단일 진입점
    {
        "name": "스케줄 단일 진입점",
        "patterns": [
            r"BackgroundScheduler\s*\(",
            r"BlockingScheduler\s*\(",
            r"\bschedule\.every\(",
            r"current_hour\s*==",
        ],
        "violation_msg": "JARVIS04_SCHEDULER.DEFAULT_JOBS 외부에서 스케줄러·폴링 패턴 의심. DEFAULT_JOBS 항목으로 등록 권장.",
    },
    # 2) 승인 게이트
    {
        "name": "승인 게이트 (external→approval)",
        "patterns": [
            r'side_effect\s*=\s*["\']external["\'][^)]*requires_approval\s*=\s*False',
        ],
        "violation_msg": "external 도구인데 requires_approval=False — 승인 게이트 우회 위험 (ERRORS [30]).",
    },
    # 3) 한국어 하드코딩 (발행 본문)
    {
        "name": "한국어 하드코딩 (발행 본문)",
        "patterns": [
            r'_FALLBACK_OUTRO\s*=\s*\{',
            r'tip_box\s*=\s*"[가-힣]',
            r'return\s+f?"[가-힣][^"]{60,}\.',
        ],
        "violation_msg": "발행 본문 한국어 단일 고정 문자열 의심 — LLM 호출 또는 변형 풀+시드 권장 (ERRORS [27]).",
    },
    # 4) 인프라 단일 진입점
    {
        "name": "인프라 단일 진입점",
        "patterns": [
            r"^def build_status",
            r"^def _build_status",
        ],
        "violation_msg": "build_status 본체는 JARVIS00_INFRA/infra_agent.py 만 합법. 다른 위치 박지 말 것.",
    },
    # 5) 3 곳 동시 갱신 — spec 의 §4 vs §6 vs §7 정합성 체크 (콜백)
    {
        "name": "3 곳 동시 갱신",
        "callback": "_check_three_place_consistency",
        "violation_msg": "신규 intent 가 §4 에 있는데 §6 dispatchers 매핑 또는 §7 ROUTER_SYSTEM_PROMPT 매핑 누락 — ERRORS [29] 재발 위험.",
    },
]


def _check_three_place_consistency(spec_md: str) -> bool:
    """Stage 8(인텐트) 에서 설계한 intent 가 Stage 8(dispatchers) + Stage 8/13(ROUTER) 에 모두 등장하는지 확인.
    위반이면 True 반환 (검출됨), 정합이면 False.
    """
    # Stage 8 전체에서 intent 패턴 추출 (`<word>.<word>` 형식)
    sec8 = _extract_section(spec_md, 8)
    # 검증 범위: Stage 8 전체 + Stage 13 (CLAUDE.md 검증)
    sec13 = _extract_section(spec_md, 13)

    # 하위 호환: 구형 §4·§6·§7
    sec4 = _extract_section(spec_md, 4) if not sec8 else ""
    sec6 = _extract_section(spec_md, 6) if not sec8 else ""
    sec7 = _extract_section(spec_md, 7) if not sec8 else ""

    search_base = sec8 or sec4
    verify_base = (sec8 + sec13) or (sec6 + sec7)

    if not search_base:
        return False

    intents = set(re.findall(r"\b([a-z_]+\.[a-z_.]+)\b", search_base.lower()))
    if not intents:
        return False

    for it in intents:
        if it not in verify_base.lower():
            return True  # 누락 검출
    return False


def _extract_section(md: str, num: int) -> str:
    """섹션 추출. 신규 `## Stage {num}:` 또는 구형 `## {num}.` 형식 모두 지원."""
    # 신규: ## Stage N: / ## Stage N (공백 허용)
    pat_new = rf"## Stage\s+{num}[\s:][^\n]*\n[\s\S]*?(?=^## Stage\s+\d+|\Z)"
    m = re.search(pat_new, md, flags=re.MULTILINE)
    if m:
        return m.group(0)
    # 구형: ## N. (하위 호환)
    pat_old = rf"## {num}\.\s[\s\S]*?(?=^## \d+\.|\Z)"
    m = re.search(pat_old, md, flags=re.MULTILINE)
    return m.group(0) if m else ""


def _verify_against_rules(spec_md: str) -> list[dict]:
    """5종 정적 점검. 결과 리스트 반환."""
    results = []
    for rule in _RULE_CHECKS:
        violated = False
        if "patterns" in rule:
            for pat in rule["patterns"]:
                if re.search(pat, spec_md, flags=re.MULTILINE):
                    violated = True
                    break
        elif "callback" in rule:
            cb = globals().get(rule["callback"])
            if cb and cb(spec_md):
                violated = True
        results.append({
            "name": rule["name"],
            "ok": not violated,
            "msg": rule["violation_msg"] if violated else "",
        })
    return results


# ══════════════════════════════════════════════════════════════
# ERRORS replay 체크
# ══════════════════════════════════════════════════════════════

_ERRORS_PATTERNS = {
    "[27]": {
        "keywords": ["고정 한국어", "tip_box", "_FALLBACK_OUTRO", "단일 문구", "하드코딩"],
        "domain_hint": "발행 본문",
        "note": "한국어 하드코딩 → 매일 동일 글 → AI작성 판정",
    },
    "[28]": {
        "keywords": ["events.type", "단일 학습 신호", "조회수만 의존"],
        "domain_hint": "학습/feedback",
        "note": "단일 신호 의존 → 외부 API 죽으면 학습 정지",
    },
    "[29]": {
        "keywords": ["dispatchers", "ROUTER_SYSTEM_PROMPT", "SAFE_INTENTS"],
        "domain_hint": "신규 capability",
        "note": "3 곳 동시 갱신 누락 → fallback 라우팅 DEFERRED",
    },
    "[30]": {
        "keywords": ["하드코딩 set", "_approval_tool_names", "auto_approve=True"],
        "domain_hint": "external 도구",
        "note": "승인 게이트 우회 → 무허가 자동 실행",
    },
    "[31]": {
        "keywords": ["functools.wraps", "__signature__"],
        "domain_hint": "함수 wrapper",
        "note": "wrapper 시그니처 누락 → LLM 이 nested kwargs 로 호출",
    },
    "[32]": {
        "keywords": ["subprocess", "PATH", "/opt/homebrew/bin", "env="],
        "domain_hint": "외부 CLI 호출",
        "note": "daemon subprocess PATH 부족 → CLI 실행 실패",
    },
}


def _replay_errors_check(spec_md: str) -> list[dict]:
    """ERRORS [27]~[32] 재현 위험 체크."""
    results = []
    md_lower = spec_md.lower()
    for err_id, meta in _ERRORS_PATTERNS.items():
        # 도메인 힌트가 spec 에 등장하면 위험 영역
        if meta["domain_hint"].lower() not in md_lower:
            results.append({"id": err_id, "risk": "low", "note": "관련 영역 없음"})
            continue
        # 키워드 *부재* 시 mitigation 미명시 → med 위험
        kws_present = sum(1 for kw in meta["keywords"] if kw.lower() in md_lower)
        if kws_present == 0:
            results.append({"id": err_id, "risk": "high", "note": f"{meta['note']} — mitigation 명시 부족"})
        elif kws_present < 2:
            results.append({"id": err_id, "risk": "med", "note": f"{meta['note']} — 일부 mitigation 명시"})
        else:
            results.append({"id": err_id, "risk": "low", "note": f"{meta['note']} — mitigation 충분 명시"})
    return results


# ══════════════════════════════════════════════════════════════
# 대안 판단
# ══════════════════════════════════════════════════════════════

def _check_alternative(parsed: dict) -> str:
    """이게 정말 에이전트여야 하는가 판단. agent / skill / tool / job / unnecessary."""
    if parsed.get("_parse_failed"):
        return "agent"  # 정보 부족 — 보수적 기본
    hint = (parsed.get("verdict_hint") or "").lower()
    if hint in ("agent", "skill", "tool", "job", "unnecessary"):
        return hint
    # 휴리스틱
    if not parsed.get("schedule_required") and parsed.get("side_effect") == "none":
        if not parsed.get("external_deps"):
            return "skill"  # 단순 LLM 처리 → skill 충분
    if parsed.get("schedule_required") and not parsed.get("external_deps"):
        return "job"  # 단순 cron 잡으로 충분
    return "agent"


# ══════════════════════════════════════════════════════════════
# 검증 결과 자동 삽입 — §10 채우기
# ══════════════════════════════════════════════════════════════

def _inject_verification_table(spec_md: str, rule_results: list[dict]) -> str:
    """부록 A(일관성 검증 결과) 표를 실제 검증 결과로 교체."""
    table_lines = ["| 검증 항목 | 결과 |", "|-----------|------|"]
    for r in rule_results:
        mark = "✅" if r["ok"] else "⚠️ " + r["msg"]
        table_lines.append(f"| {r['name']} | {mark} |")
    new_table = "\n".join(table_lines)

    # 신규 포맷: 부록 A 섹션 교체
    pat_new = r"(## 부록 A[^\n]*\n)([\s\S]*?)(?=^## 부록 B|\Z)"
    m = re.search(pat_new, spec_md, flags=re.MULTILINE)
    if m:
        return spec_md[:m.start()] + m.group(1) + "\n" + new_table + "\n\n" + spec_md[m.end():]

    # 구형 포맷(## 10.) 하위 호환
    pat_old = r"(## 10\.\s[^\n]*\n)([\s\S]*?)(?=^## 11\.|\Z)"
    m = re.search(pat_old, spec_md, flags=re.MULTILINE)
    if m:
        return spec_md[:m.start()] + m.group(1) + "\n" + new_table + "\n\n" + spec_md[m.end():]

    # 섹션 없으면 끝에 추가
    return spec_md + "\n\n## 부록 A: 일관성 검증 결과\n" + new_table + "\n"


# ══════════════════════════════════════════════════════════════
# exec_plan 생성 — §12 텍스트 → 실행 가능 JSON (Sonnet, 별도 호출)
# ══════════════════════════════════════════════════════════════

def _generate_exec_plan_from_spec(spec_md: str) -> list[dict]:
    """Stage 14 구현 계획 텍스트를 읽어 실행 가능한 exec_plan JSON 생성 (Sonnet).

    파일당 최대 20줄 스켈레톤만. 실패 시 빈 리스트 반환.
    """
    # 신규: Stage 14 / 구형 폴백: §12
    sec12 = _extract_section(spec_md, 14) or _extract_section(spec_md, 12)
    if not sec12:
        log.warning("⚠️ architect: Stage 14(구현 계획) 없음 — exec_plan 생성 스킵")
        return []

    # 에이전트 번호·이름 힌트 추출 (Stage 3 또는 §2 에서)
    sec2 = _extract_section(spec_md, 3) or _extract_section(spec_md, 2)
    agent_hint = ""
    m = re.search(r"이름[·\s*]*번호[^\n]*:\s*([^\n]+)", sec2)
    if m:
        agent_hint = m.group(1).strip()

    user_msg = (
        f"[에이전트 정보]\n{agent_hint}\n\n"
        f"[구현 계획 §12]\n{sec12}\n\n"
        "위 계획을 바탕으로 신규 파일 write_file JSON 배열을 출력하세요. JSON 배열만, 다른 텍스트 없이."
    )
    try:
        # ★ exec_plan 생성 — Sonnet 4.6 (사용자 박제 2026-05-14)
        # 신규 파일 write_file JSON 배열 = 코드 스켈레톤 생성 → coder alias
        from shared.llm import invoke_text
        raw = invoke_text(
            "coder",
            user_msg,
            system=_EXEC_PLAN_SYSTEM,
            max_tokens=3000,
            temperature=0.2,
        )
        if not raw:
            return []
        m2 = re.search(r"\[[\s\S]*\]", raw)
        if not m2:
            log.warning("⚠️ architect: exec_plan JSON 배열 미발견")
            return []
        steps = json.loads(m2.group(0))
        if not isinstance(steps, list):
            return []
        return [s for s in steps if isinstance(s, dict) and "tool" in s and "args" in s]
    except Exception as e:
        log.warning(f"⚠️ architect: exec_plan 생성 실패: {e}")
        _g_report("infra", e, module=__name__)
        return []


# ══════════════════════════════════════════════════════════════
# exec_plan 추출·분리 — LLM 출력에서 <exec_plan> JSON 블록 파싱
# ══════════════════════════════════════════════════════════════

_EXEC_PLAN_RE = re.compile(r"<exec_plan>\s*([\s\S]*?)\s*</exec_plan>", re.IGNORECASE)


def _extract_exec_plan(text: str) -> list[dict]:
    """LLM 출력에서 <exec_plan>...</exec_plan> JSON 추출. 실패 시 빈 리스트."""
    m = _EXEC_PLAN_RE.search(text)
    if not m:
        return []
    try:
        raw = m.group(1).strip()
        steps = json.loads(raw)
        if not isinstance(steps, list):
            return []
        # 최소 검증: tool + args 필드 존재
        return [s for s in steps if isinstance(s, dict) and "tool" in s and "args" in s]
    except Exception as e:
        log.warning(f"⚠️ architect: exec_plan JSON 파싱 실패: {e}")
        _g_report("infra", e, module=__name__)
        return []


def _strip_exec_plan(text: str) -> str:
    """spec_md 에서 <exec_plan> 블록 제거 (파일 저장 전)."""
    return _EXEC_PLAN_RE.sub("", text).strip()


# ══════════════════════════════════════════════════════════════
# 구현 계획 추출 — §12 → create_plan 인자 형태 (레거시 / 폴백)
# ══════════════════════════════════════════════════════════════

def _emit_plan_steps(spec_md: str) -> list[dict]:
    """§12 구현 계획 마크다운 → step dict 리스트 (exec_plan 없을 때 폴백)."""
    sec = _extract_section(spec_md, 12)
    if not sec:
        return []
    steps = []
    # `숫자. 도구명: ...` 형식 라인 추출
    for m in re.finditer(r"^\s*\d+\.\s*([a-z_]+):\s*(.+)$", sec, flags=re.MULTILINE):
        tool = m.group(1).strip()
        rest = m.group(2).strip()
        steps.append({
            "tool": tool,
            "raw": rest,
            "purpose": rest.split("—")[-1].strip() if "—" in rest else rest,
        })
    return steps


# ══════════════════════════════════════════════════════════════
# 단일 진입점 — design_new_agent
# ══════════════════════════════════════════════════════════════

# 자기참조 안전망 — 재귀 깊이 1 제한
_RECURSION_DEPTH = 0


def design_new_agent(
    user_intent: str,
    scope: str = "agent",
    output_path: Optional[str] = None,
) -> dict:
    """ARCHITECT 단일 진입점.

    Args:
        user_intent: 사용자 자유 문장.
        scope: "agent" 만 v1. "tool"|"job"|"skill" → NotImplementedError.
        output_path: 기획서 저장 경로. 없으면 docs/architect/{date}_{slug}.md.

    Returns:
        {
            "ok": bool,
            "spec_path": str,
            "summary": str,
            "verdict": "agent|skill|tool|job|unnecessary",
            "warnings": [str],
            "errors_risk": [{"id", "risk", "note"}],
            "next_plan_steps": [{"tool", "raw", "purpose"}],
        }
    """
    global _RECURSION_DEPTH

    # 입력 검증
    if not user_intent or not user_intent.strip():
        return {"ok": False, "error": "empty user_intent"}

    if scope == "meta":
        if _RECURSION_DEPTH >= 1:
            return {"ok": False, "error": "scope=meta 재귀 깊이 1 초과 — 무한루프 방지"}
        _RECURSION_DEPTH += 1
        scope = "agent"  # meta 는 자기 자신 재설계 → agent 양식 사용
    elif scope != "agent":
        return {"ok": False, "error": f"scope='{scope}' 는 v1 미지원 (agent 만 가능)"}

    try:
        # 1) Knowledge base 로드 (캐시 0)
        ctx = _load_context()

        # 2) 의도 파싱 (Sonnet)
        parsed = _parse_intent(user_intent)

        # 3) 대안 판단
        verdict = _check_alternative(parsed)

        # 4) 기획서 산출 (Sonnet)
        spec_md = _generate_spec(user_intent, scope, ctx, parsed)
        if not spec_md.strip():
            return {"ok": False, "error": "spec 산출 실패 (LLM 빈 응답)"}

        # 5) 검증 5종
        rule_results = _verify_against_rules(spec_md)
        warnings = [r["msg"] for r in rule_results if not r["ok"]]

        # 6) ERRORS replay 체크
        errors_risk = _replay_errors_check(spec_md)

        # 7) exec_plan 생성 (§12 → Sonnet 별도 호출, 파일당 20줄 스켈레톤)
        exec_plan_steps = _generate_exec_plan_from_spec(spec_md)
        # 혹시 이전 방식으로 spec 안에 <exec_plan> 있으면 제거
        spec_md = _strip_exec_plan(spec_md)

        # 8) 검증 결과 §10 자동 삽입
        spec_md = _inject_verification_table(spec_md, rule_results)

        # 9) §12 텍스트 플랜 추출 (폴백용)
        plan_steps = _emit_plan_steps(spec_md)

        # 10) 저장
        spec_path = _resolve_output_path(output_path, user_intent)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(spec_md, encoding="utf-8")

        # 11) exec_plan.json 별도 저장 (스텝이 있을 때만)
        if exec_plan_steps:
            ep_path = spec_path.with_suffix(".exec_plan.json")
            ep_path.write_text(
                json.dumps(exec_plan_steps, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info(f"🗂 exec_plan 저장: {ep_path} ({len(exec_plan_steps)}단계)")

        # 12) 요약 (텔레그램 송출용 — LLM 없이 간결)
        summary = _build_summary(parsed, verdict, len(exec_plan_steps) or len(plan_steps), len(warnings))

        return {
            "ok": True,
            "spec_path": str(spec_path),
            "summary": summary,
            "verdict": verdict,
            "warnings": warnings,
            "errors_risk": errors_risk,
            "next_plan_steps": plan_steps,       # §12 텍스트 폴백
            "exec_plan_steps": exec_plan_steps,  # 실행 가능한 JSON 스텝
        }
    except Exception as e:
        log.error(f"❌ design_new_agent 실패: {e}")
        _g_report("infra", e, module=__name__)
        return {"ok": False, "error": str(e)}
    finally:
        if _RECURSION_DEPTH > 0:
            _RECURSION_DEPTH -= 1


def _resolve_output_path(output_path: Optional[str], user_intent: str) -> Path:
    """저장 경로 결정. 미지정 시 docs/architect/{date}_{slug}.md."""
    if output_path:
        p = Path(output_path)
        if not p.is_absolute():
            p = _ROOT / p
        return p
    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^\w가-힣]+", "-", user_intent)[:40].strip("-").lower() or "untitled"
    return _ROOT / "docs" / "architect" / f"{date_str}_{slug}.md"


def _build_summary(parsed: dict, verdict: str, n_steps: int, n_warnings: int) -> str:
    """텔레그램 송출용 1-3 문장 요약 (LLM 없이 박힌 변형)."""
    name = parsed.get("name") or "?"
    role = (parsed.get("role") or "")[:60]
    verdict_ko = {
        "agent": "🆕 *새 에이전트* 권장",
        "skill": "🛠 *skill / 슬래시 명령* 권장",
        "tool": "🔧 *단일 도구* 권장",
        "job": "⏰ *cron 잡* 권장",
        "unnecessary": "⚠️ *신설 불필요*",
    }.get(verdict, verdict)
    warn_str = f" / ⚠️ 경고 {n_warnings}건" if n_warnings else ""
    return f"{verdict_ko} — {name}: {role}\n📋 구현 단계 {n_steps}개{warn_str}"


__all__ = [
    "design_new_agent",
    "SPEC_TEMPLATE",
    "_extract_exec_plan",
    "_strip_exec_plan",
]
