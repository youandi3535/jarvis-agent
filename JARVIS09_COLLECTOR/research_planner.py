"""JARVIS09_COLLECTOR/research_planner.py — 주제별 *리서치 설계* (텍스트 근거판 data_planner).

★ 사용자 박제 2026-07-02 (ADR 012): "항상 설계를 먼저 하고, 그 설계대로 수집한다."
  data_planner 가 *차트용 수치 series* 를 설계하듯, research_planner 는 *글 전체의 근거* 를
  설계한다 — 독자가 이 주제에서 진짜 알고 싶은 핵심 질문을 정하고, 질문마다
  어떤 종류의 근거(통계·논문·뉴스·사례)를 어느 출처에서 어떤 검색어로 확보할지 결정.

흐름:  주제 확정 → plan_research(theme) → ResearchPlan(질문 4~6개 + 질문별 출처·쿼리)
       → collector_engine.collect_research 가 설계대로 조준 수집
       → evidence_pack 이 커버리지를 측정 → 부족 질문만 2라운드 재수집

설계는 완전 동적 — 주제별 if-else 0. 출처 카탈로그는 data_planner._SOURCE_CATALOG
(단일 진실 소스) 재사용.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("jarvis.collector.research")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k):
        pass

from JARVIS09_COLLECTOR.data_planner import _SOURCE_CATALOG

_VALID_SOURCES = set(_SOURCE_CATALOG)
_EVIDENCE_KINDS = {"stat", "paper", "news", "case", "definition", "expert"}

_PLAN_SYSTEM = """당신은 데이터 저널리스트의 '리서치 설계자'다. 글 주제가 주어지면
독자가 이 주제에서 *진짜 알고 싶은 핵심 질문* 들을 정하고, 각 질문에 답할 근거를
어느 출처에서 어떤 검색어로 확보할지 설계한다. 절대 내용을 지어내지 않는다 —
설계만 한다(실제 수집·집필은 다른 단계)."""

_PLAN_PROMPT = """글 주제: {topic}
섹터: {sector}
맥락(선정 각도): {angle}

[사용 가능한 출처(이 키만 sources 에 사용)]
{catalog}

이 주제로 *독자 마음을 움직이는 깊이 있는 글* 을 쓰기 위한 리서치를 설계하라.

1) angle: 이 글이 잡아야 할 한 줄 각도(차별화 관점). 맥락이 있으면 발전시키고 없으면 창안.
2) reader_intent: 독자가 이 주제를 검색한 진짜 의도 1문장.
3) questions: 핵심 질문 4~6개. 각 질문:
   - id: "Q1"~"Q6"
   - q: 독자 관점의 구체적 질문 (한국어)
   - evidence_kinds: 이 질문에 필요한 근거 종류 1~3개 — stat(공식 통계·수치) | paper(논문·연구)
     | news(최신 뉴스·발표) | case(사례·후기) | definition(개념·배경) | expert(전문가 견해)
   - sources: 위 카탈로그 키 중 1~3개 (우선순위 순). 공식 통계·논문을 뉴스보다 우선.
     카탈로그 범위 밖 주제면 반드시 discover 포함.
   - queries: 그 출처에서 쓸 *서로 다른* 구체 검색어 2개 (1개는 직접형, 1개는 변형·유의어형)
   - min_evidence: 이 질문을 충분히 뒷받침하는 최소 근거 개수 (2~4)

원칙:
- 질문은 서로 다른 차원을 커버 — ① 현황·규모 ② 원인·배경 ③ 영향·전망 ④ 비교·대안 ⑤ 실전 조언.
- 최신성이 중요한 질문(news)과 불변 지식 질문(stat·paper·definition)을 섞어라.
- ★ 논문(academic·kci)은 거짓이 없으니 깊이가 필요한 질문에 적극 배정.
- ★ 확신이 안 서면 sources 마지막에 discover 를 폴백으로 추가(손해 없음).

