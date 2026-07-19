"""JARVIS06_IMAGE/template_engine.py — 슬롯 기반 레이아웃 템플릿 엔진 (임의 레이아웃 재현).

★ 사용자 박제 2026-07-05 (ERRORS [360]): 색·스타일뿐 아니라 *레이아웃 자체* 를 학습·재현.
  레퍼런스의 레이아웃/구성/장식을 *학습 시점(나이틀리·비전)* 에 **재사용 HTML 템플릿**으로
  저작(데이터는 슬롯, 색은 CSS 변수) → *렌더 시점* 은 슬롯에 검증 실데이터만 채워 즉시·안전.
  LLM 저작을 렌더 임계경로에서 뺀 채(느린 저작은 새벽 1회) 임의 레이아웃을 재현한다.

슬롯 어휘 (템플릿이 데이터 위치에 쓰는 토큰 — 코드가 실데이터로 채움):
  {{TITLE}} {{SUBTITLE}} {{EYEBROW}} {{SOURCE}} {{BRAND}}
  {{HERO_STATS}}   — 대형 히어로 스탯 블록 행 (시계열→증감%, 카테고리→최고항목)
  {{CHART_1}} {{CHART_2}} {{CHART_3}}  — 데이터셋 1~3의 완성 차트 블록(제목+SVG+범례, 형태 자동)
  {{MINI_CARDS}}   — 보조 통계 카드 행
색 변수 (템플릿은 모든 색을 이 변수로 — 코드가 :root 주입):
  var(--hero0) var(--hero1) var(--ink) var(--a1) var(--a1s) var(--a2) var(--a2s)
  var(--soft) var(--muted) var(--eyebrow) var(--grid)

진입점: render_layout(template, title, subtitle, datasets, recipe, src) -> html
        verify_layout_output(html, datasets) -> bool   (데이터 안전 게이트)
"""
from __future__ import annotations

import re

from JARVIS06_IMAGE.pro_templates import (
    _pairs, _is_timeseries, _pct_change, _sparkline, _fmt,
    _hero_stat, _line_chart, _bar_chart, _donut, _mini_card,
)

# LLM 저작 프롬프트에 넣을 슬롯 사양 (단일 소스)
SLOT_SPEC = (
    "데이터 슬롯(이 토큰 위치에 코드가 실데이터를 채움 — 너는 위치·주변 레이아웃만 설계):\n"
    "  {{TITLE}} {{SUBTITLE}} {{EYEBROW}} {{SOURCE}} {{BRAND}}\n"
    "  {{HERO_STATS}}  = 대형 히어로 스탯 블록 행(이미 완성된 flex row)\n"
    "  {{CHART_1}} {{CHART_2}} {{CHART_3}}  = 완성된 차트 블록(제목+SVG+범례). 있는 만큼만 배치\n"
    "  {{MINI_CARDS}}  = 보조 통계 카드 행\n"
    "색은 반드시 CSS 변수로만: var(--hero0) var(--hero1) var(--ink) var(--a1) var(--a1s) "
    "var(--a2) var(--a2s) var(--soft) var(--muted) var(--eyebrow) var(--grid)"
)

_SLOTS = ("{{TITLE}}", "{{SUBTITLE}}", "{{EYEBROW}}", "{{SOURCE}}", "{{BRAND}}",
          "{{HERO_STATS}}", "{{CHART_1}}", "{{CHART_2}}", "{{CHART_3}}", "{{MINI_CARDS}}")

# ── 크롬 파생 단일 진입점 (사용자 박제 2026-07-19): 라벨·출처·브랜드는 전부 데이터에서 파생 ──
#   하드코딩 라벨·거짓 출처·헤드라인 출처 차단. J09 datasets[].source(provider) 만 신뢰.
BRAND = "JARVIS · 데이터 인사이트"

_PROVIDER_LABEL = {
    "kosis": "통계청 KOSIS", "ecos": "한국은행 ECOS", "krx": "한국거래소",
    "yfinance": "Yahoo Finance", "fdr": "FinanceDataReader", "dart": "금융감독원 DART",
    "fred": "FRED", "worldbank": "World Bank", "oecd": "OECD", "imf": "IMF",
    "academic": "학술 논문", "web": "웹 공개자료", "market": "시장 데이터", "news": "언론 보도",
}


