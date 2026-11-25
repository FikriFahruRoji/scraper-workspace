"""Forms and result views for the USGS earthquake and Open-Meteo weather sources."""
from datetime import date

import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

SOURCES = ("usgs", "openmeteo")
NAMES = {"usgs": "Gempa USGS", "openmeteo": "Cuaca Open-Meteo"}
REGIONS = {
    "indonesia": ("Indonesia", "Kotak lintang 11,5°LS–6,5°LU dan bujur 94°–142°BT, termasuk sedikit wilayah tetangga"),
    "dunia": ("Seluruh dunia", "Tanpa batas wilayah; naikkan magnitudo minimum agar hasil tidak terlalu banyak"),
    "custom": ("Kotak sendiri", "Isi batas lintang dan bujur"),
}
MODES = {"history": ("Riwayat", "Cuaca masa lalu sampai hari ini"),
         "forecast": ("Prakiraan", "Perkiraan sampai 16 hari ke depan")}
INTERVALS = {"daily": ("Harian", "Satu baris per hari"), "hourly": ("Per jam", "Satu baris per jam")}
VARIABLES = {
    "daily": {
        "temperature_2m_max": "Suhu maksimum (°C)", "temperature_2m_min": "Suhu minimum (°C)",
        "temperature_2m_mean": "Suhu rata-rata (°C)", "precipitation_sum": "Curah hujan (mm)",
        "precipitation_hours": "Jam hujan", "wind_speed_10m_max": "Kecepatan angin maksimum (km/jam)",
        "wind_gusts_10m_max": "Hembusan angin maksimum (km/jam)", "shortwave_radiation_sum": "Radiasi matahari (MJ/m²)",
        "et0_fao_evapotranspiration": "Evapotranspirasi (mm)",
    },
    "hourly": {
        "temperature_2m": "Suhu (°C)", "relative_humidity_2m": "Kelembapan (%)", "precipitation": "Curah hujan (mm)",
        "wind_speed_10m": "Kecepatan angin (km/jam)", "cloud_cover": "Tutupan awan (%)",
        "surface_pressure": "Tekanan udara (hPa)",
    },
}
DEFAULT_VARIABLES = {"daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"],
                     "hourly": ["temperature_2m", "relative_humidity_2m", "precipitation"]}
MAX_LOCATIONS = 20
FIRST_DAY = date(1940, 1, 1)


