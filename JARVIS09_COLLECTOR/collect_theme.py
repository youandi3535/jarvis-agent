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
from crewai import Agent, Task, Crew, Process
from crewai.tools import BaseTool

load_dotenv()

# ── 차트·인포그래픽 함수 전체를 JARVIS06_IMAGE.theme_charts 에서 임포트 ──
# (JARVIS06_IMAGE 가 matplotlib Agg 백엔드 + 폰트 설정 자동 수행)
from JARVIS06_IMAGE.theme_charts import (
    _cap, set_font, fig_to_b64, wrap_img, CHART_STORE,
    make_theme_overview_chart, make_investment_radar_chart,
    make_theme_factors_chart, make_investment_timeline_chart,
    make_theme_mechanism_chart, make_theme_applications_chart,
    make_theme_timeline_chart, make_theme_concept_chart,
    make_terms_chart, make_profit_donut, make_cap_bar, make_per_bar,
    make_profitability_chart, make_revenue_chart, make_theme_return_chart,
    make_risk_chart, make_portfolio_chart, make_checklist_chart,
    make_stock_chart,
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
        if best_score < 3 or not best_no:
            print(f"  ℹ️ [naver_theme] '{theme_name}' — 네이버 금융 테마 매칭 없음 (best={best_score})")
            return []

        print(f"  🔍 [naver_theme] '{theme_name}' → 네이버 금융 '{best_name}' (no={best_no}, score={best_score})")

        # 3) 종목 추출
        r2 = requests.get(
            f'https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={best_no}',
            headers=_hdrs, timeout=timeout
        )
        soup2 = BeautifulSoup(r2.content, 'html.parser')
        stocks = []
        for a in soup2.find_all('a', href=re.compile(r'item/main\.naver\?code=')):
            code = re.search(r'code=(\d{6})', a.get('href', ''))
            if not code:
                continue
            code = code.group(1)
            name = a.get_text(strip=True)
            if name and code:
                # KS/KQ 구분 (코스닥 0으로 시작하는 경우 많음 — 간단 휴리스틱)
                suffix = '.KQ' if code.startswith(('0', '1', '2', '3')) and int(code) < 400000 else '.KS'
                # 추가: _naver_fin 으로 시장 확인 가능하나 속도 위해 생략
                stocks.append({
                    "name": name,
                    "code": code,
                    "ticker": f"{code}{suffix}",
                    "_naver_theme": best_name,
                })

        print(f"  ✅ [naver_theme] 네이버 금융 테마 종목 {len(stocks)}개 확보")
        return stocks

    except Exception as e:
        print(f"  ⚠️ [naver_theme] Naver Finance 테마 검색 실패: {e}")
        return []


def _strip_numbers(text: str) -> str:
    """생성된 텍스트에서 재무 수치가 포함된 문장 전체 제거"""
    import re
    # 재무 수치 키워드 + 숫자 패턴이 있는 문장 전체 제거
    fin_keywords = r'(현재가|시가총액|시총|PER|ROE|영업이익률|매출액?|순이익|영업이익)'
    # 해당 키워드와 숫자가 같이 있는 문장 제거
    sentences = re.split(r'(?<=[\.!?])\s+', text)
    clean = []
    for s in sentences:
        has_fin_kw = bool(re.search(fin_keywords, s))
        has_number  = bool(re.search(r'[\d,]+\s*(원|억|%|배)', s))
        if not (has_fin_kw and has_number):
            clean.append(s)
    text = ' '.join(clean)
    # 단독 수치 패턴도 추가 제거 (문장 단위 제거 후 잔여)
    text = re.sub(r'[\d,]+\s*(억\s*원|억원|\s*원)\b', '', text)
    text = re.sub(r'[+-]?[\d\.]+\s*%', '', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()
    return text


def _make_stock_tip(theme_name: str, name: str, is_profit: bool,
                    op_good: bool, per_ok: bool) -> tuple[str, str]:
    """종목별 *동적* 투자 포인트/유의 코멘트 — Claude 호출.

    ★ 하드코딩 본문 금지 규정 (CLAUDE.md). 매 호출마다 다른 표현 생성.
    날짜·테마·종목명 시드로 LLM 응답 자연 변동 + temperature 0.7.

    Returns: (label, body) 예: ("💡 투자 포인트", "...")
    """
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y-%m-%d")

    # 상황 분류
    if not is_profit:
        situation = "현재 적자 상태인 기업. 테마 약화 시 변동성 큼."
        label = "⚠️ 투자 유의"
        guide = "리스크 관리 중심 코멘트 (손절선 설정·분할 매수 신중·테마 강도 모니터링 등)."
    elif op_good and per_ok:
        situation = "흑자 + 수익성 양호 + 밸류에이션 적정."
        label = "💡 투자 포인트"
        guide = "안정적 투자 매력 강조 (펀더멘털 견고·중장기 관점 권장 등)."
    elif op_good and not per_ok:
        situation = "흑자 + 수익성 양호하나 밸류에이션 높음."
        label = "💡 투자 포인트"
        guide = "성장성 검증 필요 코멘트 (실적 성장 추적·과열 주의 등)."
    elif (not op_good) and per_ok:
        situation = "흑자이나 수익성 개선 여지, 밸류에이션 적정."
        label = "💡 투자 포인트"
        guide = "마진 개선 추적 코멘트 (영업이익률 추이·턴어라운드 가능성 등)."
    else:
        situation = "흑자이나 수익성·밸류에이션 모두 부담."
        label = "💡 투자 포인트"
        guide = "보수적 접근 코멘트 (포트폴리오 비중 제한·실적 모멘텀 확인 등)."

    prompt = (
        f"종목 '{name}' ({theme_name} 테마, {today}) 의 투자 코멘트를 {_LM.build_length_phrase(1, 2)}으로 작성.\n\n"
        f"상황: {situation}\n"
        f"가이드: {guide}\n\n"
        f"규칙:\n"
        f"- 친근한 해요체 (~해요/~이에요/~있어요).\n"
        f"- 종목명은 1회만 언급.\n"
        f"- 숫자·수치·% 절대 금지.\n"
        f"- 어제·그제 글과 동일 표현 금지 — 매번 자연 변동 필요.\n"
        f"- 텍스트만 출력 (HTML 태그·인용부호 없음).\n"
    )
    try:
        from shared.llm import invoke_text as _inv_cli
        body = _strip_numbers((_inv_cli("writer", prompt, timeout=60) or "").strip())
        # 따옴표·줄바꿈 정리
        body = body.replace('"', '').replace("'", '').strip()
        if len(body) < 30:
            raise ValueError(f"too short: {body}")
        return (label, body)
    except Exception as e:
        from shared.llm import invoke_text as _llm_tip
        _ctx = "적자 기업 리스크 관리 중심" if not is_profit else "흑자 기업 투자 포인트 중심"
        _retry = _llm_tip(
            "writer_fast",
            f"'{name}' 종목 ({theme_name} 테마) 투자 코멘트 {_LM.build_length_phrase(1)}. {_ctx}. 해요체. 숫자 없이. 문장만.",
            max_tokens=100, temperature=0.8
        )
        return (label, _retry or ("리스크 관리에 유의하며 신중하게 접근해보세요." if not is_profit else "실적 추이를 확인하며 분할 접근해보세요."))


def _make_stock_analysis(theme_name: str, name: str, ticker: str, rank: str,
                          is_profit: bool, cap: int, revenue: float, net_income: float,
                          per, roe, op_margin) -> str:
    """Claude API로 종목별 실제 분석 생성"""
    try:
        from shared.llm import invoke_text as _inv_cli

        rank_desc = "대장주(시총 1위)" if rank=="대장주" else ("부대장주(시총 2위)" if rank=="부대장주" else f"시총 {rank}")

        # ★ 문장수 메인 표기 (사용자 박제 2026-05-14) — LLM 은 글자수 못 세지만 문장수는 정확히 셈
        leader_phrase = _LM.build_length_phrase(_LM.STOCK_CARD_LEADER_SENTS_MIN, _LM.STOCK_CARD_LEADER_SENTS_MAX)
        other_phrase  = _LM.build_length_phrase(_LM.STOCK_CARD_OTHER_SENTS_MIN,  _LM.STOCK_CARD_OTHER_SENTS_MAX)

        if rank in ['대장주', '부대장주']:
            prompt = f"""'{theme_name}' 테마의 {rank_desc}인 '{name}' ({ticker}) 기업을 소개해줘.

아래 내용을 포함해서 {leader_phrase}으로 작성해줘:
1. 이 기업이 실제로 만들거나 공급하는 핵심 제품·소재·기술
2. '{theme_name}' 테마 공급망(밸류체인)에서의 역할
3. 이 기업만의 차별화 경쟁력 또는 시장 위치

절대 금지 (어기면 실패):
- 현재가, 시총, 시가총액, PER, ROE, 영업이익률, 매출, 순이익, 억원, %, 배 등 숫자·수치 전부 금지
- 재무 데이터, 실적 수치 일절 언급 금지

반드시 지킬 것:
- 친근한 해요체(~해요, ~이에요, ~있어요)로 작성
- {leader_phrase}, 완전한 문장으로 마무리
- 텍스트만 출력 (HTML 태그 없이)"""
        else:
            prompt = f"""'{theme_name}' 테마 관련주인 '{name}' ({ticker}) 기업을 간략히 소개해줘.

아래 내용을 포함해서 {other_phrase}으로 작성해줘:
1. 이 기업이 실제로 하는 사업과 '{theme_name}' 테마 연관성
2. 이 기업만의 특징 또는 강점

절대 금지 (어기면 실패):
- 현재가, 시총, 시가총액, PER, ROE, 영업이익률, 매출, 순이익, 억원, %, 배 등 숫자·수치 전부 금지
- 재무 데이터, 실적 수치 일절 언급 금지

반드시 지킬 것:
- 친근한 해요체(~해요, ~이에요, ~있어요)로 작성
- {other_phrase}, 완전한 문장으로 마무리
- 텍스트만 출력 (HTML 태그 없이)"""

        is_leader = rank in ['대장주', '부대장주']
        result_text = _strip_numbers((_inv_cli("writer", prompt, timeout=90) or "").strip())

        # 글자수 초과 시 재구성 — length_manager 정책 단일 진입점
        try:
            from JARVIS02_WRITER import length_manager as _LM
        except ImportError:
            import length_manager as _LM
        kor_len = _LM.count(result_text)
        max_len = _LM.STOCK_CARD_LEADER_MAX if is_leader else _LM.STOCK_CARD_OTHER_MAX
        min_len = _LM.STOCK_CARD_LEADER_MIN if is_leader else _LM.STOCK_CARD_OTHER_MIN
        if kor_len > max_len:
            # ★ 문장수 메인 표기 (사용자 박제 2026-05-14)
            trim_phrase = (_LM.build_length_phrase(_LM.STOCK_CARD_LEADER_SENTS_MIN, _LM.STOCK_CARD_LEADER_SENTS_MAX)
                           if is_leader else
                           _LM.build_length_phrase(_LM.STOCK_CARD_OTHER_SENTS_MIN, _LM.STOCK_CARD_OTHER_SENTS_MAX))
            trim_prompt = (
                f"아래 텍스트를 {trim_phrase}으로 줄여줘. "
                f"해요체 유지, 완전한 문장으로 마무리, 숫자·수치 언급 금지, 텍스트만 출력.\n\n{result_text}"
            )
            result_text = _strip_numbers((_inv_cli("writer", trim_prompt, timeout=60) or result_text).strip())
        return result_text
    except Exception as e:
        from shared.llm import invoke_text as _llm_sa
        _profit_ctx = "흑자 기업" if is_profit else "적자 기업"
        _retry = _llm_sa(
            "writer_fast",
            f"'{name}' ({theme_name} 테마, {_profit_ctx}) 기업 소개 {_LM.build_length_phrase(_LM.COMPANY_INTRO_MIN, _LM.COMPANY_INTRO_MAX)}. 해요체. 수치·숫자 없이. 문장만 출력.",
            max_tokens=150, temperature=0.8
        )
        return _retry or f"{name}은(는) {theme_name} 분야 관련 사업을 영위하는 기업이에요."


def _make_company_biz_desc(theme_name: str, name: str, ticker: str, is_profit: bool, rank: int = 99) -> str:
    """표와 차트 사이: 제품·파트너사·시장 입지 중심 설명 (prefill로 재무 수치 원천 차단)"""
    try:
        from shared.llm import invoke_text as _inv_cli

        # 대장주(0)/부대장주(1)는 분량 더 많게
        is_leader = rank <= 1
        # ★ 문장수 메인 표기 (사용자 박제 2026-05-14)
        char_range = (_LM.build_length_phrase(_LM.BIZ_DESC_LEADER_SENTS_MIN, _LM.BIZ_DESC_LEADER_SENTS_MAX)
                      if is_leader else
                      _LM.build_length_phrase(_LM.BIZ_DESC_OTHER_SENTS_MIN, _LM.BIZ_DESC_OTHER_SENTS_MAX))
        extra_guide = (
            "\n대표 종목이므로 핵심 기술·제품의 구체적 사례, 주요 고객사·파트너십, 시장 점유율이나 경쟁 우위를 풍부하게 서술해."
            if is_leader else ""
        )

        # 이름 마지막 글자 받침 유무로 은/는 결정
        import re as _re
        _last = name[-1] if name else ''
        _code = ord(_last) - 0xAC00
        _jongseong = _code % 28 if 0 <= _code <= 11171 else 1
        _particle = '은' if _jongseong else '는'
        prefill = f"{name}{_particle}"
        prompt = (
            f"'{name}' ({ticker}) 기업이 '{theme_name}' 시장에서 하는 사업을 설명해줘.\n"
            f"핵심 제품·기술 이름, 주요 고객사·납품처, 시장 내 역할·강점 중심으로 작성해.{extra_guide}\n"
            f"반드시 '{name}{_particle} ...' 형태로 시작하고 해요체, {char_range}, 완전한 문장으로 마무리."
        )
        from shared.personas import get as _persona
        _sys = _persona("jarvis02_writer")
        _full_prompt = (f"{_sys}\n\n{prompt}\n\n반드시 '{prefill} ...' 로 시작할 것." if _sys else
                        f"{prompt}\n\n반드시 '{prefill} ...' 로 시작할 것.")
        raw_resp = (_inv_cli("writer", _full_prompt, timeout=90) or "").strip()
        # 응답이 "은/는 ..." 또는 "은(는) ..." 으로 시작하면 중복 제거
        raw_resp = _re.sub(r'^[은는]\(는\)\s*', '', raw_resp)
        raw_resp = _re.sub(r'^[은는]\s+', '', raw_resp)
        result = prefill + " " + raw_resp

        # 혹시 남아있는 숫자+단위 문장 제거
        sentences = _re.split(r'(?<=[\.!?])\s+', result)
        fin_kw = _re.compile(r'현재가|시가총액|시총|PER|ROE|영업이익률|매출액?|순이익')
        num_unit = _re.compile(r'\d[\d,]*\s*(억\s*원|억원|\s*원|%)')
        clean = [s for s in sentences if not (fin_kw.search(s) and num_unit.search(s))]
        result = ' '.join(clean).strip()

        print(f"    💬 [{name}] biz_desc: {result[:50]}...")
        if result:
            return result
        # LLM 응답 비어있을 때 — 더 단순한 프롬프트로 재시도
        from shared.llm import invoke_text as _llm_retry
        _retry = _llm_retry(
            "writer_fast",
            f"'{name}' 기업이 '{theme_name}' 분야에서 하는 사업을 해요체 1~{_LM.MAX_P_SENTS}문장으로 설명해. '{name}은(는)'으로 시작.",
            max_tokens=120, temperature=0.7
        )
        return _retry or f"{name}은(는) {theme_name} 분야 관련 사업을 영위하는 기업이에요."
    except Exception as e:
        print(f"    ⚠️  biz_desc 실패 ({name}): {e}")
        _g_report("writer", e, module=__name__)
        try:
            from shared.llm import invoke_text as _llm_retry
            _retry = _llm_retry(
                "writer_fast",
                f"'{name}' 기업이 '{theme_name}' 분야에서 하는 사업을 해요체 1~{_LM.MAX_P_SENTS}문장으로 설명해. '{name}은(는)'으로 시작.",
                max_tokens=120, temperature=0.7
            )
            if _retry:
                return _retry
        except Exception as _e:
            _g_report("collect_theme", _e, module="collect_theme", func_name="_enrich_leader_desc")
        return f"{name}은(는) {theme_name} 분야 관련 사업을 영위하는 기업이에요."


# ★ PER 이상치 상한 (2026-07-02) — 순이익이 0 에 가까우면 PER 이 수백~수천으로
#   폭증(예: 463.9배)해 차트·표를 오도한다. 이 값 초과 PER 은 신뢰 불가 → N/A 처리.
_PER_OUTLIER_MAX = 200.0


def calc_fin(info, ticker_obj=None):
    """폭포수 방식: 네이버(1순위) → yfinance info → yfinance financials"""
    mc  = info.get('marketCap', 0) or 0
    ni  = info.get('netIncomeToCommon', 0) or 0
    rv  = info.get('totalRevenue', 0) or 0
    roe = info.get('returnOnEquity', 0) or 0
    om  = info.get('operatingMargins', 0) or 0
    tpe = info.get('trailingPE', 0) or 0
    fpe = info.get('forwardPE', 0) or 0
    per = None

    # ── 1순위: 네이버 금융 (가장 정확) ──
    naver = {}
    if ticker_obj:
        try:
            code = ticker_obj.ticker.replace('.KQ','').replace('.KS','')
            naver = _naver_fin(code)
            # 상장폐지 확인
            if naver.get('delisted'):
                return {'per':None,'roe':None,'op_margin':None,
                        'net_income':0,'revenue':0,'is_profit':False,'delisted':True}
            if naver.get('per'):   per = naver['per']
            if naver.get('roe'):   roe = naver['roe']
            if naver.get('op_margin'): om = naver['op_margin']
            if naver.get('net_income'): ni = naver['net_income']
            if naver.get('revenue'):    rv = naver['revenue']
            if naver.get('marcap') and not mc: mc = naver['marcap']
            if naver.get('marcap'): info['naver_marcap'] = naver['marcap']
            if naver.get('price') and not info.get('currentPrice'):
                info['currentPrice'] = naver['price']
        except Exception as _e:
            _g_report("collect_theme", _e, module="collect_theme", func_name="calc_fin.naver")

    # ── 2순위: yfinance info 보완 ──
    if not ni:  ni  = info.get('netIncomeToCommon', 0) or 0
    if not rv:  rv  = info.get('totalRevenue', 0) or 0
    if not roe: roe = info.get('returnOnEquity', 0) or 0
    if not om:  om  = info.get('operatingMargins', 0) or 0
    if not mc:  mc  = info.get('marketCap', 0) or 0
    if not per:
        tpe = info.get('trailingPE', 0) or 0
        fpe = info.get('forwardPE', 0) or 0
        if tpe > 0: per = round(float(tpe), 1)
        elif fpe > 0: per = round(float(fpe), 1)

    # ── 3순위: yfinance financials 직접 파싱 ──
    if ticker_obj and (not ni or not rv or not om):
        try:
            fin = ticker_obj.financials
            if fin is not None and not fin.empty:
                def _get(keys):
                    for k in keys:
                        if k in fin.index:
                            v = fin.loc[k].iloc[0]
                            if v and not (hasattr(v,'__float__') and __import__('math').isnan(float(v))):
                                return float(v)
                    return 0
                ni2 = _get(['Net Income','NetIncome'])
                rv2 = _get(['Total Revenue','TotalRevenue'])
                oi2 = _get(['Operating Income','OperatingIncome','EBIT'])
                if not ni and ni2: ni = ni2
                if not rv and rv2: rv = rv2
                if not om and oi2 and rv2: om = oi2/rv2
        except Exception as _e:
            _g_report("collect_theme", _e, module="collect_theme", func_name="calc_fin.yfinance_financials")

    # PER 최종 계산
    if not per and ni > 0 and mc > 0:
        per = round(mc/ni, 1)

    is_profit = ni > 0  # 순이익 기준으로 흑자/적자 판단
    if not is_profit:
        per = None
    # ★ PER 이상치 가드 (2026-07-02): 흑자라도 이익 미미로 PER 이 비정상 과대(예: 463.9)
    #   하거나 음수면 신뢰 불가 → None(표에 N/A, 차트에서 제외). 거짓 차트 방지.
    if per is not None and (per <= 0 or per > _PER_OUTLIER_MAX):
        per = None
    # ROE/OM 단위 정규화 (네이버는 소수, yfinance도 소수)
    roe_pct = round(roe*100, 1) if roe and abs(roe) <= 1 else (round(roe, 1) if roe else None)
    om_pct  = round(om*100, 1)  if om  and abs(om)  <= 1 else (round(om,  1) if om  else None)
    return {
        'per': round(per, 1) if per else None,
        'roe': roe_pct,
        'op_margin': om_pct,
        'net_income': ni, 'revenue': rv, 'is_profit': is_profit,
        '_marcap': mc,   # 네이버/yfinance 시총
        '_price': naver.get('price', 0),  # 네이버 현재가
    }

def fmt(v,suf='',d='N/A'):
    return d if v is None else f"{v}{suf}"

# ════════════════════════════════════════════════════════
#  ThemeFinanceTool
# ════════════════════════════════════════════════════════
class ThemeFinanceTool(BaseTool):
    name:str="theme_finance_tool"
    description:str="'종목명:야후파이낸스티커' 쉼표 구분. 코스피→.KS 코스닥→.KQ"

    def _run(self,stock_input:str) -> str:
        global COLLECTED_DATA
        try:
            rows=[]
            for item in [x.strip() for x in stock_input.split(',') if ':' in x]:
                name,ticker=[s.strip() for s in item.split(':',1)]
                print(f"    {name} ({ticker}) ...")
                try:
                    # ── 1순위: 네이버 금융에서 모든 데이터 수집 ──
                    code = ticker.replace('.KQ','').replace('.KS','')
                    naver = _naver_fin(code)
                    
                    # 네이버에서 정확한 종목명 가져오기
                    if naver.get('corp_name'):
                        name = naver['corp_name']
                    
                    # 네이버에서 기본 데이터 추출
                    price = int(naver.get('price', 0) or 0)
                    cap   = int(naver.get('marcap', 0) or 0)
                    ni    = naver.get('net_income', 0) or 0
                    rv    = naver.get('revenue', 0) or 0
                    oi    = naver.get('op_income', 0) or 0
                    om    = naver.get('op_margin', 0) or 0
                    roe   = naver.get('roe', 0) or 0
                    per_v = naver.get('per')
                    
                    # 상장폐지/데이터없음 감지
                    if price == 0 and cap == 0:
                        print(f"    ❌ {name}: 네이버 데이터 없음(상장폐지/상호변경) → 제외")
                        continue
                    
                    # ── 2순위: yfinance로 부족한 항목 보완 ──
                    try:
                        t    = yf.Ticker(ticker)
                        hist = t.history(period="5d")
                        info = t.info
                        if not price: price = int(hist['Close'].iloc[-1]) if not hist.empty else 0
                        if not cap:   cap   = int(info.get('marketCap', 0) or 0)
                        if not ni:    ni    = info.get('netIncomeToCommon', 0) or 0
                        if not rv:    rv    = info.get('totalRevenue', 0) or 0
                        if not om:    om    = info.get('operatingMargins', 0) or 0
                        if not roe:   roe   = info.get('returnOnEquity', 0) or 0
                        if not per_v:
                            tpe = info.get('trailingPE', 0) or 0
                            fpe = info.get('forwardPE', 0) or 0
                            if tpe > 0: per_v = round(tpe, 1)
                            elif fpe > 0: per_v = round(fpe, 1)
                        # yfinance financials 보완
                        if not ni or not rv:
                            fin2 = t.financials
                            if fin2 is not None and not fin2.empty:
                                def _g(keys):
                                    for k in keys:
                                        if k in fin2.index:
                                            v = fin2.loc[k].iloc[0]
                                            if v and not __import__('math').isnan(float(v)):
                                                return float(v)
                                    return 0
                                if not ni: ni = _g(['Net Income','NetIncome'])
                                if not rv: rv = _g(['Total Revenue','TotalRevenue'])
                    except Exception as ye:
                        print(f"    ⚠️ {name} yfinance 보완 실패: {ye}")
                        _g_report("writer", ye, module=__name__)
                    
                    # 최종 데이터 정리
                    is_profit = ni > 0
                    if not is_profit: per_v = None  # 적자면 PER 무의미
                    op_margin_pct = round(om*100, 1) if om and abs(om) <= 1 else (round(om, 1) if om else None)
                    roe_pct = round(roe*100, 1) if roe and abs(roe) <= 1 else (round(roe, 1) if roe else None)
                    
                    fin = {
                        'per': round(per_v, 1) if per_v else None,
                        'roe': roe_pct,
                        'op_margin': op_margin_pct,
                        'net_income': ni,
                        'revenue': rv,
                        'is_profit': is_profit,
                    }
                    
                    if not price and not cap:
                        print(f"    ❌ {name}: 최종 데이터 없음 → 제외")
                        continue
                    chart=make_stock_chart(ticker,name)
                    rows.append({'name':name,'ticker':ticker,'cap':cap,'price':price,'chart':chart,**fin})
                    print(f"    {'흑자' if fin['is_profit'] else '적자'} {name}: {price:,}원 | {cap//int(1e8):,}억")
                except Exception as e:
                    print(f"    ERROR {name}: {e}")
                    _g_report("writer", e, module=__name__)
            if not rows: return "데이터 없음"

            # 동일 티커 중복 제거 (에이전트가 같은 종목을 두 번 넘길 경우 방지)
            seen_tickers = set()
            rows = [r for r in rows if r['ticker'] not in seen_tickers and not seen_tickers.add(r['ticker'])]

            df=pd.DataFrame(rows).sort_values('cap',ascending=False).reset_index(drop=True)
            names=df['name'].tolist(); tickers=df['ticker'].tolist()
            caps=df['cap'].tolist(); pers=df['per'].tolist()
            roes=df['roe'].tolist(); oms=df['op_margin'].tolist()
            nis=df['net_income'].tolist(); rvs=df['revenue'].tolist()
            isp=df['is_profit'].tolist()
            pc=sum(isp); lc=len(isp)-pc

            # ── 인포그래픽 전체 생성 ──
            print("  인포그래픽 생성 중...")
            theme_name = COLLECTED_DATA.get('theme_name', '테마')
            # ★ 2026-05-26 v2: run_id 1회 생성 (글 전체 동일)
            import uuid as _uuid_ct
            _infog_run_id = _uuid_ct.uuid4().hex
            from JARVIS06_IMAGE.dynamic_infographic import generate_dynamic_infographic as _dyn_infog
            from JARVIS06_IMAGE.infographic_charts import make_hub_spoke, make_hex_node_map, make_premium_timeline

            # ── LLM 동적 창작 인포그래픽 (매 실행마다 완전히 다른 스타일) ──
            # 글 섹션별 대표 텍스트 조각 수집 (컨텍스트로 활용)
            _ctx = COLLECTED_DATA.get('summary_text', '') or COLLECTED_DATA.get('content', '') or theme_name

            # ── 팩트 데이터 dict 빌드 (LLM에 실수치 제공 — 지어내기 금지) ──
            _fact_data = {
                "stocks": [
                    {
                        "name": names[_i],
                        "ticker": tickers[_i],
                        "cap_억": round(caps[_i] / 1e8) if caps[_i] else None,
                        "per": pers[_i],
                        "roe": roes[_i],
                        "op_margin": oms[_i],
                        "net_income_억": round(nis[_i] / 1e8) if nis[_i] else None,
                        "revenue_억": round(rvs[_i] / 1e8) if rvs[_i] else None,
                        "is_profit": isp[_i],
                    }
                    for _i in range(len(names))
                ],
                "summary": {
                    "theme": theme_name,
                    "흑자종목": pc,
                    "적자종목": lc,
                    "총종목수": len(names),
                    "시총1위": names[0] if names else "",
                    "시총1위_억": round(caps[0] / 1e8) if caps and caps[0] else None,
                },
            }

            def _safe_dyn(purpose_ko: str, content_ctx: str, slot_key: str = "",
                          data: dict | None = None, fallback_fn=None):
                """동적 생성 실패 시 fallback_fn() 호출. 실수치 data 전달 → LLM 지어내기 방지."""
                try:
                    result = _dyn_infog(
                        theme_name, purpose_ko, content_ctx,
                        data=data,
                        run_id=_infog_run_id,
                        slot_key=slot_key or purpose_ko[:20],
                    )
                except Exception as _e:
                    print(f"    ⚠️ dynamic_infographic 예외: {_e}")
                    result = ""
                if not result and fallback_fn:
                    try: result = fallback_fn()
                    except Exception: result = ""
                return result or ""

            # img01: 테마 핵심 개념 구조 (동적 — 매번 다른 레이아웃)
            INFOG_STORE['img01'] = _safe_dyn(
                f"{theme_name} 테마의 핵심 개념과 구성 요소 구조도",
                _ctx[:800],
                slot_key="img01",
                data=_fact_data,
                fallback_fn=lambda: make_hub_spoke(theme_name, _infog_run_id),
            )
            print("    img01 ✅")

            # img02: 작동 원리·메커니즘 (동적)
            INFOG_STORE['img02'] = _safe_dyn(
                f"{theme_name} 작동 원리 및 핵심 메커니즘 흐름도",
                _ctx[:800],
                slot_key="img02",
                data=_fact_data,
                fallback_fn=lambda: make_theme_mechanism_chart(theme_name),
            )
            print("    img02 ✅")

            # img03: 활용 분야 (동적 — img01과 다른 스타일)
            INFOG_STORE['img03'] = _safe_dyn(
                f"{theme_name} 기술·산업 활용 분야 및 적용 사례",
                _ctx[:800],
                slot_key="img03",
                data=_fact_data,
                fallback_fn=lambda: make_hex_node_map(theme_name, _infog_run_id + "_03"),
            )
            print("    img03 ✅")

            # img04: 역사·타임라인 (동적)
            INFOG_STORE['img04'] = _safe_dyn(
                f"{theme_name} 발전 역사와 핵심 마일스톤 타임라인",
                _ctx[:800],
                slot_key="img04",
                data=_fact_data,
                fallback_fn=lambda: make_premium_timeline(theme_name, _infog_run_id + "_04"),
            )
            print("    img04 ✅")

            # img05: 투자 용어 (정적 — LLM 용어 설명 특화)
            INFOG_STORE['img05'] = make_terms_chart(theme_name)
            print("    img05 ✅")

            # img06~img12: 실데이터 기반 차트 (그대로 유지)
            INFOG_STORE['img06'] = make_profit_donut(pc, lc)
            INFOG_STORE['img07'] = make_cap_bar(names, caps)
            INFOG_STORE['img08'] = make_per_bar(names, pers)
            INFOG_STORE['img09'] = make_profitability_chart(names, oms, roes)
            INFOG_STORE['img10'] = make_revenue_chart(names, rvs, nis)
            INFOG_STORE['img11'] = make_theme_return_chart(names, tickers)
            INFOG_STORE['img12'] = make_risk_chart(names, isp, caps, pers)

            # img13: 투자 전략 (동적 — 실수치 포함)
            INFOG_STORE['img13'] = _safe_dyn(
                f"{theme_name} 투자 전략 및 포트폴리오 구성 방법",
                _ctx[:800],
                slot_key="img13",
                data=_fact_data,
                fallback_fn=lambda: make_portfolio_chart(names),
            )
            print("    img13 ✅")

            # img14: 투자 원칙 체크리스트 (동적 — 실수치 포함)
            INFOG_STORE['img14'] = _safe_dyn(
                f"{theme_name} 투자 전 반드시 확인할 6대 원칙 체크리스트",
                _ctx[:800],
                slot_key="img14",
                data=_fact_data,
                fallback_fn=lambda: make_checklist_chart(),
            )
            print("    img14 ✅")

            print(f"  인포그래픽 {len(INFOG_STORE)}개 완료 (run_id={_infog_run_id[:8]})")

            ranks=["대장주","부대장주","3위","4위","5위","6위","7위","8위","9위","10위"]
            rank_labels=["대장주 (시총 1위)","부대장주 (시총 2위)","시총 3위","시총 4위","시총 5위","시총 6위","시총 7위","시총 8위","시총 9위","시총 10위"]
            today=datetime.today().strftime('%Y년 %m월 %d일')

            # ── 인라인 스타일 순위표 HTML 생성 ──
            TH = ("padding:13px 10px;text-align:center;font-weight:700;font-size:13px;"
                  "border-right:1px solid rgba(255,255,255,0.2);white-space:nowrap;")
            TH_LAST = "padding:13px 10px;text-align:center;font-weight:700;font-size:13px;"
            TD = ("padding:12px 10px;text-align:center;font-size:13px;"
                  "border-bottom:1px solid #d0d7f0;border-right:1px solid #d0d7f0;white-space:nowrap;")
            TD_LAST = "padding:12px 10px;text-align:center;font-size:13px;border-bottom:1px solid #d0d7f0;"

            rank_table = (
                '<table style="width:100%;border-collapse:collapse;border-radius:14px;'
                'overflow:hidden;margin:18px 0;border:2px solid #2d3561;">'
                '<thead><tr style="background:linear-gradient(90deg,#1a1a2e,#2d3561);color:white;">'
                f'<th style="{TH}">순위</th>'
                f'<th style="{TH}">종목명</th>'
                f'<th style="{TH}">티커</th>'
                f'<th style="{TH}">현재가</th>'
                f'<th style="{TH}">시총(억)</th>'
                f'<th style="{TH}">재무</th>'
                f'<th style="{TH}">PER</th>'
                f'<th style="{TH}">ROE</th>'
                f'<th style="{TH_LAST}">영업이익률</th>'
                '</tr></thead><tbody>'
            )
            for i, row in df.iterrows():
                rl = ranks[i] if i < len(ranks) else f"{i+1}위"
                st = row['is_profit']
                bg = "#f8f9ff" if i % 2 == 0 else "#ffffff"
                tag = ('<span style="background:#e8f5e9;color:#2e7d32;padding:3px 10px;'
                       'border-radius:20px;font-size:12px;font-weight:700;">흑자</span>' if st else
                       '<span style="background:#ffebee;color:#c62828;padding:3px 10px;'
                       'border-radius:20px;font-size:12px;font-weight:700;">적자</span>')
                last_row = (i == len(df) - 1)
                td = TD if not last_row else TD.replace("border-bottom:1px solid #d0d7f0;","")
                td_last = TD_LAST if not last_row else TD_LAST.replace("border-bottom:1px solid #d0d7f0;","")
                rank_table += (
                    f'<tr style="background:{bg};">'
                    f'<td style="{td}">{rl}</td>'
                    f'<td style="{td}"><strong>{row["name"]}</strong></td>'
                    f'<td style="{td}">{row["ticker"]}</td>'
                    f'<td style="{td}">{row["price"]:,}원</td>'
                    f'<td style="{td}">{row["cap"]//int(1e8):,}억</td>'
                    f'<td style="{td}">{tag}</td>'
                    f'<td style="{td}">{fmt(row["per"],"배")}</td>'
                    f'<td style="{td}">{fmt(row["roe"],"%")}</td>'
                    f'<td style="{td_last}">{fmt(row["op_margin"],"%")}</td>'
                    '</tr>'
                )
            rank_table += '</tbody></table>'
            INFOG_STORE['TABLE'] = rank_table

            # ── 인라인 스타일 종목별 stat-grid 생성 ──
            CELL = ("background:#f8f9ff;padding:14px 10px;text-align:center;"
                    "border-right:1px solid #c7d2f0;border-bottom:1px solid #c7d2f0;")
            CELL_R = ("background:#f8f9ff;padding:14px 10px;text-align:center;"
                      "border-bottom:1px solid #c7d2f0;")  # 오른쪽 끝
            CELL_B = ("background:#f8f9ff;padding:14px 10px;text-align:center;"
                      "border-right:1px solid #c7d2f0;")   # 아래쪽 끝
            CELL_BR = "background:#f8f9ff;padding:14px 10px;text-align:center;"  # 모서리
            LBL = "font-size:11px;color:#888;margin-bottom:5px;font-weight:600;display:block;"
            VAL = "font-size:17px;font-weight:800;color:#1a1a2e;display:block;"
            VAL_UP = "font-size:17px;font-weight:800;color:#e53935;display:block;"
            VAL_DN = "font-size:17px;font-weight:800;color:#1976d2;display:block;"

            for i, row in df.iterrows():
                ni_s = f"{int(row['net_income'])//int(1e8):,}억원" if row['net_income'] else "N/A"
                rv_s = f"{int(row['revenue'])//int(1e8):,}억원" if row['revenue'] else "N/A"
                is_p = row['is_profit']

                def cell(c, l, v, style=None):
                    vst = style if style else VAL
                    return (f'<td style="{c}"><span style="{LBL}">{l}</span>'
                            f'<span style="{vst}">{v}</span></td>')

                sg = (
                    '<table style="width:100%;border-collapse:collapse;'
                    'border:2px solid #c7d2f0;border-radius:12px;overflow:hidden;margin:18px 0;">'
                    '<tbody>'
                    # 1행
                    f'<tr>'
                    + cell(CELL, '현재가', f'{row["price"]:,}원')
                    + cell(CELL, '시가총액', f'{row["cap"]//int(1e8):,}억원')
                    + cell(CELL_R, 'PER', fmt(row['per'], '배'))
                    + '</tr>'
                    # 2행
                    '<tr>'
                    + cell(CELL, 'ROE', fmt(row['roe'], '%'),
                           VAL_UP if (row['roe'] or 0) >= 10 else VAL_DN if (row['roe'] or 0) < 0 else VAL)
                    + cell(CELL, '영업이익률', fmt(row['op_margin'], '%'),
                           VAL_UP if (row['op_margin'] or 0) >= 10 else VAL_DN if (row['op_margin'] or 0) < 0 else VAL)
                    + cell(CELL_R, '재무상태', '흑자' if is_p else '적자',
                           VAL_UP if is_p else VAL_DN)
                    + '</tr>'
                    # 3행
                    '<tr>'
                    + cell(CELL_B, '순이익', ni_s,
                           VAL_UP if row['net_income'] and row['net_income'] > 0 else VAL_DN)
                    + cell(CELL_B, '매출액', rv_s)
                    + cell(CELL_BR, '투자등급',
                           '★★★★★' if (i == 0 and is_p) else '★★★★' if (i <= 1 and is_p) else '★★★★' if (i <= 3 and is_p) else '★★★' if (i <= 5 and is_p) else '★★' if (i <= 7 and is_p) else '★★' if (not is_p and i <= 5) else '★')
                    + '</tr>'
                    '</tbody></table>'
                )
                INFOG_STORE[f'SGRID:{row["name"]}'] = sg

            # ── 전체 종목카드 HTML Python 직접 생성 ──
            rank_titles = ["대장주","부대장주","3위","4위","5위","6위","7위","8위","9위","10위"]
            border_colors = {0:"#f59e0b", 1:"#94a3b8"}
            bg_colors = {0:"linear-gradient(135deg,#fffbeb,#fff)", 1:"linear-gradient(135deg,#f8fafc,#fff)"}
            stars_map = {0:"★★★★★",1:"★★★★",2:"★★★★",3:"★★★",4:"★★★",5:"★★★",6:"★★",7:"★★",8:"★",9:"★"}
            score_map = {0:5.0,1:4.5,2:4.0,3:3.5,4:3.0,5:3.0,6:2.5,7:2.0,8:1.5,9:1.0}

            sc_parts = []
            for i, row in df.iterrows():
                rt    = rank_titles[i] if i < len(rank_titles) else f"{i+1}위"
                is_p  = row['is_profit']
                ni_s  = (f"{int(row['net_income'])//int(1e8):,}억원" if row['net_income'] else "N/A")
                rv_s  = (f"{int(row['revenue'])//int(1e8):,}억원"   if row['revenue']    else "N/A")
                star  = stars_map.get(i,"★") if is_p else ("★★" if i<=5 else "★")
                score = score_map.get(i,1.0) if is_p else max(score_map.get(i,1.0)-1.0, 0.5)

                bc = border_colors.get(i, "#ef4444" if not is_p else "#4f46e5")
                bg = bg_colors.get(i, "linear-gradient(135deg,#fff5f5,#fff)" if not is_p else "white")

                sg_html    = INFOG_STORE.get("SGRID:" + row['name'], '')
                chart_html = CHART_STORE.get(row['name'], '')

                # ★ tip_box 동적 생성 — 하드코딩 본문 금지 규정 (CLAUDE.md)
                # _make_stock_tip 가 Claude 호출로 매번 다른 표현 생성. fallback 도 다양 변형.
                op_good = (row['op_margin'] or 0) > 5 if is_p else False
                per_ok  = bool(row['per'] and row['per'] < 50) if is_p else False
                _tip_label, _tip_body = _make_stock_tip(
                    theme_name, row['name'], is_p, op_good, per_ok,
                )
                if is_p:
                    tip_box = (
                        '<div style="background:linear-gradient(135deg,#eff6ff,#dbeafe);'
                        'border-radius:12px;padding:16px 20px;margin:16px 0;border-left:4px solid #3b82f6;">'
                        f'<div style="font-weight:800;color:#1d4ed8;margin-bottom:6px;font-size:14px;">{_tip_label}</div>'
                        '<p style="font-size:13px;color:#1e40af;margin:0;line-height:1.7;">'
                        + _tip_body
                        + '</p></div>'
                    )
                else:
                    tip_box = (
                        '<div style="background:linear-gradient(135deg,#fff7ed,#ffedd5);'
                        'border-radius:12px;padding:16px 20px;margin:16px 0;border-left:4px solid #f97316;">'
                        f'<div style="font-weight:800;color:#c2410c;margin-bottom:6px;font-size:14px;">{_tip_label}</div>'
                        '<p style="font-size:13px;color:#9a3412;margin:0;line-height:1.7;">'
                        + _tip_body
                        + '</p></div>'
                    )

                parts = [
                    '<div style="background:' + bg + ';border-radius:18px;padding:28px 32px;margin:28px 0;'
                    'box-shadow:0 2px 16px rgba(0,0,0,0.07);border-left:5px solid ' + bc + ';">',
                    '<h3 style="font-size:21px;font-weight:800;margin:0 0 4px;">'
                    + rt + '. ' + row['name'] + ' (' + row['ticker'] + ')</h3>',
                    '<div style="font-size:13px;color:#888;margin-bottom:18px;">시총 '
                    + str(i+1) + '위 | ' + ('흑자' if is_p else '적자') + '</div>',
                    '<p style="font-size:15px;color:#4a5568;line-height:1.9;margin-bottom:12px;">'
                    + _make_stock_analysis(
                        theme_name, row['name'], row['ticker'], rt, is_p,
                        row['cap'], row.get('revenue',0) or 0, row.get('net_income',0) or 0,
                        row.get('per'), row.get('roe'), row.get('op_margin')
                      ).replace('\n', '<br>')
                    + '</p>',
                    sg_html,
                    '<p style="font-size:15px;color:#4a5568;line-height:1.9;margin:12px 0;">'
                    + _make_company_biz_desc(
                        theme_name, row['name'], row['ticker'], is_p, rank=i
                      ).replace('\n', '<br>')
                    + '</p>',
                    chart_html,
                    tip_box,
                    '<div style="font-size:20px;margin:10px 0 6px;">투자 매력도: '
                    + star + ' (' + str(score) + '/5.0)</div>',
                    '</div>',
                ]
                sc_parts.append('\n'.join(parts))

            INFOG_STORE['STOCKCARDS'] = '\n'.join(sc_parts)


            # ── writer에게 전달할 데이터 블록 ──
            data_block = f"\n{'='*60}\n실제 수집된 데이터 (절대 변경 금지)\n{'='*60}\n"
            data_block += f"분석일: {today}\n"
            data_block += "\n[순위표] — HTML로 자동 생성됨. 글에서 {[TABLE]} 플레이스홀더 삽입\n"
            data_block += "\n[시총 순위 텍스트]\n순위|종목명|티커|현재가|시총(억)|재무|PER|ROE|영업이익률\n"
            for i,row in df.iterrows():
                rl=ranks[i] if i<len(ranks) else f"{i+1}위"
                st="흑자" if row['is_profit'] else "적자"
                data_block += (f"{rl}|{row['name']}|{row['ticker']}|"
                               f"{row['price']:,}원|{row['cap']//int(1e8):,}억|{st}|"
                               f"{fmt(row['per'],'배')}|{fmt(row['roe'],'%')}|{fmt(row['op_margin'],'%')}\n")
            data_block += "\n[종목별 상세 — 이 수치 그대로만 사용]\n"
            for i,row in df.iterrows():
                rl=ranks[i] if i<len(ranks) else f"{i+1}위"
                ni_s=f"{int(row['net_income'])//int(1e8):,}억원" if row['net_income'] else "N/A"
                rv_s=f"{int(row['revenue'])//int(1e8):,}억원" if row['revenue'] else "N/A"
                rl_label = rank_labels[i] if i < len(rank_labels) else f"시총 {i+1}위"
                data_block += (f"\n[{rl}] {row['name']} ({row['ticker']}) — {rl_label}\n"
                               f"  현재가: {row['price']:,}원\n"
                               f"  시총: {row['cap']//int(1e8):,}억원\n"
                               f"  재무: {'흑자' if row['is_profit'] else '적자'}\n"
                               f"  PER: {fmt(row['per'],'배')}\n"
                               f"  ROE: {fmt(row['roe'],'%')}\n"
                               f"  영업이익률: {fmt(row['op_margin'],'%')}\n"
                               f"  순이익: {ni_s}\n"
                               f"  매출: {rv_s}\n"
                               f"  stat-grid 플레이스홀더: [SGRID:{row['name']}]\n"
                               f"  주가차트 플레이스홀더: {{{CHART:{row['name']}}}}\n")
            data_block += f"\n{'='*60}\n"

            # 전역 저장 (generate_report에서 writer task에 주입)
            COLLECTED_DATA['data_block'] = data_block
            COLLECTED_DATA['today'] = today
            COLLECTED_DATA['df'] = df.to_dict('records')

            return data_block

        except Exception as e:
            import traceback; return f"[오류] {e}\n{traceback.format_exc()}"

def make_tasks(theme_name):
    # 글자수 정책은 length_manager 단일 진입점 — prompt 분량 표현도 동적 주입
    try:
        from JARVIS02_WRITER import length_manager as _LM
    except ImportError:
        import length_manager as _LM

    from shared.llm import ClaudeSDKLLM
    _llm_researcher = ClaudeSDKLLM(alias="writer_fast", max_tokens=800)
    _llm_auditor    = ClaudeSDKLLM(alias="writer_fast", max_tokens=2500)
    _llm_writer     = ClaudeSDKLLM(alias="writer",      max_tokens=8000)

    researcher = Agent(
        role='주식 리서처',
        goal=f'{{{theme_name}}} 테마 한국 상장 관련주 {_LM.STOCK_COUNT_PER_POST}개 + 야후파이낸스 티커',
        backstory=("'종목명:야후티커' 형식. 코스피→.KS, 코스닥→.KQ.\n"
                   "⚠️ 반드시 현재 KRX에 상장 중인 종목만 선택. 상장폐지/상호변경 종목 제외.\n"
                   "상장폐지 확정: SK머티리얼즈(036490.KS), 아이씨디(111290.KQ) → 절대 포함 금지.\n"
                   "\n"
                   "[핵심 원칙] 테마명을 정확히 분석하여 직접 연관 종목만 선정.\n"
                   "테마와 간접적으로만 연관된 종목은 절대 포함하지 말 것.\n"
                   "\n"
                   "'日 수출 규제(국산화 등)' 테마 예시:\n"
                   "  ✅ 포함: 불화수소/불화아르곤 생산기업, 포토레지스트 제조사, 플루오린폴리이미드 관련사,\n"
                   "          EUV 소재 국산화 기업, 고순도 화학소재 기업, 반도체 세정액 국산화 기업,\n"
                   "          일본 의존 소재·부품 국산화 추진 중소기업\n"
                   "  ❌ 제외: 단순 반도체 장비사, 반도체 제조사(삼성/SK하이닉스 등),\n"
                   "          일본 규제와 직접 관련 없는 IT/전자 기업\n"
                   "\n"
                   "[예시] 초전도체: 서남:294630.KQ, 덕성:004560.KS, 신성델타테크:065350.KQ, 모비스:250060.KQ, 파워로직스:047310.KQ, 국일신동:060370.KS, 원익QnC:074600.KQ, 수산인더스트리:097520.KQ, 씨씨에스:066790.KQ, 서원:021050.KS\n"
                   "[예시] 日수출규제: 솔브레인:357780.KS, 후성:093370.KS, 동진쎄미켐:005290.KS, 티씨케이:064760.KQ, 한솔케미칼:014680.KS, 이엔에프테크:102710.KQ, 상아프론테크:089980.KQ, 코미코:183300.KQ, 원익QnC:074600.KQ, 덕산네오룩스:213420.KS"),
        llm=_llm_researcher, verbose=False)

    auditor = Agent(
        role='재무 분석관',
        goal='theme_finance_tool로 실데이터 수집',
        backstory="theme_finance_tool 호출 → 결과 수정 없이 전달.",
        tools=[ThemeFinanceTool()], llm=_llm_auditor, verbose=False)

    # writer를 테마별로 동적 생성
    writer_dynamic = Agent(
        role='마켓시그널 수석 에디터',
        goal='수집된 실데이터를 그대로 사용한 전문 HTML 블로그',
        backstory=f"""마켓시그널 수석 에디터. 실데이터 그대로 사용한 전문 HTML 블로그 작성.

[데이터] auditor 수치 한 자리도 변경 금지. 없으면 N/A. 임의 수치 생성 절대 금지.
[플레이스홀더] 단독 줄·<p> 없이 그대로: [IMG:img01]~[IMG:img14] / [TABLE] / [STOCKCARDS](직접작성 금지)
[HTML 섹션 순서]
1.히어로(제목+부제)
2.{theme_name} 개념→[IMG:img01]  3.원리/배경→[IMG:img02]  4.활용분야→[IMG:img03]
5.역사→[IMG:img04]  6.투자용어→[IMG:img05]  7.관련주→[IMG:img06][IMG:img07]
8.[TABLE]단독줄  9.[STOCKCARDS]단독줄
10.PER→[IMG:img08]  11.수익성→[IMG:img09]  12.매출/순이익→[IMG:img10]
13.3개월수익률→[IMG:img11](1개월/6개월 언급금지)  14.위험도→[IMG:img12]
15.투자전략→[IMG:img13]  16.투자원칙→[IMG:img14]  17.면책조항
[분량] 각 섹션(2~8,10~17) 본문 {_LM.build_length_phrase(_LM.BRIEF_SECTION_SENTS_LO, _LM.BRIEF_SECTION_SENTS_HI)}. 전체 {_LM.build_length_phrase(_LM.BRIEF_REPORT_SENTS_LO, _LM.BRIEF_REPORT_SENTS_HI)}. 이미지가 상세 담당.
[완결] 토큰 한계 시 요약해서라도 면책조항까지 반드시 완성. 문장 중간 끊기 절대 금지.
[절대규칙] <!DOCTYPE html>로 시작. ©/footer/마켓시그널 금지. 마크다운 기호 금지. 응답 첫글자 <.
[말투] 본문:해요체(~해요/~이에요/~있어요). 면책조항만:~입니다/~습니다체.""",
        llm=_llm_writer, verbose=False)

    task1=Task(
        description=f"""'{theme_name}' 테마와 직접 연관된 KRX 상장 종목 {_LM.STOCK_COUNT_PER_POST}개를 선정하라.

규칙:
1. 반드시 '{theme_name}' 테마의 핵심 사업을 영위하는 기업만 선정
2. 간접 관련 기업, 단순 수혜 기업은 제외
3. 현재 KRX에 상장된 기업만 (상장폐지/합병 기업 절대 제외)
4. '종목명:야후파이낸스티커' 형식, 쉼표 구분
5. 코스피→.KS, 코스닥→.KQ

출력 예시: 솔브레인:357780.KS, 후성:093370.KS, 동진쎄미켐:005290.KS, ...""",
        agent=researcher, expected_output=f"종목명:야후티커 {_LM.STOCK_COUNT_PER_POST}개 쉼표 구분")
    task2=Task(
        description="리서처 종목 리스트를 theme_finance_tool에 그대로 전달. 결과 수정 없이 반환.",
        agent=auditor, expected_output="theme_finance_tool 전체 출력")
    task3=Task(
        description=(
            f"{theme_name} 블로그를 순수 HTML로 작성.\n"
            "auditor가 제공한 데이터 블록의 수치를 단 한 자리도 변경 금지.\n"
            "종목카드는 절대 직접 작성하지 말 것 — [STOCKCARDS] 플레이스홀더 한 줄만 삽입.\n"
            "모든 플레이스홀더([IMG:...], [TABLE], [STOCKCARDS])는 <p> 없이 단독 줄로만 삽입."
        ),
        agent=writer_dynamic,
        expected_output="완성된 HTML, 실제수치, 시총순 종목카드, 모든 플레이스홀더 단독줄 삽입, 면책조항 마무리",
        context=[task2]
    )
    return [task1, task2, task3], writer_dynamic


def generate_report(theme_name: str) -> str:
    global INFOG_STORE, COLLECTED_DATA
    # ★ .clear() 사용 — = {} 는 로컬 rebinding 버그 (원본 dict 미비워짐)
    # CHART_STORE 는 _tc.CHART_STORE 와 동일 객체이므로 _tc 를 통해 한 번만 clear
    INFOG_STORE.clear(); COLLECTED_DATA.clear()
    COLLECTED_DATA['theme_name'] = theme_name
    try:
        import JARVIS06_IMAGE.theme_charts as _tc
        _tc.CHART_STORE.clear()
    except Exception:
        pass

    print(f"  분석 시작: [{theme_name}]")
    tasks, writer_dynamic = make_tasks(theme_name)
    crew = Crew(agents=[researcher,auditor,writer_dynamic], tasks=tasks, process=Process.sequential)
    result = crew.kickoff(inputs={'theme_name': theme_name})
    content = str(result)

    # crew.kickoff 후 INFOG_STORE 누락분 직접 채우기
    # ① df 불필요 차트 — 폴백: 동적 인포그래픽 우선, 실패 시 정적 함수
    import uuid as _uuid_fb
    _fb_run_id = _uuid_fb.uuid4().hex
    from JARVIS06_IMAGE.dynamic_infographic import generate_dynamic_infographic as _dyn_fb
    from JARVIS06_IMAGE.infographic_charts import (
        make_hub_spoke as _mk_hub, make_hex_node_map as _mk_hex,
        make_premium_timeline as _mk_tl,
    )
    _fb_ctx = COLLECTED_DATA.get('summary_text', '') or theme_name
    _fb_purpose_map = {
        'img01': (f"{theme_name} 핵심 개념 구조도", lambda: _mk_hub(theme_name, _fb_run_id)),
        'img02': (f"{theme_name} 작동 원리 흐름도", lambda: make_theme_mechanism_chart(theme_name)),
        'img03': (f"{theme_name} 활용 분야 마인드맵", lambda: _mk_hex(theme_name, _fb_run_id)),
        'img04': (f"{theme_name} 역사 타임라인", lambda: _mk_tl(theme_name, _fb_run_id)),
        'img05': (None, lambda: make_terms_chart(theme_name)),       # 동적 불필요
        'img13': (f"{theme_name} 투자 전략 인포그래픽", lambda: make_portfolio_chart([])),
        'img14': (f"{theme_name} 투자 원칙 체크리스트", lambda: make_checklist_chart()),
    }
    _theme_only_keys = ('img01','img02','img03','img04','img05','img13','img14')
    for _k in _theme_only_keys:
        if _k not in INFOG_STORE:
            try:
                _purpose_ko, _static_fn = _fb_purpose_map[_k]
                if _purpose_ko:
                    # ★ 키워드 인자 명시 — positional 4번째가 data= 로 잘못 매핑되는 버그 방지
                    _res = _dyn_fb(
                        theme_name, _purpose_ko, _fb_ctx[:600],
                        data=None,           # 폴백 블록은 df 없어 수치 불가 → None
                        run_id=_fb_run_id,
                        slot_key=_k,
                    )
                    INFOG_STORE[_k] = _res if _res else _static_fn()
                else:
                    INFOG_STORE[_k] = _static_fn()
                print(f"  ✅ {_k} 폴백 생성")
            except Exception as _e:
                print(f"  ⚠️ {_k} 폴백 생성 실패: {_e}")
                _g_report("writer", _e, module=__name__)

    # ② df 필요 차트 — df 있을 때만 폴백 생성
    if COLLECTED_DATA.get('df'):
        _df_needed = ('img06','img07','img08','img09','img10','img11','img12','img13')
        _missing_df = [k for k in _df_needed if k not in INFOG_STORE]
        if _missing_df:
            print(f"  ⚠️ df차트 누락 {_missing_df} → 직접 생성...")
            import pandas as pd
            df = pd.DataFrame(COLLECTED_DATA['df'])
            names = df['name'].tolist()
            caps  = df['cap'].tolist()
            pers  = df['per'].tolist()
            oms   = df['op_margin'].tolist()
            roes  = df['roe'].tolist()
            nis   = df['net_income'].tolist()
            rvs   = df['revenue'].tolist()
            tickers = df['ticker'].tolist()
            isp   = df['is_profit'].tolist()
            pc    = sum(isp); lc = len(isp) - pc
            _df_map = {
                'img06': lambda: make_profit_donut(pc, lc),
                'img07': lambda: make_cap_bar(names, caps),
                'img08': lambda: make_per_bar(names, pers),
                'img09': lambda: make_profitability_chart(names, oms, roes),
                'img10': lambda: make_revenue_chart(names, rvs, nis),
                'img11': lambda: make_theme_return_chart(names, tickers),
                'img12': lambda: make_risk_chart(names, isp, caps, pers),
                'img13': lambda: _mk_dash(theme_name, _fb_run_id),
            }
            for _k in _missing_df:
                try:
                    INFOG_STORE[_k] = _df_map[_k]()
                    print(f"  ✅ {_k} 폴백 생성")
                except Exception as _e:
                    print(f"  ⚠️ {_k} 폴백 생성 실패: {_e}")
                    _g_report("writer", _e, module=__name__)
            print(f"  ✅ df차트 폴백 완료 (총 {len(INFOG_STORE)}개)")
    else:
        print("  ⚠️ COLLECTED_DATA['df'] 없음 → img06~img13 생성 불가")

    # ── 마크다운 코드펜스 및 백틱 제거 ──
    import re as _re2
    # ```html ... ``` 제거
    content = _re2.sub(r'```html\s*', '', content)
    content = _re2.sub(r'```\s*', '', content)
    # ` (백틱 1개) 단독 줄 제거
    content = _re2.sub(r'^\s*`+\s*$', '', content, flags=_re2.MULTILINE)
    content = content.strip()


    # ── 플레이스홀더 교체 ──
    import re as _re

    def _ph(name):
        """플레이스홀더 패턴: [NAME] 또는 <p>[NAME]</p> 형식 모두 교체"""
        esc = _re.escape(name)
        return _re.compile(
            r'<p>\s*\[' + esc + r'\]\s*</p>'   # <p>[NAME]</p>
            r'|\[' + esc + r'\]',                  # [NAME]
            _re.IGNORECASE
        )

    # 1. 순위표 [TABLE]
    if "TABLE" in INFOG_STORE:
        content = _ph("TABLE").sub(INFOG_STORE["TABLE"], content)

    # 2. 종목카드 전체 [STOCKCARDS] — Python 생성본으로 교체
    if "STOCKCARDS" in INFOG_STORE:
        content = _ph("STOCKCARDS").sub(INFOG_STORE["STOCKCARDS"], content)
        print(f"  종목카드 {len(CHART_STORE)}개 삽입 완료")
    else:
        print("  ⚠️ STOCKCARDS 없음")

    # 3. 인포그래픽 [IMG:img01~img14]
    # ★ ERRORS [171] 2026-05-26: count=1 로 첫 번째 occurrence만 교체
    # LLM이 동일 [IMG:imgXX] 를 여러 번 쓰면 같은 차트가 중복 삽입되는 버그 수정
    img_ok = 0
    for key, html in INFOG_STORE.items():
        if key.startswith("img"):
            new_c = _ph("IMG:" + key).sub(html, content, count=1)
            if new_c != content:
                img_ok += 1
            content = new_c
    # 잔여 중복 [IMG:imgXX] 플레이스홀더 제거 (count=1로 첫 교체 후 남은 것)
    for key in INFOG_STORE:
        if key.startswith("img"):
            content = _ph("IMG:" + key).sub("", content)

    print(f"  인포그래픽 {img_ok}/14개 삽입 완료")

    # 4. [종목소개_xxx], [종목분석_xxx] — writer가 채운 텍스트 그대로 유지 (교체 불필요)
    # (이미 STOCKCARDS 안에 포함됐지만, writer가 직접 카드를 쓴 경우 SGRID/CHART 잔여 교체)
    for key, html in list(INFOG_STORE.items()):
        if key.startswith("SGRID:"):
            content = _ph(key).sub(html, content)
    for name, html in CHART_STORE.items():
        content = _ph("CHART:" + name).sub(html, content)

    # 미삽입 인포그래픽 확인 및 강제 말미 삽입
    missing = []
    for key in INFOG_STORE:
        if key.startswith("img") and ("[IMG:" + key + "]") in content:
            missing.append(key)
    if missing:
        print(f"  미삽입 인포그래픽: {missing} → 말미에 강제 삽입")
        extra = '\n<div class="sec"><h2>참고 차트</h2>'
        for key in missing:
            extra += INFOG_STORE[key]
            content = content.replace(f"{{{IMG:{key}}}}", "")
        extra += '</div>'
        # 면책조항 직전에 삽입
        content = content.replace('<div class="disc">', extra + '\n<div class="disc">')

    if '</div>' not in content[-200:]:
        content += '<p style="color:#9ca3af;font-size:12px;text-align:center;padding:20px;">투자 참고용. 책임은 본인에게 있습니다.</p>'

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    from pathlib import Path
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    backup_path = logs_dir / f"market_signal_{ts}.txt"
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  백업 완료 ({len(content):,}자) → logs/market_signal_{ts}.txt")
    return content


# ════════════════════════════════════════════════════════════════
#  ★ 경량 종목 데이터 수집 (CrewAI 제거 — trend_theme_writer 용)
#  generate_report (CrewAI 3-agent) 의 *데이터 수집 부분만* 추출.
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
    stocks = [s for s in (stocks_data or {}).get("stocks") or [] if s.get("name")][:max_stocks]
    if len(stocks) < 2:
        return []
    theme = str((stocks_data or {}).get("theme") or "").strip() or "테마"
    _src = {"provider": "krx", "name": "네이버 금융(KRX 시세)",
            "url": "https://finance.naver.com", "as_of": _d_std.today().isoformat()}

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

    specs = [
        ("price",  1.0,   0, False, f"{theme} 관련주 현재가",   "원",  "bar_chart"),
        ("marcap", 1e-12, 2, False, f"{theme} 관련주 시가총액", "조원", "bar_chart"),
        ("roe",    100.0, 1, True,  f"{theme} 관련주 ROE",     "%",   "bar_chart"),
        ("per",    1.0,   1, False, f"{theme} 관련주 PER",     "배",  "bar_chart"),
        ("revenue", 1e-12, 2, False, f"{theme} 관련주 연매출",  "조원", "bar_chart"),
    ]
    datasets = []
    for field, scale, nd, neg, title, unit, viz in specs:
        rows = _rows(field, scale, nd, neg)
        if len(rows) >= 2:      # 1행짜리는 비교 차트로 무의미 — 승격 제외
            datasets.append({"title": title, "unit": unit, "viz_hint": viz,
                             "data": rows, "source": dict(_src)})
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
            "per":        fin.get("per"),
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

    # ── ③ 대장주·부대장주 사업·기술·관계 enrich (★ 사용자 박제 2026-05-14) ──
    # 시총 1·2위만 LLM 으로 사업/기술/관계 3필드 채움. 나머지 5종목은 통합 섹션
    # 사용 → 개별 enrich 불필요. theme_html_writer Pass-1 prompt 가 이 필드를 사용.
    def _enrich_leader_desc(stock: dict, theme: str) -> dict:
        try:
            from shared.llm import invoke_text
            _phrase = _LM.build_length_phrase(1, 2)  # "1~2문장(약 50~100자)"
            prompt = (
                f"한국 KRX 상장사 '{stock['name']}' (테마: {theme}).\n"
                f"아래 3항목을 각 {_phrase} 으로 작성. JSON 만 출력. 마크다운·이모지 금지.\n"
                f"{{\n"
                f'  "business":  "사업성·주력 제품·매출 구조 ({_phrase})",\n'
                f'  "tech":      "핵심 기술·경쟁력·R&D ({_phrase})",\n'
                f'  "relation":  "타 회사와의 영업·공급망·경쟁 관계 ({_phrase})"\n'
                f"}}"
            )
            raw = invoke_text("writer_fast", prompt, temperature=0.3, max_tokens=400)
            import json as _json
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", raw or "")
            if m:
                obj = _json.loads(m.group(0))
                stock["business"] = (obj.get("business") or "").strip()
                stock["tech"]     = (obj.get("tech") or "").strip()
                stock["relation"] = (obj.get("relation") or "").strip()
        except Exception as e:
            print(f"  ⚠️ [stocks_data] {stock['name']} enrich 실패: {e}")
        return stock

    # ★ 검증 (2026-07-02): 현재가 실취득 실패 종목 드롭 — 표·차트에 N/A·거짓수치가
    #   흘러가지 않게. price 미취득은 스크레이핑 실패로 간주(하류 verification 레지스트리
    #   'collect_stocks_data' 체크와 짝). 드롭 후 2종목 미만이면 상위가 테마 교체.
    _n_before = len(stocks)
    stocks = [s for s in stocks if _valid_stock_price(s)]
    if len(stocks) < _n_before:
        print(f"  🧹 [stocks_data] 현재가 미취득 {_n_before - len(stocks)}종목 드롭 (거짓데이터 방지)")

    if len(stocks) >= 2:
        # 순차 enrich
        # _enrich_leader_desc → invoke_text("writer_fast") → Claude Code SDK 통과.
        with ThreadPoolExecutor(max_workers=1) as ex:
            futs = [ex.submit(_enrich_leader_desc, stocks[i], theme_name) for i in (0, 1)]
            for fut in as_completed(futs):
                try:
                    fut.result(timeout=30)
                except Exception as _e:
                    _g_report("collect_theme", _e, module="collect_theme", func_name="stocks_data.enrich_future")
        print(f"  ✅ [stocks_data] 대장주·부대장주 사업/기술/관계 enrich 완료")

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
