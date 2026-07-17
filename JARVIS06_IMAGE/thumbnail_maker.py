"""JARVIS06_IMAGE/thumbnail_maker.py — 블로그 대표 썸네일 동적 생성.

★ 2026-05-23 (사용자 박제):
  - Pollinations.ai (FLUX) 로 AI 사진 배경 생성 → Bing 캐시 문제 원천 차단
  - 두 가지 편집 레이아웃:
    * triptych  — 사진 3분할 + 대형 키워드 (유튜브/블로그 트렌드)
    * editorial — 폴라로이드 프레임 + 파스텔 배경 + 꾸밈 요소
  - 폴백: PIL 그라디언트 아트 배경 (Pollinations 실패 시)

흐름:
  _generate_photo()  → Pollinations FLUX AI 사진
  _apply_triptych()  → 3분할 + 글로우 타이포
  _apply_editorial() → 폴라로이드 + 파스텔 + 데코
"""
from __future__ import annotations
import hashlib
import logging
import math
import random
import time
from datetime import date
from pathlib import Path

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

log = logging.getLogger("jarvis")

W, H = 1200, 630

_FONT_TTC = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
_FONT_SUPP = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"


# ── 색상 테마 ────────────────────────────────────────────────
_COLOR_THEMES = [
    dict(name="midnight_blue",
         c00=(8,20,48), c01=(14,36,80), c10=(20,50,100), c11=(10,28,65),
         accent=(79,195,247), text=(255,255,255), text2=(144,202,249), badge=(2,136,209),
         pastel=(215,230,248)),
    dict(name="deep_purple",
         c00=(20,5,50), c01=(60,10,100), c10=(35,8,75), c11=(25,5,60),
         accent=(224,64,251), text=(255,255,255), text2=(206,147,216), badge=(156,39,176),
         pastel=(235,218,248)),
    dict(name="forest",
         c00=(5,30,15), c01=(15,60,25), c10=(20,80,35), c11=(10,50,20),
         accent=(105,240,174), text=(255,255,255), text2=(165,214,167), badge=(46,125,50),
         pastel=(218,242,225)),
    dict(name="dark_gold",
         c00=(25,16,0), c01=(50,30,5), c10=(60,38,0), c11=(35,22,0),
         accent=(255,215,64), text=(255,255,255), text2=(255,224,130), badge=(230,81,0),
         pastel=(252,244,220)),
    dict(name="ocean",
         c00=(8,20,40), c01=(10,50,100), c10=(15,60,110), c11=(10,35,70),
         accent=(64,196,255), text=(255,255,255), text2=(129,212,250), badge=(2,119,189),
         pastel=(215,238,252)),
    dict(name="graphite_gold",
         c00=(20,20,20), c01=(35,35,35), c10=(40,40,40), c11=(28,28,28),
         accent=(255,215,64), text=(255,255,255), text2=(255,224,130), badge=(245,127,23),
         pastel=(252,245,220)),
    dict(name="indigo",
         c00=(20,28,110), c01=(30,42,140), c10=(40,55,160), c11=(25,35,125),
         accent=(130,177,255), text=(255,255,255), text2=(159,168,218), badge=(21,101,192),
         pastel=(222,228,252)),
    dict(name="crimson",
         c00=(80,8,50), c01=(130,15,70), c10=(150,20,80), c11=(100,12,60),
         accent=(255,128,171), text=(255,255,255), text2=(244,143,177), badge=(194,24,91),
         pastel=(252,220,230)),
    dict(name="emerald",
         c00=(0,50,40), c01=(0,80,65), c10=(0,95,75), c11=(0,65,52),
         accent=(100,255,218), text=(255,255,255), text2=(128,203,196), badge=(0,121,107),
         pastel=(210,248,240)),
    dict(name="volcanic",
         c00=(80,20,5), c01=(150,40,10), c10=(170,55,12), c11=(120,30,8),
         accent=(255,204,2), text=(255,255,255), text2=(255,204,188), badge=(216,67,21),
         pastel=(255,238,210)),
    dict(name="vivid_violet",
         c00=(35,18,100), c01=(60,30,140), c10=(75,38,160), c11=(48,24,120),
         accent=(234,128,252), text=(255,255,255), text2=(206,147,216), badge=(106,27,154),
         pastel=(240,218,252)),
    dict(name="slate",
         c00=(28,38,50), c01=(45,58,72), c10=(55,70,85), c11=(35,48,60),
         accent=(207,216,220), text=(255,255,255), text2=(176,190,197), badge=(69,90,100),
         pastel=(230,235,238)),
]

