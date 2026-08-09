"""JARVIS03 RADAR 수집 계층 — 오류 타입 세분화 단일 진입점.

★ 왜 여기인가 (ERRORS [547] · CLAUDE.md '오류는 세분화해서 기록')
  실측 90일: `source='radar'` 264건이 **전부 `ConnectionError` 한 타입** 이었다
  (전체 231건 중 223건=97%, `severity.type_granularity_issues()` 가 상시 경고).
  그 결과 ① 타입 기반 게이트가 전부 무력 ② Tier-1 지문 매칭이 타입에서 변별력 0
  ③ 기록만 보고는 *구글이 죽었는지 네이버가 죽었는지* 알 수 없었다.
  같은 병을 harness·watchdog·incident_responder 는 이미 자기 도메인에서 파생해
  풀었다(`harness_error_type`·`watchdog_error_type`·`posting_error_type`).
  수집 계층의 대응물이 이 파일이다 — 중앙 매핑표를 만들지 않는다.
"""
from __future__ import annotations

import re as _re
import traceback as _traceback

__all__ = ["radar_error_type", "radar_error_type_from_record", "report_radar"]

# 호스트·원인은 **예외 메시지에 이미 들어 있다** — 새 판단을 만들지 않고 그걸 읽는다.
_HOST_RE  = _re.compile(r"host=['\"]([A-Za-z0-9.\-]+)['\"]")
_CAUSE_RE = _re.compile(r"Caused by ([A-Za-z]+?)(?:Error|Exception)?\(")


def _camel(text: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in _re.split(r"[^A-Za-z0-9]+", text) if p)


def _derive_type(base: str, msg: str) -> str:
    """`radar_error_type` 과 `radar_error_type_from_record` 공용 파생 규칙 (① 단일 진입점)."""
    m = _HOST_RE.search(msg)
    if not m:
        return base
    labels = [l for l in m.group(1).split(".") if l]
    host = _camel("".join(f"{l} " for l in labels[:-1])) if len(labels) > 1 else _camel(labels[0])
    if not host:
        return base
    c = _CAUSE_RE.search(msg)
    cause = _camel(c.group(1)) if c else "Unreachable"
    return f"Radar{host}{cause}"


def radar_error_type(exc) -> str:
    """수집 실패 → 세분화된 error_type. 네트워크 실패가 아니면 원래 타입 그대로.

    파생 규칙 (전부 기계적 — 목록 없음):
      · 호스트  `trends.google.com` → 마지막 라벨(TLD)을 떨구고 CamelCase → `TrendsGoogle`
      · 원인    `(Caused by NameResolutionError(...))` → `NameResolution`
      → `RadarTrendsGoogleNameResolution`

    새 수집처가 생기면 **자동으로** 새 타입이 따라온다. 호스트를 못 읽으면
    원래 예외 타입으로 되돌아간다(과잉 분류 금지 — 없는 정보를 지어내지 않는다).
    """
    base = type(exc).__name__ if isinstance(exc, BaseException) else str(exc or "")
    msg = str(exc or "")
    return _derive_type(base, msg)


def radar_error_type_from_record(error_type: str, message: str) -> str:
    """DB 에 이미 저장된 (error_type, message) 쌍에서 세분화 타입을 소급 파생.

    ★ 왜 필요한가 (2026-08-09 GUARDIAN 감사 — `severity.selfcheck()` [결함4] 재발)
      `radar_error_type(exc)` 는 *앞으로* 들어오는 실시간 예외만 세분화한다.
      2026-08-08 fix 이전에 이미 `error_log` 에 뭉뚱그려 쌓인 행은 그대로 남아
      `type_granularity_issues()` 14일 창 안에서 계속 걸린다. 이 함수는 `_derive_type`
      (①단일 진입점)을 그대로 재사용해 저장된 message 문자열에서 같은 규칙으로
      재분류값을 계산한다 — 로직을 복제하지 않는다. 실제 UPDATE 는 호출자(backfill
      스크립트) 책임.
    """
    return _derive_type(error_type or "", message or "")


def report_radar(a, b, **kw):
    """RADAR 전용 보고 — **타입만** 세분화하고 나머지는 `error_collector.catch` 그대로.

    ★ 호출부를 고치지 않는다 — 각 파일이 `report as _g_report` 대신 이걸 import 하면
      기존 `_g_report("radar", e, module=__name__)` 40여 곳이 그대로 세분화된다.

    ★ 인자 순서를 **판정하지 않고 위임한다** (2026-08-08 — 실제로 여기서 사고를 냈다)
      `catch(exc_or_type, source)` 는 구 시그니처 `report(source, exc)` 314곳을 위해
      **역순 자동 교정** 을 갖고 있다(ERRORS [298]). 래퍼가 `(source, exc)` 로 서명을
      고정하면 문자열 타입 호출(`_g_report("TrendsEmptyOverwriteBlocked", "radar", …)`)
      이 통째로 뒤집혀 error_type 에 `radar`, source 에 타입명이 박힌다.
      → 예외 객체가 *어느 쪽인지* 만 보고, 세분화할 게 없으면 **원래 인자 그대로** 넘긴다.
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import report as _catch
    except ImportError:
        return None
    exc = a if isinstance(a, BaseException) else (b if isinstance(b, BaseException) else None)
    if exc is None:
        return _catch(a, b, **kw)                    # 문자열 타입 호출 — 손대지 않는다
    etype = radar_error_type(exc)
    if etype == type(exc).__name__:
        return _catch(a, b, **kw)                    # 세분화할 게 없으면 종전 그대로
    source = b if a is exc else a                    # 예외가 아닌 쪽이 source
    kw.setdefault("message", str(exc))
    kw.setdefault("tb_str", _traceback.format_exc())
    return _catch(etype, source, **kw)
