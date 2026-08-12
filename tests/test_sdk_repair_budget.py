"""자율 SDK 자가수리 브레이크 — 회귀 방지 (사용자 박제 2026-08-12).

★ 무엇이 터졌나
  최근 7일 LLM 지출의 **약 50%** 가 사건 구동 SDK 자가수리였다 (`llm_token_usage` 실측:
  `sdk_query` 54회 · 1.4억 토큰 · **$81.62**). 상한은 `guardian_agent` 경로에만 있었고
  `incident_responder` 경로엔 **없었다** — 같은 비싼 자원인데 문이 한쪽에만 달려 있었다
  (③원칙 위반). 게다가 캡차처럼 *코드로 고칠 수 없는* 사유에도 10분짜리 세션이 돌았다.

★ 이 파일이 지키는 것 (되살아나면 **빨개진다**)
  1. 같은 지문이 상한을 넘으면 SDK 를 태우지 않는다
  2. 사람 개입이 필요한 사유는 SDK 를 태우지 않는다
  3. 24시간 예산을 넘으면 차단된다
  4. **차단은 조용하지 않다** — 로그·텔레그램·오류 status 가 남는다
  5. 사용자 승인 구동·주 1회 심층 감사는 **차단되지 않는다** (과차단 방지)
  6. 판정은 촉점 **한 곳** — 호출자마다 붙이면 그게 이번에 고친 결함의 형태다
  7. `precommit --category sdkguard` 가 *가드 없는 새 통로* 를 실제로 잡는다

★ 왜 대역이 아니라 **실제 소비자 심볼** 을 겨누는가
  대역을 겨눈 단언은 공허하다(전례 4cf23ba). 그래서 게이트는 `repair_budget` 의 공개
  API 를, 촉점은 `auto_repair.run_auto_repair_targeted` 를 **그대로** 부른다.
  외부 의존(.env·네트워크·실 SDK)은 하나도 쓰지 않는다(전례 47b2574).
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ══════════════════════════════════════════════════════════════════
#  공용 — 게이트 owner 를 *실물* 로 가져온다 (없으면 그 사실이 실패로 드러난다)
# ══════════════════════════════════════════════════════════════════
def _gate():
    """게이트 owner 모듈. 없으면 **skip 이 아니라 실패** — 없는 것이 곧 결함이다."""
    try:
        from JARVIS07_GUARDIAN import repair_budget
    except ImportError as e:      # pragma: no cover - 도입 전에만 도달
        pytest.fail(
            f"자율 SDK 수리 게이트(JARVIS07_GUARDIAN/repair_budget.py)가 없다 — "
            f"비싼 자원에 브레이크가 없는 상태다: {e}")
    return repair_budget


def _rec(**kw) -> dict:
    """오류 레코드 — 기본은 ①경로(incident_responder)가 만드는 합성 레코드 꼴."""
    base = {
        "id": -1,
        "source": "incident_responder",
        "module": "posting_pipeline",
        "error_type": "PostingFailure",
        "message": "네이버 발행 실패",
        "severity": "high",
    }
    base.update(kw)
    return base


def _seed(rb, *, n: int, message_prefix: str, decision: str = "allowed") -> None:
    """장부에 시도 기록을 심는다 — 실제 소비자 API(`record_attempt`)로만."""
    for i in range(n):
        rb.record_attempt(
            error_record=_rec(error_type="NameError",
                              message=f"{message_prefix}{i} is not defined"),
            caller="incident", job_id="theme", decision=decision)


@pytest.fixture(autouse=True)
def _isolate_ledger(monkeypatch):
    """레그를 하나씩 겨눈다 — 다른 레그가 먼저 물어서 *엉뚱한 이유로 초록* 이 되지 않게.

    쿨다운·일일예산은 각 테스트가 필요할 때만 켠다. 값은 전부 환경변수 노브
    (`killswitch` 계약)로 조절 — 코드에 임계값을 박지 않는다.
    """
    monkeypatch.setenv("GUARDIAN_SDK_REPAIR_COOLDOWN_SEC", "0")
    monkeypatch.setenv("GUARDIAN_SDK_DAILY_CALLS", "9999")
    yield


# ══════════════════════════════════════════════════════════════════
#  1) 게이트가 존재하고, 판정을 *공개* 한다
# ══════════════════════════════════════════════════════════════════
def test_게이트_owner가_판정과_장부를_공개한다():
    rb = _gate()
    for name in ("sdk_repair_block_reason", "record_attempt", "record_outcome",
                 "budget_state", "status_line"):
        assert callable(getattr(rb, name, None)), f"게이트 공개 API 누락: {name}"

    # 판정 함수의 반환은 '차단 사유(str) 또는 통과(None)' 여야 한다.
    # precommit `sdkguard` 레그가 **이 주석에서** 판정 함수를 파생한다 —
    # 주석이 사라지면 검사가 조용히 무력화되므로 여기서 함께 지킨다.
    src = (_ROOT / "JARVIS07_GUARDIAN/repair_budget.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "sdk_repair_block_reason")
    ann = ast.unparse(fn.returns) if fn.returns is not None else ""
    assert "str" in ann and ("None" in ann or "Optional" in ann), \
        f"판정 반환 주석이 `str | None` 이 아니다: {ann!r} — sdkguard 파생이 끊긴다"


# ══════════════════════════════════════════════════════════════════
#  2) 사람 개입이 필요한 사유는 SDK 를 태우지 않는다 (①경로 합성 레코드)
# ══════════════════════════════════════════════════════════════════
_HUMAN_CTX = "• [naver] nv_precondition: login_invalid_backoff: 캡차 백오프 대기 중"


def test_사람개입_사유는_판정에서_차단된다():
    rb = _gate()
    why = rb.sdk_repair_block_reason(_rec(), context=_HUMAN_CTX,
                                     job_id="theme", caller="incident")
    assert why, "캡차 백오프(사람이 직접 풀어야 하는 것)에 SDK 를 태우려 한다"
    assert isinstance(why, str) and why.strip(), "사유가 비었다 — 사람이 읽을 수 있어야 한다"


# ══════════════════════════════════════════════════════════════════
#  2-B) Tier-2 금지 사유(critical 등)는 게이트가 상속해 막는다  ★ L1
#
#  ★ 왜 이 테스트가 필요한가 (뮤테이션 실측 2026-08-12)
#    L1 레그를 통째로 `if False:` 로 무력화해도 **21건이 전부 초록이었다** — 즉 그 레그는
#    지워도 아무도 모르는 상태였다. 계약은 "①경로는 severity='high' 고정이라 실질 no-op
#    이지만 걸어두지 않으면 ③위반(같은 문이 한쪽에만)" 이라 했는데, 그 '걸어둠' 자체를
#    지키는 장치가 없었다. 검사받지 않는 가드는 있으나 마나다.
#  ★ 판정의 주인은 guardian_agent.tier2_blocked_reason — 여기서 그 판단을 복제하지 않고
#    "게이트가 그것을 *상속하는가*" 만 본다(①).
# ══════════════════════════════════════════════════════════════════
def test_Tier2_금지사유를_게이트가_상속한다():
    rb = _gate()
    from JARVIS07_GUARDIAN.guardian_agent import tier2_blocked_reason

    rec = _rec(severity="critical")
    owner = tier2_blocked_reason(rec)
    assert owner, "전제 깨짐 — tier2_blocked_reason 이 critical 을 더는 막지 않는다"

    why = rb.sdk_repair_block_reason(rec, context="", job_id="economic", caller="guardian")
    assert why, "owner 가 금지한 사유인데 게이트가 SDK 를 태우려 한다(L1 미상속)"
    assert why.startswith(rb.R_TIER2 + ":"), f"L1 분류가 아니다: {why[:60]}"
    assert owner in why, "owner 의 사유 원문이 사라졌다 — 사람이 이유를 못 읽는다"


def test_사람개입_사유면_SDK가_실제로_호출되지_않는다(monkeypatch):
    """★ 코드 존재는 적용의 증거가 아니다 — *촉점을 통과시켜* 호출 0을 확인한다."""
    import shared.claude_sdk_compat as cc

    from JARVIS07_GUARDIAN import auto_repair as ar

    called: list = []
    monkeypatch.setattr(cc, "run_sdk_query",
                        lambda *a, **k: called.append(k) or {
                            "returncode": 0, "stdout": "", "elapsed": 0})

    fixed = ar.run_auto_repair_targeted(
        context=_HUMAN_CTX, job_id="theme", failed_platforms=["naver"],
        error_record=_rec(error_type="NaverLoginCaptchaTimeout", message="captcha"))
    assert fixed is False, "차단됐는데 수정했다고 보고한다"
    assert called == [], f"게이트를 지나고도 SDK 가 {len(called)}회 호출됐다"


# ══════════════════════════════════════════════════════════════════
#  3) 같은 지문이 상한을 넘으면 차단된다 (①경로에 없던 문)
# ══════════════════════════════════════════════════════════════════
def test_같은_지문이_상한을_넘으면_차단된다():
    rb = _gate()
    from JARVIS07_GUARDIAN.architecture import MAX_LLM_ATTEMPTS

    probe = _rec(error_type="NameError", message="name totally_unique_probe is not defined")
    assert rb.sdk_repair_block_reason(probe) is None, \
        "처음 보는 코드버그를 막는다 — 과차단이다"

    for _ in range(MAX_LLM_ATTEMPTS):
        rb.record_attempt(error_record=probe, caller="incident",
                          job_id="theme", decision="allowed")

    why = rb.sdk_repair_block_reason(probe)
    assert why, f"같은 지문을 {MAX_LLM_ATTEMPTS}회 태우고도 또 태운다 — 상한이 없다"
    assert str(MAX_LLM_ATTEMPTS) in why, f"상한이 사유에 안 보인다: {why!r}"


def test_고쳐진_지문은_예산을_갉아먹지_않는다():
    """성공한 시도까지 상한에 세면, 잘 고쳐지는 오류가 스스로 문을 닫는다."""
    rb = _gate()
    from JARVIS07_GUARDIAN.architecture import MAX_LLM_ATTEMPTS

    probe = _rec(error_type="NameError", message="name fixed_probe is not defined")
    for _ in range(MAX_LLM_ATTEMPTS + 2):
        aid = rb.record_attempt(error_record=probe, caller="incident",
                                job_id="theme", decision="allowed")
        rb.record_outcome(aid, fixed=True, elapsed_sec=1)

    assert rb.sdk_repair_block_reason(probe) is None, \
        "성공한 시도를 상한에 세고 있다 — 고쳐지는 오류가 막힌다"


# ══════════════════════════════════════════════════════════════════
#  4) 24시간 예산을 넘으면 차단된다
# ══════════════════════════════════════════════════════════════════
def test_일일예산_초과시_차단된다(monkeypatch):
    rb = _gate()
    monkeypatch.setenv("GUARDIAN_SDK_DAILY_CALLS", "2")
    _seed(rb, n=2, message_prefix="name daily_budget_probe_")

    probe = _rec(error_type="NameError", message="name fresh_after_budget is not defined")
    why = rb.sdk_repair_block_reason(probe)
    assert why, "24시간 예산을 소진했는데 또 태운다"
    assert "2" in why, f"소진 상태가 사유에 안 보인다: {why!r}"

    # 무배포 상향이 실제로 먹어야 한다 (사람이 손으로 풀 수 있는 통로)
    monkeypatch.setenv("GUARDIAN_SDK_DAILY_CALLS", "9999")
    assert rb.sdk_repair_block_reason(probe) is None, \
        "GUARDIAN_SDK_DAILY_CALLS 상향이 먹지 않는다 — 사람이 풀 방법이 없다"


def test_쿨다운은_프로세스_경계를_넘는다(monkeypatch):
    """★ 메모리 카운터는 subprocess 발행 경로를 못 막는다 — 장부(DB)로 판정해야 한다."""
    rb = _gate()
    monkeypatch.setenv("GUARDIAN_SDK_REPAIR_COOLDOWN_SEC", "600")
    rb.record_attempt(error_record=_rec(error_type="NameError", message="name cd is not defined"),
                      caller="guardian", job_id="writer", decision="allowed")

    probe = _rec(error_type="NameError", message="name cooldown_probe is not defined")
    assert rb.sdk_repair_block_reason(probe), "직전 세션 직후인데 곧바로 또 태운다"

    monkeypatch.setenv("GUARDIAN_SDK_REPAIR_COOLDOWN_SEC", "0")
    assert rb.sdk_repair_block_reason(probe) is None, "쿨다운 노브가 먹지 않는다"


# ══════════════════════════════════════════════════════════════════
#  5) 과차단 방지 — 진짜 코드버그·사용자 승인·주 1회 감사는 막지 않는다
# ══════════════════════════════════════════════════════════════════
def test_진짜_코드버그는_통과한다():
    rb = _gate()
    assert rb.sdk_repair_block_reason(
        {"id": 1, "source": "writer", "error_type": "NameError",
         "message": "name unseen_symbol is not defined", "severity": "high"}) is None, \
        "고칠 수 있는 코드버그를 막는다 — 자율 수리를 통째로 죽인 셈이다"


def test_킬스위치가_실제로_먹는다(monkeypatch):
    rb = _gate()
    monkeypatch.setenv(getattr(rb, "GATE_ENV", "GUARDIAN_SDK_REPAIR_GATE"), "0")
    assert rb.sdk_repair_block_reason(_rec(), context=_HUMAN_CTX) is None, \
        "킬스위치를 내렸는데 여전히 막는다 — 라이브에서 되돌릴 방법이 없다"


def test_사용자승인_구동과_주1회_감사는_게이트를_지나지_않는다():
    """자율 수리만 막는다. 사용자가 ✅ 한 위임과 심의된 주 1회 감사는 대상이 아니다."""
    gate_src = (_ROOT / "JARVIS07_GUARDIAN/repair_budget.py").read_text(encoding="utf-8")
    judge = "sdk_repair_block_reason"
    assert judge in gate_src

    def _called(path: Path, fn_name: str | None = None) -> set:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scope = tree
        if fn_name:
            scope = next(n for n in tree.body
                         if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        return {getattr(c.func, "id", "") or getattr(c.func, "attr", "")
                for c in ast.walk(scope) if isinstance(c, ast.Call)}

    # 주석은 위반이 아니다 — *호출* 만 본다
    assert judge not in _called(_ROOT / "JARVIS01_MASTER/agent_tools.py"), \
        "사용자 텔레그램 승인 구동 위임에 자율 수리 게이트가 걸렸다 — 과차단"

    assert judge not in _called(_ROOT / "JARVIS07_GUARDIAN/auto_repair.py",
                                "run_auto_repair"), \
        "주 1회 심층 감사(j07_deep_audit)에 자율 수리 게이트가 걸렸다 — 과차단"


# ══════════════════════════════════════════════════════════════════
#  6) 차단은 조용하지 않다 — 조용한 차단은 조용한 낭비만큼 나쁘다
# ══════════════════════════════════════════════════════════════════
def test_차단은_조용하지_않다(monkeypatch, caplog):
    """로그 + 텔레그램 + 오류 status — 사람이 손봐야 할 때를 놓치지 않게."""
    import logging

    import shared.notify as _notify
    from shared import db as _db

    rb = _gate()

    sent: list = []
    monkeypatch.setattr(_notify, "send_tg", lambda text, *a, **k: sent.append(text))
    if getattr(rb, "send_tg", None) is not None:      # 모듈 상단 import 형태도 덮는다
        monkeypatch.setattr(rb, "send_tg", lambda text, *a, **k: sent.append(text))

    eid = _db.save_error(source="incident_responder", error_type="NaverLoginCaptchaTimeout",
                         message="캡차 대기 시간 초과", module="naver_poster",
                         severity="high")
    assert eid and eid > 0, "장부 검증용 오류 행을 못 만들었다"
    rec = _rec(id=eid, error_type="NaverLoginCaptchaTimeout",
               message="캡차 대기 시간 초과", source="incident_responder")

    why = rb.sdk_repair_block_reason(rec, context=_HUMAN_CTX, job_id="theme",
                                     caller="guardian")
    assert why, "영구 사유인데 통과시켰다"

    with caplog.at_level(logging.WARNING):
        rb.record_attempt(error_record=rec, caller="guardian", job_id="theme",
                          decision="blocked", reason=why)

    # ① 로그
    assert any("RepairBudget" in r.getMessage() or why[:20] in r.getMessage()
               for r in caplog.records), "차단이 로그에 안 남는다"
    # ② 텔레그램
    assert sent, "차단이 텔레그램에 안 알려진다 — 사람이 손봐야 할 때를 놓친다"
    # ③ 오류 status (②경로 = DB 행이 있는 경우)
    row = _db.get_error(eid) or {}
    assert row.get("status") in ("ignored", "wontfix"), \
        f"영구 사유인데 오류가 계속 재시도 큐에 남는다: status={row.get('status')!r}"
    assert (row.get("resolution") or "").strip(), "왜 막았는지가 비었다"
    # ④ 장부
    st = rb.budget_state()
    assert isinstance(st, dict) and st.get("blocked_24h", 0) >= 1, \
        f"차단이 장부에 안 쌓인다: {st}"
    assert "자율 SDK" in rb.status_line() or "SDK" in rb.status_line(), \
        f"/status 한 줄에 예산이 안 보인다: {rb.status_line()!r}"


# ══════════════════════════════════════════════════════════════════
#  7) 판정은 촉점 한 곳 — 호출자마다 붙이면 그게 이번에 고친 결함의 형태다
# ══════════════════════════════════════════════════════════════════
def test_판정은_촉점_한곳에서만_일어난다():
    from conftest import is_scannable_source   # 제외 규칙 단일 소유자 (원칙①)

    judge = "sdk_repair_block_reason"
    holders: set = set()
    for p in _ROOT.rglob("*.py"):
        rel = p.relative_to(_ROOT).as_posix()
        if not is_scannable_source(p, _ROOT):
            continue
        if rel.startswith("tests/") or rel == "JARVIS07_GUARDIAN/repair_budget.py":
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if judge not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any((getattr(c.func, "id", "") or getattr(c.func, "attr", "")) == judge
                   for c in ast.walk(fn) if isinstance(c, ast.Call)):
                holders.add(f"{rel}::{fn.name}")

    assert holders == {"JARVIS07_GUARDIAN/auto_repair.py::run_auto_repair_targeted"}, \
        f"판정이 촉점 밖에도 있다(또는 촉점에 없다): {sorted(holders)}"


# ══════════════════════════════════════════════════════════════════
#  8) precommit `sdkguard` — 가드 없는 *새 통로* 를 실제로 잡는가
#     ★ 검사가 존재한다는 것과 잡는다는 것은 다르다 (collect/self-check 의 교훈:
#       검사를 넣고도 조용히 실패해 늘 통과하던 전례가 있었다). 합성 트리로 실증한다.
# ══════════════════════════════════════════════════════════════════
_TREE = {
    "shared/claude_sdk_compat.py": """
        def run_sdk_query(prompt: str, model=None, timeout: int = 300) -> dict:
            return {"returncode": 0}
        def build_oauth_env() -> dict:
            return {}
        __all__ = ["run_sdk_query", "build_oauth_env"]
    """,
    "JARVIS04_SCHEDULER/job_registry.py": """
        DEFAULT_JOBS = [
            {"id": "j07_deep_audit", "trigger": "cron",
             "callback": "PKGX.weekly.job_deep_audit"},
        ]
    """,
    "PKGX/weekly.py": """
        from shared.claude_sdk_compat import run_sdk_query
        def job_deep_audit():
            run_deep_audit_body()
        def run_deep_audit_body():
            return run_sdk_query(prompt="주 1회 심층 감사")
    """,
    "JARVIS07_GUARDIAN/repair_budget.py": """
        def sdk_repair_block_reason(rec, *, context="", job_id="", caller="") -> str | None:
            return None
        def record_attempt(**kw) -> int:
            return 0
        def status_line() -> str:
            return ""
        __all__ = ["sdk_repair_block_reason", "record_attempt", "status_line"]
    """,
    "JARVIS07_GUARDIAN/auto_repair.py": """
        from shared.claude_sdk_compat import run_sdk_query
        from JARVIS07_GUARDIAN.repair_budget import record_attempt, sdk_repair_block_reason
        def run_auto_repair_targeted(context, job_id):
            why = sdk_repair_block_reason(None, context=context)
            if why:
                record_attempt(decision="blocked")
                return False
            return run_sdk_query(prompt=context)
    """,
    "JARVIS07_GUARDIAN/guardian_agent.py": """
        def _status_section():
            from JARVIS07_GUARDIAN.repair_budget import status_line
            return status_line()
    """,
    "JARVIS01_MASTER/agent_tools.py": """
        from shared.claude_sdk_compat import run_sdk_query
        def register_tool(**kw):
            def deco(f):
                return f
            return deco

        @register_tool(side_effect="external", requires_approval=True)
        def delegate_to_claude_code(prompt):
            return run_sdk_query(prompt=prompt)
    """,
}


def _build_tree(root: Path, overrides: dict | None = None,
                remove: tuple = ()) -> Path:
    files = dict(_TREE)
    files.update(overrides or {})
    for rel in remove:
        files.pop(rel, None)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "shared/precommit_check.py").write_text(
        (_ROOT / "shared/precommit_check.py").read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _run_leg(root: Path) -> tuple[int, set]:
    r = subprocess.run(
        [sys.executable, str(root / "shared/precommit_check.py"),
         "--category", "sdkguard"],
        capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    ids = set(__import__("re").findall(r"\[(sdkguard/[a-z-]+)\]", out))
    return r.returncode, ids


def test_레그가_정상_배치를_통과시킨다(tmp_path):
    rc, ids = _run_leg(_build_tree(tmp_path / "ok"))
    assert rc == 0 and not ids, f"정상 배치를 막는다: rc={rc} {ids}"


def test_레그가_가드_없는_새_통로를_잡는다(tmp_path):
    """★ 이번 결함 그 자체 — 비싼 자원으로 가는 통로가 가드 없이 하나 더 생긴 상태."""
    root = _build_tree(tmp_path / "newpath", overrides={
        "JARVIS07_GUARDIAN/incident_responder.py": """
            from shared.claude_sdk_compat import run_sdk_query
            def respond(error_text):
                return run_sdk_query(prompt=error_text)
        """})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/unguarded-path" in ids, \
        f"가드 없는 새 통로를 못 잡는다: rc={rc} {ids}"


def test_레그가_촉점에서_가드가_사라진_것을_잡는다(tmp_path):
    root = _build_tree(tmp_path / "removed", overrides={
        "JARVIS07_GUARDIAN/auto_repair.py": """
            from shared.claude_sdk_compat import run_sdk_query
            def run_auto_repair_targeted(context, job_id):
                return run_sdk_query(prompt=context)
        """})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/unguarded-path" in ids, \
        f"촉점의 가드 삭제를 못 잡는다: rc={rc} {ids}"


def test_레그가_지출_뒤에_놓인_가드를_잡는다(tmp_path):
    root = _build_tree(tmp_path / "late", overrides={
        "JARVIS07_GUARDIAN/auto_repair.py": """
            from shared.claude_sdk_compat import run_sdk_query
            from JARVIS07_GUARDIAN.repair_budget import sdk_repair_block_reason
            def run_auto_repair_targeted(context, job_id):
                out = run_sdk_query(prompt=context)
                if sdk_repair_block_reason(None, context=context):
                    return False
                return out
        """})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/guard-after-spend" in ids, \
        f"이미 쓴 뒤의 가드를 못 잡는다: rc={rc} {ids}"


def test_레그가_판정_사본을_잡는다(tmp_path):
    root = _build_tree(tmp_path / "dup", overrides={
        "JARVIS07_GUARDIAN/guardian_agent.py": """
            from JARVIS07_GUARDIAN.repair_budget import sdk_repair_block_reason
            def _try_sdk_targeted_fix(error_id, error_record):
                if sdk_repair_block_reason(error_record):
                    return False
                return True
        """})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/duplicate-judgment" in ids, \
        f"판정 사본을 못 잡는다: rc={rc} {ids}"


def test_레그가_게이트_실종을_보고한다(tmp_path):
    """게이트를 지워도 조용하지 않다 — 경고는 화면에 남는다."""
    root = _build_tree(tmp_path / "gone", remove=("JARVIS07_GUARDIAN/repair_budget.py",))
    rc, ids = _run_leg(root)
    assert "sdkguard/gate-missing" in ids, f"게이트가 사라졌는데 조용하다: rc={rc} {ids}"


def test_레그가_검사_전제_붕괴를_통과로_처리하지_않는다(tmp_path):
    """fail-closed — 비싼 자원 owner 를 못 읽으면 통과가 아니라 위반이다."""
    root = _build_tree(tmp_path / "broken", overrides={
        "shared/claude_sdk_compat.py": "def build_oauth_env():\n    return {}\n"})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/self-check" in ids, \
        f"검사 전제가 무너졌는데 조용히 통과한다: rc={rc} {ids}"


def test_레그가_이름만_같은_통로를_면제하지_않는다(tmp_path):
    """★ 면제는 `파일::함수` 자격으로 — 이름만 같으면 새 통로가 조용히 빠져나간다.

    실측: 이름만 모으면 심의된 이름이 194개였다. 그 안의 흔한 이름(`report`·`_run`…)을
    새 함수가 우연히 쓰는 순간 검사가 스스로 구멍을 뚫는다.
    """
    root = _build_tree(tmp_path / "namedup", overrides={
        "JARVIS07_GUARDIAN/incident_responder.py": """
            from shared.claude_sdk_compat import run_sdk_query
            def run_deep_audit_body():
                return run_sdk_query(prompt="심의된 통로와 이름만 같다")
        """})
    rc, ids = _run_leg(root)
    assert rc == 1 and "sdkguard/unguarded-path" in ids, \
        f"이름이 같다는 이유로 새 통로를 면제한다: rc={rc} {ids}"


def test_레그가_중첩헬퍼를_무가드로_오판하지_않는다(tmp_path):
    """가드는 바깥 함수에, 지출은 안쪽 헬퍼에 — 실제 `run_auto_repair` 의 꼴이다.

    중첩 def 를 따로 세면 ① 무가드로 오판하고 ② 같은 지출을 두 번 계상한다.
    """
    root = _build_tree(tmp_path / "nested", overrides={
        "JARVIS07_GUARDIAN/auto_repair.py": """
            from shared.claude_sdk_compat import run_sdk_query
            from JARVIS07_GUARDIAN.repair_budget import sdk_repair_block_reason
            def run_auto_repair_targeted(context, job_id):
                if sdk_repair_block_reason(None, context=context):
                    return False
                def _step_run_cli():
                    return run_sdk_query(prompt=context)
                return _step_run_cli()
        """})
    rc, ids = _run_leg(root)
    assert rc == 0 and not ids, f"중첩 헬퍼를 별도 통로로 오판한다: rc={rc} {ids}"

def _fingerprint(rec: dict) -> str:
    from JARVIS07_GUARDIAN.pattern_fixer import fingerprint_of
    return fingerprint_of(rec)

# ══════════════════════════════════════════════════════════════════
#  2-C) 지속되는 '일시적' 은 일시적이 아니다 — 딱 한 번 조사를 허용한다  ★ L2 탈출구
#
#  ★ 왜 (사용자 판단 2026-08-12)
#    L2 는 분류 실패(unknown)를 '일시적' 으로 보고 막는다. 내용상 대개 옳다.
#    그러나 **항상** 막으면 *아직 타입이 안 붙은 진짜 새 버그* 가 영원히 자동수리를 못 받고,
#    지문·패턴·학습 자산이 생기지 않아 자가 학습 루프가 새 유형에 대해 시작조차 못 한다.
#    비대칭: 잘못 허용 = 세션 1회(상한 있음) / 잘못 차단 = 새 유형 영구 배제(상한 없음).
#
#  ★ 이 차단이 애초에 *우연* 이었다는 점도 기록해 둔다 — severity.py 의 Layer4 정규식에 든
#    리터럴 `발행 실패` 와 `_make_error_record` 가 합성하는 메시지가 겹쳐서 걸린다.
#    (severity 도메인 별건으로 ERRORS 박제. 여기서 그 정규식을 건드리면 ③위반)
# ══════════════════════════════════════════════════════════════════
def test_지속되는_일시적_지문은_한_번_조사를_받는다(monkeypatch, tmp_path):
    rb = _gate()
    from JARVIS07_GUARDIAN.architecture import MAX_LLM_ATTEMPTS as LIM

    rec = _rec(error_type="PostingFailure", message="발행 실패", source="incident_responder")
    assert rb.sdk_repair_block_reason(rec, context="", job_id="economic",
                                      caller="incident"), "전제 깨짐 — L2 가 더는 막지 않는다"

    seen = []
    for _ in range(LIM + 2):
        why = rb.sdk_repair_block_reason(rec, context="", job_id="economic", caller="incident")
        seen.append(None if why is None else why.split(":", 1)[0])
        rb.record_attempt(error_record=rec, caller="incident", job_id="economic",
                          decision=("blocked" if why else "allowed"), reason=(why or ""))

    allowed_at = [i for i, v in enumerate(seen) if v is None]
    assert len(allowed_at) == 1, (
        f"탈출구가 정확히 1회여야 한다 — 허용 시점 {allowed_at} / 전체 {seen}")
    assert allowed_at[0] == LIM, (
        f"상한({LIM})만큼 차단된 *뒤에* 열려야 한다 — 실제 {allowed_at[0]}번째")
    assert all(v == rb.R_HUMAN for i, v in enumerate(seen) if i != allowed_at[0]), (
        f"탈출 전후는 L2 차단이어야 한다: {seen}")


def test_한_번_허용된_지문은_다시_탈출하지_않는다():
    """무한 탈출 방지 — 허용 이력이 생기면 탈출 조건은 영구히 거짓이다."""
    rb = _gate()
    from JARVIS07_GUARDIAN.architecture import (ERROR_STATS_WINDOW_DAYS as DAYS,
                                                MAX_LLM_ATTEMPTS as LIM)
    rec = _rec(error_type="PostingFailure", message="발행 실패", source="incident_responder")
    fp = _fingerprint(rec)
    for _ in range(LIM * 3):
        rb.record_attempt(error_record=rec, caller="incident", job_id="economic",
                          decision="blocked", reason=f"{rb.R_HUMAN}:테스트")
    rb.record_attempt(error_record=rec, caller="incident", job_id="economic",
                      decision="allowed", reason="")
    assert not rb._persistent_unknown(fp, DAYS, LIM), (
        "허용 이력이 있는데도 탈출 조건이 참이다 — 매 회차 세션이 열려 상한이 무의미해진다")

# ══════════════════════════════════════════════════════════════════
#  적대적 심사(2026-08-12) 차단 4건 회귀 — B1·B2·B3
# ══════════════════════════════════════════════════════════════════
def test_B1_운영_쿨다운에서도_차단_알림이_실제로_나간다(monkeypatch):
    """★ 노브에 기대지 않는다 — 이 파일의 autouse 픽스처는 쿨다운을 0 으로 박는다.

    그 노브 때문에 초록이던 사이, 운영(600초)에서는 알림이 **100% 안 나갔다**:
    차단 행을 INSERT·commit 한 뒤 억제 질의의 max(ts) 가 *방금 넣은 그 행* 을 포함해
    gap≈0 → 항상 조기 return. 레이스가 아니라 결정론이다.
    (커밋 47b2574 '내 테스트가 .env 에 기대 로컬에서만 초록이었다' 와 같은 병)
    """
    rb = _gate()
    monkeypatch.delenv(rb.COOLDOWN_ENV, raising=False)      # ← 운영 기본값으로 되돌린다
    assert rb._cooldown_sec() > 0, "전제 깨짐 — 운영 쿨다운이 0 이면 이 테스트는 무의미하다"

    sent: list = []
    import shared.notify as _nt
    monkeypatch.setattr(_nt, "send_tg", lambda m, *a, **k: sent.append(m))

    # ★ 지문을 이 테스트 전용으로 — 앞선 테스트가 남긴 같은 (분류,지문) 행이 억제에 걸린다.
    #   장부는 프로세스 경계를 넘으라고 일부러 공유 상태다. 격리는 지문으로 한다.
    rec = _rec(error_type="__B1NotifySmoke__", message="captcha", source="harness")
    rb.record_attempt(error_record=rec, caller="incident", job_id="theme",
                      decision="blocked", reason=f"{rb.R_HUMAN}:캡차")
    assert len(sent) == 1, "운영 쿨다운에서 차단 알림이 나가지 않는다 — 조용한 차단"

    rb.record_attempt(error_record=rec, caller="incident", job_id="theme",
                      decision="blocked", reason=f"{rb.R_HUMAN}:캡차")
    assert len(sent) == 1, "같은 (분류,지문) 이 창 안에서 반복 발송됐다 — 억제가 죽었다"


def test_B2_구조화_kind_가_텍스트보다_우선한다():
    """레코드에 생산자가 명시한 kind 가 있으면 그것이 권위다 — 텍스트에서 다시 뽑지 않는다.

    ★ 판별력 있게 짜야 한다 (2026-08-12 뮤테이션 실측)
      처음 쓴 테스트는 `_k = kind_of(rec)` 를 `_k = ""` 로 지워도 초록이었다.
      guardian context 가 어차피 kind 를 못 주는 예시라 두 경로가 같은 답을 냈기 때문이다.
      그래서 **구조화 kind 와 텍스트 kind 가 서로 다른** 레코드로 고정한다.
    """
    import json

    rb = _gate()
    # 구조화: 진짜 코드 오류(execution_error) / 텍스트: 사람 개입(login_invalid_backoff)
    rec = _rec(error_type="NameError", message="name 'foo' is not defined", source="harness")
    rec["context"] = json.dumps({"kind": "execution_error"})
    text_says_human = "• [naver] nv_login: login_invalid_backoff: 캡차 백오프"

    from JARVIS07_GUARDIAN.severity import kind_of, kinds_in_text
    assert kind_of(rec) == "execution_error", "전제 깨짐 — 구조화 kind 를 못 읽는다"
    assert kinds_in_text(text_says_human) == ["login_invalid_backoff"], "전제 깨짐"

    why = rb.sdk_repair_block_reason(rec, context=text_says_human,
                                     job_id="economic", caller="guardian")
    assert why is None, (
        f"구조화 kind(execution_error)를 버리고 텍스트(login_invalid_backoff)로 막았다: {why}")


def test_B2_kind_가_섞이면_하나만_보고_막지_않는다():
    """로그 앞머리의 kind 한 줄이 뒤따르는 진짜 코드 오류를 덮지 못한다.
    선례: incident_responder._classify 의 `all(is_transient(..., kind=k) for k in kinds)`."""
    rb = _gate()
    ctx = ("• [naver] nv_login: login_invalid_backoff: 캡차 백오프\n"
           "• [tistory] ts_pub: execution_error: NameError: name 'foo' is not defined")
    rec = _rec(error_type="NameError", message="name 'foo' is not defined", source="harness")
    assert rb.sdk_repair_block_reason(rec, context=ctx, job_id="economic",
                                      caller="incident") is None, (
        "첫 kind 한 개가 전체를 결정한다 — 명백한 코드버그가 막힌다")

    # 반대로 *전부* 비코드면 여전히 막아야 한다 (과소차단 방지)
    only = "• [naver] nv_login: login_invalid_backoff: 캡차 백오프"
    rec2 = _rec(error_type="NaverLoginCaptchaTimeout", message="captcha", source="harness")
    assert rb.sdk_repair_block_reason(rec2, context=only, job_id="theme",
                                      caller="incident"), "캡차 단독이 통과한다 — 과소차단"


def test_B3_deferred_는_예산을_먹지_않는다():
    """순번을 못 잡아 *시작조차 안 된* 호출(지출 $0·턴 0)이 예산을 소진하면,
    사건이 몰릴수록(=발행 창) 브레이크가 진짜 수리를 막는다.

    ★ 실경로(run_auto_repair_targeted)를 돌리지 않는다 — 저장소 스냅샷·사전조사 검색을
      타서 느리고, 네트워크·LLM 에 기대게 된다(CI 에서 못 도는 테스트는 초록도 빨강도 아니다).
      대신 ① 의미(장부에서 빠지는가) ② 배선(finally 가 실제로 부르는가) 두 축으로 고정한다.
    """
    rb = _gate()
    rec = _rec(error_type="__B3Deferred__", message="queue full", source="writer")

    before = rb.budget_state()["calls_24h"]
    aid = rb.record_attempt(error_record=rec, caller="incident", job_id="economic",
                            decision="allowed")
    assert rb.budget_state()["calls_24h"] == before + 1, "전제 깨짐 — allowed 가 안 세어진다"

    rb.void_attempt(aid)
    assert rb.budget_state()["calls_24h"] == before, (
        "deferred 무효화 후에도 예산이 소모된 채다 — 쓰지도 않은 세션이 상한을 먹는다")


def test_B3_deferred_배선이_실제로_걸려있다():
    """`void_attempt` 가 존재해도 finally 가 *조건부로* 부르지 않으면 아무 일도 안 일어난다.

    ★ 호출 이름만 보면 안 된다 (2026-08-12 뮤테이션 실측)
      `if _deferred:` 를 `if False:` 로 바꿔도 호출 노드는 그대로 남아 초록이었다.
      **조건식이 상수가 아닌지** 까지 본다.
    """
    import ast
    import inspect

    from JARVIS07_GUARDIAN import auto_repair as ar

    tree = ast.parse(textwrap.dedent(inspect.getsource(ar.run_auto_repair_targeted)))
    fin = [h for n in ast.walk(tree) if isinstance(n, ast.Try) for h in n.finalbody]
    guards = [n for h in fin for n in ast.walk(h) if isinstance(n, ast.If)
              and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "void_attempt"
                      for c in ast.walk(n))]
    assert guards, "finally 가 void_attempt 를 조건부로 부르지 않는다 — deferred 가 예산을 계속 먹는다"
    for g in guards:
        assert not isinstance(g.test, ast.Constant), (
            "void_attempt 의 조건이 상수다 — 배선이 죽어 있다(예: `if False:`)")
        assert "_deferred" in ast.dump(g.test), (
            f"조건이 deferred 여부를 보지 않는다: {ast.dump(g.test)[:80]}")

    called = {n.func.id for h in fin for n in ast.walk(h)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "record_outcome" in called, "finally 가 결과를 마감하지 않는다"


def test_B4_harness_kind_추출은_severity_단독이다():
    """정규식·상한 사본이 두 곳에 있으면 뽑기와 해석이 갈라진다(①).
    종전엔 severity 로 '이관 선언' 만 하고 incident_responder 의 원본을 안 지웠다."""
    import JARVIS07_GUARDIAN.incident_responder as ir
    assert not hasattr(ir, "_HARNESS_KIND_RE"), "incident_responder 에 정규식 사본이 남아 있다"
    assert not hasattr(ir, "_MAX_KINDS"), "incident_responder 에 상한 사본이 남아 있다"

    from JARVIS07_GUARDIAN.severity import kinds_in_text
    t = "• [naver] a: login_invalid_backoff: x\n• [tistory] b: execution_error: y"
    assert ir._harness_kinds(t) == kinds_in_text(t), "위임이 아니라 별도 구현이다"

def test_B2_companions_는_개수_신호로_그대로_전달된다():
    """`kind`(무엇이 났나)와 `companions`(같이 실린 실이슈 **개수**)는 다른 축이다.

    ★ 왜 이 테스트가 필요한가 (2026-08-12 뮤테이션 실측)
      처음 구현은 `list(companions_of(rec) or [])` 로 썼다 — 그런데 그 함수는 **int** 를
      돌려주므로 `list(2)` 가 TypeError 를 냈고, L2 의 except 가 그것을 삼켜
      **판정이 통째로 건너뛰어졌다**(게이트가 조용히 통과). 타입만 맞춰도 초록이 나오는
      테스트로는 못 잡는다 — companions 가 판정을 실제로 바꾸는 레코드로 고정한다.
      선례: guardian_agent.py:1213 이 kind 와 companions 를 따로 넘긴다.
    """
    import json

    rb = _gate()
    from JARVIS07_GUARDIAN.severity import companions_of, is_transient

    rec = _rec(error_type="HarnessAbort", message="abort", source="harness")
    rec["context"] = json.dumps({"kind": "abort", "companions": 0})
    assert companions_of(rec) == 0, "전제 깨짐 — companions 를 못 읽는다"

    solo = is_transient("HarnessAbort", "abort", "harness", kind="abort", companions=0)
    withc = is_transient("HarnessAbort", "abort", "harness", kind="abort", companions=2)
    if solo == withc:
        pytest.skip("이 kind 는 companions 로 판정이 갈리지 않는다 — 판별 불가")

    # companions=0(단독 보고) 과 2(동봉 있음) 가 게이트 결과를 실제로 갈라야 한다
    got_solo = rb.sdk_repair_block_reason(rec, context="", job_id="t", caller="guardian")
    rec2 = dict(rec); rec2["context"] = json.dumps({"kind": "abort", "companions": 2})
    got_with = rb.sdk_repair_block_reason(rec2, context="", job_id="t", caller="guardian")
    assert (got_solo is None) != (got_with is None), (
        f"companions 가 게이트 판정에 전달되지 않는다 — solo={got_solo!r} with={got_with!r}")

def test_장부_조회_실패를_화면이_숨기지_않는다(monkeypatch):
    """DB 가 아프면 L3~L5 가 전부 통과(fail-open)한다. 그때 화면까지 '건강한 0' 을
    보고하면 브레이크가 사라진 줄도 모른다 — 조용한 무력화가 이 프로젝트의 병이다."""
    rb = _gate()
    monkeypatch.setattr(rb, "_q", lambda *a, **k: rb._LAST_Q_ERROR.__setitem__(0, "database is locked") or [])

    st = rb.budget_state()
    assert st.get("ledger_error"), "장부 조회 실패가 budget_state 에 드러나지 않는다"
    line = rb.status_line()
    assert "장부" in line and ("무효" in line or "실패" in line), (
        f"상태 한 줄이 정상인 척한다: {line}")


def test_부팅_스모크가_실제로_배선돼_있다():
    """`budget_effective()` 가 있어도 부팅이 부르지 않으면 없는 것과 같다 —
    `_check_data_verifier` 가 겪은 그 상태(만들고 배선 안 함)를 반복하지 않는다."""
    import ast
    import inspect

    from JARVIS00_INFRA import preflight as pf

    names = {n for _, fn in pf._CHECKERS for n in [getattr(fn, "__name__", "")]}
    assert "_check_repair_budget" in names, (
        "_CHECKERS 에 SDK 수리 브레이크 스모크가 없다 — 부팅이 가드 상태를 묻지 않는다")

    src = ast.parse(textwrap.dedent(inspect.getsource(pf._check_repair_budget)))
    called = {n.func.id for n in ast.walk(src)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "budget_effective" in called, "스모크가 budget_effective 를 부르지 않는다"

