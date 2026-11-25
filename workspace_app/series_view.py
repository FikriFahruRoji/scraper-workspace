"""Deret Waktu: one page for family D. Pick a dataset and one series, then forecast it, decompose it, look at
Ramadan, stock or weather views. Every section reads the same series (`timeseries.prepare`)."""
from importlib.util import find_spec

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import optional
from workspace_app import timeseries as ts
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta

KEY = "ts-"
SECTIONS = ["Forecast", "Dekomposisi", "Efek Ramadan", "Saham", "Cuaca"]
SECTION_OF_SOURCE = {"saham": "Saham", "pangan": "Efek Ramadan", "openmeteo": "Cuaca"}
VALUE_FIRST = ("adj_close", "close", "harga", "suhu_c", "nilai", "value", "magnitudo", "mag")
FILTER_FIRST = ("symbol", "komoditas", "lokasi", "provinsi", "jenis_pasar", "kategori", "kota", "negara")
SUM_HINTS = ("hujan", "precip", "jumlah", "volume", "count")
NOT_A_VALUE = ("lintang", "bujur", "latitude", "longitude", "lat", "lon", "ketinggian", "tahun", "year", "id")
ALL = "(semua)"
COUNT_ROWS = "__hitung__"
MAX_FILTER_VALUES = 300


def series_page(manager):
    ui.page_header(tr("Deret Waktu"), tr("Perkiraan dengan uji mundur, dekomposisi, efek Ramadan, saham, dan anomali "
                                           "cuaca pada satu deret"))
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-ts-input"):
        job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0, key=KEY + "dataset")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"]).drop(columns=["pekerjaan_asal"], errors="ignore")
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}, "source": job["source"]}
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        chosen = pick_series(frame, job_id)
    if chosen is None:
        return
    prepared, name, variable = chosen
    series = prepared.series
    note = (f"{ui.fmt(len(series))} {tr('titik')} · {ui.short_date(series.index.min())} – "
            f"{ui.short_date(series.index.max())} · {ui.fmt(prepared.rows)} {tr('baris')}")
    if prepared.filled:
        note += f" · {ui.fmt(prepared.filled)} {tr('periode diisi')}"
    st.caption(note)
    if prepared.rows > len(series) * 1.5 and variable != COUNT_ROWS:
        st.caption(tr("Beberapa baris jatuh pada periode yang sama dan digabung. Saring kolom di atas bila ini "
                      "satu deret tunggal."))

    default = next((SECTION_OF_SOURCE[source] for source in origins if source in SECTION_OF_SOURCE), SECTIONS[0])
    section = st.segmented_control(tr("Analisis"), SECTIONS, default=default, required=True, format_func=tr,
                                   key=f"{KEY}section-{job_id}")
    base = an.safe_name(f"{job['project']}-{name}")
    context = {"series": series, "freq": prepared.freq, "name": name, "base": base, "variable": variable,
               "origins": origins}
    {"Forecast": forecast_section, "Dekomposisi": decompose_section, "Efek Ramadan": ramadan_section,
     "Saham": stock_section, "Cuaca": weather_section}[section](context)


# --------------------------------------------------------------------------- #
# Choosing one series
# --------------------------------------------------------------------------- #

def value_columns(frame, dates):
    numeric = [column for column in fam.numeric_columns(frame) if column not in dates
               and str(column).lower() not in NOT_A_VALUE and not str(column).lower().endswith("_id")]
    order = {name: number for number, name in enumerate(VALUE_FIRST)}
    return sorted(numeric, key=lambda column: order.get(str(column).lower(), len(order)))


def filter_columns(frame, dates):
    """Text columns that name something (symbol, commodity, place): few distinct short values."""
    found = []
    for column in frame:
        if column in dates:
            continue
        values = frame[column].dropna()
        if values.empty or not values.map(lambda item: isinstance(item, str)).all():
            continue
        if 2 <= values.nunique() <= MAX_FILTER_VALUES and values.astype(str).str.len().median() <= 40:
            found.append(column)
    order = {name: number for number, name in enumerate(FILTER_FIRST)}
    return sorted(found, key=lambda column: order.get(str(column).lower(), len(order)))[:4]


