"""JARVIS07_GUARDIAN/error_fixer.py — 오류 자동 수정기.

흐름:
  1. 안전 검증 (경로 탈출 방지 / 줄 수 / ast.parse)
  1-B. ★ code-removal 가드 — "지워서 통과시키는" 패치 거부
  2. .bak 백업
  3. 파일 적용
  4. import 검증
  5. ★ 원 오류 재현 검증 (reproduced_gone / unverifiable / still_reproduces)
  6. 실패·재현 시 .bak 롤백
  7. DB 상태 업데이트 + ERRORS.md 기록 + 밴딧 *양방향* 보상

★ 자동 승인 — Telegram 버튼 없음. 검증 통과 시 즉시 적용.

★★ 재현 검증 (사용자 박제 2026-07-25) — "ast.parse + import 성공 = fixed" 폐기
  종전 판정은 *패치가 파일을 깨뜨리지 않았다* 는 사실만 확인했지 *원래 오류가 사라졌는지*
  는 한 번도 묻지 않았다. 그래서 `x[:N]` → `(x or "")[:N]` 처럼 **증상만 덮는 패치**가
  "fixed" 로 기록되고 밴딧에 *양의 보상* 까지 받아, 시스템이 **증상 은폐를 강화학습**했다.
  (APR 문헌: patch overfitting 92~98%, 최악 실패 모드가 code-removal patch.
   GitHub agentic autofix 는 CodeQL 재실행으로 경보가 닫혀야 PR 을 연다 / Meta SapFix 는
   Sapienz 를 재실행해 검증한다.) → **원 실패를 재현해보지 않은 fixed 는 fixed 가 아니라
  plausible 이다.**

  계약 (eval_agent·bandit 이 소비하는 문자열 — 변경 금지):
    "reproduced_gone"  = 원 오류 재현을 시도했고 더는 재현되지 않음 → 양의 보상
    "unverifiable"     = 재현 시도 자체가 불가능한 유형            → **보상 호출 안 함(0)**
    "still_reproduces" = 여전히 재현됨 = 수정 실패                → 롤백 + 음의 보상

  킬스위치: `GUARDIAN_FIX_VERIFY=0` → 종전 동작(구문·import 만으로 fixed + 양의 보상).
"""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("jarvis.guardian.fixer")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 수정 금지 디렉터리
_DENY_DIRS = {".venv", ".git", "__pycache__", "shared/backups", "chrome_profile", "logs"}
# 수정 허용 확장자
_ALLOW_EXT = {".py", ".sh", ".md"}
# ★ ERRORS [137] 사용자 박제 2026-05-17 — 기록·박제 파일 *절대 수정 금지* 리스트.
# GUARDIAN auto_repair 가 *기록 파일을 수정 대상으로 인식* 하여 *덮어쓰기 사고* 발견.
# 기록 파일은 *읽기 전용* — append 만 허용 (그것도 error_collector API 통해서만).
_DENY_FILES = {
    "JARVIS07_GUARDIAN/ERRORS.md",       # 오류 이력 — append only
    "MEMORY.md",                          # 메모리 인덱스
    "docs/decisions/README.md",           # ADR 인덱스
    "CLAUDE.md",                          # 헌법 — 사용자 박제만 허용
    "JARVIS02_WRITER/BLOG_SUPREME_LAW.md",  # 블로그 헌법
    "JARVIS07_GUARDIAN/project_audit_log.json",  # 감사 기록
    "JARVIS07_GUARDIAN/learned_patterns.json",   # 학습 자산 (별도 API 만)
}


def _normalize_target(raw: str) -> str:
    """★ P3 패치 (사용자 박제 2026-05-18 — ERRORS [149] 후속).

    LLM analyzer 응답의 target_file 추출 시 발생하는 *마크다운·module path·헛소리* 정제.

    실 사례 (ERRORS [149] #301~#307):
      - `JARVIS00_INFRA.preflight.external_import`  → module path 형식, file 아님
      - `` `JARVIS02_WRITER/collect_theme.py` ``    → 백틱 둘러쌈, suffix 가 `.py``
      - `none`                                       → "수정 불필요" 자연어 응답
      - "** `JARVIS00_INFRA/harness.py`** "       → 마크다운 볼드 + 백틱
      - `requirements.txt` (신규 생성 권장)          → 괄호 후행 텍스트

    정규화 규칙 (실패 시 빈 문자열 반환 — 호출자가 _safe_path에서 None 처리):
      ① 백틱·따옴표·볼드 마크다운 제거
      ② "none"·"None"·"unknown"·빈 문자열 → "" (수정 대상 없음)
      ③ 괄호로 시작하는 후행 텍스트 잘라냄
      ④ module path (dot 만 있고 슬래시 없음 + .py 안 끝남) → 슬래시 변환 시도
      ⑤ 경로 안의 공백·줄바꿈 제거
    """
    if not raw:
        return ""
    s = str(raw).strip()
    # ① 마크다운 정제 — 백틱·볼드·이탤릭
    s = s.strip("*` \t\n\r'\"")
    s = s.replace("`", "").replace("**", "").replace("*", "")
    s = s.strip()
    # ② 자연어 "수정 불필요" 응답
    if not s or s.lower() in ("none", "null", "n/a", "na", "unknown", "-"):
        return ""
    if "수정 불필요" in s or "코드 수정 불필요" in s or "신규 생성" in s:
        # 후행 자연어 절단 시도 — 괄호 또는 한글 시작 부분 자르기
        for stop in ("(", "（", " — ", " - ", "[", "{"):
            if stop in s:
                s = s.split(stop)[0].strip()
                break
        # 그래도 자연어면 빈 문자열
        if not s or s.lower() in ("none", "null", "n/a"):
            return ""
    # ③ 괄호 후행 텍스트 절단 — "foo.py (신규 ...)" → "foo.py"
    for stop in (" (", " （", " — ", " - ", " [", " {"):
        if stop in s:
            s = s.split(stop)[0].strip()
            break
    # ④ module path 휴리스틱 — 슬래시 없음 + 점 여러개 + .py 안 끝남
    if "/" not in s and "\\" not in s and s.count(".") >= 2 and not s.endswith(".py"):
        # 예: "JARVIS00_INFRA.preflight.external_import" → file path 변환 불가능
        # external_import 같은 *함수/카테고리 이름* 이 마지막에 붙는 경우가 많음.
        # 안전하게 빈 문자열 반환 (수정 skip).
        return ""
    # ⑤ 공백·줄바꿈 제거
    s = s.replace("\n", "").replace("\r", "").strip()
    return s


