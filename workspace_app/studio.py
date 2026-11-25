"""Data section: "Lakukan Scraping" (catalogue of sources) and "Dataset" (everything collected)."""
import json
from html import escape

import streamlit as st

from workspace_app.i18n import tr

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
    "gmaps_places": (":material/place:", "Maps & Review",
                     "Cari lokasi dan informasi tempat di Google Maps berdasarkan kata kunci"),
    "instagram": (":material/photo_camera:", "Media Sosial",
                  "Kumpulkan post dari profil, kata kunci, hashtag, atau komentar sebuah post"),
    "playstore": (":material/shop:", "Maps & Review",
                  "Kumpulkan ulasan aplikasi dari Google Play Store berdasarkan package ID"),
    "appstore": (":material/phone_iphone:", "Maps & Review",
                 "Kumpulkan ulasan aplikasi dari Apple App Store (hingga 500 ulasan per aplikasi dan negara)"),
    "saham": (":material/candlestick_chart:", "Keuangan",
              "Ambil harga historis OHLCV, dividen, dan split saham, aset kripto, atau kurs mata uang"),
    "threads": (":material/alternate_email:", "Media Sosial",
                "Kumpulkan post Threads berdasarkan kata kunci dan rentang tanggal"),
    "x": (":material/tag:", "Media Sosial",
          "Kumpulkan tweet dari X berdasarkan kata kunci, profil, atau URL"),
    "youtube": (":material/smart_display:", "Media Sosial",
                "Kumpulkan komentar dan balasan dari sebuah video YouTube"),
    "worldbank": (":material/public:", "Ekonomi",
                  "Ambil indikator pembangunan (PDB, inflasi, penduduk, dan lainnya) per negara dan tahun"),
    "usgs": (":material/vibration:", "Lingkungan",
             "Ambil riwayat gempa Indonesia atau seluruh dunia dari katalog USGS: magnitudo, kedalaman, lokasi, dan potensi tsunami"),
    "firms": (":material/local_fire_department:", "Lingkungan",
              "Ambil titik api (hotspot) satelit NASA per negara dan tahun: lokasi, waktu, daya radiasi api, dan tingkat keyakinan"),
    "bencana": (":material/flood:", "Lingkungan",
                "Ambil kejadian banjir, siklon, gunung api, kekeringan, kebakaran hutan, dan longsor dari GDACS dan NASA EONET"),
    "openmeteo": (":material/partly_cloudy_day:", "Lingkungan",
                  "Ambil cuaca harian atau per jam (suhu, hujan, angin) untuk lokasi mana pun, riwayat atau prakiraan"),
    "sinta": (":material/badge:", "Akademik",
              "Ambil penulis per afiliasi, profil dan metrik penulis, atau contoh dokumen dari SINTA"),
    "pangan": (":material/shopping_basket:", "Ekonomi",
               "Ambil harga harian beras, cabai, bawang, daging, telur, minyak goreng, dan gula per provinsi dari Bank Indonesia"),
    "oecd": (":material/bar_chart:", "Ekonomi",
             "Ambil dataset OECD (inflasi, PDB, pajak, kesehatan, dan dataflow lain) per negara dan tahun lewat API SDMX"),
    "imf": (":material/account_balance:", "Ekonomi",
            "Ambil data ekonomi makro IMF (World Economic Outlook) beserta proyeksinya per negara"),
}
CATEGORIES = ["Media Sosial", "Berita", "Maps & Review", "Keuangan", "Akademik", "Ekonomi", "Lingkungan"]
PAGE_SIZE = 8
# Jobs whose rows are the output of an analysis. They are listed on Hasil Analisis.
ANALYSIS_KINDS = ("sentimen", "emosi", "bertopic", "ner")
EMPTY = {
    "preprocessing": ("Belum ada hasil preprocessing untuk pilihan ini. Jalankan dulu di Preprocessing Data.",
                      "preprocessing", "Buka Preprocessing Data", ":material/filter_alt:"),
    ANALYSIS_KINDS: ("Belum ada hasil analisis yang tersimpan untuk pilihan ini. Hasil Sentimen, Emosi & "
                     "Toksisitas, dan Topik disimpan di sini setelah pekerjaannya selesai.", "sentimen", "Buka Sentimen", ":material/sentiment_satisfied:"),
}
HISTORY = {"preprocessing": "hasil preprocessing", ANALYSIS_KINDS: "hasil analisis"}
ROW = [5, 2.4, 1.3]
SORTS = {"newest": "Terbaru", "oldest": "Terlama", "rows": "Baris terbanyak"}


