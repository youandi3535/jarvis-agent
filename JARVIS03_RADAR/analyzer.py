"""
JARVIS03 RADAR — 분석 엔진
RADAR(트렌드) + ANALYST(성과 학습) + SEO(기회 점수) 통합
"""
from __future__ import annotations
import json
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS03_RADAR.collectors import report_radar as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

_CACHE_FILE = Path(__file__).parent / "data" / "sector_cache.json"
_SECTOR_CACHE: dict[str, str] = {}

def _load_cache() -> None:
    global _SECTOR_CACHE
    if _CACHE_FILE.exists():
        try:
            _SECTOR_CACHE = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _SECTOR_CACHE = {}

def _save_cache() -> None:
    _CACHE_FILE.parent.mkdir(exist_ok=True)
    write_json(_CACHE_FILE, _SECTOR_CACHE, indent=2)

_load_cache()

# ── 복합 패턴 우선 분류 (단어 조합이 특정 섹터를 강하게 시사할 때) ──────────
# (포함 단어 집합, 섹터) — 키워드 안에 해당 단어들이 '모두' 포함되면 해당 섹터로 즉시 분류
COMPOUND_PRIORITY: list[tuple[frozenset, str]] = [
    # 지정학·국제 분쟁 — 국가명 + 갈등 단어
    (frozenset(["이란", "미국"]),    "사회·이슈"),
    (frozenset(["북한", "미국"]),    "사회·이슈"),
    (frozenset(["북한", "한국"]),    "사회·이슈"),
    (frozenset(["러시아", "우크라이나"]), "사회·이슈"),
    (frozenset(["중국", "대만"]),    "사회·이슈"),
    (frozenset(["이스라엘", "팔레스타인"]), "사회·이슈"),
    (frozenset(["전쟁", "미국"]),    "사회·이슈"),
    (frozenset(["전쟁", "한국"]),    "사회·이슈"),
    # 금융 복합
    (frozenset(["주식", "금리"]),    "금융·투자"),
    (frozenset(["환율", "달러"]),    "금융·투자"),
]

SECTORS: dict[str, list[str]] = {
    "IT·테크":   ["AI", "인공지능", "반도체", "스마트폰", "갤럭시", "아이폰", "노트북",
                  "앱", "소프트웨어", "클라우드", "챗GPT", "GPT", "로봇", "자율주행",
                  "드론", "메타버스", "NFT", "블록체인", "코딩", "프로그래밍", "PC", "CPU", "GPU",
                  "기술", "전자", "데이터", "네트워크", "프로그램", "시스템", "개발"],
    "금융·투자": ["주식", "코스피", "코스닥", "ETF", "펀드", "채권", "환율", "달러",
                  "금값", "금리", "부동산투자", "코인", "비트코인", "이더리움", "경제지표",
                  "인플레이션", "금융", "투자", "배당", "증시", "선물", "옵션",
                  "주가", "상승", "하락", "거래", "증권", "포트폴리오", "특징주",
                  "카드", "신용카드", "체크카드", "은행", "보험료", "보험사", "신용",
                  "고객확인", "계좌", "대출", "금고", "예금", "적금",
                  # ★ 현대 금융 어휘 확장 (사용자 박제 2026-07-18) — 금융 주제 커버리지
                  "핀테크", "가상자산", "스테이블코인", "공모주", "IPO", "리츠", "REITs",
                  "저축은행", "인터넷은행", "간편결제", "토스", "카카오뱅크", "케이뱅크",
                  "증권사", "자산운용", "연금", "국민연금", "퇴직연금", "IRP", "ISA",
                  "가상화폐", "암호화폐", "스테이킹", "디파이", "청약", "상장", "공매도"],
    "건강·의료": ["다이어트", "운동", "헬스", "건강식품", "영양제", "비타민", "알레르기",
                  "감기", "독감", "백신", "병원", "의료", "약", "수면", "스트레스",
                  "피부", "탈모", "혈압", "당뇨", "암", "관절", "허리",
                  "휘트니스", "건강", "질병", "증상", "치료"],
    "라이프":    ["홈인테리어", "인테리어", "리모델링", "가구", "청소", "정리정돈",
                  "반려동물", "강아지", "고양이", "캠핑", "등산", "낚시", "자전거",
                  "취미", "독서", "요가", "필라테스", "명상",
                  "생활", "일상", "홈", "반려견", "반려묘"],
    "뷰티·패션": ["화장품", "스킨케어", "메이크업", "패션", "옷", "신발", "가방",
                  "명품", "코디", "색조", "향수", "헤어", "네일", "성형",
                  "의상", "스타일", "외모", "미용"],
    "음식·맛집": ["맛집", "음식", "레시피", "요리", "배달", "카페", "디저트",
                  "와인", "커피", "빵", "고기", "해산물", "비건", "식단", "먹방",
                  "음식점", "식당", "음식문화", "요리법", "식품"],
    # 여행: 순수 여행 목적 단어만 포함 (국가명 단독 제거 → 복합 문맥으로만 분류)
    "여행":      ["여행", "해외여행", "국내여행", "제주도", "부산여행", "서울여행",
                  "유럽여행", "일본여행", "미국여행", "동남아여행",
                  "호텔", "항공권", "숙소", "관광", "패키지여행", "비자",
                  "관광지", "여행지", "여행기", "숙박", "투어", "트립"],
    "교육·취업": ["취업", "이직", "면접", "자격증", "공무원", "어학", "영어", "토익",
                  "토플", "학원", "온라인강의", "대학", "수능", "입시", "유학", "스펙",
                  "교육", "학교", "시험", "수험생", "한국사"],
    "부동산":    ["아파트", "부동산", "전세", "월세", "매매", "분양", "청약", "재개발",
                  "재건축", "임대", "상가", "토지", "집값", "전셋값",
                  "주택", "건물", "건설", "공사"],
    "연예·문화": ["드라마", "영화", "음악", "아이돌", "BTS", "연예인", "예능", "웹툰",
                  "게임", "OTT", "넷플릭스", "유튜브", "틱톡", "SNS", "인스타그램", "공연",
                  "배우", "가수", "영상", "콘텐츠", "방송", "뮤직", "엔터테인먼트", "문화",
                  "연기", "성우", "박물관", "전시", "미술관", "도서관", "콘서트", "뮤지컬"],
    "스포츠":    ["축구", "야구", "농구", "배구", "골프", "테니스", "수영", "마라톤",
                  "올림픽", "월드컵", "손흥민", "류현진", "k리그", "EPL", "NBA",
                  "운동경기", "선수", "스포츠"],
    "자동차":    ["자동차", "전기차", "SUV", "세단", "현대차", "기아차", "테슬라", "BMW",
                  "중고차", "리스", "렌트카", "하이브리드", "충전", "차량", "운전"],
    "사랑·관계": ["연애", "사랑", "결혼", "이별", "소개팅", "썸", "데이트", "부부",
                  "이혼", "육아", "임신", "출산", "가족", "인간관계",
                  "관계", "부모", "자녀", "아들"],
    # 사회·이슈: 국제 분쟁·지정학 키워드 대폭 추가
    "사회·이슈": ["정치", "선거", "뉴스", "사건", "사고", "환경", "기후", "복지",
                  "정책", "법", "세금", "물가", "경기침체",
                  "대통령", "정부", "정당", "의원",
                  # 군사·안보
                  "미군", "군사", "국방", "전작권", "사령관", "작전", "군대",
                  "안보", "동맹", "주한미군", "주민번호", "주민", "의무",
                  # 국제 분쟁·지정학
                  "전쟁", "분쟁", "갈등", "위기", "제재", "폭격", "미사일",
                  "핵", "테러", "침공", "공습", "휴전", "협상", "외교",
                  "이란", "이스라엘", "팔레스타인", "러시아", "우크라이나",
                  "북한", "중동", "가자", "레바논", "시리아",
                  # 사회 이슈
                  "시위", "집회", "파업", "재난", "지진", "홍수", "태풍",
                  "사망", "피해", "피격", "폭발", "사고", "국가유산"],
    "에너지·환경": ["원전", "에너지", "태양광", "풍력", "탄소", "오염", "생태", "자연"],
    "경제·경기":  ["경제", "교역", "수출", "수입", "산업", "기업", "무역",
                  "급등", "상승세", "훈풍", "경기침체", "만원", "본업"],
}

