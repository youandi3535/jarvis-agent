"""JARVIS06_IMAGE/validators/image_data_verifier.py — 차트/인포그래픽 *데이터 사실성* 검증.

★ 사용자 박제 2026-06-29 — "데이터가 들어가는 이미지는 절대 거짓된 데이터로 만들면 안 됨."

  텍스트(대본)는 prepublish_gate 가 검수하지만, *이미지 안의 수치* 는 별도 검증이 없었다.
  이 모듈이 그 갭을 막는다: 차트 spec 의 모든 숫자가 JARVIS09 실데이터(출처 보유)로
  뒷받침되는지 검증한다.

정책 (사용자 선택 — "검증분만 재구성 후 스킵"):
  ① 텍스트 카드(숫자 없는 인포그래픽)   → 검증 면제, 그대로 통과.
  ② 이미 dataset 로 만들어진 spec       → _provenance.verified=True 신뢰, 통과.
  ③ LLM 본문 추출 수치 spec            → 각 값을 실데이터와 대조:
       - 검증된 행만 남겨 재구성 (검증 행 ≥ 최소 개수면 통과)
       - 0개 검증 + 관련 실데이터 dataset 존재 → 그 dataset 으로 *대체* (실데이터 차트)
       - 0개 검증 + dataset 없음          → None 반환 (호출자는 차트 스킵: return "")

킬스위치 (무배포 — 라이브 안전장치):
  IMAGE_DATA_GATE=0        미검증 이미지를 폐기하지 않음 (검증·기록은 계속)
  IMAGE_VERIFY_TELEMETRY=0 events 적재 중지 (판정은 그대로)

관측 (2026-08-10 사고 — '검증 없이 8장 발행' 을 아무 수치도 없이 지나쳤다):
  인증 1건마다 카운터 + `events`(image_verify) 1행. 폐기율은 *남기기만* 하고
  좋고 나쁨을 판정하지 않는다 — 지어낸 임계값보다 수치가 낫다(② 동적 설계).

공개 API (★ CLAUDE.md 규정13 — 차트 데이터 사실성 로직은 이 파일 단독):
  verify_chart_spec(spec, datasets) -> spec | None
  has_provenance(spec) -> bool
  source_caption(source) -> str
  min_rows(viz) -> int                     차트형이 성립하는 최소 행 수
  chart_fit(ds, rows=) -> str              데이터 형태 → 표현 형태 (차트형 판정 단일 소유자)
  is_timeseries(ds, rows=) -> bool         한 지표의 시점별 값인가 (선을 그어도 되는 유일 조건)
  series_shape(ds, rows=) -> dict          행 집합의 꼴 단일 파생 (시계열·가산성·차트형 공통)
  additive_total(ds, rows=) -> (값|None, 사유)   이 행들을 더해 표시해도 되는가
  row_provenance(ds, rows=) -> dict        행별 as_of·source 집계
  grounding_pool(datasets, rendered_rows=) -> (허용값집합, 원본값)
  verify_rendered_html(html, datasets, rendered_rows=) -> (ok, 미근거수치)
  certify_image(path, engine=, ...) -> provenance   ★ 레지스트리 쓰기 유일 경로
  verifier_effective() -> bool             patch_effective 표준 스모크
  verification_stats() -> dict             인증 시도·통과·폐기·사유 (관측)
  gate_enabled() -> bool                   킬스위치 IMAGE_DATA_GATE 조회
  GATE_ENV / TELEMETRY_ENV                 킬스위치 이름 단일 소스
  dataset_admissible(ds, category=) -> (bool, 사유)
  DATA_IMAGE_ATTR                          수치 이미지 <img> 표식 속성명(단일 소스)
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from shared.numeric import safe_float

log = logging.getLogger("jarvis.image.dataverify")

# ── 출처 레지스트리 (트립와이어) ─────────────────────────────────────────
# 초크포인트(`infographic_engine._emit` → `certify_image`)가 생성한 이미지 path →
# provenance 매핑. prepublish_gate 가 "검증 안 된 수치 차트가 발행에 섞였는지"
# 최종 확인하는 근거 (process-local).
# ★ 종전 주석은 `render_from_spec` 을 가리켰으나 그 함수는 2026-08-10 삭제됐다 —
#   트립와이어의 위치를 잘못 알려주는 주석은 다음 작업자를 없는 파일로 보낸다.
_PROV_REGISTRY: dict[str, dict] = {}


def _record_provenance(image_path, provenance: dict) -> None:
    """레지스트리 기록 — ★ private. 외부에서 직접 부르지 말 것 (certify_image 만 호출).

    ★ 왜 public 을 폐지했나 (사용자 박제 2026-08-10): 종전엔 공개 함수라 등록 경로가
      *렌더러마다* 따로였고, 그중 render_pro 하나가 등록을 빠뜨리자 2026-08-10 경제
      브리핑 8장이 통째로 provenance 없이(=검증 미수행) 발행됐다. 등록을 한 문(certify_image)
      으로 모으면 '등록을 빠뜨림' 이라는 상태가 아예 존재할 수 없다.
      기계 강제: precommit `image/provenance-write-outside`.
    """
    try:
        _PROV_REGISTRY[str(Path(image_path).resolve())] = dict(provenance or {})
    except Exception:
        pass


def lookup_provenance(image_path) -> dict | None:
    """이미지 path 의 등록된 provenance 조회. 미등록이면 None."""
    try:
        return _PROV_REGISTRY.get(str(Path(image_path).resolve()))
    except Exception:
        return None


# ── 킬스위치 (라이브 안전장치) ────────────────────────────────────────
# ★ 이름을 여기 한 곳에서만 정한다 — 소비자(`infographic_engine._emit`·preflight)는
#   문자열을 다시 적지 말고 이 상수를 import 한다. 종전 사고의 형태가 그대로다:
#   같은 값이 두 곳에 있으면 한쪽만 바뀌고 나머지는 조용히 옛 값을 가리킨다.
GATE_ENV: str = "IMAGE_DATA_GATE"            # 0 → 미검증 이미지도 폐기하지 않음
TELEMETRY_ENV: str = "IMAGE_VERIFY_TELEMETRY"  # 0 → events 적재 중지 (판정은 그대로)


def _flag(name: str, default: bool = True) -> bool:
    """킬스위치 조회 — ★ 판정 규칙의 주인은 `error_collector.env_flag` 단독.

    규칙("0/false/no/off"만 끔 · **호출할 때마다** 조회)을 여기 복제하지 않는다.
    주인을 부르지 못하면 `default` 를 그대로 쓴다 — 게이트 쪽에서는 그것이 ON 이다
    (킬스위치를 못 읽었다고 안전장치가 풀리면 그건 킬스위치가 아니라 구멍이다).
    """
    try:
        from JARVIS07_GUARDIAN.error_collector import env_flag
        return bool(env_flag(name, default))
    except Exception:
        return default


def gate_enabled() -> bool:
    """미검증 이미지를 *버릴 것인가* (기본 True). `IMAGE_DATA_GATE=0` 이면 통과시킨다.

    끄더라도 검증·기록은 계속 돈다 — 무슨 일이 있었는지는 항상 남는다.
    """
    return _flag(GATE_ENV)


# ── 관측 (게이트가 죽으면 *드러나야* 한다) ────────────────────────────
# ★ 왜 필요한가 (2026-08-10 사고의 본질): 8장이 통째로 검증 없이 발행됐는데
#   **아무 수치도 남지 않아** 사람이 이미지를 눈으로 볼 때까지 아무도 몰랐다.
#   "게이트가 있다" 는 코드의 존재이고, "게이트가 돌았다" 는 카운터의 존재다.
# ★ 세는 곳은 `certify_image` 한 곳뿐이다(①) — 폐기 결정을 내리는 `_emit` 에서 또 세면
#   장부가 둘이 되고, 둘이 되면 반드시 어긋난다.
_STATS: dict[str, int] = {"certified": 0, "verified": 0, "unverified": 0, "audited": 0}
_STATS_ISSUES: dict[str, int] = {}      # 사유 → 건수 (사유 목록을 박지 않는다)

# 대조가 *실제로* 일어난 판정 방법 — 이 집합도 박제가 아니라 아래 규칙에서 파생한다:
#   "issues 에 `:unaudited` 표식이 없고, 재료로 값을 맞춰본 method" 만 감사로 센다.
_UNAUDITED_MARK = "unaudited"


def _issue_key(issue: str) -> str:
    """사유 문자열 → 집계 키. `ungrounded:3` 처럼 뒤에 붙는 *건수* 는 떼어낸다.

    어휘 목록을 박지 않는다(②) — 새 사유가 생기면 자동으로 새 키가 된다.
    """
    return str(issue or "").split(":", 1)[0].strip() or "unknown"


def _observe(prov: dict) -> None:
    """인증 1건 관측 — ★ private. `certify_image` 만 호출한다.

    남기는 것은 *사실* 뿐이다: 시도·통과·폐기(=미검증)·무감사 여부·사유 분포.
    임계값으로 좋고 나쁨을 판정하지 않는다(②) — 지어낸 임계값은 오탐을 만들고,
    오탐이 잦은 경보는 곧 무시당해 경보가 없느니만 못하다.
    """
    try:
        issues = [str(i) for i in (prov.get("issues") or [])]
        verified = prov.get("verified") is True
        _STATS["certified"] += 1
        _STATS["verified" if verified else "unverified"] += 1
        if not any(_UNAUDITED_MARK in i for i in issues):
            _STATS["audited"] += 1
        for i in issues:
            k = _issue_key(i)
            _STATS_ISSUES[k] = _STATS_ISSUES.get(k, 0) + 1
        if not verified:
            log.info("[dataverify] 미검증 인증 %d/%d — engine=%s method=%s issues=%s",
                     _STATS["unverified"], _STATS["certified"],
                     prov.get("engine"), prov.get("method"), issues)
        _telemetry(prov, issues, verified)
    except Exception as e:      # 관측이 본 기능을 죽이지 않는다
        log.debug(f"[dataverify] 관측 실패(무시): {e}")


def _telemetry(prov: dict, issues: list, verified: bool) -> None:
    """durable 기록 — 기존 관측 인프라(`shared.db` events 테이블)에 그대로 적재.

    ★ 새 저장소를 만들지 않는다(①): `events` 는 이미 보존기간(30일)·정리 잡·마스킹
      관문을 갖춘 감사 로그다. 여기에 한 줄씩 남기면 폐기율·사유 분포를 *질의로 파생*
      할 수 있다 — 집계값을 따로 저장해 두면 그 순간 그것이 복사본이 된다.
    """
    if not _flag(TELEMETRY_ENV):
        return
    try:
        from shared.db import log_event
        log_event("image_verify", "jarvis06_image", {
            "engine": str(prov.get("engine") or ""),
            "kind": str(prov.get("kind") or ""),
            "method": str(prov.get("method") or ""),
            "verified": bool(verified),
            "issues": [_issue_key(i) for i in issues],
            "gate_on": gate_enabled(),      # 게이트가 꺼진 채 돈 기록도 남는다
        })
    except Exception as e:
        log.debug(f"[dataverify] telemetry 적재 실패(무시): {e}")


def verification_stats() -> dict:
    """이 프로세스의 인증 통계 + *임계값 없는* 이상 신호.

    반환:
      certified/verified/unverified/audited — 건수
      discard_rate — 미검증 비율 (수치만. 높고 낮음을 여기서 판정하지 않는다)
      issues — 사유별 건수 (많은 순)
      signals — 임계값 없이 *참·거짓으로 결정되는* 것만:
        · `no_certification` — 인증이 한 건도 없었다. 이미지가 나왔는데 이 신호가 서면
          게이트가 안 돈 것이다(초크포인트를 우회한 경로가 있다는 뜻).
        · `all_unaudited`   — 인증은 돌았는데 대조가 한 건도 없었다.
          "검사했다" 는 기록만 쌓이고 실제로 맞춰본 값이 0이라는 뜻이다.
      ※ 폐기율의 정상 범위는 데이터·주제에 따라 달라진다. 근거 없는 상·하한을 세우는
        대신 수치를 남기고, 판정은 그 분포를 실제로 가진 쪽(events 질의)에 맡긴다.
    """
    total = _STATS["certified"]
    signals: list[str] = []
    if total == 0:
        signals.append("no_certification")
    elif _STATS["audited"] == 0:
        signals.append("all_unaudited")
    return {**_STATS,
            "discard_rate": (_STATS["unverified"] / total) if total else None,
            "issues": dict(sorted(_STATS_ISSUES.items(), key=lambda kv: -kv[1])),
            "gate_on": gate_enabled(),
            "signals": signals}

# 수치 차트 최소 데이터 개수 (kpi 는 1, 그 외 2)
# ★ 공개 승격 (사용자 박제 2026-08-10 — D13): 종전 `_MIN_ROWS` 는 이 파일 내부에서만
#   보였고, 정작 *막대를 그리는 코드* 는 '막대는 최소 2행' 규칙을 볼 수 없었다.
#   그래서 1행짜리 dataset 이 트랙 100% 를 채운 막대(정보량 0)로 렌더됐다.
MIN_ROWS: dict[str, int] = {"kpi_cards": 1, "comparison_kpi": 1,
                            "highlight_card": 1, "insight_card": 1,
                            "line_chart": 3}   # 점 2개는 선이 아니라 선분 — 추세가 아니다
MIN_ROWS_DEFAULT: int = 2

# 수치 이미지 <img> 표식 속성명 — 생산자=JARVIS06 의 <img> 빌더, 소비자=prepublish_gate.
# ★ 파일명·경로 리터럴로 '이건 차트다' 를 판별하지 않기 위한 단일 소스.
DATA_IMAGE_ATTR: str = "data-jarvis-data-image"

# 렌더 HTML grounding 관용치 (★ 2026-08-10 2차 — "1건 여유 = 이미지당 조작 수치 1개 통과").
#   여유를 두는 근거는 오직 *실측된 오탐* 이다. 오탐의 원인을 꼴 규칙으로 제거한 뒤
#   (① 날짜 토큰을 달력 꼴로 좁힘 ② 라벨·단위·기준일 배지를 데이터에서 파생해 제거
#    ③ 허용값을 '차트가 실제로 인쇄하는 값' 으로 한정) 다시 실측했다:
#     경제 12 + BOK 1 + 테마 4 dataset, 단일·다중 조합 × 골격 60회전 × rendered_rows 유/무
#     = **9,240 렌더에서 미근거 수치 0건**. 오탐이 0이면 여유의 근거가 없다.
#   여유 1건을 남기면 그것은 곧 '이미지당 조작 수치 1개 무료 통과' 다 — 실제로 조작 수치
#   48,213.7 이 그 1건 여유로 통과하는 것을 재현했다. → 0 으로 조인다(fail-closed).
#   ※ 다시 열어야 한다면 *숫자를 올리지 말고* 오탐의 원인을 꼴 규칙으로 없앨 것.
MAX_BAD_ABS: int = 0
MAX_BAD_RATIO: float = 0.0

# 값 매칭 — dv==0 zero-guard 절대바닥. 그 외 tolerance 는 통일 grounds() 위임 (Step 8)
_ABS_TOL = 0.5


def _to_float(x):
    """값 → 유한 float. 파싱 실패·NaN·Inf 는 None (단일 소스: shared.numeric.safe_float)."""
    return safe_float(x)


# ★ 1-a (2026-07-02): 텍스트 필드(제목·key_message·items 등)에 담긴 수치도 검증 대상.
#   이전엔 spec["data"] 배열만 검사 → highlight_card/insight_card 가 조작 수치를
#   key_message·items 에 담으면 rows=[] → '텍스트 카드 면제'로 거짓 수치가 통과했음.
_UNIT_HINT = re.compile(r'\s*(?:%|퍼센트|원|억|조|만|천|배|명|건|개|포인트|달러|위|㎡|㎞|kg|톤|년|월|일)')
_TEXT_FIELDS = ("title", "subtitle", "key_message", "text", "caption", "summary", "headline")


def _text_data_numbers(spec: dict) -> list[float]:
    """텍스트 필드에서 '데이터성 수치' 추출. 단순 개수·서수(작은 정수)는 제외 —
    단위 동반 / 소수 / 3자리+ 만 데이터 주장으로 간주 (오탐 최소화)."""
    parts: list[str] = []
    for k in _TEXT_FIELDS:
        v = spec.get(k)
        if isinstance(v, str):
            parts.append(v)
    items = spec.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, str):
                parts.append(it)
            elif isinstance(it, dict):
                parts.extend(str(vv) for vv in it.values() if isinstance(vv, (str, int, float)))
    text = " ".join(parts)
    nums: list[float] = []
    for m in re.finditer(r'-?\d[\d,]*(?:\.\d+)?', text):
        raw = m.group(0)
        val = _to_float(raw)
        if val is None:
            continue
        has_unit = bool(_UNIT_HINT.match(text[m.end():m.end() + 4]))
        is_dataish = abs(val) >= 100 or ('.' in raw)
        if has_unit or is_dataish:
            nums.append(val)
    return nums


def _spec_numeric_rows(spec: dict) -> list[tuple[int, str, float]]:
    """spec["data"] 에서 (index, label, value) 수치 행 추출. value/before/after 모두 검사."""
    out = []
    for i, d in enumerate(spec.get("data") or []):
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        for key in ("value", "after", "before"):
            v = _to_float(d.get(key))
            if v is not None:
                out.append((i, label, v))
                break
    return out


def _all_dataset_rows(datasets: list[dict]) -> list[tuple[str, float, dict]]:
    """모든 dataset 의 (label, value, source) 평탄화."""
    rows = []
    for ds in datasets or []:
        src = ds.get("source") or {}
        for r in ds.get("data") or []:
            v = _to_float(r.get("value"))
            if v is not None:
                rows.append((str(r.get("label", "")).strip(), v, src))
    return rows


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", (s or "").lower()))


def _value_match(v: float, dv: float) -> bool:
    from JARVIS09_COLLECTOR.models import grounds   # ★ Step 8 단일 tolerance (올림/내림 or ±5%)
    if dv == 0:
        return abs(v) <= _ABS_TOL
    return grounds(v, dv)


def _match_row(label: str, value: float, dataset_rows) -> tuple[dict, float] | tuple[None, None]:
    """spec 행이 실데이터 행과 (값 근접 + 라벨 호환) 일치하면 (source, 실데이터값) 반환.
    ★ 1-b (2026-07-02): 실데이터 값(dv)도 반환 → 재구성 시 LLM 근사값 대신 실값 표시."""
    lt = _tokens(label)
    # 1차: 값 근접 + 라벨 토큰 겹침
    for dlabel, dv, src in dataset_rows:
        if _value_match(value, dv) and (lt & _tokens(dlabel)):
            return src, dv
    # 2차: 라벨 정보가 빈약할 때 값만 근접해도 인정 (실데이터 풀 안의 값)
    if not lt:
        for dlabel, dv, src in dataset_rows:
            if _value_match(value, dv):
                return src, dv
    return None, None


def has_provenance(spec: dict) -> bool:
    """spec 이 검증된 출처를 가지는지 (또는 수치 없는 텍스트 카드인지)."""
    if not isinstance(spec, dict):
        return False
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return True
    # 수치가 전혀 없으면 텍스트 카드 — 사실성 검증 대상 아님 (출처 불필요).
    # ★ 1-a: data 배열뿐 아니라 텍스트 필드(제목·key_message·items)의 데이터성 수치도
    #   없어야 진짜 텍스트 카드. 텍스트에 수치가 있으면 검증 필요 → provenance 없음(False).
    return not _spec_numeric_rows(spec) and not _text_data_numbers(spec)


def spec_chart_values(spec: dict) -> list[dict]:
    """차트 spec → 본문↔차트 교차대조용 라벨드 수치 [{label,value,unit}].

    ★ 2-4 (2026-07-02): provenance 레지스트리에 이 값을 박제해 두면 prepublish_gate
      _crosscheck_leg 가 '본문의 같은 지표 수치'와 대조할 수 있다. data 배열의 *명시
      라벨 행* 만 추출 — 텍스트필드(제목·key_message) 수치는 라벨 불명확 → 오탐 유발이라 제외.
    """
    if not isinstance(spec, dict):
        return []
    default_unit = str(spec.get("unit", "")).strip()
    out: list[dict] = []
    for d in spec.get("data") or []:
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        v = _to_float(d.get("value"))
        if label and v is not None:
            out.append({"label": label, "value": v,
                        "unit": str(d.get("unit", "") or default_unit).strip()})
    return out


def source_caption(source: dict) -> str:
    """출처 dict → 이미지에 박을 한 줄 캡션."""
    if not source:
        return ""
    name = source.get("name") or source.get("provider") or ""
    as_of = source.get("as_of") or ""
    if name and as_of:
        return f"출처: {name} ({as_of})"
    return f"출처: {name}" if name else ""


def _dataset_to_spec(dataset: dict, base: dict) -> dict:
    """실데이터 dataset → 렌더 가능한 spec (대체용)."""
    src = dataset.get("source") or {}
    return {
        "viz_type": dataset.get("viz_hint") or "bar_chart",
        "title": dataset.get("title") or base.get("title", ""),
        "subtitle": base.get("subtitle", ""),
        "unit": dataset.get("unit", ""),
        "data": [{"label": r["label"], "value": r["value"],
                  "unit": dataset.get("unit", "")} for r in dataset.get("data", [])],
        "color_theme": base.get("color_theme", "blue"),
        "source": source_caption(src),
        "keyword": base.get("keyword", ""),
        "sector": base.get("sector", ""),
        "_provenance": {"verified": True, "source": src, "method": "dataset_substitution"},
    }


def verify_chart_spec(spec: dict, datasets: list[dict] | None):
    """차트 spec 의 데이터 사실성 검증. 검증분 재구성 / 대체 / 스킵.

    Returns:
        - 검증/재구성된 spec (텍스트 카드는 그대로)
        - None  → 수치 차트인데 실데이터 뒷받침 0 → 호출자가 차트 스킵해야 함
    """
    if not isinstance(spec, dict):
        return spec

    rows = _spec_numeric_rows(spec)
    text_nums = _text_data_numbers(spec)   # ★ 1-a: 텍스트 필드 수치도 검증 대상

    # ① 진짜 텍스트 카드 (data 행·텍스트 데이터성 수치 모두 없음) — 검증 면제
    if not rows and not text_nums:
        return spec

    # ② 이미 dataset 기반(검증됨) — 통과
    prov = spec.get("_provenance") or {}
    if prov.get("verified"):
        return spec

    datasets = datasets or []
    dataset_rows = _all_dataset_rows(datasets)
    viz = spec.get("viz_type", "")
    min_rows_n = min_rows(viz)

    # ★ 1-a: data 행이 없는 '텍스트 카드'인데 텍스트 필드에 데이터성 수치가 있는 경우.
    #   그 수치가 전부 실데이터로 뒷받침되면 통과, 하나라도 미검증이면 대체/스킵(거짓<없음).
    if not rows and text_nums:
        unbacked = [v for v in text_nums
                    if not any(_value_match(v, dv) for _, dv, _ in dataset_rows)]
        if not unbacked:
            return spec
        if datasets:
            chosen = next((d for d in datasets if d.get("viz_hint") == viz), datasets[0])
            log.info(f"[dataverify] 텍스트 수치 미검증({len(unbacked)}개) → 실데이터 대체: "
                     f"'{chosen.get('title','')}'")
            return _dataset_to_spec(chosen, spec)
        log.warning(f"[dataverify] 🚫 텍스트 카드 '{spec.get('title','')}' 미검증 수치 "
                    f"{unbacked[:3]} — 실데이터 없음 → 스킵(거짓 데이터 방지)")
        return None

    # ③ LLM 본문 추출 수치(data 행) — 행별 대조
    verified: list[tuple[int, float]] = []   # (idx, 실데이터값)
    matched_src: dict | None = None
    for idx, label, value in rows:
        src, dv = _match_row(label, value, dataset_rows)
        if src is not None:
            verified.append((idx, dv))
            matched_src = matched_src or src

    if len(verified) >= min_rows_n:
        # ★ 1-b: 검증된 행만 남기되 값은 실데이터 값(dv)으로 치환 — LLM 근사값 금지
        real_val = {i: dv for i, dv in verified}
        new_data = []
        for i, d in enumerate(spec.get("data") or []):
            if i in real_val:
                d2 = dict(d) if isinstance(d, dict) else {"label": "", "value": real_val[i]}
                d2["value"] = real_val[i]
                new_data.append(d2)
        spec["data"] = new_data
        spec["_provenance"] = {"verified": True, "source": matched_src or {},
                               "method": "verified_subset"}
        cap = source_caption(matched_src or {})
        if cap and not spec.get("source"):
            spec["source"] = cap
        log.info(f"[dataverify] 검증 통과(부분) {len(new_data)}/{len(rows)}행 "
                 f"viz={viz} title='{spec.get('title','')}' (실데이터값 치환)")
        return spec

    # 0개 검증 — 관련 실데이터로 대체 시도
    if datasets:
        # 본문이 원한 viz_type 과 가까운 dataset 우선, 없으면 첫 dataset
        chosen = next((d for d in datasets if d.get("viz_hint") == viz), datasets[0])
        log.info(f"[dataverify] 본문 수치 미검증 → 실데이터 dataset 으로 대체: "
                 f"'{chosen.get('title','')}'")
        return _dataset_to_spec(chosen, spec)

    # 실데이터도 없음 — 거짓 차트 방지, 스킵
    log.warning(f"[dataverify] 🚫 수치 차트 '{spec.get('title','')}' 실데이터 뒷받침 0 → 스킵")
    return None


# ══════════════════════════════════════════════════════════════════════════
# ★ 렌더 계층 사실성 판정 (사용자 박제 2026-08-10 — D05·D06·D10·D13·D21)
#
#   종전엔 ① 차트형 적합성 ② 가산성 ③ 렌더 HTML grounding 이 각각
#   slot_renderer·infographic_engine·(없음) 에 흩어져 있었고, *정작 렌더하는 코드* 는
#   그중 어느 것도 보지 못했다. CLAUDE.md 규정13 "차트 데이터 사실성 로직은 이 파일 단독"
#   에 따라 전부 이리로 모은다. 이관된 원본(_dg_allowed·_dg_verify_html·_verify_dataset)은
#   shim 도 남기지 않고 삭제한다 — 사본을 남기면 다음 사람이 그쪽을 고친다.
# ══════════════════════════════════════════════════════════════════════════

def _num_rows(ds: dict, rows: list[dict] | None = None) -> list[dict]:
    """수치 행만. rows 가 주어지면 그것을(=화면에 실제 그려지는 행) 우선한다."""
    src = rows if rows is not None else (ds.get("data") or [])
    out = []
    for r in src or []:
        if not isinstance(r, dict):
            continue
        v = _to_float(r.get("value"))
        if v is not None:
            out.append({**r, "_v": v})
    return out


_LABEL_SUFFIX_RE = re.compile(r"\s*\(\d+\)\s*$")          # 종전 '(2)','(3)' 접미
_LABEL_DISAMBIG_RE = re.compile(r"\s*(?:·|·)\s*.+$")  # '기준금리 · KOFIA' 구분자
_LABEL_ASOF_RE = re.compile(r"\s+\d{4}[.\-]\d{1,2}(?:[.\-]\d{1,2})?\s*$")


def _base_label(lb) -> str:
    """구분 차원(접미 번호·시점·출처)을 벗긴 '지표 이름'.
    ★ 같은 지표의 재등장을 세기 위한 것 — 어휘 목록이 아니라 *꼴* 로만 판정한다."""
    t = str(lb or "").strip()
    t = _LABEL_SUFFIX_RE.sub("", t)
    t = _LABEL_ASOF_RE.sub("", t)
    t = _LABEL_DISAMBIG_RE.sub("", t)
    return t.strip()


def _base_kind(lb) -> str:
    """이 행이 말하는 '지표 이름'. 라벨 전체가 시간이면 지표명이 없다(빈 문자열).

    ★ 시간축 라벨('2023-08')은 지표명이 아니라 *좌표* 다. 좌표를 지표명으로 세면
      시계열 16행이 '16종 항목' 으로 읽혀 시계열 판정이 뒤집힌다.
    """
    try:
        from JARVIS06_IMAGE.image_spec import is_time_only_label
    except Exception:
        is_time_only_label = lambda _x: False        # noqa: E731
    return "" if is_time_only_label(lb) else _base_label(lb)


def series_shape(ds: dict, *, rows: list[dict] | None = None) -> dict:
    """행 집합의 *꼴* 단일 파생 — 시계열·가산성·차트형 판정이 같은 사실을 본다.

    ★ 사용자 박제 2026-08-10 (①단일 진입점): 종전엔 `additive_total` 만 mixed_time·
      duplicate_label 을 알고 있었고 `chart_fit` 의 line 분기는 그 지식을 못 봤다.
      같은 지식이 한쪽에만 있으면 다른 쪽에서 그대로 재발한다 — 실제로 재발했다
      (8개 이종 금리를 꺾은선으로 이어 존재하지 않는 -9.1% 추세를 발명).

    반환 키
      n              수치 행 수
      timed          시점(as_of|시간전용 라벨)이 파악된 행 수
      distinct_time  서로 다른 시점 수
      base_kinds     지표 이름 종류 수 (시간 라벨은 이름 없음으로 셈)
      duplicate_label 같은 지표가 두 번 이상 등장하는가
      mixed_time     기준일이 섞였는가 (row_provenance 파생)
    """
    rws = _num_rows(ds, rows)
    try:
        from JARVIS06_IMAGE.image_spec import row_time_key
    except Exception:
        row_time_key = lambda _r: None               # noqa: E731
    keys = [row_time_key(r) for r in rws]
    timed = [k for k in keys if k is not None]
    bases = [_base_kind(r.get("label")) for r in rws]
    prov = row_provenance(ds, rows=rws)
    return {"n": len(rws), "timed": len(timed), "distinct_time": len(set(timed)),
            "base_kinds": len(set(bases)), "duplicate_label": len(set(bases)) < len(bases),
            "mixed_time": bool(prov["mixed_time"]), "as_of_range": prov["as_of_range"]}


def is_timeseries(ds: dict, *, rows: list[dict] | None = None) -> bool:
    """이 행들이 '한 지표의 시점별 값' 인가 — 선(線)을 그어도 되는 유일한 조건.

    ★ 판정 근거는 **행 메타(as_of)** 지 라벨 문자열이 아니다 (사용자 박제 2026-08-10).
      종전 규칙은 '라벨에 \\d{4}|\\d+일... 이 60% 이상' 이었다. 그래서
        · '콜금리(1일)'·'통안증권 91일' 이 시간으로 오탐되고
        · 1차 수정이 라벨에 as_of 를 심자 판정이 통째로 뒤집혔다(금리 2/8 → 5/8 → 시계열).
      진실은 행에 실려 있다. 라벨은 표시 산출물이므로 판정 입력이 될 수 없다.

    조건 (셋 다):
      ① 모든 행의 시점이 파악된다   ② 시점이 서로 다르다   ③ 지표 이름이 1종이다
    """
    sh = series_shape(ds, rows=rows)
    n = sh["n"]
    if n < min_rows("line_chart"):
        return False
    return sh["timed"] == n and sh["distinct_time"] == n and sh["base_kinds"] == 1


def min_rows(viz: str) -> int:
    """차트형이 성립하는 최소 행 수."""
    return MIN_ROWS.get(str(viz or "").strip().lower(), MIN_ROWS_DEFAULT)


def row_provenance(ds: dict, *, rows: list[dict] | None = None) -> dict:
    """행별 as_of·source 집계 → {as_of_range, sources, mixed_source, mixed_time}.

    우선순위 (사본을 믿지 않는다): ds["as_of_range"]/ds["source_mix"] → 행의 as_of/source
    → ds["source"](구버전 dataset 하위호환).
    """
    rws = _num_rows(ds, rows)
    out = {"as_of_range": {"min": "", "max": "", "distinct": 0},
           "sources": [], "mixed_source": False, "mixed_time": False}

    # ① dataset 레벨 파생본이 있으면 그것을 쓴다 (J09 가 행에서 파생해 실어 보낸 것)
    rng = ds.get("as_of_range") if isinstance(ds.get("as_of_range"), dict) else None
    mix = ds.get("source_mix") if isinstance(ds.get("source_mix"), list) else None

    if rng is None:
        aos = [str(r.get("as_of") or "").strip() for r in rws]
        aos = [a for a in aos if a]
        if not aos:
            ao = str((ds.get("source") or {}).get("as_of", "")).strip()
            aos = [ao] if ao else []
        uniq = sorted(set(aos))
        rng = {"min": uniq[0] if uniq else "", "max": uniq[-1] if uniq else "",
               "distinct": len(uniq)}
    out["as_of_range"] = {"min": str(rng.get("min", "")), "max": str(rng.get("max", "")),
                          "distinct": int(rng.get("distinct", 0) or 0)}

    if mix is None:
        agg: dict[tuple, dict] = {}
        for r in rws:
            sc = r.get("source")
            if not isinstance(sc, dict):
                continue
            key = (str(sc.get("provider", "")), str(sc.get("name", "")))
            e = agg.setdefault(key, {"provider": key[0], "name": key[1],
                                     "url": str(sc.get("url", "")),
                                     "tier": sc.get("tier"), "count": 0})
            e["count"] += 1
        mix = sorted(agg.values(), key=lambda e: -e["count"])
        if not mix:
            sc = ds.get("source") or {}
            if sc:
                mix = [{"provider": str(sc.get("provider", "")), "name": str(sc.get("name", "")),
                        "url": str(sc.get("url", "")), "tier": sc.get("tier"), "count": len(rws)}]
    out["sources"] = list(mix or [])
    out["mixed_source"] = len(out["sources"]) > 1
    out["mixed_time"] = bool(ds.get("mixed_time")) or out["as_of_range"]["distinct"] > 1
    return out


def additive_total(ds: dict, *, rows: list[dict] | None = None) -> tuple[float | None, str]:
    """이 행들을 더한 값을 표시해도 되는가. 반환 (표시할 합계값|None, 사유코드).

    ★ 기본값은 '불가' (사용자 박제 2026-08-10 — D03·D06).
      합계 카드는 장식이고 거짓 합계는 손해다. 비대칭이 분명하므로 입증 책임을 합계에 지운다.
      단위 화이트리스트·블랙리스트를 만들지 않는다(②동적 설계) — *꼴* 과 *데이터에 실린 증거*
      로만 판정한다. 유일한 적극 증거는 `ds["totals"]`(출처가 스스로 공표한 합계)다.

    2026-08-10 실측 적용: 금리 27.2% → blocked:duplicate_label /
      환율 4,368원 → blocked:mixed_time / 지수 88,485pt·상장요건 1,550억원 →
      blocked:no_published_total / 고용 341,000명 → blocked:mixed_basis(전망+실적).
    """
    rws = _num_rows(ds, rows)
    if len(rws) < 2:
        return (None, "blocked:too_few_rows")
    # ★ 꼴 판정은 series_shape 단독 파생 — chart_fit 과 *같은 사실* 을 본다(①단일 진입점)
    sh = series_shape(ds, rows=rws)
    if is_timeseries(ds, rows=rws):
        return (None, "blocked:timeseries")           # 같은 양의 시점별 값 — 합은 존재하지 않는 양
    if sh["mixed_time"]:
        return (None, "blocked:mixed_time")           # 기준일이 섞였다 = 시계열의 카테고리 위장
    if sh["duplicate_label"]:
        return (None, "blocked:duplicate_label")      # 같은 지표의 재등장 — 항목이 아니다

    basis = {str(r.get("basis") or "") for r in rws}
    if len(basis) > 1 or "" in basis:
        return (None, "blocked:mixed_basis")          # 실적+전망 혼합. 미상("")도 안전 실패

    cats = {str(r.get("category") or "") for r in rws if r.get("category")}
    if len(cats) > 1:
        return (None, "blocked:mixed_category")

    unit = str(ds.get("unit") or "").strip()
    # ★ 꼴 판정: 'USD/배럴'·'원/달러'(무엇당 무엇)·'2020=100'(기준=100) 은 강도량·지수라
    #   합이 정의되지 않는다. 어휘 목록이 아니라 *구분자의 존재* 로 본다. 단위 미상도 불가.
    if (not unit) or ("/" in unit) or ("=" in unit):
        return (None, "blocked:intensive_unit")

    vals = [r["_v"] for r in rws]
    if unit.endswith("%") and abs(sum(vals) - 100) >= 15:
        return (None, "blocked:ratio_not_composition")   # 구성비가 아닌 비율의 합은 무의미

    tot = (ds.get("totals") or {}).get("value") if isinstance(ds.get("totals"), dict) else None
    tot = _to_float(tot)
    if tot is None:
        return (None, "blocked:no_published_total")   # 부분들의 전체를 아무도 말한 적 없다
    return (float(tot), "published_total")


def chart_fit(ds: dict, *, rows: list[dict] | None = None) -> str:
    """데이터 형태 → 표현 형태. "kpi_cards"|"line_chart"|"donut"|"bar_chart"|"none".

    ★ 차트형 판정의 유일한 소유자 (사용자 박제 2026-08-10 — D13·D21).
      `viz_hint` 는 *입력 힌트* 지 결정이 아니다. 종전엔 상류(slot_renderer)가 이미
      "단일값 → kpi_cards" 로 판정해 실어 보냈는데 렌더러가 그걸 버리고 막대를 그렸고,
      1행 막대는 v/vmax 정규화 때문에 트랙 100% 를 채워 정보량이 정확히 0이었다.
    """
    rws = _num_rows(ds, rows)
    n = len(rws)
    if n == 0:
        return "none"
    if n < min_rows("bar_chart"):
        return "kpi_cards"
    if is_timeseries(ds, rows=rws):
        return "line_chart"
    vh = str(ds.get("viz_hint") or "").lower()
    if "pie" in vh or "donut" in vh:
        return "donut"                                 # J09 의 pie 의도는 최우선 존중
    # ★ `viz_hint="line_chart"` 우회 삭제 (사용자 박제 2026-08-10 — 신규거짓 #1).
    #   선은 "시간이 흐르며 이 값이 이렇게 변했다" 는 주장이다. 행이 시계열 꼴이 아닌데
    #   힌트만 보고 선을 그으면 *없는 추세를 발명* 한다. 힌트는 입력이지 결정이 아니다.
    #   (시계열이면 위 is_timeseries 에서 이미 line_chart 로 나갔다)
    unit = str(ds.get("unit") or "").strip()
    vals = [r["_v"] for r in rws]
    if unit.endswith("%") and 2 <= n <= 6 and abs(sum(vals) - 100) < 15:
        return "donut"                                 # 구성비 → 도넛
    return "bar_chart"


# ── 출처 등급 ─────────────────────────────────────────────────────────────
# ★ JARVIS06 이 *스스로* 만들어 붙이는 producer 표식 — J09 출처 이름이 아니다.
#   (`slot_renderer.render_slot` = 대본 내장 슬롯, `render_slots` = J09 번들 폴백)
#   출처가 아니라 "이 데이터가 어느 문으로 들어왔는가" 의 표식이라 레지스트리에 없다.
_J06_SELF_PROVIDERS = frozenset({"draft_slot", "jarvis09"})


def _trusted_providers() -> frozenset[str]:
    """차트 승격을 허용하는 provider 토큰 — **J09 출처 레지스트리에서 런타임 파생**.

    ★ 사본 금지 (사용자 박제 2026-08-10 최종리뷰 #2): 종전엔 이 자리에 provider 이름
      10개가 리터럴 집합으로 박혀 있었고 *이미 드리프트해 있었다* —
        · 레지스트리 키는 `bok_official`·`finance` 인데 사본은 `bok`·`yfinance`,
        · 사본의 `market` 은 어디서도 발행되지 않는 죽은 토큰,
        · 뒤에 늘어난 `kofia`·`customs`·`fss`·`mlit`·`employment`·`naver_news`·`news`·
          `kor_econ`·`blog`·`discover` 는 사본에 영영 반영되지 않았다.
      출처가 무엇인지 아는 것은 그 출처를 수집하는 09 뿐이다(②동적 설계).
      이제 `SOURCES` 를 매 호출 조회한다 — 소스가 늘거나 빠지면 여기가 자동으로 따라온다.
      레지스트리를 못 읽으면 빈 집합 = fail-closed(티어·http URL 로만 판정).
    """
    toks = set(_J06_SELF_PROVIDERS)
    try:
        from JARVIS09_COLLECTOR.source_registry import SOURCES
    except Exception:
        return frozenset(toks)
    for spec in SOURCES:
        toks.add(spec.key)
        mod = (spec.provider or "").split(":", 1)[0]
        if mod.endswith("_provider"):
            toks.add(mod[: -len("_provider")])   # finance_provider → finance
    return frozenset(toks)


def _ds_tier(ds: dict) -> int | None:
    """dataset 의 출처 신뢰 티어. 없으면 provider 접미(evidence:<type>)에서 파생."""
    src = ds.get("source") or {}
    t = src.get("tier")
    if isinstance(t, (int, float)):
        return int(t)
    prov = str(src.get("provider", "")).lower().strip()
    stype = prov.split(":", 1)[1].strip() if ":" in prov else ""
    if not stype:
        return None
    try:
        from JARVIS09_COLLECTOR.models import trust_rank
        return int(trust_rank(stype))
    except Exception:
        return None


def dataset_admissible(ds: dict, *, category: str = "") -> tuple[bool, str]:
    """이 dataset 을 차트로 승격해도 되는가. (통과여부, 사유코드).

    ★ 종전 `_verify_dataset` 이관·강화 (사용자 박제 2026-08-10 — D05).
      옛 판정은 `_TRUSTED_PROVIDER_PREFIXES = {"evidence"}` 였는데 J09 가 *모든* fact 유래
      dataset 에 `provider="evidence:<type>"` 를 붙이므로 사실상 상수 True 였다 —
      신문 사설도 한국은행 API 와 동등하게 통과했다. 이제 *등급* 으로 본다:
      티어는 `SOURCE_TRUST_TIER`(J09 SSOT)에서 파생하고, 상한은 CATEGORY_POLICY 노브에서
      읽는다. 노브가 없는 구성에서는 티어 검사를 하지 않아 하위호환을 유지한다.
    """
    rws = _num_rows(ds)
    if not rws:
        return (False, "no_numeric_row")
    src = ds.get("source") or {}
    prov = str(src.get("provider", "")).lower().strip()
    url = str(src.get("url", "")).strip()
    tier = _ds_tier(ds)
    if not (prov in _trusted_providers() or tier is not None or url.startswith("http")):
        return (False, "no_source")

    try:
        from JARVIS09_COLLECTOR.models import policy_for
        pol = policy_for(category)
    except Exception:
        pol = {}
    cap = pol.get("chart_max_source_tier")
    if isinstance(cap, int) and tier is not None and tier > cap:
        return (False, f"source_tier:{tier}>{cap}")
    vb_above = pol.get("chart_verbatim_above_tier")
    if isinstance(vb_above, int) and tier is not None and tier > vb_above:
        # 이 등급의 출처는 *원문 대조를 통과한 행* 만 차트가 된다 (J09 신규능력1)
        if all(r.get("verbatim") is False for r in rws):
            return (False, "verbatim_failed")
    return (True, "ok")


# ── 렌더 HTML grounding ───────────────────────────────────────────────────
# ★ 수치 토큰의 *꼴* 은 `shared/numeric.py` 단독 소유 (사용자 박제 2026-08-10).
#   종전엔 여기와 `JARVIS09_COLLECTOR/evidence_pack.py` 에 `_NUM_TOKEN_RE` 라는
#   **같은 이름의 다른 정규식** 이 있었고, 한국어 자릿수('19만8900')를 J09 만 처리했다.
#   이름이 같으니 두 벌인 줄 아무도 모른 채 J06 은 그 표기를 19 와 8900 으로 읽었다.


def _given_texts(datasets: list[dict] | None, rendered_rows: list[dict] | None) -> list[str]:
    """데이터가 *준* 문자열 전량 (행 라벨·제목·단위·기준시점·크롬 텍스트). 걸러내지 않는다.

    ★ 라벨은 '데이터 주장' 이 아니라 이름이다 (사용자 박제 2026-08-10):
      '통안증권 91일'·'국고채 3년'·'2017'·'06.15'·'물가 지표 (2020=100)'.
      종전 게이트는 이것들을 `0<=int(n)<=100` 이라는 **무조건 통과 구멍** 으로 덮었고,
      그 구멍 하나로 임의 수치의 대부분이 근거 없이 통과했다.
      어휘 목록('년'·'일'…)을 박는 대신 *데이터가 실제로 준 문자열* 을 모은다 —
      목록이 아니라 런타임 파생이므로 새 라벨이 생겨도 자동으로 따라온다.

    여기서 미리 걸러내지 않는다 — 종전엔 순수 숫자 라벨('2017'·'06.15')이 이 함수 안에서
    버려졌고, 그 라벨을 인쇄한 축·연도 차트가 **캐시 5개에서 100% 폐기**됐다(실측 오탐).
    무엇을 어떻게 지울지는 `_erasers` 가 정한다.
    """
    out: set[str] = set()
    for ds in datasets or []:
        if not isinstance(ds, dict):
            continue
        out.add(str(ds.get("title") or ""))
        out.add(str(ds.get("unit") or ""))
        for r in (ds.get("data") or []):
            if isinstance(r, dict):
                out.add(str(r.get("label") or ""))
                out.add(str(r.get("as_of") or ""))
        prov = row_provenance(ds)
        out.update([str(prov["as_of_range"]["min"] or ""), str(prov["as_of_range"]["max"] or "")])
        out.add(str((ds.get("source") or {}).get("as_of") or ""))
    for r in rendered_rows or []:
        if isinstance(r, dict):
            out.add(str(r.get("label") or ""))
            out.add(str(r.get("as_of") or ""))
    # ★ 크롬 텍스트(기준일 배지·출처 푸터)는 *그것을 만든 함수에게 물어서* 얻는다.
    #   여기서 같은 문자열을 다시 조립하면 그게 곧 사본이고, 표기가 한쪽만 바뀌는 날
    #   ('2026년 7월'→'2026년 7' 절단) 멀쩡한 차트가 통째로 폐기된다 — 실측된 오탐이다.
    try:
        from JARVIS06_IMAGE.template_engine import _eyebrow_from_data, source_label
        out.add(str(_eyebrow_from_data(datasets or []) or ""))
        out.add(str(source_label(datasets or []) or ""))
    except Exception:
        pass
    return sorted({t.strip() for t in out if t.strip()}, key=len, reverse=True)


def _erasers(given: list[str]) -> list:
    """데이터가 준 문자열을 화면 텍스트에서 지우는 정규식 (긴 것부터, 컴파일 1회).

    ★ 경계를 물린 채 지운다 (사용자 박제 2026-08-10 3차). 종전엔 순수 숫자꼴 라벨
      ('2017'·'06.15')을 **아예 지우지 않았다** — 단순 부분 문자열 치환이라 라벨 '1' 이
      값 '8,161' 을 '8,6' 으로 부수고 없던 수 86 을 만들기 때문이었다. 그 대가로 축 눈금과
      연도 라벨이 전부 미근거 수치로 잡혀, 경제 브리핑 핵심 차트 3종(코스피·S&P500·
      달러/원)이 캐시 5개에서 **100% 폐기**됐다(실측 4.84%의 대부분).
      경계(`앞뒤가 숫자·소수점·자릿쉼표가 아닐 것`)를 물리면 부수기가 원천적으로 불가능해
      숫자꼴 라벨도 안전하게 지운다.
    ★ 값이 아니라 *그 문자열* 을 지운다는 점이 중요하다 — 라벨 '06.15' 를 지워도
      조작 수치 '6.15' 는 그대로 남아 검사를 받는다. (라벨의 *값* 을 근거군에 넣는 방식은
      같은 오탐을 없애지만 '6.15' 를 어디서든 통과시킨다 — 실측 검출률 -0.84%p.)
    """
    return [re.compile(r"(?<![\d.,])" + re.escape(g) + r"(?![\d.,])") for g in given]


def grounding_pool(datasets: list[dict], *,
                   rendered_rows: list[dict] | None = None) -> tuple[set, list]:
    """표시 수치 대조군(원본값 + *렌더러가 실제로 파생하는* 값) 과 원본값 리스트.

    ★ 원칙 (사용자 박제 2026-08-10): 허용값은 **렌더러의 파생 함수를 그대로 불러서** 만든다.
      "이 범위면 통과" 같은 *추측* 을 두지 않는다 — 추측은 곧 구멍이고, 실제로
      `dmin*0.7 ~ dmax*1.3` 하나 때문에 임의 수치의 80%가 통과했다.
      · 축 눈금  → `pro_templates.axis_ticks`   (선차트가 인쇄하는 바로 그 3개)
      · 표시 스케일 → `pro_templates._scale_rows_uniform` / `_auto_scale`
      · 최상위/차순위 배율 → `pro_templates.outlier_ratio`
      · 공표 합계 → `additive_total`  (계산된 합은 넣지 않는다)
    ★ 라벨·기준시점 좌표는 여기 넣지 않는다 — 근거군을 넓히면 그 값이 *어디서든* 통과한다.
      좌표는 `_erasers` 가 화면 텍스트에서 *그 문자열 그대로* 지운다(위치가 곧 근거).
    ★ 삭제분: 모든 쌍의 `abs(a-b)` (O(n²) 로 허용값을 폭증시키던 구멍) — 렌더러가
      인쇄하는 차이는 `nums[-1]-nums[0]` 하나뿐이다.
    """
    vals, raw = set(), []
    try:
        from JARVIS06_IMAGE.pro_templates import (_scale_rows_uniform, _auto_scale,
                                                  axis_ticks, outlier_ratio)
    except Exception:
        _scale_rows_uniform = _auto_scale = axis_ticks = outlier_ratio = None
    try:
        from JARVIS06_IMAGE.image_spec import enforce_time_axis_ltr as _ltr
    except Exception:
        _ltr = None
    ds_list = [d for d in (datasets or []) if isinstance(d, dict)]
    for ds in ds_list:
        rws = _num_rows(ds)
        ts = is_timeseries(ds, rows=rws)
        # 시계열은 *그리는 순서* 로 세운 뒤 첫/끝·증감률을 파생한다 (정렬 owner 재사용).
        if ts and _ltr is not None:
            try:
                rws = _num_rows(ds, _ltr([dict(r) for r in rws]))
            except Exception:
                pass
        nums = [r["_v"] for r in rws]
        raw += nums
        if not nums:
            continue
        vals.update(nums)
        vals.update([min(nums), max(nums)])            # 히어로 '최고'·'최저'
        tot, _why = additive_total(ds)
        if tot is not None:
            vals.add(float(tot))                       # 출처가 공표한 합계
        unit = ds.get("unit", "")
        scaled = list(nums)
        if _scale_rows_uniform is not None:
            try:
                srows, _u = _scale_rows_uniform([(str(i), v) for i, v in enumerate(nums)], unit)
                scaled = [float(v) for _, v in srows]
                vals.update(scaled)
                for a in nums:
                    sv, _su = _auto_scale(a, unit)
                    vals.add(float(sv))
            except Exception:
                scaled = list(nums)
        # ★ 차트형별로 *그 차트가 실제로 인쇄하는 것만* 넣는다 (사용자 박제 2026-08-10).
        #   종전엔 평균·첫값·끝값·끝-첫 차이를 차트형과 무관하게 전부 넣었다. 막대 차트는
        #   '끝값 − 첫값' 을 인쇄하지 않는데도 그 값이 허용군에 들어가 있어서, 실측에서
        #   조작 수치 48,213.7 이 증시 막대의 (54,036.93 − 7,757.64) = 46,279.29 ±5% 에
        #   걸려 통과했다. 허용군은 '있을 법한 수' 가 아니라 '인쇄되는 수' 여야 한다.
        if ts:
            vals.update([nums[0], nums[-1], nums[-1] - nums[0]])   # 선차트 끝점 배지·히어로 델타
            if nums[0]:
                vals.add((nums[-1] - nums[0]) / abs(nums[0]) * 100.0)
            if axis_ticks is not None:
                try:
                    vals.update(axis_ticks(nums))
                    vals.update(axis_ticks(scaled))                # 선차트 Y축 눈금
                except Exception:
                    pass
        elif outlier_ratio is not None:
            try:
                for _vs in (nums, scaled):
                    _r = outlier_ratio(_vs)
                    if _r is not None:
                        vals.add(float(_r))                        # 분리형 막대 최상위/차순위 배율
            except Exception:
                pass
    # 블록 번호칩(1·2·3…) — 그린 블록 수에서 파생. 리터럴 상한을 두지 않는다.
    vals.update(float(i) for i in range(0, len(ds_list) + 1))
    if rendered_rows is not None:
        vals.add(float(len(rendered_rows)))
    return vals, raw


def verify_rendered_html(html: str, datasets: list[dict], *,
                         rendered_rows: list[dict] | None = None) -> tuple[bool, list]:
    """렌더 HTML *표시 텍스트* 수치의 grounding 검증. (ok, 미근거 수치 목록).

    좌표(attribute)는 검사 안 함 — '>텍스트<' 노드만. <style>/<script> 제외는 유지
    (팔레트 hex 가 데이터로 오인되면 전 템플릿이 폐기된다).

    ★ 삭제된 무조건 통과 3종 (사용자 박제 2026-08-10 — 실측 통과율 80% → 게이트 아님):
      ① `0<=int(n)<=100`      → 라벨 속 숫자는 `_erasers` 가 *경계를 물고 지운다*
                                 (숫자꼴 라벨 포함 — 경계가 '8,161'→'8,6' 부수기를 막는다).
      ② `dmin*0.7<=|n|<=dmax*1.3` → 축 눈금은 `axis_ticks` 로 *계산해서* 맞춘다.
      ③ 연도 2000~2100        → 날짜 토큰은 `shared.numeric` 이 토큰화 단계에서 뺀다.
    """
    from JARVIS09_COLLECTOR.models import grounds as _grounds
    from shared.numeric import display_numbers, DATE_TOKEN_RE
    allowed, raw = grounding_pool(datasets or [], rendered_rows=rendered_rows)
    scan = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", html or "", flags=re.S | re.I)
    erasers = _erasers(_given_texts(datasets, rendered_rows))
    nums: list[float] = []
    for t in re.findall(r">([^<]+)<", scan):
        # ★ 순서가 중요하다: 날짜 토큰을 *먼저* 통째로 지운다.
        #   라벨/연도 조각을 먼저 지우면 '2026.06~2026.08' 이 '.06~.08' 로 부서져
        #   6·8 이라는 없는 수치가 태어난다 (실측 오탐 61.5% 의 정체).
        t = DATE_TOKEN_RE.sub(" ", t)
        for rx in erasers:
            t = rx.sub(" ", t)
        nums.extend(display_numbers(t))
    if not nums:
        return True, []
    bad = [n for n in nums if not any(_grounds(n, a) for a in allowed)]
    if bad:
        log.warning(f"[dataverify] grounding 실패 수치 {bad[:6]} (총 {len(nums)}개 중 {len(bad)})")
    ok = len(bad) <= MAX_BAD_ABS and (len(bad) / len(nums)) <= MAX_BAD_RATIO
    return ok, bad


# ── 이미지 1장 인증 (레지스트리 쓰기 유일 경로) ────────────────────────────
_NON_DATA_KINDS = {"photo", "thumbnail", "table", "text_card"}


def certify_image(image_path, *, engine: str,
                  datasets: list[dict] | None = None,
                  rendered_html: str = "",
                  rendered_rows: list[dict] | None = None,
                  spec: dict | None = None,
                  kind: str = "",
                  code_drawn: bool = False) -> dict:
    """이미지 1장의 검증 + provenance 등록 — ★ 레지스트리에 쓰는 유일한 경로.

    반환 = 등록된 provenance dict. 호출자는 `prov["verified"] is True` 로만 채택한다.
    미검증이면 *글이 아니라 이미지를 버린다* (ADR 010: 거짓 차트 < 차트 없음).

    ★ 호출자의 주장보다 재료가 먼저다 (사용자 박제 2026-08-10):
      · `kind` 는 *힌트* 다. 재료에 수치가 있으면 비수치 종류 주장은 기각된다 —
        종전엔 `kind="table"` 한 마디로 검사 0회 통과였다.
      · '행 파싱 실패' 와 '데이터 없음' 을 구분한다. 행을 받았는데 하나도 수로 읽히지
        않으면 그것은 텍스트 카드가 아니라 *검증 불능* 이다 → fail-closed.
    """
    rows = rendered_rows if rendered_rows is not None else (
        spec_chart_values(spec) if isinstance(spec, dict) else [])

    # ── 재료에서 파생 (호출자 주장 이전) ──────────────────────────────────
    _ds = [d for d in (datasets or []) if isinstance(d, dict)]
    _declared = any((d.get("data") for d in _ds)) or bool((spec or {}).get("data")) \
        or bool(rendered_rows)
    _parsed_rows = [r for r in (rows or []) if _to_float(r.get("value")) is not None]
    _parsed = bool(_parsed_rows) or any(_num_rows(d) for d in _ds)
    _html_nums: list[float] = []
    if rendered_html:
        try:
            from shared.numeric import display_numbers
            _scan = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", rendered_html,
                           flags=re.S | re.I)
            for _t in re.findall(r">([^<]+)<", _scan):
                _html_nums.extend(display_numbers(_t))
        except Exception:
            _html_nums = []
    has_nums = bool(_parsed or _html_nums)
    parse_failed = bool(_declared and not _parsed)

    claimed = str(kind or "")
    kind = claimed or ("numeric_chart" if has_nums else "text_card")
    # 재료에 수치가 있으면 비수치 주장은 기각 — 주장으로 검사를 건너뛸 수 없다.
    if claimed in _NON_DATA_KINDS and has_nums:
        kind = "numeric_chart"

    prov: dict = {"verified": False, "kind": kind, "engine": str(engine or ""),
                  "method": "unverified_render", "source": {}, "values": [], "issues": []}
    # 대표 출처 — fingerprint 안정 식별자(파일명 금지)
    if isinstance(spec, dict) and (spec.get("_provenance") or {}).get("source"):
        prov["source"] = dict((spec.get("_provenance") or {}).get("source") or {})
    elif _ds:
        prov["source"] = dict((_ds[0].get("source") or {}))
    #   단위는 행이 들고 온 것을 우선 — 여러 dataset 이 섞이면 '첫 dataset 단위' 추측이 틀린다.
    _u0 = str((_ds[0].get("unit", "") if _ds else "") or "")
    prov["values"] = [{"label": str(r.get("label", "")),
                       "value": _to_float(r.get("value")),
                       "unit": str(r.get("unit", "") or _u0)}
                      for r in _parsed_rows]

    if parse_failed:
        # ★ fail-closed: 행을 받았는데 수로 읽히지 않았다 = 검증 불능 (데이터 없음이 아니다).
        prov["issues"].append("row_parse_failed")
    elif kind in _NON_DATA_KINDS:
        prov["verified"] = True
        prov["method"] = "non_data"
        if not (rendered_html or spec or rows or _ds):
            # 재료가 0이면 '수치가 없다' 를 확인한 것이 아니라 *못 확인한* 것이다.
            prov["issues"].append(f"kind_claim_unaudited:{kind}")
    elif isinstance(spec, dict) and spec.get("_provenance"):
        prov["verified"] = bool((spec.get("_provenance") or {}).get("verified"))
        prov["method"] = "spec_verified" if prov["verified"] else "unverified_render"
    elif rendered_html:
        ok, bad = verify_rendered_html(rendered_html, _ds, rendered_rows=rows)
        prov["verified"] = bool(ok)
        prov["method"] = "grounded" if ok else "unverified_render"
        if bad:
            prov["issues"].append(f"ungrounded:{len(bad)}")
    elif code_drawn and prov["values"]:
        # ★ 텍스트 레이어가 없는 코드 직조 차트 (matplotlib PNG 등) 전용 경로.
        #   보증 범위는 정확히 이것뿐이다 — "이 이미지의 모든 수치는 호출자가 넘긴
        #   실데이터 행을 코드가 그대로 그린 것이고, LLM 이 쓴 글자가 한 자도 없다."
        #   ★ datasets 를 함께 받은 경우엔 *주장이 아니라 대조* 로 판정한다.
        if _ds:
            from JARVIS09_COLLECTOR.models import grounds as _grounds
            pool, _raw = grounding_pool(_ds, rendered_rows=rows)
            _unmatched = [v["value"] for v in prov["values"]
                          if v["value"] is not None and not any(_grounds(v["value"], a) for a in pool)]
            prov["verified"] = not _unmatched
            prov["method"] = "code_drawn" if not _unmatched else "unverified_render"
            if _unmatched:
                prov["issues"].append(f"ungrounded:{len(_unmatched)}")
        else:
            prov["verified"] = True
            prov["method"] = "code_drawn"
            prov["issues"].append("code_drawn:unaudited")   # 대조할 datasets 이 없었다
    else:
        # ★ fail-closed: 검증 재료(spec 도 HTML 도)가 없으면 통과시키지 않는다.
        prov["issues"].append("no_verification_material")

    _record_provenance(image_path, prov)
    _observe(prov)          # 관측 — 인증이 *돌았다* 는 사실은 카운터로만 남는다
    return prov


def verifier_effective() -> bool:
    """★ patch_effective 표준 스모크 — '검증기가 실제로 동작하는가' 를 *동작으로* 확인.

    가짜 dataset 1건 + 조작 수치가 박힌 가짜 HTML 을 실제 소비자 참조로 한 번 통과시켜
    False 가 나오는지 본다. 예외·True 는 곧 '검증기 무력화' → 호출자는 fail-closed.
    (코드 존재는 적용의 증거가 아니다 — CLAUDE.md)
    """
    _lvl = log.level
    log.setLevel(logging.CRITICAL)   # 스모크는 *일부러* 틀린 값을 흘린다 — 그 경고가
    try:                             # 부팅 로그에 섞이면 진짜 위반과 구별되지 않는다.
        ds = {"title": "스모크", "unit": "억원",
              "data": [{"label": "A", "value": 10.0}, {"label": "B", "value": 20.0}]}
        ok, bad = verify_rendered_html(
            "<div><span>777777.7</span><span>888888.8</span><span>999999.9</span></div>",
            [ds])
        if ok or not bad:
            return False
        ok2, _ = verify_rendered_html("<div><span>10</span><span>20</span></div>", [ds])
        if not ok2:
            return False
        # 가산성: 공표 합계가 없으면 합계를 내주지 않아야 한다
        if additive_total(ds)[0] is not None:
            return False
        return chart_fit({"data": [{"label": "A", "value": 1}]}) == "kpi_cards"
    except Exception as e:      # noqa: BLE001 — 예외 자체가 '무력화' 신호
        log.setLevel(_lvl)
        log.warning(f"[dataverify] verifier_effective 예외 → 무력 판정: {e}")
        return False
    finally:
        log.setLevel(_lvl)


__all__ = ["verify_chart_spec", "has_provenance", "source_caption",
           "lookup_provenance", "spec_chart_values",
           "min_rows", "MIN_ROWS", "MIN_ROWS_DEFAULT", "DATA_IMAGE_ATTR",
           "chart_fit", "additive_total", "row_provenance", "dataset_admissible",
           "is_timeseries", "series_shape",
           "grounding_pool", "verify_rendered_html", "certify_image",
           "verifier_effective", "verification_stats",
           "gate_enabled", "GATE_ENV", "TELEMETRY_ENV"]
