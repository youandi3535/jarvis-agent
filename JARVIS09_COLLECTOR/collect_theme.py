"""
collect_theme.py - Market Signal v11
수정사항:
1. 데이터 hallucination 방지: writer가 데이터 절대 변경 불가, 수치는 툴 결과만 사용
2. 플레이스홀더 교체 강화: <p>{{IMG:...}}</p>, 공백 포함 패턴 모두 정규식으로 처리
3. writer task에 실제 데이터 직접 주입 (context 방식)
"""

import os, io, base64, re, random as _rnd, sys
from pathlib import Path
from datetime import datetime

# subprocess로 실행될 때 jarvis-agent 루트를 sys.path에 추가 (JARVIS06_IMAGE 접근)
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 글자수 정책은 length_manager 단일 진입점 — 모듈 레벨 import
try:
    from JARVIS02_WRITER import length_manager as _LM
except ImportError:
    import length_manager as _LM

_CAP_DESC = {
    'overview':     '전체 투자 포인트 요약 인포그래픽',
    'radar':        '5개 지표 레이더 차트',
    'factors':      '상승·하락 요인 분석',
    'timeline':     '투자 단계별 체크리스트',
    'mechanism':    '테마 작동 구조 도식',
    'usecase':      '주요 활용 분야',
    'history':      '발전 역사 타임라인',
    'keyword':      '핵심 키워드 모음',
    'terms':        '핵심 투자 용어 3가지',
    'profit_loss':  '흑자/적자 종목 현황',
    'mktcap':       '시가총액 비교',
    'per':          'PER 밸류에이션 비교',
    'profitability':'수익성 지표 비교',
    'revenue':      '매출·순이익 비교',
    'return3m':     '3개월 수익률 비교',
    'risk':         '종목별 투자 위험도',
    'portfolio':    '포트폴리오 전략',
    'principle':    '투자 원칙',
}


def _cap(key: str, t: str = '', **kw) -> str:
    """차트 캡션 LLM 동적 생성 — 매번 다른 표현."""
    from shared.llm import invoke_text as _llm
    desc = _CAP_DESC.get(key, key)
    if key == 'profit_loss' and kw:
        desc = f"흑자 {kw.get('p','?')}개/적자 {kw.get('l','?')}개 종목 현황"
    theme_ctx = f"'{t}' 테마 " if t else ""
    data_ctx = ', '.join(f'{k}={v}' for k, v in kw.items()) if kw and key != 'profit_loss' else ''
    extra = f" 데이터: {data_ctx}." if data_ctx else ""
    return _llm(
        "writer_fast",
        f"{theme_ctx}블로그 차트 캡션 짧은 {_LM.CHART_CAPTION_SENTS}문장(약 {_LM.CHART_CAPTION_CHARS}자). 차트: {desc}.{extra} 해요체. 문장만 출력.",
        max_tokens=40, temperature=0.8
    ) or f"{theme_ctx}{desc}"

import numpy as np
import pandas as pd
# ── ADR 008 Phase 5 (사용자 박제 2026-05-17) — matplotlib 위임 ──
# 옛: matplotlib·plt·fm·mpatches·FancyBboxPatch·Circle 직접 import
#     → 차트 함수들이 모두 JARVIS06_IMAGE.theme_charts 에 이관됨 (이미 _mpl_setup 으로 Agg 백엔드 설정).
# 새: JARVIS06_IMAGE.theme_charts 만 import — collect_theme.py 안에서 matplotlib 직접 사용 0.
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ── 차트·인포그래픽 함수 전체를 JARVIS06_IMAGE.theme_charts 에서 임포트 ──
# (JARVIS06_IMAGE 가 matplotlib Agg 백엔드 + 폰트 설정 자동 수행)
from JARVIS06_IMAGE.theme_charts import (
    _cap, set_font, fig_to_b64, wrap_img, CHART_STORE,
)
set_font()
INFOG_STORE = {}
COLLECTED_DATA = {}

W = 'white'; PURPLE = '#667eea'; DARK = '#1a1a2e'

# ════════════════════════════════════════════════════════
#  인포그래픽 함수들 — JARVIS06_IMAGE.theme_charts 에서 임포트됨
# ════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════
#  재무 계산
# ════════════════════════════════════════════════════════
def _naver_fin(code: str) -> dict:
    """네이버 금융 1순위 - 최근 분기 기준 재무 데이터 수집"""
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Referer': 'https://finance.naver.com'
        }
        result = {}
        url = f"https://finance.naver.com/item/main.naver?code={code}"
        r = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, 'html.parser')

        def clean(txt):
            return txt.strip().replace(',','').replace('억','').replace('%','').replace('배','').replace('원','')

        for row in soup.select('tr'):
            th = row.select_one('th')
            tds = row.select('td')
            if not th or not tds: continue
            label = th.text.strip()
            # 인덱스 0 = 현재 종목 최근 분기 값
            vals = [clean(td.text) for td in tds]
            if not vals or not vals[0] or vals[0] in ['-', 'N/A', '']: continue

            try:
                v = float(vals[0])
                if label == '매출액(억)' and not result.get('revenue'):
                    result['revenue'] = v * 1e8
                elif label == '영업이익(억)' and not result.get('op_income'):
                    result['op_income'] = v * 1e8
                elif label == '당기순이익(억)' and not result.get('net_income'):
                    result['net_income'] = v * 1e8
                elif label == 'ROE(%)' and not result.get('roe'):
                    result['roe'] = v / 100
                elif label == 'PER(배)' and not result.get('per'):
                    if v > 0:
                        result['per'] = v
                elif label == 'PER(%)' and not result.get('per'):
                    if v > 0:
                        result['per'] = v
            except Exception: pass

        # 영업이익률 계산
        if result.get('op_income') and result.get('revenue') and result['revenue'] != 0:
            result['op_margin'] = result['op_income'] / result['revenue']

        # 현재가/시총/종목명
        # 종목명은 페이지 타이틀에서 추출
        try:
            title_el = soup.select_one('title')
            if title_el:
                title_txt = title_el.text.strip()
                # "종목명 : 네이버 금융" 형태
                corp_name = title_txt.split(':')[0].strip()
                if corp_name and corp_name != '네이버 금융':
                    result['corp_name'] = corp_name
        except Exception: pass
        
        for row in soup.select('tr'):
            th = row.select_one('th')
            tds = row.select('td')
            if not th or not tds: continue
            label = th.text.strip()
            vals = [clean(td.text) for td in tds]
            if not vals or not vals[0]: continue
            try:
                if label == '현재가' and not result.get('price'):
                    result['price'] = float(vals[0])
                elif '시가총액(억)' in label and not result.get('marcap'):
                    result['marcap'] = float(vals[0]) * 1e8
            except Exception: pass

        # PER - 별도 파싱 (N/A면 적자)
        if not result.get('per'):
            for row in soup.select('tr'):
                th = row.select_one('th')
                if th and 'PERlEPS' in th.text:
                    td = row.select_one('td')
                    if td:
                        txt = clean(td.text.split('l')[0])
                        if txt and txt != 'NA' and txt != '':
                            try: result['per'] = float(txt)
                            except Exception: pass
                    break

        # ── ★ 재무 기간(period) 포착 — vals[0] 이 *어느 시점* 값인지 명시 (거짓 '연매출'
        #   라벨 방지, ERRORS [367]). 매출액 행을 가진 표의 thead 기간 헤더(YYYY.MM) 중
        #   *td[0] 과 정렬되는 첫 기간* = 파서가 취한 vals[0] 의 실제 기간. 최근 분기/연간을
        #   라벨이 그대로 말해줌(예 '2025.09'=3분기, '2024.12'=연간결산). ──
        try:
            import re as _re_per
            _fin_tbl = None
            for _t in soup.select('table'):
                if _t.find(string=_re_per.compile('매출액')):
                    _fin_tbl = _t
                    break
            if _fin_tbl is not None:
                _head = _fin_tbl.select_one('thead') or _fin_tbl
                _pers = [th.get_text(' ', strip=True) for th in _head.select('th')
                         if _re_per.search(r'20\d\d[.\-/]\d{1,2}', th.get_text(' ', strip=True))]
                if _pers:
                    _m0 = _re_per.search(r'20\d\d[.\-/]\d{1,2}', _pers[0])  # td[0] 정렬 = 첫 기간
                    if _m0:
                        result['fin_period'] = _m0.group(0).replace('-', '.').replace('/', '.')
        except Exception:
            pass

        return result
    except Exception as e:
        return {}


