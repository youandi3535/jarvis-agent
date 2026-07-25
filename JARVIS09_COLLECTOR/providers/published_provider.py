"""JARVIS09_COLLECTOR/providers/published_provider.py — *발행된 우리 글* 수집.

★ 소유 이관 2026-07-23 (사용자 박제: "02는 수집 관련한건 아무것도 없도록 만들어").
  종전 위치: `JARVIS02_WRITER/scheduler.fetch_kor_counts`.

밖(네이버·티스토리)에 나가 HTML 을 받아오는 순간 그것은 *수집* 이다 — 대상이 남의 글이든
우리가 방금 올린 글이든 같다. 02 는 "이 테마 몇 자로 나갔어?" 만 묻고, 어느 URL 을 어떻게
긁을지는 09 가 정한다.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.collector.published")

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_TIMEOUT = 15

#  플랫폼별 본문 추출 규칙 — 플랫폼 추가는 이 표 한 줄 (② 동적 설계).
#    url_fix : 발행 URL → 크롤링용 URL 변환
#    extract : 응답 HTML → 본문 텍스트
_PLATFORMS: dict[str, dict] = {}


def _kor(text: str) -> int:
    return sum(1 for ch in text if "가" <= ch <= "힣")


def _strip_tags(html: str) -> str:
    raw = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL)
    raw = re.sub(r"<style.*?</style>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _naver_url(url: str) -> str:
    return url.replace("blog.naver.com", "m.blog.naver.com").split("?")[0]


def _naver_body(html: str) -> str:
    raw = _strip_tags(html)
    s = raw.find("이웃추가")
    e = raw.find("댓글", s + 5 if s > 0 else 0)
    return raw[s + 5:e] if s > 0 and e > 0 else raw


def _tistory_body(html: str) -> str:
    m = re.search(r'class="tt_article_useless_p_margin[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""


_PLATFORMS["naver"] = {"url_fix": _naver_url, "extract": _naver_body}
_PLATFORMS["tistory"] = {"url_fix": lambda u: u, "extract": _tistory_body}


def published_post_kor_counts(theme: str) -> dict:
    """테마의 최근 발행 글을 플랫폼별로 크롤링 → 한글 글자수. {naver: N, tistory: N}

    URL 은 DB(`post_analysis`)에 저장된 *실제 발행 URL* 에서 파생한다 (② 동적 설계 —
    블로그 주소를 코드에 박지 않는다). 크롤링 실패는 조용히 건너뛴다(보고용 부가 수치).
    """
    import requests

    from shared.db import get_db

    con = get_db()
    rows = con.execute(
        "SELECT platform, url FROM post_analysis "
        "WHERE theme=? ORDER BY created_at DESC LIMIT 6",
        (theme,),
    ).fetchall()
    con.close()

    url_map: dict[str, str] = {}
    for platform, url in rows:
        if platform not in url_map:
            url_map[platform] = url or ""

    counts: dict[str, int] = {}
    for platform, rule in _PLATFORMS.items():
        url = (url_map.get(platform) or "").strip()
        if not url:
            continue
        try:
            r = requests.get(rule["url_fix"](url), headers=_HEADERS, timeout=_TIMEOUT)
            counts[platform] = _kor(rule["extract"](r.text))
        except Exception as e:
            log.debug(f"[published] {platform} 글자수 수집 실패: {e}")
    return counts


__all__ = ["published_post_kor_counts"]
