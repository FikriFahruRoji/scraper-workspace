"""Form and result views for the disaster source (GDACS and NASA EONET)."""
from datetime import date

import pandas as pd
import streamlit as st

SOURCES = ("bencana",)
NAMES = {"bencana": "Bencana (GDACS & NASA EONET)"}
REGIONS = {
    "indonesia": ("Indonesia", "Kejadian yang mengenai Indonesia (GDACS) atau berada di kotak wilayah Indonesia (EONET)"),
    "dunia": ("Seluruh dunia", "Tanpa batas wilayah; persempit periode agar hasil tidak terlalu banyak"),
}
PROVIDERS = {
    "semua": ("Keduanya", "GDACS ditambah kejadian EONET yang tidak berasal dari GDACS"),
    "gdacs": ("GDACS", "Banjir, siklon, gunung api, kekeringan, kebakaran hutan, dengan level peringatan"),
    "eonet": ("NASA EONET", "Juga tanah longsor, suhu ekstrem, serta debu dan kabut asap"),
}
ALERTS = {"green": "Hijau (semua)", "orange": "Oranye ke atas", "red": "Merah saja"}
MAX_DAYS = 366 * 10


def _scraper():
    from workspace_app.sources import load
    return load("bencana")


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _types_text(params):
    names = _scraper().TYPES
    picked = params.get("types") or []
    return ", ".join(names[k][0] for k in picked if k in names) if len(picked) < len(names) else "semua jenis"


def _period(params):
    if params.get("start_date") and params.get("end_date"):
        from workspace_app import ui
        return f"{ui.short_date(_day(params['start_date']))} – {ui.short_date(_day(params['end_date']))}"
    return "belum dipilih"


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, choice, date_bounds, number, time_limit

    scraper = _scraper()
    region = choice("Wilayah", REGIONS, key=_key(source, "region"))
    provider = choice("Sumber data", PROVIDERS, key=_key(source, "provider"))
    available = [k for k, (_, gdacs, eonet) in scraper.TYPES.items()
                 if (provider != "gdacs" or gdacs) and (provider != "eonet" or eonet)]
    key = _key(source, f"types-{provider}")
    types = st.multiselect("Jenis bencana", available, key=key, format_func=lambda k: scraper.TYPES[k][0],
                           **_initial(key, available, "default"),
                           help="Gempa tidak termasuk; pakai sumber Gempa (USGS).")
    min_alert = "green"
    if provider != "eonet":
        min_alert = st.selectbox("Level peringatan GDACS minimum", list(ALERTS), key=_key(source, "alert"),
                                 format_func=ALERTS.get,
                                 help="Hijau: dampak kecil. Oranye: sedang. Merah: besar. EONET tidak punya level peringatan.")
    start, end = date_bounds(source, days=365)
    limit = number(source, "count", "Jumlah kejadian maksimum", 0, 100_000, 0, step=100,
                   help="0 mengambil semua kejadian yang ditemukan dalam batas waktu.")
    st.caption("Data: GDACS (gdacs.org, PBB dan Komisi Eropa) dan NASA EONET, publik tanpa kunci. Siklon yang tidak "
               "menyentuh daratan Indonesia tidak ikut di wilayah Indonesia versi GDACS. Provinsi dan kabupaten/kota "
               "ditentukan dari titik kejadian (batas BPS 2020, 38 provinsi). Waktu ditulis dalam WIB dan UTC.")
    return {"region": region, "provider": provider, "types": types, "min_alert": min_alert,
            "start_date": start, "end_date": end, "count": limit, "max_minutes": time_limit(source)}


def check(params, source="bencana"):
    """Raise ValueError with a plain message when the form cannot be run."""
    scraper = _scraper()
    if params.get("region") not in REGIONS:
        raise ValueError("Wilayah tidak dikenali.")
    if params.get("provider") not in PROVIDERS:
        raise ValueError("Sumber data tidak dikenali.")
    if params.get("min_alert", "green") not in ALERTS:
        raise ValueError("Level peringatan tidak dikenali.")
    types = params.get("types") or []
    if not types:
        raise ValueError("Pilih minimal satu jenis bencana.")
    if any(k not in scraper.TYPES for k in types):
        raise ValueError("Jenis bencana tidak dikenali.")
    column = {"gdacs": 1, "eonet": 2}.get(params["provider"])
    if column and not any(scraper.TYPES[k][column] for k in types):
        raise ValueError(f"Jenis yang dipilih tidak tersedia di {PROVIDERS[params['provider']][0]}.")
    if not (params.get("start_date") and params.get("end_date")):
        raise ValueError("Pilih tanggal mulai dan selesai.")
    start, end = _day(params["start_date"]), _day(params["end_date"])
    if start > end:
        raise ValueError("Tanggal mulai harus sebelum tanggal selesai.")
    if end > date.today():
        raise ValueError("Tanggal selesai tidak boleh melewati hari ini.")
    if start < scraper.FIRST_DAY:
        raise ValueError("Arsip GDACS dan EONET dipakai mulai tahun 2000.")
    if (end - start).days > MAX_DAYS:
        raise ValueError("Pilih paling lama 10 tahun per pengambilan.")


