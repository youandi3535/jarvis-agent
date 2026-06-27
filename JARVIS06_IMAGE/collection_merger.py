"""JARVIS06_IMAGE/collection_merger.py — JARVIS09 수집물 delta merge 헬퍼.

★ 사용자 박제 2026-06-07 — JARVIS06↔JARVIS09 자율 교류 프로토콜.

JARVIS06 이 이미지 생성 중 자료 부족을 감지하면 *언제든* JARVIS09 에 추가
수집을 요청할 수 있다. 단, 맹목적 재호출이 아니라 delta-aware:
  1. 현재 보유 docs 의 content_hash 목록을 exclude 로 전달
  2. JARVIS09 는 신규/갱신분만 반환
  3. 이 모듈이 url 기준으로 merge — 신규는 append, 갱신은 replace
  4. status=='no_change' 면 기존 docs 그대로 사용

단일 진입점 원칙 유지:
  - yfinance / requests 직접 호출 금지 (CLAUDE.md 수집 단일 진입점 규정)
  - JARVIS09 의 collect_for_theme* 만 호출

공개 API:
  extract_hashes(docs)                          → set[str]
  merge_delta(existing, delta_response)         → list
  request_more(theme, existing, sector, aspect) → list  ← 원샷 헬퍼
  facts_for_photo(docs, max_n)                  → list[str]
  facts_for_chart(docs, max_n)                  → list[str]
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

log = logging.getLogger("jarvis.image.merger")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass


# ── fingerprint 추출 ─────────────────────────────────────────────

def extract_hashes(docs: Iterable) -> set[str]:
    """docs 목록에서 content_hash 집합 추출.

    구버전 CollectionResult (content_hash 미보유) 도 안전하게 처리.
    """
    out: set[str] = set()
    for d in docs or []:
        h = getattr(d, "content_hash", "") or ""
        if h:
            out.add(h)
    return out


# ── merge: existing + delta → merged ─────────────────────────────

def merge_delta(existing: list, delta_response: dict) -> list:
    """기존 docs 에 delta 응답을 병합.

    Args:
        existing:       JARVIS06 이 현재 보유 중인 docs (list[CollectionResult])
        delta_response: collect_for_theme_delta 반환 dict
            {"status": "no_change"|"fresh", "added": [...], "version": ts, ...}

    Returns:
        list[CollectionResult] — merge 결과.
        - status='no_change' → existing 그대로
        - status='fresh' → existing + added (url 매칭 시 added 가 갱신본으로 replace)
    """
    if not delta_response or delta_response.get("status") == "no_change":
        return list(existing or [])

    added = delta_response.get("added") or []
    if not added:
        return list(existing or [])

    # url 키 맵으로 dedupe — url 같으면 added 가 우선 (갱신본)
    by_url: dict[str, object] = {}
    for d in existing or []:
        url = getattr(d, "url", "") or ""
        if url:
            by_url[url] = d

    replaced = 0
    appended = 0
    for d in added:
        url = getattr(d, "url", "") or ""
        if not url:
            continue
        if url in by_url:
            # 같은 url 다른 hash → 갱신
            old_hash = getattr(by_url[url], "content_hash", "")
            new_hash = getattr(d, "content_hash", "")
            if old_hash != new_hash:
                by_url[url] = d
                replaced += 1
        else:
            by_url[url] = d
            appended += 1

    log.info(f"[merger] merge_delta — existing={len(existing or [])} added={len(added)} "
             f"replaced={replaced} appended={appended} → total={len(by_url)}")
    return list(by_url.values())


# ── 원샷 헬퍼: 부족 감지 → delta 요청 → merge ─────────────────────

def request_more(
    theme: str,
    existing: list | None = None,
    sector: str = "",
    aspect: str | None = None,
) -> list:
    """JARVIS06 이 자료 보강이 필요할 때 호출하는 원샷 헬퍼.

    existing 의 hash 를 exclude 로 전달 → JARVIS09 가 신규만 반환 →
    merge_delta 로 합쳐서 최종 docs 리스트 반환.

    실패 시 existing 그대로 반환 (호출자가 빈손되지 않도록).
    """
    try:
        from JARVIS09_COLLECTOR import collect_for_theme_delta
    except ImportError as e:
        log.warning(f"[merger] collect_for_theme_delta import 실패: {e}")
        _g_report("image", e, module=__name__)
        return list(existing or [])

    try:
        excl = extract_hashes(existing or [])
        resp = collect_for_theme_delta(
            theme=theme, sector=sector,
            exclude_hashes=excl, aspect=aspect,
        )
    except Exception as e:
        log.warning(f"[merger] delta 요청 실패: {e}")
        _g_report("image", e, module=__name__)
        return list(existing or [])

    merged = merge_delta(existing or [], resp)
    log.info(f"[merger] request_more theme='{theme}' aspect={aspect} "
             f"status={resp.get('status')} → final={len(merged)}건")
    return merged


# ── facts 추출 — 프롬프트·차트 컨텍스트용 ──────────────────────────

_NUM_PAT = re.compile(
    r"[\d][\d,\.]*\s*(?:%|원|달러|억|조|만|배|p|bp|pts?|포인트|%p|개|건|명|개월|년)"
)

# ★ 주식 시가총액 문장 패턴 — 비주식 주제 글에서 차트 데이터 오염 방지
_STOCK_CAP_PAT = re.compile(
    r"(?:삼성전자|SK하이닉스|현대차|삼성SDI|셀트리온|LG에너지|카카오|네이버|삼성물산"
    r"|SK이노베이션|POSCO|포스코|KB금융|하나금융|신한지주|우리금융|LG화학|현대모비스"
    r"|롯데케미칼|한화에어로|HD현대|삼성바이오).{0,30}\d+(?:\.\d+)?조"
)
# 주식 테마 키워드 — 이 키워드가 있는 글에서는 종목 시총 데이터 허용
_STOCK_THEME_KWS = frozenset([
    "반도체", "2차전지", "배터리", "바이오", "제약", "자동차", "전기차", "게임",
    "방산", "조선", "철강", "정유", "에너지", "부동산", "건설", "항공", "유통",
    "플랫폼", "금융주", "은행주", "보험주", "통신주", "시가총액", "주가", "종목",
])


def _safe_field(doc, name: str, default: str = "") -> str:
    return str(getattr(doc, name, default) or default)


def facts_for_photo(docs: Iterable, max_n: int = 4) -> list[str]:
    """사진 프롬프트용 사실 추출 — 헤드라인·서사 중심.

    우선순위:
        1. naver_news / news / web — 사회·문화 컨텍스트 (사진에 적합)
        2. blog — 서사 컨텍스트
        3. 기타

    반환: 영문/한글 혼재 가능한 한 줄 사실 N개 (title 우선, 없으면 첫 문장).
    """
    if not docs:
        return []
    _priority = {"naver_news": 0, "news": 0, "web": 1, "blog": 2}
    sorted_docs = sorted(
        docs,
        key=lambda d: (_priority.get(_safe_field(d, "source_type"), 9),
                       -len(_safe_field(d, "title"))),
    )
    out: list[str] = []
    seen: set[str] = set()
    for d in sorted_docs:
        title = _safe_field(d, "title").strip()
        if not title:
            cleaned = _safe_field(d, "cleaned_text")
            title = (cleaned.split("\n", 1)[0] or "").strip()[:120]
        if not title:
            continue
        key = title[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(title[:160])
        if len(out) >= max_n:
            break
    return out


def facts_for_chart(docs: Iterable, max_n: int = 8, keyword: str = "") -> list[str]:
    """차트용 수치 사실 추출 — 숫자·통계 라인 우선.

    cleaned_text 에서 숫자 패턴이 들어간 문장 추출.
    공시·통계 source 우선.

    keyword: 글 키워드. 주식 테마가 아닌 경우 종목 시가총액 문장 자동 제외.
    """
    if not docs:
        return []
    # ★ 경제 일반 글(줄인상·물가 등)에서 주식 시가총액 facts 오염 방지
    _filter_stock_cap = not any(k in (keyword or "") for k in _STOCK_THEME_KWS)

    _priority = {
        "dart": 0, "ecos": 0, "kosis": 0, "krx": 1, "finance": 1,
        "kor_econ": 2, "naver_news": 3, "news": 3,
    }
    sorted_docs = sorted(
        docs,
        key=lambda d: _priority.get(_safe_field(d, "source_type"), 9),
    )
    out: list[str] = []
    seen: set[str] = set()
    for d in sorted_docs:
        text = _safe_field(d, "cleaned_text")
        if not text:
            continue
        # 숫자가 포함된 문장 분리
        for sent in re.split(r"(?<=[.!?。\n])\s+", text):
            sent = sent.strip()
            if len(sent) < 10 or len(sent) > 220:
                continue
            if not _NUM_PAT.search(sent):
                continue
            # ★ 비주식 주제에서 종목 시가총액 문장 제외 ([281] 2026-06-08)
            if _filter_stock_cap and _STOCK_CAP_PAT.search(sent):
                continue
            key = sent[:40]
            if key in seen:
                continue
            seen.add(key)
            src = _safe_field(d, "source_type")
            out.append(f"[{src}] {sent}")
            if len(out) >= max_n:
                return out
    return out


__all__ = [
    "extract_hashes",
    "merge_delta",
    "request_more",
    "facts_for_photo",
    "facts_for_chart",
]
