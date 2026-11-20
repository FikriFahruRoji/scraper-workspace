# Adapted from ScholarScraper for nOCturnal workspace jobs and dataset exports.
"""Konversi hasil scraping menjadi tabel dan berkas unduhan."""
from __future__ import annotations


import pandas as pd

from .models import ScholarProfile, ScholarSearch

ARTICLE_COLUMNS = [
    "No",
    "Judul",
    "Penulis",
    "Venue",
    "Tahun",
    "Sitasi",
    "Tautan Artikel",
    "Tautan Sitasi",
    "ID Publikasi",
]

SEARCH_COLUMNS = [
    "No",
    "Judul",
    "Penulis",
    "Venue",
    "Penerbit",
    "Tahun",
    "Sitasi",
    "Tautan Artikel",
    "Tautan PDF",
    "Tautan Sitasi",
    "Tautan Cluster",
    "Ringkasan",
    "ID Publikasi",
]

def articles_frame(profile: ScholarProfile) -> pd.DataFrame:
    """Data artikel dengan kolom berbahasa Indonesia untuk UI dan ekspor."""
    rows = [
        {
            "No": article.index,
            "Judul": article.title,
            "Penulis": article.authors,
            "Venue": article.venue,
            "Tahun": article.year,
            "Sitasi": article.citations,
            "Tautan Artikel": article.article_url,
            "Tautan Sitasi": article.citations_url,
            "ID Publikasi": article.publication_id,
        }
        for article in profile.articles
    ]
    frame = pd.DataFrame(rows, columns=ARTICLE_COLUMNS)
    for column in ("No", "Tahun", "Sitasi"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame


def search_frame(search: ScholarSearch) -> pd.DataFrame:
    """Data hasil pencarian kata kunci untuk UI dan ekspor."""
    rows = [
        {
            "No": result.index,
            "Judul": result.title,
            "Penulis": result.authors,
            "Venue": result.venue,
            "Penerbit": result.publisher,
            "Tahun": result.year,
            "Sitasi": result.citations,
            "Tautan Artikel": result.article_url,
            "Tautan PDF": result.pdf_url,
            "Tautan Sitasi": result.citations_url,
            "Tautan Cluster": result.cluster_url,
            "Ringkasan": result.snippet,
            "ID Publikasi": result.publication_id,
        }
        for result in search.results
    ]
    frame = pd.DataFrame(rows, columns=SEARCH_COLUMNS)
    for column in ("No", "Tahun", "Sitasi"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame



def history_frame(profile: ScholarProfile) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"Tahun": item.year, "Sitasi": item.citations}
            for item in profile.citation_history
        ],
        columns=["Tahun", "Sitasi"],
    )
    for column in ("Tahun", "Sitasi"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame


def profile_frame(profile: ScholarProfile) -> pd.DataFrame:
    rows: list[tuple[str, object]] = [
        ("Nama", profile.name),
        ("ID Scholar", profile.scholar_id),
        ("Afiliasi", profile.affiliation or "—"),
        ("Email terverifikasi", profile.email or "—"),
        ("Homepage", profile.homepage or "—"),
        ("Minat riset", ", ".join(profile.interests) if profile.interests else "—"),
        ("Sitasi (all-time)", profile.metrics.citations),
        ("h-index (all-time)", profile.metrics.h_index),
        ("i10-index (all-time)", profile.metrics.i10_index),
        (
            f"Sitasi (sejak {profile.recent_since_year})"
            if profile.recent_since_year
            else "Sitasi (5 tahun terakhir)",
            profile.recent_metrics.citations,
        ),
        (
            f"h-index (sejak {profile.recent_since_year})"
            if profile.recent_since_year
            else "h-index (5 tahun terakhir)",
            profile.recent_metrics.h_index,
        ),
        (
            f"i10-index (sejak {profile.recent_since_year})"
            if profile.recent_since_year
            else "i10-index (5 tahun terakhir)",
            profile.recent_metrics.i10_index,
        ),
        ("Artikel terbaca", len(profile.articles)),
        ("Artikel lain tersedia", "Ya" if profile.has_more else "Tidak"),
        ("Urutan daftar", profile.sort),
        ("Halaman diambil", profile.pages_fetched),
        ("Metode", "Browser stealth" if profile.engine == "browser" else "HTTP"),
        (
            "Waktu pengambilan (UTC)",
            profile.fetched_at.strftime("%Y-%m-%d %H:%M:%S")
            if profile.fetched_at
            else "—",
        ),
        ("URL profil", profile.source_url),
    ]
    return pd.DataFrame(rows, columns=["Atribut", "Nilai"])


def search_info_frame(search: ScholarSearch) -> pd.DataFrame:
    """Ringkasan parameter dan status satu pencarian kata kunci."""
    if search.year_from and search.year_to:
        year_label = f"{search.year_from}–{search.year_to}"
    elif search.year_from:
        year_label = f"Sejak {search.year_from}"
    elif search.year_to:
        year_label = f"Sampai {search.year_to}"
    else:
        year_label = "Semua tahun"

    if search.max_results is None:
        if search.safety_cap is None:
            limit_label = "Tidak dibatasi"
        else:
            limit_label = (
                f"Semua halaman (maks. {int(search.safety_cap):,} hasil)"
                .replace(",", ".")
            )
    else:
        limit_label = f"{search.max_results} hasil"

    rows: list[tuple[str, object]] = [
        ("Kata kunci", search.query),
        ("Batas hasil", limit_label),
        ("Perkiraan total hasil", search.total_label or "—"),
        ("Hasil terbaca", len(search.results)),
        ("Halaman diambil", search.pages_fetched),
        ("Urutan", "Tanggal terbaru" if search.sort == "date" else "Relevansi"),
        ("Bahasa halaman", search.hl),
        ("Rentang tahun", year_label),
        ("Metode", "Browser stealth" if search.engine == "browser" else "HTTP"),
        ("Masih ada hasil lain", "Ya" if search.has_more else "Tidak"),
        (
            "Waktu pengambilan (UTC)",
            search.fetched_at.strftime("%Y-%m-%d %H:%M:%S")
            if search.fetched_at
            else "—",
        ),
        ("URL pencarian", search.source_url),
    ]
    return pd.DataFrame(rows, columns=["Atribut", "Nilai"])
