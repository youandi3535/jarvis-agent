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
    "JARVIS09_COLLECTOR.collect_theme",   # 7시 사고 진원지 (02 shim 폐지 2026-07-23 — 본체 위치)
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

def _ensure_root_on_path() -> None:
    """프로젝트 루트를 sys.path 에 보장 — ★ 검증기들보다 *먼저* 불려야 한다.

    ★ 왜 run_preflight 초입으로 올렸나 (2026-08-10 실측)
      종전엔 이 보장이 `_check_internal_imports` 안에만 있었다. 그런데 검증기 순서상
      `chart_font`(그리고 새 `data_verifier`)가 그보다 **앞**에서 돈다. 그래서
      `python3 JARVIS00_INFRA/preflight.py` 나 하위 폴더 스크립트를 직접 실행하면
      sys.path[0] 이 그 폴더라 JARVIS06 을 못 찾고, 두 스모크가 조용히
      "확인 불가" 로 넘어갔다 — 실측으로 확인했다.
      *배선해 둔 안전장치가 절반의 진입점에서 한 번도 안 도는* 상태였고,
      그것이 바로 이번 사고의 형태다("코드 존재는 적용의 증거가 아니다").
    정의는 여기 한 곳 — 호출자(run_preflight·_check_internal_imports)는 부르기만 한다.
    """
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))


def _check_internal_imports(report: PreflightReport) -> None:
    """핵심 내부 모듈 import 검증 (★ 7시 사고 type 차단).

    ★ 재시도 상한은 `_max_attempts()`(harness SSOT 파생, 현재 2회) — naver_poster
       등은 pyobjc(Quartz/AppKit) 를 함수 내부에서만 지연 import 하므로 자체 코드로는
       "pyobjc-core and pyobjc" AssertionError 를 낼 수 없다(AST 확인 완료). 실제 사고는
       *여러 subprocess 진입점(economic_poster/naver_cookie_refresher 등)이 동시에
       ensure_preflight() 를 콜드 프로세스로 호출*할 때 macOS objc 브릿지가 드물게
       레이스를 일으켜 일시적으로 폭발한 것 — 6초 뒤 같은 프로세스군에서 즉시 재통과.
       진짜 코드 결함(ImportError/AttributeError 등)은 재시도해도 동일하게 실패하므로
       은폐되지 않는다.
    """
    import time as _time

    _ensure_root_on_path()

    # 재시도 상한 — 파생 leaf 하나(`shared/limits.py`). 루트 경로를 올린 *뒤* 에 부른다
    # (preflight 는 직접 실행 진입점이라 모듈레벨 import 는 sys.path 보장 전이다).
    from shared.limits import max_attempts as _max_attempts

    for mod_name in _REQUIRED_INTERNAL_MODULES:
        last_exc: Exception | None = None
        for attempt in range(_max_attempts()):
            try:
                importlib.import_module(mod_name)
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                if attempt < _max_attempts() - 1:   # 마지막 시도 뒤엔 자지 않는다
                    _time.sleep(0.5)
        if last_exc is not None:
            report.fail("internal_import", mod_name, f"{type(last_exc).__name__}: {last_exc}")


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


