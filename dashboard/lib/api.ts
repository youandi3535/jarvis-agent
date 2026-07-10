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
export type TrendData  = { today: number; top: TrendRow[]; sectors: Record<string, number> };
export type TrendRow   = { keyword: string; sector: string; score: number; opportunity_score: number; source: string };
export type GuardianStats = { total: number; new: number; fixed: number; critical: number; high: number; medium: number; low: number; recent: ErrorRow[] };
export type ErrorRow   = { id: number; timestamp: string; severity: string; status: string; error_type: string; module: string; message: string };
export type VisionSummary = { total_agents?: number; healthy?: number; degraded?: number; offline?: number };
export type OverviewData  = { daemon: DaemonInfo; posts: PostStats; trends: TrendData; guardian: GuardianStats; vision: VisionSummary; ts: string };
export type PerformanceData = { total_views: number; top_posts: PostRow[]; platform_views: Record<string, number>; naver_ranked: NaverRow[]; history: HistRow[] };
export type PostRow    = { platform: string; title: string; current_views: number; naver_rank: number | null; created_at: string };
export type NaverRow   = { title: string; naver_rank: number; current_views: number; created_at: string };
export type HistRow    = { d: string; v: number };
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

// 파이프라인 그래프 — /api/graph (사용자 박제 2026-07-11)
export type PipelineEdge = {
  id: string; from: string; to: string;
  label?: string | null; col: string; dur: number; dots: number; wt?: number;
  route?: string; lane_y?: number; dx?: number;
};
export type LegendItem = { col: string; label: string };
export type GraphData  = { edges: PipelineEdge[]; legend: LegendItem[] };
