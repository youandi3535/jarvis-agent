"use client";
import useSWR from "swr";
import { apiFetch, QualityHistory } from "@/lib/api";
import { fmtNum, fmtTime, statusColor } from "@/lib/utils";
import { useState } from "react";

type QualityStats = {
  by_status: Record<string, number>;
  status_labels: Record<string, string>;
  status_hints: Record<string, string>;
};

const BASE = "http://localhost:9198";

// 색상 힌트(백엔드) → CSS 변수(프론트) 매핑 — 비즈니스 상태와 무관한 5가지 시각 범주
const HINT_COLOR: Record<string, string> = {
  success: "var(--c-success)",
  primary: "var(--c-primary)",
  warn:    "var(--c-warn)",
  danger:  "var(--c-danger)",
  muted:   "var(--c-text2)",
};

/* ── 공통 스타일 ─────────────────────────────────────────────────── */
const card = (topColor: string): React.CSSProperties => ({
  background: "var(--c-card)",
  border: "1px solid var(--c-bdr)",
  borderRadius: 12,
  borderTop: `3px solid ${topColor}`,
  padding: "24px 20px",
  textAlign: "center",
  flex: 1,
  minWidth: 140,
});

const section: React.CSSProperties = {
  background: "var(--c-card)",
  border: "1px solid var(--c-bdr)",
  borderRadius: 12,
  padding: 20,
  marginTop: 24,
};

const th: React.CSSProperties = {
  textAlign: "left",
  padding: "10px 12px",
  fontSize: 14,
  color: "var(--c-text2)",
  fontWeight: 600,
  borderBottom: "1px solid var(--c-bdr)",
  whiteSpace: "nowrap",
};

const td: React.CSSProperties = {
  padding: "10px 12px",
  fontSize: 14,
  color: "var(--c-text)",
  borderBottom: "1px solid var(--c-bdr)",
  verticalAlign: "middle",
};

/* ── 상태 뱃지 — API 라벨 사용 ──────────────────────────────────── */
function StatusBadge({ status, label }: { status: string; label: string }) {
  const color = statusColor(status);
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      padding: "2px 10px",
      borderRadius: 20,
      fontSize: 12,
      fontWeight: 600,
      background: color + "22",
      color,
      border: `1px solid ${color}44`,
    }}>
      {label}
    </span>
  );
}

/* ── KPI 카드 ────────────────────────────────────────────────────── */
function KpiCard({ label, value, color }: { label: string; value: number | string; color: string }) {
  return (
    <div style={card(color)}>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 8 }}>{label}</div>
    </div>
  );
}

