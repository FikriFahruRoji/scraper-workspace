"""Form and result views for the OECD source: every OECD dataset, with named pickers for every dimension."""
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

SOURCES = ("oecd",)
NAMES = {"oecd": "OECD"}
MAX_SERIES = 8
CATALOGUE_AGE = 7 * 24 * 3600
DEFAULT_COUNTRY = "IDN"


def _scraper():
    from workspace_app.sources import load
    return load("oecd")


def _cache_file():
    from workspace_app.jobs import local_directory
    return Path(local_directory()) / "oecd_catalogue.json"


def load_catalogue():
    """The OECD dataset list, kept on disk for a week; the first load takes about a minute."""
    path = _cache_file()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - saved["saved"] < CATALOGUE_AGE and saved["items"]:
            return saved["items"]
    except (OSError, ValueError, KeyError, TypeError):
        saved = None
    try:
        items = _scraper().catalogue()
    except _scraper().ApiError:
        if saved and saved.get("items"):
            return saved["items"]  # an old list beats none
        raise
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"saved": time.time(), "items": items}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return items


@st.cache_data(ttl=24 * 3600, show_spinner="Memuat daftar dataset OECD (pertama kali bisa sampai satu menit)…")
def _catalogue():
    return load_catalogue()


@st.cache_data(ttl=6 * 3600, show_spinner="Memuat dimensi dan kode dataset…")
def _structure(flow):
    return _scraper().structure(flow)


def _options(items):
    """Dataset keys in display order (starred first) and a key -> label map."""
    scraper = _scraper()
    labels, featured = {}, []
    names = [item["name"] for item in items]
    for item in items:
        agency, flow_id, _version = item["flow"].split(",")
        label = item["name"] if names.count(item["name"]) == 1 else f"{item['name']} ({flow_id})"
        key = f"{agency},{flow_id}"
        if key in scraper.FEATURED:
            featured.append(item["flow"])
            label = f"★ {item['name']} · {scraper.FEATURED[key]}"
        labels[item["flow"]] = label
    rest = [item["flow"] for item in items if item["flow"] not in featured]
    return [*featured, *rest], labels


def _label(code, name):
    return f"{name} ({code})" if name and name != code else code


def _flow_name(params):
    return params.get("flow_name") or (params.get("flow") or tr("belum dipilih")).rstrip(",")


def _countries_text(codes):
    if not codes:
        return "Semua yang ada di dataset"
    return ", ".join(codes[:6]) + (f" dan {len(codes) - 6} lainnya" if len(codes) > 6 else "")


def form(source):
    """The form fields; returns the parameters the scraper takes."""
    from workspace_app.views import _initial, _key, number, time_limit

    scraper = _scraper()
    try:
        items = _catalogue()
    except scraper.ApiError as exc:
        st.error(tr('Daftar dataset OECD tidak bisa dimuat: {0}', exc))
        return {"flow": "", "flow_name": "", "countries": [], "filters": {}, "start_year": 2015,
                "end_year": date.today().year, "count": 0, "max_minutes": time_limit(source)}
    keys, labels = _options(items)
    total = f"{len(keys):,}".replace(",", ".")
    flow = st.selectbox(tr('Dataset ({0} tersedia, ketik untuk mencari)', total), keys,
                        key=_key(source, "flow"), format_func=labels.get)
    try:
        info = _structure(flow)
    except scraper.ApiError as exc:
        st.error(tr('Dimensi dataset ini tidak bisa dimuat: {0}', exc))
        info = {"dims": [], "observations": None}
    agency, flow_id, version = flow.split(",")
    size = f" · {info['observations']:,} observasi di seluruh dataset".replace(",", ".") if info["observations"] else ""
    st.caption(tr('{0} · {1} · versi {2}{3}', agency, flow_id, version, size))

    flow_key = flow_id.replace("@", "-")
    countries, filters, fixed = [], {}, []
    for dim in info["dims"]:
        codes = dict(dim["codes"])
        if dim["id"] == scraper.COUNTRY and codes:
            default = [DEFAULT_COUNTRY] if DEFAULT_COUNTRY in codes else []
            countries = st.multiselect(tr('{0} (kosong = semua)', dim['name']), list(codes), key=_key(source, f"area-{flow_key}"),
                                       format_func=lambda code, codes=codes: _label(code, codes[code]),
                                       **_initial(_key(source, f"area-{flow_key}"), default, "default"),
                                       help=tr("Hanya wilayah yang punya data di dataset ini yang ditampilkan."))
    others = [dim for dim in info["dims"] if dim["id"] != scraper.COUNTRY]
    choosable = [dim for dim in others if len(dim["codes"]) > 1]
    for dim in others:
        if len(dim["codes"]) == 1:
            fixed.append(f"{dim['name']}: {dim['codes'][0][1]}")
    if choosable:
        with st.expander(tr('Rincian data ({0} dimensi, kosong = semua)', len(choosable)), expanded=True):
            for dim in choosable:
                codes = dict(dim["codes"])
                picked = st.multiselect(tr('{0} ({1} pilihan)', dim['name'], len(codes)), list(codes),
                                        key=_key(source, f"dim-{flow_key}-{dim['id']}"),
                                        format_func=lambda code, codes=codes: _label(code, codes[code]))
                if picked:
                    filters[dim["id"]] = picked
    if fixed:
        st.caption("Tetap di dataset ini: " + "; ".join(fixed))
    this_year = date.today().year
    left, right = st.columns(2)
    start = int(left.number_input(tr("Tahun mulai"), 1950, this_year, key=_key(source, "start_year"),
                                  **_initial(_key(source, "start_year"), 2015, "value")))
    end = int(right.number_input(tr("Tahun selesai"), 1950, this_year, key=_key(source, "end_year"),
                                 **_initial(_key(source, "end_year"), this_year, "value")))
    limit = number(source, "count", tr("Jumlah observasi maksimum"), 0, 2_000_000, 100_000, step=10_000,
                   help=tr("0 mengambil semua yang dikembalikan OECD."))
    st.caption(tr("Data: OECD. OECD membatasi jumlah permintaan data per jam (sekitar 20); satu pengambilan memakai "
               "satu permintaan data."))
    return {"flow": flow, "flow_name": next(item["name"] for item in items if item["flow"] == flow), "countries": countries, "filters": filters,
            "start_year": start, "end_year": end, "count": limit, "max_minutes": time_limit(source)}


