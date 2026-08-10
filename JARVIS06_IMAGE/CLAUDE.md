# JARVIS06_IMAGE 비직관 규칙

## 핵심 원칙
0. **★ 시간축 좌→우 강제 (사용자 박제 2026-07-03)**: 시간·기간 라벨(연도·월·분기·날짜)이 있는 모든 차트·인포그래픽은 *과거 → 최근* 순서 (예: 2025년 좌, 2026년 우). 단일 진입점 `image_spec.enforce_time_axis_ltr()`. ★ 2026-08-10: 정렬 키는 **행 메타 `as_of`** 에서 파생한다 — 종전엔 라벨 문자열을 재파싱했는데 '달러/원 환율 2026.08.07' 3행이 전부 같은 키 `(2026,0,0)` 로 읽혀 **무력**이었고, 그 탓에 환율이 실제 -8.7% 하락인데 `+8.6% ▲` 로 인쇄됐다(진실을 행에 실어 놓고 소비자가 문자열을 읽으면 안 된다 — ②원칙). 렌더 경로는 `infographic_engine.generate_infographic` 하나뿐이므로 그 안에서 자동 교정된다(`render_from_spec` 은 삭제됨). 카테고리 라벨(비시간)은 무변경 (80% 파싱 임계).
1. 한국어 프롬프트는 반드시 `prompt_translator.translate()` 로 영어 변환 후 제공자에 전달
2. **★ 사진 프로바이더 — Cloudflare Workers AI 단독 (사용자 결정 2026-08-05 — ERRORS [574])**: `providers/cloudflare_provider.py` Flux-1-Schnell. 무료 10,000 neuron/일 = 하루 약 173장. **Pollinations 완전 삭제**(39개 모델 전부 유료화 402). Nanobana(Gemini)는 공식 가격표상 이미지 모델 전부 `Free Tier: Not available` — 도입하지 않는다. **프로바이더를 둘 이상 두지 말 것** — 둘이면 한쪽만 고치는 사고가 난다(실제로 이 교체 중 `thumbnail_maker` 를 빠뜨려 ③원칙 위반).
3. SVG 차트 오버레이는 Claude LLM 동적 생성 — 고정 템플릿·스타일 풀 절대 금지
3-B. **★ 썸네일 = 주제 대표 AI 실사 + 에디토리얼(폴라로이드) (사용자 박제 2026-07-05 — ERRORS [356])**: 대표 썸네일은 *주제를 한눈에 알아보는 실사*(지역화폐→돈, 반도체→웨이퍼)를 폴라로이드 프레임에 임베드 + PIL 오버레이. 단일 경로 `thumbnail_maker.create_thumbnail → _create → _apply_editorial`. **저품질 SVG 인포그래픽 썸네일(`_generate_svg_thumbnail`) 완전 폐기 — 재도입 금지.** 폴백 순서는 반드시 *품질 순*(AI사진→그라디언트→matplotlib). 하단 카테고리 태그는 `tag_line` 동적(하드코딩 금지). 데이터 인포그래픽(본문)과 대표 실사(썸네일)는 용도가 다름.
3-C. **★ 본문 인포그래픽 = 결정론 전문 템플릿 (사용자 박제 2026-07-05 — ERRORS [357][358])**: `infographic_engine.generate_infographic` 1순위는 `pro_templates.render_pro` — *전문 디자인을 코드에 박제*(팔레트 5종 seed 회전·데이터형태 자동판별·히어로 밴드·듀오톤 라인·랭킹 막대·도넛)하고 검증 실데이터만 꽂아 **LLM 0회·5.4초** 렌더. 수치는 코드가 실데이터로 채움 → 조작 불가. **LLM 실시간 HTML 저작(`_designgen`)은 이미지당 수 분 latency(SDK 스로틀)로 폐기 → opt-in(`INFOGRAPHIC_DESIGNGEN=1`, 기본 OFF)**. 폴백 순서: pro_templates → (opt-in)design-gen → render_spec(손코딩 스펙). **교훈: 디자인 품질은 LLM 실시간 생성이 아니라 코드 템플릿에 박제. LLM은 데이터 수집·검증에만.** 새 데이터 형태 추가 시 `pro_templates.build_html` 분기 확장(다른 파일에 렌더 로직 신설 금지).
3-D. **★ 인포그래픽 디자인 나이틀리 강화학습 (사용자 박제 2026-07-05 — ERRORS [359])**: 오류학습과 동형 — `design_learner.job_learn_design`(DEFAULT_JOBS `j06_design_learn`, 05:00)이 매일 새 전문 디자인 레시피 1개를 **게이트 통과분만** `design_recipes.json` 누적 → `pro_templates._pick_palette`가 기본+학습 소비 → 다양성 복리 상승. **모델 파인튜닝 아님**(불가) — *검증 통과 코드 자산 누적*.
   - **학습 소스 3단(폴백)**: ① Phase0 = **실제 사이트 이미지 세밀 학습 (5→10 단계 캡처, 사용자 박제 2026-07-06)** — 1차 `_fetch_reference(n=5)` 로 5장 캡처 → **비전 관련성 게이트**(`_analyze_reference`/`invoke_vision`, 인포그래픽 아니면 reject) 통과분 1개 추출(`_learn_from_batch`); 1차에 인포그래픽 0개면 2차 *새* 10장(`exclude_urls` 로 1차 URL 중복 금지) 캡처 후 동일 추출. requests.get 금지(Playwright Bing). ② Phase1 = LLM 지식기반 창작. ③ Phase2 = **결정론 색이론**(`_generate_recipe_deterministic`, LLM 0).
   - **★ 1회 학습 필수 보장 (사용자 강조)**: 실이미지·LLM 실패해도 Phase2 결정론이 매일 +1 보장. "조용히 스킵" 금지.
   - 게이트 = `_validate_recipe`(대비·채도·독창성) + `_test_render`(실렌더). 레퍼런스 복제 금지(원본 합성·저작권). 새 스타일 노브 추가 시 recipe schema + `_validate_recipe` + `build_html` 렌더 동시 갱신.