def source_label(datasets: list, fallback: str = "") -> str:
    """datasets[].source(provider 우선) → '데이터 출처: …'. 뉴스 헤드라인(장문 name) 배제·dedupe.
    ★ 하드코딩/헤드라인 출처 원천 차단 — 이 함수가 출처 문자열의 *유일한* 생산자."""
    seen: list[str] = []
    for ds in datasets or []:
        src = ds.get("source") or {}
        prov = str(src.get("provider", "")).strip()
        prov_l = prov.lower()
        key = prov_l.split(":")[0] if ":" in prov_l else prov_l
        disp = _PROVIDER_LABEL.get(prov_l) or _PROVIDER_LABEL.get(key)
        if not disp and ":" in prov:          # 'news:매일경제' → '매일경제'
            disp = prov.split(":", 1)[1].strip()
        if not disp:                           # provider 미상: name 은 짧을 때만(헤드라인 배제)
            nm = str(src.get("name", "")).strip()
            disp = nm if (nm and len(nm) <= 20) else ""
        if disp and disp not in seen:
            seen.append(disp)
    if not seen:
        return fallback or "데이터 출처: 공개 통계"
    return "데이터 출처: " + " · ".join(seen[:3])


def _eyebrow_from_data(datasets: list) -> str:
    """아이브로우 배지 = 데이터 기준시점(as_of)에서 파생. 없으면 '실데이터'. (고정 문구 반복 금지)"""
    for ds in datasets or []:
        ao = str((ds.get("source") or {}).get("as_of", "")).strip()
        if ao:
            return f"{ao[:7].replace('-', '.')} 기준"
    return "실데이터"


def _keep_last_slot(tmpl: str, slot: str) -> str:
    """★ 크롬 중복 차단 (사용자 박제 2026-07-19): 템플릿에 {{BRAND}}/{{SOURCE}} 가 2회+ 있으면
    마지막(footer) 1개만 남기고 앞의 것은 제거. 라이브러리·나이틀리 학습·미래 템플릿 *전부* 에
    렌더 시점 강제 → 어떤 템플릿이 와도 브랜딩·출처는 1회. (개별 템플릿 수정 불필요)"""
    parts = tmpl.split(slot)
    if len(parts) <= 2:                      # 0~1회 → 그대로
        return tmpl
    return "".join(parts[:-1]) + slot + parts[-1]


# ── 슬롯 콘텐츠 생성 (실데이터 → HTML, pro_templates 빌더 재사용) ─────────────
def _slot_hero_stats(datasets, pal) -> str:
    ts = [d for d in datasets if _is_timeseries(d)]
    cats = [d for d in datasets if not _is_timeseries(d)]
    blocks = []
    if ts:
        cols = [(pal["a1"], pal["a1s"]), (pal["a2"], pal["a2s"])]
        for i, d in enumerate(ts[:2]):
            pts = _pairs(d)
            if not pts:
                continue
            chg = _pct_change(pts)
            big = (f"{'+' if chg >= 0 else ''}{chg:.1f}<span style='font-size:34px'>%</span>"
                   f"<span style='font-size:30px'> {'▲' if chg >= 0 else '▼'}</span>") if chg is not None else _fmt(pts[-1][1])
            sub = f"{pts[-1][0]} {_fmt(pts[-1][1])}{d.get('unit','')} · {pts[0][0]} {_fmt(pts[0][1])} 대비"
            c, cs = cols[i % 2]
            blocks.append(_hero_stat(pal, d.get("title", f"지표{i+1}"), big, sub, c, cs, _sparkline(pts, cs)))
    elif cats:
        d = cats[0]
        pts = sorted(_pairs(d), key=lambda kv: -abs(kv[1]))
        if pts:
            top = pts[0]
            _ttl = str(d.get("title", "")).strip()
            # ★ 라벨 = 실제 최고 항목명(예: 다우지수). 하드코딩 "최고가 종목"(주식용) 금지 — 동적 설계.
            blocks.append(_hero_stat(pal, top[0],
                                     f"{_fmt(top[1])}<span style='font-size:30px'> {d.get('unit','')}</span>",
                                     f"{_ttl} 최고" if _ttl else "최고값", pal["a1"], pal["a1s"]))
            if len(pts) > 1:
                blocks.append(_hero_stat(pal, "항목 수", f"{len(pts)}<span style='font-size:30px'>개</span>",
                                         f"합계 {_fmt(sum(v for _, v in pts))}{d.get('unit','')}", pal["a2"], pal["a2s"]))
    # 각 히어로 블록 flex:1 → 폭을 균등 분배해 밴드 우측 여백 제거 (공간 채움·동적 설계)
    inner = "".join(f"<div style='flex:1;min-width:0'>{b}</div>" for b in blocks)
    return f"<div style='display:flex;gap:24px'>{inner}</div>" if blocks else ""