def check(params, source="oecd"):
    """Raise ValueError with a plain message when the form cannot be run."""
    scraper = _scraper()
    if not params.get("flow"):
        raise ValueError(tr("Pilih dataset terlebih dahulu."))
    params["flow"] = scraper.normalize_flow(params["flow"])
    if bad := [code for code in params["countries"] if not scraper._CODE.match(code)]:
        raise ValueError(tr('Kode wilayah tidak valid: {0}.', ', '.join(bad)))
    for name, codes in params.get("filters", {}).items():
        if not codes or any(not scraper._CODE.match(code) for code in codes):
            raise ValueError(tr('Pilihan pada {0} tidak valid.', name))
    if params["start_year"] > params["end_year"]:
        raise ValueError(tr("Tahun mulai harus sebelum atau sama dengan tahun selesai."))


def _filters_text(filters):
    return "; ".join(f"{name}={'+'.join(codes)}" for name, codes in filters.items()) or tr("tidak ada")


def summary(source, params):
    return [("Sumber", tr(NAMES[source])), ("Dataset", _flow_name(params)),
            ("Wilayah", _countries_text(params.get("countries", []))),
            ("Tahun", f"{params['start_year']} – {params['end_year']}"),
            ("Rincian", _filters_text(params.get("filters", {}))),
            ("Batas data", f"{params['count']:,}".replace(",", ".") if params.get("count") else tr("semua")),
            ("Batas waktu", tr("{0} menit", params['max_minutes']))]


def method_rows(job):
    """Rows describing how the job collected its data, for the dataset dialog."""
    params = job["params"]
    rows = [("Dataset", _flow_name(params)), ("ID dataflow", (params.get("flow") or "-").rstrip(",")),
            ("Wilayah", _countries_text(params.get("countries", []))),
            ("Rentang tahun", f"{params.get('start_year')} – {params.get('end_year')}"),
            ("Rincian", _filters_text(params.get("filters", {})))]
    fixed = (job.get("metadata") or {}).get("tetap")
    if fixed:
        rows.append(("Nilai tetap", "; ".join(f"{name}: {label}" for name, label in fixed.items())))
    return rows


def seed(source, params):
    """Pre-fill the form from an earlier job, for "Jalankan ulang"."""
    from workspace_app.views import _key

    state, flow = st.session_state, params.get("flow", "")
    if not flow:
        return
    state[_key(source, "flow")] = flow
    flow_key = flow.split(",")[1].replace("@", "-") if flow.count(",") >= 1 else flow
    state[_key(source, f"area-{flow_key}")] = list(params.get("countries", []))
    for name, codes in params.get("filters", {}).items():
        state[_key(source, f"dim-{flow_key}-{name}")] = list(codes)
    for name in ("start_year", "end_year"):
        if name in params:
            state[_key(source, name)] = params[name]
    state[_key(source, "count")] = params.get("count", 100_000)


def hints(job):
    """What to try after an empty or failed run."""
    return ["Tidak semua wilayah punya data pada semua rincian; kosongkan beberapa pilihan atau perluas tahun.",
            "Bila muncul pesan batas permintaan, tunggu sekitar satu jam; OECD membatasi permintaan per jam.",
            "Pilihan di formulir hanya berisi kode yang ada di dataset, jadi kombinasi tertentu tetap bisa kosong."]


def chart(frame, source="oecd"):
    """Line chart over time, one line per country and varying dimension (at most eight)."""
    if not {"periode", "nilai", "negara"} <= set(frame):
        st.metric(tr("Baris"), len(frame))
        return
    standard = {"dataflow", "negara_kode", "negara", "periode", "tahun", "nilai", "status", "pengali"}
    varying = [name for name in frame if name not in standard and not name.endswith("_kode")]
    labels = frame["negara"].astype(str)
    for name in varying:
        labels = labels + " · " + frame[name].astype(str)
    data = frame.assign(garis=labels).dropna(subset=["nilai"])
    top = data.groupby("garis").size().sort_values(ascending=False).head(MAX_SERIES).index
    table = data[data["garis"].isin(top)].pivot_table(index="periode", columns="garis", values="nilai")
    if table.empty:
        st.info(tr("Tidak ada nilai untuk digambar."))
        return
    st.line_chart(table.sort_index())
    if data["garis"].nunique() > MAX_SERIES:
        st.caption(tr('Grafik menampilkan {0} deret dengan data terbanyak. Tabel di tab Data memuat semuanya.', MAX_SERIES))
