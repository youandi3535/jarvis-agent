"""수집 측정 도구 — 주제 하나로 '어디서 얼마나' 수집되는지 수치화.

사용: .venv/bin/python tools/measure_collection.py "주제"
출력: 마지막 줄에 ===JSON=== 접두 구조화 결과 (출처별 문서 수·차트 데이터셋·종목·fact).
읽기 전용 수집만 — 외부 발행·전송 없음.
"""
import sys
import json
import io
import contextlib
from collections import Counter

sys.path.insert(0, "/Users/kimhyojung/portfolio/jarvis-agent")


def _provider_of_doc(d) -> str:
    """RawDocument → 세부 provider 추정 (source_type + extra + title 힌트)."""
    st = (getattr(d, "source_type", "") or "").lower()
    extra = getattr(d, "extra", {}) or {}
    prov = str(extra.get("provider") or extra.get("source") or "").lower()
    if prov:
        return prov
    title = (getattr(d, "title", "") or "")
    url = (getattr(d, "url", "") or "").lower()
    # finance API 문서는 title/url 로 provider 구분
    hints = {
        "kosis": "KOSIS(통계청)", "ecos": "ECOS(한국은행)", "bok": "ECOS(한국은행)",
        "dart": "DART(공시)", "kofia": "KOFIA(금투협)", "krx": "KRX(거래소)",
        "customs": "관세청", "unipass": "관세청", "moel": "고용노동부",
        "employ": "고용노동부", "molit": "국토부", "fss": "금감원",
        "naver": "네이버뉴스", "google": "구글뉴스", "yfinance": "yfinance",
    }
    for k, v in hints.items():
        if k in url or k in title.lower():
            return v
    return st or "web"


def measure(topic: str) -> dict:
    out = {"topic": topic, "docs_by_source": {}, "docs_total": 0,
           "doc_avg_chars": 0, "facts": 0, "fact_stats": 0,
           "charts_by_provider": {}, "charts_total": 0, "charts_timeseries": 0,
           "chart_points_total": 0, "stocks": 0, "errors": []}

    # ── 0) 주제 프로필 (실제 발행 경로와 동일 — 종목·차트 매칭에 사용) ──
    profile, related = {}, []
    try:
        from JARVIS03_RADAR.topic_pack import keyword_profile
        profile = keyword_profile(topic) or {}
        related = list(profile.get("related_terms") or [])
        out["profile"] = {"entity_type": profile.get("entity_type", ""),
                          "related_terms": related}
    except Exception as e:
        out["errors"].append(f"keyword_profile: {type(e).__name__}: {str(e)[:60]}")

    # ── 1) 리서치 텍스트 수집 (뉴스·논문·금융·웹) ──
    try:
        from JARVIS09_COLLECTOR import collect_research
        from JARVIS09_COLLECTOR.evidence_pack import build_evidence_pack, facts_to_datasets
        res = collect_research(topic)
        docs = res.get("docs", []) if isinstance(res, dict) else (res or [])
        by_src = Counter()
        total_chars = 0
        for d in docs:
            by_src[_provider_of_doc(d)] += 1
            total_chars += len(getattr(d, "raw_text", "") or getattr(d, "cleaned_text", "") or "")
        out["docs_by_source"] = dict(by_src)
        out["docs_total"] = len(docs)
        out["doc_avg_chars"] = round(total_chars / len(docs)) if docs else 0
        # fact 추출
        try:
            pack = build_evidence_pack(topic, res.get("plan", {}) if isinstance(res, dict) else {}, docs)
            facts = pack.get("facts", []) if isinstance(pack, dict) else []
            out["facts"] = len(facts)
            out["fact_stats"] = sum(1 for f in facts if f.get("kind") == "stat")
            fds = facts_to_datasets(pack)
            out["fact_datasets"] = [{"title": x.get("title", ""), "points": len(x.get("data", []))} for x in fds]
        except Exception as e:
            out["errors"].append(f"evidence_pack: {type(e).__name__}: {str(e)[:80]}")
    except Exception as e:
        out["errors"].append(f"collect_research: {type(e).__name__}: {str(e)[:80]}")

    # ── 2) 차트 데이터 수집 (provider별) ──
    try:
        from JARVIS09_COLLECTOR import collect_chart_data
        try:
            cd = collect_chart_data(theme=topic, related_terms=related, profile=profile)
        except TypeError:
            cd = collect_chart_data(theme=topic)  # 시그니처 하위호환
        ds = cd.get("datasets", []) if isinstance(cd, dict) else (cd or [])
        by_prov = Counter()
        pts = 0
        ts = 0
        chart_detail = []
        for d in ds:
            prov = (d.get("source") or {}).get("provider", "?")
            by_prov[prov] += 1
            npts = len(d.get("data", []))
            pts += npts
            if d.get("viz_hint") == "line_chart":
                ts += 1
            chart_detail.append({"title": d.get("title", "")[:30],
                                 "viz": d.get("viz_hint", ""), "points": npts, "provider": prov})
        out["charts_by_provider"] = dict(by_prov)
        out["charts_total"] = len(ds)
        out["charts_timeseries"] = ts
        out["chart_points_total"] = pts
        out["chart_detail"] = chart_detail
    except Exception as e:
        out["errors"].append(f"collect_chart_data: {type(e).__name__}: {str(e)[:80]}")

    # ── 3) 테마 종목 수집 ──
    try:
        from JARVIS09_COLLECTOR import collect_stocks_data
        try:
            sd = collect_stocks_data(topic, related_terms=related, profile=profile)
        except TypeError:
            sd = collect_stocks_data(topic)  # 시그니처 하위호환
        stocks = sd.get("stocks", []) if isinstance(sd, dict) else []
        out["stocks"] = len(stocks)
    except Exception as e:
        out["errors"].append(f"collect_stocks_data: {type(e).__name__}: {str(e)[:80]}")

    return out


if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "라면"
    # 수집 로그(print) 는 stderr 로 흘려보내고 stdout 엔 JSON 한 줄만
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = measure(topic)
    sys.stderr.write(buf.getvalue())
    print("===JSON===" + json.dumps(result, ensure_ascii=False))
