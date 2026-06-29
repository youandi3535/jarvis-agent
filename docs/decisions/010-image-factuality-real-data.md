# 010. 이미지 사실성 — 차트 데이터는 JARVIS09 실데이터로만 (+ 무료 라이브러리 자동설치)

## 상태
확정 (2026-06-29 사용자 박제)

## 배경

대본(텍스트)의 사실성은 발행 전 게이트(`prepublish_gate`)가 검수한다(ADR 009 / Layer 3). 그러나
*이미지 안에 들어가는 수치* — 차트·인포그래픽의 숫자 — 에는 검증 절차가 없었다.

특히 `image_spec.generate_image_spec()` 은 **LLM이 본문 텍스트에서 숫자를 추출**해 차트를
만들었다. 본문에 숫자가 적거나, LLM이 오추출·반올림·환각하면 *거짓 데이터 차트*가 발행될 수
있었다. 사용자 지적:

> *"글 발행 시 대본 사실성 검증은 넣었는데, 이미지 생성으로 인한 이미지에 대한 사실성 검증은?
> 특히 데이터가 들어가는 이미지는 절대 거짓된 데이터로 만들면 안 됨."*

> *"본문에서만 숫자를 추출하면 정보가 너무 없잖아. 글 주제와 본문에 관련된 데이터를 수집해서
> 연관성 있는 차트를 만들어야지. 자비스09를 만든 이유야. 삼성전자 글이면 주가·매출·직원수·공장
> 수·협력사·영업이익 등 *관련된 데이터면 뭐든 좋다*. 그걸 JARVIS09에 요청해 웹/외부 API로 받고,
> 외부 API를 추가 설치해야 하면 (무료일 경우) 승인 없이 자동 설치해서 받아라."*

> *"이미지 생성은 무조건 JARVIS06만. 자료 수집은 무조건 JARVIS09만. 서로 협업하도록 요청하고
> 작업하고 주고받아라."*

## 결정

### 1) 단일 진입점 협업 — JARVIS09 수집 → JARVIS06 생성

ADR 001/008 의 단일 진입점 원칙을 이미지 데이터에 적용:

| 책임 | Owner | 규정 |
|------|-------|------|
| 차트용 실데이터 수집 (provenance 포함) | **JARVIS09** | `collect_chart_data(theme, sector, description)` |
| 이미지 생성 (렌더링) | **JARVIS06** | `image_spec` / `chart_generator` / 렌더러 |

JARVIS06 은 "이 주제 데이터 줘"만 요청하고, JARVIS09 가 provider 선택·수집·파싱·**출처 박제**
까지 해서 datasets 를 돌려준다. JARVIS06 은 그 실데이터로 렌더링만 한다.

`collect_chart_data` 반환 dataset 은 *반드시* `source = {provider, name, url, as_of}` 를 가진다 —
이것이 사실성 검증의 근거다.

### 2) 사실성 보증 — 검증분 재구성 → 대체 → 스킵 (사용자 선택)

`JARVIS06_IMAGE/validators/image_data_verifier.py` 가 차트 spec 의 모든 수치를 검증:

1. **텍스트 카드**(숫자 없는 인포그래픽) → 검증 면제, 통과.
2. **dataset 기반 spec**(실데이터로 만든 것) → `_provenance.verified=True` 신뢰, 통과.
3. **LLM 본문 추출 수치 spec** → 각 값을 JARVIS09 실데이터와 대조:
   - 검증된 행만 남겨 **재구성** (검증 행 ≥ 최소 개수면 통과).
   - 0개 검증 + 관련 실데이터 존재 → 그 실데이터로 **대체** (실데이터 차트).
   - 0개 검증 + 실데이터 없음 → **스킵**(숫자 없는 카드로 폴백). *거짓 차트 < 차트 없음.*

### 3) 트립와이어 + 발행 전 이미지 게이트 (2중 안전망)

- **트립와이어**: `render_from_spec` 이 생성한 모든 이미지의 검증 결과를 provenance 레지스트리에
  기록. 수치 차트가 `verified` 없이 렌더되면(검증 우회) `verified=False` 로 남는다.