/* ── 메인 컴포넌트 ────────────────────────────────────────────────── */
export default function QualityPage() {
  const { data: stats, isLoading: loadingStats } =
    useSWR<QualityStats>("/api/quality/stats", (url) => apiFetch<QualityStats>(url), { refreshInterval: 30000 });
  const { data: rawHistory, isLoading: loadingHistory, mutate: mutateHistory } =
    useSWR<QualityHistory[]>("/api/quality/history", (url) => apiFetch<QualityHistory[]>(url), { refreshInterval: 60000 });

  const [loadingId, setLoadingId] = useState<number | null>(null);

  const by     = stats?.by_status     ?? {};
  const labels = stats?.status_labels ?? {};
  const hints  = stats?.status_hints  ?? {};
  const total  = Object.values(by).reduce((a, b) => a + b, 0);
  const statusEntries = Object.entries(by).sort((a, b) => b[1] - a[1]);

  const history = (rawHistory ?? []).slice(0, 20);

  async function doAction(id: number, action: "approve" | "reject") {
    setLoadingId(id);
    try {
      await fetch(`${BASE}/api/quality/${id}/${action}`, { method: "POST" });
      await mutateHistory();
    } finally {
      setLoadingId(null);
    }
  }

  return (
    <div>
      {/* 제목 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>품질 관리</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>발행 글 품질 분석 — 인사이트는 다음 글 작성에 자동 반영</p>
      </div>

      {/* KPI — 전체 + by_status 동적 렌더링 */}
      {loadingStats ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard label="전체" value={fmtNum(total)} color="var(--c-primary)" />
          {statusEntries.map(([key, count]) => (
            <KpiCard
              key={key}
              label={labels[key] ?? key}
              value={fmtNum(count)}
              color={HINT_COLOR[hints[key]] ?? "var(--c-text2)"}
            />
          ))}
        </div>
      )}

      {/* 발행 이력 테이블 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          발행 이력 {history.length > 0 && <span style={{ fontSize: 14, color: "var(--c-text2)", fontWeight: 400 }}>({history.length}건)</span>}
        </h2>

        {loadingHistory ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "20px 0" }}>로딩 중…</div>
        ) : history.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "20px 0" }}>발행 이력이 없습니다.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr>
                  <th style={th}>플랫폼</th>
                  <th style={{ ...th, minWidth: 260 }}>제목</th>
                  <th style={th}>상태</th>
                  <th style={{ ...th, textAlign: "right" }}>조회수</th>
                  <th style={{ ...th, textAlign: "right" }}>분석 시각</th>
                  <th style={{ ...th, textAlign: "center" }}>작업</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => {
                  const titleDisplay = row.title.length > 200
                    ? row.title.slice(0, 200) + "…"
                    : row.title;
                  const isLast = history.indexOf(row) === history.length - 1;
                  const rowTd = isLast ? { ...td, borderBottom: "none" } : td;
                  const busy = loadingId === row.id;
                  const rowLabel = labels[row.status] ?? row.status;

                  return (
                    <tr key={row.id} style={{ transition: "background 0.15s" }}
                      onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td style={rowTd}>
                        <span style={{
                          fontSize: 12,
                          fontWeight: 700,
                          padding: "2px 8px",
                          borderRadius: 6,
                          background: row.platform === "naver" ? "#03c75a22" : "#f9640022",
                          color: row.platform === "naver" ? "#03c75a" : "#f96400",
                        }}>
                          {row.platform === "naver" ? "N" : "T"}
                        </span>
                      </td>
                      <td style={rowTd}>
                        {row.url ? (
                          <a href={row.url} target="_blank" rel="noopener noreferrer"
                            style={{ color: "var(--c-text)", textDecoration: "none" }}
                            onMouseEnter={e => (e.currentTarget.style.color = "var(--c-primary)")}
                            onMouseLeave={e => (e.currentTarget.style.color = "var(--c-text)")}>
                            {titleDisplay}
                          </a>
                        ) : titleDisplay}
                      </td>
                      <td style={rowTd}><StatusBadge status={row.status} label={rowLabel} /></td>
                      <td style={{ ...rowTd, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                        {fmtNum(row.current_views)}
                      </td>
                      <td style={{ ...rowTd, textAlign: "right", color: "var(--c-text2)" }}>
                        {fmtTime(row.analyzed_at)}
                      </td>
                      <td style={{ ...rowTd, textAlign: "center" }}>
                        <div style={{ display: "flex", gap: 6, justifyContent: "center" }}>
                          <button
                            disabled={busy || row.status === "approved"}
                            onClick={() => doAction(row.id, "approve")}
                            style={{
                              fontSize: 12,
                              fontWeight: 600,
                              padding: "4px 12px",
                              borderRadius: 6,
                              border: "1px solid var(--c-success)",
                              background: "transparent",
                              color: "var(--c-success)",
                              cursor: busy || row.status === "approved" ? "not-allowed" : "pointer",
                              opacity: busy || row.status === "approved" ? 0.4 : 1,
                              transition: "background 0.15s",
                            }}>
                            승인
                          </button>
                          <button
                            disabled={busy || row.status === "rejected"}
                            onClick={() => doAction(row.id, "reject")}
                            style={{
                              fontSize: 12,
                              fontWeight: 600,
                              padding: "4px 12px",
                              borderRadius: 6,
                              border: "1px solid var(--c-danger)",
                              background: "transparent",
                              color: "var(--c-danger)",
                              cursor: busy || row.status === "rejected" ? "not-allowed" : "pointer",
                              opacity: busy || row.status === "rejected" ? 0.4 : 1,
                              transition: "background 0.15s",
                            }}>
                            반려
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
