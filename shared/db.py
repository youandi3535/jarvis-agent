"""
JARVIS 공유 데이터베이스
모든 에이전트가 읽고 쓰는 단일 SQLite — 기본: **`~/.jarvis/jarvis.sqlite`** (프로젝트 밖)
★ JARVIS_DB_PATH 환경변수로 경로 오버라이드 가능.

★ 왜 프로젝트 밖인가 (ERRORS [535]):
  ① git 오염 방지 — 209MB 가 매초 바뀌는 파일이라 커밋 대상이 되면 안 된다
  ② 브랜치 이동에 안전 — `git checkout` 해도 데이터가 그대로 (코드는 되돌려도 데이터는 못 되돌린다)
  ③ Claude Code VM FUSE 마운트 밖 → `.fuse_hidden*` 생성 차단
  종전 기본값이 폴더 안(`shared/jarvis.sqlite`)이라 **잔재 파일이 반복 생성**됐다.
"""
import os, sqlite3, json, shutil, logging
from pathlib import Path
from datetime import datetime, date, timedelta

log = logging.getLogger("jarvis.db")

# ★ .env 자가 로드 (단일 진입점 — db.py 가 import 순서와 무관하게 JARVIS_DB_PATH 를 항상 해석).
#   미로드 시 standalone 호출(검증 one-liner·.env 미로드 프로세스)이 기본 경로로 떨어져
#   *잔재 shared/jarvis.sqlite* 가 생기던 근본 원인 차단 (사용자 박제 2026-06-28).
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

# ★ 기본값을 *프로젝트 밖* 으로 (ERRORS [535], 사용자 판단 2026-07-27).
#   종전 기본값은 `shared/jarvis.sqlite` — 즉 **프로젝트 폴더 안**이었다.
#   `.env` 자가 로드(위)로 2026-06-28 에 한 번 막았지만 *기본값 자체는 그대로* 라
#   .env 가 없거나·깨지거나·다른 홈에서 실행되면 **또 잔재가 생긴다**.
#   실제로 344K 짜리 잔재(마지막 기록 2026-06-08, 31행)가 7주간 남아 혼동을 유발했다.
#   → 기본값을 홈으로 옮겨 *어떤 경로로 떨어져도 프로젝트가 오염되지 않게* 한다.
#   ② 동적 설계: 경로 문자열은 여기 한 곳. 다른 파일은 `from shared.db import DB_PATH`.
#   (api_server.py·shared/llm.py 가 같은 기본값을 각자 적고 있는데, 그건 이 상수를
#    import 못 하는 초기화 순서 때문 — 값이 일치하는지 selfcheck 로 감시한다.)
_default_db = Path.home() / ".jarvis" / "jarvis.sqlite"
DB_PATH     = Path(os.environ.get("JARVIS_DB_PATH", str(_default_db)))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
# ★ 백업도 프로젝트 밖 — 원본(DB)과 같은 곳에 둔다 (ERRORS [536], 2026-07-27).
#
#   종전 `shared/backups/` 는 **프로젝트 폴더 안**이었다. 문제는 용량(6.2GB)이 아니라
#   **`git clean -xdf` 한 번에 통째로 사라진다**는 것이다 — `.gitignore` 대상이라
#   그 명령의 삭제 범위에 정확히 들어간다(IDE 의 clean 기능도 같은 것을 쓴다).
#   백업의 존재 이유는 *실수해도 되게* 만드는 건데, 실수 한 번에 백업이 사라지면
#   백업이 아니다. 브랜치 이동·워크트리 생성도 같은 위험(실제로 워크트리 잔재 발견).
#
#   ② 동적 설계: `JARVIS_BACKUP_DIR` 로 오버라이드 가능. 기본은 DB 와 **같은 부모**에서
#   파생 — DB 경로를 옮기면 백업도 자동으로 따라간다(두 곳을 각각 고치지 않는다).
BACKUP_DIR = Path(os.environ.get("JARVIS_BACKUP_DIR", str(DB_PATH.parent / "backups")))

# ★ LangGraph ReAct 체크포인트 경로 — 이 파일이 소유 (ERRORS [537], 2026-07-27).
#   종전엔 `router.py` 가 `_ROOT / "shared" / "react_checkpoints.sqlite"` 로 **직접 조립**했다.
#   본 DB 는 `from shared.db import DB_PATH` 로 받아쓰면서 체크포인트만 손으로 적고 있었다 —
#   그래서 "어디에 박혀 있지?" 를 매번 찾아야 했다(① 위반).
#   ② 동적 설계: `DB_PATH.parent` 에서 파생 → DB 를 옮기면 체크포인트도 자동으로 따라간다.
#   `JARVIS_CHECKPOINT_PATH` 로 오버라이드 가능.
CHECKPOINT_PATH = Path(os.environ.get(
    "JARVIS_CHECKPOINT_PATH", str(DB_PATH.parent / "react_checkpoints.sqlite")))


