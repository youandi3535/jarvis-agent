"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { fetcher, OverviewData, VisionAgent } from "@/lib/api";
import { C, fmtNum, fmtTime, severityColor } from "@/lib/utils";

// ═══════════════════════════════════════════════
// 레이아웃 상수 — 1130 × 640 컨테이너 / 4열 그리드
// ═══════════════════════════════════════════════
const W = 1130, H = 640;
const CARD_W = 158, CARD_H = 170;
const BIG_W  = 210, BIG_H  = 215;

// 그리드 열 (카드 left-x)
const C0 = 14, C1 = 240, C2 = 500, C3 = 800;
// 그리드 행 (카드 top-y)
const R0 = 70, R1 = 260, R2 = 450;
// 카드 중앙 좌표 헬퍼
const mid = (x: number, y: number, w = CARD_W, h = CARD_H) =>
  ({ x: x + w / 2, y: y + h / 2 });

// J01 MASTER — 중앙에 크게
const MASTER_X = 460, MASTER_Y = 252;
const MASTER_CX = MASTER_X + BIG_W / 2; // 565
const MASTER_CY = MASTER_Y + BIG_H / 2; // 360

// ───────────────────────────────────────────────
// 에이전트 정의
// ───────────────────────────────────────────────
type AgentDef = { id: string; num: string; label: string; sub: string; color: string; x: number; y: number };
const AGENTS: AgentDef[] = [
  // Row 0: 입력 에이전트 3개
  { id:"j03", num:"03", label:"J03 RADAR",   sub:"트렌드 레이더",     color:"#fbbf24", x:C0, y:R0 },
  { id:"j09", num:"09", label:"J09 COLLECT", sub:"데이터 수집",       color:"#38bdf8", x:C1, y:R0 },
  { id:"j04", num:"04", label:"J04 SCHED",   sub:"작업 스케줄러",     color:"#fb923c", x:C3, y:R0 },
  // Row 1: 지원 에이전트 (J01은 별도)
  { id:"j00", num:"00", label:"J00 INFRA",   sub:"인프라 관리자",     color:"#4ade80", x:C0, y:R1 },
  { id:"j07", num:"07", label:"J07 GUARD",   sub:"오류 수호자",       color:"#f43f5e", x:C3, y:R1 },
  // Row 2: 출력 파이프라인 4개
  { id:"j06", num:"06", label:"J06 IMAGE",   sub:"이미지 생성",       color:"#e879f9", x:C0, y:R2 },
  { id:"j05", num:"05", label:"J05 VISION",  sub:"에이전트 레지스트리", color:"#84cc16", x:C1, y:R2 },
  { id:"j02", num:"02", label:"J02 WRITER",  sub:"블로그 라이터",     color:"#a78bfa", x:C2, y:R2 },
  { id:"j08", num:"08", label:"J08 PUBLISH", sub:"발행 관리자",       color:"#22d3ee", x:C3, y:R2 },
];

// 에이전트 중앙 좌표
const AGT: Record<string, {x:number;y:number}> = {
  j03: mid(C0, R0), j09: mid(C1, R0), j04: mid(C3, R0),
  j00: mid(C0, R1), j07: mid(C3, R1),
  j06: mid(C0, R2), j05: mid(C1, R2), j02: mid(C2, R2), j08: mid(C3, R2),
  j01: { x: MASTER_CX, y: MASTER_CY },
};

