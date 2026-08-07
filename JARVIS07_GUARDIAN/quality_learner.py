"""JARVIS07_GUARDIAN/quality_learner.py — ★ 글 품질 강화학습 단일 진입점 (ADR 014).

오류 강화학습(bandit.py)과 대칭 구조의 *글 품질* 폐쇄 루프:

    [작성] build_insights_block() — UCB 랭킹으로 인사이트 선택 + 주입 + 사용 기록
       ↓ (발행 → post_quality_analyzer 100점 루브릭 채점 → post_analysis.quality_score)
    [보상] job_quality_learn() — 사용 기록 ↔ 채점 결과 매칭 → reward = 루브릭점수/100
       ↓
    [갱신] apply_insight_reward() — weight EMA 갱신 (좋은 인사이트 ↑ / 무효 인사이트 ↓)
       ↓
    [다음 글] 갱신된 weight 로 재선택 — 시간이 갈수록 검증된 지침만 살아남음

설계 원칙:
- 생산(daily_review·auto_approve)은 종전 그대로 — 이 모듈은 *선택·귀속·갱신* 만.
- LLM 호출 0 (순수 통계) — 발행 경로 지연 없음.
- 실패는 항상 조용히 "" / 0 반환 — 글 작성을 절대 막지 않음.
- SQL 은 shared/db.py 헬퍼만 사용 (신경계 규정).

사용 (작성기 3곳 — jarvis_main / economic_poster / trend_economic_writer):
    from JARVIS07_GUARDIAN.quality_learner import build_insights_block
    block = build_insights_block(scope="theme", theme=theme)   # "" 가능

스케줄 (JARVIS04 DEFAULT_JOBS):
    j07_quality_learn — 매일 23:45 (daily_review 22:00 · learn_log 23:30 이후)
"""
from __future__ import annotations

import math
import uuid
from typing import Optional

__all__ = [
    "build_insights_block",
    "attribute_pending_rewards",
    "job_quality_learn",
    "stats",
    "DIRECTIVE_MAX_LEN",
    "directive_issues",
    "is_learnable_directive",
    "active_directives",
    # ★ 항목별 학습 (2026-08-07) — 총점 하나가 아니라 항목마다 신호를 준다
    "insight_target_item", "item_reward", "item_reward_neutral",
    "weak_items", "maintained_items",
]

# ══════════════════════════════════════════════════════════════════
# ★ 학습 지침 위생 게이트 (2026-08-02 전수 감사 6위 — 사용자 승인)
# ══════════════════════════════════════════════════════════════════
# 실측: `learning_insights` 381건 중 HTML 조각 24건 · 수치 포함 131건 · 최대 418자.
# 가중치 최상위 항목이 전부 *다른 글의 제목·본문* 이었다:
#     w=5.00  "미국 환율보고서, 한국 2026년에도 환율 관찰대상국 재지정"
#     w=5.00  "미국 재무부가 반기 환율보고서에서 한국을 환율 관찰대상국으로 다시 지정했습니다…"
# 그리고 이 값들이 `build_insights_block()` 을 통해 **"반드시 적용"** 헤더를 달고
# 4조합 작성 프롬프트에 그대로 주입되고 있었다.
#
# ★ 뿌리 (비직관): `post_quality_analyzer.learn_from_suggestions` 가
#   `directive = s.get("after")` 를 저장했는데, 프롬프트상 `after` 의 정의가
#   *"본문에 그대로 들어갈 최종 완성 텍스트"* 다. **글에 넣을 문장을 지침이라며 저장**한 것.
#   → 제안 스키마에 `rule`(글에 독립적인 일반 지침)을 신설하고 그것만 학습한다.
#      여기 게이트는 그 뒤를 받치는 *안전망* 이다(upsert 호출자가 3곳이므로).
#
# ★ 왜 길이만으로는 못 막나: 실측한 오염분 중에는 33자짜리 *제목* 도 있었다.
#   짧고 HTML 도 없어 길이·태그 검사를 전부 통과한다. 그래서 **행동 지시문인가** 를 함께 본다
#   (한국어 지시 어미 — 특정 어휘 목록이 아니라 *문장의 꼴*).
try:
    from JARVIS02_WRITER.length_manager import KOREAN_PER_SENTENCE as _K
except Exception:
    _K = 50
# 지침은 '한 줄' 이다 — 2문장 분량을 상한으로 본다. 리터럴을 박지 않고 파생(원칙②).
DIRECTIVE_MAX_LEN: int = _K * 2

import re as _re
from functools import lru_cache as _lru_cache

import logging as _logging

# ★ 이 모듈에 로거가 없었다 — except 핸들러의 `log.warning` 이 NameError 를 내며
#   *예외를 감추는 대신 예외를 더 만들었다* (2026-08-07 실측 확인).
_log = _logging.getLogger("jarvis.guardian.quality")

# ★ 지시문 판정 — *어휘 목록이 아니라 문장의 꼴*. 그런데 **어디에 나오는지가 결정적**이다.
#   초판은 `유지하|포함하` 같은 어간을 문장 *아무 데서나* 찾았는데, 그러면
#   "두 종목 모두 흑자를 **유지하**며 안정적인 포지션을 이어가고 있어요" 같은
#   *본문 서술* 이 통과한다(실측으로 걸렸다). 한국어는 서술어가 **문장 끝** 에 오므로
#   명령·당위는 *끝에서* 판정해야 한다.
_IMPERATIVE_RE = _re.compile(
    r"(하라|해라|하자|하십시오|할\s*것|하지\s*말\s*것|말\s*것|"
    r"해야\s*한다|되어야\s*한다|돼야\s*한다|금지|필수|권장)\s*[.!]?\s*$"
)
_HTML_RE    = _re.compile(r"<[a-zA-Z/!]")
_FACTUAL_RE = _re.compile(r"(PER|ROE|EPS|\d+\.\d+|\d+\s*(%|배|원|억|조|달러|년|월|일))")


