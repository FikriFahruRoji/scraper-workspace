"""Natural disasters other than earthquakes from GDACS and NASA EONET: public APIs, no key, no login.

GDACS (UN/European Commission) lists floods, tropical cyclones, volcanic eruptions, droughts, and forest
fires with a green/orange/red alert level. NASA EONET adds landslides, extreme temperatures, and dust/haze,
and follows storms and volcanoes from other agencies. Both are asked one month at a time, newest first.
GDACS pages its answers and does not keep a stable order between pages, so every page is read until a short
one and events are de-duplicated. EONET copies many GDACS events; when both sources are chosen those copies
are dropped. Times are stored in WIB and UTC.

Each event's point is placed in a kabupaten/kota and province offline, using the simplified outlines in
wilayah_idn.json (rebuilt by tools/build_wilayah.py). A point off the coast takes the nearest kabupaten
within NEAR_KM; `jarak_wilayah_km` says how far that was (0 when the point lies inside).
"""
import json
from functools import lru_cache
from math import cos, radians
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
from time import monotonic

from workspace_app.open_data import ApiError, get

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
PAGE_SIZE = 100
MAX_PAGES = 30
WINDOW_DAYS = 31
AREAS_FILE = Path(__file__).resolve().with_name("wilayah_idn.json")
NEAR_KM = 20
KM_PER_DEGREE = 111.32
WIB = timezone(timedelta(hours=7))
# key: (label, GDACS event type or None, EONET category or None)
TYPES = {
    "banjir": ("Banjir", "FL", "floods"),
    "siklon": ("Siklon tropis dan badai", "TC", "severeStorms"),
    "gunung_api": ("Gunung api", "VO", "volcanoes"),
    "kekeringan": ("Kekeringan", "DR", "drought"),
    "kebakaran": ("Kebakaran hutan", "WF", "wildfires"),
    "longsor": ("Tanah longsor", None, "landslides"),
    "suhu_ekstrem": ("Suhu ekstrem", None, "tempExtremes"),
    "kabut_asap": ("Debu dan kabut asap", None, "dustHaze"),
}
PROVIDERS = {"semua": ("GDACS dan NASA EONET",), "gdacs": ("GDACS",), "eonet": ("NASA EONET",)}
REGIONS = {
    # GDACS filters by affected country; EONET by a box (west, north, east, south).
    "indonesia": {"country": "Indonesia", "bbox": (94.0, 6.5, 142.0, -11.5)},
    "dunia": {"country": None, "bbox": None},
}
ALERTS = {"green": "hijau", "orange": "oranye", "red": "merah"}
ALERT_ORDER = ["green", "orange", "red"]
FIRST_DAY = date(2000, 1, 1)  # both archives are sparse before this


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _moment(text):
    """An aware UTC datetime from an ISO text with or without 'Z'; None when unreadable."""
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _times(prefix, moment):
    if moment is None:
        return {f"{prefix}_wib": "", f"{prefix}_utc": ""}
    return {f"{prefix}_wib": moment.astimezone(WIB).isoformat(timespec="seconds"),
            f"{prefix}_utc": moment.astimezone(timezone.utc).isoformat(timespec="seconds")}


def _centre(geometry):
    """(latitude, longitude) of a GeoJSON point, or the mean of a polygon's outer ring."""
    kind, coords = (geometry or {}).get("type"), (geometry or {}).get("coordinates")
    try:
        if kind == "Point":
            return float(coords[1]), float(coords[0])
        if kind == "Polygon":
            ring = coords[0]
            return (round(sum(p[1] for p in ring) / len(ring), 5), round(sum(p[0] for p in ring) / len(ring), 5))
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        pass
    return None, None


def months(start, end):
    """Inclusive (first, last) date windows of at most WINDOW_DAYS, newest first."""
    last = end
    while last >= start:
        first = max(start, last - timedelta(days=WINDOW_DAYS - 1))
        yield first, last
        last = first - timedelta(days=1)


# --------------------------------------------------------------------------- #
# Kabupaten/kota and province


@lru_cache(maxsize=1)
def _areas():
    """[(kabupaten, province, (west, south, east, north), [ring as [(x, y), ...]])], loaded once."""
    data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    return [(a["k"], a["p"], tuple(a["b"]), [list(zip(r[::2], r[1::2])) for r in a["r"]]) for a in data["areas"]]


def _inside(x, y, rings):
    """Even-odd rule over every ring of the area (holes and separate islands included)."""
    inside = False
    for ring in rings:
        x1, y1 = ring[-1]
        for x2, y2 in ring:
            if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
            x1, y1 = x2, y2
    return inside


