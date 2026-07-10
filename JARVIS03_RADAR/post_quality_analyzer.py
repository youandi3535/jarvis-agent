"""
JARVIS03 — 블로그 품질 분석 엔진
post_analysis 테이블에서 pending_analysis 글을 가져와:
  1. Claude API로 개선 제안(before→after) 생성
  2. DB 저장 (status: analyzed → pending_approval)
  3. 텔레그램 인라인 버튼으로 사용자에게 전달

실행: python post_quality_analyzer.py          (1회 실행)
      python post_quality_analyzer.py --watch   (폴링 데몬)
"""
from __future__ import annotations

import sys
import json
import time
import re
import os
import requests
from pathlib import Path
from datetime import datetime

# ── 경로 설정 ────────────────────────────────────────────────
# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
JARVIS_ROOT = BASE_DIR.parent
sys.path.insert(0, str(JARVIS_ROOT))

from dotenv import load_dotenv
load_dotenv(JARVIS_ROOT / ".env")

from shared import db
from shared.bus import on_post_analyzed

# 자비스01 글자수 정책 — length_manager 단일 진입점
try:
    from JARVIS02_WRITER import length_manager as _LM
except Exception:
    _LM = None

TG_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

PLATFORM_EMOJI = {"naver": "🟢", "tistory": "🟠"}

# ─────────────────────────────────────────────────────────────
# 분석 프롬프트
# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 한국 블로그 SEO·콘텐츠 품질 전문가입니다.
주어진 블로그 글을 분석하고, 개선이 필요한 부분을 JSON 배열로만 반환하세요.
응답은 반드시 순수 JSON 배열이어야 하며 다른 텍스트는 포함하지 마세요.

각 항목 형식:
{
  "type": "title|intro|seo|readability|keyword|cta|structure",
  "field": "한글 항목명 (예: 제목, 도입부, 태그 등)",
  "issue": "문제점 한 줄 설명",
  "before": "개선 전 원문 (__BEFORE_SNIPPET__자 이내로 발췌)",
  "after": "개선 후 제안 (구체적으로)",
  "priority": "high|medium|low"
}

⚠️ **after 필드 절대 규칙** ⚠️
- after 는 **본문에 그대로 들어갈 최종 완성 텍스트만** 작성합니다.
- before 가 있으면 after 는 그 자리에 그대로 치환되어 발행되는 글에 표시됩니다.
- 다음 표현을 절대 포함하지 마세요 (메타 설명·작성 지시문 금지):
  • "~ 제시", "~ 추가", "~ 권장", "~ 보강", "~ 필요"
  • "~등", "또는 ~", "예: ~", "예시: ~"
  • "다음과 같이 ~", "마무리 후 추가:", "글 마지막에 ~"
  • after 안에 큰따옴표나 작은따옴표로 묶인 예시 인용
  • 괄호 안의 작성 지시문 (예: "(주어-술어를 더 간결하게)")
- 잘못된 예 (이런 식으로 쓰면 안 됩니다):
  ❌ "관심 종목의 목표가 재설정 후 지정가 매수 주문을 설정하는 것을 추천합니다" 등 더 구체적인 실행 단계 제시
  ❌ 마무리 후 추가: '이 분석이 도움이 되었다면 댓글 남겨주세요'
  ❌ ③ 미국채 금리 상승(4.42%) → 고PER 성장주 압박 (주어-술어를 더 간결하게)
- 올바른 예:
  ✅ 관심 종목의 목표가를 재설정한 뒤 지정가 매수 주문을 걸어두는 것을 추천합니다.
  ✅ 이 분석이 도움이 되었다면 댓글로 의견을 남겨주세요.
  ✅ ③ 미국채 금리 상승(4.42%)은 고PER 성장주 밸류에이션에 부담입니다.

