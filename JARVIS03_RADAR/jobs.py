"""JARVIS03_RADAR scheduled job callbacks — jarvis_daemon 에서 이관.

JARVIS04_SCHEDULER DEFAULT_JOBS callback 대상.
새 job 추가 시: 여기에 함수 추가 → job_registry.DEFAULT_JOBS 에 dict 추가.

★ 하네스 5-Layer 게이트 (사용자 박제 2026-05-18):
   모든 잡은 _run_with_harness() 를 통해 실행.
   실행 오류 감지 → GUARDIAN 자동 기록 → 재시도 (max_attempts=3) → 검증 순환.
"""
from __future__ import annotations
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

try:
    from JARVIS00_INFRA.watchdog import WATCHDOG_KILL_RC, DEFAULT_ACTION_DEADLINE_SEC
except ImportError:
    WATCHDOG_KILL_RC = 75
    DEFAULT_ACTION_DEADLINE_SEC = 3600

# ★ subprocess 외곽 timeout — 내부 guard_main(deadline_sec=DEFAULT_ACTION_DEADLINE_SEC) 보다
#   짧으면 내부 워치독이 판단하기 전에 subprocess.run 이 먼저 TimeoutExpired 로 강제킬해
#   "정지 감지" 대신 "타임아웃" 오진단이 나고, 정상 장시간 작업도 무조건 실패 처리된다
#   (radar_main.py/performance_collector.py 둘 다 이 값으로 감싸이므로 여기서 한 번만 정합).
_SUBPROCESS_TIMEOUT_SEC = DEFAULT_ACTION_DEADLINE_SEC + 300  # 내부 데드라인 + 5분 여유

_log = logging.getLogger("radar.jobs")
_RADAR_DIR = Path(__file__).parent
_ROOT = _RADAR_DIR.parent
_WRITER_DIR = _ROOT / "JARVIS02_WRITER"
_PYTHON = sys.executable



