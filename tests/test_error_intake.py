"""오류 *접수* 의 정확성 — 골든 테스트 (2026-08-07).

두 가지를 못 박는다.
  ① 동시 실행 중복은 **실패가 아니다** — GUARDIAN 박제도 🚨 알림도 하지 않는다.
  ② 로그에서 주운 traceback 의 원인 위치는 **traceback 에서 파생** 한다 —
     '어디로 들어왔나'(daemon.log)를 '어디서 났나' 자리에 적지 않는다.

★ 별도 파일인 이유: 다른 세션이 `test_publish_golden.py` 를 동시에 수정 중이다.
★ 기계 독립: DB·데몬·네트워크를 건드리지 않는다 (ERRORS [568] 교훈).
"""
from __future__ import annotations

import ast
import io
import re
import threading
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _code_only(path: Path) -> str:
    """주석·독스트링을 뺀 코드 텍스트.

    소스 텍스트 검사는 *자기 설명 주석* 에 속는다 — 이 저장소에서 이미 세 번 났다.
    """
    src = path.read_text(encoding="utf-8")
    out, prev = [], tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and prev in (
                tokenize.INDENT, tokenize.NEWLINE, tokenize.NL):
            continue
        out.append(tok.string)
        if tok.type not in (tokenize.NL, tokenize.NEWLINE):
            prev = tok.type
    return " ".join(out)


# ══════════════════════════════════════════════════════════════════
# ① 동시 실행 중복은 사고가 아니다
# ══════════════════════════════════════════════════════════════════
def test_동시중복은_guardian도_알림도_부르지_않는다(monkeypatch):
    """실측(전체 이력 3건) 전부 *데몬 부팅 즉시 실행 + 주기 잡* 이 겹친 것이었다.

    고칠 코드가 없으므로 GUARDIAN 은 Tier1·2 를 반드시 실패하고, 사용자는
    조치할 것이 없는 "수동 검토 필요" 알림을 받는다. 둘 다 부르면 안 된다.
    """
    import JARVIS00_INFRA.harness as H

    calls = {"guardian": 0, "escalation": 0}
    monkeypatch.setattr(H, "_report_issues_to_guardian",
                        lambda *a, **k: calls.__setitem__("guardian", calls["guardian"] + 1))
    monkeypatch.setattr(H, "_notify_escalation",
                        lambda *a, **k: calls.__setitem__("escalation", calls["escalation"] + 1))

    started, hold = threading.Event(), threading.Event()

    def slow(_state):
        started.set()
        hold.wait(8)
        return {}

    act = H.ActionDefinition(
        name="동시성-골든", steps=[H.ActionStep(name="① 느린 단계", fn=slow)],
        verify=lambda s: [], send=lambda s: None)

    t = threading.Thread(target=lambda: H.run_action(act, {}), daemon=True)
    t.start()
    assert started.wait(5), "선행 실행이 시작되지 않았다"
    try:
        second = H.run_action(act, {})     # 겹치는 호출
    finally:
        hold.set()
        t.join(10)

    assert second.deferred is True, "동시 중복은 deferred(실패 아님)여야 한다"
    assert second.concurrent_blocked is True, "구조화 판정 필드가 서지 않았다"
    assert second.delivered is False, "송출은 하지 않는다"
    assert calls["guardian"] == 0, "고칠 코드가 없는데 GUARDIAN 을 불렀다"
    assert calls["escalation"] == 0, "조치할 것이 없는데 🚨 를 쐈다"


