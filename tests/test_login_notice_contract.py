"""로그인 실패 통보 — 2026-08-13 07:00 "사유: backoff" 회귀 방지.

사고 요약(실측):
  · 06:30 네이버 갱신이 CAPTCHA 로 실패 → 백오프(6h) 기록 + 사람 호출 알림 발송.
  · 07:00 경제 브리핑의 쿠키 게이트(`scheduler._naver_cookie_ready`)는 **bare
    `last_login_failure()`** 를 읽었다. 그 값은 백오프 창에서 즉시 거절된 시도의
    `backoff` 로 뭉개져 있었고, 안내문을 만들던 **어휘 dict** 의 키
    (`captcha_unattended`/`captcha_timeout`)와 매칭되지 않았다.
  · 결과: 사용자가 받은 문장은 `사유: backoff` 한 줄. 30분 전 캡차 알림과 같은 사고인지
    알 수 없고, "무엇을 하면 풀리는지" 도 없었다.

여기서 검증하는 것은 *코드 생김새* 가 아니라 **사용자에게 실제로 나가는 문장** 이다.
  · 실제 소비자(`scheduler._naver_cookie_ready`)를 그대로 호출한다 — 대역을 겨눠
    단언이 공허해진 전례(4cf23ba)를 반복하지 않는다.
  · `.env`·네트워크·브라우저에 기대지 않는다(로컬만 초록이던 전례 47b2574).
"""
from __future__ import annotations

import ast
import importlib
import warnings
from pathlib import Path

import textwrap

import pytest

from JARVIS08_PUBLISH.credentials import login_manager as lm
from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

ROOT = Path(__file__).resolve().parent.parent
CRED_DIR = ROOT / "JARVIS08_PUBLISH" / "credentials"


# ══════════════════════════════════════════════════════════════════
# 공통 — 소유자를 *찾아서* 조작한다 (승격 전/후 어느 쪽이든 같은 테스트가 돈다)
# ══════════════════════════════════════════════════════════════════
def _owner_modules():
    """로그인 상태의 주인 후보 — 승격처(login_manager)와 플랫폼 모듈."""
    return (lm, nc)


def _patch_backoff_file(monkeypatch, tmp_file: Path) -> dict:
    """백오프 상태 파일을 임시 경로로 돌린다 + **운영 파일 원본 경로를 돌려준다**.

    ★ 왜 이름을 박지 않나: 승격(플랫폼 중립화) 과정에서 상수 소유자가 옮겨간다.
      이름을 박아 두면 패치가 아무 데도 안 걸린 채 초록이 되거나(무력한 monkeypatch)
      **운영 백오프 파일을 진짜로 쓴다**(= 6시간 자동 로그인 차단). 둘 다 실측 위험이다.
    """
    originals: dict = {}
    for mod in _owner_modules():
        for name in dir(mod):
            if "BACKOFF_FILE" not in name.upper():
                continue
            cur = getattr(mod, name)
            if not isinstance(cur, (str, Path)):
                continue
            originals[f"{mod.__name__}.{name}"] = Path(cur)
            monkeypatch.setattr(mod, name, tmp_file)
    assert originals, (
        "백오프 상태 파일 상수를 어느 소유자에서도 못 찾았다 — 이름이 바뀌었다면 "
        "이 테스트는 운영 파일을 건드리게 된다")
    return originals


def _mark_backoff(platform: str, reason: str) -> None:
    """백오프를 건다 — 플랫폼 중립 진입점(`login_manager`) 하나만 쓴다(①)."""
    lm.mark_login_backoff(platform, reason)