@st.cache_data(show_spinner=False, max_entries=8)
def build_series(_frame, job_id, count, date_column, value_column, filters, freq, agg):
    selected = _frame
    for column, value in filters:
        selected = selected[selected[column] == value]
    return ts.prepare(selected, date_column, value_column, freq=freq, agg=agg)


def pick_series(frame, job_id):
    dates = an.date_columns(frame)
    if not dates:
        st.info(tr("Dataset ini tidak punya kolom tanggal yang bisa dibaca, jadi tidak ada deret waktu."),
                icon=":material/event:")
        return None
    numbers = value_columns(frame, dates)
    left, right = st.columns(2)
    date = left.selectbox(tr("Kolom tanggal"), dates, key=f"{KEY}date-{job_id}")
    options = [*numbers, COUNT_ROWS]
    value = right.selectbox(tr("Nilai yang dianalisis"), options, key=f"{KEY}value-{job_id}",
                            format_func=lambda item: tr("Jumlah kejadian per periode") if item == COUNT_ROWS else item,
                            help=tr("Kolom angka, atau jumlah baris per periode untuk data kejadian (gempa, titik api, "
                                    "bencana)."))
    filters = []
    columns = filter_columns(frame, dates)
    if columns:
        cols = st.columns(min(len(columns), 4))
        for holder, column in zip(cols, columns):
            counts = frame[column].value_counts()
            choices = [ALL, *counts.index]
            known = str(column).lower() in FILTER_FIRST
            selected = holder.selectbox(column, choices, index=1 if known else 0, key=f"{KEY}filter-{job_id}-{column}")
            if selected != ALL:
                filters.append((column, selected))
    selected = frame
    for column, item in filters:
        selected = selected[selected[column] == item]
    if selected.empty:
        st.info(tr("Tidak ada baris untuk pilihan ini."), icon=":material/filter_alt_off:")
        return None
    freq_guess = ts.guess_frequency(an.parse_dates(selected[date]))
    left, right = st.columns(2)
    freq = left.selectbox(tr("Periode"), list(ts.FREQS), index=list(ts.FREQS).index(freq_guess),
                          format_func=lambda item: tr(ts.FREQS[item][0]), key=f"{KEY}freq-{job_id}-{freq_guess}")
    counting = value == COUNT_ROWS
    agg_default = "sum" if not counting and any(hint in str(value).lower() for hint in SUM_HINTS) else "mean"
    agg = "sum" if counting else right.selectbox(
        tr("Gabungkan dalam periode dengan"), list(ts.AGGREGATIONS), index=list(ts.AGGREGATIONS).index(agg_default),
        format_func=lambda item: tr(ts.AGGREGATIONS[item]), key=f"{KEY}agg-{job_id}-{value}")
    if counting:
        right.caption(tr("Jumlah baris per periode; periode tanpa kejadian bernilai nol."))
    try:
        prepared = build_series(frame, job_id, len(frame), date, None if counting else value, tuple(filters), freq, agg)
    except ts.NotEnough as exc:
        st.warning(str(exc), icon=":material/warning:")
        return None
    name = (tr("jumlah kejadian") if counting else str(value)) + "".join(f" · {item}" for _, item in filters)
    return prepared, name, value


def need(*packages):
    """True when the optional packages are installed; otherwise shows the install command."""
    missing = [name for name in packages if find_spec(name) is None]
    if not missing:
        return True
    st.warning(tr('Paket belum terpasang ({0}). Jalankan di folder aplikasi:', ", ".join(missing)),
               icon=":material/download:")
    st.code(optional.INSTALL_COMMAND, language="bash")
    return False


def method_block(text):
    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(text, language=None, wrap_lines=True)


def blocked(exc):
    st.info(str(exc), icon=":material/rule:")


def layout(figure, height=340, **extra):
    figure.update_layout(height=height, margin={"l": 0, "r": 0, "t": 10, "b": 0}, paper_bgcolor="rgba(0,0,0,0)",
                         plot_bgcolor="rgba(0,0,0,0)", legend={"orientation": "h", "y": 1.12}, **extra)
    return figure


