"""
JARVIS03 RADAR — 자가학습 두뇌

다섯 갭을 메우는 학습 루프:
 1. 가중치 자동 회귀 학습  → train_weights()    (sklearn Ridge)
 2. 부정 신호 페널티       → apply_negative_signal()  (analyzer 가 호출)
 3. 사용자 승인/거부 피드백 → update_feedback_from_events()
 4. 키워드 임베딩 cold-start → get_cold_start_boost()
 5. 자동 백테스트          → run_backtest()

매일·주별 cron 으로 실행되며, opportunity_score() 가 학습 가중치를 매번 읽어서 적용.
시간 지날수록 모델이 데이터에 자동 보정됨.
"""
from __future__ import annotations

import os
import sys
import json
import math
import sqlite3
from pathlib import Path
from typing import Optional

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from shared import db as _db


# ─────────────────────────────────────────────────────────────
# 1. 학습 데이터 적재 — 매일 23:30 (performance 수집 직후)
# ─────────────────────────────────────────────────────────────

def _theme_match_keys(source_keyword: str, theme: str) -> list:
    """theme/source_keyword 를 trends.keyword 와 매칭할 후보 키 리스트.

    *우선순위*:
        1. source_keyword 정확
        2. theme 정확
        3. theme 정규화 (괄호 제거 후 strip)
        4. theme 첫 토큰 (괄호 제거 + split)
        5. theme 첫 토큰 split('/') 1번째
    """
    import re as _re
    keys = []
    sk = (source_keyword or '').strip()
    th = (theme or '').strip()
    if sk:
        keys.append(sk)
    if th and th != sk:
        keys.append(th)
    # 괄호 제거
    if th:
        norm = _re.sub(r'\([^)]*\)', '', th).strip()
        if norm and norm != th and norm not in keys:
            keys.append(norm)
        # 첫 토큰 (공백)
        head = norm.split()[0] if norm else ''
        if head and head not in keys:
            keys.append(head)
        # 첫 토큰 / 분리 (예: "카메라모듈/부품" → "카메라모듈")
        if head and '/' in head:
            sub = head.split('/')[0]
            if sub and sub not in keys:
                keys.append(sub)
    return keys