def _run_script_checked(script: Path, args: list = None, label: str = "") -> None:
    """★ 하네스용 스크립트 실행 — 실패 시 RuntimeError raise (harness 가 재시도).

    returncode != 0 또는 timeout 발생 시 Exception 을 raise 하여
    _run_with_harness() 의 execution_error 검출 → GUARDIAN 기록 → 재실행 흐름 트리거.

    ★ subprocess.run() 을 폴링 스레드로 감싸 대기 중 주기적으로 beat() 한다 (ERRORS [404][413]과
    동일 클래스 수정). 이 호출은 harness 외곽 Watchdog(freeze_sec=300 고정, run_action 이 감싼
    단일 step) 안에서 블로킹되는데, 자식 프로세스(radar_main.py 등) 내부의 자체 beat() 는 별도
    OS 프로세스라 부모 harness 의 _GLOBAL_BEAT 에 보이지 않는다 — 정상적으로 5분 넘게 걸리는
    실행도 그대로 freeze 오탐(300s 무진전)으로 잡혔다. 자식이 살아있는 동안 poll 간격마다
    beat() 로 하네스에 진행 신호를 전달해 오탐을 막는다.
    """
    cmd = [_PYTHON, str(script)] + (args or [])
    lbl = label or script.name
    _log.info(f"▶ {lbl} 시작")
    try:
        from JARVIS00_INFRA.watchdog import beat as _wd_beat
    except ImportError:
        def _wd_beat() -> None: pass  # watchdog 부재 시 no-op (실행 지속)

    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout

    def _run_blocking():
        return subprocess.run(
            cmd, cwd=str(script.parent),
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SEC,
        )

    exe = ThreadPoolExecutor(max_workers=1)
    try:
        fut = exe.submit(_run_blocking)
        poll = 15.0                       # harness freeze_sec(300s) 보다 충분히 작게
        wall_cap = _SUBPROCESS_TIMEOUT_SEC + 30.0   # subprocess 자체 timeout 위 안전 마진
        waited = 0.0
        result = None
        while result is None:
            try:
                result = fut.result(timeout=min(poll, max(0.1, wall_cap - waited)))
            except _FutTimeout:
                waited += poll
                _wd_beat()   # ★ 자식 subprocess 진행 중 — 하네스 외곽 watchdog freeze 오탐 방지
                if waited >= wall_cap:
                    raise RuntimeError(f"{lbl} 타임아웃 ({wall_cap:.0f}s, 강제 포기)")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{lbl} 타임아웃 ({_SUBPROCESS_TIMEOUT_SEC:.0f}s)")
    finally:
        exe.shutdown(wait=False)   # 내부 스레드 leak 가능 — 메인 흐름 비블로킹 우선(_bounded()·llm.py 와 동일 정책)

    for line in (result.stdout or "").strip().splitlines()[-15:]:
        _log.info(f"  {line}")
    if result.returncode != 0:
        err_tail = (result.stderr or "").strip()[-300:]
        if err_tail:
            _log.warning(f"  STDERR: {err_tail}")
        if result.returncode == WATCHDOG_KILL_RC:
            # ★ 워치독 강제킬(os._exit)은 자체 stderr를 남기지 않음 — err_tail은
            #   킬 이전에 우연히 남아있던 무관한 내용(import 경고 등)이라 오진단 유발.
            #   (2026-07-10 — RequestsDependencyWarning 잔재를 실패 원인으로 오인할 뻔한 사고)
            raise RuntimeError(
                f"{lbl} 실패 (rc={result.returncode} EX_TEMPFAIL): "
                f"워치독 정지(freeze/deadline) 감지로 강제 종료 — 네트워크·외부 API 응답 지연 의심 "
                f"(stderr 꼬리는 킬 이전 무관 내용일 수 있음: {err_tail[:120]!r})"
            )
        raise RuntimeError(f"{lbl} 실패 (rc={result.returncode}): {err_tail[:200]}")
    _log.info(f"✅ {lbl} 완료")


def _run_with_harness(
    name: str,
    run_fn: Callable,
    verify_fn: Optional[Callable] = None,
    send_fn: Optional[Callable] = None,
    max_attempts: int = 3,
) -> None:
    """★ 하네스 5-Layer 게이트 래퍼 — 전체 잡에 "수정→기록→누적→순환" 적용.

    Args:
        name:        잡 이름 (로그·박제용)
        run_fn:      실행할 함수 (예외 발생 시 execution_error 검출)
        verify_fn:   선택 — run_fn() 결과를 받아 추가 검증. 오류 문자열 list 반환.
        send_fn:     선택 — 검증 통과 후 notify/저장 등 송출. run_fn() 결과를 인자로 받음.
        max_attempts: 하네스 재시도 한도 (기본 3)
    """
    # ★ P1-③ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): harness ImportError fallback 제거.
    # 이전: harness 미가용 시 직접 실행 (검증 0회 우회 송출 위험).
    # 현재: ImportError 시 escalation + 송출 절대 안 함. circular import·코드 결함 noisy fail.
    try:
        from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue as _Issue
    except ImportError as _ie:
        _log.error(f"[{name}] harness ImportError — 잡 차단 (송출 우회 금지): {_ie}")
        try:
            from shared.notify import send_tg as _stg
            _stg(f"🚨 [{name}] harness ImportError — 잡 차단 (송출 안 함)\n사유: {_ie}")
        except Exception:
            pass
        _g_report("radar", _ie, module=__name__)
        return

    @action_step(name=f"① {name}")
    def _step(state: dict):
        try:
            result = run_fn()
            return {"__ok__": True, "__err__": None, "__result__": result}
        except Exception as e:
            _g_report("radar", e, module=__name__)
            return {"__ok__": False, "__err__": f"{type(e).__name__}: {e}", "__result__": None}

    def _verify(state: dict) -> list:
        if not state.get("__ok__"):
            err = state.get("__err__") or "알 수 없는 오류"
            return [_Issue(step=f"① {name}", kind="execution_error", detail=str(err)[:300])]
        if verify_fn:
            try:
                extras = verify_fn(state.get("__result__")) or []
                return [_Issue(step=f"① {name}", kind="result_invalid", detail=d)
                        for d in extras]
            except Exception as e:
                return [_Issue(step=f"① {name}", kind="verify_error",
                               detail=f"verify_fn 예외: {e}")]
        return []

    def _fix(state: dict, issues: list) -> tuple:
        # 실행 오류는 인라인 수정 불가 — 전체 unfixed (harness 가 step 재실행 트리거)
        return [], list(issues)

    def _send(state: dict):
        if send_fn:
            send_fn(state.get("__result__"))

    run_action(ActionDefinition(
        name=name,
        steps=[_step],
        verify=_verify,
        fix=_fix,    # ★ "수정→기록→누적→순환" 전체 에이전트 디폴트 (사용자 박제 2026-05-18)
        send=_send,
        max_attempts=max_attempts,
    ))