def directive_issues(directive: str) -> list[str]:
    """학습 지침으로 부적격인 사유 목록. 비어 있으면 적격.

    전부 *구조* 판정이다 — 금칙어 목록을 두지 않는다(목록은 반드시 낡는다).

    ★ 판정을 엄격하게 두는 이유(비대칭): 옳은 지침을 놓치면 *그 하나를 못 배울* 뿐이지만,
      틀린 지침을 들이면 **4조합 모든 글의 프롬프트를 오염** 시킨다. 손실 크기가 다르다.
    """
    d = (directive or "").strip()
    if not d:
        return ["빈 지침"]
    issues: list[str] = []
    if _HTML_RE.search(d):
        issues.append("HTML 태그 포함")
    if _FACTUAL_RE.search(d):
        issues.append("이 글의 수치·날짜 포함 — 다음 글에서 거짓이 된다")
    if len(d) > DIRECTIVE_MAX_LEN:
        # ★ 사유 문자열에 가변값(실제 길이)을 넣지 않는다 — 집계·중복제거가 깨진다.
        issues.append(f"{DIRECTIVE_MAX_LEN}자 초과 — 지침이 아니라 문단")
    if not _IMPERATIVE_RE.search(d):
        issues.append("행동 지시문이 아님 — 글의 제목·본문 조각으로 보임")
    return issues


def is_learnable_directive(directive: str) -> bool:
    """학습 자산으로 누적해도 되는 지침인가."""
    return not directive_issues(directive)

# ── 튜닝 상수 (단일 위치) ────────────────────────────────────────
UCB_C: float = 0.35           # 탐색 보너스 계수 (신규·저사용 인사이트 기회 부여)
REWARD_ALPHA: float = 0.3     # weight EMA 학습률

# ★ 보상 귀속 창 — **발행 스케줄에서 파생** (2026-08-03, 사용자 승인)
#   종전엔 `ATTRIBUTION_WINDOW_H = 18` 리터럴 + 주석 "07:00/21:00 발행 리듬 커버" 였다.
#   그 18 은 사실 *같은 글종류 재발행 간격(24h)의 3/4* 이었는데, 그 계산이 주석에만 있고
#   코드에 없었다 — 발행 시각을 바꾸면 주석이 조용히 거짓이 된다(오늘 keeper 에서 본 병).
#   판정 요건: ① 이 글이 분석될 때까지는 살아 있어야 하고(실측 지연 중앙 5분·최대 21분)
#             ② **같은 글종류의 다음 글이 나오기 전에** 닫혀야 한다(안 그러면 다음 회차 글에
#                잘못 귀속된다). → 재발행 간격의 3/4 이 두 요건을 동시에 만족한다.
_ATTRIB_SAFETY = 0.75         # 재발행 간격 대비 창 비율 (1.0 이면 다음 회차와 맞닿는다)
_ATTRIB_FALLBACK_H = 18       # 슬롯 파생 실패 시에만 (= 24h × 0.75, 종전 값과 동일)

# ★ 선택 대상 기간 — 이 기간이 지난 지침은 애초에 **다시 뽑히지 않는다**(SQL last_seen 필터).
#   종전엔 21 이 세 곳(build_insights_block · active_directives · db 기본값)에 흩어져 있었다.
SELECTION_DAYS: int = 21


def _same_type_republish_gap_h() -> int:
    """같은 글종류가 다시 발행되기까지의 **최소 간격(시간)** — 발행 슬롯에서 파생."""
    try:
        from JARVIS08_PUBLISH.publish_ledger import publish_slots

        by: dict[str, list[int]] = {}
        for pt, h, m in publish_slots():
            by.setdefault(pt, []).append(h * 60 + m)
        gaps = []
        for mins in by.values():
            mins.sort()
            n = len(mins)
            for i in range(n):
                g = (mins[(i + 1) % n] - mins[i]) % 1440 or 1440
                gaps.append(g)
        if gaps:
            return max(1, min(gaps) // 60)
    except Exception:
        pass
    return 24


def attribution_window_h() -> int:
    """사용→분석 매칭 최대 시간(h). 발행 시각을 옮기면 이 값이 따라온다."""
    try:
        return max(1, int(_same_type_republish_gap_h() * _ATTRIB_SAFETY))
    except Exception:
        return _ATTRIB_FALLBACK_H


ATTRIBUTION_WINDOW_H: int = attribution_window_h()   # 하위호환 — 기존 참조 그대로 동작
UNDERPERFORM_MIN_N: int = 5   # 저성과 판정 최소 보상 횟수
UNDERPERFORM_AVG: float = 0.35   # 평균 보상이 이 미만이면 가속 감쇠

# ★ 발행쌍 고정 — 네이버·티스토리가 *같은 지침 묶음* 을 받게 한다 (ERRORS [542]).
#   왜 필요한가 (두 가지가 동시에 고쳐진다):
#     ① 프롬프트 캐시 — 지침 블록은 작성 프롬프트의 *system* 안에 들어간다. 플랫폼마다
#        묶음이 달라지면 system 이 바이트 불일치 → prefix 캐시가 통째로 깨져 전량 재기록된다.
#        (실측: system 이 한 줄만 달라도 회수 0. 블록 내부 부분 회수는 없다.)
#     ② UCB 공정성 — 종전엔 첫 플랫폼 호출이 `record_insight_usage` 로 uses 를 올려,
#        *같은 글* 의 두 번째 플랫폼이 흔들린 랭킹을 받았다. 한 글에 대한 선택은 1회여야 한다.
#   ★ 사용 기록은 플랫폼별로 *그대로 2건* 남긴다 — 묶음만 고정하고 기록은 안 줄인다.
#     (`_match_analysis` 는 platform='' 이면 '가장 이른 글' 하나만 잡으므로, 1건으로 줄이면
#      두 번째 글의 채점 신호가 학습에서 통째로 사라진다.)
#   수명: 플랫폼 단위 직렬 발행 간격이 실측 12~13분이라 여유를 둔 값. 다음 발행 사이클
#     (경제 07:00 / 테마 21:00 — 최소 14시간)까지 절대 새지 않는다.
PAIR_PIN_TTL_MIN: int = 60

# key -> (pinned_at, picked_rows). 프로세스 지역 — 발행은 subprocess 라 곧 run 단위다.
_PAIR_PIN: dict[str, tuple] = {}


# ═══════════════════════════════════════════════════════════════
#  1. 선택 + 주입 (작성 시점)
# ═══════════════════════════════════════════════════════════════

def _ucb_rank(rows: list[dict], limit: int) -> list[dict]:
    """effective_weight + 탐색 보너스로 상위 limit 개 선택.

    score = effective_weight + UCB_C * sqrt(ln(1+total_uses) / (1+uses_i))
    → 사용 이력이 적은 인사이트도 주기적으로 시도돼 학습 기회를 얻음.
    """
    total_uses = sum(int(r.get("uses") or 0) for r in rows) + 1
    scored = []
    for r in rows:
        uses = int(r.get("uses") or 0)
        bonus = UCB_C * math.sqrt(math.log(1 + total_uses) / (1 + uses))
        scored.append((float(r.get("effective_weight") or 0) + bonus, r))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:limit]]


