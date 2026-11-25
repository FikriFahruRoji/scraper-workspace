"""Bibliometrik: one page for family G (Google Scholar & SINTA). Pick a dataset, then explore co-author
networks, citations per year, NMF title topics, or SINTA author profile distributions."""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from workspace_app import analysis as an
from workspace_app import bibliometrik as bib
from workspace_app import families as fam
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.sources import label

KEY = "bib-"
SECTIONS = ["Jaringan co-author", "Sitasi per tahun", "Topik judul", "Profil SINTA"]


def bibliometrik_page(manager):
    ui.page_header(
        tr("Bibliometrik"),
        tr("Jaringan co-author, sitasi per tahun, topik judul (NMF), dan sebaran profil SINTA"),
    )
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-bib-input"):
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

    origin = f"data {', '.join(label(source) for source in origins)}" if origins else "dataset ini"
    st.caption(f"{ui.fmt(len(frame))} {tr('baris')} · {origin}")

    default_section = "Profil SINTA" if origins == ["sinta"] else SECTIONS[0]
    section = st.segmented_control(
        tr("Analisis"),
        SECTIONS,
        default=default_section,
        required=True,
        format_func=tr,
        key=f"{KEY}section-{job_id}",
    )
    context = {
        "frame": frame,
        "origin": origin,
        "origins": origins,
        "job_id": job_id,
        "base": an.safe_name(job["project"]),
    }
    {
        "Jaringan co-author": coauthor_section,
        "Sitasi per tahun": citations_section,
        "Topik judul": topics_section,
        "Profil SINTA": sinta_section,
    }[section](context)


def _guard_sinta_document(ctx):
    if ctx["origins"] == ["sinta"]:
        st.info(
            tr("Daftar dokumen SINTA hanya sampel 10 dokumen per penulis dan isinya berubah, "
               "sehingga analisis tingkat dokumen SINTA tidak ditawarkan. Gunakan bagian Profil SINTA."),
            icon=":material/info:",
        )
        return True
    return False


# --------------------------------------------------------------------------- #
# 1. Jaringan co-author
# --------------------------------------------------------------------------- #

def coauthor_section(ctx):
    if _guard_sinta_document(ctx):
        return
    min_weight = st.slider(tr("Minimal publikasi bersama"), min_value=1, max_value=5, value=1,
                           key=f"{KEY}co-minw-{ctx['job_id']}")
    try:
        result = bib.coauthor_network(ctx["frame"], min_pubs=10, min_edge_weight=min_weight)
    except bib.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    st_d = result["stats"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(tr("Penulis"), ui.fmt(st_d["penulis"]))
    c2.metric(tr("Kolaborasi"), ui.fmt(st_d["kolaborasi"]))
    c3.metric(tr("Komunitas"), ui.fmt(st_d["komunitas"]))
    c4.metric(tr("Modularitas"), f"{st_d['modularitas']:.3f}")

    # Plot top 60 authors by collaboration weight to keep browser rendering fast
    nodes_df = result["nodes"]
    top_names = set(nodes_df.head(60)["penulis"])
    graph = result["graph"]
    pos = result["positions"]
    edge_x, edge_y = [], []
    for u, v in graph.edges():
        if u in top_names and v in top_names:
            x0, y0 = pos[u]
            x1, y1 = pos[v]
            edge_x.extend([x0, x1, None])
            edge_y.extend([y0, y1, None])

    sub_nodes = nodes_df[nodes_df["penulis"].isin(top_names)]
    fig = go.Figure()
    if edge_x:
        fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(width=0.8, color="#b8c2cc"),
                                 hoverinfo="none", showlegend=False))
    fig.add_trace(go.Scatter(
        x=[pos[n][0] for n in sub_nodes["penulis"]],
        y=[pos[n][1] for n in sub_nodes["penulis"]],
        mode="markers+text",
        text=sub_nodes["penulis"].head(20).tolist() + [""] * max(0, len(sub_nodes) - 20),
        textposition="top center",
        marker=dict(
            size=(sub_nodes["publikasi"].clip(1, 20) * 2 + 6).tolist(),
            color=sub_nodes["komunitas"].tolist(),
            colorscale="Viridis",
            showscale=False,
        ),
        hovertext=[f"{r.penulis} ({r.publikasi} pub, {r.coauthor} co-author)" for r in sub_nodes.itertuples()],
        hoverinfo="text",
        showlegend=False,
    ))
    fig.update_layout(height=440, margin=dict(l=10, r=10, t=10, b=10),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(nodes_df, use_container_width=True, hide_index=True)
    d1, d2 = st.columns(2)
    with d1:
        csv_download(nodes_df, f"{ctx['base']}-coauthor-simpul.csv", key=f"{KEY}dl-nodes")
    with d2:
        csv_download(result["edges"], f"{ctx['base']}-coauthor-sisi-gephi.csv", key=f"{KEY}dl-edges")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(bib.coauthor_method_text(ctx["origin"], result))


# --------------------------------------------------------------------------- #
# 2. Sitasi per tahun
# --------------------------------------------------------------------------- #

def citations_section(ctx):
    if _guard_sinta_document(ctx):
        return
    try:
        result = bib.citations_by_year(ctx["frame"])
    except bib.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    s = result["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(tr("Publikasi"), ui.fmt(s["total_publikasi"]))
    c2.metric(tr("Total sitasi"), ui.fmt(s["total_sitasi"]))
    c3.metric("h-index", ui.fmt(s["h_index"]))
    c4.metric("i10-index", ui.fmt(s["i10_index"]))

    yearly = result["yearly"]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=yearly["tahun"], y=yearly["publikasi"], name=tr("Publikasi")))
    if s["punya_sitasi"]:
        fig.add_trace(go.Scatter(x=yearly["tahun"], y=yearly["total_sitasi"], name=tr("Total sitasi"),
                                 mode="lines+markers", yaxis="y2"))
        fig.update_layout(yaxis2=dict(title=tr("Total sitasi"), overlaying="y", side="right"))
    fig.update_layout(height=400, margin=dict(l=20, r=20, t=20, b=20),
                      xaxis_title=tr("Tahun"), yaxis_title=tr("Publikasi"))
    st.plotly_chart(fig, use_container_width=True)

    display = yearly.copy()
    display["rata_rata_sitasi"] = display["rata_rata_sitasi"].round(2)
    st.dataframe(display, use_container_width=True, hide_index=True)
    csv_download(yearly, f"{ctx['base']}-sitasi-tahunan.csv", key=f"{KEY}dl-cit")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(bib.citations_method_text(ctx["origin"], result))


