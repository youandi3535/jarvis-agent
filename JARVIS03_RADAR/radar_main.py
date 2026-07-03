#!/usr/bin/env python3
"""
JARVIS03 RADAR — 트렌드 수집 + shared 파이프라인 push

사용법:
  python radar_main.py              # 수집 + shared DB 저장 + WRITER 파이프라인 push
  python radar_main.py --date YYYY-MM-DD   # 특정 날짜 로컬 데이터 조회
  python radar_main.py --no-push    # 파이프라인 push 없이 수집만
"""
import sys, json, time, re
from pathlib import Path
from datetime import date, datetime

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR.parent))  # shared/ + JARVIS03_RADAR 패키지 접근

# 상대 import → 절대 import (subprocess 직접 실행 시 패키지 컨텍스트 없어서 깨짐 방지)
from JARVIS03_RADAR.collectors.google_collector import get_trending_searches
from JARVIS03_RADAR.analyzer import (
    score_keywords, enrich_with_opportunity,
    build_sector_summary, generate_recommendations, generate_content_angles,
)

# 글자수 정책은 length_manager 단일 진입점
from JARVIS02_WRITER import length_manager as _LM

# ── 의미없는 키워드 필터링 ──────────────────────────────────────
# 조사·접속사·문장 파편·단순 동사 어미 등 블랙리스트
_KW_BLACKLIST: set[str] = {
    "에요", "이에요", "예요", "이야", "한다", "된다", "있다", "없다",
    "한테", "에서", "으로", "에게", "부터", "까지", "라서", "이라",
    "단독", "속보", "오늘", "뉴스", "이슈", "최신", "화제", "긴급",
    "위해", "위한", "통해", "따라", "대한", "관한", "이후", "이전",
    "하지만", "그러나", "그리고", "하면서", "하지만", "이지만",
}
# 이 패턴에 해당하면 제거 (문장 파편 — 조사/어미로 끝나는 length_manager.RADAR_KOR_NOISE_MAX 이상 단어)
_FRAGMENT_RE = re.compile(
    r"(에서|에게|으로|부터|까지|라서|이라|한다|이다|하는|하면|하여|하고|이고|이며|이지|에도|에만|에만|에서|본부장에|공사에|시장에|회장에)$"
)

def _is_meaningful(kw: str) -> bool:
    """True면 유의미한 키워드. False면 필터링."""
    kw = kw.strip()
    if not kw or len(kw) < 2:
        return False
    # 블랙리스트
    if kw in _KW_BLACKLIST:
        return False
    # 문장 파편 패턴 (조사/어미로 끝나는 것)
    if _FRAGMENT_RE.search(kw):
        return False
    # 한글인데 짧으면 제거 (접속사 등) — length_manager.RADAR_KOR_NOISE_PATTERN 사용
    if re.fullmatch(_LM.RADAR_KOR_NOISE_PATTERN, kw):
        return False
    # 공백 포함 긴 문장 파편 (단어 3개 이상)
    if len(kw.split()) >= 4:
        return False
    return True

def _filter_keywords(trending: list[str]) -> list[str]:
    filtered = [kw for kw in trending if _is_meaningful(kw)]
    removed  = [kw for kw in trending if not _is_meaningful(kw)]
    if removed:
        print(f"[RADAR] 필터링 제거 {len(removed)}개: {removed[:5]}")
    return filtered


# ── 주식·경제 시드 키워드 (DataLab 실측 트렌드 수집용) ─────────────────
# 일반 트렌딩 검색어가 주식과 무관할 때에도 항상 주식 도메인 데이터를 확보한다.
_STOCK_SEEDS = [
    "반도체", "2차전지", "배터리", "전기차", "자율주행",
    "바이오", "제약", "비만치료제", "AI", "로봇",
    "조선", "방산", "원자력", "태양광", "수소",
    "은행", "금융", "코스피", "HBM", "엔비디아",
    "화장품", "엔터", "게임", "정유", "철강",
    "건설", "부동산", "통신", "핀테크", "우주",
]


