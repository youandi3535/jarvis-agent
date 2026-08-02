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
ATTRIBUTION_WINDOW_H: int = 18   # 사용→분석 매칭 최대 시간 (h) — 07:00/21:00 발행 리듬 커버
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
                         days: int = 21) -> str:
    """학습된 작성 지침 블록 생성 + 사용 기록 (보상 귀속 대기 등록).

    반환: 프롬프트 주입용 한국어 블록 문자열. 인사이트 없음/실패 시 "".
    """
    try:
        from shared import db as _db
        # scope='all' 은 SQL 필터에선 '전체'('') 를 의미해야 함 (필터 함정 방지 — 교차 리뷰)
        _scope_filter = "" if scope in ("", "all") else scope
        rows = _db.get_ranked_learning_insights(scope=_scope_filter, limit=limit, days=days)
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


def attribute_pending_rewards(days: int = 3) -> dict:
    """미귀속 사용 기록 전수 → 분석 결과 매칭 → 보상 귀속 + weight 갱신.

    Returns: {"matched": n, "pending": n, "avg_reward": f}
    """
    from shared import db as _db

    usages = _db.get_unrewarded_usage(days=days)
    if not usages:
        return {"matched": 0, "pending": 0, "avg_reward": None}

    # 분석 완료된 글 (채점된 것) — 최근 days+1일. quality_score = 보상 신호(루브릭 총점).
    with _db.get_db() as conn:
        analyses = [dict(r) for r in conn.execute(
            """SELECT id, platform, theme, post_type, quality_score, created_at, analyzed_at
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
        r = _reward_from_analysis(a)
        if r is None:
            continue   # 점수 미기록(옛 행·채점 불가) → 보상 신호 없음, 귀속 스킵
        pair = (u["insight_id"], a["id"])
        try:
            _db.apply_insight_reward(
                usage_id=u["id"], insight_id=u["insight_id"],
                analysis_id=a["id"], reward=r, alpha=REWARD_ALPHA,
                update_weight=(pair not in rewarded_pairs),
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
        res = attribute_pending_rewards(days=3)
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
