"""IMF World Economic Outlook (WEO) through the IMF's SDMX data API. Public data, no key needed.

Values are in full units (people, US dollars), not millions or billions. WEO includes the IMF's own projections for coming years; each row says whether the year is actual data
or a projection, using the latest actual year the IMF reports for that country and indicator.
"""
import re
import xml.etree.ElementTree as ET

from workspace_app.countries import COUNTRIES
from workspace_app.open_data import ApiError, get

BASE = "https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO"
YEAR_RE = re.compile(r"\d{4}")


def _year(text):
    """First four-digit year in a period such as '2024' or 'FY2024/25'; None when there is none."""
    match = YEAR_RE.search(text or "")
    return int(match.group()) if match else None


def _tag(element):
    return element.tag.split("}")[-1]


def parse(xml_bytes):
    """Rows from a WEO structure-specific XML message."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ApiError("Jawaban IMF tidak bisa dibaca.") from exc
    names, latest, series = {}, {}, []
    for element in root.iter():
        kind = _tag(element)
        if kind == "Group":
            attrs = element.attrib
            if "SERIES_NAME" in attrs and "COUNTRY" not in attrs:
                names[attrs["INDICATOR"]] = attrs["SERIES_NAME"]
            if "LATEST_ACTUAL_ANNUAL_DATA" in attrs:
                latest[(attrs.get("COUNTRY"), attrs.get("INDICATOR"))] = attrs["LATEST_ACTUAL_ANNUAL_DATA"]
        elif kind == "Series":
            series.append(element)
    rows = []
    for element in series:
        country, indicator = element.attrib.get("COUNTRY", ""), element.attrib.get("INDICATOR", "")
        last_actual = _year(latest.get((country, indicator)))
        for obs in element:
            if _tag(obs) != "Obs" or obs.attrib.get("OBS_VALUE") in (None, ""):
                continue
            period = obs.attrib.get("TIME_PERIOD", "")
            year = _year(period)
            rows.append({
                "negara": COUNTRIES.get(country, country), "kode_negara": country,
                "kode_indikator": indicator, "indikator": names.get(indicator, indicator),
                "tahun": year if year is not None else period, "nilai": float(obs.attrib["OBS_VALUE"]),
                "jenis": "proyeksi" if last_actual and year is not None and year > last_actual else "aktual",
            })
    rows.sort(key=lambda row: (row["kode_indikator"], row["kode_negara"], str(row["tahun"])))
    return rows


def scrape_weo(countries, indicators, start_year, end_year, max_minutes=10, stop_event=None,
               checkpoint_cb=None, log_cb=None, **_ignored):
    """Fetch the chosen WEO indicators for the chosen countries ("all" means every country and group)."""
    log = log_cb or (lambda message: None)
    key = ("" if not countries or "all" in countries else "+".join(countries)) + "." + "+".join(indicators) + ".A"
    log(f"Meminta {len(indicators)} indikator untuk "
        f"{'semua negara' if key.startswith('.') else str(len(countries)) + ' negara'} ({start_year}-{end_year}).")
    try:
        body = get(f"{BASE}/{key}?startPeriod={start_year}&endPeriod={end_year}", accept="application/xml",
                   timeout=90, stop=stop_event, log=log)
        rows = parse(body)
    except ApiError as exc:
        log(f"Gagal: {exc}")
        return {"rows": [], "error": str(exc)}
    log(f"{len(rows)} baris diterima.")
    if checkpoint_cb:
        checkpoint_cb(rows)
    error = None if rows else "Tidak ada data untuk negara, indikator, dan tahun ini."
    return {"rows": rows, "error": error, "sumber": "IMF World Economic Outlook (WEO)"}
