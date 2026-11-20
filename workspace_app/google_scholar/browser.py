"""Lapisan tipis di atas browser stealth Scrapling.

Dipakai oleh scraper profil dan pencarian kata kunci agar pesan kesalahan
konsisten dan sesi browser selalu ditutup dengan rapi.
"""
from __future__ import annotations

from contextlib import contextmanager
from shutil import which

from .errors import BrowserUnavailableError

INSTALL_HINT = (
    "Browser stealth tidak bisa dijalankan. Jalankan "
    '`pip install "scrapling[fetchers]"` lalu `scrapling install` '
    "di environment yang sama."
)


def browser_hint(exc: Exception) -> str:
    """Ubah exception browser menjadi pesan yang bisa ditindaklanjuti."""
    message = str(exc)
    lowered = message.lower()
    if any(
        token in lowered
        for token in ("executable", "playwright", "chromium", "browser", "install")
    ):
        return f"{INSTALL_HINT} Detail: {type(exc).__name__}"
    return f"Browser stealth gagal dijalankan: {type(exc).__name__}: {message[:280]}"


@contextmanager
def browser_session(timeout: int = 60, retries: int = 1):
    """Buka ``StealthySession`` dan pastikan selalu ditutup.

    ``timeout`` dalam detik; Scrapling menerima milidetik.
    """
    try:
        from scrapling.fetchers import StealthySession
    except Exception as exc:  # pragma: no cover - bergantung environment
        raise BrowserUnavailableError(INSTALL_HINT) from exc

    manager = None
    try:
        manager = StealthySession(
            headless=True,
            executable_path=which("chromium"),
            timeout=int(timeout) * 1000,
            network_idle=True,
            retries=max(0, int(retries)),
        )
        session = manager.__enter__()
    except Exception as exc:  # pragma: no cover - bergantung environment
        if manager is not None:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass
        raise BrowserUnavailableError(browser_hint(exc)) from exc
    try:
        yield session
    finally:
        try:
            manager.__exit__(None, None, None)
        except Exception:  # pragma: no cover - pembersihan browser
            pass