def _check_secret_masking(report: PreflightReport) -> None:
    """시크릿 로그 마스킹이 *실제로 먹는지* 검증 (patch_effective 표준).

    ★ 왜 Layer 0 인가 (2026-08-04 감사 9위)
      로그에 평문 API 키가 3,006회 남아 있었다. 발생원은 우리 코드가 아니라 `httpx`
      가 URL 을 통째로 찍는 INFO 로그였고, 마스킹은 DB 관문 2곳만 덮고 있었다.
      *부팅 시점에 안 먹으면 그 뒤 모든 로그가 오염된다* — 그래서 부팅 게이트다.

    ★ 등급을 나누는 이유 (② 동적 설계 — 상황에서 파생)
      preflight 는 데몬만이 아니라 CLI·subprocess 자식도 부른다. 거기엔 필터가
      안 붙어 있는 게 정상이다. 그러므로
        · 필터 *미부착*  → 경고 (문맥상 정상일 수 있음)
        · 필터 부착됐는데 *안 먹음* → 실패 (확실한 고장)
    """
    try:
        from shared import secrets as _sec
        # ★ 검사만 하지 않고 **여기서 건다** (2026-08-05 정정).
        #   종전엔 데몬 부팅 1곳에서만 필터를 걸었다. 그런데 발행·분석은 **subprocess**
        #   로 돈다(`scheduler.py`·`post_quality_analyzer.py`). 그 자식들은 자기 로깅을
        #   따로 세우므로 부모의 루트 필터가 닿지 않는다 — 실측으로
        #   `JARVIS02_WRITER/logs/scheduler.log` 에 봇 토큰이 26회 평문으로 남았다
        #   (requests 예외 메시지가 URL 을 통째로 담는다).
        #   `ensure_preflight()` 는 **모든 __main__ 진입점의 의무 호출** 이다(CLAUDE.md).
        #   즉 자식이든 부모든 반드시 여기를 지난다 — 그래서 여기가 진짜 단일 진입점이다.
        _sec.install_log_masking()
        sc = _sec.selfcheck()
        if sc.get("issues"):
            report.warn("secret_masking", "selfcheck", "; ".join(sc["issues"]))
        attached = _sec.masking_filter_attached()
        if attached and not _sec.masking_effective():
            report.fail("secret_masking", "effective",
                        "마스킹 필터가 붙었으나 실제로 가리지 못함 — 로그 오염 진행 중")
        elif not attached:
            report.warn("secret_masking", "install",
                        "루트 로거에 마스킹 필터 미부착 (데몬이 아니면 정상)")
    except Exception as e:
        report.warn("secret_masking", "selfcheck", f"확인 실패: {type(e).__name__}: {e}")


def _check_chart_font(report: PreflightReport) -> None:
    """차트 한글 폰트가 *실제로 적용되는지* 동작으로 확인 (patch_effective 표준).

    ★ 왜 Layer 0 인가 (ERRORS [459] — 본문 차트 한글 두부(□□□) **무증상** 발행)
      폰트 설정은 "시도" 이지 "적용" 이 아니다. rcParams 에 이름을 넣어도 그 폰트 파일에
      한글 글리프가 없으면 조용히 □□□ 로 그려지고, 발행은 성공으로 끝난다.
      `matplotlib_renderer.font_effective()` 는 선택된 폰트의 charmap 에 U+ACBD('경')이
      있는지 보는 *효과* 판정인데, **호출자가 0곳이라 한 번도 돌지 않았다**(2026-08-08 실측).
      만들어 두고 배선하지 않은 안전장치는 없는 것과 같다.

    ★ 왜 fail 이 아니라 warn 인가 (② 상황에서 파생)
      이 결함은 *차트 이미지* 만 망가뜨린다. 부팅을 막으면 글·발행·수집까지 전부 멈춰
      피해가 원인보다 커진다. 이 사고의 본질은 "깨졌다" 가 아니라 **"아무도 몰랐다"** 이므로,
      부팅 보고에 드러나게 하는 것으로 충분하다.
    """
    try:
        from JARVIS06_IMAGE.matplotlib_renderer import font_effective
    except Exception as e:
        report.warn("chart_font", "import", f"확인 불가: {type(e).__name__}: {e}")
        return
    try:
        eff = font_effective()
    except Exception as e:
        report.warn("chart_font", "effective", f"판정 실패: {type(e).__name__}: {e}")
        return
    if eff is False:
        report.warn("chart_font", "effective",
                    "차트 한글 폰트가 적용되지 않음 — 그래프 글자가 □□□ 로 발행된다 "
                    "(한글 폰트 설치 후 재기동 필요)")
    elif eff is None:
        report.warn("chart_font", "effective", "판정 불가 (matplotlib 미설치이면 정상)")


