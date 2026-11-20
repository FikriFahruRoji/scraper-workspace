# Adapted from ScholarScraper for nOCturnal workspace jobs and dataset exports.
"""Pengambilan profil Google Scholar memakai Scrapling."""
from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from scrapling.fetchers import FetcherSession

from .browser import browser_session
from .errors import (
    AccessBlockedError,
    BrowserUnavailableError,
    ProfileNotFoundError,
    ScraperError,
)
from .models import Article, CitationYear, Metrics, ScholarProfile
from .parser import (
    SORT_VALUES,
    article_range_label,
    build_profile_url,
    build_share_url,
    clean_text,
    detect_block,
    extract_scholar_id,
    is_login_redirect,
    page_has_more,
    parse_articles,
    parse_citation_history,
    parse_header,
    parse_metrics,
)

ProgressCallback = Callable[[str, float | None], None]

PAGE_SIZE = 100
MAX_SAFETY_CAP = 2000
ENGINES = {"auto", "http", "browser"}


@dataclass(frozen=True)
class ScrapeOptions:
    """Pengaturan satu kali pengambilan profil."""

    hl: str = "en"
    sort: str = "citations"
    max_articles: int | None = 100
    allow_pagination: bool = False
    engine: str = "auto"
    delay: float = 2.0
    timeout: int = 30
    retries: int = 1
    safety_cap: int = MAX_SAFETY_CAP


