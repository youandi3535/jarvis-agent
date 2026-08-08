"""Cloudflare Workers AI 프로바이더 — **무료 티어 AI 생성** (2026-08-05 신설).

★ 왜 신설했나
  2026-08-05 07:36 부터 Pollinations 가 402 `Insufficient balance` 로 전멸했다.
  라이브 확인 결과 Pollinations 이미지 모델 **39개 전부 유료**, 키 없는 익명 티어는 401.
  Gemini(나노바나나)도 공식 가격표가 이미지 모델 전부 `Free Tier: Not available`.
  그날 썸네일이 전부 파란 그라디언트로 떨어졌다.

★ 왜 이걸 골랐나 (공식 단가로 계산)
  `Flux-1-Schnell` = 4.80 neuron/512² 타일 + 9.60 neuron/step.
  1024×1024 · 4 step = 4타일×4.80 + 4×9.60 = **57.6 neuron/장**.
  무료 한도 **10,000 neuron/일** → 하루 **약 173장**. 우리가 쓰는 건 하루 4~10장이다.
  · 실측: 3.0초 · 784KB · 주제 정확(토카막 요청 → 토카막 사진)
  · 레오나르도 계열은 100배 비싸다(장당 3,000+) — 하루 2~3장이라 쓰면 안 된다.

★ 한계
  · 무료 계정 + API 토큰이 필요하다(결제 아님).
  · 크기 지정이 없다 — 모델이 1024×1024 로 낸다. 필요한 비율은 로컬에서 자른다.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import time
from pathlib import Path

import requests

log = logging.getLogger("jarvis")

__all__ = ["CloudflareProvider", "provider_available", "provider_effective"]

_API = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"


def _ensure_env() -> None:
    """`.env` 를 스스로 적재한다 — 호출자의 import 순서에 기대지 않는다.

    ★ 같은 실수를 오늘 `shared/secrets.py` 에서 이미 했다(ERRORS [564]).
      환경변수를 남이 넣어줬겠거니 하면, 안 넣어준 경로에서 **조용히 "없음" 이 되고**
      폴백으로 새어나간다 — 실패가 아니라 *기능이 없는 것처럼* 보인다.
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
    except Exception:
        pass


