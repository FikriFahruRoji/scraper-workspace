"""Topik dari Waktu ke Waktu: NMF on the page (light tier), BERTopic as a queued job (model tier)."""
import streamlit as st

from workspace_app import analysis as an
from workspace_app import bertopic_run as bt
from workspace_app import families as fam
from workspace_app import news
from workspace_app import optional
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.sources import label
from workspace_app.views import project_field

KEY = "tp-"
SOURCE = "bertopic"
PARTS = ["Ringan (NMF)", "BERTopic: hasil", "BERTopic: jalankan"]


def topics_page(manager):
    ui.page_header(tr("Topik dari Waktu ke Waktu"),
                   tr("Tema yang dibicarakan dan bagaimana porsinya berubah dari waktu ke waktu"))
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    results = [job for job in jobs if job["source"] == SOURCE]
    inputs = [job for job in jobs if job["source"] != SOURCE]
    chosen = st.segmented_control(tr("Bagian"), PARTS, format_func=tr, default=PARTS[0], key=KEY + "part",
                                  label_visibility="collapsed")
    if chosen == PARTS[1]:
        results_section(manager, results)
    elif chosen == PARTS[2]:
        run_section(manager, inputs)
    else:
        nmf_section(manager, inputs)


def pick_dataset(jobs, key, manager):
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                          index=list(by_id).index(remembered) if remembered in by_id else 0, key=key)
    st.session_state["an-last"] = job_id
    job = by_id[job_id]
    return job, load_frame(manager, job_id, job["count"])


def origin_text(params, frame=None):
    names = [label(source) for source in params.get("sumber_asal") or []]
    return f"data {', '.join(names)}" if names else "dataset ini"


# --------------------------------------------------------------------------- #
# NMF (light tier)
# --------------------------------------------------------------------------- #

@st.cache_data(show_spinner="Menghitung topik…", max_entries=4)
def compute_nmf(job_id, count, frame, columns, date_column, topics, period):
    return news.topics_over_time(frame, list(columns), date_column, topics, period)


def nmf_section(manager, jobs):
    with st.container(key="panel-tp-nmf"):
        job, frame = pick_dataset(jobs, KEY + "nmf-dataset", manager)
        texts = an.text_columns(frame)
        dates = an.date_columns(frame)
        if not texts:
            st.info(tr("Dataset ini tidak punya kolom teks."), icon=":material/text_fields:")
            return
        key = job["id"]
        meta = load_meta(manager, job["id"], job["count"]) or {"params": {}, "source": job["source"]}
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        left, middle, right = st.columns([2, 1.5, 1])
        columns = left.multiselect(tr("Kolom teks"), texts, default=news.default_text_columns(texts, origins),
                                   key=f"{KEY}nmf-columns-{key}")
        date = middle.selectbox(tr("Kolom tanggal"), [None, *dates], key=f"{KEY}nmf-date-{key}",
                                index=1 if dates else 0,
                                format_func=lambda value: tr("Tanpa tanggal") if value is None else value)
        count = right.slider(tr("Jumlah topik"), 2, 12, 5, key=KEY + "nmf-count")
        period = st.segmented_control(tr("Periode"), list(an.PERIODS), default="ME", required=True,
                                      format_func=lambda value: tr(an.PERIODS[value]), key=KEY + "nmf-period")
        if not columns:
            return
        try:
            if date is None:
                documents = news.cleaned_rows(frame, columns)
                documents = documents[documents.ne("")]
                table, _ = an.topics(list(documents), count)
                share, coherence = None, None
                words = [[w.strip() for w in str(text).split(",") if w.strip()] for text in table["Kata utama"]]
                keep = [n for n, name in enumerate(table["Topik"]) if name != "Tanpa topik"]
                scores, mean = news.npmi_coherence([words[n] for n in keep], list(documents))
                table["Koherensi NPMI"] = None
                for n, score in zip(keep, scores):
                    table.loc[table.index[n], "Koherensi NPMI"] = round(score, 3) if score is not None else None
                coherence = {"rata_rata": mean, "dokumen": len(documents)}
            else:
                table, share, coherence, _ = compute_nmf(job["id"], job["count"], frame, tuple(columns), date,
                                                         count, period)
        except ValueError as exc:
            st.warning(str(exc), icon=":material/warning:")
            return
    cols = st.columns(3)
    cols[0].metric(tr("Dokumen"), ui.fmt(coherence["dokumen"]), border=True)
    cols[1].metric(tr("Topik"), ui.fmt(len(table[table["Topik"] != "Tanpa topik"])), border=True)
    if coherence["rata_rata"] is not None:
        cols[2].metric(tr("Koherensi rata-rata (NPMI)"), f"{coherence['rata_rata']:.3f}".replace(".", ","),
                       border=True, help=tr("Antara -1 dan 1. Makin tinggi, kata-kata teratas tiap topik makin "
                                            "sering muncul bersama dalam satu dokumen."))
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Koherensi NPMI": st.column_config.NumberColumn(format="%.3f")})
    name = an.safe_name(job["project"])
    csv_download(table, f"topik-nmf-{name}", "dl-tp-nmf")
    if share is not None and not share.empty:
        st.subheader(tr("Porsi topik per periode"), anchor=False)
        (st.area_chart if len(share) > 1 else st.bar_chart)(share, y_label=tr("% dokumen"))
        csv_download(share.reset_index(), f"topik-nmf-waktu-{name}", "dl-tp-nmf-time")
    elif date is not None:
        st.info(tr("Tidak ada tanggal yang bisa dibaca di kolom ini."), icon=":material/event:")
    st.caption(tr("Tingkat ringan: TF-IDF dan NMF membaca teks bersih. Untuk topik yang menangkap makna kalimat, "
                  "jalankan BERTopic di bagian sebelah."))
    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(news.nmf_method_text(coherence, count, list(columns), origin_text(meta["params"]),
                                 date), language=None, wrap_lines=True)


