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
| harness sentinel 패턴 (★ ERRORS 4회 반복 박제) | 결정론적 step(발행·패치 적용·파일 쓰기 등)은 **state 센티널 플래그**로 재실행 방지 필수 — `state.get(<플래그>)` 가 True 면 `return {}` (no-op). 비결정론적(LLM) step은 sentinel 불필요(재실행=개선 기회). ★ 플래그 이름을 여기 박지 말 것 — 실제 사용 중인 이름은 `grep -rn '__.*_attempted__\|__.*_applied__' --include='*.py' .` 로 확인 (현재 `__nv_send_attempted__`·`__ts_send_attempted__`). 종전엔 `__patch_applied__` 라고 적혀 있었으나 **코드 히트 0건**이었다 (ERRORS [544]). |
| harness state 에 살아있는 핸들 금지 (ERRORS [544]) | Selenium driver·DB 커넥션 등 *살아있는 객체* 를 state 에 넣지 말 것 — ① 직렬화 불가(실측 msgpack TypeError) ② 액션 종료 시 close 를 부를 주인이 없어 샌다. `JARVIS00_INFRA/resources.py` `put()` 으로 등록하고 state 엔 `<이름>_key` 문자열만. 정리는 harness 가 `close_scope(action_def.name)` 로 자동. 스코프는 `state[ACTION_NAME_KEY]` 에서 파생(이름 하드코딩 금지). 검증: `precommit_check --category harness` 의 `harness/live-handle-in-state`. |
| ★ `__main__` 진입점 ensure_preflight 의무 (ERRORS [154]·[614]) | 외부 영향 가능한 모든 `if __name__ == "__main__"` 은 `ensure_preflight()` 선행 호출. **★ grep 으로 검증하지 말 것 (2026-08-10 정정)** — 종전 검증은 `xargs grep -L 'ensure_preflight'` 로 *문자열 유무* 만 봤는데, **문자열이 있는데 한 번도 안 도는** 진입점이 16곳 중 **8곳**이었다. 하위 폴더 스크립트를 `python <파일>` 로 직접 실행하면 `sys.path[0]` 이 그 폴더라 `from JARVIS00_INFRA...` 가 ModuleNotFoundError 로 죽고, 그것을 감싼 `except Exception` 이 경고 한 줄만 찍고 삼켰다. 그 경고는 stdout 으로만 나가는데 데몬 stdout 은 `/dev/null` 이라 **어디에도 안 남았다**. `--manual` 을 사람이 직접 돌려서야 눈에 띄었다. **코드 존재 ≠ 실행.** → ① 하위 폴더 진입점은 파일 상단에 **루트 마커(`jarvis_daemon.py`) 탐색** 부트스트랩 필수 (깊이를 `parent.parent` 로 박지 말 것 — ADR 008 이관 때 깨진 전례). ② preflight 호출을 **try/except 로 감싸지 말 것**(fail-closed). **검증은 기계가 한다**: `python3 shared/precommit_check.py --category preflight` (레그 `preflight/no-path-bootstrap`·`preflight/swallowed`) + `tests/test_entrypoint_preflight.py` 가 *자식 프로세스에서 실제로 실행* 해 확인한다. **남은 범위**: `ensure_preflight` 가 *아예 없는* `__main__` 이 실측 18곳 — 그중 `jarvis_daemon.py`(`run_preflight()` 사용)·`shared/db.py`·`severity.py` 등 라이브러리는 정상이고, 나머지는 외부 영향 여부를 판정해 개별 처리할 것(미완). |
| ★ subprocess PATH 항상 prepend (ERRORS [32][160][137] 4회 반복 박제) | `subprocess.run/Popen` env 생성 시 `/opt/homebrew/bin`, `/opt/homebrew/sbin`, `/usr/local/bin` 을 *항상 prepend*. `if _brew not in _cur_path` 조건부 금지 — launchd/keeper 기동 시 PATH 최소값(`/usr/bin:/bin:...`) 환경에서 조건부는 `False` 로 평가되어도 CLI 내부 PATH 인식 실패 사고 4회 반복. 올바른 패턴: `_EXTRA_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]; env["PATH"] = ":".join(_EXTRA_PATHS) + ":" + env.get("PATH", "")`. 검증: `grep -n "_EXTRA_PATHS\|항상 prepend" shared/llm.py JARVIS07_GUARDIAN/auto_repair.py` → 두 파일 모두 존재해야 함. |
