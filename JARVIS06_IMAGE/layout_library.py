"""JARVIS06_IMAGE/layout_library.py — 코드 박제 레이아웃 라이브러리.

★ 사용자 박제 2026-07-13: Phase 0/1 실패 시 Phase 2로 보장.
  10개 레이아웃 — 색깔이 아닌 HTML 구조 자체가 다름.
  template_engine.render_layout 이 CSS 변수 주입 + 슬롯 치환 + 빈슬롯 JS 주입.

슬롯: {{TITLE}} {{SUBTITLE}} {{EYEBROW}} {{SOURCE}} {{BRAND}}
      {{HERO_STATS}} {{CHART_1}} {{CHART_2}} {{CHART_3}} {{MINI_CARDS}}
"""
from __future__ import annotations

LAYOUTS: list[dict] = []

_FONT = "@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');"
_BASE = "*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}"


def _add(id_: str, name: str, aesthetic: str, html: str) -> None:
    LAYOUTS.append({"id": id_, "name": name, "aesthetic": aesthetic, "html": html})


# ── 1. 좌우 분할 잡지형 ──────────────────────────────────────────────────────
_add("lib-split-hero", "좌우 분할 잡지형", "editorial magazine split", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft);display:flex;flex-direction:column}
.sp{display:flex;min-height:480px}
.sl{flex:0 0 460px;background:linear-gradient(160deg,var(--hero0),var(--hero1));padding:52px 48px;position:relative;overflow:hidden}
.sl::before{content:"";position:absolute;right:-90px;top:-90px;width:300px;height:300px;border-radius:50%;background:var(--a1);opacity:.12}
.sl::after{content:"";position:absolute;left:-60px;bottom:-60px;width:200px;height:200px;border-radius:50%;background:var(--a2);opacity:.1}
.eb{display:inline-flex;align-items:center;gap:8px;padding:8px 16px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:14px;font-weight:700;position:relative;z-index:1}
.eb::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--eyebrow)}
h1{color:#fff;font-size:44px;font-weight:900;line-height:1.1;margin:20px 0 12px;letter-spacing:-.022em;position:relative;z-index:1}
.sub{color:rgba(255,255,255,.62);font-size:17px;line-height:1.55;position:relative;z-index:1}
.hs{margin-top:36px;position:relative;z-index:1}.hs:empty{display:none}
.sr{flex:1;padding:28px 32px;background:#fff;display:flex;flex-direction:column;gap:20px}
section{background:var(--soft);border-radius:18px;padding:24px 26px;border:1px solid var(--grid)}
section:has([data-jarvis-empty]){display:none}
section.fl{flex:1}
.bot{padding:20px 32px;background:var(--soft);display:flex;flex-direction:column;gap:18px}
.mc{display:flex;gap:14px}.mc:empty{display:none}
section.wd{background:#fff;border-radius:18px;padding:24px 26px;border:1px solid var(--grid)}
footer{padding:16px 32px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="sp">
    <div class="sl">
      <span class="eb">{{EYEBROW}}</span>
      <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
      <div class="hs">{{HERO_STATS}}</div>
    </div>
    <div class="sr">
      <section class="fl">{{CHART_1}}</section>
      <section class="fl">{{CHART_2}}</section>
    </div>
  </div>
  <div class="bot">
    <div class="mc">{{MINI_CARDS}}</div>
    <section class="wd">{{CHART_3}}</section>
  </div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 2. KPI 밴드형 ────────────────────────────────────────────────────────────
_add("lib-kpi-band", "KPI 밴드형", "dashboard kpi band", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.hd{background:linear-gradient(135deg,var(--hero0),var(--hero1));padding:36px 60px;display:flex;align-items:center;gap:36px;position:relative;overflow:hidden}
.hd::after{content:"";position:absolute;right:-60px;bottom:-60px;width:260px;height:260px;border-radius:50%;background:var(--a2);opacity:.1}
.eb{display:inline-flex;align-items:center;gap:8px;padding:7px 14px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:13px;font-weight:700;white-space:nowrap;flex-shrink:0}
.eb::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--eyebrow)}
.vd{width:1px;height:36px;background:rgba(255,255,255,.2);flex-shrink:0}
.htx{flex:1}
h1{color:#fff;font-size:34px;font-weight:900;letter-spacing:-.02em}
.sub{color:rgba(255,255,255,.6);font-size:15px;margin-top:6px}
.brand-r{color:var(--a1s);font-size:12px;font-weight:800;white-space:nowrap;position:relative;z-index:1}
.kb{background:linear-gradient(90deg,var(--a1),var(--a1s) 40%,var(--a2));padding:24px 60px}
.kb:empty{display:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px;padding:28px 60px 0}
section{background:#fff;border-radius:20px;padding:26px 28px;border:1px solid var(--grid);box-shadow:0 4px 22px rgba(0,0,0,.06)}
section:has([data-jarvis-empty]){display:none}
.mc{padding:20px 60px;display:flex;gap:14px}.mc:empty{display:none}
footer{padding:18px 60px 28px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px;margin-top:16px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <header class="hd">
    <span class="eb">{{EYEBROW}}</span>
    <div class="vd"></div>
    <div class="htx"><h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p></div>
  </header>
  <div class="kb">{{HERO_STATS}}</div>
  <div class="grid">
    <section>{{CHART_1}}</section>
    <section>{{CHART_2}}</section>
    <section>{{CHART_3}}</section>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 3. 좌측 사이드바형 ───────────────────────────────────────────────────────
_add("lib-sidebar-left", "좌측 사이드바형", "sidebar stats panel", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;display:flex;background:var(--soft);min-height:700px}
.sb{width:290px;flex-shrink:0;background:linear-gradient(170deg,var(--hero0),var(--hero1));padding:44px 32px;display:flex;flex-direction:column;position:relative;overflow:hidden}
.sb::before{content:"";position:absolute;bottom:-70px;right:-70px;width:220px;height:220px;border-radius:50%;background:var(--a1);opacity:.15}
.sb-tag{display:inline-block;padding:6px 12px;background:var(--a1);border-radius:7px;color:#fff;font-size:12px;font-weight:800;letter-spacing:.06em;margin-bottom:28px}
h1{color:#fff;font-size:28px;font-weight:900;line-height:1.18;letter-spacing:-.01em}
.sub{color:rgba(255,255,255,.55);font-size:14px;line-height:1.6;margin:14px 0 32px}
.hs{flex:1;position:relative;z-index:1}.hs:empty{display:none}
.sb-ft{margin-top:auto;padding-top:20px;border-top:1px solid rgba(255,255,255,.14);color:rgba(255,255,255,.38);font-size:11px;line-height:1.6}
.main{flex:1;padding:32px 36px;display:flex;flex-direction:column;gap:22px;overflow:hidden}
section.top{background:#fff;border-radius:20px;padding:28px 30px;border:1px solid var(--grid);box-shadow:0 4px 20px rgba(0,0,0,.05)}
section.top:has([data-jarvis-empty]){display:none}
.row{display:flex;gap:20px}
section.sm{background:#fff;border-radius:18px;padding:24px 26px;border:1px solid var(--grid);flex:1;box-shadow:0 3px 14px rgba(0,0,0,.04)}
section.sm:has([data-jarvis-empty]){display:none}
.mc{display:flex;gap:14px}.mc:empty{display:none}
footer{padding:14px 0;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:13px;margin-top:auto}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <aside class="sb">
    <span class="sb-tag">{{EYEBROW}}</span>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
    <div class="hs">{{HERO_STATS}}</div>
  </aside>
  <main class="main">
    <section class="top">{{CHART_1}}</section>
    <div class="row">
      <section class="sm">{{CHART_2}}</section>
      <section class="sm">{{CHART_3}}</section>
    </div>
    <div class="mc">{{MINI_CARDS}}</div>
    <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
  </main>
</div></body></html>""")


# ── 4. 오버레이 카드형 ───────────────────────────────────────────────────────
_add("lib-overlay-cards", "오버레이 카드형", "dark hero floating cards", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.hero{background:linear-gradient(145deg,var(--hero0),var(--hero1));padding:56px 60px 90px;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;left:-100px;bottom:-120px;width:380px;height:380px;border-radius:50%;background:var(--a2);opacity:.12}
.hero::after{content:"";position:absolute;right:40px;top:30px;width:220px;height:220px;border-radius:50%;background:var(--a1);opacity:.09}
.eb{display:inline-flex;align-items:center;gap:8px;padding:8px 18px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:14px;font-weight:700;margin-bottom:22px}
.eb::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--eyebrow)}
h1{color:#fff;font-size:50px;font-weight:900;line-height:1.08;letter-spacing:-.025em;max-width:720px;position:relative;z-index:1}
.sub{color:rgba(255,255,255,.6);font-size:18px;margin:14px 0 0;max-width:580px;position:relative;z-index:1}
.hs{margin-top:30px;position:relative;z-index:1}.hs:empty{display:none}
.float{margin-top:-56px;padding:0 60px;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px;position:relative;z-index:10}
section.fc{background:#fff;border-radius:22px;padding:28px 30px;box-shadow:0 12px 40px rgba(0,0,0,.15);border:1px solid var(--grid)}
section.fc:has([data-jarvis-empty]){display:none}
.body{padding:22px 60px}
section.wc{background:#fff;border-radius:20px;padding:26px 28px;border:1px solid var(--grid);box-shadow:0 4px 16px rgba(0,0,0,.05)}
section.wc:has([data-jarvis-empty]){display:none}
.mc{display:flex;gap:14px;margin-top:18px}.mc:empty{display:none}
footer{padding:18px 60px 26px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px;margin-top:18px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="hero">
    <div class="eb">{{EYEBROW}}</div>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
    <div class="hs">{{HERO_STATS}}</div>
  </div>
  <div class="float">
    <section class="fc">{{CHART_1}}</section>
    <section class="fc">{{CHART_2}}</section>
  </div>
  <div class="body">
    <section class="wc">{{CHART_3}}</section>
    <div class="mc">{{MINI_CARDS}}</div>
  </div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 5. 파노라마 메인 차트형 ──────────────────────────────────────────────────
_add("lib-panoramic", "파노라마 메인 차트형", "panoramic wide chart", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.hd{background:linear-gradient(135deg,var(--hero0),var(--hero1));padding:30px 60px;display:flex;align-items:center;gap:30px}
.eb{display:inline-flex;align-items:center;gap:7px;padding:7px 14px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:13px;font-weight:700;flex-shrink:0}
.eb::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--eyebrow)}
.vd{width:1px;height:32px;background:rgba(255,255,255,.2)}
h1{color:#fff;font-size:30px;font-weight:900;letter-spacing:-.02em;flex:1}
.sub{color:rgba(255,255,255,.55);font-size:14px;max-width:300px}
.kb{background:var(--ink);padding:18px 60px}.kb:empty{display:none}
.pan{padding:26px 60px 18px}
section.pano{background:#fff;border-radius:20px;padding:28px 30px;border:1px solid var(--grid);box-shadow:0 4px 22px rgba(0,0,0,.06)}
section.pano:has([data-jarvis-empty]){display:none}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px;padding:0 60px 18px}
section.cc{background:#fff;border-radius:20px;padding:24px 26px;border:1px solid var(--grid);box-shadow:0 3px 16px rgba(0,0,0,.04)}
section.cc:has([data-jarvis-empty]){display:none}
.mc{padding:0 60px 18px;display:flex;gap:14px}.mc:empty{display:none}
footer{padding:16px 60px 26px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <header class="hd">
    <span class="eb">{{EYEBROW}}</span>
    <div class="vd"></div>
    <h1>{{TITLE}}</h1>
    <p class="sub">{{SUBTITLE}}</p>
  </header>
  <div class="kb">{{HERO_STATS}}</div>
  <div class="pan"><section class="pano">{{CHART_1}}</section></div>
  <div class="row">
    <section class="cc">{{CHART_2}}</section>
    <section class="cc">{{CHART_3}}</section>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 6. 미니멀 라이트형 ───────────────────────────────────────────────────────
_add("lib-minimal-light", "미니멀 라이트형", "minimal clean whitespace", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:#fff}
.topbar{height:5px;background:linear-gradient(90deg,var(--a1),var(--a2))}
.hd{padding:44px 60px 32px;border-bottom:1px solid var(--grid)}
.eb{display:inline-flex;align-items:center;gap:7px;padding:6px 14px;background:var(--soft);border-radius:100px;color:var(--a1);font-size:13px;font-weight:700}
.eb::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--a1)}
h1{color:var(--ink);font-size:48px;font-weight:900;letter-spacing:-.025em;margin:18px 0 10px}
.sub{color:var(--muted);font-size:18px;line-height:1.5}
.hs{padding:26px 60px;background:var(--soft);border-bottom:1px solid var(--grid)}.hs:empty{display:none}
.row2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));border-top:1px solid var(--grid)}
section.cc{background:#fff;padding:34px 40px;border-right:1px solid var(--grid)}
section.cc:last-child{border-right:none}
section.cc:has([data-jarvis-empty]){display:none}
section.wc{background:#fff;padding:34px 40px;border-top:1px solid var(--grid)}
section.wc:has([data-jarvis-empty]){display:none}
.mc{padding:22px 60px;display:flex;gap:14px;background:var(--soft);border-top:1px solid var(--grid)}.mc:empty{display:none}
footer{padding:16px 60px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="topbar"></div>
  <div class="hd">
    <span class="eb">{{EYEBROW}}</span>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
  </div>
  <div class="hs">{{HERO_STATS}}</div>
  <div class="row2">
    <section class="cc">{{CHART_1}}</section>
    <section class="cc">{{CHART_2}}</section>
  </div>
  <section class="wc">{{CHART_3}}</section>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 7. 대각선 컷형 ───────────────────────────────────────────────────────────
_add("lib-diagonal-cut", "대각선 컷형", "diagonal hero geometric", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.hero{background:linear-gradient(135deg,var(--hero0),var(--hero1));padding:58px 60px 80px;position:relative;overflow:hidden}
.hero::after{content:"";position:absolute;bottom:-38px;left:0;right:0;height:76px;background:var(--soft);transform:skewY(-2.2deg);transform-origin:left bottom}
.eb{display:inline-flex;align-items:center;gap:8px;padding:8px 18px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:14px;font-weight:700;position:relative;z-index:2}
.eb::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--eyebrow)}
h1{color:#fff;font-size:50px;font-weight:900;line-height:1.08;letter-spacing:-.025em;margin:22px 0 12px;position:relative;z-index:2;max-width:800px}
.sub{color:rgba(255,255,255,.6);font-size:18px;position:relative;z-index:2;max-width:580px}
.hs{margin-top:28px;position:relative;z-index:2}.hs:empty{display:none}
.body{padding:36px 60px 18px}
section.wc{background:#fff;border-radius:20px;padding:28px 30px;border:1px solid var(--grid);box-shadow:0 4px 22px rgba(0,0,0,.06);margin-bottom:22px}
section.wc:has([data-jarvis-empty]){display:none}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px}
section.cc{background:#fff;border-radius:20px;padding:24px 26px;border:1px solid var(--grid);box-shadow:0 3px 16px rgba(0,0,0,.04)}
section.cc:has([data-jarvis-empty]){display:none}
.mc{display:flex;gap:14px;padding:0 60px 18px}.mc:empty{display:none}
footer{padding:16px 60px 26px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="hero">
    <span class="eb">{{EYEBROW}}</span>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
    <div class="hs">{{HERO_STATS}}</div>
  </div>
  <div class="body">
    <section class="wc">{{CHART_1}}</section>
    <div class="row">
      <section class="cc">{{CHART_2}}</section>
      <section class="cc">{{CHART_3}}</section>
    </div>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 8. 보고서형 ──────────────────────────────────────────────────────────────
_add("lib-report-formal", "보고서형", "formal report document", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
.bbar{background:var(--ink);padding:13px 60px;display:flex;justify-content:space-between;align-items:center}
.bname{color:#fff;font-size:13px;font-weight:800;letter-spacing:.07em}
.bsrc{color:rgba(255,255,255,.45);font-size:12px}
.tsec{background:linear-gradient(135deg,var(--hero0),var(--hero1));padding:44px 60px;border-left:6px solid var(--a1)}
.eb{display:inline-flex;align-items:center;gap:7px;padding:6px 13px;background:rgba(255,255,255,.12);border-radius:6px;color:var(--eyebrow);font-size:13px;font-weight:700;margin-bottom:16px}
h1{color:#fff;font-size:40px;font-weight:900;letter-spacing:-.02em}
.sub{color:rgba(255,255,255,.6);font-size:16px;margin-top:10px}
.sec-hs{padding:28px 60px;border-left:4px solid var(--a1);margin:24px 60px 0}.sec-hs:empty{display:none}
.sec-c{background:#fff;border-radius:16px;padding:24px 28px;box-shadow:0 4px 18px rgba(0,0,0,.06);border:1px solid var(--grid);margin:20px 60px 0}
.sec-c:has([data-jarvis-empty]){display:none}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px;padding:0 60px;margin-top:20px}
section.cc{background:#fff;border-radius:16px;padding:22px 26px;box-shadow:0 3px 14px rgba(0,0,0,.05);border:1px solid var(--grid)}
section.cc:has([data-jarvis-empty]){display:none}
.mc{padding:16px 60px 0;display:flex;gap:14px}.mc:empty{display:none}
footer{padding:18px 60px 26px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:13px;margin-top:22px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="tsec">
    <div class="eb">{{EYEBROW}}</div>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
  </div>
  <div class="sec-hs">{{HERO_STATS}}</div>
  <div class="sec-c">{{CHART_1}}</div>
  <div class="row">
    <section class="cc">{{CHART_2}}</section>
    <section class="cc">{{CHART_3}}</section>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 9. 비대칭 모자이크형 ─────────────────────────────────────────────────────
_add("lib-mosaic-grid", "비대칭 모자이크형", "asymmetric mosaic layout", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
/* 상단: 히어로(42%) + 첫 차트(58%) */
.top{display:flex;min-height:400px}
.hero{flex:0 0 540px;background:linear-gradient(150deg,var(--hero0),var(--hero1));padding:48px 44px;position:relative;overflow:hidden;display:flex;flex-direction:column;justify-content:flex-end}
.hero::before{content:"";position:absolute;right:-80px;top:-80px;width:280px;height:280px;border-radius:50%;background:var(--a1);opacity:.13}
.eb{display:inline-flex;align-items:center;gap:7px;padding:7px 14px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:13px;font-weight:700;margin-bottom:20px;position:relative;z-index:1}
.eb::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--eyebrow)}
h1{color:#fff;font-size:36px;font-weight:900;line-height:1.12;letter-spacing:-.02em;position:relative;z-index:1}
.sub{color:rgba(255,255,255,.58);font-size:15px;margin-top:10px;position:relative;z-index:1}
.hs{margin-top:24px;position:relative;z-index:1}.hs:empty{display:none}
section.c1{flex:1;background:#fff;padding:32px 36px}
section.c1:has([data-jarvis-empty]){display:none}
/* 중단: 넓은 카드(60%) + 작은 카드(40%) */
.mid{display:flex;gap:0;border-top:1px solid var(--grid)}
section.c2{flex:0 0 580px;background:var(--soft);padding:28px 36px;border-right:1px solid var(--grid)}
section.c2:has([data-jarvis-empty]){display:none}
.mid-r{flex:1;padding:28px 30px;background:#fff;display:flex;flex-direction:column;gap:20px}
.mc{display:flex;gap:14px;flex-wrap:wrap}.mc:empty{display:none}
section.c3{background:var(--soft);border-radius:14px;padding:22px 24px;border:1px solid var(--grid)}
section.c3:has([data-jarvis-empty]){display:none}
footer{padding:16px 36px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="top">
    <div class="hero">
      <span class="eb">{{EYEBROW}}</span>
      <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
      <div class="hs">{{HERO_STATS}}</div>
    </div>
    <section class="c1">{{CHART_1}}</section>
  </div>
  <div class="mid">
    <section class="c2">{{CHART_2}}</section>
    <div class="mid-r">
      <div class="mc">{{MINI_CARDS}}</div>
      <section class="c3">{{CHART_3}}</section>
    </div>
  </div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


# ── 10. 중앙 집중 히어로형 ───────────────────────────────────────────────────
_add("lib-center-focus", "중앙 집중 히어로형", "centered hero focus", """<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"><style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;800;900&display=swap');
*{margin:0;padding:0;box-sizing:border-box;font-family:'Noto Sans KR',sans-serif}
.pg{width:1280px;background:var(--soft)}
/* 상단 브랜드 바 */
.tbar{padding:14px 60px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--grid)}
.logo{font-size:13px;font-weight:800;color:var(--ink);letter-spacing:.06em}
.src{font-size:12px;color:var(--muted)}
/* 중앙 히어로 */
.hero{background:linear-gradient(155deg,var(--hero0),var(--hero1));text-align:center;padding:52px 60px 48px;position:relative;overflow:hidden}
.hero::before{content:"";position:absolute;left:50%;top:-120px;transform:translateX(-50%);width:500px;height:500px;border-radius:50%;background:var(--a1);opacity:.08}
.eb{display:inline-flex;align-items:center;gap:8px;padding:8px 18px;border:1px solid var(--eyebrow);border-radius:100px;color:var(--eyebrow);font-size:14px;font-weight:700;margin-bottom:22px;position:relative;z-index:1}
.eb::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--eyebrow)}
h1{color:#fff;font-size:52px;font-weight:900;line-height:1.08;letter-spacing:-.025em;max-width:900px;margin:0 auto 14px;position:relative;z-index:1}
.sub{color:rgba(255,255,255,.6);font-size:18px;max-width:600px;margin:0 auto;position:relative;z-index:1}
/* 히어로 스탯 */
.hs{padding:24px 60px;background:var(--ink)}.hs:empty{display:none}
/* 3열 + 미니 */
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:22px;padding:28px 60px 18px}
section.cc{background:#fff;border-radius:20px;padding:24px 26px;border:1px solid var(--grid);box-shadow:0 4px 18px rgba(0,0,0,.05)}
section.cc:has([data-jarvis-empty]){display:none}
.mc{padding:0 60px 18px;display:flex;gap:14px}.mc:empty{display:none}
footer{padding:16px 60px 24px;border-top:1px solid var(--grid);display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:14px}
.br{font-weight:800;color:var(--ink)}
</style></head><body><div class="pg">
  <div class="hero">
    <div class="eb">{{EYEBROW}}</div>
    <h1>{{TITLE}}</h1><p class="sub">{{SUBTITLE}}</p>
  </div>
  <div class="hs">{{HERO_STATS}}</div>
  <div class="grid">
    <section class="cc">{{CHART_1}}</section>
    <section class="cc">{{CHART_2}}</section>
    <section class="cc">{{CHART_3}}</section>
  </div>
  <div class="mc">{{MINI_CARDS}}</div>
  <footer><span>{{SOURCE}}</span><span class="br">{{BRAND}}</span></footer>
</div></body></html>""")


__all__ = ["LAYOUTS"]
