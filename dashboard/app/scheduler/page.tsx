"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { fmtNum, fmtTime, C } from "@/lib/utils";

/* ─── 타입 ─────────────────────────────────────────── */
interface JobDef {
  id: string; name: string; trigger: string | { type: string; [k: string]: unknown };
  owner?: string;
}
interface JobRun {
  job_id: string; job_name?: string; started_at: string;
  success: number | boolean; error?: string; owner_agent?: string;
}
interface LastRun {
  job_id: string; started_at: string; success: number | boolean;
}
interface FailureRow { job_id: string; count: number; last_at?: string }

/* ─── KPI 카드 ─────────────────────────────────────── */
function KpiCard({
  label, value, color = C.primary, sub,
}: { label: string; value: string | number; color?: string; sub?: string }) {
  return (
    <div style={{
      background: "var(--c-card)",
      border: "1px solid var(--c-bdr)",
      borderTop: `3px solid ${color}`,
      borderRadius: 12,
      padding: "24px 20px",
      flex: 1,
      minWidth: 0,
    }}>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

/* ─── 뱃지 ─────────────────────────────────────────── */
function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "2px 10px", borderRadius: 20,
      fontSize: 12, fontWeight: 600,
      background: color + "22", color,
    }}>{label}</span>
  );
}

/* ─── 트리거 포맷 ──────────────────────────────────── */
function formatTrigger(trigger: JobDef["trigger"]): string {
  if (!trigger) return "—";
  if (typeof trigger === "string") return trigger;
  const t = trigger as Record<string, unknown>;
  if (t.type === "cron") {
    const parts = [t.hour != null ? `${t.hour}시` : "", t.minute != null ? `${t.minute}분` : ""].filter(Boolean);
    return `cron ${parts.join(" ")}`.trim();
  }
  if (t.type === "interval") {
    const mins = t.minutes ?? t.seconds != null ? `${Number(t.seconds) / 60}분` : "";
    return `매 ${t.minutes ?? mins}분`;
  }
  return t.type as string ?? "—";
}

/* ─── 성공 여부 아이콘 ─────────────────────────────── */
function SuccessIcon({ ok }: { ok: boolean }) {
  return ok
    ? <span style={{ color: C.success, fontWeight: 700 }}>✓</span>
    : <span style={{ color: C.danger,  fontWeight: 700 }}>✗</span>;
}

/* ─── 메인 페이지 ───────────────────────────────────── */
export default function SchedulerPage() {
  const { data: jobs }      = useSWR<JobDef[]>     ("/api/jobs",          fetcher, { refreshInterval: 60000 });
  const { data: runs }      = useSWR<JobRun[]>     ("/api/job-runs",      fetcher, { refreshInterval: 30000 });
  const { data: lastRuns }  = useSWR<LastRun[]>    ("/api/job-last-runs", fetcher, { refreshInterval: 30000 });
  const { data: failures }  = useSWR<FailureRow[]> ("/api/job-failures",  fetcher, { refreshInterval: 60000 });

  // 잡ID → lastRun 매핑
  const lastRunMap: Record<string, LastRun> = {};
  (lastRuns ?? []).forEach(lr => { lastRunMap[lr.job_id] = lr; });

  // KPI 계산
  const totalJobs  = (jobs ?? []).length;
  const todayRuns  = (runs ?? []).length;
  const failCount  = (failures ?? []).reduce((s, f) => s + (f.count ?? 1), 0);
  const successCnt = (runs ?? []).filter(r => r.success === 1 || r.success === true).length;
  const successPct = todayRuns > 0 ? Math.round((successCnt / todayRuns) * 100) : 0;
  const successColor = successPct >= 90 ? C.success : successPct >= 70 ? C.warn : C.danger;

  const recentRuns = (runs ?? []).slice(0, 30);

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* 제목 */}
      <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--c-text)", marginBottom: 28, marginTop: 0 }}>
        스케줄러
      </h1>

      {/* KPI 4개 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
        <KpiCard label="등록된 잡"  value={fmtNum(totalJobs)}    color={C.primary} sub="DEFAULT_JOBS" />
        <KpiCard label="오늘 실행"   value={fmtNum(todayRuns)}   color={C.success} sub="총 실행 횟수" />
        <KpiCard label="실패"        value={fmtNum(failCount)}   color={C.danger}  sub="실패 이벤트" />
        <KpiCard label="성공률"      value={`${successPct}%`}    color={successColor} sub={`${successCnt}/${todayRuns}`} />
      </div>

      {/* 잡 목록 테이블 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px", marginBottom: 28 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 16 }}>
          잡 목록 <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>등록된 {totalJobs}개</span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["잡 ID", "이름", "트리거", "소유 에이전트", "마지막 실행", "성공"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", padding: "8px 12px",
                    fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                    borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(jobs ?? []).map((job, i) => {
                const lr = lastRunMap[job.id];
                const ok = lr ? (lr.success === 1 || lr.success === true) : null;
                return (
                  <tr key={job.id} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "var(--c-text5)", fontFamily: "monospace" }}>{job.id}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text)", fontWeight: 500 }}>{job.name}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-primary)" }}>{formatTrigger(job.trigger)}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)" }}>{job.owner ?? "—"}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>
                      {lr ? fmtTime(lr.started_at) : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 16 }}>
                      {ok === null ? <span style={{ color: "var(--c-text5)" }}>—</span> : <SuccessIcon ok={ok} />}
                    </td>
                  </tr>
                );
              })}
              {(jobs ?? []).length === 0 && (
                <tr>
                  <td colSpan={6} style={{ padding: "32px", textAlign: "center", color: "var(--c-text5)", fontSize: 14 }}>
                    등록된 잡 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 최근 실행 이력 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 16 }}>
          최근 실행 이력 <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>최신 30건</span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["잡 ID", "소유자", "시작 시각", "결과", "오류"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", padding: "8px 12px",
                    fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                    borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recentRuns.map((r, i) => {
                const ok = r.success === 1 || r.success === true;
                return (
                  <tr key={`${r.job_id}-${r.started_at}-${i}`} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                    <td style={{ padding: "8px 12px", fontSize: 12, color: "var(--c-text5)", fontFamily: "monospace" }}>{r.job_id}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)" }}>{r.owner_agent ?? "—"}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>{fmtTime(r.started_at)}</td>
                    <td style={{ padding: "8px 12px" }}>
                      <Badge label={ok ? "성공" : "실패"} color={ok ? C.success : C.danger} />
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: C.danger, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.error ?? ""}
                    </td>
                  </tr>
                );
              })}
              {recentRuns.length === 0 && (
                <tr>
                  <td colSpan={5} style={{ padding: "32px", textAlign: "center", color: "var(--c-text5)", fontSize: 14 }}>
                    실행 이력 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
