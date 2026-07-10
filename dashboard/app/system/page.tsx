"use client";

import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { ago, fmtNum, C } from "@/lib/utils";

/* ─── 타입 ─────────────────────────────────────────── */
interface DaemonInfo    { alive: boolean; pid: number | null; uptime: string }
interface VisionAgent   { agent_id: string; status: string; last_seen: string; metrics?: Record<string, number> }
interface VisionSummary { total_agents?: number; healthy?: number; degraded?: number; offline?: number }
interface PublishInfo   {
  naver_cookie_ok?: boolean;  naver_cookie_age_h?: number;
  tistory_cookie_ok?: boolean; tistory_cookie_age_h?: number;
}
interface ImageInfo {
  total: number; total_size_mb: number;
  by_type?: Record<string, number>;
  recent?: { name: string; mtime: string; size_kb: number }[];
}
interface Capability    { agent_id: string; intents: string[] }

/* ─── KPI 카드 ─────────────────────────────────────── */
function KpiCard({
  label, value, color = C.primary, sub,
}: { label: string; value: string | number; color?: string; sub?: string }) {
  return (
    <div style={{
      background: "var(--c-card)",
      border: "1px solid var(--c-bdr)",
      borderTop: `3px solid ${color}`,
      borderRadius: 12,
      padding: "24px 20px",
      flex: 1,
      minWidth: 0,
    }}>
      <div style={{ fontSize: 14, color: "var(--c-text2)", marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color }}>{value}</div>
      {sub && <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

/* ─── 섹션 헤더 ─────────────────────────────────────── */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 16 }}>
      {children}
    </div>
  );
}

/* ─── 상태 뱃지 ─────────────────────────────────────── */
function StatusBadge({ status }: { status: string }) {
  const s = status?.toLowerCase() ?? "";
  const color =
    s === "healthy" || s === "ok" || s === "active" ? C.success :
    s === "degraded" || s === "warn"                  ? C.warn   :
    s === "offline"  || s === "error"                 ? C.danger :
    C.muted;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "2px 10px", borderRadius: 20,
      fontSize: 12, fontWeight: 600,
      background: color + "22", color,
    }}>{status}</span>
  );
}

/* ─── 에이전트 카드 ─────────────────────────────────── */
function AgentCard({ agent }: { agent: VisionAgent }) {
  return (
    <div style={{
      background: "var(--c-card)",
      border: "1px solid var(--c-bdr)",
      borderRadius: 12,
      padding: "18px 20px",
    }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: "var(--c-text)", marginBottom: 8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {agent.agent_id}
      </div>
      <StatusBadge status={agent.status} />
      <div style={{ fontSize: 14, color: "var(--c-text5)", marginTop: 10 }}>
        {ago(agent.last_seen)}
      </div>
    </div>
  );
}

/* ─── 발행 인프라 행 ─────────────────────────────────── */
function CookieRow({ platform, ok, ageH }: { platform: string; ok?: boolean; ageH?: number }) {
  const color = ok ? C.success : C.danger;
  const icon  = ok ? "✓" : "✗";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0", borderBottom: "1px solid var(--c-bdr)" }}>
      <span style={{ fontSize: 16, color, fontWeight: 700, width: 20 }}>{icon}</span>
      <span style={{ fontSize: 14, color: "var(--c-text)", fontWeight: 500, width: 100 }}>{platform}</span>
      <span style={{ fontSize: 14, color: ok ? C.success : C.danger }}>
        {ok ? "쿠키 유효" : "쿠키 만료 / 없음"}
      </span>
      {ageH != null && (
        <span style={{ fontSize: 14, color: "var(--c-text5)", marginLeft: "auto" }}>
          {ageH < 1 ? "방금" : `${ageH}시간 전`}
        </span>
      )}
    </div>
  );
}

