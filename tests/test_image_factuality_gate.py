"""2026-08-10 경제 인포그래픽 사고 — 회귀 방지.

★ 무엇이 났나
  라이브 렌더 경로(pro_templates.render_pro)가 provenance 를 남기지 않아
  `prepublish_gate._image_factuality_leg` 의 `prov=None` 이 **fail-open** 으로 읽혔고,
  8장이 검증 없이 발행됐다. 그 안에는 존재하지 않는 집계('합계 27.2%'),
  화면에 없는 행까지 센 개수('항목 수 8개' vs 막대 7개), 비교 대상이 없는데
  트랙을 꽉 채운 1행 막대가 들어 있었다.

★ 이 파일이 겨누는 것
  **실제 소비자가 부르는 심볼**만 겨눈다. 대역(代役)을 세우면 단언이 공허해진다 —
  대역이 소비자와 다른 심볼을 보던 전례가 있다(커밋 4cf23ba).
  · 발행 게이트     → JARVIS02_WRITER.prepublish_gate._image_factuality_leg
  · 채점            → JARVIS02_WRITER.post_scorer._b19_chart / _b20_visual_div
  · 차트형·가산성   → JARVIS06_IMAGE.validators.image_data_verifier (규정13 owner)
  · 렌더 산출물     → JARVIS06_IMAGE.pro_templates.build_html (라이브 1순위 경로)
  · 커밋 게이트     → shared.precommit_check

  네트워크·LLM·.env 에 기대지 않는다 (커밋 47b2574: `.env` 의존 테스트가 CI 를 빨갛게 했다).
"""
from __future__ import annotations

import importlib
import re

import pytest

from JARVIS02_WRITER import prepublish_gate as pg
from JARVIS02_WRITER import post_scorer as ps
from JARVIS06_IMAGE.validators import image_data_verifier as idv


# ═══════════════════════════════════════════════════════════════
# 1. 발행 게이트 — 미등록·미검증 수치 이미지는 나가지 못한다
# ═══════════════════════════════════════════════════════════════

_ATTR = idv.DATA_IMAGE_ATTR


def _draft(paths):
    return {"blocks": [("image", p) for p in paths]}


def _body(paths, marked=()):
    out = []
    for p in paths:
        mark = f" {_ATTR}='1'" if p in marked else ""
        out.append(f"<p>본문</p><img src='{p}'{mark}>")
    return "".join(out) + "<p>" + "가" * pg._MIN_BODY + "</p>"


@pytest.fixture(autouse=True)
def _clean_registry():
    """provenance 레지스트리는 process-local — 테스트 간 누수를 막는다."""
    idv._PROV_REGISTRY.clear()
    yield
    idv._PROV_REGISTRY.clear()


def _register(path, prov):
    idv._PROV_REGISTRY[str(__import__("pathlib").Path(path).resolve())] = prov


def test_미등록_수치이미지는_차단된다():
    """★ 이번 사고의 정체 — 등록이 없다는 것을 '문제 없음' 으로 읽던 구멍."""
    p = "/tmp/jarvis_t/infg_slot3_1234567.jpg"
    issues = pg._image_factuality_leg(_draft([p]), _body([p], marked=[p]))
    assert issues, "표식된 수치 이미지가 provenance 없이 통과했다 — fail-open 재발"
    assert all(i["kind"] == "data_insufficient" for i in issues)


def test_표식없는_이미지는_차단하지_않는다():
    """썸네일·표·재사용 이미지까지 잡으면 4조합이 즉시 발행 정지한다(오차단 0)."""
    p = "/tmp/jarvis_t/thumbnail_market_20260810.png"
    assert pg._image_factuality_leg(_draft([p]), _body([p])) == []


def test_등록됐지만_verified_가_True_가_아니면_차단된다():
    """None·누락·False 전부 차단 — 종전엔 `is False` 만 봤다."""
    for verified in (None, False):
        idv._PROV_REGISTRY.clear()
        p = "/tmp/jarvis_t/infg_slot4_777.jpg"
        _register(p, {"kind": "numeric_chart", "verified": verified, "engine": "render_pro"})
        assert pg._image_factuality_leg(_draft([p]), _body([p])), f"verified={verified} 통과"

    idv._PROV_REGISTRY.clear()
    p = "/tmp/jarvis_t/infg_slot4_777.jpg"
    _register(p, {"kind": "numeric_chart", "engine": "render_pro"})   # verified 키 누락
    assert pg._image_factuality_leg(_draft([p]), _body([p]))


