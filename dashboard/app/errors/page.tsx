"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { severityColor, statusColor, fmtNum, fmtTime, C } from "@/lib/utils";

/* ─── 타입 ─────────────────────────────────────────── */
interface GuardianStats {
  total: number; new: number; fixed: number;
  critical: number; high: number; medium: number; low: number;
}
interface AlltimeData  { total: number }
interface TrendDay     { day: string; total: number; crit: number; high: number; fixed: number }
interface SourceRow    { source: string; total: number; crit: number; fixed: number; new: number }
interface ErrorRow     {
  id: number; timestamp: string; severity: string; status: string;
  error_type: string; module: string; message: string; source?: string;
}

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

/* ─── 7일 추이 바 차트 ──────────────────────────────── */
function TrendChart({ trend }: { trend: TrendDay[] }) {
  const max = Math.max(...trend.map(d => d.total), 1);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, height: 80, marginTop: 12 }}>
      {trend.map(d => (
        <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
          <div style={{ fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>{d.total}</div>
          <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", height: 40 }}>
            <div style={{
              width: "100%",
              height: `${Math.max(4, (d.total / max) * 40)}px`,
              background: d.crit > 0 ? C.danger : C.primary,
              borderRadius: "4px 4px 0 0",
              opacity: 0.85,
            }} />
          </div>
          <div style={{ fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>
            {d.day.slice(5)}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── 에이전트별 바 차트 ────────────────────────────── */
function SourceChart({ sources }: { sources: SourceRow[] }) {
  const max = Math.max(...sources.map(s => s.total), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
      {sources.map(s => (
        <div key={s.source} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 120, fontSize: 14, color: "var(--c-text2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 0 }}>
            {s.source}
          </div>
          <div style={{ flex: 1, background: "var(--c-bdr)", borderRadius: 4, height: 18, overflow: "hidden" }}>
            <div style={{
              width: `${(s.total / max) * 100}%`,
              height: "100%",
              background: s.crit > 0 ? C.danger : C.primary,
              borderRadius: 4,
              opacity: 0.8,
            }} />
          </div>
          <div style={{ width: 40, fontSize: 14, color: "var(--c-text)", textAlign: "right", flexShrink: 0 }}>
            {s.total}
          </div>
          <div style={{ width: 32, fontSize: 12, color: C.success, textAlign: "right", flexShrink: 0 }}>
            {s.fixed > 0 ? `+${s.fixed}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── 메인 페이지 ───────────────────────────────────── */
export default function ErrorsPage() {
  const { data: stats }   = useSWR<GuardianStats>("/api/guardian/stats",   fetcher, { refreshInterval: 30000 });
  const { data: alltime } = useSWR<AlltimeData>  ("/api/guardian/alltime", fetcher, { refreshInterval: 60000 });
  const { data: trend }   = useSWR<TrendDay[]>   ("/api/guardian/trend",   fetcher, { refreshInterval: 60000 });
  const { data: sources } = useSWR<SourceRow[]>  ("/api/guardian/sources", fetcher, { refreshInterval: 60000 });
  const { data: errors }  = useSWR<ErrorRow[]>   ("/api/errors",           fetcher, { refreshInterval: 30000 });

  const critHigh = (stats?.critical ?? 0) + (stats?.high ?? 0);
  const latest   = (errors ?? []).slice(0, 30);

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* 제목 */}
      <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--c-text)", marginBottom: 28, marginTop: 0 }}>
        오류 관리
      </h1>

      {/* KPI 4개 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
        <KpiCard label="미해결"       value={fmtNum(stats?.new)}     color={C.danger}  sub="해결 필요" />
        <KpiCard label="CRITICAL+HIGH" value={fmtNum(critHigh)}       color={critHigh > 0 ? C.danger : C.warn} sub={`CRITICAL ${stats?.critical ?? 0} / HIGH ${stats?.high ?? 0}`} />
        <KpiCard label="7일 자동수정"  value={fmtNum(stats?.fixed)}   color={C.success} sub="자동 수정 완료" />
        <KpiCard label="전체 누적"     value={fmtNum(alltime?.total)} color={C.primary} sub="총 오류 기록" />
      </div>

      {/* 7일 추이 + 에이전트별 나란히 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 28 }}>
        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 4 }}>7일 추이</div>
          <div style={{ fontSize: 14, color: "var(--c-text5)" }}>빨강=CRITICAL 포함, 파랑=일반</div>
          {trend && trend.length > 0
            ? <TrendChart trend={trend} />
            : <div style={{ color: "var(--c-text5)", fontSize: 14, marginTop: 20 }}>데이터 없음</div>
          }
        </div>

        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 4 }}>에이전트별 오류</div>
          <div style={{ fontSize: 14, color: "var(--c-text5)" }}>초록 숫자 = 자동 수정</div>
          {sources && sources.length > 0
            ? <SourceChart sources={sources} />
            : <div style={{ color: "var(--c-text5)", fontSize: 14, marginTop: 20 }}>데이터 없음</div>
          }
        </div>
      </div>

      {/* 오류 목록 테이블 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 16 }}>
          오류 목록 <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>최신 30건</span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["ID", "시각", "에이전트", "모듈", "타입", "심각도", "상태", "메시지"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", padding: "8px 12px",
                    fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                    borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {latest.map((e, i) => (
                <tr key={e.id} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                  <td style={{ padding: "8px 12px", fontSize: 12, color: "var(--c-text5)" }}>{e.id}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>{fmtTime(e.timestamp)}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)" }}>{e.source ?? "—"}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.module ?? "—"}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{e.error_type}</td>
                  <td style={{ padding: "8px 12px" }}>
                    <Badge label={e.severity} color={severityColor(e.severity)} />
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <Badge label={e.status} color={statusColor(e.status)} />
                  </td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {e.message?.slice(0, 120)}
                  </td>
                </tr>
              ))}
              {latest.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ padding: "32px", textAlign: "center", color: "var(--c-text5)", fontSize: 14 }}>
                    오류 기록 없음
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
