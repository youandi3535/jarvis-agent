"""티스토리 경제 브리핑 이미지만 생성 (발행 없음).

새로 적용된 chart_advisor LLM 판단이 잘 동작하는지 확인용.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from JARVIS02_WRITER.trend_economic_writer import (
    load_today_trends,
    select_tistory_topic,
    generate_tistory_article,
)

def main():
    print("=" * 55)
    print("  🖼️  티스토리 경제 브리핑 이미지 생성 테스트")
    print("  ⚠️  발행 없음 — 이미지 생성·저장만")
    print("=" * 55)

    # ① 트렌드 로드
    print("\n📡 트렌드 로드 중...")
    trends = load_today_trends()
    if not trends:
        print("❌ 트렌드 데이터 없음")
        sys.exit(1)

    # ② 티스토리 주제 선정
    topic = select_tistory_topic(trends)
    if not topic:
        print("❌ 티스토리 주제 없음")
        sys.exit(1)

    keyword = topic.get('keyword', '')
    sector  = topic.get('sector', '')
    print(f"\n✅ 선정 주제: [{sector}] {keyword}")

    # ③ 기사 생성 (이미지 포함) — 발행 없음
    print(f"\n🎨 이미지 생성 시작 (chart_advisor LLM 판단 적용)...")
    result = generate_tistory_article(topic)

    if not result:
        print("❌ 기사 생성 실패")
        sys.exit(1)

    # ④ 결과 요약 — blocks는 ('image'|'text', path|html) 튜플 리스트
    blocks    = result.get('blocks', [])
    img_blocks = [b for b in blocks if isinstance(b, (tuple, list)) and b[0] == 'image']
    thumb     = result.get('thumb_path', '')

    print(f"\n{'='*55}")
    print(f"  ✅ 생성 완료: '{result.get('title', '')[:40]}'")
    print(f"  블록 수: {len(blocks)}개  /  이미지 블록: {len(img_blocks)}개")
    if thumb:
        print(f"  썸네일: {Path(thumb).name}")
    print(f"\n  📁 생성된 이미지 파일:")
    for b in img_blocks:
        src = b[1] if len(b) > 1 else ''
        if src:
            p = Path(src)
            size = f"{p.stat().st_size // 1024}KB" if p.exists() else "?"
            print(f"    - {p.name}  ({size})")
    print("=" * 55)


if __name__ == "__main__":
    main()
