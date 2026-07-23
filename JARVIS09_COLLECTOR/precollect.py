"""JARVIS09_COLLECTOR/precollect.py — 발행창 밖 선계산(precollect) 잡.

★ 소유 이관 2026-07-23 (사용자 박제: "02의 수집하는 모든 기능과 코드 폴더 등 모든것을 다 09로 옮겨").
  종전 위치: `JARVIS02_WRITER/trend_theme_writer.precollect_theme` ·
            `JARVIS02_WRITER/trend_economic_writer.precollect_economic` ·
            `JARVIS02_WRITER/scheduler.run_precollect_{theme,economic}` (잡 래퍼).
  *언제 미리 수집해 둘지* 는 수집 시점 판단 = 수집 도메인의 일이다. 대본 작성기(02) 안에
  있는 동안 02 가 수집 순서·재사용 여부를 판단했고, 그게 곧 수집 단일 진입점 위반이었다.

무엇을 하나 — 무거운 fact·chart 추출 LLM 을 *저부하 창* (경제 = 06:00 트렌드 잡 말미 체이닝 /
테마 = 20:00) 에서 미리 수행해 캐시한다. 발행창(경제 07:00 / 테마 21:00)의 `collect_all(
use_cache=True)` 이 그 상자를 그대로 재사용 → 발행창 내 추출 LLM 0회 → 직후 writer 가 버스트로
열화되지 않은 Max 풀에서 실행(300s 스톨 조건 제거).

★ 2026-07-23 부터 두 선계산은 발행의 *필수 선행* (JARVIS04 job_prereq 게이트). 성공하지 않으면
  발행이 시작되지 않고, 미충족이면 선행을 즉시 돌린 뒤 회복 갭(1시간) 뒤로 발행이 미뤄진다.
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("jarvis.precollect")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:                                   # pragma: no cover
    def _g_report(*a, **kw): pass


def _banner(title: str) -> None:
    print(f"\n{'='*50}\n⚡ {title} 시작 [{datetime.now().strftime('%H:%M:%S')}] — 발행창 밖 추출\n{'='*50}")


# ══════════════════════════════════════════════════════════════
#  테마 — 20:00
# ══════════════════════════════════════════════════════════════

def precollect_theme() -> dict:
    """★ 테마 선계산 — 테마 고정(pin) + 수집 상자 캐시.

    테마는 네이버 금융 카탈로그에서 random 선정되므로, 여기서 고른 테마를 *고정(pin)* 해
    21:00 발행이 같은 테마를 쓰게 한다 (→ 캐시 히트). 선정 자체는 자비스03(theme_picker),
    발행 상태(exclude)는 자비스02 가 준다 — 09 는 "이 테마로 미리 수집" 만 한다.
    """
    from .collector_engine import collect_all
    from .precollect_cache import save_precollect, pin_theme

    _banner("테마 선계산")
    try:
        from JARVIS02_WRITER.scheduler import select_top_theme as _sel
        theme = _sel()
    except Exception as e:
        print(f"  ⚠️ [테마 선계산] 테마 선정 실패: {e}")
        return {"success": False, "cached": 0}
    if not theme:
        print("  ⚠️ [테마 선계산] 선정 가능한 미발행 테마 없음")
        return {"success": False, "cached": 0}

    saved = 0
    try:
        bundle = collect_all(theme, category="theme", use_cache=False)
        if bundle.get("data_empty"):
            print(f"  ⏭️ [테마 선계산] '{theme}' 데이터 0 — 고정 안 함(발행 시 재선정)")
        elif save_precollect("theme", theme, bundle):
            pin_theme(theme)
            saved = 1
    except Exception as e:
        _g_report("collector", e, module=__name__, func_name="precollect_theme")
        print(f"  ⚠️ [테마 선계산] 예외: {e} — 발행이 실제 수집으로 폴백")
    print(f"⚡ 테마 선계산 완료 — 고정·캐시 {saved}개 (21:00 발행 재사용 대기)")
    return {"success": bool(saved), "cached": saved, "theme": theme}


# ══════════════════════════════════════════════════════════════
#  경제 브리핑 — 06:00 트렌드 잡 말미 체이닝
# ══════════════════════════════════════════════════════════════

#  발행 슬롯 = (캐시 카테고리, 강제 주제 env 접두사). 플랫폼별 분기를 코드에 박지 않고
#  이 목록에서 파생한다 (② 동적 설계 — 슬롯 추가는 한 줄).
_ECON_SLOTS = [("naver", "JARVIS_FORCE_NV"), ("tistory", "JARVIS_FORCE")]


def precollect_economic() -> dict:
    """★ 경제 브리핑 선계산 — 발행 슬롯(네이버·티스토리) 후보를 미리 수집·캐시.

    주제는 자비스03 `topic_pack.pick_slot_candidate()` 단독 (키워드 단독 전송 금지 — 프로필 동봉).
    두 슬롯이 같은 주제를 잡지 않도록 앞 슬롯이 선점한 키워드를 exclude 로 넘긴다.
    """
    from .collector_engine import collect_all, market_snapshot
    from .precollect_cache import save_precollect

    _banner("경제 선계산")
    _md: dict = {}
    try:
        _md = market_snapshot()
    except Exception as e:
        print(f"  ⚠️ [경제 선계산] 시장 수치 스킵: {e}")

    saved, taken = 0, ""
    for slot, force_env in _ECON_SLOTS:
        try:
            from JARVIS03_RADAR.topic_pack import pick_slot_candidate as _pick
            cand = _pick(exclude_keyword=taken, force_env=force_env) or {}
            kw = (cand.get("keyword") or "").strip()
            if not kw:
                print(f"  ⚠️ [경제 선계산/{slot}] 자비스03 주제 후보 없음 — 건너뜀")
                continue
            taken = taken or kw
            print(f"  📌 [경제 선계산/{slot}] [{cand.get('sector', '')}] {kw}")
            bundle = collect_all(
                kw, profile=cand.get("profile") or {}, sector=cand.get("sector", ""),
                category="economic", angle=(cand.get("profile") or {}).get("summary", ""),
                synonyms=cand.get("synonyms"), plan_cache=cand.get("data_plan"),
                market_data=_md, extra_meta={"section_plan": cand.get("section_plan")},
                use_cache=False)
            if save_precollect("economic", kw, bundle):
                saved += 1
        except Exception as e:
            _g_report("collector", e, module=__name__, func_name="precollect_economic")
            print(f"  ⚠️ [경제 선계산/{slot}] 예외: {e} — 발행창이 실제 수집으로 폴백")
    print(f"⚡ 경제 선계산 완료 — {saved}개 캐시 (07:00 발행 재사용 대기)")
    return {"success": True, "cached": saved}


# ══════════════════════════════════════════════════════════════
#  잡 콜백 — JARVIS04 DEFAULT_JOBS / 자비스03 아침 체인이 부르는 진입점
# ══════════════════════════════════════════════════════════════

def _run_job(label: str, fn, prereq_job: str, fallback_deadline: int) -> None:
    """선계산 잡 공통 껍데기 — 종료 중 연기 · 워치독 · 동적 데드라인.

    ★ 데드라인을 여기서 박지 않는다(종전 20:58·06:58 하드코딩). 후행 발행 잡의 *실제 다음
      실행* 2분 전까지를 JARVIS04 가 파생해준다 → 정규 실행이든 선행 회복 실행(예: 21:30)이든
      저절로 맞는다. 발행창을 침범하지 않으면서 쓸 수 있는 시간을 다 쓴다.
    """
    from JARVIS00_INFRA.harness import interpreter_shutting_down as _isd
    if _isd():
        log.info(f"⏸ [{label}] 인터프리터 종료 중(데몬 재시작) — 연기")
        return
    try:
        from JARVIS00_INFRA.watchdog import guard_main
        from JARVIS04_SCHEDULER.job_prereq import deadline_sec as _deadline
        with guard_main(label, deadline_sec=(_deadline(prereq_job) or fallback_deadline)):
            res = fn()
        log.info(f"⚡ [{label}] 완료 — 캐시 {res.get('cached', 0)}개 (발행 재사용 대기)")
    except Exception as e:
        _g_report("collector", e, module=__name__, func_name=f"job_{label}")
        log.warning(f"⚠️ [{label}] 실패 ({e}) — 발행이 실제 수집으로 폴백")


def job_precollect_theme() -> None:
    """테마 선계산 잡 (20:00 = 21:00 발행 1시간 전 — DEFAULT_JOBS `j02_theme_precollect`)."""
    _run_job("테마 선계산", precollect_theme, "j02_theme_precollect", 1500)


def job_precollect_economic() -> None:
    """경제 선계산 잡 — 고정 시각이 아니라 `radar_trends_06` 말미 체이닝 (사용자 박제 2026-07-18).

    트렌드 분석이 6분 걸리든 12분 걸리든 topic_pack 빌드가 끝나는 즉시 이어진다
    (팩 미완 재빌드 낭비·헛대기 0).
    """
    _run_job("경제 선계산", precollect_economic, "radar_trends_06", 1020)


__all__ = ["precollect_theme", "precollect_economic",
           "job_precollect_theme", "job_precollect_economic"]
