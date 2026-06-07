"""JARVIS pre-commit 검증 — CLAUDE.md 박제 grep 명령 통합 단일 진입점.

CLAUDE.md 의 27종 grep 검증 명령을 Python 으로 통합. 의존성 0
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


def _iter_py(extra_exclude: tuple[str, ...] = (), root: Path = ROOT) -> Iterable[Path]:
    """ROOT 하위 *.py 순회 (제외 경로 자동 필터)."""
    for p in root.rglob("*.py"):
        if _is_excluded(p, extra_exclude):
            continue
        yield p


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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
    pat3 = re.compile(r"(compress_to_korean|cap_content|count_korean|sanitize_body)\(")
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
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
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
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
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
        "revise_adapter",
        "auto_repair",
        "hub.py",                             # 대시보드 프로세스 제어
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
        "JARVIS06_IMAGE/",                    # 이미지 생성 (Pollinations — Bing/HF 폐기 2026-06-07)
        "JARVIS07_GUARDIAN/",                 # guardian 자가수정·git audit
        "jarvis_keeper.py",                   # 데몬 워치독 — 재시작 subprocess 정당
    )

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
    """① Pollinations URL 직접 호출 (JARVIS06_IMAGE 외부)
       ② ImageGenerationModel 직접 사용 (JARVIS06_IMAGE 외부)
    """
    cat = "image"
    pat1 = re.compile(r"https://image\.pollinations\.ai")
    pat2 = re.compile(r"ImageGenerationModel\(|imagen-[0-9]")
    img_dir = "JARVIS06_IMAGE/"

    # CLAUDE.md 규정 본문(이 파일 포함)은 검증 대상 외
    allow_files = ("shared/precommit_check.py", "CLAUDE.md")

    for p in _iter_py():
        rel = p.relative_to(ROOT)
        rel_s = str(rel)
        if img_dir in rel_s or any(a in rel_s for a in allow_files):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat1.search(line):
                report.add(Violation(cat, "image/pollinations", rel_s, i, line))
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
            (re.compile(r"^def\s+(build_length_phrase|build_prompt_length_block|build_short_length_phrase|count_korean|compress_to_korean)"),
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
    for owner in _DOMAIN_OWNERSHIP:
        if not owner.get("active", False):
            continue
        owner_dirs = owner["owner_dirs"]
        for p in _iter_py():
            rel = p.relative_to(ROOT)
            rel_s = str(rel)
            # owner 안이면 검증 제외
            if any(od in rel_s for od in owner_dirs):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
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
        report.checks_run += 1


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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
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


def run(categories: list[str] | None = None) -> Report:
    """검증 실행. categories=None 이면 전체."""
    rep = Report()
    targets = categories or list(CATEGORIES.keys())
    for name in targets:
        fn = CATEGORIES.get(name)
        if not fn:
            print(f"⚠️ 알 수 없는 카테고리: {name}", file=sys.stderr)
            continue
        fn(rep)
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
            print(f"✅ JARVIS pre-commit 통과 — {rep.checks_run}종 검증, 위반 0건")
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
