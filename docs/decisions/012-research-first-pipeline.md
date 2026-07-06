# ADR 012 — 설계-우선 리서치 파이프라인 (Research-First Pipeline)

> **★ 부분 폐지 (사용자 박제 2026-07-06 — 자비스09 단순 수집기 재설계):**
> `collect_research` 의 **커버리지 측정 + 갭 재수집 순환(ⓔ) 폐지**, **fact 추출도 09에서 제거**.
> 자비스09는 *수집만* — 설계(plan_research) → 티어순 최대 15개 원시 문서 확정 → 원시 그대로 반환.
> **fact 추출·차트 숫자·종목 설명은 JARVIS02(작성기)가 받은 docs 로 직접 수행**(`build_evidence_pack`
> 을 02가 호출). 사유: 커버리지 루프가 재수집을 무한정 돌려 06:30 발행 2시간+ 정지(ERRORS [383][385]),
> 역할 혼재(수집기가 추출·판단까지). 설계-우선 *수집*(①~③)은 유지. 아래 원문은 역사 보존.

- **상태**: 부분 폐지 (2026-07-06) / 원 적용 (2026-07-02)
- **결정자**: 사용자 박제 2026-07-02 ("항상 설계를 먼저 하고 그 설계대로 수집한다.
  양질의 데이터를 받아오는 게 핵심. 그 데이터로 대본도 이미지도 만드는 게 그 다음.")
- **관련**: ADR 010 (이미지 사실성), ADR 011 (주제 적응형 데이터 소싱)

## 문제 (탐사로 확인된 3대 누수)

1. **수집→작성 병목**: JARVIS09가 문서 수십 건을 모아도 `draft_writer._gen_theme` 이
   상위 5건 × 앞 300자만 프롬프트에 주입 — 수집 자산 대부분이 대본에 미도달.
   출처 URL·기준 시점도 미전달 → 사실성 게이트가 대조할 근거 빈약.
2. **텍스트 수집 무설계**: `collect_for_theme` = 11개 프로바이더 블라인드 병렬 스윕.
   ADR 011 로 *차트 수치* 는 설계-우선이 됐지만 *글 근거(텍스트)* 는 설계 없음.
   커버리지(충분한가?) 개념 없음 → 부족해도 재수집 없이 진행. 뉴스·웹 문서는
   제목+스니펫 수준만 추출 (기사 전문 미확보).
3. **작성 단일 패스**: 아웃라인 없음 → 섹션 간 흐름 단절. 자기비평 없음 →
   어미 반복·나열식 문장 방치. 근거-주장 연결(F# 지목) 없음.

## 결정

### 1) JARVIS09 — 설계 → 수집 → 측정 → 재수집 순환

| 부품 | 파일 | 역할 |
|------|------|------|
| 리서치 설계자 | `research_planner.py` | 주제 → 핵심 질문 4~6개 + 질문별 근거종류·출처·쿼리 2종·최소 근거 수 (LLM 동적, 폴백 = 보편 5차원) |
| 근거 팩 | `evidence_pack.py` | 문서 → fact 단위 추출(LLM 배치, 원문 근거 강제) + 출처 박제(name·url·tier·as_of) + 임베딩 dedupe + **커버리지 측정** |
| 수집 순환 | `collector_engine.collect_research` | 광역 스윕 ∥ 질문별 조준 수집 → 얇은 문서 전문 딥페치 → 팩 추출 → **미충족 질문만 2라운드 재수집** (변형 쿼리 + discover 강제) |
| 전문 추출 | `generic_fetch.fetch_article` | trafilatura(자동설치) 기사 본문 전문 — 스니펫 → 본문 확장 |
| 소스 온보딩 | `source_onboarding.py` | 설계가 원하는 소스의 키 누락 감지 → 텔레그램 1일 1회 가입 안내(URL·절차·.env 위치) → 등록 시 자동 검증·자동 사용 |

- 근거 팩은 `JARVIS09_COLLECTOR/output/evidence/evidence_*.json` 에 박제 (30일 보존
  — `shared/file_cleanup.py` 규칙 추가). 관찰 가능성 + 스테이지 간 아티팩트.
- **원칙**: 출처 없는 fact 는 팩에 못 들어온다. confidence < 0.5 폐기.
  *거짓 근거 < 근거 없음* (ADR 010 과 동일 철학의 텍스트판).

### 2) JARVIS02 — 근거 주입 + 아웃라인 + 자기비평 (3-패스)

- `draft_writer._build_evidence_block`: EvidencePack 브리프(질문별 그룹·F# 번호·출처 표기)
  를 프롬프트에 주입. **"목록 밖 수치 사용 금지"** 명문화. 종전 5건×300자 발췌는 폴백.
- `draft_writer._plan_narrative`: 서사 설계 1패스 — 공감포인트·긴장·해소·섹션별
  핵심 메시지 + 근거(F#) 배정·차별화 한 줄. theme+date 캐시로 양 플랫폼 재사용.
- `draft_writer.critique_and_refine`: 자기비평 1패스 — 루브릭(도입 구체성·근거 융화·
  어미 반복·섹션 첫 문장·마무리 행동 유도) 점검 후 *문장만* 다듬은 전체본.
  **구조 시그니처 가드**: CHART/PHOTO 플레이스홀더·표·h2 하나라도 변형되거나
  분량 ±30% 초과 변동 시 원본 유지.
- 발행 전 사실성 게이트: `as_source_docs(pack)` 로 fact 를 대조군에 합류.
- `scheduler._is_similar_theme`: 유사 주제 중복 차단 1차 판정을 임베딩 코사인
  (`shared/embeddings`, ≥0.80)으로 승격 — 고정 그룹은 폴백.

### 3) JARVIS06 / JARVIS08

- `draft_processor.process_draft(evidence_pack=...)`: fact 문서를 collection_docs 앞에
  합류 → 기존 `facts_for_chart`/`facts_for_photo` 플럼빙 그대로 소비 (신규 경로 0).
- naver/tistory 블록 루프에 **미지 블록 타입 else 폴백** (텍스트 렌더 + GUARDIAN 보고)
  — "새 블록 타입 = 양 발행자 동시 갱신" 규정 위반의 무음 유실 차단.

### 킬스위치

| env | 기본 | 효과 |
|-----|------|------|
| `RESEARCH_FIRST=0` | on | 리서치 수집 → 종전 `collect_for_theme` 스윕 |
| `WRITER_RESEARCH_FIRST=0` | on | 근거 팩 주입 → 종전 5건 발췌 |
| `WRITER_CRITIQUE=0` | on | 자기비평 패스 스킵 |

## 포기한 대안

- **완전 자동 사이트 가입**: 한국 공공 포털(KOSIS·ECOS·DART·네이버)은 휴대폰
  본인인증·이메일 인증·CAPTCHA 필수 — 무인 자동화 불가 + 헌법(외부 영향 승인
  게이트 ★영구)과 충돌. → *온보딩 안내 자동화* (감지→안내→등록 시 자동 검증·사용)
  로 대체. 인증 없는 신규 소스용 `mode="auto"` 훅은 레지스트리에 준비.
- **pipeline 테이블 스키마 확장(angle 컬럼)**: RADAR angle 전달은 research_planner 가
  자체 각도 설계로 대체 (스키마 변경 리스크 회피). 후속 과제로 남김.
- **경제(3-call) 경로 즉시 적용**: 테마 경로 안정 확인 후 후속 (동일 함수 재사용 가능).

## 검증

```bash
python3 -m py_compile JARVIS09_COLLECTOR/{research_planner,evidence_pack,source_onboarding,generic_fetch,collector_engine}.py
python3 shared/precommit_check.py     # 전 카테고리 0건 유지
# 근거 팩 박제 확인: ls JARVIS09_COLLECTOR/output/evidence/
```

## 후속 과제

1. 경제 브리핑(3-call 경로)에 근거 팩·비평 패스 배선 (함수 재사용만 하면 됨).
2. RADAR → pipeline 에 angle/hook 전달 (스키마 확장 또는 meta JSON).
3. `source_onboarding` mode="auto" 실드라이버 (인증 없는 해외 오픈 API 추가 시).
4. 발행 후 모바일 프리뷰 스크린샷 QA (JARVIS08).
