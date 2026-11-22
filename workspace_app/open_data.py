"""HTTP for public open-data APIs (World Bank, IMF): an honest User-Agent, retries, and plain errors."""
import json
import ssl
import time
import urllib.error
import urllib.request

try:
    import certifi
except ImportError:  # certifi ships with the requirements; the system store is the fallback
    certifi = None

USER_AGENT = "nOCturnalScraper/1.0 (riset lokal; data publik)"
RETRY_CODES = (429, 500, 502, 503, 504)


def _context():
    """Verified TLS using certifi's maintained bundle: some systems lack newer root certificates
    (api.imf.org chains to Sectigo Root R46), and verification must stay on."""
    return ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()


def _refusal(exc):
    """The server's own explanation when it gives one ({"reason": ...}), else the HTTP status."""
    try:
        reason = json.loads(exc.read().decode("utf-8", "replace")).get("reason")
    except (ValueError, AttributeError, OSError):
        reason = None
    return f"Server menolak permintaan: {reason}" if reason else f"Server menjawab {exc.code} ({exc.reason})."


class ApiError(Exception):
    """A failure worth showing to the user as is."""


def get(url, *, accept="application/json", timeout=30, retries=3, pause=1.5, stop=None, log=None):
    """Fetch `url` and return the body bytes. Retries brief server trouble (noting it in `log`), then raises ApiError."""
    for attempt in range(retries):
        if stop is not None and stop.is_set():
            raise ApiError("Dihentikan.")
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=_context()) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_CODES and attempt < retries - 1:
                if log:
                    log(f"  server menjawab {exc.code}, mencoba lagi ({attempt + 2} dari {retries})…")
                time.sleep(pause * (attempt + 1))
                continue
            raise ApiError(_refusal(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retries - 1:
                if log:
                    log(f"  belum ada jawaban dalam {timeout} detik, mencoba lagi ({attempt + 2} dari {retries})…")
                time.sleep(pause * (attempt + 1))
                continue
            raise ApiError("Tidak bisa menghubungi server. Periksa koneksi internet.") from exc
    raise ApiError("Permintaan gagal.")
