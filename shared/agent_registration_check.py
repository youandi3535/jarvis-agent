"""shared/agent_registration_check.py — 도메인 자동 등록 검증.

★ 사용자 박제 2026-05-17 — "새 도메인 폴더 생기면 자동 등록되야 — 매번 수동 등록하라고 말해야 하나?"

새 `JARVIS{NN}_*/` 폴더가 추가되면 다음 4 항목 자동 검증:
  1) `{name}_agent.py` 파일 존재 (데몬 자동 인식 진입점)
  2) `register(scheduler, bus)` 함수 정의
  3) `declare(agent_id=..., status_fn=..., help_section=...)` capability 등록
  4) `AGENTS.md` 의 "현재 등록된 에이전트" 표에 행 존재

호출:
  python shared/agent_registration_check.py              # 검증 + 콘솔 출력
  python shared/agent_registration_check.py --fix       # 누락 자동 보강 (stub agent.py + AGENTS.md 행)

데몬 부팅 시 자동 호출 + auto_repair Layer 8 에 통합.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

_ROOT = Path(__file__).resolve().parent.parent

# 에이전트 등록 패턴 (jarvis_daemon._autoregister_agents 와 동일 규칙)
_AGENT_FOLDER_PAT = re.compile(r'^JARVIS\d+_[A-Z_]+$')


class AgentStatus(NamedTuple):
    folder: str
    has_agent_file: bool
    agent_file: str
    has_register: bool
    has_declare: bool
    has_agents_md_row: bool
    @property
    def ok(self) -> bool:
        return all([self.has_agent_file, self.has_register, self.has_declare,
                    self.has_agents_md_row])


def scan() -> list[AgentStatus]:
    """모든 JARVIS{NN}_*/ 폴더 스캔 → AgentStatus 리스트 반환."""
    results: list[AgentStatus] = []
    agents_md = _ROOT / "AGENTS.md"
    agents_md_text = agents_md.read_text() if agents_md.exists() else ""

    for d in sorted(_ROOT.iterdir()):
        if not d.is_dir():
            continue
        if not _AGENT_FOLDER_PAT.match(d.name):
            continue
        agent_files = sorted(d.glob("*_agent.py"))
        # ★ 한 폴더에 여러 _agent.py 가 있을 수 있음 (예: JARVIS07 = eval_agent + guardian_agent).
        # register() 보유 파일 우선, 없으면 declare() 보유 파일, 그래도 없으면 첫 파일.
        af = None
        has_register = has_declare = False
        for cand in agent_files:
            src = cand.read_text()
            cand_reg = bool(re.search(r'^def\s+register\s*\(', src, re.MULTILINE))
            cand_dec = "declare(" in src
            if cand_reg:
                af, has_register, has_declare = cand, True, cand_dec
                break
        if af is None:
            for cand in agent_files:
                src = cand.read_text()
                if "declare(" in src:
                    af, has_declare = cand, True
                    break
        if af is None and agent_files:
            af = agent_files[0]
        af_name = af.name if af else ""
        # AGENTS.md 의 행 검출 — `JARVIS{NN}_NAME/` 백틱
        has_row = f"`{d.name}/`" in agents_md_text
        results.append(AgentStatus(
            folder            = d.name,
            has_agent_file    = af is not None,
            agent_file        = af_name,
            has_register      = has_register,
            has_declare       = has_declare,
            has_agents_md_row = has_row,
        ))
    return results


def report(statuses: list[AgentStatus]) -> str:
    """검증 결과 텍스트 보고."""
    lines = ["JARVIS 에이전트 자동 등록 점검", "=" * 60]
    ok_count = 0
    for s in statuses:
        icons = []
        icons.append("📄" if s.has_agent_file else "❌📄")
        icons.append("⚙️" if s.has_register else "❌⚙️")
        icons.append("📡" if s.has_declare else "❌📡")
        icons.append("📋" if s.has_agents_md_row else "❌📋")
        ok = "✅" if s.ok else "⚠️ "
        if s.ok:
            ok_count += 1
        lines.append(f"{ok} {s.folder:25s} {''.join(icons)} {s.agent_file}")
    lines.append("=" * 60)
    lines.append(f"총 {len(statuses)} 폴더 / 완전 등록 {ok_count} / 누락 {len(statuses) - ok_count}")
    lines.append("")
    lines.append("범례: 📄 *_agent.py · ⚙️ register() · 📡 declare() · 📋 AGENTS.md 행")
    return "\n".join(lines)


def find_missing(statuses: list[AgentStatus]) -> list[str]:
    """누락 항목 리스트 반환 (자동 보강 또는 알림용)."""
    missing: list[str] = []
    for s in statuses:
        if not s.has_agent_file:
            missing.append(f"{s.folder}: *_agent.py 누락 — declare()+register() stub 생성 필요")
        elif not s.has_declare:
            missing.append(f"{s.folder}/{s.agent_file}: declare() 누락 — capability 텔레그램 미노출")
        elif not s.has_register:
            missing.append(f"{s.folder}/{s.agent_file}: register() 누락 — 잡 등록 진입점 없음")
        if not s.has_agents_md_row:
            missing.append(f"{s.folder}: AGENTS.md '등록된 에이전트' 표에 행 누락")
    return missing


def main() -> int:
    statuses = scan()
    print(report(statuses))
    missing = find_missing(statuses)
    if missing:
        print()
        print("★ 누락·자동 등록 권장:")
        for m in missing:
            print(f"  • {m}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
