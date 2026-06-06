"""shared/record_claude_change.py — VS Code Claude Code 파일 수정 자동 기록 스크립트.

PostToolUse 훅에서 호출됨:
  echo '{"tool_input":{"file_path":"..."}}' | python3 shared/record_claude_change.py

수정된 파일을 JARVIS07_GUARDIAN.error_collector.record_external_change() 에 자동 박제.
→ 이후 자가 학습 시스템이 패턴 학습에 활용.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return
        data = json.loads(raw)
    except Exception:
        return

    # Write 또는 Edit 훅 — tool_input.file_path 추출
    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        return

    fp = Path(file_path)
    # jarvis-agent 폴더 외부 파일은 무시
    try:
        fp.relative_to(_ROOT)
    except ValueError:
        return

    # .py / .md / .json / .yml 만 기록 (바이너리 제외)
    if fp.suffix not in (".py", ".md", ".json", ".yml", ".yaml", ".toml"):
        return

    rel_path = str(fp.relative_to(_ROOT))

    try:
        from JARVIS07_GUARDIAN.error_collector import record_external_change
        record_external_change(
            source="vscode_claude",
            fixed_file=rel_path,
            description=f"VS Code Claude Code 수정: {rel_path}",
            actor="vscode_claude",
            severity="low",
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
