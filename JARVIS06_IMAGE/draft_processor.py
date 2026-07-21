"""JARVIS06_IMAGE/draft_processor.py — 대본 → 완성 블록 단일 진입점.

★ 사용자 박제 2026-05-31 — 이미지 생성 책임 단일화.

흐름:
  JARVIS02(대본 HTML) + JARVIS09(수집 자료·종목 데이터)
    → process_draft()
        ① [CHART_N]...[/CHART_N] 데이터 내장 슬롯 → infographic_engine 고퀄리티 인포그래픽
        ② [PHOTO_N] 플레이스홀더 → 실데이터 인포그래픽 (본문 AI 사진 폐기 2026-07-06)
        ③ (옛 h2→섹션이미지 교체 동작 폐기 — 사용자 박제 2026-05-15 ↔ 2026-06-07)
        ④ SVG 캡처 → JPG
        ⑤ 썸네일 생성
        ⑥ assemble_blocks → (text, image) 블록 조립
    → {"blocks": [...], "thumbnail_path": "...", "title": "...", "html": "..."}

호출자 (JARVIS02 _build_blocks):
    from JARVIS06_IMAGE.draft_processor import process_draft
    result = process_draft(draft_html=html, collected=collected,
                           platform=platform, out_dir=img_dir)
    blocks         = result["blocks"]
    thumbnail_path = result["thumbnail_path"]

★ v2 (Step 6, 2026-07-05): 단일 인자 collected(CollectedData)로 통일. keyword/sector/
  category·검증정답·차트seed·이미지컨텍스트를 전부 collected 에서 파생. 카테고리 노브는
  CATEGORY_POLICY 레지스트리. 본문 이미지=인포그래픽만(폴백 없음) + min-N top-up + 썸네일 필수(누락0).
"""
from __future__ import annotations

import re
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import re as _re_vis
from pathlib import Path

log = logging.getLogger("jarvis.image.draft_processor")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


# ── 본문 이미지 = 인포그래픽 디자인만 (★ 사용자 박제 2026-07-06) ────
#   AI 사진(Pollinations) 영문 프롬프트 빌더·관련성 검증 전면 삭제.
#   본문 이미지는 실데이터 인포그래픽만 — 못 만들면 빈 슬롯. 썸네일만 예외(대표 실사).


# ── 차트 생성 (Pass-2) ────────────────────────────────────────────

def _extract_chart_context(html: str, chart_idx: int) -> str:
    """[CHART_N] 앞뒤 <p> 텍스트 추출 — 차트 설명 컨텍스트."""
    pattern = re.compile(
        r"(<p[^>]*>[\s\S]*?</p>)\s*(?:[^\[]*?)\[CHART_" + str(chart_idx) + r":",
        re.IGNORECASE,
    )
    m = pattern.search(html)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return ""



# ══════════════════════════════════════════════════════════════════════════
# ★ process_draft v2 헬퍼 (Step 6, UNIFIED_PIPELINE_SPEC 2026-07-05)
#   본문 이미지=인포그래픽 디자인만(못 만들면 빈 슬롯) · min-N top-up · 로컬 썸네일 폴백 · collected 검증 ref.
#   (경제 embed-first 래퍼 tistory_html_writer 의 검증된 로직을 단일 소스로 이식)
# ══════════════════════════════════════════════════════════════════════════

# 썸네일 하단 카테고리 라벨 (category → 라벨). 미매칭 시 섹터/키워드 폴백.
_TAG_BY_CATEGORY = {"economic": "경제 브리핑", "theme": "테마 분석"}




def _count_images(html: str) -> int:
    return len(re.findall(r"<img\b", html, re.I)) + len(re.findall(r"<svg\b", html, re.I))


def _infographic_img_html(path, alt: str) -> str:
    """인포그래픽 이미지 <p><img> 래퍼 (본문 이미지=인포그래픽만 — AI 사진 폐기 2026-07-06)."""
    return (f'<p><img src="{path}" alt="{alt}" '
            f'style="width:100%;max-width:760px;border-radius:8px;'
            f'margin:16px auto;display:block;"></p>')


