"""JARVIS02_WRITER/post_type_specs_job.py — 분량 학습 보정 월간 잡 (★ ERRORS [139]).

매월 1일 04:00 (`monthly_spec_learn` 잡) 실행:
  1. post_analysis DB 최근 30일 분석 (각 post_type)
  2. 조회수 좋은 글의 평균 분량 → 최적 target 제안
  3. 텔레그램으로 사용자에게 *"economic 현재 25문장 → 데이터 분석 결과 28문장 권장. 적용?"*
  4. 사용자 ✅ 후 `save_learned_adjustment()` 호출 — learned_adjustments.json 저장

상한·하한은 *절대 박제* — 조정 안 됨.
"""
from __future__ import annotations

import logging

from JARVIS02_WRITER.post_type_specs import (
    analyze_post_type_history,
    get_spec,
    list_post_types,
)

log = logging.getLogger("jarvis")


def run_monthly_analysis():
    """월간 분량 학습 보정 — 각 post_type 분석 + 사용자에게 텔레그램 제안."""
    try:
        from shared.notify import send_tg
    except Exception:
        send_tg = lambda m: None  # noqa: E731

    msgs: list[str] = ["📊 *분량 학습 보정 월간 분석 (ERRORS [139])*\n"]
    suggestions: list[tuple[str, int, int]] = []  # (post_type, current, suggested)

    for post_type in list_post_types():
        spec = get_spec(post_type)
        analysis = analyze_post_type_history(post_type, days=30)
        if not analysis or analysis.get("sample_size", 0) < 5:
            msgs.append(f"  • `{post_type}`: 데이터 부족 (샘플 {analysis.get('sample_size', 0)})")
            continue
        current = spec.target_sentences
        suggested = int(analysis.get("suggested_target", current))
        # 상한·하한 안에서만 제안
        suggested = max(spec.min_sentences, min(suggested, spec.max_sentences))
        delta = suggested - current
        if abs(delta) >= 2:  # 2문장 이상 차이 시만 제안
            msgs.append(
                f"  • `{post_type}`: 현재 {current}문장 → 권장 {suggested}문장 "
                f"({'+' if delta > 0 else ''}{delta}, 샘플 {analysis['sample_size']})"
            )
            suggestions.append((post_type, current, suggested))
        else:
            msgs.append(f"  • `{post_type}`: 현재 {current}문장 (조정 불필요, 샘플 {analysis['sample_size']})")

    if suggestions:
        msgs.append("\n*적용하려면 호스트에서*:")
        for pt, curr, sug in suggestions:
            msgs.append(
                f"  `python -c \"from JARVIS02_WRITER.post_type_specs import save_learned_adjustment; "
                f"save_learned_adjustment('{pt}', '도입부', {sug // 7})\"`"
            )
        msgs.append("\n(섹션별 조정은 코드 수정 또는 별도 메뉴 필요)")
    else:
        msgs.append("\n✅ 모든 post_type 분량이 최적 범위. 조정 불필요.")

    final_msg = "\n".join(msgs)
    log.info(f"[post_type_specs_job] 월간 분석 완료\n{final_msg}")
    try:
        send_tg(final_msg)
    except Exception as e:
        log.warning(f"[post_type_specs_job] 텔레그램 알림 실패: {e}")
    return {"suggestions_count": len(suggestions), "analyzed_types": list_post_types()}


__all__ = ["run_monthly_analysis"]
