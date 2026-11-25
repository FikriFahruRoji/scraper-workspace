"""Entitas Bernama: run the NER job on a dataset, and read the results of one."""
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import ner
from workspace_app import news
from workspace_app import optional
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.i18n import tr
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sentiment_view import load_meta
from workspace_app.sources import label
from workspace_app.views import project_field

KEY = "ne-"
SOURCE = "ner"
PARTS = ["Hasil entitas", "Jalankan analisis"]


def ner_page(manager):
    ui.page_header(tr("Entitas Bernama"),
                   tr("Orang, organisasi, partai, dan tempat yang disebut dalam teks, dikenali model bahasa Indonesia"))
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    results = [job for job in jobs if job["source"] == SOURCE]
    inputs = [job for job in jobs if job["source"] != SOURCE]
    parts = PARTS if results else PARTS[::-1]
    chosen = st.segmented_control(tr("Bagian"), parts, format_func=tr, default=parts[0], key=KEY + "part",
                                  label_visibility="collapsed")
    if chosen == "Jalankan analisis":
        run_section(manager, inputs)
    else:
        results_section(manager, results)


def pick_dataset(jobs, key, manager):
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    job_id = st.selectbox(tr("Dataset"), list(by_id), format_func=lambda value: option_label(by_id[value]),
                          index=list(by_id).index(remembered) if remembered in by_id else 0, key=key)
    st.session_state["an-last"] = job_id
    job = by_id[job_id]
    return job, load_frame(manager, job_id, job["count"])


def origin_text(params):
    names = [label(source) for source in params.get("sumber_asal") or []]
    return f"data {', '.join(names)}" if names else "dataset ini"


# --------------------------------------------------------------------------- #
# Jalankan
# --------------------------------------------------------------------------- #

def run_section(manager, jobs):
    if not jobs:
        empty_state()
        return
    with st.container(key="panel-ne-input"):
        job, frame = pick_dataset(jobs, KEY + "dataset", manager)
        meta = load_meta(manager, job["id"], job["count"]) or {"params": {}, "source": job["source"]}
        info = fam.profile(frame)
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        if not info.text_columns:
            st.info(tr("Dataset ini tidak punya kolom teks."), icon=":material/text_fields:")
            return
        texts = [column for column in info.text_columns if column != an.CLEAN_COLUMN]
        columns = st.multiselect(tr("Kolom teks"), texts, default=[column for column in news.default_text_columns(
                                     texts, origins) if column in texts], key=f"{KEY}columns-{job['id']}",
                                 help=tr("Model membaca teks asli apa adanya, bukan teks_bersih: huruf kapital adalah "
                                         "petunjuk terkuat untuk sebuah nama."))
        st.caption(tr("Model {0} (lisensi {1}) dilatih pada korpus NERGrit yang tidak mencantumkan lisensi yang "
                      "jelas. Sebut NERGrit dan model ini bila hasilnya dipublikasikan.", ner.MODEL_ID,
                      ner.MODEL_LICENSE))

    with st.container(key="panel-ne-run"):
        st.header(tr("Jalankan"), anchor=False)
        missing = optional.missing("transformer")
        if missing:
            st.warning(tr('Paket model belum terpasang ({0}). Jalankan di folder aplikasi:', ', '.join(missing)),
                       icon=":material/download:")
            st.code(optional.INSTALL_COMMAND, language="bash")
            return
        cached = ner.model_path(manager.directory) is not None
        download = False
        if not cached:
            download = st.checkbox(
                tr('Unduh model {0} (sekitar {1} MB, lisensi {2}) ke folder data aplikasi', ner.MODEL_ID,
                   ner.MODEL_SIZE_MB, ner.MODEL_LICENSE), key=KEY + "download",
                help=tr("Sekali saja. Setelah itu model dipakai dari komputermu tanpa internet."))
        else:
            st.caption(tr('Model {0} (lisensi {1}) sudah ada di komputer ini.', ner.MODEL_ID, ner.MODEL_LICENSE))
        project = project_field(manager, SOURCE)
        st.caption(tr('{0} baris akan diproses di latar belakang, satu antrean dengan scraping. Artikel panjang '
                      'memakan waktu lebih lama.', ui.fmt(job['count'])))
        active, _ = manager.snapshot()
        ready = bool(columns) and bool(project) and active is None and (cached or download)
        if st.button(tr("Jalankan pengenalan entitas"), type="primary", icon=":material/play_arrow:",
                     disabled=not ready):
            params = {"masukan": {"pekerjaan": job["id"], "proyek": job["project"], "sumber": job["source"]},
                      "sumber_asal": origins, "kolom": columns, "unduh_model": download}
            try:
                manager.start_ner(params, manager.input_rows([job["id"]]), columns, project=project)
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
# Hasil
# --------------------------------------------------------------------------- #

