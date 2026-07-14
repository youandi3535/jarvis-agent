"use client";
import useSWR from "swr";
import { fetcher, PostStats } from "@/lib/api";
import { C, fmtNum, fmtTime } from "@/lib/utils";

// ── 타입 ────────────────────────────────────────────────────────

type PlatformStatus = {
  cookie_ok: boolean;
  cookie_age_hours?: number;
  posts_7d: number;
};

type PublishStatus = {
  naver: PlatformStatus;
  tistory: PlatformStatus;
};

type ThemeItem = {
  name: string;
  no: string;
  written: boolean;
};

type OfficialThemes = {
  total: number;
  written_count: number;
  themes: ThemeItem[];
  today_pick: { theme: string; sector: string; opportunity_score: number } | null;
};

// ── 서브 컴포넌트 ────────────────────────────────────────────────

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

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "3px 10px", borderRadius: 20,
      fontSize: 14, fontWeight: 600,
      background: color + "22", color,
    }}>
      {text}
    </span>
  );
}

function cookieAgeLabel(hours?: number): string {
  if (hours == null) return "—";
  if (hours < 1) return "방금 갱신";
  if (hours < 24) return `${Math.round(hours)}시간 전`;
  return `${Math.round(hours / 24)}일 전`;
}

// ── 메인 페이지 ─────────────────────────────────────────────────

export default function PostsPage() {
  const { data: posts }   = useSWR<PostStats>("/api/posts",   fetcher, { refreshInterval: 600000 });
  const { data: publish } = useSWR<PublishStatus>("/api/publish", fetcher, { refreshInterval: 1800000 });
  const { data: themes }  = useSWR<OfficialThemes>("/api/themes/official", fetcher, { refreshInterval: 3600000 });

  const naver   = publish?.naver;
  const tistory = publish?.tistory;

  const total        = themes?.total ?? 0;
  const writtenCount = themes?.written_count ?? 0;
  const remaining    = total > 0 ? total - writtenCount : 0;
  const pct          = total > 0 ? Math.round((writtenCount / total) * 100) : 0;
  const todayPick    = themes?.today_pick ?? null;
  const themeList    = themes?.themes ?? [];

  return (
    <div>
      {/* ── 헤더 ── */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 32, fontWeight: 800, margin: 0, color: "var(--c-text)" }}>
          발행 현황
        </h1>
        <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 4 }}>
          네이버 · 티스토리 발행 파이프라인
        </div>
      </div>

      {/* ── KPI ── */}
      <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
        <KpiCard label="오늘 발행"    value={fmtNum(posts?.today)}  sub="오늘"   color={C.primary} />
        <KpiCard label="이번 주"      value={fmtNum(posts?.week)}   sub="7일"    color={C.success} />
        <KpiCard label="이번 달"      value={fmtNum(posts?.month)}  sub="30일"   color={C.warn}    />
      </div>

      {/* ── 플랫폼 쿠키 상태 ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
        {([
          { label: "네이버 블로그", data: naver },
          { label: "티스토리",     data: tistory },
        ] as const).map(({ label, data }) => (
          <div key={label} style={{
            background: "var(--c-card)", border: "1px solid var(--c-bdr)",
            borderTop: `3px solid ${data?.cookie_ok ? C.success : C.danger}`,
            borderRadius: 12, padding: 20,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>{label}</div>
              <Badge text={data?.cookie_ok ? "쿠키 정상" : "쿠키 만료"} color={data?.cookie_ok ? C.success : C.danger} />
            </div>
            <div style={{ display: "flex", gap: 24 }}>
              <div>
                <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>쿠키 갱신</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>{cookieAgeLabel(data?.cookie_age_hours)}</div>
              </div>
              <div>
                <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>7일 발행</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: C.primary }}>{fmtNum(data?.posts_7d)}건</div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* ── 네이버 공식 테마 현황 ── */}
      <div style={{
        background: "var(--c-card)", border: "1px solid var(--c-bdr)",
        borderTop: `3px solid ${C.primary}`, borderRadius: 12, padding: 20, marginBottom: 24,
      }}>
        {/* 헤더 + KPI */}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>네이버 공식 테마</div>
            <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 4 }}>
              네이버 금융 공식 테마 — 실시간 수집 (1시간 캐시)
            </div>
          </div>
          <div style={{ display: "flex", gap: 16 }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 28, fontWeight: 800, color: C.primary }}>{fmtNum(total)}</div>
              <div style={{ fontSize: 14, color: "var(--c-text5)" }}>전체</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 28, fontWeight: 800, color: C.success }}>{fmtNum(writtenCount)}</div>
              <div style={{ fontSize: 14, color: "var(--c-text5)" }}>작성 완료</div>
            </div>
            <div style={{ textAlign: "center" }}>
              <div style={{ fontSize: 28, fontWeight: 800, color: C.warn }}>{fmtNum(remaining)}</div>
              <div style={{ fontSize: 14, color: "var(--c-text5)" }}>미작성</div>
            </div>
          </div>
        </div>

        {/* 진행률 바 */}
        {total > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
              <span style={{ fontSize: 14, color: "var(--c-text5)" }}>작성 진행률</span>
              <span style={{ fontSize: 14, fontWeight: 700, color: C.success }}>{pct}%</span>
            </div>
            <div style={{ background: "var(--c-bg)", borderRadius: 4, height: 8 }}>
              <div style={{ height: "100%", width: `${pct}%`, background: C.success, borderRadius: 4, transition: "width 0.4s" }} />
            </div>
          </div>
        )}

        {/* 오늘의 픽 */}
        {todayPick && (
          <div style={{
            background: C.warn + "11", border: `1px solid ${C.warn}44`,
            borderRadius: 10, padding: "14px 18px", marginBottom: 20,
            display: "flex", alignItems: "center", gap: 14,
          }}>
            <span style={{ fontSize: 20 }}>🎯</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, color: C.warn, fontWeight: 600, marginBottom: 2 }}>오늘의 발행 테마</div>
              <div style={{ fontSize: 18, fontWeight: 800, color: "var(--c-text)" }}>{todayPick.theme}</div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontSize: 14, color: "var(--c-text5)" }}>{todayPick.sector}</div>
              <div style={{ fontSize: 14, fontWeight: 700, color: C.success }}>기회 {todayPick.opportunity_score.toFixed(1)}</div>
            </div>
          </div>
        )}

        {/* 테마 전체 그리드 */}
        {themeList.length === 0 ? (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>{themes ? "테마 없음" : "로딩 중…"}</div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 8 }}>
            {themeList.map((t) => (
              <div key={t.no} style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px", borderRadius: 8,
                background: t.written ? C.success + "11" : "var(--c-bg)",
                border: `1px solid ${t.written ? C.success + "44" : "var(--c-bdr)"}`,
              }}>
                <span style={{ fontSize: 14, color: t.written ? C.success : "var(--c-text5)", flexShrink: 0 }}>
                  {t.written ? "✓" : "○"}
                </span>
                <span style={{
                  fontSize: 14, color: t.written ? "var(--c-text)" : "var(--c-text5)",
                  fontWeight: t.written ? 600 : 400,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {t.name}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
