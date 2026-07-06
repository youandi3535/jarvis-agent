"""JARVIS07_GUARDIAN/architecture.py — 오류 자동 캐치·수정 아키텍처 *단일 진실 소스*.

★ 단일 진입점 원칙 (사용자 박제 2026-06-28):
   티어 구조·catch 메커니즘·심각도 매트릭스·안전장치 설정을 *이 파일에서만* 정의.
   대시보드(hub.py)·텔레그램(guardian_agent._status_section)·문서가 모두 이 모듈을 읽는다.
   아키텍처 변경 시 *이 파일만* 수정 → 전체 자동 반영. (수정해도 일부만 반영되는 사고 차단)

규칙 (사용자 박제):
   · 티어 번호는 *정수, 1부터*. Tier 0 · Tier 1.5 · Tier 2.5 같은 표기 절대 금지.
   · catch() 는 *단일 진입점* (탐지·수집) — 번호 없는 진입 계층.
   · 자동 수정은 2개 티어: Tier 1(패턴·Bandit, LLM 0) → Tier 2(LLM Sonnet 5).
"""
from __future__ import annotations

# ── 탐지 단일 진입점: catch() 6개 메커니즘 ──────────────────────────────
# 모든 오류가 이 6개 경로를 통해 catch() 로 *직접* 진입한다 (error_collector.catch).
CATCH_MECHANISMS = [
    ("sys.excepthook",             "메인 스레드 미처리 예외"),
    ("threading.excepthook",       "백그라운드 스레드 예외"),
    ("APScheduler EVENT_JOB_ERROR","스케줄 잡 실패"),
    ("log_scanner",                "JARVIS*/logs/ ERROR·WARNING 줄"),
    ("auto_catch 데코레이터",       "함수 단위 wrap — @auto_catch('agent')"),
    ("report() / catch() 직접",     "try/except 블록 명시 호출"),
]

# ── 자동 수정 티어 (정수, 1부터) ─────────────────────────────────────────
# ★ 모델명은 shared/llm.py(SSOT)에서 파생 — 하드코딩 금지 (사용자 박제 2026-07-04).
#   코드가 모델을 바꾸면 웹·텔레그램 표시가 자동으로 따라온다.
from shared.llm import get_spec as _get_spec, model_label as _model_label
LLM_TIER_MODEL = _get_spec("guardian").model_id   # Tier 2 LLM 실모델 (SSOT 파생)

TIERS = [
    {
        "n":        1,
        "name":     "패턴 자동 수정",
        "engine":   "static 6종 + 학습 패턴 + Contextual Bandit (Linear UCB)",
        "uses_llm": False,
        "detail":   "Group 1(static 6 + hit≥3) · Group 2(신규 hit 1~2) — Bandit 랭킹. LLM 호출 0.",
    },
    {
        "n":        2,
        "name":     "LLM 자동 수정",
        "engine":   f"Claude Code SDK · {_model_label('guardian')}",
        "uses_llm": True,
        "detail":   "Tier 1 실패 시 위임. AST 검증 + .bak 자동 롤백. 패치 크기 무제한.",
    },
]

# ── 심각도별 처리 매트릭스 ───────────────────────────────────────────────
# (severity, 처리 흐름, 비고)
SEVERITY_MATRIX = [
    ("low",      "Tier 1 → Tier 2", "학습 후 다음엔 Tier 1 즉시 해결"),
    ("medium",   "Tier 1 → Tier 2", "수정 실패 시 알림"),
    ("high",     "Tier 1 → Tier 2", "항상 알림"),
    ("critical", "Tier 1만",         "LLM 생략 (안전) · 수동 검토"),
]

# ── 안전장치 설정 (단일 진실 소스) ───────────────────────────────────────
CB_MAX_HOUR          = 10     # Circuit breaker: 시간당 최대 자동수정 건수
ESCALATE_THRESHOLD   = 3      # 1시간 내 N회 반복 → severity 한 단계 상향
ESCALATE_WINDOW_SECS = 3600   # severity 상향 관찰 창 (초)
DOMAIN_SKEW_THRESHOLD   = 25   # 한 도메인 학습 패턴 N+ 누적 시 근본 리팩터 검토 (ADR 008) — 표시 SSOT
ERROR_STATS_WINDOW_DAYS = 7    # 오류 통계 집계 기본 윈도우(일) — get_error_stats·표시 공용 SSOT
# ★ 사용자 박제 2026-07-06 — job_retry_pending 무한 재시도로 인한 조용한 토큰 소모 사고 재발 방지.
# 어떤 재시도도 최대 3회 (하네스 max_attempts 원칙과 동일 상수). 같은 error_id 가
# Tier 2(LLM) 를 이 횟수만큼 이미 시도했으면 재시도 없이 wontfix + 텔레그램 알림.
MAX_LLM_ATTEMPTS = 3
DENY_FIX_PATHS = {            # 자동수정 절대 금지 파일 (보안·코어)
    ".env", "jarvis_daemon.py",
    "login_manager.py", "naver_cookies.pkl", "tistory_cookies.pkl",
}

__all__ = [
    "CATCH_MECHANISMS", "TIERS", "SEVERITY_MATRIX", "LLM_TIER_MODEL",
    "CB_MAX_HOUR", "ESCALATE_THRESHOLD", "ESCALATE_WINDOW_SECS", "DENY_FIX_PATHS",
    "DOMAIN_SKEW_THRESHOLD", "ERROR_STATS_WINDOW_DAYS", "MAX_LLM_ATTEMPTS",
    "tier_flow_for", "telegram_summary",
]


def tier_flow_for(severity: str) -> str:
    """심각도 → 처리 흐름 문자열 (예: 'Tier 1 → Tier 2')."""
    for sev, flow, _ in SEVERITY_MATRIX:
        if sev == severity:
            return flow
    return "Tier 1 → Tier 2"


def telegram_summary() -> str:
    """텔레그램 /status 용 아키텍처·정책 요약 블록."""
    tier_lines = " · ".join(f"Tier {t['n']}({t['name']})" for t in TIERS)
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🎣 *오류 캐치·수정 아키텍처*\n"
        f"catch() 단일 진입점 ← {len(CATCH_MECHANISMS)}개 메커니즘\n"
        "  (excepthook · threading · APScheduler · log_scanner · auto_catch · report)\n"
        f"⚙️ 자동 수정: {tier_lines}\n"
        "  Tier 1 = static 6 + 학습 + Contextual Bandit (LLM 0)\n"
        f"  Tier 2 = LLM {_model_label('guardian')} (Tier 1 실패 시)\n"
        "LOW/MED/HIGH → Tier 1 → Tier 2 | CRITICAL → Tier 1만 → 수동 검토\n"
        f"{ESCALATE_THRESHOLD}회 반복 → severity 자동 상향 | Circuit breaker {CB_MAX_HOUR}건/시간"
    )
