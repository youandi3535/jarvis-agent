"use client";
import useSWR from "swr";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  LineChart, Line, ResponsiveContainer,
} from "recharts";
import { apiFetch, PerformanceData } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/utils";

/* ── 플랫폼 표시 설정 (시각 전용 — 색상·약자) ─────────────────────── */
const PLAT_HEX: Record<string, string> = {
  naver:   "#03c75a",
  tistory: "#f96400",
  wp:      "#21759b",
};
const PLAT_SHORT: Record<string, string> = {
  naver: "N", tistory: "T", wp: "W",
};

/* ── 공통 스타일 ─────────────────────────────────────────────────── */
const section: React.CSSProperties = {
  background: "var(--c-card)",
  border: "1px solid var(--c-bdr)",
  borderRadius: 12,
  padding: 20,
  marginTop: 24,
};
const th: React.CSSProperties = {
  textAlign: "right",
  padding: "10px 14px",
  fontSize: 14,
  color: "var(--c-text2)",
  fontWeight: 600,
  borderBottom: "1px solid var(--c-bdr)",
  whiteSpace: "nowrap",
};
const td: React.CSSProperties = {
  padding: "10px 14px",
  fontSize: 14,
  color: "var(--c-text)",
  borderBottom: "1px solid var(--c-bdr)",
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const tooltipStyle: React.CSSProperties = {
  background: "var(--c-card)",
  border: "1px solid var(--c-bdr)",
  borderRadius: 8,
  fontSize: 14,
};

/* ── 플랫폼 뱃지 ──────────────────────────────────────────────────── */
function PlatBadge({ platform }: { platform: string }) {
  const color = PLAT_HEX[platform] ?? "#888";
  const short = PLAT_SHORT[platform] ?? platform[0]?.toUpperCase();
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      width: 24, height: 24, borderRadius: 6,
      fontSize: 12, fontWeight: 800,
      background: color + "22", color,
      flexShrink: 0,
    }}>
      {short}
    </span>
  );
}

