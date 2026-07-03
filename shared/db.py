"""
JARVIS 공유 데이터베이스
모든 에이전트가 읽고 쓰는 단일 SQLite — 기본: jarvis-agent/shared/jarvis.sqlite
★ JARVIS_DB_PATH 환경변수로 경로 오버라이드 가능.
  예) ~/.env: JARVIS_DB_PATH=/Users/kimhyojung/.jarvis/jarvis.sqlite
  → 프로젝트 밖에 두면 Claude Code VM FUSE 마운트 밖 → .fuse_hidden* 생성 차단.
"""
import os, sqlite3, json, shutil
from pathlib import Path
from datetime import datetime, date, timedelta

# ★ .env 자가 로드 (단일 진입점 — db.py 가 import 순서와 무관하게 JARVIS_DB_PATH 를 항상 해석).
#   미로드 시 standalone 호출(검증 one-liner·.env 미로드 프로세스)이 기본 경로로 떨어져
#   *잔재 shared/jarvis.sqlite* 가 생기던 근본 원인 차단 (사용자 박제 2026-06-28).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

_default_db = Path(__file__).parent / "jarvis.sqlite"
DB_PATH     = Path(os.environ.get("JARVIS_DB_PATH", str(_default_db)))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
BACKUP_DIR  = Path(__file__).parent / "backups"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # 다중 에이전트 동시 접근 허용
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript("""
            -- 트렌드 수집 원본
            CREATE TABLE IF NOT EXISTS trends (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT NOT NULL,
                keyword          TEXT NOT NULL,
                sector           TEXT,
                score            INTEGER DEFAULT 0,
                opportunity_score REAL DEFAULT 0,
                source           TEXT DEFAULT 'google',
                created_at       TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_trends_date ON trends(date);

            -- RADAR→WRITER 파이프라인 큐
            CREATE TABLE IF NOT EXISTS pipeline (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                theme             TEXT NOT NULL,
                sector            TEXT,
                opportunity_score REAL DEFAULT 0,
                status            TEXT DEFAULT 'suggested',
                source            TEXT DEFAULT 'radar',
                created_at        TEXT DEFAULT (datetime('now','localtime')),
                processed_at      TEXT
            );

            -- 발행된 포스트 이력
            CREATE TABLE IF NOT EXISTS posts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                theme      TEXT NOT NULL,
                platform   TEXT DEFAULT 'all',
                status     TEXT DEFAULT 'published',
                source     TEXT DEFAULT 'scheduled',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 일별 블로그 조회수
            CREATE TABLE IF NOT EXISTS performance (
                date          TEXT PRIMARY KEY,
                naver_views   INTEGER,
                tistory_views INTEGER,
                updated_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 키워드별 누적 성과 (ANALYST)
            CREATE TABLE IF NOT EXISTS keyword_performance (
                keyword     TEXT PRIMARY KEY,
                post_count  INTEGER DEFAULT 0,
                best_views  INTEGER DEFAULT 0,
                avg_views   REAL    DEFAULT 0,
                last_used   TEXT
            );

            -- 에이전트 이벤트 로그 (감사 추적)
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source     TEXT NOT NULL,
                payload    TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 발행 글 상세 (분석/재발행용)
            CREATE TABLE IF NOT EXISTS post_analysis (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                platform         TEXT NOT NULL,          -- naver / tistory
                theme            TEXT NOT NULL,
                title            TEXT,
                url              TEXT,
                original_content TEXT,                  -- 평문 본문 (분석용)
                original_html    TEXT,                  -- 원본 HTML (재발행용)
                suggestions      TEXT DEFAULT '[]',     -- JSON: [{type,field,issue,before,after,priority}]
                status           TEXT DEFAULT 'pending_analysis',
                -- pending_analysis → analyzed → pending_approval → approved/rejected → revised
                revision_patch   TEXT DEFAULT '{}',     -- 승인된 수정 내용 JSON
                is_revised       INTEGER DEFAULT 0,     -- 루프 가드: 재발행된 글은 재분석 대상 제외
                created_at       TEXT DEFAULT (datetime('now','localtime')),
                analyzed_at      TEXT,
                decided_at       TEXT,
                revised_at       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pa_status   ON post_analysis(status);
            CREATE INDEX IF NOT EXISTS idx_pa_platform ON post_analysis(platform);

            -- 사용자 즐겨찾기 키워드 (대시보드 watch list)
            CREATE TABLE IF NOT EXISTS keyword_favorites (
                keyword   TEXT PRIMARY KEY,
                note      TEXT DEFAULT '',
                added_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 사용자 설정 (key-value 저장소: 알림 임계치, UI 테마 등)
            CREATE TABLE IF NOT EXISTS user_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 브랜드 보이스 학습 코퍼스 (과거 발행 글 + 임베딩)
            CREATE TABLE IF NOT EXISTS style_corpus (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id     INTEGER UNIQUE,        -- post_analysis.id
                platform      TEXT,
                title         TEXT,
                content       TEXT,                  -- 평문 (검색 결과 표시용)
                excerpt       TEXT,                  -- 첫 800자 (few-shot 주입용)
                embedding     BLOB,                  -- numpy float32 array
                embed_model   TEXT,                  -- 모델 식별자
                embed_dim     INTEGER,               -- 차원수
                char_count    INTEGER DEFAULT 0,
                published_at  TEXT,
                views         INTEGER DEFAULT 0,
                indexed_at    TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_sc_platform ON style_corpus(platform);

            -- ─── 자가학습 백본 ───────────────────────────────────
            -- (예측, 실측) 페어 — 매일 적재 → 주별 회귀학습 입력
            CREATE TABLE IF NOT EXISTS learn_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword       TEXT,
                sector        TEXT,
                platform      TEXT,
                trend_score   REAL,
                perf_boost    REAL,
                freshness     REAL,
                velocity      REAL,
                competition   REAL,
                predicted_opp REAL,
                actual_views  INTEGER,
                days_after    INTEGER,
                logged_at     TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(keyword, platform, days_after)
            );
            CREATE INDEX IF NOT EXISTS idx_ll_logged ON learn_log(logged_at);

            -- 학습된 가중치 — 주별 갱신, 최신 row 사용
            CREATE TABLE IF NOT EXISTS learned_weights (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                w_trend       REAL,
                w_perf        REAL,
                w_fresh       REAL,
                w_velocity    REAL,
                w_competition REAL,
                intercept     REAL,
                n_samples     INTEGER,
                r2            REAL,
                mse           REAL,
                learned_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 사용자 승인/거부 누적 페널티
            CREATE TABLE IF NOT EXISTS feedback_penalty (
                target        TEXT PRIMARY KEY,  -- 'sector:전기차' / 'kw:테슬라'
                rejected      INTEGER DEFAULT 0,
                approved      INTEGER DEFAULT 0,
                penalty       REAL DEFAULT 0,
                updated_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 키워드 임베딩 — cold-start 일반화
            CREATE TABLE IF NOT EXISTS keyword_embeddings (
                keyword       TEXT PRIMARY KEY,
                embedding     BLOB,
                embed_model   TEXT,
                embed_dim     INTEGER,
                indexed_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 백테스트 이력 — 주별 정확도 추이
            CREATE TABLE IF NOT EXISTS backtest_history (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                n_samples     INTEGER,
                r2            REAL,
                mse           REAL,
                mape          REAL,        -- mean absolute percentage error
                tested_at     TEXT DEFAULT (datetime('now','localtime'))
            );

            -- ─── 일일 종합 분석 ──────────────────────────────────
            -- 매일 22:00 daily_review 잡이 적재. 하루 발행된 모든 글의 통합 분석.
            CREATE TABLE IF NOT EXISTS daily_review (
                review_date    TEXT PRIMARY KEY,    -- 'YYYY-MM-DD'
                posts_count    INTEGER DEFAULT 0,
                platforms_json TEXT DEFAULT '{}',   -- {"naver": n, "tistory": n}
                avg_views      REAL DEFAULT 0,
                top_views      INTEGER DEFAULT 0,
                quality_score  REAL DEFAULT 0,      -- 0~100, suggestions 적용률·중복률 기반
                sector_dist    TEXT DEFAULT '{}',   -- {"금융": 2, "라이프": 1, ...}
                common_issues  TEXT DEFAULT '[]',   -- [{"issue": "...", "count": 3}, ...]
                insights       TEXT DEFAULT '',     -- 자연어 요약
                next_directives TEXT DEFAULT '[]',  -- 다음날 pre_revise 에 주입할 지침 [{"do":"...","why":"..."}]
                reviewed_at    TEXT DEFAULT (datetime('now','localtime'))
            );

            -- 누적 학습 코퍼스 — daily_review 가 발견한 패턴이 누적·강화됨
            CREATE TABLE IF NOT EXISTS learning_insights (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                insight_key   TEXT UNIQUE,            -- 동일 패턴 재발견 시 occurrences 증가
                insight_type  TEXT,                   -- 'avoid' / 'prefer' / 'topic_boost' / 'platform_specific'
                description   TEXT,                   -- 한 줄 설명 (Claude 가 작성)
                directive     TEXT,                   -- 글 작성 시 적용할 구체 지침
                weight        REAL DEFAULT 1.0,       -- 적용 강도 (시간 감쇠 가능)
                occurrences   INTEGER DEFAULT 1,
                first_seen    TEXT DEFAULT (datetime('now','localtime')),
                last_seen     TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_li_type   ON learning_insights(insight_type);
            CREATE INDEX IF NOT EXISTS idx_li_weight ON learning_insights(weight DESC);

            -- ─── JARVIS04 SCHEDULER ─────────────────────────────────
            -- 모든 APScheduler 잡 실행 이력 (JARVIS04 EventListener 가 자동 적재)
            -- job_id: 잡 ID (예: "radar_trends_09")
            -- success: 1/0, error: 예외 메시지 (실패 시)
            -- duration_ms: 실행 소요 시간
            CREATE TABLE IF NOT EXISTS job_runs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id              TEXT NOT NULL,
                job_name            TEXT,
                started_at          TEXT NOT NULL,
                finished_at         TEXT,
                duration_ms         INTEGER,
                success             INTEGER DEFAULT 1,
                error               TEXT,
                scheduled_run_time  TEXT,
                owner_agent         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_job_runs_jid     ON job_runs(job_id);
            CREATE INDEX IF NOT EXISTS idx_job_runs_started ON job_runs(started_at);
            CREATE INDEX IF NOT EXISTS idx_job_runs_owner   ON job_runs(owner_agent);
            CREATE TABLE IF NOT EXISTS tool_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name    TEXT NOT NULL,
                domain       TEXT,
                success      INTEGER DEFAULT 1,
                duration_ms  INTEGER,
                ran_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S','now','localtime')),
                error        TEXT,
                cid          TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tool_runs_name   ON tool_runs(tool_name);
            CREATE INDEX IF NOT EXISTS idx_tool_runs_ran_at ON tool_runs(ran_at);

            -- JARVIS07_GUARDIAN 오류 로그
            CREATE TABLE IF NOT EXISTS error_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                source       TEXT    NOT NULL,
                module       TEXT,
                func_name    TEXT,
                error_type   TEXT,
                message      TEXT,
                traceback    TEXT,
                context      TEXT,
                seen_count   INTEGER DEFAULT 1,
                severity     TEXT    DEFAULT 'medium',
                status       TEXT    DEFAULT 'new',
                resolution   TEXT,
                fixed_file   TEXT,
                fixed_at     TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_error_log_status    ON error_log(status);
            CREATE INDEX IF NOT EXISTS idx_error_log_type      ON error_log(error_type, module);
            CREATE INDEX IF NOT EXISTS idx_error_log_timestamp ON error_log(timestamp);

            -- ★ 자가 진단 회차 메트릭 (사용자 박제 2026-05-15)
            -- "세상에서 가장 똑똑한 에이전트" 학습 곡선 추적
            CREATE TABLE IF NOT EXISTS self_repair_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                model           TEXT    NOT NULL DEFAULT 'opus',
                elapsed_sec     INTEGER NOT NULL DEFAULT 0,
                returncode      INTEGER NOT NULL DEFAULT 0,
                -- 7-Layer 결과 카운트
                syntax_fixed    INTEGER DEFAULT 0,
                rules_fixed     INTEGER DEFAULT 0,
                length_fixed    INTEGER DEFAULT 0,
                quality_fixed   INTEGER DEFAULT 0,
                data_cleaned    INTEGER DEFAULT 0,
                fixers_added    INTEGER DEFAULT 0,
                vision_pinned   INTEGER DEFAULT 0,
                total_fixed     INTEGER DEFAULT 0,
                -- 학습 누적 메트릭
                patterns_count  INTEGER DEFAULT 0,
                hits_total      INTEGER DEFAULT 0,
                llm_saved       INTEGER DEFAULT 0,
                -- 자기 평가 (1-10)
                score_quality   INTEGER DEFAULT 0,
                score_learning  INTEGER DEFAULT 0,
                score_vision    INTEGER DEFAULT 0,
                next_suggestion TEXT,
                summary         TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_srr_ran_at ON self_repair_runs(ran_at);
        """)
        # 기존 DB 마이그레이션 — current_views 컬럼 없으면 추가
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN current_views INTEGER DEFAULT 0")
        except Exception:
            pass  # 이미 존재하면 무시
        # NOTE: retry_count / retry_at / last_error 컬럼은 사후 retry 잡 폐기로 더 이상
        # 사용하지 않음. 기존 DB 에 남아 있어도 무시됨 (drop 하지 않음 — 데이터 보존).
        # source_keyword: RADAR pipeline 에서 발행 트리거 시 채워지는 trends.keyword 와
        # 동일한 raw 키워드. theme 은 표시용(축약/꾸밈), source_keyword 는 학습용 join 키.
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN source_keyword TEXT")
        except Exception:
            pass
        # post_type: 글 종류별 분리 학습용. 'economic' / 'theme' / 자유문자열.
        # NULL 이면 daily_review 가 backfill 로 theme 패턴으로 추론. 새 종류 추가 시
        # 자유문자열로 명시만 하면 자동 그룹 분리됨 (코드 수정 불필요).
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN post_type TEXT")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pa_post_type ON post_analysis(post_type)")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN image_paths TEXT DEFAULT '[]'")
        except Exception:
            pass
        # learning_insights.scope: 어떤 글 종류에 적용할 인사이트인지.
        # 'economic' / 'theme' / 'all'. pre_revise 가 호출 시 scope IN (post_type,'all') 만 주입.
        try:
            conn.execute("ALTER TABLE learning_insights ADD COLUMN scope TEXT DEFAULT 'all'")
        except Exception:
            pass
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_li_scope ON learning_insights(scope)")
        except Exception:
            pass

        # ★ 글 품질 강화학습 (2026-07-03 — ADR 014): 인사이트 주입→성과 보상 귀속 사슬.
        #   learning_insights 에 보상 누적 컬럼 + 주입 사용 기록(insight_usage) 테이블.
        #   엔진 = JARVIS07_GUARDIAN/quality_learner.py (단일 진입점).
        for _mig in (
            "ALTER TABLE learning_insights ADD COLUMN reward_sum REAL DEFAULT 0",
            "ALTER TABLE learning_insights ADD COLUMN reward_count INTEGER DEFAULT 0",
            "ALTER TABLE learning_insights ADD COLUMN last_used_at TEXT",
        ):
            try:
                conn.execute(_mig)
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS insight_usage (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id    TEXT NOT NULL,          -- 같은 글에 함께 주입된 묶음
                insight_id  INTEGER NOT NULL,       -- learning_insights.id
                scope       TEXT DEFAULT 'all',     -- economic / theme / all
                platform    TEXT DEFAULT '',        -- naver / tistory / '' (양쪽)
                theme       TEXT DEFAULT '',
                used_at     TEXT DEFAULT (datetime('now','localtime')),
                analysis_id INTEGER,                -- 보상 귀속된 post_analysis.id
                reward      REAL,                   -- NULL = 미귀속
                rewarded_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_iu_pending ON insight_usage(reward, used_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_iu_insight ON insight_usage(insight_id)")

        # learn_log.naver_rank: 네이버 검색 노출 순위 (1~100, NULL = 미측정).
        # 조회수 외 핵심 학습 신호. 낮을수록 좋음 (1위 = 최상). actual_views 와 함께 적재.
        try:
            conn.execute("ALTER TABLE learn_log ADD COLUMN naver_rank INTEGER")
        except Exception:
            pass

        # post_analysis.naver_rank / naver_rank_at — update_naver_rank() 가 사용
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN naver_rank INTEGER")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN naver_rank_at TEXT")
        except Exception:
            pass

        # keyword_performance — best_rank / avg_rank / composite_score
        # update_keyword_views_from_posts() 가 ON CONFLICT DO UPDATE 에서 사용
        try:
            conn.execute("ALTER TABLE keyword_performance ADD COLUMN best_rank INTEGER")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE keyword_performance ADD COLUMN avg_rank REAL DEFAULT 101")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE keyword_performance ADD COLUMN composite_score REAL DEFAULT 0")
        except Exception:
            pass


