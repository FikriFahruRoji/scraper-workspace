"""Cari lokasi dan informasi tempat di Google Maps memakai Scrapling.

Modul ini hanya mengambil data publik dari halaman pencarian Google Maps:
nama tempat, kategori, alamat, rating, jumlah ulasan, status buka, koordinat,
dan link tempat. Detail opsional (telepon, website, plus code, jam lengkap)
diambil dengan membuka halaman tiap tempat satu per satu.

Catatan:
  - Google Maps membatasi jumlah hasil per pencarian (sekitar 120 tempat).
  - Aplikasi tidak melewati CAPTCHA, login, atau kontrol akses apa pun.
  - Gunakan hanya untuk data publik yang Anda berhak akses dan patuhi ToS
    Google Maps.
"""

from __future__ import annotations

import pathlib
import re
import time
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import quote_plus, unquote, urlparse
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = _PROJECT_DIR / "sessions" / "google_places_profile"

_JAKARTA = ZoneInfo("Asia/Jakarta")
_MAX_SCROLLS = 80
_STOP_AFTER = 5
_MAX_RUNTIME_MIN = 30
_DETAIL_DELAY_S = 2.0
_MAX_RESULTS = 120

# --------------------------------------------------------------------------- #
# JavaScript pembacaan halaman
# --------------------------------------------------------------------------- #

_PLACES_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const feed = document.querySelector('div[role="feed"]');
  const cards = feed ? Array.from(feed.querySelectorAll('div[role="article"]')) : [];
  const out = [];

  for (const card of cards) {
    const linkEl = card.querySelector('a[href*="/maps/place/"]');
    if (!linkEl) continue;

    const name = clean(card.querySelector('.qBF1Pd')?.innerText || '');
    const rating = clean(card.querySelector('.MW4etd')?.innerText || '');
    const reviews = clean(card.querySelector('.UY7F9')?.innerText || '');
    const blocks = Array.from(card.querySelectorAll('.W4Efsd'))
      .map((el) => clean(el.innerText))
      .filter(Boolean);
    const unique = [...new Set(blocks)];

    const isRating = (text) => /^\d[.,]\d/.test(text);
    const isStatus = (text) => /^(Buka|Tutup|Open|Closed)\b/i.test(text);
    const hasStatus = (text) => /\b(Buka|Tutup|Open|Closed)\b/i.test(text);

    let meta = unique.find((text) => text.includes('·') && !isRating(text)
      && !isStatus(text) && !hasStatus(text)) || '';
    const status = unique.find(isStatus) || '';
    if (!meta) {
      meta = unique.find((text) => text.includes('·') && !isRating(text)) || '';
      if (status && meta.includes(status)) meta = clean(meta.replace(status, ''));
    }
    const description = unique.find((text) => text !== meta && text !== status
      && !isRating(text) && !text.includes('·')) || '';

    out.push({
      name,
      rating,
      reviews,
      meta,
      status,
      description,
      info: unique,
      link: linkEl.href || '',
    });
  }
  return out;
}
"""

_FEED_SCROLL_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) {
    return {
      ok: false,
      cards: document.querySelectorAll('div[role="article"]').length,
    };
  }
  const before = feed.scrollTop;
  feed.scrollTop = feed.scrollHeight;
  feed.dispatchEvent(new Event('scroll', { bubbles: true }));
  return {
    ok: true,
    before,
    top: feed.scrollTop,
    height: feed.scrollHeight,
    client: feed.clientHeight,
    at_bottom: feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 8,
    cards: document.querySelectorAll('div[role="article"]').length,
  };
}
"""

_FEED_STATS_JS = r"""
() => {
  const feed = document.querySelector('div[role="feed"]');
  if (!feed) {
    return {
      ok: false,
      cards: document.querySelectorAll('div[role="article"]').length,
    };
  }
  return {
    ok: true,
    top: feed.scrollTop,
    height: feed.scrollHeight,
    client: feed.clientHeight,
    at_bottom: feed.scrollTop + feed.clientHeight >= feed.scrollHeight - 8,
    cards: document.querySelectorAll('div[role="article"]').length,
  };
}
"""

