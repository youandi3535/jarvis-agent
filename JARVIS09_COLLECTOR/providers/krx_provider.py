"""KRX 한국거래소 프로바이더 — pykrx (API 키 불필요).

수집 전략:
  - 주요 KOSPI/KOSDAQ 종목 고정 리스트 + get_market_ohlcv_by_date (안정적 API 사용)
  - 테마 키워드와 종목명 매칭 → 관련 종목 시세 반환
  - get_market_ticker_list / get_market_cap_by_ticker 는 날짜 의존성으로 불안정 → 사용 안 함

★ KRX 로그인 (선택):
  pykrx는 import 시 webio.py:build_krx_session() 즉시 실행 → os.getenv("KRX_ID/PW") 읽음.
  .env에 KRX_ID/KRX_PW 설정 시 자동 로그인 → 지수 API 사용 가능.
  미설정이면 "KRX 로그인 실패" 경고만 출력되고 종목 시세(공개 API)는 정상 수집.
"""
from __future__ import annotations
import os
from pathlib import Path

# pykrx import 전에 .env 로드 (webio.py가 모듈 레벨에서 즉시 KRX_ID/PW 읽음)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env", override=False)
except Exception:
    pass

from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.krx")

# 주요 KOSPI/KOSDAQ 종목 (티커: 종목명) — 시가총액 상위권 대표 종목
_MAJOR_STOCKS: dict[str, str] = {
    # 반도체·IT
    "005930": "삼성전자",      "000660": "SK하이닉스",    "042700": "한미반도체",
    "009150": "삼성전기",      "018260": "삼성에스디에스", "035420": "NAVER",
    "035720": "카카오",        "030200": "KT",
    # 자동차·부품
    "005380": "현대차",        "000270": "기아",          "012330": "현대모비스",
    "094280": "현대글로비스",
    # 배터리·소재
    "006400": "삼성SDI",       "051910": "LG화학",        "003670": "포스코퓨처엠",
    "010130": "고려아연",
    # 바이오·헬스
    "207940": "삼성바이오로직스", "068270": "셀트리온",    "000100": "유한양행",
    "326030": "SK바이오사이언스",
    # 조선·방산
    "009540": "HD한국조선해양", "012450": "한화에어로스페이스", "267250": "HD현대",
    # 금융·보험
    "055550": "신한지주",      "105560": "KB금융",        "086790": "하나금융지주",
    "032830": "삼성생명",      "000810": "삼성화재",
    # 철강·에너지
    "005490": "POSCO홀딩스",   "096770": "SK이노베이션",  "034730": "SK",
    "028260": "삼성물산",
    # 항공·물류
    "003490": "대한항공",      "020560": "아시아나항공",
    # 엔터·게임
    "251270": "넷마블",        "112040": "위메이드",      "041510": "에스엠",
    # KOSDAQ 대표
    "247540": "에코프로비엠",  "086520": "에코프로",      "373220": "LG에너지솔루션",
    "011200": "HMM",
}

# 한글 숫자 포함 등락률 포맷
def _fmt_chg(v) -> str:
    try:
        return f"{float(v):+.1f}%"
    except Exception:
        return "N/A"


def _last_trading_day():
    """가장 최근 거래일 반환. 토요일→금요일, 일요일→금요일."""
    from datetime import date, timedelta
    today = date.today()
    dow = today.weekday()  # 0=월 … 4=금 5=토 6=일
    if dow == 5:
        return today - timedelta(days=1)
    if dow == 6:
        return today - timedelta(days=2)
    return today


def _recent_dates() -> tuple[str, str]:
    """최근 7영업일 범위 (start, end). 주말이면 마지막 거래일 기준."""
    from datetime import timedelta
    end = _last_trading_day()
    start = end - timedelta(days=10)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# ★ 업종별 시가총액 쿼리 감지 키워드 (ERRORS [418][420])
_SECTOR_BREAKDOWN_KWS = frozenset([
    "업종별", "업종비중", "섹터", "업종구성", "업종 비중", "업종 시가총액",
    "바이오", "반도체", "제약", "it서비스", "전기전자",
])

