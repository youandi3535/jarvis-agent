"""JARVIS07_GUARDIAN/auto_repair.py — Claude Code SDK 기반 자율 자가 진단·수정 엔진

흐름:
  1. claude-code-sdk → 전체 .py 파일 정밀 검토 (syntax + 규정 위반 + 버그)
  2. 오류 발견 시 즉시 수정 (.bak 백업 → ast.parse 검증)
  3. 수정 내용 ERRORS.md 기록
  4. self_repair_runs DB 박제 + 텔레그램 알림

직접 실행 금지 — APScheduler 잡 콜백에서만 호출.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent

_TIMEOUT    = 1200  # 최대 20분 (★ ERRORS 박제 — 164파일 전수 검토 시 900s 초과 → 1200s, --max-turns 80 병행)
# ★ 오류 수정 모델 — 단일 계층(ADR 017). ID 리터럴을 박지 않고 shared/llm.MODELS 에서 파생
#   (사본을 두면 모델 교체 시 여기만 옛 모델을 계속 가리킨다 — ERRORS [491]).
#   full model ID 로 넘기는 관행은 유지 (ERRORS [184] — bare alias 는 1M context 자동 승격 위험).
from shared.llm import model_id as _model_id  # noqa: E402
_MODEL      = _model_id("guardian")

# macOS npm/nvm 설치 경로 포함 확장 PATH
_EXTRA_PATHS = [
    "/usr/local/bin",
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    str(Path.home() / ".npm-global" / "bin"),
    str(Path.home() / ".local" / "bin"),
]



# ── 자가 진단 프롬프트 (Sonnet 5 — Bash-first 전수 검토) ─────────────
# ★ 누수 점검 (2026-05-17) — `{WORKDIR}` placeholder, 런타임에 `ROOT` 절대경로 치환.
# ★ 2026-06-06 Bash-first 리팩터 — 기존 "파일마다 Read" 방식이 164파일 × 1턴 = 164턴 소진 →
#   max_turns 80 초과 반복 (회차 38·39·41·42 연속 실패). Bash grep 배치 먼저 → 히트 파일만 Read.
#   예상 턴 수: Bash 5턴 + 히트 파일 Read N턴(보통 0~20) + Fix M턴 → 총 40턴 내외.
_BASE_PROMPT = """\
워킹 디렉토리: {WORKDIR}

JARVIS 전체 파일 자가 진단·수정. **Bash 배치 먼저, 히트 파일만 Read** 원칙.
대상: .py / .md / .json / .yaml / .yml / .txt (규정·설정·코드 전부 포함)

## 수정 절대 금지 목록
- 폴더: .venv/ .git/ __pycache__/
- 파일: .env, *.sqlite, *.pkl, CLAUDE.md, JARVIS*/CLAUDE_*.md, BLOG_SUPREME_LAW.md
- 행위: git commit/push

## 단계 1 — Python Syntax 전수 검사 (Bash 1턴)
```
find {WORKDIR} -name "*.py" \
  -not -path "*/.venv/*" -not -path "*/__pycache__/*" -not -path "*/.git/*" \
  | xargs python -m py_compile 2>&1 | head -40
```
오류 출력된 파일만 Read → 수정.

## 단계 2 — 핵심 규정 위반 grep (Bash 1턴)
```
grep -rn \
  -e "from apscheduler" \
  -e "BackgroundScheduler\\|BlockingScheduler" \\
  -e "schedule\\.every\\|import schedule" \\
  -e "claude-[a-z]\\+-[0-9]" \\
  -e "anthropic\\.Anthropic()" \\
  --include="*.py" \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  {WORKDIR} 2>/dev/null | grep -v "JARVIS04_SCHEDULER/" | grep -v "shared/llm.py"
```
※ 모델 ID 리터럴은 `shared/llm.py` 의 MODELS 만 소유한다. 다른 파일에 모델 ID 가
   박혀 있으면 위반 — `from shared.llm import model_id` 로 파생하도록 고칠 것.
   (폐기된 모델명 목록을 여기 나열하지 않는 이유: 목록 자체가 또 하나의 사본이 된다)
히트 파일만 Read → 수정.

## 단계 3 — 버그 패턴 grep (Bash 1턴)
```
grep -rn \
  -e "^\\s*except:\\s*pass" \\
  -e "\\[가-힣\\]" \\
  --include="*.py" \
  --exclude-dir=.venv --exclude-dir=__pycache__ \
  {WORKDIR} 2>/dev/null \
  | grep -v "length_manager.py" | grep -v "shared/seo.py" | head -30
```
히트 파일만 Read → 실제 버그 여부 확인 후 수정.

## 단계 4 — import 깨짐 확인 (Bash 1턴)
```
python -c "
import sys, importlib
sys.path.insert(0, '{WORKDIR}')
mods = [
  'JARVIS00_INFRA.infra_agent',
  'JARVIS01_MASTER.router',
  'JARVIS02_WRITER.scheduler',
  'JARVIS04_SCHEDULER.scheduler_agent',
  'JARVIS07_GUARDIAN.guardian_agent',
  'JARVIS08_PUBLISH.platforms.naver_poster',
  'JARVIS08_PUBLISH.platforms.tistory_poster',
  'shared.llm',
  'shared.bus',
]
for m in mods:
    try: importlib.import_module(m); print('OK', m)
    except Exception as e: print('FAIL', m, e)
"
```
FAIL 출력된 모듈의 파일만 Read → import 경로·시그니처 수정.

## 단계 5 — 설정·규정 파일 이상 확인 (Bash 1턴)
```
# JSON 파싱 오류 확인
find {WORKDIR} -name "*.json" \
  -not -path "*/.venv/*" -not -path "*/__pycache__/*" \
  -not -name "*.sqlite" \
  | xargs -I{{}} python -c "import json,sys; json.load(open('{{}}'))" 2>&1 | grep -v "^$" | head -20
```
파싱 오류 난 .json 파일만 Read → 수정.

