"""내부 링크(연관 글) 단일 진입점 — 2026-08-07 신설.

★ 왜 신설했나 — "발행은 되는데 채점·저장에는 없었다"

  `T8_internal_link`(티스토리 2점)는 실측 94건 중 **91건이 0점**이었다. 그런데 실제
  발행된 글에는 '[함께 읽으면 좋은 글]' 블록이 있다. 모순처럼 보이지만 이유는 단순하다 —
  그 블록이 **발행 시점에 에디터 DOM 으로 직접 주입**돼서, 채점·저장되는 원고(html)에는
  한 줄도 들어가지 않았다. 채점기는 있지도 않은 링크를 찾다가 매번 0점을 준 것이다.

  같은 병이 여백(B18)에도 있었다: *발행되는 것* 과 *채점되는 것* 이 다른 물건이었다.

★ 3원칙
  ① 단일 진입점 — 자기 블로그의 실제 URL 을 아는 것은 발행 도메인(JARVIS08)뿐이다.
    링크 블록을 만드는 곳은 이 파일 하나이고, 발행자의 주입 코드는 **삭제**한다
    (남겨두면 링크가 두 벌 붙는다).
  ② 동적 설계 — 링크 개수는 `seo_standards.PLATFORM_STANDARDS[platform]["internal_links"]`
    에서 파생한다. 네이버는 그 값이 0 이라 **플랫폼 if 분기 없이** 자동으로 0개가 된다.
    링크 대상도 `post_analysis` 의 우리 발행 이력에서 파생 — RSS 왕복을 없앤다.
  ③ 4조합 — `process_draft` 를 지나므로 네 조합이 같은 코드 경로를 밟고, 결과만 기준에서 갈린다.
"""
from __future__ import annotations

import logging
from html import escape

log = logging.getLogger("jarvis")

__all__ = ["link_count", "recent_links", "related_links_html", "internal_links_effective"]


def _std(platform: str, key: str, default):
    """플랫폼 SEO 기준 — 채점기 `post_scorer._std()` 와 **같은 dict** 를 읽는다."""
    try:
        from JARVIS02_WRITER.seo_standards import PLATFORM_STANDARDS
        v = PLATFORM_STANDARDS.get(platform, {}).get(key)
        return v if v is not None else default
    except Exception:
        return default


def link_count(platform: str) -> int:
    """이 플랫폼에 붙일 내부 링크 개수. 네이버는 기준상 0 → 블록 자체를 만들지 않는다."""
    try:
        return max(0, int(_std(platform, "internal_links", 0) or 0))
    except (TypeError, ValueError):
        return 0


def recent_links(platform: str, limit: int, exclude_url: str = "") -> list:
    """우리가 **실제로 발행한** 같은 플랫폼 최근 글 `[{"title","url"}]`.

    ★ 죽은 링크를 붙이면 SEO 역효과다. 그래서 ① url 이 비지 않은 행만 ② 같은 플랫폼만
      ③ 자기 자신은 제외한다. 조건을 못 채우면 빈 리스트 — **없는 링크를 지어내지 않는다.**
    """
    if limit <= 0:
        return []
    try:
        from shared.db import get_db
        with get_db() as con:
            rows = con.execute(
                "SELECT title, url FROM post_analysis "
                "WHERE platform=? AND url IS NOT NULL AND url<>'' AND url<>? "
                "ORDER BY id DESC LIMIT ?",
                (platform, exclude_url or "", int(limit)),
            ).fetchall()
        return [{"title": r["title"] or "", "url": r["url"]} for r in rows if r["url"]]
    except Exception as e:
        log.warning(f"[internal_links] 최근 글 조회 실패: {type(e).__name__}: {e}")
        return []


def related_links_html(platform: str, exclude_url: str = "") -> str:
    """연관 글 블록 HTML. 링크가 0개면 `""` (블록 자체를 넣지 않는다)."""
    n = link_count(platform)
    posts = recent_links(platform, n, exclude_url=exclude_url)
    if not posts:
        return ""
    out = ['<hr/>',
           '<div style="background:#f8f9fa;border-left:4px solid #2563eb;'
           'padding:14px 18px;margin:20px 0;border-radius:4px;">',
           '<p style="font-weight:700;margin:0 0 8px;">[함께 읽으면 좋은 글]</p>',
           '<ul style="margin:0;padding-left:18px;line-height:1.9;">']
    for p in posts:
        out.append(f'<li><a href="{escape(p["url"], quote=True)}" '
                   f'style="color:#2563eb;">{escape(p["title"])}</a></li>')
    out.append('</ul></div>')
    return "".join(out)


def internal_links_effective(platform: str = "tistory") -> dict:
    """★ 만든 블록이 **실제로 채점을 통과하는지** 동작으로 확인 (patch_effective 표준)."""
    try:
        from JARVIS02_WRITER.post_scorer import item_scores, score_post
        block = related_links_html(platform)
        body = "코스피가 올랐다. " * 40 + block
        sr = score_post({"html": body, "content": body, "title": "t",
                         "keyword": "코스피", "post_type": "economic"},
                        platform=platform, post_type="economic")
        got = {i["key"]: (i["score"], i["max"]) for i in item_scores(sr)}
        return {"ok": True, "platform": platform, "want": link_count(platform),
                "block_chars": len(block), "T8_internal_link": got.get("T8_internal_link")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