# ★ 코스닥 150 섹터 지수 — 8개 하위 지수 코드 (ERRORS [420])
_KOSDAQ150_SECTOR_CODES = {
    "2203": "코스닥 150",
    "2212": "코스닥 150 소재",
    "2213": "코스닥 150 산업재",
    "2214": "코스닥 150 필수소비재",
    "2215": "코스닥 150 자유소비재",
    "2216": "코스닥 150 정보기술",
    "2217": "코스닥 150 헬스케어",
    "2218": "코스닥 150 커뮤니케이션서비스",
}
# 코스닥 150 쿼리 감지 키워드 (공백 제거 버전 포함 — theme_lower = theme.lower().replace(" ", ""))
_KOSDAQ150_KWS = frozenset([
    "코스닥150", "kosdaq150",                            # ★ 공백 제거 버전 (theme_lower 매칭)
    "코스닥 150", "kosdaq 150",                          # 공백 포함 버전 (theme.lower() 매칭)
    "150섹터", "150지수", "150소재", "150헬스케어",      # 공백 제거 복합 키워드
    "섹터지수",                                          # "섹터 지수" 공백 제거
])


def _collect_sector_market_cap(market: str = "KOSDAQ") -> RawDocument | None:
    """코스닥/코스피 업종별 시가총액 비중 수집 — pykrx get_market_sector_classifications.

    ★ 왜 필요한가: "코스닥 업종별 비중", "바이오 반도체 IT 비중" 쿼리를 web/news 소스로 받으면
      틀린 수치가 게이트를 통과함 (ERRORS [418]). KRX 실데이터로만 정확한 비중을 얻을 수 있다.
    """
    try:
        from pykrx import stock as krx_stock
    except ImportError:
        log.warning("[KRX] pykrx 미설치 — 업종별 시가총액 수집 불가")
        return None

    dt = _last_trading_day()
    dt_str = dt.strftime("%Y%m%d")
    as_of = dt.strftime("%Y년 %-m월 %-d일")

    try:
        df = krx_stock.get_market_sector_classifications(dt_str, market=market)
        if df is None or df.empty or "업종명" not in df.columns or "시가총액" not in df.columns:
            log.warning(f"[KRX] {market} 업종 분류 데이터 없음 ({dt_str})")
            return None

        sector_cap = df.groupby("업종명")["시가총액"].sum().sort_values(ascending=False)
        total = sector_cap.sum()
        if total <= 0:
            return None

        mkt_label = "코스닥" if market == "KOSDAQ" else "코스피"
        lines = [
            f"[KRX 실데이터: {mkt_label} 업종별 시가총액 비중]",
            f"기준일: {as_of}  전체 시가총액: {total/1e12:.1f}조원",
            f"출처: 한국거래소(KRX) pykrx get_market_sector_classifications",
            "",
            "업종명|시가총액(조원)|비중(%)",
        ]
        for sec_name, cap in sector_cap.items():
            pct = cap / total * 100
            lines.append(f"{sec_name}|{cap/1e12:.1f}|{pct:.1f}")

        lines += [
            "",
            f"총 {len(sector_cap)}개 업종 집계 ({mkt_label} 전체 상장 종목 기준).",
            f"데이터 출처: 한국거래소 공식 API / {as_of} 기준",
        ]

        log.info(f"[KRX] {mkt_label} 업종별 시가총액 {len(sector_cap)}개 업종, 전체 {total/1e12:.0f}조원")
        return RawDocument(
            url=f"krx://sector_market_cap_{market}_{dt_str}",
            source_type="krx",
            raw_text="\n".join(lines),
            title=f"KRX {mkt_label} 업종별 시가총액 비중 ({as_of})",
            extra={"market": market, "as_of": as_of, "total_cap_tril": round(total / 1e12, 1)},
        )
    except Exception as e:
        log.warning(f"[KRX] {market} 업종별 시가총액 수집 실패: {e}")
        return None


