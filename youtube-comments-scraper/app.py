"""Antarmuka Streamlit untuk YouTube Comment Scraper.

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
    page_title="YouTube Comment Scraper",
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
        .yt-hero {
            border: 1px solid #f2d7dc;
            border-radius: 18px;
            padding: 1.3rem 1.5rem;
            background: linear-gradient(135deg, #fff8f8 0%, #ffedf0 100%);
            margin-bottom: 1rem;
        }
        .yt-hero h2 { margin: 0 0 .35rem 0; font-size: 1.55rem; }
        .yt-hero p { margin: 0; color: #4b5563; }
        .yt-step {
            border: 1px solid #e7ebf1;
            border-radius: 14px;
            padding: .9rem 1rem;
            background: #fff;
            min-height: 112px;
        }
        .yt-step b { color: #e0002b; }
        .yt-muted { color: #6b7280; font-size: .9rem; }
        .yt-video-title { margin: 0; font-size: 1.65rem; line-height: 1.25; font-weight: 700; }
        .yt-channel { color: #4b5563; margin-top: .2rem; }
        .yt-badge {
            display: inline-block;
            padding: .16rem .52rem;
            border-radius: 999px;
            background: #ffe8ec;
            color: #b30022;
            font-size: .78rem;
            font-weight: 600;
        }
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


def _fmt_duration(seconds) -> str:
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if total <= 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return (value or "youtube")[:60]


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

    st.title("▶️ YouTube Scraper")
    st.caption(
        "Ambil komentar publik dari sebuah video lewat link YouTube."
    )

    url_input = st.text_input(
        "Link video YouTube",
        placeholder="https://youtu.be/… atau youtube.com/watch?v=…",
        help=(
            "Didukung: youtube.com/watch?v=…, youtu.be/…, youtube.com/shorts/…, "
            "youtube.com/live/…. Link channel atau playlist tidak bisa dipakai."
        ),
        disabled=_job_running,
    )
    st.caption(
        "💡 Buka video di YouTube → **Bagikan** → **Salin link**, lalu tempel di sini."
    )

    method_label = st.selectbox(
        "Metode pengambilan",
        [
            "🥷 Stealth · endpoint komentar",
            "🖥️ Browser · gulir halaman",
        ],
        help=(
            "Mode cepat mengambil komentar per halaman lewat endpoint internal "
            "YouTube, melalui browser stealth tanpa login. Mode browser menggulir "
            "halaman video dan lebih lambat."
        ),
        disabled=_job_running,
    )
    method = "api" if method_label.startswith("🥷") else "browser"

    sort_label = st.selectbox(
        "Urutan komentar (mode cepat)",
        ["Teratas", "Terbaru"],
        help=(
            "Urutan 'Teratas' mengikuti komentar paling relevan dari YouTube. "
            "'Terbaru' mengurutkan komentar terbaru lebih dulu."
        ),
        disabled=_job_running or method != "api",
    )
    sort_key = "terbaru" if sort_label == "Terbaru" else "teratas"

    with st.expander("Opsi lanjutan", expanded=False):
        max_comments = st.number_input(
            "Maksimum komentar utama (0 = semua)",
            min_value=0,
            max_value=100000,
            value=300,
            step=50,
            disabled=_job_running,
            help=(
                "Batas jumlah komentar utama. 0 = ambil sebanyak yang diberikan "
                "YouTube sampai batas waktu/keras tercapai. Video populer bisa "
                "memiliki jutaan komentar; mulailah dari angka kecil."
            ),
        )
        include_replies = st.checkbox(
            "Sertakan balasan komentar (mode cepat)",
            value=False,
            disabled=_job_running or method != "api",
            help=(
                "Mengambil balasan untuk setiap komentar utama yang memiliki "
                "balasan. Menambah waktu dan jumlah baris secara signifikan."
            ),
        )
        max_replies = st.number_input(
            "Maksimum balasan per komentar (0 = semua)",
            min_value=0,
            max_value=1000,
            value=20,
            step=5,
            disabled=(
                _job_running or method != "api" or not include_replies
            ),
            help="Batas balasan yang diambil untuk tiap komentar utama.",
        )
        max_minutes = st.number_input(
            "Waktu maksimum (menit, 0 = batas keras 30)",
            min_value=0,
            max_value=30,
            value=10,
            step=1,
            disabled=_job_running,
            help="Proses berhenti otomatis setelah durasi ini.",
        )
        delay_s = st.slider(
            "Jeda antar-halaman (detik)",
            min_value=0.2,
            max_value=2.0,
            value=0.4,
            step=0.1,
            disabled=_job_running,
            help="Jeda sopan antar permintaan ke YouTube.",
        )
        wait_s = st.slider(
            "Jeda antar-scroll browser (detik)",
            min_value=1.0,
            max_value=4.0,
            value=1.8,
            step=0.2,
            disabled=_job_running or method != "browser",
            help="Hanya dipakai mode browser.",
        )
        headless = st.checkbox(
            "Mode headless (tanpa jendela browser)",
            value=True,
            disabled=_job_running,
            help="Hanya dipakai mode browser.",
        )
        real_chrome = st.checkbox(
            "Gunakan Google Chrome asli",
            value=True,
            disabled=_job_running,
            help=(
                "Hanya dipakai mode browser. Lebih sulit terdeteksi; "
                "nonaktifkan jika Chrome gagal dibuka."
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
        if st.button(
            "⏹️ Hentikan",
            width="stretch",
            help="Hentikan setelah permintaan yang sedang berjalan selesai.",
        ):
            _active_job.stop_event.set()
            st.toast("Permintaan berhenti dikirim…")

    with st.expander("Keamanan & batasan", expanded=False):
        st.markdown(
            """
            - Hanya mengambil **komentar publik**. Tidak ada login, API key,
              atau kata sandi yang diminta.
            - Mode cepat memakai endpoint internal YouTube yang **tidak
              terdokumentasi resmi**; endpoint dapat berubah sewaktu-waktu.
            - Jumlah komentar di YouTube mencakup komentar yang mungkin tidak
              dapat dimuat scraper. Hasil tidak dijamin lengkap.
            - Tanggal absolut hanyalah **perkiraan** dari label relatif
              (mis. “7 tahun yang lalu”).
            - Hormati Ketentuan Layanan YouTube dan privasi komentator. Jangan
              kumpulkan atau menyebarkan data pribadi tanpa dasar yang sah.
            """
        )

    st.caption(
        "⚠️ Gunakan hanya untuk komentar publik yang Anda berhak akses. Patuhi "
        "Ketentuan Layanan YouTube dan jangan kumpulkan data pribadi/sensitif."
    )

# --------------------------------------------------------------------------- #
# Mulai scraping (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    try:
        normalized_url, video_id = scraper.normalize_youtube_url(url_input)
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
            "max_comments": int(max_comments),
            "include_replies": bool(include_replies and method == "api"),
            "max_replies": int(max_replies),
            "max_minutes": int(max_minutes),
            "delay_s": float(delay_s),
            "headless": bool(headless),
            "real_chrome": bool(real_chrome),
            "wait_ms": int(wait_s * 1000),
        }

        def _worker() -> None:
            try:
                result = scraper.scrape_comments(
                    cfg["url"],
                    method=cfg["method"],
                    sort=cfg["sort"],
                    max_comments=cfg["max_comments"],
                    include_replies=cfg["include_replies"],
                    max_replies=cfg["max_replies"],
                    max_minutes=cfg["max_minutes"],
                    delay_s=cfg["delay_s"],
                    headless=cfg["headless"],
                    real_chrome=cfg["real_chrome"],
                    wait_ms=cfg["wait_ms"],
                    log_cb=job.log,
                    progress_cb=job.set_progress,
                    stop_event=job.stop_event,
                )
            except Exception as exc:  # noqa: BLE001
                result = {
                    "rows": [],
                    "video": {},
                    "error": "worker_error",
                    "message": f"Terjadi kesalahan di background worker: {exc}",
                    "url": cfg["url"],
                    "stop_reason": "gagal",
                }
            job.finish(result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        # Rerun penuh agar sidebar langsung merender status "berjalan",
        # menonaktifkan input, dan menampilkan tombol Hentikan.
        st.rerun()

# --------------------------------------------------------------------------- #
# Log live
# --------------------------------------------------------------------------- #


@st.fragment(run_every="0.6s")
def _live_panel() -> None:
    job = st.session_state.get("job")
    if job is None:
        return
    snap = job.snapshot()
    if snap["running"]:
        progress = snap["progress"] or {}
        with st.container(border=True):
            st.markdown("**📄 Log scraping**")
            p1, p2, p3 = st.columns(3)
            p1.metric("Komentar", _fmt_int(progress.get("komentar", 0)))
            p2.metric("Balasan", _fmt_int(progress.get("balasan", 0)))
            p3.metric("Halaman", _fmt_int(progress.get("halaman", 0)))
            logs = snap["logs"][-80:] or ["Membuka halaman YouTube…"]
            st.code("\n".join(logs), language=None)
        return

    # Memicu full rerun; panel log lama akan hilang setelah finalisasi.
    st.rerun(scope="app")


# Tombol Scraping di atas bisa saja baru membuat job pada run ini.
_current_job = st.session_state.get("job")
_current_snap = _current_job.snapshot() if _current_job is not None else None
if _current_snap and _current_snap["running"]:
    _live_panel()

# --------------------------------------------------------------------------- #
# Area utama
# --------------------------------------------------------------------------- #

st.title("Komentar video YouTube")

result = st.session_state.result
job = st.session_state.get("job")
job_snap = job.snapshot() if job is not None else None

if job_snap and job_snap["running"]:
    st.info(
        "Sedang scraping… Progres tampil di panel log di atas. Halaman ini "
        "akan diperbarui otomatis saat proses selesai."
    )
    st.stop()

if not isinstance(result, dict):
    st.markdown(
        """
        <div class="yt-hero">
            <h2>Ubah link video menjadi data komentar</h2>
            <p>Tempel link video YouTube, lalu ambil nama komentator, isi komentar,
            jumlah suka, waktu, balasan, dan tautan — rapi dalam tabel serta CSV/JSON.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        '<div class="yt-step"><b>1 · Salin link</b><br>Buka video di YouTube, '
        'klik Bagikan → Salin link. Link pendek youtu.be juga didukung.</div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        '<div class="yt-step"><b>2 · Tempel & atur</b><br>Masukkan link di sidebar. '
        'Atur jumlah komentar, urutan, dan balasan bila perlu.</div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        '<div class="yt-step"><b>3 · Scraping</b><br>Klik <b>Scraping 🚀</b>, '
        'lihat progres, lalu unduh hasilnya.</div>',
        unsafe_allow_html=True,
    )

    st.info(
        "Gunakan **mode cepat** untuk mengambil komentar melalui browser stealth dan tanpa "
        "login. Untuk video dengan komentar yang sangat banyak, isi **Maksimum "
        "komentar** terlebih dahulu agar proses tidak berjalan terlalu lama."
    )
    st.caption(
        "Data yang diambil adalah komentar publik. Tanggal absolut adalah "
        "perkiraan dari label relatif YouTube (mis. “7 tahun yang lalu”)."
    )
    st.stop()

