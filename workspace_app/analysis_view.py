"""Analisis: analyses that run locally on a saved dataset."""
from functools import partial

import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

from workspace_app import analysis as an
from workspace_app import ui
from workspace_app.pick import empty_state, finished, option_label

PAGES = {
    "statistik": (":material/table_chart:", "Statistik Data", "Ringkasan kolom, kelengkapan, dan sebaran nilai"),
    "frekuensi": (":material/format_list_numbered:", "Frekuensi Kata", "Kata dan pasangan kata yang paling sering muncul"),
    "tren": (":material/trending_up:", "Tren Waktu", "Perubahan jumlah data dari waktu ke waktu"),
    "topik": (":material/category:", "Topik Teks", "Kelompok topik yang muncul dari kumpulan teks"),
    "jaringan": (":material/hub:", "Jaringan Kata", "Kata-kata yang sering muncul bersama dalam satu teks"),
    # Media sosial (keluarga B); the renderers live in social_view.
    "balasan": (":material/forum:", "Jaringan Balasan", "Siapa membalas siapa, akun sentral, dan komunitas percakapan"),
    "interaksi": (":material/group:", "Interaksi & Akun", "Teks paling disukai, akun paling aktif, dan sentimen × suka"),
    "berulang": (":material/content_copy:", "Teks Berulang", "Komentar yang sangat mirip satu sama lain"),
    # Berita (keluarga C); the renderer lives in news_view.
    "pembeda": (":material/compare_arrows:", "Kata Pembeda", "Kata yang khas bagi satu portal dibanding portal lain"),
}
# General tools that suit any dataset sit in the menu's Eksplorasi group; the other pages are analyses for one
# data family and sit under Analisis.
EXPLORATION = ("statistik", "frekuensi", "tren", "topik", "jaringan")


@st.cache_data(show_spinner="Memuat data…", max_entries=3)
def load_frame(_manager, job_id, count):
    return pd.DataFrame(_manager.input_rows([job_id]))


@st.cache_data(show_spinner="Membersihkan teks…", max_entries=4)
def load_documents(_manager, job_id, count, columns):
    frame = load_frame(_manager, job_id, count)
    return an.cleaned_documents(frame.to_dict("records"), list(columns))


def csv_download(frame, name, key):
    st.download_button(tr("Unduh tabel (CSV)"), frame.to_csv(index=False).encode("utf-8-sig"), f"{name}.csv",
                       "text/csv", icon=":material/download:", key=key, on_click="ignore")


def analysis_page(manager, key):
    _, title, subtitle = PAGES[key]
    ui.page_header(title, subtitle)
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-analysis-input"):
        job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0,
                              key=f"an-dataset-{key}")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"])
        renderer = RENDERERS.get(key)
        if renderer is None:
            from workspace_app.news_view import RENDERERS as NEWS
            from workspace_app.social_view import RENDERERS as SOCIAL
            renderer = {**SOCIAL, **NEWS}[key]
        renderer(manager, job, frame)


# --------------------------------------------------------------------------- #
# Statistik Data
# --------------------------------------------------------------------------- #

