"""JARVIS06_IMAGE/chart_generator.py
Plotly + Kaleido 기반 프리미엄 차트 생성기.

generate_chart(description, keyword, sector, context_text, out_dir, chart_idx,
               run_id="") → str
  : PNG 파일 절대 경로 반환. 실패 시 "".

run_id: 글 1건당 1회 생성되는 랜덤 문자열 (uuid4 등).
        색상 시드와 차트 타입 순서를 매 발행마다 다르게 만들어
        "항상 같은 디자인" 문제를 해결한다.
        비워두면 time.time_ns() 로 자동 생성.

스타일: 프리미엄 금융 대시보드 (화이트 배경, Plotly 기본 세련됨)
색상: keyword+sector+run_id+chart_idx 해시 기반 — 매 발행마다 다름
데이터: context_text 에서 숫자 추출 → 없으면 seeded 합성 데이터
차트 종류: run_id 로 셔플된 9종 중 description 우선 선택
"""
from __future__ import annotations
# ★ yfinance 단일 진입점 → JARVIS09 (2026-05-31 이관)
from JARVIS09_COLLECTOR.providers.economic_data_provider import (
    get_ticker_history as _j09_hist,
    download_ticker as _j09_dl,
)

import hashlib
import json
import re
from pathlib import Path
from datetime import datetime, timedelta

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


_TYPE_POOL = ['iso_bar', 'line', 'bar', 'barh', 'iso_area', 'pie', 'donut', 'area', 'combo', 'step', 'scatter', 'band_line']
# ★ 제12조 스타일 중복 금지: bar/barh/iso_bar 는 같은 "막대 계열" — 1개 run에 1종만 허용
_BAR_FAMILY = frozenset({'bar', 'barh', 'iso_bar'})

# run_id 별 이미 사용한 타입 추적 (스레드 안전 — 병렬 chart 생성 시 레이스 방지)
import threading as _threading
_used_types_by_run: dict[str, list[str]] = {}
_used_types_lock = _threading.Lock()

# ── 글 간 차트 타입 히스토리 (ERRORS [247]) ─────────────────────────────
# 같은 글 내 중복 방지(_used_types_by_run)는 기존. 여기서는 최근 N개 글에서
# 사용한 타입을 추적하여 여러 글에 걸쳐서도 다양한 타입이 사용되도록 함.
_GLOBAL_TYPE_HISTORY: list[str] = []   # 최근 사용 타입 (시간순)
_GLOBAL_TYPE_LOCK   = _threading.Lock()
_GLOBAL_HISTORY_MAX = 40               # 최근 40개 타입 기록 (약 4~5개 글 분량)

def _record_global_type(chart_type: str) -> None:
    """발행 완료된 차트 타입을 글로벌 히스토리에 기록."""
    with _GLOBAL_TYPE_LOCK:
        _GLOBAL_TYPE_HISTORY.append(chart_type)
        if len(_GLOBAL_TYPE_HISTORY) > _GLOBAL_HISTORY_MAX:
            del _GLOBAL_TYPE_HISTORY[0]

def _global_type_penalty(chart_type: str) -> float:
    """최근 글에서 많이 쓰인 타입일수록 높은 페널티 (0.0~1.0). 회피 가중치로 사용."""
    with _GLOBAL_TYPE_LOCK:
        recent = _GLOBAL_TYPE_HISTORY[-20:]  # 최근 20개만
    if not recent:
        return 0.0
    count = recent.count(chart_type)
    return min(count / 5.0, 1.0)  # 5번 이상이면 최대 페널티

# ── ECOS 지표 로테이션 — 같은 지표 반복 방지 ─────────────────────────────
# 최근 사용한 ECOS 지표명을 추적하여 순환 선택.
_USED_ECOS_INDICATORS: list[str] = []   # 최근 사용 ECOS 블록명 (시간순)
_USED_ECOS_LOCK = _threading.Lock()
_ECOS_INDICATOR_POOL = ["기준금리", "소비자물가", "원/달러", "수출금액", "실업률"]

def _record_ecos_used(indicator_name: str) -> None:
    with _USED_ECOS_LOCK:
        _USED_ECOS_INDICATORS.append(indicator_name)
        if len(_USED_ECOS_INDICATORS) > 20:
            del _USED_ECOS_INDICATORS[0]

def _get_ecos_exclude() -> list[str]:
    """최근 3개 글에서 사용한 ECOS 지표 반환 (제외 대상)."""
    with _USED_ECOS_LOCK:
        return list(_USED_ECOS_INDICATORS[-6:])

# ── JARVIS09 컨텍스트 캐시 — TTL 1시간 (ERRORS [247]) ──────────────────
# 같은 글 내 여러 차트 슬롯에서 중복 수집 방지 (유지).
# 단, 1시간 지나면 만료 → 다음 글에서 최신 데이터 재수집.
_J09_CTX_CACHE: dict[str, tuple[str, float]] = {}   # keyword → (text, timestamp)
_J09_CTX_CACHE_LOCK = _threading.Lock()
_J09_CTX_TTL = 3600.0   # 1시간

# ★ ERRORS [172] 2026-05-26: 런 내 파일 MD5 중복 감지 레지스트리
_run_file_hashes: dict[str, dict[str, str]] = {}  # run_id → {md5: fname}
_run_hash_lock = _threading.Lock()


def _register_chart_hash(run_id: str, fpath: str) -> str | None:
    """파일 MD5 등록. 이미 동일 hash 존재 시 기존 fname 반환 (중복 신호). 신규면 None."""
    try:
        with open(fpath, 'rb') as f:
            h = hashlib.md5(f.read()).hexdigest()
        with _run_hash_lock:
            reg = _run_file_hashes.setdefault(run_id, {})
            if h in reg:
                return reg[h]  # 중복 — 기존 파일명
            reg[h] = fpath
            # 100 runs 초과 시 가장 오래된 run 제거 (메모리 누수 방지)
            if len(_run_file_hashes) > 100:
                del _run_file_hashes[next(iter(_run_file_hashes))]
        return None  # 신규 — 정상
    except Exception:
        return None


def _shuffled_types(run_id: str) -> list[str]:
    import random
    seed = int(hashlib.md5(run_id.encode()).hexdigest()[:8], 16)
    pool = _TYPE_POOL[:]
    random.Random(seed).shuffle(pool)
    return pool


# ── 테마 상수 ────────────────────────────────────────────────────
_BG     = "#ffffff"
_PANEL  = "#fafafa"
_GRID   = "#e9ecef"
_TEXT_H = "#212529"
_TEXT_B = "#6c757d"
_FONT   = "Apple SD Gothic Neo, Noto Sans KR, NanumGothic, sans-serif"


def _fmt_bar_val(v: float) -> str:
    """bar/barh 값 레이블 포맷 — 단위 자동 변환.
    ≥10000: X.X조  |  ≥1000: X,XXX  |  ≥100: XXX  |  else: X.X"""
    if abs(v) >= 10000:
        return f"{v / 10000:.1f}조"
    elif abs(v) >= 1000:
        return f"{v:,.0f}"
    elif abs(v) >= 10:
        return f"{v:.1f}"
    else:
        return f"{v:.2f}"


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f'rgba({r},{g},{b},{alpha})'


def _derive_colors(keyword: str, sector: str, chart_idx: int, run_id: str = "") -> dict:
    """★ ERRORS [175] 2026-05-26: 채도 0.45~0.65 (기존 0.65~0.90) — 원색·네온 방지.
    밝기 0.80~0.93 으로 상향 — 어두운 차트 방지.
    극단 hue(보라/마젠타=0.75~0.90) 회피 → 은행권 배너 느낌 방지."""
    import colorsys
    seed_str = f"{keyword}|{sector}|{chart_idx}|{run_id}"
    h16 = hashlib.md5(seed_str.encode()).hexdigest()
    raw_hue  = (int(h16[:4], 16) / 0xFFFF + chart_idx * 0.11) % 1.0
    # ★ 인디고(0.65~0.72) · 보라(0.72~0.88) · 마젠타(0.88~0.97) 전부 회피
    # 허용 색상: 빨강(0~0.05) · 주황(0.05~0.14) · 초록(0.25~0.45) · 청록(0.45~0.55) · 파랑(0.55~0.65)
    _BAD_RANGES = [(0.65, 0.97)]

    def _remap_hue(h: float) -> float:
        hm = h % 1.0
        for lo, hi in _BAD_RANGES:
            if lo <= hm < hi:
                # bad range 를 좋은 영역(파랑 이하)으로 균등 리매핑
                t = (hm - lo) / (hi - lo)   # 0~1 내 위치
                return t * 0.63             # → 0~0.63 (빨강~파랑) 으로 매핑
        return hm

    base_hue = _remap_hue(raw_hue)
    sat      = 0.45 + (int(h16[4:6], 16) / 255) * 0.20   # 0.45~0.65 (기존 0.65~0.90)
    val_main = 0.80 + (int(h16[6:8], 16) / 255) * 0.13   # 0.80~0.93 (기존 0.72~0.90)

    def _hsvhex(h, s, v):
        r, g, b = colorsys.hsv_to_rgb(_remap_hue(h % 1.0), min(s, 1.0), min(v, 1.0))
        return f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"

    primary   = _hsvhex(base_hue,        sat,        val_main)
    secondary = _hsvhex(base_hue + 0.12, sat - 0.05, val_main - 0.04)
    tertiary  = _hsvhex(base_hue + 0.26, sat - 0.10, val_main)
    accent    = _hsvhex(base_hue + 0.50, sat - 0.05, val_main)
    # list5: 0.13 스텝 × 8 = 0~1.04 전체 색상환 — remap이 보라/마젠타 자동 회피
    list5     = [_hsvhex(base_hue + i * 0.13, max(0.42, sat - i * 0.02),
                          max(0.80, val_main - i * 0.02))
                 for i in range(8)]
    return {"primary": primary, "secondary": secondary,
            "tertiary": tertiary, "accent": accent, "list5": list5}


def _data_override_type(
    chart_type: str,
    labels: list,
    values: list,
    description: str,
    run_id: str,
) -> str:
    """labels/values 확정 후 데이터 특성 기반으로 chart_type 재조정.

    키워드 매칭이 놓치는 케이스를 데이터 실수치로 보정:
      ① 합산 ≈100% → donut (구성비 차트)
      ② 항목 2~3개 + 시계열 차트 → barh (추이선이 의미없음)
      ③ 음수 포함 + fill 계열 → combo/bar (fill area 깨짐 방지)
      ④ flat 데이터 + 시계열 + 비금리 → bar (변화없는 라인은 무의미)
      ⑤ 항목 수 ≥ 8 + barh → line (가로 막대가 너무 촘촘해짐)
    """
    if not labels or not values or len(values) < 2:
        return chart_type

    n = len(values)
    v_pos = [v for v in values if isinstance(v, (int, float)) and v > 0]
    v_sum = sum(v_pos)
    has_neg = any(isinstance(v, (int, float)) and v < 0 for v in values)
    v_mean = sum(values) / n if n else 1
    v_std = (sum((v - v_mean) ** 2 for v in values) / n) ** 0.5 if n else 0
    is_flat = v_mean != 0 and (v_std / abs(v_mean)) < 0.03
    is_timeseries = chart_type in ('line', 'area', 'step', 'iso_area', 'combo', 'band_line')
    is_fill = chart_type in ('area', 'iso_area', 'band_line')

    with _used_types_lock:
        used = list(_used_types_by_run.get(run_id, []))
        bar_used = any(t in _BAR_FAMILY for t in used)

    def _swap(new_type: str) -> str:
        with _used_types_lock:
            lst = _used_types_by_run.setdefault(run_id, [])
            try: lst.remove(chart_type)
            except ValueError: pass
            lst.append(new_type)
        print(f"    ⟳ [data_override] {chart_type.upper()} → {new_type.upper()} (데이터 기반 재조정)")
        return new_type

    _rate_kw = ('기준금리', '정책금리', '금리', '이자율')

    # ① 합산 ≈100% + 항목 3~8개 → donut (시계열 여부 무관, band_line 제외)
    if 90 <= v_sum <= 110 and 3 <= n <= 8 and chart_type not in ('donut', 'pie', 'band_line'):
        return _swap('donut' if 'donut' not in used else 'pie')

    # ② 항목 수 ≤ 3 + 시계열 + 비금리 → barh (추이선이 의미없음)
    if n <= 3 and is_timeseries and chart_type != 'band_line' and not any(k in description for k in _rate_kw):
        if not bar_used:
            return _swap('barh')
        elif 'donut' not in used:
            return _swap('donut')

    # ③ 음수 포함 + fill 계열 → combo
    if has_neg and is_fill:
        return _swap('combo' if 'combo' not in used else 'bar' if not bar_used else 'step')

    # ④ flat 데이터 + 일반 시계열 (금리 키워드 없음) → bar
    if is_flat and chart_type in ('iso_area', 'line', 'area') and not any(k in description for k in _rate_kw):
        if not bar_used:
            return _swap('bar')

    # ⑤ 항목 수 ≥ 8 + barh → line (막대 너무 촘촘)
    if n >= 8 and chart_type == 'barh':
        return _swap('line' if 'line' not in used else 'iso_area')

    return chart_type


