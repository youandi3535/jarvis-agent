"""
트렌딩 키워드 → theme_list.txt 매핑 엔진

흐름:
  scored_keywords (RADAR 수집·점수화 결과)
    → 각 키워드가 theme_list.txt 어떤 테마와 연관되는지 스코어링
    → 기회점수 가중합산 → 테마 순위 반환

매칭 방식 (3단계 복합):
  1. 직접 단어 일치  — 트렌딩어 단어 ∩ 테마명 단어
  2. 부분 문자열    — 테마명 핵심어가 트렌딩어에 포함
  3. 동의어 사전    — 테마명에 없는 표준 외 표현 보완
"""
import re
from pathlib import Path

_THEME_LIST = Path(__file__).parent.parent / "JARVIS02_WRITER" / "theme_list.txt"

# ── 동의어 사전: "트렌딩 표현" → 매칭할 테마명 키워드 ─────────────────
# 테마명 자체에 없는 표현만 기재. 테마명 단어는 자동 추출됨.
_SYNONYM: dict[str, list[str]] = {
    # 반도체·메모리
    "엔비디아":    ["HBM", "시스템반도체", "AI챗봇"],
    "TSMC":       ["반도체"],
    "AMD":        ["시스템반도체"],
    "인텔":       ["반도체", "시스템반도체"],
    "SK하이닉스": ["반도체", "HBM"],
    "삼성전자":   ["반도체", "갤럭시", "OLED"],
    # 배터리·전기차
    "배터리":     ["2차전지", "전력저장장치"],
    "EV":         ["전기차"],
    "LG에너지":   ["2차전지"],
    "삼성SDI":    ["2차전지"],
    "SK온":       ["2차전지"],
    "CATL":       ["2차전지"],
    # AI·로봇
    "GPT":        ["AI챗봇"],
    "LLM":        ["AI챗봇"],
    "챗GPT":      ["AI챗봇"],
    "Claude":     ["AI챗봇"],
    "Gemini":     ["AI챗봇"],
    "엔피유":     ["온디바이스 AI"],
    "NPU":        ["온디바이스 AI"],
    "옵티머스":   ["휴머노이드 로봇"],
    "보스턴다이나믹스": ["로봇"],
    "레인보우로보틱스": ["로봇"],
    # 바이오·제약
    "위고비":     ["비만치료제"],
    "오젬픽":     ["비만치료제"],
    "마운자로":   ["비만치료제"],
    "GLP-1":      ["비만치료제"],
    "셀트리온":   ["바이오시밀러"],
    "삼성바이오": ["바이오시밀러"],
    "화이자":     ["화이자"],
    "모더나":     ["모더나"],
    # 에너지
    "원전":       ["원자력발전"],
    "SMR":        ["원자력발전"],
    "수소차":     ["수소에너지"],
    "K방산":      ["방위산업"],
    "방산":       ["방위산업"],
    "누리호":     ["우주항공산업"],
    # 가상자산·금융
    "비트코인":   ["가상화폐"],
    "이더리움":   ["가상화폐"],
    "코인":       ["가상화폐"],
    "업비트":     ["두나무"],
    "기준금리":   ["은행"],
    "환율":       ["환율하락 수혜"],
    "원달러":     ["환율하락 수혜"],
    "공모주":     ["신규상장"],
    "IPO":        ["신규상장"],
    # 자동차·조선
    "현대차":     ["자동차", "전기차", "수소에너지"],
    "기아":       ["자동차", "전기차"],
    "HD현대중공업": ["조선"],
    "대우조선":   ["조선"],
    "삼성중공업": ["조선"],
    # IT·플랫폼
    "네이버":     ["인터넷 대표주", "AI챗봇"],
    "카카오":     ["인터넷 대표주", "핀테크"],
    "쿠팡":       ["쿠팡"],
    "포스코":     ["철강", "리튬"],
    "K뷰티":      ["화장품"],
    "드라마":     ["엔터테인먼트", "영상콘텐츠"],
    "K팝":        ["엔터테인먼트"],
    "하이브":     ["엔터테인먼트"],
}

# 매칭에서 제외할 불용어 (단독으로 의미 없는 단어)
_STOP = {
    "등", "및", "또는", "관련", "수혜", "대표", "주요", "개발", "발표",
    "기업", "회사", "주식", "투자", "증권", "상장", "코스피", "코스닥",
    "급등", "급락", "상승", "하락", "강세", "약세", "반등", "하한가",
    "오늘", "이슈", "뉴스", "속보", "최신",
}


def _load_themes() -> list[str]:
    return [t.strip() for t in _THEME_LIST.read_text("utf-8").splitlines() if t.strip()]


def _extract_words(text: str) -> set[str]:
    """텍스트에서 유효 단어 추출 — 길이 기준은 length_manager.RADAR_KW_THEME_MIN."""
    raw = set(re.findall(r'[가-힣A-Za-z0-9]{2,}', text))
    return raw - _STOP


def _build_index(themes: list[str]) -> dict[str, set[str]]:
    """테마명 → 키워드 셋 (테마명 단어 + 괄호 내용 분리)."""
    idx: dict[str, set[str]] = {}
    for t in themes:
        words = _extract_words(t)
        # 괄호 안 내용 별도 추출 (예: "HBM(고대역폭메모리)" → "고대역폭메모리")
        for bracket in re.findall(r'\(([^)]+)\)', t):
            words.update(_extract_words(bracket))
        idx[t] = words
    return idx


def match_themes(scored_keywords: list[dict], top_n: int = 15) -> list[dict]:
    """
    RADAR scored_keywords를 theme_list.txt 테마에 매핑 후 점수 순 반환.

    Args:
        scored_keywords: [{"keyword":str, "opportunity_score":float, ...}, ...]
        top_n: 반환할 최대 테마 수

    Returns:
        [{"theme":str, "score":float, "matched_by":[str,...]}, ...]
    """
    themes = _load_themes()
    theme_idx = _build_index(themes)

    scores:   dict[str, float]      = {}
    matched:  dict[str, list[str]]  = {}

    for item in scored_keywords:
        kw  = str(item.get("keyword", ""))
        opp = float(item.get("opportunity_score", item.get("score", 0)))
        if opp <= 0:
            continue

        kw_words  = _extract_words(kw)
        kw_lower  = kw.lower()

        for theme, t_words in theme_idx.items():
            gain = 0.0
            hits: list[str] = []

            # ① 직접 단어 교집합
            common = kw_words & t_words
            if common:
                gain += len(common) * opp
                hits += list(common)

            # ② 테마 키워드가 트렌딩어 안에 포함 (length_manager.RADAR_KW_THEME_MIN 이상만)
            from JARVIS02_WRITER import length_manager as _LM
            for tw in t_words:
                if len(tw) >= _LM.RADAR_KW_THEME_MIN and tw not in common and tw in kw:
                    gain += opp * 0.8
                    hits.append(tw)

            # ③ 동의어 사전
            for syn, targets in _SYNONYM.items():
                if syn.lower() in kw_lower:
                    for tgt in targets:
                        if tgt and (tgt in theme or
                                    any(tgt in tw for tw in t_words)):
                            gain += opp * 1.2
                            hits.append(f"{syn}→{tgt}")

            if gain > 0:
                scores[theme]  = scores.get(theme, 0.0) + gain
                matched.setdefault(theme, [])
                matched[theme].extend(hits)

    result = []
    for theme, score in sorted(scores.items(), key=lambda x: -x[1])[:top_n]:
        result.append({
            "theme":      theme,
            "score":      round(score, 1),
            "matched_by": list(dict.fromkeys(matched.get(theme, [])))[:5],
        })

    return result
