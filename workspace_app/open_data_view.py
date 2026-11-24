"""Forms and result views for the World Bank and IMF sources: public statistics, no login."""
from datetime import date
import re

import pandas as pd
import streamlit as st

from workspace_app import firms_view, nature_view, pangan_view, sinta_view
from workspace_app.countries import COUNTRIES, clean_codes

SOURCES = ("worldbank", "imf")


def _other(source):
    """The view module that owns `source` when it is not a World Bank or IMF source, else None."""
    return next((module for module in (firms_view, nature_view, pangan_view, sinta_view) if source in module.SOURCES), None)

NAMES = {"worldbank": "World Bank", "imf": "IMF (World Economic Outlook)"}
FINDER = {
    "worldbank": "Cari kode lain di data.worldbank.org/indicator (contoh: NY.GDP.MKTP.CD).",
    "imf": "Cari kode lain di imf.org/external/datamapper (contoh: NGDP_RPCH).",
}
INDICATORS = {
    "worldbank": {
        "NY.GDP.MKTP.CD": "PDB (US$ harga berlaku)",
        "NY.GDP.MKTP.KD.ZG": "Pertumbuhan PDB (% per tahun)",
        "NY.GDP.PCAP.CD": "PDB per kapita (US$)",
        "FP.CPI.TOTL.ZG": "Inflasi, harga konsumen (% per tahun)",
        "SL.UEM.TOTL.ZS": "Pengangguran (% angkatan kerja)",
        "SP.POP.TOTL": "Penduduk total",
        "SP.URB.TOTL.IN.ZS": "Penduduk perkotaan (% total)",
        "SP.DYN.LE00.IN": "Harapan hidup saat lahir (tahun)",
        "SI.POV.GINI": "Indeks Gini",
        "SE.ADT.LITR.ZS": "Melek huruf dewasa (%)",
        "SE.XPD.TOTL.GD.ZS": "Belanja pendidikan (% PDB)",
        "SH.XPD.CHEX.GD.ZS": "Belanja kesehatan (% PDB)",
        "NE.EXP.GNFS.ZS": "Ekspor barang dan jasa (% PDB)",
        "NE.IMP.GNFS.ZS": "Impor barang dan jasa (% PDB)",
        "BX.KLT.DINV.WD.GD.ZS": "Investasi asing langsung, masuk (% PDB)",
        "IT.NET.USER.ZS": "Pengguna internet (% penduduk)",
        "EN.GHG.CO2.PC.CE.AR5": "Emisi CO2 per kapita (ton)",
    },
    "imf": {
        "NGDP_RPCH": "Pertumbuhan PDB riil (% per tahun)",
        "NGDPD": "PDB harga berlaku (US$)",
        "NGDPDPC": "PDB per kapita (US$)",
        "PPPPC": "PDB per kapita, PPP (dolar internasional)",
        "PCPIPCH": "Inflasi, rata-rata tahunan (%)",
        "LUR": "Tingkat pengangguran (%)",
        "LP": "Penduduk (jiwa)",
        "GGXWDG_NGDP": "Utang pemerintah (% PDB)",
        "BCA_NGDPD": "Transaksi berjalan (% PDB)",
        "GGXCNL_NGDP": "Pinjaman bersih pemerintah (% PDB)",
    },
}
CODE_RE = re.compile(r"^[A-Z0-9_.]+$")


def _text_codes(text):
    return [part for part in dict.fromkeys(re.split(r"[\s,;]+", text.upper().strip())) if part]


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    if module := _other(source):
        return module.form(source)
    from workspace_app.views import _initial, _key, time_limit

    this_year = date.today().year
    with st.container():
        chosen = st.multiselect("Negara", list(COUNTRIES), key=_key(source, "countries"),
                                format_func=lambda code: f"{COUNTRIES[code]} ({code})",
                                **_initial(_key(source, "countries"), ["IDN"], "default"))
        everything = st.checkbox("Semua negara dan wilayah", key=_key(source, "all"),
                                 help="Termasuk kelompok seperti dunia, ASEAN, dan Uni Eropa. "
                                      "Hasilnya bisa sangat banyak.")
        extra = st.text_input("Kode negara lain (opsional)", key=_key(source, "extra_countries"),
                              placeholder="Contoh: NOR, CHE", disabled=everything,
                              help="Kode tiga huruf ISO, dipisah koma atau spasi.")
        picked = st.multiselect("Indikator", list(INDICATORS[source]), key=_key(source, "indicators"),
                                format_func=lambda code: f"{INDICATORS[source][code]} ({code})",
                                **_initial(_key(source, "indicators"), [next(iter(INDICATORS[source]))], "default"))
        more = st.text_input("Kode indikator lain (opsional)", key=_key(source, "extra_indicators"),
                             placeholder="Contoh: " + ("SP.POP.GROW" if source == "worldbank" else "GGR_NGDP"),
                             help=FINDER[source])
        left, right = st.columns(2)
        start = int(left.number_input("Tahun mulai", 1960, this_year + 10, 2010, key=_key(source, "start_year")))
        end = int(right.number_input(
            "Tahun selesai", 1960, this_year + 10, this_year, key=_key(source, "end_year"),
            help="IMF memuat proyeksi beberapa tahun ke depan; tiap baris diberi tanda aktual atau proyeksi."
            if source == "imf" else None))
        drop_empty = True
        if source == "worldbank":
            drop_empty = st.checkbox("Buang tahun tanpa nilai", True, key=_key(source, "drop_empty"),
                                     help="Banyak indikator belum punya nilai untuk tahun terbaru.")
    params = {"countries": ["all"] if everything else list(dict.fromkeys([*chosen, *clean_codes(extra)])),
              "indicators": list(dict.fromkeys([*picked, *_text_codes(more)])),
              "start_year": start, "end_year": end, "max_minutes": time_limit(source)}
    if source == "worldbank":
        params["drop_empty"] = drop_empty
    return params


