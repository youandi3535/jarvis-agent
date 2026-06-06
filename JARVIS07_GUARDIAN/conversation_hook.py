#!/usr/bin/env python3
"""conversation_hook.py — Claude Code UserPromptSubmit / Stop 훅.

UserPromptSubmit:
  1. 로컬 캐시 (hit_count ≥ 3) → 직접 답변 반환, exit 2 (Claude 호출 X)
  2. 미해결 → exit 0 (Claude가 처리)

Stop:
  - 방금 대화의 마지막 Q&A 추출 → qa_store 학습 누적

설치 방법:
  ~/.claude/settings.json 의 hooks 섹션에 등록 (자동 설치 스크립트 참고)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# jarvis-agent 경로 추가
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.WARNING)  # hook 실행 중 불필요한 로그 억제
log = logging.getLogger("jarvis.hook")


def _read_stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


# ── UserPromptSubmit 처리 ──────────────────────────────────────────────────

def _handle_user_prompt_submit(event: dict) -> None:
    """로컬 해결 시도 → 해결되면 답변 출력 후 exit 2 (Claude 차단)."""
    prompt = event.get("prompt", "").strip()
    if not prompt or len(prompt) < 5:
        sys.exit(0)

    # JARVIS 내부 명령어 / 시스템 메시지는 그냥 통과
    if prompt.startswith("/") or len(prompt) > 3000:
        sys.exit(0)

    try:
        from JARVIS07_GUARDIAN.qa_resolver import resolve

        result = resolve(prompt, fast=True)

        if not result.get("resolved"):
            # 해결 못 함 → Claude에게 넘김
            sys.exit(0)

        source = result.get("source", "none")
        answer = result.get("answer", "").strip()
        confidence = result.get("confidence", 0.0)

        if not answer:
            sys.exit(0)

        # ★ 사용자 박제 2026-05-25 (ERRORS [161]) — 캐시 오매칭 차단
        #   기존 0.55 임계값이 FTS OR 검색의 잘못된 매칭을 통과시켜
        #   "자체학습 시스템 확인" 질문이 "trends 수집" 답변으로 차단된 사고.
        #   qa_resolver._local_cache_resolve 의 게이트와 동기화 (0.85).
        if confidence < 0.85:
            sys.exit(0)

        # 소스 레이블
        source_label = {
            "local_cache": "📚 로컬 캐시",
        }.get(source, source)

        # 답변 출력 (Claude Code가 이 내용을 응답으로 표시)
        output = f"{answer}\n\n---\n*{source_label} 자가 해결 (신뢰도: {confidence:.0%})*"
        sys.stdout.write(output)
        sys.stdout.flush()
        sys.exit(2)  # Claude 호출 차단

    except Exception as e:
        log.debug(f"[Hook/UserPromptSubmit] 오류: {e}")
        sys.exit(0)  # 오류 시 Claude에게 넘김


# ── Stop 처리 (Claude 답변 학습) ───────────────────────────────────────────

def _extract_last_qa(transcript_path: str) -> tuple[str, str] | None:
    """JSONL 트랜스크립트에서 마지막 user→assistant Q&A 추출."""
    try:
        path = Path(transcript_path)
        if not path.exists():
            return None

        lines = path.read_text(encoding="utf-8", errors="ignore").strip().split("\n")
        messages: list[tuple[str, str]] = []

        for line in lines:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue

            role = msg.get("role", "")
            content = msg.get("content", "")

            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "")
                    for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
            else:
                text = str(content).strip()

            if role in ("user", "assistant") and len(text) > 10:
                messages.append((role, text))

        # 마지막 user→assistant 쌍 찾기
        for i in range(len(messages) - 1, 0, -1):
            if messages[i][0] == "assistant" and messages[i - 1][0] == "user":
                q = messages[i - 1][1]
                a = messages[i][1]
                if len(q) > 10 and len(a) > 30:
                    return q, a

    except Exception as e:
        log.debug(f"[Hook/Stop] 트랜스크립트 파싱 오류: {e}")

    return None


def _handle_stop(event: dict) -> None:
    """Claude 답변 학습 누적."""
    transcript_path = event.get("transcript_path", "")
    if not transcript_path:
        sys.exit(0)

    try:
        qa = _extract_last_qa(transcript_path)
        if not qa:
            sys.exit(0)

        question, answer = qa

        # 내부 명령어 / 시스템 Q&A 제외
        if question.startswith("/") or len(question) > 2000:
            sys.exit(0)

        from JARVIS07_GUARDIAN.qa_resolver import learn_from_claude

        session_id = event.get("session_id", "")
        status = learn_from_claude(question, answer, session_id=session_id)

        if status in ("inserted", "updated"):
            log.info(f"[Hook/Stop] 학습 {status}: {question[:60]}...")

    except Exception as e:
        log.debug(f"[Hook/Stop] 오류: {e}")

    sys.exit(0)


# ── 진입점 ─────────────────────────────────────────────────────────────────

def main() -> None:
    event = _read_stdin_json()
    event_name = event.get("hook_event_name", "")

    if event_name == "UserPromptSubmit":
        _handle_user_prompt_submit(event)
    elif event_name == "Stop":
        _handle_stop(event)
    else:
        # 알 수 없는 이벤트 → 통과
        sys.exit(0)


if __name__ == "__main__":
    main()