def show(figure):
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def pct(value, places=1):
    return f"{value:.{places}f}%".replace(".", ",")


# --------------------------------------------------------------------------- #
# Forecast
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menjalankan model dan uji mundur…", max_entries=6)
def compute_forecast(series, freq, horizon):
    return ts.forecast(series, freq, horizon)


def forecast_section(context):
    series, freq = context["series"], context["freq"]
    if not need("statsforecast"):
        return
    try:
        ts.check_length(series, freq)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    limit = ts.max_horizon(len(series))
    default = max(1, min(ts.seasonal_length(freq), limit))
    horizon = st.slider(tr("Horizon (periode ke depan)"), 1, limit, default, key=f"{KEY}horizon-{freq}",
                        help=tr("Paling jauh 20% dari panjang data. Perkiraan yang jauh melebar cepat dan jarang "
                                "bisa dipercaya."))
    try:
        result = compute_forecast(series, freq, horizon)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    table = result["tabel"]
    cols = st.columns(4)
    cols[0].metric(tr("Model terbaik"), result["nama_terbaik"], border=True)
    naive = table[table["kode"] == ts.NAIVE].iloc[0]
    best = table.iloc[0]
    cols[1].metric("MASE", f"{best['MASE']:.3f}".replace(".", ","),
                   f"{best['Selisih MASE vs naif (%)']:.0f}% {tr('vs naif')}".replace(".", ","),
                   delta_color="inverse" if result["mengalahkan_naif"] else "off", border=True,
                   help=tr("Galat dibagi galat naif musiman. Di bawah 1 berarti lebih baik daripada naif."))
    cols[2].metric(tr("Jendela uji"), str(result["jendela"]), border=True)
    cols[3].metric(tr("Cakupan selang 95%"), pct(best["Cakupan 95% (%)"], 0), border=True)
    if result["mengalahkan_naif"]:
        st.success(tr('{0} mengalahkan baseline naif pada uji mundur.', result["nama_terbaik"]),
                   icon=":material/check_circle:")
    else:
        st.warning(tr("Tidak ada model yang mengalahkan baseline naif (nilai berikutnya = nilai terakhir). "
                      "Perkiraan di bawah tidak lebih baik daripada tebakan sederhana itu."),
                   icon=":material/warning:")
    future = result["perkiraan"]
    history = series.iloc[-max(60, 3 * horizon):]
    figure = go.Figure()
    for level, opacity in ((95, 0.15), (80, 0.28)):
        figure.add_trace(go.Scatter(x=[*future["Tanggal"], *future["Tanggal"][::-1]],
                                    y=[*future[f"Atas {level}%"], *future[f"Bawah {level}%"][::-1]], fill="toself",
                                    fillcolor=f"rgba(43, 147, 174, {opacity})", line={"width": 0}, hoverinfo="skip",
                                    name=f"{tr('Selang')} {level}%"))
    figure.add_trace(go.Scatter(x=history.index, y=history.values, name=tr("Data"), line={"color": "#6b7785"}))
    figure.add_trace(go.Scatter(x=future["Tanggal"], y=future["Perkiraan"], name=tr("Perkiraan"),
                                line={"color": ui.CHART_ACCENT, "dash": "dash"}))
    show(layout(figure))
    display = table.drop(columns="kode")
    st.dataframe(display, hide_index=True, width="stretch", column_config={
        "MASE": st.column_config.NumberColumn(format="%.3f"), "MAPE (%)": st.column_config.NumberColumn(format="%.2f"),
        "RMSE": st.column_config.NumberColumn(format="%.3g"),
        "Cakupan 95% (%)": st.column_config.NumberColumn(format="%.0f"),
        "Selisih MASE vs naif (%)": st.column_config.NumberColumn(format="%.1f")})
    st.caption(tr("Uji mundur bergulir pada {0} jendela di ujung data; tiap jendela hanya dilatih dari data "
                  "sebelumnya.", result["jendela"]))
    left, right = st.columns(2)
    with left:
        csv_download(future, f"perkiraan-{context['base']}", "dl-ts-forecast")
    with right:
        csv_download(display, f"uji-mundur-{context['base']}", "dl-ts-backtest")
    method_block(ts.forecast_method_text(result, context["name"], freq))


