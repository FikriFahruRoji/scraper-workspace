"""Emosi & Toksisitas: run the emotion/toxicity job on a dataset, and read the results of one."""
import streamlit as st

from workspace_app import analysis as an
from workspace_app import emotion as em
from workspace_app import families as fam
from workspace_app import optional
from workspace_app import preprocess as pp
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.sources import label
from workspace_app.views import project_field

KEY = "em-"
SOURCE = "emosi"
COLORS = {"senang": "#2e9e6b", "cinta": "#c2477f", "sedih": "#4f7d9c", "marah": "#d0503c", "takut": "#7b61c4",
          "tidak jelas": "#9aa0a6"}


def emotion_page(manager):
    ui.page_header(tr("Emosi & Toksisitas"),
                   tr("Lima emosi dan deteksi ujaran kebencian serta hinaan dari model bahasa Indonesia"))
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    results = [job for job in jobs if job["source"] == SOURCE]
    inputs = [job for job in jobs if job["source"] not in (SOURCE,)]
    tabs = ["Hasil emosi dan toksisitas", "Jalankan analisis"]
    if not results:
        tabs.reverse()
    chosen = st.segmented_control(tr("Bagian"), tabs, format_func=tr, default=tabs[0], key=KEY + "part",
                                  label_visibility="collapsed")
    if chosen == "Jalankan analisis":
        run_section(manager, inputs)
    else:
        results_section(manager, results)


# --------------------------------------------------------------------------- #
# Jalankan
# --------------------------------------------------------------------------- #

def run_section(manager, jobs):
    if not jobs:
        empty_state()
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    with st.container(key="panel-em-input"):
        job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0,
                              key=KEY + "dataset")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"])
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}, "source": job["source"]}
        info = fam.profile(frame)
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        if not info.text_columns:
            st.info(tr("Dataset ini tidak punya kolom teks."), icon=":material/text_fields:")
            return
        defaults = [column for source in origins for column in pp.TEXT_COLUMNS.get(source, [])
                    if column in info.text_columns]
        defaults = list(dict.fromkeys(defaults)) or [column for column in an.default_text_columns(info.text_columns)
                                                     if column != an.CLEAN_COLUMN][:1] or info.text_columns[:1]
        columns = st.multiselect(tr("Kolom teks"), info.text_columns, default=defaults, key=f"{KEY}columns-{job_id}",
                                 help=tr("Model membaca teks asli yang dinormalisasi ringan (kata gaul, emoji), "
                                         "bukan teks_bersih: stemming dan penghapusan stopword merusak konteks."))
        keys = [key for key in em.SPECS
                if st.checkbox(tr(em.SPECS[key].title), True, key=f"{KEY}task-{key}",
                               help=tr("{0} · lisensi {1} · {2}", em.SPECS[key].model_id, em.SPECS[key].license,
                                       tr(em.SPECS[key].metric)))]
        if any(key in em.TOXIC_KEYS for key in keys):
            st.caption(tr("Model toksisitas berlisensi CC BY-SA 4.0: sebut IndoToxic2024 (Susanto dkk., 2024) "
                          "bila hasilnya dipublikasikan. Label toksisitas subjektif; baca ulang teks yang ditandai."))

    with st.container(key="panel-em-run"):
        st.header(tr("Jalankan"), anchor=False)
        missing = optional.missing("transformer")
        if missing:
            st.warning(tr('Paket model belum terpasang ({0}). Jalankan di folder aplikasi:', ', '.join(missing)),
                       icon=":material/download:")
            st.code(optional.INSTALL_COMMAND, language="bash")
            return
        size = em.download_size(manager.directory, keys)
        download = False
        if size:
            download = st.checkbox(
                tr('Unduh model yang belum ada (sekitar {0} MB) ke folder data aplikasi', ui.fmt(size)),
                key=KEY + "download", help=tr("Sekali saja. Setelah itu model dipakai dari komputermu tanpa internet."))
        elif keys:
            st.caption(tr("Semua model yang dipilih sudah ada di komputer ini."))
        project = project_field(manager, SOURCE)
        st.caption(tr('{0} baris akan dinilai di latar belakang, satu antrean dengan scraping. Setiap model '
                      'menambah waktu proses.', ui.fmt(job['count'])))
        active, _ = manager.snapshot()
        ready = bool(columns) and bool(keys) and bool(project) and active is None and (not size or download)
        if st.button(tr("Jalankan analisis emosi dan toksisitas"), type="primary", icon=":material/play_arrow:",
                     disabled=not ready):
            params = {"masukan": {"pekerjaan": job_id, "proyek": job["project"], "sumber": job["source"]},
                      "sumber_asal": origins, "kolom": columns, "analisis": keys, "unduh_model": download}
            try:
                manager.start_emotion(params, manager.input_rows([job_id]), columns, keys, project=project)
                st.switch_page(ui.pages()["berjalan"])
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if active is not None:
            st.caption(tr("Tunggu pekerjaan yang sedang berjalan selesai atau hentikan dulu."))
        elif not columns:
            st.caption(tr("Pilih minimal satu kolom teks."))
        elif not keys:
            st.caption(tr("Pilih minimal satu analisis."))
        elif not project:
            st.caption(tr("Isi nama proyek untuk mengaktifkan tombol."))
        elif size and not download:
            st.caption(tr("Setujui unduhan model untuk mengaktifkan tombol."))


