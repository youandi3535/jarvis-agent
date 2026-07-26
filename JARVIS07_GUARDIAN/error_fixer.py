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
        rel_path = p.relative_to(_ROOT)
        rel = str(rel_path)
        # ── 금지 디렉터리 차단 — **경로 구성요소** 로 판정 (2026-07-26 오탐 수정) ──
        #   ★ 종전 `if deny in str(p)` 는 *문자열 포함* 이라 이름에 우연히 들어간 파일까지
        #     영구 차단했다. 실측: `JARVIS03_RADAR/blogs.py`(b-logs)·`shared/dialogs.py`
        #     (dia-logs) 가 자동수정 불가였다. 저장소의 다른 두 곳은 이미 올바른 패턴을
        #     쓰고 있었다 — `agent_tools.py:374`(rel==deny or startswith) ·
        #     `auto_repair.py:346`(d in p.parts). 여기만 유일하게 substring 이었다.
        #   다중 세그먼트 deny("shared/backups")도 함께 다루므로 두 형태를 모두 본다.
        for deny in _DENY_DIRS:
            hit = ((rel == deny or rel.startswith(deny + "/")) if "/" in deny
                   else (deny in rel_path.parts))
            if hit:
                log.warning(f"[GUARDIAN] 금지 경로: {rel}")
                return None
        # ★ 금지 파일 차단 (ERRORS.md 덮어쓰기 사고 재발 방지)
        if rel in _DENY_FILES or any(rel.endswith("/" + d) or rel == d for d in _DENY_FILES):
            log.warning(f"[GUARDIAN] 금지 파일 (기록·박제): {rel}")
            return None
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


def _rollback_ok(file_path: Path, bak_path: Path) -> bool:
    """복원 성공 여부를 **돌려주는** 롤백. 실패를 삼키면 파손을 성공으로 집계한다."""
    try:
        shutil.copy2(bak_path, file_path)
        log.info(f"[GUARDIAN] 롤백 완료: {file_path.name}")
        return True
    except Exception as e:                              # noqa: BLE001
        log.error(f"[GUARDIAN] ★ 롤백 실패 — 파일이 파손된 채 남았다: {file_path} — {e}")
        return False


def _rollback(file_path: Path, bak_path: Path):
    """백업에서 원복."""
    try:
        shutil.copy2(bak_path, file_path)
        log.info(f"[GUARDIAN] 롤백 완료: {file_path.name}")
    except Exception as e:
        log.error(f"[GUARDIAN] 롤백 실패: {e}")


# ── import 검증 프로브 (별도 인터프리터) ─────────────────────────────────────
#   ★ 왜 서브프로세스인가 (2026-07-26, ERRORS [502]):
#     종전 `_import_check` 는 `spec.loader.exec_module(mod)` 로 **패치된 코드를 살아있는
#     데몬 프로세스 안에서 실제로 실행** 했다. 두 가지가 문제였다.
#       ① 모듈 레벨 부작용이 데몬 안에서 진짜로 일어난다 (LLM 이 쓴 코드를 그대로).
#       ② `sys.modules` 캐시 — 모듈 안의 `import B` 는 데몬이 이미 들고 있는 **옛 B** 를
#          집는다. 그래서 A·B 를 함께 고쳐도 검사는 "새 A + 옛 B" 를 본다. 다중 파일
#          트랜잭션에서는 *정합성을 보려고* 함께 고치는 것이라 이 오염이 검증의 핵심을 무력화한다.
#     저장소에는 이미 같은 이유로 만든 선례가 있다 — `_run_probe` 의 재현 프로브
#     ("별도 인터프리터 — 데몬의 import 캐시가 진실을 가린다"). 그 패턴을 그대로 따른다.
#   킬스위치 `GUARDIAN_IMPORT_SUBPROC=0` → 종전 in-process 방식.
#   ★★ `sys.modules` 에 **선등록하지 말 것** (2026-07-26 회귀 자수):
#     초판은 "자기참조 import 대비" 라며 `sys.modules[mod_name] = m` 를 넣었는데, 그게
#     **멀쩡한 파일을 실패로 만든다**. 반쪽만 초기화된 모듈을 미리 꽂아두면 모듈 본문이
#     자기 패키지를 건드리는 순간 `__init__.py` 가 그 빈 객체를 집어 재수출에 실패한다.
#     실측: 무수정 상태에서 **24개 파일(JARVIS09 전량)** 이 import 실패 → 전량 롤백 →
#     *옳은 패치를 낸 arm 에 음의 보상*. 종전 in-process 검사는 선등록을 하지 않았으므로
#     이건 순수한 신규 회귀였다. 검사는 운영이 겪지 않는 상태를 만들어내면 안 된다.
#   ★★ 그리고 **운영이 실제로 쓰는 import 머시너리를 그대로 타야 한다** — 실측으로 확정.
#     무수정 저장소 202개 파일에 세 방식을 돌린 결과:
#       · `sys.modules` 선등록 + spec/exec  → 실패 24 (JARVIS09 패키지 재수출 붕괴)
#       · 선등록 없이 spec/exec             → 실패 15 (dataclass 가 `sys.modules[__module__]`
#                                              를 찾지 못함 — 파일을 '이름 없는 모듈' 로 띄운 탓)
#       · `importlib.import_module`         → **실패 0**
#     spec/exec 는 부모 패키지도, 모듈 등록도 없는 *인공 상태* 를 만든다. 운영은 그런 상태로
#     모듈을 쓰지 않는다. 같은 파일의 재현 프로브(`_PROBE_SRC`)가 이미 `import_module` 을
#     쓰고 있었다 — 검사는 소비자의 경로를 재현해야 한다는 ERRORS [499] 교훈 그대로다.
_IMPORT_PROBE_SRC = r'''
import importlib, sys
root, mod_name, path = sys.argv[1], sys.argv[2], sys.argv[3]
if root not in sys.path:
    sys.path.insert(0, root)
if mod_name and all(seg.isidentifier() for seg in mod_name.split(".")):
    importlib.import_module(mod_name)           # 운영과 동일 경로 (부모 패키지·순환 처리 포함)
else:
    # 모듈명으로 쓸 수 없는 경로(점으로 시작하는 폴더 등) — 파일 단위로만 확인
    import importlib.util
    spec = importlib.util.spec_from_file_location("_probe_mod", path)
    if spec and spec.loader:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
'''


