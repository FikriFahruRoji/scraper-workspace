"""Lintas Dataset: align two datasets by date (and optional region), then run lag correlation
and bidirectional Granger causality tests (statsmodels)."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from workspace_app import analysis as an
from workspace_app import cross as cr
from workspace_app import families as fam
from workspace_app import optional
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta

KEY = "cr-"
FREQS = [("D", "Harian"), ("W", "Mingguan"), ("M", "Bulanan")]
AGGS = [("mean", "Rata-rata"), ("sum", "Jumlah (sum)"), ("median", "Median"), ("max", "Maksimum"), ("last", "Nilai terakhir")]


def _value_options(frame):
    opts = [cr.COUNT_ROWS]
    if any(str(c).lower() == "sentimen" for c in frame.columns):
        opts.append(cr.NET_SENTIMENT)
    opts.extend(fam.numeric_columns(frame))
    return opts


def _value_label(val):
    if val == cr.COUNT_ROWS:
        return tr("Volume / jumlah baris")
    if val == cr.NET_SENTIMENT:
        return tr("Skor sentimen bersih (−1 s.d. +1)")
    return str(val)


def _pick_dataset_side(side, jobs, by_id, default_idx):
    st.subheader(tr(f"Dataset {side}"), anchor=False)
    job_id = st.selectbox(
        tr("Dataset"),
        list(by_id),
        index=default_idx,
        format_func=lambda v: option_label(by_id[v]),
        key=f"{KEY}{side}-job",
    )
    job = by_id[job_id]
    frame = load_frame(st.session_state["_cross_mgr"], job_id, job["count"]).drop(
        columns=["pekerjaan_asal"], errors="ignore"
    )
    dates = an.date_columns(frame)
    if not dates:
        st.warning(tr("Dataset ini tidak punya kolom tanggal."))
        return None

    date_col = st.selectbox(tr("Kolom tanggal"), dates, key=f"{KEY}{side}-date-{job_id}")
    val_opts = _value_options(frame)
    val_col = st.selectbox(tr("Nilai yang dibandingkan"), val_opts, format_func=_value_label,
                           key=f"{KEY}{side}-val-{job_id}")
    agg = "mean"
    if val_col not in (cr.COUNT_ROWS, cr.NET_SENTIMENT):
        agg = st.selectbox(tr("Agregasi per periode"), [a[0] for a in AGGS],
                           format_func=lambda v: tr(dict(AGGS)[v]), key=f"{KEY}{side}-agg-{job_id}")

    reg_cols = cr.region_columns(frame)
    reg_col, reg_val = None, None
    if reg_cols:
        reg_col = st.selectbox(tr("Filter wilayah (opsional)"), [None, *reg_cols],
                               format_func=lambda v: tr("Tanpa filter") if v is None else str(v),
                               key=f"{KEY}{side}-regcol-{job_id}")
        if reg_col is not None:
            values = sorted(frame[reg_col].dropna().astype(str).unique().tolist())[:300]
            reg_val = st.selectbox(tr("Pilih wilayah"), values, key=f"{KEY}{side}-regval-{job_id}")

    label_name = f"{job['project']} ({_value_label(val_col)})"
    return {
        "job": job,
        "frame": frame,
        "date_col": date_col,
        "value_col": val_col,
        "agg": agg,
        "region_col": reg_col,
        "region_value": reg_val,
        "label": label_name,
    }


def cross_page(manager):
    ui.page_header(
        tr("Lintas Dataset"),
        tr("Selaraskan dua dataset per tanggal dan wilayah untuk korelasi berlag serta uji kausalitas Granger"),
    )
    jobs = finished(manager)
    if len(jobs) < 2:
        if not jobs:
            empty_state()
        else:
            st.info(tr("Butuh minimal 2 dataset selesai untuk analisis lintas dataset."), icon=":material/info:")
        return

    st.session_state["_cross_mgr"] = manager
    by_id = {job["id"]: job for job in jobs}

    with st.container(key="panel-cross-input"):
        col_a, col_b = st.columns(2)
        with col_a:
            side_a = _pick_dataset_side("A", jobs, by_id, 0)
        with col_b:
            side_b = _pick_dataset_side("B", jobs, by_id, min(1, len(jobs) - 1))

        c1, c2, c3, c4 = st.columns(4)
        freq = c1.selectbox(tr("Frekuensi"), [f[0] for f in FREQS],
                            format_func=lambda v: tr(dict(FREQS)[v]), key=f"{KEY}freq")
        fill = c2.selectbox(tr("Penyelarasan"), ["drop", "ffill"],
                            format_func=lambda v: tr("Irisan tanggal") if v == "drop" else tr("Isi maju (ffill)"),
                            key=f"{KEY}fill")
        max_lag = c3.number_input(tr("Lag maksimum"), min_value=1, max_value=30, value=7, step=1, key=f"{KEY}lag")
        method = c4.selectbox(tr("Korelasi"), ["pearson", "spearman"], format_func=str.title, key=f"{KEY}method")

    if side_a is None or side_b is None:
        return

    try:
        s_a = cr.extract_series(
            side_a["frame"], date_col=side_a["date_col"], value_col=side_a["value_col"],
            region_col=side_a["region_col"], region_value=side_a["region_value"],
            freq=freq, agg=side_a["agg"],
        )
        s_b = cr.extract_series(
            side_b["frame"], date_col=side_b["date_col"], value_col=side_b["value_col"],
            region_col=side_b["region_col"], region_value=side_b["region_value"],
            freq=freq, agg=side_b["agg"],
        )
        aligned_res = cr.align_series(s_a, s_b, name_a=side_a["label"], name_b=side_b["label"],
                                      freq=freq, fill=fill)
        lag_res = cr.lag_correlation(aligned_res, max_lag=int(max_lag), method=method)
    except cr.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    df_al = aligned_res["aligned"]
    m1, m2, m3 = st.columns(3)
    m1.metric(tr("Titik beririsan"), ui.fmt(aligned_res["n_points"]))
    m2.metric(tr("Rentang"), f"{ui.short_date(aligned_res['start'])} – {ui.short_date(aligned_res['end'])}")
    if lag_res["best"]:
        m3.metric(tr("Korelasi terkuat"), f"r = {lag_res['best']['r']:.3f} (lag {lag_res['best']['lag']})")
    else:
        m3.metric(tr("Korelasi terkuat"), "—")

    # Dual-axis chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_al.index, y=df_al[aligned_res["name_a"]], name=aligned_res["name_a"], mode="lines+markers"))
    fig.add_trace(go.Scatter(x=df_al.index, y=df_al[aligned_res["name_b"]], name=aligned_res["name_b"],
                             mode="lines+markers", yaxis="y2"))
    fig.update_layout(
        height=380,
        margin=dict(l=20, r=20, t=20, b=20),
        yaxis=dict(title=aligned_res["name_a"]),
        yaxis2=dict(title=aligned_res["name_b"], overlaying="y", side="right"),
        legend=dict(orientation="h", y=1.12),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader(tr("Korelasi berlag"), anchor=False)
    lags_df = lag_res["lags"]
    fig_lag = go.Figure(go.Bar(x=lags_df["lag"], y=lags_df["r"]))
    fig_lag.update_layout(height=300, margin=dict(l=20, r=20, t=10, b=20),
                          xaxis_title=tr("Lag (positif: A mendahului B)"), yaxis_title="r")
    st.plotly_chart(fig_lag, use_container_width=True)

    disp_lags = lags_df.copy()
    disp_lags["r"] = disp_lags["r"].round(4)
    disp_lags["p_value"] = disp_lags["p_value"].round(5)
    st.dataframe(disp_lags, use_container_width=True, hide_index=True)

    base = an.safe_name(f"{side_a['job']['project']}-{side_b['job']['project']}")
    csv_download(lags_df, f"{base}-korelasi-lag.csv", key=f"{KEY}dl-lag")

    st.subheader(tr("Uji kausalitas Granger"), anchor=False)
    st.caption(tr("Catatan: korelasi dan uji Granger mengukur kemampuan prediksi berurutan waktu, bukan bukti hubungan sebab akibat."))
    granger_res = None
    if not optional.available("cross"):
        st.info(tr("Uji kausalitas Granger membutuhkan paket opsional `statsmodels`."), icon=":material/extension:")
        st.code(optional.INSTALL_COMMAND, language="bash")
    else:
        g1, g2 = st.columns(2)
        g_lag = g1.number_input(tr("Lag maksimum Granger"), min_value=1, max_value=12,
                                value=min(4, max(1, aligned_res["n_points"] // 8)), step=1, key=f"{KEY}g-lag")
        diff = g2.checkbox(tr("Gunakan selisih pertama (first difference)"), value=True, key=f"{KEY}g-diff")
        try:
            granger_res = cr.granger_test(aligned_res, max_lag=int(g_lag), difference=diff)
            g_disp = granger_res["results"].copy()
            g_disp["f_stat"] = g_disp["f_stat"].round(4)
            g_disp["p_value"] = g_disp["p_value"].round(5)
            st.dataframe(g_disp, use_container_width=True, hide_index=True)
            csv_download(granger_res["results"], f"{base}-granger.csv", key=f"{KEY}dl-granger")
        except cr.NotEnough as exc:
            st.info(tr(str(exc)), icon=":material/info:")

    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(cr.cross_method_text(aligned_res, lag_res, granger_res))