개선 제안은 3~6개로 제한하고, 우선순위가 높은 것부터 정렬하세요.
실질적이고 즉시 적용 가능한 개선안만 포함하세요.""".replace(
    "__BEFORE_SNIPPET__",
    str(_LM.ECO_BEFORE_SNIPPET) if _LM else "50"
)


def _build_learning_block(post_type: str = "") -> str:
    """daily_review 가 누적한 learning_insights 중 *해당 글 종류* 인사이트만 주입.

    post_type 매칭 규칙:
      - 'economic' 명시 → scope IN ('economic', 'all')
      - 'theme' 명시 → scope IN ('theme', 'all')
      - 빈 문자열 → 전체 (하위 호환)
    인사이트가 0건이면 빈 문자열 반환 (cold start 안전).
    """
    try:
        rows = db.get_top_learning_insights(limit=8, days=14, scope=post_type or "")
    except Exception:
        return ""
    if not rows:
        return ""
    pt_label = {"economic":"경제 브리핑 글", "theme":"테마 글"}.get(post_type, "발행 글")
    lines = [
        "",
        "─" * 30,
        f"📚 *최근 학습된 {pt_label} 작성 지침* (daily_review 누적, 가중치 순):",
        "이 지침은 과거 같은 종류 글에서 발견된 실패/성공 패턴입니다.",
        "다음 분석에서도 같은 문제가 보이면 우선순위 'high' 로 잡아주세요.",
        "",
    ]
    for i, r in enumerate(rows, 1):
        d   = (r.get("directive") or r.get("description") or "").strip()
        occ = r.get("occurrences", 1)
        ew  = r.get("effective_weight", r.get("weight", 1.0))
        sc  = r.get("scope", "all")
        if not d:
            continue
        scope_tag = "" if sc == "all" else f" [{sc}]"
        lines.append(f"{i}.{scope_tag} {d}  (재발견 {occ}회, 가중치 {ew:.2f})")
    return "\n".join(lines)


def analyze_post_quality(platform: str, title: str, content: str,
                          post_type: str = "") -> list:
    """Claude API로 품질 분석 — JSON suggestion list 반환. (공개 인터페이스)

    pre_revise.py 등 외부에서 이 함수를 사용할 것. _analyze_with_claude 는
    내부 호환 alias 로 유지.

    post_type 명시 시 SYSTEM_PROMPT 에 *해당 글 종류 인사이트만* 동적 주입 →
    경제 브리핑 글에는 경제 브리핑 학습만, 테마 글에는 테마 학습만 적용.
    """
    # Claude Code SDK 단일화 — fallback 은 invoke_text 실패 시에만 자동 발동 (아래 try 블록).
    # 본문 앞 length_manager.ANALYZER_INPUT_MAX 자만 분석 (토큰 절약)
    _ana_max = _LM.ANALYZER_INPUT_MAX if _LM else len(content)  # _LM 없으면 자르지 않음
    snippet = content[:_ana_max].strip()
    user_msg = f"""[플랫폼: {platform.upper()}]
제목: {title}

본문:
{snippet}