4. **★ 차트/그래프 색상은 매번 LLM으로 새로 생성** (고정 팔레트 금지) — 동일 스타일 반복 시 독자가 AI 감지 → SEO 저품질
5. **★ 같은 글 내 색상 추적 필수** — 같은 글의 여러 시각화가 같은 색상/스타일이면 안 됨. `exclude_colors` 파라미터로 제어
6. `prompt_en=` 파라미터: Claude가 영어 프롬프트를 직접 생성한 경우 번역 생략
7. 캐시 금지 — 매번 신선한 변환 및 생성
8. 타임아웃: Pollinations 30초, Claude 기본값
9. **상세 규정**: JARVIS02_WRITER/BLOG_SUPREME_LAW.md (제11~12조) 참조
10. **★ 이미지 유일성 (ERRORS 10회 반복 박제)**: 파일명에 내용 해시 포함 필수 (`hashlib.md5(섹션텍스트).hexdigest()[:8]`). 프롬프트에 섹션 텍스트 맥락 반영 필수. 파일명 고정 또는 프롬프트 고정 시 같은 이미지 반복 삽입 발생.
11. **★ 외부 이미지 API 순차 실행 (ERRORS 16회 반복 박제)**: `max_workers=1` (순차 실행) 강제. 병렬 실행(max_workers≥2) 절대 금지 — 429 오류 전부 실패 직결. Pollinations 요청 간 8초+ 대기. 재시도 로직 필수 탑재.
12. **★ 실데이터 없으면 차트 스킵 (ERRORS [44][70][87][139][161][172][175][178][182] 10회 반복 박제 — 2026-05-30)**:
    - **합성/가상 데이터로 차트 생성 금지**. 실데이터 획득 실패 시 `return ""` (빈 문자열) — 거짓 차트 > 차트 없음.
    - **금융 지수·주식**: yfinance 실데이터 레이어 먼저. 합성 fallback 금지.
    - **scatter/area/line**: 시계열·2D 전용 — 횡단면 종목 비교에 사용 금지.
    - **min()/max() guard**: 진입 최상단 빈 데이터 guard 필수. `if not x_vals or not y_vals: return ""`
    - **검증**: `grep -rn '_synth_data(' JARVIS06_IMAGE/*.py | grep -v '^.*def _synth_data' | grep -v __pycache__` → 0행이어야 함 (함수 정의 제외, 호출만 검사).
13. **★ 이미지 데이터 사실성 (ERRORS [287] / ADR 010 — 2026-06-29, ★ 2026-08-10 전면 개정)**:
    - **차트 수치는 JARVIS09 실데이터로만**. *본문에서 숫자 짜내기 금지*.
    - **검증·판정 단일 진입점 = `validators/image_data_verifier.py` 단독.** 공개 API:
      `verify_chart_spec` · `chart_fit`(차트형) · `additive_total`(가산성) · `row_provenance`(행별 출처·시점)
      · `dataset_admissible`(출처 등급 승인) · `grounding_pool` / `verify_rendered_html`(표시 수치 대조)
      · `certify_image`(**레지스트리 쓰기 유일 경로**) · `verifier_effective`(스모크) · `MIN_ROWS` · `DATA_IMAGE_ATTR`.
    - **★ 단일 초크포인트 `infographic_engine._emit`** (사용자 박제 2026-08-10):
      `generate_infographic` 의 반환은 **정확히 두 꼴** — `return ""` 과 `return _emit(...)`.
      픽셀을 낳는 모든 경로(render_pro·designgen·render_spec·render_single)가 `_emit` → `certify_image`
      를 지나 검증 + provenance 등록을 받고, **미검증이면 이미지를 폐기**한다(거짓 차트 < 차트 없음).
      · *왜*: 종전엔 반환이 4갈래라 그중 하나(render_pro)만 등록을 빠뜨려도 아무도 몰랐고,
        실제로 2026-08-10 경제 브리핑 8장 전부가 무검증·미등록으로 발행됐다.
    - **가산성은 기본이 '불가'** — `ds["totals"]`(출처가 공표한 합계)가 있을 때만 합계를 표시한다.
      단위 화이트리스트를 만들지 말 것. 판정은 *꼴*(`/`·`=` 구분자)과 데이터에 실린 증거(as_of·basis·category)로만.
    - **표시 뷰는 `template_engine.view_rows` 단독** — 히어로·차트·검증이 같은 행을 본다
      (종전엔 히어로 8행 / 막대 7행으로 같은 이미지 안에서 검산이 깨졌다). 절단 상한은
      `pro_templates.BAR_MAX_ROWS`/`DONUT_MAX_ROWS`/`KPI_MAX_CARDS` 단독 보유.
    - **출처 문자열 생산자는 `template_engine.source_label` 단독** — `render_layout` 에 `src` 인자가 없다.
      호출자가 문자열을 넣을 수 있으면 헤드라인·내부 식별자 배제 가드가 통째로 우회된다.
    - **레이아웃 템플릿의 고정 표시문구는 `template_engine.template_literals`(꼴 판정)로 차단** —
      어휘 블랙리스트 금지. 생성(`design_learner._validate_recipe`)·저장(`_save_registry`)·
      렌더 편입(`pro_templates._style_pool`) 3곳에서 본다(오염 4건이 JSON 직접 커밋이었다).
    - 기계 강제: `python3 shared/precommit_check.py --category image`
      (`self-check`·`chokepoint-single-exit`·`provenance-write-outside`·`assembler-drift`·
      `sibling-drift`·`display-literal`·`recipe-literal`·`j06-style`).
