"""JARVIS02_WRITER/post_type_specs.py — 글 종류별 *추상 범위* 단일 진실 소스.

★ ERRORS [140] 사용자 박제 2026-05-17 v2 — 섹션 자체도 동적:
  *섹션 이름·구조 자체* 가 *주제 따라 매번 다름*. 박제 X.
  spec 은 *추상 한계만* 박제 (섹션 수 범위·섹션당 문장 범위·필수 섹션·절대 한계).
  섹션 list 자체는 `generate_section_plan(spec, topic, context)` 가 LLM 호출로 매번 동적 생성.

비전 (불변 원칙):
1. *분량은 결과* — 섹션 합산 자동 도출
2. *섹션 자체도 결과* — 주제 보고 LLM 매번 결정
3. spec 박제 = 추상 범위만 (min·max·required)
4. 상한·하한은 절대 박제 — 토큰 폭증 차단
5. 새 글 종류 = purpose 한 줄 + 범위 박제로 끝

사용:
    from JARVIS02_WRITER.post_type_specs import get_spec, generate_section_plan
    spec = get_spec("economic")
    section_plan = generate_section_plan(spec, topic="반도체 시장", market_data=...)
    # → [{"name": "반도체 산업 흐름", "sentences": 5, "images": 1}, ...]
    # 주제마다 매번 다른 섹션 — 동적
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis")

_HERE = Path(__file__).resolve().parent
_LEARNED_ADJ_PATH = _HERE / "learned_adjustments.json"


# ── 데이터 클래스 ──────────────────────────────────────

@dataclass
class SectionDef:
    """LLM 이 생성한 섹션 — 이름·문장수·이미지·표.

    *spec 안에 박제되지 않음*. generate_section_plan() 이 매번 동적 생성.
    """
    name: str
    sentences: int
    images: int = 0
    tables: int = 0


@dataclass
class PostTypeSpec:
    """글 종류 — *추상 범위*만 박제. 섹션 자체는 동적 생성.

    Required:
        type_id, purpose, audience: 글 종류 정의 (목적·청중)
        min_sections, max_sections: 섹션 수 범위 (LLM 이 이 안에서 결정)
        sentences_per_section: (최소, 최대) 섹션당 문장 범위
        max_sentences·min_sentences·max_korean·max_images: 절대 한계 (변경 금지)

    Optional:
        required_sections: 반드시 포함될 섹션 이름 (예: ["면책"])
        style_hints: LLM prompt 스타일 힌트
    """
    type_id: str
    purpose: str
    audience: str
    # ★ 섹션 *범위*만 박제 — 섹션 자체는 LLM 동적
    min_sections: int
    max_sections: int
    sentences_per_section: tuple[int, int]
    # 절대 한계 (변경 금지)
    max_sentences: int
    min_sentences: int
    max_korean: int
    max_images: int
    min_images: int = 8  # ★ 사용자 박제 2026-06-01 — 썸네일 제외 최소 이미지 수
    # 선택
    required_sections: list[str] = field(default_factory=list)
    style_hints: list[str] = field(default_factory=list)

    # ── 파생 속성 ──

    @property
    def target_sentences(self) -> int:
        """권장 분량 = (min + max) / 2. 학습 보정 적용."""
        adj = self._learned_overrides()
        if "target_sentences" in adj:
            v = int(adj["target_sentences"])
            return max(self.min_sentences, min(v, self.max_sentences))
        # 기본: 중간값 사용 (학습 보정 없을 때)
        return (self.min_sentences + self.max_sentences) // 2

    @property
    def target_korean(self) -> int:
        return self.target_sentences * 50

    @property
    def llm_max_tokens(self) -> int:
        """LLM max_tokens = max_korean × 2.5. 토큰 폭증 *물리적 불가능*."""
        return int(self.max_korean * 2.5)

    def _learned_overrides(self) -> dict:
        if not _LEARNED_ADJ_PATH.exists():
            return {}
        try:
            data = json.loads(_LEARNED_ADJ_PATH.read_text(encoding="utf-8"))
            return data.get(self.type_id, {})
        except Exception:
            return {}

    # ── prompt 자동 생성 ──

    def section_plan_prompt(self, topic: str, context: str = "") -> str:
        """LLM 에게 섹션 계획 요청 prompt."""
        style = "\n".join(f"  - {h}" for h in self.style_hints) or "  (없음)"
        required = ", ".join(self.required_sections) or "(없음)"
        return f"""당신은 블로그 글 *섹션 구조* 설계자입니다.