# ── 시각 중복 판정 단일 진입점 (ERRORS [461] — 2026-07-21) ─────────────
#
# ★ 종전 결함: `_title in html_so_far` 로 *본문 전체* 를 부분문자열 검색해
#   **산문에 데이터셋 제목이 언급되기만 해도** 그 데이터셋을 스킵했다.
#   결과: "편의점 품목별 매출 증감률 추이를 보면…" 처럼 데이터를 성실히 설명한
#   글일수록 차트가 사라지고, 이미지 0개 → 헌법 제4조(글 연속+이미지 부재) 위반
#   경고가 떴다. *잘 쓸수록 벌받는* 구조. 티스토리 대본이 수치를 문장으로 풀어
#   쓰는 성향이라 티스토리에서만 재현됐다(경제·테마 공통).
#
# ★ 개념 교정: 산문에서 제목을 언급하는 것은 *중복이 아니라 이상적인 짝* 이다
#   (헌법 제4조 "글↔이미지 교차 배치"). 진짜 중복은 **이미 시각 요소로 그려진**
#   경우뿐 — <figure>/<table>/<img alt>/<figcaption> 안에 제목이 있을 때만 스킵.
_VISUAL_BLOCK_RE = _re_vis.compile(
    r"<figure\b[^>]*>.*?</figure>|<table\b[^>]*>.*?</table>|"
    r"<figcaption\b[^>]*>.*?</figcaption>|<img\b[^>]*>",
    _re_vis.IGNORECASE | _re_vis.DOTALL,
)


def already_visualized(title: str, html: str) -> bool:
    """제목이 *시각 요소* 안에 이미 등장하는가 (산문 언급은 중복 아님).

    단일 진입점 — 중복 판정 규칙을 다른 곳에 복사하지 말 것.
    """
    if not title or not html:
        return False
    for m in _VISUAL_BLOCK_RE.finditer(html):
        if title in m.group(0):
            return True
    return False


def _next_data_infographic(collected, out_dir: Path, run_id: str, used_titles: set,
                           platform: str = "", html_so_far: str = "") -> str:
    """수집 실데이터에서 *아직 안 쓴* dataset 1개 → 결정론(LLM 0회) 인포그래픽 <p><img></p>.

    ★ 사용자 박제 2026-07-05 (ERRORS [364]): "실데이터(API+텍스트)는 항상 있다 →
    인포그래픽 무조건 생성". collected.datasets = stocks_to_datasets + facts_to_datasets
    (compose_collected 가 출처 박제 조립). used_titles 로 슬롯·top-up 간 중복 방지.
    거짓 차트 금지(규정 12): generate_infographic 내부 _verify_dataset 가 출처 없는 dataset 제거.
    ★ 본문 이미지 = 인포그래픽 디자인만 (사용자 박제 2026-07-06): 인포그래픽을 못 만들면
    폴백 없이 그냥 "" (빈 슬롯). AI 사진·matplotlib 폴백 전부 폐기 — 이미지 없는 게 낫다.
    """
    try:
        from JARVIS06_IMAGE.infographic_engine import generate_infographic
    except ImportError:
        return ""
    theme = (getattr(collected, "meta", None) or {}).get("keyword", "")
    for ds in (getattr(collected, "datasets", None) or []):
        if not ds.get("data"):
            continue
        _title = (ds.get("title") or f"{theme} 핵심 수치").strip()
        if _title in used_titles or already_visualized(_title, html_so_far):
            continue
        used_titles.add(_title)   # 성공·실패 무관 1회만 시도 (중복·무한 방지)
        try:
            # subtitle="" (eyebrow 와 이중 금지) · src 미전달 → 렌더러가 ds.source(provider)에서
            # 출처 파생(헤드라인 source.name 사용 금지 — 사용자 박제 2026-07-19, 동적 설계).
            _path = generate_infographic(
                _title, "", [ds],
                run_id=run_id or theme, slot_key=f"dg{len(used_titles)}", out_dir=out_dir,
                context=f"{theme} — {_title}",
            )
            if _path:
                print(f"  📊 [{platform}] 실데이터 인포그래픽: {_title[:30]}")
                return _infographic_img_html(_path, _title[:40].replace('"', "'"))
        except Exception as e:
            print(f"  ⚠️ [{platform}] 인포그래픽 실패({_title[:20]}): {e}")
    return ""   # 인포그래픽 못 만들면 빈 슬롯 — 폴백 없음


