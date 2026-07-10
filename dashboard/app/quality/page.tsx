"use client";
import useSWR from "swr";
import { apiFetch, fetcher, QualityHistory } from "@/lib/api";
import { fmtNum, fmtTime, statusColor } from "@/lib/utils";
import { useState } from "react";

type QualityStats = {
  total: number;
  by_status: Record<string, number>;
};

const BASE = "http://localhost:9198";

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

/* ── 상태 뱃지 ────────────────────────────────────────────────────── */
function StatusBadge({ status }: { status: string }) {
  const color = statusColor(status);
  const labelMap: Record<string, string> = {
    approved: "승인완료",
    rejected: "반려",
    new: "신규",
    pending: "대기중",
    analyzing: "분석중",
  };
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
      {labelMap[status] ?? status}
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

  const by = stats?.by_status ?? {};
  const total     = stats?.total ?? 0;
  const waiting   = (by["new"] ?? 0) + (by["pending"] ?? 0);
  const approved  = by["approved"] ?? 0;
  const rejected  = by["rejected"] ?? 0;

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
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>발행 글 품질 검토 및 승인 관리</p>
      </div>

      {/* KPI 4개 */}
      {loadingStats ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard label="전체" value={fmtNum(total)} color="var(--c-primary)" />
          <KpiCard label="승인 대기" value={fmtNum(waiting)} color="var(--c-warn)" />
          <KpiCard label="승인 완료" value={fmtNum(approved)} color="var(--c-success)" />
          <KpiCard label="반려" value={fmtNum(rejected)} color="var(--c-danger)" />
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
                  const rowTd = isLast
                    ? { ...td, borderBottom: "none" }
                    : td;
                  const busy = loadingId === row.id;

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
                      <td style={rowTd}><StatusBadge status={row.status} /></td>
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