# 수동 오버라이드: LLM이 틀렸을 때만 여기에 추가 (최소한으로 유지)
ENTITY_SECTOR_OVERRIDES: dict[str, str] = {}

SECTOR_COLORS: dict[str, str] = {
    "IT·테크":   "#00D4FF",
    "금융·투자": "#FFD700",
    "건강·의료": "#00FF88",
    "라이프":    "#FF6B6B",
    "뷰티·패션": "#FF69B4",
    "음식·맛집": "#FFA500",
    "여행":      "#87CEEB",
    "교육·취업": "#9B59B6",
    "부동산":    "#E67E22",
    "연예·문화": "#E91E63",
    "스포츠":    "#2ECC71",
    "자동차":    "#95A5A6",
    "사랑·관계": "#FF4757",
    "사회·이슈": "#778CA3",
    "에너지·환경": "#1ABC9C",
    "정부·공공":  "#34495E",
    "경제·경기":  "#F39C12",
    "기타":      "#4A4A4A",
}


# ── 분류 ──────────────────────────────────────────────────────

def classify_keyword(keyword: str) -> str:
    """키워드 → 섹터 분류 (하위 호환 wrapper — 신뢰도는 classify_keyword_conf 사용)."""
    sector, _confident = classify_keyword_conf(keyword)
    return sector


