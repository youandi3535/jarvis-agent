"use client";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { fetcher, OverviewData, VisionAgent, PipelineEdge, GraphData, AgentDef } from "@/lib/api";
import { C, fmtNum, fmtTime, severityColor } from "@/lib/utils";

// ═══════════════════════════════════════════════════════
// 카드 크기 상수 — pipeline_graph.py LAYOUT 과 동기화
// 위치(x/y)는 /api/graph 에서 수신 → 하드코딩 금지
// ═══════════════════════════════════════════════════════
const CARD_W = 158, CARD_H = 170;
const BIG_W  = 210, BIG_H  = 215;

// 에이전트 경계 박스 — API 에서 받은 AgentDef 로 자동 계산
type AgentBounds = { cx:number; cy:number; top:number; bot:number; left:number; right:number };
function mkBounds(x:number, y:number, w=CARD_W, h=CARD_H): AgentBounds {
  return { cx:x+w/2, cy:y+h/2, top:y, bot:y+h, left:x, right:x+w };
}

// 렌더용 엣지 타입 (자동 라우팅 결과)
type ComputedEdge = {
  id: string; path: string; col: string; dur: number; dots: number; wt?: number;
  lbl?: { x: number; y: number; text: string };
};
// ─────────────────────────────────────────────────────
// 자동 라우팅 — 에이전트 위치는 /api/graph 에서 수신
// ─────────────────────────────────────────────────────
function buildBounds(agents: AgentDef[]): Record<string, AgentBounds> {
  return Object.fromEntries(
    agents.map(a => [a.id, mkBounds(a.x, a.y, a.big ? BIG_W : CARD_W, a.big ? BIG_H : CARD_H)])
  );
}

// 같은 행 임계값 — J01(BIG, h=215)과 J00/J04(h=170)의 중심y 차이 22.5px를 수용
const SAME_ROW_THR = 60;
const SAME_COL_THR = 20;

function routeEdge(e: PipelineEdge, bnd: Record<string, AgentBounds>): string {
  const f = bnd[e.from], t = bnd[e.to];
  if (!f || !t) return "";
  const dx = e.dx ?? 0;
  if (e.route === "via_lane" && e.lane_y != null) {
    const ly = e.lane_y;
    return `M${f.cx},${ly < f.cy ? f.top : f.bot} V${ly} H${t.cx} V${ly < t.cy ? t.top : t.bot}`;
  }
  if (Math.abs(f.cy - t.cy) < SAME_ROW_THR) {  // 같은 행 → 수평
    const [fx, tx] = f.cx < t.cx ? [f.right, t.left] : [f.left, t.right];
    return `M${fx},${(f.cy + t.cy) / 2} H${tx}`;
  }
  if (Math.abs(f.cx - t.cx) < SAME_COL_THR) {  // 같은 열 → 수직
    const [fy, ty] = f.cy < t.cy ? [f.bot, t.top] : [f.top, t.bot];
    return `M${f.cx + dx},${fy} V${ty}`;
  }
  const fy = f.cy < t.cy ? f.bot : f.top;       // L-shape fallback
  return `M${f.cx},${fy} V${f.cy < t.cy ? t.top : t.bot}`;
}

function lblPos(e: PipelineEdge, bnd: Record<string, AgentBounds>): { x:number; y:number } | null {
  if (!e.label) return null;
  const f = bnd[e.from], t = bnd[e.to];
  if (!f || !t) return null;
  if (e.route === "via_lane" && e.lane_y != null)
    return { x: (f.cx + t.cx) / 2, y: e.lane_y - 11 };
  if (Math.abs(f.cy - t.cy) < SAME_ROW_THR) {
    const [fx, tx] = f.cx < t.cx ? [f.right, t.left] : [f.left, t.right];
    return { x: (fx + tx) / 2, y: (f.cy + t.cy) / 2 - 11 };
  }
  if (Math.abs(f.cx - t.cx) < SAME_COL_THR)
    return { x: f.cx + (e.dx ?? 0) + 5, y: (f.cy + t.cy) / 2 - 5 };
  return { x: (f.cx + t.cx) / 2, y: (f.cy + t.cy) / 2 };
}

function resolveEdges(raw: PipelineEdge[], bnd: Record<string, AgentBounds>): ComputedEdge[] {
  return raw.map(e => {
    const pos = lblPos(e, bnd);
    return {
      id: e.id, path: routeEdge(e, bnd), col: e.col,
      dur: e.dur, dots: e.dots, wt: e.wt,
      lbl: (e.label && pos) ? { ...pos, text: e.label } : undefined,
    };
  });
}

