"""JARVIS07_GUARDIAN/error_analyzer.py — Claude Code SDK 기반 오류 분석기.

역할:
  1. error_log 레코드 + 관련 파일 내용 → Claude Code SDK(Sonnet 5) 분석
  2. 수정 대상 파일·수정 내용 반환
  3. 과거 resolution 재활용 (DB 조회 우선)

모델 정책 (★ 사용자 박제 2026-07-06, ADR 017: Sonnet 5 단일 통일 — ADR 015 폐지):
  - 패치 생성: Sonnet 5 ("guardian" alias) — 전체 LLM 호출 단일 모델 사용

반환 형식:
  {
    "fixable": bool,
    "target_file": "JARVIS02_WRITER/foo.py",   # 상대 경로
    "patch": "수정된 코드 or 수정 방법 설명",
    "explanation": "원인 설명",
    "source": "cached" | "llm",
  }
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("jarvis.guardian.analyzer")

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 분석 가능한 파일 확장자
_ANALYZABLE_EXT = {".py", ".sh", ".md"}
# 파일 읽기 최대 문자수 (너무 크면 컨텍스트 낭비)
_MAX_FILE_CHARS = 3000


def _read_file_safe(path: Path) -> str:
    """파일 내용 읽기. 실패 시 빈 문자열."""
    try:
        if path.suffix not in _ANALYZABLE_EXT:
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text[:_MAX_FILE_CHARS]
    except Exception:
        return ""


def _find_module_path(module_name: str) -> Path | None:
    """모듈명(파일명)으로 jarvis-agent 내 파일 탐색."""
    if not module_name:
        return None
    # 직접 경로인 경우
    p = _ROOT / module_name
    if p.exists():
        return p
    # 파일명만 있는 경우 — 전체 탐색
    for candidate in _ROOT.rglob(module_name):
        if candidate.suffix in _ANALYZABLE_EXT:
            if "__pycache__" not in str(candidate) and ".venv" not in str(candidate):
                return candidate
    return None


def analyze_llm_only(error_record: dict) -> dict:
    """★ Tier 2 (LLM) 직접 분석 — 패턴·캐시 없이 LLM 단독 호출.

    apply_fix() 의 Tier 1(패턴·Bandit) 수정 실패 시 guardian._orchestrate() 에서 호출하는 최종 fallback.
    Tier 1 단계를 건너뛰고 Claude Code SDK(Sonnet 5) 직접 분석.
    """
    import re as _re

    _empty = {"fixable": False, "target_file": None, "patch": None,
              "explanation": "", "source": "llm"}

    error_type = error_record.get("error_type", "")
    message    = error_record.get("message", "")
    module     = error_record.get("module", "")
    tb_str     = error_record.get("traceback", "") or ""
    severity   = error_record.get("severity", "medium")

    module_path  = _find_module_path(module)
    file_content = _read_file_safe(module_path) if module_path else ""
    target_rel   = str(module_path.relative_to(_ROOT)) if module_path else (module or "unknown")

    extra_files = ""
    if tb_str:
        for m in _re.finditer(r'File "([^"]+)", line (\d+)', tb_str):
            fp = Path(m.group(1))
            if _ROOT in fp.parents and "__pycache__" not in str(fp):
                content = _read_file_safe(fp)
                if content:
                    extra_files += f"\n\n[파일: {fp.relative_to(_ROOT)}]\n{content}"
                    break

    prompt = f"""\
JARVIS 에이전트 시스템에서 아래 오류가 발생했다. 원인을 파악하고 수정 방법을 제시해줘.

[오류 정보]
error_type: {error_type}
message: {message}
module: {module}
severity: {severity}

[스택트레이스]
{tb_str[:1500] if tb_str else '없음'}

[관련 파일 내용: {target_rel}]
{file_content or '파일 없음'}
{extra_files}

[응답 형식 — 반드시 아래 형식으로만]
FIXABLE: yes / no
TARGET_FILE: 수정할 파일 경로 (jarvis-agent 기준 상대경로, 없으면 none)
EXPLANATION: 원인 1~2문장
PATCH:
(수정된 코드 전체 또는 수정 방법. 코드면 python 코드블록 안에)
"""
    try:
        from shared.llm import invoke_text
        # Tier 2 — 항상 Sonnet 5 ("guardian" alias): 코드 수정 판단은 단일 모델 사용
        raw = invoke_text("guardian", prompt, timeout=300).strip()
    except Exception as e:
        log.error(f"[GUARDIAN] Claude LLM 분석 실패: {e}")
        return {**_empty, "explanation": f"LLM 분석 실패: {e}"}

    fixable  = bool(_re.search(r"FIXABLE:\s*yes", raw, _re.I))
    target_m = _re.search(r"TARGET_FILE:\s*(.+)", raw)
    expl_m   = _re.search(r"EXPLANATION:\s*(.+)", raw)
    patch_m  = _re.search(r"PATCH:\s*\n([\s\S]+?)(?:\Z|(?=\n[A-Z_]+:))", raw)

    target_file = target_m.group(1).strip() if target_m else None
    if target_file and target_file.lower() in ("none", "없음", ""):
        target_file = None
    explanation = expl_m.group(1).strip() if expl_m else ""
    patch = patch_m.group(1).strip() if patch_m else None
    if patch:
        patch = _re.sub(r"^```python\s*\n?", "", patch, flags=_re.I)
        patch = _re.sub(r"\n?```\s*$", "", patch)
        patch = patch.strip()

    log.info(f"[GUARDIAN] LLM 분석 완료 — fixable={fixable}, target={target_file}")
    return {
        "fixable": fixable,
        "target_file": target_file or target_rel,
        "patch": patch,
        "explanation": explanation,
        "source": "llm",
    }


def analyze(error_record: dict) -> dict:
    """오류 레코드를 분석해 수정 방안 반환.

    ★ 티어 정의는 architecture.py 단일 진실 소스.
      catch() 단일 진입점(탐지) 으로 수집된 오류를 아래 티어로 처리:
      Tier 1 — 패턴 자동 수정: Contextual Bandit + static 6 + learned patterns (LLM 호출 0) ← 이 함수
      Tier 2 — LLM 자동 수정 : Claude Code SDK · Sonnet 5               (_orchestrate 에서 위임)

    Returns:
        dict with keys: fixable, target_file, patch, explanation, source
    """
    _empty = {"fixable": False, "target_file": None, "patch": None,
              "explanation": "", "source": "pattern"}

    severity = error_record.get("severity", "medium")

    if severity == "critical":
        log.info(f"[GUARDIAN] critical 오류 분석 skip: {error_record.get('error_type', '')}")
        return {**_empty, "explanation": "critical 심각도 — 자동 수정 불가, 수동 검토 필요"}

    # Tier 1: 패턴 기반 자동 수정 (Contextual Bandit + static + learned, LLM 호출 0)
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import try_pattern_fix
        pat_result = try_pattern_fix(error_record)
        if pat_result:
            log.info(f"[GUARDIAN] Tier1 매칭 — {pat_result.get('pattern','?')} ({pat_result.get('target_file','?')})")
            return pat_result
    except Exception as e:
        log.warning(f"[GUARDIAN] Tier1 매칭 실패: {e}")

    # Tier 2: Tier 1 실패 → Claude Code SDK (LLM) 위임
    return _empty
