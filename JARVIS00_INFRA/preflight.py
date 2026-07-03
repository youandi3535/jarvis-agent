"""JARVIS00_INFRA/preflight.py — Layer 0 부팅·환경 검증 (★ 사용자 박제 2026-05-17 ADR 009).

★ 비전: *발행은 모든 게이트 통과의 결과 표시*. Layer 0 는 *모든 다른 Layer 의 전제조건*.
   "애초에 발행 실패 뜨면 안 된다" — 7시 사고 같이 *부팅·환경 결함* 으로 발행 잡이 폭발하는
   상황을 데몬 부팅 단계에서 *영구 차단*.

★ 단일 진입점 (CLAUDE.md 헌법): Layer 0 preflight 코드는 이 파일 단독 관리. 다른 위치 박지 말 것.

★ 누수 방지 설계:
   1. 표준 라이브러리만 사용 (importlib·sqlite3·urllib·pathlib·subprocess) — 외부 의존 0.
   2. 외부 라이브러리는 *검증 대상*. 자기 자신이 그것을 import 하지 않음.
   3. GUARDIAN/텔레그램/DB 도 *검증 대상이자 fallback 대상* — 모두 try/except 격리.
   4. 한 항목 실패해도 *전체 검증 계속 진행* → 사용자에게 *한 번에 전체 실패 리스트* 보고.
   5. 검증 *읽기 전용* — preflight 자체가 시스템 변경 일으키지 않음.

호출 (jarvis_daemon.main() 초입 1회만):
    from JARVIS00_INFRA.preflight import run_preflight
    run_preflight()   # 실패 시 sys.exit(1) — main() 의 다른 코드 도달 안 함
"""
from __future__ import annotations

import importlib
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# jarvis_daemon 과 동일한 logger — 데몬 모드에서 daemon.log 에 박힘.
# CLI 모드 (직접 실행) 에서는 logger 가 핸들러 미설정이면 stderr 로 가지만,
# print() 도 같이 호출하므로 콘솔 가시성 유지.
_log = logging.getLogger("jarvis")

# 프로젝트 루트 — 이 파일은 jarvis-agent/JARVIS00_INFRA/preflight.py
_ROOT = Path(__file__).resolve().parent.parent

# ── 검증 대상 박제 ─────────────────────────────────────────────────

# ★ 핵심 모듈 import (이 중 하나라도 폭발하면 발행 잡 전부 실패)
_REQUIRED_INTERNAL_MODULES = (
    "shared.llm",
    "shared.bus",
    "shared.db",
    "shared.tools",
    "shared.notify",
    "JARVIS02_WRITER.collect_theme",      # 7시 사고 진원지
    "JARVIS02_WRITER.jarvis_main",
    "JARVIS02_WRITER.economic_poster",
    "JARVIS02_WRITER.law_enforcer",
    "JARVIS02_WRITER.length_manager",
    "JARVIS02_WRITER.trend_economic_writer",
    "JARVIS02_WRITER.trend_theme_writer",
    "JARVIS02_WRITER.theme_html_writer",
    "JARVIS04_SCHEDULER.job_registry",
    "JARVIS04_SCHEDULER.job_catalog",
    "JARVIS06_IMAGE.image_agent",
    "JARVIS07_GUARDIAN.error_collector",
    "JARVIS07_GUARDIAN.error_fixer",
    "JARVIS07_GUARDIAN.pattern_fixer",
    "JARVIS08_PUBLISH.platforms.naver_poster",
    "JARVIS08_PUBLISH.platforms.tistory_poster",
)

# ★ 외부 의존 — pip 설치 패키지 중 *발행 흐름이 의존* 하는 것
_REQUIRED_EXTERNAL_MODULES = (
    "crewai",         # 테마글 collect_theme Agent
    "langchain_core", # router LangChain adapter
    "yfinance",       # 종목 데이터
    "dotenv",         # 환경변수 로드
    "selenium",       # 네이버·티스토리 발행
    "apscheduler",    # 모든 cron 잡
    "requests",       # HTTP 요청
    "bs4",            # 네이버 금융 파싱
    "PIL",            # 이미지 처리 (Pillow → PIL)
    "matplotlib",     # 차트 생성
    "feedparser",     # ★ 2026-07-03: JARVIS09 news/blog provider RSS 파싱 (providers/__init__ top-level import)
)

# ★ 환경변수 — 발행 잡이 필요로 하는 *필수* 키만 (선택 키는 검증 안 함)
_REQUIRED_ENV_VARS = (
    "TELEGRAM_TOKEN",
    "TELEGRAM_CHAT_ID",
    "NV_URL",
    "NV_USERNAME",
    "NV_PASSWORD",
    "TS_URL",
    "TS_USERNAME",
    "TS_PASSWORD",
)

