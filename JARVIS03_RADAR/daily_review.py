"""
JARVIS03 — 일일 종합 분석 (Daily Review)
────────────────────────────────────────────────────
매일 22:00 데몬이 호출. 그날 발행된 모든 블로그 글(3 플랫폼) 을 한꺼번에 분석해:
  1) 조회수·인기도·품질 종합 집계
  2) Claude 가 묶음 분석으로 *오늘의 개선 인사이트* 도출
  3) daily_review 테이블에 결과 저장
  4) learning_insights 누적 → pre_revise 가 다음날 글 작성 시 자동 참조
  5) 텔레그램 일일 리포트 전송

설계 원칙:
- 글 1개씩 분석하는 post_quality_analyzer 와 다르게, *하루 단위 묶음 분석*.
  같은 실수가 3 플랫폼에 반복되면 "오늘의 패턴" 으로 가중치 +0.5 부여.
- 인사이트는 누적 (learning_insights 테이블, insight_key UNIQUE).
  같은 패턴 재발견 시 occurrences+1, weight 강화 (상한 5.0).
- 30일 미접촉 인사이트는 train_weights 잡과 함께 weight*0.5 감쇠.
- Claude API 실패 시 *집계 통계만* 으로 fallback (인사이트 없이 진행 — 데몬 정지 안 함).

실행:
  python JARVIS03_RADAR/daily_review.py          # 오늘
  python JARVIS03_RADAR/daily_review.py 2026-04-30  # 특정 날짜 재실행
"""
from __future__ import annotations