def log_predictions_vs_actual(verbose: bool = False) -> dict:
    """
    post_analysis(current_views) × keyword_performance × trends 를 조인해
    (예측 feature, 실측 views) 페어를 learn_log 에 적재.

    days_after = (오늘 - 발행일) 로 계산.
    같은 글에 대해 여러 시점(예: 1일/7일/30일) 조회수가 다르므로 days_after 분리.

    매칭 로직 (다층 fallback):
        1. source_keyword 정확
        2. theme 정확
        3. theme 정규화 (괄호 제거)
        4. theme 첫 토큰
        5. 모두 실패 시 LIKE 부분 매칭
    """
    n_logged = 0
    n_skip_no_views = 0
    n_skip_no_match = 0
    n_skip_days = 0
    n_signal_views = 0
    n_signal_rank  = 0
    with _db.get_db() as conn:
        # ★ 학습 신호 *2종*: actual_views > 0 OR naver_rank IS NOT NULL.
        # 네이버 조회수 패턴 매칭 실패로 views 신호 부족하지만
        # naver_rank (검색 노출 순위) 가 더 깨끗한 학습 신호.
        rows = conn.execute(
            """SELECT pa.id, pa.platform, pa.theme, pa.source_keyword,
                      pa.created_at, pa.current_views, pa.naver_rank,
                      julianday('now', 'localtime') - julianday(pa.created_at) AS days_after
               FROM post_analysis pa
               WHERE pa.created_at IS NOT NULL
                 AND ( (pa.current_views IS NOT NULL AND pa.current_views > 0)
                       OR (pa.naver_rank IS NOT NULL) )"""
        ).fetchall()

        for r in rows:
            theme    = (r["theme"] or "").strip()
            source_keyword = (r["source_keyword"] or "").strip()
            if not theme and not source_keyword:
                continue
            days_after = int(round(r["days_after"] or 0))
            # naver_rank 는 발행 *당일도* 측정 가능 → days_after=0 도 허용
            # views 는 누적이라 days_after >= 1 권장
            cv = int(r["current_views"] or 0)
            rk = r["naver_rank"]
            if days_after < 0 or days_after > 90:
                n_skip_days += 1
                continue
            if days_after == 0 and rk is None and cv == 0:
                n_skip_days += 1
                continue

            # 다층 fallback 매칭 (정확 → 정규화 → 토큰 → LIKE)
            t = None
            match_kind = None
            match_key  = None
            for cand in _theme_match_keys(source_keyword, theme):
                t = conn.execute(
                    """SELECT score, opportunity_score, sector FROM trends
                       WHERE keyword = ? AND date <= date(?)
                       ORDER BY date DESC LIMIT 1""",
                    (cand, r["created_at"]),
                ).fetchone()
                if t:
                    match_kind = "exact"; match_key = cand; break
            if not t:
                # 마지막 fallback — LIKE 부분 매칭
                for cand in _theme_match_keys(source_keyword, theme):
                    t = conn.execute(
                        """SELECT score, opportunity_score, sector FROM trends
                           WHERE (keyword LIKE ? OR ? LIKE '%' || keyword || '%')
                             AND date <= date(?)
                           ORDER BY date DESC LIMIT 1""",
                        (f"%{cand}%", cand, r["created_at"]),
                    ).fetchone()
                    if t:
                        match_kind = "like"; match_key = cand; break

            if not t:
                n_skip_no_match += 1
                continue
            join_key = match_key or theme

            trend_score = float(t["score"] or 0)
            predicted_opp = float(t["opportunity_score"] or trend_score)
            sector = t["sector"] or ""

            # perf_boost / freshness 는 발행 시점 그대로 재현 어려움 — 현재 값으로 근사
            try:
                from JARVIS03_RADAR.analyzer import (
                    get_performance_boost as _gpb,
                    get_freshness_bonus as _gfr,
                )
                perf_boost = float(_gpb(theme))
                freshness  = float(_gfr(theme))
            except Exception:
                perf_boost = 0.0
                freshness = 0.0

            try:
                _db.learn_log_upsert(
                    keyword=theme, sector=sector, platform=r["platform"] or "",
                    trend_score=trend_score, perf_boost=perf_boost,
                    freshness=freshness, velocity=0.0, competition=50.0,
                    predicted_opp=predicted_opp,
                    actual_views=cv,
                    days_after=days_after,
                    naver_rank=int(rk) if rk is not None else None,
                )
                n_logged += 1
                if cv > 0: n_signal_views += 1
                if rk is not None: n_signal_rank += 1
            except Exception as e:
                if verbose:
                    print(f"  ⚠️ log fail kw={theme}: {e}")
                    _g_report("radar", e, module=__name__)

    if verbose:
        print(f"  📥 learn_log 적재: {n_logged}건 "
              f"(views 신호 {n_signal_views} / rank 신호 {n_signal_rank}) "
              f"(skip: days={n_skip_days}, no_match={n_skip_no_match})")
    return {
        "logged": n_logged,
        "signal_views": n_signal_views,
        "signal_rank": n_signal_rank,
        "skip_days_after": n_skip_days,
        "skip_no_match": n_skip_no_match,
    }


# ─────────────────────────────────────────────────────────────
# 2. 가중치 회귀학습 — 일요일 04:00
# ─────────────────────────────────────────────────────────────

# ★ 학습 입력 (ERRORS [485], 사용자 지시 2026-07-23) — velocity·competition 제거.
#   두 입력은 learn_log 에 상수(0.0/50.0)로만 적재됐고 trends 테이블에 컬럼 자체가 없어
#   *데이터 출처가 없는 유령 피처* 였다(고유값 1종 → 학습 원리적 불가). 남기면 화면에
#   "데이터 없음" 만 채우고 예측엔 기여 0. → 학습 대상에서 제외.
#   (DB 스키마 learned_weights.w_velocity/w_competition 컬럼은 호환 위해 유지, 0 으로 저장)
FEATURES = ["trend_score", "perf_boost", "freshness"]


