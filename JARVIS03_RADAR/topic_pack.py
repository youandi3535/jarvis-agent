"""JARVIS03_RADAR/topic_pack.py — 주제 패키지 파이프라인 (★ 사용자 박제 2026-07-03).

"자비스03이 자비스02와 자비스09에게 *동시에* 트렌드 정보를 제공한다. 폴백 없음."
— 주제 키워드+프로필의 *유일한* 공급 경로. 자비스03이 팩 생성 시점에 JARVIS09 를
직접 호출해 수집시키고(→09), 자비스02는 같은 팩에서 주제·프로필을 받아 제목·대본을
쓴다(→02). 자비스02의 자체 주제 선정(select_*_topic)·자체 JARVIS09 수집 호출은 폐지
(키워드 문자열 중계 중 프로필 유실·드리프트 — ERRORS [290] '은행나무' GIGO — 구조 차단).

흐름 (트렌드 수집 잡 말미에 자동 실행 + 팩 부재 시 즉석 호출):
  ① 경제 섹터 후보 추출 — recommendations+scored_keywords, 최근 7일 사용이력 제외, 점수 정렬
  ② LLM 배치 1회 — 후보별 {경제 주제 적합성, 프로필(한줄요약·관련어·엔티티유형), 교정 섹터}
     → 프로필 생성 자체가 오분류 트립와이어 ('은행나무' 프로필 = 활엽수·산림 → 부적합 즉시 검출)
  ③ 적합 상위 publish_slots개 → ★ JARVIS09 *직접* 선수집 (자비스02 경유 0):
       collect_research(keyword, sector, angle=프로필요약)      → 근거팩 + 정제 문서
       collect_chart_data(keyword, sector, description=프로필요약) → 인포그래픽 실데이터
  ④ data/topic_pack_YYYY-MM-DD.json 박제 — 자비스02는 발행 시 *소비만* (재수집 0)

팩을 만들 수 없으면(트렌드 없음·적합 후보 0·LLM 미가용) 발행은 명확한 오류로 차단 —
부적합 키워드 강행보다 발행 지연이 우선 (사용자 박제: 데이터 진실성).
import 방향: 02→03(소비) 단방향. 03은 02를 import 하지 않는다 (순환 금지).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
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


def _precollect(keyword: str, sector: str, summary: str) -> dict:
    """★ JARVIS09 직접 선수집 — 프로필 요약을 angle/description 으로 앵커.

    ★ 넉넉한 수집 (사용자 박제 2026-07-03 — ADR 013): "설계한 후, 제한을 두지 말고
    최대한 많은 진실성 있는 데이터를 전부" — 발행 창 밖(06:00 잡)이라 시간 여유 있음.
    """
    _max_ds = int(os.getenv("TOPIC_PACK_MAX_DATASETS", "64") or "64")
    _rounds = int(os.getenv("TOPIC_PACK_RESEARCH_ROUNDS", "3") or "3")
    out: dict = {"datasets": [], "docs": [], "evidence_path": ""}
    try:
        from JARVIS09_COLLECTOR import collect_chart_data
        cd = collect_chart_data(keyword, sector=sector,
                                description=summary or keyword, max_datasets=_max_ds) or {}
        out["datasets"] = cd.get("datasets") or []
    except Exception as e:
        log.warning(f"[topic_pack] 차트 데이터 선수집 실패({keyword}): {e}")
        _g_report("radar", e, module=__name__, func_name="_precollect")
    try:
        from JARVIS09_COLLECTOR import collect_research
        rs = collect_research(keyword, sector=sector, angle=summary or "",
                              max_rounds=_rounds) or {}
        out["docs"] = [asdict(d) for d in (rs.get("docs") or [])]
        out["evidence_path"] = rs.get("evidence_path") or ""
        # ★ 품질 플래그 (ERRORS [300]): 폴백 설계·근거 부족을 팩에 가시화
        out["plan_fallback"] = bool(rs.get("plan_fallback"))
        out["coverage_ratio"] = rs.get("coverage_ratio", 0.0)
        out["insufficient"] = bool(rs.get("insufficient"))
        # ★ 수치 fact → 데이터셋 승격 (사용자 박제 2026-07-03 — ERRORS [302]):
        #   텍스트 근거 속 수치(값·단위·출처 박제)를 인포그래픽 공급원으로 합류.
        try:
            from JARVIS09_COLLECTOR.evidence_pack import facts_to_datasets
            _fact_ds = facts_to_datasets(rs.get("evidence_pack") or {})
            if _fact_ds:
                _seen_titles = {d.get("title") for d in out["datasets"]}
                _new = [d for d in _fact_ds if d.get("title") not in _seen_titles]
                out["datasets"] = list(out["datasets"]) + _new
                log.info(f"[topic_pack] 수치 fact → 데이터셋 승격 {len(_new)}개 "
                         f"(공식수집 {len(_seen_titles)} + fact 승격 = 총 {len(out['datasets'])})")
        except Exception as _fe:
            log.warning(f"[topic_pack] fact 데이터셋 승격 스킵: {_fe}")
    except Exception as e:
        log.warning(f"[topic_pack] 리서치 선수집 실패({keyword}): {e}")
        _g_report("radar", e, module=__name__, func_name="_precollect")
        out["insufficient"] = True   # 리서치 자체 실패 = 근거 없음
    return out


def build_topic_pack(trends: dict | None = None, publish_slots: int = 2,
                     max_candidates: int = 8) -> dict | None:
    """주제 패키지 생성 + JARVIS09 선수집 + 박제. 실패 시 None (02 는 현행 폴백)."""
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

    # ★ JARVIS09 직접 선수집 + 근거 부족 시 주제 교체 (ERRORS [300] — 사용자 승인 2026-07-03)
    #   적합 후보를 순서대로 선수집 → insufficient(커버리지 0·fact<3) 면 다음 후보로 교체.
    #   충분 후보가 슬롯을 못 채우면 근거 얇은 후보로 보충 (플래그 유지 — 02 게이트가 최종 방어).
    final: list[dict] = []
    thin_pool: list[dict] = []
    for cand in selected:
        if len(final) >= publish_slots:
            break
        summ = cand["profile"]["summary"]
        log.info(f"[topic_pack] JARVIS09 선수집: '{cand['keyword']}' — {summ[:50]}")
        cand.update(_precollect(cand["keyword"], cand["sector"], summ))
        if cand.get("insufficient"):
            log.warning(f"[topic_pack] '{cand['keyword']}' 근거 부족 "
                        f"(커버리지 {cand.get('coverage_ratio')}) → 다음 후보로 교체")
            thin_pool.append(cand)
            continue
        final.append(cand)
    if len(final) < publish_slots and thin_pool:
        _fill = thin_pool[:publish_slots - len(final)]
        log.warning(f"[topic_pack] 충분 근거 후보 부족 — 근거 얇은 후보 {len(_fill)}개로 보충")
        final += _fill
    if not final:
        log.warning("[topic_pack] 선수집 가능한 후보 0개 — 팩 생성 실패")
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

    # ★ 품질 저하 가시화 (조용한 강등 금지): 폴백 설계·근거 부족 시 텔레그램 1회 통보
    try:
        _issues = []
        for c in final:
            tags = []
            if c.get("plan_fallback"):
                tags.append("설계 폴백(템플릿)")
            if c.get("insufficient"):
                tags.append(f"근거 부족(커버리지 {c.get('coverage_ratio')})")
            if tags:
                _issues.append(f"· {c['keyword']}: {', '.join(tags)}")
        swapped = [c["keyword"] for c in thin_pool if c not in final]
        if swapped:
            _issues.append(f"· 주제 교체됨(근거 부족): {', '.join(swapped)}")
        if _issues:
            from shared.notify import send_tg
            send_tg("⚠️ [자비스03] 주제 패키지 품질 경고\n" + "\n".join(_issues))
    except Exception:
        pass
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

    반환 dict: keyword/sector/profile/datasets/docs(직렬화)/evidence_path.
    docs 복원은 restore_docs() 사용.
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
    """강제 지정 주제(JARVIS_FORCE_*_TOPIC)용 — 프로필 생성 + JARVIS09 선수집.

    강제 주제도 03→09 단일 구조 유지 (02 는 09 를 직접 호출하지 않음).
    사용자가 명시 지정한 주제이므로 fit 판정은 적용하지 않는다.
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
    cand.update(_precollect(keyword, cand["sector"], summary))
    return cand


def restore_docs(cand: dict) -> list:
    """직렬화된 docs → CollectionResult 객체 복원 (JARVIS06·prepublish 호환)."""
    try:
        from JARVIS09_COLLECTOR.models import CollectionResult
        out = []
        for d in cand.get("docs") or []:
            try:
                out.append(CollectionResult(**d))
            except Exception:
                continue
        return out
    except Exception:
        return []


__all__ = ["build_topic_pack", "load_topic_pack", "pick_candidate",
           "build_for_keyword", "keyword_profile", "restore_docs"]