def classify_keyword_conf(keyword: str) -> tuple[str, bool]:
    """
    키워드 → (섹터, 신뢰도) 분류 (3단계 우선순위)

    1. COMPOUND_PRIORITY: 복합 단어 조합이 모두 포함되면 즉시 반환
       예) "이란 미국" → 사회·이슈 (여행 오분류 방지)
    2. 완전 매칭 / 하위문자열 매칭: 가장 긴 hint가 이긴다 (specificity 우선)
    3. 단어 단위 매칭: 가중치 0.8 (단독 단어는 복합보다 낮은 우선순위)

    ★ 신뢰도 (ERRORS [290] — 2026-07-03): '은행나무'가 힌트 '은행'(2글자) 부분 포함만으로
    금융·투자 오분류 → 경제 브리핑에 임업 글 발행 사고. 짧은 힌트(≤2글자)의 부분 문자열
    매칭만으로 결정되고 키워드가 힌트보다 긴 경우 confident=False — 호출자(score_keywords)가
    LLM 재분류 대상에 포함해 교정 기회 부여.
    """
    kw_lower    = keyword.lower()
    kw_no_space = re.sub(r"[^가-힣a-z0-9]", "", kw_lower)
    kw_words    = set(re.findall(r"[가-힣a-z0-9]{2,}", kw_lower))

    # ── 1단계: 복합 패턴 우선 ──────────────────────────────
    for required_words, sector in COMPOUND_PRIORITY:
        if all(w in kw_words or w in kw_lower for w in required_words):
            return sector, True

    best_sector = "기타"
    best_score  = 0
    best_hint_len = 0
    best_exact_word = False

    # ── 엔티티 기반 오버라이드 ─────────────────────────────
    for entity_hint, sector in ENTITY_SECTOR_OVERRIDES.items():
        if entity_hint.lower() in kw_lower:
            return sector, True

    for sector, hints in SECTORS.items():
        for hint in hints:
            hint_lower    = hint.lower()
            hint_no_space = re.sub(r"[^가-힣a-z0-9]", "", hint_lower)

            # ── 2단계: 완전 매칭 (최고 우선) ───────────────
            if kw_lower == hint_lower or kw_no_space == hint_no_space:
                return sector, True

            # ── 2단계: 부분 문자열 매칭 ─────────────────────
            if hint_lower in kw_lower or kw_lower in hint_lower:
                score = len(hint_lower) * 1.0
                if score > best_score:
                    best_score, best_sector = score, sector
                    best_hint_len, best_exact_word = len(hint_lower), False
                continue

            if hint_no_space in kw_no_space or kw_no_space in hint_no_space:
                score = len(hint_no_space) * 1.0
                if score > best_score:
                    best_score, best_sector = score, sector
                    best_hint_len, best_exact_word = len(hint_no_space), False
                continue

            # ── 3단계: 단어 단위 매칭 (낮은 우선순위) ────────
            for word in kw_words:
                if len(word) < 2:          # 1글자 단어는 무시 (오분류 방지)
                    continue
                if word == hint_lower or word in hint_lower:
                    score = len(word) * 0.7
                    if score > best_score:
                        best_score, best_sector = score, sector
                        best_hint_len = len(word)
                        best_exact_word = (word == hint_lower)

    if best_sector == "기타":
        return best_sector, True   # '기타'는 기존 경로대로 LLM 재분류 대상

    # ★ 저신뢰: 2글자 이하 힌트의 부분 매칭 + 키워드가 힌트보다 긴 복합어
    #   ('은행나무'·'금리는'·'대출은' 류) → LLM 재검증 필요
    confident = best_exact_word or best_hint_len >= 3 or len(kw_no_space) <= best_hint_len
    return best_sector, confident


# ── LLM Fallback 분류 ────────────────────────────────────────

_SECTOR_NAMES = list(SECTORS.keys()) + ["기타"]

# ── ★ 연속 실패 카운터 (사용자 박제 2026-06-07 — ERRORS [260])
# LLM 응답 형식 오류는 transient(일시적) — 매번 GUARDIAN report 하면 학습 자산 노이즈.
# N회 연속 실패할 때만 report → 진짜 모델/네트워크 문제만 보고.
_LLM_CONSECUTIVE_FAIL_COUNT = 0
_LLM_FAIL_REPORT_THRESHOLD = 3   # 3회 연속 실패 시만 GUARDIAN 보고


