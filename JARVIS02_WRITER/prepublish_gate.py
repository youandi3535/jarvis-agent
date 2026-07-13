"""발행 전 품질 게이트 — 사실성(차단) + 유익성·매력도(재생성) 단일 진입점.

★ 사용자 박제 2026-06-28 — "팩트만, 그리고 너무 읽고 싶은 글만 발행".
economic_poster._verify_all / trend_theme_writer._verify_all 양쪽이 호출한다.
구조 검증(_layer3_verify_draft) 통과 후에만 실행 → LLM 비용 절약.

반환: list[dict] — [{"kind": "factuality"|"engagement", "detail": str}].
빈 리스트면 통과. 호출자는 각 항목을 Issue(step=WRITER step, kind, detail) 로 변환한다.

★ kind 가 "draft_quality" 가 아니므로 _fix_drafts 가 inline 패치를 시도하지 않고
  곧장 unfixed 처리 → harness 가 해당 WRITER step 을 재실행 = 재작성 순환.
  (fact 도 engagement 도 inline 으로 못 고침 — 다시 써야 함)

★ fingerprint 안정성: Issue.detail 에 *점수 raw 숫자·attempt 변동값* 금지.
  factuality=claim 텍스트(같은 거짓 반복 시 동일 지문 → abort), engagement=실패
  차원 태그(engagement/usefulness — 안정).

★ 킬스위치(라이브 파이프라인 안전): 환경변수로 즉시 비활성화 가능.
  PREPUBLISH_FACT_GATE=0       사실성 게이트 끔
  PREPUBLISH_ENGAGEMENT_GATE=0 매력도 게이트 끔
  PREPUBLISH_IMAGE_GATE=0      이미지(차트) 사실성 게이트 끔
  PREPUBLISH_CROSSCHECK_GATE=0 본문↔차트 수치 교차대조 게이트 끔

★ 이미지 사실성 (사용자 박제 2026-06-29): 본문 수치는 사실성 게이트가, *차트 안의 수치*
  는 이미지 게이트가 막는다. JARVIS06 render_from_spec 가 검증 우회(실데이터 미확인)
  차트를 unverified 로 기록 → 여기서 차단 → 재작성 순환.
"""
from __future__ import annotations
import os
import re
import logging
log = logging.getLogger(__name__)

_MIN_BODY = 200  # 이 미만은 구조 검증(_layer3_verify_draft)이 이미 잡음 — 중복 방지


def _disabled(env_key: str) -> bool:
    return os.getenv(env_key, "1").strip().lower() in ("0", "false", "off", "no")


def _draft_body(draft: dict) -> str:
    body = draft.get("full_html") or draft.get("html") or draft.get("content") or ""
    if isinstance(body, dict):
        body = body.get("html") or body.get("content") or ""
    return body or ""


