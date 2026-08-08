const BASE = "http://localhost:9198";

export async function apiFetch<T = unknown>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

export const fetcher = <T = unknown>(url: string): Promise<T> => apiFetch<T>(url);

// 각 엔드포인트 타입
export type DaemonInfo = { alive: boolean; pid: number | null; uptime: string };
export type PostStats  = { today: number; week: number; month: number; by_platform: Record<string, number> };
export type TrendData  = {
  today: number;
  sectors: Record<string, number>;
  google_top10: Top10Item[];
  naver_top10: Top10Item[];
  combined_keywords: CombinedItem[];
  recommendations: RecommendItem[];
  trend_delta: TrendDelta;
  topic_candidates: TopicCandidate[];
};
export type Top10Item      = { rank: number; keyword: string; score?: number };
export type CombinedItem = { keyword: string; score: number; sources: string[] };
export type RecommendItem  = { keyword: string; sector: string; score: number; opportunity_score: number; velocity: string; competition: number; reason: string };
export type TrendDelta     = { prev_date?: string; new_entry?: string[]; dropped?: string[]; risen?: { keyword: string; delta: number }[]; fallen?: { keyword: string; delta: number }[] };
export type TopicCandidate = { keyword: string; sector: string; opportunity_score: number; reason: string; profile?: { summary?: string } };
export type GuardianStats = { total: number; new: number; fixed: number; critical: number; high: number; medium: number; low: number; recent: ErrorRow[] };
export type ErrorRow   = { id: number; timestamp: string; severity: string; status: string; error_type: string; module: string; message: string };
export type VisionSummary = { total_agents?: number; healthy?: number; degraded?: number; offline?: number };
export type OverviewData  = { daemon: DaemonInfo; posts: PostStats; trends: TrendData; guardian: GuardianStats; vision: VisionSummary; ts: string };
export type PerformanceData = {
  active_platforms: string[];
  platform_labels:  Record<string, string>;
  period_order:     string[];
  period_labels:    Record<string, string>;
  period_views:     Record<string, Record<string, number>>;
  daily_trend:      Array<Record<string, number | string>>;
  top_posts:        PostRow[];
  data_range:       { from: string | null; to: string | null; days: number };
};
export type PostRow    = { platform: string; title: string; current_views: number; naver_rank: number | null; created_at: string };
// ★ `llm_saved_1d` — 1일 창에서 LLM 없이 실제로 고친 횟수. 옛 칸(`llm_saved`)은
//   정의가 달라(누적 패턴 수) 더 이상 내려오지 않는다. null = 그 회차엔 측정 안 함.
export type LearningPoint  = { at: string; patterns: number; hits: number; llm_saved_1d: number | null };
export type ResolvePoint   = { at: string; total: number; resolved: number; rate: number };
export type LearningData = {
  /** 밴딧 생존 지표 — 서버(`bandit.stats()`) 단독 파생. 정지를 정지라고 말하기 위한 것. */
  bandit?: { arms?: number; observed_arms?: number; last_update_h?: number; stalled?: boolean; error?: string };
 weights: WeightRow[]; backtest: BacktestRow[]; insights: InsightRow[]; learn_log: { cnt: number; mae: number | null };
  insights_total?: number; timeline?: LearningPoint[]; resolve_rate?: ResolvePoint[];
  patterns_now?: { count: number; hits: number };
  quality_now?: { insights: number; usage: number; rewards: number; avg_reward: number; avg_weight: number; rediscovered: number; rewarded: number };
  quality_timeline?: Array<{ at: string; insights: number; added: number }>;
  feature_variance?: Record<string, number> };
export type WeightRow  = { weight_type: string; weights_json: string; trained_at: string;
  train_r2: number | null; backtest_r2: number | null; n_samples?: number };
export type BacktestRow = { tested_at: string; backtest_type: string; score: number; details: string };
export type InsightRow = { insight_key: string; insight_type: string; description: string; directive: string; weight: number; scope: string; occurrences: number; last_seen: string };
export type JobRun     = { job_id: string; job_name: string; started_at: string; success: number; error: string; owner_agent: string };
export type VisionAgent = { agent_id: string; status: string; last_seen: string; metrics?: Record<string, number> };