# ★ LLM 호출 절감: 같은 title+keyword는 1회 생성 후 공유 (네이버/티스토리 각각 AI 이미지 생성)
_PARAM_CACHE: dict[tuple, dict] = {}


def _llm_thumbnail_params(title: str, keyword: str) -> dict:
    """LLM이 글 내용 보고 *주제를 대표하는* 사진 프롬프트 + 색상 테마 결정.

    반환: {"photo_prompt": str, "color_theme": str}
    실패 시 빈 dict → 호출자가 random fallback 처리.
    ★ 핵심(사용자 박제 2026-07-05): 썸네일 사진은 독자가 한눈에 주제를 알아보는
      *가장 대표적인 실사* 여야 한다 (지역화폐→동전더미, 반도체→웨이퍼). 추상·은유로
      빠지지 말 것. 동시에 고품질(영화적 조명·구도)이어야 클릭을 유도한다.
    ★ LLM 1회 절감: 같은 title+keyword 재호출 시 캐시 반환 (네이버/티스토리 공유).
       AI 이미지 생성(Pollinations)은 플랫폼별 각각 독립 실행.
    """
    cache_key = (title, keyword)
    if cache_key in _PARAM_CACHE and _PARAM_CACHE[cache_key]:
        log.info(f"[thumbnail] LLM 파라미터 캐시 히트: {keyword}")
        return _PARAM_CACHE[cache_key]

    import json, re
    theme_names = [t["name"] for t in _COLOR_THEMES]
    try:
        from shared.llm import invoke_text
        req = (
            f"You are a creative director for a Korean blog. Pick the single most\n"
            f"RECOGNIZABLE hero photo that represents this article at a glance.\n\n"
            f"Article title: {title}\n"
            f"Topic: {keyword}\n\n"
            f"Available color themes: {', '.join(theme_names)}\n\n"
            f"Return ONLY a JSON object, no explanation:\n"
            f'{{\n'
            f'  "photo_prompt": "<English scene that CLEARLY represents the topic — a real, concrete subject a reader instantly links to the topic>",\n'
            f'  "color_theme": "<one theme name from the list that best matches the mood>"\n'
            f'}}\n\n'
            f"photo_prompt rules (STRICT):\n"
            f"- REPRESENTATIVE FIRST: the main subject must be the obvious real-world object/scene of the topic "
            f"(money topic → stacks of coins/banknotes; oil → refinery/oil drums; housing → apartment complex; "
            f"AI chips → glowing semiconductor wafers). A viewer must 'get it' in under 1 second.\n"
            f"- Then make it PREMIUM: photorealistic, cinematic lighting (golden hour / studio bokeh), "
            f"shallow depth of field, editorial magazine quality.\n"
            f"- Concrete photography ONLY — NO abstract metaphors, NO text, NO numbers, NO charts, NO logos, NO words.\n"
            f"- Avoid tired stock clichés ('suited handshake', 'generic skyline') but stay ON-TOPIC and literal.\n"
            f"- color_theme must match the emotional tone (e.g. crimson=danger/biotech, forest=green energy, "
            f"volcanic=oil/energy, indigo=tech, ocean=global finance, vivid_violet=entertainment)"
        )
        # ★ 비필수 (ERRORS [368]): 썸네일 배경 프롬프트는 결정론 폴백이 있으므로 스로틀 시 즉시 폴백
        raw = (invoke_text("writer_fast", req, timeout=45, _nonessential=True) or "").strip()
        m = re.search(r'\{.*?\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group())
            photo = data.get("photo_prompt", "").strip().strip('"\'')
            theme = data.get("color_theme", "").strip()
            if len(photo) > 20 and theme in theme_names:
                log.info(f"[thumbnail] LLM → theme={theme}, prompt={photo[:80]}")
                result = {"photo_prompt": photo, "color_theme": theme}
                _PARAM_CACHE[cache_key] = result
                # 캐시 크기 제한 (32개 초과 시 가장 오래된 항목 제거)
                if len(_PARAM_CACHE) > 32:
                    oldest = next(iter(_PARAM_CACHE))
                    del _PARAM_CACHE[oldest]
                return result
    except Exception as e:
        log.warning(f"[thumbnail] LLM 파라미터 실패 ({e})")
    return {}


# ── 유틸 ─────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False):
    from PIL import ImageFont
    idx = 6 if bold else 0
    for path, i in [(_FONT_TTC, idx), (_FONT_TTC, 0), (_FONT_SUPP, 0)]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=i)
            except Exception:
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
    return ImageFont.load_default()


