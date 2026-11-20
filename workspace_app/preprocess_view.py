"""Preprocessing page and its history page."""
from functools import partial
from io import BytesIO

import pandas as pd
import streamlit as st

from workspace_app import preprocess as pp
from workspace_app import ui
from workspace_app.excel import xlsx_bytes
from workspace_app.jobs import ACTIVE, clean_project, encode
from workspace_app.sources import label
from workspace_app.views import csv_bytes, history_page, started_label

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
    mode = st.radio("Sumber masukan", ["Riwayat scraping", "Unggah berkas"], horizontal=True, key=KEY + "input", width="stretch")
    if mode == "Riwayat scraping":
        present, _ = manager.inventory_options()
        text_sources = [source for source in present if source in pp.TEXT_COLUMNS or source == "gabungan"]
        if not text_sources:
            st.info("Belum ada data teks di Riwayat. Selesaikan satu scraping, atau unggah berkas.")
            return None
        left, right = st.columns(2)
        source = left.selectbox("Sumber", text_sources, format_func=label, key=KEY + "source")
        projects = right.multiselect("Proyek", manager.inventory_options([source])[1],
                                     placeholder="Semua proyek", key=f"{KEY}projects-{source}")
        jobs = [job for job in manager.list_jobs(source, limit=100_000)
                if job["status"] not in ACTIVE and job["count"] and (not projects or job["project"] in projects)]
        if not jobs:
            st.info("Tidak ada pekerjaan berisi data untuk pilihan ini.")
            return None
        by_id = {job["id"]: job for job in jobs}
        job_ids = st.multiselect(
            "Pekerjaan", list(by_id), default=list(by_id)[:1], key=f"{KEY}jobs-{source}-{'|'.join(projects)}",
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

    file = st.file_uploader("Berkas data (CSV atau Excel)", type=["csv", "xlsx"], key=KEY + "file")
    if not file:
        return None
    try:
        rows = read_table(file.name, file.getvalue())
    except Exception as exc:
        st.error(f"Berkas tidak bisa dibaca: {exc}")
        return None
    if not rows:
        st.info("Berkas tidak berisi baris data.")
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
    columns = st.multiselect("Kolom teks", available,
                             default=None if key in st.session_state else chosen, key=key,
                             help="Beberapa kolom digabung dengan spasi, misalnya judul dan isi berita.")
    st.caption(f"{ui.fmt(total)} baris akan diproses.")
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
    st.caption(f"**{title}:** {ui.fmt(report['entri'])} entri, {len(report['frasa'])} frasa lebih dari satu kata.")
    if issues:
        st.warning("Periksa kamus ini: " + "; ".join(issues) + ". Tidak ada yang diperbaiki otomatis.")


def show_words_check(report, title, keep_negation):
    text = f"**{title}:** {ui.fmt(report['entri'])} kata"
    if report["ganda"]:
        text += f", ganda: {', '.join(report['ganda'])}"
    st.caption(text + ".")
    if report["negasi"] and keep_negation:
        st.caption("Kata negasi di daftar ini (" + ", ".join(report["negasi"]) + ") tetap dipertahankan.")


def uploaded_slang():
    file = st.file_uploader("Kamus kata gaul tambahan (JSON atau CSV slang,formal)", type=["json", "txt", "csv"],
                            key=KEY + "slang-file")
    if not file:
        return [], None
    try:
        pairs = pp.parse_slang(file.getvalue(), file.name)
    except (ValueError, UnicodeDecodeError) as exc:
        st.error(f"Kamus tidak bisa dibaca: {exc}")
        return [], None
    show_slang_check(pp.check_slang(pairs), file.name)
    return pairs, file.name


def uploaded_stopwords(keep_negation):
    file = st.file_uploader("Daftar stopword (TXT satu kata per baris, atau CSV)", type=["txt", "csv"],
                            key=KEY + "stop-file")
    if not file:
        return [], None
    try:
        words = pp.parse_words(file.getvalue())
    except UnicodeDecodeError as exc:
        st.error(f"Daftar stopword tidak bisa dibaca: {exc}")
        return [], None
    show_words_check(pp.check_words(words), file.name, keep_negation)
    return words, file.name


def config_section():
    st.radio("Preset", list(pp.PRESETS), key=KEY + "preset", on_change=apply_preset, horizontal=True, width="stretch",
             help="Memilih preset mengisi ulang semua langkah. Setiap langkah tetap bisa diubah.")
    with st.expander("Pembersihan teks", expanded=True):
        st.caption("1. Tag HTML, entitas, Unicode (NFKC), dan spasi berlebih selalu dirapikan.")
        st.checkbox("2. Huruf kecil", key=KEY + "lowercase")
        for number, (name, label) in enumerate([("urls", "URL, email, dan @mention"), ("hashtags", "Hashtag"),
                                                ("emoji", "Emoji")], start=3):
            st.selectbox(f"{number}. {label}", list(CHOICES[name]), format_func=CHOICES[name].get, key=KEY + name)
        st.checkbox("6. Huruf berulang (bagusss → bagus)", key=KEY + "elongation")

    with st.expander("Kata gaul"):
        st.checkbox("7. Normalisasi kata gaul", key=KEY + "slang",
                    help="Dijalankan sebelum angka dan tanda baca dihapus, agar kunci seperti b4, 7an, dan w/ "
                         "tetap cocok. Penggantian hanya satu lapis.")
        show_slang_check(pp.check_slang(pp.project_slang()), "Kamus bawaan (slang.txt)")
        slang_pairs, slang_name = uploaded_slang()
        st.text_area("Kata gaul tambahan, satu per baris", key=KEY + "extra_slang", placeholder="gpp=tidak apa-apa")
        st.caption("Urutan prioritas: isian di atas, lalu berkas unggahan, lalu kamus bawaan.")

    with st.expander("Angka dan tanda baca"):
        st.selectbox("8. Angka", list(CHOICES["numbers"]), format_func=CHOICES["numbers"].get, key=KEY + "numbers")
        st.selectbox("9. Tanda baca", list(CHOICES["punctuation"]), format_func=CHOICES["punctuation"].get,
                     key=KEY + "punctuation", help="Tanda hubung di dalam kata (teman-teman) tetap dipertahankan.")

    with st.expander("Stopword, stemming, dan token"):
        st.checkbox("10. Hapus stopword", key=KEY + "stopwords")
        cols = st.columns(2)
        for index, (source, label) in enumerate(STOPWORD_SOURCES.items()):
            cols[index % 2].checkbox(label, key=f"{KEY}sw-{source}")
        keep_negation = st.toggle("Pertahankan kata negasi (tidak, bukan, jangan, belum, kurang, tak, enggak)",
                                  key=KEY + "keep_negation")
        show_words_check(pp.check_words(pp.project_stopwords()), "Bawaan proyek (stopwords.txt)", keep_negation)
        stop_words, stop_name = uploaded_stopwords(keep_negation)
        if stop_words and not st.session_state[f"{KEY}sw-upload"]:
            st.caption("Centang **Berkas unggahan** agar daftar ini dipakai.")
        left, right = st.columns(2)
        left.text_area("Stopword tambahan, satu per baris", key=KEY + "extra_stopwords")
        right.text_area("Kata yang tidak boleh dihapus", key=KEY + "keep_words")
        st.checkbox("11. Stemming (Sastrawi)", key=KEY + "stemming",
                    help="Kata dasar diambil per kata unik dan disimpan di cache selama aplikasi berjalan.")
        st.number_input("12. Panjang token minimum (0 = tidak dibatasi)", 0, 20, key=KEY + "min_length")

    with st.expander("Baris"):
        st.checkbox("13. Buang baris yang kosong setelah diproses", key=KEY + "drop_empty")
        st.checkbox("Buang baris duplikat (berdasarkan teks bersih)", key=KEY + "drop_duplicates")
    uploads = {"kata_gaul": slang_name, "stopword": stop_name}
    return slang_pairs, stop_words, uploads


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #

def show_report(report):
    cols = st.columns(3)
    cols[0].metric("Baris masuk", ui.fmt(report['baris_masuk']), border=True)
    cols[1].metric("Baris keluar", ui.fmt(report['baris_keluar']), border=True)
    cols[2].metric("Total token", ui.fmt(report['total_token']), border=True)
    st.dataframe(pd.DataFrame([{"Langkah": REPORT_LABELS.get(key, key), "Jumlah": ui.fmt(value)}
                               for key, value in report.items()]), width="stretch", hide_index=True)


def preprocessing_details(job):
    if not job:
        return
    params = job["params"]
    origin = params.get("masukan", {})
    if origin.get("jenis") == "berkas":
        st.caption(f"Masukan: berkas **{origin.get('berkas')}** · kolom {', '.join(params.get('kolom', []))}")
    else:
        names = ", ".join(dict.fromkeys(item["proyek"] for item in origin.get("pekerjaan", [])))
        st.caption(f"Masukan: {label(origin.get('sumber'))} · "
                   f"{len(origin.get('pekerjaan', []))} pekerjaan ({names}) · kolom {', '.join(params.get('kolom', []))}")
    if report := job["metadata"].get("laporan"):
        with st.expander("Laporan langkah", expanded=True):
            show_report(report)
    with st.expander("Konfigurasi"):
        st.json(params.get("konfigurasi", {}), expanded=False)


def preprocessing_page(manager):
    ui.page_header("Preprocessing Data", "Bersihkan teks hasil scraping atau berkas kamu sebelum analisis")
    ensure_defaults()
    with st.container(key="panel-pp-input"):
        st.header("Data masukan", anchor=False)
        data = input_section(manager)
    with st.container(key="panel-pp-config"):
        st.header("Konfigurasi", anchor=False)
        slang_pairs, stop_words, uploads = config_section()
    config = current_config()
    resources = pp.build_resources(config, slang_upload=slang_pairs, stopword_upload=stop_words)
    if not data or not data["columns"]:
        st.info("Pilih data dan kolom teks untuk melihat pratinjau.")
        return

    st.header(f"Pratinjau {min(PREVIEW_ROWS, len(data['sample']))} baris pertama", anchor=False)
    stats = pp.new_stats()
    st.dataframe(pd.DataFrame([{
        "Teks asli": pp.join_columns(row, data["columns"]),
        "Teks bersih": pp.clean_text(pp.join_columns(row, data["columns"]), config, resources, stats),
    } for row in data["sample"]]), width="stretch", hide_index=True)

    st.header("Jalankan", anchor=False)
    params = {"masukan": data["origin"], "kolom": data["columns"], "konfigurasi": config, "kamus_unggahan": uploads}
    save = st.toggle("Simpan ke Riwayat Preprocessing", True, key=KEY + "save")
    active, _ = manager.snapshot()
    project = ""
    if save:
        names = list(dict.fromkeys(manager.projects("preprocessing") + manager.projects()))
        project = clean_project(st.selectbox(
            "Nama proyek", names, index=None, accept_new_options=True, key=KEY + "project",
            placeholder="Ketik nama proyek baru atau pilih yang sudah ada",
        ))
        st.caption("Berjalan di latar belakang, satu antrean dengan scraping.")
    else:
        st.caption("Diproses langsung di halaman ini. Hasil bisa diunduh sampai halaman dimuat ulang; "
                   "untuk data besar halaman akan menunggu sampai selesai.")
    clicked = st.button("Jalankan preprocessing", type="primary", icon=":material/play_arrow:",
                        disabled=active is not None or (save and not project))
    if save and not project:
        st.caption("Isi nama proyek untuk mengaktifkan tombol.")
    if clicked:
        rows = data["load"]()
        if save:
            try:
                manager.start_preprocessing(params, rows, data["columns"], config, resources, project=project)
                # The running page shows the live log, so people are not left wondering whether anything happened.
                st.switch_page(ui.PAGES["berjalan"])
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        else:
            bar = st.progress(0.0, text="Memproses…")
            output, report = pp.run(rows, data["columns"], config, resources,
                                    progress=lambda done, total, _: bar.progress(done / total,
                                                                                 text=f"{ui.fmt(done)} dari {ui.fmt(total)} baris"))
            bar.empty()
            st.session_state[KEY + "result"] = {"rows": output, "report": report, "params": params}

    if result := st.session_state.get(KEY + "result"):
        st.header("Hasil (tidak disimpan)", anchor=False)
        show_report(result["report"])
        st.dataframe(pd.DataFrame(result["rows"][:PREVIEW_ROWS]).drop(columns=["paragraphs"], errors="ignore"),
                     width="stretch", hide_index=True)
        cols = st.columns(3)
        name = "preprocessing"
        cols[0].download_button("Unduh Excel", partial(xlsx_bytes, result["rows"]), f"{name}.xlsx",
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                on_click="ignore", key=KEY + "dl-xlsx")
        cols[1].download_button("Unduh CSV", partial(csv_bytes, result["rows"]), f"{name}.csv", "text/csv",
                                on_click="ignore", key=KEY + "dl-csv")
        cols[2].download_button("Unduh JSON", lambda: encode(result), f"{name}.json", "application/json",
                                on_click="ignore", key=KEY + "dl-json")


def preprocessing_history_page(manager):
    history_page(manager, preprocessing=True, details=preprocessing_details)
