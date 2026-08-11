"""JARVIS06_IMAGE/infographic_engine.py — 인포그래픽 *생성 진입점* (조립은 하지 않는다).

★ 사용자 박제 2026-08-10 (①단일 진입점) — **두 번째 조립 계층 폐기**:
  이 파일에는 2026-08-10 이전까지 차트·조립 구현이 *한 벌 더* 있었다
  (`render_spec`/`_render_panel`/`_render_single`/`pie_chart`/`vbar_chart`/`hbar_chart`/
   `kpi_card`/`stat_block`/`area_chart`/`donut_chart`/`_spark`/`_kpi_value`/인사이트 문장).
  이름만 달랐을 뿐 `pro_templates`/`template_engine` 의 `_bar_chart`·`_donut`·`_hero_stat`·
  `_kpi_cards` 와 *같은 일을 하는 사본* 이었고, 사본이라 한쪽만 고쳐졌다 —
  2026-07-06 에 '무의미한 합계 폐기' 를 조립부에만 걸었는데 이 사본은 그대로 남아
  `_render_single(금리 8행)` 이 **'합계 27.2%'·'항목 수 8'·'(총 8개 항목)'** 을 계속 인쇄했다.
  (같은 병의 1차 발현이 pro_templates 안의 조립 사본이었다 — 그건 이미 제거됐다.)

  ⛔ 여기에 차트를 그리는 코드를 다시 만들지 말 것. 차트 프리미티브의 주인은
     `pro_templates`, 조립(히어로·차트블록·미니카드)의 주인은 `template_engine.render_layout`,
     표시 수치 사실성 판정의 주인은 `validators/image_data_verifier` 단독이다.

이 파일에 남는 책임은 넷뿐:
  1. **승인**   — `_verify_dataset` (판정 본체는 image_data_verifier.dataset_admissible)
  2. **정규화** — `_normalize_ds` (시간축 좌→우·동일행 제거. 본체는 image_spec)
  3. **후보 사다리** — 어떤 렌더러에 · 어떤 폭의 데이터로 맡길지 (`_render_candidates`)
  4. **초크포인트** — `_emit`: 픽셀을 낳은 *모든* 경로가 지나는 유일한 반환 지점.
     `certify_image` 로 표시 수치 grounding 검증 + provenance 등록. 미검증이면 이미지를 버린다
     (ADR 010: 거짓 차트 < 차트 없음).

킬스위치 (무배포 · 라이브 안전장치 — prepublish_gate 관례와 같은 꼴):
  IMAGE_DATA_GATE=0        미검증 이미지를 *폐기하지 않는다* (검증·기록은 계속 돈다)
  IMAGE_VERIFY_TELEMETRY=0 events 적재만 중지 (판정·폐기는 그대로)
  ※ 이름의 주인은 `validators/image_data_verifier.GATE_ENV` / `TELEMETRY_ENV` —
    여기 문자열로 다시 적지 말 것.

데이터 입력 = JARVIS09 collect_chart_data 의 datasets:
  [{"title","viz_hint","unit","data":[{"label","value"}],"source":{...}}, ...]

공개 API:
  generate_infographic(...) -> str(path) | ""      수치 인포그래픽 1장
  render_table_infographic(...) -> str(path) | ""  본문 표 → 표 이미지(수치 변형 0)
  data_image_html(path, alt) -> '<p><img ...>'     수치 이미지 표식 단일 생산자
"""
from __future__ import annotations
import hashlib
import os
import re
import logging
from pathlib import Path

log = logging.getLogger("jarvis")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k): pass

FONT = "Noto Sans KR,sans-serif"

# 재시도·폴백 단계 상한 — 정의는 JARVIS06_IMAGE/limits.py 단독(harness SSOT 파생)
from JARVIS06_IMAGE.limits import max_attempts as _max_attempts   # noqa: E402
# 수치 표시 포맷은 pro_templates._fmt 단독 (①단일 진입점 — 같은 값이 경로마다 78.2/78.20 으로
# 갈리던 사고). 여기서 재구현하지 말 것.
from JARVIS06_IMAGE.pro_templates import _fmt   # noqa: E402


# ── 유틸 ──────────────────────────────────────────────────────────────
def _seed_int(*parts) -> int:
    h = hashlib.md5("|".join(str(p) for p in parts).encode("utf-8", "replace")).hexdigest()
    return int(h[:8], 16)