_PLACE_DETAIL_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const main = document.querySelector('div[role="main"]') || document;
  const h1 = main.querySelector('h1') || document.querySelector('h1');
  const strip = (value) => clean(value)
    .replace(/^(Alamat|Address|Telepon|Phone|Situs web|Website|Plus code)\s*:\s*/i, '');

  const address = document.querySelector('button[data-item-id="address"]');
  const phone = document.querySelector('button[data-item-id^="phone:tel:"]');
  const website = document.querySelector('a[data-item-id="authority"]');
  const category = document.querySelector('button[jsaction*="category"]')
    || document.querySelector('button.DkEaL');
  const plus = document.querySelector('button[data-item-id="oloc"]');
  const hours = document.querySelector('button[data-item-id="oh"]');
  const dayLabels = Array.from(document.querySelectorAll('[aria-label]'))
    .map((el) => el.getAttribute('aria-label') || '')
    .filter((a) => /^(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),/i.test(a))
    .map((a) => a.replace(/,?\s*(Salin jam buka|Copy opening hours|Copy hours).*$/i, '').trim());
  const weeklyHours = [...new Set(dayLabels)].join(' · ');

  const ratingBox = document.querySelector('div.F7nice');
  let ratingRaw = '';
  let countRaw = '';
  if (ratingBox) {
    const aria = Array.from(ratingBox.querySelectorAll('[aria-label]'))
      .map((el) => el.getAttribute('aria-label') || '');
    ratingRaw = clean(ratingBox.querySelector('span[aria-hidden="true"]')?.innerText || '');
    countRaw = aria.find((a) => /ulasan|review/i.test(a)) || clean(ratingBox.innerText || '');
  }

  return {
    nama: clean(h1?.innerText || document.title || ''),
    kategori: strip(category?.innerText || ''),
    alamat: strip(address?.getAttribute('aria-label') || address?.innerText || ''),
    telepon: strip(phone?.getAttribute('aria-label') || phone?.innerText || ''),
    website: website ? website.href : '',
    plus_code: strip(plus?.getAttribute('aria-label') || plus?.innerText || ''),
    jam_lengkap: strip(hours?.getAttribute('aria-label') || hours?.innerText || '') || weeklyHours,
    rating_teks: ratingRaw,
    jumlah_ulasan_teks: countRaw,
    halaman: location.href,
  };
}
"""


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def default_user_data_dir() -> str:
    """Path profil browser persisten untuk pencarian lokasi."""
    return str(DEFAULT_PROFILE_DIR)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _now_jakarta() -> datetime:
    return datetime.now(tz=_JAKARTA)


def _parse_count(raw: Any) -> int:
    """Ubah "1.856", "(122.709)", "1,2 rb" menjadi int."""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    text = str(raw).lower()
    match = re.search(r"([\d.,]+)\s*(rb|ribu|k|jt|juta|m)?", text)
    if not match:
        return 0
    number = match.group(1)
    suffix = match.group(2) or ""
    if suffix in {"rb", "ribu", "k"}:
        multiplier = 1_000
    elif suffix in {"jt", "juta", "m"}:
        multiplier = 1_000_000
    else:
        multiplier = 1
    try:
        if multiplier == 1:
            digits = re.sub(r"[^\d]", "", number)
            return int(digits) if digits else 0
        value = float(number.replace(".", "").replace(",", "."))
        return int(value * multiplier)
    except (TypeError, ValueError):
        return 0


def _parse_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(raw))
    if not match:
        return None
    try:
        value = float(match.group(0).replace(",", "."))
    except ValueError:
        return None
    return value if 0 <= value <= 5 else None


def build_places_url(keyword: str, area: str = "") -> str:
    """Bangun URL pencarian Google Maps dari kata kunci dan area opsional."""
    query = " ".join(part for part in (_clean(keyword), _clean(area)) if part)
    return f"https://www.google.com/maps/search/{quote_plus(query)}?hl=id&gl=id"


def _extract_place_id(value: str) -> str:
    match = re.search(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", value or "")
    return match.group(1) if match else ""


def _extract_coords(value: str) -> tuple[Optional[float], Optional[float]]:
    for pattern in (
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
        r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
    ):
        match = re.search(pattern, value or "")
        if match:
            try:
                lat = float(match.group(1))
                lon = float(match.group(2))
            except ValueError:
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None, None


def _place_name_from_url(value: str) -> str:
    try:
        path = urlparse(value).path
        match = re.search(r"/maps/place/([^/]+)", path)
        if match:
            return _clean(unquote(match.group(1)).replace("+", " "))
    except Exception:
        pass
    return ""


def _split_meta(meta: str) -> tuple[str, str, str]:
    """Pisahkan "Kategori · Harga · Alamat" menjadi tiga bagian."""
    segments = [part.strip() for part in _clean(meta).split("·") if part.strip()]
    if not segments:
        return "", "", ""
    price = next(
        (part for part in segments if re.match(r"^(Rp|IDR|\$)", part, re.I)),
        "",
    )
    category = segments[0] if segments else ""
    address = ""
    for part in reversed(segments[1:]):
        if part != price:
            address = part
            break
    return category, address, price


def _split_status(status: str) -> tuple[str, str]:
    parts = [part.strip() for part in _clean(status).split("·") if part.strip()]
    if not parts:
        return "", ""
    return parts[0], " · ".join(parts[1:])


def _parse_place_card(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    link = _clean(raw.get("link"))
    if not link:
        return None
    name = _clean(raw.get("name")) or _place_name_from_url(link)
    if not name:
        return None

    category, address, price = _split_meta(raw.get("meta") or "")
    status, hours = _split_status(raw.get("status") or "")
    lat, lon = _extract_coords(link)
    info = raw.get("info") or []
    info_text = " | ".join(_clean(item) for item in info if _clean(item))

    return {
        "nama": name,
        "kategori": category,
        "alamat": address,
        "rating": _parse_float(raw.get("rating")),
        "jumlah_ulasan": _parse_count(raw.get("reviews")),
        "status_buka": status,
        "jam_buka": hours,
        "jam_lengkap": "",
        "deskripsi": _clean(raw.get("description")),
        "rentang_harga": price,
        "telepon": "",
        "website": "",
        "plus_code": "",
        "lintang": lat,
        "bujur": lon,
        "place_id": _extract_place_id(link),
        "link": link,
        "info_lengkap": info_text,
        "detail_diambil": False,
    }


def _parse_detail(raw: Any, url: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    lat, lon = _extract_coords(url)
    return {
        "nama": _clean(raw.get("nama")),
        "kategori": _clean(raw.get("kategori")),
        "alamat": _clean(raw.get("alamat")),
        "telepon": _clean(raw.get("telepon")),
        "website": _clean(raw.get("website")),
        "plus_code": _clean(raw.get("plus_code")),
        "jam_lengkap": _clean(raw.get("jam_lengkap")),
        "rating": _parse_float(raw.get("rating_teks")),
        "jumlah_ulasan": _parse_count(raw.get("jumlah_ulasan_teks")),
        "lintang": lat,
        "bujur": lon,
        "link": url,
    }


def _dedupe_key(row: dict[str, Any]) -> str:
    return (
        _clean(row.get("place_id"))
        or _clean(row.get("link"))
        or f"{_clean(row.get('nama'))}|{_clean(row.get('alamat'))}"
    )


# --------------------------------------------------------------------------- #
# Interaksi halaman
# --------------------------------------------------------------------------- #


def _click_element(element) -> bool:
    try:
        element.click()
        return True
    except Exception:
        try:
            element.evaluate("el => el.click()")
            return True
        except Exception:
            return False


def _handle_consent(page, log: Callable[[str], None]) -> None:
    """Lewati layar persetujuan Google bila muncul (bukan CAPTCHA)."""
    try:
        host = (urlparse(page.url).hostname or "").lower()
        body = (page.inner_text("body") or "").lower()
        needs_consent = (
            "consent" in host
            or "sebelum anda melanjutkan" in body
            or "before you continue" in body
        )
        if not needs_consent:
            return
        log("🍪 Layar persetujuan Google terdeteksi, mencoba menerima…")
        for label in ("Terima semua", "Accept all", "Setuju", "I agree"):
            try:
                button = page.get_by_role(
                    "button", name=re.compile(rf"^{re.escape(label)}$", re.I)
                ).first
                if button and button.is_visible():
                    _click_element(button)
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                continue
    except Exception:
        pass


def _is_blocked(page) -> bool:
    try:
        if "/sorry/" in (page.url or ""):
            return True
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return any(
        text in body
        for text in (
            "unusual traffic",
            "sistem kami mendeteksi",
            "traffic tidak biasa",
            "bukan robot",
        )
    )


def _extract_place_detail(page) -> dict[str, Any]:
    try:
        return page.evaluate(_PLACE_DETAIL_JS) or {}
    except Exception:
        return {}


def _merge_detail(row: dict[str, Any], detail: dict[str, Any]) -> None:
    for key in ("kategori", "alamat", "telepon", "website", "plus_code", "jam_lengkap"):
        if detail.get(key):
            row[key] = detail[key]
    if detail.get("nama") and not row.get("nama"):
        row["nama"] = detail["nama"]
    if detail.get("rating") is not None and row.get("rating") is None:
        row["rating"] = detail["rating"]
    if detail.get("jumlah_ulasan") and not row.get("jumlah_ulasan"):
        row["jumlah_ulasan"] = detail["jumlah_ulasan"]
    if detail.get("lintang") is not None and row.get("lintang") is None:
        row["lintang"] = detail["lintang"]
    if detail.get("bujur") is not None and row.get("bujur") is None:
        row["bujur"] = detail["bujur"]
    row["detail_diambil"] = True


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #


def search_places(
    keyword: str,
    *,
    area: str = "",
    max_places: int = 60,
    detail_limit: int = 0,
    headless: bool = True,
    real_chrome: bool = False,
    user_data_dir: Optional[str] = None,
    wait_ms: int = 1600,
    max_minutes: int = 10,
    log_cb: Optional[Callable[[str], None]] = None,
    stop_event=None,
    checkpoint_cb=None,
) -> dict[str, Any]:
    """Cari tempat di Google Maps berdasarkan kata kunci.

    ``detail_limit`` 0 berarti hanya data daftar. Nilai lebih besar membuka
    halaman tiap tempat (maksimal sejumlah itu) untuk melengkapi telepon,
    website, plus code, dan jam lengkap.

    Mengembalikan dict: rows, keyword, area, detail_diminta, detail_berhasil,
    error, message, stopped.
    """
    keyword = _clean(keyword)
    area = _clean(area)
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    limit = _MAX_RESULTS if not max_places else max(1, min(int(max_places), _MAX_RESULTS))
    detail_limit = max(0, min(int(detail_limit or 0), limit))
    max_minutes = max(0, int(max_minutes or 0))

    result: dict[str, Any] = {
        "rows": [],
        "keyword": keyword,
        "area": area,
        "detail_diminta": detail_limit,
        "detail_berhasil": 0,
        "error": None,
        "message": "",
        "stopped": None,
    }

    def log(message: str) -> None:
        if log_cb:
            try:
                log_cb(message)
            except Exception:
                pass

    if not keyword:
        result["error"] = "no_places"
        result["message"] = "Isi kata kunci tempat terlebih dahulu."
        log(f"⚠️ {result['message']}")
        return result

    url = build_places_url(keyword, area)
    log(f"🔎 Mencari tempat: {keyword}" + (f" · area {area}" if area else ""))
    log(f"🔗 Membuka: {url}")

    collected: dict[str, dict[str, Any]] = {}
    state: dict[str, Any] = {"error": None, "message": "", "stopped": None}
    started = time.time()
    stamp = _now_jakarta().isoformat(timespec="seconds")

    def finalize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            row["kata_kunci"] = keyword
            row["area"] = area
            row["diambil_pada"] = stamp
        return rows

    def current_rows() -> list[dict[str, Any]]:
        return finalize(list(collected.values())[:limit])

    def add_cards(cards: Any) -> int:
        if not isinstance(cards, list):
            return 0
        added = 0
        for raw in cards:
            if len(collected) >= limit:
                break
            row = _parse_place_card(raw)
            if not row:
                continue
            key = _dedupe_key(row)
            if not key or key in collected:
                continue
            collected[key] = row
            added += 1
        if added and checkpoint_cb:
            try:
                checkpoint_cb(current_rows())
            except Exception:
                pass
        return added

    def add_single_place(page) -> int:
        detail = _parse_detail(_extract_place_detail(page), page.url)
        if not detail.get("nama"):
            return 0
        row = {
            "nama": detail["nama"],
            "kategori": detail.get("kategori", ""),
            "alamat": detail.get("alamat", ""),
            "rating": detail.get("rating"),
            "jumlah_ulasan": detail.get("jumlah_ulasan", 0),
            "status_buka": "",
            "jam_buka": "",
            "jam_lengkap": detail.get("jam_lengkap", ""),
            "deskripsi": "",
            "rentang_harga": "",
            "telepon": detail.get("telepon", ""),
            "website": detail.get("website", ""),
            "plus_code": detail.get("plus_code", ""),
            "lintang": detail.get("lintang"),
            "bujur": detail.get("bujur"),
            "place_id": _extract_place_id(page.url),
            "link": page.url,
            "info_lengkap": "",
            "detail_diambil": True,
        }
        key = _dedupe_key(row)
        if key and key not in collected:
            collected[key] = row
            if checkpoint_cb:
                try:
                    checkpoint_cb(current_rows())
                except Exception:
                    pass
            return 1
        return 0

    def action(page) -> None:
        _handle_consent(page, log)
        if _is_blocked(page):
            state["error"] = "blocked"
            state["message"] = "Google Maps memblokir permintaan otomatis."
            log(f"⚠️ {state['message']}")
            return

        try:
            page.wait_for_selector('div[role="feed"]', timeout=25000)
        except Exception:
            pass

        _handle_consent(page, log)
        page.wait_for_timeout(2000)

        if not page.query_selector('div[role="feed"]'):
            # Kata kunci yang sangat spesifik bisa langsung membuka satu tempat.
            added = add_single_place(page)
            if added:
                log(f"📍 Tempat ditemukan langsung: {next(iter(collected.values()))['nama']}")
            state["stopped"] = "single"
            return

        add_cards(page.evaluate(_PLACES_JS) or [])
        log(f"📍 {len(collected)} tempat ditemukan")

        no_new = 0
        scrolls = 0
        while scrolls < _MAX_SCROLLS:
            if stop_event is not None and stop_event.is_set():
                state["stopped"] = "cancelled"
                break
            if len(collected) >= limit:
                state["stopped"] = "max_places"
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                state["stopped"] = "timeout"
                state["message"] = "Batas waktu tercapai sebelum semua hasil diambil."
                break
            if (time.time() - started) > _MAX_RUNTIME_MIN * 60:
                state["stopped"] = "timeout"
                state["message"] = "Batas keras waktu pencarian tercapai."
                break

            before = len(collected)
            try:
                stats = page.evaluate(_FEED_SCROLL_JS) or {}
            except Exception:
                stats = {}
            scrolls += 1

            for attempt in range(6):
                if stop_event is not None and stop_event.is_set():
                    break
                page.wait_for_timeout(
                    max(600, min(int(wait_ms), 1600)) if attempt == 0 else 700
                )
                add_cards(page.evaluate(_PLACES_JS) or [])
                if len(collected) > before:
                    break
                try:
                    after = page.evaluate(_FEED_STATS_JS) or {}
                except Exception:
                    after = {}
                if after.get("at_bottom") and attempt >= 2:
                    break

            if len(collected) > before:
                no_new = 0
                log(f"  ➕ {len(collected) - before} tempat baru · total {len(collected)}")
            else:
                no_new += 1

            if scrolls % 3 == 0:
                log(f"  ↕️ gulir #{scrolls} · {len(collected)} tempat unik")

            if no_new >= _STOP_AFTER:
                state["stopped"] = "exhausted"
                log("🏁 Daftar hasil sudah habis.")
                break
            if stats.get("ok") and stats.get("at_bottom") and no_new >= 2:
                state["stopped"] = "exhausted"
                log("🏁 Mencapai dasar daftar hasil.")
                break

            page.wait_for_timeout(400)

        page.wait_for_timeout(600)
        add_cards(page.evaluate(_PLACES_JS) or [])
        log(f"✅ Daftar selesai · {len(collected)} tempat unik")

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="id-ID",
            timezone_id="Asia/Jakarta",
        ) as session:
            session.fetch(
                url,
                page_action=action,
                timeout=max(180000, (max_minutes or _MAX_RUNTIME_MIN) * 60 * 1000 + 180000),
            )

            rows = current_rows()
            if detail_limit and rows and not (stop_event is not None and stop_event.is_set()):
                enriched, stopped = _enrich_rows(
                    session,
                    rows,
                    detail_limit=detail_limit,
                    started=started,
                    max_minutes=max_minutes,
                    log=log,
                    stop_event=stop_event,
                    checkpoint_cb=lambda items: checkpoint_cb(finalize(items)) if checkpoint_cb else None,
                )
                rows = enriched
                result["detail_berhasil"] = sum(bool(row.get("detail_diambil")) for row in rows)
                if stopped == "timeout":
                    state["stopped"] = "timeout"
                    state["message"] = (
                        "Detail tempat dihentikan karena batas waktu. Data daftar tetap disimpan."
                    )
                elif stopped == "cancelled":
                    state["stopped"] = "cancelled"

            result["rows"] = rows
    except Exception as exc:  # noqa: BLE001
        state["error"] = "browser_error"
        state["message"] = f"Gagal menjalankan browser: {exc}"
        log(f"⚠️ {state['message']}")
        result["rows"] = current_rows()

    result["stopped"] = state.get("stopped")
    if state.get("error"):
        result["error"] = state["error"]
        result["message"] = state.get("message") or result["message"]
    elif not result["rows"]:
        result["error"] = "no_places"
        result["message"] = (
            "Tidak ada tempat yang ditemukan. Coba kata kunci yang lebih umum "
            "atau tambahkan area."
        )
        log(f"⚠️ {result['message']}")
    elif state.get("message"):
        result["message"] = state["message"]
        if state.get("stopped") == "timeout":
            result["error"] = "timeout"

    if result["rows"]:
        log(
            f"🏁 Selesai · {len(result['rows'])} tempat"
            + (
                f" · detail {result['detail_berhasil']} tempat"
                if result["detail_diminta"]
                else ""
            )
        )
    return result


def _enrich_rows(
    session,
    rows: list[dict[str, Any]],
    *,
    detail_limit: int,
    started: float,
    max_minutes: int,
    log: Callable[[str], None],
    stop_event,
    checkpoint_cb,
) -> tuple[list[dict[str, Any]], str]:
    """Lengkapi baris dengan data dari halaman detail tiap tempat."""
    stopped = "done"
    total = min(detail_limit, len(rows))
    log(f"🧭 Mengambil detail {total} tempat satu per satu…")

    for index, row in enumerate(rows[:total], start=1):
        if stop_event is not None and stop_event.is_set():
            stopped = "cancelled"
            break
        if max_minutes and (time.time() - started) > max_minutes * 60:
            stopped = "timeout"
            break
        link = _clean(row.get("link"))
        if not link:
            continue

        detail: dict[str, Any] = {}

        def detail_action(page) -> None:
            _handle_consent(page, log)
            try:
                page.wait_for_selector("h1", timeout=15000)
            except Exception:
                pass
            detail.update(_parse_detail(_extract_place_detail(page), page.url))

        try:
            session.fetch(link, page_action=detail_action, timeout=60000)
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ Detail {index}/{total} gagal: {exc}")
        else:
            if detail:
                _merge_detail(row, detail)
                log(f"  📌 Detail {index}/{total}: {row.get('nama') or 'tanpa nama'}")
            else:
                log(f"  ⚠️ Detail {index}/{total} tidak terbaca.")

        if checkpoint_cb:
            try:
                checkpoint_cb(rows)
            except Exception:
                pass

        if index < total and not (stop_event is not None and stop_event.is_set()):
            if stop_event is not None:
                stop_event.wait(_DETAIL_DELAY_S)
            else:
                time.sleep(_DETAIL_DELAY_S)

    return rows, stopped