def _import_timeout() -> float:
    """import 검증 상한(초). ★ 리터럴을 박지 않는다 — 재현검증 예산에서 *파생* 한다.

    같은 파일이 재현검증엔 `_verify_timeout()`(무배포 조정 노브)을 두고 있는데 import
    검증만 숫자를 박아두면, 노브를 돌려도 한쪽만 따라오는 드리프트가 생긴다.
    무배포 조정: `GUARDIAN_IMPORT_TIMEOUT`.
    """
    try:
        v = float(os.getenv("GUARDIAN_IMPORT_TIMEOUT", "") or _verify_timeout())
        return max(2.0, v)
    except Exception:                                   # noqa: BLE001
        return 25.0


def _import_check(file_path: Path, timeout: float | None = None) -> bool:
    """수정 후 import 테스트. Python 파일만. **깨끗한 인터프리터**에서 수행."""
    if file_path.suffix != ".py":
        return True
    if timeout is None:
        timeout = _import_timeout()
    try:
        rel = file_path.relative_to(_ROOT)
        module_str = str(rel).replace("/", ".").replace("\\", ".")[:-3]
    except Exception as e:                                  # noqa: BLE001
        log.warning(f"[GUARDIAN] import 대상 경로 해석 실패: {e}")
        return False

    if os.getenv("GUARDIAN_IMPORT_SUBPROC", "1").strip() not in ("0", "false", "False"):
        try:
            cp = subprocess.run(
                [sys.executable, "-c", _IMPORT_PROBE_SRC,
                 str(_ROOT), module_str, str(file_path)],
                capture_output=True, text=True, timeout=timeout, cwd=str(_ROOT),
            )
            if cp.returncode == 0:
                return True
            log.warning(f"[GUARDIAN] import 테스트 실패({file_path.name}): "
                        f"{(cp.stderr or '').strip()[-300:]}")
            return False
        except subprocess.TimeoutExpired:
            # 시간 초과 = 모듈 레벨에서 뭔가 오래 도는 것. 판정 불가지만 **통과시키지 않는다**
            # (import 조차 못 끝내는 상태를 정상으로 볼 수 없다 — fail-closed).
            log.warning(f"[GUARDIAN] import 테스트 시간초과({file_path.name}, {timeout:.0f}s)")
            return False
        except Exception as e:                              # noqa: BLE001
            # 프로브 자체를 못 띄운 경우 — 아래 in-process 로 폴백
            log.debug(f"[GUARDIAN] import 프로브 기동 실패 → in-process 폴백: {e}")

    try:
        import importlib
        spec = importlib.util.spec_from_file_location(module_str, str(file_path))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return True
    except Exception as e:                                  # noqa: BLE001
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


def _meaningful_lines(text: str, suffix: str = ".py") -> list[str]:
    """공백·주석만인 줄 제외 — '지운 양' 판정의 분모.

    ★ 언어별 주석 규칙 (2026-07-26): `#` 를 무조건 주석으로 버리면 **마크다운의 제목이
      통째로 사라진다**(`# 제목` 은 주석이 아니라 본문이다). 그러면 제목이 많은 문서는
      분모가 텅 비어 판정이 뒤집힌다 — 정상 패치가 "전체 삭제" 로 거부되거나, 반대로
      원본이 비어 보여 판정 자체를 포기(통과)한다.
      실제로 `.md` 를 이 검사에 태우기 시작하면서 드러났다.
    """
    strip_hash = suffix in (".py", ".sh")      # 마크다운은 `#` 가 본문(제목)이다
    out = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        if strip_hash and s.startswith("#"):
            continue
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
        cnt: Counter = Counter()
        for st in body:
            cnt[_stmt_key(st)] += 1
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rec(st, f"{path}.{st.name}" if path else st.name)
        # ★ 같은 이름이 두 번 정의되면 *마지막 정의가 산다* (Python 시맨틱) → 덮어쓴다.
        #   누적(+=)하면 "중복 def 제거" 가 '문장이 사라졌다' 로 오판된다(실측).
        out[path] = cnt

    rec(tree, "")
    return out


