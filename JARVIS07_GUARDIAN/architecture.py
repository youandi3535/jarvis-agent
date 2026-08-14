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
# 어떤 재시도도 harness.DEFAULT_MAX_ATTEMPTS(SSOT). 같은 error_id 가
# Tier 2(LLM) 를 이 횟수만큼 이미 시도했으면 재시도 없이 wontfix + 텔레그램 알림.
#
# ★ 2026-08-14 — **주석이 주장하던 파생을 코드가 실제로 하게 했다** (계약 드리프트 시정).
#   종전 이 줄은 `MAX_LLM_ATTEMPTS = 3` 리터럴이었다. 바로 위 주석은 "harness.
#   DEFAULT_MAX_ATTEMPTS(SSOT, 현재 2회)" 라고 적혀 있었는데 **실제 값은 3** 이었다 —
#   문서가 코드를 설명하지 못하는 상태로, `HARNESS_MAX_ATTEMPTS` 노브를 아무리 돌려도
#   이 상한만 움직이지 않았다. '복사본을 진실로 믿지 말 것'(CLAUDE.md 최우선 원칙)이
#   말하는 그 형태다: 값을 복사해 두고 원본이 바뀌어도 사본만 옛 값으로 남는다.
#   → 이제 원본에서 읽는다. `HARNESS_MAX_ATTEMPTS` 하나로 두 층이 함께 움직인다.
from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS as _HARNESS_MAX_ATTEMPTS
MAX_LLM_ATTEMPTS = int(_HARNESS_MAX_ATTEMPTS)

# ★ 자율 SDK 수리 일일 상한 (사용자 박제 2026-08-12 · **축 분리** 2026-08-14)
#   뜻: "시스템 전체가 하루에 받는 자율 SDK 세션 수".
#
#   ★ 왜 MAX_LLM_ATTEMPTS 파생을 끊었나 (2026-08-14 실측)
#     종전은 `SDK_REPAIR_DAILY_CALLS = MAX_LLM_ATTEMPTS` 였다. "새 숫자를 만들지 않는다"
#     는 뜻이었지만, 두 값은 **직교하는 축** 이다 —
#       · MAX_LLM_ATTEMPTS      = *문제 하나* 에 주는 시도 수 (건당 축)
#       · SDK_REPAIR_DAILY_CALLS = *시스템 전체* 가 하루에 쓰는 세션 수 (전역 축)
#     묶어 놓으면 한쪽을 못 움직인다. 실측 사고(2026-08-14): 지문 1종이 6시간 10분 동안
#     하루 예산 3발을 통째로 독점했다(allowed 3 / blocked 64) — 건당 상한을 다 쓴 것이
#     곧 전역 예산 소진이라, 그날 다른 어떤 오류도 자율 수리를 한 번도 못 받았다.
#     ②동적 설계는 '숫자를 줄여라' 가 아니라 '**같은 것** 을 두 번 적지 말라' 다.
#     서로 다른 것을 한 이름으로 묶는 것은 파생이 아니라 우연한 일치다.
#
#   ★ 기본값 3 은 유지한다 — 과거 $81 사고(7일 54회, 8/10 하루 17회 $32.14)를 막은 값이다.
#     축만 분리하고 값은 그대로. 무배포 조정은 GUARDIAN_SDK_DAILY_CALLS 노브
#     (판정 주인 = repair_budget._daily_cap()). 실측 대입: 3회 x 중앙값 $1.98 ~= $6/일.
SDK_REPAIR_DAILY_CALLS = 3

# ── 오류 수명주기 상태 어휘 (단일 진실 소스 — 2026-08-14) ────────────────────
#
# ★ 왜 여기인가 (①단일 진입점)
#   같은 질문("이 오류를 다시 들여다봐도 되는가")에 두 곳이 **서로 다른 답** 을 갖고 있었다:
#     · shared.db.try_claim_error(from_statuses=("new", "ignored"))   ← 선점 가능 집합
#     · guardian_agent._collect_unresolved  → ("new", "wontfix")      ← 재수집 집합
#   교집합이 아니라 **어긋난 집합** 이라, sweep 이 긁어온 `wontfix` 행이 오케스트레이터
#   앞에서 선점에 실패하고 *조용히 return* 했다. 로그도 "이미 처리 착수됨" 이라
#   원인과 정반대로 읽힌다. 집합을 두 번 적었기 때문에 생긴 자기모순이다.
#
# ★ 관계가 코드로 보장된다 — 선점 집합은 재수집 집합의 **상위집합** 이어야 한다.
#   (수집해 놓고 선점 못 하는 조합이 다시 생기지 않게 파생으로 못 박는다)
STATUS_NEW = "new"                                  # 아직 아무도 손대지 않은 버킷
UNRESOLVED_STATUSES = (STATUS_NEW, "wontfix")       # sweep 이 다시 집는 '미해결'
#   ignored = 일시적·비코드로 *의도적으로* 격리한 버킷. sweep 은 안 집지만(격리가 목적),
#   재발이 들어오면 선점·재개는 가능해야 한다 → 선점 집합에만 포함.
CLAIMABLE_STATUSES = UNRESOLVED_STATUSES + ("ignored",)
#   종결됐지만 되살릴 수 있는 버킷 = 선점 가능한 것 중 '새것' 이 아닌 것 (파생).
REOPENABLE_STATUSES = tuple(s for s in CLAIMABLE_STATUSES if s != STATUS_NEW)

