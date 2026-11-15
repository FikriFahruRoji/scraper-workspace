"""Antarmuka Streamlit untuk Scraper Harga Saham Yahoo Finance.

Jalankan dengan:
    streamlit run app.py
"""

from __future__ import annotations

import calendar
import importlib
import json
import math
import re
import threading
import time
from datetime import date, datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import scraper

# Streamlit menyimpan modul yang sudah di-import di memori. Reload agar
# perubahan pada scraper.py langsung terpakai saat rerun, tanpa restart server.
importlib.reload(scraper)

st.set_page_config(
    page_title="Scraper Harga Saham",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="auto",
)

# --------------------------------------------------------------------------- #
# Konstanta & helper tampilan
# --------------------------------------------------------------------------- #

_JAKARTA = ZoneInfo("Asia/Jakarta")
_MONTHS_ID = (
    "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
)
_INTERVAL_OPTIONS = {"Harian": "1d", "Mingguan": "1wk", "Bulanan": "1mo"}
_PRESETS = (
    "1 bulan", "3 bulan", "6 bulan", "1 tahun",
    "3 tahun", "5 tahun", "YTD", "Maksimum", "Kustom",
)
_CURRENCY_SYMBOL = {
    "IDR": "Rp",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "SGD": "S$",
    "HKD": "HK$",
    "AUD": "A$",
    "CAD": "C$",
    "CNY": "CN¥",
    "KRW": "₩",
    "INR": "₹",
    "MYR": "RM",
    "THB": "฿",
}