def _normalize_ds(ds):
    """시간축 좌→우 + 동일 항목·값 중복 제거. 본체는 image_spec(owner) — 여기는 호출만."""
    try:
        from JARVIS06_IMAGE.image_spec import enforce_time_axis_ltr as _ltr, dedupe_chart_rows as _ddr
        _fixed = _ltr(ds.get("data") or [])
        if _fixed is not ds.get("data"):
            log.info("[infg] 시간축 교정 — 과거→최근 (좌→우)")
            ds["data"] = _fixed
        _dd = _ddr(ds.get("data") or [])
        if len(_dd) != len(ds.get("data") or []):
            ds["data"] = _dd
    except Exception:
        pass
    return ds


def _verify_dataset(ds, category: str = "") -> bool:
    """차트 승격 가능 여부 — 판정 본체는 image_data_verifier.dataset_admissible."""
    from JARVIS06_IMAGE.validators.image_data_verifier import dataset_admissible
    ok, why = dataset_admissible(ds, category=category)
    if not ok:
        log.info(f"[infg] dataset 승격 거부({why}): {str(ds.get('title', ''))[:40]}")
    return ok


def _rendered_rows(datasets):
    """이 datasets 로 조립부가 *실제로 그린* 행 — 조립부(owner)에게 묻는다.

    검증의 대조군이 되므로 추측해서 만들지 않는다. 종전 사고('항목 수 8 vs 막대 7')는
    정확히 '그린 행' 과 '가진 행' 을 다른 곳에서 각자 센 데서 났다.
    """
    try:
        from JARVIS06_IMAGE.template_engine import rendered_view
        return rendered_view(datasets)
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════
# ★ 디자인-생성(design-generation) — LLM 이 전문가급 HTML/CSS/SVG 직접 저작
#   (사용자 박제 2026-07-05 — ERRORS [357]). 기본 경로는 pro_templates(결정론 템플릿).
#   ★ LLM 실시간 저작은 SDK 스로틀 시 이미지당 수 분 latency → 기본 OFF (opt-in). ERRORS [358].
#   ★ 수치 검증을 여기서 하지 않는다 — 게이트는 `_emit` 하나뿐이다(게이트가 두 곳이면
#     한쪽만 강화되고 다른 쪽이 통과 경로가 된다).
# ══════════════════════════════════════════════════════════════════════════
_DESIGNGEN_ON = os.getenv("INFOGRAPHIC_DESIGNGEN", "0") == "1"

_DG_ART = [
    "프리미엄 금융 매거진 에디토리얼 — 딥네이비 히어로 밴드 + 골드/민트 듀오톤, 좌측 초대형 히어로 스탯. 고급·신뢰.",
    "밝은 K-블로그 프리미엄 — 크림/화이트 배경 + 코랄·틸 그라디언트, 큼직한 라운드 카드와 곡선 모티프. 친근하지만 정교.",
    "모던 데이터 저널리즘 — 화이트 배경 + 단일 딥컬러 강조, 굵은 타이포 위계와 얇은 헤어라인, 절제된 미니멀.",
    "다크 대시보드 프리미엄 — 차콜/딥블루 배경 + 네온 액센트 1색, 글래스 카드와 발광 포인트, 미래적.",
    "웜 파스텔 인포그래픽 — 아이보리/피치 배경 + 딥틸·머스타드, 둥근 기하 모티프와 친근한 인라인 아이콘.",
]