import sys
import os
import json
import re
import requests
from pathlib import Path
from datetime import datetime
from collections import Counter

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
JARVIS_ROOT = BASE_DIR.parent
sys.path.insert(0, str(JARVIS_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(JARVIS_ROOT / ".env")
except Exception:
    pass

from shared import db
from shared.bus import publish, EventType

# 자비스01 글자수 정책 — length_manager 단일 진입점에서 동적 참조
try:
    from JARVIS02_WRITER import length_manager as _LM
except Exception:
    _LM = None

TG_TOKEN      = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "")

# 모델은 shared/llm.py "analyzer" alias(claude-sonnet-4-6)로 중앙 관리
# CLAUDE_URL / CLAUDE_MODEL 직접 호출 → invoke_text("analyzer") 로 교체됨

PLATFORM_EMOJI = {"naver": "🟢", "tistory": "🟠"}


# ─────────────────────────────────────────────────────────────
# 1. 집계 — Claude 호출 없이 가능한 통계
# ─────────────────────────────────────────────────────────────

def _aggregate(posts: list[dict]) -> dict:
    """post_analysis 행 리스트 → 집계 메트릭."""
    n = len(posts)
    if n == 0:
        return {"posts_count": 0, "platforms": {}, "avg_views": 0.0,
                "top_views": 0, "quality_score": 0.0,
                "sectors": {}, "issue_types": Counter()}

    plats = Counter(p.get("platform", "?") for p in posts)
    views = [int(p.get("current_views") or 0) for p in posts]
    avg_v = sum(views) / n if n else 0
    top_v = max(views) if views else 0

    # quality_score: pre_revise 적용 비율 + suggestions 적음 + 본문 길이 + 조회수
    # (사슬 1 패치 2026-05-02: pre_applied=1, suggestions=0 만으로 100점 인플레이션 방지)
    pre_applied = sum(1 for p in posts if (p.get("revision_patch") or "{}") != "{}")
    pre_ratio   = pre_applied / n
    sugg_counts = []
    issue_types: Counter = Counter()
    sectors: Counter = Counter()
    body_lens   = []   # 본문 길이 분포
    for p in posts:
        try:
            sugg = json.loads(p.get("suggestions") or "[]")
            sugg_counts.append(len(sugg))
            for s in sugg:
                t = (s.get("type") or "").strip()
                if t:
                    issue_types[t] += 1
        except Exception:
            sugg_counts.append(0)
        # 본문 길이 (original_content 우선, 없으면 html 태그 제거)
        body = (p.get("original_content") or "").strip()
        if not body:
            body = _strip_html(p.get("original_html") or "")
        body_lens.append(len(body))
        # source_keyword 가 있으면 그 키워드의 sector 추출
        sk = (p.get("source_keyword") or "").strip()
        if sk:
            try:
                with db.get_db() as conn:
                    r = conn.execute(
                        "SELECT sector FROM trends WHERE keyword=? ORDER BY date DESC LIMIT 1",
                        (sk,),
                    ).fetchone()
                    if r and r["sector"]:
                        sectors[r["sector"]] += 1
            except Exception:
                pass

    avg_sugg = sum(sugg_counts) / n if n else 0
    avg_len  = sum(body_lens) / n if n else 0

    # quality_score 4축 가중평균 (각 0~100, 가중치 합 1.0)
    # ① pre_revise 적용률 (30%): pre_revise 가 작동했는가
    # ② 사후 분석 청결도 (10%): suggestions 적을수록 좋음 (pre_revise 적용 글은 0이 정상이라 가중치 작음)
    # ③ 본문 길이 적정 (20%): 자비스01 length_manager 정책 범위 내 = full, 그 외 감점
    # ④ 평균 조회수 시그널 (40%): 조회수 200+ = full, 0 = 0
    score_pre   = pre_ratio * 100
    score_sugg  = max(0.0, (5 - min(avg_sugg, 5)) / 5.0) * 100  # 0건=100, 5건+=0
    # 길이 점수 — 자비스01 length_manager 단일 진입점 (정책 누수 방지).
    # length_manager 미가용 시 길이 점수 자체를 skip (다른 파일에 fallback 한도값 박지 않음).
    try:
        from JARVIS02_WRITER import length_manager as _LM
        _len_lo, _len_hi = _LM.MIN_VALID, int(_LM.MAX_KOREAN * 1.4)
        if _len_lo <= avg_len <= _len_hi:
            score_len = 100
        elif avg_len < _len_lo:
            score_len = max(0, avg_len / _len_lo * 100)  # 0자=0, 정책 하한=100
        else:
            score_len = max(0, 100 - (avg_len - _len_hi) / 50.0)  # 상한 초과 α마다 감점
    except Exception:
        score_len = 100  # length_manager 미가용 → 길이 평가 skip (full 부여)
    avg_views = sum(int(p.get("current_views") or 0) for p in posts) / n if n else 0
    score_views = min(100.0, avg_views / 200.0 * 100)  # 200+ = full

    q = (score_pre   * 0.30 +
         score_sugg  * 0.10 +
         score_len   * 0.20 +
         score_views * 0.40)

    return {
        "posts_count": n,
        "platforms": dict(plats),
        "avg_views": round(avg_v, 1),
        "top_views": top_v,
        "quality_score": round(q, 1),
        "sectors": dict(sectors),
        "issue_types": issue_types,
        "pre_applied_ratio": round(pre_ratio, 2),
        "avg_suggestions": round(avg_sugg, 1),
    }


# ─────────────────────────────────────────────────────────────
# 2. Claude 묶음 분석 — 오늘의 인사이트 추출
# ─────────────────────────────────────────────────────────────

REVIEW_SYSTEM_PROMPT = """당신은 한국 블로그 운영 전문가입니다.
하루 동안 발행된 여러 글(네이버/티스토리)과 그 분석 결과를 받아,
*다음날 글 작성에 즉시 적용할 수 있는 개선 지침* 을 도출하세요.

[입력 자료의 두 가지 모드]
A) suggestions 가 있는 글: 사후 분석으로 도출된 이슈 목록을 우선 참고.
B) suggestions 가 비어있고 본문 발췌가 있는 글: 발췌(도입 + 마무리) 를 직접 읽고
   패턴을 발견하세요. 같은 글이 사전 수정(pre_revise)으로 다듬어진 경우입니다.
   이런 경우에도 *반복되는 표현·구조·길이·톤·SEO 약점* 을 능동적으로 찾아 인사이트로 만드세요.

응답은 반드시 순수 JSON 배열이어야 하며 다른 텍스트는 포함하지 마세요.

각 항목 형식:
{
  "key": "고유 식별자 (재발견 시 같은 키여야 누적 학습됨, 영문/숫자 __INSIGHT_KEY_MAX__자 이내)",
  "type": "avoid|prefer|topic_boost|platform_specific",
  "description": "한 줄 설명 (한국어)",
  "directive": "다음 글 작성 시 적용할 구체 지침 (한국어, 액션 동사로 시작)",
  "weight": 1.0
}

⚠️ 작성 규칙
- 인사이트는 1~5개. *발췌 모드(B)에서도 빈 배열 반환은 가능한 피하고* 본문에서 패턴 1~2개는 도출.
- "key" 는 동일 패턴 재발견 시 누적되도록 짧고 안정적인 식별자 사용
  (예: "intro_too_long", "tistory_tag_overuse", "fomc_oversimplified").
- "directive" 는 *다음 글 작성 AI* 가 읽고 바로 따라할 수 있게 구체적으로.
  ❌ "도입부 개선"
  ✅ "도입부는 첫 2문장 안에 본론 핵심 수치를 제시하고 사담은 제외한다"
- 같은 type/key 가 3건 이상에서 반복되면 weight 1.5 부여.
- 한 플랫폼에만 해당하는 패턴이면 type='platform_specific' + key 에 플랫폼 prefix.

⚠️ 발췌 모드(B)에서 우선 점검할 패턴 예시
- 도입부가 첫 본론까지 너무 김 (3문장 이상 사담)
- 같은 표현·연결어 과다 반복 ("그래서", "따라서", "특히" 등)
- 마무리가 형식적·식상 ("정리하자면", "마치며")
- 숫자/출처 결핍 또는 과다
- 단락 길이 불균형 (한 단락 5줄 초과 등)
- 제목과 본문 첫 문단이 의미적으로 동떨어짐

⚠️ 절대 금지
- 메타 설명 ("~ 제시", "~ 추가", "~ 등") 형태로 directive 작성 금지.
- 오늘 글이 0건이면 빈 배열 [] 만 반환.
""".replace("__INSIGHT_KEY_MAX__", str(_LM.INSIGHT_KEY_MAX) if _LM else "30")


# ─────────────────────────────────────────────────────────────
# 본문 발췌 — pre_revise 적용 글도 Claude 가 직접 분석할 수 있게
# (사슬 1 패치 2026-05-02: suggestions 비어있어도 학습 데이터 생성)
# ─────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE  = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """HTML 태그 제거 + 공백 정규화."""
    if not html:
        return ""
    txt = _TAG_RE.sub(" ", html)
    txt = _WS_RE.sub(" ", txt).strip()
    return txt


def _build_excerpt(post: dict, head_chars: int = 800, tail_chars: int = 400) -> str:
    """글 본문에서 도입부 + 마무리 발췌. Claude 토큰 절약 위해 잘라냄.

    길1-A 패치 (2026-05-04): original_content 가 HTML 인 경우도 무조건 sanitize.
    이전에는 plain 으로 가정해 그대로 넘겼는데, 실제로는 theme 글의 content 에
    HTML 태그가 박혀있어 daily_review 가 "HTML 박힘" 을 학습 가치로 잘못 분류함.
    """
    raw = (post.get("original_content") or "").strip()
    if not raw:
        raw = (post.get("original_html") or "").strip()
    if not raw:
        return ""
    # HTML 태그 흔적이 있으면 무조건 sanitize (plain 텍스트도 손해 0)
    if "<" in raw and ">" in raw:
        content = _strip_html(raw)
    else:
        content = _WS_RE.sub(" ", raw).strip()
    if not content:
        return ""
    if len(content) <= head_chars + tail_chars:
        return content
    head = content[:head_chars].rstrip()
    tail = content[-tail_chars:].lstrip()
    return f"{head}\n\n…(중략 {len(content) - head_chars - tail_chars}자)…\n\n{tail}"


def _count_overflow_events(date_str: str) -> dict:
    """그날 post_overflow_compressed 이벤트 집계 — 학습 가이드 주입용."""
    out = {"total": 0, "claude_summary": 0, "hard_cut": 0, "samples": []}
    try:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT payload FROM events "
                "WHERE event_type='post_overflow_compressed' "
                "  AND date(created_at) = ? "
                "ORDER BY id DESC LIMIT 50",
                (date_str,)
            ).fetchall()
        for r in rows:
            try:
                p = json.loads(r["payload"] or "{}")
            except Exception:
                continue
            out["total"] += 1
            method = p.get("method", "")
            if method == "claude_summary":
                out["claude_summary"] += 1
            elif method == "hard_cut_fallback":
                out["hard_cut"] += 1
            if len(out["samples"]) < 3:
                out["samples"].append({
                    "ctx": p.get("context", ""),
                    "orig": p.get("original_korean", 0),
                    "comp": p.get("compressed_korean", 0),
                })
    except Exception:
        pass
    return out


