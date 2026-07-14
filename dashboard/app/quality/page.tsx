"use client";
import useSWR from "swr";
import { apiFetch, QualityHistory } from "@/lib/api";
import { fmtNum, fmtTime, statusColor } from "@/lib/utils";
import { useState } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Cell,
} from "recharts";

/* ── 타입 ────────────────────────────────────────────────────────── */
type QualityStats = {
  by_status: Record<string, number>;
  status_labels: Record<string, string>;
  status_hints: Record<string, string>;
};

type QualityTrend = {
  weekly: { week: string; posts: number; avg_issues: number }[];
  by_type: Record<string, number>;
  by_platform: Record<string, { posts: number; avg_issues: number }>;
  by_post_type: Record<string, { posts: number; avg_issues: number }>;
  top_insights: { insight_type: string; description: string; occurrences: number; weight: number }[];
};

const BASE = "http://localhost:9198";

/* ── 힌트 → CSS 색상 ─────────────────────────────────────────────── */
const HINT_COLOR: Record<string, string> = {
  success: "var(--c-success)",
  primary: "var(--c-primary)",
  warn:    "var(--c-warn)",
  danger:  "var(--c-danger)",
  muted:   "var(--c-text2)",
};

/* ── 이슈 유형 한글 라벨 (표시용 번역 — 비즈니스 로직 아님) ──────── */
const TYPE_KO: Record<string, string> = {
  structure:   "글 구조",
  intro:       "도입부",
  cta:         "CTA",
  title:       "제목",
  readability: "가독성",
  seo:         "SEO",
  keyword:     "키워드",
  content:     "내용",
};

/* ── 차트 색상 팔레트 ──────────────────────────────────────────────── */
const BAR_COLORS = [
  "#4f8ef7", "#34c77b", "#f59e0b", "#ef4444",
  "#8b5cf6", "#06b6d4", "#f97316", "#6b7280",
];

/* ── 공통 스타일 ─────────────────────────────────────────────────── */
const panel = (accent?: string): React.CSSProperties => ({
  background: "var(--c-card)",
  border: "1px solid var(--c-bdr)",
  borderRadius: 12,
  borderTop: accent ? `3px solid ${accent}` : undefined,
  padding: 20,
  marginTop: 24,
});

const th: React.CSSProperties = {
  textAlign: "left", padding: "10px 12px", fontSize: 14,
  color: "var(--c-text2)", fontWeight: 600,
  borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
};

const td: React.CSSProperties = {
  padding: "10px 12px", fontSize: 14, color: "var(--c-text)",
  borderBottom: "1px solid var(--c-bdr)", verticalAlign: "middle",
};

/* ── 서브 컴포넌트 ────────────────────────────────────────────────── */
function KpiCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div style={{
      background: "var(--c-card)", border: "1px solid var(--c-bdr)",
      borderRadius: 12, borderTop: `3px solid ${color}`,
      padding: "24px 20px", textAlign: "center", flex: 1, minWidth: 130,
    }}>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 8 }}>{label}</div>
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
      {children}
    </h2>
  );
}

function StatusBadge({ status, label }: { status: string; label: string }) {
  const color = statusColor(status);
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "2px 10px", borderRadius: 20, fontSize: 12, fontWeight: 600,
      background: color + "22", color, border: `1px solid ${color}44`,
    }}>{label}</span>
  );
}

/* ── 커스텀 툴팁 ──────────────────────────────────────────────────── */
function TrendTooltip({ active, payload, label }: { active?: boolean; payload?: {value: number; payload: {posts: number}}[]; label?: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: "var(--c-card)", border: "1px solid var(--c-bdr)",
      borderRadius: 8, padding: "10px 14px", fontSize: 13,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 4 }}>{label}</div>
      <div style={{ color: "var(--c-primary)" }}>평균 이슈 {payload[0].value}개</div>
      <div style={{ color: "var(--c-text2)" }}>{payload[0].payload.posts}개 글</div>
    </div>
  );
}