# ---- Error / hasil kosong -------------------------------------------------- #

rows = result.get("rows") or []
video = result.get("video") or {}
error = result.get("error")
message = result.get("message") or ""
stop_reason = result.get("stop_reason") or ""

if job_snap and not job_snap["running"] and job_snap["logs"]:
    logs = job_snap["logs"]
    truncated = len(logs) > 400
    if truncated:
        logs = ["… (log awal dipotong) …"] + logs[-400:]
    with st.expander(
        f"📄 Log scraping · {job_snap['elapsed']:.1f} detik", expanded=False
    ):
        st.code("\n".join(logs), language=None)

if error:
    if error == "invalid_url":
        st.error(message)
    elif error == "video_unavailable":
        st.warning("Video tidak dapat diakses.")
        st.markdown(message)
    elif error == "comments_disabled":
        st.warning("Komentar video ini tidak tersedia.")
        st.markdown(message)
        st.info(
            "Pastikan komentar video tidak dinonaktifkan dan video dapat dibuka "
            "tanpa login. Jika komentar memang aktif, coba mode browser."
        )
    elif error == "login_required":
        st.warning("YouTube meminta login/verifikasi usia.")
        st.markdown(message)
    elif error == "blocked":
        st.error("Permintaan ke YouTube dibatasi.")
        st.markdown(message)
        st.info(
            "Tunggu beberapa saat lalu coba lagi, kurangi jumlah komentar, atau "
            "gunakan mode browser."
        )
    elif error == "no_comments":
        st.warning("Tidak ada komentar yang terbaca.")
        st.markdown(message)
    else:
        st.error(message or "Scraping gagal.")
    with st.expander("Detail teknis"):
        st.json({k: v for k, v in result.items() if k != "rows"})
    st.stop()

