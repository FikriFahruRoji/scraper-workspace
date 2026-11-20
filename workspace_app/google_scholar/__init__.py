"""Google Scholar scraper berbasis Scrapling."""
from .errors import (
    AccessBlockedError,
    BrowserUnavailableError,
    InvalidProfileError,
    ProfileNotFoundError,
    ScraperError,
)
from .fetcher import ScholarScraper, ScrapeOptions
from .models import (
    Article,
    CitationYear,
    Metrics,
    ScholarProfile,
    ScholarSearch,
    SearchResult,
)
from .parser import build_profile_url, extract_scholar_id
from .search import ScholarSearcher, SearchOptions, build_search_url

__all__ = [
    "AccessBlockedError",
    "Article",
    "BrowserUnavailableError",
    "CitationYear",
    "InvalidProfileError",
    "Metrics",
    "ProfileNotFoundError",
    "ScholarProfile",
    "ScholarScraper",
    "ScholarSearcher",
    "ScholarSearch",
    "ScrapeOptions",
    "ScraperError",
    "SearchOptions",
    "SearchResult",
    "build_profile_url",
    "build_search_url",
    "extract_scholar_id",
]

__version__ = "1.3.0"