def _verify_trends(_result) -> list:
    """★ 검증 (2026-07-02): 트렌드 수집 신선도·공백 검증 (harness verify_fn).
    subprocess 산출물(trends_YYYY-MM-DD.json)을 읽어 *명백한 실패만* 잡는다(오탐 방지).
    검증기 자체 오류는 fail-open(정상 수집 무한 차단 방지)."""
    import json as _json, datetime as _dt
    try:
        today = _dt.date.today().isoformat()
        fp = _RADAR_DIR / "data" / f"trends_{today}.json"
        if not fp.exists():
            return [f"오늘({today}) 트렌드 파일 없음 — 수집 실패 의심"]
        data = _json.loads(fp.read_text(encoding="utf-8"))
        issues = []
        if data.get("date") != today:
            issues.append(f"트렌드 date({data.get('date')})가 오늘 아님 — 전일 캐시 재사용 의심")
        if not (data.get("scored_keywords") or []):
            issues.append("scored_keywords 0개 — 수집 결과 공백")
        return issues
    except Exception:
        return []


def job_collect_trends() -> None:
    """★ 하네스 래핑 — 실패 시 자동 재시도 + GUARDIAN 기록."""
    _log.info("=" * 50)
    _log.info("📡 [JARVIS03] 트렌드 수집 시작")

    def _run():
        _run_script_checked(_RADAR_DIR / "radar_main.py", label="트렌드 수집")

    _run_with_harness("트렌드 수집", _run, verify_fn=_verify_trends)

    # ★ 트렌드 수집 완료 직후 topic_pack 즉석 생성 (사용자 박제 2026-07-11 — ERRORS [406])
    # CLAUDE_RADAR.md "job_collect_trends 말미 자동 생성" 규정 구현.
    # 목적: 06:30 경제 포스터가 _tp_pick() 즉시 성공 → pack 재생성 LLM 호출 불필요.
    #       LLM 경합(트렌드수집↔대본생성↔pack생성 동시) → rate-limit throttle 연쇄를 원천 차단.
    # 실패해도 06:30 포스터가 _tp_build() 즉석 폴백(기존 동작) → 발행 안 막음.
    try:
        from JARVIS03_RADAR.topic_pack import build_topic_pack as _btp
        _pack = _btp()
        if _pack:
            cands = len((_pack.get("candidates") or []))
            _log.info(f"✅ [topic_pack] 사전 생성 완료: {cands}개 후보")
        else:
            _log.warning("[topic_pack] 사전 생성 실패 — 06:30 포스터에서 즉석 재시도")
    except Exception as _e:
        _log.warning(f"[topic_pack] 사전 생성 예외 (발행은 폴백으로 계속): {_e}")
        _g_report("radar", _e, module=__name__)