def _domain_reasons() -> frozenset:
    """로그인 도메인이 소유한 *사유 어휘* — 목록을 여기 박지 않는다(②)."""
    got: set = set()
    for p in _platforms():
        got |= set(lm.human_required_reasons(p))
    got |= set(nc.CAPTCHA_REASONS)                      # 네이버 고유 사유(주인은 여전히 네이버)
    assert got, "사유 어휘의 주인을 찾지 못했다"
    return frozenset(got)


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """`_naver_cookie_ready` 를 **실제로 호출**하고 사용자에게 나간 문장을 돌려준다."""
    from JARVIS02_WRITER import scheduler as sched
    from JARVIS07_GUARDIAN import error_collector as ec

    sent: list = []
    monkeypatch.setattr(sched, "send_telegram", lambda m: sent.append(m))
    monkeypatch.setattr(sched, "log", lambda *a, **k: None)   # 운영 로그 파일 보호
    monkeypatch.setattr(ec, "report", lambda *a, **k: None)   # 원장 오염 방지

    tmp_bo = tmp_path / "login_backoff.json"
    originals = _patch_backoff_file(monkeypatch, tmp_bo)
    prod_before = {
        p: (p.exists(), p.stat().st_mtime if p.exists() else 0.0)
        for p in set(originals.values())
    }

    def _run(why: str):
        monkeypatch.setattr(lm, "ensure_naver_ready", lambda **_k: (False, why))
        sent.clear()
        ok = sched._naver_cookie_ready("테스트")
        assert ok is False
        assert len(sent) == 1, f"통보가 정확히 1건이어야 한다: {sent}"
        return sent[0]

    _run.tmp_backoff = tmp_bo          # type: ignore[attr-defined]
    yield _run

    # ★ 운영 백오프 파일을 건드리지 않았는가 — 건드렸다면 6시간 자동 로그인이 막힌다.
    for p, (existed, mtime) in prod_before.items():
        now = p.exists()
        assert now == existed and (not now or p.stat().st_mtime == mtime), (
            f"테스트가 운영 백오프 파일을 건드렸다: {p}")


# ══════════════════════════════════════════════════════════════════
# ① 백오프 창 — 사유가 뭉개지지 않고, 사람이 읽을 안내가 붙는다
# ══════════════════════════════════════════════════════════════════
def test_백오프_창에서_사유가_backoff_로_뭉개지지_않는다(gate, monkeypatch):
    _mark_backoff("naver", "captcha_unattended")
    assert gate.tmp_backoff.exists(), (
        "백오프 기록이 임시 경로에 안 갔다 — monkeypatch 가 먹지 않았다(R6)")

    # 이번 프로세스는 백오프에 걸려 즉시 거절당한 상태 = `backoff` (08-13 07:00 그대로 재현)
    monkeypatch.setattr(nc, "_LAST_FAILURE", "backoff", raising=False)

    msg = gate("permanent")

    assert "captcha_unattended" in msg, (
        "근본 사유(캡차)가 사라졌다 — bare last_login_failure() 로 되돌아갔다")
    assert "backoff" not in msg, (
        "사유가 `backoff` 로 뭉개졌다 — 30분 전 캡차 알림과 같은 사고인 줄 알 수 없다")


def test_백오프_창_통보에_해제_방법이_붙는다(gate, monkeypatch):
    """"6시간 기다리면 되나" 오독을 막는 문장이 실제로 나가는가.

    해제 주체는 시간이 아니라 사람이다(`clear_login_backoff` 는 로그인 성공이 부른다).
    """
    _mark_backoff("naver", "captcha_unattended")
    monkeypatch.setattr(nc, "_LAST_FAILURE", "backoff", raising=False)

    msg = gate("permanent")

    assert "해제" in msg, (
        "'사람이 직접 로그인하면 즉시 해제' 안내가 빠졌다 — 어휘 dict 시절 "
        "`backoff` 사유에서 안내문이 통째로 사라졌던 그 자리다")


def test_안내문은_소비처가_아니라_사유의_주인이_만든다(gate, monkeypatch):
    """문장의 출처를 못 박는다 — 주인이 준 문장이 그대로 사용자에게 가야 한다.

    소비처가 사유별 문장을 다시 조립하기 시작하면(어휘 dict 부활) 이 센티널이 사라진다.
    """
    _mark_backoff("naver", "captcha_unattended")
    monkeypatch.setattr(nc, "_LAST_FAILURE", "backoff", raising=False)

    sentinel = "SENTINEL-도메인이-만든-안내문"
    monkeypatch.setattr(lm, "human_action_hint", lambda *a, **k: sentinel)

    assert sentinel in gate("permanent"), (
        "소비처가 로그인 도메인의 안내문을 쓰지 않는다 — 자기가 만든 문장을 쓰고 있다")


def test_네트워크_실패에는_사람_호출_안내를_붙이지_않는다(gate, monkeypatch):
    """사람이 할 일이 없는 실패에 '직접 로그인하라' 를 붙이면 진짜 경보가 죽는다."""
    monkeypatch.setattr(nc, "_LAST_FAILURE", "network_down", raising=False)

    msg = gate("network")

    assert "network_down" in msg
    assert "해제" not in msg, "네트워크 단절에 백오프 해제 안내가 붙었다"


