"""
JARVIS02 pre_revise._is_meta_after 회귀 테스트.

배경: 2026-04-30 07:17 발행 3건에서 Claude 가 'after' 필드에 메타 작성
지시문을 포함시켜 본문이 그대로 누출되는 사고 발생 (ERRORS.md [13]).
프롬프트 강화 + sanitizer 정규식 패턴으로 이중 방어 적용. 본 테스트는 그
sanitizer 의 회귀 방지용 — 누출 케이스가 차단되고 정상 케이스가 통과하는지
영구 검증한다.

ERRORS.md [13] 교훈 마지막 줄:
  "사용자가 발행물에서 직접 발견한 버그는 → 재발 시 즉시 차단되도록
   회귀 테스트 케이스로 박제. 이 4건은 영구 테스트셋."

실행:
    python tests/test_pre_revise_sanitizer.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "JARVIS02_WRITER"))

from pre_revise import _is_meta_after  # noqa: E402

# ─────────────────────────────────────────────────────────────
# 차단되어야 하는 케이스 (BAD)
# 4건은 ERRORS.md [13] 의 실제 누출 텍스트 또는 그 변형 — 영구 박제.
# 나머지는 _META_PATTERNS 의 각 패턴이 회귀하지 않도록 1건씩 커버.
# ─────────────────────────────────────────────────────────────
BAD_CASES = [
    # 실제 누출 #1 — 인용 + 등 + 구체적 실행 단계 제시
    '"관심 종목의 목표가 재설정 후 지정가 매수 주문을 설정하는 것을 추천합니다" 등 더 구체적인 실행 단계 제시',
    # 실제 누출 #2 — 마무리 후 추가
    "마무리 후 추가: 시장 변동성에 대한 대비책",
    # 실제 누출 #3 — 괄호 안 작성 지시문
    "(주어-술어를 더 간결하게)",
    # 실제 누출 #4 — 또는 ~ 제시
    "또는 출처 링크 제시",
    # 패턴 회귀 방지 — 예: 시작
    "예: 시가총액 상위 10개 종목 비교",
    # 패턴 회귀 방지 — 다음과 같이
    "다음과 같이 분기별 실적을 정리한다",
    # 패턴 회귀 방지 — 따옴표 + 등
    '"FOMC 결과 발표 직후 변동성이 커진다" 등 구체적 인용 추가',
    # 패턴 회귀 방지 — 괄호 안 따옴표 인용 예시
    "(또는 '실적 발표 일정과 시장 컨센서스')",
]

# ─────────────────────────────────────────────────────────────
# 통과해야 하는 케이스 (GOOD)
# 정상 본문 / 정상 괄호 / 정상 인용 — 오차단(false positive) 방지.
# ─────────────────────────────────────────────────────────────
GOOD_CASES = [
    "FOMC 동결과 유가 급등은 단기 변동성을 키운다.",
    "오늘 시장 핵심 정리 (2026년 4월 30일 기준)",
    "투자자는 분산 포트폴리오를 점검할 시점이다.",
    "1분기 실적 발표 후 반도체 섹터의 추가 상승 여지가 있다.",
    "한국은행은 \"물가 안정이 최우선\"이라고 밝혔다.",
]


def main() -> int:
    fails = []

    print("=== BAD 케이스 (차단 기대) ===")
    for txt in BAD_CASES:
        is_meta, hit = _is_meta_after(txt)
        if not is_meta:
            fails.append(("BAD 차단 실패", txt, ""))
            print(f"❌ 차단 실패  →  {txt[:80]}")
        else:
            print(f"✅ 차단 OK [hit={hit!r}]  →  {txt[:80]}")

    print("\n=== GOOD 케이스 (통과 기대) ===")
    for txt in GOOD_CASES:
        is_meta, hit = _is_meta_after(txt)
        if is_meta:
            fails.append(("GOOD 오차단", txt, hit))
            print(f"❌ 오차단 (hit={hit!r})  →  {txt[:80]}")
        else:
            print(f"✅ 통과 OK  →  {txt[:80]}")

    total = len(BAD_CASES) + len(GOOD_CASES)
    if fails:
        print(f"\n❌ {len(fails)}/{total} 실패:")
        for label, txt, extra in fails:
            print(f"  - {label}: {txt}  (extra={extra!r})")
        return 1
    print(f"\n✅ 전체 {total}건 통과 (BAD {len(BAD_CASES)} 차단 / GOOD {len(GOOD_CASES)} 통과)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
