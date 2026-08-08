"""JARVIS pre-commit 검증 — CLAUDE.md 박제 grep 명령 통합 단일 진입점.

CLAUDE.md 박제 규정의 grep 검증을 Python 으로 통합. 의존성 0
(외부 패키지 없음, ripgrep / grep 불요). 위반 발견 시 stderr 출력 +
exit code 1 → git pre-commit 훅이 자동 차단.

# 사용
    python shared/precommit_check.py            # 전체 검증
    python shared/precommit_check.py --category infra   # 특정 카테고리만
    python shared/precommit_check.py --list             # 검증 목록

# 자동 실행 위치
    1. git pre-commit 훅 (.githooks/pre-commit) — 커밋 차단
    2. jarvis_daemon.py 부팅 직전 — 위반 잔존 시 부팅 차단
    3. JARVIS07 Auditor 잡 (주 1회) — 드리프트 회귀 점검

# 검증 카테고리 (CLAUDE.md 박제 그대로)
    infra      — 인프라 단일 진입점 (3종)
    length     — 분량 표기 단일 진입점 (5종)
    blog       — 블로그 헌법 (3종)
    schedule   — 스케줄 단일 진입점 (7종)
    autocode   — 자율 코드 자가수정 (4종)
    tools      — 자율 에이전트 도구 (3종)
    image      — 이미지 생성 단일 진입점 (2종)
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# 공용 헬퍼
# ============================================================================

# 모든 검증에서 공통 제외 (__pycache__ / .venv / .git / backups / chrome_profile / 자기자신)
_GLOBAL_EXCLUDE = (
    "__pycache__",
    ".venv",
    ".git",
    ".claude",                         # Claude Code worktree / 세션 파일 제외
    "shared/backups",
    "chrome_profile",
    "/node_modules/",
    "/.fuse_hidden",
    "_deleted_",                       # 삭제 보관 폴더
    "_export/",                        # 이식용 단일 파일 export (별도 프로젝트 대상)
    "shared/precommit_check.py",       # 검증 스크립트 자기 자신 (regex 문자열 포함)
)


def _is_excluded(path: Path, extra: tuple[str, ...] = ()) -> bool:
    s = str(path)
    if any(ex in s for ex in _GLOBAL_EXCLUDE):
        return True
    if any(ex in s for ex in extra):
        return True
    return False


# ★ 성능 (2026-07-03): rglob 이 .venv 수천 파일까지 매 검증마다 재탐색하던 것을
#   root 별 1회 walk 로 캐시. os.walk 로 제외 디렉토리는 하강 자체를 차단.
#   ⚠️ 캐시는 one-shot 프로세스 전제 — 장수 프로세스에서 run() 재호출 금지 (stale).
#   ⚠️ _PRUNE_DIRS 는 _GLOBAL_EXCLUDE 의 *부분집합*(무조건 제외만) 유지 —
#      'backups' 처럼 경로 한정 제외(shared/backups)는 _is_excluded 가 담당.
_RGLOB_CACHE: dict[str, list[Path]] = {}
_PRUNE_DIRS = {"__pycache__", ".venv", ".git", "node_modules", "chrome_profile"}


def _walk_py(root: Path) -> list[Path]:
    key = str(root)
    if key not in _RGLOB_CACHE:
        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
            for fn in filenames:
                if fn.endswith(".py"):
                    found.append(Path(dirpath) / fn)
        _RGLOB_CACHE[key] = found
    return _RGLOB_CACHE[key]


def _iter_py(extra_exclude: tuple[str, ...] = (), root: Path = ROOT) -> Iterable[Path]:
    """ROOT 하위 *.py 순회 (제외 경로 자동 필터)."""
    for p in _walk_py(root):
        if _is_excluded(p, extra_exclude):
            continue
        yield p


# ★ 성능 (2026-07-03): 검증 30여 종이 각각 전체 트리를 재읽던 것을 1회 읽기로.
#   precommit 은 one-shot 프로세스 — 실행 중 파일 변경 없음 전제로 캐시 안전.
_FILE_CACHE: dict[str, str | None] = {}



def _blank_string_literals(text: str) -> str:
    """문자열 리터럴 *내용* 을 지운다 — 줄 수·줄번호는 그대로 보존.

    ★ 왜 필요한가 (2026-08-08)
      이 검사기는 대부분 글자 찾기라 **주석·문자열 안의 문장을 코드로 착각** 한다
      (README 가 스스로 밝힌 약점). 실제로 `tests/` 의 E2E 프로브가
      `sch.add_job(...)` 을 *subprocess 로 넘길 문자열* 안에 담고 있었는데
      `schedule/add_job` 이 그걸 코드로 보고 커밋을 막았다.
      우회(`--no-verify`)하지 않고 **규칙을 고친다** — README 가 지시한 방향이다.

    토큰 단위로 처리하므로 따옴표 종류·삼중따옴표·f-string 을 가리지 않는다.
    실패 시 원문을 그대로 돌려준다(검사가 파일 하나 때문에 죽지 않게).
    """
    import io
    import tokenize
    try:
        lines = text.splitlines(keepends=True)
        out = list(lines)
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.STRING:
                continue
            (sr, sc), (er, ec) = tok.start, tok.end
            for ln in range(sr, er + 1):
                idx = ln - 1
                if idx >= len(out):
                    break
                cur = out[idx].rstrip("\n")
                a = sc if ln == sr else 0
                b = ec if ln == er else len(cur)
                out[idx] = (cur[:a] + " " * max(0, b - a) + cur[b:]) + "\n"
        return "".join(out)
    except Exception:
        return text

def _read_py(p: Path) -> str | None:
    """파일 내용 반환 (읽기 실패 시 None). 프로세스 수명 동안 캐시."""
    key = str(p)
    if key not in _FILE_CACHE:
        try:
            _FILE_CACHE[key] = p.read_text(encoding="utf-8")
        except Exception:
            _FILE_CACHE[key] = None
    return _FILE_CACHE[key]


def _docstring_lines(source: str) -> set[int]:
    """triple-quote (`\"\"\"` or `'''`) 안의 라인 번호 집합.

    단순 휴리스틱 — 파일 단위 라인 토글. f-string·중첩 quote 등의 정밀 처리는 생략.
    docstring 안의 자연어 분량 표기는 *정책 위반 아님* — 검증 제외용.
    """
    lines = source.splitlines()
    doc_lines: set[int] = set()
    in_doc = False
    for i, line in enumerate(lines, 1):
        tq = line.count('"""') + line.count("'''")
        if in_doc:
            doc_lines.add(i)
            if tq % 2 == 1:
                in_doc = False
        else:
            if tq == 1:
                doc_lines.add(i)
                in_doc = True
            elif tq >= 2:
                doc_lines.add(i)  # 한 줄 docstring
    return doc_lines


@dataclass
class Violation:
    """단일 위반."""
    category: str
    check_id: str
    file: str
    line: int
    text: str

    def fmt(self) -> str:
        return f"  [{self.check_id}] {self.file}:{self.line}: {self.text.strip()[:140]}"


@dataclass
class Report:
    """전체 검증 결과."""
    violations: list[Violation] = field(default_factory=list)
    checks_run: int = 0
    # 실제로 실행된 카테고리 이름 — 보고 문구가 여기서 파생된다(손으로 센 숫자 금지).
    ran: list = field(default_factory=list)

    def add(self, v: Violation) -> None:
        self.violations.append(v)

    @property
    def ok(self) -> bool:
        return not self.violations

    def by_category(self) -> dict[str, list[Violation]]:
        out: dict[str, list[Violation]] = {}
        for v in self.violations:
            out.setdefault(v.category, []).append(v)
        return out


# ============================================================================
# 검증 1 — 인프라 단일 진입점 (CLAUDE.md "인프라 관리 규정")
# ============================================================================

def check_infra(report: Report) -> None:
    """① jarvis00_infra capability 본체 외부 declare
       ② build_status 본체 외부 정의 (루트·shared 만 위반)
       ③ handle_command/handle_safe_intent/execute_approval — 단, 각 에이전트의
          자기 capability handler 정의는 정당. *jarvis00_infra* capability 인지
          파일 내 jarvis00_infra 문자열 동반 여부로 판별.
    """
    cat = "infra"
    extra = ("JARVIS00_INFRA/",)

    pat1 = re.compile(r'declare\([^)]*agent_id[^)]*=[^)]*"jarvis00_infra"')
    pat2 = re.compile(r"^def (build_status|_build_status)\b")
    pat3 = re.compile(r"^def (handle_command|handle_safe_intent|execute_approval)\b")

    for p in _iter_py(extra_exclude=extra):
        text = _read_py(p)
        if text is None:
            continue
        rel = p.relative_to(ROOT)
        rel_s = str(rel)

        # 각 에이전트는 자기 capability handler 정의 가능. infra capability 인 경우만 위반.
        is_infra_context = "jarvis00_infra" in text

        for i, line in enumerate(text.splitlines(), 1):
            if pat1.search(line):
                report.add(Violation(cat, "infra/declare", rel_s, i, line))
            if pat2.match(line):
                report.add(Violation(cat, "infra/build_status", rel_s, i, line))
            if pat3.match(line) and is_infra_context:
                report.add(Violation(cat, "infra/handle_command", rel_s, i, line))

    report.checks_run += 3


# ============================================================================
# 검증 2 — 분량 표기 단일 진입점 (CLAUDE.md "블로그 본문 분량")
# ============================================================================

def check_length(report: Report) -> None:
    """① [가-힣] 정규식 직접 (length_manager / seo 외)
       ② 자연어 분량 표현 ([0-9]+자 (이내|이하|...))
       ③ compress / cap / count 외부 호출
       ④ 글자수 후보 숫자 (2500/2200/1500 자 단위)
       ⑤ 검증 게이트 (\\b2500\\b · \\b2200\\b · len(re.findall(r..[가-힣])))
    """
    cat = "length"
    # 합법 단일 진입점 + 도메인 무관 SEO 표준 정의
    allowed = (
        "length_manager.py",
        "shared/seo.py",
        "JARVIS02_WRITER/seo_standards.py",  # SEO 메타 길이 표준 (블로그 본문 분량 아님)
    )
    base_targets = ("JARVIS02_WRITER", "shared", "JARVIS03_RADAR")

    # 동사 한국어 단어 (예외 — 정규식 패턴이지만 단순 한국어 문자열)
    verb_ko = ("한다", "된다", "있다", "없다", "크다")

    # 분량 측정 패턴 — `[가-힣]` 단독 (수량자 없는, *전체 문자 계수* 의도).
    # 단어 추출 `[가-힣]{N,M}` / 어미 변환 `[가-힣]다` / alternation 안의 매칭은 분량과 무관 → 허용.
    # 진짜 *분량 측정* 의도는 pat5 `len(re.findall(r"...[가-힣]..."))` 가 잡음.
    # pat1 은 명백한 분량 의도 패턴만 (예: 수량자 +, * 단독 — 모든 한글 매칭).
    pat1 = re.compile(r"\[가-힣\]\s*[+*](?!\?)")
    # 자연어 분량 — `30자 이내` 형태. 단, `build_length_phrase()` 결과 표기는 허용.
    pat2 = re.compile(r"[0-9]+자\s*(이내|이하|초과|미만|이상|전후|범위|기준|정도|내외)|[0-9]+\s*~\s*[0-9]+자")
    pat3 = re.compile(r"(cap_content|count_korean|sanitize_body)\(")
    pat3_exempt = re.compile(r"def _cap|return _L\.compress|__all__|\.compress\(")
    # 매직 넘버 — *블로그 본문 분량 한도* 상수만. LLM API max_tokens 는 토큰 한도라 무관.
    pat4 = re.compile(r"(?<!max_tokens=)(?<!max_tokens\s)(MAX_KOREAN|MAX_BODY|_MAX_KOREAN|_BODY_LIMIT)\s*=\s*(2500|2200|1500)")
    pat5 = re.compile(r"len\(re\.findall\(r..\[가-힣\]")

    # build_length_phrase 결과 표기는 허용 + LLM API max_tokens 라인은 분량과 무관
    phrase_exempt = re.compile(
        r"build_length_phrase|build_prompt_length_block|build_short_length_phrase|"
        r"_LM\.|_L\.|max_tokens\s*[:=]|max_tokens=\s*int"
    )

    for tgt in base_targets:
        root = ROOT / tgt
        if not root.exists():
            continue
        for p in _iter_py(root=root):
            if any(a in str(p) for a in allowed):
                continue
            text = _read_py(p)
            if text is None:
                continue
            rel = p.relative_to(ROOT)
            rel_s = str(rel)
            doc_lines = _docstring_lines(text)
            for i, line in enumerate(text.splitlines(), 1):
                ls = line.strip()
                if ls.startswith("#"):
                    continue
                if i in doc_lines:  # docstring 안은 검증 제외
                    continue
                if phrase_exempt.search(line):
                    continue
                if pat1.search(line) and not any(v in line for v in verb_ko):
                    report.add(Violation(cat, "length/korean-regex", rel_s, i, line))
                if pat2.search(line):
                    report.add(Violation(cat, "length/natural-phrase", rel_s, i, line))
                if pat3.search(line) and not pat3_exempt.search(line):
                    report.add(Violation(cat, "length/compress-call", rel_s, i, line))
                if pat4.search(line):
                    report.add(Violation(cat, "length/magic-number", rel_s, i, line))
                if pat5.search(line):
                    report.add(Violation(cat, "length/gate", rel_s, i, line))

    report.checks_run += 5


