"""JARVIS06_IMAGE/image_spec.py — 차트 행(row) 정규화 헬퍼 단일 진입점.

★ 이 모듈에는 더 이상 '설계서 생성'도 '렌더링'도 없다 (사용자 박제 2026-08-10).
  종전엔 `generate_image_spec()`(LLM 이 본문 텍스트에서 숫자를 추출해 spec 을 만든다)
  → `render_from_spec()`(html→plotly→matplotlib→svg 4단 폴백) 라는 **제2 렌더
  파이프라인**이 통째로 여기 있었다. 살아있는 호출자는 0곳이었는데도 남아 있었고,
  그 탓에 ① `svg_renderer._make_fallback_svg` 가 `pro_templates._bar_chart_diverging`
  와 같은 그림을 짓는 사본으로 자라 커밋을 막았고 ② `certify_image` 를 직접 부르는
  두 번째 레지스트리 기록 경로가 되어 초크포인트(`infographic_engine._emit`)를
  우회했다. **코드가 남아 있으면 다음 작업자의 손이 그리로 간다** — 그래서 지웠다.
  함께 삭제된 파일: `svg_renderer.py` · `plotly_renderer.py` (둘 다 소비자가 여기뿐).

  인포그래픽이 필요하면 초크포인트를 직접 부를 것:
      from JARVIS06_IMAGE.infographic_engine import generate_infographic

공개 API (렌더 직전 행 정규화 — 저장소 전역 단일 소유):
  is_time_only_label(label) -> bool
  row_time_key(row)         -> tuple | None
  enforce_time_axis_ltr(rows) -> list   # 시간축 좌→우 (JARVIS06/CLAUDE.md 규정0)
  dedupe_chart_rows(rows)     -> list   # 동일 수치·동일 항목 중복 행 제거
"""
from __future__ import annotations

import re

def _time_axis_key(label) -> tuple | None:
    """시간 라벨 파싱 → (year, month, day) 정렬 키. 시간 라벨 아니면 None.

    지원: '2026', '2026년', "'25년", '2026년 5월', '2026-05', '2026.05.12',
          '5월', '3분기', 'Q1', '5월 12일' 등 한국어·ISO 혼용.
    """
    import re as _re
    s = str(label or "").strip()
    if not s:
        return None
    year = month = day = quarter = None
    m = _re.search(r"\b((?:19|20)\d{2})\b", s)
    if m:
        year = int(m.group(1))
    else:
        m2 = _re.search(r"['’‘]?(\d{2})\s*년", s)
        if m2:
            year = 2000 + int(m2.group(1))
    mq = _re.search(r"([1-4])\s*분기", s) or _re.search(r"\bQ([1-4])\b", s, _re.I)
    if mq:
        quarter = int(mq.group(1))
    miso = _re.match(r"^\s*(?:19|20)\d{2}[-./](\d{1,2})(?:[-./](\d{1,2}))?", s)
    mm = _re.search(r"(\d{1,2})\s*월", s)
    if mm:
        month = int(mm.group(1))
    elif miso:
        month = int(miso.group(1))
        if miso.group(2):
            day = int(miso.group(2))
    md = _re.search(r"(\d{1,2})\s*일", s)
    if md:
        day = int(md.group(1))
    if quarter is not None and month is None:
        month = quarter * 3
    if year is None and month is None and day is None:
        return None
    if month is not None and not (1 <= month <= 12):
        return None
    return (year if year is not None else -1, month or 0, day or 0)


# ── 행의 '시점' 단일 파생 (★ 사용자 박제 2026-08-10 — 신규거짓 #1) ──────────
#   종전엔 시점을 *라벨 문자열* 에서만 재파싱했다. 1차 수정이 라벨에 as_of 를 심자
#   그 파서가 뒤집혔고(환율 3행이 전부 (2026,0,0) 로 읽혀 정렬이 무력), 반대로
#   '콜금리(1일)'·'통안증권 91일' 은 시간으로 오탐됐다.
#   진실(as_of)은 *행에 실려 있다*. 라벨은 표시 산출물이다 — 진실을 먼저 읽는다.
_TIME_TOKEN_RE = re.compile(
    r"(?:19|20)\d{2}[-./]\d{1,2}(?:[-./]\d{1,2})?"      # 2026-08-10 / 2026.08
    r"|(?:19|20)\d{2}\s*년?"                              # 2026년 / 2026
    r"|['’‘]\d{2}\s*년"                                 # '25년
    r"|\d{1,2}\s*분기|Q[1-4]"                             # 3분기 / Q1
    r"|\d{1,2}\s*[월일]",                                 # 5월 / 12일
    re.I)


def is_time_only_label(label) -> bool:
    """라벨 *전체* 가 시간 표기인가. '2026-08'·'3분기'·'2026년 5월' → True.

    ★ 어휘가 아니라 꼴 — '시간 토큰을 *포함* 하는가' 가 아니라 '시간 토큰을 지우면
      아무 내용도 남지 않는가' 를 묻는다. 종전 규칙(포함 여부)이 '콜금리(1일)' 을
      시계열 근거로 세어 8개 이종 금리를 꺾은선으로 이었다.
    """
    s = str(label or "").strip()
    if not s:
        return False
    rest = _TIME_TOKEN_RE.sub(" ", s)
    return not re.search(r"[0-9A-Za-z가-힣]", rest)


def row_time_key(row) -> tuple | None:
    """이 행의 시점 정렬 키. 시점을 알 수 없으면 None.

    우선순위: ① 행의 `as_of` (수집기가 실은 진실) ② 라벨 *전체* 가 시간일 때만 라벨.
    """
    if not isinstance(row, dict):
        return _time_axis_key(row) if is_time_only_label(row) else None
    ao = str(row.get("as_of") or "").strip()
    if ao:
        k = _time_axis_key(ao)
        if k is not None:
            return k
    lb = row.get("label")
    return _time_axis_key(lb) if is_time_only_label(lb) else None


