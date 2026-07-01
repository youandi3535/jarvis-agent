"""JARVIS09_COLLECTOR/providers — 수집 프로바이더 패키지."""
from __future__ import annotations
from abc import ABC, abstractmethod
from ..models import RawDocument


class BaseProvider(ABC):
    """모든 프로바이더 공통 인터페이스."""
    source_type: str = "unknown"

    @abstractmethod
    def collect(self, theme: str, sector: str = "", max_items: int = 5) -> list[RawDocument]:
        """주제 관련 문서 수집. 실패 시 빈 리스트 반환."""
        ...


from .blog_provider import BlogProvider
from .news_provider import NewsProvider
from .academic_provider import AcademicProvider
from .finance_provider import FinanceProvider
from .web_provider import WebProvider
from .kor_econ_provider import KorEconProvider
from .naver_news_provider import NaverNewsProvider
from .dart_provider import DartProvider
from .ecos_provider import EcosProvider
from .kosis_provider import KosisProvider
from .krx_provider import KrxProvider
from .kci_provider import KciProvider

__all__ = [
    "BaseProvider",
    "BlogProvider", "NewsProvider", "AcademicProvider",
    "FinanceProvider", "WebProvider", "KorEconProvider",
    "NaverNewsProvider", "DartProvider", "EcosProvider",
    "KosisProvider", "KrxProvider", "KciProvider",
]
