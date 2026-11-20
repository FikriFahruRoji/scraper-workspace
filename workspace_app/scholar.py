"""Bridge Scholar's per-page snapshots to workspace jobs."""
from workspace_app.google_scholar import ScholarScraper, ScholarSearcher, ScrapeOptions, SearchOptions
from workspace_app.google_scholar.export import articles_frame, history_frame, profile_frame, search_frame, search_info_frame
from workspace_app.google_scholar.parser import extract_scholar_id


def collect(params, stop, log, checkpoint):
    rows, metadata = [], {}
    profile = params["mode"] == "profile"

    def check_stop():
        if stop.is_set():
            raise InterruptedError("Pengambilan dihentikan.")

    def sleep(seconds):
        if stop.wait(seconds):
            check_stop()

    def progress(message, fraction):
        log(message)
        check_stop()

    def save(result):
        nonlocal rows, metadata
        if profile:
            rows = articles_frame(result).to_dict("records")
            metadata = {"Profil": profile_frame(result).to_dict("records"),
                        "Sitasi per Tahun": history_frame(result).to_dict("records")}
        else:
            rows = search_frame(result).to_dict("records")
            metadata = {"Pencarian": search_info_frame(result).to_dict("records")}
        checkpoint(rows, metadata=metadata)
        check_stop()

    target = params["query"].strip()
    settings = dict(engine=params.get("engine", "http" if profile else "browser"),
                    hl=params.get("hl", "en"), timeout=params.get("timeout", 30 if profile else 60),
                    retries=0)
    try:
        check_stop()
        if profile:
            options = ScrapeOptions(max_articles=params["count"], allow_pagination=params["count"] > 100,
                                    sort=params["sort"], **settings)
            ScholarScraper(options, on_page=save, sleep=sleep).scrape(target, progress)
        else:
            options = SearchOptions(max_results=params["count"] or None,
                                    safety_cap=None if not params["count"] else params["count"],
                                    sort=params["sort"], year_from=params.get("year_from"),
                                    year_to=params.get("year_to"), **settings)
            ScholarSearcher(options, on_page=save, sleep=sleep).search(target, progress)
    except (RuntimeError, InterruptedError) as exc:
        log(str(exc))
        return rows, metadata, str(exc)
    return rows, metadata, None
