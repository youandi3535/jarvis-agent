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
| **★ 플랫폼 단위 끝까지 직렬 (사용자 박제 2026-07-03 — ERRORS [289][301])** | 경제(economic_poster)·테마(trend_theme_writer) 모두 harness 액션이 *플랫폼별 2개*: 네이버 액션(대본→검증 순환→발행) **완전 종결 후** 티스토리 액션 시작. 한쪽 실패·재작성이 다른 쪽을 지연·차단 금지 (실패 격리). verify/fix 의 step 이름 문자열은 해당 `@action_step` 이름과 정확히 일치 필수 (harness resume). 공유 자원(경제=키워드 제외 `nv_keyword_final`, 테마=수집 결과)은 액션1 state → 액션2 input_data 로 전달 |
| **★ 02 에는 수집이 없다 (사용자 박제 2026-07-23)** | 수집 파일·함수·잡 전부 09 로 물리 이관 (`precollect_cache.py`·`precollect_*`·`_theme_collect`·`fetch_kor_counts`·SEO 페치 삭제). 02 에 남은 것은 파사드 호출 한 줄뿐 — `collect_all(...)` / `market_snapshot()` / `published_post_kor_counts()` / `seo_reference_docs()`. **선계산 캐시를 02 가 열지 말 것** — 재사용 판단은 `collect_all(use_cache=)` 안(09). 상세 표: 루트 CLAUDE.md 수집 섹션 |
| **★ `trend_economic_writer` 에 주제선정·트렌드로드·레거시 발행 없음 (2026-07-23 — 816줄 삭제)** | 삭제분: `load_today_trends`·`_build_emergency_trends`(LLM 으로 트렌드를 *지어내* 03 데이터 폴더에 쓰던 코드)·`select_naver_topic`/`select_tistory_topic`·`_topic_econ_fit`·`_is_same_topic`·`_mark_keyword_used`·`_legacy_publish_guard`·`run_naver`/`run_tistory`. **전부 호출자 0 인 죽은 코드였다** — 살아있는 경로는 harness step(`nv_/ts_collect → _generate_draft → _publish`) 뿐. 왜 지웠나: 코드가 남아 있으면 다음 작업자의 손이 그리로 간다 (판단 이관 ≠ 코드 이관, ERRORS [489]). 경제 중복회피 원장은 03 이 **DB(`post_analysis`)에서 파생** — JSON 사본 없음 |
| **★ 하네스를 우회하는 발행 경로 0 (2026-07-23 — 경제·테마 동시)** | 경제 `run_naver`/`run_tistory` 와 테마 `run_naver_theme`/`run_tistory_theme` **전부 삭제**. 넷 다 수집→대본→발행을 하네스 밖에서 한 벌 더 구현한 복사본이라 prepublish 사실성·매력도 게이트도, Layer 3 검증 순환도 안 타고 곧장 블로그로 나갔다. CLI 가 `JARVIS_ALLOW_LEGACY_PUBLISH=1` 로 차단을 스스로 풀던 것도 함께 폐기(`--naver-only`/`--tistory-only` 포함). **살아있는 발행 경로 = 경제 `nv_/ts_publish` step · 테마 `run_all_themes()` — 그뿐.** 단발 발행이 필요해도 새 직접발행 함수를 만들지 말 것 |
| **★ 경제 브리핑 주제 = 자비스03 단독 (사용자 박제 2026-07-03)** | `nv/ts_collect` 는 `JARVIS03_RADAR.topic_pack.pick_slot_candidate(exclude_keyword=, force_env=)` **한 줄**로만 주제 수령 (강제주제·당일팩·소진복구 재빌드가 그 안에 있다 — 02 가 분기 복제하던 것을 03 으로 통합). 02 에서 `select_*_topic`·`collect_for_theme`·`collect_chart_data` 직접 호출 금지 |
| **★ 통합 이미지 파이프라인 (사용자 공동설계 2026-07-05)** | 경제·테마 **둘 다** `JARVIS06.draft_processor.process_draft(draft_html, collected, platform, out_dir)` 단일 이미지 경로. 대본은 **Pass-1-only(placeholder)** — 경제는 `generate_article_html(..., pass2=False)`, 테마는 `generate_theme_html(collected, ...)`. 데이터 계약 = **`CollectedData`(4-part: datasets·docs·facts·entities, `JARVIS09_COLLECTOR/models.py`)** 단일 상자. 카테고리 노브(min_images=5·thumbnail_body_chars=3000·allow_stock_financial)는 `CATEGORY_POLICY[category]` 레지스트리. 검증 tolerance = **`models.grounds()`**(표시 올림/버림 or ±5%) — slot·law_enforcer·image_data_verifier 공통. 단, prepublish 종목 재무밴드(PER/ROE ±10%·주가 ±5%)는 `_stock_facts_leg`에 유지. **★ 본문 이미지 = 실데이터 인포그래픽만 (사용자 박제 2026-07-06): 못 만들거나 datasets 소진 시 폴백 없이 빈 슬롯 — AI사진·matplotlib 폴백 전부 폐기. 썸네일만 예외(대표 AI실사+로컬 타이틀카드 폴백, 누락0).** process_draft는 본문 인포그래픽 min-5 top-up(인포그래픽으로만) + 썸네일 필수 담당 |
| max_tokens | **8192 고정** — 더 높이면 API 중간 절단 |
| 섹션 문장수 임계값 | `length_manager.py` 단일 진입점 (`SEC_SENTS` 등). 다른 파일에 박지 말 것 |
| **★ pytrends 패치 — venv 직접 수정 금지 (2026-07-20 정정, ERRORS [455])** | pytrends 4.9.2 는 `Retry(method_whitelist=...)` 를 쓰는데 urllib3 2.0 에서 `allowed_methods` 로 개명돼 `TrendReq(..., retries>0)` 첫 요청이 TypeError. 종전 규정은 `.venv/.../pytrends/request.py` 손수정이었으나 **venv 재생성 시 소실 + google_collector 가 예외를 삼켜(`except Exception: return []`) pytrends 경로만 죽은 채 RSS 폴백으로 연명하는 무증상 열화** 발생. → `shared/pytrends_utils.ensure_retry_compat()` 가 런타임 흡수(import 시 자동). **venv 안 파일을 고치지 말 것.** 검증: `TrendReq(hl="ko",tz=540,timeout=(10,30),retries=3)` 로 `interest_over_time()` 성공해야 함 |
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
- **두 레그**: ① 사실성 = `law_enforcer.factuality_issues` (출처 대조 + JARVIS09 `web_verify` 웹 재검증). ② 매력도/유익성 = `post_quality_analyzer.judge_engagement` (engagement_judge alias=Sonnet 5, 임계 70/70).
- **kind 규칙 (★ 비직관)**: 게이트 Issue 는 `kind="factuality"|"engagement"` — *`draft_quality` 아님*. 그래야 `_fix_drafts` 가 inline 패치를 *건너뛰고* 곧장 unfixed → WRITER step 재실행 = 재작성 순환. `draft_quality` 로 만들면 draft_fixer 가 못 고치는 걸 붙잡아 헛수고.
- **fingerprint 안정성 (★ 비직관)**: `Issue.detail` 에 *점수 raw·attempt 변동값 금지*. factuality=claim 텍스트, engagement=실패 차원 태그만. 변동값 넣으면 매 attempt 지문이 달라져 abort 안 됨 → max_attempts 낭비.
- **정책**: 사실 판정 LLM 실패=차단(fail-closed) / 웹 인프라 실패=통과(fail-open) / 테마글(약한 출처)=웹 1차 근거로 "웹도 확인 불가만 차단" / engagement LLM 실패=통과(fail-open, 재생성 사유일 뿐).
- **킬스위치 (라이브 안전)**: `PREPUBLISH_FACT_GATE=0` / `PREPUBLISH_ENGAGEMENT_GATE=0` → 코드 수정 없이 각 레그 즉시 비활성화.
- **모델 alias**: `fact_judge`·`engagement_judge` (둘 다 Sonnet 5, ADR 017 단일 통일) — `shared/llm.py` 의 `MODELS` dict 한 곳만 등록(★ 2026-07-04 단일소스화 — `_ALIAS_MODEL`/`_DEFAULT_MODEL_ID` 는 이 dict 에서 자동 파생).