위 블로그 글의 개선점을 JSON 배열로 분석해주세요."""

    # 동적 SYSTEM_PROMPT — base + 학습된 지침 (post_type 매칭)
    augmented_prompt = SYSTEM_PROMPT + _build_learning_block(post_type)

    try:
        from shared.llm import invoke_text as _inv
        raw = _inv("writer_fast", user_msg, system=augmented_prompt, max_tokens=1500)
        if raw:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
    except Exception as e:
        print(f"  ⚠️ Claude API 오류: {e} — 규칙 기반 분석으로 대체")
        _g_report("radar", e, module=__name__)

    return _rule_based_analysis(title, content)


# 내부 호환 alias — 직접 호출은 analyze_post_quality 사용 권장
_analyze_with_claude = analyze_post_quality


# ─────────────────────────────────────────────────────────────
# 발행 전 매력도·유익성 judge (★ 사용자 박제 2026-06-28)
#   analyze_post_quality 는 *개선 제안* 만 생성(점수 없음) → 발행 전 차단 게이트엔
#   부적합. judge_engagement 는 *점수+통과여부* 를 반환해 prepublish_gate 가
#   재작성 순환 트리거에 사용한다. _build_learning_block·invoke_text 골격 재사용.
# ─────────────────────────────────────────────────────────────

_ENGAGEMENT_MIN = 70  # engagement·usefulness 기본 임계 (0~100)

# ★ 5축 채점 임계 (2026-07-02) — engagement/usefulness 는 70 유지(회귀 0). 신규 3축은
#   주관성이 커 임계를 낮춰(오탐=정상 글 차단 최소화) '뻔한 제목·양산형'만 거른다.
#   dict 순서 = 파싱·failed_dims 정렬 기준. 심사관이 신규 축을 미출력하면 그 축은 채점 제외.
_DIM_THRESHOLDS = {
    "engagement": _ENGAGEMENT_MIN,   # 매력도 70
    "usefulness": _ENGAGEMENT_MIN,   # 유익성 70
    "title_hook": 60,                # 제목 후킹
    "originality": 60,               # 독창성
    "structure": 65,                 # 구성·완결성
}

ENGAGEMENT_SYSTEM_PROMPT = """당신은 한국어 정보 블로그의 *발행 전* 품질 심사관입니다.
독자가 끝까지 읽고 싶어하는지(매력도)·실제로 유익한지(유익성)에 더해, 제목이 클릭을
부르는지·글이 뻔한 양산형이 아닌지·짜임새가 있는지를 0~100으로 채점하세요.

채점 차원(5개):
- engagement(매력도): 도입부 훅, 가독성·흐름, 지루하지 않은 전개, 끝까지 읽고 싶은가
- usefulness(유익성): 정보의 깊이·구체성, 독자가 실제로 얻는 가치, 알맹이가 있는가
- title_hook(제목 후킹): 제목이 궁금증·클릭을 유발하는가, 본문과 부합하는가, 뻔한 상투구가 아닌가
- originality(독창성): 어디서나 보는 양산형 서술이 아니라 고유한 관점·정보가 있는가
- structure(구성): 소제목 흐름이 논리적이고 도입-전개-마무리가 완결되는가

