"""
주간 SEO 학습기 — 매주 월요일 06:00 실행.

흐름:
  1. 신뢰도 높은 SEO 공식 문서 + 주요 가이드 페이지 수집 (urllib)
  2. Claude API에 현재 seo_standards 기준과 최신 자료 비교 분석 요청
  3. 개선 필요 항목 → Finding 객체 생성 → 텔레그램 보고 (인라인 버튼)
  4. 사용자 ✅ 승인 → ReAct create_plan → seo_standards.py 업데이트
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass

# ── 분량 정책 단일 진입점 ────────────────────────────
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L
# ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 수집 대상 — 신뢰도 높은 공식/전문 SEO 소스
# ═══════════════════════════════════════════════════════════════

_SEO_SOURCES: list[tuple[str, str]] = [
    # (이름, URL)
    ("Google 검색 센터 - SEO 기초",
     "https://developers.google.com/search/docs/fundamentals/seo-starter-guide"),
    ("Google 검색 센터 - Helpful Content",
     "https://developers.google.com/search/docs/fundamentals/creating-helpful-content"),
    ("네이버 서치어드바이저 - 콘텐츠 가이드",
     "https://searchadvisor.naver.com/guide/seo-basic-guide"),
]

_FETCH_TIMEOUT  = 12    # 초
_MAX_CHARS_PAGE = 4000  # 페이지당 전달 글자 한도


# ═══════════════════════════════════════════════════════════════
# 내부 유틸
# ═══════════════════════════════════════════════════════════════

def _fetch_page(name: str, url: str) -> str:
    """★ shim → JARVIS09 단일 진입점 위임 (2026-05-31 이관)."""
    try:
        from JARVIS09_COLLECTOR.providers.economic_data_provider import fetch_seo_docs as _j09_fetch
        combined = _j09_fetch()
        # 요청된 name/url에 해당하는 부분만 추출
        for block in combined.split("\n\n"):
            if name in block or url.split("/")[-1] in block:
                return block[:_MAX_CHARS_PAGE]
        return combined[:_MAX_CHARS_PAGE]
    except Exception as e:
        log.warning(f"[SEO Learner] fetch 위임 실패 ({name}): {e}")
        return ""


def _build_fetched_block() -> str:
    """★ shim → JARVIS09 단일 진입점 위임 (2026-05-31 이관)."""
    try:
        from JARVIS09_COLLECTOR.providers.economic_data_provider import fetch_seo_docs as _j09_fetch
        result = _j09_fetch()
        return result if result else "외부 페이지 수집 실패 — Claude 내부 학습 데이터 기반 분석 수행."
    except Exception as e:
        log.warning(f"[SEO Learner] fetch_seo_docs 위임 실패: {e}")
        return "외부 페이지 수집 실패 — Claude 내부 학습 데이터 기반 분석 수행."


def _parse_improvements(raw: str) -> list[dict]:
    """Claude 응답에서 JSON 배열 파싱."""
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return []


# ═══════════════════════════════════════════════════════════════
# fix_fn 팩토리
# ═══════════════════════════════════════════════════════════════

def _make_fix_fn(imp: dict):
    """Finding.fix_fn — ✅ 클릭 시 ReAct가 seo_standards.py 업데이트."""
    def fix_fn():
        task = (
            f"SEO 주간학습에서 개선안이 발견됐습니다.\n"
            f"플랫폼: {imp.get('platform', 'all')}\n"
            f"항목: {imp.get('title', '')}\n"
            f"현재 기준: {imp.get('current', '—')}\n"
            f"개선안: {imp.get('improvement', '')}\n"
            f"근거: {imp.get('reason', '')}\n\n"
            f"JARVIS02_WRITER/seo_standards.py 의 해당 플랫폼 PLATFORM_STANDARDS를 "
            f"위 개선안 내용으로 업데이트해주세요 (seo_prompt 문자열 및 수치 필드). "
            f"LAST_UPDATED 를 오늘 날짜(YYYY-MM-DD)로, "
            f"SEO_VERSION 을 현재 버전에서 minor+1 로 올려주세요."
        )
        try:
            import jarvis_daemon as _dm
            _dm._run_react(task, max_steps=6, verbose=True)
        except Exception as e:
            from JARVIS01_MASTER.proactive_monitor import _send_tg
            _send_tg(f"⚠️ SEO 기준 업데이트 ReAct 실패: {e}")

    return fix_fn


# ═══════════════════════════════════════════════════════════════
# 공개 진입점
# ═══════════════════════════════════════════════════════════════

def run_seo_learning() -> None:
    """주간 SEO 학습 실행 — job_registry.py weekly_seo_learn 잡 콜백."""
    log.info("[SEO Learner] 주간 SEO 학습 시작")

    # ── Import ──────────────────────────────────────────────────
    try:
        from JARVIS02_WRITER.seo_standards import get_all_standards_summary
        from JARVIS01_MASTER.proactive_monitor import Finding, _dispatch_findings
        from shared.llm import invoke_text as _inv_cli
    except ImportError as e:
        log.error(f"[SEO Learner] import 실패 → 스킵: {e}")
        _g_report("writer", e, module=__name__)
        return

    today_str         = datetime.now().strftime("%Y년 %m월 %d일")
    current_standards = get_all_standards_summary()

    # ── 소스 수집 ───────────────────────────────────────────────
    log.info("[SEO Learner] 외부 소스 수집 중...")
    fetched_block = _build_fetched_block()

    # ── Claude 비교 분석 ────────────────────────────────────────
    prompt = f"""당신은 SEO 전문가입니다. 오늘 날짜: {today_str}