def description(source):
    return CATALOG[source][2]


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

def scraping_page(manager):
    ui.page_header(tr("Lakukan Scraping"), tr("Pilih sumber, atur parameternya, lalu ambil datanya"))
    catalogue(manager)


def dataset_page(manager):
    if (target := st.session_state.pop("hub-goto", None)) in ui.pages():
        st.switch_page(ui.pages()[target])
    ui.page_header(tr("Dataset"), tr("Semua data yang sudah kamu ambil, gabungkan, atau bersihkan"))
    datasets(manager)


# --------------------------------------------------------------------------- #
# Lakukan Scraping: catalogue
# --------------------------------------------------------------------------- #

def catalogue(manager):
    usage = {item["source"]: item for item in manager.inventory()}
    entries = {key: CATALOG[key] for key in LABELS}
    names = {key: tr(name) for key, name in LABELS.items()}
    counts = {category: sum(entry[1] == category for entry in entries.values()) for category in CATEGORIES}

    with st.container(key="panel-catalogue-filter"):
        query = st.text_input(tr("Cari sumber"), placeholder=tr("Instagram, Berita, Google Maps…"),
                              icon=":material/search:", label_visibility="collapsed")
        category = st.pills(
            tr("Kategori"), ["Semua", *[c for c in CATEGORIES if counts[c]]], default="Semua",
            format_func=lambda c: f"{tr(c)} · {len(entries) if c == 'Semua' else counts[c]}",
            label_visibility="collapsed", key="hub-category",
        ) or "Semua"

    keys = [key for key, (_, group, text) in entries.items()
            if (category == "Semua" or group == category)
            and (not query.strip() or query.strip().lower() in f"{names[key]} {tr(text)} {tr(group)}".lower())]
    keys.sort(key=lambda key: names[key].casefold())  # A-Z by the name shown on the row
    if not keys:
        st.info(tr("Tidak ada sumber yang cocok dengan pencarian ini."))
        return

    with st.container(key="source-grid"):  # up to three columns, fewer on narrower screens (see ui.CSS)
        for key in keys:
            source_row(key, names[key], entries[key][2], usage.get(key, {}))


def source_row(key, name, description, usage):
    with st.container(key=f"source-row-{key}", horizontal=True, vertical_alignment="center"):
        if chip := ui.source_chip(key):
            st.html(chip, width="content")
        with st.container(gap="xsmall"):
            st.html(
                '<div class="source-description">'
                f'<h2>{escape(name)}</h2>'
                f'<p>{escape(tr(description))}</p>'
                '</div>'
            )
            login_badge = (f":primary-badge[:material/lock: {tr('Perlu login')}]" if key in LOGINS
                           else f":green-badge[{tr('Tanpa login')}]")
            st.caption(tr(':material/trending_up: {0} kali dipakai  ·  {1}', ui.fmt(usage.get('jobs', 0)), login_badge))
        if st.button(tr("Kumpulkan Data"), icon=":material/arrow_forward:", icon_position="right",
                     key=f"open-{key}"):
            st.switch_page(ui.pages()[key])


# --------------------------------------------------------------------------- #
# Dataset Kamu: every job, scraping and preprocessing
# --------------------------------------------------------------------------- #

def all_jobs(manager):
    jobs = [job for kind in (None, "preprocessing", "gabungan", "sentimen", "emosi", "bertopic", "ner", *ARCHIVE_LABELS)
            for job in manager.list_jobs(kind, limit=100_000)]
    return sorted(jobs, key=lambda job: job["started"] or "", reverse=True)