def _edge_km(x, y, rings):
    """Shortest distance in km from the point to the area's outline (flat-earth, fine at this scale)."""
    scale = cos(radians(y))
    best = float("inf")
    for ring in rings:
        x1, y1 = ring[-1]
        for x2, y2 in ring:
            ax, ay, bx, by = (x1 - x) * scale, y1 - y, (x2 - x) * scale, y2 - y
            dx, dy = bx - ax, by - ay
            length = dx * dx + dy * dy
            t = max(0.0, min(1.0, -(ax * dx + ay * dy) / length)) if length else 0.0
            best = min(best, (ax + t * dx) ** 2 + (ay + t * dy) ** 2)
            x1, y1 = x2, y2
    return best ** 0.5 * KM_PER_DEGREE


def locate(latitude, longitude):
    """(kabupaten/kota, province, distance km) for a point in or near Indonesia, else ("", "", None)."""
    if latitude is None or longitude is None:
        return "", "", None
    x, y = float(longitude), float(latitude)
    margin = NEAR_KM / KM_PER_DEGREE / max(cos(radians(y)), 0.1)
    nearest = ("", "", None)
    for name, province, (west, south, east, north), rings in _areas():
        if west <= x <= east and south <= y <= north and _inside(x, y, rings):
            return name, province, 0.0
        if west - margin <= x <= east + margin and south - margin <= y <= north + margin:
            distance = _edge_km(x, y, rings)
            if distance <= NEAR_KM and (nearest[2] is None or distance < nearest[2]):
                nearest = (name, province, round(distance, 1))
    return nearest


def with_region(row):
    """The row with provinsi, kabupaten_kota, and jarak_wilayah_km placed after its country."""
    kabupaten, province, distance = locate(row.get("lintang"), row.get("bujur"))
    placed = {}
    for key, value in row.items():
        placed[key] = value
        if key == "negara":
            placed.update(provinsi=province, kabupaten_kota=kabupaten, jarak_wilayah_km=distance)
    return placed


# --------------------------------------------------------------------------- #
# GDACS

GDACS_TYPES = {gdacs: key for key, (_, gdacs, _) in TYPES.items() if gdacs}


def parse_gdacs(feature):
    """One GDACS feature as a flat row, or None when it is not a usable event."""
    props = feature.get("properties") or {}
    kind = GDACS_TYPES.get(props.get("eventtype"))
    if not kind or props.get("eventid") is None:
        return None
    latitude, longitude = _centre(feature.get("geometry"))
    severity = props.get("severitydata") or {}
    alert = (props.get("alertlevel") or "").lower()
    url = props.get("url") or {}
    return {
        "sumber": "GDACS", "id_kejadian": f"{props['eventtype']}{props['eventid']}",
        "jenis": TYPES[kind][0], "nama": props.get("name") or props.get("eventname") or "",
        "negara": props.get("country") or "",
        **_times("mulai", _moment(props.get("fromdate"))), **_times("selesai", _moment(props.get("todate"))),
        "lintang": latitude, "bujur": longitude,
        "peringatan": ALERTS.get(alert, alert), "skor_peringatan": props.get("alertscore"),
        "keparahan": (severity.get("severitytext") or "").strip(),
        "nilai_keparahan": severity.get("severity") or None, "satuan_keparahan": severity.get("severityunit") or "",
        "status": "berlangsung" if str(props.get("iscurrent")).lower() == "true" else "selesai",
        "sumber_asli": props.get("source") or "",
        "url": url.get("report", "") if isinstance(url, dict) else "",
    }


def _gdacs_window(first, last, kinds, region, min_alert, stop, log):
    """Every GDACS event of the window, reading pages until a short one."""
    params = {"eventlist": ";".join(TYPES[k][1] for k in kinds),
              "fromDate": first.isoformat(), "toDate": last.isoformat(),
              "alertlevel": ";".join(ALERT_ORDER[ALERT_ORDER.index(min_alert):]), "pageSize": PAGE_SIZE}
    if REGIONS[region]["country"]:
        params["country"] = REGIONS[region]["country"]
    base = GDACS_URL + "?" + "&".join(f"{key}={value}" for key, value in params.items())
    for page in range(1, MAX_PAGES + 1):
        body = get(f"{base}&pageNumber={page}", timeout=60, retries=3, pause=2, stop=stop, log=log)
        if not body.strip():  # GDACS answers 204 with no body when nothing matches
            return
        try:
            features = json.loads(body).get("features") or []
        except ValueError as exc:
            raise ApiError("Jawaban GDACS tidak bisa dibaca.") from exc
        yield from features
        if len(features) < PAGE_SIZE:
            return


# --------------------------------------------------------------------------- #
# EONET


