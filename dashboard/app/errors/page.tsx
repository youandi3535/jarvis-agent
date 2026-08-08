"use client";

import { useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { severityColor, statusColor, fmtNum, fmtTime, C } from "@/lib/utils";

/* ─── 타입 ─────────────────────────────────────────── */
interface GuardianStats {
  total: number; new: number; fixed: number;
  critical: number; high: number; medium: number; low: number;
}
interface AlltimeData  { total: number }
interface TrendDay     { day: string; total: number; crit: number; high: number; fixed: number }
interface SourceRow    { source: string; total: number; crit: number; fixed: number; new: number }
interface ErrorRow     {
  id: number; timestamp: string; severity: string; status: string;
  error_type: string; error_category?: string; module: string; message: string; source?: string;
}
interface Narrative    {
  no: number; title: string; date: string; slots: Record<string, string[]>;
}
interface RepairRow    {
  key: string; ids: number[]; at: string; fixed_at: string; elapsed: string;
  severity: string; error_type: string; status: string; count: number;
  detected: string; who: string; method: string;
  symptom: string; action: string; files: string[];
  outcome: { state: string; text: string; recur?: number };
  auto: boolean; kind: string; narrative?: Narrative;
}
interface HistoryResp  { items: RepairRow[]; slots: Record<string, string> }

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

/* ─── 뱃지 ─────────────────────────────────────────── */
function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center",
      padding: "2px 10px", borderRadius: 20,
      fontSize: 12, fontWeight: 600,
      background: color + "22", color,
    }}>{label}</span>
  );
}