def _extra_infographics(collected, out_dir: Path, count: int,
                        run_id: str = "", platform: str = "",
                        html_so_far: str = "", used_titles: set = None) -> list:
    """★ min-N top-up 을 *실데이터 인포그래픽* 으로 채운다 (사용자 박제 2026-07-05, ERRORS [364]).

    데이터 있으면 인포그래픽으로 채운다. datasets 소진 시 빈 리스트(폴백 없음 — AI 사진 폐기).
    """
    if count <= 0:
        return []
    used = used_titles if used_titles is not None else set()
    out: list = []
    while len(out) < count:
        fig = _next_data_infographic(collected, out_dir, run_id, used, platform, html_so_far)
        if not fig:
            break
        out.append(fig)
    return out


def _insert_extra_photos(content: str, photos: list) -> str:
    """추가 인포그래픽을 이미지 없는 h2/h3 섹션에 배포 + 나머지는 말미."""
    if not photos:
        return content
    h_matches = list(re.finditer(r'<h[23][^>]*>.*?</h[23]>', content, re.IGNORECASE | re.DOTALL))
    if not h_matches:
        return content + "\n" + "\n".join(photos)
    img_free_ends: list[int] = []
    for i, m in enumerate(h_matches):
        end = h_matches[i + 1].start() if i + 1 < len(h_matches) else len(content)
        section = content[m.start():end]
        if "<img" not in section and "<figure" not in section and "<svg" not in section:
            img_free_ends.append(end)
    result = content
    slots_used = min(len(img_free_ends), len(photos))
    for pos, photo in zip(reversed(img_free_ends[:slots_used]), reversed(photos[:slots_used])):
        result = result[:pos] + "\n" + photo + "\n" + result[pos:]
    remaining = photos[slots_used:]
    if remaining:
        result += "\n" + "\n".join(remaining)
    return result


