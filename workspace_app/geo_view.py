"""Peta Kejadian: one page for family F. Pick a dataset with coordinates, then look at a hexagon density map,
HDBSCAN clusters, counts per kabupaten/kota, or earthquake statistics."""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import geo
from workspace_app import map_ui
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.series_view import filter_columns
from workspace_app.sources import label

KEY = "geo-"
SECTIONS = ["Peta kepadatan", "Klaster", "Per wilayah", "Gempa"]
SECTION_OF_SOURCE = {"usgs": "Gempa"}
NO_WEIGHT = "__tanpa__"
PALETTE = [(43, 147, 174), (208, 80, 60), (46, 158, 107), (183, 121, 31), (123, 97, 196), (194, 71, 127),
           (79, 125, 156), (138, 143, 46)]
NOISE = (140, 150, 160)


def geo_page(manager):
    ui.page_header(tr("Peta Kejadian"), tr("Peta kepadatan, klaster titik, hitungan per kabupaten/kota, dan statistik "
                                             "gempa"))
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-geo-input"):
        job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0, key=KEY + "dataset")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"]).drop(columns=["pekerjaan_asal"], errors="ignore")
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}, "source": job["source"]}
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        chosen = pick_points(frame, job_id)
    if chosen is None:
        return
    points, report, weight, filters = chosen
    origin = f"data {', '.join(label(source) for source in origins)}" if origins else "dataset ini"
    note = f"{ui.fmt(len(points))} {tr('titik dengan koordinat valid')}"
    if len(points) != report["baris"]:
        note += f" {tr('dari')} {ui.fmt(report['baris'])} {tr('baris')}"
    dropped = report["kosong"] + report["di_luar_jangkauan"] + report["nol_nol"]
    if dropped:
        note += f" · {ui.fmt(dropped)} {tr('dibuang (tanpa koordinat, di luar jangkauan, atau tepat 0,0)')}"
    st.caption(note)

    default = next((SECTION_OF_SOURCE[source] for source in origins if source in SECTION_OF_SOURCE), SECTIONS[0])
    section = st.segmented_control(tr("Analisis"), SECTIONS, default=default, required=True, format_func=tr,
                                   key=f"{KEY}section-{job_id}")
    context = {"points": points, "report": report, "weight": weight, "origin": origin, "job_id": job_id,
               "filters": tuple(filters), "base": an.safe_name(job["project"])}
    {"Peta kepadatan": density_section, "Klaster": cluster_section, "Per wilayah": region_section,
     "Gempa": quake_section}[section](context)


# --------------------------------------------------------------------------- #
# Choosing the points
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner=False, max_entries=4)
def prepare_points(_frame, job_id, count, filters):
    selected = _frame
    for column, values in filters:
        selected = selected[selected[column].isin(values)]
    return geo.coordinates(selected)


def pick_points(frame, job_id):
    try:
        geo.coordinates(frame.head(1))
    except geo.NotEnough as exc:
        if "tidak punya kolom" in str(exc):
            st.info(tr("Dataset ini tidak punya kolom lintang dan bujur, jadi tidak bisa dipetakan. Coba USGS, NASA "
                       "FIRMS, Bencana, atau Google Maps Lokasi."), icon=":material/location_off:")
            return None
    dates = an.date_columns(frame)
    filters = []
    columns = filter_columns(frame, dates)
    if columns:
        cols = st.columns(min(len(columns), 4))
        for holder, column in zip(cols, columns):
            values = list(frame[column].value_counts().index)
            picked = holder.multiselect(column, values, default=values, key=f"{KEY}filter-{job_id}-{column}")
            if picked and len(picked) != len(values):
                filters.append((column, tuple(picked)))
    numbers = [column for column in fam.numeric_columns(frame) if column not in dates
               and str(column).lower() not in (*geo.LAT_COLUMNS, *geo.LON_COLUMNS)]
    weight = st.selectbox(tr("Bobot (opsional)"), [NO_WEIGHT, *numbers], key=f"{KEY}weight-{job_id}",
                          format_func=lambda item: tr("Tanpa bobot (hitung titik)") if item == NO_WEIGHT else item,
                          help=tr("Kolom angka (magnitudo, FRP, kecerahan) untuk menjumlahkan nilai di peta dan tabel "
                                  "wilayah, bukan sekadar menghitung titik."))
    try:
        points, report = prepare_points(frame, job_id, len(frame), tuple(filters))
    except geo.NotEnough as exc:
        st.warning(str(exc), icon=":material/warning:")
        return None
    return points, report, None if weight == NO_WEIGHT else weight, filters