# ============================================================================
# 검증 3 — 블로그 헌법 (CLAUDE.md "블로그 글·이미지·소제목")
# ============================================================================

def check_blog(report: Report) -> None:
    """① 고정 한국어 풀·폴백 상수 (제1-B조 위반)
       ② (생략) 이미지 연속·빈 헤더는 DB 런타임 검증 — pre-commit 범위 외.
    """
    cat = "blog"
    pat = re.compile(r'FALLBACK_TEXT\s*=\s*["\(]|FALLBACK_HTML\s*=\s*["\(]|_CTA_POOL\s*=')

    writer = ROOT / "JARVIS02_WRITER"
    if writer.exists():
        for p in _iter_py(root=writer):
            text = _read_py(p)
            if text is None:
                continue
            rel = p.relative_to(ROOT)
            for i, line in enumerate(text.splitlines(), 1):
                if pat.search(line):
                    report.add(Violation(cat, "blog/fixed-pool", str(rel), i, line))

    report.checks_run += 1


# ============================================================================
# 검증 4 — 스케줄 단일 진입점 (CLAUDE.md "스케줄 관리 규정")
# ============================================================================

def check_schedule(report: Report) -> None:
    """7종 검증 — add_job / BackgroundScheduler / apscheduler import /
       add_listener / _apscheduler / schedule 라이브러리 / 시간 폴링.
    """
    cat = "schedule"
    sched_dir = "JARVIS04_SCHEDULER/"

    pat1 = re.compile(r"scheduler\.add_job\(|\.add_job\(")
    pat2 = re.compile(r"BackgroundScheduler\(|BlockingScheduler\(")
    pat3 = re.compile(r"^from apscheduler|^import apscheduler", re.MULTILINE)
    pat4 = re.compile(r"\.add_listener\(")
    pat5 = re.compile(r"\b_apscheduler\b")
    pat6 = re.compile(r"schedule\.every\(|schedule\.run_pending|^import schedule\b|^from schedule\b")
    pat7 = re.compile(r"current_hour\s*==|current_hour\s*in\s*\[|now\(\)\.hour\s*==")

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        text = _read_py(p)
        if text is None:
            continue
        # ★ 문자열 리터럴 안의 코드 조각은 *실행되는 코드가 아니다* (2026-08-08).
        #   E2E 테스트가 subprocess 로 넘길 프로브를 문자열에 담으면 여기 걸렸다.
        text = _blank_string_literals(text)
        for i, line in enumerate(text.splitlines(), 1):
            ls = line.strip()
            if ls.startswith("#"):
                continue

            # ① add_job — JARVIS04 외부
            if pat1.search(line) and sched_dir not in rel_s:
                report.add(Violation(cat, "schedule/add_job", rel_s, i, line))
            # ② BackgroundScheduler — JARVIS04 외부
            if pat2.search(line) and sched_dir not in rel_s:
                report.add(Violation(cat, "schedule/scheduler-instance", rel_s, i, line))
            # ④ add_listener — job_history.py 외부
            # 예외: JARVIS07 GUARDIAN 의 error 추적 listener (다른 목적·중복 위험 0)
            if (pat4.search(line) and "job_history.py" not in rel_s
                    and "JARVIS07_GUARDIAN/guardian_agent.py" not in rel_s):
                report.add(Violation(cat, "schedule/add_listener", rel_s, i, line))
            # ⑤ _apscheduler 글로벌 — jarvis_daemon·JARVIS04 외부
            if pat5.search(line) and sched_dir not in rel_s and "jarvis_daemon.py" not in rel_s:
                report.add(Violation(cat, "schedule/apscheduler-ref", rel_s, i, line))
            # ⑥ schedule 라이브러리
            if pat6.search(line):
                report.add(Violation(cat, "schedule/schedule-lib", rel_s, i, line))
            # ⑦ 시간 폴링 — scheduler.py 주석 패턴 제외
            if pat7.search(line) and sched_dir not in rel_s:
                if "JARVIS02_WRITER/scheduler.py" in rel_s and ls.startswith("#"):
                    continue
                report.add(Violation(cat, "schedule/hour-polling", rel_s, i, line))

        # ③ apscheduler import — multiline 검사 (파일 전체)
        if pat3.search(text) and sched_dir not in rel_s:
            for i, line in enumerate(text.splitlines(), 1):
                if line.startswith(("from apscheduler", "import apscheduler")):
                    report.add(Violation(cat, "schedule/apscheduler-import", rel_s, i, line))

    report.checks_run += 7


# ============================================================================
# 검증 5 — 자율 코드 자가수정 (CLAUDE.md "자율 코드 자가수정 규정")
# ============================================================================

def check_autocode(report: Report) -> None:
    """① _BASH_WHITELIST 외부 (agent_tools.py 만 합법)
       ② Path(...).read/write_text 우회 (agent_tools / JARVIS04 외부)
       ③ subprocess.run/Popen/call 외부 (허용 모듈 제외)
       ④ create_plan 우회 (REACT_SYSTEM_PROMPT 에 명시되어야)
    """
    cat = "autocode"
    pat1 = re.compile(r"_BASH_WHITELIST")
    pat2 = re.compile(r"Path\([^)]*\)\.(read_text|read_bytes|write_text|write_bytes)\(")
    pat3 = re.compile(r"subprocess\.(run|Popen|call)")

    # ②③ 허용 위치 (CLAUDE.md 박제 + 현재 정당한 사용처)
    allow2 = (
        "JARVIS01_MASTER/agent_tools.py",
        "JARVIS04_SCHEDULER/",
        "JARVIS02_WRITER/trend_economic_writer.py",  # 생성된 HTML 디스크 저장
        "JARVIS02_WRITER/tistory_html_writer.py",    # 생성된 HTML 재로드 (Pass-2 SVG 보강)
        # ★ 누수 점검 (2026-05-17) — jarvis_main 의 캐시 원고 read (open().read() 자원 누수 수정 결과)
        "JARVIS02_WRITER/jarvis_main.py",
        # ★ scheduler subprocess 결과 파일 + 로그 파일 읽기 (정당한 사용 — subprocess output 처리)
        "JARVIS02_WRITER/scheduler.py",
    )
    allow3 = (
        "JARVIS01_MASTER/agent_tools.py",
        "jarvis_daemon.py",
        "performance_collector",
        "approval_bot",
        "radar_main",
        "post_quality",
        "auto_repair",
        # hub.py 삭제됨 (Streamlit → FastAPI+Next.js 전환)
        "JARVIS00_INFRA/",                    # 인프라 단일 진입점 (데몬·프로세스 제어)
        "JARVIS01_MASTER/dispatchers.py",     # 디스패처 subprocess
        "JARVIS01_MASTER/proactive_monitor.py",
        "JARVIS03_RADAR/jobs.py",
        "shared/llm.py",                      # claude-code-sdk 호출
        "JARVIS02_WRITER/jarvis_main.py",
        # ★ 사용자 박제 2026-05-18 — ADR 008 Phase 2 shim 4종 완전 제거 (_deleted_2026-05-18/ 보관).
        # 옛 shim 호출자는 모두 JARVIS08_PUBLISH.{platforms,credentials} 직접 import 로 교체됨.
        "JARVIS02_WRITER/economic_poster.py",
        "JARVIS02_WRITER/scheduler.py",
        "JARVIS02_WRITER/trend_economic_writer.py",
        # ★ ADR 008 Phase 2 (2026-05-17) — 발행자 본체 이관 새 위치
        "JARVIS08_PUBLISH/platforms/naver_poster.py",      # 네이버 발행 (osascript·Selenium)
        "JARVIS08_PUBLISH/platforms/tistory_poster.py",    # 티스토리 발행 (osascript·Selenium)
        "JARVIS08_PUBLISH/credentials/naver_cookie_refresher.py",   # 쿠키 갱신 (subprocess)
        "JARVIS08_PUBLISH/credentials/tistory_cookie_refresher.py", # 쿠키 갱신 (subprocess)
        "JARVIS06_IMAGE/",                    # 이미지 생성 (Cloudflare Workers AI 단독 2026-08-05)
        "JARVIS07_GUARDIAN/",                 # guardian 자가수정·git audit
        "jarvis_keeper.py",                   # 데몬 워치독 — 재시작 subprocess 정당
        # ★ 무료 데이터 라이브러리 자동설치 화이트리스트 (사용자 박제 2026-06-29 — ADR 010)
        "JARVIS09_COLLECTOR/lib_bootstrap.py",
        "api_server.py",               # FastAPI REST 백엔드 — PID/프로세스 조회 목적
        # ★ 구독 잔여량 조회 (사용자 승인 2026-07-20 — ERRORS [456])
        #   macOS Keychain 에서 본인 OAuth 토큰을 읽어 /api/oauth/usage 조회.
        #   `security` CLI 외 다른 수단이 없어 subprocess 불가피. 토큰은 메모리에서만
        #   다루고 로깅·DB박제·반환값 포함 금지. 실패는 전부 흡수(None → UI 폴백).
        #   킬스위치: TOKEN_QUOTA_LOOKUP=0
        "shared/token_usage.py",
        # ★ 테스트는 프로세스를 띄워야 검증이 된다 (2026-08-05 — 사용자 승인).
        #   이 규칙의 목적은 *자율 에이전트가 임의 셸을 여는 것* 을 막는 것이지
        #   테스트가 격리 저장소에 git 을 돌리는 것을 막는 게 아니다.
        #   실제로 2026-08-05 "훅이 진짜로 막는지" 를 임시 저장소에서 확인하는 테스트가
        #   여기 걸렸다 — 그 테스트가 없어서 훅이 2달간 죽어 있었다.
        #   ※ 운영 코드가 아니므로 발행·외부 영향 경로와 무관하다.
        "tests/",
    )

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat1.search(line) and "JARVIS01_MASTER/agent_tools.py" not in rel_s:
                report.add(Violation(cat, "autocode/whitelist", rel_s, i, line))
            if pat2.search(line) and not any(a in rel_s for a in allow2):
                report.add(Violation(cat, "autocode/path-direct", rel_s, i, line))
            if pat3.search(line) and not any(a in rel_s for a in allow3):
                report.add(Violation(cat, "autocode/subprocess", rel_s, i, line))

    report.checks_run += 3


# ============================================================================
# 검증 6 — 자율 에이전트 도구 (CLAUDE.md "자율 에이전트 도구·승인 게이트")
# ============================================================================

def check_tools(report: Report) -> None:
    """① @register_tool 외부 (shared/tools.py · agent_tools.py 만 합법)
       ② external + requires_approval=False 동시 존재 (3-line window)
       ③ auto_approve=True 운영 잔존 (test_ 제외)
    """
    cat = "tools"
    pat1 = re.compile(r"@register_tool\(")
    pat3 = re.compile(r"auto_approve=True")
    # 합법 위치: shared/tools.py + 마스터 카탈로그 + 각 에이전트 capability 단위 도구 카탈로그
    allow1 = (
        "shared/tools.py",
        "JARVIS01_MASTER/agent_tools.py",
        "JARVIS04_SCHEDULER/scheduler_agent.py",  # 스케줄 capability 도구 카탈로그
    )

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        # tests 디렉토리는 도구 등록 검증 대상 외 (테스트용 등록 정당)
        if "/tests/" in rel_s or rel_s.startswith("tests/"):
            continue
        text = _read_py(p)
        if text is None:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            ls = line.strip()
            # 주석·docstring 안의 매칭은 무시
            if ls.startswith("#") or ls.startswith('"') or ls.startswith("'"):
                continue
            if pat1.search(line) and not any(a in rel_s for a in allow1):
                report.add(Violation(cat, "tools/register-external", rel_s, i, line))
            if pat3.search(line) and "test_" not in p.name:
                # 키워드 리스트 안에 있는 "auto_approve=True" 같은 데이터는 무시
                if '"auto_approve=True"' in line or "'auto_approve=True'" in line:
                    continue
                report.add(Violation(cat, "tools/auto_approve", rel_s, i, line))

        # ② external + requires_approval=False 동시 존재 (3줄 윈도우)
        for i, line in enumerate(lines):
            if 'side_effect="external"' in line:
                window = "\n".join(lines[max(0, i - 1): min(len(lines), i + 4)])
                if "requires_approval=False" in window:
                    report.add(Violation(
                        cat, "tools/external-no-approval", rel_s, i + 1, line
                    ))

    report.checks_run += 3


