"""Sentimen: run the sentiment job on a dataset, and read the results of a sentiment dataset."""
import pandas as pd
import streamlit as st

from workspace_app import analysis as an
from workspace_app import families as fam
from workspace_app import optional
from workspace_app import preprocess as pp
from workspace_app import sentiment as sn
from workspace_app import ui
from workspace_app.analysis_view import csv_download, load_frame
from workspace_app.pick import empty_state, finished, option_label
from workspace_app.sources import label
from workspace_app.views import project_field

KEY = "sn-"
COLORS = {"positif": "#2e9e6b", "netral": "#9aa0a6", "negatif": "#d0503c"}
NO_ASPECTS = "Tanpa aspek"


@st.cache_data(show_spinner=False, max_entries=8)
def load_meta(_manager, job_id, count):
    job = _manager.get(job_id)
    return {key: job[key] for key in ("params", "metadata", "project", "source", "started")} if job else None


def sentiment_page(manager):
    ui.page_header("Sentimen", "Nada positif, netral, atau negatif dari teks, dibandingkan dengan bintang bila ada")
    jobs = finished(manager)
    if not jobs:
        empty_state()
        return
    results = [job for job in jobs if job["source"] == "sentimen"]
    inputs = [job for job in jobs if job["source"] != "sentimen"]
    tabs = ["Hasil sentimen", "Jalankan analisis"]
    if not results:
        tabs.reverse()
    chosen = st.segmented_control("Bagian", tabs, default=tabs[0], key=KEY + "part", label_visibility="collapsed")
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
    with st.container(key="panel-sn-input"):
        job_id = st.selectbox("Dataset", list(by_id), format_func=lambda value: option_label(by_id[value]),
                              index=list(by_id).index(remembered) if remembered in by_id else 0,
                              key=KEY + "dataset")
        st.session_state["an-last"] = job_id
        job = by_id[job_id]
        frame = load_frame(manager, job_id, job["count"])
        meta = load_meta(manager, job_id, job["count"]) or {"params": {}, "source": job["source"]}
        info = fam.profile(frame)
        origins = fam.origin_sources({**job, "params": meta["params"]}) or fam.merged_sources(frame)
        if not info.text_columns:
            st.info("Dataset ini tidak punya kolom teks.", icon=":material/text_fields:")
            return

        defaults = [column for source in origins for column in pp.TEXT_COLUMNS.get(source, [])
                    if column in info.text_columns]
        defaults = list(dict.fromkeys(defaults)) or [column for column in an.default_text_columns(info.text_columns)
                                                     if column != an.CLEAN_COLUMN][:1] or info.text_columns[:1]
        columns = st.multiselect("Kolom teks", info.text_columns, default=defaults, key=f"{KEY}columns-{job_id}",
                                 help="Model membaca teks asli yang dinormalisasi ringan (kata gaul, emoji), "
                                      "bukan teks_bersih: stemming dan penghapusan stopword merusak konteks.")
        ratings = [None, *[column for column in frame if column != "pekerjaan_asal"]]
        rating = st.selectbox("Kolom bintang (opsional)", ratings, key=f"{KEY}rating-{job_id}",
                              index=ratings.index(info.rating_column) if info.rating_column in ratings else 0,
                              format_func=lambda value: "Tidak ada" if value is None else value,
                              help="Bintang 1–5. Dipakai untuk classifier pembanding dan tabel ketidakcocokan.")
        star = False
        if rating:
            star = st.checkbox("Latih classifier pembanding dari bintang", True, key=KEY + "star",
                               help=f"TF-IDF + regresi logistik, bintang 1–2 negatif dan 4–5 positif. Butuh minimal "
                                    f"{sn.MIN_STAR_ROWS} ulasan dan {sn.MIN_PER_CLASS} per kelas.")
        presets = [NO_ASPECTS, *sn.ASPECT_PRESETS]
        preset_default = next((sn.SOURCE_PRESETS[source] for source in origins if source in sn.SOURCE_PRESETS),
                              NO_ASPECTS)
        preset = st.segmented_control("Kamus aspek", presets, default=preset_default, required=True,
                                      key=f"{KEY}preset-{job_id}")
        aspects = {}
        if preset != NO_ASPECTS:
            text = st.text_area("Aspek dan kata kuncinya", sn.format_aspects(sn.ASPECT_PRESETS[preset]),
                                height=180, key=f"{KEY}aspects-{job_id}-{preset}",
                                help="Satu aspek per baris: nama: kata, kata. Kalimat yang memuat kata kunci "
                                     "dinilai sentimennya sebagai aspek itu.")
            aspects = sn.parse_aspects(text)
            st.caption("Berbasis kata kunci: kalimat yang tidak memakai kata kunci tidak terhitung.")

    with st.container(key="panel-sn-run"):
        st.header("Jalankan", anchor=False)
        missing = optional.missing("transformer")
        if missing:
            st.warning(f"Paket model belum terpasang ({', '.join(missing)}). Jalankan di folder aplikasi:",
                       icon=":material/download:")
            st.code(optional.INSTALL_COMMAND, language="bash")
            return
        cached = sn.model_path(manager.directory) is not None
        download = False
        if not cached:
            download = st.checkbox(
                f"Unduh model {sn.MODEL_ID} (sekitar {sn.MODEL_SIZE_MB} MB, lisensi {sn.MODEL_LICENSE}) ke folder "
                f"data aplikasi", key=KEY + "download",
                help="Sekali saja. Setelah itu model dipakai dari komputermu tanpa internet.")
        else:
            st.caption(f"Model {sn.MODEL_ID} (lisensi {sn.MODEL_LICENSE}) sudah ada di komputer ini.")
        project = project_field(manager, "sentimen")
        st.caption(f"{ui.fmt(job['count'])} baris akan dinilai di latar belakang, satu antrean dengan scraping. "
                   "Puluhan ribu baris bisa memakan waktu beberapa menit sampai puluhan menit.")
        active, _ = manager.snapshot()
        ready = bool(columns) and bool(project) and active is None and (cached or download)
        if st.button("Jalankan analisis sentimen", type="primary", icon=":material/play_arrow:", disabled=not ready):
            params = {"masukan": {"pekerjaan": job_id, "proyek": job["project"], "sumber": job["source"]},
                      "sumber_asal": origins, "kolom": columns, "kolom_rating": rating, "classifier_bintang": star,
                      "kamus_aspek": aspects, "unduh_model": download}
            options = {"rating_column": rating, "star": star, "aspects": aspects}
            try:
                manager.start_sentiment(params, manager.input_rows([job_id]), columns, options, project=project)
                st.switch_page(ui.PAGES["berjalan"])
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if active is not None:
            st.caption("Tunggu pekerjaan yang sedang berjalan selesai atau hentikan dulu.")
        elif not columns:
            st.caption("Pilih minimal satu kolom teks.")
        elif not project:
            st.caption("Isi nama proyek untuk mengaktifkan tombol.")
        elif not (cached or download):
            st.caption("Setujui unduhan model untuk mengaktifkan tombol.")


