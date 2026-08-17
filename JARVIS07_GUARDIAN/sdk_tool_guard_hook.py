#!/usr/bin/env python3
"""PreToolUse 훅 — 자율 수리 세션의 *셸 우회* 까지 같은 목록으로 본다.

★ 왜 `.claude/hooks/` 가 아니라 여기인가 (2026-08-14 실측):
  종전 `.gitignore` 가 `.claude/` 를 통째로 무시했다 — 거기 두면 **커밋되지 않아**
  새 체크아웃·CI 에는 파일 자체가 없다(= 훅이 없는 것과 같고, CI 는 빨개진다).
  판정 owner 옆(JARVIS07)에 두고 `.claude/settings.json` 이 이 경로를 가리킨다.
  (선례: `JARVIS07_GUARDIAN/conversation_hook.py` 도 같은 이유로 추적되는 폴더에 있다.)
★ 등록도 이제 저장소를 따라간다 (2026-08-17): 종전엔 등록(`settings.json`)이 **기기
  로컬** 이라 새 체크아웃·CI 에선 이 가드가 *꺼진 채* 돌았고, 그 사실을 확인하던
  테스트는 파일이 없어 조용히 skip 됐다(= 검사가 없는 것과 같다). `.gitignore` 를
  `.claude/*` + `!.claude/settings.json` 으로 바꿔 **공유 설정 파일 하나만** 추적한다
  (개인 권한 목록 `settings.local.json` 은 계속 무시). 그래서 `precommit --category
  sdkwrite` 의 `hook-unregistered` 는 이제 *경고가 아니라 차단* 이다 — 등급도 박지 않고
  `.gitignore` 의 실제 판정에서 파생한다.

★ 2026-08-14 (T2). 도구 이름만 제한하면 셸로 우회된다 — 실제로 수리 세션이 `rm` 으로
  백업 파일을 지운 이력이 있다. SDK 의 `disallowed_tools` 는 `Write/Edit` 같은 내장
  도구에만 걸리므로, `Bash` 는 여기서 판정한다.

★ 이 파일에 **경로를 박지 않는다.** 판정도 목록도 전부 owner 모듈이 한다:
      JARVIS07_GUARDIAN/sdk_tool_guard.py  ← violations() / guard_mode() / record_observation()
  훅은 stdin(JSON) → owner 질의 → stdout(JSON) 어댑터일 뿐이다.
  (검증: `python3 shared/precommit_check.py --category sdkwrite` 의
   `sdkwrite/hook-hardcoded`·`sdkwrite/hook-unregistered` 레그)

★ 관여 범위: **자율 세션만.** 아래 표식 env 가 있을 때만 판단한다. 사람이 직접 띄운
  Claude Code 세션은 즉시 통과 — 사람의 편집을 막는 것은 이 규정의 목적이 아니다.

★ 기본은 차단이 아니라 기록(`observe`). 승격은 사람이 한다.
"""
from __future__ import annotations

import json
import os
import sys

# ★ 사본 2개 — owner(`sdk_tool_guard.MODE_ENV_KEY`/`SESSION_ENV_KEY`)가 진실이다.
#   여기 두는 이유는 둘뿐: ① 사람 세션에서 *owner import 비용조차* 치르지 않고 빠져나가기
#   ② owner 를 못 읽는 상황에서 enforce 를 fail-closed 로 유지하기.
#   어긋남은 precommit `sdkwrite/knob-mismatch` 가 잡는다.
_SESSION_ENV = "JARVIS_SDK_GUARD_SESSION"
_MODE_ENV    = "GUARDIAN_SDK_TOOL_GUARD"


def _root():
    """루트 마커(`jarvis_daemon.py`) 탐색 — 깊이를 박지 않는다(ADR 008 이관 전례)."""
    from pathlib import Path
    here = Path(__file__).resolve()
    for cand in (here.parent, *here.parents):
        if (cand / "jarvis_daemon.py").exists():
            return cand
    return None


def _deny(reason: str) -> None:
    """PreToolUse 차단 응답 — 신·구 스키마 둘 다 채운다(CLI 버전차 흡수)."""
    print(json.dumps({
        "decision": "block",
        "reason": reason,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }, ensure_ascii=False))


def main() -> int:
    if not (os.environ.get(_SESSION_ENV) or "").strip():
        return 0                              # 사람이 띄운 세션 — 관여하지 않는다
    mode = (os.environ.get(_MODE_ENV) or "").strip().lower()
    if mode == "off":
        return 0

    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        return 0                              # 입력을 못 읽으면 판단 근거가 없다
    tool = str(event.get("tool_name") or "")
    tin = event.get("tool_input") or {}
    if not tool:
        return 0

    root = _root()
    if root and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from JARVIS07_GUARDIAN.sdk_tool_guard import (
            MODE_ENFORCE, guard_mode, record_observation, violations,
        )
        mode = guard_mode()                   # owner 가 정본 (표식 env 는 그 사본)
        hits = violations(tool, tin)
    except Exception as e:                    # 파생 실패 — enforce 면 fail-closed
        if mode == "enforce":
            _deny(f"쓰기 범위 판정 불가로 차단(fail-closed): {e}")
            return 0
        print(f"[sdk_guard] 판정 불가 — 관측만 건너뜀: {e}", file=sys.stderr)
        return 0

    if not hits:
        return 0

    target, rule = hits[0]
    record_observation(mode, tool, target, rule)     # observe·enforce 공통 — 실측 재료
    if mode == MODE_ENFORCE:
        _deny(
            f"보호 대상 쓰기 차단: {target} (도구 {tool} / 규칙 {rule}). "
            f"이 경로는 자율 수리 세션이 편집할 수 없다 — 사람이 직접 수정할 것."
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _e:                   # 훅이 세션을 죽이지 않게
        print(f"[sdk_guard] hook error: {_e}", file=sys.stderr)
        sys.exit(0)
