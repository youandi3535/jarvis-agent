"""
JARVIS02_WRITER / trend_economic_writer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
티스토리·네이버 아침 경제 관련 글 — 트렌드 기반 독립 발행 모듈

역할:
  - JARVIS03 트렌드를 읽어 플랫폼별 다른 주제로 발행
  - 주제 선정·원고·이미지 모두 플랫폼별 독립 (공유 없음)

이미지 디렉터리:
  JARVIS06_IMAGE/output/images/economic_tistory/  ← 티스토리 전용
  JARVIS06_IMAGE/output/images/economic_naver/    ← 네이버 전용

진입점:
  run_tistory()  ← economic_poster.run()에서 post_tistory=True 시 호출 (또는 retry)
  run_naver()    ← economic_poster.run()에서 post_naver=True 시 호출 (또는 retry)

포맷 (★ 분량은 length_manager / post_type_specs 위임 — 본 docstring 박제 X):
  티스토리 — 생활 밀착 Q&A형 (다음 검색 중심, 실용적)
"""

from __future__ import annotations

import os
import re
import sys
import base64
import hashlib
import random
import shutil
from pathlib import Path
from datetime import date, datetime

from dotenv import load_dotenv

# ── JARVIS07 오류 보고 API ───────────────────────────
try:
    from JARVIS07_GUARDIAN.error_collector import report as _g_report
except ImportError:
    def _g_report(*a, **kw): pass
# ─────────────────────────────────────────────────────

load_dotenv()

_JARVIS_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_JARVIS_ROOT))

BASE_DIR          = Path(__file__).parent
JARVIS06_BASE     = BASE_DIR.parent / "JARVIS06_IMAGE"             # 이미지 단일 진입점 (CLAUDE.md 규정)
TISTORY_IMG_DIR   = JARVIS06_BASE / 'output' / 'images' / 'economic_tistory'
NAVER_IMG_DIR     = JARVIS06_BASE / 'output' / 'images' / 'economic_naver'
TISTORY_IMG_DIR.mkdir(parents=True, exist_ok=True)
NAVER_IMG_DIR.mkdir(parents=True, exist_ok=True)

TODAY_STR  = date.today().strftime("%Y-%m-%d")
TODAY      = date.today()
TODAY_DOW  = ['월', '화', '수', '목', '금', '토', '일'][date.today().weekday()]
TODAY_PREFIX = f"[{TODAY.month}/{TODAY.day}]"

# ADR 008 Phase 2 — 카테고리 상수 단일 진입점 (JARVIS08_PUBLISH/category)
from JARVIS08_PUBLISH.category import ECONOMIC_CATEGORY  # noqa: F401

# length_manager 단일 진입점
try:
    from JARVIS02_WRITER import length_manager as _L
