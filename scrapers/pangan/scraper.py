"""Daily food prices from Bank Indonesia's PIHPS (Pusat Informasi Harga Pangan Strategis Nasional).

Public data, no key or login. PIHPS reports the average price of 21 strategic commodities per province
(or nationally) and market type, one value per working day, in rupiah. One request serves one province
and one date window; long windows are slow and can time out on the server, so the range is cut
into 30-day windows. These are the endpoints behind bi.go.id/hargapangan, not a documented API.
"""
import json
import re
import time
from datetime import date, datetime, timedelta
from time import monotonic

from workspace_app.open_data import ApiError, get

BASE = "https://www.bi.go.id/hargapangan/WebSite/TabelHarga/GetGridDataDaerah"
WINDOW_DAYS = 30
PAUSE = 0.6
NATIONAL = "0"
PROVINCES = {
    NATIONAL: "Nasional (rata-rata)",
    "1": "Aceh", "2": "Sumatera Utara", "3": "Sumatera Barat", "4": "Riau", "5": "Kepulauan Riau",
    "6": "Jambi", "7": "Bengkulu", "8": "Sumatera Selatan", "9": "Kepulauan Bangka Belitung", "10": "Lampung",
    "11": "Banten", "12": "Jawa Barat", "13": "DKI Jakarta", "14": "Jawa Tengah", "15": "DI Yogyakarta",
    "16": "Jawa Timur", "17": "Bali", "18": "Nusa Tenggara Barat", "19": "Nusa Tenggara Timur",
    "20": "Kalimantan Barat", "21": "Kalimantan Selatan", "22": "Kalimantan Tengah", "23": "Kalimantan Timur",
    "24": "Kalimantan Utara", "25": "Gorontalo", "26": "Sulawesi Selatan", "27": "Sulawesi Tenggara",
    "28": "Sulawesi Tengah", "29": "Sulawesi Utara", "30": "Sulawesi Barat", "31": "Maluku",
    "32": "Maluku Utara", "33": "Papua", "34": "Papua Barat",
}
CATEGORIES = {
    "1": "Beras", "2": "Daging Ayam", "3": "Daging Sapi", "4": "Telur Ayam", "5": "Bawang Merah",
    "6": "Bawang Putih", "7": "Cabai Merah", "8": "Cabai Rawit", "9": "Minyak Goreng", "10": "Gula Pasir",
}
PRICE_TYPES = {"1": "Pasar Tradisional", "2": "Pasar Modern", "3": "Pedagang Besar", "4": "Produsen"}
_DATE_KEY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def windows(start, end, days=WINDOW_DAYS):
    """Consecutive (first, last) date pairs of at most `days` days covering start..end."""
    first = start
    while first <= end:
        last = min(first + timedelta(days=days - 1), end)
        yield first, last
        first = last + timedelta(days=1)


def parse_price(value):
    """'16,550' -> 16550; '-', '' and anything unreadable -> None."""
    digits = re.sub(r"[.,\s]", "", str(value or ""))
    return int(digits) if digits.isdigit() else None


def parse_grid(records, province, price_type):
    """Long rows from one grid response; the level-1 rows only name the category of the rows after them."""
    category = ""
    for record in records:
        if record.get("level") == 1:
            category = str(record.get("name", "")).strip()
            continue
        name = str(record.get("name", "")).strip()
        for key, value in record.items():
            if not _DATE_KEY.match(key):
                continue
            price = parse_price(value)
            if price is None:
                continue
            yield {
                "tanggal": datetime.strptime(key, "%d/%m/%Y").date().isoformat(),
                "provinsi": province, "jenis_pasar": price_type, "kategori": category,
                "komoditas": name, "harga": price,
            }


def _fetch(province_id, category_id, price_type_id, start, end, stop, log):
    url = (f"{BASE}?price_type_id={price_type_id}&comcat_id={f'com_{category_id}' if category_id else ''}"
           f"&province_id={'' if province_id == NATIONAL else province_id}&regency_id=&market_id="
           f"&tipe_laporan=1&start_date={start.isoformat()}&end_date={end.isoformat()}")
    try:
        data = json.loads(get(url, timeout=60, retries=3, pause=2, stop=stop, log=log))
    except ValueError as exc:
        raise ApiError("Jawaban PIHPS tidak bisa dibaca.") from exc
    return data.get("data") or []


def collect_prices(provinces, categories, price_type="1", start_date=None, end_date=None, max_minutes=10,
                   stop_event=None, checkpoint_cb=None, log_cb=None, **_ignored):
    """One row per day, province, and commodity. `categories` empty or all ten means every commodity."""
    log = log_cb or (lambda message: None)
    start, end = _day(start_date), _day(end_date)
    deadline = monotonic() + max_minutes * 60 if max_minutes else None
    wanted = {CATEGORIES[c] for c in categories if c in CATEGORIES}
    single = categories[0] if len(categories) == 1 else ""
    price_name = PRICE_TYPES.get(str(price_type), str(price_type))
    spans = list(windows(start, end))
    rows, errors, stop_reason = [], [], None
    total = len(provinces) * len(spans)
    done = 0
    for province_id in provinces:
        name = PROVINCES.get(province_id, province_id)
        for first, last in spans:
            if stop_event is not None and stop_event.is_set():
                stop_reason = "dihentikan"
                break
            if deadline and monotonic() > deadline:
                stop_reason = "waktu"
                break
            done += 1
            log(f"{name} · {first.isoformat()} – {last.isoformat()} (permintaan {done} dari {total})")
            try:
                records = _fetch(province_id, single, price_type, first, last, stop_event, log)
            except ApiError as exc:
                errors.append(f"{name} {first.isoformat()}: {exc}")
                log(f"  gagal: {exc}")
                continue
            found = [row for row in parse_grid(records, name, price_name)
                     if not wanted or len(wanted) == len(CATEGORIES) or row["kategori"] in wanted]
            log(f"  {len(found)} harga")
            rows.extend(found)
            if checkpoint_cb:
                checkpoint_cb(rows)
            time.sleep(PAUSE)
        if stop_reason:
            break
    rows.sort(key=lambda row: (row["provinsi"], row["komoditas"], row["tanggal"]))
    result = {"rows": rows, "error": "; ".join(errors) or None, "stop_reason": stop_reason,
              "sumber": "Bank Indonesia, PIHPS (hargapangan)"}
    if not rows and not errors and not stop_reason:
        result["message"] = ("PIHPS tidak mencatat harga pada rentang ini. Harga hanya ada pada hari kerja; "
                             "perluas tanggal atau pilih jenis pasar lain.")
    return result