## 수정 원칙
- 실제 버그·오류만 수정. 스타일·리팩토링·주석 추가 금지.
- .py 파일: 수정 전 .bak 백업, 수정 후 ast.parse 검증.
- 확신 없으면 수정하지 말 것.
- **파일마다 무조건 Read 금지** — grep/import 검사에서 히트된 파일만 열 것.
- **파일 삭제 금지**: 미사용 파일이라도 삭제·이동·백업 폴더 생성 절대 금지. `_deleted_*` 폴더 루트 생성 엄금. 필요 시 주석 처리까지만.

완료 후 반드시 출력:
---REPAIR-SUMMARY---
수정 파일: N개
1. [경로:줄번호] 수정 내용 (원인)
이상 없음 파일: N개
---END-SUMMARY---
"""


def _esc(text: str) -> str:
    """Claude Code SDK 출력 등 외부 텍스트의 Telegram Markdown 특수문자 이스케이프.
    의도적 bold/italic 마크다운이 없는 summary·next 제안 등에만 적용.
    """
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _send_tg(msg: str) -> None:
    # 텔레그램 알림 비활성 (사용자 박제) — 로그만 기록
    log.info(f"[AutoRepair] {msg[:300]}")


def _parse_summary(output: str) -> str:
    """---REPAIR-SUMMARY--- 블록 추출. 없으면 마지막 1000자."""
    m = re.search(r'---REPAIR-SUMMARY---(.*?)---END-SUMMARY---', output, re.DOTALL)
    if m:
        return m.group(1).strip()
    tail = output.strip()[-1000:]
    return tail if tail else "(출력 없음)"


def _extract_fix_items(summary: str) -> list[tuple[str, str]]:
    """summary 에서 수정 항목 추출 — [(파일경로, 설명), ...].

    형식: "N. [파일명:줄번호] 수정 내용 1줄" 또는 "N. [파일명] 수정 내용".
    """
    items: list[tuple[str, str]] = []
    for line in summary.splitlines():
        m = re.match(r'^\s*\d+\.\s*\[([^\]:]+)(?::\d+)?\]\s*(.+?)\s*$', line)
        if m:
            fpath = m.group(1).strip()
            desc  = m.group(2).strip()
            if fpath and desc:
                items.append((fpath, desc))
    return items


def _parse_layer_counts(summary: str) -> dict:
    """수정 파일 수 파싱 — 전체 파일 검토(수정 파일: N개) + targeted(files_fixed: N) 두 포맷 모두 지원.

    ★ ERRORS [349] — `_TARGETED_PROMPT_TMPL`(Tier 2 targeted fix) 의 완료 보고 포맷은
      영문 `files_fixed: <N>` 인데, 본 정규식이 한글 "수정 파일" 만 인식해 targeted 경로는
      실제 수정 성공 여부와 무관하게 항상 0 으로 파싱 → `run_auto_repair_targeted` 가
      매번 False 반환, GUARDIAN 이 "Tier 1·2 모두 실패" 로 오보고했다. 두 포맷 모두 매치.
    """
    m = re.search(r'수정\s*파일[:\s]*(\d+)|files_fixed[:\s]*(\d+)', summary)
    files_fixed = int(m.group(1) or m.group(2)) if m else 0
    return {
        "files_fixed":   files_fixed,
        "syntax_fixed":  files_fixed,
        "rules_fixed":   0, "length_fixed":     0,
        "quality_fixed": 0, "data_cleaned":      0,
        "fixers_added":  0, "vision_pinned":     0,
        "domain_reg_fixed": 0,
    }


def _parse_self_scores(summary: str) -> dict:
    return {"quality": 0, "learning": 0, "vision": 0, "next": ""}


def _save_run_to_db(model: str, elapsed: int, returncode: int,
                    layers: dict, scores: dict, summary: str) -> int | None:
    """self_repair_runs DB 박제 — 학습 곡선 추적용."""
    try:
        from shared import db as _db
        # pattern_fixer stats 동시 캡처 — 회차당 학습 누적 추이
        try:
            from JARVIS07_GUARDIAN.pattern_fixer import stats as _pf_stats
            pf = _pf_stats()
            patterns_count  = pf.get("total_patterns", 0)
            hits_total      = pf.get("total_hits", 0)
            actionable_hits = pf.get("actionable_hits", 0)  # ★ 실제 자동 수정 가능 hits
        except Exception:
            patterns_count = hits_total = actionable_hits = 0

        # ★ 별칭을 두 번 세지 않는다 (2026-08-08 감사 — 항상 정확히 2배였다).
        #   `_parse_layer_counts` 는 호환을 위해 `syntax_fixed` 에 `files_fixed` 와
        #   **같은 값**을 넣는다. 그걸 `sum(layers.values())` 로 더하면 한 수정이 두 번 센다.
        #   실측: '수정 파일: 3개' → total_fixed 6 (106행 중 12행 오염).
        #   총계는 별칭이 아닌 **정본 필드**에서 파생한다(② — 사본을 더하지 않는다).
        _ALIAS_OF_FILES_FIXED = ("syntax_fixed",)
        total_fixed = layers.get("files_fixed", 0) + sum(
            v for k, v in layers.items()
            if k not in ("files_fixed",) + _ALIAS_OF_FILES_FIXED)
        with _db.get_db() as conn:
            cur = conn.execute("""
                INSERT INTO self_repair_runs
                (model, elapsed_sec, returncode,
                 syntax_fixed, rules_fixed, length_fixed, quality_fixed,
                 data_cleaned, fixers_added, vision_pinned, total_fixed,
                 patterns_count, hits_total, llm_saved_1d,
                 score_quality, score_learning, score_vision,
                 next_suggestion, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                model, elapsed, returncode,
                layers.get("syntax_fixed", 0), layers.get("rules_fixed", 0),
                layers.get("length_fixed", 0), layers.get("quality_fixed", 0),
                layers.get("data_cleaned", 0), layers.get("fixers_added", 0),
                layers.get("vision_pinned", 0), total_fixed,
                # ★ 새 정의는 **새 칸(`llm_saved_1d`)** 에 담는다 (2026-08-08 정정).
                #   종전엔 옛 칸 `llm_saved` 에 그대로 덮어썼는데, 그 칸의 앞 105행은
                #   `actionable_hits`(누적 패턴 수)라 정의가 다르다. 한 칸에 두 정의가
                #   섞이면 추세가 거짓말을 한다 — 실측으로 "50 → 0 (-50회)" 라는 가짜
                #   붕괴가 텔레그램에 나갔다. 옛 값은 건드리지 않고 읽지도 않는다.
                patterns_count, hits_total, _real_llm_saved(),
                scores.get("quality", 0), scores.get("learning", 0),
                scores.get("vision", 0), scores.get("next", ""),
                summary[:4000],
            ))
            return cur.lastrowid
    except Exception as e:
        log.warning(f"[AutoRepair] self_repair_runs 박제 실패: {e}")
        return None


