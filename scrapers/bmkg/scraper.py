"""BMKG open earthquake feeds: the newest quake, the latest M 5.0+ quakes, and the latest felt quakes.

BMKG publishes only the most recent items of each feed (15 for the M 5.0+ and felt lists), not a history.
A quake that appears in more than one feed becomes one row that lists all the feeds it came from.
"""
from datetime import datetime, timedelta, timezone
import json
import re

from workspace_app.open_data import ApiError, get

BASE = "https://data.bmkg.go.id/DataMKG/TEWS"
FEEDS = {
    "terbaru": ("autogempa.json", "Terbaru"),
    "m5": ("gempaterkini.json", "M 5,0+"),
    "dirasakan": ("gempadirasakan.json", "Dirasakan"),
}
SHAKEMAP = "https://static.bmkg.go.id"
WIB = timezone(timedelta(hours=7))
NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")


def _number(text):
    match = NUMBER.search(str(text or ""))
    return float(match.group().replace(",", ".")) if match else None


def parse_quake(item):
    """One BMKG record as a flat row, with real numbers and WIB time."""
    try:
        moment = datetime.fromisoformat(item["DateTime"]).astimezone(WIB)
        latitude, longitude = (float(part) for part in item["Coordinates"].split(","))
    except (KeyError, ValueError) as exc:
        raise ApiError("Format data BMKG berubah dan tidak bisa dibaca.") from exc
    felt = (item.get("Dirasakan") or "").strip()
    shakemap = (item.get("Shakemap") or "").strip()
    return {
        "waktu": moment.isoformat(timespec="seconds"),
        "magnitudo": _number(item.get("Magnitude")),
        "kedalaman_km": _number(item.get("Kedalaman")),
        "lintang": latitude, "bujur": longitude,
        "wilayah": (item.get("Wilayah") or "").strip(),
        "potensi_tsunami": (item.get("Potensi") or "").strip(),
        "dirasakan": "" if felt in ("", "-") else felt,
        "peta_guncangan": f"{SHAKEMAP}/{shakemap}" if shakemap else "",
    }


def _records(payload):
    quakes = payload["Infogempa"]["gempa"]
    return quakes if isinstance(quakes, list) else [quakes]


def collect_earthquakes(feeds, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Fetch the chosen feeds and merge quakes that appear in several of them."""
    log = log_cb or (lambda message: None)
    merged, errors = {}, []
    for number, feed in enumerate(feeds, 1):
        if stop_event is not None and stop_event.is_set():
            break
        name, title = FEEDS[feed]
        log(f"Daftar {number} dari {len(feeds)}: gempa {title.lower()}")
        try:
            records = [parse_quake(item) for item in _records(json.loads(get(f"{BASE}/{name}", stop=stop_event,
                                                                             log=log)))]
        except (ApiError, KeyError, ValueError) as exc:
            errors.append(f"{title}: {exc}")
            log(f"  gagal: {exc}")
            continue
        log(f"  {len(records)} gempa")
        for row in records:
            key = (row["waktu"], row["lintang"], row["bujur"], row["magnitudo"])
            existing = merged.setdefault(key, {**row, "daftar": []})
            for field, value in row.items():  # a later feed may know more (felt report, tsunami note)
                if value not in ("", None) and existing.get(field) in ("", None):
                    existing[field] = value
            existing["daftar"].append(title)
        if checkpoint_cb:
            checkpoint_cb(_flat(merged))
    rows = _flat(merged)
    log(f"{len(rows)} gempa unik.")
    return {"rows": rows, "error": "; ".join(errors) or None, "sumber": "BMKG Data Terbuka (data.bmkg.go.id)"}


def _flat(merged):
    rows = [{**row, "daftar": ", ".join(row["daftar"])} for row in merged.values()]
    return sorted(rows, key=lambda row: row["waktu"], reverse=True)
