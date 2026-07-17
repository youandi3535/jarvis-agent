"""DART 전자공시 프로바이더 — OpenDART REST API (DART_API_KEY 필요).

수집 대상:
  - 테마 관련 기업 최근 공시 (사업보고서·반기·주요사항·발행공시)
  - 기업별 공시 제목·날짜 요약

API: https://opendart.fss.or.kr/api/

★ 근본 결함 대응 (실측 확증):
  DART list.json 은 corp_name 검색을 지원하지 않는다. corp_name 을 넘겨도
  DART 가 무시하고 해당 공시유형의 '최근 전체 공시'를 반환한다('라면'·'삼성전자'
  ·'없는이름xyz' 가 모두 동일한 무관 공시 10건 반환). 따라서 기업명을 반드시
  corpCode.xml 로 corp_code 로 변환한 뒤 corp_code 기반으로만 조회한다.
"""
from __future__ import annotations
import os
import io
import json
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from ..models import RawDocument
from ..rate_limiter import wait_for
from . import BaseProvider

import logging
log = logging.getLogger("jarvis.collector.dart")

_BASE = "https://opendart.fss.or.kr/api"

# ── corpCode 리졸버 캐시 설정 ──────────────────────────────────────────────
#   corpCode.xml 은 118,508행·3.5MB 대용량이므로 매 수집마다 재다운로드 금지.
#   ① 모듈레벨 메모리 캐시(_CORP_MAP_MEM) — 프로세스 1회 로드 후 재사용
#   ② 디스크 캐시(output/dart_corpcode.json) — mtime 기준 7일 TTL
_CORP_CODE_URL = f"{_BASE}/corpCode.xml"
_CACHE_DIR = Path(__file__).resolve().parent.parent / "output"
_CACHE_FILE = _CACHE_DIR / "dart_corpcode.json"
_CACHE_TTL_SEC = 7 * 24 * 3600  # 7일
_CORP_MAP_MEM: dict[str, list[tuple[str, str]]] | None = None  # 메모리 캐시

# ── 거시·일반 경제 키워드 차단 리스트 (기업명 검색 오염 방지) ──────────────────
#   '경제'·'금리' 같은 거시/산업 키워드를 corp_name(기업명) 검색어로 쓰면
#   전혀 무관한 기업공시(포스코DX·일성건설·와이즈에이아이 등)가 유입돼 API 티어
#   캡을 잠식하고 ECOS 거시지표·KRX 시세를 밀어낸다. theme 이 이 목록에 걸리면
#   공시 수집을 즉시 스킵한다. (확장 가능 — 이 상단 상수만 추가)
#   ★ corpCode 다운로드(무거움)를 피하는 저비용 사전 게이트로 존치. 실질 게이트는
#     이제 corp_code 검증(_resolve_corp_code).
_NON_CORP_KEYWORDS = frozenset({
    "경제", "금리", "기준금리", "물가", "소비자물가", "환율", "증시",
    "코스피", "코스닥", "부동산", "고용", "실업", "실업률", "무역",
    "수출", "수입", "성장률", "gdp", "인플레이션", "디플레이션", "경기",
    "경상수지", "무역수지",
})


def _is_macro_keyword(theme: str) -> bool:
    """거시·일반 경제 키워드면 True — 기업명(corp_name) 검색 대상이 아님.

    빈 문자열도 True(검색 무의미). 소문자·공백 정규화 후 부분일치로 판정.
    'HD현대일렉트릭' 같은 실제 종목명은 걸리지 않아 정상 수집된다.
    """
    t = (theme or "").strip().lower()
    if not t:
        return True
    return any(kw in t for kw in _NON_CORP_KEYWORDS)


def _normalize_name(name: str) -> str:
    """기업명 정규화 — 공백 전부 제거 + 소문자. corpCode map 정확일치 키."""
    return "".join((name or "").split()).lower()