def _classify_with_llm(keywords: list[str], context: list[str] | None = None) -> dict[str, str]:
    """규칙으로 분류 못한 키워드를 Claude API로 배치 분류 후 캐시에 저장.

    ★ 견고성 강화 (사용자 박제 2026-06-07 — ERRORS [260]):
      1. raw 가 None/빈 문자열일 때 별도 분기 (ValueError 회피)
      2. 정규식 `\\{[\\s\\S]*\\}` (멀티라인 안전)
      3. 1회 실패 시 temperature 변경 후 재시도
      4. N회 연속 실패 시만 GUARDIAN report → 학습 자산 노이즈 방지
      5. 폴백: 모든 unknown 키워드 "기타" 로 캐시 → 다음 호출 즉시 재시도 안 함
    """
    global _LLM_CONSECUTIVE_FAIL_COUNT
    unknown = [kw for kw in keywords if kw not in _SECTOR_CACHE]
    if not unknown:
        return {kw: _SECTOR_CACHE[kw] for kw in keywords}

    from shared.llm import invoke_text as _inv
    from shared.personas import get as _persona

    sector_list = "\n".join(f"- {s}" for s in _SECTOR_NAMES)
    kw_list = "\n".join(f"{i+1}. {kw}" for i, kw in enumerate(unknown))
    ctx_hint = ""
    if context:
        ctx_hint = f"\n참고 — 같은 시기 트렌딩 키워드 전체: {', '.join(context)}\n(단편적 키워드는 위 전체 문맥을 보고 가장 연관된 섹터로 분류)"

    prompt = f"""다음 한국 검색 트렌드 키워드를 섹터로 분류하세요.
{ctx_hint}
섹터 목록:
{sector_list}

분류할 키워드:
{kw_list}

응답 형식 (JSON만, 설명 없이):
{{"1": "섹터명", "2": "섹터명"}}

분류 기준 (우선순위 순):
- 사람 이름(가수·배우·방송인·유튜버·강사) → 연예·문화 또는 교육·취업
- 스포츠 선수·팀·대회명 → 스포츠
- 정치인·검찰·법원·사회사건 → 사회·이슈
- 주가·금값·환율·유가·금리 관련 → 금융·투자 또는 경제·경기
- 교사·학생·시험·교육정책 → 교육·취업
- IT기업·앱·플랫폼 → IT·테크
- 단편적 키워드는 전체 문맥 보고 가장 가까운 섹터 선택 (기타 최소화)"""

    parsed: dict[str, str] = {}
    last_err: Exception | None = None

    # 실패 시 temperature 변경 후 재시도 (상한 = harness SSOT 파생, 2026-07-21: 2회)
    for attempt in range(_max_attempts()):
        try:
            temp = 0.0 if attempt == 0 else 0.3
            raw = _inv(
                "writer_short_analysis", prompt,
                system=_persona("jarvis03_radar"),
                max_tokens=512,
                temperature=temp,
            )
            # ① raw None/빈 — 별도 분기 (ValueError 회피)
            # [transient] 접두사 → severity.py 가 low 로 분류 → GUARDIAN 자동 수정 skip
            if not raw or not raw.strip():
                last_err = RuntimeError(f"[transient] LLM 응답 빈 문자열 (attempt={attempt+1})")
                continue

            # ② 정규식 강화 — \s\S 멀티라인 안전 + raw or ""
            m = re.search(r"\{[\s\S]*\}", raw or "")
            if not m:
                # 메시지 명확화 + raw 일부 디버그 정보 포함
                _snippet = (raw or "")[:120].replace("\n", " ")
                last_err = RuntimeError(
                    f"[transient] LLM 응답 JSON 형식 누락 (attempt={attempt+1}) — raw[:120]={_snippet!r}"
                )
                continue

            _raw_json = m.group()
            try:
                parsed = json.loads(_raw_json)
            except json.JSONDecodeError:
                # Invalid \escape 처리 (#367)
                _clean = re.sub(r'\\(?!["\\/bfnrtu0-9])', r'\\\\', _raw_json)
                parsed = json.loads(_clean)

            # 성공 시 루프 탈출
            last_err = None
            break

        except Exception as e:
            last_err = e
            continue

    # 파싱 결과 반영
    if parsed:
        for idx_str, sector in parsed.items():
            try:
                kw = unknown[int(idx_str) - 1]
                sector = sector if sector in _SECTOR_NAMES else "기타"
                _SECTOR_CACHE[kw] = sector
            except (IndexError, ValueError):
                pass
        _save_cache()
        _LLM_CONSECUTIVE_FAIL_COUNT = 0   # 성공 — 카운터 리셋
    else:
        # ④ 연속 실패 카운트 + N회 이상만 report (학습 자산 노이즈 방지)
        _LLM_CONSECUTIVE_FAIL_COUNT += 1
        _err_msg = f"[LLM분류 실패 #{_LLM_CONSECUTIVE_FAIL_COUNT}] {last_err}"
        print(_err_msg, file=sys.stderr)
        if _LLM_CONSECUTIVE_FAIL_COUNT >= _LLM_FAIL_REPORT_THRESHOLD:
            # 연속 3회 이상 — 진짜 문제일 가능성. GUARDIAN 에 보고
            _g_report(
                "radar",
                last_err or RuntimeError("LLM 분류 연속 실패"),
                module=__name__,
                context={
                    "consecutive_fail_count": _LLM_CONSECUTIVE_FAIL_COUNT,
                    "unknown_keywords": unknown[:10],
                    "kind": "transient_llm_format_error",  # GUARDIAN 분류 힌트
                },
            )
        # ⑤ 폴백: 모든 unknown 키워드 "기타" 로 캐시 → 다음 호출 시 재시도 안 함
        for kw in unknown:
            _SECTOR_CACHE.setdefault(kw, "기타")
        _save_cache()

    return {kw: _SECTOR_CACHE.get(kw, "기타") for kw in keywords}


# ── 트렌드 방향성 분석 ────────────────────────────────────────

