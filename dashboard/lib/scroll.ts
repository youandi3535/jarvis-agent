"use client";

import { useEffect, useRef } from "react";

/**
 * 시간축 가로 스크롤 영역의 **기본 위치를 최신(오른쪽 끝)** 으로 둔다.
 *
 * ★ 왜 필요한가 (2026-08-09 — 사용자 지적)
 *   막대가 많아 카드를 넘칠 때 가로 스크롤로 해결했는데, 브라우저 기본
 *   `scrollLeft = 0` 은 **가장 오래된 쪽**이다. 그래서 "오늘/최신" 을 보러 온 화면이
 *   열자마자 과거를 보여주고 최신은 가려졌다(실측: /errors 7일 추이 scrollLeft 0/304).
 *   기본은 최신이어야 하고, 과거는 스크롤해서 보는 것이 맞다.
 *
 * ★ 표에는 쓰지 말 것 — 행이 *시간순* 인 막대 영역에만 해당한다.
 *   레코드 목록 표를 오른쪽 끝으로 보내면 오히려 엉뚱한 열을 보여준다.
 *
 * 사용:
 *   const ref = useLatestVisible(daily);        // 데이터가 바뀌면 다시 맞춘다
 *   <div ref={ref} style={{ overflowX: "auto" }}>…</div>
 */
export function useLatestVisible<T>(dep: T) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // scrollWidth 를 그대로 넣으면 브라우저가 최대값으로 잘라 준다 (= 오른쪽 끝)
    el.scrollLeft = el.scrollWidth;
  }, [dep]);
  return ref;
}