/* ── 메인 컴포넌트 ────────────────────────────────────────────────── */
export default function PerformancePage() {
  const { data, isLoading } = useSWR<PerformanceData>(
    "/api/performance",
    (url: string) => apiFetch<PerformanceData>(url),
    { refreshInterval: 120000 },
  );

  const active      = data?.active_platforms ?? [];
  const platLabels  = data?.platform_labels ?? {};
  const periods     = data?.period_order ?? [];
  const pLabels     = data?.period_labels ?? {};
  const pViews      = data?.period_views ?? {};
  const trend       = (data?.daily_trend ?? []) as Array<Record<string, number | string>>;
  const topPosts    = (data?.top_posts ?? []).slice(0, 15);
  const dr          = data?.data_range;

  /* 기간별 차트 데이터 */
  const periodChartData = periods.map(pid => {
    const entry: Record<string, number | string> = { name: pLabels[pid] ?? pid };
    active.forEach(p => { entry[p] = pViews[pid]?.[p] ?? 0; });
    return entry;
  });

  /* 일별 추이 날짜 포맷 MM/DD */
  const trendFormatted = trend.map(r => ({
    ...r,
    label: typeof r.date === "string" ? r.date.slice(5) : r.date,
  }));

  const subtitle = dr?.from && dr?.to
    ? `수집 기간 ${dr.from} ~ ${dr.to} · ${dr.days}일치`
    : "조회수 집계";

  return (
    <div>
      {/* 헤더 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 32, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>성과 분석</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>{subtitle}</p>
      </div>

      {isLoading && <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>}

      {/* ── 기간 × 플랫폼 매트릭스 ─────────────────────────────────── */}
      {!isLoading && (
        <div style={{ ...section, overflowX: "auto" }}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
            기간별 조회수
          </h2>

          {active.length === 0 ? (
            <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>
              수집된 조회수 데이터가 없습니다.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 640 }}>
              <thead>
                <tr>
                  <th style={{ ...th, textAlign: "left", minWidth: 100 }}>플랫폼</th>
                  {periods.map(pid => (
                    <th key={pid} style={{
                      ...th,
                      color: pid === "all" ? "var(--c-text)" : "var(--c-text2)",
                      fontWeight: pid === "all" ? 700 : 600,
                    }}>
                      {pLabels[pid] ?? pid}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {active.map(plat => (
                  <tr key={plat}>
                    <td style={{ ...td, textAlign: "left" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <PlatBadge platform={plat} />
                        <span>{platLabels[plat] ?? plat}</span>
                      </div>
                    </td>
                    {periods.map(pid => {
                      const v = pViews[pid]?.[plat] ?? 0;
                      return (
                        <td key={pid} style={{
                          ...td,
                          color: v === 0 ? "var(--c-text5)" : pid === "all" ? "var(--c-primary)" : "var(--c-text)",
                          fontWeight: pid === "all" ? 700 : 400,
                        }}>
                          {v === 0 ? "—" : fmtNum(v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                {/* 합계 행 (플랫폼 2개+ 일 때만) */}
                {active.length > 1 && (
                  <tr style={{ borderTop: "2px solid var(--c-bdr)" }}>
                    <td style={{ ...td, textAlign: "left", fontWeight: 700, color: "var(--c-text2)" }}>합계</td>
                    {periods.map(pid => {
                      const v = pViews[pid]?.total ?? 0;
                      return (
                        <td key={pid} style={{
                          ...td,
                          color: v === 0 ? "var(--c-text5)" : pid === "all" ? "var(--c-primary)" : "var(--c-text)",
                          fontWeight: 700,
                        }}>
                          {v === 0 ? "—" : fmtNum(v)}
                        </td>
                      );
                    })}
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ── 기간별 조회수 차트 ─────────────────────────────────────── */}
      {!isLoading && active.length > 0 && (
        <div style={section}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
            기간별 조회수 비교
          </h2>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={periodChartData} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--c-bdr)" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 13, fill: "var(--c-text2)" }} axisLine={false} tickLine={false} />
              <YAxis
                tick={{ fontSize: 12, fill: "var(--c-text2)" }}
                axisLine={false} tickLine={false}
                tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(value: any, name: any) => [fmtNum(Number(value)), platLabels[String(name)] ?? String(name)]}
                labelStyle={{ color: "var(--c-text)", fontWeight: 700, marginBottom: 4 }}
              />
              {active.length > 1 && <Legend formatter={(name: string) => platLabels[name] ?? name} />}
              {active.map(plat => (
                <Bar key={plat} dataKey={plat} fill={PLAT_HEX[plat] ?? "#888"} radius={[4, 4, 0, 0]} maxBarSize={48} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── 일별 조회수 추이 ───────────────────────────────────────── */}
      {!isLoading && trendFormatted.length > 0 && (
        <div style={section}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 4px" }}>
            일별 조회수 추이
            <span style={{ fontSize: 14, fontWeight: 400, color: "var(--c-text2)", marginLeft: 8 }}>
              ({trendFormatted.length}일)
            </span>
          </h2>
          {dr?.to && (
            <p style={{ fontSize: 14, color: "var(--c-text5)", margin: "0 0 16px" }}>
              마지막 수집 {dr.to}
            </p>
          )}
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trendFormatted} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--c-bdr)" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 12, fill: "var(--c-text2)" }} axisLine={false} tickLine={false} />
              <YAxis
                tick={{ fontSize: 12, fill: "var(--c-text2)" }}
                axisLine={false} tickLine={false}
                tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                formatter={(value: any, name: any) => [fmtNum(Number(value)), platLabels[String(name)] ?? String(name)]}
                labelStyle={{ color: "var(--c-text)", fontWeight: 700 }}
              />
              {active.length > 1 && <Legend formatter={(name: string) => platLabels[name] ?? name} />}
              {active.map(plat => (
                <Line
                  key={plat}
                  type="monotone"
                  dataKey={plat}
                  stroke={PLAT_HEX[plat] ?? "#888"}
                  strokeWidth={2}
                  dot={{ r: 4, fill: PLAT_HEX[plat] ?? "#888" }}
                  activeDot={{ r: 6 }}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── TOP N 개별 글 조회수 ──────────────────────────────────── */}
      {!isLoading && topPosts.length > 0 && (
        <div style={section}>
          <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
            조회수 TOP {topPosts.length}
            <span style={{ fontSize: 14, fontWeight: 400, color: "var(--c-text2)", marginLeft: 8 }}>글별</span>
          </h2>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ ...th, textAlign: "left" }}>플랫폼</th>
                  <th style={{ ...th, textAlign: "left", minWidth: 260 }}>제목</th>
                  <th style={th}>조회수</th>
                  <th style={th}>발행일</th>
                </tr>
              </thead>
              <tbody>
                {topPosts.map((post, i) => {
                  const isLast = i === topPosts.length - 1;
                  const rowTd = isLast ? { ...td, borderBottom: "none" } : td;
                  const vColor = post.current_views >= 100 ? "var(--c-success)" : "var(--c-text)";
                  return (
                    <tr key={i}
                      onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                    >
                      <td style={rowTd}>
                        <PlatBadge platform={post.platform} />
                      </td>
                      <td style={{ ...rowTd, textAlign: "left" }}>
                        {post.title.length > 60 ? post.title.slice(0, 60) + "…" : post.title}
                      </td>
                      <td style={{ ...rowTd, color: vColor, fontWeight: post.current_views >= 100 ? 700 : 400 }}>
                        {fmtNum(post.current_views)}
                      </td>
                      <td style={{ ...rowTd, color: "var(--c-text2)" }}>
                        {fmtTime(post.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
