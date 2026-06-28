"""웹 재검증 프로바이더 — 발행 전 사실성 게이트용 임의 쿼리 검색.

★ JARVIS09 수집 단일 진입점 규정: 모든 웹 수집·검색은 이 폴더 안에서만.
  발행 전 품질 게이트(law_enforcer.factuality_issues)가 본문의 핵심
  수치·고유명사·날짜를 수집 출처 코퍼스로 1차 grounding 한 뒤, 출처에서
  확인하지 못한 항목만 이 함수로 웹 재검증한다.

★ 반환 계약 — fail-open/차단 정책을 호출자가 구분할 수 있도록 두 신호를 분리:
  - 인프라 실패 (자격증명 없음·타임아웃·전송오류·비200) → `WebVerifyUnavailable` 예외.
    호출자는 이를 잡아 *fail-open(미차단)* 처리 — 웹 불안정으로 정상 글을 막지 않음.
  - HTTP 성공 → list 반환 (빈 리스트 = "검색은 됐으나 근거 못 찾음").
    호출자는 이를 *"웹에서도 확인 불가"* 로 보고 차단 판단에 사용.

기존 naver_news_provider 의 네이버 Open API 패턴 재사용 (NAVER_CLIENT_ID/SECRET).
"""
from __future__ import annotations
import os
import httpx
from ..rate_limiter import wait_for

import logging
log = logging.getLogger("jarvis.collector.verify")

# 네이버 Open API — 뉴스(최신 수치·사건) + 웹문서(일반 사실)
_NEWS_API = "https://openapi.naver.com/v1/search/news.json"
_WEBKR_API = "https://openapi.naver.com/v1/search/webkr.json"


class WebVerifyUnavailable(Exception):
    """웹 재검증 인프라 실패 — 자격증명 없음·타임아웃·전송오류·비200.

    이 예외는 "근거를 못 찾았다"가 아니라 "검증 자체를 못 했다"를 뜻한다.
    호출자(사실성 게이트)는 이를 잡아 fail-open(미차단) 으로 처리해야 한다.
    """


def _strip_tags(text: str) -> str:
    """네이버 API 응답의 <b> 하이라이트·HTML 엔티티 정리."""
    return (
        (text or "")
        .replace("<b>", "").replace("</b>", "")
        .replace("&quot;", '"').replace("&amp;", "&")
        .replace("&lt;", "<").replace("&gt;", ">")
        .strip()
    )


def web_verify(query: str, max_items: int = 5, timeout: float = 8.0) -> list[dict]:
    """임의 쿼리를 웹에서 검색해 근거 스니펫을 반환한다.

    네이버 Open API(뉴스 → 웹문서 순)로 가장 최신·정확한 한국어 근거를 수집.

    Args:
        query: 검증할 사실 주장 키워드 (수치·고유명사·날짜 등)
        max_items: 최대 근거 수
        timeout: HTTP 타임아웃(초) — 발행 임계경로 stall 방지용 짧은 기본값

    Returns:
        [{"title": str, "url": str, "snippet": str}, ...]
        — HTTP 성공 시 (빈 리스트 = 근거 못 찾음).

    Raises:
        WebVerifyUnavailable: 자격증명 미설정·타임아웃·전송오류·비200 응답 등
            *검증 인프라 실패*. 호출자는 fail-open(미차단) 처리.
    """
    query = (query or "").strip()
    if not query:
        return []  # 검증할 게 없음 — 인프라 실패 아님

    client_id = os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        raise WebVerifyUnavailable("NAVER_CLIENT_ID/SECRET 미설정 — 웹 재검증 불가")

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }
    results: list[dict] = []
    seen: set[str] = set()
    any_success = False
    last_error: Exception | None = None

    for api_url in (_NEWS_API, _WEBKR_API):
        if len(results) >= max_items:
            break
        try:
            wait_for(api_url)
            resp = httpx.get(
                api_url,
                params={"query": query, "display": max_items, "sort": "sim"},
                headers=headers,
                timeout=timeout,
            )
            if resp.status_code != 200:
                last_error = WebVerifyUnavailable(f"API {resp.status_code}: {resp.text[:160]}")
                log.warning(f"[Verify] {last_error}")
                continue
            any_success = True
            for item in resp.json().get("items", []):
                url = item.get("originallink") or item.get("link", "")
                title = _strip_tags(item.get("title", ""))
                snippet = _strip_tags(item.get("description", ""))
                if not title or (url and url in seen):
                    continue
                if url:
                    seen.add(url)
                results.append({"title": title, "url": url, "snippet": snippet})
                if len(results) >= max_items:
                    break
        except httpx.HTTPError as e:
            last_error = e
            log.warning(f"[Verify] '{query}' 전송 오류: {e}")
        except Exception as e:  # JSON 파싱 등
            last_error = e
            log.warning(f"[Verify] '{query}' 처리 오류: {e}")

    # 두 엔드포인트 모두 성공한 호출이 없으면 → 인프라 실패(fail-open 대상)
    if not any_success:
        raise WebVerifyUnavailable(f"'{query}' 웹 재검증 호출 전부 실패: {last_error}")

    log.info(f"[Verify] '{query}' → 근거 {len(results)}건")
    return results[:max_items]
