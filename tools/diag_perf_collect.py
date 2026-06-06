"""
조회수 수집 진단 도구
────────────────────────────────────
티스토리·네이버 의 각 fallback 단계에서 어느 부분이 막히는지 정확히 파악.

실행:
    python tools/diag_perf_collect.py

출력:
    - TS_COOKIE 유효성 + 관리자 페이지 응답 코드·HTML 일부
    - 티스토리 공개 페이지 조회수 패턴 매칭 결과
    - 최근 발행글의 url 정상 캡처 여부
"""
from __future__ import annotations
import os, sys, re, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

DB = ROOT / "shared" / "jarvis.sqlite"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"}


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ════════════════════════════════════════════════════════════
# 최근 발행글 url 캡처 상태
# ════════════════════════════════════════════════════════════
def diag_recent_posts():
    section("최근 발행글 url 캡처 상태")
    if not DB.exists():
        print("DB 없음"); return []
    c = sqlite3.connect(str(DB)); cu = c.cursor()
    cu.execute("""
        select id, platform, title, url, created_at
        from post_analysis
        order by id desc limit 10
    """)
    rows = cu.fetchall()
    for r in rows:
        url_ok = "✅" if r[3] else "❌"
        print(f"  [{r[0]:>3}] {r[1]:7s} url={url_ok}  {r[2][:40]}")
        if r[3]:
            print(f"       URL: {r[3]}")
    return rows


# ════════════════════════════════════════════════════════════
# 티스토리 진단
# ════════════════════════════════════════════════════════════
def diag_tistory(rows):
    section("티스토리 진단")
    # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
    from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
    ts_cookie = get_tistory_cookie().strip('"').strip("'")
    ts_url    = os.getenv("TS_URL", "")
    ts_blog   = ts_url.replace("https://", "").replace("http://", "").split(".")[0] if ts_url else ""
    print(f"  TS_URL: {ts_url}")
    print(f"  TS_BLOG: {ts_blog}")
    print(f"  TS_COOKIE: {'설정됨 (길이 ' + str(len(ts_cookie)) + ')' if ts_cookie else '❌ 미설정'}")

    if not ts_cookie:
        print("\n  ⚠️ TS_COOKIE 미설정 — 1단계 관리자 페이지 접근 불가")
        print("     갱신 절차: 브라우저에서 tistory.com 로그인 → 개발자도구 → "
              "Application → Cookies → TSSESSION 값 복사 → .env 의 TS_COOKIE= 갱신")
        return

    # 관리자 페이지 응답
    try:
        url = f"https://{ts_blog}.tistory.com/manage/posts"
        resp = requests.get(url, headers={**HDR, "Cookie": f"TSSESSION={ts_cookie}"}, timeout=15)
        print(f"\n  [1단계] 관리자 페이지 응답: {resp.status_code} ({len(resp.text)} bytes)")
        if resp.status_code != 200:
            print("     ❌ 200 이외 — TS_COOKIE 만료 가능성. 위 절차로 갱신.")
            return
        html = resp.text
        if "로그인" in html and "loginbtn" in html:
            print("     ❌ 로그인 페이지 반환 — 쿠키 무효. 갱신 필요.")
            return
        # post_id 별 행 매칭 시도
        ts_post = next((r for r in rows if r[1] == "tistory" and r[3]), None)
        if ts_post:
            m_id = re.search(r'/(\d+)$', ts_post[3].rstrip('/'))
            post_id = m_id.group(1) if m_id else None
            print(f"     post_id 샘플: {post_id} ({ts_post[3]})")
            if post_id:
                # 현재 코드의 패턴
                pat = rf'/{post_id}["\s][^<]{{0,300}}?(\d{{2,}})[\s<]'
                m = re.search(pat, html, re.DOTALL)
                if m:
                    print(f"     ✅ 패턴1 매칭: views={m.group(1)}")
                else:
                    print(f"     ❌ 패턴1 미매칭 — HTML 구조 변경 가능. 응답 일부:")
                    # post_id 주변 200자 dump
                    idx = html.find(post_id)
                    if idx >= 0:
                        print(f"        ...{html[max(0,idx-100):idx+300]}...")
                    else:
                        print(f"        post_id 자체가 응답에 없음 — 다른 페이지일 수 있음")
    except Exception as e:
        print(f"  ❌ 관리자 페이지 호출 오류: {e}")

    # 공개 페이지 시도
    if rows:
        ts_post = next((r for r in rows if r[1] == "tistory" and r[3]), None)
        if ts_post:
            try:
                resp = requests.get(ts_post[3], headers=HDR, timeout=15)
                print(f"\n  [2단계] 공개 페이지 응답: {resp.status_code} ({len(resp.text)} bytes)")
                pats = [
                    (r'조회수[^\d]{0,10}([\d,]+)', "조회수 라벨"),
                    (r'"readCount"\s*:\s*(\d+)', "JSON readCount"),
                    (r'"visitCount"\s*:\s*(\d+)', "JSON visitCount"),
                ]
                hit = False
                for p, name in pats:
                    m = re.search(p, resp.text)
                    if m:
                        print(f"     ✅ [{name}] views={m.group(1)}")
                        hit = True
                        break
                if not hit:
                    print(f"     ❌ 공개 페이지에 조회수 미노출 (스킨 설정 문제)")
            except Exception as e:
                print(f"  ❌ 공개 페이지 호출 오류: {e}")




# ════════════════════════════════════════════════════════════
# 권장 조치 출력
# ════════════════════════════════════════════════════════════
def recommendations():
    section("권장 조치")
    print("""\
[티스토리]
  - TS_COOKIE 만료가 가장 흔한 원인. 7일~30일 단위 갱신 필요.
  - 갱신 절차:
      1) 크롬에서 tistory.com 로그인
      2) 개발자도구(F12) → Application → Cookies → https://tistory.com
      3) `TSSESSION` 값 복사
      4) jarvis-agent/.env 의 TS_COOKIE= 한 줄 갱신
      5) python tools/diag_perf_collect.py 로 재검증
  - 1단계 패턴이 미매칭이면 manage/posts HTML 구조 변경 — performance_collector
    의 block_patterns 정규식 보정 필요 (이 진단에서 dump 한 HTML 일부 참조).

""")


def main():
    rows = diag_recent_posts()
    diag_tistory(rows)
    recommendations()


if __name__ == "__main__":
    main()