# --------------------------------------------------------------------------- #
# Decomposition and stationarity
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menguraikan deret…", max_entries=6)
def compute_decomposition(series, freq):
    frame, strengths = ts.decompose(series, freq)
    return frame, strengths, ts.stationarity(series)


def decompose_section(context):
    series, freq = context["series"], context["freq"]
    if not need("statsmodels"):
        return
    try:
        frame, strengths, stat = compute_decomposition(series, freq)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    cols = st.columns(3)
    cols[0].metric(tr("Kekuatan tren"), f"{strengths['kekuatan_tren']:.2f}".replace(".", ","), border=True,
                   help=tr("0 sampai 1: seberapa besar variasi yang dijelaskan tren."))
    cols[1].metric(tr("Kekuatan musiman"), f"{strengths['kekuatan_musiman']:.2f}".replace(".", ","), border=True,
                   help=tr("0 sampai 1: seberapa besar variasi yang dijelaskan siklus berulang."))
    cols[2].metric(tr("Siklus musiman"), str(strengths["periode"]), border=True)
    from plotly.subplots import make_subplots

    figure = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.04,
                           subplot_titles=[tr(name) for name in ("Nilai", "Tren", "Musiman", "Sisa")])
    for row, column in enumerate(("Nilai", "Tren", "Musiman", "Sisa"), 1):
        figure.add_trace(go.Scatter(x=frame.index, y=frame[column], line={"color": ui.CHART_ACCENT, "width": 1.5},
                                    showlegend=False), row=row, col=1)
    show(layout(figure, 620))
    level, diff = stat["level"], stat["selisih"]
    table = pd.DataFrame([
        {"Deret": tr("Level"), "ADF p": level["adf_p"], "KPSS p": level["kpss_p"], "Kesimpulan": tr(level["kesimpulan"])},
        {"Deret": tr("Selisih pertama"), "ADF p": diff["adf_p"], "KPSS p": diff["kpss_p"],
         "Kesimpulan": tr(diff["kesimpulan"])}])
    st.subheader(tr("Kestasioneran"), anchor=False)
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "ADF p": st.column_config.NumberColumn(format="%.4f"), "KPSS p": st.column_config.NumberColumn(format="%.4f")})
    st.caption(tr("ADF menguji akar unit (p kecil = stasioner), KPSS menguji kestasioneran (p kecil = tidak "
                  "stasioner). Keduanya dipakai bersama."))
    csv_download(frame.reset_index(names="Tanggal"), f"dekomposisi-{context['base']}", "dl-ts-stl")
    method_block(ts.decompose_method_text(strengths, stat, context["name"], freq))


# --------------------------------------------------------------------------- #
# Ramadan
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung efek Ramadan…", max_entries=6)
def compute_ramadan(series):
    return ts.ramadan_effect(series)


def ramadan_section(context):
    series = context["series"]
    if not need("holidays"):
        return
    try:
        table, test, deviation = compute_ramadan(series)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    cols = st.columns(3)
    cols[0].metric(tr("Rata-rata di Ramadan"), pct(table["Ramadan (%)"].mean()), border=True,
                   help=tr("Simpangan harga terhadap median bergulir 91 hari, rata-rata semua tahun."))
    cols[1].metric(tr("Rata-rata pekan Lebaran"), pct(table["Pekan Lebaran (%)"].mean()), border=True)
    cols[2].metric(tr("Tahun dihitung"), str(len(table)), border=True)
    figure = go.Figure(go.Scatter(x=deviation.index, y=deviation.values, line={"color": "#6b7785", "width": 1},
                                  name=tr("Simpangan (%)")))
    for row in table.itertuples(index=False):
        lebaran = pd.Timestamp(row[1])
        figure.add_vrect(x0=lebaran - pd.Timedelta(days=ts.RAMADAN_DAYS), x1=lebaran, fillcolor=ui.CHART_ACCENT,
                         opacity=0.18, line_width=0)
    figure.add_hline(y=0, line_color="rgba(130,146,162,.6)")
    show(layout(figure, yaxis_title=tr("% dari harga yang diharapkan")))
    st.caption(tr("Blok biru: 30 hari Ramadan sebelum Idul Fitri. Harga yang diharapkan = median bergulir 91 hari."))
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Idul Fitri": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Ramadan (%)": st.column_config.NumberColumn(format="%.1f"),
        "Pekan Lebaran (%)": st.column_config.NumberColumn(format="%.1f"),
        "Sekitar Ramadan di luar itu (%)": st.column_config.NumberColumn(format="%.1f")})
    csv_download(table, f"ramadan-{context['base']}", "dl-ts-ramadan")
    method_block(ts.ramadan_method_text(table, test, context["name"]))