/* ─── 메인 페이지 ───────────────────────────────────── */
export default function SystemPage() {
  const { data: daemon }       = useSWR<DaemonInfo>    ("/api/daemon",          fetcher, { refreshInterval: 15000 });
  const { data: agents }       = useSWR<VisionAgent[]> ("/api/vision/agents",   fetcher, { refreshInterval: 30000 });
  const { data: summary }      = useSWR<VisionSummary> ("/api/vision/summary",  fetcher, { refreshInterval: 30000 });
  const { data: publish }      = useSWR<PublishInfo>   ("/api/publish",         fetcher, { refreshInterval: 60000 });
  const { data: images }       = useSWR<ImageInfo>     ("/api/images",          fetcher, { refreshInterval: 60000 });
  const { data: capabilities } = useSWR<Capability[]>  ("/api/capabilities",    fetcher, { refreshInterval: 120000 });

  const alive     = daemon?.alive ?? false;
  const agentList = agents ?? [];
  const capList   = capabilities ?? [];

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* 제목 */}
      <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--c-text)", marginBottom: 28, marginTop: 0 }}>
        시스템
      </h1>

      {/* KPI 4개 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
        <KpiCard
          label="데몬 상태"
          value={daemon == null ? "확인 중" : alive ? "실행중" : "중단"}
          color={daemon == null ? C.muted : alive ? C.success : C.danger}
          sub={daemon?.pid ? `PID ${daemon.pid}` : undefined}
        />
        <KpiCard
          label="업타임"
          value={daemon?.uptime ?? "—"}
          color={C.primary}
          sub="데몬 가동 시간"
        />
        <KpiCard
          label="VISION 에이전트"
          value={fmtNum(agentList.length ?? summary?.total_agents)}
          color={C.primary}
          sub={summary ? `정상 ${summary.healthy ?? 0} / 저하 ${summary.degraded ?? 0} / 오프라인 ${summary.offline ?? 0}` : undefined}
        />
        <KpiCard
          label="총 캐퍼빌리티"
          value={fmtNum(capList.length)}
          color={C.muted}
          sub="등록된 기능 수"
        />
      </div>

      {/* 에이전트 그리드 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px", marginBottom: 28 }}>
        <SectionTitle>에이전트 현황</SectionTitle>
        {agentList.length > 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            {agentList.map(a => <AgentCard key={a.agent_id} agent={a} />)}
          </div>
        ) : (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>에이전트 데이터 없음</div>
        )}
      </div>

      {/* 발행 인프라 + 이미지 현황 나란히 */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 28 }}>
        {/* 발행 인프라 */}
        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <SectionTitle>발행 인프라 상태</SectionTitle>
          <CookieRow
            platform="네이버"
            ok={publish?.naver_cookie_ok}
            ageH={publish?.naver_cookie_age_h}
          />
          <CookieRow
            platform="티스토리"
            ok={publish?.tistory_cookie_ok}
            ageH={publish?.tistory_cookie_age_h}
          />
        </div>

        {/* 이미지 현황 */}
        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <SectionTitle>이미지 현황</SectionTitle>
          <div style={{ display: "flex", gap: 24, marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 12, color: "var(--c-text5)" }}>총 이미지</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.primary }}>{fmtNum(images?.total)}</div>
            </div>
            <div>
              <div style={{ fontSize: 12, color: "var(--c-text5)" }}>용량</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.muted }}>
                {images?.total_size_mb != null ? `${images.total_size_mb.toFixed(1)} MB` : "—"}
              </div>
            </div>
          </div>
          {images?.by_type && Object.keys(images.by_type).length > 0 && (
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 16 }}>
              {Object.entries(images.by_type).map(([type, cnt]) => (
                <span key={type} style={{ fontSize: 14, color: "var(--c-text2)" }}>
                  {type}: <span style={{ color: "var(--c-text)", fontWeight: 600 }}>{cnt}</span>
                </span>
              ))}
            </div>
          )}
          {images?.recent && images.recent.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {["파일명", "크기", "생성"].map(h => (
                      <th key={h} style={{ textAlign: "left", padding: "6px 8px", fontSize: 12, color: "var(--c-text5)", fontWeight: 600, borderBottom: "1px solid var(--c-bdr)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {images.recent.slice(0, 8).map(img => (
                    <tr key={img.name}>
                      <td style={{ padding: "6px 8px", fontSize: 12, color: "var(--c-text2)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{img.name}</td>
                      <td style={{ padding: "6px 8px", fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>{img.size_kb} KB</td>
                      <td style={{ padding: "6px 8px", fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>{ago(img.mtime)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* 캐퍼빌리티 목록 */}
      {capList.length > 0 && (
        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <SectionTitle>
            캐퍼빌리티 목록 <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>총 {capList.length}개</span>
          </SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {capList.map(cap => (
              <div key={cap.agent_id} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: C.primary, width: 180, flexShrink: 0 }}>{cap.agent_id}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, flex: 1 }}>
                  {(cap.intents ?? []).map(intent => (
                    <span key={intent} style={{
                      fontSize: 12, padding: "2px 10px", borderRadius: 20,
                      background: "var(--c-bdr)", color: "var(--c-text2)",
                    }}>{intent}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