# ── ★ 공식 테마 카탈로그 (사용자 박제 2026-07-03) ─────────────────────────────
# "테마주는 KRX/네이버 금융 공식 테마를 먼저 확인하고, 미작성 테마로 주제를 선정한다."
# 종전엔 1페이지(40개)만 읽어 공식 테마 280여 개의 1/7만 매칭 — 전 페이지 수집 + 캐시.
_THEME_CATALOG_CACHE: dict = {"themes": {}, "ts": 0.0}
_THEME_CATALOG_TTL = 3600.0   # 1시간


def _fetch_naver_theme_catalog(timeout: int = 8) -> dict:
    """네이버 금융 공식 테마 전체 카탈로그 {테마명: 테마번호} — 전 페이지 + 1h 캐시."""
    import time as _t
    if _THEME_CATALOG_CACHE["themes"] and _t.time() - _THEME_CATALOG_CACHE["ts"] < _THEME_CATALOG_TTL:
        return _THEME_CATALOG_CACHE["themes"]
    import requests
    from bs4 import BeautifulSoup
    _hdrs = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://finance.naver.com/',
    }
    themes: dict = {}
    try:
        for page in range(1, 11):   # 공식 테마 ~8페이지 — 여유 10
            r = requests.get(f'https://finance.naver.com/sise/theme.naver?page={page}',
                             headers=_hdrs, timeout=timeout)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.content, 'html.parser')
            table = soup.find('table', {'class': 'type_1'})
            if not table:
                break
            before = len(themes)
            for a in table.find_all('a', href=re.compile(r'sise_group_detail')):
                tname = a.get_text(strip=True)
                m = re.search(r'no=(\d+)', a.get('href', ''))
                if tname and m:
                    themes[tname] = m.group(1)
            if len(themes) == before:   # 새 항목 없음 = 마지막 페이지 지나침
                break
    except Exception as e:
        print(f"  ⚠️ [theme_catalog] 수집 오류: {e}")
    if themes:
        _THEME_CATALOG_CACHE["themes"] = themes
        _THEME_CATALOG_CACHE["ts"] = _t.time()
        print(f"  📚 [theme_catalog] 네이버 금융 공식 테마 {len(themes)}개 로드")
    return themes or dict(_THEME_CATALOG_CACHE["themes"])


def _ko_common_len(a: str, b: str) -> int:
    """두 문자열 간 공통 한국어 부분 문자열 최대 길이 (공식 테마 매칭 기준)."""
    a_ko = re.sub(r'[^가-힣]', '', a)
    b_ko = re.sub(r'[^가-힣]', '', b)
    if not a_ko or not b_ko:
        return 0
    best = 0
    for L in range(2, min(len(a_ko), len(b_ko)) + 1):
        for i in range(len(a_ko) - L + 1):
            if a_ko[i:i + L] in b_ko:
                best = max(best, L)
    return best


def is_official_theme(theme_name: str) -> bool:
    """★ 공식 테마 판정 (사용자 박제 2026-07-03) — 네이버 금융 공식 테마 카탈로그와
    한국어 3자+ 매칭. 카탈로그 수집 실패 시 True(fail-open — 네트워크 장애로 전면
    차단되는 것 방지, 실행 시 게이트가 2차 방어)."""
    catalog = _fetch_naver_theme_catalog()
    if not catalog:
        return True
    _ko = re.sub(r'[^가-힣]', '', theme_name)
    return any(_ko_common_len(theme_name, t) >= 3
               or (_ko and _ko == re.sub(r'[^가-힣]', '', t))   # 2글자 테마 정확 일치 (예: 리튬)
               for t in catalog)


