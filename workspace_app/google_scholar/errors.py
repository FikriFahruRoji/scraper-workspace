"""Kesalahan yang bisa ditampilkan langsung kepada pengguna."""
from __future__ import annotations


class ScraperError(RuntimeError):
    """Kesalahan umum saat mengambil atau membaca profil Scholar."""


class InvalidProfileError(ScraperError, ValueError):
    """Input bukan URL/ID profil Google Scholar yang valid."""


class ProfileNotFoundError(ScraperError):
    """Profil tidak ditemukan, privat, atau dialihkan ke halaman masuk Google."""


class AccessBlockedError(ScraperError):
    """Google Scholar menolak permintaan (CAPTCHA, 403/429, atau halaman /sorry)."""


class BrowserUnavailableError(ScraperError):
    """Browser stealth tidak bisa dijalankan pada mesin ini."""
