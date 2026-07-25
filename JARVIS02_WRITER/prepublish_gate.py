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
  PREPUBLISH_SCORE_GATE=0      100점 루브릭 종합 점수 게이트 끔 (70점 미달 재작성 순환)
  GATE_FAIL_CLOSED=0           ★ 판정 불가(LLM 미가용) 시 차단하지 않고 종전처럼 통과

★ 판정 불가 ≠ 통과 (사용자 박제 2026-07-25): LLM 이 판정을 *못 한* 경우
  (회로 open·SDK 실패·빈 응답·형식 오류)와 *판정한 결과 문제 없음* 은 전혀 다른 사건이다.
  종전엔 둘 다 blocked_claims=[] 라 게이트가 검사를 한 번도 안 하고 통과시켰고,
  예외가 없어 로그·GUARDIAN 기록도 남지 않았다(implicit error). 이제
  shared.llm.invoke_text_result 의 ok 로 구분해 ① 항상 GUARDIAN 기록
  ② 사실성은 fail-closed(차단) — 헌법 "사실 판정 LLM 실패=차단".

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


# ★ 판정 불가(LLM 미가용) 정책 — 단일 진입점 (사용자 박제 2026-07-25) ─────────────
#   "판정 불가" 를 "통과" 로 취급하면 게이트는 *한 번도 검사하지 않고* 무성무취로 통과한다
#   (implicit error). 사실성은 fail-closed 가 헌법(CLAUDE_WRITER.md "사실 판정 LLM 실패=차단").
#   ① 단일 진입점: 이 함수 하나가 게이트 fail-closed 정책의 유일한 스위치 —
#      law_enforcer.audit_factuality 도 여기서 파생한다(정책 상수 복제 금지).
#   킬스위치: GATE_FAIL_CLOSED=0 → 종전 동작(판정 불가 = 통과)으로 즉시 복귀.
def gate_fail_closed() -> bool:
    """판정 불가 시 차단할 것인가 (기본 True). GATE_FAIL_CLOSED=0 이면 종전 fail-open."""
    return not _disabled("GATE_FAIL_CLOSED")


# harness 의 *일시적 인프라* 이슈 kind (JARVIS00_INFRA.harness._INFRA_ISSUE_KINDS 계약).
#   이 kind 여야 ① fingerprint 제외 ② 회로 쿨다운 backoff ③ max_attempts 도달 시
#   deferred(=송출 안 함, 다음 회차 재시도) 로 처리된다. 재작성으로 못 고치는 사유이므로
#   factuality/engagement 로 올리면 무의미한 재작성 루프가 된다.
#
# ★ 값을 여기에 박지 않는다 (① 단일 진입점 / ② 동적 설계 — 2026-07-25 정정).
#   소유자는 `JARVIS00_INFRA.harness.INFRA_KIND` 단 하나. 종전엔 harness 가 SSOT 를
#   신설했는데도 이 파일이 "infra_throttle" 리터럴을 *다시 박아* 사본을 만들었다.
#   지금은 값이 같아 동작이 정상이라 precommit 도 못 잡지만, harness 가 kind 를 바꾸면
#   이 사본만 옛 값을 가리켜 harness 의 backoff/defer 계약에서 조용히 이탈한다.
#   *호출 시점* 조회로 파생한다 — 모듈 로드 시점에 캡처하면 그 자체가 또 하나의 사본이다.
def infra_issue_kind() -> str:
    """harness 가 소유한 인프라 이슈 kind 를 *호출 시점* 에 파생한다 (사본 금지).

    폴백 리터럴을 두지 않는다 — 폴백은 곧 사본이고, harness 를 못 읽는 상태면
    harness 재시도 계약 자체가 성립하지 않으므로 조용히 옛 값으로 진행하면 안 된다.

    ★ 절대 raise 하지 않는다 (라이브 안전): 종전엔 모듈 상수라 예외가 날 수 없었다.
      함수 파생으로 바꾸면서 발행 게이트 한복판에 새 예외 경로를 만들면 안 된다 —
      kind 하나 때문에 발행이 막히는 것이 최악이다. harness 를 못 읽으면 ""(미지정)을
      돌려주고 경고만 남긴다. 어차피 kind 의 유일한 소비자가 harness 이므로
      harness 가 없는 상황이면 이 값의 의미 자체가 없다.
    """
    try:
        from JARVIS00_INFRA.harness import INFRA_KIND
        return INFRA_KIND
    except Exception as _e:
        log.warning(f"[prepublish_gate] harness.INFRA_KIND 파생 실패 — kind 미지정으로 진행: {_e}")
        return ""


