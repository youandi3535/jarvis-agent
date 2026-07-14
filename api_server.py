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
    from shared.db import DB_PATH, get_db as _get_db
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
        from JARVIS09_COLLECTOR.collect_theme import _fetch_naver_theme_catalog
        catalog: dict = _fetch_naver_theme_catalog()   # {테마명: 테마번호}
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
@app.get("/api/performance")
def get_performance():
    con = _db()
    if not con:
        return {"total_views": 0, "top_posts": [], "platform_views": {}, "naver_ranked": [], "history": []}
    try:
        total   = _scalar(con, "SELECT COALESCE(SUM(current_views),0) FROM post_analysis")
        try:
            top = _rows(con, "SELECT platform,title,current_views,naver_rank,created_at FROM post_analysis WHERE current_views>0 ORDER BY current_views DESC LIMIT 15")
        except Exception:
            top = _rows(con, "SELECT platform,title,current_views,NULL as naver_rank,created_at FROM post_analysis WHERE current_views>0 ORDER BY current_views DESC LIMIT 15")
        by_plat = _rows(con, "SELECT platform,COALESCE(SUM(current_views),0) as views FROM post_analysis GROUP BY platform")
        try:
            naver_r = _rows(con, "SELECT title,naver_rank,current_views,created_at FROM post_analysis WHERE naver_rank IS NOT NULL ORDER BY naver_rank ASC LIMIT 10")
        except Exception:
            naver_r = []
        hist = _rows(con, "SELECT date(created_at) as d, COALESCE(SUM(current_views),0) as v FROM post_analysis WHERE date(created_at) >= date('now','-7 days') GROUP BY d ORDER BY d")
        con.close()
        return {
            "total_views":    total,
            "top_posts":      top,
            "platform_views": {r["platform"]: r["views"] for r in by_plat},
            "naver_ranked":   naver_r,
            "history":        hist,
        }
    except Exception:
        try: con.close()
        except Exception: pass
        return {"total_views": 0, "top_posts": [], "platform_views": {}, "naver_ranked": [], "history": []}


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
        r["weights"] = [
            {
                "weight_type":    "ridge",
                "weights_json":   json.dumps({"w_trend": x["w_trend"], "w_perf": x["w_perf"], "w_fresh": x["w_fresh"], "w_velocity": x["w_velocity"], "w_competition": x["w_competition"], "intercept": x["intercept"]}, ensure_ascii=False),
                "trained_at":     x["learned_at"],
                "backtest_score": x["r2"],
            }
            for x in w
        ]
    except Exception:
        r["weights"] = []
    try:
        bt = _rows(con, "SELECT tested_at,n_samples,r2,mse,mape FROM backtest_history ORDER BY tested_at DESC LIMIT 14")
        r["backtest"] = [{"tested_at": x["tested_at"], "backtest_type": "regression", "score": x["r2"], "details": f"n={x['n_samples']}, MSE={x['mse']:.3f}"} for x in bt]
    except Exception:
        r["backtest"] = []
    try:
        r["insights"] = _rows(con, "SELECT insight_key,insight_type,description,directive,weight,scope,occurrences,last_seen FROM learning_insights ORDER BY occurrences DESC LIMIT 20")
    except Exception:
        r["insights"] = []
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
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
        return DEFAULT_JOBS
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
        rows = _rows(con, "SELECT job_id, MAX(started_at) as started_at, MAX(success) as success FROM job_runs GROUP BY job_id")
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
    return {"total": total, "by_type": by_type, "total_size_mb": round(total_size_mb, 1), "recent": recent, "providers": {"pollinations": True}}


# ── 발행 도메인 현황 ─────────────────────────────────────────────
@app.get("/api/publish")
def get_publish():
    import re as _re
    _root   = BASE_DIR
    _legacy = _root / "JARVIS02_WRITER"
    nv_cookie = _legacy / "naver_cookies.pkl"
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
@app.get("/api/guardian/stats")
def get_guardian_stats():
    try:
        con = _get_db()
        if not con:
            raise Exception("no db")
        row = con.execute("""
            SELECT
                SUM(CASE WHEN status IN ('new','analyzing','fixed','resolved','wontfix','ignored','manual') THEN 1 ELSE 0 END) AS total,
                SUM(CASE WHEN status='new'       THEN 1 ELSE 0 END) AS new_cnt,
                SUM(CASE WHEN status='analyzing' THEN 1 ELSE 0 END) AS analyzing_cnt,
                SUM(CASE WHEN status IN ('fixed','resolved') THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status='wontfix'   THEN 1 ELSE 0 END) AS wontfix_cnt,
                SUM(CASE WHEN status='ignored'   THEN 1 ELSE 0 END) AS ignored_cnt,
                SUM(CASE WHEN status='manual'    THEN 1 ELSE 0 END) AS manual_cnt,
                SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit_cnt,
                SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high_cnt,
                SUM(CASE WHEN severity='medium'   THEN 1 ELSE 0 END) AS med_cnt,
                SUM(CASE WHEN severity='low'      THEN 1 ELSE 0 END) AS low_cnt
            FROM error_log
            WHERE timestamp >= datetime('now', '-7 days')
        """).fetchone()
        recent = [dict(r) for r in con.execute("SELECT id, timestamp, severity, status, error_type, module, message FROM error_log ORDER BY id DESC LIMIT 10").fetchall()]
        con.close()
        return {
            "total": row["total"] or 0, "new": row["new_cnt"] or 0,
            "analyzing": row["analyzing_cnt"] or 0, "fixed": row["fixed_cnt"] or 0,
            "wontfix": row["wontfix_cnt"] or 0, "ignored": row["ignored_cnt"] or 0,
            "manual": row["manual_cnt"] or 0, "critical": row["crit_cnt"] or 0,
            "high": row["high_cnt"] or 0, "medium": row["med_cnt"] or 0,
            "low": row["low_cnt"] or 0, "recent": recent,
        }
    except Exception:
        return {"total": 0, "new": 0, "analyzing": 0, "fixed": 0, "wontfix": 0, "ignored": 0, "manual": 0, "critical": 0, "high": 0, "medium": 0, "low": 0, "recent": []}


