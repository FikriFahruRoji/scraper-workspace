"""Shared dynamic pydeck map helpers: Indonesia-first viewport, constant-pixel circles on zoom,
basemap style selector, and hover tooltips."""
import html

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

from workspace_app.i18n import tr

IDN_LAT = (-11.5, 6.5)
IDN_LON = (94.0, 142.0)
IDN_CENTER = (-2.5, 118.0, 4.1)
BASEMAPS = {
    "light": "Terang (Light)",
    "dark": "Gelap (Dark)",
    "road": "Jalan (Voyager)",
}
DEFAULT_TOOLTIP_COLS = (
    "nama", "tempat", "jenis", "kategori", "provinsi", "kabupaten_kota",
    "magnitudo", "kedalaman_km", "frp_mw", "tingkat", "rating", "waktu_wib", "mulai_wib", "waktu",
)


def is_indonesia(lats, lons):
    """True when at least half of valid coordinates lie within Indonesia's bounding box."""
    lat_arr = pd.to_numeric(lats, errors="coerce").to_numpy(dtype=float)
    lon_arr = pd.to_numeric(lons, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(lat_arr) & np.isfinite(lon_arr)
    if not valid.any():
        return True
    in_box = (
        (lat_arr[valid] >= IDN_LAT[0]) & (lat_arr[valid] <= IDN_LAT[1])
        & (lon_arr[valid] >= IDN_LON[0]) & (lon_arr[valid] <= IDN_LON[1])
    )
    return float(in_box.mean()) >= 0.5


def view_state(points, lat_col="_lat", lon_col="_lon"):
    """Return an Indonesia-centered ViewState when majority of points are in Indonesia, else fit points."""
    if points is None or points.empty or is_indonesia(points[lat_col], points[lon_col]):
        return pdk.ViewState(latitude=IDN_CENTER[0], longitude=IDN_CENTER[1], zoom=IDN_CENTER[2], pitch=0)
    view = pdk.data_utils.compute_view(
        points[[lon_col, lat_col]].head(20_000).to_numpy().tolist(),
        view_proportion=0.95,
    )
    view.pitch = 0
    return view


def map_controls(key_prefix, allow_size=True, default_size=5):
    """Render basemap style selector and optional constant-pixel circle size slider."""
    if allow_size:
        c1, c2 = st.columns([1.2, 2])
        style = c1.selectbox(
            tr("Gaya peta"),
            list(BASEMAPS),
            format_func=lambda k: tr(BASEMAPS[k]),
            key=f"{key_prefix}-style",
        )
        size = c2.slider(
            tr("Ukuran titik (px)"),
            min_value=2,
            max_value=14,
            value=int(default_size),
            step=1,
            key=f"{key_prefix}-px",
        )
        return style, int(size)
    style = st.selectbox(
        tr("Gaya peta"),
        list(BASEMAPS),
        format_func=lambda k: tr(BASEMAPS[k]),
        key=f"{key_prefix}-style",
    )
    return style, int(default_size)


def _build_tooltips(frame, cols):
    present = [c for c in (cols or DEFAULT_TOOLTIP_COLS) if c in frame.columns]
    if not present:
        return ["Titik" for _ in range(len(frame))]
    tips = []
    for row in frame[present].itertuples(index=False, name=None):
        parts = []
        for col, val in zip(present, row):
            if pd.isna(val) or str(val).strip() == "":
                continue
            parts.append(f"<b>{html.escape(str(col))}:</b> {html.escape(str(val))}")
        tips.append("<br>".join(parts) if parts else "Titik")
    return tips


def render_deck(layers, points, key_prefix, lat_col="_lat", lon_col="_lon",
                map_style="light", height=500):
    """Render a pydeck chart with Indonesia-aware initial view and hover tooltips."""
    view = view_state(points, lat_col=lat_col, lon_col=lon_col)
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view,
            map_provider="carto",
            map_style=map_style,
            tooltip={"html": "{tooltip}", "style": {"fontSize": "12px"}},
        ),
        height=height,
    )
    st.caption(tr("Peta dasar dimuat dari internet (CARTO). Tanpa koneksi, hanya lapisan titik yang tampil."))


def point_map(frame, lat_col, lon_col, key_prefix, weight_col=None,
              color=(208, 80, 60, 195), tooltip_cols=None, default_size=5, height=480):
    """Interactive pydeck point map with constant pixel size on zoom and Indonesia-first framing."""
    work = frame.copy()
    work["_lat"] = pd.to_numeric(work[lat_col], errors="coerce")
    work["_lon"] = pd.to_numeric(work[lon_col], errors="coerce")
    work = work.dropna(subset=["_lat", "_lon"]).copy()
    if work.empty:
        return

    style, base_px = map_controls(key_prefix, allow_size=True, default_size=default_size)
    work["tooltip"] = _build_tooltips(work, tooltip_cols)

    if weight_col and weight_col in work.columns:
        w = pd.to_numeric(work[weight_col], errors="coerce")
        w_valid = w.dropna()
        if not w_valid.empty and w_valid.max() > w_valid.min():
            norm = ((w.fillna(w_valid.min()) - w_valid.min()) / (w_valid.max() - w_valid.min())).clip(0, 1)
            lo = max(3.0, float(base_px) - 1.0)
            hi = min(18.0, float(base_px) + 9.0)
            work["_px"] = lo + norm * (hi - lo)
        else:
            work["_px"] = float(base_px)
    else:
        work["_px"] = float(base_px)

    data = work[["_lon", "_lat", "_px", "tooltip"]]
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=data,
        get_position="[_lon, _lat]",
        get_fill_color=list(color),
        get_radius="_px",
        radius_units="'pixels'",
        pickable=True,
        auto_highlight=True,
    )
    render_deck([layer], data, key_prefix=key_prefix, map_style=style, height=height)
