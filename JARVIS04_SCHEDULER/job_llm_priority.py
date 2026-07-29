"""JARVIS04 — 발행창 LLM 우선권 게이트 (★ 사용자 박제 2026-07-25).

**요구사항 (사용자 원문)**
  "자비스03·09·02·06·08 이 작동될 때가 발행중이야. 이 때는 LLM 이 다른 것에 할당되지 않고
   오로지 글 작성에만 쓰여야 한다."
  단, *사용자가 수동으로 돌리는 Claude Code·Cowork 는 제외* (별도 프로세스라 이 경로를 타지 않음).

**왜 잡 래퍼인가 (3원칙)**
① 단일 진입점 — 창을 켜는 곳은 `register_default_jobs` 가 씌우는 이 `gate()` 한 곳.
   각 잡 콜백(03 트렌드·09 선계산·02 발행)에 `mark_publishing` 을 흩지 않는다.
   (`job_prereq.gate()` 와 같은 자리·같은 패턴 — 선례를 따른다.)
② 동적 설계 — 파이프라인 잡 목록을 손으로 적지 않는다. *발행 잡* 을 뿌리로 삼아
   `DEFAULT_JOBS` 의 `requires` 그래프를 닫아 파생한다:
       j01_economic_post ← radar_trends_06        (03 주제/트렌드)
       j01_theme_post_21 ← j02_theme_precollect   (09 선계산 수집)
   새 선행 잡을 `requires` 에 한 줄 추가하면 창이 자동으로 그 잡까지 넓어진다.
③ 모든 조합 — 경제·테마 두 뿌리 모두. 각 발행 잡이 네이버·티스토리를 직렬 수행하므로 4조합 전부.

**보류되는 것 / 안 되는 것**
  · 보류: `shared/llm.py` MODELS 에 `background=True` 로 선언된 alias
    (guardian·learn_eval·architect·diagnostic·coder) — `invoke_text` 가 `bg_defer_reason()` 으로 판정.
  · 통과: 글 작성 alias(writer·writer_fast·fact_judge·engagement_judge·analyzer·router).
    수집(09)·이미지(06)가 analyzer/router/writer_fast 를 쓰므로 막으면 발행이 죽는다.
  · 중첩 안전: `mark_publishing` 은 참조수 기반이라 이 래퍼 + 내부 run() 이 겹쳐도 정상.

**★ processpool 잡은 `run_gated` 로 (ERRORS [499] — 2026-07-25 회귀 재수정)**
  6개 processpool 잡(voice_index·keyword_embed_backfill·daily_review·learn_log·
  feedback_upd·train_weights)은 `_JobGate` *인스턴스* 를 못 쓴다. APScheduler 는 콜러블을
  pickle 로 직접 저장하지 않고 `obj_to_ref`(module:qualname 문자열)로 바꿨다가
  워커 프로세스에서 `ref_to_obj` 로 *이름을 다시 조회* 해 복원한다. 콜러블 **인스턴스**는
  `get_callable_name` 이 인스턴스가 아니라 **클래스**의 qualname 을 돌려주므로, 복원 결과가
  `_JobGate(job_id, pipeline, fn)` 인스턴스가 아니라 **클래스 `_JobGate` 자체**가 되고,
  워커가 `job.args`(보통 빈 튜플)로 그 클래스를 호출 → `_JobGate.__init__() missing 3
  required positional arguments`. `pickle.dumps(instance)` 는 이 왕복을 재현하지
  않아(직접 pickle 은 인스턴스 상태를 통째로 보존) 종전 `selfcheck()` 가 놓쳤다.
  → processpool 잡은 `run_gated(job_id, callback, *a, **kw)` (모듈 레벨 **함수**)를
  등록하고 job_id·callback 문자열은 `args` 로 넘긴다 — 문자열은 참조가 아니라 *값*으로
  pickle 되므로 프로세스 경계를 넘어도 안전. 워커 안에서 콜백을 다시 조회한다(② 동적 설계).
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["pipeline_job_ids", "gate", "run_gated", "assert_ref_serializable", "selfcheck"]

# ★★ 발행 시각은 **07:00 · 21:00 딱 둘뿐** (사용자 박제 2026-07-25).
#    발행 잡을 콜백 표식으로 찾는다 — 잡 ID 를 박지 않는다(ID 가 바뀌어도 콜백은 그대로).
#    ★ 단일 진실 소스: 종전엔 이 규칙이 `shared/llm.py:_publish_times()` 에도 복사돼 있었다(2벌).
#      두 소비자(파이프라인 판정·보호구간 파생)가 모두 여기서 파생한다.
#    ★ 09:00·15:00 은 **RADAR 트렌드 수집 시각일 뿐 발행이 아니다.** 그 시각에 발행하던
#      `job_radar_pipeline_check`(+`_radar_auto`, 잡 j01_radar_check_09/15)는 2026-07-25 전면 삭제.
PUBLISH_CALLBACK_MARKS = (
    "run_self_repair_then",      # 경제 07:00 · 테마 21:00 — 이 둘뿐
)


def is_publish_callback(callback: str) -> bool:
    """콜백 경로가 *발행을 수행하는* 잡인가 — 파생 판정 단일 소스."""
    cb = str(callback or "")
    return any(mark in cb for mark in PUBLISH_CALLBACK_MARKS)


def publish_post_type(callback: str) -> str:
    """발행 콜백에서 *글 종류* 파생 — `run_self_repair_then_economic` → `'economic'`.

    ★ 이 어휘는 `post_analysis.post_type` 과 같다 — 그래서 발행 실적 대조에 그대로 쓴다.
      마커 문자열을 밖에서 다시 쓰지 않게 하려고 여기(마커 소유자)에 둔다.
      2026-07-29 실측: `publish_ledger` 가 `"run_self_repair_then_"` 를 자체 보유해
      같은 판단이 **3벌**이 돼 있었다(job_llm_priority · job_registry · publish_ledger).
    """
    cb = str(callback or "")
    for mark in PUBLISH_CALLBACK_MARKS:
        if mark in cb:
            return cb.rsplit(mark, 1)[-1].lstrip("_").strip()
    return ""


def publish_cron_times() -> tuple:
    """발행 잡의 cron 시각 [(hour, minute), …] — 보호구간 계산용(shared/llm 소비)."""
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    except Exception:
        return ()
    out = []
    for j in DEFAULT_JOBS:
        if j.get("trigger") != "cron" or not is_publish_callback(j.get("callback")):
            continue
        kw = j.get("kwargs") or {}
        h = kw.get("hour")
        if isinstance(h, int):
            out.append((h, int(kw.get("minute") or 0)))
    return tuple(sorted(set(out)))


def pipeline_job_ids() -> frozenset[str]:
    """발행 파이프라인에 속한 잡 ID — 발행 잡 + `requires` 선행 폐쇄에서 *파생*."""
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    except Exception:
        return frozenset()
    by_id = {j.get("id"): j for j in DEFAULT_JOBS if j.get("id")}
    roots = [j["id"] for j in DEFAULT_JOBS if is_publish_callback(j.get("callback"))]
    seen: set[str] = set()

    def _close(jid: str) -> None:
        if jid in seen or jid not in by_id:
            return
        seen.add(jid)
        for req in (by_id[jid].get("requires") or []):
            _close(req)

    for r in roots:
        _close(r)
    return frozenset(seen)


class _JobGate:
    """잡 실행을 감싸 *잡 문맥* 을 심는 호출가능 객체 — **in-process 잡 전용**.

    ★ 적용 범위 (ERRORS [499] 로 좁혀짐): `executor="processpool"` 잡에는 **쓰지 않는다**.
      그 잡들은 워커가 별도 프로세스라 func 가 참조(module:qualname)로 왕복하는데,
      콜러블 인스턴스는 그 이름이 인스턴스가 아니라 *클래스* 를 가리켜 워커에서
      `_JobGate()` 로 재구성돼 TypeError 가 난다. processpool 은 `run_gated` 를 쓴다.
      (종전 이 docstring 은 "processpool 을 위해 클래스로 만들었다" 고 적혀 있었다 —
       그게 바로 사고의 원인이었다. 문서를 진실로 믿지 말 것: 판정은 `selfcheck()` 가 한다.)
    """

    __slots__ = ("job_id", "pipeline", "fn")

    def __init__(self, job_id: str, pipeline: bool, fn: Callable[..., Any]):
        self.job_id = job_id
        self.pipeline = pipeline
        self.fn = fn

    def __call__(self, *args, **kwargs):
        try:
            from shared.llm import mark_publishing as _mark, mark_job_context as _ctx
        except Exception:
            return self.fn(*args, **kwargs)      # LLM 계층 미가용 — 원본 그대로
        _ctx(self.job_id, self.pipeline)
        if self.pipeline:
            _mark(True)
        try:
            return self.fn(*args, **kwargs)
        finally:
            if self.pipeline:
                try:
                    _mark(False)
                except Exception:
                    pass
            try:
                _ctx("", False)             # ★ 스레드/프로세스 풀 재사용 대비 반드시 해제
            except Exception:
                pass


def gate(job_id: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """모든 잡에 *잡 문맥* 을 심고, 파이프라인 잡이면 실행 구간을 '발행창' 으로 표시한다.

    · 파이프라인 잡  → 문맥(pipeline=True) + `mark_publishing` 창 ON
    · 그 외 잡        → 문맥(pipeline=False) 만. 발행창이면 그 잡의 LLM 호출이 보류된다.
      (alias 로는 못 거르는 배경 잡 때문 — daily_review=analyzer, design_learn=writer)
      processpool 워커에서도 유효: 문맥은 워커 프로세스 안에서 설정되고, 발행 여부는
      `is_publishing()` 이 *파일 표식* 으로 프로세스 경계를 넘어 판정한다.
    · 잡이 아닌 문맥(텔레그램 사용자 명령·수동 실행)은 문맥이 없어 보류 대상이 아니다.
    """
    try:
        _pipeline = job_id in pipeline_job_ids()
    except Exception:
        _pipeline = False
    return _JobGate(job_id, _pipeline, fn)


def run_gated(job_id: str, callback: str, *args: Any, **kwargs: Any) -> Any:
    """processpool 잡의 *유일한* 실행 진입점 (ERRORS [499] — 모듈 레벨 함수라 참조 직렬화 안전).

    job_id·callback 은 문자열 값으로 `args` 를 통해 넘어오므로(참조가 아니라 값 pickle)
    워커 프로세스에서도 안전하게 재구성된다. 콜백·선행조건 판정은 여기서 *그때그때 재조회*
    (② 동적 설계 — 등록 시점 클로저에 박아두지 않는다. 미래에 processpool 잡에 `requires`
    가 붙어도 자동으로 맞물린다).
    """
    from JARVIS04_SCHEDULER.job_registry import _resolve_callback
    fn = _resolve_callback(callback)
    try:
        from JARVIS04_SCHEDULER.job_prereq import gate as _prereq_gate
        fn = _prereq_gate(job_id, fn)
    except Exception:
        pass
    try:
        _pipeline = job_id in pipeline_job_ids()
    except Exception:
        _pipeline = False
    try:
        from shared.llm import mark_publishing as _mark, mark_job_context as _ctx
    except Exception:
        return fn(*args, **kwargs)
    _ctx(job_id, _pipeline)
    if _pipeline:
        _mark(True)
    try:
        return fn(*args, **kwargs)
    finally:
        if _pipeline:
            try:
                _mark(False)
            except Exception:
                pass
        try:
            _ctx("", False)             # ★ 스레드/프로세스 풀 재사용 대비 반드시 해제
        except Exception:
            pass


def assert_ref_serializable(job_id: str, fn: Callable[..., Any], args: tuple = ()) -> str:
    """워커가 그 잡을 *복원해서 호출* 하는 과정을 재현한다. 통과하면 `""`, 아니면 사유.

    ★ 왜 `pickle.dumps(fn)` 로는 못 잡는가 (ERRORS [499]): APScheduler 는 콜러블을 직접
      pickle 하지 않는다 — `obj_to_ref` 로 "module:qualname" 문자열을 만들어 두었다가
      워커에서 `ref_to_obj` 로 *그 이름을 다시 조회* 해 복원한다. `pickle.dumps(instance)`
      는 인스턴스 상태를 통째로 보존해 이 왕복과 무관하게 성공해 버린다 — 그래서 종전
      검사가 통과했고 회귀가 운영에 나갔다.

    두 가지를 본다. 하나만 봐서는 이번 사고를 못 잡는다:
      ① 이름 왕복이 **같은 객체** 로 돌아오는가 (인스턴스는 클래스로 바뀌어 돌아온다)
      ② 돌아온 객체를 **job.args 로 호출할 수 있는가** (인자 개수 불일치가 곧 이번 TypeError)
    """
    try:
        from apscheduler.util import obj_to_ref, ref_to_obj
    except Exception:
        return ""                       # APScheduler 미가용 — 검사 자체를 못 함(통과 처리)
    try:
        back = ref_to_obj(obj_to_ref(fn))
    except Exception as e:
        return (f"{job_id}: 참조 직렬화 불가 ({type(fn).__name__}) — {e}. "
                f"클로저·partial 대신 모듈 레벨 함수를 등록할 것")
    if back is not fn:
        _got = (f"클래스 {back.__name__} 자체" if isinstance(back, type)
                else getattr(back, "__name__", type(back).__name__))
        return (f"{job_id}: 참조 복원이 다른 객체를 가리킨다 "
                f"({type(fn).__name__} 인스턴스 → {_got}). "
                f"콜러블 인스턴스 대신 모듈 레벨 함수(`run_gated`)를 등록하고 "
                f"문맥은 args 로 넘길 것")
    try:
        import inspect
        inspect.signature(back).bind(*args)
    except TypeError as e:
        return f"{job_id}: 워커가 job.args{args!r} 로 호출할 수 없다 — {e}"
    except (ValueError, AttributeError):
        pass                            # 시그니처를 못 읽는 내장/C 함수 — 판정 보류
    return ""


def selfcheck() -> str:
    """회귀 감지 — **등록이 실제로 넘기는 func** 가 워커 복원·호출을 버티는가.

    ★ CLAUDE.md `patch_effective()` 표준: "코드 존재는 적용의 증거가 아니다".
      그래서 이 검사는 상수를 보지 않고 `job_registry.job_func_for()` 에 **등록과 똑같이**
      물어본다. 등록 경로가 다시 콜러블 인스턴스로 되돌아가면 그 즉시 여기서 걸린다.
      (종전 검사는 `run_gated` 상수만 봐서, 등록이 무엇을 넘기든 항상 통과했다 — 거짓 보증.)
    """
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS, job_func_for
    except Exception as e:
        return f"job_registry 로드 실패: {e}"
    bad: list[str] = []
    for j in DEFAULT_JOBS:
        if j.get("executor") != "processpool":
            continue
        try:
            fn, args = job_func_for(j)
        except Exception as e:
            bad.append(f"{j.get('id')}: 등록 함수 산출 실패 — {e}")
            continue
        why = assert_ref_serializable(j["id"], fn, args)
        if why:
            bad.append(why)
    return "; ".join(bad)