def type_filter(key):
    return st.multiselect(tr("Jenis entitas"), list(ner.TYPES), default=["PER", "ORG", "NOR", "GPE"],
                          format_func=lambda kind: tr(ner.TYPES[kind]), key=key)


def results_section(manager, jobs):
    if not jobs:
        st.info(tr("Belum ada hasil entitas bernama. Jalankan analisis dulu."), icon=":material/person_search:")
        return
    job, frame = pick_dataset(jobs, KEY + "result", manager)
    meta = load_meta(manager, job["id"], job["count"])
    metadata, params = meta["metadata"] or {}, meta["params"] or {}
    if ner.COLUMN not in frame or not ner.tagged_documents(frame):
        st.warning(tr("Dataset ini belum berisi kolom hasil (pekerjaan mungkin dihentikan sebelum baris pertama)."))
        return
    if metadata.get("dihentikan"):
        st.warning(tr("Pekerjaan dihentikan sebelum selesai: hanya sebagian baris yang diproses."),
                   icon=":material/pause_circle:")
    name = an.safe_name(job["project"])
    text_columns = [column for column in metadata.get("kolom") or [] if column in frame]
    types = ner.type_table(frame)
    cols = st.columns(3)
    cols[0].metric(tr("Teks diproses"), ui.fmt(ner.tagged_documents(frame)), border=True)
    cols[1].metric(tr("Sebutan entitas"), ui.fmt(int(types["Sebutan"].sum()) if not types.empty else 0), border=True)
    cols[2].metric(tr("Entitas berbeda"), ui.fmt(int(types["Entitas"].sum()) if not types.empty else 0), border=True)
    st.caption(tr('Model {0} · lisensi {1}, {2} · perangkat {3} · {4} detik', metadata.get("model", ner.MODEL_ID),
                  metadata.get("lisensi_model", ner.MODEL_LICENSE), tr(ner.DATA_NOTE),
                  metadata.get("perangkat") or "-", metadata.get("detik", "-")))
    if types.empty:
        st.info(tr("Tidak ada entitas yang dikenali."), icon=":material/person_search:")
        return

    st.subheader(tr("Entitas teratas"), anchor=False)
    kinds = type_filter(KEY + "kinds")
    table = ner.entity_table(frame, kinds or None, top=100)
    if table.empty:
        st.info(tr("Tidak ada entitas untuk jenis yang dipilih."), icon=":material/filter_alt:")
    else:
        top = table.head(20).iloc[::-1].copy()
        top["Label"] = [name if len(name) <= 30 else name[:29] + "…" for name in top["Entitas"]]
        top["Label"] = top["Label"].where(~top["Label"].duplicated(keep=False), top["Entitas"])
        top["Jenis"] = top["Jenis"].map(tr)
        import plotly.express as px

        figure = px.bar(top, x="Dokumen", y="Label", orientation="h", color="Jenis", text="Dokumen",
                        hover_name="Entitas", hover_data={"Label": False})
        left = 8 + 7 * max(len(name) for name in top["Label"])  # automargin misjudges the font width
        figure.update_layout(height=max(320, 22 * len(top) + 60), margin={"l": left, "r": 0, "t": 0, "b": 0},
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", yaxis_title=None,
                             legend={"title": None, "orientation": "h", "y": -0.12, "x": 0})
        # Colouring by type would group the bars by type; keep them ranked by documents.
        figure.update_yaxes(categoryorder="array", categoryarray=list(top["Label"]), automargin=True)
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
        shown = table.drop(columns=["kunci"])
        shown["Jenis"] = shown["Jenis"].map(tr)
        st.dataframe(shown, hide_index=True, width="stretch")
        csv_download(shown, f"entitas-{name}", "dl-ne-top")
        chosen = st.selectbox(tr("Lihat teks yang menyebut"), list(table["kunci"]), key=f"{KEY}doc-{job['id']}",
                              format_func=lambda key: table.set_index("kunci").loc[key, "Entitas"])
        st.dataframe(ner.documents_with(frame, chosen, text_columns), hide_index=True, width="stretch",
                     column_config={"Teks": st.column_config.TextColumn(width="large")})
    with st.expander(tr("Sebaran per jenis"), icon=":material/category:"):
        shown = types.assign(Jenis=types["Jenis"].map(tr))
        st.dataframe(shown, hide_index=True, width="stretch")
        csv_download(shown, f"entitas-jenis-{name}", "dl-ne-types")

    if group := news.group_column(frame):
        group_section(frame, group, kinds, name)
    if dates := an.date_columns(frame):
        time_section(frame, dates, table, name)
    pair_section(frame, kinds, name)

    st.subheader(tr("Paragraf metode"), anchor=False)
    st.code(ner.method_text(metadata, origin_text(params)), language=None, wrap_lines=True)