_RANK_UNRANKED = 100.0   # 검색 100위 밖 = 미노출 취급 (순위 상한)


def build_target(rows: list, min_signal: int = 20) -> "tuple[np.ndarray, str]":
    """학습 정답값(y) 생성 — **단일 진입점** (★ ERRORS [483], 사용자 판단 2026-07-22).

    ★ 왜 조회수가 아닌가: 네이버·티스토리는 **공개 페이지에 조회수를 노출하지 않는다.**
      실측 2026-07-22 — 네이버 "패턴 8개 모두 매칭 실패(응답 2819자)",
      티스토리 "조회수 미수집(정책 한계)". 그 결과 `actual_views` 가 366행 중 365행이 0 이 됐고,
      · 학습기는 `len(unique(y)) < 2` 로 조용히 포기 → `learned_weights` **영구 0행**
      · 백테스트는 정답값이 상수라 `r2=1.0` → **가짜 100%** (변별력 0)
      즉 *플랫폼이 안 주는 값* 을 정답으로 삼아 학습 3단이 통째로 죽어 있었다.

    ★ 대신 `naver_rank`(검색 노출 순위)를 쓴다 — 로그인 없이 수집되고 실제로 살아 있다
      (learn_log 366행 중 365행 보유, 1~82위, 고유값 63종).
      순위는 *작을수록 좋으므로* 부호를 뒤집어 "높을수록 좋음" 으로 통일한다.
      미노출(None)은 최하위권으로 간주(_RANK_UNRANKED).

    조회수 신호가 되살아나면(로그인 수집 등) `actual_views` 를 우선 쓰도록 자동 전환된다 —
    지표 교체를 코드 수정 없이 흡수하기 위해 *데이터를 보고 고른다*.

    Returns: (y, 사용한 신호 이름)
    """
    views = np.array([float(r["actual_views"] or 0) for r in rows], dtype=np.float64)
    # ★ "고유값 2종 이상" 만으로는 부족하다 — 실측에서 366행 중 365행이 0, 1행이 1 이라
    #   조건을 통과해버렸다(그런 신호로 학습하면 잡음만 배운다).
    #   *0 이 아닌 표본이 학습 최소 표본 수 이상* 일 때만 조회수를 신뢰한다.
    if int((views > 0).sum()) >= min_signal:
        return np.log1p(views), "actual_views"

    ranks = np.array(
        [float(r["naver_rank"]) if r.get("naver_rank") is not None else _RANK_UNRANKED
         for r in rows], dtype=np.float64)
    # 순위 → 점수: 1위가 가장 높고 미노출이 0 (log 로 상위권 차이를 키움)
    return np.log1p(np.maximum(0.0, _RANK_UNRANKED - ranks)), "naver_rank"