def _report_judge_unavailable(leg: str, detail: str, post_type: str = "",
                              platform: str = "", kind: str = "") -> None:
    """판정 불가를 GUARDIAN·로그에 남긴다 — 침묵 금지 (통과시키더라도 관측 가능해야 함).

    ★ 이 결함의 본질은 '차단을 안 했다' 가 아니라 '아무도 몰랐다' 였다. 예외가 없으니
      GUARDIAN 기록도 로그도 없었다. 기록은 fail-closed/open 과 무관하게 *항상* 한다.

    ★ context["kind"] 필수 (실측으로 확인): severity.is_transient() 는 *구조화 필드* kind 를
      1순위로 본다. 이걸 빼면 'GateJudgeUnavailable' 이 코드버그로 분류돼 GUARDIAN 이
      Tier-2 LLM 자가수리를 착수한다 — 회로가 열린 것뿐인데 수십 분 LLM 을 태운다.
      kind 는 *실제로 harness 에 올린 kind* 를 그대로 넘긴다(둘 다 NON_CODE_ISSUE_KINDS).
      미지정(기본 "")이면 harness SSOT 에서 호출 시점 파생.
    """
    log.warning(f"[prepublish_gate] ⚠️ 판정 불가({leg}) — {detail} "
                f"[{post_type or '?'}·{platform or '?'}] fail_closed={gate_fail_closed()}")
    if not kind:
        try:
            kind = infra_issue_kind()
        except Exception:            # harness 미가용 — kind 없이라도 기록은 남긴다
            kind = ""
    try:
        from JARVIS07_GUARDIAN.error_collector import report
        # ★ 인자 순서 주의 (2026-07-25 정정): 실제 시그니처는 catch(exc_or_type, source, ...)
        #   이고 report = catch 다. 즉 *첫 인자가 error_type, 둘째가 source*.
        #   종전엔 뒤바뀌어 source='GateJudgeUnavailable', error_type='writer' 로 적재돼
        #   ① 이 기록의 목적(판정 불가 관측)이 무산 ② error_type 차원에 'writer' 오염
        #   ③ _J02_SRCS 매칭 실패로 mark_active("e7") 미발화 였다.
        #   하위호환 역순 교정은 source 가 Exception 일 때만 발동 → 양쪽 str 인 이 호출은 안 잡힌다.
        report("GateJudgeUnavailable", "writer",
               message=f"[{leg}] {detail} ({post_type or '?'}·{platform or '?'})",
               module=__name__, func_name="prepublish_quality_issues",
               context={"kind": kind, "leg": leg, "post_type": post_type,
                        "platform": platform, "fail_closed": gate_fail_closed()})
    except Exception as _e:      # GUARDIAN 실패가 발행을 막지 않도록
        log.debug(f"[prepublish_gate] GUARDIAN 기록 실패(무시): {_e}")


def _draft_body(draft: dict) -> str:
    body = draft.get("full_html") or draft.get("html") or draft.get("content") or ""
    if isinstance(body, dict):
        body = body.get("html") or body.get("content") or ""
    return body or ""


def send_score_report(sr: dict, post_type: str = "", platform: str = "", title: str = "") -> None:
    """★ 발행 전 100점 검증 점수 → 텔레그램 (사용자 박제 2026-07-24: 모든 글·항상).

    ① 단일 진입점 — 점수가 계산되는 유일 지점(prepublish_quality_issues 의 score_post 직후)에서만 호출.
    ② 동적 설계 — sr["sections"]/items dict 를 *순회* 해 렌더. 루브릭(항목·만점·이름)이 바뀌어도
       코드 수정 없이 자동 반영(post_scorer 가 유일한 진실). 항목명·만점을 하드코딩하지 않는다.
    ③ 모든 글 — 경제·테마 × 네이버·티스토리 4조합 모두 이 함수를 부른다(통과·미통과 무관).
    """
    try:
        from shared.notify import send_tg
        from JARVIS02_WRITER.post_scorer import PASS_THRESHOLD as _PASS
        secs = sr.get("sections") or {}
        head = f"📊 *발행 전 품질검증* [{post_type or '?'}·{platform or '?'}]"
        if title:
            head += f" {title}"
        _ok = sr.get("passed")
        lines = [head, "━━━━━━━━━━━━━━━━━━",
                 f"*종합 {sr.get('total', 0):.1f}/100*  "
                 f"{'✅ 통과' if _ok else '❌ 재작성'} (기준 {_PASS:.0f})"]
        # 섹션·항목 동적 순회 (A/B/C/D 순서 보존, dict 순서 그대로)
        for skey, sec in secs.items():
            if not isinstance(sec, dict):
                continue
            lines.append(f"\n*{skey}* {sec.get('total', 0):.1f}/{sec.get('max', 0):.0f}")
            for iv in (sec.get("items") or {}).values():
                if not isinstance(iv, dict):
                    continue
                _sc = float(iv.get("score", 0)); _mx = float(iv.get("max", 0))
                _nm = iv.get("name", "")
                _mk = "✓" if _sc >= _mx else "⚠"
                lines.append(f"  {_mk} {_nm} {_sc:.1f}/{_mx:.0f}")
        send_tg("\n".join(lines))
    except Exception as _e:
        log.warning(f"[prepublish_gate] 점수 리포트 전송 실패(무시): {_e}")