def _safe_path(target: str) -> Path | None:
    """수정 대상 경로 안전 검증. 실패 시 None.

    ★ ERRORS [137] — _DENY_FILES 보강: 기록·박제 파일 절대 수정 금지.
    ★ ERRORS [149] 후속 — target 정규화 (백틱·module path·자연어 잡음 제거) 선행.
    """
    target = _normalize_target(target)
    if not target:
        log.info("[GUARDIAN] target 정규화 후 빈 문자열 — 수정 skip")
        return None
    try:
        p = (_ROOT / target).resolve()
        # 루트 탈출 방지
        p.relative_to(_ROOT)
        # 금지 디렉터리 차단
        for deny in _DENY_DIRS:
            if deny in str(p):
                log.warning(f"[GUARDIAN] 금지 경로: {p}")
                return None
        # ★ 금지 파일 차단 (ERRORS.md 덮어쓰기 사고 재발 방지)
        try:
            rel = str(p.relative_to(_ROOT))
            if rel in _DENY_FILES or any(rel.endswith("/" + d) or rel == d for d in _DENY_FILES):
                log.warning(f"[GUARDIAN] 금지 파일 (기록·박제): {rel}")
                return None
        except Exception:
            pass
        # 확장자 체크
        if p.suffix not in _ALLOW_EXT:
            log.warning(f"[GUARDIAN] 비허용 확장자: {p.suffix}")
            return None
        return p
    except (ValueError, Exception) as e:
        log.warning(f"[GUARDIAN] 경로 검증 실패: {e}")
        return None


def _validate_python(content: str) -> bool:
    """Python 구문 검증."""
    try:
        ast.parse(content)
        return True
    except SyntaxError as e:
        log.warning(f"[GUARDIAN] 구문 오류: {e}")
        return False


def _backup(file_path: Path) -> Path | None:
    """.bak 백업 생성. 성공 시 백업 경로 반환."""
    bak = file_path.with_suffix(file_path.suffix + ".bak")
    try:
        shutil.copy2(file_path, bak)
        return bak
    except Exception as e:
        log.error(f"[GUARDIAN] 백업 실패: {e}")
        return None


def _rollback(file_path: Path, bak_path: Path):
    """백업에서 원복."""
    try:
        shutil.copy2(bak_path, file_path)
        log.info(f"[GUARDIAN] 롤백 완료: {file_path.name}")
    except Exception as e:
        log.error(f"[GUARDIAN] 롤백 실패: {e}")


