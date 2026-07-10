"use client";
import useSWR from "swr";
import { apiFetch, PerformanceData } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/utils";

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

/* ── KPI 카드 ────────────────────────────────────────────────────── */
function KpiCard({ label, value, color, sub }: { label: string; value: string; color: string; sub?: string }) {
  return (
    <div style={card(color)}>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 8 }}>{label}</div>
      {sub && <div style={{ fontSize: 12, color: "var(--c-text5)", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

/* ── 플랫폼 뱃지 ──────────────────────────────────────────────────── */
function PlatBadge({ platform }: { platform: string }) {
  const isNaver = platform === "naver";
  return (
    <span style={{
      fontSize: 12,
      fontWeight: 700,
      padding: "2px 8px",
      borderRadius: 6,
      background: isNaver ? "#03c75a22" : "#f9640022",
      color: isNaver ? "#03c75a" : "#f96400",
    }}>
      {isNaver ? "N" : "T"}
    </span>
  );
}

/* ── 7일 바 차트 ──────────────────────────────────────────────────── */
function BarChart({ history }: { history: { d: string; v: number }[] }) {
  if (!history || history.length === 0) {
    return <div style={{ color: "var(--c-text5)", fontSize: 14, padding: "12px 0" }}>데이터 없음</div>;
  }
  const maxV = Math.max(...history.map(h => h.v), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {history.map((h) => {
        const pct = Math.max((h.v / maxV) * 100, 2);
        return (
          <div key={h.d} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 12, color: "var(--c-text2)", minWidth: 50, flexShrink: 0 }}>
              {h.d.slice(5)}
            </span>
            <div style={{ flex: 1, background: "var(--c-bdr)", borderRadius: 4, height: 40, overflow: "hidden" }}>
              <div style={{
                width: `${pct}%`,
                height: "100%",
                background: "var(--c-primary)",
                borderRadius: 4,
                transition: "width 0.4s ease",
                display: "flex",
                alignItems: "center",
                paddingLeft: 10,
              }}>
                {pct > 20 && (
                  <span style={{ fontSize: 12, fontWeight: 700, color: "#fff" }}>{fmtNum(h.v)}</span>
                )}
              </div>
            </div>
            {pct <= 20 && (
              <span style={{ fontSize: 12, color: "var(--c-text2)", minWidth: 36, textAlign: "right" }}>
                {fmtNum(h.v)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── 메인 컴포넌트 ────────────────────────────────────────────────── */
export default function PerformancePage() {
  const { data, isLoading } =
    useSWR<PerformanceData>("/api/performance", (url) => apiFetch<PerformanceData>(url), { refreshInterval: 120000 });

  const totalViews       = data?.total_views ?? 0;
  const naverViews       = data?.platform_views?.["naver"] ?? 0;
  const tistoryViews     = data?.platform_views?.["tistory"] ?? 0;
  const topPosts         = (data?.top_posts ?? []).slice(0, 15);
  const naverRanked      = (data?.naver_ranked ?? []).slice(0, 10);
  const history          = data?.history ?? [];

  return (
    <div>
      {/* 제목 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>성과 분석</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>플랫폼별 조회수 및 발행 글 성과 추적</p>
      </div>

      {/* KPI 3개 */}
      {isLoading ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard label="총 조회수" value={fmtNum(totalViews)} color="var(--c-primary)" />
          <KpiCard label="네이버 조회수" value={fmtNum(naverViews)} color="var(--c-success)" />
          <KpiCard label="티스토리 조회수" value={fmtNum(tistoryViews)} color="var(--c-warn)" />
        </div>
      )}

      {/* 7일 조회수 추이 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>7일 조회수 추이</h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : (
          <BarChart history={history} />
        )}
      </div>

      {/* TOP 15 발행 글 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          TOP 15 발행 글
          {topPosts.length > 0 && (
            <span style={{ fontSize: 14, fontWeight: 400, color: "var(--c-text2)", marginLeft: 8 }}>
              ({topPosts.length}건)
            </span>
          )}
        </h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : topPosts.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>발행 글이 없습니다.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={th}>플랫폼</th>
                  <th style={{ ...th, minWidth: 280 }}>제목</th>
                  <th style={{ ...th, textAlign: "right" }}>조회수</th>
                  <th style={{ ...th, textAlign: "right" }}>네이버 순위</th>
                  <th style={{ ...th, textAlign: "right" }}>발행일</th>
                </tr>
              </thead>
              <tbody>
                {topPosts.map((post, i) => {
                  const titleDisplay = post.title.length > 300
                    ? post.title.slice(0, 300) + "…"
                    : post.title;
                  const isLast = i === topPosts.length - 1;
                  const rowTd = isLast ? { ...td, borderBottom: "none" } : td;
                  const viewColor =
                    post.current_views === 0 ? "var(--c-text5)"
                    : post.current_views >= 100 ? "var(--c-success)"
                    : "var(--c-text)";

                  return (
                    <tr key={i}
                      onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td style={rowTd}><PlatBadge platform={post.platform} /></td>
                      <td style={rowTd}>{titleDisplay}</td>
                      <td style={{ ...rowTd, textAlign: "right", fontVariantNumeric: "tabular-nums", color: viewColor, fontWeight: post.current_views >= 100 ? 700 : 400 }}>
                        {fmtNum(post.current_views)}
                      </td>
                      <td style={{ ...rowTd, textAlign: "right" }}>
                        {post.naver_rank != null ? (
                          <span style={{ color: "var(--c-primary)", fontWeight: 700 }}>{post.naver_rank}위</span>
                        ) : (
                          <span style={{ color: "var(--c-text5)" }}>—</span>
                        )}
                      </td>
                      <td style={{ ...rowTd, textAlign: "right", color: "var(--c-text2)" }}>
                        {fmtTime(post.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 네이버 순위 목록 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          네이버 순위 TOP 10
        </h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : naverRanked.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>순위 데이터가 없습니다.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {naverRanked.map((row, i) => (
              <div key={i} style={{
                display: "flex",
                alignItems: "center",
                gap: 14,
                padding: "12px 14px",
                background: "rgba(255,255,255,0.02)",
                borderRadius: 8,
                border: "1px solid var(--c-bdr)",
              }}>
                {/* 순위 뱃지 */}
                <span style={{
                  minWidth: 34,
                  height: 34,
                  borderRadius: "50%",
                  background: i < 3 ? "var(--c-primary)" : "var(--c-bdr)",
                  color: i < 3 ? "#fff" : "var(--c-text2)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 14,
                  fontWeight: 800,
                  flexShrink: 0,
                }}>
                  {row.naver_rank}
                </span>
                {/* 제목 */}
                <span style={{ flex: 1, fontSize: 14, color: "var(--c-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {row.title}
                </span>
                {/* 조회수 */}
                <span style={{ fontSize: 14, fontWeight: 700, color: "var(--c-success)", flexShrink: 0 }}>
                  {fmtNum(row.current_views)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
