"""Form and result views for the NASA FIRMS active-fire source."""
import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

SOURCES = ("firms",)
NAMES = {"firms": "Titik Api (NASA FIRMS)"}
COUNTRIES = {
    "Indonesia": "Indonesia", "Malaysia": "Malaysia", "Papua_New_Guinea": "Papua Nugini",
    "Timor-Leste": "Timor Leste", "Philippines": "Filipina", "Singapore": "Singapura", "Thailand": "Thailand",
    "Vietnam": "Vietnam", "Cambodia": "Kamboja", "Myanmar": "Myanmar", "Australia": "Australia",
    "India": "India", "China": "Tiongkok", "Japan": "Jepang", "Brazil": "Brasil", "Bolivia": "Bolivia",
    "Peru": "Peru", "Colombia": "Kolombia", "Canada": "Kanada", "Nigeria": "Nigeria",
}
OTHER = "__lain__"
MAX_YEARS = 15
MAP_POINTS = 5000


def _scraper():
    from workspace_app.sources import load
    return load("firms")


def _country_text(params):
    code = params.get("country", "")
    return COUNTRIES.get(code, code.replace("_", " ")) or tr("belum dipilih")


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, check as checkbox, number, time_limit

    scraper = _scraper()
    options = [*COUNTRIES, OTHER]
    picked = st.selectbox(tr("Negara"), options, key=_key(source, "country"),
                          format_func=lambda code: tr("Negara lain…") if code == OTHER else tr(COUNTRIES[code]))
    other = ""
    if picked == OTHER:
        other = st.text_input(tr("Nama negara (bahasa Inggris)"), key=_key(source, "other"),
                              placeholder=tr("Contoh: South Africa"),
                              help=tr("Ejaan mengikuti arsip FIRMS. Bila arsip tidak ditemukan, coba ejaan lain."))
    sensor = st.selectbox(tr("Sensor"), list(scraper.SENSORS), key=_key(source, "sensor"),
                          format_func=lambda code: scraper.SENSORS[code][0],
                          help=tr("VIIRS lebih rinci (375 m) dan tersedia sejak 2012. MODIS (1 km) tersedia sejak 2000."))
    newest, oldest = scraper.last_year(), scraper.SENSORS[sensor][1]
    left, right = st.columns(2)
    start = left.number_input(tr("Tahun mulai"), oldest, newest, key=_key(source, "start_year"),
                              **_initial(_key(source, "start_year"), newest, "value"))
    end = right.number_input(tr("Tahun selesai"), oldest, newest, key=_key(source, "end_year"),
                             **_initial(_key(source, "end_year"), newest, "value"))
    confident = checkbox(source, "confident", "Hanya keyakinan nominal dan tinggi",
                         help=tr("Buang deteksi berkeyakinan rendah (VIIRS: rendah; MODIS: di bawah 30)."))
    vegetation = checkbox(source, "vegetation", "Hanya kebakaran vegetasi",
                          help=tr("Buang gunung api aktif, sumber panas tetap (misalnya industri), dan lepas pantai."))
    limit = number(source, "count", tr("Jumlah titik maksimum"), 0, 2_000_000, 200_000, step=10_000,
                   help=tr("0 mengambil semua. Satu tahun VIIRS untuk Indonesia bisa ratusan ribu titik."))
    st.caption(tr('Data: NASA FIRMS, arsip standar per negara dan tahun (tanpa kunci). Arsip baru lengkap setelah tahun berakhir, jadi data terbaru sampai {0}. Excel maksimal 1.048.576 baris; untuk data lebih besar pakai CSV.', newest))
    return {"country": other.strip() if picked == OTHER else picked, "sensor": sensor, "start_year": int(start),
            "end_year": int(end), "only_confident": confident, "only_vegetation": vegetation, "count": limit,
            "max_minutes": time_limit(source)}


def check(params, source="firms"):
    """Raise ValueError with a plain message when the form cannot be run."""
    scraper = _scraper()
    params["country"] = scraper.normalize_country(params.get("country"))
    if params["sensor"] not in scraper.SENSORS:
        raise ValueError(tr("Sensor tidak dikenali."))
    first, newest = scraper.SENSORS[params["sensor"]][1], scraper.last_year()
    if params["start_year"] > params["end_year"]:
        raise ValueError(tr("Tahun mulai harus sebelum atau sama dengan tahun selesai."))
    if params["start_year"] < first:
        raise ValueError(tr('Sensor ini mulai merekam pada {0}.', first))
    if params["end_year"] > newest:
        raise ValueError(tr('Arsip FIRMS baru sampai {0}.', newest))
    if params["end_year"] - params["start_year"] + 1 > MAX_YEARS:
        raise ValueError(tr('Pilih paling banyak {0} tahun per pengambilan.', MAX_YEARS))