st.markdown(
    """
    <style>
        .block-container { padding-top: 1.2rem; max-width: 1480px; }
        div[data-testid="stMetricValue"] { font-size: 1.5rem; }
        [data-testid="stSidebar"] h1 { font-size: 1.5rem; line-height: 1.25; }
        #MainMenu, footer, [data-testid="stAppDeployButton"] { visibility: hidden; }
        /* Di layar kecil, toolbar memuat tombol buka sidebar — jangan disembunyikan. */
        @media (min-width: 641px) {
            [data-testid="stToolbar"] { visibility: hidden; }
        }

        .stock-hero {
            border: 1px solid #dbe7e3;
            border-radius: 18px;
            padding: 1.4rem 1.6rem;
            background: linear-gradient(135deg, #f7fdfb 0%, #eaf6f2 100%);
            margin-bottom: 1rem;
        }
        .stock-hero h2 { margin: 0 0 .4rem 0; font-size: 1.55rem; color: #134e4a; }
        .stock-hero p { margin: 0; color: #4b5563; max-width: 900px; }

        .stock-step {
            border: 1px solid #e7ebf1;
            border-radius: 14px;
            padding: .9rem 1rem;
            background: #fff;
            min-height: 112px;
            color: #374151;
        }
        .stock-step b { color: #0f766e; }
        .stock-muted { color: #6b7280; font-size: .9rem; }
        .stock-code {
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            background: #f1f5f4;
            border: 1px solid #e2e8e6;
            border-radius: 6px;
            padding: .05rem .35rem;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def _fmt_date(value: date | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return f"{value.day} {_MONTHS_ID[value.month - 1]} {value.year}"


def _fmt_datetime(value: Optional[str]) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{_fmt_date(moment.date())} {moment:%H:%M}"


def _fmt_price(value: Any, currency: str = "") -> str:
    number = scraper._as_float(value)  # helper numerik yang sama dengan scraper
    if number is None:
        return "—"
    code = (currency or "").upper()
    symbol = _CURRENCY_SYMBOL.get(code, "")
    sign = "-" if number < 0 else ""
    absolute = abs(number)

    if code == "IDR":
        return f"{sign}Rp {absolute:,.0f}".replace(",", ".")
    if symbol:
        decimals = 0 if code in {"JPY", "KRW", "VND"} else 2
        return f"{sign}{symbol}{absolute:,.{decimals}f}"
    return f"{number:,.2f} {code}".strip()


def _fmt_pct(value: Any, signed: bool = True) -> str:
    number = scraper._as_float(value)
    if number is None:
        return "—"
    return f"{number:+.2f}%" if signed else f"{number:.2f}%"


def _fmt_volume(value: Any) -> str:
    number = scraper._as_float(value)
    if number is None:
        return "—"
    absolute = abs(number)
    sign = "-" if number < 0 else ""
    for unit, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if absolute >= divisor:
            return f"{sign}{absolute / divisor:,.1f}{unit}"
    return f"{sign}{absolute:,.0f}"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", (value or "").strip()).strip("_")
    return (cleaned or "saham")[:60]


def _shift_months(value: date, months: int) -> date:
    total = value.year * 12 + (value.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _preset_range(preset: str, today: date) -> Optional[tuple[date, date]]:
    if preset == "1 bulan":
        return _shift_months(today, 1), today
    if preset == "3 bulan":
        return _shift_months(today, 3), today
    if preset == "6 bulan":
        return _shift_months(today, 6), today
    if preset == "1 tahun":
        return _shift_months(today, 12), today
    if preset == "3 tahun":
        return _shift_months(today, 36), today
    if preset == "5 tahun":
        return _shift_months(today, 60), today
    if preset == "YTD":
        return date(today.year, 1, 1), today
    if preset == "Maksimum":
        return date(1970, 1, 2), today
    return None


def _price_axis_prefix(currency: str) -> str:
    code = (currency or "").upper()
    if code == "IDR":
        return "Rp "
    if code == "USD":
        return "$"
    symbol = _CURRENCY_SYMBOL.get(code)
    return f"{symbol} " if symbol else ""


def _compute_stats(df: pd.DataFrame, interval: str) -> dict[str, Any]:
    close = df["close"].astype(float)
    returns = close.pct_change().dropna()
    periods_per_year = {"1d": 252, "1wk": 52, "1mo": 12}.get(interval, 252)

    volatility = (
        float(returns.std(ddof=1) * math.sqrt(periods_per_year) * 100)
        if len(returns) > 1
        else None
    )
    drawdown = close / close.cummax() - 1
    max_drawdown = float(drawdown.min() * 100) if len(drawdown) else None
    period_return = (
        float((close.iloc[-1] / close.iloc[0] - 1) * 100) if len(close) > 1 else None
    )

    best_return = worst_return = None
    best_date = worst_date = None
    if len(returns):
        best_return = float(returns.max() * 100)
        worst_return = float(returns.min() * 100)
        best_date = df.loc[returns.idxmax(), "date"]
        worst_date = df.loc[returns.idxmin(), "date"]

    return {
        "period_return": period_return,
        "volatility": volatility,
        "max_drawdown": max_drawdown,
        "best_return": best_return,
        "worst_return": worst_return,
        "best_date": best_date,
        "worst_date": worst_date,
    }


def _monthly_returns(df: pd.DataFrame) -> pd.Series:
    frame = df.copy()
    frame["period"] = pd.to_datetime(frame["date"]).dt.to_period("M")
    last_close = frame.groupby("period")["close"].last()
    return (last_close.pct_change().dropna() * 100).tail(36)


def _drawdown_series(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    close = df["close"].astype(float)
    dates = pd.to_datetime(df["date"])
    return dates, (close / close.cummax() - 1) * 100


# --------------------------------------------------------------------------- #
# State sesi + job thread-safe
# --------------------------------------------------------------------------- #

_DEFAULTS = {
    "result": None,
    "job": None,
    "job_finalized": False,
    "chart_type": "Candlestick",
    "show_ma": True,
}
for _key, _value in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _value


class ScrapeJob:
    """Wadah thread-safe untuk log & hasil.

    Worker thread tidak boleh menyentuh ``st.session_state``. Semua komunikasi
    dilakukan lewat objek ini dengan lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[str] = []
        self._result: Optional[dict[str, Any]] = None
        self._running = True
        self._finished_at: Optional[float] = None
        self.started_at = time.time()

    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(str(message))

    def finish(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._result = result
            self._running = False
            self._finished_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "logs": list(self._logs),
                "result": self._result,
                "running": self._running,
                "elapsed": (self._finished_at or time.time()) - self.started_at,
            }


# Finalisasi di thread utama Streamlit, sebelum sidebar dirender.
_job = st.session_state.get("job")
_job_snap = _job.snapshot() if _job is not None else None
if _job_snap and not _job_snap["running"] and not st.session_state.job_finalized:
    st.session_state.result = _job_snap["result"]
    st.session_state.job_finalized = True
    _job_snap = _job.snapshot()


# --------------------------------------------------------------------------- #
# Sidebar — kontrol
# --------------------------------------------------------------------------- #

with st.sidebar:
    _active_job = st.session_state.get("job")
    _job_running = bool(_active_job and _active_job.snapshot()["running"])

    st.title("📈 Scraper Harga Saham")
    st.caption("Ambil OHLCV, dividen, dan split dari Yahoo Finance.")

    symbol_input = st.text_input(
        "Kode saham",
        placeholder="BBCA, AAPL, BTC-USD",
        help=(
            "Masukkan kode saham tanpa atau dengan akhiran Yahoo. Untuk saham "
            "Indonesia, akhiran .JK ditambahkan otomatis."
        ),
    )

    market = st.selectbox(
        "Pasar",
        options=list(scraper.MARKET_LABELS),
        format_func=lambda key: scraper.MARKET_LABELS[key],
        index=0,
        help=(
            "IDX menambahkan .JK otomatis. Kripto menambahkan -USD bila belum "
            "ada. Pilih Kustom untuk memakai kode Yahoo apa adanya."
        ),
    )

    try:
        _preview = (
            scraper.normalize_symbol(symbol_input, market)
            if symbol_input.strip()
            else ""
        )
    except scraper.ScraperError as exc:
        _preview = ""
        st.caption(f"⚠️ {exc}")

    if _preview and _preview != symbol_input.strip().upper():
        st.caption(f"Kode Yahoo: **{_preview}**")

    today = datetime.now(_JAKARTA).date()
    preset = st.selectbox(
        "Rentang waktu",
        list(_PRESETS),
        index=3,
        help="Pilih rentang cepat, atau Kustom untuk memilih tanggal sendiri.",
    )
    preset_dates = _preset_range(preset, today)
    if preset_dates:
        start_date, end_date = preset_dates
        st.caption(f"{_fmt_date(start_date)} – {_fmt_date(end_date)}")
    else:
        col_a, col_b = st.columns(2)
        start_date = col_a.date_input(
            "Dari",
            value=_shift_months(today, 12),
            min_value=date(1970, 1, 2),
            max_value=today,
            key="custom_start",
        )
        end_date = col_b.date_input(
            "Sampai",
            value=today,
            min_value=date(1970, 1, 2),
            max_value=today,
            key="custom_end",
        )

    interval_label = st.selectbox(
        "Interval",
        list(_INTERVAL_OPTIONS),
        index=0,
        help="Harian paling detail; mingguan/bulanan cocok untuk tren jangka panjang.",
    )
    interval = _INTERVAL_OPTIONS[interval_label]

    with st.expander("Opsi lanjutan", expanded=False):
        timeout = st.slider(
            "Timeout permintaan (detik)",
            min_value=10,
            max_value=60,
            value=25,
            step=5,
        )
        retries = st.slider(
            "Jumlah percobaan",
            min_value=1,
            max_value=5,
            value=3,
            help="Dipakai saat Yahoo membatasi atau gagal merespons.",
        )

    st.divider()
    run_btn = st.button(
        "Ambil data 🚀",
        type="primary",
        width="stretch",
        disabled=_job_running,
        help="Tunggu proses yang sedang berjalan selesai." if _job_running else None,
    )

    st.caption(
        "Data Yahoo Finance dapat tertunda dan tidak selalu akurat. "
        "Bukan saran investasi."
    )

# --------------------------------------------------------------------------- #
# Mulai pengambilan data (background thread + log live)
# --------------------------------------------------------------------------- #

if run_btn:
    try:
        normalized_input = scraper.normalize_symbol(symbol_input, market)
        scraper.validate_range(start_date, end_date)
        validation_error = None
    except scraper.ScraperError as exc:
        normalized_input = ""
        validation_error = str(exc)

    if validation_error:
        st.error(validation_error)
    else:
        job = ScrapeJob()
        st.session_state.job = job
        st.session_state.result = None
        st.session_state.job_finalized = False

        cfg = {
            "symbol": symbol_input,
            "normalized": normalized_input,
            "start": start_date,
            "end": end_date,
            "interval": interval,
            "market": market,
            "timeout": int(timeout),
            "retries": int(retries),
        }

        def _worker() -> None:
            try:
                result = scraper.scrape_prices(
                    cfg["symbol"],
                    cfg["start"],
                    cfg["end"],
                    interval=cfg["interval"],
                    market=cfg["market"],
                    timeout=cfg["timeout"],
                    retries=cfg["retries"],
                    log_cb=job.log,
                )
            except scraper.ScraperError as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "symbol": cfg["normalized"],
                    "market": cfg["market"],
                    "interval": cfg["interval"],
                    "start": cfg["start"].isoformat(),
                    "end": cfg["end"].isoformat(),
                }
            except Exception as exc:  # noqa: BLE001 - jangan biarkan worker mati diam
                result = {
                    "ok": False,
                    "error": f"Kesalahan tak terduga di background worker: {exc}",
                    "symbol": cfg["normalized"],
                    "market": cfg["market"],
                    "interval": cfg["interval"],
                    "start": cfg["start"].isoformat(),
                    "end": cfg["end"].isoformat(),
                }
            job.finish(result)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()


