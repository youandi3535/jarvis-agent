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
export type LearningData = { weights: WeightRow[]; backtest: BacktestRow[]; insights: InsightRow[]; learn_log: { cnt: number; mae: number | null } };
export type WeightRow  = { weight_type: string; weights_json: string; trained_at: string; backtest_score: number };
export type BacktestRow = { tested_at: string; backtest_type: string; score: number; details: string };
export type InsightRow = { insight_key: string; insight_type: string; description: string; directive: string; weight: number; scope: string; occurrences: number; last_seen: string };
export type JobRun     = { job_id: string; job_name: string; started_at: string; success: number; error: string; owner_agent: string };
export type VisionAgent = { agent_id: string; status: string; last_seen: string; metrics?: Record<string, number> };
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
export type TokenAlias   = { alias: string; model: string; calls: number; output: number; input: number; cache_create: number; cache_read: number; cost: number; failed: number };
export type TokenCall    = { ts: string; alias: string; model: string; output_tokens: number; input_tokens: number; cache_read: number; duration_ms: number; num_turns: number; ok: number };
export type RateLimitRow = { ts: string; source: string; payload: string };
export type TokenSuggestion = { id: string; title: string; severity: string; finding: string; action: string; effect: string; tradeoff: string; knob: string };
export type TokenData = {
  generated_at?: string;
  history?: TokenDaily[];
  quota?: { available: boolean; raw?: unknown; fetched_at?: string } | null;
  suggestions?: TokenSuggestion[];
  totals?: { available: boolean; reason?: string; scanned_files?: number;
             daily?: TokenDaily[]; hourly_today?: TokenHour[]; by_project_today?: TokenProject[] };
  by_alias?: TokenAlias[];
  recent_calls?: TokenCall[];
  rate_limits?: RateLimitRow[];
  health?: { calls_1h?: number; empty_1h?: number; empty_rate?: number | null; state?: string };
  error?: string;
};