/* ─── 7일 추이 바 차트 ──────────────────────────────── */
function TrendChart({ trend }: { trend: TrendDay[] }) {
  const max = Math.max(...trend.map(d => d.total), 1);
  return (
    /* 막대 개수는 API 파생(런타임)이라 폭을 보장할 수 없다 —
       자리가 남으면 flex:1 로 채우고, 모자라면 *의도된* 가로 스크롤로 전부 보여준다.
       (자르지 않는다: overflow-x 는 auto 이지 hidden 이 아니다) */
    /* ★ height 가 아니라 minHeight — `overflow-x:auto` 를 주면 CSS 규칙상 `overflow-y` 가
        visible 로 남지 못하고 auto 로 승격된다. 그러면 가로 스크롤바 6px 이 고정 높이 80 을
        갉아먹어 막대 값 라벨 위쪽이 잘리는데, 세로로는 스크롤이 안 생겨(시작 경계 밖은
        스크롤 대상이 아니다) **읽을 방법이 사라진다**. 실측: clientH 74 / 내용 84.
        minHeight 로 두면 스크롤바 자리를 높이가 흡수한다. */
    <div style={{ display: "flex", alignItems: "flex-end", gap: 6, minHeight: 80, marginTop: 12, overflowX: "auto" }}>
      {trend.map(d => (
        <div key={d.day} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
          <div style={{ fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>{d.total}</div>
          <div style={{ width: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end", height: 40 }}>
            <div style={{
              width: "100%",
              height: `${Math.max(4, (d.total / max) * 40)}px`,
              background: d.crit > 0 ? C.danger : C.primary,
              borderRadius: "4px 4px 0 0",
              opacity: 0.85,
            }} />
          </div>
          <div style={{ fontSize: 12, color: "var(--c-text5)", whiteSpace: "nowrap" }}>
            {d.day.slice(5)}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── 에이전트별 바 차트 ────────────────────────────── */
function SourceChart({ sources }: { sources: SourceRow[] }) {
  const max = Math.max(...sources.map(s => s.total), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 12 }}>
      {sources.map(s => (
        <div key={s.source} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 120, fontSize: 14, color: "var(--c-text2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flexShrink: 0 }}>
            {s.source}
          </div>
          <div style={{ flex: 1, background: "var(--c-bdr)", borderRadius: 4, height: 18, overflow: "hidden" }}>
            <div style={{
              width: `${(s.total / max) * 100}%`,
              height: "100%",
              background: s.crit > 0 ? C.danger : C.primary,
              borderRadius: 4,
              opacity: 0.8,
            }} />
          </div>
          <div style={{ width: 40, fontSize: 14, color: "var(--c-text)", textAlign: "right", flexShrink: 0 }}>
            {s.total}
          </div>
          <div style={{ width: 32, fontSize: 12, color: C.success, textAlign: "right", flexShrink: 0 }}>
            {s.fixed > 0 ? `+${s.fixed}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ─── 수리 이력 ─────────────────────────────────────── */
const OUTCOME_STYLE: Record<string, { color: string; icon: string }> = {
  ok:    { color: C.success, icon: "✅" },
  watch: { color: C.warn,    icon: "👀" },
  recur: { color: C.danger,  icon: "🔁" },
  fail:  { color: C.danger,  icon: "↩️" },
  open:  { color: C.warn,    icon: "⏳" },
  "n/a": { color: C.muted,   icon: "—" },
};

/** 6하 한 줄 — 라벨 폭을 맞춰 눈이 세로로 흐르게 한다 */
function Line({ n, label, children, color }: {
  n: string; label: string; children: React.ReactNode; color?: string;
}) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "baseline", marginTop: 6 }}>
      <span style={{ fontSize: 14, color: "var(--c-text5)", flexShrink: 0 }}>{n}</span>
      <span style={{ fontSize: 14, color: "var(--c-text5)", width: 92, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 14, color: color ?? "var(--c-text)", flex: 1, minWidth: 0, wordBreak: "break-word" }}>
        {children}
      </span>
    </div>
  );
}

function RepairCard({ r, slots }: { r: RepairRow; slots: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const oc = OUTCOME_STYLE[r.outcome?.state] ?? OUTCOME_STYLE["n/a"];
  const nar = r.narrative;
  return (
    <div style={{
      border: "1px solid var(--c-bdr)", borderLeft: `3px solid ${oc.color}`,
      borderRadius: 10, padding: "16px 18px", marginBottom: 12, background: "var(--c-card)",
    }}>
      {/* 머리 */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <Badge label={r.severity} color={severityColor(r.severity)} />
        <span style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)" }}>{r.error_type}</span>
        {r.kind === "change" && (
          <span style={{ fontSize: 14, color: "var(--c-text5)" }}>변경 기록</span>
        )}
        {r.count > 1 && (
          <span style={{ fontSize: 14, color: "var(--c-text5)" }}>파일 {r.count}개</span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 14, color: "var(--c-text5)", whiteSpace: "nowrap" }}>
          {fmtTime(r.at)} 발생{r.elapsed ? ` → ${r.elapsed} 만에 수리` : ""}
        </span>
      </div>

      {/* 6하 */}
      <Line n="①" label="어떻게 잡았나">{r.detected}</Line>
      <Line n="②" label="무슨 증상">{r.symptom || "—"}</Line>
      <Line n="③" label="누가 고쳤나">
        <strong style={{ fontWeight: 600 }}>{r.who}</strong>
        {r.method ? <span style={{ color: "var(--c-text2)" }}> · {r.method}</span> : null}
      </Line>
      <Line n="④" label="어떻게 조치">{r.action || "—"}</Line>
      <Line n="⑤" label="지금 어떤가" color={oc.color}>{oc.icon} {r.outcome?.text}</Line>

      {/* 꼬리 */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 10, flexWrap: "wrap" }}>
        {r.files.slice(0, 4).map(f => (
          <code key={f} style={{
            fontSize: 14, color: "var(--c-text2)", background: "var(--c-bdr)",
            padding: "2px 8px", borderRadius: 6,
          }}>{f}</code>
        ))}
        <span style={{ flex: 1 }} />
        {nar && (
          <button
            onClick={() => setOpen(o => !o)}
            style={{
              fontSize: 14, color: C.primary, background: "transparent",
              border: "1px solid var(--c-bdr)", borderRadius: 6,
              padding: "4px 12px", cursor: "pointer",
            }}
          >
            {open ? "접기" : `자세히 — 기록 [${nar.no}]`}
          </button>
        )}
      </div>

      {/* 서술 (ERRORS.md 원문) */}
      {open && nar && (
        <div style={{
          marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--c-bdr)",
        }}>
          <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 10 }}>
            ERRORS.md [{nar.no}] · {nar.date} — 사람이 남긴 기록
          </div>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 12 }}>
            {nar.title}
          </div>
          {Object.entries(slots).map(([k, label]) => {
            const vals = nar.slots?.[k];
            if (!vals || vals.length === 0) return null;
            return (
              <div key={k} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 14, color: C.primary, marginBottom: 4 }}>{label}</div>
                {vals.map((v, i) => (
                  <div key={i} style={{ fontSize: 14, color: "var(--c-text2)", lineHeight: 1.7, marginBottom: 2 }}>
                    {v}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const HISTORY_TABS: { key: string; label: string }[] = [
  { key: "",       label: "전체" },
  { key: "auto",   label: "자동 수리" },
  { key: "manual", label: "사람 수리" },
];

function RepairHistory() {
  const [actor, setActor] = useState("");
  const [repairOnly, setRepairOnly] = useState(true);
  const { data } = useSWR<HistoryResp>(
    `/api/guardian/history?days=30&limit=60&actor=${actor}`,
    fetcher, { refreshInterval: 60000 },
  );
  const slots = data?.slots ?? {};
  const all = data?.items ?? [];
  const items = repairOnly ? all.filter(r => r.kind === "repair") : all;

  return (
    <div style={{
      background: "var(--c-card)", border: "1px solid var(--c-bdr)",
      borderRadius: 12, padding: "20px 24px", marginBottom: 28,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 4 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)" }}>수리 이력</div>
        <span style={{ fontSize: 14, color: "var(--c-text5)" }}>최근 30일 · {items.length}건</span>
        <span style={{ flex: 1 }} />
        {HISTORY_TABS.map(t => (
          <button key={t.key} onClick={() => setActor(t.key)} style={{
            fontSize: 14, padding: "4px 12px", borderRadius: 6, cursor: "pointer",
            border: `1px solid ${actor === t.key ? C.primary : "var(--c-bdr)"}`,
            background: actor === t.key ? C.primary + "22" : "transparent",
            color: actor === t.key ? C.primary : "var(--c-text2)",
          }}>{t.label}</button>
        ))}
        <button onClick={() => setRepairOnly(v => !v)} style={{
          fontSize: 14, padding: "4px 12px", borderRadius: 6, cursor: "pointer",
          border: `1px solid ${repairOnly ? "var(--c-bdr)" : C.primary}`,
          background: repairOnly ? "transparent" : C.primary + "22",
          color: repairOnly ? "var(--c-text2)" : C.primary,
        }}>{repairOnly ? "변경 기록 보기" : "변경 기록 숨기기"}</button>
      </div>
      <div style={{ fontSize: 14, color: "var(--c-text5)", marginBottom: 16 }}>
        어떤 오류를 누가 어떻게 잡아 고쳤고, 그 뒤 무엇이 정상으로 바뀌었나 —
        ⑤는 저장값이 아니라 <b>수정 후 같은 증상이 다시 났는지</b>로 판정합니다.
      </div>
      {items.length === 0
        ? <div style={{ color: "var(--c-text5)", fontSize: 14, padding: "24px 0" }}>
            {data ? "해당 조건의 이력 없음" : "불러오는 중…"}
          </div>
        : items.map(r => <RepairCard key={r.key} r={r} slots={slots} />)
      }
    </div>
  );
}

/* ─── 표 셀 — 긴 값을 *자르지 않고* 줄바꿈으로 흡수 ──────
   ch = 글자수 기준 상대 폭(font-size 파생). 고정 px 폭을 박지 않는다.
   overflow-wrap:anywhere 는 min-content 폭까지 줄여줘 auto 테이블이
   컨테이너 안으로 접힌다 (break-word 는 min-content 를 못 줄인다). */
const CELL_CH = { module: 18, type: 22, message: 40 } as const;
const wrapCell = (ch: number): React.CSSProperties => ({
  maxWidth: `${ch}ch`,
  minWidth: `${Math.round(ch * 0.5)}ch`,   // 상한에서 파생 — 칸이 한 글자 폭으로 짜부라지지 않게
  whiteSpace: "normal",
  overflowWrap: "break-word",
  wordBreak: "break-word",
});

/* ─── 메인 페이지 ───────────────────────────────────── */
export default function ErrorsPage() {
  const { data: stats }   = useSWR<GuardianStats>("/api/guardian/stats",   fetcher, { refreshInterval: 30000 });
  const { data: alltime } = useSWR<AlltimeData>  ("/api/guardian/alltime", fetcher, { refreshInterval: 60000 });
  const { data: trend }   = useSWR<TrendDay[]>   ("/api/guardian/trend",   fetcher, { refreshInterval: 60000 });
  const { data: sources } = useSWR<SourceRow[]>  ("/api/guardian/sources", fetcher, { refreshInterval: 60000 });
  const { data: errors }  = useSWR<ErrorRow[]>   ("/api/errors",           fetcher, { refreshInterval: 30000 });

  const critHigh = (stats?.critical ?? 0) + (stats?.high ?? 0);
  const latest   = (errors ?? []).slice(0, 30);

  return (
    <div style={{ maxWidth: 1200 }}>
      {/* 제목 */}
      <h1 style={{ fontSize: 28, fontWeight: 700, color: "var(--c-text)", marginBottom: 28, marginTop: 0 }}>
        오류 관리
      </h1>

      {/* KPI 4개 */}
      <div style={{ display: "flex", gap: 16, marginBottom: 28, flexWrap: "wrap" }}>
        <KpiCard label="미해결"       value={fmtNum(stats?.new)}     color={C.danger}  sub="해결 필요" />
        <KpiCard label="CRITICAL+HIGH" value={fmtNum(critHigh)}       color={critHigh > 0 ? C.danger : C.warn} sub={`CRITICAL ${stats?.critical ?? 0} / HIGH ${stats?.high ?? 0}`} />
        <KpiCard label="7일 자동수정"  value={fmtNum(stats?.fixed)}   color={C.success} sub="자동 수정 완료" />
        <KpiCard label="전체 누적"     value={fmtNum(alltime?.total)} color={C.primary} sub="총 오류 기록" />
      </div>

      {/* 7일 추이 + 에이전트별 나란히 —
          1fr 은 minmax(auto,1fr) 이라 트랙이 내용보다 작아지지 못한다 → minmax(0,1fr) */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 16, marginBottom: 28 }}>
        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 4 }}>7일 추이</div>
          <div style={{ fontSize: 14, color: "var(--c-text5)" }}>빨강=CRITICAL 포함, 파랑=일반</div>
          {trend && trend.length > 0
            ? <TrendChart trend={trend} />
            : <div style={{ color: "var(--c-text5)", fontSize: 14, marginTop: 20 }}>데이터 없음</div>
          }
        </div>

        <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 4 }}>에이전트별 오류</div>
          <div style={{ fontSize: 14, color: "var(--c-text5)" }}>초록 숫자 = 자동 수정</div>
          {sources && sources.length > 0
            ? <SourceChart sources={sources} />
            : <div style={{ color: "var(--c-text5)", fontSize: 14, marginTop: 20 }}>데이터 없음</div>
          }
        </div>
      </div>

      {/* 수리 이력 — 무엇이 어떻게 고쳐져 어떻게 바뀌었나 */}
      <RepairHistory />

      {/* 오류 목록 테이블 */}
      <div style={{ background: "var(--c-card)", border: "1px solid var(--c-bdr)", borderRadius: 12, padding: "20px 24px" }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: "var(--c-text)", marginBottom: 16 }}>
          오류 목록 <span style={{ fontSize: 14, color: "var(--c-text5)", fontWeight: 400 }}>최신 30건</span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["ID", "시각", "에이전트", "모듈", "타입", "심각도", "상태", "메시지"].map(h => (
                  <th key={h} style={{
                    textAlign: "left", padding: "8px 12px",
                    fontSize: 12, color: "var(--c-text5)", fontWeight: 600,
                    borderBottom: "1px solid var(--c-bdr)", whiteSpace: "nowrap",
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {latest.map((e, i) => (
                <tr key={e.id} style={{ background: i % 2 === 0 ? "transparent" : "rgba(255,255,255,0.02)" }}>
                  <td style={{ padding: "8px 12px", fontSize: 12, color: "var(--c-text5)" }}>{e.id}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", whiteSpace: "nowrap" }}>{fmtTime(e.timestamp)}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", overflowWrap: "break-word" }}>{e.source ?? "—"}</td>
                  <td style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", ...wrapCell(CELL_CH.module) }}>{e.module ?? "—"}</td>
                  <td
                    title={e.error_category ? `${e.error_category}(${e.error_type})` : e.error_type}
                    style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text)", ...wrapCell(CELL_CH.type) }}
                  >
                    {e.error_category ? `${e.error_category}(${e.error_type})` : e.error_type}
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <Badge label={e.severity} color={severityColor(e.severity)} />
                  </td>
                  <td style={{ padding: "8px 12px" }}>
                    <Badge label={e.status} color={statusColor(e.status)} />
                  </td>
                  <td title={e.message} style={{ padding: "8px 12px", fontSize: 14, color: "var(--c-text2)", ...wrapCell(CELL_CH.message) }}>
                    {e.message?.slice(0, 120)}
                  </td>
                </tr>
              ))}
              {latest.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ padding: "32px", textAlign: "center", color: "var(--c-text5)", fontSize: 14 }}>
                    오류 기록 없음
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
