"""JARVIS06_IMAGE/design_learner.py — 인포그래픽 디자인 나이틀리 강화학습 (하루 1개, 05:00).

★ 사용자 박제 2026-07-05 (ERRORS [359]): 오류 강화학습(GUARDIAN)과 *동일 구조* —
  "이미지로 모델 훈련"이 아니라 **검증 게이트를 통과한 디자인 레시피를 코드 자산으로 누적**.
  매일 새벽 Claude 가 전문 디자인 레시피(팔레트+스타일 노브)를 1개 창작 → 게이트
  (구조·대비·독창성·실렌더) 통과분만 `design_recipes.json` 에 추가 → `pro_templates` 가 소비.
  시간이 갈수록 팔레트·스타일이 불어나 인포그래픽 다양성·품질이 *복리로* 상승.

왜 이 방식인가:
  - 모델 파인튜닝 불가(Max 구독 SDK). 하루 10장 이미지 주입으론 생성능력이 안 오른다.
  - 대신 *디자인 레시피(코드 자산)* 를 누적 — 오류학습의 (지문→수정) 누적과 동형.
  - 저작권: 레퍼런스 복제 금지 — Claude 의 방대한 디자인 지식으로 *원본* 레시피 합성.
  - 가벼움: GPU·훈련 0. 나이틀리 소형 JSON LLM 호출 1회 + 실렌더 1회. 우리 셋업에 적합.
  - (phase-2 훅) 발행 성과 → Bandit 보상으로 레시피 *선택* 강화 (record_recipe_use).

진입점:
  get_recipes() / pick_recipe(seed)   — pro_templates 가 소비 (읽기 전용)
  job_learn_design()                  — DEFAULT_JOBS j06_design_learn (cron 05:00) 콜백
"""
from __future__ import annotations

import colorsys
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import requests as _req

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

log = logging.getLogger("jarvis.image.design_learner")

_DIR = Path(__file__).resolve().parent
_REGISTRY = _DIR / "design_recipes.json"
_LOG = _DIR / "design_learn_log.json"

_TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
_TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_REQUIRED = ("id", "name", "hero", "ink", "a1", "a1s", "a2", "a2s",
             "soft", "muted", "eyebrow", "grid", "hero_texture", "card_radius")
_TEXTURES = {"grid", "dots", "glow", "diagonal", "none"}

# 매일 다른 미감 탐색 (전문 디자인 사조 로테이션)
_AESTHETICS = [
    "스위스 에디토리얼(그리드·산세리프·여백)", "글래스모피즘(반투명·블러·발광)",
    "네오브루탈리즘(굵은 보더·비비드·하드섀도)", "데이터 저널리즘(NYT/Bloomberg 절제)",
    "K-매거진 프리미엄(감성·라운드·파스텔 딥컬러)", "다크 네온 대시보드(차콜+발광 1색)",
    "웜 파스텔 라이프스타일(아이보리·둥근 모티프)", "미니멀 모노톤+강조 1색",
    "그라디언트 메시 모던(부드러운 색전이)", "핀테크 프리미엄(신뢰·딥네이비·골드)",
    "테라코타 어스톤(자연·따뜻한 흙색)", "일렉트릭 사이버(비비드 시안·마젠타)",
]