// 연결선 — 실제 JARVIS 파이프라인 플로우 (ADR 013 기준)
const EDGES = [
  // ── 스케줄 트리거 ──
  { a:"j04", b:"j01", col:"#fb923c", dur:4.2, dots:1 },   // 스케줄러 → 마스터 (잡 실행 신호)
  // ── 트렌드·주제 수집 ──
  { a:"j01", b:"j03", col:"#4f90d9", dur:2.0, dots:1 },   // 마스터 → 레이더 (트렌드 요청)
  { a:"j03", b:"j01", col:"#fbbf24", dur:2.2, dots:2 },   // 레이더 → 마스터 (주제 패키지 반환)
  // ── 데이터 수집 ──
  { a:"j01", b:"j09", col:"#4f90d9", dur:1.7, dots:1 },   // 마스터 → 수집기 (수집 지시)
  { a:"j09", b:"j02", col:"#38bdf8", dur:2.0, dots:2 },   // 수집기 → 라이터 (데이터 직접 전달)
  // ── 글 작성 ──
  { a:"j01", b:"j02", col:"#a78bfa", dur:1.6, dots:2 },   // 마스터 → 라이터 (작성 지시)
  // ── 이미지 생성 ──
  { a:"j02", b:"j06", col:"#e879f9", dur:1.5, dots:2 },   // 라이터 → 이미지 (생성 요청)
  { a:"j06", b:"j02", col:"#e879f9", dur:2.1, dots:1 },   // 이미지 → 라이터 (완성 반환)
  // ── 품질 검증 ──
  { a:"j02", b:"j07", col:"#f43f5e", dur:2.6, dots:1 },   // 라이터 → 가디언 (검증 요청)
  { a:"j07", b:"j02", col:"#f43f5e", dur:2.9, dots:1 },   // 가디언 → 라이터 (승인/재작성)
  // ── 발행 ──
  { a:"j02", b:"j08", col:"#22d3ee", dur:1.6, dots:2 },   // 라이터 → 발행기 (발행 요청)
  // ── 인프라·모니터링 (상시) ──
  { a:"j00", b:"j01", col:"#4ade80", dur:5.5, dots:1 },   // 인프라 → 마스터 (상태 보고)
  { a:"j05", b:"j01", col:"#84cc16", dur:4.8, dots:1 },   // 비전 → 마스터 (에이전트 레지스트리)
];