def _learning_trend_brief() -> str:
    """최근 5회 회차 추세 요약 — 텔레그램 학습 진도 표시용."""
    try:
        from shared import db as _db
        with _db.get_db() as conn:
            rows = conn.execute("""
                SELECT ran_at, total_fixed, patterns_count, hits_total, llm_saved_1d,
                       score_quality, score_learning
                FROM self_repair_runs
                WHERE llm_saved_1d IS NOT NULL
                ORDER BY id DESC LIMIT 5
            """).fetchall()
        if not rows:
            return ""
        # 현재 actionable_hits — DB llm_saved (이제 actionable_hits 저장됨)
        latest = rows[0]
        oldest = rows[-1] if len(rows) > 1 else None
        if not oldest:
            return ""
        d_pat  = latest["patterns_count"] - oldest["patterns_count"]
        # ★ 같은 정의를 쓰는 행끼리만 뺀다 — WHERE 절이 옛 정의 행을 이미 걸러낸다.
        d_llm  = (latest["llm_saved_1d"] or 0) - (oldest["llm_saved_1d"] or 0)
        d_qual = latest["score_quality"]  - oldest["score_quality"]
        sign = lambda n: f"+{n}" if n > 0 else str(n)
        # 현재 stats() 에서 actionable 현황 실시간 조회
        try:
            from JARVIS07_GUARDIAN.pattern_fixer import stats as _pf_stats
            pf = _pf_stats()
            actionable     = pf.get("actionable", 0)
            actionable_hits = pf.get("actionable_hits", 0)
            extra = f"\n  • 자동수정 가능 패턴: {actionable}개 / 누적 hits: {actionable_hits}회"
        except Exception:
            extra = ""
        return (
            f"\n📈 *학습 추세* (최근 {len(rows)}회)\n"
            f"  • 패턴 누적: {oldest['patterns_count']} → {latest['patterns_count']} ({sign(d_pat)})\n"
            f"  • 실제 LLM 절약(1일): {oldest['llm_saved_1d'] or 0} → "
            f"{latest['llm_saved_1d'] or 0} ({sign(d_llm)}회){extra}\n"
            f"  • 품질 점수: {oldest['score_quality']} → {latest['score_quality']} ({sign(d_qual)})/10"
        )
    except Exception as e:
        log.debug(f"[AutoRepair] 학습 추세 로드 실패: {e}")
        return ""


def _real_llm_saved() -> int:
    """LLM 없이 실제로 고친 횟수 — **모르면 0** (2026-08-07 감사).

    ★ 종전 값(`actionable_hits`)은 "학습에 저장된 패턴 수" 였지 "그 패턴으로 고친 수" 가
      아니었다. 저장된 패치 47개 중 diff 컨텍스트가 맞아 **복원 가능한 것은 3개(6.4%)** 다.
      나머지를 절약으로 계상하면 지표가 실제와 반대 방향으로 부풀어 오른다.
    """
    try:
        from shared.db import get_db
        with get_db() as con:
            return int(con.execute(
                "SELECT count(*) FROM error_log "
                "WHERE status='fixed' AND llm_attempts = 0 AND fixed_file IS NOT NULL "
                "  AND fixed_at > datetime('now','-1 day')").fetchone()[0] or 0)
    except Exception:
        return 0


def _record_repairs_to_guardian(summary: str) -> int:
    """summary 의 각 수정 항목을 GUARDIAN learned_patterns 에 자동 박제.

    Returns:
        int: 박제 성공 건수
    """
    items = _extract_fix_items(summary)
    if not items:
        return 0
    try:
        from JARVIS07_GUARDIAN.error_collector import record_external_change
    except ImportError:
        return 0
    ok = 0
    for fpath, desc in items:
        try:
            eid = record_external_change(
                source="auto_repair",
                fixed_file=fpath,
                description=desc[:400],
                error_type="AutoRepairFix",
                severity="low",
                actor="claude_code_cli",
            )
            if eid:
                ok += 1
        except Exception as e:
            log.debug(f"[AutoRepair] guardian 박제 실패 ({fpath}): {e}")
    if ok:
        log.info(f"[AutoRepair] GUARDIAN 박제 완료 — {ok}건 (learned_patterns 자동 갱신)")
    return ok


def _snapshot_py_files() -> dict[str, str]:
    """Claude Code SDK 실행 전 파일 내용 스냅샷 — 이후 diff 계산용.

    git repo 가 없어도 동작. 실행 후 파일 내용과 비교하여 변경분 감지.
    대상: .py / .md / .json / .yaml / .yml (절대금지 목록 제외)
    """
    _DENY_DIRS  = {".venv", "__pycache__", ".git", "chrome_profile", "logs"}
    _DENY_FILES = {"CLAUDE.md", "BLOG_SUPREME_LAW.md", ".env"}
    _DENY_EXT   = {".sqlite", ".pkl", ".bak"}
    _INCLUDE_EXT = {".py", ".md", ".json", ".yaml", ".yml"}
    snap: dict[str, str] = {}
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(d in p.parts for d in _DENY_DIRS):
            continue
        if p.suffix in _DENY_EXT:
            continue
        if p.name in _DENY_FILES or p.suffix not in _INCLUDE_EXT:
            continue
        try:
            snap[str(p.relative_to(ROOT))] = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
    return snap


