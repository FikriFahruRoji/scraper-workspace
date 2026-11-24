"""NASA FIRMS active-fire hotspots from the yearly country archives: public files, no key, no login.

FIRMS publishes one CSV per sensor, year, and country (standard-processing archive, finished after the
year ends), so the newest year available is the last full calendar year. Near-real-time data and
arbitrary areas need a personal FIRMS MAP_KEY and are not used here. Times are stored in WIB and UTC.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from time import monotonic, sleep
from urllib.parse import quote

from workspace_app.open_data import ApiError, get

BASE = "https://firms.modaps.eosdis.nasa.gov/data/country"
WIB = timezone(timedelta(hours=7))
PAUSE = 1.0
SENSORS = {
    "viirs-snpp": ("VIIRS Suomi-NPP (375 m)", 2012, "VIIRS"),
    "modis": ("MODIS Terra dan Aqua (1 km)", 2000, "MODIS"),
}
CONFIDENCE = {"l": "rendah", "n": "nominal", "h": "tinggi"}
TYPES = {"0": "kebakaran vegetasi", "1": "gunung api aktif", "2": "permukaan darat statis",
         "3": "lepas pantai"}
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_\-'.]*$")


def last_year(today=None):
    """Newest year with a finished archive: the last full calendar year."""
    return (today or date.today()).year - 1


def normalize_country(value):
    """'papua new guinea' -> 'Papua_New_Guinea' (FIRMS names); raises ValueError for anything else."""
    text = re.sub(r"\s+", "_", (value or "").strip())
    if not text or not _NAME.match(text):
        raise ValueError("Tulis nama negara dalam bahasa Inggris, misalnya Indonesia atau Papua New Guinea.")
    return "_".join(part[:1].upper() + part[1:] for part in text.split("_"))


def archive_url(sensor, year, country):
    return f"{BASE}/{sensor}/{year}/{sensor}_{year}_{quote(country)}.csv"


def _confidence(value, instrument):
    value = (value or "").strip()
    if instrument == "VIIRS":
        return CONFIDENCE.get(value.lower(), value)
    return int(value) if value.isdigit() else None


def is_confident(value, instrument):
    """Nominal or high confidence: VIIRS n/h, MODIS 30 and above."""
    if instrument == "VIIRS":
        return str(value) in ("nominal", "tinggi")
    return isinstance(value, int) and value >= 30


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_row(record, instrument, country):
    """One archive line as a flat row, or None when its date or position cannot be read."""
    try:
        clock = (record.get("acq_time") or "").strip().zfill(4)
        moment = datetime.strptime(f"{record['acq_date']} {clock}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)
        latitude, longitude = float(record["latitude"]), float(record["longitude"])
    except (KeyError, ValueError):
        return None
    return {
        "waktu_wib": moment.astimezone(WIB).isoformat(timespec="minutes"),
        "waktu_utc": moment.isoformat(timespec="minutes"),
        "lintang": latitude, "bujur": longitude, "negara": country.replace("_", " "),
        "sensor": instrument, "satelit": {"N": "Suomi-NPP", "T": "Terra", "A": "Aqua"}.get(
            (record.get("satellite") or "").strip(), (record.get("satellite") or "").strip()),
        "kecerahan_k": _float(record.get("bright_ti4") or record.get("brightness")),
        "frp_mw": _float(record.get("frp")),
        "keyakinan": _confidence(record.get("confidence"), instrument),
        "siang_malam": {"D": "siang", "N": "malam"}.get((record.get("daynight") or "").strip(), ""),
        "jenis": TYPES.get((record.get("type") or "").strip(), ""),
    }


def collect_hotspots(country, sensor, start_year, end_year, only_confident=False, only_vegetation=False,
                     count=0, max_minutes=15, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Download each year's archive and keep the rows that pass the filters. `count` 0 means all rows."""
    log = log_cb or (lambda message: None)
    label, _first, instrument = SENSORS[sensor]
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    rows, notes, error, stop_reason = [], [], None, None
    for year in range(start_year, end_year + 1):
        if stop_event is not None and stop_event.is_set():
            stop_reason = "dihentikan"
            break
        if deadline and monotonic() > deadline:
            stop_reason = "waktu"
            break
        log(f"{label} · {country.replace('_', ' ')} · {year}")
        try:
            body = get(archive_url(sensor, year, country), accept="text/csv", timeout=240, retries=3, pause=3,
                       stop=stop_event, log=log)
        except ApiError as exc:
            if "404" in str(exc):
                notes.append(f"{year}: arsip tidak ada")
                log(f"  arsip {year} tidak ada untuk {country.replace('_', ' ')} (periksa nama negara dan tahun)")
                continue
            error = f"{year}: {exc}"
            log(f"  gagal: {exc}")
            break
        seen = kept = 0
        for record in csv.DictReader(io.StringIO(body.decode("utf-8", "replace"))):
            seen += 1
            row = parse_row(record, instrument, country)
            if row is None:
                continue
            if only_confident and not is_confident(row["keyakinan"], instrument):
                continue
            if only_vegetation and row["jenis"] != TYPES["0"]:
                continue
            rows.append(row)
            kept += 1
            if count and len(rows) >= count:
                break
        log(f"  {kept} dari {seen} titik api disimpan")
        if checkpoint_cb:
            checkpoint_cb(rows)
        if count and len(rows) >= count:
            log("  batas jumlah data tercapai")
            break
        sleep(PAUSE)
    result = {"rows": rows, "error": error, "stop_reason": stop_reason,
              "sumber": "NASA FIRMS, arsip standar per negara (firms.modaps.eosdis.nasa.gov)"}
    if not rows and not error and not stop_reason:
        result["message"] = ("Tidak ada titik api yang tersimpan. Arsip FIRMS hanya sampai "
                             f"{last_year()}; cek nama negara, tahun, atau longgarkan filter.")
    return result