# ── 저장소 전역 참조 검사 — "dead code" 를 *파일 안* 만 보고 단정하지 않는다 ──────
# ★ `.claude` 추가 (2026-07-26): 그 아래 `worktrees/` 에 **저장소 사본** 이 들어 있어
#   저장소 .py 목록의 절반을 차지한다. 사본에는 지우려는 심볼이 그대로 남아 있으므로
#   `_referenced_elsewhere` 가 "다른 파일이 아직 쓴다" 로 오판 → 정당한 정리가 영구 거부된다.
#   (오버레이는 *이번 트랜잭션의 실제 경로* 만 덮으므로 사본까지 가려주지 못한다.)
_SKIP_PARTS = {".venv", ".git", "__pycache__", "chrome_profile", "logs", "backups",
               "node_modules", "dashboard", ".claude"}


_PY_FILES_CACHE: tuple[float, list[Path]] = (0.0, [])


def _repo_py_files() -> list[Path]:
    """저장소 .py 목록 — *목록만* 60초 캐시. 파일 *내용* 은 매번 디스크에서 새로 읽는다
    (복사본을 진실로 믿지 않는다 — 캐시하는 것은 '어디를 볼지' 뿐이다)."""
    global _PY_FILES_CACHE
    ts, cached = _PY_FILES_CACHE
    if cached and (time.monotonic() - ts) < 60.0:
        return cached
    out: list[Path] = []
    for p in _ROOT.rglob("*.py"):
        if _SKIP_PARTS & set(p.relative_to(_ROOT).parts):
            continue
        out.append(p)
    _PY_FILES_CACHE = (time.monotonic(), out)
    return out


def _ast_uses_name(src: str, name: str) -> bool:
    """이 소스가 `name` 을 *코드로* 쓰는가 — 주석·문자열 오탐 배제 (AST 확인).

    ★ 텍스트 검색만 하면 "dead code 제거" 같은 *주석 속 단어* 가 참조로 잡혀
      정당한 dead code 정리를 영원히 막는다(실측). 그래서 텍스트는 *예선* 이고
      본선 판정은 AST 가 한다. 파싱 실패 시에만 보수적으로 True.
    """
    try:
        tree = ast.parse(src)
    except Exception:                      # noqa: BLE001
        return True
    if name in _referenced_names(tree):
        return True
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                if (a.asname or "") == name or a.name.split(".")[-1] == name:
                    return True
    return False


def _module_names(path: Path) -> tuple[str, str]:
    """파일 경로 → (dotted 모듈 경로, 마지막 세그먼트). 저장소 밖이면 ("","")."""
    try:
        rel = path.resolve().relative_to(_ROOT).with_suffix("")
    except Exception:                      # noqa: BLE001
        return "", ""
    parts = rel.parts
    return ".".join(parts), parts[-1]


def _imports_symbol(src: str, dotted: str, seg: str, name: str) -> bool:
    """이 소스가 *그 모듈의* `name` 을 가져다 쓰는가 — 모듈 경유 여부까지 확인.

    ★ 이름만 맞으면 참조로 치면 안 된다: `dead` 같은 흔한 지역변수가 저장소 어딘가에
      있다는 이유로 정당한 dead code 정리가 영원히 막힌다(실측 — shared/token_usage.py).
      최상위 def 는 *그 모듈을 import 해야* 밖에서 쓸 수 있으므로 그 경로를 본다.
    """
    try:
        tree = ast.parse(src)
    except Exception:                      # noqa: BLE001
        return True                        # 파싱 불가 → 보수적으로 '쓰인다'
    aliases: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            m = n.module or ""
            if m == dotted or m.endswith("." + seg) or m == seg:
                for a in n.names:
                    if a.name == name or a.name == "*":
                        return True
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name == dotted or a.name.endswith("." + seg) or a.name == seg:
                    aliases.add(a.asname or a.name.split(".")[0])
    if aliases:
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr == name:
                base = n.value
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name) and base.id in aliases:
                    return True
    # 동적 참조(importlib + getattr 등) — 모듈명과 심볼명이 *둘 다* 텍스트로 있으면 보수적 True
    return (dotted in src or seg in src) and (f'"{name}"' in src or f"'{name}'" in src)