def _day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _places(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, choice, date_bounds, number

    if source == "usgs":
        region = choice(tr("Wilayah"), REGIONS, key=_key(source, "region"))
        box = {}
        if region == "custom":
            first, second = st.columns(2)
            third, fourth = st.columns(2)
            def corner(column, label, low, high, default, name, **kwargs):
                key = _key(source, name)
                return column.number_input(label, low, high, key=key, **_initial(key, default, "value"), **kwargs)

            box = {"south": corner(first, "Lintang selatan", -90.0, 90.0, -11.5, "south",
                                   help=tr("Selatan bernilai negatif, misalnya -11,5 untuk 11,5°LS.")),
                   "north": corner(second, "Lintang utara", -90.0, 90.0, 6.5, "north"),
                   "west": corner(third, "Bujur barat", -180.0, 180.0, 94.0, "west"),
                   "east": corner(fourth, "Bujur timur", -180.0, 180.0, 142.0, "east")}
        minimum = st.number_input(tr("Magnitudo minimum"), 0.0, 9.5, step=0.1, format="%.1f",
                                  key=_key(source, "minmag"), **_initial(_key(source, "minmag"), 4.5),
                                  help=tr("Gempa kecil sangat banyak. Untuk Indonesia, M 4,5+ berarti sekitar 1.000 gempa per tahun."))
        start, end = date_bounds(source)
        limit = number(source, "count", tr("Jumlah gempa maksimum"), 0, 200_000, 0, step=100,
                       help=tr("0 mengambil semua gempa yang ditemukan dalam batas waktu."))
        st.caption(tr("Data: USGS Earthquake Catalog, domain publik. Waktu ditulis dalam WIB dan UTC."))
        return {"region": region, "min_magnitude": minimum, "start_date": start, "end_date": end, "count": limit,
                **box}

    places = st.text_area(tr("Lokasi"), key=_key(source, "places"), height=110,
                          placeholder="Bandung\nSurabaya\n-6.2,106.8",
                          help=tr("Satu lokasi per baris: nama tempat atau koordinat lintang,bujur. "
                               "Nama dicocokkan ke hasil pertama pencarian lokasi; hasilnya tertulis di kolom lokasi."),
                          **_initial(_key(source, "places"), "Jakarta"))
    mode = choice(tr("Jenis data"), MODES, key=_key(source, "mode"))
    interval = choice(tr("Rincian waktu"), INTERVALS, key=_key(source, "interval"))
    options = VARIABLES[interval]
    variables = st.multiselect(tr("Variabel"), list(options), key=_key(source, f"vars-{interval}"),
                               format_func=lambda key: tr(options[key]),
                               **_initial(_key(source, f"vars-{interval}"), DEFAULT_VARIABLES[interval], "default"))
    start = end = None
    days = 7
    if mode == "history":
        start, end = date_bounds(source)
    else:
        days = number(source, "days", tr("Jumlah hari prakiraan"), 1, 16, 7)
    drop_empty = st.checkbox(tr("Buang baris tanpa nilai"), True, key=_key(source, "drop_empty"),
                             help=tr("Beberapa hari terakhir di arsip kadang belum terisi."))
    st.caption(tr("Data: Open-Meteo.com (lisensi CC BY 4.0), untuk penggunaan non-komersial. "
               "Waktu memakai zona waktu tiap lokasi."))
    return {"locations": _places(places), "mode": mode, "interval": interval, "variables": variables,
            "start_date": start, "end_date": end, "forecast_days": days, "drop_empty": drop_empty}


def check(params, source):
    """Raise ValueError with a plain message when the form cannot be run."""
    if source == "usgs":
        if not (params.get("start_date") and params.get("end_date")):
            raise ValueError(tr("Pilih tanggal mulai dan selesai."))
        start, end = _day(params["start_date"]), _day(params["end_date"])
        if start > end:
            raise ValueError(tr("Tanggal mulai harus sebelum tanggal selesai."))
        if end > date.today():
            raise ValueError(tr("Tanggal selesai tidak boleh melewati hari ini."))
        if start < date(1900, 1, 1):
            raise ValueError(tr("Katalog USGS dimulai tahun 1900."))
        if not 0 <= params["min_magnitude"] <= 10:
            raise ValueError(tr("Magnitudo minimum harus antara 0 dan 10."))
        if params["region"] == "custom":
            south, north = params.get("south"), params.get("north")
            west, east = params.get("west"), params.get("east")
            if None in (south, north, west, east):
                raise ValueError(tr("Isi batas lintang dan bujur."))
            if not (-90 <= south <= north <= 90):
                raise ValueError(tr("Lintang selatan harus lebih kecil dari lintang utara, dalam -90 sampai 90."))
            if not (-180 <= west <= east <= 180):
                raise ValueError(tr("Bujur barat harus lebih kecil dari bujur timur, dalam -180 sampai 180."))
        elif params["region"] not in REGIONS:
            raise ValueError(tr("Wilayah tidak dikenali."))
        return
    if not params["locations"]:
        raise ValueError(tr("Isi minimal satu lokasi."))
    if len(params["locations"]) > MAX_LOCATIONS:
        raise ValueError(tr('Maksimal {0} lokasi per pengambilan.', MAX_LOCATIONS))
    if not params["variables"]:
        raise ValueError(tr("Pilih minimal satu variabel."))
    if params["mode"] == "history":
        if not (params.get("start_date") and params.get("end_date")):
            raise ValueError(tr("Pilih tanggal mulai dan selesai."))
        start, end = _day(params["start_date"]), _day(params["end_date"])
        if start > end:
            raise ValueError(tr("Tanggal mulai harus sebelum tanggal selesai."))
        if end > date.today():
            raise ValueError(tr("Riwayat hanya sampai hari ini. Pilih Prakiraan untuk data ke depan."))
        if start < FIRST_DAY:
            raise ValueError(tr("Arsip cuaca dimulai tahun 1940."))
        if params["interval"] == "hourly" and (end - start).days > 366:
            raise ValueError(tr("Data per jam dibatasi 1 tahun per pengambilan. Persempit tanggal atau pilih Harian."))


def _region(params):
    if params.get("region") == "custom":
        return (f"Kotak {params.get('south')}…{params.get('north')} LU, "
                f"{params.get('west')}…{params.get('east')} BT")
    return REGIONS.get(params.get("region"), ("-",))[0]


def _places_text(places):
    shown = ", ".join(places[:3])
    return shown + (f" dan {len(places) - 3} lainnya" if len(places) > 3 else "")


def _period(params):
    if params.get("mode") == "forecast":
        return f"Prakiraan {params.get('forecast_days')} hari"
    if params.get("start_date") and params.get("end_date"):
        from workspace_app import ui
        return f"{ui.short_date(_day(params['start_date']))} – {ui.short_date(_day(params['end_date']))}"
    return "belum dipilih"


def summary(source, params):
    if source == "usgs":
        return [("Sumber", tr(NAMES[source])), ("Wilayah", _region(params)),
                ("Magnitudo", f"{params['min_magnitude']:.1f}+".replace(".", ",")), ("Periode", _period(params)),
                ("Batas data", f"{params['count']:,}".replace(",", ".") if params.get("count") else tr("Semua yang ditemukan"))]
    return [("Sumber", tr(NAMES[source])), ("Lokasi", _places_text(params["locations"]) or "belum diisi"),
            ("Periode", _period(params)), ("Rincian", tr(INTERVALS[params["interval"]][0])),
            ("Variabel", tr("{0} variabel", len(params['variables'])))]


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params = job["params"]
    if job["source"] == "usgs":
        return [("Wilayah", _region(params)), ("Magnitudo minimum", f"{params.get('min_magnitude', 0):.1f}".replace(".", ",")),
                ("Periode", _period(params))]
    names = VARIABLES[params["interval"]]
    return [("Lokasi", "; ".join(params.get("locations", []))), ("Jenis data", MODES[params["mode"]][0]),
            ("Periode", _period(params)), ("Rincian", tr(INTERVALS[params["interval"]][0])),
            ("Variabel", ", ".join(names.get(code, code) for code in params.get("variables", [])))]


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    state = st.session_state
    if source == "usgs":
        state[_key(source, "region")] = params.get("region", "indonesia")
        state[_key(source, "minmag")] = float(params.get("min_magnitude", 4.5))
        state[_key(source, "count")] = params.get("count", 0)
        for name in ("south", "north", "west", "east"):
            if params.get(name) is not None:
                state[_key(source, name)] = params[name]
        if params.get("start_date") and params.get("end_date"):
            state[_key(source, "start")], state[_key(source, "end")] = _day(params["start_date"]), _day(params["end_date"])
        return
    state[_key(source, "places")] = "\n".join(params.get("locations", []))
    state[_key(source, "mode")] = params.get("mode", "history")
    interval = params.get("interval", "daily")
    state[_key(source, "interval")] = interval
    state[_key(source, f"vars-{interval}")] = [v for v in params.get("variables", []) if v in VARIABLES[interval]]
    if params.get("start_date") and params.get("end_date"):
        state[_key(source, "start")], state[_key(source, "end")] = _day(params["start_date"]), _day(params["end_date"])
    state[_key(source, "days")] = params.get("forecast_days", 7)
    state[_key(source, "drop_empty")] = params.get("drop_empty", True)


def hints(job):
    """What to try after an empty or failed run."""
    if job["source"] == "usgs":
        return ["Turunkan magnitudo minimum atau perluas rentang tanggal; gempa kecil jarang tercatat di daerah tertentu.",
                "Pastikan kotak koordinat benar: lintang selatan bernilai negatif dan bujur barat lebih kecil dari bujur timur.",
                "Bila earthquake.usgs.gov sedang lambat, coba lagi beberapa menit kemudian."]
    return ["Periksa penulisan nama lokasi, atau tulis koordinat lintang,bujur (misalnya -6.2,106.8).",
            "Pastikan tanggal tidak melewati hari ini dan tidak sebelum 1940.",
            "Kurangi jumlah lokasi atau persempit tanggal bila Open-Meteo membatasi permintaan."]


def chart(frame, source):
    """Earthquakes on a map; weather as one line per place."""
    if source == "usgs" and {"lintang", "bujur", "magnitudo"} <= set(frame):
        cols = st.columns(3)
        cols[0].metric(tr("Gempa"), len(frame), border=True)
        cols[1].metric(tr("Magnitudo tertinggi"), f"{frame['magnitudo'].max():.1f}".replace(".", ","), border=True)
        cols[2].metric(tr("Terdalam"), f"{frame['kedalaman_km'].max():.0f} km", border=True)
        if "waktu" in frame and len(frame) > 1:
            months = pd.to_datetime(frame["waktu"].str[:7], format="%Y-%m", errors="coerce").dropna()
            if months.nunique() > 1:
                st.bar_chart(months.dt.to_period("M").value_counts().sort_index().rename_axis("bulan").rename("gempa"))
        from workspace_app import map_ui
        map_ui.point_map(frame, "lintang", "bujur", key_prefix="nat-map", weight_col="magnitudo",
                         color=(208, 80, 60, 195))
        st.caption(tr("Lingkaran lebih besar berarti magnitudo lebih besar."))
        return
    axis = next((name for name in ("waktu", "tanggal") if name in frame), None)
    numbers = [name for name in frame.select_dtypes("number") if name not in ("lintang", "bujur", "ketinggian_m")]
    if not axis or not numbers or "lokasi" not in frame:
        st.metric(tr("Baris"), len(frame))
        return
    column = st.selectbox(tr("Variabel"), numbers, key="nature-chart")
    table = frame.pivot_table(index=axis, columns="lokasi", values=column)
    st.line_chart(table)
    if frame["lokasi"].nunique() > 1:
        st.caption(tr("Satu garis per lokasi."))