/* ── 메인 ──────────────────────────────────────────────────────────── */
export default function QualityPage() {
  const { data: stats, isLoading: loadingStats } =
    useSWR<QualityStats>("/api/quality/stats", (u) => apiFetch<QualityStats>(u), { refreshInterval: 30000 });
  const { data: trend } =
    useSWR<QualityTrend>("/api/quality/trend", (u) => apiFetch<QualityTrend>(u), { refreshInterval: 60000 });
  const { data: rawHistory, isLoading: loadingHistory, mutate: mutateHistory } =
    useSWR<QualityHistory[]>("/api/quality/history", (u) => apiFetch<QualityHistory[]>(u), { refreshInterval: 60000 });

  const [loadingId, setLoadingId] = useState<number | null>(null);

  const by     = stats?.by_status     ?? {};
  const labels = stats?.status_labels ?? {};
  const hints  = stats?.status_hints  ?? {};
  const total  = Object.values(by).reduce((a, b) => a + b, 0);
  const statusEntries = Object.entries(by).sort((a, b) => b[1] - a[1]);

  const weekly      = trend?.weekly      ?? [];
  const byType      = trend?.by_type     ?? {};
  const byPlatform  = trend?.by_platform ?? {};
  const byPostType  = trend?.by_post_type ?? {};
  const topInsights = trend?.top_insights ?? [];

  // 추이: 처음 vs 최근 4주 평균 비교
  const recentAvg = weekly.slice(-4).reduce((s, d) => s + d.avg_issues, 0) / (weekly.slice(-4).length || 1);
  const earlyAvg  = weekly.slice(0, 4).reduce((s, d) => s + d.avg_issues, 0) / (weekly.slice(0, 4).length || 1);
  const improvement = earlyAvg > 0 ? Math.round((1 - recentAvg / earlyAvg) * 100) : 0;

  const typeEntries = Object.entries(byType).map(([k, v]) => ({
    name: TYPE_KO[k] ?? k, value: v, key: k,
  }));

  const history = (rawHistory ?? []).slice(0, 20);

  async function doAction(id: number, action: "approve" | "reject") {
    setLoadingId(id);
    try {
      await fetch(`${BASE}/api/quality/${id}/${action}`, { method: "POST" });
      await mutateHistory();
    } finally { setLoadingId(null); }
  }

  return (
    <div>
      {/* 헤더 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>품질 관리</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>
          발행 글 품질 분석 — 인사이트는 다음 글 작성에 자동 반영
        </p>
      </div>

      {/* KPI */}
      {loadingStats ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard label="분석 완료" value={fmtNum(total)} color="var(--c-primary)" />
          {statusEntries.map(([key, count]) => (
            <KpiCard key={key} label={labels[key] ?? key} value={fmtNum(count)}
              color={HINT_COLOR[hints[key]] ?? "var(--c-text2)"} />
          ))}
          {improvement > 0 && (
            <KpiCard label="초기 대비 개선" value={`${improvement}%`} color="var(--c-success)" />
          )}
        </div>
      )}

      {/* ── 품질 추이 그래프 ── */}
      {weekly.length > 0 && (
        <div style={panel("var(--c-primary)")}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
            <SectionTitle>글당 평균 이슈 수 추이</SectionTitle>
            <div style={{ fontSize: 13, color: "var(--c-text2)", textAlign: "right" }}>
              <div>↓ 낮을수록 품질 좋음</div>
              {improvement > 0 && (
                <div style={{ color: "var(--c-success)", fontWeight: 700 }}>
                  초기 대비 {improvement}% 개선
                </div>
              )}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={weekly} margin={{ top: 4, right: 16, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--c-bdr)" />
              <XAxis dataKey="week" tick={{ fontSize: 12, fill: "var(--c-text2)" }} />
              <YAxis tick={{ fontSize: 12, fill: "var(--c-text2)" }} domain={[0, 7]} />
              <Tooltip content={<TrendTooltip />} />
              <Line
                type="monotone" dataKey="avg_issues"
                stroke="var(--c-primary)" strokeWidth={2.5}
                dot={{ r: 4, fill: "var(--c-primary)" }}
                activeDot={{ r: 6 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── 이슈 유형 분포 + 플랫폼 비교 ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 24 }}>

        {/* 반복 문제 유형 */}
        <div style={{ ...panel(), marginTop: 0 }}>
          <SectionTitle>반복 문제 유형</SectionTitle>
          <div style={{ fontSize: 13, color: "var(--c-text2)", marginBottom: 12 }}>
            많을수록 아직 해결 안 된 약점
          </div>
          {typeEntries.map((item, i) => {
            const maxVal = typeEntries[0]?.value ?? 1;
            const pct = Math.round((item.value / maxVal) * 100);
            return (
              <div key={item.key} style={{ marginBottom: 10 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontSize: 14, color: "var(--c-text)" }}>{item.name}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: BAR_COLORS[i % BAR_COLORS.length] }}>
                    {fmtNum(item.value)}회
                  </span>
                </div>
                <div style={{ background: "var(--c-bg)", borderRadius: 4, height: 6 }}>
                  <div style={{
                    height: "100%", width: `${pct}%`, borderRadius: 4,
                    background: BAR_COLORS[i % BAR_COLORS.length],
                    transition: "width 0.4s",
                  }} />
                </div>
              </div>
            );
          })}
        </div>

        {/* 플랫폼 / 글 유형 비교 */}
        <div style={{ ...panel(), marginTop: 0 }}>
          <SectionTitle>플랫폼 · 글 유형별 품질</SectionTitle>
          <div style={{ fontSize: 13, color: "var(--c-text2)", marginBottom: 12 }}>
            글당 평균 이슈 수 — 낮을수록 좋음
          </div>

          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-text2)", marginBottom: 8 }}>플랫폼</div>
            {Object.entries(byPlatform).map(([plat, d]) => (
              <div key={plat} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "10px 14px", borderRadius: 8, marginBottom: 6,
                background: "var(--c-bg)", border: "1px solid var(--c-bdr)",
              }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: "var(--c-text)" }}>{plat}</div>
                <div style={{ display: "flex", gap: 20, fontSize: 14 }}>
                  <span style={{ color: "var(--c-text2)" }}>{d.posts}개 글</span>
                  <span style={{ fontWeight: 700, color: d.avg_issues <= 3 ? "var(--c-success)" : d.avg_issues <= 4.5 ? "var(--c-warn)" : "var(--c-danger)" }}>
                    평균 {d.avg_issues}개
                  </span>
                </div>
              </div>
            ))}
          </div>

          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--c-text2)", marginBottom: 8 }}>글 유형</div>
            {Object.entries(byPostType).map(([pt, d]) => {
              const label = pt === "economic" ? "경제 브리핑" : pt === "theme" ? "테마주 분석" : pt;
              return (
                <div key={pt} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "10px 14px", borderRadius: 8, marginBottom: 6,
                  background: "var(--c-bg)", border: "1px solid var(--c-bdr)",
                }}>
                  <div style={{ fontSize: 14, fontWeight: 600, color: "var(--c-text)" }}>{label}</div>
                  <div style={{ display: "flex", gap: 20, fontSize: 14 }}>
                    <span style={{ color: "var(--c-text2)" }}>{d.posts}개 글</span>
                    <span style={{ fontWeight: 700, color: d.avg_issues <= 3 ? "var(--c-success)" : d.avg_issues <= 4.5 ? "var(--c-warn)" : "var(--c-danger)" }}>
                      평균 {d.avg_issues}개
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── 학습 인사이트 ── */}
      {topInsights.length > 0 && (
        <div style={panel("var(--c-success)")}>
          <SectionTitle>현재 학습된 인사이트 TOP {topInsights.length}</SectionTitle>
          <div style={{ fontSize: 13, color: "var(--c-text2)", marginBottom: 16 }}>
            다음 글 작성 시 자동으로 주입되는 개선 지침
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {topInsights.map((ins, i) => (
              <div key={i} style={{
                background: "var(--c-bg)", border: "1px solid var(--c-bdr)",
                borderRadius: 8, padding: "12px 16px",
                display: "flex", gap: 14, alignItems: "flex-start",
              }}>
                <div style={{
                  flexShrink: 0, width: 28, height: 28, borderRadius: "50%",
                  background: "var(--c-success)22", color: "var(--c-success)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12, fontWeight: 700,
                }}>
                  {ins.occurrences}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, marginBottom: 4, alignItems: "center" }}>
                    <span style={{
                      fontSize: 11, fontWeight: 700, padding: "1px 7px", borderRadius: 10,
                      background: "var(--c-primary)22", color: "var(--c-primary)",
                    }}>
                      {TYPE_KO[ins.insight_type] ?? ins.insight_type}
                    </span>
                    <span style={{ fontSize: 12, color: "var(--c-text2)" }}>
                      {ins.occurrences}회 발견
                    </span>
                  </div>
                  <div style={{
                    fontSize: 14, color: "var(--c-text)",
                    overflow: "hidden", textOverflow: "ellipsis",
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                  }}>
                    {ins.description}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 발행 이력 ── */}
      <div style={panel()}>
        <SectionTitle>
          발행 이력{" "}
          {history.length > 0 && (
            <span style={{ fontSize: 14, color: "var(--c-text2)", fontWeight: 400 }}>
              ({history.length}건)
            </span>
          )}
        </SectionTitle>
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
                  const titleDisplay = row.title.length > 180 ? row.title.slice(0, 180) + "…" : row.title;
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
                          fontSize: 12, fontWeight: 700, padding: "2px 8px", borderRadius: 6,
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
                          <button disabled={busy || row.status === "approved"}
                            onClick={() => doAction(row.id, "approve")}
                            style={{
                              fontSize: 12, fontWeight: 600, padding: "4px 12px", borderRadius: 6,
                              border: "1px solid var(--c-success)", background: "transparent",
                              color: "var(--c-success)",
                              cursor: busy || row.status === "approved" ? "not-allowed" : "pointer",
                              opacity: busy || row.status === "approved" ? 0.4 : 1,
                            }}>승인</button>
                          <button disabled={busy || row.status === "rejected"}
                            onClick={() => doAction(row.id, "reject")}
                            style={{
                              fontSize: 12, fontWeight: 600, padding: "4px 12px", borderRadius: 6,
                              border: "1px solid var(--c-danger)", background: "transparent",
                              color: "var(--c-danger)",
                              cursor: busy || row.status === "rejected" ? "not-allowed" : "pointer",
                              opacity: busy || row.status === "rejected" ? 0.4 : 1,
                            }}>반려</button>
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