# ★ 헌법·정책 파일 — 존재 자체가 시스템 가동 전제
_REQUIRED_POLICY_FILES = (
    "CLAUDE.md",
    "JARVIS02_WRITER/BLOG_SUPREME_LAW.md",
    "docs/decisions/README.md",
)

# ★ DB 핵심 테이블 — 부팅 시점에 존재해야 발행 흐름이 안전
_REQUIRED_DB_TABLES = (
    "post_analysis",
    "error_log",
)

_SDK_BIN_CANDIDATES = (
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
)


# ── 검증 결과 컨테이너 ─────────────────────────────────────────────

@dataclass
class PreflightReport:
    failures: list[tuple[str, str, str]] = field(default_factory=list)
    """list of (category, item, reason). 한 번에 전체 리스트 누적."""

    warnings: list[tuple[str, str, str]] = field(default_factory=list)
    """발행 차단까지는 아니지만 사용자 주의 필요 항목."""

    @property
    def ok(self) -> bool:
        return not self.failures

    def fail(self, category: str, item: str, reason: str) -> None:
        self.failures.append((category, item, reason[:200]))

    def warn(self, category: str, item: str, reason: str) -> None:
        self.warnings.append((category, item, reason[:200]))


# ── 개별 검증기 ────────────────────────────────────────────────────

def _check_internal_imports(report: PreflightReport) -> None:
    """핵심 내부 모듈 import 검증 (★ 7시 사고 type 차단)."""
    # 프로젝트 루트가 sys.path 에 있어야 import 가능 — jarvis_daemon 이 보장하지만
    # preflight 가 *먼저* 호출되므로 자체적으로도 보장.
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    for mod_name in _REQUIRED_INTERNAL_MODULES:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            report.fail("internal_import", mod_name, f"{type(e).__name__}: {e}")


def _check_external_imports(report: PreflightReport) -> None:
    """외부 의존 import 검증 — 발행 흐름이 의존하는 패키지만."""
    for mod_name in _REQUIRED_EXTERNAL_MODULES:
        try:
            importlib.import_module(mod_name)
        except Exception as e:
            report.fail("external_import", mod_name, f"{type(e).__name__}: {e}")


def _check_env_vars(report: PreflightReport) -> None:
    """필수 환경변수 검증. .env 는 jarvis_daemon 시작 시 dotenv 가 로드."""
    # dotenv 로드 시도 — 표준 dotenv 가 없으면 .env 직접 파싱
    _load_env_if_missing()

    for key in _REQUIRED_ENV_VARS:
        v = os.environ.get(key, "").strip()
        if not v:
            report.fail("env_var", key, "값 없음 또는 빈 문자열")
        elif len(v) < 3:
            report.warn("env_var", key, f"길이 {len(v)} — 의심스러움")


