# Adapted from ScholarScraper for nOCturnal workspace jobs and dataset exports.
"""Pencarian artikel Google Scholar berdasarkan kata kunci.

Modul ini memisahkan tiga lapisan:

* ``build_search_url`` — menyusun URL ``/scholar`` dari input yang tervalidasi.
* ``parse_search_page`` — membaca HTML hasil pencarian tanpa jaringan.
* ``ScholarSearcher`` — mengambil satu atau beberapa halaman hasil.

Pencarian ``/scholar`` jauh lebih ketat daripada halaman profil. Permintaan
HTTP biasa biasanya dialihkan ke halaman verifikasi Google (``sgsync``),
sehingga browser stealth menjadi mode default.
"""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import ceil
from typing import Callable
from urllib.parse import urlencode, urljoin

from scrapling.fetchers import FetcherSession

from .browser import browser_session
from .errors import AccessBlockedError, BrowserUnavailableError, ScraperError
from .models import ScholarSearch, SearchResult
from .parser import (
    SCHOLAR_HOST,
    SCHOLAR_SCHEME,
    YEAR_RE,
    clean_text,
    detect_block,
    parse_int,
)

SEARCH_BASE = f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/scholar"
RESULTS_PER_PAGE = 10
# Google Scholar sendiri berhenti memberikan hasil setelah sekitar 1.000 hasil.
MAX_RESULTS_CAP = 1000
SEARCH_SORTS = {"relevance": None, "date": "date"}
ENGINES = {"auto", "http", "browser"}

# Mode jeda per halaman. ``None`` berarti memakai nilai ``delay`` manual.
SAFE_DELAY_PROFILES: dict[str, tuple[float, float] | None] = {
    "safe": (5.0, 12.0),
    "very_safe": (12.0, 25.0),
    "fixed": None,
}

TITLE_BADGE_RE = re.compile(
    r"^\s*(?:\[(?:PDF|HTML|BOOK|B|C|CITATION|DOC|DOCX|PS|RTF|TXT|XML)\]\s*)+",
    re.IGNORECASE,
)
CITED_BY_RE = re.compile(
    r"(?:cited\s+by|dirujuk|disitasi(?:\s+oleh)?|dikutip(?:\s+oleh)?)\s+([\d.,\s]+)",
    re.IGNORECASE,
)
NO_RESULTS_RE = re.compile(
    r"did\s+not\s+match\s+any\s+articles|tidak\s+(?:cocok|sesuai)\s+dengan\s+artikel\s+apa\s*pun",
    re.IGNORECASE,
)

ProgressCallback = Callable[[str, float | None], None]


@dataclass(frozen=True)
class SearchOptions:
    """Pengaturan satu kali pencarian kata kunci."""

    hl: str = "en"
    max_results: int | None = 20
    engine: str = "browser"
    delay_mode: str = "safe"
    delay: float = 2.0
    year_from: int | None = None
    year_to: int | None = None
    sort: str = "relevance"
    timeout: int = 60
    retries: int = 1
    safety_cap: int | None = MAX_RESULTS_CAP


def _absolute(href: str) -> str:
    """Ubah tautan relatif Scholar menjadi URL absolut yang aman."""
    raw = (href or "").strip()
    if not raw or raw.startswith(("#", "javascript:")):
        return ""
    if raw.startswith(("http://", "https://")):
        return raw
    return urljoin(f"{SCHOLAR_SCHEME}://{SCHOLAR_HOST}/", raw)


def node_text(node) -> str:
    """Ambil teks elemen tanpa kehilangan spasi di antara tag inline.

    ``get_all_text(separator="")`` menghapus spasi pada teks yang terpisah tag
    ``<b>``, sehingga frasa seperti ``machine learning education`` bisa menjadi
    ``machinelearningeducation``. Pemisah spasi dipakai, lalu spasi sebelum
    tanda baca dirapikan kembali.
    """
    raw = node.get_all_text(separator=" ", strip=False)
    text = clean_text(raw)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return clean_text(text)