def prepublish_quality_issues(draft, post_type: str = "",
                              source_docs=None, market_data=None,
                              stocks_data=None, collected=None) -> list[dict]:
    """발행 전 품질 게이트 — 사실성 + 매력도. [{"kind","detail"}] 반환 (빈=통과).

    ★ 1-c (2026-07-02): stocks_data(실측 종목 재무)를 넘기면 본문의 PER·ROE·현재가 등
      수치를 실측값과 *결정론적으로* 대조 — LLM 전사 오류·조작(예: PER 463.9)을 차단한다.
    ★ Step 10 (2026-07-05): collected(CollectedData) 넘기면 사실성 grounding 정답을
      단일 소스에서 보강(경제 topic_pack datasets·facts 포함). 종목밴드는 stocks_data 로 유지.
    ★ LLM 1회 (2026-07-12): 사실성·매력도 통합 단일 호출(_combined_quality_call).
    """
    body = _draft_body(draft)
    if not body or len(body) < _MIN_BODY:
        return []
    out: list[dict] = []

    # ── 결정론 검사 (LLM 0회) ──────────────────────────────────────────
    if not _disabled("PREPUBLISH_FACT_GATE"):
        out.extend(_stock_facts_leg(body, stocks_data))
    if not _disabled("PREPUBLISH_IMAGE_GATE"):
        out.extend(_image_factuality_leg(draft, body))
    if not _disabled("PREPUBLISH_CROSSCHECK_GATE"):
        out.extend(_crosscheck_leg(draft, body))

    # ── 통합 LLM 1회: 사실성 + 매력도 ────────────────────────────────
    _fact_on = not _disabled("PREPUBLISH_FACT_GATE")
    _eng_on = not _disabled("PREPUBLISH_ENGAGEMENT_GATE")
    if _fact_on or _eng_on:
        title = (draft.get("title") or draft.get("keyword") or "").strip()
        corpus = ""
        try:
            from JARVIS02_WRITER.law_enforcer import (
                _build_source_corpus, _collect_gt_floats,
                _collected_gt, _claim_all_grounded, _market_point_deltas,
                _NUMERIC_UNIT_RE,
            )
            corpus = _build_source_corpus(source_docs, market_data)
        except Exception as e:
            log.warning(f"[prepublish_gate] law_enforcer import 실패: {e}")

        cqr = _combined_quality_call(body, title, corpus, post_type)

        if _fact_on:
            gt: list = []
            try:
                gt = (_collect_gt_floats(market_data, stocks_data, corpus)
                      + _collected_gt(collected) + _market_point_deltas(market_data))
            except Exception:
                pass
            blocked_n = 0
            for claim in (cqr.get("blocked_claims") or []):
                # ★ ERRORS harness 2026-07-12: _claim_all_grounded 는 단위-숫자 토큰이
                #   전혀 없으면(_NUMERIC_UNIT_RE 미매치) 설계상 항상 False(미확인) 반환한다.
                #   LLM 프롬프트는 "숫자 없는 서술 제외"를 지시하지만 흑자/적자 같은
                #   종목 손익 분류 주장은 종종 숫자 없이 blocked_claims 에 섞여 들어온다 —
                #   그러면 어떤 재작성에도 영원히 grounded=False 라 무한 재작성 순환에 빠진다.
                #   숫자 토큰이 없는 주장은 stocks_data 실측(is_profit)으로만 결정론 대조하고,
                #   대조 불가(매치 없음)면 정책대로 차단하지 않는다(★ 숫자 없는 서술 제외).
                if not _NUMERIC_UNIT_RE.search(claim):
                    issue = _profit_claim_issue(claim, stocks_data)
                    if issue:
                        out.append(issue)
                        blocked_n += 1
                    continue
                try:
                    grounded = _claim_all_grounded(claim, gt) if gt else False
                except Exception:
                    grounded = False
                if not grounded:
                    out.append({"kind": "factuality",
                                "detail": f"[사실성] 출처·데이터 미확인: {claim[:120]}"})
                    blocked_n += 1
            if blocked_n:
                log.warning(f"[prepublish_gate] 사실성 차단 {blocked_n}건 → 재작성 순환")

        if _eng_on and not cqr.get("engagement_passed", True):
            dims = cqr.get("failed_dims") or []
            tag = ",".join(sorted(dims)) if dims else "전반"
            log.warning(f"[prepublish_gate] 매력도 미달 차원={tag} → 재작성 순환")
            out.append({"kind": "engagement", "detail": f"[매력도/유익성] 임계 미달 차원: {tag}"})

    return out