# ---- Header video ---------------------------------------------------------- #

if stop_reason == "dibatalkan":
    st.info(
        "⏹️ Proses dihentikan. Hasil yang sudah terkumpul tetap ditampilkan di bawah."
    )
elif stop_reason == "waktu":
    st.warning(
        "⏱️ Proses berhenti karena batas waktu. Naikkan **Waktu maksimum** atau "
        "turunkan **Maksimum komentar** untuk hasil yang lebih cepat."
    )
elif stop_reason == "gangguan":
    st.warning(message or "Sebagian komentar gagal diambil karena gangguan.")
elif stop_reason == "maks":
    st.caption(
        "ℹ️ Pengambilan berhenti karena batas **Maksimum komentar** tercapai."
    )

title = video.get("judul") or "Video YouTube"
channel = video.get("kanal") or ""
channel_url = video.get("kanal_url") or ""
thumbnail = video.get("thumbnail") or ""

thumb_col, info_col, link_col = st.columns([1.0, 4.2, 1.2])
with thumb_col:
    if thumbnail:
        st.image(thumbnail, width=210)
with info_col:
    st.markdown(
        f'<p class="yt-video-title">{html.escape(title)}</p>',
        unsafe_allow_html=True,
    )
    meta_bits = []
    if channel:
        if channel_url:
            meta_bits.append(
                f'<a href="{html.escape(channel_url)}" target="_blank">{html.escape(channel)}</a>'
            )
        else:
            meta_bits.append(html.escape(channel))
    if video.get("penonton"):
        meta_bits.append(f"{_fmt_int(video['penonton'])} penonton")
    if video.get("tanggal"):
        meta_bits.append(html.escape(str(video["tanggal"])))
    if video.get("durasi_detik"):
        meta_bits.append(_fmt_duration(video["durasi_detik"]))
    if meta_bits:
        st.markdown(
            '<div class="yt-channel">' + " · ".join(meta_bits) + "</div>",
            unsafe_allow_html=True,
        )

    method_used = result.get("method") or "api"
    if method_used == "api":
        extra = "⚡ Diambil lewat mode cepat endpoint komentar YouTube."
    else:
        extra = "🖥️ Diambil lewat browser dengan menggulir halaman komentar."
    if result.get("include_replies") and method_used == "api":
        limit_text = "semua" if not max_replies else f"maks. {max_replies}"
        extra += (
            f" Balasan komentar disertakan ({limit_text} per komentar)."
        )
    if video.get("video_id"):
        extra += f" ID video: `{html.escape(video['video_id'])}`."
    st.caption(extra)

