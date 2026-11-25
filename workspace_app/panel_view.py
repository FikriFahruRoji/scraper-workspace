"""Panel Negara: one page for family E (World Bank, IMF, OECD). Pick a country-year dataset, then explore
trends & CAGR, indicator correlations, k-means country clusters, or fixed-effects panel regression."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import optional
from workspace_app import panel as pn
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.sources import label

KEY = "pn-"
SECTIONS = ["Tren & CAGR", "Korelasi", "Klaster negara", "Regresi panel"]


def panel_page(manager):
    ui.page_header(
        tr("Panel Negara"),
        tr("Tren & CAGR, korelasi antarindikator, klaster negara, dan regresi panel efek tetap (tanpa forecast)"),
    )
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-pn-input"):
        job_id = st.selectbox(
            tr("Dataset"),
            list(by_id),
            format_func=lambda value: option_label(by_id[value]),
            index=list(by_id).index(remembered) if remembered in by_id else 0,
            key=KEY + "dataset",
        )
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"]).drop(columns=["pekerjaan_asal"], errors="ignore")
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}, "source": job["source"]}
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        try:
            prepared = prepare_panel(frame, job_id, job["count"])
        except pn.NotEnough as exc:
            st.info(tr(str(exc)), icon=":material/globe_asia:")
            return

    rep = prepared.report
    origin = f"data {', '.join(label(source) for source in origins)}" if origins else "dataset ini"
    note = (
        f"{ui.fmt(rep['negara'])} {tr('negara')} · {ui.fmt(rep['tahun'])} {tr('tahun')} "
        f"({min(prepared.years)}–{max(prepared.years)}) · {ui.fmt(rep['indikator'])} {tr('indikator')} · "
        f"{ui.fmt(rep['dipakai'])} {tr('baris valid')}"
    )
    if rep["dibuang"]:
        note += f" · {ui.fmt(rep['dibuang'])} {tr('dibuang')}"
    st.caption(note)

    section = st.segmented_control(
        tr("Analisis"),
        SECTIONS,
        default=SECTIONS[0],
        required=True,
        format_func=tr,
        key=f"{KEY}section-{job_id}",
    )
    context = {
        "panel": prepared,
        "origin": origin,
        "job_id": job_id,
        "base": an.safe_name(job["project"]),
    }
    {
        "Tren & CAGR": trend_section,
        "Korelasi": correlation_section,
        "Klaster negara": cluster_section,
        "Regresi panel": regression_section,
    }[section](context)


@st.cache_data(show_spinner=False, max_entries=4)
def prepare_panel(_frame, job_id, count):
    return pn.prepare(_frame)


# --------------------------------------------------------------------------- #
# 1. Tren & CAGR
# --------------------------------------------------------------------------- #

def trend_section(ctx):
    prepared = ctx["panel"]
    indicator = st.selectbox(
        tr("Indikator"),
        prepared.indicators,
        key=f"{KEY}trend-ind-{ctx['job_id']}",
    )
    default_countries = prepared.countries[:8]
    countries = st.multiselect(
        tr("Negara (kosongkan untuk semua)"),
        prepared.countries,
        default=default_countries,
        key=f"{KEY}trend-countries-{ctx['job_id']}",
    )
    try:
        result = pn.trend_cagr(prepared, indicator, countries=countries or None)
    except pn.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    series_df = result["series"]
    fig = px.line(
        series_df,
        x="tahun",
        y="nilai",
        color="negara",
        markers=True,
        labels={"tahun": tr("Tahun"), "nilai": tr("Nilai"), "negara": tr("Negara")},
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=20, b=20), legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)

    summary = result["summary"]
    c1, c2, c3 = st.columns(3)
    c1.metric(tr("Negara"), ui.fmt(len(summary)))
    c2.metric(tr("Rentang tahun"), f"{min(result['years'])}–{max(result['years'])}")
    valid_cagr = summary["cagr_persen"].dropna()
    c3.metric(tr("Median CAGR"), f"{valid_cagr.median():.2f}%" if not valid_cagr.empty else "—")

    display = summary.copy()
    for col in ("nilai_awal", "nilai_akhir", "perubahan"):
        display[col] = display[col].map(lambda v: round(v, 4) if pd.notna(v) else None)
    for col in ("perubahan_persen", "cagr_persen"):
        display[col] = display[col].map(lambda v: round(v, 2) if pd.notna(v) else None)
    st.dataframe(display, use_container_width=True, hide_index=True)
    csv_download(summary, f"{ctx['base']}-tren-cagr.csv", key=f"{KEY}dl-trend")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(pn.trend_method_text(ctx["origin"], indicator, result))


# --------------------------------------------------------------------------- #
# 2. Korelasi antarindikator
# --------------------------------------------------------------------------- #

def correlation_section(ctx):
    prepared = ctx["panel"]
    if len(prepared.indicators) < 2:
        st.info(tr("Butuh minimal 2 indikator untuk korelasi. Gabungkan beberapa dataset indikator atau ambil "
                   "beberapa indikator sekaligus."), icon=":material/info:")
        return

    c1, c2, c3 = st.columns([2.5, 1, 1])
    selected = c1.multiselect(
        tr("Indikator"),
        prepared.indicators,
        default=prepared.indicators[:6],
        key=f"{KEY}corr-inds-{ctx['job_id']}",
    )
    method = c2.selectbox(tr("Metode"), ["pearson", "spearman"], format_func=str.title, key=f"{KEY}corr-method")
    year_options = [None, *prepared.years]
    year = c3.selectbox(
        tr("Tahun"),
        year_options,
        format_func=lambda y: tr("Semua tahun") if y is None else str(y),
        key=f"{KEY}corr-year",
    )

    try:
        result = pn.indicator_correlation(prepared, indicators=selected, method=method, year=year)
    except pn.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    matrix = result["matrix"]
    fig = go.Figure(
        data=go.Heatmap(
            z=matrix.values,
            x=matrix.columns.tolist(),
            y=matrix.index.tolist(),
            zmin=-1,
            zmax=1,
            colorscale="RdBu",
            text=matrix.round(2).values,
            texttemplate="%{text}",
        )
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    pairs = result["pairs"].copy()
    pairs["r"] = pairs["r"].map(lambda v: round(v, 4) if pd.notna(v) else None)
    pairs["p_value"] = pairs["p_value"].map(lambda v: round(v, 5) if pd.notna(v) else None)
    st.dataframe(pairs, use_container_width=True, hide_index=True)
    csv_download(result["pairs"], f"{ctx['base']}-korelasi.csv", key=f"{KEY}dl-corr")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(pn.correlation_method_text(ctx["origin"], result))


# --------------------------------------------------------------------------- #
# 3. Klaster negara
# --------------------------------------------------------------------------- #

def cluster_section(ctx):
    prepared = ctx["panel"]
    c1, c2, c3, c4 = st.columns([2.2, 1, 1, 1])
    selected = c1.multiselect(
        tr("Indikator untuk klaster"),
        prepared.indicators,
        default=prepared.indicators[:4],
        key=f"{KEY}cl-inds-{ctx['job_id']}",
    )
    year_options = [None, *prepared.years]
    year = c2.selectbox(
        tr("Tahun"),
        year_options,
        format_func=lambda y: tr("Semua tahun") if y is None else str(y),
        key=f"{KEY}cl-year",
    )
    aggregation = c3.selectbox(
        tr("Ringkasan"),
        ["latest", "mean"],
        format_func=lambda v: tr("Tahun terakhir") if v == "latest" else tr("Rata-rata"),
        disabled=year is not None,
        key=f"{KEY}cl-agg",
    )
    max_k = max(2, min(8, len(prepared.countries) - 1))
    k = c4.number_input(tr("Jumlah klaster (k)"), min_value=2, max_value=max_k, value=min(3, max_k), step=1,
                        key=f"{KEY}cl-k")

    try:
        result = pn.cluster_countries(prepared, indicators=selected, year=year, aggregation=aggregation, n_clusters=k)
    except pn.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    m1, m2, m3 = st.columns(3)
    m1.metric(tr("Negara dikelompokkan"), ui.fmt(result["n_countries"]))
    m2.metric(tr("Klaster"), ui.fmt(result["n_clusters"]))
    m3.metric("Silhouette", f"{result['silhouette']:.3f}" if pd.notna(result["silhouette"]) else "—")

    assignments = result["assignments"]
    if len(result["indicators"]) >= 2:
        x_col, y_col = result["indicators"][0], result["indicators"][1]
        fig = px.scatter(
            assignments,
            x=x_col,
            y=y_col,
            color=assignments["klaster"].astype(str),
            hover_name="negara",
            labels={"color": tr("Klaster")},
        )
        fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader(tr("Rata-rata tiap klaster"), anchor=False)
    st.dataframe(result["centers"], use_container_width=True, hide_index=True)
    st.subheader(tr("Anggota klaster"), anchor=False)
    st.dataframe(assignments, use_container_width=True, hide_index=True)
    csv_download(assignments, f"{ctx['base']}-klaster-negara.csv", key=f"{KEY}dl-cluster")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(pn.cluster_method_text(ctx["origin"], result))


# --------------------------------------------------------------------------- #
# 4. Regresi panel efek tetap
# --------------------------------------------------------------------------- #

def regression_section(ctx):
    prepared = ctx["panel"]
    if not optional.available("panel"):
        st.info(tr("Regresi panel efek tetap membutuhkan paket opsional `linearmodels`."), icon=":material/extension:")
        st.code(optional.INSTALL_COMMAND, language="bash")
        return

    if len(prepared.indicators) < 2:
        st.info(tr("Butuh minimal 2 indikator (1 dependen dan ≥ 1 independen) untuk regresi panel."),
                icon=":material/info:")
        return

    c1, c2 = st.columns([1.5, 2.5])
    dependent = c1.selectbox(tr("Variabel dependen (Y)"), prepared.indicators, key=f"{KEY}reg-y-{ctx['job_id']}")
    indep_choices = [col for col in prepared.indicators if col != dependent]
    independents = c2.multiselect(
        tr("Variabel independen (X)"),
        indep_choices,
        default=indep_choices[:2],
        key=f"{KEY}reg-x-{ctx['job_id']}",
    )
    e1, e2 = st.columns(2)
    entity_effects = e1.checkbox(tr("Efek tetap negara (Entity FE)"), value=True, key=f"{KEY}reg-fe-entity")
    time_effects = e2.checkbox(tr("Efek tetap tahun (Time FE)"), value=False, key=f"{KEY}reg-fe-time")

    try:
        result = pn.panel_regression(
            prepared,
            dependent=dependent,
            independents=independents,
            entity_effects=entity_effects,
            time_effects=time_effects,
        )
    except pn.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("N", ui.fmt(result["n_obs"]))
    m2.metric(tr("Negara"), ui.fmt(result["n_countries"]))
    m3.metric("R² within", f"{result['r2_within']:.4f}")
    m4.metric("F p-value", f"{result['f_pvalue']:.4f}" if pd.notna(result["f_pvalue"]) else "—")

    coef = result["coefficients"].copy()
    for col in ("koefisien", "galat_baku", "t_stat", "p_value", "ci_bawah", "ci_atas"):
        coef[col] = coef[col].map(lambda v: round(v, 5) if pd.notna(v) else None)
    st.dataframe(coef, use_container_width=True, hide_index=True)
    csv_download(result["coefficients"], f"{ctx['base']}-regresi-panel.csv", key=f"{KEY}dl-reg")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(pn.regression_method_text(ctx["origin"], result))