def job_collect_performance() -> None:
    """매일 23:00 — 발행글 조회수 수집 + 결과 텔레그램 보고. ★ 하네스 래핑."""
    _log.info("=" * 50)
    _log.info("📊 [JARVIS03] 성과 수집 시작")

    def _run():
        _run_script_checked(_RADAR_DIR / "performance_collector.py", label="성과 수집")
        from shared import db
        with db.get_db() as conn:
            rows = conn.execute(
                """SELECT id, platform, COALESCE(current_views,0) AS cv
                   FROM post_analysis
                   WHERE created_at <= datetime('now','localtime','-1 day')
                     AND created_at >= datetime('now','localtime','-30 day')"""
            ).fetchall()
        return {"rows": rows}

    def _send(result):
        try:
            from shared.notify import send_tg
            rows = (result or {}).get("rows", [])
            total = len(rows)
            zero = sum(1 for r in rows if (r["cv"] or 0) == 0)
            nz = total - zero
            avg = (sum(r["cv"] or 0 for r in rows) / nz) if nz else 0.0
            zero_pct = (zero / total * 100) if total else 0.0
            warn = "⚠️ " if zero_pct >= 80 else ""
            msg = (
                f"📊 *성과 수집 결과* (최근 30일 발행)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"전체: {total}건  |  적재됨: {nz}건  |  0행: {zero}건 ({zero_pct:.0f}%)\n"
                f"평균 조회수(>0): {avg:.0f}\n"
                f"{warn}{'대부분 0 — TS_COOKIE 갱신 필요' if zero_pct >= 80 else '정상 적재 중'}"
            )
            send_tg(msg)
            _log.info(f"📊 성과 보고: total={total} nz={nz} zero={zero} avg={avg:.1f}")
        except Exception as e:
            _log.error(f"❌ 성과 보고 알림 실패: {e}")
            _g_report("radar", e, module=__name__)

    _run_with_harness("성과 수집", _run, send_fn=_send)


def job_recycle_check() -> None:
    """★ 하네스 래핑 — 재활용 후보 글 체크."""
    _log.info("🔁 [JARVIS03] 재활용 후보 글 체크")

    def _run():
        from shared import db
        candidates = db.get_recycle_candidates()
        return {"candidates": candidates}

    def _send(result):
        candidates = (result or {}).get("candidates", [])
        if not candidates:
            return
        try:
            from shared.notify import send_tg
            lines = ["🔁 *글 재활용 제안*\n"]
            for c in candidates[:5]:
                lines.append(
                    f"📄 *{c['theme']}* [{c['platform'].upper()}]\n"
                    f"   조회수: {c.get('current_views', 0):,}회 | {(c.get('created_at') or '')[:10]}\n"
                )
            send_tg("\n".join(lines))
            _log.info(f"  재활용 후보 {len(candidates)}개 알림 전송")
        except Exception as e:
            _log.error(f"[재활용 체크] 알림 실패: {e}")
            _g_report("radar", e, module=__name__)

    _run_with_harness("재활용 체크", _run, send_fn=_send)


def job_analyzer_fallback() -> None:
    """★ 하네스 래핑 — 미처리 분석 fallback."""
    _ANALYZER = _RADAR_DIR / "post_quality_analyzer.py"

    def _run():
        from shared import db
        pending = db.get_pending_analysis(limit=5)
        if pending:
            _log.info(f"🔍 [Fallback] 미처리 분석 {len(pending)}개 → 처리")
            for record in pending:
                subprocess.Popen(
                    [_PYTHON, str(_ANALYZER), str(record["id"])],
                    cwd=str(_ANALYZER.parent),
                )
                time.sleep(2)
        return {"launched": len(pending) if pending else 0}

    _run_with_harness("분석 fallback", _run)


