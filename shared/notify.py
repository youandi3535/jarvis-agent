"""공통 Telegram 알림 — daemon 의존 없이 어디서든 메시지 전송.

★ 사용자 박제 2026-05-15 — import 시 .env 자동 로드 보장.
   데몬 안에서는 jarvis_daemon 이 미리 load_dotenv 호출하지만, *수동 실행
   (subprocess / python -c / 외부 스크립트)* 에서도 환경변수 누락 없도록
   모듈 import 시점에 .env 강제 로드.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path

import requests

# ★ .env 자동 로드 — 모듈 import 시점 (실패해도 무시 — 데몬 컨텍스트에선 이미 로드됨)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

_log = logging.getLogger("notify")


def send_tg(text: str) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        _log.debug("send_tg 스킵: TOKEN/CHAT_ID 없음")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            _log.warning(f"sendMessage 실패: {desc}")
            if "parse" in desc.lower():
                r2 = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                    timeout=10,
                )
                if not r2.json().get("ok"):
                    _log.warning(f"plain text 재시도 실패: {r2.json().get('description')}")
    except Exception as e:
        _log.warning(f"텔레그램 전송 오류: {e}")


__all__ = ["send_tg"]
