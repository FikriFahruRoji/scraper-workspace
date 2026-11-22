"""Scraping harga saham (OHLCV) dari API chart Yahoo Finance.

Endpoint yang dipakai (tanpa API key):

    GET https://query2.finance.yahoo.com/v8/finance/chart/{symbol}
        ?period1=<epoch>&period2=<epoch>&interval=1d
        &events=div,splits&includeAdjustedClose=true

Permintaan dijalankan di browser Scrapling StealthySession. Host
``query2`` dan ``query1`` dicoba dengan retry dan backoff.

Semua fungsi publik melempar :class:`ScraperError` dengan pesan berbahasa
Indonesia yang aman ditampilkan langsung ke pengguna.
"""

from __future__ import annotations

import math
import re
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from stealth_transport import StealthTransport

# --------------------------------------------------------------------------- #
# Konstanta
# --------------------------------------------------------------------------- #

BASE_URLS = (
    "https://query2.finance.yahoo.com",
    "https://query1.finance.yahoo.com",
)
SIMPLE_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

INTERVAL_LABELS = {
    "1d": "Harian",
    "1wk": "Mingguan",
    "1mo": "Bulanan",
}
SUPPORTED_INTERVALS = tuple(INTERVAL_LABELS)

MARKET_LABELS = {
    "IDX": "Indonesia (IDX)",
    "US": "Amerika Serikat",
    "CRYPTO": "Kripto",
    "FX": "Kurs (mata uang)",
    "CUSTOM": "Kustom",
}

_JAKARTA = ZoneInfo("Asia/Jakarta")
_SYMBOL_PREFIX = re.compile(r"^(?:IDX|NASDAQ|NYSE|AMEX|BURSA|YAHOO)[:\s]+", re.I)
_YAHOO_URL = re.compile(r"finance\.yahoo\.com/quote/([A-Za-z0-9.\-=^]+)", re.I)
_SYMBOL_OK = re.compile(r"^[A-Z0-9.\-=^]+$")


class ScraperError(RuntimeError):
    """Kesalahan yang bisa ditampilkan langsung ke pengguna."""


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def _log(log_cb: Optional[Callable[[str], None]], message: str) -> None:
    """Kirim log ke UI; kegagalan callback tidak boleh menggagalkan scraping."""
    if log_cb is None:
        return
    try:
        log_cb(f"{datetime.now(_JAKARTA):%H:%M:%S}  {message}")
    except Exception:  # noqa: BLE001 - callback UI tidak boleh mematikan worker
        pass