class _AutoCloseConnection(sqlite3.Connection):
    """`with get_db() as conn:` 종료 시 커밋/롤백뿐 아니라 연결 자체도 닫는다.

    ★ ERRORS [318][3322] 재발 방지 — 표준 sqlite3.Connection 의 컨텍스트 매니저는
    트랜잭션(commit/rollback)만 관리하고 close() 는 하지 않는다. 148곳 호출부가
    전부 `with get_db() as conn:` 관용구를 쓰는데 이 gotcha 를 몰라 연결이 누적
    누수 → WAL 체크포인트 정체 → DB 비대화(453MB) → get_db() 자체가 무기한 대기
    → 데몬 hang(heartbeat 정체) 로 이어졌다. 단일 진입점(get_db)에서 한 번만 고치면
    148곳 호출부 전부 해소된다.
    """

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(DB_PATH), timeout=10, check_same_thread=False, factory=_AutoCloseConnection
    )
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

            -- 사용자 설정 (key-value 저장소: 알림 임계치, UI 테마 등)
            CREATE TABLE IF NOT EXISTS user_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

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
                next_directives TEXT DEFAULT '[]',  -- 다음날 작성 프롬프트에 주입할 지침 [{"do":"...","why":"..."}]
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
                model           TEXT    NOT NULL DEFAULT 'sonnet-5',
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
        # llm_attempts: error_log 행별 Tier 2(LLM) 시도 횟수 상한 캡 (job_retry_pending 무한 재시도 방지)
        try:
            conn.execute("ALTER TABLE error_log ADD COLUMN llm_attempts INTEGER DEFAULT 0")
        except Exception:
            pass
        # claimed_at: 처리 착수(선점) 시각 + 살아있음 신호(하트비트) — ERRORS [473]
        #   종전 수확기는 '오류가 *기록된* 시각(timestamp)' 으로 stuck 을 판정했는데,
        #   그 값은 작업 진행과 아무 상관이 없어 *살아 있는 세션* 을 죽은 걸로 오인했다.
        try:
            conn.execute("ALTER TABLE error_log ADD COLUMN claimed_at TEXT")
        except Exception:
            pass
        # provisional: 아직 재시도가 남은 '잠정' 실패 — Tier-2(LLM) 판정 보류 (ERRORS [476])
        #   액션이 끝나야 '일시적' 인지 '결정론적' 인지 알 수 있다.
        try:
            conn.execute("ALTER TABLE error_log ADD COLUMN provisional INTEGER DEFAULT 0")
        except Exception:
            pass
        # ★ 지침별 준수/위반 (2026-08-07 감사 — credit assignment 붕괴 시정)
        #   종전엔 한 배치의 지침 8개가 **전부 같은 보상**(그 글의 점수)을 받았다.
        #   그러면 어느 지침이 좋았는지 영영 구분되지 않는다(실측: 배치 53개 전부
        #   `count(distinct reward)=1`). 발행 게이트가 이미 계산해 놓고 로그로만 흘리던
        #   `violated_directives` 를 여기 적어 배치 안에서 변별을 만든다.
        #   NULL = 판정 없음(옛 행), 0 = 준수, 1 = 위반.
        try:
            conn.execute("ALTER TABLE insight_usage ADD COLUMN violated INTEGER DEFAULT NULL")
        except Exception:
            pass
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
        # post_analysis.quality_score: 발행글 100점 루브릭 총점 (ADR 014 보상 신호 — 2026-07-24).
        #   글품질 강화학습 보상 = quality_score/100. NULL = 미채점(옛 행·채점불가) → 보상 스킵.
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN quality_score REAL")
        except Exception:
            pass
        # post_analysis.rubric_items: 100점 루브릭 **항목별** 점수 (2026-08-07 신설).
        #   `post_scorer.items_compact(sr)` 의 `{항목key: 점수}` JSON.
        #   ★ 왜 필요했나 — 종전엔 총점 스칼라 한 칸뿐이라 채점기가 글마다 계산한 50개
        #     항목 결과가 **즉시 폐기**됐다. 그 결과 강화학습이 "이 글 67점" 이라는 한 덩어리
        #     신호만 받아, *어느 항목이 왜 0점인지* 를 모른 채 가중치를 굴리고 있었다.
        #   만점·섹션·표시명은 **넣지 않는다** — 채점기에서 파생한다(② 동적 설계).
        #   컬럼을 50개 만들지 않는 이유도 같다: 항목이 늘 때마다 스키마를 고치게 된다.
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN rubric_items TEXT")
        except Exception:
            pass
        # post_analysis.publish_meta: 발행에 실제로 붙은 메타 (2026-08-07 신설).
        #   `{"tags": [...], "meta_description": "..."}` — process_draft ⑫ 산출물.
        #   ★ 왜 필요했나 — 발행 draft 는 그 프로세스 안에서만 산다(경제는 subprocess).
        #     이 값이 DB 를 건너지 못하면 **발행 후 채점이 태그·메타를 못 본다** →
        #     발행 전엔 N7·T7 만점인데 DB 점수(=학습 보상)는 0점인 반쪽 적용이 된다.
        #     "개선했는데 벌을 받는" 상태 — 학습이 정확히 거꾸로 간다.
        try:
            conn.execute("ALTER TABLE post_analysis ADD COLUMN publish_meta TEXT")
        except Exception:
            pass
        # learning_insights.scope: 어떤 글 종류에 적용할 인사이트인지.
        # 'economic' / 'theme' / 'all'. 작성기가 호출 시 scope IN (post_type,'all') 만 주입.
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
                rewarded_at TEXT,
                violated    INTEGER DEFAULT NULL    -- NULL=판정없음 0=준수 1=위반
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

        # ★ 알림 아웃박스 (사용자 승인 2026-07-25) — 전송 실패한 텔레그램 메시지를 *보관* 한다.
        #   종전 `notify.send_tg` 는 실패 시 로그 한 줄 남기고 메시지를 버렸다. 2026-07-25
        #   네트워크 단절 중 4건이 영구 소멸했고 그중 하나가 "테마글 발행 건너뜀" 통보라,
        #   그날 테마글이 왜 없는지 아무도 몰랐다. 성공하면 행을 지우므로 평소엔 항상 빈 표.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notify_outbox (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                chat_id     TEXT DEFAULT '',
                text        TEXT NOT NULL,
                parse_mode  TEXT DEFAULT 'Markdown',
                attempts    INTEGER DEFAULT 0,
                last_error  TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outbox_created ON notify_outbox(created_at)")

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

    # ★ 위까지가 **베이스라인(v1)** — 여기 있는 CREATE/ALTER 는 전부 멱등이라 몇 번
    #   실행해도 안전하다. 그래서 과거분은 그대로 두고, *앞으로의 변경* 만 번호를 매겨
    #   `_MIGRATIONS` 로 관리한다(아래). 기존 18개 ALTER 를 지금 번호로 재작성하면
    #   이미 적용된 DB 와 새 DB 의 경로가 갈라져 위험만 커진다.
    _apply_migrations()


# ══════════════════════════════════════════════════════════════════════════════
#  ★★ 스키마 버전 관리 (2026-07-27)
#
#  종전: `try: ALTER TABLE ... except: pass` 18개. **동작은 하지만 세 가지가 없었다** —
#    ① "지금 DB 가 몇 번 버전인가" 를 알 방법  ② 적용 시각 기록  ③ 되돌리기 근거.
#  그래서 스키마가 바뀌어도 *언제 무엇이 왜 바뀌었는지* 가 코드 diff 에만 남았다.
#
#  방식: 번호 붙은 마이그레이션을 순서대로 1회씩 적용하고 `schema_migrations` 에 박제.
#    · 새 스키마 변경은 **`_MIGRATIONS` 에 한 줄 추가** — 다른 곳에 ALTER 를 흩지 말 것.
#    · 실패해도 부팅을 막지 않는다(로그 + 계속). 스키마 변경 때문에 데몬이 못 뜨는 것이
#      더 나쁘다. 대신 적용 안 된 버전이 남아 다음 부팅에 재시도된다.
#    · 멱등하게 쓸 것(ALTER 는 이미 있으면 예외 → 적용 성공으로 간주하고 기록).
# ══════════════════════════════════════════════════════════════════════════════

#: (버전, 설명, SQL) — **오름차순 유지**. 과거 번호를 수정하지 말 것(이미 적용된 DB 와 갈라짐).
_MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "베이스라인 — init_db 의 CREATE/ALTER 전체 (2026-07-27 이전 누적분)", ""),
    (2, "vision_agent_history 압축 — 직전과 같은 상태의 반복행 제거 (변화만 남김)", """
        -- ★ 2026-07-27: 이 표는 30초마다 무조건 append 돼 182,687행(DB 최대 테이블)이
        --   됐지만 읽는 코드가 없었다. 이제 collector 가 *상태 변화 시에만* 적재한다.
        --   과거분도 같은 규칙으로 맞춘다 — 직전 행과 status 가 같은 행은 정보가 없다
        --   (online 이 30초마다 반복). **변화 시점은 전부 보존되므로 정보 손실 0.**
        --   실측: 182,687 → 48행.
        DELETE FROM vision_agent_history
        WHERE id IN (
            SELECT id FROM (
                SELECT id, status,
                       LAG(status) OVER (PARTITION BY agent_id ORDER BY recorded_at, id) AS prev
                FROM vision_agent_history
            ) WHERE prev IS NOT NULL AND prev = status,
                    violated     INTEGER DEFAULT NULL   -- NULL=판정없음 0=준수 1=위반
                );
    """),
    (3, "style_corpus 제거 — 읽는 코드가 0인 브랜드 보이스 코퍼스 폐기", """
        -- ★ 2026-07-27: 이 표는 매일 02:30 잡이 적재했지만 **읽는 코드가 하나도 없었다**
        --   (search_similar / build_few_shot_block 호출자 0). 게다가 tfidf(2048d)와
        --   MiniLM(384d)이 섞여 색인돼 검색 시 차원 다른 행을 서로 건너뛰는 상태였다.
        --   적재·조회·검색 스택을 전부 걷어냈으므로 표도 함께 제거한다.
        DROP TABLE IF EXISTS style_corpus;
    """),
    (4, "keyword_favorites 제거 — 화면 연결이 없어 3개월간 테스트 1행뿐이던 기능 폐기", """
        -- ★ 2026-07-27: 찜한 키워드에 주제 점수 +10 을 주는 기능이었으나 추가·삭제 UI 가
        --   끝내 붙지 않았다. 실측 1행('어린이날', 2026-04-30 수기 입력)뿐이고 3개월간
        --   변동 0. 그 1행이 지금도 매 주제 선정마다 가산점을 주고 있었다 —
        --   쓰지 않는 기능이 조용히 선정 결과를 흔드는 상태. 코드·표 전부 폐기.
        DROP TABLE IF EXISTS keyword_favorites;
    """),
]