def _calc_velocity(ratios: list[float]) -> tuple[str, float]:
    """DataLab 30일 ratio 리스트 → (방향성 라벨, 속도 점수 -20~+30)"""
    if len(ratios) < 7:
        return "신규", 5.0
    curr_7 = ratios[-7:]
    prev_7 = ratios[-14:-7] if len(ratios) >= 14 else ratios[:len(ratios) // 2]
    curr_avg = sum(curr_7) / len(curr_7) if curr_7 else 0
    prev_avg = sum(prev_7) / len(prev_7) if prev_7 else curr_avg
    if prev_avg < 1:
        change_pct = 100.0 if curr_avg > 0 else 0.0
    else:
        change_pct = (curr_avg - prev_avg) / prev_avg * 100

    if   change_pct >= 100: return "급등",  30.0
    elif change_pct >=  30: return "상승",  20.0
    elif change_pct >= -10: return "유지",   5.0
    elif change_pct >= -40: return "하락", -10.0
    else:                   return "급락", -20.0


# ── RADAR: 트렌드 점수 ────────────────────────────────────────

def score_keywords(
    trending: list[str],
    datalab: dict[str, list[float]] | None = None,
    competition: dict[str, float] | None = None,
) -> list[dict]:
    """
    트렌드 점수 계산 (DataLab velocity + 경쟁 강도 반영).
    datalab: {keyword: [ratio...]} — 없으면 순위 기반만 사용
    competition: {keyword: 0~100} — 없으면 50 기본값
    """
    total = len(trending)
    results: list[dict] = []
    llm_needed: list[str] = []
    datalab  = datalab or {}
    competition = competition or {}

    for i, kw in enumerate(trending):
        sector, _sec_conf = classify_keyword_conf(kw)
        rank_score = max(10, round(100 - (i / max(total, 1)) * 90))

        # DataLab velocity
        ratios = datalab.get(kw, [])
        velocity_label, velocity_score = _calc_velocity(ratios) if ratios else ("—", 0.0)

        # 경쟁 강도 (없으면 50 중립)
        comp_val = competition.get(kw, 50.0)

        item = {
            "keyword":        kw,
            "rank":           i + 1,
            "score":          rank_score,
            "sector":         sector,
            "velocity":       velocity_label,
            "velocity_score": velocity_score,
            "competition":    comp_val,
        }
        results.append(item)
        # ★ '기타' + 저신뢰 매칭(짧은 힌트 부분 포함 — '은행나무' 류) 모두 LLM 재분류 (ERRORS [290])
        if sector == "기타" or not _sec_conf:
            llm_needed.append(kw)

    if llm_needed:
        llm_results = _classify_with_llm(llm_needed)
        for item in results:
            if item["keyword"] in llm_results:
                item["sector"] = llm_results[item["keyword"]]

    return results


# ── ANALYST: 과거 성과 학습 ───────────────────────────────────

def get_performance_boost(keyword: str) -> float:
    """
    shared DB에서 해당 키워드의 과거 성과를 읽어 0~35 보너스 점수 반환.

    길1-B 패치 (2026-05-04): composite_score (avg_views + 검색 노출) 우선 사용.
    composite_score 가 없으면(옵션 B 미수집) avg_views 만으로 fallback.
      - composite_score 25점 만점: 250 → +25 (composite 250 = 강한 노출 또는 높은 조회수)
      - 재현성 10점 만점: post_count 5회 이상
    """
    try:
        from shared.db import get_keyword_performance
        kp = get_keyword_performance(keyword)
        if not kp:
            return 0.0
        post_count = kp.get("post_count", 0) or 0
        composite  = kp.get("composite_score") or 0
        avg_views  = kp.get("avg_views", 0) or 0

        # 메인 신호: composite_score 우선, 없으면 avg_views
        if composite > 0:
            main_score = min(25.0, composite / 10)   # composite 250 = 만점
        else:
            main_score = min(25.0, avg_views / 10)   # 250뷰 = 만점

        # 재현성 기여 (5회 이상 = 만점 10)
        consistency_bonus = min(10.0, post_count * 2.0)
        return round(main_score + consistency_bonus, 1)
    except Exception:
        return 0.0


# ── SEO: 기회 점수 ────────────────────────────────────────────

def get_freshness_bonus(keyword: str) -> float:
    """
    최근에 쓴 적 없는 키워드일수록 높은 점수 (0~20).
    30일 이상 안 쓴 키워드 = +20, 오늘 쓴 키워드 = 0.
    """
    try:
        from shared.db import get_keyword_performance
        from datetime import datetime, date
        kp = get_keyword_performance(keyword)
        if not kp or not kp.get("last_used"):
            return 20.0  # 한 번도 안 쓴 키워드 = 최고 점수
        last = datetime.fromisoformat(kp["last_used"]).date()
        days_ago = (date.today() - last).days
        return min(20.0, days_ago * 0.67)  # 30일 = +20점
    except Exception:
        return 20.0


# ── 학습 모듈 통합 (가중치·페널티·cold-start) ─────────────────
# learning.py 가 매번 DB hit 하지 않도록 5분 캐싱 — 학습은 주간 cron 이라 충분.
import time as _time
from JARVIS07_GUARDIAN.json_store import write_json

# 재시도 상한 — 파생 leaf 하나(`shared/limits.py`)에서 받는다.
#   ★ 여기에 accessor 를 다시 정의하지 말 것: 사본이 늘면 폴백 하나만 어긋나도
#     경로마다 재시도 횟수가 갈린다(①단일 진입점 · CLAUDE.md 재시도 상한 SSOT).
from shared.limits import max_attempts as _max_attempts


_WEIGHTS_CACHE = {"data": None, "ts": 0.0}
_WEIGHTS_TTL_SEC = 300.0

def default_weights() -> dict:
    """기본 가중치 — **주인은 `shared.db.DEFAULT_WEIGHTS` 한 곳** (① 단일 진입점).

    ★ 여기에 값을 다시 적지 말 것 (2026-08-14 정정). 종전 이 자리에 같은 값이
      한 벌 더 박혀 있었다 — `shared/db.py:DEFAULT_WEIGHTS`(학습 행이 없을 때
      `learned_weights_latest()` 가 돌려주는 값)의 사본이었다. 사본이 있으면
      한쪽만 손대는 순간 "학습 전"과 "학습 실패 폴백"이 서로 다른 기준으로
      채점한다. 값이 아니라 *주인* 을 참조한다.
    """
    from shared.db import DEFAULT_WEIGHTS as _DW   # lazy — import 시 init_db 부작용 회피
    return dict(_DW)


def apply_weights(weights: dict | None, *, trend_score: float,
                  perf: float, fresh: float) -> float:
    """가중치 → 원점수(raw). ★ **채점식의 단일 진입점** (① 단일 진입점).

    ★ 왜 함수로 뽑았나 (2026-08-14 사고의 근본)
      종전엔 같은 채점식이 두 곳에 *서로 다르게* 박혀 있었다 —
        · `learning.train_weights` 는 절편 포함 아핀 모델을 적합해 `intercept` 를 저장하고
        · `analyzer.opportunity_score` 는 **절편 항이 없는** 식으로 채점했다.
      즉 학습이 맞춰 놓은 모델과 실제로 채점되는 모델이 달랐다. 계수만 SCALE 배 하고
      절편을 버리면 아핀 관계가 깨져, 계수가 조금만 작아져도 raw 가 통째로 음수로
      내려간다(실측 2026-08-14: 학습 가중치로 일일 배치 50개가 전원 0.0 → 정렬키 사망
      → 경제 브리핑 주제 선정 붕괴 → 07:00 네이버·티스토리 동시 발행 실패).
      → 학습 쪽 변별력 가드와 적용 쪽 채점이 **이 함수 하나** 를 쓴다.

    키 누락 시 기본값도 여기 적지 않는다 — `default_weights()` 에서 파생한다(②).
    """
    w = {**default_weights(), **(weights or {})}
    return (
        trend_score * float(w["w_trend"])
        + perf      * float(w["w_perf"])
        + fresh     * float(w["w_fresh"])
        + float(w.get("intercept", 0.0) or 0.0)
    )


# 표시 점수의 범위 — 값의 주인은 여기 한 곳(①). 클립 식을 다른 파일에 다시 쓰지 말 것.
SCORE_MIN, SCORE_MAX = 0.0, 150.0


def clamp_score(raw: float) -> float:
    """원점수 → 표시 점수(클립 + 반올림). ★ **클립의 단일 진입점**.

    학습 관문의 적용형상 프로브도 이 함수를 쓴다 — 프로브가 클립을 스스로 다시
    구현하면 "관문이 본 점수" 와 "실제로 매겨지는 점수" 가 갈린다. 하한 클립이야말로
    2026-08-14 사고에서 음수 raw 를 전원 0.0 으로 만든 지점이므로, 관문은 반드시
    *클립 이후* 를 봐야 한다.
    """
    return round(max(SCORE_MIN, min(SCORE_MAX, float(raw))), 1)


def _get_learned_weights() -> dict:
    """학습된 가중치 (없으면 기본값) — 5분 캐싱."""
    now = _time.time()
    if _WEIGHTS_CACHE["data"] is None or (now - _WEIGHTS_CACHE["ts"]) > _WEIGHTS_TTL_SEC:
        try:
            from JARVIS03_RADAR import learning as _learning
            _WEIGHTS_CACHE["data"] = _learning.get_current_weights()
        except Exception:
            _WEIGHTS_CACHE["data"] = default_weights()
        _WEIGHTS_CACHE["ts"] = now
    return _WEIGHTS_CACHE["data"]


def _learning_penalty(keyword: str, sector: str = "") -> tuple[float, float, float]:
    """(negative_signal, feedback, cold_start) — 모두 0 fallback."""
    neg, fb, cold = 0.0, 0.0, 0.0
    try:
        from JARVIS03_RADAR import learning as _learning
        neg  = _learning.get_negative_signal_penalty(keyword)
        fb   = _learning.get_feedback_penalty(keyword, sector)
        cold = _learning.get_cold_start_boost(keyword)
    except Exception:
        pass
    return neg, fb, cold


def opportunity_score(
    keyword: str,
    trend_score: int,
    velocity_score: float = 0.0,
    competition: float = 50.0,
    sector: str = "",
    weights: dict | None = None,
) -> float:
    """
    RADAR(트렌드) + ANALYST(성과) + SEO(신선도) 통합 점수.

    ★ velocity_score·competition 인자는 호환 위해 남기되 *점수엔 반영 안 함* (ERRORS [485]) —
      수집 경로가 없어 학습 불가한 유령 피처였다. 화면 표시(속도 라벨)용으로만 계산된다.

    학습 통합:
      - 가중치 (w_trend / w_perf / w_fresh): learning.get_current_weights()
        — 매주 일요일 train_weights() 갱신, 데이터 부족 시 DEFAULT
      - negative_signal_penalty: 평균 미달 키워드 -15 ~ 0
      - feedback_penalty: 누적 거부 시 -30 ~ 0 (sector + keyword 합산)
      - cold_start_boost: 신규 키워드 임베딩 cosine top-3 perf 가중평균 × 0.5

    실측 기여 (DEFAULT_WEIGHTS 기준):
      trend:  ~45  (10–100 × 0.45)
      perf:   ~35  (0–35   × 1.00)
      fresh:  ~17  (0–20   × 0.85)
      페널티/부스트: -45 ~ +12

    `weights` — 지정 시 학습 캐시 대신 이 값 사용 (`enrich_with_opportunity()` 가
    배치 변별력 붕괴 감지 시 DEFAULT_WEIGHTS 로 재계산할 때 사용).
    """
    perf  = get_performance_boost(keyword)   # 0~35 (avg + 재현성)
    fresh = get_freshness_bonus(keyword)      # 0~20

    # ★ velocity·competition 제거 (ERRORS [485]) — 수집 경로가 없어 학습 불가한 유령 피처였다.
    #   점수 공식에서도 빼 실제 학습된 3개 입력(trend·perf·fresh)만 반영한다.
    w = weights if weights is not None else _get_learned_weights()
    raw = apply_weights(w, trend_score=trend_score, perf=perf, fresh=fresh)

    # 학습 통합 — 양방향 (부정 / 피드백 / cold-start)
    neg, fb, cold = _learning_penalty(keyword, sector)
    raw += neg + fb + cold

    return clamp_score(raw)


# ── 통합 파이프라인 ───────────────────────────────────────────

def enrich_with_opportunity(scored: list[dict]) -> list[dict]:
    """score_keywords() 결과에 opportunity_score를 추가.

    ★ 학습 가중치는 *학습 시점* 표본에서만 변별력을 검증한다(learning.train_weights 의
      가드). 그런데 적용 시점의 실제 배치는 학습 표본과 분포가 다를 수 있다 — 예:
      RADAR 가 매일 뽑는 후보는 대부분 '한 번도 안 쓴' 신규 키워드라 freshness 가
      거의 전원 20.0(최고점) 으로 고정되는데, 학습 표본엔 재사용 키워드도 섞여 있어
      그때는 가드를 통과했다. 그 가중치를 이 배치에 적용하면 (w_fresh 가 학습으로
      음수가 된 경우) 전원이 0.0 으로 클립되어 정렬키가 완전히 죽는다
      (실측 2026-08-14: scored_keywords 50개 전원 0.0 → topic_pack 후보 선정이
      경제와 무관한 잡음 키워드로 뒤집힘 → TopicPackNoCandidate).
      학습 시점과 동일한 판정(distinct_ratio)을 *이 배치* 에도 적용해 감지 →
      기본 가중치로 즉시 재계산(이전 학습 결과를 폐기하지 않음 — 다음 배치가
      변별력을 회복하면 학습 가중치가 다시 쓰인다).

    ★ 이 가드는 **오류를 보고하지 않는다** (2026-08-14 정정)
      종전엔 발동할 때마다 `report(RuntimeError(...))` 를 불렀다. 그런데 폴백 발동은
      *정상 동작* 이지 코드 버그가 아니라서 GUARDIAN 의 Tier1·Tier2 가 고칠 수 없고,
      고칠 수 없으니 `status='new'` 로 남아 `j07_retry_pending`(10분)이 영원히 재투입했다.
      실측: 오류 6행이 07:18~13:10 동안 Circuit-Breaker 알림 74통을 만들었고, 그 사이
      시간당 수리 토큰 10개를 전부 잠식해 **진짜 신규 오류의 수리 용량이 0** 이 됐다.
      → 폴백은 로그로 남긴다. 정말 고쳐야 할 것(썩은 학습 가중치)은 학습 관문
        (`learning.train_weights` 의 적용형상 프로브)이 애초에 저장을 막는다.
    """
    weights = _get_learned_weights()
    for item in scored:
        item["opportunity_score"] = opportunity_score(
            item["keyword"],
            item["score"],
            item.get("velocity_score", 0.0),
            item.get("competition", 50.0),
            sector=item.get("sector", ""),
            weights=weights,
        )

    if len(scored) >= 5:
        from JARVIS03_RADAR.learning import is_ranking_degenerate
        out_vals = [it["opportunity_score"] for it in scored]
        in_vals  = [it["score"] for it in scored]
        if is_ranking_degenerate(out_vals, in_vals):
            _fallback = default_weights()
            # 경고 한 줄로 남긴다 — 오류로 신고하지 않는 이유는 docstring 참조.
            print(f"[RADAR] ⚠️ opportunity_score 배치 변별력 붕괴 — 학습 가중치 "
                  f"(id={weights.get('id')}, learned_at={weights.get('learned_at')}) 를 "
                  f"이 배치({len(scored)}건)에 적용 시 정렬키가 뭉침 → 기본 가중치로 재계산")
            for item in scored:
                item["opportunity_score"] = opportunity_score(
                    item["keyword"],
                    item["score"],
                    item.get("velocity_score", 0.0),
                    item.get("competition", 50.0),
                    sector=item.get("sector", ""),
                    weights=_fallback,
                )

    return scored


def build_sector_summary(scored: list[dict]) -> dict[str, list[dict]]:
    summary: dict[str, list[dict]] = {}
    for item in scored:
        summary.setdefault(item["sector"], []).append(item)
    for kws in summary.values():
        kws.sort(key=lambda x: x.get("opportunity_score", x["score"]), reverse=True)
    return summary


def generate_recommendations(sector_summary: dict[str, list[dict]], n: int = 5) -> list[dict]:
    """opportunity_score 기준 최적 블로그 주제 추천 (LLM 각도 없는 빠른 버전)."""
    recs = []
    sector_avg = {
        s: sum(k.get("opportunity_score", k["score"]) for k in kws) / len(kws)
        for s, kws in sector_summary.items() if kws
    }
    for sector in sorted(sector_avg, key=lambda x: sector_avg[x], reverse=True)[:n]:
        kws = sector_summary.get(sector, [])
        if not kws:
            continue
        top = kws[0]
        opp = top.get("opportunity_score", top["score"])
        recs.append({
            "theme":             top["keyword"],
            "topic":             top["keyword"],  # LLM 각도가 없을 때 기본값
            "keyword":           top["keyword"],
            "sector":            sector,
            "score":             top["score"],
            "opportunity_score": opp,
            "velocity":          top.get("velocity", "—"),
            "competition":       top.get("competition", 50.0),
            "reason":            _build_reason(top),
            "angle":             "",
            "hook":              "",
            "color":             SECTOR_COLORS.get(sector, "#95A5A6"),
        })
    return recs


def _build_reason(kw: dict) -> str:
    parts = [f"트렌드 순위 #{kw['rank']}"]
    velocity = kw.get("velocity", "—")
    if velocity not in ("—", "유지", "신규"):
        parts.append(velocity)
    comp = kw.get("competition")
    if comp is not None:
        if comp < 30:
            parts.append("경쟁 낮음 (블루오션)")
        elif comp < 60:
            parts.append("경쟁 보통")
        else:
            parts.append("경쟁 높음 (레드오션)")
    opp = kw.get("opportunity_score", kw["score"])
    if opp > kw["score"]:
        parts.append("과거 성과 우수")
    if get_freshness_bonus(kw["keyword"]) >= 15:
        parts.append("최근 미발행")
    return " · ".join(parts)


def generate_content_angles(recs: list[dict], autocomplete: dict[str, list[str]] | None = None) -> list[dict]:
    """LLM + 경쟁사 분석으로 추천 키워드별 콘텐츠 각도 생성. 수집 시 1회만 호출."""
    if not recs:
        return recs
    autocomplete = autocomplete or {}

    competitor_data: dict[str, dict] = {}

    try:
        from shared.llm import invoke_text as _inv_ca

        kw_lines = []
        for i, rec in enumerate(recs, 1):
            kw      = rec["keyword"]
            related = (autocomplete.get(kw) or [])[:5]
            rel_str = f" (연관검색: {', '.join(related)})" if related else ""
            vel     = rec.get("velocity", "—")
            comp    = rec.get("competition", 50.0)
            comp_lv = "블루오션" if comp < 30 else "보통" if comp < 70 else "레드오션"
            comp_gap = competitor_data.get(kw, {}).get("gap", "")
            gap_str  = f" | 경쟁사공백: {comp_gap}" if comp_gap else ""
            kw_lines.append(
                f"{i}. [{rec['sector']}] {kw}{rel_str} | 방향:{vel} | 경쟁:{comp_lv} | 기회:{rec.get('opportunity_score',0):.0f}{gap_str}"
            )

        prompt = f"""오늘 한국 인기 검색 트렌드 키워드 기반 블로그 콘텐츠 각도를 생성하세요.

키워드 목록:
{chr(10).join(kw_lines)}

응답 형식 (JSON 배열만, 설명 없이):
[
  {{
    "idx": 1,
    "keyword": "정규화된 키워드 (오타·축약 수정, 브랜드·제품명은 공식 명칭 사용)",
    "title": "클릭을 유도하는 구체적인 블로그 포스팅 제목",
    "angle": "이 각도/관점이 효과적인 이유 (한 줄)",
    "hook": "이 글을 읽어야 하는 독자의 핵심 궁금증 (한 줄)"
  }}
]

규칙:
- keyword 필드: 오타·잘못된 음절 수정 필수. 예) 폴레드→폴드, 갤럭시S25울트라→갤럭시 S25 Ultra, 테슬나→테슬라. 브랜드·제품·기업명은 반드시 공식 표기 사용.
- 제목에 "완전 가이드" "총정리" 형태 금지
- 독자의 실제 궁금증·불안·욕구와 직접 연결되는 제목
- 트렌드 맥락(방향, 경쟁 수준)을 제목에 반영
- 블루오션 키워드는 차별화 각도로, 레드오션은 더 좁은 타겟으로
- 경쟁사공백 정보가 있으면 그 빈 틈을 파고드는 제목 우선"""

        from shared.personas import get as _persona
        raw = _inv_ca("writer_short_analysis", prompt,
                      system=_persona("jarvis03_radar"), max_tokens=1024)
        raw = (raw or "").strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            angles = json.loads(m.group())
            for angle in angles:
                idx = int(angle.get("idx", 0)) - 1
                if 0 <= idx < len(recs):
                    # 브랜드·제품명 정규화 (LLM이 오타 수정한 경우 반영)
                    normalized_kw = angle.get("keyword", "").strip()
                    if normalized_kw and normalized_kw != recs[idx]["keyword"]:
                        print(f"[RADAR] 키워드 정규화: '{recs[idx]['keyword']}' → '{normalized_kw}'")
                        recs[idx]["keyword"] = normalized_kw
                        recs[idx]["theme"]   = normalized_kw
                    recs[idx]["topic"]          = angle.get("title", recs[idx]["topic"])
                    recs[idx]["angle"]          = angle.get("angle", "")
                    recs[idx]["hook"]           = angle.get("hook",  "")
                    # 경쟁사 차별화 데이터 병합
                    kw = recs[idx]["keyword"]
                    if kw in competitor_data:
                        recs[idx]["competitor_gap"]   = competitor_data[kw].get("gap", "")
                        recs[idx]["competitor_title"]  = competitor_data[kw].get("title", "")
    except Exception as e:
        print(f"[LLM각도생성 실패] {e}", file=sys.stderr)
        _g_report("radar", e, module=__name__)
    return recs