def _build_claude_input(date_str: str, posts: list[dict], agg: dict,
                         post_type: str = "all") -> str:
    """Claude 에 보낼 사용자 메시지 — 오늘의 글 요약 + 집계 + 글 종류 명시."""
    pt_label = {"economic":"경제 브리핑 글", "theme":"테마 글",
                "manual":"수동 발행 글", "unknown":"분류 미지정 글"}.get(post_type, post_type)
    lines = [f"# 일일 발행 요약 ({date_str}) — {pt_label} 그룹\n"]
    lines.append(f"- 총 발행: {agg['posts_count']}건")
    lines.append(f"- 플랫폼별: {agg['platforms']}")
    lines.append(f"- 평균 조회수: {agg['avg_views']} (최고 {agg['top_views']})")
    lines.append(f"- 품질 점수: {agg['quality_score']}/100")
    lines.append(f"- 사전 수정 적용률: {agg['pre_applied_ratio']*100:.0f}%")
    lines.append(f"- 글당 평균 개선 제안 수: {agg['avg_suggestions']}")
    lines.append(f"- 자주 등장한 이슈 타입: {dict(agg['issue_types'].most_common(5))}")
    lines.append(f"- 섹터 분포: {agg['sectors']}")

    # 길이 초과 압축 발생 통계 — 학습 가이드 주입.
    # 한도값은 자비스01 length_manager 단일 진입점에서 동적으로 가져옴 (정책 누수 방지).
    ovf = _count_overflow_events(date_str)
    if ovf["total"] > 0:
        lines.append(f"- ⚠️ 길이 초과 압축: {ovf['total']}건 "
                     f"(자연 압축 {ovf['claude_summary']}, 강제절단 {ovf['hard_cut']})")
        if ovf["samples"]:
            for s in ovf["samples"]:
                lines.append(f"    · {s['ctx']}: 원본 {s['orig']}자 → {s['comp']}자")
        # 자비스01 길이 정책을 학습 prompt 에 그대로 주입 (도메인 결합 비용 < 정책 일관성)
        try:
            from JARVIS02_WRITER import length_manager as _LM
            _length_phrase = _LM.build_short_length_phrase()
        except Exception:
            _length_phrase = "정책 한도"
        lines.append(
            f"  ⚠️ 작성 단계에서 {_length_phrase} 한도를 *처음부터* 지켜야 함. "
            "압축 발생 = 작성 가이드 부족. 더 간결한 작성 인사이트를 도출하세요."
        )
    lines.append("")

    lines.append("# 글 별 분석 결과\n")
    # 토큰 절약: posts 가 많으면 일부만 본문 발췌 (앞 6건까지)
    EXCERPT_LIMIT = 6
    for idx, p in enumerate(posts):
        title = (p.get("title") or "")[:80]
        plat  = p.get("platform", "?")
        views = int(p.get("current_views") or 0)
        try:
            sugg = json.loads(p.get("suggestions") or "[]")
        except Exception:
            sugg = []
        lines.append(f"\n## [{plat}] {title}  (조회수 {views})")

        if sugg:
            # 모드 A: suggestions 인용
            for s in sugg[:6]:
                issue = (s.get("issue") or "")[:80]
                sty   = (s.get("type") or "").strip()
                pri   = s.get("priority", "low")
                lines.append(f"  - [{sty}/{pri}] {issue}")
        elif idx < EXCERPT_LIMIT:
            # 모드 B: 본문 발췌 (suggestions 없는 글 = pre_revise 적용 글)
            excerpt = _build_excerpt(p)
            if excerpt:
                lines.append(f"  *suggestions 없음 — 본문 발췌(도입+마무리) 직접 분석:*")
                lines.append(f"  ```")
                # 들여쓰기로 코드 블록 모양 (Claude 가 인용으로 인식)
                for line in excerpt.split("\n"):
                    lines.append(f"  {line}")
                lines.append(f"  ```")
            else:
                lines.append("  (suggestions 없음, 본문도 비어있어 패턴 도출 불가)")
        else:
            lines.append("  (suggestions 없음 — 토큰 절약 위해 발췌 생략)")

    lines.append("\n# 임무")
    lines.append("위 자료를 종합해 *내일 글 작성 시 즉시 적용할 개선 지침* 을 JSON 배열로만 반환.")
    lines.append("발췌 모드(B) 글이 다수면 본문에서 직접 패턴(도입부 길이·반복 표현·마무리 식상함 등)을 능동적으로 도출하세요.")
    return "\n".join(lines)