# ── Trends ────────────────────────────────────────────────────

def save_trends(date_str: str, scored_keywords: list):
    with get_db() as conn:
        conn.execute("DELETE FROM trends WHERE date = ?", (date_str,))
        conn.executemany(
            "INSERT INTO trends (date, keyword, sector, score, opportunity_score) VALUES (?,?,?,?,?)",
            [
                (date_str, k["keyword"], k.get("sector", ""), k.get("score", 0),
                 k.get("opportunity_score", k.get("score", 0)))
                for k in scored_keywords
            ],
        )


def get_trend_history(days: int = 14) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, keyword, sector, score, opportunity_score FROM trends "
            "ORDER BY date DESC, opportunity_score DESC LIMIT ?",
            (days * 30,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_keyword_trend_history(keyword: str) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, score, opportunity_score FROM trends WHERE keyword = ? ORDER BY date",
            (keyword,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Pipeline ──────────────────────────────────────────────────

def push_pipeline(items: list):
    """RADAR 추천 테마를 파이프라인에 등록. 당일 중복 시 점수 누적."""
    today = date.today().isoformat()
    with get_db() as conn:
        for item in items:
            score = float(item.get("opportunity_score", item.get("score", 0)))
            row = conn.execute(
                "SELECT id, opportunity_score FROM pipeline WHERE theme = ? AND date(created_at) = ?",
                (item["theme"], today),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE pipeline SET opportunity_score = ? WHERE id = ?",
                    (round(row["opportunity_score"] + score, 1), row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO pipeline (theme, sector, opportunity_score) VALUES (?,?,?)",
                    (item["theme"], item.get("sector", ""), score),
                )


def get_todays_pipeline(limit: int = 20) -> list:
    """오늘 날짜 pipeline 항목을 기회점수 내림차순으로 반환 (16시 테마 선택용)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, theme, sector, opportunity_score, created_at FROM pipeline "
            "WHERE status = 'suggested' AND date(created_at) = date('now','localtime') "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_pipeline(limit: int = 5) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, theme, sector, opportunity_score, created_at FROM pipeline "
            "WHERE status = 'suggested' ORDER BY opportunity_score DESC, created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_pipeline_status(item_id: int, status: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE pipeline SET status=?, processed_at=datetime('now','localtime') WHERE id=?",
            (status, item_id),
        )


def get_recent_published_themes(days: int = 30) -> list[dict]:
    """최근 N일 이내 post_analysis 에 발행된 theme 목록 반환.

    RADAR 주제 선정 시 중복 회피에 사용.
    Returns: [{"theme": str, "title": str, "created_at": str}, ...]
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT theme, title, created_at FROM post_analysis "
            "WHERE created_at >= datetime('now', ?, 'localtime') "
            "ORDER BY created_at DESC",
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pipeline_history(limit: int = 50) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, theme, sector, opportunity_score, status, created_at, processed_at "
            "FROM pipeline ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Posts ─────────────────────────────────────────────────────

def save_post(theme: str, platform: str = "all", status: str = "published", source: str = "scheduled"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO posts (theme, platform, status, source) VALUES (?,?,?,?)",
            (theme, platform, status, source),
        )
        conn.execute(
            """INSERT INTO keyword_performance (keyword, post_count, last_used)
               VALUES (?,1,datetime('now','localtime'))
               ON CONFLICT(keyword) DO UPDATE SET
                   post_count = post_count + 1,
                   last_used  = datetime('now','localtime')""",
            (theme,),
        )


def get_post_history(days: int = 30) -> list:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT theme, platform, status, source, created_at FROM posts "
            "WHERE created_at >= ? ORDER BY created_at DESC",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Performance ───────────────────────────────────────────────

def save_performance(date_str: str, naver: int = None, tistory: int = None):
    """플랫폼별 일일 조회수 저장. None인 값은 기존 DB 값을 유지 (덮어쓰지 않음)."""
    with get_db() as conn:
        # 기존 행 확인
        existing = conn.execute(
            "SELECT naver_views, tistory_views FROM performance WHERE date=?",
            (date_str,)
        ).fetchone()
        if existing:
            # None 이면 기존 값 유지
            naver   = naver   if naver   is not None else existing["naver_views"]
            tistory = tistory if tistory is not None else existing["tistory_views"]
        conn.execute(
            """INSERT INTO performance (date, naver_views, tistory_views)
               VALUES (?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                   naver_views   = excluded.naver_views,
                   tistory_views = excluded.tistory_views,
                   updated_at    = datetime('now','localtime')""",
            (date_str, naver, tistory),
        )


def update_keyword_views(date_str: str):
    """당일 성과를 바탕으로 keyword_performance 조회수 업데이트 (ANALYST 핵심)."""
    with get_db() as conn:
        perf = conn.execute(
            "SELECT naver_views, tistory_views FROM performance WHERE date = ?",
            (date_str,),
        ).fetchone()
        if not perf:
            return
        total_views = sum(v or 0 for v in [perf["naver_views"], perf["tistory_views"]])
        posts_today = conn.execute(
            "SELECT theme FROM posts WHERE date(created_at) = ?", (date_str,)
        ).fetchall()
        if not posts_today:
            return
        views_per_post = total_views / len(posts_today)
        for post in posts_today:
            conn.execute(
                """UPDATE keyword_performance SET
                    best_views = CASE WHEN best_views > ? THEN best_views ELSE ? END,
                    avg_views  = (avg_views * (post_count - 1) + ?) / post_count
                   WHERE keyword = ?""",
                (views_per_post, views_per_post, views_per_post, post["theme"]),
            )


def get_keyword_performance(keyword: str) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM keyword_performance WHERE keyword = ?", (keyword,)
        ).fetchone()
    return dict(row) if row else {}


def get_theme_performance_boost(theme: str) -> float:
    """테마명 기준 과거 성과 부스트 반환 (0~30).
    performance_collector가 keyword_performance에 테마명으로 저장한 실측 조회수를
    opportunity_score 계산 시 반영하는 역방향 피드백 핵심 함수.
    avg_views 기준: 1000뷰=10점, 3000뷰=20점, 5000뷰+=30점 (로그 스케일).
    """
    import math
    kp = get_keyword_performance(theme)
    if not kp or not kp.get("avg_views"):
        return 0.0
    avg = float(kp["avg_views"])
    if avg <= 0:
        return 0.0
    boost = min(30.0, math.log1p(avg / 100) * 6.5)
    return round(boost, 1)


# ── Tool Runs (Observability) ─────────────────────────────────

def log_tool_run(tool_name: str, domain: str, success: bool,
                 duration_ms: int, cid: str = None, error: str = None):
    """tool_invoke 호출 결과를 tool_runs 테이블에 기록."""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO tool_runs (tool_name, domain, success, duration_ms, cid, error) "
                "VALUES (?,?,?,?,?,?)",
                (tool_name, domain, 1 if success else 0, duration_ms, cid, error),
            )
    except Exception:
        pass


def get_tool_stats(hours: int = 24) -> list:
    """최근 N시간 도구별 호출 통계 — name/domain/calls/success_rate/avg_ms/max_ms."""
    since = f"datetime('now', 'localtime', '-{hours} hours')"
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT tool_name, domain,
                       COUNT(*) AS calls,
                       ROUND(100.0*SUM(success)/COUNT(*),1) AS success_rate,
                       ROUND(AVG(duration_ms),0) AS avg_ms,
                       MAX(duration_ms) AS max_ms
                FROM tool_runs
                WHERE ran_at >= {since}
                GROUP BY tool_name
                ORDER BY calls DESC""",
        ).fetchall()
    return [dict(r) for r in rows]


# ── Events ────────────────────────────────────────────────────

def log_event(event_type: str, source: str, payload: dict = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO events (event_type, source, payload) VALUES (?,?,?)",
            (event_type, source, json.dumps(payload or {}, ensure_ascii=False)),
        )
        return cur.lastrowid or 0


def get_recent_events(limit: int = 100) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT event_type, source, payload, created_at FROM events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Post Analysis ─────────────────────────────────────────────

def save_post_for_analysis(platform: str, theme: str, title: str,
                            url: str = "",
                            original_content: str = "", original_html: str = "",
                            source_keyword: str = "",
                            post_type: str = "",
                            image_paths: str = "[]") -> int:
    """발행 직후 분석 대기 레코드 생성. 반환값: 생성된 id.

    source_keyword: RADAR pipeline 트리거 시 trends.keyword 와 동일한 raw 키워드.
                    학습 페어링(learn_log)의 join 키로 사용. 비어 있으면 theme fallback.
    post_type:      글 종류별 분리 학습용. 'economic' / 'theme' / 자유문자열.
                    daily_review 가 GROUP BY post_type 으로 분기, learning_insights.scope
                    로 매핑되어 pre_revise 가 같은 종류 글에만 인사이트 주입.
    """
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO post_analysis
               (platform, theme, title, url,
                original_content, original_html, source_keyword, post_type, image_paths)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (platform, theme, title, url,
             original_content, original_html,
             (source_keyword or "").strip(),
             (post_type or "").strip() or None,
             image_paths or "[]"),
        )
        return cur.lastrowid


def get_pending_analysis(limit: int = 10) -> list:
    """분석 대기 중인 글 목록."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM post_analysis WHERE status='pending_analysis' AND is_revised=0 "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def try_claim_analysis(analysis_id: int) -> bool:
    """pending_analysis 상태인 경우에만 analyzing 으로 원자적 변경.
    다른 프로세스가 먼저 선점했으면 False 반환 (중복 실행 방지)."""
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE post_analysis SET status='analyzing' "
            "WHERE id=? AND status='pending_analysis'",
            (analysis_id,),
        )
        return cur.rowcount > 0