# ============================================================================
# 검증 7 — 이미지 생성 단일 진입점 (CLAUDE.md "이미지 생성 권한 규정")
# ============================================================================

def check_image(report: Report) -> None:
    """① 이미지 생성 API URL 직접 호출 (JARVIS06_IMAGE 외부)
       ② ImageGenerationModel 직접 사용 (JARVIS06_IMAGE 외부)
    """
    cat = "image"
    pat1 = re.compile(r"https://image\.pollinations\.ai|api\.cloudflare\.com/client/v4/accounts/[^\"']*\/ai/run")
    pat2 = re.compile(r"ImageGenerationModel\(|imagen-[0-9]")
    img_dir = "JARVIS06_IMAGE/"

    # CLAUDE.md 규정 본문(이 파일 포함)은 검증 대상 외
    allow_files = ("shared/precommit_check.py", "CLAUDE.md")

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        if img_dir in rel_s or any(a in rel_s for a in allow_files):
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat1.search(line):
                report.add(Violation(cat, "image/direct-api", rel_s, i, line))
            if pat2.search(line):
                report.add(Violation(cat, "image/imagen-direct", rel_s, i, line))

    report.checks_run += 2


# ============================================================================
# 검증 8 — 도메인 분산 자동 검출 (ADR 008 — 사용자 박제 2026-05-17)
# ============================================================================

# Owner 매트릭스 — 각 도메인의 *물리적 단일 진입점* + 금지 패턴
# Phase 1~6 진행에 따라 추가/조정. *현재* (Phase 0) 는 *이미 통합된* 도메인만 strict 적용.
# 진행 중인 도메인(이미지·발행) 은 Phase 완료 후 활성화 (★ TODO 표시).
_DOMAIN_OWNERSHIP: list[dict] = [
    # ── 이미지 도메인 (Phase 1 완료 2026-05-17 — active=True) ─────────
    {
        "id":          "domain/image",
        "domain":      "image",
        "owner_dirs":  ("JARVIS06_IMAGE/",),
        "active":      True,    # ★ ADR 008 Phase 1 완료 (2026-05-17)
        "patterns": [
            # 함수 정의 (본체) — owner 외 위치 금지
            (re.compile(r"^def\s+(_dedupe_consecutive_images|_dedupe_all_images|_validate_image_files|_is_heading_img_path|assemble_blocks|enforce_image_between_paragraphs|enforce_paragraph_pair_image|compute_unused_image_pool|_is_h2_header)"),
             "이미지 함수 본체 — JARVIS06_IMAGE 외부 정의 금지"),
            # _cleanup_economic_images 본체 (cleaners 도메인)
            (re.compile(r"^def\s+(cleanup_economic_images|_cleanup_economic_images)"),
             "이미지 정리 함수 본체 — JARVIS06_IMAGE/cleaners 외부 정의 금지"),
            # 직접 라이브러리 사용
            (re.compile(r"^from\s+PIL\b|^import\s+PIL\b"),
             "PIL 직접 사용 — JARVIS06_IMAGE 외부 금지"),
            (re.compile(r"^import\s+matplotlib\b|^from\s+matplotlib\b"),
             "matplotlib 직접 사용 — JARVIS06_IMAGE 외부 금지"),
        ],
    },

    # ── 발행 도메인 (Phase 2 완료 2026-05-17 — active=True) ────────
    {
        "id":          "domain/publish",
        "domain":      "publish",
        "owner_dirs":  ("JARVIS08_PUBLISH/",),
        "active":      True,    # ★ ADR 008 Phase 2 완료 (2026-05-17)
        "patterns": [
            # post_to_naver/tistory 본체 (Selenium) — JARVIS08 외부 정의 금지.
            (re.compile(r"^def\s+post_to_(naver|tistory)\b(?!\w)"),
             "발행 함수 본체 — JARVIS08_PUBLISH 외부 정의 금지"),
        ],
    },

    # ── 카테고리 (Phase 2 완료 2026-05-17 — active=True) ──────────
    {
        "id":          "domain/category",
        "domain":      "category",
        "owner_dirs":  ("JARVIS08_PUBLISH/category/",),
        "active":      True,    # ★ ADR 008 Phase 2 완료 (2026-05-17)
        "patterns": [
            (re.compile(r"^(ECONOMIC_CATEGORY|THEME_CATEGORY)\s*="),
             "카테고리 상수 — JARVIS08_PUBLISH/category 단일 진입점 필요"),
        ],
    },

    # ── 분량 도메인 (현재 활성) — length_manager.py 외 본체 정의 금지 ──
    {
        "id":          "domain/length",
        "domain":      "length",
        "owner_dirs":  ("JARVIS02_WRITER/length_manager.py", "shared/seo.py"),
        "active":      True,
        "patterns": [
            (re.compile(r"^def\s+(build_length_phrase|build_prompt_length_block|build_short_length_phrase|count_korean)"),
             "분량 헬퍼 본체 — length_manager 외부 정의 금지"),
            (re.compile(r"^(KOREAN_PER_SENTENCE|TARGET_SENTENCES|MAX_CONSECUTIVE_PARAGRAPHS_WITHOUT_IMAGE)\s*="),
             "분량 상수 본체 — length_manager 외부 정의 금지"),
        ],
    },

    # ── 헌법 집행 (현재 활성) — law_enforcer.py 외 본체 정의 금지 ───
    {
        "id":          "domain/constitution",
        "domain":      "constitution",
        "owner_dirs":  ("JARVIS02_WRITER/law_enforcer.py",),
        "active":      True,
        "patterns": [
            (re.compile(r"^def\s+(enforce_supreme_law|enforce_no_placeholders|fix_human_intro|check_human_intro|notify_violations)"),
             "헌법 집행 함수 본체 — law_enforcer 외부 정의 금지"),
        ],
    },
]


def check_domain_diffusion(report: Report) -> None:
    """도메인 분산 자동 검출 — owner 외 위치에 박힌 본체 검출.

    ADR 008 (Domain Ownership Matrix) 의 *물리적 강제* 메커니즘.
    각 도메인의 *active* 가 True 인 것만 적용 (Phase 진행에 따라 단계적 활성).
    """
    cat = "domain"
    active_owners = [o for o in _DOMAIN_OWNERSHIP if o.get("active", False)]
    if not active_owners:
        return
    # ★ 성능 (2026-07-03): 파일 1회 읽기 + 전체 텍스트 프리필터.
    #   종전 owner×files 재읽기 O(5×N) → O(N). 대부분 파일은 패턴 미포함 →
    #   라인 단위 스캔 자체를 스킵. 데몬 부팅·pre-commit 훅 지연 제거.
    #   ★ 프리필터는 반드시 MULTILINE 재컴파일 사용 — 패턴의 `^` 가 라인 단위
    #   시멘틱(종전 pat.search(line))을 유지해야 함. 전체 텍스트에 원본 패턴을
    #   그대로 쓰면 `^`=파일 첫 바이트가 되어 검출이 무력화됨 (교차 리뷰 발견).
    for owner in active_owners:
        if "_prefilter" not in owner:
            owner["_prefilter"] = [
                re.compile(pat.pattern, pat.flags | re.MULTILINE)
                for pat, _ in owner["patterns"]
            ]
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        text = _read_py(p)
        if text is None:
            continue
        lines = None  # lazy split — 프리필터 통과 시에만
        for owner in active_owners:
            # owner 안이면 검증 제외
            if any(od in rel_s for od in owner["owner_dirs"]):
                continue
            # 전체 텍스트 프리필터 (MULTILINE) — 어떤 패턴도 없으면 라인 스캔 생략
            if not any(mpat.search(text) for mpat in owner["_prefilter"]):
                continue
            if lines is None:
                lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                ls = line.strip()
                if ls.startswith("#"):
                    continue
                for pat, desc in owner["patterns"]:
                    if pat.search(line):
                        report.add(Violation(
                            cat,
                            owner["id"],
                            rel_s,
                            i,
                            f"{desc} — {line.strip()[:80]}",
                        ))
                        break
    report.checks_run += len(active_owners)


# ============================================================================
# 검증 10 — harness 표준 인프라 (ADR 009 v2 — 사용자 박제 2026-05-17)
# ============================================================================

def check_harness(report: Report) -> None:
    """★ 사용자 박제 2026-05-18 — 8건 결함 패치 후 strict 전환.

       ① harness 핵심 심볼 외부 정의 차단 (JARVIS00_INFRA/harness.py 만 합법)
       ② harness.py 파일 존재 + run_action / ActionDefinition / action_step 정의 보장
       ③ harness ImportError fallback 패턴 차단 (P1-③ 결함 회귀 방지)
       ④ 레거시 직접발행 함수 (run_tistory/run_naver) 외부 import 차단 (P0-② 회귀)
       ⑤ 동시성 락 심볼 보존 (P1-⑤ 회귀)
       ⑥ ensure_preflight 진입점 누락 검출 (P1-④ 회귀)
    """
    cat = "harness"
    legit_file = "JARVIS00_INFRA/harness.py"

    # ① harness 핵심 함수·클래스 외부 정의 차단
    pat_def = re.compile(r"^(def|class)\s+(run_action|ActionDefinition|ActionStep|ActionResult|action_step)\b")
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == legit_file or rel_s == "shared/precommit_check.py":
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_def.match(line):
                report.add(Violation(cat, "harness/def-external", rel_s, i, line))
    report.checks_run += 1

    # ② harness.py 존재 + 핵심 심볼 정의 보장
    harness_path = ROOT / legit_file
    if not harness_path.exists():
        report.add(Violation(cat, "harness/file-missing", legit_file, 0, "파일 없음"))
        report.checks_run += 1
        return
    harness_src = harness_path.read_text(encoding="utf-8")
    required_symbols = (
        "def run_action", "class ActionDefinition", "class ActionStep",
        "class ActionResult", "def action_step", "DEFAULT_MAX_ATTEMPTS",
        # P1-⑤ 박제 — 동시성 락
        "_ACTION_LOCKS", "_acquire_action_lock",
    )
    for sym in required_symbols:
        if sym not in harness_src:
            report.add(Violation(cat, "harness/symbol-missing", legit_file, 0,
                                 f"필수 심볼 '{sym}' 정의 없음"))
    report.checks_run += 1

    # ③ ★ P1-③ 회귀 방지 — harness ImportError fallback "직접 실행" 패턴 차단
    pat_legacy_fallback = re.compile(
        r"(run_fn\s*\(\s*\)|_process_one_legacy\s*\(|_run_auto_repair_legacy\s*\(\s*\))"
    )
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == "shared/precommit_check.py":
            continue
        text = _read_py(p)
        if text is None:
            continue
        if "from JARVIS00_INFRA.harness import" not in text:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if pat_legacy_fallback.search(line):
                ctx_start = max(0, i - 12)
                ctx = "\n".join(lines[ctx_start:i])
                if "except ImportError" in ctx and "★" not in line and "사용자 박제" not in line:
                    stripped = line.lstrip()
                    indent = len(line) - len(stripped)
                    # 정의·callable 변수 자체는 OK — 실행 호출 () 형태만 차단
                    if indent >= 4 and not stripped.startswith("#") and not stripped.startswith("def "):
                        report.add(Violation(
                            cat, "harness/import-fallback-bypass", rel_s, i,
                            f"ImportError 시 직접 실행 (검증 우회): {stripped[:80]}",
                        ))
    report.checks_run += 1

    # ④ ★ P0-② 회귀 방지 — 레거시 직접발행 함수 외부 import 차단
    pat_legacy_pub = re.compile(
        r"from\s+JARVIS02_WRITER\.trend_economic_writer\s+import\s+.*\b(run_naver|run_tistory)\b"
    )
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s in ("shared/precommit_check.py",
                     "JARVIS02_WRITER/trend_economic_writer.py"):
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_legacy_pub.search(line):
                report.add(Violation(
                    cat, "harness/legacy-publish-import", rel_s, i,
                    f"레거시 직접발행 함수 import (harness 우회): {line.strip()[:80]}",
                ))
    report.checks_run += 1

    # ⑤ ★ P1-④ 회귀 방지 — preflight.ensure_preflight 정의 보장
    preflight_path = ROOT / "JARVIS00_INFRA" / "preflight.py"
    if preflight_path.exists():
        pf_src = preflight_path.read_text(encoding="utf-8")
        if "def ensure_preflight" not in pf_src:
            report.add(Violation(
                cat, "harness/ensure-preflight-missing", "JARVIS00_INFRA/preflight.py", 0,
                "ensure_preflight() 정의 없음 — subprocess 자식 Layer 0 우회",
            ))
        if "JARVIS_PREFLIGHT_DONE" not in pf_src:
            report.add(Violation(
                cat, "harness/preflight-marker-missing", "JARVIS00_INFRA/preflight.py", 0,
                "JARVIS_PREFLIGHT_DONE 환경변수 박제 없음 — 자식 우회 차단 못 함",
            ))
    report.checks_run += 1

    # ⑥ ★ harness state 에 *살아있는 핸들* 금지 (ERRORS [544])
    #
    #   왜: state 는 step 사이를 흐르는 dict 이고 액션이 끝나면 그냥 버려진다.
    #     ① 살아있는 객체가 들어가면 직렬화가 통째로 불가능해지고
    #        (실측: Selenium WebDriver → msgpack `TypeError`)
    #     ② close 를 불러줄 주인이 없어 샌다 — 실제로 경제 브리핑이 티스토리 driver 를
    #        성공할 때마다 남기고 있었다(소비처 0 · quit 은 실패 분기에만).
    #   → 핸들은 `JARVIS00_INFRA/resources.py` 에 두고 state 엔 **키 문자열만**.
    #   금지 접미사 목록의 주인은 `resources.LIVE_HANDLE_SUFFIXES` 한 곳 (원칙②).
    _suffixes: tuple = ()
    try:
        _rsrc = (ROOT / "JARVIS00_INFRA" / "resources.py").read_text(encoding="utf-8")
        _m = re.search(r"LIVE_HANDLE_SUFFIXES\s*=\s*\(([^)]*)\)", _rsrc)
        if _m:
            _suffixes = tuple(s.strip().strip("\"'") for s in _m.group(1).split(",") if s.strip())
    except Exception:
        pass
    if not _suffixes:
        # fail-closed — 목록을 못 읽으면 검사가 *조용히 무력화* 된다 (collect/cache 와 같은 규약)
        report.add(Violation(
            cat, "harness/resource-selfcheck", "JARVIS00_INFRA/resources.py", 0,
            "LIVE_HANDLE_SUFFIXES 를 읽지 못해 state 핸들 검사가 무력화됨 — 검사를 고칠 것"))
    else:
        # step 반환 dict 리터럴에 금지 접미사 키가 있으면 위반 (`..._key` 는 정상)
        pat_state = re.compile(
            r"""["'](\w*(?:%s))["']\s*:""" % "|".join(re.escape(s) for s in _suffixes))
        for p in _iter_py():
            rel_s = str(p.relative_to(ROOT))
            if rel_s in ("shared/precommit_check.py", "JARVIS00_INFRA/resources.py"):
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            for i, ln in enumerate(lines, 1):
                if "return {" not in ln and "state[" not in ln:
                    continue
                m = pat_state.search(ln)
                if m and not m.group(1).endswith("_key"):
                    report.add(Violation(
                        cat, "harness/live-handle-in-state", rel_s, i,
                        f"state 에 살아있는 핸들 추정 키 '{m.group(1)}' — 직렬화 불가 + 정리 미아. "
                        f"`JARVIS00_INFRA.resources.put()` 로 넣고 state 엔 '<이름>_key' 만 둘 것"))
    report.checks_run += 1


