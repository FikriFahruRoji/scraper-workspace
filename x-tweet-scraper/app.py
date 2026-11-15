"""Antarmuka Streamlit untuk X (Twitter) Tweet Scraper.

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
    page_title="X Tweet Scraper",
    page_icon="🐦",
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

MODE_LABELS = {
    "Search": "search",
    "Profile": "profile",
    "Tweet + Reply": "tweet_reply",
    "Search + Reply": "search_reply",
}
MODE_HINTS = {
    "Search": "Cari tweet berdasarkan kata kunci atau hashtag.",
    "Profile": "Ambil tweet dari profil user tertentu.",
    "Tweet + Reply": "Ambil tweet tertentu beserta semua balasannya.",
    "Search + Reply": "Cari tweet per kata kunci beserta balasan tiap tweet.",
}

with st.sidebar:
    st.title("X Tweet Scraper")

    mode_label = st.selectbox("Ambil data dari", list(MODE_LABELS.keys()), index=0)
    mode = MODE_LABELS[mode_label]
    st.caption(MODE_HINTS[mode_label])

    # Nilai default agar variabel selalu terdefinisi.
    keyword = ""
    username = ""
    tweet_url = ""
    start_date = None
    end_date = None
    sort_label = "Terbaru"
    max_reply_expand = 10

    if mode in ("search", "search_reply"):
        keyword = st.text_input(
            "Kata kunci / hashtag",
            placeholder="mis. #pemilu2026, kecerdasan buatan",
        )
        st.markdown("**Rentang waktu**")
        col_a, col_b = st.columns(2)
        start_date = col_a.date_input(
            "Dari", value=date.today() - timedelta(days=7), max_value=date.today(), key="sd_search"
        )
        end_date = col_b.date_input(
            "Sampai", value=date.today(), max_value=date.today(), key="ed_search"
        )
        sort_label = st.radio("Urutan hasil", ["Terbaru", "Terpopuler"], horizontal=True, key="sort_search")
        if mode == "search_reply":
            max_reply_expand = st.number_input(
                "Maks. tweet yang diambil balasannya",
                min_value=1,
                max_value=50,
                value=10,
                step=1,
                help="Makin banyak makin lama dan berisiko kena rate-limit.",
            )
    elif mode == "profile":
        username = st.text_input("Username profil", placeholder="mis. elonmusk (tanpa @)")
        st.markdown("**Filter rentang waktu**")
        col_a, col_b = st.columns(2)
        start_date = col_a.date_input(
            "Dari", value=date.today() - timedelta(days=30), max_value=date.today(), key="sd_profile"
        )
        end_date = col_b.date_input(
            "Sampai", value=date.today(), max_value=date.today(), key="ed_profile"
        )
    elif mode == "tweet_reply":
        tweet_url = st.text_input(
            "URL tweet",
            placeholder="https://x.com/user/status/12345",
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
            "Waktu maksimum per tahap (menit)",
            min_value=0,
            max_value=120,
            value=0,
            step=1,
            help="0 = tanpa batas waktu (berhenti saat hasil habis).",
        )

    st.divider()

    if st.session_state.logged_in is True:
        st.success("Sesi X: **aktif** ✅")
    elif st.session_state.logged_in is False:
        st.error("Sesi X: **belum login** ❌")
    else:
        st.info("Status sesi X: belum dicek.")

    if st.button("🔑 Login / Refresh sesi", width="stretch"):
        with st.spinner(
            "Membuka browser… Silakan login ke X di jendela browser yang muncul. "
            "Setelah berhasil login, tunggu deteksi otomatis."
        ):
            res = scraper.login(real_chrome=real_chrome)
            st.session_state.logged_in = res["ok"]
        if res["ok"]:
            st.success("Login berhasil! Sesi tersimpan untuk dipakai lagi.")
        else:
            st.warning(res.get("message") or "Login gagal / dibatalkan.")

    st.divider()

    run_btn = st.button("Scraping 🚀", type="primary", width="stretch")

# --------------------------------------------------------------------------- #
# Mulai scraping (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    err_msg = None
    if mode in ("search", "search_reply") and not keyword.strip():
        err_msg = "Masukkan kata kunci/hashtag terlebih dahulu."
    elif mode == "profile" and not username.strip():
        err_msg = "Masukkan username profil terlebih dahulu."
    elif mode == "tweet_reply" and not tweet_url.strip():
        err_msg = "Masukkan URL tweet terlebih dahulu."
    elif (
        mode in ("search", "search_reply", "profile")
        and start_date
        and end_date
        and start_date > end_date
    ):
        err_msg = 'Tanggal "Dari" tidak boleh lebih besar dari "Sampai".'

    if err_msg:
        st.error(err_msg)
    else:
        # Label sumber & rentang untuk ditampilkan pada hasil.
        if mode == "search":
            source = f"Search · {keyword.strip()}"
            last_kw = keyword.strip()
        elif mode == "profile":
            source = f"Profile · @{username.strip().lstrip('@')}"
            last_kw = username.strip().lstrip("@")
        elif mode == "tweet_reply":
            source = f"Tweet + Reply · {tweet_url.strip()}"
            last_kw = "tweet_reply"
        else:
            source = f"Search + Reply · {keyword.strip()}"
            last_kw = keyword.strip()
        range_label = ""
        if mode in ("search", "search_reply", "profile") and start_date and end_date:
            range_label = f"Rentang: {start_date:%d %b %Y} – {end_date:%d %b %Y}"

        st.session_state.scrape_keyword = last_kw
        st.session_state.last_keyword = last_kw
        st.session_state.last_source = source
        st.session_state.last_range = range_label
        st.session_state.scrape_log = []
        st.session_state.scrape_running = True
        st.session_state.scrape_done = False
        st.session_state.scrape_rows = []
        st.session_state.scrape_err = None
        st.session_state.results = []

        cfg = dict(
            mode=mode,
            keyword=keyword.strip(),
            username=username.strip(),
            tweet_url=tweet_url.strip(),
            start_date=start_date,
            end_date=end_date,
            sort="latest" if sort_label == "Terbaru" else "top",
            headless=headless,
            real_chrome=real_chrome,
            wait_ms=wait_s * 1000,
            max_minutes=int(max_minutes),
            max_reply_expand=int(max_reply_expand),
        )

        def _worker():
            def log_cb(msg: str) -> None:
                st.session_state.scrape_log.append(msg)

            rows, err = scraper.scrape_tweets(
                mode=cfg["mode"],
                keyword=cfg["keyword"],
                username=cfg["username"],
                tweet_url=cfg["tweet_url"],
                start_date=cfg["start_date"],
                end_date=cfg["end_date"],
                sort=cfg["sort"],
                headless=cfg["headless"],
                real_chrome=cfg["real_chrome"],
                wait_ms=cfg["wait_ms"],
                max_minutes=cfg["max_minutes"],
                max_reply_expand=cfg["max_reply_expand"],
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
            "Belum login ke X. Klik tombol **🔑 Login / Refresh sesi** di sidebar, "
            "lalu login di jendela browser yang terbuka."
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

st.title("Hasil Scraping Tweet X")

if st.session_state.scrape_running:
    st.info("Sedang scraping… Proses berjalan, lihat log di atas.")
elif not st.session_state.results:
    st.info(
        "Pilih sumber data di sidebar, isi parameternya, lalu klik **🚀 Mulai Scraping**. "
        "Pastikan sudah login ke X terlebih dahulu."
    )
else:
    rows = st.session_state.results
    df = pd.DataFrame(rows)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Tweet", len(df))
    m2.metric("Akun unik", int(df["username"].nunique()))
    m3.metric("Total suka", int(df["FAVORITE_COUNT"].sum()))
    m4.metric("Total repost", int(df["RETWEET_COUNT"].sum()))
    m5.metric("Total tayangan", int(df["VIEW_COUNT"].sum()))

    caption = f"Sumber: **{st.session_state.last_source}**"
    if st.session_state.last_range:
        caption += f" · {st.session_state.last_range}"
    caption += " · Waktu **GMT+7**"
    st.caption(caption)

    tab_table, tab_summary = st.tabs(["📋 Tabel tweet", "📈 Ringkasan"])

    with tab_table:
        cols = [
            "waktu", "nama", "username", "VERIFIED", "text", "bahasa",
            "IN_REPLY_TO", "IN_REPLY_TO_URL", "VIEW_COUNT", "QUOTE_COUNT",
            "REPLY_COUNT", "RETWEET_COUNT", "FAVORITE_COUNT", "USER_LOCATION", "TWEET URL",
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
                "bahasa": st.column_config.TextColumn("Bahasa"),
                "IN_REPLY_TO": st.column_config.TextColumn("In Reply To"),
                "IN_REPLY_TO_URL": st.column_config.LinkColumn("In Reply To URL", display_text="buka ↗"),
                "VIEW_COUNT": st.column_config.NumberColumn("Views"),
                "QUOTE_COUNT": st.column_config.NumberColumn("Quotes"),
                "REPLY_COUNT": st.column_config.NumberColumn("Replies"),
                "RETWEET_COUNT": st.column_config.NumberColumn("Reposts"),
                "FAVORITE_COUNT": st.column_config.NumberColumn("Favorites"),
                "USER_LOCATION": st.column_config.TextColumn("Lokasi"),
                "TWEET URL": st.column_config.LinkColumn("Tweet URL", display_text="buka ↗"),
            },
        )

    with tab_summary:
        if df.empty:
            st.info("Belum ada data.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Tweet per hari")
                per_day = df.copy()
                per_day["day"] = pd.to_datetime(per_day["waktu"], errors="coerce").dt.date
                st.bar_chart(per_day.groupby("day").size())
            with c2:
                st.subheader("10 akun teratas")
                st.bar_chart(df["username"].value_counts().head(10))

    st.divider()
    st.subheader("Ekspor hasil")
    safe_kw = "".join(c if c.isalnum() else "_" for c in st.session_state.last_keyword)[:40] or "tweets"
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button(
        "⬇️ Unduh CSV",
        data=df[cols].to_csv(index=False).encode("utf-8-sig"),
        file_name=f"tweets_{safe_kw}.csv",
        mime="text/csv",
        width="stretch",
    )
    d2.download_button(
        "⬇️ Unduh JSON",
        data=json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"tweets_{safe_kw}.json",
        mime="application/json",
        width="stretch",
    )