# ── '자동수정 성공' 을 뜻하는 상태 · 죽은 상태 (2026-08-14) ────────────────────
#
# ★ 왜 분리하나 — `resolved` 는 **쓰는 코드가 저장소 전역 0곳** 이다(실측 grep).
#   그런데 대시보드 집계 4곳이 `status IN ('fixed','resolved')` 로 *자동수정 성공* 에
#   합산하고 있었다. DB 실측: fixed 115건 · resolved 185건 — 즉 화면의 "자동수정"
#   숫자 중 **62%가 아무도 쓰지 않는 상태** 였다. resolved 행은 전부 2026-05-12~06-07
#   구간의 옛 기록이고 그 뒤로 단 1건도 생기지 않았다(30일 창 0건).
#   행을 지우지 않는다(이력) — 다만 *다른 것을 다른 이름으로* 센다.
STATUS_FIXED     = "fixed"
STATUS_WONTFIX   = "wontfix"
STATUS_ANALYZING = "analyzing"
STATUS_IGNORED   = "ignored"
STATUS_MANUAL    = "manual"
FIXED_STATUSES = (STATUS_FIXED,)          # 자동수정 성공으로 셀 상태 — 여기 하나뿐
LEGACY_STATUSES = ("resolved",)           # 쓰기 코드 0곳. 옛 행 보존용 — 집계에서 분리
ALL_STATUSES = (
    UNRESOLVED_STATUSES
    + (STATUS_ANALYZING, STATUS_FIXED, STATUS_IGNORED, STATUS_MANUAL)
    + LEGACY_STATUSES)

# ★ 2026-08-14 (P2) — 어휘 사본이 '없어진' 게 아니라 **옮겨 앉아 있었다**.
#   위 목록을 여기 단독으로 만들어 놓고도 `repair_history` 는 여전히
#   `status IN ('fixed','resolved','manual','wontfix')`(:374) 와
#   `if status in ("fixed","resolved")`(:211) 를 리터럴로 들고 있었다 —
#   즉 화면 한쪽만 새 정의를 따르고 *수리 이력 화면* 은 옛 정의로 남았다.
#   같은 어휘가 두 벌이면 반드시 갈라진다. 그래서 **파생 집합에도 이름을 준다**:
#   소비자가 자기 필요에 맞는 이름을 고르게 하고, 고를 이름이 없으면 여기에 만든다.

#   '손을 댄 결과가 있는' 상태 = 전체 − 아직 안 본 것(new·analyzing) − 격리한 것(ignored).
#   수리 이력(repair_history.history)이 보여주는 집합이 바로 이것이다.
ATTEMPTED_STATUSES = tuple(
    s for s in ALL_STATUSES
    if s not in (STATUS_NEW, STATUS_ANALYZING, STATUS_IGNORED))
#   '자동수정 성공으로 셀 수 있는 것' + '옛 정의로 성공이라 적힌 것'.
#   집계는 FIXED_STATUSES 만 세고, *이력 표시* 는 옛 행도 설명해야 하므로 이쪽을 쓴다.
FIXED_OR_LEGACY_STATUSES = FIXED_STATUSES + LEGACY_STATUSES
#   '아직 아무도 결론을 내지 않은' 버킷 — 이력 화면의 "아직 수리 전".
OPEN_STATUSES = (STATUS_NEW, STATUS_ANALYZING)
#   수리 비대상으로 *세워둔* 버킷 = `_park_non_code` 가 낼 수 있는 값 전부.
#   게이트가 이미 사유를 적어 둔 자리라 관성적으로 덮어쓰면 안 되는 곳이기도 하다.
PARKED_STATUSES = (STATUS_IGNORED, STATUS_WONTFIX)
#   종결된(= 더 이상 자동으로 다시 열리지 않는) 버킷 — circuit breaker 재청구 판정용.
CLOSED_STATUSES = PARKED_STATUSES + FIXED_STATUSES

# ── 합성(관측용) 행 — 집계에서 배제할 소스 (2026-08-14 P2) ────────────────────
#
# ★ 왜 어휘의 주인이 여기인가
#   `error_fixer.verification_tag_effective()` 는 '태그가 DB 까지 살아남는가' 를
#   **실제로 통과시켜** 확인한다(patch_effective 표준). 그래서 `error_log` 에 합성 행을
#   만들었다 지운다. 그런데 지우기 전 창(과 삭제 실패분)이 `guardian_slo()` 의
#   '검증된 코드수정' 에 **그대로 합산**됐다 — 관측이 관측 대상을 부풀렸다.
#   배제 조건을 집계 쿼리마다 복사하면 새 집계가 생길 때마다 또 샌다. 식별자는 하나.
SYNTHETIC_SOURCES = ("__smoke__",)
DENY_FIX_PATHS = {            # 자동수정 절대 금지 파일 (보안·코어)
    ".env", "jarvis_daemon.py",
    "login_manager.py", "naver_cookies.pkl", "tistory_cookies.pkl",
}

__all__ = [
    "SDK_REPAIR_DAILY_CALLS",
    "CATCH_MECHANISMS", "TIERS", "SEVERITY_MATRIX",
    "CB_MAX_HOUR", "ESCALATE_THRESHOLD", "ESCALATE_WINDOW_SECS", "DENY_FIX_PATHS",
    "DOMAIN_SKEW_THRESHOLD", "ERROR_STATS_WINDOW_DAYS", "MAX_LLM_ATTEMPTS",
    "STATUS_NEW", "UNRESOLVED_STATUSES", "CLAIMABLE_STATUSES", "REOPENABLE_STATUSES",
    "STATUS_FIXED", "STATUS_WONTFIX", "STATUS_ANALYZING", "STATUS_IGNORED", "STATUS_MANUAL",
    "FIXED_STATUSES", "LEGACY_STATUSES", "ALL_STATUSES",
    "ATTEMPTED_STATUSES", "FIXED_OR_LEGACY_STATUSES", "CLOSED_STATUSES",
    "OPEN_STATUSES", "PARKED_STATUSES",
    "SYNTHETIC_SOURCES",
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