with link_col:
    if video.get("link"):
        st.link_button("Buka di YouTube ↗", video["link"], width="stretch")

# ---- Metrik --------------------------------------------------------------- #

df = pd.DataFrame(rows)
if df.empty:
    st.info("Tidak ada baris komentar untuk ditampilkan.")
    st.stop()

for numeric_col in ("suka", "balasan", "level"):
    if numeric_col in df:
        df[numeric_col] = (
            pd.to_numeric(df[numeric_col], errors="coerce").fillna(0).astype(int)
        )
for flag_col in ("terverifikasi", "kreator"):
    if flag_col in df:
        df[flag_col] = df[flag_col].fillna(False).astype(bool)

top_level = df[df["jenis"] == "komentar"] if "jenis" in df else df
reply_rows = df[df["jenis"] == "balasan"] if "jenis" in df else df.iloc[0:0]

total_claim = int(result.get("total_komentar") or 0)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Komentar utama", _fmt_int(len(top_level)))
m2.metric("Balasan", _fmt_int(len(reply_rows)))
m3.metric(
    "Komentator unik",
    _fmt_int(df["nama"].nunique()) if "nama" in df else "—",
)
m4.metric("Total suka", _fmt_int(df["suka"].sum()) if "suka" in df else "—")
m5.metric(
    "Rata-rata suka",
    _fmt_int(df["suka"].mean()) if "suka" in df and len(df) else "—",
)