def save_analysis_result(analysis_id: int, suggestions: list):
    """분석 결과 저장 → status: analyzed."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET suggestions=?, status='analyzed', "
            "analyzed_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(suggestions, ensure_ascii=False), analysis_id),
        )


def set_analysis_pending_approval(analysis_id: int):
    """텔레그램 전송 완료 후 상태 업데이트."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET status='pending_approval' WHERE id=?",
            (analysis_id,),
        )


def set_partial_selection(analysis_id: int, selected: list) -> list:
    """부분 승인용 선택 인덱스 토글. revision_patch.selected 에 저장. 갱신된 selected 반환."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT revision_patch FROM post_analysis WHERE id=?", (analysis_id,)
        ).fetchone()
        existing = {}
        if row and row["revision_patch"]:
            try:
                existing = json.loads(row["revision_patch"]) or {}
            except Exception:
                existing = {}
        existing["selected"] = list(selected)
        conn.execute(
            "UPDATE post_analysis SET revision_patch=? WHERE id=?",
            (json.dumps(existing, ensure_ascii=False), analysis_id),
        )
    return existing["selected"]


def get_partial_selection(analysis_id: int, default_n: int = 0) -> list:
    """현재 선택된 인덱스 리스트. 없으면 [0..N-1] 전체 선택."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT revision_patch FROM post_analysis WHERE id=?", (analysis_id,)
        ).fetchone()
    if not row:
        return list(range(default_n))
    try:
        patch = json.loads(row["revision_patch"] or "{}")
        sel   = patch.get("selected")
        if isinstance(sel, list):
            return sel
    except Exception:
        pass
    return list(range(default_n))


def get_pending_approval_older_than(hours: int = 1) -> list:
    """N시간 이상 사용자 응답 없는 pending_approval 글 목록 (자동 승인용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM post_analysis
               WHERE status='pending_approval'
                 AND analyzed_at IS NOT NULL
                 AND analyzed_at < datetime('now','localtime',?)
               ORDER BY analyzed_at ASC""",
            (f"-{hours} hours",),
        ).fetchall()
    return [dict(r) for r in rows]


def approve_analysis(analysis_id: int, patch: dict):
    """승인 처리 — revision_patch 저장, status: approved."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET status='approved', revision_patch=?, "
            "decided_at=datetime('now','localtime') WHERE id=?",
            (json.dumps(patch, ensure_ascii=False), analysis_id),
        )


def reject_analysis(analysis_id: int):
    """거부 처리."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET status='rejected', "
            "decided_at=datetime('now','localtime') WHERE id=?",
            (analysis_id,),
        )


def mark_revised(analysis_id: int):
    """재발행 완료 — 루프 가드 플래그 ON."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET status='revised', is_revised=1, "
            "revised_at=datetime('now','localtime') WHERE id=?",
            (analysis_id,),
        )


def save_pre_revise(analysis_id: int, applied_suggestions: list):
    """사전 수정 완료 마킹 — 발행 전에 대본을 자동 패치한 글 표시.
    revision_patch 저장 + status='revised' + is_revised=1 → 사후 분석/수정 큐 자동 skip.
    JARVIS02 jarvis_main.py / economic_poster.py 가 발행 직전 호출.
    """
    patch = json.dumps(
        {"suggestions": applied_suggestions or [], "mode": "pre_revise"},
        ensure_ascii=False,
    )
    with get_db() as conn:
        conn.execute(
            """UPDATE post_analysis
               SET revision_patch=?, status='revised', is_revised=1,
                   analyzed_at=COALESCE(analyzed_at, datetime('now','localtime')),
                   decided_at=COALESCE(decided_at, datetime('now','localtime')),
                   revised_at=datetime('now','localtime')
               WHERE id=?""",
            (patch, analysis_id),
        )


def get_approved_for_revision(limit: int = 5) -> list:
    """재발행 대기 중인 승인 글 — 사용자가 직접 승인 트리거한 건만 처리.

    사후 retry 잡은 폐기됨 (ERRORS.md [14]). 본 함수는 인라인 버튼/대시보드의
    명시적 1회 트리거 직후 호출되는 경로만 가정한다.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM post_analysis
               WHERE status='approved' AND is_revised=0
               ORDER BY decided_at ASC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_analysis_history(limit: int = 50) -> list:
    """대시보드용 전체 분석 이력."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, platform, theme, title, url, suggestions, status, "
            "revision_patch, created_at, analyzed_at, decided_at, revised_at "
            "FROM post_analysis ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_analysis_by_id(analysis_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM post_analysis WHERE id=?", (analysis_id,)
        ).fetchone()
    return dict(row) if row else {}


def get_posts_for_view_collection() -> list:
    """조회수 수집 대상 글 목록 — URL이 있고 발행된 모든 글."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, platform, theme, title, url, current_views,
                      source_keyword
               FROM post_analysis
               WHERE url IS NOT NULL AND url != ''
               ORDER BY created_at DESC LIMIT 100"""
        ).fetchall()
    return [dict(r) for r in rows]


def update_post_views(analysis_id: int, views: int):
    """특정 글의 최신 조회수 업데이트."""
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET current_views=? WHERE id=?",
            (views, analysis_id),
        )


def update_naver_rank(analysis_id: int, rank: int | None):
    """네이버 검색 노출 순위 업데이트 (옵션 B 패치 2026-05-04).

    rank: 1~100 (낮을수록 노출 강함), None = 100위 밖 미노출.
    naver_rank_at 자동 갱신.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE post_analysis SET naver_rank=?, naver_rank_at=datetime('now','localtime') "
            "WHERE id=?",
            (rank, analysis_id),
        )