def _as_float(value: Any) -> Optional[float]:
    """Konversi nilai apa pun menjadi float terbatas; None jika tidak valid."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _at(values: Any, index: int) -> Any:
    if isinstance(values, (list, tuple)) and 0 <= index < len(values):
        return values[index]
    return None


def _zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(str(name)) if name else _JAKARTA
    except (KeyError, ValueError, TypeError):
        return _JAKARTA


def _epoch_to_iso(value: Any, tz: ZoneInfo) -> Optional[str]:
    number = _as_float(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _date_to_epoch_utc(value: date) -> int:
    """Awal hari UTC untuk tanggal kalender (dipakai period1/period2 Yahoo)."""
    return int(datetime.combine(value, dt_time.min, tzinfo=timezone.utc).timestamp())


# --------------------------------------------------------------------------- #
# Normalisasi input
# --------------------------------------------------------------------------- #


def normalize_symbol(value: str, market: str = "IDX") -> str:
    """Ubah input pengguna menjadi kode Yahoo Finance yang dipakai API.

    Contoh:
      - ``"bbca"`` + IDX      -> ``BBCA.JK``
      - ``"BBCA.JK"`` + IDX   -> ``BBCA.JK``
      - ``"aapl"`` + US       -> ``AAPL``
      - ``"btc"`` + CRYPTO    -> ``BTC-USD``
      - ``"^jkse"`` + IDX     -> ``^JKSE``
      - ``"usd/idr"`` + FX    -> ``USDIDR=X``  (``"idr"`` alone means USD to IDR)
    """
    raw = (value or "").strip()
    if not raw:
        raise ScraperError("Kode saham belum diisi.")

    url_match = _YAHOO_URL.search(raw)
    if url_match:
        raw = url_match.group(1)

    raw = _SYMBOL_PREFIX.sub("", raw).strip().upper().replace(" ", "").lstrip("$")
    if (market or "").upper() == "FX":
        raw = raw.replace("/", "").replace("-", "").replace("_", "")
    if not raw:
        raise ScraperError("Kode saham belum diisi.")
    if len(raw) > 32:
        raise ScraperError("Kode saham terlalu panjang (maksimum 32 karakter).")
    if not _SYMBOL_OK.fullmatch(raw):
        raise ScraperError(
            "Kode saham mengandung karakter yang tidak valid. "
            "Gunakan huruf, angka, titik, minus, atau ^ (contoh: BBCA.JK)."
        )

    market = (market or "IDX").upper()
    if market == "IDX":
        if "." not in raw and not raw.startswith("^"):
            raw += ".JK"
    elif market == "CRYPTO":
        if "-" not in raw:
            raw += "-USD"
    elif market == "FX":
        if not raw.endswith("=X"):
            if len(raw) not in (3, 6) or not raw.isalpha():
                raise ScraperError("Kode kurs berupa pasangan tiga huruf, misalnya USD/IDR atau EURIDR.")
            raw += "=X"
    return raw


def validate_range(start_date: date, end_date: date) -> None:
    """Pastikan rentang tanggal masuk akal sebelum memanggil API."""
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise ScraperError("Rentang tanggal belum lengkap.")
    if start_date > end_date:
        raise ScraperError('Tanggal "Dari" tidak boleh lebih besar dari "Sampai".')

    today = datetime.now(_JAKARTA).date()
    if end_date > today:
        raise ScraperError("Tanggal akhir melewati hari ini.")
    if start_date > today:
        raise ScraperError("Tanggal awal melewati hari ini.")


# --------------------------------------------------------------------------- #
# HTTP + retry
# --------------------------------------------------------------------------- #


def _not_found_message(symbol: str) -> str:
    return (
        f"Kode saham '{symbol}' tidak ditemukan di Yahoo Finance. "
        "Periksa ejaan dan pasar yang dipilih. Saham Indonesia biasanya memakai "
        "akhiran .JK (mis. BBCA.JK), sedangkan kripto memakai pasangan seperti "
        "BTC-USD."
    )


def _no_data_message(symbol: str) -> str:
    return (
        f"Yahoo Finance tidak memiliki data untuk {symbol} pada rentang ini. "
        "Kemungkinan rentang hanya berisi akhir pekan/hari libur, atau kode tidak "
        "diperdagangkan pada periode tersebut. Coba rentang yang lebih panjang."
    )


def _fetch_chart(
    symbol: str,
    params: dict[str, Any],
    *,
    timeout: int,
    retries: int,
    log_cb: Optional[Callable[[str], None]],
    stop_event=None, real_chrome=True,
) -> dict[str, Any]:
    """Ambil ``chart.result[0]`` dari Yahoo dengan host fallback + retry."""
    attempts = max(1, int(retries))
    timeout = max(5, int(timeout))
    last_error = "Tidak ada respons."

    try:
        session = StealthTransport(timeout=timeout, real_chrome=real_chrome)
    except Exception as exc:  # noqa: BLE001
        raise ScraperError(f"Tidak dapat menyiapkan HTTP client: {exc}") from exc
    session.headers.update(SIMPLE_HEADERS)

    try:
        for attempt in range(1, attempts + 1):
            for base in BASE_URLS:
                if stop_event is not None and stop_event.is_set():
                    raise ScraperError("Pekerjaan dihentikan.")
                host = base.split("//", 1)[-1]
                _log(log_cb, f"🌐 {host} · percobaan {attempt}/{attempts}")

                url = f"{base}/v8/finance/chart/{quote(symbol, safe='^.-=')}"
                try:
                    response = session.get(url, params=params, timeout=timeout)
                except Exception as exc:  # noqa: BLE001 - termasuk timeout/TLS
                    last_error = f"Koneksi gagal: {exc}"
                    _log(log_cb, f"⚠️ {last_error}")
                    continue

                if response.status_code == 404:
                    raise ScraperError(_not_found_message(symbol))
                if response.status_code == 429:
                    last_error = "Yahoo Finance membatasi permintaan (HTTP 429)."
                    _log(log_cb, f"⚠️ {last_error}")
                    continue
                if response.status_code in (400, 404, 422):
                    # Yahoo memakai HTTP 400 untuk rentang tanpa data.
                    try:
                        error_payload = response.json()
                    except Exception:  # noqa: BLE001
                        error_payload = {}
                    error_detail = (
                        (error_payload.get("chart") or {}).get("error") or {}
                    )
                    description = str(error_detail.get("description") or "")
                    lowered = description.lower()
                    if response.status_code == 404 or "not found" in lowered:
                        raise ScraperError(_not_found_message(symbol))
                    if (
                        "doesn't exist" in lowered
                        or "does not exist" in lowered
                        or "no data" in lowered
                        or "delisted" in lowered
                    ):
                        raise ScraperError(_no_data_message(symbol))
                    raise ScraperError(
                        "Yahoo Finance menolak permintaan"
                        + (f": {description}" if description else f" (HTTP {response.status_code}).")
                    )
                if response.status_code >= 500:
                    last_error = f"Server Yahoo mengembalikan HTTP {response.status_code}."
                    _log(log_cb, f"⚠️ {last_error}")
                    continue
                if response.status_code != 200:
                    last_error = f"Yahoo mengembalikan HTTP {response.status_code}."
                    _log(log_cb, f"⚠️ {last_error}")
                    continue

                try:
                    payload = response.json()
                except Exception:  # noqa: BLE001
                    last_error = "Respons Yahoo bukan JSON yang valid."
                    _log(log_cb, f"⚠️ {last_error}")
                    continue

                chart = payload.get("chart") or {}
                error = chart.get("error")
                if error:
                    description = error.get("description") or error.get("code") or "error"
                    code = str(error.get("code") or "").lower()
                    if code == "not found":
                        raise ScraperError(_not_found_message(symbol))
                    raise ScraperError(f"Yahoo Finance menolak permintaan: {description}")

                result = chart.get("result") or []
                if not result:
                    raise ScraperError(_not_found_message(symbol))

                _log(log_cb, f"✅ Respons OK (HTTP 200) dari {host}")
                return result[0]

            if attempt < attempts:
                delay = min(2 ** (attempt - 1), 8)
                _log(log_cb, f"⏳ Menunggu {delay} detik sebelum mencoba lagi…")
                if stop_event is not None:
                    stop_event.wait(delay)
                else:
                    time.sleep(delay)

        raise ScraperError(
            f"Gagal mengambil data dari Yahoo Finance setelah {attempts} percobaan. "
            f"{last_error} Tunggu 1–2 menit lalu coba lagi."
        )
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Parsing respons
# --------------------------------------------------------------------------- #


def _parse_events(
    events: Any,
    key: str,
    tz: ZoneInfo,
) -> list[dict[str, Any]]:
    """Ubah events dividen/split Yahoo menjadi daftar yang sederhana."""
    raw = (events or {}).get(key) or {}
    items = raw.values() if isinstance(raw, dict) else raw
    rows: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        timestamp = item.get("date")
        if timestamp is None:
            continue
        try:
            event_date = datetime.fromtimestamp(int(timestamp), tz).date().isoformat()
        except (OverflowError, OSError, TypeError, ValueError):
            continue

        if key == "dividends":
            amount = _as_float(item.get("amount"))
            if amount is None:
                continue
            rows.append(
                {
                    "type": "dividend",
                    "date": event_date,
                    "amount": amount,
                    "ratio": None,
                }
            )
        else:
            numerator = _as_float(item.get("numerator"))
            denominator = _as_float(item.get("denominator"))
            ratio = item.get("splitRatio")
            if not ratio and numerator and denominator:
                ratio = f"{numerator:g}:{denominator:g}"
            rows.append(
                {
                    "type": "split",
                    "date": event_date,
                    "amount": None,
                    "ratio": ratio or "",
                }
            )

    return sorted(rows, key=lambda row: row["date"])


def _parse_meta(meta: dict[str, Any], requested_symbol: str, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "symbol": meta.get("symbol") or requested_symbol,
        "name": meta.get("longName")
        or meta.get("shortName")
        or meta.get("symbol")
        or requested_symbol,
        "short_name": meta.get("shortName") or "",
        "currency": meta.get("currency") or "",
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
        "exchange_code": meta.get("exchangeName") or "",
        "instrument_type": meta.get("instrumentType") or "",
        "timezone": meta.get("exchangeTimezoneName") or str(tz),
        "timezone_short": meta.get("timezone") or "",
        "regular_market_price": _as_float(meta.get("regularMarketPrice")),
        "regular_market_change_pct": _as_float(meta.get("regularMarketChangePercent")),
        "regular_market_volume": _as_float(meta.get("regularMarketVolume")),
        "fifty_two_week_high": _as_float(meta.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _as_float(meta.get("fiftyTwoWeekLow")),
        "chart_previous_close": _as_float(meta.get("chartPreviousClose")),
        "regular_market_time": _epoch_to_iso(meta.get("regularMarketTime"), tz),
        "first_trade_date": _epoch_to_iso(meta.get("firstTradeDate"), tz),
    }


def _parse_chart(
    result: dict[str, Any],
    *,
    requested_symbol: str,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[str], bool]:
    """Kembalikan (meta, rows, events, warnings, ada_bar_berjalan)."""
    meta_raw = result.get("meta") or {}
    tz = _zone(meta_raw.get("exchangeTimezoneName"))
    meta = _parse_meta(meta_raw, requested_symbol, tz)

    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote_blocks = indicators.get("quote") or []
    quote = quote_blocks[0] if quote_blocks and isinstance(quote_blocks[0], dict) else {}
    adj_blocks = indicators.get("adjclose") or []
    adj_values = (
        adj_blocks[0].get("adjclose")
        if adj_blocks and isinstance(adj_blocks[0], dict)
        else []
    )

    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    live_price = meta.get("regular_market_price")
    live_volume = meta.get("regular_market_volume")
    regular_time = meta_raw.get("regularMarketTime")
    regular_day = (
        datetime.fromtimestamp(float(regular_time), tz).date()
        if _as_float(regular_time) is not None
        else None
    )

    warnings: list[str] = []
    rows_by_date: dict[str, dict[str, Any]] = {}
    has_partial_bar = False
    last_index = len(timestamps) - 1

    for index, timestamp in enumerate(timestamps):
        try:
            bar_day = datetime.fromtimestamp(int(timestamp), tz).date()
        except (OverflowError, OSError, TypeError, ValueError):
            continue
        if bar_day < start_date or bar_day > end_date:
            continue

        close = _as_float(_at(closes, index))
        open_price = _as_float(_at(opens, index))
        high = _as_float(_at(highs, index))
        low = _as_float(_at(lows, index))
        volume = _as_float(_at(volumes, index))
        adjusted = _as_float(_at(adj_values, index))

        # Bar hari berjalan biasanya belum punya close. Isi dengan harga pasar
        # terkini hanya jika tanggal bar sama dengan tanggal regularMarketTime.
        if close is None and index == last_index and live_price is not None:
            if regular_day is None or bar_day == regular_day:
                close = live_price
                volume = volume if volume is not None else live_volume
                has_partial_bar = True

        if close is None:
            continue

        open_price = close if open_price is None else open_price
        high = max(open_price, close) if high is None else high
        low = min(open_price, close) if low is None else low
        volume = 0.0 if volume is None else volume
        adjusted = close if adjusted is None else adjusted

        rows_by_date[bar_day.isoformat()] = {
            "date": bar_day.isoformat(),
            "open": round(open_price, 6),
            "high": round(high, 6),
            "low": round(low, 6),
            "close": round(close, 6),
            "adj_close": round(adjusted, 6),
            "volume": int(volume),
            "change_pct": None,
        }

    rows = [rows_by_date[key] for key in sorted(rows_by_date)]
    # Baris pertama dibandingkan dengan close sebelum rentang (meta Yahoo),
    # sehingga kolom perubahan tidak pernah kosong untuk data normal.
    previous: Optional[float] = meta.get("chart_previous_close")
    for row in rows:
        if previous is not None and previous != 0:
            row["change_pct"] = round((row["close"] / previous - 1) * 100, 4)
        previous = row["close"]

    if not adj_values:
        warnings.append(
            "Adj Close tidak tersedia dari Yahoo untuk rentang ini; "
            "kolomnya diisi sama dengan Close."
        )
    if has_partial_bar:
        warnings.append(
            "Bar terakhir belum memiliki close final; harga close diisi harga pasar "
            "terkini dari Yahoo Finance."
        )

    events = _parse_events(result.get("events"), "dividends", tz) + _parse_events(
        result.get("events"), "splits", tz
    )
    events.sort(key=lambda row: (row["date"], row["type"]), reverse=True)

    return meta, rows, events, warnings, has_partial_bar


# --------------------------------------------------------------------------- #
# API publik
# --------------------------------------------------------------------------- #


def scrape_prices(
    symbol: str,
    start_date: date,
    end_date: date,
    interval: str = "1d",
    market: str = "IDX",
    timeout: int = 25,
    retries: int = 3,
    log_cb: Optional[Callable[[str], None]] = None,
    stop_event=None, real_chrome=True,
) -> dict[str, Any]:
    """Ambil OHLCV, dividen, dan split untuk satu kode saham.

    Returns:
        Dict berisi ``symbol``, ``meta``, ``rows``, ``events``, ``warnings``,
        ``interval``, ``start``, ``end``, ``fetched_at``, dan ``source``.

    Raises:
        ScraperError: input tidak valid, kode tidak ditemukan, atau Yahoo gagal.
    """
    normalized = normalize_symbol(symbol, market)
    validate_range(start_date, end_date)
    if interval not in SUPPORTED_INTERVALS:
        raise ScraperError(
            f"Interval '{interval}' tidak didukung. Pilih Harian, Mingguan, atau Bulanan."
        )

    _log(log_cb, f"🔎 Kode Yahoo: {normalized}")
    _log(
        log_cb,
        f"📅 Rentang: {start_date.isoformat()} s/d {end_date.isoformat()} "
        f"· {INTERVAL_LABELS[interval]}",
    )

    params = {
        "period1": _date_to_epoch_utc(start_date),
        # period2 eksklusif; tambah 1 hari agar tanggal akhir ikut.
        "period2": _date_to_epoch_utc(end_date + timedelta(days=1)),
        "interval": interval,
        "events": "div,splits",
        "includeAdjustedClose": "true",
    }

    result = _fetch_chart(
        normalized,
        params,
        timeout=timeout,
        retries=retries,
        log_cb=log_cb, stop_event=stop_event, real_chrome=real_chrome,
    )
    raw_count = len(result.get("timestamp") or [])
    _log(log_cb, f"📦 {raw_count} baris mentah diterima")

    _log(log_cb, "🧮 Memproses OHLCV, dividen, dan split…")
    meta, rows, events, warnings, has_partial_bar = _parse_chart(
        result,
        requested_symbol=normalized,
        start_date=start_date,
        end_date=end_date,
    )

    if not rows:
        raise ScraperError(
            f"Tidak ada data harga untuk {normalized} pada "
            f"{start_date.isoformat()}–{end_date.isoformat()}. "
            "Coba rentang yang lebih panjang, atau pastikan tanggal tidak hanya "
            "mencakup akhir pekan/hari libur."
        )

    _log(
        log_cb,
        f"✅ Selesai: {len(rows)} baris · {meta.get('name') or normalized}",
    )
    if events:
        _log(log_cb, f"💰 {len(events)} event dividen/split ditemukan")

    return {
        "ok": True,
        "symbol": meta.get("symbol") or normalized,
        "market": market,
        "interval": interval,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "meta": meta,
        "rows": rows,
        "events": events,
        "warnings": warnings,
        "has_partial_bar": has_partial_bar,
        "source": "Yahoo Finance Chart API",
        "fetched_at": datetime.now(_JAKARTA).isoformat(timespec="seconds"),
    }
