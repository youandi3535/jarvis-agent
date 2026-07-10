"use client";
import useSWR from "swr";
import { apiFetch, LearningData, WeightRow, BacktestRow, InsightRow } from "@/lib/api";
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

/* ── 가중치 필드 목록 ─────────────────────────────────────────────── */
const WEIGHT_KEYS: { key: string; label: string }[] = [
  { key: "w_trend",       label: "트렌드" },
  { key: "w_perf",        label: "성과" },
  { key: "w_fresh",       label: "신선도" },
  { key: "w_velocity",    label: "속도" },
  { key: "w_competition", label: "경쟁도" },
];

/* ── 가중치 파싱 ──────────────────────────────────────────────────── */
function parseWeights(json: string): Record<string, number> {
  try { return JSON.parse(json) ?? {}; }
  catch { return {}; }
}

/* ── KPI 카드 ────────────────────────────────────────────────────── */
function KpiCard({ label, value, color }: { label: string; value: string | number; color: string }) {
  return (
    <div style={card(color)}>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 8 }}>{label}</div>
    </div>
  );
}

/* ── 가로 미니 바 ─────────────────────────────────────────────────── */
function MiniBar({ value, max = 1, color = "var(--c-primary)" }: { value: number; max?: number; color?: string }) {
  const pct = Math.min((value / Math.max(max, 0.001)) * 100, 100);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, height: 8, background: "var(--c-bdr)", borderRadius: 4, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 4, transition: "width 0.3s" }} />
      </div>
      <span style={{ fontSize: 12, color: "var(--c-text2)", minWidth: 34, textAlign: "right" }}>
        {(value * 100).toFixed(0)}%
      </span>
    </div>
  );
}

