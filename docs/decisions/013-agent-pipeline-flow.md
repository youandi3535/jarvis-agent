# ADR 013 — 에이전트 파이프라인 정본 흐름 (Canonical Agent Pipeline Flow)

- **상태**: 적용 (2026-07-03)
- **결정자**: 사용자 박제 2026-07-03 ("이 로직이야. 전체적인 흐름인데, 그 하나하나의
  작업은 아주 디테일하고 정교하고 촘촘하게, 그리고 완벽하게 결과물을 만들어 내야해.")
- **관련**: ADR 008 (도메인 소유권), ADR 010 (이미지 사실성), ADR 011 (주제 적응형
  소싱), ADR 012 (설계-우선 리서치), ERRORS [289][290][294]

## 정본 흐름 (사용자 지시 원문 기반)

```
자비스03 (트렌드 분석)
  └─ 주제 키워드 선정 + 프로필(한줄 정의·관련어·엔티티 유형) 생성
  └─ ★ 자비스02와 자비스09에게 *동시에* 트렌드 정보 제공   [폴백 없음 — 유일 경로]
        │
자비스09 (수집)
  └─ 주제 정보 수신 → 어디서 어떻게 받을지 *설계* (research_planner, ADR 012)
  └─ ★ 제한 없이, 받을 수 있는 곳 *전부* 에서 최대한 많은 진실성 있는 데이터 수집
  └─ 충분히 넉넉한 데이터를 자비스06과 자비스02에게 전달
        │
자비스02 (작성)
  └─ 주제(자비스03) + 데이터(자비스09) → LLM이 대본을 *아주 매력적으로* 작성
  └─ 작성된 대본을 자비스06과 자비스08에게 전달
        │
자비스06 (이미지)
  └─ 데이터(자비스09) + 대본(자비스02) → 퀄리티 높은 이미지 생성
  └─ 생성한 이미지를 자비스08에게 전달
        │
자비스08 (발행)
  └─ 대본(자비스02) + 이미지(자비스06) 를 합쳐 블로그 업로드
     (★ 네이버 먼저 → 티스토리, 직렬 — ERRORS [289])
```

## 핵심 원칙 (사용자 박제 — 모두 2026-07-03)

### 0) ★★ 키워드 단독 전송 금지 (강제사항 — 예외 없음)
- "자비스03은 트렌드 키워드를 누군가에게 보낼 때 키워드만 딸랑 보내지 말고, *항상*
  그 키워드를 설명할 수 있는 다양한 기본 정보까지 보태서 보내야 해. 강제사항으로 규정해놔!"
- 예: '배' → 과일? 선박? 인체? — 프로필 없이는 하류(수집·작성·이미지)가 판별 불가.
- 단일 진입점: `JARVIS03_RADAR/topic_pack.py` — 팩 후보는 프로필 필수 구조,
  임의 키워드는 `keyword_profile()` / `build_for_keyword()` 경유.
  테마 파이프라인도 `collect_research(angle=프로필요약)` 로 동봉 (trend_theme_writer).

### 1) 주제는 자비스03이 프로필까지 만들어 배포한다 — 02 중계·폴백 금지
- 구현: `JARVIS03_RADAR/topic_pack.py` — 트렌드 수집 잡 말미 자동 실행.
  경제 후보 추출 → LLM 배치 1회로 {적합성, 프로필, 교정 섹터} → 적합 상위 2개 →
  **JARVIS09 직접 선수집** (`collect_research(angle=프로필)` + `collect_chart_data(description=프로필)`)
  → `data/topic_pack_YYYY-MM-DD.json` 박제.
- 자비스02 `nv/ts_generate_draft` 는 `pick_candidate()` *소비만*. `select_*_topic`
  호출·JARVIS09 직접 호출 폐지. 팩 부재 시 `build_topic_pack()` 즉석 실행(동일 경로).
  강제 주제(JARVIS_FORCE_*)도 `build_for_keyword()` 경유.
- 이유: 키워드 *문자열* 만 중계되면 프로필이 유실돼 '은행나무'류 중의적 키워드를
  하류가 혼동 (ERRORS [290] GIGO). 프로필 생성 자체가 오분류 트립와이어.

### 2) 수집은 전부, 선택은 신뢰 순 — "논문 > API > 뉴스 > 기사 > 웹"
- **수집 범위 제한 금지**: "진실성 높은 논문이 있다고 논문만 받으면 안 된다. 전부
  다 받아야 해 일단!" — 수집 폭 배율 `J09_BREADTH`(기본 2.0), 소스별 상한
  `J09_MAX_PER_SOURCE`(기본 30), 팩 선수집 `TOPIC_PACK_MAX_DATASETS`(기본 40)·
  `TOPIC_PACK_RESEARCH_ROUNDS`(기본 3).
- **중복·충돌 시 선택 우선순위** (단일 진입점 `JARVIS09_COLLECTOR/models.py
  SOURCE_TRUST_TIER` / `trust_rank()`):
  | 티어 | 소스 |
  |------|------|
  | 1 논문 | academic, kci |
  | 2 공식 데이터 API | kosis, ecos, dart, krx, finance |
  | 3 뉴스 | naver_news, news |
  | 4 기사·전문지 | kor_econ, web_data |
  | 5 웹 | web (미지 소스 기본값) |
  | 6 블로그 | blog |
- 적용 지점: `collector_engine.collect_for_theme` (신뢰순 정렬 + content_hash 중복 시
  고신뢰 유지), `evidence_pack._dedupe_facts` (fact 충돌 시 낮은 티어 승리),
  `build_evidence_pack` (fact 추출 문서 우선순위).

### 3) 수치만 하드 게이트 — 프로즈는 자유 (제2조 재정의)
- "숫자로 들어가는 수치 데이터는 무조건 진실되어야 한다. 그것만 조심하면 돼.
  글은 상상·추론·예상할 수 있으니 꼭 팩트가 아니어도 된다."
- 구현: `law_enforcer._extract_claims/_ground_unsupported` — *수치 포함 주장* 만
  추출·차단. 비수치 서사(감상·전망·해석·상상·추론·예상)는 게이트 비대상.
  차트 수치는 ADR 010 (`verify_chart_spec`) 그대로. 본문 수치는 데이터 카탈로그
  값만 인용 (`draft_writer._build_data_catalog`).
- 목적: 팩트 데이터를 재료로 *매력적·매혹적·감동적* 글쓰기 — 재작성 순환은
  수치 거짓·매력도 미달일 때만.

### 4) 발행 순서 — 네이버 먼저, 티스토리 나중 (직렬)
- ERRORS [289] 참조. 경제·테마 파이프라인 공통.

## 포기한 대안

- **02 가 팩 부재 시 자체 주제 선정으로 폴백**: 사용자 명시 거부 ("폴백도 만들지
  마") — 이중 경로는 프로필 없는 주제가 다시 새는 구멍. 팩 실패 = 발행 차단이
  부적합 주제 강행보다 낫다 (데이터 진실성 원칙).
- **프로즈 전반 사실성 차단 유지**: 재작성 순환 낭비 + 글 매력 저하. 수치만
  지키면 서사는 자유가 사용자 의도.
- **고신뢰 소스만 수집**: 커버리지 붕괴. 수집은 전부, 선택만 신뢰순.

## 검증

```bash
# 02 가 주제 선정·수집을 직접 하지 않는가 (nv/ts_generate_draft 내)
grep -n "select_naver_topic\|select_tistory_topic\|collect_for_theme\|collect_chart_data" \
  JARVIS02_WRITER/trend_economic_writer.py | grep -v "def select" | grep -v "^1[0-9][0-9][0-9]:"
# → nv/ts_generate_draft 함수 안에는 0건 (레거시 run_naver/run_tistory 만 잔존 — guard 차단)

# 신뢰 티어 단일 진입점
grep -rn "SOURCE_TRUST_TIER\s*=" --include='*.py' JARVIS09_COLLECTOR | grep -v models.py
# → 0건

python3 shared/precommit_check.py   # 전체 44종
```
