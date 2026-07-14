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

from JARVIS03_RADAR.topic_pack import _ECON_SECTORS  # SSOT: topic_pack.py 단일 진입점
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
        from shared.notify import send_tg
        send_tg(msg, parse_mode="HTML")
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 썸네일 이미지 생성 (matplotlib)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _img_dir(platform: str):
    """플랫폼별 이미지 저장 디렉터리."""
    if platform == 'naver':
        return NAVER_IMG_DIR
    return TISTORY_IMG_DIR




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
#  콘텐츠 차트 — JARVIS06_IMAGE draft_processor 위임
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━



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



# 섹션 이미지 + 단락 이미지 경로 임시 저장 (generate → run 간 전달)
_section_img_paths: dict[int, str] = {}
_para_img_paths:    dict[int, str] = {}




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
            body_text=content[:3000],   # ★ 전체 대본 기반 썸네일 (사용자 박제 2026-07-03)
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
                body_text=content[:3000],   # ★ 전체 대본 기반 썸네일 (사용자 박제 2026-07-03)
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

def _market_data_to_datasets(market_data: dict) -> list:
    """경제 브리핑 전용: market_data → CollectedData datasets 변환.

    collect_chart_data 가 빈 결과일 때 시장 지표를 이미지 파이프라인에 공급.
    market_data 구조: {"market": {지표명: {value, change, as_of}}, "calendar": [...]}
    """
    import hashlib as _hl
    market = (market_data or {}).get("market") or {}
    if not market:
        return []

    _INDICES = ["코스피", "코스닥", "S&P500", "NASDAQ", "DOW"]
    _FX_COMMO = ["달러/원", "금", "유가(WTI)"]
    _RATES = ["미국채10년"]

    def _make_ds(title, keys, unit, src_name, src_url):
        rows = [(k, market[k]) for k in keys if k in market]
        if not rows:
            return None
        as_of = max((v.get("as_of") or "") for _, v in rows)
        data = [{"label": k, "value": v.get("value", 0), "change_pct": v.get("change", 0)}
                for k, v in rows]
        fp = _hl.md5(f"{title}{as_of}".encode()).hexdigest()[:12]
        return {"title": title, "viz_hint": "kpi_cards", "unit": unit,
                "data": data,
                "source": {"provider": "yfinance", "name": src_name,
                           "url": src_url, "as_of": as_of},
                "fingerprint": fp}

    return [ds for ds in [
        _make_ds("주요 증시 지표", _INDICES, "pt", "Yahoo Finance", "https://finance.yahoo.com"),
        _make_ds("환율·원자재", _FX_COMMO, "", "Yahoo Finance", "https://finance.yahoo.com"),
        _make_ds("금리 지표", _RATES, "%", "Yahoo Finance", "https://finance.yahoo.com"),
    ] if ds]