def _naver_fin_theme_search(theme_name: str, timeout: int = 8) -> list:
    """네이버 금융 테마 목록에서 유사 테마를 찾아 종목 코드 목록 반환.

    흐름:
      1. finance.naver.com/sise/theme.naver 에서 40개 테마 수집
      2. theme_name 키워드 fuzzy match (공통 2글자+ 단어 수 최대화)
      3. 매칭 테마 detail 페이지에서 종목명+코드 추출

    Returns list[{"name", "code", "ticker"}] — 빈 리스트 = 미매칭 or 오류
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        _hdrs = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Referer': 'https://finance.naver.com/',
        }

        # 1) 테마 목록 수집 — ★ 전 페이지 카탈로그 (사용자 박제 2026-07-03)
        naver_themes = _fetch_naver_theme_catalog(timeout=timeout)
        if not naver_themes:
            return []

        # 2) fuzzy match — 한국어 키워드 공통 부분 기준 (영어 약어 오매칭 방지)
        #    전략: 한국어 2자+ 공통 부분 문자열 슬라이딩 → 스코어. 영어 단어는 별도 처리.
        def _ko_score(a: str, b: str) -> int:
            """두 문자열 간 공통 한국어 부분 문자열 최대 길이."""
            # 한국어 문자만 추출
            a_ko = re.sub(r'[^가-힣]', '', a)
            b_ko = re.sub(r'[^가-힣]', '', b)
            if not a_ko or not b_ko:
                return 0
            best = 0
            for L in range(2, min(len(a_ko), len(b_ko)) + 1):
                for i in range(len(a_ko) - L + 1):
                    if a_ko[i:i+L] in b_ko:
                        best = max(best, L)
            return best

        best_no, best_name, best_score = None, None, 0
        for tname, tno in naver_themes.items():
            score = _ko_score(theme_name, tname)
            if score > best_score:
                best_score = score
                best_no = tno
                best_name = tname

        # 한국어 3자 미만 매칭은 너무 느슨함 (2자 = "상장"·"항공" 등 단독 단어는 오매칭 위험)
        # → ★ LLM 의미 매칭 폴백 (사용자 박제 2026-07-04, ERRORS [352]): 부분문자열이
        #   실패해도 테마 키워드에 가장 부합하는 *네이버 공식 테마* 를 LLM 이 카탈로그에서
        #   직접 고른다. "테마주명이 있으면 그 안에 종목도 있다" — 종목 작문이 아니라 공식
        #   테마의 *실제 구성종목* 을 가져오는 것. (예: '백신/진단시약/방역(신종플루, AI 등)'
        #   처럼 표기가 조금 달라 부분문자열 매칭이 어긋나도 공식 테마로 정확히 매핑.)
        if best_score < 3 or not best_no:
            try:
                from shared.llm import invoke_text as _llm
                _names = list(naver_themes.keys())
                _numbered = "\n".join(f"{i+1}. {nm}" for i, nm in enumerate(_names))
                _resp = (_llm(
                    "router",
                    f"아래 [네이버 금융 공식 테마 목록] 중 '{theme_name}' 과 가장 부합하는 테마 "
                    f"하나의 *번호만* 출력하라(예: 12). 의미상 부합하는 테마가 전혀 없으면 0. "
                    f"숫자만, 다른 말 금지.\n\n[목록]\n{_numbered}",
                ) or "").strip()
                _mm = re.search(r'\d+', _resp)
                _idx = (int(_mm.group()) - 1) if _mm else -1
                if 0 <= _idx < len(_names):
                    best_name = _names[_idx]
                    best_no = naver_themes[best_name]
                    print(f"  🔍 [naver_theme] LLM 의미매칭 '{theme_name}' → '{best_name}' (no={best_no})")
            except Exception as _e:
                print(f"  ⚠️ [naver_theme] LLM 의미매칭 실패: {_e}")
            if not best_no:
                print(f"  ℹ️ [naver_theme] '{theme_name}' — 부합 공식 테마 없음 (부분문자열 best={best_score})")
                return []

        print(f"  🔍 [naver_theme] '{theme_name}' → 네이버 금융 '{best_name}' (no={best_no}, score={best_score})")

        # 3) 종목 추출 — ★ 재시도 (사용자 박제 2026-07-04, ERRORS [352]): 공식 테마로
        #    매칭됐는데도 상세페이지 fetch 가 *일시* 실패/빈응답이면 종목 0개로 테마가 통째로
        #    폐기되던 사고 방지. "테마주명이 있으면 그 안에 종목도 있다" — 네트워크 순간
        #    장애로 그 종목을 못 가져오는 것뿐이므로 최대 3회 백오프 재시도.
        import time as _t2
        stocks = []
        for _try in range(3):
            try:
                r2 = requests.get(
                    f'https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={best_no}',
                    headers=_hdrs, timeout=timeout,
                )
                soup2 = BeautifulSoup(r2.content, 'html.parser')
                _found = []
                for a in soup2.find_all('a', href=re.compile(r'item/main\.naver\?code=')):
                    code = re.search(r'code=(\d{6})', a.get('href', ''))
                    if not code:
                        continue
                    code = code.group(1)
                    name = a.get_text(strip=True)
                    if name and code:
                        # KS/KQ 구분 (코스닥 0으로 시작하는 경우 많음 — 간단 휴리스틱)
                        suffix = '.KQ' if code.startswith(('0', '1', '2', '3')) and int(code) < 400000 else '.KS'
                        _found.append({
                            "name": name, "code": code,
                            "ticker": f"{code}{suffix}", "_naver_theme": best_name,
                        })
                if _found:
                    stocks = _found
                    break
                print(f"  ⚠️ [naver_theme] 상세페이지 종목 0개 (시도 {_try+1}/3) — 재시도")
            except Exception as _e2:
                print(f"  ⚠️ [naver_theme] 상세페이지 fetch 실패 (시도 {_try+1}/3): {_e2}")
            _t2.sleep(1.5 * (_try + 1))

        print(f"  ✅ [naver_theme] 네이버 금융 '{best_name}' 종목 {len(stocks)}개 확보")
        return stocks

    except Exception as e:
        print(f"  ⚠️ [naver_theme] Naver Finance 테마 검색 실패: {e}")
        return []




# ★ PER 이상치 상한 (2026-07-02) — 순이익이 0 에 가까우면 PER 이 수백~수천으로
#   폭증(예: 463.9배)해 차트·표를 오도한다. 이 값 초과 PER 은 신뢰 불가 → N/A 처리.
_PER_OUTLIER_MAX = 200.0


def _cap_per(per):
    """★ ERRORS [347] — PER 이상치 상한을 *모든* 종목수집 경로에 일관 적용.
    calc_fin 은 캡하지만 SSOT 경로(collect_stocks_data._enrich→_naver_fin)가
    원값을 그대로 흘리면, 218.5배 같은 신뢰 불가 PER 이
    본문·grounding 코퍼스·차트에 유입돼 오도 + 사실성 게이트 혼란을 유발했다.
    흑자 무관, per<=0 또는 >_PER_OUTLIER_MAX 는 None(N/A) 로 정규화한다."""
    try:
        p = float(per)
    except (TypeError, ValueError):
        return None
    return round(p, 1) if 0 < p <= _PER_OUTLIER_MAX else None



# ════════════════════════════════════════════════════════════════
#  ★ 경량 종목 데이터 수집 (CrewAI 제거 — trend_theme_writer 용)
#  종목 시세·재무 데이터만 수집 (HTML 생성은 하지 않음).
#  HTML 생성은 trend_theme_writer 가 Claude Code SDK 1-pass 로 직접.
# ════════════════════════════════════════════════════════════════

def _valid_stock_price(s: dict) -> bool:
    """종목이 현재가를 실제로 취득했는지 (거짓/공백 데이터 종목 판별)."""
    try:
        return bool(s) and float(s.get("price") or 0) > 0
    except (TypeError, ValueError):
        return False


# ★ 검증 레지스트리 등록 (2026-07-02): collect_stocks_data 산출물 체크포인트.
#   import 시 자동 등록 → verify_output('collect_stocks_data', data) 로 소비 가능.
try:
    from JARVIS00_INFRA.verification import register_check as _register_check

    @_register_check("collect_stocks_data", "실취득 종목(현재가) 확보", severity="block")
    def _chk_stocks_have_price(output, ctx):
        stocks = (output or {}).get("stocks") or []
        bad = [s.get("name", "?") for s in stocks if not _valid_stock_price(s)]
        return f"현재가 미취득 종목 {bad}" if bad else ""

    @_register_check("collect_stocks_data", "최소 종목수", severity="block")
    def _chk_stocks_min_count(output, ctx):
        n = len((output or {}).get("stocks") or [])
        return f"종목 {n}개 — 2개 미만(테마 교체 필요)" if n < 2 else ""
except Exception:   # verification 미가용 환경(테스트 등)에서도 수집은 동작
    pass


def stocks_to_datasets(stocks_data: dict, max_stocks: int = 8) -> list[dict]:
    """★ 종목 시세·재무 → 차트 데이터셋 승격 (ERRORS [313] — 사용자 지적 2026-07-03).

    "테마주는 확실히 데이터를 받을 수 있는데 왜 슬롯에 데이터가 없다고 하지?" —
    이미 수집된 종목 실데이터(테마주 글의 가장 확실한 수치)를 인포그래픽 풀에 합류.
    출처 provenance 동봉 (네이버 금융 KRX 시세) — image 사실성 게이트 통과 요건.

    단위 근거 (_naver_fin 파서 실측): price=원 / marcap·revenue=원(억 파싱 후 ×1e8)
    / roe·op_margin=소수(0.15=15%) / per=배.
    """
    from datetime import date as _d_std
    from JARVIS09_COLLECTOR.models import dataset_fingerprint as _dfp
    stocks = [s for s in (stocks_data or {}).get("stocks") or [] if s.get("name")][:max_stocks]
    if len(stocks) < 2:
        return []
    theme = str((stocks_data or {}).get("theme") or "").strip() or "테마"
    # ★ 출처 정확화 (사용자 박제 2026-07-05, ERRORS [367]): 시세(현재가·시총)와 재무제표
    #   (매출·ROE·영업이익률·PER)는 *다른 데이터* — 출처를 구분해 실제 수집처를 표기.
    _src_price = {"provider": "naver_finance", "name": "네이버 금융 · 시세",
                  "url": "https://finance.naver.com", "as_of": _d_std.today().isoformat()}
    _src_fin   = {"provider": "naver_finance", "name": "네이버 금융 · 재무제표",
                  "url": "https://finance.naver.com", "as_of": _d_std.today().isoformat()}
    # ★ 재무 기간 (거짓 '연매출' 라벨 방지, ERRORS [367]): 종목들의 fin_period 최빈값.
    #   네이버 분기 실적이면 'YYYY.MM'(예 2025.09) — 라벨에 명시. 없으면 '최근 실적'(정직).
    _pers = [str(s.get("fin_period") or "").strip() for s in stocks if str(s.get("fin_period") or "").strip()]
    _period = ""
    if _pers:
        from collections import Counter as _Ctr
        _period = _Ctr(_pers).most_common(1)[0][0]
    _rev_title = (f"{theme} 관련주 매출액 ({_period} 기준)" if _period
                  else f"{theme} 관련주 매출액 (최근 실적 기준)")

    def _rows(field, scale=1.0, nd=2, allow_neg=False):
        out = []
        for s in stocks:
            try:
                v = float(s.get(field) or 0)
            except (TypeError, ValueError):
                continue
            if v > 0 or (allow_neg and v != 0):
                out.append({"label": str(s["name"])[:12], "value": round(v * scale, nd)})
        return out

    # ★ ERRORS [347] — nd(소수 자리)는 본문 포맷터(draft_writer)와 *정합* 필수.
    #   조원 필드(marcap·revenue)는 본문 `_fmt_marcap` 이 `.1f`(1 자리, "5.9조원")로
    #   쓰는데 승격값이 nd=2("5.88조원")면 grounding LLM 이 진실 시가총액을 매칭 실패
    #   → "출처·웹 모두 확인 불가" false-positive 차단. roe/op_margin/per(.1f)·price(콤마정수)
    #   는 이미 정합. 조원 두 필드만 nd=1 로 본문 표기와 일치시킨다. [346] 표기정합 원칙 연장.
    specs = [
        ("price",     1.0,   0, False, f"{theme} 관련주 현재가",   "원",  "bar_chart", _src_price),
        ("marcap",    1e-12, 1, False, f"{theme} 관련주 시가총액", "조원", "bar_chart", _src_price),
        ("roe",       100.0, 1, True,  f"{theme} 관련주 ROE",     "%",   "bar_chart", _src_fin),
        # ★ op_margin 승격 (ERRORS [344] — [343] 대칭 갭). roe 와 동일하게 소수(0.136)로
        #   저장되므로 scale=100.0 로 %(13.6) 변환. 누락 시 진실한 "영업이익률 13.6%" 가
        #   grounding 코퍼스에 없어 "출처·웹 확인 불가"로 false-positive 차단됨.
        ("op_margin", 100.0, 1, True,  f"{theme} 관련주 영업이익률", "%", "bar_chart", _src_fin),
        ("per",       1.0,   1, False, f"{theme} 관련주 PER",     "배",  "bar_chart", _src_fin),
        # ★ '연매출'(연간) → 실제 기간 라벨 (ERRORS [367]): 네이버 재무는 최근 *분기* 값.
        ("revenue",   1e-12, 1, False, _rev_title,               "조원", "bar_chart", _src_fin),
    ]
    datasets = []
    for field, scale, nd, neg, title, unit, viz, src in specs:
        rows = _rows(field, scale, nd, neg)
        if len(rows) >= 2:      # 1행짜리는 비교 차트로 무의미 — 승격 제외
            datasets.append({"title": title, "unit": unit, "viz_hint": viz,
                             "data": rows, "source": dict(src),
                             "fingerprint": _dfp(title, unit)})

    # ★ 대장주(rank=1)/부대장주(rank=2) 주가 이력 — 분기별 종가 요약 (5y→3y→1y 폴백)
    #   LLM 이 슬롯에 박을 수 있는 데이터셋으로 승격. viz_hint="stock_price" 로 표시해
    #   JARVIS06 이 인포그래픽 대신 전용 주가 차트 렌더러로 분기한다.
    _LEADER_RANKS = {1: "leader", 2: "second"}
    _src_hist = {"provider": "yfinance", "name": "Yahoo Finance · 주가 이력",
                 "url": "https://finance.yahoo.com", "as_of": _d_std.today().isoformat()}
    for stock in sorted(stocks, key=lambda s: s.get("rank") or 99):
        rank = stock.get("rank")
        if rank not in _LEADER_RANKS:
            continue
        ticker = stock.get("ticker", "")
        name   = stock.get("name", "")
        if not ticker or not name:
            continue

        # 최대 5년 요청 — yfinance 가 상장 이후 데이터만 반환하므로 있는 만큼만 사용.
        # alt 티커 포함 순서대로 시도.
        alt_tickers = [ticker]
        if ticker.endswith(".KS"):
            alt_tickers.append(ticker.replace(".KS", ".KQ"))
        elif ticker.endswith(".KQ"):
            alt_tickers.append(ticker.replace(".KQ", ".KS"))

        hist_df = pd.DataFrame()
        for tk in alt_tickers:
            try:
                h = yf.Ticker(tk).history(period="5y")
                if not h.empty and len(h) >= 6:
                    hist_df = h
                    break
            except Exception:
                pass

        if hist_df.empty:
            print(f"  ⚠️ [stocks_to_datasets] {name}({ticker}) 주가 이력 없음 — 스킵")
            continue

        # 월별 마지막 종가 요약
        try:
            hist_df.index = pd.to_datetime(hist_df.index).tz_localize(None)
            monthly = hist_df["Close"].resample("ME").last().dropna()
        except Exception:
            try:
                monthly = hist_df["Close"].resample("M").last().dropna()
            except Exception:
                continue
        if len(monthly) < 6:
            continue

        m_rows = [{"label": f"{ts.year}.{ts.month:02d}", "value": round(float(v), 0)}
                  for ts, v in monthly.items()]

        # 실제 보유 기간 문자열 (5년 5개월 / 3년 / 8개월 등)
        n_months = len(m_rows)
        n_years, n_rem = divmod(n_months, 12)
        if n_years >= 1 and n_rem > 0:
            period_str = f"{n_years}년 {n_rem}개월"
        elif n_years >= 1:
            period_str = f"{n_years}년"
        else:
            period_str = f"{n_months}개월"

        label_key  = _LEADER_RANKS[rank]
        title_hist = f"{name} 주가 흐름 (최근 {period_str})"
        datasets.append({
            "title":      title_hist,
            "unit":       "원",
            "viz_hint":   "stock_price",
            "label_key":  label_key,
            "ticker":     ticker,
            "name":       name,
            "rank":       rank,
            "period":     period_str,   # "3년 2개월" 형태 — 차트 제목에 사용
            "data":       m_rows,
            "source":     dict(_src_hist),
            "fingerprint": _dfp(title_hist, "원"),
        })
        print(f"  📈 [stocks_to_datasets] {name} 주가 이력 {n_months}개월 (최근 {period_str}) 승격")

    return datasets


def collect_stocks_data(theme_name: str) -> dict:
    """테마 키워드 → 종목 {STOCK_COUNT_PER_POST}개 + 시세 + 재무 데이터 수집.

    흐름 (LLM 호출 1회 + Naver 금융 병렬 N회):
      1. Claude Sonnet 호출 — 테마 관련 KRX 상장 종목 7개 추출 ('종목명:티커' CSV)
      2. 각 종목 코드별 _naver_fin() 병렬 호출 — 시세·재무
      3. dict 반환

    Returns:
        {
          "theme":     str,
          "stocks":    [
              {"name", "code", "ticker", "price", "marcap", "per", "roe",
               "op_margin", "revenue", "net_income", "is_profit", "rank"}
              × {STOCK_COUNT_PER_POST}
          ],
          "summary":   {"profit_count", "loss_count", "leader_name"},
        }
    """
    # ★ 새 테마 = 새 상태: 이전 테마 잔류 데이터 완전 제거 (단일 진입점 패치)
    global INFOG_STORE, COLLECTED_DATA
    INFOG_STORE.clear()       # .clear() — = {} 는 rebinding 버그
    COLLECTED_DATA.clear()
    COLLECTED_DATA['theme_name'] = theme_name
    try:
        import JARVIS06_IMAGE.theme_charts as _tc
        _tc.CHART_STORE.clear()
    except Exception:
        pass

    print(f"  📊 [stocks_data] 테마 '{theme_name}' 종목 데이터 수집 시작")

    # ── ★ Fix-A: LLM 호출 전 Naver Finance 테마 검색 시도 (ERRORS [176] 박제 2026-05-26) ──
    # 이유: "2026 상반기 신규상장" 같은 LLM 학습 범위 밖 테마는 5회 LLM 폴백 전부
    #       exit-0-empty / timeout 300s 으로 20+분 낭비 → Naver Finance 먼저 시도.
    # 전략: Naver Finance 40개 테마 fuzzy match → 3자+ 공통 부분 있으면 즉시 사용,
    #        매칭 없으면 LLM 폴백으로 fall-through.
    _naver_pre_pairs = _naver_fin_theme_search(theme_name)

    # ── ★ 공식 테마 게이트 (사용자 박제 2026-07-03 — ERRORS [306]) ──────────────
    # "KRX/네이버 금융 공식 테마를 먼저 확인하고, 미작성 공식 테마로 주제를 선정한다.
    #  글을 다 쓰고 테마가 있는지 찾는 건 역순."
    # 공식 테마 매칭 실패 = 이 테마로 글을 쓰지 않는다 — LLM 종목 작문 폴백 금지,
    # 즉시 빈 반환 → 상위 data_empty 흐름이 테마 교체. 킬스위치 THEME_OFFICIAL_ONLY=0.
    if not _naver_pre_pairs and os.getenv("THEME_OFFICIAL_ONLY", "1") != "0":
        print(f"  ⛔ [stocks_data] '{theme_name}' — 네이버 금융 공식 테마 미등록. "
              f"종목 작문 폴백 금지 → 테마 교체 필요 (ERRORS [306])")
        return {"theme": theme_name, "stocks": [], "summary": {}}

    # ── ① Claude Sonnet — 종목 N개(=7개) 추출 — 사용자 박제: 반드시 7개 ──
    from shared.llm import invoke_text
    n = _LM.STOCK_COUNT_PER_POST

    def _build_prompt(target: int, excluded: list[str] = None, attempt: int = 0) -> str:
        ex = ""
        if excluded:
            ex = f"\n[제외 — 이미 선정된 종목]: {', '.join(excluded)}\n위 종목 외의 신규 종목만 선정."
        # 시도 횟수에 따라 기준 완화 (인프라·정책 테마는 핵심사업 기준으로 0개 반환 방지)
        if attempt == 0:
            scope = f"반드시 '{theme_name}' 핵심 사업을 영위하는 기업만"
            exclude_rule = "간접 관련·단순 수혜·상장폐지·합병 기업 제외"
        elif attempt == 1:
            scope = f"'{theme_name}' 관련 사업을 영위하거나 직접 수혜를 받는 기업"
            exclude_rule = "상장폐지·합병 기업 제외. 관련 매출 비중이 높은 기업 우선"
        else:  # attempt >= 2
            scope = f"'{theme_name}' 테마에서 수혜가 예상되는 건설·장비·소재·서비스 기업 포함"
            exclude_rule = "상장폐지·합병 기업만 제외. 간접 수혜 기업도 포함"
        return (
            f"'{theme_name}' 테마와 연관된 KRX 상장 종목 *정확히 {target}개*를 시가총액 큰 순으로 선정하라.\n"
            f"규칙:\n"
            f"1. {scope}\n"
            f"2. {exclude_rule}\n"
            f"3. '종목명:야후파이낸스티커' 형식, 쉼표 구분\n"
            f"4. 코스피→.KS, 코스닥→.KQ\n"
            f"5. 추가 설명 금지 — 한 줄만 출력{ex}\n\n"
            f"예시: 삼성전자:005930.KS, SK하이닉스:000660.KS, ..."
        )

    def _parse_pairs(raw_text: str, exclude_codes: set = None) -> list:
        pairs_local = []
        exclude_codes = exclude_codes or set()
        for item in (raw_text or "").split(","):
            item = item.strip()
            if ":" not in item:
                continue
            name, ticker = item.split(":", 1)
            name   = name.strip()
            ticker = ticker.strip()
            code   = ticker.split(".")[0]
            if name and code.isdigit() and len(code) == 6 and code not in exclude_codes:
                pairs_local.append({"name": name, "ticker": ticker, "code": code})
        return pairs_local

    pairs = []
    seen_codes = set()

    # ── ★ Fix-A/B: Naver Finance 사전 매칭 결과 활용 ─────────────────────────────
    # Naver Finance에서 3자+ 공통 테마를 찾았으면 해당 종목을 초기 시드로 사용.
    # 이렇게 하면:
    #   ① 매칭 성공 → 이미 n개 이상이면 LLM 3-loop 전부 건너뜀 (22분 낭비 방지)
    #   ② 매칭 성공 but n 미달 → LLM으로 부족분만 보충 (1회 이하로 줄어듦)
    #   ③ 매칭 실패(빈 리스트) → 기존 LLM 흐름 동일하게 진행
    if _naver_pre_pairs:
        for p in _naver_pre_pairs:
            if len(pairs) >= n:
                break
            pairs.append(p)
            seen_codes.add(p["code"])
        print(f"  ✅ [stocks_data] Naver Finance 사전 매칭 {len(pairs)}개 시드 확보")

    # ── ★ Fix-A: LLM-불가 패턴 즉시 감지 — LLM 3-loop 건너뜀 (ERRORS [176]) ──────
    # 대상: "20XX 상반기/하반기 신규상장", "20XX IPO" 등 LLM 학습 범위 밖 최신 이벤트.
    # Naver Finance 매칭도 실패한 경우(pairs=0) → LLM 3-loop을 건너뛰고 직접 5차 폴백으로.
    # 이유: 이런 테마는 3회 LLM + 4차 폴백 = 최대 ~20분 낭비 후 어차피 5차 폴백에 도달.
    _LLM_SKIP_PATTERNS = [
        re.compile(r'20\d\d\s*(년\s*)?(상반기|하반기|[1-4]분기)\s*(신규상장|IPO|공모주|청약)', re.I),
        re.compile(r'신규상장\s*20\d\d', re.I),
        re.compile(r'(최신|최근|이번)\s*(주|달|분기|반기)\s*(신규상장|IPO|공모주)', re.I),
    ]
    _skip_to_fallback = (not pairs) and any(p.search(theme_name) for p in _LLM_SKIP_PATTERNS)
    if _skip_to_fallback:
        print(f"  ⚡ [stocks_data] LLM 불가 패턴 감지 ('{theme_name}') — LLM 3-loop 즉시 건너뜀")

    # 최대 3회 시도 — 7개 미만이면 부족분만큼 추가 요청 (★ 사용자 박제: 필히 7개)
    for attempt in range(3):
        needed = n - len(pairs)
        if needed <= 0:
            break
        # ★ Fix-A: LLM-불가 패턴이면 3-loop 건너뜀 (ERRORS [176])
        if _skip_to_fallback:
            print(f"  ⚡ [stocks_data] attempt {attempt+1} 건너뜀 (LLM-불가 패턴)")
            break
        try:
            raw = invoke_text(
                "router",
                _build_prompt(needed, excluded=[p["name"] for p in pairs] if pairs else None, attempt=attempt),
                temperature=0.2 + 0.1 * attempt,  # retry 마다 다양성 ↑
            ) or ""
        except Exception as e:
            print(f"  ❌ [stocks_data] LLM 호출 실패 (시도 {attempt+1}): {e}")
            _g_report("writer", e, module=__name__)
            break
        new_pairs = _parse_pairs(raw, exclude_codes=seen_codes)
        for p in new_pairs:
            if len(pairs) >= n:
                break
            pairs.append(p)
            seen_codes.add(p["code"])
        if len(pairs) >= n:
            break
        print(f"  ⚠️ [stocks_data] {len(pairs)}/{n}개 — 재시도 {attempt+1}/3")

    # ★ 4차 폴백 (ERRORS [168] 박제 2026-05-25) — 3회 시도 후에도 0개면 극완화 기준으로 1회 더
    # 원인: "스포츠행사 수혜(올림픽, 월드컵 등)" 같은 계절성·광의 테마는
    #        attempt=2 기준에서도 LLM이 6자리 코드 반환 못 함 → data_empty → abort.
    # 해결: 4차 시도는 "어떤 방식으로든 수혜 가능한 기업" 극완화 + 더 높은 temperature.
    if not pairs and not _skip_to_fallback:
        print(f"  ⚠️ [stocks_data] 3회 시도 전부 0개 — 4차 극완화 폴백 시도")
        _fallback_prompt = (
            f"'{theme_name}' 테마와 관련 가능성이 있는 KRX 상장 종목 *정확히 {n}개*를 선정하라.\n"
            f"규칙:\n"
            f"1. 직접·간접·계절적 수혜 기업 모두 포함. 브랜드·중계방송·의류·음식료·여행·건설 등 연관 업종 전부 허용\n"
            f"2. 상장폐지·합병 기업만 제외\n"
            f"3. '종목명:야후파이낸스티커' 형식, 쉼표 구분. 코스피→.KS, 코스닥→.KQ\n"
            f"4. 추가 설명 없이 한 줄만 출력\n\n"
            f"예시: 삼성전자:005930.KS, SK하이닉스:000660.KS, ..."
        )
        try:
            raw4 = invoke_text("router", _fallback_prompt, temperature=0.7) or ""
            new_pairs4 = _parse_pairs(raw4, exclude_codes=seen_codes)
            for p in new_pairs4:
                if len(pairs) >= n:
                    break
                pairs.append(p)
                seen_codes.add(p["code"])
            if pairs:
                print(f"  ✅ [stocks_data] 4차 폴백 성공: {len(pairs)}개 추출")
            else:
                print(f"  ❌ [stocks_data] 4차 폴백도 0개 — 5차 시도 진입")
        except Exception as e:
            print(f"  ❌ [stocks_data] 4차 폴백 오류: {e}")

    # ★ 5차 폴백 (ERRORS [174] 박제 2026-05-26) — 구조적 추출 불가 테마 전용
    # 원인: "2026 상반기 신규상장", "신규상장 IPO", "계절성 이벤트" 등 LLM 학습 데이터에 없는
    #        최신 이벤트/미래 IPO 테마는 4차까지 전부 실패.
    # 해결 ①: "신규상장" 계열 테마 → 주관 증권사(상장 수혜 대장주) 종목으로 대체 프롬프트.
    # 해결 ②: 여전히 0개 → "주식시장 전반 대표주(시총 상위)"로 완전 대체.
    if not pairs:
        _tn_lower = theme_name.lower().replace(" ", "")
        # ① 신규상장/IPO 계열 → 주관 증권사·주식시장 수혜주
        if any(kw in _tn_lower for kw in ("신규상장", "ipo", "공모주", "상장주선", "청약")):
            print(f"  ⚠️ [stocks_data] 신규상장 계열 테마 감지 — 주관 증권사 수혜주 폴백")
            # ★ LLM 비의존 하드코딩 증권사 시드 (ERRORS [219] 박제 2026-05-31)
            # 이유: LLM 다운 시 5차 폴백 invoke_text도 실패 → 여전히 0개.
            # 해결: LLM 호출 전 하드코딩 증권사 목록으로 먼저 채움.
            _IPO_HARDCODED = [
                {"name": "미래에셋증권", "ticker": "006800.KS", "code": "006800"},
                {"name": "한국금융지주", "ticker": "071050.KS", "code": "071050"},
                {"name": "NH투자증권",   "ticker": "005940.KS", "code": "005940"},
                {"name": "삼성증권",     "ticker": "016360.KS", "code": "016360"},
                {"name": "키움증권",     "ticker": "039490.KS", "code": "039490"},
                {"name": "메리츠증권",   "ticker": "008560.KS", "code": "008560"},
                {"name": "대신증권",     "ticker": "003540.KS", "code": "003540"},
                {"name": "KB금융",       "ticker": "105560.KS", "code": "105560"},
                {"name": "신한지주",     "ticker": "055550.KS", "code": "055550"},
            ]
            for _hc in _IPO_HARDCODED:
                if _hc["code"] not in seen_codes:
                    pairs.append(_hc)
                    seen_codes.add(_hc["code"])
                if len(pairs) >= n:
                    break
            if pairs:
                print(f"  ✅ [stocks_data] 하드코딩 증권사 시드 {len(pairs)}개 확보")
            _ipo_prompt = (
                f"KRX 상장된 대형 증권사 및 주관사 {n}개를 시가총액 큰 순으로 선정하라.\n"
                f"(배경: '{theme_name}' 테마는 신규상장 수혜 증권사 분석 글에 활용)\n"
                f"규칙:\n"
                f"1. 미래에셋증권·한국투자증권·KB증권·NH투자증권·삼성증권·키움증권·메리츠증권 우선\n"
                f"2. '종목명:야후파이낸스티커' 형식, 쉼표 구분. 코스피→.KS, 코스닥→.KQ\n"
                f"3. 한 줄 출력만"
            )
        else:
            # ② 그 외 → 주식시장 전반 대표주 + 시장 수혜 업종
            print(f"  ⚠️ [stocks_data] 전체 폴백 — 관련 업종 대표주 완전 대체")
            _ipo_prompt = (
                f"'{theme_name}' 테마로 투자자가 관심 가질 KRX 대표주 {n}개를 시가총액 순으로 선정하라.\n"
                f"규칙:\n"
                f"1. 유동성 높은 코스피·코스닥 대형주 우선 (2024~2025년 기준 알고 있는 종목)\n"
                f"2. 테마와 넓게 연관되는 업종(소비·금융·IT·건설·여행 등) 다양하게\n"
                f"3. '종목명:야후파이낸스티커' 형식, 쉼표 구분. 코스피→.KS, 코스닥→.KQ\n"
                f"4. 한 줄 출력만"
            )
        if len(pairs) < n:
            try:
                raw5 = invoke_text("router", _ipo_prompt, temperature=0.5) or ""
                new_pairs5 = _parse_pairs(raw5, exclude_codes=seen_codes)
                for p in new_pairs5:
                    if len(pairs) >= n:
                        break
                    pairs.append(p)
                    seen_codes.add(p["code"])
            except Exception as e:
                print(f"  ⚠️ [stocks_data] 5차 LLM 폴백 오류 (하드코딩 시드로 대체): {e}")
        if pairs:
            print(f"  ✅ [stocks_data] 5차 폴백 최종: {len(pairs)}개 추출 (대체 종목)")
        else:
            print(f"  ❌ [stocks_data] 5차 폴백도 0개 — 테마 자체가 KRX 종목 없음")

    # ── ★ 6차 폴백: Naver Finance 테마 검색 (ERRORS [176] 박제 2026-05-26) ──────
    # 5차 LLM 폴백까지 전부 실패한 경우 — LLM 호출 없이 웹 스크레이핑으로 종목 확보.
    # 사전 매칭(_naver_pre_pairs)에서 이미 시드를 얻었어도, n 미달이면 여기서 재확인.
    if not pairs and not _naver_pre_pairs:
        print(f"  ⚠️ [stocks_data] 6차 Naver Finance 테마 폴백 시도")
        _nav_pairs = _naver_fin_theme_search(theme_name)
        for p in _nav_pairs:
            if len(pairs) >= n:
                break
            if p["code"] not in seen_codes:
                pairs.append(p)
                seen_codes.add(p["code"])
        if pairs:
            print(f"  ✅ [stocks_data] 6차 Naver Finance 폴백 성공: {len(pairs)}개")
        else:
            print(f"  ❌ [stocks_data] 6차 폴백도 0개 — 네이버 금융 테마 없음, 테마 건너뜀")

    if len(pairs) < n:
        print(f"  ⚠️ [stocks_data] 최종 {len(pairs)}/{n}개 — 목표 {n}개 미달 (LLM 한계)")
    if not pairs:
        print(f"  ❌ [stocks_data] 종목 추출 완전 실패 — 테마: {theme_name}")
        return {"theme": theme_name, "stocks": [], "summary": {}}

    print(f"  ✅ [stocks_data] 종목 {len(pairs)}개 추출: " +
          ", ".join(p["name"] for p in pairs))

    # ── ② 재무 데이터 병렬 수집 (네이버 금융) ────────────────────
    from concurrent.futures import ThreadPoolExecutor, as_completed
    stocks = []

    def _enrich(pair: dict) -> dict:
        fin = _naver_fin(pair["code"]) or {}
        return {
            "name":       fin.get("corp_name") or pair["name"],
            "code":       pair["code"],
            "ticker":     pair["ticker"],
            "price":      fin.get("price"),
            "marcap":     fin.get("marcap"),
            "per":        _cap_per(fin.get("per")),   # ★ ERRORS [347] 아웃라이어 캡 일관
            "roe":        fin.get("roe"),
            "op_margin":  fin.get("op_margin"),
            "revenue":    fin.get("revenue"),
            "net_income": fin.get("net_income"),
            "op_income":  fin.get("op_income"),
            "is_profit":  (fin.get("net_income") or 0) > 0,
        }

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_enrich, p): p for p in pairs}
        for fut in as_completed(futures):
            try:
                stocks.append(fut.result(timeout=15))
            except Exception as e:
                p = futures[fut]
                print(f"  ⚠️ [stocks_data] _naver_fin 실패 ({p['name']}): {e}")
                stocks.append({**p, "price": None, "marcap": None, "is_profit": False})

    # 시총 큰 순 정렬 + rank 부여
    stocks.sort(key=lambda s: (s.get("marcap") or 0), reverse=True)
    for i, s in enumerate(stocks, 1):
        s["rank"] = i

    # ★ 검증 (2026-07-02): 현재가 실취득 실패 종목 드롭 — 표·차트에 N/A·거짓수치가
    #   흘러가지 않게. price 미취득은 스크레이핑 실패로 간주(하류 verification 레지스트리
    #   'collect_stocks_data' 체크와 짝). 드롭 후 2종목 미만이면 상위가 테마 교체.
    _n_before = len(stocks)
    stocks = [s for s in stocks if _valid_stock_price(s)]
    if len(stocks) < _n_before:
        print(f"  🧹 [stocks_data] 현재가 미취득 {_n_before - len(stocks)}종목 드롭 (거짓데이터 방지)")

    # ★ 대장주·부대장주 enrich(사업/기술/관계) LLM 2회 폐지 → 대본 흡수 (ERRORS [375]).
    #   _stocks_text 의 business/tech/relation 주입은 조건부라 빈 채로 두면 생략되고,
    #   대본이 대장주 섹션(사업성·핵심기술)을 corpus·종목데이터에서 직접 서술한다
    #   (수치는 사실성 게이트가, 정성 서술은 ADR 013 대로 자유). LLM 호출 2회 절감.

    pc = sum(1 for s in stocks if s.get("is_profit"))
    lc = len(stocks) - pc
    leader = stocks[0]["name"] if stocks else ""
    second = stocks[1]["name"] if len(stocks) > 1 else ""

    print(f"  ✅ [stocks_data] 완료 — {len(stocks)}종목 (흑자 {pc} · 적자 {lc}) · 대장주: {leader} · 부대장주: {second}")
    return {
        "theme":   theme_name,
        "stocks":  stocks,
        "summary": {
            "profit_count": pc, "loss_count": lc,
            "leader_name":  leader,
            "second_name":  second,
        },
    }