def _call_claude(user_msg: str, max_tokens: int = 1500) -> list:
    """Claude 호출 — JSON 배열 반환. 실패 시 빈 리스트 + stderr.
    shared/llm.py invoke_text("analyzer") 로 중앙화 — claude-sonnet-4-6.
    """
    try:
        from shared.llm import invoke_text as _inv
        text = _inv("analyzer", user_msg, system=REVIEW_SYSTEM_PROMPT,
                    max_tokens=max_tokens)
        if not text:
            return []
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
        m = re.search(r"\[[\s\S]*\]", text)
        if not m:
            print(f"  ⚠️ Claude 응답에서 JSON 배열 찾지 못함: {text[:200]}", file=sys.stderr)
            return []
        return json.loads(m.group(0))
    except Exception as e:
        print(f"  ❌ Claude 호출 실패: {e}", file=sys.stderr)
        _g_report("radar", e, module=__name__)
        return []


# ─────────────────────────────────────────────────────────────
# 3. 학습 인사이트 누적 + 텔레그램 리포트
# ─────────────────────────────────────────────────────────────

def _persist_insights(insights: list[dict], scope: str = "all") -> int:
    """learning_insights 테이블에 UPSERT — 같은 key 면 occurrences+1.

    scope: 'economic' / 'theme' / 'all'. 글 종류별 분리 학습.
    """
    n = 0
    for ins in insights or []:
        try:
            key  = (ins.get("key") or "").strip()[:80]
            typ  = (ins.get("type") or "avoid").strip()[:40]
            desc = (ins.get("description") or "").strip()[:300]
            dirc = (ins.get("directive") or "").strip()[:500]
            w    = float(ins.get("weight", 1.0))
            if not key or not desc:
                continue
            db.upsert_learning_insight(key, typ, desc, dirc, weight=w, scope=scope)
            n += 1
        except Exception as e:
            print(f"  ⚠️ insight 저장 실패: {e}", file=sys.stderr)
            _g_report("radar", e, module=__name__)
    return n


