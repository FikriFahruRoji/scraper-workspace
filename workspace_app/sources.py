"""Load the scrapers in scrapers/ under distinct module names."""
import importlib.util
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRAPERS = ROOT / "scrapers"
SHARED = SCRAPERS / "_shared"  # modules several scrapers import by plain name, such as stealth_transport
LABELS = {
    "google_scholar": "Google Scholar",
    "news": "Berita", "gmaps": "Google Maps Review", "gmaps_places": "Google Maps Lokasi",
    "instagram": "Instagram",
    "playstore": "Play Store", "appstore": "App Store", "saham": "Saham", "threads": "Threads",
    "x": "X / Twitter", "youtube": "YouTube", "worldbank": "World Bank", "imf": "IMF",
    "oecd": "OECD", "usgs": "Gempa (USGS)", "firms": "Titik Api (NASA FIRMS)", "bencana": "Bencana (GDACS & EONET)", "pangan": "Harga Pangan (BI)", "openmeteo": "Cuaca (Open-Meteo)", "sinta": "SINTA",
}
# Every scraper lives in scrapers/<source>/. Only Google Scholar is a package of the app itself.
FOLDERS = {key: key for key in LABELS if key != "google_scholar"}
PROFILES = {"gmaps", "gmaps_places", "instagram", "threads", "x"}
ARCHIVE_LABELS = {"scholar": "Google Scholar (arsip)", "openalex": "OpenAlex (arsip)", "bmkg": "Gempa BMKG (arsip)"}
OTHER_LABELS = {**ARCHIVE_LABELS, "preprocessing": "Preprocessing", "gabungan": "Gabungan", "sentimen": "Sentimen", "emosi": "Emosi & Toksisitas"}


def label(source):
    return LABELS.get(source) or OTHER_LABELS.get(source, source)


# Google Maps keeps isolated browser profiles but offers no Google login.
LOGINS = PROFILES - {"gmaps", "gmaps_places"}
FUNCTIONS = {
    "google_scholar": "collect",
    "news": "run_scrape", "gmaps": "scrape_reviews", "gmaps_places": "search_places",
    "instagram": "scrape_content",
    "playstore": "scrape_reviews", "appstore": "scrape_reviews", "saham": "scrape_prices", "threads": "scrape_posts",
    "x": "scrape_tweets", "youtube": "scrape_comments",
    "worldbank": "scrape_indicators", "imf": "scrape_weo",
    "oecd": "collect_data", "usgs": "collect_earthquakes", "firms": "collect_hotspots", "bencana": "collect_disasters", "pangan": "collect_prices", "openmeteo": "collect_weather", "sinta": "collect_sinta",
}


@lru_cache
def load(source):
    if source == "google_scholar":
        from workspace_app import scholar
        return scholar
    folder = SCRAPERS / FOLDERS[source]
    path = folder / ("__init__.py" if source == "news" else "scraper.py")
    name = f"_workspace_scraper_{source}"
    options = {"submodule_search_locations": [str(folder)]} if source == "news" else {}
    spec = importlib.util.spec_from_file_location(name, path, **options)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Play Store's local parser and the shared transport are imported by plain name.
    search = [str(folder), str(SHARED)]
    sys.path[:0] = search
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        for entry in search:
            sys.path.remove(entry)
    return module


def execute(source, params, profile, stop, log, checkpoint):
    module = load(source)
    if source == "google_scholar":
        return module.collect(params, stop, log, checkpoint)
    options = dict(params)
    function = FUNCTIONS[source]
    if source == "instagram" and options.pop("comments"):
        function = "scrape_comments"
    if source in PROFILES:
        options["user_data_dir"] = str(profile)
    if source != "news":
        options["real_chrome"] = False
    options.update(stop_event=stop)
    if source != "saham":
        options["checkpoint_cb"] = checkpoint
    options["log_cb"] = (lambda message, _count: log(message)) if source == "news" else log
    result = getattr(module, function)(**options)
    if isinstance(result, tuple):
        rows, error = result
        return rows, {}, error
    if source == "news":
        return result["articles"], {k: v for k, v in result.items() if k != "articles"}, "; ".join(result["errors"])
    error = result.get("error")
    if not error and result.get("stop_reason") in {"gangguan", "waktu", "stagnan"}:
        error = result.get("message") or result["stop_reason"]
    if not error and result.get("api_stopped") in {"blocked", "parse_error", "timeout"}:
        error = result.get("message") or result["api_stopped"]
    return result.get("rows", []), {
        k: v for k, v in result.items() if k != "rows"
    }, error