# ============================================================================
# 검증 9 — Layer 0 preflight (ADR 009 — 사용자 박제 2026-05-17)
# ============================================================================

def check_preflight(report: Report) -> None:
    """① preflight 외부 정의 차단 (JARVIS00_INFRA/preflight.py 만 합법)
       ② jarvis_daemon.main() 초입에서 run_preflight() 호출 보장
       ③ run_preflight 호출이 _acquire_lock 보다 *먼저* 와야 함 (다른 코드 도달 전 차단)
    """
    cat = "preflight"
    # 합법 위치
    legit_file = "JARVIS00_INFRA/preflight.py"
    legit_caller = "jarvis_daemon.py"

    # ① preflight 본체 외부 정의 (def run_preflight)
    pat_def = re.compile(r"^def\s+run_preflight\b")
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == legit_file or rel_s == "shared/precommit_check.py":
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_def.match(line):
                report.add(Violation(cat, "preflight/def-external", rel_s, i, line))
    report.checks_run += 1

    # ② jarvis_daemon.py main() 안에서 run_preflight() 호출
    daemon_path = ROOT / legit_caller
    if not daemon_path.exists():
        report.add(Violation(cat, "preflight/daemon-missing", legit_caller, 0, "파일 없음"))
        report.checks_run += 1
        return
    daemon_src = daemon_path.read_text(encoding="utf-8")
    if "run_preflight()" not in daemon_src:
        report.add(Violation(cat, "preflight/daemon-no-call", legit_caller, 0,
                             "main() 안 run_preflight() 호출 없음"))
    report.checks_run += 1

    # ③ run_preflight 호출이 _acquire_lock 보다 *먼저* 와야 함
    lines = daemon_src.splitlines()
    main_start = None
    preflight_line = None
    lock_line = None
    for i, line in enumerate(lines, 1):
        if re.match(r"^def\s+main\s*\(", line):
            main_start = i
            continue
        if main_start is None:
            continue
        if "run_preflight()" in line and preflight_line is None:
            preflight_line = i
        if "_acquire_lock()" in line and lock_line is None:
            lock_line = i
    if main_start and (preflight_line is None or (lock_line and preflight_line > lock_line)):
        report.add(Violation(cat, "preflight/call-order", legit_caller, lock_line or 0,
                             f"run_preflight() 가 _acquire_lock() 보다 *뒤*에 위치 (preflight={preflight_line}, lock={lock_line})"))
    report.checks_run += 1


# ============================================================================
# 카탈로그 + main
# ============================================================================

CATEGORIES: dict[str, Callable[[Report], None]] = {
    "infra": check_infra,
    "length": check_length,
    "blog": check_blog,
    "schedule": check_schedule,
    "autocode": check_autocode,
    "tools": check_tools,
    "image": check_image,
    "domain": check_domain_diffusion,   # ★ ADR 008 (2026-05-17)
    "preflight": check_preflight,        # ★ ADR 009 Layer 0 (2026-05-17)
    "harness": check_harness,            # ★ ADR 009 v2 Layer 1~4 (2026-05-17)
    "auth": None,  # ★ LOGIN_SUPREME_LAW (2026-05-17) — 아래에서 함수 박혀있음
}

# ============================================================================
# 검증 11 — 로그인·인증 단일 진입점 (LOGIN_SUPREME_LAW.md — 사용자 박제 2026-05-17)
# ============================================================================

def check_auth(report: Report) -> None:
    """① login_manager.py 외부에서 NV/TS 환경변수 직접 참조 검출
       ② 로그인 함수 본체 외부 정의 차단 (_auth_headers·_auth_token·refresh_*_cookies)
       ③ 쿠키 파일 경로 하드코딩 검출
    """
    cat = "auth"
    # 합법 위치 — 로그인 코드 본체
    legit = (
        "JARVIS08_PUBLISH/credentials/login_manager.py",
        "JARVIS08_PUBLISH/credentials/naver_cookie_refresher.py",
        "JARVIS08_PUBLISH/credentials/tistory_cookie_refresher.py",
        "JARVIS08_PUBLISH/credentials/__init__.py",
        "JARVIS08_PUBLISH/credentials/LOGIN_SUPREME_LAW.md",
        "JARVIS08_PUBLISH/platforms/naver_poster.py",   # selenium 로그인 본체
        "JARVIS08_PUBLISH/platforms/tistory_poster.py", # selenium 로그인 본체
        "JARVIS00_INFRA/preflight.py",                   # env 검증
        "shared/precommit_check.py",
        "JARVIS02_WRITER/scheduler.py",                  # _harness_precondition_check
    )

    # ① 환경변수 직접 참조 (외부)
    pat_env = re.compile(r'os\.(?:environ|getenv)\(?[\[\.]?\s*[\'"](?:NV_USERNAME|NV_PASSWORD|TS_COOKIE|TS_USERNAME|TS_PASSWORD)[\'"]')
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if any(rel_s.endswith(l.split("/")[-1]) and l in rel_s for l in legit):
            continue
        if any(l == rel_s for l in legit):
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            ls = line.strip()
            if ls.startswith("#") or ls.startswith('"""') or ls.startswith("'''"):
                continue
            if pat_env.search(line):
                report.add(Violation(cat, "auth/env-direct", rel_s, i, line))
    report.checks_run += 1

    # ② 로그인 함수 본체 외부 정의 (def _auth_headers / refresh_naver_cookies / refresh_tistory_cookies)
    pat_def = re.compile(r'^def\s+(_auth_headers|_auth_token|refresh_naver_cookies|refresh_tistory_cookies|check_cookie_valid|get_naver_cookies|get_tistory_cookie)\b')
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if any(l == rel_s for l in legit):
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_def.match(line):
                report.add(Violation(cat, "auth/def-external", rel_s, i, line))
    report.checks_run += 1

    # ③ login_manager.py 존재 보장
    lm_path = ROOT / "JARVIS08_PUBLISH/credentials/login_manager.py"
    if not lm_path.exists():
        report.add(Violation(cat, "auth/login_manager-missing",
                             "JARVIS08_PUBLISH/credentials/login_manager.py", 0,
                             "login_manager.py 없음"))
    else:
        src = lm_path.read_text(encoding="utf-8")
        for sym in ("def get_naver_cookies",
                    "def get_tistory_cookie", "def verify_all_logins"):
            if sym not in src:
                report.add(Violation(cat, "auth/symbol-missing",
                                     "JARVIS08_PUBLISH/credentials/login_manager.py", 0,
                                     f"필수 심볼 '{sym}' 없음"))
    report.checks_run += 1


# auth 카테고리 등록
CATEGORIES["auth"] = check_auth


def check_verification(report: Report) -> None:
    """범용 작업 검증 레지스트리 무결성 (사용자 박제 2026-07-02).

    "모든 에이전트가 작업 완료 시 작업 종류에 맞는 검증을 통과해야만 통과" 원칙의
    단일 진입점 JARVIS00_INFRA/verification.py 가 필수 API 를 보유하는지 보장.
    ① 파일 존재 ② 필수 심볼(register_check/verify_output/CheckResult) 정의
    ③ register_check 데코레이터 외부 재정의 금지(단일 진입점).
    """
    cat = "verification"
    vpath = ROOT / "JARVIS00_INFRA/verification.py"
    if not vpath.exists():
        report.add(Violation(cat, "verification/missing",
                             "JARVIS00_INFRA/verification.py", 0,
                             "verification.py 없음 — 범용 검증 레지스트리 단일 진입점"))
        report.checks_run += 1
        return
    src = vpath.read_text(encoding="utf-8")
    for sym in ("def register_check", "def verify_output", "class CheckResult",
                "def has_blocking", "def is_valid_image_file"):
        if sym not in src:
            report.add(Violation(cat, "verification/symbol-missing",
                                 "JARVIS00_INFRA/verification.py", 0,
                                 f"필수 심볼 '{sym}' 없음"))
    report.checks_run += 1

    # register_check 는 verification.py 만 정의 (외부 재정의 = 레지스트리 분산)
    pat_def = re.compile(r"^def register_check\b")
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == "JARVIS00_INFRA/verification.py":
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_def.match(line):
                report.add(Violation(cat, "verification/def-external", rel_s, i, line))
    report.checks_run += 1


CATEGORIES["verification"] = check_verification


# ── 모델 위생 (검사기가 모델명을 '박지 않는다') ──────────────────────
# 버전 세그먼트는 숫자로 시작 — 문장 끝 마침표("...4-6.") 오탐 방지
_PAT_MODEL_ID = re.compile(r"claude-([a-z]+)-([0-9][0-9a-z\-]*)")
# 버전이 생략·말줄임된 형태 — `claude-<family>-...` 처럼. 뒤가 숫자/점일 때만 family 로 인정해
# `claude-code-sdk` 같은 *모델 아닌* 이름을 오탐하지 않는다.
_PAT_MODEL_ID_LOOSE = re.compile(r"claude-([a-z]+)-(?=[0-9.])")
# 사람이 읽는 라벨 + 접두 없는 축약형 — "<Family> 5" / "<family>-4-5" / "<family> 4.6"
# 대소문자 무시·구분자(공백/하이픈) 허용·버전 구분자(점/하이픈) 허용:
# `claude-` 접두가 떨어져 나간 흔적(예: `writer_fast=<family>-4-6`)까지 잡기 위함.
_PAT_MODEL_LABEL = re.compile(
    r"\b([A-Za-z]{3,9})[\s\-]([0-9](?:[.\-][0-9]){0,3})\b")