def build_search_url(
    query: str,
    hl: str = "en",
    *,
    start: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    sort: str = "relevance",
) -> str:
    """Susun URL pencarian Scholar dari kata kunci dan filter.

    Hanya parameter yang dikenal Google Scholar yang dipakai, sehingga input
    pengguna tidak bisa mengubah host tujuan request.
    """
    keyword = clean_text(query)
    if not keyword:
        raise ValueError("Kata kunci pencarian wajib diisi.")
    if len(keyword) > 500:
        raise ValueError("Kata kunci terlalu panjang (maksimal 500 karakter).")
    if sort not in SEARCH_SORTS:
        raise ValueError(f"sort harus salah satu dari {sorted(SEARCH_SORTS)}")
    if year_from is not None and not 1500 <= int(year_from) <= 2200:
        raise ValueError("year_from harus antara 1500 dan 2200.")
    if year_to is not None and not 1500 <= int(year_to) <= 2200:
        raise ValueError("year_to harus antara 1500 dan 2200.")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise ValueError("year_from tidak boleh lebih besar dari year_to.")

    params: dict[str, object] = {"hl": hl, "as_sdt": "0,5", "q": keyword}
    if year_from is not None:
        params["as_ylo"] = int(year_from)
    if year_to is not None:
        params["as_yhi"] = int(year_to)
    if SEARCH_SORTS.get(sort):
        params["scisbd"] = "1"
    if start:
        params["start"] = int(start)
    return f"{SEARCH_BASE}?{urlencode(params)}"


def parse_byline(raw: str) -> tuple[str, str, str, int | None]:
    """Pecah baris ``Penulis - Venue, Tahun - Penerbit``.

    Google Scholar memakai format yang tidak selalu lengkap, misalnya tanpa
    venue atau tanpa penerbit. Fungsi ini mengembalikan
    ``(authors, venue, publisher, year)`` dan tetap masuk akal untuk variasi
    tersebut.
    """
    text = clean_text(raw)
    if not text:
        return "", "", "", None

    parts = [part.strip() for part in text.split(" - ")]
    authors = parts[0] if parts else ""
    middle = ""
    publisher = ""
    if len(parts) >= 3:
        middle = " - ".join(parts[1:-1])
        publisher = parts[-1]
    elif len(parts) == 2:
        candidate = parts[1]
        if YEAR_RE.search(candidate) or not candidate:
            middle = candidate
        else:
            publisher = candidate
    elif len(parts) == 1:
        # Tidak ada pemisah: seluruh teks dianggap venue bila bukan penulis.
        authors = ""
        middle = text

    raw_year = YEAR_RE.search(middle) or YEAR_RE.search(text)
    year = int(raw_year.group(1)) if raw_year else None
    if year is not None and not 1500 <= year <= 2200:
        year = None

    venue = middle
    if year is not None:
        if venue.strip() == str(year):
            venue = ""
        else:
            for suffix in (f", {year}", f"({year})", f" {year}"):
                if venue.endswith(suffix):
                    venue = venue[: -len(suffix)]
                    break
    venue = venue.strip(" ,;")

    # Penerbit yang ternyata hanya berisi tahun bukan penerbit.
    if publisher and YEAR_RE.fullmatch(publisher):
        if not venue:
            venue = publisher
        publisher = ""

    authors = re.sub(r"\s+…", "…", authors.strip(" ,;"))
    return authors, venue, publisher.strip(" ,;"), year