# --------------------------------------------------------------------------- #
# Hasil
# --------------------------------------------------------------------------- #

def bar(frame, x, y):
    import plotly.express as px

    figure = px.bar(frame, x=x, y=y, color=x, color_discrete_map=COLORS, text=y)
    figure.update_layout(showlegend=False, height=280, margin={"l": 0, "r": 0, "t": 0, "b": 0},
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_title=None)
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def results_section(manager, jobs):
    if not jobs:
        st.info(tr("Belum ada hasil emosi dan toksisitas. Jalankan analisis dulu."), icon=":material/mood:")
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    job_id = st.selectbox(tr("Hasil emosi dan toksisitas"), list(by_id),
                          format_func=lambda value: option_label(by_id[value]),
                          index=list(by_id).index(remembered) if remembered in by_id else 0, key=KEY + "result")
    st.session_state["an-last"] = job_id
    job = by_id[job_id]
    frame = load_frame(manager, job_id, job["count"])
    meta = load_meta(manager, job_id, job["count"])
    metadata, params = meta["metadata"] or {}, meta["params"] or {}
    keys = [key for key in metadata.get("analisis") or [] if key in frame]
    if not keys:
        st.warning(tr("Dataset ini belum berisi kolom hasil (pekerjaan mungkin dihentikan sebelum baris pertama)."))
        return
    if metadata.get("dihentikan"):
        st.warning(tr("Pekerjaan dihentikan sebelum selesai: hanya sebagian baris yang dinilai."),
                   icon=":material/pause_circle:")
    name = an.safe_name(job["project"])
    text_columns = [column for column in metadata.get("kolom") or [] if column in frame]
    models = metadata.get("model") or {}
    st.caption(" · ".join(f"{models[key]['id']} ({models[key]['lisensi']})" for key in keys if key in models)
               + f" · {tr('perangkat')} {metadata.get('perangkat') or '-'} · {metadata.get('detik', '-')} {tr('detik')}")

    if "emosi" in keys:
        emotion_section(frame, name)
    toxic = [key for key in keys if key in em.TOXIC_KEYS]
    if toxic:
        toxic_section(frame, toxic, text_columns, name)
    if dates := an.date_columns(frame):
        time_section(frame, keys, dates, name)

    st.subheader(tr("Paragraf metode"), anchor=False)
    origin = ", ".join(label(source) for source in params.get("sumber_asal") or []) or "data"
    st.code(em.method_text(metadata, origin), language=None, wrap_lines=True)