# ══════════════════════════════════════════════════════════════════
# ② 어휘 목록 부활 방지 — 사유 문자열은 주인 밖에 살지 않는다
# ══════════════════════════════════════════════════════════════════
def test_로그인_사유_어휘가_소비처에_없다():
    """사유 → 안내문 dict 가 어디서든 되살아나면 빨개진다.

    ★ 왜 문자열 스캔인가: 어휘를 소비처에 두면 *새 사유가 생길 때 조용히 낡는다*.
      실제로 `backoff` 사유가 생겼을 때 안내문이 통째로 사라졌다. 대상 어휘 목록은
      주인에게서 파생하므로(②) 사유가 늘어도 이 검사는 자동으로 따라온다.
    """
    from tests.conftest import is_scannable_source

    reasons = _domain_reasons()
    offenders: list = []
    for path in ROOT.rglob("*.py"):
        if not is_scannable_source(path, ROOT):
            continue
        rel = path.relative_to(ROOT)
        if rel.parts[0] == "tests" or str(rel).startswith("JARVIS08_PUBLISH/credentials"):
            continue                       # 주인(그리고 주인을 검사하는 테스트)은 가져도 된다
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")   # 남의 파일 정규식 이스케이프 경고까지 떠안지 않는다
                tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:                  # noqa: BLE001
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in reasons:
                offenders.append(f"{rel}:{node.lineno} {node.value!r}")
    assert not offenders, (
        "로그인 실패 사유 어휘가 소비처에 박혔다 — 사유가 늘면 조용히 낡는다:\n"
        + "\n".join(offenders))


def test_사람개입_판정이_어휘목록이_아니다():
    """티스토리의 한국어 키워드 나열(`_HUMAN_INTERVENTION_KEYWORDS`)은 ② 위반이다.

    네이버는 같은 판정을 이미 *꼴* 로 한다(`captcha_present` — 선택자 + 3-상태).
    낱말 판정은 실측으로 한 번 무너졌다: 캡차 없는 평상시 로그인 페이지에도
    'captcha' 7회·'보안' 2회가 들어 있어 판정이 **항상 참** 이었다(ERRORS [595]).
    """
    ts = importlib.import_module(
        "JARVIS08_PUBLISH.credentials.tistory_cookie_refresher")
    assert not hasattr(ts, "_HUMAN_INTERVENTION_KEYWORDS"), (
        "낱말 나열로 사람 개입을 판정한다 — 문구가 바뀌면 조용히 무력해진다")


# ══════════════════════════════════════════════════════════════════
# ③ 4조합 대칭 — 사람 호출·백오프가 네이버·티스토리 양쪽에 있는가
# ══════════════════════════════════════════════════════════════════
def _platforms() -> tuple:
    """플랫폼 목록을 박지 않는다 — 주인에게 묻고, 없으면 파일 이름 규약에서 파생(②)."""
    try:
        return tuple(lm.platforms())
    except Exception:                                   # noqa: BLE001
        return tuple(sorted(p.name[: -len("_cookie_refresher.py")]
                            for p in CRED_DIR.glob("*_cookie_refresher.py")))


def test_사람_행동안내가_모든_플랫폼에서_나온다():
    """중립 표면(③) — 어떤 플랫폼이든 '사람이 필요한 사유' 엔 행동 안내가 붙는다.

    네이버에만 안내가 붙으면 티스토리 사고는 **소리 없이** 지나간다.
    """
    plats = _platforms()
    assert len(plats) >= 2, f"플랫폼 파생이 무너졌다: {plats}"
    for p in plats:
        reasons = lm.human_required_reasons(p)
        assert reasons, f"{p}: 사람이 필요한 사유가 0개 — 사람을 부를 길이 없다"
        for r in sorted(reasons):
            hint = lm.human_action_hint(p, r)
            assert hint.strip(), f"{p}/{r}: 행동 안내가 비었다"
            assert lm.recovery_command(p) in hint, (
                f"{p}/{r}: 복구 명령이 빠졌다 — 사용자가 무엇을 실행할지 모른다")
        assert lm.human_action_hint(p, "some_code_defect") == "", (
            f"{p}: 코드 결함 사유에까지 '직접 로그인하라' 를 붙인다 — 진짜 경보가 죽는다")


