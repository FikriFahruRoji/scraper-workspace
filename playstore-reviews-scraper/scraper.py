"""Scraping ulasan/komentar aplikasi Google Play.

Modul ini diadaptasi dari script unofficial
`amrrs/Google-Play-Store-Review-Extractor
<https://github.com/amrrs/Google-Play-Store-Review-Extractor>`_.
Script asli memakai ``google-play-scraper`` untuk mengambil ulasan lewat
permintaan HTTP, lalu menyimpannya sebagai CSV/JSON. Di proyek ini logika
tersebut dibungkus agar bisa dipakai Streamlit: ada progres per batch,
percobaan ulang, pembatalan, dan metadata aplikasi.

Catatan:
  - ``google-play-scraper`` adalah pustaka **tidak resmi** (unofficial), bukan
    API Google. Google dapat mengubah atau membatasi endpoint-nya kapan saja.
  - Gunakan hanya untuk ulasan publik, dengan izin/otorisasi yang sesuai, dan
    patuhi Ketentuan Layanan Google Play serta hukum yang berlaku.
  - Aplikasi ini tidak melewati CAPTCHA, paywall, atau kontrol akses apa pun.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

from google_play_scraper import Sort
from play_stealth import PlayStealthClient
from functools import wraps
from google_play_scraper.exceptions import NotFoundError

# --------------------------------------------------------------------------- #
# Konstanta
# --------------------------------------------------------------------------- #

SORT_MAP = {
    "relevan": Sort.MOST_RELEVANT,
    "terbaru": Sort.NEWEST,
    "rating": Sort.RATING,
}

SORT_LABELS = {
    "relevan": "Paling relevan",
    "terbaru": "Terbaru",
    "rating": "Rating",
}

BATCH_SIZE = 200              # ukuran batch maksimum yang didukung Play
STAGNANT_BATCH_LIMIT = 5      # hentikan bila N batch tidak menghasilkan baris baru
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0

_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
_ALLOWED_HOSTS = {"play.google.com", "market.android.com"}

CSV_FIELDS = [
    "id_ulasan",
    "nama",
    "rating",
    "tanggal",
    "waktu",
    "ulasan",
    "suka",
    "versi_aplikasi",
    "versi_dibuat",
    "balasan",
    "tanggal_balasan",
    "foto_profil",
    "aplikasi",
    "kode_aplikasi",
]

# --------------------------------------------------------------------------- #
# Input aplikasi
# --------------------------------------------------------------------------- #


def normalize_app_input(value: str) -> str:
    """Ubah input pengguna menjadi kode aplikasi (package id).

    Menerima kode aplikasi seperti ``com.whatsapp`` atau link Play Store
    (``https://play.google.com/store/apps/details?id=com.whatsapp``).

    Raises:
        ValueError: bila input kosong, host bukan Play Store, atau kode tidak valid.
    """

    text = (value or "").strip()
    if not text:
        raise ValueError(
            "Masukkan kode aplikasi (mis. com.whatsapp) atau link Play Store-nya."
        )

    if "://" in text or text.lower().startswith(("play.google.com", "market.android.com")):
        candidate = text if "://" in text else f"https://{text}"
        parsed = urlparse(candidate)
        host = (parsed.netloc or "").lower().split(":")[0]
        if host not in _ALLOWED_HOSTS:
            raise ValueError(
                "Link harus berasal dari play.google.com, mis. "
                "https://play.google.com/store/apps/details?id=com.whatsapp"
            )
        ids = parse_qs(parsed.query).get("id") or []
        if not ids:
            raise ValueError(
                "Link Play Store tidak memuat parameter ?id=. Salin link halaman "
                "aplikasi, bukan link pencarian."
            )
        text = ids[0].strip()

    # Buang spasi/newline yang tidak sengaja ikut tersalin.
    text = re.sub(r"\s+", "", text)
    if not _PACKAGE_RE.fullmatch(text):
        raise ValueError(
            "Kode aplikasi tidak valid. Contoh yang benar: com.whatsapp, "
            "com.instagram.android, atau id.co.bankmandiri.mandirionline."
        )
    return text


def app_url(app_id: str, lang: str = "id", country: str = "id") -> str:
    """URL halaman aplikasi di Play Store."""

    return (
        "https://play.google.com/store/apps/details"
        f"?id={app_id}&hl={lang}&gl={country}"
    )


# --------------------------------------------------------------------------- #
# Helper
# --------------------------------------------------------------------------- #


def _iso(value: Any) -> str:
    """Serialisasi tanggal/datetime/string menjadi string ISO yang aman."""

    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value).strip()


def _date_only(value: Any) -> str:
    """Ambil bagian tanggal (YYYY-MM-DD) dari beberapa format Play Store."""

    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")

    text = str(value).strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        pass
    cleaned = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return cleaned[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", cleaned) else ""


def _unix_to_date(seconds: Any) -> str:
    try:
        return datetime.fromtimestamp(int(seconds), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\r\n?", "\n", str(value)).strip()


def _sleep(seconds: float, stop_event: Optional[Any]) -> None:
    """Tidur yang bisa dibatalkan."""

    seconds = max(0.0, float(seconds))
    if seconds == 0:
        return
    if stop_event is not None:
        stop_event.wait(seconds)
    else:
        time.sleep(seconds)


def _with_retry(func: Callable[[], Any], *, log_cb: Callable[[str], None], stop_event=None):
    """Panggil ``func`` dengan percobaan ulang untuk gangguan jaringan."""

    last_exc: Optional[Exception] = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("Pekerjaan dihentikan.")
        try:
            return func()
        except NotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= RETRY_ATTEMPTS:
                break
            wait = RETRY_BASE_DELAY * attempt + random.uniform(0, 0.8)
            log_cb(
                f"⚠️ Percobaan {attempt} gagal ({type(exc).__name__}). "
                f"Coba lagi dalam {wait:.1f} detik…"
            )
            _sleep(wait, stop_event)
    raise last_exc  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Metadata aplikasi
# --------------------------------------------------------------------------- #


def _normalize_app(app_id: str, data: dict, lang: str, country: str) -> dict:
    histogram = data.get("histogram") or []
    histogram_map: dict[int, int] = {}
    if isinstance(histogram, (list, tuple)):
        for index, count in enumerate(histogram[:5], start=1):
            number = _as_int(count) or 0
            histogram_map[index] = number

    title = _clean_text(data.get("title")) or app_id
    return {
        "id": app_id,
        "nama": title,
        "developer": _clean_text(data.get("developer")),
        "icon": data.get("icon") or "",
        "header_image": data.get("headerImage") or "",
        "rating": data.get("score"),
        "jumlah_rating": _as_int(data.get("ratings")),
        "jumlah_ulasan": _as_int(data.get("reviews")),
        "instalasi": data.get("installs") or "",
        "min_instalasi": _as_int(data.get("minInstalls")),
        "genre": _clean_text(data.get("genre")),
        "kategori": list(data.get("categories") or [])[:5],
        "versi": _clean_text(data.get("version")),
        "diperbarui": data.get("lastUpdatedOn") or _unix_to_date(data.get("updated")),
        "rilis": data.get("released") or "",
        "gratis": bool(data.get("free")),
        "harga": data.get("price"),
        "mata_uang": data.get("currency") or "",
        "ada_iklan": bool(data.get("containsAds")),
        "pembelian_dalam_aplikasi": bool(data.get("offersIAP")),
        "rating_isi": _clean_text(data.get("contentRating")),
        "ringkasan": _clean_text(data.get("summary")),
        "deskripsi": _clean_text(data.get("description")),
        "url": data.get("url") or app_url(app_id, lang, country),
        "histogram": histogram_map,
        "bahasa": lang,
        "negara": country,
    }


def fetch_app_info(
    app_id: str,
    *,
    lang: str = "id",
    country: str = "id",
    log_cb: Optional[Callable[[str], None]] = None,
    _client=None,
    stop_event=None,
) -> dict:
    """Ambil metadata aplikasi dari Play Store.

    Raises:
        NotFoundError: bila aplikasi tidak ditemukan.
        Exception: gangguan jaringan setelah percobaan ulang.
    """

    if _client is None:
        with PlayStealthClient() as client:
            return fetch_app_info(app_id, lang=lang, country=country, log_cb=log_cb, _client=client, stop_event=stop_event)
    log = log_cb or (lambda _msg: None)
    data = _with_retry(
        lambda: _client.app(app_id, lang=lang, country=country), log_cb=log, stop_event=stop_event
    )
    return _normalize_app(app_id, data, lang, country)


# --------------------------------------------------------------------------- #
# Normalisasi ulasan
# --------------------------------------------------------------------------- #


def _normalize_review(review: dict, app_name: str, app_id: str) -> dict:
    """Ubah satu ulasan ``google-play-scraper`` menjadi baris tabel."""

    at = review.get("at")
    replied_at = review.get("repliedAt")
    user_name = _clean_text(review.get("userName"))
    return {
        "id_ulasan": review.get("reviewId") or "",
        "nama": user_name or "Pengguna anonim",
        "rating": _as_int(review.get("score")),
        "tanggal": _date_only(at),
        "waktu": _iso(at),
        "ulasan": _clean_text(review.get("content")),
        "suka": _as_int(review.get("thumbsUpCount")) or 0,
        "versi_aplikasi": _clean_text(review.get("appVersion")),
        "versi_dibuat": _clean_text(review.get("reviewCreatedVersion")),
        "balasan": _clean_text(review.get("replyContent")),
        "tanggal_balasan": _date_only(replied_at),
        "foto_profil": review.get("userImage") or "",
        "aplikasi": app_name,
        "kode_aplikasi": app_id,
    }


# --------------------------------------------------------------------------- #
# Scraping utama
# --------------------------------------------------------------------------- #


def _scrape_reviews(
    app_id: str,
    *,
    count: int = 0,
    lang: str = "id",
    country: str = "id",
    sort: str = "terbaru",
    score: Optional[int] = None,
    only_with_text: bool = False,
    delay: float = 0.6,
    max_minutes: int = 0,
    stop_event: Optional[Any] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[dict], None]] = None,
    _client=None,
    checkpoint_cb=None,
) -> dict:
    """Ambil ulasan aplikasi Play Store.

    Args:
        app_id: Kode aplikasi, mis. ``com.whatsapp``.
        count: Jumlah ulasan yang diinginkan. ``0`` berarti **semua** ulasan
            yang tersedia, tanpa batas jumlah dari sisi aplikasi.
        lang: Kode bahasa dua huruf, mis. ``id``.
        country: Kode negara dua huruf, mis. ``id``.
        sort: ``relevan``, ``terbaru``, atau ``rating``.
        score: Filter bintang 1–5. ``None``/``0`` berarti semua bintang.
        only_with_text: Hanya sertakan ulasan yang memiliki teks komentar.
        delay: Jeda antar-batch (detik).
        max_minutes: Batas waktu proses (menit). ``0`` berarti tanpa batas.
        stop_event: ``threading.Event`` untuk membatalkan proses.
        log_cb: Callback untuk pesan log.
        progress_cb: Callback progres dengan dict berisi ``collected``,
            ``target``, ``batch``, dan ``requested``.

    Returns:
        Dict berisi ``app``, ``rows``, ``error``, ``message``, parameter, dan
        statistik proses. Tidak melempar exception untuk kegagalan yang wajar.
    """

    log = log_cb or (lambda _msg: None)
    progress = progress_cb or (lambda _data: None)

    started = time.time()
    max_minutes_value = max(0, int(max_minutes or 0))
    deadline = started + max_minutes_value * 60 if max_minutes_value else None
    target = max(0, int(count or 0))
    normalized_sort = sort if sort in SORT_MAP else "terbaru"
    score_filter = int(score) if score in (1, 2, 3, 4, 5) else None

    result: dict[str, Any] = {
        "app": {},
        "rows": [],
        "requested": target,
        "sort": normalized_sort,
        "sort_label": SORT_LABELS.get(normalized_sort, normalized_sort),
        "lang": lang,
        "country": country,
        "score_filter": score_filter,
        "only_with_text": bool(only_with_text),
        "error": None,
        "message": "",
        "stop_reason": "",
        "batches": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": "",
        "duration_s": 0.0,
    }

    # ---- 1. Metadata aplikasi ------------------------------------------- #
    log(f"Mengambil info aplikasi {app_id} ({lang}/{country})…")
    try:
        app_info = fetch_app_info(app_id, lang=lang, country=country, log_cb=log, _client=_client, stop_event=stop_event)
    except NotFoundError:
        result["error"] = "app_not_found"
        result["message"] = (
            f"Aplikasi dengan kode **{app_id}** tidak ditemukan di Play Store "
            f"untuk locale {lang}/{country}. Periksa kembali kodenya "
            "(contoh: com.whatsapp)."
        )
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        log("❌ Aplikasi tidak ditemukan.")
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = "app_error"
        result["message"] = (
            f"Gagal mengambil info aplikasi: {type(exc).__name__}: {exc}"
        )
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        log(f"❌ {result['message']}")
        return result

    result["app"] = app_info
    app_name = app_info.get("nama") or app_id
    log(f"✅ Aplikasi: {app_name} · {app_info.get('developer') or '—'}")

    total_available = app_info.get("jumlah_ulasan")
    if total_available:
        log(f"ℹ️ Play Store mencantumkan {total_available:,} ulasan.".replace(",", "."))
    log(
        "Mengambil ulasan "
        f"(urut {SORT_LABELS.get(normalized_sort, normalized_sort)}"
        + (f", bintang {score_filter}" if score_filter else "")
        + (", hanya yang berteks" if only_with_text else "")
        + ")…"
    )
    if target == 0:
        log(
            "ℹ️ Mode semua ulasan: tidak ada batas jumlah. Proses dapat berjalan "
            "lama untuk aplikasi populer; gunakan tombol Hentikan bila perlu."
        )
    if deadline is None:
        log("ℹ️ Tanpa batas waktu. Proses berhenti saat feed habis atau dibatalkan.")

    # ---- 2. Ambil ulasan per batch -------------------------------------- #
    rows: list[dict] = []
    seen: set[str] = set()
    token = None
    batch_no = 0
    stop_reason = "habis"
    last_error: Optional[Exception] = None
    stagnant_batches = 0

    while True:
        if stop_event is not None and stop_event.is_set():
            stop_reason = "dibatalkan"
            log("⏹️ Proses dibatalkan oleh pengguna.")
            break
        if target and len(rows) >= target:
            stop_reason = "target"
            break
        if deadline is not None and time.time() > deadline:
            stop_reason = "waktu"
            log("⏱️ Batas waktu tercapai; menyimpan hasil yang sudah didapat.")
            break

        batch_size = BATCH_SIZE
        if target:
            # Ambil sedikit lebih banyak bila baris tanpa teks akan dibuang,
            # agar target baris berteks tetap bisa tercapai.
            batch_size = min(
                BATCH_SIZE, max(50, (target - len(rows)) if not only_with_text else 200)
            )

        batch_no += 1
        try:
            batch, token = _with_retry(
                lambda: _client.reviews(
                    app_id,
                    lang=lang,
                    country=country,
                    sort=SORT_MAP[normalized_sort],
                    count=batch_size,
                    continuation_token=token,
                    filter_score_with=score_filter,
                ),
                log_cb=log, stop_event=stop_event,
            )
        except NotFoundError:
            result["error"] = "app_not_found"
            result["message"] = f"Aplikasi **{app_id}** tidak ditemukan saat mengambil ulasan."
            log("❌ Aplikasi tidak ditemukan.")
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"❌ Gagal mengambil batch {batch_no}: {type(exc).__name__}: {exc}")
            if rows:
                result["message"] = (
                    f"Pengambilan berhenti di batch {batch_no} karena gangguan: "
                    f"{type(exc).__name__}: {exc}. Hasil sementara tetap disimpan."
                )
                stop_reason = "gangguan"
            else:
                result["error"] = "fetch_error"
                result["message"] = (
                    f"Gagal mengambil ulasan: {type(exc).__name__}: {exc}"
                )
                stop_reason = "gagal"
            break

        if not batch:
            stop_reason = "habis"
            log("✅ Tidak ada ulasan baru; feed sudah habis.")
            break

        new_rows = 0
        for item in batch:
            review_id = str(item.get("reviewId") or "")
            if review_id and review_id in seen:
                continue
            content = _clean_text(item.get("content"))
            if only_with_text and not content:
                continue
            if review_id:
                seen.add(review_id)
            rows.append(_normalize_review(item, app_name, app_id))
            new_rows += 1
            if target and len(rows) >= target:
                break

        if checkpoint_cb and new_rows:
            checkpoint_cb(rows)
        result["batches"] = batch_no
        progress(
            {
                "collected": len(rows),
                "target": target,
                "batch": batch_no,
                "requested": target,
            }
        )
        shown_total = f"{len(rows):,}".replace(",", ".")
        log(f"📥 Batch {batch_no}: +{new_rows} · total {shown_total} ulasan")

        if target and len(rows) >= target:
            stop_reason = "target"
            break
        if not token:
            stop_reason = "habis"
            log("✅ Continuation token habis; semua ulasan yang tersedia sudah diambil.")
            break

        # Pengaman anti-loop (bukan batas data): hentikan bila Play Store
        # mengembalikan batch tanpa baris baru berulang kali.
        if new_rows == 0:
            stagnant_batches += 1
            if stagnant_batches >= STAGNANT_BATCH_LIMIT:
                stop_reason = "stagnan"
                log(
                    "⚠️ Play Store tidak mengembalikan baris baru beberapa kali; "
                    "proses dihentikan agar tidak berputar tanpa akhir."
                )
                break
        else:
            stagnant_batches = 0

        _sleep(delay, stop_event)

    # ---- 3. Rapikan hasil ----------------------------------------------- #
    # Urutan baris mengikuti urutan yang diminta dari Play Store (relevan /
    # terbaru / rating). Pengurutan tampilan dilakukan di antarmuka.
    result["rows"] = rows
    result["stop_reason"] = stop_reason
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["duration_s"] = round(time.time() - started, 2)

    if not rows and not result["error"]:
        result["error"] = "no_reviews"
        filter_bits = []
        if score_filter:
            filter_bits.append(f"bintang {score_filter}")
        if only_with_text:
            filter_bits.append("hanya yang berteks")
        suffix = f" dengan filter {', '.join(filter_bits)}" if filter_bits else ""
        result["message"] = (
            f"Tidak ada ulasan{suffix} yang dikembalikan Play Store untuk "
            f"{app_name} ({lang}/{country}). Coba ubah bahasa/negara, urutan, "
            "filter bintang, atau tambah jumlah ulasan."
        )
        log("⚠️ Tidak ada ulasan yang terbaca.")
    elif rows:
        log(
            f"🎉 Selesai: {len(rows):,} ulasan dalam {result['duration_s']:.1f} detik "
            f"({batch_no} batch).".replace(",", ".")
        )
        if last_error is not None:
            result["error"] = "partial"
    else:
        log("Selesai tanpa ulasan.")

    return result


# --------------------------------------------------------------------------- #
# Ekspor
# --------------------------------------------------------------------------- #


def rows_to_csv(rows: list[dict]) -> str:
    """Serialisasi baris ulasan menjadi CSV (UTF-8 dengan BOM untuk Excel)."""

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    return buffer.getvalue()


def rows_to_json(
    rows: list[dict],
    app_info: Optional[dict] = None,
    parameters: Optional[dict] = None,
) -> str:
    """Serialisasi ulasan + metadata aplikasi menjadi JSON."""

    payload = {
        "aplikasi": app_info or {},
        "parameter": parameters or {},
        "diambil_pada": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "ulasan": rows,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


@wraps(_scrape_reviews)
def scrape_reviews(*args, real_chrome=True, **kwargs):
    """Own one Scrapling stealth browser per review collection job."""
    with PlayStealthClient(real_chrome=real_chrome) as client:
        return _scrape_reviews(*args, **kwargs, _client=client)
