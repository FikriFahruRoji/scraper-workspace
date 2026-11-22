"""Open-Meteo weather: history (ERA5 reanalysis archive) or a forecast, daily or hourly, for any place.

Locations are place names (looked up with Open-Meteo's geocoding) or "latitude,longitude".
Data: Open-Meteo.com, CC BY 4.0, for non-commercial use.
"""
import json
import re
from urllib.parse import urlencode

from workspace_app.open_data import ApiError, get

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$")
# API variable -> column name, with the unit in the name.
DAILY = {
    "temperature_2m_max": "suhu_maks_c", "temperature_2m_min": "suhu_min_c", "temperature_2m_mean": "suhu_rata_c",
    "precipitation_sum": "hujan_mm", "precipitation_hours": "jam_hujan",
    "wind_speed_10m_max": "angin_maks_kmj", "wind_gusts_10m_max": "hembusan_maks_kmj",
    "shortwave_radiation_sum": "radiasi_mj_m2", "et0_fao_evapotranspiration": "evapotranspirasi_mm",
}
HOURLY = {
    "temperature_2m": "suhu_c", "relative_humidity_2m": "kelembapan_persen", "precipitation": "hujan_mm",
    "wind_speed_10m": "angin_kmj", "cloud_cover": "awan_persen", "surface_pressure": "tekanan_hpa",
}


def resolve(text, stop=None, log=None):
    """(name, latitude, longitude) for a place name or a 'lat,lon' pair."""
    match = COORDINATES.match(text)
    if match:
        latitude, longitude = float(match.group(1)), float(match.group(2))
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ApiError(f"Koordinat di luar jangkauan: {text.strip()}.")
        return f"{latitude:g},{longitude:g}", latitude, longitude
    query = urlencode({"name": text.strip(), "count": 1, "language": "id", "format": "json"})
    found = json.loads(get(f"{GEOCODING}?{query}", stop=stop, log=log)).get("results") or []
    if not found:
        raise ApiError(f"Lokasi '{text.strip()}' tidak ditemukan. Coba nama lain atau tulis koordinat lintang,bujur.")
    place = found[0]
    name = ", ".join(part for part in (place.get("name"), place.get("admin1"), place.get("country")) if part)
    return name, place["latitude"], place["longitude"]


def fetch(latitude, longitude, mode, interval, variables, start_date, end_date, forecast_days, stop=None, log=None):
    """The API's response for one place."""
    params = {"latitude": latitude, "longitude": longitude, "timezone": "auto", interval: ",".join(variables)}
    if mode == "forecast":
        params["forecast_days"] = forecast_days
    else:
        params.update(start_date=start_date, end_date=end_date)
    data = json.loads(get(f"{FORECAST if mode == 'forecast' else ARCHIVE}?{urlencode(params)}", stop=stop, log=log))
    if isinstance(data, dict) and data.get("error"):
        raise ApiError(f"Open-Meteo menolak: {data.get('reason', 'permintaan tidak valid')}")
    return data


def rows_from(data, name, interval, variables, drop_empty):
    names = DAILY if interval == "daily" else HOURLY
    block = data.get(interval) or {}
    times = block.get("time") or []
    for index, moment in enumerate(times):
        values = {names[variable]: (block.get(variable) or [None] * len(times))[index] for variable in variables}
        if drop_empty and all(value is None for value in values.values()):
            continue
        yield {"lokasi": name, "lintang": data.get("latitude"), "bujur": data.get("longitude"),
               "ketinggian_m": data.get("elevation"),
               "tanggal" if interval == "daily" else "waktu": moment, **values}


def collect_weather(locations, mode, interval, variables, start_date=None, end_date=None, forecast_days=7,
                    drop_empty=True, stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """One request per place; places that cannot be found or fetched are reported and skipped."""
    log = log_cb or (lambda message: None)
    rows, errors = [], []
    for number, text in enumerate(locations, 1):
        if stop_event is not None and stop_event.is_set():
            break
        log(f"Lokasi {number} dari {len(locations)}: {text.strip()}")
        try:
            name, latitude, longitude = resolve(text, stop_event, log)
            log(f"  {name} ({latitude:.3f}, {longitude:.3f})")
            data = fetch(latitude, longitude, mode, interval, variables, start_date, end_date, forecast_days,
                         stop_event, log)
        except (ApiError, KeyError, ValueError) as exc:
            errors.append(f"{text.strip()}: {exc}")
            log(f"  gagal: {exc}")
            continue
        found = list(rows_from(data, name, interval, variables, drop_empty))
        log(f"  {len(found)} baris")
        rows.extend(found)
        if checkpoint_cb:
            checkpoint_cb(rows)
    return {"rows": rows, "error": "; ".join(errors) or None, "sumber": "Open-Meteo.com (CC BY 4.0)"}
