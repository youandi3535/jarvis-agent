"""JARVIS02_WRITER/length_manager.py — ★ 문장수·글자수 단일 진입점 (★ 사용자 박제)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
★ 단일 진입점 원칙 (강제 — 예외 없음)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
시스템 내 *모든* 문장수·글자수 상수·헬퍼는 이 파일에서만 관리한다.

금지:
  - 다른 .py 파일에 문장수·글자수 숫자 하드코딩 (예: "3문장(약 150자)", 상수 int = 3)
  - `[가-힣]{N,M}` 정규식 직접 사용 (length_manager.py 외)
  - `text[:N]` 본문 자르기 직접 호출

발견 즉시:
  - 상수 → 이 파일로 이관 + 호출자는 `from JARVIS02_WRITER import length_manager as _L` 로 교체
  - 글자수 표현 → `_L.build_length_phrase(min, max=None)` 로 교체

구조 정책:
  - 상한·하한·target 은 `JARVIS02_WRITER/post_type_specs.PostTypeSpec` 에서 자동 도출
  - 새 글 종류 추가 = `post_type_specs.POST_TYPE_SPECS` 에 섹션 list 1개 추가
  - 분량은 *결과*, 구조는 *본질*. 직접 박지 말 것.

표기 표준 (BLOG_SUPREME_LAW 제8-B조):
    build_length_phrase(5)    → "5문장(약 250자)"
    build_length_phrase(5, 6) → "5~6문장(약 250~300자)"
"""
from __future__ import annotations
import os
import re
from typing import Iterable

# ★ post_type_specs 단일 진실 소스 — 모든 분량은 spec 에서 도출
from JARVIS02_WRITER.post_type_specs import get_spec as _get_spec

# economic / theme 기본 spec — alias 도출 시 사용
_SPEC_ECON = _get_spec("economic")
_SPEC_THEME = _get_spec("theme")

# ══════════════════════════════════════════════════════════════
# ★ 분량 정책 — post_type_specs 위임. 옛 하드코딩 상수 *제거*.
# 호출자가 변수명 그대로 사용 가능 (호환 보장). 변경 시 post_type_specs.py 만 수정.
# ══════════════════════════════════════════════════════════════

KOREAN_PER_SENTENCE: int = 50   # 1문장 ≈ 50자 (spec.target_korean 산출 기준)

