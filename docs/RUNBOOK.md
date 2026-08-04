# RUNBOOK — 발행이 안 나갔을 때

> **이 문서의 규칙: 사실을 적지 않는다. 사실을 알아내는 명령을 적는다.**
>
> 잡 ID·시각·경로·임계값을 여기 베껴 두면 코드가 바뀌는 순간 이 문서가 거짓말을 시작한다
> (CLAUDE.md — *복사본을 진실로 믿지 말 것*). 그래서 아래는 대부분 **명령** 이다.
> 명령의 답이 곧 현재 사실이다.

---

## 0. 30초 안에 상태 파악

```bash
cd <저장소 루트>
.venv/bin/python -c "
from JARVIS08_PUBLISH.publish_ledger import job_audit_publish_completeness as a
import json; print(json.dumps(a(), ensure_ascii=False, indent=2))"
```

읽는 법:

| 필드 | 뜻 |
|------|-----|
| `slot` | 지금 감사 대상인 **발행 창** (날짜가 아니라 창이다 — 21시 글은 다음날 07시까지가 제 창) |
| `expected` / `published` | 이 창에서 나갔어야 할 수 / 실제로 나간 수 |
| `gaps` | 안 나간 플랫폼 |
| `in_progress` | `true` 면 **실패가 아니라 아직 돌고 있는 중** — 기다린다 |

`gaps` 가 비어 있으면 정상이다. 여기서 끝.

---

## 1. 아직 돌고 있는가? (`in_progress: true`)

발행 락이 잡혀 있다는 뜻이다. 손대지 않는다. 락이 언제 낡은 것으로 간주되는지:

```bash
.venv/bin/python -c "
from JARVIS02_WRITER.scheduler import publish_lock_stale_sec, LOCK_FILE
import time, os
age = time.time() - LOCK_FILE.stat().st_mtime if LOCK_FILE.exists() else None
print('락 파일:', LOCK_FILE, '| 존재:', LOCK_FILE.exists())
print('경과:', f'{age/60:.0f}분' if age else '-', '| 낡음 기준:', publish_lock_stale_sec()//60, '분')"
```

경과가 기준을 넘었는데도 락이 남아 있으면 프로세스가 죽으면서 락을 못 지운 것이다 → **2번** 으로.

---

## 2. 데몬이 살아 있는가

```bash
pgrep -fl jarvis_daemon.py
ps -o lstart= -p $(pgrep -f jarvis_daemon.py | head -1)     # 언제 떴는지
```

- **아무것도 안 나옴** → 데몬이 죽었다. 맥북이 꺼져 있었거나 절전에 들어갔을 수 있다.
- **떠 있는데 코드 수정 시각보다 먼저 떴음** → 옛 코드를 메모리에 들고 있다. 재시작 필요.

재시작은 **반드시** 이 스크립트로 (keeper 언로드 → 좀비 정리 → 기동 → keeper 재등록 순서가 안에 박혀 있다):

```bash
./restart_daemon.sh
```

`pkill` + `nohup` 조합은 쓰지 않는다 — 중복 인스턴스와 keeper 영구 정지를 만든다.

---

## 3. 잡이 애초에 돌긴 했는가

```bash
.venv/bin/python -c "
from JARVIS04_SCHEDULER.job_registry import DEFAULT_JOBS
from JARVIS04_SCHEDULER.job_llm_priority import is_publish_callback, publish_post_type
for j in DEFAULT_JOBS:
    if is_publish_callback(j.get('callback')):
        print(j['id'], '|', publish_post_type(j.get('callback')), '|', j.get('kwargs'))"
```

여기서 나온 **잡 ID** 로 이력을 본다:

```bash
.venv/bin/python -c "
from shared.db import get_db
for r in get_db().execute(
  'SELECT job_id, started_at, success, error FROM job_runs ORDER BY id DESC LIMIT 20'):
    print(r)"
```

- **행이 없음** → 스케줄러가 잡을 안 돌렸다. 데몬이 그 시각에 안 떠 있었거나(misfire),
  부팅에서 막혔다 → 4번.
- **`success=0`** → 잡은 돌았고 안에서 터졌다. `error` 컬럼을 읽고 5번으로.
- **`success=1` 인데 글은 없다** → 잡은 끝났지만 *송출까지는 못 갔다*. 이게 가장 흔하다 → 5번.