_DG_FEWSHOT = """<!-- 참고 구조 예시 (품질·구성 수준의 하한선. 그대로 베끼지 말고 이 수준 이상으로) -->
<div style="width:1280px;background:#eef2f8;font-family:'Noto Sans KR',sans-serif">
  <div style="padding:52px 64px;background:linear-gradient(135deg,#0a1730,#16345f);position:relative;overflow:hidden">
    <div style="display:inline-flex;gap:9px;padding:8px 16px;border:1px solid rgba(245,184,41,.4);border-radius:999px;color:#ffd466;font-size:15px;font-weight:700">● 리포트 라벨</div>
    <h1 style="margin:20px 0 10px;color:#fff;font-size:56px;font-weight:900;letter-spacing:-.02em">임팩트 있는 제목</h1>
    <div style="color:#a9bad6;font-size:19px">부제 · 기간</div>
    <div style="display:flex;gap:26px;margin-top:38px">
      <div style="flex:1;padding:26px 28px;border-radius:20px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09)">
        <div style="color:#cdd8ec;font-weight:700">항목 A</div>
        <div style="font-size:76px;font-weight:900;color:#ffce54">＋10.2<span style="font-size:40px">%</span></div>
        <div style="color:#9fb0cc">실제값 맥락</div>
      </div>
      <div style="flex:1;padding:26px 28px;border-radius:20px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09)">
        <div style="color:#cdd8ec;font-weight:700">항목 B</div>
        <div style="font-size:76px;font-weight:900;color:#37d6cf">＋7.1<span style="font-size:40px">%</span></div>
      </div>
    </div>
  </div>
  <div style="padding:40px 64px">
    <div style="background:#fff;border-radius:24px;padding:34px 38px;box-shadow:0 18px 50px rgba(18,42,83,.10)">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:14px;align-items:center"><div style="width:34px;height:34px;border-radius:10px;background:#0f1b33;color:#fff;font-weight:800;display:flex;align-items:center;justify-content:center">01</div><h2 style="font-size:24px;font-weight:800;color:#0f1b33">차트 제목</h2></div>
        <div style="font-size:15px;color:#37476a;font-weight:700">범례 · 축 설명</div>
      </div>
      <svg width="100%" viewBox="0 0 960 330"><!-- 인라인 SVG 차트: 그라디언트 area + 라인 + 축라벨 + 끝점 강조 + 주석 --></svg>
    </div>
    <div style="display:flex;gap:20px;margin-top:22px">
      <div style="flex:1;background:#fff;border-radius:18px;padding:22px 24px;border:1px solid #e7ecf5"><div style="color:#64748b;font-size:15px">라벨</div><div style="font-size:30px;font-weight:900;color:#0f1b33">값</div></div>
    </div>
  </div>
  <div style="padding:20px 64px 30px;display:flex;justify-content:space-between;color:#8b98af;font-size:14px"><span>데이터 출처 · ...</span><span style="font-weight:800;color:#0f1b33">JARVIS · 데이터 인사이트</span></div>
</div>"""

_DG_RUBRIC = """너는 세계 최정상급 편집 인포그래픽 아트디렉터다 (Bloomberg Graphics / 뉴욕타임스 그래픽 / Information is Beautiful 수준).
아래 *실데이터* 로 전문 디자이너가 만든 프리미엄 인포그래픽 1장을 완결 HTML 로 저작한다.

[품질 기준 — 전부 충족]
1. 컨셉: 데이터를 카드에 나열만 하지 말 것. 하나의 시각적 스토리(히어로 스탯→근거→맥락).
2. 타이포 위계: 디스플레이급 초대형 숫자(80px+)·굵기 대비·아이브로우 라벨. 숫자가 디자인 요소.
3. 색 시스템: 단색 flat 금지. 주색1+강조1~2+그라디언트/듀오톤. 여러 시리즈는 서로 다른 색. 배경도 미묘한 그라디언트.
4. 구도: 비대칭 균형·명확한 포컬포인트·의도적 여백. 죽은 여백 금지.
5. 데이터-잉크: 차트에 직접 라벨·시작/끝점 강조·핵심 주석(annotation)·비교 프레이밍. 범례 의존 최소.
6. 장식: 주제 연관 인라인 SVG 아이콘·기하 모티프·번호칩·구분선 등 일관 장식 언어. 과하지 않게.
7. 깊이: 레이어링·부드러운 그림자·카드 elevation·유리질감 절제.
8. 편집 완성도: 출처 푸터·일관 spacing·정렬 규율.

[아트디렉션] __ART__

[데이터 정확성 — 절대]
- 아래 데이터의 수치만 사용. 어떤 숫자도 새로 지어내지 말 것. (증감률·합계·평균·최대/최소는 이 데이터로 산출 가능한 것만.)
- 차트 선/막대/도넛의 길이·각도·좌표는 실제 값에 비례. 시간축은 과거→최근 좌→우.

[기술 규격]
- 출력: 완결 HTML 하나만. 설명·마크다운·코드펜스 금지. <!DOCTYPE html> 로 시작 </html> 로 끝.
- 루트 컨테이너 width 정확히 1280px, 배경 흰색. 높이는 내용에 맞게.
- 폰트: @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700;800;900&display=swap'); font-family:'Noto Sans KR'. 무게 900 사용 가능.
- 차트는 인라인 SVG 로 직접 그려라 (외부 라이브러리·이미지·JS 금지). CSS 는 인라인/<style> 만. 전부 self-contained (아이콘=인라인 SVG path).

[제목] __TITLE__
[부제] __SUB__
[글 맥락] __CTX__

[데이터 — 아래 수치만 사용]
__DATA__

__FEWSHOT__

이제 위 기준을 전부 충족하는 완결 HTML 을 저작하라. HTML 만 출력."""


