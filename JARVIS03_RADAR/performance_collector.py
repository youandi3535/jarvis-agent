"""
JARVIS03 — 블로그 성과 수집기
발행된 글의 실제 조회수를 각 플랫폼에서 수집해 DB에 저장.
keyword_performance 학습 루프를 완성시키는 핵심 모듈.

실행:
  python performance_collector.py          # 전체 글 1회 수집
  python performance_collector.py --today  # 오늘 발행 글만 수집
"""
from __future__ import annotations

import sys
import os
import re
import time
import requests
from pathlib import Path
from datetime import date

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

# ── 요청 헤더 — 일반(공개 사이트용) ─────────────────────────
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── 데몬용 — Post Views Counter "크롤러 제외" 가 인식하는 명시적 봇
# 사용자가 카운팅 탭의 "방문자 제외 → 크롤러" 체크해두면 이 헤더로 호출 시 카운트에서 자동 제외.
# 블로그 스크래핑은 사이트 관리자(=사용자 본인) 소유라 봇 차단 위험 없음.
_BOT_HEADERS = {
    "User-Agent": "JARVIS-Bot/1.0 (+https://jarvis-agent.local; Mozilla/5.0 compatible; bot)",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ─────────────────────────────────────────────────────────────
# 네이버 검색 노출 순위 측정 (옵션 B 패치 2026-05-04)
# 본인 글이 키워드 검색 결과 1~100위 중 어디에 노출되는지 측정.
# 100위 안 = rank 정수 / 100위 밖 = None ("미노출")
# ─────────────────────────────────────────────────────────────

def _collect_naver_rank(keyword: str, post_url: str) -> int | None:
    """네이버 검색 API 로 본인 글의 노출 순위 측정.

    Returns:
        1~100: 검색 결과 순위 (낮을수록 강한 노출)
        None: 100위 밖 미노출 OR API 실패
    """
    if not keyword or not post_url:
        return None

    cid = os.getenv("NAVER_CLIENT_ID", "")
    csec = os.getenv("NAVER_CLIENT_SECRET", "")
    if not (cid and csec):
        print(f"  [네이버 rank] OpenAPI 키 없음")
        return None

    # 본인 logNo 추출 (URL 매칭 핵심)
    m = re.search(r'/(\d{8,})', post_url) or re.search(r'logNo=(\d+)', post_url)
    if not m:
        return None
    own_log_no = m.group(1)

    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/blog.json",
            headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec},
            params={"query": keyword, "display": 100, "sort": "sim"},
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"  [네이버 rank] API {resp.status_code}: {resp.text[:120]}")
            return None
        items = resp.json().get("items", [])
        if not items:
            return None

        for idx, item in enumerate(items, 1):
            link = item.get("link", "")
            # 본인 글 URL 매칭 — logNo 가 같으면 본인 글
            if own_log_no in link:
                print(f"  [네이버 rank] '{keyword[:20]}' → {idx}위 / {len(items)}건")
                return idx

        # 100위 안에 없음 = 미노출
        print(f"  [네이버 rank] '{keyword[:20]}' → 100위 밖 (미노출)")
        return None

    except Exception as e:
        print(f"  [네이버 rank] 오류: {e}")
        _g_report("radar", e, module=__name__)
        return None


# ─────────────────────────────────────────────────────────────
# 네이버 블로그 조회수 수집
# ─────────────────────────────────────────────────────────────

