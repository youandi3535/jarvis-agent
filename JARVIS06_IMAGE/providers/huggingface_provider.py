"""JARVIS06_IMAGE/providers/huggingface_provider.py — HuggingFace FLUX 프로바이더.

HuggingFace Inference API 를 통해 FLUX.1-schnell 모델로 이미지 생성.
HUGGINGFACE_API_KEY 필요: https://huggingface.co/settings/tokens

설정:
  HUGGINGFACE_API_KEY=hf_... (.env 에 등록)
"""
from __future__ import annotations
import hashlib, logging, os, time
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis")

_MODELS = [
    "black-forest-labs/FLUX.1-schnell",
    "black-forest-labs/FLUX.1-dev",
    "stabilityai/stable-diffusion-xl-base-1.0",
]

_API_URL_TMPL = "https://api-inference.huggingface.co/models/{model}"
_TIMEOUT = 60


class HuggingFaceProvider:
    """HuggingFace Inference API FLUX 프로바이더."""
    PROVIDER_ID = "huggingface"

    def __init__(self) -> None:
        self._api_key: Optional[str] = os.getenv("HUGGINGFACE_API_KEY", "").strip() or None
        if self._api_key:
            log.info("[HuggingFaceProvider] 초기화 완료 (API 키 있음)")
        else:
            log.info("[HuggingFaceProvider] HUGGINGFACE_API_KEY 없음 — 비활성화")

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def generate(self, prompt_en: str, out_dir: Path,
                 width: int = 1024, height: int = 1024,
                 seed: Optional[int] = None) -> Path:
        """HuggingFace FLUX 로 이미지 생성 → 로컬 파일 경로 반환.

        Raises:
            RuntimeError: API 키 없음 / 모든 모델 실패.
        """
        if not self.available:
            raise RuntimeError("HuggingFaceProvider 비활성화 (HUGGINGFACE_API_KEY 없음)")

        try:
            import requests
        except ImportError:
            raise RuntimeError("requests 미설치 — pip install requests")

        out_dir.mkdir(parents=True, exist_ok=True)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload: dict = {"inputs": prompt_en}
        if seed is not None:
            payload["parameters"] = {"seed": seed}

        last_err: Optional[Exception] = None
        for model in _MODELS:
            url = _API_URL_TMPL.format(model=model)
            log.info(f"[HuggingFaceProvider] 요청: model={model.split('/')[-1]} prompt='{prompt_en[:60]}'")
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
                if resp.status_code == 503:
                    # 모델 로딩 중 — 최대 30초 대기 후 재시도
                    wait = min(resp.json().get("estimated_time", 20), 30)
                    log.info(f"[HuggingFaceProvider] 모델 로딩 중 — {wait:.0f}초 대기")
                    time.sleep(wait)
                    resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT)
                ct = resp.headers.get("Content-Type", "")
                if resp.status_code == 200 and "image" in ct:
                    h = hashlib.md5(prompt_en.encode()).hexdigest()[:8]
                    ext = "jpg" if "jpeg" in ct else "png"
                    fname = f"hf_{h}_{int(time.time())}.{ext}"
                    out_path = out_dir / fname
                    out_path.write_bytes(resp.content)
                    log.info(f"[HuggingFaceProvider] 생성 완료: {out_path}")
                    return out_path
                else:
                    last_err = RuntimeError(
                        f"HuggingFace {model} 실패 (status={resp.status_code}, ct={ct}): {resp.text[:200]}"
                    )
                    log.warning(str(last_err))
            except Exception as e:
                last_err = e
                log.warning(f"[HuggingFaceProvider] {model} 오류: {e}")

        raise RuntimeError(f"HuggingFaceProvider 모든 모델 실패: {last_err}")


__all__ = ["HuggingFaceProvider"]