def _dg_data_block(datasets) -> str:
    """LLM 저작 프롬프트에 넣을 데이터 블록.

    ★ '유형' 은 `chart_fit()` 이 정한다 (①단일 진입점): 종전엔 여기서 쓰던 `_infer_kind` 가
      '이 데이터가 무슨 꼴인가' 를 두 번째로 판정하는 사본이었다.
    """
    from JARVIS06_IMAGE.validators.image_data_verifier import chart_fit
    lines = []
    for i, ds in enumerate(datasets):
        unit = ds.get("unit", "")
        title = ds.get("title", "") or str(i + 1)
        pairs = " | ".join(f"{r.get('label')}={_fmt(r.get('value'))}"
                           for r in (ds.get("data") or []) if r.get("label") is not None)
        lines.append(f'· "{title}" (단위:{unit or "—"}, 유형:{chart_fit(ds)}): {pairs}')
    return "\n".join(lines)


def _designgen(title, subtitle, datasets, out_path, context, seed) -> tuple[str, str]:
    """LLM design-generation → Chromium 렌더. 반환 (경로, HTML). 실패 시 ("","")."""
    if not _DESIGNGEN_ON or not datasets:
        return "", ""
    try:
        from shared.llm import invoke_text
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
    except Exception:
        return "", ""
    art = _DG_ART[seed % len(_DG_ART)]
    prompt = (_DG_RUBRIC.replace("__ART__", art)
              .replace("__TITLE__", str(title))
              .replace("__SUB__", str(subtitle))
              .replace("__CTX__", str(context)[:600])
              .replace("__DATA__", _dg_data_block(datasets))
              .replace("__FEWSHOT__", _DG_FEWSHOT))
    # ★ 하드 예산(fast-fail) — SDK 스로틀 시 재시도 지옥 대신 즉시 다음 후보로.
    #   짧은 timeout + _retries = harness SSOT 파생(사용자 박제 2026-07-21: 2회).
    try:
        raw = invoke_text("writer_long_infographic", prompt, max_tokens=7000, timeout=110,
                          _retries=_max_attempts())
        if not raw:
            log.info("[designgen] LLM 저작 미수신(스로틀/타임아웃)")
            return "", ""
        m = (re.search(r"(<!DOCTYPE html>.*?</html>)", raw, re.S | re.I)
             or re.search(r"(<html.*?</html>)", raw, re.S | re.I))
        if not m:
            log.info("[designgen] HTML 추출 실패")
            return "", ""
        html = m.group(1)
        ok = _html_to_jpg(html, Path(out_path), width=1280)
        _p = Path(out_path)
        if ok and _p.exists() and _p.stat().st_size > 3000:
            log.info(f"[designgen] 인포그래픽 저작 완료 (art={seed % len(_DG_ART)})")
            return str(out_path), html
    except Exception as e:
        log.warning(f"[designgen] 실패: {e}")
        _g_report("image", e, module=__name__, func_name="_designgen")
    return "", ""


def data_image_html(path, alt: str = "") -> str:
    """수치 이미지 경로 → 본문 삽입용 <p><img> 블록. ★ 표식 속성 단일 생산자.

    ★ 왜 여기 한 곳인가 (사용자 박제 2026-08-10): 같은 <img> 빌더가
      `draft_processor._infographic_img_html` 과 `slot_renderer._path_to_img_html` 에
      *글자 그대로 같은 사본* 으로 두 벌 있었다. 표식(속성)을 한쪽에만 붙이면
      prepublish_gate 가 다른 쪽 이미지를 '수치 차트인지' 판별할 수 없다.
    속성명은 image_data_verifier.DATA_IMAGE_ATTR 단일 소스 — 파일명·경로 리터럴로
    '이건 차트다' 를 판별하지 않기 위한 것이다.
    """
    from JARVIS06_IMAGE.validators.image_data_verifier import DATA_IMAGE_ATTR
    _alt = str(alt or "").replace('"', "'")
    return (f'<p><img src="{path}" alt="{_alt}" {DATA_IMAGE_ATTR}="1" '
            f'style="width:100%;max-width:760px;border-radius:8px;'
            f'margin:16px auto;display:block;"></p>')


