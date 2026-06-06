"""JARVIS08_PUBLISH — 발행 도메인 단일 진입점.

ADR 008 Phase 2 (사용자 박제 2026-05-17) 신설.

도메인 책임: 네이버·티스토리 발행 추상화 + 카테고리 검색 + 쿠키 갱신.

서브 패키지:
  - platforms/   네이버·티스토리 발행자 본체
  - category/    카테고리 상수·검색 로직
  - credentials/ 네이버·티스토리 쿠키 refresher

호출자 가이드 (★ 단일 진입점 규칙):
  from JARVIS08_PUBLISH.platforms import post_to_naver, post_to_tistory
  from JARVIS08_PUBLISH.category import (
      ECONOMIC_CATEGORY,
      resolve_naver_category, resolve_tistory_category,
  )
  from JARVIS08_PUBLISH.credentials import ensure_naver_cookies, ensure_tistory_cookies

이 패키지 외부 위치에 발행 함수·카테고리 상수·쿠키 갱신 코드 *본체* 정의 금지
(precommit_check `domain/publish`·`domain/category` 강제).
"""