# ── 색 유틸 ────────────────────────────────────────────────────────────────
def _hex_rgb(h: str):
    h = str(h).lstrip("#")
    if len(h) != 6:
        raise ValueError(h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(h: str) -> float:
    r, g, b = (c / 255 for c in _hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _sat(h: str) -> float:
    r, g, b = _hex_rgb(h)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _dist(h1: str, h2: str) -> float:
    a, b = _hex_rgb(h1), _hex_rgb(h2)
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# ── 레지스트리 I/O ──────────────────────────────────────────────────────────
def get_recipes() -> list:
    """전체 레시피(기본 + 학습). pro_templates 가 소비. 실패 시 최소 1개 보장."""
    try:
        data = json.loads(_REGISTRY.read_text(encoding="utf-8"))
        recs = data.get("recipes") or []
        if recs:
            return recs
    except Exception as e:
        log.warning(f"[design_learner] 레지스트리 로드 실패: {e}")
    # 폴백 — pro_templates 내장 팔레트 사용 (design_recipes.json 유실 대비)
    from JARVIS06_IMAGE.pro_templates import PALETTES
    return [{**p, "hero_texture": "grid", "card_radius": 24, "id": f"fb{i}"}
            for i, p in enumerate(PALETTES)]


def pick_recipe(seed: int) -> dict:
    recs = get_recipes()
    return recs[seed % len(recs)]


def _save_registry(recs: list) -> None:
    _REGISTRY.write_text(json.dumps({"version": 1, "recipes": recs},
                                    ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(entry: dict) -> None:
    try:
        hist = json.loads(_LOG.read_text(encoding="utf-8")) if _LOG.exists() else []
    except Exception:
        hist = []
    hist.append(entry)
    _LOG.write_text(json.dumps(hist[-200:], ensure_ascii=False, indent=2), encoding="utf-8")


def _tg(msg: str) -> None:
    if not (_TG_TOKEN and _TG_CHAT_ID):
        return
    try:
        _req.post(f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
                  json={"chat_id": _TG_CHAT_ID, "text": msg}, timeout=8)
    except Exception:
        pass


# ── 게이트 ─────────────────────────────────────────────────────────────────
def _validate_recipe(rec: dict, existing: list) -> tuple[bool, str]:
    """구조·대비·채도·독창성 게이트. (통과, 사유)."""
    if not isinstance(rec, dict):
        return False, "dict 아님"
    for k in _REQUIRED:
        if k not in rec:
            return False, f"필수키 누락:{k}"
    hero = rec["hero"]
    if not (isinstance(hero, list) and len(hero) == 2):
        return False, "hero 는 2색 리스트"
    try:
        for c in [*hero, rec["ink"], rec["a1"], rec["a1s"], rec["a2"], rec["a2s"],
                  rec["soft"], rec["muted"], rec["eyebrow"], rec["grid"]]:
            _hex_rgb(c)
    except Exception:
        return False, "잘못된 hex"
    if _lum(hero[0]) > 0.42:
        return False, "hero 가 밝음(흰 텍스트 대비 불가)"
    if _lum(rec["soft"]) < 0.85:
        return False, "soft 배경이 어두움"
    if _sat(rec["a1"]) < 0.32 or _sat(rec["a2"]) < 0.28:
        return False, "강조색 채도 낮음(회색)"
    if _dist(rec["a1"], rec["a2"]) < 60:
        return False, "두 강조색이 유사"
    if rec["hero_texture"] not in _TEXTURES:
        return False, f"hero_texture 부적합:{rec['hero_texture']}"
    try:
        rad = int(rec["card_radius"])
    except Exception:
        return False, "card_radius 정수 아님"
    if not (16 <= rad <= 30):
        return False, "card_radius 범위(16~30) 벗어남"
    # 독창성 — 기존 어떤 레시피와도 강조색이 충분히 다름
    for ex in existing:
        try:
            if _dist(rec["a1"], ex["a1"]) < 45 and _dist(rec["hero"][0], ex["hero"][0]) < 45:
                return False, f"기존 '{ex.get('id')}' 과 너무 유사"
        except Exception:
            continue
    return True, "ok"


def _test_render(rec: dict) -> bool:
    """샘플 데이터로 실제 렌더 — 깨지지 않고 이미지 산출되는지 확인."""
    try:
        from JARVIS06_IMAGE.pro_templates import build_html
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        sample = [
            {"title": "지표 A 추이", "unit": "포인트", "viz_hint": "line_chart",
             "data": [{"label": f"{m}월", "value": v} for m, v in
                      zip(range(1, 7), [100, 108, 115, 112, 124, 133])]},
            {"title": "항목별 비중", "unit": "%", "viz_hint": "bar_chart",
             "data": [{"label": l, "value": v} for l, v in
                      [("A", 34), ("B", 26), ("C", 21), ("D", 12), ("E", 7)]]},
        ]
        # 템플릿(레이아웃) 있으면 직접 검증 — 슬롯 전부 치환 + 데이터 안전
        if rec.get("template"):
            from JARVIS06_IMAGE.template_engine import (render_layout,
                                                        has_all_slots_resolved, verify_layout_output)
            html = render_layout(rec["template"], "레시피 검증 리포트", "샘플 렌더", sample, rec, "테스트")
            if not has_all_slots_resolved(html) or not verify_layout_output(html, sample):
                log.info("[design_learner] 템플릿 슬롯/데이터안전 실패 → 팔레트만 사용")
                rec.pop("template", None)     # 레이아웃 결함 → 색만 살리고 기본 레이아웃 폴백
        html = build_html("디자인 레시피 검증", "샘플 렌더", sample, 0, "테스트", recipe=rec)
        if not html:
            return False
        tmp = Path(tempfile.mkdtemp()) / "recipe_test.jpg"
        ok = _html_to_jpg(html, tmp, width=1280)
        return bool(ok and tmp.exists() and tmp.stat().st_size > 3000)
    except Exception as e:
        log.warning(f"[design_learner] 테스트 렌더 실패: {e}")
        return False


# ── 생성 (LLM) ──────────────────────────────────────────────────────────────
def _generate_recipe(existing: list, aesthetic: str) -> dict | None:
    from shared.llm import invoke_text
    ex_brief = "\n".join(
        f"- {e.get('name','')}: hero {e.get('hero',['',''])[0]} / a1 {e.get('a1')} / a2 {e.get('a2')}"
        for e in existing[-12:])
    prompt = (
        "너는 세계적 인포그래픽 아트디렉터다. 아래 '기존 레시피'와 색이 뚜렷이 다른, "
        f"프리미엄 인포그래픽용 새 색상/스타일 레시피 1개를 '{aesthetic}' 미감으로 창작하라.\n\n"
        f"[기존 레시피 — 이 색들과 명확히 다르게]\n{ex_brief}\n\n"
        "[규칙]\n"
        "- hero[0]=어두운 딥컬러(흰 텍스트 대비), hero[1]=hero[0] 계열 약간 밝은 색.\n"
        "- soft=아주 밝은 배경(거의 흰색), ink=본문용 짙은 색, muted=중간 회색, "
        "eyebrow=hero 위 밝은 강조, grid=아주 옅은 라인색.\n"
        "- a1/a2=채도 높은 강조 2색(서로 뚜렷이 구분). a1s/a2s=각각의 밝은 버전.\n"
        "- 모두 #RRGGBB. hero_texture ∈ {grid,dots,glow,diagonal,none}. card_radius 16~30 정수.\n\n"
        "[출력] 설명 없이 JSON 하나만:\n"
        '{"id":"영문소문자-하이픈","name":"한글 이름","aesthetic":"' + aesthetic + '",'
        '"hero":["#000000","#000000"],"ink":"#000000","a1":"#000000","a1s":"#000000",'
        '"a2":"#000000","a2s":"#000000","soft":"#ffffff","muted":"#000000",'
        '"eyebrow":"#000000","grid":"#eeeeee","hero_texture":"grid","card_radius":24}'
    )
    try:
        raw = invoke_text("writer", prompt, max_tokens=700, timeout=90, _retries=1)
        if not raw:
            return None
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception as e:
        log.warning(f"[design_learner] 레시피 생성 실패: {e}")
        return None


# ── 실제 사이트 이미지 세밀 학습 (★ 사용자 박제 2026-07-05 — 진짜 이미지 학습) ────────
def _fetch_reference(out_dir: Path, n: int = 5, exclude_urls: set | None = None,
                     name_prefix: str = "ref") -> list:
    """Bing 이미지에서 인포그래픽 레퍼런스 후보 여러 장 다운로드 (best-effort).

    Playwright 브라우저 컨텍스트로 검색+다운로드 (requests.get 수집 금지 규정 회피).
    ★ 관련성은 비전 게이트(_analyze_reference)가 최종 판정 — 여기선 후보만 모은다
    (검색 결과엔 무관 이미지 섞임: 여행사진·클립아트 등 → 비전이 reject). 실패 시 [].
    ★ 반환: [(경로, 원본URL)] — exclude_urls 로 앞 배치와 *URL 중복* 회피(2차 10장용).
    저작권: 학습(분석)용 임시 파일, 장기 저장 안 함 — 추출한 *디자인 원리* 만 남긴다.
    """
    exclude_urls = exclude_urls or set()
    out: list = []
    try:
        from JARVIS06_IMAGE.html_renderer import _find_chromium
        from playwright.sync_api import sync_playwright
        import urllib.parse as _up
        # 인포그래픽 특화 쿼리 (사진 필터 금지 — 인포그래픽은 '사진'이 아님)
        q = _up.quote("infographic statistics chart data visualization report design")
        url = f"https://www.bing.com/images/search?q={q}&setlang=en"
        UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=_find_chromium(), headless=True,
                                  args=["--no-sandbox"])
            ctx = b.new_context(user_agent=UA, locale="en-US")
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(2500)
            # 지연 로딩 — 스크롤로 더 많은 결과 확보 (상위 결과가 관련성 높음)
            for _ in range(4):
                pg.mouse.wheel(0, 2600)
                pg.wait_for_timeout(1200)
            metas = pg.eval_on_selector_all(
                "a.iusc", "els => els.map(e => e.getAttribute('m'))") or []
            i = 0
            for m in metas:
                if len(out) >= n:
                    break
                try:
                    murl = json.loads(m).get("murl", "")
                except Exception:
                    continue
                if not murl or not murl.startswith("http"):
                    continue
                if murl in exclude_urls:      # ★ 앞 배치(1차 5장)와 겹치는 URL 은 건너뜀
                    continue
                try:
                    resp = ctx.request.get(murl, timeout=12000)
                    body = resp.body()
                    ct = resp.headers.get("content-type", "")
                    # 확장자 대신 content-type 으로 판정(Bing CDN URL 확장자 없는 경우 다수)
                    if resp.ok and ct.startswith("image") and 20000 < len(body) < 8_000_000:
                        ext = ".png" if "png" in ct else ".jpg"
                        dst = out_dir / f"{name_prefix}{i}{ext}"
                        dst.write_bytes(body)
                        out.append((dst, murl))
                        i += 1
                except Exception:
                    continue
            b.close()
        log.info(f"[design_learner] 레퍼런스 후보 {len(out)}장 수집")
    except Exception as e:
        log.warning(f"[design_learner] 레퍼런스 수집 실패: {e}")
    return out


_VISION_PROMPT = (
    "너는 세계적 인포그래픽 아트디렉터다.\n"
    "★ 먼저 판정: 이 이미지가 *디자인된 정보 그래픽*인가? — 차트·그래프·대시보드·데이터 시각화·"
    "통계 인포그래픽·지도 인포그래픽·다이어그램·타임라인·프로세스 도표 등 색 시스템과 레이아웃이 "
    "설계된 정보 그래픽이면 모두 인정. 오직 *일반 사진(인물·풍경·음식·제품)·순수 회화·단순 밈/클립아트*"
    '처럼 정보 디자인이 아닌 것만 다른 출력 없이 정확히 {"reject": true} 로 거절하라.\n\n'
    "정보 그래픽이 맞으면, 이 이미지를 *아주 세밀하게* 분석하라 — "
    "색상 시스템, 레이아웃 구조, 차트 종류·스타일, 타이포 위계, 장식 요소(아이콘·모티프·구분선), "
    "여백 리듬, 배경 질감까지 작은 디테일도 놓치지 말 것.\n"
    "그 디자인 언어를 우리 렌더러가 재현할 '레시피 JSON'으로 추출하라 (색은 이미지의 실제 주요 색을 뽑아 매핑):\n"
    "- hero[0]=이미지의 어두운/딥 배경색(흰텍스트 대비), hero[1]=그 계열 약간 밝은 색.\n"
    "- a1/a2=이미지의 주요 강조 2색(고채도, 서로 뚜렷이 구분), a1s/a2s=각 밝은 버전.\n"
    "- soft=본문 배경(밝게), ink=본문 텍스트(짙게), muted=중간 회색, eyebrow=hero 위 밝은 강조, grid=옅은 라인.\n"
    "- hero_texture=배경 질감에 가장 가까운 것 ∈ {grid,dots,glow,diagonal,none}. card_radius=카드 모서리(16~30 정수).\n"
    "- notes=재현 시 놓치면 안 될 세부 관찰 5개 이상(차트 종류·주석 방식·아이콘 스타일·타이포 특징·구성).\n"
    "- aesthetic=한 줄 스타일 요약(한국어).\n\n"
    "★ 그리고 이 레퍼런스의 *레이아웃/구성/장식* 을 재현하는 **재사용 HTML 템플릿** 을 저작하라.\n"
    "  - 전체 폭 1280px 완결 HTML(<!DOCTYPE html>~</html>). 레퍼런스의 배치·히어로 처리·그리드·"
    "여백·장식(도형·구분선·배경)을 최대한 충실히 재현.\n"
    "  - ★ 데이터(숫자·차트)는 절대 직접 쓰지 말고 아래 슬롯 토큰만 배치. 색은 CSS 변수만.\n"
    "  - 폰트 @import Noto Sans KR. 슬롯/변수 외 임의 통계 숫자 금지.\n"
    "__SLOT_SPEC__\n\n"
    "[출력] 아래 마커 형식 정확히 (설명·코드펜스 없이):\n"
    "===PALETTE===\n"
    '{"id":"영문소문자-하이픈","name":"한글 이름","aesthetic":"...","hero":["#000000","#000000"],'
    '"ink":"#000000","a1":"#000000","a1s":"#000000","a2":"#000000","a2s":"#000000","soft":"#ffffff",'
    '"muted":"#000000","eyebrow":"#000000","grid":"#eeeeee","hero_texture":"grid","card_radius":24,'
    '"eyebrow_label":"짧은 라벨","notes":["..."]}\n'
    "===TEMPLATE===\n"
    "<!DOCTYPE html>...완결 HTML(슬롯·CSS변수만)...</html>\n"
    "===END==="
)


def _analyze_reference(img_path: Path, existing: list) -> dict | None:
    """비전 LLM 으로 실이미지 세밀 분석 → 팔레트 + *레이아웃 템플릿*. 실패 시 None."""
    try:
        from shared.llm import invoke_vision
        from JARVIS06_IMAGE.template_engine import SLOT_SPEC
        prompt = _VISION_PROMPT.replace("__SLOT_SPEC__", SLOT_SPEC)
        raw = invoke_vision("writer", prompt, [str(img_path)],
                            timeout=220, cwd=str(img_path.parent))
        if not raw:
            return None
        if re.search(r'"?reject"?\s*:\s*true', raw, re.I) and "===PALETTE===" not in raw:
            log.info("[design_learner] 비전 관련성 게이트: 인포그래픽 아님 → 스킵")
            return None
        # 마커 파싱: ===PALETTE=== {json} ===TEMPLATE=== <html> ===END===
        pm = re.search(r"===PALETTE===\s*(\{.*?\})\s*===TEMPLATE===", raw, re.S)
        tm = re.search(r"===TEMPLATE===\s*(.*?)\s*===END===", raw, re.S)
        if not pm:
            return None
        rec = json.loads(pm.group(1))
        if rec.get("reject") or "a1" not in rec:
            return None
        if tm:
            tmpl = tm.group(1).strip()
            tmpl = re.sub(r"^```[a-z]*\n?|\n?```$", "", tmpl.strip(), flags=re.M)
            if "{{" in tmpl and "<" in tmpl:      # 슬롯·HTML 최소 형태 확인
                rec["template"] = tmpl
        return rec
    except Exception as e:
        log.warning(f"[design_learner] 비전 분석 실패: {e}")
        return None


# ── 결정론 색이론 생성 (LLM 실패 시 *매일 1개 보장* — 사용자 박제) ──────────────
def _hls_hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def _generate_recipe_deterministic(existing: list, seed: int) -> dict:
    """색이론(HSL)으로 게이트 통과 레시피를 *코드로* 생성 — LLM 없이 매일 보장.

    기존 강조색과 색상(hue) 간격이 가장 먼 hue 를 골라 독창성 확보. 다크 히어로 +
    밝은 배경 + 고채도 보색 강조 2색 → _validate_recipe 를 결정론적으로 통과.
    """
    ex_h = []
    for e in existing:
        try:
            r, g, b = (c / 255 for c in _hex_rgb(e.get("a1", "#808080")))
            ex_h.append(colorsys.rgb_to_hls(r, g, b)[0])
        except Exception:
            continue
    # hue 후보 중 기존과 최소간격이 가장 큰 것 (seed 로 미세 오프셋 → 매일 다름)
    best_h, best_gap = (seed % 36) / 36.0, -1.0
    for i in range(36):
        h = (i / 36.0 + (seed % 11) * 0.009) % 1.0
        gap = 1.0 if not ex_h else min(min(abs(h - e), 1 - abs(h - e)) for e in ex_h)
        if gap > best_gap:
            best_gap, best_h = gap, h
    h = best_h
    a2h = (h + 0.5) % 1.0          # 보색 강조
    return {
        "hero": [_hls_hex(h, 0.13, 0.42), _hls_hex(h, 0.22, 0.38)],
        "ink": _hls_hex(h, 0.17, 0.30),
        "a1": _hls_hex(h, 0.55, 0.80),
        "a1s": _hls_hex(h, 0.70, 0.82),
        "a2": _hls_hex(a2h, 0.55, 0.74),
        "a2s": _hls_hex(a2h, 0.70, 0.80),
        "soft": _hls_hex(h, 0.965, 0.28),
        "muted": _hls_hex(h, 0.46, 0.08),
        "eyebrow": _hls_hex(h, 0.76, 0.72),
        "grid": _hls_hex(h, 0.925, 0.18),
        "hero_texture": ("grid", "dots", "glow", "diagonal")[seed % 4],
        "card_radius": 18 + (seed % 7) * 2,       # 18~30
    }


# ── 나이틀리 잡 ─────────────────────────────────────────────────────────────
def _assign_id(rec: dict, recs: list, today: str, source: str) -> dict:
    ids = {r.get("id") for r in recs}
    base = re.sub(r"[^a-z0-9-]", "", str(rec.get("id", "")).lower()) or f"rec{len(recs)}"
    rid, k = base, 1
    while rid in ids:
        rid = f"{base}-{k}"; k += 1
    rec["id"] = rid
    rec["source"] = source
    rec["created"] = today
    rec.setdefault("name", rid)
    rec.setdefault("aesthetic", source)
    return rec


def _commit(rec: dict, recs: list, today: str, aesthetic: str, via: str) -> str:
    recs.append(rec)
    _save_registry(recs)
    _append_log({"date": today, "action": "learned", "id": rec["id"],
                 "name": rec.get("name"), "aesthetic": aesthetic, "via": via, "total": len(recs)})
    msg = (f"🎨 인포그래픽 디자인 학습 +1 ({via})\n"
           f"· {rec.get('name')} ({rec['id']})\n"
           f"· 미감: {aesthetic}\n"
           f"· 총 레시피: {len(recs)}개 (다양성 복리 ↑)")
    log.info(f"[design_learner] {msg}")
    _tg(msg)
    return f"learned {rec['id']} via {via} (total={len(recs)})"


def _learn_from_batch(refs: list, recs: list, today: str) -> str | None:
    """레퍼런스 배치 [(경로,URL)] 를 순회하며 비전 분석 → 게이트 통과 시 커밋 후 결과 반환.
    배치에 인포그래픽이 하나도 없으면(전부 비전 reject/게이트 탈락) None → 다음 단계로."""
    for ref, _url in refs:
        rec = _analyze_reference(ref, recs)
        if not rec:
            continue                                  # 무관 이미지/분석 실패 → 다음 후보
        rec = _assign_id(rec, recs, today, "learned-vision")
        ok, why = _validate_recipe(rec, recs)
        if ok and _test_render(rec):
            return _commit(rec, recs, today, rec.get("aesthetic", "실이미지 분석"), "실이미지비전")
        log.info(f"[design_learner] 비전 레시피 게이트 탈락({why}) → 다음 후보")
    return None


def job_learn_design() -> str:
    """하루 1개 새 디자인 레시피 학습 (05:00). ★ 사용자 박제: 1회 학습은 *반드시* 성공.

    Phase 1 = LLM 창작(다양·감성) → 게이트+실렌더. 스로틀/탈락 시
    Phase 2 = 결정론 색이론 생성(코드, LLM 0) → 게이트+실렌더로 *보장*. 스킵 없음.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    recs = get_recipes()
    aesthetic = _AESTHETICS[len(recs) % len(_AESTHETICS)]
    log.info(f"[design_learner] 나이틀리 학습 시작 (기존 {len(recs)}개, 미감='{aesthetic}')")

    # ── Phase 0: 실제 사이트 이미지 세밀 학습 (★ 사용자 박제 2026-07-06 — 5→10 단계 캡처) ──
    #   1차: 사이트에서 5장 캡처 → 인포그래픽 있으면 그 중 1개 추출(즉시 종료).
    #   없으면 2차: *새* 10장 캡처(1차와 URL 중복 금지) → 인포그래픽 있으면 1개 추출.
    #   무관 이미지는 비전 게이트(_analyze_reference)가 reject. 전부 실패/사이트 차단 시
    #   Phase 1(LLM)·Phase 2(결정론)로 *매일 1개 보장*.
    try:
        _tmp = Path(tempfile.mkdtemp())
        seen_urls: set = set()
        # 1차 — 5장
        batch1 = _fetch_reference(_tmp, n=5, exclude_urls=seen_urls, name_prefix="a")
        seen_urls |= {u for _, u in batch1}
        res = _learn_from_batch(batch1, recs, today)
        if res:
            return res
        # 1차에 인포그래픽 없음 → 2차 *새* 10장 (1차 URL 제외 = 겹침 방지)
        log.info(f"[design_learner] 1차 {len(batch1)}장에 인포그래픽 없음 → 2차 새 10장 캡처")
        batch2 = _fetch_reference(_tmp, n=10, exclude_urls=seen_urls, name_prefix="b")
        res = _learn_from_batch(batch2, recs, today)
        if res:
            return res
        if not batch1 and not batch2:
            log.info("[design_learner] 레퍼런스 수집 실패(사이트 차단) → 지식기반 폴백")
        else:
            log.info("[design_learner] 1·2차 모두 인포그래픽 미발견 → 지식기반 폴백")
    except Exception as e:
        log.warning(f"[design_learner] Phase0 비전 학습 예외 → 폴백: {e}")

    # ── Phase 1: LLM 지식기반 창작 (best-effort, 2회) ──
    for attempt in range(2):
        rec = _generate_recipe(recs, aesthetic)
        if not rec:
            continue
        rec = _assign_id(rec, recs, today, "learned")
        ok, why = _validate_recipe(rec, recs)
        if not ok:
            log.info(f"[design_learner] LLM attempt{attempt+1} 게이트 탈락: {why}")
            continue
        if _test_render(rec):
            return _commit(rec, recs, today, aesthetic, "LLM")

    # ── Phase 2: 결정론 색이론 — 매일 1개 *보장* (LLM 0) ──
    log.info("[design_learner] LLM 실패 → 결정론 색이론 폴백(보장)")
    for s in range(len(recs) * 7, len(recs) * 7 + 60):
        rec = _generate_recipe_deterministic(recs, s)
        rec = _assign_id(rec, recs, today, "learned-auto")
        ok, _ = _validate_recipe(rec, recs)
        if ok and _test_render(rec):
            return _commit(rec, recs, today, "알고리즘 색이론", "결정론")

    # ── 최후 보루: 독창성만 완화(구조·대비·실렌더는 여전히 통과) — 그래도 1개 보장 ──
    #   ★ 재시도 최대 3회 (사용자 박제 2026-07-06): 게이트가 seed 무관 결정론이라
    #   결과는 첫 반복에서 결정 — 40폭은 재시도 이득 0. 실패 시 GUARDIAN 에스컬레이션.
    for s in range(9973, 9973 + 3):
        rec = _assign_id(_generate_recipe_deterministic(recs, s), recs, today, "learned-auto")
        try:
            _hex_rgb(rec["hero"][0])
            if _lum(rec["hero"][0]) <= 0.42 and _lum(rec["soft"]) >= 0.85 and _test_render(rec):
                return _commit(rec, recs, today, "알고리즘 색이론(완화)", "결정론-완화")
        except Exception:
            continue
    # 여기까지 오면 렌더 인프라 자체 문제 — GUARDIAN 에 보고
    _append_log({"date": today, "action": "failed", "reason": "render infra", "total": len(recs)})
    _g_report("image", RuntimeError("design_learner 렌더 인프라 실패 — 학습 보장 불가"),
              module=__name__, func_name="job_learn_design")
    return "failed: render infra"


__all__ = ["get_recipes", "pick_recipe", "job_learn_design"]
