"""Forms and result views for the local Streamlit application."""
from datetime import date, timedelta
from functools import partial
import csv
from urllib.parse import urlencode
from io import StringIO

import pandas as pd
import streamlit as st

from workspace_app.excel import xlsx_bytes
from workspace_app.jobs import ACTIVE, encode
from workspace_app.sources import LABELS, PROFILES, PUBLICATIONS, load

STATUS = {
    "running": "Berjalan", "stopping": "Sedang menghentikan", "completed": "Selesai",
    "failed": "Gagal", "partial": "Selesai sebagian", "stopped": "Dihentikan",
    "interrupted": "Terputus",
}


def date_bounds(days=30):
    today = date.today()
    bounds = st.date_input("Rentang tanggal", (today - timedelta(days=days), today),
                           max_value=today, format="DD/MM/YYYY")
    return tuple(bounds) if len(bounds) == 2 else (None, None)


def maximum(label="Batas hasil", initial=300):
    return int(st.number_input(label, min_value=0, max_value=100_000, value=initial,
                               help="0 mengambil sebanyak yang tersedia dalam batas waktu."))


def params_for(source, mode):
    if source == "news":
        portal_labels = load("news").PORTAL_LABELS
        portals = st.multiselect("Portal", list(portal_labels), default=list(portal_labels),
                                 format_func=portal_labels.get)
        keyword = st.text_input("Kata kunci", help='Mendukung AND, OR, dan frasa dalam tanda kutip.')
        start, end = date_bounds()
        with st.expander("Pengaturan browser"):
            headless = st.checkbox("Jalankan browser di latar belakang", True)
            resources = st.checkbox("Batasi pemuatan sumber daya", True)
            tabs = st.slider("Maksimum tab", 1, 4, 4)
        return dict(portal_keys=portals, keyword=keyword, start_date=start and start.isoformat(),
                    end_date=end and end.isoformat(), headless=headless,
                    disable_resources=resources, max_tabs=tabs)
    if source == "saham":
        symbol = st.text_input("Kode saham atau aset", placeholder="BBCA, AAPL, BTC-USD")
        market = st.selectbox("Pasar", ["IDX", "US", "CRYPTO", "CUSTOM"])
        interval = st.selectbox("Interval", ["1d", "1wk", "1mo"])
        start, end = date_bounds(365)
        return dict(symbol=symbol, market=market, interval=interval, start_date=start, end_date=end)
    minutes = int(st.number_input("Batas waktu (menit)", 1, 30, 10))
    if source == "gmaps":
        url = st.text_input("URL atau nama lokasi Google Maps")
        method = st.selectbox("Cara pengambilan", ["stealth", "browser"],
                              format_func={"stealth": "Endpoint lewat browser stealth",
                                           "browser": "Tampilan Google Maps lewat browser stealth"}.get)
        sort = st.selectbox("Urutan", ["relevant", "newest", "highest", "lowest"])
        return dict(url_input=url, method=method, sort=sort, max_minutes=minutes,
                    max_reviews=maximum("Batas ulasan"),
                    include_empty_reviews=st.checkbox("Sertakan rating tanpa teks"))
    if source == "instagram":
        count = maximum()
        target = st.text_input("URL post atau reel" if mode == "comments" else "Username, kata kunci, atau hashtag")
        if mode == "comments":
            return dict(comments=True, post_input=target,
                        max_comments=count, include_replies=st.checkbox("Sertakan balasan", True),
                        max_minutes=minutes)
        kind = st.selectbox("Jenis pencarian", ["username", "keyword", "hashtag"])
        start, end = date_bounds() if st.checkbox("Batasi tanggal") else (None, None)
        return dict(comments=False, query=target, input_type=kind, max_posts=count, max_minutes=minutes,
                    start_date=start, end_date=end)
    if source == "playstore":
        package = st.text_input("Package ID atau URL Play Store", placeholder="com.android.chrome")
        lang = st.text_input("Kode bahasa", "id")
        country = st.text_input("Kode negara", "id")
        sort = st.selectbox("Urutan", ["terbaru", "relevan", "rating"])
        score = st.selectbox("Rating", [None, 1, 2, 3, 4, 5],
                             format_func=lambda value: "Semua" if value is None else str(value))
        return dict(app_id=package, count=maximum("Batas ulasan"), lang=lang.strip().lower(),
                    country=country.strip().lower(), sort=sort, score=score,
                    max_minutes=minutes, only_with_text=st.checkbox("Hanya ulasan dengan teks", True))
    if source == "threads":
        keyword = st.text_input("Kata kunci")
        start, end = date_bounds()
        sort = st.selectbox("Urutan", ["recent", "top"])
        return dict(keyword=keyword, sort=sort, start_date=start, end_date=end,
                    max_minutes=minutes, max_posts=maximum("Batas post"))
    if source == "x":
        field = {"search": "keyword", "search_reply": "keyword",
                 "profile": "username", "tweet_reply": "tweet_url"}[mode]
        label = {"keyword": "Kata kunci", "username": "Username", "tweet_url": "URL tweet"}[field]
        value = st.text_input(label)
        start, end = date_bounds() if mode != "tweet_reply" else (None, None)
        sort = st.selectbox("Urutan", ["latest", "top"])
        reply_limit = int(st.number_input("Maksimum tweet yang diambil balasannya", 1, 50, 10)) if mode == "search_reply" else 10
        return dict(mode=mode, start_date=start, end_date=end, sort=sort,
                    max_reply_expand=reply_limit, max_minutes=minutes, **{field: value})
    url = st.text_input("URL atau ID video YouTube")
    method = st.selectbox("Cara pengambilan", ["api", "browser"],
                          format_func={"api": "Endpoint lewat browser stealth", "browser": "Tampilan YouTube lewat browser stealth"}.get)
    sort = st.selectbox("Urutan", ["teratas", "terbaru"])
    replies = st.checkbox("Sertakan balasan", disabled=method != "api") and method == "api"
    max_replies = int(st.number_input("Batas balasan per komentar", 0, 1000, 20)) if replies else 20
    return dict(url_input=url, method=method, sort=sort, include_replies=replies,
                max_replies=max_replies, max_comments=maximum("Batas komentar utama"),
                max_minutes=minutes)