def _compute_diffs(py_snapshot: dict | None) -> dict[str, str]:
    """snapshot 대비 *변경된* 파일들의 unified diff 반환 — {rel_path: diff_text}.

    _capture_diff_patches(배치 sweep) + run_auto_repair_targeted(반응형 밴딧 브리지) 공용.
    git repo 유무와 무관 (file snapshot 방식).
    """
    if not py_snapshot:
        return {}
    import difflib as _dl
    out: dict[str, str] = {}
    for rel_path, before_content in py_snapshot.items():
        target = ROOT / rel_path
        if not target.exists():
            continue
        try:
            after_content = target.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if after_content == before_content:
            continue
        diff_lines = list(_dl.unified_diff(
            before_content.splitlines(keepends=True),
            after_content.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            n=5,
        ))
        if diff_lines:
            out[rel_path] = "".join(diff_lines)
    return out


def _capture_diff_patches(layers: dict, py_snapshot: dict | None = None) -> int:
    """Claude Code SDK 수정 완료 후 변경된 파일 diff → auto_patch 학습 저장.

    git repo 유무와 무관하게 동작 (file snapshot 방식).
    이후 동일 파일에서 같은 위반이 재발하면 LLM/SDK 호출 없이 즉시 재적용 가능.
    Returns: 저장된 패치 수
    """
    diffs = _compute_diffs(py_snapshot)
    if not diffs:
        return 0

    try:
        from JARVIS07_GUARDIAN.pattern_fixer import record_pattern_hit
    except ImportError:
        return 0

    stored = 0
    for rel_path, diff_text in diffs.items():
        try:
            fake_record = {
                "error_type": "AutoRepairFix",
                "message":    rel_path,
                "source":     "auto_repair",
            }
            record_pattern_hit(
                fake_record,
                fixer_name="auto_patch",
                fixed_file=rel_path,
                source="auto_repair",
                patch=diff_text,
                target_file=rel_path,
            )
            stored += 1
            log.info(f"[AutoRepair] auto_patch 저장: {rel_path}")
        except Exception as e:
            log.debug(f"[AutoRepair] auto_patch 저장 실패 ({rel_path}): {e}")

    if stored:
        log.info(f"[AutoRepair] ★ auto_patch {stored}개 learned_patterns 저장 — 재발 시 LLM 0")
    return stored


def _heartbeat_thread(start_ts: float, stop_event):
    """★ 사용자 박제 2026-05-15 — 5분 간격 텔레그램 진행 heartbeat.
    Claude Code SDK 가 15분까지 일하는 동안 사용자가 *진짜 작동 중인지* 확인 가능."""
    import threading as _th
    while not stop_event.wait(timeout=300):  # 5분
        elapsed = int(time.time() - start_ts)
        m, s = elapsed // 60, elapsed % 60
        _send_tg(
            f"⏳ *자가 진단·수정 진행 중* — {m}분 {s}초 경과\n"
            f"Claude Code SDK Sonnet 5 가 전체 파일 정밀 검토 진행 중..."
        )


