"""JARVIS09_COLLECTOR/source_onboarding.py — 데이터 소스 가입·API 키 온보딩 (ADR 012).

★ 사용자 박제 2026-07-02: "수집 설계가 새 사이트 가입·API 키를 요구하면 자동으로
  가입해서 받아온다." — 단, 자비스 헌법(외부 영향 = 텔레그램 승인 게이트, ★영구)과
  현실 제약(한국 공공 포털 = 휴대폰 본인인증, CAPTCHA)을 반영해 2모드로 나눈다:

  - mode="auto":     사람 인증이 없는 소스 — 등록 절차를 코드가 끝까지 수행 가능.
                     (현재 카탈로그에는 해당 없음 — 신규 소스 추가 시 사용)
  - mode="assisted": 본인인증·CAPTCHA 필수 소스 — 자비스가 가입 URL·정확한 절차·
                     키 등록 위치까지 텔레그램으로 안내 → 사용자가 키만 .env 에 넣으면
                     다음 회차부터 자동 사용 + 자동 검증.

역할:
  1) 리서치/데이터 설계(plan)가 원하는 provider 중 키 누락 감지
  2) 텔레그램 1일 1회 안내 (스팸 방지 상태 파일)
  3) 키 유효성 검증 (등록 후 자동 확인)
  4) register_key() — .env 단일 위치에 키 기록

다른 파일 금지: 소스 가입·키 안내 로직은 이 파일 단독 (수집 도메인 = JARVIS09).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("jarvis.collector.onboarding")

try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **k):
        pass

_ROOT = Path(__file__).parent.parent
_STATE_PATH = Path(__file__).parent / "output" / "onboarding_state.json"
_NOTIFY_COOLDOWN_SEC = 24 * 3600      # 소스당 1일 1회 안내

# ── 키 필요 소스 카탈로그 — provider 키 ↔ env 키 ↔ 가입 절차 ─────────────────
#   신규 키-필요 provider 추가 시 여기에 한 줄 추가하면 감지·안내·검증 자동.
SOURCE_REGISTRY: dict[str, dict] = {
    "naver_news": {
        "name": "네이버 개발자센터 (뉴스·검색 API)",
        "env_keys": ["NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"],
        "signup_url": "https://developers.naver.com/apps/#/register",
        "mode": "assisted",  # 네이버 로그인 필요
        "steps": ("① 네이버 로그인 → 애플리케이션 등록 → '검색' API 선택\n"
                  "② Client ID/Secret 발급 (즉시)\n"
                  "③ .env 에 NAVER_CLIENT_ID=..., NAVER_CLIENT_SECRET=... 추가"),
        "value": "가장 정확한 한국어 뉴스 검색 — 수집 품질 직결",
    },
    "dart": {
        "name": "금융감독원 DART OpenAPI",
        "env_keys": ["DART_API_KEY"],
        "signup_url": "https://opendart.fss.or.kr/uss/umt/EgovMberInsertView.do",
        "mode": "assisted",  # 이메일 인증 필요
        "steps": ("① 이메일로 회원가입 → 인증 메일 확인\n"
                  "② 인증키 신청 (즉시 발급)\n"
                  "③ .env 에 DART_API_KEY=... 추가"),
        "value": "상장기업 재무제표·공시 — 종목 분석 사실성의 근간",
    },
    "ecos": {
        "name": "한국은행 ECOS OpenAPI",
        "env_keys": ["ECOS_API_KEY"],
        "signup_url": "https://ecos.bok.or.kr/api/#/AuthKeyApply",
        "mode": "assisted",
        "steps": ("① 이메일 회원가입 → 인증키 신청 (즉시)\n"
                  "② .env 에 ECOS_API_KEY=... 추가"),
        "value": "기준금리·환율·물가 등 거시 시계열 — 경제 글 차트 원천",
    },
    "kosis": {
        "name": "통계청 KOSIS OpenAPI",
        "env_keys": ["KOSIS_API_KEY"],
        "signup_url": "https://kosis.kr/openapi/index/index.jsp",
        "mode": "assisted",
        "steps": ("① 회원가입 → 활용신청 (즉시 발급)\n"
                  "② .env 에 KOSIS_API_KEY=... 추가"),
        "value": "인구·고용·물가·지역 공식 통계 — 비경제 주제 커버리지 확장",
    },
}


# ── 상태 파일 (알림 쿨다운) ─────────────────────────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    except Exception:
        pass


# ── 감지 ────────────────────────────────────────────────────────────────
def missing_sources(wanted_providers: list[str] | set[str] | None = None) -> list[str]:
    """키 누락 소스 목록. wanted_providers 지정 시 그 안에서만 검사."""
    out = []
    for pkey, spec in SOURCE_REGISTRY.items():
        if wanted_providers is not None and pkey not in set(wanted_providers):
            continue
        if any(not (os.getenv(k) or "").strip() for k in spec["env_keys"]):
            out.append(pkey)
    return out


def plan_wanted_providers(plan: dict) -> set[str]:
    """리서치/데이터 설계도에서 요구된 provider 키 집합."""
    wanted: set[str] = set()
    for q in (plan or {}).get("questions", []):
        wanted.update(q.get("sources") or [])
    for s in (plan or {}).get("series", []):     # data_planner 설계도 호환
        wanted.update(s.get("sources") or [])
    return wanted


# ── 안내 (텔레그램 1일 1회) ─────────────────────────────────────────────
def notify_missing(providers: list[str]) -> int:
    """누락 소스 가입 안내 텔레그램 전송. 전송 건수 반환 (쿨다운 내 재전송 0)."""
    if not providers:
        return 0
    state = _load_state()
    now = time.time()
    sent = 0
    for pkey in providers:
        spec = SOURCE_REGISTRY.get(pkey)
        if not spec:
            continue
        last = float(state.get(f"notified_{pkey}", 0))
        if now - last < _NOTIFY_COOLDOWN_SEC:
            continue
        msg = (f"🔑 *데이터 소스 키 등록 안내* — {spec['name']}\n"
               f"수집 설계가 이 소스를 원하지만 API 키가 없어 건너뛰는 중입니다.\n"
               f"가치: {spec['value']}\n"
               f"가입: {spec['signup_url']}\n"
               f"{spec['steps']}\n"
               f"등록하면 다음 발행부터 자동 사용·자동 검증됩니다.")
        try:
            from shared.notify import send_tg
            send_tg(msg)
            state[f"notified_{pkey}"] = now
            sent += 1
        except Exception as e:
            log.warning(f"[onboarding] 안내 전송 실패({pkey}): {e}")
    if sent:
        _save_state(state)
    return sent


def check_and_notify(plan: dict | None = None) -> list[str]:
    """설계도 기준 키 누락 감지 + 안내. 누락 provider 목록 반환 (fail-open)."""
    try:
        wanted = plan_wanted_providers(plan) if plan else None
        missing = missing_sources(wanted)
        if missing:
            log.info(f"[onboarding] 키 누락 소스: {missing}")
            notify_missing(missing)
        return missing
    except Exception as e:
        _g_report("collector", e, module=__name__, func_name="check_and_notify")
        return []


# ── 키 등록 + 검증 ──────────────────────────────────────────────────────
def register_key(env_key: str, value: str) -> bool:
    """.env 에 키 기록 (기존 키는 교체). 텔레그램 자연어 흐름·수동 등록 공용."""
    env_key = (env_key or "").strip().upper()
    value = (value or "").strip()
    if not env_key or not value or not re.match(r"^[A-Z][A-Z0-9_]+$", env_key):
        return False
    env_path = _ROOT / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
        pat = re.compile(rf"^{re.escape(env_key)}\s*=")
        lines = [ln for ln in lines if not pat.match(ln)]
        lines.append(f"{env_key}={value}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.environ[env_key] = value
        log.info(f"[onboarding] {env_key} 등록 완료")
        return True
    except Exception as e:
        _g_report("collector", e, module=__name__, func_name="register_key")
        return False


def validate_source(pkey: str, timeout: int = 8) -> bool:
    """등록된 키가 실제 동작하는지 경량 검증 (provider 1건 수집 시도)."""
    try:
        if pkey in missing_sources([pkey]):
            return False
        from JARVIS09_COLLECTOR.providers import (
            NaverNewsProvider, DartProvider, EcosProvider, KosisProvider,
        )
        prov_map = {
            "naver_news": NaverNewsProvider,
            "dart": DartProvider,
            "ecos": EcosProvider,
            "kosis": KosisProvider,
        }
        cls = prov_map.get(pkey)
        if cls is None:
            return True                   # 검증 수단 없는 소스는 통과 (fail-open)
        docs = cls().collect("경제", "", max_items=1)
        ok = bool(docs)
        log.info(f"[onboarding] {pkey} 검증 {'통과' if ok else '실패(응답 0건)'}")
        return ok
    except Exception as e:
        log.warning(f"[onboarding] {pkey} 검증 오류: {e}")
        return False


def onboarding_status() -> dict:
    """대시보드·/status 용 요약 — 소스별 등록 여부."""
    out = {}
    for pkey, spec in SOURCE_REGISTRY.items():
        out[pkey] = {
            "name": spec["name"],
            "registered": pkey not in missing_sources([pkey]),
            "mode": spec["mode"],
        }
    return out


__all__ = [
    "SOURCE_REGISTRY", "missing_sources", "plan_wanted_providers",
    "notify_missing", "check_and_notify", "register_key",
    "validate_source", "onboarding_status",
]