def validate(source, params):
    item_keys = {
        "news": ["keyword"], "saham": ["symbol"], "gmaps": ["url_input"],
        "instagram": ["post_input", "query"], "playstore": ["app_id"],
        "threads": ["keyword"], "x": ["keyword", "username", "tweet_url"],
        "youtube": ["url_input"],
    }
    for key in item_keys[source]:
        if key in params and not params[key].strip():
            raise ValueError("Isi input pencarian terlebih dahulu.")
    if source == "news" and not params["portal_keys"]:
        raise ValueError("Pilih minimal satu portal.")
    if "start_date" in params and source != "instagram" and not (params["start_date"] and params["end_date"]):
        if source != "x" or params.get("mode") != "tweet_reply":
            raise ValueError("Pilih tanggal awal dan akhir.")
    if source == "playstore":
        params["app_id"] = load(source).normalize_app_input(params["app_id"])
        for key in ("lang", "country"):
            value = params[key]
            if len(value) != 2 or not value.isascii() or not value.isalpha():
                raise ValueError("Kode bahasa dan negara harus terdiri dari dua huruf.")


def publications_form(manager, source, busy):
    parser = load(source)
    mode = st.radio("Mode", ("Kata kunci", "Profil penulis"), horizontal=True)
    profile = mode == "Profil penulis"
    field = ("Tautan profil publik" if source == "scholar" else "Tautan atau ID penulis OpenAlex") if profile else "Kata kunci"
    target = st.text_input(field).strip()
    url = ""
    valid = True
    if target:
        try:
            if profile:
                if source == "scholar":
                    url = target = parser.profile_url(target)
                else:
                    target = parser.author_id(target)
                    url = f"{parser.BASE}/authors/{target}"
            else:
                url = (parser.search_url(target) if source == "scholar" else
                       f"{parser.BASE}/works?{urlencode({'search': target})}")
        except ValueError as exc:
            valid = False
            st.warning(str(exc))
    if url:
        st.link_button(f"Buka di {LABELS[source]}", url)

    limit = None if profile else int(st.number_input(
        "Batas publikasi", min_value=1, max_value=100_000, value=100,
    ))
    file_types = ["html", "htm"]
    if source == "openalex" or profile:
        file_types.append("csv")
    if profile and source == "scholar":
        st.caption("Untuk daftar lengkap, buka profil, klik Show more, lalu simpan HTML. "
                   "CSV dari menu Export bisa diimpor langsung untuk profil sendiri.")
    elif profile:
        st.caption("Di profil OpenAlex, klik Works dan simpan daftar publikasi sebagai HTML atau CSV.")
    elif source == "openalex":
        st.caption("Buka pencarian, lalu simpan hasil sebagai HTML atau unduh CSV dari menu ekspor.")
    else:
        st.caption("Buka pencarian, lalu simpan halaman hasil sebagai HTML sesuai urutan halaman.")

    files = st.file_uploader("Berkas publikasi", type=file_types, accept_multiple_files=True)
    pasted = ""
    if source == "scholar" and profile:
        with st.expander("Salin tabel profil dari browser"):
            st.caption("Tampilkan seluruh publikasi, jalankan perintah ini di Console Chrome atau Edge, lalu tempel hasilnya.")
            st.code(
                "copy('<html><body>' + "
                "['gsc_a_t', 'gsc_rsb_st', 'gsc_bpf_more'].map(id => "
                "document.getElementById(id)?.outerHTML ?? '').join('') + '</body></html>')",
                language="javascript",
            )
            pasted = st.text_area("HTML dari clipboard")
            if pasted.strip():
                st.caption("HTML dari clipboard akan digunakan.")

    import_clicked = st.button(
        "Impor publikasi", type="primary",
        disabled=busy or not valid or not (files or pasted.strip()),
    )
    preview = False
    if profile:
        label = ("Ambil halaman awal profil" if source == "scholar" else "Ambil metrik profil")
        preview = st.button(label, disabled=busy or not valid or not target)
    if not (import_clicked or preview):
        return
    documents = []
    if import_clicked:
        documents = ([("profil.html", pasted.encode("utf-8"))] if pasted.strip() else
                     [(file.name, file.getvalue()) for file in files])
    params = {
        "mode": mode, "target": target, "url": url, "limit": limit,
        "action": "preview" if preview else "import",
        "files": [name for name, _ in documents],
    }
    try:
        manager.start(source, params, documents=documents)
        st.rerun()
    except RuntimeError as exc:
        st.error(str(exc))