def _textsize(text: str, font) -> tuple[int, int]:
    from PIL import Image as _I, ImageDraw as _ID
    d = _ID.Draw(_I.new("RGB", (1, 1)))
    bb = d.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _wrap(text: str, max_chars: int = 18) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 > max_chars and cur:
            lines.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines[:3]


def _draw_glow(img, text: str, x: int, y: int, font,
               text_col: tuple, glow_col: tuple, radius: int = 20, passes: int = 3):
    from PIL import Image as _I, ImageDraw as _ID, ImageFilter as _IF
    layer = _I.new("RGBA", img.size, (0, 0, 0, 0))
    _ID.Draw(layer).text((x, y), text, font=font, fill=(*glow_col, 255))
    blurred = layer.filter(_IF.GaussianBlur(radius=radius))
    result = img.convert("RGBA")
    for _ in range(passes):
        result = _I.alpha_composite(result, blurred)
    d = _ID.Draw(result)
    d.text((x + 3, y + 4), text, font=font, fill=(0, 0, 0, 100))
    d.text((x, y), text, font=font, fill=(*text_col, 255))
    return result.convert("RGB")


def _draw_text_shadow(img, text: str, x: int, y: int, font, color: tuple):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.text((x + 2, y + 3), text, font=font, fill=(0, 0, 0, 140))
    d.text((x, y), text, font=font, fill=color)
    return img


# ── AI 사진 생성 ───────────────────────────────────────────────

def _generate_photo(keyword: str, title: str, seed: int, tmp_dir: Path,
                    platform: str = "naver", prompt_en: str = "") -> Path | None:
    """AI 사진 생성 — Pollinations.ai 단독.

    ★ 사용자 박제 2026-06-07 (ERRORS [263]) — Bing / HuggingFace 완전 삭제.
    Bing 쿠키 무한 만료 + HuggingFace DNS 차단·hf-inference 미지원 → 전멸 → 폐기.
    """
    base_prompt = prompt_en if prompt_en else (
        f"Professional editorial photography, {keyword} theme, "
        f"dramatic lighting, cinematic wide angle, high quality"
    )
    full_prompt = f"{base_prompt}, ultra high quality, professional photography, 4k, no text no watermark"

    return _generate_photo_pollinations(full_prompt, seed, tmp_dir)


def _generate_photo_pollinations(full_prompt: str, seed: int, tmp_dir: Path) -> Path | None:
    """Pollinations.ai 생성 — 큐 제한 시 8초 후 최대 3회 재시도 (사용자 박제 2026-07-06: 재시도 상한 통일 3회)."""
    from JARVIS06_IMAGE.providers.pollinations_provider import PollinationsProvider
    log.info(f"[thumbnail] Pollinations 요청: {full_prompt[:70]}")
    for attempt in range(3):
        try:
            path = PollinationsProvider().generate(
                full_prompt, tmp_dir, width=W, height=H, seed=seed + attempt
            )
            log.info(f"[thumbnail] Pollinations 완료: {path}")
            return path
        except Exception as e:
            err_msg = str(e)
            if attempt < 2 and ("Queue full" in err_msg or "429" in err_msg):
                log.info("[thumbnail] Pollinations 큐 제한 → 8초 후 재시도")
                time.sleep(8)
                continue
            log.warning(f"[thumbnail] Pollinations 실패 ({e}) → 그라디언트 폴백")
            return None
    return None