def datasets(manager, only=None, prefix="ds"):
    """The dataset list. `only` limits it to one kind of job ("preprocessing") or a tuple of kinds
    (ANALYSIS_KINDS) and hides the source filter."""
    kinds = (only,) if isinstance(only, str) else only
    jobs = [job for job in all_jobs(manager) if kinds is None or job["source"] in kinds]
    sources_ = {**LABELS, **ARCHIVE_LABELS, "preprocessing": "Preprocessing", "gabungan": "Gabungan", "sentimen": "Sentimen", "emosi": "Emosi & Toksisitas",
                "bertopic": "Topik (BERTopic)", "ner": "Entitas Bernama"}
    with st.container(key="panel-dataset-filter"):
        cols = st.columns([2, 1.4, 1.4, 1.2] if not only else [2, 1.4, 1.2], vertical_alignment="bottom")
        query = cols[0].text_input(tr("Cari"), placeholder=tr("Cari nama proyek atau ID…"), icon=":material/search:",
                                   label_visibility="collapsed", key=f"{prefix}-search")
        source = None if only else cols[1].selectbox(
            tr("Sumber"), [None, *sources_], label_visibility="collapsed", key=f"{prefix}-source",
            format_func=lambda key: tr(sources_.get(key, "Semua Sumber")))
        order = cols[-1].selectbox(tr("Urutkan"), list(SORTS), label_visibility="collapsed", key=f"{prefix}-sort",
                                   format_func=lambda key: tr(SORTS[key]))
        status = cols[-2].selectbox(tr("Status"), [None, "active", "completed", "partial", "failed", "stopped",
                                              "interrupted"], label_visibility="collapsed", key=f"{prefix}-status",
                                   format_func=lambda key: tr({None: "Semua Status", "active": "Sedang Berjalan"}.get(
                                       key, ui.STATUS.get(key))))

        in_scope = [job for job in jobs if source is None or job["source"] == source]
        projects = {}
        for job in in_scope:
            projects[job["project"]] = projects.get(job["project"], 0) + 1
        top = list(projects)[:8]
        project = st.pills(tr("Proyek"), ["Semua", *top], default="Semua", key=f"{prefix}-project-{source}",
                           label_visibility="collapsed",
                           format_func=lambda name: f"{tr(name) if name == 'Semua' else name} · {len(in_scope) if name == 'Semua' else projects[name]}"
                           ) or "Semua"

    text = query.strip().lower()
    shown = [job for job in in_scope
             if (project == "Semua" or job["project"] == project)
             and (status is None or (job["status"] in ACTIVE if status == "active" else job["status"] == status))
             and (not text or text in job["project"].lower() or job["id"].startswith(text))]
    if order == "oldest":
        shown.reverse()
    elif order == "rows":
        shown.sort(key=lambda job: job["count"] or 0, reverse=True)
    if not shown:
        if only:
            message, target, link, icon = EMPTY[only]
            st.info(tr(message), icon=":material/inbox:")
            if target in ui.pages():
                st.page_link(ui.pages()[target], label=tr(link), icon=icon)
        else:
            st.info(tr("Belum ada dataset untuk pilihan ini. Mulai dari Lakukan Scraping."), icon=":material/inbox:")
            st.page_link(ui.pages()["scraping"], label=tr("Buka Lakukan Scraping"), icon=":material/travel_explore:")
        manage_history(manager, only, prefix)
        return

    pages = (len(shown) - 1) // PAGE_SIZE + 1
    page_key = f"{prefix}-page-{source}-{status}-{project}-{text}-{order}"
    page = min(st.session_state.get(page_key, 1), pages)
    first = (page - 1) * PAGE_SIZE

    with st.container(key="joblist"):
        with st.container(key="jobhead"):
            head = st.columns(ROW)
            for col, title in zip(head, ["Dataset", "Dibuat", ""]):
                col.html(f'<div class="job-head">{tr(title)}</div>')
        for job in shown[first:first + PAGE_SIZE]:
            job_row(manager, job)

    nav = st.columns([3, 1, 1], vertical_alignment="center")
    nav[0].caption(tr('Menampilkan {0}-{1} dari {2}', first + 1, min(first + PAGE_SIZE, len(shown)), ui.fmt(len(shown))))
    if nav[1].button(tr("Sebelumnya"), icon=":material/chevron_left:", disabled=page == 1, width="stretch"):
        st.session_state[page_key] = page - 1
        st.rerun()
    if nav[2].button(tr("Berikutnya"), icon=":material/chevron_right:", icon_position="right",
                     disabled=page == pages, width="stretch"):
        st.session_state[page_key] = page + 1
        st.rerun()
    manage_history(manager, only, prefix)