def summary(source, params):
    scraper = _scraper()
    flags = [name for key, name in (("only_confident", "keyakinan nominal+"), ("only_vegetation", "vegetasi saja"))
             if params.get(key)]
    return [("Sumber", tr(NAMES[source])), ("Negara", _country_text(params)),
            ("Sensor", scraper.SENSORS.get(params["sensor"], (params["sensor"],))[0]),
            ("Tahun", f"{params['start_year']} – {params['end_year']}"),
            ("Filter", ", ".join(flags) or tr("tidak ada")),
            ("Batas data", f"{params['count']:,}".replace(",", ".") if params.get("count") else tr("semua")),
            ("Batas waktu", tr("{0} menit", params['max_minutes']))]


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params, scraper = job["params"], _scraper()
    flags = [name for key, name in (("only_confident", "keyakinan nominal+"), ("only_vegetation", "vegetasi saja"))
             if params.get(key)]
    return [("Negara", _country_text(params)),
            ("Sensor", scraper.SENSORS.get(params.get("sensor"), ("-",))[0]),
            ("Tahun", f"{params.get('start_year')} – {params.get('end_year')}"),
            ("Filter", ", ".join(flags) or tr("tidak ada"))]


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    state, country = st.session_state, params.get("country", "Indonesia")
    state[_key(source, "country")] = country if country in COUNTRIES else OTHER
    state[_key(source, "other")] = "" if country in COUNTRIES else country.replace("_", " ")
    state[_key(source, "sensor")] = params.get("sensor", "viirs-snpp")
    for name in ("start_year", "end_year"):
        if name in params:
            state[_key(source, name)] = params[name]
    state[_key(source, "confident")] = bool(params.get("only_confident"))
    state[_key(source, "vegetation")] = bool(params.get("only_vegetation"))
    state[_key(source, "count")] = params.get("count", 0)


def hints(job):
    """What to try after an empty or failed run."""
    return ["Arsip FIRMS hanya lengkap sampai tahun kalender terakhir yang sudah berakhir.",
            "Periksa ejaan negara (bahasa Inggris, mis. Papua New Guinea) dan tahun mulai sensor.",
            "Longgarkan filter keyakinan atau vegetasi bila hasilnya kosong.",
            "Bila server NASA lambat, pilih lebih sedikit tahun per pengambilan."]


def chart(frame, source="firms"):
    """Hotspot counts per month and a map of a sample of the points."""
    if not {"waktu_wib", "lintang", "bujur"} <= set(frame):
        st.metric(tr("Baris"), len(frame))
        return
    cols = st.columns(3)
    cols[0].metric(tr("Titik api"), f"{len(frame):,}".replace(",", "."), border=True)
    if "frp_mw" in frame and frame["frp_mw"].notna().any():
        cols[1].metric(tr("FRP tertinggi"), f"{frame['frp_mw'].max():,.0f} MW".replace(",", "."), border=True)
    if "siang_malam" in frame:
        cols[2].metric(tr("Terdeteksi malam"), f"{(frame['siang_malam'] == 'malam').mean():.0%}", border=True)
    months = pd.to_datetime(frame["waktu_wib"].str[:7], format="%Y-%m", errors="coerce").dropna()
    if months.nunique() > 1:
        st.bar_chart(months.dt.to_period("M").value_counts().sort_index().rename_axis("bulan").rename("titik api"))
    points = frame.dropna(subset=["lintang", "bujur"])
    shown = points.sample(MAP_POINTS, random_state=0) if len(points) > MAP_POINTS else points
    from workspace_app import map_ui
    map_ui.point_map(shown, "lintang", "bujur", key_prefix="firms-map",
                     weight_col="frp_mw" if "frp_mw" in shown else None, color=(227, 80, 30, 190))
    if len(points) > MAP_POINTS:
        st.caption(f"Peta menampilkan contoh acak {MAP_POINTS:,} titik. Tabel di tab Data memuat semuanya.".replace(",", "."))