[현재 JARVIS SEO 기준]
{current_standards}

[최신 SEO 자료 (인터넷 수집)]
{fetched_block}

위 자료를 바탕으로, 현재 JARVIS SEO 기준에서 2025~2026년 최신 알고리즘 트렌드 대비 \
개선이 필요한 부분을 찾아주세요.
네이버(C-Rank·DIA), 티스토리(구글 SEO) 각각 검토.

JSON 배열로만 답변 (실제 개선 필요 항목만 — 이미 잘 적용된 것은 제외):
[
  {{
    "platform": "naver|tistory|all",
    "severity": "important|minor",
    "title": "개선 항목 제목 ({_L.SEO_IMPROVEMENT_TITLE_MAX}자 이내)",
    "current": "현재 기준 요약 (없으면 '미정의')",
    "improvement": "구체적 개선안 ({_L.build_length_phrase(_L.SEO_IMPROVEMENT_SENTS_MIN, _L.SEO_IMPROVEMENT_SENTS_MAX)})",
    "reason": "최신 알고리즘 변화·트렌드 근거"
  }}
]"""

    try:
        raw_text     = (_inv_cli("writer", prompt, timeout=120) or "").strip()
        improvements = _parse_improvements(raw_text)
    except Exception as e:
        log.error(f"[SEO Learner] Claude 분석 실패: {e}")
        _g_report("writer", e, module=__name__)
        return

    if not improvements:
        log.info("[SEO Learner] 개선 사항 없음 — 이번 주 기준 유지")
        return

    # ── Finding 생성 → 텔레그램 ─────────────────────────────────
    findings: list[Finding] = []
    for imp in improvements[:5]:  # 최대 5건 (텔레그램 flooding 방지)
        pf_label = imp.get("platform", "all").upper()
        sev      = "warning" if imp.get("severity") == "important" else "info"
        findings.append(Finding(
            key=f"seo_learn:{pf_label}:{imp.get('title','')[:25]}",
            severity=sev,
            title=f"[SEO 주간학습] {pf_label}: {imp.get('title','')}",
            detail=(
                f"*현재*: {imp.get('current', '—')}\n"
                f"*개선*: {imp.get('improvement', '')}\n"
                f"*근거*: {imp.get('reason', '')}"
            ),
            fix_fn=_make_fix_fn(imp),
            fix_label="seo_standards.py 업데이트",
        ))

    _dispatch_findings(findings, source=f"SEO 주간학습 ({today_str})")
    log.info(f"[SEO Learner] {len(findings)}건 개선안 텔레그램 전송 완료")