def prepublish_quality_issues(draft, post_type: str = "", platform: str = "",
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

        # ── ★ 판정 불가 처리 (사용자 박제 2026-07-25) ────────────────────────
        #   종전: 회로 open 이면 두 LLM 레그 모두 SDK 미호출 즉시 "" → blocked_claims=[]
        #         → "차단할 게 없다" 로 읽혀 *검사 0회로* 통과. 예외도 로그도 GUARDIAN 기록도 없음.
        #   현재: judge_status 로 구분해 ① 항상 기록(침묵 금지) ② 사실성은 fail-closed.
        _js = cqr.get("judge_status", "ok")
        if _js != "ok":
            if _js == "unavailable":
                # 인프라 미가용 = *재작성으로 못 고침*. harness 의 infra_throttle 계약을 쓴다:
                #   fingerprint 제외 + 회로 쿨다운 backoff 후 재시도 → max_attempts 도달 시
                #   deferred(=송출 안 함, 다음 회차 자연 재시도). 무한 재작성 루프 없음.
                _lab, _kind = ("LLM 미가용(회로 open·SDK 실패·빈 응답)", infra_issue_kind())
                _detail = ("[사실성] 판정 불가 — 사실성·매력도 LLM 미가용"
                           "(일시적, 다음 시도/회차 재개)")
            else:
                # 형식 오류는 재호출로 풀릴 수 있으나 반복되면 지문 동일 → harness abort.
                #   (무한 defer 로 조용히 묻히지 않고 사용자에게 escalation 된다)
                _lab, _kind = ("판정 응답 형식 오류(JSON 아님)", "factuality")
                _detail = "[사실성] 판정 불가 — 판정 응답 형식 오류(검증 미수행)"
            # ③ 모든 글: 경제·테마 × 네이버·티스토리 4조합이 전부 이 한 지점을 지난다.
            _report_judge_unavailable(
                f"fact+engagement/{_js}", _lab, post_type, platform, kind=_kind)
            if _fact_on and gate_fail_closed():
                out.append({"kind": _kind, "detail": _detail})
                log.warning("[prepublish_gate] 🚫 판정 불가 → fail-closed 차단 "
                            "(발행 안 함). 종전 동작 복귀: GATE_FAIL_CLOSED=0")
            else:
                # 매력도만 켜져 있거나 킬스위치 OFF — 통과시키되 위 기록으로 관측 가능.
                log.warning("[prepublish_gate] 판정 불가 → 통과(fail-open) "
                            f"[fact_on={_fact_on} fail_closed={gate_fail_closed()}]")

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

        # ★ 100점 통일 (사용자 박제 2026-07-19): 매력도 5축 *개별 임계(각 70)* veto 폐지 —
        #   이 개별 게이트가 LLM 채점 ±5점 노이즈에 매번 흔들려 괜찮은 글도 재작성시켰다.
        #   품질 판정은 아래 100점 종합(A20+B50+C50+D10) *하나로 통일*. 매력도는 Section A(20점)로
        #   합류해 80점 결정론(헌법·SEO·글종류)에 노이즈가 희석 → 좋은 글이 일관되게 통과한다.
        #   (매력도 점수 자체는 _combined_quality_call 이 계속 산출 → Section A 로 반영.)

    # ── 100점 루브릭 종합 점수 게이트 (PREPUBLISH_SCORE_GATE) — 유일 품질 게이트 ──────
    if not _disabled("PREPUBLISH_SCORE_GATE"):
        _llm_sc = cqr.get("llm_scores") if "cqr" in dir() else None
        if _llm_sc is None:
            # ★ 수동수정 2026-07-16: 통합 LLM 호출(_combined_quality_call)이 실패/스킵되면
            #   llm_scores 가 없다 — 이때 Section A 를 0점 처리하면 실제 콘텐츠 품질과
            #   무관하게 총점이 20점 깎여 재작성 순환에 빠진다(A=0.0/20 반복 사고).
            #   모듈 전체 fail-open 철학과 일치하도록 점수 게이트 자체를 통과시킨다.
            log.warning("[prepublish_gate] 통합 LLM 점수 없음(호출 실패/스킵) → 점수 게이트 통과(fail-open)")
        else:
            try:
                from JARVIS02_WRITER.post_scorer import score_post as _score_fn
                _fi = [x for x in out if x.get("kind") == "factuality"]
                _sr = _score_fn(
                    draft,
                    platform=platform,      # ★ [468] 미전달 시 C 축이 항상 네이버로 폴백
                    post_type=post_type,
                    llm_scores=_llm_sc,
                    factuality_issues=_fi,
                )
                log.info(
                    "[prepublish_gate] 종합 점수 %.1f/100 → %s",
                    _sr["total"], "통과" if _sr["passed"] else "재작성"
                )
                # ★ 모든 글·항상 점수 텔레그램 (사용자 박제 2026-07-24) — 통과·미통과 무관.
                send_score_report(_sr, post_type, platform,
                                  (draft.get("title") or draft.get("keyword") or "").strip())
                if not _sr["passed"]:
                    sec = _sr.get("sections", {})
                    detail_parts = [
                        f"A={sec.get('A',{}).get('total',0):.1f}/20",
                        f"B={sec.get('B',{}).get('total',0):.1f}/50",
                        f"C={sec.get('C',{}).get('total',0):.1f}/20",
                        f"D={sec.get('D',{}).get('total',0):.1f}/10",
                    ]
                    out.append({
                        "kind": "engagement",
                        "detail": f"[품질점수] 종합 {_sr['total']:.1f}/100 (70미달) — {' '.join(detail_parts)}"
                    })
            except Exception as _e:
                log.warning(f"[prepublish_gate] score_post 실패 → 통과(fail-open): {_e}")

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


# ── 매력도·유익성 5축 임계 — 단일 진실 소스 (★ 2026-07-16 상수 승격) ─────────
#   판정 프롬프트·임계 판정·작성 체크리스트(law_enforcer.build_gate_checklist_block)가
#   모두 이 상수에서 파생 — "생성-검증 임계 일치" 원칙 (keyword_frequency_rule 선례).
ENGAGEMENT_THRESHOLDS: dict = {
    "engagement": 70,    # 첫 문단 공감 훅·읽는 재미
    "usefulness": 70,    # 소제목마다 실용 정보
    "title_hook": 60,    # 제목의 구체 수치·질문 훅
    "originality": 60,   # 독창적 관점
    "structure": 65,     # 구조 완결성
}


def _combined_quality_call(body: str, title: str, corpus: str, post_type: str) -> dict:
    """사실성 + 매력도 통합 LLM 1회 판정 (★ 사용자 박제 2026-07-12).

    Returns: {"blocked_claims": [str], "engagement_passed": bool, "failed_dims": [str],
              "llm_scores": dict|None, "judge_status": "ok"|"unavailable"|"malformed"}

    ★ judge_status (2026-07-25 — 이 반환값의 핵심 결함 수정):
        "ok"          판정했다. blocked_claims=[] 는 *차단할 게 없다* 는 뜻.
        "unavailable" 모델이 아예 답을 못 했다(회로 open·SDK 실패·빈 응답).
        "malformed"   답은 왔는데 JSON 이 아니다(판정 형식 실패).
      종전엔 셋 다 `{"blocked_claims": []}` 로 같아서 **"차단할 게 없다" 와 "판정을 못 했다"
      가 반환값에서 구분되지 않았다** — 호출자는 그걸 통과로 읽을 수밖에 없었다.
      판정 자체가 없었던 경우 blocked_claims 는 언제나 [] 이므로 호출자는 반드시
      judge_status 를 먼저 본다.

    ★ llm_scores=None 은 통합 호출 실패/스킵을 의미 — 호출자(SCORE_GATE)는 이 경우
      Section A 를 0점 처리하지 말고 점수 게이트 자체를 fail-open 해야 한다
      (harness RuntimeError [품질점수] A=0.0/20 반복 사고 — prepublish_gate.py 수동수정 2026-07-16).
    """
    import json as _json
    from shared.llm import invoke_text_result as _inv_r

    def _no_verdict(status: str) -> dict:
        return {"blocked_claims": [], "engagement_passed": True, "failed_dims": [],
                "llm_scores": None, "judge_status": status}

    stripped = re.sub(r"<[^>]+>", " ", body or "")[:4000].strip()
    if not stripped:
        # 판정 *대상* 이 없는 것은 판정 불가가 아니다 → ok (차단·기록 대상 아님)
        return _no_verdict("ok")

    corpus_snippet = (corpus or "").strip()[:2000] or "(없음)"
    prompt = (
        f"제목: {title}\n\n[본문]\n{stripped}\n\n[출처]\n{corpus_snippet}\n\n"
        "아래 두 가지를 동시에 판정하라.\n\n"
        "## A. 사실성 — 발행 차단 주장\n"
        "본문에서 *구체적 수치가 포함된 주장* 중 발행하면 안 되는 것만 골라라.\n"
        "차단 기준: (a) 출처 수치와 모순 (b) 구체 수치인데 출처에 근거 전혀 없음\n"
        "★ 차단 제외: 숫자 없는 서술·전망·해석, 상식 수치, 출처에서 추론 가능한 수치\n\n"
        "## B. 매력도·유익성 5축 (0~100 점수 — 임계: "
        + ", ".join(f"{k}≥{v}" for k, v in ENGAGEMENT_THRESHOLDS.items()) + ")\n\n"
        "JSON 하나만 반환(다른 말 금지):\n"
        '{"blocked_claims":["차단 주장 원문 최대5개, 없으면 []"],'
        '"engagement_score":85,"usefulness_score":80,"title_hook_score":70,'
        '"originality_score":75,"structure_score":70,'
        '"failed_dims":["임계 미달 차원 목록, 없으면 []"]}'
    )
    try:
        raw, ok = _inv_r("fact_judge", prompt, max_tokens=600, timeout=90, _nonessential=True)
        if not ok:
            # ★ 인프라 1회 재시도 (2026-07-16) — _nonessential 은 재시도 0회라
            #   타임아웃 1번 = 판정 기회 소멸이었다. 한 번 더 시도 후에만 판정불가 확정.
            #   ★ 2026-07-25: 판정 여부는 ok 로 본다. 종전엔 raw 공백 여부로만 봤는데,
            #     회로 open 이면 두 호출 모두 *SDK 미호출 즉시 ""* 라 재시도가 무의미했고
            #     그 사실이 반환값 어디에도 남지 않았다.
            log.info("[prepublish_gate] 통합 판정 응답 없음 — 1회 재시도")
            raw, ok = _inv_r("fact_judge", prompt, max_tokens=600, timeout=90, _nonessential=True)
        if not ok or not (raw or "").strip():
            return _no_verdict("unavailable")
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return _no_verdict("malformed")
        obj = _json.loads(m.group())
        blocked = [str(x).strip() for x in (obj.get("blocked_claims") or []) if str(x).strip()]
        raw_dims = list(obj.get("failed_dims") or [])
        _int = lambda k, d: int(obj.get(k) or d)
        if not raw_dims:
            for _dim, _thr in ENGAGEMENT_THRESHOLDS.items():
                if _int(f"{_dim}_score", 100) < _thr:
                    raw_dims.append(_dim)
        llm_scores = {
            "engagement_score":  _int("engagement_score",  0),
            "usefulness_score":  _int("usefulness_score",  0),
            "title_hook_score":  _int("title_hook_score",  0),
            "originality_score": _int("originality_score", 0),
            "structure_score":   _int("structure_score",   0),
        }
        return {
            "blocked_claims": blocked,
            "engagement_passed": not raw_dims,
            "failed_dims": raw_dims,
            "llm_scores": llm_scores,
            "judge_status": "ok",
        }
    except Exception as e:
        # 응답은 왔으나 파싱·구조가 깨짐 = 판정 형식 실패(통과 아님).
        log.warning(f"[prepublish_gate] 통합 품질 판정 파싱 실패: {e}")
        return _no_verdict("malformed")


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
            # ★ 2026-07-24 P1: kind="data_insufficient" — 이미지 수치 미검증은 *수집 datasets 부족*이
            #   근본이라 재작성으로 못 고친다(harness 재시도는 collect step 을 건너뛰어 datasets 불변).
            #   fix 훅이 이 kind 를 abort 로 즉시 종결 → 무의미한 2차 시도(플랫폼당 ~15분) 차단.
            #   detail 은 fingerprint 안정 위해 run별 파일명(seed) 금지 — 불변 식별자(출처명, 대개 고정).
            _src = (prov.get("source") or {}) if isinstance(prov, dict) else {}
            _ident = _src.get("name") or _src.get("provider") or "수치차트"
            out.append({"kind": "data_insufficient",
                        "detail": f"[이미지사실성] 출처 미검증 수치 차트 ({_ident})"})
    if out:
        log.warning(f"[prepublish_gate] 이미지 사실성 차단 {len(out)}건 → 데이터부족 abort")
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


