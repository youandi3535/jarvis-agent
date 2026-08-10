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
import json
import time
import requests
from pathlib import Path
from datetime import date
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS03_RADAR.collectors import report_radar as _g_report
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
# ★ 외부 wall-clock 상한 헬퍼 (ERRORS [401] 동일 패턴 — HTTP timeout= 파라미터만으론
#   DNS/소켓 레벨 hang을 못 막음. ThreadPoolExecutor + fut.result(timeout=N) 으로
#   호출 자체가 응답 없이 무한 대기하는 것을 방지. 내부 스레드는 leak 될 수 있으나
#   shutdown(wait=False)로 메인 루프를 블로킹하지 않음.
# ─────────────────────────────────────────────────────────────
def _bounded(fn, *args, timeout: float = 30.0, default=None, **kwargs):
    exe = ThreadPoolExecutor(max_workers=1)
    try:
        fut = exe.submit(fn, *args, **kwargs)
        try:
            return fut.result(timeout=timeout)
        except _FutureTimeoutError:
            name = getattr(fn, "__name__", str(fn))
            print(f"  [경고] {name} 응답 없음 {timeout:.0f}s 초과 — 스킵")
            return default
    finally:
        exe.shutdown(wait=False)


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

# ─────────────────────────────────────────────────────────────
# 네이버 글별 조회수 — 관리자 통계 (2026-08-08)
# ─────────────────────────────────────────────────────────────
#
# ★ 공개 페이지 스크래핑은 **원리적으로 불가능** 하다 (실측 2026-08-08)
#   `m.blog.naver.com` 응답 10만자 안에 `조회`·`visitorCount`·`viewCount` 가 **각 0회**.
#   네이버는 공개 페이지에 조회수를 노출하지 않는다 — 패턴을 고쳐도 없는 값은 못 찾는다.
#   (그래서 종전 `_collect_naver_views` 는 "패턴 8개 모두 매칭 실패" 를 반복했고,
#    `post_analysis.current_views` 가 네이버 107편 중 2편만 >0 이었다.)
#
# ★ 진짜 경로는 로그인 후 **관리자 통계 도메인** 이다.
#   추측으로 찾은 게 아니라 통계 화면의 링크를 그대로 따라갔다:
#       admin.blog.naver.com/{uid}/stat/today  →  iframe  →
#       blog.stat.naver.com/blog/article/{logNo}/cv
#   그 페이지에 `날짜 조회수` 표가 있고 `2026.08.05. (수) 7` 형태로 **일별** 값이 온다.
#
# ★ 브라우저를 글마다 띄우지 않는다 — 한 번 띄워 전부 순회한다(티스토리 배치와 같은 형태).
_NAVER_STAT_URL = "https://blog.stat.naver.com/blog/article/{log_no}/cv"
_NV_DAILY_ROW = re.compile(r"^\s*\d{4}\.\d{2}\.\d{2}\.\s*\([월화수목금토일]\)\s*([\d,]+)\s*$", re.M)
_NV_BATCH_CACHE: dict[str, int] = {}


def _naver_log_no(url: str) -> str:
    """URL → logNo. 못 찾으면 빈 문자열."""
    for pat in (r"blog\.naver\.com/[^/]+/(\d{6,})", r"logNo=(\d{6,})"):
        m = re.search(pat, url or "")
        if m:
            return m.group(1)
    return ""


def _collect_naver_stats_batch(urls: list) -> dict:
    """네이버 글별 조회수 일괄 수집 — {logNo: 조회수}. 실패 시 빈 dict.

    ★ 레이블로 판정한다, 위치로 하지 않는다
      통계 페이지엔 조회수·공감수·댓글수가 섞여 나온다. "숫자 N번째" 로 집으면
      네이버가 항목 하나만 추가해도 조용히 엉뚱한 값을 학습에 넣게 된다.
      `날짜 조회수` 표의 **행 꼴**(`YYYY.MM.DD. (요일) 숫자`)만 취해 합산한다.
    """
    log_nos = [n for n in (_naver_log_no(u) for u in urls) if n]
    if not log_nos:
        return {}
    drv = None
    out: dict = {}
    try:
        from JARVIS08_PUBLISH.credentials.login_manager import get_naver_cookies
        from JARVIS08_PUBLISH.platforms.naver_poster import _get_driver
        drv = _get_driver()
        drv.set_page_load_timeout(40)
        drv.get("https://www.naver.com")
        time.sleep(1)
        for c in (get_naver_cookies() or []):
            try:
                drv.add_cookie({"name": c["name"], "value": c["value"],
                                "domain": c.get("domain") or ".naver.com",
                                "path": c.get("path", "/")})
            except Exception:
                pass
        for log_no in log_nos:
            try:
                drv.get(_NAVER_STAT_URL.format(log_no=log_no))
                time.sleep(3)
                text = drv.find_element("tag name", "body").text or ""
                if "조회수" not in text:
                    continue          # 로그인 만료·페이지 변경 — 0 으로 단정하지 않는다
                vals = [int(v.replace(",", "")) for v in _NV_DAILY_ROW.findall(text)]
                if vals:
                    out[log_no] = sum(vals)
            except Exception as e:      # noqa: BLE001 — 한 건 실패가 배치를 깨지 않는다
                print(f"  [네이버] logNo={log_no} 통계 실패: {type(e).__name__}")
    except Exception as e:              # noqa: BLE001
        print(f"  [네이버] 통계 배치 실패: {type(e).__name__}: {e}")
    finally:
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
    return out