def _referenced_elsewhere(name: str, exclude: Path | None,
                          overlay: dict | None = None) -> bool:
    """이 이름이 *다른 파일* 에서 그 모듈의 심볼로 쓰이는가.

    공개 헬퍼를 dead code 로 오판해 지우는 것을 막는다.
    ② 동적 설계: 예외 목록을 손으로 두지 않고 *저장소 실물* 을 훑어 판정한다.

    ★ overlay (2026-07-26): `{resolved Path: 적용 후 내용}`. 다중 파일을 **한 트랜잭션으로**
      고칠 때, 아직 디스크에 안 쓴 동료 파일의 *적용 후* 모습을 여기에 넣는다.
      없으면 디스크만 보게 되는데, 그러면 A 에서 함수를 지우고 B 에서 그 호출부를 지우는
      **정당한 리팩터가 "B 가 아직 참조한다" 로 영원히 거부**된다 (실측 확인:
      `job_window_deadline` 제거 패치 → `functional — 저장소 다른 파일에서 참조됨`).
      판정 기준은 "지금 디스크" 가 아니라 **"이 트랜잭션이 끝난 뒤의 저장소"** 여야 한다.
    """
    if not name or name.startswith("__"):
        return True                        # 던더는 판정 포기 → 보수적으로 '쓰인다'
    if exclude is None:
        return False                       # 대상 파일 불명 → 파일 안 판정만 (호출자 선택)
    dotted, seg = _module_names(exclude)
    if not seg:
        return True                        # 저장소 밖 파일 → 판정 포기(보수적)
    import re as _re
    npat = _re.compile(r"\b" + _re.escape(name) + r"\b")
    try:
        ex = exclude.resolve()
    except Exception:                      # noqa: BLE001
        ex = None
    for p in _repo_py_files():
        try:
            rp = p.resolve()
            if ex and rp == ex:
                continue
            # ★ 오버레이 우선 — `in` 으로 판정한다. `overlay.get(rp) or read_text()` 로 쓰면
            #   *패치 결과가 빈 문자열* 일 때 falsy 로 떨어져 디스크 옛 내용을 읽는다.
            if overlay is not None and rp in overlay:
                src = overlay[rp]
            else:
                src = p.read_text(encoding="utf-8", errors="ignore")
            if seg not in src or not npat.search(src):
                continue                   # 예선 — 모듈도 이름도 언급 없으면 볼 것 없음
            if _imports_symbol(src, dotted, seg, name):
                return True
        except Exception:                  # noqa: BLE001
            continue
    return False


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


def classify_pure_removal(original: str, patch: str,
                          target_path: Path | None = None,
                          overlay: dict | None = None) -> tuple[str, str]:
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
                if target_path is not None and _referenced_elsewhere(nm, target_path, overlay):
                    return "functional", f"삭제된 `{nm}` 가 저장소 다른 파일에서 참조됨"
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


def _removal_issue(original: str, patch: str, target_path: Path | None = None,
                   overlay: dict | None = None) -> str:
    """★ code-removal patch 가드 — 기능을 *지워서* 통과시키는 패치 거부.

    APR 문헌의 최악 실패 모드. 판정 순서 (P3 정교화 2026-07-25):
      ⓪ 순수 삭제(추가 0줄) 이면 **AST 로 성격을 먼저 판정** —
         정당한 정리(중복 def·dead code·미사용 import)면 통과, 기능 제거면 거부.
         ★ 이때 ① 도 면제한다: 큰 dead 함수를 지우면 줄 수가 크게 주는 것이 *정상* 이고,
           비율만 보면 헌법이 시킨 정리를 영원히 못 한다.
      ① 유의미한 줄이 `_max_shrink_ratio()` 넘게 줄어듦
      ② 순수 삭제이면서 AST 판정 불가(unparsable) → 종전대로 거부 (fail-closed)
    ⓪ 는 *순수 삭제에만* 적용한다 — 추가가 있는 일반 패치까지 문장 손실로 재면
      `x[:N]` → `(x or "")[:N]` 같은 정상 치환도 전부 거부돼 수정이 마비된다.
    위반 사유 문자열 반환, 정상이면 "".
    """
    _sfx = target_path.suffix if target_path is not None else ".py"
    orig = _meaningful_lines(original, _sfx)
    new  = _meaningful_lines(patch, _sfx)
    if not orig:                       # 원본을 못 읽었으면 판정 불가 — 통과(보수적)
        return ""
    if not new:
        return "패치가 비어 있음(전체 삭제)"

    # ★ 언어 구분 (2026-07-26): `.md`/`.sh` 는 AST 가 없어 문장 단위 판정을 못 한다.
    #   그렇다고 검사를 통째로 건너뛰면 — 종전이 그랬다 — 허용 확장자 3종 중 2종이
    #   **전면 삭제까지 무검증** 으로 통과했다(호출자가 `.py` 일 때만 이 함수를 불렀다).
    #   언어 무관한 두 검사(빈 패치·과다 축소)는 모두에 걸고, AST 판정만 `.py` 로 한정한다.
    is_code = (target_path.suffix == ".py") if target_path is not None else True

    added = set(new) - set(orig)
    # ★ "순수 삭제" 판정은 *줄 집합 차이* 가 아니라 **추가 0 + 줄 수 감소** 로 본다.
    #   집합 차이로 보면 *중복* 줄 삭제(중복 def 제거)가 removed=∅ 로 잡혀 이 경로를 못 탄다(실측).
    pure_deletion = (not added) and len(new) < len(orig)

    if is_code and pure_deletion and _cleanup_allowed():
        kind, why = classify_pure_removal(original, patch, target_path, overlay)
        if kind == "cleanup":
            log.info(f"[GUARDIAN] 순수 삭제 허용 — {why}")
            return ""
        if kind == "functional":
            return f"순수 삭제 패치 — {why}"
        # unparsable → 아래 종전 규칙으로 넘긴다 (fail-closed)

    lost = len(orig) - len(new)
    if lost > 0:
        ratio = lost / len(orig)
        if ratio > _max_shrink_ratio():
            return (f"삭제 과다 — 유의미한 줄 {len(orig)}→{len(new)} "
                    f"({ratio:.0%} 감소 > 임계 {_max_shrink_ratio():.0%})")
    # ★ "추가 0줄 순수 삭제" 자동 거부는 **코드에만** 적용한다.
    #   문서(.md)·스크립트(.sh)는 내용을 덜어내는 정리가 정상 작업이라, 여기까지 걸면
    #   문서 정리가 영원히 막힌다. 문서는 위의 과다 축소 임계로만 지킨다.
    if is_code and pure_deletion:
        return (f"순수 삭제 패치(추가 0줄 / 유의미한 줄 {len(orig)}→{len(new)}) "
                f"— 기능 제거로 통과 시도")
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


