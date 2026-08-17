"""
JARVIS 파일 정리 모듈 — 2주 간격 자동 실행
jarvis_daemon.py APScheduler에서 호출.
"""
from __future__ import annotations
# ── 직접 실행(python <이 파일>) 대비 — 프로젝트 루트를 sys.path 에 올린다 (2026-08-10) ──
#   ★ 없으면 `from JARVIS00_INFRA...` 가 ModuleNotFoundError 로 죽고, 그것을 감싼 except 가
#     조용히 삼켜 **Layer 0 preflight 가 한 번도 안 도는** 상태가 된다 (실측: 진입점 16곳 중 8곳).
#     경고 한 줄만 찍히고 그대로 진행하므로, 안전장치가 있다고 착각하기 딱 좋다.
#   ★ 깊이를 숫자로 박지 않는다(②) — 파일이 폴더를 옮기면 조용히 깨진다(ADR 008 이관 전례).
#     루트는 유일한 진입점 `jarvis_daemon.py` 의 존재로 판별한다.
import sys as _sys
from pathlib import Path as _Path
for _anc in _Path(__file__).resolve().parents:
    if (_anc / "jarvis_daemon.py").exists():
        if str(_anc) not in _sys.path:
            _sys.path.insert(0, str(_anc))
        break
del _anc


import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR      = Path(__file__).parent.parent
WRITER_DIR    = BASE_DIR / "JARVIS02_WRITER"
RADAR_DIR     = BASE_DIR / "JARVIS03_RADAR"
JARVIS06_DIR  = BASE_DIR / "JARVIS06_IMAGE"            # 이미지 단일 진입점 (CLAUDE.md 규정)
JARVIS09_DIR  = BASE_DIR / "JARVIS09_COLLECTOR"        # 수집 단일 진입점 (CLAUDE.md 규정)
LOGS_DIR      = WRITER_DIR / "logs"

# ★ 로그 디렉터리는 **실물에서 파생** 한다 (2026-08-05 — ③원칙 위반 시정).
#   종전엔 `LOGS_DIR = WRITER_DIR/"logs"` 한 곳만 정리 대상이었다. 그런데 실물 로그
#   디렉터리는 4곳이고, 그중 3곳이 **통째로 정책 밖**이었다 (실측 미적용 53.5MB —
#   루트 `logs/` 49.6MB · RADAR 2.6MB · GUARDIAN 0.03MB).
#   같은 병을 `shared/secrets.py:redact_logs` 가 먼저 앓고 `rglob("logs")` 로 고쳤다 —
#   *그 정답 형태를 그대로 베낀다*(형태를 베끼는 것은 사본이 아니다. 값을 베끼는 것이 사본이다).
_LOG_SKIP_PARTS = {".venv", ".git", "node_modules", ".next"}


def log_dirs() -> list[Path]:
    """이름이 `logs` 인 실물 디렉터리 전부 — 새 에이전트가 자기 로그 폴더를 만들어도 자동 포함."""
    return [d for d in sorted(BASE_DIR.rglob("logs"))
            if d.is_dir() and not _LOG_SKIP_PARTS & set(d.parts)]


# 기본 로그 보존 — 위 패턴에 안 걸리는 *나머지 모든* 로그 파일에 적용 (일)
_LOG_DEFAULT_KEEP_DAYS = 30


# ── 정리 규칙 (날짜 기준 보존 일수) ──────────────────────────────
_RULES: list[tuple[Path, str, int]] = [
    # (폴더,          glob 패턴,              보존 일수)
    (LOGS_DIR,       "economic_*.log",        7),    # 경제 브리핑 로그: 7일
    (LOGS_DIR,       "market_signal_*.txt",   14),   # Market Signal 로그: 14일
    (LOGS_DIR,       "report_*.txt",          30),   # 원고 리포트: 30일
    (RADAR_DIR/"data", "trends_*.json",       30),   # RADAR 트렌드 캐시: 30일
    (JARVIS09_DIR/"output"/"evidence", "evidence_*.json", 30),  # 근거 팩 박제 (ADR 012): 30일
    (RADAR_DIR/"data", "topic_pack_*.json",   30),   # 주제 패키지 (ADR 013): 30일
]

