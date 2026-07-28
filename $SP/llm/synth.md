## [발행품질] 블로그 본문 대본 생성 (경제·테마 공통, Pass-1 통짜)
- alias: writer_long_body (max_tokens 8000 (선언값 — 실제 미적용, 아래 reducible 참조))
- 위치: JARVIS02_WRITER/draft_writer.py:599 (_draft_invoke)
- 트리거: j01_economic_post(cron 07:00) / j01_theme_post_21(cron 21:00) → harness nv_/ts_generate_draft
- 빈도: 플랫폼당 정확히 1회 → 발행 1회(NV+TS)=2회 × 글종류 2 = 하루 4회. harness DEFAULT_MAX_ATTEMPTS=2 재작성 순환 시 최악 8회
- 소비자: 발행 본문 HTML 그 자체 → 네이버·티스토리 게시. 최종 소비자 = 독자
- 줄일여지: ★ 캐시가 0 (cache_read=0, 호출당 $0.73 로 단가 1위). NV/TS 가 같은 수집·같은 supreme_block 으로 2번 도는데 캐시가 안 걸린다. MODELS 주석이 '호출 2회뿐이라 손익분기 2.11회 미달' 로 cache=False 해뒀는데, system 자리 분리(_SPLIT_SYSTEM)가 이미 적용됐으므로 재측정 가치 있음. 예상절감: 캐시가 걸리면 2번째 플랫폼 호출의 입력분(테마 실측 input 50,950) 대부분 → 발행당 ~25% 절감. 위험: 낮음(캐시는 내용 불변). 단 이건 시스템 최우선 품질 경로라 프롬프트 축소는 권하지 않음
- 근거: DB 7일 n=8 tot=642,819 out=261,305 cache_read=0 $5.82. grep _draft_invoke 호출부 = _gen_economic_ts_nv:775 / _gen_theme:1348 뿐

## [발행품질] 대본 빈응답 재시도 가드
- alias: writer_long_body (max_tokens 8000)
- 위치: JARVIS02_WRITER/draft_writer.py:603
- 트리거: 동일 대본 step — _draft_invoke 내부 ①분기
- 빈도: raw 공백일 때만 +1회. last_call_infra_incomplete() 참이면 미발사(자가증폭 차단). 실측 발화 0
- 소비자: 동일 (대본)
- 줄일여지: —
- 근거: draft_writer.py:603 조건부. 최근 4회 발행 로그에 재호출 흔적 0

## [발행품질] 대본 design-only 재시도 (설계블록만 오고 본문 0자)
- alias: writer_long_body (max_tokens 8000)
- 위치: JARVIS02_WRITER/draft_writer.py:610
- 트리거: 동일 — _draft_invoke 내부 ②분기
- 빈도: 조건부 +1회. 실측 발화 0
- 소비자: 동일 (대본)
- 줄일여지: —
- 근거: draft_writer.py:610 조건부

## [발행품질] 경제 3섹션 분할 폴백 call1/call2/call3 (+각 재시도)
- alias: writer_long_body (max_tokens 8000 × 3콜)
- 위치: JARVIS02_WRITER/draft_writer.py:867·869 / 915·917 / 965·967 (_gen_economic_ts_nv_parallel)
- 트리거: tistory_html_writer.generate_article_html:336 — 단일 호출이 빈 문자열이고 인프라 사유가 아닐 때만
- 빈도: 폴백 전용. 발동 시 3콜(각 +1 재시도 = 최대 6콜/플랫폼). 실측 발화 0
- 소비자: call1/2/3 결과를 정규식으로 잘라 대본 1개로 조립
- 줄일여지: 폴백 3단(단일 → 3섹션 → 'CLI') 중 3단째가 같은 _gen_economic_ts_nv 를 또 부르는 동일 프롬프트 재시도라 잉여 가능성. 발동 0이라 실절감 0 — 지금 손대지 말 것
- 근거: ThreadPoolExecutor(max_workers=1) 확인 — 이름만 _parallel, 실제 순차

## [발행품질] 경제 CLI 폴백 (실제로는 Pass-1 3번째 시도)
- alias: writer_long_body (max_tokens 8000)
- 위치: JARVIS02_WRITER/tistory_html_writer.py:338 → :174 → draft_writer._gen_economic_ts_nv
- 트리거: 단일 호출·3섹션 폴백 모두 실패 시
- 빈도: 조건부 +1콜(가드 포함 최대 3콜). 실측 발화 0
- 소비자: 대본
- 줄일여지: 이름은 CLI 인데 앞 단계와 프롬프트가 동일 — 실패 원인이 콘텐츠면 결과도 동일. 폴백 3단 중 1단은 잉여
- 근거: tistory_html_writer.py:338 호출 체인 확인

## [발행품질] 테마 대본 문장수 미달 재생성 (Pass-1 통째로 다시)
- alias: writer_long_body (max_tokens 8000)
- 위치: JARVIS02_WRITER/theme_html_writer.py:141
- 트리거: j01_theme_post_21 → sent_count < MIN_SENTENCES_THRESHOLD(20)
- 빈도: 조건부 +1회/플랫폼 → 발동 시 테마 대본이 2→4콜. 07-27 실측 34·40문장으로 미발동
- 소비자: ★ 조건부 폐기 — sc2 > sent_count 일 때만 채택. 재생성분이 더 짧으면 8000토큰 응답을 받고 버린다
- 줄일여지: harness Layer3 분량 검증이 뒤에서 같은 걸 잡으므로 중복 방어. 경제 쪽 동일 로직은 이미 죽어 있어(pass2=False) 경제/테마 비대칭 = ③ 모든 글 적용 위반 신호. 예상절감: 발동률에 비례(현재 0). 위험: 낮음
- 근거: theme_html_writer.py:141 채택 조건 확인

## [기타] [죽은 분기] 경제 대본 문장수 미달 재생성
- alias: writer_long_body (max_tokens 8000)
- 위치: JARVIS02_WRITER/tistory_html_writer.py:381 (if ... and pass2:)
- 트리거: 없음 — 도달 불가
- 빈도: 0회. 살아있는 호출자 2곳(trend_economic_writer.py:331·607)이 둘 다 pass2=False
- 소비자: 소비자 0 (실행 자체가 안 됨)
- 줄일여지: 죽은 분기. 토큰 0이지만 테마에는 살아 있어 비대칭 — 삭제 또는 양쪽 통일 필요
- 근거: grep pass2= → 두 호출자 모두 False 확인

## [기타] [죽은 코드] 감성 도입부 1문장 생성
- alias: writer_long_body (max_tokens 8000 (1문장 생성에 본문용 alias))
- 위치: JARVIS02_WRITER/draft_writer.py:259 (_gen_hook)
- 트리거: 없음 — 어떤 잡·경로도 부르지 않음
- 빈도: 0회/일
- 소비자: ★ 소비자 0 — tistory_html_writer.py:41 이 import 만 하고 호출부 없음
- 줄일여지: 삭제 대상. 죽은 코드 + alias 오배정 이중 결함. 절감 0이지만 다음 작업자가 되살리면 1문장에 8000짜리 alias 를 쓰게 됨(위험). 위험: 없음
- 근거: grep _gen_hook → 정의(draft_writer:253) + import(tistory_html_writer:41) + 주석뿐, 호출 0

## [발행품질] 발행 전 통합 품질 판정 (사실성 blocked_claims + 매력도 5축) — 발행 게이트 유일 LLM
- alias: fact_judge (max_tokens 600 (호출 override — 실제 미적용))
- 위치: JARVIS02_WRITER/prepublish_gate.py:479 (_combined_quality_call)
- 트리거: j01_economic_post / j01_theme_post_21 → harness Layer3 _verify_all → prepublish_quality_issues (구조 검증 통과 후에만)
- 빈도: 플랫폼당 1회 = 하루 4회. 재작성 순환 시 최악 8회
- 소비자: ① blocked_claims → factuality Issue → 재작성 순환 ② llm_scores → post_scorer Section A(20점) → 100점 총점 게이트 + 텔레그램 점수 리포트. 둘 다 실소비 확인
- 줄일여지: 이미 사실성+매력도를 1콜로 통합했고 본문 4000자·코퍼스 2000자로 절단. 추가 절감 여지 작음 — 손대지 말 것
- 근거: DB 7일 n=33 tot=1,182,665 $3.28. 07시 16 / 21시 13 / 22시 2 분포

## [발행품질] 발행 전 통합 판정 인프라 재시도
- alias: fact_judge (max_tokens 600)
- 위치: JARVIS02_WRITER/prepublish_gate.py:487
- 트리거: 1차 호출이 ok=False 일 때만
- 빈도: 조건부 +1회. _nonessential=True 라 회로 open 시 즉시 반환
- 소비자: 동일 (판정 결과)
- 줄일여지: —
- 근거: 07-27 fact_judge 5건 중 1건이 21:21 실패→21:22 재시도

## [기타] [죽은 게이트] 사실성 claim 추출
- alias: fact_judge (max_tokens 4000)
- 위치: JARVIS02_WRITER/law_enforcer.py:1456 (_extract_claims)
- 트리거: 없음 — 유일 호출자 factuality_issues():1766 을 부르는 코드가 저장소에 0
- 빈도: 0회/일
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상(3콜 세트 통째). ★ CLAUDE_WRITER.md 가 아직 '두 레그 ① 사실성 = law_enforcer.factuality_issues' 로 박제 — 문서·코드 드리프트. 실제 사실성 판정은 prepublish_gate._combined_quality_call 단독. 절감 0, 위험: 없음(이미 죽음)
- 근거: grep 'factuality_issues(' 전수 → 매칭 0행. post_scorer/prepublish_gate 의 factuality_issues= 는 동명 키워드 인자