def record_rollback_learning(error_record: dict, analysis: dict, reason: str,
                             verification: str = "") -> dict:
    """롤백을 학습 자산에 반영 — **GUARDIAN 내 두 적용 경로 공통 정문** (2026-07-26).

    `apply_fix` 와 `pattern_fixer.apply_stored_patches` 가 같은 강등 규칙을 쓰도록
    공개 이름 하나로 모은다. (밴딧 arm 은 경로마다 산출 근거가 달라 — 학습 재적용은
    *저장된 fingerprint* 로 계산해야 한다 — 각자 계산하는 것이 맞다.)
    """
    return _record_learning_failure(error_record, analysis, reason, verification)


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


# ══════════════════════════════════════════════════════════════════════════════
#  ★★ 안전 적용 코어 — **정책 기반 자동수정**이 파일에 닿는 단 하나의 문 (사용자 승인 2026-07-26)
#
#  ★ 정확히 말한다 (과장 금지): 저장소에서 파일을 쓰는 자동 경로는 **셋** 이고, 이 코어는
#    그중 *정책 기반 자동수정* 둘을 덮는다. 세 번째 — `auto_repair` 의 Claude Code SDK
#    자율수정(`permission_mode="bypassPermissions"`, Edit/Write 직접) — 은 **여전히 경계 밖**
#    이다. 그건 별도 신뢰 경계(사용자가 SDK 에 위임한 자율성)이며 이 코어로 덮이지 않는다.
#    "단 하나의 문" 이라고 적어두면 그 배너 자체가 *복사본을 진실로 믿는* 사고가 된다.
#
#  왜 만들었나 (ERRORS [502]): 정책 기반 경로 **둘** 의 가드가 서로 달랐다.
#    · `apply_fix`                     — 가드 5종 전부 통과
#    · `pattern_fixer.apply_stored_patches` — 가드 **0종** (매일 04:30 auto_repair 가 호출)
#  두 번째 길에는 경로안전·삭제가드·import 검증·재현검증이 하나도 없었고, 구문검사마저
#  *파일에 쓴 뒤* 였다. `_DENY_FILES` 로 지킨 learned_patterns.json 이 그 길에선 무방비였다.
#  가드를 아무리 정교하게 만들어도 옆문이 열려 있으면 정책은 새어나간다(CLAUDE.md 실례 [474]).
#
#  계약 — **전부 아니면 전무**:
#    ① 검사는 *쓰기 전* 에 전량. 하나라도 어긋나면 **아무 파일도 건드리지 않는다**.
#    ② 통과분은 전량 백업 → 전량 쓰기.
#    ③ 이후 어떤 실패(쓰기·import·재현)든 **전량 롤백**.
#  이 계약이 곧 다중파일 트랜잭션의 정의이기도 하다 — 파일 1개는 N=1인 특수경우일 뿐.
# ══════════════════════════════════════════════════════════════════════════════

class _Staged:
    """검사를 통과해 적용 대기 중인 파일 하나. 롤백에 필요한 것을 전부 들고 있다."""
    __slots__ = ("rel", "path", "content", "original", "bak", "written")

    def __init__(self, rel: str, path: Path, content: str, original: str):
        self.rel, self.path, self.content, self.original = rel, path, content, original
        self.bak: Path | None = None
        self.written = False

    def __repr__(self):
        return f"_Staged({self.rel})"


