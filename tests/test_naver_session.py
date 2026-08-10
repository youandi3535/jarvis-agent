"""네이버 세션 — 2026-08-10 07:00 경제 브리핑 네이버 미발행 회귀 방지.

사고 요약(실측):
  · 08-09 23:23 로그인은 **성공**했는데, 하필 `www.naver.com` 만 들르는 수집 경로로
    저장돼 blog 도메인 쿠키(BA_DEVICE·JSESSIONID)가 0개인 pkl 이 만들어졌다.
  · 그 pkl 은 포털 판정을 통과하고(= "✅ 쿠키 유효") 글쓰기 화면에서 튕긴다
    (= "⚠️ 쿠키 브라우저 적용 실패") → 전체 로그인 → 무인 CAPTCHA → 미발행.
  · 무인 판정은 subprocess 경계를 못 넘어 120초 대기를 4회(482초) 버렸다.
  · 네이버 쿠키 게이트가 세트 전체를 `return` 으로 끊어 **티스토리까지** 죽었다.

네 결함 모두 '코드가 어떻게 생겼나' 가 아니라 **동작** 으로 검증한다.
"""
import threading

import pytest

from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc


# ══════════════════════════════════════════════════════════════════
# ① 수집 — 발행 도메인까지 순회하며 누적하는가
# ══════════════════════════════════════════════════════════════════
class _FakeDriver:
    """도메인별로 다른 쿠키를 주는 가짜 브라우저 — 실제 selenium 의 계약을 흉내낸다.

    `get_cookies()` 가 *현재 문서에서 접근 가능한 것만* 준다는 성질이 핵심이다.
    """

    _BY_HOST = {
        "www.naver.com": [{"name": "NNB", "domain": ".naver.com"},
                          {"name": "NM_media_current", "domain": "www.naver.com"}],
        "nid.naver.com": [{"name": "NID_AUT", "domain": ".naver.com"},
                          {"name": "NID_SES", "domain": ".naver.com"},
                          {"name": "NID_JST", "domain": ".nid.naver.com"}],
        "blog.naver.com": [{"name": "BA_DEVICE", "domain": ".blog.naver.com"},
                           {"name": "JSESSIONID", "domain": "blog.naver.com"},
                           {"name": "NM_media_current", "domain": "blog.naver.com"}],
    }

    def __init__(self):
        self.visited = []
        self._host = ""

    def get(self, url):
        self.visited.append(url)
        for host in self._BY_HOST:
            if host in url:
                self._host = host
                return
        self._host = ""

    def get_cookies(self):
        return [dict(c) for c in self._BY_HOST.get(self._host, [])]


def test_수집이_발행_도메인까지_들른다(monkeypatch):
    """포털만 들르면 글쓰기 권한 쿠키가 통째로 빠진다 — 그 pkl 이 08-10 사고를 만들었다."""
    monkeypatch.setattr(nc.time, "sleep", lambda *_a, **_k: None)
    d = _FakeDriver()
    cookies = nc._harvest_cookies(d)

    names = {c["name"] for c in cookies}
    assert "NID_AUT" in names and "NID_SES" in names, "인증 쿠키를 못 담았다"
    assert "BA_DEVICE" in names and "JSESSIONID" in names, (
        "blog 도메인을 안 들렀다 — 포털 판정만 통과하고 글쓰기에서 튕기는 pkl 이 된다")
    assert "NNB" in names, "포털 쿠키가 덮어써져 사라졌다(누적이 아니라 대체)"


def test_같은_이름_다른_도메인_쿠키가_살아남는다(monkeypatch):
    """이름만 키로 쓰면 도메인이 다른 동명 쿠키가 조용히 하나 사라진다(실측 pkl 에 2개)."""
    monkeypatch.setattr(nc.time, "sleep", lambda *_a, **_k: None)
    cookies = nc._harvest_cookies(_FakeDriver())
    dup = [c for c in cookies if c["name"] == "NM_media_current"]
    assert len(dup) == 2, f"동명 쿠키가 {len(dup)}개만 남았다 — 도메인별 보존 실패"


def test_수집이_한_도메인_실패에도_나머지를_건진다(monkeypatch):
    """한 곳이 죽었다고 수집 전체를 버리면 회복 가능한 상황이 회복 불가가 된다."""
    monkeypatch.setattr(nc.time, "sleep", lambda *_a, **_k: None)

    class _Flaky(_FakeDriver):
        def get(self, url):
            if "nid.naver.com" in url:
                raise RuntimeError("일시 장애")
            super().get(url)

    cookies = nc._harvest_cookies(_Flaky())
    names = {c["name"] for c in cookies}
    assert "BA_DEVICE" in names, "한 도메인 실패가 뒤 도메인 수집까지 막았다"