def job_auto_approve() -> None:
    """매 30분 — 1시간 무응답 pending_approval 자동 학습 처리. ★ 하네스 래핑.

    selected 부분 승인 상태가 있으면 그 인덱스만 학습,
    없으면 전체 제안 학습. 현재 글 재발행 없이 learning_insights 에
    저장 → 다음날 글 작성 시 자동 반영.
    """
    import json as _json

    def _run():
        from shared import db
        from shared.notify import send_tg

        rows = db.get_pending_approval_older_than(hours=1)
        if not rows:
            return {"processed": 0}

        _log.info(f"⏰ 자동 승인 대상 {len(rows)}건")
        processed = 0
        for r in rows:
            aid = r["id"]
            try:
                suggestions = _json.loads(r.get("suggestions") or "[]")
                patch_raw = _json.loads(r.get("revision_patch") or "{}")
                sel = patch_raw.get("selected")
                if isinstance(sel, list) and sel:
                    applied = [s for i, s in enumerate(suggestions) if i in sel]
                    mode = "auto_partial"
                else:
                    applied = suggestions
                    mode = "auto_all"

                if not applied:
                    db.reject_analysis(aid)
                    send_tg(
                        f"⏰ [{r['platform'].upper()}] {r['theme']}\n"
                        f"1시간 무응답 + 선택된 제안 없음 → 거부 처리"
                    )
                    continue

                db.approve_analysis(aid, {"suggestions": applied, "mode": mode, "auto": True})
                platform = r.get("platform", "all")
                learned = 0
                for s in applied:
                    if not isinstance(s, dict):
                        continue
                    field = s.get("field", "content")
                    stype = s.get("type", "revision")
                    issue = s.get("issue", "")
                    after = s.get("after", "")
                    if not issue:
                        continue
                    try:
                        db.upsert_learning_insight(
                            insight_key  = f"{stype}_{field}",
                            insight_type = stype,
                            description  = issue,
                            directive    = after,
                            weight       = 1.0,
                            scope        = platform,
                        )
                        learned += 1
                    except Exception as le:
                        _log.warning(f"[auto_approve] insight 저장 실패: {le}")
                        _g_report("radar", le, module=__name__)

                send_tg(
                    f"⏰ *자동 학습* [{r['platform'].upper()}] {r['theme']}\n"
                    f"1시간 무응답 → {learned}개 제안 학습 완료 — 내일 글에 반영"
                )
                processed += 1
                time.sleep(2)
            except Exception as e:
                _log.error(f"[auto_approve] id={aid} 실패: {e}")
                _g_report("radar", e, module=__name__)
        return {"processed": processed}

    _run_with_harness("자동 승인 처리", _run)


def job_voice_index() -> None:
    """일별 — 신규 발행 글 브랜드 보이스 인덱싱 + 트렌드 키워드 임베딩 백필. ★ 하네스 래핑."""

    def _run():
        from shared.style import run_full_index  # ★ Phase 2 통합 (2026-05-18)
        res = run_full_index(reindex=False, verbose=False)
        _log.info(f"[voice_index] indexed={res['indexed']} provider={res['provider']}")
        from JARVIS03_RADAR.learning import backfill_keyword_embeddings
        embed_res = backfill_keyword_embeddings(verbose=False)
        if embed_res["new"] > 0:
            _log.info(f"[keyword_embed] 신규 {embed_res['new']}개 임베딩 완료 (total={embed_res['total']})")
        return {"indexed": res["indexed"], "embed_new": embed_res["new"]}

    _run_with_harness("보이스 인덱싱", _run)


def job_daily_review() -> None:
    """매일 22:00 — 그날 발행 글 통합 분석 → 학습 인사이트 누적. ★ 하네스 래핑."""
    _log.info("=" * 50)
    _log.info("📊 [JARVIS03] 일일 종합 분석 시작 (22:00)")

    def _run():
        from JARVIS03_RADAR.daily_review import run_daily_review
        res = run_daily_review()
        _log.info(
            f"📊 daily_review: 글 {res.get('total_posts',0)}건 "
            f"학습 인사이트 신규/갱신 {res.get('total_insights',0)}건"
        )
        return res

    _run_with_harness("일일 종합 분석", _run)


