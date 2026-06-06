"""JARVIS08_PUBLISH/credentials — 쿠키·로그인 자격증명 단일 진입점.

ADR 008 Phase 2 (사용자 박제 2026-05-17).

이관:
  - naver_cookie_refresher  (네이버 쿠키 갱신)
  - tistory_cookie_refresher (티스토리 쿠키 갱신)

옛 위치 (`JARVIS02_WRITER/naver_cookie_refresher.py` / `tistory_cookie_refresher.py`)
는 backward-compat shim 만 남김 — 신규 호출자는 이 패키지 사용.
"""