def summary(source, params):
    rows = [("Sumber", NAMES[source]), ("Wilayah", REGIONS.get(params.get("region"), ("-",))[0]),
            ("Data dari", PROVIDERS.get(params.get("provider"), ("-",))[0]), ("Jenis", _types_text(params)),
            ("Periode", _period(params))]
    if params.get("provider") != "eonet":
        rows.append(("Peringatan", ALERTS.get(params.get("min_alert", "green"), "-")))
    rows.append(("Batas data", f"{params['count']:,}".replace(",", ".") if params.get("count") else "Semua yang ditemukan"))
    return rows


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params = job["params"]
    return [("Wilayah", REGIONS.get(params.get("region"), ("-",))[0]),
            ("Data dari", PROVIDERS.get(params.get("provider"), ("-",))[0]),
            ("Jenis", _types_text(params)), ("Periode", _period(params)),
            ("Peringatan minimum", ALERTS.get(params.get("min_alert", "green"), "-"))]


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    state, provider = st.session_state, params.get("provider", "semua")
    state[_key(source, "region")] = params.get("region", "indonesia")
    state[_key(source, "provider")] = provider
    state[_key(source, f"types-{provider}")] = list(params.get("types") or [])
    state[_key(source, "alert")] = params.get("min_alert", "green")
    state[_key(source, "count")] = params.get("count", 0)
    if params.get("start_date") and params.get("end_date"):
        state[_key(source, "start")], state[_key(source, "end")] = _day(params["start_date"]), _day(params["end_date"])


def hints(job):
    """What to try after an empty or failed run."""
    return ["Perluas periode atau pilih lebih banyak jenis bencana.",
            "Turunkan level peringatan GDACS minimum ke Hijau; sebagian besar kejadian berlevel hijau.",
            "Siklon di laut tanpa negara terdampak hanya muncul bila wilayah Seluruh dunia dipilih.",
            "Bila gdacs.org atau eonet.gsfc.nasa.gov lambat, coba lagi beberapa menit kemudian."]


def chart(frame, source="bencana"):
    """Counts per type and month, and a map of the events."""
    if not {"jenis", "lintang", "bujur"} <= set(frame):
        st.metric("Baris", len(frame))
        return
    cols = st.columns(3)
    cols[0].metric("Kejadian", f"{len(frame):,}".replace(",", "."), border=True)
    cols[1].metric("Jenis terbanyak", frame["jenis"].mode().iat[0] if len(frame) else "-", border=True)
    if "peringatan" in frame:
        cols[2].metric("Peringatan oranye/merah", int(frame["peringatan"].isin(["oranye", "merah"]).sum()), border=True)
    st.bar_chart(frame["jenis"].value_counts().rename_axis("jenis").rename("kejadian"), horizontal=True)
    if "provinsi" in frame and (frame["provinsi"].fillna("") != "").any():
        provinces = frame.loc[frame["provinsi"].fillna("") != ""]
        st.bar_chart(provinces.pivot_table(index="provinsi", columns="jenis", values="id_kejadian", aggfunc="count",
                                           fill_value=0).assign(total=lambda t: t.sum(axis=1))
                     .nlargest(15, "total").drop(columns="total"), horizontal=True)
        st.caption("15 provinsi dengan kejadian terbanyak. Provinsi dan kabupaten/kota ditentukan dari lintang dan "
                   "bujur titik kejadian.")
    if "mulai_wib" in frame:
        months = pd.to_datetime(frame["mulai_wib"].str[:7], format="%Y-%m", errors="coerce")
        table = frame.assign(bulan=months.dt.to_period("M").astype(str)).dropna(subset=["bulan"])
        table = table[table["bulan"] != "NaT"]
        if table["bulan"].nunique() > 1:
            st.bar_chart(table.pivot_table(index="bulan", columns="jenis", values="id_kejadian", aggfunc="count",
                                           fill_value=0))
    points = frame.dropna(subset=["lintang", "bujur"]).rename(columns={"lintang": "latitude", "bujur": "longitude"})
    if len(points):
        st.map(points, latitude="latitude", longitude="longitude", size=20000)
