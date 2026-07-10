"use client";

import useSWR from "swr";
import { fetcher, DbStats, DbTable, BackupFile } from "@/lib/api";
import { fmtNum, fmtTime, C } from "@/lib/utils";

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

/* ─── 행 수 바 ─────────────────────────────────────── */
function RowBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ flex: 1, background: "var(--c-bdr)", borderRadius: 3, height: 6, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: C.primary, borderRadius: 3, opacity: 0.8 }} />
      </div>
      <span style={{ fontSize: 14, color: "var(--c-text)", width: 64, textAlign: "right", flexShrink: 0 }}>
        {fmtNum(value)}
      </span>
    </div>
  );
}

/* ─── 메인 페이지 ───────────────────────────────────── */
export default function DbPage() {
  const { data, error, isLoading } = useSWR<DbStats>("/api/db", fetcher, { refreshInterval: 120000 });

  if (isLoading) {
    return (
      <div style={{ color: "var(--c-text5)", fontSize: 16, padding: 40 }}>데이터 로딩 중…</div>
    );
  }
  if (error || !data) {
    return (
      <div style={{ color: C.danger, fontSize: 16, padding: 40 }}>DB 데이터를 불러올 수 없습니다.</div>
    );
  }

  // 행 수 내림차순 정렬
  const sortedTables: DbTable[] = [...(data.tables ?? [])].sort((a, b) => b.rows - a.rows);
  const maxRows = sortedTables[0]?.rows ?? 1;
  const backups: BackupFile[] = data.backup_files ?? [];

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* 제목 */}
      <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--c-text)", marginBottom: 28, marginTop: 0 }}>
        데이터베이스
      </h1>

      {/* KPI 3개 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
        <KpiCard
          label="DB 크기"
          value={`${data.size_mb?.toFixed(2) ?? "—"} MB`}
          color={C.primary}
          sub={data.wal_exists ? "WAL 모드 활성" : undefined}
        />
        <KpiCard
          label="총 행 수"
          value={fmtNum(data.total_rows)}
          color={C.success}
          sub={`${sortedTables.length}개 테이블`}
        />
        <KpiCard
          label="백업 수"
          value={fmtNum(backups.length)}
          color={C.warn}
          sub="보관된 백업 파일"
        />
      </div>

      {/* 테이블별 현황 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px", marginBottom: 28 }}>
        <SectionTitle>
          테이블별 현황{" "}
          <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>행 수 많은 순</span>
        </SectionTitle>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["테이블명", "행 수", "오늘 행 수", "마지막 쓰기"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", padding: "8px 12px",
                    fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                    borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedTables.map((t, i) => (
                <tr key={t.name} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text)", fontFamily: "monospace", whiteSpace: "nowrap" }}>
                    {t.name}
                  </td>
                  <td style={{ padding: "8px 12px", minWidth: 180 }}>
                    <RowBar value={t.rows} max={maxRows} />
                  </td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: t.today_rows > 0 ? C.success : "var(--c-text5)" }}>
                    {t.today_rows > 0 ? `+${fmtNum(t.today_rows)}` : "—"}
                  </td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>
                    {fmtTime(t.last_write)}
                  </td>
                </tr>
              ))}
              {sortedTables.length === 0 && (
                <tr>
                  <td colSpan={4} style={{ padding: "32px", textAlign: "center", color: "var(--c-text5)", fontSize: 14 }}>
                    테이블 데이터 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 백업 목록 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
        <SectionTitle>
          백업 목록{" "}
          <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>{backups.length}개</span>
        </SectionTitle>
        {backups.length > 0 ? (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  {["파일명", "크기", "생성 시각"].map(h => (
                    <th key={h} style={{
                      textAlign: "left", padding: "8px 12px",
                      fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                      borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {backups.map((b, i) => (
                  <tr key={b.name} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text)", fontFamily: "monospace" }}>{b.name}</td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>
                      {b.size_mb != null ? `${b.size_mb.toFixed(2)} MB` : "—"}
                    </td>
                    <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>
                      {fmtTime(b.mtime)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div style={{ color: "var(--c-text5)", fontSize: 14 }}>백업 파일 없음</div>
        )}
      </div>
    </div>
  );
}