def _load_env_if_missing() -> None:
    """dotenv 가 import 가능하면 사용, 안 되면 .env 직접 파싱."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path)
    except Exception:
        # fallback: 표준 라이브러리로 직접 파싱
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)
        except Exception:
            pass


def _check_policy_files(report: PreflightReport) -> None:
    """헌법·정책 파일 존재 검증."""
    for rel in _REQUIRED_POLICY_FILES:
        p = _ROOT / rel
        if not p.exists():
            report.fail("policy_file", rel, "파일 없음")
        elif p.stat().st_size < 100:
            report.fail("policy_file", rel, f"크기 {p.stat().st_size}bytes — 비정상")


def _check_db_integrity(report: PreflightReport) -> None:
    """DB 무결성 검증 — 핵심 테이블 존재 + 열기 가능."""
    from shared.db import DB_PATH as db_path
    if not db_path.exists():
        report.fail("db", str(db_path), "DB 파일 없음")
        return
    try:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {r[0] for r in cur.fetchall()}
            for t in _REQUIRED_DB_TABLES:
                if t not in tables:
                    report.fail("db", f"table:{t}", "테이블 없음")
        finally:
            con.close()
    except sqlite3.Error as e:
        report.fail("db", "open", f"sqlite3 오류: {e}")


def _check_claude_sdk_binary(report: PreflightReport) -> None:
    """Claude Code SDK 런타임 검증 — claude-code-sdk Python 패키지가 내부적으로 spawn 함.
    (`claude` 는 npm 패키지 @anthropic-ai/claude-code 의 실행 파일명 — 변경 불가)"""
    import shutil as _sh
    found = _sh.which("claude")
    if found:
        return
    for cand in _SDK_BIN_CANDIDATES:
        if Path(cand).exists():
            return
    report.fail("claude_sdk_binary", "binary", "Claude Code SDK 런타임 PATH 에 없음 + 알려진 경로 부재")


def _check_disk_space(report: PreflightReport) -> None:
    """디스크 여유 공간 검증 — 발행 잡이 이미지·로그 생성하므로 최소 1GB 필요."""
    try:
        import shutil as _sh
        free_bytes = _sh.disk_usage(str(_ROOT)).free
        free_mb = free_bytes / (1024 * 1024)
        if free_mb < 500:
            report.fail("disk", "free_space", f"여유 {free_mb:.0f}MB — 1GB 미만 위험")
        elif free_mb < 1024:
            report.warn("disk", "free_space", f"여유 {free_mb:.0f}MB — 권장 1GB+")
    except Exception as e:
        report.warn("disk", "free_space", f"확인 실패: {e}")


# ── 검증기 카탈로그 ────────────────────────────────────────────────

_CHECKERS: tuple[tuple[str, Callable[[PreflightReport], None]], ...] = (
    ("policy_file",    _check_policy_files),     # 헌법 파일이 첫 게이트
    ("env_var",        _check_env_vars),         # 환경변수 먼저 로드해야 다른 검증 가능
    ("claude_sdk_binary", _check_claude_sdk_binary),  # 바이너리 없으면 SDK 호출 불가
    ("disk",           _check_disk_space),       # 디스크 부족이면 즉시 차단
    ("external_import", _check_external_imports),# 외부 의존 — 내부보다 먼저
    ("internal_import", _check_internal_imports),# 내부 모듈 — 외부 통과 후에야 의미
    ("db",             _check_db_integrity),     # DB — 마지막 (다른 검증과 독립)
)


# ── 실패 처리 ──────────────────────────────────────────────────────

def _report_to_guardian(failures: list[tuple[str, str, str]]) -> None:
    """GUARDIAN error_collector 에 박제 (가능 시). 자체 학습 자산화.

    error_collector.report() 의 context 표준은 *문자열* — json.dumps 로 직렬화 후 전달.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import report as g_report
    except Exception:
        return
    import json as _json
    for category, item, reason in failures:
        try:
            # synthetic exception — GUARDIAN 표준 형식
            exc = RuntimeError(f"[preflight] {category}/{item}: {reason}")
            ctx = _json.dumps(
                {"category": category, "item": item, "reason": reason},
                ensure_ascii=False,
            )
            g_report(
                source="preflight",
                exc=exc,
                module=f"JARVIS00_INFRA.preflight.{category}",
                func_name="_check_" + category,
                context=ctx,
            )
        except Exception:
            # GUARDIAN 자체 실패 — 텔레그램 fallback 에서 알림
            pass


def _notify_telegram(failures: list[tuple[str, str, str]], warnings: list[tuple[str, str, str]]) -> None:
    """텔레그램으로 부팅 차단 알림. shared.notify 미가용 시 urllib fallback."""
    msg = "🚨 *데몬 부팅 차단 — Layer 0 preflight 실패*\n\n"
    msg += f"❌ *실패 {len(failures)}건*:\n"
    for category, item, reason in failures[:20]:
        msg += f"  • `{category}/{item}` — {reason[:80]}\n"
    if len(failures) > 20:
        msg += f"  ... 외 {len(failures) - 20}건\n"
    if warnings:
        msg += f"\n⚠️ *경고 {len(warnings)}건* (차단은 아님):\n"
        for category, item, reason in warnings[:5]:
            msg += f"  • `{category}/{item}` — {reason[:80]}\n"
    msg += "\n호스트 macOS 에서 문제 해결 후 재기동 필요."

    # 1순위: shared.notify
    try:
        from shared.notify import send_tg  # type: ignore
        send_tg(msg)
        return
    except Exception:
        pass

    # 2순위: urllib 직접 호출
    try:
        import urllib.request
        import urllib.parse
        import json
        token = os.environ.get("TELEGRAM_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not (token and chat_id):
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "Markdown",
        }).encode()
        urllib.request.urlopen(url, data=data, timeout=10).read()
    except Exception:
        pass  # 마지막 fallback — stderr 출력은 호출자에서