## [기타] [죽은 게이트] 사실성 출처 grounding 판정
- alias: fact_judge (max_tokens 4000)
- 위치: JARVIS02_WRITER/law_enforcer.py:1485 (_ground_unsupported)
- 트리거: 없음 — factuality_issues():1842 에서만 호출
- 빈도: 0회/일
- 소비자: ★ 소비자 0
- 줄일여지: 위와 동일 — 통째 삭제
- 근거: 동일 grep 근거

## [기타] [죽은 게이트] 웹 근거 확인 (claim 단위 루프)
- alias: fact_judge (max_tokens 4000)
- 위치: JARVIS02_WRITER/law_enforcer.py:1508 (_web_confirms), 호출부 :1893
- 트리거: 없음 — factuality_issues 내부 for 루프
- 빈도: 0회/일. 살아있었다면 claim 1건당 1콜 (_FACT_MAX_WEB_CHECKS 상한만큼 배수)
- 소비자: ★ 소비자 0
- 줄일여지: ★ 되살릴 때 최고 주의 — 유일한 claim-N배 증폭 지점. 통합 1콜 게이트로 이미 대체된 설계라 삭제가 안전
- 근거: 동일 grep 근거 + :1893 이 for c in pending 루프 안

## [감사진단] [LLM 도달 불가] 제2조 진실성 감사 — 본문 수치↔출처 대조
- alias: writer_short_analysis (max_tokens 1600)
- 위치: JARVIS02_WRITER/law_enforcer.py:1277 (audit_factuality 내부)
- 트리거: enforce_supreme_law:488 에서 호출은 됨. 그러나 LLM 레그 진입 조건 `if source_data and num_patterns` 를 못 넘음
- 빈도: 0회. 살아있는 호출자 2곳이 source_data 를 안 넘긴다 — draft_processor.py:609 `_esl(blocks, platform, tag)` · draft_fixer.py:83 `enforce_supreme_law(blocks, platform, tag)`
- 소비자: ★ 소비자 0 — 매 발행마다 '제2조 진실성 감사' 를 통과했다고 기록되지만 실제로 아무 수치도 검사하지 않음
- 줄일여지: ★ 토큰은 0이지만 *검증이 있다고 믿는 상태* 가 위험 — 절감 대상이 아니라 결함. 되살리려면 호출자에 source_data/post_type 전달 필요 → 그 순간 0에서 하루 4회로 증가. 판단 필요: prepublish_gate 통합 게이트와 중복이면 감사 자체를 폐기하는 쪽이 맞음
- 근거: grep 'enforce_supreme_law(' 전수 = 호출자 2곳 확인, 둘 다 3-인자 위치호출로 source_data 미전달. law_enforcer.py:1262 조건 확인

## [발행품질] 제0조 감성 도입부 LLM 교체 (AI식 팩트 오프닝 감지 시)
- alias: writer_short_analysis (max_tokens 1600)
- 위치: JARVIS02_WRITER/law_enforcer.py:550 (_generate_human_intro)
- 트리거: enforce_supreme_law 2경로 — draft_processor.py:609(플랫폼 대본마다) + draft_fixer.py:83
- 빈도: 조건부 0~1회/플랫폼. _AI_OPENER 매치 시에만. 최근 발행 4건 로그에 '제0조' 0건 → 실사용 사실상 0
- 소비자: 생성 문장을 첫 블록 앞에 삽입 → 발행 본문
- 줄일여지: —
- 근거: law_enforcer.py:550 + _AI_OPENER 조건. 발행 로그 grep 0건

## [기타] [죽은 코드] 차트 이미지 사이 연결 문장 ×2 (해요체/합니다체)
- alias: writer_short_analysis (max_tokens 80)
- 위치: JARVIS02_WRITER/economic_poster.py:288, 293 (_fix_consecutive_images)
- 트리거: 없음 — economic_poster._fix_consecutive_images(:283) 호출자 0. 실제 경로는 동명의 draft_fixer._fix_consecutive_images:57 (LLM 0)
- 빈도: 0회/일
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. ★ 부활 시 함수 진입만으로 무조건 2콜(조건 검사 전에 LLM 을 먼저 부르는 구조). 동명 함수가 두 파일에 있는 ① 단일 진입점 위반 동반
- 근거: grep _fix_consecutive_images → draft_fixer:57 정의·:288 호출 / economic_poster:283 정의만, 호출 0

## [기타] [죽은 코드] 본문 분량 초과 LLM 재작성 압축
- alias: writer_short_analysis (max_tokens 1600)
- 위치: shared/seo.py:199 (_claude_compress) ← compress_to_korean:88 ← length_manager.py:396 compress()
- 트리거: 없음 — length_manager.compress() 호출자가 저장소 전체에 0
- 빈도: 0회/일
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. 분량 초과는 현재 draft_fixer._fix_sentence_overflow(블록 잘라내기, LLM 0) + harness 재작성이 처리
- 근거: grep compress_to_korean → 정의·precommit 정규식·length_manager import 뿐, 실호출 0

## [발행품질] 글종류별 대본 섹션 구성 판정 (section_plan 선계산)
- alias: analyzer_posttype (max_tokens 2500)
- 위치: JARVIS02_WRITER/post_type_specs.py:245 (generate_section_plan) ← warm_section_plan:277
- 트리거: radar_trends_06(06:00) → topic_pack.build_topic_pack:301 → warm_section_plan. 발행 경로가 아닌 아침 팩 빌드
- 빈도: 후보 수만큼 = publish_slots(2) → 2회. 검증 실패 시 후보당 max_retries=2 → 최대 4회. 실측 07-28 4건(=2후보×2시도)
- 소비자: candidate['section_plan'] → collected.meta → draft_writer._build_economic_sections → 경제 대본 골격. generic 폴백이면 None 처리되어 그 콜은 버려짐
- 줄일여지: ★ 실측 4건 = 후보 2개인데 각 2회 → 절반이 검증 실패 재시도. _validate_section_plan 실패 원인을 잡으면 즉시 절반 절감(~20K/일). 추가로 후보 2개 중 실제 발행에 쓰이는 건 1개(NV/TS 가 수집 공유) → 나머지 1개분 통째로 미사용 가능. 위험: 낮음
- 근거: DB 7일 n=4 tot=39,360. post_type_specs.py:242 for 루프 확인

## [배경학습] 주간 SEO 학습 — 레퍼런스 분석 → 지침 도출
- alias: writer_long_learn (max_tokens 3500)
- 위치: JARVIS02_WRITER/seo_learner.py:96, 136 (run_seo_learning)
- 트리거: DEFAULT_JOBS weekly_seo_learn — cron 월 06:00
- 빈도: 주 1회 (같은 함수 안 2개 호출부). 프롬프트에 seo_reference_docs() 수집 원문 전량 동봉
- 소비자: ★ 조건부 — _parse_improvements → proactive_monitor 텔레그램 Finding 카드. 사용자가 ✅ 를 눌러야만 ReAct 가 seo_standards.py 수정. 미클릭이면 응답이 아무 데도 반영 안 됨
- 줄일여지: 승인율이 낮으면 사실상 소비자 0. 주 1회라 총량은 작음(background=True 라 발행창 자동 보류). 판단 필요: 최근 승인 이력이 0이면 잡 폐기 검토
- 근거: job_registry.py:123 cron 월 06:00. seo_learner.py:96·136 두 호출부

## [배경학습] ★ 디자인 레퍼런스 이미지 비전 분석 (Step1: 팔레트+레이아웃 추출) — 최근 최대 소비원
- alias: writer_long_learn (invoke_vision, source='vision') (max_tokens 3500 (선언값))
- 위치: JARVIS06_IMAGE/design_learner.py:522 (_extract_vision)
- 트리거: DEFAULT_JOBS j06_design_learn (cron 매일 05:00) → job_learn_design → Phase0A/0B → _learn_from_batch 루프
- 빈도: ★ 잡 1회당 최대 20회 — Phase0A batch1 n=5(:743) + batch2 n=10(:749) + Phase0B curated n=5(:767), 각 이미지마다 1회. 실측 07-28 19회 / 07-27 18회
- 소비자: ★ 실측 2일 연속 소비자 0 — 07-27 은 via='결정론'(HSL 색이론, LLM 0), 07-28 은 via='라이브러리'(코드 내장 레이아웃, LLM 0)로 커밋. 그날 채택된 레시피는 LLM 산출물이 아니었고 비전 응답 전량이 버려졌다
- 줄일여지: ★★ 최우선 절감 후보. 근거: ① 하루 0.9~1.3M 토큰을 써서 레시피 1개를 얻고, 그마저 2일 연속 결정론/라이브러리가 대신 커밋해 LLM 산출물 채택 0 ② 비용의 거의 전부가 이미지 자체(프롬프트 736자≈300토큰인데 호출당 ~65K) ③ 이 경로는 allowed_tools=['Read'] 라 invoke_text 의 도구차단(31,468→227) 최적화를 못 받는다 ④ Phase2 결정론이 '매일 +1개'를 이미 보장하므로 Phase0 을 건너뛰어도 규정이 안 깨진다. 예상절감: 장수 20→5 로 축소 시 ~0.7M/일, 주기 매일→주2회 병행 시 ~1.1M/일 (7일 기준 최대 1.9M 중 대부분). 위험: 레시피 다양성 정체 — 단 실측상 LLM 레시피가 채택된 적이 없어 현재 위험 ≈ 0
- 근거: design_learner.py:743·749·767 배치 장수 코드 확인. DB writer_long_learn 7일 n=45 tot=1,932,561 fail=15(33%) + vision alias n=9 tot=430,057 fail=3