def _collect_finance_headlines() -> list[str]:
    """네이버 뉴스 검색 API → 주식·경제 관련 핵심어 추출.
    API 키 없으면 Google Trends RSS 경제 카테고리로 폴백.
    """
    import os, requests
    from pathlib import Path as _P
    try:
        from dotenv import load_dotenv
        load_dotenv(_P(__file__).parent.parent / ".env")
    except Exception:
        pass

    client_id     = os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "")

    keywords: list[str] = []
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ko-KR"})

    if client_id and client_secret:
        # 주식·업종별 뉴스 쿼리 — 각 3개씩 추출
        _QUERIES = ["주식 급등", "증시 강세", "코스피 업종", "반도체 주가", "배터리 주가",
                    "바이오 임상", "방산 수주", "AI 반도체", "전기차 시장"]
        for q in _QUERIES[:6]:
            try:
                r = sess.get(
                    "https://openapi.naver.com/v1/search/news.json",
                    headers={"X-Naver-Client-Id": client_id,
                             "X-Naver-Client-Secret": client_secret},
                    params={"query": q, "display": 5, "sort": "date"},
                    timeout=8,
                )
                if r.status_code != 200:
                    continue
                for item in r.json().get("items", []):
                    title = re.sub(r"<[^>]+>", "", item.get("title", ""))
                    words = re.findall(_LM.RADAR_KW_PATTERN_KOR_UPPER, title)
                    for w in words[:3]:
                        if w not in keywords and _is_meaningful(w):
                            keywords.append(w)
                time.sleep(0.15)
            except Exception:
                pass
        print(f"[RADAR] 경제뉴스(Naver API) 키워드 {len(keywords)}개 추출")
    else:
        # 폴백: Google Trends RSS에서 경제 관련 필터링
        try:
            r = sess.get("https://trends.google.com/trending/rss?geo=KR", timeout=10)
            import xml.etree.ElementTree as ET
            root = ET.fromstring(r.text)
            for item in root.findall(".//item")[:30]:
                el = item.find("title")
                if el is None or not el.text:
                    continue
                keywords.append(el.text.strip())
        except Exception as e:
            print(f"[Finance] 폴백 오류: {e}")
            _g_report("radar", e, module=__name__)
        print(f"[RADAR] 경제뉴스(RSS 폴백) 키워드 {len(keywords)}개 추출")

    return keywords[:30]


def collect_today() -> dict:
    """오늘 트렌드 수집 → DataLab·경쟁강도·LLM 각도 포함 dict 반환."""
    print("[RADAR] Google Trends 수집 중...")
    raw_trending = get_trending_searches(limit=40)  # 여유있게 수집 후 필터링
    trending = _filter_keywords(raw_trending)[:30]
    print(f"[RADAR] 필터링 후 유효 키워드: {len(trending)}개")

    # 경제 뉴스 헤드라인 + 주식 시드 키워드 병합 (중복 제거)
    finance_kws = _collect_finance_headlines()
    extra = [kw for kw in (finance_kws + _STOCK_SEEDS) if kw not in trending]
    trending = list(dict.fromkeys(trending + extra))[:50]
    print(f"[RADAR] 경제 보완 후 총 키워드: {len(trending)}개")

    # ── 1. Naver DataLab: 30일 추세 곡선 ─────────────────────────
    datalab: dict = {}
    try:
        from JARVIS03_RADAR.collectors.naver_collector import get_batch_datalab, has_api_key
        if has_api_key():
            print("[RADAR] Naver DataLab 트렌드 수집 중...")
            datalab = get_batch_datalab(trending[:20], days=30)
            print(f"[RADAR] DataLab 수집: {len(datalab)}개 키워드")
    except Exception as e:
        print(f"[RADAR] DataLab 스킵: {e}")

    # ── 1-b. DataLab 없으면 Google interest_over_time fallback ───
    iot_used = False
    if not datalab:
        try:
            from JARVIS03_RADAR.collectors.google_collector import get_interest_over_time
            print("[RADAR] Google interest_over_time velocity fallback 수집 중...")
            datalab = get_interest_over_time(trending[:20], days=30)
            if datalab:
                iot_used = True
                print(f"[RADAR] IOT fallback 성공: {len(datalab)}개 키워드")
        except Exception as e:
            print(f"[RADAR] IOT fallback 스킵: {e}")

    # ── 2. 경쟁 강도 분석 (네이버 뉴스 검색량 기반) ───────────────
    competition: dict = {}
    try:
        from JARVIS03_RADAR.collectors.naver_collector import get_competition_score, has_api_key
        if has_api_key():
            print("[RADAR] 경쟁 강도 분석 중...")
            for kw in trending[:15]:
                competition[kw] = get_competition_score(kw)
                time.sleep(0.2)
            print(f"[RADAR] 경쟁 강도 분석 완료: {len(competition)}개")
    except Exception as e:
        print(f"[RADAR] 경쟁 분석 스킵: {e}")

    # ── 3. 자동완성 연관 키워드 (전체 trending, 인증 불필요) ────────
    autocomplete: dict = {}
    try:
        from JARVIS03_RADAR.collectors.naver_collector import get_autocomplete
        for kw in trending[:20]:  # 10→20으로 확대
            ac = get_autocomplete(kw)
            if ac:
                autocomplete[kw] = ac
            time.sleep(0.1)
    except Exception as e:
        print(f"[RADAR] 자동완성 스킵: {e}")

    # ── 4. 점수 계산 ───────────────────────────────────────────────
    scored     = score_keywords(trending, datalab=datalab, competition=competition)
    scored     = enrich_with_opportunity(scored)
    sector_sum = build_sector_summary(scored)
    recs       = generate_recommendations(sector_sum, n=10)  # 5→10으로 확대

    # ── 5. LLM 콘텐츠 각도 생성 — 추천 + 고점수 키워드 전체 ────────
    # recs에 없는 상위 scored 키워드도 각도 생성
    top_scored_kws = [k for k in scored[:15] if k["keyword"] not in {r["keyword"] for r in recs}]
    extra_recs = []
    for k in top_scored_kws:
        extra_recs.append({
            "theme": k["keyword"], "topic": k["keyword"],
            "keyword": k["keyword"], "sector": k["sector"],
            "score": k["score"], "opportunity_score": k.get("opportunity_score", k["score"]),
            "velocity": k.get("velocity","—"), "competition": k.get("competition", 50.0),
            "reason": f"점수 {k['score']} · {k.get('sector','기타')}",
            "angle": "", "hook": "", "color": "#4a5568",
        })

    all_for_llm = recs + extra_recs
    print(f"[RADAR] LLM 각도 생성 대상: {len(all_for_llm)}개 키워드")
    all_for_llm = generate_content_angles(all_for_llm, autocomplete=autocomplete)

    # recs / extra 분리
    recs_kws   = {r["keyword"] for r in recs}
    recs       = [r for r in all_for_llm if r["keyword"] in recs_kws]
    extra_recs = [r for r in all_for_llm if r["keyword"] not in recs_kws]

    # content_angles 통합 저장
    content_angles = {
        r["keyword"]: {"topic": r["topic"], "angle": r.get("angle",""), "hook": r.get("hook","")}
        for r in (recs + extra_recs)
    }

    # ── 6. 전일 대비 변화 계산 ──────────────────────────────────────
    trend_delta = _calc_trend_delta(trending)

    return {
        "date":            date.today().strftime("%Y-%m-%d"),
        "collected_at":    datetime.now().strftime("%H:%M:%S"),
        "google_trending": trending,
        "scored_keywords": scored,
        "sector_summary":  dict(sector_sum),
        "recommendations": recs,
        "extra_angles":    extra_recs,    # 추가 키워드 각도
        "content_angles":  content_angles,
        "autocomplete":    autocomplete,
        "datalab_used":    bool(datalab) and not iot_used,
        "iot_used":        iot_used,
        "trend_delta":     trend_delta,   # 전일 대비 신규/이탈/순위변화
    }


