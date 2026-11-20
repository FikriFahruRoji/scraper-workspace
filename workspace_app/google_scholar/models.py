"""Model data hasil scraping profil Google Scholar."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Metrics:
    """Tiga metrik utama Google Scholar (nilai all-time atau 5 tahun terakhir)."""

    citations: int | None = None
    h_index: int | None = None
    i10_index: int | None = None


@dataclass(frozen=True)
class ProfileHeader:
    """Bagian identitas yang tampil di kepala halaman profil."""

    name: str
    affiliation: str = ""
    email: str = ""
    homepage: str = ""
    interests: tuple[str, ...] = ()


@dataclass(frozen=True)
class Article:
    """Satu baris publikasi pada daftar artikel profil."""

    index: int
    title: str
    authors: str = ""
    venue: str = ""
    year: int | None = None
    citations: int | None = None
    article_url: str = ""
    citations_url: str = ""
    publication_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "authors": self.authors,
            "venue": self.venue,
            "year": self.year,
            "citations": self.citations,
            "article_url": self.article_url,
            "citations_url": self.citations_url,
            "publication_id": self.publication_id,
        }


@dataclass(frozen=True)
class CitationYear:
    """Jumlah sitasi baru pada satu tahun menurut grafik profil."""

    year: int
    citations: int

    def to_dict(self) -> dict[str, int]:
        return {"year": self.year, "citations": self.citations}


@dataclass(frozen=True)
class SearchResult:
    """Satu hasil pencarian artikel di halaman ``/scholar``."""

    index: int
    title: str
    authors: str = ""
    venue: str = ""
    publisher: str = ""
    year: int | None = None
    citations: int | None = None
    article_url: str = ""
    pdf_url: str = ""
    citations_url: str = ""
    cluster_url: str = ""
    snippet: str = ""
    publication_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "authors": self.authors,
            "venue": self.venue,
            "publisher": self.publisher,
            "year": self.year,
            "citations": self.citations,
            "article_url": self.article_url,
            "pdf_url": self.pdf_url,
            "citations_url": self.citations_url,
            "cluster_url": self.cluster_url,
            "snippet": self.snippet,
            "publication_id": self.publication_id,
        }


@dataclass(frozen=True)
class ScholarSearch:
    """Snapshot satu pencarian kata kunci beserta hasil yang terbaca."""

    query: str
    results: tuple[SearchResult, ...] = ()
    total_label: str = ""
    source_url: str = ""
    request_url: str = ""
    fetched_at: datetime | None = None
    engine: str = "browser"
    hl: str = "en"
    sort: str = "relevance"
    year_from: int | None = None
    year_to: int | None = None
    pages_fetched: int = 0
    has_more: bool = False
    max_results: int | None = None
    safety_cap: int | None = 1000

    @property
    def total_estimate(self) -> int | None:
        """Perkiraan jumlah total hasil dari label Scholar, bila ada.

        Label dapat berbentuk ``About 5.460.000 results (0,09 sec)`` atau
        ``Sekitar 1.230 hasil``. Bagian durasi dan satuan dibuang agar angka
        detik tidak ikut terbaca.
        """
        head = re.split(
            r"\bresults?\b|\bhasil\b|\(", self.total_label, maxsplit=1, flags=re.IGNORECASE
        )[0]
        digits = "".join(char for char in head if char.isdigit())
        return int(digits) if digits else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "max_results": self.max_results,
            "safety_cap": self.safety_cap,
            "results": [result.to_dict() for result in self.results],
            "total_label": self.total_label,
            "total_estimate": self.total_estimate,
            "source_url": self.source_url,
            "request_url": self.request_url,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "engine": self.engine,
            "hl": self.hl,
            "sort": self.sort,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "pages_fetched": self.pages_fetched,
            "has_more": self.has_more,
            "result_count": len(self.results),
        }


@dataclass(frozen=True)
class ScholarProfile:
    """Snapshot lengkap satu profil beserta artikel yang berhasil dibaca."""

    scholar_id: str
    name: str
    affiliation: str = ""
    email: str = ""
    homepage: str = ""
    interests: tuple[str, ...] = ()
    metrics: Metrics = Metrics()
    recent_metrics: Metrics = Metrics()
    recent_since_year: int | None = None
    citation_history: tuple[CitationYear, ...] = ()
    articles: tuple[Article, ...] = ()
    source_url: str = ""
    request_url: str = ""
    fetched_at: datetime | None = None
    engine: str = "http"
    sort: str = "citations"
    pages_fetched: int = 1
    has_more: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "scholar_id": self.scholar_id,
            "name": self.name,
            "affiliation": self.affiliation,
            "email": self.email,
            "homepage": self.homepage,
            "interests": list(self.interests),
            "metrics": {
                "citations": self.metrics.citations,
                "h_index": self.metrics.h_index,
                "i10_index": self.metrics.i10_index,
            },
            "recent_metrics": {
                "citations": self.recent_metrics.citations,
                "h_index": self.recent_metrics.h_index,
                "i10_index": self.recent_metrics.i10_index,
                "since_year": self.recent_since_year,
            },
            "citation_history": [item.to_dict() for item in self.citation_history],
            "articles": [article.to_dict() for article in self.articles],
            "source_url": self.source_url,
            "request_url": self.request_url,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "engine": self.engine,
            "sort": self.sort,
            "pages_fetched": self.pages_fetched,
            "has_more": self.has_more,
            "article_count": len(self.articles),
        }
