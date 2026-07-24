"""
JARVIS03 — 텔레그램 승인 봇
인라인 버튼 콜백을 처리해 post_analysis 상태를 업데이트하고,
승인(✅) 시 개선 제안을 learning_insights 로 학습 → *다음 글* 에 반영.
(★ '이 글 재발행'(revise_adapter)은 fd87275 에서 폐지 — 사용자 결정 2026-07-24. 개선은 학습만.)

실행: python approval_bot.py   (long-polling 데몬)
"""
from __future__ import annotations

import sys
import json
import time
import os
import requests
from pathlib import Path

BASE_DIR    = Path(__file__).parent
JARVIS_ROOT = BASE_DIR.parent
sys.path.insert(0, str(JARVIS_ROOT))

from dotenv import load_dotenv
load_dotenv(JARVIS_ROOT / ".env")

from shared import db
from shared.bus import on_post_revise_approved

TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def _answer_callback(callback_id: str, text: str = "처리 완료"):
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/answerCallbackQuery",
        json={"callback_query_id": callback_id, "text": text},
        timeout=10,
    )


def _send_tg(text: str, chat_id: str = None):
    from shared.notify import send_tg
    send_tg(text, chat_id=chat_id)


def _edit_keyboard(chat_id, message_id, keyboard):
    """기존 메시지의 키보드만 갱신 (토글 후 시각적 반영)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id, "reply_markup": keyboard},
            timeout=10,
        )
    except Exception:
        pass


def _handle_callback(cq: dict):
    """인라인 버튼 콜백 처리."""
    cq_id      = cq["id"]
    data       = cq.get("data", "")
    chat_id    = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]

    if ":" not in data:
        _answer_callback(cq_id, "알 수 없는 명령")
        return

    parts = data.split(":")
    action = parts[0]
    try:
        analysis_id = int(parts[1])
    except (ValueError, IndexError):
        _answer_callback(cq_id, "잘못된 ID")
        return

    record = db.get_analysis_by_id(analysis_id)
    if not record:
        _answer_callback(cq_id, "레코드를 찾을 수 없음")
        return

    suggestions = json.loads(record.get("suggestions") or "[]")
    n_shown     = min(len(suggestions), 6)

    # ── 토글 (pending_approval 상태에서만 동작) ──────────────────
    if action == "tog":
        if record["status"] != "pending_approval":
            _answer_callback(cq_id, f"이미 처리됨 ({record['status']})")
            return
        try:
            idx = int(parts[2])
        except (ValueError, IndexError):
            _answer_callback(cq_id, "잘못된 인덱스")
            return
        cur = set(db.get_partial_selection(analysis_id, default_n=n_shown))
        if idx in cur:
            cur.discard(idx)
            mark = "⬜"
        else:
            cur.add(idx)
            mark = "✅"
        new_sel = sorted(cur)
        db.set_partial_selection(analysis_id, new_sel)
        # 키보드 다시 그리기 (지연 import 로 순환 회피)
        from JARVIS03_RADAR.post_quality_analyzer import _build_partial_keyboard
        kb = _build_partial_keyboard(analysis_id, suggestions, new_sel)
        _edit_keyboard(chat_id, message_id, kb)
        _answer_callback(cq_id, f"제안 {idx+1} {mark}")
        return

    # ── 그 외 액션은 pending_approval 에서만 의미 있음 ──────────
    if record["status"] != "pending_approval":
        _answer_callback(cq_id, f"이미 처리된 항목 (상태: {record['status']})")
        return

    if action in ("apply", "approve_all"):
        # apply: 부분 선택 적용 / approve_all: 전체 (호환성)
        if action == "approve_all":
            applied = suggestions
            mode    = "all"
        else:
            sel     = db.get_partial_selection(analysis_id, default_n=n_shown)
            applied = [s for i, s in enumerate(suggestions) if i in sel]
            mode    = "partial" if len(applied) < len(suggestions) else "all"

        if not applied:
            _answer_callback(cq_id, "선택된 제안 없음 — ✅ 토글 또는 ❌ 거부 사용")
            return

        db.approve_analysis(analysis_id, {"suggestions": applied, "mode": mode})
        # ★ 2026-07-24 (사용자 결정): '이 글 재발행'(revise_adapter) 폐지 → 승인 = *학습*(다음 글 반영).
        #   수동 ✅ 도 1시간 자동승인과 *동일* learn_from_suggestions 단일 함수로 학습(종전엔 수동 ✅ 가
        #   죽은 revise 만 불러 학습조차 안 됐다). scope=post_type 이어야 실제 재사용됨.
        from JARVIS03_RADAR.post_quality_analyzer import learn_from_suggestions
        _scope = (record.get("post_type") or "").strip() or "all"
        _learned = learn_from_suggestions(applied, scope=_scope)
        on_post_revise_approved(analysis_id, record["platform"], record["theme"])
        _answer_callback(cq_id, f"✅ {_learned}개 학습 반영")
        _send_tg(
            f"✅ *[{record['platform'].upper()}] {record['theme']}*\n"
            f"{_learned}/{len(suggestions)}개 제안 학습 완료 — 다음 글에 반영 ({mode})",
            str(chat_id),
        )

    elif action == "reject":
        db.reject_analysis(analysis_id)
        _answer_callback(cq_id, "❌ 건너뜀")
        _send_tg(
            f"❌ *[{record['platform'].upper()}] {record['theme']}* 개선 건너뜀",
            str(chat_id),
        )

    else:
        _answer_callback(cq_id, "알 수 없는 액션")


def _clear_webhook():
    """webhook 설정 제거 — 409 Conflict 원인 중 하나."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
    except Exception:
        pass


# ── 진입점 제거됨 ────────────────────────────────────────────
# polling 루프는 jarvis_daemon.py 의 통합 텔레그램 봇이 담당합니다.
# 이 모듈은 _handle_callback() 만 외부에서 호출됩니다.
if __name__ == "__main__":
    from pathlib import Path
    _daemon = Path(__file__).resolve().parent.parent / "jarvis_daemon.py"
    print("⚠️  approval_bot.py 는 라이브러리 모듈입니다. 직접 실행하지 마세요.")
    print(f"   통합 데몬 실행:  python {_daemon}")