/* ── 가중치 카드 ──────────────────────────────────────────────────── */
function WeightCard({ row }: { row: WeightRow }) {
  const w = parseWeights(row.weights_json);
  return (
    <div style={{
      background: "rgba(255,255,255,0.02)",
      border: "1px solid var(--c-bdr)",
      borderRadius: 10,
      padding: "16px 18px",
      flex: 1,
      minWidth: 200,
    }}>
      <div style={{ fontSize: 12, color: "var(--c-text5)", marginBottom: 12 }}>
        {fmtTime(row.trained_at)} &nbsp;·&nbsp; 백테스트 {row.backtest_score != null ? `${(row.backtest_score * 100).toFixed(1)}%` : "—"}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {WEIGHT_KEYS.map(({ key, label }) => {
          const val = typeof w[key] === "number" ? w[key] : 0;
          return (
            <div key={key}>
              <div style={{ fontSize: 12, color: "var(--c-text2)", marginBottom: 4 }}>{label}</div>
              <MiniBar value={val} max={1} color="var(--c-primary)" />
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── 백테스트 점수 색상 ───────────────────────────────────────────── */
function scoreColor(score: number): string {
  if (score >= 80) return "var(--c-success)";
  if (score >= 60) return "var(--c-warn)";
  return "var(--c-danger)";
}

/* ── 발생횟수 색상 ────────────────────────────────────────────────── */
function occColor(n: number): string {
  if (n >= 10) return "var(--c-primary)";
  if (n >= 5)  return "var(--c-warn)";
  return "var(--c-text2)";
}

/* ── 메인 컴포넌트 ────────────────────────────────────────────────── */
export default function LearningPage() {
  const { data, isLoading } =
    useSWR<LearningData>("/api/learning", (url) => apiFetch<LearningData>(url), { refreshInterval: 300000 });

  /* KPI 파싱 */
  const firstWeight   = data?.weights?.[0];
  const firstWeightW  = firstWeight ? parseWeights(firstWeight.weights_json) : {};
  const nSamples: number = (firstWeightW["n_samples"] as number) ?? 0;

  const firstBacktest = data?.backtest?.[0];
  const btScore       = firstBacktest?.score ?? null;

  const insights      = (data?.insights ?? []).slice(0, 20);
  const backtests     = (data?.backtest ?? []).slice(0, 14);
  const weights       = (data?.weights ?? []).slice(0, 3);
  const mae           = data?.learn_log?.mae ?? null;

  return (
    <div>
      {/* 제목 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>AI 자기학습</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>품질 강화학습 가중치·인사이트·백테스트 현황</p>
      </div>

      {/* KPI 4개 */}
      {isLoading ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard
            label="학습 샘플 수"
            value={fmtNum(nSamples)}
            color="var(--c-primary)"
          />
          <KpiCard
            label="백테스트 정확도"
            value={btScore != null ? `${(btScore * 100).toFixed(1)}%` : "—"}
            color="var(--c-success)"
          />
          <KpiCard
            label="학습 인사이트"
            value={fmtNum(data?.insights?.length ?? 0)}
            color="var(--c-warn)"
          />
          <KpiCard
            label="예측 오차 MAE"
            value={mae != null ? mae.toFixed(1) : "—"}
            color="var(--c-muted, #94a3b8)"
          />
        </div>
      )}

      {/* 가중치 변화 — 최신 3개 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          가중치 변화 — 최신 3회
        </h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : weights.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>가중치 데이터가 없습니다.</div>
        ) : (
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {weights.map((row, i) => <WeightCard key={i} row={row} />)}
          </div>
        )}
      </div>

      {/* 학습 인사이트 목록 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          학습 인사이트
          {insights.length > 0 && (
            <span style={{ fontSize: 14, fontWeight: 400, color: "var(--c-text2)", marginLeft: 8 }}>
              ({insights.length}건)
            </span>
          )}
        </h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : insights.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>인사이트가 없습니다.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={th}>키</th>
                  <th style={th}>타입</th>
                  <th style={{ ...th, minWidth: 220 }}>설명</th>
                  <th style={{ ...th, textAlign: "right" }}>발생횟수</th>
                  <th style={{ ...th, textAlign: "right" }}>최근</th>
                </tr>
              </thead>
              <tbody>
                {insights.map((row: InsightRow, i: number) => {
                  const isLast = i === insights.length - 1;
                  const rowTd = isLast ? { ...td, borderBottom: "none" } : td;
                  const desc  = row.description.length > 150
                    ? row.description.slice(0, 150) + "…"
                    : row.description;

                  return (
                    <tr key={i}
                      onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.03)")}
                      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                      <td style={rowTd}>
                        <code style={{ fontSize: 12, color: "var(--c-primary)", background: "rgba(79,144,217,0.1)", padding: "2px 6px", borderRadius: 4 }}>
                          {row.insight_key}
                        </code>
                      </td>
                      <td style={rowTd}>
                        <span style={{ fontSize: 12, color: "var(--c-text2)" }}>{row.insight_type}</span>
                      </td>
                      <td style={rowTd}>{desc}</td>
                      <td style={{ ...rowTd, textAlign: "right" }}>
                        <span style={{ fontWeight: 700, color: occColor(row.occurrences) }}>
                          {row.occurrences}
                        </span>
                      </td>
                      <td style={{ ...rowTd, textAlign: "right", color: "var(--c-text2)" }}>
                        {fmtTime(row.last_seen)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 백테스트 이력 */}
      <div style={section}>
        <h2 style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", margin: "0 0 16px" }}>
          백테스트 이력
          {backtests.length > 0 && (
            <span style={{ fontSize: 14, fontWeight: 400, color: "var(--c-text2)", marginLeft: 8 }}>
              ({backtests.length}건)
            </span>
          )}
        </h2>
        {isLoading ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
        ) : backtests.length === 0 ? (
          <div style={{ color: "var(--c-text2)", fontSize: 14, padding: "16px 0" }}>백테스트 기록이 없습니다.</div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {backtests.map((row: BacktestRow, i: number) => {
              const scorePct  = row.score * 100;
              const color     = scoreColor(scorePct);
              const detailStr = typeof row.details === "string" ? row.details : JSON.stringify(row.details ?? "");

              return (
                <div key={i} style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 16,
                  padding: "12px 16px",
                  background: "rgba(255,255,255,0.02)",
                  border: "1px solid var(--c-bdr)",
                  borderRadius: 8,
                  transition: "background 0.15s",
                }}
                  onMouseEnter={e => (e.currentTarget.style.background = "rgba(255,255,255,0.05)")}
                  onMouseLeave={e => (e.currentTarget.style.background = "rgba(255,255,255,0.02)")}>
                  {/* 점수 */}
                  <span style={{
                    fontSize: 20,
                    fontWeight: 800,
                    color,
                    minWidth: 58,
                    textAlign: "right",
                    flexShrink: 0,
                  }}>
                    {scorePct.toFixed(1)}%
                  </span>
                  {/* 구분선 */}
                  <div style={{ width: 1, height: 32, background: "var(--c-bdr)", flexShrink: 0 }} />
                  {/* 타입 */}
                  <span style={{ fontSize: 12, color: "var(--c-text2)", minWidth: 80, flexShrink: 0 }}>
                    {row.backtest_type}
                  </span>
                  {/* 상세 */}
                  <span style={{ fontSize: 14, color: "var(--c-text)", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {detailStr}
                  </span>
                  {/* 시각 */}
                  <span style={{ fontSize: 12, color: "var(--c-text5)", flexShrink: 0 }}>
                    {fmtTime(row.tested_at)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
