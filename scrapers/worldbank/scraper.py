"""World Bank Indicators API: one row per country, indicator, and year. Public data, no key needed."""
import json
from time import monotonic
from urllib.parse import quote

from workspace_app.open_data import ApiError, get

BASE = "https://api.worldbank.org/v2"
PAGE_SIZE = 1000


def _page(codes, indicator, start, end, page, stop, log=None):
    url = (f"{BASE}/country/{codes}/indicator/{quote(indicator, safe='.')}"
           f"?format=json&per_page={PAGE_SIZE}&page={page}&date={start}:{end}")
    data = json.loads(get(url, stop=stop, log=log))
    if not isinstance(data, list) or not data:
        raise ApiError("Jawaban World Bank tidak bisa dibaca.")
    if len(data) == 1 or (isinstance(data[0], dict) and data[0].get("message")):
        message = (data[0].get("message") or [{}])[0].get("value", "")
        if not message or "not valid" in message or "not found" in message.lower():
            message = "Kode indikator atau negara tidak dikenali World Bank."
        raise ApiError(message)
    return data[0], data[1] or []


def _rows(records, drop_empty):
    for record in records:
        value = record.get("value")
        if value is None and drop_empty:
            continue
        year = str(record.get("date", ""))
        yield {
            "negara": (record.get("country") or {}).get("value", ""),
            "kode_negara": record.get("countryiso3code") or (record.get("country") or {}).get("id", ""),
            "kode_indikator": (record.get("indicator") or {}).get("id", ""),
            "indikator": (record.get("indicator") or {}).get("value", ""),
            "tahun": int(year) if year.isdigit() else year,
            "nilai": value,
            "satuan": record.get("unit", ""),
        }


def scrape_indicators(countries, indicators, start_year, end_year, drop_empty=True, max_minutes=10,
                      stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """Fetch each indicator for the chosen countries ("all" means every country and region)."""
    log = log_cb or (lambda message: None)
    codes = "all" if not countries or "all" in countries else ";".join(countries)
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    rows, errors, stop_reason = [], [], None
    for number, indicator in enumerate(indicators, 1):
        if stop_event is not None and stop_event.is_set():
            stop_reason = "dihentikan"
            break
        if deadline and monotonic() > deadline:
            stop_reason = "waktu"
            break
        log(f"Indikator {number} dari {len(indicators)}: {indicator}")
        try:
            meta, records = _page(codes, indicator, start_year, end_year, 1, stop_event, log)
            collected = list(records)
            for page in range(2, int(meta.get("pages", 1)) + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                log(f"  halaman {page} dari {meta['pages']}")
                collected.extend(_page(codes, indicator, start_year, end_year, page, stop_event, log)[1])
        except ApiError as exc:
            errors.append(f"{indicator}: {exc}")
            log(f"  gagal: {exc}")
            continue
        found = list(_rows(collected, drop_empty))
        log(f"  {len(found)} baris dari {meta.get('total', len(collected))} data")
        rows.extend(found)
        if checkpoint_cb:
            checkpoint_cb(rows)
    rows.sort(key=lambda row: (row["kode_indikator"], row["kode_negara"], str(row["tahun"])))
    return {"rows": rows, "error": "; ".join(errors) or None, "stop_reason": stop_reason,
            "sumber": "World Bank, World Development Indicators"}