def show(figure, height=320):
    figure.update_layout(height=height, margin={"l": 0, "r": 0, "t": 10, "b": 0}, paper_bgcolor="rgba(0,0,0,0)",
                         plot_bgcolor="rgba(0,0,0,0)", legend={"orientation": "h", "y": 1.12})
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def method_block(text):
    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(text, language=None, wrap_lines=True)


def pct(value, places=1):
    return f"{value:.{places}f}%".replace(".", ",")


def deck(layers, points, style="light"):
    map_ui.render_deck(layers, points, key_prefix="geo", map_style=style, height=520)


def map_points(points, limit=geo.MAX_MAP_POINTS):
    if len(points) > limit:
        st.caption(tr('Peta menampilkan {0} titik acak dari {1} (seed 42); hitungan di bagian lain memakai semua.',
                      ui.fmt(limit), ui.fmt(len(points))))
        return points.sample(limit, random_state=geo.SEED)
    return points


# --------------------------------------------------------------------------- #
# Peta kepadatan
# --------------------------------------------------------------------------- #

def density_section(context):
    points, weight = context["points"], context["weight"]
    span = max(points["_lat"].max() - points["_lat"].min(), (points["_lon"].max() - points["_lon"].min())
               * np.cos(np.radians(points["_lat"].mean())), 0.01) * geo.KM_PER_DEGREE
    suggested = float(np.clip(span / 40, 1, 150))
    radius = st.slider(tr("Jari-jari heksagon (km)"), 1, 200, int(round(suggested)), 1, key=f"{KEY}radius-{context['job_id']}",
                       help=tr("Titik di dalam heksagon dijumlahkan. Mulai dari nilai awal; perkecil untuk detail."))
    shown = map_points(points)
    # Short column names: the points travel to the browser as JSON, so each name is repeated once per point.
    data = pd.DataFrame({"x": shown["_lon"].round(4), "y": shown["_lat"].round(4)})
    if weight:
        data["w"] = pd.to_numeric(shown[weight], errors="coerce").fillna(0)
    style, _ = map_ui.map_controls(f"{KEY}hex-{context['job_id']}", allow_size=False)
    layer = pdk.Layer(
        "HexagonLayer", data=data, get_position="[x, y]", radius=int(radius * 1000), extruded=False, pickable=True,
        opacity=0.55, auto_highlight=True, coverage=0.95,
        **({"get_color_weight": "w", "color_aggregation": "SUM"} if weight else {}),
        color_range=[[255, 255, 204], [255, 237, 160], [254, 217, 118], [254, 178, 76], [253, 141, 60], [227, 26, 28]])
    deck([layer], shown, style=style)
    st.caption(tr("Warna makin merah = makin banyak " + ("nilai bobot" if weight else "titik") + " di heksagon itu."))
    method_block(geo.density_method_text(context["report"], context["origin"], radius, weight))


# --------------------------------------------------------------------------- #
# Klaster
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Mengelompokkan titik dengan HDBSCAN…", max_entries=4)
def compute_clusters(points, size, samples):
    return geo.cluster_points(points["_lat"].to_numpy(), points["_lon"].to_numpy(), size, samples)