def _collect_naver_views(url: str) -> int:
    """
    네이버 블로그 포스트 조회수 스크래핑.
    URL 예: https://blog.naver.com/{blogId}/{logNo}
    반환: 조회수 (실패 시 0)
    """
    try:
        # logNo 추출
        m = re.search(r'blog\.naver\.com/[^/]+/(\d+)', url)
        if not m:
            m = re.search(r'logNo=(\d+)', url)
        if not m:
            print(f"  [네이버] logNo 파악 불가: {url}")
            return 0

        log_no  = m.group(1)
        # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
        from JARVIS08_PUBLISH.credentials.login_manager import get_naver_user
        blog_id = get_naver_user()
        if not blog_id:
            return 0

        # 네이버 블로그 모바일 페이지 — 조회수 파싱 용이
        api_url = (
            f"https://blog.naver.com/PostView.naver"
            f"?blogId={blog_id}&logNo={log_no}&redirect=Dlog"
        )
        # 다중 URL 시도: PostView (PC) → m.blog (모바일) → 일반 desktop
        candidate_urls = [
            api_url,
            f"https://m.blog.naver.com/{blog_id}/{log_no}",
            f"https://blog.naver.com/{blog_id}/{log_no}",
        ]
        html = ""
        for try_url in candidate_urls:
            try:
                resp = requests.get(try_url, headers=_HEADERS, timeout=15)
                if resp.status_code == 200 and len(resp.text) > 1000:
                    html = resp.text
                    break
            except Exception:
                continue

        if not html:
            print(f"  [네이버] 모든 URL 응답 실패")
            return 0

        # 패턴 풀 — '조회/뷰' 관련 컨텍스트가 있는 마크업만 신뢰 (2026-05-04 정밀화)
        # 일반적 cnt/pcol2/viewCount 단독 매칭은 연도(2026) 오인 위험 → 제거
        # 각 패턴은 *조회수임이 명백한* 컨텍스트 (조회/view-count/visitorCount/inflowCount) 필수
        patterns = [
            ("se_viewCount",    r'se_viewCount[^>]*>[^<]*<em[^>]*>([0-9,]+)</em>'),
            ("조회 라벨인접",    r'조회\s*</?[^>]*>\s*<[^>]+>\s*([\d,]+)'),
            ("조회 직후 숫자",  r'조회수?\s*[:：]?\s*([\d,]+)\s*(?:회|명|view)'),
            ("inflowCount",     r'inflowCount["\']?\s*[:=]\s*["\']?(\d+)'),
            ("visitorCount",    r'visitorCount["\']?\s*[:=]\s*["\']?(\d+)'),
            ("data-view-count", r'data-view-count\s*=\s*["\'](\d+)'),
            ("post-views",      r'class="[^"]*post-views?[-_]count[^"]*"[^>]*>\s*([\d,]+)'),
            ("viewCount JSON",  r'"viewCount"\s*:\s*(\d+)'),  # 마지막 — 가장 일반적이라 후순위
        ]

        # 연도 오인 가드: 1900~2099 범위의 4자리이면서 컨텍스트가 약하면 skip
        def _is_year_like(v: int, pattern_name: str) -> bool:
            if 1900 <= v <= 2099 and pattern_name in ("viewCount JSON", "조회 직후 숫자"):
                return True
            return False

        for name, pat in patterns:
            m = re.search(pat, html)
            if m:
                raw = m.group(1).replace(",", "")
                views = int(raw)
                if views <= 0:
                    continue
                if _is_year_like(views, name):
                    print(f"  [네이버] '{name}' 패턴이 {views} 잡았으나 연도 의심 — skip")
                    continue
                # 정상 인식 — 어느 패턴이 잡았는지 명시
                print(f"  [네이버] 조회수: {views:,}회 (패턴: {name})")
                return views

        # 모든 패턴 실패
        print(f"  [네이버] 패턴 8개 모두 매칭 실패 (응답 길이 {len(html)}자)")

    except Exception as e:
        print(f"  [네이버] 조회수 수집 오류: {e}")
        _g_report("radar", e, module=__name__)
    return 0


# ─────────────────────────────────────────────────────────────
# 티스토리 조회수 수집
# ─────────────────────────────────────────────────────────────