def update_keyword_views_from_posts():
    """post_analysis 의 current_views + naver_rank 종합 → keyword_performance 학습 업데이트.

    옵션 B 패치 (2026-05-04): rank 가중치 실제 INSERT 까지 반영.
    길1-C 패치 (2026-05-04): GROUP BY 를 source_keyword 우선으로 — 네이버 검색에서 실제
    매칭되는 키워드여야 학습 가치 있음. source_keyword 가 NULL 이면 theme fallback.
    composite_score = avg_views * 1.0 + (101 - avg_rank) * 2.0
        - rank 1 ~ 100 → 102 ~ 2 점 가산
        - rank NULL (미노출) → avg_rank=101 → 0점 가산
        - 즉 노출 안 되면 views 만으로 평가
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT COALESCE(NULLIF(source_keyword, ''), theme) AS keyword,
                      MAX(current_views) as best_v,
                      AVG(current_views) as avg_v,
                      MIN(naver_rank) as best_rank,
                      AVG(CASE WHEN naver_rank IS NULL THEN 101 ELSE naver_rank END) as avg_rank,
                      COUNT(*) as cnt
               FROM post_analysis
               WHERE current_views > 0 OR naver_rank IS NOT NULL
               GROUP BY COALESCE(NULLIF(source_keyword, ''), theme)"""
        ).fetchall()
        for r in rows:
            avg_v    = round(r["avg_v"] or 0, 1)
            best_v   = r["best_v"] or 0
            avg_rank = round(r["avg_rank"] or 101, 1)
            best_rank = r["best_rank"]  # NULL 가능 (모두 미노출)
            composite = round(avg_v * 1.0 + max(0.0, 101 - avg_rank) * 2.0, 1)
            conn.execute(
                """INSERT INTO keyword_performance
                       (keyword, post_count, avg_views, best_views,
                        best_rank, avg_rank, composite_score, last_used)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
                   ON CONFLICT(keyword) DO UPDATE SET
                       post_count       = excluded.post_count,
                       avg_views        = excluded.avg_views,
                       best_views       = CASE WHEN best_views > excluded.best_views
                                          THEN best_views ELSE excluded.best_views END,
                       best_rank        = CASE
                                            WHEN best_rank IS NULL THEN excluded.best_rank
                                            WHEN excluded.best_rank IS NULL THEN best_rank
                                            WHEN best_rank < excluded.best_rank THEN best_rank
                                            ELSE excluded.best_rank
                                          END,
                       avg_rank         = excluded.avg_rank,
                       composite_score  = excluded.composite_score,
                       last_used        = excluded.last_used""",
                (r["keyword"], r["cnt"], avg_v, best_v,
                 best_rank, avg_rank, composite),
            )


def get_best_publish_hour(platform: str) -> int:
    """
    platform별 최적 발행 시간(시) 반환.
    성과 데이터(조회수 있는 글 2개 이상)가 충분할 때만 학습값 반환.
    데이터 부족 시 None 반환 → 호출부에서 "현재 시간에 발행" 처리.
    """
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT CAST(strftime('%H', created_at) AS INTEGER) as hour,
                          AVG(current_views) as avg_views, COUNT(*) as cnt
                   FROM post_analysis
                   WHERE platform=? AND current_views > 0
                   GROUP BY hour HAVING cnt >= 2
                   ORDER BY avg_views DESC LIMIT 1""",
                (platform,),
            ).fetchall()
            if rows:
                return rows[0]["hour"]
    except Exception:
        pass
    return None  # 데이터 부족 — 현재 시간에 발행


def get_recycle_candidates() -> list:
    """
    재활용 후보 글 목록.
    조건: 발행 6개월+ 경과 AND (조회수 상위 30% OR 당시 인기 키워드)
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, platform, theme, title, url, current_views, created_at
               FROM post_analysis
               WHERE created_at < datetime('now', '-6 months', 'localtime')
                 AND is_revised = 0
               ORDER BY current_views DESC
               LIMIT 20"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_today_posts() -> list:
    """오늘 발행된 글 목록 (성과 대시보드용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, platform, theme, title, url, current_views, status, created_at
               FROM post_analysis
               WHERE date(created_at) = date('now', 'localtime')
               ORDER BY created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_pipeline_with_sector() -> list:
    """파이프라인 목록 + 섹터 정보 (콘텐츠 캘린더용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, theme, sector, opportunity_score, status, created_at
               FROM pipeline
               WHERE status IN ('pending', 'scheduled', 'suggested')
               ORDER BY opportunity_score DESC
               LIMIT 30"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_performance_history(days: int = 14) -> list:
    """일자별 성과 이력 (성과현황 차트용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT date, naver_views, tistory_views
               FROM performance
               ORDER BY date DESC LIMIT ?""",
            (days,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_post_summary() -> dict:
    """발행 글 요약 통계 (성과현황 KPI용)."""
    with get_db() as conn:
        # post_analysis 우선, 없으면 posts 테이블 사용
        analysis_rows = conn.execute(
            "SELECT platform, COUNT(*) as cnt, COALESCE(SUM(current_views),0) as views "
            "FROM post_analysis GROUP BY platform"
        ).fetchall()
        post_rows = conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM posts GROUP BY platform"
        ).fetchall()
        today_cnt = conn.execute(
            "SELECT COUNT(*) FROM post_analysis WHERE date(created_at)=date('now','localtime')"
        ).fetchone()[0]
    by_platform = {r["platform"]: {"posts": r["cnt"], "views": r["views"]} for r in analysis_rows}
    for r in post_rows:
        by_platform.setdefault(r["platform"], {"posts": 0, "views": 0})
        by_platform[r["platform"]]["posts"] = max(by_platform[r["platform"]]["posts"], r["cnt"])
    return {"by_platform": by_platform, "today_posts": today_cnt}


# ── 즐겨찾기 ──────────────────────────────────────────────────

def add_favorite(keyword: str, note: str = "") -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO keyword_favorites(keyword, note) VALUES(?, ?) "
            "ON CONFLICT(keyword) DO UPDATE SET note=excluded.note",
            (keyword, note),
        )