# ── 레이아웃 1: 트리픽 (3분할) ───────────────────────────────

def _apply_triptych(photo: "Image.Image", title: str, keyword: str,
                    today: str, scheme: dict, rng: random.Random,
                    show_dividers: bool = True, tag_line: str = "") -> "Image.Image":
    """사진을 3분할해 각 패널에 살짝 다른 색조 → 대형 키워드 글로우 오버레이."""
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

    IW, IH = photo.size

    if show_dividers:
        # 실제 사진: 3분할 패널 + 구분선
        gap = 5
        pw = (IW - gap * 2) // 3
        canvas = Image.new("RGB", (IW, IH), (8, 8, 10))
        brightnesses = sorted([rng.uniform(0.75, 0.95),
                               rng.uniform(0.85, 1.0),
                               rng.uniform(0.7, 0.9)])
        for i in range(3):
            x0 = i * IW // 3
            x1 = (i + 1) * IW // 3
            strip = photo.crop((x0, 0, x1, IH)).resize((pw, IH), Image.LANCZOS)
            strip = ImageEnhance.Brightness(strip).enhance(brightnesses[i])
            if i == 1:
                strip = ImageEnhance.Color(strip).enhance(1.2)
            canvas.paste(strip, (i * (pw + gap), 0))
        grad = Image.new("RGBA", (IW, IH), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for y in range(IH // 3, IH):
            a = int(200 * (y - IH // 3) / (IH * 2 / 3))
            gd.rectangle([(0, y), (IW, y + 1)], fill=(0, 0, 0, min(a, 200)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), grad).convert("RGB")
        draw = ImageDraw.Draw(canvas)
        for i in (1, 2):
            x = i * (pw + gap) - gap
            draw.rectangle([(x, 0), (x + gap - 1, IH)], fill=(20, 20, 22))
    else:
        # 폴백 그라디언트: 분할 없이 그대로 사용 + 하단 오버레이만
        canvas = photo.copy()
        grad = Image.new("RGBA", (IW, IH), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for y in range(IH // 2, IH):
            a = int(180 * (y - IH // 2) / (IH // 2))
            gd.rectangle([(0, y), (IW, y + 1)], fill=(0, 0, 0, min(a, 180)))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), grad).convert("RGB")

    # ── 타이포 ──────────────────────────────────────
    kw_size = 140 if len(keyword) <= 3 else (110 if len(keyword) <= 5 else 82)
    kw_font = _load_font(kw_size, bold=True)
    kw_x, kw_y = 50, int(IH * 0.42)
    canvas = _draw_glow(canvas, keyword, kw_x, kw_y, kw_font,
                        scheme["accent"], scheme["accent"], radius=28, passes=4)

    # 구분 라인
    tw, th = _textsize(keyword, kw_font)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([(kw_x, kw_y + th + 12), (kw_x + tw, kw_y + th + 16)],
                   fill=scheme["accent"])

    # 소제목 (#해시태그 스타일)
    sub = f"# {title[:32]}"
    s_font = _load_font(30, bold=False)
    canvas = _draw_text_shadow(canvas, sub, kw_x, kw_y + th + 26, s_font, (220, 222, 225))

    # 날짜 (우하단)
    draw = ImageDraw.Draw(canvas)
    draw.text((IW - 44, IH - 32), today, font=_load_font(20),
              fill=(180, 185, 195), anchor="rm")

    # 카테고리 태그 (좌하단) — 제공 시만
    _tag = (tag_line or "").strip()
    if _tag:
        draw.text((44, IH - 32), f"★ {_tag} ★", font=_load_font(18),
                  fill=scheme["accent"], anchor="lm")

    # 상단 얇은 액센트 바
    draw.rectangle([(0, 0), (IW, 6)], fill=scheme["accent"])

    return canvas


# ── 레이아웃 2: 에디토리얼 (폴라로이드) ──────────────────────

def _apply_editorial(photo: "Image.Image", title: str, keyword: str,
                     today: str, scheme: dict, rng: random.Random,
                     tag_line: str = "") -> "Image.Image":
    """폴라로이드 프레임 + 파스텔 배경 + 데코 요소 — 감성 블로그 스타일."""
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

    IW, IH = W, H
    pastel = scheme["pastel"]

    # ── 파스텔 배경 ──────────────────────────────────
    canvas = Image.new("RGB", (IW, IH), pastel)
    draw = ImageDraw.Draw(canvas)

    # 도트 그리드 패턴
    dot_c = tuple(max(0, c - 22) for c in pastel)
    for x in range(0, IW, 28):
        for y in range(0, IH, 28):
            draw.ellipse([(x - 2, y - 2), (x + 2, y + 2)], fill=dot_c)

    # 코너 곡선 장식
    accent_light = tuple(min(255, c + 40) for c in scheme["accent"])
    _draw_corner_deco(draw, IW, IH, scheme["accent"], rng)

    # ── 폴라로이드 프레임 ────────────────────────────
    ph_w, ph_h = 520, 400
    resized = photo.resize((ph_w, ph_h), Image.LANCZOS)
    resized = ImageEnhance.Color(resized).enhance(1.1)

    brd = 14
    brd_b = 50  # 아래쪽 여백 (폴라로이드 특징)
    frame = Image.new("RGB", (ph_w + brd * 2, ph_h + brd + brd_b), (255, 255, 255))
    frame.paste(resized, (brd, brd))

    # 그림자
    angle = rng.uniform(-8, -3)
    shadow_offset = 14
    frame_rot = frame.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    fw, fh = frame_rot.size
    px = 45
    py = (IH - fh) // 2 + rng.randint(-15, 15)

    shadow_layer = Image.new("RGBA", (IW, IH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow_layer)
    sd.rectangle([(px + shadow_offset, py + shadow_offset),
                  (px + fw + shadow_offset, py + fh + shadow_offset)],
                 fill=(0, 0, 0, 75))
    shadow_b = shadow_layer.filter(ImageFilter.GaussianBlur(radius=18))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow_b).convert("RGB")

    # 폴라로이드 붙이기
    canvas.paste(frame_rot, (px, py), mask=None)

    # ── 테이프 장식 ──────────────────────────────────
    tape_c = scheme["accent"]
    draw = ImageDraw.Draw(canvas)
    # 좌상단 테이프
    _draw_tape(draw, px - 10, py + 15, 55, 16, tape_c, angle=-12)
    # 우하단 테이프
    _draw_tape(draw, px + fw - 40, py + fh - 30, 55, 16, tape_c, angle=8)

    # ── 텍스트 영역 (우측) ───────────────────────────
    tx = px + fw + 55
    ty = IH // 4

    # 날짜 소형 텍스트
    draw.text((tx, ty), today, font=_load_font(22), fill=scheme["badge"])

    # 키워드 대형 텍스트 (배경 필 포함)
    kw_size = 96 if len(keyword) <= 3 else (76 if len(keyword) <= 5 else 58)
    kw_font = _load_font(kw_size, bold=True)
    kw_w, kw_h = _textsize(keyword, kw_font)
    pad = 14
    # 키워드 배경 박스
    draw.rounded_rectangle(
        [(tx - pad, ty + 36), (tx + kw_w + pad, ty + 36 + kw_h + pad)],
        radius=12, fill=scheme["badge"]
    )
    draw.text((tx, ty + 36 + pad // 2), keyword, font=kw_font, fill=(255, 255, 255))

    # 부제목 줄바꿈
    ty2 = ty + 36 + kw_h + pad + 28
    lines = _wrap(title, 16)
    dark_c = tuple(max(0, c - 100) for c in pastel)
    for li, ln in enumerate(lines):
        t_font = _load_font(30 if li == 0 else 26, bold=(li == 0))
        draw.text((tx, ty2 + li * 40), ln, font=t_font, fill=dark_c)

    # 소형 태그 라인 — 카테고리 라벨 동적 (경제 브리핑 / 테마 분석 / 키워드)
    _tag = (tag_line or keyword or "").strip() or keyword
    draw.text((tx, IH - 48), f"★ {_tag} ★", font=_load_font(18),
              fill=scheme["badge"])

    return canvas


def _draw_corner_deco(draw, IW: int, IH: int, accent: tuple, rng: random.Random) -> None:
    """코너에 원호 장식"""
    r = rng.randint(120, 200)
    thick = rng.randint(6, 12)
    alpha_light = tuple(min(255, c + 80) for c in accent)
    # 우상단
    draw.arc([(IW - r * 2, -r), (IW, r)], start=90, end=180, fill=alpha_light, width=thick)
    draw.arc([(IW - r * 2 - 30, -r - 30), (IW + 30, r + 30)], start=90, end=180,
             fill=tuple(max(0, c - 20) for c in accent), width=thick // 2)
    # 좌하단
    draw.arc([(-r, IH - r), (r, IH + r), ], start=270, end=360, fill=alpha_light, width=thick)


def _draw_tape(draw, x: int, y: int, w: int, h: int,
               color: tuple, angle: float = 0) -> None:
    """테이프 스티커 (반투명 직사각형)"""
    tape_alpha = (*color, 190)
    # 간단하게 평행사변형으로 그림
    angle_r = math.radians(angle)
    dx = int(h * math.sin(angle_r))
    pts = [
        (x + dx, y),
        (x + dx + w, y),
        (x + w, y + h),
        (x, y + h),
    ]
    draw.polygon(pts, fill=tape_alpha)
    # 테이프 질감선 (반투명 세로선)
    for i in range(3, w, 8):
        draw.line([(x + dx + i, y), (x + i, y + h)],
                  fill=(*color, 80), width=1)


# ── PIL 그라디언트 폴백 ─────────────────────────────────────

def _make_gradient_fallback(scheme: dict, rng: random.Random) -> "Image.Image":
    """Pollinations 실패 시 — 4코너 그라디언트 + 보케."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    x = np.linspace(0.0, 1.0, W, dtype=np.float32)[np.newaxis, :]
    y = np.linspace(0.0, 1.0, H, dtype=np.float32)[:, np.newaxis]
    c00, c01 = np.array(scheme["c00"]), np.array(scheme["c01"])
    c10, c11 = np.array(scheme["c10"]), np.array(scheme["c11"])
    arr = np.zeros((H, W, 3), dtype=np.float32)
    for i in range(3):
        arr[:, :, i] = (c00[i]*(1-x)*(1-y) + c01[i]*x*(1-y) +
                        c10[i]*(1-x)*y       + c11[i]*x*y)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    # 보케
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for _ in range(12):
        cx = rng.randint(-W//4, W+W//4)
        cy = rng.randint(-H//4, H+H//4)
        r  = rng.randint(60, 220)
        ld.ellipse([cx-r, cy-r, cx+r, cy+r],
                   fill=(*scheme["accent"], rng.randint(20, 55)))
    img = Image.alpha_composite(img.convert("RGBA"),
                                layer.filter(__import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(50))
                                ).convert("RGB")
    return img


# ── 퍼블릭 API ───────────────────────────────────────────────

def create_thumbnail(theme: str, title: str, output_path: str, body_text: str = "",
                     platform: str = "naver", tag_line: str = "") -> str:
    """블로그 대표 썸네일 생성 — 주제를 대표하는 AI 사진 임베드 (★ 사용자 박제 2026-07-05).

    1) AI 사진 + 감성 에디토리얼(폴라로이드) 오버레이 — 주제 대표 이미지 임베드 (기본)
    2) 그라디언트 폴백 (사진 실패 시 _create 내부에서 자동 처리)
    3) matplotlib 카드 (최후)

    ★ 구버전 저품질 SVG 인포그래픽 썸네일 폐기 (ERRORS [356]) — 실사진 없는
      값싼 그래픽은 클릭을 유도하지 못함. 데이터 이미지가 아닌 *대표 사진* 이 원칙.
    """
    today_str = date.today().strftime("%Y.%m.%d")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 1순위: AI 사진 기반 에디토리얼 (주제 대표 이미지 임베드)
    try:
        return _create(theme, title, output_path, today_str, platform=platform,
                       tag_line=tag_line)
    except Exception as e:
        log.warning(f"[thumbnail_maker] AI 사진 실패 → matplotlib 폴백 ({e})")
        _g_report("image", e, module=__name__)
        return _simple_fallback(theme, output_path, today_str)


def _create(theme: str, title: str, output_path: str, today_str: str,
            platform: str = "naver", tag_line: str = "") -> str:
    import tempfile
    from PIL import Image

    # 나노초 seed → 항상 다른 랜덤
    seed_ns = int(hashlib.md5(f"{theme}{title}{time.time_ns()}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed_ns)
    seed_poll = seed_ns % 999_999_999

    # LLM이 글 내용 보고 대표 사진 프롬프트·색상 결정
    params = _llm_thumbnail_params(title, theme)

    # 색상 테마 — LLM 선택 우선, 실패 시 완전 랜덤
    theme_name = params.get("color_theme", "")
    matched = [t for t in _COLOR_THEMES if t["name"] == theme_name]
    scheme = matched[0] if matched else rng.choice(_COLOR_THEMES)

    # 사진 프롬프트 — LLM 생성 우선, 실패 시 generic fallback
    photo_prompt = params.get("photo_prompt", "")

    tmp_dir = Path(tempfile.mkdtemp())
    photo_path = _generate_photo(theme, title, seed_poll, tmp_dir, platform=platform,
                                  prompt_en=photo_prompt)

    # ★ 레이아웃 (사용자 박제 2026-07-05): 실사진 확보 시 *항상* 에디토리얼(폴라로이드).
    #   주제 대표 사진을 프레임에 임베드하는 것이 사용자 선호 스타일. triptych 는
    #   사진을 못 구한 그라디언트 폴백 전용 (저품질 SVG 는 폐기됨).
    if photo_path and photo_path.exists():
        photo = Image.open(str(photo_path)).convert("RGB").resize((W, H), Image.LANCZOS)
        img = _apply_editorial(photo, title, theme, today_str, scheme, rng, tag_line=tag_line)
        layout = "editorial"
    else:
        photo = _make_gradient_fallback(scheme, rng)
        img = _apply_triptych(photo, title, theme, today_str, scheme, rng,
                              show_dividers=False, tag_line=tag_line)
        layout = "triptych(fallback)"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", dpi=(144, 144))
    log.info(f"[thumbnail_maker] 저장: {output_path} (layout={layout}, scheme={scheme['name']})")
    return output_path


def _simple_fallback(theme: str, output_path: str, today_str: str) -> str:
    """matplotlib 기반 동적 썸네일 — AI·SVG 모두 실패 시 최종 폴백.

    테마 해시로 매번 다른 배경색·레이아웃 선택.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import hashlib as _hl

        seed = int(_hl.md5(f"{theme}{today_str}".encode()).hexdigest()[:8], 16)
        rng2 = random.Random(seed)

        # 배경 팔레트 (밝고 다양한 파스텔)
        bg_options = [
            ("#E8F4FD", "#1565C0"), ("#FFF3E0", "#E65100"), ("#F3E5F5", "#6A1B9A"),
            ("#E8F5E9", "#1B5E20"), ("#FFF8E1", "#F57F17"), ("#FCE4EC", "#880E4F"),
            ("#E3F2FD", "#0D47A1"), ("#F1F8E9", "#33691E"), ("#FBE9E7", "#BF360C"),
        ]
        accent_options = [
            "#1565C0", "#E65100", "#6A1B9A", "#1B5E20", "#F57F17",
            "#880E4F", "#0D47A1", "#33691E", "#BF360C",
        ]
        bg_color, text_color = bg_options[seed % len(bg_options)]
        accent = accent_options[(seed + 3) % len(accent_options)]

        fig, ax = plt.subplots(figsize=(12, 6.3), dpi=150)
        fig.patch.set_facecolor(bg_color)
        ax.set_facecolor(bg_color)
        ax.axis("off")

        # 상단 강조 바
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.0, 0.93), 1.0, 0.07, transform=ax.transAxes,
            boxstyle="square,pad=0", facecolor=accent, linewidth=0,
        ))
        # 날짜
        ax.text(0.5, 0.88, today_str, transform=ax.transAxes,
                ha="center", va="top", fontsize=14, color=text_color, alpha=0.7)
        # 키워드 배지
        ax.add_patch(mpatches.FancyBboxPatch(
            (0.15, 0.52), 0.70, 0.22, transform=ax.transAxes,
            boxstyle="round,pad=0.02", facecolor=accent, linewidth=0, alpha=0.15,
        ))
        ax.text(0.5, 0.64, theme[:16], transform=ax.transAxes,
                ha="center", va="center", fontsize=42, color=accent,
                fontweight="bold")
        # 구분선
        ax.axhline(y=0.48, xmin=0.2, xmax=0.8, color=accent, linewidth=1.5, alpha=0.5,
                   transform=ax.transAxes)
        # 장식 원
        for xi, yi, r, a in rng2.choices(
            [(0.08, 0.85, 0.06, 0.12), (0.92, 0.85, 0.06, 0.10),
             (0.05, 0.20, 0.08, 0.08), (0.95, 0.20, 0.08, 0.08)], k=4
        ):
            ax.add_patch(plt.Circle((xi, yi), r, transform=ax.transAxes,
                                    color=accent, alpha=a))
        # 하단 태그
        ax.text(0.5, 0.08, f"★ {theme} ★", transform=ax.transAxes,
                ha="center", va="bottom", fontsize=18, color=accent, alpha=0.8)

        plt.tight_layout(pad=0.1)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor=bg_color)
        plt.close(fig)
        log.info(f"[thumbnail_maker] matplotlib 폴백 썸네일 생성: {output_path}")
    except Exception as _e:
        log.warning(f"[thumbnail_maker] matplotlib 폴백도 실패: {_e}")
        # PIL 최후 폴백
        try:
            rng3 = random.Random(int(time.time()))
            scheme = rng3.choice(_COLOR_THEMES)
            img = _make_gradient_fallback(scheme, rng3)
            d = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).ImageDraw.Draw(img)
            f = _load_font(80, bold=True)
            d.text((W//2, H//2), theme[:10], font=f, fill=scheme["accent"], anchor="mm")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            img.save(output_path, "PNG")
        except Exception:
            pass
    return output_path


# economic_charts.py 에서 import 하는 심볼
_COLOR_THEMES = _COLOR_THEMES  # re-export
_FONT = _FONT_TTC

def _rgba(hex_c, alpha):
    try:
        if isinstance(hex_c, tuple):
            r, g, b = hex_c[:3]
        elif isinstance(hex_c, str) and hex_c.startswith("rgba("):
            # already rgba(...) — extract r,g,b and replace alpha
            parts = hex_c[5:].rstrip(")").split(",")
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
        elif isinstance(hex_c, str) and hex_c.startswith("rgb("):
            parts = hex_c[4:].rstrip(")").split(",")
            r, g, b = int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
        else:
            h = str(hex_c).lstrip("#")
            if len(h) == 3:  # CSS shorthand #abc → #aabbcc
                h = h[0]*2 + h[1]*2 + h[2]*2
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except Exception:
        r, g, b = 128, 128, 128  # fallback gray
    return f"rgba({r},{g},{b},{alpha})"

__all__ = ["create_thumbnail", "_COLOR_THEMES", "_FONT", "_rgba"]
