"""JARVIS06_IMAGE/html_renderer.py — HTML/CSS → Playwright 스크린샷 렌더러.

preview.html 수준의 고품질 인포그래픽을 순수 HTML/CSS로 렌더링.
Chart.js CDN 의존 없이 순수 CSS 레이아웃만 사용.

지원 viz_type:
    comparison_kpi   — 다중 컬럼 KPI 비교 카드 (img1 스타일)
    status_checklist — 색상 배지 체크리스트 (img4 스타일)
    timeline         — 수평 타임라인 (img5 스타일)
    decision_matrix  — 상황 → 행동 매트릭스 (img8 스타일)
    dark_summary     — 다크 배경 요약 카드 (img9 스타일)
    cost_breakdown   — 비례 바 + 합계 테이블 (img10 스타일)
    kpi_cards        — KPI 숫자 카드 그리드
    highlight_card   — 한 줄 강조 카드
    insight_card     — 인사이트 카드
    scenario_cards   — 시나리오 3단 카드

공개 API:
    render(spec: dict, out_path: Path) -> Path
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis")

# Playwright Chromium 경로 탐색 순서
_CHROMIUM_CANDIDATES = [
    # sandbox 환경 (개발)
    os.path.expanduser("~/.cache/ms-playwright/chromium-1217/chrome-linux/chrome"),
    os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-1217/chrome-linux/headless_shell"),
    # Mac 프로덕션 환경
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]

# 공통 폰트 스택
_FONT = "'Apple SD Gothic Neo', 'Nanum Gothic', 'NanumGothic', 'Malgun Gothic', sans-serif"

# 공통 색상 팔레트
_C = {
    "blue_dark":   "#1a237e",
    "blue_mid":    "#3949ab",
    "blue_light":  "#e8eaf6",
    "blue_border": "#5c6bc0",
    "green_dark":  "#2e7d32",
    "green_mid":   "#43a047",
    "green_light": "#e8f5e9",
    "orange_dark": "#e65100",
    "orange_mid":  "#f57c00",
    "orange_light":"#fff3e0",
    "red_dark":    "#c62828",
    "red_mid":     "#e53935",
    "red_light":   "#ffebee",
    "purple_dark": "#4a148c",
    "purple_mid":  "#7b1fa2",
    "purple_light":"#f3e5f5",
    "gray_bg":     "#f8f9fa",
    "gray_border": "#e0e0e0",
    "text":        "#212121",
    "text2":       "#616161",
    "text3":       "#9e9e9e",
    "white":       "#ffffff",
}

# 컬러 테마 → 색상 3단계
_THEME_COLORS: dict[str, tuple[str,str,str]] = {
    "blue":   (_C["blue_dark"],   _C["blue_mid"],   _C["blue_light"]),
    "green":  (_C["green_dark"],  _C["green_mid"],  _C["green_light"]),
    "orange": (_C["orange_dark"], _C["orange_mid"], _C["orange_light"]),
    "red":    (_C["red_dark"],    _C["red_mid"],    _C["red_light"]),
    "purple": (_C["purple_dark"], _C["purple_mid"], _C["purple_light"]),
    "mixed":  (_C["blue_dark"],   _C["blue_mid"],   _C["blue_light"]),
}

# 컬럼별 강조 컬러 (comparison_kpi용)
_COL_COLORS = [
    (_C["blue_mid"],   _C["blue_light"]),
    (_C["orange_mid"], _C["orange_light"]),
    (_C["green_mid"],  _C["green_light"]),
    (_C["purple_mid"], _C["purple_light"]),
    ("#00838f",        "#e0f7fa"),
    ("#6d4c41",        "#efebe9"),
]

# status_checklist 배지 컬러 매핑
_BADGE_COLORS: dict[str, tuple[str,str,str]] = {
    "success":  (_C["green_mid"],  "#fff", _C["green_light"]),
    "warn":     (_C["orange_mid"], "#fff", _C["orange_light"]),
    "danger":   (_C["red_mid"],    "#fff", _C["red_light"]),
    "info":     (_C["blue_mid"],   "#fff", _C["blue_light"]),
    "neutral":  (_C["text2"],      "#fff", "#f5f5f5"),
    # 한국어 키워드 매핑
    "추천":     (_C["green_mid"],  "#fff", _C["green_light"]),
    "권장":     (_C["green_mid"],  "#fff", _C["green_light"]),
    "구매 추천":(_C["green_mid"],  "#fff", _C["green_light"]),
    "대기":     (_C["orange_mid"], "#fff", _C["orange_light"]),
    "고려":     (_C["orange_mid"], "#fff", _C["orange_light"]),
    "대기 고려":(_C["orange_mid"], "#fff", _C["orange_light"]),
    "대안":     (_C["red_mid"],    "#fff", _C["red_light"]),
    "주의":     (_C["red_mid"],    "#fff", _C["red_light"]),
}


# ══════════════════════════════════════════════════════════════════════
# 공개 API
# ══════════════════════════════════════════════════════════════════════

def render(spec: dict[str, Any], out_path: Path) -> Path:
    """spec → HTML 렌더링 → Playwright 스크린샷 → JPG.

    Args:
        spec:     generate_image_spec()이 반환한 설계서 dict
        out_path: 저장 파일 경로 (.jpg / .png)
    Returns:
        생성된 이미지 Path
    Raises:
        RuntimeError: Playwright 브라우저를 찾을 수 없을 때
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html = _build_html(spec)
    return _screenshot(html, out_path)