def _calc_trend_delta(today_kws: list[str]) -> dict:
    """전일 데이터와 비교해 신규 진입 / 이탈 / 순위 변화 계산."""
    dates = list_dates()
    # 오늘 날짜 제외하고 가장 최근 날짜
    today_str = date.today().strftime("%Y-%m-%d")
    prev_dates = [d for d in dates if d != today_str]
    if not prev_dates:
        return {}
    prev_data = load(prev_dates[0])
    if not prev_data:
        return {}

    prev_kws = prev_data.get("google_trending", [])
    prev_rank = {kw: i+1 for i, kw in enumerate(prev_kws)}
    today_rank = {kw: i+1 for i, kw in enumerate(today_kws)}

    new_entry  = [kw for kw in today_kws if kw not in prev_rank]
    dropped    = [kw for kw in prev_kws  if kw not in today_rank]
    risen      = sorted(
        [(kw, prev_rank[kw] - today_rank[kw]) for kw in today_kws if kw in prev_rank and prev_rank[kw] - today_rank[kw] >= 3],
        key=lambda x: -x[1]
    )[:5]
    fallen     = sorted(
        [(kw, today_rank[kw] - prev_rank[kw]) for kw in today_kws if kw in prev_rank and today_rank[kw] - prev_rank[kw] >= 3],
        key=lambda x: -x[1]
    )[:5]

    return {
        "prev_date": prev_dates[0],
        "new_entry": new_entry[:10],
        "dropped":   dropped[:10],
        "risen":     [{"keyword": kw, "delta": d} for kw, d in risen],
        "fallen":    [{"keyword": kw, "delta": d} for kw, d in fallen],
    }


def save(data: dict):
    path = DATA_DIR / f"trends_{data['date']}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[RADAR] 로컬 저장: {path.name}")


