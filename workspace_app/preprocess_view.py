"""Preprocessing page and its history page."""
from functools import partial
from io import BytesIO

import pandas as pd
import streamlit as st

from workspace_app.i18n import tr

from workspace_app import preprocess as pp
from workspace_app import ui
from workspace_app.excel import xlsx_bytes
from workspace_app.jobs import ACTIVE, clean_project, encode
from workspace_app.sources import label
from workspace_app.views import csv_bytes, started_label

PREVIEW_ROWS = 20
KEY = "pp-"
CHOICES = {
    "urls": {"token": "Ganti token (<url>, <email>, <user>)", "remove": "Hapus", "keep": "Biarkan"},
    "hashtags": {"strip": "Buang tanda #, kata tetap", "remove": "Hapus seluruhnya", "keep": "Biarkan"},
    "emoji": {"words": "Ubah ke kata", "remove": "Hapus", "keep": "Biarkan"},
    "numbers": {"remove": "Hapus", "keep": "Biarkan"},
    "punctuation": {"remove": "Hapus", "keep": "Biarkan"},
}
STOPWORD_SOURCES = {
    "project": "Bawaan proyek (stopwords.txt)", "sastrawi": "Bawaan Sastrawi",
    "english": "Inggris (scikit-learn)", "upload": "Berkas unggahan",
}
REPORT_LABELS = {
    "baris_masuk": "Baris masuk", "baris_keluar": "Baris keluar",
    "baris_kosong_dibuang": "Baris kosong dibuang", "duplikat_dibuang": "Duplikat dibuang",
    "total_token": "Total token", "url": "URL", "email": "Email", "mention": "@mention",
    "hashtag": "Hashtag dihapus", "emoji": "Emoji", "kata_gaul": "Kata gaul diganti",
    "angka": "Angka dihapus", "stopword": "Stopword dihapus", "stemming": "Kata di-stem",
    "token_pendek": "Token pendek dibuang",
}
TEXT_SETTINGS = ("extra_slang", "extra_stopwords", "keep_words")


# --------------------------------------------------------------------------- #
# Configuration state
# --------------------------------------------------------------------------- #

def apply_preset():
    values = {**pp.PRESETS[st.session_state[KEY + "preset"]], **pp.DEFAULT_LISTS}
    sources = values.pop("stopword_sources")
    for name, value in values.items():
        st.session_state[KEY + name] = value
    for source in STOPWORD_SOURCES:
        st.session_state[f"{KEY}sw-{source}"] = source in sources


def ensure_defaults():
    if KEY + "preset" not in st.session_state:
        st.session_state[KEY + "preset"] = next(iter(pp.PRESETS))
        apply_preset()
    for name in TEXT_SETTINGS:
        st.session_state.setdefault(KEY + name, "")


def current_config():
    state = st.session_state
    config = {name: state[KEY + name] for name in pp.LIGHT}
    config["min_length"] = int(config["min_length"])
    config["keep_negation"] = state[KEY + "keep_negation"]
    config["stopword_sources"] = [source for source in STOPWORD_SOURCES if state[f"{KEY}sw-{source}"]]
    config.update({name: state[KEY + name] for name in TEXT_SETTINGS})
    config["preset"] = state[KEY + "preset"]
    return config


# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #

@st.cache_data(max_entries=2, show_spinner="Membaca berkas…")
def read_table(name, data):
    if name.lower().endswith(".xlsx"):
        frame = pd.read_excel(BytesIO(data), dtype=str, keep_default_na=False)
        # Excel headers can be numeric. Keep column names usable in the UI and
        # preserve distinct columns such as the number 2025 and the text "2025".
        reserved = set(map(str, frame.columns))
        names = []
        used = set()
        for column in frame.columns:
            label = str(column)
            if label in used:
                suffix = 1
                while f"{label}.{suffix}" in reserved:
                    suffix += 1
                label = f"{label}.{suffix}"
                reserved.add(label)
            names.append(label)
            used.add(label)
        frame.columns = names
    else:
        frame = pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return frame.to_dict("records")