// ═══════════════════════════════════════════════
// 로봇 SVG — 3D 책상·컴퓨터 작업
// 책상: 배경과 구분되는 명도로 조정
// ═══════════════════════════════════════════════
function mkRobot(color: string, uid: string, size = 50): string {
  const h = Math.round(size * 80 / 72);
  return `<svg width="${size}" height="${h}" viewBox="0 0 72 80" fill="none" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="gLR${uid}" x1="0%" y1="0%" x2="100%" y2="0%">
    <stop offset="0%" stop-color="${color}"/>
    <stop offset="100%" stop-color="${color}" stop-opacity="0.42"/>
  </linearGradient>
  <radialGradient id="gHL${uid}" cx="28%" cy="22%" r="65%">
    <stop offset="0%" stop-color="white" stop-opacity="0.28"/>
    <stop offset="100%" stop-color="white" stop-opacity="0"/>
  </radialGradient>
  <filter id="fw${uid}" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="1.8" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="fs${uid}" x="-15%" y="-15%" width="130%" height="130%">
    <feDropShadow dx="2.5" dy="2.5" stdDeviation="1.5" flood-color="${color}" flood-opacity="0.2"/>
  </filter>
</defs>
<!-- ══ 바닥 그림자 ══ -->
<ellipse cx="36" cy="78" rx="24" ry="3" fill="black" opacity="0.32"/>
<!-- ══ 책상 3D (배경과 구분되는 청회색) ══ -->
<rect x="16" y="44" width="4" height="17" rx="2" fill="#1e3252"/>
<rect x="52" y="44" width="4" height="17" rx="2" fill="#1e3252"/>
<rect x="9"  y="49" width="5" height="22" rx="2.5" fill="#243c5c"/>
<rect x="58" y="49" width="5" height="22" rx="2.5" fill="#243c5c"/>
<path d="M7 44 L65 44 L65 49 L7 49 Z" fill="#152d4a"/>
<path d="M11 39 L61 39 L65 44 L7 44 Z" fill="#1e3d65"/>
<path d="M11 39 L61 39 L61 40.5 L11 40.5 Z" fill="${color}" opacity="0.28"/>
<!-- ══ 책상 표면 하이라이트 ══ -->
<path d="M12 39.5 L60 39.5 L60 40 L12 40 Z" fill="white" opacity="0.07"/>
<!-- ══ 모니터 3D ══ -->
<path d="M48 4 L53 7 L53 31 L48 28 Z" fill="${color}" opacity="0.18"/>
<path d="M14 28 L48 28 L53 31 L19 31 Z" fill="${color}" opacity="0.12"/>
<rect x="14" y="4" width="34" height="26" rx="2.5" fill="#050810" stroke="${color}" stroke-width="1.5" filter="url(#fs${uid})"/>
<rect x="16" y="6" width="30" height="22" rx="1.5" fill="#060a18"/>
<path d="M17 7 L30 7 L27 11 L16 11 Z" fill="white" opacity="0.05"/>
<rect x="19" y="10" width="15" height="1.8" rx="0.7" fill="${color}" opacity="0.6"><animate attributeName="opacity" values="0.2;0.75;0.2" dur="1.8s" repeatCount="indefinite"/></rect>
<rect x="19" y="14" width="23" height="1.8" rx="0.7" fill="${color}" opacity="0.35"/>
<rect x="19" y="18" width="17" height="1.8" rx="0.7" fill="#4ade80" opacity="0.55"/>
<rect x="19" y="22" width="6" height="1.8" rx="0.7" fill="${color}" opacity="0.4"><animate attributeName="width" values="3;20;3" dur="2.5s" repeatCount="indefinite"/></rect>
<circle cx="31" cy="27.2" r="0.9" fill="${color}" opacity="0.7"/>
<!-- ══ 스탠드 3D ══ -->
<path d="M36 31 L39 33 L39 37 L36 35 Z" fill="${color}" opacity="0.18"/>
<rect x="32" y="31" width="4" height="6" rx="1" fill="${color}" opacity="0.22"/>
<path d="M26 37 L47 37 L50 39.5 L23 39.5 Z" fill="${color}" opacity="0.15"/>
<rect x="26" y="35" width="21" height="4" rx="1.5" fill="${color}" opacity="0.22"/>
<!-- ══ 키보드 3D ══ -->
<path d="M16 37.5 L56 37.5 L56 39.5 L16 39.5 Z" fill="#0e1c30"/>
<path d="M18 34 L54 34 L56 37.5 L16 37.5 Z" fill="#112236" stroke="${color}" stroke-width="0.7" stroke-opacity="0.3"/>
<rect x="20" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.25"/>
<rect x="25" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.25"/>
<rect x="30" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.25"/>
<rect x="35" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.25"/>
<rect x="40" y="34.5" width="9" height="2" rx="0.4" fill="${color}" opacity="0.32"><animate attributeName="opacity" values="0.1;0.55;0.1" dur="0.4s" repeatCount="indefinite"/></rect>
<!-- ══ 안테나 ══ -->
<rect x="33.5" y="20" width="2" height="9" rx="1" fill="${color}" opacity="0.8"/>
<circle cx="34.5" cy="20" r="3.2" fill="${color}" filter="url(#fw${uid})">
  <animate attributeName="r" values="2.2;3.8;2.2" dur="1.9s" repeatCount="indefinite"/>
  <animate attributeName="opacity" values="0.35;1;0.35" dur="1.9s" repeatCount="indefinite"/>
</circle>
<!-- ══ 머리 3D ══ -->
<path d="M44 27 L48 30 L48 40 L44 37 Z" fill="${color}" opacity="0.27"/>
<path d="M28 37 L44 37 L48 40 L32 40 Z" fill="${color}" opacity="0.18"/>
<rect x="28" y="27" width="16" height="12" rx="3.5" fill="url(#gLR${uid})" filter="url(#fs${uid})"/>
<rect x="28" y="27" width="16" height="12" rx="3.5" fill="url(#gHL${uid})"/>
<rect x="28" y="27" width="16" height="3" rx="1.5" fill="white" opacity="0.08"/>
<circle cx="32" cy="32" r="3.3" fill="white" opacity="0.92"/>
<circle cx="32" cy="32.3" r="2" fill="#030509"/>
<circle cx="31.2" cy="30.9" r="0.75" fill="white"/>
<circle cx="37.5" cy="32" r="3.3" fill="white" opacity="0.92"/>
<circle cx="37.5" cy="32.3" r="2" fill="#030509"/>
<circle cx="36.7" cy="30.9" r="0.75" fill="white"/>
<path d="M31 36.5 Q35.5 38.5 40 36.5" stroke="${color}" stroke-width="1.2" fill="none" opacity="0.6"/>
<!-- ══ 몸통 3D ══ -->
<path d="M44 41 L48 44 L48 55 L44 52 Z" fill="${color}" opacity="0.25"/>
<path d="M28 52 L44 52 L48 55 L32 55 Z" fill="${color}" opacity="0.15"/>
<rect x="28" y="41" width="16" height="13" rx="3" fill="url(#gLR${uid})" opacity="0.92" filter="url(#fs${uid})"/>
<rect x="28" y="41" width="16" height="13" rx="3" fill="url(#gHL${uid})"/>
<rect x="30" y="42" width="12" height="2.5" rx="1.2" fill="white" opacity="0.07"/>
<rect x="29.5" y="46.5" width="13" height="6" rx="1.8" fill="rgba(0,0,0,0.45)"/>
<circle cx="33" cy="49.5" r="1.9" fill="${color}" filter="url(#fw${uid})"><animate attributeName="opacity" values="0.25;1;0.25" dur="1.7s" repeatCount="indefinite"/></circle>
<circle cx="36.5" cy="49.5" r="1.9" fill="#4ade80" filter="url(#fw${uid})"><animate attributeName="opacity" values="0.55;1;0.55" dur="1.05s" repeatCount="indefinite"/></circle>
<circle cx="40" cy="49.5" r="1.9" fill="${color}" filter="url(#fw${uid})"><animate attributeName="opacity" values="0.15;0.75;0.15" dur="2.2s" repeatCount="indefinite"/></circle>
<!-- ══ 팔 3D ══ -->
<path d="M18 42.5 L28 44.5 L28 48 L17 47 Z" fill="url(#gLR${uid})" opacity="0.6"/>
<path d="M17 47 L28 48 L28 49.5 L16 48.5 Z" fill="${color}" opacity="0.22"/>
<path d="M44 44.5 L54 42.5 L55 47 L44 48 Z" fill="url(#gLR${uid})" opacity="0.6"/>
<path d="M44 48 L55 47 L56 48.5 L44 49.5 Z" fill="${color}" opacity="0.22"/>
<!-- ══ 의자 3D ══ -->
<path d="M44 56 L48 58.5 L48 65 L44 62.5 Z" fill="${color}" opacity="0.2"/>
<rect x="26" y="56" width="18" height="9" rx="2.5" fill="url(#gLR${uid})" opacity="0.52"/>
<rect x="26" y="56" width="18" height="9" rx="2.5" fill="url(#gHL${uid})"/>
<rect x="29" y="65" width="3.5" height="8" rx="1.75" fill="#1a2d45"/>
<rect x="39.5" y="65" width="3.5" height="8" rx="1.75" fill="#1a2d45"/>
<rect x="33" y="63" width="2.5" height="6" rx="1.25" fill="#121f35"/>
<rect x="36.5" y="63" width="2.5" height="6" rx="1.25" fill="#121f35"/>
<!-- ══ 커피잔 (책상 위) ══ -->
<rect x="57" y="37" width="5" height="4" rx="1" fill="#2a1608" stroke="${color}" stroke-width="0.5" stroke-opacity="0.4"/>
<path d="M57.5 38 Q59.5 37.2 61.5 38" stroke="${color}" stroke-width="0.6" fill="none" opacity="0.5"/>
</svg>`;
}