def push_to_shared(data: dict):
    """수집 결과를 shared DB에 저장하고 WRITER 파이프라인에 추천 주제 등록."""
    try:
        from shared.db import save_trends, push_pipeline
        from shared.bus import on_trend_detected, on_theme_queued
        from theme_matcher import match_themes
        from analyzer import classify_keyword

        save_trends(data["date"], data["scored_keywords"])
        print(f"[RADAR→shared] 트렌드 {len(data['scored_keywords'])}개 저장")

        # ── 트렌딩 키워드 → theme_list.txt 테마 매핑 ─────────────────────
        matched = match_themes(data["scored_keywords"], top_n=15)

        if matched:
            print(f"[RADAR→WRITER] 테마 매핑 결과 (상위 5개):")
            for m in matched[:5]:
                print(f"  {m['theme']} (점수:{m['score']:.0f}) ← {m['matched_by']}")
        else:
            print("[RADAR→WRITER] 매핑된 테마 없음 — 기존 recommendations 사용")

        # 매핑 결과가 있으면 사용, 없으면 기존 recommendations 폴백
        if matched:
            from shared.db import get_theme_performance_boost
            pipeline_items = []
            for m in matched:
                sector = classify_keyword(m["theme"])
                perf_boost = get_theme_performance_boost(m["theme"])
                final_score = round(m["score"] + perf_boost, 1)
                if perf_boost > 0:
                    print(f"  📈 성과 부스트: {m['theme']} +{perf_boost} → {final_score}")
                pipeline_items.append({
                    "theme":             m["theme"],
                    "sector":            sector,
                    "opportunity_score": final_score,
                })
        else:
            recs = data.get("recommendations", [])
            pipeline_items = [
                {
                    "theme":             r["theme"],
                    "sector":            r["sector"],
                    "opportunity_score": r.get("opportunity_score", r.get("score", 0)),
                }
                for r in recs
            ]

        # ── ★ 공식 테마 게이트 (사용자 박제 2026-07-03 — ERRORS [306]) ──────────
        # 테마주 글의 주제는 KRX/네이버 금융 *공식 테마* 에서만 선정 — 비공식 테마는
        # 큐잉 자체를 차단 (실행 시 stocks_data 게이트가 2차 방어).
        try:
            from JARVIS09_COLLECTOR.collect_theme import is_official_theme
            _before_cnt = len(pipeline_items)
            _dropped = [it["theme"] for it in pipeline_items if not is_official_theme(it["theme"])]
            pipeline_items = [it for it in pipeline_items if it["theme"] not in set(_dropped)]
            if _dropped:
                print(f"[RADAR→WRITER] ⛔ 공식 테마 게이트 — 비공식 {len(_dropped)}개 제외: "
                      f"{', '.join(_dropped[:5])}{' …' if len(_dropped) > 5 else ''}")
        except Exception as _ge:
            print(f"[RADAR→WRITER] 공식 테마 게이트 스킵(오류): {_ge}")

        push_pipeline(pipeline_items)
        print(f"[RADAR→WRITER] 파이프라인 {len(pipeline_items)}개 등록 (공식 테마만)")

        recs = data.get("recommendations", [])
        on_trend_detected(data["date"], data["google_trending"], recs)
        for item in pipeline_items:
            on_theme_queued(item["theme"], item["sector"], item["opportunity_score"])

    except Exception as e:
        print(f"[RADAR] shared push 오류 (무시하고 계속): {e}")
        _g_report("radar", e, module=__name__)


def load(target_date: str = None) -> dict:
    if target_date is None:
        target_date = date.today().strftime("%Y-%m-%d")
    path = DATA_DIR / f"trends_{target_date}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def list_dates() -> list[str]:
    return sorted(
        [p.stem.replace("trends_", "") for p in DATA_DIR.glob("trends_*.json")],
        reverse=True,
    )


if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    # 부모가 박은 JARVIS_PREFLIGHT_DONE=1 있으면 skip, 없으면 자체 검증.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    args = sys.argv[1:]

    if "--date" in args:
        idx = args.index("--date")
        d   = args[idx + 1] if idx + 1 < len(args) else None
        existing = load(d) if d else {}
        if existing:
            print(json.dumps(existing, ensure_ascii=False, indent=2))
        else:
            print(f"[RADAR] {d} 데이터 없음")
    else:
        data     = collect_today()
        save(data)
        no_push  = "--no-push" in args
        if not no_push:
            push_to_shared(data)

        print("\n[RADAR] 섹터별 TOP 3:")
        for sector, kws in data["sector_summary"].items():
            print(f"  {sector}: {', '.join(k['keyword'] for k in kws[:3])}")
        print("\n[RADAR] WRITER 파이프라인 추천:")
        for rec in data["recommendations"]:
            print(f"  [{rec['sector']}] {rec['theme']} (기회점수: {rec.get('opportunity_score', rec.get('score'))})")