14. **★ 차트 스타일 단일 진입점 의무 (ERRORS [139][169][175] 3회 반복 박제 — 2026-05-26)**:
    - **matplotlib 차트**: 모든 함수 최상단에서 `setup_chart_defaults()` 1회 호출 필수. 함수 내 `fontsize=` 하드코딩 금지 — `CHART_STYLE["FONT_*"]` 상수 사용.
    - **Plotly 차트**: `_base_layout()` 사용 (font=16 이상, title=28). `_derive_colors()`로 채도 0.45~0.65 범위 컬러 사용 — 직접 hex 하드코딩 금지.
    - **신규 차트 파일 추가 시**: `from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE` 먼저 추가 후 작성.
    - **검증**: `grep -rn 'fontsize=[0-9]' JARVIS06_IMAGE/*.py | grep -v '# style_engine\|CHART_STYLE'` → 0행이어야 함.

## 파일 구조
| 파일 | 역할 |
|------|------|
| `image_agent.py` | 공개 API (`generate_photo / generate_thumbnail`) + `register()`. ★ `generate_chart` 는 2026-08-10 **삭제** — certify_image 초크포인트를 지나지 않는 우회로였다 |
| `infographic_engine.py` | ★ **데이터 인포그래픽 단일 진입점** `generate_infographic` + 초크포인트 `_emit` |
| `prompt_translator.py` | 한국어 → 영어 변환 (shared.llm 위임) |
| `thumbnail_maker.py` | Claude 동적 썸네일 (bg 프롬프트 창작 → AI 사진 → SVG 오버레이) |
| `section_title.py` | matplotlib 소제목 배너 이미지 |
| `trend_charts.py` | 트렌드 키워드 차트 + 썸네일 |
| `economic_charts.py` | 경제 브리핑 차트 + 썸네일 |
| `providers/cloudflare_provider.py` | Cloudflare Workers AI REST 호출 (무료 티어 — **단일 프로바이더**) |
| ~~`providers/claude_svg_provider.py`~~ | **고아 (호출자 0)** — 유일 소비자 `image_agent.generate_chart` 가 2026-08-10 삭제되며 끊겼다. `providers/__init__` 재export 도 제거됨(우연한 배선 차단). 파일 삭제는 별건 |

## 외부에서 호출 방법 (유일한 합법 패턴)
```python
from JARVIS06_IMAGE.image_agent import generate_photo, generate_thumbnail
from JARVIS06_IMAGE.infographic_engine import generate_infographic   # 데이터 차트는 이것 하나뿐
from JARVIS06_IMAGE.providers.cloudflare_provider import CloudflareProvider  # 영어 프롬프트 있을 때만
```

## 이관 의무 (★ 즉시 — 예외 없음)
- 다른 파일에서 `https://image.pollinations.ai` 직접 URL 발견 즉시 이관
- 다른 파일에서 PIL 이미지 생성·matplotlib 이미지 함수 신규 추가 시 이관
- 신규 이미지 생성 함수는 *반드시* 이 폴더 안에만 추가

## 검증 명령
```bash
# ① 외부 이미지 생성 호출
grep -rnE 'https://image\.pollinations\.ai' --include='*.py' .. | grep -v JARVIS06_IMAGE/ | grep -v __pycache__
# ② 고정 팔레트 상수 (모두 삭제되어야 함 — 0행)
grep -rnE '^_PALETTES|^COLORS\s*=' --include='*.py' . | grep -v '_get_dynamic_colors'
```
모두 0행이어야 함.