def _detect_type(description: str, chart_idx: int = 1, run_id: str = "") -> str:
    with _used_types_lock:
        rotation = _shuffled_types(run_id)
        used = list(_used_types_by_run.get(run_id, []))  # snapshot — 락 안에서 복사
        d = description

        preferred: str | None = None

        # ★ 꺾은선/라인 차트 명시적 지정 — 최우선 (ERRORS [177] 2026-05-27)
        if any(k in d for k in ['꺾은선', '라인차트', '라인 차트', 'line chart']):
            preferred = 'line'
        # ★ ERRORS [175] 2026-05-26: 종목 집합 차트 — 시계열/산점도 부적합 감지
        # 종목 개수가 명시된 비교 차트는 항상 bar/barh 계열 (횡단면 데이터 only)
        _has_stock_set = bool(re.search(r'\d+\s*종목', d) or '종목별' in d)
        if preferred:
            pass  # 이미 결정됨
        elif _has_stock_set:
            # ★ 제12조: bar 계열 이미 사용됐으면 같은 run에 bar 계열 재선택 금지
            _bar_used = any(t in _BAR_FAMILY for t in used)
            if _bar_used:
                # bar 계열 이미 사용 → 종목 비교라도 다른 시각 타입 사용
                preferred = next((t for t in ('donut', 'pie', 'step', 'scatter', 'area') if t not in used), 'donut')
            elif any(k in d for k in ['순위', '랭킹', '규모 순', '시총 기준']):
                preferred = 'barh'
            elif any(k in d for k in ['추이', '변화', '흐름', '트렌드', '시계열']):
                preferred = next((t for t in ('bar', 'barh', 'iso_bar') if t not in used), 'bar')
            else:
                preferred = next((t for t in ('barh', 'iso_bar', 'bar') if t not in used), 'barh')
        elif any(k in d for k in ['산점도', '분포도', 'PER', 'ROE', '상관관계', 'X축', 'Y축', '2차원', '버블']):
            preferred = 'scatter'
        elif any(k in d for k in ['점유율', '구성비', '비율 분포']):
            preferred = 'donut' if 'donut' not in used else 'pie'
        elif any(k in d for k in ['기준금리', '정책금리', '변화 과정', '단계적']):
            preferred = 'band_line'
        elif any(k in d for k in ['추이', '변화', '흐름', '트렌드', '시계열', '1년', '최근']):
            # 시계열 — iso_area 우선, 없으면 iso_bar / line / area 순
            preferred = next((t for t in ('iso_area', 'iso_bar', 'line', 'area') if t not in used), None)
        elif any(k in d for k in ['순위', '랭킹', '비교 순서', '기업별', '업체별', '사별', '국가별', '항목별']) or \
             re.search(r'\d\s*개?\s*사\s*(가입|비교|규모|매출|점유|현황|수|순위)', d):
            preferred = 'barh'
        elif any(k in d for k in ['비교', '현황', '규모', '매출', '점유']):
            preferred = next((t for t in ('iso_bar', 'bar') if t not in used), None)

        def _pick_unused(want: str | None) -> str:
            # ★ 제12조: bar 계열은 run 전체에서 1종만 허용
            _bar_used_flag = any(t in _BAR_FAMILY for t in used)
            if want in _BAR_FAMILY and _bar_used_flag:
                want = None  # bar 계열 재선택 금지
            if want and want not in used:
                return want
            for t in rotation:
                # bar 계열 이미 사용됐으면 rotation에서도 bar 계열 건너뜀
                if t not in used and not (t in _BAR_FAMILY and _bar_used_flag):
                    return t
            from collections import Counter
            cnt = Counter(used)
            return min(rotation, key=lambda t: cnt.get(t, 0))

        chosen = _pick_unused(preferred)

        # ★ 글로벌 히스토리 기반 타입 다양화 (ERRORS [247])
        # preferred가 명시적으로 지정된 경우가 아니면, 최근 글들에서 과다 사용된 타입 회피.
        if not preferred or _global_type_penalty(chosen) > 0.6:
            # 글로벌 히스토리에서 덜 사용된 타입 탐색
            _global_penalty = {t: _global_type_penalty(t) for t in rotation}
            _bar_used_flag2 = any(t in _BAR_FAMILY for t in used)
            _alt = min(
                (t for t in rotation if t not in used and
                 not (t in _BAR_FAMILY and _bar_used_flag2)),
                key=lambda t: _global_penalty.get(t, 0.0),
                default=chosen,
            )
            if _global_type_penalty(_alt) < _global_type_penalty(chosen):
                chosen = _alt

        if run_id:
            _used_types_by_run.setdefault(run_id, []).append(chosen)
            if len(_used_types_by_run) > 100:
                del _used_types_by_run[next(iter(_used_types_by_run))]
        # ★ 글로벌 히스토리 기록은 generate_chart 에서 PNG 저장 성공 후에만 수행.
        # 여기서 호출하면 데이터 없어 차트 스킵되는 경우에도 히스토리에 오염됨.

    return chosen


def _extract_numbers(text: str) -> list[float]:
    patterns = [
        r'(\d+(?:\.\d+)?)\s*%',
        r'(\d+(?:\.\d+)?)\s*억',
        r'(\d+(?:\.\d+)?)\s*조',
        r'\$(\d+(?:\.\d+)?)\s*(?:억|만|B|M)?',
        r'(\d{1,3}(?:,\d{3})*(?:\.\d+)?)',
    ]
    nums = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            try:
                v = float(m.group(1).replace(',', ''))
                if 0 < v < 1e9:
                    nums.append(v)
            except ValueError:
                pass
    return nums[:10]


_DOMAIN_LABELS: list[tuple[list[str], list[str]]] = [
    (['이동통신', '통신사', 'SKT', 'KT', 'LGU', '5G', '통신 3사'],
     ['SKT', 'KT', 'LGU+']),
    (['은행', '시중은행', 'KB', '신한', '하나', '우리', '농협'],
     ['KB국민', '신한', '하나', '우리', 'NH농협']),
    (['보험', '생명보험', '손해보험'],
     ['삼성생명', '한화생명', '교보생명', '삼성화재', 'DB손해보험']),
    (['증권', '금융투자'],
     ['미래에셋', '삼성증권', 'KB증권', '한국투자', 'NH투자증권']),
    (['반도체', '메모리'],
     ['삼성전자', 'SK하이닉스', '마이크론', 'TSMC', '인텔']),
    (['자동차', '완성차', '전기차', 'EV'],
     ['현대차', '기아', 'GM', '테슬라', '도요타']),
    (['유통', '이커머스', '쇼핑'],
     ['쿠팡', '네이버쇼핑', 'G마켓', '11번가', 'SSG']),
    (['플랫폼', 'SNS', '앱'],
     ['카카오', '네이버', '쿠팡', '토스', '당근마켓']),
    (['항공', '여행'],
     ['대한항공', '아시아나', '제주항공', '진에어', '티웨이']),
    (['국가', '나라', 'GDP'],
     ['미국', '중국', '일본', '한국', '독일']),
    (['AI 가속기', 'AI칩', 'GPU', 'NVIDIA', '엔비디아', 'AMD', 'AI 반도체'],
     ['엔비디아', 'AMD', '구글 TPU', '인텔 가우디', 'AWS 트레이니움']),
    (['빅테크', '클라우드', '마이크로소프트', '아마존', 'AWS', '구글', '메타', '애플'],
     ['아마존(AWS)', '마이크로소프트', '구글', '메타', '애플']),
    (['배터리', '2차전지', '리튬', '셀', 'CATL', 'BYD'],
     ['LG에너지솔루션', '삼성SDI', 'SK온', 'CATL', 'BYD']),
    (['게임', '게임주', '게임사'],
     ['넥슨', '넷마블', '크래프톤', 'NC소프트', '펄어비스']),
    (['코스피', 'KOSPI', '코스닥', '주가지수', '글로벌 증시', '주요 지수'],
     ['코스피', '코스닥', '나스닥', 'S&P500', '다우존스']),
    (['환율', '원달러', '원·달러', '달러 강세', '달러 약세', '엔화', '위안화'],
     ['원달러', '원엔', '원위안', '원유로', 'DXY(달러지수)']),
    (['정유', '에너지', '원유', '석유', '천연가스'],
     ['SK이노베이션', 'GS칼텍스', 'S-Oil', 'HD현대오일뱅크', '에쓰오일']),
    (['바이오', '제약', '헬스케어', '의약품', '신약'],
     ['삼성바이오로직스', '셀트리온', '한미약품', '유한양행', '종근당']),
    (['건설', '아파트', '부동산', '분양'],
     ['삼성물산', '현대건설', 'GS건설', '대우건설', 'DL이앤씨']),
    (['조선', '선박', '해운'],
     ['HD현대중공업', '삼성중공업', '한화오션', 'HMM', '팬오션']),
]


def _extract_names_from_text(text: str) -> list[str] | None:
    for paren_m in re.finditer(r'\(([^)]{3,80})\)', text):
        inner = paren_m.group(1)
        if re.search(r'\s+vs\s+', inner, re.IGNORECASE):
            names = [n.strip() for n in re.split(r'\s+vs\s+', inner, flags=re.IGNORECASE)]
            names = [n for n in names if n and len(n) >= 2]
            if len(names) >= 2:
                return names[:6]
    _STOP = {'비교', '추이', '분석', '현황', '전망', '동향', '시장', '점유율', '변화', '흐름',
             '환율', '금리', '주가', '수익률', '수출', '수입', '성장률', '물가'}
    if re.search(r'\S+\s+vs\s+\S+', text, re.IGNORECASE):
        parts = re.split(r'\s+vs\s+', text, flags=re.IGNORECASE)
        names = []
        for i, p in enumerate(parts):
            words = p.strip().split()
            if not words:
                continue
            # 마지막 파트 뒤에 붙는 "비교/추이/환율..." 접미 단어 제거 — 정지 단어 전까지만
            if i == len(parts) - 1:
                clean = []
                for w in words:
                    if w in _STOP:
                        break
                    clean.append(w)
                candidate = ' '.join(clean[:2]) if clean else ''
            else:
                # 중간 파트: 마지막 1~2 단어 (직전 컨텍스트 단어)
                candidate = ' '.join(words[-2:]) if len(words) >= 2 else words[-1]
            if candidate and 2 <= len(candidate) <= 15:
                names.append(candidate)
        names = [n for n in names if n]
        if len(names) >= 2:
            return names[:6]
    return None


def _domain_labels(keyword: str, description: str) -> list[str] | None:
    text = keyword + description
    for keys, labels in _DOMAIN_LABELS:
        if any(k in text for k in keys):
            return labels
    return _extract_names_from_text(description)