def remove_favorite(keyword: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM keyword_favorites WHERE keyword=?", (keyword,))


def get_favorites() -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM keyword_favorites ORDER BY added_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def is_favorite(keyword: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM keyword_favorites WHERE keyword=?", (keyword,)
        ).fetchone()
    return bool(row)


# ── 라이프사이클 분류 ─────────────────────────────────────────

def classify_lifecycle(history: list) -> str:
    """trends 14일 히스토리 → Emerging/Rising/Peak/Declining/Dead.

    history: [{date, score}, ...] 시간순 정렬 가정. 빈 리스트면 'Unknown'.
    """
    if not history:
        return "Unknown"
    scores = [float(h.get("score", 0) or 0) for h in history]
    n = len(scores)
    if n == 1:
        return "Emerging"
    recent = scores[-3:] if n >= 3 else scores
    older  = scores[:-3] if n >= 6 else scores[:max(1, n // 2)]
    avg_recent = sum(recent) / len(recent)
    avg_older  = sum(older)  / max(1, len(older))
    peak       = max(scores)

    # Dead — 거의 0
    if avg_recent < 5 and peak < 30:
        return "Dead"
    # Declining — 최근이 과거보다 30% 이상 하락
    if avg_older > 0 and avg_recent < avg_older * 0.7:
        return "Declining"
    # Peak — 최고점이 최근 3일 안에 있고 점수도 높음
    if peak >= 70 and scores.index(peak) >= n - 3:
        return "Peak"
    # Rising — 최근이 과거보다 20% 이상 상승
    if avg_older > 0 and avg_recent > avg_older * 1.2:
        return "Rising"
    # 신규 (히스토리 짧거나 점수 낮은데 양수)
    if n <= 3 or avg_recent < 30:
        return "Emerging"
    return "Rising"


# ── Action Board ──────────────────────────────────────────────

def get_action_board() -> dict:
    """첫 화면 액션 카드 데이터.
    Returns: {high_opp, declining, recycle, golden_time, insights}
    """
    today = date.today().isoformat()
    out   = {"high_opp": [], "declining": [], "recycle": [], "golden_time": {}, "insights": []}

    with get_db() as conn:
        # 1) 즉시 발행 추천 — 오늘 트렌드 중 opp 80+ 미발행
        rows = conn.execute(
            """SELECT t.keyword, t.sector, t.score, t.opportunity_score, t.source
                 FROM trends t
                WHERE t.date = ?
                  AND t.opportunity_score >= 80
                  AND NOT EXISTS (
                      SELECT 1 FROM post_analysis p WHERE p.theme = t.keyword
                  )
                ORDER BY t.opportunity_score DESC
                LIMIT 5""",
            (today,),
        ).fetchall()
        out["high_opp"] = [dict(r) for r in rows]

        # 2) 식어가는 키워드 — 14일 내 발행했고 keyword_performance 데이터 있는데 최근 트렌드 점수 하락
        rows = conn.execute(
            """SELECT k.keyword, k.avg_views, k.last_used,
                      (SELECT score FROM trends WHERE keyword=k.keyword ORDER BY date DESC LIMIT 1) AS recent_score,
                      (SELECT AVG(score) FROM trends WHERE keyword=k.keyword AND date >= date('now','-14 days')) AS avg_score
                 FROM keyword_performance k
                WHERE k.last_used >= date('now','-30 days')"""
        ).fetchall()
        for r in rows:
            d = dict(r)
            recent = d.get("recent_score") or 0
            avg    = d.get("avg_score") or 0
            if avg and recent < avg * 0.6:
                out["declining"].append(d)
        out["declining"] = sorted(out["declining"], key=lambda x: -(x.get("avg_views") or 0))[:5]

        # 3) 재활용 후보 — 90~180일 전 잘 됐던 글 (조회수 상위) 중 재발행 안 한 테마
        rows = conn.execute(
            """SELECT theme, MAX(current_views) AS best_views, MIN(date(created_at)) AS first_date
                 FROM post_analysis
                WHERE date(created_at) BETWEEN date('now','-180 days') AND date('now','-90 days')
                  AND current_views > 50
                GROUP BY theme
                HAVING NOT EXISTS (
                    SELECT 1 FROM post_analysis p2
                     WHERE p2.theme = post_analysis.theme
                       AND date(p2.created_at) > date('now','-30 days')
                )
                ORDER BY best_views DESC
                LIMIT 5"""
        ).fetchall()
        out["recycle"] = [dict(r) for r in rows]

        # 4) 골든타임 — 플랫폼별 최적 발행 시간 vs 현재 시각
        from datetime import datetime as _dt
        now_h = _dt.now().hour
        for plat in ("naver", "tistory"):
            row = conn.execute(
                """SELECT CAST(strftime('%H', created_at) AS INTEGER) AS hh,
                          AVG(current_views) AS avg_v,
                          COUNT(*) AS cnt
                     FROM post_analysis
                    WHERE platform = ? AND current_views > 0
                    GROUP BY hh
                   HAVING cnt >= 2
                    ORDER BY avg_v DESC
                    LIMIT 1""",
                (plat,),
            ).fetchone()
            if row:
                best_h    = row["hh"]
                avg_v     = row["avg_v"] or 0
                hours_off = abs(best_h - now_h)
                out["golden_time"][plat] = {
                    "best_hour": best_h,
                    "avg_views": int(avg_v),
                    "is_now":    hours_off <= 1,
                    "hours_off": hours_off,
                }

    # 5) 인사이트 자동 생성 (데이터 기반)
    insights = []
    if out["high_opp"]:
        kw = out["high_opp"][0]
        insights.append(f"🚀 *{kw['keyword']}* — 기회점수 {kw['opportunity_score']:.0f}, 즉시 발행 추천")
    if out["declining"]:
        insights.append(f"📉 식어가는 키워드 *{len(out['declining'])}개* — 더 늦으면 효과 ↓")
    if out["recycle"]:
        kw = out["recycle"][0]
        insights.append(f"🔄 *{kw['theme']}* — 과거 {kw['best_views']:,}뷰, 재활용 시점")
    out["insights"] = insights

    return out


# ── Funnel 메트릭 ─────────────────────────────────────────────

def get_funnel_metrics(days: int = 30) -> dict:
    """추천→큐→발행→조회 단계별 카운트 + 전환율."""
    with get_db() as conn:
        suggested = conn.execute(
            "SELECT COUNT(*) FROM pipeline WHERE date(created_at) >= date('now',?)",
            (f"-{days} days",),
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM pipeline "
            " WHERE date(created_at) >= date('now',?) AND status IN ('approved','published')",
            (f"-{days} days",),
        ).fetchone()[0]
        published = conn.execute(
            "SELECT COUNT(*) FROM post_analysis WHERE date(created_at) >= date('now',?)",
            (f"-{days} days",),
        ).fetchone()[0]
        with_views = conn.execute(
            "SELECT COUNT(*), COALESCE(AVG(current_views),0), COALESCE(SUM(current_views),0) "
            "  FROM post_analysis WHERE date(created_at) >= date('now',?) AND current_views > 0",
            (f"-{days} days",),
        ).fetchone()
    return {
        "suggested":  suggested,
        "approved":   approved,
        "published":  published,
        "with_views": with_views[0],
        "avg_views":  round(with_views[1] or 0, 1),
        "sum_views":  with_views[2] or 0,
    }


def get_opportunity_vs_views(days: int = 60) -> list:
    """opportunity_score (pipeline) vs 실측 조회수 (post_analysis) — 자가검증용 산점도."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT p.theme, p.opportunity_score, pa.current_views, pa.platform
                 FROM pipeline p
                 JOIN post_analysis pa ON pa.theme = p.theme
                WHERE date(p.created_at) >= date('now',?)
                  AND pa.current_views > 0
                  AND p.opportunity_score > 0""",
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 수정 효과 (Did it work?) ──────────────────────────────────

def get_revision_effect(days: int = 60) -> dict:
    """수정 적용 글 vs 미적용 글의 평균 조회수 비교."""
    with get_db() as conn:
        revised = conn.execute(
            """SELECT COUNT(*), COALESCE(AVG(current_views),0)
                 FROM post_analysis
                WHERE is_revised = 1 AND current_views > 0
                  AND date(created_at) >= date('now',?)""",
            (f"-{days} days",),
        ).fetchone()
        non_revised = conn.execute(
            """SELECT COUNT(*), COALESCE(AVG(current_views),0)
                 FROM post_analysis
                WHERE is_revised = 0 AND status IN ('rejected','approved','revised')
                  AND current_views > 0
                  AND date(created_at) >= date('now',?)""",
            (f"-{days} days",),
        ).fetchone()
        per_post = conn.execute(
            """SELECT theme, platform, current_views, is_revised, status,
                      decided_at, revised_at, created_at
                 FROM post_analysis
                WHERE current_views > 0
                  AND date(created_at) >= date('now',?)
                ORDER BY created_at DESC
                LIMIT 50""",
            (f"-{days} days",),
        ).fetchall()
    revised_n,  revised_avg     = revised
    non_n,      non_avg         = non_revised
    lift_pct = round(((revised_avg / non_avg) - 1) * 100, 1) if non_avg else 0
    return {
        "revised_n":   revised_n,
        "revised_avg": round(revised_avg or 0, 1),
        "non_n":       non_n,
        "non_avg":     round(non_avg or 0, 1),
        "lift_pct":    lift_pct,
        "per_post":    [dict(r) for r in per_post],
    }


# ── 운영 메트릭 ───────────────────────────────────────────────

def get_ops_metrics() -> dict:
    """시스템 탭용 운영 통계."""
    with get_db() as conn:
        today = conn.execute(
            "SELECT COUNT(*) FROM post_analysis WHERE date(created_at)=date('now','localtime')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM post_analysis WHERE status='pending_approval'"
        ).fetchone()[0]
        auto_approved = conn.execute(
            """SELECT COUNT(*) FROM post_analysis
                WHERE status IN ('approved','revised')
                  AND revision_patch LIKE '%"auto":%true%'
                  AND date(decided_at) >= date('now','-7 days')"""
        ).fetchone()[0]
        manual_approved = conn.execute(
            """SELECT COUNT(*) FROM post_analysis
                WHERE status IN ('approved','revised')
                  AND (revision_patch IS NULL OR revision_patch NOT LIKE '%"auto":%true%')
                  AND date(decided_at) >= date('now','-7 days')"""
        ).fetchone()[0]
        events_today = conn.execute(
            "SELECT COUNT(*) FROM events WHERE date(created_at)=date('now','localtime')"
        ).fetchone()[0]
        events_total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        pa_total = conn.execute("SELECT COUNT(*) FROM post_analysis").fetchone()[0]
        trends_total = conn.execute("SELECT COUNT(*) FROM trends").fetchone()[0]

    db_size_mb = round(DB_PATH.stat().st_size / (1024 * 1024), 2) if DB_PATH.exists() else 0
    backups = []
    if BACKUP_DIR.exists():
        for p in sorted(BACKUP_DIR.glob("jarvis_*.sqlite"), reverse=True):
            try:
                d = date.fromisoformat(p.stem.replace("jarvis_", ""))
                backups.append({"date": d.isoformat(), "size_kb": p.stat().st_size // 1024})
            except Exception:
                continue

    return {
        "today_posts":    today,
        "pending":        pending,
        "auto_approved":  auto_approved,
        "manual_approved": manual_approved,
        "events_today":   events_today,
        "events_total":   events_total,
        "pa_total":       pa_total,
        "trends_total":   trends_total,
        "db_size_mb":     db_size_mb,
        "backups":        backups[:30],
    }


def get_event_timeline(days: int = 1) -> list:
    """최근 N일 이벤트 시계열 (시스템 탭 타임라인용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, event_type, source, payload, created_at
                 FROM events
                WHERE created_at >= datetime('now','localtime',?)
                ORDER BY created_at DESC
                LIMIT 200""",
            (f"-{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 사용자 설정 (key-value) ────────────────────────────────────

def get_setting(key: str, default=None):
    """user_settings 단일 키 조회. JSON 디코드 자동."""
    with get_db() as conn:
        row = conn.execute("SELECT value FROM user_settings WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    raw = row["value"]
    try:
        return json.loads(raw)
    except Exception:
        return raw if raw is not None else default


def set_setting(key: str, value) -> None:
    """user_settings 저장 (str/int/dict/list 자동 JSON 인코딩)."""
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_settings(key,value,updated_at) VALUES(?,?,datetime('now','localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, serialized),
        )


def get_all_settings() -> dict:
    with get_db() as conn:
        rows = conn.execute("SELECT key,value FROM user_settings").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    return out


# ── Keyword Performance 학습 자산 시각화 ──────────────────────

def get_top_keywords(limit: int = 20, min_posts: int = 1) -> list:
    """keyword_performance 학습된 상위 키워드 (avg_views * post_count 가중).
    total_views 는 (avg_views * post_count) 로 산출."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT keyword, avg_views, post_count, best_views, last_used,
                      (avg_views * post_count) AS total_views
                 FROM keyword_performance
                WHERE post_count >= ?
                ORDER BY (avg_views * post_count) DESC
                LIMIT ?""",
            (min_posts, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_keyword_perf_scatter(limit: int = 200) -> list:
    """post_count vs avg_views 산점도용 — 학습된 모든 키워드."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT keyword, avg_views, post_count, last_used,
                      (avg_views * post_count) AS total_views
                 FROM keyword_performance
                WHERE avg_views > 0
                ORDER BY post_count DESC
                LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 글별 수정 라이프사이클 (Did it work? 글 단위) ──────────────

def get_revision_lifecycle(days: int = 60, limit: int = 60) -> list:
    """글별 발행→분석→수정 시간선 + 조회수 (lifecycle 타임라인용)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, theme, platform, status, current_views,
                      is_revised, created_at, analyzed_at, decided_at, revised_at
                 FROM post_analysis
                WHERE date(created_at) >= date('now',?)
                ORDER BY created_at DESC
                LIMIT ?""",
            (f"-{days} days", limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 다음 수집 스케줄 안내 ──────────────────────────────────────

def next_collection_eta() -> dict:
    """jarvis_daemon.py 의 트렌드 수집 스케줄 (10:00, 17:00, 21:00) 기준 다음 ETA."""
    from datetime import datetime as _dt
    now = _dt.now()
    fixed = [(10, 0), (17, 0), (21, 0)]
    today_eta = []
    for h, m in fixed:
        cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand > now:
            today_eta.append(cand)
    if today_eta:
        nxt = today_eta[0]
    else:
        # 오늘 일정 끝 → 내일 첫 일정
        nxt = now.replace(hour=fixed[0][0], minute=fixed[0][1], second=0, microsecond=0)
        nxt = nxt.replace(day=now.day) if now.hour < fixed[0][0] else nxt
        from datetime import timedelta as _td
        if nxt <= now:
            nxt += _td(days=1)
    delta = nxt - now
    mins = int(delta.total_seconds() // 60)
    return {
        "next_at":   nxt.strftime("%Y-%m-%d %H:%M"),
        "minutes":   mins,
        "schedule":  "10:00 / 17:00 / 21:00 KST",
    }


# ── Maintenance (백업·정리) ───────────────────────────────────

def backup_db(retention_days: int = 30) -> dict:
    """SQLite .backup API 로 WAL 안전 백업 + retention 보관.

    반환: {"backup": Path, "removed": int, "size_kb": int}
    """
    BACKUP_DIR.mkdir(exist_ok=True)
    today  = date.today().isoformat()
    target = BACKUP_DIR / f"jarvis_{today}.sqlite"

    # 1) 백업 — sqlite3.Connection.backup() 은 WAL 도 flush 후 일관 상태로 복사
    try:
        src = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.OperationalError:
        # Fallback — WAL 체크포인트 후 파일 직접 복사 (특정 FS 환경에서 .backup() 실패 시)
        if target.exists():
            target.unlink()
        with sqlite3.connect(str(DB_PATH), timeout=10) as cp:
            cp.execute("PRAGMA wal_checkpoint(FULL)")
        shutil.copy2(DB_PATH, target)

    # 2) Retention — 30일 이전 백업 삭제
    cutoff  = date.today() - timedelta(days=retention_days)
    removed = 0
    for p in BACKUP_DIR.glob("jarvis_*.sqlite"):
        try:
            d = date.fromisoformat(p.stem.replace("jarvis_", ""))
            if d < cutoff:
                p.unlink()
                removed += 1
        except Exception:
            continue

    return {
        "backup":  target,
        "removed": removed,
        "size_kb": target.stat().st_size // 1024,
    }


def cleanup_events(days: int = 30) -> int:
    """events 테이블에서 N일 이전 row 삭제. 삭제 row 수 반환."""
    with get_db() as conn:
        cur = conn.execute(
            "DELETE FROM events WHERE created_at < datetime('now','localtime',?)",
            (f"-{days} days",),
        )
        deleted = cur.rowcount
        conn.commit()
    # 단편화 회수 — VACUUM 은 트랜잭션 밖에서만 실행
    if deleted:
        with sqlite3.connect(str(DB_PATH), timeout=30) as v:
            v.execute("VACUUM")
    return deleted


# ─────────────────────────────────────────────────────────────
# 브랜드 보이스 학습 코퍼스 (style_corpus)
# ─────────────────────────────────────────────────────────────

def style_corpus_upsert(
    source_id: int, platform: str, title: str, content: str, excerpt: str,
    embedding_bytes: bytes, embed_model: str, embed_dim: int,
    char_count: int = 0, published_at: str = "", views: int = 0,
) -> None:
    """과거 발행 글 + 임베딩 저장. source_id 충돌 시 업데이트."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO style_corpus
                (source_id, platform, title, content, excerpt,
                 embedding, embed_model, embed_dim, char_count,
                 published_at, views, indexed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))
               ON CONFLICT(source_id) DO UPDATE SET
                 platform=excluded.platform,
                 title=excluded.title,
                 content=excluded.content,
                 excerpt=excluded.excerpt,
                 embedding=excluded.embedding,
                 embed_model=excluded.embed_model,
                 embed_dim=excluded.embed_dim,
                 char_count=excluded.char_count,
                 views=excluded.views,
                 indexed_at=excluded.indexed_at""",
            (source_id, platform, title, content, excerpt,
             embedding_bytes, embed_model, embed_dim, char_count,
             published_at, views),
        )


def style_corpus_count() -> int:
    with get_db() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM style_corpus").fetchone()
    return int(row["c"]) if row else 0


def style_corpus_unindexed_post_ids(min_chars: int = 200) -> list[int]:
    """post_analysis 에 본문 있고 style_corpus 에 없는 ID 목록."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT pa.id
                 FROM post_analysis pa
                 LEFT JOIN style_corpus sc ON sc.source_id = pa.id
                WHERE sc.id IS NULL
                  AND length(pa.original_content) >= ?
                ORDER BY pa.created_at DESC""",
            (min_chars,),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def style_corpus_fetch_post(post_id: int) -> dict | None:
    """인덱싱용 — post_analysis 한 건 가져오기."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT id, platform, theme, title, original_content,
                      created_at, current_views
                 FROM post_analysis WHERE id = ?""",
            (post_id,),
        ).fetchone()
    return dict(row) if row else None


def style_corpus_all_embeddings() -> list[dict]:
    """전체 코퍼스 (임베딩 + 메타). 검색 시 메모리에 로드."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT id, source_id, platform, title, excerpt,
                      embedding, embed_model, embed_dim,
                      char_count, published_at, views
                 FROM style_corpus""").fetchall()
    return [dict(r) for r in rows]


def style_corpus_stats() -> dict:
    """대시보드 패널용."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS n,
                      AVG(char_count) AS avg_chars,
                      MAX(indexed_at) AS last_indexed,
                      MIN(published_at) AS first_published,
                      MAX(published_at) AS last_published
                 FROM style_corpus""").fetchone()
        # 플랫폼 분포
        plat = conn.execute(
            "SELECT platform, COUNT(*) AS c FROM style_corpus GROUP BY platform"
        ).fetchall()
    out = dict(row) if row else {}
    out["by_platform"] = {p["platform"]: p["c"] for p in plat}
    return out


def style_corpus_clear() -> int:
    """코퍼스 비우기 (재인덱싱 전 사용). 삭제 row 수 반환."""
    with get_db() as conn:
        cur = conn.execute("DELETE FROM style_corpus")
        return cur.rowcount


# ── 자가학습 — learn_log ────────────────────────────────────────

def learn_log_upsert(keyword: str, sector: str, platform: str,
                     trend_score: float, perf_boost: float, freshness: float,
                     velocity: float, competition: float, predicted_opp: float,
                     actual_views: int, days_after: int,
                     naver_rank: int | None = None) -> None:
    """예측 feature + 실측 신호 (조회수 또는 네이버 노출 rank) 한 row 적재.

    동일 (keyword, platform, days_after) 면 업데이트.
    naver_rank: 1~100 (낮을수록 좋음) / 100 위 밖이면 None.
    """
    with get_db() as conn:
        conn.execute(
            """INSERT INTO learn_log
               (keyword, sector, platform, trend_score, perf_boost, freshness,
                velocity, competition, predicted_opp, actual_views, days_after, naver_rank)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(keyword, platform, days_after) DO UPDATE SET
                 trend_score=excluded.trend_score,
                 perf_boost=excluded.perf_boost,
                 freshness=excluded.freshness,
                 velocity=excluded.velocity,
                 competition=excluded.competition,
                 predicted_opp=excluded.predicted_opp,
                 actual_views=excluded.actual_views,
                 naver_rank=excluded.naver_rank,
                 logged_at=datetime('now','localtime')""",
            (keyword, sector, platform, trend_score, perf_boost, freshness,
             velocity, competition, predicted_opp, actual_views, days_after, naver_rank),
        )


def learn_log_fetch(min_samples: int = 20, max_age_days: int = 365) -> list[dict]:
    """학습용 row 가져오기 — 최근 max_age_days 이내, 최소 min_samples 이상이어야 함."""
    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT keyword, sector, platform, trend_score, perf_boost, freshness,
                       velocity, competition, predicted_opp, actual_views, days_after, logged_at
                FROM learn_log
                WHERE logged_at >= datetime('now', '-{int(max_age_days)} days', 'localtime')
                  AND actual_views IS NOT NULL""",
        ).fetchall()
    return [dict(r) for r in rows] if len(rows) >= min_samples else []


def learn_log_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM learn_log").fetchone()[0]


# ── 자가학습 — learned_weights ──────────────────────────────────

# 기본(하드코딩) 가중치 — 학습 데이터 부족 시 fallback. analyzer.opportunity_score 의 기존 값과 일치.
DEFAULT_WEIGHTS = {
    "w_trend": 0.45, "w_perf": 1.0, "w_fresh": 0.85,
    "w_velocity": 0.5, "w_competition": -0.2,
    "intercept": 0.0, "n_samples": 0, "r2": None, "mse": None,
    "learned_at": "default",
}


def learned_weights_save(w_trend: float, w_perf: float, w_fresh: float,
                         w_velocity: float, w_competition: float,
                         intercept: float, n_samples: int,
                         r2: float, mse: float) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO learned_weights
               (w_trend, w_perf, w_fresh, w_velocity, w_competition,
                intercept, n_samples, r2, mse)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (w_trend, w_perf, w_fresh, w_velocity, w_competition,
             intercept, n_samples, r2, mse),
        )
        return cur.lastrowid


def learned_weights_latest() -> dict:
    """가장 최근 학습 가중치 + 메타. 없으면 DEFAULT_WEIGHTS 반환."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM learned_weights ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else dict(DEFAULT_WEIGHTS)


def learned_weights_history(limit: int = 12) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM learned_weights ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── 자가학습 — feedback_penalty ─────────────────────────────────

def feedback_penalty_upsert(target: str, *, rejected_inc: int = 0,
                            approved_inc: int = 0, penalty: float | None = None) -> None:
    """target = 'sector:X' 또는 'kw:X'. rejected/approved 누적, penalty 명시값으로 갱신."""
    with get_db() as conn:
        # upsert
        conn.execute(
            """INSERT INTO feedback_penalty (target, rejected, approved, penalty, updated_at)
               VALUES (?, ?, ?, COALESCE(?, 0), datetime('now','localtime'))
               ON CONFLICT(target) DO UPDATE SET
                 rejected = rejected + excluded.rejected,
                 approved = approved + excluded.approved,
                 penalty  = COALESCE(?, penalty),
                 updated_at = datetime('now','localtime')""",
            (target, rejected_inc, approved_inc, penalty, penalty),
        )


def feedback_penalty_get(target: str) -> float:
    with get_db() as conn:
        r = conn.execute("SELECT penalty FROM feedback_penalty WHERE target=?", (target,)).fetchone()
    return float(r["penalty"]) if r else 0.0


def feedback_penalty_recompute_all() -> int:
    """rejected/approved 비율로 penalty 재계산. 갱신된 row 수 반환.
    공식: penalty = -10 * (rejected / (rejected + approved + 1)) * log10(rejected + 1) [0 ~ -20]."""
    import math
    with get_db() as conn:
        rows = conn.execute("SELECT target, rejected, approved FROM feedback_penalty").fetchall()
        n = 0
        for r in rows:
            rej = int(r["rejected"] or 0)
            app = int(r["approved"] or 0)
            if rej + app == 0:
                continue
            ratio = rej / (rej + app + 1)
            penalty = round(-10.0 * ratio * (math.log10(rej + 1) + 0.1), 2)
            penalty = max(-20.0, min(0.0, penalty))
            conn.execute(
                "UPDATE feedback_penalty SET penalty=?, updated_at=datetime('now','localtime') WHERE target=?",
                (penalty, r["target"]),
            )
            n += 1
        return n


def feedback_penalty_all() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM feedback_penalty ORDER BY penalty ASC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


# ── 자가학습 — keyword_embeddings ───────────────────────────────

def keyword_embedding_upsert(keyword: str, embedding_bytes: bytes,
                             model: str, dim: int) -> None:
    with get_db() as conn:
        conn.execute(
            """INSERT INTO keyword_embeddings (keyword, embedding, embed_model, embed_dim)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(keyword) DO UPDATE SET
                 embedding=excluded.embedding,
                 embed_model=excluded.embed_model,
                 embed_dim=excluded.embed_dim,
                 indexed_at=datetime('now','localtime')""",
            (keyword, embedding_bytes, model, dim),
        )


def keyword_embedding_get(keyword: str) -> dict | None:
    with get_db() as conn:
        r = conn.execute("SELECT * FROM keyword_embeddings WHERE keyword=?", (keyword,)).fetchone()
    return dict(r) if r else None


def keyword_embeddings_all() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM keyword_embeddings").fetchall()
    return [dict(r) for r in rows]


# ── 자가학습 — backtest_history ─────────────────────────────────

def backtest_save(n_samples: int, r2: float, mse: float, mape: float) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO backtest_history (n_samples, r2, mse, mape) VALUES (?,?,?,?)",
            (n_samples, r2, mse, mape),
        )
        return cur.lastrowid


def backtest_history(limit: int = 12) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM backtest_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── 일일 종합 분석 (daily_review) ────────────────────────────────

def get_today_post_analyses_grouped(date_str: str = None) -> dict[str, list[dict]]:
    """daily_review 가 글 종류별로 분리 분석할 수 있게 그룹핑 반환.

    반환: {"economic": [...], "theme": [...], "unknown": [...]}.
    NULL/빈 post_type 은 'unknown' 그룹으로 분류 (backfill 안 된 기존 글 대비).
    새 글 종류가 추가되면 자동으로 새 그룹 생성 (코드 수정 불필요).
    """
    posts = get_today_post_analyses(date_str)
    grouped: dict[str, list[dict]] = {}
    for p in posts:
        pt = (p.get("post_type") or "").strip() or "unknown"
        grouped.setdefault(pt, []).append(p)
    return grouped


def get_today_post_analyses(date_str: str = None) -> list[dict]:
    """오늘(또는 지정일) 발행된 모든 post_analysis 행. daily_review 잡 입력용."""
    with get_db() as conn:
        if date_str:
            rows = conn.execute(
                """SELECT * FROM post_analysis
                   WHERE date(created_at) = date(?)
                   ORDER BY created_at""",
                (date_str,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM post_analysis
                   WHERE date(created_at) = date('now','localtime')
                   ORDER BY created_at"""
            ).fetchall()
    return [dict(r) for r in rows]


def upsert_daily_review(review_date: str, payload: dict) -> None:
    """daily_review UPSERT — 같은 날짜 재실행 시 갱신."""
    cols = [
        "posts_count", "platforms_json", "avg_views", "top_views",
        "quality_score", "sector_dist", "common_issues", "insights",
        "next_directives",
    ]
    vals = [payload.get(c, 0 if c in ("posts_count","top_views") else
                            (0.0 if c in ("avg_views","quality_score") else
                             ("[]" if c in ("common_issues","next_directives") else
                              ("{}" if c in ("platforms_json","sector_dist") else ""))))
            for c in cols]
    with get_db() as conn:
        conn.execute(
            f"""INSERT INTO daily_review
                (review_date, {', '.join(cols)}, reviewed_at)
                VALUES (?, {', '.join(['?']*len(cols))}, datetime('now','localtime'))
                ON CONFLICT(review_date) DO UPDATE SET
                  {', '.join(f'{c}=excluded.{c}' for c in cols)},
                  reviewed_at = datetime('now','localtime')""",
            (review_date, *vals),
        )


def get_daily_review(review_date: str) -> dict | None:
    with get_db() as conn:
        r = conn.execute(
            "SELECT * FROM daily_review WHERE review_date = ?", (review_date,)
        ).fetchone()
    return dict(r) if r else None


def get_recent_daily_reviews(days: int = 7) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM daily_review
               WHERE review_date >= date('now','localtime',?)
               ORDER BY review_date DESC""",
            (f"-{int(days)} day",),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 누적 학습 인사이트 (learning_insights) ────────────────────────

def upsert_learning_insight(insight_key: str, insight_type: str,
                            description: str, directive: str,
                            weight: float = 1.0,
                            scope: str = "all") -> None:
    """동일 (insight_key, scope) 면 occurrences+1 + last_seen 갱신, weight 누적 강화.

    scope: 'economic' / 'theme' / 'all' / 자유문자열. 글 종류별 분리 학습 키.
    같은 insight_key 가 다른 scope 로 들어오면 별개 행으로 격리.
    구현 노트: 테이블의 insight_key UNIQUE 제약 때문에 실제 저장 키는 'scope:insight_key'
    합성 형태로 저장. UI 표시·조회 시 scope 컬럼이 진짜 scope, insight_key 에서 prefix
    제거 후 표시. get_top_learning_insights() 가 자동 처리.
    """
    scope = (scope or "all").strip() or "all"
    composite_key = f"{scope}:{insight_key}" if not insight_key.startswith(f"{scope}:") else insight_key
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id, occurrences, weight FROM learning_insights WHERE insight_key = ?",
            (composite_key,),
        ).fetchone()
        if existing:
            new_occ    = int(existing["occurrences"]) + 1
            new_weight = min(5.0, float(existing["weight"]) + 0.5)
            conn.execute(
                """UPDATE learning_insights
                   SET occurrences = ?, weight = ?,
                       description = ?, directive = ?, scope = ?,
                       last_seen = datetime('now','localtime')
                   WHERE id = ?""",
                (new_occ, new_weight, description, directive, scope, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO learning_insights
                   (insight_key, insight_type, description, directive, weight, scope)
                   VALUES (?,?,?,?,?,?)""",
                (composite_key, insight_type, description, directive, weight, scope),
            )


def get_top_learning_insights(limit: int = 10, days: int = 30,
                              insight_type: str = "",
                              scope: str = "") -> list[dict]:
    """pre_revise SYSTEM_PROMPT 보강용 — 최근 N일 활성 + 가중치 상위 N개.

    scope: 'economic' / 'theme' 등 명시 시 scope IN (해당, 'all') 인 인사이트만.
           빈 문자열이면 전체. 가중치는 시간 감쇠 (7일마다 0.7 곱).
    insight_key 가 'scope:original_key' 합성 형태로 저장돼 있으니 표시용 'key'
    필드를 별도로 추출해서 반환.
    """
    with get_db() as conn:
        sql = """SELECT *,
                  weight * power(0.7, max(0, julianday('now','localtime')
                                            - julianday(last_seen)) / 7.0) AS effective_weight
                  FROM learning_insights
                  WHERE last_seen >= date('now','localtime',?)"""
        params: list = [f"-{int(days)} day"]
        if insight_type:
            sql += " AND insight_type = ?"
            params.append(insight_type)
        if scope:
            sql += " AND COALESCE(scope,'all') IN (?, 'all')"
            params.append(scope)
        sql += " ORDER BY effective_weight DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 합성 키 분리: 'economic:intro_too_long' → display_key='intro_too_long'
        ik = d.get("insight_key") or ""
        sc = d.get("scope") or "all"
        d["display_key"] = ik[len(sc) + 1:] if ik.startswith(f"{sc}:") else ik
        out.append(d)
    return out


def decay_learning_insights(min_weight: float = 0.05) -> int:
    """주기적 정리 — last_seen 30일 경과 시 weight 0.5배. min_weight 이하는 삭제.

    train_weights 잡과 함께 일요일에 호출 권장.
    """
    n = 0
    with get_db() as conn:
        conn.execute(
            """UPDATE learning_insights
               SET weight = weight * 0.5
               WHERE last_seen < date('now','localtime','-30 day')""",
        )
        cur = conn.execute(
            "DELETE FROM learning_insights WHERE weight < ?", (min_weight,),
        )
        n = cur.rowcount or 0
    return n


# ── 글 품질 강화학습 보상 사슬 (ADR 014 — 2026-07-03) ─────────────
#   알고리즘(UCB 선택·보상 계산·EMA 갱신)은 JARVIS07_GUARDIAN/quality_learner.py
#   단일 진입점. 여기는 순수 SQL 헬퍼만.

def get_ranked_learning_insights(scope: str = "", limit: int = 8,
                                 days: int = 21) -> list[dict]:
    """UCB 랭킹용 원자료 — effective_weight + 사용횟수 + 평균보상 포함."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT li.*,
                      li.weight * power(0.7, max(0, julianday('now','localtime')
                                                    - julianday(li.last_seen)) / 7.0)
                          AS effective_weight,
                      COALESCE(u.uses, 0)         AS uses,
                      COALESCE(u.rewarded, 0)     AS rewarded
               FROM learning_insights li
               LEFT JOIN (SELECT insight_id, COUNT(*) AS uses,
                                 COUNT(reward) AS rewarded
                          FROM insight_usage GROUP BY insight_id) u
                    ON u.insight_id = li.id
               WHERE li.last_seen >= date('now','localtime',?)
                 AND (? = '' OR COALESCE(li.scope,'all') IN (?, 'all'))
               ORDER BY effective_weight DESC
               LIMIT ?""",
            (f"-{int(days)} day", scope, scope, int(limit) * 3),
        ).fetchall()
    return [dict(r) for r in rows]


def record_insight_usage(batch_id: str, insight_ids: list,
                         scope: str = "all", platform: str = "",
                         theme: str = "") -> int:
    """주입된 인사이트 묶음을 사용 기록 (보상 귀속 대기)."""
    if not insight_ids:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.executemany(
            "INSERT INTO insight_usage (batch_id, insight_id, scope, platform, theme) "
            "VALUES (?, ?, ?, ?, ?)",
            [(batch_id, int(i), scope, platform, theme[:120]) for i in insight_ids],
        )
        conn.execute(
            f"UPDATE learning_insights SET last_used_at = ? "
            f"WHERE id IN ({','.join('?' * len(insight_ids))})",
            [now, *[int(i) for i in insight_ids]],
        )
    return len(insight_ids)


def get_unrewarded_usage(days: int = 3) -> list[dict]:
    """reward 미귀속 사용 기록 (최근 N일)."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM insight_usage
               WHERE reward IS NULL
                 AND used_at >= datetime('now','localtime',?)
               ORDER BY used_at ASC""",
            (f"-{int(days)} day",),
        ).fetchall()
    return [dict(r) for r in rows]


def apply_insight_reward(usage_id: int, insight_id: int, analysis_id: int,
                         reward: float, alpha: float = 0.3,
                         update_weight: bool = True) -> None:
    """보상 귀속 — usage 행 마감 + learning_insights 가중치 EMA 갱신.

    weight ← clamp(0.05, 3.0, weight + alpha*(reward - 0.5))
    reward 0.5 중립 기준: 좋은 글(제안 적음) → weight ↑, 나쁜 글 → ↓.
    update_weight=False: usage 마감(부기)만 — 같은 (insight, analysis) 쌍
    중복 보상 방지용 (quality_learner 가 판단).
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        conn.execute(
            "UPDATE insight_usage SET reward = ?, analysis_id = ?, rewarded_at = ? "
            "WHERE id = ?",
            (float(reward), int(analysis_id), now, int(usage_id)),
        )
        if update_weight:
            conn.execute(
                """UPDATE learning_insights
                   SET reward_sum   = COALESCE(reward_sum, 0) + ?,
                       reward_count = COALESCE(reward_count, 0) + 1,
                       weight       = max(0.05, min(3.0, weight + ? * (? - 0.5)))
                   WHERE id = ?""",
                (float(reward), float(alpha), float(reward), int(insight_id)),
            )


# ── JARVIS05 VISION ───────────────────────────────────────────

def _init_vision_tables() -> None:
    """JARVIS05_VISION 전용 테이블 초기화. vision_agent.register() 에서 1회 호출."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS vision_agent_status (
                agent_id     TEXT PRIMARY KEY,
                agent_name   TEXT,
                agent_domain TEXT,
                status       TEXT DEFAULT 'unknown',   -- online / warn / offline
                message      TEXT DEFAULT '',
                metrics_json TEXT DEFAULT '{}',
                last_seen    TEXT,
                registered_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS vision_agent_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id     TEXT NOT NULL,
                agent_name   TEXT,
                status       TEXT,
                message      TEXT DEFAULT '',
                metrics_json TEXT DEFAULT '{}',
                recorded_at  TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_vision_history_agent
                ON vision_agent_history(agent_id, recorded_at DESC);
        """)


# ── Error Log (JARVIS07_GUARDIAN) ────────────────────────────────

def save_error(
    source: str,
    error_type: str,
    message: str,
    module: str = None,
    func_name: str = None,
    traceback: str = None,
    context: str = None,
    severity: str = "medium",
) -> int:
    """오류 저장. 동일 오류(source+module+error_type+message) 중복 시 seen_count 증가.

    Returns:
        int: error_log.id (신규) 또는 기존 id (중복 시)
    """
    with get_db() as conn:
        # 중복 검사 (최근 1시간 내 동일 오류)
        existing = conn.execute(
            """SELECT id, seen_count FROM error_log
               WHERE source=? AND module IS ? AND error_type=?
                 AND message=? AND status!='fixed'
                 AND timestamp >= datetime('now','-1 hour','localtime')
               ORDER BY id DESC LIMIT 1""",
            (source, module, error_type, message[:500] if message else None),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE error_log SET seen_count=seen_count+1, timestamp=strftime('%Y-%m-%dT%H:%M:%S','now','localtime') WHERE id=?",
                (existing["id"],),
            )
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO error_log
               (source, module, func_name, error_type, message, traceback, context, severity)
               VALUES (?,?,?,?,?,?,?,?)""",
            (source, module, func_name, error_type,
             (message or "")[:2000], traceback, context, severity),
        )
        return cur.lastrowid


def get_error(error_id: int) -> dict:
    """오류 상세 조회."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM error_log WHERE id=?", (error_id,)).fetchone()
        return dict(row) if row else {}


def list_errors(status: str = "new", limit: int = 20) -> list:
    """오류 목록 조회."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM error_log WHERE status=? ORDER BY timestamp DESC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_error_fixed(error_id: int, resolution: str, fixed_file: str = None):
    """오류 해결 처리."""
    with get_db() as conn:
        conn.execute(
            """UPDATE error_log
               SET status='fixed', resolution=?, fixed_file=?,
                   fixed_at=strftime('%Y-%m-%dT%H:%M:%S','now','localtime')
               WHERE id=?""",
            (resolution, fixed_file, error_id),
        )


def mark_error_status(error_id: int, status: str):
    """오류 상태 변경 (analyzing / wontfix / ignored)."""
    with get_db() as conn:
        conn.execute("UPDATE error_log SET status=? WHERE id=?", (status, error_id))


def get_error_resolution(error_type: str, module: str = None) -> str | None:
    """과거에 동일 오류를 해결한 resolution 반환 (자동 수정 재활용)."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT resolution FROM error_log
               WHERE error_type=? AND (module IS NULL OR module=?)
                 AND status='fixed' AND resolution IS NOT NULL
               ORDER BY fixed_at DESC LIMIT 1""",
            (error_type, module),
        ).fetchone()
        return row["resolution"] if row else None


