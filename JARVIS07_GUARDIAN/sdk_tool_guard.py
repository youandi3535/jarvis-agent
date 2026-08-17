"""JARVIS07_GUARDIAN/sdk_tool_guard.py — 자율 수리 세션의 *쓰기 범위* 단일 진입점.

★ 2026-08-14 사용자 지시 (T2) — **권한을 넓히는 모듈이 아니라 좁히는 모듈이다.**

문제 (실측):
  `auto_repair` 의 Claude Code SDK 호출 2곳이 `permission_mode="bypassPermissions"` 로
  세션을 띄우면서 **도구 허용·금지 목록을 하나도 넘기지 않았다.** 그래서 자율 수리
  세션이 `error_fixer` 의 보호목록에 있는 파일(헌법 문서·오류 기록·학습 원장)과
  자기 브레이크 모듈까지 편집할 수 있었고, 실제로 편집한 이력이 DB 에 남아 있다.
  `run_sdk_query` 는 금지목록 파라미터 자체가 **없어서** 호출자가 걸고 싶어도 못 걸었다.

이 모듈이 하는 일 — 딱 셋:
  ① 무엇을 못 건드리게 할지 **파생** 한다 (`guarded_targets`).
     목록을 새로 만들지 않는다. 주인은 셋뿐이고 전부 남의 집이다:
       · `error_fixer.protected_files()`  — 기록·학습 자산 (되돌리면 사라지는 것)
       · `error_fixer.deny_dirs()`        — 금지 디렉터리
       · `error_fixer.self_guard_files()` — 자기 안전장치 (고치면 못 막게 되는 것)
       · `architecture.DENY_FIX_PATHS`    — 보안·코어
     파생에 실패하면 통과가 아니라 **예외**(fail-closed). 이 저장소에는 '검사가 있는데
     조용히 무력화된' 사고가 여러 건 있다 — 못 읽었으면 세션을 띄우지 않는다.
  ② SDK 세션에 넘길 도구 금지 스펙을 만든다 (`disallowed_tools`).
  ③ 셸 우회를 같은 목록으로 판정한다 (`violations`) — PreToolUse 훅이 이걸 부른다.
     도구 이름만 막으면 `rm`·리다이렉션으로 그대로 우회된다 (실제로 수리 세션이
     `rm` 으로 백업 파일을 지운 이력이 있다).

★ 기본 동작은 '차단' 이 아니라 '기록' 이다 (`GUARDIAN_SDK_TOOL_GUARD=observe`).
  금지 범위가 넓으면 정당한 수리까지 막혀 *조용한 정지* 가 된다. 1주 실측
  (`error_log` 의 `SdkGuard*` 행)으로 무엇이 실제로 막힐지 본 뒤 사람이 `enforce` 로
  올린다. `off` 는 탈출구 — 이 모듈이 깨졌을 때도 시스템이 서지 않게 하는 유일한 문.

★ 관여 범위: **자율 세션만.** `run_sdk_query` 가 세션 env 에 표식을 심고, 훅은 그
  표식이 있을 때만 판단한다. 사람이 직접 띄운 Claude Code 세션(=이 저장소에서
  사람이 작업하는 경우)은 조용히 통과한다 — 사람의 편집을 막는 것은 이 규정의
  목적이 아니고, 막으면 규정이 먼저 미움받아 꺼진다.
"""
from __future__ import annotations

import logging
import os
import re
import shlex
from pathlib import Path

log = logging.getLogger("jarvis.guardian.sdk_guard")

_ROOT = Path(__file__).resolve().parents[1]

# ── 노브 (함수 안에서 읽는다 — 모듈 레벨 상수로 굳히면 monkeypatch·무배포 조정이 안 먹는다)
MODE_ENV_KEY    = "GUARDIAN_SDK_TOOL_GUARD"     # observe | enforce | off
SESSION_ENV_KEY = "JARVIS_SDK_GUARD_SESSION"    # 자율 세션 표식 (run_sdk_query 가 심는다)

MODE_OBSERVE = "observe"
MODE_ENFORCE = "enforce"
MODE_OFF     = "off"
MODES = (MODE_OBSERVE, MODE_ENFORCE, MODE_OFF)


class GuardDerivationError(RuntimeError):
    """보호 목록 파생 실패 — 통과가 아니라 **세션 미기동**(fail-closed)."""


# ── ① 파생 ─────────────────────────────────────────────────────────────