def schema_version() -> int:
    """현재 DB 스키마 버전 — 적용된 마이그레이션 중 최대 번호. 미적용 DB 는 0."""
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    INTEGER PRIMARY KEY,
                    note       TEXT,
                    applied_at TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:                              # noqa: BLE001
        log.warning(f"[db/schema] 버전 조회 실패: {e}")
        return 0


def _apply_migrations() -> int:
    """미적용 마이그레이션을 번호순으로 적용. Returns: 이번에 적용한 개수."""
    cur_ver = schema_version()
    applied = 0
    for version, note, sql in sorted(_MIGRATIONS, key=lambda m: m[0]):
        if version <= cur_ver:
            continue
        try:
            with get_db() as conn:
                if sql.strip():
                    conn.executescript(sql)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, note) VALUES (?,?)",
                    (version, note),
                )
                conn.commit()
            applied += 1
            log.info(f"[db/schema] v{version} 적용 — {note}")
        except Exception as e:                          # noqa: BLE001
            # 부팅을 막지 않는다. 다음 부팅에 재시도된다.
            log.warning(f"[db/schema] v{version} 적용 실패(다음 부팅 재시도): {e}")
            break                                       # 순서 보장 — 실패 뒤는 건너뛰지 않는다
    return applied


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
    """오늘 날짜 pipeline 항목을 기회점수 내림차순으로 반환 (RADAR 추천 대기열 조회용)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, theme, sector, opportunity_score, created_at FROM pipeline "
            "WHERE status = 'suggested' AND date(created_at) = date('now','localtime') "
            "ORDER BY opportunity_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


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
    # ★ 시크릿 마스킹 관문 (2026-07-30 전수 감사 3위 — 사용자 승인).
    #   `events.payload` 에도 봇 토큰이 39행 평문으로 있었다(오류 payload 를 그대로 실어서).
    #   `error_collector` 와 같은 이유로 *생산자* 가 아니라 **적재 관문** 에서 거른다(원칙①).
    try:
        from shared.secrets import mask_obj as _mask_obj
        payload = _mask_obj(payload or {})
    except Exception:
        payload = payload or {}
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO events (event_type, source, payload) VALUES (?,?,?)",
            (event_type, source, json.dumps(payload, ensure_ascii=False)),
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
                            image_paths: str = "[]",
                            publish_meta: "dict | None" = None) -> int:
    """발행 직후 분석 대기 레코드 생성. 반환값: 생성된 id.

    source_keyword: RADAR pipeline 트리거 시 trends.keyword 와 동일한 raw 키워드.
                    학습 페어링(learn_log)의 join 키로 사용. 비어 있으면 theme fallback.
    post_type:      글 종류별 분리 학습용. 'economic' / 'theme' / 자유문자열.
                    daily_review 가 GROUP BY post_type 으로 분기, learning_insights.scope
                    로 매핑되어 같은 종류 글에만 인사이트 주입.
    """
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO post_analysis
               (platform, theme, title, url,
                original_content, original_html, source_keyword, post_type, image_paths,
                publish_meta)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (platform, theme, title, url,
             original_content, original_html,
             (source_keyword or "").strip(),
             (post_type or "").strip() or None,
             image_paths or "[]",
             _items_json(publish_meta)),
        )
        return cur.lastrowid


def get_pending_analysis(limit: int = 10) -> list:
    """분석 대기 중인 글 목록 — **먼저 발행된 것부터**(FIFO).

    ★ 2026-07-30 `DESC` → `ASC`. 대기열을 최신순으로 꺼내면 *같은 슬롯에서 나중에 발행된 글이
      항상 먼저* 분석돼 LLM 예산을 먼저 쓴다. 그 결과 먼저 발행된 쪽(네이버, 07:12)이
      **매번** 스로틀에 걸려 미채점으로 남았다 — 실측 07-26~29 naver 0/8 · tistory 7/8.
      대기열은 먼저 들어온 것을 먼저 처리해야 한다(공정성이 곧 편향 제거다).
      ※ 이것만 고치면 편향 방향만 뒤집힌다 — 근본은 `post_quality_analyzer` 의
        `_essential` 교정이고, 둘은 같은 커밋에 있어야 한다.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM post_analysis WHERE status='pending_analysis' AND is_revised=0 "
            "ORDER BY created_at ASC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_unscored_analyzed(since: str, limit: int = 5) -> list:
    """분석은 끝났는데 **점수만 비어 있는** 글 — 재채점 대기열.

    ★ 왜 별도 대기열인가 (2026-08-04 감사 6위)
      `get_pending_analysis` 는 `status='pending_analysis'` 만 본다. 그런데 루브릭 채점이
      실패하면 규칙 폴백으로 넘어가 **분석은 '완료'로 표시되고 점수만 None 으로 남는다**.
      그 순간 이 글은 어느 대기열에도 없다 — 영원히 미채점이고, ADR 014 보상 신호가
      조용히 사라진다. 실측 08-02~08-04 티스토리 4건 중 3건이 그렇게 사라졌다.

    ★ `since` 를 인자로 받는 이유 (① 단일 진입점)
      '언제까지 재채점이 의미 있는가' 는 **보상을 소비하는 잡의 일정** 이 정한다.
      그 판단은 도메인(post_quality_analyzer)이 하고, 여기는 질의만 한다.
      DB 계층이 스케줄러를 import 하기 시작하면 층이 무너진다.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM post_analysis "
            "WHERE quality_score IS NULL AND is_revised=0 "
            "  AND analyzed_at IS NOT NULL AND created_at >= ? "
            "ORDER BY created_at ASC LIMIT ?", (since, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def save_quality_score(analysis_id: int, quality_score: float,
                       rubric_items: "dict | None" = None) -> bool:
    """재채점 결과 — **점수(와 항목)만** 채운다.

    제안·상태·analyzed_at 을 건드리지 않는다. 건드리면 텔레그램 재전송·승인 재요청이
    딸려와 사용자에게 같은 글이 두 번 간다. 이미 비어 있을 때만 쓴다(경합 방어).
    """
    _ij = _items_json(rubric_items)
    with get_db() as conn:
        if _ij is None:
            cur = conn.execute(
                "UPDATE post_analysis SET quality_score=? WHERE id=? AND quality_score IS NULL",
                (quality_score, analysis_id),
            )
        else:
            cur = conn.execute(
                "UPDATE post_analysis SET quality_score=?, rubric_items=? "
                "WHERE id=? AND quality_score IS NULL",
                (quality_score, _ij, analysis_id),
            )
        return cur.rowcount > 0


def backfill_rubric_items(analysis_id: int, rubric_items: dict) -> bool:
    """★ 이미 총점은 있는데 **항목만 비어 있는** 옛 행을 채운다 (2026-08-07 소급).

    `save_quality_score` 는 `quality_score IS NULL` 인 행만 쓰므로 옛 행에 닿지 못한다.
    총점은 **건드리지 않는다** — 그날의 보상 신호를 사후에 바꾸면 학습 이력이 오염된다.
    """
    _ij = _items_json(rubric_items)
    if _ij is None:
        return False
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE post_analysis SET rubric_items=? WHERE id=? AND rubric_items IS NULL",
            (_ij, analysis_id),
        )
        return cur.rowcount > 0


def get_rubric_items(analysis_id: int) -> dict:
    """저장된 항목별 점수 `{항목key: 점수}`. 없으면 빈 dict."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT rubric_items FROM post_analysis WHERE id=?", (analysis_id,)
        ).fetchone()
    if not row or not row["rubric_items"]:
        return {}
    try:
        v = json.loads(row["rubric_items"])
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


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