def ts_collect(nv_keyword: str = '', supreme_block=None, market_data: dict | None = None) -> dict:
    """티스토리 주제선정 + JARVIS09 수집 + CollectedData 조립.

    Returns: success, keyword, sector, reason, collected (CollectedData),
             supreme_block (enriched), source_docs
    """
    from datetime import datetime as _dt_ts
    print(f"\n  🔴 [TISTORY-COLLECT] 주제 선정 + 수집 중... [{_dt_ts.now().strftime('%H:%M:%S')}]")

    keyword = ""
    try:
        import os as _os
        # ★ 주제+데이터 단일 공급원 = 자비스03 topic_pack (사용자 박제 2026-07-03)
        from JARVIS03_RADAR.topic_pack import (
            pick_candidate as _tp_pick, build_topic_pack as _tp_build,
            build_for_keyword as _tp_for_kw,
        )
        _force = _os.environ.get("JARVIS_FORCE_TOPIC", "").strip()
        if _force:
            _cand = _tp_for_kw(
                _force,
                sector=_os.environ.get("JARVIS_FORCE_SECTOR", "").strip() or "강제 지정",
                reason=_os.environ.get("JARVIS_FORCE_REASON", "").strip() or "사용자 강제 주제",
            )
            print(f"  📌 [강제 주제 — 티스토리] {_force}")
        else:
            _cand = _tp_pick(exclude_keyword=nv_keyword)
            if _cand is None:
                # ★ ERRORS [404]: 팩이 publish_slots(=2)개만 박제(ERRORS [384])되므로
                #   fit 후보가 1개뿐이면 네이버가 선점한 뒤 재빌드해도 동일 1개만
                #   재생산돼 티스토리가 영구 소진. 소진 복구 재빌드만 max_candidates
                #   를 넓혀 더 깊은 후보 풀에서 fit 대안을 찾는다(평시 프로파일링
                #   비용은 그대로 — ERRORS [384] 원칙 유지, 소진 시에만 예외).
                print("  🎁 [topic_pack] 당일 팩 없음/소진 — 자비스03 파이프라인 즉석 실행(확장 재탐색)")
                _tp_build(max_candidates=8)
                _cand = _tp_pick(exclude_keyword=nv_keyword)
            if _cand is None:
                return {"success": False, "keyword": "",
                        "error": "자비스03 주제 패키지 없음 (트렌드·적합 후보·LLM 확인)"}
        keyword = _cand.get('keyword', '')
        sector = _cand.get('sector', '')
        _profile = _cand.get('profile') or {}
        reason = _profile.get('summary') or _cand.get('reason', '')
        print(f"  📌 [티스토리 주제 — 자비스03 팩] [{sector}] {keyword}"
              + (f" — {reason[:60]}" if reason else ""))

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass
        _rel_terms = ", ".join(_profile.get('related_terms') or [])
        if reason:
            supreme_block = (supreme_block or "") + (
                f"\n\n[주제 프로필 — 자비스03]\n- 주제: {keyword} ({sector})\n- 정의: {reason}"
                + (f"\n- 관련어: {_rel_terms}" if _rel_terms else ""))

        # ★ JARVIS09 직접 수집 (topic_pack = 키워드+프로필만 — 선수집 없음)
        _pool: list = []
        _ev_pack: dict = {}
        _kw_collection_docs: list = []
        try:
            from JARVIS09_COLLECTOR import collect_research, collect_chart_data
            try:
                from shared.pipeline_activity import mark_active
                mark_active(["e1", "e2"])  # J03→J09 선수집 요청, J09→J02 데이터 전달
            except Exception:
                pass
            print(f"  🕸️ [JARVIS09] '{keyword}' 수집 시작...")
            _chart = collect_chart_data(keyword, sector=sector, description=reason,
                                        synonyms=_cand.get("synonyms")) or {}
            _pool = list(_chart.get("datasets") or [])
            _res = collect_research(keyword, sector=sector, angle=reason) or {}
            _kw_collection_docs = list(_res.get("docs") or [])
            # ★ 02가 fact 추출 (09는 원시 수집만 — 단순 수집기 재설계 2026-07-06)
            from JARVIS09_COLLECTOR.evidence_pack import build_evidence_pack as _bep, facts_to_datasets as _f2d
            _ev_pack = _bep(keyword, _res.get("plan") or {}, _kw_collection_docs) or {}
            # ★ facts → datasets 변환 후 _pool에 병합 (text 수치도 차트化 — 이미지 개수 확대)
            try:
                _fact_ds = _f2d(_ev_pack)
                if _fact_ds:
                    _existing_titles = {d.get("title", "") for d in _pool}
                    _new_ds = [d for d in _fact_ds if d.get("title", "") not in _existing_titles]
                    _pool = _pool + _new_ds
                    print(f"  📊 [facts→datasets] {len(_new_ds)}개 수치 데이터셋 추가 (총 {len(_pool)}개)")
            except Exception as _f2d_e:
                print(f"  ⚠️ [facts→datasets] 변환 스킵: {_f2d_e}")
            print(f"  🕸️ [JARVIS09] '{keyword}' 수집 완료: 문서 {len(_kw_collection_docs)}건, "
                  f"데이터셋 {len(_pool)}개")
        except Exception as _je:
            print(f"  ⚠️ [JARVIS09] 수집 실패: {_je}")

        # ★ 경제 브리핑 전용: 차트 데이터 없으면 시장 지표로 폴백
        if not _pool and market_data:
            _pool = _market_data_to_datasets(market_data)
            if _pool:
                print(f"  🔄 [시장지표 폴백] 차트 데이터 없음 → 시장 지표 {len(_pool)}개 datasets 생성")

        # 데이터 카탈로그 주입
        try:
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 데이터 주입 스킵: {_de}")

        # 근거 브리프 주입
        try:
            from JARVIS09_COLLECTOR.evidence_pack import evidence_brief
            _brief = evidence_brief(_ev_pack)
            if _brief:
                supreme_block = (supreme_block or "") + "\n\n" + _brief
                print(f"  📚 [근거 브리프] fact {len(_ev_pack.get('facts', []))}개 "
                      f"→ 대본 프롬프트 직접 주입")
        except Exception as _ebe:
            print(f"  ⚠️ [근거 브리프] 주입 스킵: {_ebe}")

        # 수집 자료 전문 주입
        try:
            from JARVIS02_WRITER.draft_writer import build_corpus_block as _bcb
            _corpus = _bcb(_kw_collection_docs)
            if _corpus:
                supreme_block = (supreme_block or "") + "\n\n" + _corpus
                print(f"  📖 [수집 전문] 문서 {len(_kw_collection_docs)}건 "
                      f"→ 대본 프롬프트 전문 주입 (~{len(_corpus) // 1000}K자)")
        except Exception as _cbe:
            print(f"  ⚠️ [수집 전문] 주입 스킵: {_cbe}")

        # CollectedData 조립
        from JARVIS09_COLLECTOR.models import CollectedData
        try:
            from dataclasses import asdict as _asdict
            _docs_ser = [_asdict(d) if hasattr(d, '__dataclass_fields__') else d
                         for d in _kw_collection_docs]
        except Exception:
            _docs_ser = []
        collected = CollectedData.from_dict({
            "meta": {"keyword": keyword, "sector": sector, "category": "economic",
                     "profile": _cand.get("profile") or {}},
            "datasets": _pool,
            "docs": _docs_ser,
            "facts": list(_ev_pack.get("facts") or []),
            "entities": [],
        })

        print(f"  ✅ [TISTORY-COLLECT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "sector": sector,
            "reason": reason,
            "collected": collected,
            "supreme_block": supreme_block,
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [TISTORY-COLLECT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


def ts_generate_draft(keyword: str, sector: str, reason: str,
                      collected, supreme_block=None,
                      gate_feedback: list | None = None,
                      source_docs: list | None = None) -> dict:
    """티스토리 Pass-1 대본 생성 + JARVIS06 이미지 파이프라인.

    ts_collect() 결과를 받아 대본 생성 단계만 담당.
    """
    from datetime import datetime as _dt_ts
    print(f"\n  🔴 [TISTORY-DRAFT] 대본 생성 중... [{_dt_ts.now().strftime('%H:%M:%S')}]")
    _section_img_paths.clear()
    _para_img_paths.clear()
    _cleanup_tistory_images()

    try:
        from JARVIS02_WRITER.tistory_html_writer import generate_article_html, extract_text_content
        from JARVIS06_IMAGE.draft_processor import process_draft

        # Pass-1-only 대본(placeholder) → process_draft 단일 이미지 경로
        _ref_ds_ts = getattr(collected, "datasets", None) or []
        draft_html = generate_article_html(keyword, sector, reason, supreme_block,
                                           ref_datasets=_ref_ds_ts,
                                           gate_feedback=gate_feedback, pass2=False)
        if not draft_html:
            return {"success": False, "keyword": keyword, "error": "HTML 생성 실패"}

        result = process_draft(draft_html, collected=collected, platform="tistory",
                               out_dir=TISTORY_IMG_DIR)
        html = result["html"]
        title = result["title"]
        content = extract_text_content(html)
        html_path = result.get("html_path", "")
        img_dir = str(TISTORY_IMG_DIR)
        visual_paths = []
        blocks = result["blocks"]  # J06 이 썸네일 prepend + 법률집행 완료

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
            "source_docs": source_docs or [],
            "collected": collected,
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
        from JARVIS06_IMAGE.draft_processor import publish_assembled
        from JARVIS08_PUBLISH.platforms import post_to_tistory
        print(f"  📤 [TISTORY-PUB] J06→J08 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']
        html = draft['html']

        def _pub_fn(blocks, title, **_kw):
            return post_to_tistory(
                title=title,
                html_content=draft['content'],
                blocks=blocks,
                category=ECONOMIC_CATEGORY,
            )

        result = publish_assembled(draft, _pub_fn, "tistory")

        if result:
            # ★ DB 기록 (ERRORS [370]): 성공 발행 → on_post_published_detail 이 posts·post_analysis
            #   *둘 다* 기록 → 대시보드(오늘 발행 글)·Daily Review 자동 동기화. 하네스 경제 흐름은
            #   이 함수를 send 콜백으로 쓰는데 emit 이 누락돼 07-01 이후 발행이 기록 0 이었음.
            try:
                from shared.bus import on_post_published_detail as _emit
                _imgs = [str(b[1]) for b in (blocks or []) if b and b[0] == "image"]
                _emit(theme=keyword, platform="tistory", title=draft['title'],
                      content=draft.get('content', ''), html=html,
                      source_keyword=keyword, post_type="economic", image_paths=_imgs)
                print(f"  ✅ [DB] post_analysis·posts 저장 완료 (이미지 {len(_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류(무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
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


def nv_collect(ts_keyword: str = '', supreme_block=None, market_data: dict | None = None) -> dict:
    """네이버 주제선정 + JARVIS09 수집 + CollectedData 조립.

    Returns: success, keyword, sector, reason, collected (CollectedData),
             supreme_block (enriched), source_docs
    """
    from datetime import datetime as _dt_nv
    print(f"\n  🟢 [NAVER-COLLECT] 주제 선정 + 수집 중... [{_dt_nv.now().strftime('%H:%M:%S')}]")

    keyword = ""
    try:
        import os as _os
        # ★ 주제+데이터 단일 공급원 = 자비스03 topic_pack (사용자 박제 2026-07-03)
        from JARVIS03_RADAR.topic_pack import (
            pick_candidate as _tp_pick, build_topic_pack as _tp_build,
            build_for_keyword as _tp_for_kw,
        )
        _force_nv = _os.environ.get("JARVIS_FORCE_NV_TOPIC", "").strip()
        if _force_nv:
            _cand = _tp_for_kw(
                _force_nv,
                sector=_os.environ.get("JARVIS_FORCE_NV_SECTOR", "").strip() or "강제 지정",
                reason=_os.environ.get("JARVIS_FORCE_NV_REASON", "").strip() or "사용자 강제 주제",
            )
            print(f"  📌 [강제 주제 — 네이버] {_force_nv}")
        else:
            _cand = _tp_pick(exclude_keyword=ts_keyword)
            if _cand is None:
                # ★ ERRORS [404] — ts_collect 와 동일 사유로 소진 복구 재빌드만 확장 재탐색.
                print("  🎁 [topic_pack] 당일 팩 없음/소진 — 자비스03 파이프라인 즉석 실행(확장 재탐색)")
                _tp_build(max_candidates=8)
                _cand = _tp_pick(exclude_keyword=ts_keyword)
            if _cand is None:
                return {"success": False, "keyword": "",
                        "error": "자비스03 주제 패키지 없음 (트렌드·적합 후보·LLM 확인)"}
        keyword = _cand.get('keyword', '')
        sector = _cand.get('sector', '')
        _profile = _cand.get('profile') or {}
        reason = _profile.get('summary') or _cand.get('reason', '')
        print(f"  📌 [네이버 주제 — 자비스03 팩] [{sector}] {keyword}"
              + (f" — {reason[:60]}" if reason else ""))

        if supreme_block is None:
            from JARVIS02_WRITER.law_enforcer import build_writing_rules_block as _law_blk
            supreme_block = _law_blk()
        try:
            from JARVIS02_WRITER.law_enforcer import keyword_frequency_rule as _kw_rule
            supreme_block = (supreme_block or "") + _kw_rule(keyword)
        except Exception:
            pass
        _rel_terms = ", ".join(_profile.get('related_terms') or [])
        if reason:
            supreme_block = (supreme_block or "") + (
                f"\n\n[주제 프로필 — 자비스03]\n- 주제: {keyword} ({sector})\n- 정의: {reason}"
                + (f"\n- 관련어: {_rel_terms}" if _rel_terms else ""))

        # ★ JARVIS09 직접 수집 (topic_pack = 키워드+프로필만 — 선수집 없음)
        _pool: list = []
        _ev_pack: dict = {}
        _kw_collection_docs: list = []
        try:
            from JARVIS09_COLLECTOR import collect_research, collect_chart_data
            try:
                from shared.pipeline_activity import mark_active
                mark_active(["e1", "e2"])  # J03→J09 선수집 요청, J09→J02 데이터 전달
            except Exception:
                pass
            print(f"  🕸️ [JARVIS09] '{keyword}' 수집 시작...")
            _chart = collect_chart_data(keyword, sector=sector, description=reason,
                                        synonyms=_cand.get("synonyms")) or {}
            _pool = list(_chart.get("datasets") or [])
            _res = collect_research(keyword, sector=sector, angle=reason) or {}
            _kw_collection_docs = list(_res.get("docs") or [])
            # ★ 02가 fact 추출 (09는 원시 수집만 — 단순 수집기 재설계 2026-07-06)
            from JARVIS09_COLLECTOR.evidence_pack import build_evidence_pack as _bep, facts_to_datasets as _f2d
            _ev_pack = _bep(keyword, _res.get("plan") or {}, _kw_collection_docs) or {}
            # ★ facts → datasets 변환 후 _pool에 병합 (text 수치도 차트化 — 이미지 개수 확대)
            try:
                _fact_ds = _f2d(_ev_pack)
                if _fact_ds:
                    _existing_titles = {d.get("title", "") for d in _pool}
                    _new_ds = [d for d in _fact_ds if d.get("title", "") not in _existing_titles]
                    _pool = _pool + _new_ds
                    print(f"  📊 [facts→datasets] {len(_new_ds)}개 수치 데이터셋 추가 (총 {len(_pool)}개)")
            except Exception as _f2d_e:
                print(f"  ⚠️ [facts→datasets] 변환 스킵: {_f2d_e}")
            print(f"  🕸️ [JARVIS09] '{keyword}' 수집 완료: 문서 {len(_kw_collection_docs)}건, "
                  f"데이터셋 {len(_pool)}개")
        except Exception as _je:
            print(f"  ⚠️ [JARVIS09] 수집 실패: {_je}")

        # ★ 경제 브리핑 전용: 차트 데이터 없으면 시장 지표로 폴백
        if not _pool and market_data:
            _pool = _market_data_to_datasets(market_data)
            if _pool:
                print(f"  🔄 [시장지표 폴백] 차트 데이터 없음 → 시장 지표 {len(_pool)}개 datasets 생성")

        # 데이터 카탈로그 주입
        try:
            from JARVIS02_WRITER.draft_writer import _build_data_catalog as _bdc
            if _pool:
                supreme_block = (supreme_block or "") + "\n\n" + _bdc(_pool)
                print(f"  🗂️ [데이터-우선] 실데이터 {len(_pool)}개 → 카탈로그 주입")
            else:
                print("  ⚠️ [데이터-우선] 실데이터 0 — 차트는 AI사진 대체(거짓차트 금지)")
        except Exception as _de:
            print(f"  ⚠️ [데이터-우선] 데이터 주입 스킵: {_de}")

        # 근거 브리프 주입
        try:
            from JARVIS09_COLLECTOR.evidence_pack import evidence_brief
            _brief = evidence_brief(_ev_pack)
            if _brief:
                supreme_block = (supreme_block or "") + "\n\n" + _brief
                print(f"  📚 [근거 브리프] fact {len(_ev_pack.get('facts', []))}개 "
                      f"→ 대본 프롬프트 직접 주입")
        except Exception as _ebe:
            print(f"  ⚠️ [근거 브리프] 주입 스킵: {_ebe}")

        # 수집 자료 전문 주입
        try:
            from JARVIS02_WRITER.draft_writer import build_corpus_block as _bcb
            _corpus = _bcb(_kw_collection_docs)
            if _corpus:
                supreme_block = (supreme_block or "") + "\n\n" + _corpus
                print(f"  📖 [수집 전문] 문서 {len(_kw_collection_docs)}건 "
                      f"→ 대본 프롬프트 전문 주입 (~{len(_corpus) // 1000}K자)")
        except Exception as _cbe:
            print(f"  ⚠️ [수집 전문] 주입 스킵: {_cbe}")

        # CollectedData 조립
        from JARVIS09_COLLECTOR.models import CollectedData
        try:
            from dataclasses import asdict as _asdict
            _docs_ser = [_asdict(d) if hasattr(d, '__dataclass_fields__') else d
                         for d in _kw_collection_docs]
        except Exception:
            _docs_ser = []
        collected = CollectedData.from_dict({
            "meta": {"keyword": keyword, "sector": sector, "category": "economic",
                     "profile": _cand.get("profile") or {}},
            "datasets": _pool,
            "docs": _docs_ser,
            "facts": list(_ev_pack.get("facts") or []),
            "entities": [],
        })

        print(f"  ✅ [NAVER-COLLECT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "sector": sector,
            "reason": reason,
            "collected": collected,
            "supreme_block": supreme_block,
            "source_docs": _kw_collection_docs,
        }

    except Exception as e:
        import traceback
        print(f"  ❌ [NAVER-COLLECT] 예외: {e}")
        _g_report("writer", e, module=__name__)
        traceback.print_exc()
        return {"success": False, "keyword": keyword, "error": str(e)[:100]}


def nv_generate_draft(keyword: str, sector: str, reason: str,
                      collected, supreme_block=None,
                      gate_feedback: list | None = None,
                      source_docs: list | None = None) -> dict:
    """네이버 Pass-1 대본 생성 + JARVIS06 이미지 파이프라인.

    nv_collect() 결과를 받아 대본 생성 단계만 담당.
    """
    from datetime import datetime as _dt_nv
    print(f"\n  🟢 [NAVER-DRAFT] 대본 생성 중... [{_dt_nv.now().strftime('%H:%M:%S')}]")
    _section_img_paths.clear()
    _para_img_paths.clear()
    _cleanup_naver_images()

    try:
        from JARVIS02_WRITER.tistory_html_writer import generate_article_html, extract_text_content
        from JARVIS06_IMAGE.draft_processor import process_draft

        # Pass-1-only 대본(placeholder) → process_draft 단일 이미지 경로
        _ref_ds = getattr(collected, "datasets", None) or []
        draft_html = generate_article_html(keyword, sector, reason, supreme_block, platform="naver",
                                           ref_datasets=_ref_ds,
                                           gate_feedback=gate_feedback, pass2=False)
        if not draft_html:
            return {"success": False, "keyword": keyword, "error": "HTML 생성 실패"}

        result = process_draft(draft_html, collected=collected, platform="naver",
                               out_dir=NAVER_IMG_DIR)
        html = result["html"]
        title = result["title"]
        img_dir = str(NAVER_IMG_DIR)
        visual_paths = []
        blocks = result["blocks"]  # J06 이 썸네일 prepend + 법률집행 완료

        print(f"  ✅ [NAVER-DRAFT] 완료: {keyword}")
        return {
            "success": True,
            "keyword": keyword,
            "title": title,
            "content": extract_text_content(html),
            "html": html,
            "blocks": blocks,
            "visual_paths": visual_paths,
            "source_docs": source_docs or [],
            "collected": collected,
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
        from JARVIS06_IMAGE.draft_processor import publish_assembled
        from JARVIS08_PUBLISH.platforms import post_to_naver
        print(f"  📤 [NAVER-PUB] J06→J08 발행 중...")
        keyword = draft['keyword']
        blocks = draft['blocks']

        def _pub_fn(blocks, title, **_kw):
            return post_to_naver(
                title=title,
                html_content=draft['content'],
                blocks=blocks,
                category=ECONOMIC_CATEGORY,
            )

        result = publish_assembled(draft, _pub_fn, "naver")

        if result:
            # ★ DB 기록 (ERRORS [370]): 성공 발행 → posts·post_analysis 둘 다 기록 → 대시보드 동기화
            try:
                from shared.bus import on_post_published_detail as _emit
                _imgs = [str(b[1]) for b in (blocks or []) if b and b[0] == "image"]
                _emit(theme=keyword, platform="naver", title=draft['title'],
                      content=draft.get('content', ''), html=draft.get('html', ''),
                      source_keyword=keyword, post_type="economic", image_paths=_imgs)
                print(f"  ✅ [DB] post_analysis·posts 저장 완료 (이미지 {len(_imgs)}개)")
            except Exception as _dbe:
                print(f"  ⚠️ [DB] 저장 오류(무시): {_dbe}")
                _g_report("writer", _dbe, module=__name__)
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