def test_플랫폼_모듈이_실패사유_배관을_갖춘다():
    """중립 API 는 *이름 규약* 으로 플랫폼 모듈의 배관을 읽는다 — 그 배관이 없으면
    `current_login_failure_reason()` 은 언제나 빈 문자열이고, 사람 호출도 사유를 잃는다.

    지금 티스토리 쿠키는 실제로 만료 상태인데(오케스트레이터 실측
    `verify_all_logins()` → tistory ok=False) 사유를 남기는 배관이 0곳이다.
    """
    missing: list = []
    for p in _platforms():
        mod = lm._platform_module(p)
        if mod is None:
            missing.append(f"{p}: 모듈 없음")
            continue
        for name in ("last_login_failure", "HUMAN_REQUIRED_REASONS"):
            if not hasattr(mod, name):
                missing.append(f"{p}_cookie_refresher.{name}")

    assert not missing, "플랫폼 간 로그인 배관이 비대칭이다(③):\n" + "\n".join(missing)


def test_사람필요_타입이_모든_플랫폼에서_Tier2를_안_태운다():
    """사람이 풀어야 하는 실패를 GUARDIAN 이 '코드 결함' 으로 보면 매 창마다 LLM 수리
    세션을 태운다. 네이버는 이미 갈라냈다 — 티스토리도 같아야 한다(③).

    ★ 판정 주인은 `severity.is_transient` 이고, 그 근거는 로그인 도메인의
      `human_required_reasons(platform)` 이다. 여기서 목록을 박지 않는다(②).
    """
    from JARVIS07_GUARDIAN.severity import is_transient

    naver_types = {lm.login_error_type("naver", r)
                   for r in lm.human_required_reasons("naver")}
    for et in sorted(naver_types):
        assert is_transient(et, source="publish") is True, (
            f"{et}: 사람이 필요한 실패인데 Tier-2 낭비 대상으로 남는다")

    gaps = [lm.login_error_type(p, r)
            for p in _platforms() if p != "naver"
            for r in sorted(lm.human_required_reasons(p))
            if is_transient(lm.login_error_type(p, r), source="publish") is not True]
    if gaps:
        pytest.xfail(
            "GUARDIAN 분류가 아직 네이버 전용이다 — `severity._naver_login_human_required_types()` "
            "가 플랫폼 중립으로 파생해야 한다(Group D 잔여): " + ", ".join(gaps))
    assert not gaps


# ══════════════════════════════════════════════════════════════════
# ④ 지속성 — '지금 열린다' 와 '내일도 열린다' 는 다른 질문이다
# ══════════════════════════════════════════════════════════════════
_SESSION_ONLY = [{"name": "NID_AUT"}, {"name": "NID_SES"}]      # 실측 pkl 그대로(expiry 없음)


def test_세션전용_쿠키가_발행게이트를_조이지_않는다():
    """★ R1 — 여기를 조이면 네이버 경제·테마가 **즉시 전면 중단**된다.

    현재 `naver_cookies.pkl` 의 NID_AUT·NID_SES 는 100% 세션 쿠키(실측)다.
    `has_publish_auth` 가 지속성까지 요구하는 순간 `check_cookie_valid` →
    `verify_all_logins` → harness precondition → `_naver_cookie_ready` 가 연쇄로
    False 가 되고, 복구 경로는 캡차·백오프로 막혀 있어 자력 복귀가 불가능하다.
    """
    assert nc.has_publish_auth(_SESSION_ONLY) is True, (
        "지속성 판정이 발행 게이트를 조였다 — 세션 쿠키뿐이어도 *지금은* 발행된다")


def test_세션전용_쿠키를_지속가능으로_보고하지_않는다():
    """'지금 열린다' 를 '내일도 열린다' 로 보고하면 반쪽 복구가 성공으로 통과한다."""
    # ★ 2026-08-13 — `pytest.skip("미착륙")` 유예 제거. 착륙했는데도 유예가 남아 있으면
    #   `auth_persistence` 를 통째로 지워도 **실패가 아니라 skip** 으로 스위트가 초록을 유지한다.
    #   직전 커밋 8267a61('조용한 무력화를 막겠다고 넣은 장치가 조용히 무력화됐다')이 겨눈 그 패턴이다.
    reporter = getattr(nc, "auth_persistence", None)
    assert callable(reporter), "auth_persistence() 가 없다 — 지속성 판정이 사라졌다"

    got = reporter(_SESSION_ONLY)
    assert got["durable"] is False, "세션 쿠키뿐인데 지속 가능하다고 보고한다"
    assert set(got["session_only"]) == {"NID_AUT", "NID_SES"}
    assert reporter([])["durable"] is None, (
        "판정 불가('모름')를 '아님'으로 단정한다 — 커밋 4e09141 의 교훈")