def normalize_patch_items(analysis: dict) -> list:
    """analysis dict → `[(target_rel, new_content), ...]` — **패치 목록 해석 단일 지점**.

    ★ 하위호환: 종전 단일 키(`target_file`/`patch`)는 1원소 목록으로 승격된다.
      새 키 `patches: [{"target_file","patch"}, ...]` 가 있으면 그쪽이 우선.
    ★ `patch_full` 도 함께 흡수 — Tier-1 정적 fixer 6종이 쓰는 이름이다.
    """
    out: list = []
    raw = analysis.get("patches")
    if isinstance(raw, list) and raw:
        for it in raw:
            if not isinstance(it, dict):
                continue
            rel = str(it.get("target_file") or "").strip()
            body = it.get("patch", it.get("patch_full", ""))
            if rel and body:
                out.append((rel, body))
        if out:
            return out
    rel = str(analysis.get("target_file") or "").strip()
    body = analysis.get("patch", analysis.get("patch_full", ""))
    # ★ 콤마로 이어붙인 다중 경로("a.py, b.py")는 *경로가 아니다* — 학습 원장에 실제로
    #   4건 쌓여 있었고 `_ROOT/"a.py, b.py"` 가 존재하지 않아 조용히 영구 스킵됐다.
    #   내용이 하나뿐이라 어느 파일 것인지 복원할 수 없으므로 여기서 명시적으로 버린다
    #   (조용한 스킵보다 드러나는 거부가 낫다).
    if rel and "," in rel:
        log.warning(f"[GUARDIAN] target_file 이 다중 경로 문자열 — 해석 불가로 거부: {rel[:120]}")
        return []
    return [(rel, body)] if (rel and body) else []


# 거부 사유 *코드* — 호출자의 분기는 이 코드로만 한다.
#   ★ 표시 문자열(한국어)로 분기하면 문구를 다듬는 순간 조용히 어긋난다.
#     실제로 초판이 `if "구문 오류" in _why` 로 밴딧 보상 방향을 갈랐다 — 지금은 안전하지만
#     사유 문자열에 남의 예외 메시지가 섞여 들어오는 구조라 언제든 오판이 될 수 있었다.
REJ_NONE     = ""
REJ_EMPTY    = "empty"        # 패치 목록·내용 없음
REJ_PATH     = "path"         # 경로 안전 검증 실패 (금지폴더·금지파일·루트탈출·확장자)
REJ_MISSING  = "missing"      # 대상 파일 없음
REJ_DUP      = "duplicate"    # 같은 파일이 두 번 지정
REJ_READ     = "read"         # 원본 읽기 실패
REJ_SYNTAX   = "syntax"       # 패치 구문 오류 ← 유일하게 *전략의 실패*(음의 보상)
REJ_REMOVAL  = "removal"      # 지워서 통과 가드


def precheck_patchset(items: list, *, tag: str = "") -> tuple:
    """적용 **전** 전수 검사. Returns `(staged, 사유, 사유코드)`.

    하나라도 어긋나면 빈 목록을 돌려준다 — 호출자가 여기서 멈추면 파일은 무사하다.
    """
    if not items:
        return [], "패치 목록 없음", REJ_EMPTY

    # ① 경로·존재 — 먼저 전부 해석해야 오버레이를 만들 수 있다
    staged: list = []
    seen: dict = {}
    for rel, content in items:
        if not rel or not content:
            return [], f"패치/대상 누락 ({rel or '(경로없음)'})", REJ_EMPTY
        path = _safe_path(rel)
        if not path:
            return [], f"경로 검증 실패: {rel}", REJ_PATH
        if not path.exists():
            return [], f"파일 없음: {rel}", REJ_MISSING
        if path in seen:
            # 같은 파일을 두 항목이 가리키면 백업이 서로를 덮어써 롤백이 깨진다
            return [], f"같은 파일이 두 번 지정됨: {rel} (= {seen[path]})", REJ_DUP
        seen[path] = rel
        try:
            original = path.read_text(encoding="utf-8")
        except Exception as e:                      # noqa: BLE001
            return [], f"원본 읽기 실패 {rel}: {str(e)[:60]}", REJ_READ
        staged.append(_Staged(rel, path, content, original))

    # ② 구문 — `.py` 만. 쓰기 전에 보므로 깨진 코드는 디스크에 닿지 않는다
    for st in staged:
        if st.path.suffix == ".py" and not _validate_python(st.content):
            return [], f"patch 구문 오류: {st.rel}", REJ_SYNTAX

    # ③ 삭제 가드 — ★ 오버레이(트랜잭션 전체의 '적용 후' 모습)를 함께 넘긴다
    if _verify_enabled():
        overlay = {st.path: st.content for st in staged}
        for st in staged:
            why = _removal_issue(st.original, st.content, st.path, overlay)
            if why:
                return [], f"code-removal 패치 거부 ({st.rel}): {why}", REJ_REMOVAL

    if tag:
        log.info(f"[GUARDIAN/apply] {tag} 선검사 통과 — {len(staged)}개 파일: "
                 f"{', '.join(s.rel for s in staged)}")
    return staged, "", REJ_NONE


def rollback_patchset(staged: list) -> tuple:
    """쓴 것을 전부 되돌린다. Returns `(되돌린 수, 되돌리지 못한 rel 목록)`.

    ★ 실패를 삼키지 않는다 (2026-07-26): 종전엔 `_rollback` 이 예외를 로그로만 남기고
      호출자는 개수만 받아, **디스크에 파손된 파일을 남기고도 "되돌렸다" 고 보고**했다.
      되돌리지 못한 파일이 있으면 그건 '자동수정 실패' 가 아니라 **저장소 파손** 이다.
    """
    ok, failed = 0, []
    for st in staged:
        if not st.bak:
            continue
        # ★ `written` 이 False 여도 시도한다 — 쓰기 도중 예외(인코딩·ENOSPC)면 파일이
        #   이미 잘려 있을 수 있다. 백업이 있으면 되돌리는 쪽이 항상 안전하다.
        if _rollback_ok(st.path, st.bak):
            st.written = False
            ok += 1
        else:
            failed.append(st.rel)
    return ok, failed