def run_auto_repair() -> None:
    """★ 하네스 5-Layer 게이트 — 자가 진단·수정 (수정→기록→누적→순환) 2026-05-18.

    Layer 2: ① 준비(컨텍스트 수집) → ② Claude Code SDK 실행
    Layer 3: returncode + summary 유효성 검증 + 즉시수정훅(auth 알림)
    Layer 4: GUARDIAN 박제 + DB 저장 + TG 완료 알림
    max_attempts — harness.DEFAULT_MAX_ATTEMPTS(SSOT) 상속. 2026-07-21 사용자 박제로 2회.
    (2026-07-06 '3회 통일' 은 폐지 — 상한은 harness 한 곳에서만 정의)
    원래 2(SDK 비용 고려)였으나
    "재시도는 무조건 3회" 지시로 상향. heartbeat는 run_action() 전체 기간 유지.
    """
    import threading as _th
    # ★ P1-③ 패치 (사용자 박제 2026-05-18 — ADR 009 v2): harness ImportError fallback 제거.
    # 이전: ImportError 시 _run_auto_repair_legacy() (검증 0회 우회 가능).
    # 현재: ImportError 시 차단 + TG 알림. auto_repair 는 자가수정이므로 중지 영향 적음.
    try:
        from JARVIS00_INFRA.harness import action_step, ActionDefinition, run_action, Issue as _Issue
    except ImportError as _ie:
        _g_report("master", _ie, module=__name__)
        try:
            _send_tg(f"🚨 [auto_repair] harness ImportError — 자가수정 차단 (송출 우회 금지)\n사유: {_ie}")
        except Exception:
            pass
        return

    start = time.time()
    log.info("[AutoRepair] 자가 수정 시작 (하네스)")
    # ★ 사용자 박제 2026-05-18 v2 — 자가진단·발행 *하나의 세트* (동일 callback)
    from datetime import datetime as _dt0
    _hr = _dt0.now().hour
    _pre_label = ""
    if _hr == 7:
        _pre_label = " — 경제 브리핑 세트 (진단 → 발행)"
    elif _hr == 16:
        _pre_label = " — 테마글 세트 (진단 → 발행)"
    _send_tg(
        f"🚀 *JARVIS 자가 진단·수정 시작*{_pre_label} (Sonnet 5)\n"
        "전체 파일 정밀 검토 중... (최대 20분)\n"
        "5분 간격 진행 상황 자동 알림"
    )

    # ★ heartbeat — harness 전체 실행 기간(재시도 포함) 동안 5분마다 TG 알림
    _hb_stop = _th.Event()
    _hb_thread = _th.Thread(
        target=_heartbeat_thread, args=(start, _hb_stop),
        daemon=True, name="auto_repair_heartbeat",
    )
    _hb_thread.start()

    try:
        # ── [L2] ① 정적 패턴 + 학습 패치 우선 적용 ──────────────────
        # ★ 사용자 박제 2026-05-31 — Tier 1: 정적패턴·학습패치 우선 → Tier 2: Claude Code SDK
        @action_step(name="① 정적 패턴·학습 패치 우선 적용")
        def _step_pre_patch(state: dict):
            pre_applied = 0
            try:
                from JARVIS07_GUARDIAN.pattern_fixer import apply_stored_patches
                pre_applied = apply_stored_patches()
                if pre_applied:
                    log.info(f"[AutoRepair] ★ 학습 패치 {pre_applied}건 선적용 (Claude 전)")
            except Exception as _pe:
                log.warning(f"[AutoRepair] 학습 패치 선적용 실패: {_pe}")
            return {"pre_applied": pre_applied}

        # ── [L2] ② 준비: 컨텍스트 수집 ────────────────────────────
        @action_step(name="② 준비: 컨텍스트 수집")
        def _step_prepare(state: dict):
            prompt = _BASE_PROMPT.replace("{WORKDIR}", str(ROOT.resolve()))
            # ★ SDK 실행 전 파일 스냅샷 — git repo 없어도 변경 diff 계산 가능
            py_snapshot = _snapshot_py_files()
            log.debug(f"[AutoRepair] 스냅샷: {len(py_snapshot)}개 .py 파일")
            return {"prompt": prompt, "py_snapshot": py_snapshot}

        # ── [L2] ③ Claude Code SDK 실행 ────────────────────────────
        @action_step(name="③ Claude Code SDK 실행")
        def _step_run_cli(state: dict):
            # ★ 사용자 박제 2026-06-07 — Claude CLI 잔존 흔적 일소.
            # 옛 동작: 호출자가 anyio+query 인라인 + PATH·OAuth 직접 관리 (3곳 복붙) →
            # cli_not_found / MessageParseError / API 가짜 키 누수 반복.
            # 새 동작: shared.claude_sdk_compat.run_sdk_query 단일 진입점 — 모든 환경 보장.
            from shared.claude_sdk_compat import run_sdk_query
            result = run_sdk_query(   # ★ 배경 작업 — 순번 대기 상한 적용 (ERRORS [474])
                prompt=state["prompt"], model=_MODEL,
                cwd=str(ROOT),
                background=True,   # ★ 심층감사도 배경 — 발행 우선 (ERRORS [474])
                max_turns=60,  # ★ 2026-06-06 박제 — Bash-first 방식: Bash 5턴 + 히트파일 처리 40턴 내외.
                permission_mode="bypassPermissions",
                timeout=_TIMEOUT,
            )
            log.info("[AutoRepair] SDK 완료 elapsed=%ds stdout=%d rc=%d",
                     result["elapsed"], len(result.get("stdout", "")), result["returncode"])
            return result

        # ── [L3] 순수 검증 ─────────────────────────────────────────
        def _verify(state: dict) -> list:
            """★ 순수 검증만 — 수정은 _fix 훅 담당."""
            issues = []
            rc     = state.get("returncode", -999)
            stdout = state.get("stdout", "")
            stderr = state.get("stderr", "")

            if rc == -2:
                issues.append(_Issue(
                    step="③ Claude Code SDK 실행", kind="timeout",
                    detail=f"SDK 타임아웃 ({_TIMEOUT}s 초과)",
                ))
            elif rc != 0:
                combined = (stdout + "\n" + stderr).lower()
                if "credit balance" in combined or "credits" in combined:
                    issues.append(_Issue(
                        step="③ Claude Code SDK 실행", kind="auth_error",
                        detail="Claude Code 인증 오류 (API 키 모드 전환·잔액 부족)",
                    ))
                else:
                    err_tail = (stderr or stdout).strip()[-200:]
                    issues.append(_Issue(
                        step="③ Claude Code SDK 실행", kind="sdk_error",
                        detail=f"exitcode={rc}: {err_tail}",
                    ))

            if rc == 0:
                summary = _parse_summary(stdout)
                if summary == "(출력 없음)":
                    issues.append(_Issue(
                        step="③ Claude Code SDK 실행", kind="empty_output",
                        detail="SDK stdout 완전 빈값 (turns 소진 또는 실행 실패)",
                    ))
            return issues

        # ── CI 게이트 (2026-08-05 — 축소판) ───────────────────────
        #
        # ★ 무엇을 하고 무엇을 안 하는가
        #   한다  — SDK 가 저장소를 고친 뒤 CI 와 *같은 검사* 를 돌려보고, 실패하면
        #           ① 텔레그램 경보 ② **검증 안 된 diff 를 학습에 넣지 않는다**
        #   안 한다 — 롤백. 스냅샷 복원은 `dashboard/node_modules`(1,039개)와 학습 산출물
        #           (`learned_patterns.json` 등)까지 되돌린다. "안전 게이트" 라는 이름으로
        #           저장소에서 가장 파괴적인 경로를 만드는 셈이라 폐기했다.
        #           변경은 워킹트리에 남고, 사람이 커밋할 때 pre-commit 훅이 차단한다.
        #
        # ★ 실측 근거로 범위를 좁혔다: auto_repair 는 최근 10회 연속 수정 0건이고
        #   (마지막 실제 변경 2026-07-17), auto_patch 학습은 1건·diff 없음이었다.
        #   유일하게 실재하는 위험이 "무가드 diff 의 학습 오염" 이라 그것만 막는다.
        #
        # ★ 인터프리터를 명시 치환한다 — 데몬은 `.../Python.app/Contents/MacOS/Python`
        #   으로 도는데 그 디렉터리엔 `python3` 도 `pytest` 도 없다. CI 문자열의
        #   `python3` 를 그대로 쓰면 엉뚱한 파이썬이 잡혀 *멀쩡한 수정이 실패로 판정* 된다.
        def _ci_gate_failures() -> list:
            """CI 가 정의한 검사를 그대로 돌려 실패 목록을 반환. 킬스위치 `GUARDIAN_CI_GATE=0`."""
            import os as _os
            import re as _re
            import subprocess as _sp
            import sys as _sys
            if _os.getenv("GUARDIAN_CI_GATE", "1") == "0":
                return []
            ci = ROOT / ".github" / "workflows" / "ci.yml"
            if not ci.exists():
                return ["ci.yml 부재 — 게이트 검사 불가"]
            cmds = []
            for line in ci.read_text(encoding="utf-8").splitlines():
                m = _re.match(r"\s*(?:run:\s*)?(python3?\s+-m\s+pytest[^\n]*|python3?\s+shared/precommit_check\.py[^\n]*)", line)
                if m:
                    cmds.append(m.group(1).strip())
            if not cmds:
                return ["ci.yml 에서 검사 명령을 파생하지 못함"]
            bad = []
            for c in cmds:
                c = _re.sub(r"^python3?\b", _sys.executable, c)
                try:
                    r = _sp.run(c, shell=True, cwd=str(ROOT), capture_output=True,
                                text=True, timeout=600)
                    if r.returncode != 0:
                        bad.append(f"{c.split()[-2:]}: rc={r.returncode} {(r.stdout or r.stderr)[-200:]}")
                except Exception as e:
                    bad.append(f"{c}: {type(e).__name__}: {e}")
            return bad

        # ── [L3] 즉시 수정 훅 ─────────────────────────────────────
        def _fix(state: dict, issues: list) -> tuple:
            """SDK 오류는 인라인 수정 불가 — unfixed 전체 반환 (harness 가 재실행).
            auth_error 에 한해 재인증 안내 TG 발송.
            """
            for iss in issues:
                if iss.kind == "auth_error":
                    _send_tg(
                        "⚠️ *자가 수정 인증 오류 — 재인증 필요*\n"
                        "`claude auth status` 로 OAuth 세션 확인 후\n"
                        "`claude auth login` 재인증 필요.\n"
                        "원인: API 키 환경변수가 격리되지 않아 잔액 모드 전환."
                    )
                elif iss.kind == "cli_not_found":
                    _send_tg(
                        "❌ *자가 수정 실패*: claude 바이너리 PATH 미등록.\n"
                        "원인: launchd 기동 시 PATH 최솟값. 데몬 재시작 또는 PATH 확인 필요."
                    )
            return [], list(issues)  # 모두 unfixed → harness 재실행

        # ── [L4] 송출 — GUARDIAN 박제 + DB + TG ──────────────────
        def _send(state: dict):
            stdout      = state.get("stdout", "")
            elapsed     = state.get("elapsed", 0)
            rc          = state.get("returncode", 0)
            pre_applied = state.get("pre_applied", 0)
            summary = _parse_summary(stdout)

            guardian_recorded = _record_repairs_to_guardian(summary)
            layers = _parse_layer_counts(summary)
            # 학습 패치 선적용 건수를 총 수정에 합산
            if pre_applied:
                layers["syntax_fixed"] = layers.get("syntax_fixed", 0) + pre_applied
            scores = _parse_self_scores(summary)
            # ★ 변경된 파일 diff → auto_patch 학습 저장 (재발 시 LLM 0 재적용, git 불필요)
            py_snapshot = state.get("py_snapshot", {})
            # ★ 검증 안 된 diff 를 학습에 넣지 않는다 (2026-08-05).
            #   여기 들어간 패치는 발행 직전 Tier-1 sweep 이 LLM 0 으로 재적용한다 —
            #   즉 한 번 잘못 들어가면 매 발행마다 되풀이된다.
            _ci_bad = _ci_gate_failures()
            if _ci_bad:
                log.error(f"[auto_repair] CI 게이트 실패 {len(_ci_bad)}건 — 학습 적재 건너뜀")
                _send_tg("⚠️ *자가수정 사후 검증 실패*\n"
                         + "\n".join(f"· {b}" for b in _ci_bad[:3])
                         + "\n\n_변경은 워킹트리에 남아 있습니다. 커밋 시 pre-commit 훅이 차단합니다._"
                           "\n_검증 안 된 패치는 학습에 넣지 않았습니다._")
                try:
                    from JARVIS07_GUARDIAN.error_collector import report as _rep
                    _rep("AutoRepairCIGateFailed", "guardian",
                         message=f"자가수정 후 CI 검사 실패: {_ci_bad[:2]}",
                         module=__name__, func_name="_send",
                         context={"kind": "sdk_error", "failures": _ci_bad[:3]})
                except Exception:
                    pass
            else:
                _capture_diff_patches(layers, py_snapshot)
            run_id = _save_run_to_db(_MODEL, elapsed, rc, layers, scores, summary)
            trend  = _learning_trend_brief()

            # ★ 사용자 박제 2026-05-18 v2 — 자가진단·발행 *하나의 세트* 안내.
            # 현재 시각 기반으로 어느 세트인지 판정 → 다음 페이즈 (발행) 안내.
            from datetime import datetime as _dt
            _now = _dt.now()
            _next_pub = ""
            if _now.hour == 6:
                _next_pub = "\n⏰ *다음 페이즈 (동일 세트)*: 경제 브리핑 발행 즉시 시작"
            elif _now.hour == 16:
                _next_pub = "\n⏰ *다음 페이즈 (동일 세트)*: 테마글 발행 즉시 시작"

            # 코드 변경 발생 여부 — Python import 캐시 때문에 즉시 반영 안 됨
            _code_changed = sum(layers.get(k, 0) for k in (
                "syntax_fixed", "rules_fixed", "length_fixed", "quality_fixed", "data_cleaned"
            ))
            _restart_hint = ""
            if _code_changed > 0:
                _restart_hint = (
                    f"\n\n⚠️ *코드 변경 {_code_changed}건 발생*"
                    f"\n  → Python import 캐시로 즉시 반영 안 됨."
                    f"\n  → 비코드 효과 (learned_patterns·DB·정책) 는 발행에 *즉시* 반영."
                    f"\n  → 코드 변경 완전 발효는 *데몬 재시작 후* (이번 발행 끝나고 권장)."
                )

            _pre_note = f"\n🧠 학습 패치 선적용: {pre_applied}건" if pre_applied else ""
            _gd_note  = f"\n📚 GUARDIAN 박제: {guardian_recorded}건" if guardian_recorded else ""
            _score_note = ""
            if any(scores[k] for k in ("quality", "learning", "vision")):
                _score_note = (
                    f"\n\n🎯 *자기 평가*"
                    f"\n  • 코드 품질: {scores['quality']}/10"
                    f"\n  • 학습 누적: {scores['learning']}/10"
                    f"\n  • 비전 정합: {scores['vision']}/10"
                )
                if scores["next"]:
                    _score_note += f"\n  • 다음 회차: {_esc(scores['next'][:120])}"
            _send_tg(
                f"✅ *자가 진단·수정 완료* (Sonnet 5, run #{run_id or '?'} "
                f"— {elapsed // 60}분 {elapsed % 60}초){_next_pub}\n\n{_esc(summary)}"
                f"{_pre_note}{_gd_note}{_score_note}{trend}{_restart_hint}"
            )

        action_def = ActionDefinition(
            name="auto-repair",
            steps=[_step_pre_patch, _step_prepare, _step_run_cli],
            verify=_verify,
            fix=_fix,    # ★ "수정→기록→누적→순환" 전체 에이전트 디폴트 (사용자 박제 2026-05-18)
            send=_send,
            # ★ max_attempts 미지정 = harness.DEFAULT_MAX_ATTEMPTS 상속 (SSOT, 현재 2회)
        )
        result = run_action(action_def)

        if not result.delivered:
            elapsed_total = int(time.time() - start)
            _reason = result.escalation_reason or '하네스 검증 한도 초과'
            _send_tg(
                f"❌ *자가 수정 실패 — 수동 확인 필요*\n"
                f"사유: {_reason}\n"
                f"소요: {elapsed_total // 60}분 {elapsed_total % 60}초"
            )
            # ★ 사용자 박제 2026-06-07 — 실패 회차도 self_repair_runs 박제 (통계 누수 방지).
            # 옛 동작: delivered=False 면 _send 미호출 → DB 박제 0건 → 대시보드 학습 곡선이
            #          *성공 회차만* 반영해 실제 상태 왜곡. 발행 callback 안 실패 6회 (6/4~6/7)
            #          가 통계에 안 보이는 사고 직결.
            try:
                _save_run_to_db(
                    _MODEL, elapsed_total, -99,
                    {}, {}, f"(송출 실패: {_reason[:160]})"
                )
            except Exception as _se:
                log.warning("[AutoRepair] 실패 회차 박제 실패: %s", _se)

    finally:
        # ★ heartbeat 스레드 정지 — 종료 경로 무엇이든 무조건 정지
        _hb_stop.set()