def input_section(manager):
    mode = st.radio(tr("Sumber masukan"), ["Riwayat scraping", "Unggah berkas"], format_func=tr, horizontal=True, key=KEY + "input", width="stretch")
    if mode == "Riwayat scraping":
        present, _ = manager.inventory_options()
        text_sources = [source for source in present if source in pp.TEXT_COLUMNS or source == "gabungan"]
        if not text_sources:
            st.info(tr("Belum ada data teks di Riwayat. Selesaikan satu scraping, atau unggah berkas."))
            return None
        left, right = st.columns(2)
        source = left.selectbox(tr("Sumber"), text_sources, format_func=lambda key: tr(label(key)), key=KEY + "source")
        projects = right.multiselect(tr("Proyek"), manager.inventory_options([source])[1],
                                     placeholder=tr("Semua proyek"), key=f"{KEY}projects-{source}")
        jobs = [job for job in manager.list_jobs(source, limit=100_000)
                if job["status"] not in ACTIVE and job["count"] and (not projects or job["project"] in projects)]
        if not jobs:
            st.info(tr("Tidak ada pekerjaan berisi data untuk pilihan ini."))
            return None
        by_id = {job["id"]: job for job in jobs}
        job_ids = st.multiselect(
            tr("Pekerjaan"), list(by_id), default=list(by_id)[:1], key=f"{KEY}jobs-{source}-{'|'.join(projects)}",
            format_func=lambda key: (f"{by_id[key]['project']} · {started_label(by_id[key]['started'])} · "
                                     f"{ui.fmt(by_id[key]['count'])} baris"),
        )
        if not job_ids:
            return None
        sample = manager.input_rows(job_ids, PREVIEW_ROWS)
        total = sum(by_id[key]["count"] for key in job_ids)
        default = (["teks_bersih", *(column for columns in pp.TEXT_COLUMNS.values() for column in columns)]
                   if source == "gabungan" else pp.TEXT_COLUMNS.get(source, []))
        origin = {"jenis": "riwayat", "sumber": source,
                  "pekerjaan": [{"id": key, "proyek": by_id[key]["project"]} for key in job_ids]}
        available = manager.input_text_columns(job_ids)
        return choose_columns(sample, total, default, origin, lambda: manager.input_rows(job_ids), source, available)

    file = st.file_uploader(tr("Berkas data (CSV atau Excel)"), type=["csv", "xlsx"], key=KEY + "file")
    if not file:
        return None
    try:
        rows = read_table(file.name, file.getvalue())
    except Exception as exc:
        st.error(tr('Berkas tidak bisa dibaca: {0}', exc))
        return None
    if not rows:
        st.info(tr("Berkas tidak berisi baris data."))
        return None
    available = list(dict.fromkeys(column for row in rows for column, value in row.items()
                                   if isinstance(value, str)))
    return choose_columns(rows[:PREVIEW_ROWS], len(rows), [], {"jenis": "berkas", "berkas": file.name},
                          lambda: rows, "berkas", available)


def choose_columns(sample, total, default, origin, load, scope, available):
    available = [column for column in available if column != "pekerjaan_asal"]
    chosen = list(dict.fromkeys(column for column in default if column in available))
    chosen = chosen or available[:1]
    key = f"{KEY}columns-{scope}"
    if key in st.session_state:
        previous = st.session_state[key]
        filtered = [column for column in previous if column in available]
        if filtered != previous:
            st.session_state[key] = filtered or chosen
    columns = st.multiselect(tr("Kolom teks"), available,
                             default=None if key in st.session_state else chosen, key=key,
                             help=tr("Beberapa kolom digabung dengan spasi, misalnya judul dan isi berita."))
    st.caption(tr('{0} baris akan diproses.', ui.fmt(total)))
    return {"sample": sample, "total": total, "columns": columns, "origin": origin, "load": load}


# --------------------------------------------------------------------------- #
# Configuration widgets
# --------------------------------------------------------------------------- #