# ══════════════════════════════════════════════════════════════════════
# HTML 빌더
# ══════════════════════════════════════════════════════════════════════

_DISPATCH_TPL: dict[str, Any] = {}  # populated after def

def _build_html(spec: dict) -> str:
    viz = spec.get("viz_type", "kpi_cards")
    fn = _DISPATCH_TPL.get(viz, _tpl_kpi_cards)
    body_content = fn(spec)
    return _wrap(body_content, bg=spec.get("bg", "#ffffff"))


def _wrap(body: str, bg: str = "#ffffff") -> str:
    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: {_FONT}; background: {bg}; color: {_C["text"]}; }}
  .wrap {{ padding: 32px 40px 28px; }}
  .title {{ font-size: 22px; font-weight: 700; text-align: center; margin-bottom: 6px; color: {_C["text"]}; }}
  .subtitle {{ font-size: 14px; color: {_C["text2"]}; text-align: center; margin-bottom: 22px; }}
  .source {{ font-size: 12px; color: {_C["text3"]}; text-align: center; margin-top: 16px; }}
</style>
</head><body>
{body}
</body></html>"""


# ── 1. comparison_kpi (img1 스타일) ──────────────────────────────────
def _tpl_comparison_kpi(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("data", [])
    source   = spec.get("source", "")
    left_label  = spec.get("left_label", "이전")
    right_label = spec.get("right_label", "이후")

    cols = ""
    for i, item in enumerate(items):
        c, bg = _COL_COLORS[i % len(_COL_COLORS)]
        label  = item.get("label", "")
        before = item.get("before", item.get("value_before", ""))
        after  = item.get("after",  item.get("value_after",  item.get("value", "")))
        unit   = item.get("unit", "")
        cols += f"""
        <div style="flex:1;border:2.5px solid {c};border-radius:12px;padding:22px 16px;
                    display:flex;flex-direction:column;align-items:center;gap:10px;background:{bg}20;">
          <div style="font-size:14px;color:{_C['text2']};font-weight:600;text-align:center;">{label}</div>
          <div style="font-size:22px;color:{_C['text3']};margin-top:4px;">{before}{unit}</div>
          <div style="font-size:14px;color:{_C['text3']};">↓</div>
          <div style="font-size:28px;font-weight:700;color:{c};">{after}{unit}</div>
        </div>"""

    legend = ""
    if left_label or right_label:
        legend = f"""<div style="font-size:12px;color:{_C['text3']};margin-top:10px;text-align:center;">
          <span style="color:{_C['text3']}">회색: {left_label}</span>
          &nbsp;|&nbsp;
          <span style="color:{_C['blue_mid']}">컬러: {right_label}</span>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="display:flex;gap:14px;margin-top:8px;">{cols}</div>
  {legend}
  {"<div class='source'>"+source+"</div>" if source else ""}