# ══════════════════════════════════════════════════════════════════
#  배선 검사 — 판정 함수가 있어도 *불리지 않으면* 없는 것과 같다
# ══════════════════════════════════════════════════════════════════
def test_로그인상태유지_체크가_실제로_배선돼_있다():
    """★ 이번 사고의 **뿌리** 인데 회귀 방지가 0건이었다.

    `enable_keep_login()` 로직만 테스트하고 *호출되는지* 는 아무도 안 보면,
    다음 세션이 호출 두 줄을 지워도 스위트가 초록이다. 코드 존재는 적용의 증거가 아니다.
    자동 폼로그인과 수동 로그인 **양쪽** 에 걸려 있어야 한다 —
    자동은 캡차에 막히므로 실제로 지속 쿠키를 심는 것은 수동 경로다.
    """
    import ast
    import inspect

    for fn_name in ("refresh_naver_cookies", "manual_login_and_save"):
        fn = getattr(nc, fn_name, None)
        assert fn is not None, f"{fn_name} 이 없다"
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "enable_keep_login" in called, (
            f"{fn_name} 이 '로그인 상태 유지' 를 켜지 않는다 — 세션 쿠키만 저장돼 반나절 뒤 죽는다")


def test_세션전용_경고가_저장경로에_배선돼_있다():
    """`_warn_if_session_only` 는 체크박스가 실제로 먹었는지 재는 **유일한 계측점** 이다.
    저장 단일 진입점에서 불리지 않으면 지속성 결여를 아무도 모른다."""
    import ast
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(nc._save_cookies)))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_warn_if_session_only" in called, (
        "_save_cookies 가 지속성 계측을 부르지 않는다 — 체크박스가 죽어도 조용하다")

def test_티스토리_HTTP판정은_모름을_만료로_적지_않는다():
    """★ 실측 오탐 6/6 (2026-08-13).

    `cookie_valid_http()` 는 `TSSESSION` 단독 HTTP 로 manage 에 접근하는데, 그 경로로는
    유효한 쿠키도 로그인으로 리다이렉트된다(브라우저 UA 를 붙여도 동일 — 직접 요청해 확인).
    그 무능을 '만료' 로 적어 하루 2회 거짓 경보를 냈고, 그때마다 한 시간 뒤 발행은 성공했다:
      08-10 07:24 · 21:30 / 08-11 21:32 / 08-12 07:27 · 21:39 / 08-13 07:28  (6/6)
    함수 docstring 스스로 "'모른다' 를 '만료' 로 적으면 거짓 경보가 된다" 고 적어 놓고
    정작 그러고 있었다. 판정이 필요하면 소비자와 같은 방식(`check_cookie_valid(driver)`)을 쓴다(①).
    """
    import types

    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    fake = types.SimpleNamespace(
        status_code=302,
        headers={"Location": "https://www.tistory.com/auth/login?redirectUrl=x"})
    monkey = pytest.MonkeyPatch()
    try:
        import requests as _req
        monkey.setattr(_req, "get", lambda *a, **k: fake)
        monkey.setenv("TS_COOKIE", "dummy-cookie-value")
        got = tc.cookie_valid_http()
    finally:
        monkey.undo()
    assert got is None, (
        f"로그인 리다이렉트를 '만료'({got})로 단정한다 — 유효한 쿠키에 거짓 경보가 난다")