## [배경학습] 디자인 학습 — 레이아웃 설명 → HTML 템플릿 생성 (Step2)
- alias: writer_long_learn (max_tokens 3500)
- 위치: JARVIS06_IMAGE/design_learner.py:551 (_generate_html_from_desc)
- 트리거: j06_design_learn → _analyze_reference:573 — Step1 이 layout_desc 를 뱉었을 때마다
- 빈도: Step1 통과 장수만큼 (최악 20회) × _retries=2. 실측 07-28 8회 / 07-27 9회
- 소비자: recipe['template'] → _validate_recipe/_test_render. 위와 같은 이유로 2일 연속 최종 채택 0
- 줄일여지: Step1 과 1:1 로 붙어 곱해진다 — 비전 20회면 최악 40 LLM 호출/회차. 자기가 유발한 스로틀에 자기가 막힘(같은 잡의 비전 레그가 버스트 예산 소진 직후 호출돼 대부분 거절, fail 33%). Step1 장수를 줄이면 Step2 성공률이 자동 상승 — 같은 뿌리라 별도 조치 불필요
- 근거: design_learner.py:551·573. DB writer_long_learn 45건 안에 혼재

## [배경학습] 디자인 레시피 LLM 창작 (Phase1 폴백)
- alias: writer_long_learn (max_tokens 700)
- 위치: JARVIS06_IMAGE/design_learner.py:250 (_generate_recipe)
- 트리거: j06_design_learn → Phase0A/0B 전량 탈락 시 for attempt in range(_max_attempts())
- 빈도: Phase0 전량 실패 시에만 최대 2회 (각 _retries=2 → 최악 4 spawn)
- 소비자: design_recipes.json. 2일 연속 탈락 → Phase2 라이브러리/결정론이 대신 커밋
- 줄일여지: 작은 편. Phase0·1 을 통째로 주 1회로 낮춰도 Phase2 가 '매일 +1 보장'이라 규정 불위반
- 근거: design_learner.py:781 for 루프 + _max_attempts()=harness SSOT 2

## [발행품질] 썸네일 배경 사진 프롬프트 + 색상 테마 창작
- alias: writer_short_title (max_tokens 200 (선언값))
- 위치: JARVIS06_IMAGE/thumbnail_maker.py:145 (_llm_thumbnail_params), 진입 :547
- 트리거: j01_economic_post / j01_theme_post_21 → draft_processor._mandatory_thumbnail:244 → generate_thumbnail → create_thumbnail:512
- 빈도: ★ 발행 1회당 2번(NV·TS 각 1) = 하루 4회 기대. 그러나 실측 7일 18건. 원인 확정: _PARAM_CACHE 는 실패 시 결과를 저장하지 않고 빈 dict 를 반환하는데(thumbnail_maker.py:159 근처, 캐시 저장은 result 가 참일 때만), _mandatory_thumbnail 이 3회 재시도하므로 LLM 실패 1회 = LLM 3회로 증폭
- 소비자: photo_prompt → Pollinations 사진 생성, color_theme → _COLOR_THEMES 선택. 실소비 확인. 실패 시 generic 프롬프트+random 폴백
- 줄일여지: ★ 두 겹 절감. ① 캐시 키를 (title,keyword) → (keyword,category) 로 바꾸면 NV/TS 가 공유해 4회→2회/일 ② 실패를 캐시하지 않아 재시도 3회가 LLM 3회가 되는 증폭 차단(negative caching). 근거: 7일 출력 총합이 1,269 토큰인데 입력은 159,788 = 오버헤드 126배. 예상절감: ~90K/7일. 위험: 낮음(NV/TS 썸네일 문구가 동일해짐 — 현재도 같은 주제라 실질 영향 작음)
- 근거: DB 7일 n=18 tot=159,788 out=1,269(!). thumbnail_maker.py:99 _PARAM_CACHE 키=(title,keyword) + :115 히트조건 `and _PARAM_CACHE[cache_key]` + draft_processor.py:245 for attempt in range(3)

## [발행품질] [꺼짐] designgen — LLM 이 인포그래픽 HTML 통짜 저작
- alias: writer_long_infographic (max_tokens 7000 (선언값 / alias 상한 11000))
- 위치: JARVIS06_IMAGE/infographic_engine.py:1109 (_designgen), 게이트 :1166
- 트리거: generate_infographic:1166 `if _DESIGNGEN_ON` — INFOGRAPHIC_DESIGNGEN=1 일 때만 (기본 0=OFF)
- 빈도: 현재 0회. 켜면 본문 이미지 1장당 1회 × min_images=5 × 플랫폼 2 = 발행당 10회+
- 소비자: 켜지면 _dg_verify_html grounding 검증 후 Chromium 렌더. 현재 소비자 0
- 줄일여지: 이미 꺼져 있음 (ERRORS [358] — 이미지당 수 분 latency 로 폐기). ★ 다시 켜지 말 것 — 발행당 10회+ 대형 호출 부활
- 근거: infographic_engine.py:961 `_DESIGNGEN_ON = os.getenv('INFOGRAPHIC_DESIGNGEN','0')=='1'`

## [기타] [죽은 코드] HTML+CSS 프리미엄 인포그래픽 생성 — 시스템 최대 max_tokens
- alias: writer_long_infographic (max_tokens 11000 (MODELS 최대))
- 위치: JARVIS06_IMAGE/html_infographic.py:340 (generate_html_infographic)
- 트리거: 없음 — image_agent.py:379 위임 래퍼조차 외부 호출자 0
- 빈도: 0회/일
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. ★ writer_long_infographic alias 전체가 사문화(designgen 도 OFF) — 이 alias 를 기준으로 세운 정책(회로차단 면제·발행창 보호 목록 등)이 있으면 실제와 어긋남
- 근거: grep generate_html_infographic → JARVIS06_IMAGE 밖 매칭 0행

## [기타] [죽은 코드] 인포그래픽 배치 설계 프라이밍
- alias: writer_short_visual (max_tokens 4000)
- 위치: JARVIS06_IMAGE/infographic_engine.py:556·568 (prime_batch_designs)
- 트리거: 없음 — 저장소 전역 호출자 0. __all__(:1297)에만 등재
- 빈도: 0회
- 소비자: ★ 소비자 0 — _BATCH_DESIGN_CACHE 를 채우는 유일한 함수인데 아무도 안 부르므로 generate_infographic:1174 의 캐시 조회는 항상 미스
- 줄일여지: 삭제 대상. 절감 0(안 돌므로)이지만 남겨두면 '16개→1콜 배치 최적화가 걸려 있다'는 오해를 낳는다 — 실제로는 배치 최적화가 존재하지 않는다
- 근거: grep prime_batch_designs → 정의(:540)·내부 report(:565)·주석·__all__ 뿐, 호출 0

## [발행품질] 인포그래픽 개별 설계(JSON) — design-selection
- alias: writer_short_visual (max_tokens 1600)
- 위치: JARVIS06_IMAGE/infographic_engine.py:444 (_llm_design), 게이트 :1180
- 트리거: 발행 경로 — generate_infographic:1180 3순위 폴백. 1순위 pro_templates.render_pro(결정론, LLM 0) 성공 시 미도달
- 빈도: 실측 0회. render_pro 실패 이미지 1장당 1회 (이론상 발행당 10회+)
- 소비자: render_spec(spec, datasets) 렌더러. 발화 자체가 없음
- 줄일여지: 이미 LLM 0(pro_templates 결정론이 100% 흡수). 손댈 필요 없음 — 다만 pro_templates 가 깨지면 즉시 발행당 10회+ 로 튀는 잠재 증폭점이라 render_pro 실패율 모니터 권장
- 근거: DB writer_short_visual 7일 0건. generate_infographic:1167-1180 순위 확인

## [발행품질] 이미지 설계서(spec) 생성 — 섹션 텍스트 → 차트 스펙
- alias: analyzer_imagespec (max_tokens 800)
- 위치: JARVIS06_IMAGE/image_spec.py:304 (generate_image_spec)
- 트리거: ★ 살아있음 — JARVIS02_WRITER/draft_fixer.py:259 _fix_image_count_underflow ← _route_fix:303 ('이미지 최소 미달'/'이미지 부족' 이슈)
- 빈도: 실측 0회. 발화 시 부족한 장수만큼 반복(draft_fixer.py:249 for 루프, 최대 5회) × 플랫폼 2 × 발행 2 = 이론상 하루 20회
- 소비자: render_from_spec(spec, dest) → 이미지 파일 → 본문 삽입
- 줄일여지: ★ 토큰보다 정책이 더 중요한 발견 — 2026-07-06 사용자 박제 '본문 이미지 = 실데이터 인포그래픽만, AI사진·폴백 전부 폐기' 와 정면 충돌하는 잔존 경로. draft_fixer 가 아직 LLM 설계 이미지를 끼워넣을 수 있다. 폐기 검토 대상
- 근거: draft_fixer.py:259 import + :303 라우팅 조건 확인. DB analyzer_imagespec 0건