</div>"""


# ── 2. status_checklist (img4 스타일) ────────────────────────────────
def _tpl_status_checklist(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("items", spec.get("data", []))
    source   = spec.get("source", "")

    rows = ""
    for item in items:
        if isinstance(item, str):
            text   = item
            status = "neutral"
        else:
            text   = item.get("text", item.get("label", str(item)))
            status = item.get("status", item.get("type", "neutral"))

        # 배지 컬러 결정
        bc, btc, bbg = _BADGE_COLORS.get(status,
                       _BADGE_COLORS.get(text[:4], _BADGE_COLORS["neutral"]))
        rows += f"""
        <div style="display:flex;align-items:center;gap:14px;padding:12px 16px;
                    background:{_C['white']};border:1px solid {_C['gray_border']};
                    border-radius:8px;margin-bottom:8px;">
          <span style="background:{bc};color:{btc};padding:5px 14px;border-radius:6px;
                       font-size:13px;font-weight:700;white-space:nowrap;min-width:80px;
                       text-align:center;">{status}</span>
          <span style="font-size:15px;color:{_C['text']};">{text}</span>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="margin-top:12px;">{rows}</div>
  {"<div class='source'>"+source+"</div>" if source else ""}
</div>"""


# ── 3. timeline (img5 스타일) ─────────────────────────────────────────
def _tpl_timeline(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("items", spec.get("data", []))
    source   = spec.get("source", "")
    theme    = spec.get("color_theme", "blue")
    c_dark, c_mid, _ = _THEME_COLORS.get(theme, _THEME_COLORS["blue"])

    n = len(items)
    if n == 0:
        return "<div class='wrap'><div class='title'>데이터 없음</div></div>"

    # items: str 또는 {date, label, highlight}
    parsed = []
    for item in items:
        if isinstance(item, str):
            parts = item.split(":", 1)
            parsed.append({"date": parts[0].strip(), "label": parts[1].strip() if len(parts) > 1 else "", "highlight": False})
        else:
            parsed.append({
                "date":      item.get("date", item.get("label", "")),
                "label":     item.get("label", item.get("text", "")),
                "highlight": item.get("highlight", False),
            })

    dot_size = 18
    dot_items = ""
    label_items = ""
    date_items = ""
    w_pct = 100 / n

    for i, p in enumerate(parsed):
        is_hl = p["highlight"] or i == len(parsed) - 1
        dot_color = c_mid if is_hl else "#bdbdbd"
        text_color = c_mid if is_hl else _C["text"]
        date_color = c_dark if is_hl else _C["text2"]
        border = f"3px solid {_C['white']}" if is_hl else "none"
        date_items += f"""<div style="width:{w_pct}%;text-align:center;font-size:14px;
                              font-weight:{'700' if is_hl else '400'};color:{date_color};">{p['date']}</div>"""
        label_items += f"""<div style="width:{w_pct}%;text-align:center;font-size:14px;
                               font-weight:{'700' if is_hl else '400'};color:{text_color};
                               white-space:pre-line;">{p['label']}</div>"""
        dot_items += f"""<div style="width:{w_pct}%;display:flex;justify-content:center;">
          <div style="width:{dot_size}px;height:{dot_size}px;border-radius:50%;
                      background:{dot_color};border:{border};
                      box-shadow:{'0 0 0 3px '+c_mid+'40' if is_hl else 'none'};"></div>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="margin-top:20px;">
    <div style="display:flex;">{date_items}</div>
    <div style="position:relative;margin:14px 0 14px;">
      <div style="position:absolute;top:50%;left:5%;right:5%;height:3px;
                  background:{c_dark};transform:translateY(-50%);z-index:0;"></div>
      <div style="display:flex;position:relative;z-index:1;">{dot_items}</div>
    </div>
    <div style="display:flex;">{label_items}</div>
  </div>
  {"<div class='source'>"+source+"</div>" if source else ""}
</div>"""