class ScholarScraper:
    """Ambil profil publik dan daftar artikelnya.

    Default hanya meminta satu halaman (maksimal 100 artikel) karena
    robots.txt Google Scholar melarang parameter ``cstart``. Pagination harus
    diaktifkan secara eksplisit oleh pemanggil.
    """

    def __init__(self, options: ScrapeOptions | None = None, *, on_page=None, sleep=time.sleep):
        self.on_page = on_page
        self.sleep = sleep
        self.options = options or ScrapeOptions()
        self._validate_options(self.options)

    @staticmethod
    def _validate_options(options: ScrapeOptions) -> None:
        if options.engine not in ENGINES:
            raise ValueError(f"engine harus salah satu dari {sorted(ENGINES)}")
        if options.sort not in SORT_VALUES:
            raise ValueError(f"sort harus salah satu dari {sorted(SORT_VALUES)}")
        if not 5 <= int(options.timeout) <= 180:
            raise ValueError("timeout harus antara 5 dan 180 detik")
        if not 0 <= float(options.delay) <= 60:
            raise ValueError("delay harus antara 0 dan 60 detik")
        if options.max_articles is not None and options.max_articles < 1:
            raise ValueError("max_articles harus >= 1 atau None untuk semua")
        if (
            options.max_articles is not None
            and options.max_articles > PAGE_SIZE
            and not options.allow_pagination
        ):
            raise ValueError(
                "max_articles > 100 memerlukan allow_pagination=True karena "
                "robots.txt Google Scholar melarang parameter cstart."
            )

    def scrape(
        self,
        profile: str,
        on_progress: ProgressCallback | None = None,
    ) -> ScholarProfile:
        """Ambil profil dari URL/ID. Melempar ``ScraperError`` jika gagal."""
        scholar_id = extract_scholar_id(profile)
        if self.options.engine == "auto":
            try:
                return self._scrape_with("http", scholar_id, on_progress)
            except AccessBlockedError as http_error:
                self._notify(
                    on_progress,
                    "HTTP diblokir; mencoba browser stealth sekali...",
                    0.1,
                )
                try:
                    return self._scrape_with("browser", scholar_id, on_progress)
                except BrowserUnavailableError as browser_error:
                    raise AccessBlockedError(
                        f"{http_error} Fallback browser juga tidak tersedia: {browser_error}"
                    ) from browser_error
        return self._scrape_with(self.options.engine, scholar_id, on_progress)

    def _scrape_with(
        self,
        engine: str,
        scholar_id: str,
        on_progress: ProgressCallback | None,
    ) -> ScholarProfile:
        if engine == "browser":
            with browser_session(self.options.timeout, retries=1) as session:
                return self._paginate(session, engine, scholar_id, on_progress)
        self._notify(on_progress, "Menyiapkan sesi HTTP...", 0.05)
        with FetcherSession(
            impersonate="chrome",
            timeout=self.options.timeout,
            retries=0,
            stealthy_headers=True,
        ) as session:
            return self._paginate(session, engine, scholar_id, on_progress)

    def _paginate(
        self,
        session,
        engine: str,
        scholar_id: str,
        on_progress: ProgressCallback | None,
    ) -> ScholarProfile:
        options = self.options
        limit = options.max_articles
        if limit is None:
            limit = options.safety_cap
        if not options.allow_pagination:
            limit = min(limit, PAGE_SIZE)

        self._notify(
            on_progress,
            f"Meminta halaman profil {scholar_id} lewat {engine.upper()}...",
            0.05,
        )

        articles: list[Article] = []
        seen: set[str] = set()
        header = None
        metrics = Metrics()
        recent_metrics = Metrics()
        recent_since = None
        history: tuple[CitationYear, ...] = ()
        pages_fetched = 0
        cstart = 0
        last_page_count = 0
        more_available = False

        while True:
            url = build_profile_url(
                scholar_id,
                options.hl,
                sort=options.sort,
                cstart=cstart or None,
                pagesize=PAGE_SIZE,
            )
            page = self._get(session, engine, url)

            if pages_fetched == 0:
                header = parse_header(page)
                if not header.name:
                    raise ProfileNotFoundError(
                        "Nama profil tidak ditemukan. Profil mungkin privat, "
                        "ID salah, atau halaman diblokir."
                    )
                metrics, recent_metrics, recent_since = parse_metrics(page)
                history = parse_citation_history(page)

            page_articles = parse_articles(page, start_index=len(articles) + 1)
            previous_count = len(articles)
            for article in page_articles:
                key = (
                    article.publication_id
                    or article.article_url
                    or f"{article.title.casefold()}|{article.year}"
                )
                if key in seen:
                    continue
                seen.add(key)
                articles.append(replace(article, index=len(articles) + 1))

            pages_fetched += 1
            last_page_count = len(page_articles)
            more_available = page_has_more(page)
            snapshot = ScholarProfile(
                scholar_id=scholar_id,
                name=header.name,
                affiliation=header.affiliation,
                email=header.email,
                homepage=header.homepage,
                interests=header.interests,
                metrics=metrics,
                recent_metrics=recent_metrics,
                recent_since_year=recent_since,
                citation_history=history,
                articles=tuple(articles[:limit]),
                source_url=build_share_url(scholar_id, hl=options.hl),
                request_url=build_profile_url(scholar_id, hl=options.hl, sort=options.sort),
                fetched_at=datetime.now(timezone.utc),
                engine=engine,
                sort=options.sort,
                pages_fetched=pages_fetched,
                has_more=more_available or len(articles) > limit,
            )
            if self.on_page:
                self.on_page(snapshot)
            label = article_range_label(page) or f"Halaman {pages_fetched}"
            self._notify(
                on_progress,
                f"{label}: {len(page_articles)} artikel dibaca "
                f"(total unik {len(articles)}).",
                self._fraction(len(articles), limit),
            )

            if len(articles) == previous_count:
                break
            if len(articles) >= limit:
                break
            if not options.allow_pagination:
                break
            if not more_available:
                break

            # Google kadang mengabaikan pagesize=100 dan mengembalikan 20 baris.
            # Offset berikutnya mengikuti jumlah baris yang benar-benar dibaca.
            cstart += last_page_count if last_page_count > 0 else PAGE_SIZE
            if options.delay:
                self._notify(
                    on_progress,
                    f"Jeda {options.delay:.1f} detik sebelum halaman berikutnya...",
                    self._fraction(len(articles), limit),
                )
                self.sleep(options.delay)

        return snapshot


    def _get(self, session, engine: str, url: str):
        attempts = max(1, 1 + int(self.options.retries))
        last_reason: str | None = None
        for attempt in range(attempts):
            try:
                if engine == "http":
                    page = session.get(url)
                else:
                    page = session.fetch(url)
            except Exception as exc:
                if attempt + 1 < attempts:
                    self.sleep(min(2 ** attempt, 5))
                    continue
                raise ScraperError(
                    f"Gagal mengambil halaman profil: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                ) from exc

            reason = detect_block(
                getattr(page, "status", None),
                str(getattr(page, "url", "") or ""),
                getattr(page, "html_content", "") or "",
                str(page.get_all_text()) if hasattr(page, "get_all_text") else "",
            )
            if reason:
                last_reason = reason
                if reason == "HTTP 429" or reason.startswith("HTTP 5"):
                    if attempt + 1 < attempts:
                        self.sleep(3 * (attempt + 1))
                        continue
                raise AccessBlockedError(self._block_message(reason))

            if is_login_redirect(str(getattr(page, "url", "") or "")):
                raise ProfileNotFoundError(
                    "Profil dialihkan ke halaman masuk Google. Pastikan profil "
                    "publik dan URL/ID benar."
                )
            return page

        raise AccessBlockedError(self._block_message(last_reason or "tidak diketahui"))

    @staticmethod
    def _block_message(reason: str) -> str:
        return (
            f"Google Scholar menolak permintaan ({reason}). Tunggu beberapa menit, "
            "coba mode Browser stealth, atau ganti jaringan. Hindari menekan "
            "tombol berulang kali."
        )

    def _fraction(self, count: int, limit: int) -> float | None:
        if self.options.max_articles is None:
            return None
        return min(0.99, max(0.05, count / max(1, limit)))

    @staticmethod
    def _notify(
        callback: ProgressCallback | None, message: str, fraction: float | None
    ) -> None:
        if callback is not None:
            callback(clean_text(message), fraction)