def render_statistics(manager, job, frame):
    frame = frame.drop(columns=["pekerjaan_asal"], errors="ignore")
    profile = an.column_profile(frame)
    cols = st.columns(3)
    cols[0].metric(tr("Baris"), ui.fmt(len(frame)), border=True)
    cols[1].metric(tr("Kolom"), ui.fmt(frame.shape[1]), border=True)
    cols[2].metric(tr("Rata-rata terisi"), f"{profile['Terisi (%)'].mean():.0f}%".replace(".", ","), border=True)

    st.subheader(tr("Kolom"), anchor=False)
    st.dataframe(profile, hide_index=True, width="stretch",
                 column_config={"Terisi (%)": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                              format="%.0f%%")})
    numbers = an.numeric_summary(frame)
    if not numbers.empty:
        st.subheader(tr("Kolom angka"), anchor=False)
        st.dataframe(numbers, hide_index=True, width="stretch")

    candidates = [name for name in frame if frame[name].map(lambda value: isinstance(value, (list, dict))).sum() == 0
                  and 1 < frame[name].nunique() <= max(50, len(frame) // 4)]
    if candidates:
        st.subheader(tr("Sebaran nilai"), anchor=False)
        column = st.selectbox(tr("Kolom"), candidates, key="stat-column")
        counts = an.category_counts(frame[column])
        st.bar_chart(counts, x="Nilai", y="Jumlah", horizontal=True, sort="-Jumlah")
    csv_download(profile, f"statistik-{an.safe_name(job['project'])}", "dl-stat")


# --------------------------------------------------------------------------- #
# Frekuensi Kata and the text pickers
# --------------------------------------------------------------------------- #

def choose_text(manager, job, frame, key):
    columns = an.text_columns(frame)
    if not columns:
        st.info(tr("Dataset ini tidak punya kolom teks. Pilih dataset lain, misalnya hasil scraping berita atau ulasan."),
                icon=":material/text_fields:")
        return None
    default = an.default_text_columns(columns)
    chosen = st.multiselect(tr("Kolom teks"), columns, default=default, key=f"{key}-columns",
                            help=tr("Beberapa kolom digabung. Dataset hasil Preprocessing memakai teks_bersih."))
    if not chosen:
        return None
    documents, skipped = load_documents(manager, job["id"], job["count"], tuple(chosen))
    if not documents:
        st.info(tr("Tidak ada teks yang tersisa setelah dibersihkan. Coba kolom lain."), icon=":material/text_fields:")
        return None
    note = f"{ui.fmt(len(documents))} teks dianalisis"
    if skipped:
        note += f", {ui.fmt(skipped)} baris kosong dilewati"
    if len(documents) >= an.MAX_DOCUMENTS:
        note += f". Dibatasi {ui.fmt(an.MAX_DOCUMENTS)} teks pertama"
    st.caption(note + ".")
    return documents


def render_frequency(manager, job, frame):
    documents = choose_text(manager, job, frame, "freq")
    if documents is None:
        return
    top = st.slider(tr("Jumlah kata teratas"), 10, 50, 25, key="freq-top")
    words, pairs = an.word_counts(documents, top), an.bigram_counts(documents, top)
    unique = len({word for text in documents for word in text.split()})
    cols = st.columns(2)
    cols[0].metric(tr("Teks"), ui.fmt(len(documents)), border=True)
    cols[1].metric(tr("Kata berbeda"), ui.fmt(unique), border=True)
    words_tab, pairs_tab = st.tabs([tr("Kata"), tr("Pasangan kata")])
    with words_tab:
        st.bar_chart(words, x="Kata", y="Jumlah", horizontal=True, sort="-Jumlah")
        csv_download(words, f"kata-{an.safe_name(job['project'])}", "dl-words")
    with pairs_tab:
        st.bar_chart(pairs, x="Pasangan kata", y="Jumlah", horizontal=True, sort="-Jumlah")
        csv_download(pairs, f"pasangan-kata-{an.safe_name(job['project'])}", "dl-pairs")


# --------------------------------------------------------------------------- #
# Tren Waktu
# --------------------------------------------------------------------------- #

def render_trend(manager, job, frame):
    dates = an.date_columns(frame)
    if not dates:
        st.info(tr("Dataset ini tidak punya kolom tanggal atau waktu yang bisa dibaca."), icon=":material/event:")
        return
    left, right = st.columns(2)
    column = left.selectbox(tr("Kolom tanggal"), dates, key="trend-date")
    period = right.segmented_control(tr("Periode"), list(an.PERIODS), default="D", key="trend-period",
                                     format_func=lambda key: tr(an.PERIODS[key]), required=True)
    groups = [name for name in frame if name != column and frame[name].map(lambda v: isinstance(v, str)).all()
              and 1 < frame[name].nunique() <= 30]
    group = st.selectbox(tr("Bandingkan menurut (opsional)"), [None, *groups], key="trend-group",
                         format_func=lambda name: tr("Tanpa pembanding") if name is None else name,
                         help=tr("Menampilkan lima kelompok terbesar berdampingan."))
    table, skipped = an.volume_over_time(frame, column, period, group)
    if table.empty:
        st.info(tr("Tidak ada tanggal yang bisa dibaca di kolom ini."), icon=":material/event:")
        return
    totals = table.sum(axis=1)
    peak = totals.idxmax()
    cols = st.columns(2)
    cols[0].metric(tr('Puncak ({0} baris)', ui.fmt(totals.max())), ui.short_date(peak), border=True)
    cols[1].metric(tr("Rata-rata per periode"), ui.fmt(round(totals.mean())), border=True)
    st.caption(tr('Rentang data: {0} sampai {1}.', ui.short_date(table.index.min()), ui.short_date(table.index.max())))
    if len(table) > 1:
        st.line_chart(table)
    else:
        st.bar_chart(table)
    if skipped:
        st.caption(tr('{0} baris dilewati karena tanggalnya kosong atau tidak terbaca. Waktu dengan zona dihitung dalam WIB.', ui.fmt(skipped)))
    csv_download(table.reset_index(), f"tren-{an.safe_name(job['project'])}", "dl-trend")


# --------------------------------------------------------------------------- #
# Topik Teks
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung topik…", max_entries=4)
def compute_topics(documents, count):
    return an.topics(list(documents), count)


def render_topics(manager, job, frame):
    documents = choose_text(manager, job, frame, "topic")
    if documents is None:
        return
    count = st.slider(tr("Jumlah topik"), 2, 10, 5, key="topic-count",
                      help=tr("Mulai dari 4 sampai 6. Naikkan bila satu topik terasa mencampur beberapa hal."))
    try:
        table, assignment = compute_topics(tuple(documents), count)
    except ValueError as exc:
        st.warning(str(exc), icon=":material/warning:")
        return
    st.dataframe(table, hide_index=True, width="stretch")
    st.bar_chart(table, x="Topik", y="Dokumen", horizontal=True)
    with st.expander(tr("Contoh teks per topik"), icon=":material/format_quote:"):
        for number, name in enumerate(table["Topik"]):
            examples = [text for text, topic in zip(documents, assignment) if topic == number][:3]
            st.markdown(f"**{name}**")
            for text in examples:
                st.caption(text[:200] + ("…" if len(text) > 200 else ""))
    st.caption(tr("Topik dihitung dengan TF-IDF dan NMF. Hasilnya dugaan statistik: periksa contoh teksnya "
               "sebelum menamai topik."))
    csv_download(table, f"topik-{an.safe_name(job['project'])}", "dl-topic")


# --------------------------------------------------------------------------- #
# Jaringan Kata
# --------------------------------------------------------------------------- #

def render_network(manager, job, frame):
    import plotly.graph_objects as go

    documents = choose_text(manager, job, frame, "net")
    if documents is None:
        return
    left, right = st.columns(2)
    words = left.slider(tr("Jumlah kata"), 10, 60, 30, key="net-words")
    suggested = an.suggested_min_edge(documents, words)
    minimum = right.slider(tr("Muncul bersama minimal (kali)"), 1, max(20, suggested * 3), suggested,
                           key=f"net-min-{job['id']}-{words}",
                           help=tr("Garis hanya digambar bila dua kata muncul bersama sebanyak ini. "
                                "Nilai awal dipilih agar grafik tetap terbaca."))
    nodes, edges = an.word_network(documents, words, minimum)
    if nodes.empty:
        st.info(tr("Tidak ada pasangan kata yang cukup sering muncul bersama. Turunkan batas minimal."),
                icon=":material/hub:")
        return
    at = nodes.set_index("word")
    heaviest = edges["weight"].max()
    figure = go.Figure()
    for edge in edges.itertuples():
        figure.add_trace(go.Scatter(
            x=[at.x[edge.a], at.x[edge.b]], y=[at.y[edge.a], at.y[edge.b]], mode="lines", hoverinfo="skip",
            line={"width": 0.6 + 4 * edge.weight / heaviest, "color": ui.CHART_LINE}, showlegend=False))
    figure.add_trace(go.Scatter(
        x=nodes["x"], y=nodes["y"], mode="markers+text", text=nodes["word"], textposition="top center",
        hovertemplate="%{text}<br>muncul di %{customdata} teks<extra></extra>", customdata=nodes["count"],
        marker={"size": 10 + 28 * nodes["count"] / nodes["count"].max(), "color": ui.CHART_ACCENT,
                "line": {"width": 1, "color": ui.CHART_SURFACE}},
        textfont={"size": 13}, showlegend=False))
    figure.update_layout(height=560, margin={"l": 10, "r": 10, "t": 10, "b": 10}, hovermode="closest",
                         xaxis={"visible": False}, yaxis={"visible": False},
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    pairs = edges.sort_values("weight", ascending=False).head(25).rename(
        columns={"a": "Kata 1", "b": "Kata 2", "weight": "Muncul bersama"})
    with st.expander(tr("Pasangan kata teratas"), icon=":material/table_rows:"):
        st.dataframe(pairs, hide_index=True, width="stretch")
    csv_download(pairs, f"jaringan-kata-{an.safe_name(job['project'])}", "dl-net")


RENDERERS = {"statistik": render_statistics, "frekuensi": render_frequency, "tren": render_trend,
             "topik": render_topics, "jaringan": render_network}


def page_for(manager, key):
    return partial(analysis_page, manager, key)