# ── 4. decision_matrix (img8 스타일) ─────────────────────────────────
def _tpl_decision_matrix(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("items", spec.get("data", []))
    source   = spec.get("source", "")
    left_label  = spec.get("left_label", "상황")
    right_label = spec.get("right_label", "추천 행동")

    rows = ""
    for item in items:
        if isinstance(item, dict):
            situation = item.get("situation", item.get("label", ""))
            action    = item.get("action",    item.get("text",  ""))
            status    = item.get("status",    item.get("type",  "info"))
        else:
            parts = str(item).split("→", 1)
            situation = parts[0].strip()
            action    = parts[1].strip() if len(parts) > 1 else ""
            status    = "info"

        bc, btc, bbg = _BADGE_COLORS.get(status,
                       _BADGE_COLORS.get(action[:4], _BADGE_COLORS["info"]))
        rows += f"""
        <div style="display:flex;align-items:center;border:1px solid {_C['gray_border']};
                    border-radius:6px;margin-bottom:8px;overflow:hidden;">
          <div style="flex:1;padding:14px 20px;font-size:15px;color:{_C['text']};
                      background:{_C['white']};">{situation}</div>
          <div style="padding:0 20px;">
            <div style="background:{bbg};border:2px solid {bc};color:{bc};
                        padding:8px 20px;border-radius:6px;font-size:14px;
                        font-weight:700;white-space:nowrap;">{action}</div>
          </div>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="display:flex;gap:16px;margin:14px 0 8px;">
    <div style="background:{_C['blue_dark']};color:#fff;padding:5px 16px;
                border-radius:6px;font-size:14px;font-weight:700;">{left_label}</div>
    <div style="flex:1;"></div>
    <div style="background:{_C['blue_dark']};color:#fff;padding:5px 16px;
                border-radius:6px;font-size:14px;font-weight:700;">{right_label}</div>
  </div>
  <div>{rows}</div>
  {"<div class='source'>"+source+"</div>" if source else ""}
</div>"""


# ── 5. dark_summary (img9 스타일) ─────────────────────────────────────
def _tpl_dark_summary(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("items", spec.get("data", []))
    source   = spec.get("source", "")

    # 배지 컬러 순환
    badge_colors_dark = [
        ("#5c6bc0", "#3949ab"),  # blue
        ("#66bb6a", "#43a047"),  # green
        ("#ffa726", "#ef6c00"),  # orange
    ]

    rows = ""
    for i, item in enumerate(items):
        if isinstance(item, str):
            parts = item.split(":", 1)
            label = parts[0].strip()
            text  = parts[1].strip() if len(parts) > 1 else item
        else:
            label = item.get("label", item.get("category", ""))
            text  = item.get("text", item.get("value", ""))

        bc, border_c = badge_colors_dark[i % len(badge_colors_dark)]
        rows += f"""
        <div style="display:flex;align-items:center;gap:20px;padding:14px 0;
                    border-bottom:1px solid rgba(255,255,255,0.1);">
          <div style="background:transparent;border:2px solid {border_c};
                      color:{bc};padding:8px 18px;border-radius:8px;
                      font-size:15px;font-weight:700;min-width:80px;
                      text-align:center;flex-shrink:0;">{label}</div>
          <div style="color:rgba(255,255,255,0.85);font-size:15px;">—&nbsp;&nbsp;{text}</div>
        </div>"""

    source_line = f'<div style="font-size:12px;color:rgba(255,255,255,0.4);text-align:right;margin-top:14px;">{source}</div>' if source else ""

    return f"""<div class="wrap" style="background:#0d1b2a;border-radius:0;">
  <div style="font-size:24px;font-weight:700;text-align:center;color:#ffffff;
              margin-bottom:8px;">{title}</div>
  {"<div style='font-size:14px;color:rgba(255,255,255,0.5);text-align:center;margin-bottom:20px;'>"+subtitle+"</div>" if subtitle else "<div style='margin-bottom:20px;'></div>"}
  <div>{rows}</div>
  {source_line}
</div>"""


# ── 6. cost_breakdown (img10 스타일) ──────────────────────────────────
def _tpl_cost_breakdown(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("data", spec.get("items", []))
    source   = spec.get("source", "")
    total    = spec.get("total", "")
    total_label = spec.get("total_label", "총 예상 비용")

    bar_colors = [
        _C["blue_mid"], "#1e88e5", _C["green_mid"],
        _C["orange_mid"], "#8e24aa", _C["text2"],
    ]

    if not items:
        return "<div class='wrap'><div class='title'>데이터 없음</div></div>"

    max_val = max((float(str(it.get("value", 1)).replace(",", "").replace("원", "").replace("달러", "")) for it in items if isinstance(it, dict)), default=1)

    rows = ""
    for i, item in enumerate(items):
        if isinstance(item, dict):
            label = item.get("label", "")
            value = item.get("value", 0)
            unit  = item.get("unit", "")
        else:
            label = str(item)
            value = 0
            unit  = ""

        try:
            num = float(str(value).replace(",", "").replace("원", "").replace("달러", ""))
        except Exception:
            num = 0

        bar_w = max(4, int(num / max_val * 85)) if max_val > 0 else 4
        bc = bar_colors[i % len(bar_colors)]
        rows += f"""
        <div style="display:flex;align-items:center;gap:14px;padding:10px 0;
                    border-bottom:1px solid {_C['gray_border']};">
          <div style="width:140px;font-size:15px;color:{_C['text']};flex-shrink:0;">{label}</div>
          <div style="flex:1;display:flex;align-items:center;gap:10px;">
            <div style="width:{bar_w}%;height:22px;background:{bc};border-radius:3px;
                        min-width:8px;"></div>
          </div>
          <div style="font-size:15px;font-weight:600;color:{bc};
                      white-space:nowrap;min-width:90px;text-align:right;">{value}{unit}</div>
        </div>"""

    total_row = ""
    if total:
        total_row = f"""
        <div style="display:flex;justify-content:space-between;align-items:center;
                    padding:14px 0 2px;border-top:2px solid {_C['text2']};">
          <div style="font-size:16px;font-weight:700;color:{_C['text']};">{total_label}</div>
          <div style="font-size:12px;color:{_C['text3']};flex:1;text-align:center;">
            {source}</div>
          <div style="font-size:20px;font-weight:700;color:{_C['blue_dark']};">{total}</div>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="margin-top:12px;">{rows}</div>
  {total_row}
  {"<div class='source'>"+source+"</div>" if not total and source else ""}
</div>"""


# ── 7. kpi_cards (숫자 KPI 그리드) ───────────────────────────────────
def _tpl_kpi_cards(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    km       = spec.get("key_message", "")
    items    = spec.get("data", [])
    theme    = spec.get("color_theme", "blue")
    c_dark, c_mid, c_light = _THEME_COLORS.get(theme, _THEME_COLORS["blue"])

    n = len(items)
    cols = min(n, 4) if n > 0 else 3

    cards = ""
    for item in items:
        label = item.get("label", "")
        value = item.get("value", "")
        unit  = item.get("unit",  "")
        hl    = item.get("highlight", False)
        diff  = item.get("diff", item.get("change", ""))
        diff_pos = item.get("diff_positive", True)

        bg_col    = c_light if hl else _C["gray_bg"]
        num_color = c_dark  if hl else c_mid
        bdr       = f"2px solid {c_mid}" if hl else f"1px solid {_C['gray_border']}"

        diff_html = ""
        if diff:
            dc = _C["green_mid"] if diff_pos else _C["red_mid"]
            arrow = "▲" if diff_pos else "▼"
            diff_html = f'<div style="font-size:13px;color:{dc};margin-top:4px;">{arrow} {diff}</div>'

        cards += f"""
        <div style="background:{bg_col};border:{bdr};border-radius:12px;
                    padding:20px 16px;text-align:center;">
          <div style="font-size:13px;color:{_C['text2']};margin-bottom:8px;">{label}</div>
          <div style="font-size:32px;font-weight:700;color:{num_color};line-height:1.1;">{value}</div>
          <div style="font-size:13px;color:{_C['text3']};margin-top:4px;">{unit}</div>
          {diff_html}
        </div>"""

    km_html = ""
    if km:
        km_html = f"""<div style="background:{c_light};border-left:4px solid {c_mid};
                          padding:10px 16px;margin-top:16px;border-radius:0 6px 6px 0;
                          font-size:14px;color:{c_dark};font-weight:600;">{km}</div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="display:grid;grid-template-columns:repeat({cols},1fr);gap:14px;margin-top:12px;">
    {cards}
  </div>
  {km_html}
</div>"""


# ── 8. highlight_card / insight_card ─────────────────────────────────
def _tpl_highlight_card(spec: dict) -> str:
    title = spec.get("title", "")
    km    = spec.get("key_message", spec.get("text", ""))
    theme = spec.get("color_theme", "blue")
    c_dark, c_mid, c_light = _THEME_COLORS.get(theme, _THEME_COLORS["blue"])
    items = spec.get("items", spec.get("data", []))

    points = ""
    for item in items:
        t = item.get("text", item.get("label", str(item))) if isinstance(item, dict) else str(item)
        points += f'<div style="padding:8px 0;font-size:15px;color:{_C["text"]};border-bottom:1px solid {_C["gray_border"]};">· {t}</div>'

    return f"""<div class="wrap" style="background:{c_light};">
  <div style="background:{c_dark};color:#fff;padding:14px 20px;margin:-32px -40px 24px;
              font-size:20px;font-weight:700;">{title}</div>
  {"<div style='font-size:17px;font-weight:600;color:"+c_dark+";margin-bottom:16px;line-height:1.6;'>"+km+"</div>" if km else ""}
  {points}
</div>"""


# ── 9. scenario_cards ─────────────────────────────────────────────────
def _tpl_scenario_cards(spec: dict) -> str:
    title    = spec.get("title", "")
    subtitle = spec.get("subtitle", "")
    items    = spec.get("items", spec.get("data", []))

    scn_colors = [
        (_C["green_dark"], _C["green_mid"], _C["green_light"]),
        (_C["blue_dark"],  _C["blue_mid"],  _C["blue_light"]),
        (_C["red_dark"],   _C["red_mid"],   _C["red_light"]),
    ]

    cards = ""
    for i, item in enumerate(items):
        c_dark, c_mid, c_light = scn_colors[i % len(scn_colors)]
        if isinstance(item, str):
            parts = item.split(":", 1)
            label = parts[0].strip()
            text  = parts[1].strip() if len(parts) > 1 else ""
        else:
            label = item.get("label", item.get("scenario", f"시나리오 {i+1}"))
            text  = item.get("text", item.get("description", ""))

        cards += f"""
        <div style="background:{c_light};border-top:4px solid {c_mid};border-radius:10px;
                    padding:18px 20px;flex:1;">
          <div style="font-size:14px;font-weight:700;color:{c_dark};margin-bottom:10px;
                      background:{c_mid};color:#fff;padding:4px 12px;border-radius:4px;
                      display:inline-block;">{label}</div>
          <div style="font-size:14px;color:{_C['text']};line-height:1.6;margin-top:8px;">{text}</div>
        </div>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <div style="display:flex;gap:16px;margin-top:14px;">{cards}</div>
</div>"""


# ── 10. comparison_table (좌우 비교) ──────────────────────────────────
def _tpl_comparison_table(spec: dict) -> str:
    title       = spec.get("title", "")
    left_label  = spec.get("left_label",  "A")
    right_label = spec.get("right_label", "B")
    left_items  = spec.get("left_items",  [])
    right_items = spec.get("right_items", [])
    subtitle    = spec.get("subtitle", "")

    max_n = max(len(left_items), len(right_items))

    rows = ""
    for i in range(max_n):
        lt = left_items[i]  if i < len(left_items)  else ""
        rt = right_items[i] if i < len(right_items) else ""
        bg = _C["gray_bg"] if i % 2 == 0 else _C["white"]
        rows += f"""
        <tr style="background:{bg};">
          <td style="padding:12px 16px;font-size:14px;color:{_C['text']};
                     border-right:2px solid {_C['gray_border']};">{lt}</td>
          <td style="padding:12px 16px;font-size:14px;color:{_C['text']};">{rt}</td>
        </tr>"""

    return f"""<div class="wrap">
  <div class="title">{title}</div>
  {"<div class='subtitle'>"+subtitle+"</div>" if subtitle else ""}
  <table style="width:100%;border-collapse:collapse;margin-top:14px;
                border:1px solid {_C['gray_border']};border-radius:8px;overflow:hidden;">
    <thead>
      <tr>
        <th style="background:{_C['blue_dark']};color:#fff;padding:12px 16px;
                   font-size:15px;font-weight:700;text-align:left;
                   width:50%;border-right:2px solid rgba(255,255,255,0.2);">{left_label}</th>
        <th style="background:{_C['blue_dark']};color:#fff;padding:12px 16px;
                   font-size:15px;font-weight:700;text-align:left;">{right_label}</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


# ── dispatch 테이블 ───────────────────────────────────────────────────
_DISPATCH_TPL.update({
    "comparison_kpi":    _tpl_comparison_kpi,
    "status_checklist":  _tpl_status_checklist,
    "checklist":         _tpl_status_checklist,
    "timeline":          _tpl_timeline,
    "decision_matrix":   _tpl_decision_matrix,
    "dark_summary":      _tpl_dark_summary,
    "cost_breakdown":    _tpl_cost_breakdown,
    "kpi_cards":         _tpl_kpi_cards,
    "highlight_card":    _tpl_highlight_card,
    "insight_card":      _tpl_highlight_card,
    "scenario_cards":    _tpl_scenario_cards,
    "comparison_table":  _tpl_comparison_table,
    # fallback for chart types not handled here → caller should use matplotlib
    "infographic":       _tpl_kpi_cards,
})


# ══════════════════════════════════════════════════════════════════════
# Playwright 스크린샷
# ══════════════════════════════════════════════════════════════════════

def _find_chromium() -> str:
    """사용 가능한 Chromium 실행 파일 경로 반환."""
    for c in _CHROMIUM_CANDIDATES:
        if os.path.isfile(c):
            return c
    # Playwright 기본 경로 자동 탐색
    try:
        from playwright._impl._driver import compute_driver_executable
        import subprocess
        result = subprocess.run(
            [str(compute_driver_executable()), "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        pass
    raise RuntimeError(
        "Chromium 실행 파일을 찾을 수 없습니다. "
        "python -m playwright install chromium 을 실행하세요."
    )


def _screenshot(html: str, out_path: Path) -> Path:
    """HTML 문자열 → Playwright 스크린샷 → JPG 저장."""
    from playwright.sync_api import sync_playwright

    chromium_path = _find_chromium()
    jpg_path = out_path.with_suffix(".jpg")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chromium_path,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 1055, "height": 600})
        page.set_content(html, wait_until="domcontentloaded")

        # 콘텐츠 높이에 맞게 클리핑 (최소 300, 최대 900)
        content_h = page.evaluate(
            "() => Math.min(900, Math.max(300, document.body.scrollHeight + 20))"
        )
        page.set_viewport_size({"width": 1055, "height": content_h})

        # PNG로 캡처 후 JPEG 변환 (Pillow)
        png_bytes = page.screenshot(full_page=False)
        browser.close()

    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        img.save(str(jpg_path), "JPEG", quality=92, optimize=True)
    except ImportError:
        # Pillow 없으면 PNG 그대로 저장
        jpg_path = out_path.with_suffix(".png")
        jpg_path.write_bytes(png_bytes)

    log.info(f"[html_renderer] ✅ {out_path.stem} → {jpg_path.name}")
    return jpg_path


__all__ = ["render"]
