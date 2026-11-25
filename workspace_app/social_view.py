"""Analyses for social media text (family B): reply network, interaction and accounts, repeated text.

Each renderer takes (manager, job, frame) like the ones in analysis_view and is dispatched from there.
"""
import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import social as so
from workspace_app import ui
from workspace_app.analysis_view import csv_download
from workspace_app.sources import label

COMMUNITY_COLORS = ["#2B93AE", "#d0503c", "#2e9e6b", "#b7791f", "#7b61c4", "#c2477f", "#4f7d9c", "#8a8f2e"]
OTHER_COLOR = "rgba(130, 146, 162, .8)"


def origin_text(manager, job):
    """'komentar YouTube' style phrase for a method paragraph."""
    full = manager.get(job["id"]) or job
    names = [label(source) for source in fam.origin_sources({**job, "params": full.get("params") or {}})]
    return f"data {', '.join(names)}" if names else "dataset ini"


def method_block(text):
    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(text, language=None, wrap_lines=True)


def pick(frame, title, found, candidates, key, help=None):
    """Selectbox for a column, starting at the column found by name.

    The caller puts the job id in `key`, so another dataset starts again from its own column."""
    options = list(candidates)
    if not options:
        return None
    index = options.index(found) if found in options else 0
    return st.selectbox(title, options, index=index, key=key, help=help,
                        format_func=lambda column: tr("Tanpa kolom ini") if column is None else column)


def text_like_columns(frame):
    return [column for column in frame if frame[column].map(lambda value: isinstance(value, str)).mean() > 0.5]


# --------------------------------------------------------------------------- #
# Jaringan Balasan
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung jaringan…", max_entries=4)
def compute_network(job_id, count, frame, author):
    edges, info = so.reply_edges(frame, author)
    table, metrics = so.reply_network(edges) if not edges.empty else (pd.DataFrame(), {})
    return edges, info, table, metrics


def render_replies(manager, job, frame):
    kind, columns = so.reply_columns(frame)
    if kind is None:
        st.info(tr("Dataset ini tidak punya kolom balasan. Jaringan balasan butuh `parent_id` (Instagram), "
                "`id_induk` (YouTube), atau `IN_REPLY_TO` (X, Threads)."), icon=":material/hub:")
        return
    author = pick(frame, tr("Kolom akun"), so.author_column(frame), text_like_columns(frame), f"rep-author-{job['id']}",
                  help=tr("Akun penulis tiap baris. Simpul jaringan adalah akun ini."))
    if author is None:
        return
    edges, info, table, metrics = compute_network(job["id"], job["count"], frame, author)
    link = (f"`{columns[1]}` → `{columns[0]}`" if kind == "induk" else f"`{columns[0]}`")
    st.caption(tr('Balasan dibaca dari {0}. {1} baris adalah balasan; {2} induknya tidak ada di dataset dan {3} membalas diri sendiri, keduanya tidak dihitung.', link, ui.fmt(info['balasan']), ui.fmt(info['induk_hilang']), ui.fmt(info['ke_diri_sendiri'])))
    if table.empty:
        st.info(tr("Tidak ada balasan antarakun yang bisa dihubungkan."), icon=":material/hub:")
        return

    cols = st.columns(4)
    cols[0].metric(tr("Akun"), ui.fmt(metrics["akun"]), border=True)
    cols[1].metric(tr("Relasi"), ui.fmt(metrics["relasi"]), border=True)
    cols[2].metric(tr("Resiprositas"), so.decimal(metrics["resiprositas"], 2), border=True,
                   help=tr("Porsi relasi yang dibalas balik. 0 = satu arah semua, 1 = semua saling membalas."))
    cols[3].metric(tr("Komunitas"), ui.fmt(metrics["komunitas"]), border=True,
                   help=tr('Louvain, modularitas {0}. Di atas 0,3 biasanya menandakan kelompok yang cukup terpisah.', so.decimal(metrics['modularitas'], 3)))
    st.caption(tr('Komponen terhubung: {0}; komponen terbesar memuat {1}% akun. Kepadatan {2}.', ui.fmt(metrics['komponen']), so.decimal(metrics['komponen_terbesar'] * 100, 0), so.decimal(metrics['kepadatan'], 4)))

    shown = len(table)
    if len(table) > 10:
        shown = st.slider(tr("Akun yang digambar"), 10, min(200, len(table)), min(60, len(table)), key="rep-shown",
                          help=tr("Akun dengan PageRank tertinggi. Tabel di bawah memuat semua akun."))
    draw(edges, table.head(shown))

    st.subheader(tr("Akun sentral"), anchor=False)
    st.caption(tr("PageRank tinggi: sering dibalas oleh akun yang juga sering dibalas. Betweenness tinggi: "
               "menjembatani kelompok yang berbeda."))
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"PageRank": st.column_config.NumberColumn(format="%.4f"),
                                "Betweenness": st.column_config.NumberColumn(format="%.4f")})
    name = an.safe_name(job["project"])
    left, right = st.columns(2)
    with left:
        csv_download(table, f"akun-sentral-{name}", "dl-rep-nodes")
    with right:
        st.download_button(tr("Unduh daftar relasi (CSV)"), edges.to_csv(index=False).encode("utf-8-sig"),
                           f"relasi-balasan-{name}.csv", "text/csv", icon=":material/download:",
                           key="dl-rep-edges", on_click="ignore",
                           help=tr("Bisa dibuka di Gephi: kolom dari, ke, balasan."))
    method_block(so.network_method_text(metrics, info, origin_text(manager, job)))