def guarded_sources() -> tuple[frozenset, frozenset]:
    """(금지 파일, 금지 디렉터리) — 주인들에게서 파생. **여기에 목록을 적지 않는다.**

    Raises:
        GuardDerivationError: 주인을 못 읽거나 결과가 비었을 때 (fail-closed).
    """
    try:
        from JARVIS07_GUARDIAN.error_fixer import (
            deny_dirs, protected_files, self_guard_files,
        )
        from JARVIS07_GUARDIAN.architecture import DENY_FIX_PATHS
        files = set(protected_files()) | set(self_guard_files()) | set(DENY_FIX_PATHS)
        dirs = set(deny_dirs())
    except GuardDerivationError:
        raise
    except Exception as e:                                   # noqa: BLE001
        raise GuardDerivationError(
            f"보호 목록 파생 실패 — 경계를 모르면 세션을 띄우지 않는다: {e}") from e
    if not files or not dirs:
        raise GuardDerivationError(
            f"보호 목록이 비었다 (files={len(files)}, dirs={len(dirs)}) — "
            "주인이 바뀌었는지 확인할 것. 빈 목록으로 통과시키면 가드가 없는 것과 같다")
    return frozenset(files), frozenset(dirs)


def guarded_targets() -> frozenset:
    """금지 파일 ∪ 금지 디렉터리 — 판정에 쓰는 단일 집합."""
    files, dirs = guarded_sources()
    return frozenset(files | dirs)


def guard_mode() -> str:
    """`observe`(기본) | `enforce` | `off`. 알 수 없는 값은 기본으로 되돌린다."""
    raw = (os.getenv(MODE_ENV_KEY) or "").strip().lower()
    if raw and raw not in MODES:
        log.warning("[sdk_guard] 알 수 없는 %s=%r — %s 로 처리", MODE_ENV_KEY, raw, MODE_OBSERVE)
        return MODE_OBSERVE
    return raw or MODE_OBSERVE


def in_guarded_session() -> bool:
    """지금 프로세스가 *자율 수리 세션 안* 인가 (훅이 사람 세션과 구분하는 유일한 근거)."""
    return bool((os.getenv(SESSION_ENV_KEY) or "").strip())


def session_env(mode: str | None = None) -> dict:
    """SDK 세션에 심을 env — 표식 + *그 시점에 정해진* 모드.

    ★ 모드를 함께 심는 이유: 훅은 별도 프로세스라 데몬의 env 를 못 볼 수 있다.
      세션이 시작될 때 정해진 모드가 그 세션 내내 유효해야 판정이 일관된다.
    """
    m = mode or guard_mode()
    return {SESSION_ENV_KEY: "1", MODE_ENV_KEY: m}


# ── ② SDK 도구 금지 스펙 ────────────────────────────────────────────────

# 파일을 *쓰는* 내장 도구. 읽기 도구(Read/Glob/Grep)는 막지 않는다 —
# 수리 세션은 읽어야 고칠 수 있고, 읽기는 되돌릴 수 없는 손상을 만들지 않는다.
_EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# 판정에서 '쓰기 의도 없음' 으로 보는 도구 — 그 외 *경로를 받는 모든 도구* 는
# 쓰기로 간주한다(② 동적: 새 도구가 생겨도 기본이 '보호' 쪽이다).
_READONLY_TOOLS = frozenset({
    "Read", "NotebookRead", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
    "TodoWrite", "Task", "ExitPlanMode", "AskUserQuestion",
})


def disallowed_tools() -> list:
    """SDK `ClaudeCodeOptions.disallowed_tools` 에 넘길 스펙 — 파생 결과에서 생성.

    형식은 Claude Code 권한 규칙(`Tool(경로패턴)`). Bash 는 여기서 다루지 않는다 —
    셸은 경로 패턴으로 표현되지 않아 **훅이 판정** 한다(`violations`).
    """
    files, dirs = guarded_sources()
    pats: list = []
    for f in sorted(files):
        pats.append(f)
        if "/" not in f:                     # 파일명만 있는 항목(예: `.env`)은 어디에 있든
            pats.append(f"**/{f}")
    for d in sorted(dirs):
        pats.append(f"{d}/**")
        if "/" not in d:
            pats.append(f"**/{d}/**")
    return [f"{tool}({p})" for p in pats for tool in _EDIT_TOOLS]


