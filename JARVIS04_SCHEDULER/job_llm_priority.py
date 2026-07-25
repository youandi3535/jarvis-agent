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
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["pipeline_job_ids", "gate"]

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
    """잡 실행을 감싸 *잡 문맥* 을 심는 호출가능 객체.

    ★ 왜 클로저가 아니라 모듈 레벨 클래스인가 (2026-07-25 회귀 수정):
      `executor="processpool"` 잡(voice_index·keyword_embed_backfill·daily_review·
      learn_log·feedback_upd·train_weights 6개)은 APScheduler 가 **pickle** 해서 워커
      프로세스로 보낸다. 클로저(`gate.<locals>._wrapped`)는 picklable 이 아니라
      `ValueError: This Job cannot be serialized` 로 *매 실행 실패* 한다.
      모듈 레벨 클래스 + 모듈 레벨 함수(fn)는 참조로 직렬화되므로 안전하다.
      회귀 방지: `selfcheck()` 가 6개 잡의 pickle 가능 여부를 실제로 확인한다.
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


def selfcheck() -> str:
    """회귀 감지 — processpool 잡이 직렬화 가능한가. 위반 시 사유 문자열, 정상이면 "".

    ★ CLAUDE.md `patch_effective()` 표준: "코드 존재는 적용의 증거가 아니다".
    """
    import pickle
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS, _resolve_callback
    except Exception as e:
        return f"job_registry 로드 실패: {e}"
    bad: list[str] = []
    for j in DEFAULT_JOBS:
        if j.get("executor") != "processpool":
            continue
        try:
            pickle.dumps(gate(j["id"], _resolve_callback(j["callback"])))
        except Exception as e:
            bad.append(f"{j['id']}({type(e).__name__})")
    return ("processpool 잡 직렬화 불가: " + ", ".join(bad)) if bad else ""