def _pinned_pick(scope: str, theme: str, limit: int, rows: list[dict]) -> list[dict]:
    """발행쌍(네이버→티스토리) 안에서는 *같은 묶음* 을 돌려준다 (위 PAIR_PIN_TTL_MIN 주석 참조).

    첫 호출만 UCB 를 돌리고, TTL 안의 후속 호출은 그 결과를 그대로 재사용한다.
    ★ 사용 기록(record_insight_usage)은 호출마다 그대로 남는다 — 여기서 줄이는 것은
      *선택* 뿐이지 *기록* 이 아니다.
    """
    import time as _t
    key = f"{scope}|{theme}|{limit}"
    now = _t.monotonic()          # 벽시계 아님 — 시스템 시각 변경에 흔들리지 않게
    hit = _PAIR_PIN.get(key)
    if hit and (now - hit[0]) < PAIR_PIN_TTL_MIN * 60:
        return hit[1]
    picked = _ucb_rank(rows, limit)
    if picked:
        _PAIR_PIN[key] = (now, picked)
    return picked


def active_directives(scope: str = "all", theme: str = "", limit: int = 8) -> list[str]:
    """이번 발행쌍에 **주입된 지침 문장** 목록 — *조회 전용*(사용 기록을 남기지 않는다).

    ★ 왜 필요한가 (2026-08-03 전수 감사 — 사용자 승인)
      학습된 지침이 프롬프트에 들어가는 것은 실측으로 확인됐다(08-03 21:00 배치 8/8 주입).
      그런데 **글이 그 지침을 지켰는지는 아무도 확인하지 않았다** — 실측: 08-03 네이버
      테마글(70점)이 주입된 8건 중 2건을 어겼고 *같은 지적이 다음 분석에서 또 나왔다*.
      넣어주기만 하고 검사하지 않으면 학습은 프롬프트를 길게 만들 뿐 글을 바꾸지 못한다.

    ★ 왜 상태를 배선하지 않는가
      작성 시점의 batch_id 를 draft 에 실어 게이트까지 나르는 방법도 있지만, 그러면
      *그 값을 안 넘기는 호출자* 가 생기는 순간 조용히 검사가 꺼진다(오늘만 두 번 본 병).
      대신 **같은 선택을 다시 묻는다** — `_pinned_pick` 이 `PAIR_PIN_TTL_MIN` 동안
      같은 묶음을 돌려주므로(발행쌍 고정), 작성기가 받은 것과 동일한 지침을 얻는다.
      선택 로직은 그대로 `quality_learner` 소유 — 게이트에 사본을 두지 않는다(원칙①).

    ※ `build_insights_block` 과 달리 `record_insight_usage` 를 부르지 않는다.
      검사 때문에 사용 기록이 두 배로 늘면 보상 통계가 오염된다.
    """
    try:
        from shared import db as _db

        _scope_filter = "" if scope in ("", "all") else scope
        rows = _db.get_ranked_learning_insights(scope=_scope_filter, limit=limit, days=SELECTION_DAYS)
        rows = [r for r in rows if not directive_issues(r.get("directive") or "")]
        if not rows:
            return []
        picked = _pinned_pick(scope, theme, limit, rows)
        return [(r.get("directive") or "").strip() for r in (picked or []) if (r.get("directive") or "").strip()]
    except Exception:
        return []


def pair_pin_effective(scope: str = "__selfcheck__") -> bool:
    """★ 고정이 *실제로 먹는지* 동작으로 확인 (저장소 표준 — 설치 플래그는 적용의 증거가 아니다).

    가짜 rows 로 두 번 뽑아 같은 묶음이 나오는지 본다. True 면 발행쌍 고정이 살아 있다.
    """
    fake = [{"id": i, "effective_weight": 1.0, "uses": i, "directive": f"d{i}"}
            for i in range(1, 6)]
    _PAIR_PIN.pop(f"{scope}||3", None)
    a = [r["id"] for r in _pinned_pick(scope, "", 3, fake)]
    # uses 를 흔들어도(=UCB 랭킹이 바뀔 조건) 고정이면 같은 묶음이어야 한다
    for r in fake:
        r["uses"] = 100 - r["id"]
    b = [r["id"] for r in _pinned_pick(scope, "", 3, fake)]
    _PAIR_PIN.pop(f"{scope}||3", None)
    return bool(a) and a == b