def show_slang_check(report, title):
    issues = []
    if report["kunci_ganda"]:
        issues.append(f"{len(report['kunci_ganda'])} kunci ganda, entri terakhir dipakai: "
                      + ", ".join(report["kunci_ganda"]))
    if report["ke_diri_sendiri"]:
        issues.append("dipetakan ke dirinya sendiri: " + ", ".join(report["ke_diri_sendiri"]))
    if report["saling_tukar"]:
        issues.append("saling tukar: " + ", ".join(f"{a} ↔ {b}" for a, b in report["saling_tukar"]))
    st.caption(tr('**{0}:** {1} entri, {2} frasa lebih dari satu kata.', title, ui.fmt(report['entri']), len(report['frasa'])))
    if issues:
        st.warning("Periksa kamus ini: " + "; ".join(issues) + ". Tidak ada yang diperbaiki otomatis.")


def show_words_check(report, title, keep_negation):
    text = tr("**{0}:** {1} kata", title, ui.fmt(report["entri"]))
    if report["ganda"]:
        text += tr(", ganda: {0}", ", ".join(report["ganda"]))
    st.caption(text + ".")
    if report["negasi"] and keep_negation:
        st.caption(tr("Kata negasi di daftar ini ({0}) tetap dipertahankan.", ", ".join(report["negasi"])))


def uploaded_slang():
    file = st.file_uploader(tr("Kamus kata gaul tambahan (JSON atau CSV slang,formal)"), type=["json", "txt", "csv"],
                            key=KEY + "slang-file")
    if not file:
        return [], None
    try:
        pairs = pp.parse_slang(file.getvalue(), file.name)
    except (ValueError, UnicodeDecodeError) as exc:
        st.error(tr('Kamus tidak bisa dibaca: {0}', exc))
        return [], None
    show_slang_check(pp.check_slang(pairs), file.name)
    return pairs, file.name


def uploaded_stopwords(keep_negation):
    file = st.file_uploader(tr("Daftar stopword (TXT satu kata per baris, atau CSV)"), type=["txt", "csv"],
                            key=KEY + "stop-file")
    if not file:
        return [], None
    try:
        words = pp.parse_words(file.getvalue())
    except UnicodeDecodeError as exc:
        st.error(tr('Daftar stopword tidak bisa dibaca: {0}', exc))
        return [], None
    show_words_check(pp.check_words(words), file.name, keep_negation)
    return words, file.name