---

## 4. 부팅에서 막혔는가 (Layer 0)

```bash
.venv/bin/python -c "
from JARVIS00_INFRA.preflight import run_preflight
r = run_preflight(strict=False)
print('통과:', r.ok); [print(' 실패', f) for f in r.failures]; [print(' 경고', w) for w in r.warnings]"
```

실패가 있으면 데몬은 뜨지 않는다(설계상 정상). 실패 항목을 먼저 고친다.

---

## 5. 잡은 돌았는데 글이 안 나갔다 — 어디서 멈췄나

발행은 harness 5 Layer 를 지난다. **송출(Layer 4)은 검증(Layer 3)을 통과해야만 열린다.**
즉 "글이 안 나갔다" 는 대부분 *실패* 가 아니라 **검증에서 멈춘 것** 이다.

```bash
.venv/bin/python -c "
from shared.db import get_db
for r in get_db().execute('''SELECT timestamp, source, error_type, status, substr(message,1,100)
  FROM error_log WHERE timestamp > datetime('now','-1 day')
  ORDER BY id DESC LIMIT 25'''): print(r)"
```

오류 타입으로 바로 조준 검색한다 (ERRORS.md 를 통독하지 않는다):

```bash
.venv/bin/python -c "
from JARVIS07_GUARDIAN.repair_history import incidents_brief
print(incidents_brief('<위에서 본 오류 메시지>', top_k=3))"
```

> ⛔ 검색 결과에 **헛다리** 로 표시된 항목은 다시 시도하지 않는다. 이미 실측으로 기각된 가설이다.

---

## 6. 손으로 되살리기

경보 메시지에 찍힌 잡 ID 를 그대로 쓴다. **텔레그램에서** 자연어로:

```
j01_economic_post 지금 실행
```

→ 인라인 버튼 ✅ 를 눌러야 실제로 돈다. 이 승인 게이트는 우회 경로가 없다(설계상 의도).

**주의 — 발행 시각을 늘리지 않는다.** 발행은 07:00 과 21:00 두 창뿐이다.
낮에 되살릴 때도 잡을 새로 등록하지 말고 *기존 잡을 지금 한 번 실행* 시킨다.

---

## 7. 로그를 열 때

```bash
.venv/bin/python -c "
import logging; from shared.secrets import install_log_masking as m
print(m())"        # 마스킹이 실제로 먹는지 (effective: true 여야 한다)

tail -200 logs/daemon.log
```

로그는 회전한다(`daemon.log.1` … ). 과거분에 평문 비밀이 남아 있는지 확인·세척:

```bash
.venv/bin/python -c "
from shared.secrets import redact_logs; print(redact_logs(dry_run=True))"   # 세어만 봄
.venv/bin/python -c "
from shared.secrets import redact_logs; print(redact_logs(dry_run=False))"  # 제자리 마스킹
```

세척은 **데몬을 내린 상태에서** 한다 (열려 있는 파일을 덮어쓰면 오프셋이 어긋난다).

---

## 8. 고치고 나서 — 순서 고정

① 수정 → ② `./restart_daemon.sh` → ③ **재시작된 프로세스로** 검증 → ④ 전부 커밋

②를 건너뛰고 "고쳤다" 고 판단하지 않는다. 파이썬은 옛 코드를 메모리에 들고 있다.
③은 `ps -o lstart=` 시각이 파일 수정 시각보다 **나중** 인지 확인한 뒤에 한다.

수정 사실은 반드시 박제한다 (안 하면 다음에 같은 걸 또 판다):

```bash
.venv/bin/python -c "
from JARVIS07_GUARDIAN.error_collector import report_manual_fix
report_manual_fix(source='publish', fixed_file='<파일>', description='<무엇을 왜>',
                  error_type='<세분화된 타입>', severity='medium', actor='user')"
```

---

## 부록 — 이 문서를 고쳐야 할 때

명령의 **출력** 이 달라지는 건 정상이다(그래서 명령을 적었다).
명령 **자체** 가 안 도는 순간이 이 문서를 고칠 때다. 사실을 베껴 넣고 싶어지면 참는다 —
그 유혹이 바로 이 저장소가 반복해서 앓았던 병이다.