// ═══════════════════════════════════════════════
// 에이전트 카드 (AGT 좌표는 레거시 참조용 — 엣지 라우팅은 path 문자열로 직접)
// ═══════════════════════════════════════════════
function AgentCard({
  num, label, sub, color, stat, big = false, isActive = false,
}: { num:string; label:string; sub:string; color:string; stat?:string; big?:boolean; isActive?:boolean }) {
  const [hov, setHov] = useState(false);
  const w = big ? BIG_W : CARD_W, h = big ? BIG_H : CARD_H;
  const rSz = big ? 62 : 50;

  return (
    <div style={{ position:"relative", width:w, height:h }}>
      {/* 활성 맥동 링 — 카드 외부로 방사 */}
      {isActive && [0, 0.55, 1.1].map((delay, i) => (
        <div key={i} style={{
          position:"absolute", inset:"-8px",
          border:`2px solid ${color}`,
          borderRadius:16,
          animation:`agent-ring-expand 1.65s ease-out ${delay}s infinite`,
          pointerEvents:"none",
        }}/>
      ))}

      <div
        onMouseEnter={() => setHov(true)}
        onMouseLeave={() => setHov(false)}
        style={{
          width:w, height:h, position:"relative", overflow:"hidden",
          background: isActive
            ? `linear-gradient(145deg,#1c2140 0%,#0e1020 100%)`
            : `linear-gradient(145deg,#131827 0%,#0b0d1a 100%)`,
          border:`2px solid ${color}`,
          borderRadius:12,
          padding:"7px 9px",
          transform: hov
            ? `perspective(450px) rotateX(-7deg) rotateY(5deg) scale(1.05) translateZ(8px)`
            : `perspective(450px) rotateX(0) rotateY(0) scale(1)`,
          transition:"transform 0.26s cubic-bezier(0.23,1,0.32,1), box-shadow 0.3s ease",
          boxShadow: hov
            ? `0 0 36px ${color}66, 0 22px 55px rgba(0,0,0,0.65), inset 0 1px 0 ${color}55`
            : isActive
              ? `0 0 60px ${color}cc, 0 0 120px ${color}55, 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 ${color}99`
              : `0 0 16px ${color}44, 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 ${color}28`,
          cursor:"default",
        }}
      >
        {/* 스위핑 스캔라인 (active only) */}
        {isActive && (
          <div style={{
            position:"absolute", left:0, right:0, height:2, top:0,
            background:`linear-gradient(90deg,transparent,${color}aa,white,${color}aa,transparent)`,
            animation:"card-scan-line 2.2s linear infinite",
            pointerEvents:"none", zIndex:10,
          }}/>
        )}
        {/* 배경 스캔라인 */}
        <div style={{
          position:"absolute",inset:0,borderRadius:10,pointerEvents:"none",
          background:"repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.06) 3px,rgba(0,0,0,0.06) 4px)",
        }}/>
        {/* 코너 브라켓 */}
        {[
          {top:0,left:0,borderTop:`2px solid ${color}`,borderLeft:`2px solid ${color}`,borderRadius:"10px 0 0 0"},
          {top:0,right:0,borderTop:`2px solid ${color}`,borderRight:`2px solid ${color}`,borderRadius:"0 10px 0 0"},
          {bottom:0,left:0,borderBottom:`2px solid ${color}`,borderLeft:`2px solid ${color}`,borderRadius:"0 0 0 10px"},
          {bottom:0,right:0,borderBottom:`2px solid ${color}`,borderRight:`2px solid ${color}`,borderRadius:"0 0 10px 0"},
        ].map((s,i) => (
          <div key={i} style={{ position:"absolute",width:12,height:12,...s }}/>
        ))}
        {/* 번호 + 상태 LED */}
        <div style={{ display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:2 }}>
          <span style={{ fontSize:9,fontWeight:900,color,letterSpacing:1.5,opacity:isActive ? 1 : 0.7 }}>J{num}</span>
          <span style={{
            width:isActive ? 8 : 5, height:isActive ? 8 : 5,
            borderRadius:"50%",
            background:isActive ? color : "#4ade80",
            boxShadow:isActive ? `0 0 16px ${color}, 0 0 32px ${color}66` : "0 0 6px #4ade80",
            display:"inline-block",
            animation:isActive ? "led-blink 0.45s ease-in-out infinite" : "none",
            transition:"width 0.3s, height 0.3s",
          }}/>
        </div>
        {/* 로봇 */}
        <div style={{ display:"flex",justifyContent:"center",marginBottom:3 }}
          dangerouslySetInnerHTML={{ __html: mkRobot(color, `r${num}`, rSz) }}/>
        {/* 이름 */}
        <div style={{ textAlign:"center",fontSize:big?13:11,fontWeight:900,
          letterSpacing:0.5,color,
          textShadow:isActive ? `0 0 22px ${color}, 0 0 44px ${color}88` : `0 0 12px ${color}cc`,
          marginBottom:1 }}>
          {label}
        </div>
        {/* 역할 */}
        <div style={{ textAlign:"center",fontSize:9.5,color:isActive ? "#7a8a9a" : "#56637a",marginBottom:4 }}>{sub}</div>
        {/* 데이터 칩 */}
        <div style={{
          background:isActive ? "rgba(0,0,0,0.55)" : "rgba(0,0,0,0.45)",
          borderRadius:5,padding:"3px 6px",
          fontSize:9.5,color:isActive ? "#8ba0b8" : "#6b7a94",textAlign:"center",
          borderTop:`1px solid ${isActive ? color+"55" : color+"28"}`,
          overflow:"hidden",whiteSpace:"nowrap",textOverflow:"ellipsis",
        }}>{stat ?? "—"}</div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// 연결선 레이블 — ComputedEdge[] 에서 읽음
// ═══════════════════════════════════════════════
type EdgeLabel = { x: number; y: number; text: string; col: string };
function computeEdgeLabels(edges: ComputedEdge[]): EdgeLabel[] {
  return edges
    .filter(e => e.lbl)
    .map(e => ({ x: e.lbl!.x, y: e.lbl!.y, text: e.lbl!.text, col: e.col }));
}

// ═══════════════════════════════════════════════
// SVG 연결선 + 흐름 점 — 직선(orthogonal) 전용
// ═══════════════════════════════════════════════
// 엣지 설명 — 실시간 활동 배너용
const EDGE_DESC: Record<string, string> = {
  e1:"J03→J09 선수집", e2:"J09→J02 데이터 전달", e3:"J02→J06 대본 전달",
  e5:"J03→J02 topic_pack", e6:"J06→J08 발행 중", e7:"J02→J07 오류 보고",
  e8:"J07→J02 코드 수정", e9:"J09→J05 수집 완료", e10:"J05→J07 헬스 리포트",
  e11:"J00→J01 인프라", e12:"J01→J02 라우팅", e13:"J04→J03 트리거", e14:"J04→J02 트리거",
};

function buildEdgeSvg(edges: ComputedEdge[], activeEdgeIds: Set<string>): string {
  const filters = edges.flatMap(e => {
    const on = activeEdgeIds.has(e.id);
    const base =
      `<filter id="gd${e.id}" x="-100%" y="-100%" width="300%" height="300%">` +
      `<feGaussianBlur stdDeviation="${on ? "4" : "1.2"}" result="b"/>` +
      `<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`;
    const halo = on
      ? `<filter id="gd${e.id}x" x="-200%" y="-200%" width="500%" height="500%">` +
        `<feGaussianBlur stdDeviation="8" result="b"/>` +
        `<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`
      : "";
    return [base, halo];
  }).join("");

  const lines = edges.flatMap(e => {
    const on    = activeEdgeIds.has(e.id);
    const wt    = (e.wt ?? 1.6) * (on ? 2.5 : 1.0);
    const lineO = on ? 0.96 : 0.38;
    const glowO = on ? 0.55 : 0.05;
    const dotR  = on ? 9.0  : 3.0;
    const dotO  = on ? 1.0  : 0.40;
    const dur   = on ? e.dur * 0.35 : e.dur * 2.5;
    const cnt   = on ? Math.max(e.dots * 2, 3) : 1;

    const segs: string[] = [];
    segs.push(`<path id="${e.id}" d="${e.path}" fill="none"/>`);
    // 외부 앰비언트 글로우
    segs.push(`<path d="${e.path}" fill="none" stroke="${e.col}" stroke-width="${wt + (on ? 20 : 3)}" opacity="${on ? 0.14 : 0.04}" stroke-linecap="round"/>`);
    // 이너 글로우
    segs.push(`<path d="${e.path}" fill="none" stroke="${e.col}" stroke-width="${wt + (on ? 7 : 2)}" opacity="${glowO}" stroke-linecap="round"/>`);
    // 메인 선
    segs.push(`<path d="${e.path}" fill="none" stroke="${e.col}" stroke-width="${wt}" opacity="${lineO}" stroke-linecap="round" stroke-linejoin="round"/>`);

    if (on) {
      // 화이트 플래시 펄스
      segs.push(`<path d="${e.path}" fill="none" stroke="white" stroke-width="${(wt * 0.55).toFixed(1)}" opacity="0" stroke-linecap="round"><animate attributeName="opacity" values="0;0.65;0" dur="0.5s" repeatCount="indefinite"/></path>`);
      // 컬러 링 펄스
      segs.push(`<path d="${e.path}" fill="none" stroke="${e.col}" stroke-width="${wt + 10}" opacity="0" stroke-linecap="round"><animate attributeName="opacity" values="0;0.38;0" dur="0.8s" repeatCount="indefinite"/></path>`);
    }

    for (let k = 0; k < cnt; k++) {
      const begin = (k * dur / cnt).toFixed(2);
      if (on) {
        // 혜성 헤일로
        segs.push(
          `<circle r="${(dotR * 2.1).toFixed(1)}" fill="${e.col}" opacity="0.25" filter="url(#gd${e.id}x)">` +
          `<animateMotion dur="${dur}s" repeatCount="indefinite" begin="${begin}s">` +
          `<mpath href="#${e.id}"/></animateMotion></circle>`
        );
      }
      // 메인 닷
      segs.push(
        `<circle r="${dotR}" fill="${e.col}" opacity="${dotO}" filter="url(#gd${e.id})">` +
        `<animateMotion dur="${dur}s" repeatCount="indefinite" begin="${begin}s">` +
        `<mpath href="#${e.id}"/></animateMotion></circle>`
      );
      if (on) {
        // 화이트 코어
        segs.push(
          `<circle r="3.8" fill="white" opacity="0.95">` +
          `<animateMotion dur="${dur}s" repeatCount="indefinite" begin="${begin}s">` +
          `<mpath href="#${e.id}"/></animateMotion></circle>`
        );
      }
    }
    return segs;
  });

  return `<defs>${filters}</defs>\n` + lines.join("\n");
}

// ═══════════════════════════════════════════════
// 사무실 뷰 메인
// ═══════════════════════════════════════════════
function OfficeView({ ov }: { ov?: OverviewData }) {
  const [time, setTime] = useState("--:--:--");
  const [blink, setBlink] = useState(true);
  useEffect(() => {
    const id = setInterval(() => {
      setTime(new Date().toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}));
      setBlink(b => !b);
    }, 1000);
    return () => clearInterval(id);
  }, []);

  // 파이프라인 그래프 — 에이전트·엣지·레이아웃 모두 /api/graph 에서 동적 수신
  // 새 에이전트·연결은 shared/pipeline_graph.py 만 수정하면 자동 반영
  const { data: graphData } = useSWR<GraphData>("/api/graph", fetcher, { refreshInterval: 60000 });
  const agents = useMemo<AgentDef[]>(() => graphData?.agents ?? [], [graphData]);
  const bnd = useMemo(() => buildBounds(agents), [agents]);
  const resolvedEdges = useMemo(() => resolveEdges(graphData?.edges ?? [], bnd), [graphData, bnd]);

  // 캔버스 크기·파이프라인 위치 — agents 에서 자동 파생 (하드코딩 금지)
  const W  = graphData?.layout?.W  ?? 1130;
  const H  = graphData?.layout?.H  ?? 660;
  const J03_X  = useMemo(() => agents.find(a => a.id === "j03")?.x ?? 18,  [agents]);
  const J08_X  = useMemo(() => agents.find(a => a.id === "j08")?.x ?? 954, [agents]);
  const ROW1_Y = useMemo(() => agents.find(a => a.id === "j03")?.y ?? 252, [agents]);
  const ROW2_Y = useMemo(() => agents.find(a => a.id === "j05")?.y ?? 462, [agents]);

  // 실시간 파이프라인 활동 — 2초 폴링
  const { data: actData } = useSWR<{active: string[]}>("/api/pipeline/activity", fetcher, { refreshInterval: 2000 });
  const activeEdgeSet = useMemo(() => new Set<string>(actData?.active ?? []), [actData]);
  const activeAgentSet = useMemo(() => {
    const s = new Set<string>();
    for (const e of graphData?.edges ?? []) {
      if (activeEdgeSet.has(e.id)) { s.add(e.from); s.add(e.to); }
    }
    return s;
  }, [activeEdgeSet, graphData]);
  const pipelineActive = useMemo(
    () => ["e1","e2","e3","e5","e6"].some(id => activeEdgeSet.has(id)),
    [activeEdgeSet]
  );

  // 파이프라인 현황 로그 — 5초 폴링
  const { data: logData } = useSWR<{log: {ts:string; msg:string}[]}>(
    "/api/pipeline/log", fetcher, { refreshInterval: 5000 }
  );
  const activityLog = logData?.log ?? [];

  const stats: Record<string, string> = {
    j00: `PID ${ov?.daemon?.pid ?? "—"} · ${ov?.daemon?.uptime ?? "—"}`,
    j01: `라우터 · 인텐트 분류`,
    j04: `잡 등록 · APScheduler`,
    j03: `트렌드 ${fmtNum(ov?.trends?.today)}개`,
    j09: `수집 ${fmtNum(ov?.trends?.today)}건`,
    j02: `오늘 ${fmtNum(ov?.posts?.today)}건`,
    j06: `인포그래픽 생성`,
    j08: `네이버·티스토리 ✓`,
    j05: `에이전트 ${fmtNum(ov?.vision?.total_agents ?? 0)}개 헬스 ${fmtNum(ov?.vision?.healthy ?? 0)}✓`,
    j07: `신규 ${fmtNum(ov?.guardian?.new)} · CRIT ${fmtNum(ov?.guardian?.critical)}`,
  };

  const edgeSvg = buildEdgeSvg(resolvedEdges, activeEdgeSet);
  const edgeLabels = computeEdgeLabels(resolvedEdges);

  return (<>
    <style>{`
      @keyframes agent-ring-expand {
        0%   { transform: scale(1.0); opacity: 0.85; }
        100% { transform: scale(1.65); opacity: 0.0; }
      }
      @keyframes led-blink {
        0%, 100% { opacity: 1.0; }
        50%       { opacity: 0.07; }
      }
      @keyframes card-scan-line {
        0%   { transform: translateY(-4px);  opacity: 0;  }
        6%   { opacity: 0.9; }
        94%  { opacity: 0.5; }
        100% { transform: translateY(230px); opacity: 0;  }
      }
    `}</style>
    <div style={{
      background:"var(--c-card)", border:"1px solid var(--c-bdr)",
      borderTop:`3px solid ${C.primary}`, borderRadius:12,
      overflow:"hidden", marginBottom:24,
    }}>
      {/* 타이틀 바 */}
      <div style={{
        display:"flex", alignItems:"center", justifyContent:"space-between",
        padding:"10px 20px", borderBottom:"1px solid var(--c-bdr)",
        background:"linear-gradient(90deg,#0e1119,#121624,#0e1119)",
      }}>
        <div style={{ display:"flex", alignItems:"center", gap:12 }}>
          <span style={{ fontSize:17, fontWeight:700, color:"var(--c-text)" }}>🖥 JARVIS 에이전트 사무실</span>
          <span style={{ fontSize:12, color:"var(--c-text5)" }}>10개 에이전트 · Self-Evolving v3</span>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <span style={{ fontFamily:"monospace", fontSize:14, fontWeight:700, color:"#4ade80" }}>{time}</span>
          <span style={{ display:"flex", alignItems:"center", gap:5 }}>
            <span style={{
              width:8, height:8, borderRadius:"50%", background:"#4ade80",
              boxShadow:"0 0 8px #4ade80", display:"inline-block",
              opacity: blink ? 1 : 0.25, transition:"opacity 0.4s",
            }}/>
            <span style={{ fontSize:11, fontWeight:800, color:"#4ade80", letterSpacing:1 }}>LIVE</span>
          </span>
        </div>
      </div>

      {/* 실시간 활동 배너 — 파이프라인 실행 중일 때만 표시 */}
      {activeEdgeSet.size > 0 && (
        <div style={{
          display:"flex", alignItems:"center", gap:12,
          padding:"6px 20px", borderBottom:"1px solid #4ade8022",
          background:"rgba(74,222,128,0.05)",
        }}>
          <span style={{ display:"flex",alignItems:"center",gap:5,flexShrink:0 }}>
            <span style={{
              width:7, height:7, borderRadius:"50%", background:"#4ade80",
              boxShadow:"0 0 8px #4ade80", display:"inline-block",
              animation:"pulse 0.8s ease-in-out infinite",
            }}/>
            <span style={{ fontSize:9,fontWeight:800,color:"#4ade80",letterSpacing:1.5 }}>ACTIVE</span>
          </span>
          <span style={{ fontSize:11, color:"#6ee7b7", letterSpacing:0.3 }}>
            {Array.from(activeEdgeSet).map(id => EDGE_DESC[id] ?? id).join("  →  ")}
          </span>
        </div>
      )}

      {/* 사무실 본체 */}
      <div style={{ overflowX:"auto" }}>
        <div style={{ position:"relative", width:W, height:H, margin:"0 auto" }}>

          {/* 배경 그리드 */}
          <div style={{
            position:"absolute", inset:0,
            backgroundImage:"linear-gradient(#1a2035 1px,transparent 1px),linear-gradient(90deg,#1a2035 1px,transparent 1px)",
            backgroundSize:"44px 44px", opacity:0.2,
          }}/>
          {/* 중앙 방사 그라디언트 */}
          <div style={{
            position:"absolute", inset:0,
            background:"radial-gradient(ellipse 60% 50% at 50% 38%,#1a2540 0%,#090b14 100%)",
          }}/>

          {/* 파이프라인 흐름 표시줄 (ROW1 배경 하이라이트) */}
          <div style={{
            position:"absolute",
            left:J03_X, top:ROW1_Y - 8,
            width:J08_X + CARD_W - J03_X, height:CARD_H + 16,
            background:pipelineActive ? "rgba(74,222,128,0.04)" : "rgba(255,255,255,0.015)",
            border:`1px solid ${pipelineActive ? "rgba(74,222,128,0.14)" : "rgba(255,255,255,0.04)"}`,
            borderRadius:16,
            boxShadow:pipelineActive ? "0 0 50px rgba(74,222,128,0.07) inset" : "none",
            transition:"background 0.8s ease, border 0.8s ease, box-shadow 0.8s ease",
          }}/>
          {/* 파이프라인 라벨 */}
          <div style={{
            position:"absolute", left:J03_X, top:ROW1_Y - 22,
            fontSize:9, fontWeight:700, color:"#2d3d55", letterSpacing:1.5,
          }}>— 발행 파이프라인 ——————————————————————————————————————————————</div>

          {/* 연결선 SVG */}
          <svg
            style={{ position:"absolute",top:0,left:0,width:"100%",height:"100%",pointerEvents:"none",zIndex:1 }}
            viewBox={`0 0 ${W} ${H}`}
            dangerouslySetInnerHTML={{ __html: edgeSvg }}
          />

          {/* 엣지 레이블 */}
          {edgeLabels.map((lb, i) => {
            const edgeId = resolvedEdges.find(e => e.lbl?.text === lb.text)?.id ?? "";
            const on = activeEdgeSet.has(edgeId);
            return (
              <div key={i} style={{
                position:"absolute",
                left: lb.x - 28, top: lb.y - 8,
                width: 56, textAlign:"center",
                fontSize:8, fontWeight: on ? 900 : 700,
                color: lb.col, opacity: on ? 1 : 0.55,
                letterSpacing:0.3,
                pointerEvents:"none", zIndex:2,
                textShadow: on ? `0 0 12px ${lb.col}cc` : `0 0 8px ${lb.col}44`,
                transition:"opacity 0.3s, font-weight 0.2s",
              }}>{lb.text}</div>
            );
          })}

          {/* CCTV — J00 우측 ↔ Mission Board 사이 빈 공간 (top 18) */}
          <div style={{
            position:"absolute", left:192, top:18,
            background:"#090b14", border:"1.5px solid #38bdf8",
            borderRadius:8, padding:"5px 10px",
            boxShadow:"0 0 14px #38bdf822",
            display:"flex", alignItems:"center", gap:7,
            zIndex:3,
          }}>
            <div style={{ width:8,height:8,borderRadius:"50%",background:"#f43f5e",
              boxShadow:"0 0 8px #f43f5e", opacity:blink?1:0.35, transition:"opacity 0.5s" }}/>
            <div>
              <div style={{ fontSize:9,fontWeight:900,color:"#38bdf8",letterSpacing:1.5 }}>CCTV</div>
              <div style={{ fontSize:8,color:"#2d3d55" }}>REC 24/7</div>
            </div>
          </div>

          {/* MISSION BOARD */}
          <div style={{
            position:"absolute", left:"50%", top:8, transform:"translateX(-50%)",
            background:"#090b14", border:"1.5px solid #4f90d9",
            borderRadius:8, padding:"6px 22px", textAlign:"center",
            boxShadow:"0 0 22px #4f90d933", zIndex:3,
          }}>
            <div style={{ fontSize:12,fontWeight:900,color:"#4f90d9",letterSpacing:2 }}>
              🖥 JARVIS MISSION BOARD
            </div>
            <div style={{ fontSize:9,color:"#2d3d55",marginTop:1 }}>
              자동화 · 트렌드 · 자가학습 · Self-Evolving v3
            </div>
          </div>

          {/* 에이전트 카드 렌더 — /api/graph 에서 동적 로드 */}
          {agents.map(a => (
            <div key={a.id} style={{ position:"absolute", left:a.x, top:a.y, zIndex:2 }}>
              <AgentCard
                num={a.num} label={a.label} sub={a.sub}
                color={a.color} stat={stats[a.id]} big={a.big}
                isActive={activeAgentSet.has(a.id)}
              />
            </div>
          ))}

          {/* 연결 범례 */}
          <div style={{
            position:"absolute", right:12, bottom:14,
            background:"rgba(9,11,20,0.9)", border:"1px solid #1e2640",
            borderRadius:8, padding:"9px 13px",
            backdropFilter:"blur(8px)", zIndex:4,
          }}>
            <div style={{ fontSize:9,fontWeight:700,color:"#374460",marginBottom:6,letterSpacing:1 }}>연결 범례</div>
            <div style={{ display:"flex", flexDirection:"column", gap:4 }}>
              {(graphData?.legend ?? []).map(it => (
                <div key={it.label} style={{ display:"flex",alignItems:"center",gap:6 }}>
                  <div style={{ width:20,height:2,background:it.col,borderRadius:1,
                    boxShadow:`0 0 4px ${it.col}88`,flexShrink:0 }}/>
                  <span style={{ fontSize:9,color:"#7a8aaa" }}>{it.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 소품 */}
          <div style={{ position:"absolute",left:600,bottom:14,fontSize:22,opacity:0.35 }}>🌱</div>
          <div style={{ position:"absolute",left:640,bottom:12,fontSize:18,opacity:0.4 }}>☕</div>
          <div style={{ position:"absolute",left:60, bottom:8,fontSize:18,opacity:0.30 }}>🖨️</div>
          <div style={{ position:"absolute",right:12, top:12,fontSize:18,opacity:0.28 }}>📡</div>

          {/* ── 파이프라인 현황판 (좌하단) ── */}
          <div style={{
            position:"absolute", left:14, top:ROW2_Y,
            width:230, height:CARD_H,
            background:"rgba(8,10,20,0.94)",
            border:`1px solid ${activeEdgeSet.size > 0 ? "rgba(74,222,128,0.22)" : "rgba(255,255,255,0.07)"}`,
            borderTop:`2px solid ${activeEdgeSet.size > 0 ? "#4ade80" : "#1e3252"}`,
            borderRadius:12,
            overflow:"hidden", zIndex:2,
            transition:"border 0.6s ease, border-top 0.6s ease",
          }}>
            {/* 현황판 헤더 */}
            <div style={{
              display:"flex", alignItems:"center", justifyContent:"space-between",
              padding:"6px 10px 5px",
              borderBottom:"1px solid rgba(255,255,255,0.05)",
              background:"rgba(0,0,0,0.3)",
            }}>
              <div style={{ display:"flex", alignItems:"center", gap:5 }}>
                <span style={{ width:6,height:6,borderRadius:"50%",background:"#4ade80",
                  boxShadow:"0 0 6px #4ade80",display:"inline-block",
                  opacity: activeEdgeSet.size > 0 ? 1 : 0.35 }}/>
                <span style={{ fontSize:9,fontWeight:900,color:"#4ade80",letterSpacing:1.5 }}>실시간 현황</span>
              </div>
              <span style={{ fontSize:8,color:"#2d3d55" }}>LIVE LOG</span>
            </div>
            {/* 로그 목록 */}
            <div style={{ overflowY:"auto", height:CARD_H - 32 }}>
              {activityLog.length === 0 ? (
                <div style={{
                  display:"flex", alignItems:"center", justifyContent:"center",
                  height:"100%", fontSize:8, color:"#2d3d55", letterSpacing:0.5,
                }}>대기 중...</div>
              ) : activityLog.map((item, i) => (
                <div key={i} style={{
                  display:"flex", gap:7, padding:"4px 10px",
                  borderBottom:"1px solid rgba(255,255,255,0.025)",
                  background: i === 0 ? "rgba(74,222,128,0.06)" : "transparent",
                }}>
                  <span style={{
                    fontSize:7.5, color:"#2d3d55", flexShrink:0,
                    fontFamily:"monospace", paddingTop:1,
                  }}>{item.ts}</span>
                  <span style={{
                    fontSize:8.5, lineHeight:1.45,
                    color: i === 0 ? "#6ee7b7" : "#4a5a70",
                  }}>{item.msg}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  </>);
}

// ═══════════════════════════════════════════════
// 공통 컴포넌트
// ═══════════════════════════════════════════════
function KpiCard({ label, value, sub, color }: { label:string; value:string|number; sub:string; color:string }) {
  return (
    <div style={{
      background:"var(--c-card)", border:"1px solid var(--c-bdr)",
      borderTop:`3px solid ${color}`, borderRadius:12,
      padding:"22px 20px", textAlign:"center", flex:1,
    }}>
      <div style={{ fontSize:14, color:"var(--c-text2)", marginBottom:10 }}>{label}</div>
      <div style={{ fontSize:42, fontWeight:800, color, lineHeight:1 }}>{value}</div>
      <div style={{ fontSize:13, color:"var(--c-text5)", marginTop:8 }}>{sub}</div>
    </div>
  );
}

function Badge({ text, color }: { text:string; color:string }) {
  return (
    <span style={{
      display:"inline-flex", alignItems:"center", padding:"3px 10px",
      borderRadius:20, fontSize:13, fontWeight:600,
      background:color+"22", color,
    }}>{text}</span>
  );
}

// ═══════════════════════════════════════════════
// 홈 페이지
// ═══════════════════════════════════════════════
export default function HomePage() {
  const { data: ov }     = useSWR<OverviewData>("/api/overview",       fetcher, { refreshInterval:30000 });
  const { data: agents } = useSWR<VisionAgent[]>("/api/vision/agents", fetcher, { refreshInterval:30000 });
  const [nowStr, setNowStr] = useState("");
  useEffect(() => {
    const t = () => setNowStr(new Date().toLocaleString("ko-KR",{
      year:"numeric",month:"2-digit",day:"2-digit",
      hour:"2-digit",minute:"2-digit",second:"2-digit",
    }));
    t(); const id = setInterval(t,1000); return ()=>clearInterval(id);
  },[]);

  void agents;
  const gNew = ov?.guardian?.new ?? 0;
  const errs = ov?.guardian?.recent?.slice(0,5) ?? [];

  return (
    <div>
      {/* 헤더 */}
      <div style={{ display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:22 }}>
        <div>
          <h1 style={{ fontSize:30,fontWeight:800,margin:0,color:"var(--c-text)" }}>JARVIS Hub</h1>
          <div style={{ fontSize:13,color:"var(--c-text5)",marginTop:4 }}>{nowStr}</div>
        </div>
        <div style={{
          display:"flex",alignItems:"center",gap:16,
          background:"var(--c-card)",border:"1px solid var(--c-bdr)",
          borderRadius:10,padding:"10px 18px",
        }}>
          <div style={{ display:"flex",alignItems:"center",gap:7 }}>
            <span style={{ width:9,height:9,borderRadius:"50%",display:"inline-block",
              background:ov?.daemon?.alive ? C.success : C.danger }}/>
            <span style={{ fontSize:13,color:"var(--c-text)" }}>
              {ov?.daemon?.alive ? "데몬 실행 중" : "데몬 정지"}
            </span>
          </div>
          {ov?.daemon?.pid    != null && <span style={{ fontSize:13,color:"var(--c-text5)" }}>PID {ov.daemon.pid}</span>}
          {ov?.daemon?.uptime       && <span style={{ fontSize:13,color:"var(--c-text5)" }}>가동 {ov.daemon.uptime}</span>}
        </div>
      </div>

      {/* KPI 4개 */}
      <div style={{ display:"flex",gap:14,marginBottom:26 }}>
        <KpiCard label="오늘 발행"     value={fmtNum(ov?.posts?.today)}   sub={`이번 주 ${fmtNum(ov?.posts?.week)}건`}   color={C.primary}/>
        <KpiCard label="트렌드 키워드" value={fmtNum(ov?.trends?.today)}  sub="오늘 수집"                               color={C.success}/>
        <KpiCard label="미해결 오류"   value={fmtNum(gNew)}               sub={`전체 ${fmtNum(ov?.guardian?.total)}건`} color={gNew>0?C.danger:C.success}/>
        <KpiCard label="정상 에이전트" value={ov?.vision?.healthy!=null?fmtNum(ov.vision.healthy):"—"} sub={`총 ${fmtNum(ov?.vision?.total_agents??0)}개`} color={C.primary}/>
      </div>

      {/* 에이전트 사무실 뷰 */}
      <OfficeView ov={ov} />

      {/* 최근 오류 + 심각도 분포 */}
      <div style={{ display:"grid",gridTemplateColumns:"3fr 1fr",gap:18 }}>
        <div style={{
          background:"var(--c-card)",border:"1px solid var(--c-bdr)",
          borderTop:`3px solid ${C.danger}`,borderRadius:12,padding:20,
        }}>
          <div style={{ fontSize:17,fontWeight:700,marginBottom:14,color:"var(--c-text)" }}>최근 오류</div>
          {errs.length===0 ? (
            <div style={{ color:"var(--c-text5)",fontSize:14 }}>{ov?"오류 없음 ✓":"로딩 중…"}</div>
          ) : (
            <table style={{ width:"100%",borderCollapse:"collapse",fontSize:14 }}>
              <thead>
                <tr style={{ color:"var(--c-text5)",textAlign:"left" }}>
                  {["시각","심각도","모듈","메시지"].map(h=>(
                    <th key={h} style={{ paddingBottom:10,fontWeight:600,whiteSpace:"nowrap" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {errs.map(e=>(
                  <tr key={e.id} style={{ borderTop:"1px solid var(--c-bdr)" }}>
                    <td style={{ padding:"9px 12px 9px 0",color:"var(--c-text5)",whiteSpace:"nowrap" }}>{fmtTime(e.timestamp)}</td>
                    <td style={{ padding:"9px 12px 9px 0" }}><Badge text={e.severity} color={severityColor(e.severity)}/></td>
                    <td style={{ padding:"9px 12px 9px 0",color:"var(--c-text2)",whiteSpace:"nowrap" }}>{e.module}</td>
                    <td style={{ padding:"9px 0",color:"var(--c-text2)",maxWidth:300,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap" }}>{e.message?.slice(0,100)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div style={{
          background:"var(--c-card)",border:"1px solid var(--c-bdr)",
          borderTop:`3px solid ${C.warn}`,borderRadius:12,padding:20,
        }}>
          <div style={{ fontSize:17,fontWeight:700,marginBottom:14,color:"var(--c-text)" }}>심각도 분포</div>
          <div style={{ display:"flex",flexDirection:"column",gap:9 }}>
            {[
              {label:"심각",  value:ov?.guardian?.critical??0, color:C.danger},
              {label:"높음",  value:ov?.guardian?.high??0,     color:"#f97316"},
              {label:"중간",  value:ov?.guardian?.medium??0,   color:C.warn},
              {label:"낮음",  value:ov?.guardian?.low??0,      color:C.muted},
              {label:"해결됨",value:ov?.guardian?.fixed??0,    color:C.success},
            ].map(it=>(
              <div key={it.label} style={{
                background:"var(--c-bg)",borderRadius:8,padding:"9px 14px",
                display:"flex",justifyContent:"space-between",alignItems:"center",
              }}>
                <span style={{ fontSize:14,color:"var(--c-text2)" }}>{it.label}</span>
                <span style={{ fontSize:20,fontWeight:800,color:it.color }}>{it.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
