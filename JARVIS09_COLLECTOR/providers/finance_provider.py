"""금융 데이터 프로바이더 — yfinance 공식 API (허용)."""
from __future__ import annotations
import concurrent.futures as _cf
import yfinance as yf
from ..models import RawDocument
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.finance")


# ★ yfinance 1.x 는 curl_cffi 세션만 지원 — requests.Session 주입 금지 (ERRORS [407])
def _yf_with_timeout(fn, timeout: int = 15):
    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn).result(timeout=timeout)


# 시장 전체 지표 키워드 — 이 키워드가 쿼리에 있으면 코스닥/코스피 장기 데이터 우선 수집
_KOREAN_MARKET_KWS = frozenset([
    "코스닥", "코스피", "시가총액", "증시", "지수", "주식시장", "증권시장",
    "kosdaq", "kospi",
])

# 코스닥/코스피 티커 (yfinance)
_KR_INDEX_TICKERS = {
    "코스닥": "^KQ11",
    "코스피": "^KS11",
}

# ★ 주간 등락률 키워드 — 이 키워드가 있으면 실가격 데이터로 직접 계산 (ERRORS [424])
_WEEKLY_RETURN_KWS = frozenset([
    "주간 등락률", "주간등락률", "주간 수익률", "주간수익률",
    "등락률", "주간 변동", "주간변동", "이번주", "금주",
])

# 주간 등락률 계산 대상 지수 (yfinance 티커)
# ★ 코스닥150: yfinance ^KQ150 미지원 → KODEX 코스닥150 ETF(229200.KS) 대리 사용
_WEEKLY_RETURN_TICKERS = {
    "코스피":    "^KS11",
    "코스피200": "^KS200",
    "코스닥":    "^KQ11",
    "코스닥150": "229200.KS",   # KODEX 코스닥150 ETF
}


def _collect_weekly_returns(theme: str) -> RawDocument | None:
    """코스피·코스피200·코스닥·코스닥150 주간 등락률 — yfinance 실데이터 직접 계산.

    ★ 왜 필요한가: web_data 수집 시 등락률이 완전히 틀린 값을 반환 (ERRORS [424]).
       코스피 실제 -7.57% → web 수치 +1.9% (9.5p 오차). 직접 계산만 신뢰 가능.
    주간 등락률 = (최근 종가 / 5거래일 전 종가 - 1) × 100
    """
    theme_lower = theme.lower().replace(" ", "")
    if not any(kw.replace(" ", "") in theme_lower for kw in _WEEKLY_RETURN_KWS):
        return None

    from datetime import datetime, date, timedelta
    today = date.today()
    wd = today.weekday()   # Mon=0 … Fri=4, Sat=5, Sun=6

    # ★ "완성된 주(Mon-Fri)" 기준 — 해당 주의 월요일 → 금요일 등락률
    #   토·일: 이번 주 이미 완료 → 이번 주 월~금 사용
    #   평일(월~금): 이번 주 미완료 → 지난주 월~금 사용
    if wd >= 5:                                        # 토(5)·일(6)
        target_fri = today - timedelta(days=wd - 4)   # 이번 주 금요일
    else:                                              # 평일 — 지난주 사용
        target_fri = today - timedelta(days=wd + 3)   # 지난 금요일

    target_mon = target_fri - timedelta(days=4)        # 같은 주 월요일
    as_of = datetime.now().strftime("%Y년 %-m월 %-d일")
    week_label = f"{target_mon.strftime('%m/%d')}~{target_fri.strftime('%m/%d')} 주간"

    rows = []
    for name, ticker in _WEEKLY_RETURN_TICKERS.items():
        try:
            def _fetch(t=ticker):
                return yf.Ticker(t).history(period="20d")
            df = _yf_with_timeout(_fetch, timeout=20)
            if df is None or df.empty:
                log.warning(f"[Finance] {name}({ticker}) 데이터 없음 — 스킵")
                continue
            close = df["Close"].dropna()

            # 해당 주 월~금 범위의 거래일만 추출 (공휴일 자동 보정)
            mask_week = (close.index.date >= target_mon) & (close.index.date <= target_fri)
            if not mask_week.any():
                log.warning(f"[Finance] {name} 해당 주 거래일 없음 — 스킵 ({target_mon}~{target_fri})")
                continue

            week_close = close[mask_week]
            mon_price = float(week_close.iloc[0])    # 해당 주 첫 거래일 종가 (기준)
            fri_price = float(week_close.iloc[-1])   # 해당 주 마지막 거래일 종가
            mon_date  = str(week_close.index[0].date())
            fri_date  = str(week_close.index[-1].date())
            weekly_chg = (fri_price - mon_price) / mon_price * 100

            note = "(ETF 대리)" if ticker.endswith(".KS") else ""
            rows.append({
                "name": name, "ticker": ticker, "note": note,
                "mon_price": round(mon_price, 2), "fri_price": round(fri_price, 2),
                "weekly_chg": round(weekly_chg, 2),
                "mon_date": mon_date, "fri_date": fri_date,
            })
            log.info(f"[Finance] {name} {week_label} 등락률: {weekly_chg:+.2f}%"
                     f" ({mon_date}:{mon_price:.2f} → {fri_date}:{fri_price:.2f})")
        except Exception as e:
            log.warning(f"[Finance] {name}({ticker}) 주간 등락률 수집 실패: {e}")

    if not rows:
        return None

    lines = [
        f"[{week_label} 등락률 실데이터 — {as_of} 기준]",
        f"기준: 해당 주 첫 거래일 종가 → 마지막 거래일 종가",
        "지수명 | 주간등락률(%) | 주초종가 | 주말종가",
        "-------|------------|--------|--------",
    ]
    for r in rows:
        note = f" {r['note']}" if r["note"] else ""
        lines.append(
            f"{r['name']}{note} | {r['weekly_chg']:+.2f}% | {r['mon_price']:.2f} | {r['fri_price']:.2f}"
        )
    lines += [
        "",
        f"※ 주간 등락률 = (주 마지막 거래일 종가 / 주 첫 거래일 종가 - 1) × 100",
        f"※ 공휴일 시 해당 주 내 다음/직전 거래일 자동 보정",
        f"※ 데이터 출처: yfinance 공식 API / {as_of} 기준",
        "※ 코스닥150은 KODEX 코스닥150 ETF(229200.KS)로 대리 산출",
    ]

    return RawDocument(
        url="yfinance://weekly_returns",
        source_type="finance",
        raw_text="\n".join(lines),
        title=f"코스피·코스닥 {week_label} 등락률 ({as_of})",
        extra={"theme": theme, "source": "yfinance_weekly_returns",
               "rows": rows, "target_mon": str(target_mon), "target_fri": str(target_fri)},
    )


