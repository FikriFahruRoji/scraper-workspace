"""Sesi Chromium Scrapling yang dipakai ulang untuk semua portal."""

from shutil import which

from scrapling.fetchers import AsyncStealthySession


def open_session(
    *,
    headless: bool = True,
    solve_cloudflare: bool = False,
    timeout: int = 15000,
    disable_resources: bool = True,
    max_pages: int = 4,
) -> AsyncStealthySession:
    return AsyncStealthySession(
        executable_path=which("chromium"),
        headless=headless,
        load_dom=False,
        network_idle=False,
        disable_resources=disable_resources,
        solve_cloudflare=solve_cloudflare,
        timeout=timeout,
        retries=1,
        max_pages=max_pages,
    )
