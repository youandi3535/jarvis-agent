# 011. 주제 적응형 동적 데이터 소싱 — 고정 카탈로그 → 웹 발견·범용 수집

## 상태
확정 (2026-07-01 사용자 박제)

## 배경

ADR 010 은 "차트 수치는 JARVIS09 실데이터로만"을 세웠고, `data_planner.plan_data_sources()` 가
주제별로 series·출처·검색어를 LLM 으로 *설계* 하게 했다. 그러나 실행에 구조적 결함이 있었다:

**설계 LLM 이 고를 수 있는 출처가 고정 11-provider 카탈로그**(`_SOURCE_CATALOG`: kosis·ecos·dart·
krx·finance·academic·kci·naver_news·news·kor_econ·web·blog)뿐이었다. 전부 *한국 거시경제·공공통계*
편향이라, 트렌드 주제가 그 범위를 벗어나면(지역·교통·특정기업·신기술·해외·문화) **실데이터 0** 이
됐다. 사용자 지적:

> *"트렌드 주제는 계속 바뀌지? 고정이 아니라고. 그런데 넌 고정인 것처럼 어느 사이트 웹 API로만
> 계속 데이터를 받으려 해. 주제가 뭔 줄 알고? 주제에 따라 받을 수 있는 데이터 웹주소·API가
> 달라지는 거야. 그걸 판단할 수 있어야 해."*

> *"주제가 선정되면, 어떻게·어디서 받아야 할지부터 설계해. 그런 다음 동적으로 받아야 할 곳으로부터
> 데이터를 받아. 데이터를 받는 와중에 우리가 없는 카탈로그가 있으면 API든 설치든 뭐든 해서
> 연결하고 데이터를 받아라. 자동 설치해야 할 게 있으면 스스로 설치도 하고."*

실제 증상: `기흥구`(부동산 지역)·`KTX`·`로보스타`·`Claude Sonnet 5` 등에서 실데이터 0 → 차트 못
만듦 → JARVIS06 이 *추상 AI 사진* 대체 → Pollinations 가 기형(미로 돌·기형 동물)으로 렌더.

## 결정

### 1) 카탈로그는 '상한'이 아니라 '아는 빠른 길' 힌트

`_SOURCE_CATALOG` 에 `discover` 소스를 추가. planner 프롬프트를 *"카탈로그로 안 되는 주제엔
반드시 `discover`+구체 검색어를 넣어라"* 로 개정. 확신이 안 서면 각 series 에 `discover` 를 폴백으로
항상 붙이도록 유도. 결정론 폴백도 경제 편향(발행액·만족도) → **discover 1순위 주제 적응형**으로 교체.

### 2) 발견(discovery) — 어떤 주제든 실제 데이터 페이지를 찾는다

`JARVIS09_COLLECTOR/discovery.py` `web_search(query)` — 3 백엔드 병행(fail-open):

| 백엔드 | 연결 | 키 |
|--------|------|----|
| DuckDuckGo (`ddgs`) | lib_bootstrap 자동설치 | 불필요 |
| Naver 검색 API (webkr·encyc·doc·news) | 보유 `NAVER_CLIENT_ID/SECRET` | 보유 |
| data.go.kr 공공데이터포털 | 스크레이프 | 불필요 |

랭킹 = **쿼리 관련성 최우선** + 도메인 신뢰도(정부·통계·논문 우선) + 수치 스니펫 가산.

### 3) 범용 수집(generic_fetch) — 받아야 할 곳에서 실제로 받아온다

`JARVIS09_COLLECTOR/generic_fetch.py` `fetch_documents(hits)` — robots·rate-limit 준수 후 fetch →
HTML 표(`pandas.read_html`)·JSON·프로즈(수치 문장)에서 데이터 추출 → `RawDocument`. 필요 라이브러리
(pandas·lxml·bs4·html5lib)는 **자동설치**(ADR 010 게이트). 반환 문서는 **기존
`chart_data._extract_series_from_docs`(LLM 추출)로 재사용** → dataset+출처(URL) 자동 생성.

### 4) 실행 계층 연결 — `discover` 는 catch-all 폴백

`DiscoveryProvider`(BaseProvider) 로 감싸 `_get_provider("discover")` 등록. `_collect_one_series`
가 고정 출처 전패 시 discover 폴백, Step3 멀티소스 루프에도 discover 추가(비용 가드: 검색어 2개).
새 provider 추가는 여전히 가능하나 **필수는 아님** — discover 가 미지 주제를 흡수.

### 5) LLM 스로틀 무음 degrade 방지 (부수 발견)

Claude Code SDK(Max 구독)는 rate-limit 시 `ResultMessage(num_turns=0, duration_api_ms=0)` 로
**빈 응답**(예외 아님)을 낸다 → planner 설계·translate·추출이 조용히 폴백/원문통과로 degrade 하던
것을 발견. `shared/llm._run_sdk_sync` 가 이를 감지·로그(`⏳ rate-limit 스로틀`), `invoke_text` 가
백오프 재시도(2회). 지속 스로틀은 planner→discover 폴백으로 graceful(비치명적).

## 포기한 대안

- **주제별 if-else 로 소스 라우팅**: 주제는 무한 → 하드코딩 불가. LLM 설계 + 웹 발견이 유일 확장.
- **유료 검색 API(SerpAPI/Brave) 단독**: 키·과금 필요. 무료(ddgs)+보유(Naver)+공공(data.go.kr)
  병행으로 키 추가 0.
- **실패 슬롯에 추상 은유 AI 사진**: 사실성·품질 훼손(기형 이미지). 근본은 데이터 소싱 확대.
  단기 완화로 실패 슬롯은 *주제 구체 실사진* + 초현실·추상 금지 negative(부차 과제 B).
- **planner 스로틀을 긴 블로킹 재시도로 극복**: Max 구독 스로틀 창이 길면 파이프라인이 분 단위
  정지. 짧은 재시도 + discover 폴백이 옳음(속도·견고 양립).

## 영향
- 신규: `JARVIS09_COLLECTOR/{discovery.py, generic_fetch.py, providers/discovery_provider.py}`.
- 변경: `data_planner.py`(카탈로그=힌트·discover·폴백 교체), `chart_data.py`(discover 연결·폴백·
  Step3), `providers/__init__.py`, `shared/llm.py`(스로틀 감지·invoke_text 재시도).
- 부차: `economic_poster.py`(.env 명시경로 최상단 로드), `tistory_html_writer.py`·
  `prompt_translator.py`·`draft_processor.py`(실패 슬롯 실사진 + 초현실·추상 금지 negative).
- 검증: discovery/generic_fetch 는 JARVIS09 내부(httpx) — 수집 단일 진입점 준수. precommit 40종 통과.
