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


# ══════════════════════════════════════════════════════════════════
#  알림 아웃박스 — "못 보낸 말을 잊지 않는다" (사용자 승인 2026-07-25)
#
#  종전엔 전송 실패 시 로그 한 줄 남기고 메시지를 **버렸다**. 2026-07-25 네트워크 단절 중
#  4건이 영구 소멸했고 그중 하나가 "🚨 네이버 쿠키 점검 실패 — 테마글 발행 건너뜀" 이라,
#  그날 테마글이 왜 없는지 사용자가 알 방법이 없었다.
#
#  ① 단일 진입점 — 실제로 텔레그램에 던지는 곳은 `_post_message` **하나**. 보관·재전송
#     정책은 그 위에만 얹는다(재귀 없음: flush 는 send_tg 가 아니라 _post_message 를 쓴다).
#  ② 동적 설계 — 유효기간은 `NOTIFY_OUTBOX_TTL_H` 로 무배포 조정.
#  ③ 모든 통로 — 저장소의 모든 사용자 알림은 결국 이 파일의 두 함수로 모인다
#     (scheduler.send_telegram·post_quality_analyzer 등은 전부 이 둘을 부르는 래퍼).
#     예외는 `preflight.py` 의 부팅 실패 통보뿐 — Layer 0 은 DB 가용을 전제할 수 없다.
# ══════════════════════════════════════════════════════════════════

OUTBOX_TTL_HOURS = float(os.getenv("NOTIFY_OUTBOX_TTL_H", "6") or 6)
_NO_CONFIG = "TOKEN/CHAT_ID 없음"
_flush_lock = threading.Lock()


def _post_message(text: str, parse_mode: str, chat_id: str,
                  buttons: list | None = None) -> tuple[bool, str, bool]:
    """텔레그램에 **실제로 던지는 유일한 함수**. 아웃박스를 건드리지 않는다.

    Returns: `(성공?, 사유, 재시도가치있음?)`
      · 예외(네트워크·타임아웃) → 재시도 가치 **있음**. 길이 막힌 것뿐이다.
      · API 가 응답한 거절(chat not found·파싱 실패 등) → 재시도 가치 **없음**.
        서버에 닿았는데 거절당한 것이므로 100번 더 보내도 같다.
      · 429(과다요청)·5xx 만 예외적으로 재시도 가치 있음.
    """
    token = os.getenv("TELEGRAM_TOKEN", "")
    _chat = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not _chat:
        return False, _NO_CONFIG, False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {"chat_id": _chat, "text": text}
    if parse_mode:                      # truthy 일 때만 키 포함 (None/"" 이면 plain 전송)
        payload["parse_mode"] = parse_mode
    if buttons is not None:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        data = call_with_hard_timeout(
            requests.post, url, json=payload, timeout=10, hard_timeout=15,
        ).json()
        if data.get("ok"):
            return True, "", False
        desc = data.get("description", "")
        if "parse" in desc.lower() and parse_mode:
            # Markdown 파싱 실패 — warning 없이 조용히 plain 재전송부터 시도
            payload.pop("parse_mode", None)
            data2 = call_with_hard_timeout(
                requests.post, url, json=payload, timeout=10, hard_timeout=15,
            ).json()
            if data2.get("ok"):
                _log.info(f"Markdown 파싱 실패 → plain 재전송 성공: {desc}")
                return True, "", False
            return False, f"{desc} / plain 재전송도 실패: {data2.get('description')}", False
        code = int(data.get("error_code") or 0)
        return False, desc, (code == 429 or code >= 500)
    except Exception as e:
        return False, str(e), True                  # 네트워크·타임아웃 = 일시적


def _delayed_prefix(created_at: str) -> str:
    """지연 전송임을 본문 첫 줄에 박는다.

    ★ 이게 없으면 3시간 전 "발행 건너뜀" 이 지금 도착해 *지금 일* 로 오해된다.
      Markdown/plain 어느 쪽으로 전송돼도 깨지지 않도록 특수문자를 쓰지 않는다.
    """
    ts = str(created_at or "")[11:16]               # 'YYYY-MM-DD HH:MM:SS' → 'HH:MM'
    return f"🕐 {ts} 발생 (지연 전송)\n" if ts else ""


