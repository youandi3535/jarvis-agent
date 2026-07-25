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

# 발행 파이프라인의 *뿌리* 를 찾는 표식 — 콜백 경로에 이 조각이 있으면 발행 잡.
#   (잡 ID 를 박지 않는다: ID 가 바뀌어도 콜백은 그대로다.)
_PUBLISH_CALLBACK_MARK = "run_self_repair_then"


def pipeline_job_ids() -> frozenset[str]:
    """발행 파이프라인에 속한 잡 ID — 발행 잡 + `requires` 선행 폐쇄에서 *파생*."""
    try:
        from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
    except Exception:
        return frozenset()
    by_id = {j.get("id"): j for j in DEFAULT_JOBS if j.get("id")}
    roots = [j["id"] for j in DEFAULT_JOBS
             if _PUBLISH_CALLBACK_MARK in (j.get("callback") or "")]
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


def gate(job_id: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """모든 잡에 *잡 문맥* 을 심고, 파이프라인 잡이면 실행 구간을 '발행창' 으로 표시한다.

    · 파이프라인 잡  → 문맥(pipeline=True) + `mark_publishing` 창 ON
    · 그 외 잡        → 문맥(pipeline=False) 만. 발행창이면 그 잡의 LLM 호출이 보류된다.
      (alias 로는 못 거르는 배경 잡 때문 — daily_review=analyzer, design_learn=writer)
    · 잡이 아닌 문맥(텔레그램 사용자 명령·수동 실행)은 문맥이 없어 보류 대상이 아니다.
    """
    try:
        _pipeline = job_id in pipeline_job_ids()
    except Exception:
        _pipeline = False

    def _wrapped(*args, **kwargs):
        try:
            from shared.llm import mark_publishing as _mark, mark_job_context as _ctx
        except Exception:
            return fn(*args, **kwargs)      # LLM 계층 미가용 — 원본 그대로
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
                _ctx("", False)             # ★ 스레드풀 재사용 대비 반드시 해제
            except Exception:
                pass

    _wrapped.__name__ = getattr(fn, "__name__", "job")
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    return _wrapped