def job_row(manager, job):
    with st.container(key=f"jobrow-{job['id']}"):
        cols = st.columns(ROW, vertical_alignment="center")
        with cols[0]:
            st.markdown(f"**{job['project'] or '-'}**  {kind_badges(job)}")
            facts = [tr('{0} baris', ui.fmt(job['count']))]
            if job["finished"]:
                facts.append(ui.duration(job["started"], job["finished"]))
            facts.append(f"ID {job['id'][:8]}")
            st.caption(" · ".join(part for part in facts if part))
            if job["source"] in ANALYSIS_KINDS and job["status"] == "completed":
                # The badge already names the analysis, so this says what it was run on.
                origin = [job.get("masukan"), job.get("sumber_asal") and label(job["sumber_asal"])]
                if any(origin):
                    st.caption("dari " + " · ".join(str(part) for part in origin if part))
            if job["source"] == "sentimen" and job["status"] == "completed":
                sentiment_strip(job.get("sentimen"), job.get("f1_bintang"))
        with cols[1]:
            st.markdown(f":small[{ui.long_date(job['started'])}]")
            if not job["finished"]:
                st.caption(tr("Masih berjalan…"))
        with cols[2]:
            if st.button(tr("Buka"), icon=":material/open_in_new:", key=f"open-job-{job['id']}", width="stretch"):
                open_job(manager, job["id"])


def kind_badges(job):
    """What kind of dataset a row is, and its status when that is not the usual 'done'."""
    source, status = job["source"], job["status"]
    if source == "preprocessing" and status == "completed":
        return ":primary-badge[:material/filter_alt: Preprocessed]"
    if source == "gabungan" and status == "completed":
        return ":primary-badge[:material/merge: Gabungan]"
    kind = f":gray-badge[{tr(label(source))}]"
    return f"{kind} {ui.status_markdown(status)}" if source not in ANALYSIS_KINDS or status != "completed" else kind


def open_job(manager, job_id):
    """Open a dataset on its own page. Without the page registered (a view run on its own) it falls back to a dialog."""
    if "dataset-detail" in ui.pages():
        st.session_state["detail-job"] = job_id
        st.switch_page(ui.pages()["dataset-detail"])
    else:
        job_dialog(manager, job_id)


@st.dialog("Dataset", width="large")
def job_dialog(manager, job_id):
    job_detail(manager, job_id, standalone=False)


def dataset_detail_page(manager):
    job_id = st.session_state.get("detail-job")
    if st.button(tr("Kembali ke Dataset"), icon=":material/arrow_back:", key="detail-back"):
        st.switch_page(ui.pages()["dataset"])
    if not job_id or not manager.get(job_id):
        st.info(tr("Pekerjaan ini sudah dihapus."))
        return
    job_detail(manager, job_id, standalone=True)


def confirm_delete(manager, job_id, project):
    st.dialog(tr("Hapus dataset ini?"))(_confirm_delete)(manager, job_id, project)


def _confirm_delete(manager, job_id, project):
    st.warning(tr("Dataset “{0}” beserta log dan parameternya akan dihapus. Tindakan ini tidak bisa dibatalkan.", project or job_id[:8]))
    left, right = st.columns(2)
    if left.button(tr("Batal"), width="stretch", key=f"cancel-delete-{job_id}"):
        st.rerun()
    if right.button(tr("Ya, hapus"), type="primary", icon=":material/delete:", width="stretch", key=f"confirm-delete-{job_id}"):
        manager.delete(job_id)
        st.session_state.pop("detail-job", None)
        if "dataset" in ui.pages():
            st.switch_page(ui.pages()["dataset"])
        st.rerun()


