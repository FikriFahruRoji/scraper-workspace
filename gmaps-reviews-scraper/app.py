"""Antarmuka Streamlit untuk Google Maps Review Scraper.

Jalankan dengan:
    streamlit run app.py
"""

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
    page_title="Google Maps Review Scraper",
    page_icon="📍",
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
        .gm-hero {
            border: 1px solid #e4e9f2;
            border-radius: 18px;
            padding: 1.3rem 1.5rem;
            background: linear-gradient(135deg, #f8fbff 0%, #eef5ff 100%);
            margin-bottom: 1rem;
        }
        .gm-hero h2 { margin: 0 0 .35rem 0; font-size: 1.55rem; }
        .gm-hero p { margin: 0; color: #4b5563; }
        .gm-step {
            border: 1px solid #e7ebf1;
            border-radius: 14px;
            padding: .9rem 1rem;
            background: #fff;
            min-height: 112px;
        }
        .gm-step b { color: #1a73e8; }
        .gm-muted { color: #6b7280; font-size: .9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

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
    """Wadah thread-safe untuk log & hasil scraping.

    Worker thread TIDAK boleh menyentuh ``st.session_state``. Streamlit 1.67
    dapat melempar StopException saat session state diakses dari thread lain,
    jadi semua komunikasi dilakukan lewat objek biasa + lock ini.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[str] = []
        self._result: dict | None = None
        self._running = True
        self._finished_at: float | None = None
        self.started_at = time.time()

    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(str(message))

    def finish(self, result: dict) -> None:
        with self._lock:
            self._result = result
            self._running = False
            self._finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "logs": list(self._logs),
                "result": self._result,
                "running": self._running,
                "elapsed": (self._finished_at or time.time()) - self.started_at,
            }


def _safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return (value or "google_maps")[:60]


# Finalisasi dilakukan di thread utama Streamlit, bukan di worker, dan sebelum
# sidebar dirender.
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

    st.title("📍 Google Maps Scraper")
    st.caption("Ambil ulasan publik dari sebuah tempat lewat link Google Maps.")

    url_input = st.text_input(
        "Link lokasi Google Maps",
        placeholder="https://maps.app.goo.gl/… atau https://www.google.com/maps/place/…",
        help=(
            "Tempel link tempat (bukan link profil). Link pendek maps.app.goo.gl, "
            "link panjang Google Maps, atau nama tempat juga bisa."
        ),
    )
    st.caption(
        "💡 Buka tempat di Google Maps → **Bagikan** → **Salin link**, lalu tempel di sini."
    )

    method_label = st.selectbox(
        "Metode pengambilan",
        [
            "🥷 Samaran · browser stealth + endpoint ulasan",
            "🖥️ Browser · tampilan Google Maps",
        ],
        index=0,
        help=(
            "Samaran menjalankan request ulasan dari browser StealthySession "
            "dengan pagination. Pilihan Browser membaca kartu ulasan dari tampilan Google Maps."
        ),
    )
    method = "stealth" if method_label.startswith("🥷") else "browser"

    sort_label = st.selectbox(
        "Urutan ulasan (mode cepat/samaran)",
        ["Paling relevan", "Terbaru", "Rating tertinggi", "Rating terendah"],
        help="Urutan ini dipakai oleh endpoint ulasan Google.",
        disabled=method == "browser",
    )
    sort_map = {
        "Paling relevan": "relevant",
        "Terbaru": "newest",
        "Rating tertinggi": "highest",
        "Rating terendah": "lowest",
    }
    sort_key = sort_map.get(sort_label, "relevant")

    with st.expander("Opsi lanjutan", expanded=False):
        headless = st.checkbox("Mode headless (tanpa jendela browser)", value=True)
        real_chrome = st.checkbox(
            "Gunakan Google Chrome asli",
            value=True,
            help="Lebih sulit terdeteksi. Nonaktifkan jika Chrome gagal dibuka.",
        )
        wait_s = st.slider(
            "Jeda antar-scroll (detik)",
            min_value=1.0,
            max_value=5.0,
            value=1.6,
            step=0.2,
        )
        max_reviews = st.number_input(
            "Maksimum ulasan (0 = semua yang tersedia)",
            min_value=0,
            max_value=50000,
            value=0,
            step=100,
            help=(
                "0 = ambil semua komentar yang ditampilkan Google Maps, sampai "
                "feed habis atau batas waktu tercapai."
            ),
        )
        include_empty = st.checkbox(
            "Sertakan rating tanpa komentar (mode cepat/samaran)",
            value=False,
            help=(
                "Total rating di Google sering jauh lebih besar daripada jumlah "
                "komentar teks. Aktifkan jika ingin menyertakan rating kosong."
            ),
        )
        max_minutes = st.number_input(
            "Waktu maksimum (menit)",
            min_value=0,
            max_value=180,
            value=30,
            step=5,
            help=(
                "Untuk tempat dengan puluhan ribu ulasan, gunakan 60–120 menit. "
                "0 = memakai batas sesi browser (mode samaran tetap "
                "dibatasi oleh sesi browser)."
            ),
        )

    st.divider()

    with st.expander("Keamanan & batasan", expanded=False):
        st.markdown(
            """
            - Aplikasi tidak memakai login Google. Mode browser biasanya hanya
              menampilkan sebagian kecil ulasan.
            - Mode cepat/samaran memakai endpoint ulasan internal Google yang
              tidak terdokumentasi resmi; endpoint dapat berubah sewaktu-waktu.
            - Google Maps dapat mengubah struktur halaman atau membatasi akses
              kapan saja. Hasil tidak dijamin lengkap.
            """
        )

    st.divider()

    run_btn = st.button(
        "Scraping 🚀",
        type="primary",
        width="stretch",
        disabled=_job_running,
        help="Tunggu proses yang sedang berjalan selesai." if _job_running else None,
    )

    st.caption(
        "⚠️ Gunakan hanya untuk ulasan publik yang Anda berhak akses. Patuhi "
        "Ketentuan Layanan Google Maps dan jangan kumpulkan data pribadi/sensitif."
    )

# --------------------------------------------------------------------------- #
# Mulai scraping (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    try:
        normalized_url = scraper.normalize_maps_url(url_input)
        validation_error = None
    except ValueError as exc:
        normalized_url = ""
        validation_error = str(exc)

    if validation_error:
        st.error(validation_error)
    else:
        job = ScrapeJob()
        st.session_state.last_input = normalized_url
        st.session_state.result = None
        st.session_state.job = job
        st.session_state.job_finalized = False

        cfg = {
            "url": normalized_url,
            "method": method,
            "sort": sort_key,
            "headless": headless,
            "real_chrome": real_chrome,
            "wait_ms": int(wait_s * 1000),
            "max_minutes": int(max_minutes),
            "max_reviews": int(max_reviews),
            "include_empty_reviews": bool(include_empty),
        }

        def _worker() -> None:
            try:
                result = scraper.scrape_reviews(
                    cfg["url"],
                    method=cfg["method"],
                    sort=cfg["sort"],
                    headless=cfg["headless"],
                    real_chrome=cfg["real_chrome"],
                    wait_ms=cfg["wait_ms"],
                    max_minutes=cfg["max_minutes"],
                    max_reviews=cfg["max_reviews"],
                    include_empty_reviews=cfg["include_empty_reviews"],
                    log_cb=job.log,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "rows": [],
                    "place": {},
                    "limited": False,
                    "logged_in": None,
                    "error": "worker_error",
                    "message": f"Terjadi kesalahan di background worker: {exc}",
                    "url": cfg["url"],
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
        with st.container(border=True):
            st.markdown("**📄 Log scraping**")
            st.code("\n".join(snap["logs"]) or "Membuka browser…", language=None)
        return

    # Memicu full rerun; blok log lama akan hilang setelah finalisasi.
    st.rerun(scope="app")


# Ambil ulang: tombol Scraping di atas bisa saja baru membuat job pada run ini.
_current_job = st.session_state.get("job")
_current_snap = _current_job.snapshot() if _current_job is not None else None
if _current_snap and _current_snap["running"]:
    _live_log()

# --------------------------------------------------------------------------- #
# Area utama
# --------------------------------------------------------------------------- #

st.title("Ulasan Google Maps")

result = st.session_state.result
job = st.session_state.get("job")
job_snap = job.snapshot() if job is not None else None

if job_snap and job_snap["running"]:
    st.info(
        "Sedang scraping… Browser dibuka di latar belakang. Progres tampil di log "
        "atas; halaman ini akan diperbarui otomatis saat selesai."
    )
    st.stop()

if not isinstance(result, dict):
    st.markdown(
        """
        <div class="gm-hero">
            <h2>Ubah link lokasi menjadi data ulasan</h2>
            <p>Tempel link tempat dari Google Maps, lalu ambil nama reviewer, rating,
            tanggal, teks ulasan, dan jumlah suka — rapi dalam tabel serta CSV/JSON.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        '<div class="gm-step"><b>1 · Salin link</b><br>Buka tempat di Google Maps, '
        'klik Bagikan → Salin link. Link pendek juga didukung.</div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        '<div class="gm-step"><b>2 · Tempel & atur</b><br>Masukkan link di sidebar. '
        'Atur batas ulasan bila perlu.</div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        '<div class="gm-step"><b>3 · Scraping</b><br>Klik <b>Scraping 🚀</b>, '
        'lihat progres, lalu unduh hasilnya.</div>',
        unsafe_allow_html=True,
    )

    st.info(
        "Gunakan **mode samaran** atau **mode cepat** untuk mengambil ribuan "
        "ulasan tanpa login. Mode browser tetap tersedia sebagai cadangan, tetapi "
        "tampilan Google Maps biasanya berhenti di beberapa ratus kartu."
    )
    st.caption(
        "Data yang diambil adalah ulasan publik. Mode cepat/samaran memakai "
        "endpoint internal Google dan timestamp asli; mode browser memakai "
        "perkiraan dari label relatif (mis. “3 bulan lalu”)."
    )
    st.stop()

# ---- Error / hasil kosong -------------------------------------------------- #

rows = result.get("rows") or []
place = result.get("place") or {}
error = result.get("error")
message = result.get("message") or ""

if job_snap and not job_snap["running"] and job_snap["logs"]:
    with st.expander(f"📄 Log scraping · {job_snap['elapsed']:.1f} detik"):
        st.code("\n".join(job_snap["logs"]), language=None)

if error:
    if error == "invalid_url":
        st.error(message)
    elif error == "limited_view":
        st.warning("Google Maps membatasi tampilan ulasan.")
        st.markdown(message)
        st.info(
            "Coba **mode samaran** atau **mode cepat**, atau gunakan API resmi "
            "Google Places."
        )
    elif error == "no_reviews":
        st.warning("Tidak ada ulasan yang terbaca.")
        st.markdown(message)
    else:
        st.error(message or "Scraping gagal.")
    with st.expander("Detail teknis"):
        st.json({k: v for k, v in result.items() if k != "rows"})
    st.stop()

# ---- Header tempat --------------------------------------------------------- #

place_name = place.get("nama") or "Tempat di Google Maps"
st.subheader(place_name)

meta_bits = []
if place.get("kategori"):
    meta_bits.append(place["kategori"])
if place.get("alamat"):
    meta_bits.append(place["alamat"])
if place.get("rating"):
    rating_text = f"⭐ {place['rating']:.1f}".replace(".", ",")
    if place.get("jumlah_ulasan"):
        rating_text += f" · {place['jumlah_ulasan']:,} ulasan".replace(",", ".")
    meta_bits.append(rating_text)
if meta_bits:
    st.caption(" · ".join(meta_bits))

if result.get("method") in ("api", "stealth"):
    _mode_label = (
        "🥷 mode samaran (StealthySession)" if result.get("method") == "stealth"
        else "⚡ mode cepat (HTTP)"
    )
    st.caption(
        f"Diambil lewat {_mode_label}. Tanggal memakai timestamp dari Google "
        "bila tersedia."
        + (
            " Hanya ulasan yang memiliki teks komentar yang ditampilkan."
            if not result.get("include_empty_reviews", include_empty)
            else " Termasuk rating tanpa teks komentar."
        )
    )

link_col, _ = st.columns([1, 4])
if place.get("link"):
    link_col.link_button("Buka di Google Maps ↗", place["link"], width="stretch")
if place.get("website"):
    st.caption(f"Website: {place['website']}")

if result.get("limited"):
    st.warning(
        "🚧 Google Maps menampilkan **versi terbatas**. Hasil di bawah mungkin "
        "hanya sebagian kecil dari total ulasan. Coba mode samaran atau mode "
        "cepat untuk mengambil lebih banyak data."
    )

_total_place = int(place.get("jumlah_ulasan") or 0)
if _total_place and _total_place > len(rows):
    _total_fmt = f"{_total_place:,}".replace(",", ".")
    _got_fmt = f"{len(rows):,}".replace(",", ".")
    if result.get("method") in ("api", "stealth"):
        st.caption(
            f"ℹ️ Google mencantumkan **{_total_fmt} rating**; mode cepat/samaran "
            f"berhasil "
            f"mengambil **{_got_fmt} komentar**. Angka total juga mencakup rating "
            "tanpa teks komentar."
            + (
                " Centang **Sertakan rating tanpa komentar** jika ingin menghitung "
                "rating kosong juga."
                if not result.get("include_empty_reviews", include_empty)
                else " Sebagian rating tetap bisa dibatasi oleh endpoint Google."
            )
        )
    else:
        st.caption(
            f"ℹ️ Google mencantumkan **{_total_fmt} rating**, tetapi feed komentar "
            f"yang dapat dimuat berhenti di **{_got_fmt}**. Angka total mencakup "
            "rating tanpa teks; Google juga dapat membatasi jumlah kartu yang dimuat."
        )

# ---- Metrik --------------------------------------------------------------- #

df = pd.DataFrame(rows)
if df.empty:
    st.info("Tidak ada baris ulasan untuk ditampilkan.")
    st.stop()

if "rating" in df:
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
for numeric_col in ("suka", "foto", "ulasan_reviewer", "foto_reviewer"):
    if numeric_col in df:
        df[numeric_col] = pd.to_numeric(df[numeric_col], errors="coerce").fillna(0).astype(int)

avg_rating = df["rating"].mean() if "rating" in df else 0
local_guide_pct = df["local_guide"].mean() * 100 if "local_guide" in df else 0

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Ulasan terkumpul", f"{len(df):,}".replace(",", "."))
m2.metric("Rating rata-rata", f"{avg_rating:.2f}".replace(".", ",") if avg_rating else "—")
m3.metric("Reviewer unik", f"{df['nama'].nunique():,}".replace(",", "."))
if result.get("method") in ("api", "stealth"):
    m4.metric("Total suka", "—")
else:
    m4.metric("Total suka", f"{int(df['suka'].sum()):,}".replace(",", "."))
m5.metric("Local Guide", f"{local_guide_pct:.0f}%")

if result.get("method") in ("api", "stealth"):
    st.caption(
        "ℹ️ Mode cepat/samaran tidak mengambil jumlah suka per ulasan; kolom itu "
        "disembunyikan. Data lain diambil dari respons ulasan Google."
    )

# ---- Filter --------------------------------------------------------------- #

with st.expander("🔎 Filter & urutan", expanded=True):
    f1, f2, f3 = st.columns([1.2, 2, 1.5])
    rating_options = [5, 4, 3, 2, 1]
    selected_ratings = f1.multiselect(
        "Bintang",
        rating_options,
        default=[],
        placeholder="Semua bintang",
    )
    keyword = f2.text_input(
        "Cari kata di ulasan / nama",
        placeholder="mis. parkir, bersih, pelayanan",
    )
    sort_label = f3.selectbox(
        "Urutkan",
        [
            "Urutan Google Maps",
            "Rating tertinggi",
            "Rating terendah",
            "Perkiraan tanggal terbaru",
            "Perkiraan tanggal terlama",
        ],
    )

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

if sort_label == "Rating tertinggi":
    filtered = filtered.sort_values(["rating", "suka"], ascending=[False, False])
elif sort_label == "Rating terendah":
    filtered = filtered.sort_values(["rating", "suka"], ascending=[True, False])
elif sort_label in ("Perkiraan tanggal terbaru", "Perkiraan tanggal terlama"):
    filtered = filtered.assign(
        _tanggal=pd.to_datetime(filtered["perkiraan_tanggal"], errors="coerce")
    )
    ascending = sort_label == "Perkiraan tanggal terlama"
    filtered = filtered.sort_values("_tanggal", ascending=ascending, na_position="last")

st.caption(
    f"Menampilkan **{len(filtered):,}** dari **{len(df):,}** ulasan terkumpul."
    .replace(",", ".")
)

# ---- Tabel & ringkasan ---------------------------------------------------- #

tab_table, tab_summary = st.tabs(["📋 Tabel ulasan", "📈 Ringkasan"])

with tab_table:
    if filtered.empty:
        st.info("Tidak ada ulasan yang cocok dengan filter.")
    else:
        cols = [
            "nama", "rating", "tanggal", "perkiraan_tanggal", "ulasan", "suka",
            "foto", "local_guide", "info_reviewer", "profil", "balasan_pemilik",
        ]
        if result.get("method") in ("api", "stealth"):
            cols.remove("suka")
        cols = [c for c in cols if c in filtered.columns]
        st.dataframe(
            filtered[cols],
            width="stretch",
            height=580,
            hide_index=True,
            column_config={
                "nama": st.column_config.TextColumn("Nama"),
                "rating": st.column_config.NumberColumn("Bintang", format="%.0f ★"),
                "tanggal": st.column_config.TextColumn("Tanggal (dari Google)"),
                "perkiraan_tanggal": st.column_config.TextColumn(
                    "Tanggal (ISO)" if result.get("method") in ("api", "stealth")
                    else "Perkiraan tanggal"
                ),
                "ulasan": st.column_config.TextColumn("Ulasan", width="large"),
                "suka": st.column_config.NumberColumn("Suka"),
                "foto": st.column_config.NumberColumn("Foto"),
                "local_guide": st.column_config.CheckboxColumn("Local Guide"),
                "info_reviewer": st.column_config.TextColumn("Info reviewer", width="medium"),
                "profil": st.column_config.LinkColumn("Profil", display_text="buka ↗"),
                "balasan_pemilik": st.column_config.TextColumn(
                    "Balasan pemilik", width="medium"
                ),
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
                filtered["rating"].dropna().astype(int).value_counts().sort_index()
                .reindex([1, 2, 3, 4, 5], fill_value=0)
            )
            dist.index = [f"{i} ★" for i in dist.index]
            st.bar_chart(dist, color="#1a73e8")

        with c2:
            st.subheader("Ulasan per bulan")
            dates = pd.to_datetime(filtered["perkiraan_tanggal"], errors="coerce")
            monthly = dates.dropna().dt.to_period("M").astype(str).value_counts().sort_index()
            if monthly.empty:
                st.info("Tanggal perkiraan tidak tersedia.")
            else:
                st.bar_chart(monthly, color="#34a853")

        st.subheader("10 reviewer dengan kontribusi terbanyak")
        top = (
            filtered.drop_duplicates(subset=["profil"])
            .dropna(subset=["ulasan_reviewer"])
            .nlargest(10, "ulasan_reviewer")
            .set_index("nama")["ulasan_reviewer"]
        )
        if top.empty:
            st.info("Data kontribusi reviewer tidak tersedia.")
        else:
            st.bar_chart(top, horizontal=True, color="#fbbc04")

        if result.get("method") in ("api", "stealth"):
            st.caption(
                "Grafik tanggal memakai timestamp dari Google; hanya ulasan dengan "
                "teks komentar yang disertakan."
            )
        else:
            st.caption(
                "Grafik tanggal memakai **perkiraan** dari label relatif Google Maps, "
                "bukan tanggal absolut resmi."
            )

# ---- Ekspor ---------------------------------------------------------------- #

st.divider()
st.subheader("Ekspor hasil")

safe_name = _safe_filename(place_name)
d1, d2, _ = st.columns([1, 1, 3])
d1.download_button(
    "⬇️ Unduh CSV",
    data=scraper.rows_to_csv(rows).encode("utf-8-sig"),
    file_name=f"gmaps_ulasan_{safe_name}.csv",
    mime="text/csv",
    width="stretch",
)
d2.download_button(
    "⬇️ Unduh JSON",
    data=scraper.rows_to_json(rows, place).encode("utf-8"),
    file_name=f"gmaps_ulasan_{safe_name}.json",
    mime="application/json",
    width="stretch",
)

if result.get("method") in ("api", "stealth"):
    st.caption(
        "Tanggal di mode cepat/samaran memakai timestamp dari Google. "
        "Hormati privasi reviewer dan Ketentuan Layanan Google."
    )
else:
    st.caption(
        "Tanggal absolut adalah perkiraan dari label relatif Google Maps. "
        "Hormati privasi reviewer dan Ketentuan Layanan Google."
    )
