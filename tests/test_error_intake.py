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
