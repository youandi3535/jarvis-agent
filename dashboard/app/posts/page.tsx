"use client";
import useSWR from "swr";
import { fetcher, PostStats } from "@/lib/api";
import { C, fmtNum, fmtTime } from "@/lib/utils";

// ── 인라인 타입 ──────────────────────────────────────────────────

type PipelineItem = {
  theme: string;
  status: string;
  created_at: string;
};

type PipelineData = {
  today: Record<string, number>;
  recent: PipelineItem[];
};

type PlatformStatus = {
  cookie_ok: boolean;
  cookie_age_hours?: number;
  posts_7d: number;
};

type PublishStatus = {
  naver: PlatformStatus;
  tistory: PlatformStatus;
};

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

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex",
      alignItems: "center",
      padding: "3px 10px",
      borderRadius: 20,
      fontSize: 14,
      fontWeight: 600,
      background: color + "22",
      color,
    }}>
      {text}
    </span>
  );
}

function pipelineStatusColor(status: string): string {
  const s = status?.toLowerCase() ?? "";
  if (s === "done" || s === "success") return C.success;
  if (s === "error" || s === "failed") return C.danger;
  if (s === "running") return C.warn;
  return C.muted; // pending, queued, etc.
}

function cookieAgeLabel(hours?: number): string {
  if (hours == null) return "—";
  if (hours < 1) return "방금 갱신";
  if (hours < 24) return `${Math.round(hours)}시간 전`;
  return `${Math.round(hours / 24)}일 전`;
}

// ── 페이지 ──────────────────────────────────────────────────────

export default function PostsPage() {
  const { data: posts } = useSWR<PostStats>(
    "/api/posts", fetcher, { refreshInterval: 30000 }
  );
  const { data: pipeline } = useSWR<PipelineData>(
    "/api/pipeline", fetcher, { refreshInterval: 30000 }
  );
  const { data: publish } = useSWR<PublishStatus>(
    "/api/publish", fetcher, { refreshInterval: 60000 }
  );

  const todayByStatus = pipeline?.today ?? {};
  const statusOrder = ["done", "running", "pending", "error"] as const;
  const recentPipeline = pipeline?.recent?.slice(0, 10) ?? [];

  const naver = publish?.naver;
  const tistory = publish?.tistory;

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

      {/* ── KPI 3개 ── */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28 }}>
        <KpiCard
          label="오늘 발행"
          value={fmtNum(posts?.today)}
          sub="오늘"
          color={C.primary}
        />
        <KpiCard
          label="이번 주"
          value={fmtNum(posts?.week)}
          sub="7일"
          color={C.success}
        />
        <KpiCard
          label="이번 달"
          value={fmtNum(posts?.month)}
          sub="30일"
          color={C.warn}
        />
      </div>

      {/* ── 플랫폼 상태 카드 2개 ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
        {/* 네이버 */}
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${naver?.cookie_ok ? C.success : C.danger}`,
          borderRadius: 12, padding: 20,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>네이버 블로그</div>
            <Badge
              text={naver?.cookie_ok ? "쿠키 정상" : "쿠키 만료"}
              color={naver?.cookie_ok ? C.success : C.danger}
            />
          </div>
          <div style={{ display: "flex", gap: 20 }}>
            <div>
              <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>쿠키 갱신</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>
                {cookieAgeLabel(naver?.cookie_age_hours)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>7일 발행</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.primary }}>
                {fmtNum(naver?.posts_7d)}건
              </div>
            </div>
          </div>
        </div>

        {/* 티스토리 */}
        <div style={{
          background: "var(--c-card)", border: "1px solid var(--c-bdr)",
          borderTop: `3px solid ${tistory?.cookie_ok ? C.success : C.danger}`,
          borderRadius: 12, padding: 20,
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>티스토리</div>
            <Badge
              text={tistory?.cookie_ok ? "쿠키 정상" : "쿠키 만료"}
              color={tistory?.cookie_ok ? C.success : C.danger}
            />
          </div>
          <div style={{ display: "flex", gap: 20 }}>
            <div>
              <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>쿠키 갱신</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--c-text)" }}>
                {cookieAgeLabel(tistory?.cookie_age_hours)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 4 }}>7일 발행</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: C.primary }}>
                {fmtNum(tistory?.posts_7d)}건
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── 파이프라인 현황 ── */}
      <div style={{
        background: "var(--c-card)", border: "1px solid var(--c-bdr)",
        borderTop: `3px solid ${C.primary}`, borderRadius: 12, padding: 20,
        marginBottom: 24,
      }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16, color: "var(--c-text)" }}>
          오늘 파이프라인 현황
        </div>

        {Object.keys(todayByStatus).length === 0 ? (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>
            {pipeline ? "파이프라인 없음" : "로딩 중…"}
          </div>
        ) : (
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {/* 정의된 순서대로 먼저, 나머지는 뒤에 */}
            {[
              ...statusOrder.filter((s) => s in todayByStatus),
              ...Object.keys(todayByStatus).filter(
                (s) => !(statusOrder as readonly string[]).includes(s)
              ),
            ].map((status) => {
              const cnt = todayByStatus[status] ?? 0;
              const color = pipelineStatusColor(status);
              return (
                <div key={status} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  background: "var(--c-bg)", borderRadius: 10,
                  padding: "12px 18px", border: "1px solid var(--c-bdr)",
                }}>
                  <Badge text={status} color={color} />
                  <span style={{ fontSize: 24, fontWeight: 800, color }}>{cnt}</span>
                  <span style={{ fontSize: 14, color: "var(--c-text5)" }}>건</span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── 최근 파이프라인 목록 ── */}
      <div style={{
        background: "var(--c-card)", border: "1px solid var(--c-bdr)",
        borderTop: `3px solid ${C.success}`, borderRadius: 12, padding: 20,
      }}>
        <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 16, color: "var(--c-text)" }}>
          최근 파이프라인
        </div>

        {recentPipeline.length === 0 ? (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>
            {pipeline ? "최근 파이프라인 없음" : "로딩 중…"}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ color: "var(--c-text5)", textAlign: "left" }}>
                <th style={{ paddingBottom: 10, fontWeight: 600 }}>테마</th>
                <th style={{ paddingBottom: 10, fontWeight: 600 }}>상태</th>
                <th style={{ paddingBottom: 10, fontWeight: 600, textAlign: "right" }}>시각</th>
              </tr>
            </thead>
            <tbody>
              {recentPipeline.map((item, i) => {
                const color = pipelineStatusColor(item.status);
                return (
                  <tr key={i} style={{ borderTop: "1px solid var(--c-bdr)" }}>
                    <td style={{
                      padding: "9px 16px 9px 0", color: "var(--c-text)",
                      maxWidth: 400, overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>
                      {item.theme}
                    </td>
                    <td style={{ padding: "9px 16px 9px 0" }}>
                      <Badge text={item.status} color={color} />
                    </td>
                    <td style={{ padding: "9px 0", color: "var(--c-text5)", textAlign: "right", whiteSpace: "nowrap" }}>
                      {fmtTime(item.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
