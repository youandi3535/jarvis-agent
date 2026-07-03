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
# ★ yfinance 단일 진입점 → JARVIS09 공개 API (2026-06-29 — provider 내부 직접 import 제거)
from JARVIS09_COLLECTOR import (
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
# ★ 제12조 스타일 중복 금지: bar/barh/iso_bar/combo 는 같은 "막대 계열" — 1개 run에 1종만 허용
#   (combo 는 합성 추세선 제거 후 실데이터 막대만 렌더 → bar 와 동일 계열, 2026-07-02)
_BAR_FAMILY = frozenset({'bar', 'barh', 'iso_bar', 'combo'})

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



# ── ECOS 지표 로테이션 — 같은 지표 반복 방지 ─────────────────────────────
# 최근 사용한 ECOS 지표명을 추적하여 순환 선택.
_USED_ECOS_INDICATORS: list[str] = []   # 최근 사용 ECOS 블록명 (시간순)
_USED_ECOS_LOCK = _threading.Lock()
_ECOS_INDICATOR_POOL = ["기준금리", "소비자물가", "원/달러", "수출금액", "실업률"]



# ── JARVIS09 컨텍스트 캐시 — TTL 1시간 (ERRORS [247]) ──────────────────
# 같은 글 내 여러 차트 슬롯에서 중복 수집 방지 (유지).
# 단, 1시간 지나면 만료 → 다음 글에서 최신 데이터 재수집.
_J09_CTX_CACHE: dict[str, tuple[str, float]] = {}   # keyword → (text, timestamp)
_J09_CTX_CACHE_LOCK = _threading.Lock()
_J09_CTX_TTL = 3600.0   # 1시간

# ★ ERRORS [172] 2026-05-26: 런 내 파일 MD5 중복 감지 레지스트리
_run_file_hashes: dict[str, dict[str, str]] = {}  # run_id → {md5: fname}
_run_hash_lock = _threading.Lock()






# ── 테마 상수 (고정값은 텍스트·그리드만 — 배경은 run_id 기반 동적 생성) ─────
_BG_PALETTE = [
    "#ffffff", "#fafcff", "#fff8f0", "#f0f8ff", "#f5fff5",
    "#fffbf0", "#f8f0ff", "#f0fffc", "#fff0f8", "#f2f4f8",
]
_PANEL_PALETTE = [
    "#fafafa", "#f5f8ff", "#fff5ee", "#edf5ff", "#f0fff4",
    "#fffde7", "#f3e8ff", "#e8fff9", "#ffe8f5", "#eef0f5",
]
_GRID   = "#e9ecef"
_TEXT_H = "#212529"
_TEXT_B = "#6c757d"
_FONT   = "Apple SD Gothic Neo, Noto Sans KR, NanumGothic, sans-serif"




_BG     = _BG_PALETTE[0]   # 기본값 (run_id 미지정 시)
_PANEL  = _PANEL_PALETTE[0]














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








# ── Plotly 차트 생성 ─────────────────────────────────────────────





# ── LLM 데이터 추출 ──────────────────────────────────────────────



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




# ── 종목 데이터 직접 파싱 ────────────────────────────────────────
# context_text 에 '[종목 데이터]' 블록이 있으면 LLM 없이 직접 파싱.
# _stocks_text() 가 생성하는 구조화 텍스트 형식을 regex 로 추출.
# ★ ERRORS [175] 2026-05-26 — LLM 추출 실패 시 합성 데이터 → 면책 문구 방지




















# ★ 경제 일반 키워드 — 이 키워드가 포함된 차트에서 collect_stocks_data 스킵 ([281] 2026-06-08)
_GENERAL_ECON_KWS = frozenset([
    "줄인상", "물가", "인플레", "생필품", "외식", "공공요금", "cpi", "소비자물가",
    "가격인상", "가격 인상", "생활물가", "식품값", "장바구니", "라면", "과자",
    "밀키트", "배달비", "관리비", "전기요금", "가스요금", "수도요금", "버스요금",
    "지하철요금", "교통요금", "금시세", "금값", "금 시세", "원자재", "유가",
    "환율", "달러", "원달러", "무역수지", "경상수지", "gdp", "성장률", "실업",
    "취업률", "고용률", "임금", "연봉", "최저임금",
])










# ── 수치 단위 추론 (모든 숫자엔 단위 — 사용자 박제 2026-06-30) ──────────────


# ── JARVIS09 협력 — 실데이터 부족 시 요청 (팩트 데이터: API자동설치·뉴스·논문·기사) ──────
#   ★ 사용자 박제 2026-06-30: 이미지 생성 중 데이터 부족하면 JARVIS09 에 요청 → JARVIS09 가
#   어떻게든 팩트 기반 실데이터(출처·단위 박제)를 수집해 보내줌 → 그 데이터로만 차트.
#   거짓 수치 절대 금지: collect_chart_data 는 실제 등장 수치(URL 출처)만 반환.
_CHART_DATA_POOL: dict = {}   # run_id -> [datasets] (run 당 1회 수집, 슬롯마다 부분집합 회전)
_SESSION_POOL: list = []       # ★ writer 가 대본 전 미리 수집한 풀 (데이터-우선: Pass-2 재수집 0)
_SESSION_POOL_SET: bool = False  # ★ writer 가 명시적으로 풀을 등록했는지 (빈 풀도 존중 — garbage 폴백 차단)
# ★ 사용 인덱스는 *풀 정체* 별 (ERRORS [313]): 전역 set 이면 재작성·타 플랫폼의 *새 풀* 이
#   이전 시도의 인덱스에 굶주림 (4/7→1/7→0/7 사고). "session" = 세션풀, "run:{id}" = 런 풀.
_USED_POOL_IDX: dict = {}
_POOL_LOCK = _threading.Lock()   # 병렬 슬롯 워커의 중복 수집 방지 (동일 run 3중 수집 사고)


def set_session_pool(pool):
    """데이터-우선 — writer 가 대본 작성 전 수집한 실데이터 풀을 등록. Pass-2 가 재수집 없이 사용.
    ★ 빈 리스트로 등록해도 '명시적 등록'으로 기록 → chart_generator 가 per-chart garbage 수집으로
    빠지지 않고 *오직* 이 풀만 사용(없으면 차트 스킵→AI사진)."""
    global _SESSION_POOL, _SESSION_POOL_SET
    _SESSION_POOL = list(pool or [])
    _SESSION_POOL_SET = True
    _USED_POOL_IDX.clear()   # 새 글 → 사용 인덱스 초기화 (반복 추적 리셋)


def clear_session_pool():
    global _SESSION_POOL, _SESSION_POOL_SET
    _SESSION_POOL = []
    _SESSION_POOL_SET = False
    _USED_POOL_IDX.clear()


def _collect_data_fallback(keyword, sector, description, chart_idx, out_path, run_id,
                           seed_datasets=None):
    """실데이터 경로 실패 시 JARVIS09 collect_chart_data 요청 → 검증 실데이터로 인포그래픽.
    ★ 데이터-우선: writer 가 set_session_pool() 로 미리 수집한 풀이 있으면 재수집 없이 사용.
    ★ seed_datasets (ERRORS [313]): 호출자가 이미 보유한 확실한 실데이터(테마 종목 시세 등)
      — 웹 수집과 *합류*. 테마주 글이 손에 쥔 시세를 두고 굶는 사고 차단."""
    try:
        with _POOL_LOCK:   # 병렬 워커 동시 진입 → 같은 run 3중 수집 방지 (ERRORS [313])
            pool = _SESSION_POOL or _CHART_DATA_POOL.get(run_id)
            # ★ writer 가 세션풀을 명시 등록(빈 풀 포함)했으면 per-chart 재수집 금지 — garbage 차단.
            if not pool and not _SESSION_POOL_SET:
                from JARVIS09_COLLECTOR import collect_chart_data
                # ★ 테마 앵커: 섹션 description 이 드리프트해도 *주제(keyword)* 로 수집 → 주제 실데이터 보장.
                res = collect_chart_data(keyword, sector=sector, description=keyword,
                                         max_datasets=24)   # ★ 풍부 원칙 [314] — 슬롯 7개+재작성 여유
                collected = (res or {}).get("datasets") or []
                seed = list(seed_datasets or [])
                _titles = {str(d.get("title", "")) for d in seed}
                pool = seed + [d for d in collected if str(d.get("title", "")) not in _titles]
                _CHART_DATA_POOL[run_id] = pool
                print(f"  🕸️ [chart_generator→JARVIS09] '{keyword}' 실데이터 요청 "
                      f"→ 수집 {len(collected)} + 종목시세 승격 {len(seed)} = {len(pool)}개 dataset")
        if not pool:
            print(f"  ⚠️ [chart_generator] '{keyword}' 게이트 실데이터 0 — 차트 스킵(거짓·무관 < 차트 없음)")
            return ""
        # ★ C1 배치 설계 (사용자 박제 2026-07-02) — 글당 1회 LLM 으로 pool 전체 인포그래픽 설계.
        #   idempotent(run_id 당 1회) → 이후 각 차트의 generate_infographic 은 캐시 사용(LLM 0).
        try:
            from JARVIS06_IMAGE.infographic_engine import prime_batch_designs
            prime_batch_designs(run_id, pool, keyword)
        except Exception:
            pass
        n = len(pool)
        # ★ 반복 금지 (사용자 박제 2026-07-01): 같은 데이터셋을 여러 슬롯에 반복(중복 차트)하지 않는다.
        #   used-set 은 *이 풀* 스코프 (ERRORS [313]) — 재작성/타 플랫폼의 새 풀은 새 추적.
        _ukey = "session" if _SESSION_POOL else f"run:{run_id}"
        with _POOL_LOCK:
            _used = _USED_POOL_IDX.setdefault(_ukey, set())
            _avail = [i for i in range(n) if i not in _used]
            if not _avail:
                print(f"  ℹ️ [chart_generator] 실데이터 {n}개 모두 소진 → 슬롯 {chart_idx}는 AI 사진 대체(반복 금지)")
                return ""
            _pick = _avail[0]
            _used.add(_pick)
        one = pool[_pick]
        from JARVIS06_IMAGE.infographic_engine import generate_infographic
        p = generate_infographic(str(one.get("title", keyword))[:36], "실데이터 기반", [one],
                                 run_id=run_id, slot_key=str(chart_idx), out_dir=out_path,
                                 context=f"{keyword} — {one.get('title','')}")
        if p and Path(p).exists():
            print(f"    chart_{chart_idx:02d} → 85점 인포그래픽 [{one.get('title','')[:20]}]")
            return str(Path(p).resolve())
    except Exception as e:
        print(f"  ⚠️ [chart_generator] collect_chart_data 폴백 실패: {e}")
    return ""


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
    seed_datasets: list | None = None,
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

        # ★★ 데이터 단일 경로 (사용자 박제 2026-07-01): 경제 차트 데이터는 *오직* 관련성·단위
        #   게이트를 통과한 실데이터(collect_chart_data: 세션풀 우선, 없으면 주제 수집)만 사용한다.
        #   시장지수 dump(_fetch_from_j09: S&P500·NASDAQ)·본문추출·합성 등 *비게이트 경로* 는
        #   '지역화폐 발행액'인데 S&P500 데이터 같은 *주제 불일치 garbage* 를 만들어 전면 차단.
        #   게이트 통과 실데이터가 없으면 차트를 만들지 않고 "" 반환 → 상위에서 AI 사진으로 대체.
        _gated = _collect_data_fallback(keyword, sector, description, chart_idx, out_path, _rid,
                                        seed_datasets=seed_datasets)
        if _gated:
            return _gated
        print(f"  ⚠️ [chart_generator] '{keyword}' 게이트 통과 실데이터 0 → 차트 스킵 (AI 사진 대체, garbage 차단)")
        return ""

    except Exception as e:
        print(f"  ⚠️ [chart_generator] CHART_{chart_idx} 생성 오류: {e}")
        _g_report("writer", e, module=__name__)
        return ""


def clear_session_cache() -> None:
    """글 작성 세션 시작 전 인메모리 캐시 전체 초기화.

    대상:
      - _J09_CTX_CACHE       : keyword별 JARVIS09 컨텍스트 캐시
      - _GLOBAL_TYPE_HISTORY : 전역 차트 타입 이력 (글 간 스타일 중복 방지용)
      - _used_types_by_run   : 실행별 차트 스타일 추적

    호출 위치: 글 작성 최초 진입점 (run_all_themes / ts_generate_draft / nv_generate_draft)
    """
    global _GLOBAL_TYPE_HISTORY
    with _J09_CTX_CACHE_LOCK:
        _J09_CTX_CACHE.clear()
    _GLOBAL_TYPE_HISTORY.clear()
    _used_types_by_run.clear()
    with _POOL_LOCK:                 # 런 풀·사용 인덱스도 새 글에서 리셋 (ERRORS [313])
        _CHART_DATA_POOL.clear()
        _USED_POOL_IDX.clear()
    print("  🧹 [chart_generator] 글 세션 캐시 초기화 완료")


__all__ = ["generate_chart", "clear_session_cache"]
