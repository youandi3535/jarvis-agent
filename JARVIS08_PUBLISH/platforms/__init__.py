"""JARVIS08_PUBLISH/platforms — 플랫폼별 발행자 단일 진입점.

ADR 008 Phase 2 (사용자 박제 2026-05-17). Phase 2-4/2-5 완료.
"""
from JARVIS08_PUBLISH.platforms.naver_poster import post_to_naver  # noqa: F401
from JARVIS08_PUBLISH.platforms.tistory_poster import post_to_tistory  # noqa: F401


def last_post_url(platform: str) -> str:
    """방금 발행한 글의 URL — **발행 도메인 단일 진입점** (★ ERRORS [482]).

    ★ 왜 필요한가: 발행자는 발행 직후 RSS 로 URL 을 확보해 `_last_post_url` 에 담아두는데,
      발행 이벤트(`bus.on_post_published_detail`)를 부르는 4곳이 **아무도 그 값을 안 넘겼다**.
      그래서 `post_analysis.url` 이 2026-05-18 이후 전부 비었고(221건 중 122건),
      URL 이 없으니 조회수 수집기가 아무것도 못 긁었다(발행 221건 중 조회수>0 은 4건).
      그 결과 학습 원천(`learn_log.actual_views`)이 366행 중 365행이 0 →
      가중치 학습기는 "배울 게 없다" 며 영구 미학습(`learned_weights` 0행),
      백테스트는 정답값이 상수라 r2=1.0(가짜 100%). **한 줄 누락이 학습 3단을 죽였다.**

    ★ 호출자가 각자 `naver_poster._last_post_url` 을 직접 읽지 말 것 —
      플랫폼이 늘면 호출부마다 분기가 생긴다. 여기서 한 번에 해석한다.

    Returns: URL 문자열 (없으면 "")
    """
    _p = (platform or "").strip().lower()
    try:
        if _p == "naver":
            from JARVIS08_PUBLISH.platforms import naver_poster as _m
        elif _p == "tistory":
            from JARVIS08_PUBLISH.platforms import tistory_poster as _m
        else:
            return ""
        return str(getattr(_m, "_last_post_url", "") or "")
    except Exception:
        return ""


__all__ = [
    "post_to_naver",
    "post_to_tistory",
    "last_post_url",
]