def group_section(frame, group, kinds, name):
    st.subheader(tr("Entitas teratas per portal"), anchor=False)
    top = st.slider(tr("Entitas per portal"), 3, 20, 5, key=KEY + "group-top")
    table = ner.by_group(frame, group, kinds or None, top=top)
    if table.empty:
        st.info(tr("Tidak ada entitas untuk jenis yang dipilih."), icon=":material/filter_alt:")
        return
    st.caption(tr("Persen dihitung dari jumlah teks tiap portal, jadi portal besar dan kecil bisa dibandingkan."))
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Persen dokumen": st.column_config.ProgressColumn(format="%.1f%%", min_value=0,
                                                                                   max_value=100)})
    csv_download(table, f"entitas-per-portal-{name}", "dl-ne-group")


def time_section(frame, dates, table, name):
    st.subheader(tr("Dari waktu ke waktu"), anchor=False)
    if table.empty:
        return
    left, right = st.columns(2)
    date = left.selectbox(tr("Kolom tanggal"), dates, key=KEY + "date")
    period = right.segmented_control(tr("Periode"), list(an.PERIODS), default="ME", required=True,
                                     format_func=lambda value: tr(an.PERIODS[value]), key=KEY + "period")
    names = table.set_index("kunci")["Entitas"]
    keys = st.multiselect(tr("Entitas"), list(names.index), default=list(names.index[:5]), key=KEY + "time-keys",
                          format_func=lambda key: names[key], max_selections=10)
    over = ner.over_time(frame, date, keys, period)
    if over.empty:
        st.info(tr("Tidak ada tanggal yang bisa dibaca di kolom ini."), icon=":material/event:")
        return
    st.caption(tr("Jumlah teks yang menyebut tiap entitas per periode."))
    (st.line_chart if len(over) > 1 else st.bar_chart)(over)
    csv_download(over.reset_index(), f"entitas-per-waktu-{name}", "dl-ne-time")


def pair_section(frame, kinds, name):
    st.subheader(tr("Disebut bersama"), anchor=False)
    minimum = st.slider(tr("Minimal teks bersama"), 2, 20, 2, key=KEY + "pair-min",
                        help=tr("Pasangan yang muncul bersama di lebih sedikit teks dari ini tidak ditampilkan."))
    pairs = ner.cooccurrence(frame, kinds or None, minimum=minimum)
    if pairs.empty:
        st.info(tr("Belum ada pasangan entitas yang cukup sering disebut bersama."), icon=":material/link_off:")
        return
    st.caption(tr("Jaccard = teks yang menyebut keduanya dibagi teks yang menyebut salah satunya (0–1)."))
    st.dataframe(pairs, hide_index=True, width="stretch",
                 column_config={"Jaccard": st.column_config.NumberColumn(format="%.3f")})
    left, right = st.columns(2)
    with left:
        csv_download(pairs, f"entitas-bersama-{name}", "dl-ne-pairs")
    with right:
        csv_download(ner.edges(frame, kinds or None, minimum=minimum), f"entitas-gephi-{name}", "dl-ne-gephi")
    st.caption(tr("Berkas kedua berisi kolom Source, Target, dan Weight, siap dibuka di Gephi."))


def ner_details(job):
    """Shown in the dataset dialog of an NER job."""
    params, metadata = job["params"], job["metadata"]
    origin = params.get("masukan") or {}
    st.caption(tr('Masukan: {0} · proyek {1} · kolom {2}', label(origin.get("sumber")), origin.get("proyek", "-"),
                  ", ".join(params.get("kolom", []))))
    if metadata.get("model"):
        st.code(ner.method_text(metadata, "teks"), language=None, wrap_lines=True)