def _build_telegram_report(date_str: str, agg: dict, insights: list[dict]) -> str:
    lines = [
        f"📊 *일일 분석 리포트* ({date_str})",
        "━━━━━━━━━━━━━━━━━━",
        f"발행: {agg['posts_count']}건  |  품질: {agg['quality_score']}/100",
        f"평균 조회수: {agg['avg_views']}  (최고 {agg['top_views']})",
        f"사전 수정 적용률: {agg['pre_applied_ratio']*100:.0f}%",
    ]
    if agg["posts_count"] == 0:
        lines.append("\n_오늘 발행된 글이 없어 분석을 건너뜁니다._")
        return "\n".join(lines)

    if agg.get("issue_types"):
        top_issues = ", ".join(f"{k}({v})" for k, v in agg["issue_types"].most_common(3))
        lines.append(f"잦은 이슈: {top_issues}")

    if insights:
        lines.append("\n💡 *내일 적용할 학습 지침*")
        for i, ins in enumerate(insights[:5], 1):
            d = (ins.get("directive") or ins.get("description") or "")[:100]
            lines.append(f"{i}. {d}")
    else:
        lines.append("\n_오늘은 새로 도출된 학습 지침이 없습니다._")

    if agg["avg_views"] == 0:
        lines.append("\n⚠️ 조회수 0 — performance_collector 점검 필요 "
                     "(TS_COOKIE 점검).")
    return "\n".join(lines)