def parse_search_page(page, start_index: int = 1) -> list[SearchResult]:
    """Baca hasil pencarian dari HTML ``/scholar``."""
    results: list[SearchResult] = []
    for row in page.css("div.gs_r.gs_or.gs_scl"):
        if not row.css("div.gs_ri"):
            continue
        title_nodes = row.css("h3.gs_rt")
        if not title_nodes:
            continue
        raw_title = node_text(title_nodes[0])
        title = clean_text(TITLE_BADGE_RE.sub("", raw_title))
        if not title:
            continue

        link_nodes = row.css("h3.gs_rt a")
        article_url = (
            _absolute(link_nodes[0].attrib.get("href", "")) if link_nodes else ""
        )

        byline_nodes = row.css("div.gs_a")
        authors, venue, publisher, year = parse_byline(
            node_text(byline_nodes[0]) if byline_nodes else ""
        )

        citations: int | None = None
        citations_url = ""
        for node in row.css("div.gs_fl a"):
            text = node_text(node)
            match = CITED_BY_RE.search(text)
            if match:
                citations = parse_int(match.group(1)) or 0
                citations_url = _absolute(node.attrib.get("href", ""))
                break

        pdf_url = ""
        for node in row.css("div.gs_ggs a"):
            href = _absolute(node.attrib.get("href", ""))
            if href:
                pdf_url = href
                break

        cluster_url = ""
        for node in row.css("div.gs_fl a"):
            href = _absolute(node.attrib.get("href", ""))
            if "cluster=" in href:
                cluster_url = href
                break

        snippet_nodes = row.css("div.gs_rs")
        snippet = node_text(snippet_nodes[0]) if snippet_nodes else ""

        results.append(
            SearchResult(
                index=start_index + len(results),
                title=title,
                authors=authors,
                venue=venue,
                publisher=publisher,
                year=year,
                citations=citations,
                article_url=article_url,
                pdf_url=pdf_url,
                citations_url=citations_url,
                cluster_url=cluster_url,
                snippet=snippet,
                publication_id=(row.attrib.get("data-cid") or "").strip(),
            )
        )
    return results


def parse_total_label(page) -> str:
    """Label perkiraan jumlah hasil, mis. ``About 5.460.000 results``."""
    nodes = page.css("#gs_ab_md div.gs_ab_mdw")
    if not nodes:
        nodes = page.css("#gs_ab_md")
    if not nodes:
        return ""
    return node_text(nodes[0])


def search_has_more(page) -> bool:
    """True jika tombol/tautan halaman berikutnya masih aktif.

    Label aksesibilitas tombol mengikuti bahasa antarmuka (``Next`` atau
    ``Berikutnya``), jadi kelas tombol dipakai lebih dulu.
    """
    buttons = page.css("#gs_nm button.gs_btnPR")
    if not buttons:
        buttons = page.css('#gs_nm button[aria-label="Next"]')
    if buttons:
        return "disabled" not in buttons[0].attrib
    return bool(page.css("#gs_n a span.gs_ico_nav_next"))


def search_is_empty(page) -> bool:
    text = str(page.get_all_text()) if hasattr(page, "get_all_text") else ""
    return bool(NO_RESULTS_RE.search(text))


def detect_search_block(
    status: int | None, url: str, html: str, text: str = ""
) -> str | None:
    """Deteksi blokir khusus halaman pencarian, termasuk redirect ``sgsync``."""
    reason = detect_block(status, url, html, text)
    if reason:
        return reason

    lowered_url = (url or "").lower()
    if "/sgsync" in lowered_url or "sgsync?" in lowered_url:
        return "halaman verifikasi Google (sgsync)"

    lowered_html = (html or "").lower()
    if "<title>verifying" in lowered_html:
        return "halaman verifikasi Google (sgsync)"
    return None