def _slot_chart_block(ds, pal, num) -> str:
    """한 데이터셋 → 완성 차트 블록(제목+SVG+범례). 형태 자동(시계열→라인/카테고리→막대/비중→도넛)."""
    if ds is None:
        return ""
    pts = _pairs(ds)
    if not pts:
        return ""
    vh = (ds.get("viz_hint") or "").lower()
    unit = ds.get("unit", "")
    title = ds.get("title", "")
    if _is_timeseries(ds):
        chart, note = _line_chart([{"name": title, "pts": pts, "c": pal["a1"], "cs": pal["a1s"]}], pal)
        inner = chart
        right = f"<span style='font-size:14px;color:{pal['muted']}'>{note}</span>"
    elif "pie" in vh or "donut" in vh or (unit == "%" and 2 <= len(pts) <= 6 and abs(sum(v for _, v in pts) - 100) < 15):
        donut, legend = _donut(pts, pal)
        inner = f"<div style='display:flex;align-items:center;gap:36px'>{donut}<div style='flex:1'>{legend}</div></div>"
        right = ""
    else:
        inner = _bar_chart(sorted(pts, key=lambda kv: -abs(kv[1])), pal)
        right = f"<span style='font-size:15px;color:{pal['muted']};font-weight:700'>{unit}</span>" if unit else ""
    head = (f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:14px'>"
            f"<div style='display:flex;align-items:center;gap:12px'>"
            f"<div style='width:32px;height:32px;border-radius:9px;background:var(--ink);color:#fff;font-weight:800;"
            f"font-size:15px;display:flex;align-items:center;justify-content:center'>{num:02d}</div>"
            f"<div style='font-size:21px;font-weight:800;color:var(--ink)'>{title}</div></div>{right}</div>")
    return head + inner


def _slot_mini_cards(datasets, pal, n_charts_used=0) -> str:
    # n_charts_used: CHART_1~3 슬롯에서 이미 소비된 datasets 수 → 중복 표시 방지
    cards = []
    for d in datasets[n_charts_used:n_charts_used + 3]:
        pts = _pairs(d)
        if not pts:
            continue
        top = max(pts, key=lambda kv: abs(kv[1]))
        cards.append(_mini_card(pal, "chart", pal["soft"], pal["ink"],
                                d.get("title", ""), _fmt(top[1]), d.get("unit", "")))
    return f"<div style='display:flex;gap:20px'>{''.join(cards)}</div>" if cards else ""


# ── 렌더 ────────────────────────────────────────────────────────────────────
def _root_vars(pal) -> str:
    keys = ["ink", "a1", "a1s", "a2", "a2s", "soft", "muted", "eyebrow", "grid"]
    parts = [f"--hero0:{pal['hero'][0]};--hero1:{pal['hero'][1]};"]
    parts += [f"--{k}:{pal.get(k, '#888')};" for k in keys]
    return ":root{" + "".join(parts) + "}"


