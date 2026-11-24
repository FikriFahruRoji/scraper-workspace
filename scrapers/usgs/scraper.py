"""Earthquake history from the USGS Earthquake Catalog (FDSN event API): public, no key, no login.

The catalog goes back decades and covers the whole world, so a region and a magnitude floor choose what
is returned. One request returns at most 20,000 events: the date range is cut into yearly windows, and any
window that hits the cap is split in half until every piece fits. Times are stored in WIB and UTC.
"""
import json
from datetime import date, datetime, timedelta, timezone
from time import monotonic

from workspace_app.open_data import ApiError, get

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
CAP = 20000
WIB = timezone(timedelta(hours=7))
FIRST_DAY = date(1900, 1, 1)
REGIONS = {
    "indonesia": {"south": -11.5, "north": 6.5, "west": 94.0, "east": 142.0},
    "dunia": None,
}
ALERTS = {"green": "hijau", "yellow": "kuning", "orange": "oranye", "red": "merah"}


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def day_range(start, end):
    """Aware datetimes (WIB) from the start of `start` to the start of the day after `end`."""
    first = datetime(start.year, start.month, start.day, tzinfo=WIB)
    return first, datetime(end.year, end.month, end.day, tzinfo=WIB) + timedelta(days=1)


def _stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def bounds_of(region, south=None, north=None, west=None, east=None):
    """The latitude/longitude box for a preset region or a custom box; None means the whole world."""
    if region == "custom":
        return {"south": south, "north": north, "west": west, "east": east}
    return REGIONS[region]


def parse_event(feature):
    """One GeoJSON feature as a flat row, or None when it has no usable time or position."""
    props = feature.get("properties") or {}
    try:
        longitude, latitude, *rest = feature["geometry"]["coordinates"]
        moment = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "id_kejadian": feature.get("id", ""),
        "waktu": moment.astimezone(WIB).isoformat(timespec="seconds"),
        "waktu_utc": moment.isoformat(timespec="seconds"),
        "magnitudo": props.get("mag"), "jenis_magnitudo": props.get("magType") or "",
        "kedalaman_km": rest[0] if rest else None,
        "lintang": latitude, "bujur": longitude,
        "tempat": props.get("place") or "",
        "potensi_tsunami": "ya" if props.get("tsunami") else "tidak",
        "laporan_dirasakan": props.get("felt"),
        "intensitas_dirasakan": props.get("cdi"), "intensitas_mmi": props.get("mmi"),
        "peringatan_pager": ALERTS.get(props.get("alert"), props.get("alert") or ""),
        "status": props.get("status") or "", "signifikansi": props.get("sig"),
        "jaringan": props.get("net") or "", "url": props.get("url") or "",
    }


def _query(first, last, minimum, box, stop, log):
    params = {"format": "geojson", "eventtype": "earthquake", "orderby": "time", "limit": CAP,
              "starttime": _stamp(first), "endtime": _stamp(last), "minmagnitude": minimum}
    if box:
        params.update(minlatitude=box["south"], maxlatitude=box["north"],
                      minlongitude=box["west"], maxlongitude=box["east"])
    url = URL + "?" + "&".join(f"{key}={value}" for key, value in params.items())
    try:
        return json.loads(get(url, timeout=60, retries=3, pause=2, stop=stop, log=log)).get("features") or []
    except ValueError as exc:
        raise ApiError("Jawaban USGS tidak bisa dibaca.") from exc


def _fetch(first, last, minimum, box, stop, log, deadline):
    """Newest-first events in [first, last), splitting the window while it hits the per-request cap."""
    features = _query(first, last, minimum, box, stop, log)
    if len(features) < CAP or last - first <= timedelta(minutes=1):
        yield from features
        return
    middle = first + (last - first) / 2
    log(f"  {CAP} kejadian dalam satu permintaan, rentang dibagi dua.")
    for part in ((middle, last), (first, middle)):
        if stop is not None and stop.is_set() or deadline and monotonic() > deadline:
            return
        yield from _fetch(*part, minimum, box, stop, log, deadline)


def yearly(first, last):
    """Windows of at most a year, newest first."""
    while first < last:
        begin = max(first, last - timedelta(days=366))
        yield begin, last
        last = begin


def collect_earthquakes(region, start_date, end_date, min_magnitude=4.5, south=None, north=None, west=None,
                        east=None, count=0, max_minutes=10, stop_event=None, checkpoint_cb=None, log_cb=None,
                        **_ignored):
    """Fetch quakes in the box and period, newest first. `count` 0 means every quake found in the time limit."""
    log = log_cb or (lambda message: None)
    box = bounds_of(region, south, north, west, east)
    first, last = day_range(_day(start_date), _day(end_date))
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    rows, seen, stop_reason, error = [], set(), None, None
    for begin, finish in yearly(first, last):
        if stop_event is not None and stop_event.is_set():
            stop_reason = "dihentikan"
            break
        if deadline and monotonic() > deadline:
            stop_reason = "waktu"
            break
        log(f"{begin.date().isoformat()} – {(finish - timedelta(days=1)).date().isoformat()}")
        try:
            for feature in _fetch(begin, finish, min_magnitude, box, stop_event, log, deadline):
                row = parse_event(feature)
                if row and row["id_kejadian"] not in seen:
                    seen.add(row["id_kejadian"])
                    rows.append(row)
        except ApiError as exc:
            error = str(exc)
            log(f"  gagal: {exc}")
            break
        log(f"  {len(rows)} gempa terkumpul")
        if checkpoint_cb:
            checkpoint_cb(rows)
        if count and len(rows) >= count:
            break
    rows.sort(key=lambda row: row["waktu_utc"], reverse=True)
    if count:
        del rows[count:]
    result = {"rows": rows, "error": error, "stop_reason": stop_reason,
              "sumber": "USGS Earthquake Catalog (earthquake.usgs.gov)"}
    if not rows and not error and not stop_reason:
        result["message"] = "USGS tidak mencatat gempa pada wilayah, periode, dan magnitudo ini. Turunkan magnitudo minimum atau perluas periode."
    return result
