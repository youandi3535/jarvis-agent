"""JARVIS06_IMAGE/trend_charts.py — 트렌드 경제 브리핑 차트 생성 (trend_economic_writer에서 이관)."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_OUTPUT_DIR = Path(__file__).resolve().parent / "output"
_MPL_READY  = False
_DOW_KR     = ['월', '화', '수', '목', '금', '토', '일']


def _now():
    return datetime.now()


def _today_str():
    return _now().strftime("%Y-%m-%d")


def _today_dow():
    return _DOW_KR[_now().weekday()]


def _mpl_setup():
    global _MPL_READY
    if _MPL_READY:
        return
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from pathlib import Path as _P
    candidates = [
        '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
        '/usr/share/fonts/truetype/nanum/NanumGothic.ttf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    ]
    for c in candidates:
        if _P(c).exists():
            import matplotlib.font_manager as fm
            prop = fm.FontProperties(fname=c)
            plt.rcParams['font.family'] = prop.get_name()
            break
    plt.rcParams['axes.unicode_minus'] = False
    _MPL_READY = True


def _out(out_dir) -> Path:
    p = Path(out_dir) if out_dir else _OUTPUT_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _try_svg(data: dict, chart_type: str, title: str, out_dir) -> str:
    """ClaudeSVGProvider로 SVG 차트 생성 시도. PNG 변환 실패 or 오류 시 '' 반환 → matplotlib 폴백."""
    try:
        from JARVIS06_IMAGE.providers.claude_svg_provider import ClaudeSVGProvider
        path = ClaudeSVGProvider().generate(data, chart_type, title, _out(out_dir))
        if str(path).endswith('.svg'):  # cairosvg PNG 변환 실패 → matplotlib 폴백
            return ""
        return str(path)
    except Exception as e:
        print(f"  ⚠️ SVG 생성 실패 → matplotlib 폴백: {type(e).__name__}: {e}")
        _g_report("image", e, module=__name__)
        return ""


# ★ 더이상 고정 팔레트 사용 안 함 — 동적 생성만 (매번 다른 색상)
_SECTOR_COLORS_LEGACY = {  # 폴백용 (삭제 예정)
    '경제·경기':   '#00D4AA',
    '금융·투자':   '#F0B429',
    '에너지·환경': '#FF9F43',
    'IT·테크':     '#8A5CF7',
    '금융·은행':   '#00A8FF',
    '주식·투자':   '#F0B429',
    '부동산':      '#FF6B6B',
    '기술·IT':     '#8A5CF7',
    '에너지·자원': '#FF9F43',
    '무역·통상':   '#26C281',
    '정책·규제':   '#A29BFE',
    '산업·기업':   '#FDCB6E',
    '글로벌·해외': '#74B9FF',
}

# ★ 동적 색상 생성 미사용 시 폴백 (SECTOR_COLORS는 동적 생성으로 매번 다름)
_SECTOR_COLORS = _SECTOR_COLORS_LEGACY


def _get_dynamic_sector_color(sector: str, keyword: str = "") -> str:
    """섹터·키워드에 맞는 동적 주색상 생성. 매번 호출할 때마다 다른 색상."""
    try:
        from JARVIS06_IMAGE.style_engine import generate_sector_colors
        palette = generate_sector_colors(sector, keyword)
        return palette.get("primary_color", _SECTOR_COLORS_LEGACY.get(sector, '#00C9A7'))
    except Exception:
        return _SECTOR_COLORS_LEGACY.get(sector, '#00C9A7')

_KO_EN_MAP = {
    '유튜버': 'YouTuber content creator', '크리에이터': 'digital content creator',
    'MCN': 'multi-channel network media', '구독': 'subscription growth',
    '수익': 'revenue earnings profit', '광고': 'digital advertising',
    '인플레이션': 'inflation economy', '금리': 'interest rate Federal Reserve',
    '환율': 'currency exchange rate dollar', '주가': 'stock market chart',
    '코스피': 'Korean stock market KOSPI', '나스닥': 'NASDAQ technology stocks',
    '반도체': 'semiconductor chip factory', '인공지능': 'artificial intelligence AI',
    '부동산': 'real estate property market', '전기차': 'electric vehicle EV',
    '배터리': 'battery energy storage', '스타트업': 'startup innovation tech',
    '플랫폼': 'digital platform ecosystem', '글로벌': 'global world economy',
    '투자': 'investment finance growth', '소비': 'consumer spending retail',
    '수출': 'export trade shipping', '제조': 'manufacturing industry factory',
    '2차전지': 'lithium battery energy storage',
}


def _ko_to_en_prompt(keyword: str, section_text: str) -> str:
    en_keyword = _KO_EN_MAP.get(keyword, keyword)
    extra = []
    for ko, en in _KO_EN_MAP.items():
        if ko in section_text and ko != keyword:
            extra.append(en.split()[0])
    extra_str = ', '.join(extra[:3])
    return (
        f"Professional infographic illustration about {en_keyword}"
        f"{', ' + extra_str if extra_str else ''}, "
        "modern flat design, clean corporate style, "
        "data visualization aesthetic, blue and gold color palette, "
        "no text, no letters, high quality"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  썸네일
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_trend_thumbnail(keyword: str, sector: str, platform: str = 'naver',
                         market: dict = None, out_dir=None) -> str:
    """AI 배경 + PIL 텍스트 합성 썸네일. 실패 시 matplotlib 폴백. 동적 색상 적용.

    ★ 사용자 박제 2026-05-15 — 5-axis 다양화 + 결정적 seed 제거.
    """
    from JARVIS06_IMAGE.style_engine import generate_sector_colors
    from JARVIS06_IMAGE.thumbnail_maker import _pick_style_hints, _unique_token
    import random as _rnd, time as _time

    import io as _io
    today_str = _today_str()
    today_dow = _today_dow()
    # ★ 동적 주색상 생성 (매번 다른 스타일)
    palette = generate_sector_colors(sector, keyword)
    accent    = palette.get("primary_color", '#00C9A7')
    img_dir   = _out(out_dir)
    # ★ 파일명에 unix timestamp + 변이 토큰 — 같은 키워드라도 매번 새 파일 (캐시 차단)
    _utok = _unique_token()
    _ts = int(_time.time()) % 100000
    out_path  = img_dir / f'trend_{platform}_thumb_{today_str}_{_ts}_{_utok}.png'

    _SECTOR_EN = {
        '경제·경기': 'macroeconomics market trends',
        '금융·투자': 'finance investment portfolio',
        '에너지·환경': 'energy environment sustainability',
        'IT·테크': 'technology digital innovation',
        '금융·은행': 'banking financial district',
        '주식·투자': 'stock market trading floor',
        '부동산': 'real estate architecture',
        '기술·IT': 'tech startup digital',
        '에너지·자원': 'energy resources industrial',
        '무역·통상': 'global trade commerce',
        '정책·규제': 'government policy regulation',
        '산업·기업': 'corporate industry manufacturing',
        '글로벌·해외': 'global world economy',
    }
    sector_en = _SECTOR_EN.get(sector, 'financial market economy')
    # ★ 결정적 md5 seed 제거 — 매번 random (사용자 박제 2026-05-15)
    # 옛 사고: 같은 키워드/날짜/플랫폼 = 같은 seed = Pollinations 같은 이미지 캐시.
    seed = _rnd.randint(1, 999_999_999)
    # ★ 5-axis 다양화 — thumbnail_maker 와 동일 풀 공유 (174,960 조합)
    style_hint, mood_hint, angle_hint, palette_hint, time_hint = _pick_style_hints()

    # Claude가 키워드·섹터·5-axis 를 모두 반영해 동적 prompt 창작
    try:
        from shared.llm import invoke_text as _inv
        _dynamic_req = (
            f"[Variation token: {_utok} — produce a DIFFERENT image each time]\n\n"
            f"Blog topic: '{keyword}' (sector: {sector_en}).\n"
            f"Style direction (use ALL 5 axes — do not pick anything else):\n"
            f"  • Visual style:   {style_hint}\n"
            f"  • Mood/lighting:  {mood_hint}\n"
            f"  • Composition:    {angle_hint}\n"
            f"  • Color palette:  {palette_hint}\n"
            f"  • Time of day:    {time_hint}\n\n"
            "Write a unique image prompt for a blog thumbnail background that follows ALL 5 style "
            "axes above and captures the essence of this specific topic creatively. "
            "Be vivid and specific. Avoid generic 'cinematic financial' clichés. "
            "Output the prompt text only — no title, no label, no markdown. "
            "English only. No text, no logos, no people. 1 paragraph, under 60 words."
        )
        _raw = _inv("writer_fast", _dynamic_req, max_tokens=120, temperature=0.95) or ""
        _lines = [l.strip() for l in _raw.strip().splitlines()
                  if l.strip() and not l.strip().startswith("#")]
        bg_prompt = " ".join(_lines) if _lines else ""
    except Exception:
        bg_prompt = ""
    if not bg_prompt or len(bg_prompt) < 10:
        # ★ 폴백도 5-axis + 변이 토큰 — 항상 다른 결과 (옛 hard-coded 폐기)
        bg_prompt = (
            f"{style_hint}, {mood_hint}, {angle_hint}, {palette_hint}, {time_hint}, "
            f"{sector_en} concept inspired by '{keyword}', variation_{_utok}, "
            f"professional blog thumbnail, no text no logo, 4k ultra hd"
        )
    # 이미지 API 캐시 회피 — prompt 끝에 변이 토큰
    bg_prompt = f"{bg_prompt} [variation:{_utok}]"

    try:
        from PIL import Image, ImageDraw, ImageFont
        import tempfile as _tmp2, re as _re, io as _io
        from JARVIS06_IMAGE.image_agent import generate_photo as _gen_photo
        from shared.llm import invoke_text as _inv2

        print(f"  🌐 [{platform.upper()}] AI 배경 생성 중 (JARVIS06_IMAGE)...")
        _tmp_dir = Path(_tmp2.mkdtemp())
        _bg_path = _gen_photo(
            prompt_ko="", prompt_en=bg_prompt,
            out_dir=_tmp_dir,
            width=1800, height=1012, seed=seed,
        )
        print(f"  ✅ AI 배경 생성 완료")

        W, H = 1800, 1012
        bg = Image.open(str(_bg_path)).convert('RGBA').resize((W, H), Image.LANCZOS)

        # Claude가 오버레이 SVG를 매번 새롭게 창작
        _market_lines = ""
        if market:
            _market_lines = " | ".join(
                f"{k}: {'+' if v.get('change',0)>=0 else ''}{v.get('change',0):.1f}%"
                for k, v in list(market.items())[:5]
            )
        _svg_prompt = (
            f"Create a transparent SVG overlay (1800x1012px) for a Korean blog thumbnail.\n"
            f"Topic keyword: '{keyword}'\n"
            f"Sector: {sector}\n"
            f"Today: {today_str} ({today_dow}요일)\n"
            f"Market data: {_market_lines}\n\n"
            "Design rules:\n"
            "- SVG background must be fully transparent (no <rect fill> covering the whole canvas)\n"
            "- The AI photo background will show through — only overlay text and decorative shapes\n"
            "- Make the keyword '{keyword}' the dominant visual element (large, bold, prominent)\n"
            "- Use semi-transparent dark panels/gradients behind text so it's readable\n"
            "- Include: keyword (very large), sector label, today's date, a catchy Korean tagline related to the topic\n"
            "- Color scheme: choose colors that match the topic's mood (don't always use the same colors)\n"
            "- Layout: be creative — left-aligned, centered, split, diagonal, asymmetric — vary it based on topic\n"
            "- Korean font: font-family='Apple SD Gothic Neo'\n"
            "- All font sizes minimum 28px\n"
            "- Output ONLY the <svg> tag, nothing else. No markdown, no explanation.\n"
        ).replace("'{keyword}'", f"'{keyword}'")
        _svg_raw = _inv2("writer_fast", _svg_prompt, max_tokens=3000, temperature=0.85) or ""
        _svg_match = _re.search(r'(<svg[\s\S]*?</svg>)', _svg_raw, _re.IGNORECASE)
        _svg_text = _svg_match.group(1) if _svg_match else ""

        if _svg_text:
            print(f"  🎨 SVG 오버레이 창작 완료 ({len(_svg_text)}자)")
            # 마크다운 코드펜스·& 이스케이프 정제
            _svg_text = _re.sub(r'^```[a-z]*\n?', '', _svg_text.strip(), flags=_re.MULTILINE)
            _svg_text = _re.sub(r'\n?```$', '', _svg_text.strip(), flags=_re.MULTILINE)
            _svg_text = _re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', _svg_text)
            # 한국어 폰트 강제
            _svg_text = _re.sub(r"font-family\s*=\s*['\"][^'\"]*['\"]",
                                 "font-family='Apple SD Gothic Neo'", _svg_text)
            _svg_text = _re.sub(r"font-family\s*:\s*[^;\"'<\}]+",
                                 "font-family:'Apple SD Gothic Neo'", _svg_text)
            try:
                import cairosvg  # type: ignore
                _png_bytes = cairosvg.svg2png(
                    bytestring=_svg_text.encode(),
                    output_width=W, output_height=H,
                )
                overlay_img = Image.open(_io.BytesIO(_png_bytes)).convert('RGBA').resize((W, H), Image.LANCZOS)
                bg = Image.alpha_composite(bg, overlay_img)
            except Exception as _svg_err:
                print(f"  ⚠️ SVG 렌더링 실패 ({_svg_err}) → PIL 기본 오버레이")
                _g_report("image", _svg_err, module=__name__)
                _svg_text = ""

        if not _svg_text:
            # SVG 실패 시 최소 PIL 폴백 (가독성 보장용)
            def _c(h, a=255):
                h = h.lstrip('#'); return (int(h[0:2],16),int(h[2:4],16),int(h[4:6],16),a)
            def _font(sz):
                for p in ['/System/Library/Fonts/Supplemental/AppleGothic.ttf',
                           '/System/Library/Fonts/AppleGothic.ttf',
                           '/usr/share/fonts/truetype/nanum/NanumGothic.ttf']:
                    if Path(p).exists():
                        try: return ImageFont.truetype(p, sz)
                        except Exception: pass
                return ImageFont.load_default()
            overlay = Image.new('RGBA', (W, H), (0,0,0,130))
            bg = Image.alpha_composite(bg, overlay)
            draw = ImageDraw.Draw(bg)
            ACCENT = _c(accent)
            kw_fs = 130 if len(keyword)<=5 else (105 if len(keyword)<=8 else (84 if len(keyword)<=12 else 67))
            draw.text((W//2, H//2), keyword, font=_font(kw_fs), fill=(255,255,255,255), anchor='mm')
            draw.text((W//2, H//2+kw_fs//2+30), sector, font=_font(36), fill=ACCENT, anchor='mm')
            draw.text((W//2, 60), today_str, font=_font(30), fill=(180,190,200,255), anchor='mm')

        bg.convert('RGB').save(str(out_path), 'PNG', dpi=(150, 150))
        print(f"  🖼️ [{platform.upper()}] AI 썸네일 저장: {out_path.name}")
        return str(out_path)

    except Exception as e:
        import traceback
        print(f"  ⚠️ AI 썸네일 실패 → matplotlib 폴백")
        _g_report("image", e, module=__name__)
        print(f"     원인: {type(e).__name__}: {e}")
        traceback.print_exc()
        return _make_trend_thumbnail_mpl(keyword, sector, platform, market, out_path)


def _make_trend_thumbnail_mpl(keyword: str, sector: str, platform: str,
                               market: dict, out_path: Path) -> str:
    """matplotlib 썸네일 폴백."""
    _mpl_setup()
    today_str = _today_str()
    today_dow = _today_dow()
    try:
        from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        accent   = palette.get("primary_color", '#00C9A7')
        LEFT_BG  = _interpolate_color(palette.get("primary_color", '#060E1F'), '#000000', 0.85)
        RIGHT_BG = _interpolate_color(palette.get("accent_color", '#0F1E3A'), '#000000', 0.75)
        WHITE    = palette.get("bg_color", '#FFFFFF')
        YELLOW   = palette.get("neutral_color", '#FFE234')
        RED      = palette.get("down_color", '#FF2D55')
        GREEN    = palette.get("up_color", '#00E676')
        LGRAY    = palette.get("neutral_color", '#8FA8C8')
        ORANGE   = palette.get("primary_color", '#FF6B35')

        def hex2rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4))

        W, H = 12, 6.75
        fig  = plt.figure(figsize=(W, H), dpi=150)
        ax   = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H)
        ax.axis('off')

        ax.add_patch(mpatches.Rectangle((0,        0), W*0.52, H, color=LEFT_BG,  zorder=0))
        ax.add_patch(mpatches.Rectangle((W*0.52,   0), W*0.48, H, color=RIGHT_BG, zorder=0))

        diag_xs = [W*0.50, W*0.54]
        ax.fill_betweenx([0, H], diag_xs[0], diag_xs[1], color=accent, alpha=0.85, zorder=2)

        ax.add_patch(mpatches.FancyBboxPatch(
            (0.28, H-0.90), 3.5, 0.62,
            boxstyle='round,pad=0.06', facecolor=RED, linewidth=0, zorder=5))
        ax.text(2.03, H-0.58, '  지금 당장 읽어야 할',
                ha='center', va='center', fontsize=17, fontweight='bold', color=WHITE, zorder=6)

        kw_fs = 70 if len(keyword) <= 4 else (56 if len(keyword) <= 6 else
                (44 if len(keyword) <= 9 else (34 if len(keyword) <= 13 else 26)))
        ax.text(W*0.25 + 0.12, H*0.52 - 0.12, keyword,
                ha='center', va='center', fontsize=kw_fs, fontweight='bold',
                color=accent, alpha=0.25, zorder=3)
        ax.text(W*0.25, H*0.52, keyword,
                ha='center', va='center', fontsize=kw_fs, fontweight='bold',
                color=WHITE, zorder=6)
        kw_w = min(len(keyword) * kw_fs * 0.011 + 0.6, W*0.44)
        ax.add_patch(mpatches.Rectangle(
            (W*0.25 - kw_w/2, H*0.52 - kw_fs*0.011 - 0.22),
            kw_w, 0.15, color=accent, zorder=5, alpha=0.95))

        ax.text(W*0.25, H*0.30, f'#{sector}',
                ha='center', va='center', fontsize=19, fontweight='bold', color=accent, zorder=5)
        ax.text(W*0.25, H*0.19, f'{today_str} ({today_dow})',
                ha='center', va='center', fontsize=14, color=LGRAY, zorder=5)

        ax.text(W*0.76, H-0.46, 'JARVIS',
                ha='center', va='center', fontsize=20, fontweight='bold', color=accent, zorder=5)
        ax.text(W*0.76, H-0.72, 'ECONOMIC BRIEFING',
                ha='center', va='center', fontsize=12, color=LGRAY, zorder=5)
        ax.add_patch(mpatches.Rectangle((W*0.57, H-0.82), W*0.38, 0.04,
                                        color=accent, alpha=0.4, zorder=4))

        if market:
            items = list(market.items())[:4]
            card_y_start = H * 0.72
            card_h = 0.55
            card_gap = 0.10
            for i, (name, mdata) in enumerate(items):
                cy   = card_y_start - i * (card_h + card_gap)
                chg  = mdata.get('change', 0)
                clr  = GREEN if chg >= 0 else RED
                sign = '+' if chg >= 0 else ''
                arrow = '▲' if chg >= 0 else '▼'
                ax.add_patch(mpatches.FancyBboxPatch(
                    (W*0.57, cy - card_h*0.5), W*0.38, card_h,
                    boxstyle='round,pad=0.04',
                    facecolor='#162240', linewidth=1, edgecolor=clr, alpha=0.9, zorder=4))
                ax.text(W*0.62, cy + 0.05, name,
                        ha='left', va='center', fontsize=13, color=LGRAY, zorder=6)
                ax.text(W*0.92, cy + 0.05, f"{arrow} {sign}{chg:.2f}%",
                        ha='right', va='center', fontsize=14, fontweight='bold', color=clr, zorder=6)
                ax.text(W*0.76, cy - 0.18, str(mdata.get('value', '')),
                        ha='center', va='center', fontsize=12, color=WHITE, zorder=6)
        else:
            ax.add_patch(mpatches.FancyBboxPatch(
                (W*0.57, H*0.25), W*0.38, H*0.48,
                boxstyle='round,pad=0.08',
                facecolor='#162240', linewidth=1.5, edgecolor=accent, alpha=0.9, zorder=4))
            ax.text(W*0.76, H*0.55, '오늘의 핵심\n경제 이슈',
                    ha='center', va='center', fontsize=22, fontweight='bold',
                    color=WHITE, zorder=6, linespacing=1.4)
            ax.text(W*0.76, H*0.32, f'상세 분석 → {keyword}',
                    ha='center', va='center', fontsize=14, color=accent, zorder=6)

        ax.add_patch(mpatches.Rectangle((0, 0), W, 0.78, color='#0D1A30', zorder=3))
        ax.add_patch(mpatches.Rectangle((0, 0.75), W, 0.04, color=ORANGE, alpha=0.8, zorder=4))
        ax.add_patch(mpatches.FancyBboxPatch(
            (W/2-4.0, 0.12), 8.0, 0.52,
            boxstyle='round,pad=0.06', facecolor=ORANGE, alpha=0.92, linewidth=0, zorder=5))
        ax.text(W/2, 0.38, '▶  지금 읽지 않으면 뒤처진다! 클릭해서 확인하기',
                ha='center', va='center', fontsize=16, fontweight='bold', color=WHITE, zorder=6)

        ax.add_patch(mpatches.FancyBboxPatch(
            (0.20, 0.88), 2.8, 0.44,
            boxstyle='round,pad=0.05', facecolor=RED, linewidth=0, zorder=6))
        ax.text(1.60, 1.10, '★ 투자자 필독',
                ha='center', va='center', fontsize=15, fontweight='bold', color=WHITE, zorder=7)

        fig.savefig(str(out_path), dpi=150, bbox_inches='tight', facecolor=LEFT_BG, pad_inches=0)
        plt.close(fig)
        print(f"  🖼️ [{platform.upper()}] 썸네일 생성: {out_path.name}")
        return str(out_path)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ⚠️ 썸네일 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  섹션 배너 이미지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_section_image(section_title: str, section_num: int, keyword: str,
                       sector: str, platform: str = 'naver',
                       key_points: list = None, out_dir=None) -> str:
    """섹션 구분 배너 — Claude LLM SVG 동적 생성 (제1-B조: 고정 템플릿 금지).

    매번 Claude가 section_title·keyword·sector 기반으로 새로운 SVG 배너를 창작.
    SVG 실패 시 matplotlib 폴백.
    """
    import re as _re2
    today_str = _today_str()
    dest      = _out(out_dir)
    safe_title = _re2.sub(r'[^\w가-힣\s]', '', section_title).replace(' ', '_')[:20].strip('_') or f'sec{section_num}'
    out_path   = dest / f'section_{section_num:02d}_{safe_title}_{today_str}.png'

    # ── Claude LLM SVG 생성 (1순위) ──────────────────────────────────
    try:
        import json as _json
        from JARVIS06_IMAGE.svg_renderer import _generate_svg, _svg_to_png, _PROMPTS

        spec = {
            "section_num":   f"{section_num:02d}",
            "section_title": section_title,
            "keyword":       keyword,
            "sector":        sector,
            "today":         today_str,
            "key_points":    key_points or [],
        }
        prompt = _PROMPTS["section_banner"].format(spec_json=_json.dumps(spec, ensure_ascii=False))
        svg_code = _generate_svg(prompt)
        if svg_code:
            svg_path = out_path.with_suffix('.svg')
            svg_path.write_text(svg_code, encoding='utf-8')
            if _svg_to_png(svg_path, out_path):
                print(f"  ✅ 섹션 배너 [Claude SVG] {section_num:02d}: {out_path.name}")
                return str(out_path)
            # PNG 변환 실패 → SVG 반환
            print(f"  ✅ 섹션 배너 [Claude SVG, PNG실패] {section_num:02d}: {svg_path.name}")
            return str(svg_path)
    except Exception as e:
        print(f"  ⚠️ 섹션 배너 Claude SVG 실패({e}), matplotlib 폴백")
        _g_report("image", e, module=__name__)

    # ── matplotlib 폴백 (SVG 실패 시만) ─────────────────────────────
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch

        accent   = '#F0B429'
        DARK_BG  = '#0D1B2A'
        LIGHT_BG = '#F8F9FC'
        WHITE    = '#FFFFFF'
        DARK_TXT = '#0D1B2A'
        MID_TXT  = '#4A5568'

        W, H = 14, 3.2
        fig = plt.figure(figsize=(W, H), dpi=130, facecolor=LIGHT_BG)
        ax  = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H)
        ax.axis('off')

        PANEL_W = 3.8
        ax.add_patch(mpatches.Rectangle((0, 0), W, H, color=LIGHT_BG, zorder=0))
        ax.add_patch(mpatches.Rectangle((0, 0), PANEL_W, H, color=DARK_BG, zorder=1))
        ax.add_patch(mpatches.Rectangle((0, H - 0.18), PANEL_W, 0.18, color=accent, zorder=2))
        ax.text(PANEL_W / 2, H * 0.72, 'S E C T I O N',
                ha='center', va='center', fontsize=14, color=accent, fontweight='bold', zorder=4)
        ax.text(PANEL_W / 2, H * 0.42, f'{section_num:02d}',
                ha='center', va='center', fontsize=68, fontweight='bold', color=WHITE, zorder=4)
        ax.text(PANEL_W / 2, H * 0.13, f'# {keyword}',
                ha='center', va='center', fontsize=16, color=accent, fontweight='bold', zorder=4)
        ax.add_patch(mpatches.Rectangle((PANEL_W, H * 0.15), 0.06, H * 0.7, color=accent, zorder=3))

        tag_x = PANEL_W + 0.36
        tag_box = FancyBboxPatch(
            (tag_x - 0.1, H * 0.76), len(sector) * 0.16 + 0.3, 0.34,
            boxstyle='round,pad=0.05', linewidth=0, facecolor=accent, alpha=0.15, zorder=3)
        ax.add_patch(tag_box)
        ax.text(tag_x + len(sector) * 0.08 + 0.05, H * 0.93, sector,
                ha='center', va='center', fontsize=18, color=accent, fontweight='bold', zorder=4)
        title_fs = (38 if len(section_title) <= 10 else 34 if len(section_title) <= 14 else
                    30 if len(section_title) <= 20 else 26 if len(section_title) <= 28 else 22)
        ax.text(tag_x, H * 0.52, section_title,
                ha='left', va='center', fontsize=title_fs, fontweight='bold', color=DARK_TXT, zorder=4)
        underline_w = min(len(section_title) * title_fs * 0.009 + 0.5, W - PANEL_W - 0.5)
        ax.add_patch(mpatches.Rectangle((tag_x, H * 0.32), underline_w, 0.07, color=accent, zorder=4))
        ax.text(tag_x, H * 0.18, f'{today_str}  ·  트렌드 분석',
                ha='left', va='center', fontsize=16, color=MID_TXT, zorder=4)
        ax.add_patch(mpatches.Rectangle((PANEL_W, 0), W - PANEL_W, 0.08, color=accent, alpha=0.35, zorder=3))

        fig.savefig(str(out_path), dpi=130, bbox_inches='tight', facecolor=LIGHT_BG)
        plt.close(fig)
        print(f"  ✅ 섹션 배너 [matplotlib 폴백] {section_num:02d}: {out_path.name}")
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 섹션 이미지 생성 완전 실패 (섹션{section_num}): {e}")
        _g_report("image", e, module=__name__)
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  콘텐츠 차트 함수들
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_market_chart(market: dict, keyword: str, sector: str,
                      card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """시장 데이터 KPI 카드 + 등락률 바 차트 — 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

    today_str = _today_str()
    items = list(market.items())
    _svg = _try_svg({
        "labels": [n for n, _ in items],
        "values": [d.get('change', 0) for _, d in items],
        "prices": [str(d.get('value', '')) for _, d in items],
    }, "bar", f"글로벌 시장 현황 | {keyword}", out_dir)
    if _svg:
        print(f"  📊 시장 차트 생성 (SVG): {Path(_svg).name}")
        return _svg
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import matplotlib.gridspec as gridspec

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        RED    = palette.get("down_color", '#E24B4A')
        GREEN  = palette.get("up_color", '#1D9E75')
        BG     = palette.get("bg_color", '#FFFFFF')
        PANEL  = _interpolate_color(palette.get("primary_color", '#4f46e5'), BG, 0.15)
        DARK   = palette.get("text_color", '#111827')
        GRAY   = palette.get("neutral_color", '#6B7280')
        BORDER = palette.get("border_color", '#E5E7EB')
        accent = palette.get("primary_color", '#00C9A7')

        n = len(items)

        fig = plt.figure(figsize=(14, 6.5), dpi=130, facecolor=BG)
        gs  = gridspec.GridSpec(2, 1, figure=fig, height_ratios=[1, 1.9], hspace=0.4)

        ax_top = fig.add_subplot(gs[0])
        ax_top.set_xlim(0, n); ax_top.set_ylim(0, 1)
        ax_top.axis('off'); ax_top.set_facecolor(BG)

        for i, (name, data) in enumerate(items):
            cx   = i
            chg  = data.get('change', 0)
            clr  = GREEN if chg >= 0 else RED
            sign = '+' if chg >= 0 else ''
            arrow = '▲' if chg >= 0 else '▼'
            ax_top.add_patch(mpatches.FancyBboxPatch(
                (cx+0.04, 0.07), 0.92, 0.86,
                boxstyle='round,pad=0.02',
                facecolor=PANEL, edgecolor=BORDER, linewidth=0.8, zorder=2))
            ax_top.add_patch(mpatches.Rectangle(
                (cx+0.04, 0.89), 0.92, 0.04, color=clr, zorder=3))
            ax_top.text(cx+0.50, 0.72, name,
                        ha='center', va='center', fontsize=10, color=GRAY, zorder=4)
            ax_top.text(cx+0.50, 0.47, str(data.get('value', '')),
                        ha='center', va='center', fontsize=14, fontweight='bold', color=DARK, zorder=4)
            ax_top.text(cx+0.50, 0.22, f'{arrow} {sign}{chg:.2f}%',
                        ha='center', va='center', fontsize=12, fontweight='bold', color=clr, zorder=4)

        ax_bot = fig.add_subplot(gs[1])
        ax_bot.set_facecolor(BG)
        for sp in ax_bot.spines.values(): sp.set_visible(False)

        names   = [n for n, _ in items]
        changes = [d.get('change', 0) for _, d in items]
        colors  = [GREEN if c >= 0 else RED for c in changes]

        ax_bot.bar(range(len(names)), changes, color=colors, alpha=0.85, width=0.55, zorder=3, linewidth=0)
        ax_bot.axhline(0, color='#D1D5DB', lw=1.2, zorder=2)
        ax_bot.set_xticks(range(len(names)))
        ax_bot.set_xticklabels(names, fontsize=12, color=DARK)
        ax_bot.tick_params(axis='x', length=0)
        ax_bot.tick_params(axis='y', labelsize=10, labelcolor=GRAY, length=0)
        ax_bot.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v:+.1f}%'))
        ax_bot.grid(axis='y', color='#F3F4F6', lw=0.9, zorder=1)
        ax_bot.set_title('글로벌 시장 현황  |  등락률 (%)', fontsize=13, color=GRAY, pad=10, loc='left')

        for i, (bar_h, chg) in enumerate(zip(ax_bot.patches, changes)):
            sign = '+' if chg >= 0 else ''
            clr  = GREEN if chg >= 0 else RED
            ypos = bar_h.get_height()
            va   = 'bottom' if chg >= 0 else 'top'
            off  = 0.025 if chg >= 0 else -0.025
            ax_bot.text(bar_h.get_x() + bar_h.get_width()/2, ypos + off,
                        f'{sign}{chg:.2f}%', ha='center', va=va,
                        fontsize=11, fontweight='bold', color=clr)

        out_path = _out(out_dir) / f'chart_market_{card_idx:03d}_{today_str}.png'
        plt.savefig(str(out_path), dpi=130, bbox_inches='tight', facecolor=BG, pad_inches=0.15)
        plt.close(fig)
        print(f"  📊 시장 차트 생성: {out_path.name}")
        return str(out_path)
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"  ⚠️ 시장 차트 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_checklist_chart(items: list, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """투자자 체크포인트 — 전문 테이블형 레이아웃 (항목 | 상태 컬럼). 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

    today_str = _today_str()
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # ★ 동적 색상 팔레트 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        ACCENT  = palette.get("primary_color", '#2563EB')
        BG      = palette.get("bg_color", '#FFFFFF')
        HDR_BG  = ACCENT
        DARK    = palette.get("text_color", '#0F172A')
        PANEL   = _interpolate_color(ACCENT, BG, 0.85)
        ALT     = _interpolate_color(ACCENT, BG, 0.95)
        BORDER  = palette.get("border_color", '#E2E8F0')
        # ★ 동적 상태 색상 (up/down/neutral 기반)
        STATUS_CLR = [
            palette.get("up_color", '#16A34A'),
            palette.get("neutral_color", '#D97706'),
            palette.get("down_color", '#DC2626'),
            palette.get("up_color", '#16A34A'),
            palette.get("neutral_color", '#D97706')
        ]

        n    = min(len(items), 6)
        if n == 0:
            return ""
        ROW_H = 1.35
        HDR_H = 1.8
        LEG_H = 0.9
        W     = 20
        H     = HDR_H + n * ROW_H + LEG_H + 0.4
        COL_W = 3.5   # 상태 컬럼 너비
        TXT_X = 0.9   # 항목 텍스트 시작 x
        DOT_X = W - COL_W/2  # 상태 도트 x 중심

        fig = plt.figure(figsize=(W, H), dpi=130, facecolor=BG)
        ax  = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off'); ax.set_facecolor(BG)

        # ── 헤더 ────────────────────────────────────────
        ax.add_patch(mpatches.Rectangle((0, H-HDR_H), W, HDR_H, color=HDR_BG, zorder=2))
        ax.text(W/2, H-HDR_H*0.5, f'투자자 핵심 체크포인트  |  {keyword}',
                ha='center', va='center', fontsize=30, fontweight='bold', color='#FFFFFF', zorder=3)

        # ── 컬럼 헤더 ──────────────────────────────────
        col_hdr_y = H - HDR_H - 0.45
        ax.add_patch(mpatches.Rectangle((0, H-HDR_H-ROW_H*0.7), W, ROW_H*0.7,
                                        color='#1E3A5F', zorder=2))
        ax.text(TXT_X, col_hdr_y, '항  목', ha='left', va='center',
                fontsize=24, fontweight='bold', color='#FFFFFF', zorder=3)
        ax.text(DOT_X, col_hdr_y, '상  태', ha='center', va='center',
                fontsize=24, fontweight='bold', color='#FFFFFF', zorder=3)

        # ── 행 ──────────────────────────────────────────
        row_top = H - HDR_H - ROW_H * 0.7
        LABELS  = ['주목', '검토', '주의', '핵심', '보류']
        for i, item in enumerate(items[:n]):
            y_bot = row_top - (i + 1) * ROW_H
            row_c = PANEL if i % 2 == 0 else ALT
            ax.add_patch(mpatches.Rectangle((0, y_bot), W, ROW_H, color=row_c, zorder=1))

            # 구분선
            ax.plot([0, W], [y_bot + ROW_H, y_bot + ROW_H], color=BORDER, lw=1.0, zorder=2)

            # 번호 배지
            dot_clr = STATUS_CLR[i % len(STATUS_CLR)]
            badge = plt.Circle((0.45, y_bot + ROW_H/2), 0.30, color=ACCENT, zorder=3)
            ax.add_patch(badge)
            ax.text(0.45, y_bot + ROW_H/2, str(i+1),
                    ha='center', va='center', fontsize=20, fontweight='bold', color='#FFFFFF', zorder=4)

            # 항목 텍스트 — 최대 32자, 잘림 없음
            txt = str(item).replace('\n', ' ')[:32]
            ax.text(TXT_X, y_bot + ROW_H/2, txt,
                    ha='left', va='center', fontsize=22, color=DARK, zorder=3, clip_on=False)

            # 상태 도트 + 라벨
            status_dot = plt.Circle((DOT_X, y_bot + ROW_H/2), 0.22, color=dot_clr, zorder=4)
            ax.add_patch(status_dot)
            ax.text(DOT_X + 0.38, y_bot + ROW_H/2, LABELS[i % len(LABELS)],
                    ha='left', va='center', fontsize=20, color=dot_clr, fontweight='bold', zorder=4)

        # 하단 구분선
        bot_y = row_top - n * ROW_H
        ax.plot([0, W], [bot_y, bot_y], color=ACCENT, lw=3.0, alpha=0.6, zorder=2)

        # 범례
        leg_y = bot_y - LEG_H * 0.5
        legend = [('주목', '#16A34A'), ('검토', '#D97706'), ('주의', '#DC2626')]
        for li, (lbl, lclr) in enumerate(legend):
            lx = 0.8 + li * 3.2
            ax.add_patch(plt.Circle((lx, leg_y), 0.16, color=lclr, zorder=3))
            ax.text(lx + 0.3, leg_y, lbl, ha='left', va='center',
                    fontsize=20, color='#475569', zorder=3)

        out_path = _out(out_dir) / f'chart_check_{card_idx:03d}_{today_str}.png'
        plt.savefig(str(out_path), dpi=130, bbox_inches='tight', facecolor=BG, pad_inches=0.12)
        plt.close(fig)
        print(f"  📋 체크리스트 차트 생성: {out_path.name}")
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 체크리스트 차트 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_scenario_chart(scenarios: list, keyword: str, sector: str,
                         card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """낙관/중립/비관 시나리오 3단 비교 차트 — 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

    today_str = _today_str()
    def _sc_pair(s):
        if isinstance(s, dict): return s.get('title', ''), s.get('desc', '')
        return s[0], s[1]
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        BG   = palette.get("bg_color", '#FFFFFF')
        DARK = palette.get("text_color", '#111827')
        GRAY = palette.get("neutral_color", '#6B7280')
        # ★ 시나리오별 동적 색상 (up/neutral/down 기반)
        COLS = {
            '낙관': palette.get("up_color", '#1D9E75'),
            '중립': palette.get("neutral_color", '#F59E0B'),
            '비관': palette.get("down_color", '#E24B4A'),
            '▲ 상승 전망': palette.get("up_color", '#1D9E75'),
            '▼ 하락 위험': palette.get("down_color", '#E24B4A'),
            '= 중립 관점': palette.get("neutral_color", '#F59E0B'),
            '▲ 상승 시나리오': palette.get("up_color", '#1D9E75'),
            '▼ 하락 시나리오': palette.get("down_color", '#E24B4A'),
            '= 중립 시나리오': palette.get("neutral_color", '#F59E0B'),
        }

        W, H = 15, 9.0
        fig  = plt.figure(figsize=(W, H), dpi=130, facecolor=BG)
        ax   = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H)
        ax.axis('off'); ax.set_facecolor(BG)

        ax.add_patch(mpatches.Rectangle((0, H-1.4), W, 1.4, color='#111827', zorder=2))
        ax.text(W/2, H-0.70, f'향후 전망 시나리오  |  {keyword}',
                ha='center', va='center', fontsize=28, fontweight='bold', color='#FFFFFF', zorder=3)

        col_w = W / 3


        labels = [_sc_pair(s)[0] for s in scenarios]
        texts  = [_sc_pair(s)[1] for s in scenarios]

        for i, (label, text) in enumerate(zip(labels, texts)):
            base = label.replace('▲ ', '').replace('▼ ', '').replace('= ', '')
            clr  = COLS.get(base, COLS.get(label, '#6B7280'))
            cx   = i * col_w
            ax.add_patch(mpatches.FancyBboxPatch(
                (cx+0.15, 0.35), col_w-0.30, H-2.0,
                boxstyle='round,pad=0.06',
                facecolor='#F9FAFB', edgecolor=clr, linewidth=2.5, zorder=2))
            ax.add_patch(mpatches.Rectangle(
                (cx+0.15, H-2.0), col_w-0.30, 1.0, color=clr, zorder=3))
            ax.text(cx + col_w/2, H-1.50, label,
                    ha='center', va='center', fontsize=28, fontweight='bold', color='#FFFFFF', zorder=4)
            chars, lines, cur = [], [], ''
            for ch in text:
                cur += ch
                if len(cur) >= 10:
                    lines.append(cur.strip()); cur = ''
            if cur:
                lines.append(cur.strip())
            for j, line in enumerate(lines[:5]):
                ax.text(cx + col_w/2, H-3.2 - j*1.0, line,
                        ha='center', va='center', fontsize=22, color=DARK, zorder=3)
            icons = {'낙관': '↑', '중립': '→', '비관': '↓',
                     '▲ 상승 전망': '↑', '▼ 하락 위험': '↓', '= 중립 관점': '→',
                     '▲ 상승 시나리오': '↑', '▼ 하락 시나리오': '↓', '= 중립 시나리오': '→'}
            ax.text(cx + col_w/2, 1.10, icons.get(label, '↔'),
                    ha='center', va='center', fontsize=52, fontweight='bold', color=clr, alpha=0.4, zorder=3)

        # 범례 — 각 시나리오마다 색상 다르게
        leg_items = [
            ('긍정 시나리오', '#1D9E75'),
            ('부정 시나리오', '#E24B4A'),
            ('중립 시나리오', '#F59E0B'),
        ]
        leg_y = 0.25
        for li, (lbl, lclr) in enumerate(leg_items[:len(labels)]):
            lx = 0.6 + li * (W/3)
            ax.add_patch(mpatches.Rectangle((lx, leg_y-0.12), 0.40, 0.24, color=lclr, zorder=3))
            ax.text(lx + 0.55, leg_y, lbl, ha='left', va='center',
                    fontsize=18, color='#374151', zorder=3)

        out_path = _out(out_dir) / f'chart_scenario_{card_idx:03d}_{today_str}.png'
        plt.savefig(str(out_path), dpi=130, bbox_inches='tight', facecolor=BG, pad_inches=0.15)
        plt.close(fig)
        print(f"  📊 시나리오 차트 생성: {out_path.name}")
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 시나리오 차트 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_impact_chart(factors: list, keyword: str, sector: str,
                      card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """영향 요인 수평 막대 차트 — 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors

    today_str = _today_str()
    if not factors:
        factors = [
            (keyword[:8], 2.0, '긍정'),
            ('시장 반응', 1.5, '긍정'),
            ('투자 위험', -1.2, '부정'),
            ('변동성', -0.8, '부정'),
        ]
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        RED    = palette.get("down_color", '#E24B4A')
        GREEN  = palette.get("up_color", '#1D9E75')
        BG     = palette.get("bg_color", '#FFFFFF')
        DARK   = palette.get("text_color", '#111827')
        GRAY   = palette.get("neutral_color", '#6B7280')
        accent = palette.get("primary_color", '#00C9A7')

        n = len(factors)
        H = max(n * 1.5 + 3.5, 6.0)
        fig, ax = plt.subplots(figsize=(16, H), dpi=130)
        fig.patch.set_facecolor(BG)
        ax.set_facecolor(BG)
        for sp in ax.spines.values(): sp.set_visible(False)

        names  = [f[0][:12] for f in factors]
        values = [f[1] if len(f) > 1 else 0.5 for f in factors]
        colors = [GREEN if (len(f) > 2 and '긍정' in str(f[2])) or v >= 0
                  else RED for f, v in zip(factors, values)]

        bars = ax.barh(range(n), values, color=colors, alpha=0.8, height=0.55, zorder=3)
        ax.axvline(0, color='#D1D5DB', lw=1.2, zorder=2)
        ax.set_yticks(range(n))
        ax.set_yticklabels(names, fontsize=24, color=DARK)
        ax.tick_params(axis='y', length=0)
        ax.tick_params(axis='x', labelsize=20, labelcolor=GRAY, length=0)
        ax.grid(axis='x', color='#F3F4F6', lw=1.2, zorder=1)
        ax.set_title(f'주요 영향 요인 분석  |  {keyword}', fontsize=26, color=GRAY, pad=14, loc='left')
        max_val = max(abs(v) for v in values) if values else 1
        ax.set_xlim(-max_val*1.3, max_val*1.3)

        for bar, val in zip(bars, values):
            sign = '+' if val >= 0 else ''
            clr  = GREEN if val >= 0 else RED
            ax.text(bar.get_width() + (0.02 if val >= 0 else -0.02),
                    bar.get_y() + bar.get_height()/2,
                    f'{sign}{val:.1f}',
                    ha='left' if val >= 0 else 'right',
                    va='center', fontsize=22, fontweight='bold', color=clr)

        out_path = _out(out_dir) / f'chart_impact_{card_idx:03d}_{today_str}.png'
        plt.savefig(str(out_path), dpi=130, bbox_inches='tight', facecolor=BG, pad_inches=0.15)
        plt.close(fig)
        print(f"  📊 영향 분석 차트 생성: {out_path.name}")
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 영향 분석 차트 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_highlight_card(text: str, keyword: str, sector: str,
                        card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """핵심 인사이트 카드 — 다크 배경 + 대형 인용 장식 + 골드 포인트."""
    today_str = _today_str()
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.patches import FancyBboxPatch

        GOLD  = '#F0B429'
        DARK  = '#0D1B2A'
        WHITE = '#FFFFFF'
        LGRAY = '#94A3B8'

        W, H = 15, 5.0
        fig  = plt.figure(figsize=(W, H), dpi=120, facecolor=DARK)
        ax   = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H)
        ax.axis('off'); ax.set_facecolor(DARK)

        ax.add_patch(mpatches.Rectangle((0, H - 0.15), W, 0.15, color=GOLD, zorder=3))
        circle = plt.Circle((W * 0.88, H * 0.22), 2.2, color=GOLD, alpha=0.04, zorder=1)
        ax.add_patch(circle)
        ax.text(0.50, H * 0.72, '"',
                ha='center', va='center', fontsize=100, fontweight='bold', color=GOLD, alpha=0.25, zorder=2)

        label_x = 1.6
        ax.add_patch(FancyBboxPatch(
            (label_x - 0.12, H * 0.80), 2.8, 0.48,
            boxstyle='round,pad=0.05', linewidth=0, facecolor=GOLD, alpha=0.18, zorder=3))
        ax.text(label_x + 1.28, H * 0.94, 'KEY INSIGHT',
                ha='center', va='center', fontsize=22, fontweight='bold', color=GOLD, zorder=4)

        ax.add_patch(mpatches.Rectangle(
            (label_x, H * 0.72), W - label_x - 0.8, 0.06, color=GOLD, alpha=0.25, zorder=3))

        lines, cur = [], ''
        for ch in text.replace('\n', ' '):
            cur += ch
            if len(cur) >= 20:
                lines.append(cur.strip()); cur = ''
        if cur:
            lines.append(cur.strip())
        lines = lines[:3]
        n = len(lines)
        line_gap = 0.70
        start_y  = H * 0.52 + (n - 1) * line_gap / 2
        for i, line in enumerate(lines):
            ax.text(label_x, start_y - i * line_gap, line,
                    ha='left', va='center', fontsize=28, fontweight='bold', color=WHITE, zorder=5)

        ax.text(label_x, H * 0.12, f'# {keyword}   ·   {today_str}',
                ha='left', va='center', fontsize=20, color=LGRAY, zorder=4)
        ax.add_patch(mpatches.Rectangle((0, 0), W, 0.12, color=GOLD, alpha=0.35, zorder=3))

        out_path = _out(out_dir) / f'chart_highlight_{card_idx:03d}_{today_str}.png'
        plt.savefig(str(out_path), dpi=120, bbox_inches='tight', facecolor=DARK, pad_inches=0.08)
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 하이라이트 카드 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_insight_card(text: str, label: str, card_idx: int,
                      keyword: str, sector: str, platform: str = 'naver', out_dir=None) -> str:
    """단락 사이 핵심 인사이트 카드 이미지 — 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

    today_str = _today_str()
    _svg = _try_svg(
        {"text": text, "label": label, "keyword": keyword},
        "custom", f"{label} | {keyword}", out_dir,
    )
    if _svg:
        print(f"  💡 인사이트 카드 생성 (SVG): {Path(_svg).name}")
        return _svg
    _mpl_setup()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        accent = palette.get("primary_color", '#00C9A7')
        BG    = palette.get("bg_color", '#FFFFFF')
        DARK  = palette.get("text_color", '#0B1426')
        LGRAY = palette.get("neutral_color", '#6B7280')

        W, H = 12, 2.2
        fig  = plt.figure(figsize=(W, H), dpi=120)
        ax   = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W); ax.set_ylim(0, H)
        ax.axis('off')

        ax.add_patch(mpatches.Rectangle((0, 0), W, H, color=BG, zorder=0))
        ax.add_patch(mpatches.Rectangle((0, 0), 0.45, H, color=accent, zorder=2))
        ax.add_patch(mpatches.Rectangle((0.45, 0), W-0.45, H, color='#F8FAFF', zorder=1))
        ax.add_patch(mpatches.Rectangle((0.45, H-0.06), W-0.45, 0.06, color=accent, alpha=0.3, zorder=2))

        ax.text(0.7, H-0.32, label,
                ha='left', va='center', fontsize=13, fontweight='bold', color=accent, zorder=4)
        ax.plot([0.7, W-0.3], [H-0.52, H-0.52], color=accent, lw=0.8, alpha=0.4, zorder=3)

        if len(text) > 28:
            mid = len(text) // 2
            split_idx = text.rfind(' ', 0, mid + 5)
            if split_idx == -1:
                split_idx = mid
            line1, line2 = text[:split_idx], text[split_idx:].strip()
            text_fs = 18 if len(line1) <= 24 else 16
            ax.text(0.7, H*0.50 + 0.1, line1,
                    ha='left', va='center', fontsize=text_fs, fontweight='bold', color=DARK, zorder=5)
            ax.text(0.7, H*0.50 - 0.28, line2,
                    ha='left', va='center', fontsize=text_fs, fontweight='bold', color=DARK, zorder=5)
        else:
            text_fs = 20 if len(text) <= 20 else 18
            ax.text(0.7, H*0.42, text,
                    ha='left', va='center', fontsize=text_fs, fontweight='bold', color=DARK, zorder=5)

        out_path = _out(out_dir) / f'insight_{card_idx:03d}_{today_str}.png'
        fig.savefig(str(out_path), dpi=120, bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ 인사이트 카드 생성 실패 (카드{card_idx}): {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_line_trend_chart(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """추세 라인/에어리어 차트 — 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors

    import re
    today_str = _today_str()
    import datetime as _dt
    nums = [float(n) for n in re.findall(r'\d+\.?\d*', text) if 0 < float(n) < 1000][:6]
    if len(nums) < 4:
        nums = [45, 52, 48, 61, 57, 70]
    # 실제 월 레이블 생성 — 오늘 기준 과거 N개월
    def _month_labels(n: int) -> list:
        today = _dt.date.today()
        result = []
        for i in range(n):
            offset = n - 1 - i
            y, m = today.year, today.month - offset
            while m <= 0:
                y -= 1; m += 12
            result.append(f"{y}.{m:02d}")
        return result
    _mpl_setup()
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        accent = palette.get("primary_color", '#00C9A7')
        x = list(range(len(nums)))
        labels = _month_labels(len(nums))

        fig, ax = plt.subplots(figsize=(14, 8), dpi=150)
        fig.patch.set_facecolor('#FAFBFF')
        ax.set_facecolor('#FAFBFF')

        ax.fill_between(x, nums, alpha=0.18, color=accent)
        ax.plot(x, nums, color=accent, linewidth=4.0, marker='o',
                markersize=14, markerfacecolor='white', markeredgecolor=accent, markeredgewidth=3,
                label=f'{keyword} 관심도 추이')

        max_i = nums.index(max(nums))
        ax.annotate(f'▲ {nums[max_i]:.0f}',
                    xy=(max_i, nums[max_i]), xytext=(max_i, nums[max_i] + max(nums)*0.10),
                    ha='center', fontsize=22, color=accent, fontweight='bold')

        ax.legend(fontsize=18, loc='upper left', framealpha=0.85)
        ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=20, rotation=15, ha='right')
        ax.set_ylabel('관심도 지수', fontsize=22)
        ax.tick_params(axis='y', labelsize=20)
        ax.spines[['top', 'right']].set_visible(False)
        ax.spines[['left', 'bottom']].set_color('#DDE3EE')
        ax.yaxis.set_tick_params(labelcolor='#555')

        fig.text(0.5, 0.96, f'{keyword} 트렌드 추이', ha='center',
                 fontsize=28, fontweight='bold', color='#1A2A4A')
        fig.text(0.5, 0.91, today_str, ha='center', fontsize=20, color='#8898AA')

        plt.tight_layout(rect=[0, 0, 1, 0.88])
        out_path = _out(out_dir) / f'chart_line_{card_idx:03d}_{today_str}.png'
        fig.savefig(str(out_path), dpi=150, bbox_inches='tight', facecolor='#FAFBFF')
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ line_trend 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_stat_infographic(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver',
                          prebuilt: list = None, out_dir=None) -> str:
    """KPI 숫자 인포그래픽 — 큰 숫자 4개 그리드, 다크 배경."""
    import re
    today_str = _today_str()
    if prebuilt and len(prebuilt) >= 2:
        stats = [{'val': s['val'], 'unit': s['unit'], 'label': s['label']} for s in prebuilt[:4]]
    else:
        raw_nums = re.findall(
            r'([가-힣]{1,8}[은는이가도의]?\s*)?(\d[\d,]*\.?\d*)\s*(%|억|조|만|명|개|배|위|년|원)?',
            text)
        stats = []
        seen: set = set()
        for lgrp, val, unit in raw_nums:
            if val in seen: continue
            seen.add(val)
            lbl = lgrp.strip().rstrip('은는이가도의을를에서도 ') if lgrp.strip() else ''
            if not lbl:
                idx = text.find(val)
                pre = re.findall(r'[가-힣]{2,5}', text[max(0, idx-30):idx])
                lbl = pre[-1] if pre else keyword
            stats.append({'val': val.replace(',', ''), 'unit': unit or '', 'label': lbl[:6]})
        if len(stats) < 4:
            core = [w for w in re.findall(r'[가-힣]{2,5}', text)
                    if w not in {'하지만', '그러나', '있습니다', '합니다'}]
            seen_w: set = set()
            for w in core:
                if w in seen_w or len(stats) >= 4: break
                seen_w.add(w)
                stats.append({'val': str(text.count(w)), 'unit': '회', 'label': w[:6]})
        # LLM으로 의미 있는 KPI 제목 보정
        try:
            from shared.llm import invoke_text as _inv
            import json as _j
            _nums = [f"{s['val']}{s['unit']}" for s in stats[:4]]
            _p = (
                f"본문(앞 600자):\n{text[:600]}\n\n"
                f"키워드: {keyword}\n"
                f"수치 목록: {', '.join(_nums)}\n\n"
                f"각 수치에 대해 본문 맥락에 맞는 2~5글자 KPI 명사 제목을 JSON 배열로만 출력.\n"
                f"배열 길이={len(_nums)}, 예: [\"영업이익\",\"매출액\",\"성장률\",\"점유율\"]"
            )
            _raw = _inv("writer_fast", _p, max_tokens=120, temperature=0.3)
            _m = re.search(r'\[.*?\]', _raw, re.DOTALL)
            if _m:
                _labels = _j.loads(_m.group(0))
                for i, lbl in enumerate(_labels[:len(stats)]):
                    if lbl and str(lbl).strip():
                        stats[i]['label'] = str(lbl).strip()[:6]
        except Exception:
            pass  # 폴백 label 유지
    # stats가 비어있으면 LLM으로 키워드 기반 KPI 생성
    if not stats:
        try:
            from shared.llm import invoke_text as _inv_s
            import json as _js, re as _res
            _sp = (
                f"키워드: {keyword}\n본문(앞 400자):\n{text[:400]}\n\n"
                f"이 키워드·본문 맥락에서 투자자에게 의미 있는 핵심 수치 KPI 3~4개를 추론하라.\n"
                f"본문에 없으면 일반 경제 지식 기반으로 추정해도 됨. 라벨은 2~5글자.\n"
                f"JSON만: [{{\"label\":\"시장규모\",\"val\":\"3.5\",\"unit\":\"조원\"}}, ...]"
            )
            _sr = _inv_s("writer_fast", _sp, max_tokens=150, temperature=0.4)
            _sm = _res.search(r'\[[\s\S]*?\]', _sr)
            if _sm:
                for d in _js.loads(_sm.group(0))[:4]:
                    if d.get('label') and d.get('val'):
                        stats.append({'val': str(d['val']), 'unit': str(d.get('unit', '')), 'label': str(d['label'])[:6]})
        except Exception:
            pass
    _mpl_setup()
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        accent  = _SECTOR_COLORS.get(sector, '#00C9A7')

        BG = '#0D1B2E'; CARD = '#162240'; WHITE = '#FFFFFF'; GRAY = '#7B8FA6'
        W, H = 14, 8.0
        fig = plt.figure(figsize=(W, H), dpi=150)
        ax  = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')
        ax.add_patch(mpatches.Rectangle((0, 0), W, H, color=BG, zorder=0))
        ax.text(W/2, H-0.65, f'{keyword} 핵심 수치', ha='center', va='center',
                fontsize=28, fontweight='bold', color=WHITE, zorder=3)
        ax.add_patch(mpatches.Rectangle((0, H-1.20), W, 0.08, color=accent, alpha=0.6, zorder=2))

        positions = [(0.25, 2.20), (W/2+0.25, 2.20), (0.25, 0.25), (W/2+0.25, 0.25)]
        cw, ch = W/2 - 0.50, 1.80
        for i, (px, py) in enumerate(positions):
            if i >= len(stats): break
            ax.add_patch(mpatches.FancyBboxPatch(
                (px, py), cw, ch, boxstyle='round,pad=0.06',
                facecolor=CARD, edgecolor=accent, linewidth=2.0, alpha=0.95, zorder=2))
            ax.text(px + cw/2, py + ch*0.68,
                    f"{stats[i]['val']}{stats[i]['unit']}",
                    ha='center', va='center', fontsize=44, fontweight='bold', color=accent, zorder=4)
            ax.text(px + cw/2, py + ch*0.22, stats[i]['label'][:8],
                    ha='center', va='center', fontsize=22, color=GRAY, zorder=4)

        out_path = _out(out_dir) / f'chart_stat_{card_idx:03d}_{today_str}.png'
        fig.savefig(str(out_path), dpi=150, bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ stat_infographic 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


def make_comparison_chart(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver',
                          pros: list = None, cons: list = None, out_dir=None) -> str:
    """좌우 비교 카드 — 긍정/리스크. 동적 스타일 적용."""
    from JARVIS06_IMAGE.style_engine import generate_sector_colors, _interpolate_color

    import re
    today_str = _today_str()
    if not pros or not cons:
        sents = [s.strip() for s in re.split(r'[。.!?\n]', text) if len(s.strip()) > 5]
        pos_kws = ['상승', '증가', '호조', '성장', '개선', '기회', '수혜', '강세', '회복']
        neg_kws = ['하락', '감소', '부진', '위축', '우려', '리스크', '약세', '악화', '침체']
        _pros = [s[:14] for s in sents if any(k in s for k in pos_kws)][:3]
        _cons = [s[:14] for s in sents if any(k in s for k in neg_kws)][:3]
        if not _pros: _pros = [s[:14] for s in sents[:3]] or [f'{keyword} 성장 기대', '수요 확대', '기회 요인']
        if not _cons: _cons = [s[:14] for s in reversed(sents[-3:])] or [f'{keyword} 변동성', '공급 불균형', '규제 리스크']
        if pros is None: pros = _pros
        if cons is None: cons = _cons
    _mpl_setup()
    try:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        # ★ 동적 색상 생성 (매번 다른 스타일)
        palette = generate_sector_colors(sector, keyword)
        GREEN = palette.get("up_color", '#1B7A4A')
        RED = palette.get("down_color", '#C0392B')
        WHITE = palette.get("bg_color", '#FFFFFF')
        BG = _interpolate_color(palette.get("primary_color", '#4f46e5'), WHITE, 0.95)
        LEFT_C = _interpolate_color(GREEN, WHITE, 0.85)
        RIGHT_C = _interpolate_color(RED, WHITE, 0.85)
        LBORDER = GREEN
        RBORDER = RED
        W, H = 16, 9.0
        fig = plt.figure(figsize=(W, H), dpi=150)
        ax  = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(0, H); ax.axis('off')
        ax.add_patch(mpatches.Rectangle((0, 0), W, H, color=BG, zorder=0))

        # 제목 바
        ax.add_patch(mpatches.Rectangle((0, H-1.2), W, 1.2, color='#1A2A4A', zorder=1))
        ax.text(W/2, H-0.6, f'{keyword}  비교 분석', ha='center', va='center',
                fontsize=30, fontweight='bold', color=WHITE, zorder=3)

        half = W/2
        # 왼쪽 패널
        ax.add_patch(mpatches.FancyBboxPatch((0.3, 0.3), half-0.55, H-1.8,
            boxstyle='round,pad=0.1', facecolor=LEFT_C, edgecolor=LBORDER, linewidth=3.0, zorder=2))
        ax.add_patch(mpatches.Rectangle((0.3, H-2.0), half-0.55, 0.72, color=LBORDER, zorder=3))
        ax.text(0.3+(half-0.55)/2, H-1.64, '✓  장점 (Pros)', ha='center', va='center',
                fontsize=24, fontweight='bold', color=WHITE, zorder=4)

        # 오른쪽 패널
        ax.add_patch(mpatches.FancyBboxPatch((half+0.25, 0.3), half-0.55, H-1.8,
            boxstyle='round,pad=0.1', facecolor=RIGHT_C, edgecolor=RBORDER, linewidth=3.0, zorder=2))
        ax.add_patch(mpatches.Rectangle((half+0.25, H-2.0), half-0.55, 0.72, color=RBORDER, zorder=3))
        ax.text(half+0.25+(half-0.55)/2, H-1.64, '✕  리스크 (Cons)', ha='center', va='center',
                fontsize=24, fontweight='bold', color=WHITE, zorder=4)

        item_y_start = H - 2.6
        row_gap = 1.55
        for i, p in enumerate(pros[:3]):
            iy = item_y_start - i * row_gap
            ax.add_patch(mpatches.FancyBboxPatch((0.5, iy - 0.55), half-0.9, 1.05,
                boxstyle='round,pad=0.05', facecolor='#D5F0E3', edgecolor=LBORDER, linewidth=1.2, zorder=3))
            # 텍스트 자르지 않고 표시
            txt = str(p)[:22]
            ax.text(0.75, iy, f'  {txt}', ha='left', va='center',
                    fontsize=20, color=GREEN, fontweight='bold', zorder=4, clip_on=False)
        for i, c in enumerate(cons[:3]):
            iy = item_y_start - i * row_gap
            ax.add_patch(mpatches.FancyBboxPatch((half+0.45, iy - 0.55), half-0.9, 1.05,
                boxstyle='round,pad=0.05', facecolor='#FADBD8', edgecolor=RBORDER, linewidth=1.2, zorder=3))
            txt = str(c)[:22]
            ax.text(half+0.70, iy, f'  {txt}', ha='left', va='center',
                    fontsize=20, color=RED, fontweight='bold', zorder=4, clip_on=False)

        out_path = _out(out_dir) / f'chart_comp_{card_idx:03d}_{today_str}.png'
        fig.savefig(str(out_path), dpi=150, bbox_inches='tight', facecolor=BG)
        plt.close(fig)
        return str(out_path)
    except Exception as e:
        print(f"  ⚠️ comparison_chart 생성 실패: {e}")
        _g_report("image", e, module=__name__)
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AI 이미지
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_ai_section_image(section_text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver', out_dir=None) -> str:
    """섹션 내용 분석 → LLM 프롬프트 동적 생성 → AI 이미지 생성.

    섹션 텍스트를 LLM이 분석해 해당 내용에 맞는 고유 이미지 프롬프트를 만든 뒤
    generate_photo() 폴백 체인으로 생성. 섹션마다 반드시 다른 이미지.
    """
    today_str = _today_str()
    try:
        img_dir  = _out(out_dir)
        out_path = img_dir / f'ai_section_{card_idx:03d}_{today_str}.png'

        if out_path.exists():
            return str(out_path)

        # ── 섹션 내용 기반 LLM 프롬프트 동적 생성 ──────────────────────
        prompt_en: str | None = None
        try:
            from shared.llm import invoke_text as _inv
            _lp = (
                f"Read the following Korean article section and write a concise English image prompt "
                f"(20-35 words) for a photorealistic economic/business photo that visually represents "
                f"the core topic. No text, logos, or charts in the image. High quality.\n\n"
                f"Section (keyword: {keyword}, sector: {sector}):\n{section_text[:400]}"
            )
            prompt_en = _inv("writer_fast", _lp, max_tokens=80, temperature=0.85)
            if prompt_en:
                import re as _re_p
                # 마크다운 헤더·레이블 제거 (# Image Prompt, **English:**, 등)
                prompt_en = _re_p.sub(r'^#+\s*[^\n]*\n+', '', prompt_en.strip(), flags=_re_p.MULTILINE)
                prompt_en = _re_p.sub(r'^\*{1,2}[^*]+\*{1,2}:?\s*', '', prompt_en.strip())
                prompt_en = prompt_en.strip().strip('"').strip("'").strip()
        except Exception:
            prompt_en = None

        # LLM 실패 시 키워드 기반 폴백 (section_text 앞 부분 반영)
        if not prompt_en:
            snippet = section_text[:60].replace('\n', ' ')
            prompt_en = (
                f"Professional economic photo about {keyword} in Korean market context, "
                f"related to: {snippet}, no text, high quality"
            )

        seed = abs(hash(f"{today_str}_{keyword}_{card_idx}_{section_text[:50]}")) % 9999
        print(f"  🤖 AI 이미지 생성 중 (섹션 {card_idx}): {prompt_en[:60]}...")

        from JARVIS06_IMAGE.image_agent import generate_photo as _gen_photo
        result = _gen_photo(
            prompt_ko=section_text[:80],   # 번역용 (prompt_en 있으면 무시됨)
            prompt_en=prompt_en,
            out_dir=img_dir,
            width=1200, height=630, seed=seed,
        )
        if result and result.exists() and str(result) != str(out_path):
            result.rename(out_path)
        print(f"  ✅ AI 이미지 저장: {out_path.name}")
        return str(out_path)

    except Exception as e:
        import traceback
        print(f"  ⚠️ AI 이미지 생성 실패 (섹션 {card_idx}): {type(e).__name__}: {e}")
        _g_report("image", e, module=__name__)
        traceback.print_exc()
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NEW: 스마트 섹션 이미지 — 설계서 기반 단일 진입점
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_smart_section_image(
    section_text: str,
    section_title: str,
    keyword: str,
    sector: str,
    card_idx: int,
    platform: str = "naver",
    out_dir=None,
) -> str:
    """섹션 본문 → Claude LLM SVG 인포그래픽 (제1-B조: 고정 템플릿 금지).

    흐름:
      1. Claude LLM이 섹션 텍스트 분석 → content_infographic SVG 직접 생성
      2. SVG 실패 시 → make_ai_section_image() (AI 사진) 폴백
    Plotly/matplotlib 중간 레이어 완전 제거.
    """
    import re as _re
    import json as _json
    import hashlib as _hl

    text      = (section_text or '').strip()
    today_str = _today_str()
    dest      = _out(out_dir)

    _sig     = _hl.md5(f"{keyword}_{card_idx}_{today_str}".encode()).hexdigest()[:8]
    out_path = dest / f"smart_{card_idx:03d}_{today_str}_{_sig}.png"

    if out_path.exists():
        return str(out_path)

    print(f"  🎨 [smart {card_idx}] '{(section_title or keyword)[:20]}' Claude SVG 생성 중...")

    # ── Claude LLM이 섹션 텍스트에서 인포그래픽 스펙 추출 ──────────
    spec: dict | None = None
    try:
        from shared.llm import invoke_text as _inv
        _spec_prompt = (
            f"섹션 제목: {section_title}\n"
            f"키워드: {keyword} / 섹터: {sector}\n"
            f"본문:\n{text[:800]}\n\n"
            "위 섹션 내용에서 독자에게 가장 유용한 인포그래픽 1개를 설계하라.\n"
            "JSON만 출력 (마크다운·설명 없음):\n"
            '{"title":"인포그래픽 제목","subtitle":"부제목(선택)",'
            '"key_message":"독자가 가져갈 핵심 1문장",'
            '"viz_type":"infographic|kpi_cards|comparison_table|checklist|timeline|scenario_cards|flow_diagram|highlight_card",'
            '"data":[{"label":"항목","value":"값","unit":"단위(선택)","highlight":false}],'
            '"items":["텍스트 항목(viz_type이 checklist/timeline/scenario_cards일 때 사용)"]}\n\n'
            "data/items는 섹션 본문에 실제 등장하는 내용으로만 채울 것. 없으면 빈 배열."
        )
        raw_spec = _inv("writer_fast", _spec_prompt, max_tokens=600, temperature=0.2)
        cleaned  = _re.sub(r'```[a-z]*\n?', '', (raw_spec or '')).strip()
        m        = _re.search(r'\{.*\}', cleaned, _re.DOTALL)
        if m:
            spec = _json.loads(m.group())
            spec.setdefault("keyword", keyword)
            spec.setdefault("sector",  sector)
            spec.setdefault("today",   today_str)
    except Exception as e:
        print(f"  ⚠️ [smart {card_idx}] 스펙 추출 실패: {e}")
        _g_report("image", e, module=__name__)
        spec = None

    # ── Claude SVG 렌더링 ──────────────────────────────────────────
    if spec:
        try:
            from JARVIS06_IMAGE.svg_renderer import _generate_svg, _svg_to_png, _PROMPTS
            import json as _json2
            viz = spec.get("viz_type", "infographic")
            prompt_tpl = _PROMPTS.get(viz) or _PROMPTS.get("content_infographic") or _PROMPTS["infographic"]
            prompt     = prompt_tpl.format(spec_json=_json2.dumps(spec, ensure_ascii=False))
            svg_code   = _generate_svg(prompt)
            if svg_code:
                svg_path = out_path.with_suffix('.svg')
                svg_path.write_text(svg_code, encoding='utf-8')
                if _svg_to_png(svg_path, out_path):
                    print(f"  ✅ [smart {card_idx}] Claude SVG ({viz}) → {out_path.name}")
                    return str(out_path)
                print(f"  ✅ [smart {card_idx}] Claude SVG (PNG실패) → {svg_path.name}")
                return str(svg_path)
        except Exception as e:
            print(f"  ⚠️ [smart {card_idx}] Claude SVG 렌더링 실패({e}) → AI 사진 폴백")
            _g_report("image", e, module=__name__)

    # ── AI 사진 폴백 ────────────────────────────────────────────────
    print(f"  🤖 [smart {card_idx}] AI 사진 폴백")
    return make_ai_section_image(
        section_text=text, keyword=keyword, sector=sector,
        card_idx=card_idx, platform=platform, out_dir=out_dir,
    )


__all__ = [
    "make_trend_thumbnail",
    "make_section_image",
    "make_market_chart",
    "make_checklist_chart",
    "make_scenario_chart",
    "make_impact_chart",
    "make_highlight_card",
    "make_insight_card",
    "make_line_trend_chart",
    "make_stat_infographic",
    "make_comparison_chart",
    "make_ai_section_image",
    "make_smart_section_image",   # NEW
    "_SECTOR_COLORS",
    "_KO_EN_MAP",
]
