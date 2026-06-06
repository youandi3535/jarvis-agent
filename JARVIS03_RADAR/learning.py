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

FEATURES = ["trend_score", "perf_boost", "freshness", "velocity", "competition"]


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
    y = np.log1p(np.array([r["actual_views"] or 0 for r in rows], dtype=np.float64))

    if X.shape[0] < min_samples or len(np.unique(y)) < 2:
        return {"trained": False, "n_samples": X.shape[0]}

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

    # 음수 방지(단, competition 은 음수 허용)
    w_scaled[0] = max(0.0, w_scaled[0])  # trend
    w_scaled[1] = max(0.0, w_scaled[1])  # perf
    w_scaled[2] = max(0.0, w_scaled[2])  # fresh
    # velocity, competition 은 부호 유지

    new_id = _db.learned_weights_save(
        w_trend=round(w_scaled[0], 4), w_perf=round(w_scaled[1], 4),
        w_fresh=round(w_scaled[2], 4), w_velocity=round(w_scaled[3], 4),
        w_competition=round(w_scaled[4], 4),
        intercept=round(intercept, 4),
        n_samples=X.shape[0], r2=round(r2, 4), mse=round(mse, 4),
    )

    if verbose:
        print(f"  🧠 가중치 학습 완료 (id={new_id}, n={X.shape[0]}, r2={r2:.3f})")
        print(f"     trend={w_scaled[0]:.3f} perf={w_scaled[1]:.3f} "
              f"fresh={w_scaled[2]:.3f} vel={w_scaled[3]:.3f} comp={w_scaled[4]:.3f}")

    return {"trained": True, "n_samples": X.shape[0], "r2": r2, "mse": mse,
            "weights": dict(zip([f"w_{f.split('_')[0] if f != 'competition' else 'competition'}"
                                 for f in FEATURES], w_scaled))}


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
        from shared.style import _get_provider, _embed_voyage, _tfidf_fit_transform, _pack  # ★ Phase 2 통합 (2026-05-18)
        provider, model, dim, fn = _get_provider()
        if provider == "voyage":
            v = _embed_voyage([keyword])[0]
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


def backfill_keyword_embeddings(verbose: bool = True) -> dict:
    """trends 테이블 고유 키워드 전체 → keyword_embeddings 일괄 백필.

    이미 임베딩된 키워드는 건너뜀. TF-IDF 모드는 기존 vectorizer pickle 사용.
    반환: {total, new, skipped, failed}
    """
    import sqlite3 as _sq
    con = _sq.connect(str(ROOT / "shared" / "jarvis.sqlite"))
    all_kws = [r[0] for r in con.execute(
        "SELECT DISTINCT keyword FROM trends ORDER BY keyword"
    ).fetchall()]
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
        y = np.log1p(np.array([r["actual_views"] or 0 for r in rs], dtype=np.float64))
        return X, y

    X_tr, y_tr = _xy(rows[:split])
    X_te, y_te = _xy(rows[split:])
    m = Ridge(alpha=1.0).fit(X_tr, y_tr)
    pred = m.predict(X_te)

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