def job_learn_log() -> None:
    """매일 23:30 — 예측(opportunity_score) vs 실측(current_views) 페어 적재. ★ 하네스 래핑."""

    def _run():
        from JARVIS03_RADAR import learning
        result = learning.log_predictions_vs_actual(verbose=True)
        _log.info(f"📊 learn_log: 적재 {result.get('inserted', 0)}건 / 갱신 {result.get('updated', 0)}건")
        return result

    _run_with_harness("예측-실측 학습 로그", _run)


def job_feedback_update() -> None:
    """매일 04:00 — events 테이블에서 최근 7일 승인/거부 → feedback_penalty 갱신. ★ 하네스 래핑."""

    def _run():
        from JARVIS03_RADAR import learning
        result = learning.update_feedback_from_events(days=7, verbose=True)
        n_app = result.get("approved", 0)
        n_rej = result.get("rejected", 0)
        n_pen = result.get("penalties_updated", 0)
        _log.info(f"👤 feedback_update: 승인 {n_app}건 / 거부 {n_rej}건 → penalty {n_pen}건 갱신")
        return result

    _run_with_harness("피드백 penalty 갱신", _run)


def job_train_weights() -> None:
    """매주 일 04:00 — learn_log 회귀학습 → learned_weights 갱신 + 백테스트. ★ 하네스 래핑."""

    def _run():
        from JARVIS03_RADAR import learning
        train_res = learning.train_weights(min_samples=20, verbose=True)
        if train_res.get("ok"):
            _log.info(
                f"🧠 train_weights: n={train_res.get('n_samples')} "
                f"R²={train_res.get('r2'):.3f} MSE={train_res.get('mse'):.2f}"
            )
        else:
            _log.info(f"🧠 train_weights skip: {train_res.get('reason')}")

        bt_res = learning.run_backtest(test_ratio=0.2, verbose=True)
        if bt_res.get("ok"):
            _log.info(
                f"📈 backtest: n={bt_res.get('n_samples')} "
                f"R²={bt_res.get('r2'):.3f} MAPE={bt_res.get('mape', 0):.1f}%"
            )
        else:
            _log.info(f"📈 backtest skip: {bt_res.get('reason')}")

        try:
            from JARVIS03_RADAR import analyzer as _ana
            _ana._WEIGHTS_CACHE["data"] = None
            _ana._WEIGHTS_CACHE["ts"] = 0.0
        except Exception:
            pass

        from shared import db as _db
        n_pruned = _db.decay_learning_insights(min_weight=0.05)
        _log.info(f"🧹 learning_insights decay: 삭제 {n_pruned}건")
        return {"train": train_res, "backtest": bt_res, "pruned": n_pruned}

    _run_with_harness("가중치 학습 + 백테스트", _run)


def job_keyword_embed_backfill() -> None:
    """매일 02:45 — trends 신규 키워드 임베딩 백필. ★ 하네스 래핑."""

    def _run():
        from JARVIS03_RADAR import learning
        result = learning.backfill_keyword_embeddings(verbose=True)
        _log.info(
            f"🔢 keyword_embed_backfill: 신규 {result.get('new', 0)}개 "
            f"(전체 {result.get('total', 0)} / 기존 {result.get('skipped', 0)} / "
            f"실패 {result.get('failed', 0)})"
        )
        return result

    _run_with_harness("키워드 임베딩 백필", _run)


__all__ = [
    "job_collect_trends", "job_collect_performance",
    "job_recycle_check", "job_analyzer_fallback", "job_auto_approve",
    "job_voice_index", "job_daily_review", "job_learn_log",
    "job_feedback_update", "job_train_weights", "job_keyword_embed_backfill",
]