def _synth_data(chart_type: str, keyword: str, chart_idx: int, n_points: int = 6,
                description: str = "", run_id: str = ""):
    """★ ERRORS [171] 2026-05-26: run_id 를 seed 에 포함 → 같은 키워드라도 매 발행마다 다른 합성 데이터."""
    import numpy as np
    seed = int(hashlib.md5(f"{keyword}_{chart_idx}_{run_id}".encode()).hexdigest()[:8], 16)
    rng  = np.random.default_rng(seed)
    now  = datetime.now()

    if chart_type in ('line', 'area', 'step'):
        labels = [(now - timedelta(days=30*(11-i))).strftime('%y.%m') for i in range(12)]
        base   = rng.uniform(40, 80)
        drift  = rng.uniform(-2, 5)
        noise  = rng.normal(0, 5, 12)
        values = (base + np.arange(12) * drift + noise).tolist()
        if chart_type == 'step':
            n_steps = int(rng.integers(3, 5))
            q = np.linspace(min(values), max(values), n_steps)
            values = [float(q[min(int((v - min(values)) / (max(values) - min(values) + 1e-9) * n_steps),
                                  n_steps - 1)]) for v in values]
    elif chart_type in ('pie', 'donut'):
        domain = _domain_labels(keyword, description)
        if domain:
            n, labels = len(domain), domain
        else:
            n = int(rng.integers(4, 6))
            _fallbacks = {
                '공격': ['계정 탈취', '금융 사기', '개인정보 유출', '2FA 우회', '기타'],
                '해킹': ['랜섬웨어', '피싱', '계정 탈취', '데이터 유출', '기타'],
                '보안': ['악성코드', '피싱', 'DDoS', '내부자 위협', '기타'],
                '시장': ['1위 기업', '2위 기업', '3위 기업', '4위 기업', '기타'],
                '구성': ['핵심 자산', '성장 자산', '안전 자산', '현금성 자산', '기타'],
            }
            labels_5 = next((v for k, v in _fallbacks.items() if k in description + keyword),
                            [f'유형 {i+1}' for i in range(5)])
            labels = labels_5[:n]
        raw    = rng.dirichlet(np.ones(n))
        values = (raw * 100).tolist()
    elif chart_type == 'barh':
        domain = _domain_labels(keyword, description)
        if domain:
            names = domain
        else:
            _kw_known = {
                '엔비디아': ['엔비디아', 'AMD', '구글 TPU', '인텔', 'AWS 트레이니움'],
                'AMD':      ['AMD', '엔비디아', '인텔', 'ARM', 'TSMC'],
                '삼성':     ['삼성전자', 'SK하이닉스', 'LG전자', '현대차', 'SK이노베이션'],
                '현대':     ['현대차', '기아', 'GM', '테슬라', '도요타'],
                '카카오':   ['카카오', '네이버', '쿠팡', '토스', '크래프톤'],
            }
            names = next((v for k, v in _kw_known.items() if k in keyword or k in description),
                         ['A 기업', 'B 기업', 'C 기업', 'D 기업', 'E 기업'])
        labels = names[:5]
        values = sorted(rng.uniform(20, 100, 5).tolist(), reverse=True)
    elif chart_type == 'scatter':
        domain = _domain_labels(keyword, description)
        names  = domain[:6] if domain else ['A사', 'B사', 'C사', 'D사', 'E사']
        labels = names
        x_vals = rng.uniform(2, 9, len(names)).tolist()
        y_vals = rng.uniform(2, 9, len(names)).tolist()
        values = [v for pair in zip(x_vals, y_vals) for v in pair]
    else:  # bar / combo
        labels = [(now - timedelta(days=30*(5-i))).strftime('%y.%m') for i in range(6)]
        base   = rng.uniform(50, 80)
        values = (base + rng.uniform(-10, 20, 6)).tolist()
    return labels, values


# ── Plotly 차트 생성 ─────────────────────────────────────────────

def _base_layout(title: str, keyword: str, sector: str,
                 use_synth: bool, width: int, height: int) -> dict:
    annotations = [
        dict(x=0, y=1.12, xref='paper', yref='paper', showarrow=False,
             text=f"<b>{title}</b>",
             font=dict(size=28, color=_TEXT_H, family=_FONT),
             align='left', xanchor='left'),
        dict(x=0, y=1.05, xref='paper', yref='paper', showarrow=False,
             text=f"{keyword}  ·  {sector}",
             font=dict(size=16, color=_TEXT_B, family=_FONT),
             align='left', xanchor='left'),
        dict(x=1, y=1.12, xref='paper', yref='paper', showarrow=False,
             text=datetime.now().strftime('%Y.%m'),
             font=dict(size=16, color=_TEXT_B, family=_FONT),
             align='right', xanchor='right'),
    ]
    if use_synth:
        annotations.append(dict(
            x=0.5, y=-0.14, xref='paper', yref='paper', showarrow=False,
            text='[ 참고용 예시 차트 — 실제 수치와 다를 수 있음 ]',
            font=dict(size=14, color='#c0392b', family=_FONT),
            bgcolor='#fff3cd', bordercolor='#e67e22', borderwidth=1.5,
            borderpad=6, align='center',
        ))
    return dict(
        paper_bgcolor=_BG,
        plot_bgcolor=_PANEL,
        font=dict(family=_FONT, size=16, color=_TEXT_B),
        width=width, height=height,
        margin=dict(t=110, b=90, l=80, r=80),
        annotations=annotations,
        showlegend=False,
        xaxis=dict(showgrid=True, gridcolor=_GRID, gridwidth=1,
                   zeroline=False, showline=True, linecolor='#dee2e6',
                   tickfont=dict(size=16, color=_TEXT_B)),
        yaxis=dict(showgrid=True, gridcolor=_GRID, gridwidth=1,
                   zeroline=False, showline=False,
                   tickfont=dict(size=16, color=_TEXT_B)),
    )