def check(params, source="worldbank"):
    """Raise ValueError with a plain message when the form cannot be run."""
    if module := _other(source):
        return module.check(params, source)
    if not params["countries"]:
        raise ValueError("Pilih minimal satu negara.")
    if any(code != "all" and not re.fullmatch(r"[A-Z]{3}", code) for code in params["countries"]):
        raise ValueError("Kode negara harus tiga huruf, misalnya IDN atau MYS.")
    if not params["indicators"]:
        raise ValueError("Pilih minimal satu indikator.")
    if bad := [code for code in params["indicators"] if not CODE_RE.fullmatch(code)]:
        raise ValueError(f"Kode indikator tidak valid: {', '.join(bad)}.")
    if params["start_year"] > params["end_year"]:
        raise ValueError("Tahun mulai harus sebelum atau sama dengan tahun selesai.")


def countries_text(codes):
    if "all" in codes:
        return "Semua negara dan wilayah"
    names = [COUNTRIES.get(code, code) for code in codes]
    return ", ".join(names[:4]) + (f" dan {len(names) - 4} lainnya" if len(names) > 4 else "")


def summary(source, params):
    if module := _other(source):
        return module.summary(source, params)
    from workspace_app import ui

    return [("Sumber", NAMES[source]), ("Negara", countries_text(params["countries"]) if params["countries"]
                                        else "belum dipilih"),
            ("Indikator", f"{ui.fmt(len(params['indicators']))} indikator" if params["indicators"] else "belum dipilih"),
            ("Tahun", f"{params['start_year']} – {params['end_year']}"),
            ("Batas waktu", f"{params['max_minutes']} menit")]


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params, source = job["params"], job["source"]
    if module := _other(source):
        return module.method_rows(job)
    names = INDICATORS[source]
    return [("Negara", countries_text(params.get("countries", []))),
            ("Indikator", "; ".join(f"{names.get(code, code)} ({code})" for code in params.get("indicators", []))),
            ("Rentang tahun", f"{params.get('start_year')} – {params.get('end_year')}")]


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    if module := _other(source):
        return module.seed(source, params)
    from workspace_app.views import _key

    state, names = st.session_state, INDICATORS[source]
    codes = params.get("countries", [])
    state[_key(source, "all")] = "all" in codes
    known = [code for code in codes if code in COUNTRIES]
    state[_key(source, "countries")] = known
    state[_key(source, "extra_countries")] = ", ".join(code for code in codes if code not in COUNTRIES and code != "all")
    wanted = params.get("indicators", [])
    state[_key(source, "indicators")] = [code for code in wanted if code in names]
    state[_key(source, "extra_indicators")] = ", ".join(code for code in wanted if code not in names)
    for name in ("start_year", "end_year"):
        if name in params:
            state[_key(source, name)] = params[name]
    if source == "worldbank":
        state[_key(source, "drop_empty")] = params.get("drop_empty", True)


def hints(job):
    """What to try after an empty or failed run."""
    if module := _other(job["source"]):
        return module.hints(job)
    found = ["Pastikan kode negara tiga huruf (IDN, MYS) dan kode indikator ditulis persis.",
             "Lebarkan rentang tahun; indikator tertentu hanya punya data untuk tahun tertentu."]
    found.append(FINDER[job["source"]].replace("Cari kode lain di", "Cek daftar kode di"))
    return found


def chart(frame, source="worldbank"):
    """Line chart of one indicator over the years, one line per country (at most eight)."""
    if module := _other(source):
        return module.chart(frame, source)
    if not {"tahun", "nilai", "negara"} <= set(frame):
        st.metric("Baris", len(frame))
        return
    labels = frame.drop_duplicates("kode_indikator").set_index("kode_indikator")["indikator"].to_dict()
    code = st.selectbox("Indikator", list(labels), format_func=lambda value: f"{labels[value]} ({value})",
                        key="open-data-chart")
    subset = frame[frame["kode_indikator"] == code].dropna(subset=["nilai"])
    top = subset.groupby("negara").size().sort_values(ascending=False).head(8).index
    table = subset[subset["negara"].isin(top)].pivot_table(index="tahun", columns="negara", values="nilai")
    if table.empty:
        st.info("Tidak ada nilai untuk indikator ini.")
        return
    st.line_chart(table)
    if len(subset["negara"].unique()) > 8:
        st.caption("Grafik menampilkan 8 negara dengan data terbanyak. Tabel di tab Data memuat semuanya.")
    if "jenis" in frame and (frame["jenis"] == "proyeksi").any():
        st.caption("Nilai berlabel proyeksi di kolom jenis adalah perkiraan IMF, bukan data aktual.")