# family 단독 등장 — "<Family> 전체 파일 분석" 처럼 버전 없이 이름만 남은 흔적
_PAT_MODEL_FAMILY = re.compile(r"\b([A-Za-z]{4,9})\b")
# 모델 ID 리터럴의 유일한 소유자 + 이 검사기 자신(패턴 보유)
_MODEL_ID_OWNERS = ("shared/llm.py", "shared/precommit_check.py")


def _live_model_ids() -> set[str]:
    """살아있는 모델 ID 를 shared/llm.py 원문에서 *매번* 파싱.

    ② 동적 설계 — 검사기에 유효 ID 를 박아두면 그 목록이 또 하나의 사본이 되어
    모델 교체 시 검사기만 옛 값을 가리킨다. import 대신 원문 파싱인 이유:
    precommit 은 의존성 없이 단독 실행돼야 하고, llm.py import 는 무겁다.
    """
    try:
        src = (ROOT / "shared" / "llm.py").read_text(encoding="utf-8")
    except Exception:
        return set()
    return set(re.findall(r'model_id\s*=\s*"([^"]+)"', src))


def _label_of(model_id: str) -> tuple[str, str]:
    """모델 ID → (family 소문자, 버전 점표기). 'claude-sonnet-5' → ('sonnet','5')"""
    m = _PAT_MODEL_ID.search(model_id)
    if not m:
        return ("", "")
    ver = ".".join(x for x in m.group(2).split("-") if x.isdigit() and len(x) <= 2)
    return (m.group(1), ver)


def check_model(report: Report) -> None:
    """모델 위생 — 폐기 모델 흔적 0 + 모델 ID 단일 소유 (사용자 박제 2026-07-24).

    두 가지를 동시에 막는다. 둘 다 실제로 사고를 냈다(ERRORS [491]).

    ① `model/hardcoded-id` — 모델 ID 리터럴은 `shared/llm.py` 의 MODELS 만 소유.
       다른 파일이 ID 를 박으면 모델을 갈아끼울 때 그 사본만 옛 모델에 남는다.
       파생 방법: `from shared.llm import model_id` → `model_id("guardian")`.
    ② `model/dead-name` — 지금 살아있지 않은 모델 ID·라벨은 코드·문서 어디에도
       남기지 않는다. 주석·docstring 의 옛 이름은 사람이 grep 에서 보고 오해한다.

    유효 목록을 검사기에 박지 않는다 — `shared/llm.py` 를 매 실행 파싱해 파생하고,
    못 읽으면 통과가 아니라 `model/self-check` 위반(fail-closed).
    폐기 family 어휘도 저장소에서 실제 발견된 ID 로부터 파생 — 이름 목록 박제 없음.

    ★ 흔적은 온전한 ID 형태로만 남지 않는다 (2026-07-24 보강). 실제로 세 변형이
    1차 검사를 빠져나갔다 — `claude-<family>-...`(버전 생략) · `<family>-4-6`(접두 탈락) ·
    `<Family>`(이름만). 그래서 family 어휘를 알면 *버전 없이 이름만* 있어도 잡는다.
    한계는 정직하게 적는다: family 어휘는 저장소에 남은 ID 에서 파생하므로, 저장소가
    완전히 깨끗해진 뒤 새로 유입되는 *처음 보는* 폐기 family 는 ID 형태로 들어와야
    걸린다. 그 경우도 ID 레그가 잡으므로 실질 구멍은 없다.
    """
    cat = "model"
    live_ids = _live_model_ids()
    if not live_ids:
        report.add(Violation(cat, "model/self-check", "shared/llm.py", 0,
                             "MODELS 에서 유효 모델 ID 를 파싱하지 못함 — 검사 불능(fail-closed)"))
        report.checks_run += 1
        return
    live_labels = {_label_of(m) for m in live_ids}
    live_families = {f for f, _ in live_labels}

    # 검사 대상 = 코드(.py) + 문서(.md) — 흔적은 주석·문서에 더 오래 남는다
    targets: list[Path] = list(_iter_py())
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        for fn in filenames:
            if fn.endswith(".md"):
                p = Path(dirpath) / fn
                if not _is_excluded(p):
                    targets.append(p)

    # 폐기 family 어휘: 저장소에 실제로 등장한 ID 들에서 파생 (목록 하드코딩 회피)
    seen_families: set[str] = set()
    file_lines: list[tuple[str, list[str]]] = []
    for p in targets:
        text = _read_py(p)
        if text is None:
            continue
        lines = text.splitlines()
        file_lines.append((str(p.relative_to(ROOT)), lines))
        for f, _v in _PAT_MODEL_ID.findall(text):
            seen_families.add(f)
        seen_families.update(_PAT_MODEL_ID_LOOSE.findall(text))
    known_families = seen_families | live_families

    for rel_s, lines in file_lines:
        is_owner = any(rel_s == o or rel_s.endswith("/" + o) for o in _MODEL_ID_OWNERS)
        for i, line in enumerate(lines, 1):
            for f, v in _PAT_MODEL_ID.findall(line):
                mid = f"claude-{f}-{v}"
                if mid not in live_ids:
                    report.add(Violation(cat, "model/dead-name", rel_s, i, line))
                elif not is_owner and rel_s.endswith(".py"):
                    report.add(Violation(cat, "model/hardcoded-id", rel_s, i, line))
            if is_owner:
                continue
            flagged = False
            for word, ver in _PAT_MODEL_LABEL.findall(line):
                fam = word.lower()
                ver_dot = ver.replace("-", ".")
                if fam in known_families and (fam, ver_dot) not in live_labels:
                    report.add(Violation(cat, "model/dead-name", rel_s, i, line))
                    flagged = True
                    break
            if flagged:
                continue
            # 버전 없이 이름만 남은 흔적 — 폐기 family 는 단독 등장도 흔적이다
            for word in _PAT_MODEL_FAMILY.findall(line):
                fam = word.lower()
                if fam in known_families and fam not in live_families:
                    report.add(Violation(cat, "model/dead-name", rel_s, i, line))
                    break
    report.checks_run += 3


# model 카테고리 등록
CATEGORIES["model"] = check_model


def check_ssot(report: Report) -> None:
    """표시 계층 SSOT — 웹 대시보드가 모델명을 *하드코딩* 하지 못하게 강제.

    사용자 박제 2026-07-04: "코드만 바꾸면 웹·텔레그램이 자동으로 따라와야 한다."
    hub.py 는 모델명을 '<Family> 4.8' 처럼 직접 쓰지 말고 shared.llm.model_label()
    로 파생해야 한다 → 코드(shared/llm.py MODELS)가 모델을 바꾸면 대시보드가
    자동 갱신, 2중·3중 수정 제거. 하드코딩 리터럴 발견 시 커밋·부팅 단계에서 차단.

    표시 파일 추가 시 display_files 에 등록. (텔레그램 표시는 architecture.py
    telegram_summary 등이 이미 model_label 로 파생 — 함수 파생이라 리터럴 없음.)
    """
    cat = "ssot"
    # 모델 family 이름을 검사기에 박지 않는다 — 살아있는 ID 에서 파생 (② 동적 설계)
    _fams = sorted({f for f, _ in (_label_of(m) for m in _live_model_ids()) if f})
    pat_label = re.compile(
        r"\b(?:" + "|".join(f.capitalize() for f in _fams) + r")\s+[0-9]"
    ) if _fams else re.compile(r"(?!x)x")                                    # 사람이 읽는 모델 라벨
    pat_sched = re.compile(r"(?:매일|매주|매월)[가-힣\s·]*[0-9]{1,2}:[0-9]{2}")  # 스케줄 구절(매일 06:30 등)
    display_files = ()   # hub.py 삭제됨 — Next.js 프론트엔드는 Python SSOT 검사 대상 아님
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if not any(rel_s == f or rel_s.endswith("/" + f) for f in display_files):
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat_label.search(line):
                report.add(Violation(cat, "ssot/model-label", rel_s, i, line))
            if pat_sched.search(line):
                report.add(Violation(cat, "ssot/schedule", rel_s, i, line))
    report.checks_run += 2


CATEGORIES["ssot"] = check_ssot


