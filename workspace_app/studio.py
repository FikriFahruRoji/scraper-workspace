"""Data section: "Lakukan Scraping" (catalogue of sources) and "Dataset" (everything collected)."""
from html import escape

import streamlit as st

from workspace_app import ui
from workspace_app.jobs import ACTIVE
from workspace_app.sources import ARCHIVE_LABELS, LABELS, LOGINS, label

CATALOG = {
    "google_scholar": (":material/school:", "Akademik",
                       "Ambil profil peneliti, sitasi, dan publikasi berdasarkan kata kunci"),
    "news": (":material/newspaper:", "Berita",
             "Kumpulkan artikel dari 10 portal berita Indonesia berdasarkan kata kunci dan tanggal"),
    "gmaps": (":material/location_on:", "Maps & Review",
              "Kumpulkan ulasan tempat dari Google Maps berdasarkan URL atau nama lokasi"),
    "instagram": (":material/photo_camera:", "Media Sosial",
                  "Kumpulkan post dari profil, kata kunci, hashtag, atau komentar sebuah post"),
    "playstore": (":material/shop:", "Maps & Review",
                  "Kumpulkan ulasan aplikasi dari Google Play Store berdasarkan package ID"),
    "saham": (":material/candlestick_chart:", "Keuangan",
              "Ambil harga historis OHLCV, dividen, dan split saham atau aset kripto"),
    "threads": (":material/alternate_email:", "Media Sosial",
                "Kumpulkan post Threads berdasarkan kata kunci dan rentang tanggal"),
    "x": (":material/tag:", "Media Sosial",
          "Kumpulkan tweet dari X berdasarkan kata kunci, profil, atau URL"),
    "youtube": (":material/smart_display:", "Media Sosial",
                "Kumpulkan komentar dan balasan dari sebuah video YouTube"),
}
CATEGORIES = ["Media Sosial", "Berita", "Maps & Review", "Keuangan", "Akademik"]
PAGE_SIZE = 8
ROW = [3, 2.6, 3.2, 2]


def description(source):
    return CATALOG[source][2]


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def scraping_page(manager):
    ui.page_header("Lakukan Scraping", "Pilih sumber, atur parameternya, lalu ambil datanya")
    catalogue(manager)


def dataset_page(manager):
    if (target := st.session_state.pop("hub-goto", None)) in ui.PAGES:
        st.switch_page(ui.PAGES[target])
    ui.page_header("Dataset", "Semua data yang sudah kamu ambil, gabungkan, atau bersihkan")
    datasets(manager)


# --------------------------------------------------------------------------- #
# Lakukan Scraping: catalogue
# --------------------------------------------------------------------------- #

def catalogue(manager):
    usage = {item["source"]: item for item in manager.inventory()}
    entries = {key: CATALOG[key] for key in LABELS}
    names = dict(LABELS)
    counts = {category: sum(entry[1] == category for entry in entries.values()) for category in CATEGORIES}

    with st.container(key="panel-catalogue-filter"):
        query = st.text_input("Cari sumber", placeholder="Instagram, Berita, Google Maps…",
                              icon=":material/search:", label_visibility="collapsed")
        category = st.pills(
            "Kategori", ["Semua", *[c for c in CATEGORIES if counts[c]]], default="Semua",
            format_func=lambda c: f"{c} · {len(entries) if c == 'Semua' else counts[c]}",
            label_visibility="collapsed", key="hub-category",
        ) or "Semua"

    keys = [key for key, (_, group, text) in entries.items()
            if (category == "Semua" or group == category)
            and (not query.strip() or query.strip().lower() in f"{names[key]} {text} {group}".lower())]
    keys.sort(key=lambda key: -usage.get(key, {}).get("jobs", 0))
    if not keys:
        st.info("Tidak ada sumber yang cocok dengan pencarian ini.")
        return

    for key in keys:
        source_row(key, names[key], entries[key][2], usage.get(key, {}))


def source_row(key, name, description, usage):
    with st.container(key=f"source-row-{key}", horizontal=True, vertical_alignment="center"):
        with st.container(gap="xsmall"):
            st.html(
                '<div class="source-description">'
                f'<h2>{escape(name)}</h2>'
                f'<p>{escape(description)}</p>'
                '</div>'
            )
            login_badge = (":primary-badge[:material/lock: Perlu login]" if key in LOGINS
                           else ":green-badge[Tanpa login]")
            st.caption(f":material/trending_up: {ui.fmt(usage.get('jobs', 0))} kali dipakai  ·  {login_badge}")
        if st.button("Kumpulkan Data", icon=":material/arrow_forward:", icon_position="right",
                     key=f"open-{key}"):
            st.switch_page(ui.PAGES[key])


# --------------------------------------------------------------------------- #
# Dataset Kamu: every job, scraping and preprocessing
# --------------------------------------------------------------------------- #

def all_jobs(manager):
    jobs = [job for kind in (None, "preprocessing", "gabungan", *ARCHIVE_LABELS)
            for job in manager.list_jobs(kind, limit=100_000)]
    return sorted(jobs, key=lambda job: job["started"] or "", reverse=True)