# --------------------------------------------------------------------------- #
# 3. Topik judul (NMF)
# --------------------------------------------------------------------------- #

def topics_section(ctx):
    if _guard_sinta_document(ctx):
        return
    n_topics = st.slider(tr("Jumlah topik"), min_value=2, max_value=10, value=5, key=f"{KEY}top-k-{ctx['job_id']}")
    try:
        result = bib.title_topics(ctx["frame"], n_topics=n_topics)
    except bib.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    topics_df = result["topics"]
    fig = px.bar(topics_df, x="jumlah_publikasi", y="topik", orientation="h",
                 labels={"jumlah_publikasi": tr("Jumlah publikasi"), "topik": tr("Topik")})
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=20), yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(topics_df, use_container_width=True, hide_index=True)

    if result["by_year"] is not None:
        fig_yr = px.area(result["by_year"], x="tahun", y="jumlah", color="topik",
                         labels={"tahun": tr("Tahun"), "jumlah": tr("Publikasi"), "topik": tr("Topik")})
        fig_yr.update_layout(height=340, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig_yr, use_container_width=True)

    csv_download(topics_df, f"{ctx['base']}-topik-judul.csv", key=f"{KEY}dl-topics")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(bib.topics_method_text(ctx["origin"], result))


# --------------------------------------------------------------------------- #
# 4. Profil SINTA
# --------------------------------------------------------------------------- #

def sinta_section(ctx):
    try:
        result = bib.sinta_profile_analysis(ctx["frame"])
    except bib.NotEnough as exc:
        st.info(tr(str(exc)), icon=":material/info:")
        return

    profiles = result["profiles"]
    c1, c2, c3 = st.columns(3)
    c1.metric(tr("Profil penulis"), ui.fmt(result["n_profiles"]))
    med_scopus = profiles["scopus_h_index"].median()
    med_gs = profiles["gscholar_h_index"].median()
    c2.metric("Median h-index Scopus", f"{med_scopus:.1f}" if pd.notna(med_scopus) else "—")
    c3.metric("Median h-index GS", f"{med_gs:.1f}" if pd.notna(med_gs) else "—")

    if result["corr_h"]:
        paired = profiles.dropna(subset=["scopus_h_index", "gscholar_h_index"])
        fig = px.scatter(
            paired,
            x="scopus_h_index",
            y="gscholar_h_index",
            hover_name="nama",
            labels={"scopus_h_index": "Scopus h-index", "gscholar_h_index": "Google Scholar h-index"},
        )
        fig.update_layout(height=380, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)

    if not result["by_prodi"].empty:
        st.subheader(tr("Sebaran per program studi"), anchor=False)
        st.dataframe(result["by_prodi"], use_container_width=True, hide_index=True)
    if not result["by_afiliasi"].empty:
        st.subheader(tr("Sebaran per afiliasi"), anchor=False)
        st.dataframe(result["by_afiliasi"], use_container_width=True, hide_index=True)

    csv_download(profiles, f"{ctx['base']}-profil-sinta.csv", key=f"{KEY}dl-sinta")
    with st.expander(tr("Teks metode untuk riset"), icon=":material/description:"):
        st.write(bib.sinta_method_text(ctx["origin"], result))
