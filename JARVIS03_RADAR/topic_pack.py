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

# 경제 섹터 집합 — JARVIS02_WRITER/trend_economic_writer._ECON_SECTORS 와 동치 유지.
# (03→02 import 는 순환 금지라 로컬 보유. 섹터 추가 시 양쪽 동시 갱신.)
_ECON_SECTORS = {
    '경제·경기', '금융·투자', '에너지·환경', 'IT·테크',
    '금융·은행', '주식·투자', '부동산', '산업·기업',
    '기술·IT', '에너지·자원', '무역·통상', '정책·규제', '글로벌·해외',
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


def _candidates(trends: dict, max_candidates: int = 8) -> list[dict]:
    """경제 섹터 후보 — 사용이력 제외 + 점수 내림차순 + 키워드 dedup."""
    used = _used_keywords()
    pool: list[dict] = []
    seen: set[str] = set()
    items = list(trends.get("recommendations") or []) + list(trends.get("scored_keywords") or [])
    for it in items:
        kw = (it.get("keyword") or "").strip()
        if not kw or kw.lower() in used or kw.lower() in seen:
            continue
        if it.get("sector", "") not in _ECON_SECTORS:
            continue
        seen.add(kw.lower())
        pool.append(it)
    pool.sort(key=lambda x: x.get("opportunity_score", x.get("score", 0)), reverse=True)
    return pool[:max_candidates]


def _profile_batch(cands: list[dict]) -> list[dict]:
    """LLM 배치 1회 — 후보별 프로필+적합성. 실패 시 [] (호출자가 팩 생성 포기 → 02 폴백)."""
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
        "각 항목: {\"keyword\": 원문 그대로, \"fit\": true|false, \"sector\": 교정 섹터,\n"
        "  \"summary\": 키워드가 무엇인지 한 문장 (동음이의 구분 명확히),\n"
        "  \"related_terms\": 관련어 5개 배열, \"entity_type\": 기업|산업|정책|지표|사건|제품|기타}\n\n"
        "fit 판정: 한국 *경제·금융·산업·투자* 독자용 블로그 주제로 적합하면 true.\n"
        "동식물·자연물·인물·연예·스포츠 등 경제 무관 대상은 false\n"
        "(예: '은행나무'는 나무(활엽수·산림·은행열매)이므로 false — summary 에 그 실체를 쓸 것.\n"
        " '기준금리'·'반도체 수출'은 true).\n\n"
        f"[키워드 목록]\n{lines}",
        max_tokens=2000,
        _essential=True,
    )
    if not (raw or "").strip():
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
    if max_candidates is None:
        max_candidates = publish_slots + 2
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