def draw(edges, nodes):
    import plotly.graph_objects as go

    accounts = list(nodes["Akun"])
    position = so.layout(edges, accounts)
    keep = set(accounts)
    shown = edges[edges["dari"].isin(keep) & edges["ke"].isin(keep)]
    heaviest = max(shown["balasan"].max() if not shown.empty else 1, 1)
    figure = go.Figure()
    for edge in shown.itertuples(index=False):
        (x0, y0), (x1, y1) = position[edge.dari], position[edge.ke]
        figure.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines", hoverinfo="skip", showlegend=False,
                                    line={"width": 0.6 + 3 * edge.balasan / heaviest, "color": ui.CHART_LINE}))
        figure.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y", axref="x", ayref="y", showarrow=True,
                              arrowhead=2, arrowsize=1, arrowwidth=1, arrowcolor=ui.CHART_LINE, standoff=8, text="")
    colors = [COMMUNITY_COLORS[(number - 1) % len(COMMUNITY_COLORS)] if number <= len(COMMUNITY_COLORS)
              else OTHER_COLOR for number in nodes["Komunitas"]]
    largest = max(nodes["PageRank"].max(), 1e-9)
    label_top = set(nodes.head(15)["Akun"])
    figure.add_trace(go.Scatter(
        x=[position[account][0] for account in accounts], y=[position[account][1] for account in accounts],
        mode="markers+text", text=[account if account in label_top else "" for account in accounts],
        textposition="top center", textfont={"size": 12}, showlegend=False,
        customdata=list(zip(accounts, nodes["Balasan diterima"], nodes["Balasan dikirim"], nodes["Komunitas"])),
        hovertemplate="%{customdata[0]}<br>diterima %{customdata[1]} · dikirim %{customdata[2]}"
                      "<br>komunitas %{customdata[3]}<extra></extra>",
        marker={"size": 8 + 30 * nodes["PageRank"] / largest, "color": colors,
                "line": {"width": 1, "color": ui.CHART_SURFACE}}))
    figure.update_layout(height=600, margin={"l": 10, "r": 10, "t": 10, "b": 10}, hovermode="closest",
                         xaxis={"visible": False}, yaxis={"visible": False},
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption(tr("Ukuran titik mengikuti PageRank, warna mengikuti komunitas, panah dari pembalas ke yang dibalas."))


# --------------------------------------------------------------------------- #
# Interaksi & Akun
# --------------------------------------------------------------------------- #

def render_interaction(manager, job, frame):
    author_found, like_found = so.author_column(frame), so.like_column(frame)
    if author_found is None and like_found is None:
        st.info(tr("Dataset ini tidak punya kolom akun maupun jumlah suka."), icon=":material/group:")
        return
    texts = so.text_columns(frame)
    job_key = job["id"]
    left, middle, right = st.columns(3)
    with left:
        text = pick(frame, tr("Kolom teks"), texts[0] if texts else None, texts, f"int-text-{job_key}")
    with middle:
        author = pick(frame, tr("Kolom akun"), author_found, [None, *text_like_columns(frame)], f"int-author-{job_key}")
    with right:
        numbers = fam.numeric_columns(frame)
        like = pick(frame, tr("Kolom suka"), like_found, [None, *numbers], f"int-like-{job_key}")
    origin = origin_text(manager, job)
    name = an.safe_name(job["project"])
    summary, test = None, None

    if like is not None and text is not None:
        st.subheader(tr("Teks paling disukai"), anchor=False)
        top = so.top_texts(frame, text, like, 20, author)
        st.dataframe(top, hide_index=True, width="stretch",
                     column_config={"Teks": st.column_config.TextColumn(width="large")})
        csv_download(top, f"teks-teratas-{name}", "dl-int-top")

    if author is not None:
        st.subheader(tr("Akun paling aktif"), anchor=False)
        table, summary = so.active_accounts(frame, author, like)
        cols = st.columns(3)
        cols[0].metric(tr("Akun"), ui.fmt(summary["akun"]), border=True)
        cols[1].metric(tr("Teks dari 10% akun teraktif"), f"{so.decimal(summary['porsi_10_persen_teratas'], 0)}%",
                       border=True)
        cols[2].metric(tr("Gini keaktifan"), so.decimal(summary["gini"], 2), border=True,
                       help=tr("0 = setiap akun menulis sama banyak, mendekati 1 = segelintir akun menulis hampir semua."))
        st.caption(tr('{0} akun hanya menulis sekali.', ui.fmt(summary['satu_kali'])))
        st.dataframe(table.head(100), hide_index=True, width="stretch",
                     column_config={"Porsi teks (%)": st.column_config.NumberColumn(format="%.1f")})
        csv_download(table, f"akun-aktif-{name}", "dl-int-accounts")

    if like is not None and "sentimen" in frame:
        st.subheader(tr("Sentimen dan suka"), anchor=False)
        table, test = so.sentiment_by_likes(frame, like)
        if table.empty:
            st.info(tr("Kolom sentimen kosong."), icon=":material/sentiment_satisfied:")
        else:
            st.dataframe(table, hide_index=True, width="stretch",
                         column_config={"Rata-rata suka": st.column_config.NumberColumn(format="%.2f"),
                                        "Porsi suka (%)": st.column_config.NumberColumn(format="%.1f")})
            if test:
                verdict = "berbeda bermakna" if test["p"] < 0.05 else "tidak berbeda bermakna"
                st.caption(tr('Uji Kruskal–Wallis: H({0}) = {1}, p {2}, ε² = {3}. Jumlah suka antarsentimen {4} pada α = 0,05.', test['df'], so.decimal(test['h'], 2), so.p_text(test['p']), so.decimal(test['epsilon2'], 3), verdict))
            else:
                st.caption(tr("Uji beda butuh minimal dua kelompok sentimen dengan lima teks atau lebih."))
            csv_download(table, f"sentimen-suka-{name}", "dl-int-sentiment")
    elif like is not None and "sentimen" not in frame and "sentimen" in ui.pages():
        st.caption(tr("Jalankan Sentimen pada dataset ini untuk membandingkan sentimen dengan jumlah suka."))

    if summary:
        method_block(so.interaction_method_text(summary, test, origin, like))


# --------------------------------------------------------------------------- #
# Teks Berulang
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Membandingkan teks…", max_entries=4)
def compute_duplicates(job_id, count, frame, text, threshold, author):
    return so.near_duplicates(frame, text, threshold, author)


def render_duplicates(manager, job, frame):
    texts = so.text_columns(frame)
    if not texts:
        st.info(tr("Dataset ini tidak punya kolom teks."), icon=":material/content_copy:")
        return
    raw = [column for column in texts if column != an.CLEAN_COLUMN] or texts
    left, middle, right = st.columns([1.2, 1.2, 1])
    with left:
        text = pick(frame, tr("Kolom teks"), raw[0], texts, f"dup-text-{job['id']}", help=tr("Teks asli lebih tepat daripada teks_bersih."))
    with middle:
        author = pick(frame, tr("Kolom akun"), so.author_column(frame), [None, *text_like_columns(frame)],
                      f"dup-author-{job['id']}")
    with right:
        threshold = st.slider(tr("Kemiripan minimal"), 0.70, 1.0, so.DUPLICATE_THRESHOLD, 0.01, key="dup-threshold",
                              help=tr("Kemiripan kosinus TF-IDF. 1 = sama persis setelah dinormalisasi."))
    filled = frame[text].fillna("").astype(str).str.strip().ne("").sum()
    if filled < so.DUPLICATE_MIN_TEXTS:
        st.info(tr('Butuh minimal {0} teks, kolom ini berisi {1}.', so.DUPLICATE_MIN_TEXTS, ui.fmt(filled)),
                icon=":material/content_copy:")
        return
    table, membership, summary = compute_duplicates(job["id"], job["count"], frame, text, threshold, author)
    cols = st.columns(3)
    cols[0].metric(tr("Teks dibandingkan"), ui.fmt(summary["teks"]), border=True)
    cols[1].metric(tr("Grup teks berulang"), ui.fmt(summary["grup"]), border=True)
    cols[2].metric(tr("Teks dalam grup"), f"{so.decimal(summary['persen'], 1)}%", border=True)
    if summary["teks"] >= an.MAX_DOCUMENTS:
        st.caption(tr('Dibatasi {0} teks pertama.', ui.fmt(an.MAX_DOCUMENTS)))
    st.caption(tr("Teks berulang bisa datang dari templat, kutipan, salin-tempel orang biasa, atau kampanye "
               "terkoordinasi. Ini indikasi untuk dibaca, bukan bukti akun bot."))
    if table.empty:
        st.info(tr("Tidak ada teks yang semirip ini. Turunkan kemiripan minimal untuk melihat yang lebih longgar."),
                icon=":material/content_copy:")
    else:
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={"Contoh": st.column_config.TextColumn(width="large")})
        group = st.selectbox(tr("Lihat isi grup"), list(table["Grup"]), key="dup-group",
                             format_func=lambda number: f"Grup {number} · "
                                                        f"{int(table.set_index('Grup').loc[number, 'Teks'])} teks")
        members = membership[membership == group].index
        detail = frame.loc[members, [column for column in (author, text) if column is not None]]
        st.dataframe(detail, hide_index=True, width="stretch")
        name = an.safe_name(job["project"])
        rows = frame.loc[membership.index].assign(grup_teks_berulang=membership.astype(int).values)
        left, right = st.columns(2)
        with left:
            csv_download(table, f"grup-teks-berulang-{name}", "dl-dup-groups")
        with right:
            st.download_button(tr("Unduh baris bergrup (CSV)"), rows.to_csv(index=False).encode("utf-8-sig"),
                               f"baris-teks-berulang-{name}.csv", "text/csv", icon=":material/download:",
                               key="dl-dup-rows", on_click="ignore")
    method_block(so.duplicate_method_text(summary, origin_text(manager, job)))


RENDERERS = {"balasan": render_replies, "interaksi": render_interaction, "berulang": render_duplicates}