반드시 아래 JSON 객체 *하나만* 출력하세요 (다른 텍스트·코드블록 금지):
{"engagement_score": 0~100, "usefulness_score": 0~100, "title_hook_score": 0~100, "originality_score": 0~100, "structure_score": 0~100, "verdict": "pass" 또는 "revise", "reasons": ["부족한 점 간단히"]}"""


def _judge_unavailable_alert(post_type: str, err: str) -> None:
    """매력도 심사관이 재시도에도 실패해 fail-open 통과할 때 가시화 (조용한 무력화 방지)."""
    msg = f"⚠️ 매력도 심사관(engagement_judge) 재시도 실패 → fail-open 통과 [{post_type}]: {err}"
    print(f"  {msg}")
    try:
        from shared.bus import publish, EventType
        publish(EventType.ERROR_DETECTED, "engagement_judge",
                {"detail": msg, "severity": "medium", "post_type": post_type})
    except Exception:
        pass


def judge_engagement(title: str, content: str, post_type: str = "",
                     platform: str = "") -> dict:
    """발행 전 유익성·매력도 채점 — engagement_judge(Sonnet 5).

    Returns:
        {"passed": bool, "engagement_score": int, "usefulness_score": int,
         "failed_dims": [str], "reasons": [str]}

    ★ fail-open: LLM 호출·파싱 실패 시 passed=True. 매력도는 진실성과 달리
      *재생성 사유* 일 뿐이므로, 심사관 불안정으로 정상 글을 무한 재생성시키지 않는다.
    """
    _max = _LM.ANALYZER_INPUT_MAX if _LM else len(content)
    snippet = re.sub(r"<[^>]+>", " ", content or "")[:_max].strip()
    if not snippet:
        return {"passed": True, "engagement_score": 0, "usefulness_score": 0,
                "failed_dims": [], "reasons": ["빈 본문"]}

    user_msg = f"제목: {title}\n\n본문:\n{snippet}"
    system = ENGAGEMENT_SYSTEM_PROMPT + _build_learning_block(post_type)

    # ★ fail-open 강화 (2026-07-02): 즉시 통과 대신 3회 재시도(★ 사용자 박제
    #   2026-07-06 — 재시도 상한 예외 없이 3회 통일, 기존 2회에서 상향) → 심사관
    #   일시 불안정에 매력도 게이트가 통째로 무력화되는 것 방지. 재시도도 실패하면
    #   통과하되(진실성과 달리 재생성 사유일 뿐) '심사 불가'를 가시화(점수 -1 = 미채점,
    #   GUARDIAN·버스 경고).
    from shared.llm import invoke_text as _inv
    obj = None
    last_err = ""
    for _attempt in range(3):
        try:
            # ★ 비필수 (ERRORS [368]): 매력도는 fail-open(폴백=통과)이므로 스로틀 시 즉시 폴백
            #   — 발행 임계경로를 매력도 LLM 대기로 막지 않는다(재생성 사유일 뿐).
            raw = _inv("engagement_judge", user_msg, system=system, max_tokens=600,
                       timeout=45, _nonessential=True)
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            if not m:
                last_err = "판정 응답 없음"
                continue
            obj = json.loads(m.group())
            break
        except Exception as e:
            last_err = str(e)
            _g_report("radar", e, module=__name__)

    if obj is None:
        _judge_unavailable_alert(post_type, last_err)   # 조용한 무력화 방지
        return {"passed": True, "engagement_score": -1, "usefulness_score": -1,
                "failed_dims": [], "reasons": [f"심사 불가(재시도 실패): {last_err}"]}

    # ★ 5축 파싱 (2026-07-02) — _DIM_THRESHOLDS 순서대로 <dim>_score 추출.
    #   심사관이 신규 3축(title_hook·originality·structure)을 누락하면 그 축은 채점 제외
    #   (오탐=정상 글 차단 방지). engagement/usefulness 누락·파싱실패는 fail-open(기존 동작).
    scores: dict[str, int] = {}
    for _dim in _DIM_THRESHOLDS:
        _raw = obj.get(f"{_dim}_score", None)
        if _raw is None:
            continue
        try:
            scores[_dim] = int(_raw)
        except (TypeError, ValueError):
            continue
    if "engagement" not in scores or "usefulness" not in scores:
        _judge_unavailable_alert(post_type, "핵심 점수(engagement/usefulness) 파싱 실패")
        return {"passed": True, "engagement_score": -1, "usefulness_score": -1,
                "failed_dims": [], "reasons": ["점수 파싱 실패 — fail-open 통과"]}

    # failed_dims: dict 순서 고정 → fingerprint 안정 (detail 에 점수 raw 안 넣음)
    failed = [d for d in _DIM_THRESHOLDS if d in scores and scores[d] < _DIM_THRESHOLDS[d]]
    result = {"passed": not failed,
              "engagement_score": scores["engagement"],
              "usefulness_score": scores["usefulness"],
              "failed_dims": failed, "reasons": list(obj.get("reasons") or [])}
    for _d in ("title_hook", "originality", "structure"):
        if _d in scores:
            result[f"{_d}_score"] = scores[_d]   # 가시화·학습용(하위 소비자는 passed·failed_dims만)
    return result


def _pick_cta(platform: str) -> str:
    """동적으로 CTA 생성 — LLM이 매번 새로운 문구 창작 (제1-B조)."""
    try:
        from shared.llm import invoke_text as _inv_cta

        platform_context = {
            "naver": "네이버 블로그 (이웃신청·공감 기반)",
            "tistory": "티스토리 블로그 (공감·댓글 기반)",
        }.get(platform, "일반 블로그")

        prompt = f"""'{platform_context}'에 맞는 독창적인 CTA(행동 유도) 문구를 1개 창작하세요.

