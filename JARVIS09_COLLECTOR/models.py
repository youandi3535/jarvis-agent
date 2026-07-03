"""JARVIS09_COLLECTOR/models.py — 수집 데이터 모델.

★ 사용자 박제 2026-06-07 — delta-aware 교류 프로토콜 지원:
   CollectionResult 에 `content_hash`(SHA1) + `fetched_at`(epoch sec) 추가.
   호출자(예: JARVIS06)가 이미 가진 hash 목록을 제외하고 신규/갱신분만
   수령할 수 있도록 fingerprint 부여.
"""
from __future__ import annotations
import hashlib
import time as _time_mod
from dataclasses import dataclass, field
from datetime import datetime


def _hash_text(text: str) -> str:
    """SHA1 hex digest (앞 16자) — content fingerprint."""
    return hashlib.sha1((text or "").encode("utf-8", errors="replace")).hexdigest()[:16]


# ★ 출처 신뢰 우선순위 — 단일 진입점 (사용자 박제 2026-07-03 — ADR 013)
#   "논문 > API > 뉴스 > 기사 > 웹. 데이터가 겹치면 이 순서로 하나를 선택.
#    단, 수집은 받을 수 있는 곳 전부에서 다 받는다 — 논문만 받으면 안 된다."
#   이 티어는 *중복·충돌 해소 전용* — 수집 범위 제한에 사용 금지.
SOURCE_TRUST_TIER: dict[str, int] = {
    "academic": 1, "kci": 1,                                   # 논문
    "kosis": 2, "ecos": 2, "dart": 2, "krx": 2, "finance": 2,  # 공식 데이터 API
    "naver_news": 3, "news": 3,                                # 뉴스
    "kor_econ": 4, "web_data": 4,                              # 기사·전문지
    "web": 5,                                                  # 웹
    "blog": 6,                                                 # 블로그
}


def trust_rank(source_type: str) -> int:
    """출처 신뢰 순위 (낮을수록 신뢰 높음). 미지 소스는 웹 수준(5)."""
    return SOURCE_TRUST_TIER.get((source_type or "").strip().lower(), 5)


@dataclass
class RawDocument:
    """수집 직후 원본 문서."""
    url: str
    source_type: str          # blog | news | academic | finance | web
    raw_html: str = ""
    raw_text: str = ""
    title: str = ""
    published_at: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extra: dict = field(default_factory=dict)


@dataclass
class CollectionResult:
    """정제 완료 결과 — JARVIS02 WRITER 전달용.

    ★ delta 교류 필드 (사용자 박제 2026-06-07):
        content_hash: SHA1(cleaned_text)[:16] — 내용 동일성 판정
        fetched_at:   epoch seconds — 신선도 비교
    """
    theme: str
    source_type: str
    url: str
    title: str
    cleaned_text: str         # 잡음 제거된 원본 텍스트 (요약 아님)
    word_count: int = 0
    collected_at: str = field(default_factory=lambda: datetime.now().isoformat())
    meta: dict = field(default_factory=dict)
    content_hash: str = ""    # ★ 자동 계산 — __post_init__ 처리
    fetched_at: float = field(default_factory=_time_mod.time)

    def __post_init__(self) -> None:
        # content_hash 미지정 시 cleaned_text + title + url 조합으로 자동 산출.
        if not self.content_hash:
            seed = f"{self.url}|{self.title}|{self.cleaned_text}"
            self.content_hash = _hash_text(seed)