def build_insights_block(scope: str = "all", theme: str = "",
                         platform: str = "", limit: int = 8,
                         days: int = 0) -> str:
    """학습된 작성 지침 블록 생성 + 사용 기록 (보상 귀속 대기 등록).

    반환: 프롬프트 주입용 한국어 블록 문자열. 인사이트 없음/실패 시 "".
    """
    try:
        from shared import db as _db
        # scope='all' 은 SQL 필터에선 '전체'('') 를 의미해야 함 (필터 함정 방지 — 교차 리뷰)
        _scope_filter = "" if scope in ("", "all") else scope
        # days=0(기본) → 선택 기간 상수 상속. 0 을 그대로 SQL 에 넘기면 후보가 통째로 사라진다.
        rows = _db.get_ranked_learning_insights(
            scope=_scope_filter, limit=limit, days=(days or SELECTION_DAYS))
        # ★ 주입 직전 2차 방어 (2026-08-02). 저장 게이트가 있는데 왜 또 보는가 —
        #   오늘 실측으로 *면제·필터가 있어도 앞단이 무력화하면 그대로 샌다* 는 것을 두 번 봤다
        #   (watchdog 판정 순서 · engagement_judge 회로 면제). 프롬프트 주입은 4조합 모든 글에
        #   영향을 주는 마지막 관문이므로 여기서 한 번 더 거른다.
        #   규칙 본체는 `directive_issues` 하나 — 사본을 만들지 않는다(원칙①).
        rows = [r for r in rows if not directive_issues(r.get("directive") or "")]
        if not rows:
            return ""
        # ★ 발행쌍 고정 — platform 은 키에 넣지 않는다 (NV·TS 가 같은 묶음을 받게 하는 것이 목적).
        picked = _pinned_pick(scope, theme, limit, rows)
        if not picked:
            return ""

        lines = [
            "",
            "─" * 30,
            "📚 *과거 글 분석에서 도출된 작성 지침* — 이번 글 작성 시 반드시 적용:",
            "",
        ]
        used_ids = []
        for i, r in enumerate(picked, 1):
            d = (r.get("directive") or r.get("description") or "").strip()
            if not d:
                continue
            occ = r.get("occurrences", 1)
            rc = int(r.get("reward_count") or 0)
            avg = (float(r.get("reward_sum") or 0) / rc) if rc else None
            tag = f" (재발견 {occ}회" + (f" · 검증 보상 {avg:.2f}" if avg is not None else "") + ")"
            sc = r.get("scope") or "all"
            stag = "" if sc == "all" else f" [{sc}]"
            lines.append(f"{i}.{stag} {d}{tag}")
            used_ids.append(r["id"])

        if not used_ids:
            return ""

        # ★ 약점 항목 명시 주입 (2026-08-07 — 사용자 요구 "0점이 안 나오도록 학습")
        #   지침만으로는 부족했다. 지침은 *일반론* 이라 "이번에 어디서 몇 점을 잃고 있는지"
        #   를 알려주지 못한다. 실측 230건에서 **8개 항목이 전건 0점** 이었는데도 지침은
        #   그걸 한 번도 지목하지 못했다. 여기서 채점기의 실측을 그대로 들이민다.
        lines.append(_weak_items_block(scope, platform))

        # 사용 기록 — 보상 귀속 대기 (배치 = 이 글에 함께 주입된 묶음)
        # dry_run 은 발행이 없어 영원히 미귀속 노이즈 → 기록 스킵 (블록은 정상 반환)
        import os as _os
        if _os.environ.get("JARVIS_FORCE_SECTOR", "") != "dry_run":
            try:
                _db.record_insight_usage(
                    batch_id=uuid.uuid4().hex[:12],
                    insight_ids=used_ids,
                    scope=scope, platform=platform, theme=theme,
                )
            except Exception:
                pass  # 기록 실패해도 주입은 진행 (학습 1회 누락 < 글 품질)

        return "\n".join(lines)
    except Exception:
        return ""


WEAK_ITEM_LIMIT: int = 6      # 프롬프트에 실을 약점 개수 — 많으면 지시가 묽어진다
WEAK_MIN_SAMPLE: int = 5      # 이보다 표본이 적으면 약점을 말하지 않는다(소표본 오판)


def weak_items(scope: str = "", platform: str = "", days: int = 30,
               limit: int = 0) -> list:
    """최근 글에서 **가장 많이 잃고 있는 항목** — 저장된 채점 실측에서 파생.

    ★ 목록을 코드에 박지 않는다 (② 동적 설계). `post_analysis.rubric_items` 를 집계해
      *지금* 무엇을 잃고 있는지 매번 다시 구한다. 어떤 항목을 고쳐 손실이 사라지면
      그 항목은 **자동으로 목록에서 빠지고** 다음 약점이 올라온다 — 100점 수렴의 동력이다.

    Returns: [{"key","name","avg","max","zero_rate","loss","n"}]  손실 큰 순
    """
    try:
        import json as _json
        from shared.db import get_db
        from JARVIS02_WRITER.post_scorer import RUBRIC_MAX, item_index

        q = ("SELECT rubric_items FROM post_analysis WHERE rubric_items IS NOT NULL "
             "AND created_at > datetime('now', ?)")
        args: list = [f"-{int(days)} day"]
        if scope and scope != "all":
            q += " AND post_type = ?"
            args.append(scope)
        if platform:
            q += " AND platform = ?"
            args.append(platform)
        agg: dict = {}
        with get_db() as con:
            for (raw,) in con.execute(q, tuple(args)):
                try:
                    v = _json.loads(raw)
                except Exception:
                    continue
                for k, sc in v.items():
                    agg.setdefault(k, []).append(float(sc))
        idx = item_index()
        out = []
        for k, vals in agg.items():
            mx = float(RUBRIC_MAX.get(k, 0))
            if not mx or len(vals) < WEAK_MIN_SAMPLE:
                continue
            loss = mx * len(vals) - sum(vals)
            if loss <= 0:
                continue                     # 전건 만점 — 약점이 아니다
            out.append({
                "key": k, "name": (idx.get(k) or {}).get("name", k),
                "avg": round(sum(vals) / len(vals), 2), "max": mx,
                "zero_rate": round(sum(1 for x in vals if x == 0) / len(vals), 2),
                "loss": round(loss, 1), "n": len(vals),
            })
        out.sort(key=lambda d: -d["loss"])
        return out[: (limit or WEAK_ITEM_LIMIT)]
    except Exception as e:
        _log.warning(f"[quality_learner] 약점 항목 파생 실패: {type(e).__name__}: {e}")
        return []