@app.get("/api/guardian/alltime")
def get_guardian_alltime():
    try:
        con = _get_db()
        r = con.execute("""
            SELECT COUNT(*) AS total,
                SUM(CASE WHEN status='new'                   THEN 1 ELSE 0 END) AS new_cnt,
                SUM(CASE WHEN status IN ('fixed','resolved') THEN 1 ELSE 0 END) AS fixed_cnt,
                SUM(CASE WHEN status='manual'                THEN 1 ELSE 0 END) AS manual_cnt,
                SUM(CASE WHEN status='wontfix'               THEN 1 ELSE 0 END) AS wontfix_cnt,
                SUM(CASE WHEN status='ignored'               THEN 1 ELSE 0 END) AS ignored_cnt,
                MIN(timestamp) AS first_seen
            FROM error_log
        """).fetchone()
        con.close()
        return {"total": r["total"] or 0, "new": r["new_cnt"] or 0, "fixed": r["fixed_cnt"] or 0, "manual": r["manual_cnt"] or 0, "wontfix": r["wontfix_cnt"] or 0, "ignored": r["ignored_cnt"] or 0, "first": (r["first_seen"] or "")[:10]}
    except Exception:
        return {"total": 0, "new": 0, "fixed": 0, "manual": 0, "wontfix": 0, "ignored": 0, "first": ""}


@app.get("/api/errors")
def get_errors(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    days: int = 30,
    limit: int = 200,
):
    try:
        con = _get_db()
        where = [f"timestamp >= datetime('now', '-{days} days', 'localtime')"]
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
        return rows
    except Exception:
        return []


@app.get("/api/guardian/trend")
def get_guardian_trend(days: int = 14):
    try:
        con = _get_db()
        rows = con.execute(f"""
            SELECT DATE(timestamp, 'localtime') AS day,
                   COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN severity='high'     THEN 1 ELSE 0 END) AS high,
                   SUM(CASE WHEN status='fixed'      THEN 1 ELSE 0 END) AS fixed
            FROM error_log
            WHERE timestamp >= datetime('now', '-{days} days', 'localtime')
            GROUP BY day ORDER BY day
        """).fetchall()
        con.close()
        return [{"day": r[0], "total": r[1], "crit": r[2], "high": r[3], "fixed": r[4]} for r in rows]
    except Exception:
        return []


@app.get("/api/guardian/sources")
def get_guardian_sources(days: int = 7):
    try:
        con = _get_db()
        rows = con.execute(f"""
            SELECT source, COUNT(*) AS total,
                   SUM(CASE WHEN severity='critical' THEN 1 ELSE 0 END) AS crit,
                   SUM(CASE WHEN status='fixed'      THEN 1 ELSE 0 END) AS fixed,
                   SUM(CASE WHEN status='new'        THEN 1 ELSE 0 END) AS new_cnt
            FROM error_log
            WHERE timestamp >= datetime('now', '-{days} days', 'localtime')
            GROUP BY source ORDER BY total DESC LIMIT 10
        """).fetchall()
        con.close()
        return [{"source": r[0], "total": r[1], "crit": r[2], "fixed": r[3], "new": r[4]} for r in rows]
    except Exception:
        return []


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


@app.get("/api/patterns")
def get_patterns():
    try:
        patterns_file = BASE_DIR / "JARVIS07_GUARDIAN" / "learned_patterns.json"
        if patterns_file.exists():
            data = json.loads(patterns_file.read_text())
            return data if isinstance(data, list) else list(data.values())
    except Exception:
        pass
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
    """실시간 파이프라인 활동 상태 — 현재 active 엣지 ID 목록 반환 (2초 폴링용)."""
    import time as _t
    try:
        from shared.pipeline_activity import get_active
        return {"active": get_active(), "ts": _t.time()}
    except Exception:
        return {"active": [], "ts": _t.time()}


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
    uvicorn.run("api_server:app", host="0.0.0.0", port=9198, reload=False)