# ── 초크포인트 ────────────────────────────────────────────────────────────
def _emit(path, *, engine: str, html: str = "", rows=None, datasets=None,
          spec=None, kind: str = "", code_drawn: bool = False) -> str:
    """★ 픽셀을 낳은 모든 경로가 지나는 유일한 반환 지점 (사용자 박제 2026-08-10).

    왜 함수 하나로 모으는가 — 종전엔 반환이 4갈래(render_pro·designgen·render_spec·
    _render_single)라 그중 하나(render_pro)만 등록을 빠뜨려도 아무도 몰랐다.
    실제로 2026-08-10 경제 브리핑 8장 전부가 provenance 없이 발행됐고,
    `prepublish_gate._image_factuality_leg` 는 `prov=None` 을 fail-open 으로 통과시켰다.
    미검증이면 *글이 아니라 이미지를 버린다* (ADR 010: 거짓 차트 < 차트 없음).

    `kind` 는 image_data_verifier 가 정의한 비수치 종류(표·사진 등)를 명시할 때만 쓴다.
    비우면 표시 수치 유무에서 자동 파생된다.
    """
    if not path:
        return ""
    from JARVIS06_IMAGE.validators.image_data_verifier import (
        certify_image, gate_enabled, GATE_ENV)
    prov = certify_image(path, engine=engine, datasets=datasets,
                         rendered_html=html, rendered_rows=rows, spec=spec, kind=kind,
                         code_drawn=code_drawn)
    if prov.get("verified") is not True:
        # ★ 킬스위치 (라이브 안전장치 — `IMAGE_DATA_GATE=0`): 검증은 *언제나* 돌고
        #   기록도 남는다. 끌 수 있는 것은 '버릴지' 뿐이다. 그래야 껐다는 사실 자체가
        #   provenance(verified=False)로 남아 발행 게이트가 이어서 막을 수 있다 —
        #   끄는 순간 아무 흔적도 없어지는 스위치는 안전장치가 아니라 구멍이다.
        if not gate_enabled():
            log.warning(f"[infg] ⚠️ {GATE_ENV}=0 — 미검증 이미지를 폐기하지 않고 통과"
                        f"({engine}): {prov.get('issues')}")
            return str(path)
        log.warning(f"[infg] 검증 미통과({engine}) → 이미지 폐기: {prov.get('issues')}")
        return ""
    return str(path)


# ★ 공개 이름 — *별칭이지 사본이 아니다* (정의는 `_emit` 한 곳).
#   이미지 도메인 안의 다른 파일(주가 차트·썸네일)도 픽셀을 낳는다. 그 파일들이
#   `certify_image` 를 각자 부르면 초크포인트가 이름만 남는다 — 실제로 2026-08-10
#   `theme_charts._emit_price_chart` 가 그렇게 자기 인증을 한 벌 더 갖고 있었고,
#   그 한 벌만 `datasets` 를 안 넘겨 무감사로 통과했다. 문은 하나여야 한다.
emit_certified = _emit


def _ladder_emit(candidates) -> str:
    """후보를 순서대로 `_emit` 에 태우고 *처음 검증을 통과한* 경로를 돌려준다.

    ★ 이 함수에는 `_emit` 외의 출구가 없다 — 검증을 우회하는 반환문을 여기 두지 말 것.
      (초크포인트 검사 `image/chokepoint-single-exit` 는 `generate_infographic` 의 반환
       *꼴* 만 본다. 그 검사를 통과시키려고 검증을 건너뛰는 래퍼를 만들면 그 순간
       이 파일의 존재 이유가 사라진다.)
    앞 후보가 *검증에서* 떨어졌다고 곧장 빈 슬롯으로 끝내지 않는다 —
    다음 후보를 태운다. 전부 떨어지면 그때 폐기한다.
    """
    from JARVIS06_IMAGE.validators.image_data_verifier import verification_stats
    _before = verification_stats()
    got = ""
    for engine, ds_used, path, html, rows in candidates:
        got = _emit(path, engine=engine, html=html, rows=rows, datasets=ds_used)
        if got:
            break
    _log_verify_summary(_before, verification_stats(), bool(got))
    return got or ""