def _weak_items_block(scope: str = "", platform: str = "") -> str:
    """약점 항목을 작성 프롬프트용 한국어 블록으로. 없으면 "".

    ★ 점수·항목key 를 그대로 노출하지 않는다 — 작성 LLM 에게 필요한 것은
      *무엇을 어떻게 해야 하는가* 이지 내부 채점 코드가 아니다. 다만 **0점 항목은
      0점이라고 분명히 말한다** — 그게 이 블록의 존재 이유다.
    """
    items = weak_items(scope=scope, platform=platform)
    if not items:
        return ""
    lines = ["",
             "⚠️ *최근 같은 종류 글에서 실제로 점수를 잃은 지점* — 이번 글에서는 반드시 확보:"]
    for d in items:
        if d["zero_rate"] >= 0.9:
            how = f"**최근 {int(d['zero_rate']*100)}% 의 글에서 0점** — 이번엔 반드시 채울 것"
        elif d["avg"] < d["max"] * 0.5:
            how = f"평균 {d['avg']}/{d['max']:.0f} 로 절반 미만 — 우선 보강"
        else:
            how = f"평균 {d['avg']}/{d['max']:.0f} — 만점까지 조금 남음"
        lines.append(f"- {d['name']}: {how}")
    return "\n".join(lines)


