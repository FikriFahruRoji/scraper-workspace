"""Antarmuka Streamlit untuk Play Store Review Scraper.

Jalankan dengan:
    streamlit run app.py
"""

import html
import importlib
import re
import threading
import time

import pandas as pd
import streamlit as st

import scraper

# Streamlit menyimpan modul yang sudah di-import di memori. Reload agar
# perubahan pada scraper.py langsung terpakai saat rerun, tanpa restart server.
importlib.reload(scraper)

st.set_page_config(
    page_title="Play Store Review Scraper",
    page_icon="▶️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------- #
# Gaya tampilan
# --------------------------------------------------------------------------- #

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.2rem; max-width: 1500px; }
        div[data-testid="stMetricValue"] { font-size: 1.55rem; }
        #MainMenu, footer { visibility: hidden; }
        [data-testid="stToolbar"] { display: none; }
        .ps-hero {
            border: 1px solid #dcefe6;
            border-radius: 18px;
            padding: 1.4rem 1.6rem;
            background: linear-gradient(135deg, #f5fcf8 0%, #e9f7f0 100%);
            margin-bottom: 1rem;
        }
        .ps-hero h2 { margin: 0 0 .35rem 0; font-size: 1.55rem; }
        .ps-hero p { margin: 0; color: #4b5563; }
        .ps-step {
            border: 1px solid #e7ebf1;
            border-radius: 14px;
            padding: .9rem 1rem;
            background: #fff;
            min-height: 112px;
        }
        .ps-step b { color: #01875f; }
        .ps-muted { color: #6b7280; font-size: .9rem; }
        .ps-badge {
            display: inline-block;
            padding: .18rem .55rem;
            border-radius: 999px;
            background: #e8f5ee;
            color: #016b4a;
            font-size: .78rem;
            font-weight: 600;
        }
        .ps-app-name { margin: 0; font-size: 1.7rem; line-height: 1.2; }
        .ps-app-dev { color: #4b5563; margin-top: .15rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# Konstanta UI
# --------------------------------------------------------------------------- #

LOCALES = {
    "Indonesia (id / ID)": ("id", "id"),
    "English – United States (en / US)": ("en", "us"),
    "English – United Kingdom (en / GB)": ("en", "gb"),
    "English – India (en / IN)": ("en", "in"),
    "English – Singapore (en / SG)": ("en", "sg"),
    "Melayu (ms / MY)": ("ms", "my"),
    "Filipino (tl / PH)": ("tl", "ph"),
    "Thai (th / TH)": ("th", "th"),
    "Vietnam (vi / VN)": ("vi", "vn"),
    "Jepang (ja / JP)": ("ja", "jp"),
    "Korea (ko / KR)": ("ko", "kr"),
    "Mandarin Sederhana (zh / CN)": ("zh", "cn"),
    "Mandarin Tradisional (zh / TW)": ("zh", "tw"),
    "Hindi (hi / IN)": ("hi", "in"),
    "Arab (ar / SA)": ("ar", "sa"),
    "Spanyol (es / ES)": ("es", "es"),
    "Portugis – Brasil (pt / BR)": ("pt", "br"),
    "Prancis (fr / FR)": ("fr", "fr"),
    "Jerman (de / DE)": ("de", "de"),
    "Belanda (nl / NL)": ("nl", "nl"),
    "Turki (tr / TR)": ("tr", "tr"),
    "Rusia (ru / RU)": ("ru", "ru"),
}

SORTS = {
    "Terbaru": "terbaru",
    "Paling relevan": "relevan",
    "Rating": "rating",
}

TABLE_SORTS = [
    "Urutan Play Store",
    "Rating tertinggi",
    "Rating terendah",
    "Tanggal terbaru",
    "Tanggal terlama",
    "Paling disukai",
]

# --------------------------------------------------------------------------- #
# State sesi
# --------------------------------------------------------------------------- #

_DEFAULTS = {
    "result": None,
    "last_input": "",
    "job": None,
    "job_finalized": False,
}
for _key, _value in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _value


class ScrapeJob:
    """Wadah thread-safe untuk log, progres, dan hasil scraping.

    Worker thread TIDAK boleh menyentuh ``st.session_state``. Semua komunikasi
    dilakukan lewat objek biasa + lock ini.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[str] = []
        self._progress: dict = {}
        self._result: dict | None = None
        self._running = True
        self._finished_at: float | None = None
        self.started_at = time.time()
        self.stop_event = threading.Event()

    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(str(message))

    def set_progress(self, data: dict) -> None:
        with self._lock:
            self._progress = dict(data)

    def finish(self, result: dict) -> None:
        with self._lock:
            self._result = result
            self._running = False
            self._finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "logs": list(self._logs),
                "progress": dict(self._progress),
                "result": self._result,
                "running": self._running,
                "elapsed": (self._finished_at or time.time()) - self.started_at,
            }


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _fmt_rating(value) -> str:
    try:
        return f"{float(value):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return (value or "playstore")[:60]


# Finalisasi dilakukan di thread utama Streamlit, bukan di worker, dan sebelum
# sidebar dirender agar status proses ikut terbarui.
_job = st.session_state.get("job")
_job_snap = _job.snapshot() if _job is not None else None
if (
    _job_snap
    and not _job_snap["running"]
    and not st.session_state.get("job_finalized")
):
    st.session_state.result = _job_snap["result"]
    st.session_state.job_finalized = True
    _job_snap = _job.snapshot()


# --------------------------------------------------------------------------- #
# Sidebar — kontrol
# --------------------------------------------------------------------------- #

with st.sidebar:
    _active_job = st.session_state.get("job")
    _job_running = bool(_active_job and _active_job.snapshot()["running"])

    st.title("▶️ Play Store Scraper")
    st.caption(
        "Ambil komentar publik aplikasi Android hanya dengan kode aplikasi."
    )

    app_input = st.text_input(
        "Kode aplikasi atau link Play Store",
        placeholder="com.whatsapp",
        help=(
            "Kode aplikasi adalah bagian setelah ?id= pada URL Play Store. "
            "Contoh: com.whatsapp, com.instagram.android, "
            "id.co.bankmandiri.mandirionline. Link Play Store juga diterima."
        ),
        disabled=_job_running,
    )
    st.caption(
        "💡 Salin kode dari URL Play Store — bagian setelah `?id=`. "
        "Link lengkap juga bisa ditempel."
    )

    with st.expander("Opsi lanjutan", expanded=False):
        locale_label = st.selectbox(
            "Bahasa & negara toko",
            list(LOCALES.keys()),
            index=0,
            disabled=_job_running,
            help=(
                "Menentukan toko Play Store yang dibaca. Ulasan yang tampil "
                "bisa berbeda antar-negara."
            ),
        )
        sort_label = st.selectbox(
            "Urutan dari Play Store",
            list(SORTS.keys()),
            index=0,
            disabled=_job_running,
            help="Urutan awal saat ulasan diambil dari Play Store.",
        )
        score_label = st.selectbox(
            "Filter bintang",
            ["Semua bintang", "5 ★", "4 ★", "3 ★", "2 ★", "1 ★"],
            index=0,
            disabled=_job_running,
            help="Filter dilakukan oleh endpoint Play Store, bukan setelah unduh.",
        )
        count = st.number_input(
            "Jumlah ulasan (0 = semua)",
            min_value=0,
            value=0,
            step=100,
            disabled=_job_running,
            help=(
                "0 = ambil SEMUA ulasan yang tersedia, tanpa batas jumlah dari "
                "sisi aplikasi. Isi angka hanya bila ingin berhenti setelah "
                "jumlah tertentu."
            ),
        )
        only_text = st.checkbox(
            "Hanya ulasan yang memiliki teks",
            value=True,
            disabled=_job_running,
            help=(
                "Play Store juga mengembalikan rating tanpa komentar. Aktifkan "
                "opsi ini bila hanya ingin komentar yang berisi teks."
            ),
        )
        delay = st.slider(
            "Jeda antar-batch (detik)",
            min_value=0.2,
            max_value=3.0,
            value=0.6,
            step=0.2,
            disabled=_job_running,
            help="Jeda sopan antar permintaan agar tidak membebani Play Store.",
        )
        max_minutes = st.number_input(
            "Batas waktu (menit, 0 = tanpa batas)",
            min_value=0,
            value=0,
            step=5,
            disabled=_job_running,
            help=(
                "0 = tanpa batas waktu. Isi angka bila ingin proses berhenti "
                "otomatis setelah durasi tertentu."
            ),
        )

    st.divider()

    run_btn = st.button(
        "Scraping 🚀",
        type="primary",
        width="stretch",
        disabled=_job_running,
        help="Tunggu proses yang sedang berjalan selesai." if _job_running else None,
    )

    if _job_running:
        if st.button("⏹️ Hentikan", width="stretch"):
            _active_job.stop_event.set()
            st.toast("Permintaan berhenti dikirim…")

    st.caption(
        "⚠️ Gunakan hanya untuk ulasan publik yang Anda berhak akses. Patuhi "
        "Ketentuan Layanan Google Play dan jangan kumpulkan data pribadi tanpa "
        "dasar yang sah."
    )

    with st.expander("ℹ️ Tentang scraper", expanded=False):
        st.markdown(
            """
            Aplikasi ini memakai pustaka **`google-play-scraper`**, pendekatan
            yang sama dengan script unofficial
            [`Google-Play-Store-Review-Extractor`](https://github.com/amrrs/Google-Play-Store-Review-Extractor)
            di GitHub. Data diambil lewat permintaan HTTP ke endpoint publik
            Play Store — **bukan API resmi Google**.

            - Tidak ada login, CAPTCHA, atau proxy.
            - Google dapat mengubah endpoint atau membatasi akses kapan saja.
            - Hasil tidak dijamin lengkap; untuk kebutuhan produksi gunakan
              layanan/API resmi yang sesuai.
            """
        )

# --------------------------------------------------------------------------- #
# Mulai scraping (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    try:
        normalized_app = scraper.normalize_app_input(app_input)
        validation_error = None
    except ValueError as exc:
        normalized_app = ""
        validation_error = str(exc)

    if validation_error:
        st.error(validation_error)
    else:
        lang, country = LOCALES[locale_label]
        score_value = 0 if score_label == "Semua bintang" else int(score_label[0])

        job = ScrapeJob()
        st.session_state.last_input = normalized_app
        st.session_state.result = None
        st.session_state.job = job
        st.session_state.job_finalized = False

        cfg = {
            "app_id": normalized_app,
            "count": int(count),
            "lang": lang,
            "country": country,
            "sort": SORTS[sort_label],
            "score": score_value or None,
            "only_with_text": bool(only_text),
            "delay": float(delay),
            "max_minutes": int(max_minutes),
        }

        def _worker() -> None:
            try:
                result = scraper.scrape_reviews(
                    cfg["app_id"],
                    count=cfg["count"],
                    lang=cfg["lang"],
                    country=cfg["country"],
                    sort=cfg["sort"],
                    score=cfg["score"],
                    only_with_text=cfg["only_with_text"],
                    delay=cfg["delay"],
                    max_minutes=cfg["max_minutes"],
                    stop_event=job.stop_event,
                    log_cb=job.log,
                    progress_cb=job.set_progress,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "app": {},
                    "rows": [],
                    "error": "worker_error",
                    "message": f"Terjadi kesalahan di background worker: {exc}",
                    "stop_reason": "gagal",
                    "requested": cfg["count"],
                    "lang": cfg["lang"],
                    "country": cfg["country"],
                }
            job.finish(result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

# --------------------------------------------------------------------------- #
# Finalisasi hasil + log live
# --------------------------------------------------------------------------- #


@st.fragment(run_every="0.5s")
def _live_log() -> None:
    job = st.session_state.get("job")
    if job is None:
        return
    snap = job.snapshot()
    if snap["running"]:
        progress = snap["progress"] or {}
        collected = int(progress.get("collected") or 0)
        target = int(progress.get("target") or 0)
        with st.container(border=True):
            head_left, head_right = st.columns([3, 1])
            head_left.markdown("**📄 Log scraping**")
            head_right.caption(f"{snap['elapsed']:.0f} detik")
            if target:
                st.progress(
                    min(collected / target, 1.0),
                    text=f"{_fmt_int(collected)} / {_fmt_int(target)} ulasan",
                )
            else:
                st.caption(f"{_fmt_int(collected)} ulasan terkumpul…")
            st.code("\n".join(snap["logs"][-14:]) or "Memulai…", language=None)
        return

    # Memicu full rerun; blok log lama akan hilang setelah finalisasi.
    st.rerun(scope="app")


_current_job = st.session_state.get("job")
_current_snap = _current_job.snapshot() if _current_job is not None else None
if _current_snap and not st.session_state.get("job_finalized"):
    _live_log()

# --------------------------------------------------------------------------- #
# Area utama
# --------------------------------------------------------------------------- #

st.title("Ulasan aplikasi Google Play")

result = st.session_state.result
job = st.session_state.get("job")
job_snap = job.snapshot() if job is not None else None

if job_snap and job_snap["running"]:
    st.info(
        "Sedang scraping… Proses berjalan di latar belakang dan halaman ini "
        "diperbarui otomatis saat selesai. Log progres tampil di atas."
    )
    st.stop()

if not isinstance(result, dict):
    st.markdown(
        """
        <div class="ps-hero">
            <h2>Dari kode aplikasi menjadi data ulasan</h2>
            <p>Masukkan kode aplikasi Play Store, lalu ambil komentar publik,
            rating, tanggal, jumlah suka, dan balasan developer — rapi dalam
            tabel serta CSV/JSON.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        '<div class="ps-step"><b>1 · Salin kode aplikasi</b><br>Buka aplikasi di '
        'Play Store, lihat URL, lalu salin bagian setelah <code>?id=</code>.</div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        '<div class="ps-step"><b>2 · Atur opsi</b><br>Pilih bahasa/negara toko, '
        'urutan, jumlah ulasan, dan filter bintang bila perlu.</div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        '<div class="ps-step"><b>3 · Scraping</b><br>Klik <b>Scraping 🚀</b>, '
        'pantau log, lalu saring dan unduh hasilnya.</div>',
        unsafe_allow_html=True,
    )

    st.markdown("")
    left, right = st.columns([1.05, 1])

    with left:
        st.subheader("Contoh kode aplikasi")
        examples = pd.DataFrame(
            [
                ("WhatsApp Messenger", "com.whatsapp"),
                ("Instagram", "com.instagram.android"),
                ("Tokopedia", "com.tokopedia.tkpd"),
                ("Gojek", "com.gojek.app"),
                ("Spotify", "com.spotify.music"),
            ],
            columns=["Aplikasi", "Kode aplikasi"],
        )
        st.dataframe(examples, hide_index=True, width="stretch")

    with right:
        st.subheader("Yang perlu diketahui")
        st.markdown(
            f"""
            <span class="ps-badge">UNOFFICIAL</span>

            Scraper memakai pendekatan script **Google-Play-Store-Review-Extractor**
            (`google-play-scraper`) — permintaan HTTP ke endpoint publik Play
            Store.

            - Tanpa login, melalui browser stealth, tanpa API key.
            - **Tanpa batas jumlah** dari sisi aplikasi: ambil semua ulasan
              yang tersedia sampai feed habis atau dihentikan.
            - Jumlah ulasan yang tersedia tetap ditentukan Google.
            - Ulasan berbahasa sesuai toko yang dipilih.
            - Hanya untuk ulasan publik; patuhi ToS Google Play.
            """,
            unsafe_allow_html=True,
        )

    st.info(
        "Coba mulai dengan **com.tokopedia.tkpd** dan toko **Indonesia**. "
        "Biarkan jumlah ulasan **0** untuk mengambil semua yang tersedia, atau "
        "isi angka tertentu untuk uji cepat. Aplikasi populer dapat memiliki "
        "jutaan ulasan, jadi proses “semua” bisa berlangsung lama."
    )
    st.stop()

# ---- Error / hasil kosong -------------------------------------------------- #

rows = result.get("rows") or []
app_info = result.get("app") or {}
error = result.get("error")
message = result.get("message") or ""
stop_reason = result.get("stop_reason") or ""

if job_snap and not job_snap["running"] and job_snap["logs"]:
    with st.expander(f"📄 Log scraping · {job_snap['elapsed']:.1f} detik"):
        st.code("\n".join(job_snap["logs"]), language=None)

if error in ("app_not_found", "app_error", "fetch_error", "worker_error"):
    st.error(message or "Scraping gagal.")
    with st.expander("Detail teknis"):
        st.json({k: v for k, v in result.items() if k != "rows"})
    st.stop()

if error == "no_reviews" or (not rows and not error):
    st.warning("Tidak ada ulasan yang terbaca.")
    st.markdown(message or "Coba ubah bahasa/negara, filter, atau jumlah ulasan.")
    with st.expander("Detail teknis"):
        st.json({k: v for k, v in result.items() if k != "rows"})
    st.stop()

# ---- Header aplikasi ------------------------------------------------------- #

app_name = app_info.get("nama") or st.session_state.get("last_input") or "Aplikasi"
app_link = app_info.get("url") or scraper.app_url(
    app_info.get("id", ""), result.get("lang", "id"), result.get("country", "id")
)

head_icon, head_text, head_action = st.columns([1, 7, 2], vertical_alignment="center")
if app_info.get("icon"):
    head_icon.image(app_info["icon"], width=88)
with head_text:
    st.markdown(
        f'<h2 class="ps-app-name">{html.escape(app_name)}</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div class="ps-app-dev">{html.escape(app_info.get("developer") or "—")}</div>',
        unsafe_allow_html=True,
    )
    rating_bits = []
    if app_info.get("rating"):
        rating_bits.append(f"⭐ {_fmt_rating(app_info['rating'])}")
    if app_info.get("jumlah_rating"):
        rating_bits.append(f"{_fmt_int(app_info['jumlah_rating'])} rating")
    if app_info.get("genre"):
        rating_bits.append(app_info["genre"])
    if app_info.get("instalasi"):
        rating_bits.append(f"{app_info['instalasi']} unduhan")
    if rating_bits:
        st.caption(" · ".join(rating_bits))
with head_action:
    st.link_button("Buka di Play Store ↗", app_link, width="stretch")

if error == "partial":
    st.warning(message)
elif stop_reason == "dibatalkan":
    st.info("Proses dihentikan. Menampilkan hasil yang sempat terkumpul.")
elif stop_reason == "waktu":
    st.warning(
        "Batas waktu tercapai. Hasil di bawah adalah sebagian dari yang "
        "sempat diambil; kosongkan atau naikkan **Batas waktu** bila perlu."
    )

if result.get("only_with_text"):
    st.caption("Hanya ulasan yang memiliki teks komentar yang ditampilkan.")
if result.get("score_filter"):
    st.caption(f"Filter bintang aktif: {result['score_filter']} ★")

# ---- Metrik --------------------------------------------------------------- #

df = pd.DataFrame(rows)
if df.empty:
    st.info("Tidak ada baris ulasan untuk ditampilkan.")
    st.stop()

df["rating"] = pd.to_numeric(df.get("rating"), errors="coerce")
for numeric_col in ("suka",):
    if numeric_col in df:
        df[numeric_col] = pd.to_numeric(df[numeric_col], errors="coerce").fillna(0).astype(int)

avg_rating = df["rating"].mean()
unique_users = df["nama"].nunique()
total_likes = int(df["suka"].sum()) if "suka" in df else 0
replied_pct = (
    df["balasan"].fillna("").str.strip().ne("").mean() * 100
    if "balasan" in df
    else 0
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Ulasan terkumpul", _fmt_int(len(df)))
m2.metric("Rating rata-rata", _fmt_rating(avg_rating) if pd.notna(avg_rating) else "—")
m3.metric("Pengguna unik", _fmt_int(unique_users))
m4.metric("Total suka", _fmt_int(total_likes))
m5.metric("Dibalas developer", f"{replied_pct:.0f}%")

if app_info.get("jumlah_ulasan"):
    st.caption(
        f"Play Store mencantumkan **{_fmt_int(app_info['jumlah_ulasan'])} ulasan** "
        f"dan **{_fmt_int(app_info.get('jumlah_rating'))} rating** untuk aplikasi "
        f"ini (angka rating termasuk rating tanpa teks)."
    )

# ---- Filter --------------------------------------------------------------- #

with st.expander("🔎 Filter & urutan", expanded=True):
    f1, f2, f3, f4 = st.columns([1.1, 2, 1.5, 1])
    selected_ratings = f1.multiselect(
        "Bintang",
        [5, 4, 3, 2, 1],
        default=[],
        placeholder="Semua bintang",
    )
    keyword = f2.text_input(
        "Cari kata di ulasan / nama",
        placeholder="mis. lambat, login, iklan, bagus",
    )
    table_sort = f3.selectbox("Urutkan tabel", TABLE_SORTS, index=0)
    only_replied = f4.checkbox("Hanya dibalas", value=False)

filtered = df.copy()
if selected_ratings:
    filtered = filtered[filtered["rating"].isin(selected_ratings)]
if keyword.strip():
    needle = keyword.strip().lower()
    mask = (
        filtered["ulasan"].fillna("").str.lower().str.contains(needle, regex=False)
        | filtered["nama"].fillna("").str.lower().str.contains(needle, regex=False)
    )
    filtered = filtered[mask]
if only_replied:
    filtered = filtered[filtered["balasan"].fillna("").str.strip().ne("")]

if table_sort == "Rating tertinggi":
    filtered = filtered.sort_values(["rating", "suka"], ascending=[False, False])
elif table_sort == "Rating terendah":
    filtered = filtered.sort_values(["rating", "suka"], ascending=[True, False])
elif table_sort in ("Tanggal terbaru", "Tanggal terlama"):
    filtered = filtered.assign(_tanggal=pd.to_datetime(filtered["tanggal"], errors="coerce"))
    filtered = filtered.sort_values(
        "_tanggal",
        ascending=table_sort == "Tanggal terlama",
        na_position="last",
    )
elif table_sort == "Paling disukai":
    filtered = filtered.sort_values("suka", ascending=False)

st.caption(
    f"Menampilkan **{_fmt_int(len(filtered))}** dari **{_fmt_int(len(df))}** "
    "ulasan terkumpul."
)

# ---- Tabel & ringkasan ---------------------------------------------------- #

tab_table, tab_summary, tab_info = st.tabs(
    ["📋 Tabel ulasan", "📈 Ringkasan", "ℹ️ Info aplikasi"]
)

with tab_table:
    if filtered.empty:
        st.info("Tidak ada ulasan yang cocok dengan filter.")
    else:
        cols = [
            "foto_profil",
            "nama",
            "rating",
            "tanggal",
            "ulasan",
            "suka",
            "versi_aplikasi",
            "balasan",
            "tanggal_balasan",
        ]
        cols = [c for c in cols if c in filtered.columns]
        st.dataframe(
            filtered[cols],
            width="stretch",
            height=580,
            hide_index=True,
            column_config={
                "foto_profil": st.column_config.ImageColumn("Foto", width="small"),
                "nama": st.column_config.TextColumn("Nama"),
                "rating": st.column_config.NumberColumn("Bintang", format="%.0f ★"),
                "tanggal": st.column_config.TextColumn("Tanggal"),
                "ulasan": st.column_config.TextColumn("Ulasan", width="large"),
                "suka": st.column_config.NumberColumn("Suka"),
                "versi_aplikasi": st.column_config.TextColumn("Versi app"),
                "balasan": st.column_config.TextColumn(
                    "Balasan developer", width="medium"
                ),
                "tanggal_balasan": st.column_config.TextColumn("Tanggal balasan"),
            },
        )

with tab_summary:
    if filtered.empty:
        st.info("Belum ada data untuk diringkas.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Distribusi bintang")
            dist = (
                filtered["rating"]
                .dropna()
                .astype(int)
                .value_counts()
                .sort_index()
                .reindex([1, 2, 3, 4, 5], fill_value=0)
            )
            dist.index = [f"{i} ★" for i in dist.index]
            st.bar_chart(dist, color="#01875f")

        with c2:
            st.subheader("Ulasan per bulan")
            dates = pd.to_datetime(filtered["tanggal"], errors="coerce")
            monthly = (
                dates.dropna()
                .dt.to_period("M")
                .astype(str)
                .value_counts()
                .sort_index()
            )
            if monthly.empty:
                st.info("Tanggal ulasan tidak tersedia.")
            else:
                st.bar_chart(monthly, color="#4285f4")

        c3, c4 = st.columns(2)
        with c3:
            st.subheader("Persentase dibalas developer")
            reply_by_star = (
                filtered.assign(
                    _dibalas=filtered["balasan"].fillna("").str.strip().ne("")
                )
                .groupby("rating")["_dibalas"]
                .mean()
                .mul(100)
                .reindex([1, 2, 3, 4, 5])
            )
            reply_by_star.index = [f"{i} ★" for i in reply_by_star.index]
            st.bar_chart(reply_by_star.fillna(0), color="#fbbc04")

        with c4:
            st.subheader("10 ulasan paling disukai")
            top = (
                filtered[filtered["suka"] > 0]
                .nlargest(10, "suka")
                .set_index("nama")["suka"]
            )
            if top.empty:
                st.info("Belum ada ulasan yang mendapat suka.")
            else:
                st.bar_chart(top, horizontal=True, color="#ea4335")

        st.caption(
            "Tanggal mengikuti data Play Store. Grafik bulanan membantu melihat "
            "sebaran waktu, bukan tanggal resmi yang dijamin akurat."
        )

with tab_info:
    if not app_info:
        st.info("Metadata aplikasi tidak tersedia.")
    else:
        i1, i2, i3, i4 = st.columns(4)
        i1.markdown(f"**Developer**  \n{app_info.get('developer') or '—'}")
        i2.markdown(f"**Genre**  \n{app_info.get('genre') or '—'}")
        i3.markdown(f"**Versi**  \n{app_info.get('versi') or '—'}")
        i4.markdown(f"**Diperbarui**  \n{app_info.get('diperbarui') or '—'}")

        j1, j2, j3, j4 = st.columns(4)
        j1.markdown(f"**Unduhan**  \n{app_info.get('instalasi') or '—'}")
        j2.markdown(f"**Rating**  \n⭐ {_fmt_rating(app_info.get('rating'))}")
        j3.markdown(f"**Jumlah rating**  \n{_fmt_int(app_info.get('jumlah_rating'))}")
        j4.markdown(
            f"**Iklan / IAP**  \n"
            f"{'Ada iklan' if app_info.get('ada_iklan') else 'Tanpa iklan'} · "
            f"{'Ada IAP' if app_info.get('pembelian_dalam_aplikasi') else 'Tanpa IAP'}"
        )

        st.markdown(f"**Kode aplikasi:** `{app_info.get('id')}`")
        if app_info.get("url"):
            st.markdown(f"**Halaman Play Store:** {app_info['url']}")
        if app_info.get("ringkasan"):
            st.markdown(f"**Ringkasan:** {app_info['ringkasan']}")
        if app_info.get("deskripsi"):
            with st.expander("Deskripsi lengkap"):
                st.write(app_info["deskripsi"])
        st.caption(
            "Metadata diambil dari toko Play Store sesuai bahasa/negara yang "
            f"dipilih ({result.get('lang')}/{result.get('country')})."
        )

# ---- Ekspor ---------------------------------------------------------------- #

st.divider()
st.subheader("Ekspor hasil")

safe_name = _safe_filename(f"{app_info.get('id') or app_name}")
parameters = {
    "kode_aplikasi": app_info.get("id") or st.session_state.get("last_input"),
    "jumlah_diminta": result.get("requested") or "semua",
    "bahasa": result.get("lang"),
    "negara": result.get("country"),
    "urutan": result.get("sort"),
    "filter_bintang": result.get("score_filter"),
    "hanya_berteks": result.get("only_with_text"),
}

d1, d2, _ = st.columns([1, 1, 3])
d1.download_button(
    "⬇️ Unduh CSV",
    data=scraper.rows_to_csv(rows).encode("utf-8-sig"),
    file_name=f"playstore_ulasan_{safe_name}.csv",
    mime="text/csv",
    width="stretch",
)
d2.download_button(
    "⬇️ Unduh JSON",
    data=scraper.rows_to_json(rows, app_info, parameters).encode("utf-8"),
    file_name=f"playstore_ulasan_{safe_name}.json",
    mime="application/json",
    width="stretch",
)

st.caption(
    "CSV memakai UTF-8 + BOM agar langsung rapi di Excel. JSON menyertakan "
    "metadata aplikasi dan parameter scraping. Hormati privasi pengulas dan "
    "Ketentuan Layanan Google Play."
)
