"""JARVIS07_GUARDIAN/error_fixer.py — 오류 자동 수정기.

흐름:
  1. 안전 검증 (경로 탈출 방지 / 줄 수 / ast.parse)
  2. .bak 백업
  3. 파일 적용
  4. import 검증
  5. 실패 시 .bak 롤백
  6. DB 상태 업데이트 + ERRORS.md 기록

★ 자동 승인 — Telegram 버튼 없음. 검증 통과 시 즉시 적용.
"""
from __future__ import annotations

import ast
import logging
import shutil
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


def _update_errors_md(error_record: dict, analysis: dict, success: bool):
    """ERRORS.md에 오류 기록 추가 (기존 규정 준수)."""
    try:
        errors_md = Path(__file__).parent / "ERRORS.md"
        if not errors_md.exists():
            return
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        status_icon = "✅ 자동수정" if success else "❌ 자동수정실패"
        entry = (
            f"\n---\n"
            f"### [{now_str}] {status_icon} — {error_record.get('error_type','?')}\n"
            f"- **증상**: {error_record.get('message','')[:200]}\n"
            f"- **모듈**: {error_record.get('module','')}\n"
            f"- **원인**: {analysis.get('explanation','')}\n"
            f"- **파일**: {analysis.get('target_file','')}\n"
            f"- **해결**: {'자동 수정 적용' if success else '자동 수정 실패 — 수동 검토 필요'}\n"
        )
        with open(errors_md, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        log.warning(f"[GUARDIAN] ERRORS.md 업데이트 실패: {e}")


def apply_fix(error_id: int, analysis: dict, mark_wontfix: bool = True) -> bool:
    """분석 결과를 실제 파일에 적용.

    Args:
        error_id: error_log.id
        analysis: error_analyzer.analyze() 반환값
        mark_wontfix: False 이면 실패 시 wontfix 마킹·알림·ERRORS.md 기록 생략.
            guardian._orchestrate() 에서 Claude fallback 전 1차 시도 시 False 로 호출.

    Returns:
        bool: 수정 성공 여부
    """
    def _fail(reason: str) -> bool:
        if mark_wontfix:
            _mark_wontfix(error_id, reason)
        return False

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
        return _fail("patch 구문 오류")

    # ── .bak 백업 ────────────────────────────────────────────────
    bak = _backup(file_path)
    if not bak:
        return _fail("백업 실패")

    # ── 파일 적용 + 원본 캡처 (diff 저장용) ─────────────────────
    try:
        original_content = file_path.read_text(encoding="utf-8")
    except Exception:
        original_content = ""
    try:
        file_path.write_text(patch, encoding="utf-8")
        log.info(f"[GUARDIAN] #{error_id} 파일 적용: {file_path.name}")
    except Exception as e:
        log.error(f"[GUARDIAN] #{error_id} 파일 쓰기 실패: {e}")
        _rollback(file_path, bak)
        return _fail(f"파일 쓰기 실패: {str(e)[:50]}")

    # ── import 검증 ──────────────────────────────────────────────
    time.sleep(0.3)
    if not _import_check(file_path):
        log.warning(f"[GUARDIAN] #{error_id} import 실패 → 롤백")
        _rollback(file_path, bak)
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
            _update_errors_md(error_record, analysis, success=False)
        return False

    # ── 성공 처리 ────────────────────────────────────────────────
    try:
        from shared import db as _db
        _db.mark_error_fixed(
            error_id,
            resolution=analysis.get("explanation", "") + "\n" + (patch[:500] if patch else ""),
            fixed_file=str(file_path.relative_to(_ROOT)),
        )
        error_record = _db.get_error(error_id)
    except Exception as e:
        log.error(f"[GUARDIAN] DB 업데이트 실패: {e}")
        error_record = {}

    # ★ 학습 등록 — unified diff 로 저장 (full-file 대체) → 파일 변경 후에도 안전 재적용
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
        record_pattern_hit(
            error_record or {},
            fixer_name=analysis.get("pattern") or "llm_patch",
            fixed_file=_rel,
            source=analysis.get("source", "auto-llm"),
            patch=_store_patch,
            target_file=target_rel or "",
        )
    except Exception as e:
        log.debug(f"[GUARDIAN/learned] apply_fix 학습 등록 실패: {e}")

    # ★ Bandit 양의 보상 — 실제 파일 수정 성공 후 기록 (진짜 reward signal)
    try:
        from JARVIS07_GUARDIAN.bandit import reward as _bandit_reward
        _et  = (error_record or {}).get("error_type", "")
        _bfx = analysis.get("_bandit_fixer") or analysis.get("pattern", "")
        if _et and _bfx:
            _bandit_reward(_et, _bfx, success=True)
    except Exception as _be:
        log.debug(f"[BANDIT] 양의 보상 기록 실패: {_be}")

    _update_errors_md(error_record, analysis, success=True)
    _notify_success(error_id, file_path.name, analysis.get("explanation", ""))
    log.info(f"[GUARDIAN] #{error_id} 자동 수정 성공 ✅ — {file_path.name}")
    return True


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