def test_수집_진입점이_하나다():
    """수집이 3벌이라 서로 다른 도메인을 들렀던 것이 사고의 근본이다(원칙①).

    ★ 문자열 grep 이 아니라 **AST 호출 노드**로 센다 — 주석·docstring 은 AST 에 없다.
      소스 문자열 검사는 "코드가 어떻게 생겼나" 만 묻는 약한 테스트다(커밋 83860eb 교훈).
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(nc))
    sites = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "get_cookies"]
    assert len(sites) == 1, (
        f"쿠키 수집 호출이 {len(sites)}곳(줄 {[n.lineno for n in sites]}) — "
        f"단일 진입점이 깨졌다. 도메인 순회는 _harvest_cookies 단독이어야 한다.")

    # 그 하나가 정말 _harvest_cookies 안에 있는가 (다른 곳으로 옮겨가면 의미가 없다)
    harvest = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_harvest_cookies")
    inside = {getattr(n, "lineno", -1) for n in ast.walk(harvest)}
    assert sites[0].lineno in inside, "수집 호출이 _harvest_cookies 밖으로 나갔다"


# ══════════════════════════════════════════════════════════════════
# ② 판정 — 소비처와 같은 문을 두드리는가
# ══════════════════════════════════════════════════════════════════
def test_판정이_발행이_여는_화면을_친다(monkeypatch):
    """포털을 보고 유효라 하던 것이 08-10 의 '유효인데 적용 실패' 였다."""
    hit = {}

    def _fake_get(url, **kw):
        hit["url"] = url

        class _R:
            text = "<html>에디터</html>"
        return _R()

    import requests
    monkeypatch.setattr(requests, "get", _fake_get)
    assert nc._session_alive_http({"NID_AUT": "x"}, timeout=1) is True
    assert hit["url"] == nc.blog_write_url(), (
        f"판정이 {hit['url']} 를 쳤다 — 발행이 여는 화면이 아니다")
    assert "postwrite" in hit["url"], "글쓰기 화면이 아니다"


def test_로그인_바운스를_만료로_읽는다(monkeypatch):
    """미인증 응답은 에디터가 아니라 nidlogin 리다이렉트 스텁이다(실측 565바이트)."""
    import requests

    class _R:
        text = ('<script>var toGo="https://nid.naver.com/nidlogin.login?mode=form";'
                'top.location.href=toGo;</script>')
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    assert nc._session_alive_http({"NID_AUT": "x"}, timeout=1) is False


def test_판정불가를_유효로_단정하지_않는다(monkeypatch):
    """네트워크 오류를 '정상' 으로 적으면 건강진단이 거짓 초록이 된다(ERRORS [596])."""
    import requests

    def _boom(*a, **k):
        raise OSError("DNS 실패")
    monkeypatch.setattr(requests, "get", _boom)
    assert nc._session_alive_http({"NID_AUT": "x"}, timeout=1) is None


def test_인증쿠키_이름_판정이_한_곳이다():
    """`{"NID_AUT","NID_SES"} <= names` 가 네 곳에 복사돼 있었다(원칙①)."""
    assert nc.has_publish_auth([{"name": "NID_AUT"}, {"name": "NID_SES"}]) is True
    assert nc.has_publish_auth([{"name": "NID_AUT"}]) is False
    assert nc.has_publish_auth([]) is False
    assert nc.has_publish_auth(None) is False          # 깨진 입력을 통과시키지 않는다


def test_발행_URL_이_단일_소스다():
    """poster 가 URL 을 자기 안에 박으면 판정과 다시 어긋난다(②)."""
    from JARVIS08_PUBLISH.platforms import naver_poster
    assert naver_poster._blog_write_url() == nc.blog_write_url()


# ══════════════════════════════════════════════════════════════════
# ③ 무인 판정 — 프로세스·스레드 경계를 넘는가
# ══════════════════════════════════════════════════════════════════
def test_무인_판정이_새_스레드에서도_유효하다():
    """인시던트 재시도는 새 스레드로 돈다 — threading.local 문맥은 거기서 빈다."""
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("w", nc.human_wait_sec()))
    t.start()
    t.join()
    assert out["w"] == 0, "새 스레드에서 '사람이 있다'로 오판한다(08-10 07:33·07:37 경로)"


def test_무인_판정이_자식_프로세스에서도_유효하다():
    """발행은 subprocess 로 뜬다 — 이 경로가 08-10 07:07·07:11 에서 480초를 태웠다."""
    import subprocess, sys, os
    code = ("from JARVIS08_PUBLISH.credentials.naver_cookie_refresher import human_wait_sec;"
            "print(human_wait_sec())")
    env = dict(os.environ)
    env.pop("JARVIS_VERBOSE", None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.strip().splitlines()[-1] == "0", (
        f"자식 프로세스가 사람을 기다린다: {r.stdout.strip()[-200:]}")


# ══════════════════════════════════════════════════════════════════
# ④ 실패 격리 — 네이버가 티스토리를 죽이지 않는가
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("callback,runner", [
    ("run_self_repair_then_economic", "run_economic_poster"),
    ("run_self_repair_then_theme",    "run_radar_top_theme"),
])
def test_네이버_쿠키_실패가_발행_세트를_끊지_않는다(monkeypatch, callback, runner):
    """08-08·08-09 세 슬롯이 전부 '결손 2건: naver·tistory' 였던 이유(원칙③ 4조합)."""
    import JARVIS02_WRITER.scheduler as sch

    called = {}
    monkeypatch.setattr(sch, "_naver_cookie_ready", lambda *_a, **_k: False)
    monkeypatch.setattr(sch, runner, lambda *a, **k: called.setdefault("ran", True))
    monkeypatch.setattr(sch, "_clear_all_cookies", lambda *_a, **_k: None)
    monkeypatch.setattr(sch, "_run_self_repair_phase",
                        lambda *_a, **_k: {"code_changed": 0, "elapsed_sec": 0})
    monkeypatch.setattr(sch, "send_telegram", lambda *_a, **_k: None)
    monkeypatch.setattr(sch, "_is_locked_externally", lambda *_a, **_k: False)
    monkeypatch.setattr(sch, "_paused", False, raising=False)
    # 중복 차단·종료 레이스 가드는 이 테스트의 관심사가 아니다
    monkeypatch.setattr("JARVIS00_INFRA.harness.interpreter_shutting_down", lambda: False)

    getattr(sch, callback)()

    assert called.get("ran"), (
        "네이버 쿠키가 준비 안 됐다고 발행 세트 전체가 끊겼다 — 티스토리까지 죽는다")


# ══════════════════════════════════════════════════════════════════
# ⑤ 환경 오염 — import·쿠키조회가 남의 환경변수를 되돌리지 않는가
# ══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("mod", [
    "JARVIS08_PUBLISH.platforms.naver_poster",
    "JARVIS08_PUBLISH.platforms.tistory_poster",
    "JARVIS02_WRITER.economic_poster",
    "JARVIS03_RADAR.performance_collector",
])
def test_발행모듈_import_가_환경변수를_덮지_않는다(mod):
    """★ `load_dotenv(override=True)` 는 .env 의 *모든* 키로 프로세스 환경을 덮는다.

    실측 피해(2026-08-10): `tistory_poster` 모듈 로드 한 줄이 테스트가 격리해 둔
    `JARVIS_DB_PATH` 를 운영 경로로 되돌려, 발행 모듈을 import 하는 테스트가 생기자마자
    pytest 112건이 "테스트가 운영 DB 를 잡았다" 로 터졌다. 운영에서도 같다 —
    호출자가 의도적으로 세운 값이 조용히 .env 값으로 돌아간다.

    ★ 자식 프로세스에서 검증한다 — 부모는 이미 import 를 마쳐 오염이 재현되지 않는다.
    """
    import os
    import subprocess
    import sys

    sentinel = "/tmp/__jarvis_env_probe__/test.sqlite"
    code = (f"import os; os.environ['JARVIS_DB_PATH']={sentinel!r};"
            f"__import__({mod!r});"
            "print(os.environ['JARVIS_DB_PATH'])")
    env = dict(os.environ)
    env["JARVIS_DB_PATH"] = sentinel
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.strip().splitlines()[-1] == sentinel, (
        f"{mod} import 가 JARVIS_DB_PATH 를 덮었다 — load_dotenv(override=True) 를 의심하라")


def test_티스토리_쿠키_최신값_책임이_한_곳이다(monkeypatch, tmp_path):
    """갱신 직후 옛 값을 주면 호출자가 다시 `override=True` 를 앞세우게 된다(①)."""
    from JARVIS08_PUBLISH.credentials import login_manager as lm

    env_file = tmp_path / ".env"
    env_file.write_text('TS_COOKIE="파일에_있는_새값"\n', encoding="utf-8")
    monkeypatch.setattr(lm, "_PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("TS_COOKIE", "프로세스에_남은_옛값")

    assert lm.get_tistory_cookie() == "파일에_있는_새값", (
        "갱신된 .env 대신 프로세스에 남은 옛 값을 반환한다")

    # 발행자는 같은 답을 받아야 한다 (판정이 두 벌이면 다시 어긋난다)
    from JARVIS08_PUBLISH.platforms import tistory_poster
    assert tistory_poster._get_cookie() == lm.get_tistory_cookie()