def _log_verify_summary(before: dict, after: dict, emitted: bool) -> None:
    """이 슬롯에서 인증이 *몇 번 돌았고 몇 장이 버려졌는지* 를 한 줄로 남긴다.

    ★ 왜 로그로도 남기나 (2026-08-10 사고): 8장이 검증 없이 나갔을 때 로그에는
      "실패" 도 "성공" 도 없었다 — **아무 줄도 없었다**. 게이트가 도는지 여부가
      기록되지 않으면, 게이트가 죽어도 조용하다. 숫자를 남기되 좋고 나쁨은
      판정하지 않는다(② — 정상 폐기율은 데이터에 따라 달라진다).
    누적 사유 분포는 verification_stats() 가, durable 기록은 events 테이블이 갖는다.
    """
    try:
        tried = int(after.get("certified", 0)) - int(before.get("certified", 0))
        if tried <= 0:
            return
        dropped = int(after.get("unverified", 0)) - int(before.get("unverified", 0))
        log.info("[infg] 인증 시도 %d · 채택 %s · 폐기 %d | 누적 %d건 폐기율 %s 사유 %s",
                 tried, "1" if emitted else "0", dropped,
                 after.get("certified", 0),
                 ("%.2f" % after["discard_rate"]) if after.get("discard_rate") is not None else "-",
                 after.get("issues") or {})
        for sig in (after.get("signals") or []):
            log.warning("[infg] 관측 신호: %s (인증 %d · 감사 %d)",
                        sig, after.get("certified", 0), after.get("audited", 0))
    except Exception:
        pass


def _dataset_ladder(datasets) -> list[list]:
    """데이터 폭 사다리 — 전체 → 뒤에서 하나씩 덜어낸 부분집합.

    표시 수치가 적을수록 근거 없는 수치가 섞일 여지도 준다. 단계 수는 harness 의
    재시도 상한(SSOT)에서 파생한다 — 숫자를 여기 박지 않는다(②동적 설계).
    """
    ladder = [list(datasets)]
    while len(ladder) < max(1, _max_attempts()) and len(ladder[-1]) > 1:
        ladder.append(ladder[-1][:-1])
    return ladder


def _render_candidates(title, subtitle, datasets, seed, out, *, chip, context):
    """렌더 후보를 *필요할 때만* 하나씩 만들어 내는 사다리 (engine, datasets, 경로, HTML, 행).

    두 축에서 파생한다 — 후보 목록을 박지 않는다:
      ① 렌더러 — pro_templates(결정론 템플릿, LLM 0회) → design-generation(opt-in LLM 저작)
      ② 데이터 폭 — `_dataset_ladder`
    조립은 전부 `pro_templates.render_pro` → `template_engine.render_layout` 한 벌이다.
    여기서 차트를 직접 그리지 않는다.
    """
    from JARVIS06_IMAGE.pro_templates import render_pro
    for ds in _dataset_ladder(datasets):
        try:
            _p, _h = render_pro(title, subtitle, ds, seed, out, chip=chip)
        except Exception as e:
            _g_report("image", e, module=__name__, func_name="_render_candidates")
            _p, _h = "", ""
        if _p:
            # rows = 조립부가 그렸다고 *스스로 말하는* 행. 다른 렌더러엔 쓰지 않는다.
            yield "render_pro", ds, _p, _h, _rendered_rows(ds)
        if _DESIGNGEN_ON:
            _d, _dh = _designgen(title, subtitle, ds, out,
                                 context or f"{title} — {subtitle}", seed)
            if _d:
                yield "designgen", ds, _d, _dh, None