def datasets(manager, only=None, prefix="ds"):
    """The dataset list. `only` limits it to one kind of job (e.g. "preprocessing") and hides the source filter."""
    jobs = [job for job in all_jobs(manager) if only is None or job["source"] == only]
    sources_ = {**LABELS, **ARCHIVE_LABELS, "preprocessing": "Preprocessing", "gabungan": "Gabungan"}
    with st.container(key="panel-dataset-filter"):
        cols = st.columns([2, 1.4, 1.4] if not only else [2, 1.4], vertical_alignment="bottom")
        query = cols[0].text_input("Cari", placeholder="Cari nama proyek atau ID…", icon=":material/search:",
                                   label_visibility="collapsed", key=f"{prefix}-search")
        source = None if only else cols[1].selectbox(
            "Sumber", [None, *sources_], label_visibility="collapsed", key=f"{prefix}-source",
            format_func=lambda key: sources_.get(key, "Semua Sumber"))
        status = cols[-1].selectbox("Status", [None, "active", "completed", "partial", "failed", "stopped",
                                              "interrupted"], label_visibility="collapsed", key=f"{prefix}-status",
                                   format_func=lambda key: {None: "Semua Status", "active": "Sedang Berjalan"}.get(
                                       key, ui.STATUS.get(key)))

        in_scope = [job for job in jobs if source is None or job["source"] == source]
        projects = {}
        for job in in_scope:
            projects[job["project"]] = projects.get(job["project"], 0) + 1
        top = list(projects)[:8]
        project = st.pills("Proyek", ["Semua", *top], default="Semua", key=f"{prefix}-project-{source}",
                           label_visibility="collapsed",
                           format_func=lambda name: f"{name} · {len(in_scope) if name == 'Semua' else projects[name]}"
                           ) or "Semua"

    text = query.strip().lower()
    shown = [job for job in in_scope
             if (project == "Semua" or job["project"] == project)
             and (status is None or (job["status"] in ACTIVE if status == "active" else job["status"] == status))
             and (not text or text in job["project"].lower() or job["id"].startswith(text))]
    if not shown:
        if only:
            st.info("Belum ada hasil preprocessing untuk pilihan ini. Jalankan dulu di Preprocessing Data.",
                    icon=":material/inbox:")
            st.page_link(ui.PAGES["preprocessing"], label="Buka Preprocessing Data", icon=":material/filter_alt:")
        else:
            st.info("Belum ada dataset untuk pilihan ini. Mulai dari Lakukan Scraping.", icon=":material/inbox:")
            st.page_link(ui.PAGES["scraping"], label="Buka Lakukan Scraping", icon=":material/travel_explore:")
        manage_history(manager, only, prefix)
        return

    pages = (len(shown) - 1) // PAGE_SIZE + 1
    page_key = f"{prefix}-page-{source}-{status}-{project}-{text}"
    page = min(st.session_state.get(page_key, 1), pages)
    first = (page - 1) * PAGE_SIZE

    with st.container(key="joblist"):
        with st.container(key="jobhead"):
            head = st.columns(ROW)
            for col, title in zip(head, ["Aplikasi / Status", "Proyek", "Dibuat", "Aksi"]):
                col.html(f'<div class="job-head">{title}</div>')
        for job in shown[first:first + PAGE_SIZE]:
            job_row(manager, job)

    nav = st.columns([3, 1, 1], vertical_alignment="center")
    nav[0].caption(f"Menampilkan {first + 1}-{min(first + PAGE_SIZE, len(shown))} dari {ui.fmt(len(shown))}")
    if nav[1].button("Sebelumnya", icon=":material/chevron_left:", disabled=page == 1, width="stretch"):
        st.session_state[page_key] = page - 1
        st.rerun()
    if nav[2].button("Berikutnya", icon=":material/chevron_right:", icon_position="right",
                     disabled=page == pages, width="stretch"):
        st.session_state[page_key] = page + 1
        st.rerun()
    manage_history(manager, only, prefix)


def job_row(manager, job):
    with st.container(key=f"jobrow-{job['id']}"):
        cols = st.columns(ROW, vertical_alignment="center")
        with cols[0]:
            if job["source"] in ("preprocessing", "gabungan") and job["status"] == "completed":
                pill = (":primary-badge[:material/filter_alt: Preprocessed]" if job["source"] == "preprocessing"
                        else ":primary-badge[:material/merge: Gabungan]")
            else:
                pill = ui.status_markdown(job["status"])
            st.markdown(f"**{label(job['source'])}**  \n{pill}")
        with cols[1]:
            st.markdown(f"**{job['project'] or '-'}**")
            st.caption(f"{ui.fmt(job['count'])} baris · ID {job['id'][:8]}")
        with cols[2]:
            st.markdown(f":small[{ui.long_date(job['started'])}]")
            if job["finished"]:
                st.caption(f":material/schedule: {ui.duration(job['started'], job['finished'])}")
            else:
                st.caption("Masih berjalan…")
        with cols[3]:
            if st.button("Buka", icon=":material/open_in_new:", key=f"open-job-{job['id']}", width="stretch"):
                job_dialog(manager, job["id"])