- **발행 전 게이트**: `prepublish_gate._image_factuality_leg` 가 draft 의 이미지 중 `verified=False`
  를 잡아 `kind="factuality"` Issue → 재작성 순환. 킬스위치 `PREPUBLISH_IMAGE_GATE=0`.

### 4) 무료 데이터 라이브러리 자동설치 — 화이트리스트 예외조항

ADR 004 의 텔레그램 승인 게이트는 *자율 에이전트(ReAct/LLM)가 사용자 미인지 외부 행동*을 하는
것을 막는다. 본 결정은 그 보호 대상이 **아닌** 좁은 예외를 신설한다:

- `JARVIS09_COLLECTOR/lib_bootstrap.py` 가 필요한 무료 데이터 라이브러리를 **갯수 제한 없이**
  `pip install` 한다 (**승인 없이**). ★ 사용자 박제: "데이터 받기 위해 새로 설치해야 하면
  갯수 제한 없이 설치하라."
- 고정 화이트리스트(캡) 대신 **안전 정책 게이트**로 통제 — ① 데니리스트(`_DENYLIST`) 아님
  ② PyPI 공식 저장소 *실존* (오타·typosquat 일부 차단) ③ *상용 전용* 라이선스 아님(무료 원칙).
  셋 다 통과하면 갯수 무관 설치. `_KNOWN_DATA_LIBS` 는 import명↔pip명 매핑 편의표(상한 아님).
- 분류: **internal 부트스트랩** (venv 내부 변경 — 외부 발행·과금 없음).
- 텔레그램은 *승인 인라인 버튼*이 아니라 **설치 알림**만 송출.
- ReAct `run_bash`(external, requires_approval) 경로와 무관 — 수집 코드가 안전 정책을 통과한
  데이터 라이브러리만 직접 부트스트랩하므로 자율 에이전트의 임의 셸 실행이 아니다.

`precommit_check` 의 `autocode/subprocess` 허용 목록에 `JARVIS09_COLLECTOR/lib_bootstrap.py`
추가 (예외조항 명문화).

## 포기한 대안

- **렌더된 PNG 직접 OCR 검증**: 비싸고 불안정. 생성 시점 검증(provenance)이 정확·저렴.
- **모든 차트를 dataset 으로만 생성(LLM 본문추출 폐기)**: 카드형 시각화(timeline·checklist 등
  텍스트 중심)는 본문 기반이 적합. 수치 차트만 실데이터 강제가 옳음.
- **무료 API도 텔레그램 승인 유지**: 사용자가 "무료면 승인 없이"를 명시 박제. 화이트리스트로
  안전 경계를 그어 자율성과 통제를 양립.

## 영향
- 신규: `JARVIS09_COLLECTOR/chart_data.py`, `lib_bootstrap.py`,
  `JARVIS06_IMAGE/validators/image_data_verifier.py`.
- 변경: `image_spec.py`(real_datasets 경로·검증·트립와이어), `prepublish_gate.py`(이미지 leg),
  `image_agent.generate_infographic`(passthrough), `precommit_check.py`(예외).
- **chart_generator 단일 진입점 이관 (2026-06-29 완료)**: `chart_generator`(JARVIS06)가
  `JARVIS09_COLLECTOR.providers.{ecos,krx,economic_data}_provider` *내부* 를 직접 import 하던
  것을 전부 제거. JARVIS09 공개 래퍼 `get_ecos_raw`/`get_krx_raw`/`get_ticker_history`/
  `download_ticker` 호출로 교체 → **수집(provider 접근)은 JARVIS09 단독**.
  - 파싱·로테이션·flat감지(`_parse_ecos_timeseries` 등)는 *"무엇을 차트로 그릴지" 시각화
    결정* 이므로 JARVIS06 잔류 (파서 본체 무변경 — 회귀 0). 새 글의 정식 경로는 이미
    `collect_chart_data`(JARVIS09)가 파싱·출처까지 구조화해 반환.
