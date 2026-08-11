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
         "basis": "actual|forecast|threshold|",  # 실적/전망/기준선 (""=미상 → 가산 불가)
         "verbatim": bool,                  # 값이 원문에 실재하는지 대조 결과 (신규 2026-08-10)
         "as_of": str,                      # 기준 시점 (문서에서 확인된 것만)
         "question_id": "Q1",              # 어느 핵심 질문의 근거인가
         "source": {"name","url","type","tier"},
         "confidence": float}
      ],
      "coverage": {"Q1": {"found": int, "need": int, "ok": bool}, ...},
      "doc_count": int, "created_at": str,
    }

원칙:
  - statement 는 반드시 원문에 근거 (LLM 프롬프트 + 저신뢰 폐기 + ★ 수치는 원문 대조 `verbatim`).
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
#   공식 API(1) > 뉴스(2) > 기사(3) > 웹(4) > 블로그(5).
#   중복 fact 충돌 시 낮은 티어(=높은 신뢰)가 이긴다 (_dedupe_facts).
from .models import SOURCE_TRUST_TIER as _TIER_BY_TYPE
# ★ 축 라벨 상한·basis 어휘도 models 단일 소스 파생 (2026-08-10 — 사본 금지).
from .models import (AXIS_LABEL_MAX as _QUALIFIED_LABEL_MAX,
                     BASIS_TITLE as _BASIS_TITLE,
                     BASIS_KINDS as _BASIS_KINDS,
                     LOWEST_TRUST_TIER as _LOWEST_TIER,
                     source_tier as _source_tier)

# ★ 입력 절단 폐지 (사용자 박제 2026-07-17): fact 추출은 수집 문서 *전문* 을 읽는다.
#   옛 티어별 자수컷(_TIER_CHARS)은 뉴스 600·웹 300자 등으로 뒷부분 수치·사실을 통째
#   버렸다 → 폐지. 상수는 하위호환·env 재활성화용으로만 잔존(기본 미적용).
_TIER_CHARS: dict[int, int] = {1: 1500, 2: 600, 3: 400, 4: 300, 5: 200}
# ★ 티어별 문서당 fact 추출 상한
_TIER_MAX_FACTS_PER_DOC: dict[int, int] = {1: 5, 2: 2, 3: 1, 4: 1, 5: 0}

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
- basis: 이 수치의 성격 하나 — actual(이미 일어난 실적·집계값) / forecast(전망·추정·목표치) /
  threshold(요건·기준선·한도처럼 '넘어야 하는 값'). 문서로 판단이 안 서면 빈 문자열.
  ★ 실적과 전망을 같은 차트 축에 올리면 안 되므로 이 구분이 그룹 분리에 쓰인다.
- 발언 인용은 kind=quote, 사례·후기는 kind=case, 그 외 kind=fact.
- as_of 는 문서에서 확인된 시점만 (없으면 빈 문자열).
- question_id 는 위 질문 중 가장 맞는 것 (없으면 "").
- doc 번호(doc_idx)를 정확히 — 출처 추적에 쓴다.
- 문서 표시 [T1]=공식 API, [T2]=뉴스, [T3+]=기타.
- 전체 최대 {max_facts}개. 관련 없는 문서는 건너뛴다.

[수집 문서]
{docs_block}

[★ 추출 전 — 먼저 *전문 리서처의 추출 전략* 을 설계 (꼼꼼·전문·디테일)]
먼저 <design> 안에 추출 전략을 세워라 (중괄호 절대 금지, 6줄 이내):
① [문서 유형·신뢰도] 각 문서가 뉴스·재무·통계·블로그 중 무엇이고 어느 게 신뢰 우선인지.
② [질문 매핑] 위 핵심 질문 각각에 답이 될 수치·사실이 어느 문서(doc 번호)에 있는지.
③ [우선 추출] 수치가 살아있는 사실(kind=stat)을 최우선 — 구체 금액·비율·규모·시점.
④ [상충·중복] 문서 간 값이 다르면 신뢰 높은 출처 채택, 같은 사실은 한 번만.
그 다음 그 전략대로 아래 JSON 을 출력하라. <design> 다음 첫 '{{' 부터가 결과 JSON.