[주제]
{topic}

[글 종류 정의]
- 목적: {self.purpose}
- 청중: {self.audience}

[추가 맥락]
{context or "(없음)"}

[제약 조건 — 절대 준수]
- 섹션 수: 최소 {self.min_sections}개, 최대 {self.max_sections}개
- 섹션당 문장: 최소 {self.sentences_per_section[0]}, 최대 {self.sentences_per_section[1]}
- 총 문장 합: 최소 {self.min_sentences}, 최대 {self.max_sentences}
- 총 글자 합: 최대 {self.max_korean}자 (1문장 ≈ 50자)
- 반드시 포함 섹션: {required}
- 스타일:
{style}

[출력 형식 — JSON 만, 다른 텍스트 금지]
[
  {{"name": "섹션 이름 (주제 맞춤)", "sentences": 5, "images": 1, "tables": 0}},
  ...
]

★ 주제에 *정확히* 맞는 섹션 이름 (일반적 "도입부·본론" 아닌 *구체적 내용 반영*).
★ 모든 섹션 sentences 합 ≤ {self.max_sentences}.
★ 마지막에 반드시 "면책" 같은 required 섹션 포함."""


# ── 글 종류 카탈로그 — *추상 범위만* 박제 ──────────────────

POST_TYPE_SPECS: dict[str, PostTypeSpec] = {
    "economic": PostTypeSpec(
        type_id="economic",
        purpose="단일 주제 시장 분석 — 오늘의 핵심 경제 이슈를 데이터·지표로 해설",
        audience="경제 관심 일반 투자자 (30~50대)",
        # ★ 섹션 *범위*만 박제
        min_sections=4,
        max_sections=7,
        sentences_per_section=(3, 7),
        # 절대 한계 — target=(20+40)//2=30문장·약1500자 사용자 박제
        max_sentences=40,
        min_sentences=20,
        max_korean=2000,
        max_images=12,
        # 필수
        required_sections=["면책"],
        style_hints=[
            "격식체 (~습니다/~합니다)",
            "각 섹션에 수치·데이터 1개 이상",
            "전문 용어 사용 시 1문장 설명",
        ],
    ),
    "theme": PostTypeSpec(
        type_id="theme",
        purpose="다중 종목 테마 분석 — 한 테마 안에서 대장주·관련주·섹터 흐름",
        audience="개별 주식 관심 투자자",
        min_sections=5,
        max_sections=8,
        sentences_per_section=(3, 7),
        max_sentences=40,
        min_sentences=20,   # target=(20+40)//2=30문장·약1500자 사용자 박제
        max_korean=2000,
        max_images=10,
        required_sections=["면책"],
        style_hints=[
            "도입부 감성체 → 본론 분석체",
            "종목 언급 시 (코스피 종목코드) 병기",
            "차트 데이터 인용 가능",
        ],
    ),
    # ── 향후 새 글 종류 = 추상 범위만 박제 1 entry ──
    # "video_script": PostTypeSpec(
    #     type_id="video_script", purpose="...", audience="...",
    #     min_sections=3, max_sections=5, ...
    # ),
}


# ── 진입 API ───────────────────────────────────────

def get_spec(post_type: Optional[str] = None) -> PostTypeSpec:
    """글 종류별 spec 반환. 미지정·미정의 시 'economic' fallback."""
    key = (post_type or "economic").strip().lower()
    return POST_TYPE_SPECS.get(key, POST_TYPE_SPECS["economic"])


def list_post_types() -> list[str]:
    return sorted(POST_TYPE_SPECS.keys())


# ── ★ 동적 섹션 생성 (LLM 호출) ──────────────────────────

def generate_section_plan(
    spec: PostTypeSpec,
    topic: str,
    context: str = "",
    max_retries: int = 3,
) -> list[SectionDef]:
    """주제·맥락 보고 *섹션 list 동적 생성* (LLM 호출).

    매 호출마다 다른 섹션 가능 — *주제 맞춤*. spec 한계 검증 후 반환.
    검증 실패 시 max_retries 까지 재시도. 모두 실패 시 *fallback 구조* 반환.

    Returns:
        list[SectionDef] — 검증 통과한 섹션 list.
    """
    from shared.llm import invoke_text

    for attempt in range(1, max_retries + 1):
        prompt = spec.section_plan_prompt(topic, context)
        try:
            raw = invoke_text("analyzer", prompt, temperature=0.5) or ""
        except Exception as e:
            log.warning(f"[section_plan] LLM 호출 실패 ({attempt}/{max_retries}): {e}")
            continue

        # JSON 추출 — LLM이 마크다운 코드블록 또는 텍스트 섞을 수 있음
        json_match = re.search(r'\[\s*\{[\s\S]*?\}\s*\]', raw)
        if not json_match:
            log.warning(f"[section_plan] JSON 추출 실패 ({attempt}/{max_retries})")
            continue
        try:
            section_data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            log.warning(f"[section_plan] JSON 파싱 실패 ({attempt}): {e}")
            continue

        # 검증
        issues = _validate_section_plan(section_data, spec)
        if not issues:
            return [SectionDef(
                name=str(s.get("name", "섹션")),
                sentences=int(s.get("sentences", 4)),
                images=int(s.get("images", 0)),
                tables=int(s.get("tables", 0)),
            ) for s in section_data]
        log.warning(f"[section_plan] 검증 실패 ({attempt}/{max_retries}): {issues[:3]}")

    # 모든 시도 실패 — 최소 fallback 구조
    log.warning(f"[section_plan] {max_retries}회 모두 실패 → fallback 구조 사용")
    return _fallback_section_plan(spec)


def _validate_section_plan(plan: list, spec: PostTypeSpec) -> list[str]:
    """LLM 응답 섹션 list 가 spec 한계 안인지 검증.

    ★ required_sections (면책 등) 은 *문장 범위 예외* — 짧음이 정상 (1~3문장 허용).
    """
    issues: list[str] = []
    if not isinstance(plan, list):
        return ["응답이 list 아님"]
    n = len(plan)
    if not (spec.min_sections <= n <= spec.max_sections):
        issues.append(f"섹션 수 {n} (범위 {spec.min_sections}~{spec.max_sections})")
    total_sents = 0
    for i, s in enumerate(plan):
        if not isinstance(s, dict):
            issues.append(f"섹션 {i} 형식 오류")
            continue
        sent = s.get("sentences", 0)
        if not isinstance(sent, int) or sent < 1:
            issues.append(f"섹션 {i} sentences 비정상: {sent}")
            continue
        name = str(s.get("name", ""))
        # ★ required_sections (면책 등) 예외 — 1~3문장 허용 (짧음 정상)
        is_required = any(req in name for req in spec.required_sections)
        if is_required:
            if sent < 1 or sent > 3:
                issues.append(f"required 섹션 '{name}' sentences {sent} (예외 범위 1~3)")
        else:
            lo, hi = spec.sentences_per_section
            if not (lo <= sent <= hi):
                issues.append(f"섹션 '{name}' sentences {sent} (범위 {lo}~{hi})")
        total_sents += sent
    if total_sents > spec.max_sentences:
        issues.append(f"총 문장 합 {total_sents} > 상한 {spec.max_sentences}")
    if total_sents < spec.min_sentences:
        issues.append(f"총 문장 합 {total_sents} < 하한 {spec.min_sentences}")
    # required_sections 포함 검증
    names = {str(s.get("name", "")) for s in plan if isinstance(s, dict)}
    for req in spec.required_sections:
        if not any(req in n for n in names):
            issues.append(f"required 섹션 '{req}' 누락")
    return issues


def _fallback_section_plan(spec: PostTypeSpec) -> list[SectionDef]:
    """LLM 실패 시 최소 fallback 섹션 — *generic name* 사용 (주제 맞춤 아님)."""
    n = (spec.min_sections + spec.max_sections) // 2
    avg_sents = max(spec.sentences_per_section[0],
                    min(spec.target_sentences // n, spec.sentences_per_section[1]))
    plan: list[SectionDef] = []
    for i in range(n - len(spec.required_sections)):
        plan.append(SectionDef(name=f"섹션 {i+1}", sentences=avg_sents, images=1))
    for req in spec.required_sections:
        plan.append(SectionDef(name=req, sentences=2, images=0))
    return plan


# ── 학습 보정 ─────────────────────────────────────

def save_learned_adjustment(post_type: str, key: str, value) -> bool:
    """학습 조정값 저장 — 상한·하한 안에서만."""
    spec = get_spec(post_type)
    if key == "target_sentences":
        if not (spec.min_sentences <= int(value) <= spec.max_sentences):
            log.warning(f"[post_type_specs] 학습 조정 거부 — 범위 밖: {value}")
            return False
    data = {}
    if _LEARNED_ADJ_PATH.exists():
        try:
            data = json.loads(_LEARNED_ADJ_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    type_adj = data.get(post_type, {})
    type_adj[key] = value
    data[post_type] = type_adj
    try:
        _LEARNED_ADJ_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        return True
    except Exception:
        return False


def analyze_post_type_history(post_type: str, days: int = 30) -> dict:
    """post_analysis DB 분석 — 최근 N일 글 평균 분량 vs 조회수."""
    try:
        import sqlite3
        from pathlib import Path as _P
        db_path = _P(__file__).resolve().parent.parent / "shared" / "jarvis.sqlite"
        if not db_path.exists():
            return {}
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        rows = con.execute(f"""
            SELECT id, post_type, original_html, current_views
            FROM post_analysis
            WHERE post_type = ?
              AND created_at >= datetime('now', '-{int(days)} days')
            ORDER BY id DESC
        """, (post_type,)).fetchall()
        con.close()
        if len(rows) < 5:
            return {"sample_size": len(rows), "note": "데이터 부족 (최소 5건)"}
        sent_views = []
        for r in rows:
            html = r["original_html"] or ""
            text = re.sub(r"<[^>]+>", " ", html)
            sents = len([s for s in re.split(r"[.!?。]\s*|\n+", text) if len(s.strip()) > 3])
            sent_views.append((sents, r["current_views"] or 0))
        avg = sum(s for s, _ in sent_views) / len(sent_views)
        sorted_sv = sorted(sent_views, key=lambda x: x[1], reverse=True)
        top_n = max(1, len(sorted_sv) // 3)
        high_avg = sum(s for s, _ in sorted_sv[:top_n]) / top_n
        return {
            "post_type": post_type,
            "sample_size": len(rows),
            "avg_sentences": round(avg),
            "high_view_avg": round(high_avg),
            "suggested_target": round(high_avg),
        }
    except Exception as e:
        log.warning(f"[post_type_specs] 분석 실패: {e}")
        return {}


__all__ = [
    "SectionDef",
    "PostTypeSpec",
    "POST_TYPE_SPECS",
    "get_spec",
    "list_post_types",
    "generate_section_plan",
    "save_learned_adjustment",
    "analyze_post_type_history",
]
