"""JARVIS07_GUARDIAN/pattern_fixer.py — 패턴 기반 자동 수정기 + 학습형.

★ LLM 호출 없이 흔한 오류 패턴을 정규식·AST 로 직접 수정.
★ 자동/수동 수정 사례를 `learned_patterns.json` 에 누적 → 동일 오류 재발 시 즉시 매칭.
빠르고 안전 + 결정적. error_analyzer.analyze() 가 LLM 호출 전 먼저 시도.

기본 5종 정적 패턴:
  1. ModuleNotFoundError 상대 import → 절대 import 자동 변환
  2. TypeError 'NoneType' object is not subscriptable → (x or "")[:N] 안전 슬라이싱
  3. NameError name 'X' is not defined → 오타 자동 교정 (difflib 유사 식별자)
  4. AttributeError 'NoneType' object has no attribute → None 가드 삽입
  5. ImportError cannot import name → 모듈 내 유사 심볼 자동 교정

★ 학습 패턴 (`learned_patterns.json`):
  - 자동/수동 수정 성공 시 fingerprint 자동 누적
  - 동일 fingerprint 재발 시 매핑된 fixer 즉시 실행 (LLM 호출 0)
  - hit_count 누적으로 자주 매칭되는 패턴 우선순위
  - 시간이 지날수록 자동 수정 비율 증가

확장 원칙:
  - 각 패턴은 *명확하고 결정적* — LLM 추론 불필요
  - 위험 신호 감지 시 패스 → LLM fallback
  - 모든 패치는 단위 변경 (전체 파일 덮어쓰기 X)
"""
from __future__ import annotations

import ast
import difflib
import json
import logging
import re
import sys
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.guardian.pattern")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 자비스 폴더 prefix (절대 import 변환 시 사용)
_AGENT_FOLDERS = ("JARVIS00_INFRA", "JARVIS01_MASTER", "JARVIS02_WRITER",
                  "JARVIS03_RADAR", "JARVIS04_SCHEDULER", "JARVIS05_VISION",
                  "JARVIS06_IMAGE", "JARVIS07_GUARDIAN", "JARVIS08_PUBLISH", "shared")

# ★ hit_count 가 이 값에 도달하면 "충분히 검증된 패턴" 로그 출력 (정보 목적)
# Bandit 풀 편입은 hit_count ≥ 1 — 즉시 (Bandit UCB 가 신뢰도 직접 관리)
_HIGH_COUNT_THRESHOLD = 3


# ──────────────────────────────────────────────────────────────
# ★ ADR 008 Phase 4 (사용자 박제 2026-05-17) — 도메인 카테고리 분류
# ──────────────────────────────────────────────────────────────
# 학습 패턴별 *도메인 소속* 자동 추정. ADR 008 Domain Ownership Matrix 기반.
#
# 우선순위:
#   1) fixed_file 경로 prefix (가장 신뢰)
#   2) error_type / fixer_name 키워드
#   3) "unknown" fallback
#
# 새 도메인 추가 시 *_DOMAIN_RULES* 만 갱신. 다른 곳 수정 불필요.

# (도메인명, 경로/키워드 패턴 — 첫 매칭 도메인 우선)
_DOMAIN_RULES: list[tuple[str, list[str]]] = [
    # ADR 008 single-entry-point 도메인 (owner_dirs 기준)
    ("image",        ["JARVIS06_IMAGE/", "image_validators", "image_injectors",
                      "block_assembler", "economic_charts", "thumbnail_maker",
                      "html_screenshotter", "image_agent", "image_spec",
                      "_dedupe_image", "_dedupe_consec", "_dedupe_all",
                      "_validate_image", "_is_heading_img", "assemble_blocks",
                      "enforce_image_between", "enforce_paragraph_pair_image",
                      "compute_unused_image_pool", "EmptySVGFallback",
                      "RenderingQuality", "SpacingPolicy", "UserObserved"]),
    ("publish",      ["JARVIS08_PUBLISH/platforms/", "naver_poster", "tistory_poster",
                      "post_to_naver", "post_to_tistory",
                      "TistoryRedirect", "TistoryStuck", "PostAnalysis", "post_analysis",
                      "PostingFailure", "incident_responder", "posting_fail"]),
    ("category",     ["JARVIS08_PUBLISH/category/", "ECONOMIC_CATEGORY",
                      "category_resolver"]),
    ("credentials",  ["JARVIS08_PUBLISH/credentials/", "naver_cookie_refresher",
                      "tistory_cookie_refresher", "TS_COOKIE", "NV_COOKIE"]),
    ("length",       ["length_manager", "shared/seo.py", "build_length_phrase",
                      "LengthPhrase", "KOREAN_PER_SENTENCE"]),
    ("constitution", ["law_enforcer", "BLOG_SUPREME_LAW", "enforce_supreme_law",
                      "PolicyAlignment", "SupremeBlock", "PromptLeak",
                      "PromptSystem", "human_intro", "FlowAudit", "FlowDefect",
                      "ConstitutionPinning", "비전 박제", "헌법 박제", "CLAUDE.md"]),
    ("schedule",     ["JARVIS04_SCHEDULER/", "job_registry", "job_catalog",
                      "DEFAULT_JOBS", "BackgroundScheduler"]),
    ("tools",        ["shared/tools.py", "JARVIS01_MASTER/agent_tools",
                      "register_tool", "@register_tool"]),
    ("guardian",     ["JARVIS07_GUARDIAN/", "auto_repair", "error_collector",
                      "pattern_fixer", "eval_agent", "AutoRepairFix",
                      "ExternalEdit", "GuardianLearning", "SelfRepair", "SelfLearning",
                      "NeutralToken", "record_external_change", "self_repair_runs",
                      "TEST_DRY_RUN"]),
    ("infra",        ["JARVIS00_INFRA/", "infra_agent", "build_status", "SPOF"]),
    ("master",       ["JARVIS01_MASTER/", "dispatchers.py", "core_agent",
                      "router.py", "intents.py"]),
    ("radar",        ["JARVIS03_RADAR/", "performance_collector", "trend_collector"]),
    ("writer",       ["JARVIS02_WRITER/", "shared/llm.py", "SystemMessage",
                      "ModelCatalog"]),  # 가장 마지막 — writer 폴더 + LLM 공유
]


def _infer_domain(
    *,
    fixed_file: str = "",
    error_type: str = "",
    fixer_name: str = "",
    message: str = "",
    target_file: str = "",
) -> str:
    """학습 패턴의 도메인 자동 추정 (ADR 008 Phase 4).

    Args:
        fixed_file: 수정된 파일 경로 (가장 신뢰)
        error_type: Python 예외 클래스명 또는 정책 타입
        fixer_name: 적용된 fixer 함수 이름
        message: 오류 메시지 (보조 신호)
        target_file: llm_patch 타겟 파일

    Returns:
        도메인 이름 (image/publish/category/credentials/length/constitution/
        schedule/tools/guardian/infra/master/radar/writer/unknown)
    """
    # 경로 신호 통합 (fixed_file > target_file)
    paths = " ".join(filter(None, [str(fixed_file), str(target_file)]))
    # 키워드 신호 통합 (error_type + fixer_name + message)
    keywords = " ".join(filter(None, [str(error_type), str(fixer_name), str(message)]))
    combined = f"{paths} {keywords}"
    combined_lc = combined.lower()  # case-insensitive 매칭용 (소문자 fixer name 대응)

    for domain, patterns in _DOMAIN_RULES:
        for pat in patterns:
            if pat in combined or pat.lower() in combined_lc:
                return domain
    return "unknown"


# ──────────────────────────────────────────────────────────────
# 패턴 1: ModuleNotFoundError 상대 import → 절대 import
# ──────────────────────────────────────────────────────────────

def _fix_relative_import(error_record: dict) -> Optional[dict]:
    """`from <mod> import` 또는 `import <mod>` 가 상대 import 라 실패 → 절대 변환.

    검출 조건:
      - error_type = 'ModuleNotFoundError' / 'ImportError'
      - message: "No module named '<mod>'"
      - traceback 마지막 frame 파일이 자비스 폴더 내
      - 같은 폴더에 동명 모듈(.py) 또는 동명 하위 패키지 존재
    """
    et = error_record.get("error_type", "")
    if et not in ("ModuleNotFoundError", "ImportError"):
        return None

    msg = error_record.get("message", "") or ""
    m = re.search(r"No module named ['\"]([^'\"]+)['\"]", msg)
    if not m:
        return None
    missing = m.group(1).split(".")[0]   # 최상위 모듈명만

    tb = error_record.get("traceback", "") or ""
    # 마지막 자비스 폴더 내 파일 찾기
    file_path = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
        except ValueError:
            continue
    if not file_path or not file_path.exists():
        return None

    # 같은 폴더에 동명 모듈 또는 다른 자비스 폴더에 동명 모듈 탐색
    file_parent = file_path.parent
    candidate_pkg = None

    # 같은 폴더
    sibling_py  = file_parent / f"{missing}.py"
    sibling_pkg = file_parent / missing / "__init__.py"
    if sibling_py.exists() or sibling_pkg.exists():
        # 폴더명이 자비스 prefix 면 그 폴더가 패키지
        try:
            rel = file_parent.relative_to(_ROOT)
            parts = rel.parts
            if parts and parts[0] in _AGENT_FOLDERS:
                candidate_pkg = ".".join(parts)
        except ValueError:
            pass

    # 다른 자비스 폴더 검색 (예: collectors → JARVIS03_RADAR.collectors)
    if not candidate_pkg:
        for folder in _AGENT_FOLDERS:
            test_py  = _ROOT / folder / f"{missing}.py"
            test_pkg = _ROOT / folder / missing / "__init__.py"
            if test_py.exists() or test_pkg.exists():
                candidate_pkg = folder
                break

    if not candidate_pkg:
        return None

    abs_prefix = f"{candidate_pkg}.{missing}"

    # 파일 내용에서 상대 import 라인 찾아 절대 import 로 치환
    text = file_path.read_text(encoding="utf-8")
    orig = text

    # 패턴 A: from <missing> import ...
    text = re.sub(
        rf'(^|\n)(\s*)from\s+{re.escape(missing)}(\s+|\.\w)',
        rf'\1\2from {abs_prefix}\3',
        text,
    )
    # 패턴 B: import <missing> (as ...)?
    text = re.sub(
        rf'(^|\n)(\s*)import\s+{re.escape(missing)}(\s|$|,|;)',
        rf'\1\2import {abs_prefix}\3',
        text,
    )

    if text == orig:
        return None

    return {
        "fixable": True,
        "pattern": "relative_import",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": text,
        "explanation": (
            f"'{missing}' 모듈 상대 import 실패 → 절대 경로 "
            f"`from {abs_prefix}` 로 일괄 변환 ({orig.count(missing) - text.count(abs_prefix) + text.count(abs_prefix)}건)."
        ),
    }