def _collect_tistory_views(url: str) -> int:
    """
    티스토리 조회수 수집 (2단계):
    1) 쿠키 인증 → 관리자 포스트 목록 페이지에서 파싱
    2) 공개 포스트 페이지 스크래핑 폴백
    """
    if not url:
        return 0

    # post_id 추출
    m_id = re.search(r'/(\d+)$', url.rstrip('/'))
    post_id = m_id.group(1) if m_id else None

    # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
    from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
    ts_raw = get_tistory_cookie().strip('"').strip("'")
    ts_blog = (os.getenv("TS_URL", "").replace("https://", "").replace("http://", "").split(".")[0])

    # ── 1단계: 쿠키 인증으로 관리자 페이지 조회 ──────────────
    if ts_raw and ts_blog and post_id:
        try:
            auth_headers = {**_HEADERS, "Cookie": f"TSSESSION={ts_raw}"}
            manage_url = f"https://{ts_blog}.tistory.com/manage/posts"
            resp = requests.get(manage_url, headers=auth_headers, timeout=15)
            if resp.status_code == 200:
                html = resp.text
                # 해당 포스트 행에서 조회수 추출
                # 패턴: postId와 같은 행/블록에서 숫자 찾기
                block_patterns = [
                    rf'/{post_id}["\s][^<]{{0,300}}?(\d{{2,}})[\s<]',
                    rf'postId["\s:=]+{post_id}[^{{}}]{{0,500}}?views?["\s:]+(\d+)',
                ]
                for pat in block_patterns:
                    m = re.search(pat, html, re.DOTALL)
                    if m:
                        views = int(m.group(1))
                        print(f"  [티스토리 관리자] 조회수: {views:,}회")
                        return views

                # 관리자 JSON embed 패턴
                m_json = re.search(
                    rf'"id"\s*:\s*{post_id}[^}}]{{0,300}}"(readCount|visitCount|views?)"\s*:\s*(\d+)',
                    html, re.DOTALL
                )
                if m_json:
                    views = int(m_json.group(2))
                    print(f"  [티스토리 관리자 JSON] 조회수: {views:,}회")
                    return views
        except Exception as e:
            print(f"  [티스토리 관리자] 접근 실패: {e}")
            _g_report("radar", e, module=__name__)

    # ── 2단계: 공개 포스트 페이지 스크래핑 ──────────────────
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        if resp.status_code != 200:
            return 0
        html = resp.text
        patterns = [
            # 사용자 스킨에 추가한 마크업 (Odyssey 커스터마이징, 가장 우선)
            r'class="view-count"[^>]*>\s*([\d,]+)\s*<',
            r'class="view"[^>]*>\s*조회\s*<[^>]+>\s*([\d,]+)\s*<',
            r'조회\s+([\d,]+)\s*회',
            # 일반
            r'조회수[^\d]{0,10}([\d,]+)',
            r'"readCount"\s*:\s*(\d+)',
            r'"visitCount"\s*:\s*(\d+)',
            r'class="[^"]*cnt[^"]*"[^>]*>\s*([\d,]+)',
            r'class="[^"]*count[^"]*"[^>]*>\s*([\d,]+)',
            r'<em[^>]*>([\d,]+)</em>\s*명',
        ]
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                views = int(m.group(1).replace(",", ""))
                if views > 0:
                    print(f"  [티스토리 공개] 조회수: {views:,}회")
                    return views
    except Exception as e:
        print(f"  [티스토리] 수집 오류: {e}")
        _g_report("radar", e, module=__name__)

    # ERRORS.md [20] — 티스토리는 글별 조회수를 공개 페이지에 노출 안 함 (정책).
    # [##_article_rep_view_##] 치환자 deprecated, <s_rp_count> 는 댓글용. A2 결정으로 보류.
    # 학습 루프의 양적 신호는 네이버로만 진행. 티스토리는 질적 분석 (suggestions) 만 활용.
    print(f"  [티스토리] 조회수 미수집 (정책 한계 — ERRORS.md [20])")
    return 0


# ─────────────────────────────────────────────────────────────
# 플랫폼 라우터
# ─────────────────────────────────────────────────────────────

_COLLECTORS = {
    "naver":   lambda r: _collect_naver_views(r.get("url", "")),
    "tistory": lambda r: _collect_tistory_views(r.get("url", "")),
}


