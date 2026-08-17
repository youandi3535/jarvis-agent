"""
JARVIS Hub API Server — FastAPI 백엔드 (포트 9198)
Next.js 대시보드(9199)에 데이터 제공.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ── 경로 설정 ────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

# ── DB ───────────────────────────────────────────────────────────
try:
    # ★ error_log.timestamp 비교 포맷의 주인은 shared.db 하나다 (2026-08-08, ①).
    #   여기서 datetime(...) 을 직접 쓰면 'T' vs 공백 구분자 때문에 같은 날짜 행이
    #   시각과 무관하게 전부 통과한다 (실측 60분 창 115행 vs 올바른 3행).
    from shared.db import DB_PATH, get_db as _get_db, ts_cutoff_sql as _ts_cut
    def _db():
        try:
            return _get_db()
        except Exception:
            return None
except ImportError:
    import sqlite3
    _DB_PATH_STR = os.getenv("JARVIS_DB_PATH", str(Path.home() / ".jarvis" / "jarvis.sqlite"))
    DB_PATH = Path(_DB_PATH_STR)
    def _db():
        if not DB_PATH.exists():
            return None
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        return con
    def _get_db():
        return _db()
    def _ts_cut(*mods: str) -> str:
        """폴백 — `shared.db` 를 못 불러온 환경에서도 **같은 포맷**을 쓴다.

        ★ 포맷을 두 벌 적는 것처럼 보이지만, 이 분기는 `shared.db` 자체가 없을 때만
          도는 최후 폴백이다(그때는 참조할 원본이 없다). 위 정상 분기가 진실이고,
          둘이 어긋나면 `test_timestamp_비교가_한_포맷을_쓴다` 가 잡는다.
        """
        if not mods:
            return "strftime('%Y-%m-%dT%H:%M:%S', datetime('now','localtime'))"
        _m = ", ".join("'" + x.replace("'", "''") + "'" for x in mods)
        return f"strftime('%Y-%m-%dT%H:%M:%S', datetime('now','localtime', {_m}))"

# ── Vision 포트 ──────────────────────────────────────────────────
_VISION_PORT = int(os.getenv("JARVIS_VISION_PORT", "8505"))

# ── FastAPI 앱 ───────────────────────────────────────────────────
app = FastAPI(title="JARVIS Hub API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:9199", "http://127.0.0.1:9199"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════════
def _rows(con, sql, params=()):
    try:
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _scalar(con, sql, params=(), default=0):
    try:
        row = con.execute(sql, params).fetchone()
        return row[0] if row else default
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════
# 엔드포인트
# ══════════════════════════════════════════════════════════════════

@app.get("/api/health")
def health():
    return {"ok": True, "ts": datetime.now().isoformat()}


# ── 데몬 상태 ────────────────────────────────────────────────────
@app.get("/api/daemon")
def get_daemon():
    pid_file = BASE_DIR / "logs" / "daemon.pid"
    r = {"alive": False, "pid": None, "uptime": "—"}
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().split("\n")[0].strip())
            r["pid"] = pid
            ps = subprocess.run(["ps", "-p", str(pid), "-o", "pid,etime="],
                                capture_output=True, text=True)
            if ps.returncode == 0:
                r["alive"] = True
                lines = ps.stdout.strip().splitlines()
                if len(lines) >= 2:
                    r["uptime"] = lines[-1].strip().split()[-1]
        except Exception:
            pass
    return r


# ── 발행 통계 ────────────────────────────────────────────────────
@app.get("/api/posts")
def get_posts():
    con = _db()
    if not con:
        return {"today": 0, "week": 0, "month": 0, "by_platform": {}}
    today     = datetime.now().strftime("%Y-%m-%d")
    week_ago  = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        r = {
            "today": _scalar(con, "SELECT COUNT(*) FROM posts WHERE date(created_at)=?", (today,)),
            "week":  _scalar(con, "SELECT COUNT(*) FROM posts WHERE date(created_at)>=?", (week_ago,)),
            "month": _scalar(con, "SELECT COUNT(*) FROM posts WHERE date(created_at)>=?", (month_ago,)),
            "by_platform": {
                row["platform"]: row["n"]
                for row in _rows(con, "SELECT platform,COUNT(*) as n FROM posts WHERE date(created_at)=? GROUP BY platform", (today,))
            },
        }
    except Exception:
        r = {"today": 0, "week": 0, "month": 0, "by_platform": {}}
    con.close()
    return r


# ── 파이프라인 ───────────────────────────────────────────────────
@app.get("/api/themes/official")
def get_official_themes():
    """네이버 공식 테마 전체 + 작성 현황 + 오늘의 픽."""
    try:
        import sys as _sys
        _sys.path.insert(0, str(BASE_DIR))
        from JARVIS09_COLLECTOR import naver_theme_catalog   # ★ 09 공개 정문 (private 직수입 폐기 2026-07-23)
        catalog: dict = naver_theme_catalog()   # {테마명: 테마번호}
    except Exception:
        catalog = {}

    con = _db()
    written_set: set[str] = set()
    today_pick: dict | None = None
    try:
        if con:
            # 작성 완료 테마 (post_analysis, 경제지표·경제브리핑 제외)
            rows = _rows(con, """
                SELECT DISTINCT theme FROM post_analysis
                WHERE theme IS NOT NULL
                  AND theme NOT LIKE '경제지표%'
                  AND theme NOT LIKE '경제 브리핑%'
            """)
            # 카탈로그에 있는 테마만 ✓ 처리 (비공식 주제 제외)
            written_set = {r["theme"] for r in rows if r["theme"] in catalog}

            # 오늘의 픽: pipeline에서 오늘 등록된 것 중 opportunity_score 최상위 1개
            pick_rows = _rows(con, """
                SELECT theme, sector, opportunity_score FROM pipeline
                WHERE status = 'suggested'
                  AND date(created_at) = date('now', 'localtime')
                ORDER BY opportunity_score DESC, created_at DESC
                LIMIT 1
            """)
            if pick_rows:
                today_pick = dict(pick_rows[0])
            con.close()
    except Exception:
        if con:
            con.close()

    themes = [
        {"name": name, "no": no, "written": name in written_set}
        for name, no in catalog.items()
    ]
    themes.sort(key=lambda x: (not x["written"], x["name"]))

    return {
        "total":         len(themes),
        "written_count": len(written_set),
        "themes":        themes,
        "today_pick":    today_pick,
    }


@app.get("/api/pipeline")
def get_pipeline():
    con = _db()
    if not con:
        return {"today": {}, "all": {}, "recent": []}
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        rows_today = _rows(con, "SELECT status,COUNT(*) as n FROM pipeline WHERE date(created_at)=? GROUP BY status", (today,))
        rows_all   = _rows(con, "SELECT status,COUNT(*) as n FROM pipeline GROUP BY status")
        recent     = _rows(con, "SELECT theme,status,created_at FROM pipeline ORDER BY created_at DESC LIMIT 10")
        con.close()
        return {
            "today":  {r["status"]: r["n"] for r in rows_today},
            "all":    {r["status"]: r["n"] for r in rows_all},
            "recent": recent,
        }
    except Exception:
        con.close()
        return {"today": {}, "all": {}, "recent": []}


# ── 트렌드 ───────────────────────────────────────────────────────
@app.get("/api/trends")
def get_trends():
    con = _db()
    if not con:
        return {"today": 0, "top": [], "sectors": {}, "google_top10": [], "naver_top10": [], "combined_keywords": []}
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        count   = _scalar(con, "SELECT COUNT(*) FROM trends WHERE date=?", (today,))
        top     = _rows(con, "SELECT keyword,sector,score,opportunity_score,source FROM trends WHERE date=? ORDER BY opportunity_score DESC LIMIT 15", (today,))
        sectors = _rows(con, "SELECT sector,COUNT(*) as n FROM trends WHERE date=? GROUP BY sector ORDER BY n DESC", (today,))
        con.close()
        google_top10, naver_top10, combined_keywords = [], [], []
        recommendations, trend_delta = [], {}
        json_path = BASE_DIR / "JARVIS03_RADAR" / "data" / f"trends_{today}.json"
        if json_path.exists():
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
                google_top10     = raw.get("google_top10", [])
                naver_top10      = raw.get("naver_top10", [])
                # 구 필드명(combined_top50) 호환 fallback
                combined_keywords = raw.get("combined_keywords", raw.get("combined_top50", []))
                recommendations   = raw.get("recommendations", [])
                trend_delta       = raw.get("trend_delta", {})
            except Exception:
                pass
        topic_candidates = []
        pack_path = BASE_DIR / "JARVIS03_RADAR" / "data" / f"topic_pack_{today}.json"
        if pack_path.exists():
            try:
                pack = json.loads(pack_path.read_text(encoding="utf-8"))
                topic_candidates = pack.get("candidates", [])
            except Exception:
                pass
        return {
            "today":              count,
            "sectors":            {r["sector"]: r["n"] for r in sectors},
            "google_top10":       google_top10,
            "naver_top10":        naver_top10,
            "combined_keywords":  combined_keywords,
            "recommendations":    recommendations,
            "trend_delta":        trend_delta,
            "topic_candidates":   topic_candidates,
        }
    except Exception:
        con.close()
        return {"today": 0, "top": [], "sectors": {}}


# ── 품질 통계 ────────────────────────────────────────────────────
# 현재 활성 상태 메타 — 여기 있는 것만 UI에 표시. 폐기된 상태는 이 목록에서 제거.
_STATUS_META: dict[str, dict] = {
    "approved":  {"label": "승인 완료", "hint": "success"},
    "analyzing": {"label": "분석 중",   "hint": "primary"},
    "ignored":   {"label": "무시",      "hint": "muted"},
}

@app.get("/api/quality/stats")
def get_quality_stats():
    con = _db()
    if not con:
        return {"by_status": {}, "status_labels": {}, "status_hints": {}, "recent": []}
    try:
        rows = _rows(con, "SELECT status, COUNT(*) as n FROM post_analysis GROUP BY status")
        # _STATUS_META 에 있는 활성 상태만 표시 — 폐기된 상태(revised 등)는 자동 제외
        by_status     = {r["status"]: r["n"] for r in rows if r["status"] in _STATUS_META}
        status_labels = {k: _STATUS_META[k]["label"] for k in by_status}
        status_hints  = {k: _STATUS_META[k]["hint"]  for k in by_status}
        recent = _rows(con, "SELECT platform,title,status,created_at,current_views FROM post_analysis ORDER BY created_at DESC LIMIT 20")
        con.close()
        return {"by_status": by_status, "status_labels": status_labels, "status_hints": status_hints, "recent": recent}
    except Exception:
        con.close()
        return {"by_status": {}, "status_labels": {}, "status_hints": {}, "recent": []}


@app.get("/api/quality/trend")
def get_quality_trend():
    con = _db()
    if not con:
        return {}
    try:
        import json as _json
        from collections import defaultdict
        rows = _rows(con, """
            SELECT strftime('%Y-W%W', created_at) as week,
                   suggestions, post_type, platform
            FROM post_analysis
            WHERE created_at IS NOT NULL
            ORDER BY created_at
        """)
        weekly   = defaultdict(lambda: {"posts": 0, "total_issues": 0})
        by_type  = defaultdict(int)
        by_plat  = defaultdict(lambda: {"posts": 0, "total_issues": 0})
        by_ptype = defaultdict(lambda: {"posts": 0, "total_issues": 0})
        for r in rows:
            week = r["week"]
            try:   sugs = _json.loads(r["suggestions"] or "[]")
            except: sugs = []
            n = len(sugs)
            weekly[week]["posts"]        += 1
            weekly[week]["total_issues"] += n
            for s in sugs:
                by_type[s.get("type", "other")] += 1
            plat = r["platform"] or "unknown"
            pt   = r["post_type"] or "unknown"
            by_plat[plat]["posts"]        += 1
            by_plat[plat]["total_issues"] += n
            by_ptype[pt]["posts"]         += 1
            by_ptype[pt]["total_issues"]  += n

        def _week_label(week_str: str) -> str:
            """'2026-W17' → '4월 셋째주'"""
            try:
                from datetime import date as _date
                import math as _math
                year, w = week_str.split("-")
                monday = _date.fromisocalendar(int(year), int(w.lstrip("W")), 1)
                nth = _math.ceil(monday.day / 7)
                _ord = ["첫", "둘", "셋", "넷", "다섯"]
                return f"{monday.month}월 {_ord[min(nth,5)-1]}째주"
            except:
                return week_str

        weekly_trend = []
        for week in sorted(weekly.keys()):
            d    = weekly[week]
            posts = d["posts"]
            avg  = round(d["total_issues"] / posts, 1) if posts else 0
            weekly_trend.append({"week": _week_label(week), "posts": posts, "avg_issues": avg})

        def _stats(d):
            return {k: {"posts": v["posts"],
                        "avg_issues": round(v["total_issues"]/v["posts"], 1) if v["posts"] else 0}
                    for k, v in d.items()}

        top_insights = _rows(con, """
            SELECT insight_type, description, occurrences, weight
            FROM learning_insights
            ORDER BY occurrences DESC, weight DESC
            LIMIT 8
        """)
        con.close()
        return {
            "weekly":       weekly_trend,
            "by_type":      dict(sorted(by_type.items(), key=lambda x: -x[1])),
            "by_platform":  _stats(by_plat),
            "by_post_type": _stats(by_ptype),
            "top_insights": [dict(r) for r in top_insights],
        }
    except Exception as e:
        con.close()
        return {}


@app.get("/api/quality/history")
def get_quality_history(limit: int = 150):
    try:
        from shared import db as _sdb
        rows = _sdb.get_analysis_history(limit=limit) or []
        return rows
    except Exception:
        con = _db()
        if not con:
            return []
        rows = _rows(con, "SELECT id,platform,theme,title,url,status,suggestions,analyzed_at,created_at,current_views,naver_rank FROM post_analysis ORDER BY created_at DESC LIMIT ?", (limit,))
        con.close()
        return rows


@app.post("/api/quality/{post_id}/approve")
def approve_post(post_id: int):
    con = _db()
    if not con:
        raise HTTPException(404)
    try:
        con.execute("UPDATE post_analysis SET status='approved' WHERE id=?", (post_id,))
        con.commit()
        con.close()
        return {"ok": True}
    except Exception as e:
        con.close()
        raise HTTPException(500, str(e))


@app.post("/api/quality/{post_id}/reject")
def reject_post(post_id: int):
    con = _db()
    if not con:
        raise HTTPException(404)
    try:
        con.execute("UPDATE post_analysis SET status='rejected' WHERE id=?", (post_id,))
        con.commit()
        con.close()
        return {"ok": True}
    except Exception as e:
        con.close()
        raise HTTPException(500, str(e))


# ── 성과 ────────────────────────────────────────────────────────
# 기간 정의 순서 (표시 순서)
_PERIOD_ORDER = ["today", "week", "month", "3month", "6month", "year", "all"]
_PERIOD_META: dict[str, dict] = {
    "today":  {"label": "당일",  "where": "date = date('now')",           "min_days": 0},
    "week":   {"label": "1주일", "where": "date >= date('now','-7 days')", "min_days": 6},
    "month":  {"label": "한달",  "where": "date >= date('now','-30 days')","min_days": 29},
    "3month": {"label": "3개월", "where": "date >= date('now','-90 days')","min_days": 89},
    "6month": {"label": "6개월", "where": "date >= date('now','-180 days')","min_days": 179},
    "year":   {"label": "1년",   "where": "date >= date('now','-365 days')","min_days": 364},
    "all":    {"label": "전체",  "where": "1=1",                           "min_days": 0},
}
# ★ 표시 대상 플랫폼의 단일 소스 — 아래 모든 조회가 여기서 파생한다(①②).
#   WordPress('wp')는 2026-07-27 제거 — 발행자 코드가 없고 2026-05-18 이후 신규 0.
#   과거 실측치(performance.wp_views)는 기록이라 컬럼째 남긴다.
_PLAT_COLS   = [("naver", "naver_views"), ("tistory", "tistory_views")]
_PLAT_LABELS = {"naver": "네이버", "tistory": "티스토리"}

@app.get("/api/performance")
def get_performance():
    con = _db()
    _period_labels = {k: v["label"] for k, v in _PERIOD_META.items()}
    _empty = {
        "active_platforms": [], "platform_labels": {},
        "period_order": ["today", "all"], "period_labels": _period_labels,
        "period_views": {}, "daily_trend": [], "top_posts": [],
        "data_range": {"from": None, "to": None, "days": 0},
    }
    if not con:
        return _empty
    try:
        col_map = dict(_PLAT_COLS)

        # 데이터 있는 플랫폼 탐지
        active: list[str] = []
        for plat, col in _PLAT_COLS:
            n = _scalar(con, f"SELECT COUNT(*) FROM performance WHERE {col} IS NOT NULL AND {col} > 0")
            if n > 0:
                active.append(plat)

        # 실제 데이터 스팬 계산 (MIN~MAX 날짜 차이, 일 단위)
        span_days: int = _scalar(
            con,
            "SELECT CAST(julianday(MAX(date)) - julianday(MIN(date)) AS INTEGER) FROM performance"
        ) or 0

        # 스팬 기준으로 의미 있는 기간만 추출 (today·all은 항상 포함)
        visible_periods = [
            pid for pid in _PERIOD_ORDER
            if span_days >= _PERIOD_META[pid]["min_days"]
        ]

        # 기간 × 플랫폼 매트릭스 (visible 기간만 계산)
        period_views: dict[str, dict] = {}
        for pid in visible_periods:
            where = _PERIOD_META[pid]["where"]
            row: dict[str, int] = {}
            total = 0
            for plat in active:
                v = _scalar(con, f"SELECT COALESCE(SUM({col_map[plat]}),0) FROM performance WHERE {where}")
                row[plat] = v
                total += v
            row["total"] = total
            period_views[pid] = row

        # 일별 추이 (스팬에 맞게 행 수 결정 — 최대 90행)
        trend_limit = max(7, min(span_days + 1, 90)) if span_days > 0 else 30
        _trend_cols = ", ".join(c for _, c in _PLAT_COLS)
        daily_rows = _rows(con, f"SELECT date, {_trend_cols} FROM performance ORDER BY date DESC LIMIT {trend_limit}")
        daily_trend: list[dict] = []
        for r in reversed(daily_rows):
            entry: dict = {"date": r["date"]}
            for plat in active:
                entry[plat] = r.get(col_map[plat]) or 0
            daily_trend.append(entry)

        # 수집 기간 정보
        rng = _rows(con, "SELECT MIN(date) as from_d, MAX(date) as to_d, COUNT(*) as days FROM performance")
        dr = rng[0] if rng else {"from_d": None, "to_d": None, "days": 0}

        # 개별 글 조회수 (post_analysis 크롤링 기반)
        try:
            top = _rows(con, "SELECT platform,title,current_views,naver_rank,created_at FROM post_analysis WHERE current_views>0 ORDER BY current_views DESC LIMIT 15")
        except Exception:
            top = _rows(con, "SELECT platform,title,current_views,NULL as naver_rank,created_at FROM post_analysis WHERE current_views>0 ORDER BY current_views DESC LIMIT 15")

        con.close()
        return {
            "active_platforms": active,
            "platform_labels":  {p: _PLAT_LABELS[p] for p in active},
            "period_order":     visible_periods,
            "period_labels":    _period_labels,
            "period_views":     period_views,
            "daily_trend":      daily_trend,
            "top_posts":        top,
            "data_range": {
                "from": dr.get("from_d"),
                "to":   dr.get("to_d"),
                "days": dr.get("days", 0),
            },
        }
    except Exception:
        try: con.close()
        except Exception: pass
        return _empty


# ── 키워드 성과 ──────────────────────────────────────────────────
@app.get("/api/keywords")
def get_keywords(limit: int = 30):
    con = _db()
    if not con: return []
    try:
        rows = _rows(con, "SELECT keyword,avg_views,best_views,best_rank,avg_rank,composite_score,post_count AS total_posts,last_used AS last_seen FROM keyword_performance ORDER BY composite_score DESC LIMIT ?", (limit,))
        con.close()
        return rows
    except Exception:
        con.close()
        return []


# ── 일일 리뷰 ───────────────────────────────────────────────────
@app.get("/api/daily-review")
def get_daily_review(days: int = 7):
    con = _db()
    if not con: return []
    try:
        rows = _rows(con, "SELECT review_date,posts_count,avg_views,quality_score,sector_dist,common_issues,insights,next_directives,reviewed_at FROM daily_review ORDER BY review_date DESC LIMIT ?", (days,))
        con.close()
        return rows
    except Exception:
        con.close()
        return []


# ── AI 학습 현황 ─────────────────────────────────────────────────
@app.get("/api/learning")
def get_learning():
    con = _db()
    if not con: return {}
    r: dict = {}
    try:
        w = _rows(con, "SELECT id,w_trend,w_perf,w_fresh,w_velocity,w_competition,intercept,n_samples,r2,mse,learned_at FROM learned_weights ORDER BY id DESC LIMIT 3")
        # ★ learned_weights.r2 는 *학습에 쓴 데이터로 자기 자신을 채점한* 값이다 (ERRORS [484]).
        #   종전엔 이것을 '백테스트' 라는 이름으로 화면에 내보냈다 — 시험 문제를 미리 보고 푼
        #   점수를 모의고사 점수라 부른 셈. 학습 점수는 언제나 후하므로 실력을 과대평가한다.
        #   (실측: 학습 0.489 vs 진짜 백테스트 0.330)
        #   → `train_r2`(학습 정확도)와 `backtest_r2`(안 써본 데이터 검증)를 *분리해서* 내보낸다.
        _bt_all = _rows(con, "SELECT tested_at, r2 FROM backtest_history ORDER BY tested_at DESC")

        def _nearest_backtest(when: str):
            """같은 회차의 백테스트를 시각으로 매칭 — 학습·백테스트는 같은 잡에서 연이어 돈다."""
            if not when or not _bt_all:
                return None
            _d = str(when)[:10]
            for b in _bt_all:
                if str(b["tested_at"])[:10] == _d:
                    return b["r2"]
            return None

        r["weights"] = [
            {
                "weight_type":    "ridge",
                "weights_json":   json.dumps({"w_trend": x["w_trend"], "w_perf": x["w_perf"], "w_fresh": x["w_fresh"], "w_velocity": x["w_velocity"], "w_competition": x["w_competition"], "intercept": x["intercept"]}, ensure_ascii=False),
                "trained_at":     x["learned_at"],
                "train_r2":       x["r2"],                       # 자기 채점 (낙관적)
                "backtest_r2":    _nearest_backtest(x["learned_at"]),  # 안 써본 데이터 검증
                "n_samples":      x["n_samples"],
            }
            for x in w
        ]

        # ★ 학습 입력별 변별력 — '영향 없음(0%)' 과 '판단 불가(데이터 없음)' 은 다르다 (ERRORS [484])
        #   velocity·competition 은 learn_log 에 상수로만 적재돼(0.0 / 50.0) 학습이 원리적으로 불가능.
        try:
            _fv = {}
            for _c in ("trend_score", "perf_boost", "freshness", "velocity", "competition"):
                _row = con.execute(f"SELECT COUNT(DISTINCT {_c}) c FROM learn_log").fetchone()
                _fv[_c] = int(_row["c"] or 0)
            r["feature_variance"] = _fv
        except Exception:
            r["feature_variance"] = {}
    except Exception:
        r["weights"] = []
    try:
        bt = _rows(con, "SELECT tested_at,n_samples,r2,mse,mape FROM backtest_history ORDER BY tested_at DESC LIMIT 14")
        r["backtest"] = [{"tested_at": x["tested_at"], "backtest_type": "regression", "score": x["r2"], "details": f"n={x['n_samples']}, MSE={x['mse']:.3f}"} for x in bt]
    except Exception:
        r["backtest"] = []
    try:
        r["insights"] = _rows(con, "SELECT insight_key,insight_type,description,directive,weight,scope,occurrences,last_seen FROM learning_insights ORDER BY occurrences DESC LIMIT 20")
        # ★ 총 개수는 *별도 조회* — 화면이 LIMIT 20 배열 길이를 실제 개수로 착각하던 버그 (ERRORS [479])
        _ic = con.execute("SELECT COUNT(*) c FROM learning_insights").fetchone()
        r["insights_total"] = _ic["c"] if _ic else 0
    except Exception:
        r["insights"] = []
        r["insights_total"] = 0

    # ★ KPI 시계열 (ERRORS [479]) — 과거→현재 추세를 화면에서 바로 보이게.
    #   원천은 self_repair_runs(자가진단 회차별 스냅샷). 오래된 것부터 정렬해 그대로 차트에 사용.
    try:
        # ★ `llm_saved` 옛 칸을 차트에 쓰지 않는다 (2026-08-08 감사).
        #   그 칸의 앞 105행은 `actionable_hits`(누적 패턴 수)이고 뒤 행은 1일 창 실적이라
        #   **정의가 다르다**. 한 축에 그리면 정의가 바뀐 지점이 '붕괴' 로 보인다.
        #   새 칸(`llm_saved_1d`)만 쓰고, 값이 없는 옛 회차는 None 으로 내려 화면이
        #   0 과 '측정 안 함' 을 구분하게 한다.
        _tl = _rows(con,
            "SELECT ran_at, patterns_count, hits_total, llm_saved_1d "
            "FROM self_repair_runs ORDER BY id DESC LIMIT 60")
        r["timeline"] = [
            {"at": x["ran_at"], "patterns": x["patterns_count"] or 0,
             "hits": x["hits_total"] or 0,
             "llm_saved_1d": x["llm_saved_1d"]}
            for x in reversed(_tl)
        ]
    except Exception:
        r["timeline"] = []

    # ★ 밴딧 생존 지표 — **텔레그램 `/status` 와 같은 파생을 쓴다** (2026-08-07 감사, ③원칙).
    #   종전엔 정지 표시가 `/status` 에만 있었다. 대시보드는 학습이 11일 멈춰 있어도
    #   "LLM 절약 58회" 차트만 보여줬다 — 같은 거짓말이 두 통로에 있는데 한쪽만 고치면
    #   다른 쪽에서 재발한다. 판정은 `bandit.stats()` 단독(사본 금지).
    try:
        from JARVIS07_GUARDIAN.bandit import stats as _bstats
        _b = _bstats()
        r["bandit"] = {
            "arms": _b.get("arm_count", 0),
            "observed_arms": _b.get("observed_arms", 0),
            "last_update_h": _b.get("last_update_h", -1),
            "stalled": bool(_b.get("stalled")),
        }
    except Exception as _be:
        r["bandit"] = {"error": str(_be), "stalled": True}

    # 일별 오류 자동해소율 — '학습이 결과를 바꾸고 있나' 의 최종 지표
    try:
        _fx_sql = _status_sql(_guardian_status_vocab()[1])
        _dr = _rows(con,
            "SELECT substr(timestamp,1,10) d, COUNT(*) tot, "
            f"SUM(CASE WHEN status IN ({_fx_sql}) THEN 1 ELSE 0 END) fx "
            "FROM error_log WHERE timestamp >= date('now','-30 days') "
            "GROUP BY d ORDER BY d")
        r["resolve_rate"] = [
            {"at": x["d"], "total": x["tot"], "resolved": x["fx"],
             "rate": round((x["fx"] * 100.0) / max(x["tot"], 1), 1)}
            for x in _dr
        ]
    except Exception as _re:
        # ★ 빈 배열은 화면에서 '해소 0건' 으로 읽힌다 — 조회 실패와 구분되지 않는다.
        #   실패는 실패라고 말한다(P2 가 4개 엔드포인트에서 고친 것과 같은 병).
        r["resolve_rate"] = []
        r["resolve_rate_error"] = str(_re)

    # 현재 학습 자산 요약 (learned_patterns.json 실시간 조회 — 복사본 금지)
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import all_patterns
        _pats = all_patterns()
        r["patterns_now"] = {
            "count": len(_pats),
            "hits":  sum(int(x.get("hit_count", 0) or 0) for x in _pats),
            "measured": True,
        }
    except Exception as _pe:
        # ★ 0 은 대시보드에서 '학습 자산 없음' 으로 읽힌다 — 학습 원장을 못 읽은 것과
        #   전멸한 것이 같은 화면이 된다. 수치를 주지 않고 사유를 준다.
        r["patterns_now"] = {"error": str(_pe), "measured": False}

    # ★ 글 품질 학습 (ADR 014) — 오류 학습과 *다른 시스템* 이므로 별도 섹션 (ERRORS [480])
    #   라벨만 "학습 패턴" 이라고 쓰면 어느 학습인지 알 수 없다는 지적에 따라 분리.
    try:
        # ★ 학습 루프 4단계를 그대로 보여준다 (ERRORS [481])
        #   ① 지침 축적 → ② 프롬프트 주입 → ③ 성과 채점 → ④ 실제 보상
        #   ★ '주입' 은 반드시 insight_usage(실제 주입 기록)에서 센다.
        #     종전엔 SUM(occurrences)=661 을 '주입' 이라 표시했는데, occurrences 는
        #     "이 지침이 글 분석에서 *재발견* 된 횟수"(①단계)라 완전히 다른 값이다.
        #     실제 주입은 92회였다 — 7배 부풀려 보고 있었다.
        _q = con.execute(
            "SELECT COUNT(*) c, SUM(COALESCE(occurrences,0)) occ, "
            "SUM(COALESCE(reward_count,0)) rc, AVG(weight) w, "
            "AVG(CASE WHEN reward_count > 0 THEN reward_sum / reward_count END) ar, "
            "SUM(CASE WHEN COALESCE(reward_count,0) > 0 THEN 1 ELSE 0 END) rewarded "
            "FROM learning_insights").fetchone()
        _used = con.execute("SELECT COUNT(*) FROM insight_usage").fetchone()
        r["quality_now"] = {
            "insights":   _q["c"] or 0,            # ① 쌓인 지침
            "usage":      (_used[0] if _used else 0),  # ② 실제 주입 (insight_usage)
            "rewards":    _q["rc"] or 0,           # ③ 채점 횟수
            "avg_reward": round(_q["ar"] or 0, 3),  # ④ 평균 보상 = 실제 성과
            "avg_weight": round(_q["w"] or 0, 3),   # (참고) 내부 신뢰도
            "rediscovered": _q["occ"] or 0,        # (참고) 재발견 누계 — '주입' 아님
            "rewarded":   _q["rewarded"] or 0,     # 보상 받은 지침 수
        }
    except Exception:
        r["quality_now"] = {"insights": 0, "usage": 0, "rewards": 0,
                            "avg_reward": 0, "avg_weight": 0, "rediscovered": 0, "rewarded": 0}

    # 품질 지침 누적 추이 (first_seen 일별 → 누적 합산)
    try:
        _qd = _rows(con,
            "SELECT substr(first_seen,1,10) d, COUNT(*) n FROM learning_insights "
            "WHERE first_seen IS NOT NULL GROUP BY d ORDER BY d")
        _acc, _out = 0, []
        for x in _qd:
            _acc += x["n"]
            _out.append({"at": x["d"], "insights": _acc, "added": x["n"]})
        r["quality_timeline"] = _out[-60:]
    except Exception:
        r["quality_timeline"] = []
    try:
        ll = con.execute("SELECT COUNT(*) as cnt, AVG(ABS(actual_views - predicted_opp)) as mae FROM learn_log").fetchone()
        r["learn_log"] = {"cnt": ll["cnt"] if ll else 0, "mae": ll["mae"] if ll else None}
    except Exception:
        r["learn_log"] = {"cnt": 0, "mae": None}
    con.close()
    return r


# ── 피드백 패턴 ──────────────────────────────────────────────────
@app.get("/api/feedback")
def get_feedback(limit: int = 20):
    con = _db()
    if not con: return []
    try:
        rows = _rows(con, "SELECT * FROM feedback_penalty ORDER BY penalty_score DESC LIMIT ?", (limit,))
        con.close()
        return rows
    except Exception:
        con.close()
        return []


# ── 잡 실행 이력 ─────────────────────────────────────────────────
@app.get("/api/jobs")
def get_jobs():
    try:
        # 선언(DEFAULT_JOBS)이 아니라 *실제 등록되는* 명세. 유예는 선행 관계에서 파생된다.
        from JARVIS04_SCHEDULER.job_registry import job_specs
        return job_specs()
    except Exception:
        return []


@app.get("/api/job-runs")
def get_job_runs(owner: Optional[str] = None, days: int = 1, limit: int = 30):
    con = _db()
    if not con: return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        if owner:
            rows = _rows(con, "SELECT * FROM job_runs WHERE owner_agent=? AND started_at>=? ORDER BY started_at DESC LIMIT ?", (owner, cutoff, limit))
        else:
            rows = _rows(con, "SELECT * FROM job_runs WHERE started_at>=? ORDER BY started_at DESC LIMIT ?", (cutoff, limit))
        con.close()
        return rows
    except Exception:
        con.close()
        return []


@app.get("/api/job-last-runs")
def get_job_last_runs():
    con = _db()
    if not con: return []
    try:
        # ★ `MAX(success)` 는 '마지막 실행' 이 아니라 **'한 번이라도 성공했나'** 다
        #   (2026-08-05 교정). 그래서 잡이 오늘 실패해도 과거에 한 번 성공했으면
        #   대시보드는 영원히 초록불이었다 — 발행 결손을 잡 이력에 보정해 넣어도
        #   화면은 그대로였을 것이다.
        #   → 진짜 *마지막 실행 행* 의 결과를 읽는다.
        rows = _rows(con, """
            SELECT r.job_id, r.started_at, r.success, r.error
            FROM job_runs r
            JOIN (SELECT job_id, MAX(started_at) AS m FROM job_runs GROUP BY job_id) t
              ON r.job_id = t.job_id AND r.started_at = t.m
            GROUP BY r.job_id""")
        con.close()
        return rows  # array — frontend LastRun[] expects started_at field
    except Exception:
        con.close()
        return []


@app.get("/api/job-failures")
def get_job_failures(days: int = 7):
    con = _db()
    if not con: return []
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = _rows(con, "SELECT job_id, COUNT(*) as count, MAX(started_at) as last_at FROM job_runs WHERE success=0 AND started_at>=? GROUP BY job_id ORDER BY count DESC LIMIT 20", (cutoff,))
        con.close()
        return rows  # array of {job_id, count, last_at} — matches FailureRow interface
    except Exception:
        con.close()
        return []


# ── 에이전트 capabilities ────────────────────────────────────────
@app.get("/api/capabilities")
def get_capabilities():
    try:
        from shared import capabilities as _caps
        return [{"agent_id": c.agent_id, "intents": getattr(c, "intents", [])} for c in _caps.all_capabilities()]
    except Exception:
        return []


# ── VISION 에이전트 ──────────────────────────────────────────────
@app.get("/api/vision/agents")
def get_vision_agents():
    try:
        import requests as _req
        r = _req.get(f"http://127.0.0.1:{_VISION_PORT}/api/agents", timeout=3)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return []


@app.get("/api/vision/timeline")
def get_vision_timeline(days: int | None = None):
    """에이전트 상태 변화 타임라인 — 대시보드 30일 흐름 차트 (2026-07-27).

    ★ VISION(:8505) 을 프록시한다. 조립 로직은 `collector.get_status_timeline` 단독
      (① 단일 진입점) — 여기서 다시 계산하지 않는다.
    ★ VISION 이 내려가 있으면 **DB 를 직접 읽어** 폴백한다. 상태 이력은 DB 가 원본이고
      VISION 은 그 조회자일 뿐이라, VISION 장애 때 차트가 통째로 비는 것이 더 나쁘다.
    """
    try:
        import requests as _req
        url = f"http://127.0.0.1:{_VISION_PORT}/api/history/timeline"
        r = _req.get(url, params={"days": days} if days else None, timeout=5)
        if r.ok:
            return r.json()
    except Exception:
        pass
    try:
        from JARVIS05_VISION.collector import get_status_timeline
        return get_status_timeline(days)
    except Exception as e:                                  # noqa: BLE001
        return {"days": days or 0, "agents": [], "error": str(e)[:120]}


@app.get("/api/vision/summary")
def get_vision_summary():
    try:
        import requests as _req
        r = _req.get(f"http://127.0.0.1:{_VISION_PORT}/api/metrics/summary", timeout=3)
        if r.ok:
            d = r.json()
            return {
                "total_agents": d.get("total", 0),
                "healthy":      d.get("online", 0),
                "degraded":     d.get("warn", 0),
                "offline":      d.get("offline", 0),
                "health_pct":   d.get("health_pct", 0.0),
            }
    except Exception:
        pass
    return {}


# ── 이미지 통계 ──────────────────────────────────────────────────
def _image_providers() -> dict:
    """이미지 프로바이더 가용 상태 — **실제로 확인** 한다.

    ★ 종전엔 `{"pollinations": True}` 하드코딩이었다. Pollinations 가 죽어도
      화면엔 계속 초록이었고(2026-08-05 실측), 삭제된 뒤에도 True 라고 말했을 것이다.
    """
    try:
        from JARVIS06_IMAGE.providers.cloudflare_provider import provider_available
        return {"cloudflare": bool(provider_available())}
    except Exception:
        return {"cloudflare": False}


@app.get("/api/images")
def get_images():
    out_dir = BASE_DIR / "JARVIS06_IMAGE" / "output"
    total, total_size_mb = 0, 0.0
    by_type: dict = {}
    recent: list = []
    if out_dir.exists():
        files = sorted(
            (f for f in out_dir.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".svg")),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        for f in files:
            total += 1
            ext = f.suffix.lower().lstrip(".")
            by_type[ext] = by_type.get(ext, 0) + 1
            total_size_mb += f.stat().st_size / 1024 / 1024
        recent = [
            {"name": f.name, "mtime": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m/%d %H:%M"), "size_kb": round(f.stat().st_size / 1024, 1), "type": f.suffix.lower().lstrip(".")}
            for f in files[:10]
        ]
    return {"total": total, "by_type": by_type, "total_size_mb": round(total_size_mb, 1), "recent": recent, "providers": _image_providers()}


# ── 발행 도메인 현황 ─────────────────────────────────────────────
@app.get("/api/publish")
def get_publish():
    import re as _re
    _root   = BASE_DIR
    _legacy = _root / "JARVIS02_WRITER"
    from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import (  # noqa: PLC0415
        COOKIE_FILE as nv_cookie)   # ★ 경로 사본 금지 (ERRORS [615])
    nv_ok     = nv_cookie.exists()
    nv_age_h: float | None = None
    if nv_ok:
        nv_age_h = round((datetime.now().timestamp() - nv_cookie.stat().st_mtime) / 3600, 1)
    env_file = _root / ".env"
    ts_ok = False
    try:
        if env_file.exists():
            _et = env_file.read_text(encoding="utf-8")
            ts_ok = bool(_re.search(r"^TS_COOKIE\s*=\s*\S+", _et, _re.MULTILINE))
    except Exception:
        pass
    plat_counts: dict = {}
    con = _db()
    if con:
        try:
            for r in _rows(con, "SELECT platform, COUNT(*) as n FROM posts WHERE date(created_at) >= date('now', '-7 days', 'localtime') GROUP BY platform"):
                plat_counts[r["platform"]] = r["n"]
        except Exception:
            pass
        con.close()
    return {
        "naver": {
            "cookie_ok":       nv_ok,
            "cookie_age_hours": nv_age_h,
            "posts_7d":        plat_counts.get("naver", 0),
        },
        "tistory": {
            "cookie_ok":       ts_ok,
            "cookie_age_hours": None,
            "posts_7d":        plat_counts.get("tistory", 0),
        },
        # 구 필드 호환 (system/page.tsx)
        "naver_cookie_ok":  nv_ok,
        "naver_cookie_age": nv_age_h,
        "ts_cookie_ok":     ts_ok,
        "plat_7d":          plat_counts,
    }


# ── GUARDIAN 오류 ────────────────────────────────────────────────
def _with_error_category(rows: list) -> list:
    """오류 행에 표시용 분류 라벨을 붙인다 — **API 가 오류를 내보내는 유일한 통로** (ERRORS [548]).

    ★ 왜 헬퍼인가: 종전엔 `/api/errors` 한 곳에만 `describe_category` 가 붙어 있고
      `/api/guardian/stats` 의 `recent` 에는 없었다. 대시보드는 두 응답을 **같은 타입**
      (`ErrorRow`)으로 쓰므로, 같은 오류가 화면에 따라 `발행 검증(HarnessFactuality)` 로도
      `HarnessFactuality` 로도 보였다. 부착을 두 곳에 두면 반드시 한쪽이 빠진다(원칙①).
    ★ 라벨 자체는 만들지 않는다 — `severity.describe_category` 파생. 여기서 매핑하면 사본이 된다.
    """
    try:
        from JARVIS07_GUARDIAN.severity import describe_category
    except Exception:
        return rows
    for r in rows:
        try:
            r["error_category"] = describe_category(r.get("error_type", ""))
        except Exception:
            pass
    return rows


# ── 오류 상태 어휘는 GUARDIAN 소유 — 여기서 리터럴로 다시 적지 않는다 (②) ──────
#   ★ 2026-08-14: 종전엔 `status IN ('fixed','resolved')` 가 4곳에 박혀 있었다.
#     `resolved` 는 **쓰는 코드가 0곳** 인데 '자동수정 성공' 에 합산돼 화면 숫자의
#     62%를 차지했다(fixed 115 / resolved 185). 목록을 파생으로 바꾸면 어휘가 바뀔 때
#     화면이 자동으로 따라온다.
def _and(*clauses: str) -> str:
    """빈 조각을 견디는 WHERE 조립기 — 조건이 하나도 없으면 '1=1' (조건 주인은 shared.db)."""
    try:
        from shared.db import and_sql
        return and_sql(*clauses)
    except Exception:
        parts = [c for c in clauses if c and c.strip()]
        return " AND ".join(parts) if parts else "1=1"


def _ts_cut_clause(*mods: str) -> str:
    """`timestamp >= <컷>` 한 조각 — 포맷 주인은 `_ts_cut`."""
    return "timestamp >= " + _ts_cut(*mods)


def _STATUS_NEW() -> str:
    """'아직 아무도 안 본' 상태명 — 주인에서 파생(리터럴 금지)."""
    from JARVIS07_GUARDIAN.architecture import STATUS_NEW
    return str(STATUS_NEW)


def _status_sql(names) -> str:
    """('a','b') → "'a','b'" — IN 절 본문. 어휘의 주인은 architecture 단독."""
    return ",".join("'" + str(n).replace("'", "") + "'" for n in names)


def _guardian_status_vocab():
    """(전체, 자동수정성공, 죽은상태) — 주인에서 파생. **폴백 사본을 두지 않는다.**

    ★ 2026-08-14 (P2) — 종전엔 import 실패 시
      `("new","analyzing","fixed","wontfix","ignored","manual"), ("fixed",), ()` 를
      돌려줬다. 그게 정확히 CLAUDE.md '복사본을 진실로 믿지 말 것' 표의 *스키마를 코드에*
      항목이다: 어휘가 바뀌면 **이 줄만 낡고**, 화면은 옛 정의로 조용히 계속 그려진다.
      게다가 바깥 `except` 가 전부 0 을 돌려주므로 사람 눈에는 "문제 없음" 으로 보였다.
      → 못 읽으면 **올린다**. 호출자가 명시적 오류 페이로드로 바꿔 화면에 드러낸다.
    """
    from JARVIS07_GUARDIAN.architecture import (ALL_STATUSES, FIXED_STATUSES,
                                                LEGACY_STATUSES)
    return tuple(ALL_STATUSES), tuple(FIXED_STATUSES), tuple(LEGACY_STATUSES)


def _syn_excl() -> str:
    """관측용 합성 행 배제 조건 — 조건의 주인은 `shared.db` 단독(사본 금지)."""
    try:
        from shared.db import synthetic_exclusion_sql
        return synthetic_exclusion_sql()
    except Exception:
        return ""


def _status_bucket_sql(names) -> str:
    """상태별 카운트 SELECT 조각을 **어휘에서 생성** — 상태명을 손으로 적지 않는다.

    새 상태가 `architecture.ALL_STATUSES` 에 추가되면 이 화면이 자동으로 따라온다
    (종전엔 SUM(CASE ...) 12줄이 손으로 적혀 있어 새 상태가 조용히 누락됐다).
    """
    bad = [n for n in names if not str(n).isidentifier()]
    if bad:
        # 조용히 넘기면 그 상태만 화면에서 사라진다 — 올려서 오류 페이로드로 드러낸다.
        raise ValueError(f"SQL 별칭으로 쓸 수 없는 상태명: {bad}")
    return ", ".join(
        f"SUM(CASE WHEN status='{n}' THEN 1 ELSE 0 END) AS st_{n}" for n in names)


def _vocab_error_payload(e: Exception, extra: dict) -> dict:
    """어휘 파생·집계 실패를 **0 이 아니라 오류로** 돌려준다 (0 은 '문제 없음' 으로 읽힌다)."""
    out = {"error": f"{type(e).__name__}: {e}"[:300], "measured": False}
    out.update(extra)
    return out


@app.get("/api/guardian/stats")
def get_guardian_stats():
    try:
        con = _get_db()
        if not con:
            raise RuntimeError("DB 연결 불가")
        _all, _fixed, _legacy = _guardian_status_vocab()
        _excl = _syn_excl()
        row = con.execute(f"""
            SELECT
                SUM(CASE WHEN status IN ({_status_sql(_all)}) THEN 1 ELSE 0 END) AS total,
                {_status_bucket_sql(_all)},
                SUM(CASE WHEN status IN ({_status_sql(_fixed)}) THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status IN ({_status_sql(_legacy) or "''"}) THEN 1 ELSE 0 END) AS legacy_cnt,
                SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit_cnt,
                SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high_cnt,
                SUM(CASE WHEN severity='medium'   THEN 1 ELSE 0 END) AS med_cnt,
                SUM(CASE WHEN severity='low'      THEN 1 ELSE 0 END) AS low_cnt
            FROM error_log
            WHERE """ + _and(_ts_cut_clause('-7 days'), _excl) + """
        """).fetchone()
        recent = _with_error_category(
            [dict(r) for r in con.execute(
                "SELECT id, timestamp, severity, status, error_type, module, message "
                "FROM error_log WHERE " + _and(_excl) + " ORDER BY id DESC LIMIT 10").fetchall()])
        con.close()
        out = {n: (row[f"st_{n}"] or 0) for n in _all}      # 상태별 — 어휘에서 생성
        out.update({
            "total": row["total"] or 0,
            "fixed": row["fixed_cnt"] or 0,
            # legacy = 쓰기 코드가 없는 옛 상태(resolved). 총계엔 남기고 '자동수정' 엔 안 넣는다.
            "legacy": row["legacy_cnt"] or 0,
            "critical": row["crit_cnt"] or 0, "high": row["high_cnt"] or 0,
            "medium": row["med_cnt"] or 0, "low": row["low_cnt"] or 0,
            "recent": recent, "measured": True,
        })
        return out
    except Exception as e:
        # ★ 0 을 돌려주지 않는다 — 0 은 화면에서 '문제 없음' 으로 읽힌다 (2026-08-14 P2).
        return _vocab_error_payload(e, {"recent": []})


@app.get("/api/guardian/alltime")
def get_guardian_alltime():
    try:
        con = _get_db()
        _all, _fixed, _legacy = _guardian_status_vocab()
        _excl = _syn_excl()
        r = con.execute(f"""
            SELECT COUNT(*) AS total,
                {_status_bucket_sql(_all)},
                SUM(CASE WHEN status IN ({_status_sql(_fixed)})    THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status IN ({_status_sql(_legacy) or "''"}) THEN 1 ELSE 0 END) AS legacy_cnt,
                MIN(timestamp) AS first_seen
            FROM error_log WHERE """ + _and(_excl) + """
        """).fetchone()
        con.close()
        out = {n: (r[f"st_{n}"] or 0) for n in _all}
        out.update({"total": r["total"] or 0, "fixed": r["fixed_cnt"] or 0,
                    "legacy": r["legacy_cnt"] or 0,
                    "first": (r["first_seen"] or "")[:10], "measured": True})
        return out
    except Exception as e:
        return _vocab_error_payload(e, {"first": ""})


@app.get("/api/errors")
def get_errors(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    days: int = 30,
    limit: int = 200,
):
    try:
        con = _get_db()
        where = [f"timestamp >= {_ts_cut(f'-{days} days')}"]
        params: list = []
        if status:
            where.append("status = ?"); params.append(status)
        if severity:
            where.append("severity = ?"); params.append(severity)
        w = " AND ".join(where)
        rows = [dict(r) for r in con.execute(
            f"SELECT id, timestamp, source, module, func_name, error_type, message, traceback, severity, status, resolution, fixed_file, fixed_at, seen_count FROM error_log WHERE {w} ORDER BY id DESC LIMIT {limit}",
            params,
        ).fetchall()]
        con.close()
        return _with_error_category(rows)
    except Exception:
        return []


@app.get("/api/guardian/history")
def get_guardian_history(days: int = 30, limit: int = 40, actor: str = ""):
    """수리 이력 — 조립은 JARVIS07 `repair_history` 단독. 여기는 위임만."""
    try:
        from JARVIS07_GUARDIAN.repair_history import history, SLOT_LABELS
        # slots 를 함께 내려 화면이 라벨을 하드코딩하지 않게 한다 (② 동적 설계).
        return {"items": history(days=days, limit=limit, actor=actor),
                "slots": SLOT_LABELS}
    except Exception as e:
        return {"error": str(e)[:300], "items": [], "slots": {}}


@app.get("/api/guardian/trend")
def get_guardian_trend(days: int = 14):
    try:
        con = _get_db()
        _TSCUT = _ts_cut(f"-{days} days")
        rows = con.execute(f"""
            SELECT DATE(timestamp, 'localtime') AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high,
                   SUM(CASE WHEN status IN ({_status_sql(_guardian_status_vocab()[1])}) THEN 1 ELSE 0 END) AS fixed
            FROM error_log
            WHERE {_and(f"timestamp >= {_TSCUT}", _syn_excl())}
            GROUP BY day ORDER BY day
        """).fetchall()
        con.close()
        return [{"day": r[0], "total": r[1], "crit": r[2], "high": r[3], "fixed": r[4]} for r in rows]
    except Exception as e:
        return _vocab_error_payload(e, {"items": []})


@app.get("/api/guardian/sources")
def get_guardian_sources(days: int = 7):
    try:
        con = _get_db()
        _TSCUT = _ts_cut(f"-{days} days")
        rows = con.execute(f"""
            SELECT source, COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN status IN ({_status_sql(_guardian_status_vocab()[1])}) THEN 1 ELSE 0 END) AS fixed,
                   SUM(CASE WHEN status='{_STATUS_NEW()}'        THEN 1 ELSE 0 END) AS new_cnt
            FROM error_log
            WHERE {_and(f"timestamp >= {_TSCUT}", _syn_excl())}
            GROUP BY source ORDER BY total DESC LIMIT 10
        """).fetchall()
        con.close()
        return [{"source": r[0], "total": r[1], "crit": r[2], "fixed": r[3], "new": r[4]} for r in rows]
    except Exception as e:
        return _vocab_error_payload(e, {"items": []})


@app.get("/api/repairs")
def get_repairs(limit: int = 30):
    con = _db()
    if not con: return []
    try:
        rows = _rows(con, "SELECT * FROM self_repair_runs ORDER BY id DESC LIMIT ?", (limit,))
        con.close()
        return rows
    except Exception:
        con.close()
        return []


@app.get("/api/tokens")
def get_tokens(days: int = 8):
    """LLM 토큰 사용량 현황 — 집계는 shared/token_usage 단일 진입점 위임.

    totals(트랜스크립트 총량) + by_alias(라이브 계기 내역) + rate_limits + health.
    """
    try:
        from shared.token_usage import summary
        return summary(days=days)
    except Exception as e:
        return {"error": str(e)[:300], "totals": {"available": False},
                "by_alias": [], "recent_calls": [], "rate_limits": [],
                "health": {"state": "집계 실패"}}


@app.get("/api/patterns")
def get_patterns():
    # ★ 2026-07-27 — 종전 `list(data.values())` 는 {"version":"1.0","patterns":[...]} 를
    #   **["1.0", [...]]** 로 만들어 반환했다(문자열이 첫 원소로 섞임). 경로 사본을 들고
    #   직접 파싱하다 생긴 사고 — 조회는 pattern_fixer 단일 진입점으로 (① 단일 진입점).
    try:
        from JARVIS07_GUARDIAN.pattern_fixer import all_patterns
        return all_patterns()
    except Exception:
        return []


# ── DB 통계 ──────────────────────────────────────────────────────
@app.get("/api/db")
def get_db_stats():
    result = {"size_mb": 0.0, "tables": [], "backup_files": [], "total_rows": 0, "wal_exists": False}
    if DB_PATH.exists():
        result["size_mb"] = round(DB_PATH.stat().st_size / 1024 / 1024, 2)
        result["wal_exists"] = (DB_PATH.parent / (DB_PATH.name + "-wal")).exists()
    backup_dir = BASE_DIR / "shared" / "backups"
    if backup_dir.exists():
        for bf in sorted(backup_dir.glob("jarvis_*.sqlite"), reverse=True)[:10]:
            result["backup_files"].append({"name": bf.name, "size_mb": round(bf.stat().st_size / 1024 / 1024, 2), "mtime": datetime.fromtimestamp(bf.stat().st_mtime).strftime("%Y-%m-%d")})
    con = _db()
    if not con:
        return result
    try:
        tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        today  = datetime.now().strftime("%Y-%m-%d")
        for t in tables:
            name = t[0]
            try:
                cnt = con.execute(f"SELECT COUNT(*) FROM [{name}]").fetchone()[0]
            except Exception:
                cnt = 0
            last_write = "—"
            for col in ["created_at","recorded_at","timestamp","updated_at","logged_at","ran_at","indexed_at","reviewed_at"]:
                try:
                    row = con.execute(f"SELECT MAX([{col}]) FROM [{name}]").fetchone()
                    if row and row[0]:
                        last_write = str(row[0])[:16]
                        break
                except Exception:
                    continue
            today_cnt = 0
            for col in ["created_at","recorded_at","timestamp","logged_at","ran_at","indexed_at"]:
                try:
                    row = con.execute(f"SELECT COUNT(*) FROM [{name}] WHERE date([{col}])=?", (today,)).fetchone()
                    if row:
                        today_cnt = row[0]
                        break
                except Exception:
                    continue
            result["tables"].append({"name": name, "rows": cnt, "last_write": last_write, "today_rows": today_cnt})
            result["total_rows"] += cnt
        con.close()
    except Exception:
        pass
    return result


# ── 홈 요약 (Overview) ───────────────────────────────────────────
@app.get("/api/overview")
def get_overview():
    """홈 탭용 종합 요약 — 한 번의 요청으로 핵심 KPI 전체."""
    daemon  = get_daemon()
    posts   = get_posts()
    trends  = get_trends()
    gs      = get_guardian_stats()
    vision  = get_vision_summary()
    return {
        "daemon":   daemon,
        "posts":    posts,
        "trends":   trends,
        "guardian": gs,
        "vision":   vision,
        "ts":       datetime.now().isoformat(),
    }


@app.get("/api/pipeline/activity")
def get_pipeline_activity():
    """실시간 파이프라인 활동 상태 — active 엣지 ID + 동적 flow(실제 쌍) + busy 에이전트 (2초 폴링용).

    ★ flows (사용자 박제 2026-07-19): [{from,to,label}] — 고정 엣지로 표현 못 하는 실제 상호작용
    쌍(예: J07→J06 수정). 프론트가 두 끝점 노드 활성화 + 기존 양방향 엣지 경로를 통째로 점등한다.
    """
    import time as _t
    try:
        from shared.pipeline_activity import get_active, get_busy_agents, get_active_flows
        return {"active": get_active(), "flows": get_active_flows(),
                "busy": get_busy_agents(), "ts": _t.time()}
    except Exception:
        return {"active": [], "flows": [], "busy": {}, "ts": _t.time()}


@app.get("/api/pipeline/log")
def get_pipeline_log():
    """파이프라인 현황 로그 — 최신 60개 이벤트 반환 (5초 폴링용)."""
    try:
        from shared.pipeline_activity import get_activity_log
        return {"log": get_activity_log()}
    except Exception:
        return {"log": []}


@app.get("/api/graph")
def get_pipeline_graph():
    """파이프라인 그래프 — 에이전트·연결·범례 전부 반환.

    단일 진실 소스: shared/pipeline_graph.py
    새 에이전트·연결 추가 시 이 파일만 수정하면 대시보드·로그·잡매핑 자동 반영.
    """
    try:
        from shared.pipeline_graph import AGENTS, PIPELINE_EDGES, LEGEND, LAYOUT
        return {"agents": AGENTS, "edges": PIPELINE_EDGES, "legend": LEGEND, "layout": LAYOUT}
    except ImportError:
        return {"agents": [], "edges": [], "legend": [], "layout": {}}


# ══════════════════════════════════════════════════════════════════
# 실행 진입점
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import uvicorn
    # ★ 루프백 고정 (2026-07-30 전수 감사 3위 — 사용자 승인)
    #   종전 `0.0.0.0` 은 *모든 네트워크 인터페이스* 에 붙는다 = 같은 공유기에 있는 아무
    #   기기나 접속 가능. 그런데 이 API 에는 인증이 **한 줄도 없고**(`Depends`/토큰/세션 0건),
    #   `/api/errors` 는 `message`·`traceback` 을 **원문 그대로** 반환한다.
    #   그 원문에는 텔레그램 봇 토큰이 평문으로 들어 있었다(실측 119행) —
    #   즉 "같은 와이파이 → 대시보드 → 오류 목록 → 봇 토큰" 경로가 열려 있었다.
    #   봇 토큰은 ADR 004 승인 게이트의 자격증명 그 자체다.
    #   대시보드·Next.js 는 모두 이 맥에서만 접속하므로 루프백으로 묶어도 기능 손실 0.
    #   ※ 다른 기기에서 봐야 할 일이 생기면 인증을 먼저 붙이고 풀 것 — 순서를 바꾸지 말 것.
    _host = os.getenv("HUB_API_HOST", "127.0.0.1")
    uvicorn.run("api_server:app", host=_host, port=9198, reload=False)