def train_weights(min_samples: int = 20, verbose: bool = True) -> dict:
    """
    learn_log 에서 (X, y) 학습 → Ridge regression → learned_weights 저장.

    target: log1p(actual_views) — 분포 왜도 보정.
    fallback: 샘플 부족 시 학습 안 함, 기본값 유지.
    """
    rows = _db.learn_log_fetch(min_samples=min_samples)
    if not rows:
        if verbose:
            print(f"  ⏸  학습 보류 — learn_log 샘플 < {min_samples}")
        return {"trained": False, "n_samples": _db.learn_log_count()}

    try:
        from sklearn.linear_model import Ridge
        from sklearn.metrics import r2_score, mean_squared_error
    except ImportError:
        print("  ❌ sklearn 미설치 — 학습 불가")
        return {"trained": False, "error": "sklearn missing"}

    X = np.array([[r[f] or 0.0 for f in FEATURES] for r in rows], dtype=np.float64)
    y, _signal = build_target(rows, min_signal=min_samples)   # ★ ERRORS [483] (단일 진입점)

    if X.shape[0] < min_samples or len(np.unique(y)) < 2:
        # ★ 조용한 포기 금지 — 왜 학습이 안 됐는지 남긴다 (종전엔 이유 없이 return 만 했고,
        #   잡은 success=1 로 보고돼 "성공했는데 결과가 없는" 상태가 몇 달 지속됐다)
        _reason = ("정답값 변별 없음(전부 동일)" if len(np.unique(y)) < 2
                   else f"샘플 부족 {X.shape[0]}<{min_samples}")
        if verbose:
            print(f"  ⏸  학습 보류 — {_reason} (신호={_signal})")
        return {"trained": False, "n_samples": X.shape[0],
                "reason": _reason, "signal": _signal}

    model = Ridge(alpha=1.0)
    model.fit(X, y)
    pred = model.predict(X)
    r2 = float(r2_score(y, pred))
    mse = float(mean_squared_error(y, pred))

    w = model.coef_.tolist()
    intercept = float(model.intercept_)

    # opportunity_score 스케일(0~120) 에 맞도록 가중치 정규화
    # log-views 회귀결과는 작은 값이라 그대로 쓰면 영향 미미 → 스케일링
    SCALE = 6.0  # 경험적 — 학습 가중치를 0.1~5 범위로 끌어올림
    w_scaled = [v * SCALE for v in w]

    # FEATURES 는 전부 클수록 좋음 → 음수 방지 (인덱스 하드코딩 제거 — FEATURES 파생)
    w_scaled = [max(0.0, v) for v in w_scaled]
    _wmap = dict(zip(FEATURES, w_scaled))   # {trend_score: w, perf_boost: w, freshness: w}

    new_id = _db.learned_weights_save(
        w_trend=round(_wmap.get("trend_score", 0.0), 4),
        w_perf=round(_wmap.get("perf_boost", 0.0), 4),
        w_fresh=round(_wmap.get("freshness", 0.0), 4),
        w_velocity=0.0,      # ★ 제거된 유령 피처 — 0 저장 (스키마 호환, ERRORS [485])
        w_competition=0.0,
        intercept=round(intercept, 4),
        n_samples=X.shape[0], r2=round(r2, 4), mse=round(mse, 4),
    )

    if verbose:
        print(f"  🧠 가중치 학습 완료 (id={new_id}, n={X.shape[0]}, r2={r2:.3f})")
        print("     " + " ".join(f"{f.split('_')[0]}={_wmap[f]:.3f}" for f in FEATURES))

    return {"trained": True, "n_samples": X.shape[0], "r2": r2, "mse": mse,
            "weights": {"w_trend": _wmap.get("trend_score", 0.0),
                        "w_perf": _wmap.get("perf_boost", 0.0),
                        "w_freshness": _wmap.get("freshness", 0.0)}}


def get_current_weights() -> dict:
    """analyzer.opportunity_score 가 매번 호출 — 최신 학습 가중치 반환.
    학습 데이터 없으면 DEFAULT_WEIGHTS 사용."""
    return _db.learned_weights_latest()


# ─────────────────────────────────────────────────────────────
# 3. 사용자 승인/거부 피드백 — 매일 04:00
# ─────────────────────────────────────────────────────────────