def _items_json(rubric_items: "dict | None") -> "str | None":
    """항목별 점수를 저장 문자열로 — **직렬화를 한 곳에서만** 한다(① 단일 진입점).

    두 저장 경로(`save_analysis_result` · `save_quality_score`)가 각자 json.dumps 하면
    한쪽만 형식을 바꾸는 사고가 난다. 빈 dict 는 '항목 없음'이 아니라 '채점 안 함'과
    구분이 안 되므로 None 으로 떨어뜨린다.
    """
    if not rubric_items:
        return None
    return json.dumps(rubric_items, ensure_ascii=False, sort_keys=True)


def save_analysis_result(analysis_id: int, suggestions: list,
                         quality_score: "float | None" = None,
                         rubric_items: "dict | None" = None):
    """분석 결과 저장 → status: analyzed.

    quality_score = 발행글 100점 루브릭 총점(post_scorer). ADR 014 강화학습 보상 신호로
    23:45 job_quality_learn 이 읽는다 (reward = score/100). None = 미채점(보상 스킵).

    rubric_items = `post_scorer.items_compact(sr)` — 항목별 학습의 입력 (2026-08-07).
      None 이면 **덮어쓰지 않는다** — 옛 호출자가 항목을 지우는 일이 없어야 한다.
    """
    _ij = _items_json(rubric_items)
    with get_db() as conn:
        if _ij is None:
            conn.execute(
                "UPDATE post_analysis SET suggestions=?, quality_score=?, status='analyzed', "
                "analyzed_at=datetime('now','localtime') WHERE id=?",
                (json.dumps(suggestions, ensure_ascii=False), quality_score, analysis_id),
            )
        else:
            conn.execute(
                "UPDATE post_analysis SET suggestions=?, quality_score=?, rubric_items=?, "
                "status='analyzed', analyzed_at=datetime('now','localtime') WHERE id=?",
                (json.dumps(suggestions, ensure_ascii=False), quality_score, _ij, analysis_id),
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


# ── Maintenance (백업·정리) ───────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
#  백업 보존 — 계층 보존(GFS: Grandfather-Father-Son)  ★ ERRORS [536] 2026-07-27
# ══════════════════════════════════════════════════════════════════════════════
#
#  ★ 왜 매일 30개가 아닌가 (실측 근거):
#    종전 "30일 매일 보관" 은 **24개 6.2GB** 였다. 본 DB 가 200MB 이므로
#    백업 1개 = DB 전체다. 30일을 채우면 **7~8GB** 에서 평형을 이룬다.
#
#  ★ 백업은 최근 것일수록 가치가 높다:
#      "어제 실수로 지웠다"     → 1~2일 전이 필요
#      "이번 주에 뭔가 틀어졌다" → 7일 전
#      "3주 전으로 되돌린다"    → 그 사이 3주치 발행·학습이 통째로 날아간다
#                                → **되돌릴 수 있어도 되돌리지 않는다**
#    따라서 오래된 구간은 *간격을 벌려도* 실질 손실이 없다.
#
#  ★ GFS = 최근은 촘촘히, 과거는 성기게. 백업 소프트웨어의 표준 방식.
#    커버 기간(30일)은 그대로 두면서 개수를 절반으로 줄인다.
#
#  ② 동적 설계: 계층을 코드에 박지 않고 이 레지스트리에서 파생.
#     무배포 조정: `DB_BACKUP_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY`
_BACKUP_TIERS: tuple[tuple[str, str, int], ...] = (
    #  (계층,     설명,                     보관 개수)
    ("daily",   "최근 N일 — 매일",           7),
    ("weekly",  "그 이전 — 주 1회(월요일)",    4),
    ("monthly", "그 이전 — 월 1회(1일)",      3),
)


def _backup_keep(tier: str, default: int) -> int:
    """계층별 보관 개수 — *호출 시점* env 조회 (모듈 로드 캡처 금지)."""
    raw = (os.getenv(f"DB_BACKUP_KEEP_{tier.upper()}") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return default


def _gfs_keep_set(dates: list[date]) -> set[date]:
    """보관할 날짜 집합을 GFS 규칙으로 *파생*.

    ★ 규칙을 날짜 리스트에서 파생한다 — "오늘부터 며칠 전" 같은 절대 계산을 쓰면
      백업이 하루 걸렀을 때 구멍이 생긴다. **가진 것 중에서 고른다.**
    """
    if not dates:
        return set()
    ds = sorted(set(dates), reverse=True)          # 최신 우선
    keep: set[date] = set()

    keep.update(ds[: _backup_keep("daily", 7)])    # ① 최근 N개는 무조건

    # ② 주간 — 각 ISO 주(연도,주차)의 *가장 최신* 1개씩
    seen_w: dict[tuple, date] = {}
    for d in ds:
        k = d.isocalendar()[:2]
        seen_w.setdefault(k, d)
    keep.update(list(seen_w.values())[: _backup_keep("weekly", 4) + _backup_keep("daily", 7)])

    # ③ 월간 — 각 (연,월)의 가장 최신 1개씩
    seen_m: dict[tuple, date] = {}
    for d in ds:
        seen_m.setdefault((d.year, d.month), d)
    keep.update(list(seen_m.values())[: _backup_keep("monthly", 3)])
    return keep


def verify_backup(path) -> str:
    """방금 만든 백업이 **성한가** — 빈 문자열이면 정상, 아니면 사유.

    ★ 왜 필요한가 (2026-08-05 실측): 저장소 전체에 `integrity_check` 가 **0행**이었다.
      백업 파일이 2.0GB 쌓여 있는데 *그중 하나라도 복원 가능한지 확인한 적이 없었다.*
      백업은 "만들었다" 가 아니라 "복원된다" 여야 백업이다.

    ★ 비용: 159MB DB 에 0.8초 (실측). 하루 1회니 무시할 수 있다.

    ★ 왜 여기(shared/db)인가: '만들어진 DB 파일이 성한가' 는 DB 도메인의 질문이다.
      다른 모듈에 두면 `shared.db ↔ 그 모듈` 순환 import 가 생긴다.
    """
    p = Path(path)
    if not p.exists():
        return "백업 파일이 만들어지지 않음"
    if p.stat().st_size <= 0:
        return "백업 파일이 0바이트"
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
        try:
            r = con.execute("PRAGMA integrity_check").fetchone()
            if not r or str(r[0]).lower() != "ok":
                return f"integrity_check 실패: {r}"
            # 핵심 테이블이 실제로 읽히는가 (헤더만 성한 파일 방어)
            for t in ("post_analysis", "error_log", "job_runs"):
                con.execute(f"SELECT count(*) FROM {t}").fetchone()
        finally:
            con.close()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


def backup_gaps(days: int = 7) -> list:
    """최근 N일 중 **백업이 없는 날** — 결손 감지.

    ★ 발행 완결성 감사와 같은 사고방식: *기대* 를 파생하고 *실제* 와 대조한다.
      기대 = 매일 1개(백업 잡이 cron daily). 실제 = 파일명의 날짜.
      실측 2026-08-05: 07-31·08-02 백업이 **조용히 빠져 있었다** — 아무도 몰랐다.
      ※ GFS 보존이 오래된 날을 의도적으로 지우므로 **최근 N일만** 본다.
    """
    have = set()
    for p in BACKUP_DIR.glob("jarvis_*.sqlite"):
        try:
            have.add(date.fromisoformat(p.stem.replace("jarvis_", "")))
        except Exception:
            continue
    today = date.today()
    return [(today - timedelta(days=i)).isoformat()
            for i in range(1, max(1, days) + 1)
            if (today - timedelta(days=i)) not in have]


def backup_db(retention_days: int = 30) -> dict:
    """SQLite .backup API 로 WAL 안전 백업 + **GFS 계층 보존**.

    ★ `retention_days` 는 **하위호환용 상한**으로만 쓴다 — 이보다 오래된 것은
      GFS 가 남기려 해도 지운다. 실제 선별은 `_gfs_keep_set()` 이 한다.
      (호출부 `job_db_backup(retention_days=30)` 을 안 깨기 위해 시그니처 유지.)

    반환: {"backup": Path, "removed": int, "size_kb": int, "kept": int}
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
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

    # 1-B) ★ 무결성 검증 — **retention 앞** (2026-08-05).
    #   순서가 정책이다: 새 백업이 깨졌는데 옛 백업을 지우면 성한 사본이 하나도 안 남는다.
    #   깨진 파일은 `.corrupt` 로 남기지 않는다 — 그러면 retention glob 밖이라 영구 잔존한다
    #   (실측 선례: `jarvis_premask_2026-08-02.sqlite` 176MB 가 그렇게 관리 밖에 있다).
    #   쓸모없는 파일이므로 즉시 지우고 사유만 박제한다.
    _bad = verify_backup(target)
    if _bad:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            from JARVIS07_GUARDIAN.error_collector import report as _rep
            _rep("BackupIntegrityFailed", "infra",
                 message=f"백업 무결성 검증 실패 — 파일 폐기, 옛 백업 보존: {_bad}",
                 module=__name__, func_name="backup_db",
                 context={"target": str(target), "reason": _bad, "kind": "daemon_down"})
        except Exception:
            pass
        raise RuntimeError(f"백업 무결성 검증 실패: {_bad}")

    # 2) Retention — GFS 계층 보존 (일 7 + 주 4 + 월 3 ≈ 14개로 30일 커버)
    cutoff = date.today() - timedelta(days=retention_days)
    found: dict[date, Path] = {}
    for p in BACKUP_DIR.glob("jarvis_*.sqlite"):
        try:
            found[date.fromisoformat(p.stem.replace("jarvis_", ""))] = p
        except Exception:  # noqa: BLE001 — 이름 규칙 밖 파일은 건드리지 않는다
            continue

    keep = _gfs_keep_set(list(found))
    removed = 0
    for d, p in found.items():
        if d in keep and d >= cutoff:
            continue                       # 보관 대상
        try:
            p.unlink()
            # WAL/SHM 동반 파일도 같이 (남으면 다음 조회에서 혼동)
            for suf in ("-wal", "-shm"):
                sib = p.with_name(p.name + suf)
                if sib.exists():
                    sib.unlink()
            removed += 1
        except Exception as e:  # noqa: BLE001
            log.warning(f"[db/backup] 만료 백업 삭제 실패(무시) {p.name}: {e}")

    return {
        "backup":  target,
        "removed": removed,
        "kept":    len(found) - removed,
        "size_kb": target.stat().st_size // 1024,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  ★★ 보존 정책 레지스트리 — **테이블별 보존 기간의 단일 진실 소스** (2026-07-27)
#
#  왜 (실측): DB 209MB 의 대부분이 무한 누적 테이블이었다 —
#    `vision_agent_history` **192,417행**(30초마다 적재) · `job_runs` **155,483행** ·
#    `qa_ingested_sessions` 15,567행. 이 중 뒤 둘은 **보존 규칙이 아예 없었다**.
#  그리고 규칙이 있는 둘조차 일수가 *잡 콜백에 박혀* 있었다
#    (`job_cleanup_events(days=30)` · `job_cleanup_vision_history(days=7)`).
#  → ① 단일 진입점 위반: "이 테이블 며칠 보관?" 의 답이 코드 두 곳에 흩어짐.
#  → ② 동적 설계 위반: 숫자가 호출부에 박혀 있어 바꾸려면 잡을 찾아가야 함.
#
#  ★ 설계 원칙: **테이블을 만들 때 보존 기간을 함께 선언한다.** 종전엔 DB 가 453MB 로
#    불어 데몬이 hang 된 *뒤에* 정리 잡이 생겼다(사후 대응). 이제 여기에 한 줄 추가하지
#    않으면 그 테이블은 정리 대상이 아니라는 것이 **명시적으로 드러난다**.
#
#  형식: 테이블 → (보존일수, 시각컬럼, 설명).  보존일수 0 = 영구 보존(정리 안 함).
#  무배포 조정: `DB_RETENTION_<대문자테이블명>=일수`  (예: DB_RETENTION_JOB_RUNS=30)
# ══════════════════════════════════════════════════════════════════════════════

RETENTION: dict[str, tuple[int, str, str]] = {
    # 빠르게 쌓이는 관측 데이터 — 짧게
    # ★ vision_agent_history 는 2026-07-27 부터 **상태 변화 시에만** 적재한다
    #   (종전 30초마다 → 182,437행 = DB 최대 테이블, 그런데 읽는 코드 0).
    #   양이 1/1000 로 줄었으므로 보존을 7일 → **30일로 늘렸다** — 대시보드가
    #   "지난 30일 언제 죽었나" 를 차트로 보여준다(`/api/vision/history`).
    "vision_agent_history":  (30,  "recorded_at",  "에이전트 상태 *변화* 이력 (30일 차트 근거)"),
    "events":                (30,  "created_at",   "이벤트 버스 기록"),
    "job_runs":              (60,  "started_at",   "잡 실행 이력 — 대시보드는 최근분만 본다"),
    "qa_ingested_sessions":  (90,  "ingested_at",  "QA 세션 흡수 이력(중복 방지용 표식)"),
    "llm_rate_limit_events": (90,  "ts",           "한도 이벤트"),
    "tool_runs":             (90,  "ran_at",       "도구 실행 이력"),
    "llm_token_usage":       (180, "ts",           "토큰 장부 — 추세 분석에 쓰이므로 길게"),
    # ★ 영구 보존(0) — 지우면 안 되는 것들. *명시적으로* 0 을 적어 '누락' 과 구분한다.
    "error_log":             (0,   "timestamp",    "오류 이력 — 학습 자산. 영구"),
    "post_analysis":         (0,   "created_at",   "발행 이력 — 영구"),
    "self_repair_runs":      (0,   "ran_at",       "자가수리 회차 메트릭 — 학습 곡선. 영구"),
    "insight_usage":         (0,   "used_at",      "지침 보상 귀속 — 학습 자산. 영구"),
    "keyword_embeddings":    (0,   "indexed_at",   "키워드 벡터 — 재생성 비용 큼. 영구"),
}


# ★ 본 DB 밖에 있는 SQLite — 같은 보존 원칙을 적용해야 하는데 위 루프가 닿지 않는다.
#   (ERRORS [535], 사용자 판단 2026-07-27)
#   `react_checkpoints.sqlite` 는 LangGraph SqliteSaver 가 쓰는 **별도 파일**이라
#   `get_db()` 커넥션으로는 접근 불가 → 위 RETENTION 루프에서 구조적으로 누락됐다.
#   실측 52MB / 체크포인트 898개 / writes 2,809행 — 정리 잡이 없어 무한 누적 중이었다.
#   ★ 여기 선언해 두면 "정리 대상이 아니다" 가 아니라 "정리 대상인데 파일이 다르다" 가
#     명시적으로 드러난다. RETENTION 과 같은 형식(일수·설명)을 유지한다.
EXTERNAL_RETENTION: dict[str, tuple[Path, int, str]] = {
    "react_checkpoints": (
        CHECKPOINT_PATH, 14,
        "ReAct 대화 체크포인트 — 재개는 최근 것만 의미 있다(오래된 스레드는 이어갈 일이 없음)",
    ),
}


def external_retention_days(name: str) -> int:
    """외부 SQLite 보존 일수 — env 우선(`DB_RETENTION_<대문자>`), 없으면 레지스트리."""
    spec = EXTERNAL_RETENTION.get(name)
    if not spec:
        return 0
    raw = os.getenv(f"DB_RETENTION_{name.upper()}", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return spec[1]


def cleanup_external_sqlite(vacuum: bool = True) -> dict:
    """본 DB 밖 SQLite 정리 — `EXTERNAL_RETENTION` 선언분만.

    ★ LangGraph 체크포인트 스키마는 라이브러리 소유라 컬럼명을 박지 않는다(② 동적 설계).
      `checkpoints` 테이블의 실제 컬럼을 PRAGMA 로 조회해 시각 후보를 *파생* 하고,
      시각 컬럼이 없으면 **thread_id 단위로 최신 N개만 남기는** 방식으로 degrade 한다.
      (스키마를 가정하고 짜면 라이브러리 업그레이드에 조용히 깨진다.)
    """
    out: dict = {}
    for name, (path, _d, _desc) in EXTERNAL_RETENTION.items():
        days = external_retention_days(name)
        if days <= 0 or not path.exists():
            continue
        before = path.stat().st_size
        try:
            con = sqlite3.connect(str(path), timeout=10)
            cols = {r[1] for r in con.execute("PRAGMA table_info(checkpoints)")}
            if not cols:
                con.close()
                continue
            ts_col = next((c for c in ("created_at", "ts", "checkpoint_ts") if c in cols), "")
            if ts_col:
                cur = con.execute(
                    f'DELETE FROM checkpoints WHERE "{ts_col}" < '
                    f"datetime('now','localtime',?)", (f"-{days} days",))
                n = cur.rowcount or 0
            else:
                # 시각 컬럼 없음 → thread 별 최신 1개만 남기고 정리 (rowid 순서로 파생)
                cur = con.execute(
                    "DELETE FROM checkpoints WHERE rowid NOT IN "
                    "(SELECT MAX(rowid) FROM checkpoints GROUP BY thread_id)")
                n = cur.rowcount or 0
            # 고아 writes 정리 (checkpoint 가 사라졌는데 남은 쓰기 기록)
            if "writes" in {r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}:
                wcols = {r[1] for r in con.execute("PRAGMA table_info(writes)")}
                if "checkpoint_id" in wcols and "checkpoint_id" in cols:
                    con.execute("DELETE FROM writes WHERE checkpoint_id NOT IN "
                                "(SELECT checkpoint_id FROM checkpoints)")
            con.commit()
            if vacuum and n:
                con.execute("VACUUM")
            con.close()
            if n:
                freed = (before - path.stat().st_size) / 1048576
                out[name] = {"deleted": n, "freed_mb": round(freed, 1)}
        except Exception as e:  # noqa: BLE001 — 정리 실패가 데몬을 막으면 안 된다
            log.warning(f"[db/retention] 외부 SQLite {name} 정리 실패(무시): {e}")
    return out


def retention_days(table: str) -> int:
    """이 테이블의 보존 일수 — env 우선, 없으면 레지스트리 (② 런타임 파생)."""
    spec = RETENTION.get(table)
    if not spec:
        return 0
    raw = os.getenv(f"DB_RETENTION_{table.upper()}", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return spec[0]


def cleanup_by_retention(vacuum: bool = True) -> dict:
    """레지스트리에 선언된 모든 테이블을 한 번에 정리 — **정리의 단일 진입점**.

    Returns: `{table: 삭제행수}` (+ `_vacuum_mb` 회수 용량).
    ★ VACUUM 은 마지막에 **한 번만** — 테이블마다 돌리면 209MB 를 N번 재기록한다.
    """
    out: dict = {}
    total = 0
    for table, (_d, ts_col, _desc) in RETENTION.items():
        days = retention_days(table)
        if days <= 0:
            continue                                   # 영구 보존
        try:
            with get_db() as conn:
                cur = conn.execute(
                    f'DELETE FROM "{table}" '
                    f'WHERE "{ts_col}" IS NOT NULL '
                    f'  AND "{ts_col}" < datetime(\'now\',\'localtime\',?)',
                    (f"-{days} days",),
                )
                n = cur.rowcount or 0
                conn.commit()
            if n:
                out[table] = n
                total += n
        except Exception as e:                          # noqa: BLE001
            log.warning(f"[db/retention] {table} 정리 실패(무시): {e}")
    if vacuum and total:
        before = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        try:
            with sqlite3.connect(str(DB_PATH), timeout=120) as v:
                v.execute("VACUUM")
            after = DB_PATH.stat().st_size if DB_PATH.exists() else 0
            out["_vacuum_mb"] = round((before - after) / 1024 / 1024, 1)
        except Exception as e:                          # noqa: BLE001
            log.warning(f"[db/retention] VACUUM 실패(무시): {e}")
    return out


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
            # ★ naver_rank 포함 (ERRORS [483]) — 조회수는 플랫폼이 안 주므로 순위가 실질 학습
            #   신호다. 종전엔 이 SELECT 에서 빠져 있어 build_target 이 순위를 못 봤다.
            f"""SELECT keyword, sector, platform, trend_score, perf_boost, freshness,
                       velocity, competition, predicted_opp, actual_views, days_after,
                       logged_at, naver_rank
                FROM learn_log
                WHERE logged_at >= datetime('now', '-{int(max_age_days)} days', 'localtime')
                  AND (actual_views IS NOT NULL OR naver_rank IS NOT NULL)""",
        ).fetchall()
    return [dict(r) for r in rows] if len(rows) >= min_samples else []


def learn_log_count() -> int:
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) FROM learn_log").fetchone()[0]


# ── 자가학습 — learned_weights ──────────────────────────────────

# 기본(하드코딩) 가중치 — 학습 데이터 부족 시 fallback. analyzer.opportunity_score 의 기존 값과 일치.
DEFAULT_WEIGHTS = {
    "w_trend": 0.45, "w_perf": 1.0, "w_fresh": 0.85,
    "w_velocity": 0.0, "w_competition": 0.0,   # 유령 피처 — 미사용 (ERRORS [485])
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

    # ★ 학습 지침 위생 게이트 (2026-08-02 전수 감사 6위 — 사용자 승인).
    #   판정 규칙 본체는 강화학습 소유자 `JARVIS07_GUARDIAN.quality_learner`(ADR 014) 단독 —
    #   저장소는 *묻기만* 한다. 여기 규칙을 복사하면 그 순간 두 곳이 어긋나기 시작한다(원칙①).
    #   호출자가 3곳(post_quality_analyzer · daily_review · trend_theme_writer)이라
    #   각 호출부에 검사를 흩지 않고 **반드시 지나가는 이 관문** 에서 한 번 막는다.
    try:
        from JARVIS07_GUARDIAN.quality_learner import directive_issues as _di
        _issues = _di(directive)
        if _issues:
            print(f"  ⚠️ 학습 지침 거부 [{scope}:{insight_key}]: {', '.join(_issues)}"
                  f" — {str(directive)[:50]}")
            return 0
    except ImportError:
        pass   # 게이트 미가용 시 종전 동작 유지 (학습이 멈추는 것보다 낫다)

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
    """작성 프롬프트 보강용 — 최근 N일 활성 + 가중치 상위 N개.

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
               -- ★ weight=0 은 '무력화' 를 뜻한다 (2026-08-02 오염 지침 378건 정리).
               --   종전엔 이 필터가 없어 weight 를 0 으로 내려도 **그대로 주입** 됐다.
               AND li.weight > 0
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


def mark_usage_violated(batch_id: str, insight_ids: list) -> int:
    """이번 배치에서 **지켜지지 않은** 지침을 표시 (2026-08-07).

    표시된 행은 보상 귀속 때 감점을 받아, 같은 글이라도 지침마다 다른 신호가 된다.
    `insight_ids` 에 없는 같은 배치 행은 0(준수)으로 마감한다 — 판정이 있었다는 뜻.
    """
    if not batch_id:
        return 0
    with get_db() as conn:
        conn.execute("UPDATE insight_usage SET violated = 0 "
                     "WHERE batch_id = ? AND violated IS NULL", (batch_id,))
        if not insight_ids:
            return 0
        q = ",".join("?" * len(insight_ids))
        cur = conn.execute(
            f"UPDATE insight_usage SET violated = 1 "
            f"WHERE batch_id = ? AND insight_id IN ({q})",
            [batch_id, *[int(i) for i in insight_ids]])
        return cur.rowcount


def latest_batch(scope: str, platform: str) -> str:
    """이 조합의 **가장 최근 미귀속 배치 id** — 게이트가 방금 주입분을 찾을 때 쓴다."""
    with get_db() as conn:
        r = conn.execute(
            "SELECT batch_id FROM insight_usage "
            "WHERE scope = ? AND platform = ? AND reward IS NULL "
            "ORDER BY id DESC LIMIT 1", (scope, platform)).fetchone()
    return str(r[0]) if r else ""


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
                         update_weight: bool = True,
                         neutral: float = 0.5) -> None:
    """보상 귀속 — usage 행 마감 + learning_insights 가중치 EMA 갱신.

    weight ← clamp(0.05, 3.0, weight + alpha*(reward - neutral))

    ★ `neutral` 을 인자로 받는 이유 (2026-08-07 감사)
      중립점 0.5 를 박아 두었더니 실측 점수 분포(59~77)에서 **Δw 가 항상 양수** 였다.
      "쓰인 지침은 전부 생존" 이라 하향이 구조적으로 불가능했다.
      *무엇이 중립인가* 는 보상 도메인(`quality_learner.reward_neutral()`)이 안다 —
      DB 계층은 질의만 한다. 기본값 0.5 는 하위호환용이고, 호출자가 파생값을 넘긴다.
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
                       weight       = max(0.05, min(3.0, weight + ? * (? - ?)))
                   WHERE id = ?""",
                (float(reward), float(alpha), float(reward), float(neutral), int(insight_id)),
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

# ★ error_log.message 저장 길이 — 단일 진실 소스 (사용자 박제 2026-07-25)
#   종전엔 dedup SELECT 가 message[:500], INSERT 가 message[:2000] 로 *서로 다른 값* 이었다.
#   → 500자를 넘는 메시지는 저장된 값(2000자 절단)과 조회 키(500자 절단)가 영영 달라
#     `message=?` 가 절대 매칭되지 않는다 = **dedup 영구 미스** (seen_count 누적 불가,
#     같은 오류가 매번 새 행으로 적재). 같은 값이 두 곳에 박힌 ①단일 진입점 위반의 전형.
#   이제 두 쿼리 모두 아래 `_error_message_key()` 한 곳에서 파생한다(②).
ERROR_MESSAGE_MAX = 2000


def _error_message_key(message: str) -> str:
    """error_log.message 로 *실제 저장되는* 정규 형태 — 조회 키와 저장 값의 단일 파생원.

    None/빈 값도 ""(빈 문자열)로 통일한다. 종전 SELECT 는 falsy 메시지를 None 으로 넘겨
    INSERT 가 저장한 ""(빈 문자열) 행과 `message=?` 가 매칭되지 않았다 — 같은 병의 2차 발현.
    """
    return (message or "")[:ERROR_MESSAGE_MAX]


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
    # ★ 조회 키 = 저장 값. 한 번만 계산해 SELECT·INSERT 양쪽에 *같은 값* 을 넘긴다.
    msg_key = _error_message_key(message)
    with get_db() as conn:
        # 중복 검사 (최근 1시간 내 동일 오류)
        existing = conn.execute(
            """SELECT id, seen_count FROM error_log
               WHERE source=? AND module IS ? AND error_type=?
                 AND message=? AND status!='fixed'
                 AND timestamp >= datetime('now','-1 hour','localtime')
               ORDER BY id DESC LIMIT 1""",
            (source, module, error_type, msg_key),
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
             msg_key, traceback, context, severity),
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


# ══════════════════════════════════════════════════════════════════
#  알림 아웃박스 — 전송 실패한 텔레그램 메시지 보관 (사용자 승인 2026-07-25)
#  · SQL 은 여기(DB 소유자), *언제 보내고 언제 버릴지* 정책은 shared/notify.py(전송 소유자).
#  · 성공 = 행 삭제. 그래서 평소 이 표는 비어 있고, 행이 있으면 곧 "아직 못 전한 말" 이다.
# ══════════════════════════════════════════════════════════════════

def outbox_put(text: str, parse_mode: str = "Markdown", chat_id: str = ""):
    """전송 실패한 메시지를 보관. Returns: row id (실패 시 None — 알림 때문에 죽지 않는다)."""
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO notify_outbox (chat_id, text, parse_mode) VALUES (?,?,?)",
                (chat_id or "", text, parse_mode or ""),
            )
            return cur.lastrowid
    except Exception:
        return None


