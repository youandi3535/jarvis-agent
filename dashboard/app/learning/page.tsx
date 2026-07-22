"use client";
import useSWR from "swr";
import { apiFetch, LearningData, WeightRow, BacktestRow, InsightRow } from "@/lib/api";
import { fmtNum, fmtTime } from "@/lib/utils";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";

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

/* ── KPI 추세 차트 (★ ERRORS [479]) ───────────────────────────────
   홈탭과 동일한 recharts 패턴 사용 — 새 라이브러리·새 API 도입 없음. */
function TrendChart({ title, hint, data, xKey, yKey, color, unit = "" }: {
  title: string; hint: string;
  data: Array<Record<string, unknown>>; xKey: string; yKey: string;
  color: string; unit?: string;
}) {
  const pts = data ?? [];
  return (
    <div style={card(color)}>
      <div style={{ fontSize: 16, fontWeight: 700, color: "var(--c-text)" }}>{title}</div>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 4, marginBottom: 12 }}>{hint}</div>
      {pts.length < 2 ? (
        <div style={{ fontSize: 14, color: "var(--c-text2)", padding: "28px 0", textAlign: "center" }}>
          추세를 그릴 만큼 기록이 쌓이지 않았습니다 ({pts.length}개)
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={pts} margin={{ top: 4, right: 12, left: -12, bottom: 0 }}>
            <CartesianGrid stroke="var(--c-bdr)" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey={xKey} tick={{ fontSize: 14, fill: "var(--c-text2)" }}
                   tickFormatter={(v: string) => String(v ?? "").slice(5, 10)}
                   minTickGap={28} stroke="var(--c-bdr)" />
            <YAxis tick={{ fontSize: 14, fill: "var(--c-text2)" }} stroke="var(--c-bdr)" width={44} />
            <Tooltip
              contentStyle={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)",
                              borderRadius: 8, fontSize: 14 }}
              labelFormatter={(v) => String(v ?? "").slice(0, 16)}
              formatter={(v) => [`${v ?? ""}${unit}`, title] as [string, string]} />
            <Line type="monotone" dataKey={yKey} stroke={color} strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      )}
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

  /* ── KPI (★ ERRORS [479] 교체) ─────────────────────────────────────
     종전 4개는 전부 RADAR 키워드 회귀(learned_weights) 지표였는데 그 학습기는
     0행이라 아예 안 돌고 있었고, 백테스트는 정답값이 상수라 항상 r2=1.0(=100%)
     이 나오는 가짜 만점이었다. '학습 인사이트'는 API 의 LIMIT 20 배열 길이를
     실제 개수(309)로 착각한 값이었다.
     → 이 시스템 자기학습의 본체인 GUARDIAN 오류학습 지표로 교체하고,
       *넷 다 시계열이 있는 것* 만 골라 아래에 추세 차트를 붙인다. */
  const timeline    = data?.timeline ?? [];
  const resolveRate = data?.resolve_rate ?? [];
  const patNow      = data?.patterns_now ?? { count: 0, hits: 0 };
  const last        = timeline.length ? timeline[timeline.length - 1] : null;

  const kpiPatterns = patNow.count || last?.patterns || 0;
  const kpiHits     = patNow.hits  || last?.hits     || 0;
  const kpiSaved    = last?.llm_saved ?? 0;
  const kpiRate     = resolveRate.length ? resolveRate[resolveRate.length - 1].rate : null;

  /* 글 품질 학습(ADR 014) — 오류 학습과 다른 시스템 (★ ERRORS [480]) */
  const q        = data?.quality_now ?? { insights: 0, usage: 0, rewards: 0, avg_weight: 0, used: 0 };
  const qTimeline = data?.quality_timeline ?? [];

  const insights      = (data?.insights ?? []).slice(0, 20);
  const insightsTotal = data?.insights_total ?? insights.length;
  const backtests     = (data?.backtest ?? []).slice(0, 14);
  const weights       = (data?.weights ?? []).slice(0, 3);

  return (
    <div>
      {/* 제목 */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 30, fontWeight: 800, color: "var(--c-text)", margin: 0 }}>AI 자기학습</h1>
        <p style={{ fontSize: 14, color: "var(--c-text2)", marginTop: 6 }}>
            서로 다른 두 학습 시스템 — ① 오류 자가수리(GUARDIAN) ② 글 품질(ADR 014)
          </p>
      </div>

      {/* KPI 4개 */}
      {isLoading ? (
        <div style={{ color: "var(--c-text2)", fontSize: 14 }}>로딩 중…</div>
      ) : (
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <KpiCard label="오류 학습 패턴"    value={fmtNum(kpiPatterns)} color="var(--c-primary)" />
          <KpiCard label="오류 패턴 적중"    value={fmtNum(kpiHits)}     color="var(--c-success)" />
          <KpiCard label="오류 — LLM 없이 해결" value={fmtNum(kpiSaved)}  color="var(--c-warn)" />
          <KpiCard label="오류 자동해소율"    value={kpiRate != null ? `${kpiRate.toFixed(1)}%` : "—"}
                   color="var(--c-muted, #94a3b8)" />
        </div>
      )}

      {/* ── ① 오류 자가수리 학습 (GUARDIAN) — 추세 ─────────────────── */}
      {!isLoading && (
        <>
          <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--c-text)", margin: "28px 0 4px" }}>
            🛡 오류 자가수리 학습 (GUARDIAN)
          </h2>
          <p style={{ fontSize: 14, color: "var(--c-text2)", margin: "0 0 12px" }}>
            런타임 오류를 스스로 고치는 학습 — 위 KPI 4개가 이 시스템의 지표입니다
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: 16 }}>
            <TrendChart title="오류 학습 패턴 누적" hint="자산이 늘고 있나 — 평평해지면 학습 정체"
                        data={timeline} xKey="at" yKey="patterns" color="#3b82f6" />
            <TrendChart title="오류 패턴 적중 누적" hint="학습한 패턴이 실제로 재사용되고 있나"
                        data={timeline} xKey="at" yKey="hits" color="#22c55e" />
            <TrendChart title="오류 — LLM 없이 해결" hint="학습의 목적 — 높을수록 LLM 호출 절약"
                        data={timeline} xKey="at" yKey="llm_saved" color="#f59e0b" />
            <TrendChart title="오류 자동해소율 (일별)" hint="학습이 결과를 바꾸고 있나" unit="%"
                        data={resolveRate} xKey="at" yKey="rate" color="#94a3b8" />
          </div>

          {/* ── ② 글 품질 학습 (ADR 014) — 오류 학습과 다른 시스템 ────── */}
          <h2 style={{ fontSize: 20, fontWeight: 700, color: "var(--c-text)", margin: "32px 0 4px" }}>
            ✍ 글 품질 학습 (ADR 014)
          </h2>
          <p style={{ fontSize: 14, color: "var(--c-text2)", margin: "0 0 12px" }}>
            블로그 글을 더 잘 쓰게 만드는 학습 — 위 오류 학습과 별개 시스템입니다
          </p>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
            <KpiCard label="품질 지침"      value={fmtNum(q.insights)} color="var(--c-primary)" />
            <KpiCard label="지침 주입"      value={fmtNum(q.usage)}    color="var(--c-success)" />
            <KpiCard label="보상 검증"      value={fmtNum(q.rewards)}  color="var(--c-warn)" />
            <KpiCard label="평균 지침 가중치" value={q.avg_weight.toFixed(3)}
                     color="var(--c-muted, #94a3b8)" />
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: 16 }}>
            <TrendChart title="품질 지침 누적" hint="글쓰기 지침 자산이 늘고 있나"
                        data={qTimeline} xKey="at" yKey="insights" color="#3b82f6" />
            <TrendChart title="신규 지침 (일별)" hint="새 지침이 계속 발굴되고 있나"
                        data={qTimeline} xKey="at" yKey="added" color="#22c55e" />
          </div>
        </>
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