def update_feedback_from_events(days: int = 7, verbose: bool = False) -> dict:
    """
    events 테이블에서 최근 N일 승인/거부 이벤트 → feedback_penalty 업데이트.
    sector 단위 + keyword 단위 양쪽 누적.
    """
    n_app = 0
    n_rej = 0
    with _db.get_db() as conn:
        # 컬럼명 정정: events.type → events.event_type, ts → created_at
        rows = conn.execute(
            f"""SELECT event_type, payload, created_at
                FROM events
                WHERE event_type IN ('post_revise_approved', 'post_revise_rejected',
                                      'auto_approved', 'manual_approved', 'rejected')
                  AND created_at >= datetime('now', '-{int(days)} days', 'localtime')"""
        ).fetchall()

    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            continue
        theme = payload.get("theme") or payload.get("keyword") or ""
        sector = payload.get("sector") or ""
        is_reject = "reject" in (r["event_type"] or "").lower()

        if theme:
            _db.feedback_penalty_upsert(
                f"kw:{theme}",
                rejected_inc=1 if is_reject else 0,
                approved_inc=0 if is_reject else 1,
            )
            n_rej += int(is_reject); n_app += int(not is_reject)
        if sector:
            _db.feedback_penalty_upsert(
                f"sector:{sector}",
                rejected_inc=1 if is_reject else 0,
                approved_inc=0 if is_reject else 1,
            )

    # rejected/approved 비율로 penalty 일괄 재계산
    n_recomp = _db.feedback_penalty_recompute_all()
    if verbose:
        print(f"  👤 feedback events 처리: 승인 {n_app} / 거부 {n_rej} → penalty {n_recomp}건 갱신")
    return {"approved": n_app, "rejected": n_rej, "penalties_updated": n_recomp}


def get_feedback_penalty(keyword: str = "", sector: str = "") -> float:
    """opportunity_score 가 호출 — keyword + sector 페널티 합."""
    p = 0.0
    if keyword:
        p += _db.feedback_penalty_get(f"kw:{keyword}")
    if sector:
        p += _db.feedback_penalty_get(f"sector:{sector}")
    return max(-30.0, p)  # 캡


# ─────────────────────────────────────────────────────────────
# 2-bis. 부정 신호 — 평균 미달 키워드 음수 페널티
# ─────────────────────────────────────────────────────────────

def get_negative_signal_penalty(keyword: str) -> float:
    """
    keyword_performance 의 avg_views 가 전체 평균보다 낮으면 음수 페널티 (0 ~ -15).
    "잘 된 키워드는 더 추천, 안 된 키워드는 덜 추천" — 양방향 학습.
    """
    try:
        with _db.get_db() as conn:
            kp = conn.execute(
                "SELECT avg_views, post_count FROM keyword_performance WHERE keyword=?", (keyword,)
            ).fetchone()
            if not kp or not kp["avg_views"]:
                return 0.0
            global_avg = conn.execute(
                "SELECT AVG(avg_views) FROM keyword_performance WHERE post_count >= 1"
            ).fetchone()[0] or 100.0
        avg = float(kp["avg_views"])
        post_count = int(kp["post_count"] or 0)
        if avg >= global_avg:
            return 0.0
        # 평균 절반 이하 + 3회 이상 발행이면 진짜 약한 키워드
        ratio = avg / max(global_avg, 1.0)  # 0~1
        confidence = min(1.0, post_count / 5.0)
        return round(-15.0 * (1 - ratio) * confidence, 2)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# 4. 키워드 임베딩 cold-start
# ─────────────────────────────────────────────────────────────

def index_keyword_embedding(keyword: str) -> bool:
    """신규 키워드 임베딩 → DB 저장."""
    try:
        from shared.style import _get_provider, _pack  # ★ Phase 2 통합 (2026-05-18)
        provider, model, dim, fn = _get_provider()
        if provider != "tfidf":
            # voyage / local_minilm(공유 MiniLM 384d) — fn 직접 호출, model·dim 은 provider 값
            v = fn([keyword])[0]
        else:
            # TF-IDF — 기존 vectorizer 사용 (없으면 skip)
            import pickle
            pkl = ROOT / "shared/.tfidf_vec.pkl"
            if not pkl.exists():
                return False
            vec = pickle.load(open(pkl, "rb"))
            arr = vec.transform([keyword]).toarray().astype(np.float32)[0]
            n = np.linalg.norm(arr) + 1e-9
            v = arr / n
            model = "tfidf-char-2-4"
            dim = arr.shape[0]
        _db.keyword_embedding_upsert(keyword, _pack(v), model, int(dim))
        return True
    except Exception:
        return False