def outbox_has_pending() -> bool:
    """보관 중인 메시지가 있는가 — **DB 가 진실**(발행 subprocess 가 넣은 것도 보인다).

    ★ 메모리 플래그를 쓰지 않는 이유: 경제 브리핑은 subprocess 라 데몬 메모리의 플래그로는
      "저쪽이 넣은 미전송" 을 영영 못 본다 (CLAUDE.md 프로세스 경계 규칙).
    """
    try:
        with get_db() as conn:
            return conn.execute("SELECT 1 FROM notify_outbox LIMIT 1").fetchone() is not None
    except Exception:
        return False


def outbox_pending(limit: int = 50) -> list:
    """보관 중인 메시지를 **오래된 순** 으로. 순서를 지켜야 사건 순서대로 읽힌다."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, created_at, chat_id, text, parse_mode, attempts "
                "FROM notify_outbox ORDER BY id LIMIT ?", (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def outbox_claim(row_id: int, attempts: int) -> bool:
    """보내기 *직전* 에 이 행을 선점한다. 이미 남이 가져갔으면 False.

    ★ 왜 필요한가 (프로세스 경계): 경제 브리핑은 subprocess 라 데몬과 *동시에* 아웃박스를
      흘려보낼 수 있다. 메모리 잠금은 한 프로세스만 지킨다 — 그래서 DB 의 attempts 값을
      조건으로 거는 낙관적 선점을 쓴다. 같은 메시지가 두 번 가는 것을 이것으로 막는다.
    """
    try:
        with get_db() as conn:
            cur = conn.execute(
                "UPDATE notify_outbox SET attempts=attempts+1 WHERE id=? AND attempts=?",
                (row_id, attempts),
            )
            return (cur.rowcount or 0) == 1
    except Exception:
        return False


def outbox_done(row_id: int) -> None:
    """전송 성공 — 보관 해제."""
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM notify_outbox WHERE id=?", (row_id,))
    except Exception:
        pass


def outbox_fail(row_id: int, err: str) -> None:
    """전송 재실패 — 시도 횟수·사유만 갱신하고 계속 보관."""
    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE notify_outbox SET attempts=attempts+1, last_error=? WHERE id=?",
                (str(err)[:200], row_id),
            )
    except Exception:
        pass


def outbox_purge(ttl_hours: float) -> int:
    """유효기간 지난 메시지 폐기. Returns: 버린 건수.

    ★ 왜 버리나: 3시간 전 "발행 건너뜀" 이 지금 도착하면 *지금 일* 로 오해된다.
      너무 늦은 알림은 도움이 아니라 혼선이다 — 전달 실패보다 오해가 더 나쁘다.
    """
    try:
        with get_db() as conn:
            cur = conn.execute(
                "DELETE FROM notify_outbox "
                "WHERE created_at < datetime('now','localtime', ?)",
                (f"-{float(ttl_hours)} hours",),
            )
            return cur.rowcount or 0
    except Exception:
        return 0


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


# ── 격리 버킷(ignored) 관측 — 공개 헬퍼 (사용자 박제 2026-07-25) ──────────────
#
# 현업은 "격리한 것" 을 버리지 않고 *별도 버킷으로 계속 집계* 한다. 격리는 판단이고,
# 판단은 틀릴 수 있기 때문이다. 우리 DB 의 ignored 440건 중 220건이 resolution NULL —
# *왜 무시했는지 아무도 모르는 채* 쌓여 있었다. 여기서 그 버킷을 사유·타입·추세로 집계한다.
#
# ★ ②동적 설계 — 이 함수에는 상태 목록도, '코드버그 타입' 목록도 박혀 있지 않다.
#   · 상태 버킷      : error_log 에 실제 존재하는 status 를 DISTINCT 로 파생
#   · 코드버그 타입   : "실제로 코드를 고쳐 종결된 이력(status='fixed' AND fixed_file 존재)"
#                      이 있는 error_type 집합을 DB 에서 파생 → ignored 와 교집합.
#     손으로 나열하지 않으므로 새 오류 타입이 생겨도 자동으로 검사 대상이 된다.

def try_claim_error(error_id: int, claim_status: str = "analyzing",
                     from_statuses: tuple = ("new", "ignored")) -> bool:
    """오류 처리 착수를 DB 레벨에서 원자적으로 선점.

    in-memory 집합(guardian_agent._processing)은 같은 프로세스 내 스레드만 방어 —
    bus 재전달(dispatch_pending 폴백)·job_retry_pending 스윕이 겹치면 서로 다른
    스레드가 동시에 오케스트레이터 진입점을 통과할 수 있다. UPDATE...WHERE 조건부
    갱신은 SQLite 자체가 직렬화하므로 두 번째 호출은 반드시 rowcount=0 을 받는다.
    """
    placeholders = ",".join("?" for _ in from_statuses)
    _now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            f"UPDATE error_log SET status=?, claimed_at=? "
            f"WHERE id=? AND status IN ({placeholders})",
            (claim_status, _now, error_id, *from_statuses),
        )
        return cur.rowcount > 0


def heartbeat_error(error_id: int) -> bool:
    """처리 중인 오류에 '아직 살아있음' 신호 갱신 — ERRORS [473] (2026-07-22).

    ★ 왜 필요한가: 수확기(job_retry_pending)는 오래 묶인 'analyzing' 을 죽은 세션으로 보고
      'new' 로 되돌린다. 그런데 판정 기준이 `timestamp`(오류가 *기록된* 시각)였다 —
      작업이 얼마나 진행됐는지와 무관한 값이라, **실제로 살아 있는 세션도 리셋**됐다.
      (2026-07-18 실측: #3435 의 82분짜리 Tier-2 세션이 75분 시점에 리셋되어
       같은 오류에 두 번째 세션이 중복 기동 → LLM 단일 차선을 서로 경합)
    ★ 이제 작업자가 살아있는 동안 주기적으로 이 함수를 불러 `claimed_at` 을 갱신한다.
      수확기는 '마지막 신호로부터 얼마나 지났나' 를 보므로 작업 길이와 무관하게 정확하다.
      죽으면 신호가 끊기니 정상적으로 회수된다.

    status='analyzing' 인 행만 갱신 — 이미 끝난 오류를 되살리지 않는다.
    """
    _now = datetime.now().isoformat(timespec="seconds")
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE error_log SET claimed_at=? WHERE id=? AND status='analyzing'",
            (_now, error_id),
        )
        return cur.rowcount > 0


def mark_error_provisional(error_id: int, provisional: bool = True) -> bool:
    """오류를 '잠정 실패' 로 표시/해제 — Tier-2(LLM) 판정 보류 여부 (ERRORS [476]).

    ★ 왜 필요한가: harness 는 실패하면 재시도한다. attempt=1 실패 시점엔 그것이
      *일시적* 인지(재시도로 해결) *결정론적* 인지(진짜 코드 버그) **알 수 없다**.
      그런데 종전엔 그 즉시 GUARDIAN 이 Tier-2(LLM 수십 분)를 시작했다.
      실측 2026-07-22: Tier-2 를 태운 harness 오류 74건 중 **57건(77%)이 attempt=1** —
      결과를 알기도 전에 태운 것. 그중 일부는 나중에 액션이 성공해 소급 무효화됐다.
    ★ 기록 자체는 즉시 남긴다(대시보드 관측성 유지). *판정만* 미룬다.
    """
    with get_db() as conn:
        cur = conn.execute("UPDATE error_log SET provisional=? WHERE id=?",
                           (1 if provisional else 0, error_id))
        return cur.rowcount > 0


def finalize_provisional_errors(error_ids: list) -> int:
    """액션이 최종 실패로 끝났을 때 — 잠정 표시를 풀어 Tier-2 판정 대상으로 승격."""
    ids = [int(i) for i in (error_ids or [])]
    if not ids:
        return 0
    ph = ",".join("?" for _ in ids)
    with get_db() as conn:
        cur = conn.execute(f"UPDATE error_log SET provisional=0 WHERE id IN ({ph})", ids)
        return cur.rowcount


def bump_llm_attempts(error_id: int) -> int:
    """Tier 2(LLM) 시도 횟수 +1 하고 갱신된 값 반환 (job_retry_pending 무한 재시도 캡 판정용)."""
    with get_db() as conn:
        conn.execute(
            "UPDATE error_log SET llm_attempts = COALESCE(llm_attempts, 0) + 1 WHERE id=?",
            (error_id,),
        )
        row = conn.execute("SELECT llm_attempts FROM error_log WHERE id=?", (error_id,)).fetchone()
        return int(row["llm_attempts"]) if row and row["llm_attempts"] is not None else 1


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
