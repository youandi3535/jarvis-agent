"""JARVIS06_IMAGE/providers/pollinations_provider.py — Pollinations.ai 무료 REST API."""
from __future__ import annotations
import logging, urllib.parse, random, time
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("jarvis")

_BASE = "https://image.pollinations.ai/prompt/{prompt}"
_TIMEOUT = 60  # 2026-05-29: 이미지 생성 느린 경우 대비 60초로 증가
_MAX_RETRIES = 6  # ★ ERRORS [267] 박제 — Queue full 재시도 (4→6 확대)
_BASE_DELAY = 10  # 첫 재시도 대기 10초 (지수 백오프)
_QUEUE_FULL_DELAY = 30  # ★ 402 Queue full 전용 — 큐 해소까지 더 긴 대기 (20→30 ERRORS [267] 재발)


class PollinationsProvider:
    PROVIDER_ID = "pollinations"
    requires_approval = False  # 무료·인증 없음 — 승인 불필요

    # ★ 사용자 박제 2026-06-07 — 모델 명시. flux = 무료·고품질 기본값.
    # 옛 동작: model 파라미터 없음 → Pollinations 가 임의 기본 모델 선택 (품질 변동).
    DEFAULT_MODEL = "flux"

    def generate(self, prompt_en: str, out_dir: Path,
                 width: int = 1024, height: int = 1024,
                 seed: Optional[int] = None,
                 model: Optional[str] = None) -> Path:
        """Pollinations.ai 로 이미지 생성 후 로컬 파일 경로 반환.

        ★ Queue full / 429 / 5xx 시 지수 백오프 재시도 (최대 4회).
        CLAUDE.md 규칙 #11 "재시도 로직 필수 탑재" 준수.

        Raises:
            RuntimeError: 모든 재시도 실패 시.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        # ★ 사용자 박제 2026-05-15 — seed 없을 시 자동 랜덤 부여 + nofeed 추가.
        # Pollinations 가 같은 prompt 받으면 같은 캐시 반환하는 사고 차단.
        if seed is None:
            seed = random.randint(1, 999_999_999)
        _model = model or self.DEFAULT_MODEL
        params = (
            f"?width={width}&height={height}&nologo=true&nofeed=true"
            f"&seed={seed}&model={_model}&enhance=true"
        )
        encoded = urllib.parse.quote(prompt_en, safe="")
        url = _BASE.format(prompt=encoded) + params

        last_err: Exception | None = None
        _saw_queue_full = False
        for attempt in range(_MAX_RETRIES):
            if attempt > 0:
                # ★ 402 Queue full 시 전용 대기 (큐 해소에 더 긴 시간 필요)
                if _saw_queue_full:
                    delay = _QUEUE_FULL_DELAY * (2 ** (attempt - 1))
                else:
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                delay = min(delay, 120)  # 최대 2분 cap
                log.warning(f"[Pollinations] 재시도 {attempt}/{_MAX_RETRIES - 1} — {delay}초 대기 (queue_full={_saw_queue_full})")
                time.sleep(delay)
            try:
                log.info(f"[Pollinations] GET (attempt={attempt}) {url[:120]}")
                r = requests.get(url, timeout=_TIMEOUT)
                ct = r.headers.get("Content-Type", "")
                if "image" in ct:
                    # 성공 — 파일 저장
                    _ts = int(time.time() * 1000) & 0xFFFFFF
                    fname = f"poll_{abs(hash(prompt_en)) & 0xFFFFFF:06x}_{seed:08d}_{_ts:06x}.png"
                    out_path = out_dir / fname
                    out_path.write_bytes(r.content)
                    log.info(f"[Pollinations] 저장: {out_path}")
                    return out_path
                # Queue full / 비정상 응답 → 재시도 대상
                body_preview = r.text[:200]
                is_queue_full = "Queue full" in body_preview or "queue" in body_preview.lower() or r.status_code == 402
                is_server_err = r.status_code >= 500
                is_rate_limit = r.status_code == 429
                if is_queue_full:
                    _saw_queue_full = True
                if is_queue_full or is_server_err or is_rate_limit:
                    last_err = RuntimeError(
                        f"Pollinations 일시 오류 (attempt={attempt}, status={r.status_code}, ct={ct}): {body_preview}"
                    )
                    log.warning(f"[Pollinations] {last_err}")
                    continue
                # 비일시적 오류 — 즉시 raise
                raise RuntimeError(f"Pollinations 비정상 응답 (ct={ct}): {body_preview}")
            except requests.RequestException as exc:
                last_err = exc
                log.warning(f"[Pollinations] 네트워크 오류 (attempt={attempt}): {exc}")
                continue

        raise RuntimeError(
            f"Pollinations {_MAX_RETRIES}회 재시도 모두 실패: {last_err}"
        )


__all__ = ["PollinationsProvider"]