def _print_report(report: PreflightReport) -> None:
    """stderr + logging 양쪽 출력 — CLI 모드 (콘솔) + 데몬 모드 (daemon.log) 일관."""
    header = "🚨 LAYER 0 PREFLIGHT — 부팅 차단"
    # stderr (CLI 모드 콘솔 가시성)
    sys.stderr.write("\n" + "=" * 60 + "\n")
    sys.stderr.write(header + "\n")
    sys.stderr.write("=" * 60 + "\n")
    # logging (데몬 모드 daemon.log)
    _log.error(header)
    if report.failures:
        sys.stderr.write(f"\n❌ 실패 {len(report.failures)}건:\n")
        _log.error(f"❌ 실패 {len(report.failures)}건")
        for category, item, reason in report.failures:
            line = f"  [{category:<18}] {item}: {reason}"
            sys.stderr.write(line + "\n")
            _log.error(f"[preflight] {category}/{item}: {reason}")
    if report.warnings:
        sys.stderr.write(f"\n⚠️  경고 {len(report.warnings)}건 (차단 아님):\n")
        _log.warning(f"⚠️ 경고 {len(report.warnings)}건 (차단 아님)")
        for category, item, reason in report.warnings:
            line = f"  [{category:<18}] {item}: {reason}"
            sys.stderr.write(line + "\n")
            _log.warning(f"[preflight] {category}/{item}: {reason}")
    sys.stderr.write("\n호스트 macOS 에서 문제 해결 후 데몬 재기동 필요.\n")
    sys.stderr.write("=" * 60 + "\n\n")


# ── 메인 진입점 ────────────────────────────────────────────────────

def run_preflight(strict: bool = True) -> PreflightReport:
    """Layer 0 부팅·환경 검증 — jarvis_daemon.main() 초입에서 1회만 호출.

    ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess 자식 우회 차단.
       부모 프로세스가 JARVIS_PREFLIGHT_DONE=1 환경변수를 자식에 전파 → 자식은
       경량 모드로 skip (단 ensure_preflight() 가 호출되어야 함). 환경변수 없으면
       자식도 완전 검증 수행 → 우회 진입점 방어.

    Args:
        strict: True (기본) 면 실패 시 sys.exit(1) 으로 부팅 차단.
                False 면 보고서만 반환 (테스트·진단용).

    Returns:
        PreflightReport — strict=False 일 때만 의미.

    Side effects (strict=True 일 때):
        - GUARDIAN 에 실패 항목 박제 (학습 자산화)
        - 텔레그램 알림
        - stderr 보고서 출력
        - sys.exit(1) 으로 프로세스 종료
    """
    report = PreflightReport()

    for category, checker in _CHECKERS:
        try:
            checker(report)
        except Exception as e:
            # 검증기 자체 폭발 — 자기 결함이지만 부팅은 차단해야 함
            report.fail("preflight_self", category, f"검증기 폭발: {type(e).__name__}: {e}")

    if report.ok:
        # 성공 — print (CLI 모드 콘솔) + log.info (데몬 모드 daemon.log) 양쪽
        warn_n = len(report.warnings)
        suffix = f" (경고 {warn_n}건)" if warn_n else ""
        msg = f"✅ Layer 0 preflight 통과{suffix}"
        print(msg)
        _log.info(msg)
        if report.warnings:
            for category, item, reason in report.warnings:
                _log.warning(f"[preflight] {category}/{item}: {reason}")
        # ★ P1-④ 패치: 자식 프로세스 전파용 마커
        os.environ["JARVIS_PREFLIGHT_DONE"] = "1"
        return report

    # 실패 — 보고·박제·차단
    _print_report(report)
    _report_to_guardian(report.failures)
    _notify_telegram(report.failures, report.warnings)

    if strict:
        sys.exit(1)
    return report


def ensure_preflight(strict: bool = True) -> PreflightReport | None:
    """★ P1-④ 패치 (사용자 박제 2026-05-18) — subprocess 자식 진입점용 보장 함수.

    호출 위치: subprocess 로 실행되는 모든 스크립트 (radar_main.py · performance_collector.py ·
    economic_poster.py CLI · trend_*.py CLI 등) 의 if __name__ == "__main__" 블록 최상단.

    동작:
      - 부모가 JARVIS_PREFLIGHT_DONE=1 박았으면 skip (이미 검증됨).
      - 미박혀 있으면 *완전 preflight* 실행. CLI 직접 실행·외부 호출 진입점에서
        Layer 0 우회 차단.

    Returns:
        PreflightReport (preflight 실행 시) 또는 None (skip 시).
    """
    if os.environ.get("JARVIS_PREFLIGHT_DONE") == "1":
        _log.debug("[preflight] 부모 프로세스에서 이미 통과 — skip")
        return None
    return run_preflight(strict=strict)


__all__ = ["run_preflight", "ensure_preflight", "PreflightReport"]


if __name__ == "__main__":
    # CLI 진단 모드 — strict=False 로 보고서만 출력
    import argparse
    parser = argparse.ArgumentParser(description="Layer 0 preflight 진단 (CLI 모드)")
    parser.add_argument("--strict", action="store_true",
                        help="실패 시 sys.exit(1) (운영 모드)")
    args = parser.parse_args()
    rpt = run_preflight(strict=args.strict)
    sys.exit(0 if rpt.ok else 2)