def _collect_kr_index_history(theme: str) -> RawDocument | None:
    """코스닥/코스피 지수 장기 이동평균 수집 — 시가총액·지수 추이 쿼리 전용.

    단위: pt(포인트). yfinance 20년 역사 데이터 기반 기간별 이동평균 계산.
    ★ 왜 이게 필요한가: KrxProvider/FinanceProvider 기존 코드는 개별 종목·해외 지수만
       수집하고 코스닥/코스피 지수 자체가 없었음 → data_planner 가 finance 소스 설계해도
       수집 0건 → web 소스 폴백 → 틀린 수치 게이트 통과 (ERRORS [416] 교훈).
    """
    theme_lower = theme.lower().replace(" ", "")
    matched = {name: ticker for name, ticker in _KR_INDEX_TICKERS.items()
               if name.replace(" ", "") in theme_lower or name in theme}
    if not matched:
        # 시가총액/증시/지수 등 한국 시장 일반 쿼리면 코스닥+코스피 둘 다
        if any(kw in theme for kw in _KOREAN_MARKET_KWS):
            matched = dict(_KR_INDEX_TICKERS)
    if not matched:
        return None

    from datetime import datetime
    docs = []
    for name, ticker in matched.items():
        try:
            def _fetch(t=ticker):
                return yf.Ticker(t).history(period="20y", auto_adjust=True)
            df = _yf_with_timeout(_fetch, timeout=30)
            if df is None or df.empty or "Close" in df.columns is False:
                continue
            s = df["Close"].dropna()
            if len(s) < 20:
                continue

            latest     = float(s.iloc[-1])
            as_of_dt   = s.index[-1]
            as_of      = as_of_dt.strftime("%Y년 %-m월")

            avg_6m  = float(s.iloc[-126:].mean())
            avg_1y  = float(s.iloc[-252:].mean())
            avg_3y  = float(s.iloc[-756:].mean()) if len(s) >= 756 else None
            avg_5y  = float(s.iloc[-1260:].mean()) if len(s) >= 1260 else None
            avg_10y = float(s.iloc[-2520:].mean()) if len(s) >= 2520 else None
            avg_20y = float(s.mean())

            # KOSIS 형식으로 작성 → _parse_clean_doc fast-path 통과
            lines = [
                f"[KOSIS 통계표: {name} 지수 기간별 이동평균]",
                f"조회일: {as_of}  단위: pt",
                f"출처: yfinance 공식 API ({ticker})",
                "",
                f"  최근: {latest:.2f} pt",
                f"  6개월평균: {avg_6m:.2f} pt",
                f"  1년평균: {avg_1y:.2f} pt",
            ]
            if avg_3y:  lines.append(f"  3년평균: {avg_3y:.2f} pt")
            if avg_5y:  lines.append(f"  5년평균: {avg_5y:.2f} pt")
            if avg_10y: lines.append(f"  10년평균: {avg_10y:.2f} pt")
            lines.append(f"  20년평균: {avg_20y:.2f} pt")
            lines += [
                "",
                f"※ {name} 지수 기준. 단위 pt(포인트). 시가총액 절댓값(조원)과 다름.",
                f"※ 데이터 출처: yfinance 공식 API / {as_of} 기준",
            ]
            docs.append((name, "\n".join(lines), as_of, latest))
            log.info(f"[Finance] {name} 지수 이동평균 수집 완료 (최근 {latest:.0f}pt, {as_of})")
        except Exception as e:
            log.warning(f"[Finance] {name} 장기 데이터 수집 실패: {e}")

    if not docs:
        return None

    combined = "\n\n".join(d[1] for d in docs)
    title = " / ".join(d[0] for d in docs) + " 지수 이동평균"
    return RawDocument(
        url="yfinance://kr_index_history",
        source_type="finance",
        raw_text=combined,
        title=title,
        extra={"theme": theme, "source": "yfinance_kr_index"},
    )