def test_검증통과한_수치차트는_통과한다():
    p = "/tmp/jarvis_t/infg_slot1_1.jpg"
    _register(p, {"kind": "numeric_chart", "verified": True, "engine": "render_pro"})
    assert pg._image_factuality_leg(_draft([p]), _body([p])) == []


def test_비수치_이미지는_수치사실성_대상이_아니다():
    """사진·썸네일·표는 verified 가 False 여도 이 레그가 잡을 대상이 아니다."""
    for kind in ("thumbnail", "photo", "table", "text_card"):
        idv._PROV_REGISTRY.clear()
        p = f"/tmp/jarvis_t/{kind}_1.png"
        _register(p, {"kind": kind, "verified": False})
        assert pg._image_factuality_leg(_draft([p]), _body([p])) == [], kind


def test_구버전_provenance_는_종전_계약대로_판정한다():
    """kind 가 없던 기록(v1)까지 새 규칙으로 잡으면 과잉 차단이 된다."""
    p1 = "/tmp/jarvis_t/v1_bad.png"
    _register(p1, {"verified": False, "method": "unverified_render"})
    assert pg._image_factuality_leg(_draft([p1]), _body([p1]))

    idv._PROV_REGISTRY.clear()
    p2 = "/tmp/jarvis_t/v1_ok.png"
    _register(p2, {"verified": True, "method": "no_data"})
    assert pg._image_factuality_leg(_draft([p2]), _body([p2])) == []