JSON만 출력:
{{"facts":[{{"statement":"...","kind":"stat","value":"12.3","unit":"%","category":"물가","label":"CPI 상승률","basis":"actual","as_of":"2026-05","question_id":"Q1","doc_idx":1,"confidence":0.9}}]}}"""


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


# ★ per-doc 절단 기본값 — **이 파일에서 이 상수 하나만** (ERRORS [544], 원칙①).
#   왜 상수화했나: 같은 노브가 한 파일 안에 기본값 **셋**으로 흩어져 있었다 —
#     `_docs_block(per_doc_chars=0)` / `_extract_facts(=1200)` / `build_evidence_pack(=900)`.
#   실제로 걸리는 건 제일 바깥값(900)이라, 바로 위 docstring 이 선언한
#   *"티어별 자수컷 폐지 — 문서 전문 주입"(사용자 박제 2026-07-17)* 이 **조용히 무효**였다.
#   실측 손실: 팩당 35,000~47,000 토큰분(문서 15개 중 11~12개가 900자 초과, 중앙 1,618~3,449자).
#   0 = 절단 없음(전문). 비상 축소는 코드가 아니라 env `JARVIS_EVIDENCE_PER_DOC_CHARS` 로만.
PER_DOC_CHARS_DEFAULT = 0


def _docs_block(docs: list, per_doc_chars: int = PER_DOC_CHARS_DEFAULT) -> str:
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
        tier = _TIER_BY_TYPE.get(str(src).strip().lower(), _LOWEST_TIER)
        body = str(_doc_attr(d, "cleaned_text") or _doc_attr(d, "raw_text"))
        if _cap > 0:
            body = body[:_cap]
        # 티어 표시 → LLM 이 추출 우선순위를 구분하도록
        lines.append(f"--- doc {i} [{src}/T{tier}] {title}\n{body}")
    return "\n".join(lines)


def build_corpus_digest(docs: list, per_source_chars: int = 700) -> str:
    """★ 코퍼스 dense digest (사용자 박제 2026-07-19 — distill 압축).

    원시 문서(≈16만자)를 *소스별 정보밀도 높은 요약* 으로 압축(수치·고유명사·핵심 주장·인과·서사
    맥락 보존, 중복 verbatim·보일러플레이트·광고·네비 제거)해 writer 프롬프트의 corpus 전문을
    대체 → 콜당 입력·prefill·TPM 스로틀 감소. 수치 정보는 facts(evidence_brief)가 별도 전량
    보존하므로 digest 는 서사·맥락 담당. 사실성 게이트는 원문(collection_docs) 그대로 사용(별개).

    ★ 선계산(저부하 창)에서만 실행 — 발행창(is_publishing)이면 "" 반환 → 호출자는 원문
      build_corpus_block 폴백. distill LLM 비용은 20:00/06:00 창에 흡수. analyzer alias 는
      발행창 LLM 경합을 피하려 선계산으로 이전된 것이라(shared/llm.py:448) 이 게이트 유지가 정석.
    ★ 2026-07-24 P2: 종전엔 이 게이트로 캐시미스 시 원문 폴백이 205문서·119K자로 폭주했는데,
      그 폭주는 *원문 폴백 자체를 유계로 캡*(P2-b: DRAFT_CORPUS_MAX_CHARS 40000 + P2-c: 스윕
      쿼터)해서 잡는다 — 발행창에 LLM(analyzer)을 새로 들이지 않고(P3 와 정합) 코퍼스만 유계화.
    """
    docs = list(docs or [])
    if not docs:
        return ""
    try:
        from shared.llm import is_publishing, invoke_text
    except Exception:
        return ""
    if is_publishing():
        return ""   # 발행창 — 즉석 digest 금지(analyzer 경합 회피), 호출자 원문 폴백(P2-b 로 캡됨)
    blocks = []
    for i, d in enumerate(docs, 1):
        body = str(_doc_attr(d, "cleaned_text") or _doc_attr(d, "raw_text") or "").strip()
        if not body:
            continue
        title = str(_doc_attr(d, "title"))[:70]
        src = str(_doc_attr(d, "source_type") or "web")
        blocks.append(f"[자료 {i} | {src} | {title}]\n{body[:8000]}")  # 요약 입력 상한(요약이라 OK)
    if not blocks:
        return ""
    prompt = (
        "아래 수집 자료들을 *소스별로* 정보밀도 높게 요약하라.\n"
        "보존: 수치·고유명사·핵심 주장·인과관계·서사 맥락. 제거: 중복 문장·보일러플레이트·광고·네비게이션.\n"
        f"각 자료를 {per_source_chars}자 내외로. 원문을 그대로 베끼지 말고 밀도 있게 재서술.\n\n"
        + "\n\n".join(blocks)
        + "\n\n출력: 자료마다 `[자료 N | 소스 | 제목]` 헤더 + 요약."
    )
    try:
        raw = invoke_text("analyzer_evidence", prompt, max_tokens=6000, temperature=0.2, _nonessential=True)
    except Exception as e:
        log.warning(f"[digest] corpus 요약 실패: {e}")
        return ""
    if not (raw or "").strip():
        return ""
    log.info(f"[digest] corpus {len(blocks)}건 → 요약 {len(raw)}자(원문 대비 압축)")
    return ("[★ 수집 자료 요약 — 글의 서사·맥락·통찰의 근거. 수치 인용은 실데이터 카탈로그·근거 팩 참조]\n"
            + raw.strip())


# ══════════════════════════════════════════════════════════════════════════
# ★ 추출값 ↔ 원문 대조 (사용자 박제 2026-08-10 — 신규 능력)
#   값은 LLM 이 문서에서 뽑는다(_extract_facts_batch). 그런데 *그 값이 원문에 실재하는지*
#   대조하는 코드가 저장소 전역에 0행이었다 — 집행 수단이 프롬프트와 confidence 임계뿐이라
#   이 파일 자신이 상단 docstring 에 "LLM 프롬프트로 강제" 라고 적어 두었다.
#   그래서 사설·보도자료에서 뽑힌 수치가 검증 없이 차트가 됐다(2026-08-10 slot2·slot6).
#   ★ LLM 재호출 0 — *꼴* 로만 판정한다(결정론, 비용 0).
# ══════════════════════════════════════════════════════════════════════════

# ★ 수치 토큰의 *꼴* 은 이 파일이 소유하지 않는다 (사용자 지시 2026-08-10 — ①단일 진입점).
#   종전엔 `_NUM_TOKEN_RE` 라는 **같은 이름의 다른 정규식** 이 여기와
#   JARVIS06_IMAGE/validators/image_data_verifier.py 두 곳에 있었고, 이름이 같아 두 벌인 줄
#   아무도 몰랐다 (J06 쪽만 '19만8900' 을 19 와 8900 두 수로 읽었다).
#   09 는 06 을 import 할 수 없으므로(수집 단일 진입점) 소유는 shared/numeric.py 다.
from shared.numeric import corpus_numbers as _doc_number_candidates, num_and_dp as _num_and_dp


def _same_number(n: float, dp_n: int, cand: float, dp_c: int) -> bool:
    """표기 흔들림만 흡수하는 *엄격* 수치 동일성 판정.

    ★ models.grounds() 를 쓰지 않는 이유 (사용자 지시 2026-08-10):
      grounds() 는 '대본 수치가 수집값에 근거하는가' 용이라 ±5% 를 허용한다. 그 폭을
      원문 대조에 쓰면 문서에 널린 아무 수나 근거로 잡혀 게이트가 사실상 상수 True 가 된다
      ("다른 수를 같다고 하면 안 된다"). 여기서 흡수할 것은 *표기 차이* 뿐이다 —
      쉼표(1,234=1234)·후행 0·낮은 정밀도 표시(78.18 을 78.2 로 적은 것).
    """
    if n == cand:
        return True
    # ★ 표시 자릿수 차이가 1 을 넘으면 '표기 차이' 가 아니다.
    #   이 가드가 없으면 문서에 널린 정수 하나가 소수 여러 자리 값을 근거로 잡는다
    #   (실측: 원문의 '3' 이 3.14159 를 통과시켰다) — 게이트가 사실상 상수 True 가 된다.
    if abs(dp_n - dp_c) > 1:
        return False
    dp = min(dp_n, dp_c)            # 덜 정밀하게 적힌 쪽 기준으로 비교
    q = 10.0 ** dp
    return abs(round(n * q) - round(cand * q)) < 0.5


def _verbatim_check(value: str, text: str) -> bool:
    """추출 수치가 원문에 실재하는가 (LLM 호출 0).

    보지 않는 것 두 가지 — 둘 다 '수가 원문에 있는가' 와 다른 축이라 여기서 잡으면 오탐이 된다:
      · 단위: 문서마다 표기가 다르다(억/억원/조). 판정 대상은 *수 그 자체* 다.
      · 부호가 원문에 없을 때의 방향: 원문이 "manufacturing shed 62,000 jobs" 라고 쓰면
        문서에 있는 토큰은 `62,000` 이고 감소라는 방향은 *산문* 이 담는다. 정규식은 산문을
        읽지 못하므로, 원문 토큰에 부호가 **명시되지 않았으면** 크기로만 대조한다.
        원문이 `-62,000` 처럼 부호를 명시했다면 부호까지 일치해야 한다(방향을 뒤집지 못하게).
    """
    got = _num_and_dp(str(value or ""))
    if got is None:
        return False                # 범위값·비수치 — 애초에 차트화 불가(정직하게 실패)
    n, dp_n = got
    body = str(text or "")
    if not body:
        return False
    for c, dp_c, signed in _doc_number_candidates(body):
        if _same_number(n, dp_n, c, dp_c):
            return True
        if not signed and _same_number(abs(n), dp_n, abs(c), dp_c):
            return True
    return False


def verbatim_state(fact: dict):
    """fact 의 원문 대조 *3-상태* 판정 — True(대조 통과) / False(대조 실패) / None(대조 불가).

    ★ 왜 3-상태인가 (사용자 박제 2026-08-10 — 1차 수정의 회귀):
      '검증에 실패했다' 와 '검증 정보가 없다' 는 다르다. 원문 대조 능력이 생기기 *이전에*
      만들어진 팩에는 `verbatim` 키 자체가 없는데, 이를 False(실패)로 읽어 fail-closed
      배제하자 구 선계산 캐시 소비 시 fact 유래 dataset 이 12→6 으로 반토막 났다
      (실측: economic_20260810 팩). 없는 것은 *모름* 이지 실패가 아니다.
    """
    v = fact.get("verbatim")
    return v if isinstance(v, bool) else None


def _doc_text_index(docs: list) -> dict:
    """docs → {url: 본문} · {title: 본문} 색인. fact.source 로 원문을 되찾기 위한 것."""
    idx: dict = {}
    for d in docs or []:
        body = str(_doc_attr(d, "cleaned_text") or _doc_attr(d, "raw_text") or "")
        if not body:
            continue
        for k in (str(_doc_attr(d, "url") or ""), str(_doc_attr(d, "title") or "")):
            if k and k not in idx:
                idx[k] = body
    return idx


def backfill_verbatim(pack: dict, docs: list) -> dict:
    """대조 기록이 없는 fact 에 원문 대조 결과를 *지금* 채운다 (LLM 0 · 결정론).

    ★ 조용히 통과시키지도, 조용히 버리지도 않는다 (사용자 지시 2026-08-10):
      원문이 아직 손에 있으면(선계산 캐시는 docs 를 함께 담는다) 모름을 *측정* 으로 바꾼다.
      원문을 되찾지 못한 fact 만 `verbatim=None`(대조 불가) 로 남기고, 건수를 로그와
      pack['extraction'] 에 남겨 캐시된 팩만 보고도 대조율을 알 수 있게 한다.

    반환: 같은 pack 객체(제자리 갱신). facts 를 버리지 않는다 — 승격 심사만 이 값을 본다.
    """
    facts = (pack or {}).get("facts") or []
    todo = [f for f in facts if f.get("kind") == "stat" and verbatim_state(f) is None]
    if not todo:
        return pack
    idx = _doc_text_index(docs)
    n_ok = n_bad = n_unk = 0
    for f in todo:
        src = f.get("source") or {}
        body = idx.get(str(src.get("url") or "")) or idx.get(str(src.get("name") or ""))
        val = str(f.get("value") or "").strip()
        if not body or not val:
            f["verbatim"] = None        # 대조 불가 — 원문이 없다(모름을 모름으로 남긴다)
            n_unk += 1
            continue
        ok = _verbatim_check(val, body)
        f["verbatim"] = ok
        n_ok += int(ok)
        n_bad += int(not ok)
    ex = dict((pack or {}).get("extraction") or {})
    ex.update({"backfilled": len(todo), "backfill_ok": n_ok,
               "backfill_failed": n_bad, "unverifiable": n_unk})
    pack["extraction"] = ex
    log.info(f"[evidence] 구팩 원문 대조 소급: {len(todo)}건 → 통과 {n_ok} · "
             f"실패 {n_bad} · 대조불가 {n_unk} (원문 미보유)")
    return pack


def _extract_facts_batch(theme: str, plan: dict, docs: list,
                         max_facts: int = 14,
                         per_doc_chars: int = PER_DOC_CHARS_DEFAULT) -> list[dict]:
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
        # max_tokens=4800: 공식 API 티어 고품질 fact 증가 수용 (2026-07-12)
        # timeout=150: 스로틀 시 5분 무한대기 방지 (빈 facts로 계속 진행)
        raw = invoke_text("analyzer_evidence", prompt, system=_EXTRACT_SYSTEM,
                          max_tokens=4800, temperature=0.1, timeout=150)
    except Exception as e:
        log.warning(f"[evidence] fact 추출 실패: {e}")
        _g_report("collector", e, module=__name__, func_name="_extract_facts_batch")
        return []
    parsed = _extract_json(raw) or {}
    out = []
    n_unverified = 0
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
        _val = str(f.get("value", "")).strip()
        _basis = str(f.get("basis", "")).strip().lower()
        if _basis not in _BASIS_KINDS:      # ★ 어휘 목록은 models.BASIS_TITLE 파생 (사본 금지)
            _basis = ""              # 미상 — 하류는 '가산 불가' 로 안전 실패한다
        # ★ 원문 대조 — 이 fact 의 값이 *LLM 이 읽은 그 문서* 에 실재하는가.
        #   대조 실패해도 fact 를 버리지 않는다: 요약·서사 근거로는 여전히 쓸모가 있고,
        #   차트 승격에서만 배제한다(facts_to_datasets). 근거 없는 수치를 지우는 것이 아니라
        #   *차트라는 권위 있는 표현* 을 얻는 문턱을 두는 것이 여기서의 목적이다.
        _doc_text = str(_doc_attr(d, "cleaned_text") or _doc_attr(d, "raw_text") or "")
        _vb = _verbatim_check(_val, _doc_text) if _val else False
        if _val and not _vb:
            n_unverified += 1
        out.append({
            "statement": stmt,
            "kind": str(f.get("kind", "fact")).strip() or "fact",
            "value": _val,
            "unit": str(f.get("unit", "")).strip(),
            "category": str(f.get("category", "")).strip(),
            "label": str(f.get("label", "")).strip()[:14],
            "basis": _basis,
            "as_of": str(f.get("as_of", "")).strip(),
            "question_id": str(f.get("question_id", "")).strip(),
            "source": {
                "name": str(_doc_attr(d, "title"))[:80] or src_type,
                "url": str(_doc_attr(d, "url")),
                "type": src_type,
                "tier": _TIER_BY_TYPE.get(src_type, _LOWEST_TIER),
            },
            "confidence": conf,
            "verbatim": _vb,
        })
    if n_unverified:
        # ★ 조용히 버리지 않는다 — 몇 건이 원문 대조에 실패했는지 항상 남긴다.
        log.info(f"[evidence] 원문 대조: {len(out)}건 중 {n_unverified}건 미확인 "
                 f"(폐기하지 않음 — 차트 승격에서만 배제)")
    return out


def _dedupe_facts(facts: list[dict], sim_threshold: float = 0.86) -> list[dict]:
    """의미 중복 제거 — 임베딩 코사인 (미가용 시 토큰 자카드 폴백). 신뢰 티어 좋은 것 유지."""
    if len(facts) <= 1:
        return facts
    # 티어(낮을수록 좋음) → confidence 순 정렬 후 앞선 것 우선 보존
    # ★ 동티어 tie-break 에 원문 대조 결과를 넣는다 (2026-08-10) — 같은 신뢰 등급이면
    #   '원문에 있는 값' 이 이긴다. 종전엔 confidence(LLM 자기신고)만 봤다.
    ordered = sorted(facts, key=lambda f: (_source_tier(f.get("source")),
                                           {True: 0, None: 1, False: 2}[verbatim_state(f)],
                                           -f.get("confidence", 0)))
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


_HIGH_TIER_SET = frozenset({1})   # 공식 API(1) — 최상위 신뢰 티어
_HIGH_TARGET   = 30                  # 고품질 소스 목표 fact 수 (★ 15→30 상향 2026-07-17 — 전문 추출로 사실 밀도 증가분 수용)


def build_evidence_pack(theme: str, plan: dict, docs: list,
                        max_docs: int = 20, per_doc_chars: int = PER_DOC_CHARS_DEFAULT,
                        category: str = "") -> dict:
    """수집 문서 → EvidencePack.

    ★ 2-패스 추출 (사용자 박제 2026-07-12):
      Pass-1: 공식 API(T1) 에서만 최대 15개 추출.
      Pass-2: 15개 미달 시에만 뉴스·기사·웹(T2+) 에서 부족분 보충.
    → 고품질 소스가 충분하면 뉴스 LLM 호출 발생하지 않음.
    """
    docs = list(docs or [])
    docs.sort(key=lambda d: _TIER_BY_TYPE.get(str(_doc_attr(d, "source_type")), 5))

    # 고품질(T1) / 후순위(T2+) 분리
    def _tier(d):
        return _TIER_BY_TYPE.get(str(_doc_attr(d, "source_type")).strip().lower(), _LOWEST_TIER)

    high_docs = [d for d in docs if _tier(d) in _HIGH_TIER_SET]
    low_docs  = [d for d in docs if _tier(d) not in _HIGH_TIER_SET]

    # Pass-1: 공식 API
    facts: list[dict] = []
    if high_docs:
        facts = _extract_facts_batch(theme, plan, high_docs[:max_docs],
                                     max_facts=_HIGH_TARGET, per_doc_chars=per_doc_chars)
        log.info(f"[evidence] Pass-1(공식 API) 문서 {len(high_docs)}개 → fact {len(facts)}개")

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
    n_stat = sum(1 for f in facts if f.get("kind") == "stat")
    n_vb = sum(1 for f in facts if f.get("kind") == "stat" and f.get("verbatim") is True)
    pack = {
        "theme": theme,
        # ★ 카테고리를 팩에 박제 (2026-08-10): 차트 승격 정책(출처 등급·원문 대조 문턱)이
        #   CATEGORY_POLICY 파생이라 facts_to_datasets 가 카테고리를 알아야 한다. 호출자에
        #   `if category == ...` 분기를 만들지 않으려고 값 자체를 상자에 실어 보낸다.
        "category": (category or "").strip(),
        "plan": plan or {},
        "facts": facts,
        "coverage": _measure_coverage(plan, facts),
        "doc_count": len(docs),
        # 관측용 — 캐시된 팩만 보고도 대조율을 알 수 있게 (조용한 폐기 방지).
        "extraction": {"stat": n_stat, "verbatim_ok": n_vb},
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    log.info(f"[evidence] '{theme}' 팩 완성: fact {len(facts)}개 (수치 {n_stat}, "
             f"원문대조 통과 {n_vb}) / 문서 {len(docs)}건 / 커버리지 "
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


# ══════════════════════════════════════════════════════════════════════════
# ★ 행 단위 진실 보존 (사용자 박제 2026-08-10 — 뿌리2)
#   종전 조립은 (category, unit) 로 묶은 뒤 대표 fact 1개의 출처·기준일을 dataset 전체에
#   박제하고 행에는 {label, value} 만 남겼다. 그래서 KOFIA·yfinance 수치가 '한국은행'
#   출처로, 2023-08 값이 '2026.08 기준' 배지로 발행됐다(2026-08-10 slot3·slot4).
#   손실은 되돌릴 수 없다 — 하류에는 '서로 다른 지표 3개' 로 보이므로 어떤 검사도
#   시점충돌·중복으로 인식할 수 없었다. 그래서 *버리지 않는다* 가 유일한 해법이다.
# ══════════════════════════════════════════════════════════════════════════

# 자유 표기 as_of("2026-08-07"·"2026"·"2026년 상반기") → (연, 월, 일) 파싱.
_AS_OF_RE = re.compile(r"(\d{4})\s*[-/.년]?\s*(\d{1,2})?\s*[-/.월]?\s*(\d{1,2})?")

# 구분자를 붙인 축 라벨의 상한. 기본 라벨 상한은 14(_axis_label)지만, 구분자가 잘리면
# 구분이 사라져 D02 로 되돌아간다 → 구분자를 살리고 base 를 먼저 자른다.
# ★ 값은 models.AXIS_LABEL_MAX 파생 (2026-08-10, 상단 import) — 종전엔
#   chart_data._clean_label 과 여기가 각자 22 를 들고 있었다(사본).

# basis(수치의 성격) → 제목에 쓸 한국어. 그룹이 갈라지면 제목도 갈라져야 한다 —
# 같은 제목·단위면 dataset_fingerprint 가 같아져 조립 단계에서 한쪽이 사라진다.
# ★ 어휘의 단일 소스는 models.BASIS_TITLE (상단 import) — 추출 유효성 검사·그룹 키·제목이
#   같은 목록에서 파생한다.


def _as_of_parts(as_of: str) -> tuple[int, int, int]:
    """as_of 문자열 → (연, 월, 일). 파싱 불가·부재는 (0, 0, 0)."""
    m = _AS_OF_RE.search(str(as_of or ""))
    if not m:
        return (0, 0, 0)
    y = int(m.group(1))
    mo = int(m.group(2)) if m.group(2) and 1 <= int(m.group(2)) <= 12 else 0
    d = int(m.group(3)) if m.group(3) and 1 <= int(m.group(3)) <= 31 else 0
    return (y, mo, d)


def _abbrev_as_of(as_of: str, level: int = 1) -> str:
    """as_of → 축 라벨에 붙일 짧은 시점 표기. level 1='2026.08', 2='2026.08.07'."""
    y, mo, d = _as_of_parts(as_of)
    if not y:
        return str(as_of or "").strip()[:10]
    if not mo:
        return f"{y}"
    if level >= 2 and d:
        return f"{y}.{mo:02d}.{d:02d}"
    return f"{y}.{mo:02d}"


def _join_label(base: str, qual: str) -> str:
    """base + 구분자. 상한 초과 시 *base 를 먼저 잘라* 구분자를 보존한다.

    ★ 구분자 예산은 상한에서 파생한다 (2026-08-10 — ②동적 설계): 종전엔 호출부가
      출처명을 `[:12]` 로 미리 잘라 넘겼는데, 그 12 는 어디서도 파생되지 않은 숫자였고
      *유일성 판정까지* 잘린 문자열로 하고 있었다 — 앞 12자가 같은 두 기관이 '구분 불가'
      로 판정돼 최후수단 (2),(3) 접미로 떨어졌다. 유일성은 온전한 이름으로 보고,
      자르는 일은 표시 직전인 여기서만 한다.
    """
    if not qual:
        return base[:_QUALIFIED_LABEL_MAX]
    q = qual[:_QUALIFIED_LABEL_MAX // 2]          # 구분자가 라벨을 통째로 먹지 않게 절반까지
    room = _QUALIFIED_LABEL_MAX - len(q) - 1
    return f"{base[:room]} {q}"


def _row_meta(f: dict) -> dict:
    """fact → 행 메타 (models.ROW_META 스키마). 행이 자기 출처·시점을 들고 다닌다."""
    src = f.get("source") or {}
    return {
        "as_of": str(f.get("as_of") or ""),
        "source": {"provider": f"evidence:{src.get('type', '')}",
                   "name": str(src.get("name") or ""),
                   "url": str(src.get("url") or ""),
                   "type": str(src.get("type") or ""),
                   "tier": _source_tier(src)},
        "category": str(f.get("category") or ""),
        "basis": str(f.get("basis") or ""),
        "fact_id": str(f.get("id") or ""),
        # ★ 3-상태 그대로 (True/False/None). bool 로 눌러 담으면 '대조 불가' 가
        #   '대조 실패' 로 둔갑해 하류가 없는 판정을 있다고 읽는다.
        "verbatim": verbatim_state(f),
    }


def _disambiguate_labels(items: list) -> list[dict]:
    """같은 라벨·다른 값을 *구분 차원을 복원해* 갈라놓는다 (items = [(fact, value, label)]).

    ★ 왜 ' (2)',' (3)' 접미를 폐기했나 (사용자 박제 2026-08-10):
      같은 라벨에 다른 값이 온다는 것은 대개 '다른 지표' 가 아니라 *같은 지표의 다른 시점·
      다른 출처* 다. 접미 번호는 그 사실을 **지우고** 독자에게 '달러/원 환율이 3종류 있다'
      로 읽히게 만들었으며, 하류의 중복 가드(dedupe_chart_rows)까지 무력화했다.
      정보를 만들어 내지 못하는 구분자 대신, 이미 fact 가 갖고 있던 구분 차원을 복원한다.
        ① 기준 시점이 다르면  → '기준금리 2023.08'
        ② 시점이 같고 출처가 다르면 → '기준금리 · 한국은행'
        ③ 그래도 같으면 값 동일은 병합, 다르면 최후에만 (2),(3)
    ★ tolerance(±5%) 병합은 쓰지 않는다 — 1,419 vs 1,408(0.78% 차) 같은 *정당한 시계열* 이
      조용히 삭제된다. 중복 제거가 아니라 구분 복원이 이 함수의 일이다.
    """
    # 같은 (라벨, 값) 은 같은 관측 — 병합(첫 항목 유지: _dedupe_facts 가 신뢰순 정렬해 둠)
    uniq: list = []
    seen_pairs: set = set()
    for f, v, lb in items:
        key = (lb, v)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        uniq.append((f, v, lb))

    by_base: dict = {}
    for i, (f, v, lb) in enumerate(uniq):
        by_base.setdefault(lb, []).append(i)

    quals: list[str] = [""] * len(uniq)
    for lb, idxs in by_base.items():
        if len(idxs) < 2:
            continue
        for level in (1, 2):
            cand = [_abbrev_as_of(uniq[i][0].get("as_of", ""), level) for i in idxs]
            if all(cand) and len(set(cand)) == len(idxs):
                for i, q in zip(idxs, cand):
                    quals[i] = q
                break
        else:
            names = [str((uniq[i][0].get("source") or {}).get("name") or "") for i in idxs]
            if all(names) and len(set(names)) == len(idxs):
                for i, nm in zip(idxs, names):
                    quals[i] = f"· {nm}"
            else:
                for n, i in enumerate(idxs, 1):
                    quals[i] = "" if n == 1 else f"({n})"   # 최후 수단

    return [{**_row_meta(f), "label": _join_label(lb, q), "value": v}
            for (f, v, lb), q in zip(uniq, quals)]


def _row_provenance(rows: list[dict]) -> tuple[list[dict], dict]:
    """*실제로 dataset 에 남은 행들* → (source_mix, as_of_range). 대표 1건 박제 대신 분포를 남긴다.

    ★ 입력이 fact 목록이 아니라 완성된 행인 이유: 병합·중복 제거로 사라진 행의 출처를
      분포에 계상하면 푸터가 화면에 없는 출처를 인쇄한다 — D01 과 같은 종류의 거짓말이다.
    ★ 종전 대표 선정 `min(items, key=tier)` 는 동티어에서 **삽입 순서 임의 tie-break** 였다
      (2026-08-10 금리 8건이 전부 tier=1 → 대표가 예측 불가). 여기서는 등장 횟수 → 티어 →
      최초 등장 순의 *안정 정렬* 이라 같은 입력이면 항상 같은 결과가 나온다.
    """
    info: dict = {}
    for i, r in enumerate(rows):
        src = r.get("source") or {}
        key = (str(src.get("type") or ""), str(src.get("name") or ""), str(src.get("url") or ""))
        rec = info.get(key)
        if rec is None:
            rec = info[key] = {"provider": f"evidence:{key[0]}", "name": key[1],
                               "url": key[2], "tier": _source_tier(src),
                               "count": 0, "_order": i}
        rec["count"] += 1
    mix = sorted(info.values(), key=lambda r: (-r["count"], r["tier"], r["_order"]))
    for r in mix:
        r.pop("_order", None)

    as_ofs = [str(r.get("as_of") or "").strip() for r in rows]
    as_ofs = [a for a in as_ofs if a]
    if as_ofs:
        ordered = sorted(set(as_ofs), key=lambda a: (_as_of_parts(a), a))
        rng = {"min": ordered[0], "max": ordered[-1], "distinct": len(ordered)}
    else:
        rng = {"min": "", "max": "", "distinct": 0}
    return mix, rng


def _chart_admissible(stats: list[dict], pol: dict) -> list[dict]:
    """차트 승격 자격 심사 — 출처 등급 + 원문 대조 (사용자 박제 2026-08-10, 신규 능력 2).

    ★ 왜 필요한가: 신뢰순위(공식API>뉴스>기사>웹)가 *선호* 에만 쓰이고 *배제* 에는 한 번도
      쓰이지 않았다. 그래서 신문 **사설** 과 기업 **보도자료** 의 수치가 한국은행 API 와
      동등하게 차트가 됐다(2026-08-10 slot6·slot2). 차트는 권위 있는 표현이므로 문턱이 있어야 한다.
    ★ 어휘 목록('[Editorial]' 같은 문자열)을 박지 않는다 — 이미 있는 tier·type 구조에서
      파생한다. 새 출처가 source_registry 에 추가되면 이 정책이 자동으로 적용된다.
    """
    # ★ 문턱에 폴백 리터럴을 두지 않는다 (2026-08-10) — `policy_for` 가 기본값을 덮어
    #   씌워 돌려주므로 두 노브는 *항상* 존재한다. `.get(..., 2)` 를 적는 순간 그 2 가
    #   정책의 사본이 되어, 레지스트리를 고쳐도 여기만 옛 값으로 남는다.
    max_tier = int(pol["chart_max_source_tier"])
    vb_above = int(pol["chart_verbatim_above_tier"])
    kept, drop_tier, drop_vb, unknown = [], 0, 0, 0
    for f in stats:
        tier = _source_tier(f.get("source"))
        if tier > max_tier:
            drop_tier += 1
            continue
        if tier > vb_above:
            st = verbatim_state(f)
            if st is False:
                # 대조 *실패* — 값이 원문에 없다. 차트 승격 불가 (fail-closed).
                # 텍스트 근거로는 살아 있다 — 여기서 막는 것은 '차트가 되는 것' 뿐이다.
                drop_vb += 1
                continue
            if st is None:
                # ★ 대조 *불가* — 실패가 아니라 모름 (사용자 박제 2026-08-10).
                #   원문을 되찾을 수 있으면 backfill_verbatim 이 이미 측정으로 바꿔 놓는다.
                #   여기까지 온 것은 원문이 사라진 구팩뿐이다. 모름을 실패로 단정해 버리면
                #   결함 없는 데이터가 통째로 사라진다(실측 12→6). 승격은 시키되
                #   '검증됨' 이라고 말하지 않는다 — 행 메타 verbatim=None 이 그대로 흐르고,
                #   dataset 에 verbatim_unknown_rows 로 건수가 박힌다.
                unknown += 1
        kept.append(f)
    if drop_tier or drop_vb or unknown:
        log.info(f"[evidence] 차트 승격 심사: 수치 fact {len(stats)}건 중 "
                 f"출처등급 미달 {drop_tier}건 · 원문대조 실패 {drop_vb}건 배제 · "
                 f"대조불가(모름) {unknown}건 승격하되 미검증 표시 "
                 f"→ {len(kept)}건 승격 (tier<={max_tier}, verbatim>tier{vb_above})")
    return kept


def facts_to_datasets(pack: dict, max_datasets: int = 60,
                      category: str = "",
                      llm_label_fallback: bool = True) -> list[dict]:   # ★ 24→60 상향 2026-07-17
    """★ 수치 fact → 인포그래픽 데이터셋 승격 (사용자 박제 2026-07-03 — ADR 013 보강).

    "수치는 텍스트 안에도 많다" — 근거팩의 kind=stat fact(값·단위·기준일·출처 박제)를
    차트 엔진 dataset 형식으로 변환해, 공식 통계 테이블이 없는 주제에서도 인포그래픽
    공급을 확대한다. 진실성 불변 조건:
      - 값·단위·기준일·출처 = fact 그대로 (LLM 은 라벨 작명만)
      - 범위값('1708~1733')·비수치 값은 스킵 (단일 수치만 — 거짓 차트 < 차트 없음)

    ★ 승격 자격 (2026-08-10): 출처 등급 + 원문 대조를 통과한 fact 만 차트가 된다
      (`_chart_admissible`). 등급 미달로 남는 게 없으면 **차트를 포기한다** — 거짓 차트 < 차트 없음.
    ★ 그룹핑: (category, unit, basis) — 지표 분류·단위가 같아도 *실적과 전망* 은 한 축에
      올리지 않는다(2026-08-10 slot6 은 상반기 실적 2건 + 2026 전망 1건을 더해 '합계 341,000명'
      을 인쇄했다). basis 가 미상("")끼리는 한 그룹 — 표기로 덮을 수 없는 구분만 키에 넣는다.
      국가·as_of 는 키에 넣지 않는다: 그 축이 fact 에 없거나(국가) 쪼개면 차트가 파편화되어
      (as_of: 금리 8행 → 1/4/1/2) 죽는다. 대신 하류가 판정할 수 있도록 *증거를 실어 보낸다*.
    ★ 행 단위 진실 보존: 각 행이 as_of·source·category·basis·fact_id·verbatim 을 들고 간다
      (models.ROW_META). dataset 레벨엔 그 분포(`source_mix`·`as_of_range`·`mixed_time`).
      `source`(대표 1건)는 하위호환용으로 유지 — 소비자는 분포가 있으면 그것을 먼저 쓴다.
    ★ 축 라벨: 추출 단계 label(지표명) 우선 → category → 문장 명사구. LLM 호출 제거
      (라벨이 전부 빈 구버전 fact 만 있을 때 1회 폴백 배치 방어만 유지).
    ★ 스케일 가드: 한 그룹 값 편차 20배 초과면 중앙값 군집만 남겨 무관 지표를 재분리.
    ★ 중복 라벨: 구분 차원(시점·출처)을 복원해 갈라놓는다 (`_disambiguate_labels`).
    """
    from .models import policy_for
    all_stats = [f for f in (pack.get("facts") or []) if f.get("kind") == "stat"]
    pol = policy_for(category or (pack or {}).get("category") or "")
    stats = _chart_admissible(all_stats, pol)
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
        if all_stats:
            log.info("[evidence] 승격 자격을 갖춘 수치 fact 0건 → 차트 포기 (거짓 차트 < 차트 없음)")
        return []

    # 축 라벨: 추출 label 이 이미 오므로 LLM 없이 결정. 단, 라벨이 *전부* 빈
    # 구버전 fact 만 있을 때만 1회 폴백 배치 호출(방어).
    #   ★ llm_label_fallback=False 면 이 방어 배치도 생략한다 — 선계산 캐시 재조립 경로가
    #     "발행창 추출 LLM 0회" 계약을 *확률이 아니라 구조로* 지키게 하기 위한 것.
    #     (실측: 캐시 41개 중 라벨 전무 팩은 0개라 실제 발화는 없었지만, 계약은 절대여야 한다)
    if llm_label_fallback and all(not (f.get("label") or "").strip() for f, _ in rows):
        _fb = _label_batch([f["statement"] for f, _ in rows])
    else:
        _fb = [None] * len(rows)
    labels = [_axis_label(f, fb) for (f, _v), fb in zip(rows, _fb)]

    theme = pack.get("theme", "")
    groups: dict = {}
    for (f, v), lb in zip(rows, labels):
        cat = (f.get("category") or "기타").strip() or "기타"
        unit = (f.get("unit") or "").strip()
        basis = (f.get("basis") or "").strip()
        groups.setdefault((cat, unit, basis), []).append((f, v, lb))

    from JARVIS09_COLLECTOR.models import dataset_fingerprint as _dfp
    out: list[dict] = []
    for (cat, unit, basis), items in groups.items():
        items = _scale_filter(items)
        if not items:
            continue
        items = items[:20]   # ★ 8→20 상향 2026-07-17 (fact 유래 차트 행 확대)
        # 제목 작명: category(+basis) 우선 → 없으면 라벨 상위 2개 → 최후 폴백
        _b = _BASIS_TITLE.get(basis, "")
        if cat and cat != "기타":
            title = f"{cat} {_b or '지표'}" + (f" ({unit})" if unit else "")
        else:
            top_labels = [lb for _, _, lb in items if lb][:2]
            if top_labels:
                title = "·".join(top_labels) + (f" 등 {_b}".rstrip() if _b else " 등")
            else:
                title = f"{theme} 핵심 수치{(' ' + _b) if _b else ''}" + (f" ({unit})" if unit else "")
        data = _disambiguate_labels([(f, v, (lb or f["statement"][:14])) for f, v, lb in items])
        source_mix, as_of_range = _row_provenance(data)
        rep = source_mix[0] if source_mix else {}
        # 같은 base 라벨이 서로 다른 시점으로 2회 이상 → 시계열이 카테고리로 위장한 상태.
        _by_lb: dict = {}
        for f, _v, lb in items:
            _by_lb.setdefault(lb, set()).add(str(f.get("as_of") or ""))
        mixed_time = any(len(v) > 1 for v in _by_lb.values())
        ds = {
            "title": title,
            "unit": unit,
            "viz_hint": "bar_chart",      # ★ 스키마 통일 (Step 2) — 3 생산자 공통 키
            "data": data,
            # ★ 하위호환 대표 1건. as_of 는 *최신* 시점 — 단일 시점 주장이 불가능한
            #   데이터에서 어느 하나를 골라야 한다면 '자료 최신 시점' 이 가장 덜 틀린다.
            #   진실은 as_of_range 에 있고, 소비자는 그것을 먼저 봐야 한다.
            "source": {"provider": rep.get("provider", "evidence:"),
                       "name": rep.get("name", ""),
                       "url": rep.get("url", ""),
                       "as_of": as_of_range["max"]},
            "source_mix": source_mix,
            "as_of_range": as_of_range,
            "fingerprint": _dfp(title, unit),
            "_from_facts": True,          # all_numbers dedupe 근거 (fact 이중표현 표시)
        }
        if mixed_time:
            ds["mixed_time"] = True
        # ★ '검증됨' 과 '모름' 을 화면·게이트가 구분할 수 있도록 건수를 남긴다.
        _unk = sum(1 for r in data if r.get("verbatim") is None)
        if _unk:
            ds["verbatim_unknown_rows"] = _unk
        out.append(ds)
    out.sort(key=lambda d: -len(d["data"]))   # 다행(多行) 차트 우선
    return out[:max_datasets]


__all__ = [
    "build_evidence_pack",
    "evidence_brief", "as_source_docs",
    "facts_to_datasets",
]
