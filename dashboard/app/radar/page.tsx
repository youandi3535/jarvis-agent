"use client";
import useSWR from "swr";
import { fetcher, TrendData, Top10Item, CombinedItem, RecommendItem, TrendDelta, TopicCandidate } from "@/lib/api";
import { C, fmtNum } from "@/lib/utils";

function KpiCard({ label, value, sub, color }: {
  label: string; value: string | number; sub: string; color: string;
}) {
  return (
    <div style={{
      background: "var(--c-card)", border: "1px solid var(--c-bdr)",
      borderTop: `3px solid ${color}`, borderRadius: 12,
      padding: "24px 20px", textAlign: "center", flex: 1,
    }}>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginBottom: 12 }}>{label}</div>
      <div style={{ fontSize: 44, fontWeight: 800, color, lineHeight: 1 }}>{value}</div>
      <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 8 }}>{sub}</div>
    </div>
  );
}

function velocityColor(v: string) {
  if (v === "급등") return C.danger;
  if (v === "상승") return C.warn;
  if (v === "하락") return C.muted;
  return C.primary;
}

export default function RadarPage() {
  const { data } = useSWR<TrendData>("/api/trends", fetcher, { refreshInterval: 60000 });

  const sectorCount = data?.sectors ? Object.keys(data.sectors).length : 0;
  const sectorEntries = Object.entries(data?.sectors ?? {}).sort(([, a], [, b]) => b - a);
  const maxSectorCount = sectorEntries.length > 0 ? Math.max(...sectorEntries.map(([, v]) => v)) : 1;

  const google10:    Top10Item[]      = data?.google_top10      ?? [];
  const naver10:     Top10Item[]      = data?.naver_top10       ?? [];
  const mixed30:     CombinedItem[]   = (data?.combined_keywords ?? []).slice(0, 30);
  const recs:        RecommendItem[]  = data?.recommendations   ?? [];
  const delta:       TrendDelta       = data?.trend_delta       ?? {};
  const candidates:  TopicCandidate[] = data?.topic_candidates  ?? [];

  const newCount = delta.new_entry?.length ?? 0;

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

      {/* ── KPI ── */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28 }}>
        <KpiCard label="수집 키워드" value={fmtNum(data?.today)} sub="오늘 분석에 쓴 총 키워드 수" color={C.primary} />
        <KpiCard label="섹터 수" value={fmtNum(sectorCount)} sub="카테고리" color={C.success} />
        <KpiCard label="신규 진입" value={data ? newCount : "—"} sub="어제 대비 새 키워드" color={C.warn} />
      </div>

      {/* ── 섹터별 분포 ── */}
      <div style={{
        background: "var(--c-card)", border: "1px solid var(--c-bdr)",
        borderTop: `3px solid ${C.success}`, borderRadius: 12, padding: 20, marginBottom: 20,
      }}>
        <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", marginBottom: 16 }}>섹터별 분포</div>
        {sectorEntries.length === 0 ? (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>{data ? "섹터 데이터 없음" : "로딩 중…"}</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>
            {sectorEntries.map(([sector, count]) => (
              <div key={sector}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <span style={{ fontSize: 14, color: "var(--c-text2)" }}>{sector}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: "var(--c-text)" }}>{count}</span>
                </div>
                <div style={{ background: "var(--c-bg)", borderRadius: 4, height: 8, overflow: "hidden" }}>
                  <div style={{
                    height: "100%",
                    width: `${Math.round((count / maxSectorCount) * 100)}%`,
                    background: C.primary, borderRadius: 4,
                  }} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 오늘의 발행 후보 ── */}
      {candidates.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", marginBottom: 12 }}>
            오늘의 발행 후보
          </div>
          <div style={{ display: "grid", gridTemplateColumns: `repeat(${candidates.length}, 1fr)`, gap: 16 }}>
            {candidates.map((c) => (
              <div key={c.keyword} style={{
                background: "var(--c-card)", border: "1px solid var(--c-bdr)",
                borderTop: `3px solid ${C.warn}`, borderRadius: 12, padding: 20,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
                  <span style={{ fontSize: 22, fontWeight: 800, color: C.warn }}>{c.keyword}</span>
                  <span style={{
                    background: C.primary + "22", color: C.primary,
                    borderRadius: 20, padding: "2px 10px", fontSize: 14, fontWeight: 600,
                  }}>{c.sector}</span>
                </div>
                <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 8 }}>{c.reason}</div>
                {c.profile?.summary && (
                  <div style={{ fontSize: 14, color: "var(--c-text2)" }}>{c.profile.summary}</div>
                )}
                <div style={{ marginTop: 12, fontSize: 16, fontWeight: 700, color: C.success }}>
                  기회점수 {c.opportunity_score.toFixed(1)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 추천 주제 랭킹 ── */}
      {recs.length > 0 && (
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${C.primary}`, borderRadius: 12, padding: 20, marginBottom: 20,
        }}>
          <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)", marginBottom: 16 }}>
            추천 주제 랭킹
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ color: "var(--c-text5)", textAlign: "left" }}>
                <th style={{ paddingBottom: 10, fontWeight: 600, width: 32 }}>#</th>
                <th style={{ paddingBottom: 10, fontWeight: 600 }}>키워드</th>
                <th style={{ paddingBottom: 10, fontWeight: 600 }}>섹터</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, textAlign: "right" }}>점수</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, textAlign: "right" }}>기회</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, paddingLeft: 12 }}>속도</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, textAlign: "right" }}>경쟁</th>
              </tr>
            </thead>
            <tbody>
              {recs.map((r, i) => (
                <tr key={r.keyword} style={{ borderTop: "1px solid var(--c-bdr)" }}>
                  <td style={{ padding: "9px 0", color: "var(--c-text5)" }}>{i + 1}</td>
                  <td style={{ padding: "9px 10px 9px 0", fontWeight: 700, color: "var(--c-text)" }}>{r.keyword}</td>
                  <td style={{ padding: "9px 10px 9px 0" }}>
                    <span style={{
                      background: C.primary + "22", color: C.primary,
                      borderRadius: 20, padding: "2px 8px", fontSize: 14, fontWeight: 600,
                    }}>{r.sector}</span>
                  </td>
                  <td style={{ padding: "9px 0", textAlign: "right", fontWeight: 700, color: C.success }}>{r.score}</td>
                  <td style={{ padding: "9px 0", textAlign: "right", color: "var(--c-text2)" }}>{r.opportunity_score.toFixed(1)}</td>
                  <td style={{ padding: "9px 0 9px 12px" }}>
                    <span style={{ color: velocityColor(r.velocity), fontWeight: 600 }}>{r.velocity}</span>
                  </td>
                  <td style={{ padding: "9px 0", textAlign: "right", color: r.competition >= 80 ? C.danger : "var(--c-text5)" }}>
                    {r.competition.toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── 오늘의 변화 ── */}
      {(delta.new_entry?.length || delta.risen?.length || delta.fallen?.length) ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 20 }}>

          {/* 신규 진입 */}
          <div style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${C.success}`, borderRadius: 12, padding: 20,
          }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.success, marginBottom: 12 }}>
              🆕 신규 진입 ({delta.new_entry?.length ?? 0})
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {(delta.new_entry ?? []).map(kw => (
                <span key={kw} style={{
                  background: C.success + "22", color: C.success,
                  borderRadius: 20, padding: "4px 12px", fontSize: 14, fontWeight: 600,
                }}>{kw}</span>
              ))}
            </div>
          </div>

          {/* 급등 */}
          <div style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${C.danger}`, borderRadius: 12, padding: 20,
          }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: C.danger, marginBottom: 12 }}>
              📈 급등 ({delta.risen?.length ?? 0})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(delta.risen ?? []).map(r => (
                <div key={r.keyword} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 14, color: "var(--c-text)" }}>{r.keyword}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: C.danger }}>▲{r.delta}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 하락 */}
          <div style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${C.muted}`, borderRadius: 12, padding: 20,
          }}>
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--c-text5)", marginBottom: 12 }}>
              📉 하락 ({delta.fallen?.length ?? 0})
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(delta.fallen ?? []).map(r => (
                <div key={r.keyword} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 14, color: "var(--c-text)" }}>{r.keyword}</span>
                  <span style={{ fontSize: 14, fontWeight: 700, color: "var(--c-text5)" }}>▼{r.delta}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {/* ── Google TOP N / Naver TOP N ── */}
      {(google10.length > 0 || naver10.length > 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 20 }}>
          {/* Google */}
          <div style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${C.primary}`, borderRadius: 12, padding: 20,
          }}>
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.primary }}>🔍 Google TOP {google10.length}</div>
              <div style={{ fontSize: 12, color: "var(--c-text5)", marginTop: 4 }}>
                점수 기준: pytrends 검색량 순위 역수 — 1위 = 1.00, 하위로 갈수록 감소
              </div>
            </div>
            {google10.map(item => {
              const pct = Math.round((item.score ?? 0) * 100);
              return (
                <div key={item.rank} style={{ padding: "7px 0", borderBottom: "1px solid var(--c-bdr)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: C.primary, minWidth: 22, textAlign: "right" }}>{item.rank}</span>
                    <span style={{ fontSize: 15, color: "var(--c-text)", flex: 1 }}>{item.keyword}</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: C.primary }}>{pct}점</span>
                  </div>
                  <div style={{ marginLeft: 32, height: 4, background: "var(--c-bdr)", borderRadius: 2 }}>
                    <div style={{ height: "100%", width: `${pct}%`, background: C.primary, borderRadius: 2, transition: "width 0.4s" }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* Naver */}
          <div style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${C.success}`, borderRadius: 12, padding: 20,
          }}>
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.success }}>🟢 Naver TOP {naver10.length}</div>
              <div style={{ fontSize: 12, color: "var(--c-text5)", marginTop: 4 }}>
                점수 기준: 뉴스 헤드라인 출현 빈도 — 최다 출현 키워드 = 1.00 (상대 정규화)
              </div>
            </div>
            {naver10.map(item => {
              const pct = Math.round((item.score ?? 0) * 100);
              return (
                <div key={item.rank} style={{ padding: "7px 0", borderBottom: "1px solid var(--c-bdr)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                    <span style={{ fontSize: 13, fontWeight: 700, color: C.success, minWidth: 22, textAlign: "right" }}>{item.rank}</span>
                    <span style={{ fontSize: 15, color: "var(--c-text)", flex: 1 }}>{item.keyword}</span>
                    <span style={{ fontSize: 13, fontWeight: 700, color: C.success }}>{pct}점</span>
                  </div>
                  <div style={{ marginLeft: 32, height: 4, background: "var(--c-bdr)", borderRadius: 2 }}>
                    <div style={{ height: "100%", width: `${pct}%`, background: C.success, borderRadius: 2, transition: "width 0.4s" }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── 혼합 TOP 30 ── */}
      {mixed30.length > 0 && (
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${C.warn}`, borderRadius: 12, padding: 20, marginBottom: 20,
        }}>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>
              혼합 트렌드 TOP {mixed30.length} (Google + Naver)
            </div>
            <div style={{ fontSize: 12, color: "var(--c-text5)", marginTop: 4 }}>
              점수 기준: (구글 점수 + 네이버 점수) ÷ 소스 수 · 양쪽 동시 등장 시 +15점 보너스
            </div>
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ color: "var(--c-text5)", textAlign: "left" }}>
                <th style={{ paddingBottom: 10, fontWeight: 600, width: 36 }}>#</th>
                <th style={{ paddingBottom: 10, fontWeight: 600 }}>키워드</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, width: 120 }}>점수</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, width: 110 }}>소스</th>
              </tr>
            </thead>
            <tbody>
              {mixed30.map((item, i) => {
                const hasGoogle = item.sources.includes("google");
                const hasNaver  = item.sources.includes("naver");
                const both      = hasGoogle && hasNaver;
                const pct       = Math.min(Math.round(item.score * 100), 100);
                return (
                  <tr key={`${item.keyword}-${i}`} style={{ borderTop: "1px solid var(--c-bdr)" }}>
                    <td style={{ padding: "8px 0", color: "var(--c-text5)", fontWeight: 700, verticalAlign: "middle" }}>{i + 1}</td>
                    <td style={{ padding: "8px 10px 8px 0", color: "var(--c-text)", verticalAlign: "middle" }}>
                      {item.keyword}
                      {both && <span style={{ marginLeft: 6, fontSize: 11, color: C.warn, fontWeight: 700 }}>★</span>}
                    </td>
                    <td style={{ padding: "8px 10px 8px 0", verticalAlign: "middle" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <div style={{ flex: 1, height: 4, background: "var(--c-bdr)", borderRadius: 2 }}>
                          <div style={{ height: "100%", width: `${pct}%`, background: both ? C.warn : hasGoogle ? C.primary : C.success, borderRadius: 2 }} />
                        </div>
                        <span style={{ fontSize: 12, fontWeight: 700, color: "var(--c-text2)", minWidth: 30, textAlign: "right" }}>{pct}점</span>
                      </div>
                    </td>
                    <td style={{ padding: "8px 0", verticalAlign: "middle" }}>
                      <div style={{ display: "flex", gap: 4 }}>
                        {hasGoogle && <span style={{ background: C.primary + "22", color: C.primary, borderRadius: 20, padding: "2px 7px", fontSize: 11, fontWeight: 600 }}>G</span>}
                        {hasNaver  && <span style={{ background: C.success + "22", color: C.success, borderRadius: 20, padding: "2px 7px", fontSize: 11, fontWeight: 600 }}>N</span>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--c-text5)" }}>
            ★ 구글·네이버 동시 등장 키워드 (크로스 플랫폼 검증)
          </div>
        </div>
      )}

    </div>
  );
}