def enforce_time_axis_ltr(rows: list) -> list:
    """★ 시간축 좌→우 강제 (사용자 박제 2026-07-03): "이미지에서 시간 흐름은 항상
    좌→우 — 25년이 좌, 26년이 우."

    ★ 정렬 키는 `row_time_key` — as_of 우선 (사용자 박제 2026-08-10).
      종전엔 라벨만 파싱했고, 라벨에 지표명이 섞이면(달러/원 환율 2026.08.07) 월·일이
      안 잡혀 세 행이 전부 같은 키가 되어 **정렬이 조용히 no-op** 이었다. 그 결과
      [08.07, 08.10, 06] 순서 그대로 렌더돼 실제 -8.7% 하락이 +8.6% 상승으로 인쇄됐다.

    정책 (카테고리 차트 오폭 방지):
      - 시점이 파악된 행이 80% 미만이거나 키가 1종이면 무변경.
      - 연도 정보가 있으면 오름차순 안정 정렬.
      - 연도 없는 키(월·분기만)는 *엄격 내림차순일 때만* 역순 (연말→연초 랩 보존).
    """
    if not rows or len(rows) < 2:
        return rows
    keys = [row_time_key(r) for r in rows]
    parsed = [k for k in keys if k is not None]
    if len(parsed) < max(2, int(len(rows) * 0.8)) or len(set(parsed)) < 2:
        return rows
    has_year = all(k[0] != -1 for k in parsed)
    full = [k if k is not None else (9999, 99, 99) for k in keys]  # 미파싱은 뒤로
    if has_year:
        if full != sorted(full):
            order = sorted(range(len(rows)), key=lambda i: full[i])
            return [rows[i] for i in order]
        return rows
    # 연도 없음 — 엄격 내림차순일 때만 역순
    if all(full[i] > full[i + 1] for i in range(len(full) - 1)):
        return list(reversed(rows))
    return rows


def dedupe_chart_rows(rows: list) -> list:
    """★ 차트 내 동일 수치 중복 제거 (사용자 박제 2026-07-03).

    "차트 이미지 속에 같은 수치값이 여러 번 중복으로 나오는 문제" —
      ① 정규화 라벨이 같은 행 반복 → 첫 행만 유지
      ② 값이 같고 라벨 토큰이 60%+ 겹치는 행 (예: '매출'/'매출액' = 같은 값) → 첫 행만
    단, *시계열* 라벨(연·월·분기)이 과반이면 무변경 — 평평한 시계열(기준금리
    2.5% 6개월 연속 등)의 동일값은 정당한 데이터다.
    """
    if not rows or len(rows) < 2:
        return rows
    import re as _re_d
    # 시계열이면 dedupe 대상 아님 — 평평한 시계열의 동일값은 정당한 데이터다.
    # ★ 판정은 `image_data_verifier.is_timeseries` 단독 (사용자 박제 2026-08-10).
    #   종전엔 여기에 '시간 라벨이 과반' 이라는 *세 번째* 라벨 규칙이 박혀 있었다.
    #   ①위반이기도 하지만 더 나쁜 것은, J09 가 모든 행에 as_of 를 싣기 시작하면
    #   '시간 라벨 과반' 이 카테고리 데이터에서도 참이 되어 dedupe 가 통째로 무력화된다.
    try:
        from JARVIS06_IMAGE.validators.image_data_verifier import is_timeseries as _is_ts
        if _is_ts({"data": rows}):
            return rows
    except Exception:
        pass

    def _norm(s: str) -> str:
        return _re_d.sub(r"[^가-힣a-z0-9]", "", str(s or "").lower())

    def _toks(s: str) -> set:
        # 한국어는 2자+, 라틴/숫자는 1자도 토큰 — '시나리오 A/B'·'Day 1/2' 의
        # 단일문자 판별자를 버리면 다른 항목을 같은 항목으로 오판 (ERRORS [312])
        return set(_re_d.findall(r"[가-힣]{2,}|[a-z0-9]+", str(s or "").lower()))

    kept: list = []
    for r in rows:
        if not isinstance(r, dict):
            kept.append(r)
            continue
        lb, val = _norm(r.get("label")), r.get("value")
        dup = False
        for k in kept:
            if not isinstance(k, dict):
                continue
            k_lb = _norm(k.get("label"))
            if lb and lb == k_lb:
                dup = True   # 동일 라벨 반복
                break
            try:
                same_val = abs(float(val) - float(k.get("value"))) < 1e-9
            except (TypeError, ValueError):
                same_val = False
            if same_val:
                t1, t2 = _toks(r.get("label")), _toks(k.get("label"))
                if t1 and t2:
                    # 접두 포함 매칭 ('매출'↔'매출액' 같은 접미 변형을 같은 항목으로)
                    # 단, 접두 규칙은 2자+ 토큰만 — '1'↔'10' 오병합 방지, 1자는 정확 일치만
                    _m = sum(1 for a in t1
                             if any(a == b or (len(a) > 1 and len(b) > 1
                                               and (a.startswith(b) or b.startswith(a)))
                                    for b in t2))
                    if _m / max(len(t1), len(t2)) >= 0.6:
                        dup = True   # 같은 값 + 사실상 같은 항목
                        break
        if not dup:
            kept.append(r)
    if len(kept) < len(rows):
        print(f"  🧹 [dedupe] 차트 동일 수치 중복 {len(rows) - len(kept)}행 제거")
    return kept



__all__ = [
    "is_time_only_label",
    "row_time_key",
    "enforce_time_axis_ltr",
    "dedupe_chart_rows",
]