_TARGETED_TIMEOUT = 600  # 최대 10분 (full 15분의 2/3)

def _incidents_block(context: str) -> str:
    """오류 문맥 → 과거 유사 사고 블록 (ERRORS [534]).

    ★ ① 단일 진입점: 검색·포맷은 `repair_history` 소유. 여기선 호출만 한다.
    ★ fail-open: 검색이 실패해도 수리는 진행돼야 하므로 빈 문자열로 degrade.
      단 실패를 조용히 넘기지 않고 로그로 남긴다(무증상 열화 금지).
    킬스위치 `GUARDIAN_INCIDENT_SEARCH=0` → 종전처럼 검색 없이 진행.
    """
    import os as _os
    if (_os.getenv("GUARDIAN_INCIDENT_SEARCH") or "").strip().lower() in ("0", "false", "off", "no"):
        return "(과거 사고 검색 비활성 — GUARDIAN_INCIDENT_SEARCH=0)"
    try:
        from JARVIS07_GUARDIAN.repair_history import incidents_brief
        return incidents_brief(context or "", top_k=3) or "(과거 유사 사고 없음)"
    except Exception as e:  # noqa: BLE001
        log.warning("[AutoRepair] 과거 사고 검색 실패 — 검색 없이 진행: %s", e)
        return "(과거 사고 검색 실패)"