def _send_tg(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
# 4. 진입점
# ─────────────────────────────────────────────────────────────

def run_daily_review(date_str: str | None = None) -> dict:
    """그날 발행 글을 *글 종류별로 분리 분석* → 종류별 인사이트 누적.

    흐름:
      1) get_today_post_analyses_grouped() → {economic: [...], theme: [...], ...}
      2) 각 그룹마다 _aggregate + Claude 묶음 분석 + _persist_insights(scope=ptype)
      3) 종류별 인사이트가 분리 누적되어 pre_revise 가 같은 종류 글에만 주입
      4) daily_review 테이블에는 *전체 통합 통계* 저장 (별도 group 통계는 insights 텍스트에)
      5) 텔레그램 리포트는 그룹별 섹션 분리

    반환: {"date":..., "groups": {economic: {...}, theme: {...}}, "total_posts":..., "total_insights":...}
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    grouped = db.get_today_post_analyses_grouped(date_str)
    total_posts = sum(len(v) for v in grouped.values())
    print(f"📊 daily_review {date_str} — 총 {total_posts}건  그룹 {list(grouped.keys())}")

    if total_posts == 0:
        # 글 0건이면 빈 record 만 저장 + 알림
        db.upsert_daily_review(date_str, {
            "posts_count": 0, "platforms_json": "{}", "avg_views": 0, "top_views": 0,
            "quality_score": 0, "sector_dist": "{}", "common_issues": "[]",
            "insights": "", "next_directives": "[]",
        })
        _send_tg(f"📊 *일일 분석 리포트* ({date_str})\n오늘 발행된 글이 없어 분석을 건너뜁니다.")
        try:
            publish("daily_review_completed", "RADAR",
                    {"date": date_str, "posts_count": 0, "groups": {}})
        except Exception:
            pass
        return {"date": date_str, "groups": {}, "total_posts": 0, "total_insights": 0}

    # ── 그룹별 분리 분석 ──────────────────────────────────────────
    group_results: dict[str, dict] = {}
    total_insights = 0
    overall_issues: Counter = Counter()
    overall_views_total = 0
    overall_quality_sum = 0.0

    for ptype, group_posts in grouped.items():
        agg = _aggregate(group_posts)
        overall_issues.update(agg["issue_types"])
        overall_views_total += sum(int(p.get("current_views") or 0) for p in group_posts)
        overall_quality_sum += agg["quality_score"] * agg["posts_count"]

        insights: list = []
        try:
            user_msg = _build_claude_input(date_str, group_posts, agg, post_type=ptype)
            insights = _call_claude(user_msg)
        except Exception as e:
            print(f"  ⚠️ [{ptype}] 인사이트 추출 실패: {e}", file=sys.stderr)
            _g_report("radar", e, module=__name__)

        n_p = _persist_insights(insights, scope=ptype)
        total_insights += n_p
        print(f"  💾 [{ptype}] 글 {agg['posts_count']}건 → 인사이트 신규/갱신 {n_p}건")

        group_results[ptype] = {
            "posts": agg["posts_count"],
            "platforms": agg["platforms"],
            "avg_views": agg["avg_views"],
            "quality": agg["quality_score"],
            "insights": insights or [],
            "insights_new": n_p,
            "agg": agg,
        }

    # ── 통합 통계 (daily_review 테이블 — 한 행/날짜) ─────────────
    avg_views_overall = (overall_views_total / total_posts) if total_posts else 0
    quality_overall   = (overall_quality_sum / total_posts) if total_posts else 0
    payload = {
        "posts_count":    total_posts,
        "platforms_json": json.dumps(
            {pt: r["platforms"] for pt, r in group_results.items()}, ensure_ascii=False),
        "avg_views":      round(avg_views_overall, 1),
        "top_views":      max((max([int(p.get("current_views") or 0) for p in g] or [0])
                               for g in grouped.values()), default=0),
        "quality_score":  round(quality_overall, 1),
        "sector_dist":    json.dumps(
            {pt: r["agg"]["sectors"] for pt, r in group_results.items()}, ensure_ascii=False),
        "common_issues":  json.dumps(
            [{"type": k, "count": v} for k, v in overall_issues.most_common(10)],
            ensure_ascii=False),
        "insights":       "\n".join(
            f"[{pt}] {(ins.get('directive') or ins.get('description') or '')}"
            for pt, r in group_results.items() for ins in r["insights"]),
        "next_directives": json.dumps(
            {pt: r["insights"] for pt, r in group_results.items()}, ensure_ascii=False),
    }
    db.upsert_daily_review(date_str, payload)

    # ── 텔레그램 리포트 (그룹별 섹션) ─────────────────────────────
    msg = _build_grouped_telegram_report(date_str, group_results, total_posts)
    _send_tg(msg)

    # ── 이벤트 발행 ──────────────────────────────────────────────
    try:
        publish("daily_review_completed", "RADAR", {
            "date": date_str,
            "posts_count": total_posts,
            "groups": {pt: {"posts": r["posts"], "insights_new": r["insights_new"]}
                       for pt, r in group_results.items()},
        })
    except Exception:
        pass

    return {
        "date": date_str,
        "groups": {pt: {"posts": r["posts"], "quality": r["quality"],
                        "insights_new": r["insights_new"]}
                   for pt, r in group_results.items()},
        "total_posts": total_posts,
        "total_insights": total_insights,
    }


def _build_grouped_telegram_report(date_str: str, groups: dict, total: int) -> str:
    """그룹별 섹션을 분리한 일일 리포트."""
    lines = [
        f"📊 *일일 분석 리포트* ({date_str})",
        "━━━━━━━━━━━━━━━━━━",
        f"총 발행: {total}건  ({len(groups)}개 종류)",
    ]
    pt_label = {"economic":"📰 경제 브리핑", "theme":"📈 테마글",
                "manual":"✍️ 수동", "unknown":"❓ 미분류"}
    for pt, r in groups.items():
        label = pt_label.get(pt, f"🔖 {pt}")
        lines.append("")
        lines.append(f"{label} — {r['posts']}건  품질 {r['quality']:.0f}/100")
        if r["insights"]:
            for i, ins in enumerate(r["insights"][:3], 1):
                d = (ins.get("directive") or ins.get("description") or "")[:90]
                lines.append(f"  {i}. {d}")
        else:
            lines.append("  _(새 인사이트 없음)_")
    return "\n".join(lines)


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18) — subprocess Layer 0 게이트
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    arg = sys.argv[1] if len(sys.argv) > 1 else None
    res = run_daily_review(arg)
    print(json.dumps(res, ensure_ascii=False, indent=2))