def maintained_items(scope: str = "", platform: str = "", days: int = 30) -> list:
    """**만점을 유지 중인 항목** — 퇴행 감시 대상 (2026-08-07).

    ★ 왜 필요한가: `deducted_items()` 는 감점된 것만 돌려주므로 만점 항목은 시스템
      눈에 보이지 않았다. "만점이던 게 떨어졌다" 를 감지할 수단이 없어, 실측으로
      퇴행 45건이 개선 27건보다 많은데도 아무도 몰랐다. *유지* 도 학습 대상이다.
    """
    try:
        import json as _json
        from shared.db import get_db
        from JARVIS02_WRITER.post_scorer import RUBRIC_MAX, item_index
        q = ("SELECT rubric_items FROM post_analysis WHERE rubric_items IS NOT NULL "
             "AND created_at > datetime('now', ?)")
        args: list = [f"-{int(days)} day"]
        if scope and scope != "all":
            q += " AND post_type = ?"; args.append(scope)
        if platform:
            q += " AND platform = ?"; args.append(platform)
        agg: dict = {}
        with get_db() as con:
            for (raw,) in con.execute(q, tuple(args)):
                try:
                    v = _json.loads(raw)
                except Exception:
                    continue
                for k, sc in v.items():
                    agg.setdefault(k, []).append(float(sc))
        idx = item_index()
        out = []
        for k, vals in agg.items():
            mx = float(RUBRIC_MAX.get(k, 0))
            if not mx or len(vals) < WEAK_MIN_SAMPLE:
                continue
            if all(x >= mx for x in vals):
                out.append({"key": k, "name": (idx.get(k) or {}).get("name", k),
                            "max": mx, "n": len(vals)})
        out.sort(key=lambda d: -d["max"])
        return out
    except Exception as e:
        _log.warning(f"[quality_learner] 유지 항목 파생 실패: {type(e).__name__}: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
#  2. 보상 계산 + 귀속 (분석 이후)
# ═══════════════════════════════════════════════════════════════

def _reward_from_analysis(row: dict) -> Optional[float]:
    """분석 결과 → 보상 [0, 1]. **발행글의 실제 100점 루브릭 총점을 그대로 쓴다.**

    reward = quality_score / 100
    (발행 전 차단 게이트와 *같은 채점표*(post_scorer) — 100점=1.0, 감점 클수록 낮음.
     비례·무포화. score_post 가 4조합을 단일 진입점으로 커버.)

    None 반환 = 점수 미기록(옛 행·채점 불가) → 보상 신호 없음 → 귀속 스킵(weight 불변).
    """
    sc = row.get("quality_score")
    if sc is None:
        return None
    try:
        return round(max(0.0, min(1.0, float(sc) / 100.0)), 4)
    except (TypeError, ValueError):
        return None


def _match_analysis(usage: dict, analyses: list[dict]) -> Optional[dict]:
    """사용 기록 1건 ↔ 분석된 글 매칭.

    조건: ① scope == post_type (usage.scope='all' 이면 전부 허용)
          ② platform 일치 (usage.platform='' 이면 양쪽 허용)
          ③ 글 생성 시각이 used_at 이후 ATTRIBUTION_WINDOW_H 이내
    복수 매칭 시 가장 이른 글 (같은 발행 사이클).
    """
    from datetime import datetime, timedelta
    try:
        used_at = datetime.strptime(usage["used_at"][:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
    window_end = used_at + timedelta(hours=ATTRIBUTION_WINDOW_H)

    best = None
    for a in analyses:
        ptype = (a.get("post_type") or "").strip()
        if usage.get("scope") not in ("", "all") and ptype and ptype != usage["scope"]:
            continue
        if usage.get("platform") and a.get("platform") != usage["platform"]:
            continue
        try:
            created = datetime.strptime((a.get("created_at") or "")[:19],
                                        "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if not (used_at <= created <= window_end):
            continue
        if best is None or created < best[0]:
            best = (created, a)
    return best[1] if best else None


# 위반 감점 폭 — 중립점 아래로 확실히 떨어지되 과하지 않게. 상수 대신 EMA 계수에서 파생.
#   REWARD_ALPHA 가 갱신 보폭이므로 그 절반이면 한 회차에 방향이 뒤집힌다.
_VIOLATION_PENALTY = 0.15


def record_directive_violations(scope: str, platform: str, theme: str,
                                violated_texts: list) -> int:
    """게이트가 판정한 **미준수 지침** 을 사용 기록에 남긴다 (2026-08-07 감사).

    ★ 왜 필요한가 — credit assignment 가 붕괴해 있었다
      한 배치의 지침 8개가 전부 같은 보상(그 글의 점수)을 받으면, 어느 지침이 좋았는지
      영영 구분되지 않는다(실측: 배치 53개 전부 `count(distinct reward)=1`).
      그런데 `prepublish_gate` 는 이미 *어느 지침이 안 지켜졌는지* 를 계산해 놓고
      `log.info` 한 줄로 버리고 있었다. 계산은 이미 끝났고 흘려보내기만 하면 된다.

    ★ 왜 여기인가 (①) — 지침 텍스트를 id 로 되돌리는 건 지침의 주인만 할 수 있다.
      게이트는 텍스트만 알고, 이 모듈은 그 텍스트가 어느 행에서 나왔는지 안다.
    """
    if not violated_texts:
        return 0
    try:
        from shared import db as _db
        batch = _db.latest_batch(scope or "all", platform or "")
        if not batch:
            return 0
        # 텍스트 → id : 활성 지침 조회와 **같은 원본**에서 뽑는다(사본 금지)
        # ★ 함수명 교정 (2026-08-07) — `get_learning_insights` 는 **존재하지 않는다**.
        #   어제 이 코드를 넣으면서 실제로 부르지 않고 커밋했고, 골든 테스트도
        #   *소스 문자열만* 검사해 초록으로 통과시켰다. 902행 중 violated 는 0행이었다.
        #   "정적 검사는 코드가 어떻게 생겼나만 답한다" 를 그 자리에서 어겼다.
        rows = _db.get_ranked_learning_insights(scope=scope or "all", limit=200) or []
        want = {str(t).strip()[:200] for t in violated_texts if str(t).strip()}
        ids = [r["id"] for r in rows
               if str(r.get("directive") or "").strip()[:200] in want]
        return _db.mark_usage_violated(batch, ids)
    except Exception as e:
        _log.warning(f"[quality_learner] 지침 위반 기록 실패: {type(e).__name__}: {e}")
        return 0


@_lru_cache(maxsize=1)
def _item_name_index() -> list:
    """루브릭 **항목명 → 항목key** 색인 — 긴 이름부터 (부분일치 오탐 방지).

    ★ 이 표를 손으로 적지 않는다 (② 동적 설계). `post_scorer.item_index()` 가
      채점기 자신에서 파생한 것을 그대로 뒤집는다 — 항목이 추가·개명되면 **자동 추종**한다.
    """
    try:
        from JARVIS02_WRITER.post_scorer import item_index
        pairs = [(v.get("name") or "", k) for k, v in item_index().items()]
    except Exception:
        return []
    return sorted([(n, k) for n, k in pairs if n], key=lambda x: -len(x[0]))


def insight_target_item(insight_key: str) -> "str | None":
    """지침이 **겨누는 루브릭 항목** — `insight_key` 에서 파생. 없으면 None.

    ★ 왜 파생이 가능한가 (비직관)
      `insight_key` 는 `"{scope}:{type}_{감점항목명}"` 꼴이고, 그 항목명은 발행 후 분석
      프롬프트가 **"감점 목록의 이름 그대로 사용"** 하라고 지시해 만들어진 것이다.
      즉 지침↔항목 연결은 *데이터에 이미 있었는데* 코드가 그걸 읽지 않았을 뿐이다.
      실측: 가중치 상위 10개 전부(10/10) 이 방식으로 항목이 역추적된다.

    이 연결이 없으면 배치 안 8개 지침이 **전부 같은 총점 보상**을 받는다 —
    실측으로 보상 배치 53개 전부 `distinct reward = 1` 이었다(신용할당 붕괴).
    """
    if not insight_key:
        return None
    for name, key in _item_name_index():
        if name in insight_key:
            return key
    return None


def item_reward(item_key: str, rubric_items: dict) -> "float | None":
    """그 항목의 획득률 `점수/만점` → 보상 [0,1]. 채점 안 된 항목이면 None.

    만점은 `RUBRIC_MAX`(단일 진실 소스)에서 파생한다 — 저장된 값을 믿지 않는다.
    """
    if not item_key or not rubric_items or item_key not in rubric_items:
        return None
    try:
        from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
        mx = float(RUBRIC_MAX.get(item_key, 0))
        if not mx:
            return None
        return round(max(0.0, min(1.0, float(rubric_items[item_key]) / mx)), 4)
    except Exception:
        return None


def item_reward_neutral(item_key: str, days: int = 60) -> "float | None":
    """**항목별** 중립점 — 그 항목 획득률의 최근 중앙값. 표본 부족(<8)이면 None.

    ★ 왜 항목마다 따로 두는가
      항목은 난이도가 제각각이다. `B5_factuality` 는 230건 전부 만점(획득률 1.0)이고
      `T7_meta_desc` 는 전건 0점이었다. 전역 중립점(총점 중앙값 ≈0.69) 하나로 재면
      쉬운 항목을 겨눈 지침은 **가만히 있어도** 계속 오르고, 어려운 항목을 겨눈 지침은
      **아무리 개선해도** 계속 내린다 — 학습이 난이도를 실력으로 착각한다.
      항목별 중앙값을 쓰면 "이 항목에서 평균보다 나았는가" 라는 옳은 질문이 된다.
    """
    if not item_key:
        return None
    try:
        import json as _json
        import statistics as _st
        from shared.db import get_db
        from JARVIS02_WRITER.post_scorer import RUBRIC_MAX
        mx = float(RUBRIC_MAX.get(item_key, 0))
        if not mx:
            return None
        vals = []
        with get_db() as con:
            for (raw,) in con.execute(
                "SELECT rubric_items FROM post_analysis "
                "WHERE rubric_items IS NOT NULL AND created_at > datetime('now', ?)",
                (f"-{int(days)} day",),
            ):
                try:
                    v = _json.loads(raw)
                except Exception:
                    continue
                if item_key in v:
                    vals.append(max(0.0, min(1.0, float(v[item_key]) / mx)))
        if len(vals) < 8:
            return None
        return round(max(0.05, min(0.95, _st.median(vals))), 4)
    except Exception:
        return None


def reward_neutral(days: int = 60) -> float:
    """보상의 **중립점** — 이 값보다 높으면 weight ↑, 낮으면 ↓.

    ★ 왜 0.5 가 아닌가 (2026-08-07 감사 — 강화학습이 인기투표가 돼 있었다)
      갱신식은 `w ← w + α(reward − 중립)` 이고 `reward = quality_score/100` 이다.
      그런데 실측 점수 분포는 **59~77, 중앙값 69, 50점 미만 0건** 이다.
      중립을 0.5 로 두면 `Δw` 최솟값이 **+0.027 — 항상 양수** 다.
      즉 "검증된 지침만 생존" 이 아니라 **"쓰인 지침은 전부 생존"** 이었다.
      하향이 구조적으로 도달 불가능하면 그건 학습이 아니라 최근성 가중 인기투표다.

    ★ 왜 68.5 를 박지 않는가 (② 동적 설계)
      점수 분포는 글이 좋아지면 통째로 올라간다. 중립점을 박으면 그 순간부터 또
      전부 양수가 된다. **중앙값을 런타임에 계산** 하면 "평균보다 나은 지침" 이라는
      상대 기준이 유지된다 — 분포가 올라가도 절반은 내려간다.

    표본이 부족하면(<8) 0.5 로 폴백 — 소표본 중앙값은 튀어서 오히려 해롭다.
    """
    try:
        from shared.db import get_db
        with get_db() as con:
            rows = [float(r[0]) for r in con.execute(
                "SELECT quality_score FROM post_analysis "
                "WHERE quality_score IS NOT NULL "
                "  AND created_at > datetime('now', ?) ORDER BY quality_score",
                (f"-{int(days)} day",)) if r[0] is not None]
        if len(rows) < 8:
            return 0.5
        import statistics as _st
        return round(max(0.05, min(0.95, _st.median(rows) / 100.0)), 4)
    except Exception:
        return 0.5


def reward_retry_days() -> int:
    """미귀속 사용 기록을 **며칠까지 다시 시도할 것인가** — 선택 기간에서 파생.

    ★ 왜 3 이 아니라 파생인가 (2026-08-03, 사용자 승인)
      종전엔 `attribute_pending_rewards(days=3)` 로 3 을 박아 불렀다. 그 3 은
      "채점이 3일 안에 끝난다" 는 *가정을 코드에 복사* 해 둔 것인데, 실측은 그 가정이
      깨졌음을 보여준다 — `insight_usage` 694건 중 보상 귀속은 **170건(24.5%)** 뿐이고
      나머지는 채점이 끝내 오지 않아 **영구 사장**됐다(사용자 휴가 3일 등 외부 요인 포함).

      재시도의 값은 *그 지침이 아직 선택 대상일 때* 까지다 — `SELECTION_DAYS` 가 지나면
      그 지침은 어차피 다시 뽑히지 않으므로 보상을 붙여도 다음 글에 영향이 없다.
      그래서 재시도 창 = 선택 기간. 상한이 스스로 설명된다.
    """
    return max(1, int(SELECTION_DAYS))


def _insight_key_of(insight_id) -> str:
    """insight_id → insight_key. 한 회차 안에서 반복 조회되므로 프로세스 캐시를 둔다."""
    if insight_id is None:
        return ""
    try:
        iid = int(insight_id)
    except (TypeError, ValueError):
        return ""
    if iid in _IKEY_CACHE:
        return _IKEY_CACHE[iid]
    try:
        from shared.db import get_db
        with get_db() as con:
            row = con.execute("SELECT insight_key FROM learning_insights WHERE id=?",
                              (iid,)).fetchone()
        val = (row["insight_key"] if row else "") or ""
    except Exception:
        val = ""
    _IKEY_CACHE[iid] = val
    return val


def _rubric_items_of(analysis_row: dict) -> dict:
    """분석 행의 `rubric_items` 를 dict 로. 없거나 깨졌으면 빈 dict."""
    raw = (analysis_row or {}).get("rubric_items")
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        import json as _json
        v = _json.loads(raw)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


_IKEY_CACHE: dict = {}


def attribute_pending_rewards(days: int | None = None) -> dict:
    """미귀속 사용 기록 전수 → 분석 결과 매칭 → 보상 귀속 + weight 갱신.

    Returns: {"matched": n, "pending": n, "avg_reward": f}
    """
    # ★ 중립점을 루프 밖에서 1회 파생 (2026-08-07) — 매 행마다 SQL 을 돌리지 않는다.
    _neutral = reward_neutral()
    from shared import db as _db

    days = reward_retry_days() if days is None else int(days)
    usages = _db.get_unrewarded_usage(days=days)
    if not usages:
        return {"matched": 0, "pending": 0, "avg_reward": None}

    # 분석 완료된 글 (채점된 것) — 최근 days+1일. quality_score = 보상 신호(루브릭 총점).
    with _db.get_db() as conn:
        analyses = [dict(r) for r in conn.execute(
            """SELECT id, platform, theme, post_type, quality_score, created_at, analyzed_at,
                      rubric_items
               FROM post_analysis
               WHERE analyzed_at IS NOT NULL
                 AND created_at >= datetime('now','localtime',?)""",
            (f"-{int(days) + 1} day",),
        ).fetchall()]

    # ★ 중복 보상 방지 (교차 리뷰): 재작성 순환 등으로 같은 글에 여러 배치가
    #   주입될 수 있음 — (insight_id, analysis_id) 쌍당 weight 갱신은 1회만.
    with _db.get_db() as conn:
        rewarded_pairs = {
            (r[0], r[1]) for r in conn.execute(
                "SELECT insight_id, analysis_id FROM insight_usage "
                "WHERE reward IS NOT NULL AND analysis_id IS NOT NULL"
            ).fetchall()
        }

    matched, rewards = 0, []
    for u in usages:
        a = _match_analysis(u, analyses)
        if a is None:
            continue
        # ★ 항목별 신용할당 (2026-08-07) — 지침이 겨눈 **그 항목의 획득률** 을 보상으로 쓴다.
        #   종전엔 배치 안 모든 지침이 같은 총점을 받아 실측 53개 배치 전부
        #   distinct reward = 1 이었다 — 어느 지침이 기여했는지 구분이 0이었다.
        #   항목이 역추적 안 되거나(일반 지침) 그 항목이 채점 안 됐으면 총점으로 폴백한다.
        _ikey = _insight_key_of(u["insight_id"])
        _item = insight_target_item(_ikey)
        _ri = _rubric_items_of(a)
        r = item_reward(_item, _ri)
        _n = item_reward_neutral(_item) if r is not None else None
        if r is None:
            r = _reward_from_analysis(a)
        if _n is None:
            _n = _neutral
        if r is None:
            continue   # 점수 미기록(옛 행·채점 불가) → 보상 신호 없음, 귀속 스킵
        pair = (u["insight_id"], a["id"])
        try:
            _db.apply_insight_reward(
                usage_id=u["id"], insight_id=u["insight_id"],
                analysis_id=a["id"], alpha=REWARD_ALPHA,
                update_weight=(pair not in rewarded_pairs),
                # ★ 지침별 변별 (2026-08-07) — 같은 글이라도 안 지켜진 지침은 감점.
                #   이게 없으면 배치 안 8개가 전부 같은 값이라 학습 신호가 0이다.
                reward=(r if not u.get("violated") else max(0.0, r - _VIOLATION_PENALTY)),
                neutral=_n,           # ★ 항목별 중앙값(없으면 전역) — 하향이 가능해진다
            )
            rewarded_pairs.add(pair)
            matched += 1
            rewards.append(r)
        except Exception:
            continue

    return {
        "matched": matched,
        "pending": len(usages) - matched,
        "avg_reward": round(sum(rewards) / len(rewards), 4) if rewards else None,
    }


def _decay_underperformers() -> int:
    """검증 결과 저성과 인사이트 가속 감쇠 — 다음 선택에서 자연 탈락 유도.

    (완전 삭제는 기존 decay_learning_insights 가 weight<0.05 에서 수행)
    """
    from shared import db as _db
    with _db.get_db() as conn:
        cur = conn.execute(
            """UPDATE learning_insights
               SET weight = weight * 0.5
               WHERE COALESCE(reward_count, 0) >= ?
                 AND COALESCE(reward_sum, 0) / reward_count < ?
                 AND weight > 0.05""",
            (UNDERPERFORM_MIN_N, UNDERPERFORM_AVG),
        )
        return cur.rowcount or 0


# ═══════════════════════════════════════════════════════════════
#  3. 스케줄 잡 + 상태
# ═══════════════════════════════════════════════════════════════

def job_quality_learn() -> None:
    """매일 23:45 — 보상 귀속 + 저성과 감쇠 + 요약 알림 (DEFAULT_JOBS callback)."""
    try:
        res = attribute_pending_rewards()   # 창은 reward_retry_days() 가 파생 — 여기 박지 않는다
        n_decay = _decay_underperformers()
        if res["matched"] == 0 and n_decay == 0:
            return  # 조용히 패스 (신호 없음)
        s = stats()
        msg = (
            "🧠 *글 품질 강화학습 일일 갱신*\n"
            f"보상 귀속 {res['matched']}건"
            + (f" (평균 {res['avg_reward']:.2f})" if res.get("avg_reward") is not None else "")
            + (f" · 미매칭 {res['pending']}건" if res.get("pending") else "")
            + (f"\n저성과 감쇠 {n_decay}건" if n_decay else "")
            + f"\n활성 인사이트 {s.get('active', 0)}개 · 누적 검증 {s.get('total_rewards', 0)}회"
        )
        try:
            from shared.notify import send_tg
            send_tg(msg)
        except Exception:
            pass
    except Exception as e:
        try:
            from JARVIS07_GUARDIAN.error_collector import report
            report(e, "guardian", module=__name__, func_name="job_quality_learn")
        except Exception:
            pass


def stats() -> dict:
    """학습 현황 — hub 카드·/status 용."""
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            row = conn.execute(
                """SELECT COUNT(*)                            AS active,
                          COALESCE(SUM(reward_count), 0)      AS total_rewards,
                          ROUND(AVG(CASE WHEN reward_count > 0
                                THEN reward_sum / reward_count END), 3) AS avg_reward
                   FROM learning_insights WHERE weight >= 0.05""",
            ).fetchone()
            used = conn.execute("SELECT COUNT(*) FROM insight_usage").fetchone()[0]
        return {
            "active": row["active"], "total_rewards": row["total_rewards"],
            "avg_reward": row["avg_reward"], "total_usage": used,
        }
    except Exception:
        return {"active": 0, "total_rewards": 0, "avg_reward": None, "total_usage": 0}