# --------------------------------------------------------------------------- #
# Hasil
# --------------------------------------------------------------------------- #

def bar(frame, x, y, title=None):
    import plotly.express as px

    figure = px.bar(frame, x=x, y=y, color=x, color_discrete_map=COLORS, text=y, title=title)
    figure.update_layout(showlegend=False, height=280, margin={"l": 0, "r": 0, "t": 30 if title else 0, "b": 0},
                         paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis_title=None)
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def lines(frame, x, title):
    import plotly.express as px

    present = [label_ for label_ in sn.SENTIMENTS if label_ in frame]
    figure = px.line(frame, x=x, y=present, color_discrete_map=COLORS, markers=True, title=title)
    figure.update_layout(height=300, margin={"l": 0, "r": 0, "t": 30, "b": 0}, yaxis_title="% teks",
                         xaxis_title=None, legend_title=None, paper_bgcolor="rgba(0,0,0,0)",
                         plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


def pct(value):
    return f"{value:.1f}%".replace(".", ",")


def results_section(manager, jobs):
    if not jobs:
        st.info("Belum ada hasil sentimen. Jalankan analisis dulu.", icon=":material/sentiment_satisfied:")
        return
    by_id = {job["id"]: job for job in jobs}
    remembered = st.session_state.get("an-last")
    job_id = st.selectbox("Hasil sentimen", list(by_id), format_func=lambda value: option_label(by_id[value]),
                          index=list(by_id).index(remembered) if remembered in by_id else 0, key=KEY + "result")
    st.session_state["an-last"] = job_id
    job = by_id[job_id]
    frame = load_frame(manager, job_id, job["count"])
    meta = load_meta(manager, job_id, job["count"])
    metadata, params = meta["metadata"], meta["params"]
    if "sentimen" not in frame:
        st.warning("Dataset ini belum berisi kolom sentimen (pekerjaan mungkin dihentikan sebelum baris pertama).")
        return
    show_results(job, frame, metadata, params)


def show_results(job, frame, metadata, params):
    name = an.safe_name(job["project"])
    rating = metadata.get("kolom_rating")
    rating = rating if rating in frame else None
    text_columns = metadata.get("kolom") or []
    distribution = sn.distribution(frame)
    rated = int(distribution["Jumlah"].sum())

    if metadata.get("dihentikan"):
        st.warning("Pekerjaan dihentikan sebelum selesai: hanya sebagian baris yang dinilai.",
                   icon=":material/pause_circle:")
    cols = st.columns(4)
    cols[0].metric("Teks dinilai", ui.fmt(rated), border=True)
    for column, sentiment in zip(cols[1:], sn.SENTIMENTS):
        share = distribution.set_index("Sentimen").loc[sentiment, "Persen"] if rated else 0
        column.metric(sentiment.capitalize(), pct(share), border=True)
    st.caption(f"Model {metadata.get('model')} · lisensi {metadata.get('lisensi_model')} · "
               f"perangkat {metadata.get('perangkat') or '-'} · {metadata.get('detik', '-')} detik")

    bar(distribution, "Sentimen", "Jumlah")
    csv_download(distribution, f"sentimen-{name}", "dl-sn-dist")

    if rating:
        stars_section(frame, metadata, rating, text_columns, name)
    if sn.aspect_columns(frame):
        aspects_section(frame, name)
    time_section(frame, name)

    st.subheader("Paragraf metode", anchor=False)
    origin = ", ".join(label(source) for source in params.get("sumber_asal") or []) or "data"
    st.code(sn.method_text(metadata, f"teks {origin}"), language=None, wrap_lines=True)


def stars_section(frame, metadata, rating, text_columns, name):
    st.subheader("Bintang dan isi teks", anchor=False)
    table = sn.by_rating(frame, rating)
    if not table.empty:
        st.caption("Persentase sentimen model per bintang. Bintang tinggi dengan banyak teks negatif patut dibaca.")
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={label_: st.column_config.ProgressColumn(label_, min_value=0, max_value=100,
                                                                             format="%.1f%%")
                                    for label_ in sn.SENTIMENTS})

    agreements = []
    for column, title in (("sentimen", "Model RoBERTa"), ("sentimen_bintang", "Classifier dari bintang")):
        if column in frame and (found := sn.agreement(frame, rating, column)):
            agreements.append({"Metode": title, "Ulasan": found["n"], "Sesuai bintang (%)": round(found["sesuai"], 1),
                               "Cohen's kappa": round(found["kappa"], 3)})
    if agreements:
        st.markdown("**Kesepakatan dengan bintang** (bintang 1–2 dan 4–5)")
        st.dataframe(pd.DataFrame(agreements), hide_index=True, width="stretch")

    star = metadata.get("classifier_bintang") or {}
    if "akurasi" in star:
        with st.expander("Evaluasi classifier dari bintang", icon=":material/fact_check:"):
            cols = st.columns(3)
            cols[0].metric("Akurasi (data uji)", f"{star['akurasi']:.3f}", border=True)
            cols[1].metric("F1 makro", f"{star['f1_makro']:.3f}", border=True)
            cols[2].metric("Latih / uji", f"{ui.fmt(star['data_latih'])} / {ui.fmt(star['data_uji'])}", border=True)
            st.dataframe(pd.DataFrame(star["per_kelas"]).T.round(3), width="stretch")
            matrix = star["matriks_kebingungan"]
            st.caption("Matriks kebingungan (baris = bintang, kolom = tebakan)")
            st.dataframe(pd.DataFrame(matrix["nilai"], index=matrix["label"], columns=matrix["label"]),
                         width="stretch")
    elif star.get("alasan"):
        st.info(f"Classifier dari bintang tidak dilatih. {star['alasan']}", icon=":material/info:")

    wrong = sn.mismatches(frame, rating, text_columns)
    st.markdown(f"**Ketidakcocokan bintang dan teks** · {ui.fmt(len(wrong))} ulasan")
    if wrong.empty:
        st.caption("Tidak ada ulasan bintang 4–5 bernada negatif atau bintang 1–2 bernada positif.")
    else:
        st.caption("Bintang 4–5 tapi teks negatif, atau bintang 1–2 tapi teks positif; diurutkan dari skor model "
                   "tertinggi. Bisa berupa keluhan tersembunyi, sarkasme, atau salah tekan bintang.")
        st.dataframe(wrong, hide_index=True, width="stretch", height=320)
        csv_download(wrong, f"ketidakcocokan-{name}", "dl-sn-mismatch")


