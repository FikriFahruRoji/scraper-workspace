"""Analyses for news (family C): distinctive words between portals, and sentiment per portal.

`render_distinctive` is dispatched from analysis_view like the other analysis pages; the sentiment section is
drawn from the sentiment results page when the dataset has a portal (or source) column. Topics over time are
in topics_view.
"""
import pandas as pd
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import news
from workspace_app import sentiment as sn
from workspace_app import ui
from workspace_app.analysis_view import csv_download
from workspace_app.i18n import tr
from workspace_app.sources import label


def origin_text(manager, job):
    full = manager.get(job["id"]) or job
    names = [label(source) for source in fam.origin_sources({**job, "params": full.get("params") or {}})]
    return f"data {', '.join(names)}" if names else "dataset ini"


def group_candidates(frame):
    """Columns that could name a group: few distinct text values, found one first."""
    found = news.group_column(frame)
    others = [column for column in frame if column != found and column != "pekerjaan_asal"
              and frame[column].map(lambda value: isinstance(value, str)).all()
              and 2 <= frame[column].nunique() <= 30]
    return found, ([found] if found else []) + others


# --------------------------------------------------------------------------- #
# Kata Pembeda
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung kata pembeda…", max_entries=4)
def compute_distinctive(job_id, count, frame, columns, group, top):
    documents = news.cleaned_rows(frame, list(columns))
    return news.distinctive_words(documents, frame.loc[documents.index, group].astype(str), top)


def render_distinctive(manager, job, frame):
    texts = an.text_columns(frame)
    found, groups = group_candidates(frame)
    if not texts or not groups:
        st.info(tr("Kata pembeda butuh kolom teks dan kolom kelompok (misalnya portal) dengan minimal dua nilai."),
                icon=":material/compare_arrows:")
        return
    key = job["id"]
    left, right = st.columns(2)
    full = manager.get(key) or job
    origins = fam.origin_sources({**job, "params": full.get("params") or {}}) or fam.merged_sources(frame)
    columns = left.multiselect(tr("Kolom teks"), texts, default=news.default_text_columns(texts, origins),
                               key=f"pb-columns-{key}")
    group = right.selectbox(tr("Bandingkan menurut"), groups, key=f"pb-group-{key}",
                            index=groups.index(found) if found in groups else 0)
    top = st.slider(tr("Jumlah kata per kelompok"), 10, 50, 25, key="pb-top")
    if not columns:
        return
    results, summary = compute_distinctive(job["id"], job["count"], frame, tuple(columns), group, top)
    if not results:
        st.info(tr('Butuh minimal dua kelompok dengan {0} dokumen atau lebih. Kelompok yang dilewati: {1}.',
                   news.MIN_GROUP_DOCS, ", ".join(summary["dilewati"]) or "-"), icon=":material/compare_arrows:")
        return
    cols = st.columns(3)
    cols[0].metric(tr("Kelompok"), ui.fmt(len(summary["kelompok"])), border=True)
    cols[1].metric(tr("Token"), ui.fmt(summary["token"]), border=True)
    cols[2].metric(tr("Kata berbeda"), ui.fmt(summary["kata"]), border=True)
    st.caption(tr("Skor z tinggi = kata khas kelompok itu dibanding semua kelompok lain, bukan sekadar yang paling "
                  "sering muncul. |z| ≥ 1,96 signifikan pada taraf 95%."))
    name = an.safe_name(job["project"])
    chosen = st.segmented_control(tr("Kelompok"), summary["kelompok"], default=summary["kelompok"][0], required=True,
                                  key=f"pb-view-{key}")
    table = results[chosen]
    st.bar_chart(table.head(15), x="Kata", y="Skor z", horizontal=True, sort="-Skor z")
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Signifikan": st.column_config.CheckboxColumn()})
    everything = pd.concat([frame_.assign(Kelompok=name_) for name_, frame_ in results.items()])
    left, right = st.columns(2)
    with left:
        csv_download(table, f"kata-pembeda-{an.safe_name(str(chosen))}-{name}", "dl-pb-one")
    with right:
        csv_download(everything, f"kata-pembeda-semua-{name}", "dl-pb-all")
    skipped = summary["dilewati"]
    if skipped:
        st.caption(tr('Dilewati karena kurang dari {0} dokumen: {1}.', news.MIN_GROUP_DOCS, ", ".join(skipped)))
    if len(frame) > an.MAX_DOCUMENTS:
        st.caption(tr("Dibatasi {0} baris pertama.", ui.fmt(an.MAX_DOCUMENTS)))
    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(news.distinctive_method_text(summary, origin_text(manager, job), "portal" if group == "portal" else
                                         "kelompok"), language=None, wrap_lines=True)


# --------------------------------------------------------------------------- #
# Sentimen per portal (drawn inside the sentiment results page)
# --------------------------------------------------------------------------- #

def sentiment_group_section(frame, name, origin):
    group = news.group_column(frame)
    if group is None or "sentimen" not in frame:
        return
    table, test = news.sentiment_by_group(frame, group)
    if table.empty:
        return
    from workspace_app.sentiment_view import COLORS

    st.subheader(tr("Per portal") if group == "portal" else tr("Per sumber"), anchor=False)
    import plotly.express as px

    long = table.melt(id_vars=["Kelompok", "Teks"], value_vars=list(sn.SENTIMENTS), var_name="Sentimen",
                      value_name="Persen")
    figure = px.bar(long, x="Persen", y="Kelompok", color="Sentimen", orientation="h", color_discrete_map=COLORS,
                    category_orders={"Sentimen": list(sn.SENTIMENTS), "Kelompok": list(table["Kelompok"])})
    figure.update_layout(height=60 + 34 * len(table), margin={"l": 0, "r": 0, "t": 0, "b": 0}, legend_title=None,
                         xaxis_title="% teks", yaxis_title=None, paper_bgcolor="rgba(0,0,0,0)",
                         plot_bgcolor="rgba(0,0,0,0)")
    figure.update_yaxes(autorange="reversed")
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={label_: st.column_config.ProgressColumn(label_, min_value=0, max_value=100,
                                                                         format="%.1f%%") for label_ in sn.SENTIMENTS})
    csv_download(table, f"sentimen-{group}-{name}", "dl-sn-group")
    st.code(news.group_sentiment_method_text(test, origin, "portal" if group == "portal" else "sumber"),
            language=None, wrap_lines=True)


RENDERERS = {"pembeda": render_distinctive}
