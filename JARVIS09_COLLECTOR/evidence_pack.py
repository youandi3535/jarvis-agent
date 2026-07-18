"""JARVIS09_COLLECTOR/evidence_pack.py — 수집 문서 → 구조화 근거 팩 (ADR 012).

★ 사용자 박제 2026-07-02: "양질의 데이터를 받아오는 게 핵심. 그 데이터로 대본도
  이미지도 만드는 게 그 다음." — 원시 문서 더미를 그대로 넘기지 않고,
  *사실(fact) 단위* 로 추출·출처 박제·중복 제거·커버리지 측정까지 마친
  EvidencePack 을 JARVIS02(대본)·JARVIS06(이미지)·prepublish 게이트에 공급한다.

EvidencePack 구조:
    {
      "theme": str,
      "plan": ResearchPlan,                # research_planner 산출물
      "facts": [
        {"id": "F1", "statement": str,      # 한 문장 사실 (수치·주체·시점 포함)
         "kind": "stat|fact|quote|case",
         "value": str, "unit": str,         # 수치 사실이면 값·단위
         "as_of": str,                      # 기준 시점 (문서에서 확인된 것만)
         "question_id": "Q1",              # 어느 핵심 질문의 근거인가
         "source": {"name","url","type","tier"},
         "confidence": float}
      ],
      "coverage": {"Q1": {"found": int, "need": int, "ok": bool}, ...},
      "doc_count": int, "created_at": str,
    }

원칙:
  - statement 는 반드시 원문에 근거 (LLM 프롬프트로 강제 + 저신뢰 폐기).
  - 출처 없는 fact 는 팩에 들어올 수 없다 (거짓 근거 < 근거 없음).
  - 중복 fact 는 임베딩(shared.embeddings) 코사인 유사도로 제거 (미가용 시 토큰 폴백).
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.collector.evidence")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k):
        pass

_OUT_DIR = Path(__file__).parent / "output" / "evidence"

# ★ 출처 신뢰 등급 — models.SOURCE_TRUST_TIER 단일 진입점 (사용자 박제 2026-07-03 — ADR 013)
#   논문(1) > 공식 API(2) > 뉴스(3) > 기사(4) > 웹(5) > 블로그(6).
#   중복 fact 충돌 시 낮은 티어(=높은 신뢰)가 이긴다 (_dedupe_facts).
from .models import SOURCE_TRUST_TIER as _TIER_BY_TYPE

# ★ 입력 절단 폐지 (사용자 박제 2026-07-17): fact 추출은 수집 문서 *전문* 을 읽는다.
#   옛 티어별 자수컷(_TIER_CHARS)은 뉴스 600·웹 300자 등으로 뒷부분 수치·사실을 통째
#   버렸다 → 폐지. 상수는 하위호환·env 재활성화용으로만 잔존(기본 미적용).
_TIER_CHARS: dict[int, int] = {1: 2500, 2: 1500, 3: 600, 4: 400, 5: 300, 6: 200}
# ★ 티어별 문서당 fact 추출 상한
_TIER_MAX_FACTS_PER_DOC: dict[int, int] = {1: 6, 2: 5, 3: 2, 4: 1, 5: 1, 6: 0}

_EXTRACT_SYSTEM = """당신은 팩트체커 겸 리서처다. 수집 문서에서 *문서에 실제로 적힌*
사실만 추출한다. 문서에 없는 내용을 추론·창작하면 절대 안 된다.
각 사실은 주체·수치·시점이 살아있는 완결된 한 문장으로 정리한다."""

_EXTRACT_PROMPT = """주제: {theme}
핵심 질문 목록:
{questions}

아래 수집 문서들에서 주제와 핵심 질문에 *직접 관련된 사실* 을 추출하라.