def provider_available() -> bool:
    """자격증명이 있는가 — 없으면 호출자가 조용히 다음 폴백으로 넘어간다."""
    _ensure_env()
    return bool(os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN"))


class CloudflareProvider:
    """Workers AI Flux-1-Schnell — 무료 티어 내 AI 이미지 생성."""

    # ★ 모델을 여기 한 곳에만 둔다. 무배포 교체: `CLOUDFLARE_IMAGE_MODEL`.
    #   (다른 모델은 neuron 단가가 100배까지 차이 난다 — 바꾸려면 단가부터 확인할 것)
    DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"
    STEPS = 4              # Schnell 은 4 step 이 설계값. 늘려도 품질 이득이 거의 없다.
    TIMEOUT = 120
    RETRIES = 3
    MIN_BYTES = 10_000     # 이보다 작으면 정상 사진이 아니다

    def __init__(self):
        _ensure_env()
        self.acct = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
        self.token = os.getenv("CLOUDFLARE_API_TOKEN", "")
        self.model = os.getenv("CLOUDFLARE_IMAGE_MODEL", "") or self.DEFAULT_MODEL

    def generate(self, prompt_en: str, out_dir: Path,
                 width: int = 1024, height: int = 1024,
                 seed: "int | None" = None,
                 model: "str | None" = None) -> Path:
        """이미지 생성 후 로컬 경로 반환.

        시그니처는 종전 프로바이더와 **동일하게 유지** — 호출자가 프로바이더를
        갈아끼울 때 분기를 만들지 않기 위해서다(①).

        Raises:
            RuntimeError: 자격증명 부재 또는 모든 재시도 실패 (호출자가 다음 폴백으로).
        """
        if not provider_available():
            raise RuntimeError("Cloudflare 자격증명 없음 (CLOUDFLARE_ACCOUNT_ID/API_TOKEN)")

        _model = model or self.model
        url = _API.format(acct=self.acct, model=_model)
        # 유일성 (CLAUDE.md JARVIS06 규칙 #10 — 10회 반복 박제)
        h = hashlib.md5(f"{prompt_en}|{seed}".encode("utf-8")).hexdigest()[:8]
        dest = Path(out_dir)
        dest.mkdir(parents=True, exist_ok=True)
        out_path = dest / f"cf_{h}.jpg"

        payload = {"prompt": prompt_en, "steps": self.STEPS}
        if seed is not None:
            payload["seed"] = int(seed)

        last = ""
        for attempt in range(self.RETRIES):
            try:
                r = requests.post(url, headers={"Authorization": f"Bearer {self.token}"},
                                  json=payload, timeout=self.TIMEOUT)
                ct = (r.headers.get("content-type") or "").lower()
                raw = b""
                if ct.startswith("image/"):
                    raw = r.content                       # 일부 모델은 바이너리 직반환
                elif r.status_code == 200:
                    j = r.json()
                    if not j.get("success"):
                        last = f"API 실패: {(j.get('errors') or [{}])[0]}"
                        raw = b""
                    else:
                        b64 = (j.get("result") or {}).get("image", "")
                        raw = base64.b64decode(b64) if b64 else b""
                else:
                    last = f"status={r.status_code} {r.text[:160]}"

                if len(raw) >= self.MIN_BYTES:
                    out_path.write_bytes(raw)
                    self._fit(out_path, width, height)
                    log.info(f"[Cloudflare] 생성 완료 {out_path.name} "
                             f"({out_path.stat().st_size // 1024}KB, {_model.split('/')[-1]})")
                    return out_path
                if not last:
                    last = f"응답이 너무 작음({len(raw)}B) — 이미지가 아님"
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
            log.warning(f"[Cloudflare] 재시도 {attempt + 1}/{self.RETRIES} — {last}")
            time.sleep(2 * (attempt + 1))

        raise RuntimeError(f"Cloudflare {self.RETRIES}회 재시도 모두 실패: {last}")

    @staticmethod
    def _fit(path: Path, width: int, height: int) -> None:
        """요청 비율로 맞춘다 — 모델은 1024×1024 만 낸다.

        비율이 다르면 **중앙 크롭 후 리사이즈** 한다. 늘리지 않는 이유: 썸네일에서
        인물·구조물이 찌그러지면 바로 티가 난다. PIL 이 없으면 원본을 그대로 둔다
        (크기가 다른 것보다 이미지가 없는 게 나쁘다).
        """
        try:
            from PIL import Image
        except Exception:
            return
        try:
            with Image.open(path) as im:
                if im.size == (int(width), int(height)):
                    return
                tw, th = int(width), int(height)
                sw, sh = im.size
                scale = max(tw / sw, th / sh)
                nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
                im2 = im.convert("RGB").resize((nw, nh), Image.LANCZOS)
                left, top = (nw - tw) // 2, (nh - th) // 2
                im2.crop((left, top, left + tw, top + th)).save(path, "JPEG", quality=92)
        except Exception as e:
            log.warning(f"[Cloudflare] 비율 조정 실패(원본 유지): {e}")


def provider_effective() -> dict:
    """★ 실제로 생성되는지 **동작으로 확인** (patch_effective 표준).

    "프로바이더를 추가했다" 는 적용의 증거가 아니다. 한 장 받아봐야 안다.
    """
    import tempfile
    if not provider_available():
        return {"ok": False, "error": "자격증명 없음"}
    try:
        with tempfile.TemporaryDirectory() as td:
            p = CloudflareProvider().generate("a red apple on a wooden table",
                                              Path(td), width=512, height=288, seed=7)
            return {"ok": True, "bytes": p.stat().st_size}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