def rename_dialog(manager, job_id, project):
    st.dialog(tr("Ganti nama dataset"))(_rename)(manager, job_id, project)


def _rename(manager, job_id, project):
    name = st.text_input(tr("Nama proyek"), project, key=f"rename-{job_id}")
    if st.button(tr("Simpan nama"), type="primary", key=f"rename-save-{job_id}"):
        try:
            manager.rename(job_id, name)
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def column_count(rows):
    return len({key for row in rows[:50] for key in row if key != "paragraphs"})


def detail_header(manager, job, standalone):
    """Name, status, the figures of the dataset, and where to go next."""
    job_id, rows = job["id"], job["rows"]
    active = job["status"] in ACTIVE
    with st.container(key="detail-header"):
        with st.container(horizontal=True, vertical_alignment="center", key="detail-title"):
            st.markdown(f"## {job['project'] or tr(label(job['source']))}")
            st.markdown(ui.status_markdown(job["status"]))
            if standalone:  # a dialog cannot open another dialog, so the menu is only on the page
                with st.container(key="detail-menu"):
                    choice = st.menu_button(tr("Kelola"), ["rename", "delete"], icon=":material/more_horiz:", type="tertiary",
                                            key=f"menu-{job_id}",
                                            format_func=lambda key: {"rename": tr("Ganti nama"), "delete": tr("Hapus dataset")}[key])
                if choice == "rename":
                    rename_dialog(manager, job_id, job["project"])
                elif choice == "delete":
                    confirm_delete(manager, job_id, job["project"])
        st.caption(f"{tr(label(job['source']))} · {ui.long_date(job['started'])}")
        if not active:
            with st.container(key="detail-stats"):
                stats = st.columns(4)
                stats[0].metric(tr("Baris"), ui.fmt(job["count"]))
                stats[1].metric(tr("Kolom"), ui.fmt(column_count(rows)) if rows else "-")
                stats[2].metric(tr("Durasi"), ui.duration(job["started"], job["finished"]) or "-")
                stats[3].metric(tr("Status"), tr(ui.STATUS.get(job["status"], job["status"])))
            if standalone and rows:
                next_steps(job)


def next_steps(job):
    """Where a finished dataset can go next: cleaning its text, or finding a suitable analysis."""
    from workspace_app import preprocess as pp
    can_clean = (job["source"] in pp.TEXT_COLUMNS or job["source"] == "gabungan") and "preprocessing" in ui.pages()
    with st.container(horizontal=True, key="detail-next"):
        if can_clean and st.button(tr("Lanjutkan ke Preprocessing"), icon=":material/filter_alt:", type="primary",
                                   key=f"next-pp-{job['id']}"):
            st.session_state["pp-handoff"] = {"source": job["source"], "job": job["id"]}
            st.switch_page(ui.pages()["preprocessing"])
        if "rekomendasi" in ui.pages() and st.button(
                tr("Lanjutkan ke Rekomendasi Analisis"), icon=":material/recommend:",
                type="secondary" if can_clean else "primary", key=f"next-rec-{job['id']}"):
            st.session_state["an-last"] = job["id"]
            st.switch_page(ui.pages()["rekomendasi"])


def job_detail(manager, job_id, standalone=True):
    # Imported here: views imports this module for the catalogue texts.
    from workspace_app.views import data_summary, data_table, downloads, problem_notice, show_job

    job = manager.get(job_id)
    if not job:
        st.info(tr("Pekerjaan ini sudah dihapus."))
        return
    detail_header(manager, job, standalone)
    if job["status"] in ACTIVE:
        show_job(manager, {"id": job_id, "status": job["status"]})
        return
    problem_notice(job)

    rows = job["rows"]
    names = [tr(":material/dashboard: Ringkasan"), tr(":material/table_view: Data"),
             tr(":material/science: Metode"), tr(":material/terminal: Log")]
    summary, data, method, log = st.tabs(names, default=names[1])  # the table is what people come for
    with summary:
        summary_tab(job, rows, data_summary)
    with data:
        data_tab(manager, job, rows, data_table, downloads)
    with method:
        method_tab(manager, job)
    with log:
        logs = manager.logs(job_id)
        if logs:
            st.code("\n".join(logs), language="text")
        else:
            st.caption(tr("Belum ada log tersimpan."))