def test_verify_all_logins_가_판정불가로_ok를_깎지_않는다(monkeypatch):
    """'모름' 이 `issues` 로 새면 발행 게이트가 멀쩡한 티스토리를 막는다.

    ★ `.env` 에 기대지 않는다 (커밋 47b2574 — '내 테스트가 .env 에 기대 로컬에서만 초록').
      CI 에는 자격증명이 없어 env 누락 issue 가 먼저 쌓인다. 그것은 이 테스트의 관심사가
      아니므로 필요한 env 를 대역으로 채우고, **판정 불가가 issues 를 늘리는지** 만 본다.
    """
    from JARVIS08_PUBLISH.credentials import login_manager as lm
    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    for _k in lm._REQUIRED_ENV["tistory"]:
        monkeypatch.setenv(_k, "dummy-for-test")
    monkeypatch.setattr(tc, "cookie_valid_http", lambda *a, **k: None)

    got = lm.verify_all_logins(platforms=("tistory",))["tistory"]
    assert got["ok"] is True, f"판정 불가인데 ok=False 로 깎였다: {got['issues']}"

    # 대조군 — 진짜 만료(False)는 여전히 걸러야 한다(과소차단 방지)
    monkeypatch.setattr(tc, "cookie_valid_http", lambda *a, **k: False)
    bad = lm.verify_all_logins(platforms=("tistory",))["tistory"]
    assert bad["ok"] is False, "진짜 만료까지 통과시킨다 — 오탐을 고치다 과소차단을 만들었다"

def test_한_플랫폼_로그인_성공이_다른_플랫폼_백오프를_지우지_않는다():
    """★ 실기능 버그였다 (2026-08-13) — 백오프 파일이 플랫폼 공유가 됐는데
    `clear_login_backoff()` 가 `unlink()` 로 파일을 **통째** 지웠다. 그래서 네이버가
    로그인에 성공할 때마다(정상 경로 `_save_cookies`) 티스토리의 '사람 필요' 상태가
    소리 없이 사라졌다 — 사람이 손봐야 할 때를 놓친다."""
    from JARVIS08_PUBLISH.credentials import login_manager as lm
    from JARVIS08_PUBLISH.credentials import naver_cookie_refresher as nc

    lm.mark_login_backoff("naver", "captcha_unattended")
    lm.mark_login_backoff("tistory", "human_intervention")
    try:
        nc.clear_login_backoff()          # 네이버 로그인 성공 경로
        assert lm.login_backoff_active_reason("naver") == "", "네이버 백오프가 안 풀렸다"
        assert lm.login_backoff_active_reason("tistory") == "human_intervention", (
            "네이버 성공이 티스토리 백오프까지 지웠다 — 다른 플랫폼 상태를 건드리면 안 된다")
    finally:
        for _p in ("naver", "tistory"):
            lm.clear_login_backoff(_p)


def test_하네스_전제조건이_플랫폼_중립이다():
    """③원칙 — 종전엔 `if platform == "naver"` 가드 때문에 티스토리가 뭉뚱그린
    `login_invalid` 로 떨어져 하네스가 '사람이 필요한가' 를 구분하지 못했다.
    경제·테마 **양쪽** 을 확인한다(한쪽만 고치면 ③위반).

    ★ 두 검증 함수는 harness 액션 안의 **중첩 함수** 라 모듈 속성으로 잡히지 않는다 —
      AST 로 그 함수 노드를 찾아 그 안만 본다(파일 전체를 훑으면 무관한 분기에 걸린다).
    """
    import ast
    from pathlib import Path as _P

    ROOT = _P(__file__).resolve().parent.parent
    for rel, fn_name in (("JARVIS02_WRITER/economic_poster.py", "_verify_platform"),
                         ("JARVIS02_WRITER/trend_theme_writer.py", "_verify_theme_platform")):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == fn_name), None)
        assert node is not None, f"{rel}::{fn_name} 을 찾지 못했다"
        guards = [n for n in ast.walk(node)
                  if isinstance(n, ast.Compare)
                  and any(isinstance(c, ast.Constant) and c.value == "naver"
                          for c in n.comparators)]
        assert not guards, (
            f"{rel}::{fn_name} 에 네이버 전용 가드가 남아 있다 — "
            f"티스토리는 사람 필요 여부를 구분받지 못한다(③위반)")
        seg = ast.get_source_segment((ROOT / rel).read_text(encoding="utf-8"), node) or ""
        assert "login_manager" in seg, (
            f"{fn_name} 이 플랫폼 중립 판정(login_manager)을 쓰지 않는다")