# ──────────────────────────────────────────────────────────────
# 패턴 2: TypeError 'NoneType' object is not subscriptable
# → r.get("X","")[:N] → (r.get("X") or "")[:N]
# ──────────────────────────────────────────────────────────────

def _fix_none_slicing(error_record: dict) -> Optional[dict]:
    et = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    if et != "TypeError":
        return None
    if "not subscriptable" not in msg and "subscript" not in msg:
        return None

    tb = error_record.get("traceback", "") or ""
    # traceback 의 마지막 자비스 내 file:line 추출
    file_path = None
    line_no = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
                line_no = int(tm.group(2))
        except ValueError:
            continue
    if not file_path or not file_path.exists() or not line_no:
        return None

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_no - 1 >= len(lines):
        return None

    target_line = lines[line_no - 1]
    # 패턴: <expr>.get("X","")[:N]  또는  <expr>.get("X","")[:N]
    new_line = re.sub(
        r'(\w+)\.get\(\s*(["\'][^"\']+["\'])\s*(?:,\s*["\'][^"\']*["\'])?\s*\)\s*\[:',
        r'(\1.get(\2) or "")[:',
        target_line,
    )
    if new_line == target_line:
        return None

    lines[line_no - 1] = new_line
    return {
        "fixable": True,
        "pattern": "none_slicing",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": "".join(lines),
        "explanation": (
            f"L{line_no}: `dict.get(k, '')[:N]` 패턴이 값이 None 일 때 슬라이싱 실패 → "
            f"`(dict.get(k) or '')[:N]` 안전 패턴으로 변환."
        ),
    }


# ──────────────────────────────────────────────────────────────
# 패턴 3: NameError name 'X' is not defined — 오타 자동 교정
# ──────────────────────────────────────────────────────────────

def _fix_name_typo(error_record: dict) -> Optional[dict]:
    et = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    if et != "NameError":
        return None
    m = re.search(r"name ['\"]([^'\"]+)['\"]\s+is not defined", msg)
    if not m:
        return None
    typo = m.group(1)

    tb = error_record.get("traceback", "") or ""
    file_path = None
    line_no = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
                line_no = int(tm.group(2))
        except ValueError:
            continue
    if not file_path or not file_path.exists() or not line_no:
        return None

    src = file_path.read_text(encoding="utf-8")
    # AST 로 정의된 이름 목록 수집
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add(alias.asname or alias.name.split(".")[0])

    # 유사 이름 후보 (difflib)
    candidates = difflib.get_close_matches(typo, defined, n=1, cutoff=0.7)
    if not candidates:
        return None
    correct = candidates[0]

    lines = src.splitlines(keepends=True)
    if line_no - 1 >= len(lines):
        return None
    target_line = lines[line_no - 1]
    # 단어 경계로 정확히 typo → correct (다른 우연 매치 회피)
    new_line = re.sub(rf'\b{re.escape(typo)}\b', correct, target_line)
    if new_line == target_line:
        return None

    lines[line_no - 1] = new_line
    return {
        "fixable": True,
        "pattern": "name_typo",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": "".join(lines),
        "explanation": f"L{line_no}: `{typo}` 미정의 → 유사 식별자 `{correct}` 로 교정 (오타).",
    }


# ──────────────────────────────────────────────────────────────
# 패턴 4: AttributeError 'NoneType' object has no attribute 'X'
# → 직전 변수가 None 가능성 — `or {}` / `or ""` / 명시적 None 체크 삽입
# ──────────────────────────────────────────────────────────────

def _fix_none_attribute(error_record: dict) -> Optional[dict]:
    et = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    if et != "AttributeError":
        return None
    m = re.search(r"['\"]NoneType['\"] object has no attribute ['\"](\w+)['\"]", msg)
    if not m:
        return None
    attr = m.group(1)

    tb = error_record.get("traceback", "") or ""
    file_path = None
    line_no = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
                line_no = int(tm.group(2))
        except ValueError:
            continue
    if not file_path or not file_path.exists() or not line_no:
        return None

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_no - 1 >= len(lines):
        return None
    target_line = lines[line_no - 1]

    # 패턴: <var>.<attr>(...) 또는 <var>.<attr>
    # 예: scheduler.add_job(...) → if scheduler: scheduler.add_job(...)
    m2 = re.search(rf'(\w+)\.{re.escape(attr)}\b', target_line)
    if not m2:
        return None
    var = m2.group(1)
    if var in ("self", "cls"):   # self/cls 는 None 가능성 낮음 — skip
        return None

    indent = re.match(r'\s*', target_line).group(0)
    # if <var>: 가드 라인 추가 + 원본 라인을 한 단계 들여쓰기
    guard_line = f"{indent}if {var} is not None:\n"
    indented_line = "    " + target_line   # 4-space 추가 들여쓰기
    new_lines = lines[:line_no - 1] + [guard_line, indented_line] + lines[line_no:]

    return {
        "fixable": True,
        "pattern": "none_attribute",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": "".join(new_lines),
        "explanation": (
            f"L{line_no}: `{var}.{attr}` 호출 시 `{var}` 가 None → "
            f"`if {var} is not None:` 가드 자동 삽입."
        ),
    }


# ──────────────────────────────────────────────────────────────
# 패턴 5: ImportError cannot import name 'X' from 'Y'
# → Y 모듈 내 유사 심볼 자동 교정
# ──────────────────────────────────────────────────────────────

def _fix_import_name(error_record: dict) -> Optional[dict]:
    et = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    if et != "ImportError":
        return None
    m = re.search(r"cannot import name ['\"](\w+)['\"]\s+from\s+['\"]([\w\.]+)['\"]", msg)
    if not m:
        return None
    bad_name = m.group(1)
    src_module = m.group(2)

    # src_module 의 파일 경로 추정
    parts = src_module.split(".")
    src_path = _ROOT / Path(*parts).with_suffix(".py")
    if not src_path.exists():
        src_path = _ROOT / Path(*parts) / "__init__.py"
        if not src_path.exists():
            return None

    src_text = src_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src_text)
    except SyntaxError:
        return None

    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and not t.id.startswith("_"):
                    exported.add(t.id)

    candidates = difflib.get_close_matches(bad_name, exported, n=1, cutoff=0.7)
    if not candidates:
        return None
    correct = candidates[0]

    tb = error_record.get("traceback", "") or ""
    file_path = None
    line_no = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
                line_no = int(tm.group(2))
        except ValueError:
            continue
    if not file_path or not file_path.exists() or not line_no:
        return None

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_no - 1 >= len(lines):
        return None
    target_line = lines[line_no - 1]
    new_line = re.sub(rf'\b{re.escape(bad_name)}\b', correct, target_line)
    if new_line == target_line:
        return None
    lines[line_no - 1] = new_line

    return {
        "fixable": True,
        "pattern": "import_name",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": "".join(lines),
        "explanation": (
            f"`{bad_name}` 을 `{src_module}` 에서 import 불가 → "
            f"유사 심볼 `{correct}` 로 교정."
        ),
    }