_TARGETED_PROMPT_TMPL = """\
당신은 JARVIS 포스팅 실패를 즉각 수정하는 Claude Code 에이전트입니다.
워킹 디렉토리: {WORKDIR}

## 실패 상황
- 작업 ID: {job_id}
- 실패 플랫폼: {failed_platforms}
{theme_line}

## 오류 내용
{context}

## ★ 반드시 따를 수정 절차 (순서 엄수)

### 1단계 — 과거 유사 사고 확인 (필수)
{incidents}
위 검색 결과는 `repair_history.search_incidents()` 가 ERRORS.md **전체**(사고 186건·
헛다리 373건)를 이 오류 문장으로 조준 검색한 것이다.
- 유사 사고가 있으면 **기록된 해결책을 먼저 적용**한다.
- ⛔ **헛다리로 표시된 접근은 절대 다시 시도하지 마라** — 이미 실측으로 기각된 가설이다.
- 검색 결과가 비어 있으면 과거 사례가 없다는 뜻이다. `grep` 으로 ERRORS.md 를 추가
  탐색해도 되지만, **전체를 통독하지는 마라** (긴 입력은 오히려 정확도를 떨어뜨린다).

### 2단계 — 오류 메시지에서 핵심 키워드 추출
오류 내용에서 다음 중 해당하는 것을 파악:
- "키워드 '...' body 등장 N회" → `JARVIS02_WRITER/economic_poster.py` 의 `_validate_draft_issues` 함수 키워드 검증 로직
- "draft_invalid" → 초안 검증 함수 (`_validate_draft_issues` 또는 `trend_theme_writer._validate_draft`)
- "이미지 연속" → `JARVIS06_IMAGE/validators/image_validators.py` + `law_enforcer.py`
- "분량 초과·하한" → `JARVIS02_WRITER/length_manager.py` + `post_type_specs.py`
- "ImportError / AttributeError" → traceback 파일 직접 수정

### 3단계 — 관련 코드 검색 (Bash로 직접)
```bash
# 오류 메시지 키워드로 코드 찾기
grep -rn "관련_함수명" JARVIS02_WRITER/ --include="*.py" | head -20
grep -rn "관련_함수명" JARVIS00_INFRA/ --include="*.py" | head -20
```

### 4단계 — 근본 원인 파악 후 코드 수정
- 표면 증상(0회 등장, 검증 실패)이 아닌 **버그 코드 자체**를 고쳐야 함
- 수정 전 `.bak` 백업: `cp target.py target.py.bak`
- 수정 후 구문 검증: `python -m py_compile target.py`

### 5단계 — 수정이 불가한 경우
LLM 이 해당 키워드를 본문에 삽입할 수 없는 구조적 이유 (예: 관련 없는 주제, 데이터 없음)라면:
해당 키워드를 **발행 대상에서 제외**하는 로직을 검토하고 그 내용을 보고에 명시.

## 완료 보고 (필수 — 없으면 학습 누락)
---REPAIR-SUMMARY---
files_fixed: <N>
syntax_fixed: 0
rules_fixed: 0
length_fixed: 0
quality_fixed: 0
data_cleaned: 0
fixers_added: 0
vision_pinned: 0
---END-SUMMARY---
"""