if total_claim:
    got = len(top_level) + len(reply_rows)
    if total_claim > len(top_level):
        st.caption(
            f"ℹ️ YouTube mencantumkan **{_fmt_int(total_claim)} komentar**. "
            f"Scraper berhasil mengambil **{_fmt_int(len(top_level))} komentar "
            f"utama**"
            + (f" + **{_fmt_int(len(reply_rows))} balasan**" if len(reply_rows) else "")
            + f" ({_fmt_int(got)} baris total). Angka YouTube mencakup komentar "
            "yang tidak selalu dapat dimuat scraper."
        )
    else:
        st.caption(
            f"ℹ️ YouTube mencantumkan **{_fmt_int(total_claim)} komentar**; "
            f"**{_fmt_int(len(top_level))}** berhasil dibaca."
        )

if not result.get("include_replies") and method_used == "api":
    st.caption(
        "Balasan tidak diminta. Aktifkan **Sertakan balasan** di Opsi lanjutan "
        "bila ingin ikut mengambil balasan komentar."
    )

# ---- Filter --------------------------------------------------------------- #

with st.expander("🔎 Filter & urutan", expanded=True):
    f1, f2, f3, f4 = st.columns([1.2, 2.4, 1.7, 1.4])
    kind_label = f1.selectbox(
        "Jenis",
        ["Semua", "Komentar utama", "Balasan"],
        help="Pisahkan komentar utama dari balasan.",
    )
    keyword = f2.text_input(
        "Cari kata di komentar / nama",
        placeholder="mis. musik, lucu, @nama",
    )
    sort_table = f3.selectbox(
        "Urutkan",
        [
            "Urutan YouTube",
            "Suka terbanyak",
            "Suka tersedikit",
            "Perkiraan terbaru",
            "Perkiraan terlama",
        ],
    )
    only_verified = f4.checkbox("Terverifikasi", value=False)
    only_creator = f4.checkbox("Dari kreator", value=False)

filtered = df.copy()
if kind_label == "Komentar utama":
    filtered = filtered[filtered["jenis"] == "komentar"]
elif kind_label == "Balasan":
    filtered = filtered[filtered["jenis"] == "balasan"]
if keyword.strip():
    needle = keyword.strip().lower()
    mask = (
        filtered["komentar"].fillna("").str.lower().str.contains(needle, regex=False)
        | filtered["nama"].fillna("").str.lower().str.contains(needle, regex=False)
    )
    filtered = filtered[mask]
if only_verified:
    filtered = filtered[filtered["terverifikasi"]]
if only_creator:
    filtered = filtered[filtered["kreator"]]

if sort_table == "Suka terbanyak":
    filtered = filtered.sort_values(["suka", "balasan"], ascending=[False, False])
elif sort_table == "Suka tersedikit":
    filtered = filtered.sort_values(["suka", "balasan"], ascending=[True, False])
elif sort_table in ("Perkiraan terbaru", "Perkiraan terlama"):
    filtered = filtered.assign(
        _tanggal=pd.to_datetime(filtered["perkiraan_tanggal"], errors="coerce")
    )
    ascending = sort_table == "Perkiraan terlama"
    filtered = filtered.sort_values(
        "_tanggal", ascending=ascending, na_position="last"
    )

st.caption(
    f"Menampilkan **{_fmt_int(len(filtered))}** dari **{_fmt_int(len(df))}** "
    "baris terkumpul."
)

# ---- Tabel & ringkasan ---------------------------------------------------- #

tab_table, tab_summary = st.tabs(["📋 Tabel komentar", "📈 Ringkasan"])