@st.dialog("Dataset", width="large")
def job_dialog(manager, job_id):
    # Imported here: views imports this module for the catalogue texts.
    from workspace_app.preprocess_view import preprocessing_details
    from workspace_app.views import (data_tabs, downloads, method_rows, method_text, problem_notice,
                                     seed_form, show_job, show_method)

    job = manager.get(job_id)
    if not job:
        st.info("Pekerjaan ini sudah dihapus.")
        return
    if job["status"] in ACTIVE:
        show_job(manager, {"id": job_id, "status": job["status"]})
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{job['project'] or label(job['source'])}**")
        st.markdown(ui.status_markdown(job["status"]))
    problem_notice(job)

    can_rerun = job["source"] in LABELS
    if job["source"] == "preprocessing":
        preprocessing_details(job)
    elif job["source"] == "gabungan":
        merge_details(job)
    elif job["source"] in ARCHIVE_LABELS:
        st.caption("Arsip dari versi sebelumnya. Parameter dan metadata tersimpan tersedia di bawah.")
    elif can_rerun:
        st.markdown("**Cara data diambil**")
        show_method(method_rows(job))
        active, _ = manager.snapshot()
        if st.button("Jalankan ulang dengan parameter ini", icon=":material/replay:", key=f"rerun-{job_id}",
                     disabled=active is not None,
                     help="Membuka form sumber yang sudah terisi. Kamu bisa mengubahnya sebelum mulai."):
            seed_form(job["source"], job["params"], job["project"])
            # Switching pages from inside a dialog leaves the next page blank, so
            # the page switch happens on the full rerun instead.
            st.session_state["hub-goto"] = job["source"]
            st.rerun()

    rows = job["rows"]
    if rows or (job["source"] in ("google_scholar", *ARCHIVE_LABELS) and job["metadata"]):
        data_tabs(job, rows)
        downloads(job, rows)
    elif job["status"] == "completed":
        st.info("Pekerjaan selesai tanpa data: sumber tidak mengembalikan hasil untuk parameter ini. "
                "Ubah parameter lalu jalankan ulang.")

    if can_rerun:
        with st.expander("Teks metode untuk laporan", icon=":material/description:"):
            st.caption("Salin ke bab metode penelitian, lalu sesuaikan kalimatnya.")
            st.code(method_text(job), language=None, wrap_lines=True)
    with st.expander("Log pekerjaan", icon=":material/terminal:"):
        logs = manager.logs(job_id)
        if logs:
            st.code("\n".join(logs), language="text")
        else:
            st.caption("Belum ada log tersimpan.")
    with st.expander("Parameter asli (JSON)", icon=":material/data_object:"):
        st.json({"parameter": job["params"], "metadata": job["metadata"]}, expanded=False)
    with st.expander("Kelola", icon=":material/settings:"):
        new_name = st.text_input("Nama proyek", job["project"], key=f"rename-{job_id}")
        if st.button("Simpan nama", key=f"rename-save-{job_id}"):
            try:
                manager.rename(job_id, new_name)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        st.divider()
        st.caption("Menghapus dataset ini juga menghapus log dan parameternya. Tidak bisa dibatalkan.")
        if st.button("Hapus dataset ini", icon=":material/delete:", key=f"delete-{job_id}"):
            manager.delete(job_id)
            st.rerun()


def merge_details(job):
    """What a merged dataset was made from."""
    params = job["params"]
    st.markdown("**Dibuat dari**")
    items = "".join(f"<dt>{escape(item['proyek'])}</dt><dd>{escape(item['sumber'])} · {ui.fmt(item['baris'])} baris</dd>"
                    for item in params.get("dataset", []))
    st.html(f'<dl class="method">{items}</dl>')
    dropped = job["metadata"].get("duplikat_dibuang", 0)
    notes = ["Kolom sumber_data dan pekerjaan_asal ditambahkan." if params.get("tandai") else "Tanpa kolom penanda sumber.",
             f"{ui.fmt(dropped)} baris duplikat dibuang." if params.get("buang_duplikat") else "Baris duplikat dibiarkan."]
    st.caption("  ".join(notes))


def manage_history(manager, only=None, prefix="ds"):
    with st.expander("Kelola riwayat"):
        what = "hasil preprocessing" if only else "pekerjaan yang sudah berakhir, termasuk hasil preprocessing"
        confirm = st.checkbox(f"Saya ingin menghapus seluruh {what}.", key=f"{prefix}-confirm")
        if st.button("Hapus seluruh riwayat", disabled=not confirm, key=f"{prefix}-delete-all"):
            manager.delete(sources_=[only] if only else [*LABELS, *ARCHIVE_LABELS, "preprocessing", "gabungan"])
            st.rerun()


def preprocessing_dataset_page(manager):
    ui.page_header("Dataset Preprocessing", "Hasil pembersihan teks: buka untuk melihat laporan langkah, "
                   "tabel, dan unduhan")
    datasets(manager, only="preprocessing", prefix="pp")