def session_guard() -> dict:
    """`run_sdk_query` 가 부르는 **단 하나의 문** — 모드·금지스펙·세션 env 를 한 번에.

    Raises:
        GuardDerivationError: 파생 실패 (호출자는 세션을 띄우지 말 것).
    """
    mode = guard_mode()
    if mode == MODE_OFF:
        return {"mode": mode, "disallowed_tools": [], "env": {}}
    specs = disallowed_tools()               # ← 파생 실패는 여기서 예외로 터진다
    return {
        "mode": mode,
        # observe 는 **막지 않는다** — 무엇이 막힐지 먼저 본다. 실제 차단은 enforce 만.
        "disallowed_tools": specs if mode == MODE_ENFORCE else [],
        "env": session_env(mode),
    }


# ── ③ 판정 (도구 + 셸 우회) ─────────────────────────────────────────────

def _rel_to_root(raw: str) -> str:
    """경로 문자열 → 저장소 상대경로. 루트 밖·판정 불가면 빈 문자열."""
    s = (raw or "").strip().strip("'\"")
    if not s:
        return ""
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = _ROOT / p                        # 세션 cwd 는 루트 (run_sdk_query 가 그렇게 띄운다)
    try:
        return str(p.resolve().relative_to(_ROOT.resolve()))
    except Exception:                        # noqa: BLE001
        return ""


def _match(rel: str, targets) -> str:
    """`rel` 이 금지 대상에 걸리면 걸린 항목, 아니면 빈 문자열.

    ★ 대조 규칙은 `error_fixer._safe_path` · `auto_repair._snapshot_py_files` 와 같은 꼴
      (경로형은 접두, 이름형은 경로 성분) — 셋이 서로 다르면 한쪽만 새는 구멍이 생긴다.
    """
    if not rel:
        return ""
    parts = Path(rel).parts
    name = Path(rel).name
    for t in targets:
        if "/" in t:
            # ★ 파일명까지 보는 이유: 셸은 `cd JARVIS07_GUARDIAN && rm ERRORS.md` 로
            #   상대경로를 바꿔 버린다. 세션 cwd 가 루트라는 전제는 세그먼트 안에서 깨진다.
            #   경로를 추적할 수는 없으니 **보수적으로** 이름 일치도 걸린 것으로 본다
            #   (관측 모드에선 기록만 되고, 과잉분은 1주 실측에서 그대로 드러난다).
            if rel == t or rel.startswith(t + "/") or name == Path(t).name:
                return t
        elif t in parts:                     # 파일명 일치 + 단일 세그먼트 디렉터리 모두 커버
            return t
    return ""


# 셸에서 *파일을 바꾸는* 낱말. 목록이 아니라 **의도** 를 잡는다 —
# 여기 없는 도구는 아래 인터프리터 규칙이 보수적으로 걷어낸다.
_WRITE_UTILS = frozenset({
    "rm", "mv", "cp", "dd", "tee", "truncate", "chmod", "chown", "chgrp",
    "ln", "install", "patch", "shred", "unlink", "rmdir", "mkdir", "touch",
})
_GIT_WRITE_SUBS = frozenset({
    "checkout", "restore", "reset", "clean", "rm", "mv", "apply", "stash", "commit",
})
_INTERPRETERS = frozenset({
    "python", "python3", "perl", "ruby", "node", "bash", "sh", "zsh", "awk", "sed",
})
_SEGMENT_SPLIT = re.compile(r"(?:\|\||&&|[;\n|])")
# `> path` / `>> path`. fd 복제(`2>&1`)·프로세스 치환(`>(…)`)은 뒤따르는 문자가
# 문자클래스에서 빠져 있어 **자동으로** 안 걸린다. 앞자리 숫자를 배제하지 않는 이유:
# `2> logs/x.log` 는 진짜 파일 쓰기다 — 종전 lookbehind 는 그것까지 놓쳤다.
_REDIRECT = re.compile(r">>?\s*([^\s;|&()<>]+)")