def cluster_section(context):
    points = context["points"]
    if len(points) < geo.MIN_CLUSTER_POINTS:
        st.info(tr('Klaster butuh minimal {0} titik, data ini {1}.', geo.MIN_CLUSTER_POINTS, len(points)),
                icon=":material/rule:")
        return
    left, right = st.columns(2)
    size = left.slider(tr("Ukuran klaster minimal"), 5, max(10, min(500, len(points) // 4)),
                       max(geo.MIN_CLUSTER_POINTS, min(len(points) // 100, 200)),
                       key=f"{KEY}size-{context['job_id']}",
                       help=tr("Jumlah titik terkecil yang boleh membentuk satu klaster. Naikkan bila klaster terlalu "
                               "banyak dan kecil."))
    use_samples = right.checkbox(tr("Atur min_samples sendiri"), False, key=KEY + "own-samples",
                                 help=tr("Bawaan: sama dengan ukuran klaster. Makin kecil, makin sedikit titik dianggap "
                                         "derau."))
    samples = right.slider("min_samples", 1, size, max(1, size // 2), key=KEY + "samples") if use_samples else None
    try:
        labels, table, summary = compute_clusters(points[["_lat", "_lon"]], size, samples)
    except geo.NotEnough as exc:
        st.info(str(exc), icon=":material/rule:")
        return
    cols = st.columns(3)
    cols[0].metric(tr("Klaster"), ui.fmt(summary["klaster"]), border=True)
    cols[1].metric(tr("Titik derau"), pct(summary["noise"] / max(summary["dikelompokkan"], 1) * 100), border=True,
                   help=tr("Tidak cukup padat untuk masuk klaster mana pun."))
    cols[2].metric(tr("Titik dikelompokkan"), ui.fmt(summary["dikelompokkan"]), border=True)
    if summary["diambil_sampel"]:
        st.caption(tr('Lebih dari {0} titik: {1} titik acak dikelompokkan (seed 42), sisanya tidak ikut.',
                      ui.fmt(geo.MAX_CLUSTER_POINTS), ui.fmt(summary["dikelompokkan"])))
    used = points[labels != -2].copy()
    used["klaster"] = labels[labels != -2]
    shown = map_points(used, 60_000)
    shown = shown.assign(
        r=[PALETTE[label_ % len(PALETTE)][0] if label_ >= 0 else NOISE[0] for label_ in shown["klaster"]],
        g=[PALETTE[label_ % len(PALETTE)][1] if label_ >= 0 else NOISE[1] for label_ in shown["klaster"]],
        b=[PALETTE[label_ % len(PALETTE)][2] if label_ >= 0 else NOISE[2] for label_ in shown["klaster"]],
        a=[200 if label_ >= 0 else 70 for label_ in shown["klaster"]],
        tooltip=[tr("Klaster") + f" {label_}" if label_ >= 0 else tr("Derau") for label_ in shown["klaster"]])
    style, base_px = map_ui.map_controls(f"{KEY}cl-{context['job_id']}", allow_size=True, default_size=5)
    layer = pdk.Layer("ScatterplotLayer", data=shown[["_lon", "_lat", "r", "g", "b", "a", "tooltip"]],
                      get_position="[_lon, _lat]", get_fill_color="[r, g, b, a]", get_radius=base_px,
                      radius_units="'pixels'", pickable=True)
    centres = pd.DataFrame({"clon": table["Bujur pusat"], "clat": table["Lintang pusat"], "tooltip": [
        f"{tr('Klaster')} {row.Klaster}: {row.Titik} {tr('titik')}" for row in table.itertuples()]})
    ring = pdk.Layer("ScatterplotLayer", data=centres, get_position="[clon, clat]", stroked=True,
                     filled=False, get_line_color=[20, 30, 40, 220], line_width_min_pixels=2,
                     get_radius=base_px + 6, radius_units="'pixels'", pickable=True)
    deck([layer, ring], used, style=style)
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Lintang pusat": st.column_config.NumberColumn(format="%.3f"),
        "Bujur pusat": st.column_config.NumberColumn(format="%.3f"),
        "Radius median (km)": st.column_config.NumberColumn(format="%.1f"),
        "Radius 90% (km)": st.column_config.NumberColumn(format="%.1f")})
    csv_download(table, f"klaster-{context['base']}", "dl-geo-cluster")
    method_block(geo.cluster_method_text(summary, context["origin"]))


# --------------------------------------------------------------------------- #
# Per wilayah
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menempatkan titik pada kabupaten/kota…", max_entries=4)
def compute_regions(points, job_id, filters):
    return geo.assign_regions(points["_lat"].to_numpy(), points["_lon"].to_numpy())


def region_section(context):
    points, weight = context["points"], context["weight"]
    regions = compute_regions(points[["_lat", "_lon"]], context["job_id"], context["filters"])
    placed = points.reset_index(drop=True).join(regions.reset_index(drop=True))
    level = st.segmented_control(tr("Tingkat"), ["kabupaten_kota", "provinsi"], default="kabupaten_kota", required=True,
                                 format_func=lambda item: tr("Kabupaten/kota") if item == "kabupaten_kota" else tr("Provinsi"),
                                 key=KEY + "level")
    table, summary = geo.region_table(placed, level, weight)
    if table.empty:
        st.info(tr("Tidak ada titik di dalam atau dekat batas Indonesia. Analisis ini hanya untuk wilayah "
                   "Indonesia."), icon=":material/public_off:")
        return
    cols = st.columns(4)
    cols[0].metric(tr("Titik"), ui.fmt(summary["total"]), border=True)
    cols[1].metric(tr("Di dalam batas"), ui.fmt(summary["di_wilayah"]), border=True)
    cols[2].metric(tr("Di sekitar pantai"), ui.fmt(summary["di_laut"]), border=True,
                   help=tr('Di luar garis batas tetapi dalam {0} km dari kabupaten/kota terdekat.', geo.NEAR_KM))
    cols[3].metric(tr("Di luar jangkauan"), ui.fmt(summary["luar"]), border=True,
                   help=tr("Terlalu jauh dari batas Indonesia; tidak dihitung."))
    name = table.columns[0]
    top = table.head(15)
    figure = go.Figure(go.Bar(x=top["Kejadian"][::-1], y=top[name][::-1], orientation="h", marker_color=ui.CHART_ACCENT))
    show(figure, 80 + 26 * len(top))
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Porsi (%)": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.1f%%")})
    left, right = st.columns(2)
    with left:
        csv_download(table, f"wilayah-{level}-{context['base']}", "dl-geo-region")
    with right:
        keep = placed.drop(columns=["_lat", "_lon"])
        st.download_button(tr("Unduh data dengan kabupaten/kota (CSV)"), keep.to_csv(index=False).encode("utf-8-sig"),
                           f"data-wilayah-{context['base']}.csv", "text/csv", icon=":material/download:",
                           key="dl-geo-rows", on_click="ignore")
    method_block(geo.region_method_text(summary, context["origin"],
                                        "kabupaten/kota" if level == "kabupaten_kota" else "provinsi"))


# --------------------------------------------------------------------------- #
# Gempa
# --------------------------------------------------------------------------- #

def quake_section(context):
    points = context["points"]
    magnitude = geo.find_column(points, geo.MAGNITUDE_COLUMNS)
    depth = geo.find_column(points, geo.DEPTH_COLUMNS)
    if magnitude is None:
        st.info(tr("Dataset ini tidak punya kolom magnitudo, jadi bukan katalog gempa. Pilih data USGS."),
                icon=":material/vibration:")
        return
    values = pd.to_numeric(points[magnitude], errors="coerce").dropna()
    if len(values) < geo.MIN_QUAKES:
        st.info(tr('Butuh minimal {0} gempa dengan magnitudo, data ini {1}.', geo.MIN_QUAKES, len(values)),
                icon=":material/rule:")
        return
    left, right = st.columns(2)
    own = left.checkbox(tr("Tentukan Mc sendiri"), False, key=f"{KEY}own-mc-{context['job_id']}",
                        help=tr("Bawaan: kurvatur maksimum + 0,2. Naikkan bila kurva di bawah Mc tampak melandai."))
    mc = right.number_input("Mc", float(values.min()), float(values.max()), float(geo.completeness_magnitude(values)),
                            0.1, key=KEY + "mc") if own else None
    result = geo.gutenberg_richter(values, mc=mc)
    cols = st.columns(4)
    cols[0].metric(tr("Gempa"), ui.fmt(result["n"]), border=True)
    cols[1].metric("Mc", f"{result['mc']:.1f}".replace(".", ","), border=True,
                   help=tr("Magnitudo kelengkapan: di bawahnya katalog kehilangan gempa kecil."))
    if result["b"] is not None:
        cols[2].metric(tr("Nilai b"), f"{result['b']:.2f} ± {result['sigma_b']:.2f}".replace(".", ","), border=True,
                       help=tr("Aki (1965) dengan galat Shi & Bolt (1982). Sekitar 1 umum untuk kerak bumi."))
        cols[3].metric(tr("Nilai a"), f"{result['a']:.2f}".replace(".", ","), border=True)
    else:
        cols[2].metric(tr("Nilai b"), "–", border=True)
        st.warning(tr('Nilai b tidak dihitung: hanya {0} gempa di atas Mc (minimal {1}).', result["n_di_atas_mc"],
                      geo.MIN_QUAKES), icon=":material/warning:")
    curve = result["kurva"]
    figure = go.Figure()
    figure.add_trace(go.Bar(x=curve["Magnitudo"], y=curve["Per kelas"], name=tr("Per kelas"),
                            marker_color="rgba(130,146,162,.45)"))
    figure.add_trace(go.Scatter(x=curve["Magnitudo"], y=curve["Kumulatif (≥ M)"], mode="markers", name="N (≥ M)",
                                marker={"color": ui.CHART_ACCENT, "size": 6}))
    if result["b"] is not None:
        line = curve[curve["Magnitudo"] >= result["mc"] - 1e-9]
        figure.add_trace(go.Scatter(x=line["Magnitudo"], y=10 ** (result["a"] - result["b"] * line["Magnitudo"]),
                                    mode="lines", name=tr("Garis Gutenberg–Richter"), line={"color": "#d0503c"}))
    figure.add_vline(x=result["mc"], line_dash="dot", line_color="rgba(130,146,162,.8)")
    figure.update_yaxes(type="log", title=tr("jumlah gempa"))
    figure.update_xaxes(title=tr("magnitudo"))
    show(figure, 340)
    st.caption(tr("Garis putus-putus: Mc. Di bawahnya katalog tidak lengkap, jadi titik melandai dari garis."))
    csv_download(curve, f"gutenberg-richter-{context['base']}", "dl-geo-gr")
    if depth is not None:
        st.subheader(tr("Kedalaman"), anchor=False)
        try:
            table, stats = geo.depth_summary(points[depth])
        except geo.NotEnough as exc:
            st.info(str(exc))
        else:
            cols = st.columns(3)
            cols[0].metric(tr("Median"), f"{stats['median']:.0f} km", border=True)
            cols[1].metric(tr("Persentil 90"), f"{stats['persentil_90']:.0f} km", border=True)
            cols[2].metric(tr("Terdalam"), f"{stats['maks']:.0f} km", border=True)
            bar = go.Figure(go.Bar(x=table["Kelas"], y=table["Gempa"], marker_color=ui.CHART_ACCENT))
            show(bar, 240)
            csv_download(table, f"kedalaman-{context['base']}", "dl-geo-depth")
    method_block(geo.gutenberg_richter_text(result, context["origin"]))