def check_copytruth(report: Report) -> None:
    """★ '복사본을 진실로 믿는' 패턴 자동 검출 (사용자 박제 2026-07-20).

    사용자 요구: "동적 설계를 매번 말하지 않아도 자동으로 인식되게 하라."

    ssot 카테고리가 *모델명 표시* 에 한정된 것을 일반화한다. 2026-07-20 하루에
    같은 병이 5번 나왔다 — 전부 *진실을 한 곳에서 읽지 않고 어딘가에 복사해두고
    그 복사본을 믿은* 사고:
      · 제안 엔진이 "재시도 3회"·"잡 42개" 를 문자열로 박음 → 노브 변경 미반영
      · 대시보드가 five_hour/seven_day 키를 박음 → 버킷 추가 시 미표시
      · 문서가 hub.py 를 현행이라 기술 → 코드에선 이미 삭제 (ERRORS [456])
      · 패치를 .venv 안에 복사 → venv 재생성에 소멸 (ERRORS [455])
      · `_PATCH_INSTALLED=True` 플래그를 효과의 증거로 사용 → 무력해도 True (ERRORS [457])

    검출 3종 (오탐을 피하려 *지시·선언* 형태만 잡는다):
      ① venv/site-packages 내부 파일을 고치라는 *지시* 가 문서·주석에 있음
      ② monkey-patch(모듈 속성 대입)를 하면서 효과 검증 함수가 저장소에 없음
      ③ 설치/적용 플래그를 정의하면서 같은 파일에 효과 검증이 없음
    """
    cat = "copytruth"

    # ① venv 내부 수정 지시 — ERRORS [455] 재발 방지
    pat_venv_edit = re.compile(
        r"\.venv/[^\s]*\.py[^\n]*(?:수정|고치|패치|변경|바꾸)|"
        r"(?:수정|고치|패치|변경|바꾸)[^\n]*\.venv/[^\s]*\.py"
    )
    for p in list(_iter_py()) + list(ROOT.glob("**/*.md")):
        rel_s = str(p.relative_to(ROOT))
        if any(seg in rel_s for seg in (".venv/", "__pycache__", "node_modules",
                                        ".claude/", "JARVIS07_GUARDIAN/ERRORS.md")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            if not pat_venv_edit.search(line):
                continue
            # ★ 오탐 차단: *지시* 만 잡고 *금지 규정·역사 서술* 은 통과시킨다.
            #   금지어가 같은 줄에 없어도 주변 문맥(±3줄)에 있으면 정당한 서술.
            ctx = "\n".join(lines[max(0, i - 4): i + 3])
            if any(w in ctx for w in ("금지", "말 것", "하지 마", "종전", "폐기", "대신")):
                continue
            report.add(Violation(cat, "copytruth/venv-edit", rel_s, i, line.strip()[:160]))

    # ②③ monkey-patch·설치플래그가 있는 파일에 효과 검증이 있는가
    pat_patch_assign = re.compile(
        r"^\s*[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\s*=\s*_?[A-Za-z_][A-Za-z0-9_]*patch",
        re.MULTILINE)
    # ★ pytest 의 `monkeypatch` 픽스처는 제외 (2026-08-07).
    #   이 규칙이 겨냥하는 것은 *운영 코드* 의 몽키패치다 — "설치했다고 적어놓고 실제로는
    #   안 먹은" 사고(ERRORS [457]). pytest 픽스처는 성질이 다르다:
    #     · 테스트 종료 시 **자동 원복** 되므로 잔존 상태가 없다
    #     · 패치가 안 먹으면 그 테스트가 곧바로 실패한다 — **테스트 자체가 효과 검증**이다
    #   즉 여기에 `patch_effective()` 를 요구하는 것은 "검증을 검증하라" 는 순환이다.
    #   실제로 대역을 쓰는 정상 테스트가 이 규칙에 걸려 있었다(tests/test_publish_dedupe.py).
    #   ※ 좁게 막는다 — 픽스처 호출 형태만 면제하고, 맨 `setattr(...)` 은 테스트에서도 잡는다.
    pat_setattr = re.compile(
        r"(?<!monkeypatch\.)setattr\(\s*_?[A-Za-z_][A-Za-z0-9_]*\s*,\s*[\"'][A-Za-z_]")
    pat_flag = re.compile(r"^\s*_?[A-Z][A-Z0-9_]*(?:INSTALLED|PATCHED|APPLIED|DONE)\s*=\s*(?:True|False)",
                          re.MULTILINE)
    # 효과 검증 신호 — 이 중 하나라도 있으면 통과
    pat_verify = re.compile(r"def\s+\w*(?:effective|verify|smoke|selfcheck|self_check)\w*\s*\(|"
                            r"_effective\(|patch_effective")

    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == "shared/precommit_check.py":
            continue          # 이 검사기 자신의 정규식 리터럴 제외
        text = _read_py(p)
        if text is None:
            continue
        has_patch = bool(pat_patch_assign.search(text) or pat_setattr.search(text))
        has_flag  = bool(pat_flag.search(text))
        if not (has_patch or has_flag):
            continue
        if pat_verify.search(text):
            continue          # 효과 검증 존재 → 정당
        kind = "copytruth/patch-unverified" if has_patch else "copytruth/flag-unverified"
        first = next((i for i, l in enumerate(text.splitlines(), 1)
                      if (pat_patch_assign.match(l) or pat_setattr.search(l)
                          or pat_flag.match(l))), 1)
        report.add(Violation(
            cat, kind, rel_s, first,
            "패치·설치플래그가 있으나 효과 검증(patch_effective/verify/smoke)이 없음 "
            "— 플래그는 '시도' 지 '적용' 의 증거가 아님 (ERRORS [457])"))

    report.checks_run += 3


CATEGORIES["copytruth"] = check_copytruth

def check_visual_dup(report: Report) -> None:
    """★ 시각 중복 판정이 *산문* 을 잡지 못하게 강제 (ERRORS [461], 2026-07-21).

    사고: `_title in html_so_far` 로 본문 전체를 부분문자열 검색해, 데이터셋 제목이
      산문에 언급되기만 해도 차트를 스킵했다 → 데이터를 성실히 설명한 글일수록
      이미지 0개 → 헌법 제4조 위반. 티스토리 경제·테마 양쪽에서 재현.
    규칙: 중복 판정은 `already_visualized()` 단일 진입점만 사용한다.
      본문 HTML 전체를 대상으로 한 raw `in html` 제목 검색은 금지.
    """
    cat = "visualdup"
    pat = re.compile(r"_title\s+in\s+html_so_far|title\s+in\s+html_so_far")
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == "shared/precommit_check.py":
            continue
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                report.add(Violation(
                    cat, "visualdup/prose-match", rel_s, i,
                    "산문 포함 본문 전체를 제목 부분문자열로 검색 — already_visualized() 사용 필수 (ERRORS [461])"))
    report.checks_run += 1


CATEGORIES["visualdup"] = check_visual_dup


# ── 수집 단일 진입점 강제 (★ 사용자 박제 2026-07-23) ────────────────────────────
#   "자비스09가 데이터 수집 에이전트다. 모든 수집은 09 단일 진입점이다.
#    수집을 엉뚱한 놈이 하는 막되먹은 수정이 절대 안 되도록 강제하라."
#
#   왜 grep 하나로 안 되나: 지금까지도 02 는 09 의 *API 를 호출* 했다. 규정을 어긴 건
#   호출이 아니라 **조합** 이었다 — 무엇을 먼저 부르고, 실패하면 무엇으로 대체하고,
#   결과를 어떤 상자로 조립할지를 02 가 정했다. 그래서 "requests.get 금지" 류 검사는
#   전부 통과하는데도 수집 오케스트레이션이 5벌 흩어져 있었다.
#   → 이 검사는 *조합* 을 잡는다: 09 의 수집 API 를 2종 이상 쓰거나, 수집 산출물 조립
#     함수를 09 밖에서 부르면 위반.
#
#   ② 동적 설계: 금지 대상 API 목록을 여기에 박지 않는다. `JARVIS09_COLLECTOR.__all__`
#     을 런타임에 읽어 파생 — 09 에 새 수집 API 가 생기면 자동으로 이 검사에 편입된다.

# 조합을 대신해 주는 *정문* — 이것만은 몇 개를 쓰든 정상 (09 가 조합한 결과를 받는 것)
_COLLECT_FACADES = {"collect_all", "market_snapshot", "CollectedData", "CATEGORY_POLICY",
                    "policy_for", "grounds", "ATTR_UNITS", "evidence_brief", "as_source_docs",
                    "check_source_onboarding", "register_source_key", "onboarding_status",
                    # ★ 선계산이 남긴 *마커 조회* — 밖에 나가 받아오는 게 없으니 수집이 아니다.
                    #   (02 가 21:00 발행에서 "어떤 테마가 고정됐나" 를 묻는 한 줄)
                    "load_pinned_theme"}
# 수집 산출물 *조립* 함수 — 09 밖 호출 자체가 위반 (조립 규칙은 09 소유)
_COLLECT_ASSEMBLERS = {"compose_collected", "market_data_to_datasets",
                       "facts_to_datasets", "stocks_to_datasets", "select_by_trust_quota"}
# 수집 도메인 owner + 정당한 예외
_COLLECT_OWNER = "JARVIS09_COLLECTOR/"
_COLLECT_EXEMPT_DIRS = (
    "JARVIS03_RADAR/",   # 주제·트렌드 owner — 선수집 요청은 ADR 013 정본 경로
    "tools/",            # 계측 스크립트 (발행 경로 아님)
)
# 원시 수집 라이브러리 — 09 밖에서 *데이터를 받아오면* 위반 (pytrends 는 03 트렌드 예외)
_COLLECT_RAW_LIB_NAMES = ("yfinance", "pykrx", "FinanceDataReader", "pytrends", "feedparser")
_COLLECT_RAW_FROM = re.compile(
    rf"^\s*from\s+({'|'.join(_COLLECT_RAW_LIB_NAMES)})\b.*\bimport\b")
_COLLECT_RAW_IMPORT = re.compile(
    rf"^\s*import\s+({'|'.join(_COLLECT_RAW_LIB_NAMES)})\b(?:\s+as\s+(\w+))?")
# 패키지 *메타* 만 쓰는 건 수집이 아니다 (예: 번들 폰트 경로 탐색 `pykrx.__file__`)
_COLLECT_LIB_META_ATTRS = {"__file__", "__path__", "__version__", "__name__", "__doc__"}

# ── ④⑤ 정문 우회 — 09 의 *내부* 를 밖에서 붙잡는 형태 (사용자 박제 2026-07-23) ──
#   ①②③ 은 "몇 종을 조합했나" 를 본다. 그런데 09 API 를 *한 종만* 쓰면서도 경계가 새는
#   길이 두 개 남아 있었다 (실제로 4곳이 이 길로 새 있었고 ①②③ 은 전부 통과했다):
#     ④ private 심볼 직수입 — `_fetch_naver_theme_catalog` 처럼 `_` 로 시작하는 내부 함수.
#        밖이 붙잡는 순간 09 는 자기 내부를 못 고친다 (이름 하나가 곧 공개 계약이 된다).
#     ⑤ 내부 계층(하위 패키지) 직수입 — `JARVIS09_COLLECTOR.providers.*`.
#        provider 선택은 09 가 하는 판단이다. 밖에서 특정 provider 를 지목하면 폴백이 죽는다.
#   ② 동적 설계: '내부 계층' 목록도 박지 않는다 — 09 폴더의 하위 *패키지* 를 실물로 훑어 파생.
_COLLECT_FROM_09 = re.compile(r"^\s*from\s+JARVIS09_COLLECTOR(?:\.([\w.]+))?\s+import\s+(.+)$")
_COLLECT_IMPORT_09 = re.compile(r"^\s*import\s+JARVIS09_COLLECTOR\.([\w.]+)")


def _collect_internal_packages() -> set[str] | None:
    """09 의 내부 계층(하위 패키지) 이름 — 파일시스템에서 파생. 09 가 계층을 늘리면 자동 편입.

    읽기 실패는 `None` (= 검사 무력화, fail-closed). 빈 집합은 '하위 패키지가 없다' 는 사실.
    """
    root = ROOT / "JARVIS09_COLLECTOR"
    try:
        return {d.name for d in root.iterdir()
                if d.is_dir() and not d.name.startswith((".", "__")) and (d / "__init__.py").exists()}
    except Exception:
        return None


def _collect_imported_names(names_s: str, lineno: int, lines: list[str]) -> list[str]:
    """`from ... import` 의 *원본* 이름들 (alias 는 무시 — `as _x` 는 밖의 사정)."""
    if "(" in names_s and ")" not in names_s:          # 괄호 여러 줄
        buf, j = [names_s], lineno
        while j < len(lines) and ")" not in lines[j]:
            buf.append(lines[j]); j += 1
        if j < len(lines):
            buf.append(lines[j])
        names_s = " ".join(buf)
    raw = names_s.replace("(", " ").replace(")", " ").replace("\\", " ")
    out = []
    for tok in raw.split(","):
        nm = tok.split("#")[0].split(" as ")[0].strip()
        if nm:
            out.append(nm)
    return out


def _collect_api_names() -> set[str]:
    """09 가 공개한 수집 API 이름 — 런타임 파생 (하드코딩 금지, ② 동적 설계).

    ★ 무거운 import 없이 `__init__.py` 의 `__all__` 을 *소스에서* 읽는다.
      importlib 로 실제 로드하면 `python3 shared/precommit_check.py` 처럼
      sys.path[0]=shared/ 인 실행에서 조용히 실패해 **검사가 통째로 무력화**된다
      (실제로 그렇게 통과했다 — '검사 존재'는 '적용'의 증거가 아니다).
    빈 집합이면 호출자가 검사 무력화로 간주하고 위반을 낸다 (fail-closed).
    """
    src = ROOT / "JARVIS09_COLLECTOR" / "__init__.py"
    try:
        text = src.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    m = re.search(r"__all__\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        return set()
    names = set(re.findall(r"[\"'](\w+)[\"']", m.group(1)))
    return {n for n in names if n not in _COLLECT_FACADES}


def check_collect(report: Report) -> None:
    """★ 수집 단일 진입점 = JARVIS09 (사용자 박제 2026-07-23).

    ① 09 밖에서 수집 API 2종 이상 사용 → *조합* = 수집 오케스트레이션 (위반)
    ② 09 밖에서 수집 산출물 조립 함수 호출 → 조립 규칙 유출 (위반)
    ③ 09 밖에서 원시 수집 라이브러리 import → 수집 신설 (위반)
    ④ 09 의 private(`_`) 심볼 직수입 → 내부 구현을 밖이 붙잡음 (위반)
    ⑤ 09 의 내부 계층(하위 패키지) 직수입 → provider 선택 판단 유출 (위반)
    """
    cat = "collect"
    api = _collect_api_names()
    internal_pkgs = _collect_internal_packages()
    assemblers = _COLLECT_ASSEMBLERS & (api | _COLLECT_ASSEMBLERS)
    if not api or internal_pkgs is None:
        # fail-closed — 목록을 못 읽으면 검사가 *조용히 무력화*된다. 통과시키지 않는다.
        what = "__all__" if not api else "하위 패키지 목록"
        report.add(Violation(
            cat, "collect/self-check", "JARVIS09_COLLECTOR/__init__.py", 0,
            f"09 의 {what} 을 읽지 못해 수집 검사가 무력화됨 — 검사 자체를 고칠 것"))
        report.checks_run += 1
        return

    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if rel_s == "shared/precommit_check.py" or rel_s.startswith(_COLLECT_OWNER):
            continue
        text = _read_py(p)
        if text is None:
            continue
        lines = text.splitlines()
        code = [(i, l) for i, l in enumerate(lines, 1) if not l.lstrip().startswith("#")]

        # ④⑤ 정문 우회 — 예외 폴더(03·tools)도 대상. 정문은 *누구에게나* 정문이다.
        for i, l in code:
            m = _COLLECT_IMPORT_09.match(l)
            if m:
                if m.group(1).split(".")[0] in internal_pkgs:
                    report.add(Violation(
                        cat, "collect/internal-module", rel_s, i,
                        f"09 내부 계층 `{m.group(1)}` 직수입 — 09 **정문**"
                        f"(`from JARVIS09_COLLECTOR import ...`) 으로 받을 것"))
                continue
            m = _COLLECT_FROM_09.match(l)
            if not m:
                continue
            sub = m.group(1) or ""
            if sub.split(".")[0] in internal_pkgs:
                report.add(Violation(
                    cat, "collect/internal-module", rel_s, i,
                    f"09 내부 계층 `{sub}` 직수입 — provider 선택·폴백은 09 의 판단. "
                    f"09 **정문** 으로 받을 것"))
            for nm in _collect_imported_names(m.group(2), i, lines):
                if nm.startswith("_") and not nm.startswith("__"):
                    report.add(Violation(
                        cat, "collect/private-api", rel_s, i,
                        f"09 private 심볼 `{nm}` 직수입 — 내부 구현을 밖이 붙잡으면 "
                        f"09 가 자기 내부를 못 고친다. 09 에 **공개 정문** 을 만들어 쓸 것"))

        # ③ 원시 수집 라이브러리 — *데이터 API 를 실제로 쓰는지* 로 판정
        if not rel_s.startswith("JARVIS03_RADAR/"):     # 03 트렌드 수집만 예외 (CLAUDE.md)
            for i, l in code:
                m = _COLLECT_RAW_FROM.match(l)
                if m:
                    report.add(Violation(
                        cat, "collect/raw-lib", rel_s, i,
                        f"`{m.group(1)}` API 직접 import — 수집은 JARVIS09 단독"))
                    continue
                m = _COLLECT_RAW_IMPORT.match(l)
                if not m:
                    continue
                alias = m.group(2) or m.group(1)
                # 모듈 메타(`__file__` 등) 외의 속성을 쓰면 = 데이터 API 사용 = 위반
                hits = re.findall(rf"\b{re.escape(alias)}\.(\w+)", text)
                if any(h not in _COLLECT_LIB_META_ATTRS for h in hits):
                    report.add(Violation(
                        cat, "collect/raw-lib", rel_s, i,
                        f"`{m.group(1)}` 로 직접 데이터 취득 — 수집은 JARVIS09 단독"))

        if any(rel_s.startswith(d) for d in _COLLECT_EXEMPT_DIRS):
            continue

        # ② 조립 함수 유출
        for i, l in code:
            for name in assemblers:
                if re.search(rf"\b{name}\s*\(", l):
                    report.add(Violation(
                        cat, "collect/assembler-outside", rel_s, i,
                        f"수집 산출물 조립 `{name}()` 을 09 밖에서 호출 — "
                        f"조립은 compose_collected(09) 안에서만"))

        # ① 수집 API 조합
        used: dict[str, int] = {}
        for i, l in code:
            for name in api:
                if name in used:
                    continue
                if re.search(rf"\b{name}\s*\(", l):
                    used[name] = i
        if len(used) >= 2:
            first_line = min(used.values())
            report.add(Violation(
                cat, "collect/orchestration-outside", rel_s, first_line,
                f"09 수집 API {len(used)}종({', '.join(sorted(used))})을 한 파일에서 조합 "
                f"— 순서·폴백·조립 판단이 09 밖에 생김. `collect_all()` 한 번으로 받을 것"))

    report.checks_run += 5


CATEGORIES["collect"] = check_collect


# ══════════════════════════════════════════════════════════════════
#  cache — system 프롬프트는 *플랫폼 무관* 이어야 한다 (ERRORS [542])
# ══════════════════════════════════════════════════════════════════
def _platform_varying_keys() -> set:
    """PLATFORM_SPEC 에서 *플랫폼마다 값이 다른* 키만 파생 (원칙② — 목록을 박지 말 것).

    반환 빈 집합 = 파생 실패. 호출자가 fail-closed 처리한다.
    """
    try:
        import ast as _ast
        src = (ROOT / "JARVIS02_WRITER" / "draft_writer.py").read_text(encoding="utf-8")
        tree = _ast.parse(src)
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Assign):
                continue
            if not any(getattr(t, "id", "") == "PLATFORM_SPEC" for t in node.targets):
                continue
            per_platform = {}
            for vnode in node.value.values:          # 플랫폼별 dict
                for k, v in zip(vnode.keys, vnode.values):
                    per_platform.setdefault(k.value, []).append(_ast.dump(v))
            # 값이 플랫폼마다 다른 키만 = system 에 있으면 캐시를 깨는 것
            return {k for k, vs in per_platform.items() if len(set(vs)) > 1}
    except Exception:
        pass
    return set()


def check_cache(report: Report) -> None:
    """★ 작성 프롬프트의 system 블록에 플랫폼별 문구를 두지 말 것 (ERRORS [542]).

    **왜 (실측으로 확정)**
      프롬프트 캐시는 *prefix* 로 동작하고 `system` 이 그 앞부분이다:
        ① system 이 바이트 동일하면 user 가 완전히 달라도 system 은 회수된다 (read 23,875 실측)
        ② system 이 한 줄이라도 다르면 user 가 같아도 **전부 무효** (read 0 실측)
        ③ 블록 내부 부분 회수는 **없다** — 앞 27K 가 같아도 꼬리 한 줄이 다르면 통째로 날아간다
      경제 브리핑 system 은 약 44,300 토큰이다. `문체: 해요체/격식체` 한 줄 때문에
      네이버·티스토리가 매번 전량 재기록됐다.

    플랫폼별 지시는 `draft_writer.build_platform_block()` 단일 출구로만 — 그것은 user 로 간다.
    """
    cat = "cache"
    keys = _platform_varying_keys()
    if not keys:
        # fail-closed — 목록을 못 읽으면 검사가 *조용히 무력화*된다 (collect 와 같은 규약)
        report.add(Violation(
            cat, "cache/self-check", "JARVIS02_WRITER/draft_writer.py", 0,
            "PLATFORM_SPEC 에서 플랫폼 가변 키를 파생하지 못해 캐시 검사가 무력화됨 — 검사를 고칠 것"))
        report.checks_run += 1
        return

    path = ROOT / "JARVIS02_WRITER" / "draft_writer.py"
    lines = path.read_text(encoding="utf-8").splitlines()
    # system 블록 = `system_msg = f"""` … `"""`  +  이름에 system_msg 가 든 함수의 return f"""…"""
    in_block, opened_at = False, 0
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not in_block:
            if re.search(r'(system_msg\s*=\s*f"""|return f""")', s) and "system" in "".join(
                    lines[max(0, i - 12):i]).lower():
                in_block, opened_at = True, i
            continue
        if s.endswith('"""'):
            in_block = False
            continue
        for k in sorted(keys):
            if f"spec['{k}']" in ln or f'spec["{k}"]' in ln:
                report.add(Violation(
                    cat, "cache/platform-in-system",
                    "JARVIS02_WRITER/draft_writer.py", i,
                    f"system 블록(줄 {opened_at}~) 안에 플랫폼 가변 값 spec['{k}'] — "
                    f"네이버/티스토리 프리픽스가 갈려 캐시가 통째로 무효화된다. "
                    f"build_platform_block() 로 빼서 user_msg 에 둘 것"))
    report.checks_run += 1


CATEGORIES["cache"] = check_cache


# ══════════════════════════════════════════════════════════════════
#  crossproc — 크로스커팅 상태를 메모리 플래그로 두지 말 것 (사용자 박제 2026-07-25)
# ══════════════════════════════════════════════════════════════════
def check_crossproc(report: "Report") -> None:
    """프로세스 경계를 넘어야 하는 상태를 *메모리* 로만 판정하는 코드를 차단.

    **왜 (같은 병이 3번 났다)**
      경제 브리핑은 subprocess, 테마는 데몬 안에서 돈다. 그래서 `threading.Event`·전역 set
      같은 메모리 표식은 *한쪽에서만* 참이다. 실제 사고:
        · ERRORS [474] — 발행 우선 규칙이 `invoke_text` 에만 걸려 `run_sdk_query` 로 우회
        · 2026-07-25   — 배경 LLM 차단이 `_PUBLISHING_ACTIVE.is_set()`(Event)만 봐서
                         경제 발행(subprocess) 내내 데몬 쪽 배경 작업이 한도를 먹음
      CLAUDE.md 에 "프로세스 경계를 넘는가" 가 적혀 있었지만 *검사가 없어* 반복됐다.

    **규칙** — 크로스커팅 상태(발행 중 여부 등)는 파일/DB 가 진실이고 메모리는 캐시다.
      판정은 반드시 *합성 조회 함수*(`is_publishing()` 등)를 쓴다. 원시 Event 직접 조회 금지.

    검사: `_PUBLISHING_ACTIVE.is_set()` 를 소유 모듈(shared/llm.py) 밖에서, 또는 소유 모듈
      안이라도 합성 함수(`is_publishing`)를 우회해 *판정에* 쓰면 위반.
    """
    cat = "crossproc"
    owner = "shared/llm.py"
    # 소유 모듈 안에서 원시 Event 조회가 허용되는 곳 = 합성 함수 자신과 상태 조작부
    allowed_owner_ctx = ("def is_publishing", "def mark_publishing",
                         "def _reset_publishing_state", "_PUBLISHING_ACTIVE.set",
                         "_PUBLISHING_ACTIVE.clear")
    pat = re.compile(r"_PUBLISHING_ACTIVE\s*\.\s*is_set\s*\(")
    for p in _iter_py():
        rel_s = str(p.relative_to(ROOT))
        if any(seg in rel_s for seg in (".venv/", "__pycache__", "node_modules")):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, 1):
            if not pat.search(line):
                continue
            if rel_s == owner:
                ctx = "\n".join(lines[max(0, i - 12): i + 2])
                if any(w in ctx for w in allowed_owner_ctx):
                    continue
            report.add(Violation(
                cat, "crossproc/memory-flag-as-truth", rel_s, i,
                "발행 여부를 메모리 Event 로 직접 판정 — subprocess 에서 항상 False. "
                "`is_publishing()`(파일 표식 포함) 또는 `bg_defer_reason()` 을 쓸 것"))

    # ② 잡 래퍼는 picklable 이어야 한다 (processpool 잡 6개) — 실제 직렬화로 확인
    #
    # ★ sys.path 에 저장소 루트를 얹고 부른다 (2026-08-05).
    #   훅은 `python3 shared/precommit_check.py` 로 실행하므로 sys.path[0] 이 `shared/` 다.
    #   그래서 `JARVIS04_SCHEDULER` 를 못 찾고 ModuleNotFoundError 로 떨어졌는데,
    #   종전 코드가 그걸 `except: pass` 로 삼켜 **이 검사는 쓰인 이래 한 번도 실행된 적이
    #   없었다**(system·venv 양쪽에서 실측 확인). 화면엔 계속 "위반 0건" 이 떴다.
    #   같은 함정을 `_collect_api_names()` 는 이미 알고 피했는데(그 docstring 참조)
    #   여기만 빠져 있었다 — 같은 병의 두 번째 발현.
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from JARVIS04_SCHEDULER.job_llm_priority import selfcheck as _jlp_selfcheck
        why = _jlp_selfcheck()
        if why:
            report.add(Violation(
                cat, "crossproc/job-wrapper-unpicklable",
                "JARVIS04_SCHEDULER/job_llm_priority.py", 0, why))
    except Exception as _e:
        # ★ fail-closed (2026-08-05) — 종전엔 `pass` 였다.
        #   이 import 가 깨지면 잡 래퍼 직렬화 검사가 *조용히 사라지는데* 화면에는
        #   여전히 "통과, 위반 0건" 이 뜬다. 검사가 없는 것과 통과한 것은 다르다.
        #   `collect/self-check` 와 같은 형태로 통일한다.
        report.add(Violation(
            cat, "crossproc/self-check",
            "JARVIS04_SCHEDULER/job_llm_priority.py", 0,
            f"잡 래퍼 직렬화 검사를 실행하지 못해 무력화됨 ({type(_e).__name__}: {_e}) "
            "— 검사 자체를 고칠 것"))

    report.checks_run += 2


