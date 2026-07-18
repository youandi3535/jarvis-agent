"""JARVIS03_RADAR/topic_pack.py — 주제 패키지 파이프라인 (★ 사용자 박제 2026-07-03).

topic_pack = 키워드 + 프로필(한줄요약·관련어·엔티티유형) *만*.
JARVIS09 수집은 파이프라인(trend_economic_writer)이 팩 소비 시점에 직접 수행.

흐름 (경제 브리핑 파이프라인 시작 시 즉석 생성 + 당일 캐시 재사용):
  ① 경제 섹터 후보 추출 — recommendations+scored_keywords, 최근 7일 사용이력 제외, 점수 정렬
  ② LLM 배치 1회 — 후보별 {경제 주제 적합성, 프로필(한줄요약·관련어·엔티티유형), 교정 섹터}
     → 프로필 생성 자체가 오분류 트립와이어 ('은행나무' 프로필 = 활엽수·산림 → 부적합 즉시 검출)
  ③ 적합 상위 publish_slots개 → data/topic_pack_YYYY-MM-DD.json 박제
  ④ 자비스02가 pick_candidate() 로 소비 → JARVIS09 직접 수집 → 대본 생성

팩을 만들 수 없으면(트렌드 없음·적합 후보 0·LLM 미가용) 발행은 명확한 오류로 차단.
import 방향: 02→03(소비) 단방향. 03은 02를 import 하지 않는다 (순환 금지).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("jarvis")

_RADAR_DIR = Path(__file__).resolve().parent
_DATA_DIR = _RADAR_DIR / "data"
_USED_KW_FILE = _DATA_DIR / "used_economic_keywords.json"

# 경제 브리핑 대상 섹터 (★ 사용자 박제 2026-07-18) — analyzer 실제 분류 라벨과 일치.
# JARVIS02_WRITER/trend_economic_writer._ECON_SECTORS 와 동치 유지(03→02 import 순환 금지라 로컬).
# 실제 적합성 판정은 fit LLM(동적)이 하고, 이 집합은 '대상 섹터 스코프' + 섹터명 단독 키워드 거부용.
_ECON_SECTORS = {
    '사회·이슈', '금융·투자', '경제·경기', 'IT·테크', '에너지·환경',
}

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw):
        pass
# ─────────────────────────────────────────────────────


def _pack_path(day: str | None = None) -> Path:
    d = day or date.today().isoformat()
    return _DATA_DIR / f"topic_pack_{d}.json"


def _used_keywords(days: int = 7) -> set[str]:
    """최근 N일 발행 사용 키워드 (JARVIS02 _mark_keyword_used 가 적재하는 파일)."""
    if not _USED_KW_FILE.exists():
        return set()
    try:
        data = json.loads(_USED_KW_FILE.read_text(encoding="utf-8"))
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return {e["keyword"].strip().lower() for e in data if e.get("date", "") >= cutoff}
    except Exception:
        return set()


# ★ 섹터 수준 단독 단어 — 너무 넓어서 수집·글 작성 둘 다 불가 (topic_pack 진입 자체 차단)
_BLOCKED_GENERIC_KW: frozenset[str] = frozenset({
    "경제", "금융", "무역", "부동산", "고용", "소비", "경기", "증시",
    "주식", "투자", "산업", "기업", "환율", "금리", "물가", "수출",
    "수입", "통상", "정책", "규제", "시장", "산업화", "글로벌",
})


def _is_too_generic(kw: str) -> bool:
    """섹터 수준 단독 단어 또는 2자 이하 단어 → topic 불가."""
    kw = kw.strip()
    if len(kw) <= 2:
        return True
    if kw in _BLOCKED_GENERIC_KW:
        return True
    # 섹터 집합과 완전 일치 (e.g. "경제·경기" → 단독 "경제"는 이미 위에서 차단)
    if kw in _ECON_SECTORS:
        return True
    return False


def _candidates(trends: dict, max_candidates: int = 8) -> list[dict]:
    """혼합 트렌딩(combined_keywords) 기반 후보 — 사용이력 제외 + opportunity_score 내림차순."""
    used = _used_keywords()
    # scored_keywords 인덱스 (keyword → 점수·섹터 조인용)
    scored_idx: dict[str, dict] = {
        (it.get("keyword") or "").lower(): it
        for it in (trends.get("scored_keywords") or [])
    }
    pool: list[dict] = []
    seen: set[str] = set()
    # ★ 소스: 혼합 트렌딩 풀 (Google+Naver 교차 순위)
    combined = list(trends.get("combined_keywords") or trends.get("combined_top50") or [])
    for it in combined:
        kw = (it.get("keyword") or "").strip()
        if not kw or kw.lower() in used or kw.lower() in seen:
            continue
        # ★ 섹터 이름 단독·너무 짧은 키워드 사전 차단 (수집·글쓰기 불가)
        if _is_too_generic(kw):
            log.debug(f"[topic_pack] '{kw}' 너무 넓은 키워드 — 후보 제외")
            continue
        seen.add(kw.lower())
        # scored_keywords에서 점수·섹터 조인
        scored = scored_idx.get(kw.lower(), {})
        pool.append({
            **it,
            "sector":            scored.get("sector", "기타"),
            "opportunity_score": scored.get("opportunity_score", it.get("score", 0)),
            "score":             scored.get("score", it.get("score", 0)),
            "velocity":          scored.get("velocity", "—"),
            "competition":       scored.get("competition", 50.0),
        })
    pool.sort(key=lambda x: x.get("opportunity_score", x.get("score", 0)), reverse=True)
    return pool[:max_candidates]


def _profile_batch(cands: list[dict]) -> list[dict]:
    """LLM 배치 1회 — 후보별 프로필+적합성.
    LLM 미가용(빈 응답) 시 섹터 기반 기본 프로필로 폴백 — 발행 차단 방지."""
    if not cands:
        return []
    from shared.llm import invoke_text
    lines = "\n".join(
        f"- {c.get('keyword')} (분류된 섹터: {c.get('sector')})" for c in cands
    )
    raw = invoke_text(
        "analyzer",
        # (_essential — 프로필은 키워드 단독 전송 금지 규정의 심장: 회로 차단 중에도 1회 실시도)
        "다음 트렌드 키워드 각각에 대해 JSON 배열로만 답해라 (다른 말 금지).\n"
        "각 항목: {\"keyword\": 원문 그대로, \"fit\": true 또는 false, \"sector\": 교정 섹터,\n"
        "  \"summary\": 키워드가 무엇인지 한 문장 (동음이의 구분 명확히),\n"
        "  \"related_terms\": 관련어 5개 배열, \"entity_type\": 기업 또는 산업 또는 정책 또는 지표 또는 사건 또는 제품 또는 기타}\n\n"
        "fit 판정: 경제 브리핑(오늘의 경제·금융 상식·배경 설명) 주제로 적합하면 true.\n"
        "대상 섹터 — 사회·이슈 / 금융·투자 / 경제·경기 / IT·테크 / 에너지·환경 — 의 주제 중\n"
        "*경제·금융적 배경이나 파급이 있는 것*은 true (정책·제도·사건의 경제 영향, 금융·투자·산업·\n"
        "기술·에너지 이슈 등). 순수 비경제(동식물·자연물·인물·연예·스포츠·가십)만 false.\n"
        "(예: 은행나무는 나무이므로 false. 기준금리·반도체수출·전세사기·탄소배출권·핀테크는 true).\n\n"
        f"[키워드 목록]\n{lines}",
        max_tokens=2000,
        _essential=True,
    )
    if not (raw or "").strip():
        # ★ fail-closed (전수감사 FIX[4]): LLM 빈 응답 = *일시적 인프라 실패*(스로틀/회로차단).
        #   종전엔 모든 후보를 fit=True + related_terms=[] + 템플릿 summary 로 위조해 반환 →
        #   '은행나무'·연예인 등 비경제 키워드가 *프로필 없이* fit 필터를 통과, '키워드 단독
        #   전송 금지' 안전규정(ERRORS[290])을 인프라 실패가 뚫었다. docstring('LLM 미가용 시
        #   발행 차단')과도 정반대. → 빈 리스트 반환 → build_topic_pack 이 None(발행 차단) →
        #   경제 파이프라인이 defer/스킵(다음 회차 재시도). 거짓 프로필 발행 < 발행 안 함.
        log.warning("[topic_pack] 프로필 LLM 빈 응답(인프라) → fail-closed(팩 None, 다음 회차 재시도)")
        return []
    try:
        import re
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        parsed = json.loads(m.group(0) if m else raw)
        return [p for p in parsed if isinstance(p, dict) and p.get("keyword")]
    except Exception as e:
        log.warning(f"[topic_pack] 프로필 배치 파싱 실패: {e}")
        return []




def build_topic_pack(trends: dict | None = None, publish_slots: int = 2,
                     max_candidates: int | None = None) -> dict | None:
    """주제 패키지 생성 + 박제. topic_pack = 키워드 + 프로필만 (수집은 파이프라인에서). 실패 시 None.

    ★ 발행 슬롯만큼만 선정 (사용자 박제 2026-07-06): 네이버·티스토리 각 1개 = 2개만 쓴다.
    쓰지도 않을 주제를 프로파일링·박제하는 건 낭비 → 후보는 publish_slots + 소폭 버퍼(2)만
    LLM 프로파일링(부적합 판정으로 걸러질 것 대비), 팩에는 적합 상위 publish_slots개만 박제.
    """
    try:
        from shared.pipeline_activity import mark_active
        mark_active("e13")  # J04→J03 스케줄 트리거
    except Exception:
        pass
    if max_candidates is None:
        max_candidates = publish_slots + 8  # 혼합 30개 중 경제 키워드 충분히 확보
    if trends is None:
        try:
            from JARVIS03_RADAR.radar_main import load as _load
            trends = _load(date.today().isoformat())
        except Exception:
            trends = None
        if not trends:
            p = _DATA_DIR / f"trends_{date.today().isoformat()}.json"
            if p.exists():
                try:
                    trends = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    trends = None
    if not trends or not (trends.get("scored_keywords") or trends.get("recommendations")):
        # ★ 당일 캐시 부재 — 즉석 실제 수집 (사용자 박제 2026-07-10: 기존엔 캐시 재조회만
        #   하고 "즉석 실행"이라 주장했으나 실제 수집을 트리거하지 않아 아침 06시 잡이
        #   지연/유실되면 자가 치유가 불가능했음 — collect_today() 로 진짜 수집 수행)
        log.info("[topic_pack] 당일 트렌드 캐시 없음 — 즉석 수집 실행")
        try:
            from JARVIS03_RADAR.radar_main import collect_today as _collect, save as _save
            trends = _collect()
            _save(trends)
            log.info("[topic_pack] 즉석 트렌드 수집 완료")
        except Exception as e:
            log.warning(f"[topic_pack] 즉석 트렌드 수집 실패: {e}")
            trends = None
    if not trends or not (trends.get("scored_keywords") or trends.get("recommendations")):
        log.info("[topic_pack] 트렌드 데이터 없음 — 팩 생성 스킵")
        return None

    cands = _candidates(trends, max_candidates=max_candidates)
    if not cands:
        log.info("[topic_pack] 경제 섹터 후보 0개 — 팩 생성 스킵")
        return None

    profiles = _profile_batch(cands)
    if not profiles:
        log.info("[topic_pack] 프로필 LLM 미가용 — 팩 생성 실패 (발행은 차단됨)")
        return None
    prof_map = {p["keyword"].strip(): p for p in profiles}

    selected: list[dict] = []
    for c in cands:
        kw = (c.get("keyword") or "").strip()
        p = prof_map.get(kw)
        if not p:
            continue
        if not p.get("fit"):
            log.info(f"[topic_pack] '{kw}' 부적합 판정 ({(p.get('summary') or '')[:40]}) — 제외")
            continue
        selected.append({
            "keyword": kw,
            "sector": p.get("sector") or c.get("sector", ""),
            "opportunity_score": c.get("opportunity_score", c.get("score", 0)),
            "reason": c.get("reason", ""),
            "profile": {
                "summary": p.get("summary", ""),
                "related_terms": p.get("related_terms") or [],
                "entity_type": p.get("entity_type", ""),
            },
        })
    if not selected:
        log.info("[topic_pack] 적합 후보 0개 — 팩 생성 실패 (부적합 키워드 강행 금지)")
        return None

    final = selected[:publish_slots]
    if not final:
        log.warning("[topic_pack] 적합 후보 0개 — 팩 생성 실패")
        return None

    # ★ 동의어 선행 확장 (방향 2 — 위상 분리): topic_pack 생성 시점(LLM 부하 낮음)에
    #   chart_data 동의어를 미리 확장·캐시. collect_chart_data 진입 시 캐시 히트 → LLM 0회.
    #   실패해도 chart_data 가 런타임에 재시도하므로 예외 무시.
    try:
        from JARVIS09_COLLECTOR.chart_data import warm_synonyms as _warm_syns
        _syn_map = _warm_syns([c["keyword"] for c in final])
        for c in final:
            c["synonyms"] = _syn_map.get(c["keyword"]) or []
        log.info(f"[topic_pack] 동의어 선행 확장: { {k: v for k, v in _syn_map.items() if v} }")
    except Exception as _e:
        log.warning(f"[topic_pack] 동의어 선행 확장 실패 (무시): {_e}")

    # ★ 데이터 소싱 *설계(plan)* 선계산 (사용자 박제 2026-07-18) — 동의어와 동형 위상분리.
    #   저부하 창(topic_pack)에 plan_data_sources 를 돌려 각 후보에 data_plan 박제 → 발행창
    #   (스로틀 포화)에서 네이버·티스토리가 이 plan 을 공유해 planner LLM 을 아예 안 부른다.
    #   이게 '매번 제네릭 폴백 → 같은 타입 데이터' 문제의 근본 완화. 실패해도 런타임 재설계가 받침.
    try:
        from JARVIS09_COLLECTOR.chart_data import warm_plan as _warm_plan
        _plan_map = _warm_plan([
            {"keyword": c["keyword"], "sector": c.get("sector", ""),
             "profile": c.get("profile") or {}, "synonyms": c.get("synonyms")}
            for c in final
        ])
        for c in final:
            c["data_plan"] = _plan_map.get(c["keyword"])
        _n_ok = sum(1 for c in final if (c.get("data_plan") or {}).get("series"))
        log.info(f"[topic_pack] 데이터 설계 선계산: {_n_ok}/{len(final)}개 plan 박제")
    except Exception as _e:
        log.warning(f"[topic_pack] 데이터 설계 선계산 실패 (무시): {_e}")

    pack = {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "candidates": final,
    }
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _pack_path().write_text(
            json.dumps(pack, ensure_ascii=False, default=str), encoding="utf-8")
        log.info(f"[topic_pack] 박제 완료: {len(final)}개 후보 → {_pack_path().name}")
    except Exception as e:
        log.warning(f"[topic_pack] 박제 실패: {e}")
        return None
    return pack


def load_topic_pack(day: str | None = None) -> dict | None:
    """당일 주제 패키지 로드 (없으면 None)."""
    p = _pack_path(day)
    if not p.exists():
        return None
    try:
        pack = json.loads(p.read_text(encoding="utf-8"))
        if pack.get("date") != (day or date.today().isoformat()):
            return None
        return pack
    except Exception:
        return None


def pick_candidate(exclude_keyword: str = "") -> dict | None:
    """자비스02 소비용 — 당일 팩에서 미사용·미중복 첫 후보 반환.

    반환 dict: keyword/sector/profile.
    """
    pack = load_topic_pack()
    if not pack:
        return None
    used = _used_keywords()
    ex = (exclude_keyword or "").strip().lower()
    for cand in pack.get("candidates") or []:
        kw = (cand.get("keyword") or "").strip().lower()
        if not kw or kw in used:
            continue
        if ex and (kw == ex or kw in ex or ex in kw):
            continue
        try:
            from shared.pipeline_activity import mark_active
            mark_active("e5")  # J03→J02 topic_pack 전달 활성화
        except Exception:
            pass
        return cand
    return None


def keyword_profile(keyword: str, sector: str = "") -> dict:
    """★ 키워드 단독 전송 금지 규정용 공용 헬퍼 (사용자 박제 2026-07-03 — 강제).

    "자비스03은 트렌드 키워드를 누군가에게 보낼 때 키워드만 딸랑 보내지 말고
    *항상* 그 키워드를 설명할 수 있는 다양한 기본 정보까지 보태서 보내야 해."
    ('배' = 과일? 선박? 인체? — 프로필 없이는 하류가 판별 불가)

    반환: {"summary", "related_terms", "entity_type"} — LLM 미가용 시 빈 필드
    (호출자는 빈 프로필이면 키워드 전달을 보수적으로 취급할 것).
    """
    prof_list = _profile_batch([{"keyword": keyword, "sector": sector}])
    p = prof_list[0] if prof_list else {}
    return {
        "summary": p.get("summary", ""),
        "related_terms": p.get("related_terms") or [],
        "entity_type": p.get("entity_type", ""),
    }


def build_for_keyword(keyword: str, sector: str = "", reason: str = "") -> dict:
    """강제 지정 주제(JARVIS_FORCE_*_TOPIC)용 — 프로필 생성만.

    사용자가 명시 지정한 주제이므로 fit 판정은 적용하지 않는다.
    수집은 파이프라인에서 직접 수행.
    """
    prof_list = _profile_batch([{"keyword": keyword, "sector": sector}])
    prof = prof_list[0] if prof_list else {}
    summary = prof.get("summary", "") or reason or keyword
    cand = {
        "keyword": keyword,
        "sector": prof.get("sector") or sector,
        "reason": reason,
        "profile": {
            "summary": summary,
            "related_terms": prof.get("related_terms") or [],
            "entity_type": prof.get("entity_type", ""),
        },
    }
    return cand


__all__ = ["build_topic_pack", "load_topic_pack", "pick_candidate",
           "build_for_keyword", "keyword_profile"]