def config_section():
    st.radio(tr("Preset"), list(pp.PRESETS), format_func=tr, key=KEY + "preset", on_change=apply_preset, horizontal=True, width="stretch",
             help=tr("Memilih preset mengisi ulang semua langkah. Setiap langkah tetap bisa diubah."))
    with st.expander(tr("Pembersihan teks"), expanded=True):
        st.caption(tr("1. Tag HTML, entitas, Unicode (NFKC), dan spasi berlebih selalu dirapikan."))
        st.checkbox(tr("2. Huruf kecil"), key=KEY + "lowercase")
        for number, (name, label) in enumerate([("urls", "URL, email, dan @mention"), ("hashtags", "Hashtag"),
                                                ("emoji", "Emoji")], start=3):
            st.selectbox(f"{number}. {label}", list(CHOICES[name]), format_func=lambda k, labels=CHOICES[name]: tr(labels[k]), key=KEY + name)
        st.checkbox(tr("6. Huruf berulang (bagusss → bagus)"), key=KEY + "elongation")

    with st.expander(tr("Kata gaul")):
        st.checkbox(tr("7. Normalisasi kata gaul"), key=KEY + "slang",
                    help=tr("Dijalankan sebelum angka dan tanda baca dihapus, agar kunci seperti b4, 7an, dan w/ "
                         "tetap cocok. Penggantian hanya satu lapis."))
        show_slang_check(pp.check_slang(pp.project_slang()), tr("Kamus bawaan (slang.txt)"))
        slang_pairs, slang_name = uploaded_slang()
        st.text_area(tr("Kata gaul tambahan, satu per baris"), key=KEY + "extra_slang", placeholder=tr("gpp=tidak apa-apa"))
        st.caption(tr("Urutan prioritas: isian di atas, lalu berkas unggahan, lalu kamus bawaan."))

    with st.expander(tr("Angka dan tanda baca")):
        st.selectbox(tr("8. Angka"), list(CHOICES["numbers"]), format_func=lambda k: tr(CHOICES["numbers"][k]), key=KEY + "numbers")
        st.selectbox(tr("9. Tanda baca"), list(CHOICES["punctuation"]), format_func=lambda k: tr(CHOICES["punctuation"][k]),
                     key=KEY + "punctuation", help=tr("Tanda hubung di dalam kata (teman-teman) tetap dipertahankan."))

    with st.expander(tr("Stopword, stemming, dan token")):
        st.checkbox(tr("10. Hapus stopword"), key=KEY + "stopwords")
        cols = st.columns(2)
        for index, (source, label) in enumerate(STOPWORD_SOURCES.items()):
            cols[index % 2].checkbox(tr(label), key=f"{KEY}sw-{source}")
        keep_negation = st.toggle(tr("Pertahankan kata negasi (tidak, bukan, jangan, belum, kurang, tak, enggak)"),
                                  key=KEY + "keep_negation")
        show_words_check(pp.check_words(pp.project_stopwords()), tr("Bawaan proyek (stopwords.txt)"), keep_negation)
        stop_words, stop_name = uploaded_stopwords(keep_negation)
        if stop_words and not st.session_state[f"{KEY}sw-upload"]:
            st.caption(tr("Centang **Berkas unggahan** agar daftar ini dipakai."))
        left, right = st.columns(2)
        left.text_area(tr("Stopword tambahan, satu per baris"), key=KEY + "extra_stopwords")
        right.text_area(tr("Kata yang tidak boleh dihapus"), key=KEY + "keep_words")
        st.checkbox(tr("11. Stemming (Sastrawi)"), key=KEY + "stemming",
                    help=tr("Kata dasar diambil per kata unik dan disimpan di cache selama aplikasi berjalan."))
        st.number_input(tr("12. Panjang token minimum (0 = tidak dibatasi)"), 0, 20, key=KEY + "min_length")

    with st.expander(tr("Baris")):
        st.checkbox(tr("13. Buang baris yang kosong setelah diproses"), key=KEY + "drop_empty")
        st.checkbox(tr("Buang baris duplikat (berdasarkan teks bersih)"), key=KEY + "drop_duplicates")
    uploads = {"kata_gaul": slang_name, "stopword": stop_name}
    return slang_pairs, stop_words, uploads


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

def show_report(report):
    cols = st.columns(3)
    cols[0].metric(tr("Baris masuk"), ui.fmt(report['baris_masuk']), border=True)
    cols[1].metric(tr("Baris keluar"), ui.fmt(report['baris_keluar']), border=True)
    cols[2].metric(tr("Total token"), ui.fmt(report['total_token']), border=True)
    st.dataframe(pd.DataFrame([{"Langkah": REPORT_LABELS.get(key, key), "Jumlah": ui.fmt(value)}
                               for key, value in report.items()]), width="stretch", hide_index=True)


def preprocessing_details(job):
    if not job:
        return
    params = job["params"]
    origin = params.get("masukan", {})
    if origin.get("jenis") == "berkas":
        st.caption(tr('Masukan: berkas **{0}** · kolom {1}', origin.get('berkas'), ', '.join(params.get('kolom', []))))
    else:
        names = ", ".join(dict.fromkeys(item["proyek"] for item in origin.get("pekerjaan", [])))
        st.caption(tr('Masukan: {0} · {1} pekerjaan ({2}) · kolom {3}', label(origin.get('sumber')), len(origin.get('pekerjaan', [])), names, ', '.join(params.get('kolom', []))))
    if report := job["metadata"].get("laporan"):
        with st.expander(tr("Laporan langkah"), expanded=True):
            show_report(report)
    with st.expander(tr("Konfigurasi")):
        st.json(params.get("konfigurasi", {}), expanded=False)


