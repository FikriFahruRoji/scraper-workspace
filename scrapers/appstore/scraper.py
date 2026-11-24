"""App Store customer reviews from Apple's public RSS feed: no key, login, or browser needed.

Apple serves at most 10 pages of 50 reviews (500 newest or most helpful) per app and country, and the
feed carries no developer replies. Names and ratings are public; the feed has no review dates older than
the page limit, so keep collecting on a schedule and merge to build a longer history.
"""
import json
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from workspace_app.open_data import ApiError, get

FEED = "https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby={sort}/json"
LOOKUP = "https://itunes.apple.com/lookup?id={app_id}&country={country}"
PAGE_SIZE = 50
MAX_PAGES = 10
MAX_REVIEWS = PAGE_SIZE * MAX_PAGES
SORTS = {"terbaru": "mostrecent", "membantu": "mosthelpful"}
_ID_RE = re.compile(r"^\d{5,12}$")
_URL_ID_RE = re.compile(r"/id(\d{5,12})")


def normalize_app_input(value):
    """Accept a numeric app ID (389801252, id389801252) or an apps.apple.com link and return the numeric ID."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Masukkan ID aplikasi (mis. 389801252) atau link App Store-nya.")
    if text.lower().startswith("id") and _ID_RE.match(text[2:]):
        return text[2:]
    if _ID_RE.match(text):
        return text
    parsed = urlparse(text if "//" in text else f"https://{text}")
    if parsed.hostname not in {"apps.apple.com", "itunes.apple.com"}:
        raise ValueError("Link harus dari apps.apple.com, atau tulis ID angka aplikasinya.")
    found = _URL_ID_RE.search(parsed.path) or _URL_ID_RE.search("/" + (parse_qs(parsed.query).get("id") or [""])[0])
    if not found:
        raise ValueError("Link App Store tidak memuat ID aplikasi (bagian id123456789).")
    return found.group(1)


def _label(node):
    return (node or {}).get("label", "") if isinstance(node, dict) else ""


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def parse_entry(entry, app_name="", app_id=""):
    """One feed entry as a row; None for the app's own header entry (it has no rating)."""
    if "im:rating" not in entry:
        return None
    stamp = _date(_label(entry.get("updated")))
    return {
        "id_ulasan": _label(entry.get("id")),
        "nama": _label((entry.get("author") or {}).get("name")),
        "rating": _int(_label(entry.get("im:rating"))),
        "tanggal": stamp.date().isoformat() if stamp else _label(entry.get("updated"))[:10],
        "waktu": stamp.isoformat() if stamp else _label(entry.get("updated")),
        "judul": _label(entry.get("title")),
        "ulasan": _label(entry.get("content")),
        "suka": _int(_label(entry.get("im:voteSum"))),
        "jumlah_suara": _int(_label(entry.get("im:voteCount"))),
        "versi_aplikasi": _label(entry.get("im:version")),
        "aplikasi": app_name,
        "kode_aplikasi": app_id,
    }


def _page(app_id, country, sort, page, stop, log):
    url = FEED.format(country=country, page=page, app_id=app_id, sort=SORTS[sort])
    try:
        data = json.loads(get(url, stop=stop, log=log))
    except ValueError as exc:
        raise ApiError("Jawaban App Store tidak bisa dibaca.") from exc
    entries = (data.get("feed") or {}).get("entry") or []
    return [entries] if isinstance(entries, dict) else entries


def fetch_app_info(app_id, country="id", stop=None, log=None):
    """Name, developer, and rating summary from Apple's lookup API; {} when the app is not in that store."""
    try:
        data = json.loads(get(LOOKUP.format(app_id=app_id, country=country), stop=stop, log=log))
    except (ValueError, ApiError):
        return {}
    found = (data.get("results") or [{}])[0]
    return {
        "nama": found.get("trackName", ""), "pengembang": found.get("artistName", ""),
        "kategori": found.get("primaryGenreName", ""), "rating": found.get("averageUserRating"),
        "jumlah_rating": found.get("userRatingCount"), "versi": found.get("version", ""),
        "url": found.get("trackViewUrl", ""),
    }


def scrape_reviews(app_id, country="id", sort="terbaru", count=0, score=None, only_with_text=True,
                   max_minutes=0, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Collect reviews page by page. `count` 0 means everything Apple serves (up to 500)."""
    from time import monotonic
    log = log_cb or (lambda message: None)
    limit = min(count, MAX_REVIEWS) if count else MAX_REVIEWS
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    result = {"rows": [], "error": None, "stop_reason": None, "sumber": "Apple App Store (RSS ulasan pelanggan)"}
    info = fetch_app_info(app_id, country, stop_event, log)
    app_name = info.get("nama", "")
    result["app_info"] = info
    log(f"Aplikasi: {app_name or app_id} · toko {country.upper()}")
    if not info:
        log("  info aplikasi tidak ditemukan; ulasan tetap dicoba.")
    seen, rows = set(), result["rows"]
    for page in range(1, MAX_PAGES + 1):
        if stop_event is not None and stop_event.is_set():
            result["stop_reason"] = "dihentikan"
            break
        if deadline and monotonic() > deadline:
            result["stop_reason"] = "waktu"
            break
        try:
            entries = _page(app_id, country, sort, page, stop_event, log)
        except ApiError as exc:
            if page == 1 or str(exc) != "Server menjawab 400 (Bad Request).":
                result["error"] = str(exc) if not rows else "partial"
                log(f"  gagal di halaman {page}: {exc}")
            break
        fresh = 0
        for entry in entries:
            row = parse_entry(entry, app_name, app_id)
            if row is None or row["id_ulasan"] in seen:
                continue
            seen.add(row["id_ulasan"])
            fresh += 1
            if score and row["rating"] != score:
                continue
            if only_with_text and not row["ulasan"].strip():
                continue
            rows.append(row)
        log(f"Halaman {page}: {fresh} ulasan baru, {len(rows)} tersimpan")
        if checkpoint_cb:
            checkpoint_cb(rows)
        if fresh == 0 or len(rows) >= limit:
            break
    del rows[limit:]
    if not rows and not result["error"]:
        result["message"] = (f"Tidak ada ulasan untuk {app_name or app_id} di toko {country.upper()}. "
                             "Coba kode negara lain atau longgarkan filter.")
    return result