# ─────────────────────────────────────────────────────────────
# 메인 수집 루프
# ─────────────────────────────────────────────────────────────

def collect_all(today_only: bool = False) -> dict:
    """
    모든 발행 글 조회수 수집 → DB 업데이트 → 키워드 학습 반영.
    반환: {"updated": N, "total": M, "by_platform": {...}}
    """
    posts = db.get_posts_for_view_collection()
    if today_only:
        today = date.today().strftime("%Y-%m-%d")
        posts = [p for p in posts if (p.get("created_at") or "").startswith(today)]

    print(f"\n📊 성과 수집 시작: {len(posts)}개 글")
    updated    = 0
    by_platform: dict[str, list[int]] = {}

    rank_updated = 0
    for post in posts:
        aid      = post["id"]
        platform = post["platform"]
        title    = post.get("title") or post.get("theme") or "?"

        collector = _COLLECTORS.get(platform)
        if not collector:
            continue

        print(f"\n  [{platform.upper()}] {title[:40]}")
        views = collector(post)

        if views > 0:
            db.update_post_views(aid, views)
            by_platform.setdefault(platform, []).append(views)
            updated += 1

        # 네이버 글: 검색 노출 순위 측정 (옵션 B)
        # source_keyword 가 있으면 그것으로, 없으면 title 첫 부분 fallback
        if platform == "naver":
            kw = (post.get("source_keyword") or "").strip()
            if not kw:
                kw = (title or "").split("|")[0].split("-")[0].strip()[:30]
            if kw:
                rank = _collect_naver_rank(kw, post.get("url", ""))
                db.update_naver_rank(aid, rank)
                if rank is not None:
                    rank_updated += 1
                time.sleep(0.3)  # 네이버 OpenAPI rate limit 여유

        time.sleep(1.0)  # 플랫폼 요청 간격

    # keyword_performance 학습 업데이트 — views OR rank 둘 중 하나라도 갱신되면 호출
    # 길1-B 패치 (2026-05-04): rank 만 갱신되는 경우(현재 100%)도 composite_score 채우게
    if updated > 0 or rank_updated > 0:
        db.update_keyword_views_from_posts()
        print(f"\n✅ 키워드 성과 학습 업데이트 완료 (views {updated}건 + rank {rank_updated}건)")

    # performance 테이블 일별 집계 업데이트
    _update_daily_performance(by_platform)

    result = {
        "updated":     updated,
        "total":       len(posts),
        "by_platform": {p: {"count": len(v), "avg": round(sum(v)/len(v))} for p, v in by_platform.items()},
        "rank_updated": rank_updated,
    }
    print(f"\n📈 수집 완료: 조회수 {updated}/{len(posts)}개 | "
          f"네이버 rank {rank_updated}건 노출 | {result['by_platform']}")
    return result


def _update_daily_performance(by_platform: dict):
    """오늘 수집한 플랫폼별 평균 조회수를 performance 테이블에 기록."""
    today = date.today().strftime("%Y-%m-%d")
    naver   = int(sum(by_platform.get("naver",   [])) / max(1, len(by_platform.get("naver",   [])))) if by_platform.get("naver")   else None
    tistory = int(sum(by_platform.get("tistory", [])) / max(1, len(by_platform.get("tistory", [])))) if by_platform.get("tistory") else None

    if any(v is not None for v in [naver, tistory]):
        db.save_performance(today, naver=naver, tistory=tistory)
        print(f"  📅 daily performance 저장: 네이버={naver} 티스토리={tistory}")


if __name__ == "__main__":
    # ★ P1-④ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): subprocess Layer 0 게이트.
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    today_only = "--today" in sys.argv
    from JARVIS00_INFRA.watchdog import guard_main
    with guard_main("성과 수집", deadline_sec=1800):
        result = collect_all(today_only=today_only)
    print(f"\n✅ 최종: {result['updated']}개 글 조회수 업데이트 완료")
