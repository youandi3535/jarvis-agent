"""JARVIS01_MASTER/agent_tools.py — JARVIS01 마스터 라우터 도구 카탈로그 (Phase 2-B A).

★ 자율 에이전트 시스템의 *행동 어휘*. ReAct 패턴 라우터가 이 도구들을 호출해
실제 일을 한다. 처음부터 *권한·로그·승인 게이트* 박아둬야 폭주 방지.

설계:
- 모든 도구는 `@register_tool` (shared/tools.py) 로 중앙 등록.
- SAFE (3개): 정보 조회 — 즉시 실행. 부작용 없음.
    - list_capabilities — 등록된 에이전트/인텐트 카탈로그
    - get_recent_events — 이벤트 버스 최근 N건
    - query_post_analysis — DB 발행글 조회 (필터)
- APPROVAL (2개): 외부 영향 — 텔레그램 인라인 버튼 승인 후 실행.
    - call_jarvis01 — 블로그 발행 위임
    - call_jarvis02 — 트렌드/품질 분석 위임

★ 16시 cron 영향 0 보장: 도구는 *새 엔트리포인트* 로만 노출. 기존 호출 흐름
(scheduler·watchdog·텔레그램 직접 명령) 은 그대로. 라우터 ReAct 노드만 사용.

Phase 2-B B/C 에서 활용:
- B: router.py 가 LLM bind_tools() 로 이 도구들을 LLM 에 노출 → 다단계 ReAct.
- C: requires_approval=True 도구 호출 시 텔레그램 인라인 버튼 게이트.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

# 루트 sys.path
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.tools import register_tool


# ══════════════════════════════════════════════════════════════
# SAFE 도구 (정보 조회 — 부작용 없음, 즉시 실행)
# ══════════════════════════════════════════════════════════════

@register_tool(
    name="list_capabilities",
    domain="core",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="등록된 모든 에이전트와 그들이 처리 가능한 intent 카탈로그 조회. 라우터가 어떤 에이전트가 있는지 파악할 때 사용.",
)
def list_capabilities() -> dict:
    """등록된 에이전트 capability 카탈로그.

    Returns:
        {
          "agents": [
            {"agent_id": str, "domain": str, "intents": [str], "tools": [str],
             "requires_approval": [str], "cost_class": str, "description": str}
          ],
          "total_agents": int,
          "total_intents": int,
        }
    """
    from shared import capabilities

    caps = capabilities.all_capabilities()
    agents = [
        {
            "agent_id": c.agent_id,
            "domain": c.domain,
            "intents": list(c.intents),
            "tools": list(c.tools),
            "requires_approval": list(c.requires_approval),
            "cost_class": c.cost_class,
            "description": c.description,
        }
        for c in caps
    ]
    return {
        "agents": agents,
        "total_agents": len(agents),
        "total_intents": len(capabilities.list_intents()),
    }


@register_tool(
    name="get_recent_events",
    domain="core",
    side_effect="none",
    cost_class="low",
    requires_approval=False,
    description="이벤트 버스의 최근 이벤트 조회. event_type 필터 옵션. 라우터가 시스템 활동 흐름을 파악할 때 사용.",
)
def get_recent_events(limit: int = 20, event_type: Optional[str] = None) -> dict:
    """events 테이블 최근 N건 조회.

    Args:
        limit: 최대 행 수 (1~100, 기본 20).
        event_type: 정확 매칭 필터 (예: "post_published"). None=전체.

    Returns:
        {"events": [{"id", "event_type", "source", "payload", "created_at"}], "count": n}
    """
    import json as _json
    from shared import db

    limit = max(1, min(int(limit or 20), 100))
    rows: list[dict] = []
    try:
        with db.get_db() as conn:
            if event_type:
                cur = conn.execute(
                    "SELECT id, event_type, source, payload, created_at "
                    "FROM events WHERE event_type=? ORDER BY id DESC LIMIT ?",
                    (event_type, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT id, event_type, source, payload, created_at "
                    "FROM events ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            for r in cur.fetchall():
                pay = r["payload"] or "{}"
                try:
                    pay = _json.loads(pay) if isinstance(pay, str) else pay
                except Exception:
                    pay = {"_raw": str(pay)[:200]}
                rows.append({
                    "id": r["id"],
                    "event_type": r["event_type"],
                    "source": r["source"],
                    "payload": pay,
                    "created_at": r["created_at"],
                })
    except Exception as e:
        return {"events": [], "count": 0, "error": str(e)}
    return {"events": rows, "count": len(rows)}


@register_tool(
    name="query_post_analysis",
    domain="blog",
    side_effect="none",
    cost_class="low",
    requires_approval=False,
    description="DB post_analysis 테이블 조회 — 발행된 블로그 글 메타. platform/status/theme 필터. 라우터가 발행 이력을 참조할 때 사용.",
)
def query_post_analysis(
    limit: int = 10,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    theme_contains: Optional[str] = None,
) -> dict:
    """post_analysis 테이블 필터 조회.

    Args:
        limit: 최대 행 수 (1~50, 기본 10).
        platform: "naver" | "tistory" | None.
        status: "pending_analysis" | "analyzed" | "revised" | ... | None.
        theme_contains: theme LIKE %X% 부분 매칭.

    Returns:
        {"posts": [{"id","platform","theme","title","url","status","created_at","is_revised"}], "count": n}
    """
    from shared import db

    limit = max(1, min(int(limit or 10), 50))
    where = []
    args: list[Any] = []
    if platform:
        where.append("platform = ?")
        args.append(platform)
    if status:
        where.append("status = ?")
        args.append(status)
    if theme_contains:
        where.append("theme LIKE ?")
        args.append(f"%{theme_contains}%")
    sql = (
        "SELECT id, platform, theme, title, url, status, is_revised, created_at "
        "FROM post_analysis"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)

    rows: list[dict] = []
    try:
        with db.get_db() as conn:
            for r in conn.execute(sql, args).fetchall():
                rows.append({
                    "id": r["id"],
                    "platform": r["platform"],
                    "theme": r["theme"],
                    "title": r["title"],
                    "url": r["url"],
                    "status": r["status"],
                    "is_revised": r["is_revised"],
                    "created_at": r["created_at"],
                })
    except Exception as e:
        return {"posts": [], "count": 0, "error": str(e)}
    return {"posts": rows, "count": len(rows)}


# ══════════════════════════════════════════════════════════════
# APPROVAL 도구 (외부 영향 — 텔레그램 승인 게이트 필수)
# ══════════════════════════════════════════════════════════════

@register_tool(
    name="call_jarvis01",
    domain="blog",
    side_effect="external",
    rollback=None,
    cost_class="medium",
    requires_approval=True,
    description="JARVIS02 WRITER 위임 — 블로그 발행. intent: blog.theme_post.create | blog.economic_post.create. params.platforms 로 플랫폼 분리. (발행글 수정 blog.post.revise 는 인라인 승인 버튼 전용 — 여기로 라우팅 금지)",
)
def call_jarvis01(intent: str, params: Optional[dict] = None) -> dict:
    """JARVIS02 WRITER 호출.

    Args:
        intent: "blog.theme_post.create" / "blog.economic_post.create" / "blog.post.revise".
        params: {"theme_name": str, "platforms": ["naver"|"tistory", ...]}.
                platforms 미지정·3개 → 전체. 1~2개 → 분리 발행.

    Returns:
        {"ok": bool, "commands": [str], "dispatched": bool, "note": str}

    ★ 안전망: requires_approval=True. 라우터 ReAct 노드는 호출 전 텔레그램
    인라인 버튼 게이트 통과해야 함 (Phase 2-B C 에서 통합).
    """
    from JARVIS01_MASTER.dispatchers import build_j01_command, get_dispatch_mode

    if get_dispatch_mode(intent) != "APPROVAL":
        return {
            "ok": False,
            "dispatched": False,
            "commands": [],
            "note": f"intent '{intent}' 은 APPROVAL 모드 아님. SAFE 도구 사용.",
        }

    params = params if isinstance(params, dict) else {}
    cmd = build_j01_command(intent, params)
    if cmd is None:
        return {
            "ok": False,
            "dispatched": False,
            "commands": [],
            "note": f"intent '{intent}' 에 매칭되는 J01 명령 없음.",
        }
    cmds: list[str] = cmd if isinstance(cmd, list) else [cmd]

    # e12 (J01→J02 라우팅) 활성화
    try:
        from shared.pipeline_activity import mark_active
        mark_active("e12")
    except Exception:
        pass

    # 실제 디스패치 — JARVIS02 의 handle_telegram_command 호출
    # (이는 내부적으로 별도 스레드에서 실행되므로 즉시 리턴)
    try:
        import importlib
        spec = importlib.util.spec_from_file_location(
            "_jarvis01_scheduler",
            str(_ROOT / "JARVIS02_WRITER" / "scheduler.py"),
        )
        if spec is None or spec.loader is None:
            raise ImportError("JARVIS02_WRITER/scheduler.py 로드 실패")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore
        for c in cmds:
            mod.handle_telegram_command(c)
    except Exception as e:
        return {
            "ok": False,
            "dispatched": False,
            "commands": cmds,
            "note": f"JARVIS02 호출 실패: {e}",
        }

    return {
        "ok": True,
        "dispatched": True,
        "commands": cmds,
        "note": f"{len(cmds)}개 명령 JARVIS02 에 디스패치 완료 (백그라운드 실행).",
    }


@register_tool(
    name="call_jarvis02",
    domain="trend",
    side_effect="external",
    rollback=None,
    cost_class="medium",
    requires_approval=True,
    description="JARVIS03 RADAR 위임 — 트렌드 보고/품질 분석 트리거. intent: trend.report | blog.post.evaluate.",
)
def call_jarvis02(intent: str, params: Optional[dict] = None) -> dict:
    """JARVIS03 RADAR 호출.

    Args:
        intent: "trend.report" (즉시 결과 텍스트) | "blog.post.evaluate" (백그라운드 분석).
        params: 현재 미사용 (예약).

    Returns:
        {"ok": bool, "intent": str, "result": str, "mode": "sync"|"async"}

    ★ trend.report 는 SAFE 인텐트지만 도구는 APPROVAL 로 박음 — 라우터의
    *모든* 외부 도메인 호출에 일관된 게이트 적용. Phase 2-B C 에서 정책 세분화 가능.
    """
    from JARVIS01_MASTER.dispatchers import execute_safe

    if intent not in ("trend.report", "blog.post.evaluate"):
        return {
            "ok": False,
            "intent": intent,
            "result": "",
            "mode": "sync",
            "note": f"지원하지 않는 intent: {intent}",
        }
    try:
        result = execute_safe(intent, params or {}, "")
    except Exception as e:
        return {
            "ok": False,
            "intent": intent,
            "result": "",
            "mode": "sync",
            "note": f"실행 실패: {e}",
        }
    mode = "async" if intent == "blog.post.evaluate" else "sync"
    return {
        "ok": True,
        "intent": intent,
        "result": result or "(빈 결과)",
        "mode": mode,
    }


# ══════════════════════════════════════════════════════════════
# Phase 3-A — 파일 도구 (코드 자가수정 능력)
# ══════════════════════════════════════════════════════════════
#
# ★ 안전 박스: jarvis-agent 폴더 안 파일만 접근. 절대 경로·심볼릭링크·..
#   탈출 차단. 변경 작업은 *모두* APPROVAL → 텔레그램 인라인 버튼.

import os as _os
import re as _re

_JARVIS_ROOT_ABS = _ROOT.resolve()
_DENY_DIRS = {".venv", ".git", "__pycache__", "shared/backups", "JARVIS02_WRITER/chrome_profile"}


def _safe_path(path: str) -> Optional[Path]:
    """jarvis-agent 폴더 안 경로만 허용. 그 밖이면 None.

    상대 경로는 jarvis-agent 루트 기준. 절대 경로는 jarvis-agent 안만.
    .. 탈출·심볼릭링크 차단.
    """
    try:
        p = Path(path)
        if not p.is_absolute():
            p = (_JARVIS_ROOT_ABS / p)
        p_resolved = p.resolve()
        # jarvis-agent 폴더 안인지
        try:
            p_resolved.relative_to(_JARVIS_ROOT_ABS)
        except ValueError:
            return None
        # deny dirs
        rel = str(p_resolved.relative_to(_JARVIS_ROOT_ABS))
        for deny in _DENY_DIRS:
            if rel == deny or rel.startswith(deny + "/"):
                return None
        return p_resolved
    except Exception:
        return None


@register_tool(
    name="read_file",
    domain="code",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="jarvis-agent 폴더 안 파일 읽기. limit=N 으로 처음 N줄만, offset=K 로 K줄부터. 큰 파일은 limit 권장.",
)
def read_file(path: str, limit: Optional[int] = None, offset: int = 0) -> dict:
    """파일 내용 읽기.

    Args:
        path: jarvis-agent 루트 기준 상대 경로 또는 절대 경로 (jarvis-agent 안만).
        limit: 최대 줄 수 (None=전체, 권장: 200).
        offset: 시작 줄 번호 (0-based).

    Returns: {"ok", "path", "lines", "total_lines", "content"}
    """
    p = _safe_path(path)
    if p is None:
        return {"ok": False, "error": f"unsafe path: {path}"}
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if p.is_dir():
        return {"ok": False, "error": f"is directory: {path}"}
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        total = len(all_lines)
        start = max(0, int(offset))
        end = total if limit is None else min(total, start + int(limit))
        chunk = all_lines[start:end]
        # 각 라인에 번호 부여 (1-based)
        numbered = [f"{i+1:5d}\t{line}" for i, line in enumerate(chunk, start=start)]
        return {
            "ok": True, "path": str(p.relative_to(_JARVIS_ROOT_ABS)),
            "lines": end - start, "total_lines": total,
            "offset": start,
            "content": "".join(numbered)[:30000],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="glob_files",
    domain="code",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="jarvis-agent 폴더 안 파일 패턴 매칭. 예: 'JARVIS02_WRITER/*.py', '**/*_agent.py'.",
)
def glob_files(pattern: str) -> dict:
    """glob 패턴으로 파일 찾기."""
    if ".." in pattern:
        return {"ok": False, "error": "pattern contains '..'"}
    try:
        results: list[str] = []
        for p in _JARVIS_ROOT_ABS.glob(pattern):
            try:
                rel = str(p.relative_to(_JARVIS_ROOT_ABS))
            except ValueError:
                continue
            # deny dirs 제외
            if any(rel == d or rel.startswith(d + "/") for d in _DENY_DIRS):
                continue
            results.append(rel)
        results.sort()
        return {"ok": True, "pattern": pattern, "count": len(results),
                "files": results[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="grep_code",
    domain="code",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="jarvis-agent 폴더 안 코드 정규식 검색. file_glob 으로 범위 제한 가능.",
)
def grep_code(pattern: str, file_glob: Optional[str] = None,
              max_results: int = 100) -> dict:
    """ripgrep 비슷한 코드 검색.

    Args:
        pattern: 정규식 (Python re 문법).
        file_glob: '*.py' 같은 패턴으로 범위 제한 (옵션).
        max_results: 결과 줄 최대 (기본 100).
    """
    try:
        rx = _re.compile(pattern)
    except _re.error as e:
        return {"ok": False, "error": f"invalid regex: {e}"}

    matches: list[dict] = []
    file_patterns = [file_glob] if file_glob else ["**/*.py", "**/*.md"]
    seen_files: set[str] = set()
    for fp in file_patterns:
        for p in _JARVIS_ROOT_ABS.glob(fp):
            if not p.is_file():
                continue
            try:
                rel = str(p.relative_to(_JARVIS_ROOT_ABS))
            except ValueError:
                continue
            if rel in seen_files:
                continue
            if any(rel == d or rel.startswith(d + "/") for d in _DENY_DIRS):
                continue
            seen_files.add(rel)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            matches.append({
                                "file": rel, "line": i,
                                "text": line.rstrip("\n")[:300],
                            })
                            if len(matches) >= max_results:
                                break
            except Exception:
                continue
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break
    return {"ok": True, "pattern": pattern, "count": len(matches),
            "matches": matches}


@register_tool(
    name="syntax_check",
    domain="code",
    side_effect="none",
    cost_class="free",
    requires_approval=False,
    description="Python 파일 syntax 검증 (ast.parse). 오류 시 줄·메시지 반환.",
)
def syntax_check(path: str) -> dict:
    p = _safe_path(path)
    if p is None or not p.exists() or not p.suffix == ".py":
        return {"ok": False, "error": f"not a Python file or unsafe: {path}"}
    import ast as _ast
    try:
        _ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        return {"ok": True, "path": str(p.relative_to(_JARVIS_ROOT_ABS)),
                "valid": True}
    except SyntaxError as e:
        return {"ok": True, "valid": False,
                "path": str(p.relative_to(_JARVIS_ROOT_ABS)),
                "error_line": e.lineno, "error_msg": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── APPROVAL: 파일 변경 ──────────────────────────────────────

def _verify_py_or_rollback(p: Path, backup_path, new_created: bool) -> Optional[dict]:
    """★ 검증 (2026-07-02): .py 작성 후 ast.parse 검증 → syntax 오류면 자동 롤백.
    (CLAUDE.md '수정 후 syntax 오류 시 자동 rollback' 요구 충족)
    통과·비-py 는 None, 실패면 error dict(롤백 완료)."""
    if p.suffix != ".py":
        return None
    import ast as _ast
    try:
        _ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        return None
    except SyntaxError as e:
        try:
            if backup_path and Path(backup_path).exists():
                p.write_bytes(Path(backup_path).read_bytes())   # 이전 내용 복원
            elif new_created:
                p.unlink(missing_ok=True)                        # 신규 파일 제거
        except Exception:
            pass
        return {"ok": False, "rolled_back": True,
                "error": f"syntax 오류 자동 롤백 (line {e.lineno}): {e.msg}"}


@register_tool(
    name="write_file",
    domain="code",
    side_effect="external",
    rollback="restore_backup",
    cost_class="low",
    requires_approval=True,
    description="파일 생성 또는 덮어쓰기. 기존 파일은 .bak 백업 자동 생성. jarvis-agent 폴더 안만.",
)
def write_file(path: str, content: str) -> dict:
    p = _safe_path(path)
    if p is None:
        return {"ok": False, "error": f"unsafe path: {path}"}
    try:
        # 기존 파일 백업
        backup_path = None
        if p.exists():
            backup_path = p.with_suffix(p.suffix + ".bak")
            backup_path.write_bytes(p.read_bytes())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _v = _verify_py_or_rollback(p, backup_path, new_created=(backup_path is None))
        if _v:
            return _v
        return {
            "ok": True, "path": str(p.relative_to(_JARVIS_ROOT_ABS)),
            "bytes": len(content.encode("utf-8")),
            "backup": str(backup_path.relative_to(_JARVIS_ROOT_ABS)) if backup_path else None,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="edit_file",
    domain="code",
    side_effect="external",
    rollback="restore_backup",
    cost_class="low",
    requires_approval=True,
    description="파일 안 *exact 문자열* 1회 치환. .bak 백업 자동. old_string 이 unique 해야 안전. replace_all=True 면 전체 치환.",
)
def edit_file(path: str, old_string: str, new_string: str,
              replace_all: bool = False) -> dict:
    p = _safe_path(path)
    if p is None or not p.exists():
        return {"ok": False, "error": f"unsafe or not found: {path}"}
    try:
        text = p.read_text(encoding="utf-8")
        cnt = text.count(old_string)
        if cnt == 0:
            return {"ok": False, "error": "old_string not found"}
        if cnt > 1 and not replace_all:
            return {"ok": False, "error": f"old_string appears {cnt} times — use replace_all=True or provide more context"}
        backup_path = p.with_suffix(p.suffix + ".bak")
        backup_path.write_bytes(p.read_bytes())
        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)
        p.write_text(new_text, encoding="utf-8")
        _v = _verify_py_or_rollback(p, backup_path, new_created=False)
        if _v:
            return _v
        return {
            "ok": True, "path": str(p.relative_to(_JARVIS_ROOT_ABS)),
            "replacements": cnt if replace_all else 1,
            "backup": str(backup_path.relative_to(_JARVIS_ROOT_ABS)),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
# Phase 3-B — 셸 도구 (run_bash + 화이트리스트)
# ══════════════════════════════════════════════════════════════
#
# ★ 위험 큼 — 다층 안전:
#   1. APPROVAL 강제 (텔레그램 ✅ 후만 실행)
#   2. 화이트리스트 — 첫 토큰이 허용 명령일 때만
#   3. 위험 패턴 정규식 차단 (rm -rf, sudo, > /, eval, etc.)
#   4. 30초 timeout
#   5. cwd = jarvis-agent 폴더 강제

# 허용 명령 (첫 토큰)
_BASH_WHITELIST: set[str] = {
    "python", "python3", "pytest", "pip",
    "git", "ls", "cat", "head", "tail", "wc", "grep",
    "find", "echo", "pwd",
    "npm", "node",
}

# 차단 패턴 (정규식 — 어디든 등장 시 거부)
_BASH_DENY_PATTERNS: list[str] = [
    r'\brm\s+-rf?\b',        # rm -r/-rf
    r'\bsudo\b',
    r'\bsu\s',
    r'>\s*/',                # 절대 경로로 redirect
    r'>>\s*/',               # 절대 경로로 append
    r'\b(eval|exec)\b',
    r'\bcurl\s+.*\|\s*(sh|bash)',
    r'\bwget\s+.*\|\s*(sh|bash)',
    r'\bchmod\s+777\b',
    r'\bchown\b',
    r'\bdd\s+if=',
    r'\bmkfs\b',
    r'\b/dev/sd[a-z]',
    r'\b:\(\)\{',            # fork bomb
    r'\bkill\s+-9\s+1\b',    # kill init
]


@register_tool(
    name="run_bash",
    domain="code",
    side_effect="external",
    rollback=None,
    cost_class="medium",
    requires_approval=True,
    description="jarvis-agent 폴더에서 bash 명령 실행. 화이트리스트 (python/pytest/git 등) 첫 토큰만. rm -rf·sudo·redirect 차단. 30초 timeout.",
)
def run_bash(command: str, timeout: int = 30) -> dict:
    """안전 박스 안에서 bash 실행.

    Args:
        command: 실행할 명령 (예: "python -c 'import sys; print(sys.version)'", "git diff --stat").
        timeout: 최대 실행 시간 (초, 기본 30, 최대 120).

    Returns: {"ok", "stdout", "stderr", "returncode", "duration"}
    """
    cmd = (command or "").strip()
    if not cmd:
        return {"ok": False, "error": "empty command"}
    if len(cmd) > 2000:
        return {"ok": False, "error": "command too long (>2000 chars)"}

    # 1) 차단 패턴
    for pat in _BASH_DENY_PATTERNS:
        if _re.search(pat, cmd):
            return {"ok": False, "error": f"blocked by deny pattern: {pat}"}

    # 2) 화이트리스트 (첫 토큰)
    first = cmd.split()[0] if cmd.split() else ""
    # 우회 시도 차단 — './script', 'sh ', 'bash ' 등
    if first.startswith(".") or first in {"sh", "bash", "zsh", "csh", "tcsh", "ksh"}:
        return {"ok": False, "error": f"blocked: shell wrapper '{first}'"}
    if first not in _BASH_WHITELIST:
        return {"ok": False,
                "error": f"'{first}' not in whitelist. allowed: {sorted(_BASH_WHITELIST)}"}

    # 3) timeout 제한
    try:
        timeout = max(1, min(int(timeout or 30), 120))
    except (TypeError, ValueError):
        timeout = 30

    # 4) 실행
    import subprocess as _sp
    import time as _time
    # PATH 보강 — daemon subprocess 가 brew·node·git 등 못 찾는 사고 방지
    env = _os.environ.copy()
    extra_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    cur = env.get("PATH", "")
    parts = cur.split(":") if cur else []
    for p in extra_paths:
        if p not in parts:
            parts.insert(0, p)
    env["PATH"] = ":".join(parts)
    t0 = _time.time()
    try:
        proc = _sp.run(
            ["bash", "-c", cmd],
            cwd=str(_JARVIS_ROOT_ABS),
            env=env,
            capture_output=True, text=True,
            timeout=timeout,
        )
        elapsed = _time.time() - t0
        out = (proc.stdout or "")[:8000]
        err = (proc.stderr or "")[:4000]
        return {
            "ok": True,
            "command": cmd,
            "returncode": proc.returncode,
            "stdout": out,
            "stderr": err,
            "duration": round(elapsed, 2),
        }
    except _sp.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s",
                "command": cmd}
    except Exception as e:
        return {"ok": False, "error": str(e), "command": cmd}


# ══════════════════════════════════════════════════════════════
# Phase 3-C — 계획·실행 흐름 (create_plan)
# ══════════════════════════════════════════════════════════════
#
# ★ 안전 패턴: LLM 이 *코드 수정·셸 실행* 같은 큰 작업을 시작하기 전
# 전체 계획을 사용자에게 한 번에 보여주고 ✅/🔍/❌ 받는다.
#
# 흐름:
#   사용자: "X 를 고쳐줘"
#   LLM (read·grep 후): create_plan(...) 호출 → 텔레그램에 단계 N개 표시
#   사용자: ✅ 전체 승인 → 자동 실행 / 🔍 단계별 → 단계별 승인 / ❌ 취소
#
# 보관: 데몬의 _PENDING_J00_PLAN dict 가 보관. 콜백이 plan_id 로 회수.

@register_tool(
    name="create_plan",
    domain="core",
    side_effect="external",
    rollback=None,
    cost_class="free",
    requires_approval=True,
    description="작업 계획 수립 — 사용자 승인 후 단계별 실행. steps: [{'tool': '도구명', 'args': {...}, 'note': '설명'}]. 코드 수정·셸 실행 같은 큰 작업 시작 전 *반드시* 호출.",
)
def create_plan(goal: str, steps: list, single_approval: bool = True) -> dict:
    """계획 수립 → 텔레그램 인라인 버튼 게이트.

    Args:
        goal: 사용자 요청 한 줄 요약.
        steps: 실행 단계 리스트. 각 step = {"tool": str, "args": dict, "note": str}.
               예: [{"tool":"edit_file","args":{"path":"X","old_string":"a","new_string":"b"},"note":"X 파일 a→b 변경"}]
        single_approval: True (전체 한 번 ✅) / False (단계별 ✅ 받음).

    Returns: {"ok", "plan_id", "step_count", "summary"}

    ★ 이 도구 호출 자체가 APPROVAL — 텔레그램에 계획 표시 + 인라인 버튼.
    승인되면 daemon 의 _execute_plan(plan_id) 가 단계별 tool_invoke 실행.
    """
    if not steps or not isinstance(steps, list):
        return {"ok": False, "error": "steps must be non-empty list"}
    # validate steps
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            return {"ok": False, "error": f"step {i}: not a dict"}
        if "tool" not in s:
            return {"ok": False, "error": f"step {i}: missing 'tool'"}
        from shared.tools import get_tool
        if get_tool(s["tool"]) is None:
            return {"ok": False, "error": f"step {i}: unknown tool '{s['tool']}'"}
    # plan_id 만 반환 — 실제 텔레그램 송출·실행은 daemon 의 plan 핸들러가 처리
    import uuid as _uuid
    plan_id = "plan:" + _uuid.uuid4().hex[:8]
    summary = "\n".join([f"  {i+1}. [{s['tool']}] {s.get('note','') or s['tool']}"
                          for i, s in enumerate(steps)])
    return {
        "ok": True,
        "plan_id": plan_id,
        "step_count": len(steps),
        "summary": summary,
        "goal": goal,
        "single_approval": bool(single_approval),
        # _execute_plan 가 회수할 데이터 (LLM 응답에 포함되어 daemon 이 캐치)
        "_plan_data": {
            "plan_id": plan_id, "goal": goal,
            "steps": steps, "single_approval": bool(single_approval),
        },
    }


# ══════════════════════════════════════════════════════════════
# Phase 3-E — Claude Code SDK 위임 브릿지 (★ 2026-06-06 표기 통일: CLI → SDK)
# ══════════════════════════════════════════════════════════════
#
@register_tool(
    name="delegate_to_claude_code",
    domain="core",
    side_effect="external",
    rollback=None,
    cost_class="high",
    requires_approval=True,
    description="복잡한 작업을 Claude Code SDK 에 위임. jarvis-agent 폴더에서 실행. 10분 timeout. allowed_tools 로 권한 좁힘 (기본: Read·Glob·Grep — 읽기 전용). 코드 수정·셸 실행은 명시적 화이트리스트 추가 필요.",
)
def delegate_to_claude_code(prompt: str,
                             allowed_tools: Optional[str] = None,
                             max_turns: int = 20,
                             timeout: int = 600) -> dict:
    """Claude Code SDK 호출.

    Args:
        prompt: Claude Code 에 보낼 명령 (자유 문장).
        allowed_tools: 공백/쉼표 구분 화이트리스트 (예: 'Read Glob Grep').
                       None=기본 (Read·Glob·Grep 만). Write·Bash 허용 시 사용자가 명시.
        max_turns: 최대 도구 호출 라운드 (기본 20).
        timeout: 초 단위 (기본 600=10분, 최대 1800=30분).

    Returns: {"ok", "returncode", "stdout", "stderr", "duration"}
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "empty prompt"}
    if len(prompt) > 8000:
        return {"ok": False, "error": "prompt too long (>8000 chars)"}

    tools_list = (allowed_tools or "Read Glob Grep").replace(",", " ").split()

    try:
        timeout = max(60, min(int(timeout or 600), 1800))
        max_turns = max(1, min(int(max_turns or 20), 50))
    except (TypeError, ValueError):
        timeout, max_turns = 600, 20

    import anyio, time as _time
    from claude_code_sdk import query, ClaudeCodeOptions, AssistantMessage, TextBlock
    from claude_code_sdk._errors import CLINotFoundError

    run_env = dict(_os.environ)
    run_env["PATH"] = ":".join(["/opt/homebrew/bin", "/usr/local/bin"]) + ":" + run_env.get("PATH", "")

    t0 = _time.time()

    async def _run_sdk() -> dict:
        try:
            with anyio.fail_after(timeout):
                options = ClaudeCodeOptions(
                    model="claude-sonnet-5",   # ★ 수정 가능 위임 도구 — Sonnet 5 단일 통일 (사용자 박제 2026-07-06, ADR 017)
                    max_turns=max_turns,
                    allowed_tools=tools_list,
                    cwd=str(_JARVIS_ROOT_ABS),
                    env=run_env,
                )
                parts: list[str] = []
                async for msg in query(prompt=prompt, options=options):
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                parts.append(block.text)
                out = "".join(parts)[:30000]
                return {"ok": True, "returncode": 0, "stdout": out, "stderr": "",
                        "duration": round(_time.time() - t0, 1), "allowed_tools": " ".join(tools_list)}
        except TimeoutError:
            return {"ok": False, "error": f"timeout after {timeout}s"}

    try:
        return anyio.run(_run_sdk)
    except CLINotFoundError as e:
        return {"ok": False, "error": f"Claude Code SDK 런타임 없음: {e}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
# Phase 3-F — 자비스00 자체 강화 (web_fetch + ask_claude)
# ══════════════════════════════════════════════════════════════
#
# ★ 옵션 B — Claude Code SDK 위임 외에 자비스 자체 능력 보강 (표기 통일 2026-06-06).
# - web_fetch: 외부 URL 페이지 가져와 텍스트 변환 (조사·분석용)
# - ask_claude: Sonnet 직접 호출 (긴 글·복잡 추론용 — 도구 호출 없음)


@register_tool(
    name="web_fetch",
    domain="core",
    side_effect="none",
    cost_class="low",
    requires_approval=False,
    description="외부 URL 의 HTML 가져와 텍스트로 변환. 조사·분석용 (LLM 에 주입 후 요약 가능). 15초 timeout, 본문 30KB 제한.",
)
def web_fetch(url: str) -> dict:
    """URL → text 변환.

    Args:
        url: 가져올 페이지 URL (http/https).

    Returns: {"ok", "url", "title", "text", "bytes"}
    """
    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return {"ok": False, "error": "url must start with http:// or https://"}
    try:
        import requests as _req
        r = _req.get(url, timeout=15,
                     headers={"User-Agent": "Mozilla/5.0 (JARVIS web_fetch)"})
    except Exception as e:
        return {"ok": False, "error": f"fetch failed: {e}"}
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}", "url": url}
    try:
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            html = r.text
            # 간단 HTML → text (BeautifulSoup 가용 시 더 정확)
            try:
                from bs4 import BeautifulSoup as _BS
                soup = _BS(html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                title = (soup.title.string if soup.title else "")
                text = soup.get_text(separator="\n", strip=True)
            except ImportError:
                # fallback — 정규식 단순 변환
                text = _re.sub(r"<script[^>]*>.*?</script>", "", html,
                               flags=_re.S | _re.I)
                text = _re.sub(r"<style[^>]*>.*?</style>", "", text,
                               flags=_re.S | _re.I)
                text = _re.sub(r"<[^>]+>", " ", text)
                text = _re.sub(r"\s+", " ", text).strip()
                title = ""
        else:
            title = ""
            text = r.text
        return {
            "ok": True,
            "url": url,
            "title": (title or "")[:200],
            "text": text[:30000],
            "bytes": len(r.content),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@register_tool(
    name="ask_claude",
    domain="core",
    side_effect="none",
    cost_class="low",
    requires_approval=False,
    description="Sonnet 모델에 직접 질의 (도구 호출 X, 텍스트 답변만). 추론·창작·요약·번역 등에 사용.",
)
def ask_claude(prompt: str, system: str = "",
               max_tokens: int = 3000) -> dict:
    """Sonnet 텍스트 답변.

    Args:
        prompt: 사용자 질의.
        system: system message (옵션 — 역할·톤 지정).
        max_tokens: 최대 응답 길이 (기본 3000).
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "error": "empty prompt"}
    try:
        from shared.llm import invoke_text
        text = invoke_text("writer", prompt,
                           max_tokens=int(max_tokens),
                           **({"system": system} if system else {}))
        return {"ok": True, "text": text or "", "model": "claude-sonnet-5"}
    except Exception as e:
        # fallback — invoke_text 직접 호출
        try:
            from shared.llm import invoke_text as _inv_cli
            _full = f"{system}\n\n{prompt}".strip() if system else prompt
            text2 = _inv_cli("writer", _full, timeout=300)
            return {"ok": True, "text": text2 or "", "model": "claude-sonnet-5"}
        except Exception as e2:
            return {"ok": False, "error": f"{e}; fallback: {e2}"}


# ══════════════════════════════════════════════════════════════
# Phase 3-D — 자기 등록 (새 잡·새 에이전트 자율 추가)
# ══════════════════════════════════════════════════════════════
#
# ★ 위험 큼 — 시스템 구조 자체를 자비스가 변경. 안전망:
#   1. 모두 APPROVAL — 사용자 ✅ 후만 실행
#   2. 새 에이전트는 *템플릿* 기반 — 사용자가 코드 직접 안 짬
#   3. 충돌 검사 (이미 있는 ID 거부)
#   4. 데몬 재시작 필요 안내 (자동 재시작 없음 — 사용자가 결정)


@register_tool(
    name="register_new_job",
    domain="schedule",
    side_effect="external",
    rollback=None,
    cost_class="low",
    requires_approval=True,
    description="JARVIS04 DEFAULT_JOBS 에 새 cron 잡 추가 (영구). callback='module.func' 형식. 데몬 재시작 후 적용.",
)
def register_new_job(job_id: str, name: str, trigger: str,
                     trigger_kwargs: dict, callback: str,
                     owner: str = "user",
                     misfire_grace_time: int = 600) -> dict:
    """JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 에 dict 추가.

    Args:
        job_id: 고유 ID (예: 'user_morning_brief').
        name: 표시명 (예: '아침 브리핑').
        trigger: 'cron' | 'interval'.
        trigger_kwargs: 예 {"hour":8, "minute":0} (cron) / {"minutes":30} (interval).
        callback: 'jarvis2_scheduler.run_economic_poster' 같은 lazy import path.
        owner: 'user' / 'jarvis02_writer' 등.

    Returns: {"ok", "job_id", "note"}
    """
    if trigger not in {"cron", "interval"}:
        return {"ok": False, "error": "trigger must be 'cron' or 'interval'"}
    if not isinstance(trigger_kwargs, dict):
        return {"ok": False, "error": "trigger_kwargs must be dict"}
    # callback 형식 검증
    if "." not in callback:
        return {"ok": False, "error": "callback must be 'module.func'"}

    # job_registry.py 의 DEFAULT_JOBS 끝에 dict 추가
    p = _safe_path("JARVIS04_SCHEDULER/job_registry.py")
    if p is None:
        return {"ok": False, "error": "job_registry.py path unsafe"}
    text = p.read_text(encoding="utf-8")
    # 중복 검사
    if f'"id":"{job_id}"' in text or f"'id':'{job_id}'" in text:
        return {"ok": False, "error": f"job_id '{job_id}' already exists"}
    # DEFAULT_JOBS 닫는 ]\n\n 직전에 삽입 — pattern: "    {... infra}, \n]"
    closing = "\n]\n\n\ndef _resolve_callback"
    if closing not in text:
        return {"ok": False, "error": "DEFAULT_JOBS 닫는 패턴 미발견 — 수동 수정 필요"}
    # kwargs 를 그대로 repr — dict 라 자동 처리
    new_entry = (
        f'    # ── 사용자 동적 등록 (register_new_job) ──\n'
        f'    {{"id":"{job_id}", "name":"{name}", "trigger":"{trigger}",\n'
        f'     "kwargs":{trigger_kwargs!r}, "callback":"{callback}",\n'
        f'     "misfire_grace_time":{int(misfire_grace_time)}, "owner":"{owner}"}},\n'
    )
    backup_path = p.with_suffix(p.suffix + ".bak")
    backup_path.write_bytes(p.read_bytes())
    new_text = text.replace(closing, new_entry + closing)
    p.write_text(new_text, encoding="utf-8")
    # syntax 검증
    import ast as _ast
    try:
        _ast.parse(new_text)
    except SyntaxError as e:
        # rollback
        p.write_bytes(backup_path.read_bytes())
        return {"ok": False, "error": f"syntax error after insert: {e}",
                "rolled_back": True}
    return {
        "ok": True, "job_id": job_id,
        "backup": str(backup_path.relative_to(_JARVIS_ROOT_ABS)),
        "note": "DEFAULT_JOBS 에 추가됨. 데몬 재시작 후 적용.",
    }


@register_tool(
    name="register_new_intent",
    domain="core",
    side_effect="external",
    rollback=None,
    cost_class="low",
    requires_approval=True,
    description="dispatchers.py SAFE_INTENTS 또는 APPROVAL_INTENTS 에 새 intent 추가. 별도 처리 함수는 사용자가 직접 작성.",
)
def register_new_intent(intent: str, mode: str = "SAFE",
                        capability_owner: str = "jarvis01_master") -> dict:
    """새 intent 를 dispatcher 에 등록.

    Args:
        intent: dot-naming (예: 'memo.quick.add').
        mode: 'SAFE' | 'APPROVAL'.
        capability_owner: capability 선언할 에이전트 ID (참고 정보).

    ★ 처리 함수 (`execute_safe` 또는 APPROVAL 콜백) 는 *별도로* 작성 필요.
    이 도구는 라우팅 등록만.
    """
    if mode not in {"SAFE", "APPROVAL"}:
        return {"ok": False, "error": "mode must be SAFE or APPROVAL"}
    if "." not in intent:
        return {"ok": False, "error": "intent must be dot-naming"}
    p = _safe_path("JARVIS01_MASTER/dispatchers.py")
    if p is None:
        return {"ok": False, "error": "dispatchers.py path unsafe"}
    text = p.read_text(encoding="utf-8")
    if f'"{intent}"' in text or f"'{intent}'" in text:
        return {"ok": False, "error": f"intent '{intent}' already exists"}
    set_name = "SAFE_INTENTS" if mode == "SAFE" else "APPROVAL_INTENTS"
    # 닫는 } 직전 줄 삽입
    import re as _re
    pat = _re.compile(rf'({set_name}: set\[str\] = \{{[^}}]*?)(\n\}})', _re.S)
    m = pat.search(text)
    if not m:
        return {"ok": False, "error": f"{set_name} 패턴 미발견"}
    new_line = f'    "{intent}",  # register_new_intent (owner={capability_owner})'
    new_text = text[:m.end(1)] + "\n" + new_line + text[m.end(1):]
    backup_path = p.with_suffix(p.suffix + ".bak")
    backup_path.write_bytes(p.read_bytes())
    p.write_text(new_text, encoding="utf-8")
    import ast as _ast
    try:
        _ast.parse(new_text)
    except SyntaxError as e:
        p.write_bytes(backup_path.read_bytes())
        return {"ok": False, "error": f"syntax error: {e}", "rolled_back": True}
    return {
        "ok": True, "intent": intent, "mode": mode,
        "backup": str(backup_path.relative_to(_JARVIS_ROOT_ABS)),
        "note": f"{set_name} 에 추가됨. 처리 함수 (execute_safe / _APPROVAL_META) 는 별도 작성 필요.",
    }


@register_tool(
    name="create_new_agent",
    domain="core",
    side_effect="external",
    rollback=None,
    cost_class="medium",
    requires_approval=True,
    description="새 에이전트 폴더 생성 + 템플릿 파일 (CLAUDE.md, *_agent.py, __init__.py) 박제. agent_id 는 'jarvisNN_name' 형식.",
)
def create_new_agent(agent_id: str, domain: str, description: str = "",
                     intents: list = None) -> dict:
    """새 에이전트 폴더 + 템플릿 생성.

    Args:
        agent_id: 'jarvis04_finance' 같은 snake_case.
        domain: 'finance' / 'memo' / 'schedule' 등.
        description: 에이전트 역할 (capability declare 에 박힘).
        intents: 초기 intent 리스트 (옵션).

    Returns: {"ok", "folder", "files"}
    """
    import re as _re
    if not _re.match(r'^jarvis\d{2}_[a-z_]+$', agent_id):
        return {"ok": False, "error": "agent_id must match 'jarvisNN_name' (lowercase)"}
    # 표준화: jarvis04_finance → JARVIS04_FINANCE
    parts = agent_id.split("_", 1)
    folder_name = parts[0].upper() + "_" + parts[1].upper()
    folder = _JARVIS_ROOT_ABS / folder_name
    if folder.exists():
        return {"ok": False, "error": f"folder already exists: {folder_name}"}
    folder.mkdir(parents=True)

    intents_list = intents or [f"{domain}.unknown"]
    intents_repr = ",\n        ".join(f'"{i}"' for i in intents_list)
    agent_filename = parts[1] + "_agent.py"

    # __init__.py
    (folder / "__init__.py").write_text(
        f'"""\n{folder_name} — {description or domain + " 에이전트"}.\n"""\n',
        encoding="utf-8",
    )
    # CLAUDE.md
    (folder / "CLAUDE.md").write_text(
        f"# {folder_name}\n\n"
        f"## 역할\n{description or '(설명 미지정)'}\n\n"
        f"## 비직관적 파일 역할\n- `{agent_filename}` — 진입점. capability + register(scheduler, bus).\n\n"
        f"## 비직관적 규칙\n| 항목 | 규칙 |\n|------|------|\n| 잡 등록 | JARVIS04_SCHEDULER 통합 |\n| 새 도구 | shared.tools.register_tool 사용 |\n",
        encoding="utf-8",
    )
    # *_agent.py 템플릿
    agent_code = f'''"""{folder_name}/{agent_filename} — capability 선언 + (선택) register().

데몬 부팅 시 _autoregister_agents 가 자동 import.
잡 등록은 JARVIS04_SCHEDULER/job_registry.DEFAULT_JOBS 통합 권장.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.capabilities import declare


CAPABILITIES = declare(
    agent_id="{agent_id}",
    domain="{domain}",
    intents=[
        {intents_repr},
    ],
    tools=[],
    requires_approval=[],
    cost_class="low",
    description="{description}",
    tags=["{domain}"],
)


def register(scheduler, bus):
    """데몬 부팅 시 자동 호출. 잡은 JARVIS04 통합 관리."""
    pass
'''
    (folder / agent_filename).write_text(agent_code, encoding="utf-8")
    return {
        "ok": True,
        "folder": folder_name,
        "files": [
            f"{folder_name}/__init__.py",
            f"{folder_name}/CLAUDE.md",
            f"{folder_name}/{agent_filename}",
        ],
        "note": "데몬 재시작 후 자동등록. dispatchers.py / intents.py 갱신은 register_new_intent 사용.",
    }


# ══════════════════════════════════════════════════════════════
# Phase 4 — ARCHITECT (JARVIS00_INFRA 위임 — 설계타임 메타)
# ══════════════════════════════════════════════════════════════

@register_tool(
    name="design_new_agent",
    domain="meta",
    side_effect="none",
    cost_class="medium",
    requires_approval=False,
    description=(
        "새 에이전트·도구·잡·skill 신설 *기획서* 산출. "
        "사용자 자유 문장 → 표준 양식 12 섹션 마크다운 + 구현 계획서. "
        "★ 설계만 함, 실행 0. 코드 변경은 산출 후 create_plan 위임 + 인라인 버튼 ✅. "
        "JARVIS00_INFRA.architect 단일 진입점."
    ),
)
def design_new_agent(user_intent: str, scope: str = "agent",
                     output_path: Optional[str] = None) -> dict:
    """ARCHITECT 위임 — JARVIS00_INFRA.architect.design_new_agent.

    Args:
        user_intent: 사용자 자유 문장 (예: "가계부 자동화 에이전트 만들고 싶어").
        scope: "agent" 만 v1. "tool"/"job"/"skill" 은 v2+ 예정.
        output_path: 기획서 저장 경로. 미지정 시 docs/architect/{date}_{slug}.md.

    Returns:
        {
            "ok": bool,
            "spec_path": str,           # 산출 마크다운 절대 경로
            "summary": str,             # 텔레그램 송출용 요약
            "verdict": str,             # agent|skill|tool|job|unnecessary
            "warnings": [str],          # anti-pattern 경고
            "errors_risk": [...],       # ERRORS [27]~[32] 재현 위험
            "next_plan_steps": [dict],  # create_plan 인자 형태
        }
    """
    try:
        from JARVIS00_INFRA.architect import design_new_agent as _impl
    except Exception as e:
        return {"ok": False, "error": f"architect 모듈 로드 실패: {e}"}
    return _impl(user_intent=user_intent, scope=scope, output_path=output_path)


# ══════════════════════════════════════════════════════════════
# 등록 트리거 — 모듈 import 만으로 도구가 _TOOLS 레지스트리에 박힘
# ══════════════════════════════════════════════════════════════

def ensure_loaded() -> list[str]:
    """tools 레지스트리에 본 모듈 도구가 등록되었는지 확인 + 이름 반환.

    데몬 부팅 시 검증용. router.py 의 ReAct 노드가 시작 전 호출하여
    bind_tools 대상 보장.
    """
    from shared.tools import all_tools

    names = {t.name for t in all_tools()}
    expected = {
        # 기본 5개
        "list_capabilities", "get_recent_events", "query_post_analysis",
        "call_jarvis01", "call_jarvis02",
        # Phase 3-A 파일 도구 6개
        "read_file", "glob_files", "grep_code", "syntax_check",
        "write_file", "edit_file",
        # Phase 3-B 셸 도구 1개
        "run_bash",
        # Phase 3-C 계획 도구
        "create_plan",
        # Phase 3-D 자기 등록 도구
        "register_new_job", "register_new_intent", "create_new_agent",
        # Phase 3-E 옵션 A — Claude Code SDK 위임 (표기 통일 2026-06-06)
        "delegate_to_claude_code",
        # Phase 3-F 옵션 B — 자비스 자체 강화
        "web_fetch", "ask_claude",
        # Phase 4 — ARCHITECT (설계타임 메타, JARVIS00_INFRA 위임)
        "design_new_agent",
    }
    missing = expected - names
    if missing:
        # 등록 누락 (import 실패 등) — 명시 ❌ 경고 + 누락 도구명 출력
        print(f"  ❌ agent_tools 등록 누락 ({len(missing)}개): {sorted(missing)} "
              f"— 해당 @register_tool 데코레이터 import 실패 또는 시그니처 오류 점검")
    return sorted(expected & names)


__all__ = [
    "list_capabilities",
    "get_recent_events",
    "query_post_analysis",
    "call_jarvis01",
    "call_jarvis02",
    "design_new_agent",
    "ensure_loaded",
]