def preprocessing_page(manager):
    ui.page_header(tr("Preprocessing Data"), tr("Bersihkan teks hasil scraping atau berkas kamu sebelum analisis"))
    ensure_defaults()
    with st.container(key="panel-pp-input"):
        st.header(tr("Data masukan"), anchor=False)
        data = input_section(manager)
    with st.container(key="panel-pp-config"):
        st.header(tr("Konfigurasi"), anchor=False)
        slang_pairs, stop_words, uploads = config_section()
    config = current_config()
    resources = pp.build_resources(config, slang_upload=slang_pairs, stopword_upload=stop_words)
    if not data or not data["columns"]:
        st.info(tr("Pilih data dan kolom teks untuk melihat pratinjau."))
        return

    st.header(tr('Pratinjau {0} baris pertama', min(PREVIEW_ROWS, len(data['sample']))), anchor=False)
    stats = pp.new_stats()
    st.dataframe(pd.DataFrame([{
        tr("Teks asli"): pp.join_columns(row, data["columns"]),
        tr("Teks bersih"): pp.clean_text(pp.join_columns(row, data["columns"]), config, resources, stats),
    } for row in data["sample"]]), width="stretch", hide_index=True)

    st.header(tr("Jalankan"), anchor=False)
    params = {"masukan": data["origin"], "kolom": data["columns"], "konfigurasi": config, "kamus_unggahan": uploads}
    save = st.toggle(tr("Simpan ke Riwayat Preprocessing"), True, key=KEY + "save")
    active, _ = manager.snapshot()
    project = ""
    if save:
        names = list(dict.fromkeys(manager.projects("preprocessing") + manager.projects()))
        project = clean_project(st.selectbox(
            tr("Nama proyek"), names, index=None, accept_new_options=True, key=KEY + "project",
            placeholder=tr("Ketik nama proyek baru atau pilih yang sudah ada"),
        ))
        st.caption(tr("Berjalan di latar belakang, satu antrean dengan scraping."))
    else:
        st.caption(tr("Diproses langsung di halaman ini. Hasil bisa diunduh sampai halaman dimuat ulang; "
                   "untuk data besar halaman akan menunggu sampai selesai."))
    clicked = st.button(tr("Jalankan preprocessing"), type="primary", icon=":material/play_arrow:",
                        disabled=active is not None or (save and not project))
    if save and not project:
        st.caption(tr("Isi nama proyek untuk mengaktifkan tombol."))
    if clicked:
        rows = data["load"]()
        if save:
            try:
                manager.start_preprocessing(params, rows, data["columns"], config, resources, project=project)
                # The running page shows the live log, so people are not left wondering whether anything happened.
                st.switch_page(ui.pages()["berjalan"])
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        else:
            bar = st.progress(0.0, text=tr("Memproses…"))
            output, report = pp.run(rows, data["columns"], config, resources,
                                    progress=lambda done, total, _: bar.progress(done / total,
                                                                                 text=tr('{0} dari {1} baris', ui.fmt(done), ui.fmt(total))))
            bar.empty()
            st.session_state[KEY + "result"] = {"rows": output, "report": report, "params": params}

    if result := st.session_state.get(KEY + "result"):
        st.header(tr("Hasil (tidak disimpan)"), anchor=False)
        show_report(result["report"])
        st.dataframe(pd.DataFrame(result["rows"][:PREVIEW_ROWS]).drop(columns=["paragraphs"], errors="ignore"),
                     width="stretch", hide_index=True)
        cols = st.columns(3)
        name = "preprocessing"
        cols[0].download_button(tr("Unduh Excel"), partial(xlsx_bytes, result["rows"]), f"{name}.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                on_click="ignore", key=KEY + "dl-xlsx")
        cols[1].download_button(tr("Unduh CSV"), partial(csv_bytes, result["rows"]), f"{name}.csv", "text/csv",
                                on_click="ignore", key=KEY + "dl-csv")
        cols[2].download_button(tr("Unduh JSON"), lambda: encode(result), f"{name}.json", "application/json",
                                on_click="ignore", key=KEY + "dl-json")
