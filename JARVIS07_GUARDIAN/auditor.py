"""JARVIS07 Auditor — 헌법 위반·드리프트 검출 + Refine Rules 제안.

A모델 분리 (ADR 007 — Self-Evolving Harness 비전):
  - auto_repair / pattern_fixer 는 *진단·수정* 만
  - eval_agent 는 *수정 결과 평가 + 학습 자산화 결정* 만
  - auditor 는 *헌법 위반·드리프트 검출 + Refine Rules 제안* 만 ← 본 모듈

# 실행 위치
JARVIS04 스케줄 DEFAULT_JOBS 의 `auditor_weekly` 잡 — 주 1회 일요일 04:30 자동 실행.

# 책임 (단일 진입점)

run(now=None) → AuditResult
  종합 감사 수행 + 결과 반환 + 텔레그램 보고.

audit_constitution_violations()  — shared/precommit_check.py 27종 호출
audit_repeated_lessons(window_days=30) — ERRORS.md 최근 30일 회고
audit_learned_patterns_meta_learning() — 5회+ 반복 → 새 fixer 신설 제안

# 출력
AuditResult dataclass — 텔레그램 인라인 버튼 ✅/❌ 게이트로 사용자 결정 받음.

# 모델
대부분 정적 검사. 메타 학습 제안 시만 Opus 4.6 (audit_refine alias) 호출 — 추론 깊이 필요.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("jarvis.guardian.auditor")

_ROOT = Path(__file__).resolve().parents[1]
_LEARNED_JSON = Path(__file__).resolve().parent / "learned_patterns.json"
_ERRORS_MD = Path(__file__).resolve().parent / "ERRORS.md"


# ──────────────────────────────────────────────────────────────
# 결과 dataclass
# ──────────────────────────────────────────────────────────────

@dataclass
class ConstitutionAudit:
    """precommit_check 27종 검증 결과."""
    total_violations: int
    by_category: dict[str, int]
    sample: list[str]                    # 첫 5건 예시
    output_full: str = ""


@dataclass
class RepeatedLessons:
    """ERRORS.md 최근 N일 동일 교훈 3회+ 반복 검출."""
    window_days: int
    repeated: list[dict[str, Any]]       # [{lesson, count, refs}]


@dataclass
class MetaLearningSuggestion:
    """5회+ 반복 패턴 → 새 fixer 신설 제안."""
    candidates: list[dict[str, Any]]     # [{error_type, fingerprint, hit_count, sample_message}]


@dataclass
class DomainDistribution:
    """★ ADR 008 Phase 4 — 도메인별 학습 패턴 분포·skew·top fixer.

    *도메인 skew* = 한 도메인에 패턴이 과도하게 집중 → 그 도메인 *근본 리팩터*
    필요 신호 (예: image 도메인 40+ → 작성 단계 자체 결함). 임계값 25개.
    """
    by_domain:        dict[str, int]              # 도메인별 패턴 수
    by_domain_hits:   dict[str, int]              # 도메인별 누적 히트
    skewed_domains:   list[dict[str, Any]]        # 임계값 초과 도메인 + 사유
    unknown_count:    int                          # 도메인 분류 실패 잔존
    top_fixers_by_domain: dict[str, list[str]]    # 도메인별 상위 fixer 3개


@dataclass
class AuditResult:
    """종합 감사 결과."""
    audited_at: str
    constitution: ConstitutionAudit
    repeated_lessons: RepeatedLessons
    meta_learning: MetaLearningSuggestion
    domain_distribution: DomainDistribution | None = None   # ★ ADR 008 Phase 4
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {
            "audited_at": self.audited_at,
            "constitution": asdict(self.constitution),
            "repeated_lessons": asdict(self.repeated_lessons),
            "meta_learning": asdict(self.meta_learning),
            "summary": self.summary,
        }
        if self.domain_distribution is not None:
            d["domain_distribution"] = asdict(self.domain_distribution)
        return d


# ──────────────────────────────────────────────────────────────
# 1) 헌법 위반 검출 — shared/precommit_check.py 호출
# ──────────────────────────────────────────────────────────────

def audit_constitution_violations() -> ConstitutionAudit:
    """shared/precommit_check.py 27종 검증 실행. 결과를 카테고리별 집계."""
    script = _ROOT / "shared" / "precommit_check.py"
    if not script.exists():
        return ConstitutionAudit(total_violations=0, by_category={}, sample=[])

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as e:
        log.warning("[AUDITOR] precommit_check 실행 실패: %s", e)
        return ConstitutionAudit(total_violations=0, by_category={}, sample=[])

    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return ConstitutionAudit(total_violations=0, by_category={}, sample=[],
                                 output_full=output)

    # 출력 파싱
    by_cat: dict[str, int] = {}
    sample: list[str] = []
    cur_cat: str | None = None
    for line in output.splitlines():
        m = re.match(r"\[(\w+)\]\s+(\d+)건", line.strip())
        if m:
            cur_cat = m.group(1)
            by_cat[cur_cat] = int(m.group(2))
            continue
        if cur_cat and line.strip().startswith("["):
            if len(sample) < 5:
                sample.append(line.strip())

    total = sum(by_cat.values())
    return ConstitutionAudit(
        total_violations=total,
        by_category=by_cat,
        sample=sample,
        output_full=output[:4000],
    )


# ──────────────────────────────────────────────────────────────
# 2) ERRORS.md 회고 — 동일 교훈 3회+ 반복 검출
# ──────────────────────────────────────────────────────────────

# ERRORS.md 항목 헤더 패턴 (자유 형식이라 휴리스틱)
_ERROR_HEADER_RE = re.compile(r"^##?\s*\[(\d+)\]\s+(.+?)$", re.MULTILINE)
_LESSON_LINE_RE = re.compile(r"^\s*[교훈|결론|얻은\s*교훈]+\s*[:：]\s*(.+?)$", re.MULTILINE)
_DATE_RE = re.compile(r"\b(20\d{2})[\-.](\d{1,2})[\-.](\d{1,2})\b")


def audit_repeated_lessons(window_days: int = 30) -> RepeatedLessons:
    """ERRORS.md 최근 N일 항목에서 *동일/유사 교훈* 3회+ 반복 검출.

    유사 판별: 교훈 라인의 *키워드 집합 jaccard 0.6 이상* 휴리스틱.
    """
    if not _ERRORS_MD.exists():
        return RepeatedLessons(window_days=window_days, repeated=[])

    try:
        text = _ERRORS_MD.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return RepeatedLessons(window_days=window_days, repeated=[])

    cutoff = datetime.now() - timedelta(days=window_days)
    lessons: list[tuple[str, list[str]]] = []  # (lesson_text, refs)

    # 항목 단위 분할
    items = re.split(r"^##?\s*\[\d+\]", text, flags=re.MULTILINE)
    headers = re.findall(r"^##?\s*\[(\d+)\][^\n]*", text, flags=re.MULTILINE)

    for idx, item in enumerate(items[1:], start=0):
        # 항목 안 날짜 → 최근 window 안만
        date_match = _DATE_RE.search(item)
        if date_match:
            try:
                dt = datetime(int(date_match.group(1)),
                              int(date_match.group(2)),
                              int(date_match.group(3)))
                if dt < cutoff:
                    continue
            except Exception:
                pass

        # 교훈 라인 추출
        lesson_match = _LESSON_LINE_RE.search(item)
        if not lesson_match:
            continue
        lesson = lesson_match.group(1).strip()
        ref = f"[{headers[idx]}]" if idx < len(headers) else "?"
        lessons.append((lesson, [ref]))

    # 유사 교훈 그룹화 (jaccard 0.6)
    grouped: list[dict[str, Any]] = []
    used = [False] * len(lessons)
    for i in range(len(lessons)):
        if used[i]:
            continue
        base_kw = _tokenize_keywords(lessons[i][0])
        refs = list(lessons[i][1])
        for j in range(i + 1, len(lessons)):
            if used[j]:
                continue
            other_kw = _tokenize_keywords(lessons[j][0])
            if _jaccard(base_kw, other_kw) >= 0.6:
                refs.extend(lessons[j][1])
                used[j] = True
        used[i] = True
        if len(refs) >= 3:
            grouped.append({
                "lesson": lessons[i][0][:120],
                "count": len(refs),
                "refs": refs[:10],
            })

    grouped.sort(key=lambda x: x["count"], reverse=True)
    return RepeatedLessons(window_days=window_days, repeated=grouped[:10])


def _tokenize_keywords(text: str) -> set[str]:
    """교훈 텍스트에서 2자 이상 한글·영문 키워드 추출."""
    return set(re.findall(r"[가-힣]{2,}|[A-Za-z_]{3,}", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ──────────────────────────────────────────────────────────────
# 3) learned_patterns 5회+ 반복 → 새 fixer 신설 제안
# ──────────────────────────────────────────────────────────────

def audit_learned_patterns_meta_learning(min_hits: int = 5) -> MetaLearningSuggestion:
    """learned_patterns.json 에서 hit_count >= min_hits 인 *LLM 패치* 패턴 추출.

    이런 패턴은 *정적 fixer 로 승급* 할 가치가 있음 (pattern_fixer.py 의 5종 외 신설).
    """
    if not _LEARNED_JSON.exists():
        return MetaLearningSuggestion(candidates=[])

    try:
        data = json.loads(_LEARNED_JSON.read_text(encoding="utf-8"))
    except Exception:
        return MetaLearningSuggestion(candidates=[])

    out: list[dict[str, Any]] = []
    for p in data.get("patterns", []):
        hit = int(p.get("hit_count", 0))
        fixer = (p.get("fixer_name") or p.get("fixer") or "").strip()
        # LLM patch 만 후보 (정적 fixer 는 이미 처리됨)
        if hit < min_hits or fixer != "llm_patch":
            continue
        out.append({
            "error_type": p.get("error_type", ""),
            "fingerprint": p.get("fingerprint", "")[:40],
            "hit_count": hit,
            "sample_message": (p.get("normalized_message") or p.get("message_pattern") or "")[:120],
        })

    out.sort(key=lambda x: x["hit_count"], reverse=True)
    return MetaLearningSuggestion(candidates=out[:10])


# ──────────────────────────────────────────────────────────────
# 4) 도메인 단위 학습 패턴 분포 (★ ADR 008 Phase 4)
# ──────────────────────────────────────────────────────────────

# 도메인 skew 임계값 — 한 도메인에 패턴이 이 이상 집중되면 *근본 리팩터* 신호
_DOMAIN_SKEW_THRESHOLD: int = 25


def audit_domain_distribution() -> DomainDistribution:
    """learned_patterns 의 도메인 분포 분석.

    ★ ADR 008 Phase 4 (사용자 박제 2026-05-17) — 도메인 카테고리 단위로
    학습 패턴이 *과도하게 한 도메인에 집중*되어 있는지 검출. 집중 = 그 도메인의
    *근본 결함* (예: image 도메인 40+ → 작성·발행 단계 자체 결함).
    """
    if not _LEARNED_JSON.exists():
        return DomainDistribution(
            by_domain={}, by_domain_hits={}, skewed_domains=[],
            unknown_count=0, top_fixers_by_domain={},
        )

    try:
        data = json.loads(_LEARNED_JSON.read_text(encoding="utf-8"))
    except Exception:
        return DomainDistribution(
            by_domain={}, by_domain_hits={}, skewed_domains=[],
            unknown_count=0, top_fixers_by_domain={},
        )

    pats = data.get("patterns", [])
    by_domain: dict[str, int] = {}
    by_domain_hits: dict[str, int] = {}
    fixers_by_domain: dict[str, dict[str, int]] = {}

    for p in pats:
        d = p.get("domain") or "unknown"
        by_domain[d] = by_domain.get(d, 0) + 1
        by_domain_hits[d] = by_domain_hits.get(d, 0) + int(p.get("hit_count", 0))
        fx = (p.get("fixer") or "?").strip()
        fxmap = fixers_by_domain.setdefault(d, {})
        fxmap[fx] = fxmap.get(fx, 0) + 1

    # 도메인별 top 3 fixer
    top_fixers_by_domain: dict[str, list[str]] = {}
    for d, fxmap in fixers_by_domain.items():
        sorted_fx = sorted(fxmap.items(), key=lambda x: -x[1])
        top_fixers_by_domain[d] = [f"{fx} ({n}건)" for fx, n in sorted_fx[:3]]

    # skew 검출 — 임계값 초과 도메인 (unknown 제외)
    skewed: list[dict[str, Any]] = []
    for d, n in by_domain.items():
        if d == "unknown":
            continue
        if n >= _DOMAIN_SKEW_THRESHOLD:
            skewed.append({
                "domain": d,
                "pattern_count": n,
                "hit_count": by_domain_hits.get(d, 0),
                "top_fixers": top_fixers_by_domain.get(d, []),
                "signal": (
                    f"{d} 도메인에 {n}개 패턴 누적 — 임계 {_DOMAIN_SKEW_THRESHOLD} 초과. "
                    f"근본 리팩터 검토 필요 (단순 학습 누적 한계)."
                ),
            })
    skewed.sort(key=lambda x: -x["pattern_count"])

    return DomainDistribution(
        by_domain=by_domain,
        by_domain_hits=by_domain_hits,
        skewed_domains=skewed,
        unknown_count=by_domain.get("unknown", 0),
        top_fixers_by_domain=top_fixers_by_domain,
    )


# ──────────────────────────────────────────────────────────────
# 종합 진입점
# ──────────────────────────────────────────────────────────────

def run(send_telegram: bool = False) -> AuditResult:
    """주간 종합 감사 수행."""
    audited_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    constitution = audit_constitution_violations()
    lessons = audit_repeated_lessons(window_days=30)
    meta = audit_learned_patterns_meta_learning(min_hits=5)
    # ★ ADR 008 Phase 4 — 도메인 분포 추가
    domain_dist = audit_domain_distribution()

    parts: list[str] = []
    parts.append(f"🛡️ *JARVIS Auditor* — {audited_at}")
    parts.append("")
    parts.append(f"📋 헌법 위반: *{constitution.total_violations}건*")
    if constitution.total_violations > 0:
        for cat, n in constitution.by_category.items():
            parts.append(f"  • `{cat}` — {n}건")
    parts.append("")
    parts.append(f"🔁 반복 교훈 (최근 30일, 3회+): *{len(lessons.repeated)}건*")
    for r in lessons.repeated[:3]:
        parts.append(f"  • {r['count']}회: {r['lesson'][:80]}")
    parts.append("")
    parts.append(f"🧠 메타 학습 후보 (5회+ LLM 패치): *{len(meta.candidates)}건*")
    for c in meta.candidates[:3]:
        parts.append(f"  • {c['hit_count']}회 `{c['error_type']}`")
    parts.append("")
    # ★ ADR 008 Phase 4 — 도메인 분포 보고
    parts.append(f"🌐 도메인 분포 (학습 패턴 {sum(domain_dist.by_domain.values())}건):")
    top_doms = sorted(domain_dist.by_domain.items(), key=lambda x: -x[1])[:5]
    for d, n in top_doms:
        h = domain_dist.by_domain_hits.get(d, 0)
        parts.append(f"  • `{d}` — {n}개 패턴 (hit {h})")
    if domain_dist.skewed_domains:
        parts.append("")
        parts.append(f"⚠️ *도메인 skew 검출 ({len(domain_dist.skewed_domains)}건)* — 근본 리팩터 검토:")
        for sk in domain_dist.skewed_domains[:3]:
            parts.append(f"  • `{sk['domain']}` — {sk['pattern_count']}개 ≥ 임계 {_DOMAIN_SKEW_THRESHOLD}")

    summary = "\n".join(parts)
    result = AuditResult(
        audited_at=audited_at,
        constitution=constitution,
        repeated_lessons=lessons,
        meta_learning=meta,
        domain_distribution=domain_dist,
        summary=summary,
    )

    if send_telegram:
        _send_telegram_report(result)

    # 회차 결과 DB 박제 (옵션)
    try:
        _save_to_db(result)
    except Exception as e:
        log.warning("[AUDITOR] DB 박제 실패: %s", e)

    return result


def _send_telegram_report(result: AuditResult) -> None:
    """텔레그램 보고 비활성 (사용자 박제) — 로그만 기록."""
    log.info("[AUDITOR] 감사 완료 — 위반 %d건 / 반복 %d건 / 메타 %d건",
             result.constitution.total_violations,
             len(result.repeated_lessons.repeated),
             len(result.meta_learning.candidates))


def _save_to_db(result: AuditResult) -> None:
    """audit_runs 테이블 (없으면 생성)."""
    try:
        # ★ 2026-07-03: get_conn 미존재 (shared.db 는 get_db) — audit 이력이 DB에 한 번도 저장 안 되고 있었음
        from shared.db import get_db  # type: ignore
    except Exception:
        return

    con = get_db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            audited_at TEXT NOT NULL,
            total_violations INTEGER NOT NULL,
            by_category_json TEXT,
            repeated_lessons_count INTEGER,
            meta_learning_candidates INTEGER,
            summary TEXT,
            full_json TEXT
        )
    """)
    cur.execute(
        "INSERT INTO audit_runs (audited_at, total_violations, by_category_json, "
        "repeated_lessons_count, meta_learning_candidates, summary, full_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            result.audited_at,
            result.constitution.total_violations,
            json.dumps(result.constitution.by_category, ensure_ascii=False),
            len(result.repeated_lessons.repeated),
            len(result.meta_learning.candidates),
            result.summary[:2000],
            json.dumps(result.to_dict(), ensure_ascii=False)[:8000],
        ),
    )
    con.commit()


# ──────────────────────────────────────────────────────────────
# 스케줄 잡 진입점 (JARVIS04 DEFAULT_JOBS 에서 호출)
# ──────────────────────────────────────────────────────────────

def job_auditor_weekly() -> None:
    """주 1회 일요일 04:30 자동 실행 — DEFAULT_JOBS callback."""
    try:
        run(send_telegram=True)
    except Exception as e:
        log.error("[AUDITOR] 주간 감사 실패: %s", e, exc_info=True)


__all__ = [
    "run", "job_auditor_weekly",
    "audit_constitution_violations",
    "audit_repeated_lessons",
    "audit_learned_patterns_meta_learning",
    "AuditResult", "ConstitutionAudit", "RepeatedLessons", "MetaLearningSuggestion",
]


if __name__ == "__main__":
    # ★ P1-④ Phase 2 보강 (사용자 박제 2026-05-18)
    try:
        from JARVIS00_INFRA.preflight import ensure_preflight as _ep
        _ep(strict=True)
    except Exception as _ee:
        print(f"⚠️ preflight 호출 실패: {_ee}")

    # 수동 실행 — 텔레그램 알림 없이 stdout 출력만
    r = run(send_telegram=False)
    print(r.summary)