// ═══════════════════════════════════════════════
// 로봇 SVG — 3D 책상·컴퓨터 작업 (viewBox 0 0 72 80)
// 2.5D: trapezoid 상판 + 우측면 depth + 구형 하이라이트
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
<!-- ══ 책상 3D ══ -->
<rect x="16" y="44" width="4" height="17" rx="2" fill="#080e1c"/>
<rect x="52" y="44" width="4" height="17" rx="2" fill="#080e1c"/>
<rect x="9" y="49" width="5" height="22" rx="2.5" fill="#0f1a2f"/>
<rect x="58" y="49" width="5" height="22" rx="2.5" fill="#0f1a2f"/>
<path d="M7 44 L65 44 L65 49 L7 49 Z" fill="#09111f"/>
<path d="M11 39 L61 39 L65 44 L7 44 Z" fill="#112035"/>
<path d="M11 39 L61 39 L61 40.5 L11 40.5 Z" fill="${color}" opacity="0.09"/>
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
<path d="M16 37.5 L56 37.5 L56 39.5 L16 39.5 Z" fill="#060811"/>
<path d="M18 34 L54 34 L56 37.5 L16 37.5 Z" fill="#080a18" stroke="${color}" stroke-width="0.7" stroke-opacity="0.22"/>
<rect x="20" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.18"/>
<rect x="25" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.18"/>
<rect x="30" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.18"/>
<rect x="35" y="34.5" width="4" height="2" rx="0.4" fill="${color}" opacity="0.18"/>
<rect x="40" y="34.5" width="9" height="2" rx="0.4" fill="${color}" opacity="0.25"><animate attributeName="opacity" values="0.1;0.5;0.1" dur="0.4s" repeatCount="indefinite"/></rect>
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
<!-- ══ 팔 3D (키보드 방향) ══ -->
<path d="M18 42.5 L28 44.5 L28 48 L17 47 Z" fill="url(#gLR${uid})" opacity="0.6"/>
<path d="M17 47 L28 48 L28 49.5 L16 48.5 Z" fill="${color}" opacity="0.22"/>
<path d="M44 44.5 L54 42.5 L55 47 L44 48 Z" fill="url(#gLR${uid})" opacity="0.6"/>
<path d="M44 48 L55 47 L56 48.5 L44 49.5 Z" fill="${color}" opacity="0.22"/>
<!-- ══ 의자 3D ══ -->
<path d="M44 56 L48 58.5 L48 65 L44 62.5 Z" fill="${color}" opacity="0.2"/>
<rect x="26" y="56" width="18" height="9" rx="2.5" fill="url(#gLR${uid})" opacity="0.52"/>
<rect x="26" y="56" width="18" height="9" rx="2.5" fill="url(#gHL${uid})"/>
<rect x="29" y="65" width="3.5" height="8" rx="1.75" fill="#0c1628"/>
<rect x="39.5" y="65" width="3.5" height="8" rx="1.75" fill="#0c1628"/>
<rect x="33" y="63" width="2.5" height="6" rx="1.25" fill="#07101e"/>
<rect x="36.5" y="63" width="2.5" height="6" rx="1.25" fill="#07101e"/>
</svg>`;
}

// ═══════════════════════════════════════════════
// 에이전트 카드 (3D)
// ═══════════════════════════════════════════════
function AgentCard({ num, label, sub, color, stat, big = false }: {
  num:string; label:string; sub:string; color:string; stat?:string; big?:boolean;
}) {
  const [hov, setHov] = useState(false);
  const w = big ? BIG_W : CARD_W, h = big ? BIG_H : CARD_H;
  const rSz = big ? 62 : 50;

  return (
    <div
      onMouseEnter={() => setHov(true)}
      onMouseLeave={() => setHov(false)}
      style={{
        width:w, height:h, position:"relative", overflow:"hidden",
        background:"linear-gradient(145deg,#131827 0%,#0b0d1a 100%)",
        border:`2px solid ${color}`,
        borderRadius:12,
        padding:"7px 9px",
        transform: hov
          ? `perspective(450px) rotateX(-7deg) rotateY(5deg) scale(1.05) translateZ(8px)`
          : `perspective(450px) rotateX(0) rotateY(0) scale(1)`,
        transition:"transform 0.26s cubic-bezier(0.23,1,0.32,1), box-shadow 0.26s ease",
        boxShadow: hov
          ? `0 0 36px ${color}66, 0 22px 55px rgba(0,0,0,0.65), inset 0 1px 0 ${color}55`
          : `0 0 16px ${color}44, 0 8px 32px rgba(0,0,0,0.5), inset 0 1px 0 ${color}28`,
        cursor:"default",
      }}
    >
      {/* 스캔라인 */}
      <div style={{
        position:"absolute",inset:0,borderRadius:10,pointerEvents:"none",
        background:"repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,0,0,0.06) 3px,rgba(0,0,0,0.06) 4px)",
      }}/>
      {/* 코너 브라켓 */}
      <div style={{ position:"absolute",top:0,left:0,width:12,height:12,
        borderTop:`2px solid ${color}`,borderLeft:`2px solid ${color}`,borderRadius:"10px 0 0 0" }}/>
      <div style={{ position:"absolute",top:0,right:0,width:12,height:12,
        borderTop:`2px solid ${color}`,borderRight:`2px solid ${color}`,borderRadius:"0 10px 0 0" }}/>
      <div style={{ position:"absolute",bottom:0,left:0,width:12,height:12,
        borderBottom:`2px solid ${color}`,borderLeft:`2px solid ${color}`,borderRadius:"0 0 0 10px" }}/>
      <div style={{ position:"absolute",bottom:0,right:0,width:12,height:12,
        borderBottom:`2px solid ${color}`,borderRight:`2px solid ${color}`,borderRadius:"0 0 10px 0" }}/>

      {/* 번호 + 상태 LED */}
      <div style={{ display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:2 }}>
        <span style={{ fontSize:9,fontWeight:900,color,letterSpacing:1.5,opacity:0.7 }}>J{num}</span>
        <span style={{ width:5,height:5,borderRadius:"50%",background:"#4ade80",
          boxShadow:"0 0 6px #4ade80",display:"inline-block" }}/>
      </div>

      {/* 로봇 */}
      <div style={{ display:"flex",justifyContent:"center",marginBottom:3 }}
        dangerouslySetInnerHTML={{ __html: mkRobot(color, `r${num}`, rSz) }}/>

      {/* 이름 */}
      <div style={{ textAlign:"center",fontSize:big?13:11,fontWeight:900,
        letterSpacing:0.5,color,textShadow:`0 0 12px ${color}cc`,marginBottom:1 }}>
        {label}
      </div>
      {/* 역할 */}
      <div style={{ textAlign:"center",fontSize:9.5,color:"#56637a",marginBottom:4 }}>{sub}</div>
      {/* 데이터 칩 */}
      <div style={{
        background:"rgba(0,0,0,0.45)",borderRadius:5,padding:"3px 6px",
        fontSize:9.5,color:"#6b7a94",textAlign:"center",
        borderTop:`1px solid ${color}28`,
        overflow:"hidden",whiteSpace:"nowrap",textOverflow:"ellipsis",
      }}>{stat ?? "—"}</div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// SVG 연결선 + 흐름 점 (Quadratic bezier, 법선 굴곡)
// ═══════════════════════════════════════════════
function buildEdgeSvg(): string {
  const L: string[] = [];
  EDGES.forEach((e, i) => {
    const f = AGT[e.a], t = AGT[e.b];
    if (!f || !t) return;
    const dx = t.x - f.x, dy = t.y - f.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    // 법선 방향으로 자연스러운 아치 — 수평 연결도 굴곡
    const sag = Math.min(dist * 0.24, 52);
    const nx = -dy / dist, ny = dx / dist;
    const mx = (f.x + t.x) / 2 + nx * sag;
    const my = (f.y + t.y) / 2 + ny * sag;
    const d = `M${f.x},${f.y} Q${mx.toFixed(1)},${my.toFixed(1)} ${t.x},${t.y}`;
    L.push(`<path id="e${i}" d="${d}" fill="none"/>`);
    // 배경 선 (글로우 효과)
    L.push(`<path d="${d}" fill="none" stroke="${e.col}" stroke-width="3.5" opacity="0.08"/>`);
    // 대시 선
    L.push(`<path d="${d}" fill="none" stroke="${e.col}" stroke-width="1.6" opacity="0.3" stroke-dasharray="6 6"/>`);
    // 흐름 점들
    for (let k = 0; k < e.dots; k++) {
      const begin = (k * e.dur / e.dots).toFixed(2);
      L.push(`<circle r="4.5" fill="${e.col}" opacity="0.88" filter="url(#gd${i})"><animateMotion dur="${e.dur}s" repeatCount="indefinite" begin="${begin}s"><mpath href="#e${i}"/></animateMotion></circle>`);
      L.push(`<circle r="2" fill="white" opacity="0.5"><animateMotion dur="${e.dur}s" repeatCount="indefinite" begin="${begin}s"><mpath href="#e${i}"/></animateMotion></circle>`);
    }
  });
  // 글로우 필터 defs
  const defs = EDGES.map((e, i) =>
    `<filter id="gd${i}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>`
  ).join("\n");
  return `<defs>${defs}</defs>\n` + L.join("\n");
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

  const stats: Record<string,string> = {
    j03: `트렌드 ${fmtNum(ov?.trends?.today)}개`,
    j09: `수집 ${fmtNum(ov?.trends?.today)}건`,
    j04: `잡 등록 — · 대기 0건`,
    j00: `PID ${ov?.daemon?.pid ?? "—"}`,
    j07: `신규 ${fmtNum(ov?.guardian?.new)} · CRIT ${fmtNum(ov?.guardian?.critical)}`,
    j06: `이미지 0개`,
    j05: `에이전트 ${fmtNum(ov?.vision?.total_agents ?? 0)}개 등록`,
    j02: `오늘 ${fmtNum(ov?.posts?.today)}건`,
    j08: `네이버 ✓ 티스토리 ✓`,
    j01: `에이전트 ${fmtNum(ov?.vision?.total_agents ?? 0)}개 가동 중`,
  };

  const edgeSvg = buildEdgeSvg();

  return (
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

      {/* 사무실 본체 */}
      <div style={{ overflowX:"auto" }}>
        <div style={{ position:"relative", width:W, height:H, margin:"0 auto" }}>

          {/* 배경 그리드 */}
          <div style={{
            position:"absolute",inset:0,
            backgroundImage:"linear-gradient(#1a2035 1px,transparent 1px),linear-gradient(90deg,#1a2035 1px,transparent 1px)",
            backgroundSize:"44px 44px", opacity:0.2,
          }}/>
          {/* 중앙 방사 그라디언트 */}
          <div style={{
            position:"absolute",inset:0,
            background:"radial-gradient(ellipse 55% 45% at 50% 44%,#1a2540 0%,#090b14 100%)",
          }}/>

          {/* 연결선 SVG overlay */}
          <svg
            style={{ position:"absolute",top:0,left:0,width:"100%",height:"100%",pointerEvents:"none" }}
            viewBox={`0 0 ${W} ${H}`}
            dangerouslySetInnerHTML={{ __html: edgeSvg }}
          />

          {/* CCTV */}
          <div style={{
            position:"absolute", left:14, top:10,
            background:"#090b14", border:"1.5px solid #38bdf8",
            borderRadius:8, padding:"6px 12px",
            boxShadow:"0 0 14px #38bdf822",
            display:"flex", alignItems:"center", gap:8,
          }}>
            <div style={{ width:9,height:9,borderRadius:"50%",background:"#f43f5e",
              boxShadow:"0 0 8px #f43f5e", opacity:blink?1:0.35, transition:"opacity 0.5s" }}/>
            <div>
              <div style={{ fontSize:9.5,fontWeight:900,color:"#38bdf8",letterSpacing:1.5 }}>CCTV</div>
              <div style={{ fontSize:8,color:"#2d3d55" }}>REC 24/7</div>
            </div>
          </div>

          {/* MISSION BOARD */}
          <div style={{
            position:"absolute", left:"50%", top:10, transform:"translateX(-50%)",
            background:"#090b14", border:"1.5px solid #4f90d9",
            borderRadius:8, padding:"7px 24px", textAlign:"center",
            boxShadow:"0 0 22px #4f90d933",
          }}>
            <div style={{ fontSize:13,fontWeight:900,color:"#4f90d9",letterSpacing:2 }}>
              🖥 JARVIS MISSION BOARD
            </div>
            <div style={{ fontSize:9.5,color:"#2d3d55",marginTop:2 }}>
              자동화 · 트렌드 · 자가학습 · Self-Evolving v3
            </div>
          </div>

          {/* 에이전트 카드 */}
          {AGENTS.map(a => (
            <div key={a.id} style={{ position:"absolute", left:a.x, top:a.y, zIndex:2 }}>
              <AgentCard num={a.num} label={a.label} sub={a.sub} color={a.color} stat={stats[a.id]}/>
            </div>
          ))}

          {/* J01 MASTER — 중앙 크게 */}
          <div style={{ position:"absolute", left:MASTER_X, top:MASTER_Y, zIndex:2 }}>
            <AgentCard num="01" label="J01 MASTER" sub="마스터 라우터" color="#4f90d9" stat={stats.j01} big/>
          </div>

          {/* 연결 범례 — J08 오른쪽 빈 공간 */}
          <div style={{
            position:"absolute", right:10, top:260,
            background:"rgba(9,11,20,0.88)", border:"1px solid #1e2640",
            borderRadius:8, padding:"10px 14px",
            backdropFilter:"blur(8px)", zIndex:3,
          }}>
            <div style={{ fontSize:9,fontWeight:700,color:"#374460",marginBottom:7,letterSpacing:1 }}>연결 범례</div>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:"4px 16px" }}>
              {[
                {c:"#4f90d9",l:"Master"}, {c:"#4ade80",l:"Infra"},
                {c:"#a78bfa",l:"Writer"}, {c:"#fbbf24",l:"Radar"},
                {c:"#fb923c",l:"Sched"},  {c:"#38bdf8",l:"Collect"},
                {c:"#e879f9",l:"Image"},  {c:"#22d3ee",l:"Publish"},
                {c:"#f43f5e",l:"Guard"},  {c:"#84cc16",l:"Vision"},
              ].map(it => (
                <div key={it.l} style={{ display:"flex",alignItems:"center",gap:5 }}>
                  <div style={{ width:16,height:2,background:it.c,borderRadius:1,
                    boxShadow:`0 0 4px ${it.c}88` }}/>
                  <span style={{ fontSize:9.5,color:"#7a8aaa" }}>{it.l}</span>
                </div>
              ))}
            </div>
          </div>

          {/* 소품 */}
          <div style={{ position:"absolute",right:730,bottom:18,fontSize:28,opacity:0.22 }}>🌱</div>
          <div style={{ position:"absolute",right:690,bottom:10,fontSize:24,opacity:0.18 }}>☕</div>
          <div style={{ position:"absolute",left:14, bottom:14,fontSize:22,opacity:0.18 }}>🖨️</div>
        </div>
      </div>
    </div>
  );
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