except ImportError:
    import length_manager as _L  # 같은 폴더 직접 실행 시


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. JARVIS03 트렌드 데이터 로더
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_today_trends() -> dict:
    """JARVIS03 RADAR 트렌드 로드 — 다단 폴백 자율 회복.

    ★ 사용자 박제 2026-06-07 — 3단 폴백 + 강제 캐싱:
      ① 오늘 + 최근 14일 파일/radar_main.load() (확장)
      ② 14일 모두 실패 시 → LLM 즉석 폴백 (_build_emergency_trends)
      ③ LLM 도 실패 시 → 빈 dict (발행 skip — 마지막 방어)

    fallback 이유: ERRORS [84] (2026-05-14) → [264] (2026-06-07) 재발 패턴.
    RADAR cron 이 N일 안 돌면 5일 fallback 도 빈손 → 발행 통째 skip 사고.
    LLM 폴백으로 RADAR 장기 부재에도 *자율 회복* 보장.
    """
    import json
    from datetime import timedelta as _td
    radar_data_dir = _JARVIS_ROOT / 'JARVIS03_RADAR' / 'data'

    for days_back in range(0, 14):   # ★ 5 → 14일 확대 (2026-06-07)
        d = (date.today() - _td(days=days_back)).strftime("%Y-%m-%d")
        # ① radar_main.load() 시도
        try:
            from JARVIS03_RADAR.radar_main import load as _radar_load
            data = _radar_load(d)
            if data and data.get('scored_keywords'):
                kw_cnt = len(data.get('scored_keywords', []))
                if days_back == 0:
                    print(f"  ✅ JARVIS03 트렌드 로드: {kw_cnt}개 키워드 (오늘)")
                else:
                    print(f"  ⚠️ 오늘 트렌드 없음 → {days_back}일 전({d}) {kw_cnt}개 키워드 사용 (fallback)")
                return data
        except Exception as e:
            if days_back == 0:
                print(f"  ⚠️ JARVIS03 트렌드 로드 실패 ({e}) → 파일 직접 읽기")
                _g_report("writer", e, module=__name__)
        # ② JSON 파일 직접 읽기
        path = radar_data_dir / f'trends_{d}.json'
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if data and data.get('scored_keywords'):
                    kw_cnt = len(data.get('scored_keywords', []))
                    if days_back == 0:
                        print(f"  ✅ 트렌드 파일 로드: {path.name} ({kw_cnt}개)")
                    else:
                        print(f"  ⚠️ 오늘 트렌드 없음 → {days_back}일 전 파일 {path.name} 사용 ({kw_cnt}개, fallback)")
                    return data
            except Exception as e:
                print(f"  ⚠️ 트렌드 파일 파싱 실패 ({path.name}): {e}")

    # ★ ② 14일 fallback 실패 → LLM 즉석 폴백 (자율 회복)
    print(f"  ⚠️ 최근 14일간 트렌드 파일 없음 → LLM 즉석 폴백 시도 (ERRORS [264])")
    emergency = _build_emergency_trends()
    if emergency and emergency.get('scored_keywords'):
        # 폴백 결과를 *오늘 trends 파일로 캐싱* → 같은 날 재호출 시 LLM 재호출 없음
        try:
            today_path = radar_data_dir / f"trends_{date.today().strftime('%Y-%m-%d')}.json"
            radar_data_dir.mkdir(parents=True, exist_ok=True)
            today_path.write_text(json.dumps(emergency, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f"  💾 [emergency_trends] 폴백 결과 {today_path.name} 캐싱 — 같은 날 재발행은 즉시 로드")
        except Exception as _ce:
            print(f"  ⚠️ [emergency_trends] 캐싱 실패 (무시): {_ce}")
        return emergency

    # ★ ③ LLM 폴백도 실패 → 빈 dict (최후의 방어선)
    print(f"  ❌ 트렌드 데이터·LLM 폴백 모두 실패 → 빈 dict (발행 불가)")
    return {}


def _build_emergency_trends() -> dict:
    """RADAR 장기 부재 시 Sonnet 즉석 폴백 — 오늘 경제 핫이슈 5개 생성.

    ★ 사용자 박제 2026-06-07 (ERRORS [264]) — load_today_trends 의 14일 폴백 실패 시
    호출. RADAR scored_keywords 와 동일 스키마로 반환하여 select_*_topic 함수가
    수정 없이 그대로 작동.

    실패 시 빈 dict 반환 — 호출자(load_today_trends)는 마지막으로 빈 dict 반환.
    """
    try:
        from shared.llm import invoke_text
        today = date.today().strftime("%Y-%m-%d")
        prompt = (
            f"오늘은 {today} 이다. 한국 경제 블로그 발행용 *경제·금융 관련* 핫이슈 5개를 JSON 으로 출력해라.\n\n"
            "[형식]\n"
            "{\n"
            '  "scored_keywords": [\n'
            '    {"keyword": "...", "sector": "경제·경기" | "금융·투자" | "에너지·환경" | "IT·테크",\n'
            '     "score": 70.0, "topic": "..."},\n'
            "    ...\n"
            "  ],\n"
            '  "recommendations": [\n'
            '    {"keyword": "...", "sector": "...", "opportunity_score": 80.0,\n'
            '     "reason": "한 문장 사유", "topic": "...", "theme": "..."},\n'
            "    ...\n"
            "  ]\n"
            "}\n\n"
            "- keyword: 짧고 구체적인 경제 사건/주제 (예: '미 연준 금리 인하', '엔비디아 실적 발표')\n"
            "- sector: 위 4종 중 하나만 사용\n"
            "- score / opportunity_score: 60~95 사이 실수\n"
            "- scored_keywords 5개, recommendations 최소 3개\n"
            "- 한국 시장과 직접 연관 있는 주제 우선\n"
            "- JSON 만 출력. 다른 텍스트·코드 블록·주석 금지."
        )
        raw = invoke_text("analyzer", prompt, max_tokens=1500, temperature=0.5)
        if not raw:
            return {}
        # JSON 블록 추출 — 마크다운 코드 블록·접두 텍스트 제거
        import re as _re, json as _j
        cleaned = _re.sub(r'^```(?:json)?\s*', '', raw.strip())
        cleaned = _re.sub(r'\s*```$', '', cleaned).strip()
        m = _re.search(r'\{[\s\S]+\}', cleaned)
        if not m:
            print(f"  ⚠️ [emergency_trends] JSON 추출 실패")
            return {}
        data = _j.loads(m.group(0))
        sk = data.get("scored_keywords", [])
        rec = data.get("recommendations", [])
        if not sk:
            print(f"  ⚠️ [emergency_trends] scored_keywords 비어있음")
            return {}
        # 메타데이터 박제 — 추후 발행 글에서 'emergency 폴백' 식별 가능
        data["_emergency_fallback"] = True
        data["_generated_at"] = today
        data["recommendations"] = rec or []
        print(f"  🆘 [emergency_trends] LLM 즉석 생성 {len(sk)}개 키워드 / {len(rec)}개 추천 (RADAR 부재 자율 회복)")
        return data
    except Exception as e:
        print(f"  ❌ [emergency_trends] 실패: {e}")
        _g_report("writer", e, module=__name__)
        return {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 주제 선정 — 경제/금융 관련 트렌드 필터링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_ECON_SECTORS = {
    # JARVIS03 실제 섹터명 기준 (확인: 2026-05-08)
    '경제·경기', '금융·투자', '에너지·환경', 'IT·테크',
    # 추후 추가될 수 있는 섹터명
    '금융·은행', '주식·투자', '부동산', '산업·기업',
    '기술·IT', '에너지·자원', '무역·통상', '정책·규제', '글로벌·해외',
}
_EXCLUDE_SECTORS = {'스포츠', '연예·문화', '정치·사회', '날씨·재난', '기타', '건강·의료', '사회·이슈'}

# ── 최근 사용 키워드 추적 (중복 발행 방지) ────────────────────────
_USED_KW_FILE = _JARVIS_ROOT / "JARVIS03_RADAR" / "data" / "used_economic_keywords.json"
_DEDUP_DAYS   = 7  # 최근 N일 이내 사용된 키워드 제외


def _get_used_keywords(days: int = _DEDUP_DAYS) -> set[str]:
    """최근 N일 동안 발행에 사용된 키워드 집합 반환."""
    import json as _json
    if not _USED_KW_FILE.exists():
        return set()
    try:
        data: list[dict] = _json.loads(_USED_KW_FILE.read_text(encoding="utf-8"))
        cutoff = (date.today() - __import__("datetime").timedelta(days=days)).isoformat()
        return {e["keyword"].strip().lower() for e in data if e.get("date", "") >= cutoff}
    except Exception:
        return set()


def _mark_keyword_used(keyword: str, platform: str = "") -> None:
    """발행 성공 키워드를 영구 기록 (중복 방지용)."""
    import json as _json
    data: list[dict] = []
    if _USED_KW_FILE.exists():
        try:
            data = _json.loads(_USED_KW_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = []
    data.append({"keyword": keyword, "platform": platform, "date": date.today().isoformat()})
    # 30일 초과 항목 자동 정리
    cutoff = (date.today() - __import__("datetime").timedelta(days=30)).isoformat()
    data = [e for e in data if e.get("date", "") >= cutoff]
    try:
        _USED_KW_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USED_KW_FILE.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _e:
        print(f"  ⚠️ [dedup] 키워드 기록 실패: {_e}")


def _normalize_keyword(kw: str) -> str:
    """알려진 오표기·축약어를 공식 표기로 교정."""
    _MAP = {
        '폴레드': '폴드', '갤럭시S25울트라': '갤럭시 S25 Ultra', '테슬나': '테슬라',
        '애플리': '애플', '엔비디아엔': '엔비디아', '챗지피티': 'ChatGPT',
        '빙에이아이': 'Bing AI', '메타에이아이': 'Meta AI',
    }
    return _MAP.get(kw.strip(), kw.strip())


def _is_same_topic(rec: dict | str, ref_keyword: str) -> bool:
    """추천 항목(또는 키워드 문자열)이 ref_keyword와 동일 주제인지 판단.

    keyword·topic·theme 세 필드 모두 양방향 부분 포함으로 검사.
    """
    if not ref_keyword:
        return False
    b = ref_keyword.strip().lower()
    if isinstance(rec, str):
        a = rec.strip().lower()
        return a == b or a in b or b in a
    for field in ('keyword', 'topic', 'theme'):
        a = rec.get(field, '').strip().lower()
        if a and (a == b or a in b or b in a):
            return True
    return False


def _topic_econ_fit(keyword: str, sector: str) -> bool | None:
    """경제·금융 브리핑 주제 적합성 LLM 판정 (ERRORS [290] — 2026-07-03).

    '은행나무'(임업)가 '은행' 부분 매칭 오분류로 [금융·투자] 주제로 선정 →
    경제 브리핑에 임업 재배면적 글 발행 사고 방지. True=적합 / False=부적합 /
    None=판정 불가(LLM 미가용 — 호출자가 결정론 폴백 사용).
    """
    try:
        from shared.llm import invoke_text
        raw = invoke_text(
            "analyzer",
            f"키워드: {keyword} (분류된 섹터: {sector})\n\n"
            "이 키워드가 한국 *경제·금융·산업·투자* 독자를 위한 경제 블로그 주제로 "
            "적합한지 판정해라. 동식물·자연물·인물·연예·스포츠·일상 등 경제 콘텐츠와 "
            "무관한 대상이면 부적합이다 (예: '은행나무'는 나무이므로 부적합, "
            "'기준금리'·'반도체 수출'은 적합).\n"
            "다른 말 없이 정확히 한 단어로만 답: 적합 또는 부적합",
            max_tokens=10,
        )
        t = (raw or "").strip()
        if not t:
            return None
        if "부적합" in t:
            return False
        if "적합" in t:
            return True
        return None
    except Exception:
        return None


def _first_fit_topic(pool: list, label: str) -> dict | None:
    """후보 풀에서 경제 주제 적합 첫 후보 선택 — LLM 판정 우선, 미가용 시 분류 신뢰도(결정론).

    ★ 부적합 후보 강행 금지 (사용자 박제 2026-07-03): "데이터는 실질적·유익·진실해야".
    적합 후보가 없으면 None — 호출자가 emergency trends(경제 핫이슈 LLM 생성)로 폴백.
    """
    for cand in pool[:5]:   # LLM 비용 상한 — 상위 5 후보만
        kw = (cand.get('keyword', '') or '').strip()
        if not kw:
            continue
        fit = _topic_econ_fit(kw, cand.get('sector', ''))
        if fit is True:
            return cand
        if fit is False:
            print(f"  ⛔ [{label}] '{kw}' 경제 주제 부적합 판정 → 다음 후보")
            continue
        # LLM 미가용 — 결정론 폴백: 섹터 분류 신뢰도로 복합어 오분류('은행나무' 류) 차단
        try:
            from JARVIS03_RADAR.analyzer import classify_keyword_conf
            _sec2, _conf = classify_keyword_conf(kw)
            if _conf and _sec2 in _ECON_SECTORS:
                return cand
            print(f"  ⛔ [{label}] '{kw}' 저신뢰 섹터 분류 (LLM 미가용) → 다음 후보")
        except Exception:
            return cand   # 판정 수단 전무 — 기존 동작 유지 (차단보다 발행 우선)
    return None


def _emergency_topic(ref_keyword: str, label: str) -> dict | None:
    """적합 후보 전무 시 최후 폴백 — LLM 즉석 경제 핫이슈에서 주제 선택."""
    print(f"  ⚠️ [{label}] 적합 경제 주제 없음 → emergency 경제 이슈 폴백")
    em = _build_emergency_trends() or {}
    for r in (em.get('recommendations') or []) + (em.get('scored_keywords') or []):
        if r.get('keyword') and not _is_same_topic(r, ref_keyword):
            r['keyword'] = _normalize_keyword(r.get('keyword', ''))
            r['theme']   = r['keyword']
            print(f"  📌 [{label}] emergency 주제: [{r.get('sector')}] {r.get('keyword')}")
            return r
    return None


def select_tistory_topic(trends: dict, nv_keyword: str = '') -> dict | None:
    """티스토리용 주제 선정 — 네이버 키워드 제외 + 경제 섹터, 최근 7일 미사용 키워드 우선.

    ★ 네이버 우선 직렬 (사용자 박제 2026-07-03): 네이버가 먼저 작성되므로
    중복 방지 방향이 naver→tistory 로 반전. nv_keyword 와 동일 주제 제외.
    """
    recs   = trends.get('recommendations', [])
    scored = trends.get('scored_keywords', [])
    used   = _get_used_keywords()

    def _not_used(r: dict | str) -> bool:
        kw = (r.get('keyword', '') if isinstance(r, dict) else r).strip().lower()
        return kw not in used

    econ_recs = [
        r for r in recs
        if r.get('sector', '') in _ECON_SECTORS
        and not _is_same_topic(r, nv_keyword)
        and r.get('sector', '') not in _EXCLUDE_SECTORS
    ]
    # 미사용 우선 → 없으면 사용 이력 무시 (발행 건너뜀 방지)
    fresh = [r for r in econ_recs if _not_used(r)]
    pool  = fresh or econ_recs
    if pool:
        pool = sorted(pool, key=lambda x: x.get('opportunity_score', 0), reverse=True)
        best = _first_fit_topic(pool, "티스토리")   # ★ 주제 적합성 게이트 (ERRORS [290])
        if best:
            best['keyword'] = _normalize_keyword(best.get('keyword', ''))
            best['theme']   = best['keyword']
            reused = "♻️ 재사용(미사용 없음)" if not fresh else ""
            print(f"  📌 티스토리 주제: [{best.get('sector')}] {best.get('keyword')} "
                  f"(기회점수 {best.get('opportunity_score', 0):.1f}) {reused}")
            return best

    econ_scored = [k for k in scored
                   if k.get('sector', '') in _ECON_SECTORS and not _is_same_topic(k, nv_keyword)]
    fresh2 = [k for k in econ_scored if _not_used(k)]
    pool2  = fresh2 or econ_scored
    if pool2:
        pool2 = sorted(pool2, key=lambda x: x.get('opportunity_score', x.get('score', 0)), reverse=True)
        best = _first_fit_topic(pool2, "티스토리 폴백")   # ★ 주제 적합성 게이트 (ERRORS [290])
        if best:
            best['keyword'] = _normalize_keyword(best.get('keyword', ''))
            best['theme']   = best['keyword']
            print(f"  📌 티스토리 주제 (폴백): [{best.get('sector')}] {best.get('keyword')}")
            return best

    # ★ 적합 후보 전무 → 부적합 키워드 강행 대신 emergency 경제 이슈 (사용자 박제 2026-07-03)
    best = _emergency_topic(nv_keyword, "티스토리")
    if best:
        return best

    print("  ⚠️ 티스토리 경제 관련 트렌드 주제 없음")
    return None


def select_naver_topic(trends: dict, ts_keyword: str = '') -> dict | None:
    """네이버용 주제 선정 — 티스토리 키워드 제외 + 최근 7일 미사용 + 경제 섹터 최고점."""
    recs   = trends.get('recommendations', [])
    scored = trends.get('scored_keywords', [])
    used   = _get_used_keywords()

    def _not_used(r: dict | str) -> bool:
        kw = (r.get('keyword', '') if isinstance(r, dict) else r).strip().lower()
        return kw not in used

    econ_recs = [
        r for r in recs
        if r.get('sector', '') in _ECON_SECTORS
        and not _is_same_topic(r, ts_keyword)
        and r.get('sector', '') not in _EXCLUDE_SECTORS
    ]
    fresh = [r for r in econ_recs if _not_used(r)]
    pool  = fresh or econ_recs
    if pool:
        pool = sorted(pool, key=lambda x: x.get('opportunity_score', 0), reverse=True)
        best = _first_fit_topic(pool, "네이버")   # ★ 주제 적합성 게이트 (ERRORS [290])
        if best:
            best['keyword'] = _normalize_keyword(best.get('keyword', ''))
            best['theme']   = best['keyword']
            reused = "♻️ 재사용(미사용 없음)" if not fresh else ""
            print(f"  📌 네이버 주제: [{best.get('sector')}] {best.get('keyword')} "
                  f"(기회점수 {best.get('opportunity_score', 0):.1f}) {reused}")
            return best

    econ_scored = [k for k in scored if k.get('sector', '') in _ECON_SECTORS and not _is_same_topic(k, ts_keyword)]
    fresh2 = [k for k in econ_scored if _not_used(k)]
    pool2  = fresh2 or econ_scored
    if pool2:
        pool2 = sorted(pool2, key=lambda x: x.get('opportunity_score', x.get('score', 0)), reverse=True)
        best = _first_fit_topic(pool2, "네이버 폴백")   # ★ 주제 적합성 게이트 (ERRORS [290])
        if best:
            best['keyword'] = _normalize_keyword(best.get('keyword', ''))
            best['theme']   = best['keyword']
            print(f"  📌 네이버 주제 (폴백): [{best.get('sector')}] {best.get('keyword')}")
            return best

    # ★ 적합 후보 전무 → 부적합 키워드 강행 대신 emergency 경제 이슈 (사용자 박제 2026-07-03)
    best = _emergency_topic(ts_keyword, "네이버")
    if best:
        return best

    print("  ⚠️ 네이버 경제 관련 트렌드 주제 없음")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 텔레그램 헬퍼
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _tg(msg: str) -> None:
    try:
        import requests
        token = os.getenv('TELEGRAM_TOKEN', '')
        chat  = os.getenv('TELEGRAM_CHAT_ID', '')
        if token and chat:
            requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat, 'text': msg, 'parse_mode': 'HTML'},
                timeout=5)
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 썸네일 이미지 생성 (matplotlib)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 이미지 생성 — JARVIS06_IMAGE 위임 (trend_charts.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from JARVIS06_IMAGE.trend_charts import (
    make_trend_thumbnail      as _j06_make_trend_thumbnail,
    make_section_image        as _j06_make_section_image,
    make_market_chart         as _j06_make_market_chart,
    make_checklist_chart      as _j06_make_checklist_chart,
    make_scenario_chart       as _j06_make_scenario_chart,
    make_impact_chart         as _j06_make_impact_chart,
    make_highlight_card       as _j06_make_highlight_card,
    make_insight_card         as _j06_make_insight_card,
    make_line_trend_chart     as _j06_make_line_trend_chart,
    make_stat_infographic     as _j06_make_stat_infographic,
    make_comparison_chart     as _j06_make_comparison_chart,
    make_ai_section_image     as _j06_make_ai_section_image,
    make_smart_section_image  as _j06_make_smart_section_image,   # NEW
)


def _img_dir(platform: str):
    """플랫폼별 이미지 저장 디렉터리."""
    if platform == 'naver':
        return NAVER_IMG_DIR
    return TISTORY_IMG_DIR


def make_trend_thumbnail(keyword: str, sector: str, platform: str = 'naver',
                         market: dict = None) -> str:
    return _j06_make_trend_thumbnail(keyword, sector, platform, market, out_dir=_img_dir(platform))


def _make_trend_thumbnail_mpl(keyword, sector, platform, market, out_path):
    raise NotImplementedError("_LEGACY: 이 함수는 JARVIS06_IMAGE/trend_charts.py 로 이관됨")


def make_section_image(section_title: str, section_num: int, keyword: str,
                       sector: str, platform: str = 'naver',
                       key_points: list = None) -> str:
    return _j06_make_section_image(section_title, section_num, keyword, sector,
                                   platform, key_points, out_dir=_img_dir(platform))



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. 원고 생성 — 티스토리 생활 밀착 Q&A형
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 네이버·티스토리 표준 구조 — ★ 사용자 박제 2026-05-15
# 경제 브리핑 = 모든 블로그 통일 구조 (Q&A형 폐기, 심층 해설형 4섹션)
# 플랫폼별 차이 = 문체만 (네이버=해요체, 티스토리=합니다체)
_TS_SECTIONS = """\
아래 6개 파트를 순서대로 작성해. 각 소제목 아래 반드시 1문장 이상 (빈 소제목 절대 금지).
(헌법 제0-B조 적용 — 한 <p> 태그 = 최대 2문장)

[도입부] — {INTRO_SENTS_PHRASE} ★ 헌법 제0조
(첫 문장 감성 오프닝 — 일상 관찰·공감 질문·계절 분위기. 숫자·지표 시작 절대 금지)
왜 지금 이 키워드가 급상승하는지, 독자가 왜 알아야 하는지 자연스럽게 연결.
→ <p>(2문장)</p>  [섹션이미지 ①]  <p>(2문장)</p>

[섹션 ①] <h2>'{KEYWORD}'란 무엇인가?</h2> — {SEC_SENTS_PHRASE}
처음 듣는 독자도 이해할 수 있도록 개념과 배경을 쉽게 설명. 전문 용어는 반드시 풀어서.
→ <p>(2문장)</p>  [단락 차트]  <p>(2문장)</p>  [섹션이미지 ②]  <p>(2문장)</p>

[섹션 ②] <h2>지금 무슨 일이 일어나고 있나?</h2> — {SEC_SENTS_PHRASE}
현재 상황·트렌드 배경·시장 데이터. 구체적 수치 포함.
→ <p>(2문장)</p>  [단락 차트]  <p>(2문장)</p>  [섹션이미지 ③]  <p>(2문장)</p>

[섹션 ③] <h2>국내 증시·실생활에 미치는 영향</h2> — {SEC_SENTS_PHRASE}
주식·환율·금리·부동산·소비 중 관련 항목을 연결하여 실생활 영향 설명.
→ <p>(2문장)</p>  [단락 차트]  <p>(2문장)</p>  [섹션이미지 ④]  <p>(2문장)</p>

[섹션 ④] <h2>투자자 체크포인트와 향후 전망</h2> — {SEC_SENTS_PHRASE}
개인 투자자 시각 주목 포인트 2가지+ + 낙관/중립/비관 시나리오 or 단기·중기 전망.
→ <p>(2문장)</p>  [단락 차트]  <p>(2문장)</p>  [섹션이미지 ⑤]  <p>(2문장)</p>

[마무리] — {OUTRO_SENTS_PHRASE} + 면책 {OUTRO_SENTS_PHRASE} ★ 헌법 제5조
핵심 요약 {OUTRO_SENTS_PHRASE} + [섹션이미지 ⑥] + 면책 {OUTRO_SENTS_PHRASE} (매번 다른 표현 — 정보 제공·투자 권유 아님·판단 책임은 독자).
→ <p>(2문장)</p>  [섹션이미지 ⑥]  <p>(면책 2문장)</p>

[총합] {TOTAL_SENTS_PHRASE} + 섹션이미지 6 + 단락 차트 4+ + 썸네일 1
"""

# 옛 Q&A 분량 상수 — 호환 alias (deprecated, 신규 사용 금지)
_TS_Q1 = (4, 5)
_TS_Q2 = (5, 6)
_TS_Q3 = (5, 6)
_TS_Q4 = (4, 5)


def _generate_tistory_text(topic: dict, supreme_block: str) -> dict:
    """① 원고 생성 — 텍스트만 (이미지 생성 없음).

    규정은 run_tistory()에서 데이터 수집 직후 로드해 주입.
    Returns: {"title": str, "content": str, "keyword": str, "sector": str}
    """
    from shared.llm import invoke_text

    keyword = topic.get('keyword', '')
    sector  = topic.get('sector', '')
    reason  = topic.get('reason', '')

    print(f"  ✍️ [티스토리] Q&A형 원고 생성 중: {keyword} ({sector})")

    ts_target = _L.TARGET_KOREAN
    _ts_sec_sents = (_L.TARGET_SENTENCES - _L.INTRO_SENTS_MIN - _L.OUTRO_SENTS) // 4
    sections  = _TS_SECTIONS \
        .replace("{INTRO_SENTS_PHRASE}", _L.build_length_phrase(_L.INTRO_SENTS_MIN, _L.INTRO_SENTS_MAX)) \
        .replace("{SEC_SENTS_PHRASE}",   _L.build_length_phrase(_ts_sec_sents)) \
        .replace("{OUTRO_SENTS_PHRASE}", _L.build_length_phrase(_L.OUTRO_SENTS)) \
        .replace("{TOTAL_SENTS_PHRASE}", _L.build_length_phrase(_L.TARGET_SENTENCES)) \
        .replace("{KEYWORD}", keyword) \
        .replace("{Q1}", _L.build_length_phrase(*_TS_Q1)) \
        .replace("{Q2}", _L.build_length_phrase(*_TS_Q2)) \
        .replace("{Q3}", _L.build_length_phrase(*_TS_Q3)) \
        .replace("{Q4}", _L.build_length_phrase(*_TS_Q4))
    learn_block = _load_learn_insights("economic")

    _seed = int(hashlib.md5(f"{TODAY_STR}{keyword}tistory".encode()).hexdigest(), 16) % 10000
    hook_style = random.Random(_seed).choice([
        "요즘 주변에서 이 얘기가 부쩍 많아졌더라고요.",
        "지난주 뉴스를 보다가 문득 궁금해진 게 있었는데요.",
        "커피 한 잔 마시면서 생각해봤는데, 참 신기하더라고요.",
        "최근에 지인이 물어봐서 같이 찾아봤습니다.",
        "솔직히 저도 처음엔 잘 몰랐는데, 공부하다 보니 꽤 중요한 내용이더라고요.",
        "이걸 알고 나면 뉴스 볼 때 느낌이 달라질 거예요.",
        "혹시 요즘 이런 단어 들어보셨나요? 저도 궁금해서 한번 정리해봤어요.",
    ])

    prompt = f"""{supreme_block}
오늘({TODAY_STR} {TODAY_DOW}요일) 경제 브리핑 블로그 — 티스토리 생활 밀착 Q&A형 글을 작성해줘.

[오늘의 트렌드 키워드]
- 키워드: {keyword}
- 섹터: {sector}
- 급상승 이유: {reason}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[글 스타일 — 위 헌법 블록(supreme_block) 전체 적용. 오프닝 힌트: "{hook_style}"]
- 독자: 경제 초보자 포함 20~40대 모바일 독자 / 문체: 격식체(~습니다)
- 목표 분량: {_L.TARGET_SENTENCES}문장(약 {ts_target}자)
- HTML h2 태그로 Q 소제목 구조화. 각 Q 아래 <p> 단락 2개 이상 분리 (이미지 삽입 슬롯)
- 숫자·비교·실생활 예시 포함으로 공감도 강화
{learn_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[섹션 구조]
{sections}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[출력 형식]
TITLE:
(제목 — 궁금증 유발하는 클릭 유도형, {_L.TITLE_PROMPT_MAX}자 이내, 해요체)

CONTENT:
(본문 HTML — h2 태그 포함, p 태그로 단락 구분)
"""

    raw = invoke_text("writer", prompt, temperature=0.68, max_tokens=5000)
    if not raw:
        print("  ❌ [티스토리] LLM 응답 없음")
        return {}

    title   = _parse_block(raw, "TITLE:",   "CONTENT:")
    content = _parse_block(raw, "CONTENT:", None)
    if not title or not content:
        print("  ⚠️ [티스토리] 파싱 실패 — raw 응답 활용")
        title   = f"{keyword}, 나한테 어떤 영향이 있을까요? — {TODAY_STR}"
        content = raw

    import re as _re_em
    _emoji_re = _re_em.compile(
        r'[\U00010000-\U0010FFFF\U00002700-\U000027BF\U0001F300-\U0001F9FF\U00002600-\U000026FF]+',
        flags=_re_em.UNICODE)
    title   = _emoji_re.sub("", title).strip()
    content = _emoji_re.sub("", content)

    _L.warn_length(keyword, "tistory-trend", content)
    content = _enforce_paragraph_rule(content)   # 제0-B조 자동 적용
    print(f"  ✅ [티스토리] 텍스트 원고 완료: '{title}' ({_L.count(content)}자)")
    return {"title": title, "content": content, "keyword": keyword, "sector": sector}


def _assemble_tistory_blocks(content: str, thumb_path: str) -> list:
    """HTML + placeholder → blocks 리스트 변환."""
    import re as _re_fig
    _all = {}
    _all.update({f'__SECTION_IMG_{n}__': p for n, p in _section_img_paths.items()})
    _all.update({f'__PARA_IMG_{n}__':    p for n, p in _para_img_paths.items()})

    blocks: list = []
    if thumb_path and Path(thumb_path).exists():
        blocks.append(('image', thumb_path))

    for part in _re_fig.split(r'(<figure[^>]*>.*?</figure>)', content, flags=_re_fig.DOTALL):
        part = part.strip()
        if not part:
            continue
        if part.startswith('<figure'):
            m_ph = _re_fig.search(r'src=["\'](__(?:SECTION|PARA)_IMG_\d+__)["\']', part)
            if m_ph:
                actual = _all.get(m_ph.group(1), '')
                if actual and Path(actual).exists():
                    blocks.append(('image', actual))
            else:
                m_src = _re_fig.search(r'src=["\']([^"\']+)["\']', part)
                if m_src:
                    blocks.append(('image', m_src.group(1)))
        else:
            blocks.append(('text', part))
    return blocks


def generate_tistory_article(topic: dict) -> dict:
    """[하위 호환 래퍼] 단독 호출 시 규정 자체 로드 후 단계별 실행.

    run_tistory() 는 규정을 데이터 수집 직후 명시적으로 로드하므로
    이 래퍼는 외부 단독 실행 용도에만 사용.
    """
    _cleanup_tistory_images()  # 이전 이미지 전체 삭제 후 생성 시작
    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
    supreme_block = _law_blk()
    text_result   = _generate_tistory_text(topic, supreme_block)
    if not text_result:
        return {}
    keyword = text_result['keyword']
    sector  = text_result['sector']
    content = text_result['content']
    _section_img_paths.clear(); _para_img_paths.clear()
    content = _inject_section_images(content, keyword, sector, platform='tistory')
    content = _inject_paragraph_images(content, keyword, sector, platform='tistory')
    thumb_path = make_trend_thumbnail(keyword, sector, platform='tistory')
    blocks = _assemble_tistory_blocks(content, thumb_path)
    print(f"  ✅ [티스토리] 완료: '{text_result['title']}' / 블록 {len(blocks)}개")
    return {"title": text_result['title'], "html": content, "blocks": blocks, "thumb_path": thumb_path}



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. 이미지 정리
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _cleanup_tistory_images() -> None:
    """티스토리 이미지 폴더 전체 초기화 — 패턴 무관 모든 파일 삭제."""
    for f in TISTORY_IMG_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink(missing_ok=True)
            except (PermissionError, OSError):
                pass

def _cleanup_naver_images() -> None:
    """Naver 이미지 폴더 전체 초기화 — 패턴 무관 모든 파일 삭제."""
    from JARVIS02_WRITER.economic_poster import ECONOMIC_IMG_DIR
    naver_dir = ECONOMIC_IMG_DIR if hasattr(ECONOMIC_IMG_DIR, 'glob') else Path(ECONOMIC_IMG_DIR)
    naver_dir.mkdir(parents=True, exist_ok=True)
    for f in naver_dir.iterdir():
        if f.is_file():
            try:
                f.unlink(missing_ok=True)
            except (PermissionError, OSError):
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  9. 내부 유틸
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _parse_block(text: str, start_marker: str, end_marker: str | None) -> str:
    """LLM 응답에서 마커 사이 텍스트 추출."""
    idx = text.find(start_marker)
    if idx == -1:
        return ""
    start = idx + len(start_marker)
    if end_marker:
        end = text.find(end_marker, start)
        return text[start:end].strip() if end != -1 else text[start:].strip()
    return text[start:].strip()


def _enforce_paragraph_rule(html: str) -> str:
    """최대 2문장 단락 규칙 적용 (개선사항 #3).

    <p> 태그 내 3문장 이상이면 2문장 단위로 분리.
    문장 구분: 마침표+공백 또는 다/니다/습니다 패턴.
    """
    import re

    def split_sentences(text: str) -> list[str]:
        # 한국어 문장 끝: 다./요./니다./습니다./이다. 등 + 따옴표 포함
        pattern = r'(?<=[다요니]\.)\s+|(?<=다\.)\s+|(?<=요\.)\s+'
        parts = re.split(pattern, text.strip())
        # 공백만 남거나 빈 것 제거
        return [p.strip() for p in parts if p.strip()]

    def process_p(match):
        inner = match.group(1)
        # 이미 하위 태그(li, strong 등) 포함된 복잡한 p는 건드리지 않음
        if re.search(r'<[a-z]', inner):
            return match.group(0)
        sentences = split_sentences(inner)
        if len(sentences) <= 2:
            return match.group(0)
        # 2문장씩 묶어서 별도 <p>로 분리
        chunks = []
        for i in range(0, len(sentences), 2):
            chunk = ' '.join(sentences[i:i+2])
            chunks.append(f'<p>{chunk}</p>')
        return '\n'.join(chunks)

    return re.sub(r'<p>(.*?)</p>', process_p, html, flags=re.DOTALL)


def _inject_section_images(html: str, keyword: str, sector: str,
                            platform: str = 'naver') -> str:
    """h2 소제목 유지 (★ 사용자 박제 2026-05-15 — 옛 h2→이미지 교체 동작 폐기).

    사용자 박제 구조: <h2> 유지 + <p>2문장</p> + [차트] + <p>2문장</p> + [섹션이미지] + <p>2문장</p>
    섹션 이미지는 `_inject_paragraph_images()` 에서 <p> 사이에 동적 삽입.
    소제목 앞뒤 여백은 `law_enforcer.enforce_spacing()` 가 발행 직전 자동 처리.
    """
    import re
    _section_img_paths.clear()
    h2_count = len(re.findall(r'<h2>', html, re.IGNORECASE))
    print(f"  🖼️ h2 소제목 {h2_count}개 보존 — 섹션 이미지는 단락 사이 동적 삽입")
    return html


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  섹션별 콘텐츠 차트 생성 — 섹션 내용 유형에 맞는 실제 시각화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _detect_section_type(html: str) -> str:
    """섹션 HTML 내용 분석 → 시각화 유형 결정."""
    import re
    text = re.sub(r'<[^>]+>', '', html)
    if re.search(r'S&P|나스닥|다우|달러.?원|환율|WTI|금\s*현물|코스피|코스닥', text):
        return 'market'
    circ = len(re.findall(r'[①②③④⑤⑥⑦⑧⑨]', text))
    num  = len(re.findall(r'\n\s*\d+\.\s', text))
    if circ >= 2 or num >= 2:
        return 'checklist'
    if re.search(r'낙관|비관|중립|시나리오|[강약]세\s*시나리오', text):
        return 'scenario'
    nums = re.findall(r'\d+\.?\d*\s*%', text)
    if len(nums) >= 2 and re.search(r'영향|상승|하락|증가|감소', text):
        return 'impact'
    return 'highlight'


def _extract_list_items(html: str, max_items: int = 5) -> list:
    """HTML에서 리스트 항목 추출 (①②③ 또는 번호 목록)."""
    import re
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'<[^>]+>', '', text)
    items = []
    # ①②③ 패턴
    for m in re.finditer(r'[①②③④⑤⑥⑦⑧⑨]\s*([^\n①②③④⑤⑥⑦⑧⑨]{8,80})', text):
        items.append(m.group(1).strip()[:45])
    if not items:
        # 번호 목록 패턴
        for m in re.finditer(r'\d+\.\s+([^\n]{8,80})', text):
            items.append(m.group(1).strip()[:45])
    if not items:
        # 문장 분리 fallback
        sentences = [s.strip() for s in re.split(r'[.。]', text) if len(s.strip()) >= 10]
        items = sentences[:max_items]
    return items[:max_items]


def _extract_scenarios(html: str) -> list:
    """HTML에서 낙관/중립/비관 시나리오 추출."""
    import re
    text = re.sub(r'<[^>]+>', '', html)
    result = []
    patterns = [
        ('낙관', r'낙관[^。.]*[。.]?([^。.]{10,80})'),
        ('중립', r'중립[^。.]*[。.]?([^。.]{10,80})'),
        ('비관', r'비관[^。.]*[。.]?([^。.]{10,80})'),
    ]
    for label, pat in patterns:
        m = re.search(pat, text)
        if m:
            result.append((label, m.group(1).strip()[:50]))
        else:
            # 키워드만 찾아서 주변 문장 추출
            idx = text.find(label)
            if idx != -1:
                snippet = text[idx:idx+60].split('。')[0].split('.')[0]
                result.append((label, snippet.strip()[:50]))
            else:
                result.append((label, '추가 분석 필요'))
    return result

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  콘텐츠 차트 — JARVIS06_IMAGE 위임 (trend_charts.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def make_market_chart(market: dict, keyword: str, sector: str,
                       card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_market_chart(market, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_checklist_chart(items: list, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_checklist_chart(items, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_scenario_chart(scenarios: list, keyword: str, sector: str,
                         card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_scenario_chart(scenarios, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_impact_chart(factors: list, keyword: str, sector: str,
                       card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_impact_chart(factors, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_highlight_card(text: str, keyword: str, sector: str,
                         card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_highlight_card(text, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_insight_card(text: str, label: str, card_idx: int,
                      keyword: str, sector: str, platform: str = 'naver') -> str:
    return _j06_make_insight_card(text, label, card_idx, keyword, sector, platform, out_dir=_img_dir(platform))


def make_line_trend_chart(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_line_trend_chart(text, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_stat_infographic(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver',
                          prebuilt: list = None) -> str:
    return _j06_make_stat_infographic(text, keyword, sector, card_idx, platform, prebuilt, out_dir=_img_dir(platform))


def make_comparison_chart(text: str, keyword: str, sector: str,
                          card_idx: int, platform: str = 'naver',
                          pros: list = None, cons: list = None) -> str:
    return _j06_make_comparison_chart(text, keyword, sector, card_idx, platform, pros, cons, out_dir=_img_dir(platform))


def make_ai_section_image(section_text: str, keyword: str, sector: str,
                           card_idx: int, platform: str = 'naver') -> str:
    return _j06_make_ai_section_image(section_text, keyword, sector, card_idx, platform, out_dir=_img_dir(platform))


def make_smart_section_image(section_text: str, section_title: str, keyword: str,
                              sector: str, card_idx: int, platform: str = 'naver',
                              out_dir=None) -> str:
    return _j06_make_smart_section_image(
        section_text=section_text, section_title=section_title,
        keyword=keyword, sector=sector, card_idx=card_idx,
        platform=platform, out_dir=out_dir or _img_dir(platform),
    )



def _llm_extract_chart_data(text: str, keyword: str, chart_type: str) -> dict | None:
    """Claude LLM으로 차트 데이터를 동적 추출. 실패 시 None 반환."""
    try:
        from shared.llm import invoke_text as _inv
        import json as _j, re as _re2

        _PROMPTS = {
            'impact': (
                f"본문(앞 500자):\n{text[:500]}\n\n"
                f"키워드: {keyword}\n"
                "위 본문에서 주요 영향 요인 4~6개를 추출하라.\n"
                "각 요인은 2~5글자 경제 명사 라벨 + 영향 강도(-3.0~3.0) + 방향(긍정/부정).\n"
                "JSON만 출력: [{\"label\":\"금리\",\"value\":2.5,\"dir\":\"긍정\"}, ...]"
            ),
            'checklist': (
                f"본문(앞 400자):\n{text[:400]}\n\n"
                f"키워드: {keyword}\n"
                "위 본문에서 투자자가 확인해야 할 핵심 체크포인트 4~5개를 한 문장씩 추출하라.\n"
                "JSON만 출력: [\"첫 번째 포인트\", \"두 번째 포인트\", ...]"
            ),
            'scenario': (
                f"본문(앞 400자):\n{text[:400]}\n\n"
                f"키워드: {keyword}\n"
                f"위 본문 기반으로 상승·하락·중립 시나리오 3개를 작성하라. 각 {_L.SCENARIO_LABEL_MAX}자 이내.\n"
                "JSON만 출력: [{\"title\":\"▲ 상승 전망\",\"desc\":\"...\"},{\"title\":\"▼ 하락 위험\",\"desc\":\"...\"},{\"title\":\"= 중립 관점\",\"desc\":\"...\"}]"
            ),
            'line_trend': (
                f"본문(앞 500자):\n{text[:500]}\n\n"
                f"키워드: {keyword}, 오늘: {__import__('datetime').date.today().strftime('%Y년 %m월')}\n"
                "위 본문 내용 기반으로 시계열 트렌드 데이터 4~6개를 만들어라.\n"
                "라벨 규칙: 반드시 사람이 이해하는 실제 시점 표현 사용 (예: '22년', '23년', '24년', '25년', '1분기', '2분기', '1월', '6월' 등).\n"
                "T1·T2·S1·S2 같은 무의미한 임시 라벨 절대 금지. 본문에 시점이 없으면 최근 연도를 추론해 사용.\n"
                "값은 본문 내 수치 또는 맥락에서 추론한 상대적 강도(0~100).\n"
                "JSON만 출력: [{\"label\":\"22년\",\"value\":45}, {\"label\":\"23년\",\"value\":60}, ...]"
            ),
            'stat_card': (
                f"본문(앞 500자):\n{text[:500]}\n\n"
                f"키워드: {keyword}\n"
                "위 본문에서 핵심 수치 KPI 3~4개를 추출하라. 라벨은 2~6글자 경제 명사.\n"
                "JSON만 출력: [{\"label\":\"영업이익\",\"val\":\"2.3\",\"unit\":\"조\"}, ...]"
            ),
            'comparison': (
                f"본문(앞 400자):\n{text[:400]}\n\n"
                f"키워드: {keyword}\n"
                f"위 본문에서 긍정 요인 2~3개, 부정 요인 2~3개를 각 {_L.SCENARIO_LABEL_MAX}자 이내로 추출하라.\n"
                "JSON만 출력: {\"pros\":[\"...\"],\"cons\":[\"...\"]}"
            ),
            'highlight': (
                f"본문(앞 400자):\n{text[:400]}\n\n"
                f"키워드: {keyword}\n"
                f"위 본문에서 가장 핵심적인 인사이트 문장 1개({_L.INSIGHT_KEY_MAX}자 이내)를 추출하라.\n"
                "JSON만 출력: {\"text\":\"...\"}"
            ),
        }

        prompt = _PROMPTS.get(chart_type)
        if not prompt:
            return None

        raw = _inv("writer_fast", prompt, max_tokens=300, temperature=0.2)
        # JSON 블록 추출
        m = _re2.search(r'(\[[\s\S]*?\]|\{[\s\S]*?\})', raw)
        if not m:
            return None
        data = _j.loads(m.group(1))

        if chart_type == 'impact':
            if not isinstance(data, list) or not data:
                return None
            factors = [(str(d.get('label', keyword))[:8], float(d.get('value', 1.0)),
                        d.get('dir', '긍정')) for d in data if d.get('label')]
            return {'data': factors[:6], 'label': f'{keyword} 영향 요인 분석',
                    'title': f'{keyword} 주요 영향 분석'}

        if chart_type == 'checklist':
            if not isinstance(data, list) or not data:
                return None
            return {'data': [str(i)[:24] for i in data[:5]],
                    'label': f'{keyword} 체크포인트',
                    'title': f'{keyword} 투자자 체크리스트'}

        if chart_type == 'scenario':
            if not isinstance(data, list) or len(data) < 2:
                return None
            scenarios = [{'title': str(d.get('title',''))[:12],
                          'desc': str(d.get('desc',''))[:20]} for d in data[:3]]
            return {'data': scenarios, 'label': '시나리오 분석',
                    'title': f'{keyword} 향후 시나리오'}

        if chart_type == 'line_trend':
            if not isinstance(data, list) or not data:
                return None
            points = [(str(d.get('label','T'))[:4], float(d.get('value', 0))) for d in data if d.get('label')]
            return {'data': points[:8], 'label': f'{keyword} 트렌드 추이',
                    'title': f'{keyword} 수치 변화 추이'}

        if chart_type == 'stat_card':
            if not isinstance(data, list) or not data:
                return None
            stats = [{'label': str(d.get('label', keyword))[:6],
                      'val': str(d.get('val', '')), 'unit': str(d.get('unit', ''))}
                     for d in data if d.get('label') and d.get('val')]
            return {'data': stats[:4], 'label': f'{keyword} 핵심 수치',
                    'title': f'{keyword} 주요 지표'}

        if chart_type == 'comparison':
            if not isinstance(data, dict):
                return None
            return {'pros': [str(p)[:22] for p in data.get('pros', [])[:3]],
                    'cons': [str(c)[:22] for c in data.get('cons', [])[:3]],
                    'label': '비교 분석', 'title': f'{keyword} 긍·부정 분석'}

        if chart_type == 'highlight':
            if not isinstance(data, dict):
                return None
            return {'data': str(data.get('text', ''))[:40], 'label': '핵심 인사이트',
                    'title': f'{keyword} 핵심 포인트'}

    except Exception:
        pass
    return None


def _extract_for_chart(text: str, keyword: str, chart_type: str) -> dict:
    """차트 데이터 추출 — LLM 우선, regex 폴백.

    원칙: LLM이 본문을 이해해 의미 있는 라벨/데이터를 생성. regex는 LLM 실패 시만.
    """
    # LLM 우선 시도
    llm_result = _llm_extract_chart_data(text, keyword, chart_type)
    if llm_result:
        return llm_result

    import re as _re

    # ── 공통 전처리 ──────────────────────────────
    # 마침표·줄바꿈으로 먼저 분리, 부족하면 40자 단위 분할
    raw = [s.strip() for s in _re.split(r'[。.!?\n]+', text) if len(s.strip()) > 8]
    if len(raw) < 2 and len(text) > 20:
        raw = [text[i:i+40].strip() for i in range(0, len(text), 40)
               if text[i:i+40].strip()]
    sents = raw

    # 핵심 명사 불용어 (접속사·부사·서술어·조사결합어)
    _STOP = {
        '다만','또한','하지만','그러나','그리고','따라서','이에','이는','이로','이와',
        '반면','한편','특히','현재','이후','향후','최근','이미','아직','더욱','여전히',
        '지속','계속','점점','매우','다소','앞서','이전','오늘','어제','내일','올해',
        '지난해','이번','지금','당시','이상','이하','그래서','물론','비록','설령',
        '있어서','있으며','있습니다','합니다','됩니다','있다','한다','된다','이다',
        '때문에','이러한','다음과','관련해','관련된','중에서','대해서','에서는',
        '이라고','이라는','라고는','라는것','라면서','라며','하며','하면서','하는데',
        '상승','증가','호조','성장','개선','강세','수혜','기회','확대','호황','급등',
        '회복','하락','감소','부진','위축','우려','약세','악화','위기','폭락','감속',
        '침체','리스크','올랐','내렸','늘었','줄었',
    }
    nouns = list(dict.fromkeys(n for n in _re.findall(r'[가-힣]{2,6}', text)
                               if n not in _STOP))

    num_matches = _re.findall(
        r'([가-힣]{1,8}[은는이가도의]?\s*)?(\d[\d,]*\.?\d*)\s*(%|억|조|만|bp|배|위|명|개|년|원)?',
        text)

    POS = ['상승','증가','호조','성장','개선','강세','수혜','기회','확대','호황','급등','회복','올랐','늘었']
    NEG = ['하락','감소','부진','위축','우려','약세','악화','위기','폭락','감속','침체','리스크','내렸','줄었']

    def _trim(s: str, n: int) -> str:
        return s[:n].rstrip()

    def _wrap(s: str, width: int = 12) -> str:
        """한글 width자 단위 줄 바꿈, 최대 3줄"""
        parts, i = [], 0
        while i < len(s):
            parts.append(s[i:i+width])
            i += width
        return '\n'.join(parts[:3])

    # ─────────────────────────────────────────────────
    if chart_type == 'impact':
        factors = []
        _SENT_STOP = _STOP | {'유가도','금리도','달러도','주가도','환율도'}
        for sent in sents[:10]:
            pos_c = sum(1 for k in POS if k in sent)
            neg_c = sum(1 for k in NEG if k in sent)
            if pos_c == 0 and neg_c == 0:
                continue
            # 불용어+감성어 제외, 3자 이상 우선, 가장 긴 단어 선택
            cands = [w for w in _re.findall(r'[가-힣]{2,6}', sent)
                     if w not in _SENT_STOP and w not in POS + NEG]
            # 3자 이상 우선 선택, 없으면 2자 사용
            long_cands = [w for w in cands if len(w) >= 3]
            best = (long_cands[0] if long_cands else (cands[0] if cands else None))
            lbl = _trim(best, 8) if best else _trim(keyword, 8)
            val = round((pos_c - neg_c) * 2.5, 1) or (1.0 if pos_c else -1.0)
            factors.append((lbl, val))
        if len(factors) < 3:
            core = [n for n in nouns if n not in POS + NEG and n not in _SENT_STOP and len(n) >= 2]
            for i, w in enumerate(core[:max(0, 5-len(factors))]):
                factors.append((_trim(w, 8), round(2.5 - i * 0.8, 1)))
        # LLM으로 라벨 보정 — 의미없는 접속사/부사 라벨이 남아있으면 교체
        _bad_lbls = _SENT_STOP | {'다만','오늘','내일','이번','현재','또한','특히','최근','향후'}
        if factors and any(lbl in _bad_lbls or len(lbl) <= 1 for lbl, _ in factors):
            try:
                from shared.llm import invoke_text as _inv
                import json as _j
                _lbls_req = (
                    f"본문(앞 300자):\n{text[:300]}\n\n"
                    f"키워드: {keyword}\n"
                    f"아래 {len(factors[:6])}개 항목의 영향 요인 KPI 라벨을 2~5글자 경제 명사로 교체하라.\n"
                    f"현재 라벨(참고용): {[l for l, _ in factors[:6]]}\n"
                    f"JSON 배열만 출력. 예: [\"금리\",\"환율\",\"수출\",\"유가\",\"주가\"]"
                )
                _raw = _inv("writer_fast", _lbls_req, max_tokens=80, temperature=0.2)
                import re as _re2
                _m = _re2.search(r'\[.*?\]', _raw, _re2.DOTALL)
                if _m:
                    _new_lbls = _j.loads(_m.group(0))
                    factors = [(_trim(str(_new_lbls[i]).strip(), 8) if i < len(_new_lbls) and _new_lbls[i] else lbl, val)
                               for i, (lbl, val) in enumerate(factors[:6])]
            except Exception:
                pass
        return {'data': factors[:6], 'label': f'{keyword} 영향 요인 분석',
                'title': f'{keyword} 주요 영향 분석'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'checklist':
        items = []
        for sent in sents[:5]:
            # 12자 단위 줄 바꿈, 2줄까지만
            wrapped = _wrap(_trim(sent, 24), 12)
            lines = wrapped.split('\n')
            items.append('\n'.join(lines[:2]))
        if not items:
            items = [_wrap(f'{keyword} 최신 동향', 12),
                     _wrap('시장 영향 분석', 12),
                     _wrap('주요 리스크 확인', 12)]
        return {'data': items[:5], 'label': f'{keyword} 체크포인트',
                'title': f'{keyword} 투자자 체크리스트'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'scenario':
        pos_s = [s for s in sents if any(k in s for k in POS)]
        neg_s = [s for s in sents if any(k in s for k in NEG)]
        neu_s = [s for s in sents if s not in pos_s and s not in neg_s]
        # 긍정 시나리오 텍스트
        p_txt = _trim(pos_s[0], 20) if pos_s else _trim(sents[0], 20) if sents else f'{keyword} 회복 기대'
        # 부정 시나리오 텍스트
        n_txt = _trim(neg_s[0], 20) if neg_s else _trim(sents[-1], 20) if sents else f'{keyword} 하락 위험'
        # 중립 시나리오 텍스트
        t_txt = _trim(neu_s[0], 20) if neu_s else (_trim(sents[len(sents)//2], 20) if len(sents) >= 2 else f'{keyword} 현황 유지')
        scenarios = [
            {'title': '▲ 상승 전망', 'desc': _wrap(p_txt, 10)},
            {'title': '▼ 하락 위험', 'desc': _wrap(n_txt, 10)},
            {'title': '= 중립 관점', 'desc': _wrap(t_txt, 10)},
        ]
        return {'data': scenarios, 'label': '시나리오 분석',
                'title': f'{keyword} 향후 시나리오'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'line_trend':
        _FUNC_WORDS = {'오늘','현재','지금','당장','이미','또한','특히','이후','이전',
                       '이상','이하','이번','지난','약','총','각각','최대','최소',
                       '최고','최저','기존','다음','이런','이같','따라','통해'}
        nums, xlabels = [], []
        for lgrp, val_str, unit in num_matches:
            try:
                val = float(val_str.replace(',', ''))
            except Exception:
                continue
            if val == 0:
                continue
            if val > 100_000: val = round(val / 10_000, 1)
            elif val > 10_000: val = round(val / 1_000, 1)
            elif val > 1_000:  val = round(val / 100, 1)
            lbl = lgrp.strip().rstrip('은는이가도의을를에서도 ') if lgrp.strip() else ''
            if lbl in _FUNC_WORDS: lbl = ''
            import datetime as _dt2
            _cur_yr = _dt2.date.today().year
            _fallback_lbl = _trim(lbl, 4) if lbl else f'{_cur_yr - 4 + len(nums)}년'
            nums.append(val)
            xlabels.append(_fallback_lbl)
        if len(nums) < 3:
            # 최근 연도 기반 시점 라벨 생성 (S1/S2 대신)
            import datetime as _dt
            _cur_year = _dt.date.today().year
            _time_lbls = [f'{_cur_year - 4 + i}년' for i in range(6)]
            for i, s in enumerate(sents[:6]):
                pc = sum(1 for k in POS if k in s)
                nc = sum(1 for k in NEG if k in s)
                nums.append(50 + (pc - nc) * 10)
                xlabels.append(_time_lbls[i] if i < len(_time_lbls) else f'{_cur_year - 1 + i}년')
        # LLM으로 x축 라벨 보정 (T{n} 라벨이 과반이면 LLM으로 의미있는 이름 추출)
        t_count = sum(1 for l in xlabels if l.startswith('T') or l.startswith('S'))
        if t_count >= len(xlabels) // 2 and text:
            try:
                from shared.llm import invoke_text as _inv
                import json as _j
                _p = (
                    f"본문(앞 400자):\n{text[:400]}\n\n"
                    f"키워드: {keyword}, 수치 {len(xlabels[:8])}개의 x축 라벨(2~4글자 명사)을 JSON 배열로만 출력.\n"
                    f"예: [\"수출\",\"수입\",\"무역수지\",\"환율\"], 배열 길이={len(xlabels[:8])}"
                )
                _raw = _inv("writer_fast", _p, max_tokens=100, temperature=0.3)
                _m = re.search(r'\[.*?\]', _raw, re.DOTALL)
                if _m:
                    _labels = _j.loads(_m.group(0))
                    for i, lbl in enumerate(_labels[:len(xlabels)]):
                        if lbl and str(lbl).strip():
                            xlabels[i] = _trim(str(lbl).strip(), 4)
            except Exception:
                pass
        return {'data': list(zip(xlabels[:8], nums[:8])),
                'label': f'{keyword} 트렌드 추이',
                'title': f'{keyword} 수치 변화 추이'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'stat_card':
        stats = []
        for lgrp, val_str, unit in num_matches:
            try:
                float(val_str.replace(',', ''))
            except Exception:
                continue
            u   = unit or ''
            # 임시 폴백 라벨 (파티클 제거)
            raw_lbl = lgrp.strip().rstrip('은는이가도의을를에서도 ') if lgrp.strip() else ''
            if not raw_lbl:
                idx = text.find(val_str)
                pre = _re.findall(r'[가-힣]{2,5}', text[max(0, idx-30):idx])
                raw_lbl = pre[-1] if pre else keyword
            stats.append({'val': val_str.replace(',', ''), 'unit': u,
                          'label': _trim(raw_lbl, 6)})
        # 중복 제거
        seen: set = set()
        stats = [s for s in stats if not (s['val'] in seen or seen.add(s['val']))]
        # 부족하면 핵심 명사 + 언급 빈도 수치로 보완
        if len(stats) < 4:
            core_n = [n for n in nouns if n not in POS + NEG and len(n) >= 2]
            for n in core_n[:max(0, 4 - len(stats))]:
                cnt = text.count(n)
                stats.append({'val': str(cnt), 'unit': '회', 'label': _trim(n, 6)})
        # LLM으로 의미 있는 KPI 제목 보정
        try:
            from shared.llm import invoke_text as _inv
            import json as _j
            _nums = [f"{s['val']}{s['unit']}" for s in stats[:4]]
            _p = (
                f"본문(앞 600자):\n{text[:600]}\n\n"
                f"키워드: {keyword}\n"
                f"수치 목록: {', '.join(_nums)}\n\n"
                f"각 수치에 대해 본문 맥락에 맞는 2~5글자 KPI 명사 제목을 JSON 배열로만 출력.\n"
                f"배열 길이={len(_nums)}, 예: [\"영업이익\",\"매출액\",\"성장률\",\"점유율\"]"
            )
            _raw = _inv("writer_fast", _p, max_tokens=120, temperature=0.3)
            _m = re.search(r'\[.*?\]', _raw, re.DOTALL)
            if _m:
                _labels = _j.loads(_m.group(0))
                for i, lbl in enumerate(_labels[:len(stats)]):
                    if lbl and str(lbl).strip():
                        stats[i]['label'] = _trim(str(lbl).strip(), 6)
        except Exception:
            pass  # 폴백 label 유지
        return {'data': stats[:4], 'label': f'{keyword} 핵심 수치',
                'title': f'{keyword} 주요 지표'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'comparison':
        pros = [_trim(s, 14) for s in sents if any(k in s for k in POS)][:3]
        cons = [_trim(s, 14) for s in sents if any(k in s for k in NEG)][:3]
        # 키워드 없으면 앞/뒤 문장으로 대체
        if not pros:
            pros = [_trim(s, 14) for s in sents[:3]]
        if not cons:
            cons = [_trim(s, 14) for s in reversed(sents[-3:])]
        # 그래도 없으면 keyword 기반 단어
        if not pros:
            pros = [f'{keyword} 성장 기대', '수요 확대 기대', '기회 요인 부각']
        if not cons:
            cons = [f'{keyword} 변동성', '공급 불균형', '규제 불확실성']
        return {'pros': pros, 'cons': cons, 'label': '비교 분석',
                'title': f'{keyword} 긍·부정 분석'}

    # ─────────────────────────────────────────────────
    elif chart_type == 'highlight':
        ranked = sorted(sents, key=lambda s: (
            sum(1 for k in POS + NEG if k in s) +
            len(_re.findall(r'\d', s)) * 0.4
        ), reverse=True)
        best = _trim(ranked[0], 40) if ranked else keyword + ' 핵심 동향'
        return {'data': best, 'label': '핵심 인사이트',
                'title': f'{keyword} 핵심 포인트'}

    return {'data': None, 'label': keyword, 'title': keyword}


def _analyze_section_content(text_plain: str, keyword: str) -> dict:
    """섹션 텍스트를 읽고 최적 차트 유형 + 실제 데이터를 반환.

    위치 고정 없음 — 매일 달라지는 글 내용 기반으로 동적 결정.
    Returns: {'type': str, 'data': any, 'label': str}
    """
    import re

    # ── 1. 레이블 + 수치% 패턴 (가장 명확한 차트 데이터)
    labeled = re.findall(
        r'([가-힣A-Za-z·\/\-]{2,15})\s*[은는이가]?\s*(?:약\s*)?(\d+\.?\d*)\s*%',
        text_plain)
    if len(labeled) >= 2:
        factors = []
        for n, v in labeled[:5]:
            val = float(v)
            if val > 100:
                val = round(val / 100, 1)
            factors.append((n[:14], val))
        return {'type': 'impact', 'data': factors, 'label': '주요 지표 분석'}

    # ── 2. 원문자(①②③) / 번호 목록
    circle = re.findall(r'[①②③④⑤⑥⑦⑧⑨⑩]\s*([^\n①②③④⑤⑥⑦⑧⑨⑩]{5,50})', text_plain)
    num_list = re.findall(r'(?:^|\n)\s*\d+[\.)\s]\s*(.{5,50})', text_plain)
    items = circle or num_list
    if len(items) >= 3:
        return {'type': 'checklist', 'data': [i.strip() for i in items[:6]],
                'label': '핵심 포인트'}

    # ── 3. 시나리오 구조
    if re.search(r'낙관|비관|중립|시나리오|[강약]세', text_plain):
        return {'type': 'scenario', 'data': None, 'label': '시나리오 분석'}

    # ── 4. 레이블 없는 수치% (레이블은 앞 문맥에서 추출 시도)
    context = re.findall(
        r'([가-힣]{2,8})\s+(?:\w+\s*){0,3}?(\d+\.?\d*)\s*%', text_plain)
    raw_pcts = re.findall(r'(\d+\.?\d*)\s*%', text_plain)
    if len(raw_pcts) >= 2:
        if context and len(context) >= 2:
            factors = [(n[:12], float(v) if float(v) <= 100 else round(float(v)/10, 1))
                       for n, v in context[:5]]
        else:
            factors = [(f'지표 {i+1}', float(p)) for i, p in enumerate(raw_pcts[:5])]
        return {'type': 'impact', 'data': factors, 'label': '수치 분석'}

    # ── 5. 레이블 + 정수/소수 (단위 없는 수치)
    labeled_nums = re.findall(
        r'([가-힣]{2,8})\s*[은는이가]?\s*(\d{1,6}(?:,\d{3})*(?:\.\d+)?)\s*'
        r'(?:만|억|조|개|명|달러|원|위안|%)?',
        text_plain)
    if len(labeled_nums) >= 2:
        factors = []
        for n, v in labeled_nums[:5]:
            raw = float(v.replace(',', ''))
            if raw > 100_000:  raw = round(raw / 10_000, 1)
            elif raw > 10_000: raw = round(raw / 1_000, 1)
            elif raw > 1_000:  raw = round(raw / 100, 1)
            elif raw > 100:    raw = round(raw / 10, 1)
            factors.append((n[:12], raw))
        if len(factors) >= 2:
            return {'type': 'impact', 'data': factors, 'label': '주요 수치'}

    # ── 6. fallback → 핵심 문장 하이라이트 (숫자 포함 문장 우선)
    sentences = [s.strip() for s in re.split(r'[.。!?]', text_plain)
                 if len(s.strip()) >= 15]
    best = (next((s for s in sentences if re.search(r'\d', s)), None)
            or (sentences[0] if sentences else keyword))
    return {'type': 'highlight', 'data': best[:60], 'label': '핵심 인사이트'}


def _split_long_paragraphs(html: str) -> str:
    """각 <p> 안의 문장 중 2문장(약 100자) 이상인 것이 있으면 1문장씩 별도 <p>로 분리.

    분리된 <p>들은 _inject_paragraph_images PASS 1에서 자연스럽게
    이미지 삽입 대상이 됨 (마지막 <p> 제외).
    """
    import re

    def _split_p(match):
        inner = match.group(1).strip()
        if not inner:
            return match.group(0)
        # 문장 경계: 한국어 마침표·물음표·느낌표 + 공백 or 끝
        sents = re.split(r'(?<=[.。!?])\s+', inner)
        sents = [s.strip() for s in sents if s.strip()]
        if len(sents) <= 1:
            return match.group(0)  # 단일 문장 → 그대로
        # 2문장(약 100자) 이상인 문장이 하나라도 있을 때만 분리
        if any(len(s) >= 100 for s in sents):
            return '\n'.join(f'<p>{s}</p>' for s in sents)
        return match.group(0)

    return re.sub(r'<p>(.*?)</p>', _split_p, html, flags=re.DOTALL)


def _inject_paragraph_images(html: str, keyword: str, sector: str,
                               market: dict = None, platform: str = 'naver') -> str:
    """섹션당 이미지 2개 삽입 — 첫 <p> 뒤 + 중간 <p> 뒤.

    AI 이미지(Pollinations)는 ThreadPoolExecutor 5개 병렬 생성.
    matplotlib 차트는 스레드 안전 문제로 순차 생성.
    모든 이미지는 글 내용 기반 동적 생성 (위치 고정 없음).
    """
    import re
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from collections import defaultdict

    # 2문장(약 100자)+ 문장이 있는 <p>를 먼저 1문장씩 분리 (이미지 삽입 슬롯 자동 증가)
    html = _split_long_paragraphs(html)

    parts = re.split(
        r'(<figure[^>]*class="blog-image[^"]*"[^>]*>.*?</figure>)',
        html, flags=re.DOTALL)

    # ── PASS 1: 각 섹션에서 삽입 위치 2곳 수집 ─────────────────────
    cidx_counter = [0]
    market_used  = [False]
    task_list    = []   # dict: part_idx, insert_after(p index), text, orig_p_matches

    for pi, part in enumerate(parts):
        if part.startswith('<figure'):
            continue
        p_matches = list(re.finditer(r'<p>(.*?)</p>', part, re.DOTALL))
        if len(p_matches) < 2:
            continue

        n        = len(p_matches)
        text_full = re.sub(r'<[^>]+>', '', part)
        is_market_section = (not market_used[0] and market
                             and re.search(r'시장|주가|지수|환율|금리|코스피|나스닥|S&P', text_full))
        if is_market_section:
            market_used[0] = True

        # 마지막 <p> 제외하고 모든 <p> 뒤에 이미지 삽입
        # 패턴: <p>글</p> → [이미지] → <p>글</p> → [이미지] → ... → <p>글</p>(마지막, 이미지 없음)
        for p_idx in range(n - 1):
            cidx_counter[0] += 1
            cidx = cidx_counter[0]

            # 이미지를 위한 텍스트: 섹션 전체 텍스트 (자르지 않음 — 설계서 생성에 활용)
            full_section_text = re.sub(r'<[^>]+>', '', part)
            # 소제목 추출: 현재 <p> 위치까지의 텍스트에서 마지막 h2/h3 탐색
            # (전체 part에서 re.search하면 항상 첫 번째 h2만 잡힘)
            _part_before_p = part[:p_matches[p_idx].start()]
            _h_m = None
            for _hm_iter in re.finditer(r'<h[23][^>]*>(.*?)</h[23]>', _part_before_p,
                                         re.IGNORECASE | re.DOTALL):
                _h_m = _hm_iter  # 현재 단락 직전의 마지막 h2/h3
            _section_title = re.sub(r'<[^>]+>', '', _h_m.group(1)).strip() if _h_m else ''
            # ctx_text: 해당 <p> + 다음 <p> (기존 폴백 함수들 호환용)
            ctx_text = re.sub(r'<[^>]+>', '',
                              p_matches[p_idx].group(0) + p_matches[p_idx + 1].group(0))

            task_list.append({
                'part_idx'      : pi,
                'insert_after'  : p_idx,      # 이 인덱스의 <p> 뒤에 삽입
                'orig_p_matches': p_matches,
                'cidx'          : cidx,
                'text'          : ctx_text,          # 기존 폴백용 (2문단)
                'full_text'     : full_section_text,  # NEW: 섹션 전체 텍스트
                'section_title' : _section_title,     # NEW: 소제목
                'market_chart'  : bool(is_market_section and p_idx == 0),
                'part_html'     : part,
            })

    # ── 이미지 개수 동적 결정 (★ 사용자 박제 2026-05-15) ──────
    # 박제 구조: 1 intro + 4섹션×2 + 1 outro = 10개. h2 4개 기준 상한 10.
    # h2 적을 때 (h2_count < 4) 도 안전: max(3, ...) 보장.
    h2_count = len(re.findall(r'<h[23][^>]*>', html, re.IGNORECASE))
    MAX_IMGS = max(3, min(h2_count * 2 + 2, 10))
    if len(task_list) > MAX_IMGS:
        market_tasks = [t for t in task_list if t['market_chart']]
        other_tasks  = [t for t in task_list if not t['market_chart']]
        slots = max(0, MAX_IMGS - len(market_tasks))
        if slots > 0 and other_tasks:
            step = max(1, len(other_tasks) // slots)
            selected = [other_tasks[min(i * step, len(other_tasks) - 1)] for i in range(slots)]
        else:
            selected = []
        task_list = market_tasks + selected
        print(f"  📸 섹션 이미지 동적 제한: h2/h3={h2_count}개 → 최대 {MAX_IMGS}개 (전체 슬롯 {len(market_tasks)+len(other_tasks)} → {len(task_list)}개 선택)")

    # ── PASS 2 & 3: 동적 이미지 유형 배정 (이미지 개수만큼 최대 다양성) ──────
    # 전체 유형 풀에서 겹치지 않게 순환 — 이미지 수가 많을수록 더 다양한 타입
    import time as _time_p3
    import random as _random

    # 전체 가용 유형 (market_chart 제외)
    _ALL_IMG_TYPES = [
        'ai',           # Pollinations AI 사진
        'impact',       # 수평 바차트 (영향 요인)
        'checklist',    # 체크리스트/테이블
        'scenario',     # 시나리오 카드
        'line_trend',   # 라인/에어리어 트렌드 차트
        'stat_card',    # KPI 숫자 인포그래픽
        'comparison',   # 좌우 비교 카드
        'highlight',    # 텍스트 하이라이트 카드
    ]

    def _build_slot_sequence(n: int) -> list:
        """n개 슬롯에 중복 최소화한 유형 시퀀스 반환."""
        seq = []
        pool = _ALL_IMG_TYPES[:]
        while len(seq) < n:
            chunk = pool[:]
            _random.seed(int(TODAY_STR.replace('-','')) + len(seq))  # 날짜 고정 시드 → 재실행해도 동일
            _random.shuffle(chunk)
            # 첫 사이클: AI를 앞쪽에 배치 (가장 시각적으로 다름)
            if not seq:
                chunk.remove('ai'); chunk.insert(0, 'ai')
            seq.extend(chunk)
        return seq[:n]

    # market_chart가 아닌 슬롯만 카운트
    non_market = [t for t in task_list if not t['market_chart']]
    slot_seq   = _build_slot_sequence(len(non_market))
    slot_map   = {t['cidx']: slot_seq[i] for i, t in enumerate(non_market)}

    img_results: dict[int, tuple[str, str]] = {}

    for task in task_list:
        cidx          = task['cidx']
        text          = task['text']             # 2문단 텍스트 (폴백용)
        full_text     = task.get('full_text', text)   # 섹션 전체 텍스트
        section_title = task.get('section_title', '')

        # ── 시장 현황 차트 (고정 — 데이터 기반)
        if task['market_chart']:
            path = make_market_chart(market, keyword, sector, cidx, platform)
            img_results[cidx] = (path, '글로벌 시장 현황')
            continue

        # ── NEW: 스마트 이미지 — 설계서 기반 전문 시각화 ─────────────────
        # 섹션 전체 텍스트 → Claude 설계서 → Plotly/SVG 렌더링
        # (내부에서 실패 시 AI 사진으로 자동 폴백)
        path = make_smart_section_image(
            section_text  = full_text,
            section_title = section_title,
            keyword       = keyword,
            sector        = sector,
            card_idx      = cidx,
            platform      = platform,
            out_dir       = _img_dir(platform),
        )
        label = section_title or f'{keyword} 핵심 데이터'

        if path:
            img_results[cidx] = (path, label)
        else:
            print(f"  ⚠️ 이미지 생성 실패 (cidx={cidx})")

    # ── PASS 4: 역순 삽입 (높은 위치부터 → 낮은 위치 보존) ──────────
    tasks_by_part: dict[int, list] = defaultdict(list)
    for task in task_list:
        cidx = task['cidx']
        if cidx not in img_results:
            continue
        path, label = img_results[cidx]
        if not path or not Path(path).exists():
            continue
        insert_pos = task['orig_p_matches'][task['insert_after']].end()
        tasks_by_part[task['part_idx']].append((insert_pos, cidx, path, label))

    result_parts = list(parts)
    inserted = 0

    for pi, insertions in tasks_by_part.items():
        part = result_parts[pi]
        # 높은 위치부터 처리 → 낮은 위치 오프셋 불변
        for pos, cidx, path, label in sorted(insertions, key=lambda x: x[0], reverse=True):
            placeholder = f'__PARA_IMG_{cidx}__'
            fig_html    = (f'\n<figure class="blog-image size-large">'
                           f'<img src="{placeholder}" alt="{label}" /></figure>\n')
            _para_img_paths[cidx] = path
            part     = part[:pos] + fig_html + part[pos:]
            inserted += 1
        result_parts[pi] = part

    print(f"  📊 섹션별 콘텐츠 이미지 {inserted}개 삽입 완료 (<p> 사이마다 1개, 마지막 제외)")
    return ''.join(result_parts)


# 섹션 이미지 + 단락 이미지 경로 임시 저장 (generate → run 간 전달)
_section_img_paths: dict[int, str] = {}
_para_img_paths:    dict[int, str] = {}


def _load_learn_insights(scope: str) -> str:
    """DB에서 학습 지침 로드 → 프롬프트 주입 블록 반환."""
    try:
        from shared import db as _db
        rows = _db.get_top_learning_insights(limit=6, days=14, scope=scope)
        if not rows:
            return ""
        lines = [
            "",
            "─" * 30,
            "📚 *과거 글 분석 기반 작성 지침 — 반드시 적용:*",
            "",
        ]
        for i, r in enumerate(rows, 1):
            d = (r.get("directive") or r.get("description") or "").strip()
            if d:
                lines.append(f"{i}. {d}  (재발견 {r.get('occurrences', 1)}회)")
        return "\n".join(lines) + "\n"
    except Exception as e:
        print(f"  ⚠️ 학습 지침 로드 실패(무시): {e}")
        _g_report("writer", e, module=__name__)
        return ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  10. 진입점 — run_tistory / run_naver
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _legacy_publish_guard(name: str) -> None:
    """★ P0-② 패치 (사용자 박제 2026-05-18 — ADR 009 v2 우회 차단).

    레거시 직접 발행 함수 (run_tistory/run_naver) 가 *harness 외부* 에서
    호출되면 차단. economic_poster.run() 의 harness 경로만 합법 진입점.

    우회 허용: JARVIS_ALLOW_LEGACY_PUBLISH=1 환경변수 (CLI 디버그·수동 점검 전용).
    """
    import os as _os
    if _os.environ.get("JARVIS_ALLOW_LEGACY_PUBLISH") == "1":
        return  # 명시적 우회 허용
    # 호출자 스택 검사 — harness send 콜백 (_send_all) 안에서 호출되면 허용
    import sys as _sys
    caller_names = []
    if hasattr(_sys, "_getframe"):
        try:
            frame = _sys._getframe(1)  # guard 의 직접 호출자부터
        except ValueError:
            frame = None
        while frame is not None and len(caller_names) < 10:
            caller_names.append(frame.f_code.co_name)
            frame = frame.f_back
    if any(n in ("_send_all", "_send", "run_action") for n in caller_names):
        return  # harness 경로 — 합법
    # 비합법 — GUARDIAN 박제 후 raise
    try:
        from JARVIS07_GUARDIAN.error_collector import report as _gr
        _gr(source="writer",
            exc=RuntimeError(f"레거시 {name}() 가 harness 외부에서 호출됨 — 우회 차단"),
            module=__name__, func_name=name,
            context={"caller_chain": caller_names[:5]})
    except Exception:
        pass
    raise RuntimeError(
        f"[Legacy bypass blocked] {name}() 는 harness send 콜백 안에서만 호출 허용. "
        f"economic_poster.run() 사용 또는 JARVIS_ALLOW_LEGACY_PUBLISH=1 환경변수 설정."
    )


def run_tistory() -> dict:
    """티스토리 트렌드 경제 글 발행 진입점.

    1-pass 파이프라인 (Claude Code SDK 완성 원고):
      ① 데이터 수집  — load_today_trends() + 키워드 선정
      ② 규정 로드    — BLOG_SUPREME_LAW (데이터 수집 직후)
      ③ 원고 생성    — Claude Code SDK 1-pass: 텍스트 + inline SVG 완성 원고
      ④ HTML 저장    — output/html/{date}_{keyword}/article.html
      ⑤ SVG 캡처     — JARVIS06: inline SVG 추출 → JPG (cairosvg/Selenium)
      ⑥ 블록 조립    — SVG 제거 텍스트 섹션 + JPG 경로 인터리빙
      ⑦ 품질 검증    — enforce_text_between_images + enforce_supreme_law
      ⑧ 발행         — tistory_poster (Selenium)

    Returns: {"success": bool, "url": str, "keyword": str}
    """
    _legacy_publish_guard("run_tistory")
    print("\n  🔴 [TISTORY-TREND] 트렌드 기반 경제 글 발행 시작 (HTML 파이프라인)...")
    _cleanup_tistory_images()

    # ── ⓪ 쿠키 사전 갱신 (★ 사용자 직접 박제 2026-05-14) ───────────
    # 글 작성 전 항상 .env의 TS_USERNAME/TS_PASSWORD로 로그인 → TSSESSION 갱신.
    # 실패 시 즉시 return → Claude API 비용 0. driver 는 ⑧단계까지 재사용.
    print("  🍪 [TISTORY-TREND] ⓪ 글 작성 전 쿠키 강제 갱신")
    _preloaded_driver = None
    try:
        from JARVIS08_PUBLISH.credentials.tistory_cookie_refresher import run as _tcr_run
        _ok, _preloaded_driver = _tcr_run(force=True, return_driver=True)
        if not _ok:
            _tg("❌ [TISTORY-TREND] 쿠키 갱신 실패 — 글 작성 중단 (Claude 비용 0)")
            if _preloaded_driver:
                try: _preloaded_driver.quit()
                except Exception: pass
            return {"success": False, "url": "", "keyword": ""}
        load_dotenv(override=True)
        print("  ✅ [TISTORY-TREND] ⓪ 쿠키 갱신 완료 — driver 재사용")
    except Exception as _ce:
        print(f"  ❌ [TISTORY-TREND] ⓪ 쿠키 갱신 예외: {_ce}")
        _g_report("writer", _ce, module=__name__)
        if _preloaded_driver:
            try: _preloaded_driver.quit()
            except Exception: pass
        return {"success": False, "url": "", "keyword": ""}

    # ── ① 데이터 수집 ──────────────────────────────────────────────
    trends = load_today_trends()
    if not trends:
        _tg("⚠️ [TISTORY-TREND] 트렌드 데이터 없음 — 발행 건너뜀")
        if _preloaded_driver:
            try: _preloaded_driver.quit()
            except Exception: pass
        return {"success": False, "url": "", "keyword": ""}

    topic = select_tistory_topic(trends)
    if not topic:
        _tg("⚠️ [TISTORY-TREND] 경제 관련 주제 없음 — 발행 건너뜀")
        return {"success": False, "url": "", "keyword": ""}

    keyword = topic.get('keyword', '')
    sector  = topic.get('sector', '')
    reason  = topic.get('reason', '')
    _tg(f"📝 [TISTORY-TREND] 주제 선정: *{keyword}* ({sector})\n"
        f"기회점수: {topic.get('opportunity_score', 0):.1f} | 이유: {reason[:60]}")

    # ── ★ 단일 진입점: 이미지 상태 격리 (날짜·run_id 기반 폴더) ─────
    try:
        from JARVIS09_COLLECTOR.run_context import new_run as _new_run
        _new_run(keyword, platform="tistory", post_type="economic")
    except Exception:
        pass

    # ── ② 규정 로드 ──────────────────────────────────────────────────
    print("  📜 [티스토리] 블로그 최상위 헌법 + 규정 로드 중...")
    from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
    supreme_block = _law_blk()
    print("  ✅ [티스토리] 규정 로드 완료")

    # ── ③ HTML 생성 (글 + 시각 블록 통합) ──────────────────────────
    from JARVIS02_WRITER.tistory_html_writer import (
        generate_article_html, save_article_html,
        screenshot_article,
        extract_title, extract_text_content,
    )
    from JARVIS06_IMAGE.injectors import assemble_blocks

    html = generate_article_html(keyword, sector, reason, supreme_block)
    if not html:
        _tg(f"❌ [TISTORY-TREND] HTML 생성 실패: {keyword}")
        return {"success": False, "url": "", "keyword": keyword}

    title = extract_title(html, keyword)

    # ── ④ HTML 저장 ─────────────────────────────────────────────────
    html_path, img_dir = save_article_html(html, keyword, platform="tistory")
    img_dir = str(TISTORY_IMG_DIR)  # ★ 플랫폼별 폴더 사용 (새 폴더 생성 X)

    # ── ⑤ SVG 추출 → JPG 캡처 (JARVIS06 단일 진입점) ───────────────
    print("  📸 [티스토리] SVG → JPG 캡처 (JARVIS06)...")
    visual_paths = screenshot_article(html_path, img_dir)
    if not visual_paths:
        _tg(f"⚠️ [TISTORY-TREND] 스크린샷 0개 — 텍스트 전용으로 발행 계속")
    else:
        print(f"  ✅ [티스토리] JPG {len(visual_paths)}개 생성")

    # ── ⑥ 블록 조립 (p/svg/h2 순서 파싱 → svg를 JPG로 치환) ────────
    blocks  = assemble_blocks(html, visual_paths, out_dir=img_dir)
    content = extract_text_content(html)   # post_to_tistory html_content용

    # ── ⑥-A+⑥-B 제4조 보강(병렬) + 썸네일 생성(동시) ─────────────────
    from concurrent.futures import ThreadPoolExecutor as _TsTPE
    from pathlib import Path as _P
    _ts_thumb_exec = _TsTPE(max_workers=1)
    _ts_thumb_fut = None
    try:
        from JARVIS06_IMAGE.image_agent import generate_thumbnail as _gen_thumb
        _ts_thumb_fut = _ts_thumb_exec.submit(
            _gen_thumb, title=title, keyword=keyword, sector=sector,
            platform="tistory", out_dir=_P(img_dir),
            body_text=content[:400],
        )
        print("  🖼️  [티스토리] 썸네일 생성 시작 (백그라운드)...")
    except Exception as _te:
        print(f"  ⚠️ 썸네일 시작 오류 (무시): {_te}")
        _g_report("writer", _te, module=__name__)

    # 제4조 보강 — 경제 브리핑은 SVG 데이터 차트가 이미지 역할 → AI 사진 삽입 금지

    # 썸네일 결과 수령 → blocks 맨 앞 삽입
    if _ts_thumb_fut:
        try:
            thumb_path = _ts_thumb_fut.result(timeout=300)
            if thumb_path and _P(thumb_path).exists():
                blocks = [("image", thumb_path)] + blocks
                print(f"  ✅ 썸네일: {_P(thumb_path).name}")
            else:
                print("  ⚠️ 썸네일 생성 실패 — 썸네일 없이 계속")
        except Exception as _te:
            print(f"  ⚠️ 썸네일 오류 (무시): {_te}")
            _g_report("writer", _te, module=__name__)
        finally:
            _ts_thumb_exec.shutdown(wait=False)

    n_text = sum(1 for b in blocks if b[0] == "text")
    n_img  = sum(1 for b in blocks if b[0] == "image")
    print(f"  ✅ [티스토리] 블록 {len(blocks)}개 조립 (텍스트 {n_text}개 + 이미지 {n_img}개)")

    # ── ⑦ 품질 검증 ─────────────────────────────────────────────────
    try:
        from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
        blocks = enforce_text_between_images(blocks, source='TISTORY-TREND')
    except Exception as _ee:
        print(f"  ⚠️ enforce_text_between_images(tistory) 오류 (무시): {_ee}")
        _g_report("writer", _ee, module=__name__)

    _ts_law_block = False
    try:
        from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations, is_blocking as _is_blk
        from JARVIS06_IMAGE.injectors import compute_unused_image_pool
        _ts_pool = compute_unused_image_pool(blocks, visual_paths)
        blocks, _ts_v = enforce_supreme_law(blocks, "tistory", "TISTORY-경제글", image_pool=_ts_pool)
        notify_violations(_ts_v, "tistory", "TISTORY-경제글")
        _ts_law_block, _ts_blk_msgs = _is_blk(_ts_v)
        if _ts_law_block:
            send_telegram(f"🚫 [티스토리] 헌법 위반 — 발행 차단\n" + "\n".join(f"• {m}" for m in _ts_blk_msgs))
            log(f"🚫 [티스토리] 헌법 위반 차단: {_ts_blk_msgs}")
    except Exception as _le:
        print(f"  ⚠️ LawEnforcer(tistory) 오류 (무시): {_le}")
        _g_report("writer", _le, module=__name__)

    if _ts_law_block:
        print("  🚫 [티스토리] 헌법 위반 — 발행 건너뜀")
        return {"success": False, "url": "", "keyword": keyword, "blocked": True}

    # ── ⑧ 발행 (⓪에서 받은 driver 재사용 — 재로그인 0) ──────────────
    print("  📤 [티스토리] 발행 시작 (⓪ 갱신 driver 재사용)")
    try:
        # ★ ADR 008 Phase 2 완전 이관 (사용자 박제 2026-05-18) — shim 제거, 신 경로 직접 import
        import JARVIS08_PUBLISH.platforms.tistory_poster as tistory_poster
        # ★ ERRORS [145] LOGIN_SUPREME_LAW 위임
        from JARVIS08_PUBLISH.credentials.login_manager import get_tistory_cookie
        tistory_poster.TS_COOKIE = get_tistory_cookie().strip('"').strip("'")
        from JARVIS08_PUBLISH.platforms import post_to_tistory

        # ★ 사용자 박제 2026-05-15 — 태그 특수기호 절대 금지 (제14조 단일 진입점)
        from shared.seo import sanitize_tags as _stg
        tags  = _stg([keyword, sector, '경제브리핑', '경제', '트렌드'])
        ts_ok = post_to_tistory(
            title=title,
            html_content=content,
            blocks=blocks,
            category="경제 브리핑",
            preloaded_driver=_preloaded_driver,
            tags=tags,
        )

        if ts_ok:
            # DB 저장 (텍스트 + HTML + 이미지 경로 포함)
            try:
                from shared.bus import on_post_published_detail as _emit
                _all_imgs = [str(b[1]) for b in blocks if b[0] == "image"]
                _emit(theme=keyword, platform="tistory", title=title,
                      content=content, html=html,
                      source_keyword=keyword, post_type="economic",
                      image_paths=_all_imgs)
                print(f"  ✅ [DB] post_analysis 저장 완료 (이미지 {len(_all_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류 (무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
            _mark_keyword_used(keyword, "tistory")
            _tg(f"✅ [TISTORY-TREND] 발행 완료!\n제목: {title}\n키워드: {keyword}\n이미지: {len(visual_paths)}개")
            print(f"  ✅ [티스토리] 발행 완료: {title}")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [TISTORY-TREND] 발행 실패: {keyword}")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        _tg(f"❌ [TISTORY-TREND] 예외 발생: {str(e)[:100]}")
        print(f"  ❌ [티스토리] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": keyword}


def run_naver(ts_keyword: str = '') -> dict:
    """네이버 트렌드 경제 글 발행 진입점 — 티스토리와 동일한 1-pass 파이프라인.

    ① 데이터 수집  — load_today_trends() + 티스토리 중복 제외 키워드 선정
    ② 규정 로드    — BLOG_SUPREME_LAW
    ③ 원고 생성    — Claude Code SDK 1-pass (platform="naver", 해요체)
    ④ HTML 저장    — output/html/{date}_{kw_hash}_naver/article.html
    ⑤ SVG 캡처     — JARVIS06: inline SVG → JPG
    ⑥ 블록 조립    — assemble_blocks()
    ⑥-A 제4조 보강 — text→text 구간 이미지 삽입
    ⑥-B 썸네일    — generate_thumbnail() → blocks 맨 앞
    ⑦ 품질 검증    — enforce_text_between_images + enforce_supreme_law (spacer 포함)
    ⑧ 발행         — naver_poster (Selenium)

    Returns: {"success": bool, "url": str, "keyword": str}
    """
    _legacy_publish_guard("run_naver")
    print("\n  🟢 [NAVER-TREND] 트렌드 기반 경제 글 발행 시작 (1-pass 파이프라인)...")
    _cleanup_naver_images()

    keyword = ""
    try:
        # ── ① 데이터 수집 ──────────────────────────────────────────────
        trends = load_today_trends()
        if not trends:
            _tg("⚠️ [NAVER-TREND] 트렌드 데이터 없음 — 발행 건너뜀")
            return {"success": False, "url": "", "keyword": ""}

        topic = select_naver_topic(trends, ts_keyword=ts_keyword)
        if not topic:
            _tg("⚠️ [NAVER-TREND] 경제 관련 주제 없음 — 발행 건너뜀")
            return {"success": False, "url": "", "keyword": ""}

        keyword = topic.get('keyword', '')
        sector  = topic.get('sector', '')
        reason  = topic.get('reason', '')
        _tg(f"📝 [NAVER-TREND] 주제 선정: *{keyword}* ({sector})\n"
            f"기회점수: {topic.get('opportunity_score', 0):.1f} | 이유: {reason[:60]}")

        # ── ★ 단일 진입점: 이미지 상태 격리 (날짜·run_id 기반 폴더) ─────
        try:
            from JARVIS09_COLLECTOR.run_context import new_run as _new_run
            _new_run(keyword, platform="naver", post_type="economic")
        except Exception:
            pass

        # ── ② 규정 로드 ──────────────────────────────────────────────────
        print("  📜 [네이버] 블로그 최상위 헌법 + 규정 로드 중...")
        from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
        supreme_block = _law_blk()
        print("  ✅ [네이버] 규정 로드 완료")

        # ── ③ HTML 생성 ──────────────────────────────────────────────────
        from JARVIS02_WRITER.tistory_html_writer import (
            generate_article_html, save_article_html,
            screenshot_article,
            extract_title, extract_text_content,
        )
        from JARVIS06_IMAGE.injectors import assemble_blocks

        html = generate_article_html(keyword, sector, reason, supreme_block,
                                     platform="naver")
        if not html:
            _tg(f"❌ [NAVER-TREND] 원고 생성 실패: {keyword}")
            return {"success": False, "url": "", "keyword": keyword}

        title   = extract_title(html, keyword)
        content = extract_text_content(html)

        # ── ④ HTML 저장 ─────────────────────────────────────────────────
        import hashlib as _hs
        from pathlib import Path as _P
        _kw_hash = _hs.md5(f"{keyword}_naver".encode()).hexdigest()[:8]
        from JARVIS02_WRITER.tistory_html_writer import OUTPUT_HTML_DIR
        _html_dir = OUTPUT_HTML_DIR / f"{_kw_hash}_naver"
        _img_dir  = NAVER_IMG_DIR  # ★ 플랫폼별 폴더 사용 (새 폴더 생성 X)
        _html_dir.mkdir(parents=True, exist_ok=True)
        # ★ 새로운 이미지 생성 전 기존 이미지 모두 삭제 (리셋)
        if _img_dir.exists():
            shutil.rmtree(_img_dir)
            print(f"  🔄 [Naver] 이미지 폴더 리셋: {_img_dir}")
        _img_dir.mkdir(parents=True, exist_ok=True)
        _html_path = str(_html_dir / "article.html")
        _P(_html_path).write_text(html, encoding="utf-8")
        img_dir = str(_img_dir)

        # ── ⑤ SVG 캡처 ──────────────────────────────────────────────────
        print("  📸 [네이버] SVG → JPG 캡처 (JARVIS06)...")
        visual_paths = screenshot_article(_html_path, img_dir)
        if not visual_paths:
            _tg("⚠️ [NAVER-TREND] 스크린샷 0개 — 텍스트 전용으로 발행 계속")
        else:
            print(f"  ✅ [네이버] JPG {len(visual_paths)}개 생성")

        # ── ⑥ 블록 조립 ─────────────────────────────────────────────────
        blocks = assemble_blocks(html, visual_paths, out_dir=img_dir)

        # ── ⑥-A+⑥-B 제4조 보강(병렬) + 썸네일 생성(동시) ─────────────────
        from concurrent.futures import ThreadPoolExecutor as _NvTPE
        _nv_thumb_exec = _NvTPE(max_workers=1)
        _nv_thumb_fut = None
        try:
            from JARVIS06_IMAGE.image_agent import generate_thumbnail as _gen_thumb
            _nv_thumb_fut = _nv_thumb_exec.submit(
                _gen_thumb, title=title, keyword=keyword, sector=sector,
                platform="naver", out_dir=_img_dir,
                body_text=content[:400],
            )
            print("  🖼️  [네이버] 썸네일 생성 시작 (백그라운드)...")
        except Exception as _te:
            print(f"  ⚠️ 썸네일 시작 오류 (무시): {_te}")
            _g_report("writer", _te, module=__name__)

        # 제4조 보강 — 경제 브리핑은 SVG 데이터 차트가 이미지 역할 → AI 사진 삽입 금지

        # 썸네일 결과 수령 → blocks 맨 앞 삽입
        if _nv_thumb_fut:
            try:
                thumb_path = _nv_thumb_fut.result(timeout=300)
                if thumb_path and _P(thumb_path).exists():
                    blocks = [("image", thumb_path)] + blocks
                    print(f"  ✅ 썸네일: {_P(thumb_path).name}")
                else:
                    print("  ⚠️ 썸네일 생성 실패 — 썸네일 없이 계속")
            except Exception as _te:
                print(f"  ⚠️ 썸네일 오류 (무시): {_te}")
                _g_report("writer", _te, module=__name__)
            finally:
                _nv_thumb_exec.shutdown(wait=False)

        n_text = sum(1 for b in blocks if b[0] == "text")
        n_img  = sum(1 for b in blocks if b[0] == "image")
        print(f"  ✅ [네이버] 블록 {len(blocks)}개 (텍스트 {n_text}개 + 이미지 {n_img}개)")

        # ── ⑦ 품질 검증 ─────────────────────────────────────────────────
        try:
            from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
            blocks = enforce_text_between_images(blocks, source='NAVER-TREND')
        except Exception as _ee:
            print(f"  ⚠️ enforce_text_between_images(naver) 오류 (무시): {_ee}")
            _g_report("writer", _ee, module=__name__)

        _nv_law_block = False
        try:
            from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations, is_blocking as _is_blk
            from JARVIS06_IMAGE.injectors import compute_unused_image_pool
            _nv_pool = compute_unused_image_pool(blocks, visual_paths)
            blocks, _nv_v = enforce_supreme_law(blocks, "naver", "NAVER-경제글", image_pool=_nv_pool)
            notify_violations(_nv_v, "naver", "NAVER-경제글")
            _nv_law_block, _nv_blk_msgs = _is_blk(_nv_v)
            if _nv_law_block:
                send_telegram(f"🚫 [네이버] 헌법 위반 — 발행 차단\n" + "\n".join(f"• {m}" for m in _nv_blk_msgs))
                log(f"🚫 [네이버] 헌법 위반 차단: {_nv_blk_msgs}")
        except Exception as _le:
            print(f"  ⚠️ LawEnforcer(naver) 오류 (무시): {_le}")
            _g_report("writer", _le, module=__name__)

        if _nv_law_block:
            print("  🚫 [네이버] 헌법 위반 — 발행 건너뜀")
            return False

        # ── ⑧ 발행 ──────────────────────────────────────────────────────
        from JARVIS08_PUBLISH.platforms import post_to_naver
        # ★ 사용자 박제 2026-05-15 — 태그 특수기호 절대 금지 (제14조 단일 진입점)
        from shared.seo import sanitize_tags as _stg
        tags = _stg([keyword, sector, '경제브리핑', '경제', '트렌드'])
        nv_ok = post_to_naver(
            title=title,
            html_content=content,
            blocks=blocks,
            category="경제 브리핑",
            tags=tags,
        )

        if nv_ok:
            # DB 저장 (텍스트 + HTML + 이미지 경로 포함)
            try:
                from shared.bus import on_post_published_detail as _emit
                _all_imgs = [str(b[1]) for b in blocks if b[0] == "image"]
                _emit(theme=keyword, platform="naver", title=title,
                      content=content, html=html,
                      source_keyword=keyword, post_type="economic",
                      image_paths=_all_imgs)
                print(f"  ✅ [DB] post_analysis 저장 완료 (이미지 {len(_all_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류 (무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
            _mark_keyword_used(keyword, "naver")
            _tg(f"✅ [NAVER-TREND] 발행 완료!\n제목: {title}\n키워드: {keyword}\n이미지: {len(visual_paths)}개")
            print(f"  ✅ [네이버] 발행 완료: {title}")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [NAVER-TREND] 발행 실패: {keyword}")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        _tg(f"❌ [NAVER-TREND] 예외 발생: {str(e)[:100]}")
        print(f"  ❌ [네이버] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": keyword}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  분리 함수 — 대본 생성 + 발행 분리 (병렬화용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ts_generate_draft(supreme_block=None, collection_docs=None, nv_keyword: str = '') -> dict:
    """티스토리 대본 생성 (①-⑦ 단계) — 네이버와 중복되지 않은 주제로 (네이버 우선 직렬)."""
    from datetime import datetime as _dt_ts
    print(f"\n  🔴 [TISTORY-DRAFT] 대본 생성 중... [{_dt_ts.now().strftime('%H:%M:%S')}]")
    # ★ 글 작성 전 인메모리 캐시 전체 초기화 — 이전 글 잔재 완전 제거
    try:
        from JARVIS06_IMAGE.chart_generator import clear_session_cache as _clear_cache
        _clear_cache()
    except Exception as _ce:
        print(f"  ⚠️ [cache clear] 스킵: {_ce}")
    _section_img_paths.clear()
    _para_img_paths.clear()
    _cleanup_tistory_images()

    keyword = ""
    try:
        import os as _os
        _force = _os.environ.get("JARVIS_FORCE_TOPIC", "").strip()
        if _force:
            keyword = _force
            sector = _os.environ.get("JARVIS_FORCE_SECTOR", "").strip() or "강제 지정"
            reason = _os.environ.get("JARVIS_FORCE_REASON", "").strip() or "사용자 강제 주제"
            print(f"  📌 [강제 주제 — 티스토리] {keyword}")
        else:
            trends = load_today_trends()
            if not trends:
                return {"success": False, "keyword": "", "error": "트렌드 데이터 없음"}
            topic = select_tistory_topic(trends, nv_keyword=nv_keyword)
            if not topic:
                return {"success": False, "keyword": "", "error": "경제 주제 없음"}
            keyword = topic.get('keyword', '')
            sector = topic.get('sector', '')
            reason = topic.get('reason', '')

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        # ★ 생성 단계 예방 (사용자 박제 2026-06-29): 키워드 빈도 규칙을 작성 프롬프트에 주입
        #   — 검증과 동일 임계(keyword_min_count) → "처음부터 규정대로" → 사후 차단 방지
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass

        # ── ★ 데이터-우선 (사용자 박제 2026-06-30): 대본 작성 *전* 실데이터 풍부 수집 →
        #    카탈로그를 supreme_block(모든 Pass-1 호출 전달)에 주입 → 대본이 *있는 차트만* 계획.
        #    세션풀 등록 → Pass-2 가 재수집 없이 같은 실데이터로 인포그래픽 (text↔chart 일치).
        try:
            from JARVIS09_COLLECTOR import collect_chart_data as _ccd
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            from JARVIS06_IMAGE.chart_generator import set_session_pool as _ssp
            _pool = (_ccd(keyword, sector=sector, description=reason or keyword, max_datasets=12) or {}).get("datasets", [])
            # ★ 테마 앵커 재시도 (사용자 박제 2026-07-01): reason 이 관광 등으로 드리프트해 게이트가
            #   풀을 비우면, 주제(keyword) 자체로 재수집 → 항상 *주제* 실데이터로 채움.
            if len(_pool) < 4:
                _seen = {d.get("title") for d in _pool}
                _pool2 = (_ccd(keyword, sector=sector, description=keyword, max_datasets=12) or {}).get("datasets", [])
                _pool += [d for d in _pool2 if d.get("title") not in _seen]
            _ssp(_pool)   # ★ 항상 등록(빈 풀 포함) → chart_generator 가 *오직* 이 풀만 사용(garbage 폴백 차단)
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입 + 세션풀 등록")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 수집 스킵: {_de}")

        # ★ 글 keyword로 JARVIS09 재수집 — 대본 주제에 맞는 데이터로 이미지 생성
        _kw_collection_docs = list(collection_docs or [])
        try:
            from JARVIS09_COLLECTOR import collect_for_theme as _j09_kw_collect
            _kw_docs = _j09_kw_collect(keyword, sector)
            if _kw_docs:
                _kw_collection_docs = _kw_docs
                print(f"  🕸️ [JARVIS09] '{keyword}' 재수집: {len(_kw_docs)}건 → 이미지 생성 전달")
            else:
                print(f"  ⚠️ [JARVIS09] '{keyword}' 재수집 0건 — 경제 일반 수집물 유지")
        except Exception as _j09_kw_e:
            print(f"  ⚠️ [JARVIS09] '{keyword}' 재수집 스킵: {_j09_kw_e}")

        from JARVIS02_WRITER.tistory_html_writer import (
            generate_article_html, save_article_html, screenshot_article,
            extract_title, extract_text_content,
        )
        from JARVIS06_IMAGE.injectors import assemble_blocks

        html = generate_article_html(keyword, sector, reason, supreme_block,
                                     collection_docs=_kw_collection_docs)
        if not html:
            return {"success": False, "keyword": keyword, "error": "HTML 생성 실패"}

        title = extract_title(html, keyword)
        content = extract_text_content(html)
        html_path, img_dir = save_article_html(html, keyword, platform="tistory")

        visual_paths = screenshot_article(html_path, img_dir)
        blocks = assemble_blocks(html, visual_paths, out_dir=img_dir)

        # 썸네일 생성
        from concurrent.futures import ThreadPoolExecutor as _TsTPE
        _ts_thumb_exec = _TsTPE(max_workers=1)
        _ts_thumb_fut = None
        thumb_path = None
        try:
            from JARVIS06_IMAGE.image_agent import generate_thumbnail as _gen_thumb
            _ts_thumb_fut = _ts_thumb_exec.submit(
                _gen_thumb, title=title, keyword=keyword, sector=sector,
                platform="tistory", out_dir=__import__('pathlib').Path(img_dir),
                body_text=content[:400],
            )
        except Exception as _te:
            _g_report("writer", _te, module=__name__)

        if _ts_thumb_fut:
            try:
                thumb_path = _ts_thumb_fut.result(timeout=300)
                if thumb_path and __import__('pathlib').Path(thumb_path).exists():
                    blocks = [("image", thumb_path)] + blocks
            except Exception:
                pass
            finally:
                _ts_thumb_exec.shutdown(wait=False)

        # 검증
        try:
            from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
            blocks = enforce_text_between_images(blocks, source='TISTORY-DRAFT')
        except Exception as _ee:
            print(f"  ⚠️ enforce_text_between_images(tistory-draft) 오류 (무시): {_ee}")
            _g_report("writer", _ee, module=__name__)

        try:
            from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
            blocks, _ts_v = enforce_supreme_law(blocks, "tistory", "Tistory-경제글")
            notify_violations(_ts_v, "tistory", "Tistory-경제글")
        except Exception:
            _g_report("writer", Exception(), module=__name__)

        print(f"  ✅ [TISTORY-DRAFT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "title": title,
            "html": html,
            "content": content,
            "html_path": html_path,
            "img_dir": img_dir,
            "blocks": blocks,
            "visual_paths": visual_paths,
            # ★ 2-2 (2026-07-02): 작성에 실제 쓴 주제 특화 corpus(kw 재수집, 실패 시 일반)를
            #   검증(prepublish 사실성 게이트) source_docs 로 노출 — 작성=검증 corpus 정합.
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-DRAFT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


def ts_publish(draft: dict) -> dict:
    """티스토리 대본 발행 (⑧ 단계)."""
    if not draft.get('success'):
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}

    try:
        from JARVIS08_PUBLISH.platforms import post_to_tistory
        print(f"  📤 [TISTORY-PUB] 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']
        html = draft['html']

        try:
            from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
            blocks, _ts_v = enforce_supreme_law(blocks, "tistory", "Tistory-경제글-발행")
            notify_violations(_ts_v, "tistory", "Tistory-경제글-발행")
        except Exception:
            pass

        result = post_to_tistory(
            title=draft['title'],
            html_content=draft['content'],
            blocks=blocks,
            category=ECONOMIC_CATEGORY,
        )

        if result:
            _tg(f"✅ [TISTORY-TREND] 발행 완료!\n제목: {draft['title']}\n키워드: {keyword}")
            print(f"  ✅ [TISTORY-PUB] 완료")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [TISTORY-TREND] 발행 실패")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-PUB] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}


def nv_generate_draft(ts_keyword: str = '', supreme_block=None, collection_docs=None) -> dict:
    """네이버 대본 생성 (①-⑦ 단계) — 티스토리와 중복되지 않은 주제로."""
    from datetime import datetime as _dt_nv
    print(f"\n  🟢 [NAVER-DRAFT] 대본 생성 중... [{_dt_nv.now().strftime('%H:%M:%S')}]")
    # ★ 글 작성 전 인메모리 캐시 전체 초기화 — 이전 글 잔재 완전 제거
    try:
        from JARVIS06_IMAGE.chart_generator import clear_session_cache as _clear_cache
        _clear_cache()
    except Exception as _ce:
        print(f"  ⚠️ [cache clear] 스킵: {_ce}")
    _section_img_paths.clear()
    _para_img_paths.clear()
    _cleanup_naver_images()   # ★ 재생성 시 직전 시도 이미지 리셋 (TS 와 동일 패턴)

    keyword = ""
    try:
        import os as _os
        _force_nv = _os.environ.get("JARVIS_FORCE_NV_TOPIC", "").strip()
        if _force_nv:
            keyword = _force_nv
            sector = _os.environ.get("JARVIS_FORCE_NV_SECTOR", "").strip() or "강제 지정"
            reason = _os.environ.get("JARVIS_FORCE_NV_REASON", "").strip() or "사용자 강제 주제"
            print(f"  📌 [강제 주제 — 네이버] {keyword}")
        else:
            trends = load_today_trends()
            if not trends:
                return {"success": False, "keyword": "", "error": "트렌드 데이터 없음"}

            topic = select_naver_topic(trends, ts_keyword=ts_keyword)
            if not topic:
                return {"success": False, "keyword": "", "error": "경제 주제 없음"}

            keyword = topic.get('keyword', '')
            sector = topic.get('sector', '')
            reason = topic.get('reason', '')

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        # ★ 생성 단계 예방 (사용자 박제 2026-06-29): 키워드 빈도 규칙을 작성 프롬프트에 주입
        #   — 검증과 동일 임계(keyword_min_count) → "처음부터 규정대로" → 사후 차단 방지
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass

        # ── ★ 데이터-우선 (사용자 박제 2026-06-30): 대본 작성 *전* 실데이터 풍부 수집 →
        #    카탈로그를 supreme_block(모든 Pass-1 호출 전달)에 주입 → 대본이 *있는 차트만* 계획.
        #    세션풀 등록 → Pass-2 가 재수집 없이 같은 실데이터로 인포그래픽 (text↔chart 일치).
        try:
            from JARVIS09_COLLECTOR import collect_chart_data as _ccd
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            from JARVIS06_IMAGE.chart_generator import set_session_pool as _ssp
            _pool = (_ccd(keyword, sector=sector, description=reason or keyword, max_datasets=12) or {}).get("datasets", [])
            # ★ 테마 앵커 재시도 (사용자 박제 2026-07-01): reason 이 관광 등으로 드리프트해 게이트가
            #   풀을 비우면, 주제(keyword) 자체로 재수집 → 항상 *주제* 실데이터로 채움.
            if len(_pool) < 4:
                _seen = {d.get("title") for d in _pool}
                _pool2 = (_ccd(keyword, sector=sector, description=keyword, max_datasets=12) or {}).get("datasets", [])
                _pool += [d for d in _pool2 if d.get("title") not in _seen]
            _ssp(_pool)   # ★ 항상 등록(빈 풀 포함) → chart_generator 가 *오직* 이 풀만 사용(garbage 폴백 차단)
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입 + 세션풀 등록")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 수집 스킵: {_de}")

        # ★ 글 keyword로 JARVIS09 재수집 — 대본 주제에 맞는 데이터로 이미지 생성
        _kw_collection_docs = list(collection_docs or [])
        try:
            from JARVIS09_COLLECTOR import collect_for_theme as _j09_kw_collect
            _kw_docs = _j09_kw_collect(keyword, sector)
            if _kw_docs:
                _kw_collection_docs = _kw_docs
                print(f"  🕸️ [JARVIS09] '{keyword}' 재수집: {len(_kw_docs)}건 → 이미지 생성 전달")
            else:
                print(f"  ⚠️ [JARVIS09] '{keyword}' 재수집 0건 — 경제 일반 수집물 유지")
        except Exception as _j09_kw_e:
            print(f"  ⚠️ [JARVIS09] '{keyword}' 재수집 스킵: {_j09_kw_e}")

        from JARVIS02_WRITER.tistory_html_writer import (
            generate_article_html, extract_title, extract_text_content,
            OUTPUT_HTML_DIR, OUTPUT_IMG_DIR,
        )

        html = generate_article_html(keyword, sector, reason, supreme_block, platform="naver",
                                     collection_docs=_kw_collection_docs)
        if not html:
            return {"success": False, "keyword": keyword, "error": "HTML 생성 실패"}

        title = extract_title(html, keyword)
        _kw_hash = __import__('hashlib').md5(f"{keyword}_naver".encode()).hexdigest()[:8]
        _html_dir = OUTPUT_HTML_DIR / f"{_kw_hash}_naver"
        _img_dir = NAVER_IMG_DIR  # ★ 플랫폼별 폴더만 사용 (새 폴더 생성 X)
        _html_dir.mkdir(parents=True, exist_ok=True)
        _img_dir.mkdir(parents=True, exist_ok=True)
        _html_path = str(_html_dir / "article.html")
        __import__('pathlib').Path(_html_path).write_text(html, encoding="utf-8")
        img_dir = str(_img_dir)

        from JARVIS02_WRITER.tistory_html_writer import screenshot_article
        from JARVIS06_IMAGE.injectors import assemble_blocks
        visual_paths = screenshot_article(_html_path, img_dir)
        blocks = assemble_blocks(html, visual_paths, out_dir=img_dir)

        # 썸네일 생성
        from concurrent.futures import ThreadPoolExecutor as _NvTPE
        _nv_thumb_exec = _NvTPE(max_workers=1)
        _nv_thumb_fut = None
        thumb_path = None
        try:
            from JARVIS06_IMAGE.image_agent import generate_thumbnail as _gen_thumb
            _nv_thumb_fut = _nv_thumb_exec.submit(
                _gen_thumb, title=title, keyword=keyword, sector=sector,
                platform="naver", out_dir=_img_dir,
            )
        except Exception:
            _g_report("writer", Exception(), module=__name__)

        if _nv_thumb_fut:
            try:
                thumb_path = _nv_thumb_fut.result(timeout=300)
                if thumb_path and __import__('pathlib').Path(thumb_path).exists():
                    blocks = [("image", thumb_path)] + blocks
            except Exception:
                pass
            finally:
                _nv_thumb_exec.shutdown(wait=False)

        # 검증
        try:
            from JARVIS02_WRITER.jarvis_main import enforce_text_between_images
            blocks = enforce_text_between_images(blocks, source='NAVER-DRAFT')
        except Exception as _ee:
            print(f"  ⚠️ enforce_text_between_images(naver-draft) 오류 (무시): {_ee}")
            _g_report("writer", _ee, module=__name__)

        try:
            from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
            blocks, _nv_v = enforce_supreme_law(blocks, "naver", "Naver-경제글")
            notify_violations(_nv_v, "naver", "Naver-경제글")
        except Exception:
            _g_report("writer", Exception(), module=__name__)

        print(f"  ✅ [NAVER-DRAFT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "title": title,
            "content": extract_text_content(html),
            "html": html,
            "blocks": blocks,
            "visual_paths": visual_paths,
            # ★ 2-2: 작성에 실제 쓴 주제 특화 corpus 를 검증 source_docs 로 노출.
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-DRAFT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


def nv_publish(draft: dict, ts_keyword: str = '') -> dict:
    """네이버 대본 발행 (⑧ 단계)."""
    if not draft.get('success'):
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}

    try:
        from JARVIS08_PUBLISH.platforms import post_to_naver
        print(f"  📤 [NAVER-PUB] 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']

        try:
            from JARVIS02_WRITER.law_enforcer import enforce_supreme_law, notify_violations
            blocks, _nv_v = enforce_supreme_law(blocks, "naver", "Naver-경제글-발행")
            notify_violations(_nv_v, "naver", "Naver-경제글-발행")
        except Exception:
            pass

        result = post_to_naver(
            title=draft['title'],
            html_content=draft['content'],
            blocks=blocks,
            category=ECONOMIC_CATEGORY,
        )

        if result:
            _tg(f"✅ [NAVER-TREND] 발행 완료!\n제목: {draft['title']}\n키워드: {keyword}")
            print(f"  ✅ [NAVER-PUB] 완료")
            return {"success": True, "url": "", "keyword": keyword}
        else:
            _tg(f"❌ [NAVER-TREND] 발행 실패")
            return {"success": False, "url": "", "keyword": keyword}

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-PUB] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "url": "", "keyword": draft.get('keyword', '')}