def source_page(manager, source):
    st.title(LABELS[source])
    active, login_status = manager.snapshot()
    if source in PUBLICATIONS:
        publications_form(manager, source, active is not None)
    else:
        scraper_form(manager, source, active, login_status)

    jobs = manager.list_jobs(source, limit=1)
    if jobs:
        st.subheader("Pekerjaan terakhir")
        show_job(manager, jobs[0])



def scraper_form(manager, source, active, login_status):
    if source in PROFILES:
        cols = st.columns([1, 2])
        if cols[0].button("Login platform", disabled=active is not None, key=f"login-{source}"):
            try:
                manager.login(source)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
        cols[1].caption(login_status.get(source, "Profil browser ini khusus aplikasi gabungan."))

    modes = {"instagram": {"comments": "Komentar / balasan", "content": "Konten"},
             "x": {"search": "Pencarian", "profile": "Profil", "tweet_reply": "Tweet dan balasan",
                   "search_reply": "Pencarian dan balasan"}}
    mode = (st.selectbox("Mode", list(modes[source]), format_func=modes[source].get,
                         key=f"mode-{source}") if source in modes else None)
    # Conditional controls update before the user starts a job.
    with st.container(border=True):
        params = params_for(source, mode)
        if st.button("Mulai scraping", type="primary", disabled=active is not None, key=f"start-{source}"):
            try:
                validate(source, params)
                manager.start(source, params)
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))


def csv_bytes(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: "'" + value if isinstance(value, str) and value.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")) else value
            for key, value in row.items()
        })
    return buffer.getvalue().encode("utf-8-sig")


def show_job(manager, summary):
    job_id = summary["id"]
    if summary["status"] in ACTIVE:
        live_result(manager, job_id)
    else:
        render_result(manager.get(job_id), manager.logs(job_id))


@st.fragment(run_every=2)
def live_result(manager, job_id):
    render_result(manager.get(job_id), manager.logs(job_id))


