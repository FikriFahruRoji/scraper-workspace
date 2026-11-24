"""Form and result views for the Harga Pangan (Bank Indonesia PIHPS) source."""
from datetime import date

import pandas as pd
import streamlit as st

SOURCES = ("pangan",)
NAMES = {"pangan": "Harga Pangan (PIHPS BI)"}
MAX_PROVINCES = 40
MAX_DAYS = 3660


def _scraper():
    from workspace_app.sources import load
    return load("pangan")


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, date_bounds, time_limit

    scraper = _scraper()
    chosen = st.multiselect("Wilayah", list(scraper.PROVINCES), key=_key(source, "provinces"),
                            format_func=scraper.PROVINCES.get,
                            **_initial(_key(source, "provinces"), [scraper.NATIONAL], "default"),
                            help="Nasional adalah rata-rata seluruh pasar yang dipantau. Pilih provinsi untuk harga daerah.")
    picked = st.multiselect("Komoditas", list(scraper.CATEGORIES), key=_key(source, "categories"),
                            format_func=scraper.CATEGORIES.get,
                            **_initial(_key(source, "categories"), ["1", "5", "7"], "default"),
                            help="Kosongkan untuk semua 10 kelompok (21 komoditas).")
    kind = st.selectbox("Jenis pasar", list(scraper.PRICE_TYPES), key=_key(source, "price_type"),
                        format_func=scraper.PRICE_TYPES.get)
    start, end = date_bounds(source, days=90)
    st.caption("Sumber: Bank Indonesia, Pusat Informasi Harga Pangan Strategis Nasional (hargapangan). "
               "Harga rata-rata harian dalam rupiah, hanya hari kerja.")
    return {"provinces": chosen, "categories": picked, "price_type": kind, "start_date": start,
            "end_date": end, "max_minutes": time_limit(source)}


def check(params, source="pangan"):
    """Raise ValueError with a plain message when the form cannot be run."""
    scraper = _scraper()
    if not params["provinces"]:
        raise ValueError("Pilih minimal satu wilayah.")
    if len(params["provinces"]) > MAX_PROVINCES:
        raise ValueError("Terlalu banyak wilayah dipilih.")
    if not (params.get("start_date") and params.get("end_date")):
        raise ValueError("Pilih tanggal mulai dan selesai.")
    start, end = _day(params["start_date"]), _day(params["end_date"])
    if start > end:
        raise ValueError("Tanggal mulai harus sebelum tanggal selesai.")
    if end > date.today():
        raise ValueError("Tanggal selesai tidak boleh melewati hari ini.")
    if (end - start).days > MAX_DAYS:
        raise ValueError("Rentang tanggal terlalu panjang. Pilih paling banyak 10 tahun per pengambilan.")
    if params["price_type"] not in scraper.PRICE_TYPES:
        raise ValueError("Jenis pasar tidak dikenali.")


def _names(codes, names, empty):
    shown = [names.get(code, code) for code in codes]
    if not shown:
        return empty
    return ", ".join(shown[:3]) + (f" dan {len(shown) - 3} lainnya" if len(shown) > 3 else "")


def _period(params):
    from workspace_app import ui
    if params.get("start_date") and params.get("end_date"):
        return f"{ui.short_date(_day(params['start_date']))} – {ui.short_date(_day(params['end_date']))}"
    return "belum dipilih"


def summary(source, params):
    scraper = _scraper()
    return [("Sumber", NAMES[source]), ("Wilayah", _names(params["provinces"], scraper.PROVINCES, "belum dipilih")),
            ("Komoditas", _names(params["categories"], scraper.CATEGORIES, "Semua komoditas")),
            ("Jenis pasar", scraper.PRICE_TYPES.get(params["price_type"], params["price_type"])),
            ("Periode", _period(params)), ("Batas waktu", f"{params['max_minutes']} menit")]


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params, scraper = job["params"], _scraper()
    return [("Wilayah", _names(params.get("provinces", []), scraper.PROVINCES, "-")),
            ("Komoditas", _names(params.get("categories", []), scraper.CATEGORIES, "Semua komoditas")),
            ("Jenis pasar", scraper.PRICE_TYPES.get(params.get("price_type"), "-")),
            ("Periode", _period(params))]


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    scraper, state = _scraper(), st.session_state
    state[_key(source, "provinces")] = [c for c in params.get("provinces", []) if c in scraper.PROVINCES]
    state[_key(source, "categories")] = [c for c in params.get("categories", []) if c in scraper.CATEGORIES]
    state[_key(source, "price_type")] = params.get("price_type", "1")
    if params.get("start_date") and params.get("end_date"):
        state[_key(source, "start")], state[_key(source, "end")] = _day(params["start_date"]), _day(params["end_date"])


def hints(job):
    """What to try after an empty or failed run."""
    return ["Harga hanya dicatat pada hari kerja; perluas rentang tanggal.",
            "Beberapa provinsi atau jenis pasar tidak punya data pada periode tertentu; coba Nasional atau Pasar Tradisional.",
            "Bila situs hargapangan Bank Indonesia sedang lambat, kurangi jumlah provinsi atau persempit tanggal."]


def chart(frame, source="pangan"):
    """Price over time, one line per commodity, for one region."""
    if not {"tanggal", "harga", "komoditas", "provinsi"} <= set(frame):
        st.metric("Baris", len(frame))
        return
    place = st.selectbox("Wilayah", sorted(frame["provinsi"].unique()), key="pangan-chart-place")
    subset = frame[frame["provinsi"] == place].copy()
    subset["tanggal"] = pd.to_datetime(subset["tanggal"], errors="coerce")
    top = subset.groupby("komoditas").size().sort_values(ascending=False).head(8).index
    table = subset[subset["komoditas"].isin(top)].pivot_table(index="tanggal", columns="komoditas", values="harga")
    if table.empty:
        st.info("Tidak ada harga untuk wilayah ini.")
        return
    st.line_chart(table)
    if subset["komoditas"].nunique() > 8:
        st.caption("Grafik menampilkan 8 komoditas dengan data terbanyak. Tabel di tab Data memuat semuanya.")
