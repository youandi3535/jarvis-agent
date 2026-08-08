// 색 팔레트 — 단일 진실 소스는 `dashboard/app/globals.css` 의 `--c-*` 토큰이다.
//
// C 는 규정이 허용하는 "페이지의 C 상수" 이며, 값을 *리터럴 16진수로* 보유한다.
// var() 로 못 바꾸는 이유는 소비처의 두 가지 사용 형태 때문 — 둘 다 CSS 파서를 타지 않는다:
//   ① 알파 접미사 이어붙이기 — `C.primary + "22"` / `` `1px solid ${C.warn}44` `` (실측 10곳:
//      posts·errors·radar). `var(--c-primary)22` 는 유효한 색이 아니라 선언 전체가 무시된다.
//   ② Recharts SVG 프레젠테이션 속성 — `stroke={C.primary}` / `dot={{ fill:C.primary }}`
//      (app/page.tsx 차트 시리즈 색). 속성값은 var() 치환 대상이 아니다.
// 따라서 아래 5줄은 검사기 면제(주석)를 쓰되, **값은 globals.css 토큰과 반드시 동일하게**
// 유지한다. 토큰을 바꾸면 이 5줄도 같이 바꿀 것 (현재 5개 전부 토큰과 일치 확인됨).
export const C = {
  primary: "#4f90d9", // = var(--c-primary) — 알파 접미사·차트 시리즈용 리터럴
  success: "#4ade80", // = var(--c-success) — 알파 접미사·차트 시리즈용 리터럴
  warn:    "#fbbf24", // = var(--c-warn)    — 알파 접미사·차트 시리즈용 리터럴
  danger:  "#f87171", // = var(--c-danger)  — 알파 접미사용 리터럴
  muted:   "#94a3b8", // = var(--c-muted)   — 알파 접미사용 리터럴
};

// N — 중립(배경·보더·텍스트) 팔레트. 토큰을 *복사* 하지 않고 *참조* 한다.
// 종전엔 16진수 사본이었는데 globals.css 의 텍스트 토큰이 대비 개선으로 밝아지는 동안
// 사본만 옛 값에 남아 3건이 어긋나 있었다 (text #e2e8f0≠#ffffff · text2 #94a3b8≠#c8d6e8 ·
// text5 #475569≠#a8bcce). 참조로 바꾸면 드리프트가 구조적으로 불가능해진다.
// ※ CSS 컨텍스트(style 속성) 전용 — SVG 속성이나 문자열 이어붙이기에는 쓰지 말 것.
export const N = {
  bg:    "var(--c-bg)",
  card:  "var(--c-card)",
  bdr:   "var(--c-bdr)",
  text:  "var(--c-text)",
  text2: "var(--c-text2)",
  text5: "var(--c-text5)",
};

export function statusColor(status: string): string {
  const s = status?.toLowerCase() ?? "";
  if (s === "new" || s === "error" || s === "critical") return C.danger;
  if (s === "fixed" || s === "resolved" || s === "success" || s === "healthy") return C.success;
  if (s === "analyzing" || s === "pending" || s === "warn" || s === "degraded") return C.warn;
  if (s === "ignored" || s === "wontfix" || s === "offline") return C.muted;
  return C.primary;
}

export function severityColor(sev: string): string {
  const s = sev?.toLowerCase() ?? "";
  if (s === "critical") return C.danger;
  if (s === "high")     return "#f97316"; // orange — 5색 토큰에 없는 중간 단계(critical↔medium) 구분색
  if (s === "medium")   return C.warn;
  return C.muted;
}

export function fmtNum(n: number | undefined | null): string {
  if (n == null) return "—";
  return n >= 10000 ? (n / 10000).toFixed(1) + "만"
       : n >= 1000  ? n.toLocaleString()
       : String(n);
}

export function fmtTime(s: string | undefined | null): string {
  if (!s) return "—";
  return s.slice(5, 16).replace("T", " ");
}

export function ago(s: string | undefined | null): string {
  if (!s) return "—";
  const diff = Date.now() - new Date(s).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return "방금";
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 전`;
  return `${Math.floor(h / 24)}일 전`;
}

export function pct(a: number, b: number): string {
  if (!b) return "0%";
  return `${Math.round((a / b) * 100)}%`;
}
