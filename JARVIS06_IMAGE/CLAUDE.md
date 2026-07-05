# JARVIS06_IMAGE 비직관 규칙

## 핵심 원칙
0. **★ 시간축 좌→우 강제 (사용자 박제 2026-07-03)**: 시간·기간 라벨(연도·월·분기·날짜)이 있는 모든 차트·인포그래픽은 *과거 → 최근* 순서 (예: 2025년 좌, 2026년 우). 단일 진입점 `image_spec.enforce_time_axis_ltr()` — `render_from_spec`·`infographic_engine.generate_infographic` 양쪽에서 렌더 직전 자동 교정 + spec 생성 프롬프트에도 명시. 새 렌더 경로 추가 시 이 함수 경유 의무. 카테고리 라벨(비시간)은 무변경 (80% 파싱 임계).
1. 한국어 프롬프트는 반드시 `prompt_translator.translate()` 로 영어 변환 후 제공자에 전달
2. **★ 사진 프로바이더 (ERRORS [263] 박제 2026-06-07)**: Pollinations.ai 단일 사용. Bing/HuggingFace 완전 삭제 — Bing 쿠키 무한 만료 + HuggingFace DNS 차단 반복. 차기 1순위로 Nanobana(Gemini) 도입 예정.
3. SVG 차트 오버레이는 Claude LLM 동적 생성 — 고정 템플릿·스타일 풀 절대 금지
3-B. **★ 썸네일 = 주제 대표 AI 실사 + 에디토리얼(폴라로이드) (사용자 박제 2026-07-05 — ERRORS [356])**: 대표 썸네일은 *주제를 한눈에 알아보는 실사*(지역화폐→돈, 반도체→웨이퍼)를 폴라로이드 프레임에 임베드 + PIL 오버레이. 단일 경로 `thumbnail_maker.create_thumbnail → _create → _apply_editorial`. **저품질 SVG 인포그래픽 썸네일(`_generate_svg_thumbnail`) 완전 폐기 — 재도입 금지.** 폴백 순서는 반드시 *품질 순*(AI사진→그라디언트→matplotlib). 하단 카테고리 태그는 `tag_line` 동적(하드코딩 금지). 데이터 인포그래픽(본문)과 대표 실사(썸네일)는 용도가 다름.
3-C. **★ 본문 인포그래픽 = 결정론 전문 템플릿 (사용자 박제 2026-07-05 — ERRORS [357][358])**: `infographic_engine.generate_infographic` 1순위는 `pro_templates.render_pro` — *전문 디자인을 코드에 박제*(팔레트 5종 seed 회전·데이터형태 자동판별·히어로 밴드·듀오톤 라인·랭킹 막대·도넛)하고 검증 실데이터만 꽂아 **LLM 0회·5.4초** 렌더. 수치는 코드가 실데이터로 채움 → 조작 불가. **LLM 실시간 HTML 저작(`_designgen`)은 이미지당 수 분 latency(SDK 스로틀)로 폐기 → opt-in(`INFOGRAPHIC_DESIGNGEN=1`, 기본 OFF)**. 폴백 순서: pro_templates → (opt-in)design-gen → render_spec(손코딩 스펙). **교훈: 디자인 품질은 LLM 실시간 생성이 아니라 코드 템플릿에 박제. LLM은 데이터 수집·검증에만.** 새 데이터 형태 추가 시 `pro_templates.build_html` 분기 확장(다른 파일에 렌더 로직 신설 금지).
3-D. **★ 인포그래픽 디자인 나이틀리 강화학습 (사용자 박제 2026-07-05 — ERRORS [359])**: 오류학습과 동형 — `design_learner.job_learn_design`(DEFAULT_JOBS `j06_design_learn`, 05:00)이 매일 새 전문 디자인 레시피 1개를 **게이트 통과분만** `design_recipes.json` 누적 → `pro_templates._pick_palette`가 기본+학습 소비 → 다양성 복리 상승. **모델 파인튜닝 아님**(불가) — *검증 통과 코드 자산 누적*.
   - **학습 소스 3단(폴백)**: ① Phase0 = **실제 사이트 이미지 세밀 학습** — `_fetch_reference`(Playwright Bing, requests.get 금지) 로 후보 수집 → **비전 관련성 게이트**(`invoke_vision` = `shared/llm` SDK Read 도구, 인포그래픽 아니면 reject) → 세밀 분석으로 팔레트·스타일·notes 추출. ② Phase1 = LLM 지식기반 창작. ③ Phase2 = **결정론 색이론**(`_generate_recipe_deterministic`, LLM 0).
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
13. **★ 이미지 데이터 사실성 (ERRORS [287] / ADR 010 — 2026-06-29)**:
    - **차트 수치는 JARVIS09 실데이터로만**. `from JARVIS09_COLLECTOR import collect_chart_data` 로 주제 연관 실데이터(출처 박제) 수집 → 그 데이터로 차트 생성. *본문에서 숫자 짜내기 금지*.
    - **검증 단일 진입점**: `validators/image_data_verifier.verify_chart_spec(spec, datasets)`. 검증분 재구성 → 0개면 실데이터 대체 → 그것도 없으면 숫자 없는 카드 폴백. `render_from_spec` 이 provenance 레지스트리 기록(트립와이어).
    - 다른 파일에 차트 데이터 사실성 로직 박지 말 것. 검증: `grep -rn 'def verify_chart_spec' JARVIS06_IMAGE | grep -v image_data_verifier` → 0행.
14. **★ 차트 스타일 단일 진입점 의무 (ERRORS [139][169][175] 3회 반복 박제 — 2026-05-26)**:
    - **matplotlib 차트**: 모든 함수 최상단에서 `setup_chart_defaults()` 1회 호출 필수. 함수 내 `fontsize=` 하드코딩 금지 — `CHART_STYLE["FONT_*"]` 상수 사용.
    - **Plotly 차트**: `_base_layout()` 사용 (font=16 이상, title=28). `_derive_colors()`로 채도 0.45~0.65 범위 컬러 사용 — 직접 hex 하드코딩 금지.
    - **신규 차트 파일 추가 시**: `from JARVIS06_IMAGE.style_engine import setup_chart_defaults, CHART_STYLE` 먼저 추가 후 작성.
    - **검증**: `grep -rn 'fontsize=[0-9]' JARVIS06_IMAGE/*.py | grep -v '# style_engine\|CHART_STYLE'` → 0행이어야 함.

## 파일 구조
| 파일 | 역할 |
|------|------|
| `image_agent.py` | 공개 API (`generate_photo / generate_chart / generate_thumbnail`) + `register()` |
| `prompt_translator.py` | 한국어 → 영어 변환 (shared.llm 위임) |
| `thumbnail_maker.py` | Claude 동적 썸네일 (bg 프롬프트 창작 → AI 사진 → SVG 오버레이) |
| `section_title.py` | matplotlib 소제목 배너 이미지 |
| `trend_charts.py` | 트렌드 키워드 차트 + 썸네일 |
| `economic_charts.py` | 경제 브리핑 차트 + 썸네일 |
| `providers/pollinations_provider.py` | Pollinations.ai REST 호출 (키 불필요 — 현재 단일 프로바이더) |
| `providers/claude_svg_provider.py` | Claude LLM → SVG 동적 생성 → PNG 변환 |

## 외부에서 호출 방법 (유일한 합법 패턴)
```python
from JARVIS06_IMAGE.image_agent import generate_photo, generate_chart, generate_thumbnail
from JARVIS06_IMAGE.providers.pollinations_provider import PollinationsProvider  # 영어 프롬프트 있을 때만
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