class ScholarSearcher:
    """Ambil hasil pencarian kata kunci dari Google Scholar."""

    def __init__(self, options: SearchOptions | None = None, *, on_page=None, sleep=time.sleep):
        self.on_page = on_page
        self.sleep = sleep
        self.options = options or SearchOptions()
        self._validate_options(self.options)

    @staticmethod
    def _validate_options(options: SearchOptions) -> None:
        if options.engine not in ENGINES:
            raise ValueError(f"engine harus salah satu dari {sorted(ENGINES)}")
        if options.sort not in SEARCH_SORTS:
            raise ValueError(f"sort harus salah satu dari {sorted(SEARCH_SORTS)}")
        if not 5 <= int(options.timeout) <= 180:
            raise ValueError("timeout harus antara 5 dan 180 detik")
        if not 0 <= float(options.delay) <= 60:
            raise ValueError("delay harus antara 0 dan 60 detik")
        if options.delay_mode not in SAFE_DELAY_PROFILES:
            raise ValueError(
                f"delay_mode harus salah satu dari {sorted(SAFE_DELAY_PROFILES)}"
            )
        if options.safety_cap is not None and int(options.safety_cap) < 1:
            raise ValueError("safety_cap harus >= 1 atau None untuk tanpa batas")
        if options.max_results is not None:
            upper = (
                int(options.safety_cap)
                if options.safety_cap is not None
                else None
            )
            if int(options.max_results) < 1 or (
                upper is not None and int(options.max_results) > upper
            ):
                raise ValueError(
                    "max_results harus >= 1"
                    + (f" dan <= {upper}" if upper is not None else "")
                    + ", atau None untuk semua halaman"
                )
        if options.year_from is not None and options.year_to is not None:
            if int(options.year_from) > int(options.year_to):
                raise ValueError(
                    "Tahun awal tidak boleh lebih besar dari tahun akhir."
                )
        for value in (options.year_from, options.year_to):
            if value is not None and not 1500 <= int(value) <= 2200:
                raise ValueError("Rentang tahun harus antara 1500 dan 2200.")

    def search(
        self,
        query: str,
        on_progress: ProgressCallback | None = None,
    ) -> ScholarSearch:
        """Jalankan pencarian. Melempar ``ScraperError`` jika gagal."""
        keyword = clean_text(query)
        if not keyword:
            raise ValueError("Kata kunci pencarian wajib diisi.")

        if self.options.engine == "auto":
            try:
                return self._search_with("http", keyword, on_progress)
            except AccessBlockedError as http_error:
                self._notify(
                    on_progress,
                    "HTTP dialihkan ke halaman verifikasi; mencoba browser stealth...",
                    0.1,
                )
                try:
                    return self._search_with("browser", keyword, on_progress)
                except BrowserUnavailableError as browser_error:
                    raise AccessBlockedError(
                        f"{http_error} Fallback browser juga tidak tersedia: "
                        f"{browser_error}"
                    ) from browser_error
        return self._search_with(self.options.engine, keyword, on_progress)

    def _search_with(
        self,
        engine: str,
        query: str,
        on_progress: ProgressCallback | None,
    ) -> ScholarSearch:
        if engine == "browser":
            with browser_session(self.options.timeout, retries=1) as session:
                return self._paginate(session, engine, query, on_progress)
        self._notify(on_progress, "Menyiapkan sesi HTTP...", 0.05)
        with FetcherSession(
            impersonate="chrome",
            timeout=self.options.timeout,
            retries=0,
            stealthy_headers=True,
        ) as session:
            return self._paginate(session, engine, query, on_progress)

    def _paginate(
        self,
        session,
        engine: str,
        query: str,
        on_progress: ProgressCallback | None,
    ) -> ScholarSearch:
        options = self.options
        if options.max_results is None:
            limit = options.safety_cap
        else:
            limit = int(options.max_results)
            if options.safety_cap is not None:
                limit = min(limit, int(options.safety_cap))
        pages_needed = (
            ceil(limit / RESULTS_PER_PAGE) if limit is not None else None
        )

        results: list[SearchResult] = []
        seen: set[str] = set()
        total_label = ""
        pages_fetched = 0
        more_available = False
        start = 0
        first_url = ""

        while (limit is None or len(results) < limit) and (
            pages_needed is None or pages_fetched < pages_needed
        ):
            url = build_search_url(
                query,
                options.hl,
                start=start or None,
                year_from=options.year_from,
                year_to=options.year_to,
                sort=options.sort,
            )
            if not first_url:
                first_url = url

            if pages_fetched:
                wait = self._page_wait()
                if wait > 0:
                    prefix = (
                        "Jeda aman"
                        if options.delay_mode != "fixed"
                        else "Jeda"
                    )
                    self._notify(
                        on_progress,
                        f"{prefix} {wait:.1f} detik sebelum halaman berikutnya...",
                        self._fraction(len(results), limit),
                    )
                    self.sleep(wait)

            page = self._get(session, engine, url)
            if pages_fetched == 0:
                total_label = parse_total_label(page)

            page_results = parse_search_page(page, start_index=len(results) + 1)
            if not page_results and not search_is_empty(page):
                raise ScraperError("Halaman pencarian Google Scholar tidak dapat dibaca.")
            added = 0
            for result in page_results:
                key = (
                    result.publication_id
                    or result.article_url
                    or f"{result.title.casefold()}|{result.year}"
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(replace(result, index=len(results) + 1))
                added += 1

            pages_fetched += 1
            more_available = search_has_more(page)
            snapshot = ScholarSearch(
                query=query,
                max_results=options.max_results,
                safety_cap=options.safety_cap,
                results=tuple(results[:limit]),
                total_label=total_label,
                source_url=build_search_url(
                    query,
                    options.hl,
                    year_from=options.year_from,
                    year_to=options.year_to,
                    sort=options.sort,
                ),
                request_url=first_url,
                fetched_at=datetime.now(timezone.utc),
                engine=engine,
                hl=options.hl,
                sort=options.sort,
                year_from=options.year_from,
                year_to=options.year_to,
                pages_fetched=pages_fetched,
                has_more=more_available or (limit is not None and len(results) > limit),
            )
            if self.on_page:
                self.on_page(snapshot)
            self._notify(
                on_progress,
                f"Halaman {pages_fetched}: {len(page_results)} hasil dibaca "
                f"(total unik {len(results)}).",
                self._fraction(len(results), limit),
            )

            if not page_results or added == 0:
                break
            if not more_available:
                break
            start += RESULTS_PER_PAGE

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
                    f"Gagal mengambil halaman pencarian: {type(exc).__name__}: "
                    f"{str(exc)[:300]}"
                ) from exc

            status = getattr(page, "status", None)
            reason = detect_search_block(
                status,
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
            if status is not None and not 200 <= status < 300:
                if status >= 500 and attempt + 1 < attempts:
                    self.sleep(3 * (attempt + 1))
                    continue
                raise ScraperError(f"Gagal mengambil halaman pencarian Google Scholar (HTTP {status}).")
            return page

        raise AccessBlockedError(self._block_message(last_reason or "tidak diketahui"))

    @staticmethod
    def _block_message(reason: str) -> str:
        return (
            f"Google Scholar menolak permintaan pencarian ({reason}). Pencarian "
            "otomatis lebih ketat daripada halaman profil. Tunggu beberapa menit, "
            "gunakan mode Browser stealth, atau ganti jaringan. Hindari menekan "
            "tombol berulang kali."
        )

    def _page_wait(self) -> float:
        """Waktu tunggu sebelum halaman berikutnya.

        Mode aman memakai rentang acak agar pola permintaan tidak terlalu
        seragam, sementara mode manual memakai nilai ``delay``.
        """
        profile = SAFE_DELAY_PROFILES.get(self.options.delay_mode)
        if profile is None:
            return float(self.options.delay)
        return random.uniform(*profile)

    def _fraction(self, count: int, limit: int | None) -> float | None:
        if self.options.max_results is None or limit is None:
            return None
        return min(0.99, max(0.05, count / max(1, limit)))

    @staticmethod
    def _notify(
        callback: ProgressCallback | None, message: str, fraction: float | None
    ) -> None:
        if callback is not None:
            callback(clean_text(message), fraction)