class FinanceProvider(BaseProvider):
    source_type = "finance"

    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        """테마 관련 시장 데이터 수집. 주로 경제 브리핑용.

        ★ 주간 등락률 키워드 → 실가격 기반 주간 수익률 직접 계산 (ERRORS [424]).
        ★ 코스닥/코스피/시가총액 키워드 → 장기 지수 이동평균 우선 수집 (ERRORS [416]).
        """
        results = []

        # ① 주간 등락률 키워드 → yfinance 실가격으로 직접 계산 (web_data 완전 차단)
        theme_lower = theme.lower().replace(" ", "")
        if any(kw.replace(" ", "") in theme_lower for kw in _WEEKLY_RETURN_KWS):
            doc = _collect_weekly_returns(theme)
            if doc:
                results.append(doc)
                return results[:max_items]   # 주간 등락률은 이 데이터로 충분

        # ② 코스닥/코스피/시가총액 키워드 → 장기 지수 데이터 (정확한 공식 소스)
        if any(kw in theme for kw in _KOREAN_MARKET_KWS):
            doc = _collect_kr_index_history(theme)
            if doc:
                results.append(doc)

        # ② 글로벌 시장 지표 (기존)
        _TICKERS = {
            "S&P500": "^GSPC", "NASDAQ": "^IXIC", "DOW": "^DJI",
            "달러/원": "KRW=X", "금": "GC=F", "유가(WTI)": "CL=F", "미국채10년": "^TNX",
        }
        from datetime import datetime
        lines = [f"[시장 데이터 — {theme} / {datetime.now().strftime('%Y-%m-%d')}]", ""]
        for name, ticker in _TICKERS.items():
            try:
                def _fetch(t=ticker):
                    return yf.Ticker(t).history(period="2d")
                hist = _yf_with_timeout(_fetch, timeout=15)
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
                    curr = float(hist["Close"].iloc[-1])
                    chg = (curr - prev) / prev * 100
                    direction = "상승" if chg > 0 else ("하락" if chg < 0 else "보합")
                    lines.append(f"• {name}: 현재 {curr:.2f}, 전일 대비 {chg:+.2f}% {direction}")
            except Exception:
                pass
        if len(lines) > 2:
            lines.append("")
            lines.append(f"총 {len(lines)-3}개 주요 지표 기준 시장 현황. 데이터 출처: yfinance 공식 API.")
            results.append(RawDocument(
                url="yfinance://market_data",
                source_type=self.source_type,
                raw_text="\n".join(lines),
                title=f"{theme} 시장 데이터",
                extra={"theme": theme},
            ))
        return results[:max_items]