요구사항:
- 정확히 1개 문구만 (1문장, 평문)
- 그 플랫폼에 맞는 상호작용 방식 반영
- 진정성 있고 자연스러운 톤
- 과도한 존댓말 또는 경어 쓰지 말 것
- 마크다운·기호·개행 없음 (평문만)

응답: 문구만 (설명 없음)"""

        cta = _inv_cta("writer_fast", prompt, temperature=0.9, max_tokens=60)
        return cta.strip() if cta else f"{platform_context}의 공감과 참여를 부탁드립니다."
    except Exception as e:
        log = __import__("logging").getLogger("jarvis")
        log.warning(f"[CTA] 동적 생성 실패: {e}, 폴백 사용")
        fallback = {
            "naver": "도움이 되셨다면 이웃신청과 공감 부탁드려요",
            "tistory": "유익하셨다면 공감 버튼 한 번 눌러주세요.",
        }
        return fallback.get(platform, "도움이 되셨다면 공감 부탁드려요")


def _rule_based_analysis(title: str, content: str, platform: str = "") -> list:
    """Claude API 없을 때 규칙 기반 분석 폴백."""
    suggestions = []
    # 제목 길이 — length_manager 단일 진입점
    _title_min = _LM.TITLE_MIN_RECOMMEND if _LM else 0
    _title_max = _LM.TITLE_MAX_RECOMMEND if _LM else 10**9
    if len(title) < _title_min:
        suggestions.append({
            "type": "title", "field": "제목",
            "issue": "제목이 너무 짧아 검색 노출 및 클릭률 저하 우려",
            "before": title,
            "after": f"{title} — 투자자가 꼭 알아야 할 핵심 포인트 3가지",
            "priority": "high",
        })
    elif len(title) > _title_max:
        suggestions.append({
            "type": "title", "field": "제목",
            "issue": "제목이 너무 길어 검색 결과에서 잘릴 수 있음",
            "before": title[:60] + "...",
            "after": title[:50],
            "priority": "medium",
        })

    # 숫자 포함 여부
    if not re.search(r'\d', title):
        suggestions.append({
            "type": "title", "field": "제목 숫자",
            "issue": "제목에 숫자가 없어 클릭률이 낮을 수 있음",
            "before": title,
            "after": f"{title.rstrip()} 2026",
            "priority": "medium",
        })

    # 본문 길이 — 모듈 레벨 _LM 사용 (함수 내 재선언 시 UnboundLocalError 발생 — ERRORS.md 참조)
    if _LM and len(content) < _LM.MIN_VALID * 0.67:  # 정책 하한의 67% 미만 = 너무 짧음
        suggestions.append({
            "type": "structure", "field": "본문 길이",
            "issue": "본문이 너무 짧아 검색 엔진 평가 불리",
            "before": f"현재 {len(content)}자",
            "after": f"최소 {_LM.TARGET_LOW:,}자 이상 권장. 추가 분석·사례·전망 섹션 보강 필요",
            "priority": "high",
        })

    # CTA 없음
    if "이웃" not in content and "댓글" not in content and "공감" not in content:
        suggestions.append({
            "type": "cta", "field": "행동 유도 문구",
            "issue": "독자 참여 유도 문구(CTA)가 없음",
            "before": "(CTA 없음)",
            "after": _pick_cta(platform),
            "priority": "low",
        })

    # 소제목 없음
    if content.count('\n\n') < 3:
        suggestions.append({
            "type": "readability", "field": "가독성",
            "issue": "단락 구분이 부족해 가독성 저하",
            "before": "(단락 구분 부족)",
            "after": "각 섹션마다 소제목(H2/H3) 추가 및 3~4문장마다 단락 나눔",
            "priority": "medium",
        })

    return suggestions[:6]


# ─────────────────────────────────────────────────────────────
# 텔레그램 전송
# ─────────────────────────────────────────────────────────────

def _build_partial_keyboard(analysis_id: int, suggestions: list, selected: list) -> dict:
    """제안별 토글 버튼 + 하단 액션 행. approval_bot 토글 콜백에서도 재사용."""
    sel_set   = set(selected)
    n_shown   = min(len(suggestions), 6)
    rows      = []
    NUM_EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
    for i in range(n_shown):
        s     = suggestions[i]
        mark  = "✅" if i in sel_set else "⬜"
        field = (s.get("field") or "?")[:14]
        rows.append([{
            "text": f"{NUM_EMOJI[i]} {mark} {field}",
            "callback_data": f"tog:{analysis_id}:{i}",
        }])
    # 하단 액션 행
    rows.append([
        {"text": f"✏️ 적용 ({len(sel_set)}/{n_shown})", "callback_data": f"apply:{analysis_id}"},
        {"text": "❌ 모두 거부",                          "callback_data": f"reject:{analysis_id}"},
    ])
    return {"inline_keyboard": rows}


def _send_telegram_analysis(record: dict, suggestions: list):
    """분석 결과를 텔레그램 인라인 버튼과 함께 전송."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("  ⚠️ TELEGRAM_TOKEN 또는 CHAT_ID 없음 — 텔레그램 전송 건너뜀")
        return

    analysis_id = record["id"]
    platform    = record["platform"]
    theme       = record["theme"]
    title       = record["title"] or theme
    url         = record.get("url", "")
    emoji       = PLATFORM_EMOJI.get(platform, "📝")

    # 메시지 본문 구성
    high   = [s for s in suggestions if s.get("priority") == "high"]
    medium = [s for s in suggestions if s.get("priority") == "medium"]
    low    = [s for s in suggestions if s.get("priority") == "low"]

    lines = [
        f"✍️ *블로그 품질 분석 완료*",
        f"{emoji} *{platform.upper()}* | {theme}",
        f"📝 {title}",
        f"🔗 {url}" if url else "",
        "",
        f"📊 개선 제안 *{len(suggestions)}개* (🔴고{len(high)} 🟡중{len(medium)} 🟢저{len(low)})",
        "─" * 20,
    ]

    for i, s in enumerate(suggestions[:6], 1):
        p_icon = "🔴" if s.get("priority") == "high" else ("🟡" if s.get("priority") == "medium" else "🟢")
        lines.append(f"{p_icon} *{s.get('field','?')}*: {s.get('issue','')}")
        lines.append(f"   Before: `{s.get('before','')[:60]}`")
        lines.append(f"   After: `{s.get('after','')[:80]}`")

    lines += [
        "",
        "각 제안을 ✅/❌ 토글로 선택 후 [✏️ 적용].",
        "1시간 무응답 시 현재 선택 상태로 자동 적용됨.",
    ]

    text = "\n".join(l for l in lines if l is not None)

    # 초기 선택 상태: 전부 선택 (기본 = 전체 승인 동작)
    n_shown = min(len(suggestions), 6)
    db.set_partial_selection(analysis_id, list(range(n_shown)))

    # 인라인 키보드 — 제안별 토글 + 하단 액션 행
    keyboard = _build_partial_keyboard(analysis_id, suggestions, list(range(n_shown)))

    try:
        from shared.notify import send_tg_with_buttons
        send_tg_with_buttons(text, keyboard["inline_keyboard"])
        print(f"  ✅ 텔레그램 전송 완료 (analysis_id={analysis_id})")
    except Exception as e:
        print(f"  ⚠️ 텔레그램 전송 오류: {e}")
        _g_report("radar", e, module=__name__)


