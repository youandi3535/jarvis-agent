"""발행창 밖 선계산(precollect) 캐시 — 사용자 박제 2026-07-18.

무거운 fact·chart 추출 LLM 을 *저부하 창*(경제=06:00 트렌드 잡 말미 체이닝 / 테마 20:00)에서 미리
수행해 캐시하고, 발행창(경제 07:00 / 테마 21:00)은 캐시를 재사용한다 → 발행창 내 추출 LLM 0회 →
직후 writer 가 버스트로 열화되지 않은 Max 풀에서 실행(300s 스톨 조건 제거). "수집 데이터 전부 활용" 박제는
전문 추출을 그대로 유지하고 *시점만* 앞당기므로 무위반.

★ 순수 최적화 — 캐시 미스·만료·오류 시 호출자는 반드시 기존 수집 경로로 폴백(현행 동작 보존).
캐시는 결코 발행을 막지 않는다.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("jarvis.precollect")

_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "precollect"
# 같은 발행 사이클 안에서만 유효(경제 06:00 트렌드후→07:00 발행·테마 20:00→21:00 발행). 하루 넘긴 잔재 무효.
_TTL_SEC = 6 * 3600


def _key_path(post_type: str, keyword: str) -> Path:
    h = hashlib.md5((keyword or "").strip().lower().encode("utf-8")).hexdigest()[:12]
    d = datetime.now().strftime("%Y%m%d")
    return _CACHE_DIR / f"{post_type}_{d}_{h}.pkl"


def save_precollect(post_type: str, keyword: str, payload: dict) -> bool:
    """선계산 결과(nv_collect/ts_collect 반환 dict)를 피클 캐시. 성공 여부 반환."""
    if not keyword or not isinstance(payload, dict) or not payload.get("success"):
        return False
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _key_path(post_type, keyword)
        with open(p, "wb") as f:
            pickle.dump({"ts": time.time(), "keyword": keyword, "payload": payload}, f)
        log.info(f"[precollect] 저장: {post_type}/{keyword} → {p.name}")
        return True
    except Exception as e:
        log.warning(f"[precollect] 저장 실패({post_type}/{keyword}): {e}")
        return False


def load_precollect(post_type: str, keyword: str) -> dict | None:
    """당일·TTL 내 선계산 결과 반환. 없거나 만료·오류면 None(호출자는 기존 수집으로 폴백)."""
    if not keyword:
        return None
    try:
        p = _key_path(post_type, keyword)
        if not p.exists():
            return None
        with open(p, "rb") as f:
            rec = pickle.load(f)
        if time.time() - float(rec.get("ts", 0)) > _TTL_SEC:
            log.info(f"[precollect] 만료 무시: {post_type}/{keyword}")
            return None
        payload = rec.get("payload")
        if isinstance(payload, dict) and payload.get("success"):
            log.info(f"[precollect] 히트: {post_type}/{keyword} — 발행창 추출 LLM 0회")
            return payload
        return None
    except Exception as e:
        log.warning(f"[precollect] 로드 실패({post_type}/{keyword}): {e} — 기존 수집 폴백")
        return None


def _pin_path() -> Path:
    d = datetime.now().strftime("%Y%m%d")
    return _CACHE_DIR / f"theme_pinned_{d}.txt"


def pin_theme(theme: str) -> bool:
    """★ 테마 선계산 고정 (사용자 박제 2026-07-18) — 테마는 카탈로그에서 random 선정되므로,
    선계산 잡(20:00)이 고른 테마를 마커로 고정해 발행(21:00)이 *같은 테마*를 쓰게 한다
    (→ 캐시 히트). 마커 없으면 발행은 기존 random 선정으로 폴백."""
    if not theme:
        return False
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _pin_path().write_text(theme.strip(), encoding="utf-8")
        log.info(f"[precollect] 테마 고정: {theme}")
        return True
    except Exception as e:
        log.warning(f"[precollect] 테마 고정 실패({theme}): {e}")
        return False


def load_pinned_theme() -> str | None:
    """당일 고정 테마 반환(선계산 잡이 고른 것). 없으면 None(발행은 random 선정 폴백)."""
    try:
        p = _pin_path()
        if not p.exists():
            return None
        t = p.read_text(encoding="utf-8").strip()
        return t or None
    except Exception:
        return None


def clear_pinned_theme() -> None:
    """발행 완료·소비 후 고정 마커 제거 (다음 사이클 오염 방지)."""
    try:
        _pin_path().unlink(missing_ok=True)
    except Exception:
        pass


__all__ = ["save_precollect", "load_precollect",
           "pin_theme", "load_pinned_theme", "clear_pinned_theme"]