## [발행품질] SVG 차트 코드 LLM 생성 (image_spec 3순위 렌더러)
- alias: analyzer_imagespec (max_tokens 3000)
- 위치: JARVIS06_IMAGE/svg_renderer.py:263 (_generate_svg), 호출 :235
- 트리거: image_spec.py:614 render_from_spec 3순위 (plotly → matplotlib 실패 후)
- 빈도: 실측 0회. 상위 generate_image_spec 이 안 돌아 도달 불가에 가까움
- 소비자: render_from_spec 반환 경로
- 줄일여지: —
- 근거: DB analyzer_imagespec 7일 0건

## [발행품질] matplotlib 차트 동적 색상 팔레트 생성
- alias: writer_short_visual (max_tokens 4000 (선언값, 실제 출력은 hex 5개))
- 위치: JARVIS06_IMAGE/matplotlib_renderer.py:103 (_get_dynamic_colors), 호출 :187
- 트리거: image_spec.py:604 render_from_spec matplotlib 렌더러
- 빈도: 실측 0회. 발화 시 차트 1장당 1회
- 소비자: 차트 색상. 실패 시 결정론 6색 폴백(seed 기반) 존재
- 줄일여지: 결정론 폴백이 이미 '토픽별 reproducible + 토픽 다르면 다른 색'을 만족 — LLM 없이도 헌법 제11조 취지 충족. 결정론 전환 후보(현재 0회라 실이득 0)
- 근거: DB writer_short_visual 7일 0건

## [발행품질] Plotly 차트 동적 색상 팔레트 생성
- alias: writer_short_visual (max_tokens 4000)
- 위치: JARVIS06_IMAGE/plotly_renderer.py:51 (_get_dynamic_palette_plotly), 호출 :71·347·377·412
- 트리거: image_spec.py:594 render_from_spec 1순위 렌더러
- 빈도: 실측 0회. 발화 시 ★ 차트 1장당 최대 4회 (호출 지점 4곳: 기본·scatter·waterfall·gauge)
- 소비자: Plotly 색상. 해시 seed 결정론 폴백 존재
- 줄일여지: 결정론 폴백으로 충분. 살아나면 차트당 4배 증폭 — 결정론 전환이 안전
- 근거: grep 호출 지점 4곳 확인. DB 0건

## [발행품질] 차트 스타일 스펙 창작 (레이아웃·색·폰트·섀도)
- alias: writer_short_visual (max_tokens 300)
- 위치: JARVIS06_IMAGE/style_engine.py:167 (generate_style_spec)
- 트리거: economic_charts.py:70 render_html_table_as_image ← block_assembler.py:84 <table> 블록의 2순위 폴백
- 빈도: 실측 0회. 발화 시 표 1개당 1회
- 소비자: ★ header_color/accent_color 두 값만 실제 사용(economic_charts.py:71-73). LLM 이 8개 필드를 만드는데 2개만 읽는다
- 줄일여지: 발화 시에도 8필드 중 2필드만 소비 = 프롬프트 대부분 낭비. 결정론 2색으로 대체 가능. 현재 0회라 실이득 없음
- 근거: DB 0건. economic_charts.py:71-73 소비 필드 확인

## [기타] [죽은 코드] 섹터/키워드 동적 색상 팔레트 생성
- alias: writer_short_visual (max_tokens 200)
- 위치: JARVIS06_IMAGE/style_engine.py:309 (generate_sector_colors)
- 트리거: 없음 — 저장소 전역 호출자 0. __all__(:374)에만 등재
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상
- 근거: grep generate_sector_colors → 정의(:290)·__all__(:374) 뿐

## [기타] [죽은 코드] 소제목 배너 동적 색상 1개 생성
- alias: writer_short_visual (max_tokens 50)
- 위치: JARVIS06_IMAGE/section_title.py:22 (_get_dynamic_color), 호출 :46
- 트리거: 없음 — 상위 make_section_title_image 의 외부 호출자 0 (jarvis_main.py:89 는 위임 래퍼일 뿐, 그 래퍼 호출자도 0)
- 빈도: 0회. 살아나면 소제목 배너 1장당 1회 → 글당 소제목 수만큼 곱
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. ★ 출력 50토큰인데 SDK 오버헤드로 입력 수천 토큰이 붙는 전형적 '작지만 안 싼' 호출. 결정론 팔레트로 충분한 영역
- 근거: grep make_section_title_image → 정의(:32)·__all__·위임래퍼(jarvis_main:89)·주석뿐

## [기타] [죽은 코드] 차트 캡션 1문장 LLM 생성 — 중복 정의
- alias: writer_short_visual (max_tokens 40)
- 위치: JARVIS06_IMAGE/theme_charts.py:77 (_cap) / 사본: JARVIS09_COLLECTOR/collect_theme.py:62 (alias=writer_short_analysis)
- 트리거: 없음 — collect_theme.py:98 이 theme_charts 의 _cap 을 import 해 로컬 정의(:53)를 덮어쓰는데, 덮어쓰는 쪽도 저장소 전역 호출자 0
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상 + ★ ① 단일 진입점 위반 동반 — 같은 LLM 함수가 두 폴더에 복사돼 있고 alias 도 서로 다르다(writer_short_visual vs writer_short_analysis)
- 근거: grep _cap → 두 정의 + import 덮어쓰기, 실호출 0

## [기타] [죽은 코드] 한국어 이미지 프롬프트 → 영어 변환
- alias: writer_short_analysis (max_tokens 400)
- 위치: JARVIS06_IMAGE/prompt_translator.py:48 (translate), 호출 image_agent.py:91·99
- 트리거: 없음 — 유일 상위 호출자 image_agent.generate_photo 의 외부 진입점이 _handle_bus_request(bus 'image.request')뿐인데, 그 이벤트를 발행하는 코드가 저장소에 0곳
- 빈도: 0회
- 소비자: ★ 소비자 0. 썸네일 사진 프롬프트는 thumbnail_maker._llm_thumbnail_params 가 처음부터 영어로 만든다(prompt_en 직결)
- 줄일여지: 삭제 대상. ★ JARVIS06 CLAUDE.md 원칙('한국어 프롬프트는 반드시 translate 로 변환')이 실제 코드와 어긋나 있다 — 문서 드리프트
- 근거: grep 'image.request' → subscribe(image_agent:309)·핸들러만, publish/emit 0

## [기타] [죽은 코드] Claude SVG 차트 생성 (KPI 카드·일반 차트)
- alias: writer_short_visual (max_tokens 4000)
- 위치: JARVIS06_IMAGE/providers/claude_svg_provider.py:137 (generate)
- 트리거: 없음 — 호출자 image_agent.generate_chart ← _handle_bus_request(image.request 'chart'). 이벤트 발행 0
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상
- 근거: 위와 동일 버스 근거

## [발행품질] 경제 주제 후보 프로필+적합성 배치 생성 (키워드 단독 전송 금지 이행)
- alias: analyzer (★ 구 alias 잔존) (max_tokens 2000 (호출 인자))
- 위치: JARVIS03_RADAR/topic_pack.py:145 (_profile_batch), 진입 :232·423·438
- 트리거: radar_trends_06 → job_collect_trends_morning → build_topic_pack_once → build_topic_pack. 추가: pick_slot_candidate:409 소진복구 재빌드 · keyword_profile:423 ← theme_picker.theme_topic:78
- 빈도: 기본 1회/일 + build_topic_pack_once 재시도 최대 2 + 소진복구 슬롯당 1 + 테마 collect_all 경유 1. 2026-07-26 이전엔 09/12/15 도 팩을 재빌드해 4배였음
- 소비자: topic_pack candidates → 02 경제 대본(주제·프로필) + 09 collect_all 프로필. fail-closed — 빈 응답이면 팩 None → 경제 발행 차단
- 줄일여지: ★ 두 가지. ① 프로필 대상은 publish_slots+8=10개 후보인데 팩에는 상위 2개만 박제(build_topic_pack:186 final=selected[:publish_slots]) → 최소 8개분이 버려진다(단 fit=false 대비 버퍼라 순손실은 아님) ② 구 alias 'analyzer' 를 계속 써서 대시보드가 evidence_pack._label_batch 와 구분 불가 — analyzer 10.6M 의 내역 분해가 불가능한 근본 원인. 전용 alias 분리가 선행돼야 정확한 절감 판단 가능
- 근거: grep 호출부 4곳 확인. DB analyzer 7일 n=285 tot=10,617,284 $27.90 — 1위 alias

## [기타] ★ 트렌드 키워드 콘텐츠 각도·제목·훅 생성
- alias: writer_short_analysis (max_tokens 1024)
- 위치: JARVIS03_RADAR/analyzer.py:724 (generate_content_angles), 호출 radar_main.py:335
- 트리거: DEFAULT_JOBS radar_trends_06/09/12/15 (cron 06·09·12·15시) → job_collect_trends → radar_main subprocess → collect_today:335
- 빈도: 하루 4회 (수집 회차마다 1회). 한 프롬프트에 recs 10개 + top_scored 최대 15개 = 최대 25키워드
- 소비자: ★ 소비자 0 — angle·hook 은 analyzer.py:740-741 에서 recs 에 채워지고 radar_main.py:346 content_angles 로 trends_YYYY-MM-DD.json 에 저장되는데, 저장소 전체에서 content_angles/extra_angles 를 *읽는* 코드가 0. 대시보드 RecommendItem 타입에도 angle/hook 없음. topic 필드만 /radar 텔레그램에서 1줄 표시
- 줄일여지: ★★ 소비자 0 이 가장 명확한 건. 하루 4회 × 25키워드 프롬프트가 통째로 버려진다. 예상절감: writer_short_analysis 26회/7일 중 이 경로가 최대 28회(4×7) — 실측 tot 280,046 의 상당분. 게다가 CLAUDE_RADAR.md 2026-07-26 개정('09/12/15 는 수집만, 팩 재빌드 없음')과 함께 보면 이 4회 전부가 소비자 없는 잔존물. 위험: 없음(읽는 코드가 0). 조치: generate_content_angles 호출 삭제 또는 06시 1회로 축소
- 근거: grep content_angles / extra_angles → 생성부(radar_main:346·364-365)만 존재, 소비 0행. 대시보드 node_modules 외 매칭 0