_SCREENSHOT_KEEP_DAYS = 30  # screenshots 폴더: 30일

# GUARDIAN 패치 백업 보존 (일) — 롤백에 성공하면 그 자리에서 지워지므로 여기 남는 것은
# *성공한 수정의 직전 원본* 뿐이다. 사람이 되돌려 보고 싶을 만한 기간만 남긴다.
_PATCH_BAK_KEEP_DAYS = 14


def _rules() -> list:
    """정리 규칙 — 고정 목록 + **경로의 주인에게서 받아오는** 규칙(②).

    ★ GUARDIAN 패치 백업 경로를 여기 다시 적지 않는다. 주인은 `error_fixer.patch_backup_dir`
      이고, 그쪽이 폴더를 옮기면 이 규칙이 조용히 빈 폴더를 쓸게 된다(사본을 진실로 믿는 병).
      보존 *기간* 은 정리 정책이라 이 파일이 주인이다 — 경로만 받아온다.
    """
    rules = list(_RULES)
    try:
        from JARVIS07_GUARDIAN.error_fixer import patch_backup_dir
        rules.append((patch_backup_dir(), "*.bak", _PATCH_BAK_KEEP_DAYS))
    except Exception as e:                              # noqa: BLE001
        print(f"  ⚠️  GUARDIAN 백업 정리 규칙 로드 실패 — 건너뜀: {e}")
    return rules


def _is_old(path: Path, keep_days: int) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return (datetime.now() - mtime).days >= keep_days
    except Exception:
        return False