with tab_table:
    if filtered.empty:
        st.info("Tidak ada komentar yang cocok dengan filter.")
    else:
        cols = [
            "nama",
            "komentar",
            "suka",
            "balasan",
            "waktu",
            "perkiraan_tanggal",
            "jenis",
            "link_komentar",
        ]
        cols = [c for c in cols if c in filtered.columns]

        display = filtered.copy()
        if "terverifikasi" in display:
            display["nama"] = display["nama"].fillna("") + display[
                "terverifikasi"
            ].map({True: " ✓", False: ""})
        if "jenis" in display:
            display["jenis"] = display["jenis"].map(
                {"komentar": "Komentar", "balasan": "Balasan"}
            ).fillna(display["jenis"])

        st.dataframe(
            display[cols],
            width="stretch",
            height=580,
            hide_index=True,
            column_config={
                "nama": st.column_config.TextColumn("Nama", width="small"),
                "komentar": st.column_config.TextColumn("Komentar", width="large"),
                "suka": st.column_config.NumberColumn("Suka", format="%d"),
                "balasan": st.column_config.NumberColumn("Balasan", format="%d"),
                "waktu": st.column_config.TextColumn("Waktu (dari YouTube)"),
                "perkiraan_tanggal": st.column_config.TextColumn(
                    "Perkiraan tanggal"
                ),
                "jenis": st.column_config.TextColumn("Jenis", width="small"),
                "link_komentar": st.column_config.LinkColumn(
                    "Link", display_text="buka ↗", width="small"
                ),
            },
        )

with tab_summary:
    if filtered.empty:
        st.info("Belum ada data untuk diringkas.")
    else:
        chart_a, chart_b = st.columns(2)
        with chart_a:
            st.subheader("10 komentar paling disukai")
            top = filtered.nlargest(10, "suka").copy()
            if top.empty:
                st.info("Tidak ada data suka.")
            else:
                top["label"] = (
                    top["nama"].fillna("").str.slice(0, 18)
                    + " — "
                    + top["komentar"].fillna("").str.slice(0, 42)
                )
                st.bar_chart(
                    top.set_index("label")["suka"],
                    horizontal=True,
                    color="#e0002b",
                )

        with chart_b:
            st.subheader("Perkiraan komentar per bulan")
            dates = pd.to_datetime(
                filtered["perkiraan_tanggal"], errors="coerce"
            )
            monthly = (
                dates.dropna()
                .dt.to_period("M")
                .astype(str)
                .value_counts()
                .sort_index()
            )
            if monthly.empty:
                st.info("Tanggal perkiraan tidak tersedia.")
            else:
                st.bar_chart(monthly, color="#7c3aed")

        st.subheader("10 komentator paling aktif")
        top_people = (
            filtered[filtered["jenis"] == "komentar"]
            .groupby("nama", dropna=False)
            .size()
            .nlargest(10)
            .sort_values()
        )
        if top_people.empty:
            st.info("Data komentator tidak tersedia.")
        else:
            st.bar_chart(top_people, horizontal=True, color="#f59e0b")

        st.caption(
            "Grafik tanggal memakai **perkiraan** dari label relatif YouTube "
            "(bulan = 30 hari, tahun = 365 hari), bukan tanggal absolut resmi. "
            "Grafik komentator menghitung komentar utama saja."
        )

# ---- Ekspor ---------------------------------------------------------------- #

st.divider()
st.subheader("Ekspor hasil")

safe_name = _safe_filename(video.get("video_id") or "youtube")
meta = {
    "metode": result.get("method"),
    "urutan": result.get("sort"),
    "jumlah_diminta": int(max_comments),
    "sertakan_balasan": bool(result.get("include_replies")),
    "maks_balasan_per_komentar": int(max_replies),
    "total_komentar_youtube": total_claim or None,
    "stop_reason": stop_reason,
    "diambil_pada": result.get("diambil_pada"),
}
d1, d2, _ = st.columns([1, 1, 3])
d1.download_button(
    "⬇️ Unduh CSV",
    data=scraper.rows_to_csv(rows).encode("utf-8-sig"),
    file_name=f"youtube_komentar_{safe_name}.csv",
    mime="text/csv",
    width="stretch",
)
d2.download_button(
    "⬇️ Unduh JSON",
    data=scraper.rows_to_json(rows, video, meta).encode("utf-8"),
    file_name=f"youtube_komentar_{safe_name}.json",
    mime="application/json",
    width="stretch",
)

st.caption(
    f"Ekspor berisi **{_fmt_int(len(rows))} baris** (seluruh hasil scraping, "
    "bukan hanya yang lolos filter). Tanggal absolut adalah perkiraan dari "
    "label relatif YouTube. Hormati privasi komentator dan Ketentuan Layanan "
    "YouTube."
)