def parse_eonet(event, skip_gdacs=False):
    """One EONET event as a flat row (position and size from its latest observation), or None."""
    categories = [c.get("id") for c in event.get("categories") or []]
    kind = next((key for key, (_, _, eonet) in TYPES.items() if eonet in categories), None)
    sources = [s.get("id") for s in event.get("sources") or [] if s.get("id")]
    if not kind or not event.get("id"):
        return None
    if skip_gdacs and sources and set(sources) <= {"GDACS"}:
        return None
    observations = sorted(event.get("geometry") or [], key=lambda g: g.get("date") or "")
    latest = observations[-1] if observations else {}
    latitude, longitude = _centre(latest)
    value = latest.get("magnitudeValue")
    unit = latest.get("magnitudeUnit") or ""
    closed = _moment(event.get("closed"))
    return {
        "sumber": "NASA EONET", "id_kejadian": event["id"], "jenis": TYPES[kind][0],
        "nama": event.get("title") or "", "negara": "",
        **_times("mulai", _moment(observations[0].get("date")) if observations else None),
        **_times("selesai", closed or (_moment(latest.get("date")) if latest else None)),
        "lintang": latitude, "bujur": longitude,
        "peringatan": "", "skor_peringatan": None,
        "keparahan": f"{value:g} {unit}".strip() if isinstance(value, (int, float)) else "",
        "nilai_keparahan": value, "satuan_keparahan": unit,
        "status": "selesai" if event.get("closed") else "berlangsung",
        "sumber_asli": ", ".join(sources), "url": event.get("link") or "",
    }


def _eonet_window(first, last, kinds, region, stop, log):
    params = {"status": "all", "start": first.isoformat(), "end": last.isoformat(),
              "category": ",".join(TYPES[k][2] for k in kinds)}
    if REGIONS[region]["bbox"]:
        params["bbox"] = ",".join(str(edge) for edge in REGIONS[region]["bbox"])
    url = EONET_URL + "?" + "&".join(f"{key}={value}" for key, value in params.items())
    try:
        return json.loads(get(url, timeout=60, retries=3, pause=2, stop=stop, log=log)).get("events") or []
    except ValueError as exc:
        raise ApiError("Jawaban NASA EONET tidak bisa dibaca.") from exc


# --------------------------------------------------------------------------- #


def collect_disasters(start_date, end_date, region="indonesia", provider="semua", types=None, min_alert="green",
                      count=0, max_minutes=10, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Disaster events in the region and period, newest first. `count` 0 keeps every event found in time."""
    log = log_cb or (lambda message: None)
    kinds = [k for k in (types or list(TYPES)) if k in TYPES]
    start, end = _day(start_date), _day(end_date)
    use_gdacs, use_eonet = provider in {"semua", "gdacs"}, provider in {"semua", "eonet"}
    gdacs_kinds = [k for k in kinds if TYPES[k][1]] if use_gdacs else []
    eonet_kinds = [k for k in kinds if TYPES[k][2]] if use_eonet else []
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    rows, seen, errors, stop_reason = [], set(), {}, None

    def keep(row):
        if row and row["id_kejadian"] not in seen:
            seen.add(row["id_kejadian"])
            rows.append(row)

    for first, last in months(start, end):
        if stop_event is not None and stop_event.is_set():
            stop_reason = "dihentikan"
            break
        if deadline and monotonic() > deadline:
            stop_reason = "waktu"
            break
        log(f"{first.isoformat()} – {last.isoformat()}")
        if gdacs_kinds and "GDACS" not in errors:
            try:
                for feature in _gdacs_window(first, last, gdacs_kinds, region, min_alert, stop_event, log):
                    keep(parse_gdacs(feature))
            except ApiError as exc:
                errors["GDACS"] = str(exc)
                log(f"  GDACS gagal: {exc}")
        if eonet_kinds and "NASA EONET" not in errors:
            try:
                for event in _eonet_window(first, last, eonet_kinds, region, stop_event, log):
                    keep(parse_eonet(event, skip_gdacs=bool(gdacs_kinds)))
            except ApiError as exc:
                errors["NASA EONET"] = str(exc)
                log(f"  NASA EONET gagal: {exc}")
        log(f"  {len(rows)} kejadian terkumpul")
        if checkpoint_cb:
            checkpoint_cb(rows)
        if count and len(rows) >= count:
            break
        if (not gdacs_kinds or "GDACS" in errors) and (not eonet_kinds or "NASA EONET" in errors):
            break  # every chosen source has failed
    rows.sort(key=lambda row: row["mulai_utc"], reverse=True)
    if count:
        del rows[count:]
    if rows:
        log("Menentukan provinsi dan kabupaten/kota dari lintang dan bujur…")
        rows[:] = [with_region(row) for row in rows]
    error = "; ".join(f"{name}: {message}" for name, message in errors.items()) or None
    names = [name for name, used in (("GDACS (gdacs.org)", gdacs_kinds), ("NASA EONET (eonet.gsfc.nasa.gov)", eonet_kinds)) if used]
    result = {"rows": rows, "error": error, "stop_reason": stop_reason, "sumber": " dan ".join(names)}
    if not gdacs_kinds and not eonet_kinds:
        result["error"] = "Jenis bencana yang dipilih tidak tersedia di sumber ini."
    elif not rows and not error and not stop_reason:
        result["message"] = ("Tidak ada kejadian bencana tercatat pada wilayah, periode, dan jenis ini. "
                             "Perluas periode atau turunkan level peringatan minimum.")
    return result