def run_cleanup(verbose: bool = True) -> dict:
    """파일 정리 실행. 삭제 통계 dict 반환."""
    stats: dict[str, int] = {}
    total = 0

    # 1. 날짜 기준 로그/데이터 파일
    _rule_list = _rules()
    for folder, pattern, keep_days in _rule_list:
        removed = 0
        for f in folder.glob(pattern):
            if _is_old(f, keep_days):
                f.unlink(missing_ok=True)
                removed += 1
        if removed:
            stats[pattern] = removed
            total += removed
        if verbose and removed:
            print(f"  🗑️  {pattern}: {removed}개 삭제 ({keep_days}일 초과)")

    # 1-B. ★ 위 패턴에 안 걸린 *나머지 로그 파일* — 실물 디렉터리 전수 (2026-08-05)
    #   패턴 목록만으로는 새로 생기는 로그를 영영 못 잡는다. 목록에 없는 것은
    #   기본 보존일로 처리한다 — "정책 밖" 이라는 상태를 없앤다.
    #   ※ *지금 쓰이고 있는* 파일은 건드리지 않는다(mtime 이 최근이면 자동 제외).
    _rule_pairs = {(str(f), pat) for f, pat, _k in _rule_list}
    import fnmatch as _fn
    for d in log_dirs():
        removed = 0
        for f in d.iterdir():
            if not f.is_file():
                continue
            if any(str(d) == fd and _fn.fnmatch(f.name, pat) for fd, pat in _rule_pairs):
                continue      # 전용 규칙이 이미 담당
            if _is_old(f, _LOG_DEFAULT_KEEP_DAYS):
                f.unlink(missing_ok=True)
                removed += 1
        if removed:
            key = f"{d.name}:기타로그"
            stats[key] = stats.get(key, 0) + removed
            total += removed
            if verbose:
                print(f"  🗑️  {d.relative_to(BASE_DIR)} 기타 로그: {removed}개 삭제 "
                      f"({_LOG_DEFAULT_KEEP_DAYS}일 초과)")

    # 2. screenshots — 30일 이상 된 파일 (JARVIS06_IMAGE/output/screenshots/ — 이관됨)
    ss_dir = JARVIS06_DIR / "output" / "screenshots"
    if ss_dir.exists():
        removed = 0
        for f in ss_dir.rglob("*"):
            if f.is_file() and _is_old(f, _SCREENSHOT_KEEP_DAYS):
                f.unlink(missing_ok=True)
                removed += 1
        # 빈 하위 폴더 정리
        for d in sorted(ss_dir.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        if removed:
            stats["screenshots"] = removed
            total += removed
        if verbose and removed:
            print(f"  🗑️  screenshots: {removed}개 삭제 ({_SCREENSHOT_KEEP_DAYS}일 초과)")

    # 3. .DS_Store
    ds_count = 0
    for ds in BASE_DIR.rglob(".DS_Store"):
        if ".venv" not in str(ds) and "chrome_profile" not in str(ds):
            ds.unlink(missing_ok=True)
            ds_count += 1
    if ds_count:
        stats[".DS_Store"] = ds_count
        total += ds_count
        if verbose:
            print(f"  🗑️  .DS_Store: {ds_count}개 삭제")

    # 4. .fuse_hidden* — FUSE 잔여 임시파일 (프로젝트 전체)
    _deny = {".venv", "chrome_profile", "__pycache__", ".git"}
    fuse_count = 0
    for fh in BASE_DIR.rglob(".fuse_hidden*"):
        if any(d in fh.parts for d in _deny):
            continue
        fh.unlink(missing_ok=True)
        fuse_count += 1
    if fuse_count:
        stats[".fuse_hidden"] = fuse_count
        total += fuse_count
        if verbose:
            print(f"  🗑️  .fuse_hidden: {fuse_count}개 삭제")

    # 5. 빈 data 서브폴더
    for d in [RADAR_DIR / "data"]:
        if d.exists():
            for sub in d.iterdir():
                if sub.is_dir() and not any(sub.iterdir()):
                    sub.rmdir()
                    stats["empty_dirs"] = stats.get("empty_dirs", 0) + 1
                    total += 1
                    if verbose:
                        print(f"  🗑️  빈 폴더 삭제: {sub.name}")

    stats["total"] = total
    return stats


def cleanup_fuse_hidden(verbose: bool = False) -> int:
    """프로젝트 전체 .fuse_hidden* + .DS_Store 즉시 정리 — 15분 간격 자동 실행.

    ★ 사용자 박제 2026-06-07 — shared/ 한정 → 프로젝트 전체 rglob 으로 확장.
    FUSE 임시파일은 생성 즉시 삭제 대상 (보존 이유 없음).
    """
    _DENY = {".venv", "chrome_profile", "__pycache__", ".git"}
    count = 0
    for fh in BASE_DIR.rglob(".fuse_hidden*"):
        if any(d in fh.parts for d in _DENY):
            continue
        try:
            fh.unlink(missing_ok=True)
            count += 1
        except Exception:
            pass
    # .DS_Store 도 함께 정리 (run_cleanup 대기 없이 즉시)
    for ds in BASE_DIR.rglob(".DS_Store"):
        if any(d in ds.parts for d in _DENY):
            continue
        try:
            ds.unlink(missing_ok=True)
            count += 1
        except Exception:
            pass
    if count and verbose:
        print(f"  🗑️  즉시 정리: {count}개 삭제")
    return count


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — 파일 삭제 직전 환경 검증
    # ★ try/except 로 감싸지 않는다 (2026-08-10) — 감싸는 순간 ImportError 가 삼켜져
    #   "preflight 가 있다" 는 착각만 남고 **실제로는 한 번도 안 도는** 상태가 된다.
    #   실측(2026-08-10): 진입점 16곳 중 8곳이 그 상태였고, 경고는 stdout 으로만 나가는데
    #   데몬 stdout 은 /dev/null 이라 어디에도 안 남았다 — 완전한 침묵이었다.
    #   루트 경로는 파일 상단 부트스트랩이 보장한다. 여기서 실패하면 진짜 환경 문제다(fail-closed).
    from JARVIS00_INFRA.preflight import ensure_preflight
    ensure_preflight(strict=True)

    print(f"[file_cleanup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 실행")
    r = run_cleanup(verbose=True)
    print(f"[file_cleanup] 완료: 총 {r['total']}개 삭제")