REJ_BACKUP = "backup"         # 백업 실패 — *아직 아무것도 안 쓴* 상태
REJ_WRITE  = "write"          # 쓰기 실패 → 전량 롤백
REJ_IMPORT = "import"         # import 검증 실패 → 전량 롤백


def apply_patchset(staged: list, *, tag: str = "") -> tuple:
    """백업 전량 → 쓰기 전량 → import 검증 전량. 실패 시 **전량 롤백**.

    Returns `(성공?, 사유, 사유코드)`. 실패면 호출자가 추가로 되돌릴 것은 없다.
    """
    if not staged:
        return False, "적용 대상 없음", REJ_EMPTY

    def _abort(reason: str, kind: str) -> tuple:
        """전량 롤백 후 실패 반환. 되돌리지 못한 파일이 있으면 **사유에 박아** 올린다."""
        _n, _failed = rollback_patchset(staged)
        if _failed:
            reason = (f"{reason} / ★ 롤백 실패 {len(_failed)}개 — 저장소 파손 상태: "
                      f"{', '.join(_failed)}")
            log.error(f"[GUARDIAN/apply] {tag} {reason}")
        return False, reason, kind

    for st in staged:                                  # ① 백업 전량
        st.bak = _backup(st.path)
        if not st.bak:
            return _abort(f"백업 실패: {st.rel}", REJ_BACKUP)

    # ② 쓰기 전량 — ★ 임시파일에 쓴 뒤 **원자 교체**.
    #   `write_text` 는 먼저 truncate 하고 인코딩하므로, 인코딩 도중 예외가 나면
    #   원본이 이미 0바이트로 날아간 뒤다(실측: lone surrogate 가 섞인 `.md` 로 재현).
    #   교체 방식이면 실패해도 원본이 그대로 남는다 — 되돌릴 일 자체를 없앤다.
    #   같은 이유로 저장소는 이미 `json_store` 에서 이 패턴을 쓰고 있다.
    for st in staged:
        tmp = st.path.with_suffix(st.path.suffix + ".tmp")
        try:
            tmp.write_text(st.content, encoding="utf-8")
            os.replace(tmp, st.path)
            st.written = True
        except Exception as e:                         # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except Exception:                          # noqa: BLE001
                pass
            return _abort(f"파일 쓰기 실패 {st.rel}: {str(e)[:60]}", REJ_WRITE)

    # ③ import 검증 — ★ N개를 *전부 쓴 뒤* 에 한다. 하나씩 쓰고 검사하면 중간 상태
    #    (새 A + 옛 B)를 검사하게 되는데, 그건 우리가 만들려는 최종 상태가 아니다.
    time.sleep(0.3)
    for st in staged:
        if not _import_check(st.path):
            return _abort(f"import 검증 실패: {st.rel}", REJ_IMPORT)

    if tag:
        log.info(f"[GUARDIAN/apply] {tag} 적용 완료 — {len(staged)}개 파일")
    return True, "", REJ_NONE