def backfill_keyword_embeddings(verbose: bool = True, reindex: bool = False) -> dict:
    """trends 테이블 고유 키워드 전체 → keyword_embeddings 일괄 백필.

    이미 임베딩된 키워드는 건너뜀. TF-IDF 모드는 기존 vectorizer pickle 사용.
    reindex=True: 프로바이더 전환(tfidf→local_minilm 등)으로 임베딩 공간이 바뀌었을 때
      현재 모델과 다른 행을 삭제 후 재임베딩 (마이그레이션 1회용). 기본 False.
    반환: {total, new, skipped, failed}
    """
    import sqlite3 as _sq
    from shared.db import DB_PATH as _jarvis_db
    con = _sq.connect(str(_jarvis_db))
    all_kws = [r[0] for r in con.execute(
        "SELECT DISTINCT keyword FROM trends ORDER BY keyword"
    ).fetchall()]
    if reindex:
        # 프로바이더 전환 → 임베딩 공간 불일치 → 현재 모델 아닌 행만 삭제 후 재백필
        from shared.style import _get_provider as _gp
        _cur_model = _gp()[1]
        con.execute(
            "DELETE FROM keyword_embeddings WHERE embed_model IS NULL OR embed_model != ?",
            (_cur_model,),
        )
        con.commit()
    already = {r[0] for r in con.execute(
        "SELECT keyword FROM keyword_embeddings"
    ).fetchall()}
    con.close()

    todo = [k for k in all_kws if k not in already]
    total = len(all_kws)
    skipped = len(already)
    new_cnt = failed = 0

    if verbose:
        print(f"🔢 trends 고유 키워드: {total}개 | 기존 임베딩: {skipped}개 | 신규 대상: {len(todo)}개")

    for i, kw in enumerate(todo, 1):
        ok = index_keyword_embedding(kw)
        if ok:
            new_cnt += 1
        else:
            failed += 1
        if verbose and i % 50 == 0:
            print(f"  진행 {i}/{len(todo)} — 성공 {new_cnt} / 실패 {failed}")

    if verbose:
        print(f"✅ 완료: 신규 {new_cnt}개 임베딩 | 실패 {failed}개")
    return {"total": total, "new": new_cnt, "skipped": skipped, "failed": failed}


def get_cold_start_boost(keyword: str, top_k: int = 3) -> float:
    """
    신규 키워드의 임베딩 cosine top-K 의 perf 가중평균을 cold-start 보너스로 반환 (0~25).
    이미 keyword_performance 데이터가 있으면 0 (cold-start 불필요).
    """
    try:
        kp = _db.get_keyword_performance(keyword)
        if kp and (kp.get("post_count") or 0) >= 1:
            return 0.0  # 자체 데이터 있음 — cold-start 불필요

        rows = _db.keyword_embeddings_all()
        if not rows:
            return 0.0

        # 신규 키워드 임베딩 (없으면 인덱싱)
        own = _db.keyword_embedding_get(keyword)
        if not own:
            if not index_keyword_embedding(keyword):
                return 0.0
            own = _db.keyword_embedding_get(keyword)
            if not own:
                return 0.0

        from shared.style import unpack  # ★ Phase 2 통합 (2026-05-18)
        own_v = unpack(own["embedding"], own["embed_dim"])

        sims = []
        for r in rows:
            if r["keyword"] == keyword:
                continue
            try:
                cv = unpack(r["embedding"], r["embed_dim"])
                if cv.shape != own_v.shape:
                    continue
                s = float(np.dot(own_v, cv))
            except Exception:
                continue
            sims.append((s, r["keyword"]))
        sims.sort(reverse=True)
        sims = sims[:top_k]
        if not sims:
            return 0.0

        # 유사도 가중 perf 평균
        total_w = 0.0
        total_p = 0.0
        for s, kw in sims:
            if s < 0.1:
                continue
            kp2 = _db.get_keyword_performance(kw)
            if not kp2:
                continue
            avg_v = float(kp2.get("avg_views") or 0)
            # 25점 만점 — get_performance_boost 와 같은 스케일
            perf = min(25.0, avg_v / 10.0)
            total_p += perf * s
            total_w += s
        if total_w == 0:
            return 0.0
        return round((total_p / total_w) * 0.5, 2)  # cold-start 는 50% 만 반영(과신 방지)
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────
# 5. 자동 백테스트 — 일요일 04:30
# ─────────────────────────────────────────────────────────────

