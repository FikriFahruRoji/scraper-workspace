"""Antarmuka Streamlit untuk Instagram Scraper.

Jalankan dengan:
    streamlit run app.py
"""

from __future__ import annotations

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
    page_title="Instagram Scraper",
    page_icon="📸",
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

        .ig-hero {
            border: 1px solid #f4d8e5;
            border-radius: 20px;
            padding: 1.5rem 1.7rem;
            background:
                radial-gradient(circle at 92% 10%, rgba(252, 176, 69, .18), transparent 38%),
                linear-gradient(135deg, #fff8fb 0%, #fdf1f6 45%, #fff9f1 100%);
            margin-bottom: 1rem;
        }
        .ig-kicker {
            color: #c13584;
            font-size: .74rem;
            font-weight: 800;
            letter-spacing: .11em;
            text-transform: uppercase;
            margin-bottom: .35rem;
        }
        .ig-hero h2 { margin: 0 0 .4rem 0; font-size: 1.65rem; line-height: 1.2; }
        .ig-hero p { margin: 0; color: #4b5563; max-width: 880px; }
        .ig-step {
            border: 1px solid #e9edf2;
            border-radius: 15px;
            padding: .95rem 1.05rem;
            background: #fff;
            min-height: 118px;
        }
        .ig-step b { color: #c13584; }
        .ig-step .ig-step-title { font-weight: 700; margin-bottom: .3rem; }
        .ig-muted { color: #6b7280; font-size: .9rem; }
        .ig-badge {
            display: inline-block;
            padding: .18rem .58rem;
            border-radius: 999px;
            background: #fce8f2;
            color: #a32c6d;
            font-size: .78rem;
            font-weight: 700;
            margin-right: .35rem;
        }
        .ig-badge.green { background: #e8f7ee; color: #157347; }
        .ig-badge.amber { background: #fff4df; color: #9a6700; }
        .ig-profile {
            border: 1px solid #eef0f4;
            border-radius: 16px;
            padding: 1rem 1.15rem;
            background: #fff;
        }
        .ig-profile h3 { margin: 0 0 .2rem 0; }
        .ig-profile .ig-handle { color: #c13584; font-weight: 700; }
        .ig-quote {
            border-left: 3px solid #f0b6d0;
            padding: .15rem 0 .15rem .8rem;
            color: #4b5563;
            margin: .35rem 0 0 0;
            white-space: pre-wrap;
        }
        .ig-note {
            border: 1px solid #f0e2b8;
            background: #fffaf0;
            border-radius: 14px;
            padding: .85rem 1rem;
            color: #6b4f00;
            font-size: .92rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# State sesi & job background
# --------------------------------------------------------------------------- #

_DEFAULTS = {
    "result": None,
    "job": None,
    "job_finalized": False,
    "logged_in": None,
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
        self._result: dict | None = None
        self._running = True
        self._finished_at: float | None = None
        self.started_at = time.time()
        self.stop_event = threading.Event()

    def log(self, message: str) -> None:
        with self._lock:
            if len(self._logs) < 4000:
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


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def _safe_filename(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", (name or "").strip()).strip("_")
    return (value or "instagram")[:60]


def _short(text: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _safe_url(value) -> str:
    text = str(value or "").strip()
    return text if text.startswith(("http://", "https://")) else ""


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

_active_job = st.session_state.get("job")
_job_running = bool(_active_job and _active_job.snapshot()["running"])

# --------------------------------------------------------------------------- #
# Sidebar — kontrol
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.title("📸 Instagram Scraper")
    st.caption(
        "Ambil komentar dari satu post, atau kumpulkan konten dari username / "
        "kata kunci / hashtag."
    )

    mode_label = st.radio(
        "Mode pengambilan",
        ["💬 Komentar post", "🖼️ Konten (post)"],
        disabled=_job_running,
        help=(
            "Mode komentar membutuhkan link post/reel. Mode konten mengambil "
            "daftar postingan tanpa membuka komentarnya."
        ),
    )
    mode = "comments" if mode_label.startswith("💬") else "content"

    post_input = ""
    content_input = ""
    input_type = "username"
    if mode == "comments":
        post_input = st.text_input(
            "Link post / reel Instagram",
            placeholder="https://www.instagram.com/p/XXXXXXXXXXX/",
            disabled=_job_running,
            help=(
                "Salin link dari tombol Bagikan → Salin tautan pada post. "
                "Link /p/, /reel/, /tv/, dan link share Instagram didukung."
            ),
        )
        st.caption(
            "💡 Buka post → **Bagikan** → **Salin tautan** → tempel di sini. "
            "Untuk komentar lebih dari ±10, login terlebih dahulu."
        )
    else:
        type_label = st.radio(
            "Jenis input",
            ["👤 Username", "🔎 Kata kunci", "#️⃣ Hashtag"],
            horizontal=True,
            disabled=_job_running,
            help=(
                "Username mengambil postingan satu akun. Kata kunci memakai "
                "pencarian Instagram (perlu login). Hashtag memakai halaman tag "
                "yang bisa dibuka tanpa login."
            ),
        )
        input_type = (
            "username"
            if type_label.startswith("👤")
            else "hashtag" if type_label.startswith("#️⃣") else "keyword"
        )
        placeholder = {
            "username": "mis. instagram atau @instagram",
            "keyword": "mis. ballon d'or, resep rendang",
            "hashtag": "mis. #ballondor atau ballondor",
        }[input_type]
        content_input = st.text_input(
            "Username / kata kunci / hashtag",
            placeholder=placeholder,
            disabled=_job_running,
            help=(
                "Tempel username (boleh dengan @), kata kunci, atau hashtag. "
                "Link profil/hashtag juga diterima."
            ),
        )
        if input_type == "keyword":
            st.caption(
                "🔐 Pencarian kata kunci di Instagram memerlukan login. "
                "Tanpa login, gunakan **Hashtag**."
            )
        elif input_type == "hashtag":
            st.caption(
                "💡 Halaman hashtag bisa dibuka tanpa login, tetapi Instagram "
                "membatasi jumlah post untuk pengunjung."
            )
        else:
            st.caption(
                "💡 Tanpa login, Instagram biasanya hanya menampilkan ±12 post "
                "terbaru dari profil publik."
            )

    with st.expander("Opsi lanjutan", expanded=False):
        headless = st.checkbox(
            "Mode headless (tanpa jendela browser)",
            value=True,
            disabled=_job_running,
            help="Matikan untuk melihat browser bekerja saat debugging.",
        )
        real_chrome = st.checkbox(
            "Gunakan Google Chrome asli",
            value=True,
            disabled=_job_running,
            help="Lebih sulit terdeteksi. Nonaktifkan jika Chrome gagal dibuka.",
        )
        wait_s = st.slider(
            "Jeda antar-gulir (detik)",
            min_value=1.0,
            max_value=5.0,
            value=1.8,
            step=0.2,
            disabled=_job_running,
        )
        if mode == "comments":
            max_items = st.number_input(
                "Maksimum komentar (0 = semua yang tersedia)",
                min_value=0,
                max_value=50_000,
                value=0,
                step=100,
                disabled=_job_running,
                help=(
                    "0 = ambil semua komentar yang bisa dimuat, sampai feed habis "
                    "atau batas waktu tercapai."
                ),
            )
            include_replies = st.checkbox(
                "Sertakan balasan komentar",
                value=True,
                disabled=_job_running,
                help=(
                    "Mengklik tombol “View replies” dan menyertakan balasan dalam "
                    "tabel. Balasan memiliki kolom jenis “Balasan”."
                ),
            )
            start_date_c = end_date_c = None
        else:
            max_items = st.number_input(
                "Maksimum post (0 = semua yang tersedia)",
                min_value=0,
                max_value=10_000,
                value=0,
                step=50,
                disabled=_job_running,
                help=(
                    "0 = ambil semua post yang bisa dimuat, sampai daftar habis "
                    "atau batas waktu tercapai."
                ),
            )
            include_replies = True
            d1, d2 = st.columns(2)
            start_date_c = d1.date_input(
                "Dari tanggal (opsional)",
                value=None,
                disabled=_job_running,
                key="content_start_date",
                help="Kosongkan untuk tanpa filter tanggal.",
            )
            end_date_c = d2.date_input(
                "Sampai tanggal (opsional)",
                value=None,
                disabled=_job_running,
                key="content_end_date",
                help="Kosongkan untuk tanpa filter tanggal.",
            )
        max_minutes = st.number_input(
            "Waktu maksimum (menit)",
            min_value=0,
            max_value=60,
            value=10,
            step=1,
            disabled=_job_running,
            help="0 = memakai batas keras internal 30 menit.",
        )

    st.divider()

    # ---- Login Instagram (opsional) -------------------------------------- #
    st.markdown("**🔐 Sesi Instagram (opsional)**")
    st.caption(
        "Login hanya untuk membuka lebih banyak komentar/post. Kata sandi tidak "
        "dibaca aplikasi; hanya cookie sesi yang tersimpan di profil browser."
    )

    if st.session_state.logged_in is True:
        st.success("Sesi Instagram: **aktif** ✅")
    elif st.session_state.logged_in is False:
        st.warning("Sesi Instagram: **belum login**")
    else:
        st.info("Status sesi Instagram: belum dicek.")

    col_login, col_check = st.columns(2)
    login_clicked = col_login.button(
        "🔑 Login",
        width="stretch",
        disabled=_job_running,
        help="Membuka jendela browser untuk login manual ke akun Anda sendiri.",
    )
    check_clicked = col_check.button(
        "🔍 Cek sesi", width="stretch", disabled=_job_running
    )

    if login_clicked:
        with st.spinner(
            "Membuka browser… Silakan login ke Instagram di jendela yang muncul. "
            "Tunggu sampai login terdeteksi otomatis."
        ):
            res = scraper.login(real_chrome=real_chrome)
            st.session_state.logged_in = res["ok"]
        if res["ok"]:
            st.success("Login berhasil! Sesi tersimpan untuk scraping berikutnya.")
        else:
            st.warning(res.get("message") or "Login gagal / dibatalkan.")

    if check_clicked:
        with st.spinner("Memeriksa profil sesi Instagram…"):
            ok = scraper.is_logged_in(real_chrome=real_chrome)
            st.session_state.logged_in = ok
        if ok:
            st.success("Sesi Instagram aktif.")
        else:
            st.warning("Belum ada sesi Instagram yang tersimpan.")

    with st.expander("Keamanan & batasan", expanded=False):
        st.markdown(
            """
            - Login hanya menyimpan **profil browser** di folder
              `sessions/instagram_profile`. Jangan bagikan folder `sessions/` —
              isinya cookie akun Anda.
            - Gunakan akun Anda sendiri dan login secara manual. Aplikasi tidak
              membaca kata sandi, tidak melewati CAPTCHA, dan tidak memakai proxy.
            - Tanpa login, Instagram biasanya hanya menampilkan **±10 komentar**
              dan **±12 post**.
            - Pencarian **kata kunci** memerlukan login; username dan hashtag
              bisa dibuka tanpa login dengan hasil terbatas.
            - Instagram dapat membatasi, mengubah, atau menolak permintaan kapan
              saja. Hasil tidak dijamin lengkap.
            """
        )

    st.divider()

    if _job_running and _active_job is not None:
        if st.button("⏹️ Hentikan scraping", width="stretch"):
            _active_job.stop_event.set()
            st.toast("Permintaan berhenti dikirim. Menunggu hasil sementara…")

    run_btn = st.button(
        "Scraping 🚀",
        type="primary",
        width="stretch",
        disabled=_job_running,
        help="Tunggu proses yang sedang berjalan selesai." if _job_running else None,
    )

    st.caption(
        "⚠️ Gunakan hanya untuk konten publik yang Anda berhak akses. Patuhi "
        "Ketentuan Layanan Instagram/Meta dan jangan kumpulkan data pribadi/sensitif."
    )

# --------------------------------------------------------------------------- #
# Mulai scraping
# --------------------------------------------------------------------------- #

if run_btn:
    validation_error = None
    normalized_post = ""
    normalized_content = ""

    try:
        if mode == "comments":
            normalized_post, _code = scraper.normalize_post_url(post_input)
        else:
            if not content_input.strip():
                raise ValueError(
                    "Masukkan username, kata kunci, atau hashtag terlebih dahulu."
                )
            if input_type == "username":
                normalized_content = scraper.normalize_username(content_input)
            elif input_type == "hashtag":
                normalized_content = scraper.normalize_hashtag(content_input)
            else:
                normalized_content = scraper.normalize_keyword(content_input)
            if start_date_c and end_date_c and start_date_c > end_date_c:
                raise ValueError(
                    'Tanggal "Dari" tidak boleh lebih besar dari "Sampai".'
                )
    except ValueError as exc:
        validation_error = str(exc)

    if validation_error:
        st.error(validation_error)
    else:
        job = ScrapeJob()
        st.session_state.job = job
        st.session_state.result = None
        st.session_state.job_finalized = False

        cfg = {
            "mode": mode,
            "post": normalized_post,
            "content": normalized_content,
            "input_type": input_type,
            "headless": bool(headless),
            "real_chrome": bool(real_chrome),
            "wait_ms": int(wait_s * 1000),
            "max_items": int(max_items),
            "max_minutes": int(max_minutes),
            "include_replies": bool(include_replies),
            "start_date": start_date_c,
            "end_date": end_date_c,
        }

        def _worker() -> None:
            try:
                if cfg["mode"] == "comments":
                    result = scraper.scrape_comments(
                        cfg["post"],
                        headless=cfg["headless"],
                        real_chrome=cfg["real_chrome"],
                        include_replies=cfg["include_replies"],
                        wait_ms=cfg["wait_ms"],
                        max_minutes=cfg["max_minutes"],
                        max_comments=cfg["max_items"],
                        stop_event=job.stop_event,
                        log_cb=job.log,
                    )
                else:
                    result = scraper.scrape_content(
                        cfg["content"],
                        input_type=cfg["input_type"],
                        headless=cfg["headless"],
                        real_chrome=cfg["real_chrome"],
                        wait_ms=cfg["wait_ms"],
                        max_minutes=cfg["max_minutes"],
                        max_posts=cfg["max_items"],
                        start_date=cfg["start_date"],
                        end_date=cfg["end_date"],
                        stop_event=job.stop_event,
                        log_cb=job.log,
                    )
            except Exception as exc:  # noqa: BLE001
                job.log(f"❌ Kesalahan di background worker: {exc}")
                result = {
                    "mode": cfg["mode"],
                    "rows": [],
                    "post": {},
                    "profile": {},
                    "query": {},
                    "logged_in": None,
                    "limited": False,
                    "error": "worker_error",
                    "message": f"Terjadi kesalahan di background worker: {exc}",
                }
            job.finish(result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()


# --------------------------------------------------------------------------- #
# Log live
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
            st.code(
                "\n".join(snap["logs"][-450:]) or "Menyiapkan browser…",
                language=None,
            )
            st.caption(f"⏱️ Berjalan {snap['elapsed']:.1f} detik")
        return
    st.rerun(scope="app")


_current_job = st.session_state.get("job")
_current_snap = _current_job.snapshot() if _current_job is not None else None
if _current_snap and _current_snap["running"]:
    _live_log()

# --------------------------------------------------------------------------- #
# Area utama
# --------------------------------------------------------------------------- #

result = st.session_state.result
job = st.session_state.get("job")
job_snap = job.snapshot() if job is not None else None

if job_snap and job_snap["running"]:
    st.title("Instagram Scraper")
    st.info(
        "Sedang scraping… Browser berjalan di latar belakang. Log proses tampil "
        "di atas; halaman ini otomatis diperbarui saat selesai."
    )
    st.stop()

if not isinstance(result, dict):
    if mode == "comments":
        st.markdown(
            """
            <div class="ig-hero">
                <div class="ig-kicker">Instagram Scraper · Komentar</div>
                <h2>Ubah komentar Instagram menjadi data yang rapi</h2>
                <p>Tempel link post atau reel, lalu ambil username, teks komentar,
                waktu, jumlah suka, balasan, dan status verifikasi — siap diunduh
                sebagai CSV/JSON.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            '<div class="ig-step"><div class="ig-step-title">1 · Salin link post</div>'
            'Buka post/reel → <b>Bagikan</b> → <b>Salin tautan</b>. Link pendek '
            'share Instagram juga didukung.</div>',
            unsafe_allow_html=True,
        )
        c2.markdown(
            '<div class="ig-step"><div class="ig-step-title">2 · Tempel & atur</div>'
            'Tempel link di sidebar. Centang <b>Sertakan balasan</b> bila ingin '
            'ikut mengambil balasan komentar.</div>',
            unsafe_allow_html=True,
        )
        c3.markdown(
            '<div class="ig-step"><div class="ig-step-title">3 · Scraping</div>'
            'Klik <b>Scraping 🚀</b>, lihat log live, lalu saring dan unduh '
            'hasilnya.</div>',
            unsafe_allow_html=True,
        )
        st.info(
            "Tanpa login, Instagram biasanya hanya menampilkan **±10 komentar**. "
            "Klik **🔑 Login** di sidebar dengan akun Anda sendiri untuk membuka "
            "lebih banyak komentar dan balasan."
        )
    else:
        st.markdown(
            """
            <div class="ig-hero">
                <div class="ig-kicker">Instagram Scraper · Konten</div>
                <h2>Kumpulkan postingan Instagram tanpa membuka satu per satu</h2>
                <p>Masukkan username, kata kunci, atau hashtag. Ambil caption,
                tanggal, jenis media, suka, komentar, tayangan, dan link post —
                rapi dalam tabel, ringkasan, serta CSV/JSON.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2, c3 = st.columns(3)
        c1.markdown(
            '<div class="ig-step"><div class="ig-step-title">1 · Pilih sumber</div>'
            '<b>Username</b> untuk satu akun, <b>Kata kunci</b> untuk pencarian, '
            'atau <b>Hashtag</b> untuk tag tertentu.</div>',
            unsafe_allow_html=True,
        )
        c2.markdown(
            '<div class="ig-step"><div class="ig-step-title">2 · Atur batas</div>'
            'Isi maksimum post atau biarkan <b>0</b> untuk mengambil semua yang '
            'tersedia. Filter tanggal bersifat opsional.</div>',
            unsafe_allow_html=True,
        )
        c3.markdown(
            '<div class="ig-step"><div class="ig-step-title">3 · Scraping</div>'
            'Klik <b>Scraping 🚀</b>. Login dianjurkan agar daftar post tidak '
            'dibatasi Instagram.</div>',
            unsafe_allow_html=True,
        )
        st.info(
            "**Username** dan **hashtag** bisa dicoba tanpa login, tetapi "
            "Instagram membatasi jumlah post untuk pengunjung. **Kata kunci** "
            "memerlukan login karena halaman pencariannya dialihkan ke login."
        )

    with st.expander("📌 Contoh input & tips", expanded=False):
        st.markdown(
            """
            | Mode | Contoh input |
            |---|---|
            | Komentar | `https://www.instagram.com/p/DfPQchkG1tg/` |
            | Komentar | `https://www.instagram.com/reel/DfsGt_WvvsH/` |
            | Konten · Username | `instagram` atau `@instagram` |
            | Konten · Kata kunci | `ballon d'or`, `resep rendang` |
            | Konten · Hashtag | `#ballondor` atau `ballondor` |

            **Tips**

            - Gunakan **link post**, bukan link profil, untuk mode komentar.
            - Login manual sekali lewat sidebar; sesi tersimpan di profil browser.
            - Bila hasil terasa sedikit, naikkan **Waktu maksimum** dan ulangi.
            - Instagram dapat membatasi permintaan setelah terlalu banyak scraping.
              Beri jeda beberapa menit bila itu terjadi.
            """
        )

    st.caption(
        "Data yang diambil adalah konten publik. Hormati privasi pemilik konten "
        "dan Ketentuan Layanan Instagram/Meta."
    )
    st.stop()

# --------------------------------------------------------------------------- #
# Error / hasil kosong
# --------------------------------------------------------------------------- #

rows = result.get("rows") or []
error = result.get("error")
message = result.get("message") or ""
result_mode = result.get("mode") or mode

if job_snap and not job_snap["running"] and job_snap["logs"]:
    with st.expander(f"📄 Log scraping · {job_snap['elapsed']:.1f} detik"):
        st.code("\n".join(job_snap["logs"]), language=None)

if error:
    if error in ("invalid_url", "invalid_input"):
        st.error(message)
    elif error == "login_required":
        st.warning("Instagram meminta login untuk data ini.")
        st.markdown(message)
        st.info(
            "Klik **🔑 Login** di sidebar, masuk dengan akun Anda sendiri di "
            "jendela browser yang terbuka, lalu ulangi scraping."
        )
    elif error in ("not_found", "private"):
        st.warning(message)
    elif error in ("no_comments", "no_posts"):
        st.warning(message)
    else:
        st.error(message or "Scraping gagal.")
    with st.expander("Detail teknis"):
        st.json({k: v for k, v in result.items() if k != "rows"})
    st.stop()

if not rows:
    st.warning(message or "Tidak ada data yang berhasil dibaca.")
    st.info(
        "Coba lagi dengan **Waktu maksimum** lebih besar, pastikan sudah login, "
        "atau gunakan input lain."
    )
    st.stop()

# --------------------------------------------------------------------------- #
# Hasil — header
# --------------------------------------------------------------------------- #

if result_mode == "comments":
    post = result.get("post") or {}
    username = post.get("username") or ""
    title = f"Komentar @{username}" if username else "Komentar post"
    st.subheader(title)

    meta_bits = []
    if post.get("tanggal"):
        meta_bits.append(f"📅 {post['tanggal']}")
    if post.get("media"):
        meta_bits.append(f"🖼️ {post['media']}")
    if post.get("suka"):
        meta_bits.append(f"❤️ {_fmt_int(post['suka'])} suka")
    if post.get("komentar"):
        meta_bits.append(f"💬 {_fmt_int(post['komentar'])} komentar di post")
    if meta_bits:
        st.caption(" · ".join(meta_bits))

    if post.get("caption"):
        st.markdown(
            f'<div class="ig-quote">{html.escape(_short(post["caption"], 420))}</div>',
            unsafe_allow_html=True,
        )

    link_col, _ = st.columns([1, 4])
    if _safe_url(post.get("url")):
        link_col.link_button("Buka post ↗", _safe_url(post["url"]), width="stretch")

    if result.get("limited"):
        st.warning(
            "🚧 Hasil dibatasi karena Anda belum login. Instagram biasanya hanya "
            "menampilkan ±10 komentar untuk pengunjung. Login lewat sidebar untuk "
            "membuka lebih banyak."
        )
    elif message:
        st.info(message)
else:
    profile = result.get("profile") or {}
    query = result.get("query") or {}
    label = query.get("label") or profile.get("username") or "Instagram"
    if query.get("input_type") == "username":
        title = f"Konten @{profile.get('username') or query.get('target')}"
    elif query.get("input_type") == "hashtag":
        title = f"Konten {label}"
    else:
        title = f"Konten untuk {label}"
    st.subheader(title)

    meta_bits = []
    if profile.get("nama") and profile.get("nama") != profile.get("username"):
        meta_bits.append(profile["nama"])
    if profile.get("followers"):
        meta_bits.append(f"{_fmt_int(profile['followers'])} follower")
    if profile.get("following"):
        meta_bits.append(f"{_fmt_int(profile['following'])} following")
    if profile.get("jumlah_post"):
        meta_bits.append(f"{_fmt_int(profile['jumlah_post'])} post di akun")
    total_available = int(result.get("total_available") or 0)
    if total_available and total_available != int(profile.get("jumlah_post") or 0):
        meta_bits.append(f"{_fmt_int(total_available)} post tercantum")
    if meta_bits:
        st.caption(" · ".join(meta_bits))

    if profile.get("bio"):
        st.markdown(
            f'<div class="ig-quote">{html.escape(_short(profile["bio"], 350))}</div>',
            unsafe_allow_html=True,
        )

    if profile.get("akun_teratas"):
        st.caption(
            "Akun paling sering muncul: "
            + ", ".join(f"@{name}" for name in profile["akun_teratas"])
        )

    link_col, _ = st.columns([1, 4])
    if _safe_url(profile.get("url")):
        link_col.link_button("Buka profil ↗", _safe_url(profile["url"]), width="stretch")
    elif _safe_url(query.get("url")):
        link_col.link_button("Buka sumber ↗", _safe_url(query["url"]), width="stretch")

    if result.get("limited"):
        st.warning(
            "🚧 Hasil dibatasi karena Anda belum login. Instagram membatasi "
            "jumlah post yang dapat dimuat untuk pengunjung. Login lewat sidebar "
            "untuk membuka lebih banyak."
        )
    elif message:
        st.info(message)

# --------------------------------------------------------------------------- #
# Metrik
# --------------------------------------------------------------------------- #

df = pd.DataFrame(rows)
numeric_columns = (
    ("suka", "balasan")
    if result_mode == "comments"
    else ("suka", "komentar", "tayangan")
)
for column in numeric_columns:
    if column in df.columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        if result_mode == "comments":
            df[column] = df[column].fillna(0).astype(int)

if "username" in df.columns:
    df["username"] = df["username"].fillna("").astype(str)

if result_mode == "comments":
    verified_total = int(df["VERIFIED"].fillna(False).astype(bool).sum()) if "VERIFIED" in df else 0
    replies_total = int((df["jenis"] == "Balasan").sum()) if "jenis" in df else 0
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Komentar terkumpul", _fmt_int(len(df)))
    m2.metric("Komentator unik", _fmt_int(df["username"].nunique()))
    m3.metric("Total suka", _fmt_int(df["suka"].sum()))
    m4.metric("Balasan", _fmt_int(replies_total))
    m5.metric("Akun terverifikasi", _fmt_int(verified_total))
else:
    accounts = df["username"].nunique() if "username" in df else 0

    def _sum_or_dash(column: str) -> str:
        if column not in df.columns:
            return "—"
        series = pd.to_numeric(df[column], errors="coerce")
        if series.isna().all() or not series.fillna(0).ne(0).any():
            return "—"
        return _fmt_int(series.sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Post terkumpul", _fmt_int(len(df)))
    m2.metric("Akun unik", _fmt_int(accounts))
    m3.metric("Total suka", _sum_or_dash("suka"))
    m4.metric("Total komentar", _sum_or_dash("komentar"))
    m5.metric("Total tayangan", _sum_or_dash("tayangan"))

    missing_metrics = [
        label
        for label, column in (
            ("suka", "suka"),
            ("komentar", "komentar"),
            ("tayangan", "tayangan"),
        )
        if column in df.columns
        and (
            pd.to_numeric(df[column], errors="coerce").isna().all()
            or not pd.to_numeric(df[column], errors="coerce").fillna(0).ne(0).any()
        )
    ]
    if missing_metrics:
        st.caption(
            "ℹ️ Instagram tidak menampilkan metrik "
            + ", ".join(missing_metrics)
            + " untuk pengunjung pada sumber ini; nilai ditampilkan sebagai —. "
            "Login dapat membuka metrik yang lebih lengkap."
        )

# --------------------------------------------------------------------------- #
# Filter & urutan
# --------------------------------------------------------------------------- #

with st.expander("🔎 Filter & urutan", expanded=True):
    if result_mode == "comments":
        f1, f2, f3 = st.columns([1.1, 2, 1.4])
        selected_types = f1.multiselect(
            "Jenis",
            ["Komentar", "Balasan"],
            default=[],
            placeholder="Semua jenis",
        )
        keyword = f2.text_input(
            "Cari kata di komentar / username",
            placeholder="mis. keren, harga, jakarta",
        )
        sort_label = f3.selectbox(
            "Urutkan",
            ["Terbaru", "Terlama", "Paling disukai", "Username A–Z"],
        )
        verified_only = st.checkbox("Hanya akun terverifikasi", value=False)

        filtered = df.copy()
        if selected_types:
            filtered = filtered[filtered["jenis"].isin(selected_types)]
        if keyword.strip():
            needle = keyword.strip().lower()
            filtered = filtered[
                filtered["komentar"].fillna("").str.lower().str.contains(needle, regex=False)
                | filtered["username"].fillna("").str.lower().str.contains(needle, regex=False)
            ]
        if verified_only:
            filtered = filtered[filtered["VERIFIED"].fillna(False).astype(bool)]

        if sort_label == "Terbaru":
            filtered = filtered.sort_values("waktu", ascending=False)
        elif sort_label == "Terlama":
            filtered = filtered.sort_values("waktu", ascending=True)
        elif sort_label == "Paling disukai":
            filtered = filtered.sort_values(["suka", "waktu"], ascending=[False, False])
        else:
            filtered = filtered.sort_values("username", ascending=True)
    else:
        f1, f2, f3 = st.columns([1.2, 2, 1.4])
        media_options = sorted(
            {str(value) for value in df.get("media", pd.Series(dtype=str)).dropna() if str(value)}
        )
        selected_media = f1.multiselect(
            "Jenis media",
            media_options,
            default=[],
            placeholder="Semua media",
        )
        keyword = f2.text_input(
            "Cari kata di caption / username",
            placeholder="mis. ballon d'or, resep, tutorial",
        )
        sort_label = f3.selectbox(
            "Urutkan",
            [
                "Terbaru",
                "Terlama",
                "Paling disukai",
                "Paling dikomentari",
                "Paling ditonton",
            ],
        )

        filtered = df.copy()
        if selected_media:
            filtered = filtered[filtered["media"].isin(selected_media)]
        if keyword.strip():
            needle = keyword.strip().lower()
            filtered = filtered[
                filtered["caption"].fillna("").str.lower().str.contains(needle, regex=False)
                | filtered["username"].fillna("").str.lower().str.contains(needle, regex=False)
            ]
        if sort_label == "Terbaru":
            filtered = filtered.sort_values("waktu", ascending=False)
        elif sort_label == "Terlama":
            filtered = filtered.sort_values("waktu", ascending=True)
        elif sort_label == "Paling disukai":
            filtered = filtered.sort_values(["suka", "waktu"], ascending=[False, False])
        elif sort_label == "Paling dikomentari":
            filtered = filtered.sort_values(["komentar", "waktu"], ascending=[False, False])
        else:
            filtered = filtered.sort_values(["tayangan", "suka"], ascending=[False, False])

st.caption(
    f"Menampilkan **{_fmt_int(len(filtered))}** dari **{_fmt_int(len(df))}** baris."
)

# --------------------------------------------------------------------------- #
# Tabel & ringkasan
# --------------------------------------------------------------------------- #

if result_mode == "comments":
    tab_table, tab_summary = st.tabs(["📋 Tabel komentar", "📈 Ringkasan"])
    with tab_table:
        if filtered.empty:
            st.info("Tidak ada komentar yang cocok dengan filter.")
        else:
            cols = [
                "id",
                "id_komentar_utama",
                "id_komentar",
                "waktu",
                "username",
                "komentar",
                "suka",
                "balasan",
                "jenis",
                "VERIFIED",
                "profil",
            ]
            cols = [column for column in cols if column in filtered.columns]
            st.dataframe(
                filtered[cols],
                width="stretch",
                height=580,
                hide_index=True,
                column_config={
                    "id": st.column_config.TextColumn(
                        "ID (utama_balasan)", width="medium"
                    ),
                    "id_komentar_utama": st.column_config.TextColumn(
                        "ID komentar utama", width="medium"
                    ),
                    "id_komentar": st.column_config.TextColumn(
                        "ID komentar", width="medium"
                    ),
                    "waktu": st.column_config.TextColumn("Waktu (GMT+7)"),
                    "username": st.column_config.TextColumn("Username"),
                    "komentar": st.column_config.TextColumn("Komentar", width="large"),
                    "suka": st.column_config.NumberColumn("Suka"),
                    "balasan": st.column_config.NumberColumn("Jumlah balasan"),
                    "jenis": st.column_config.TextColumn("Jenis"),
                    "VERIFIED": st.column_config.CheckboxColumn("Verified"),
                    "profil": st.column_config.LinkColumn("Profil", display_text="buka ↗"),
                },
            )

    with tab_summary:
        if filtered.empty:
            st.info("Belum ada data untuk diringkas.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Komentar per hari")
                days = (
                    pd.to_datetime(filtered["tanggal"], errors="coerce")
                    .dropna()
                    .dt.date.value_counts()
                    .sort_index()
                )
                if days.empty:
                    st.info("Tanggal tidak tersedia.")
                else:
                    st.bar_chart(days, color="#c13584")
            with c2:
                st.subheader("10 komentator teratas")
                top = filtered["username"].value_counts().head(10)
                if top.empty:
                    st.info("Username tidak tersedia.")
                else:
                    st.bar_chart(top, horizontal=True, color="#e1306c")

            c3, c4 = st.columns(2)
            with c3:
                st.subheader("Komposisi jenis")
                jenis = filtered["jenis"].value_counts()
                st.bar_chart(jenis, color="#fd8a3c")
            with c4:
                st.subheader("10 komentar paling disukai")
                favorite = (
                    filtered.nlargest(10, "suka")[["username", "komentar", "suka"]]
                    .assign(
                        label=lambda data: data["username"].fillna("")
                        + " · "
                        + data["komentar"].fillna("").str.slice(0, 38)
                    )
                    .set_index("label")["suka"]
                )
                if favorite.empty or not filtered["suka"].ne(0).any():
                    st.info("Data suka tidak tersedia untuk komentar ini.")
                else:
                    st.bar_chart(favorite, horizontal=True, color="#833ab4")
else:
    tab_table, tab_summary = st.tabs(["📋 Tabel konten", "📈 Ringkasan"])
    with tab_table:
        if filtered.empty:
            st.info("Tidak ada post yang cocok dengan filter.")
        else:
            table = filtered.copy()
            unavailable_metrics = [
                metric
                for metric in ("suka", "komentar", "tayangan")
                if metric in table.columns
                and pd.to_numeric(table[metric], errors="coerce").isna().all()
            ]
            if "username" in table.columns:
                table["profil"] = table["username"].apply(
                    lambda value: f"https://www.instagram.com/{value}/" if value else ""
                )
            cols = [
                "waktu",
                "username",
                "caption",
                "media",
                "suka",
                "komentar",
                "tayangan",
                "tanggal_perkiraan",
                "profil",
                "url",
            ]
            cols = [
                column
                for column in cols
                if column in table.columns and column not in unavailable_metrics
            ]
            st.dataframe(
                table[cols],
                width="stretch",
                height=580,
                hide_index=True,
                column_config={
                    "id": st.column_config.TextColumn(
                        "ID (utama_balasan)", width="medium"
                    ),
                    "id_komentar_utama": st.column_config.TextColumn(
                        "ID komentar utama", width="medium"
                    ),
                    "id_komentar": st.column_config.TextColumn(
                        "ID komentar", width="medium"
                    ),
                    "waktu": st.column_config.TextColumn("Waktu (GMT+7)"),
                    "username": st.column_config.TextColumn("Username"),
                    "caption": st.column_config.TextColumn("Caption", width="large"),
                    "media": st.column_config.TextColumn("Media"),
                    "suka": st.column_config.NumberColumn("Suka"),
                    "komentar": st.column_config.NumberColumn("Komentar"),
                    "tayangan": st.column_config.NumberColumn("Tayangan"),
                    "tanggal_perkiraan": st.column_config.CheckboxColumn("Tanggal perkiraan"),
                    "profil": st.column_config.LinkColumn("Profil", display_text="buka ↗"),
                    "url": st.column_config.LinkColumn("Post", display_text="buka ↗"),
                },
            )

    with tab_summary:
        if filtered.empty:
            st.info("Belum ada data untuk diringkas.")
        else:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Post per hari")
                days = (
                    pd.to_datetime(filtered["tanggal"], errors="coerce")
                    .dropna()
                    .dt.date.value_counts()
                    .sort_index()
                )
                if days.empty:
                    st.info("Tanggal tidak tersedia.")
                else:
                    st.bar_chart(days, color="#c13584")
            with c2:
                st.subheader("Komposisi media")
                media = filtered["media"].value_counts()
                st.bar_chart(media, color="#fd8a3c")

            c3, c4 = st.columns(2)
            with c3:
                st.subheader("10 akun paling sering muncul")
                top_accounts = filtered["username"].value_counts().head(10)
                st.bar_chart(top_accounts, horizontal=True, color="#e1306c")
            with c4:
                st.subheader("10 post paling disukai")
                top_posts = (
                    filtered.nlargest(10, "suka")[["username", "caption", "suka"]]
                    .assign(
                        label=lambda data: data["username"].fillna("")
                        + " · "
                        + data["caption"].fillna("").str.slice(0, 34)
                    )
                    .set_index("label")["suka"]
                )
                if (
                    top_posts.empty
                    or not filtered["suka"].notna().any()
                    or not filtered["suka"].ne(0).any()
                ):
                    st.info("Data suka tidak tersedia untuk sumber ini.")
                else:
                    st.bar_chart(top_posts, horizontal=True, color="#833ab4")

            if filtered.get("tanggal_perkiraan", pd.Series(dtype=bool)).any():
                st.caption(
                    "ℹ️ Sebagian tanggal adalah **perkiraan** yang dihitung dari ID "
                    "media Instagram, karena Instagram tidak menampilkan timestamp "
                    "asli untuk pengunjung."
                )

# --------------------------------------------------------------------------- #
# Ekspor
# --------------------------------------------------------------------------- #

st.divider()
st.subheader("Ekspor hasil")

if result_mode == "comments":
    post = result.get("post") or {}
    safe_name = _safe_filename(post.get("kode_post") or "komentar")
    file_base = f"instagram_komentar_{safe_name}"
else:
    query = result.get("query") or {}
    safe_name = _safe_filename(query.get("target") or "konten")
    file_base = f"instagram_konten_{safe_name}"

d1, d2, _ = st.columns([1, 1, 3])
d1.download_button(
    "⬇️ Unduh CSV",
    data=scraper.rows_to_csv(rows, result_mode).encode("utf-8-sig"),
    file_name=f"{file_base}.csv",
    mime="text/csv",
    width="stretch",
)
d2.download_button(
    "⬇️ Unduh JSON",
    data=scraper.rows_to_json(rows, result).encode("utf-8"),
    file_name=f"{file_base}.json",
    mime="application/json",
    width="stretch",
)

st.caption(
    "Hormati privasi pemilik konten. Jangan menyebarkan data pribadi tanpa dasar "
    "yang sah, dan patuhi Ketentuan Layanan Instagram/Meta."
)