# ─────────────────────────────────────────────────────────────
# 메인 처리 루프
# ─────────────────────────────────────────────────────────────

def run_once():
    """pending_analysis 글을 가져와 분석 1회 수행."""
    pending = db.get_pending_analysis(limit=5)
    if not pending:
        print("분석 대기 글 없음.")
        return 0

    processed = 0
    for record in pending:
        aid      = record["id"]
        platform = record["platform"]
        theme    = record["theme"]
        title    = record.get("title") or theme
        content  = record.get("original_content") or record.get("original_html") or ""
        post_type = record.get("post_type") or ""  # P1 패치 (2026-05-04): scope 매칭

        print(f"\n🔍 분석 중: [{platform}] {title} (id={aid})")

        if not db.try_claim_analysis(aid):
            print(f"  [건너뜀] 이미 다른 프로세스가 처리 중")
            continue

        # 분석 — post_type 으로 학습 인사이트 scope 매칭
        suggestions = analyze_post_quality(platform, title, content, post_type=post_type)
        print(f"  → 제안 {len(suggestions)}개 생성")

        # DB 저장
        db.save_analysis_result(aid, suggestions)

        # 텔레그램 전송
        _send_telegram_analysis(record, suggestions)

        # 승인 대기 상태로
        db.set_analysis_pending_approval(aid)

        # 이벤트 발행
        on_post_analyzed(aid, platform, theme, len(suggestions))

        processed += 1

    return processed


