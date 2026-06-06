#!/usr/bin/env python3
"""
monitor.py — JARVIS 실시간 로그 모니터
────────────────────────────────────────
VS Code 터미널에서 실행:
    python monitor.py                    # 전체 로그 실시간
    python monitor.py --only harness     # 특정 컴포넌트만
    python monitor.py --only harness,writer,guardian
    python monitor.py --level warning    # WARNING 이상만
    python monitor.py --tail 200         # 최근 200줄부터 (기본 50)

컴포넌트 키워드 목록:
    daemon / harness / writer / guardian / radar / scheduler
    publisher / image / infra / bot / bus / llm / login
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# ── 경로 ────────────────────────────────────────────────────
ROOT    = Path(__file__).parent
LOG_DIR = ROOT / "logs"
DAEMON_LOG = LOG_DIR / "daemon.log"

# ── ANSI 색상 ────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

# 레벨별 색상
LEVEL_COLOR = {
    "DEBUG"   : "\033[36m",     # cyan
    "INFO"    : "\033[97m",     # bright white
    "WARNING" : "\033[33m",     # yellow
    "WARN"    : "\033[33m",
    "ERROR"   : "\033[91m",     # bright red
    "CRITICAL": "\033[41;97m",  # red bg + white
}

# 컴포넌트별 색상 (logger name 기반)
COMPONENT_COLOR = {
    "harness"   : "\033[94m",   # bright blue  — 핵심 순환 엔진
    "writer"    : "\033[92m",   # bright green — 글 작성
    "guardian"  : "\033[95m",   # bright magenta — 오류 감시
    "radar"     : "\033[96m",   # bright cyan  — 트렌드 분석
    "scheduler" : "\033[93m",   # bright yellow — 스케줄러
    "publisher" : "\033[35m",   # magenta — 발행
    "image"     : "\033[34m",   # blue — 이미지
    "infra"     : "\033[90m",   # dark gray — 인프라
    "bot"       : "\033[37m",   # gray — 텔레그램 봇
    "bus"       : "\033[36m",   # cyan — 이벤트 버스
    "llm"       : "\033[33m",   # yellow — LLM 호출
    "login"     : "\033[31m",   # red — 인증
    "daemon"    : "\033[97m",   # white — 데몬 메인
    "notify"    : "\033[35m",   # magenta — 알림
}

# 특수 패턴 강조
HIGHLIGHT_PATTERNS = {
    "▶️"   : BOLD + "\033[94m",   # 동작 시작 — 파랑 굵게
    "✅"   : BOLD + "\033[92m",   # 검증 통과 — 초록 굵게
    "📤"   : BOLD + "\033[92m",   # 송출 완료 — 초록 굵게
    "🔧"   : BOLD + "\033[93m",   # 즉시 수정 — 노랑 굵게
    "🚫"   : BOLD + "\033[91m",   # abort     — 빨강 굵게
    "🚨"   : BOLD + "\033[41m",   # 긴급 에스컬레이션
    "⚠️"   : "\033[33m",          # 경고
    "❌"   : "\033[91m",          # 실패
    "Running job" : "\033[93m",   # 잡 시작
    "executed successfully" : "\033[92m",  # 잡 완료
}


def _component_color(logger_name: str) -> str:
    """logger name 에서 컴포넌트 키워드 추출 → 색상 반환."""
    name_lower = logger_name.lower()
    for key, color in COMPONENT_COLOR.items():
        if key in name_lower:
            return color
    return "\033[97m"  # default white


def _colorize_line(line: str, only_keywords: list[str], min_level: str) -> str | None:
    """한 줄을 파싱해 색상 입힌 문자열 반환. 필터 대상이면 None."""
    line = line.rstrip()
    if not line:
        return None

    # ── 파싱: "2026-05-18 09:48:16,277 [INFO    ] JARVIS-DAEMON                ... msg"
    parts = line.split(None, 3)
    if len(parts) < 4:
        # 파싱 불가 줄 (print() 출력 등) — 그냥 dim 처리
        if only_keywords:
            return None
        return DIM + line + RESET

    date_s, time_s = parts[0], parts[1]
    level_raw = parts[2].strip("[]").strip()
    rest = parts[3]  # "   ] LOGGER_NAME    message"  또는  "LOGGER_NAME    message"

    # [INFO    ] 포맷에서 닫힘 브래킷(]) 이 rest 앞에 붙어 있을 수 있음 → 제거
    rest = rest.lstrip("] \t")

    # logger name + message 분리
    rest_parts = rest.split(None, 1)
    logger_name = rest_parts[0] if rest_parts else ""
    message     = rest_parts[1] if len(rest_parts) > 1 else ""

    # ── 레벨 필터 ──
    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "WARN": 2, "ERROR": 3, "CRITICAL": 4}
    min_order   = level_order.get(min_level.upper(), 0)
    cur_order   = level_order.get(level_raw.upper(), 1)
    if cur_order < min_order:
        return None

    # ── 컴포넌트 필터 ──
    if only_keywords:
        combined = (logger_name + " " + message).lower()
        if not any(kw.lower() in combined for kw in only_keywords):
            return None

    # ── 색상 조합 ──
    level_c = LEVEL_COLOR.get(level_raw.upper(), "\033[97m")
    comp_c  = _component_color(logger_name)

    # 특수 패턴 강조
    msg_colored = message
    for pat, col in HIGHLIGHT_PATTERNS.items():
        if pat in msg_colored:
            msg_colored = msg_colored.replace(pat, col + pat + RESET)
            break

    # 조립
    ts    = DIM + f"{date_s} {time_s}" + RESET
    level = level_c + f"[{level_raw:<8}]" + RESET
    name  = comp_c + f"{logger_name:<28}" + RESET
    msg   = level_c + msg_colored + RESET

    return f"{ts} {level} {name} {msg}"


def _print_header(args: argparse.Namespace) -> None:
    """시작 헤더 출력."""
    print(BOLD + "\033[94m" + "─" * 80 + RESET)
    print(BOLD + "\033[94m  🤖 JARVIS 실시간 모니터" + RESET)
    print(BOLD + "\033[94m" + "─" * 80 + RESET)
    print(f"  📄 로그 파일 : {DAEMON_LOG}")
    if args.only:
        print(f"  🔍 필터      : {', '.join(args.only)}")
    if args.level.upper() != "DEBUG":
        print(f"  📊 레벨 기준 : {args.level.upper()} 이상")
    print(f"  📌 시작 위치 : 최근 {args.tail}줄부터")
    print(BOLD + "\033[94m" + "─" * 80 + RESET)
    print(DIM + "  Ctrl+C 로 종료" + RESET)
    print()


def _seek_tail(f, n: int) -> None:
    """파일 끝에서 n줄 앞으로 이동."""
    try:
        f.seek(0, 2)
        size = f.tell()
        chunk = min(size, n * 200)  # 줄당 평균 200바이트 추정
        f.seek(max(0, size - chunk))
        lines = f.read().split("\n")
        target = max(0, len(lines) - n - 1)
        # 시작점 재계산
        f.seek(0, 2)
        rewind = sum(len(l) + 1 for l in lines[target:])
        f.seek(max(0, size - rewind))
    except Exception:
        f.seek(0, 2)


def monitor(args: argparse.Namespace) -> None:
    """실시간 모니터 메인 루프."""
    _print_header(args)

    if not DAEMON_LOG.exists():
        print(f"\033[91m❌ 로그 파일 없음: {DAEMON_LOG}\033[0m")
        print("   데몬이 실행 중인지 확인하세요: python jarvis_daemon.py")
        sys.exit(1)

    only = args.only  # list or []

    with open(DAEMON_LOG, "r", encoding="utf-8", errors="replace") as f:
        _seek_tail(f, args.tail)

        # 기존 줄 출력
        for line in f:
            colored = _colorize_line(line, only, args.level)
            if colored is not None:
                print(colored)

        # 실시간 tail
        print(DIM + "\n  ⏳ 실시간 감시 중..." + RESET + "\n")
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.3)
                # 파일 교체(logrotate) 대응
                try:
                    if os.stat(DAEMON_LOG).st_ino != os.fstat(f.fileno()).st_ino:
                        f.close()
                        f = open(DAEMON_LOG, "r", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                continue
            colored = _colorize_line(line, only, args.level)
            if colored is not None:
                print(colored, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="JARVIS 실시간 로그 모니터",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--only", "-o",
        type=lambda s: [x.strip() for x in s.split(",")],
        default=[],
        metavar="KEYWORDS",
        help="컴포넌트 필터 (쉼표 구분). 예: harness,writer,guardian",
    )
    parser.add_argument(
        "--level", "-l",
        default="debug",
        metavar="LEVEL",
        help="최소 로그 레벨 (debug/info/warning/error). 기본: debug",
    )
    parser.add_argument(
        "--tail", "-n",
        type=int,
        default=50,
        metavar="N",
        help="시작 시 표시할 최근 줄 수. 기본: 50",
    )
    args = parser.parse_args()

    try:
        monitor(args)
    except KeyboardInterrupt:
        print("\n\n" + DIM + "  모니터 종료." + RESET)


if __name__ == "__main__":
    main()