def get_error_stats(days: int = 7) -> dict:
    """오류 통계 요약."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT severity, status, COUNT(*) as cnt
               FROM error_log
               WHERE timestamp >= datetime('now',?,'localtime')
               GROUP BY severity, status""",
            (f"-{days} days",),
        ).fetchall()
        stats: dict = {}
        for r in rows:
            key = f"{r['severity']}_{r['status']}"
            stats[key] = r["cnt"]
        total = conn.execute(
            "SELECT COUNT(*) FROM error_log WHERE timestamp >= datetime('now',?,'localtime')",
            (f"-{days} days",),
        ).fetchone()[0]
        stats["total"] = total
        return stats


def archive_old_errors(days: int = 30) -> int:
    """30일 초과 해결·무시 오류 삭제 후 삭제 건수 반환."""
    with get_db() as conn:
        cur = conn.execute(
            """DELETE FROM error_log
               WHERE status IN ('fixed','ignored','wontfix')
                 AND timestamp < datetime('now',?,'localtime')""",
            (f"-{days} days",),
        )
        return cur.rowcount


# ── JARVIS09 COLLECTOR — 수집 결과 ─────────────────────────────

def _init_collection_table() -> None:
    """collection_results 테이블 초기화 (없으면 생성)."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS collection_results (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                theme        TEXT NOT NULL,
                source_type  TEXT NOT NULL,
                url          TEXT NOT NULL,
                title        TEXT,
                cleaned_text TEXT,
                collected_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE INDEX IF NOT EXISTS idx_cr_theme ON collection_results(theme);
            CREATE INDEX IF NOT EXISTS idx_cr_collected ON collection_results(collected_at);
        """)


def save_collection_result(theme: str, source_type: str, url: str, title: str, cleaned_text: str) -> int:
    """수집 결과 저장. 삽입된 row id 반환."""
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO collection_results (theme, source_type, url, title, cleaned_text) VALUES (?,?,?,?,?)",
            (theme, source_type, url, title or "", cleaned_text or ""),
        )
        return cur.lastrowid


def get_collection_results(theme: str, limit: int = 20) -> list[dict]:
    """테마별 최근 수집 결과 조회."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, theme, source_type, url, title, cleaned_text, collected_at "
            "FROM collection_results WHERE theme=? ORDER BY collected_at DESC LIMIT ?",
            (theme, limit),
        ).fetchall()
    return [dict(zip(["id","theme","source_type","url","title","cleaned_text","collected_at"], r)) for r in rows]


def get_collection_stats() -> dict:
    """수집 현황 통계."""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM collection_results").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM collection_results WHERE collected_at >= date('now','localtime')"
        ).fetchone()[0]
    return {"total": total, "today": today}


# 임포트 시 자동 초기화
init_db()
try:
    _init_collection_table()
except Exception:
    pass