def test_검증기가_죽어있으면_발행하지_않는다(monkeypatch):
    """'검증할 수 없다' 를 '검증할 게 없다' 로 읽으면 게이트는 있으나 마나다."""
    monkeypatch.setattr(idv, "verifier_effective", lambda: False)
    p = "/tmp/jarvis_t/whatever.png"
    issues = pg._image_factuality_leg(_draft([p]), _body([p]))
    assert issues and issues[0]["kind"] == "data_insufficient"

    monkeypatch.setattr(idv, "verifier_effective",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert pg._image_factuality_leg(_draft([p]), _body([p]))


def test_차단사유_지문이_run마다_변하지_않는다():
    """detail 에 seed 파일명이 들어가면 attempt 마다 지문이 달라져 abort 가 안 걸린다."""
    a = "/tmp/jarvis_t/infg_slot3_1234567.jpg"
    b = "/tmp/jarvis_t/infg_slot3_9999999.jpg"
    da = pg._image_factuality_leg(_draft([a]), _body([a], marked=[a]))[0]["detail"]
    db = pg._image_factuality_leg(_draft([b]), _body([b], marked=[b]))[0]["detail"]
    assert da == db, f"지문 불안정: {da!r} != {db!r}"


def test_킬스위치가_레그를_끈다(monkeypatch):
    monkeypatch.setenv("PREPUBLISH_IMAGE_GATE", "0")
    assert pg._disabled("PREPUBLISH_IMAGE_GATE") is True


# ═══════════════════════════════════════════════════════════════
# 2. 채점 — 미검증 차트 감점 / 제12조 축이 입력을 본다
# ═══════════════════════════════════════════════════════════════

def test_b19_는_게이트와_같은_계약을_쓴다():
    """같은 판정이 두 곳에 다른 규칙으로 남으면 한쪽만 고쳐져 재발한다(③원칙)."""
    p = "/tmp/jarvis_t/chart_a.png"
    _register(p, {"kind": "numeric_chart", "verified": None, "engine": "render_pro"})
    assert ps._b19_chart(_draft([p])) < ps.mx("B19_chart")

    idv._PROV_REGISTRY.clear()
    _register(p, {"kind": "numeric_chart", "verified": True, "engine": "render_pro"})
    assert ps._b19_chart(_draft([p])) == ps.mx("B19_chart")


def test_b20_는_입력을_실제로_본다():
    """★ 종전엔 draft 를 읽지도 않고 상수 만점이었다(감사 D15).

    같은 행을 두 번 그린 이미지가 있으면 감점되어야 한다.
    """
    rows = [{"label": "A", "value": 1, "unit": "%"}]
    a, b = "/tmp/jarvis_t/i1.png", "/tmp/jarvis_t/i2.png"
    _register(a, {"kind": "numeric_chart", "verified": True, "engine": "render_pro", "values": rows})
    _register(b, {"kind": "numeric_chart", "verified": True, "engine": "render_pro", "values": rows})
    dup_score = ps._b20_visual_div(_draft([a, b]))
    assert dup_score < ps.mx("B20_visual_div"), "같은 시각화 2장인데 감점 0"

    idv._PROV_REGISTRY.clear()
    _register(a, {"kind": "numeric_chart", "verified": True, "engine": "render_pro", "values": rows})
    _register(b, {"kind": "numeric_chart", "verified": True, "engine": "render_pro",
                  "values": [{"label": "B", "value": 2, "unit": "%"}]})
    assert ps._b20_visual_div(_draft([a, b])) == ps.mx("B20_visual_div")


def test_b20_은_관측대상이_없으면_감점하지_않는다():
    """이미지가 0~1장이면 '중복' 은 성립하지 않는다 — 잡음으로 감점하지 말 것."""
    assert ps._b20_visual_div({"blocks": []}) == ps.mx("B20_visual_div")


# ═══════════════════════════════════════════════════════════════
# 3. 렌더 산출물 — 거짓 집계·검산 불가·정보량 0 인코딩
# ═══════════════════════════════════════════════════════════════

# 2026-08-10 slot3 재현: 같은 지표가 여러 시점으로 8행, 공표 합계 없음
_RATE_ROWS = [
    {"label": "기준금리", "value": 2.75}, {"label": "콜금리", "value": 2.769},
    {"label": "국고채3년", "value": 3.742}, {"label": "회사채AA-", "value": 4.445},
    {"label": "통안증권91일", "value": 2.787}, {"label": "미국채10년", "value": 4.66},
    {"label": "기준금리 2023.08", "value": 3.5}, {"label": "기준금리 2025.05", "value": 2.5},
]
_RATE_DS = {"title": "금리 지표 (%)", "unit": "%", "data": _RATE_ROWS}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_비가산_지표에_합계가_인쇄되지_않는다():
    """'합계 27.2%'·'합계 88,485pt'·'합계 4,368원' — 현실에 없는 수치가 8장 중 5장에 찍혔다."""
    from JARVIS06_IMAGE.pro_templates import build_html
    for ds in (_RATE_DS,
               {"title": "증시 지표 (pt)", "unit": "pt",
                "data": [{"label": "다우", "value": 54037}, {"label": "나스닥", "value": 26691},
                         {"label": "S&P", "value": 7758}]},
               {"title": "상장유지 요건", "unit": "억원",
                "data": [{"label": "코스닥", "value": 300}, {"label": "코스닥2", "value": 350},
                         {"label": "일본그로스", "value": 900}]}):
        html = build_html(ds["title"], "수집 실데이터 기반", [ds], 20260810, "", chip="")
        assert "합계" not in _text(html), f"{ds['title']} 에 합계 KPI 부활"


def test_가산성은_증명된_경우에만_허용된다():
    """기본값은 '불가'. 합계 카드는 장식이고 거짓 합계는 손해다 — 입증 책임은 합계 쪽."""
    assert idv.additive_total(_RATE_DS)[0] is None
    published = {"title": "구성", "unit": "억원",
                 "data": [{"label": "A", "value": 300.0, "basis": "actual",
                           "category": "x", "as_of": "2026-08"},
                          {"label": "B", "value": 700.0, "basis": "actual",
                           "category": "x", "as_of": "2026-08"}],
                 "totals": {"value": 1000.0, "label": "합계"}}
    assert idv.additive_total(published)[0] == 1000.0


def test_표시_행_집합이_히어로와_차트에서_같다():
    """'항목 수 8개' 인데 막대는 7개 — 독자가 검산하면 즉시 틀리던 모순(D04/D07)."""
    from JARVIS06_IMAGE import template_engine as te
    from JARVIS06_IMAGE.pro_templates import BAR_MAX_ROWS, build_html

    view = te.view_rows(_RATE_DS)
    assert len(view) == min(len(_RATE_ROWS), BAR_MAX_ROWS)

    html = build_html(_RATE_DS["title"], "sub", [_RATE_DS], 20260810, "", chip="")
    text = _text(html)
    shown = {str(r.get("label")) for r in view}
    dropped = {str(r["label"]) for r in _RATE_ROWS} - shown
    assert dropped, "절단이 일어나지 않으면 이 회귀 테스트가 의미를 잃는다"
    for lb in dropped:
        assert lb not in text, f"화면에 없는 행 '{lb}' 이 텍스트에 남아 계산에 섞인다"


def test_한_행짜리_데이터는_막대로_그리지_않는다():
    """행이 1개면 v/vmax 정규화 때문에 막대가 트랙 100% 를 채운다 = 정보량 0 (D13/D21)."""
    from JARVIS06_IMAGE import template_engine as te
    from JARVIS06_IMAGE.pro_templates import PALETTES, _bar_chart

    one = {"title": "에너지 지표", "unit": "USD/배럴",
           "data": [{"label": "WTI 유가", "value": 78.2}]}
    assert idv.chart_fit(one) == "kpi_cards"

    pal = PALETTES[0]
    block = te._slot_chart_block(one, pal, 1)
    bar = _bar_chart([("WTI 유가", 78.2)], pal, unit="USD/배럴")
    assert block != bar, "단일값 dataset 이 여전히 랭킹 막대로 렌더된다"


def test_막대_최소행수_규칙을_렌더러가_볼_수_있다():
    """'막대는 최소 2행' 규칙이 정작 막대를 그리는 코드에서 보이지 않던 배선 공백(D13)."""
    assert idv.min_rows("bar_chart") >= 2
    assert idv.min_rows("kpi_cards") == 1


# ═══════════════════════════════════════════════════════════════
# 4. 커밋 게이트 — 규정에 러너가 붙어 있는가
# ═══════════════════════════════════════════════════════════════

_pcc = importlib.import_module("shared.precommit_check")


def test_image_카테고리가_JARVIS06_내부를_검사한다():
    """종전 check_image 는 JARVIS06 내부를 통째로 면제해 fontsize 65건이 초록이었다."""
    rep = _pcc.Report()
    _pcc.check_image(rep)
    assert rep.checks_run > 2, "외향 2종만 돌고 내부 레그가 없다"
    ids = {v.check_id for v in rep.violations}
    assert "image/self-check" not in ids, f"검사 전제 붕괴: {[v.text for v in rep.violations if v.check_id=='image/self-check']}"


def test_문서의_검증grep_이_러너에_배선된다():
    """규정을 문서에 적어두고 러너를 안 만들면 반드시 샌다(대시보드 폰트 94곳과 같은 병)."""
    doc = (_pcc.ROOT / _pcc._J06_DOC).read_text(encoding="utf-8")
    rules = _pcc._j06_doc_rules(doc)
    assert rules, "JARVIS06/CLAUDE.md 의 검증 grep 을 하나도 파싱하지 못했다"
    pats = [r["pat"].pattern for r in rules]
    assert any("fontsize" in p for p in pats), pats
    assert any("_synth_data" in p for p in pats), pats
    # 외향 규칙(JARVIS06 바깥을 겨눔)은 여기서 돌리지 않는다
    assert not any("pollinations" in p for p in pats), pats


def test_표시문구_리터럴을_꼴로_잡는다():
    """어휘 목록이 아니라 꼴로 판정 — 새 문구도 잡고, 아이콘 키는 안 잡는다."""
    src = (
        "def f(pal):\n"
        "    blocks.append(_hero_stat(pal, '항목 수', '8개', f'합계 {x}%', a, b))\n"
        "    cards.append(_mini_card(pal, 'chart', s, i, t, v, u))\n"
        "    blocks.append(_hero_stat(pal, 'KEY METRIC', v, '', a, b))\n"
    )
    hits = _pcc._display_literals(src, {"_hero_stat", "_mini_card"})
    texts = {t for _, _, t in hits}
    assert "항목 수" in texts and "합계" in " ".join(texts)
    assert "KEY METRIC" in texts, "영문 표시 문구도 잡아야 한다"
    assert "chart" not in texts, "아이콘 키('chart')는 표시 문구가 아니다 — 오탐"


def test_레시피_고정문구를_잡고_CSS_는_안잡는다():
    tpl = ("<style>.a{content:'x';font-size:14px}</style>"
           "<div class='eyebrow'>핵심 지표 · KEY METRIC</div>"
           "<h1>{{TITLE}}</h1><div>{{CHART_1}}</div><span>2026</span>")
    lits = _pcc._markup_display_text(tpl)   # 2026-08-10 개명: 레시피·프리미티브 몸통 공용
    assert "핵심 지표 · KEY METRIC" in lits
    assert not any("font-size" in x for x in lits), lits
    assert "2026" not in lits, "숫자만 있는 노드는 표시 문구가 아니다"


def test_형제사본_탐지가_실제_모듈을_읽는다():
    defs = _pcc._j06_module_defs()
    assert defs, "JARVIS06 모듈 레벨 def 를 하나도 읽지 못했다 — 검사 무력화"


# ── 2026-08-10 3차 — 커밋 게이트가 *못 보던* 것들 (회귀 방지) ────────────────
#   ① 프리미티브 *몸통 안* 의 표시 문구 ② 재시도 상한 SSOT 사본

def test_프리미티브_몸통에_박힌_표시문구를_잡는다():
    """종전 레그는 *호출부가 넘긴* 리터럴만 봤다.

    정작 사고 문구(`↑ 2위 대비 …`)는 프리미티브가 자기 몸통에서 마크업에 직접
    박는다. 인쇄되는 글자는 어느 쪽에서 오든 이미지를 폐기시킨다.
    """
    body = ("<svg width='100%'><text x='10' y='20' fill='#111'>↑ 2위 대비 3배</text>"
            "<text x='10' y='40'>12.3</text></svg>")
    lits = _pcc._markup_display_text(body)
    assert any("2위 대비" in x for x in lits), lits
    assert not any(x.strip() == "12.3" for x in lits), "수치 노드는 표시 문구가 아니다"


def test_산문속_꺾쇠는_태그가_아니다():
    """LLM 프롬프트의 `<English scene that ...>` 를 태그로 오인하면 오탐이 쏟아진다."""
    prompt = ('Return ONLY JSON:\n'
              '  "photo_prompt": "<English scene that CLEARLY represents the topic — real>",\n'
              '  "color_theme": "<one theme from the list>"')
    assert _pcc._markup_display_text(prompt) == []


def test_정규식_리터럴은_표시문구가_아니다():
    assert _pcc._markup_display_text(r"(<p[^>]*>[\s\S]*?</p>)") == []


def test_재시도상한_SSOT_주인을_실물에서_찾는다():
    owner, owners = _pcc._retry_ssot_owner()
    assert owner and owner.endswith("harness.py"), (owner, owners)
    # 상한을 받는 인자 이름도 실물(ActionDefinition 기본값)에서 파생한다 — 이름 박제 금지
    assert _pcc._retry_ssot_kwarg(owner) == "max_attempts"


def test_재시도상한_레그가_사본을_차단한다():
    """2026-08-10: 사본 9벌을 파생 leaf 하나로 모은 뒤 **차단 등급으로 잠갔다**.

    종전 이 테스트는 "경고 등급으로 *보고* 한다" 를 확인했다. 그 형태는 위반이
    남아 있어야만 통과하므로, 다 고치는 순간 테스트가 깨진다 — 즉 *결함의 존재* 를
    계약으로 삼고 있었다. 지금 지켜야 할 계약은 셋이다:
      ① 저장소 현행 위반 0  ② 등급 block  ③ 레그가 살아 있다(사본을 넣으면 막힌다)
    ③ 이 없으면 ①은 '검사가 죽어서 0' 과 구별되지 않는다.
    """
    # ① 현행 저장소 — 사본 0
    rep = _pcc.Report()
    _pcc.check_retry_ssot(rep)
    vs = [v for v in rep.violations if v.category == "retry"]
    assert vs == [], f"재시도 상한 사본이 남아 있다: {[(v.check_id, v.path) for v in vs]}"
    assert rep.ok

    # ② 등급 — 경고로 되돌리면 영원히 안 고쳐진다
    assert _pcc._RETRY_SEVERITY == "block", "차단 등급을 경고로 되돌렸다"

    # ③ 레그 생존 — 폴백 리터럴을 주입하면 잡고, 커밋을 막는다
    from pathlib import Path as _Path
    probe = _Path(_pcc.ROOT) / "_retry_ssot_probe_tmp.py"
    probe.write_text(
        "def _copy() -> int:\n"
        "    try:\n"
        "        from JARVIS00_INFRA.harness import DEFAULT_MAX_ATTEMPTS\n"
        "        return DEFAULT_MAX_ATTEMPTS\n"
        "    except Exception:\n"
        "        return 7\n")
    try:
        _pcc._RGLOB_CACHE.clear(); _pcc._FILE_CACHE.clear()
        rep2 = _pcc.Report()
        _pcc.check_retry_ssot(rep2)
        ids = {v.check_id for v in rep2.violations if v.category == "retry"}
        assert "retry/ssot-fallback" in ids, f"주입한 폴백 사본을 못 잡았다: {ids}"
        assert not rep2.ok, "block 등급인데 커밋이 막히지 않는다"
    finally:
        probe.unlink(missing_ok=True)
        _pcc._RGLOB_CACHE.clear(); _pcc._FILE_CACHE.clear()