JSON만 출력:
{{"angle":"...","reader_intent":"...","questions":[{{"id":"Q1","q":"...","evidence_kinds":["stat"],"sources":["kosis","discover"],"queries":["...","..."],"min_evidence":2}}]}}"""


def _extract_json(raw):
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", str(raw))
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _sanitize(plan: dict, topic: str) -> dict | None:
    if not isinstance(plan, dict):
        return None
    questions = []
    for i, q in enumerate((plan.get("questions") or [])[:6], 1):
        text = str(q.get("q", "")).strip()
        if not text:
            continue
        kinds = [k for k in (q.get("evidence_kinds") or []) if k in _EVIDENCE_KINDS] or ["news"]
        srcs = [s for s in (q.get("sources") or []) if s in _VALID_SOURCES]
        if not srcs:
            srcs = ["naver_news", "discover"]
        queries = [str(x).strip() for x in (q.get("queries") or []) if str(x).strip()]
        if not queries:
            queries = [f"{topic} {text[:20]}"]
        try:
            min_ev = max(1, min(4, int(q.get("min_evidence", 2))))
        except Exception:
            min_ev = 2
        questions.append({
            "id": f"Q{i}",
            "q": text,
            "evidence_kinds": kinds[:3],
            "sources": srcs[:3],
            "queries": queries[:2],
            "min_evidence": min_ev,
        })
    if len(questions) < 3:
        return None
    return {
        "topic": topic,
        "angle": str(plan.get("angle", "")).strip(),
        "reader_intent": str(plan.get("reader_intent", "")).strip(),
        "questions": questions,
    }


def _fallback_plan(topic: str, sector: str) -> dict:
    """LLM 설계 실패 시 결정론 폴백 — 보편 5차원 질문 + discover 우선 (주제 적응형)."""
    dims = [
        ("현황·규모", ["stat", "news"], ["kosis", "naver_news", "discover"], "현황 규모 통계"),
        ("원인·배경", ["news", "definition"], ["naver_news", "web", "discover"], "원인 배경 이유"),
        ("영향·전망", ["news", "paper"], ["news", "kci", "discover"], "전망 영향 분석"),
        ("비교·대안", ["stat", "case"], ["discover", "news"], "비교 대안 사례"),
        ("실전 조언", ["case", "expert"], ["blog", "naver_news", "discover"], "방법 유의점 전문가"),
    ]
    questions = []
    for i, (dim, kinds, srcs, q_suffix) in enumerate(dims, 1):
        questions.append({
            "id": f"Q{i}",
            "q": f"{topic}의 {dim}은 어떠한가?",
            "evidence_kinds": kinds,
            "sources": srcs,
            "queries": [f"{topic} {q_suffix}", f"{topic} {dim}"],
            "min_evidence": 2,
        })
    return {
        "topic": topic,
        "angle": f"{topic} — 데이터로 확인한 현재와 전망",
        "reader_intent": f"{topic}에 대해 믿을 수 있는 정보와 판단 근거를 얻고 싶다",
        "questions": questions,
    }


def plan_research(topic: str, sector: str = "", angle: str = "") -> dict:
    """주제 → 리서치 설계도(핵심 질문 + 질문별 출처·쿼리). LLM 동적 설계, 실패 시 폴백.

    반환: {"topic","angle","reader_intent","questions":[{id,q,evidence_kinds,sources,queries,min_evidence}]}
    """
    topic = (topic or "").strip()
    if not topic:
        return {}
    catalog = "\n".join(f"- {k}: {v}" for k, v in _SOURCE_CATALOG.items())
    prompt = _PLAN_PROMPT.format(topic=topic, sector=sector or "-",
                                 angle=(angle or "-")[:300], catalog=catalog)
    # ★ 외부 재시도 = JSON 파싱 실패 시만 (ERRORS [399] — 스로틀 근본 차단).
    # 이전: 3 outer × 3 inner = 9 스폰 → 스로틀 시 _circuit_record_throttle 3회 누적 →
    #   회로 차단기 개방 → 후속 대본·품질 게이트 LLM 전체 차단.
    # 수정: 빈 응답(=Max 스로틀)이면 외부 루프 즉시 종료 → 폴백.
    #   JSON 파싱 실패(응답은 있지만 형식 오류)인 경우만 온도 0.5 로 1회 재시도.
    # 효과: 스로틀 시 스폰 9→3, throttle 레코드 3→1 (회로 차단 임계 3회에 도달 방지).
    from shared.llm import invoke_text
    for _attempt in range(2):
        try:
            # ★ _essential=True (ERRORS [300]): 설계는 수집 품질의 조타수 —
            #   회로 차단 중에도 1회 실시도 보장 (즉시 폴백 금지).
            raw = invoke_text("analyzer", prompt, system=_PLAN_SYSTEM,
                              max_tokens=1600, temperature=0.2 if _attempt == 0 else 0.5,
                              _essential=True)
        except Exception as e:
            log.warning(f"[research] 설계 시도{_attempt + 1} 예외: {e}")
            _g_report("collector", e, module=__name__, func_name="plan_research")
            break  # 예외 → 폴백
        if not raw.strip():
            # ★ 빈 응답 = Max 스로틀 → 외부 루프 즉시 종료 (추가 스폰 금지)
            log.warning(f"[research] '{topic}' 빈 응답(스로틀) → 즉시 폴백 (회로차단기 보호)")
            break
        plan = _sanitize(_extract_json(raw), topic)
        if plan:
            log.info(f"[research] '{topic}' → 질문 {len(plan['questions'])}개 설계 "
                     f"(시도 {_attempt + 1})")
            plan["fallback"] = False
            return plan
        # 응답은 있지만 JSON 파싱 실패 → 온도 0.5 로 1회 재시도 (탈출 아님)
        log.debug(f"[research] 시도{_attempt + 1} JSON 파싱 실패 — 온도 상향 재시도")
    fb = _fallback_plan(topic, sector)
    # ★ 폴백 플래그 (ERRORS [300]) — 조용한 강등 금지: 팩·알림에서 가시화.
    fb["fallback"] = True
    log.warning(f"[research] '{topic}' LLM 설계 실패 → 보편 5차원 폴백")
    return fb


__all__ = ["plan_research"]