def apply_files_safely(items: list, *, tag: str = "") -> tuple:
    """선검사 → 적용을 한 번에. **외부 호출자용 정문** (pattern_fixer 등).

    Returns `(성공?, 사유, staged)`. 성공 시 `staged` 로 추가 검증 후 직접 롤백할 수 있다.
    """
    staged, why, _kind = precheck_patchset(items, tag=tag)
    if why:
        return False, why, []
    ok, why2, _k2 = apply_patchset(staged, tag=tag)
    return ok, why2, (staged if ok else [])


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

    # ── 패치 목록 해석 (단일/다중 공통 — `normalize_patch_items` 단일 지점) ──
    _items = normalize_patch_items(analysis)
    if not _items:
        log.warning(f"[GUARDIAN] #{error_id} patch 또는 target 없음")
        return _fail("patch/target 누락")

    # ── ★ 선검사 전량 (경로안전·구문·삭제가드) — 통과 못 하면 파일은 손도 안 댄다 ──
    _staged, _why, _kind = precheck_patchset(_items, tag=f"#{error_id}")
    if _why:
        log.warning(f"[GUARDIAN] #{error_id} 선검사 거부 — {_why}")
        # ★ 구문 오류는 *그 전략의 실패* 다 → 음의 보상. 나머지 거부(경로·삭제가드)는
        #   *판정 불가·부적격* 이지 "수정 실패" 가 아니다 → 보상 0 (사용자 박제 2026-07-25 P3).
        #   판정은 **사유 코드**로 한다 — 표시 문구로 분기하면 문구를 다듬는 순간 어긋난다.
        if _kind == REJ_SYNTAX:
            if _verify_enabled():
                _bandit_signal(_fetch_record(error_id, analysis), analysis, success=False,
                               why="patch 구문 오류")
            return _fail(_why, verification=VERIFY_REPRODUCES)
        return _fail(_why)

    # 하류(재현검증·학습·DB 기록)는 *대표 1개* 를 기준으로 동작한다 — 목록의 첫 원소.
    _primary          = _staged[0]
    file_path         = _primary.path
    target_rel        = _primary.rel
    patch             = _primary.content
    original_content  = _primary.original

    # ── 백업 전량 → 쓰기 전량 → import 검증 전량 (실패 시 전량 롤백) ──
    _ok, _why, _kind = apply_patchset(_staged, tag=f"#{error_id}")
    if not _ok:
        # 백업 실패는 *아직 아무것도 안 쓴* 상태 — 학습·보상 신호를 줄 사건이 아니다.
        if _kind == REJ_BACKUP:
            log.error(f"[GUARDIAN] #{error_id} {_why}")
            return _fail(_why)

        _rec_w = _fetch_record(error_id, analysis)
        _bandit_signal(_rec_w, analysis, success=False, why=f"{_why} → 롤백")
        # ★ 결함 2 배선 — 롤백은 학습 자산에도 반영한다 (감쇠·강등·격리)
        _record_learning_failure(_rec_w, analysis, f"{_why} → 롤백")
        log.warning(f"[GUARDIAN] #{error_id} {_why} → 전량 롤백")
        if mark_wontfix:
            try:
                from shared import db as _db
                _db.mark_error_status(error_id, "wontfix")
            except Exception:
                pass
            _notify_fail(error_id, f"{_why} — 롤백 완료")
            error_record = {}
            try:
                from shared import db as _db
                error_record = _db.get_error(error_id)
            except Exception:
                pass
            _update_errors_md(error_record, analysis, success=False,
                              verified=["선검사 통과", f"{_why} → 자동 롤백"])
        return FixResult(False, verification=VERIFY_REPRODUCES, reason=_why)

    # ── ★ 원 오류 재현 검증 (구문·import 통과 ≠ 오류 해소) ──────────
    _rec_for_verify = _fetch_record(error_id, analysis)
    _vstate, _vdetail = verify_fix(_rec_for_verify, analysis, file_path,
                                   original_content=original_content)
    log.info(f"[GUARDIAN] #{error_id} 재현검증 = {_vstate or '(비활성)'} — {_vdetail}")

    if _vstate == VERIFY_REPRODUCES:
        # 원 오류가 그대로 재현 → 이 패치는 고친 것이 아니다. 되돌린다.
        log.warning(f"[GUARDIAN] #{error_id} 원 오류 재현됨 → 롤백 (증상 은폐 학습 차단)")
        rollback_patchset(_staged)          # ★ 전량 롤백 — 다중 파일이면 N개 전부
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
        # ★ fixed_file 은 **경로 하나** 로 유지한다 (2026-07-26). 다중 파일이라고 콤마로
        #   이어붙이면 ① `pattern_fixer` 의 도메인 분류가 경로 prefix 로 판정하므로 어긋나고
        #   ② 그 문자열이 학습 원장에 들어가 재적용 시 `_ROOT/"a.py, b.py"` 로 조회돼
        #   **조용히 영구 스킵**된다 — 실제로 그렇게 죽은 항목이 4건 있었다.
        #   함께 고친 파일 목록은 사람이 읽는 `resolution` 본문에만 남긴다.
        _files_note = ("함께 수정: " + ", ".join(s.rel for s in _staged) + "\n"
                       if len(_staged) > 1 else "")
        _db.mark_error_fixed(
            error_id,
            resolution=_vtag + (_vdetail + "\n" if _vdetail else "") + _files_note
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

        def _diff_of(st) -> str:
            """파일 하나의 unified diff (5줄 context). 계산 불가면 full-file 로 폴백."""
            _dl_lines = list(_dl.unified_diff(
                st.original.splitlines(keepends=True),
                st.content.splitlines(keepends=True),
                fromfile=f"a/{st.rel}", tofile=f"b/{st.rel}", n=5,
            ))
            return "".join(_dl_lines) if _dl_lines else st.content

        # ★ 다중 파일이면 **전부** 박제한다 (2026-07-26). 대표 1개만 저장하면 재적용이
        #   반쪽만 되살려 오히려 깨진 상태를 만든다. 단일 파일이면 종전과 동일한 형태.
        _all_patches = [(st.rel, _diff_of(st)) for st in _staged]
        _store_patch = _all_patches[0][1]
        _learned_hits = record_pattern_hit(
            error_record or {},
            fixer_name=analysis.get("pattern") or "llm_patch",
            fixed_file=_rel,
            source=analysis.get("source", "auto-llm"),
            patch=_store_patch,
            target_file=target_rel or "",
            patches=_all_patches,
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
    if len(_staged) > 1:
        _verified_notes.insert(0, f"다중 파일 원자적 적용 {len(_staged)}개 — "
                                  f"{', '.join(s.rel for s in _staged)}")
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