## [배경학습] 미분류 트렌드 키워드 섹터 배치 분류
- alias: writer_short_analysis (max_tokens 512)
- 위치: JARVIS03_RADAR/analyzer.py:302 (_classify_with_llm), 호출 :442
- 트리거: radar_trends_06/09/12/15 → radar_main.collect_today → analyzer.score_keywords:442 (sector=='기타' 또는 저신뢰 매칭이 1개라도 있을 때만)
- 빈도: 최대 4회/일 (수집 1회당 배치 1회). analyzer.py:299 for attempt in range(_max_attempts()) → 파싱 실패 시 최대 2회. 규칙 분류로 전부 잡히면 0회
- 소비자: scored_keywords[].sector → topic_pack._candidates 섹터 조인 + 대시보드 섹터 분포
- 줄일여지: ★ 중복 질문 — topic_pack._profile_batch(위)가 같은 키워드에 대해 'sector 교정'을 또 한다(topic_pack.py:147). 섹터 판정이 두 LLM 호출에 걸쳐 있다. 한쪽으로 통합 가능. 위험: 낮음(둘 다 같은 판정)
- 근거: analyzer.py:299·302·442 확인

## [배경학습] 일일 종합 분석 — 그날 발행글 묶음 → 개선 인사이트
- alias: analyzer_quality (max_tokens 2500)
- 위치: JARVIS03_RADAR/daily_review.py:376 (_call_claude), 호출 :481
- 트리거: DEFAULT_JOBS daily_review (cron 22:00) → job_daily_review → run_daily_review:481
- 빈도: post_type 그룹 수만큼 (economic·theme 2회/일). 실측 7일 2건 — 잡은 7회 돌았는데 LLM 은 2회뿐
- 소비자: learning_insights 테이블 → post_quality_analyzer._build_learning_block:104 (judge_engagement·루브릭 제안 system 에 상위 8개 주입) + /api/learning 대시보드
- 줄일여지: —
- 근거: DB analyzer_quality 7일 n=2 tot=6,882. job_registry.py:63 cron 22:00

## [배경학습] 발행 후 100점 루브릭 채점 → before→after 개선안
- alias: writer_short_analysis (max_tokens 1500)
- 위치: JARVIS03_RADAR/post_quality_analyzer.py:184 (_analyze_by_rubric)
- 트리거: DEFAULT_JOBS analyzer_fb (interval 5분) → job_analyzer_fallback → post_quality_analyzer subprocess (글당 1 Popen)
- 빈도: 발행 글 1건당 1회 = 4회/일. 루브릭 감점 0이면 :169 에서 LLM 없이 조기 반환. 5분 간격이지만 pending 있을 때만 + bg_defer_reason 발행창 보류
- 소비자: suggestions → 텔레그램 승인 버튼 → learn_from_suggestions:582 → learning_insights → 다음 글 프롬프트
- 줄일여지: —
- 근거: job_registry.py:50 interval. post_quality_analyzer.py:169 조기반환

## [배경학습] 발행 후 매력도·유익성 5축 채점
- alias: engagement_judge (max_tokens 600)
- 위치: JARVIS03_RADAR/post_quality_analyzer.py:299 (judge_engagement)
- 트리거: _analyze_by_rubric:158 안 (analyzer_fb 경로). ★ 발행 *전* 게이트는 이 함수를 안 쓴다 — prepublish_gate 가 fact+engagement 를 fact_judge 한 번에 합쳐 부른다
- 빈도: 발행 글 1건당 1회 = 4회/일. :296 for _attempt in range(2) 파싱 실패 재시도
- 소비자: post_scorer Section A 점수 → quality_score → save_analysis_result → ADR 014 quality_learner 보상
- 줄일여지: ★ 문서 드리프트 — CLAUDE_WRITER.md 는 '두 레그 ② 매력도 = post_quality_analyzer.judge_engagement' 를 발행 전 레그로 기술하나, 실제 발행 전 경로는 _combined_quality_call 단독. 토큰 절감이 아니라 문서 정정 사안
- 근거: DB engagement_judge 7일 n=6 tot=103,904. prepublish_gate.py:479 가 fact_judge 단일 통합 호출임을 확인

## [발행품질] CTA 문구 동적 생성
- alias: writer_short_cta (max_tokens 120)
- 위치: JARVIS03_RADAR/post_quality_analyzer.py:372 (_pick_cta)
- 트리거: _rule_based_analysis:433 — 루브릭 LLM 분석이 예외로 실패했을 때의 규칙 폴백 경로에서만
- 빈도: 정상 경로 0회. 실측 7일 1회
- 소비자: 규칙 폴백 suggestion 의 after 필드 → 텔레그램 제안 목록
- 줄일여지: ★ 출력 57토큰에 8,740토큰 소비 = 오버헤드 153배 — 시스템 최악의 토큰 대비 산출 비율. 다만 7일 1회라 절대량은 무시 가능. '작은 max_tokens 가 싼 호출을 뜻하지 않는다'의 대표 증거
- 근거: DB writer_short_cta 7일 n=1 tot=8,740 out=57

## [발행품질] 수집 코퍼스 dense digest 압축
- alias: analyzer_evidence (max_tokens 6000)
- 위치: JARVIS09_COLLECTOR/evidence_pack.py:181 (build_corpus_digest)
- 트리거: collector_engine.collect_research:404(with_digest=True) ← _collect_research_leg:557 ← collect_all. 잡: j02_theme_precollect(20:00) / radar_trends_06 경제 선계산
- 빈도: collect_all(use_cache=False) 1회당 1회 = 경제 2 + 테마 1 = 3회/일. 발행창이면 evidence_pack.py:167 is_publishing() 에서 즉시 '' 반환 → 0회. 입력 = 문서 전건 × 각 8,000자
- 소비자: collected.meta['corpus_digest'] → draft_writer.py:1186 대본 프롬프트 코퍼스 블록
- 줄일여지: ★ digest 는 '작성기 프롬프트를 줄이려고' LLM 을 하나 더 태우는 구조인데, 압축으로 아끼는 writer 토큰 vs 압축 비용의 순손익 근거가 코드에 없다. 게다가 cache_selfcheck 가 [C1] analyzer_evidence 재사용 0.00배 = 캐시가 순손실(+39,567/7일) 로 판정 → cache=False 로 바꾸면 즉시 ~40K/7일 절감. 위험: 없음(캐시만 끄는 것)
- 근거: DB analyzer_evidence 7일 n=6 tot=84,392 cache_read=0

## [발행품질] 수집 문서 → fact 배치 추출
- alias: analyzer_evidence (max_tokens 4800 (호출))
- 위치: JARVIS09_COLLECTOR/evidence_pack.py:207 (_extract_facts_batch), 호출 :334(Pass-1)·342(Pass-2)
- 트리거: collector_engine.collect_research:388(with_facts=True) ← _collect_research_leg ← collect_all
- 빈도: ★ 팩 1개당 최대 2회 (Pass-1 공식API T1 → 15개 미달이면 Pass-2 뉴스·웹 보충). collect_all 1회당 1~2회 → 3~6회/일. 입력 = 문서 최대 20개 × 900자
- 소비자: evidence_brief → 대본 프롬프트 근거(최대 60 fact), facts_to_datasets → 차트 dataset, prepublish 사실성 대조
- 줄일여지: 이미 배치 통합 완료(ERRORS[374]). 캐시는 위와 동일하게 순손실 판정 — cache=False 검토
- 근거: evidence_pack.py:334·342 2-Pass 확인. ERRORS[374] 로 문서별→전문서 단일 호출 통합됨

## [발행품질] stat fact → 차트 축 라벨 배치 작명
- alias: analyzer (★ 구 alias 잔존) (max_tokens 800)
- 위치: JARVIS09_COLLECTOR/evidence_pack.py:449 (_label_batch), 호출 :569
- 트리거: facts_to_datasets:569 ← collector_engine.py:493 compose_collected ← collect_all
- 빈도: collect_all 1회당 1회(stat fact 존재 시) = 3회/일. 입력 = 문장 각 90자컷
- 소비자: dataset title → JARVIS06 인포그래픽 축 라벨. 실패해도 statement[:14] 폴백이라 무손실
- 줄일여지: ★ 폴백이 statement[:14] 문자열 자르기라 실패 시 품질 저하가 거의 없다 = 결정론 대체 후보. 그리고 구 alias 'analyzer' 를 써서 7일 최다 소비 alias(10.6M)의 내역 분해를 막고 있다 — analyzer_* 분화 목적과 어긋남
- 근거: evidence_pack.py:449·569 확인. analyzer alias 를 topic_pack._profile_batch 와 공유