# ── 전체 글 정책 (economic 기본) — post_type_specs.PostTypeSpec 위임 ─────
# ★ ERRORS [140] — 섹션 자체 동적. 옛 spec.total_images / section_sentences 박제 제거.
TARGET_SENTENCES:        int = _SPEC_ECON.target_sentences
TARGET_KOREAN:           int = _SPEC_ECON.target_korean
MIN_SENTENCES_THRESHOLD: int = _SPEC_ECON.min_sentences
# 본문 이미지 fallback — spec 에 박지 않은 *기본 추정값* (대략 max_images / 1.5)
MIN_SVG_COUNT:           int = max(4, _SPEC_ECON.max_images // 2)

# ── 도입부·마무리·단락 (제0조·제0-B조·마무리) ───────────────
INTRO_SENTS_MIN:    int = 3      # 도입부 최소 문장
INTRO_SENTS_MAX:    int = 4      # 도입부 최대 문장
INTRO_SENTS:        str = "3~4"  # prompt 표시용 (하위 호환)
OUTRO_SENTS:        int = 2      # 마무리 (산술용)
MAX_P_SENTS:        int = 2      # 제0-B조 — 한 <p> 최대 문장 수

# ── 섹션 분량 범위 (generate_naver 등 — 동적 배분이 아닌 범위 표현용) ──
SEC_SENTS_MIN:      int = 3      # 섹션 최소 문장 수
SEC_SENTS_MAX:      int = 6      # 섹션 최대 문장 수

# ── 제4조 배치 규정 임계값 (★ 사용자 박제 2026-05-19) ─────────────────
# 이미지 없이 연속 가능한 최대 단락 수. 이 값을 초과하면 이미지 강제 삽입 (자동 또는 경고).
# 정책: BLOG_SUPREME_LAW 제4조 허용 패턴 — 최대 연속 문단 2개 (패턴3·4).
# → 3 단락 이상 연속 = 위반.
MAX_CONSECUTIVE_PARAGRAPHS_WITHOUT_IMAGE: int = 2   # 초과(3+) 시 위반

# ── 차트·이미지 개수 — 문장수 기반 동적 계산 (하드코딩 금지) ─────────────
# ★ 사용자 박제 2026-07-05 (8→5): 썸네일 제외 최소 이미지 5장 (디폴트 아닌 절대 최솟값)
MIN_IMAGES: int = _SPEC_ECON.min_images   # 5 — 썸네일 제외, 모든 글 공통

# 예: TARGET_SENTENCES=30(목표), MAX_CONSECUTIVE=2 → 30÷3=10 → MIN_CHART_COUNT=10
MIN_CHART_COUNT: int = max(
    MIN_SVG_COUNT,
    TARGET_SENTENCES // (MAX_CONSECUTIVE_PARAGRAPHS_WITHOUT_IMAGE + 1),
)
MAX_CHART_COUNT: int = _SPEC_ECON.max_images  # 절대 상한 (post_type_specs 위임)

_INTRO_SENTS = INTRO_SENTS_MIN
_OUTRO_SENTS = OUTRO_SENTS

# ── 제0조 감성 도입부 (★ 사용자 박제 2026-05-15 — 3→4문장 갱신) ───
# 패턴: <p>2문장</p> + [섹션 이미지 ①] + <p>2문장</p>
HUMAN_INTRO_SENTS:  int = 4                                          # 4문장
HUMAN_INTRO_CHARS:  int = HUMAN_INTRO_SENTS * KOREAN_PER_SENTENCE    # 약 200자 (파생)

# ── 제5조 면책·주의 수칙 (★ 사용자 박제) ─────────────────────
DISCLAIMER_SENTS:        int = 2                                          # 2문장
DISCLAIMER_KOREAN:       int = DISCLAIMER_SENTS * KOREAN_PER_SENTENCE     # 약 100자 (파생)
DISCLAIMER_INLINE_SENTS: int = 1    # 인라인 면책 1문장 (outro 끝 단독 삽입)

# ── 경제 브리핑 섹션별 (economic_poster.py 전용) ────────────
ECO_GREETING_SENTS:       int = 3   # 3문장(약 150자) — 도입부 감성문단
ECO_HIGHLIGHT_SENTS:      int = 1   # 1문장(약 50자)  — 도입부 핵심박스
ECO_SEC_INTRO_SENTS:      int = 1   # 1문장(약 50자)  — 섹션 소개글
ECO_SEC_ANALYSIS_SENTS:   int = 4   # 4문장(약 200자) — 글로벌·오늘 지표 분석글
ECO_SEC_TERM_MIN:         int = 2   # 2문장(약 100자) — 지표 이해 h3 아래 최소
ECO_SEC_TERM_MAX:         int = 3   # 3문장(약 150자) — 지표 이해 h3 아래 최대
ECO_SEC_ITEM_SENTS:       int = 2   # 2문장(약 100자) — 항목당 문장 수
ECO_SEC_WEEKLY_SENTS:     int = 3   # 3문장(약 150자) — 이번 주 분석글
ECO_OUTRO_SUMMARY_SENTS:  int = 2   # 2문장(약 100자) — 마무리 요약

# ── 기업 소개·SEO 소제목 (모두 문장 수 단위) ─────────────────
COMPANY_INTRO_MIN:        int = 2   # 2문장(약 100자)
COMPANY_INTRO_MAX:        int = 3   # 3문장(약 150자)
SEO_HEADING_SENTS_MIN:    int = 2   # 2문장(약 100자)
SEO_HEADING_SENTS_MAX:    int = 3   # 3문장(약 150자)

# ── 차트 캡션 분량 (collect_theme.py 용) ─────────────────
CHART_CAPTION_SENTS:      int = 1   # 캡션 1문장
CHART_CAPTION_CHARS:      int = 25  # 캡션 글자 한도 (짧은 설명형 — 1문장 ≈ 25자)

# ── SEO 학습 분량 (seo_learner.py 용) ────────────────────
SEO_IMPROVEMENT_TITLE_MAX:  int = 30  # 개선 항목 제목 한도
SEO_IMPROVEMENT_SENTS_MIN:  int = 2   # 개선안 최소 문장
SEO_IMPROVEMENT_SENTS_MAX:  int = 3   # 개선안 최대 문장

# ── SEO 이상적 분량 (네이버·티스토리) — spec 위임 ────────────
SEO_CHAR_SENTS:           int = TARGET_SENTENCES                          # spec.target_sentences
SEO_CHAR_IDEAL:           int = SEO_CHAR_SENTS * KOREAN_PER_SENTENCE

# ── 하위 호환 alias — spec.max_korean 위임 (상한 박제) ───────
MAX_KOREAN:        int = _SPEC_ECON.max_korean   # ★ 상한 (절대 박제 — spec 변경만)
TARGET_LOW:        int = TARGET_KOREAN
MIN_VALID:         int = TARGET_KOREAN
PASSTHROUGH_RATIO: float = 1.00   # 미사용

# ── 알고리즘 임계 (본문 분량 기준 — 문장수 메인) ─────────────
PARAGRAPH_SPLIT_SENTS:  int = 2                                              # 2문장
PARAGRAPH_SPLIT_KOREAN: int = PARAGRAPH_SPLIT_SENTS * KOREAN_PER_SENTENCE    # 약 100자
FILLER_IMG_SENTS:       int = 12                                             # 12문장
FILLER_IMG_THRESHOLD:   int = FILLER_IMG_SENTS * KOREAN_PER_SENTENCE         # 약 600자
BLOCK_SPLIT_SENTS:      int = 10                                             # 10문장
BLOCK_SPLIT_THRESHOLD:  int = BLOCK_SPLIT_SENTS * KOREAN_PER_SENTENCE        # 약 500자

# ── 단순 한도 (글자수 단독 — 본문 분량 아님 — 문장 표기 제외) ─
TITLE_MAX:             int = 40   # 제목 한 줄 한글 한도 (1문장 미만)
TAG_MAX:               int = 10   # 태그 한 개 한도 (단어 단위)
SHORT_BLOCK_THRESHOLD: int = 40   # 어미 변환 skip 임계 (1문장 미만)
MIN_TOKEN_LEN:         int = 2    # 토큰 최소 길이 (단어 단위)

# ── 종목 정책 (테마글당) ─────────────────────────────────────
STOCK_COUNT_PER_POST:  int = 7    # 테마글당 다룰 종목 수 (개수 단위)

# ── 종목 카드 (collect_theme: 종목별 설명) ──────────────────
STOCK_CARD_LEADER_SENTS_MIN: int = 5                                              # 5문장
STOCK_CARD_LEADER_SENTS_MAX: int = 6                                              # 6문장
STOCK_CARD_LEADER_MIN: int = STOCK_CARD_LEADER_SENTS_MIN * KOREAN_PER_SENTENCE   # 약 250자
STOCK_CARD_LEADER_MAX: int = STOCK_CARD_LEADER_SENTS_MAX * KOREAN_PER_SENTENCE   # 약 300자
STOCK_CARD_OTHER_SENTS_MIN:  int = 2                                              # 2문장
STOCK_CARD_OTHER_SENTS_MAX:  int = 3                                              # 3문장
STOCK_CARD_OTHER_MIN:  int = STOCK_CARD_OTHER_SENTS_MIN * KOREAN_PER_SENTENCE    # 약 100자
STOCK_CARD_OTHER_MAX:  int = STOCK_CARD_OTHER_SENTS_MAX * KOREAN_PER_SENTENCE    # 약 150자

# ── 회사 비즈 설명 (종목 카드 사업 설명 분량) ──
BIZ_DESC_LEADER_SENTS_MIN: int = 4                                              # 4문장
BIZ_DESC_LEADER_SENTS_MAX: int = 5                                              # 5문장
BIZ_DESC_LEADER_MIN:   int = BIZ_DESC_LEADER_SENTS_MIN * KOREAN_PER_SENTENCE    # 약 200자
BIZ_DESC_LEADER_MAX:   int = BIZ_DESC_LEADER_SENTS_MAX * KOREAN_PER_SENTENCE    # 약 250자
BIZ_DESC_OTHER_SENTS_MIN:  int = 2                                              # 2문장
BIZ_DESC_OTHER_SENTS_MAX:  int = 3                                              # 3문장
BIZ_DESC_OTHER_MIN:    int = BIZ_DESC_OTHER_SENTS_MIN * KOREAN_PER_SENTENCE     # 약 100자
BIZ_DESC_OTHER_MAX:    int = BIZ_DESC_OTHER_SENTS_MAX * KOREAN_PER_SENTENCE     # 약 150자

# ── 테마글 섹션 fallback (★ ERRORS [140] — 섹션 동적, 옛 호출자 호환만) ────
# 섹션 자체는 generate_section_plan() 으로 동적 생성. 아래는 *옛 호출자 호환 fallback*.
# sentences_per_section 의 중간값 사용 — 실제 작성 시 section_plan 이 우선.
_THEME_AVG_SENTS = (_SPEC_THEME.sentences_per_section[0] + _SPEC_THEME.sentences_per_section[1]) // 2
THEME_LEADER_SENTS:       int = _THEME_AVG_SENTS
THEME_LEADER_KOREAN:      int = THEME_LEADER_SENTS * KOREAN_PER_SENTENCE
THEME_LEADER_CHART_COUNT: int = 1
THEME_OTHERS_SENTS:       int = _THEME_AVG_SENTS        # 부대장주 섹션
THEME_OTHERS_KOREAN:      int = THEME_OTHERS_SENTS * KOREAN_PER_SENTENCE
THEME_OTHERS_CHART_COUNT: int = 2
THEME_MULTI_SENTS:        int = 6                       # 그 외 다수 종목 통합 섹션 (부대장주 이하 5개)
THEME_MULTI_KOREAN:       int = THEME_MULTI_SENTS * KOREAN_PER_SENTENCE
THEME_SECTOR_SENTS:       int = 4                       # 섹터 & 시장 분석 (구조상 짧게)
THEME_SECTOR_KOREAN:      int = THEME_SECTOR_SENTS * KOREAN_PER_SENTENCE
THEME_SECTOR_CHART_COUNT: int = 1
THEME_STRATEGY_SENTS:     int = 4                       # 투자 전략 & 위험 요인 (구조상 짧게)
THEME_STRATEGY_KOREAN:    int = THEME_STRATEGY_SENTS * KOREAN_PER_SENTENCE
THEME_STRATEGY_CHART_COUNT: int = 1
# 테마글 총합 — spec 자동 도출 (target)
THEME_TOTAL_SENTS:        int = _SPEC_THEME.target_sentences
THEME_TOTAL_CHART_COUNT:  int = max(5, _SPEC_THEME.max_images - 3)  # 추정 fallback

# 테마글 제목 한도 (jarvis_main: TITLE_MAX 와 별도)
TITLE_THEME_MAX:       int = 45     # 테마글 제목 길이 한도

# RADAR 트렌드 키워드 추출 (자비스02 도메인이지만 사용자 명령상 단일 진입점)
RADAR_KW_KOR_MIN:      int = 2      # 한글 키워드 최소 글자수
RADAR_KW_KOR_MAX:      int = 8      # 한글 키워드 최대 글자수
RADAR_KW_EN_LOWER_MIN: int = 2      # 영문 소문자 부분 최소
RADAR_KW_EN_LOWER_MAX: int = 10     # 영문 소문자 부분 최대
RADAR_KW_EN_UPPER_MIN: int = 2      # 영문 대문자(약어) 최소
RADAR_KW_EN_UPPER_MAX: int = 6      # 영문 대문자 최대 (radar_main 패턴)
RADAR_KW_THEME_MIN:    int = 3      # theme_matcher: 테마 키워드 매칭 최소 글자수
RADAR_NUM_CTX_MIN:     int = 3      # diagnose_naver_view: 컨텍스트 숫자 후보 최소 자리수
RADAR_NUM_CTX_MAX:     int = 7      # 동 최대 자리수
RADAR_KOR_NOISE_MAX:   int = 2      # radar_main: 1~2자 한글 노이즈 (접속사 등) 제거 임계

# RADAR 키워드 추출 정규식 (이미 빌드된 패턴 — 호출자는 단순 import 만)
RADAR_KW_PATTERN_FULL: str = (
    rf"[가-힣]{{{RADAR_KW_KOR_MIN},{RADAR_KW_KOR_MAX}}}"
    rf"|[A-Z][a-z]{{{RADAR_KW_EN_LOWER_MIN},{RADAR_KW_EN_LOWER_MAX}}}"
    rf"|[A-Z]{{{RADAR_KW_EN_UPPER_MIN},}}"
)
RADAR_KW_PATTERN_KOR_UPPER: str = (
    rf"[가-힣]{{{RADAR_KW_KOR_MIN},{RADAR_KW_KOR_MAX}}}"
    rf"|[A-Z]{{{RADAR_KW_EN_UPPER_MIN},{RADAR_KW_EN_UPPER_MAX}}}"
)
RADAR_KOR_NOISE_PATTERN: str = rf"[가-힣]{{1,{RADAR_KOR_NOISE_MAX}}}"
RADAR_NUM_CTX_PATTERN: str = rf"\b(\d{{1,{RADAR_NUM_CTX_MAX}}})\b"

# ── 카테고리별 글쓰기 분량 가이드 (자비스02 WRITING_GUIDE) ───
# 문장수 메인 (사용자 박제 2026-05-14) — 글자수 alias 는 자동 파생.
# 형식: "카테고리": (sents_min, sents_max)
BLOG_CATEGORY_SENTS: dict = {
    "금융·투자": (40, 60),   # 40~60문장 (약 2000~3000자)
    "IT·테크":   (50, 80),   # 50~80문장 (약 2500~4000자)
    "건강·의료": (50, 70),   # 50~70문장 (약 2500~3500자)
    "라이프":    (25, 50),   # 25~50문장 (약 1250~2500자)
    "뷰티·패션": (25, 50),
    "음식·맛집": (24, 40),   # 24~40문장 (약 1200~2000자)
    "여행":      (40, 70),
    "교육·취업": (60, 100),  # 60~100문장 (약 3000~5000자)
    "부동산":    (40, 70),
    "연예·문화": (20, 40),
    "스포츠":    (20, 40),
    "자동차":    (40, 70),
    "사회·이슈": (40, 60),
    "기타":      (25, 50),
}
# 글자수 alias 자동 파생 — 외부 import 호환
BLOG_CATEGORY_LENGTH: dict = {
    k: (lo * KOREAN_PER_SENTENCE, hi * KOREAN_PER_SENTENCE)
    for k, (lo, hi) in BLOG_CATEGORY_SENTS.items()
}

# ── 도입부·마무리·표 해설 분량 (모두 문장수 메인) ───────────
INTRO_TARGET_SENTS:        int = 4                                              # 4문장
INTRO_TARGET:              int = INTRO_TARGET_SENTS * KOREAN_PER_SENTENCE       # 약 200자 — 경제글 도입부
INTRO_THEME_TARGET_SENTS:  int = 4                                              # 4문장
INTRO_THEME_TARGET:        int = INTRO_THEME_TARGET_SENTS * KOREAN_PER_SENTENCE # 약 200자 — 테마글 도입부
PARAGRAPH_MIN_SENTS:       int = 5                                              # 5문장
PARAGRAPH_MIN_KOREAN:      int = PARAGRAPH_MIN_SENTS * KOREAN_PER_SENTENCE      # 약 250자 — naver_poster 단락 분리 최소
OUTRO_TARGET_SENTS:        int = 4                                              # 4문장
OUTRO_TARGET:              int = OUTRO_TARGET_SENTS * KOREAN_PER_SENTENCE       # 약 200자 — 마무리+면책
INTRO_KEYWORD_WINDOW_SENTS:int = 2                                              # 2문장
INTRO_KEYWORD_WINDOW:      int = INTRO_KEYWORD_WINDOW_SENTS * KOREAN_PER_SENTENCE  # 약 100자 — SEO 4원칙
SECTION_COMMENTARY_SENTS:  int = 5                                              # 5문장
SECTION_COMMENTARY:        int = SECTION_COMMENTARY_SENTS * KOREAN_PER_SENTENCE # 약 250자 — 표 아래 해설
WEEKLY_INSIGHT_SENTS:      int = 4                                              # 4문장
WEEKLY_INSIGHT:            int = WEEKLY_INSIGHT_SENTS * KOREAN_PER_SENTENCE     # 약 200자 — 이번 주 일정 해설
IMG_FOLLOWUP_MIN_SENTS:    int = 2                                              # 2문장
IMG_FOLLOWUP_MIN:          int = IMG_FOLLOWUP_MIN_SENTS * KOREAN_PER_SENTENCE   # 약 100자 — 이미지 뒤 텍스트 보완

# ── 단순 한도 (글자수 단독 — 본문 분량 아님 — 문장 표기 제외) ─
META_DESC_MAX:         int = 150    # 구글 메타 디스크립션 한도 (1줄 메타 — 문장 표기 부적합)

# ── SEO 프롬프트 권장 한도 (생성 가이드 — validation 한도 아님) ──
TITLE_PROMPT_MAX:      int = 35     # SEO 제목 프롬프트 권장 (validation: TITLE_MAX=40)
META_DESC_PROMPT_MAX:  int = 140    # 메타 디스크립션 프롬프트 권장 (validation: META_DESC_MAX=150)
SCENARIO_LABEL_MAX:    int = 15     # 시나리오·비교 라벨 최대
ECO_TITLE_PROMPT_MAX:  int = 15     # 경제 브리핑 긴급 폴백 제목 최대

# ── 용어 카드·표 셀 (단어/구 단위 — 문장 아님) ───────────────
TERM_NAME_MAX:         int = 10     # 용어명
TERM_FORMULA_MAX:      int = 15     # 계산법
TERM_CRITERIA_MAX:     int = 20     # 판단기준
LINE_BREAK_THRESHOLD:  int = 30     # 줄바꿈 기준

# ── 썸네일 텍스트 (단어/구 단위 — 문장 아님) ─────────────────
THUMB_THEME_MAX:       int = 10     # 썸네일 테마명
THUMB_SUBTITLE_MAX:    int = 30     # 썸네일 서브 타이틀

# ── 학습 인사이트 식별자 (영문/숫자 키 — 본문 분량 아님) ─────
INSIGHT_KEY_MAX:       int = 30

# ── 경제글 섹션별 분량 (모두 문장수 메인) ────────────────────
ECO_TERM_EXPLAIN_SENTS:   int = 5                                              # 5문장
ECO_TERM_EXPLAIN:         int = ECO_TERM_EXPLAIN_SENTS * KOREAN_PER_SENTENCE   # 약 250자 — ③ 지표 쉽게 이해하기
ECO_MARKET_IMPACT_SENTS:  int = 6                                              # 6문장
ECO_MARKET_IMPACT:        int = ECO_MARKET_IMPACT_SENTS * KOREAN_PER_SENTENCE  # 약 300자 — ④ 국내 증시 영향
ECO_BEHAVIOR_GUIDE_SENTS: int = 5                                              # 5문장
ECO_BEHAVIOR_GUIDE:       int = ECO_BEHAVIOR_GUIDE_SENTS * KOREAN_PER_SENTENCE # 약 250자 — ⑥ 투자자 행동 가이드
ECO_CHECKLIST_SENTS:      int = 5                                              # 5문장
ECO_CHECKLIST:            int = ECO_CHECKLIST_SENTS * KOREAN_PER_SENTENCE      # 약 250자 — ⑥ 체크리스트 합산
ECO_SUMMARY_CARD_SENTS:   int = 4                                              # 4문장
ECO_SUMMARY_CARD:         int = ECO_SUMMARY_CARD_SENTS * KOREAN_PER_SENTENCE   # 약 200자 — 마무리 요약 카드

# ── 알고리즘·도구 한도 (본문 분량 측정 기반 — 문장수 메인) ─────
# 모두 *_SENTS 가 진실 소스. 글자수는 산술 파생 (사용자 박제 2026-05-14).
ECO_BEFORE_SNIPPET_SENTS:  int = 1                                                # 1문장
ECO_BEFORE_SNIPPET:        int = ECO_BEFORE_SNIPPET_SENTS * KOREAN_PER_SENTENCE  # 약 50자 — post_quality 'before' 발췌
ANALYZER_INPUT_SENTS:      int = 60                                               # 60문장
ANALYZER_INPUT_MAX:        int = ANALYZER_INPUT_SENTS * KOREAN_PER_SENTENCE      # 약 3000자 — Claude 입력 절단
BODY_SNIPPET_SENTS:        int = 16                                               # 16문장
BODY_SNIPPET_LEN:          int = BODY_SNIPPET_SENTS * KOREAN_PER_SENTENCE        # 약 800자 — 본문 발췌 (이메일 미리보기)
INDEXER_BODY_MIN_SENTS:    int = 4                                                # 4문장
INDEXER_BODY_MIN:          int = INDEXER_BODY_MIN_SENTS * KOREAN_PER_SENTENCE    # 약 200자 — style_indexer 인덱싱 최소
INDEXER_BODY_MAX_SENTS:    int = 100                                              # 100문장
INDEXER_BODY_MAX:          int = INDEXER_BODY_MAX_SENTS * KOREAN_PER_SENTENCE    # 약 5000자 — 코퍼스 저장 한도
INDEXER_EMBED_SENTS:       int = 160                                              # 160문장
INDEXER_EMBED_MAX:         int = INDEXER_EMBED_SENTS * KOREAN_PER_SENTENCE       # 약 8000자 — 임베딩 호출 본문 한도

# ── 단순 한도 (제목 — 1줄 단위 — 문장 표기 부적합) ────────────
TITLE_MIN_RECOMMEND:   int = 20     # 제목 너무 짧음 판정 하한 (제목 한도)
TITLE_MAX_RECOMMEND:   int = 60     # 제목 너무 김 판정 상한 (제목 한도)

# 글 구조 정책 (소제목 = 헤더 단락 — h2/h3/## 형태)
MIN_SENTENCES_PER_HEADING: int = 1  # 모든 소제목 아래 최소 문장 수 (강제 — 빈 소제목 금지)
MIN_KOREAN_PER_HEADING:    int = 30 # 소제목 아래 최소 한글 (1문장 보장 임계 — 1문장 미만이라 문장 표기 제외)

# ── CrewAI 내부 짧은 리포트 (블로그 본문 X — 이미지 보조 자료) ──
# 문장수 메인 (사용자 박제 2026-05-14)
BRIEF_REPORT_SENTS_LO:  int = 12                                              # 12문장
BRIEF_REPORT_SENTS_HI:  int = 18                                              # 18문장
BRIEF_REPORT_LO:        int = BRIEF_REPORT_SENTS_LO * KOREAN_PER_SENTENCE    # 약 600자
BRIEF_REPORT_HI:        int = BRIEF_REPORT_SENTS_HI * KOREAN_PER_SENTENCE    # 약 900자
BRIEF_SECTION_SENTS_LO: int = 1                                               # 1문장
BRIEF_SECTION_SENTS_HI: int = 2                                               # 2문장 (각 섹션 짧은 보조)
BRIEF_SECTION_LO:       int = BRIEF_SECTION_SENTS_LO * KOREAN_PER_SENTENCE   # 약 50자
BRIEF_SECTION_HI:       int = BRIEF_SECTION_SENTS_HI * KOREAN_PER_SENTENCE   # 약 100자 (50→100 정규화)

# ── shared.seo 위임 (없으면 fallback) ───────────────────────────
try:
    from shared.seo import (
        compress_to_korean as _seo_compress,
        count_korean as _seo_count,
        sanitize_body as _seo_sanitize,
    )
    _SEO_OK = True
except ImportError:
    _SEO_OK = False
    _KOR_RE_FALLBACK = re.compile(r"[가-힣]")
    def _seo_count(text: str) -> int:
        return len(_KOR_RE_FALLBACK.findall(text or ""))
    def _seo_compress(text: str, max_korean: int = MAX_KOREAN,
                      context: str = "", emit_event: bool = True, **_) -> str:
        return text or ""
    def _seo_sanitize(text: str) -> str:
        return text or ""

# ── 텔레그램 (jarvis_main.tg 와 동일 패턴, 순환 import 회피) ─────
try:
    import requests as _requests
    _TG_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
    _TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
except ImportError:
    _requests = None
    _TG_TOKEN = ""
    _TG_CHAT_ID = ""


def _tg(msg: str) -> None:
    """텔레그램 알림 (실패 무시)."""
    if not (_requests and _TG_TOKEN and _TG_CHAT_ID):
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
            json={"chat_id": _TG_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# 카운트 / 판정
# ──────────────────────────────────────────────────────────────

_SENT_RE = re.compile(r"[.!?。]\s*(?=\n|$|\s)")


def count_sentences(text: str) -> int:
    """문장 수 카운트 (마침표·물음표·느낌표 종결 기준)."""
    return len(_SENT_RE.findall(text or ""))


def count(text: str) -> int:
    """한글 글자수 (가-힣 범위). 영어·숫자·기호 제외."""
    return _seo_count(text or "")


def sum_korean(*texts: str) -> int:
    """여러 문자열 한글 합산."""
    return sum(count(t) for t in texts)



# ──────────────────────────────────────────────────────────────
# 압축 (cap) — LLM 없는 문장 경계 hard-cut
# ──────────────────────────────────────────────────────────────

def compress(text: str, context: str = "theme",
             max_korean: int = MAX_KOREAN, emit_event: bool = True) -> str:
    """잘라내기 없음 — 원문 그대로 반환. 호출자 호환성 유지용 passthrough."""
    from shared.seo import sanitize_body
    return sanitize_body(text) if text else ""


def cap_for_publish(text: str, context: str = "publish",
                    max_korean: int = MAX_KOREAN) -> str:
    """잘라내기 없음 — 원문 그대로 반환. 호출자 호환성 유지용 passthrough."""
    from shared.seo import sanitize_body
    return sanitize_body(text) if text else ""


# ──────────────────────────────────────────────────────────────
# 경고 (텔레그램)
# ──────────────────────────────────────────────────────────────

def warn_length(theme: str, platform: str, text: str, label: str = "") -> int:
    """발행 직전 분량 로그. 잘라내기·경고 없음."""
    s = count_sentences(text)
    n = count(text)
    tag = f"[{label} {platform}]" if label else f"[{platform}]"
    try:
        print(f"  📏 {tag} 발행 직전 {s}문장 / 한글 {n:,}자 (목표 {TARGET_SENTENCES}문장)")
    except Exception:
        pass
    return n



# ──────────────────────────────────────────────────────────────
# 블록 누적 cap (pre_revise.py 용)
# ──────────────────────────────────────────────────────────────

def cap_blocks(blocks: list, context: str = "pre_revise",
               max_korean: int = MAX_KOREAN) -> tuple:
    """잘라내기 없음 — 모든 블록 그대로 반환. 호환성 유지용 passthrough."""
    return blocks, False


# ──────────────────────────────────────────────────────────────
# 섹션 리스트 cap (jarvis_main.py 후처리 용)
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# 진단 헬퍼
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Prompt 블록 빌더 (LLM 지침에 분량 규정을 동적으로 주입)
# ──────────────────────────────────────────────────────────────

def build_prompt_length_block(target_low: int = TARGET_KOREAN,
                               max_korean: int = TARGET_KOREAN,
                               min_valid: int = TARGET_KOREAN,
                               n_sections: int = 4) -> str:
    """LLM 작성 prompt 에 들어갈 [분량 규정] 블록 생성.

    TARGET_SENTENCES 변경 시 모든 prompt 자동 동기화.
    합계는 반드시 TARGET_SENTENCES 와 일치 — 나머지는 첫 섹션에 배분.
    """
    T      = TARGET_SENTENCES
    i_sent = _INTRO_SENTS
    o_sent = _OUTRO_SENTS
    body   = T - i_sent - o_sent   # 30 - 4 - 2 = 24

    # 섹션당 기본 문장수 + 나머지는 첫 섹션에 배분 → 합계 항상 T
    base      = max(2, body // n_sections)
    remainder = body - base * n_sections   # 0 이상 (base가 내림이므로)
    sec_sents = [base + (1 if i < remainder else 0) for i in range(n_sections)]
    actual_total = i_sent + sum(sec_sents) + o_sent  # 항상 T

    rows = "\n".join(
        f"│ 섹션 {i+1}   │ {sec_sents[i]}문장 │"
        for i in range(n_sections)
    )
    return (
        f"[분량 규정 — 정확히 {T}문장]\n"
        f"문장 = 마침표(.)·물음표(?)·느낌표(!) 로 끝나는 완결 문장 기준.\n\n"
        f"│ 파트     │ 문장수   │\n"
        f"│ 도입부   │ {i_sent}문장    │\n"
        f"{rows}\n"
        f"│ 마무리   │ {o_sent}문장    │\n"
        f"│ 합계     │ {actual_total}문장   │\n\n"
        f"- 섹션 하나가 길면 다른 섹션을 줄여 합산 {T}문장 유지.\n"
        f"- 모든 문장은 마침표·물음표·느낌표로 종결 — 중간에 끊긴 문장 금지.\n"
        f"- 각 소제목 아래 최소 1문장(완결) 필수 — 빈 소제목 절대 금지."
    )


def build_short_length_phrase(target_low: int = TARGET_LOW,
                               max_korean: int = MAX_KOREAN) -> str:
    """짧은 한 줄 분량 표현. docstring·로그·텔레그램 메시지용."""
    return f"전체 {TARGET_SENTENCES}문장"


def build_length_phrase(min_sents: int, max_sents: int = None) -> str:
    """분량 표기 표준 — "N문장(약 N×50자)" / "M~N문장(약 X~Y자)" 패턴.

    1문장 ≈ 약 50자 (KOREAN_PER_SENTENCE) 기준 — 사용자 박제 2026-05-14.
    모든 prompt 내 분량 표현은 이 함수로 통일.

    예:
        build_length_phrase(4)         → "4문장(약 200자)"
        build_length_phrase(5, 6)      → "5~6문장(약 250~300자)"
        build_length_phrase(1, 2)      → "1~2문장(약 50~100자)"
    """
    k = KOREAN_PER_SENTENCE
    if max_sents is None or max_sents == min_sents:
        return f"{min_sents}문장(약 {min_sents * k}자)"
    return f"{min_sents}~{max_sents}문장(약 {min_sents * k}~{max_sents * k}자)"


__all__ = [
    # 본문 분량 정책
    "TARGET_SENTENCES",                                              # 주 기준 (문장 수)
    "MIN_SENTENCES_THRESHOLD", "MIN_SVG_COUNT",                     # 출력 검증 기준
    "MIN_CHART_COUNT", "MAX_CHART_COUNT",                           # 차트 개수 동적 계산
    "INTRO_SENTS", "OUTRO_SENTS", "MAX_P_SENTS",        # 도입부/마무리/단락 최대 문장 수 (섹션은 동적)
    "SEC_SENTS_MIN", "SEC_SENTS_MAX",                  # 섹션 분량 범위
    "DISCLAIMER_INLINE_SENTS",                          # 인라인 면책 1문장
    "CHART_CAPTION_SENTS", "CHART_CAPTION_CHARS",       # 차트 캡션
    "SEO_IMPROVEMENT_TITLE_MAX",                        # SEO 학습 제목 한도
    "SEO_IMPROVEMENT_SENTS_MIN", "SEO_IMPROVEMENT_SENTS_MAX",  # SEO 학습 개선안
    "ECO_GREETING_SENTS", "ECO_HIGHLIGHT_SENTS", "ECO_SEC_INTRO_SENTS",
    "ECO_SEC_ANALYSIS_SENTS", "ECO_SEC_TERM_MIN", "ECO_SEC_TERM_MAX",
    "ECO_SEC_ITEM_SENTS", "ECO_SEC_WEEKLY_SENTS", "ECO_OUTRO_SUMMARY_SENTS",
    "COMPANY_INTRO_MIN", "COMPANY_INTRO_MAX",                       # 기업 소개 분량
    "SEO_HEADING_SENTS_MIN", "SEO_HEADING_SENTS_MAX", "SEO_CHAR_IDEAL",  # SEO 소제목·글자수
    "TARGET_KOREAN",                                                 # 참고 기준 (글자수)
    "MAX_KOREAN", "TARGET_LOW", "MIN_VALID", "PASSTHROUGH_RATIO",  # 하위 호환 alias
    # 알고리즘 임계값
    "PARAGRAPH_SPLIT_KOREAN", "FILLER_IMG_THRESHOLD",
    "TITLE_MAX", "TAG_MAX", "SHORT_BLOCK_THRESHOLD", "MIN_TOKEN_LEN",
    "BLOCK_SPLIT_THRESHOLD",
    "HUMAN_INTRO_CHARS", "HUMAN_INTRO_SENTS",
    "DISCLAIMER_SENTS", "DISCLAIMER_KOREAN",
    "STOCK_COUNT_PER_POST",
    "STOCK_CARD_LEADER_MIN", "STOCK_CARD_LEADER_MAX",
    "STOCK_CARD_OTHER_MIN", "STOCK_CARD_OTHER_MAX",
    "BIZ_DESC_LEADER_MIN", "BIZ_DESC_LEADER_MAX",
    "BIZ_DESC_OTHER_MIN", "BIZ_DESC_OTHER_MAX",
    "KOREAN_PER_SENTENCE",
    "THEME_LEADER_SENTS", "THEME_LEADER_KOREAN", "THEME_LEADER_CHART_COUNT",
    "THEME_OTHERS_SENTS", "THEME_OTHERS_KOREAN", "THEME_OTHERS_CHART_COUNT",
    "THEME_MULTI_SENTS", "THEME_MULTI_KOREAN",
    "THEME_SECTOR_SENTS", "THEME_SECTOR_KOREAN", "THEME_SECTOR_CHART_COUNT",
    "THEME_STRATEGY_SENTS", "THEME_STRATEGY_KOREAN", "THEME_STRATEGY_CHART_COUNT",
    "THEME_TOTAL_SENTS", "THEME_TOTAL_CHART_COUNT",
    "build_length_phrase",
    "TITLE_THEME_MAX",
    "RADAR_KW_KOR_MIN", "RADAR_KW_KOR_MAX",
    "RADAR_KW_EN_LOWER_MIN", "RADAR_KW_EN_LOWER_MAX",
    "RADAR_KW_EN_UPPER_MIN", "RADAR_KW_EN_UPPER_MAX",
    "RADAR_KW_THEME_MIN", "RADAR_NUM_CTX_MIN", "RADAR_NUM_CTX_MAX",
    "RADAR_KOR_NOISE_MAX",
    "RADAR_KW_PATTERN_FULL", "RADAR_KW_PATTERN_KOR_UPPER",
    "RADAR_KOR_NOISE_PATTERN", "RADAR_NUM_CTX_PATTERN",
    "BLOG_CATEGORY_SENTS", "BLOG_CATEGORY_LENGTH",
    "INTRO_TARGET_SENTS", "INTRO_TARGET",
    "INTRO_THEME_TARGET_SENTS", "INTRO_THEME_TARGET",
    "OUTRO_TARGET_SENTS", "OUTRO_TARGET",
    "INTRO_KEYWORD_WINDOW_SENTS", "INTRO_KEYWORD_WINDOW",
    "PARAGRAPH_MIN_SENTS", "PARAGRAPH_MIN_KOREAN",
    "META_DESC_MAX",
    "TITLE_PROMPT_MAX", "META_DESC_PROMPT_MAX", "SCENARIO_LABEL_MAX", "ECO_TITLE_PROMPT_MAX",
    "SECTION_COMMENTARY_SENTS", "SECTION_COMMENTARY",
    "WEEKLY_INSIGHT_SENTS", "WEEKLY_INSIGHT",
    "IMG_FOLLOWUP_MIN_SENTS", "IMG_FOLLOWUP_MIN",
    "TERM_NAME_MAX", "TERM_FORMULA_MAX", "TERM_CRITERIA_MAX", "LINE_BREAK_THRESHOLD",
    "THUMB_THEME_MAX", "THUMB_SUBTITLE_MAX",
    "INSIGHT_KEY_MAX",
    "ECO_TERM_EXPLAIN_SENTS", "ECO_TERM_EXPLAIN",
    "ECO_MARKET_IMPACT_SENTS", "ECO_MARKET_IMPACT",
    "ECO_BEHAVIOR_GUIDE_SENTS", "ECO_BEHAVIOR_GUIDE",
    "ECO_BEFORE_SNIPPET_SENTS", "ECO_BEFORE_SNIPPET",
    "ECO_CHECKLIST_SENTS", "ECO_CHECKLIST",
    "ECO_SUMMARY_CARD_SENTS", "ECO_SUMMARY_CARD",
    "ANALYZER_INPUT_SENTS",
    "BODY_SNIPPET_SENTS",
    "INDEXER_BODY_MIN_SENTS", "INDEXER_BODY_MAX_SENTS", "INDEXER_EMBED_SENTS",
    "ANALYZER_INPUT_MAX",
    "TITLE_MIN_RECOMMEND", "TITLE_MAX_RECOMMEND",
    "BODY_SNIPPET_LEN", "INDEXER_BODY_MAX", "INDEXER_BODY_MIN", "INDEXER_EMBED_MAX",
    "MIN_SENTENCES_PER_HEADING", "MIN_KOREAN_PER_HEADING",
    # CrewAI 짧은 리포트 분량
    "BRIEF_REPORT_SENTS_LO", "BRIEF_REPORT_SENTS_HI",
    "BRIEF_REPORT_LO", "BRIEF_REPORT_HI",
    "BRIEF_SECTION_SENTS_LO", "BRIEF_SECTION_SENTS_HI",
    "BRIEF_SECTION_LO", "BRIEF_SECTION_HI",
    # 함수
    "count_sentences", "count", "sum_korean",
      
    "compress", "cap_for_publish",
    "warn_length", 
    "cap_blocks", 
    
    "build_prompt_length_block", "build_short_length_phrase",
]
