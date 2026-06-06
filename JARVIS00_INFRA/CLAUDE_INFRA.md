# JARVIS00_INFRA

## 역할 — 시스템 메타 관리 (런타임 + 설계타임)
- **런타임 메타**: 데몬 프로세스 라이프사이클 + 시스템 상태 종합 보고 (`/status`·`/restart`·`/quit`)
- **설계타임 메타**: 새 에이전트·도구·잡·skill 신설 *기획서* 산출 (`architect.design` 인텐트). 실행은 절대 안 함 — 마크다운 산출물만, 코드 변경은 `create_plan` 위임 + 인라인 버튼 ✅.

## 비직관적 파일 역할
- `infra_agent.py` — 런타임 메타 진입점. capability 등록 + 상태 빌드 + 모든 인프라 핸들러 + ARCHITECT 위임 분기.
- `architect.py` — 설계타임 메타 본체 (`design_new_agent`). 호출당 CLAUDE.md / ERRORS.md / capability declares / 도구 카탈로그 / DEFAULT_JOBS *동적* 로드 — 캐시 0.
- `ARCHITECT_DESIGN.md` — ARCHITECT 자체 설계 기획서 (수동 v0). v1 안정화 후 자기 자신 재설계로 갱신.

## 비직관적 규칙

| 항목 | 규칙 |
|------|------|
| daemon 런타임 참조 | `import jarvis_daemon as _dm` 함수 내 lazy import — 모듈 초기화 시 circular import 방지 |
| 새 명령 추가 | `handle_command(cmd)` 에 elif 추가. 처리하면 True 반환 필수 |
| 새 SAFE 인텐트 | `handle_safe_intent(intent, params)` + `dispatchers.py SAFE_INTENTS` 동시 추가 (★ params 인자 시그니처 — 자유 문장 파라미터 전달) |
| 새 승인 인텐트 | `execute_approval(intent)` + `dispatchers.py APPROVAL_INTENTS` 동시 추가 |
| ARCHITECT 호출 | 항상 `JARVIS00_INFRA.architect.design_new_agent` 단일 진입점. 다른 위치에서 직접 LLM 호출 금지 |
| ARCHITECT 캐시 | `_load_context()` 결과 캐시 *절대* 금지 — 시스템 진화 즉시 반영 의무 |
| ARCHITECT 산출물 | `docs/architect/{date}_{slug}.md` 단일 마크다운. 코드 수정 0건 |
| ARCHITECT 재귀 | `scope="meta"` 호출 시 깊이 1 제한 (자기 자신 재설계 무한루프 방지) |
| harness sentinel 패턴 (★ ERRORS 4회 반복 박제) | 결정론적 step(패치 적용·파일 쓰기 등)은 `__patch_applied__` 플래그로 재실행 방지 필수. `state.get("__patch_applied__")` 확인 후 True이면 `return {}` (no-op). 비결정론적(LLM) step은 sentinel 불필요 — 재실행=개선 기회. |
| ★ `__main__` 진입점 ensure_preflight 의무 (ERRORS [154] 박제) | 외부 영향 가능한 모든 `if __name__ == "__main__"` 블록은 *반드시* `from JARVIS00_INFRA.preflight import ensure_preflight; ensure_preflight()` 선행 호출. 새 진입점 추가 시 자동 검증: `grep -rn '__name__.*__main__' --include='*.py' . \| xargs grep -L 'ensure_preflight' 2>/dev/null` — 결과가 read-only 도구 (log_monitor·agent_registration_check) 만이어야 함. |
| ★ subprocess PATH 항상 prepend (ERRORS [32][160][137] 4회 반복 박제) | `subprocess.run/Popen` env 생성 시 `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin` 을 *항상 prepend*. `if _brew not in _cur_path` 조건부 금지 — launchd/keeper 기동 시 PATH 최소값(`/usr/bin:/bin:...`) 환경에서 조건부는 `False` 로 평가되어도 CLI 내부 PATH 인식 실패 사고 4회 반복. 올바른 패턴: `_EXTRA_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]; env["PATH"] = ":".join(_EXTRA_PATHS) + ":" + env.get("PATH", "")`. 검증: `grep -n "_EXTRA_PATHS\|항상 prepend" shared/llm.py JARVIS07_GUARDIAN/auto_repair.py` → 두 파일 모두 존재해야 함. |