def _pg_to_float(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").replace("원", "").strip())
    except (TypeError, ValueError):
        return None


# ★ 1-c (2026-07-02): 본문 재무 수치 ↔ 실측 stocks_data 결정론 대조.
#   지표별 본문 수치가 *어떤 실측 종목값과도* 허용오차 밖이면 전사 오류/조작으로 간주.
#   fail-closed(차단)이므로 오탐 방지 위해 관대한 오차 — 명백한 불일치만 잡는다.
_STOCK_METRIC_PATTERNS = {
    "per":       (r'PER[^\d\-]{0,6}(-?\d[\d,]*\.?\d*)\s*배', 0.10, 0.5),   # ±10% 또는 ±0.5
    "roe":       (r'ROE[^\d\-]{0,6}(-?\d[\d,]*\.?\d*)\s*%',  0.10, 0.5),
    "op_margin": (r'영업이익률[^\d\-]{0,6}(-?\d[\d,]*\.?\d*)\s*%', 0.10, 0.5),
    "price":     (r'현재가[^\d\-]{0,8}(-?\d[\d,]*)\s*원',    0.05, 0.0),   # ±5%
}


def _stock_facts_leg(body: str, stocks_data) -> list[dict]:
    stocks = (stocks_data or {}).get("stocks") if isinstance(stocks_data, dict) else None
    if not stocks:
        return []
    # 지표별 실측값 집합
    real: dict[str, list[float]] = {k: [] for k in _STOCK_METRIC_PATTERNS}
    for s in stocks:
        if not isinstance(s, dict):
            continue
        for k in real:
            v = _pg_to_float(s.get(k))
            if v is not None:
                # ★ 단위 정합 (ERRORS [344]): roe·op_margin 은 stocks_data 에 소수(0.15)로
                #   저장되나 본문·패턴은 %(15) 단위 → |v|<=1 이면 비율로 보고 ×100 승격.
                #   미승격 시 13.6%(본문) vs 0.136(실측) 비교로 진실 수치를 오차단.
                if k in ("roe", "op_margin") and abs(v) <= 1:
                    v *= 100
                real[k].append(v)
    out: list[dict] = []
    for metric, (pat, rel, ab) in _STOCK_METRIC_PATTERNS.items():
        reals = real.get(metric) or []
        if not reals:
            continue   # 실측 없으면 판정 보류(fail-open)
        for m in re.finditer(pat, body):
            v = _pg_to_float(m.group(1))
            if v is None:
                continue
            if not any(abs(v - rv) <= max(abs(rv) * rel, ab) for rv in reals):
                out.append({"kind": "factuality",
                            "detail": f"[사실성] 본문 {metric.upper()} {v} — 실측 종목 데이터와 불일치(전사 오류·조작 의심)"})
    if out:
        log.warning(f"[prepublish_gate] 실측 재무 불일치 {len(out)}건 → 재작성 순환")
    return out


def _profit_claim_issue(claim: str, stocks_data) -> dict | None:
    """숫자 없는 흑자/적자(손익 분류) 주장 → stocks_data 실측 is_profit 결정론 대조.

    ★ ERRORS harness 2026-07-12: _claim_all_grounded 는 단위-숫자 토큰 매칭 전용이라
      "OO은 흑자 종목, XX는 적자 종목" 처럼 숫자가 없는 손익 분류 주장은 검증 불가능
      (설계상 항상 미확인=차단). 이런 주장은 stocks_data(collect_stocks_data 의
      is_profit = net_income>0)로 직접 대조 가능하므로 여기서 결정론 판정한다.
      종목명이 stocks_data 에 없거나 흑자/적자 단어가 같은 절에 없으면 대조 불가 →
      정책대로(★ 숫자 없는 서술 제외) 차단하지 않고 None 반환.

    ★ 절 단위 매칭(쉼표 분리) — 고정폭 문자 윈도우는 "A는 흑자 종목인 반면, B는
      적자 종목" 처럼 대조 문장에서 쉼표 너머 *다른 종목의* 흑자/적자 단어를 잘못
      끌어와 오탐(정상 주장을 차단)한다. 종목명과 흑자/적자 단어가 *같은 쉼표절*
      안에 있을 때만 대조한다.
    """
    stocks = (stocks_data or {}).get("stocks") if isinstance(stocks_data, dict) else None
    if not stocks:
        return None
    clauses = claim.split(",")
    for s in stocks:
        if not isinstance(s, dict):
            continue
        name = (s.get("name") or "").strip()
        is_profit = s.get("is_profit")
        if not name or is_profit is None:
            continue
        for clause in clauses:
            if name not in clause:
                continue
            if "흑자" in clause and not is_profit:
                return {"kind": "factuality",
                        "detail": f"[사실성] {name} 흑자 분류 주장 — 실측 순이익 적자와 불일치"}
            if "적자" in clause and is_profit:
                return {"kind": "factuality",
                        "detail": f"[사실성] {name} 적자 분류 주장 — 실측 순이익 흑자와 불일치"}
    return None


def _combined_quality_call(body: str, title: str, corpus: str, post_type: str) -> dict:
    """사실성 + 매력도 통합 LLM 1회 판정 (★ 사용자 박제 2026-07-12).

    fail-open: LLM 실패·스로틀 시 모두 통과.
    Returns: {"blocked_claims": [str], "engagement_passed": bool, "failed_dims": [str]}
    """
    import json as _json
    from shared.llm import invoke_text as _inv

    stripped = re.sub(r"<[^>]+>", " ", body or "")[:4000].strip()
    if not stripped:
        return {"blocked_claims": [], "engagement_passed": True, "failed_dims": []}

    corpus_snippet = (corpus or "").strip()[:2000] or "(없음)"
    prompt = (
        f"제목: {title}\n\n[본문]\n{stripped}\n\n[출처]\n{corpus_snippet}\n\n"
        "아래 두 가지를 동시에 판정하라.\n\n"
        "## A. 사실성 — 발행 차단 주장\n"
        "본문에서 *구체적 수치가 포함된 주장* 중 발행하면 안 되는 것만 골라라.\n"
        "차단 기준: (a) 출처 수치와 모순 (b) 구체 수치인데 출처에 근거 전혀 없음\n"
        "★ 차단 제외: 숫자 없는 서술·전망·해석, 상식 수치, 출처에서 추론 가능한 수치\n\n"
        "## B. 매력도·유익성 (임계 engagement≥70, usefulness≥70, title_hook≥60)\n\n"
        "JSON 하나만 반환(다른 말 금지):\n"
        '{"blocked_claims":["차단 주장 원문 최대5개, 없으면 []"],'
        '"engagement_score":85,"usefulness_score":80,"title_hook_score":70,'
        '"failed_dims":["임계 미달 차원 목록, 없으면 []"]}'
    )
    try:
        raw = _inv("fact_judge", prompt, max_tokens=600, timeout=90, _nonessential=True)
        if not (raw or "").strip():
            return {"blocked_claims": [], "engagement_passed": True, "failed_dims": []}
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return {"blocked_claims": [], "engagement_passed": True, "failed_dims": []}
        obj = _json.loads(m.group())
        blocked = [str(x).strip() for x in (obj.get("blocked_claims") or []) if str(x).strip()]
        raw_dims = list(obj.get("failed_dims") or [])
        if not raw_dims:
            if int(obj.get("engagement_score", 100) or 100) < 70:
                raw_dims.append("engagement")
            if int(obj.get("usefulness_score", 100) or 100) < 70:
                raw_dims.append("usefulness")
            if int(obj.get("title_hook_score", 100) or 100) < 60:
                raw_dims.append("title_hook")
        return {"blocked_claims": blocked, "engagement_passed": not raw_dims, "failed_dims": raw_dims}
    except Exception as e:
        log.warning(f"[prepublish_gate] 통합 품질 판정 실패 → 통과(fail-open): {e}")
        return {"blocked_claims": [], "engagement_passed": True, "failed_dims": []}


_IMG_EXT = re.compile(r"\.(?:jpg|jpeg|png|webp)$", re.I)


def _walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def _collect_image_paths(draft, body: str) -> list[str]:
    """draft blocks + 본문 HTML 에서 이미지 파일 경로 후보 수집."""
    paths: list[str] = []
    blocks = draft.get("blocks") if isinstance(draft, dict) else None
    if isinstance(blocks, (list, tuple)):
        for s in _walk_strings(blocks):
            if _IMG_EXT.search(s):
                paths.append(s)
    for m in re.finditer(r'src=["\']([^"\']+\.(?:jpg|jpeg|png|webp))["\']', body or "", re.I):
        paths.append(m.group(1))
    # dedupe (순서 보존)
    seen: set = set()
    return [p for p in paths if not (p in seen or seen.add(p))]


def _image_factuality_leg(draft, body) -> list[dict]:
    """이미지(차트) 사실성 — render 시 unverified 로 기록된 수치 차트가 있으면 차단.

    JARVIS06 render_from_spec 가 모든 생성 이미지의 검증 결과를 provenance 레지스트리에
    기록한다. 수치 차트가 실데이터로 검증 안 된 채 렌더되면 verified=False 로 남고,
    여기서 그것을 잡아 재작성 순환으로 보낸다 (fail-open — 게이트 자체 오류는 발행 허용)."""
    try:
        from JARVIS06_IMAGE.validators.image_data_verifier import lookup_provenance
    except Exception as e:
        log.warning(f"[prepublish_gate] 이미지 검증 import 실패 → 통과: {e}")
        return []
    out: list[dict] = []
    for p in _collect_image_paths(draft, body):
        try:
            prov = lookup_provenance(p)
        except Exception:
            prov = None
        if prov and prov.get("verified") is False:
            out.append({"kind": "factuality",
                        "detail": f"[이미지사실성] 출처 미검증 수치 차트: {os.path.basename(p)}"})
    if out:
        log.warning(f"[prepublish_gate] 이미지 사실성 차단 {len(out)}건 → 재작성 순환")
        for o in out:
            log.warning(f"  ↳ {o['detail']}")
    return out


# ★ 2-4 (2026-07-02): 본문 수치 ↔ 차트 수치 교차대조.
#   같은 지표가 본문과 차트에서 서로 다른 값이면 독자가 즉시 불신 → 차단.
#   오탐이 곧 정상글 차단이므로: ① 비율지표(%·배)만 대상(가격·지수는 시점차 드리프트로 제외)
#   ② 본문에 같은 라벨-단위 수치가 없거나 서로 다른 값이 복수면 판정 보류(fail-open)
#   ③ ±3% 관대 오차. provenance.values 미존재(대부분) 시 leg no-op(무회귀).
_CC_SAFE_UNITS = {"%", "퍼센트", "배"}
_CC_METRIC_KW = re.compile(r"PER|PBR|PSR|ROE|ROA|영업이익률|순이익률|배당|증가율|성장률|점유율|비중|마진")
_CC_NUM = r"-?\d[\d,]*\.?\d*"


def _cc_close(a: float, b: float, rel: float = 0.03, ab: float = 0.5) -> bool:
    return abs(a - b) <= max(abs(b) * rel, ab)


def _cc_image_paths(draft) -> set:
    paths: set = set()
    for b in (draft.get("blocks") or []):
        try:
            data = b[1] if isinstance(b, (list, tuple)) and len(b) >= 2 else None
            if isinstance(data, str) and re.search(r'\.(png|jpe?g|webp|svg)$', data, re.I):
                paths.add(data)
        except Exception:
            pass
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', _draft_body(draft) or ""):
        paths.add(m.group(1))
    return paths


def _cc_body_value(body: str, label: str, unit: str):
    """본문에서 label 뒤(12자 내) unit 붙은 단일 수치. 서로 다른 값 복수면 None(판정 보류)."""
    pat = re.compile(re.escape(label) + r'[^\d\-]{0,12}(' + _CC_NUM + r')\s*' + re.escape(unit))
    vals = set()
    for m in pat.finditer(body or ""):
        v = _pg_to_float(m.group(1))
        if v is not None:
            vals.add(round(v, 4))
    return next(iter(vals)) if len(vals) == 1 else None


def _crosscheck_leg(draft, body) -> list[dict]:
    try:
        from JARVIS06_IMAGE.validators.image_data_verifier import lookup_provenance
    except Exception:
        return []
    out: list[dict] = []
    seen_labels = set()
    for path in _cc_image_paths(draft):
        prov = lookup_provenance(path) or lookup_provenance(os.path.abspath(path))
        if not prov:
            continue
        for row in (prov.get("values") or []):
            label = str(row.get("label", "")).strip()
            unit = str(row.get("unit", "")).strip()
            cv = _pg_to_float(row.get("value"))
            if cv is None or not label or label in seen_labels:
                continue
            # 비율지표만 대상 (가격·지수 원/포인트는 시점차 드리프트 → 제외)
            if unit not in _CC_SAFE_UNITS and not _CC_METRIC_KW.search(label):
                continue
            bv = _cc_body_value(body, label, unit)
            if bv is None:
                continue
            seen_labels.add(label)
            if not _cc_close(bv, cv):
                out.append({"kind": "factuality",
                            "detail": f"[교차대조] '{label}' 본문 {bv}{unit} vs 차트 {cv}{unit} 불일치"})
    if out:
        log.warning(f"[prepublish_gate] 본문↔차트 수치 불일치 {len(out)}건 → 재작성 순환")
    return out