# --------------------------------------------------------------------------- #
# Stock
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung return dan indikator…", max_entries=6)
def compute_stock(series):
    summary, returns, drawdown = ts.returns_summary(series)
    return summary, returns, drawdown, ts.indicators(series)


@st.cache_data(show_spinner="Mencocokkan GARCH…", max_entries=6)
def compute_garch(series):
    return ts.garch(series)


def stock_section(context):
    series = context["series"]
    if (series <= 0).any():
        st.info(tr("Analisis saham butuh harga positif. Pilih kolom harga penutupan."), icon=":material/rule:")
        return
    try:
        summary, returns, drawdown, indicator = compute_stock(series)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    cols = st.columns(4)
    cols[0].metric(tr("Return total"), pct(summary["return_total"]), border=True)
    cols[1].metric(tr("Volatilitas tahunan"), pct(summary["volatilitas_tahunan"]), border=True)
    cols[2].metric(tr("Drawdown maksimum"), pct(summary["drawdown_maks"]), border=True,
                   help=ui.short_date(summary["tanggal_drawdown"]))
    cols[3].metric(tr("Kurtosis berlebih"), f"{summary['kurtosis_lebih']:.1f}".replace(".", ","), border=True,
                   help=tr("Di atas 0: ekor lebih tebal daripada distribusi normal, jadi hari ekstrem lebih sering "
                           "daripada perkiraan model normal."))
    price = go.Figure()
    for column, color in (("Harga", "#6b7785"), ("MA20", ui.CHART_ACCENT), ("MA50", "#d0503c")):
        price.add_trace(go.Scatter(x=indicator.index, y=indicator[column], name=column,
                                   line={"color": color, "width": 2 if column == "Harga" else 1.5}))
    show(layout(price))
    left, right = st.columns(2)
    with left:
        rsi = go.Figure(go.Scatter(x=indicator.index, y=indicator["RSI14"], line={"color": ui.CHART_ACCENT}))
        for level in (30, 70):
            rsi.add_hline(y=level, line_dash="dot", line_color="rgba(130,146,162,.7)")
        st.caption("RSI (14)")
        show(layout(rsi, 220, yaxis={"range": [0, 100]}))
    with right:
        macd = go.Figure()
        macd.add_trace(go.Bar(x=indicator.index, y=indicator["Histogram"], marker_color="rgba(130,146,162,.55)",
                              name="Histogram"))
        macd.add_trace(go.Scatter(x=indicator.index, y=indicator["MACD"], name="MACD", line={"color": ui.CHART_ACCENT}))
        macd.add_trace(go.Scatter(x=indicator.index, y=indicator["Sinyal"], name=tr("Sinyal"),
                                  line={"color": "#d0503c"}))
        st.caption("MACD (12, 26, 9)")
        show(layout(macd, 220))
    st.subheader(tr("Sebaran return harian"), anchor=False)
    hist = go.Figure(go.Histogram(x=returns * 100, nbinsx=60, marker_color=ui.CHART_ACCENT))
    show(layout(hist, 240, xaxis_title=tr("% per hari")))
    st.caption(tr('Jarque–Bera p {0}: {1}', f"{summary['jarque_bera_p']:.4f}".replace(".", ","),
                  tr("return tidak berdistribusi normal.") if summary["jarque_bera_p"] < 0.05
                  else tr("tidak ada bukti menyimpang dari normal.")))

    fit = None
    st.subheader(tr("Volatilitas (GARCH)"), anchor=False)
    if need("arch"):
        try:
            conditional, fit, projected = compute_garch(series)
        except ts.NotEnough as exc:
            blocked(exc)
        else:
            cols = st.columns(3)
            cols[0].metric("α + β", f"{fit['persistensi']:.3f}".replace(".", ","), border=True,
                           help=tr("Persistensi volatilitas: makin dekat 1, guncangan makin lama bertahan."))
            cols[1].metric(tr("Volatilitas jangka panjang"), pct(fit["volatilitas_jangka_panjang"], 2) + tr("/hari"),
                           border=True)
            cols[2].metric(tr("Prakiraan 10 hari"), pct(float(np.mean(projected)), 2) + tr("/hari"), border=True)
            vol = go.Figure(go.Scatter(x=conditional.index, y=conditional.values, line={"color": "#d0503c"}))
            show(layout(vol, 260, yaxis_title=tr("% per hari")))
            csv_download(conditional.rename("volatilitas_bersyarat").to_frame().reset_index(names="Tanggal"),
                         f"garch-{context['base']}", "dl-ts-garch")
    st.caption(tr("Harga saham mendekati random walk: di bagian Forecast, model hampir tak pernah mengalahkan "
                  "baseline naif. Indikator teknikal hanya merangkum harga lampau."))
    csv_download(indicator.reset_index(names="Tanggal"), f"indikator-{context['base']}", "dl-ts-indicators")
    method_block(ts.stock_method_text(summary, fit, context["name"]))


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #

def weather_section(context):
    series, freq = context["series"], context["freq"]
    try:
        frame, climatology = ts.anomalies(series, freq)
    except ts.NotEnough as exc:
        blocked(exc)
        return
    extreme = frame["Skor z"].abs().max()
    cols = st.columns(3)
    cols[0].metric(tr("Anomali terbesar"), f"{frame['Anomali'].loc[frame['Anomali'].abs().idxmax()]:+.1f}".replace(".", ","),
                   ui.short_date(frame["Anomali"].abs().idxmax()), delta_color="off", border=True)
    cols[1].metric(tr("Periode |z| ≥ 2"), ui.fmt(int((frame["Skor z"].abs() >= 2).sum())), border=True)
    cols[2].metric(tr("Skor z terbesar"), f"{extreme:.1f}".replace(".", ","), border=True)
    monthly = frame["Anomali"].resample("ME").mean() if freq in ("D", "B", "W") else frame["Anomali"]
    figure = go.Figure(go.Bar(x=monthly.index, y=monthly.values,
                              marker_color=np.where(monthly.values >= 0, "#d0503c", "#4f7d9c")))
    figure.add_hline(y=0, line_color="rgba(130,146,162,.6)")
    st.caption(tr("Anomali rata-rata per bulan (merah di atas rata-rata, biru di bawah)"))
    show(layout(figure, 300))
    st.caption(tr('Pembanding: {0}. Ini rata-rata rentang data ini, bukan normal iklim 30 tahun.', climatology))
    top = frame.reindex(frame["Skor z"].abs().sort_values(ascending=False).index).head(15).reset_index(names="Tanggal")
    st.subheader(tr("Periode paling menyimpang"), anchor=False)
    st.dataframe(top, hide_index=True, width="stretch", column_config={
        "Tanggal": st.column_config.DateColumn(format="DD MMM YYYY"),
        "Nilai": st.column_config.NumberColumn(format="%.2f"),
        "Rata-rata periode": st.column_config.NumberColumn(format="%.2f"),
        "Anomali": st.column_config.NumberColumn(format="%+.2f"), "Skor z": st.column_config.NumberColumn(format="%+.1f")})
    csv_download(frame.reset_index(names="Tanggal"), f"anomali-{context['base']}", "dl-ts-anomaly")
    method_block(ts.anomaly_method_text(frame, climatology, context["name"]))