def aspects_section(frame, name):
    st.subheader("Sentimen per aspek", anchor=False)
    summary = sn.aspect_summary(frame)
    if summary.empty or not summary["Disebut"].sum():
        st.caption("Tidak ada kalimat yang memuat kata kunci aspek.")
        return
    st.caption("Cakupan = persentase baris yang menyebut aspek. Berbasis kata kunci, jadi baca contoh kalimatnya "
               "sebelum menyimpulkan.")
    columns = [f"{label_.capitalize()} (%)" for label_ in sn.SENTIMENTS]
    st.dataframe(summary, hide_index=True, width="stretch",
                 column_config={column: st.column_config.ProgressColumn(column, min_value=0, max_value=100,
                                                                         format="%.1f%%")
                                for column in ["Cakupan (%)", *columns]})
    csv_download(summary, f"aspek-{name}", "dl-sn-aspect")


def time_section(frame, name):
    dates = an.date_columns(frame)
    has_version = "versi_aplikasi" in frame and frame["versi_aplikasi"].astype(str).str.strip().ne("").any()
    if not dates and not has_version:
        return
    st.subheader("Dari waktu ke waktu", anchor=False)
    if dates:
        left, right = st.columns(2)
        column = left.selectbox("Kolom tanggal", dates, key=KEY + "date")
        period = right.segmented_control("Periode", list(an.PERIODS), default="ME", required=True,
                                         format_func=an.PERIODS.get, key=KEY + "period")
        table = sn.trend(frame, column, period)
        if table.empty:
            st.caption("Tanggal pada kolom ini tidak bisa dibaca.")
        else:
            lines(table, "Waktu", "Persentase sentimen per periode")
            csv_download(table, f"tren-sentimen-{name}", "dl-sn-trend")
    if has_version:
        table = sn.by_version(frame, date_column=dates[0] if dates else None)
        if not table.empty:
            st.markdown("**Per versi aplikasi** (15 versi dengan ulasan terbanyak, urut rilis)")
            st.dataframe(table, hide_index=True, width="stretch",
                         column_config={label_: st.column_config.ProgressColumn(label_, min_value=0, max_value=100,
                                                                                 format="%.1f%%")
                                        for label_ in sn.SENTIMENTS})
            csv_download(table, f"sentimen-versi-{name}", "dl-sn-version")


def sentiment_details(job):
    """Shown in the dataset dialog of a sentiment job."""
    params, metadata = job["params"], job["metadata"]
    origin = params.get("masukan") or {}
    st.caption(f"Masukan: {label(origin.get('sumber'))} · proyek {origin.get('proyek', '-')} · "
               f"kolom {', '.join(params.get('kolom', []))}")
    if metadata.get("model"):
        st.code(sn.method_text(metadata, "teks"), language=None, wrap_lines=True)
