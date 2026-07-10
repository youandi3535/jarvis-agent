"use client";
import useSWR from "swr";
import { fetcher, TrendData } from "@/lib/api";
import { C, fmtNum } from "@/lib/utils";

// ── 공통 서브 컴포넌트 ──────────────────────────────────────────

function KpiCard({
  label, value, sub, color,
}: {
  label: string; value: string | number; sub: string; color: string;
}) {
  return (
    <div style={{
      background: "var(--c-card)",
      border: "1px solid var(--c-bdr)",
      borderTop: `3px solid ${color}`,
      borderRadius: 12,
      padding: "24px 20px",
      textAlign: "center",
      flex: 1,
    }}>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginBottom: 12 }}>{label}</div>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 8 }}>{sub}</div>
    </div>
  );
}

function scoreColor(score: number): string {
  if (score >= 70) return C.success;
  if (score >= 50) return C.warn;
  return C.muted;
}

// ── 페이지 ──────────────────────────────────────────────────────

export default function RadarPage() {
  const { data } = useSWR<TrendData>("/api/trends", fetcher, { refreshInterval: 60000 });

  const sectorKeys = data?.sectors ? Object.keys(data.sectors) : [];
  const sectorCount = sectorKeys.length;
  const topScore = data?.top?.[0]?.opportunity_score ?? 0;
  const rows = data?.top?.slice(0, 15) ?? [];

  const sectorEntries = Object.entries(data?.sectors ?? {}).sort(
    ([, a], [, b]) => b - a
  );
  const maxSectorCount = sectorEntries.length > 0
    ? Math.max(...sectorEntries.map(([, v]) => v))
    : 1;

  return (
    <div>
      {/* ── 헤더 ── */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 32, fontWeight: 800, margin: 0, color: "var(--c-text)" }}>
          레이더 — 트렌드 분석
        </h1>
        <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 4 }}>
          실시간 키워드 트렌드 모니터링
        </div>
      </div>

      {/* ── KPI 3개 ── */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28 }}>
        <KpiCard
          label="수집 키워드"
          value={fmtNum(data?.today)}
          sub="오늘"
          color={C.primary}
        />
        <KpiCard
          label="섹터 수"
          value={fmtNum(sectorCount)}
          sub="카테고리"
          color={C.success}
        />
        <KpiCard
          label="최고 기회점수"
          value={topScore ? topScore.toFixed(1) : "—"}
          sub="Top 키워드"
          color={C.warn}
        />
      </div>

      {/* ── 메인 그리드: 키워드 테이블 + 섹터 바 차트 ── */}
      <div style={{ display: "grid", gridTemplateColumns: "3fr 1fr", gap: 20 }}>

        {/* 키워드 테이블 */}
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${C.primary}`, borderRadius: 12, padding: 20,
        }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16, color: "var(--c-text)" }}>
            오늘 트렌드 키워드
          </div>

          {rows.length === 0 ? (
            <div style={{ color: "var(--c-text5)", fontSize: 14 }}>
              {data ? "수집된 키워드 없음" : "로딩 중…"}
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ color: "var(--c-text5)", textAlign: "left" }}>
                  <th style={{ paddingBottom: 10, fontWeight: 600, width: 36 }}>#</th>
                  <th style={{ paddingBottom: 10, fontWeight: 600 }}>키워드</th>
                  <th style={{ paddingBottom: 10, fontWeight: 600 }}>섹터</th>
                  <th style={{ paddingBottom: 10, fontWeight: 600, textAlign: "right" }}>기회점수</th>
                  <th style={{ paddingBottom: 10, fontWeight: 600, paddingLeft: 16 }}>소스</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const sc = scoreColor(r.opportunity_score);
                  return (
                    <tr key={`${r.keyword}-${i}`} style={{ borderTop: "1px solid var(--c-bdr)" }}>
                      <td style={{ padding: "9px 0", color: "var(--c-text5)" }}>{i + 1}</td>
                      <td style={{ padding: "9px 10px 9px 0", color: "var(--c-text)", fontWeight: 600 }}>
                        {r.keyword}
                      </td>
                      <td style={{ padding: "9px 10px 9px 0" }}>
                        <span style={{
                          background: C.primary + "22", color: C.primary,
                          borderRadius: 20, padding: "3px 10px",
                          fontSize: 14, fontWeight: 600,
                        }}>
                          {r.sector}
                        </span>
                      </td>
                      <td style={{ padding: "9px 0", textAlign: "right" }}>
                        <span style={{ color: sc, fontWeight: 700, fontSize: 16 }}>
                          {r.opportunity_score.toFixed(1)}
                        </span>
                      </td>
                      <td style={{ padding: "9px 0 9px 16px", color: "var(--c-text5)" }}>
                        {r.source}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* 섹터별 분포 바 차트 */}
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${C.success}`, borderRadius: 12, padding: 20,
        }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16, color: "var(--c-text)" }}>
            섹터별 분포
          </div>

          {sectorEntries.length === 0 ? (
            <div style={{ color: "var(--c-text5)", fontSize: 14 }}>
              {data ? "섹터 데이터 없음" : "로딩 중…"}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {sectorEntries.map(([sector, count]) => (
                <div key={sector}>
                  <div style={{
                    display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6,
                  }}>
                    <span style={{ fontSize: 14, color: "var(--c-text2)" }}>{sector}</span>
                    <span style={{ fontSize: 14, fontWeight: 700, color: "var(--c-text)" }}>
                      {count}
                    </span>
                  </div>
                  <div style={{
                    background: "var(--c-bg)", borderRadius: 4,
                    height: 8, overflow: "hidden",
                  }}>
                    <div style={{
                      height: "100%",
                      width: `${Math.round((count / maxSectorCount) * 100)}%`,
                      background: C.primary,
                      borderRadius: 4,
                    }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
