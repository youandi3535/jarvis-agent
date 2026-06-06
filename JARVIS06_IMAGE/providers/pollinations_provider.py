"""JARVIS06_IMAGE/providers/pollinations_provider.py — Pollinations.ai 무료 REST API."""
from __future__ import annotations
import logging, urllib.parse, random, time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("jarvis")

_BASE = "https://image.pollinations.ai/prompt/{prompt}"
_TIMEOUT = 60  # 2026-05-29: 이미지 생성 느린 경우 대비 60초로 증가


class PollinationsProvider:
    PROVIDER_ID = "pollinations"
    requires_approval = False  # 무료·인증 없음 — 승인 불필요

    def generate(self, prompt_en: str, out_dir: Path,
                 width: int = 1024, height: int = 1024,
                 seed: Optional[int] = None) -> Path:
        """Pollinations.ai 로 이미지 생성 후 로컬 파일 경로 반환.

        Raises:
            RuntimeError: Content-Type 이 image/* 가 아닌 경우.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        # ★ 사용자 박제 2026-05-15 — seed 없을 시 자동 랜덤 부여 + nofeed 추가.
        # Pollinations 가 같은 prompt 받으면 같은 캐시 반환하는 사고 차단.
        if seed is None:
            seed = random.randint(1, 999_999_999)
        params = f"?width={width}&height={height}&nologo=true&nofeed=true&seed={seed}"
        encoded = urllib.parse.quote(prompt_en, safe="")
        url = _BASE.format(prompt=encoded) + params
        log.info(f"[Pollinations] GET {url[:120]}")
        r = requests.get(url, timeout=_TIMEOUT)
        ct = r.headers.get("Content-Type", "")
        if "image" not in ct:
            raise RuntimeError(f"Pollinations 비정상 응답 (ct={ct}): {r.text[:200]}")
        # ★ 파일명에 timestamp+seed 포함 — 같은 prompt 라도 덮어쓰기 차단
        _ts = int(time.time() * 1000) & 0xFFFFFF
        fname = f"poll_{abs(hash(prompt_en)) & 0xFFFFFF:06x}_{seed:08d}_{_ts:06x}.png"
        out_path = out_dir / fname
        out_path.write_bytes(r.content)
        log.info(f"[Pollinations] 저장: {out_path}")
        return out_path


__all__ = ["PollinationsProvider"]