def generate_infographic(title, subtitle, datasets, *, run_id="", slot_key="",
                         out_dir=None, context="", orientation=None, illustration_b64=None,
                         used=None, chip="", src="", category=""):
    """실데이터 인포그래픽 1장 생성 (단일 진입점). 실패·미검증 시 "".

    ★ 반환은 정확히 두 꼴만 존재한다 — `return ""` 과 `return _ladder_emit(...)`(→ `_emit`).
      픽셀을 낳는 모든 경로가 `_emit` 을 지나야 검증·provenance 등록을 빠뜨릴 수 없다.
      기계 강제: precommit `image/chokepoint-single-exit`.
    ★ `used` = 이 글에서 이미 쓴 골격 id 수 (제12조 시각 스타일 중복 방지) — seed 위상 이동.
    ★ `src`·`orientation`·`illustration_b64` 는 의도적으로 소비하지 않는다.
      출처는 `template_engine.source_label` 이 데이터에서만 파생하고(D20 우회 폐쇄),
      배치·방향은 골격(레이아웃 템플릿)이 정한다. 인자는 호출자 호환용으로만 남긴다.
    """
    datasets = [d for d in (datasets or []) if d.get("data") and _verify_dataset(d, category)]
    if not datasets:
        return ""
    for _d in datasets:
        _normalize_ds(_d)
    out_dir = Path(out_dir) if out_dir else Path(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = _seed_int(run_id, slot_key, title)
    _used = used if isinstance(used, (set, list, tuple)) else ()
    seed += 7 * len(_used)
    _sk = re.sub(r"[^0-9A-Za-z]", "", str(slot_key))[:10] or "s"
    out = out_dir / f"infg_{_sk}_{seed % 100000000}.jpg"
    return _ladder_emit(_render_candidates(title, subtitle, datasets, seed, out,
                                           chip=chip, context=context))


# ── 표 이미지 ─────────────────────────────────────────────────────────────
def _table_palette(seed) -> dict:
    """표 이미지 크롬 색 — 팔레트 레지스트리(`pro_templates._pick_palette`)에서 파생.

    ★ 종전엔 이 파일에 8벌짜리 색 표(`PALETTES`)가 따로 있었다 — 레시피 레지스트리와
      같은 일을 하는 두 번째 팔레트다. 읽지 못하면 색을 지어내지 않고 렌더를 포기한다.
    """
    try:
        from JARVIS06_IMAGE.pro_templates import _pick_palette
        pal = _pick_palette(int(seed))
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="_table_palette")
        return {}
    hero = list(pal.get("hero") or [])
    if len(hero) < 2 or not all(pal.get(k) for k in ("ink", "soft", "muted", "grid")):
        return {}
    return pal