def _import_check(file_path: Path) -> bool:
    """수정 후 import 테스트. Python 파일만."""
    if file_path.suffix != ".py":
        return True
    try:
        rel = file_path.relative_to(_ROOT)
        module_str = str(rel).replace("/", ".").replace("\\", ".")[:-3]
        import importlib
        spec = importlib.util.spec_from_file_location(module_str, str(file_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return True
    except Exception as e:
        log.warning(f"[GUARDIAN] import 테스트 실패: {e}")
        return False


def _update_errors_md(error_record: dict, analysis: dict, success: bool,
                      verified: list | None = None):
    """ERRORS.md에 오류 기록 추가 (기존 규정 준수).

    ★ 검증·결과 필수 (사용자 박제 2026-07-23): "무엇을 고쳤나" 만 적으면
      *고친 뒤 무엇이 정상으로 바뀌었는지* 를 아무도 답할 수 없다. 자동 수리도
      수동 수리와 **같은 서식** 을 남긴다 (③ 모든 경로 동일 적용).
    """
    try:
        errors_md = Path(__file__).parent / "ERRORS.md"
        if not errors_md.exists():
            return
        from datetime import datetime
        from JARVIS07_GUARDIAN.severity import format_error_label
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        status_icon = "✅ 자동수정" if success else "❌ 자동수정실패"
        checks = ", ".join(verified or []) or ("검증 기록 없음" if success else "적용 전 검증에서 차단")
        outcome = (
            f"수정본이 적용된 상태로 동작 중 — 같은 증상 재발 여부는 이후 발생 기록으로 판정"
            if success else
            "원본으로 되돌림 — 시스템은 수정 전 상태 그대로 동작 (수동 검토 필요)"
        )
        entry = (
            f"\n---\n"
            f"### [{now_str}] {status_icon} — {format_error_label(error_record.get('error_type',''))}\n"
            f"- **증상**: {error_record.get('message','')[:200]}\n"
            f"- **모듈**: {error_record.get('module','')}\n"
            f"- **원인**: {analysis.get('explanation','')}\n"
            f"- **파일**: {analysis.get('target_file','')}\n"
            f"- **해결**: {'자동 수정 적용' if success else '자동 수정 실패 — 수동 검토 필요'}\n"
            f"- **검증**: {checks}\n"
            f"- **결과**: {outcome}\n"
        )
        with open(errors_md, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        log.warning(f"[GUARDIAN] ERRORS.md 업데이트 실패: {e}")


# ══════════════════════════════════════════════════════════════════
# ★ 원 오류 재현 검증 (사용자 박제 2026-07-25)
# ══════════════════════════════════════════════════════════════════

# 계약 문자열 — error_fixer 가 *생산*, eval_agent·bandit 이 *소비*. 손으로 재정의 금지.
VERIFY_GONE        = "reproduced_gone"
VERIFY_UNVERIFIABLE = "unverifiable"
VERIFY_REPRODUCES  = "still_reproduces"


def _verify_enabled() -> bool:
    """킬스위치 — 런타임 조회(모듈 로드 시점 고정 금지: 데몬 무재시작 토글 가능)."""
    return os.getenv("GUARDIAN_FIX_VERIFY", "1") != "0"


def _verify_timeout() -> float:
    try:
        return max(3.0, float(os.getenv("GUARDIAN_FIX_VERIFY_TIMEOUT", "25")))
    except Exception:
        return 25.0


# ★ code-removal 가드 임계값 — *상수 하나* (②). 무배포 조정: GUARDIAN_FIX_MAX_SHRINK
_MAX_SHRINK_RATIO_DEFAULT = 0.30


def _max_shrink_ratio() -> float:
    try:
        return max(0.0, min(1.0, float(os.getenv("GUARDIAN_FIX_MAX_SHRINK",
                                                 str(_MAX_SHRINK_RATIO_DEFAULT)))))
    except Exception:
        return _MAX_SHRINK_RATIO_DEFAULT


def _meaningful_lines(text: str) -> list[str]:
    """공백·주석만인 줄 제외 — '지운 양' 판정의 분모."""
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


# ── ★ P3 (사용자 박제 2026-07-25) — "정당한 정리" 와 "기능 제거" 를 구분한다 ────
#
#   종전 규칙② (`removed and not added` → 무조건 거부) 는 너무 넓었다. CLAUDE.md 가
#   auto_repair 의 *명시적 대상* 으로 못박은 **중복 함수 제거 / dead code 제거** 가 정확히
#   이 규칙에 걸려, GUARDIAN 이 자기 헌법이 시키는 정리를 스스로 못 하게 만들었다.
#   게다가 거부하면서 밴딧에 음의 보상까지 줘 *맞는 수정을 낸 arm* 을 깎았다.
#
#   구분 기준을 **하드코딩 목록이 아니라 판별 가능한 성질** 로 둔다 (②동적 설계):
#     · 살아있는 스코프의 *실행 문장* 이 사라졌는가            → 기능 제거 (거부)
#     · 사라진 def/class 이름이 패치 후에도 *참조* 되는가       → 기능 제거 (거부)
#     · 사라진 import 가 패치 후에도 *사용* 되는가              → 기능 제거 (거부)
#     · 위 어디에도 해당 없음 = 중복 def·미참조 def·미사용 import·주석/문서열 뿐
#                                                            → 정당한 정리 (통과)
#   AST 파싱 실패 시엔 판정하지 않고 *종전대로 거부* (fail-closed).
#   킬스위치: `GUARDIAN_FIX_ALLOW_CLEANUP=0` → 종전 동작(순수 삭제 전면 거부).

def _cleanup_allowed() -> bool:
    """P3 전용 킬스위치 — 런타임 조회(모듈 로드 시점 캡처 금지)."""
    return os.getenv("GUARDIAN_FIX_ALLOW_CLEANUP", "1") != "0"


def _stmt_key(node: ast.AST) -> str:
    """문장 1개의 동일성 키. def/class·import 는 *이름만* 남겨 따로 판정한다."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return f"DEF:{node.name}"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = ",".join(sorted((a.asname or a.name.split(".")[0]) for a in node.names))
        return f"IMP:{names}"
    if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
            and isinstance(node.value.value, str):
        return "DOC"                      # docstring — 주석성, 삭제돼도 기능 아님
    try:
        return "STMT:" + ast.dump(node, annotate_fields=False)
    except Exception:                     # noqa: BLE001
        return "STMT:?"


def _scope_bodies(tree: ast.AST) -> dict[str, "Counter"]:
    """스코프(qualname) → 그 스코프 *직속* 문장 키 Counter.

    def/class 는 자식 스코프로 따로 수집하고 부모에는 `DEF:<name>` placeholder 만 남긴다
    → *중복 정의 제거*(같은 이름 2개 → 1개)는 이름 집합이 안 변하므로 자연히 정당 판정된다.
    """
    from collections import Counter
    out: dict[str, Counter] = {}

    def rec(node: ast.AST, path: str) -> None:
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            return
        out.setdefault(path, Counter())
        for st in body:
            out[path][_stmt_key(st)] += 1
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rec(st, f"{path}.{st.name}" if path else st.name)

    rec(tree, "")
    return out


def _defined_names(tree: ast.AST) -> set[str]:
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def _referenced_names(tree: ast.AST) -> set[str]:
    """패치 *후* 코드가 여전히 쓰고 있는 이름 — Load 참조 + 속성 베이스 + `__all__` 문자열."""
    refs: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            refs.add(n.id)
        elif isinstance(n, ast.Attribute):
            base = n
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name):
                refs.add(base.id)
        elif isinstance(n, ast.Assign):
            # `__all__ = [...]` 의 문자열도 *공개 계약 참조* 로 본다
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in n.targets):
                for c in ast.walk(n.value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        refs.add(c.value)
    return refs


def classify_pure_removal(original: str, patch: str) -> tuple[str, str]:
    """순수 삭제 패치의 성격 판정. 반환 ("cleanup"|"functional"|"unparsable", 사유).

    ★ 공개(테스트·감사가 부를 수 있게) — 그러나 정책 결정은 `_removal_issue` 한 곳만 한다.
    """
    try:
        o_tree = ast.parse(original)
        n_tree = ast.parse(patch)
    except Exception as e:                # noqa: BLE001
        return "unparsable", f"AST 파싱 불가({type(e).__name__}) — 판정 포기"

    o_bodies = _scope_bodies(o_tree)
    n_bodies = _scope_bodies(n_tree)
    new_defs = _defined_names(n_tree)
    new_refs = _referenced_names(n_tree)

    explained: list[str] = []
    for scope, ocnt in o_bodies.items():
        ncnt = n_bodies.get(scope)
        if ncnt is None:
            # 스코프 자체가 사라짐 → 부모 스코프의 DEF 손실로 이미 판정된다
            continue
        for key, cnt in (ocnt - ncnt).items():
            if cnt <= 0 or key == "DOC":
                continue
            where = scope or "<module>"
            if key.startswith("DEF:"):
                nm = key[4:]
                if nm in new_defs:
                    explained.append(f"중복 정의 제거({nm})")
                    continue
                if nm in new_refs:
                    return "functional", f"삭제된 `{nm}` 가 패치 후에도 참조됨 @{where}"
                explained.append(f"dead code 제거({nm})")
                continue
            if key.startswith("IMP:"):
                names = [x for x in key[4:].split(",") if x]
                used = [x for x in names if x in new_refs]
                if used:
                    return "functional", f"사용 중인 import 제거({','.join(used)}) @{where}"
                explained.append(f"미사용 import 제거({','.join(names) or '?'})")
                continue
            return "functional", f"실행 문장 제거 @{where}"

    if explained:
        return "cleanup", "정당한 정리로 설명됨 — " + ", ".join(explained[:4])
    return "cleanup", "제거된 유의미 문장 없음(주석·공백·문서열만)"


def _removal_issue(original: str, patch: str) -> str:
    """★ code-removal patch 가드 — 기능을 *지워서* 통과시키는 패치 거부.

    APR 문헌의 최악 실패 모드. 두 규칙 (임계 상수는 하나, 나머지는 *구조 술어*):
      ① 유의미한 줄이 `_max_shrink_ratio()` 넘게 줄어듦
      ② 순수 삭제(추가 0줄) 이면서 **AST 상 기능 제거** 인 경우 (P3 정교화 2026-07-25)
         — 중복 def·dead code·미사용 import 정리는 통과시킨다.
    위반 사유 문자열 반환, 정상이면 "".
    """
    orig = _meaningful_lines(original)
    new  = _meaningful_lines(patch)
    if not orig:                       # 원본을 못 읽었으면 판정 불가 — 통과(보수적)
        return ""
    if not new:
        return "패치가 비어 있음(전체 삭제)"
    lost = len(orig) - len(new)
    if lost > 0:
        ratio = lost / len(orig)
        if ratio > _max_shrink_ratio():
            return (f"코드 삭제 과다 — 유의미한 줄 {len(orig)}→{len(new)} "
                    f"({ratio:.0%} 감소 > 임계 {_max_shrink_ratio():.0%})")
    added = set(new) - set(orig)
    removed = set(orig) - set(new)
    if removed and not added:
        if not _cleanup_allowed():
            return f"순수 삭제 패치(추가 0줄 / 삭제 {len(removed)}줄) — 기능 제거로 통과 시도"
        kind, why = classify_pure_removal(original, patch)
        if kind == "cleanup":
            log.info(f"[GUARDIAN] 순수 삭제 허용 — {why}")
            return ""
        if kind == "functional":
            return f"순수 삭제 패치 — {why}"
        # unparsable → 판정 불가. 종전대로 거부 (fail-closed)
        return (f"순수 삭제 패치(추가 0줄 / 삭제 {len(removed)}줄) — {why}")
    return ""


def _reproducible_types() -> frozenset:
    """★ ② 동적 설계 — 재현 가능한 오류 타입을 *손으로 나열하지 않는다*.

    `severity.DETERMINISTIC_CODE_ERROR_TYPES` = "재시도해도 100% 같게 실패하는 타입"
    = 곧 "지금 다시 돌려보면 재현되는 타입". 재현 검증의 정의와 정확히 같은 집합이므로
    거기서 파생한다. severity 에 타입이 추가되면 재현 검증도 자동으로 넓어진다.
    (severity.py 는 다른 소유자 — 읽기만 하고 import 로 파생. 사본 금지.)
    """
    try:
        from JARVIS07_GUARDIAN.severity import DETERMINISTIC_CODE_ERROR_TYPES
        return frozenset(DETERMINISTIC_CODE_ERROR_TYPES)
    except Exception as e:      # fail-open: 파생 실패 시 재현 시도 안 함(=unverifiable)
        log.debug(f"[VERIFY] 재현가능 타입 파생 실패: {e}")
        return frozenset()


# ── 재현 프로브 (별도 인터프리터 — 데몬의 import 캐시가 진실을 가림) ──────────
_PROBE_SRC = r'''
import builtins, importlib, json, sys
spec = json.loads(sys.argv[1])
root = spec.get("root") or ""
if root and root not in sys.path:
    sys.path.insert(0, root)
_et = spec.get("etype") or ""
_orig = getattr(builtins, _et, None)
if not (isinstance(_orig, type) and issubclass(_orig, BaseException)):
    _orig = Exception
def _same(e):
    t = type(e)
    return issubclass(t, _orig) or issubclass(_orig, t)   # 양방향 = 같은 계열
out = {"ran": True, "repro": False, "raised": "", "msg": ""}
try:
    kind = spec["kind"]
    if kind == "import_module":
        importlib.import_module(spec["mod"])
    elif kind == "import_symbol":
        m = importlib.import_module(spec["mod"])
        if not hasattr(m, spec["sym"]):
            raise ImportError("cannot import name %r from %r" % (spec["sym"], spec["mod"]))
    elif kind == "compile_file":
        with open(spec["path"], encoding="utf-8") as f:
            src = f.read()
        compile(src, spec["path"], "exec")
    else:
        out["ran"] = False
except BaseException as e:
    out["raised"] = type(e).__name__
    out["msg"] = str(e)[:300]
    out["repro"] = bool(_same(e))
sys.stdout.write(json.dumps(out))
'''

# ★ ERRORS [32][160][137] 4회 반복 박제 — subprocess PATH 는 *조건 없이* prepend
_EXTRA_PATHS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin"]


def _run_probe(spec: dict, budget: float | None = None) -> dict:
    """프로브 1건 실행. 반환: {"ran","repro","raised","msg"} (실행 불가 시 ran=False).

    ★ budget: 남은 총 예산(초). 발행 파이프라인을 막지 않도록 검증 전체가
      `_verify_timeout()` 안에서 끝난다 — 초과분은 실행하지 않고 unverifiable 로 떨어진다.
    """
    env = dict(os.environ)
    env["PATH"] = ":".join(_EXTRA_PATHS) + ":" + env.get("PATH", "")
    env["PYTHONPATH"] = str(_ROOT) + ":" + env.get("PYTHONPATH", "")
    env["JARVIS_PROBE"] = "1"          # 하류가 프로브 실행을 구분할 수 있게
    spec = dict(spec)
    spec["root"] = str(_ROOT)
    try:
        cp = subprocess.run(
            [sys.executable, "-c", _PROBE_SRC, json.dumps(spec)],
            cwd=str(_ROOT), env=env, capture_output=True, text=True,
            timeout=max(2.0, budget if budget is not None else _verify_timeout()),
        )
        raw = (cp.stdout or "").strip().splitlines()
        for ln in reversed(raw):        # 하류 print 노이즈 무시 — 마지막 JSON 만
            ln = ln.strip()
            if ln.startswith("{") and ln.endswith("}"):
                return json.loads(ln)
        return {"ran": False, "repro": False, "raised": "", "msg": (cp.stderr or "")[:200]}
    except Exception as e:
        return {"ran": False, "repro": False, "raised": "", "msg": f"probe 실패: {e}"[:200]}


def _origin_files(record: dict) -> list[Path]:
    """오류가 난 파일 후보 — traceback 의 `File "..."` 중 저장소 안쪽 (뒤에서부터)."""
    import re as _re
    out: list[Path] = []
    tb = str((record or {}).get("traceback") or "")
    for m in _re.finditer(r'File "([^"]+\.py)"', tb):
        try:
            p = Path(m.group(1)).resolve()
            p.relative_to(_ROOT)
            if p.exists() and p not in out:
                out.append(p)
        except Exception:
            continue
    out.reverse()                        # 최심부 프레임 우선
    mod = str((record or {}).get("module") or "").strip()
    if mod and "/" not in mod:
        cand = _ROOT / (mod.replace(".", "/") + ".py")
        if cand.exists() and cand not in out:
            out.append(cand)
    return out


def _parse_import_target(message: str) -> dict:
    """ImportError 계열 메시지 → 재현 프로브 스펙."""
    import re as _re
    msg = message or ""
    m = _re.search(r"cannot import name ['\"]([\w.]+)['\"](?:\s+from\s+['\"]?([\w.]+))?", msg)
    if m and m.group(2):
        return {"kind": "import_symbol", "mod": m.group(2), "sym": m.group(1)}
    m = _re.search(r"No module named ['\"]([\w.]+)['\"]", msg)
    if m:
        return {"kind": "import_module", "mod": m.group(1)}
    return {}


def _is_repo_module(mod: str) -> bool:
    """이 모듈이 *저장소 안* 코드인가 — 밖(서드파티/stdlib)이면 코드로 판정할 수 없다."""
    top = (mod or "").split(".")[0]
    if not top:
        return False
    return (_ROOT / f"{top}.py").exists() or (_ROOT / top / "__init__.py").exists() \
        or (_ROOT / top).is_dir()


def _source_imports(src: str, mod: str) -> bool:
    """이 소스가 `mod` 를 (그 이름 그대로) import 하는가 — AST 판정."""
    top = (mod or "").split(".")[0]
    if not top or not src:
        return False
    try:
        tree = ast.parse(src)
    except Exception:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any((a.name or "").split(".")[0] == top for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "").split(".")[0] == top:
                return True
    return False


def _file_imports(path: Path, mod: str) -> bool:
    try:
        return _source_imports(path.read_text(encoding="utf-8"), mod)
    except Exception:
        return False


def _name_still_unbound(path: Path, name: str) -> bool | None:
    """NameError 정적 재현 — 그 이름이 여전히 *바인딩 없이 참조* 되는가.

    런타임 NameError 는 함수 안에서 나므로 import 로 재현 못 한다. 대신 AST 로
    "어디에도 묶이지 않은 채 읽히는가" 를 본다. 묶여 있으면 gone(보수적).
    반환: True=재현 / False=해소 / None=판정 불가
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception:
        return None
    if not name:
        return None
    import builtins as _bi
    if hasattr(_bi, name) or name in ("self", "cls"):
        return False
    loaded = bound = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id == name:
                loaded = True
            elif node.id == name:
                bound = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                bound = True
        elif isinstance(node, ast.arg) and node.arg == name:
            bound = True
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if (a.asname or a.name.split(".")[0]) == name:
                    bound = True
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            bound = True
        elif isinstance(node, ast.ExceptHandler) and node.name == name:
            bound = True
    if not loaded:
        return False                     # 더는 참조되지 않음 = 해소
    return not bound


def verify_fix(error_record: dict, analysis: dict, file_path: Path,
               original_content: str = "") -> tuple[str, str]:
    """원 오류를 *실제로 다시 일으켜 본다*. 반환: (계약 문자열, 사람이 읽는 사유).

    ★ 이 함수는 파일을 고치지 않는다 — 판정만. 롤백 여부는 apply_fix 가 결정.
    """
    if not _verify_enabled():
        return "", "검증 비활성(GUARDIAN_FIX_VERIFY=0)"

    rec = error_record or {}
    et  = str(rec.get("error_type") or analysis.get("error_type") or "").strip()
    msg = str(rec.get("message") or "")
    if not et:
        return VERIFY_UNVERIFIABLE, "error_type 없음 — 재현 대상 특정 불가"

    # 일시적·외부 오류는 재현 자체가 무의미 (severity 판단 재사용 — 사본 금지)
    try:
        from JARVIS07_GUARDIAN.severity import is_transient, kind_of
        if is_transient(et, msg, str(rec.get("source") or ""), kind_of(rec)):
            return VERIFY_UNVERIFIABLE, f"일시적·외부 오류({et}) — 재현 불가 유형"
    except Exception:
        pass

    short = et.rsplit(".", 1)[-1]
    if short not in _reproducible_types():
        return VERIFY_UNVERIFIABLE, f"{et} 는 런타임 데이터 의존 — 재현 불가 유형"

    probes: list[dict] = []
    # ① ImportError 계열 — 메시지에서 모듈·심볼을 뽑아 실제로 다시 import
    spec = _parse_import_target(msg)
    if spec:
        probes.append(spec)
    # ② 문법 계열 — 오류가 난 파일(+ 방금 고친 파일)을 다시 컴파일
    targets = _origin_files(rec)
    if file_path and file_path.suffix == ".py" and file_path not in targets:
        targets.append(file_path)
    if not spec:
        for t in targets[:3]:
            probes.append({"kind": "compile_file", "path": str(t)})

    detail_bits: list[str] = []
    ran_any = False
    _deadline = time.monotonic() + _verify_timeout()     # ★ 총 예산 — 발행을 막지 않는다
    for p in probes:
        _left = _deadline - time.monotonic()
        if _left < 2.0:
            detail_bits.append("검증 예산 소진 — 남은 프로브 생략")
            break
        r = _run_probe({**p, "etype": short}, budget=_left)
        if not r.get("ran"):
            detail_bits.append(f"{p.get('kind')}: 실행 불가({r.get('msg','')[:60]})")
            continue
        ran_any = True
        if r.get("repro"):
            _why = (f"{p.get('kind')}({p.get('mod') or p.get('path','')}) → "
                    f"{r.get('raised')}: {r.get('msg','')[:120]}")
            _mod = str(p.get("mod") or "")
            if p.get("kind") == "import_module" and not _is_repo_module(_mod):
                # ⓐ 실패하던 import 문 자체가 사라졌다 → 그 실패 지점은 진짜로 해소됨
                #    (예: 정적 fixer `relative_import` 가 절대 경로로 재작성한 경우)
                #    ★ *원본에 그 import 가 실제로 있었다* 는 증거를 요구한다 — 없으면
                #      "원래부터 없었다" 를 "고쳐졌다" 로 오판한다(합성 record 에서 실측됨).
                if (_source_imports(original_content, _mod)
                        and not any(_file_imports(t, _mod) for t in targets)):
                    ran_any = True
                    detail_bits.append(f"실패하던 `import {_mod}` 가 제거·재작성됨")
                    continue
                # ⓑ 여전히 그 이름으로 import 하는데 환경에 없음 → *코드로 판정 불가*.
                #    여기서 still_reproduces 로 단정하면 "선택적 의존성 graceful fallback"
                #    같은 정당한 수정을 되돌려 오히려 크래시를 복원한다(라이브 — 보수적으로).
                #    → 롤백도 보상도 하지 않고 unverifiable 로 남긴다(양의 보상 역시 0).
                return (VERIFY_UNVERIFIABLE,
                        f"환경 의존(저장소 밖 모듈 미설치) — 코드로 판정 불가: {_why}")
            return VERIFY_REPRODUCES, _why
        detail_bits.append(f"{p.get('kind')}({p.get('mod') or Path(str(p.get('path',''))).name}) OK")

    # ③ NameError — 정적 unbound 검사 (런타임 재현 불가 → AST 로 대체)
    if short == "NameError":
        import re as _re
        m = _re.search(r"name ['\"](\w+)['\"] is not defined", msg)
        nm = m.group(1) if m else ""
        for t in targets[:3]:
            v = _name_still_unbound(t, nm)
            if v is None:
                continue
            ran_any = True
            if v:
                return VERIFY_REPRODUCES, f"'{nm}' 가 {t.name} 에서 여전히 미바인딩 참조"
            detail_bits.append(f"name({nm}) bound/미참조 @ {t.name}")

    if not ran_any:
        return VERIFY_UNVERIFIABLE, "재현 프로브를 하나도 실행하지 못함 — " + ("; ".join(detail_bits) or "대상 없음")
    return VERIFY_GONE, "; ".join(detail_bits)[:300]


# ── 밴딧 보상 — ★ 양방향 (성공만 기록하던 단방향 폐기) ─────────────────────
def _bandit_signal(error_record: dict, analysis: dict, success: bool,
                   learned_hits: int = 0, why: str = "") -> None:
    """밴딧에 결과 반영. success=False 면 음의 보상(bandit._LOSS).

    ★ bandit.py 는 다른 파일 — 편집하지 않고 공개 시그니처
      `reward(error_type, fixer_name, success: bool, error_record)` 만 호출한다.
      `success=False` 가 곧 음의 보상 표현(r=_LOSS=-1.0)이라 별도 API 불필요.
    """
    try:
        from JARVIS07_GUARDIAN.bandit import reward as _bandit_reward
        rec = error_record or {}
        _et = str(rec.get("error_type") or "")
        _bfx = analysis.get("_bandit_fixer") or analysis.get("pattern", "")
        if not _bfx and _et and learned_hits > 0:
            from JARVIS07_GUARDIAN.pattern_fixer import bandit_arm_name as _arm_name
            _bfx = _arm_name(rec, learned_hits)
        if not _bfx and str(analysis.get("source", "")) in ("llm", "cached"):
            _bfx = "llm_patch"        # LLM 폴백 전략 arm (bandit._arm_key → "llm")
        if _et and _bfx:
            _bandit_reward(_et, _bfx, success=success, error_record=rec)
            log.info(f"[BANDIT] {'＋' if success else '－'} 보상 {_et}/{_bfx} — {why}")
        else:
            log.debug(f"[BANDIT] 귀속 불가 — 보상 생략 (et={_et}, fixer={_bfx}, why={why})")
    except Exception as _be:
        log.debug(f"[BANDIT] 보상 기록 실패: {_be}")


def _record_learning_failure(error_record: dict, analysis: dict,
                             reason: str, verification: str = "") -> dict:
    """★ 결함 2 배선 (2026-07-25) — 롤백·재현 실패를 *학습 자산* 에 반영한다.

    `eval_agent.record_fix_failure()` 는 만들어졌지만 **호출자가 0곳** 이었다 (죽은 함수).
    적용한 수정이 되돌려졌다는 사실은 그 패턴이 나쁘다는 *가장 강한 외생 신호* 인데
    learned_patterns 의 hit_count 는 오르기만 하고 내려가는 길이 없었다.

    ★ 데드락 주의 (실측으로 확인한 락 순서):
      `pattern_fixer.record_pattern_hit()` 의 `with _LEARNED_LOCK:` 블록은
      1217~1289 줄이고, 그 안에서는 eval_agent 를 호출하지 않는다(evaluate 는 1164 —
      락 *밖*). 본 함수는 apply_fix 의 *롤백 경로* 에서만, 어떤 락도 잡지 않은 상태로
      호출된다 → `_LEARNED_LOCK` 중첩 획득 없음.
      (게다가 record_fix_failure 는 `acquire(timeout=5.0)` 이라 최악에도 블록되지 않는다.)
    """
    try:
        from JARVIS07_GUARDIAN.eval_agent import record_fix_failure as _rff
        res = _rff(
            error_record or {},
            fixer_name=str(analysis.get("pattern") or analysis.get("_bandit_fixer")
                           or ("llm_patch" if str(analysis.get("source", "")) in ("llm", "cached")
                               else "")),
            reason=reason,
            verification=verification,
        )
        log.info(f"[GUARDIAN/learn-fail] {res.get('action')} — fp='{str(res.get('fingerprint'))[:50]}' "
                 f"fail={res.get('fail_count')} hit={res.get('hit_count')} ({reason[:60]})")
        return res
    except Exception as e:      # noqa: BLE001 — 학습 반영 실패가 수정 흐름을 막지 않는다
        log.debug(f"[GUARDIAN/learn-fail] record_fix_failure 호출 실패: {e}")
        return {"ok": False, "action": f"error:{type(e).__name__}"}


def _fetch_record(error_id: int, analysis: dict) -> dict:
    """error_record 확보 — 롤백 경로에서도 밴딧 귀속이 가능하도록."""
    try:
        if isinstance(error_id, int) and error_id >= 0:
            from shared import db as _db
            rec = _db.get_error(error_id)
            if rec:
                return rec
    except Exception:
        pass
    # error_id=-1 (incident_responder 합성 경로) 은 DB 행이 없다 → analysis 가 가진 것으로 최선
    return {k: analysis.get(k, "") for k in
            ("error_type", "message", "module", "source", "traceback", "context")}


def _note_resolution(error_id: int, text: str) -> None:
    """★ 스키마 변경 없이 기존 `resolution` 텍스트 컬럼에만 기록 (다른 소유자 존중)."""
    try:
        if not isinstance(error_id, int) or error_id < 0:
            return
        from shared.db import get_db as _get_db
        with _get_db() as conn:
            conn.execute("UPDATE error_log SET resolution=? WHERE id=?", (text[:4000], error_id))
    except Exception as e:
        log.debug(f"[GUARDIAN] resolution 기록 실패: {e}")


class FixResult(int):
    """apply_fix 반환값 — ★ 하위호환: `bool` 처럼 쓰이고, 계약대로 `verification` 키도 준다.

    기존 호출자(`success = apply_fix(...)`, `if apply_fix(...)`)는 그대로 동작하고,
    새 호출자는 `res["verification"]` / `res.get("verification")` 로 검증 상태를 읽는다.
    """
    def __new__(cls, ok: bool, **meta):
        obj = super().__new__(cls, 1 if ok else 0)
        obj._meta = {"fixed": bool(ok), **meta}
        return obj

    def get(self, key, default=None):
        return self._meta.get(key, default)

    def __getitem__(self, key):
        return self._meta[key]

    def __contains__(self, key):
        return key in self._meta

    def keys(self):
        return self._meta.keys()

    def items(self):
        return self._meta.items()

    def __repr__(self):
        return f"FixResult({bool(self)}, {self._meta})"


def apply_fix(error_id: int, analysis: dict, mark_wontfix: bool = True) -> bool:
    """분석 결과를 실제 파일에 적용.

    Args:
        error_id: error_log.id
        analysis: error_analyzer.analyze() 반환값
        mark_wontfix: False 이면 실패 시 wontfix 마킹·알림·ERRORS.md 기록 생략.
            guardian._orchestrate() 에서 Claude fallback 전 1차 시도 시 False 로 호출.

    Returns:
        FixResult — `bool` 처럼 동작(하위호환)하면서 계약 키를 갖는 값:
          res["verification"] ∈ {"reproduced_gone","unverifiable","still_reproduces",""}
          ("" = 킬스위치 GUARDIAN_FIX_VERIFY=0 로 검증을 돌리지 않음)
    """
    try:
        # ★ 동적 flow (사용자 박제 2026-07-19): 고정 e8(J07→J02) 대신 *실제 수정 대상* 에이전트로.
        #   오류 module/source 로 대상 판별(예: JARVIS06 → j06) → J07→해당에이전트 정확히 활성화·로그.
        from shared.pipeline_activity import mark_flow, module_to_agent
        # error_analyzer 는 실제 패치 대상을 target_file 로 방출 → 우선 사용. module/target 도 보조.
        _tgt = (module_to_agent(str(analysis.get("target_file") or ""))
                or module_to_agent(str(analysis.get("module") or ""))
                or module_to_agent(str(analysis.get("target") or "")))
        if not _tgt and isinstance(error_id, int) and error_id >= 0:
            try:  # 실제 error_id 만 DB 조회 (합성 -1 제외 — 매칭 0행 → 거짓 폴백 원인이었음)
                import sqlite3 as _sq
                from shared.db import DB_PATH as _dbp
                _c = _sq.connect(str(_dbp))
                _row = _c.execute("SELECT module, source FROM error_log WHERE id=?", (error_id,)).fetchone()
                _c.close()
                if _row:
                    _tgt = module_to_agent(_row[0] or "") or module_to_agent(_row[1] or "")
            except Exception:
                pass
        # ★ 대상이 정확히 판별될 때만 신호 (사용자 박제 2026-07-19): 거짓 j02 폴백 제거.
        #   판별 불가면 대시보드에 가짜 J07→J02 를 그리지 않는다(잘못된 연결 금지).
        if _tgt:
            mark_flow("j07", _tgt, "수정")
    except Exception:
        pass

    def _fail(reason: str, verification: str = "") -> bool:
        if mark_wontfix:
            _mark_wontfix(error_id, reason)
        return FixResult(False, verification=verification, reason=reason)

    if not analysis.get("fixable"):
        log.info(f"[GUARDIAN] #{error_id} fixable=False — 수정 skip")
        return _fail("fixable=False")

    patch      = analysis.get("patch", "")
    target_rel = analysis.get("target_file", "")

    if not patch or not target_rel:
        log.warning(f"[GUARDIAN] #{error_id} patch 또는 target 없음")
        return _fail("patch/target 누락")

    # ── 경로 안전 검증 ───────────────────────────────────────────
    file_path = _safe_path(target_rel)
    if not file_path:
        log.warning(f"[GUARDIAN] #{error_id} 경로 검증 실패: {target_rel}")
        return _fail("경로 검증 실패")

    if not file_path.exists():
        log.warning(f"[GUARDIAN] #{error_id} 파일 없음: {file_path}")
        return _fail("파일 없음")

    # ── Python 구문 검증 ─────────────────────────────────────────
    if file_path.suffix == ".py" and not _validate_python(patch):
        log.warning(f"[GUARDIAN] #{error_id} 구문 오류 — 수정 중단")
        # 깨진 패치를 만든 것도 그 전략의 실패다 — 음의 보상(종전엔 무신호였다)
        if _verify_enabled():
            _bandit_signal(_fetch_record(error_id, analysis), analysis, success=False,
                           why="patch 구문 오류")
        return _fail("patch 구문 오류", verification=VERIFY_REPRODUCES)

    # ── 원본 캡처 (diff 저장 + code-removal 가드) ────────────────
    try:
        original_content = file_path.read_text(encoding="utf-8")
    except Exception:
        original_content = ""

    # ── ★ code-removal 가드 (적용 *전* 차단) ─────────────────────
    #   "기능을 지워서 통과시키는" 패치는 파일이 파싱·import 되므로 종전 판정으론
    #   무조건 fixed 였다. APR 문헌의 최악 실패 모드 — 적용 자체를 막는다.
    if _verify_enabled() and file_path.suffix == ".py":   # 코드에만 적용(.md 재구성은 정상)
        _rm = _removal_issue(original_content, patch)
        if _rm:
            # ★ P3 (사용자 박제 2026-07-25): 거부는 *판정 불가·부적격* 이지 "수정 실패" 가 아니다.
            #   종전엔 여기서 밴딧에 음의 보상을 주고 verification=still_reproduces 로 흘려
            #   ① 맞는 수정을 낸 arm 을 깎고 ② 하류(eval)가 실패로 오인하게 만들었다.
            #   → 보상 신호 없음(0) + verification 신호 없음("") 으로 정정.
            log.warning(f"[GUARDIAN] #{error_id} code-removal 패치 거부 — {_rm} "
                        f"(보상 0 — 판정 불가는 실패가 아니다)")
            return _fail(f"code-removal 패치 거부: {_rm}")

    # ── .bak 백업 ────────────────────────────────────────────────
    bak = _backup(file_path)
    if not bak:
        return _fail("백업 실패")

    # ── 파일 적용 ────────────────────────────────────────────────
    try:
        file_path.write_text(patch, encoding="utf-8")
        log.info(f"[GUARDIAN] #{error_id} 파일 적용: {file_path.name}")
    except Exception as e:
        log.error(f"[GUARDIAN] #{error_id} 파일 쓰기 실패: {e}")
        _rollback(file_path, bak)
        _rec_w = _fetch_record(error_id, analysis)
        _bandit_signal(_rec_w, analysis, success=False, why="파일 쓰기 실패 → 롤백")
        _record_learning_failure(_rec_w, analysis, f"파일 쓰기 실패 → 롤백: {str(e)[:60]}")
        return _fail(f"파일 쓰기 실패: {str(e)[:50]}", verification=VERIFY_REPRODUCES)

    # ── import 검증 ──────────────────────────────────────────────
    time.sleep(0.3)
    if not _import_check(file_path):
        log.warning(f"[GUARDIAN] #{error_id} import 실패 → 롤백")
        _rollback(file_path, bak)
        _rec_i = _fetch_record(error_id, analysis)
        _bandit_signal(_rec_i, analysis, success=False, why="import 검증 실패 → 롤백")
        # ★ 결함 2 배선 — 롤백은 학습 자산에도 반영한다 (감쇠·강등·격리)
        _record_learning_failure(_rec_i, analysis, "import 검증 실패 → 롤백")
        if mark_wontfix:
            try:
                from shared import db as _db
                _db.mark_error_status(error_id, "wontfix")
            except Exception:
                pass
            _notify_fail(error_id, "import 검증 실패 — 롤백 완료")
            error_record = {}
            try:
                from shared import db as _db
                error_record = _db.get_error(error_id)
            except Exception:
                pass
            _update_errors_md(error_record, analysis, success=False,
                              verified=["문법 검사 통과", "import 검증 실패 → 자동 롤백"])
        return FixResult(False, verification=VERIFY_REPRODUCES, reason="import 검증 실패")

    # ── ★ 원 오류 재현 검증 (구문·import 통과 ≠ 오류 해소) ──────────
    _rec_for_verify = _fetch_record(error_id, analysis)
    _vstate, _vdetail = verify_fix(_rec_for_verify, analysis, file_path,
                                   original_content=original_content)
    log.info(f"[GUARDIAN] #{error_id} 재현검증 = {_vstate or '(비활성)'} — {_vdetail}")

    if _vstate == VERIFY_REPRODUCES:
        # 원 오류가 그대로 재현 → 이 패치는 고친 것이 아니다. 되돌린다.
        log.warning(f"[GUARDIAN] #{error_id} 원 오류 재현됨 → 롤백 (증상 은폐 학습 차단)")
        _rollback(file_path, bak)
        _bandit_signal(_rec_for_verify, analysis, success=False,
                       why=f"still_reproduces — {_vdetail[:80]}")
        # ★ 결함 2 배선 — 외생 신호(재현됨)를 learned_patterns 에 반영: 감쇠 → 임계 시 격리
        _record_learning_failure(_rec_for_verify, analysis,
                                 f"원 오류 재현 → 롤백: {_vdetail[:100]}",
                                 verification=VERIFY_REPRODUCES)
        if mark_wontfix:
            try:
                from shared import db as _db
                _db.mark_error_status(error_id, "wontfix")
            except Exception:
                pass
            _note_resolution(error_id, f"[verification={VERIFY_REPRODUCES}] {_vdetail}")
            _notify_fail(error_id, "원 오류 재현 — 롤백 완료")
            _update_errors_md(_rec_for_verify, analysis, success=False,
                              verified=["문법 검사 통과", "import 검증 통과",
                                        f"원 오류 재현 검증: {VERIFY_REPRODUCES} — {_vdetail[:120]}",
                                        "→ 자동 롤백"])
        return FixResult(False, verification=VERIFY_REPRODUCES, reason=_vdetail)

    # ── 성공 처리 ────────────────────────────────────────────────
    #   ★ resolution 에 검증 상태를 *기계 판독 가능한 접두* 로 박제 (스키마 변경 0).
    #     나중에  SELECT ... WHERE resolution LIKE '[verification=reproduced_gone]%'
    #     로 "진짜 검증된 수정" 만 집계할 수 있다.
    _vtag = f"[verification={_vstate or 'legacy_unchecked'}] "
    try:
        from shared import db as _db
        _db.mark_error_fixed(
            error_id,
            resolution=_vtag + (_vdetail + "\n" if _vdetail else "")
                       + analysis.get("explanation", "") + "\n" + (patch[:500] if patch else ""),
            fixed_file=str(file_path.relative_to(_ROOT)),
        )
        error_record = _db.get_error(error_id)
    except Exception as e:
        log.error(f"[GUARDIAN] DB 업데이트 실패: {e}")
        error_record = _rec_for_verify or {}

    # ★ 학습 등록 — unified diff 로 저장 (full-file 대체) → 파일 변경 후에도 안전 재적용
    _learned_hits = 0
    try:
        import difflib as _dl
        from JARVIS07_GUARDIAN.pattern_fixer import record_pattern_hit
        _rel = str(file_path.relative_to(_ROOT))
        # unified diff 계산 (5줄 context)
        _diff_lines = list(_dl.unified_diff(
            original_content.splitlines(keepends=True),
            patch.splitlines(keepends=True),
            fromfile=f"a/{_rel}",
            tofile=f"b/{_rel}",
            n=5,
        ))
        _store_patch = "".join(_diff_lines) if _diff_lines else patch
        _learned_hits = record_pattern_hit(
            error_record or {},
            fixer_name=analysis.get("pattern") or "llm_patch",
            fixed_file=_rel,
            source=analysis.get("source", "auto-llm"),
            patch=_store_patch,
            target_file=target_rel or "",
            # ★ 결함 1 배선 (2026-07-25) — 외생 검증 신호를 *생산지에서 소비지까지* 관통.
            #   종전엔 eval_agent 가 verification 인자를 받도록 만들어져 있었지만
            #   저장소 전체에 그 인자를 넘기는 호출자가 **0곳** 이라 인위 주입 시에만
            #   동작하는 죽은 배선이었다. 여기가 유일한 생산 지점이다.
            verification=_vstate or "",
        )
    except Exception as e:
        log.debug(f"[GUARDIAN/learned] apply_fix 학습 등록 실패: {e}")

    # ★ Bandit 보상 — ★ 검증된 것만 양의 보상 (사용자 박제 2026-07-25)
    #   · reproduced_gone → +1  (원 오류가 실제로 사라짐을 확인)
    #   · unverifiable    →  0  (보상 호출 자체를 안 함 — 검증 못 한 것에 양의 보상 금지)
    #   · 킬스위치 OFF    → +1  (종전 동작 그대로)
    if _vstate == VERIFY_UNVERIFIABLE:
        log.info(f"[BANDIT] #{error_id} unverifiable — 보상 생략(0). 검증 못 한 수정에 가점 없음")
    else:
        _bandit_signal(error_record or _rec_for_verify, analysis, success=True,
                       learned_hits=_learned_hits,
                       why=_vstate or "legacy_unchecked")

    _verified_notes = ["문법 검사(ast.parse) 통과", "import 검증 통과", "원본 .bak 보관"]
    if _vstate:
        _verified_notes.append(f"원 오류 재현 검증: {_vstate} — {_vdetail[:120]}")
    _update_errors_md(error_record, analysis, success=True, verified=_verified_notes)
    _notify_success(error_id, file_path.name, analysis.get("explanation", ""))
    log.info(f"[GUARDIAN] #{error_id} 자동 수정 성공 ✅ — {file_path.name} ({_vstate or 'legacy'})")
    return FixResult(True, verification=_vstate or "", detail=_vdetail,
                     fixed_file=str(file_path.relative_to(_ROOT)))


def _mark_wontfix(error_id: int, reason: str = ""):
    """오류 상태를 wontfix로 변경."""
    try:
        from shared import db as _db
        _db.mark_error_status(error_id, "wontfix")
    except Exception as e:
        log.warning(f"[GUARDIAN] #{error_id} wontfix 상태 변경 실패: {e}")


def _notify_success(error_id: int, filename: str, explanation: str):
    pass  # 텔레그램 알림 비활성 (사용자 박제)


def _notify_fail(error_id: int, reason: str):
    pass  # 텔레그램 알림 비활성 (사용자 박제)