def flush_outbox(limit: int = 50) -> dict:
    """보관 중인 메시지 재전송 — **아웃박스의 유일한 배출구**. Returns 통계 dict."""
    stat = {"sent": 0, "kept": 0, "purged": 0}
    if not _flush_lock.acquire(blocking=False):
        return stat                                 # 이미 흘리는 중 — 중복 전송 방지
    try:
        from shared import db as _db
        stat["purged"] = _db.outbox_purge(OUTBOX_TTL_HOURS)
        for row in _db.outbox_pending(limit=limit):
            if not _db.outbox_claim(row["id"], row["attempts"]):
                continue                            # 다른 프로세스가 선점 — 건너뛴다
            ok, why, retryable = _post_message(
                _delayed_prefix(row["created_at"]) + row["text"],
                row["parse_mode"], row["chat_id"],
            )
            if ok:
                _db.outbox_done(row["id"])
                stat["sent"] += 1
            elif not retryable:
                _db.outbox_done(row["id"])
                stat["purged"] += 1
                _log.warning(f"[outbox] #{row['id']} 폐기(재시도 무의미): {why}")
            else:
                _db.outbox_fail(row["id"], why)
                stat["kept"] += 1
                break                               # 길이 아직 막혔다 — 나머지는 다음 회차
    except Exception as e:
        _log.warning(f"[outbox] flush 오류: {e}")
    finally:
        _flush_lock.release()
    if stat["sent"] or stat["purged"]:
        _log.info(f"[outbox] 재전송 {stat['sent']} / 보관 {stat['kept']} / 폐기 {stat['purged']}")
    return stat


def _on_send_success() -> None:
    """전송이 한 번 성공했다 = 길이 열렸다 → 밀린 것을 *즉시* 흘려보낸다.

    주기 잡(5분)을 기다리지 않고 복구 순간에 따라붙는다. 보낸 사람의 대기시간이 길어지지
    않도록 한 번에 소량만(limit=10) — 나머지는 주기 잡이 마저 흘린다.
    """
    try:
        from shared import db as _db
        if _db.outbox_has_pending():
            flush_outbox(limit=10)
    except Exception:
        pass


def _keep(text: str, parse_mode: str, chat_id: str, why: str) -> None:
    """전송 실패분 보관 — 실패 사유를 로그에 남기고 아웃박스에 넣는다."""
    try:
        from shared import db as _db
        rid = _db.outbox_put(text, parse_mode or "", chat_id or "")
        _log.warning(f"텔레그램 전송 실패 — 아웃박스 보관(#{rid}): {why}")
    except Exception as e:
        _log.warning(f"텔레그램 전송 실패 + 아웃박스 보관도 실패: {why} / {e}")


def send_tg(text: str, parse_mode: str = "Markdown", chat_id: str = None) -> None:
    ok, why, retryable = _post_message(text, parse_mode, chat_id or "")
    if ok:
        _on_send_success()
        return
    if why == _NO_CONFIG:
        _log.debug("send_tg 스킵: TOKEN/CHAT_ID 없음")
        return
    if retryable:
        _keep(text, parse_mode, chat_id or "", why)
    else:
        _log.warning(f"sendMessage 실패(재시도 무의미): {why}")


def send_tg_with_buttons(text: str, buttons: list, chat_id: str = None,
                          parse_mode: str = "Markdown") -> None:
    """인라인 키보드 버튼이 달린 텔레그램 메시지 전송.

    ★ 버튼 메시지는 **아웃박스에 넣지 않는다** (의도적): 승인 대기 상태(`_PENDING_*`)는
      데몬 *메모리* 에 있어 나중에 되살려 보내도 눌리지 않고, 지난 승인을 지금 승인한 것으로
      오해할 위험까지 있다. 대신 "요청이 있었다" 는 사실만 글로 남겨 보관한다 —
      사용자가 다시 요청할 수 있으면 그걸로 충분하고, 그게 유령 버튼보다 안전하다.
    """
    ok, why, retryable = _post_message(text, parse_mode, chat_id or "", buttons=buttons)
    if ok:
        _on_send_success()
        return
    if why == _NO_CONFIG:
        _log.debug("send_tg_with_buttons 스킵: TOKEN/CHAT_ID 없음")
        return
    _log.warning(f"send_tg_with_buttons 실패: {why}")
    if retryable:
        head = (text or "").strip().splitlines()[0][:80] if (text or "").strip() else ""
        _keep(f"⚠️ 승인 요청을 전하지 못했습니다 (네트워크). 필요하면 다시 요청해 주세요.\n· {head}",
              "", chat_id or "", why)


def job_flush_outbox() -> None:
    """APScheduler 콜백 — 밀린 알림 재전송 (JARVIS04 DEFAULT_JOBS `notify_outbox_flush`)."""
    flush_outbox()


__all__ = ["send_tg", "send_tg_with_buttons", "call_with_hard_timeout", "md_escape",
           "flush_outbox", "job_flush_outbox", "OUTBOX_TTL_HOURS"]