## [발행품질] 데이터 소싱 설계도 생성 (ADR 012 조타수)
- alias: analyzer_plan (max_tokens 1200)
- 위치: JARVIS09_COLLECTOR/data_planner.py:225 (plan_data_sources)
- 트리거: ① chart_data.warm_plan:584 ← topic_pack.build_topic_pack:290 (아침 저부하창) ② collect_chart_data:1555 plan_cache 미제공 시
- 빈도: warm_plan 은 for c in candidates 루프 = 후보 2개 → 2회/일. :224 for _attempt in range(2) 백오프 재시도 → 주제당 최대 2회. 발행창(JARVIS_LLM_DEADLINE_TS)에선 :211 에서 무조건 스킵
- 소비자: series 설계 → chart_data 조준 수집 쿼리·출처 순서. 실패해도 _fallback_plan 결정론 스캐폴드가 받침
- 줄일여지: —
- 근거: DB analyzer_plan 7일 n=2 tot=7,229

## [발행품질] 주제 → 공식통계 검색용 동의어 확장 (KOSIS 정식명 탐색)
- alias: analyzer_chart (max_tokens 120)
- 위치: JARVIS09_COLLECTOR/chart_data.py:521 (_expand_theme)
- 트리거: ① warm_synonyms:559 ← topic_pack.build_topic_pack:271 ② collect_chart_data:1543 synonyms 미제공 시
- 빈도: warm_synonyms 는 for t in themes 루프 = 후보 2개 → 아침 2회. ★ synonym_cache.json 영구 캐시 히트 시 0회(:509)
- 소비자: chart_data 검색 쿼리 + _ref_tokens 관련성 토큰 + data_planner 폴백 쿼리
- 줄일여지: —
- 근거: chart_data.py:509 캐시 조건 확인

## [발행품질] 차트 dataset 배치 추출 + 관련성 판정 (전 항목 1회)
- alias: analyzer_chart (max_tokens 4000)
- 위치: JARVIS09_COLLECTOR/chart_data.py:1344 (_batch_extract_all), 호출 :1668
- 트리거: collect_chart_data:1668 ← collector_engine._collect_charts_leg:543 ← collect_all. CATEGORY_POLICY 상 collect_charts=True 는 economic 뿐
- 빈도: collect_chart_data 1회당 1회. 경제 선계산이 슬롯 2개를 돌아 2회/일
- 소비자: datasets → compose_collected → JARVIS06 인포그래픽 + 본문 수치 + image_data_verifier 사실성 근거
- 줄일여지: ★ 두 가지. ① cache_selfcheck 실행 결과 [C1] analyzer_chart 재사용 0.20배 = 캐시가 순손실(+69,702 토큰/7일) → cache=False 로 즉시 ~70K/7일 절감, 위험 0 ② 프롬프트가 두 폴더 통틀어 최대 — pending 최대 _BATCH_CAP=24개 × 항목당 문서 8개 × 발췌 4000자 = 이론상 입력 768K자. CHART_BATCH_DOCS·EXCERPT_CHARS 는 env 노브지만 _BATCH_CAP=24 는 코드 상수라 조정 불가
- 근거: DB analyzer_chart 7일 n=4 tot=109,105 (호출당 27,276) cache_create=84,775 cache_read=16,748

## [기타] [죽은 코드] 차트 series 단건 추출 (문서 전문 투입, 입력 상한 없음)
- alias: analyzer_chart (max_tokens 700)
- 위치: JARVIS09_COLLECTOR/chart_data.py:963 (_extract_series_from_docs)
- 트리거: 없음 — 유일 호출자 _collect_one_series(:1423) 가 저장소 전체에서 호출 0
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. ★ :955 주석이 '문서 8개 상한 폐지 + 1500자컷 폐지' 를 명시 = 입력 토큰 무제한 구조. 남아 있으면 다음 작업자가 배치 추출 대신 이 단건 경로를 되살릴 위험
- 근거: grep '_collect_one_series(' → 정의(:1423) 1행뿐, 호출 0

## [기타] [죽은 코드] 차트 dataset 관련성 게이트
- alias: analyzer_chart (max_tokens 200)
- 위치: JARVIS09_COLLECTOR/chart_data.py:1155 (_relevance_filter 내부)
- 트리거: 없음 — _relevance_filter(:1110) 호출자 0. collect_chart_data 주석이 '관련성 게이트는 BATCH 추출 시 relevant=true/false 로 이미 처리' 명시
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: 삭제 대상. ★ 임베딩 선판정(_embed_prefilter, 로컬 MiniLM·토큰 0)까지 이 죽은 함수 안에 묶여 있어 그 절감 로직도 함께 죽어 있다
- 근거: grep '_relevance_filter(' → 정의(:1110) 1행뿐

## [발행품질] 테마 종목 7개 선정 (1차)
- alias: router (★ 용도-이름 불일치) (max_tokens 1000)
- 위치: JARVIS09_COLLECTOR/collect_theme.py:854 (collect_stocks_data)
- 트리거: collector_engine._collect_stocks_leg:526 ← collect_all(category='theme'). CATEGORY_POLICY 상 collect_stocks=True 는 theme 뿐
- 빈도: ★ :846 for attempt in range(3) — 7개를 못 채우면 부족분만큼 재요청해 테마 1개당 최대 3회. 테마 선계산(20:00) 1회 → 최대 3회/일
- 소비자: stocks_data → 테마 대본 재무표·차트 + prepublish_gate._stock_facts_leg 재무밴드 검증
- 줄일여지: ★ ERRORS [540] 이 '테마 종목 폴백이 router alias 를 빌려 써 대시보드가 거짓말했다'며 collect_theme_fallback 을 신설했는데 **:854 는 아직 router 를 쓴다** — 같은 병이 한 줄 남았다. JARVIS01 마스터 라우터와 장부에서 섞여 귀속 분리 불가. 토큰 절감은 아니지만 계측 정합 수정 필요
- 근거: collect_theme.py:846 for 루프 확인. DB router 7일 n=3 (전량 텔레그램 ReAct 로 보임)

## [발행품질] 네이버 금융 공식 테마 의미매칭 폴백
- alias: router (★ 동일 문제) (max_tokens 1000)
- 위치: JARVIS09_COLLECTOR/collect_theme.py:420 (_naver_fin_theme_search), 호출 :758·976
- 트리거: collect_stocks_data ← collect_all(theme). 결정론 매칭 best_score<3 일 때만 진입
- 빈도: 테마 collect_all 1회당 최대 2회 (:758 사전매칭 + :976 6차폴백). 프롬프트에 네이버 공식 테마 266개 통짜 목록 포함
- 소비자: _naver_pre_pairs → 종목 시드 + 공식 테마 게이트(THEME_OFFICIAL_ONLY) — 미매칭이면 발행 중단·테마 교체
- 줄일여지: 266개 테마 목록을 매 호출 통짜로 넣는다 — 임베딩 선매칭(로컬 MiniLM, 토큰 0)으로 후보를 20개로 줄인 뒤 LLM 에 넘기면 입력 대폭 축소. 위험: 낮음. alias 도 router → collect_theme_* 로 정정 필요
- 근거: collect_theme.py:758·976 두 호출부

## [발행품질] 테마 종목 4차 극완화 폴백
- alias: collect_theme_fallback (max_tokens 1000)
- 위치: JARVIS09_COLLECTOR/collect_theme.py:890
- 트리거: collect_theme.py:884 — 1차 3회 시도가 전부 0개일 때만
- 빈도: 조건부 1회. 실측 7일 0회
- 소비자: stocks_data pairs → 테마글 종목 구성 (data_empty 로 인한 테마 교체 방지)
- 줄일여지: —
- 근거: DB collect_theme_fallback 7일 0건

## [발행품질] 테마 종목 5차 IPO·대표주 폴백
- alias: collect_theme_fallback (max_tokens 1000)
- 위치: JARVIS09_COLLECTOR/collect_theme.py:957
- 트리거: 4차도 0개일 때 (:955)
- 빈도: 조건부 1회. 실측 7일 0회
- 소비자: stocks_data pairs
- 줄일여지: —
- 근거: DB 0건

## [발행품질] 네이버 발행 보조 LLM 호출
- alias: writer (★ 구 alias 잔존) (max_tokens 8000 (spec 상속))
- 위치: JARVIS08_PUBLISH/platforms/naver_poster.py:135 (_inv_cli)
- 트리거: j01_economic_post / j01_theme_post_21 → harness Layer4 send → post_to_naver
- 빈도: 네이버 발행 1회당 1회 = 하루 2회 (경제·테마). tags is None 일 때만 발화 — 실측상 경제에서만
- 소비자: 네이버 발행 태그 처리
- 줄일여지: ★ 구 alias 'writer' 사용 → 대시보드에 '본문 작성'으로 오표시. 짧은 태그 호출인데 8000 spec 을 상속. writer alias 는 7일 5.67M/$27.94 로 2위 소비원인데 05시 최다(디자인학습 혼재)라 '발행 alias' 로 보고 세운 보호정책이 빗나간다. writer_short_* 로 분화 필요
- 근거: naver_poster.py:134-135 확인. DB writer 7일 n=88 tot=5,668,213 $27.94 fail=34(39%)

## [발행품질] 티스토리 발행 보조 LLM 호출
- alias: writer (★ 구 alias 잔존) (max_tokens 8000)
- 위치: JARVIS08_PUBLISH/platforms/tistory_poster.py:120 (_inv_cli)
- 트리거: harness Layer4 send → post_to_tistory
- 빈도: 티스토리 발행 1회당 1회 = 하루 2회
- 소비자: 티스토리 발행 태그 처리
- 줄일여지: 네이버와 동일 — 구 alias 잔존 + max_tokens 과대 상속
- 근거: tistory_poster.py:119-120 확인