def summary_tab(job, rows, data_summary):
    source = job["source"]
    if source == "preprocessing":
        from workspace_app.preprocess_view import show_report
        if report := job["metadata"].get("laporan"):
            show_report(report)
        else:
            st.caption(tr("Belum ada laporan langkah untuk hasil ini."))
    elif source in ARCHIVE_LABELS or source == "google_scholar" or not rows:
        st.caption(tr("Tidak ada ringkasan untuk dataset ini. Lihat tab Data dan Metode.") if source != "google_scholar"
                   else tr("Grafik dan ringkasan ada di tab Data."))
    else:
        if source == "sentimen":
            sentiment_strip(job.get("sentimen") or job["metadata"].get("sentimen"),
                            job.get("f1_bintang") or job["metadata"].get("f1_bintang"))
        data_summary(job, rows)


def data_tab(manager, job, rows, data_table, downloads):
    source = job["source"]
    if rows or (source in ("google_scholar", *ARCHIVE_LABELS) and job["metadata"]):
        if source == "google_scholar":
            from workspace_app.scholar_view import data_tabs as scholar_tabs
            scholar_tabs(job)
        elif rows:
            data_table(job, rows)
        downloads(job, rows)
    elif job["status"] == "completed":
        st.info(tr("Pekerjaan selesai tanpa data: sumber tidak mengembalikan hasil untuk parameter ini. "
                   "Ubah parameter lalu jalankan ulang."))
        rerun_button(manager, job, "empty")


def rerun_button(manager, job, where="method"):
    if job["source"] not in LABELS:
        return
    from workspace_app.views import seed_form
    active, _ = manager.snapshot()
    if st.button(tr("Jalankan ulang dengan parameter ini"), icon=":material/replay:", key=f"rerun-{where}-{job['id']}",
                 disabled=active is not None,
                 help=tr("Membuka form sumber yang sudah terisi. Kamu bisa mengubahnya sebelum mulai.")):
        seed_form(job["source"], job["params"], job["project"])
        # Switching pages from inside a dialog leaves the next page blank, so
        # the page switch happens on the full rerun instead.
        st.session_state["hub-goto"] = job["source"]
        st.rerun()


def method_tab(manager, job):
    from workspace_app.views import method_rows, method_text, show_method
    source = job["source"]
    if source in LABELS:
        st.markdown(tr("**Cara data diambil**"))
        show_method(method_rows(job))
        rerun_button(manager, job)
        st.markdown(tr("**Teks metode untuk laporan**"))
        st.caption(tr("Salin ke bab metode penelitian, lalu sesuaikan kalimatnya."))
        st.code(method_text(job), language=None, wrap_lines=True)
    elif source == "preprocessing":
        from workspace_app.preprocess_view import preprocessing_origin, settings_table
        preprocessing_origin(job)
        st.markdown(tr("**Konfigurasi**"))
        st.dataframe(settings_table(job["params"].get("konfigurasi", {})), hide_index=True, width="stretch")
    elif source == "gabungan":
        merge_details(job)
    elif source == "sentimen":
        from workspace_app.sentiment_view import sentiment_details
        sentiment_details(job)
    elif source == "emosi":
        from workspace_app.emotion_view import emotion_details
        emotion_details(job)
    elif source == "bertopic":
        from workspace_app.topics_view import topic_details
        topic_details(job)
    elif source == "ner":
        from workspace_app.ner_view import ner_details
        ner_details(job)
    else:
        from workspace_app.preprocess_view import settings_table
        st.caption(tr("Arsip dari versi sebelumnya. Parameter dan metadata tersimpan tersedia di bawah."))
        st.markdown(tr("**Parameter**"))
        st.dataframe(settings_table(job["params"]), hide_index=True, width="stretch")
        if job["metadata"]:
            st.markdown(tr("**Metadata**"))
            st.dataframe(settings_table(job["metadata"]), hide_index=True, width="stretch")