# ★ 사용자 박제 2026-05-16 (ERRORS [111]) — 튜플 unpack mismatch 자동 fix
def _fix_unpack_mismatch(error_record: dict) -> Optional[dict]:
    """ValueError 'too many/not enough values to unpack (expected N, got M)' 자동 수정.

    원인: 함수 시그니처 변경 (3-tuple → 5-tuple) 후 호출자 일부 누락.
    수정: 호출자의 unpacking 변수 개수를 *정의 측 tuple 개수* 와 동기화.

    예시:
      Before: a, b, c = some_fn()           # 5-tuple 반환인데 3개 unpacking
      After:  a, b, c, d, e = some_fn()     # 함수 정의에서 _4·_5 변수명 추정

    learned_patterns 누적 후 다음 같은 사례 즉시 매칭.
    """
    et = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    if et != "ValueError":
        return None
    # 메시지 패턴 — "too many values to unpack (expected 3)" 또는 "not enough values to unpack (expected 5, got 3)"
    m = re.search(
        r"(?:too many|not enough)\s+values?\s+to\s+unpack\s*\(expected\s+(\d+)(?:,\s*got\s+(\d+))?\)",
        msg, re.IGNORECASE,
    )
    if not m:
        return None
    expected = int(m.group(1))   # 호출자가 *기대* 한 개수 = 현재 unpacking 변수 수
    got = int(m.group(2)) if m.group(2) else None  # 함수가 *실제* 반환한 개수

    # traceback 에서 호출자 파일·라인 추출
    tb = error_record.get("traceback", "") or ""
    file_path = None
    line_no = None
    for tm in re.finditer(r'File "([^"]+)", line (\d+)', tb):
        fp = Path(tm.group(1))
        try:
            fp.relative_to(_ROOT)
            if "__pycache__" not in str(fp) and ".venv" not in str(fp):
                file_path = fp
                line_no = int(tm.group(2))
        except ValueError:
            continue
    if not file_path or not file_path.exists() or not line_no:
        return None

    lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if line_no - 1 >= len(lines):
        return None
    target_line = lines[line_no - 1]

    # unpacking 패턴 파싱 — `var1, var2, ... = func(...)` 또는 `var1, var2, ... = expr`
    unpack_m = re.match(
        r"^(\s*)((?:[\w_]+(?:\s*,\s*[\w_]+)+))\s*=\s*(.+)$",
        target_line.rstrip('\n')
    )
    if not unpack_m:
        return None
    indent = unpack_m.group(1)
    var_str = unpack_m.group(2)
    rhs = unpack_m.group(3)
    current_vars = [v.strip() for v in var_str.split(",")]

    # 현재 unpacking 개수 = expected (메시지의 expected 와 일치해야 함)
    if len(current_vars) != expected:
        return None

    # got 이 알려져 있으면 그것을 새 개수로 사용. 아니면 RHS 함수 호출 추적해서 추론.
    if got is not None:
        new_count = got
    else:
        # "too many values to unpack (expected N)" — got 정보 없음.
        # RHS 가 *함수 호출* 이면 함수 정의 찾아서 return tuple 개수 카운트.
        fn_call_m = re.match(r"([\w_\.]+)\s*\(", rhs.strip())
        if not fn_call_m:
            return None
        fn_name = fn_call_m.group(1).split(".")[-1]
        # 같은 파일·import 모듈에서 함수 정의 검색 → return tuple 개수
        # 단순화: 호출자 파일 안의 def 만 검색 (외부 모듈은 grep 으로 별도 추적 필요)
        src_text = file_path.read_text(encoding="utf-8")
        # import 추적해서 외부 함수 위치 찾기
        from_imports = re.findall(
            rf"from\s+([\w\.]+)\s+import\s+(?:[\w_,\s]+,\s*)?{re.escape(fn_name)}",
            src_text,
        )
        target_module = from_imports[0] if from_imports else None
        if target_module:
            parts = target_module.split(".")
            module_path = _ROOT / Path(*parts).with_suffix(".py")
            if module_path.exists():
                try:
                    mod_text = module_path.read_text(encoding="utf-8")
                    tree = ast.parse(mod_text)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                            # return tuple 개수 카운트 — 마지막 return 문 기준
                            for ret in ast.walk(node):
                                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple):
                                    new_count = len(ret.value.elts)
                                    break
                            else:
                                continue
                            break
                    else:
                        return None
                except SyntaxError:
                    return None
            else:
                return None
        else:
            return None

    if new_count <= expected or new_count <= 0 or new_count > 12:
        # 개수 줄이는 경우는 위험 (기존 변수 사용 코드 깨질 수 있음) — skip
        # 너무 많은 unpacking 도 skip
        return None

    # 새 변수명 생성 — 기존 + `_extra{N}` 또는 단순 `_var{N}`
    extra_vars = [f"_extra{i+1}" for i in range(new_count - expected)]
    new_vars = current_vars + extra_vars
    new_var_str = ", ".join(new_vars)
    new_line = f"{indent}{new_var_str} = {rhs}\n"
    if new_line == target_line:
        return None
    lines[line_no - 1] = new_line

    return {
        "fixable": True,
        "pattern": "unpack_mismatch",
        "target_file": str(file_path.relative_to(_ROOT)),
        "patch_full": "".join(lines),
        "explanation": (
            f"tuple unpack mismatch — 시그니처가 {new_count}-tuple 반환인데 "
            f"호출자는 {expected}개로 unpacking. 부족한 {new_count - expected}개 자리 "
            f"`{', '.join(extra_vars)}` 추가."
        ),
    }


# ──────────────────────────────────────────────────────────────
# 학습 저장소 (learned_patterns.json) — 자동/수동 수정 사례 누적
# ──────────────────────────────────────────────────────────────

_LEARNED_PATH = Path(__file__).parent / "learned_patterns.json"
_LEARNED_LOCK = threading.Lock()

# fixer 이름 → 함수 매핑 (학습 패턴에서 호출용)
_FIXER_REGISTRY = {
    "relative_import": "_fix_relative_import",
    "none_slicing":    "_fix_none_slicing",
    "name_typo":       "_fix_name_typo",
    "none_attribute":  "_fix_none_attribute",
    "import_name":     "_fix_import_name",
    "unpack_mismatch": "_fix_unpack_mismatch",
    "auto_patch":      "_fix_auto_patch",        # ★ git diff 재적용 (LLM 0)
}

# ★ 시맨틱 임베딩 폴백 (정확 fingerprint miss 시) — 2026-07-02 사용자 박제.
#   오탐(다른 오류를 같다 판정 → 엉뚱한 패치 재적용)이 미탐(놓침 → LLM 폴백, 안전)보다
#   훨씬 비싸다 → 임계는 *보수적(높게)*.
#   ★ 실측 캘리브레이션 (입력은 _normalize_message 정규화 텍스트임에 주의):
#     - 폴백은 '정규화 후에도 구조가 다른' 케이스에만 발동 (구조 같으면 exact match 가 선처리).
#     - 같은 error_type 안 different-fixer 쌍(NEGATIVE) cos 최대 ≈0.60,
#       same-fixer 쌍(POSITIVE)은 0.63~1.0 로 분산.
#     → 0.88 은 NEGATIVE 상한(~0.60) 훨씬 위 = 오탐 사실상 0, 대신 저유사 POSITIVE 는
#       안전하게 놓침(LLM 폴백). 마법 숫자 아님 — FP-안전 보수값(측정 근거).
#   3중 방어: ① error_type 완전일치 게이트(AND) ② cos ≥ 임계 ③ (llm/auto_patch 는)
#     _apply_diff_replacements before-text 존재검사 = 4차 방어.
#   env GUARDIAN_SEMANTIC_SIM_MIN 로 튜너블, calibrate_semantic_threshold() 가 실데이터
#   누적 시 NEGATIVE 분포를 재측정해 하향 여지 진단 (고정 추측 아닌 계속 측정되는 값).
import os as _os
_SEMANTIC_SIM_MIN = float(_os.environ.get("GUARDIAN_SEMANTIC_SIM_MIN", "0.88"))
_SEMANTIC_ENABLED = _os.environ.get("GUARDIAN_SEMANTIC_MATCH", "1") != "0"
# 재적용 가능(actionable) fixer 만 시맨틱 재사용 대상 — 정적 6종 + auto_patch + llm_patch
_ACTIONABLE_FIXERS = set(_FIXER_REGISTRY.keys()) | {"llm_patch"}


def _apply_diff_replacements(target_file: str, diff_text: str) -> str | None:
    """unified diff → search-replace 방식으로 파일에 적용. 성공 시 new_content 반환.

    git apply / patch 명령 불필요 — Python 순수 구현.
    hunk 별로 (before_lines, after_lines) 추출 → str.replace(1회).
    before가 현재 파일에 없으면 해당 hunk 스킵 (부분 적용 허용).
    """
    target_path = _ROOT / target_file
    if not target_path.exists():
        return None
    try:
        original = target_path.read_text(encoding="utf-8")
    except Exception:
        return None

    modified = original
    applied  = 0

    for hunk in re.split(r'(?=^@@)', diff_text, flags=re.MULTILINE):
        if not hunk.strip() or not hunk.startswith("@@"):
            continue
        before_lines: list[str] = []
        after_lines:  list[str] = []
        for line in hunk.splitlines(keepends=True):
            if line.startswith("@@"):
                continue
            if line.startswith("-") and not line.startswith("---"):
                before_lines.append(line[1:])
            elif line.startswith("+") and not line.startswith("+++"):
                after_lines.append(line[1:])
        if not before_lines or not after_lines:
            continue
        before = "".join(before_lines)
        after  = "".join(after_lines)
        if before in modified:
            modified = modified.replace(before, after, 1)
            applied += 1

    if applied == 0 or modified == original:
        return None
    return modified


def _fix_auto_patch(error_record: dict):
    """auto_patch fixer placeholder — 실제 처리는 _fix_from_learned 에서 직접 수행."""
    return None


def _normalize_message(msg: str) -> str:
    """message 정규화 — fingerprint 추출용. 변하는 부분은 placeholder.

    ★ Phase C 강화 (사용자 박제 2026-05-15) — hit 률 향상:
      - 메모리 주소 (0x...)
      - line N, char N 위치 정보
      - 날짜·시각 (ISO/한국어)
      - 임시 디렉터리 (/tmp/..., /var/folders/...)
      - 큰 숫자 (PID·timestamp)
    같은 오류라도 *경로·숫자·시각* 만 다르면 같은 fingerprint 로 통합.
    """
    if not msg:
        return ""
    m = msg.strip()
    # 메모리 주소
    m = re.sub(r"0x[0-9a-fA-F]+",          "<ADDR>", m)
    # 시간·날짜
    m = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?", "<TIMESTAMP>", m)
    m = re.sub(r"\d{4}-\d{2}-\d{2}",       "<DATE>",  m)
    m = re.sub(r"\d{2}:\d{2}:\d{2}",       "<TIME>",  m)
    # 위치 정보
    m = re.sub(r"line\s+\d+",              "line <N>", m, flags=re.IGNORECASE)
    m = re.sub(r"col(?:umn)?\s+\d+",       "col <N>",  m, flags=re.IGNORECASE)
    m = re.sub(r"char\s+\d+",              "char <N>", m, flags=re.IGNORECASE)
    # 임시 경로
    m = re.sub(r"/(?:tmp|var/folders/[^/]+)/[\w/\-\.]+", "<TMP_PATH>", m)
    # 파일 경로 — .py·.json·.log·.txt 등
    m = re.sub(r"/[\w/\-\.]+\.(py|json|log|txt|md|yml|yaml|html|css|js)\b",
               r"<PATH>.\1", m)
    # 인용 문자열 — 변수명·식별자
    m = re.sub(r"'[^']+'",                 "'<NAME>'", m)
    m = re.sub(r'"[^"]+"',                 '"<NAME>"', m)
    # 남은 큰 숫자 (4자리+) — PID·timestamp·ID
    m = re.sub(r"\b\d{4,}\b",              "<BIGINT>", m)
    # 일반 숫자
    m = re.sub(r"\b\d+\b",                 "<N>",      m)
    # 공백 정규화
    m = re.sub(r"\s+",                     " ",        m).strip()
    return m[:200]


