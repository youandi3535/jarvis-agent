"""RADAR error_log 소급 재분류 — 일회성 DB backfill.

★ 왜 필요한가 (2026-08-09 GUARDIAN 감사 — severity.selfcheck() [결함4] 재발)
  커밋 5686c16 (2026-08-08) 이 `JARVIS03_RADAR/collectors/report_radar` 로 *앞으로*
  들어오는 수집 오류만 세분화하도록 고쳤다. 그 시점 이전에 이미 error_log 에
  뭉뚱그려 쌓인 행(source='radar')은 그대로 남아 `type_granularity_issues()` 의
  14일 창 안에서 매번 같은 결함으로 재발했다. 이 스크립트는 저장된 message 를
  `JARVIS03_RADAR.collectors.radar_error_type_from_record` (실시간 경로와 동일한
  단일 파생 함수) 에 통과시켜 error_type 컬럼만 갱신한다 — 새 판단을 만들지 않는다.

멱등: 이미 세분화된 행은 새 값과 같아 SKIP. 여러 번 실행해도 안전.

사용: .venv/bin/python tools/backfill_radar_error_types.py [--dry-run]
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.db import get_db
from JARVIS03_RADAR.collectors import radar_error_type_from_record


def backfill(dry_run: bool = False) -> dict:
    con = get_db()
    rows = con.execute(
        "SELECT id, error_type, message FROM error_log WHERE source='radar'"
    ).fetchall()
    changed: dict[int, tuple[str, str]] = {}
    for r in rows:
        new_type = radar_error_type_from_record(r["error_type"], r["message"] or "")
        if new_type and new_type != r["error_type"]:
            changed[r["id"]] = (r["error_type"], new_type)
            if not dry_run:
                con.execute("UPDATE error_log SET error_type=? WHERE id=?", (new_type, r["id"]))
    if not dry_run:
        con.commit()
    con.close()
    return changed


if __name__ == "__main__":
    result = backfill(dry_run="--dry-run" in sys.argv)
    dist = Counter(new for _old, new in result.values())
    print(f"재분류 대상 {len(result)}건")
    for t, c in dist.most_common():
        print(f"  {t}: {c}")