def _make_plotly_fig(chart_type: str, labels: list, values: list,
                     C: dict, title: str, keyword: str, sector: str,
                     use_synth: bool, run_id: str = "") -> object:
    import plotly.graph_objects as go
    import numpy as np

    pal    = C['list5']
    W, H   = (960, 840) if chart_type in ('pie', 'donut') else \
             (1200, 860) if chart_type == 'scatter' else (1280, 840)
    layout = _base_layout(title, keyword, sector, use_synth, W, H)

    fig = go.Figure()

    # ── LINE ────────────────────────────────────────────────────
    if chart_type == 'line':
        fig.add_trace(go.Scatter(
            x=labels, y=values, mode='lines+markers',
            line=dict(color=C['primary'], width=3),
            marker=dict(size=8, color=C['primary'],
                        line=dict(color='white', width=2)),
            fill='tozeroy', fillcolor=_rgba(C['primary'], 0.08),
        ))
        max_i, min_i = int(np.argmax(values)), int(np.argmin(values))
        for i, (clr, ay) in [(max_i, (C['accent'], -40)), (min_i, (_TEXT_B, 40))]:
            layout.setdefault('annotations', []).append(dict(
                x=labels[i], y=values[i], text=f"<b>{values[i]:.1f}</b>",
                showarrow=True, arrowhead=2, arrowcolor=clr, arrowsize=0.8,
                ay=ay, ax=0, font=dict(color=clr, size=16, family=_FONT),
                bgcolor=_BG, bordercolor=clr, borderwidth=1.5, borderpad=4,
                xanchor='center',
            ))

    # ── BAR ─────────────────────────────────────────────────────
    elif chart_type == 'bar':
        bar_colors = (pal * 3)[:len(labels)]
        fig.add_trace(go.Bar(
            x=labels, y=values,
            marker_color=bar_colors,
            marker_line=dict(color='white', width=1.5),
            text=[f'<b>{_fmt_bar_val(v)}</b>' for v in values],
            textposition='outside',
            textfont=dict(size=16, color=_TEXT_H, family=_FONT),
        ))
        # 평균선
        avg = float(np.mean(values))
        fig.add_hline(y=avg, line_dash='dot', line_color=C['accent'],
                      line_width=2,
                      annotation_text=f'평균 {avg:.1f}',
                      annotation_position='top right',
                      annotation_font=dict(color=C['accent'], size=16, family=_FONT))

    # ── BARH ────────────────────────────────────────────────────
    elif chart_type == 'barh':
        bar_colors = (pal * 3)[:len(labels)]
        fig.add_trace(go.Bar(
            x=values, y=labels, orientation='h',
            marker_color=bar_colors,
            marker_line=dict(color='white', width=1.5),
            text=[f'<b>{_fmt_bar_val(v)}</b>' for v in values],
            textposition='outside',
            textfont=dict(size=16, color=_TEXT_H, family=_FONT),
        ))
        layout['xaxis']['showgrid'] = True
        layout['xaxis']['automargin'] = True
        layout['yaxis']['showgrid'] = False
        layout['yaxis']['tickfont'] = dict(size=16, color=_TEXT_H, family=_FONT)
        layout['yaxis']['autorange'] = 'reversed'

    # ── PIE ─────────────────────────────────────────────────────
    elif chart_type == 'pie':
        fig.add_trace(go.Pie(
            labels=labels, values=values,
            marker=dict(colors=(pal * 3)[:len(labels)],
                        line=dict(color='white', width=2.5)),
            textinfo='label+percent',
            textfont=dict(size=16, family=_FONT),
            insidetextorientation='radial',
            hoverinfo='label+percent+value',
        ))
        layout['xaxis'] = dict(visible=False)
        layout['yaxis'] = dict(visible=False)

    # ── DONUT ────────────────────────────────────────────────────
    elif chart_type == 'donut':
        total = sum(values)
        fig.add_trace(go.Pie(
            labels=labels, values=values,
            hole=0.52,
            marker=dict(colors=(pal * 3)[:len(labels)],
                        line=dict(color='white', width=2.5)),
            textinfo='label+percent',
            textfont=dict(size=16, family=_FONT),
            hoverinfo='label+percent+value',
        ))
        fig.add_annotation(
            x=0.5, y=0.52, xref='paper', yref='paper',
            text=f'<b>{total:.0f}</b>', showarrow=False,
            font=dict(size=34, color=C['primary'], family=_FONT),
        )
        fig.add_annotation(
            x=0.5, y=0.42, xref='paper', yref='paper',
            text='TOTAL', showarrow=False,
            font=dict(size=16, color=_TEXT_B, family=_FONT),
        )
        layout['xaxis'] = dict(visible=False)
        layout['yaxis'] = dict(visible=False)

    # ── AREA ─────────────────────────────────────────────────────
    elif chart_type == 'area':
        import numpy as _np2
        # ★ ERRORS [172]: run_id 포함 → 같은 title이라도 매 발행마다 다른 v2 시리즈
        seed2  = int(hashlib.md5(f"{title}|{run_id}".encode()).hexdigest()[:8], 16) % 99999
        rng2   = _np2.random.default_rng(seed2)
        scale2 = rng2.uniform(0.55, 0.80)
        v2     = [max(0.1, v * scale2 + float(rng2.uniform(-4, 4))) for v in values]
        fig.add_trace(go.Scatter(
            x=labels, y=values, name='시리즈 A',
            mode='lines', fill='tozeroy',
            line=dict(color=C['primary'], width=2.5),
            fillcolor=_rgba(C['primary'], 0.18),
        ))
        fig.add_trace(go.Scatter(
            x=labels, y=v2, name='시리즈 B',
            mode='lines', fill='tozeroy',
            line=dict(color=C['secondary'], width=2),
            fillcolor=_rgba(C['secondary'], 0.12),
        ))
        layout['showlegend'] = True
        layout['legend'] = dict(orientation='h', y=1.02, x=1,
                                 xanchor='right', font=dict(size=16, family=_FONT))

    # ── COMBO ────────────────────────────────────────────────────
    elif chart_type == 'combo':
        import numpy as _np3
        trend = _np3.convolve(values, _np3.ones(3)/3, mode='same').tolist()
        fig.add_trace(go.Bar(
            x=labels, y=values, name='값',
            marker_color=_rgba(C['primary'], 0.75),
            marker_line=dict(color='white', width=1),
            yaxis='y',
        ))
        fig.add_trace(go.Scatter(
            x=labels, y=trend, name='추세',
            mode='lines+markers',
            line=dict(color=C['accent'], width=2.5),
            marker=dict(size=7, color=C['accent'],
                        line=dict(color='white', width=1.5)),
            yaxis='y2',
        ))
        layout['showlegend'] = True
        layout['legend'] = dict(orientation='h', y=1.02, x=1,
                                 xanchor='right', font=dict(size=16, family=_FONT))
        layout['yaxis2'] = dict(
            overlaying='y', side='right',
            showgrid=False, zeroline=False,
            tickfont=dict(size=16, color=C['accent'], family=_FONT),
            title=dict(text='추세', font=dict(color=C['accent'], size=16, family=_FONT)),
        )

    # ── STEP ─────────────────────────────────────────────────────
    elif chart_type == 'step':
        fig.add_trace(go.Scatter(
            x=labels, y=values, mode='lines+markers',
            line=dict(color=C['primary'], width=4, shape='hv'),
            marker=dict(size=11, color=C['accent'],
                        line=dict(color='white', width=2.5)),
            fill='tozeroy', fillcolor=_rgba(C['primary'], 0.10),
        ))
        # 값이 바뀌는 지점만 레이블 (12개 전부 표시하면 오른쪽 여백 침범)
        import numpy as _snp
        vals_arr = _snp.array(values)
        change_idxs = [0] + [i for i in range(1, len(values)) if values[i] != values[i-1]]
        if len(change_idxs) < 2:
            change_idxs = list(range(0, len(labels), max(1, len(labels)//6)))
        for i in change_idxs:
            layout.setdefault('annotations', []).append(dict(
                x=labels[i], y=values[i], text=f'<b>{values[i]:.1f}</b>',
                showarrow=True, arrowhead=2, arrowcolor=C['primary'],
                ax=0, ay=-36,
                font=dict(size=16, color=_TEXT_H, family=_FONT),
                bgcolor=_BG, bordercolor=C['primary'],
                borderwidth=1.5, borderpad=4,
                xanchor='center',
            ))

    # ── SCATTER ──────────────────────────────────────────────────
    elif chart_type == 'scatter':
        n      = len(labels)
        x_vals = values[0::2][:n]
        y_vals = values[1::2][:n]
        if not x_vals or not y_vals:
            fig = go.Figure()
            fig.update_layout(**layout)
            return fig
        x_mid  = float(np.mean(x_vals))
        y_mid  = float(np.mean(y_vals))

        # 사분면 음영 (shapes)
        x_min, x_max = min(x_vals), max(x_vals)
        y_min, y_max = min(y_vals), max(y_vals)
        pad_x = (x_max - x_min) * 0.18 + 0.5
        pad_y = (y_max - y_min) * 0.18 + 0.5
        layout.setdefault('shapes', [])
        layout['shapes'] += [
            dict(type='rect', xref='x', yref='y',
                 x0=x_mid, y0=y_mid, x1=x_max+pad_x, y1=y_max+pad_y,
                 fillcolor='rgba(232,245,233,0.55)', line=dict(width=0)),
            dict(type='rect', xref='x', yref='y',
                 x0=x_min-pad_x, y0=y_min-pad_y, x1=x_mid, y1=y_mid,
                 fillcolor='rgba(252,228,236,0.35)', line=dict(width=0)),
            dict(type='line', xref='x', yref='y',
                 x0=x_mid, y0=y_min-pad_y, x1=x_mid, y1=y_max+pad_y,
                 line=dict(color='#adb5bd', dash='dot', width=1.2)),
            dict(type='line', xref='x', yref='y',
                 x0=x_min-pad_x, y0=y_mid, x1=x_max+pad_x, y1=y_mid,
                 line=dict(color='#adb5bd', dash='dot', width=1.2)),
        ]
        # 산점도 포인트
        point_colors = (pal * 3)[:n]
        fig.add_trace(go.Scatter(
            x=x_vals, y=y_vals, mode='markers+text',
            text=labels,
            textposition='top center',
            textfont=dict(size=16, color=_TEXT_H, family=_FONT),
            marker=dict(
                size=22, color=point_colors,
                line=dict(color='white', width=2.5),
                opacity=0.92,
            ),
        ))
        layout['xaxis'].update(range=[x_min-pad_x, x_max+pad_x])
        layout['yaxis'].update(range=[y_min-pad_y, y_max+pad_y])

    fig.update_layout(**layout)

    # 시계열 차트 — x축을 데이터 범위로 고정 (어노테이션이 오른쪽 여백을 침범하는 버그 방지)
    if chart_type in ('line', 'area', 'step') and labels:
        fig.update_xaxes(range=[-0.5, len(labels) - 0.5])

    # ★ ERRORS [175] 2026-05-26: bar 차트 — textposition='outside' 때문에 y축 자동 확장
    # → 플롯 영역이 위로 늘어나 title annotation(y=1.12)이 잘리는 버그 방지.
    # y최대값 * 1.22 로 명시 고정 → 텍스트 레이블 여유 확보 + 타이틀 클립 방지.
    if chart_type == 'bar' and values:
        v_min = min(min(values) * 1.05, 0)
        v_max = max(values) * 1.22
        fig.update_yaxes(range=[v_min, v_max])

    return fig


# ── LLM 데이터 추출 ──────────────────────────────────────────────

def _llm_extract_chart_data(description, keyword, sector, context_text, chart_type):
    try:
        from shared.llm import invoke_text as _inv
        is_time_series = chart_type in ('line', 'area', 'step')
        is_scatter     = chart_type == 'scatter'

        stock_count_m  = re.search(r'(\d+)\s*종목', description)
        stock_hint = (
            f"차트 설명에 '{stock_count_m.group(0)}'가 명시되어 있습니다. "
            f"labels 에는 반드시 {stock_count_m.group(1)}개 종목(기업)명을 사용하세요.\n"
            if stock_count_m else ""
        )
        company_count_m = re.search(
            r'(\d+)\s*개?\s*사\s*(가입|비교|규모|매출|점유|현황|수|순위)', description)
        company_hint = (
            f"차트 설명에 '{company_count_m.group(0)}'가 명시되어 있습니다. "
            f"labels 에는 반드시 실제 기업/기관명 {company_count_m.group(1)}개를 사용하세요.\n"
            if company_count_m else ""
        )

        if is_time_series:
            label_rule = "시계열 차트이므로 날짜/기간 라벨을 사용하세요.\n"
        elif is_scatter:
            label_rule = (
                "★ 산점도입니다. 본문에 실제 수치가 명시된 경우에만 추출하세요.\n"
                "수치가 없으면 빈 배열을 반환하세요 — 추정 수치 생성 금지.\n"
            )
        else:
            label_rule = (
                "★ 카테고리 차트입니다. labels 에는 실제 명칭(기업명·종목명·섹터명)을 사용하세요.\n"
                "★ '1분기', '2분기' 같은 시계열 라벨은 절대 금지합니다.\n"
            )

        if is_scatter:
            json_format = '{"labels":["종목1","종목2",...], "x_values":[x1,x2,...], "y_values":[y1,y2,...]}'
        else:
            json_format = '{"labels":["라벨1","라벨2",...], "values":[숫자1,숫자2,...]}'

        prompt = (
            f"차트 설명: {description}\n"
            f"키워드: {keyword}  섹터: {sector}\n"
            f"\n━━━ 관련 본문 ━━━\n{context_text[:1200]}\n━━━━━━━━━━━━\n\n"
            + stock_hint + company_hint
            + "위 본문에서 차트에 표시할 실제 라벨과 수치를 추출하세요.\n"
            + label_rule
            + "★ labels 에 '경쟁사 A', '업체 A', '항목 A' 등 익명 표현 절대 금지.\n"
            + (
                "본문에서 수치를 찾지 못한 경우: 반드시 빈 배열 반환 — 추정·예시 수치 생성 절대 금지.\n"
            )
            + f"\nJSON만 출력 (설명 없음):\n{json_format}"
        )
        raw = _inv("analyzer", prompt, max_tokens=400, temperature=0.1)
        if not raw:
            return [], []
        m = re.search(r'\{[\s\S]*\}', raw)
        if not m:
            return [], []
        data   = json.loads(m.group(0))
        labels = data.get("labels", [])

        if is_scatter:
            x_vals = data.get("x_values", [])
            y_vals = data.get("y_values", [])
            if not labels or not x_vals or not y_vals or len(labels) != len(x_vals):
                return [], []
            n = min(len(labels), len(x_vals), len(y_vals))
            if n < 2:      # ★ scatter 1개 포인트 → 빈 canvas 방지
                return [], []
            x_f = [float(v) for v in x_vals[:n]]
            y_f = [float(v) for v in y_vals[:n]]
            return labels[:n], [v for pair in zip(x_f, y_f) for v in pair]
        else:
            values = data.get("values", [])
            if not labels or not values or len(labels) != len(values):
                return [], []
            values = [float(v) for v in values
                      if str(v).replace('.','',1).replace('-','',1).isdigit()
                      or isinstance(v, (int, float))]
            labels = labels[:len(values)]
            return ([], []) if len(labels) < 2 else (labels, values)
    except Exception as e:
        print(f"  ⚠️ [chart_generator] LLM 라벨 추출 실패: {e}")
        return [], []


# ── 금융 지수 실데이터 레이어 ────────────────────────────────────
# 키워드가 알려진 금융 지수와 매칭되면 yfinance 실데이터 사용 (합성 데이터 대체)
# 긴 패턴 우선 매칭 (코스피 200 > 코스피)
_INDEX_TICKERS: dict[str, str] = {
    '코스피 200': '^KS200',
    'KOSPI200':   '^KS200',
    '코스피200':  '^KS200',
    '코스피':     '^KS11',
    'KOSPI':      '^KS11',
    '코스닥':     '^KQ11',
    '나스닥':     '^IXIC',
    'NASDAQ':     '^IXIC',
    'S&P500':     '^GSPC',
    'S&P 500':    '^GSPC',
    '다우존스':   '^DJI',
    '다우':       '^DJI',
    'WTI':        'CL=F',
    '원유':       'CL=F',
    '금 현물':    'GC=F',
    '달러지수':   'DX-Y.NYB',
    'DXY':        'DX-Y.NYB',
    '국채':       '^TNX',
    '미국채':     '^TNX',
}


def _fetch_real_index_data(description: str, keyword: str) -> tuple[list, list]:
    """금융 지수 키워드 감지 시 yfinance 실데이터 반환 (ERRORS [177] 2026-05-27 수정).

    수정 사항:
    1. 설명에서 기간 키워드 파싱 ('최근 N개월', 'N개월', '1년', '6개월' 등) → period 자동 결정
    2. 1y 데이터 → monthly resample → 기간에 맞게 tail() 슬라이싱
    3. 재시도 1회 추가 (간헐적 네트워크 실패 대응)
    4. 반환 전 의미 있는 값 범위 검증 (지수명 숫자 오염 방지)
    """
    text = (description or '') + ' ' + (keyword or '')
    ticker = None
    # 긴 패턴 먼저 체크
    for k in sorted(_INDEX_TICKERS, key=len, reverse=True):
        if k in text:
            ticker = _INDEX_TICKERS[k]
            break
    if not ticker:
        return [], []

    # ── 기간 파싱 ─────────────────────────────────────────────────
    # "최근 3개월" → tail_months=3, "6개월" → 6, "1년" → 12, 기본=12
    _period_map = [
        (re.compile(r'최근\s*([0-9]+)\s*개월'), int),
        (re.compile(r'([0-9]+)\s*개월'), int),
        (re.compile(r'([0-9]+)\s*년'), lambda x: int(x) * 12),
    ]
    tail_months = 12
    for pat, fn in _period_map:
        m = pat.search(text)
        if m:
            try:
                tail_months = max(1, min(fn(m.group(1)), 24))
                break
            except Exception:
                pass

    for _attempt in range(2):  # 최대 2회 시도
        try:
            # 일별 데이터 후 월말 리샘플 (interval='1mo' 는 일부 지수에서 1건만 반환)
            hist = _j09_hist(ticker, period='2y')
            if hist is None or (hasattr(hist, 'empty') and hist.empty) or len(hist) < 5:
                continue
            monthly = hist['Close'].resample('ME').last().dropna()
            if len(monthly) < 2:
                continue

            # ── tail 슬라이싱: "최근 N개월" 에 맞게 자르기 ────────────
            monthly = monthly.tail(tail_months)

            labels = [d.strftime('%y.%m') for d in monthly.index]
            values = [round(float(c), 2) for c in monthly.values]

            # ── 값 범위 위생 검사 ──────────────────────────────────────
            # 지수명에 포함된 숫자(예: "코스피 200"의 200)가 실제 값 범위와 동떨어진 경우 통과
            if len(values) >= 2:
                v_range = max(values) - min(values)
                # 모든 값이 동일한 경우 = 실제 데이터 아님 → 폐기
                if v_range == 0:
                    print(f"  ⚠️ [chart_generator] 실데이터 전부 동일값 — 폐기 ({ticker}): {values[:3]}")
                    return [], []
            if not labels:
                continue

            print(f"  ✅ [chart_generator] 실데이터 획득 ({ticker}, 최근 {tail_months}개월): "
                  f"{labels[0]}~{labels[-1]}, 범위 {min(values):.1f}~{max(values):.1f}")
            return labels, values

        except Exception as _e:
            print(f"  ⚠️ [chart_generator] 실데이터 fetch 실패 ({ticker}, 시도{_attempt+1}): {_e}")

    return [], []


# ── 종목 데이터 직접 파싱 ────────────────────────────────────────
# context_text 에 '[종목 데이터]' 블록이 있으면 LLM 없이 직접 파싱.
# _stocks_text() 가 생성하는 구조화 텍스트 형식을 regex 로 추출.
# ★ ERRORS [175] 2026-05-26 — LLM 추출 실패 시 합성 데이터 → 면책 문구 방지

def _num_from_str(s: str) -> float | None:
    """'5,000억원', '12.5배', '8.3%', '-2.1' 에서 float 추출. 조 단위 → 억 변환."""
    if not s:
        return None
    clean = s.replace(',', '').strip()
    m = re.search(r'-?[0-9]+(?:\.[0-9]+)?', clean)
    if not m:
        return None
    v = float(m.group(0))
    if '조' in s:
        v *= 10000   # 조 → 억
    return v


def _section_metric(section_text: str, metric: str) -> float | None:
    """'- PER: 12.5배' 등 구조화 섹션에서 metric 수치 추출."""
    pat_map = {
        'marcap':    [r'시가총액:\s*([\d,]+(?:\.\d+)?\s*(?:억|조)원?)'],
        'price':     [r'현재가:\s*([\d,]+)원?'],
        'per':       [r'PER:\s*([\d.]+)배?'],
        'roe':       [r'ROE:\s*(-?[\d.]+)%?'],
        'op_margin': [r'영업이익률:\s*(-?[\d.]+)%?'],
        'revenue':   [r'매출:\s*([\d,]+(?:\.\d+)?\s*(?:억|조)원?)'],
    }
    for pat in pat_map.get(metric, []):
        mm = re.search(pat, section_text)
        if mm:
            return _num_from_str(mm.group(1))
    return None


def _extract_news_numbers_direct(docs: list, keyword: str) -> tuple[list, list]:
    """뉴스·블로그 문서에서 정규식으로 실수치 직접 추출 → (labels, values).

    LLM 없이 작동. 테마별 기사에서 실제 숫자 (%, 억원, 배, 위) 를 추출.
    같은 수치가 여러 기사에서 반복되면 평균, 상이하면 별도 항목으로 처리.
    실패 시 ([], []).
    """
    import re as _re

    # 패턴: (레이블, 값, 단위) 추출
    # 예: "매출 12조원", "영업이익률 15%", "시장점유율 3위", "전년 대비 23% 증가"
    _patterns = [
        # 퍼센트 변화율
        (r'([가-힣a-zA-Z\s]{2,12})\s+(\d+(?:\.\d+)?)\s*%\s*(?:증가|성장|상승|확대)',   'pct_up'),
        (r'([가-힣a-zA-Z\s]{2,12})\s+(\d+(?:\.\d+)?)\s*%\s*(?:감소|하락|축소)',        'pct_dn'),
        (r'(\d+(?:\.\d+)?)\s*%\s+(?:증가|성장|상승)',                                   'pct_bare'),
        # 매출·영업이익 (조/억)
        (r'([가-힣a-zA-Z\s]{2,15})\s+매출\s+(\d+(?:\.\d+)?)\s*조',                    'rev_tril'),
        (r'([가-힣a-zA-Z\s]{2,15})\s+매출\s+(\d+(?:,\d+)?(?:\.\d+)?)\s*억',           'rev_hund'),
        (r'매출\s+(\d+(?:\.\d+)?)\s*조원?',                                              'rev_tril_bare'),
        # 순위
        (r'([가-힣a-zA-Z\s]{2,15})\s+(?:세계|글로벌|국내)?\s*(\d+)\s*위',              'rank'),
        # 시장점유율
        (r'시장점유율\s+(\d+(?:\.\d+)?)\s*%',                                            'mktshare'),
        # 주가 관련
        (r'주가\s+(\d+(?:,\d+)?)\s*원',                                                  'price'),
        # 영업이익률
        (r'영업이익률\s+(\d+(?:\.\d+)?)\s*%',                                            'opm'),
    ]

    extracted: dict[str, list[float]] = {}

    for doc in docs:
        text = f"{getattr(doc, 'title', '')} {(getattr(doc, 'cleaned_text', '') or getattr(doc, 'raw_text', ''))[:800]}"
        for pat, ptype in _patterns:
            for m in _re.finditer(pat, text):
                grps = m.groups()
                if len(grps) == 2:
                    label_raw, val_str = grps
                    label = (label_raw or ptype).strip()[-12:]
                elif len(grps) == 1:
                    label = ptype
                    val_str = grps[0]
                else:
                    continue
                try:
                    val = float(val_str.replace(',', ''))
                    if ptype == 'rev_tril':
                        val *= 10000  # 조 → 억 환산
                    if ptype in ('pct_dn',):
                        val = -val
                    if 0 < abs(val) < 1_000_000:
                        extracted.setdefault(label, []).append(val)
                except (ValueError, TypeError):
                    continue

    if not extracted:
        return [], []

    # 각 레이블 중앙값 → 상위 6개 (절댓값 기준)
    aggregated = {lbl: sorted(vals)[len(vals)//2] for lbl, vals in extracted.items() if vals}
    top = sorted(aggregated.items(), key=lambda x: abs(x[1]), reverse=True)[:6]
    if len(top) < 2:
        return [], []

    labels = [t[0] for t in top]
    values = [t[1] for t in top]
    return labels, values


def _parse_stock_context(context_text: str, description: str) -> tuple[list, list]:
    """[종목 데이터] 섹션 직접 파싱 → LLM 없이 라벨·수치 반환.
    성공 시 (names≥2, values), 실패 시 ([], []).
    """
    if "[종목 데이터]" not in context_text:
        return [], []

    stocks_block = context_text[context_text.index("[종목 데이터]"):]
    d = description.lower()

    # 지표 우선순위 결정
    if any(k in d for k in ['시가총액', '시총', '시장규모', '캡', '규모']):
        metrics = ['marcap', 'price']
    elif any(k in d for k in ['per', '주가수익']):
        metrics = ['per', 'marcap']
    elif 'roe' in d:
        metrics = ['roe', 'op_margin']
    elif any(k in d for k in ['영업이익률', '영업마진']):
        metrics = ['op_margin', 'roe']
    elif any(k in d for k in ['현재가', '주가', '주가수준']):
        metrics = ['price', 'marcap']
    elif any(k in d for k in ['매출', '수익', '순이익']):
        metrics = ['revenue', 'marcap']
    else:
        metrics = ['marcap', 'price', 'per']   # 기본: 시총

    names, values = [], []

    # ── 상세 섹션 (대장주/부대장주) 파싱 ────────────────────────
    header_re = re.compile(r'\[(?:대장주|부대장주) — (.+?) \(rank=\d+\)\]')
    for hm in header_re.finditer(stocks_block):
        name = hm.group(1).strip()
        rest = stocks_block[hm.end():]
        next_h = re.search(r'\[(?:대장주|부대장주|나머지)', rest)
        sect = rest[:next_h.start()] if next_h else rest[:600]
        v = None
        for metric in metrics:
            v = _section_metric(sect, metric)
            if v is not None:
                break
        if v is not None and name:
            names.append(name)
            values.append(v)

    # ── 테이블 파싱 (나머지 N종목) ──────────────────────────────
    # 형식: "순위 | 종목명 | 현재가 | 시가총액 | PER | ROE | 흑/적자"
    tbl_m = re.search(
        r'\[나머지 \d+종목[^\]]*\]\n[^\n]+\n-+\n(.*?)(?:\n요약 —|$)',
        stocks_block, re.DOTALL,
    )
    if tbl_m:
        # col index: 0=rank, 1=name, 2=price, 3=marcap, 4=per, 5=roe/op_margin, 6=profit
        primary_metric = metrics[0]
        col_idx = {'price': 2, 'marcap': 3, 'per': 4, 'roe': 5, 'op_margin': 5, 'revenue': 3}
        ci = col_idx.get(primary_metric, 3)
        for row in tbl_m.group(1).strip().splitlines():
            parts = [p.strip() for p in row.split('|')]
            if len(parts) < 5:
                continue
            rname = parts[1].strip()
            if not rname or rname in ('종목명', '?'):
                continue
            raw_val = parts[ci] if ci < len(parts) else ''
            v = _num_from_str(raw_val)
            # marcap "N/A" 일 때 price 로 대체
            if v is None and primary_metric == 'marcap' and len(parts) > 2:
                v = _num_from_str(parts[2])
            if v is not None:
                names.append(rname)
                values.append(v)

    if len(names) < 2:
        return [], []
    return names, values


def _fetch_tickers_from_context(context_text: str) -> list:
    """[종목 데이터] 블록에서 yfinance 티커 목록 파싱 (순서 유지·dedup)."""
    if "[종목 데이터]" not in context_text:
        return []
    return list(dict.fromkeys(re.findall(r'\((\d{6}\.K[SQ])\)', context_text)))


def _build_j09_context(keyword: str, description: str) -> str:
    """JARVIS09 모든 소스 병렬 수집 → 차트 생성용 통합 컨텍스트 텍스트.

    호출하는 소스:
      1. collect_stocks_data  — 종목 재무 (시가총액·PER·ROE·영업이익률)
      2. collect_for_theme    — 뉴스·블로그·웹·공시·통계 텍스트 전체
      3. EcosProvider         — 한국은행 거시경제 시계열 (금리·CPI·환율·수출)
      4. KrxProvider          — KRX 주요 종목 실시간 시세
      5. get_market_data      — 글로벌 시장 지표 (S&P500·NASDAQ·달러원 등)

    ★ ERRORS [241] — 결과를 _J09_CTX_CACHE에 키워드 단위로 캐싱.
    같은 키워드의 두 번째 이상 호출은 즉시 반환 (실패 결과도 캐시).
    """
    from concurrent.futures import ThreadPoolExecutor

    # ── 캐시 히트 즉시 반환 (TTL 1시간) ─────────────────────────────
    import time as _time_cache
    with _J09_CTX_CACHE_LOCK:
        if keyword in _J09_CTX_CACHE:
            cached_text, cached_ts = _J09_CTX_CACHE[keyword]
            age = _time_cache.time() - cached_ts
            if age < _J09_CTX_TTL:
                print(f"  📦 [J09 컨텍스트] '{keyword}' 캐시 히트 ({len(cached_text)}자, {int(age)}초 전) — 수집 생략")
                return cached_text
            else:
                print(f"  ♻️ [J09 컨텍스트] '{keyword}' 캐시 만료 ({int(age)}초 경과) → 재수집")

    parts: list[str] = []

    def _stocks() -> str:
        from JARVIS09_COLLECTOR import collect_stocks_data
        data = collect_stocks_data(keyword)
        stocks = data.get("stocks") or []
        if not stocks:
            return ""
        lines = ["[종목 데이터]"]
        for s in stocks:
            name   = s.get("name", "?")
            ticker = s.get("ticker") or s.get("code", "")
            if ticker and "." not in ticker:
                ticker += ".KS"
            marcap = s.get("marcap") or 0
            price  = s.get("price") or 0
            per    = s.get("per") or ""
            roe    = s.get("roe") or ""
            op_m   = s.get("op_margin") or ""
            lines.append(
                f"{name} ({ticker}): 시가총액={marcap}억, 현재가={price}원"
                f", PER={per}, ROE={roe}, 영업이익률={op_m}%"
            )
        return "\n".join(lines)

    def _theme() -> str:
        from JARVIS09_COLLECTOR import collect_for_theme
        docs = collect_for_theme(keyword)
        if not docs:
            return ""
        # 소스 다양성 확보: 소스별 1~2건씩
        seen_src: dict[str, int] = {}
        selected = []
        for doc in docs:
            src = doc.source_type
            if seen_src.get(src, 0) < 2:
                selected.append(doc)
                seen_src[src] = seen_src.get(src, 0) + 1
            if len(selected) >= 12:
                break
        # CollectionResult: cleaned_text 속성 사용 (raw_text 폴백)
        text = "\n\n".join(
            f"[{d.source_type}] {d.title}\n{(getattr(d,'cleaned_text','') or getattr(d,'raw_text',''))[:400]}"
            for d in selected
        )
        return f"[수집 자료]\n{text}"

    def _ecos() -> str:
        from JARVIS09_COLLECTOR.providers.ecos_provider import EcosProvider
        docs = EcosProvider().collect(keyword)
        return docs[0].raw_text if docs else ""

    def _krx() -> str:
        from JARVIS09_COLLECTOR.providers.krx_provider import KrxProvider
        docs = KrxProvider().collect(keyword)
        return "\n".join(d.raw_text for d in docs) if docs else ""

    def _market() -> str:
        from JARVIS09_COLLECTOR import get_market_data
        data = get_market_data()
        if not data:
            return ""
        lines = ["[글로벌 시장 지표]"]
        for name, info in data.items():
            val = info.get("value") or info.get("current", "")
            chg = info.get("change") or info.get("change_pct", "")
            lines.append(f"  {name}: {val} ({chg:+.2f}%)" if isinstance(chg, float) else f"  {name}: {val}")
        return "\n".join(lines)

    tasks = {
        "stocks": _stocks,
        "theme":  _theme,
        "ecos":   _ecos,
        "krx":    _krx,
        "market": _market,
    }

    import time as _time
    from concurrent.futures import wait as _wait, FIRST_COMPLETED as _FIRST

    _deadline = _time.time() + 45  # 전체 45초 데드라인

    # ★ ERRORS [241] — shutdown(wait=False) 사용: 데드라인 초과 스레드가
    # 컨텍스트 매니저 종료 시 블로킹하는 문제 방지.
    # with 컨텍스트 매니저는 __exit__ 시 shutdown(wait=True) 호출 →
    # cancel 불가 실행 중 스레드(collect_stocks_data 등)가 끝날 때까지 대기.
    exe = ThreadPoolExecutor(max_workers=5)
    try:
        future_map = {exe.submit(fn): name for name, fn in tasks.items()}
        pending = set(future_map.keys())

        while pending:
            remaining = max(0.5, _deadline - _time.time())
            if remaining <= 0.5 and _time.time() > _deadline:
                break
            done, pending = _wait(pending, timeout=remaining, return_when=_FIRST)
            for fut in done:
                name = future_map[fut]
                try:
                    result = fut.result()
                    if result and result.strip():
                        parts.append(result)
                        print(f"  ✅ [J09 컨텍스트] {name} 완료 ({len(result)}자)")
                except Exception as e:
                    print(f"  ⚠️ [J09 컨텍스트] {name} 실패: {e}")

        # 데드라인 초과 미완료 future 취소 (미시작 future만 실제 취소됨)
        for fut in pending:
            fut.cancel()
            print(f"  ⏱️ [J09 컨텍스트] {future_map[fut]} 타임아웃 — 스킵")
    finally:
        # wait=False: 아직 실행 중인 백그라운드 스레드를 기다리지 않고 반환
        exe.shutdown(wait=False)

    result_text = "\n\n".join(parts)
    # ── 결과 캐싱 (TTL 타임스탬프 포함) ─────────────────────────────
    import time as _time_store
    with _J09_CTX_CACHE_LOCK:
        _J09_CTX_CACHE[keyword] = (result_text, _time_store.time())
        # 만료 항목 정리 (캐시 크기 제한) — 100개 초과 시 만료된 항목 먼저 제거
        if len(_J09_CTX_CACHE) > 100:
            _now = _time_store.time()
            expired = [k for k, (_, ts) in _J09_CTX_CACHE.items()
                       if _now - ts >= _J09_CTX_TTL]
            for k in expired:
                del _J09_CTX_CACHE[k]
    return result_text


def _parse_ecos_timeseries(context_text: str, description: str,
                           exclude_indicators: list[str] | None = None) -> tuple[list, list]:
    """ECOS 텍스트에서 월별 시계열 추출 → (labels, values).

    형식: '  202511: 2.5 연%' 또는 '  202511: 117.2 2020=100'
    description 키워드로 관련 블록 선택.
    exclude_indicators: 최근 글에서 이미 사용한 지표명 — 제외하여 로테이션.
    """
    if "[한국은행 ECOS" not in context_text:
        return [], []

    d_lower = description.lower()
    exclude = set(exclude_indicators or [])

    # 설명에 맞는 지표 블록 선택
    _kw_map = [
        (["금리", "기준금리"], "기준금리"),
        (["cpi", "물가", "소비자"], "소비자물가"),
        (["환율", "달러", "원달러"], "원/달러"),
        (["수출", "무역"], "수출금액"),
        (["실업", "고용"], "실업률"),
    ]
    # 설명 키워드 우선 → 단, 최근에 쓴 지표면 스킵
    target_section = None
    for kws, section in _kw_map:
        if any(k in d_lower for k in kws):
            target_section = section
            break

    blocks = re.split(r'\n\n', context_text)
    data_blocks = [(b, _block_name(b)) for b in blocks if re.search(r'\d{6}:\s*[\d.]+', b)]

    def _extract(block: str, name: str) -> tuple[list, list]:
        rows = re.findall(r'(\d{6}):\s*([\d.]+)', block)
        if len(rows) < 3:
            return [], []
        labels = [f"{r[0][2:4]}.{r[0][4:6]}" for r in rows]
        values = [float(r[1]) for r in rows]
        _record_ecos_used(name)
        return labels, values

    # 1순위: description 매칭 + 미사용 지표
    if target_section and target_section not in exclude:
        for block, name in data_blocks:
            if target_section in name:
                return _extract(block, name)

    # 2순위: 미사용 지표 중 아무거나 (로테이션)
    for block, name in data_blocks:
        if name not in exclude:
            return _extract(block, name)

    # 3순위: 전체 중 첫 번째 (모두 사용됐을 때)
    for block, name in data_blocks:
        return _extract(block, name)

    return [], []


def _block_name(block: str) -> str:
    """ECOS 블록에서 지표명 추출 ('기준금리 — 최근 6개월' → '기준금리')."""
    m = re.search(r'\[([^\]—\n]+?)(?:\s*—|\s*\])', block)
    if m:
        return m.group(1).strip()
    # 첫 번째 줄에서 추출
    first = block.strip().split('\n')[0]
    return first[:20].strip()


def _parse_krx_prices(context_text: str, keyword: str) -> tuple[list, list]:
    """KRX 시세 텍스트에서 종목명·가격 추출 → (labels, values).

    형식: '종목명(티커)|날짜 가격원|등락률|거래량'
    """
    if "KRX" not in context_text:
        return [], []

    names, prices = [], []
    for line in context_text.splitlines():
        # '삼성전자(005930)|2026-06-01 349,000원|+10.1%|...' 형식
        m = re.match(r'^(.+?)\(\d{6}\)\|[\d-]+ ([\d,]+)원', line)
        if m:
            name  = m.group(1).strip()
            price = float(m.group(2).replace(",", ""))
            if name and price > 0:
                names.append(name)
                prices.append(price)

    if len(names) >= 2:
        return names, prices
    return [], []


def _fetch_from_j09(keyword: str, description: str, chart_type: str) -> tuple[list, list]:
    """JARVIS09 모든 소스 활용 → 차트에 맞는 (labels, values) 반환.

    수집 소스 (순서대로 시도):
      0. band_line 전용 — ECOS 금리 시계열 직접 (주가·합성 데이터 절대 금지)
      1. collect_stocks_data  — 종목 재무 (bar/scatter/pie/donut)
      2. KrxProvider          — 실시간 종목 시세 (bar)
      3. EcosProvider         — 거시경제 시계열 (line/area)
      4. collect_for_theme    — 전체 소스 텍스트 → LLM 숫자 추출
      5. get_market_data      — 글로벌 시장 지표 (line/bar)
    실패/타임아웃 시 ([], []).
    """
    print(f"  🔄 [chart_generator] JARVIS09 전체 소스 수집: '{keyword}' / {chart_type}")
    d_lower = description.lower()

    # ── 0. band_line — ECOS 금리 실데이터 전용, 주가·합성 fallback 절대 금지 ──
    if chart_type == 'band_line':
        _rate_kw = ('기준금리', '정책금리', '금리', '이자율')
        _combined = (keyword or '') + ' ' + (description or '')
        if any(k in _combined for k in _rate_kw):
            try:
                from JARVIS09_COLLECTOR.providers.ecos_provider import EcosProvider
                ecos_docs = EcosProvider().collect(keyword)
                if ecos_docs:
                    ecos_text = ecos_docs[0].raw_text
                    _bl_labels, _bl_values = _parse_ecos_timeseries(
                        ecos_text, description, exclude_indicators=_get_ecos_exclude()
                    )
                    if len(_bl_labels) >= 3:
                        print(f"  ✅ [J09] ECOS 기준금리 {len(_bl_labels)}포인트 → BAND_LINE")
                        return _bl_labels, _bl_values
            except Exception as _e:
                print(f"  ⚠️ [J09] EcosProvider(band_line) 실패: {_e}")
        # 실데이터 없음 — 거짓 차트 방지, 스킵
        print(f"  🚫 [J09] band_line 실데이터 없음 — 차트 스킵")
        return [], []

    # ── 1. collect_stocks_data (종목 재무 — 기존 로직 유지) ──────────────
    try:
        from JARVIS09_COLLECTOR import collect_stocks_data
        data   = collect_stocks_data(keyword)
        stocks = data.get("stocks") or []

        if stocks:
            if chart_type in ('line', 'area', 'step', 'iso_area', 'combo'):
                leader = next((s for s in stocks if s.get("rank") == 1), stocks[0])
                ticker = leader.get("ticker") or leader.get("code", "")
                if ticker and "." not in ticker:
                    ticker += ".KS"
                if ticker:
                    from datetime import timedelta
                    end   = datetime.now()
                    start = end - timedelta(days=365)
                    hist  = _j09_dl(ticker, start=start.strftime('%Y-%m-%d'),
                                    end=end.strftime('%Y-%m-%d'), interval='1mo')
                    if not hist.empty:
                        closes = hist['Close'].dropna()
                        if len(closes) >= 3:
                            labels = [idx.strftime('%y.%m') for idx in closes.index]
                            values = [round(float(v), 0) for v in closes.values]
                            print(f"  ✅ [J09] 주가이력 {len(labels)}포인트 ({leader.get('name',ticker)})")
                            return labels, values

            elif chart_type == 'scatter':
                names, pers, roes = [], [], []
                for s in stocks:
                    per = s.get("per")
                    roe = s.get("roe")
                    if per and roe and float(per) > 0 and float(roe) != 0:
                        names.append(s.get("name", "?"))
                        pers.append(float(per))
                        roes.append(float(roe))
                if len(names) >= 2:
                    interleaved = [v for p, r in zip(pers, roes) for v in (p, r)]
                    print(f"  ✅ [J09] PER/ROE {len(names)}종목 → SCATTER")
                    return names, interleaved

            else:  # bar/barh/pie/donut/step
                _metric_kw = [
                    (['per', '주가수익'], 'per'),
                    (['roe'], 'roe'),
                    (['영업이익률', '영업마진'], 'op_margin'),
                    (['현재가', '주가'], 'price'),
                ]
                metric = next(
                    (m for kws, m in _metric_kw if any(k in d_lower for k in kws)),
                    'marcap'
                )
                names, vals = [], []
                for s in stocks:
                    v = s.get(metric) or (s.get('price') if metric == 'marcap' else None)
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        continue
                    if v and v > 0:
                        names.append(s.get("name", "?"))
                        vals.append(v)
                if len(names) >= 2:
                    print(f"  ✅ [J09] {metric} {len(names)}종목 → {chart_type.upper()}")
                    return names, vals
    except Exception as e:
        print(f"  ⚠️ [J09] collect_stocks_data 실패: {e}")

    # ── 2. KrxProvider — 실시간 종목 시세 (bar/barh 계열) ───────────────
    if chart_type not in ('line', 'area', 'step', 'iso_area', 'combo', 'scatter'):
        try:
            from JARVIS09_COLLECTOR.providers.krx_provider import KrxProvider
            krx_docs = KrxProvider().collect(keyword, max_items=3)
            if krx_docs:
                krx_text = "\n".join(d.raw_text for d in krx_docs)
                names, prices = _parse_krx_prices(krx_text, keyword)
                if len(names) >= 2:
                    print(f"  ✅ [J09] KRX 종목 시세 {len(names)}개 → {chart_type.upper()}")
                    return names, prices
        except Exception as e:
            print(f"  ⚠️ [J09] KrxProvider 실패: {e}")

    # ── 2.5 collect_for_theme — 뉴스 직접 수치 파싱 (LLM 없이, 테마별 실데이터) ──
    _theme_docs_cache: list = []
    try:
        from JARVIS09_COLLECTOR import collect_for_theme
        _theme_docs_cache = collect_for_theme(keyword) or []
        if _theme_docs_cache:
            # 소스 다양성 확보하며 top 8 문서
            seen_src: dict[str, int] = {}
            selected = []
            for doc in _theme_docs_cache:
                src = doc.source_type
                if seen_src.get(src, 0) < 2:
                    selected.append(doc)
                    seen_src[src] = seen_src.get(src, 0) + 1
                if len(selected) >= 8:
                    break
            labels, values = _extract_news_numbers_direct(selected, keyword)
            if len(labels) >= 2:
                print(f"  ✅ [J09] 뉴스 직접 수치 파싱 {len(labels)}개 → {chart_type.upper()}")
                return labels, values
    except Exception as e:
        print(f"  ⚠️ [J09] 뉴스 직접 파싱 실패: {e}")

    # ── 3. EcosProvider — 거시경제 시계열 (로테이션 — 이전 글과 다른 지표) ──
    _ecos_kws = ['금리', '물가', 'cpi', '환율', '달러', '수출', '실업', '성장', '경제지표']
    if chart_type in ('line', 'area', 'step', 'iso_area', 'combo') or \
       any(k in d_lower for k in _ecos_kws):
        try:
            from JARVIS09_COLLECTOR.providers.ecos_provider import EcosProvider
            ecos_docs = EcosProvider().collect(keyword)
            if ecos_docs:
                ecos_text = ecos_docs[0].raw_text
                labels, values = _parse_ecos_timeseries(
                    ecos_text, description, exclude_indicators=_get_ecos_exclude()
                )
                if len(labels) >= 3:
                    print(f"  ✅ [J09] ECOS 시계열 {len(labels)}포인트 → {chart_type.upper()}")
                    return labels, values
        except Exception as e:
            print(f"  ⚠️ [J09] EcosProvider 실패: {e}")

    # ── 4. collect_for_theme — LLM 숫자 추출 (직접 파싱 실패 시) ──────────
    try:
        if _theme_docs_cache:
            # 이미 수집한 결과 재사용 (중복 수집 방지)
            seen_src2: dict[str, int] = {}
            selected2 = []
            for doc in _theme_docs_cache:
                src = doc.source_type
                if seen_src2.get(src, 0) < 2:
                    selected2.append(doc)
                    seen_src2[src] = seen_src2.get(src, 0) + 1
                if len(selected2) >= 10:
                    break
            combined = "\n\n".join(
                f"[{d.source_type}] {d.title}\n"
                f"{(getattr(d,'cleaned_text','') or getattr(d,'raw_text',''))[:600]}"
                for d in selected2
            )
            if combined:
                labels, values = _llm_extract_chart_data(
                    description, keyword, "", combined, chart_type
                )
                if len(labels) >= 2:
                    print(f"  ✅ [J09] collect_for_theme → LLM 추출 {len(labels)}개")
                    return labels, values
    except Exception as e:
        print(f"  ⚠️ [J09] collect_for_theme LLM 추출 실패: {e}")

    # ── 5. get_market_data — 글로벌 시장 지표 폴백 ──────────────────────
    try:
        from JARVIS09_COLLECTOR import get_market_data
        market = get_market_data()
        if market:
            pairs = [
                (name, float(info.get("value") or info.get("current", 0)))
                for name, info in market.items()
                if (info.get("value") or info.get("current", 0))
            ]
            pairs = [(n, v) for n, v in pairs if v > 0]
            if len(pairs) >= 3:
                labels, values = zip(*pairs)
                print(f"  ✅ [J09] 글로벌 시장 지표 {len(labels)}개 → {chart_type.upper()}")
                return list(labels), list(values)
    except Exception as e:
        print(f"  ⚠️ [J09] get_market_data 실패: {e}")

    print(f"  ⚠️ [J09] 모든 소스 실패 — 차트 스킵")
    return [], []


def _fetch_stock_price_history(context_text: str) -> tuple:
    """대장주 yfinance 1년 월별 종가 → (labels, values). 실패 시 ([], [])."""
    tickers = _fetch_tickers_from_context(context_text)
    if not tickers:
        return [], []
    try:
        from datetime import timedelta
        ticker = tickers[0]
        end = datetime.now()
        start = end - timedelta(days=365)
        hist = _j09_dl(ticker, start=start.strftime('%Y-%m-%d'),
                           end=end.strftime('%Y-%m-%d'), interval='1mo',
                           progress=False, auto_adjust=True)
        if hist.empty:
            return [], []
        # pandas 2.x: 멀티인덱스 컬럼 처리
        closes = hist['Close']
        if hasattr(closes, 'iloc') and closes.ndim > 1:
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        if len(closes) < 3:
            return [], []
        labels = [str(d)[:7] for d in closes.index]
        values = [round(float(v), 0) for v in closes.values]
        return labels, values
    except Exception:
        return [], []


def _fetch_per_roe_scatter(context_text: str) -> tuple:
    """[종목 데이터]에서 PER + ROE 파싱 → scatter 포맷 (interleaved [x1,y1,x2,y2,...]).
    x=PER, y=ROE. 실패 시 ([], []).
    """
    if "[종목 데이터]" not in context_text:
        return [], []
    stocks_block = context_text[context_text.index("[종목 데이터]"):]
    names, pers, roes = [], [], []

    header_re = re.compile(r'\[(?:대장주|부대장주) — (.+?) \(rank=\d+\)\]')
    for hm in header_re.finditer(stocks_block):
        name = hm.group(1).strip()
        rest = stocks_block[hm.end():]
        next_h = re.search(r'\[(?:대장주|부대장주|나머지)', rest)
        sect = rest[:next_h.start()] if next_h else rest[:600]
        per = _section_metric(sect, 'per')
        roe = _section_metric(sect, 'roe')
        if per is not None and roe is not None and name:
            names.append(name)
            pers.append(per)
            roes.append(roe)

    tbl_m = re.search(
        r'\[나머지 \d+종목[^\]]*\]\n[^\n]+\n-+\n(.*?)(?:\n요약 —|$)',
        stocks_block, re.DOTALL,
    )
    if tbl_m:
        for row in tbl_m.group(1).strip().splitlines():
            parts = [p.strip() for p in row.split('|')]
            if len(parts) < 6:
                continue
            rname = parts[1].strip()
            if not rname or rname in ('종목명', '?'):
                continue
            per = _num_from_str(parts[4]) if len(parts) > 4 else None
            roe = _num_from_str(parts[5]) if len(parts) > 5 else None
            if per is not None and roe is not None:
                names.append(rname)
                pers.append(per)
                roes.append(roe)

    if len(names) < 2:
        return [], []
    # scatter 포맷: [x1, y1, x2, y2, ...] (x=PER, y=ROE)
    return names, [v for pair in zip(pers, roes) for v in pair]


# ── 공개 API ────────────────────────────────────────────────────

def generate_chart(
    description: str,
    keyword: str,
    sector: str,
    context_text: str = "",
    out_dir: str | Path = ".",
    chart_idx: int = 1,
    run_id: str = "",
    collection_docs: list | None = None,
) -> str:
    """차트 설명 → Plotly PNG → 파일 경로. 실패 시 "".

    ★ 사용자 박제 2026-06-07 — collection_docs 인자 추가 + delta-aware 보강:
       ① context_text 짧으면 먼저 collection_docs 의 facts_for_chart 재사용
       ② 그래도 부족하면 collection_merger.request_more 로 JARVIS09 delta 호출
          (aspect="numeric_facts" — 공시·통계·시세 우선)
       ③ 마지막 폴백: 기존 _build_j09_context (시계열·KRX·market 구조화)
    """
    try:
        import time
        import numpy as np

        _rid = run_id or str(time.time_ns())

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        # ★ ERRORS [171] 2026-05-26: 파일명에 내용 해시 포함 (CLAUDE.md 박제 위반 수정)
        # 같은 chart_idx라도 description·run_id가 다르면 다른 파일 → 덮어쓰기·중복 방지
        _content_hash = hashlib.md5(
            f"{description}|{keyword}|{_rid}|{chart_idx}".encode()
        ).hexdigest()[:8]
        fname = out_path / f"chart_{chart_idx:02d}_{_content_hash}.png"

        # ── 컨텍스트 먼저 확보 → LLM 어드바이저가 실데이터 보고 타입 결정 ──
        colors = _derive_colors(keyword, sector, chart_idx, _rid)
        use_synth = False

        # ── ① collection_docs 우선 활용 (상위 호출자가 이미 수집한 것 재사용) ──
        if len(context_text) < 300 and collection_docs:
            try:
                from JARVIS06_IMAGE.collection_merger import facts_for_chart
                _facts = facts_for_chart(collection_docs, max_n=12)
                if _facts:
                    _docs_ctx = "[수집 사실 라인]\n" + "\n".join(_facts)
                    context_text = (context_text + "\n\n" + _docs_ctx).strip() if context_text else _docs_ctx
                    print(f"  ♻️ [chart_generator] collection_docs facts 재사용 → {len(context_text)}자")
            except Exception as _e:
                print(f"  ⚠️ [chart_generator] facts_for_chart 실패: {_e}")

        # ── ② delta 보강 — 그래도 부족하면 JARVIS09에 numeric_facts 추가 요청 ──
        if len(context_text) < 300:
            try:
                from JARVIS06_IMAGE.collection_merger import request_more as _req_more
                from JARVIS06_IMAGE.collection_merger import facts_for_chart as _ffc
                print(f"  🔄 [chart_generator] context 부족({len(context_text)}자) → JARVIS09 delta 요청 (aspect=numeric_facts)")
                _merged = _req_more(
                    theme=keyword, existing=collection_docs or [],
                    sector=sector, aspect="numeric_facts",
                )
                if _merged:
                    _facts2 = _ffc(_merged, max_n=12)
                    if _facts2:
                        _delta_ctx = "[수집 사실 라인]\n" + "\n".join(_facts2)
                        context_text = (context_text + "\n\n" + _delta_ctx).strip() if context_text else _delta_ctx
                        print(f"  ✅ [chart_generator] delta 보강 완료 → {len(context_text)}자")
            except Exception as _e:
                print(f"  ⚠️ [chart_generator] delta 요청 실패: {_e}")

        # ── ③ 마지막 폴백: 기존 _build_j09_context (시계열·KRX·market 등 구조화) ──
        if len(context_text) < 300:
            print(f"  📡 [chart_generator] 여전히 부족({len(context_text)}자) → 구조화 소스(ECOS/KRX/market) 보강...")
            _j09_ctx = _build_j09_context(keyword, description)
            if _j09_ctx:
                context_text = (context_text + "\n\n" + _j09_ctx).strip() if context_text else _j09_ctx
                print(f"  ✅ [chart_generator] 구조화 보강 완료 → {len(context_text)}자")

        # ── ★ LLM 어드바이저 — 실데이터 본 후 최적 차트 타입 판단 ──────────
        # _detect_type()은 키워드 매칭 fallback. LLM이 성공하면 LLM 판단 우선.
        with _used_types_lock:
            _used_snapshot = list(_used_types_by_run.get(_rid, []))
        try:
            from JARVIS06_IMAGE.chart_advisor import advise_chart_type as _advise
            _advised = _advise(description, keyword, sector, context_text, _used_snapshot)
        except Exception:
            _advised = ""

        if _advised:
            chart_type = _advised
            # 어드바이저 선택 타입 사용 이력 등록
            with _used_types_lock:
                _used_types_by_run.setdefault(_rid, []).append(chart_type)
            print(f"  🧠 [ChartAdvisor] '{description[:30]}...' → {chart_type.upper()}")
        else:
            # LLM 실패 시 키워드 매칭 fallback
            chart_type = _detect_type(description, chart_idx, _rid)
            print(f"  🔤 [_detect_type fallback] → {chart_type.upper()}")

        is_iso      = chart_type in ('iso_bar', 'iso_area', 'band_line')
        is_pie_like = chart_type in ('pie', 'donut')
        is_scatter  = chart_type == 'scatter'

        labels, values = _llm_extract_chart_data(
            description, keyword, sector, context_text, chart_type)

        # ★ ERRORS [175] 2026-05-26: LLM 추출 실패 시 [종목 데이터] 직접 파싱
        # LLM이 합성 데이터를 쓰기 전에 구조화 종목 컨텍스트에서 실수치 획득 시도.
        # 성공하면 use_synth=False → "참고용 예시 차트" 면책 문구 미표시.
        if not labels and "[종목 데이터]" in context_text:
            _parsed_l, _parsed_v = _parse_stock_context(context_text, description)
            if len(_parsed_l) >= 2:
                labels, values = _parsed_l, _parsed_v
                use_synth = False
                # 시계열·산점도 타입이면 횡단면 적합 타입으로 교체
                if chart_type in ('scatter', 'line', 'area', 'step', 'iso_area', 'combo'):
                    with _used_types_lock:
                        _lst = _used_types_by_run.setdefault(_rid, [])
                        # ① 원래 타입 제거
                        try: _lst.remove(chart_type)
                        except ValueError: pass
                        # ② ★ 제12조: bar 계열 이미 사용됐으면 bar 계열 재선택 금지
                        _bar_already = any(t in _BAR_FAMILY for t in _lst)
                        if _bar_already:
                            _cands = ('donut', 'pie', 'step', 'scatter', 'area')
                        else:
                            _cands = ('barh', 'bar', 'iso_bar', 'donut', 'pie', 'step')
                        chart_type = next((t for t in _cands if t not in _lst), _cands[0])
                        # ③ 새 타입 등록 (lock 안에서)
                        _lst.append(chart_type)
                    is_iso      = chart_type in ('iso_bar', 'iso_area')
                    is_scatter  = chart_type == 'scatter'
                    is_pie_like = chart_type in ('pie', 'donut')
                    print(f"    ✅ [종목데이터 직접파싱] {len(labels)}종목 → {chart_type.upper()} 차트")

        if not labels:
            if is_scatter:
                # 실데이터 PER+ROE 산점도 우선
                _per_roe_names, _per_roe_vals = _fetch_per_roe_scatter(context_text)
                if len(_per_roe_names) >= 2:
                    labels, values = _per_roe_names, _per_roe_vals
                    use_synth = False
                    print(f"    ✅ [chart_generator] PER/ROE 실데이터 {len(labels)}종목 → SCATTER")
                else:
                    # scatter 실데이터 없음 → barh/bar 타입 전환 + 종목 데이터 시도
                    with _used_types_lock:
                        _lst2 = _used_types_by_run.setdefault(_rid, [])
                        try: _lst2.remove('scatter')
                        except ValueError: pass
                        _bar_already2 = any(t in _BAR_FAMILY for t in _lst2)
                        _fb_type = next(
                            (t for t in (('donut', 'pie', 'step') if _bar_already2
                                         else ('barh', 'bar', 'donut', 'pie'))
                             if t not in _lst2),
                            'barh' if not _bar_already2 else 'donut',
                        )
                        _lst2.append(_fb_type)
                    chart_type = _fb_type
                    is_scatter = False
                    is_pie_like = chart_type in ('pie', 'donut')
                    _stock_l, _stock_v = _parse_stock_context(context_text, description)
                    if len(_stock_l) >= 2:
                        labels, values = _stock_l, _stock_v
                        use_synth = False
                        print(f"    ✅ [chart_generator] scatter→{chart_type.upper()} + 종목 실데이터 {len(labels)}개")
                    else:
                        _j9_l, _j9_v = _fetch_from_j09(keyword, description, chart_type)
                        if len(_j9_l) >= 2:
                            labels, values = _j9_l, _j9_v
                            use_synth = False
                        else:
                            print(f"  ⚠️ [chart_generator] scatter 실데이터 없음 — 차트 스킵")
                            return ""
            elif chart_type == 'band_line':
                # band_line: ECOS 금리 실데이터만 허용, 주가·합성 데이터 절대 금지
                _j9_l, _j9_v = _fetch_from_j09(keyword, description, chart_type)
                if len(_j9_l) >= 3:
                    labels, values = _j9_l, _j9_v
                    use_synth = False
                else:
                    print(f"  ⚠️ [chart_generator] band_line 실데이터 없음 — 차트 스킵")
                    return ""
            elif chart_type in ('line', 'area', 'step', 'iso_area', 'combo'):
                # 시계열: 개별 종목 주가 이력 우선 → 금융 지수 폴백
                labels, values = _fetch_stock_price_history(context_text)
                if labels:
                    use_synth = False
                    if chart_type in ('iso_area',):
                        chart_type = 'area'
                        is_iso = False
                    print(f"    ✅ [chart_generator] 주가 이력 실데이터 {len(labels)}포인트 → {chart_type.upper()}")
                else:
                    labels, values = _fetch_real_index_data(description, keyword)
                    if labels:
                        use_synth = False
                        if chart_type in ('iso_area',):
                            chart_type = 'area'
                            is_iso = False
                        print(f"    ✅ [chart_generator] 지수 실데이터 {len(labels)}포인트 → {chart_type.upper()}")
                    else:
                        _j9_l, _j9_v = _fetch_from_j09(keyword, description, chart_type)
                        if len(_j9_l) >= 3:
                            labels, values = _j9_l, _j9_v
                            use_synth = False
                        else:
                            print(f"  ⚠️ [chart_generator] 시계열 실데이터 없음 — 차트 스킵")
                            return ""
            else:
                # bar/barh/iso_bar/pie/donut/step: 종목 데이터 파싱
                labels, values = _parse_stock_context(context_text, description)
                if len(labels) >= 2:
                    use_synth = False
                    print(f"    ✅ [chart_generator] 종목 실데이터 {len(labels)}개 → {chart_type.upper()}")
                else:
                    _j9_l, _j9_v = _fetch_from_j09(keyword, description, chart_type)
                    if len(_j9_l) >= 2:
                        labels, values = _j9_l, _j9_v
                        use_synth = False
                    else:
                        print(f"  ⚠️ [chart_generator] 실데이터 없음 — 차트 스킵")
                        return ""

        # 타이틀 — scatter 합성 대체 시 안전한 표현으로 교체
        disp_title = description
        if use_synth and re.search(r'산점도|PER.*ROE|ROE.*PER|분포도', description):
            stock_m2   = re.search(r'(\d+)\s*종목', description)
            n_str      = f"{stock_m2.group(1)}종목 " if stock_m2 else ""
            disp_title = f"{n_str}주요 투자지표 상대 비교 (참고용)"
        title_short = disp_title[:42] + ('…' if len(disp_title) > 42 else '')

        # ── 아이소메트릭 3D 렌더러 ─────────────────────────────────
        # ── 공통 가드: labels/values 길이 불일치 방지 (#362 shape mismatch)
        if labels and values:
            _n = min(len(labels), len(values))
            labels, values = labels[:_n], values[:_n]

        # ── 데이터 기반 chart_type 재조정 (labels/values 확정 후) ──
        if labels and values:
            chart_type = _data_override_type(chart_type, labels, values, description, _rid)
            is_iso      = chart_type in ('iso_bar', 'iso_area', 'band_line')
            is_pie_like = chart_type in ('pie', 'donut')
            is_scatter  = chart_type == 'scatter'

        # pie/donut 음수 값 제거 (#373 Wedge sizes must be non negative)
        if is_pie_like and values:
            values = [max(0.0, v) for v in values]
            if not any(v > 0 for v in values):
                values = [1.0] * len(labels)  # 모두 0이면 균등 분포

        if is_iso and labels and values:
            from JARVIS06_IMAGE.isometric_charts import (
                make_iso_bar_chart, make_iso_area_chart, make_band_line_chart)
            if chart_type == 'iso_bar':
                result = make_iso_bar_chart(
                    labels, values, title_short, keyword, sector,
                    str(fname), run_id=_rid)
            elif chart_type == 'band_line':
                result = make_band_line_chart(
                    labels, values, title_short, keyword, sector,
                    str(fname), run_id=_rid)
            else:
                result = make_iso_area_chart(
                    labels, values, title_short, keyword, sector,
                    str(fname), run_id=_rid)
            if result:
                # ★ PNG 저장 성공 후에만 글로벌 히스토리 기록
                _record_global_type(chart_type)
                print(f"    chart_{chart_idx:02d}.png [{chart_type.upper()}]")
                return str(Path(result).resolve())
            # 실패 시 일반 Plotly 폴백
            chart_type = 'bar' if chart_type == 'iso_bar' else 'area'

        # ── Plotly 렌더러 ──────────────────────────────────────────
        fig = _make_plotly_fig(
            chart_type, labels, values, colors,
            title_short, keyword, sector, use_synth, run_id=_rid,
        )

        fig.write_image(str(fname), scale=2)

        # ★ ERRORS [172] 2026-05-26: 파일 MD5 중복 감지 → 자동 재생성 (최대 2회)
        dup_src = _register_chart_hash(_rid, str(fname))
        if dup_src:
            print(f"  ♻️ [chart_generator] CHART_{chart_idx} 중복 감지 → 타입 강제 변경 재생성")
            for _retry in range(2):
                # 강제로 다른 차트 타입 선택
                with _used_types_lock:
                    rotation = _shuffled_types(_rid)
                    used_now = list(_used_types_by_run.get(_rid, []))
                    alt_type = next(
                        (t for t in rotation if t != chart_type and t not in used_now),
                        rotation[(_retry + chart_idx) % len(rotation)]
                    )
                    _used_types_by_run.setdefault(_rid, []).append(alt_type)

                alt_colors = _derive_colors(keyword, sector, chart_idx + 50 + _retry, _rid)
                _alt_hash = hashlib.md5(
                    f"{description}|{keyword}|{_rid}|{chart_idx}|retry{_retry}".encode()
                ).hexdigest()[:8]
                fname = out_path / f"chart_{chart_idx:02d}_{_alt_hash}.png"
                # scatter 재시도는 데이터 포맷 불일치 → skip
                if alt_type == 'scatter':
                    continue
                # scatter 원본이면 x값(PER)만 추출해 bar계열에 사용
                _retry_vals = values[0::2] if chart_type == 'scatter' and values else values
                alt_fig = _make_plotly_fig(
                    alt_type, labels, _retry_vals, alt_colors,
                    title_short, keyword, sector, use_synth, run_id=_rid,
                )
                alt_fig.write_image(str(fname), scale=2)
                dup2 = _register_chart_hash(_rid, str(fname))
                if not dup2:
                    chart_type = alt_type
                    print(f"  ✅ [chart_generator] CHART_{chart_idx} 재생성 성공 [{alt_type.upper()}]")
                    break
                print(f"  ⚠️ [chart_generator] 재생성 {_retry+1}회도 중복 — 다음 시도")

        # ★ PNG 저장 성공 후에만 글로벌 히스토리 기록 (실패·스킵된 타입은 제외)
        _record_global_type(chart_type)
        print(f"    chart_{chart_idx:02d}.png [{chart_type.upper()}]")
        return str(fname.resolve())

    except Exception as e:
        print(f"  ⚠️ [chart_generator] CHART_{chart_idx} 생성 오류: {e}")
        _g_report("writer", e, module=__name__)
        return ""


__all__ = ["generate_chart"]
