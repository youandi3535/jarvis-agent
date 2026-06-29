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
"""
from __future__ import annotations
import os
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
                              source_docs=None, market_data=None) -> list[dict]:
    """발행 전 품질 게이트 — 사실성 + 매력도. [{"kind","detail"}] 반환 (빈=통과)."""
    body = _draft_body(draft)
    if not body or len(body) < _MIN_BODY:
        return []
    out: list[dict] = []
    if not _disabled("PREPUBLISH_FACT_GATE"):
        out.extend(_factuality_leg(body, post_type, source_docs, market_data))
    if not _disabled("PREPUBLISH_ENGAGEMENT_GATE"):
        out.extend(_engagement_leg(draft, body, post_type))
    return out


def _factuality_leg(body, post_type, source_docs, market_data) -> list[dict]:
    """출처 대조 + 웹 재검증. 게이트 자체 크래시는 발행을 막지 않음(GUARDIAN 박제 후 통과)."""
    try:
        from JARVIS02_WRITER.law_enforcer import factuality_issues
    except Exception as e:
        log.warning(f"[prepublish_gate] factuality import 실패: {e}")
        return []
    try:
        from JARVIS09_COLLECTOR import web_verify as _wv
    except Exception:
        _wv = None
    try:
        res = factuality_issues(body, source_docs=source_docs, post_type=post_type,
                                web_verify_fn=_wv, market_data=market_data)
    except Exception as e:
        log.error(f"[prepublish_gate] 사실성 게이트 예외 → 통과(보고): {e}")
        _report_safe("writer", e, "_factuality_leg")
        return []
    for note in (res.get("policy_notes") or []):
        log.info(f"[prepublish_gate] 정책: {note}")
    out: list[dict] = []
    for b in (res.get("blocked") or []):
        claim = str(b.get("claim", ""))[:120]
        reason = str(b.get("reason", ""))[:80]
        out.append({"kind": "factuality", "detail": f"[사실성] {reason}: {claim}"})
    if out:
        log.warning(f"[prepublish_gate] 사실성 차단 {len(out)}건 → 재작성 순환")
        for o in out:
            log.warning(f"  ↳ {o['detail']}")
    return out


def _engagement_leg(draft, body, post_type) -> list[dict]:
    """유익성·매력도 LLM judge. LLM 실패는 judge 내부에서 fail-open(통과) 처리."""
    try:
        from JARVIS03_RADAR.post_quality_analyzer import judge_engagement
    except Exception as e:
        log.warning(f"[prepublish_gate] engagement import 실패: {e}")
        return []
    title = (draft.get("title") or draft.get("keyword") or "").strip()
    try:
        res = judge_engagement(title=title, content=body, post_type=post_type)
    except Exception as e:
        log.error(f"[prepublish_gate] 매력도 게이트 예외 → 통과(보고): {e}")
        _report_safe("radar", e, "_engagement_leg")
        return []
    if res.get("passed", True):
        return []
    dims = res.get("failed_dims") or []
    tag = ",".join(sorted(dims)) if dims else "전반"
    # detail 에 점수 raw 금지 — 실패 차원 태그만 (fingerprint 안정)
    log.warning(f"[prepublish_gate] 매력도 미달 차원={tag} → 재작성 순환")
    return [{"kind": "engagement", "detail": f"[매력도/유익성] 임계 미달 차원: {tag}"}]


def _report_safe(source: str, exc: Exception, func_name: str) -> None:
    try:
        from JARVIS07_GUARDIAN.error_collector import report as _r
        _r(source, exc, module=__name__, func_name=func_name)
    except Exception:
        pass