def run_auto_repair_targeted(
    context: str,
    job_id: str,
    failed_platforms: list[str],
    theme: str = "",
    error_record: dict | None = None,
) -> bool:
    """포스팅 실패에 특화된 targeted fix (빠른 수정, 전체 진단 아님).

    incident_responder Tier 2 (LLM 자동 수정) — Tier 1(패턴·Bandit) 실패 시 호출.
    최대 10분 timeout.

    ★ 밴딧 학습 브리지 (사용자 박제 2026-06-28): error_record 가 주어지면
      SDK 실행 전 파일 스냅샷 → 수정 성공 후 diff 를 *원본 오류 fingerprint* llm_patch 로
      학습 + 밴딧 보상 (pattern_fixer.record_sdk_fix). 이로써 SDK 자동수정이 밴딧을 비대화시킨다.

    Returns:
        True: 적어도 1개 파일 수정 완료
        False: 수정 없음 또는 실패
    """
    log.info("[AutoRepair/Targeted] 시작: job=%s, platforms=%s", job_id, failed_platforms)

    theme_line = f"- 테마: {theme}" if theme else ""
    # ★ ERRORS [534] — 종전 `head -60`(전체의 0.6%·최신 1건)을 **조준 검색**으로 교체.
    #   1.2MB 를 통독시키지도, 앞부분만 보여주지도 않는다 — 이 오류와 유사한 사고
    #   상위 3건만 주입한다(헛다리 포함). 검색 실패 시 빈 문자열 → 프롬프트는 정상 동작.
    prompt = _TARGETED_PROMPT_TMPL.format(
        WORKDIR=str(ROOT.resolve()),
        job_id=job_id,
        failed_platforms=", ".join(failed_platforms),
        theme_line=theme_line,
        context=context[-3000:],
        incidents=_incidents_block(context),
    )

    # ★ 밴딧 학습 브리지 — error_record 있으면 SDK 실행 전 스냅샷 (수정 후 diff 계산용)
    _pre_snapshot = _snapshot_py_files() if error_record else None

    # ★ 사용자 박제 2026-06-07 — Claude CLI 잔존 흔적 일소.
    # PATH·OAuth·MessageParseError 처리 모두 run_sdk_query 가 자동 흡수.
    from shared.claude_sdk_compat import run_sdk_query
    result = run_sdk_query(
        prompt=prompt, model=_MODEL,
        cwd=str(ROOT),
        permission_mode="bypassPermissions",
        timeout=_TARGETED_TIMEOUT,
        background=True,   # ★ 순번 대기 상한 — 줄이 길면 포기하고 defer (ERRORS [474])
    )
    # ★ 계약 위반을 크래시로 만들지 않는다 (2026-08-07 감사).
    #   run_sdk_query 는 이제 None 을 내지 않지만, 몽키패치·구버전 등으로 깨질 수 있다.
    #   그때 `result["elapsed"]` 는 즉시 터지고, 위쪽 except 가 삼키면 원인이 사라진다.
    if not isinstance(result, dict):
        log.error(f"[AutoRepair/Targeted] run_sdk_query 계약 위반: {type(result).__name__} 반환")
        result = {"returncode": -3, "stdout": "", "stderr": "SDK 반환 계약 위반",
                  "elapsed": 0, "error_kind": "sdk_error"}
    elapsed = result.get("elapsed", 0)
    rc      = result.get("returncode", -3)

    if rc != 0:
        kind = result.get("error_kind") or "sdk_error"
        if kind == "deferred":
            # ★ ERRORS [474] — LLM 순번을 못 잡아 *시작도 못 한* 것. 실패가 아니다.
            #   알림 없이 조용히 물러나고 다음 retry_pending 이 재시도한다.
            log.info("[AutoRepair/Targeted] 순번 대기 초과로 보류(defer) — %s",
                     result.get("stderr", ""))
            return False
        if kind == "cli_not_found":
            _send_tg("❌ *targeted 수정 실패*: claude 바이너리 PATH 미등록.")
        elif kind == "timeout":
            _send_tg(f"⏰ *targeted 수정 timeout* ({_TARGETED_TIMEOUT}초 초과)")
        else:
            _send_tg(f"❌ *targeted 수정 예외*: {(result.get('stderr') or '?')[:200]}")
        log.error("[AutoRepair/Targeted] 실패(%s): %s", kind, result.get("stderr",""))
        return False

    output = result["stdout"]
    summary = _parse_summary(output)
    counts  = _parse_layer_counts(summary)
    files_fixed = counts.get("files_fixed", 0)

    log.info("[AutoRepair/Targeted] 완료: %ds, files_fixed=%d", elapsed, files_fixed)
    _send_tg(
        f"{'✅' if files_fixed > 0 else '⚠️'} *targeted 수정 완료* "
        f"(job={job_id}, {elapsed}초)\n"
        f"파일 수정: {files_fixed}개"
    )

    if files_fixed > 0:
        _record_repairs_to_guardian(summary)
        # ★ 밴딧 학습 브리지 — SDK 수정 diff → 원본 오류 fingerprint llm_patch 등록 + 밴딧 보상
        if error_record and _pre_snapshot:
            try:
                from JARVIS07_GUARDIAN.pattern_fixer import record_sdk_fix
                diffs = _compute_diffs(_pre_snapshot)
                if record_sdk_fix(error_record, diffs):
                    log.info("[AutoRepair/Targeted] ★ 밴딧 학습 완료 — SDK 수정이 밴딧 arm 으로 자산화")
            except Exception as e:
                log.debug("[AutoRepair/Targeted] 밴딧 학습 브리지 실패: %s", e)

    return files_fixed > 0