def _collect_naver_views(url: str) -> int:
    """네이버 글 조회수 — 배치 캐시에서 조회. 없으면 0.

    ★ 배치가 먼저 채운다(`collect_all` 이 호출). 캐시에 없으면 **0 을 돌려주되
      그건 '미수집' 이지 '조회 0' 이 아니다** — 학습 쪽(`build_target`)이
      관측/결측을 구분하므로 여기서 거짓 0 을 만들지 않는 것이 중요하다.
    """
    log_no = _naver_log_no(url)
    return int(_NV_BATCH_CACHE.get(log_no, 0)) if log_no else 0


_TS_BATCH_CACHE: dict[str, int] = {}


def _collect_tistory_stats_batch() -> dict[str, int]:
    """
    티스토리 통계 API(topEntry) 4-window 합산으로 인기글 조회수 일괄 수집.
    - 7일(day) + 30일(day) + 90일(week) + 180일(week) 각각 호출 → 중복 제거 후 최대값 보존
    - 현재는 90일 이후 데이터가 없으나 서비스 성숙 시 자동으로 수집됨
    HTTP만으로 동작 — Selenium 불필요.
    반환: {post_id_str: view_count}  (조회수 있는 글만)
    """
    from datetime import timedelta
    from JARVIS08_PUBLISH.credentials.login_manager import (
        get_tistory_cookie, refresh_tistory_cookies,
    )

    ts_blog = (os.getenv("TS_URL", "")
               .replace("https://", "").replace("http://", "").split(".")[0])
    if not ts_blog:
        return {}

    # (days_back, granularity) — 4개 구간으로 최대 커버리지
    # 180일: 지금은 0이지만 서비스 성숙 시 데이터 생김
    _WINDOWS = [
        (7,   "day"),
        (30,  "day"),
        (90,  "week"),
        (180, "week"),
    ]

    def _try(ts_raw: str) -> dict[str, int]:
        if not ts_raw:
            return {}
        hdrs = {
            **_HEADERS,
            "Accept": "application/json",
            "Cookie": f"TSSESSION={ts_raw}",
            "Referer": f"https://{ts_blog}.tistory.com/manage/statistics/blog",
        }
        base = f"https://{ts_blog}.tistory.com/manage/v2/statistics/blog/topEntry"
        result: dict[str, int] = {}
        for days, gran in _WINDOWS:
            start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
            try:
                r = requests.get(
                    f"{base}?metric=pv&startDate={start}&granularity={gran}",
                    headers=hdrs, timeout=15,
                )
                if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                    for p in r.json().get("data", {}).get("result", []):
                        pid = str(p.get("entryId") or "")
                        v   = int(p.get("count", 0) or 0)
                        if pid and v > 0:
                            # 기간마다 counts가 달라도 최대값 보존
                            result[pid] = max(result.get(pid, 0), v)
            except Exception as e:
                print(f"  [티스토리 배치] topEntry({days}d) 오류: {e}")
                _g_report("radar", e, module=__name__, func_name="_collect_tistory_stats_batch")
            time.sleep(0.3)
        return result

    # 1차: 현재 쿠키
    ts_raw = get_tistory_cookie().strip('"').strip("'")
    result = _try(ts_raw)

    # 실패 시 쿠키 갱신 후 재시도
    if not result:
        try:
            print("  [티스토리 배치] 쿠키 갱신 후 재시도...")
            refresh_tistory_cookies()
            # ★ load_dotenv(override=True) 제거 — 최신 TS_COOKIE 는 get_tistory_cookie() 가 .env 에서 직접 읽는다 — env 전체를 덮지 않는다(2026-08-10)
            ts_raw = get_tistory_cookie().strip('"').strip("'")
            result = _try(ts_raw)
        except Exception as e:
            print(f"  [티스토리 배치] 쿠키 갱신 실패: {e}")

    if result:
        print(f"  [티스토리 배치] topEntry 수집 완료: {len(result)}개 글")
    else:
        print("  [티스토리 배치] topEntry 수집 실패 → per-URL 폴백")
    return result


