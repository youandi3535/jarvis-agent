"""shared/numeric.py — 수치 토큰의 *꼴* 단일 소유 (leaf, 의존 0).

★ 사용자 박제 2026-08-10 (①단일 진입점): 종전엔 `_NUM_TOKEN_RE` 라는 **같은 이름의
  다른 정규식** 이 두 곳에 있었다 —
    · `JARVIS09_COLLECTOR/evidence_pack.py` : 원문 대조용(한국어 자릿수 '19만8900' 처리)
    · `JARVIS06_IMAGE/validators/image_data_verifier.py` : 렌더 표시텍스트용(자릿수 미처리)
  이름이 같아 두 벌인 줄 아무도 모르는 채, J06 쪽만 '19만8900' 을 19 와 8900 두 수로 읽었다.
  J09 는 J06 을 import 할 수 없으므로(수집 단일 진입점 규정) 소유를 여기로 올린다.

두 소비 형태를 *하나의 정규식* 에서 파생한다 — 목적이 다른 것은 함수로 갈린다.
  · `corpus_numbers(text)`  — 원문에 어떤 수가 실재하는가 (관대: 후보를 넓게 흘린다)
  · `display_numbers(text)` — 화면에 인쇄된 '데이터 주장' 은 무엇인가 (엄격: 식별자 배제)
"""
from __future__ import annotations

import math
import re

# 수치 토큰 + 한국어 자릿수 표기. 세 꼴을 한 번에 잡는다:
#   ① '1,234'  ② '1.2천'(접미)  ③ '19만8900'(접미 + 뒤끝 — 한국어에 아주 흔한 복합 표기)
NUM_TOKEN_RE = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)(?:\s*([천만억조])\s?(\d[\d,]*(?:\.\d+)?)?)?")

SCALE_SUFFIX: dict[str, float] = {"천": 1e3, "만": 1e4, "억": 1e8, "조": 1e12}

# 날짜 토큰 — '2026.08', '2026-08-10'. 기준일 배지는 데이터 주장이 아니다.
#   ★ *달력 꼴* 로 좁힌다 (사용자 박제 2026-08-10): 종전 `\d{4}[.\-/]\d{1,2}` 는
#     '3254.67'·'1619.16' 같은 **네 자리 정수부 + 소수** 를 통째로 날짜로 먹어치웠다.
#     그 결과 조작 수치가 토큰화 단계에서 사라져 게이트가 검사할 대상 자체를 잃었다
#     (실측: 임의 수치 0~5000 중 약 76%가 '날짜' 로 지워진 뒤 무조건 통과).
#     연도는 19xx/20xx, 월은 1~12, 일은 1~31 — 달력이 아닌 것은 수치로 남긴다.
DATE_TOKEN_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[.\-/](?:0?[1-9]|1[0-2])(?:[.\-/](?:0?[1-9]|[12]\d|3[01]))?(?!\d)")

# 식별자 인접 — 숫자 바로 앞이 라틴/한글 글자면 이름의 일부다 ('S&P500'·'코스피200').
#   *뒤* 의 한글은 단위이므로 허용한다 ('1,542원'). 앞뒤 비대칭이 핵심.
_IDENT_PREFIX = r"(?<![0-9A-Za-z가-힣])"
_IDENT_SUFFIX = r"(?![0-9A-Za-z])"
DISPLAY_TOKEN_RE = re.compile(
    _IDENT_PREFIX + r"(-?\d[\d,]*(?:\.\d+)?)(?:\s*([천만억조])\s?(\d[\d,]*(?:\.\d+)?)?)?"
    + _IDENT_SUFFIX)


def safe_float(v) -> float | None:
    """값(문자열·숫자) → 유한 float. 쉼표·% 제거. 파싱 실패·NaN·Inf 는 전부 None.

    ★ 사용자 박제 2026-08-10 (①단일 진입점): `float("nan")`/`float("inf")` 는
      Python 이 유효한 파싱으로 받아준다 — `JARVIS06_IMAGE/pro_templates._num` ·
      `template_engine._num_of` · `validators/image_data_verifier._to_float` 세 곳이
      각자 이 함수를 복제하면서 셋 다 이 함정을 그대로 물려받았다(ERRORS `_fmt` NaN→int 크래시).
      값이 유한한지 판정하는 단 하나의 자리를 여기로 올린다 — 나머지는 위임만 한다.
    """
    try:
        f = float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def num_and_dp(tok: str) -> tuple[float, int] | None:
    """수치 토큰 → (값, 표시 소수자릿수). 쉼표 제거. 파싱 실패는 None."""
    t = (tok or "").replace(",", "").strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    try:
        return float(t), (len(t.split(".", 1)[1]) if "." in t else 0)
    except ValueError:
        return None


def corpus_numbers(text: str):
    """원문 텍스트 → (값, 표시자릿수, 부호가 원문에 명시됐는가) 후보 스트림.

    관대한 쪽 — '이 수가 문서에 실재하는가' 를 묻는 용도라 한 토큰에서 여러 후보를 흘린다.
    """
    for m in NUM_TOKEN_RE.finditer(text or ""):
        head = num_and_dp(m.group(1))
        if head is None:
            continue
        v, dp = head
        signed = m.group(1).lstrip().startswith("-")
        yield v, dp, signed
        mul = SCALE_SUFFIX.get(m.group(2) or "")
        if not mul:
            continue
        yield v * mul, 0, signed                      # '1.2천' → 1200
        tail = num_and_dp(m.group(3) or "")
        if tail is not None:
            yield v * mul + tail[0], tail[1], signed  # '19만8900' → 198900


def display_numbers(text: str) -> list[float]:
    """화면 표시 텍스트 → *데이터 주장* 수치 목록 (토큰당 정확히 0~1개).

    엄격한 쪽 — grounding 게이트의 입력이라 한 토큰이 여러 값을 시도하면 게이트가 헐거워진다.
      · 식별자 접두(라틴/한글)에 붙은 숫자는 이름의 일부 → 버린다.
      · 자릿수 접미 뒤에 *숫자가 이어질 때만* 결합한다 ('19만8900'→198900).
        뒤에 숫자가 없으면 그 글자는 자릿수가 아니라 **단위** 다 ('3.4 조원' → 3.4).
        (이 구분을 안 하면 억원 단위 차트의 '3.4 조원' 이 3.4e12 로 읽혀 오탐이 난다)
    """
    out: list[float] = []
    for m in DISPLAY_TOKEN_RE.finditer(DATE_TOKEN_RE.sub(" ", text or "")):
        head = num_and_dp(m.group(1))
        if head is None:
            continue
        mul = SCALE_SUFFIX.get(m.group(2) or "")
        tail = num_and_dp(m.group(3) or "")
        if mul and tail is not None:
            out.append(head[0] * mul + tail[0])
        else:
            out.append(head[0])
    return out


__all__ = ["NUM_TOKEN_RE", "DISPLAY_TOKEN_RE", "DATE_TOKEN_RE", "SCALE_SUFFIX",
           "safe_float", "num_and_dp", "corpus_numbers", "display_numbers"]