def render_result(job, logs=()):
    if not job:
        return
    rows = job["rows"]
    metrics = job["metadata"].get("profile_metrics", [])
    active = job["status"] in ACTIVE
    st.caption(f"{STATUS[job['status']]} · {len(rows):,} baris tersimpan")
    if job["message"]:
        st.warning(job["message"])
    with st.expander("Log pekerjaan", expanded=active):
        if logs:
            st.code("\n".join(logs), language="text")
            if len(logs) == 300:
                st.caption("Menampilkan maksimal 300 pesan terakhir.")
        else:
            st.caption("Belum ada log tersimpan untuk pekerjaan ini.")
    with st.expander("Parameter dan metadata"):
        st.json({"parameter": job["params"], "metadata": job["metadata"]}, expanded=False)
    if metrics:
        st.subheader("Metrik profil")
        st.dataframe(pd.DataFrame(metrics), width="stretch", hide_index=True)
    if not rows and not metrics:
        st.info("Menunggu hasil pertama." if active else "Pekerjaan ini tidak menghasilkan data.")
        return
    if active:
        st.subheader("Hasil sementara")
        if len(rows) > 500:
            st.caption("Tabel sementara menampilkan 500 baris pertama.")
        st.dataframe(pd.DataFrame(rows[:500]).drop(columns=["paragraphs"], errors="ignore"),
                     width="stretch", hide_index=True)
        return
    if rows:
        st.subheader("Hasil pekerjaan")
        frame = pd.DataFrame(rows).drop(columns=["paragraphs"], errors="ignore")
        result_tab, detail_tab = st.tabs(["Data", "Ringkasan"])
        with result_tab:
            query = st.text_input("Cari di hasil", key=f"find-{job['id']}")
            if query.strip():
                mask = frame.fillna("").astype(str).apply(
                    lambda col: col.str.contains(query.strip(), case=False, regex=False)
                ).any(axis=1)
                frame = frame[mask]
            st.dataframe(frame, width="stretch", hide_index=True)
        with detail_tab:
            if job["source"] == "saham" and {"date", "close"}.issubset(frame):
                st.line_chart(frame.set_index("date")["close"])
                if events := job["metadata"].get("events"):
                    st.dataframe(events, hide_index=True)
            else:
                group = next((column for column in ("portal", "rating", "media", "username") if column in frame), None)
                if group and not frame.empty:
                    st.bar_chart(frame[group].value_counts().head(15))
                else:
                    st.metric("Baris", len(frame))
    cols = st.columns(3)
    filename = f"{job['source']}-{job['id'][:8]}"
    cols[0].download_button(
        "Unduh Excel", partial(xlsx_bytes, rows or metrics, profile_metrics=metrics if rows else ()), f"{filename}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click="ignore", key=f"xlsx-{job['id']}",
    )
    cols[1].download_button("Unduh CSV", partial(csv_bytes, rows or metrics), f"{filename}.csv", "text/csv",
                            on_click="ignore", key=f"csv-{job['id']}")
    cols[2].download_button("Unduh JSON", lambda: encode(job), f"{filename}.json", "application/json",
                            on_click="ignore", key=f"json-{job['id']}")
    if rows and metrics:
        st.download_button("Unduh CSV metrik", partial(csv_bytes, metrics),
                           f"{filename}-metrik.csv", "text/csv",
                           on_click="ignore", key=f"metrics-csv-{job['id']}")


def history_page(manager):
    st.title("Riwayat")
    source = st.selectbox("Sumber", [None, *LABELS],
                          format_func=lambda key: LABELS.get(key, "Semua sumber"))
    limit = int(st.number_input("Maksimum riwayat yang ditampilkan", 1, 100_000, 200))
    jobs = manager.list_jobs(source, limit=limit)
    if not jobs:
        st.info("Belum ada pekerjaan untuk pilihan ini.")
    else:
        choices = {job["id"]: job for job in jobs}
        def option(job_id):
            job = choices[job_id]
            return (f"{LABELS[job['source']]} · {job['started']} · "
                    f"{STATUS[job['status']]} · {job['count']} baris")
        selected = st.selectbox("Buka pekerjaan", list(choices), format_func=option)
        job = choices[selected]
        show_job(manager, job)
        if job["status"] not in ACTIVE:
            with st.expander("Hapus pekerjaan ini"):
                if st.button("Konfirmasi hapus pekerjaan"):
                    manager.delete(selected)
                    st.rerun()
    with st.expander("Hapus seluruh riwayat"):
        confirm = st.checkbox("Saya ingin menghapus seluruh pekerjaan yang sudah berakhir.")
        if st.button("Hapus seluruh riwayat", disabled=not confirm):
            manager.delete()
            st.rerun()
