"""Antarmuka Streamlit untuk Threads Scraper.

Jalankan dengan:
    streamlit run app.py
"""

import importlib
import json
import threading
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

import scraper

# Streamlit menyimpan modul yang sudah di-import di memori. Reload agar
# perubahan pada scraper.py langsung terpakai saat rerun, tanpa restart server.
importlib.reload(scraper)

st.set_page_config(
    page_title="Threads Scraper",
    page_icon="🧵",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Gaya tampilan
# --------------------------------------------------------------------------- #

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.4rem; }
        div[data-testid="stMetricValue"] { font-size: 1.6rem; }
        #MainMenu, footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# State sesi
# --------------------------------------------------------------------------- #

_DEFAULTS = {
    "results": [],
    "last_source": "",
    "last_range": "",
    "last_keyword": "",
    "logged_in": None,
    "scrape_keyword": "",
    "scrape_log": [],
    "scrape_running": False,
    "scrape_done": False,
    "scrape_rows": [],
    "scrape_err": None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# --------------------------------------------------------------------------- #
# Sidebar — kontrol
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.title("🧵 Threads Scraper")
    st.caption("Cari post publik Threads berdasarkan kata kunci dan rentang waktu.")

    keyword = st.text_input(
        "Kata kunci",
        placeholder="mis. kecerdasan buatan, #AI, resep rendang",
        help="Bisa berupa kata, frasa, atau hashtag.",
    )

    date_range = st.date_input(
        "Rentang waktu (GMT+7, inklusif)",
        value=(date.today() - timedelta(days=7), date.today()),
        max_value=date.today(),
        key="date_range_search",
        help="Klik tanggal awal, lalu tanggal akhir.",
    )
    if isinstance(date_range, (tuple, list)):
        start_date = date_range[0] if len(date_range) > 0 else None
        end_date = date_range[1] if len(date_range) > 1 else start_date
    else:
        start_date = end_date = date_range

    sort_label = st.radio(
        "Urutan hasil",
        ["Terbaru", "Terpopuler"],
        horizontal=True,
        help=(
            "Terbaru meminta Threads mengurutkan dari yang paling baru; "
            "hasil tetap difilter ulang sesuai rentang tanggal."
        ),
    )

    with st.expander("Opsi lanjutan", expanded=False):
        headless = st.checkbox("Mode headless (tanpa jendela browser)", value=True)
        real_chrome = st.checkbox(
            "Gunakan Google Chrome asli",
            value=True,
            help="Lebih sulit terdeteksi. Nonaktifkan jika Chrome gagal dibuka.",
        )
        wait_s = st.slider("Jeda antar-scroll (detik)", min_value=1, max_value=5, value=2)
        max_minutes = st.number_input(
            "Waktu maksimum (menit)",
            min_value=0,
            max_value=120,
            value=10,
            step=1,
            help="0 = tanpa batas waktu (berhenti saat hasil habis).",
        )
        max_posts = st.number_input(
            "Maksimum post (0 = tanpa batas)",
            min_value=0,
            max_value=5000,
            value=0,
            step=50,
            help="Batas pengaman agar tidak menarik terlalu banyak data.",
        )

    st.divider()

    if st.session_state.logged_in is True:
        st.success("Sesi Threads: **aktif** ✅")
    elif st.session_state.logged_in is False:
        st.error("Sesi Threads: **belum login** ❌")
    else:
        st.info("Status sesi Threads: belum dicek.")

    col_login, col_check = st.columns(2)
    login_clicked = col_login.button("🔑 Login", width="stretch")
    check_clicked = col_check.button("🔍 Cek sesi", width="stretch")

    if login_clicked:
        with st.spinner(
            "Membuka browser… Silakan login ke Threads (atau lanjutkan dengan "
            "Instagram) di jendela browser yang muncul. Tunggu deteksi otomatis."
        ):
            res = scraper.login(real_chrome=real_chrome)
            st.session_state.logged_in = res["ok"]
        if res["ok"]:
            st.success("Login berhasil! Sesi tersimpan untuk dipakai lagi.")
        else:
            st.warning(res.get("message") or "Login gagal / dibatalkan.")

    if check_clicked:
        with st.spinner("Membuka browser headless untuk memeriksa sesi…"):
            ok = scraper.is_logged_in(real_chrome=real_chrome)
            st.session_state.logged_in = ok
        if ok:
            st.success("Sesi Threads aktif.")
        else:
            st.warning("Belum ada sesi Threads yang tersimpan.")

    st.divider()

    run_btn = st.button("Scraping 🚀", type="primary", width="stretch")

    st.caption(
        "⚠️ Gunakan hanya untuk konten publik yang Anda berhak akses. "
        "Threads membatasi pengumpulan otomatis; pastikan Anda memiliki izin "
        "dari Meta/Threads sebelum menarik data."
    )

# --------------------------------------------------------------------------- #
# Mulai scraping (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    err_msg = None
    if not keyword.strip():
        err_msg = "Masukkan kata kunci terlebih dahulu."
    elif start_date and end_date and start_date > end_date:
        err_msg = 'Tanggal "Dari" tidak boleh lebih besar dari "Sampai".'

    if err_msg:
        st.error(err_msg)
    else:
        source = f"Search · {keyword.strip()}"
        range_label = f"Rentang: {start_date:%d %b %Y} – {end_date:%d %b %Y}"

        st.session_state.scrape_keyword = keyword.strip()
        st.session_state.last_keyword = keyword.strip()
        st.session_state.last_source = source
        st.session_state.last_range = range_label
        st.session_state.scrape_log = []
        st.session_state.scrape_running = True
        st.session_state.scrape_done = False
        st.session_state.scrape_rows = []
        st.session_state.scrape_err = None
        st.session_state.results = []

        cfg = dict(
            keyword=keyword.strip(),
            start_date=start_date,
            end_date=end_date,
            sort="recent" if sort_label == "Terbaru" else "top",
            headless=headless,
            real_chrome=real_chrome,
            wait_ms=wait_s * 1000,
            max_minutes=int(max_minutes),
            max_posts=int(max_posts),
        )

        def _worker():
            def log_cb(msg: str) -> None:
                st.session_state.scrape_log.append(msg)

            rows, err = scraper.scrape_posts(
                keyword=cfg["keyword"],
                start_date=cfg["start_date"],
                end_date=cfg["end_date"],
                sort=cfg["sort"],
                headless=cfg["headless"],
                real_chrome=cfg["real_chrome"],
                wait_ms=cfg["wait_ms"],
                max_minutes=cfg["max_minutes"],
                max_posts=cfg["max_posts"],
                log_cb=log_cb,
            )
            st.session_state.scrape_rows = rows
            st.session_state.scrape_err = err
            st.session_state.scrape_running = False
            st.session_state.scrape_done = True

        thread = threading.Thread(target=_worker, daemon=True)
        ctx = get_script_run_ctx()
        if ctx is not None:
            add_script_run_ctx(thread, ctx)
        thread.start()

# Finalisasi hasil ketika worker selesai (dijalankan saat full rerun).
if st.session_state.scrape_done and not st.session_state.scrape_running:
    rows = st.session_state.scrape_rows
    err = st.session_state.scrape_err
    st.session_state.scrape_done = False
    if err == "not_logged_in":
        st.session_state.logged_in = False
        st.session_state.results = []
        st.error(
            "Belum login ke Threads dan tidak ada post yang bisa diambil. Klik "
            "**🔑 Login / Refresh sesi** di sidebar, lalu login di jendela browser "
            "yang terbuka."
        )
    elif err == "logged_out_limited":
        st.session_state.logged_in = False
        st.session_state.results = rows
        st.warning(
            "Hasil **dibatasi karena belum login**. Threads hanya menampilkan "
            "sekitar 24 post pertama untuk pengunjung. Login lewat sidebar untuk "
            "mendapatkan hasil yang lebih banyak."
        )
    elif err:
        st.session_state.results = rows
        st.warning(f"Selesai dengan catatan: {err}")
    else:
        st.session_state.results = rows

# --------------------------------------------------------------------------- #
# Log live (fragment yang me-refresh otomatis)
# --------------------------------------------------------------------------- #


@st.fragment(run_every="0.5s")
def _live_log() -> None:
    if st.session_state.scrape_running:
        st.subheader("📄 Log scraping")
        st.code("\n".join(st.session_state.scrape_log) or "Memulai scraping…", language=None)
    elif st.session_state.scrape_done:
        st.rerun(scope="app")


if st.session_state.scrape_running or st.session_state.scrape_done:
    _live_log()

# --------------------------------------------------------------------------- #
# Area utama — hasil
# --------------------------------------------------------------------------- #

st.title("Hasil Scraping Threads")

if st.session_state.scrape_running:
    st.info("Sedang scraping… Proses berjalan, lihat log di atas.")
elif not st.session_state.results:
    st.info(
        "Isi **kata kunci** dan **rentang waktu** di sidebar, lalu klik "
        "**🚀 Scraping**. Sangat disarankan login terlebih dahulu agar hasil "
        "tidak dibatasi Threads."
    )
    st.caption(
        "Threads tidak menyediakan operator tanggal resmi. Rentang waktu "
        "diterapkan pada hasil yang terkumpul (berdasarkan waktu post, GMT+7)."
    )
else:
    rows = st.session_state.results
    df = pd.DataFrame(rows)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Post", f"{len(df):,}".replace(",", "."))
    m2.metric("Akun unik", f"{int(df['username'].nunique()):,}".replace(",", "."))
    m3.metric("Total suka", f"{int(df['LIKE_COUNT'].sum()):,}".replace(",", "."))
    m4.metric("Total balasan", f"{int(df['REPLY_COUNT'].sum()):,}".replace(",", "."))
    m5.metric("Total repost", f"{int(df['REPOST_COUNT'].sum()):,}".replace(",", "."))

    caption = f"Sumber: **{st.session_state.last_source}**"
    if st.session_state.last_range:
        caption += f" · {st.session_state.last_range}"
    caption += " · Waktu **GMT+7**"
    st.caption(caption)

    tab_table, tab_summary = st.tabs(["📋 Tabel post", "📈 Ringkasan"])

    with tab_table:
        cols = [
            "waktu", "nama", "username", "VERIFIED", "text",
            "MEDIA", "LIKE_COUNT", "REPLY_COUNT", "REPOST_COUNT",
            "QUOTE_COUNT", "IN_REPLY_TO", "USER_BIO", "POST URL",
        ]
        st.dataframe(
            df[cols],
            width="stretch",
            height=560,
            hide_index=True,
            column_config={
                "waktu": st.column_config.TextColumn("Waktu (GMT+7)"),
                "nama": st.column_config.TextColumn("Nama"),
                "username": st.column_config.TextColumn("Username"),
                "VERIFIED": st.column_config.CheckboxColumn("Verified"),
                "text": st.column_config.TextColumn("Text", width="large"),
                "MEDIA": st.column_config.TextColumn("Media"),
                "LIKE_COUNT": st.column_config.NumberColumn("Suka"),
                "REPLY_COUNT": st.column_config.NumberColumn("Balasan"),
                "REPOST_COUNT": st.column_config.NumberColumn("Repost"),
                "QUOTE_COUNT": st.column_config.NumberColumn("Kutipan"),
                "IN_REPLY_TO": st.column_config.TextColumn("Balasan untuk"),
                "USER_BIO": st.column_config.TextColumn("Bio", width="medium"),
                "POST URL": st.column_config.LinkColumn("Post URL", display_text="buka ↗"),
            },
        )

    with tab_summary:
        if df.empty:
            st.info("Belum ada data.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Post per hari")
                per_day = df.copy()
                per_day["day"] = (
                    pd.to_datetime(per_day["waktu"], errors="coerce", utc=True)
                    .dt.tz_convert("Asia/Jakarta")
                    .dt.date
                )
                st.bar_chart(per_day.groupby("day").size())
            with c2:
                st.subheader("10 akun teratas")
                st.bar_chart(df["username"].value_counts().head(10))

            st.subheader("Komposisi media")
            st.bar_chart(df["MEDIA"].value_counts())

    st.divider()
    st.subheader("Ekspor hasil")
    safe_kw = (
        "".join(c if c.isalnum() else "_" for c in st.session_state.last_keyword)[:40]
        or "threads"
    )
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "⬇️ Unduh CSV",
        data=df[cols].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"threads_{safe_kw}.csv",
        mime="text/csv",
        width="stretch",
    )
    d2.download_button(
        "⬇️ Unduh JSON",
        data=json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"threads_{safe_kw}.json",
        mime="application/json",
        width="stretch",
    )