## [감사진단] GUARDIAN 심층감사 1부 — backlog 오류 Tier-2 LLM 분석·패치 생성
- alias: guardian (max_tokens 8000 (선언값) / 호출 timeout=120s)
- 위치: JARVIS07_GUARDIAN/error_analyzer.py:132 (analyze_llm_only), 호출 guardian_agent.py:934
- 트리거: DEFAULT_JOBS j07_deep_audit (cron 토 03:00) → job_deep_audit 1부 deep_audit_backlog(limit=40, max_llm=15). 별도로 j07_retry_pending(10분) → _orchestrate Tier1 실패 시
- 빈도: 주 1회 잡, run 당 LLM 최대 15회. invoke_text 내부 재시도 2 → SDK spawn 최대 30. 실측 7일 90건(잡이 매일이던 07-21~26 기간, 하루 11~18)
- 소비자: guardian_agent.py:935 apply_fix(mark_wontfix=True) → 실제 파일 패치 + record_pattern_hit/Bandit 보상
- 줄일여지: ★ 90행 중 66행(73%)이 0토큰·turns=0·duration=0 = timeout 120s 벽시계에 걸려 강제 포기된 호출. 상류에서 소비는 됐는데 산출물 0. timeout 을 늘리거나 max_llm 을 줄이는 양자택일 필요. 잡은 이미 매일→토 주1회로 축소 적용됨(07-26 이후 기록 0) → 다음 토요일(08-01) 실측 필요. 위험: 축소 시 backlog 재처리 지연(단 신규 오류는 j07_retry_pending 10분이 계속 처리)
- 근거: DB guardian 7일 n=90 tot=3,406,243 $5.04 fail=8. job_registry.py:214 cron sat 03:00

## [감사진단] GUARDIAN 심층감사 2부 — 저장소 광범위 코드 감사 (SDK 직접, 도구 사용)
- alias: sdk:shared.claude_sdk_compat.run_sdk_query (model=model_id('guardian')) (max_tokens SDK 세션 (max_turns=60))
- 위치: JARVIS07_GUARDIAN/auto_repair.py:525 (_step_run_cli), 함수 run_auto_repair():444, 호출 guardian_agent.py:1161
- 트리거: DEFAULT_JOBS j07_deep_audit (토 03:00) 2부
- 빈도: 주 1회 1건. harness max_attempts 미지정=2 상속 → SDK 세션 ≤2/주. (07-26 이전엔 매일 = 주 7회)
- 소비자: ★ 산출물 8회 연속 0건 — self_repair_runs id 96~104(07-18~26) 전부 total_fixed=0. 기계 소비자는 없고 텔레그램 요약뿐이며 그 내용도 '이상 없음' 반복
- 줄일여지: ★★ 절감 후보 상위. ① 8연속 산출 0건인데 max_turns=60·timeout 1200s 세션을 계속 돌린다 ② ★ 2026-07-26 절감(c3309fd)이 이 통로엔 미적용 — shared/llm.py:1287 주석이 '적용 범위: invoke_text 경로만, run_sdk_query 는 건드리지 않는다' 명시. cwd=ROOT 이므로 CLAUDE.md 계열 ≈49K + 도구 정의 ≈31K 가 매 세션 자동 로드. 프롬프트 본문(_BASE_PROMPT)은 2,585자뿐 = 비용의 대부분이 '봉투' ③ 예상절감: 주 1회로 이미 축소돼 주당 ~0.4M 추정, 잡 폐기 시 전액. 위험: 아직 안 난 문제 발굴이 사라짐 — 단 8회 연속 0건이라 실효 위험 낮음
- 근거: auto_repair.py:525 run_sdk_query. max_turns=60, timeout=1200s, permission_mode=bypassPermissions. CLAUDE.md 박제: 단건 최대 439K·10턴

## [감사진단] GUARDIAN 표적 자가수리 (발행 실패·잔류오류 즉시수정, SDK 직접)
- alias: sdk:shared.claude_sdk_compat.run_sdk_query (model=model_id('guardian')) (max_tokens SDK 세션 — ★ max_turns 미지정(상한 없음), timeout=600s)
- 위치: JARVIS07_GUARDIAN/auto_repair.py:812, 함수 run_auto_repair_targeted():771. 호출자 guardian_agent.py:478 · incident_responder.py:290
- 트리거: A: j07_retry_pending(interval 10분) → status='new' 최대 20건 병렬 → Tier1 실패 시 Tier2. B: bus ERROR_DETECTED. C: 발행 실패 → scheduler.py:476·1022 incident_responder.respond_in_background
- 빈도: 이론상 하루 144run × 20건 = 2,880 진입이나 실측 7일 10회(≈1.4/일). 게이트 7겹: is_transient / 발행중 보류 / try_claim_error 선점 / provisional / bg_defer_reason / is_auto_fixable / MAX_LLM_ATTEMPTS=3 / CB_MAX_HOUR=10
- 소비자: files_fixed>0 → auto_repair.py:858 record_sdk_fix() → learn_eval 채점 → learned_patterns + Bandit 보상 + _retry_original_job() 원 잡 재시도
- 줄일여지: ★ ① 토큰 장부에 단 1행도 없다 — run_sdk_query 계측(claude_sdk_compat.py:200)이 2026-07-26 에야 추가돼 source='sdk_query' 행이 전 기간 0건. **소비량을 모른 채 돌고 있다** ② 위와 같은 봉투 문제(cwd=ROOT → CLAUDE.md 49K + 도구 31K) ③ max_turns 상한이 없다(전수감사는 60인데 targeted 는 무제한) — 한 건이 timeout 600s 를 다 태울 수 있다. 조치: max_turns 상한 부여가 최우선(위험 없음)
- 근거: job_registry.py:218 interval 10분. auto_repair.py:771·812

## [배경학습] 학습 자산화 게이트 — 패치 안전성·정확성·재사용성 채점
- alias: learn_eval (max_tokens 300 (호출 override — 실제 미적용))
- 위치: JARVIS07_GUARDIAN/eval_agent.py:774 (_evaluate_llm_patch), 게이트 진입 pattern_fixer.py:1249
- 트리거: 잡 아님 — 이벤트 구동. record_pattern_hit() 안에서 fixer_name 이 llm_patch 집합일 때만. 경로: error_fixer.py:1540 · pattern_fixer.py:1417 · error_collector.py:882 · auto_repair.py:413
- 빈도: 오류 수정 1건당 1회. 실측 7일 24회 — 07-25 하루 19회, 그중 17:11~17:24 13분간 12회로 몰림
- 소비자: pattern_fixer.py:1256 `if not _eval.should_register: return 0` — learned_patterns.json 등록 여부를 실제로 결정 + eval_meta 박제
- 줄일여지: ★ 출력 300토큰 O/X 판정에 호출당 54,669 토큰. 본문(error_type+message 200자+traceback 400자+patch 2,000자)은 1K 미만인데 봉투가 30배 이상. 07-21 단건 224,841(5턴)·07-25 108,641(3턴)처럼 도구를 돌려 폭증한 케이스가 있는데 O/X 판정에 도구는 불필요. 2026-07-26 c3309fd 로 봉투가 227까지 줄었으나 이후 호출 0건이라 개선 실측 미확보. 예상절감: 봉투 제거 반영 시 ~1.0M/7일. 위험: 없음
- 근거: DB learn_eval 7일 n=24 tot=1,312,061 out=41,988 cache_read=1,078,640 $1.91

## [감사진단] [LLM 0 — 문서가 거짓말] 주간 헌법·드리프트 감사
- alias: 없음 (auditor.py:24 docstring 이 'audit_refine alias' 를 주장하나 MODELS 에 없고 코드에 LLM 호출 0행) (max_tokens 해당 없음)
- 위치: JARVIS07_GUARDIAN/auditor.py:24 (허위 기재) / 본체 :117·171·256·292 전부 정적 검사
- 트리거: DEFAULT_JOBS auditor_weekly (cron 일 04:30)
- 빈도: 주 1회. LLM 호출 0회
- 소비자: 텔레그램 보고 + audit DB. LLM 응답 소비자는 애초에 없음
- 줄일여지: 토큰 0. 다만 docstring 이 있지도 않은 LLM 호출을 선언해 절감 대상을 오판하게 만든다 — 문서 드리프트 정정 필요
- 근거: auditor.py 전수 grep invoke_text → 0행

## [기타] 마스터 라우터 1-step 인텐트 분류 (ReAct 실패 시 fallback)
- alias: router (max_tokens 1000 (★ 이 경로는 chat() 어댑터라 max_tokens 가 실제 적용됨))
- 위치: JARVIS01_MASTER/router.py:86·96-101 (_node_classify), 2차 raw JSON 재시도 :112
- 트리거: 텔레그램 자유 문장 → bot.py:163 _route00 (_run_react 가 False 반환 시). /route 명령도 동일. 크론 없음
- 빈도: 자유 문장 1건당 1~2회. ReAct 성공 시 0회
- 소비자: 인텐트 → dispatchers.execute_safe / describe_approval → 사용자
- 줄일여지: 프롬프트에 capabilities.render_for_router_prompt() 카탈로그 전량이 매번 박힌다(:82-83). 대화 빈도가 낮아 총액은 작음
- 근거: router.py:86 llm.chat('router') → with_structured_output