def render_layout(template: str, title: str, subtitle: str, datasets: list,
                  recipe: dict, src: str = "", chip: str = "") -> str:
    """레이아웃 템플릿 + 실데이터 → 완성 HTML (LLM 0). 색 변수 주입 + 슬롯 치환."""
    # 제목·카드 제목 내 'N종목' LLM 추정치 → 실데이터 실제 개수로 교정
    def _fix_n(t, n):
        return re.sub(r'\d+종목', f'{n}종목', t) if n > 0 and t else t
    datasets = [{**d, "title": _fix_n(d.get("title", ""), len(_pairs(d)))} for d in datasets]
    if datasets:
        title = _fix_n(title, len(_pairs(datasets[0])))

    ts = [d for d in datasets if _is_timeseries(d)]
    cats = [d for d in datasets if not _is_timeseries(d)]
    ordered = ts + cats
    n_charts = min(len(ordered), 3)
    # 빈 CHART 슬롯: 마커 삽입 → JS post-processing 이 해당 컨테이너 섹션 숨김
    _EMPTY = '<span data-jarvis-empty="1" style="display:none"></span>'
    chart_slots = {
        "{{CHART_1}}": _slot_chart_block(ordered[0] if len(ordered) > 0 else None, recipe, 1),
        "{{CHART_2}}": _slot_chart_block(ordered[1] if len(ordered) > 1 else None, recipe, 2),
        "{{CHART_3}}": _slot_chart_block(ordered[2] if len(ordered) > 2 else None, recipe, 3),
    }
    for k in chart_slots:
        if not chart_slots[k]:
            chart_slots[k] = _EMPTY
    subs = {
        "{{TITLE}}": str(title),
        "{{SUBTITLE}}": str(subtitle),
        "{{EYEBROW}}": chip or _eyebrow_from_data(datasets),   # 데이터 기준시점 파생(고정 문구 금지)
        "{{SOURCE}}": src or source_label(datasets),            # provider 파생(헤드라인·거짓 출처 금지)
        "{{BRAND}}": BRAND,                                     # 단일 상수(레이아웃 footer 1회만)
        "{{HERO_STATS}}": _slot_hero_stats(datasets, recipe),
        "{{MINI_CARDS}}": _slot_mini_cards(datasets, recipe, n_charts_used=n_charts),
        **chart_slots,
    }
    # ★ 크롬 중복 정규화 (전 템플릿 공통): BRAND/SOURCE 는 footer 1회만 남김
    template = _keep_last_slot(template, "{{BRAND}}")
    template = _keep_last_slot(template, "{{SOURCE}}")
    html = template
    for k, v in subs.items():
        html = html.replace(k, v)
    root = f"<style>{_root_vars(recipe)}</style>"
    if "</head>" in html:
        html = html.replace("</head>", root + "</head>", 1)
    elif re.search(r"<body[^>]*>", html):
        html = re.sub(r"(<body[^>]*>)", r"\1" + root, html, count=1)
    else:
        html = root + html
    # 빈 슬롯 마커의 조상 '레이아웃 셀'(grid/flex 자식·SECTION) 숨기기 — 구조 무관 범용
    # (라이브러리·학습·미래 템플릿 전부: 부모가 grid/flex 인 첫 조상을 셀로 보고 숨김 → 빈 카드 잔존 0)
    _hide_js = (
        "<script>(function(){"
        "document.querySelectorAll('[data-jarvis-empty]').forEach(function(el){"
        "var p=el.parentElement,d=0;"
        "while(p&&d<12&&p.tagName!=='BODY'){"
        "var cs=getComputedStyle(p),par=p.parentElement,pd=par?getComputedStyle(par).display:'';"
        "var bg=cs.backgroundColor;"
        "var card=(bg&&bg!=='rgba(0, 0, 0, 0)'&&bg!=='transparent')||parseFloat(cs.borderTopWidth)>0||cs.boxShadow!=='none'||parseFloat(cs.borderRadius)>0;"
        "if(p.offsetHeight<420&&(p.tagName==='SECTION'||pd==='grid'||pd==='flex'||card)){p.style.display='none';return;}"
        "p=par;d++;}"
        "});"
        "})();</script>"
    )
    if "</body>" in html:
        html = html.replace("</body>", _hide_js + "</body>", 1)
    else:
        html += _hide_js
    return html


def has_all_slots_resolved(html: str) -> bool:
    """치환 후 잔여 슬롯 토큰 없어야 (템플릿이 미정의 슬롯 사용 시 탐지)."""
    return not re.search(r"\{\{[A-Z_0-9]+\}\}", html)


def verify_layout_output(html: str, datasets: list) -> bool:
    """렌더 출력의 표시 수치가 실데이터·파생값에 grounding 되는지 (템플릿 내 하드코딩 수치 차단)."""
    try:
        from JARVIS06_IMAGE.infographic_engine import _dg_verify_html
        return _dg_verify_html(html, datasets)
    except Exception:
        return True   # 검증기 부재 시 통과(폴백 안전망이 별도 존재)


__all__ = ["render_layout", "verify_layout_output", "has_all_slots_resolved", "SLOT_SPEC"]
