# JARVIS02 WRITER

## ★ 최상위 헌법 — BLOG_SUPREME_LAW.md (단일 진실 소스)

**블로그 관련 모든 규정은 `JARVIS02_WRITER/BLOG_SUPREME_LAW.md` 에서만 관리된다.**

- 제0조~제16조 전체 포함 (글쓰기·이미지·SEO·원고 생성 프로세스·태그 등)
- 규정 수정 시 이 파일만 수정. 다른 파일에 중복 규정 추가 금지.
- 런타임 집행: `law_enforcer.py` 단일 진입점. SEO 파서: `parse_seo_block()` / `parse_diff_block()`.

**원고 생성 2단계 의무**: `BLOG_SUPREME_LAW.md` **제16조** 참조.
**SEO 기준 전체**: `BLOG_SUPREME_LAW.md` **제15조** 참조 (`seo_standards.py` 는 어댑터 역할만).

---

## 기본 규칙
- 답변: **한국어**
- 새 기능 추가 시 → 미사용 파일 정리 + 이 파일 업데이트 (자동)
- 이 파일 원칙: 비직관적 *구현* 규칙만. 콘텐츠 정책은 BLOG_SUPREME_LAW.md 에만.

---

## 비직관적 규칙 (기술 제약 — 콘텐츠 정책 아님)

| 항목 | 규칙 |
|------|------|
| max_tokens | **8192 고정** — 더 높이면 API 중간 절단 |
| 섹션 문장수 임계값 | `length_manager.py` 단일 진입점 (`SEC_SENTS` 등). 다른 파일에 박지 말 것 |
| pytrends 패치 | `.venv/.../pytrends/request.py`: `method_whitelist` → `allowed_methods` |
| Finder Cmd+V | `CGEventPost(kCGHIDEventTap)` HID 레벨. 클립보드는 클릭 전 선복사 |

버그 이력 → `BUGS.md`

---

## ★ 테마 발행 실패 대응 원칙 (ERRORS [168][174][176] 3회 반복 박제)

**`data_empty`는 harness retry 가 아닌 *테마 교체* 로 대응해야 함. 동일 테마 retry 는 동일 실패 반복.**

| 상황 | 잘못된 대응 | 올바른 대응 |
|------|------------|------------|
| 종목 데이터 0개 (data_empty) | harness retry 2회 반복 | 폴백 후보 테마로 즉시 교체 |
| LLM이 특정 테마 응답 불가 (신규상장 등) | 동일 LLM 호출 반복 (22분 낭비) | `_LLM_SKIP_PATTERNS` 즉시 우회 → Naver Finance 폴백 |
| 폴백 후보 전부 유사주제·완료 | 빈 폴백 루프 종료 | 폴백 후보 선정 시 *선별·완료·유사주제 사전 필터* 필수 |

**검증**: `grep -rn '_collect_data_empty\|_LLM_SKIP_PATTERNS\|_fallback_candidates' JARVIS02_WRITER/scheduler.py JARVIS02_WRITER/collect_theme.py JARVIS02_WRITER/trend_theme_writer.py` → 3개 패턴 모두 존재해야 함.

---

## ★ 발행 전 품질 게이트 — `prepublish_gate.py` 단일 진입점 (사용자 박제 2026-06-28)

**"팩트만, 그리고 너무 읽고 싶은 글만 발행". 검수는 발행 *전* harness Layer 3 에서 한다.**

- **단일 진입점**: `prepublish_gate.prepublish_quality_issues(draft, post_type, source_docs, market_data)`. economic_poster·trend_theme_writer 두 `_verify_all` 이 *구조 검증 통과 후에만* 호출 (LLM 비용 절약). 새 검수 차원 추가 시 이 모듈만 수정.
- **두 레그**: ① 사실성 = `law_enforcer.factuality_issues` (출처 대조 + JARVIS09 `web_verify` 웹 재검증). ② 매력도/유익성 = `post_quality_analyzer.judge_engagement` (engagement_judge alias=Opus, 임계 70/70).
- **kind 규칙 (★ 비직관)**: 게이트 Issue 는 `kind="factuality"|"engagement"` — *`draft_quality` 아님*. 그래야 `_fix_drafts` 가 inline 패치를 *건너뛰고* 곧장 unfixed → WRITER step 재실행 = 재작성 순환. `draft_quality` 로 만들면 draft_fixer 가 못 고치는 걸 붙잡아 헛수고.
- **fingerprint 안정성 (★ 비직관)**: `Issue.detail` 에 *점수 raw·attempt 변동값 금지*. factuality=claim 텍스트, engagement=실패 차원 태그만. 변동값 넣으면 매 attempt 지문이 달라져 abort 안 됨 → max_attempts 낭비.
- **정책**: 사실 판정 LLM 실패=차단(fail-closed) / 웹 인프라 실패=통과(fail-open) / 테마글(약한 출처)=웹 1차 근거로 "웹도 확인 불가만 차단" / engagement LLM 실패=통과(fail-open, 재생성 사유일 뿐).
- **킬스위치 (라이브 안전)**: `PREPUBLISH_FACT_GATE=0` / `PREPUBLISH_ENGAGEMENT_GATE=0` → 코드 수정 없이 각 레그 즉시 비활성화.
- **모델 alias**: `fact_judge`·`engagement_judge` (둘 다 Opus 4.6) — `shared/llm.py` 의 `MODELS`·`_sdk_model`·`_ALIAS_MODEL`·`_model_map` 4곳 등록.
