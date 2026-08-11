"""2026-08-10 인포그래픽 감사 21건 — **뮤테이션 검증에서 무방비로 드러난 결함**의 회귀 테스트.

★ 왜 이 파일이 따로 있나
  21건을 하나씩 되살려(뮤테이션) 무엇이 빨개지는지 실측했다. 절반은 기존 게이트·테스트가
  잡았지만(`additive_total`·`chart_fit`·`verify_rendered_html`·B20·precommit image 레그),
  **아무것도 잡지 못한 갈래가 11개** 있었다. 그것들만 여기서 막는다.
  "테스트가 있다" 와 "작동한다" 는 다르다 — 아래 각 테스트는 *실제로 되살린 결함* 에서
  빨개지는 것을 확인하고 넣었다(주석의 `되살린 방법` 이 그 뮤테이션이다).

★ 이 파일의 규칙
  · 대역이 아니라 **실제 소비자가 쓰는 심볼** 을 겨눈다 (4cf23ba 의 교훈 —
    precommit 의 내부 헬퍼를 겨누면 정작 런타임 게이트가 죽어도 초록이었다).
  · `.env`·네트워크·LLM 에 기대지 않는다 (47b2574 의 교훈). 전부 순수 함수 호출.
  · 상수를 박지 않는다 — 티어 상한·소스 목록은 owner 에서 조회해 파생한다.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from JARVIS06_IMAGE import template_engine as te                     # noqa: E402
from JARVIS06_IMAGE.pro_templates import _pick_palette               # noqa: E402
from JARVIS06_IMAGE.validators import image_data_verifier as idv     # noqa: E402
from JARVIS09_COLLECTOR import evidence_pack as ep                   # noqa: E402
from JARVIS09_COLLECTOR.models import policy_for                     # noqa: E402
from JARVIS09_COLLECTOR.models import trust_rank                     # noqa: E402
from JARVIS09_COLLECTOR.source_registry import (                     # noqa: E402
    SOURCE_NAMES, SOURCE_TRUST_TIER)


# ── 공용 헬퍼 ────────────────────────────────────────────────────────────
def _fact(fid, label, value, *, unit="%", as_of="", stype="bok_official",
          name="한국은행", category="금리", basis="", verbatim=True, url="https://x/y"):
    return {"id": fid, "kind": "stat", "label": label, "value": str(value), "unit": unit,
            "as_of": as_of, "category": category, "basis": basis, "verbatim": verbatim,
            "statement": f"{label} 은 {value}{unit} 이다",
            # tier 는 박지 않는다 — 레지스트리에서 파생 (수집기가 실제로 하는 일과 동일)
            "source": {"type": stype, "name": name, "url": url,
                       "tier": trust_rank(stype)}}


def _datasets(facts, theme="금리"):
    pack = {"theme": theme, "category": "economic", "facts": facts}
    return ep.facts_to_datasets(pack, category="economic", llm_label_fallback=False)


def _source_above(cap: int) -> str:
    """차트 상한 티어를 *넘는* 실존 소스 키 — 목록을 박지 않고 레지스트리에서 파생."""
    for k, t in sorted(SOURCE_TRUST_TIER.items(), key=lambda kv: kv[1]):
        if t > cap:
            return k
    pytest.skip("차트 상한을 넘는 소스가 레지스트리에 없다")


def _fn_of(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{path.name} 에 {name}() 가 없다 — 검사 전제 붕괴")


# ═════════════════════════════════════════════════════════════════════════
# D01 — 행 단위 출처·기준일 보존
#   되살린 방법: `evidence_pack._row_meta` 가 `{}` 를 돌려주게 (행에서 as_of·source 폐기)
#   실측: pytest 391p·precommit EXIT=0·재현렌더 판정 전부 초록. 그동안 배지는
#         '실데이터', 푸터는 '공개 통계' 로 조용히 열화하고 환율 시계열이 카테고리
#         막대로 뒤집혔다 — 아무도 잡지 못했다.
# ═════════════════════════════════════════════════════════════════════════
def test_d01_행이_자기_출처와_기준일을_들고_간다():
    facts = [_fact("f1", "기준금리", 2.75, as_of="2026-08-07",
                   stype="bok_official", name="한국은행"),
             _fact("f2", "국고채3년", 2.769, as_of="2026-08-06",
                   stype="kofia", name="금융투자협회")]
    ds = _datasets(facts)
    assert ds, "수치 fact 2건이 dataset 으로 승격되지 않았다"
    rows = ds[0]["data"]
    assert len(rows) == 2, rows
    for r in rows:
        assert r.get("as_of"), f"행이 기준일을 잃었다: {r}"
        assert isinstance(r.get("source"), dict) and r["source"].get("name"), \
            f"행이 출처를 잃었다: {r}"
    # 소비자(JARVIS06)가 그 진실을 복원할 수 있어야 한다
    prov = idv.row_provenance(ds[0])
    assert prov["as_of_range"]["distinct"] == 2, prov
    assert prov["mixed_time"] is True, prov
    assert {s["name"] for s in prov["sources"]} == {"한국은행", "금융투자협회"}, prov


def test_d01_시점이_섞이면_배지가_단일시점을_주장하지_않는다():
    facts = [_fact("f1", "기준금리", 2.75, as_of="2026-08-07"),
             _fact("f2", "국고채3년", 3.5, as_of="2023-08-01",
                   stype="ecos", name="한국은행 ECOS")]
    badge = te._eyebrow_from_data(_datasets(facts))
    assert badge != "실데이터", "기준일이 통째로 유실됐다"
    assert "~" in badge, f"섞인 시점을 단일 시점으로 박제했다: {badge!r}"


def test_d01_푸터_출처가_행별_출처_전부를_반영한다():
    facts = [_fact("f1", "기준금리", 2.75, as_of="2026-08-07",
                   stype="bok_official", name="한국은행"),
             _fact("f2", "국고채3년", 2.769, as_of="2026-08-06",
                   stype="kofia", name="금융투자협회")]
    label = te.source_label(_datasets(facts))
    assert "한국은행" in label and "금융투자협회" in label, label


# ═════════════════════════════════════════════════════════════════════════
# D02 / D08 — 접미 번호 위조 금지 · 정당한 관측 삭제 금지
#   되살린 방법 A: `_disambiguate_labels` 를 옛 `_dedup_labels`(' (2)',' (3)')로 교체
#     → pytest·precommit 초록. 스크래치패드 재현 스크립트만 잡았다(저장소에 안 남는다).
#   되살린 방법 B: ±5% tolerance 병합 도입 → 전부 초록인 채 환율 한 행이 사라졌다.
# ═════════════════════════════════════════════════════════════════════════
def test_d02_같은_라벨_다른_시점은_접미번호가_아니라_시점으로_구분한다():
    facts = [_fact("f1", "달러/원 환율", 1418.8, unit="원", category="환율",
                   as_of="2026-08-07", stype="bok_official", name="한국은행"),
             _fact("f2", "달러/원 환율", 1407.78, unit="원", category="환율",
                   as_of="2026-08-10", stype="finance", name="Yahoo Finance"),
             _fact("f3", "달러/원 환율", 1541.5, unit="원", category="환율",
                   as_of="2026-06-01", stype="finance", name="Yahoo Finance")]
    labels = [r["label"] for r in _datasets(facts)[0]["data"]]
    assert len(labels) == 3, f"정당한 시점 3개가 삭제됐다: {labels}"
    assert len(set(labels)) == 3, f"구분 차원이 복원되지 않았다: {labels}"
    for bad in ("(2)", "(3)"):
        assert not any(bad in lb for lb in labels), \
            f"접미 번호로 별개 항목을 위조했다: {labels}"
    # 복원된 구분자는 *지어낸 번호* 가 아니라 fact 가 이미 갖고 있던 시점이어야 한다
    assert all(any(y in lb for y in ("2026", "2023")) for lb in labels), labels


def test_d08_근소한_차이의_행을_조용히_삭제하지_않는다():
    """1,418.8 vs 1,407.78 은 0.78% 차 — tolerance 병합을 넣으면 한 행이 사라진다."""
    facts = [_fact("f1", "달러/원 환율", 1418.8, unit="원", category="환율",
                   as_of="2026-08-07"),
             _fact("f2", "달러/원 환율", 1407.78, unit="원", category="환율",
                   as_of="2026-08-10")]
    vals = sorted(float(r["value"]) for r in _datasets(facts)[0]["data"])
    assert vals == [1407.78, 1418.8], f"정당한 관측이 병합돼 사라졌다: {vals}"


# ═════════════════════════════════════════════════════════════════════════
# D05 — 출처 등급 배제 (사설·보도자료가 한국은행 API 와 동등하게 차트가 되던 결함)
#   되살린 방법: `dataset_admissible` → `(True,"ok")` · `_chart_admissible` → 전량 통과
#   실측: pytest·precommit·재현렌더 전부 초록.
# ═════════════════════════════════════════════════════════════════════════
def test_d05_상한_티어를_넘는_출처는_차트가_되지_못한다():
    cap = int(policy_for("economic")["chart_max_source_tier"])
    low = _source_above(cap)
    facts = [_fact("f1", "점포수", 100, unit="개", category="유통", stype=low,
                   name="저신뢰 출처", as_of="2026-08-01"),
             _fact("f2", "폐업수", 200, unit="개", category="유통", stype=low,
                   name="저신뢰 출처", as_of="2026-08-01")]
    assert _datasets(facts) == [], \
        f"티어 {SOURCE_TRUST_TIER[low]} 출처(상한 {cap})가 차트로 승격됐다"


def test_d05_dataset_admissible_이_티어_상한을_실제로_집행한다():
    cap = int(policy_for("economic")["chart_max_source_tier"])
    low = _source_above(cap)
    ds = {"title": "t", "unit": "개",
          "data": [{"label": "a", "value": 1}, {"label": "b", "value": 2}],
          "source": {"provider": f"evidence:{low}", "name": "n",
                     "url": "https://example.com", "tier": SOURCE_TRUST_TIER[low]}}
    ok, why = idv.dataset_admissible(ds, category="economic")
    assert ok is False, f"저신뢰 출처가 통과했다 ({why})"
    assert "tier" in why, why


def test_d05_원문대조_실패_행은_차트가_되지_못한다():
    pol = policy_for("economic")
    above, cap = int(pol["chart_verbatim_above_tier"]), int(pol["chart_max_source_tier"])
    key = next((k for k, t in sorted(SOURCE_TRUST_TIER.items(), key=lambda kv: kv[1])
                if above < t <= cap), None)
    if key is None:
        pytest.skip("원문 대조 대상 티어의 소스가 레지스트리에 없다")
    facts = [_fact("f1", "취업자", 1000, unit="명", category="고용", stype=key,
                   name="뉴스", as_of="2026-08-01", verbatim=False),
             _fact("f2", "실업자", 2000, unit="명", category="고용", stype=key,
                   name="뉴스", as_of="2026-08-01", verbatim=False)]
    assert _datasets(facts) == [], "원문 대조에 실패한 값이 차트가 됐다"


# ═════════════════════════════════════════════════════════════════════════
# D09 — 실적과 전망을 한 축에 올리지 않는다 (그룹핑 키에 basis 포함)
#   되살린 방법: 그룹핑 시 `basis = ""` 로 눌러 키에서 제거
#   실측: 전부 초록 (이 캐시엔 마침 발현 데이터가 없었다 — 다음 주제에서 터진다).
# ═════════════════════════════════════════════════════════════════════════
def test_d09_실적과_전망은_같은_차트에_섞이지_않는다():
    facts = [_fact("f1", "상반기 취업자", 300000, unit="명", category="고용",
                   basis="actual", as_of="2026-07-01"),
             _fact("f2", "상반기 실업자", 62000, unit="명", category="고용",
                   basis="actual", as_of="2026-07-01"),
             _fact("f3", "2026 취업자 전망", 103000, unit="명", category="고용",
                   basis="forecast", as_of="2026-01-01")]
    ds = _datasets(facts)
    assert ds, "고용 fact 가 통째로 배제됐다 — 픽스처가 잘못됐거나 승격이 죽었다"
    for d in ds:
        bases = {str(r.get("basis") or "") for r in d["data"]}
        assert len(bases) == 1, f"실적·전망이 한 축에 섞였다: {d['title']} {bases}"


# ═════════════════════════════════════════════════════════════════════════
# D10 — 초크포인트: 픽셀을 낳은 모든 경로가 certify 를 지난다
#   되살린 방법: `infographic_engine._emit` 첫 줄에서 `return str(path)` (certify 생략)
#   실측: pytest·precommit·재현렌더 전부 초록 — 2026-08-10 사고(8장이 통째로
#         provenance 없이 발행)가 그대로 재발 가능한 상태였다.
# ═════════════════════════════════════════════════════════════════════════
_SMOKE_DS = [{"title": "t", "unit": "억원",
              "data": [{"label": "A", "value": 10.0}, {"label": "B", "value": 20.0}]}]


def test_d10_emit_은_검증을_통과하지_못한_이미지를_버린다(tmp_path):
    from JARVIS06_IMAGE import infographic_engine as ie
    p = tmp_path / "fake.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    out = ie._emit(p, engine="test", datasets=_SMOKE_DS,
                   html="<div><span>777777.7</span><span>888888.8</span>"
                        "<span>999999.9</span></div>")
    assert out == "", "근거 없는 수치가 박힌 이미지가 초크포인트를 통과했다"
    prov = idv.lookup_provenance(p)
    assert prov is not None, "초크포인트가 provenance 를 등록하지 않았다"
    assert prov.get("verified") is not True, prov


def test_d10_emit_은_검증을_통과한_이미지를_등록하고_돌려준다(tmp_path):
    from JARVIS06_IMAGE import infographic_engine as ie
    p = tmp_path / "ok.jpg"
    p.write_bytes(b"\xff\xd8\xff")
    out = ie._emit(p, engine="test", datasets=_SMOKE_DS,
                   html="<div><span>10</span><span>20</span></div>")
    assert out == str(p)
    prov = idv.lookup_provenance(p)
    assert prov and prov.get("verified") is True, prov


def test_d10_초크포인트_별칭이_같은_함수를_가리킨다():
    from JARVIS06_IMAGE import infographic_engine as ie
    assert ie.emit_certified is ie._emit, "공개 별칭이 사본이 됐다 — 문이 둘이 된다"


# ═════════════════════════════════════════════════════════════════════════
# D11 — 차트 프리미티브에 단위가 전달된다 (값 라벨 단위표기·자동 스케일)
#   되살린 방법: `_slot_chart_block` 의 `unit=unit` 3곳 제거 (시그니처 확장 미갱신 재현)
#   실측: 전부 초록. HTML 이 짧아졌을 뿐 — 단위 글자가 사라진 것을 아무도 몰랐다.
# ═════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("unit,rows", [
    # 막대·도넛·KPI 세 갈래를 모두 지난다 (호출부가 세 곳이라 한 곳만 고쳐지면 재발)
    ("억원", [{"label": "A", "value": 300}, {"label": "B", "value": 350},
              {"label": "C", "value": 900}]),                       # → bar_chart
    ("%", [{"label": "A", "value": 2.75}, {"label": "B", "value": 4.66}]),   # → bar_chart
    ("%", [{"label": "A", "value": 60}, {"label": "B", "value": 40}]),       # → donut
    ("억원", [{"label": "A", "value": 300}]),                        # → kpi_cards
])
def test_d11_차트_값라벨에_단위가_붙는다(unit, rows):
    ds = {"title": "t", "unit": unit, "data": rows}
    html = te._slot_chart_block(ds, _pick_palette(1), 1)
    assert html, "차트가 그려지지 않았다"
    fit = idv.chart_fit(ds)
    nodes = re.findall(r">([^<>]+)<", html)
    if fit == "kpi_cards":
        # KPI 카드는 값과 단위를 다른 노드로 나눠 그린다 (헤더 배지는 이 갈래에서 비어 있다)
        assert any(unit in n for n in nodes), \
            f"[{fit}] 값 라벨에 단위 {unit!r} 가 빠졌다 — 호출부가 unit 을 안 넘긴다"
    else:
        # ★ 헤더 우측 배지에도 단위가 찍히므로 `unit in html` 로는 무력하다.
        #   '수치와 단위가 *같은 표시 노드* 에 있는가' 를 본다.
        assert any(unit in n and re.search(r"\d", n) for n in nodes), \
            f"[{fit}] 값 라벨에 단위 {unit!r} 가 빠졌다 — 호출부가 unit 을 안 넘긴다: {nodes[:8]}"


# ═════════════════════════════════════════════════════════════════════════
# D12 — 히어로 KPI 조립부는 하나뿐 (형제 사본 금지)
#   되살린 방법: `pro_templates` 에 `_hero_stat` 을 조립하는 `kpi_band()` 신설
#   실측: 표시 리터럴('합계')이 있으면 precommit `image/display-literal` 이 잡았지만,
#         **리터럴 없는 사본은 전부 초록**이었다 — 이름이 다르면 형제사본 레그도 못 본다.
# ═════════════════════════════════════════════════════════════════════════
def _j06_callers_of(func_name: str) -> dict:
    """JARVIS06 안에서 `func_name()` 을 부르는 {파일: {함수…}} — 이름 목록을 박지 않는다."""
    out: dict = {}
    for f in sorted((ROOT / "JARVIS06_IMAGE").rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # 모듈 레벨 def 만 — 중첩 헬퍼는 그 def 의 *일부* 이지 별개 조립부가 아니다
        for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == func_name):
                    out.setdefault(str(f.relative_to(ROOT)), set()).add(fn.name)
    return out


def test_d12_히어로_KPI_조립부는_하나뿐이다():
    callers = _j06_callers_of("_hero_stat")
    assert callers, "히어로 프리미티브 호출을 하나도 찾지 못했다 — 검사 전제 붕괴"
    assert set(callers) == {"JARVIS06_IMAGE/template_engine.py"}, (
        f"히어로 KPI 조립이 2벌 이상이다: {callers} — 한쪽만 고쳐지면 재발한다")
    assert callers["JARVIS06_IMAGE/template_engine.py"] == {"_slot_hero_stats"}, callers


# ═════════════════════════════════════════════════════════════════════════
# D16 — '최고' 는 절댓값이 아니라 실제 최댓값
#   되살린 방법: 히어로 top 과 `view_rows` 정렬키를 `abs()` 기반으로 되돌림
#   실측: 전부 초록 (관측 8장이 전부 양수라 안 드러났을 뿐 — 음수가 섞이면 즉시 거짓).
# ═════════════════════════════════════════════════════════════════════════
def test_d16_음수가_섞여도_첫_히어로_카드는_실제_최댓값이다():
    ds = {"title": "고용 지표", "unit": "명",
          "data": [{"label": "취업자 증가", "value": 103000},
                   {"label": "실업자 감소", "value": -620000}]}
    html = te._slot_hero_stats([ds], _pick_palette(3))
    assert html, "히어로가 그려지지 않았다"
    assert "취업자 증가" in html and "실업자 감소" in html, html[:300]
    assert html.index("취업자 증가") < html.index("실업자 감소"), \
        "절댓값이 큰 음수를 첫 카드('최고' 자리)로 표기했다"


def test_d16_막대_정렬도_실제값_내림차순이다():
    ds = {"title": "t", "unit": "명",
          "data": [{"label": "P", "value": 10}, {"label": "N", "value": -99},
                   {"label": "M", "value": 5}]}
    assert [r["label"] for r in te.view_rows(ds)] == ["P", "M", "N"], \
        "절댓값 정렬이 살아 있다 — 꼴찌가 1위로 올라간다"


# ═════════════════════════════════════════════════════════════════════════
# D06 / D17 — 가산성 판정은 owner(image_data_verifier) 단독. 우회·어휘 리터럴 금지.
#   되살린 방법 A: `_slot_hero_stats` 안에서 `sum(pts)` 로 직접 합계 계산(owner 우회)
#   되살린 방법 B: `_tlabel = _tlabel or "항목 수"` (역할 어휘 리터럴 재도입)
#   실측: A·B 둘 다 pytest·precommit 초록. B 는 *변수 대입 한 단계* 를 거치면
#         `image/display-literal` 이 못 본다(직접 인자 대입은 잡는다).
# ═════════════════════════════════════════════════════════════════════════
def test_d06_가산이_막힌_데이터셋은_공표합계_이름표도_인쇄하지_않는다():
    ds = {"title": "금리 지표", "unit": "%",
          "totals": {"value": 6.25, "label": "합계"},
          "mixed_time": True,
          "data": [{"label": "기준금리", "value": 2.75, "as_of": "2026-08-07"},
                   {"label": "기준금리", "value": 3.5, "as_of": "2023-08-01"}]}
    assert idv.additive_total(ds)[0] is None, "가산성 판정이 이 데이터를 허용했다"
    html = te._slot_hero_stats([ds], _pick_palette(2))
    assert "합계" not in html, f"owner 가 막은 합계가 조립부에서 부활했다: {html[:400]}"
    assert "6.25" not in html and "6.3" not in html, html[:400]


def test_d06_조립부는_가산성_판정을_owner_에게_묻는다():
    fn = _fn_of(ROOT / "JARVIS06_IMAGE" / "template_engine.py", "_slot_hero_stats")
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "additive_total" in calls, \
        "히어로 조립부가 가산성을 owner 에게 묻지 않는다 (①단일 진입점 위반)"


def test_d17_역할어휘가_히어로_조립부_코드에_리터럴로_없다():
    """이미 폐기된 역할 어휘가 조립부 소스에 문자열 리터럴로 재등장하면 잡는 트립와이어."""
    fn = _fn_of(ROOT / "JARVIS06_IMAGE" / "template_engine.py", "_slot_hero_stats")
    doc = ast.get_docstring(fn, clean=False)
    lits = [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value != doc]                       # 설명(docstring)은 인쇄되지 않는다
    for banned in ("합계", "항목 수", "최고", "최저"):
        assert not any(banned in s for s in lits), \
            f"히어로 조립부에 역할 어휘 리터럴 {banned!r} 재등장 — 데이터에서 파생할 것"


# ═════════════════════════════════════════════════════════════════════════
# D19 — 레시피 고정 표시문구 게이트는 *꼴* 로 판정한다 (어휘 블랙리스트 금지)
#   되살린 방법: `design_learner._template_literals` 를 6개짜리 어휘 목록으로 되돌림
#   실측: 전부 초록. 기존 테스트는 precommit 내부 헬퍼(`_markup_display_text`)만 겨눠
#         **실제 소비자인 런타임 게이트가 죽어도 몰랐다** (4cf23ba 와 같은 함정).
# ═════════════════════════════════════════════════════════════════════════
_POISON_TEMPLATES = [
    "<div class='eyebrow'>핵심 지표 · KEY METRIC</div><h1>{{TITLE}}</h1>{{CHART_1}}",
    "<h1>{{TITLE}}</h1><div class='cap'>Chart 01</div>{{CHART_1}}",
    "<h1>{{TITLE}}</h1><div>기준 A</div><div>VS</div><div>기준 B</div>{{CHART_1}}",
]


@pytest.mark.parametrize("tmpl", _POISON_TEMPLATES)
def test_d19_레시피_게이트가_새_고정문구도_잡는다(tmpl):
    from JARVIS06_IMAGE import design_learner as dl
    assert dl._template_literals(tmpl), \
        f"고정 표시문구를 못 잡았다 (어휘 목록으로 퇴행): {tmpl[:60]}"


def _recipe_with(template: str) -> dict:
    """검증을 통과하는 실제 팔레트 + 오염 템플릿. 필수키는 owner 에서 파생(② 동적 설계)."""
    from JARVIS06_IMAGE import design_learner as dl
    from JARVIS06_IMAGE.pro_templates import PALETTES
    rec = dict(PALETTES[0])
    if not (isinstance(rec.get("hero"), list) and len(rec["hero"]) == 2):
        rec["hero"] = ["#101828", "#1d3557"]
    for k in dl._REQUIRED:
        if k in rec:
            continue
        rec[k] = ("regression" if k in ("id", "name")
                  else sorted(dl._TEXTURES)[0] if k == "hero_texture"
                  else 24 if k == "card_radius" else "#123456")
    rec["template"] = template
    return rec


def test_d19_검증기가_고정문구_레시피를_거부한다():
    from JARVIS06_IMAGE import design_learner as dl
    ok, why = dl._validate_recipe(_recipe_with(_POISON_TEMPLATES[0]), [])
    assert ok is False and "고정 표시문구" in why, (ok, why)


def test_d19_깨끗한_템플릿은_리터럴_사유로_거부되지_않는다():
    """오탐 방지 — 이 게이트가 *모든* 레시피를 떨어뜨리는 상수 False 가 되면 잡는다."""
    from JARVIS06_IMAGE import design_learner as dl
    _ok, why = dl._validate_recipe(
        _recipe_with("<h1>{{TITLE}}</h1><div>{{CHART_1}}</div>"), [])
    assert "고정 표시문구" not in why, why


def test_d19_슬롯토큰만_있는_템플릿은_통과한다():
    """오탐 방지 — 데이터로 채워지는 자리만 있으면 표시 리터럴이 아니다."""
    from JARVIS06_IMAGE import design_learner as dl
    assert dl._template_literals(
        "<style>.a{font-size:14px}</style><h1>{{TITLE}}</h1>"
        "<div>{{CHART_1}}</div><div>{{SOURCE}}</div>") == []


# ═════════════════════════════════════════════════════════════════════════
# D20 — 출처 문자열은 데이터에서만 파생. 리터럴 폴백·헤드라인 통과·주입구 금지.
#   되살린 방법: `source_label` 기본 인자에 '한국거래소 · Yahoo Finance' 리터럴 +
#                이름 정제 가드(`_human_source_name`) 우회
#   실측: 전부 초록 (마침 이 캐시의 provider 가 전부 해석돼 발현하지 않았을 뿐).
# ═════════════════════════════════════════════════════════════════════════
def test_d20_출처_폴백에_구체적_기관명_리터럴이_없다():
    default = str(inspect.signature(te.source_label).parameters["fallback"].default or "")
    hit = [n for n in SOURCE_NAMES.values() if n and n in default]
    assert not hit, f"출처 폴백에 거짓이 될 수 있는 기관명이 박혔다: {hit} ({default!r})"


def test_d20_뉴스_헤드라인은_출처로_인쇄되지_않는다():
    headline = "[Editorial] Linking growth to jobs — 성장과 일자리를 잇는 길에 대하여"
    ds = [{"title": "t", "unit": "명",
           "data": [{"label": "a", "value": 1,
                     "source": {"provider": "evidence:unknown_src_xyz",
                                "name": headline, "url": "https://news/x", "tier": 2}}]}]
    label = te.source_label(ds)
    assert "Editorial" not in label and headline not in label, \
        f"기사 헤드라인이 '데이터 출처' 로 인쇄됐다: {label!r}"


def test_d20_render_layout_은_출처_문자열_주입구를_갖지_않는다():
    assert "src" not in inspect.signature(te.render_layout).parameters, \
        "출처 문자열 주입 인자가 부활했다 — source_label 가드가 통째로 우회된다"