# --------------------------------------------------------------------------- #
# BERTopic: run
# --------------------------------------------------------------------------- #

def run_section(manager, jobs):
    with st.container(key="panel-tp-input"):
        job, frame = pick_dataset(jobs, KEY + "dataset", manager)
        meta = load_meta(manager, job["id"], job["count"]) or {"params": {}, "source": job["source"]}
        info = fam.profile(frame)
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        if not info.text_columns:
            st.info(tr("Dataset ini tidak punya kolom teks."), icon=":material/text_fields:")
            return
        columns = st.multiselect(tr("Kolom teks"), info.text_columns, default=news.default_text_columns(info.text_columns, origins),
                                 key=f"{KEY}columns-{job['id']}",
                                 help=tr("Model membaca teks asli (judul dan isi), bukan teks_bersih. Kata wakil tiap "
                                         "topik diambil dari teks yang dibersihkan tanpa stemming."))
        size = st.slider(tr("Ukuran topik minimal"), 5, 100, bt.DEFAULTS["min_topic_size"], key=KEY + "size",
                         help=tr("Jumlah teks terkecil yang boleh membentuk satu topik. Naikkan bila topiknya "
                                 "terlalu banyak dan sempit; turunkan bila terlalu banyak teks tanpa topik."))

    with st.container(key="panel-tp-run"):
        st.header(tr("Jalankan"), anchor=False)
        missing = optional.missing("topic")
        if missing:
            st.warning(tr('Paket model belum terpasang ({0}). Jalankan di folder aplikasi:', ', '.join(missing)),
                       icon=":material/download:")
            st.code(optional.INSTALL_COMMAND, language="bash")
            return
        cached = bt.model_path(manager.directory) is not None
        download = False
        if not cached:
            download = st.checkbox(
                tr('Unduh model {0} (sekitar {1} MB, lisensi {2}) ke folder data aplikasi', bt.MODEL_ID,
                   bt.MODEL_SIZE_MB, bt.MODEL_LICENSE), key=KEY + "download",
                help=tr("Sekali saja. Setelah itu model dipakai dari komputermu tanpa internet."))
        else:
            st.caption(tr('Model {0} (lisensi {1}) sudah ada di komputer ini.', bt.MODEL_ID, bt.MODEL_LICENSE))
        project = project_field(manager, SOURCE)
        st.caption(tr('{0} baris akan dianalisis di latar belakang, satu antrean dengan scraping. Ribuan artikel '
                      'memakan beberapa menit.', ui.fmt(job['count'])))
        if job["count"] < bt.MIN_DOCUMENTS:
            st.caption(tr('BERTopic butuh minimal {0} teks.', bt.MIN_DOCUMENTS))
        active, _ = manager.snapshot()
        ready = (bool(columns) and bool(project) and active is None and (cached or download)
                 and job["count"] >= bt.MIN_DOCUMENTS)
        if st.button(tr("Jalankan BERTopic"), type="primary", icon=":material/play_arrow:", disabled=not ready):
            options = {**bt.DEFAULTS, "min_topic_size": size}
            params = {"masukan": {"pekerjaan": job["id"], "proyek": job["project"], "sumber": job["source"]},
                      "sumber_asal": origins, "kolom": columns, "parameter": options, "unduh_model": download}
            try:
                manager.start_bertopic(params, manager.input_rows([job["id"]]), columns, options, project=project)
                st.switch_page(ui.pages()["berjalan"])
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if active is not None:
            st.caption(tr("Tunggu pekerjaan yang sedang berjalan selesai atau hentikan dulu."))
        elif not columns:
            st.caption(tr("Pilih minimal satu kolom teks."))
        elif not project:
            st.caption(tr("Isi nama proyek untuk mengaktifkan tombol."))
        elif not (cached or download):
            st.caption(tr("Setujui unduhan model untuk mengaktifkan tombol."))


