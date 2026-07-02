"""
JARVIS 파일 정리 모듈 — 2주 간격 자동 실행
jarvis_daemon.py APScheduler에서 호출.
"""
from __future__ import annotations

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


# ── 정리 규칙 (날짜 기준 보존 일수) ──────────────────────────────
_RULES: list[tuple[Path, str, int]] = [
    # (폴더,          glob 패턴,              보존 일수)
    (LOGS_DIR,       "economic_*.log",        7),    # 경제 브리핑 로그: 7일
    (LOGS_DIR,       "market_signal_*.txt",   14),   # Market Signal 로그: 14일
    (LOGS_DIR,       "report_*.txt",          30),   # 원고 리포트: 30일
    (RADAR_DIR/"data", "trends_*.json",       30),   # RADAR 트렌드 캐시: 30일
    (JARVIS09_DIR/"output"/"evidence", "evidence_*.json", 30),  # 근거 팩 박제 (ADR 012): 30일
]

_SCREENSHOT_KEEP_DAYS = 30  # screenshots 폴더: 30일


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
    for folder, pattern, keep_days in _RULES:
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
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    print(f"[file_cleanup] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 실행")
    r = run_cleanup(verbose=True)
    print(f"[file_cleanup] 완료: 총 {r['total']}개 삭제")
