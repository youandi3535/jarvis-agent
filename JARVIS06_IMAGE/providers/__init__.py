# ★ 사용자 박제 2026-06-07 — Bing / HuggingFace 완전 삭제 (ERRORS [263])
# Bing 쿠키 무한 만료 + HuggingFace DNS 차단·hf-inference 미지원 → 전멸 → 폐기.
from .cloudflare_provider import CloudflareProvider

# ★ ClaudeSVGProvider 재export 제거 (사용자 박제 2026-08-10): 유일한 소비자였던
#   `image_agent.generate_chart` 가 삭제되면서 **호출자 0곳의 고아** 가 됐다. 패키지
#   __init__ 의 재export 만 남으면 '살아있는 프로바이더' 로 오인돼 다시 배선된다.
#   ※ `claude_svg_provider.py` **파일 자체는 남아 있다** — 삭제분이 다른 세션의 커밋에
#     잘못 섞였다가 `f1db938` 로 복원됐다. 병행 편집 중인 남의 트리를 다시 지우지 않는다
#     (커밋 규정 2026-08-10: 내가 수정한 것만). 재export 가 없으므로 우연히 배선될 길은
#     닫혀 있다 — 파일 삭제는 소유가 정리된 뒤 별건으로 처리할 것.
__all__ = ['CloudflareProvider']
