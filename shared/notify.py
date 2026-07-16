"""공통 Telegram 알림 — daemon 의존 없이 어디서든 메시지 전송.

★ 사용자 박제 2026-05-15 — import 시 .env 자동 로드 보장.
   데몬 안에서는 jarvis_daemon 이 미리 load_dotenv 호출하지만, *수동 실행
   (subprocess / python -c / 외부 스크립트)* 에서도 환경변수 누락 없도록
   모듈 import 시점에 .env 강제 로드.
"""
from __future__ import annotations
import logging
import os
import re
import threading
from pathlib import Path

import requests

# ★ .env 자동 로드 — 모듈 import 시점 (실패해도 무시 — 데몬 컨텍스트에선 이미 로드됨)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except Exception:
    pass

_log = logging.getLogger("notify")


def call_with_hard_timeout(fn, *args, hard_timeout: float = 15.0, **kwargs):
    """fn(*args, **kwargs) 을 데몬 스레드로 실행해 wall-clock 상한을 강제 (2026-07-06).

    `requests` 의 `timeout=` 은 post-sleep-wake 좀비 소켓 등 OS/네트워크 이상 상태에서
    종종 무력화된다(실전 확인: ssl.py 내부 read 가 명시적 timeout=35 를 넘겨 정지).
    이 래퍼는 그런 상황에서도 호출자가 확실히 제어를 돌려받도록 보장한다.
    하드 타임아웃 초과 시 TimeoutError — 방치된 스레드는 daemon=True 라 프로세스
    종료를 막지 않는다.
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(hard_timeout)
    if t.is_alive():
        raise TimeoutError(f"{getattr(fn, '__name__', fn)} 하드 타임아웃 {hard_timeout:.0f}초 초과")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def md_escape(s: str) -> str:
    """legacy Markdown 특수문자 4종(_ * ` [) 이스케이프.

    동적 값(식별자·경로·오류메시지)을 Markdown 골격 메시지에 넣을 때 사용.
    예: 스네이크케이스 잡 ID(`j07_deep_audit`)의 `_` 가 미닫힘 엔티티로
    "can't parse entities" 를 유발하는 것을 사전 차단.
    """
    return re.sub(r'([_*`\[])', r'\\\1', s)


def send_tg(text: str, parse_mode: str = "Markdown", chat_id: str = None) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "")
    _chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not _chat_id:
        _log.debug("send_tg 스킵: TOKEN/CHAT_ID 없음")
        return
    try:
        # parse_mode 가 truthy 일 때만 키 포함 (None/"" 이면 plain 전송)
        payload = {"chat_id": _chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = call_with_hard_timeout(
            requests.post,
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
            hard_timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            if "parse" in desc.lower():
                # Markdown 파싱 실패 — warning 없이 조용히 plain 재전송부터 시도
                payload.pop("parse_mode", None)
                r2 = call_with_hard_timeout(
                    requests.post,
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=payload,
                    timeout=10,
                    hard_timeout=15,
                )
                if r2.json().get("ok"):
                    _log.info(f"Markdown 파싱 실패 → plain 재전송 성공: {desc}")
                else:
                    _log.warning(
                        f"sendMessage 실패: {desc} / plain 재전송도 실패: "
                        f"{r2.json().get('description')}"
                    )
            else:
                # parse 무관 오류 (chat not found 등) — 즉시 warning
                _log.warning(f"sendMessage 실패: {desc}")
    except Exception as e:
        _log.warning(f"텔레그램 전송 오류: {e}")


def send_tg_with_buttons(text: str, buttons: list, chat_id: str = None,
                          parse_mode: str = "Markdown") -> None:
    """인라인 키보드 버튼이 달린 텔레그램 메시지 전송."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    _chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not _chat_id:
        _log.debug("send_tg_with_buttons 스킵: TOKEN/CHAT_ID 없음")
        return
    try:
        # parse_mode 가 truthy 일 때만 키 포함 (None/"" 이면 plain 전송)
        payload = {"chat_id": _chat_id, "text": text,
                   "reply_markup": {"inline_keyboard": buttons}}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        r = call_with_hard_timeout(
            requests.post,
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=10, hard_timeout=15,
        )
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            if "parse" in desc.lower():
                # Markdown 파싱 실패 — warning 없이 조용히 plain 재전송부터 시도
                payload.pop("parse_mode", None)
                r2 = call_with_hard_timeout(
                    requests.post,
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=payload, timeout=10, hard_timeout=15,
                )
                if r2.json().get("ok"):
                    _log.info(f"Markdown 파싱 실패 → plain 재전송 성공: {desc}")
                else:
                    _log.warning(
                        f"send_tg_with_buttons 실패: {desc} / plain 재전송도 실패: "
                        f"{r2.json().get('description')}"
                    )
            else:
                # parse 무관 오류 (chat not found 등) — 즉시 warning
                _log.warning(f"send_tg_with_buttons 실패: {desc}")
    except Exception as e:
        _log.warning(f"텔레그램 버튼 메시지 전송 오류: {e}")


__all__ = ["send_tg", "send_tg_with_buttons", "call_with_hard_timeout", "md_escape"]
