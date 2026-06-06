"""JARVIS06_IMAGE/providers/bing_provider.py — Bing Image Creator 프로바이더.

Bing Image Creator (DALL-E 기반) 무료 이미지 생성.
_U 쿠키 필요: Microsoft 계정 로그인 후 bing.com 쿠키 추출.

설정:
  BING_COOKIE=_U 쿠키값 (.env 에 등록)
"""
from __future__ import annotations
import hashlib, logging, os, re, time
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis")

_BING_CREATE_URL = "https://www.bing.com/images/create"
_BING_POLL_URL   = "https://www.bing.com/images/create/async/results"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bing.com/images/create",
}


class BingProvider:
    """Bing Image Creator 프로바이더."""
    PROVIDER_ID = "bing"

    def __init__(self) -> None:
        self._cookie: Optional[str] = os.getenv("BING_COOKIE", "").strip() or None
        if self._cookie:
            log.info("[BingProvider] 초기화 완료 (BING_COOKIE 있음)")
        else:
            log.info("[BingProvider] BING_COOKIE 없음 — 비활성화")

    @property
    def available(self) -> bool:
        return bool(self._cookie)

    def generate(self, prompt_en: str, out_dir: Path,
                 width: int = 1024, height: int = 1024) -> Path:
        """Bing Image Creator 로 이미지 생성 → 로컬 파일 경로 반환.

        Raises:
            RuntimeError: 쿠키 없음 / API 오류 / 타임아웃.
        """
        if not self.available:
            raise RuntimeError("BingProvider 비활성화 (BING_COOKIE 없음)")

        try:
            import requests
        except ImportError:
            raise RuntimeError("requests 미설치 — pip install requests")

        out_dir.mkdir(parents=True, exist_ok=True)

        # 세션 수립 — _U 쿠키를 처음부터 포함해 GET → 인증된 세션 쿠키 확보
        session = requests.Session()
        session.headers.update(_HEADERS)
        session.cookies.set("_U", self._cookie, domain=".bing.com")
        session.get(_BING_CREATE_URL, timeout=15)        # 인증 상태로 세션 쿠키 확보

        # 1) 이미지 생성 요청
        log.info(f"[BingProvider] 생성 요청: '{prompt_en[:50]}'")
        resp = session.post(
            _BING_CREATE_URL,
            params={"q": prompt_en, "rt": "4", "FORM": "GENCRE"},
            allow_redirects=False,
            timeout=20,
        )

        # 인증 실패 감지 — 302 redirect 없이 200 이면 로그인 안됨
        redirect_url = resp.headers.get("Location", "")
        if not redirect_url and resp.status_code == 200:
            raise RuntimeError(f"Bing 인증 실패 (HTTP {resp.status_code}, Location 없음) — _U 쿠키 만료")

        if not redirect_url:
            m = re.search(r'id=([^&"]+)', resp.text)
            if not m:
                raise RuntimeError(f"Bing: redirect 없음, status={resp.status_code}")
            request_id = m.group(1)
        else:
            m = re.search(r'id=([^&"]+)', redirect_url)
            if not m:
                raise RuntimeError(f"Bing: redirect URL 에서 ID 추출 실패: {redirect_url}")
            request_id = m.group(1)

        log.debug(f"[BingProvider] request_id={request_id}")

        # 2) polling — session 쿠키 그대로 사용
        img_url = self._poll(request_id, session, timeout=30)

        # 3) 이미지 다운로드
        dl = session.get(img_url, timeout=30)
        dl.raise_for_status()
        img_bytes = dl.content

        # 플레이스홀더 감지 — Bing 쿠키 만료/차단 시 ~40KB 기본 이미지 반환
        # 실제 AI 생성 이미지는 최소 100KB 이상
        if len(img_bytes) < 90_000:
            log.warning(f"[BingProvider] 응답 {len(img_bytes):,}B — 플레이스홀더 의심, 폴백 처리")
            raise RuntimeError(f"Bing 플레이스홀더 감지 ({len(img_bytes):,}B < 90KB) — 쿠키 만료 가능성")

        h = hashlib.md5(prompt_en.encode()).hexdigest()[:8]
        fname = f"bing_{h}.jpg"
        out_path = out_dir / fname
        out_path.write_bytes(img_bytes)
        log.info(f"[BingProvider] 생성 완료: {out_path}")
        return out_path

    def _poll(self, request_id: str, session, timeout: int = 60) -> str:
        """polling 루프 — 완료될 때까지 3초마다 확인 → 이미지 URL 반환."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = session.get(
                _BING_POLL_URL,
                params={"requestId": request_id},
                timeout=15,
            )
            if r.status_code == 200 and r.text.strip():
                # 응답 본문에서 이미지 URL 추출
                urls = re.findall(r'https://[^"\'<>\s]+\.(?:jpg|jpeg|png|webp)[^"\'<>\s]*', r.text)
                # bing 썸네일 필터링 (th? 로 시작하는 것 제외, 원본만)
                clean_urls = [u for u in urls if "th?" not in u and "bing.com/th/" not in u]
                if clean_urls:
                    return clean_urls[0]
                # 원본 없으면 아무거나
                if urls:
                    return urls[0]
            time.sleep(3)

        raise RuntimeError(f"Bing: {timeout}초 내 이미지 생성 완료 안됨 (id={request_id})")


__all__ = ["BingProvider"]
