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

진입점: render_layout(template, title, subtitle, datasets, recipe, chip) -> html
        verify_layout_output(html, datasets) -> bool   (데이터 안전 게이트)
"""
from __future__ import annotations

import logging
import re

from JARVIS06_IMAGE.pro_templates import (
    _pairs, _is_timeseries, _pct_change, _sparkline, _fmt, _auto_scale, _num,
    _hero_stat, _line_chart, _bar_chart, _donut, _mini_card, _kpi_cards,
    BAR_MAX_ROWS, DONUT_MAX_ROWS, KPI_MAX_CARDS,
)
# ★ 사실성 판정은 owner 단독 (CLAUDE.md 규정13) — 여기서 *물어보기만* 한다.
from JARVIS06_IMAGE.validators.image_data_verifier import (
    chart_fit, additive_total, row_provenance,
)

log = logging.getLogger("jarvis")

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

# ── 출처 표기 (★ 사용자 박제 2026-08-10 — D20 / ②동적 설계 2026-08-10) ──────
#   ① 어휘 목록을 만들지 않는다 — *꼴* 로만 판정한다.
#      종전 가드는 `len(name) <= 20` 하나뿐이라 내부 식별자 `naver_news`(10자)와
#      코드가 조립한 제목 `market 시장 데이터`(13자)가 그대로 '데이터 출처:' 로 인쇄됐다.
#   ② provider 목록을 손으로 베끼지 않는다.
#      종전엔 여기 17줄짜리 `_PROVIDER_LABEL` 리터럴 매핑이 있었다. 그 목록의 주인은
#      JARVIS09 `source_registry` 다(`SOURCE_TRUST_TIER` 가 그 파생 뷰). 사본을 두면
#      J09 가 소스를 늘리거나 지워도 여기는 옛 목록을 가리킨 채 남는다 —
#      '복사본을 진실로 믿지 말 것'(CLAUDE.md)이 정확히 이 꼴이다.
#      이제 J09 레지스트리를 *런타임 조회* 해 이름을 파생한다.
_INTERNAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")   # naver_news · draft_slot 같은 내부 id
#   '민낯 소문자 라틴 토큰' = 코드 어휘의 꼴 (market·yfinance·draft_slot).
#   대문자로 시작하는 고유명사('Yahoo Finance')는 걸리지 않는다 — 종전 `[a-z]{2,}` 는
#   'Yahoo Finance · 주가 이력' 같은 정당한 출처명까지 잘라냈다.
_BARE_LOWER_RE = re.compile(r"(?<![A-Za-z])[a-z]{2,}(?![A-Za-z])")
#   ③ 꼴 게이트를 통과 못한 이름은 *버리기 전에 머리만 취해* 다시 본다
#      (★ 사용자 박제 2026-08-10 3차). 종전엔 한 번 보고 끝이라 J09 레지스트리 18키 중
#      **10키**가 빈 라벨로 떨어졌고, 그 자리를 '데이터 출처: 공개 통계' 가 메웠다
#      (실측 558 dataset 중 57건). 떨어진 이유는 전부 *꼴* 이었지 정당성이 아니었다 —
#        · '글로벌 시장지표(yfinance)'          → 괄호 안 소문자 토큰
#        · 'Google News + 경제지(한국경제·매경·연합)' → 나열이라 길다
#        · 'Yahoo Finance · 주가 이력'          → 부제가 붙어 길다
#      (앞 둘은 J09 가 `name` 필드를 신설해 근본 해소됐다. 아래 줄이기는 dataset 이
#       실어 오는 `source.name` — 'Yahoo Finance · 주가 이력' 같은 부제 붙은 이름 — 몫이다.)
#      어휘 목록을 더하면(②원칙 위반) 새 출처가 생길 때마다 또 샌다. 대신 *꼴로 줄인다*:
#        ① 원문 → ② 나열/설명 구분자 앞 머리 → ③ 거기서 꼬리 괄호 제거
#      각 단계마다 같은 게이트로 다시 판정하고, 처음 통과한 것을 쓴다.
#   ※ 구분자에 ASCII 하이픈(' - ')은 **넣지 않는다** — 뉴스 제목의 바이라인 구분자가
#     바로 그 꼴이라('… 전망" - 조세금융신문'), 넣는 순간 헤드라인 조각이 출처로 인쇄된다.
_NAME_HEAD_SEP_RE = re.compile(r"\s[—–·|+/]\s")
_TAIL_PAREN_RE = re.compile(r"\s*[(\[（][^()\[\]（）]*[)\]）]\s*$")


def _name_gate(nm: str) -> bool:
    """이 문자열을 출처로 인쇄해도 되는가 — *꼴* 판정 단독 (어휘 목록 없음)."""
    if not nm or len(nm) > 20:
        return False                    # 장문 = 뉴스 헤드라인·보도자료 제목
    if _INTERNAL_ID_RE.match(nm):
        return False                    # 내부 식별자 (naver_news·draft_slot)
    if _BARE_LOWER_RE.search(nm):
        return False                    # 'market 시장 데이터' — 코드가 조립한 제목
    return True


def _name_forms(nm: str):
    """출처명 후보를 *꼴로 줄여가며* 흘린다 — 원문 → 머리(반복) → 각 머리의 꼬리괄호 제거.

    ★ 순서가 규칙이다: *머리 취하기가 괄호 벗기기보다 먼저* 다. 뒤집으면
      '네이버 블로그 — 체감·후기(보조, 신뢰도 낮음)' 이 괄호만 벗고 길이 게이트를 통과해
      '네이버 블로그 — 체감·후기' 라는 설명문이 출처로 인쇄된다(실측 회귀).
    """
    seen: set = set()
    cand, forms = nm, [nm]
    for _ in range(3):                  # 나열이 여러 겹이어도 몇 단계면 바닥
        head = _NAME_HEAD_SEP_RE.split(cand, 1)[0].strip()
        if not head or head == cand:
            break
        forms.append(head)
        forms.append(_TAIL_PAREN_RE.sub("", head).strip())
        cand = head
    forms.append(_TAIL_PAREN_RE.sub("", nm).strip())
    for form in forms:
        if form and form not in seen:
            seen.add(form)
            yield form


def _human_source_name(nm) -> str:
    """사람에게 보여도 되는 출처명. 어느 꼴로도 통과 못하면 "" (표기하지 않는다).

    거짓 출처 < 출처 없음 — 못 읽으면 이름을 지어내지 않고 비운다.
    """
    nm = str(nm or "").strip()
    if not nm:
        return ""
    for form in _name_forms(nm):
        if _name_gate(form):
            return form                 # 'KOSIS 국가통계포털'·'Yahoo Finance' 는 통과
    return ""


def provider_labels() -> dict:
    """provider key → 사람이 읽는 출처명. ★ JARVIS09 출처 레지스트리에서 *파생*.

    키 집합의 주인은 `SOURCE_TRUST_TIER`(= `SOURCES` 의 파생 뷰), 표기 문자열의 주인은
    각 소스의 **`name` 필드** 다. J09 에 소스가 늘면 자동으로 따라온다.
    · ★ 카탈로그 문자열을 잘라 이름인 척 쓰지 않는다 (사용자 박제 2026-08-10 3차):
      종전엔 `catalog`("표시명 — 설명")의 머리말을 여기서 잘랐는데, 그 자르기 규칙이
      곧 J09 표시명의 *사본* 이었다. 사본이라 J09 가 '글로벌 시장지표(yfinance)' 처럼
      쓰는 순간 꼴 게이트에 걸려 탈락했고(18키 중 10키 탈락 → 이미지 푸터가
      '데이터 출처: 공개 통계' 로 열화, 실측 558 dataset 중 57건), 카탈로그가 없는
      7키는 아예 이름이 없었다. 이제 이름은 09 에서 *받아온다*.
    · 받은 이름도 사람이 읽을 꼴이 아니면(장문·소문자 식별자 포함) *표기하지 않는다* —
      없는 이름을 지어내느니 출처 줄을 비우는 편이 낫다(거짓 출처 < 출처 없음).
    · 레지스트리를 못 읽으면 빈 표. 그때도 거짓말은 만들지 않는다(호출자가 dataset 이
      들고 온 `source.name` 으로 내려간다).
    · 결과를 캐시하지 않는다 — 캐시는 곧 사본이고, 사본은 원본이 바뀌어도 안 바뀐다.
      (18개 스펙 순회 + 정규식 1회. 이미지 1장당 몇 번 도는 비용이라 캐시할 값이 아니다.)
    """
    try:
        from JARVIS09_COLLECTOR.source_registry import SOURCES, SOURCE_TRUST_TIER
    except Exception as e:                       # J09 미가용 — 이름을 지어내지 않는다
        log.warning(f"[template_engine] 출처 레지스트리 조회 실패 — 출처 표기 생략: {e}")
        return {}
    known = set(SOURCE_TRUST_TIER)               # 목록의 주인은 J09
    out: dict = {}
    for spec in SOURCES:
        key = str(getattr(spec, "key", "")).strip().lower()
        if not key or key not in known:
            continue
        nm = _human_source_name(getattr(spec, "name", "") or "")
        if nm:
            out[key] = nm
    return out


def _provider_label(prov) -> str:
    """provider 문자열 → 표기. 레지스트리에 없는 내부 토큰은 표기하지 않는다("")."""
    p = str(prov or "").strip()
    if not p:
        return ""
    labels = provider_labels()
    pl = p.lower()
    disp = labels.get(pl)
    if disp:
        return disp
    if ":" in pl:                        # 'evidence:naver_news' / 'news:매일경제'
        head, _, tail = pl.partition(":")
        disp = labels.get(tail) or labels.get(head)
        if disp:
            return disp
        return _human_source_name(p.split(":", 1)[1])
    return ""


def source_label(datasets: list, fallback: str = "") -> str:
    """datasets → '데이터 출처: …'. ★ 출처 문자열의 *유일한* 생산자.

    ★ 우회 폐쇄 (사용자 박제 2026-08-10 — D20): 종전엔 `render_layout(src=...)` 로
      호출자가 문자열을 직접 넣을 수 있어 이 함수의 가드가 통째로 무력화됐다
      (slot_renderer 가 J09 원본 source.name = 뉴스 헤드라인을 그대로 넣고 있었다).
      이제 `src` 인자 자체를 없앴다 — 출처는 데이터에서만 파생된다.
    행별 출처(source_mix)가 있으면 그것을, 없으면 dataset 대표 출처를 쓴다.
    """
    seen: list[str] = []
    for ds in datasets or []:
        for sc in (row_provenance(ds)["sources"] or [{}]):
            disp = _provider_label(sc.get("provider"))
            if not disp:
                disp = _human_source_name(sc.get("name"))
            if disp and disp not in seen:
                seen.append(disp)
    if not seen:
        return fallback or "데이터 출처: 공개 통계"
    return "데이터 출처: " + " · ".join(seen[:3])


#   as_of 는 J09 가 실어 보내는 대로 온다 — ISO('2026-07-31')일 수도 한국어('2026년 7월')일
#   수도 있다. *꼴을 가정하지 말고* 연·월을 뽑는다.
_AS_OF_YM_RE = re.compile(r"(\d{4})\D{0,2}(\d{1,2})")


def _as_of_text(a) -> str:
    """as_of → 'YYYY.MM'. 없거나 읽을 수 없으면 "".

    ★ 자르지 않는다 (사용자 박제 2026-08-10): 종전엔 `str(a)[:7].replace("-", ".")` 로
      앞 7글자를 잘랐다. J09 가 '2026년 7월' 을 실어 보내면 **'2026년 7'** 이라는 세상에
      없는 표기가 배지에 찍히고, 그 조각 숫자(2026·7)가 표시 수치 grounding 에서
      '근거 없는 수' 로 잡혀 *이미지가 통째로 폐기*됐다 (실측: '한국은행 기준금리 추이' 1장).
      입력 꼴을 가정한 슬라이싱은 이렇게 조용히 두 번 해를 끼친다 — 거짓 표기 + 오폐기.
    ★ 0 패딩('2026.07')은 표기 통일이자 정렬키다 — 사전순 = 시간순이라 별도 파싱이 필요 없다.
    """
    m = _AS_OF_YM_RE.search(str(a or ""))
    if not m:
        return ""
    mm = int(m.group(2))
    if not 1 <= mm <= 12:
        return ""
    return f"{m.group(1)}.{mm:02d}"


def _eyebrow_from_data(datasets: list) -> str:
    """아이브로우 배지 = 데이터 기준시점에서 파생.

    ★ 단일 시점 주장 금지 (사용자 박제 2026-08-10 — D01/D09): 종전엔 대표 fact 1건의
      as_of 를 '2026.08 기준' 으로 박아, 2023-08·2025-05 값이 섞인 금리 차트까지
      2026.08 로 라벨링했다. 시점이 섞였으면 *구간* 으로 표기한다.
    """
    stamps = []
    for ds in datasets or []:
        r = row_provenance(ds)["as_of_range"]
        for key in ("min", "max"):
            t = _as_of_text(r.get(key))
            if t:
                stamps.append(t)
    if not stamps:
        return "실데이터"
    lo, hi = min(stamps), max(stamps)
    return f"{lo} 기준" if lo == hi else f"{lo}~{hi}"


# ── 레이아웃 템플릿 표시 리터럴 검출 (★ 사용자 박제 2026-08-10 — D19) ──────
#   종전 게이트는 6개짜리 *어휘 블랙리스트*(`_BAD_PHRASES`)였다. 그래서 학습·시드 레시피에
#   박힌 '핵심 지표 · KEY METRIC' · 'Chart 01' · '기준 A/VS/기준 B' 가 전량 통과했고,
#   2026-08-10 경제 slot4 이미지에 실제로 인쇄됐다.
#   JARVIS08 tags.py 가 남긴 교훈 그대로 — **검증은 어휘가 아니라 꼴**이다.
#   판정: 슬롯 토큰을 지우고 남은 '>텍스트<' 노드에 글자(한글/라틴)가 있으면 표시 리터럴.
#   (숫자·구두점·불릿 '●' 같은 장식은 데이터를 주장하지 않으므로 허용)
_SLOT_TOKEN_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")
_NONDISPLAY_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>|<!--.*?-->", re.S | re.I)


def template_literals(html: str) -> list[str]:
    """레이아웃 템플릿에 박힌 표시 텍스트 리터럴 목록 (슬롯 토큰 외). 없으면 []."""
    if not html:
        return []
    scan = _NONDISPLAY_RE.sub(" ", str(html))
    out: list[str] = []
    for m in re.finditer(r">([^<>]+)<", scan):
        t = _SLOT_TOKEN_RE.sub(" ", m.group(1)).strip()
        if t and re.search(r"[가-힣A-Za-z]", t):
            out.append(t)
    return out


def _keep_last_slot(tmpl: str, slot: str) -> str:
    """★ 크롬 중복 차단 (사용자 박제 2026-07-19): 템플릿에 {{BRAND}}/{{SOURCE}} 가 2회+ 있으면
    마지막(footer) 1개만 남기고 앞의 것은 제거. 라이브러리·나이틀리 학습·미래 템플릿 *전부* 에
    렌더 시점 강제 → 어떤 템플릿이 와도 브랜딩·출처는 1회. (개별 템플릿 수정 불필요)"""
    parts = tmpl.split(slot)
    if len(parts) <= 2:                      # 0~1회 → 그대로
        return tmpl
    return "".join(parts[:-1]) + slot + parts[-1]


# ── 표시 뷰 단일 소유 (★ 사용자 박제 2026-08-10 — D04/D07) ──────────────────
#   종전엔 히어로가 전량(8행), 막대가 rows[:7] 을 각자 보고 그려서 같은 이미지 안에
#   '항목 수 8개 / 합계 27.2%' vs 막대 7개(합 24.7) 로 검산이 깨졌다. 화면에 없는 값이
#   합계에 들어간 것이다. 이제 히어로·차트·검증이 *같은 뷰* 를 본다.
def view_rows(ds: dict) -> list[dict]:
    """이 dataset 이 *실제로 화면에 그려지는* 행 (렌더 순서·절단 반영).

    절단 상한은 pro_templates(차트 프리미티브 owner)의 상수를 *조회* 한다 — 사본 금지.
    """
    if not isinstance(ds, dict):
        return []
    rows = [r for r in (ds.get("data") or [])
            if isinstance(r, dict) and _num(r.get("value")) is not None]
    fit = chart_fit(ds, rows=rows)
    if fit == "none":
        return []
    if fit == "line_chart":
        return rows                                  # 시계열은 시간 순서 보존·전량
    if fit == "kpi_cards":
        return rows[:KPI_MAX_CARDS]
    if fit == "donut":
        return rows[:DONUT_MAX_ROWS]                 # 도넛은 원 순서 유지(비중 표현)
    # 막대 랭킹 — ★ 실제값 desc (절댓값 아님. ROE 음수=꼴찌 — pro_templates 주석 규칙 채택)
    rows = sorted(rows, key=lambda r: -_num(r.get("value")))
    return rows[:BAR_MAX_ROWS]


def rendered_view(datasets: list) -> list[dict]:
    """render_layout 이 실제로 그린 전체 행 = concat(view_rows(d) for d in (ts+cats)[:3]).
    ★ 초크포인트(_emit → certify_image)의 rendered_rows 인자가 이것."""
    ts = [d for d in (datasets or []) if _is_timeseries(d)]
    cats = [d for d in (datasets or []) if not _is_timeseries(d)]
    out: list[dict] = []
    for d in (ts + cats)[:3]:
        _u = d.get("unit", "")
        # 단위를 행에 실어 보낸다 — 여러 dataset 이 섞이면 '첫 dataset 단위' 추측이 틀린다.
        # (이 값이 provenance["values"] → prepublish_gate._crosscheck_leg 의 대조 재료가 된다)
        out.extend({**r, "unit": r.get("unit") or _u} for r in view_rows(d))
    return out


def _pts_of(rows) -> list[tuple[str, float]]:
    out = []
    for r in rows or []:
        v = _num(r.get("value"))
        if v is not None:
            out.append((str(r.get("label", "")), v))
    return out


# ── 슬롯 콘텐츠 생성 (실데이터 → HTML, pro_templates 빌더 재사용) ─────────────
def _slot_hero_stats(datasets, pal) -> str:
    """히어로 KPI — ★ 조립 단일 구현 (사용자 박제 2026-08-10 — D03/D06/D12/D16/D17).

    종전 결함 3가지를 한꺼번에 없앤다.
      ① 무조건 합산 '합계' — 단위·의미·가산성을 보지 않고 더해 '합계 27.2%'(금리 8종),
         '합계 4,368원'(환율 3시점), '합계 341,000명'(실적+전망) 을 인쇄했다.
         → `additive_total()` 이 값을 돌려줄 때(=출처가 공표한 합계)만 합계 카드를 낸다.
      ② '항목 수' 가 절단 전 전량 — 이제 `view_rows` 공유라 막대 개수와 정의상 일치.
      ③ '최고' 정렬키가 절댓값 desc — 음수가 섞이면 꼴찌를 최고로 표기했다. 실제값 desc 로 통일.
      ④ 역할 어휘('최고'·'최저'·'합계')가 코드 리터럴이었다 — 카드 라벨은 *항목명* 으로
         파생하고, 합계 카드는 출처가 붙인 이름이 있을 때만 낸다(②동적 설계).
    """
    ts = [d for d in datasets if _is_timeseries(d)]
    cats = [d for d in datasets if not _is_timeseries(d)]
    blocks = []
    if ts:
        cols = [(pal["a1"], pal["a1s"]), (pal["a2"], pal["a2s"])]
        for i, d in enumerate(ts[:2]):
            pts = _pts_of(view_rows(d))
            if not pts:
                continue
            _u = d.get("unit", "")
            chg = _pct_change(pts)
            _lv, _lu = _auto_scale(pts[-1][1], _u)
            _fv, _fu = _auto_scale(pts[0][1], _u)
            big = (f"{'+' if chg >= 0 else ''}{chg:.1f}<span style='font-size:34px'>%</span>"
                   f"<span style='font-size:30px'> {'▲' if chg >= 0 else '▼'}</span>"
                   ) if chg is not None else f"{_fmt(_lv)}<span style='font-size:30px'> {_lu}</span>"
            sub = f"{pts[-1][0]} {_fmt(_lv)}{_lu} · {pts[0][0]} {_fmt(_fv)}{_fu}"
            c, cs = cols[i % 2]
            blocks.append(_hero_stat(pal, d.get("title", ""), big, sub, c, cs, _sparkline(pts, cs)))
    elif cats:
        d = cats[0]
        _unit = d.get("unit", "")
        _ttl = str(d.get("title", "")).strip()
        view = view_rows(d)
        pts = _pts_of(view)
        if pts:
            def _card(label, value, color, colors):
                _v, _u = _auto_scale(value, _unit)
                return _hero_stat(pal, str(label),
                                  f"{_fmt(_v)}<span style='font-size:30px'> {_u}</span>",
                                  _ttl, color, colors)
            # ★ 카드 라벨은 *항목명* — 역할 어휘('최고'·'최저'·'합계')를 코드에 두지 않는다
            #   (사용자 박제 2026-08-10 — ②동적 설계 / precommit image/display-literal).
            #   순위는 카드 순서와 아래 랭킹 차트가 말한다. 지어낸 어휘 없이도 읽힌다.
            top = max(pts, key=lambda kv: kv[1])          # 실제 최댓값 (절댓값 아님)
            blocks.append(_card(top[0], top[1], pal["a1"], pal["a1s"]))
            if len(pts) > 1:
                total, _why = additive_total(d, rows=view)
                # 합계는 *출처가 공표한 합계* 일 때만. 그 이름도 출처가 붙인 것만 쓴다 —
                # 이름을 지어내야 한다면 그 카드는 애초에 낼 자격이 없다.
                _tot = d.get("totals") if isinstance(d.get("totals"), dict) else {}
                _tlabel = str(_tot.get("label") or "").strip()
                if total is not None and _tlabel:
                    blocks.append(_card(_tlabel, total, pal["a2"], pal["a2s"]))
                else:
                    low = min(pts, key=lambda kv: kv[1])
                    blocks.append(_card(low[0], low[1], pal["a2"], pal["a2s"]))
    # 각 히어로 블록 flex:1 → 폭을 균등 분배해 밴드 우측 여백 제거 (공간 채움·동적 설계)
    inner = "".join(f"<div style='flex:1;min-width:0'>{b}</div>" for b in blocks)
    return f"<div style='display:flex;gap:24px'>{inner}</div>" if blocks else ""


def _slot_chart_block(ds, pal, num) -> str:
    """한 데이터셋 → 완성 차트 블록(제목+SVG+범례).

    ★ 차트형은 `chart_fit()` 이 정한다 (사용자 박제 2026-08-10 — D13/D21).
      종전엔 여기 3분기(시계열/도넛/그 외 막대)가 전부라 *데이터 포인트 개수를 보는 조건이
      한 줄도 없었다*. 1행 dataset 도 막대로 떨어졌고, 1행 막대는 v/vmax 정규화 탓에
      트랙 100% 를 채워 정보량이 0이었다(slot2·5·7). 상류가 이미 판정해 실어 보낸
      `viz_hint="kpi_cards"` 도 버려지고 있었다.
    ★ `unit=` 전달 (D11): 종전 3개 호출부가 unit 을 안 넘겨 `_scale_rows_uniform` 이
      항상 no-op 이고 값 라벨에 단위가 빠졌다 — 시그니처 확장 시 미갱신된 호출부였다.
    """
    if ds is None:
        return ""
    view = view_rows(ds)
    pts = _pts_of(view)
    if not pts:
        return ""
    unit = ds.get("unit", "")
    title = ds.get("title", "")
    fit = chart_fit(ds, rows=view)
    right = ""
    if fit == "line_chart":
        inner, note = _line_chart([{"name": title, "pts": pts, "c": pal["a1"], "cs": pal["a1s"]}],
                                  pal, unit=unit)
        right = f"<span style='font-size:14px;color:{pal['muted']}'>{note}</span>"
    elif fit == "kpi_cards":
        inner = _kpi_cards(pts, pal, unit=unit)
    elif fit == "donut":
        donut, legend = _donut(pts, pal, unit=unit)
        inner = f"<div style='display:flex;align-items:center;gap:36px'>{donut}<div style='flex:1'>{legend}</div></div>"
    else:
        inner = _bar_chart(pts, pal, unit=unit)      # view_rows 가 이미 실제값 desc 정렬
        right = (f"<span style='font-size:15px;color:{pal['muted']};font-weight:700'>{unit}</span>"
                 if unit else "")
    if not inner:
        return ""
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
        pts = _pts_of(view_rows(d))          # ★ 화면에 그려지는 행과 같은 뷰
        if not pts:
            continue
        top = max(pts, key=lambda kv: kv[1])  # 실제 최댓값 (절댓값 아님 — 정렬키 통일)
        _v, _u = _auto_scale(top[1], d.get("unit", ""))
        cards.append(_mini_card(pal, "chart", pal["soft"], pal["ink"],
                                d.get("title", ""), _fmt(_v), _u))
    return f"<div style='display:flex;gap:20px'>{''.join(cards)}</div>" if cards else ""


# ── 렌더 ────────────────────────────────────────────────────────────────────
def _root_vars(pal) -> str:
    keys = ["ink", "a1", "a1s", "a2", "a2s", "soft", "muted", "eyebrow", "grid"]
    parts = [f"--hero0:{pal['hero'][0]};--hero1:{pal['hero'][1]};"]
    parts += [f"--{k}:{pal.get(k, '#888')};" for k in keys]
    return ":root{" + "".join(parts) + "}"


def render_layout(template: str, title: str, subtitle: str, datasets: list,
                  recipe: dict, chip: str = "") -> str:
    """레이아웃 템플릿 + 실데이터 → 완성 HTML (LLM 0). 색 변수 주입 + 슬롯 치환.

    ★ `src` 인자 폐지 (사용자 박제 2026-08-10 — D20): 종전 `src or source_label(datasets)`
      는 호출자가 문자열을 채워 보내면 출처 가드를 통째로 우회했다. 출처는 *데이터에서만*
      파생한다 — 우회로를 남기면 정책은 반드시 샌다(CLAUDE.md 실례 [474]와 동형).
    """
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
        "{{SOURCE}}": source_label(datasets),                   # provider 파생(헤드라인·거짓 출처 금지)
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
    """렌더 출력의 표시 수치가 실데이터·파생값에 grounding 되는지 (템플릿 내 하드코딩 수치 차단).

    ★ 판정 본체는 owner(validators/image_data_verifier)에 있다 — 여기는 얇은 어댑터.
      종전엔 판정이 infographic_engine._dg_verify_html 에 있어 규정13(사실성 로직 단일
      진입점)을 어겼고, 그 사본이 sum()·len() 을 허용 파생값으로 명시해 무의미한 합계를
      '근거 있음' 으로 통과시켰다.
    """
    from JARVIS06_IMAGE.validators.image_data_verifier import verify_rendered_html
    ok, _bad = verify_rendered_html(html, datasets, rendered_rows=rendered_view(datasets))
    return ok


__all__ = ["render_layout", "verify_layout_output", "has_all_slots_resolved",
           "SLOT_SPEC", "source_label", "provider_labels", "view_rows", "rendered_view",
           "template_literals"]