def sentiment_strip(counts, f1=None):
    """Share of each sentiment as a thin stacked bar with the percentages beside it.

    `counts` is the `sentimen` object of a sentiment job's metadata, as JSON text or a dict; `f1` is the macro F1
    of the star classifier when it was trained."""
    if isinstance(counts, str):
        try:
            counts = json.loads(counts)
        except ValueError:
            return
    if not isinstance(counts, dict):
        return
    total = sum(counts.values())
    if not total:
        return
    from workspace_app.sentiment_view import COLORS
    parts = [(name, counts.get(name, 0) / total * 100) for name in ("positif", "netral", "negatif")]
    bar = "".join(f'<span style="width:{share:.2f}%;background:{COLORS[name]}"></span>' for name, share in parts if share)
    text = " · ".join(f"{share:.0f}% {name}" for name, share in parts)
    extra = f'<span class="f1">F1 classifier bintang {f1:.2f}</span>'.replace(".", ",") if f1 is not None else ""
    st.html(f'<div class="sentiment-strip"><div class="bar" aria-hidden="true">{bar}</div>'
            f'<span>{text}</span>{extra}</div>')


def merge_details(job):
    """What a merged dataset was made from."""
    params = job["params"]
    st.markdown(tr("**Dibuat dari**"))
    items = "".join(f"<dt>{escape(item['proyek'])}</dt><dd>{escape(item['sumber'])} · {ui.fmt(item['baris'])} baris</dd>"
                    for item in params.get("dataset", []))
    st.html(f'<dl class="method">{items}</dl>')
    dropped = job["metadata"].get("duplikat_dibuang", 0)
    notes = ["Kolom sumber_data dan pekerjaan_asal ditambahkan." if params.get("tandai") else "Tanpa kolom penanda sumber.",
             f"{ui.fmt(dropped)} baris duplikat dibuang." if params.get("buang_duplikat") else "Baris duplikat dibiarkan."]
    st.caption("  ".join(notes))


def confirm_delete_all(manager, what, sources_):
    st.dialog(tr("Hapus seluruh riwayat?"))(_confirm_delete_all)(manager, what, sources_)


def _confirm_delete_all(manager, what, sources_):
    st.warning(tr("Seluruh {0} akan dihapus. Tindakan ini tidak bisa dibatalkan.", tr(what)))
    left, right = st.columns(2)
    if left.button(tr("Batal"), width="stretch", key="cancel-delete-all"):
        st.rerun()
    if right.button(tr("Ya, hapus semua"), type="primary", icon=":material/delete:", width="stretch",
                    key="confirm-delete-all"):
        manager.delete(sources_=sources_)
        st.rerun()


def manage_history(manager, only=None, prefix="ds"):
    with st.expander(tr("Kelola riwayat")):
        what = HISTORY[only] if only else "pekerjaan yang sudah berakhir, termasuk hasil preprocessing"
        sources_ = ([only] if isinstance(only, str) else list(only)) if only else [
            *LABELS, *ARCHIVE_LABELS, "preprocessing", "gabungan", "sentimen", "emosi", "bertopic", "ner"]
        if st.button(tr("Hapus seluruh riwayat"), icon=":material/delete:", key=f"{prefix}-delete-all"):
            confirm_delete_all(manager, what, sources_)


def preprocessing_dataset_page(manager):
    ui.page_header(tr("Dataset Preprocessing"), tr("Hasil pembersihan teks: buka untuk melihat laporan langkah, "
                   "tabel, dan unduhan"))
    datasets(manager, only="preprocessing", prefix="pp")


def analysis_results_page(manager):
    ui.page_header(tr("Hasil Analisis"), tr("Hasil analisis yang tersimpan: buka untuk melihat metrik, teks metode, "
                   "tabel, dan unduhan"))
    datasets(manager, only=ANALYSIS_KINDS, prefix="ha")
