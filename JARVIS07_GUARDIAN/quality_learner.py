"""JARVIS07_GUARDIAN/quality_learner.py — ★ 글 품질 강화학습 단일 진입점 (ADR 014).

오류 강화학습(bandit.py)과 대칭 구조의 *글 품질* 폐쇄 루프:

    [작성] build_insights_block() — UCB 랭킹으로 인사이트 선택 + 주입 + 사용 기록
       ↓ (발행 → post_quality_analyzer 분석 → post_analysis.suggestions)
    [보상] job_quality_learn() — 사용 기록 ↔ 분석 결과 매칭 → 보상 계산
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

import json
import math
import uuid
from typing import Optional

__all__ = [
    "build_insights_block",
    "attribute_pending_rewards",
    "job_quality_learn",
    "job_quality_learn_daytime",
    "stats",
]

# ── 튜닝 상수 (단일 위치) ────────────────────────────────────────
UCB_C: float = 0.35           # 탐색 보너스 계수 (신규·저사용 인사이트 기회 부여)
REWARD_ALPHA: float = 0.3     # weight EMA 학습률
ATTRIBUTION_WINDOW_H: int = 18   # 사용→분석 매칭 최대 시간 (h) — 06:30/16:00 발행 리듬 커버
UNDERPERFORM_MIN_N: int = 5   # 저성과 판정 최소 보상 횟수
UNDERPERFORM_AVG: float = 0.35   # 평균 보상이 이 미만이면 가속 감쇠

# suggestion priority → 페널티 (post_quality_analyzer 의 high/medium/low)
_PRIORITY_PENALTY = {"high": 0.25, "medium": 0.12, "low": 0.05}


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
        if not rows:
            return ""
        picked = _ucb_rank(rows, limit)
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

def _reward_from_analysis(row: dict) -> float:
    """분석 결과 → 보상 [0, 1]. 개선 제안이 적고 가벼울수록 좋은 글.

    reward = 1 - min(1, Σ priority_penalty)
    (v2 후보: judge_engagement 점수·조회수 percentile 합성 — 현재는 결정론만)
    """
    try:
        sugs = json.loads(row.get("suggestions") or "[]")
    except Exception:
        sugs = []
    penalty = 0.0
    for s in sugs:
        if isinstance(s, dict):
            penalty += _PRIORITY_PENALTY.get(str(s.get("priority", "low")).lower(), 0.05)
    return round(max(0.0, 1.0 - min(1.0, penalty)), 4)


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

    # 분석 완료된 글 (suggestions 채워진 것) — 최근 days+1일
    with _db.get_db() as conn:
        analyses = [dict(r) for r in conn.execute(
            """SELECT id, platform, theme, post_type, suggestions, created_at, analyzed_at
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


def job_quality_learn_daytime() -> None:
    """07:30·13:30·19:30 중간 실행 — 보상 귀속만. 저성과 감쇠는 23:45(job_quality_learn)만."""
    try:
        res = attribute_pending_rewards(days=3)
        if res["matched"] == 0:
            return
        try:
            from shared.notify import send_tg
            send_tg(
                f"🧠 글 품질 학습 중간 귀속 {res['matched']}건"
                + (f" (평균 {res['avg_reward']:.2f})" if res.get("avg_reward") is not None else "")
            )
        except Exception:
            pass
    except Exception as e:
        try:
            from JARVIS07_GUARDIAN.error_collector import report
            report(e, "guardian", module=__name__, func_name="job_quality_learn_daytime")
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