def emotion_section(frame, name):
    st.subheader(tr("Emosi"), anchor=False)
    threshold = st.slider(tr("Probabilitas minimal"), 0.2, 0.9, em.CONFIDENT, 0.05, key=KEY + "threshold",
                          help=tr("Model tidak punya kelas netral, jadi setiap teks dipaksa ke salah satu emosi. "
                                  "Teks di bawah ambang ini dihitung sebagai tidak jelas."))
    table = em.emotion_table(frame, threshold)
    if table.empty:
        st.info(tr("Kolom emosi kosong."), icon=":material/mood:")
        return
    cols = st.columns(3)
    cols[0].metric(tr("Teks dinilai"), ui.fmt(int(table["Jumlah"].sum())), border=True)
    top = table[table["Emosi"] != "tidak jelas"].sort_values("Jumlah", ascending=False).iloc[0]
    cols[1].metric(tr("Emosi terbanyak"), tr(top["Emosi"]), f"{top['Persen']:.1f}%".replace(".", ","),
                   delta_color="off", border=True)
    unclear = table.set_index("Emosi").loc["tidak jelas", "Persen"]
    cols[2].metric(tr("Tidak jelas"), f"{unclear:.1f}%".replace(".", ","), border=True)
    bar(table, "Emosi", "Jumlah")
    csv_download(table, f"emosi-{name}", "dl-em-dist")


def toxic_section(frame, keys, text_columns, name):
    st.subheader(tr("Toksisitas"), anchor=False)
    table = em.toxic_table(frame)
    cols = st.columns(len(table) or 1)
    for column, row in zip(cols, table.to_dict("records")):
        column.metric(tr(row["Deteksi"]), f"{row['Persen']:.1f}%".replace(".", ","),
                      tr('{0} dari {1} teks', ui.fmt(row["Terdeteksi"]), ui.fmt(row["Teks dinilai"])),
                      delta_color="off", border=True)
    st.caption(tr("Ditandai bila probabilitas ≥ 0,5. Ini indikasi otomatis, bukan penilaian: baca ulang teksnya."))
    chosen = st.segmented_control(tr("Lihat teks yang ditandai"), keys, default=keys[0], required=True,
                                  format_func=lambda key: tr(em.SPECS[key].title), key=KEY + "flagged")
    flagged = em.flagged_rows(frame, chosen, text_columns)
    if flagged.empty:
        st.info(tr("Tidak ada teks yang ditandai."), icon=":material/check_circle:")
    else:
        st.dataframe(flagged, hide_index=True, width="stretch",
                     column_config={"Teks": st.column_config.TextColumn(width="large")})
        csv_download(flagged, f"{chosen}-{name}", f"dl-em-{chosen}")
    csv_download(table, f"toksisitas-{name}", "dl-em-toxic")


def time_section(frame, keys, dates, name):
    st.subheader(tr("Dari waktu ke waktu"), anchor=False)
    left, right = st.columns(2)
    date = left.selectbox(tr("Kolom tanggal"), dates, key=KEY + "date")
    period = right.segmented_control(tr("Periode"), list(an.PERIODS), default="ME", key=KEY + "period",
                                     format_func=lambda value: tr(an.PERIODS[value]), required=True)
    column = keys[0] if len(keys) == 1 else st.segmented_control(
        tr("Tampilkan"), keys, default=keys[0], required=True, key=KEY + "time-key",
        format_func=lambda key: tr(em.SPECS[key].title))
    labels = list(em.EMOTIONS) if column == "emosi" else ["ya"]
    table = em.over_time(frame, column, date, period, None)
    present = [name_ for name_ in labels if name_ in table]
    if table.empty or not present:
        st.info(tr("Tidak ada tanggal yang bisa dibaca di kolom ini."), icon=":material/event:")
        return
    st.line_chart(table[present], y_label=tr("% teks"))
    csv_download(table.reset_index(), f"{column}-per-waktu-{name}", "dl-em-time")


def emotion_details(job):
    """Shown in the dataset dialog of an emotion job."""
    params, metadata = job["params"], job["metadata"]
    origin = params.get("masukan") or {}
    st.caption(tr('Masukan: {0} · proyek {1} · kolom {2}', label(origin.get('sumber')), origin.get('proyek', '-'),
                  ', '.join(params.get('kolom', []))))
    if metadata.get("model"):
        st.code(em.method_text(metadata, "teks"), language=None, wrap_lines=True)
