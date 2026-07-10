export const C = {
  primary: "#4f90d9",
  success: "#4ade80",
  warn:    "#fbbf24",
  danger:  "#f87171",
  muted:   "#94a3b8",
};

export const N = {
  bg:    "#0f1117",
  card:  "#1a1d27",
  bdr:   "#2d3148",
  text:  "#e2e8f0",
  text2: "#94a3b8",
  text5: "#475569",
};

export function statusColor(status: string): string {
  const s = status?.toLowerCase() ?? "";
  if (s === "new" || s === "error" || s === "critical") return C.danger;
  if (s === "fixed" || s === "resolved" || s === "success" || s === "healthy") return C.success;
  if (s === "analyzing" || s === "pending" || s === "warn" || s === "degraded") return C.warn;
  if (s === "ignored" || s === "wontfix" || s === "offline") return C.muted;
  return C.primary;
}

export function severityColor(sev: string): string {
  const s = sev?.toLowerCase() ?? "";
  if (s === "critical") return C.danger;
  if (s === "high")     return "#f97316"; // orange
  if (s === "medium")   return C.warn;
  return C.muted;
}

export function fmtNum(n: number | undefined | null): string {
  if (n == null) return "—";
  return n >= 10000 ? (n / 10000).toFixed(1) + "만"
       : n >= 1000  ? n.toLocaleString()
       : String(n);
}

export function fmtTime(s: string | undefined | null): string {
  if (!s) return "—";
  return s.slice(5, 16).replace("T", " ");
}

export function ago(s: string | undefined | null): string {
  if (!s) return "—";
  const diff = Date.now() - new Date(s).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  return `${Math.floor(h / 24)}일 전`;
}

export function pct(a: number, b: number): string {
  if (!b) return "0%";
  return `${Math.round((a / b) * 100)}%`;
}