def _collect_kosdaq150_sectors() -> RawDocument | None:
    """코스닥 150 전체 섹터 지수(8개) 수집 — pykrx get_index_ohlcv_by_date.

    ★ 왜 필요한가: "코스닥 150 섹터 지수" 쿼리를 KOSIS/web에서 받으면 8개 섹터 중 일부만
      수집되어 최고/최저 KPI가 잘못됨 (ERRORS [420]). 실제 최고는 헬스케어(5,873)인데
      소재(3,194)가 "최고"로 표시된 사례. KRX get_index_ohlcv_by_date로 8개 전부 보장.
    """
    try:
        from pykrx import stock as krx_stock
    except ImportError:
        log.warning("[KRX] pykrx 미설치 — 코스닥 150 섹터 지수 수집 불가")
        return None

    dt = _last_trading_day()
    dt_str = dt.strftime("%Y%m%d")
    start_str = (dt - __import__("datetime").timedelta(days=3)).strftime("%Y%m%d")
    as_of = dt.strftime("%Y년 %-m월 %-d일")

    rows = []
    for code, name in _KOSDAQ150_SECTOR_CODES.items():
        try:
            df = krx_stock.get_index_ohlcv_by_date(start_str, dt_str, code)
            if df is not None and not df.empty and "종가" in df.columns:
                val = float(df["종가"].iloc[-1])
                rows.append({"name": name, "code": code, "value": val})
        except Exception as e:
            log.debug(f"[KRX] 코스닥 150 지수 {name}({code}) 수집 실패: {e}")

    if len(rows) < 3:
        log.warning(f"[KRX] 코스닥 150 섹터 지수 수집 부족: {len(rows)}개")
        return None

    rows.sort(key=lambda r: r["value"], reverse=True)
    top = rows[0]
    bot = rows[-1]

    lines = [
        "[KRX 실데이터: 코스닥 150 섹터 지수]",
        f"기준일: {as_of}  데이터: {len(rows)}개 섹터 전체",
        f"출처: 한국거래소(KRX) pykrx get_index_ohlcv_by_date",
        "",
        f"최고 섹터: {top['name']} = {top['value']:,.1f}",
        f"최저 섹터: {bot['name']} = {bot['value']:,.1f}",
        "",
        "섹터명|지수값",
    ]
    for r in rows:
        lines.append(f"{r['name']}|{r['value']:.1f}")

    lines += [
        "",
        f"※ 코스닥 150 하위 섹터 지수 {len(rows)}종 전체 포함.",
        f"데이터 출처: 한국거래소 공식 API / {as_of} 기준",
    ]

    log.info(f"[KRX] 코스닥 150 섹터 지수 {len(rows)}개 수집 완료 (최고: {top['name']} {top['value']:.0f})")
    return RawDocument(
        url=f"krx://kosdaq150_sectors_{dt_str}",
        source_type="krx",
        raw_text="\n".join(lines),
        title=f"KRX 코스닥 150 섹터 지수 전체 ({as_of})",
        extra={"as_of": as_of, "top_sector": top["name"], "top_val": top["value"],
               "bot_sector": bot["name"], "bot_val": bot["value"]},
    )


class KrxProvider(BaseProvider):
    """KRX 공식 데이터 — pykrx 안정 API 사용."""
    source_type = "krx"

    def collect(self, theme: str, sector: str = "", max_items: int = 8) -> list[RawDocument]:
        results: list[RawDocument] = []
        try:
            from pykrx import stock as krx_stock
        except ImportError:
            log.warning("[KRX] pykrx 미설치: pip install pykrx")
            return []

        start_dt, end_dt = _recent_dates()
        theme_lower = theme.lower().replace(" ", "")
        theme_words = [w for w in theme.split() if len(w) >= 2]
        if not theme_words and len(theme) >= 2:
            theme_words = [theme]

        # ── 0-A. 코스닥 150 섹터 지수 쿼리 감지 → 8개 섹터 전체 수집 (ERRORS [420]) ──
        # "코스닥 150 섹터", "코스닥 150 지수", "150 헬스케어" 등
        _is_kosdaq150_query = any(kw in theme_lower for kw in _KOSDAQ150_KWS)
        if _is_kosdaq150_query:
            k150_doc = _collect_kosdaq150_sectors()
            if k150_doc:
                results.append(k150_doc)
                log.info("[KRX] 코스닥 150 쿼리 감지 → 8개 섹터 지수 전체 수집 완료")

        # ── 0-B. 업종별 시가총액 비중 쿼리 감지 → KRX 실데이터 우선 (ERRORS [418]) ──
        # "코스닥 업종별", "바이오 반도체 IT 비중", "섹터 구성" 등 업종 비중 관련 쿼리
        _is_sector_query = any(kw in theme_lower for kw in _SECTOR_BREAKDOWN_KWS)
        if _is_sector_query:
            market = "KOSPI" if any(kw in theme_lower for kw in ("코스피", "kospi")) else "KOSDAQ"
            sector_doc = _collect_sector_market_cap(market)
            if sector_doc:
                results.append(sector_doc)
                log.info(f"[KRX] 업종 쿼리 감지 → {market} 업종별 시가총액 비중 수집 완료")

        # ── 1. 테마 관련 종목 시세 ───────────────────────────────────────
        matched_tickers = {
            ticker: name
            for ticker, name in _MAJOR_STOCKS.items()
            if any(w in name for w in theme_words)
        }

        if matched_tickers:
            lines = [f"['{theme}' 관련 종목 최근 시세]",
                     "종목명(티커)|최신종가|등락률|거래량"]
            ok = 0
            for ticker, name in list(matched_tickers.items())[:20]:   # ★ 8→20 상향 2026-07-17 (관련 종목 시세 더 많이)
                try:
                    df = krx_stock.get_market_ohlcv_by_date(start_dt, end_dt, ticker)
                    if df is not None and not df.empty and "종가" in df.columns:
                        row   = df.iloc[-1]
                        price = int(row["종가"])
                        vol   = int(row.get("거래량", 0))
                        chg   = _fmt_chg(row.get("등락률", 0))
                        date  = df.index[-1].strftime("%Y-%m-%d")
                        lines.append(f"{name}({ticker})|{date} {price:,}원|{chg}|{vol:,}주")
                        ok += 1
                except Exception as e:
                    log.debug(f"[KRX] {name} 시세 실패: {e}")

            if ok > 0:
                results.append(RawDocument(
                    url=f"krx://theme_{theme}",
                    source_type=self.source_type,
                    raw_text="\n".join(lines),
                    title=f"KRX '{theme}' 관련 종목 시세",
                    extra={"theme": theme, "source": "krx_theme"},
                ))
                log.info(f"[KRX] '{theme}' 관련 {ok}종목 시세 수집")

        # ── 2. 주요 종목 시세 요약 (테마 매칭 없을 때 대표 종목) ──────────
        _REPRESENTATIVE = ["005930", "000660", "005380", "006400", "068270"]
        lines = [f"[KRX 주요 종목 최근 시세 ({end_dt})]",
                 "종목명(티커)|최신종가|등락률|거래량"]
        ok = 0
        for ticker in _REPRESENTATIVE:
            name = _MAJOR_STOCKS.get(ticker, ticker)
            try:
                df = krx_stock.get_market_ohlcv_by_date(start_dt, end_dt, ticker)
                if df is not None and not df.empty and "종가" in df.columns:
                    row   = df.iloc[-1]
                    price = int(row["종가"])
                    vol   = int(row.get("거래량", 0))
                    chg   = _fmt_chg(row.get("등락률", 0))
                    date  = df.index[-1].strftime("%Y-%m-%d")
                    lines.append(f"{name}({ticker})|{date} {price:,}원|{chg}|{vol:,}주")
                    ok += 1
            except Exception as e:
                log.debug(f"[KRX] 대표종목 {name} 실패: {e}")

        if ok > 0:
            results.append(RawDocument(
                url="krx://major_stocks",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title=f"KRX 주요 종목 시세 ({end_dt})",
                extra={"theme": theme, "source": "krx_major"},
            ))
            log.info(f"[KRX] 주요 {ok}종목 시세 수집")

        log.info(f"[KRX] 총 {len(results)}건 수집 완료")
        return results[:max_items]