def run_backtest(test_ratio: float = 0.2, verbose: bool = True) -> dict:
    """
    learn_log 시간순 분할 → 앞쪽으로 학습, 뒤쪽으로 평가.
    R² / MSE / MAPE 를 backtest_history 에 기록 → 대시보드 추이 차트.
    """
    rows = _db.learn_log_fetch(min_samples=30)
    if not rows:
        return {"ok": False, "reason": "insufficient samples"}

    try:
        from sklearn.linear_model import Ridge
        from sklearn.metrics import r2_score, mean_squared_error
    except ImportError:
        return {"ok": False, "reason": "sklearn missing"}

    rows.sort(key=lambda r: r.get("logged_at") or "")
    split = int(len(rows) * (1 - test_ratio))
    if split < 10 or len(rows) - split < 5:
        return {"ok": False, "reason": "split too small"}

    def _xy(rs):
        X = np.array([[r[f] or 0.0 for f in FEATURES] for r in rs], dtype=np.float64)
        y, _ = build_target(rs)   # ★ ERRORS [483] — 학습과 *같은* 정답 정의 사용
        return X, y

    X_tr, y_tr = _xy(rows[:split])
    X_te, y_te = _xy(rows[split:])
    m = Ridge(alpha=1.0).fit(X_tr, y_tr)
    pred = m.predict(X_te)

    # ★ 정답값이 상수면 sklearn r2_score 는 1.0 을 돌려준다 — "완벽 예측" 이 아니라
    #   *평가 불가* 다. 종전엔 이 값을 그대로 저장해 화면에 백테스트 100% 로 표시됐다
    #   (전 회차 r2=1.0/mse=0.0/mape=0.0). 변별 없는 구간은 저장하지 않는다. (ERRORS [483])
    if len(np.unique(y_te)) < 2:
        if verbose:
            print("  ⏸  백테스트 보류 — 검증 구간 정답값 변별 없음 (r2=1.0 가짜 만점 방지)")
        return {"ok": False, "reason": "test target constant"}

    r2 = float(r2_score(y_te, pred))
    mse = float(mean_squared_error(y_te, pred))
    # MAPE on expm1 (실제 views 스케일)
    actual = np.expm1(y_te)
    pred_act = np.expm1(pred)
    eps = 1.0
    mape = float(np.mean(np.abs(actual - pred_act) / (np.abs(actual) + eps))) * 100.0

    _db.backtest_save(n_samples=len(rows), r2=round(r2, 4), mse=round(mse, 4),
                      mape=round(mape, 2))
    if verbose:
        print(f"  📈 backtest: n={len(rows)} r2={r2:.3f} mse={mse:.3f} mape={mape:.1f}%")
    return {"ok": True, "n": len(rows), "r2": r2, "mse": mse, "mape": mape}


# ─────────────────────────────────────────────────────────────
# CLI 디버그
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", action="store_true", help="learn_log 적재")
    ap.add_argument("--train", action="store_true", help="가중치 학습")
    ap.add_argument("--feedback", action="store_true", help="feedback 갱신")
    ap.add_argument("--backtest", action="store_true", help="백테스트")
    ap.add_argument("--all", action="store_true", help="전체 파이프라인")
    args = ap.parse_args()

    if args.all or args.log:      log_predictions_vs_actual(verbose=True)
    if args.all or args.feedback: update_feedback_from_events(verbose=True)
    if args.all or args.train:    train_weights(verbose=True)
    if args.all or args.backtest: run_backtest(verbose=True)
    if not any([args.all, args.log, args.train, args.feedback, args.backtest]):
        # 기본 — 현재 상태 출력
        print(f"learn_log rows: {_db.learn_log_count()}")
        w = get_current_weights()
        print(f"current weights: {w}")