def _getattr_chain_order(path: Path, obj: str) -> list[str]:
    """`if getattr(<obj>, "X") ... elif getattr(<obj>, "Y")` 의 X·Y 를 **AST 로** 순서대로.

    ★ 텍스트 검색을 쓰면 안 된다 — 같은 이름의 *지역변수 초기화* 가 파일 앞쪽에 있어
      `find()` 가 분기가 아닌 그곳을 먼저 집는다(이 테스트 초판이 실제로 그렇게 속아
      변이를 놓쳤다). 판정은 **분기 조건 노드** 에서만 읽는다.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []

    def cond_attr(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == obj
                and isinstance(node.args[1], ast.Constant)):
            return node.args[1].value
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        cur = node
        chain: list[str] = []
        while isinstance(cur, ast.If):
            a = cond_attr(cur.test)
            if a:
                chain.append(a)
            cur = cur.orelse[0] if (len(cur.orelse) == 1
                                    and isinstance(cur.orelse[0], ast.If)) else None
        if len(chain) >= 2:
            out = chain
            break
    return out


def test_경제가_동시중복을_deferred보다_먼저_본다():
    """★ 순서가 뒤집히면 **인터리브 이중 발행** 이 열린다.

    동시 중복도 deferred 의 한 종류라, deferred 를 먼저 검사하면 동시중복 분기에
    영영 도달하지 못하고 `_concurrent_blocked` 가 안 서서 티스토리가 그대로 진행된다.
    """
    chain = _getattr_chain_order(ROOT / "JARVIS02_WRITER/economic_poster.py", "_nv_res")
    assert "concurrent_blocked" in chain and "deferred" in chain, (
        f"두 판정이 같은 if/elif 사슬에 있어야 한다 (실제: {chain})")
    assert chain.index("concurrent_blocked") < chain.index("deferred"), (
        f"deferred 를 먼저 검사한다 — 인터리브 이중 발행 위험 (순서: {chain})")


def test_동시중복_판정에_문자열_비교가_없다():
    """문구 한 글자만 바뀌어도 죽는 판정을 남기지 않는다(원칙②)."""
    for rel in ("JARVIS02_WRITER/economic_poster.py",
                "JARVIS02_WRITER/trend_theme_writer.py"):
        code = _code_only(ROOT / rel)
        assert not re.search(r'"동시 실행 중복 차단"\s*in', code), (
            f"{rel}: escalation_reason 문자열 비교로 동시중복을 판정한다")


# ══════════════════════════════════════════════════════════════════
# ② 로그에서 주운 오류의 '원인 위치' 는 traceback 에서 파생한다
# ══════════════════════════════════════════════════════════════════
def test_traceback에서_저장소_안쪽_마지막_프레임을_고른다():
    """맨 마지막 프레임을 쓰면 `.venv` 의 *남의 코드* 가 원인으로 박제된다."""
    from JARVIS07_GUARDIAN.error_collector import _tb_origin

    r = str(ROOT)
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{r}/JARVIS02_WRITER/economic_poster.py", line 100, in run\n'
        "    driver.click()\n"
        f'  File "{r}/.venv/lib/python3.10/site-packages/selenium/webdriver/x.py", line 55, in click\n'
        "    raise WebDriverException()\n"
        "selenium.common.exceptions.WebDriverException: boom"
    )
    assert _tb_origin(tb) == ("JARVIS02_WRITER/economic_poster.py", "run")


def test_실사고_traceback이_올바른_위치를_가리킨다():
    """2026-08-07 14:54 알림의 실물. 종전엔 module='daemon.log' 였다."""
    from JARVIS07_GUARDIAN.error_collector import _tb_origin

    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{ROOT}/JARVIS07_GUARDIAN/guardian_agent.py", line 486, in _try_sdk_targeted_fix\n'
        "    f\"traceback:\\n{(error_record.get('traceback', ''))[:2000]}\"\n"
        "TypeError: 'NoneType' object is not subscriptable"
    )
    assert _tb_origin(tb) == ("JARVIS07_GUARDIAN/guardian_agent.py", "_try_sdk_targeted_fix")


def test_저장소_밖만_있으면_빈값_폴백():
    """순수 라이브러리 크래시 — 위치를 지어내지 않는다. 호출부가 로그파일명으로 폴백."""
    from JARVIS07_GUARDIAN.error_collector import _tb_origin

    tb = ('Traceback (most recent call last):\n'
          '  File "/usr/lib/python3.10/threading.py", line 1, in run\n'
          "    x()\n"
          "RuntimeError: nope")
    assert _tb_origin(tb) == ("", "")


def test_로그스캐너가_파일명을_원인위치로_쓰지_않는다():
    """`module=log_file.name` 회귀 방지.

    그 한 줄 때문에 ① 같은 사건이 갈리고 ② Tier1·2 가 반드시 실패하고
    ③ **이미 고친 버그를 수동 검토하라**는 알림이 갔다.

    ★ AST 로 본다 — 텍스트 검사는 못 잡았다. 초판은 주석 제거기가 토큰을 공백으로
      이어붙여 `log_file.name` 이 `log_file . name` 이 되는 바람에 정규식이 빗나갔고,
      변이를 통과시켰다. `catch(...)` 호출의 `module=` **인자 노드**를 직접 읽는다.
    """
    tree = ast.parse((ROOT / "JARVIS07_GUARDIAN/error_collector.py").read_text(encoding="utf-8"))
    # ★ 범위를 `_scan_file` 안으로 좁힌다 — 이 규칙은 *로그에서 주운* 오류에만 적용된다.
    #   파일 전체의 `catch(...)` 7건에 걸면 정상 호출(예: 스레드 이름을 module 로 쓰는
    #   excepthook)이 걸려 테스트가 거짓 실패한다. 초판이 실제로 그렇게 실패했다.
    scan_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_scan_file"), None)
    assert scan_fn is not None, "_scan_file 을 찾지 못했다 — 테스트 갱신 필요"

    found = False
    for node in ast.walk(scan_fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "catch"):
            continue
        for kw in node.keywords:
            if kw.arg != "module":
                continue
            found = True
            # 허용: `_origin_mod or log_file.name` (파생 우선 + 폴백)
            # 금지: `log_file.name` 단독
            if isinstance(kw.value, ast.Attribute):
                base = kw.value.value
                assert not (isinstance(base, ast.Name) and base.id == "log_file"), (
                    "로그 파일명을 원인 위치로 그대로 쓴다 — traceback 파생을 우선할 것")
            if isinstance(kw.value, ast.BoolOp):
                names = [n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)]
                assert "_origin_mod" in names, (
                    f"module 이 traceback 파생값을 쓰지 않는다 (참조: {names})")
    assert found, "log_scanner 의 catch(module=...) 호출을 찾지 못했다 — 테스트 갱신 필요"


# ══════════════════════════════════════════════════════════════════
# ③ "내가 껐다" 와 "진짜 고장" 을 가른다 (사용자 박제 2026-08-07)
# ══════════════════════════════════════════════════════════════════
#   이 시스템은 개인 노트북에서 돈다. 사용자가 다른 일을 하다 노트북을 끄면 그 회차는
#   당연히 안 나간다 — **결함이 아니라 사실** 이다. 그걸 실패로 계상하면
#   ① 완결률이 기계 사용 습관을 뒤쫓고 ② 고칠 것 없는 일에 GUARDIAN 이 매번 열리고
#   ③ **진짜 고장이 그 소음에 묻힌다.**
def _fake_slot(monkeypatch, *, was_down: bool, worst_sec: int = 0):
    """`downtime_in_window` 를 대역으로 — 호출 인자도 함께 기록한다."""
    import JARVIS00_INFRA.downtime as D
    seen = {}

    def _fake(start, end):
        seen["start"], seen["end"] = start, end
        return (was_down, worst_sec)

    monkeypatch.setattr(D, "downtime_in_window", _fake)
    return seen


def test_전원_오프_슬롯은_조용히_기록되고_알림하지_않는다(monkeypatch):
    """💤 사용자가 끈 것 — 기록만 남기고 🚨 도 잡이력 보정도 하지 않는다."""
    import JARVIS08_PUBLISH.publish_ledger as L

    _fake_slot(monkeypatch, was_down=True, worst_sec=9000)
    monkeypatch.setattr(L, "slot_gaps", lambda now=None: ("economic", ["naver"], ["naver", "tistory"]))
    monkeypatch.setattr(L, "publishing_in_progress", lambda: False)
    monkeypatch.setattr(L, "scoring_gaps", lambda *a, **k: [])
    monkeypatch.setattr(L, "record_publish_gap", lambda *a, **k: True)
    sent = []
    import shared.notify as N
    monkeypatch.setattr(N, "send_tg", lambda *a, **k: sent.append(a))

    res = L.job_audit_publish_completeness()
    assert res.get("reason") == "daemon_down", f"전원 오프로 분류되지 않았다: {res}"
    assert res.get("job_row") == "skipped(daemon_down)", "잡 이력을 건드렸다"
    assert not sent, "전원 오프인데 🚨 를 쐈다"


def test_기계가_켜져_있었으면_진짜_실패로_남는다(monkeypatch):
    """🚨 같은 '글 0건' 이라도 기계가 살아 있었으면 코드 문제다 — 알려야 한다."""
    import JARVIS08_PUBLISH.publish_ledger as L

    _fake_slot(monkeypatch, was_down=False, worst_sec=180)
    monkeypatch.setattr(L, "slot_gaps", lambda now=None: ("economic", ["naver"], ["naver", "tistory"]))
    monkeypatch.setattr(L, "publishing_in_progress", lambda: False)
    monkeypatch.setattr(L, "scoring_gaps", lambda *a, **k: [])
    monkeypatch.setattr(L, "record_publish_gap", lambda *a, **k: True)
    monkeypatch.setattr(L, "recovery_hint", lambda pt: [])
    sent = []
    import shared.notify as N
    monkeypatch.setattr(N, "send_tg", lambda *a, **k: sent.append(a))

    res = L.job_audit_publish_completeness()
    assert res.get("reason") == "audit", f"진짜 실패로 분류되지 않았다: {res}"
    assert res.get("job_row") != "skipped(daemon_down)", "잡 이력 보정을 건너뛰었다"


def test_판정창은_슬롯_전체가_아니라_발행_필수구간이다(monkeypatch):
    """★ 창이 넓으면 **낮에 노트북 닫은 것이 아침의 진짜 실패를 덮는다**.

    경제 슬롯 창은 07:00~21:00(14시간)이다. 그 전체를 보면 15시에 한 번 닫은 것만으로
    07:00 의 코드 결함이 '전원 오프' 가 된다 — 진짜 고장을 전원 탓으로 돌리는
    이 방향의 오판이 반대(알림 한 번 더)보다 훨씬 나쁘다.
    """
    import JARVIS08_PUBLISH.publish_ledger as L

    seen = _fake_slot(monkeypatch, was_down=False)
    monkeypatch.setattr(L, "slot_gaps", lambda now=None: ("economic", ["naver"], ["naver", "tistory"]))
    monkeypatch.setattr(L, "publishing_in_progress", lambda: False)
    monkeypatch.setattr(L, "scoring_gaps", lambda *a, **k: [])
    monkeypatch.setattr(L, "record_publish_gap", lambda *a, **k: True)
    monkeypatch.setattr(L, "recovery_hint", lambda pt: [])
    import shared.notify as N
    monkeypatch.setattr(N, "send_tg", lambda *a, **k: None)

    L.job_audit_publish_completeness()
    span_min = (seen["end"] - seen["start"]).total_seconds() / 60
    slot = L.current_slot()
    full_min = (slot[2] - slot[1]).total_seconds() / 60 if slot else 840
    assert span_min < full_min, (
        f"판정창이 슬롯 전체({full_min:.0f}분)와 같다 — 발행 필수 구간으로 좁혀야 한다")
    # 발행이 실제로 필요한 시간(= audit_lag)보다 짧아도 안 된다
    from JARVIS04_SCHEDULER.job_registry import misfire_grace_for
    need = L.audit_lag_minutes(misfire_grace_for(L.publish_job_id("economic")))
    assert span_min >= min(need, full_min) - 1, (
        f"판정창 {span_min:.0f}분 < 발행 필수 {need}분 — 정상 지연을 실패로 볼 수 있다")


def test_생존신호를_못_읽으면_전원탓으로_돌리지_않는다(monkeypatch):
    """모르면 '꺼져 있었다' 고 하지 않는다 — 진짜 고장을 덮는 쪽이 더 나쁘다."""
    import JARVIS00_INFRA.downtime as D
    import JARVIS04_SCHEDULER.job_registry as R
    import datetime as _dt

    monkeypatch.setattr(R, "heartbeat_job_id", lambda: "")   # 파생 실패 상황
    down, worst = D.downtime_in_window(_dt.datetime(2026, 8, 7, 7),
                                       _dt.datetime(2026, 8, 7, 9))
    assert down is False and worst == 0, "판정 불가인데 전원 오프로 단정했다"


# ══════════════════════════════════════════════════════════════════
# ④ 자기 자신과 교착하던 파일락 (2026-08-07)
# ══════════════════════════════════════════════════════════════════
def test_파이프라인_기록이_자기자신과_교착하지_않는다(tmp_path, monkeypatch):
    """★ 실측 재현: 쓰기 1회에 **10.01초** 였다 (단독 락은 0.0001초).

    원인 — `pipeline_activity` 가 `json_store.locked()` 와 **같은 `.lock` 파일**에
    자체 flock 을 따로 걸었다. `_write()` → `write_json()` → `locked()` 로 중첩되는데
    flock 은 *open file description* 단위라 **같은 프로세스의 두 fd 도 서로를 막는다.**
    그 지연이 데몬 우아한 종료를 ~90초로 늘려 재시작이 실패하고 인스턴스가 2개가 됐다.

    ★ 동작으로 검사한다 — "fcntl 이 없는가" 같은 꼴 검사는 다른 방식으로 사본이
      되살아나면 못 잡는다. 느려지면 이 테스트가 즉시 죽는다.
    """
    import time
    import shared.pipeline_activity as PA

    monkeypatch.setattr(PA, "_DATA_FILE", tmp_path / "pipeline_activity.json")
    t0 = time.time()
    for i in range(3):
        PA.mark_active(f"golden-{i}")
    elapsed = time.time() - t0
    assert elapsed < 1.0, (
        f"쓰기 3회에 {elapsed:.1f}초 — 자기 잠금(회당 10초 타임아웃) 재발 의심")


def test_크로스프로세스_락_구현이_한_벌뿐이다():
    """락은 `json_store.locked()` 하나만 — 두 벌이면 서로를 막는다(원칙①).

    `json_store.locked()` 는 재진입 가드를 갖고 있고 그 docstring 이
    *"★ 재진입 필수 — 안 하면 자기 자신과 데드락"* 이라고 적어 두었다.
    사본을 만들면 그 가드가 따라오지 않는다.
    """
    code = _code_only(ROOT / "shared/pipeline_activity.py")
    assert "flock" not in code, (
        "pipeline_activity 가 자체 flock 을 다시 갖고 있다 — json_store.locked() 를 쓸 것")


# ══════════════════════════════════════════════════════════════════
# ⑤ Tier-2 브리지가 traceback=None 에 막히지 않는다
# ══════════════════════════════════════════════════════════════════
def test_traceback이_NULL이어도_Tier2_브리지에_닿는다(monkeypatch):
    """`error_log.traceback` 은 nullable — 실측 4,159/5,076(82%)이 NULL.

    `dict.get(k, default)` 는 키가 **있고 값이 None** 이면 기본값을 쓰지 않는다.
    그래서 프롬프트 조립의 `[:2000]` 이 TypeError 로 터졌고, `llm` arm 의 유일한
    보상 경로가 조용히 막힌 채 status 만 wontfix 로 쌓였다.

    ★ 단언을 'TypeError 가 안 난다' 로 쓰면 **안 된다** — 아래 `except Exception` 이
      그 예외를 잡아 기록만 하고 밖으로 안 던진다. 가드를 지워도 통과하는
      *가짜 회귀 테스트* 가 된다(메모리 뮤턴트로 실증: 도달 True/False 가 갈리는데
      예외 전파는 양쪽 다 False). **판별식은 브리지 도달 여부 하나뿐이다.**
    """
    import JARVIS07_GUARDIAN.guardian_agent as ga
    import JARVIS07_GUARDIAN.auto_repair as ar

    seen: list = []
    monkeypatch.setattr(ar, "run_auto_repair_targeted",
                        lambda **kw: (seen.append(kw), False)[1])
    if hasattr(ga, "_retry_original_job"):
        monkeypatch.setattr(ga, "_retry_original_job", lambda *a, **k: None)

    ga._try_sdk_targeted_fix(999999, {
        "traceback": None, "error_type": "X", "source": "s", "module": "m",
        "func_name": "f", "message": "m", "severity": "high"})

    assert len(seen) == 1, (
        "traceback=None 이 Tier-2 브리지를 막았다 — 밴딧 보상 경로가 끊긴다")


# ══════════════════════════════════════════════════════════════════
# ⑥ `.get(키, 기본값)[...]` 미가드 — 사전 검사 (2026-08-07)
# ══════════════════════════════════════════════════════════════════
#   `dict.get(k, D)` 는 *키가 없을 때만* D 를 쓴다. 키가 **있고 값이 None** 이면 None 이
#   그대로 나와 `[...]` 에서 TypeError. DB 의 NULL 이 정확히 이 꼴로 들어온다.
#   실제 피해: `guardian_agent._try_sdk_targeted_fix` 가 이 병으로 터져 Tier-2 브리지가
#   막혔고 7/27 이후 llm 시도 22건 중 18건이 wontfix 로 쌓였다.
def test_저장소에_미가드_get_첨자가_없다():
    """잔존 0 을 유지한다 — 하나라도 들어오면 여기서 죽는다.

    ★ 검사기(`--category symmetry`)와 **같은 판정을 다시 쓰지 않는다**(원칙①).
      검사기를 호출해 결과를 본다 — 판정 로직이 두 벌이 되면 서로 어긋난다.
    ★ **반환 객체를 본다** (2026-08-08 정정). 초판은 subprocess 의 `stdout` 을 봤는데
      `main()` 은 위반을 전부 `file=sys.stderr` 로 찍는다 — stdout 에는 성공 문구만 간다.
      그래서 `shared/precommit_check.py` 를 **통째로 지워도 이 테스트는 통과했다**
      (실측 변이: 검사기 mv 후 `1 passed`). 진짜 위반을 심어도 초록이었다.
      스트림·종료코드에 기대지 않고 `Report.violations` 를 직접 본다.
    """
    from shared.precommit_check import run

    rep = run(["symmetry"])
    assert "symmetry" in rep.ran, "symmetry 카테고리가 실행되지 않았다 — 검사가 무력"
    bad = [v for v in rep.violations
           if v.check_id == "symmetry/get-default-unguarded"]
    assert not bad, ("미가드 `.get(k,D)[...]` 가 남아 있다:\n"
                     + "\n".join(v.fmt() for v in bad[:20]))


def test_get_미가드_검사가_실제로_잡는다(tmp_path, monkeypatch):
    """검사기가 허수아비가 아닌지 — **배포되는 검사기 그 자체**에 위반을 먹여본다.

    ★ 초판은 판정 AST 로직을 이 테스트 안에 *베껴* 두고 그 사본을 채점했다.
      배포 검사기를 한 줄도 부르지 않아서, `shared/precommit_check.py` 를 통째로
      지운 상태에서도 초록이었다(실측 변이). CLAUDE.md **'복사본을 진실로 믿지 말 것'**
      정면 위반이고, 바로 위 테스트가 선언한 원칙①("판정을 두 벌 만들지 않는다")과도
      스스로 어긋났다.
    ★ 저장소 안에 임시 `.py` 를 만들지 않는다 — 다른 세션이 그 순간 커밋하면
      위반 상태가 커밋된다. 대신 **검사기의 탐색 뿌리를 임시 트리로 돌린다.**
      `_iter_py` 의 `root` 기본값은 *정의 시점* 에 묶이므로 `ROOT` 만 바꿔선 안 되고
      순회 자체도 함께 갈아끼워야 한다(실측).
    """
    from shared import precommit_check as pc

    cases = {
        "bad.py":  'def f(rec):\n    return rec.get("msg", "")[:50]\n',
        "good.py": 'def f(rec):\n    return (rec.get("msg") or "")[:50]\n',
        "env.py":  'import os\ndef g():\n    return os.environ.get("PATH", "")[:10]\n',
    }
    for name, src in cases.items():
        (tmp_path / name).write_text(src, encoding="utf-8")
    files = sorted(tmp_path.glob("*.py"))

    monkeypatch.setattr(pc, "ROOT", tmp_path)
    monkeypatch.setattr(pc, "_iter_py", lambda *a, **k: iter(files))
    rep = pc.Report()
    pc.check_symmetry(rep)

    hits = {v.file for v in rep.violations
            if v.check_id == "symmetry/get-default-unguarded"}
    assert "bad.py" in hits, "배포 검사기가 위반을 못 잡는다 — 허수아비다"
    assert "good.py" not in hits, "가드된 코드를 오탐한다"
    assert "env.py" not in hits, "os.environ 면제가 안 먹는다"