def collect_market_trading_volume() -> dict | None:
    """코스피·코스닥 최근 1개월 일평균 거래대금 수집.

    Returns:
        {"kospi": float, "kosdaq": float, "as_of": str} | None  (단위: 조원)
        실데이터 획득 실패 시 None — ADR 010: 거짓 합성 절대 금지.
    """
    try:
        from pykrx import stock as _st
    except ImportError:
        log.warning("[KRX] pykrx 미설치 — 시장 거래대금 수집 불가")
        return None

    from datetime import timedelta
    end = _last_trading_day()
    start = end - timedelta(days=28)  # ~4주(약 20 거래일)
    end_s = end.strftime("%Y%m%d")
    start_s = start.strftime("%Y%m%d")

    def _via_index_ohlcv(idx_code: str) -> float | None:
        """지수 OHLCV의 거래대금 컬럼 평균 (조원)."""
        try:
            df = _st.get_index_ohlcv_by_date(start_s, end_s, idx_code)
            if df is None or df.empty or "거래대금" not in df.columns:
                return None
            avg = df["거래대금"].dropna().mean()
            return round(float(avg) / 1e12, 1) if avg and avg > 0 else None
        except Exception:
            return None

    def _via_trading_value(market: str) -> float | None:
        """시장별 매도거래대금 평균 (조원) — 매수와 동일, 중복 집계 없음."""
        try:
            df = _st.get_market_trading_value_by_date(start_s, end_s, market)
            if df is None or df.empty:
                return None
            for col in df.columns:
                col_s = str(col)
                if "거래대금" in col_s and "매도" in col_s:
                    avg = df[col].dropna().mean()
                    return round(float(avg) / 1e12, 1) if avg and avg > 0 else None
            return None
        except Exception:
            return None

    kospi = _via_index_ohlcv("1001") or _via_trading_value("KOSPI")
    kosdaq = _via_index_ohlcv("2001") or _via_trading_value("KOSDAQ")

    if kospi is None and kosdaq is None:
        log.info("[KRX] 시장 거래대금 수집 실패 — 데이터 없음 (주말·API 제한)")
        return None

    return {
        "kospi": kospi,
        "kosdaq": kosdaq,
        "as_of": f"{end.year}년 {end.month}월 기준 최근 4주 평균",
    }