규칙:
- 반드시 문서에 명시된 내용만. 문서에 없는 수치·주장 창작 금지.
- 수치가 있는 사실을 최우선 (kind=stat, value·unit 채움).
- category: 지표의 큰 분류 하나 (금리/물가/환율/증시/성장/고용/무역/재정/부동산/에너지/기타 중 하나). 차트 그룹핑에 쓴다.
- label: 차트 축에 쓸 짧은 지표명 6~14자 (예: '기준금리', 'CPI 상승률', '코스피'). 날짜·문장 금지, 지표 이름만.
- 발언 인용은 kind=quote, 사례·후기는 kind=case, 그 외 kind=fact.
- as_of 는 문서에서 확인된 시점만 (없으면 빈 문자열).
- question_id 는 위 질문 중 가장 맞는 것 (없으면 "").
- doc 번호(doc_idx)를 정확히 — 출처 추적에 쓴다.
- 문서 표시 [T1]=논문(최대 6개), [T2]=공식 API(최대 5개), [T3]=뉴스(최대 2개), [T4+]=기타(최대 1개).
- 전체 최대 {max_facts}개. 관련 없는 문서는 건너뛴다.

[수집 문서]
{docs_block}

[★ 추출 전 — 먼저 *전문 리서처의 추출 전략* 을 설계 (꼼꼼·전문·디테일)]
먼저 <design> 안에 추출 전략을 세워라 (중괄호 절대 금지, 6줄 이내):
① [문서 유형·신뢰도] 각 문서가 뉴스·재무·통계·논문·블로그 중 무엇이고 어느 게 신뢰 우선인지.
② [질문 매핑] 위 핵심 질문 각각에 답이 될 수치·사실이 어느 문서(doc 번호)에 있는지.
③ [우선 추출] 수치가 살아있는 사실(kind=stat)을 최우선 — 구체 금액·비율·규모·시점.
④ [상충·중복] 문서 간 값이 다르면 신뢰 높은 출처 채택, 같은 사실은 한 번만.
그 다음 그 전략대로 아래 JSON 을 출력하라. <design> 다음 첫 '{{' 부터가 결과 JSON.