# --------------------------------------------------------------------------- #
# Log live
# --------------------------------------------------------------------------- #


@st.fragment(run_every="0.6s")
def _live_log() -> None:
    job = st.session_state.get("job")
    if job is None:
        return
    snap = job.snapshot()
    if snap["running"]:
        with st.container(border=True):
            st.markdown("**📄 Log pengambilan data**")
            st.code(
                "\n".join(snap["logs"]) or "Menyiapkan permintaan…",
                language=None,
            )
        return

    # Memicu full rerun agar hasil difinalisasi di thread utama.
    st.rerun(scope="app")


_current_job = st.session_state.get("job")
_current_snap = _current_job.snapshot() if _current_job is not None else None

# Antisipasi job yang selesai setelah blok finalisasi di atas: jika snapshot
# terbaru sudah selesai, finalisasi sekarang agar UI tidak tetap di halaman awal.
if (
    _current_snap
    and not _current_snap["running"]
    and not st.session_state.job_finalized
):
    st.session_state.result = _current_snap["result"]
    st.session_state.job_finalized = True

# --------------------------------------------------------------------------- #
# Area utama
# --------------------------------------------------------------------------- #

result = st.session_state.result

if _current_snap and _current_snap["running"]:
    _live_log()
    st.title("Mengambil data harga…")
    st.info(
        "Permintaan sedang diproses di latar belakang. Log tampil di atas dan "
        "halaman akan diperbarui otomatis setelah selesai."
    )
    st.stop()