def test_판정불가면_브라우저_실확인에_위임한다(monkeypatch):
    """★ 오탐을 고치다 만든 **과소차단** 회귀 (2026-08-13).

    `TSSESSION` 단독 HTTP 로는 유효/만료를 원리적으로 못 가린다(엔드포인트 6종·리다이렉트
    추적·도메인 쿠키까지 실측 — 전부 유효=무효 동일). 그래서 `None`(판정 불가)로 고쳤는데,
    `auto_refresh_if_needed` 가 `None` 을 **아무 일 안 함** 으로 읽어 만료 탐지가 0 이 됐다
    (실측: `deadbeef` 무효 쿠키도 통과). 정확한 판정은 소비자와 같은 방식(브라우저)에 있다.
    """
    from JARVIS08_PUBLISH.credentials import login_manager as lm
    from JARVIS08_PUBLISH.credentials import tistory_cookie_refresher as tc

    called: list = []
    monkeypatch.setattr(lm, "get_tistory_cookie", lambda: "dummy")
    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda *a, detail=False, **k: (None, "indeterminate") if detail else None)
    monkeypatch.setattr(lm, "refresh_tistory_cookies",
                        lambda *a, **k: called.append(True) or True)

    lm.auto_refresh_if_needed(platforms=("tistory",))
    assert called, (
        "원리적 판정 불가인데 브라우저 실확인을 하지 않는다 — 만료를 아무도 못 잡는다")

    # ★ 대조군 — **순단**(network)은 건드리면 안 된다. 순단마다 로그인하면 캡차를 부른다.
    called.clear()
    monkeypatch.setattr(tc, "cookie_valid_http",
                        lambda *a, detail=False, **k: (None, "network") if detail else None)
    lm.auto_refresh_if_needed(platforms=("tistory",))
    assert not called, "네트워크 순단에 로그인을 시도한다 — 그게 캡차를 부른다"


def test_고아_세션행이_영구차단을_만들지_않는다():
    """★ C-3 수정이 만든 자기잠금 (2026-08-13 실증).

    세션 도중 프로세스가 죽으면 `outcome` 이 영원히 NULL 이다(`record_outcome` 은 finally —
    SIGKILL·재부팅엔 안 돈다). 그것을 무조건 '지금 도는 중' 으로 읽으면 gap≈0 이 되어
    **모든 자율 SDK 수리가 영구 차단**된다. 사유가 비영구(cooldown)라 오류는 `new` 로 남아
    10분마다 영원히 재시도되고, 풀리려면 새 allowed 행이 필요한데 그 행이 바로 차단당한다.
    """
    rb = _gate_budget()
    rec = {"error_type": "__OrphanSmoke__", "message": "x", "source": "w", "id": -1}
    aid = rb.record_attempt(error_record=rec, caller="g", job_id="j", decision="allowed")
    con = rb._db()
    con.execute("UPDATE sdk_repair_attempts SET ts=datetime('now','localtime','-3 hours') "
                "WHERE id=?", (aid,))
    con.commit()

    gap = rb._sec_since_last_allowed()
    cap = rb._session_cap_sec()
    assert gap is not None and gap > cap, (
        f"3시간 전 고아 행이 '도는 중' 으로 읽힌다 (경과 {gap}초) — 영구 차단이 된다")

    # 반대로 **방금 시작한** 세션은 여전히 막아야 한다(과소차단 방지)
    rb.record_attempt(error_record=rec, caller="g", job_id="j", decision="allowed")
    assert rb._sec_since_last_allowed() < cap, "도는 중인 세션을 종료로 본다 — 동시 실행이 열린다"


def test_비용귀속창이_쿨다운_노브에_흔들리지_않는다(monkeypatch):
    """C-7 재발 방지 — 세션 길이 파생이 한 파일에 두 벌이면 노브 하나로 회계가 뒤집힌다."""
    rb = _gate_budget()
    import ast
    import inspect

    base = rb._session_cap_sec()
    monkeypatch.setenv(rb.COOLDOWN_ENV, "1")
    assert rb._session_cap_sec() == base, (
        "쿨다운 노브를 바꿨더니 세션 길이가 따라 변했다 — 비용 귀속이 흔들린다")

    # ★ 판별력 — 상수만 보면 *사용처* 가 쿨다운을 쓰도록 되돌려도 초록이다(뮤테이션 실측).
    #   비용 귀속 질의가 어느 함수에서 창을 파생하는지 소스로 고정한다.
    src = textwrap.dedent(inspect.getsource(rb._cost_24h))
    called = {n.func.id for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_session_cap_sec" in called, "비용 귀속 창을 세션 길이에서 파생하지 않는다"
    assert "_cooldown_sec" not in called, (
        "비용 귀속 창이 쿨다운 노브에서 파생된다 — 노브를 낮추면 380초·$3.00 세션이 $0.00 이 된다")


def _gate_budget():
    from JARVIS07_GUARDIAN import repair_budget
    return repair_budget

