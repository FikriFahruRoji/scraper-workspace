"""Gabungkan Data: combine several finished datasets into one new dataset."""
import pandas as pd
import streamlit as st

from workspace_app import ui
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sources import label
from workspace_app.views import project_field


def merge_page(manager):
    ui.page_header("Gabungkan Data", "Satukan beberapa dataset menjadi satu dataset baru")
    jobs = finished(manager)
    if len(jobs) < 2:
        empty_state("Butuh minimal dua dataset untuk digabungkan. Ambil data di Lakukan Scraping dulu.")
        return
    by_id = {job["id"]: job for job in jobs}

    with st.container(key="panel-merge"):
        chosen = st.multiselect("Dataset yang digabungkan", list(by_id), key="merge-jobs",
                                format_func=lambda key: option_label(by_id[key]),
                                placeholder="Pilih dua dataset atau lebih",
                                help="Boleh dari sumber berbeda. Kolom yang tidak ada di salah satu dataset "
                                     "dibiarkan kosong pada baris dataset itu.")
        selected = [by_id[key] for key in chosen]
        if len(selected) >= 2:
            show_preview(manager, selected)
        tag = st.checkbox("Tambahkan kolom sumber_data dan pekerjaan_asal", True, key="merge-tag",
                          help="Supaya setiap baris bisa ditelusuri ke dataset asalnya.")
        dedupe = st.checkbox("Buang baris yang persis sama", False, key="merge-dedupe",
                             help="Baris dengan isi kolom data yang sama disimpan sekali. Penanda gabungan diabaikan.")
        project = project_field(manager, "gabungan")
        active, _ = manager.snapshot()
        ready = len(selected) >= 2 and bool(project) and active is None
        if st.button("Gabungkan Data", type="primary", icon=":material/merge:", disabled=not ready):
            params = {"dataset": [{"id": job["id"], "proyek": job["project"], "sumber": label(job["source"]),
                                   "baris": job["count"]} for job in selected],
                      "tandai": tag, "buang_duplikat": dedupe}
            try:
                manager.start_merge(params, chosen, project=project, tag_source=tag, drop_duplicates=dedupe)
                st.session_state["merge-started"] = project
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if active is not None:
            st.caption("Tunggu pekerjaan yang sedang berjalan selesai atau hentikan dulu.")
        elif len(selected) < 2:
            st.caption("Pilih minimal dua dataset.")
        elif not project:
            st.caption("Isi nama proyek untuk mengaktifkan tombol.")

    if started := st.session_state.pop("merge-started", None):
        st.success(f"Penggabungan untuk proyek {started} berjalan. Hasilnya muncul di Dataset.",
                   icon=":material/check_circle:")
        st.page_link(ui.PAGES["dataset"], label="Buka Dataset", icon=":material/folder:")


def show_preview(manager, selected):
    """Row and column counts per dataset, and which columns they share."""
    columns = {}
    for job in selected:
        sample = manager.input_rows([job["id"]], 50)
        columns[job["id"]] = list(dict.fromkeys(key for row in sample for key in row if key != "pekerjaan_asal"))
    union = list(dict.fromkeys(name for names in columns.values() for name in names))
    shared = [name for name in union if all(name in names for names in columns.values())]
    total = sum(job["count"] for job in selected)

    cols = st.columns(3)
    cols[0].metric("Baris hasil", ui.fmt(total), border=True)
    cols[1].metric("Kolom hasil", ui.fmt(len(union) + 2), border=True,
                   help="Termasuk dua kolom penanda bila diaktifkan.")
    cols[2].metric("Kolom yang sama", ui.fmt(len(shared)), border=True)
    st.dataframe(pd.DataFrame([{
        "Dataset": job["project"], "Sumber": label(job["source"]),
        "Baris": job["count"], "Kolom": len(columns[job["id"]]),
    } for job in selected]), hide_index=True, width="stretch")
    if not shared:
        st.warning("Dataset ini tidak punya kolom yang sama. Hasil gabungan akan berisi kolom yang saling "
                   "terpisah, dan sebagian besar selnya kosong.", icon=":material/warning:")
    elif len(shared) < len(union) / 2:
        st.caption("Kolom yang sama: " + ", ".join(shared[:8]) + (" …" if len(shared) > 8 else "") + ". "
                   "Kolom lain hanya terisi untuk dataset yang memilikinya.")