# ---- Belum ada hasil ------------------------------------------------------- #

if not isinstance(result, dict):
    st.markdown(
        """
        <div class="stock-hero">
            <h2>Dari kode saham menjadi data harga yang siap diolah</h2>
            <p>Masukkan kode saham dan rentang waktu. Aplikasi mengambil OHLCV,
            dividen, dan split dari Yahoo Finance, lalu menyajikannya dalam
            grafik, tabel, ringkasan statistik, serta ekspor CSV/JSON.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns(3)
    c1.markdown(
        '<div class="stock-step"><b>1 · Pilih pasar</b><br>IDX, Amerika Serikat, '
        'kripto, atau kode Yahoo kustom.</div>',
        unsafe_allow_html=True,
    )
    c2.markdown(
        '<div class="stock-step"><b>2 · Isi kode & rentang</b><br>Contoh '
        '<span class="stock-code">BBCA</span> untuk IDX atau '
        '<span class="stock-code">AAPL</span> untuk AS.</div>',
        unsafe_allow_html=True,
    )
    c3.markdown(
        '<div class="stock-step"><b>3 · Ambil data</b><br>Klik '
        '<b>Ambil data 🚀</b>, lihat grafik, lalu unduh CSV/JSON.</div>',
        unsafe_allow_html=True,
    )

    st.markdown("#### Contoh kode")
    e1, e2, e3 = st.columns(3)
    e1.markdown(
        "**🇮🇩 Indonesia (IDX)**\n\n`BBCA` · `TLKM` · `ASII` · `BMRI` · `^JKSE`"
    )
    e2.markdown(
        "**🇺🇸 Amerika Serikat**\n\n`AAPL` · `MSFT` · `TSLA` · `NVDA` · `SPY`"
    )
    e3.markdown(
        "**₿ Kripto**\n\n`BTC-USD` · `ETH-USD` · `SOL-USD`"
    )

    st.info(
        "Saham IDX otomatis memakai akhiran **.JK** — cukup ketik `BBCA`, bukan "
        "`BBCA.JK`. Untuk kode lain, pilih pasar **Kustom** agar kode dipakai "
        "apa adanya."
    )
    st.caption(
        "Sumber data: Yahoo Finance Chart API. Harga dapat tertunda 15–20 menit "
        "dan tidak dijamin lengkap. Gunakan untuk riset, bukan sebagai saran investasi."
    )
    st.stop()

# ---- Gagal mengambil data -------------------------------------------------- #

if not result.get("ok"):
    st.title("Data gagal diambil")
    st.error(result.get("error") or "Terjadi kesalahan yang tidak diketahui.")
    st.markdown(
        f"Kode: `{result.get('symbol', '—')}` · "
        f"Rentang: {_fmt_date(result.get('start'))} – {_fmt_date(result.get('end'))}"
    )
    with st.expander("💡 Yang bisa dicoba", expanded=True):
        st.markdown(
            """
            - Periksa **kode saham** dan **pasar** yang dipilih.
            - Saham Indonesia memakai akhiran `.JK` (mis. `BBCA.JK`); kripto
              memakai pasangan seperti `BTC-USD`.
            - Jika pesan menyebut **HTTP 429**, tunggu 1–2 menit lalu coba lagi.
            - Coba rentang waktu yang lebih panjang; akhir pekan/hari libur saja
              biasanya tidak menghasilkan bar.
            """
        )
    if _current_snap and _current_snap["logs"]:
        with st.expander(f"📄 Log · {_current_snap['elapsed']:.1f} detik"):
            st.code("\n".join(_current_snap["logs"]), language=None)
    st.stop()

# ---- Hasil ---------------------------------------------------------------- #

rows = result.get("rows") or []
meta = result.get("meta") or {}
events = result.get("events") or []
warnings = result.get("warnings") or []
df = pd.DataFrame(rows)

if df.empty:
    st.error("Yahoo Finance tidak mengembalikan baris harga.")
    st.stop()

symbol = result.get("symbol") or meta.get("symbol") or "—"
currency = meta.get("currency") or ""
interval = result.get("interval") or "1d"
interval_label = scraper.INTERVAL_LABELS.get(interval, interval)
stats = _compute_stats(df, interval)
name = meta.get("name") or symbol

st.markdown(f"## {name}")
st.caption(
    f"`{symbol}` · {meta.get('exchange') or '—'} · {currency or 'mata uang —'} · "
    f"{interval_label} · {_fmt_date(result.get('start'))} – "
    f"{_fmt_date(result.get('end'))} · Sumber: Yahoo Finance"
)
if meta.get("regular_market_time"):
    st.caption(
        f"Data pasar per {_fmt_datetime(meta.get('regular_market_time'))} "
        f"{meta.get('timezone_short') or ''} · diambil "
        f"{_fmt_datetime(result.get('fetched_at'))} WIB"
    )

for warning in warnings:
    st.warning(warning)

# ---- Metrik utama ---------------------------------------------------------- #

last_price = meta.get("regular_market_price")
if last_price is None:
    last_price = df["close"].iloc[-1]
day_change = meta.get("regular_market_change_pct")

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric(
    "Harga terakhir",
    _fmt_price(last_price, currency),
    delta=_fmt_pct(day_change) if day_change is not None else None,
    help="Harga pasar terakhir dari Yahoo Finance; dapat tertunda.",
)
m2.metric(
    "Perubahan periode",
    _fmt_pct(stats["period_return"]),
    help="Perubahan close pertama ke close terakhir pada rentang yang dipilih.",
)
m3.metric("Tertinggi", _fmt_price(df["high"].max(), currency))
m4.metric("Terendah", _fmt_price(df["low"].min(), currency))
m5.metric("Volume rata-rata", _fmt_volume(df["volume"].mean()))

# --------------------------------------------------------------------------- #
# Grafik
# --------------------------------------------------------------------------- #


def _price_figure(
    frame: pd.DataFrame,
    info: dict[str, Any],
    chart_type: str,
    show_ma: bool,
) -> go.Figure:
    dates = pd.to_datetime(frame["date"])
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.76, 0.24],
    )

    if chart_type == "Garis":
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=frame["close"],
                mode="lines",
                name="Close",
                line={"color": "#0f766e", "width": 2.2},
                hovertemplate="%{y:,.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )
    else:
        fig.add_trace(
            go.Candlestick(
                x=dates,
                open=frame["open"],
                high=frame["high"],
                low=frame["low"],
                close=frame["close"],
                name="Harga",
                increasing_line_color="#0f9f6e",
                increasing_fillcolor="#0f9f6e",
                decreasing_line_color="#dc2626",
                decreasing_fillcolor="#dc2626",
                showlegend=False,
            ),
            row=1,
            col=1,
        )

    if show_ma:
        for window, color in ((20, "#2563eb"), (50, "#f59e0b")):
            moving_average = frame["close"].astype(float).rolling(window).mean()
            if moving_average.notna().sum() >= 2:
                fig.add_trace(
                    go.Scatter(
                        x=dates,
                        y=moving_average,
                        mode="lines",
                        name=f"MA{window}",
                        line={"color": color, "width": 1.3},
                        hovertemplate="%{y:,.2f}<extra>MA"
                        + str(window)
                        + "</extra>",
                    ),
                    row=1,
                    col=1,
                )

    volume_colors = [
        "#86efac" if close >= open_price else "#fca5a5"
        for open_price, close in zip(frame["open"], frame["close"])
    ]
    fig.add_trace(
        go.Bar(
            x=dates,
            y=frame["volume"],
            name="Volume",
            marker_color=volume_colors,
            showlegend=False,
            hovertemplate="%{y:,.0f}<extra>Volume</extra>",
        ),
        row=2,
        col=1,
    )

    code = (info.get("currency") or "").upper()
    prefix = _price_axis_prefix(code)
    fig.update_yaxes(
        title_text=f"Harga ({code})" if code else "Harga",
        tickprefix=prefix,
        gridcolor="#e8eeec",
        row=1,
        col=1,
    )
    fig.update_yaxes(
        title_text="Volume",
        tickformat="~s",
        gridcolor="#f1f5f4",
        row=2,
        col=1,
    )
    fig.update_xaxes(showgrid=False, row=1, col=1)
    fig.update_xaxes(showgrid=False, row=2, col=1)
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    if result.get("interval") == "1d":
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    elif result.get("interval") == "1wk":
        fig.update_xaxes(tickformat="%d %b %Y", row=2, col=1)
    else:
        fig.update_xaxes(tickformat="%b %Y", row=2, col=1)

    fig.update_layout(
        template="plotly_white",
        height=610,
        margin={"l": 10, "r": 10, "t": 36, "b": 10},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.01,
            "x": 0,
        },
        hovermode="x unified",
        bargap=0.15,
        xaxis_rangeslider_visible=False,
    )
    return fig


def _monthly_figure(monthly: pd.Series) -> go.Figure:
    labels = [str(period) for period in monthly.index]
    values = monthly.values
    colors = ["#0f9f6e" if value >= 0 else "#dc2626" for value in values]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=values,
            marker_color=colors,
            text=[f"{value:+.1f}%" for value in values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}<br>%{y:+.2f}%<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_color="#9ca3af", line_width=1)
    fig.update_layout(
        template="plotly_white",
        height=310,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        yaxis_title="Return (%)",
        showlegend=False,
        bargap=0.25,
    )
    fig.update_xaxes(tickangle=-45)
    return fig


def _drawdown_figure(dates: pd.Series, drawdown: pd.Series) -> go.Figure:
    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=drawdown,
            mode="lines",
            fill="tozeroy",
            line={"color": "#dc2626", "width": 1.4},
            hovertemplate="%{x|%d %b %Y}<br>%{y:.2f}%<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_color="#9ca3af", line_width=1)
    fig.update_layout(
        template="plotly_white",
        height=310,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        yaxis_title="Drawdown (%)",
        showlegend=False,
    )
    return fig


tab_chart, tab_table, tab_summary, tab_events = st.tabs(
    ["📈 Grafik", "📋 Tabel", "📊 Ringkasan", "💰 Dividen & Split"]
)

with tab_chart:
    chart_col, ma_col = st.columns([2, 1])
    chart_type = chart_col.radio(
        "Jenis grafik",
        ["Candlestick", "Garis"],
        horizontal=True,
        key="chart_type",
    )
    show_ma = ma_col.checkbox(
        "Tampilkan MA20 & MA50",
        value=True,
        key="show_ma",
        help="Moving average sederhana dari harga close.",
    )

    st.plotly_chart(
        _price_figure(df, meta, chart_type, show_ma),
        config={
            "displaylogo": False,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
        },
        key="price_chart",
    )
    if chart_type == "Garis":
        st.caption(
            "Garis memakai harga close. Volume diwarnai hijau saat close naik "
            "dan merah saat close turun."
        )
    else:
        st.caption(
            "Candlestick memakai harga OHLC mentah. Volume diwarnai hijau saat "
            "close naik dan merah saat close turun."
        )

with tab_table:
    table = df.copy()
    table["date"] = pd.to_datetime(table["date"]).dt.date
    table = table.rename(
        columns={
            "date": "Tanggal",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "adj_close": "Adj Close",
            "volume": "Volume",
            "change_pct": "Perubahan %",
        }
    )
    table = table[
        ["Tanggal", "Open", "High", "Low", "Close", "Adj Close", "Volume", "Perubahan %"]
    ]
    table["Perubahan %"] = pd.to_numeric(table["Perubahan %"], errors="coerce")

    number_format = "%,.0f" if currency.upper() == "IDR" else "%,.2f"
    st.dataframe(
        table,
        width="stretch",
        height=560,
        hide_index=True,
        column_config={
            "Tanggal": st.column_config.DateColumn("Tanggal", format="DD MMM YYYY"),
            "Open": st.column_config.NumberColumn("Open", format=number_format),
            "High": st.column_config.NumberColumn("High", format=number_format),
            "Low": st.column_config.NumberColumn("Low", format=number_format),
            "Close": st.column_config.NumberColumn("Close", format=number_format),
            "Adj Close": st.column_config.NumberColumn(
                "Adj Close", format=number_format
            ),
            "Volume": st.column_config.NumberColumn("Volume", format="%,d"),
            "Perubahan %": st.column_config.NumberColumn(
                "Perubahan %", format="%+.2f%%"
            ),
        },
    )
    st.caption(
        f"{len(df)} baris · harga dalam {currency or 'mata uang tidak diketahui'} · "
        "Perubahan % dihitung terhadap close sebelumnya."
    )

with tab_summary:
    s1, s2, s3, s4 = st.columns(4)
    s1.metric(
        "Volatilitas tahunan",
        _fmt_pct(stats["volatility"], signed=False),
        help=f"Standar deviasi return per {interval_label.lower()}, disetahunkan.",
    )
    s2.metric(
        "Max drawdown",
        _fmt_pct(stats["max_drawdown"], signed=False),
        help="Penurunan terbesar dari puncak sebelumnya pada rentang ini.",
    )
    s3.metric(
        "Return terbaik",
        _fmt_pct(stats["best_return"]),
        help=(
            f"Pada {_fmt_date(stats['best_date'])}"
            if stats["best_date"]
            else "Tidak cukup data."
        ),
    )
    s4.metric(
        "Return terburuk",
        _fmt_pct(stats["worst_return"]),
        help=(
            f"Pada {_fmt_date(stats['worst_date'])}"
            if stats["worst_date"]
            else "Tidak cukup data."
        ),
    )

    left, right = st.columns(2)
    with left:
        st.markdown("**Return per bulan**")
        monthly = _monthly_returns(df)
        if monthly.empty:
            st.info("Rentang terlalu pendek untuk menghitung return bulanan.")
        else:
            st.plotly_chart(
                _monthly_figure(monthly),
                config={"displaylogo": False},
                key="monthly_chart",
            )
            if len(monthly) >= 36:
                st.caption("Ditampilkan hingga 36 bulan terakhir.")
    with right:
        st.markdown("**Drawdown**")
        dates, drawdown = _drawdown_series(df)
        st.plotly_chart(
            _drawdown_figure(dates, drawdown),
            config={"displaylogo": False},
            key="drawdown_chart",
        )

    st.markdown("**Rincian data**")
    detail = pd.DataFrame(
        [
            ("Baris data", f"{len(df):,}".replace(",", ".")),
            ("Close pertama", _fmt_price(df["close"].iloc[0], currency)),
            ("Close terakhir", _fmt_price(df["close"].iloc[-1], currency)),
            ("Total volume", _fmt_volume(df["volume"].sum())),
            ("Rata-rata volume", _fmt_volume(df["volume"].mean())),
            ("52 minggu tertinggi", _fmt_price(meta.get("fifty_two_week_high"), currency)),
            ("52 minggu terendah", _fmt_price(meta.get("fifty_two_week_low"), currency)),
            ("Close sebelumnya", _fmt_price(meta.get("chart_previous_close"), currency)),
        ],
        columns=["Metrik", "Nilai"],
    )
    st.dataframe(detail, width="stretch", hide_index=True)

with tab_events:
    if not events:
        st.info(
            "Yahoo Finance tidak mencatat dividen atau split pada rentang ini. "
            "Saham yang tidak membayar dividen juga akan tampil kosong."
        )
    else:
        dividend_total = sum(
            event["amount"] for event in events if event["type"] == "dividend"
        )
        if dividend_total:
            st.metric(
                "Total dividen pada periode",
                _fmt_price(dividend_total, currency),
                help="Jumlah dividen per saham pada rentang ini, sebelum pajak.",
            )

        event_frame = pd.DataFrame(events)
        event_frame["Tanggal"] = pd.to_datetime(event_frame["date"]).dt.date
        event_frame["Jenis"] = event_frame["type"].map(
            {"dividend": "Dividen", "split": "Split"}
        )
        event_frame["Jumlah"] = pd.to_numeric(event_frame["amount"], errors="coerce")
        event_frame["Rasio"] = event_frame["ratio"].fillna("")
        event_frame = event_frame[["Tanggal", "Jenis", "Jumlah", "Rasio"]]
        number_format = "%,.0f" if currency.upper() == "IDR" else "%,.2f"

        st.dataframe(
            event_frame,
            width="stretch",
            hide_index=True,
            column_config={
                "Tanggal": st.column_config.DateColumn(
                    "Tanggal", format="DD MMM YYYY"
                ),
                "Jenis": st.column_config.TextColumn("Jenis"),
                "Jumlah": st.column_config.NumberColumn(
                    "Jumlah per saham", format=number_format
                ),
                "Rasio": st.column_config.TextColumn("Rasio split"),
            },
        )
        st.caption(
            f"Nilai dividen dalam {currency or 'mata uang saham'}, belum "
            "memperhitungkan pajak."
        )

# --------------------------------------------------------------------------- #
# Ekspor + log
# --------------------------------------------------------------------------- #

st.divider()
st.subheader("Ekspor hasil")

csv_frame = df.copy()
csv_frame.insert(0, "symbol", symbol)
csv_bytes = csv_frame.to_csv(index=False).encode("utf-8-sig")
json_payload = {
    "symbol": symbol,
    "meta": meta,
    "interval": interval,
    "range": {"start": result.get("start"), "end": result.get("end")},
    "rows": rows,
    "events": events,
    "warnings": result.get("warnings") or [],
    "has_partial_bar": bool(result.get("has_partial_bar")),
    "source": result.get("source"),
    "fetched_at": result.get("fetched_at"),
}
json_bytes = json.dumps(json_payload, ensure_ascii=False, indent=2).encode("utf-8")

safe_symbol = _safe_filename(symbol.replace(".", "_"))
file_suffix = (
    f"{safe_symbol}_{result.get('start')}_{result.get('end')}_{interval}"
)
d1, d2, _ = st.columns([1, 1, 3])
d1.download_button(
    "⬇️ Unduh CSV",
    data=csv_bytes,
    file_name=f"{file_suffix}.csv",
    mime="text/csv",
    width="stretch",
)
d2.download_button(
    "⬇️ Unduh JSON",
    data=json_bytes,
    file_name=f"{file_suffix}.json",
    mime="application/json",
    width="stretch",
)

if _current_snap and _current_snap["logs"]:
    with st.expander(f"📄 Log pengambilan data · {_current_snap['elapsed']:.1f} detik"):
        st.code("\n".join(_current_snap["logs"]), language=None)

st.caption(
    "Data disediakan oleh Yahoo Finance dan dapat tertunda, tidak lengkap, atau "
    "berubah tanpa pemberitahuan. Aplikasi ini bukan alat rekomendasi investasi."
)