CATEGORIES["crossproc"] = check_crossproc


# ── symmetry — ③원칙(모든 곳 적용) 자동 검사 (2026-08-05) ──────────────────
#
# ★ 왜 이 카테고리가 필요한가
#   CLAUDE.md 3원칙 중 ①②는 이 파일이 이미 자동 강제하는데 **③만 사람 손에** 맡겨져
#   있었다. 그래서 ③만 반복해서 샜다 — 프로덕션 감사가 실례 5건을 찾았다:
#     · json_store(원자 저장)를 만들어 놓고 2개 파일에만 적용
#     · redact_logs 가 로그 디렉터리 5개 중 1개만 훑음
#     · 로그 회전이 daemon.log 에만
#     · 마스킹이 12자 이상만
#     · 이미지 프로바이더 교체 때 thumbnail_maker 를 빠뜨림 (2026-08-05 실제 발생)
#   공통 구조: **보호 함수 F 가 있는데 적용 대상 S 의 진부분집합에만 걸려 있다.**
#
# ★ self-match 로 신선도를 재지 않는다 (설계 검토에서 폐기된 초안)
#   "이 정규식은 owner 본체에도 매칭돼야 한다" 는 발상은 owner 를 한 줄 regex 로
#   표현할 수 있을 때만 성립한다. `json_store` 의 실제 쓰기는
#   `write_text(_dumps(...))` + `os.replace` 라 `json.dump(` 에 안 걸린다 —
#   초판대로 냈으면 **검사가 자기 self-check 에 걸려 실제 위반을 0건 보고**했다.
#   → owner 의 생존은 **동작 확인**(`store_effective()`)으로 판정한다.
def check_symmetry(report: Report) -> None:
    """③원칙 — 보호 장치가 *일부에만* 걸린 상태를 검출."""
    cat = "symmetry"

    # ① 원자적 JSON 저장 — owner 가 살아 있는가를 *동작* 으로 먼저 확인 (fail-closed)
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from JARVIS07_GUARDIAN.json_store import store_effective
        if store_effective() is False:
            report.add(Violation(
                cat, "symmetry/self-check", "JARVIS07_GUARDIAN/json_store.py", 0,
                "원자 저장이 실제로 동작하지 않음 — 이 검사의 전제가 무너졌다"))
            report.checks_run += 1
            return
    except Exception as e:
        report.add(Violation(
            cat, "symmetry/self-check", "JARVIS07_GUARDIAN/json_store.py", 0,
            f"원자 저장 owner 를 확인하지 못해 검사 무력화 ({type(e).__name__}: {e})"))
        report.checks_run += 1
        return

    raw_write = re.compile(r"json\.dump\(|write_text\(\s*json\.dumps")
    for p in _iter_py():
        rel = str(p.relative_to(ROOT))
        if "json_store.py" in rel or rel.startswith("tests/"):
            continue      # owner 자신과 테스트는 대상 아님
        text = _read_py(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if raw_write.search(line):
                report.add(Violation(
                    cat, "symmetry/json-atomic", rel, i,
                    "JSON 을 직접 쓴다 — 쓰는 도중 죽으면 잘린 파일이 남는다. "
                    "`json_store.write_json()` 을 쓸 것 (원자 교체·fsync·락)"))

    # ② 형제 대칭 — 같은 폴더의 `post_to_*` 같은 짝은 같은 처리를 갖춰야 한다.
    #   (블록 타입 추가 시 발행자 양쪽 동시 갱신 의무 — CLAUDE.md 제4조 6곳 규정)
    sib: dict = {}
    for p in _iter_py():
        text = _read_py(p)
        if text is None:
            continue
        for m in re.finditer(r"^def (post_to_\w+)", text, re.M):
            sib.setdefault(p.parent, set()).add(m.group(1))
    for folder, names in sib.items():
        if len(names) < 2:
            continue
        # 각 발행자가 다루는 블록 타입 집합이 어긋나면 한쪽만 고친 것이다.
        types: dict = {}
        for n in sorted(names):
            for p in folder.glob("*.py"):
                text = _read_py(p) or ""
                if f"def {n}" in text:
                    types[n] = set(re.findall(r'btype\s*==\s*["\'](\w+)["\']', text))
        if len(types) >= 2:
            allt = set().union(*types.values())
            for n, ts in types.items():
                missing = allt - ts
                if missing:
                    report.add(Violation(
                        cat, "symmetry/sibling-drift",
                        str(folder.relative_to(ROOT)), 0,
                        f"{n} 이 형제와 다른 블록 타입 집합을 갖는다 — 누락: {sorted(missing)}"))

    # ③ ★ `.get(키, 기본값)` 결과를 **or 가드 없이** 첨자/슬라이스 (2026-08-07)
    #
    #   `dict.get(k, D)` 는 *키가 없을 때만* D 를 쓴다. 키가 **있고 값이 None** 이면
    #   그대로 None 을 돌려준다 — 그래서 `[...]` 에서 `TypeError: 'NoneType' object is
    #   not subscriptable` 가 난다. DB 의 NULL 이 정확히 이 꼴로 들어온다
    #   (`error_log.traceback` 은 실측 4,159/5,076 = 82% 가 NULL).
    #
    #   ★ 실제로 값을 치렀다 — `guardian_agent._try_sdk_targeted_fix` 가 이 병으로 터져
    #     Tier-2(LLM 수리) 브리지가 막혔고, 7/27 이후 llm 시도 22건 중 **18건이 wontfix**
    #     로 쌓였다. 밴딧 `llm` arm 의 유일한 보상 경로가 조용히 끊겨 있었다.
    #
    #   ★ 왜 *사전* 검사인가: `pattern_fixer._fix_none_slicing` 이라는 **사후 수리기**는
    #     이미 있었다. 터진 다음 고치는 코드는 있는데 들어오는 걸 막는 코드가 없었다.
    #     이 레그가 그 사전판이다(판정 로직은 여기 하나 — ①원칙).
    #
    #   처방은 `(d.get(k) or D)[...]`. `or` 는 None 뿐 아니라 빈 컬렉션까지 D 로 바꾸므로
    #   인덱스 접근(`[0]`·`[i]`)의 IndexError 도 함께 막는다 (실증 12/12).
    for p in _iter_py():
        rel = str(p.relative_to(ROOT))
        if rel == "shared/precommit_check.py":
            continue                      # 이 검사기 자신의 설명 코드 제외
        if rel.startswith("tests/"):
            continue                      # 테스트는 일부러 위험한 꼴을 만든다
        text = _read_py(p)
        if text is None or ".get(" not in text:
            continue                      # 2-phase — 사전 필터로 AST 파싱 비용 회피
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            # fail-closed — 못 읽었으면 통과가 아니라 위반이다 (collect/self-check 관례)
            report.add(Violation(cat, "symmetry/self-check", rel, e.lineno or 0,
                                 f"파싱 실패로 검사 무력화: {e.msg}"))
            continue
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Call)):
                continue
            fn = node.value.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "get"
                    and node.value.args):
                continue
            # ★ 면제 — 목록을 박지 않는다(②원칙). "값이 None 일 수 없는 매핑" 만 제외.
            #   `os.environ` 은 값이 항상 str 이고 없으면 default 를 준다(언어 계약).
            obj = ast.get_source_segment(text, fn.value) or ""
            if obj.split(".")[-1] == "environ":
                continue
            src_line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if " or " in src_line:
                continue                  # 이미 가드됨
            report.add(Violation(
                cat, "symmetry/get-default-unguarded", rel, node.lineno,
                f"{src_line.strip()[:88]}  → `({obj}.get(...) or 기본값)[...]` 로 감쌀 것"))
    report.checks_run += 3