def render_table_infographic(table_html, idx=0, out_dir=None, datasets=None, *,
                             title="", run_id=""):
    """HTML 표 → **인증된** 표 이미지. 대조군(datasets) 없으면 만들지 않는다.

    ★ 인증은 여기 한 번뿐이다 (사용자 박제 2026-08-10 — ①단일 진입점):
      종전엔 같은 이미지에 인증이 **두 번** 걸렸다.
        · 안쪽 — 여기서 `_emit(kind="table", 재료 0)`. 재료가 없으니 인증기는
          `kind_claim_unaudited:table` 을 남기고 **통과**시킨다. 즉 "표라고 하니
          표로 믿었다" 는 기록이지 검사가 아니다.
        · 바깥 — `block_assembler._table_image` 가 datasets + 표 HTML 로 재인증.
      호출자가 하나뿐이라 지금은 바깥(엄격)이 실효 게이트지만, **다른 호출자가
      생기는 순간 느슨한 안쪽이 출구가 된다.** 게이트를 두 개 두면 언제나 느슨한
      쪽으로 물이 샌다 — 그래서 재료를 안으로 끌어와 하나로 합쳤다.
    ★ `kind` 를 주장하지 않는다(②): 무엇으로 볼지는 인증기가 재료에서 파생한다.
      표 안 숫자는 *LLM 이 쓴 것* 이므로 대조군 없이 그림이 되면 텍스트 사실성
      게이트의 시야에서 통째로 사라진다.
    ★ '대조군 없음' 을 여기서 판정하지 않는다(②): 재료를 그대로 인증기에 넘기고
      판정은 owner 가 한다. 결과는 저절로 옳은 쪽으로 떨어진다 —
      숫자 없는 표는 통과(위조할 수치가 없다), 숫자가 있는데 대조군이 없으면 폐기.
      표는 그때 **텍스트로 남아** 사실성 게이트가 계속 본다 — 지우는 것보다 낫고,
      거짓 그림보다 낫다.
    실패 시 "" 반환 → 호출자(block_assembler)가 기존 plain 표 렌더러로 폴백.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return ""
    try:
        soup = BeautifulSoup(str(table_html), "html.parser")
        trs = soup.find_all("tr")
        if not trs:
            return ""

        def _ct(el):
            t = el.get_text(separator=" ", strip=True)
            t = re.sub(r"[\U0001F000-\U0001FFFF]", "", t).replace("⭐", "★").replace("\U0001F31F", "★")
            return t.strip()

        first = trs[0]
        headers = [_ct(c) for c in first.find_all(["th", "td"])]
        body_trs = trs[1:]
        rows = []
        for tr in body_trs:
            cells = tr.find_all(["td", "th"])
            row = []
            for c in cells:
                txt = _ct(c)
                col = None
                if "▲" in txt or ("+" in txt and "%" in txt):
                    col = "#e8513a"          # 상승 — 빨강 계열
                elif "▼" in txt or (txt.startswith("-") and "%" in txt):
                    col = "#1b78d6"          # 하락 — 파랑 계열
                row.append((txt, col))
            if any(t for t, _ in row):
                rows.append(row)
        if not rows or not headers:
            return ""
        ncol = max(len(headers), max(len(r) for r in rows))
        headers = (headers + [""] * ncol)[:ncol]
        rows = [(r + [("", None)] * ncol)[:ncol] for r in rows]

        seed = _seed_int(run_id, str(idx), title or (headers[0] if headers else ""))
        pal = _table_palette(seed)
        if not pal:
            return ""
        h0, h1 = list(pal["hero"])[:2]
        th = "".join(
            f"<th style='padding:14px 16px;text-align:{'left' if j == 0 else 'center'};"
            f"font-size:16px;font-weight:800;color:#fff;white-space:nowrap'>{h}</th>"
            for j, h in enumerate(headers))
        body_rows = []
        for i, row in enumerate(rows):
            bg = pal["soft"] if i % 2 == 0 else "#ffffff"
            tds = "".join(
                f"<td style='padding:13px 16px;text-align:{'left' if j == 0 else 'center'};"
                f"font-size:16px;font-weight:{'800' if j == 0 else '600'};"
                f"color:{col or (pal['ink'] if j == 0 else pal['muted'])};white-space:nowrap'>{txt}</td>"
                for j, (txt, col) in enumerate(row))
            body_rows.append(f"<tr style='background:{bg}'>{tds}</tr>")
        card = (f"<div style='background:#fff;border-radius:20px;border:1px solid {pal['grid']};"
                f"box-shadow:0 8px 28px rgba(20,40,80,.08);padding:8px;overflow:hidden'>"
                f"<table style='width:100%;border-collapse:collapse;font-family:{FONT}'>"
                f"<thead><tr style='background:linear-gradient(135deg,{h0},{h1})'>{th}</tr></thead>"
                f"<tbody>{''.join(body_rows)}</tbody></table></div>")
        _t = str(title or (headers[0] if headers else ""))[:24]
        from JARVIS06_IMAGE.template_engine import BRAND
        W = 1280
        html = (f"<!DOCTYPE html><html lang=ko><head><meta charset=UTF-8><style>"
                f"@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;700;800;900&display=swap');"
                f"*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:{FONT};width:{W}px;background:#fff}}"
                f"</style></head><body><div style='width:{W}px;background:#fff'>"
                f"<div style='padding:34px 40px;background:linear-gradient(135deg,{h0},{h1})'>"
                f"<h1 style='color:#fff;font-size:34px;font-weight:900;letter-spacing:-.02em'>{_t}</h1></div>"
                f"<div style='background:{pal['soft']};padding:22px'>{card}</div>"
                f"<div style='padding:16px 40px;border-top:1px solid {pal['grid']};text-align:right;"
                f"font-size:14px;font-weight:800;color:{pal['ink']}'>{BRAND}</div>"
                f"</div></body></html>")
        out_dir = Path(out_dir) if out_dir else Path(".")
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"tableinfg_{idx}_{seed % 100000000}.jpg"
        from JARVIS06_IMAGE.html_infographic import _html_to_jpg
        ok = _html_to_jpg(html, Path(out), width=W)
        _p = Path(out)
        if not (ok and _p.exists() and _p.stat().st_size > 2000):
            return ""
        # ★ 표 안의 수치를 *실데이터와 대조* 한다. 표의 숫자는 LLM 이 쓴 것이므로
        #   대조 없이 그림이 되면 그 순간 아무도 검사하지 않는 수치가 된다.
        #   (판정 본체는 image_data_verifier 단독 — 여기서 검사를 만들지 말 것.)
        return _emit(out, engine="table_infographic",
                     html=str(table_html), datasets=list(datasets or []))
    except Exception as e:
        _g_report("image", e, module=__name__, func_name="render_table_infographic")
        return ""


__all__ = ["generate_infographic", "render_table_infographic", "data_image_html",
           "emit_certified"]