def _check_data_verifier(report: PreflightReport) -> None:
    """이미지 *수치 사실성* 검증기가 실제로 동작하는지 (patch_effective 표준).

    ★ 왜 Layer 0 인가 (2026-08-10 경제 브리핑 8장 사고)
      검증기는 **있었다**. 그런데 그 검증기가 동작한다는 증거를 부팅에서 아무도 묻지
      않았고, 그날 8장은 검증 기록조차 없이 발행됐다. 아이러니하게도
      `shared/precommit_check.py` 는 이미 `verifier_effective()` 의 *존재* 를 요구한다 —
      존재만 요구하고 **부르지는 않았다**. 코드 존재는 적용의 증거가 아니다(CLAUDE.md).
      그래서 `_check_secret_masking`·`_check_chart_font` 와 똑같은 자리에서, 조작 수치를
      한 번 통과시켜 False 가 나오는지 *동작으로* 확인한다.

    ★ 왜 기본이 fail 이 아니라 warn 인가 (② 상황에서 파생 — chart_font 관례)
      검증기가 죽어도 *거짓 이미지가 밖으로 나가지는 않는다*:
      `prepublish_gate._image_factuality_leg` 가 같은 스모크를 fail-closed 로 다시 보고
      발행을 차단한다. 즉 이 상태의 피해는 '발행 중단' 이지 '거짓 발행' 이 아니므로,
      부팅을 죽여 수집·분석·대시보드까지 멈추면 피해가 원인보다 커진다.
      이 사고의 본질은 "깨졌다" 가 아니라 **"아무도 몰랐다"** — 보고에 드러나면 충분하다.

    ★ 단, 그물이 하나도 없으면 fail (파생 판정 — 임계값이 아니라 상태에서 나온다)
      이미지 게이트 킬스위치가 꺼져 있으면(`IMAGE_DATA_GATE=0`) 미검증 이미지가 폐기되지
      않는다. '검증기 고장 + 게이트 꺼짐' 은 검증이 *한 겹도* 남지 않은 상태다.
      이때만 부팅을 막는다. 스위치 이름은 검증기(owner)가 가진 상수에서 가져온다 —
      여기 문자열로 다시 적으면 그 순간 사본이 되고, 이름이 바뀌면 조용히 어긋난다.
    """
    try:
        from JARVIS06_IMAGE.validators.image_data_verifier import (
            verifier_effective, gate_enabled, GATE_ENV)
    except Exception as e:
        report.warn("data_verifier", "import",
                    f"이미지 수치 검증기 확인 불가: {type(e).__name__}: {e}")
        return
    try:
        gate_on = bool(gate_enabled())
    except Exception:
        gate_on = True
    try:
        healthy = verifier_effective() is True
    except Exception as e:
        # verifier_effective 는 자체적으로 예외를 삼켜 False 를 주지만, import 이후의
        # 예기치 못한 폭발도 '무력' 으로 본다 (fail-closed 방향).
        report.warn("data_verifier", "effective", f"판정 실패: {type(e).__name__}: {e}")
        healthy = False
    if not healthy:
        msg = ("이미지 수치 검증기가 조작 수치를 통과시킴 — 차트 사실성 검증 무력 "
               "(발행은 prepublish_gate 가 차단하므로 이미지가 나가진 않는다)")
        if gate_on:
            report.warn("data_verifier", "effective", msg)
        else:
            report.fail("data_verifier", "effective",
                        f"{msg} + {GATE_ENV}=0 으로 폐기까지 꺼져 있음 — 남은 그물 0")
    elif not gate_on:
        report.warn("data_verifier", "killswitch",
                    f"{GATE_ENV}=0 — 미검증 이미지를 폐기하지 않는 상태로 가동 중")


# ── 검증기 카탈로그 ────────────────────────────────────────────────

_CHECKERS: tuple[tuple[str, Callable[[PreflightReport], None]], ...] = (
    ("policy_file",    _check_policy_files),     # 헌법 파일이 첫 게이트
    ("chart_font",     _check_chart_font),       # 차트 한글 폰트 *효과* 확인 (ERRORS [459])
    ("data_verifier",  _check_data_verifier),    # 이미지 수치 검증기 *효과* 확인 (2026-08-10)
    ("env_var",        _check_env_vars),         # 환경변수 먼저 로드해야 다른 검증 가능
    ("claude_sdk_binary", _check_claude_sdk_binary),  # 바이너리 없으면 SDK 호출 불가
    ("disk",           _check_disk_space),       # 디스크 부족이면 즉시 차단
    ("external_import", _check_external_imports),# 외부 의존 — 내부보다 먼저
    ("internal_import", _check_internal_imports),# 내부 모듈 — 외부 통과 후에야 의미
    ("db",             _check_db_integrity),     # DB — 마지막 (다른 검증과 독립)
    ("secret_masking", _check_secret_masking), # 로그 오염 방지 — 부팅 후 전 구간 영향
)