def _corp_code_map(api_key: str) -> dict[str, list[tuple[str, str]]]:
    """DART corpCode.xml → {정규화기업명: [(corp_code, stock_code), ...]}.

    118,508행·3.5MB 대용량이라 메모리 캐시 + 디스크 캐시(7일 TTL) 필수.
    매 수집마다 재다운로드 절대 금지. 키 없거나 네트워크·파싱 실패 시 빈 dict
    (수집이 죽지 않도록 안전 폴백).
    """
    global _CORP_MAP_MEM
    # ① 메모리 캐시 — 프로세스 내 1회 로드 후 재사용
    if _CORP_MAP_MEM is not None:
        return _CORP_MAP_MEM
    if not api_key:
        return {}

    # ② 디스크 캐시 — 파일 mtime 기준 TTL 내이면 로드
    try:
        if _CACHE_FILE.exists():
            age = time.time() - _CACHE_FILE.stat().st_mtime
            if age < _CACHE_TTL_SEC:
                with open(_CACHE_FILE, "r", encoding="utf-8") as fp:
                    raw = json.load(fp)
                # json 은 tuple 을 list 로 저장 → tuple 로 복원
                _CORP_MAP_MEM = {k: [tuple(v) for v in vs] for k, vs in raw.items()}
                log.info(f"[DART] corpCode 디스크 캐시 로드 — {len(_CORP_MAP_MEM)}개 기업명")
                return _CORP_MAP_MEM
    except Exception as e:
        log.debug(f"[DART] corpCode 캐시 로드 실패(재다운로드로 진행): {e}")

    # ③ 다운로드(ZIP 바이너리) → CORPCODE.xml 추출 → 파싱
    result: dict[str, list[tuple[str, str]]] = {}
    try:
        wait_for(_CORP_CODE_URL)
        resp = httpx.get(_CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=30)
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = next((n for n in zf.namelist() if n.lower().endswith(".xml")), None)
            if not xml_name:
                log.warning("[DART] corpCode ZIP 안에 xml 파일 없음 — 빈 map 반환")
                return {}
            xml_bytes = zf.read(xml_name)
        root = ET.fromstring(xml_bytes)
        for item in root.iter("list"):
            corp_code  = (item.findtext("corp_code") or "").strip()
            corp_name  = (item.findtext("corp_name") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            if not corp_code or not corp_name:
                continue
            key = _normalize_name(corp_name)
            if not key:
                continue
            result.setdefault(key, []).append((corp_code, stock_code))
        log.info(f"[DART] corpCode 다운로드·파싱 완료 — {len(result)}개 기업명")
    except Exception as e:
        log.warning(f"[DART] corpCode 다운로드/파싱 실패 — 빈 map 반환: {e}")
        return {}

    if not result:
        # 파싱 결과가 비면 캐시하지 않음(다음 호출에서 재시도 여지)
        return {}

    # ④ 디스크 캐시 저장 + 메모리 캐시 확정
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as fp:
            json.dump(result, fp, ensure_ascii=False)
    except Exception as e:
        log.debug(f"[DART] corpCode 캐시 저장 실패: {e}")
    _CORP_MAP_MEM = result
    return result


def _resolve_corp_code(name: str, api_key: str | None = None) -> str | None:
    """기업명 → corp_code (★ 정확일치만, 부분일치 금지).

    여러 후보면 stock_code 있는 상장사 우선. 매칭 없으면 None.
    api_key 미지정 시 환경변수에서 로드(모듈 함수 단독 호출·검증 지원).
    '라면'·'신라면' 처럼 등록 기업명이 아니면 None → DART 전체공시 유입 차단.
    """
    if api_key is None:
        api_key = os.getenv("DART_API_KEY", "")
    key = _normalize_name(name)
    if not key:
        return None
    cmap = _corp_code_map(api_key)
    candidates = cmap.get(key)
    if not candidates:
        return None
    # 상장사(stock_code 존재) 우선 — 동명 비상장 다수 대비 과매칭 차단
    listed = [c for c in candidates if c[1]]
    chosen = listed[0] if listed else candidates[0]
    return chosen[0]


class DartProvider(BaseProvider):
    """DART 전자공시 — OpenDART REST API."""
    source_type = "dart"

    def __init__(self):
        self._api_key = os.getenv("DART_API_KEY", "")

    @property
    def _available(self) -> bool:
        return bool(self._api_key)

    def _search_filings(self, corp_code: str, days: int = 180,
                        ptype: str = "A") -> list[dict]:
        """특정 기업(corp_code) 최근 공시 목록.

        ★ corp_code 가 없으면 즉시 [] — corp_name 검색은 DART 가 무시하고
          전체공시를 뱉는 고장 상태라 corp_code 기반만 신뢰(근본 결함 차단).
        """
        if not corp_code:
            return []
        end_de   = datetime.now().strftime("%Y%m%d")
        bgn_de   = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        url = f"{_BASE}/list.json"
        try:
            wait_for(url)
            resp = httpx.get(url, params={
                "crtfc_key":  self._api_key,
                "corp_code":  corp_code,
                "bgn_de":     bgn_de,
                "end_de":     end_de,
                "pblntf_ty":  ptype,
                "page_count": "10",
                "sort":       "date",
                "sort_mthd":  "desc",
            }, timeout=10)
            data = resp.json()
            if data.get("status") == "000":
                return data.get("list", [])
        except Exception as e:
            log.debug(f"[DART] corp_code={corp_code} 공시 조회 실패: {e}")
        return []

    def _fetch_company_info(self, corp_code: str) -> dict:
        """기업 기본 정보 조회 — 대표자·상장구분·소재지·설립일 (DART company.json)."""
        try:
            wait_for(f"{_BASE}/company.json")
            resp = httpx.get(f"{_BASE}/company.json", params={
                "crtfc_key": self._api_key,
                "corp_code": corp_code,
            }, timeout=8)
            data = resp.json()
            if data.get("status") == "000":
                return data
        except Exception as e:
            log.debug(f"[DART] company.json 실패 ({corp_code}): {e}")
        return {}

    def collect(self, theme: str, sector: str = "", max_items: int = 10) -> list[RawDocument]:
        # ★ 거시·일반 키워드는 기업명 검색 오염원 — 조기 스킵 (corpCode 다운로드 회피)
        if _is_macro_keyword(theme):
            log.info(f"[dart] 거시/일반 키워드 '{theme}' — 기업공시 수집 스킵")
            return []
        if not self._available:
            log.warning("[DART] DART_API_KEY 없음 — (https://opendart.fss.or.kr 무료 발급)")
            return []

        results: list[RawDocument] = []

        # 테마에서 기업명 후보 추출 (단어 분리)
        # "삼성전자 반도체" → ["삼성전자", "삼성", "반도체"]
        theme_words = []
        for w in theme.split():
            if len(w) >= 2:
                theme_words.append(w)
        # 원본 테마도 추가 (2글자 이상)
        if len(theme) >= 2 and theme not in theme_words:
            theme_words.insert(0, theme)

        # ★ 각 검색 단어 → corp_code 정확일치 해석 (부분일치 금지·상장우선)
        #   해석된 것만 조회. 하나도 해석 안 되면 전체공시 유입 원천 차단 → []
        resolved: list[tuple[str, str]] = []  # (word, corp_code)
        seen_codes: set[str] = set()
        for word in theme_words[:4]:
            code = _resolve_corp_code(word, self._api_key)
            if code and code not in seen_codes:
                seen_codes.add(code)
                resolved.append((word, code))

        if not resolved:
            # '라면'·'신라면' 등 실제 등록 기업명이 아니면 corp_code 미해석 → 스킵
            log.info(f"[DART] '{theme}' — corp_code 해석 실패(기업 아님) → 공시 수집 스킵")
            return []

        # 해석된 corp_code 로만 DART 검색 (중복 rcept 제거)
        seen_rcept = set()
        all_filings: list[dict] = []
        for word, corp_code in resolved:
            # A(정기공시) + B(주요사항) 두 유형 검색
            for ptype in ("A", "B"):
                filings = self._search_filings(corp_code, days=90, ptype=ptype)
                for f in filings:
                    rcept = f.get("rcept_no", "")
                    if rcept and rcept not in seen_rcept:
                        seen_rcept.add(rcept)
                        all_filings.append(f)
            if len(all_filings) >= 20:
                break

        if not all_filings:
            log.info(f"[DART] '{theme}' 관련 공시 없음")
            return []

        # 기업별로 그룹핑해서 요약 문서 생성
        corp_map: dict[str, list[dict]] = {}
        for f in all_filings:
            corp = f.get("corp_name", "미상")
            corp_map.setdefault(corp, []).append(f)

        _cls_label = {"Y": "코스피(유가증권)", "K": "코스닥", "N": "코넥스", "E": "기타"}

        # 기업 개요 병렬 prefetch (company.json — LLM 호출 0, 순수 HTTP)
        target_corps = list(corp_map.items())[:max_items]
        corp_codes = {
            filings[0].get("corp_code", ""): corp_name
            for corp_name, filings in target_corps
            if filings[0].get("corp_code", "")
        }
        company_info_cache: dict[str, dict] = {}
        if corp_codes:
            with ThreadPoolExecutor(max_workers=min(len(corp_codes), 8)) as pool:
                future_to_code = {
                    pool.submit(self._fetch_company_info, code): code
                    for code in corp_codes
                }
                for future in as_completed(future_to_code):
                    code = future_to_code[future]
                    try:
                        info = future.result()
                        if info:
                            company_info_cache[code] = info
                    except Exception as e:
                        log.debug(f"[DART] company.json 병렬 실패 ({code}): {e}")
            log.info(f"[DART] company.json 병렬 fetch 완료 — {len(company_info_cache)}/{len(corp_codes)}건")

        for corp_name, filings in target_corps:
            lines = [f"[{corp_name} 전자공시 — {theme}]", "[최근 공시]"]
            for f in filings[:5]:
                rdate = f.get("rcept_dt", "")
                title = f.get("report_nm", "")
                if rdate and title:
                    lines.append(f"• [{rdate}] {title}")

            corp_code = filings[0].get("corp_code", "")

            # 기업 개요 — 병렬 prefetch 결과 사용
            info = company_info_cache.get(corp_code, {})
            if info:
                lines.append("")
                lines.append("[기업 개요]")
                if info.get("ceo_nm"):
                    lines.append(f"• 대표자: {info['ceo_nm']}")
                if info.get("corp_cls"):
                    lines.append(f"• 시장: {_cls_label.get(info['corp_cls'], info['corp_cls'])}")
                if info.get("adres"):
                    lines.append(f"• 소재지: {info['adres']}")
                est = info.get("est_dt", "")
                if est and len(est) == 8:
                    lines.append(f"• 설립일: {est[:4]}년 {int(est[4:6])}월 {int(est[6:])}일")
                if info.get("hm_url"):
                    lines.append(f"• 홈페이지: {info['hm_url']}")

            if len(lines) > 2:
                results.append(RawDocument(
                    url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={filings[0].get('rcept_no','')}",
                    source_type=self.source_type,
                    raw_text="\n".join(lines),
                    title=f"{corp_name} 전자공시",
                    extra={"theme": theme, "source": "dart", "corp": corp_name,
                           "corp_code": corp_code},
                ))

        log.info(f"[DART] '{theme}' 공시 {len(all_filings)}건 → {len(results)}개 기업 정리 완료")
        return results