def _make_fingerprint(error_type: str, message: str) -> str:
    """error_type + normalized message 로 패턴 고유 키."""
    return f"{error_type or 'Unknown'}::{_normalize_message(message or '')}"


def bandit_arm_name(error_record: dict, hit_count: int) -> str:
    """학습 fingerprint 의 밴딧 arm 이름 — 단일 진실 소스.

    _get_new_fixers / _get_verified_fixers 가 만드는 arm 이름과 *정확히* 동일해야
    보상이 다음 랭킹에 반영된다. record_sdk_fix(SDK 경로) + error_fixer.apply_fix(LLM 경로)
    공용 → arm 이름 규칙 드리프트 방지.
    """
    fp = _make_fingerprint(
        error_record.get("error_type", "") or "",
        error_record.get("message", "") or "",
    )
    return f"verified:{fp[:32]}" if hit_count >= _HIGH_COUNT_THRESHOLD else f"new:{fp[:32]}"


def _load_learned() -> dict:
    """learned_patterns.json 로드. 파일 없으면 빈 구조 반환."""
    if not _LEARNED_PATH.exists():
        return {"version": "1.0", "patterns": []}
    try:
        return json.loads(_LEARNED_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[GUARDIAN/learned] 로드 실패: {e}")
        return {"version": "1.0", "patterns": []}


def _save_learned(data: dict) -> None:
    """learned_patterns.json 저장 (스레드 안전)."""
    try:
        _LEARNED_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.warning(f"[GUARDIAN/learned] 저장 실패: {e}")


def _fix_from_learned(error_record: dict, min_hit_count: int = 0) -> Optional[dict]:
    """★ 학습된 fingerprint 와 매칭되면 즉시 수정 반환 (LLM 호출 0).

    매칭 흐름:
      1. (error_type, normalized message) fingerprint 생성
      2. learned_patterns.json 에서 동일 fingerprint 검색
      3a. fixer == "llm_patch" → 저장된 patch/target_file 직접 반환 (LLM 재호출 0)
      3b. 그 외 → 등록된 fixer 함수 호출
      4. 미매칭이면 None 반환 (정적 5종 패턴으로 fallback)

    min_hit_count: 이 값 이상인 패턴만 시도 (0 = 전체, 5 = 고빈도 승격 패턴만)
    """
    et  = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""
    fp  = _make_fingerprint(et, msg)

    data = _load_learned()
    matched = None
    for p in data.get("patterns", []):
        # hit_count 필터 (고빈도 승격 전용 호출 시)
        if int(p.get("hit_count", 0)) < min_hit_count:
            continue
        # 정확 매칭 (fingerprint 동일) 우선
        if p.get("fingerprint") == fp:
            matched = p
            break
        # 부분 매칭 (error_type + message_pattern regex)
        if p.get("error_type") == et:
            try:
                if re.search(p.get("message_pattern", ""), msg):
                    matched = p
                    break
            except re.error:
                continue

    # ★ 정확·정규식 매칭 miss → 시맨틱 임베딩 폴백 (오탐 방지 3중 게이트)
    if not matched:
        matched = _semantic_fallback_match(et, msg, data, min_hit_count)
    if not matched:
        return None

    fixer_name = matched.get("fixer")
    if not fixer_name:
        return None

    # ── llm_patch: 저장된 패치 직접 반환 (LLM 재호출 없음) ──────
    if fixer_name == "llm_patch":
        stored_patch  = matched.get("stored_patch", "")
        stored_target = matched.get("stored_target_file", "")
        if not stored_patch or not stored_target:
            log.debug("[GUARDIAN/learned] llm_patch 패치 없음 — LLM fallback")
            return None
        # stored_patch 가 unified diff 형식이면 diff 적용, 아니면 full-file 덮어쓰기
        if stored_patch.lstrip().startswith(("---", "@@", "diff ")):
            new_content = _apply_diff_replacements(stored_target, stored_patch)
            if new_content is None:
                log.debug("[GUARDIAN/learned] llm_patch diff 적용 불가 — LLM fallback")
                return None
            patch_to_use = new_content
        else:
            patch_to_use = stored_patch  # 기존 full-file 방식 (backward compat)
        result = {
            "fixable":     True,
            "target_file": stored_target,
            "patch":       patch_to_use,
            "explanation": f"학습 캐시 재적용 — {matched.get('fingerprint','')[:60]}",
            "pattern":     "llm_patch",
            "source":      "learned_cache",
        }
        _bump_hit_count(data, matched.get("fingerprint"))
        result["learned"] = True
        result["fingerprint"] = matched.get("fingerprint")
        log.info(
            f"[GUARDIAN/learned] ★ LLM 패치 캐시 적용 — fp='{fp[:70]}' "
            f"hit_count={matched.get('hit_count', 0) + 1}"
        )
        return result

    # ── auto_patch: git diff → search-replace 적용 (LLM 재호출 없음) ──
    if fixer_name == "auto_patch":
        stored_diff   = matched.get("stored_patch", "")
        stored_target = matched.get("stored_target_file", "")
        if not stored_diff or not stored_target:
            log.debug("[GUARDIAN/learned] auto_patch 없음 — fallback")
            return None
        new_content = _apply_diff_replacements(stored_target, stored_diff)
        if new_content is None:
            log.debug(f"[GUARDIAN/learned] auto_patch diff 적용 불가 ({stored_target}) — fallback")
            return None
        _bump_hit_count(data, matched.get("fingerprint"))
        result = {
            "fixable":     True,
            "target_file": stored_target,
            "patch":       new_content,
            "explanation": f"auto_patch 재적용 (LLM 0) — {stored_target}",
            "pattern":     "auto_patch",
            "source":      "learned_cache",
        }
        result["learned"] = True
        result["fingerprint"] = matched.get("fingerprint")
        log.info(
            f"[GUARDIAN/learned] ★ auto_patch 적용 — {stored_target} "
            f"hit_count={matched.get('hit_count', 0) + 1}"
        )
        return result

    # ── 정적 fixer 함수 호출 ─────────────────────────────────────
    if fixer_name not in _FIXER_REGISTRY:
        return None
    fn_name = _FIXER_REGISTRY[fixer_name]
    fn = globals().get(fn_name)
    if not fn:
        return None

    try:
        result = fn(error_record)
    except Exception as e:
        log.debug(f"[GUARDIAN/learned] {fn_name} 실행 실패: {e}")
        return None

    if not result:
        return None

    _bump_hit_count(data, matched.get("fingerprint"))

    result["learned"] = True
    result["fingerprint"] = matched.get("fingerprint")
    log.info(
        f"[GUARDIAN/learned] ★ 학습 매칭 — fingerprint='{fp[:70]}' "
        f"fixer={fixer_name} hit_count={matched.get('hit_count',0)+1}"
    )
    return result


def _bump_hit_count(data: dict, fingerprint: str) -> None:
    """hit_count 증가 + last_seen 갱신 (공통 헬퍼).

    호출자가 이미 `_load_learned()` 로 읽어 둔 `data` 를 전달 — 중복 디스크 읽기 회피.
    """
    from datetime import datetime
    with _LEARNED_LOCK:
        for p in data.get("patterns", []):
            if p.get("fingerprint") == fingerprint:
                p["hit_count"] = int(p.get("hit_count", 0)) + 1
                p["last_seen"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                break
        _save_learned(data)


def _semantic_fallback_match(et: str, msg: str, data: dict, min_hit_count: int) -> Optional[dict]:
    """정확 fingerprint miss 시 임베딩 코사인 유사도로 후보 재사용. 오탐 방지 다중 게이트.

    게이트: ① kill-switch/임베딩 가용 ② error_type 완전일치 + embedding 보유 + actionable
    + hit_count ③ cos ≥ _SEMANTIC_SIM_MIN(실측 근거). 반환된 matched 는 기존 apply 로직을
    그대로 타므로 llm/auto_patch 는 before-text 존재검사가 4차 방어가 된다.
    """
    if not _SEMANTIC_ENABLED:
        return None
    try:
        from shared import embeddings as _emb
    except Exception:
        return None
    if not _emb.available():
        return None
    # 후보 사전 필터 — error_type 완전일치 + embedding 보유 + actionable + hit_count
    cands = [p for p in data.get("patterns", [])
             if p.get("error_type") == et
             and p.get("embedding")
             and p.get("fixer") in _ACTIONABLE_FIXERS
             and int(p.get("hit_count", 0)) >= min_hit_count]
    if not cands:
        return None   # 후보 0 → 모델 로드조차 안 함 (성능)
    q = _emb.encode(_normalize_message(msg))
    if not q:
        return None
    best, best_sim = None, 0.0
    for p in cands:
        sim = _emb.cosine(q, p["embedding"])
        if sim > best_sim:
            best, best_sim = p, sim
    if best is None or best_sim < _SEMANTIC_SIM_MIN:
        return None
    log.info(f"[GUARDIAN/learned] ★ 시맨틱 폴백 매칭 sim={best_sim:.3f} "
             f"et={et} fp='{best.get('fingerprint','')[:50]}' fixer={best.get('fixer')}")
    best = dict(best)          # 원본 오염 방지 (표식만 추가)
    best["_semantic_sim"] = round(best_sim, 4)
    return best


def backfill_embeddings() -> int:
    """cold-start: embedding 없는 actionable 패턴에 fingerprint→normalized_message 복원 후 채움.

    기존 learned_patterns 는 임베딩이 없어 시맨틱 폴백이 아무것도 못 찾음 → 1회 채우면
    이후 재사용 가능. 미채움 상태여도 기존 정확매칭 경로는 100% 무해 동작. idempotent.
    """
    try:
        from shared import embeddings as _emb
    except Exception:
        return 0
    if not _emb.available():
        return 0
    n = 0
    with _LEARNED_LOCK:
        data = _load_learned()
        for p in data.get("patterns", []):
            if p.get("embedding") or p.get("fixer") not in _ACTIONABLE_FIXERS:
                continue
            parts = p.get("fingerprint", "").split("::", 1)
            norm = parts[1] if len(parts) == 2 else ""
            if not norm:
                continue
            vec = _emb.encode(norm)
            if vec:
                p["embedding"] = [round(float(x), 5) for x in vec]
                n += 1
        if n:
            _save_learned(data)
    log.info(f"[GUARDIAN/learned] backfill_embeddings — {n}개 패턴 임베딩 채움")
    return n


def calibrate_semantic_threshold() -> dict:
    """실데이터로 시맨틱 임계값 재측정 — '0.88 이 여전히 안전한가' 진단 (자동 적용 안 함).

    같은 error_type 안에서 패턴 임베딩 쌍의 코사인 분포를 재고, 대부분이 서로 *다른*
    근본원인(negative proxy)이라는 전제 하에 그 분포의 상위 백분위 + 여유를 제안 임계로
    보고. 고정 추측이 아니라 *계속 측정되는* 값으로 만드는 게 목적.
    """
    try:
        import numpy as _np
        from shared import embeddings as _emb
    except Exception as e:  # noqa: BLE001
        return {"error": f"의존성 미가용: {e}"}
    if not _emb.available():
        return {"error": "임베딩 모델 미가용"}
    data = _load_learned()
    by_type: dict[str, list] = {}
    for p in data.get("patterns", []):
        if p.get("embedding") and p.get("fixer") in _ACTIONABLE_FIXERS:
            by_type.setdefault(p.get("error_type", ""), []).append(p["embedding"])
    sims: list[float] = []
    for _et, vecs in by_type.items():
        for i in range(len(vecs)):
            for j in range(i + 1, len(vecs)):
                sims.append(_emb.cosine(vecs[i], vecs[j]))
    if len(sims) < 3:
        return {"pairs": len(sims), "note": "표본 부족 — 현재 임계 유지", "current": _SEMANTIC_SIM_MIN}
    arr = _np.array(sims)
    p50, p90, p95, mx = (float(_np.percentile(arr, q)) for q in (50, 90, 95, 100))
    suggested = round(min(0.95, max(0.85, p95 + 0.03)), 3)   # 상위 분포 위 + 여유, [0.85,0.95] 클램프
    return {
        "pairs": len(sims), "current": _SEMANTIC_SIM_MIN,
        "cos_p50": round(p50, 3), "cos_p90": round(p90, 3),
        "cos_p95": round(p95, 3), "cos_max": round(mx, 3),
        "suggested_min": suggested,
    }


# ★ 합성 지문 타입 — 재발 불가라 학습 자산이 될 수 없다 (ERRORS [479])
#   "무엇을 고쳤다" 는 작업 기록일 뿐, "무엇이 실패했다" 는 오류 지문이 아니다.
_SYNTHETIC_TYPES = frozenset({"AutoRepairFix"})


def record_pattern_hit(
    error_record: dict,
    fixer_name: str,
    fixed_file: str = "",
    source: str = "auto",
    patch: str = "",
    target_file: str = "",
) -> int:
    """자동/수동 수정 성공 시 learned_patterns 에 fingerprint 등록·누적.

    Returns:
        int: 등록·갱신된 패턴의 hit_count (skip·eval 거부 시 0).
             ★ record_sdk_fix 가 이 값으로 밴딧 arm(new:/verified:) 을 결정.

    정적 5종 fixer 가 처리한 케이스도 학습 데이터로 박제 → 통계 + 향후 회귀 검증.
    fixer_name == "llm_patch" 일 때 patch / target_file 를 함께 저장 →
    2번째 동일 오류 발생 시 _fix_from_learned 가 LLM 재호출 없이 즉시 재적용.

    ★ 노이즈 게이트 (Phase A 강화, 사용자 박제 2026-05-15):
      - fixer_name 비어있음 → 매칭해도 재현 불가 → 등록 SKIP
      - error_type + normalized_message 모두 비어있음 → fingerprint 무의미 → SKIP
      - 정책 작업 박제 (PromptLeak, RuleConsolidation 등) message 비어있을 때 → SKIP
        (런타임 오류 패턴이 아닌 *수동 정책 변경 박제* — 재발 자체가 의미 없음)
    """
    et  = error_record.get("error_type", "")
    msg = error_record.get("message", "") or ""

    # ★ 노이즈 게이트 1: fixer_name 없으면 *재현 불가 패턴* → 등록 skip
    if not fixer_name or not str(fixer_name).strip():
        log.info(f"[GUARDIAN/learned] skip — fixer_name 없음 (et={et}, source={source})")
        return 0

    # ★ 노이즈 게이트 2: error_type + message 모두 빈 케이스 → fingerprint 무의미
    norm = _normalize_message(msg)
    if not et and not norm:
        log.info(f"[GUARDIAN/learned] skip — error_type/message 둘 다 빈 채로 학습 시도")
        return 0

    # ★ 노이즈 게이트 4: *합성 지문* — 재발할 수 없으므로 학습 자산이 아니다 (ERRORS [479])
    #   `AutoRepairFix::<파일경로>` 는 "auto_repair 가 이 파일을 고쳤다" 는 *작업 기록* 이지
    #   런타임에 *다시 발생할 수 있는 오류의 지문* 이 아니다. 파일 경로는 매번 달라지고,
    #   같은 경로가 또 온다 해도 그때의 오류는 전혀 다른 것이다.
    #   → 등록해봐야 영원히 재사용되지 않고, "학습 패턴 N개·LLM 절약 N회" 만 부풀린다.
    #   실측 2026-07-22: 51개 중 8개(15%)가 이것이었고, 적중 상위 5개 중 4개를 차지했다.
    #   CLAUDE.md/ADR 005 가 "Tier 2 도 *실제 오류 지문* 으로 학습(AutoRepairFix 합성 지문 아님)"
    #   이라고 못박은 그대로다. 정본 경로(primary_rel)는 실제 오류 지문으로 이미 등록된다.
    if et in _SYNTHETIC_TYPES:
        log.info(f"[GUARDIAN/learned] skip — 합성 지문(재발 불가) et={et} src={source}")
        return 0

    # ★ 노이즈 게이트 3: *프로젝트 정책 작업 박제* (메시지 없는 사용자 박제)
    # — 런타임 오류 패턴이 아니라 *일회성 작업 이력*. 학습 대상 아님.
    _POLICY_TYPES = {
        "PromptLeak", "RuleConsolidation", "SupremeBlockStatic", "RuleAddition",
        "FlowDefect", "SandboxLeak", "DashboardFilter", "StatusEnumStandard",
        "AgentAddition", "AutoFixCapability", "ManualFixTracking",
        "ModelInconsistency", "ModelCatalogUpgrade", "ModelUpgradeSonnet",
        "ClaudeCodeCLIModelLock", "ArchitectModelUpgrade", "FolderMigrationFlat",
        "LengthPhraseUnification", "TistoryRedirectLeak", "TistoryStuckBypass",
        "ThumbnailVariationToken", "SpacerStyleEnforce", "GuardianPendingSweep",
        "OldFileCleanup",
    }
    if et in _POLICY_TYPES and not norm:
        log.info(f"[GUARDIAN/learned] skip — 정책 작업 박제 (et={et}, 재현 불가)")
        return 0

    # ★ 노이즈 게이트 4 (사용자 박제 2026-07-04): actionable fixer 만 학습 등록.
    #   변경추적·정책 이벤트(GitCommit·ExternalEdit·PolicyChange·ArchitectureChange…)는
    #   fixer 가 registry/llm_patch 에 없음 → _fix_from_learned 가 절대 적용 못 하는 *죽은 패턴*.
    #   등록해도 재적용 불가 + 밴딧 arm 오염(무한 증식)만 유발 → 여기서 단일 초크포인트로 차단.
    #   (error_log status='manual' 변경추적 기록은 그대로 유지 — 작업량 카드 불변.)
    if fixer_name not in _ACTIONABLE_FIXERS:
        log.info(f"[GUARDIAN/learned] skip — 비-actionable fixer '{fixer_name}' "
                 f"(et={et}) → 변경추적/정책은 학습·밴딧 대상 아님")
        return 0

    # ★ A모델 분리 (ADR 007) — eval_agent 학습 자산화 게이트
    # 노이즈 게이트 3종 통과 후 *정밀 평가* 단계. 정적 fixer 는 자동 통과,
    # llm_patch 는 Sonnet 5 로 안전성·정확성·재사용 가치 채점.
    try:
        from JARVIS07_GUARDIAN import eval_agent as _eval_mod
        _eval = _eval_mod.evaluate(
            error_record, fixer_name,
            patch=patch, target_file=target_file or fixed_file,
        )
        if not _eval.should_register:
            log.info(
                f"[GUARDIAN/learned] eval 거부 — score={_eval.score} "
                f"safe={_eval.safe} acc={_eval.accurate} : {_eval.rationale}"
            )
            return 0
        _eval_meta = _eval_mod.to_meta(_eval)
    except Exception as e:
        # eval_agent 자체 실패 → 보수적 통과 (기존 동작 유지, 학습 중단 방지)
        log.warning(f"[GUARDIAN/learned] eval_agent 호출 실패 → 보수적 통과: {e}")
        _eval_meta = None

    # ★ ADR 008 Phase 4 — 도메인 자동 추정 (사용자 박제 2026-05-17)
    _domain = _infer_domain(
        fixed_file=fixed_file, error_type=et, fixer_name=fixer_name,
        message=msg, target_file=target_file,
    )

    # tier 결정: static / llm / auto_patch / manual
    if fixer_name == "llm_patch":
        _tier = "llm"
    elif fixer_name == "auto_patch":
        _tier = "auto_patch"
    elif fixer_name in _FIXER_REGISTRY:
        _tier = "static"
    else:
        _tier = "manual"

    fp  = _make_fingerprint(et, msg)

    # message_pattern 생성 (error_type 별 분기)
    if et == "ModuleNotFoundError":
        msg_pat = r"No module named ['\"]([^'\"]+)['\"]"
    elif et == "ImportError" and "cannot import name" in msg:
        msg_pat = r"cannot import name ['\"](\w+)['\"]\s+from"
    elif et == "TypeError" and "subscript" in msg:
        msg_pat = r"'NoneType' object is not subscriptable"
    elif et == "NameError":
        msg_pat = r"name ['\"]([^'\"]+)['\"]\s+is not defined"
    elif et == "AttributeError" and "NoneType" in msg:
        msg_pat = r"'NoneType' object has no attribute ['\"](\w+)['\"]"
    else:
        # 일반화 어려운 경우 — message 자체 (부분)
        msg_pat = re.escape(msg[:80])

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    _result_hits = 0
    with _LEARNED_LOCK:
        data = _load_learned()
        found = False
        for p in data.get("patterns", []):
            if p.get("fingerprint") == fp:
                p["hit_count"] = int(p.get("hit_count", 0)) + 1
                p["last_seen"] = now
                _result_hits = p["hit_count"]
                # ★ 고빈도 승격 알림 — hit_count 가 임계값 도달 시 1회 로그
                if p["hit_count"] == _HIGH_COUNT_THRESHOLD:
                    log.info(
                        f"[GUARDIAN/learned] ★ 정적 패턴 승격 — "
                        f"fp='{fp[:60]}' hit_count={p['hit_count']} "
                        f"fixer={fixer_name} → _fix_from_high_count 로 처리됨"
                    )
                # llm_patch / auto_patch: 최신 패치로 갱신
                if fixer_name in ("llm_patch", "auto_patch") and patch:
                    p["stored_patch"]       = patch
                    p["stored_target_file"] = target_file or fixed_file
                # ★ eval_meta 갱신 (A모델, ADR 007)
                if _eval_meta is not None:
                    p["eval_meta"] = _eval_meta
                # ★ domain 갱신 (ADR 008 Phase 4) — 더 정확한 시그널 발견 시
                if p.get("domain") in (None, "unknown") and _domain != "unknown":
                    p["domain"] = _domain
                elif "domain" not in p:
                    p["domain"] = _domain
                # tier 갱신 (없거나 unknown인 경우만)
                if p.get("tier") in (None, "unknown"):
                    p["tier"] = _tier
                # 새 example 추가 (중복 방지)
                ex = {"fixed_file": fixed_file, "source": source, "ts": now}
                examples = p.setdefault("examples", [])
                if not any(e.get("fixed_file") == fixed_file for e in examples[-5:]):
                    examples.append(ex)
                    if len(examples) > 10:
                        examples[:] = examples[-10:]   # 최근 10개만
                found = True
                break
        if not found:
            entry = {
                "fingerprint":     fp,
                "error_type":      et,
                "message_pattern": msg_pat,
                "fixer":           fixer_name,
                "tier":            _tier,     # ★ static/llm/manual 분류
                "domain":          _domain,   # ★ ADR 008 Phase 4 — 도메인 카테고리
                "examples":        [{"fixed_file": fixed_file, "source": source, "ts": now}],
                "hit_count":       1,
                "first_seen":      now,
                "last_seen":       now,
            }
            # llm_patch / auto_patch: 패치 저장 → 재발 시 LLM 재호출 없이 즉시 적용
            if fixer_name in ("llm_patch", "auto_patch") and patch:
                entry["stored_patch"]       = patch
                entry["stored_target_file"] = target_file or fixed_file
            # ★ eval_meta 박제 (A모델, ADR 007)
            if _eval_meta is not None:
                entry["eval_meta"] = _eval_meta
            # ★ 시맨틱 폴백용 임베딩 저장 (actionable 패턴만 — 재적용 가능·오탐 방지)
            if fixer_name in _ACTIONABLE_FIXERS:
                try:
                    from shared import embeddings as _emb
                    _vec = _emb.encode(norm)   # norm = _normalize_message(msg) (fingerprint 동일)
                    if _vec:
                        entry["embedding"] = [round(float(x), 5) for x in _vec]
                except Exception as _e:  # noqa: BLE001
                    log.debug(f"[GUARDIAN/learned] 임베딩 저장 skip: {_e}")
            data.setdefault("patterns", []).append(entry)
            _result_hits = 1
            log.info(f"[GUARDIAN/learned] ★ 신규 패턴 등록 — fp='{fp[:70]}' fixer={fixer_name} tier={_tier} domain={_domain}")
        data["patterns"].sort(key=lambda x: -int(x.get("hit_count", 0)))
        _save_learned(data)
    return _result_hits


def record_sdk_fix(
    error_record: dict,
    diffs_by_file: dict[str, str],
    source: str = "auto-sdk-targeted",
) -> int:
    """★ SDK(auto_repair) 반응형 자동수정 성공 → 밴딧 학습 브리지 (사용자 박제 2026-06-28).

    Claude Agent SDK 가 오류 1건을 고쳤을 때 그 결과를 *밴딧·learned_patterns 학습 자산* 으로 전환한다.
    기존 auto_repair 는 record_external_change(manual tier, 밴딧 보상 0) 만 호출 →
    SDK 가 아무리 고쳐도 밴딧이 비대해지지 않던 *단절 지점* 을 연결한다.

    흐름:
      ① 대표 변경 파일 diff 를 *원본 오류 fingerprint* 로 ``llm_patch`` 등록
         → eval_agent 진짜 LLM 채점(safe·accurate·score≥80) 통과분만 stored_patch 박제
         → 재발 시 Tier-1 이 LLM 0 으로 재적용
      ② 등록 성공 시 해당 fingerprint arm 에 bandit 양의 보상 → 다음 동일·유사 오류에서 우선 시도 (밴딧 비대화)
      ③ 나머지 변경 파일은 ``auto_patch`` 로 best-effort 저장

    Args:
        error_record  : 원본 오류 레코드 (error_type·message → fingerprint)
        diffs_by_file : {rel_path: unified_diff_text} — SDK 가 변경한 파일들
        source        : 학습 출처 라벨

    Returns:
        int: 1 (등록·보상 성공) / 0 (변경 없음 또는 eval 게이트 거부)
    """
    if not error_record or not diffs_by_file:
        return 0
    et = error_record.get("error_type", "") or ""

    # 대표 파일 = diff 변경량 최대 — 단일 fingerprint↔단일 stored_patch 스키마 정합
    primary_rel  = max(diffs_by_file, key=lambda k: len(diffs_by_file[k] or ""))
    primary_diff = diffs_by_file[primary_rel]

    hits = record_pattern_hit(
        error_record,
        fixer_name="llm_patch",      # ★ eval 진짜 채점 + 재적용 가능 경로 (auto_patch/manual 은 보수적 통과 70)
        fixed_file=primary_rel,
        source=source,
        patch=primary_diff,
        target_file=primary_rel,
    )
    if hits <= 0:
        # eval 게이트 거부 또는 노이즈 → 학습·보상 안 함 (밴딧 오염 방지)
        log.info(f"[GUARDIAN/sdk-bridge] 등록 보류 — eval 게이트 미통과 (et={et})")
        return 0

    # ── 밴딧 양의 보상 — bandit_arm_name 단일 진실 소스 (arm 이름 규칙 드리프트 방지) ──
    arm = bandit_arm_name(error_record, hits)
    try:
        from JARVIS07_GUARDIAN.bandit import reward as _bandit_reward
        _bandit_reward(et, arm, success=True, error_record=error_record)
        log.info(f"[GUARDIAN/sdk-bridge] ★ 밴딧 보상 — arm={arm} hits={hits} (SDK 수정 학습 자산화)")
    except Exception as e:
        log.debug(f"[GUARDIAN/sdk-bridge] 밴딧 보상 실패: {e}")

    # ── 나머지 변경 파일 best-effort 저장 (재발 LLM-0) ──
    for rel, diff in diffs_by_file.items():
        if rel == primary_rel or not diff:
            continue
        try:
            record_pattern_hit(
                {"error_type": "AutoRepairFix", "message": rel, "source": source},
                fixer_name="auto_patch",
                fixed_file=rel, source=source,
                patch=diff, target_file=rel,
            )
        except Exception:
            pass
    return 1


def stats() -> dict:
    """학습 패턴 통계 — 텔레그램·웹 대시보드 표시용.

    ★ ADR 008 Phase 4 (사용자 박제 2026-05-17) — by_domain 추가.
    """
    data = _load_learned()
    pats = data.get("patterns", [])
    # 도메인별 패턴 수 + hit 합산
    by_domain_count: dict[str, int] = {}
    by_domain_hits:  dict[str, int] = {}
    by_tier:         dict[str, int] = {}
    for p in pats:
        d = p.get("domain") or "unknown"
        by_domain_count[d] = by_domain_count.get(d, 0) + 1
        by_domain_hits[d]  = by_domain_hits.get(d, 0) + int(p.get("hit_count", 0))
        t = p.get("tier") or "unknown"
        by_tier[t] = by_tier.get(t, 0) + 1
    # 자동 수정 가능 패턴: static fixer / llm_patch / auto_patch
    _actionable_fixers = set(list(_FIXER_REGISTRY.keys()) + ["llm_patch", "auto_patch"])
    actionable = sum(
        1 for p in pats if p.get("fixer") in _actionable_fixers
    )
    actionable_hits = sum(
        int(p.get("hit_count", 0)) for p in pats if p.get("fixer") in _actionable_fixers
    )
    return {
        "total_patterns":    len(pats),
        "total_hits":        sum(int(p.get("hit_count", 0)) for p in pats),
        "actionable":        actionable,        # 자동 수정 가능 패턴 수
        "actionable_hits":   actionable_hits,   # 그 중 실제 hit된 횟수
        "by_fixer":          {
            fx: sum(1 for p in pats if p.get("fixer") == fx)
            for fx in list(_FIXER_REGISTRY.keys()) + [None]
        },
        "by_tier":           by_tier,           # ★ static/llm/manual 분포
        "by_domain":         by_domain_count,   # ★ ADR 008 Phase 4
        "by_domain_hits":    by_domain_hits,    # ★ ADR 008 Phase 4
        "top5":              pats[:5],
    }


def apply_stored_patches() -> int:
    """Claude Code SDK 호출 전 1순위 — learned_patterns.json 의 auto_patch/llm_patch 전수 재적용.

    ★ 사용자 박제 2026-05-31 — 스케줄 스캔(auto_repair) 에서 Claude Code SDK 보다 먼저 실행.
    이미 알려진 패치를 먼저 적용하여 자가 수정 비율 향상 + Claude 작업량 감소.

    적용 조건:
      - diff 의 before-context 가 현재 파일에 존재할 때만 (회귀 감지)
      - _apply_diff_replacements 가 None 반환 시 skip (이미 수정됐거나 context 불일치)

    Returns: 적용 성공 건수
    """
    import ast as _ast
    import shutil as _shutil

    data = _load_learned()
    patterns = data.get("patterns", [])

    applied = 0
    bumped: list[str] = []

    for entry in patterns:
        fixer = entry.get("fixer")
        if fixer not in ("auto_patch", "llm_patch"):
            continue
        stored_diff = entry.get("stored_patch", "")
        target_rel  = entry.get("stored_target_file", "")
        if not stored_diff or not target_rel:
            continue
        target_path = _ROOT / target_rel
        if not target_path.exists():
            continue

        new_content = _apply_diff_replacements(target_rel, stored_diff)
        if new_content is None:
            continue  # context 불일치 → skip

        bak = target_path.with_suffix(target_path.suffix + ".bak")
        _shutil.copy2(target_path, bak)
        try:
            target_path.write_text(new_content, encoding="utf-8")
            if target_path.suffix == ".py":
                _ast.parse(new_content)
            applied += 1
            bumped.append(entry.get("fingerprint", ""))
            log.info(f"[GUARDIAN/patch] ★ 학습 패치 재적용: {target_rel} ({fixer})")
            bak.unlink(missing_ok=True)
            # ★ 재적용 성공 → 밴딧 양의 보상 (재발→재적용→reward 사슬 완성, 2026-07-02).
            #   arm 은 *저장된 fingerprint* 로 직접 계산 — bandit_arm_name 재계산 시
            #   message_pattern(정규식)으로 fp 가 어긋나 다른 arm 에 보상되는 것 방지.
            try:
                from JARVIS07_GUARDIAN.bandit import reward as _bandit_reward
                _fp  = entry.get("fingerprint", "")
                _hc  = int(entry.get("hit_count", 0) or 0)
                _arm = (f"verified:{_fp[:32]}" if _hc >= _HIGH_COUNT_THRESHOLD
                        else f"new:{_fp[:32]}")
                _bandit_reward(
                    entry.get("error_type", "") or "", _arm, success=True,
                    error_record={"error_type": entry.get("error_type", "") or "",
                                  "message": entry.get("message_pattern", "") or "",
                                  "module": target_rel},
                )
            except Exception as _re:
                log.debug(f"[GUARDIAN/patch] 재적용 보상 실패: {_re}")
        except Exception as _e:
            _shutil.copy2(bak, target_path)
            bak.unlink(missing_ok=True)
            log.debug(f"[GUARDIAN/patch] 패치 검증 실패 ({target_rel}): {_e}")

    if bumped:
        fresh = _load_learned()
        for fp in bumped:
            if fp:
                _bump_hit_count(fresh, fp)
        _save_learned(fresh)

    return applied


def backfill_domains() -> dict:
    """기존 learned_patterns.json 의 entry 에 domain 필드 backfill.

    ★ ADR 008 Phase 4 (사용자 박제 2026-05-17) — 일회성 마이그레이션.
    domain 필드가 *없는* entry 만 추정·저장. 이미 있는 건 변경 안 함.

    Returns:
        {"total": int, "updated": int, "by_domain": dict, "before_unknown": int}
    """
    with _LEARNED_LOCK:
        data = _load_learned()
        pats = data.get("patterns", [])
        updated = 0
        before_unknown = sum(1 for p in pats if p.get("domain") in (None, "unknown"))
        for p in pats:
            if "domain" in p and p["domain"] not in (None, "unknown"):
                continue
            # examples[].fixed_file 신호로 추정
            fixed = ""
            for ex in p.get("examples", []):
                if ex.get("fixed_file"):
                    fixed = ex["fixed_file"]
                    break
            _domain = _infer_domain(
                fixed_file=fixed,
                error_type=p.get("error_type", ""),
                fixer_name=p.get("fixer", ""),
                message=p.get("message_pattern", ""),
                target_file=p.get("stored_target_file", ""),
            )
            p["domain"] = _domain
            updated += 1
        _save_learned(data)
    return {
        "total":          len(pats),
        "updated":        updated,
        "before_unknown": before_unknown,
        "by_domain":      {d: sum(1 for p in pats if (p.get("domain") or "unknown") == d)
                            for d in sorted({(p.get("domain") or "unknown") for p in pats})},
    }


def backfill_tiers() -> dict:
    """기존 learned_patterns.json 의 entry 에 tier 필드 백필.

    tier 없거나 'unknown' 인 entry 만 처리.
    fixer == 'llm_patch' → 'llm', fixer in _FIXER_REGISTRY → 'static', 그 외 → 'manual'
    """
    with _LEARNED_LOCK:
        data = _load_learned()
        pats = data.get("patterns", [])
        updated = 0
        for p in pats:
            if p.get("tier") not in (None, "unknown"):
                continue
            fn = p.get("fixer", "")
            if fn == "llm_patch":
                p["tier"] = "llm"
            elif fn == "auto_patch":
                p["tier"] = "auto_patch"
            elif fn in _FIXER_REGISTRY:
                p["tier"] = "static"
            else:
                p["tier"] = "manual"
            updated += 1
        _save_learned(data)
    by_tier = {}
    for p in pats:
        t = p.get("tier", "unknown")
        by_tier[t] = by_tier.get(t, 0) + 1
    return {"total": len(pats), "updated": updated, "by_tier": by_tier}


# ──────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────

# ★ 학습 패턴이 최우선 — 동일 사례 즉시 매칭
# 정적 5종은 학습되지 않은 새 패턴 처리용
def _apply_single_pattern(
    pattern: dict,
    error_record: dict,
    data: Optional[dict] = None,
) -> Optional[dict]:
    """매칭된 학습 패턴 dict 를 error_record 에 적용 — 공통 적용 로직.

    _fix_from_learned 내부 로직 + 승격 fixer 클로저에서 재사용.
    data: 이미 로드된 learned data (None 이면 내부 로드).
    """
    fixer_name = pattern.get("fixer")
    if not fixer_name:
        return None
    fp = pattern.get("fingerprint", "")

    if data is None:
        data = _load_learned()

    # ── llm_patch ──────────────────────────────────────────────────
    if fixer_name == "llm_patch":
        stored_patch  = pattern.get("stored_patch", "")
        stored_target = pattern.get("stored_target_file", "")
        if not stored_patch or not stored_target:
            return None
        if stored_patch.lstrip().startswith(("---", "@@", "diff ")):
            new_content = _apply_diff_replacements(stored_target, stored_patch)
            if new_content is None:
                return None
            patch_to_use = new_content
        else:
            patch_to_use = stored_patch
        _bump_hit_count(data, fp)
        return {
            "fixable":     True,
            "target_file": stored_target,
            "patch":       patch_to_use,
            "explanation": f"학습 캐시 재적용 — {fp[:60]}",
            "pattern":     "llm_patch",
            "source":      "learned_cache",
            "learned":     True,
            "fingerprint": fp,
        }

    # ── auto_patch ─────────────────────────────────────────────────
    if fixer_name == "auto_patch":
        stored_diff   = pattern.get("stored_patch", "")
        stored_target = pattern.get("stored_target_file", "")
        if not stored_diff or not stored_target:
            return None
        new_content = _apply_diff_replacements(stored_target, stored_diff)
        if new_content is None:
            return None
        _bump_hit_count(data, fp)
        return {
            "fixable":     True,
            "target_file": stored_target,
            "patch":       new_content,
            "explanation": f"auto_patch 재적용 — {stored_target}",
            "pattern":     "auto_patch",
            "source":      "learned_cache",
            "learned":     True,
            "fingerprint": fp,
        }

    # ── 정적 fixer 함수 ─────────────────────────────────────────────
    if fixer_name not in _FIXER_REGISTRY:
        return None
    fn_name = _FIXER_REGISTRY[fixer_name]
    fn = globals().get(fn_name)
    if not fn:
        return None
    try:
        result = fn(error_record)
    except Exception as e:
        log.debug(f"[GUARDIAN/learned] {fn_name} 실행 실패: {e}")
        return None
    if not result:
        return None

    _bump_hit_count(data, fp)
    result["learned"]     = True
    result["fingerprint"] = fp
    log.info(f"[GUARDIAN/learned] ★ 학습 매칭 — fp='{fp[:70]}' fixer={fixer_name}")
    return result


def _make_learned_fn(pattern: dict, stored_data: dict, source_label: str):
    """학습 패턴 하나를 fixer 클로저로 변환 — 두 그룹 공용 팩토리."""
    def _fn(error_record: dict) -> Optional[dict]:
        et  = error_record.get("error_type", "") or ""
        msg = error_record.get("message",    "") or ""
        target_fp = pattern.get("fingerprint", "")

        # 정확 매칭
        matched = (_make_fingerprint(et, msg) == target_fp)
        # 부분 매칭 (error_type + message_pattern regex)
        if not matched and pattern.get("error_type") == et:
            try:
                if re.search(pattern.get("message_pattern", ""), msg):
                    matched = True
            except re.error:
                pass
        if not matched:
            return None

        r = _apply_single_pattern(pattern, error_record, stored_data)
        if r:
            r["source"] = r.get("source", source_label)
        return r
    return _fn


# ⚠️ DEPRECATED (사용자 박제 2026-07-04) — _get_verified_fixers / _get_new_fixers 는
#   더 이상 try_pattern_fix 의 *밴딧 후보* 로 쓰지 않는다. 학습 패턴을 개별 arm 으로 펼치면
#   오류 지문마다 arm 이 생겨 밴딧이 무한 증식(402MB·θ≈0 죽은 신호)한다.
#   학습 캐시 조회는 _fix_from_learned 단일 경로가 담당(정확→정규식→시맨틱 매칭).
#   ★ 이 함수들을 밴딧 후보 리스트로 되돌리지 말 것 — 오염 회귀 금지. (잔존 이유: 하위호환)
def _get_verified_fixers() -> list[tuple[str, object]]:
    """(레거시 — 밴딧 후보 아님) hit_count ≥ _HIGH_COUNT_THRESHOLD 인 검증된 학습 패턴.

    ⚠️ try_pattern_fix 는 이제 이 함수를 호출하지 않는다 (위 DEPRECATED 배너 참조).
    """
    data = _load_learned()
    result: list[tuple[str, object]] = []
    for p in data.get("patterns", []):
        if int(p.get("hit_count", 0)) < _HIGH_COUNT_THRESHOLD:
            continue
        fp = p.get("fingerprint", "")
        if not fp or not p.get("fixer"):
            continue
        result.append((f"verified:{fp[:32]}", _make_learned_fn(p, data, "verified_learned")))
    if result:
        log.debug(f"[GUARDIAN/pattern] 검증 학습 패턴 {len(result)}종 (hit≥{_HIGH_COUNT_THRESHOLD})")
    return result


def _get_new_fixers() -> list[tuple[str, object]]:
    """hit_count 1 ~ (_HIGH_COUNT_THRESHOLD-1) 인 신규 학습 패턴 — Group 2.

    1번이라도 수정 성공한 순간 Bandit 풀 편입 → UCB exploration bonus 받아 탐색.
    검증된 그룹(Group 1)이 모두 실패한 뒤에 시도.
    """
    data = _load_learned()
    result: list[tuple[str, object]] = []
    for p in data.get("patterns", []):
        hc = int(p.get("hit_count", 0))
        if hc < 1 or hc >= _HIGH_COUNT_THRESHOLD:
            continue
        fp = p.get("fingerprint", "")
        if not fp or not p.get("fixer"):
            continue
        result.append((f"new:{fp[:32]}", _make_learned_fn(p, data, "new_learned")))
    if result:
        log.debug(f"[GUARDIAN/pattern] 신규 학습 패턴 {len(result)}종 (hit 1~{_HIGH_COUNT_THRESHOLD-1})")
    return result


def _try_fixer_group(
    error_record: dict,
    group: list[tuple[str, object]],
    error_type: str,
    bandit_rank_fn,
    bandit_reward_fn,
) -> Optional[dict]:
    """fixer 그룹을 Bandit 순서로 시도. 성공 시 결과 반환, 전체 실패 시 None.

    - bandit_rank_fn / bandit_reward_fn 이 None 이면 Bandit 없이 주어진 순서로 실행.
    - 실패한 fixer 는 즉시 음의 보상 기록.
    """
    if not group:
        return None

    if bandit_rank_fn:
        names       = [n for n, _ in group]
        ranked      = bandit_rank_fn(error_record, names)
        fn_map      = {n: fn for n, fn in group}
        ordered     = [(n, fn_map[n]) for n in ranked if n in fn_map]
    else:
        ordered = group

    failed: list[str] = []

    for fixer_name, fn in ordered:
        try:
            result = fn(error_record)   # type: ignore[operator]
            if result:
                # 앞서 실패한 fixer 일괄 음의 보상
                if bandit_reward_fn and failed:
                    for fn_fail in failed:
                        try:
                            bandit_reward_fn(error_type, fn_fail, success=False,
                                             error_record=error_record)
                        except Exception:
                            pass

                # static fixer 성공 시 자동 학습 등록
                if not result.get("learned") and result.get("pattern"):
                    try:
                        record_pattern_hit(
                            error_record,
                            fixer_name=result["pattern"],
                            fixed_file=result.get("target_file", ""),
                            source="auto-static",
                        )
                    except Exception as e:
                        log.debug(f"[GUARDIAN/learned] 자동 학습 등록 실패: {e}")

                result["source"]        = result.get("source", "pattern")
                result["patch"]         = result.get("patch", result.get("patch_full", ""))
                result["_bandit_fixer"] = fixer_name
                log.info(
                    f"[GUARDIAN/pattern] {fixer_name} 매칭 — "
                    f"{result.get('target_file','?')} : {result.get('explanation','')[:60]}"
                )
                return result
            else:
                failed.append(fixer_name)
        except Exception as e:
            log.debug(f"[GUARDIAN/pattern] {fixer_name} 시도 실패: {e}")
            failed.append(fixer_name)

    # 그룹 전체 실패 → 일괄 음의 보상
    if bandit_reward_fn and failed:
        for fn_fail in failed:
            try:
                bandit_reward_fn(error_type, fn_fail, success=False, error_record=error_record)
            except Exception:
                pass
    return None


# ★ 정적 fixer 코어 6종 — 하드코딩된 기본 집합 (Group 1 에 항상 포함)
_STATIC_FIXERS_CORE: list[tuple[str, object]] = [
    ("relative_import", _fix_relative_import),
    ("none_slicing",    _fix_none_slicing),
    ("name_typo",       _fix_name_typo),
    ("none_attribute",  _fix_none_attribute),
    ("import_name",     _fix_import_name),
    ("unpack_mismatch", _fix_unpack_mismatch),
]

# legacy 호환 참조
_PATTERN_FIXERS = [
    _fix_relative_import, _fix_none_slicing, _fix_name_typo,
    _fix_none_attribute,  _fix_import_name,  _fix_unpack_mismatch,
    _fix_from_learned,
]


def try_pattern_fix(error_record: dict) -> Optional[dict]:
    """패턴 기반 자동 수정 시도. 성공 시 patch dict 반환, 실패 시 None.

    ★ Tier 1 시도 순서 — Bandit 랭킹, arm 은 *유한한 전략* (사용자 박제 2026-07-04):
      후보 = "learned"(학습 캐시 정확·정규식·시맨틱 단일 조회) + 정적 코어 6종
      → Bandit Linear UCB 가 이 소수 전략의 시도 순서를 학습.
      ★ 학습 패턴을 개별 arm 으로 펼치지 않는다 — 구 _get_verified/_get_new 방식은
        오류 지문마다 arm 을 만들어 밴딧 무한증식(402MB·죽은 신호)의 원인이었다.
        _fix_from_learned 가 내부에서 정확→정규식→시맨틱 매칭으로 THE 패턴 1건을 직접 재적용.
      전체 실패 시 None → error_analyzer 가 Tier 2 (LLM) 로 위임.

    양의 보상(성공)은 error_fixer.apply_fix() 에서 실제 파일 수정 후 기록.
    """
    error_type = error_record.get("error_type", "unknown")

    try:
        from JARVIS07_GUARDIAN.bandit import rank_fixers as _bandit_rank, reward as _bandit_reward
        _br, _bw = _bandit_rank, _bandit_reward
    except Exception:
        _br, _bw = None, None

    # ── 후보: learned 캐시 단일 조회 + 정적 코어 6종 (arm 상한 ~8) ──────────
    def _learned_fixer(er: dict) -> Optional[dict]:
        return _fix_from_learned(er)

    group = [("learned", _learned_fixer)] + _STATIC_FIXERS_CORE
    result = _try_fixer_group(error_record, group, error_type, _br, _bw)
    if result:
        return result

    return None


__all__ = [
    "try_pattern_fix", "record_pattern_hit", "record_sdk_fix", "bandit_arm_name",
    "stats", "_make_fingerprint",
    "_infer_domain", "backfill_domains", "backfill_tiers",   # ★ ADR 008 Phase 4
]
