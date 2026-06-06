"""JARVIS06_IMAGE/section_title.py — 소제목 배너 이미지 생성 (jarvis_main에서 이관)."""
from __future__ import annotations
import logging
import json
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger("jarvis")


def _get_dynamic_color(seed: str = "", fallback: str = "#1976D2") -> str:
    """제목을 seed로 동적 색상 생성 (BLOG_SUPREME_LAW 제11조)."""
    try:
        from shared.llm import invoke_text
        prompt = f"Generate 1 harmonious vibrant hex color for section title '{seed[:30]}'. Return JSON: {{\"color\": \"#xxxxxx\"}}"
        result = invoke_text("writer_fast", prompt, temperature=0.7, max_tokens=50)
        data = json.loads(result)
        color = data.get("color", fallback)
        # hex 형식 검증
        return color if color.startswith("#") and len(color) == 7 else fallback
    except Exception as e:
        log.warning(f"[SectionTitle] 동적 색상 생성 실패: {e}")
        return fallback


def make_section_title_image(title: str, save_path: str,
                              level: int = 2, number: int = 0) -> bool:
    """소제목 배너 이미지 생성 — 번호 박스 + 다크 네이비 스타일."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        plt.rcParams['font.family'] = ['AppleGothic', 'Apple SD Gothic Neo', 'sans-serif']
        plt.rcParams['axes.unicode_minus'] = False

        BG_DARK   = '#0D1117'
        TXT_W     = '#E6EDF3'
        LINE_C    = '#2A4080'
        BOX_COLOR = _get_dynamic_color(seed=title, fallback='#1976D2')

        if level == 2:
            fig_h = 1.8
            fig = plt.figure(figsize=(10, fig_h), facecolor=BG_DARK)
            ax  = fig.add_axes([0, 0, 1, 1])
            ax.set_xlim(0, 10); ax.set_ylim(0, fig_h)
            ax.axis('off')
            ax.add_patch(mpatches.Rectangle((0, 0), 10, fig_h, color=BG_DARK))
            box_w = 1.6
            ax.add_patch(mpatches.Rectangle((0, 0), box_w, fig_h, color=BOX_COLOR))
            num_str = f"{number:02d}" if number > 0 else "○"
            ax.text(box_w / 2, fig_h * 0.58, num_str,
                    ha='center', va='center', fontsize=28, fontweight='bold', color=TXT_W)
            ax.text(box_w / 2, fig_h * 0.20, 'SECTION',
                    ha='center', va='center', fontsize=7, color=TXT_W, alpha=0.75)
            ax.text(box_w + 0.4, fig_h * 0.58, title,
                    ha='left', va='center', fontsize=36, fontweight='bold', color=TXT_W)
            line_y = fig_h * 0.26
            ax.plot([box_w + 0.4, 9.8], [line_y, line_y],
                    color=LINE_C, linewidth=0.8, alpha=0.9)
        else:
            BG_H3  = '#F0F4FF'
            ACC_H3 = '#3B82F6'
            TXT_H3 = '#1E3A5F'
            fig_h  = 0.9
            fig = plt.figure(figsize=(10, fig_h), facecolor=BG_H3)
            ax  = fig.add_axes([0, 0, 1, 1])
            ax.set_xlim(0, 10); ax.set_ylim(0, fig_h)
            ax.axis('off')
            ax.add_patch(mpatches.Rectangle((0, 0), 10, fig_h, color=BG_H3))
            ax.add_patch(mpatches.Rectangle((0, 0), 0.12, fig_h, color=ACC_H3))
            ax.text(0.37, fig_h / 2, title,
                    ha='left', va='center', fontsize=26, fontweight='bold', color=TXT_H3)

        plt.savefig(save_path, dpi=120, bbox_inches='tight',
                    facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        log.info(f"[SectionTitle] 생성: {Path(save_path).name}")
        return True
    except Exception as e:
        log.warning(f"[SectionTitle] 생성 실패 ({title}): {e}")
        _g_report("image", e, module=__name__)
        return False


__all__ = ["make_section_title_image"]