def _local_text_thumbnail(title: str, keyword: str, out_dir: Path):
    """최후 로컬 썸네일 폴백 — 외부 API 없이 타이틀 카드 (누락 0 보장)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import hashlib
        try:
            from JARVIS06_IMAGE.style_engine import setup_chart_defaults
            setup_chart_defaults()
        except Exception:
            pass
        h = hashlib.md5((keyword or title or "x").encode("utf-8")).hexdigest()
        accent = "#" + h[:6]
        fig = plt.figure(figsize=(12.8, 6.72), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, color="#1a2230"))
        ax.add_patch(plt.Rectangle((0, 0), 0.018, 1, color=accent))
        ax.text(0.06, 0.5, (title or keyword or "")[:38], fontsize=34,
                color="white", va="center", ha="left")
        out = Path(out_dir) / f"thumb_local_{h[:8]}.png"
        fig.savefig(str(out), facecolor="#1a2230")
        plt.close(fig)
        return str(out)
    except Exception as e:
        log.warning(f"로컬 썸네일 폴백 실패: {e}")
        return None


def _mandatory_thumbnail(title: str, keyword: str, sector: str, platform: str,
                         out_dir: Path, body_text: str, tag_line: str = ""):
    """★ 썸네일 필수 생성 (사용자 박제 2026-07-05) — 재시도 3회(원래 2회, 사용자 지시로 통일 2026-07-06) + 로컬 폴백 → 누락 0."""
    from JARVIS06_IMAGE.image_agent import generate_thumbnail
    for attempt in range(3):
        try:
            p = generate_thumbnail(title=title, keyword=keyword, sector=sector,
                                   platform=platform, out_dir=out_dir, body_text=body_text,
                                   tag_line=tag_line)
            if p:
                return p
        except Exception as e:
            log.warning(f"썸네일 시도 {attempt+1} 실패: {e}")
            _g_report("image", e, module=__name__)
    return _local_text_thumbnail(title, keyword, out_dir)   # 최후 로컬 카드


def _generate_charts(html: str, theme: str, sector: str, collected,
                     platform: str, out_dir: Path,
                     context_docs: list | None = None,
                     run_id: str = "", used_titles: set = None) -> str:
    """[CHART_N]...[/CHART_N] 데이터 내장 슬롯 → infographic_engine 고퀄리티 인포그래픽.

    구형식 [CHART_N: 설명] 잔존 슬롯 = 데이터 없음 → 실데이터 인포그래픽 (없으면 빈 슬롯).
    본문 이미지는 인포그래픽 디자인만 (AI 사진 폐기 2026-07-06). 검증 ref = CollectedData.
    """
    # ── 0단계: ★ 차트 슬롯 렌더 — 실데이터 직접 주입 (LLM 수치 완전 무시) ──
    #   신형식 [CHART_N]...[/CHART_N] → 슬롯 제목으로 collected.datasets 매칭 → 실데이터 렌더
    #   ★ 사용자 박제 2026-07-11: LLM이 슬롯에 뭘 써도 무시. 항상 collected.datasets 사용.
    try:
        from JARVIS06_IMAGE.slot_renderer import render_slots_from_collected
        import os as _os_dp
        # ★ 카탈로그와 동일 슬라이스 전달 — D1이 index 0, D2가 index 1 ... 정확히 일치
        _cat_max = int(_os_dp.getenv("DATA_CATALOG_MAX", "16") or "16")
        _catalog_datasets = list(collected.datasets or [])[:_cat_max]
        _run_id = uuid.uuid4().hex[:8]
        html, _s_ok, _s_total = render_slots_from_collected(
            html, _catalog_datasets, out_dir, run_id=_run_id, theme=theme)
        if _s_total:
            print(f"  🎨 [{platform}] 차트 슬롯 {_s_ok}/{_s_total}개 실데이터 렌더 (LLM 수치 무시)")
    except Exception as _sre:
        print(f"  ⚠️ [{platform}] 슬롯 처리 스킵: {_sre}")

    # ── 1단계: 구형식 [CHART_N: text] 잔존 슬롯 → 실데이터 인포그래픽 (없으면 빈 슬롯) ──
    # 구형식 = 구조화 데이터 없음. 거짓 차트 금지(규정 12) + 본문 AI 사진 폐기(2026-07-06).
    placeholders = re.findall(r"\[CHART_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html

    # ★ 인포그래픽 디자인만 (사용자 박제 2026-07-06): "차트 자리"로 판단된 슬롯을 수집
    #   실데이터 인포그래픽으로 채우고, 못 만들면 그냥 비운다. AI 사진·폴백 없음.
    print(f"  ⚠️ [{platform}] 구형식 슬롯 {len(placeholders)}개 → 실데이터 인포그래픽 (없으면 빈 슬롯)")
    _desc_by_pos = {i + 1: desc.strip() for i, (_, desc) in enumerate(placeholders)}
    _used = used_titles if used_titles is not None else set()
    svg_map: dict[int, str] = {}

    for pos, desc in _desc_by_pos.items():
        svg_map[pos] = _next_data_infographic(collected, out_dir, run_id, _used, platform, html)

    _pos = [0]

    def _replace(m: re.Match) -> str:
        _pos[0] += 1
        return svg_map.get(_pos[0], "")   # 인포그래픽 실패 시 빈 슬롯

    result = re.sub(r"\[CHART_(\d+):[^\]]+\]", _replace, html)
    ok = sum(1 for v in svg_map.values() if v)
    print(f"  ✅ [{platform}] 구형식 슬롯 {ok}/{len(placeholders)}개 인포그래픽 치환 (나머지 빈 슬롯)")
    return result


# ── [PHOTO_N] 슬롯 처리 — 인포그래픽 디자인만 (본문 AI 사진 폐기 2026-07-06) ──

def _render_photo_slots(html: str, collected, out_dir: Path, run_id: str,
                        used_titles: set, platform: str = "") -> str:
    """[PHOTO_N: 설명] 슬롯 → 실데이터 인포그래픽(있으면) 아니면 슬롯 제거.

    ★ 사용자 박제 2026-07-06: 본문 이미지는 인포그래픽 디자인만. 장식용 AI 사진 전면 폐기.
      [PHOTO_N] 도 [CHART_N] 과 동일하게 인포그래픽으로 채우거나, 못 만들면 그냥 비운다.
    """
    placeholders = re.findall(r"\[PHOTO_(\d+):\s*([^\]]+)\]", html)
    if not placeholders:
        return html
    info_map: dict[int, str] = {}
    for i, _ in enumerate(placeholders, 1):
        info_map[i] = _next_data_infographic(collected, out_dir, run_id, used_titles, platform, html)
    ok = sum(1 for v in info_map.values() if v)
    print(f"  📊 [{platform}] PHOTO 슬롯 {ok}/{len(placeholders)}개 인포그래픽 치환 (나머지 빈 슬롯)")
    _pos = [0]

    def _replace(m: re.Match) -> str:
        _pos[0] += 1
        return info_map.get(_pos[0], "")

    return re.sub(r"\[PHOTO_(\d+):[^\]]+\]", _replace, html)


# ── 섹션 AI 사진 (h2 소제목 교체) — ★ 폐기 (사용자 박제 2026-05-15 ↔ 2026-06-07 정합) ──
#
# 옛 _inject_section_images 는 <h2> 를 <figure><img></figure> 로 *교체* 하여
# 소제목 텍스트가 사라지게 만들었다. 사용자 박제 2026-05-15 (trend_economic_writer.py:562)
# 에서 *옛 h2→이미지 교체 동작 폐기* 가 명시됐고, 2026-06-07 사용자 결정으로
# 테마글에도 동일 적용. h2 는 텍스트 소제목으로 유지하고, 본문 사이 이미지는
# [PHOTO_N] / [CHART_N] 플레이스홀더 단일 경로로만 진입.
#
# 따라서 _inject_section_images 와 _select_section_facts 모두 삭제. process_draft
# 흐름에서도 호출 제거 (① 차트 / ② 사진 / 썸네일 / 캡처 / 조립).


def _inject_leader_price_charts(html: str, collected, out_dir=None) -> str:
    """[PRICE_CHART_LEADER]...[/PRICE_CHART_LEADER] 슬롯 → 주가 차트 교체.

    주 경로: collected.datasets 의 viz_hint="stock_price" 데이터로 차트 생성
    폴백   : 데이터 없으면 collected.entities ticker 로 yfinance 직접 조회
    실데이터 없으면 슬롯 제거 (ADR 010 — 거짓 차트 금지).

    out_dir 제공 시 PNG 파일로 저장 → <figure><img src="..."/> 반환 (발행자 업로드 가능).
    out_dir 없으면 base64 data URI (backward compat).

    rank는 1-indexed (collect_theme.py enumerate(stocks, 1)):
      rank=1 → 대장주 (label_key="leader")
      rank=2 → 부대장주 (label_key="second")
    """
    try:
        from JARVIS06_IMAGE.theme_charts import (
            make_leader_price_chart_from_data,
            make_leader_price_chart,
        )
    except ImportError:
        for key in ("LEADER", "SECOND"):
            html = re.sub(rf'\[PRICE_CHART_{key}\].*?\[/PRICE_CHART_{key}\]',
                          '', html, flags=re.DOTALL)
        return html

    _SLOTS = [("LEADER", "leader", 1), ("SECOND", "second", 2)]

    # datasets 에서 viz_hint="stock_price" 항목 인덱스
    datasets = list(getattr(collected, "datasets", None) or [])
    _price_ds = {
        d.get("label_key"): d
        for d in datasets
        if d.get("viz_hint") == "stock_price" and d.get("label_key")
    }

    # entities 에서 rank=1,2 인덱스 (폴백용 ticker)
    entities = list(getattr(collected, "entities", None) or [])
    _by_rank = {e.get("rank"): e for e in entities
                if isinstance(e.get("rank"), int) and e.get("rank") in (1, 2)}

    for slot_key, label_key, rank in _SLOTS:
        slot_pat = re.compile(
            rf'\[PRICE_CHART_{slot_key}\](.*?)\[/PRICE_CHART_{slot_key}\]',
            re.DOTALL,
        )
        if not slot_pat.search(html):
            continue

        chart_html = ""

        # 주 경로: collected.datasets 의 분기별 데이터
        ds = _price_ds.get(label_key)
        if ds and ds.get("data"):
            _out_path = None
            if out_dir:
                import hashlib as _hlib
                _h = _hlib.md5(f"{label_key}{ds.get('name','')}".encode()).hexdigest()[:8]
                _out_path = Path(out_dir) / f"price_{label_key}_{_h}.png"
            chart_html = make_leader_price_chart_from_data(
                rows=ds["data"],
                name=ds.get("name", label_key),
                period=ds.get("period", ""),
                out_path=_out_path,
            )

        # 폴백: entities ticker 로 실시간 조회 (ticker 없으면 code 사용 — ERRORS [402] 연관)
        if not chart_html:
            stock = _by_rank.get(rank)
            _yf_ticker = stock.get("ticker") or stock.get("code") or "" if stock else ""
            if stock and _yf_ticker and stock.get("name"):
                _out_path = None
                if out_dir:
                    import hashlib as _hlib
                    _h = _hlib.md5(f"{label_key}{_yf_ticker}".encode()).hexdigest()[:8]
                    _out_path = Path(out_dir) / f"price_{label_key}_{_h}.png"
                chart_html = make_leader_price_chart(
                    yf_ticker=_yf_ticker, name=stock["name"], out_path=_out_path
                )

        html = slot_pat.sub(chart_html, html)  # 차트 없으면 chart_html="" → 슬롯 제거

    return html


# ── 공개 API ──────────────────────────────────────────────────────

def process_draft(draft_html: str, collected, platform: str = "tistory",
                  out_dir: Path = None) -> dict:
    """★ v2 (Step 6, 2026-07-05) — 대본 HTML + CollectedData → 완성 블록.

    전 카테고리 공통 이미지 오케스트레이터. keyword/sector/category·검증 정답·
    차트 seed·이미지 컨텍스트를 *모두* CollectedData 단일 상자에서 파생.
    카테고리별 노브(min_images·thumbnail_body_chars)는
    CATEGORY_POLICY[category] 레지스트리에서 조회.

    Args:
        draft_html:  Pass-1 대본 (플레이스홀더 [CHART_N]/[PHOTO_N] 포함)
        collected:   JARVIS09 CollectedData (meta+datasets+docs+facts+entities) — 필수
        platform:    "tistory" | "naver"
        out_dir:     이미지 저장 폴더

    Returns:
        {"blocks": list[tuple], "thumbnail_path": str|None, "title": str, "html": str, "html_path": str}
    """
    # ★ 파이프라인 활동 표시 — 조립 시작 시 바쁨 마킹, 종료 시(성공·실패 무관) 해제.
    #   lazy import + no-op 폴백 — clear_busy 는 다른 작업자가 동시 신설 중이라
    #   import 실패에도 안전해야 함 (표시 실패가 조립을 막으면 안 됨).
    try:
        from shared.pipeline_activity import mark_busy
        mark_busy("j06", "인포그래픽·이미지 조립", ttl=900)
    except Exception:
        pass
    try:
        return _process_draft_impl(draft_html, collected, platform, out_dir)
    finally:
        try:
            from shared.pipeline_activity import clear_busy
            clear_busy("j06")
        except Exception:
            pass


def _process_draft_impl(draft_html: str, collected, platform: str = "tistory",
                        out_dir: Path = None) -> dict:
    """process_draft 실제 본체 — 바쁨 마킹 try/finally 래퍼(process_draft)에서만 호출."""
    try:
        from shared.pipeline_activity import mark_active
        mark_active("e3")  # J02→J06 대본 전달 활성화
    except Exception:
        pass
    from JARVIS09_COLLECTOR.models import policy_for, dataset_is_stock_financial
    if collected is None or not hasattr(collected, "all_numbers"):
        raise TypeError("process_draft: collected(CollectedData) 필수 — "
                        "호출자는 compose_collected/cand_collected 로 조립해 전달")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta = collected.meta or {}
    theme = meta.get("keyword", "")
    sector = meta.get("sector", "")
    category = meta.get("category", "theme")
    policy = policy_for(category)
    min_images = int(policy.get("min_images", 5))
    thumb_chars = int(policy.get("thumbnail_body_chars", 3000))

    # ★ 경제 브리핑은 개별 종목 재무 차트(PER/ROE/영업이익률/현재가) 배제 (사용자 박제 2026-07-18).
    #   방어심층 — 수집 게이트(collect_chart_data category)가 1차 차단하지만, 어떤 경로로
    #   섞여 들어와도 이미지 렌더 전 제거. 테마(allow_stock_financial=True)는 필터 미발동.
    if not policy.get("allow_stock_financial", True):
        import dataclasses as _dc
        _all_ds = list(collected.datasets or [])
        _kept = [d for d in _all_ds if not dataset_is_stock_financial(d)]
        if len(_kept) != len(_all_ds):
            print(f"  🚫 [{platform}] 경제 브리핑 — 종목 재무 차트 {len(_all_ds) - len(_kept)}개 제외 (거시·개념 인포그래픽만)")
            collected = _dc.replace(collected, datasets=_kept)

    # 이미지 컨텍스트 = 근거 fact(어댑터) + 수집 문서 (fact-grounding 보존).
    #   facts_for_chart/facts_for_photo 가 그대로 소비 → 차트 수치·사진 장면 접지.
    context_docs = list(collected.docs or [])
    try:
        from JARVIS09_COLLECTOR.evidence_pack import as_source_docs
        _fact_docs = as_source_docs(collected.facts)
        if _fact_docs:
            context_docs = _fact_docs + context_docs
            print(f"  🧾 [{platform}] 근거 fact {len(_fact_docs)}개 → 이미지 컨텍스트 합류")
    except Exception as e:
        log.warning(f"근거 fact 합류 실패(무시): {e}")

    # ① [CHART_N] → 인포그래픽 디자인 (못 만들면 빈 슬롯 — AI 사진 폐기 2026-07-06)
    #   used_titles: 슬롯·top-up 이 *같은 dataset 을 중복* 시각화하지 않도록 공유 (ERRORS [364])
    _run_id = str(meta.get("run_id") or theme)
    _used_titles: set = set()
    html = _generate_charts(draft_html, theme, sector, collected, platform, out_dir,
                            context_docs=context_docs,
                            run_id=_run_id, used_titles=_used_titles)

    # ② [PHOTO_N] → 인포그래픽 디자인(데이터 있으면) 아니면 슬롯 제거. ★ 본문 AI 사진 폐기.
    html = _render_photo_slots(html, collected, out_dir, _run_id, _used_titles, platform)

    # ②-B 대장주·부대장주 주가 차트 (카테고리=theme 시, rank=1/2 종목, ADR 010)
    #   주 경로: collected.datasets 의 viz_hint="stock_price" 분기별 데이터
    #   폴백:   collected.entities 의 ticker 로 yfinance 실시간 조회
    if category == "theme":
        html = _inject_leader_price_charts(html, collected, out_dir=out_dir)

    # ★ min-N top-up — 실데이터 인포그래픽만 (사용자 박제 2026-07-06: 본문 이미지 = 인포그래픽
    #   디자인만, 폴백 없음). datasets 로 결정론 인포그래픽을 채우고, *소진되면 그대로 둔다*
    #   (부족해도 AI 사진·matplotlib 폴백 없음 — 이미지 없는 게 낫다).
    n_img = _count_images(html)
    if n_img < min_images:
        need = min_images - n_img
        log.warning(f"[{platform}] 본문 이미지 {n_img} < 최소 {min_images} → 인포그래픽 {need}개 보충 시도 "
                    f"(datasets={len(getattr(collected, 'datasets', None) or [])}개)")
        infos = _extra_infographics(collected, out_dir, need, run_id=_run_id,
                                    platform=platform, html_so_far=html,
                                    used_titles=_used_titles)
        if infos:
            html = _insert_extra_photos(html, infos)
            n_img = _count_images(html)
            print(f"  📊 [{platform}] 인포그래픽 {len(infos)}개 보충 → 본문 이미지 {n_img}/{min_images}")
        if n_img < min_images:
            log.warning(f"[{platform}] 데이터 소진 — 이미지 {n_img}/{min_images} (빈 슬롯, 폴백 없음). "
                        f"datasets={len(getattr(collected, 'datasets', None) or [])}개 — 0이면 수집 실패, "
                        f"0이 아닌데 이미지가 없으면 중복 판정 과잉 의심")

    # ③ (옛 h2→이미지 교체 폐기) — 본문 이미지는 [CHART_N]/[PHOTO_N] 단일 경로만.

    # ④ 제목
    from JARVIS02_WRITER.tistory_html_writer import extract_title, save_article_html, screenshot_article
    title = extract_title(html, theme)

    # ⑤ HTML 저장
    html_path, _ = save_article_html(html, theme, platform=platform)

    # ⑥ SVG 캡처 → JPG — 인라인 <svg> 있을 때만 (슬롯 렌더 경로는 차트를 이미
    #    JPG 파일 + <p><img> 로 본문에 내장 → inline SVG 0개가 *정상* — 오경보 방지)
    if re.search(r"<svg\b", html, re.IGNORECASE):
        print(f"  📸 [{platform}] SVG 캡처...")
        visual_paths = screenshot_article(html_path, str(out_dir))
        if not visual_paths:
            print(f"  ⚠️ [{platform}] 스크린샷 0개 — 텍스트 전용 진행")
    else:
        visual_paths = []
        _n_body_imgs = len(re.findall(r"<img\b", html, re.IGNORECASE))
        if _n_body_imgs:
            print(f"  ℹ️ [{platform}] 인라인 SVG 없음 — 슬롯 렌더 이미지 {_n_body_imgs}개 사용, 캡처 생략")
        else:
            print(f"  ⚠️ [{platform}] 스크린샷 0개 — 텍스트 전용 진행")

    # ⑦ 썸네일 필수 (body 3000, 재시도 + 로컬 폴백 → 누락 0)
    body_text = re.sub(r"<[^>]+>", "", html)[:thumb_chars]
    # 하단 카테고리 라벨 — 카테고리별 고정 라벨, 미매칭 시 섹터/키워드
    _tag_line = _TAG_BY_CATEGORY.get((category or "").strip().lower()) or (sector or theme)
    thumbnail_path = _mandatory_thumbnail(title, theme, sector, platform, out_dir,
                                          body_text, tag_line=_tag_line)
    if thumbnail_path:
        print(f"  🖼️ [{platform}] 썸네일 생성 완료")
    else:
        print(f"  ⛔ [{platform}] 썸네일 최종 실패 (로컬 폴백까지 실패)")

    # ⑧ assemble_blocks → 완성 블록
    from JARVIS06_IMAGE.injectors import assemble_blocks
    blocks = assemble_blocks(html, visual_paths, out_dir=out_dir)

    # ⑨ 썸네일 맨 앞 prepend — J06 책임으로 통합 (사용자 박제 2026-07-11)
    if thumbnail_path and Path(thumbnail_path).exists():
        blocks = [("image", str(thumbnail_path))] + blocks

    # ⑩ 블록 법률 집행 — 이미지 연속 방지 + 헌법 검증 (J06 책임으로 통합)
    try:
        from JARVIS02_WRITER.jarvis_main import enforce_text_between_images as _etbi
        blocks = _etbi(blocks, source=f"J06-{platform.upper()}")
    except Exception as _ee:
        log.warning(f"[{platform}] enforce_text_between_images 오류(무시): {_ee}")
    try:
        # 신 파이프라인은 이미지가 이미 blocks 에 내장 — image_pool 미전달이 정상 (본문 이미지=인포그래픽만, 폴백 없음 규정)
        from JARVIS02_WRITER.law_enforcer import (
            enforce_supreme_law as _esl, notify_violations as _nviol
        )
        blocks, _viols = _esl(blocks, platform, f"J06-{platform}")
        _nviol(_viols, platform, f"J06-{platform}")
    except Exception as _ee:
        log.warning(f"[{platform}] enforce_supreme_law 오류(무시): {_ee}")

    print(f"  ✅ [{platform}] process_draft 완료 — 블록 {len(blocks)}개")
    return {
        "blocks":         blocks,
        "thumbnail_path": thumbnail_path,
        "title":          title,
        "html":           html,
        "html_path":      str(html_path),   # ★ Step 9: 경제 반환 계약 호환 (재저장 금지)
    }


def publish_assembled(result: dict, publish_fn, platform: str = "") -> dict:
    """★ J06 발행 진입점 — process_draft() 완성 블록을 J08 에 직접 넘긴다 (사용자 박제 2026-07-11).

    J06 이 이미지·썸네일·법률집행까지 끝낸 result 를 받아 publish_fn(J08) 을 직접 호출.
    J02 가 J08 을 직접 호출하던 역방향 흐름 제거 — J06 → J08 단방향.

    Args:
        result:     process_draft() 반환값 {"blocks", "title", "html", ...}
        publish_fn: J08 발행 함수 — (blocks, title, **kw) → dict{"success": bool, ...}
        platform:   로깅 용도 ("naver" | "tistory")
    Returns:
        publish_fn 반환값 (최소 "success" 키 필수)
    """
    blocks = list(result.get("blocks") or [])
    title  = result.get("title", "")
    try:
        from shared.pipeline_activity import mark_active
        mark_active("e6")  # J06→J08 발행 활성화
    except Exception:
        pass
    print(f"  📤 [J06→J08] {platform} 발행 위임 — 블록 {len(blocks)}개")
    try:
        return publish_fn(blocks=blocks, title=title)
    except Exception as _pe:
        log.error(f"[J06→J08] {platform} 발행 실패: {_pe}")
        _g_report("image", _pe, module=__name__, func_name="publish_assembled")
        return None