JSON만 출력:
{{"facts":[{{"statement":"...","kind":"stat","value":"12.3","unit":"%","category":"물가","label":"CPI 상승률","as_of":"2026-05","question_id":"Q1","doc_idx":1,"confidence":0.9}}]}}"""


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


def _doc_attr(doc, name: str, default=""):
    """CollectionResult(dataclass) / dict 양쪽 호환 접근."""
    if isinstance(doc, dict):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _docs_block(docs: list, per_doc_chars: int = 0) -> str:
    """★ 수집 원본 전문 주입 (사용자 박제 2026-07-17 — 티어별 자수컷 폐지).

    옛 _TIER_CHARS 절단(뉴스 600·웹 300자 등)을 폐지 — 문서 전문을 그대로 넣어
    뒷부분에만 있는 수치·사실도 fact 추출 대상이 되게 한다.
    env JARVIS_EVIDENCE_PER_DOC_CHARS 를 양수로 주면 그 값으로만 절단(비상 축소용).
    """
    import os as _os_e
    _cap = int(_os_e.getenv("JARVIS_EVIDENCE_PER_DOC_CHARS", "0") or "0") or per_doc_chars
    lines = []
    for i, d in enumerate(docs, 1):
        title = str(_doc_attr(d, "title"))[:80]
        src = _doc_attr(d, "source_type")
        tier = _TIER_BY_TYPE.get(str(src).strip().lower(), 5)
        body = str(_doc_attr(d, "cleaned_text") or _doc_attr(d, "raw_text"))
        if _cap > 0:
            body = body[:_cap]
        # 티어 표시 → LLM 이 추출 우선순위를 구분하도록
        lines.append(f"--- doc {i} [{src}/T{tier}] {title}\n{body}")
    return "\n".join(lines)


def _extract_facts_batch(theme: str, plan: dict, docs: list,
                         max_facts: int = 14, per_doc_chars: int = 1200) -> list[dict]:
    """문서 묶음 1회 LLM 호출 → fact 목록 (doc_idx → 출처 연결)."""
    if not docs:
        return []
    q_lines = "\n".join(f"- {q['id']}: {q['q']}" for q in (plan or {}).get("questions", []))
    prompt = _EXTRACT_PROMPT.format(
        theme=theme, questions=q_lines or "- (질문 미지정 — 주제 관련 사실 위주)",
        max_facts=max_facts, docs_block=_docs_block(docs, per_doc_chars))
    raw = None
    try:
        from shared.llm import invoke_text
        # ★ 단일 호출로 전 문서 처리 (ERRORS [374])
        # max_tokens=4800: 논문·API 티어 고품질 fact 증가 수용 (2026-07-12)
        # timeout=150: 스로틀 시 5분 무한대기 방지 (빈 facts로 계속 진행)
        raw = invoke_text("analyzer", prompt, system=_EXTRACT_SYSTEM,
                          max_tokens=4800, temperature=0.1, timeout=150)
    except Exception as e:
        log.warning(f"[evidence] fact 추출 실패: {e}")
        _g_report("collector", e, module=__name__, func_name="_extract_facts_batch")
        return []
    parsed = _extract_json(raw) or {}
    out = []
    for f in (parsed.get("facts") or [])[:max_facts]:
        stmt = str(f.get("statement", "")).strip()
        try:
            idx = int(f.get("doc_idx", 0))
        except Exception:
            idx = 0
        if not stmt or not (1 <= idx <= len(docs)):
            continue
        try:
            conf = float(f.get("confidence", 0.7))
        except Exception:
            conf = 0.7
        if conf < 0.5:
            continue                     # 저신뢰 폐기 — 거짓 근거 < 근거 없음
        d = docs[idx - 1]
        src_type = str(_doc_attr(d, "source_type") or "web")
        out.append({
            "statement": stmt,
            "kind": str(f.get("kind", "fact")).strip() or "fact",
            "value": str(f.get("value", "")).strip(),
            "unit": str(f.get("unit", "")).strip(),
            "category": str(f.get("category", "")).strip(),
            "label": str(f.get("label", "")).strip()[:14],
            "as_of": str(f.get("as_of", "")).strip(),
            "question_id": str(f.get("question_id", "")).strip(),
            "source": {
                "name": str(_doc_attr(d, "title"))[:80] or src_type,
                "url": str(_doc_attr(d, "url")),
                "type": src_type,
                "tier": _TIER_BY_TYPE.get(src_type, 5),
            },
            "confidence": conf,
        })
    return out


def _dedupe_facts(facts: list[dict], sim_threshold: float = 0.86) -> list[dict]:
    """의미 중복 제거 — 임베딩 코사인 (미가용 시 토큰 자카드 폴백). 신뢰 티어 좋은 것 유지."""
    if len(facts) <= 1:
        return facts
    # 티어(낮을수록 좋음) → confidence 순 정렬 후 앞선 것 우선 보존
    ordered = sorted(facts, key=lambda f: (f["source"].get("tier", 5), -f.get("confidence", 0)))
    kept: list[dict] = []
    try:
        from shared.embeddings import embed_texts, available
        if not available():
            raise RuntimeError("embeddings unavailable")
        import numpy as np
        vecs = embed_texts([f["statement"] for f in ordered])
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = vecs / norms
        kept_idx: list[int] = []
        for i in range(len(ordered)):
            dup = any(float(unit[i] @ unit[j]) >= sim_threshold for j in kept_idx)
            if not dup:
                kept_idx.append(i)
        kept = [ordered[i] for i in kept_idx]
    except Exception:
        # 폴백: ① stat 은 동일 값+단위(한국어 조사 차이 무시) ② 토큰 자카드
        def _toks(s):
            return set(re.findall(r"[\w가-힣]{2,}", s.lower()))

        def _num_key(f):
            v = re.sub(r"[^\d.]", "", str(f.get("value", "")))
            return (v, f.get("unit", "")) if v else None
        seen_toks: list[set] = []
        seen_nums: set = set()
        for f in ordered:
            nk = _num_key(f) if f.get("kind") == "stat" else None
            if nk and nk in seen_nums:
                continue
            t = _toks(f["statement"])
            dup = any(t and s and len(t & s) / max(1, len(t | s)) >= 0.6 for s in seen_toks)
            if dup:
                continue
            kept.append(f)
            seen_toks.append(t)
            if nk:
                seen_nums.add(nk)
    return kept


def _measure_coverage(plan: dict, facts: list[dict]) -> dict:
    cov = {}
    for q in (plan or {}).get("questions", []):
        qid = q["id"]
        found = sum(1 for f in facts if f.get("question_id") == qid)
        need = int(q.get("min_evidence", 2))
        cov[qid] = {"found": found, "need": need, "ok": found >= need}
    return cov


_HIGH_TIER_SET = frozenset({1, 2})   # 논문(1) + 공식 API(2)
_HIGH_TARGET   = 30                  # 고품질 소스 목표 fact 수 (★ 15→30 상향 2026-07-17 — 전문 추출로 사실 밀도 증가분 수용)


def build_evidence_pack(theme: str, plan: dict, docs: list,
                        max_docs: int = 20, per_doc_chars: int = 900) -> dict:
    """수집 문서 → EvidencePack.

    ★ 2-패스 추출 (사용자 박제 2026-07-12):
      Pass-1: 논문(T1)·공식API(T2) 에서만 최대 15개 추출.
      Pass-2: 15개 미달 시에만 뉴스·기사·웹(T3+) 에서 부족분 보충.
    → 고품질 소스가 충분하면 뉴스 LLM 호출 발생하지 않음.
    """
    docs = list(docs or [])
    docs.sort(key=lambda d: _TIER_BY_TYPE.get(str(_doc_attr(d, "source_type")), 5))

    # 고품질(T1·T2) / 후순위(T3+) 분리
    def _tier(d):
        return _TIER_BY_TYPE.get(str(_doc_attr(d, "source_type")).strip().lower(), 5)

    high_docs = [d for d in docs if _tier(d) in _HIGH_TIER_SET]
    low_docs  = [d for d in docs if _tier(d) not in _HIGH_TIER_SET]

    # Pass-1: 논문·API
    facts: list[dict] = []
    if high_docs:
        facts = _extract_facts_batch(theme, plan, high_docs[:max_docs],
                                     max_facts=_HIGH_TARGET, per_doc_chars=per_doc_chars)
        log.info(f"[evidence] Pass-1(논문·API) 문서 {len(high_docs)}개 → fact {len(facts)}개")

    # Pass-2: 부족 시에만 후순위 소스 보충
    gap = _HIGH_TARGET - len(facts)
    if gap > 0 and low_docs:
        log.info(f"[evidence] Pass-2(뉴스·기타) 문서 {len(low_docs)}개 → 부족분 {gap}개 보충 시도")
        extra = _extract_facts_batch(theme, plan, low_docs[:max_docs],
                                     max_facts=gap, per_doc_chars=per_doc_chars)
        facts = facts + extra
        log.info(f"[evidence] Pass-2 결과: +{len(extra)}개 → 합계 {len(facts)}개")
    facts = _dedupe_facts(facts)
    for i, f in enumerate(facts, 1):
        f["id"] = f"F{i}"
    pack = {
        "theme": theme,
        "plan": plan or {},
        "facts": facts,
        "coverage": _measure_coverage(plan, facts),
        "doc_count": len(docs),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    n_stat = sum(1 for f in facts if f.get("kind") == "stat")
    log.info(f"[evidence] '{theme}' 팩 완성: fact {len(facts)}개 (수치 {n_stat}) "
             f"/ 문서 {len(docs)}건 / 커버리지 "
             f"{sum(1 for c in pack['coverage'].values() if c['ok'])}/{len(pack['coverage'])}")
    return pack


def evidence_brief(pack, max_facts: int = 60) -> str:   # ★ 24→60 상향 2026-07-17 — 근거 전량 작성기 주입
    """대본 프롬프트 주입용 근거 브리프 — 질문별 그룹 + 출처 표기.

    JARVIS02 draft_writer 가 그대로 프롬프트에 삽입한다. 사실 번호(F#)로
    글쓴이가 근거를 지목할 수 있게 하고, 목록 밖 수치 사용을 금지한다.

    ★ pack(dict) 또는 facts(list) 둘 다 수용 (Step 3 — collected.facts 직접 입력 지원).
    """
    if isinstance(pack, list):
        pack = {"facts": pack, "plan": {}}
    if not pack or not pack.get("facts"):
        return ""
    plan = pack.get("plan") or {}
    q_map = {q["id"]: q["q"] for q in plan.get("questions", [])}
    by_q: dict[str, list[dict]] = {}
    for f in pack["facts"][:max_facts]:
        by_q.setdefault(f.get("question_id") or "일반", []).append(f)

    lines = ["[★ 리서치 근거 팩 — 본문의 사실·수치는 반드시 아래 근거만 사용]"]
    if plan.get("angle"):
        lines.append(f"(이 글의 각도: {plan['angle']})")
    if plan.get("reader_intent"):
        lines.append(f"(독자 의도: {plan['reader_intent']})")
    for qid, group in by_q.items():
        q_text = q_map.get(qid, "")
        lines.append(f"\n◆ {qid}{(': ' + q_text) if q_text else ''}")
        for fi, f in enumerate(group, 1):
            fid = f.get("id") or f"F{fi}"
            src = f.get("source") or {}
            tail = []
            if f.get("as_of"):
                tail.append(f"기준 {f['as_of']}")
            if src.get("name"):
                tail.append(f"출처: {src['name']}")
            tail_s = f" ({', '.join(tail)})" if tail else ""
            lines.append(f"  {fid}. {f.get('statement', '')}{tail_s}")
    lines.append("\n★ 위 근거에 *없는* 수치·사실을 본문에 쓰지 마라 — 근거 없는 수치는 거짓이다.")
    lines.append("★ 근거는 그대로 복붙하지 말고 글 흐름에 자연스럽게 녹여 쓰되, 수치는 원값 그대로.")
    return "\n".join(lines)


class _FactDoc:
    """prepublish factuality 게이트 호환 어댑터 — CollectionResult 형태 흉내."""
    __slots__ = ("theme", "source_type", "url", "title", "cleaned_text", "word_count", "meta")

    def __init__(self, theme, source_type, url, title, cleaned_text):
        self.theme = theme
        self.source_type = source_type
        self.url = url
        self.title = title
        self.cleaned_text = cleaned_text
        self.word_count = len((cleaned_text or "").split())
        self.meta = {}


def as_source_docs(pack) -> list:
    """EvidencePack → 발행 전 사실성 게이트(source_docs)용 문서 목록.

    ★ pack(dict) 또는 facts(list) 둘 다 수용 (Step 3 — collected.facts 직접 입력 지원).
    """
    if isinstance(pack, list):
        pack = {"facts": pack, "theme": ""}
    docs = []
    theme = (pack or {}).get("theme", "")
    for f in (pack or {}).get("facts", []):
        src = f.get("source") or {}
        docs.append(_FactDoc(
            theme=theme,
            source_type=src.get("type", "evidence"),
            url=src.get("url", ""),
            title=src.get("name", ""),
            cleaned_text=f.get("statement", ""),
        ))
    return docs


def _label_batch(statements: list[str]) -> list[str]:
    """stat fact 문장 → 차트 라벨(6~14자) 배치 생성. LLM 1회, 실패 시 문장 앞부분 폴백.

    ★ 숫자·단위·출처는 여기서 절대 다루지 않는다 — 라벨(이름)만 작명.
    """
    fallback = [s[:14] for s in statements]
    try:
        from shared.llm import invoke_text
        joined = "\n".join(f"{i + 1}. {s[:90]}" for i, s in enumerate(statements))
        raw = invoke_text(
            "analyzer",
            "다음 각 문장의 수치가 *무엇의 값* 인지 나타내는 6~14자 한국어 라벨을 지어라.\n"
            "차트 축 라벨용 — 명사구만, 조사·서술어 금지 (예: '온실가스 감축률', '기준금리', "
            "'태양광 설비용량').\n"
            "★ 라벨은 수치의 *종류와 일치* — 금액이면 '~액'(예: 영업이익액), "
            "비율(%)이면 '~률/비중', 개수면 '~수'. 문장 속 다른 지표명을 빌려오지 마라.\n"
            f'문장 수와 같은 길이의 JSON 문자열 배열만 출력:\n{joined}',
            max_tokens=800,
            _nonessential=True, timeout=60,
        )
        m = re.search(r"\[[\s\S]*\]", raw or "")
        parsed = json.loads(m.group(0)) if m else None
        if isinstance(parsed, list) and len(parsed) == len(statements):
            return [str(x).strip()[:14] or fb for x, fb in zip(parsed, fallback)]
    except Exception:
        pass
    return fallback


def _noun_phrase(statement: str) -> str:
    """문장에서 축 라벨용 짧은 명사구 추출 — 주격/주제 조사(는/은/이/가) 앞부분 우선,
    최후에만 문장 앞 14자. (LLM 호출 없음 — 결정론)"""
    s = (statement or "").strip()
    if not s:
        return ""
    # 명사구 + 주격/주제 조사 패턴 (첫 매치)
    m = re.search(r"([가-힣A-Za-z][\w가-힣·]{1,13})(?:는|은|이|가)(?=\s|\d|$)", s)
    if m and m.group(1).strip():
        return m.group(1).strip()[:14]
    return s[:14]


def _axis_label(f: dict, fallback: str | None = None) -> str:
    """차트 축 라벨 우선순위: 추출 label → (구버전 폴백 LLM 라벨) → category → 문장 명사구.
    fact["label"] 가 추출 단계에서 이미 오므로 대개 LLM 없이 결정된다."""
    lb = (f.get("label") or "").strip()
    if lb:
        return lb[:14]
    if fallback and str(fallback).strip():
        return str(fallback).strip()[:14]
    cat = (f.get("category") or "").strip()
    if cat and cat != "기타":
        return cat[:14]
    return _noun_phrase(f.get("statement", ""))


def _scale_filter(items: list) -> list:
    """스케일 가드 — 한 그룹 내 값 편차가 20배 초과면 무관 지표가 섞인 것으로 보고
    중앙값 기준 0.1~10배 군집만 남긴다 (나머지 드롭). 무관 지표를 스케일로도 분리."""
    if len(items) < 2:
        return items
    absvals = [abs(v) for _, v, _ in items if v]
    if not absvals:
        return items
    lo, hi = min(absvals), max(absvals)
    if lo <= 0 or hi / lo <= 20:
        return items
    med = statistics.median([abs(v) for _, v, _ in items])
    if med <= 0:
        return items
    kept = [it for it in items if med * 0.1 <= abs(it[1]) <= med * 10]
    return kept or items


def _dedup_labels(data: list[dict]) -> list[dict]:
    """같은 dataset 내 라벨 중복 병합 — 값이 같으면 하나로 병합, 다르면 ' (2)', ' (3)' 접미.
    중복 라벨이 축에 그대로 반복되지 않게 한다."""
    values_seen: dict = {}
    counts: dict = {}
    out: list[dict] = []
    for d in data:
        lb, val = d["label"], d["value"]
        if lb in values_seen:
            if val in values_seen[lb]:
                continue                       # 값 동일 → 병합(스킵)
            values_seen[lb].add(val)
            counts[lb] += 1
            out.append({"label": f"{lb} ({counts[lb]})", "value": val})
        else:
            values_seen[lb] = {val}
            counts[lb] = 1
            out.append({"label": lb, "value": val})
    return out


def facts_to_datasets(pack: dict, max_datasets: int = 60) -> list[dict]:   # ★ 24→60 상향 2026-07-17
    """★ 수치 fact → 인포그래픽 데이터셋 승격 (사용자 박제 2026-07-03 — ADR 013 보강).

    "수치는 텍스트 안에도 많다" — 근거팩의 kind=stat fact(값·단위·기준일·출처 박제)를
    차트 엔진 dataset 형식으로 변환해, 공식 통계 테이블이 없는 주제에서도 인포그래픽
    공급을 확대한다. 진실성 불변 조건:
      - 값·단위·기준일·출처 = fact 그대로 (LLM 은 라벨 작명만)
      - 범위값('1708~1733')·비수치 값은 스킵 (단일 수치만 — 거짓 차트 < 차트 없음)

    ★ 그룹핑: (category, unit) — 지표 분류(금리/물가/증시…)와 단위가 같은 fact 들이 한
      차트의 행. plan.questions 부재로 question_id 가 비어도 category 로 갈라지므로
      "단위 단독 잡탕 차트"(기준금리+미국채+심리지수 혼합)가 생기지 않는다. category 가
      비면 "기타"로 폴백. 1행 그룹도 유효 (KPI 카드형 렌더).
    ★ 축 라벨: 추출 단계 label(지표명) 우선 → category → 문장 명사구. LLM 호출 제거
      (라벨이 전부 빈 구버전 fact 만 있을 때 1회 폴백 배치 방어만 유지).
    ★ 스케일 가드: 한 그룹 값 편차 20배 초과면 중앙값 군집만 남겨 무관 지표를 재분리.
    ★ 중복 라벨: 같은 dataset 내 동일 라벨은 값 병합 또는 접미 번호로 축 중복 방지.
    """
    stats = [f for f in (pack.get("facts") or []) if f.get("kind") == "stat"]
    rows: list[tuple[dict, float]] = []
    for f in stats:
        _v = str(f.get("value", "")).replace(",", "").strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?", _v):
            continue   # 범위·비수치 — 정직하게 차트화 불가 → 스킵
        try:
            rows.append((f, float(_v)))
        except Exception:
            continue
    if not rows:
        return []

    # 축 라벨: 추출 label 이 이미 오므로 LLM 없이 결정. 단, 라벨이 *전부* 빈
    # 구버전 fact 만 있을 때만 1회 폴백 배치 호출(방어).
    if all(not (f.get("label") or "").strip() for f, _ in rows):
        _fb = _label_batch([f["statement"] for f, _ in rows])
    else:
        _fb = [None] * len(rows)
    labels = [_axis_label(f, fb) for (f, _v), fb in zip(rows, _fb)]

    theme = pack.get("theme", "")
    groups: dict = {}
    for (f, v), lb in zip(rows, labels):
        cat = (f.get("category") or "기타").strip() or "기타"
        unit = (f.get("unit") or "").strip()
        groups.setdefault((cat, unit), []).append((f, v, lb))

    from JARVIS09_COLLECTOR.models import dataset_fingerprint as _dfp
    out: list[dict] = []
    for (cat, unit), items in groups.items():
        items = _scale_filter(items)
        if not items:
            continue
        # 대표 출처 = 신뢰 티어 최상 fact (논문>API>뉴스>기사>웹 — ADR 013)
        best = min(items, key=lambda x: x[0].get("source", {}).get("tier", 5))[0]
        # 제목 작명: category 우선 → 없으면 라벨 상위 2개 → 최후 폴백
        if cat and cat != "기타":
            title = f"{cat} 지표" + (f" ({unit})" if unit else "")
        else:
            top_labels = [lb for _, _, lb in items if lb][:2]
            if top_labels:
                title = "·".join(top_labels) + " 등"
            else:
                title = f"{theme} 핵심 수치" + (f" ({unit})" if unit else "")
        data = _dedup_labels([{"label": (lb or f["statement"][:14]), "value": v}
                              for f, v, lb in items[:20]])   # ★ 8→20 상향 2026-07-17 (fact 유래 차트 행 확대)
        src = best.get("source") or {}
        out.append({
            "title": title,
            "unit": unit,
            "viz_hint": "bar_chart",      # ★ 스키마 통일 (Step 2) — 3 생산자 공통 키
            "data": data,
            "source": {"provider": f"evidence:{src.get('type', '')}",
                       "name": src.get("name", ""),
                       "url": src.get("url", ""),
                       "as_of": best.get("as_of", "")},
            "fingerprint": _dfp(title, unit),
            "_from_facts": True,          # all_numbers dedupe 근거 (fact 이중표현 표시)
        })
    out.sort(key=lambda d: -len(d["data"]))   # 다행(多行) 차트 우선
    return out[:max_datasets]


__all__ = [
    "build_evidence_pack",
    "evidence_brief", "as_source_docs",
    "facts_to_datasets",
]