## [기타] ReAct 라우터 — LLM↔도구 다단계 왕복
- alias: router (max_tokens 1000)
- 위치: JARVIS01_MASTER/router.py:489-494 (_react_agent_node)
- 트리거: 텔레그램 자유 문장 → bot.py:152 _run_react(text, max_steps=12) · bot.py:686 /react. 크론 없음
- 빈도: ★ 대화 1건당 최대 12회 LLM 호출(max_steps=12, :663 상한 체크). 도구 왕복마다 누적 messages 전체 재전송 → 뒤로 갈수록 커짐. 실측 7일 3행 = 1개 대화
- 소비자: 사용자 (텔레그램 응답)
- 줄일여지: ★ 빈도는 낮지만 단가가 극단적 — 대화 1건 ≈ 585K = 디자인학습 반나절치. 원인: 매 step 마다 all_langchain_tools() 전체 스키마 + REACT_SYSTEM_PROMPT + capability 카탈로그 + 누적 메시지를 재전송. ★ 이 경로는 llm.py:1266-1268 이 도구 스키마를 system 에 주입하므로 도구차단 최적화의 명시적 예외 — 줄이려면 max_steps 12 → 6 축소가 유일한 지렛대. 위험: 복잡한 요청 미완료
- 근거: DB router 7일 n=3 tot=585,693 = 호출당 195,231. bot.py:152 max_steps=12

## [기타] ask_claude 도구 — 자유 문장 응답
- alias: writer_long_chat (max_tokens 3000 (도구 기본) / alias 8000)
- 위치: JARVIS01_MASTER/agent_tools.py:992 (+1003 예외 폴백)
- 트리거: ReAct 가 도구로 선택했을 때만 (side_effect='none')
- 빈도: 실측 0회. ReAct 대화 1건 안에서 0~N회 (max_steps 12 안)
- 소비자: ReAct 다음 step → 사용자
- 줄일여지: 예외 폴백(:1000-1004)이 같은 alias 로 재호출해 실패 시 2배. 빈도 0 이라 실이득 없음
- 근거: DB writer_long_chat 8일 0건

## [기타] delegate_to_claude_code — Claude Code SDK 위임 (경로 ②)
- alias: sdk:shared.claude_sdk_compat.run_sdk_query (model=model_id('guardian')) (max_tokens SDK 세션 (max_turns 기본 20, 상한 50 / timeout 600s, 상한 1800s))
- 위치: JARVIS01_MASTER/agent_tools.py:878
- 트리거: ReAct 도구 (side_effect='external', requires_approval=True) → 텔레그램 인라인 버튼 ✅ 후 _execute_j00_react_approval
- 빈도: 실측 0회 관측. 승인 1건당 SDK 세션 1개, 세션 내부 최대 20턴
- 소비자: stdout 30,000자 절단 후 ReAct 다음 step → 사용자
- 줄일여지: 단발이지만 단가 최대급(같은 통로로 j07_deep_audit 이 단건 439K·10턴을 쓴 전례가 CLAUDE.md 에 박제). 도구 차단 미적용(도구가 목적이라 정당). ★ 이 통로도 guardian 모델·미계측이라 alias 귀속이 auto_repair 와 섞일 수 있음
- 근거: agent_tools.py:878-880 run_sdk_query(model=model_id('guardian'))

## [기타] ARCHITECT — 사용자 의도 파싱
- alias: writer_short_analysis (max_tokens 800)
- 위치: JARVIS00_INFRA/architect.py:659 (_parse_intent)
- 트리거: architect.design 인텐트 — 사용자 자유 문장. 크론 없음. 경로: dispatchers.py:261 / infra_agent.py:237 / agent_tools.py:1285
- 빈도: 호출 1건당 1회. 실측: docs/architect/ 산출물이 2026-05-31 1건뿐 → 약 2개월 0회
- 소비자: _generate_spec 의 parsed_summary 입력 → 기획서 마크다운
- 줄일여지: ★ MODELS 에 architect alias(max_tokens=10000, background=True)가 있는데 architect.py 는 그걸 안 쓰고 writer_short_analysis/coder 를 쓴다 — alias 이름-용도 드리프트. 온디맨드라 상시 비용 0
- 근거: docs/architect/ 파일 1건. DB 해당 시각 기록 0

## [기타] ARCHITECT — 기획서 마크다운 산출
- alias: coder (max_tokens 8000, timeout 600s)
- 위치: JARVIS00_INFRA/architect.py:699 (_generate_spec)
- 트리거: architect.design 인텐트. 크론 없음
- 빈도: 호출 1건당 1회. 실측 약 2개월 0회
- 소비자: docs/architect/{date}_{slug}.md + 텔레그램 → 사용자
- 줄일여지: 온디맨드지만 단건이 크다 — system 에만 컨텍스트 14,000자, 출력은 15단계×15소단계 전량 요구(:684-687). architect alias 가 있는데 coder 를 씀(드리프트). 상시 비용 0
- 근거: DB coder 8일 0건

## [기타] ARCHITECT — exec_plan(write_file JSON 배열) 생성
- alias: coder (max_tokens 3000)
- 위치: JARVIS00_INFRA/architect.py:967 (_generate_exec_plan_from_spec)
- 트리거: architect.design — spec_md 의 Stage 14(또는 §12) 존재 시
- 빈도: 호출 1건당 최대 1회. 실측 0회
- 소비자: 인라인 버튼 승인 후 create_plan 실행 계획
- 줄일여지: 온디맨드, 상시 0
- 근거: DB 0건

## [기타] [고아 alias] diagnostic — 저장소에 호출 지점 0
- alias: diagnostic (max_tokens 6000)
- 위치: shared/llm.py:284 (MODELS 정의만)
- 트리거: 없음 — 저장소 전체에서 invoke_text('diagnostic') 매칭 0
- 빈도: 0회
- 소비자: ★ 소비자 0
- 줄일여지: MODELS 에서 삭제 대상. 토큰 0 이지만 alias 26종 중 8종이 8일 0건(writer_long_infographic·writer_long_chat·writer_short_visual·collect_theme_fallback·analyzer_imagespec·coder·architect·diagnostic)이고 그중 diagnostic 만 호출 지점 자체가 없다
- 근거: grep invoke_text('diagnostic' → 0행. shared/llm.py:284 정의 + job_llm_priority.py 주석뿐

## [기타] [시스템 전역 결함] max_tokens 노브가 SDK 에 전달되지 않음
- alias: sdk:shared.llm._run_sdk_sync (전 alias 공통) (max_tokens 해당 없음)
- 위치: shared/llm.py:1250-1290 (_opts_kw 조립) / invoke_text:1668 **overrides
- 트리거: invoke_text / invoke_text_result 를 쓰는 모든 호출 (= 위 표의 거의 전부)
- 빈도: 해당 없음 — 구조 결함
- 소비자: 해당 없음
- 줄일여지: ★★ 판단의 전제를 바꾸는 발견. MODELS 의 max_tokens 는 **전부 라벨이지 상한이 아니다**(invoke_text 경로 한정). 실측 반증: writer_short_analysis 평균 출력 6,133 vs 선언 1,600 / analyzer_evidence 7,469 vs 6,000 / analyzer 10,492 vs 2,500. 결론: 'max_tokens 를 낮춰 토큰을 줄인다'는 계획은 **무효**. 실제 지렛대는 ① 호출 횟수 ② 입력(봉투+프롬프트) ③ 캐시 ④ 결정론 대체 넷뿐. 조치: _opts_kw 에 max_tokens 전달을 추가하거나(효과 확인 필요), 안 되면 MODELS 주석에 '라벨' 임을 명시해 오판 차단
- 근거: _opts_kw = {'model','env'} + cwd + system_prompt + disallowed_tools 만(llm.py:1250-1290). ClaudeCodeOptions(**_opts_kw) 에 max_tokens 없음. invoke_text(:1668)는 max_tokens 를 **overrides 로 받아 invoke_text_result 로 넘기고 거기서 소멸. llm.py:392 는 LangChain chat() 어댑터 전용(router 만 사용)

## [기타] [토큰 0 — 혼동 방지] 임베딩 (로컬 MiniLM CPU)
- alias: 없음 (LLM 아님) (max_tokens 해당 없음)
- 위치: shared/embeddings.py — vector_store·오류 시맨틱 매칭·RADAR 검색·bandit·QA 검색
- 트리거: j07_vector_backfill(일 02:30) · repair_history.incidents_brief · chart_data._embed_prefilter 등
- 빈도: 해당 없음
- 소비자: 벡터 검색 결과
- 줄일여지: ★ 절감 대상 아님 — API 토큰 0. 단 조건부 주의: shared/style.py:45 `if os.getenv('VOYAGE_API_KEY')` 이면 유료 voyage-3-lite 로 전환된다. 현재 .env 에 키 없음(local_minilm 경로). 키가 생기면 keyword_embed_backfill·index_keyword_embedding·_embed_prefilter 가 전부 과금 경로가 됨
- 근거: sentence-transformers 로컬 CPU. llm_token_usage 에 0행 남기는 것이 정상(누락 아님)

## [기타] [토큰 0 — 혼동 방지] Pollinations / Nanobana 이미지 생성
- alias: 없음 (외부 이미지 REST API) (max_tokens 해당 없음)
- 위치: JARVIS06_IMAGE/providers/pollinations_provider.py ← thumbnail_maker.py:245
- 트리거: 발행 경로 — 썸네일 생성 (플랫폼별 독립 실행, 캐시 없음)
- 빈도: 발행 1회당 2번 × 각 최대 2회 재시도 = 하루 최대 8회
- 소비자: _apply_editorial → 폴라로이드 프레임 임베드 → 썸네일
- 줄일여지: ★ Claude 토큰 0 — 절감 목록에 넣지 말 것. 지연시간(8초 대기·429 재시도)은 비용이지만 토큰은 아니다. Nanobana 는 Google 할당량 별도 축
- 근거: pollinations_provider 는 HTTP 이미지 요청. llm_token_usage 무관