def _collect_tistory_today_blog_views() -> int | None:
    """
    티스토리 통계 API(count)에서 오늘 블로그 전체 조회수 수집.
    performance 테이블 일별 집계용.
    """
    from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie

    ts_blog = (os.getenv("TS_URL", "")
               .replace("https://", "").replace("http://", "").split(".")[0])
    if not ts_blog:
        return None

    ts_raw = get_tistory_cookie().strip('"').strip("'")
    if not ts_raw:
        return None

    hdrs = {
        **_HEADERS,
        "Accept": "application/json",
        "Cookie": f"TSSESSION={ts_raw}",
        "Referer": f"https://{ts_blog}.tistory.com/manage/statistics/blog",
    }
    try:
        r = requests.get(
            f"https://{ts_blog}.tistory.com/manage/v2/statistics/blog/count",
            headers=hdrs, timeout=10,
        )
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            today_pv = (r.json()
                        .get("data", {})
                        .get("result", {})
                        .get("pv", {})
                        .get("today", None))
            if today_pv is not None:
                print(f"  [티스토리 블로그 통계] 오늘 조회수: {today_pv}회")
                return int(today_pv)
    except Exception as e:
        print(f"  [티스토리 블로그 통계] 오류: {e}")
        _g_report("radar", e, module=__name__, func_name="_collect_tistory_today_blog_views")
    return None


def _collect_tistory_views(url: str) -> int:
    """
    티스토리 조회수 수집:
    0) 관리자 배치 캐시 우선 (collect_all 시작 시 선행 수집)
    1) 쿠키 인증 → 관리자 포스트 목록 페이지에서 파싱
    2) 공개 포스트 페이지 스크래핑 폴백
    """
    if not url:
        return 0

    # post_id 추출
    m_id = re.search(r'/(\d+)(?:\?|$)', url.rstrip('/'))
    post_id = m_id.group(1) if m_id else None

    # 0) 배치 캐시 조회 (우선)
    if post_id and _TS_BATCH_CACHE and post_id in _TS_BATCH_CACHE:
        v = _TS_BATCH_CACHE[post_id]
        print(f"  [티스토리 캐시] post_id={post_id} 조회수={v:,}회")
        return v

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
    global _TS_BATCH_CACHE, _NV_BATCH_CACHE
    _TS_BATCH_CACHE = {}  # 매 실행마다 캐시 초기화
    _NV_BATCH_CACHE = {}

    posts = db.get_posts_for_view_collection()
    if today_only:
        today = date.today().strftime("%Y-%m-%d")
        posts = [p for p in posts if (p.get("created_at") or "").startswith(today)]

    # 티스토리 글이 있으면 관리자 배치 수집 선행 (N+1 HTTP 요청 방지)
    if any(p.get("platform") == "tistory" for p in posts):
        _TS_BATCH_CACHE = _collect_tistory_stats_batch()

    # ★ 네이버도 같은 형태 — 브라우저를 글마다 띄우지 않는다 (2026-08-08).
    #   공개 페이지엔 조회수가 없어(실측) 관리자 통계로만 얻을 수 있고, 그건 로그인
    #   세션이 필요하다. 한 번 띄워 전부 순회한 뒤 닫는다.
    _nv_urls = [p.get("url", "") for p in posts if p.get("platform") == "naver"]
    if _nv_urls:
        _NV_BATCH_CACHE = _collect_naver_stats_batch(_nv_urls)
        print(f"  [네이버] 통계 배치: {len(_NV_BATCH_CACHE)}/{len(_nv_urls)}건 수집")

    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except Exception:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (수집 지속)

    print(f"\n📊 성과 수집 시작: {len(posts)}개 글")
    updated    = 0
    by_platform: dict[str, list[int]] = {}

    rank_updated = 0
    for post in posts:
        _wd_beat()   # ★ 글 단위 진행 신호 — 다건 스크래핑 장시간 실행 freeze 오탐 방지
        aid      = post["id"]
        platform = post["platform"]
        title    = post.get("title") or post.get("theme") or "?"

        collector = _COLLECTORS.get(platform)
        if not collector:
            continue

        print(f"\n  [{platform.upper()}] {title[:40]}")
        # ★ 게시글당 60초 상한 — DNS/소켓 hang이 있어도 다음 글로 진행 보장
        views = _bounded(collector, post, timeout=60.0, default=0)
        _wd_beat()  # 상한 통과 직후 재신호 (긴 정상 스크래핑도 freeze 오탐 방지)

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
                # ★ 20초 상한
                rank = _bounded(_collect_naver_rank, kw, post.get("url", ""),
                                 timeout=20.0, default=None)
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

    # 티스토리 performance: 블로그 전체 오늘 조회수 (per-post 평균보다 정확)
    if any(p.get("platform") == "tistory" for p in posts):
        ts_today = _collect_tistory_today_blog_views()
        if ts_today is not None:
            by_platform["tistory"] = [ts_today]  # 일별 블로그 총 PV

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
    # ★ deadline_sec=1800(30분)은 성격이 다른 블로그 발행 액션 값의 복붙 미스매치였음
    #   (ERRORS [403]). N건 순차 스크래핑 배치는 SSOT DEFAULT_ACTION_DEADLINE_SEC(60분) 사용.
    from JARVIS00_INFRA.watchdog import guard_main, DEFAULT_ACTION_DEADLINE_SEC
    with guard_main("성과 수집", deadline_sec=DEFAULT_ACTION_DEADLINE_SEC):
        result = collect_all(today_only=today_only)
    print(f"\n✅ 최종: {result['updated']}개 글 조회수 업데이트 완료")