# --------------------------------------------------------------------------- #
# BERTopic: results
# --------------------------------------------------------------------------- #

def results_section(manager, jobs):
    if not jobs:
        st.info(tr("Belum ada hasil BERTopic. Jalankan analisis dulu."), icon=":material/timeline:")
        return
    job, frame = pick_dataset(jobs, KEY + "result", manager)
    meta = load_meta(manager, job["id"], job["count"])
    metadata, params = meta["metadata"] or {}, meta["params"] or {}
    if "topik_id" not in frame or not metadata.get("topik"):
        st.warning(tr("Dataset ini belum berisi topik (pekerjaan mungkin dihentikan sebelum selesai)."))
        return
    name = an.safe_name(job["project"])
    table = bt.topic_table(metadata)
    cols = st.columns(4)
    cols[0].metric(tr("Teks dianalisis"), ui.fmt(metadata.get("teks", 0)), border=True)
    cols[1].metric(tr("Topik"), ui.fmt(len(table)), border=True)
    share = metadata.get("tanpa_topik", 0) / max(metadata.get("teks", 1), 1) * 100
    cols[2].metric(tr("Tanpa topik"), f"{share:.1f}%".replace(".", ","), border=True,
                   help=tr("Teks yang tidak cukup dekat dengan kelompok mana pun (outlier HDBSCAN)."))
    if metadata.get("koherensi_rata_rata") is not None:
        cols[3].metric(tr("Koherensi rata-rata (NPMI)"), f"{metadata['koherensi_rata_rata']:.3f}".replace(".", ","),
                       border=True)
    st.caption(tr('Model {0} · lisensi {1} · perangkat {2} · {3} detik', metadata.get("model"),
                  metadata.get("lisensi_model"), metadata.get("perangkat") or "-", metadata.get("detik", "-")))
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Koherensi NPMI": st.column_config.NumberColumn(format="%.3f")})
    csv_download(table, f"topik-bertopic-{name}", "dl-tp-table")

    text_columns = [column for column in metadata.get("kolom") or [] if column in frame]
    chosen = st.selectbox(tr("Lihat teks wakil topik"), [item["id"] for item in metadata["topik"]],
                          format_func=lambda number: next(item["nama"] for item in metadata["topik"]
                                                          if item["id"] == number), key=f"{KEY}topic-{job['id']}")
    st.dataframe(bt.documents_of(frame, chosen, text_columns), hide_index=True, width="stretch",
                 column_config={"Teks": st.column_config.TextColumn(width="large")})

    if dates := an.date_columns(frame):
        st.subheader(tr("Topik dari waktu ke waktu"), anchor=False)
        left, right = st.columns(2)
        date = left.selectbox(tr("Kolom tanggal"), dates, key=KEY + "date")
        period = right.segmented_control(tr("Periode"), list(an.PERIODS), default="ME", required=True,
                                         format_func=lambda value: tr(an.PERIODS[value]), key=KEY + "period")
        top = st.slider(tr("Jumlah topik ditampilkan"), 2, max(2, min(15, len(table))), min(8, max(2, len(table))),
                        key=KEY + "top") if len(table) > 2 else 2
        over = bt.over_time(frame, date, period, top)
        if over.empty:
            st.info(tr("Tidak ada tanggal yang bisa dibaca di kolom ini."), icon=":material/event:")
        else:
            st.caption(tr("Jumlah teks per topik per periode."))
            (st.line_chart if len(over) > 1 else st.bar_chart)(over)
            csv_download(over.reset_index(), f"topik-waktu-{name}", "dl-tp-time")

    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(bt.method_text(metadata, origin_text(params)), language=None, wrap_lines=True)


def topic_details(job):
    """Shown in the dataset dialog of a BERTopic job."""
    params, metadata = job["params"], job["metadata"]
    origin = params.get("masukan") or {}
    st.caption(tr('Masukan: {0} · proyek {1} · kolom {2}', label(origin.get("sumber")), origin.get("proyek", "-"),
                  ", ".join(params.get("kolom", []))))
    if metadata.get("topik"):
        st.code(bt.method_text(metadata, "teks"), language=None, wrap_lines=True)
