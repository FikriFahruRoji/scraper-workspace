"""Rekomendasi Analisis: pick a dataset, see what kind of data it is and which analyses suit it."""
import streamlit as st

from workspace_app import families as fam
from workspace_app import ui
from workspace_app.analysis_view import load_frame
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta


def page_link(page_key):
    """The st.Page registered for an analysis page key, if the app registered it."""
    return ui.PAGES.get("sentimen" if page_key == "sentimen" else f"analisis-{page_key}")


def characteristics(info):
    parts = [f"{ui.fmt(info.rows)} baris"]
    if info.text_columns:
        parts.append(f"teks: {', '.join(info.text_columns[:3])}")
    if info.rating_column:
        parts.append(f"bintang: {info.rating_column}")
    if info.date_columns:
        parts.append(f"tanggal: {', '.join(info.date_columns[:2])}")
    if info.has_coordinates:
        parts.append("koordinat")
    if info.has_panel:
        parts.append("negara × tahun")
    if info.has_sentiment:
        parts.append("sudah bersentimen")
    return " · ".join(parts)


def recommend_page(manager):
    ui.page_header("Rekomendasi Analisis", "Analisis yang cocok dengan karakteristik dataset kamu")
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-recommend-input"):
        job_id = st.selectbox("Dataset", list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0, key="rec-dataset")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"])
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}}
        info = fam.profile(frame)
        found, how = fam.detect({**job, "params": meta["params"]}, frame, info)

    if not found:
        st.info("Jenis data ini belum dikenali dari sumber maupun kolomnya. Statistik Data tetap bisa dipakai.",
                icon=":material/help:")
        if link := page_link("statistik"):
            st.page_link(link, label="Buka Statistik Data", icon=":material/table_chart:")
        return

    st.markdown(" ".join(f":primary-badge[{key} · {fam.FAMILIES[key][0]}]" for key in found)
                + f"  \n:small[Dikenali {how}. {characteristics(info)}]")
    for key in found:
        st.caption(f"**{fam.FAMILIES[key][0]}**: {fam.FAMILIES[key][1]}")

    items = fam.recommendations(found, info)
    ready = [(family, item) for family, item in items if item.ready]
    blocked = [(family, item) for family, item in items if item.page and item.blocked]
    planned = [(family, item) for family, item in items if item.page is None]

    if ready:
        st.subheader("Bisa dijalankan sekarang", anchor=False)
        for number, (family, item) in enumerate(ready, 1):
            with st.container(border=True, key=f"rec-{family}-{item.key}"):
                left, right = st.columns([4, 1.3], vertical_alignment="center")
                left.markdown(f"**{number}. {item.title}**  \n:small[{item.why}]")
                if link := page_link(item.page):
                    right.page_link(link, label="Buka", icon=":material/arrow_forward:")
    if blocked:
        st.subheader("Belum cocok untuk dataset ini", anchor=False)
        for family, item in blocked:
            st.markdown(f"- **{item.title}**: {item.blocked}")
    if planned:
        with st.expander(f"Direncanakan ({len(planned)} analisis)", icon=":material/schedule:"):
            st.caption("Analisis yang cocok dengan jenis data ini tapi belum dibangun. Urutannya mengikuti "
                       "docs/ANALISIS.md.")
            for family, item in planned:
                st.markdown(f"- **{item.title}**: {item.why}")