def _shell_write_targets(command: str) -> list:
    """셸 명령에서 *쓰기 대상이 될 수 있는* 경로 후보 → [(경로, 규칙)].

    세그먼트(`;` `&&` `|` 등)로 쪼개 각각 판단한다 — 통째로 보면
    `rm /tmp/x && cat ERRORS.md` 가 'ERRORS.md 를 지운다' 로 오판된다.
    """
    out: list = []
    for seg in _SEGMENT_SPLIT.split(command or ""):
        seg = seg.strip()
        if not seg:
            continue
        for m in _REDIRECT.finditer(seg):
            out.append((m.group(1), "redirect"))
        try:
            words = shlex.split(seg, comments=False)
        except ValueError:                   # 따옴표 불균형 — 낱말로만 쪼갠다
            words = seg.split()
        if not words:
            continue
        head = Path(words[0]).name
        args = [w for w in words[1:] if not w.startswith("-")]
        if head in _WRITE_UTILS:
            out += [(a, f"shell:{head}") for a in args]
        elif head == "git" and args and args[0] in _GIT_WRITE_SUBS:
            out += [(a, f"git:{args[0]}") for a in args[1:]]
        elif head in _INTERPRETERS:
            # 인터프리터는 무엇이든 할 수 있다(`python -c "open(p,'w')"`·heredoc).
            # 의도를 못 읽으므로 *보호 대상이 언급되면* 보수적으로 신고한다.
            out += [(w, f"interpreter:{head}") for w in words[1:]]
    return out


def violations(tool_name: str, tool_input: dict) -> list:
    """이 도구 호출이 보호 대상을 건드리는가 → [(대상, 규칙)]. 비면 통과.

    Raises:
        GuardDerivationError: 파생 실패 (호출자가 모드에 따라 처리 — enforce 면 차단).
    """
    targets = guarded_targets()
    ti = tool_input if isinstance(tool_input, dict) else {}
    hits: list = []
    seen = set()

    def _add(raw: str, rule: str) -> None:
        rel = _rel_to_root(raw)
        hit = _match(rel, targets)
        if hit and (rel, rule) not in seen:
            seen.add((rel, rule))
            hits.append((rel, rule))

    if tool_name == "Bash":
        for raw, rule in _shell_write_targets(str(ti.get("command") or "")):
            _add(raw, rule)
        return hits

    if tool_name in _READONLY_TOOLS:
        return hits

    # 경로를 받는 그 외 모든 도구 = 쓰기로 간주 (새 도구의 기본값이 '보호' 쪽)
    for key, val in ti.items():
        if isinstance(val, str) and (key == "path" or key.endswith("_path")):
            _add(val, f"tool:{tool_name}")
    return hits


def violation(tool_name: str, tool_input: dict) -> str:
    """`violations` 의 한 줄 요약 — 없으면 빈 문자열."""
    hits = violations(tool_name, tool_input)
    if not hits:
        return ""
    head = hits[0]
    more = f" 외 {len(hits) - 1}건" if len(hits) > 1 else ""
    return f"{tool_name} → {head[0]} (규칙 {head[1]}){more}"


# ── 기록 (새 채널 신설 없음 — 기존 error_collector 박제 경로 재사용) ──────

def guard_error_type(mode: str, tool_name: str) -> str:
    """오류 타입은 **그 도메인이 파생한다** (CLAUDE.md 세분화 규정).

    모드·도구가 늘어나면 타입이 자동으로 따라온다 — 중앙 매핑표를 만들지 않는다.
    """
    def _cap(s: str) -> str:
        s = re.sub(r"[^A-Za-z0-9]", "", (s or "").strip())
        return (s[:1].upper() + s[1:]) if s else "Unknown"
    return f"SdkGuard{_cap(mode)}{_cap(tool_name)}"


def record_observation(mode: str, tool_name: str, target: str, rule: str) -> None:
    """무엇이 걸렸는지 박제 — observe 1주 실측의 재료.

    ★ 채널을 신설하지 않는다: `error_collector.record_external_change` 는 이미
      "외부 도구가 코드를 건드렸다" 를 적는 자리이고, status='manual' 로 들어가
      **자동 수리 대상이 되지 않는다**(관측이 수리 루프를 다시 돌리면 안 된다).
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import record_external_change
        record_external_change(
            source="sdk_tool_guard",
            fixed_file=target,
            description=(f"[{mode}] 자율 수리 세션이 보호 대상 쓰기를 시도 — "
                         f"{tool_name} / 규칙 {rule}"),
            error_type=guard_error_type(mode, tool_name),
            severity="low",
            actor="sdk_session",
        )
    except Exception as e:                                   # noqa: BLE001
        log.warning("[sdk_guard] 관측 박제 실패 (%s) — 판정은 계속한다", e)


__all__ = [
    "MODE_ENV_KEY", "SESSION_ENV_KEY", "MODES",
    "MODE_OBSERVE", "MODE_ENFORCE", "MODE_OFF",
    "GuardDerivationError",
    "guard_mode", "in_guarded_session", "session_env",
    "guarded_sources", "guarded_targets", "disallowed_tools", "session_guard",
    "violations", "violation", "guard_error_type", "record_observation",
]