def run_single(analysis_id: int) -> bool:
    """특정 analysis_id 1개만 즉시 분석 — 발행 직후 이벤트 트리거용."""
    record = db.get_analysis_by_id(analysis_id)
    if not record:
        print(f"[분석기] ID {analysis_id} 레코드 없음")
        return False
    if record.get("status") != "pending_analysis":
        print(f"[분석기] ID {analysis_id} 상태={record.get('status')} — 건너뜀")
        return False

    # 원자적 선점 — 즉시트리거(jarvis_main)와 fallback(daemon) 동시 실행 시 중복 전송 방지
    if not db.try_claim_analysis(analysis_id):
        print(f"[분석기] ID {analysis_id} 이미 다른 프로세스가 처리 중 — 건너뜀")
        return False

    platform = record["platform"]
    theme    = record["theme"]
    title    = record.get("title") or theme
    content  = record.get("original_content") or record.get("original_html") or ""
    post_type = record.get("post_type") or ""  # P1 패치 (2026-05-04): scope 매칭

    print(f"\n🔍 즉시 분석: [{platform}] {title} (id={analysis_id})")
    suggestions = _analyze_with_claude(platform, title, content, post_type=post_type)
    print(f"  → 제안 {len(suggestions)}개 생성")

    db.save_analysis_result(analysis_id, suggestions)
    _send_telegram_analysis(record, suggestions)
    db.set_analysis_pending_approval(analysis_id)
    on_post_analyzed(analysis_id, platform, theme, len(suggestions))
    return True


def run_watch(interval: int = 300):
    """폴링 데몬 — 이벤트 트리거 실패 시 누락 항목 보정 fallback."""
    print(f"📡 품질 분석 fallback 데몬 시작 (폴링 간격: {interval}s)")
    while True:
        try:
            n = run_once()
            if n:
                print(f"  fallback 처리: {n}개")
        except Exception as e:
            print(f"⚠️ 분석 루프 오류: {e}")
            _g_report("radar", e, module=__name__)
        time.sleep(interval)


if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    # python post_quality_analyzer.py <id>    → 특정 글 즉시 분석 (이벤트 트리거)
    # python post_quality_analyzer.py --watch → fallback 폴링 데몬
    # python post_quality_analyzer.py         → 미처리 pending 1회 전체 처리
    args = sys.argv[1:]
    if "--watch" in args:
        # ★ 무한 폴링 데몬 — 전체 루프는 감싸지 않음 (상주 데몬). 폴 1건 단위 가드는 run_watch 내부 몫.
        run_watch(interval=int(os.getenv("ANALYZER_POLL_SEC", "300")))
    else:
        # ★ 정지 방어: fire-and-forget 자식이라 자체 가드 필수 — 일회성 작업(run_single/run_once)만 감쌈.
        from JARVIS00_INFRA.watchdog import guard_main
        with guard_main("품질 분석", deadline_sec=900):
            aid = next((a for a in args if a.isdigit()), None)
            if aid:
                ok = run_single(int(aid))
                print(f"\n✅ 분석 {'완료' if ok else '건너뜀'}: id={aid}")
            else:
                n = run_once()
                print(f"\n✅ 분석 완료: {n}개")