def check_dashboard(report: Report) -> None:
    """대시보드 글자 크기·색상 규정 (CLAUDE.md '웹 대시보드 폰트/색상 규정').

    ★ 왜 이 검사가 이제야 생겼나 (2026-08-08 — ERRORS [589])
      규정은 오래전부터 있었지만 **검증 명령이 `app.py` 를 겨눴다.** 그 파일은 Streamlit
      대시보드와 함께 커밋 `0be08d9` 에서 삭제됐다. 대상이 없으니 grep 은 늘 "위반 0" 이었고,
      규정은 *한 번도 집행되지 않은 채* 살아있는 Next.js 대시보드에 94곳이 쌓였다.
      **규정의 대상이 사라지면 규정은 조용히 죽는다** — 그래서 사람 손이 아니라 여기에 건다.

    ★ 대상을 목록으로 박지 않는다(②) — `dashboard/` 의 추적 파일에서 파생한다.
      새 페이지를 만들면 자동으로 검사 대상이 된다.
    """
    cat = "dashboard"
    root = ROOT / "dashboard"
    if not root.is_dir():
        # fail-closed — 대상 폴더가 사라졌으면 '통과' 가 아니라 '검사 무력화' 다.
        # 종전 규정이 정확히 이 방식으로 죽었다.
        report.add(Violation(cat, "dashboard/self-check", "dashboard/", 0,
                             "대시보드 폴더를 찾지 못함 — 규정 대상이 바뀌었으면 CLAUDE.md 를 함께 고칠 것"))
        return

    import subprocess as _sp
    try:
        files = _sp.run(["git", "ls-files", "dashboard"], cwd=str(ROOT),
                        capture_output=True, text=True).stdout.split()
    except Exception as e:
        report.add(Violation(cat, "dashboard/self-check", "dashboard/", 0,
                             f"파일 목록 파생 실패로 검사 무력화: {type(e).__name__}: {e}"))
        return
    files = [f for f in files if f.endswith((".tsx", ".ts", ".css"))]
    if not files:
        report.add(Violation(cat, "dashboard/self-check", "dashboard/", 0,
                             "검사할 파일이 0개 — 확장자 규칙이 낡았는지 확인할 것"))
        return

    size_re = re.compile(r'(?:fontSize:\s*|font-size:\s*)(\d+(?:\.\d+)?)')
    hex_re = re.compile(r'#[0-9a-fA-F]{6}\b')
    for rel in files:
        text = _read_py(ROOT / rel)
        if text is None:
            try:
                text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
        for i, line in enumerate(text.splitlines(), 1):
            if "precommit" in line or "eslint-disable" in line:
                continue
            for m in size_re.finditer(line):
                v = float(m.group(1))
                if v < 14:
                    report.add(Violation(cat, "dashboard/font-too-small", rel, i,
                                         f"{m.group(0)} — 최소 14px (CLAUDE.md 폰트 규정)"))
                elif v != int(v) or int(v) % 2:
                    report.add(Violation(cat, "dashboard/font-odd", rel, i,
                                         f"{m.group(0)} — 짝수만 허용"))
            # 색: globals.css 는 토큰 *정의처* 라 면제. 차트 시리즈 색은 주석으로 사유를 밝힌 줄만 면제.
            if rel.endswith("globals.css"):
                continue
            if hex_re.search(line) and "//" not in line and "차트" not in line:
                report.add(Violation(cat, "dashboard/inline-hex", rel, i,
                                     f"{hex_re.search(line).group(0)} — `var(--c-*)` 토큰 사용"))
    report.checks_run += 1


CATEGORIES["dashboard"] = check_dashboard


CATEGORIES["symmetry"] = check_symmetry


def run(categories: list[str] | None = None) -> Report:
    """검증 실행. categories=None 이면 전체.

    ★ 실행한 카테고리 이름을 `rep.ran` 에 남긴다 — 보고 문구가 *실제로 돈 것* 에서
      파생되도록(② 동적 설계). 손으로 센 숫자를 화면에 띄우지 않는다.
    """
    rep = Report()
    targets = categories or list(CATEGORIES.keys())
    for name in targets:
        fn = CATEGORIES.get(name)
        if not fn:
            print(f"⚠️ 알 수 없는 카테고리: {name}", file=sys.stderr)
            continue
        fn(rep)
        rep.ran.append(name)
    return rep


def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS pre-commit 검증")
    parser.add_argument("--category", "-c", action="append",
                        help="실행할 카테고리 (반복 가능). 미지정 시 전체.")
    parser.add_argument("--list", action="store_true",
                        help="카테고리 목록 출력 후 종료")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="위반 없을 때 stdout 출력 생략")
    args = parser.parse_args()

    if args.list:
        for k, fn in CATEGORIES.items():
            print(f"  {k:10s} — {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0

    rep = run(args.category)

    if rep.ok:
        if not args.quiet:
            # ★ 개수를 손으로 더하지 않는다 (2026-08-05).
            #   종전 `checks_run` 은 각 검사 함수가 `+= 3`·`+= 5` 처럼 손으로 더한 값이라
            #   검사를 늘려도 숫자를 안 고치면 조용히 어긋났다(실측: harness 는 6이라
            #   세는데 실제 9개). 화면에 뜨는 숫자가 틀리면 *숫자를 믿을 수 없게* 된다.
            #   → 실제로 실행한 카테고리 수를 센다. 늘리면 자동으로 따라온다.
            print(f"✅ JARVIS pre-commit 통과 — {len(rep.ran)}개 카테고리 검증, 위반 0건")
        return 0

    # 위반 출력 (카테고리별 그룹)
    print(f"❌ JARVIS pre-commit 위반 {len(rep.violations)}건 발견", file=sys.stderr)
    for cat, vs in rep.by_category().items():
        print(f"\n[{cat}] {len(vs)}건", file=sys.stderr)
        for v in vs[:20]:  # 카테고리당 최대 20건만 표시
            print(v.fmt(), file=sys.stderr)
        if len(vs) > 20:
            print(f"  ... (+{len(vs) - 20} 추가)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