# ── 실패 처리 ──────────────────────────────────────────────────────

def _is_canonical_venv() -> bool:
    """이 프로세스가 프로젝트 정본 `.venv` 인터프리터로 도는가.

    ★ 왜 필요한가 (2026-08-11 사고, error_log #5840~5846): CI 재현용 "최소 의존 venv"
      (`requirements-test.txt` 만 설치)로 진입점을 pytest 밖에서 직접 실행하면
      langchain_core·yfinance·bs4·PIL·matplotlib·feedparser 가 동시에 ModuleNotFoundError
      난다 — *운영 venv 결함이 아니라 검증 방법론의 산물*(실제 `.venv` 는 전부 정상 — 재현 확인함).
      그런데 GUARDIAN DB(`~/.jarvis/jarvis.sqlite`, ERRORS [535])는 프로젝트 밖 홈 경로라
      *어떤 venv 로 실행하든 같은 DB 를 공유* 하고, pytest 밖에서 돌리면
      `tests/conftest.py` 의 `JARVIS_TEST_MODE` 격리도 안 걸린다 — 그대로 실제 GUARDIAN
      오케스트레이터가 물고 Tier-2 LLM 수정까지 낭비했다(7건 동시 "high" 오탐).
      정본 인터프리터가 아니면 실패를 *그 프로세스만의 문제*로 보고 GUARDIAN 박제·
      텔레그램 알림을 skip — 실패 자체(및 sys.exit)는 그대로 유지해 이상 환경에서
      계속 진행되지는 않는다.

    ★ 계산은 여기서 다시 하지 않는다 — 단일 진실 소스는
      `JARVIS07_GUARDIAN.error_collector._is_canonical_interpreter()`. 같은 `sys.prefix`
      비교를 두 파일에 사본으로 두면 한쪽만 고쳐질 때 판정이 갈라진다(CLAUDE.md
      '복사본을 진실로 믿지 말 것' — 종전엔 실제로 이 파일에 별도 사본이 있었다).
      GUARDIAN 은 이미 이 파일의 *검증 대상이자 fallback 대상*이라 지연 import 관례가
      있다(`_report_to_guardian` 참조) — "표준 라이브러리만" 원칙과 충돌하지 않는다.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import _is_canonical_interpreter
        return _is_canonical_interpreter()
    except Exception:
        return True  # 판정 불가 — 기존 동작(보고) 유지


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
                exc,                       # ★ FIX[5]: exc 는 첫 위치인자 (catch 시그니처엔 exc= 없음 → 종전 TypeError→빈 except 삼킴)
                source="preflight",
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
        # ★ 테스트에선 나가지 않는다 (2026-08-09) — Layer 0 은 `shared.notify` 를 못 쓰는
        #   *문서화된 예외* 지만, '예외' 는 차단 면제가 아니다. 통로가 둘이면 둘 다 막는다(③).
        if (os.environ.get("JARVIS_TEST_MODE", "") or "").strip() not in ("", "0"):
            return
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
    _ensure_root_on_path()      # ★ 모든 검증기보다 먼저 — 안 그러면 스모크가 조용히 꺼진다

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
    if _is_canonical_venv():
        _report_to_guardian(report.failures)
        _notify_telegram(report.failures, report.warnings)
    else:
        msg = (f"[preflight] 정본 .venv 인터프리터가 아님 ({sys.executable}) — "
               "검증용 실행으로 판단, GUARDIAN 박제·텔레그램 알림 skip")
        sys.stderr.write(f"\n⚠️  {msg}\n")
        _log.warning(msg)

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
