"use client";
import "./globals.css";
import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { path: "/",           icon: "⌂", label: "홈" },
  { path: "/radar",      icon: "◉", label: "레이더" },
  { path: "/posts",      icon: "✎", label: "발행" },
  { path: "/quality",    icon: "✦", label: "품질" },
  { path: "/performance",icon: "▲", label: "성과" },
  { path: "/learning",   icon: "◈", label: "AI학습" },
  { path: "/errors",     icon: "⚠", label: "오류" },
  { path: "/scheduler",  icon: "⏱", label: "스케줄" },
  { path: "/system",     icon: "◎", label: "시스템" },
  { path: "/db",         icon: "◻", label: "DB" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>JARVIS Hub</title>
      </head>
      <body style={{ display: "flex", minHeight: "100vh", background: "var(--c-bg)", margin: 0 }}>
        {/* 좌측 사이드바 */}
        <aside style={{
          width: 72,
          minHeight: "100vh",
          background: "var(--c-card)",
          borderRight: "1px solid var(--c-bdr)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          padding: "20px 0",
          position: "fixed",
          top: 0, left: 0, bottom: 0,
          zIndex: 100,
        }}>
          <div style={{ fontSize: 13, fontWeight: 900, color: "var(--c-primary)", letterSpacing: 1, marginBottom: 24, textAlign: "center", lineHeight: 1.3 }}>
            J<br/>A<br/>I
          </div>
          {NAV.map(n => {
            const active = n.path === "/" ? path === "/" : path.startsWith(n.path);
            return (
              <Link key={n.path} href={n.path} style={{
                display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
                width: 56, height: 52, borderRadius: 10, marginBottom: 4, textDecoration: "none",
                background: active ? "rgba(79,144,217,0.12)" : "transparent",
                borderLeft: active ? "3px solid var(--c-primary)" : "3px solid transparent",
              }}>
                <span style={{ fontSize: 16, color: active ? "var(--c-primary)" : "var(--c-text2)" }}>{n.icon}</span>
                <span style={{ fontSize: 10, color: active ? "var(--c-primary)" : "var(--c-text2)", marginTop: 3, fontWeight: active ? 700 : 400 }}>{n.label}</span>
              </Link>
            );
          })}
        </aside>

        {/* 메인 영역 */}
        <main style={{ marginLeft: 72, flex: 1, padding: "28px 32px 40px", minWidth: 0 }}>
          {children}
        </main>
      </body>
    </html>
  );
}
