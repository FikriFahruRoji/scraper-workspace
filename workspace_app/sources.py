"""Load existing scrapers under distinct module names."""
import importlib.util
from functools import lru_cache
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "google_scholar": "Google Scholar",
    "news": "Berita", "gmaps": "Google Maps", "instagram": "Instagram",
    "playstore": "Play Store", "saham": "Saham", "threads": "Threads",
    "x": "X / Twitter", "youtube": "YouTube",
}
FOLDERS = {
    "news": "news-scraper", "gmaps": "gmaps-reviews-scraper",
    "instagram": "instagram-scraper", "playstore": "playstore-reviews-scraper",
    "saham": "saham-scraper", "threads": "threads-scraper",
    "x": "x-tweet-scraper", "youtube": "youtube-comments-scraper",
}
PROFILES = {"gmaps", "instagram", "threads", "x"}
ARCHIVE_LABELS = {"scholar": "Google Scholar (arsip)", "openalex": "OpenAlex (arsip)"}
OTHER_LABELS = {**ARCHIVE_LABELS, "preprocessing": "Preprocessing", "gabungan": "Gabungan"}


def label(source):
    return LABELS.get(source) or OTHER_LABELS.get(source, source)


# Google Maps keeps an isolated browser profile but offers no Google login.
LOGINS = PROFILES - {"gmaps"}
FUNCTIONS = {
    "google_scholar": "collect",
    "news": "run_scrape", "gmaps": "scrape_reviews", "instagram": "scrape_content",
    "playstore": "scrape_reviews", "saham": "scrape_prices", "threads": "scrape_posts",
    "x": "scrape_tweets", "youtube": "scrape_comments",
}


@lru_cache
def load(source):
    if source == "google_scholar":
        from workspace_app import scholar
        return scholar
    folder = ROOT / FOLDERS[source]
    path = folder / ("scraper/__init__.py" if source == "news" else "scraper.py")
    name = f"_workspace_scraper_{source}"
    options = {"submodule_search_locations": [str(path.parent)]} if source == "news" else {}
    spec = importlib.util.spec_from_file_location(name, path, **options)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    # Play Store's local parser and the three identical transport modules
    # use absolute imports in the standalone apps.
    sys.path.insert(0, str(folder))
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        sys.path.remove(str(folder))
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