// ── 에이전트 상태 흐름 (30일) ─────────────────────────────
// ★ 구간 조립·위치(%) 계산은 전부 서버(JARVIS05_VISION/collector.get_status_timeline).
//   여기 타입은 *받은 것을 그대로 그리기* 위한 것 — 프론트에서 재계산하지 말 것.
export type TimelineSegment = {
  status: string; start: string; end: string; minutes: number;
  message: string; left_pct: number; width_pct: number;
};
export type TimelineAgent = {
  agent_id: string; agent_name: string; current: string;
  uptime_pct: number | null; incidents: number;
  observed_start: string | null; observed_pct: number;
  segments: TimelineSegment[];
};
export type VisionTimeline = {
  days: number; generated_at: string;
  window_start: string; window_end: string; window_minutes: number;
  agents: TimelineAgent[];
};
export type QualityHistory = { id: number; platform: string; theme: string; title: string; url: string; status: string; suggestions: string; analyzed_at: string; created_at: string; current_views: number; naver_rank: number | null };
export type RepairRun  = { id: number; started_at: string; syntax_fixed: number; rules_fixed: number; patterns_count: number; hits_total: number; llm_saved: number };
export type Pattern    = { fingerprint: string; fixer_name: string; hit_count: number; last_seen: string };
export type DbTable    = { name: string; rows: number; last_write: string; today_rows: number };
export type DbStats    = { size_mb: number; tables: DbTable[]; backup_files: BackupFile[]; total_rows: number; wal_exists: boolean };
export type BackupFile = { name: string; size_mb: number; mtime: string };

// 파이프라인 그래프 — /api/graph
// 새 에이전트·연결은 shared/pipeline_graph.py 만 수정하면 자동 반영됨
export type AgentDef = {
  id: string; num: string; label: string; sub: string; color: string;
  x: number; y: number; big?: boolean;
};
export type PipelineEdge = {
  id: string; from: string; to: string;
  label?: string | null; col: string; dur: number; dots: number; wt?: number;
  route?: string; lane_y?: number; dx?: number; dy?: number;
};
export type LegendItem = { col: string; label: string };
export type LayoutConst = { W: number; H: number; CARD_W: number; CARD_H: number; BIG_W: number; BIG_H: number };
export type GraphData  = { agents: AgentDef[]; edges: PipelineEdge[]; legend: LegendItem[]; layout: LayoutConst };

// ── 토큰 사용량 현황판 (ERRORS [456]) ──────────────────────────────
export type TokenDaily   = { date: string; output: number; input: number; cache_create: number; cache_read: number; calls: number };
export type TokenHour    = { hour: string; output: number };
export type TokenProject = { project: string; output: number; calls: number };
/** 소비 주체별 오늘 사용량 — 누가 태우는지 한눈에 (daemon / subagent / session) */
export type TokenConsumer = { consumer: string; output: number; calls: number; total: number };
export type TokenAlias   = { alias: string; model: string; calls: number; output: number; input: number; cache_create: number; cache_read: number; cost: number; failed: number };
export type TokenCall    = { ts: string; alias: string; model: string; output_tokens: number;
                             input_tokens: number; cache_create: number; cache_read: number;
                             cost_usd: number | null; duration_ms: number; num_turns: number; ok: number };
export type RateLimitRow = { ts: string; status: string; status_desc: string; ok: boolean;
                             window: string; reset: string | null; overage: string | null;
                             raw?: string };
export type TokenSuggestion = { id: string; title: string; severity: string; finding: string; action: string; effect: string; tradeoff: string; knob: string };
export type TokenData = {
  generated_at?: string;
  history?: TokenDaily[];
  quota?: { available: boolean; raw?: unknown; fetched_at?: string } | null;
  suggestions?: TokenSuggestion[];
  totals?: { available: boolean; reason?: string; scanned_files?: number; deduped_lines?: number;
             daily?: TokenDaily[]; hourly_today?: TokenHour[]; by_project_today?: TokenProject[];
             by_consumer_today?: TokenConsumer[] };
  by_alias?: TokenAlias[];
  recent_calls?: TokenCall[];
  daemon_today?: { calls:number; output:number; input:number; cache_create:number;
                   cache_read:number; cost_usd:number; cache_reuse_ratio:number|null };
  rate_limits?: RateLimitRow[];
  rate_limit_summary?: { total:number; normal:number; abnormal:number;
                         last_ts?:string|null; last_abnormal_ts?:string|null; windows?:string[] };
  health?: { calls_1h?: number; empty_1h?: number; empty_rate?: number | null; state?: string };
  error?: string;
};
