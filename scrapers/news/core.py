"""Model data dan utilitas bersama untuk scraper berita multi-portal."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Optional

# Nama bulan Indonesia -> nomor bulan.
MONTHS = {
    "januari": 1, "februari": 2, "maret": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "agustus": 8, "september": 9, "oktober": 10, "november": 11,
    "desember": 12,
}

_JUNK = re.compile(
    r"(baca juga|advertisement|iklan|scroll to|oleh\s*:|ikuti|simak|baca:\s|"
    r"bagikan|share|komentar|baca selengkapnya|artikel ini telah)",
    re.I,
)


def parse_date_text(text: Optional[str]) -> Optional[date]:
    """Parse teks tanggal Indonesia seperti "12 November 2026"."""
    text = (text or "").strip().lower()
    m = re.match(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", text)
    if not m:
        return None
    day, month = int(m.group(1)), MONTHS.get(m.group(2))
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, day)
    except ValueError:
        return None


def parse_iso(text: Optional[str]) -> Optional[date]:
    """Parse ISO datetime/date seperti "2026-11-12T15:29:21+07:00"."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def clean_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


@dataclass
class Article:
    """Satu artikel berita lengkap."""

    portal: str
    title: str
    url: str
    date_iso: str = ""
    display_date: str = ""
    category: str = ""
    image: str = ""
    snippet: str = ""
    content: str = ""          # isi lengkap (paragraf digabung)
    paragraphs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Portal:
    """Konfigurasi satu portal berita."""

    key: str
    label: str
    base: str
    kind: str                       # "search" | "tag" | "rss"
    article_re: re.Pattern
    body_selectors: list[str]
    dateline: Optional[re.Pattern] = None
    title_suffix: Optional[re.Pattern] = None
    list_selector: Optional[str] = None
    search_url: Optional[str] = None     # template
    tag_page: Optional[str] = None       # format "?page={n}" / "/{n}"
    feeds: Optional[list[str]] = None
    light: bool = True             # blokir semua subresource (halaman yang menggantung)
    cloudflare: bool = False        # perlu penyelesaian tantangan Cloudflare


def strip_title_suffix(title: str, portal: Portal) -> str:
    title = clean_ws(title)
    if portal.title_suffix:
        title = re.sub(portal.title_suffix, "", title).strip()
    return title


def clean_paragraph(text: str, portal: Portal) -> str:
    text = clean_ws(text)
    if portal.dateline:
        text = re.sub(portal.dateline, "", text).strip()
    text = re.sub(r"^[-–—]+\s+", "", text).strip()
    return text
