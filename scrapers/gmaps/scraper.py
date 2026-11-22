"""Scraping ulasan tempat di Google Maps menggunakan Scrapling.

Modul ini membungkus ``StealthySession`` dari Scrapling untuk membuka halaman
tempat Google Maps, membuka tab ulasan, menggulir daftar ulasan, lalu
mengekstrak data dari DOM.

Catatan penting:
  - Google Maps membatasi tampilan ulasan untuk pengunjung yang belum login
    (saat proyek ini dibuat: sekitar 5 ulasan pertama + ajakan login).
  - Workspace menyediakan profil browser lokal untuk sesi Google Maps.
  - Aplikasi tidak melewati CAPTCHA, paywall, atau kontrol akses apa pun.
    Gunakan hanya untuk data publik yang Anda berhak akses dan patuhi ToS
    Google Maps.
"""

from __future__ import annotations

import html as html_lib
import json
import pathlib
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession

try:  # python-dateutil biasanya sudah ikut terpasang bersama pandas.
    from dateutil import parser as date_parser
except Exception:  # pragma: no cover - fallback bila tidak tersedia
    date_parser = None

# --------------------------------------------------------------------------- #
# Konstanta & path
# --------------------------------------------------------------------------- #

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = _PROJECT_DIR / "sessions" / "google_profile"

_JAKARTA = ZoneInfo("Asia/Jakarta")
_MAX_SCROLLS = 900          # pengaman agar tidak menggulir tanpa henti
_STOP_AFTER = 6             # berhenti jika N gulir berturut-turut tanpa ulasan baru
_MAX_RUNTIME_MIN = 30       # batas keras satu proses browser

_PAUSE_EVERY_S = 120        # jeda berkala setiap 2 menit
_PAUSE_DURATION_S = 20      # durasi jeda (detik)

# Endpoint ulasan internal Google Maps (mode cepat). Tidak terdokumentasi
# secara publik dan bisa berubah kapan saja.
_BOQ_ENDPOINT = (
    "https://www.google.com/httpservice/web/"
    "PrivateLocalSearchUiDataService/GetLocalBoqProxy"
)
_SORT_ORDERS = {
    "relevant": 1,
    "newest": 2,
    "highest": 3,
    "lowest": 4,
}
_API_PAGE_DELAY_S = 0.35
_API_MAX_RETRIES = 3
_API_PAGE_SIZE = 60          # ukuran batch maksimum yang dilayani endpoint

# Host Google Maps yang diterima sebagai input. Link pendek maps.app.goo.gl
# akan diarahkan otomatis oleh browser.
_SAFE_GOOGLE_TLDS = {
    "com", "co.id", "co.uk", "com.au", "ca", "de", "fr", "co.jp", "com.sg",
    "com.my", "co.in", "com.br", "com.mx", "es", "it", "nl", "ae", "com.tr",
    "co.th", "com.ph", "com.vn", "co.kr", "com.tw", "com.hk", "co.nz", "co.za",
    "com.ar", "cl", "com.co", "com.pe", "com.sa", "co.ve", "com.eg", "co.ke",
    "com.ng", "com.pk", "com.bd", "com.ua", "pl", "se", "no", "dk", "fi", "ch",
    "at", "be", "ie", "pt", "cz", "ro", "gr", "hu", "bg", "sk", "co.il",
}

# --------------------------------------------------------------------------- #
# JavaScript untuk membaca & menggulir halaman
# --------------------------------------------------------------------------- #

_PLACE_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const main = document.querySelector('div[role="main"]') || document;
  const h1 = main.querySelector('h1') || document.querySelector('h1');
  const label = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute('aria-label') || el.innerText || '').trim() : '';
  };
  const strip = (value) => clean(value).replace(/^(Alamat|Address|Telepon|Phone|Situs web|Website)\s*:\s*/i, '');

  const ratingBox = document.querySelector('div.F7nice');
  let ratingRaw = '';
  let countRaw = '';
  if (ratingBox) {
    const aria = Array.from(ratingBox.querySelectorAll('[aria-label]'))
      .map((el) => el.getAttribute('aria-label') || '');
    ratingRaw = clean(ratingBox.querySelector('span[aria-hidden="true"]')?.innerText || '');
    countRaw = aria.find((a) => /ulasan|review/i.test(a)) || clean(ratingBox.innerText || '');
  }

  const address = document.querySelector('button[data-item-id="address"]');
  const phone = document.querySelector('button[data-item-id^="phone:tel:"]');
  const website = document.querySelector('a[data-item-id="authority"]');
  const category = document.querySelector('button[jsaction*="category"]');
  const title = clean(h1?.innerText || document.title || '');

  return {
    nama: title.split('\n')[0].trim(),
    kategori: clean(category?.innerText || ''),
    alamat: strip(address?.getAttribute('aria-label') || address?.innerText || ''),
    telepon: strip(phone?.getAttribute('aria-label') || phone?.innerText || ''),
    website: website ? website.href : '',
    rating_teks: ratingRaw,
    jumlah_ulasan_teks: countRaw,
    halaman: location.href,
  };
}
"""

_REVIEWS_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const cards = Array.from(document.querySelectorAll('div[data-review-id].jftiEf'));
  const out = [];
  const seen = new Set();

  for (const card of cards) {
    const id = card.getAttribute('data-review-id') || '';
    if (id && seen.has(id)) continue;
    if (id) seen.add(id);

    const name = clean(
      card.querySelector('.d4r55')?.innerText ||
      card.getAttribute('aria-label') || ''
    );
    const info = clean(card.querySelector('.RfnDt')?.innerText || '');
    const ratingLabel = card.querySelector('.kvMYJc')?.getAttribute('aria-label') || '';
    const dateText = clean(card.querySelector('.rsqaWe')?.innerText || '');
    let text = clean(card.querySelector('.wiI7pd')?.innerText || '');

    if (!text) {
      const box = card.querySelector('.MyEned');
      if (box) text = clean(box.innerText || '');
    }

    const linkEl = card.querySelector('button[data-href*="/maps/contrib/"]')
      || card.querySelector('a[href*="/maps/contrib/"]');
    const profile = linkEl
      ? (linkEl.getAttribute('data-href') || linkEl.href || '')
      : '';
    const avatarEl = card.querySelector('img');
    const avatar = avatarEl ? (avatarEl.src || '') : '';

    const likeBtn = Array.from(card.querySelectorAll('button')).find((b) => {
      const a = (b.getAttribute('aria-label') || '').trim();
      const t = (b.innerText || '').trim();
      return /^(Suka|Like)\b/i.test(a) || /^(Suka|Like)\b/i.test(t);
    });
    // Hanya teks tombol yang dipakai untuk hitungan. Aria-label biasanya berisi
    // nama reviewer dan bisa mengandung angka yang bukan jumlah suka.
    const likeText = likeBtn ? clean(likeBtn.innerText || '') : '';

    let photoCount = 0;
    for (const b of card.querySelectorAll('button.Tya61d')) {
      const a = b.getAttribute('aria-label') || '';
      const plus = a.match(/\+\s*(\d+)\s+(?:foto|photo)/i);
      if (plus) {
        photoCount += parseInt(plus[1], 10) || 0;
      } else if (!/\b(video|videos)\b/i.test(a)) {
        photoCount += 1;
      }
    }

    let owner = '';
    const ownerBox = card.querySelector('.CDe7pd');
    if (ownerBox) owner = clean(ownerBox.innerText || '');
    if (!owner) {
      const ownerLabel = Array.from(card.querySelectorAll('div, span')).find((el) => {
        if (el.children.length) return false;
        const t = (el.innerText || '').trim();
        return /^(Balasan dari pemilik|Response from the owner)\b/i.test(t);
      });
      if (ownerLabel?.parentElement) {
        owner = clean(ownerLabel.parentElement.innerText || '')
          .replace(ownerLabel.innerText.trim(), '')
          .trim();
      }
    }

    out.push({
      id,
      nama: name,
      info_reviewer: info,
      rating_teks: ratingLabel,
      tanggal: dateText,
      teks: text,
      profil: profile,
      foto_profil: avatar,
      suka_teks: likeText,
      foto: photoCount,
      balasan_pemilik: owner,
    });
  }
  return out;
}
"""

_EXPAND_JS = r"""
() => {
  let count = 0;
  for (const b of document.querySelectorAll('button.w8nwRe')) {
    const text = (b.innerText || '').trim();
    const aria = (b.getAttribute('aria-label') || '').trim();
    if (/^(Lainnya|Lihat lainnya|More|Show more)$/i.test(text) || /lainnya|show more/i.test(aria)) {
      try { b.click(); count += 1; } catch (e) {}
    }
  }
  return count;
}
"""

_SCROLL_JS = r"""
() => {
  const candidates = Array.from(document.querySelectorAll('div')).filter((el) => {
    const s = getComputedStyle(el);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll')
      && el.scrollHeight > el.clientHeight + 80
      && el.querySelector('[data-review-id]');
  });
  const scroller = candidates.sort((a, b) => {
    const ca = a.querySelectorAll('[data-review-id]').length;
    const cb = b.querySelectorAll('[data-review-id]').length;
    return (cb - ca) || (b.scrollHeight - a.scrollHeight);
  })[0];
  if (!scroller) return { ok: false, cards: document.querySelectorAll('div[data-review-id].jftiEf').length };

  const before = scroller.scrollTop;
  // Lompat ke dasar panel. Kartu yang sudah terlihat diekstrak sebelum
  // menggulir, jadi tidak ada ulasan yang terlewat; ini memicu batch
  // berikutnya jauh lebih cepat daripada menggulir sedikit-sedikit.
  scroller.scrollTop = scroller.scrollHeight;
  scroller.dispatchEvent(new Event('scroll', { bubbles: true }));
  return {
    ok: true,
    before,
    top: scroller.scrollTop,
    height: scroller.scrollHeight,
    client: scroller.clientHeight,
    at_bottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8,
    cards: document.querySelectorAll('div[data-review-id].jftiEf').length,
  };
}
"""


_PANEL_JS = r"""
() => {
  const candidates = Array.from(document.querySelectorAll('div')).filter((el) => {
    const s = getComputedStyle(el);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll')
      && el.scrollHeight > el.clientHeight + 80
      && el.querySelector('[data-review-id]');
  });
  const scroller = candidates.sort((a, b) => {
    const ca = a.querySelectorAll('[data-review-id]').length;
    const cb = b.querySelectorAll('[data-review-id]').length;
    return (cb - ca) || (b.scrollHeight - a.scrollHeight);
  })[0];
  const body = (document.body.innerText || '').toLowerCase();
  if (!scroller) {
    return {
      ok: false,
      cards: document.querySelectorAll('div[data-review-id].jftiEf').length,
      loading: /memuat ulasan|loading reviews|memuat.../.test(body),
    };
  }
  return {
    ok: true,
    top: scroller.scrollTop,
    height: scroller.scrollHeight,
    client: scroller.clientHeight,
    at_bottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8,
    cards: document.querySelectorAll('div[data-review-id].jftiEf').length,
    loading: /memuat ulasan|loading reviews|memuat.../.test(body),
  };
}
"""

_FETCH_JSON_JS = r"""
async (url) => {
  const res = await fetch(url, {
    credentials: "include",
    signal: AbortSignal.timeout(30000),
    headers: { "Accept": "*/*" },
  });
  const text = await res.text();
  return { status: res.status, text: text };
}
"""


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def default_user_data_dir() -> str:
    """Path profil browser persisten (cookie sesi Google, bila login)."""
    return str(DEFAULT_PROFILE_DIR)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _now_jakarta() -> datetime:
    return datetime.now(tz=_JAKARTA)


def _parse_count(raw: Any) -> int:
    """Ubah "1.856" / "1,856" / "(122.709)" menjadi int."""
    if raw is None:
        return 0
    try:
        if isinstance(raw, (int, float)):
            return int(raw)
        digits = re.sub(r"[^\d]", "", str(raw))
        return int(digits) if digits else 0
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


def _is_google_maps_host(host: str) -> bool:
    host = (host or "").lower().strip(".")
    if not host:
        return False
    if host == "goo.gl" or host.endswith(".goo.gl"):
        return True
    parts = host.split(".")
    if len(parts) < 2 or "google" not in parts:
        return False
    idx = parts.index("google")
    if idx > 1:
        return False
    tld = ".".join(parts[idx + 1:])
    return tld in _SAFE_GOOGLE_TLDS


def build_search_url(query: str) -> str:
    """Bangun URL pencarian Google Maps dari teks biasa."""
    return (
        "https://www.google.com/maps/search/"
        f"{quote_plus((query or '').strip())}?hl=id&gl=id"
    )


def _with_locale(url: str) -> str:
    """Paksa bahasa Indonesia + region Indonesia tanpa merusak parameter data."""
    parts = urlparse(url)
    if not _is_google_maps_host(parts.hostname or ""):
        return url
    # Link pendek tidak perlu (dan terkadang tidak suka) parameter tambahan.
    if "goo.gl" in (parts.hostname or "").lower():
        return url
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["hl"] = "id"
    query["gl"] = "id"
    return urlunparse(parts._replace(query=urlencode(query)))


def normalize_maps_url(value: str) -> str:
    """Validasi input dan kembalikan URL Google Maps yang aman.

    - URL Google Maps / link pendek diterima.
    - Teks biasa dianggap sebagai query pencarian tempat.
    - Host selain Google ditolak.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError("Masukkan link lokasi Google Maps terlebih dahulu.")

    # Pengguna sering menempel teks berbagi ("Nama tempat\nhttps://maps.app...").
    url_match = re.search(r"https?://[^\s<>\"']+", value, re.I)
    if url_match:
        value = url_match.group(0).rstrip(".,;)]}")
    elif not re.match(r"^https?://", value, re.I):
        looks_like_domain = re.match(
            r"^(maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.|maps\.)?google\.[a-z.]+/maps)",
            value,
            re.I,
        )
        if not looks_like_domain:
            return build_search_url(value)
        value = "https://" + value

    parsed = urlparse(value)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Link harus diawali http:// atau https://.")
    if parsed.username or parsed.password:
        raise ValueError("Link tidak boleh memuat kredensial.")
    if not _is_google_maps_host(parsed.hostname or ""):
        raise ValueError("Hanya link Google Maps (google.com/maps atau maps.app.goo.gl) yang didukung.")
    if parsed.port and parsed.port not in (80, 443):
        raise ValueError("Port pada link tidak didukung.")

    clean_parts = parsed._replace(fragment="")
    return _with_locale(urlunparse(clean_parts))


def extract_place_id(value: str) -> str:
    """Ambil feature ID Google Maps (0x...:0x...) dari URL."""
    for pattern in (
        r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)",
        r"(?:data=|&)(?:[^?]*?)(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)",
    ):
        match = re.search(pattern, value or "")
        if match:
            return match.group(1)
    return ""


def _place_name_from_url(value: str) -> str:
    try:
        path = urlparse(value).path
        match = re.search(r"/maps/place/([^/]+)", path)
        if match:
            return _clean(unquote(match.group(1)).replace("+", " "))
    except Exception:
        pass
    return ""


def _place_stub(url: str, place_id: str) -> dict[str, Any]:
    return {
        "nama": _place_name_from_url(url) or "Tempat di Google Maps",
        "kategori": "",
        "alamat": "",
        "telepon": "",
        "website": "",
        "rating": None,
        "jumlah_ulasan": 0,
        "place_id": place_id,
        "link": url,
    }


def _html_to_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return _clean(text)


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_boq_url(
    place_id: str,
    sort_order: int = 1,
    token: str = "",
    page_size: int = _API_PAGE_SIZE,
) -> str:
    """Bangun URL endpoint ulasan internal Google Maps (GetLocalBoqProxy).

    Endpoint melayani hingga 60 ulasan per permintaan. Memakai 60 (bukan 10/20)
    membuat pengambilan ribuan ulasan jauh lebih cepat.
    """
    page_size = max(1, min(int(page_size), _API_PAGE_SIZE))
    if token:
        inner = [
            None, sort_order, None, None, None, None, None, None, None, page_size,
            None, [place_id], None, None, None, None, None, None, None, token,
        ]
    else:
        inner = [
            None, sort_order, None, None, None, None, None, None, None, page_size,
            None, [place_id],
        ]
    payload = [None, [None] * 9 + [inner]]
    encoded = quote(json.dumps(payload, separators=(",", ":")))
    return f"{_BOQ_ENDPOINT}?msc=gwsrpc&reqpld={encoded}"


def _parse_boq_text(text: str) -> tuple[list[Any], str]:
    if ")]}'" in text:
        text = text.split(")]}'", 1)[1]
    data = json.loads(text)
    if not isinstance(data, list) or len(data) < 2:
        raise ValueError("Struktur respons ulasan tidak dikenali.")
    payload = data[1]
    node = payload[10] if isinstance(payload, list) and len(payload) > 10 else None
    if not isinstance(node, list) or len(node) < 3 or not isinstance(node[2], list):
        raise ValueError("Daftar ulasan tidak ditemukan di respons.")
    next_token = node[6] if len(node) > 6 and isinstance(node[6], str) else ""
    return node[2], next_token


def _count_api_photos(value: Any) -> int:
    count = 0
    if isinstance(value, str):
        if "googleusercontent.com" in value:
            count += 1
    elif isinstance(value, list):
        for item in value:
            count += _count_api_photos(item)
    elif isinstance(value, dict):
        for item in value.values():
            count += _count_api_photos(item)
    return count


def _flatten_api_review(
    review: Any,
    *,
    place: dict[str, Any],
    now: datetime,
    order: int,
) -> Optional[dict[str, Any]]:
    """Ubah satu entri array endpoint ulasan Google menjadi baris tabel."""
    if not isinstance(review, list) or len(review) < 6:
        return None

    review_id = _first_str(review[5] if len(review) > 5 else "")
    if not review_id:
        return None

    rating = _int_value(review[1], 0)
    if rating < 1 or rating > 5:
        rating = None

    time_info = review[2] if isinstance(review[2], list) else []
    relative_date = _first_str(time_info[0] if time_info else "")
    ts: Optional[datetime] = None
    if len(time_info) > 2:
        raw_ms = _first_str(time_info[2])
        if raw_ms.isdigit():
            try:
                ts = datetime.fromtimestamp(int(raw_ms) / 1000.0, tz=_JAKARTA)
            except (OverflowError, OSError, ValueError):
                ts = None
    if ts is None:
        ts = _parse_review_date(relative_date, now)

    author = review[3] if isinstance(review[3], list) else []
    name = _first_str(author[0] if author else "")
    avatar = _first_str(author[1] if len(author) > 1 else "")
    profile = _first_str(author[2] if len(author) > 2 else "")
    reviewer_reviews = _int_value(author[3] if len(author) > 3 else 0, 0)
    reviewer_photos = _int_value(author[4] if len(author) > 4 else 0, 0)
    local_guide = False
    if len(author) > 5 and isinstance(author[5], list):
        local_guide = bool(author[5][1]) if len(author[5]) > 1 else bool(author[5][0])

    text = ""
    if len(review) > 27 and isinstance(review[27], str):
        text = review[27]
    if not text and len(review) > 28 and isinstance(review[28], str):
        text = review[28]
    text = _html_to_text(text)

    review_link = _first_str(review[12] if len(review) > 12 else "")
    photos = _count_api_photos(review[14] if len(review) > 14 else [])

    info_parts = []
    if local_guide:
        info_parts.append("Local Guide")
    if reviewer_reviews:
        info_parts.append(f"{reviewer_reviews:,} ulasan".replace(",", "."))
    if reviewer_photos:
        info_parts.append(f"{reviewer_photos:,} foto".replace(",", "."))
    info = " · ".join(info_parts)

    if profile.startswith("/"):
        profile = "https://www.google.com" + profile
    if profile and not profile.startswith("http"):
        profile = "https://www.google.com" + profile

    return {
        "nama": name,
        "rating": rating,
        "tanggal": relative_date,
        "perkiraan_tanggal": ts.strftime("%Y-%m-%d") if ts else "",
        "ulasan": text,
        "suka": 0,
        "foto": photos,
        "local_guide": local_guide,
        "info_reviewer": info,
        "ulasan_reviewer": reviewer_reviews,
        "foto_reviewer": reviewer_photos,
        "profil": profile,
        "foto_profil": avatar,
        "balasan_pemilik": "",
        "id_ulasan": review_id,
        "link_ulasan": review_link,
        "tempat": place.get("nama", ""),
        "link_tempat": place.get("link", ""),
        "_ts": ts,
        "_order": order,
    }


def _resolve_place_via_browser(
    url: str,
    *,
    headless: bool,
    real_chrome: bool,
    user_data_dir: str,
    log: Callable[[str], None],
) -> dict[str, Any]:
    """Buka halaman tempat hanya untuk mendapatkan place_id + metadata."""
    state: dict[str, Any] = {"place": {}, "logged_in": None, "url": url}

    def action(page) -> None:
        try:
            page.wait_for_selector("div[role='main']", timeout=20000)
        except Exception:
            pass
        _open_search_result_if_needed(page, log)
        try:
            page.wait_for_selector("h1", timeout=15000)
        except Exception:
            pass
        # Tunggu SPA Google Maps memperbarui URL dari /search/ ke /place/.
        try:
            for _ in range(20):
                if "/maps/place/" in (page.url or ""):
                    break
                page.wait_for_timeout(500)
        except Exception:
            pass
        try:
            state["logged_in"] = _has_google_session(page.context.cookies())
        except Exception:
            state["logged_in"] = None
        state["place"] = _extract_place(page)
        state["url"] = page.url
        _refresh_place(page, state)

        # URL bisa tetap berupa URL pencarian meskipun panel tempat sudah
        # terbuka. Ambil feature ID dari HTML/DOM sebagai cadangan.
        if not state["place"].get("place_id"):
            try:
                html = page.content() or ""
                match = re.search(
                    r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)", html
                )
                if match:
                    state["place"]["place_id"] = match.group(1)
            except Exception:
                pass

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="id-ID",
        ) as session:
            response = session.fetch(url, page_action=action, timeout=90000)
        final_url = getattr(response, "url", None) or state.get("url") or url
        state["url"] = final_url
        place = state.get("place") or {}
        if not place.get("place_id"):
            place["place_id"] = extract_place_id(final_url)
        place["link"] = final_url
        state["place"] = place
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ Gagal membuka halaman tempat: {exc}")
    return state


def _has_google_session(cookies: list[dict[str, Any]]) -> bool:
    """True jika cookie login Google ditemukan di profil browser."""
    names = {str(c.get("name") or "") for c in cookies if isinstance(c, dict)}
    # Cookie akun Google yang umum. Hindari cookie anonim seperti NID saja.
    if names & {"SID", "HSID", "SSID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"}:
        return True
    return False


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# --------------------------------------------------------------------------- #
# Tanggal relatif
# --------------------------------------------------------------------------- #

_UNIT_DAYS = {
    "menit": 1 / 1440,
    "jam": 1 / 24,
    "hari": 1,
    "minggu": 7,
    "bulan": 30,
    "tahun": 365,
    "minute": 1 / 1440,
    "minutes": 1 / 1440,
    "hour": 1 / 24,
    "hours": 1 / 24,
    "day": 1,
    "days": 1,
    "week": 7,
    "weeks": 7,
    "month": 30,
    "months": 30,
    "year": 365,
    "years": 365,
}


def _parse_review_date(raw: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Perkirakan tanggal absolut dari label relatif Google Maps.

    Google Maps menampilkan tanggal relatif ("3 bulan lalu"). Nilai absolut
    di sini adalah perkiraan (bulan = 30 hari, tahun = 365 hari), bukan tanggal
    resmi dari Google.
    """
    raw = _clean(raw)
    if not raw:
        return None
    now = now or _now_jakarta()
    low = raw.lower()

    if re.search(r"\b(baru saja|sebentar lalu|just now|right now)\b", low):
        return now
    if re.search(r"\b(kemarin|yesterday)\b", low):
        return now - timedelta(days=1)

    match = re.search(
        r"(\d+)\s*(menit|jam|hari|minggu|bulan|tahun|minute|minutes|hour|hours|day|days|week|weeks|month|months|year|years)",
        low,
    )
    if match:
        amount = int(match.group(1))
        days = _UNIT_DAYS.get(match.group(2), 0)
        if days:
            try:
                return now - timedelta(days=amount * days)
            except OverflowError:
                return None

    # Label bahasa Inggris seperti "a day ago" / "an hour ago".
    match = re.search(r"\b(?:a|an)\s+(minute|hour|day|week|month|year)\b", low)
    if match:
        days = _UNIT_DAYS.get(match.group(1), 0)
        if days:
            return now - timedelta(days=days)

    if date_parser is not None:
        # Beberapa ulasan lama bisa menampilkan tanggal absolut.
        try:
            parsed = date_parser.parse(raw, default=now, dayfirst=True)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_JAKARTA)
            return parsed.astimezone(_JAKARTA)
        except (ValueError, OverflowError, TypeError):
            return None
    return None


def _parse_reviewer_info(info: str) -> tuple[int, int]:
    """Ambil jumlah ulasan/foto milik reviewer dari teks profilnya."""
    info = _clean(info)
    reviews = photos = 0
    match = re.search(r"([\d.,]+)\s*(?:ulasan|review)", info, re.I)
    if match:
        reviews = _parse_count(match.group(1))
    match = re.search(r"([\d.,]+)\s*(?:foto|photo)", info, re.I)
    if match:
        photos = _parse_count(match.group(1))
    return reviews, photos


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
    """Lewati layar consent Google bila muncul (bukan CAPTCHA)."""
    try:
        host = (urlparse(page.url).hostname or "").lower()
        body = (page.inner_text("body") or "").lower()
        needs_consent = "consent" in host or "sebelum anda melanjutkan" in body or "before you continue" in body
        if not needs_consent:
            return
        log("🍪 Layar persetujuan Google terdeteksi, mencoba menerima…")
        for label in ("Terima semua", "Accept all", "Setuju", "I agree"):
            try:
                button = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
                if button and button.is_visible():
                    _click_element(button)
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                continue
    except Exception:
        pass


def _open_search_result_if_needed(page, log: Callable[[str], None]) -> None:
    """Jika yang terbuka adalah daftar hasil, buka hasil pertama."""
    try:
        if page.query_selector("div[role='main'] h1"):
            return
        links = page.query_selector_all('a[href*="/maps/place/"]')
        for link in links[:3]:
            href = link.get_attribute("href") or ""
            if "/maps/place/" not in href:
                continue
            log("📍 Membuka hasil tempat pertama dari daftar pencarian…")
            if _click_element(link):
                page.wait_for_timeout(4500)
                return
    except Exception:
        return


def _open_reviews(page, log: Callable[[str], None]) -> None:
    """Buka tab Ulasan/Reviews pada panel tempat."""
    try:
        tabs = page.query_selector_all('button[role="tab"]')
    except Exception:
        tabs = []

    for tab in tabs:
        try:
            text = (tab.inner_text() or "").strip().lower()
            aria = (tab.get_attribute("aria-label") or "").strip().lower()
            selected = (tab.get_attribute("aria-selected") or "").lower()
            if text in ("ulasan", "reviews") or aria.startswith(("ulasan", "review")):
                if selected == "true":
                    log("⭐ Tab ulasan sudah aktif.")
                    return
                log("⭐ Membuka tab ulasan…")
                if _click_element(tab):
                    page.wait_for_timeout(3500)
                return
        except Exception:
            continue

    # Cadangan: tombol/tautan lain yang mengarah ke ulasan.
    try:
        for el in page.query_selector_all("button, a"):
            label = ((el.get_attribute("aria-label") or "") + " " + (el.inner_text() or "")).lower()
            if "lihat ulasan" in label or "lihat semua ulasan" in label or "see all reviews" in label:
                log("⭐ Membuka daftar ulasan…")
                if _click_element(el):
                    page.wait_for_timeout(3500)
                return
    except Exception:
        pass
    log("⚠️ Tab ulasan tidak ditemukan; mencoba membaca ulasan yang tampil.")


def _extract_place(page) -> dict[str, Any]:
    try:
        raw = page.evaluate(_PLACE_JS) or {}
    except Exception:
        raw = {}

    url = _first_str(raw.get("halaman"), page.url)
    place_id = ""
    try:
        match = re.search(r"!1s(0x[0-9a-f]+:0x[0-9a-f]+)", page.url, re.I)
        if match:
            place_id = match.group(1)
    except Exception:
        pass

    name = _first_str(raw.get("nama"), "Tempat di Google Maps")
    name = re.sub(r"\s*-\s*Google Maps\s*$", "", name, flags=re.I).strip()

    return {
        "nama": name or "Tempat di Google Maps",
        "kategori": _clean(raw.get("kategori")),
        "alamat": _clean(raw.get("alamat")),
        "telepon": _clean(raw.get("telepon")),
        "website": _clean(raw.get("website")),
        "rating": _parse_float(raw.get("rating_teks")),
        "jumlah_ulasan": _parse_count(raw.get("jumlah_ulasan_teks")),
        "place_id": place_id,
        "link": url,
    }


def _extract_reviews(page) -> list[dict[str, Any]]:
    try:
        data = page.evaluate(_REVIEWS_JS)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _body_limited(page) -> bool:
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        return False
    return "tampilan terbatas" in body or "limited view" in body


def _expand_and_wait(page, wait_ms: int = 700) -> int:
    """Buka semua teks ulasan yang terpotong, lalu beri waktu render."""
    try:
        expanded = page.evaluate(_EXPAND_JS) or 0
    except Exception:
        expanded = 0
    if expanded:
        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            pass
    return int(expanded)


def _refresh_place(page, state: dict[str, Any]) -> None:
    """Perbarui URL/place_id setelah Google Maps selesai mengarahkan halaman.

    Jangan menimpa nama/rating/jumlah ulasan yang sudah terbaca dari panel
    utama dengan nilai kosong dari DOM yang sedang dalam masa transisi.
    """
    latest = _extract_place(page)
    current = state.get("place") or {}
    for key in ("kategori", "alamat", "telepon", "website", "place_id"):
        if not current.get(key) and latest.get(key):
            current[key] = latest[key]
    if not current.get("nama") or current["nama"] == "Tempat di Google Maps":
        if latest.get("nama"):
            current["nama"] = latest["nama"]
    if not current.get("rating") and latest.get("rating"):
        current["rating"] = latest["rating"]
    if not current.get("jumlah_ulasan") and latest.get("jumlah_ulasan"):
        current["jumlah_ulasan"] = latest["jumlah_ulasan"]
    current["link"] = page.url or current.get("link", "")
    state["place"] = current
    state["url"] = page.url


def _flatten_review(
    item: dict[str, Any],
    *,
    place: dict[str, Any],
    now: datetime,
    order: int,
) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    text = _clean(item.get("teks"))
    name = _clean(item.get("nama"))
    if not text and not name:
        return None

    rating = _parse_float(item.get("rating_teks"))
    date_raw = _clean(item.get("tanggal"))
    date_dt = _parse_review_date(date_raw, now)
    reviews_count, photos_count = _parse_reviewer_info(item.get("info_reviewer") or "")
    like_text = _clean(item.get("suka_teks"))
    likes = _parse_count(like_text) if re.search(r"\d", like_text) else 0

    profile = _clean(item.get("profil"))
    if profile.startswith("/"):
        profile = "https://www.google.com" + profile
    if profile and not profile.startswith("http"):
        profile = "https://www.google.com/maps" + profile.lstrip("/")

    row = {
        "nama": name,
        "rating": rating,
        "tanggal": date_raw,
        "perkiraan_tanggal": date_dt.strftime("%Y-%m-%d") if date_dt else "",
        "ulasan": text,
        "suka": likes,
        "foto": int(item.get("foto") or 0),
        "local_guide": "local guide" in (item.get("info_reviewer") or "").lower(),
        "info_reviewer": _clean(item.get("info_reviewer")),
        "ulasan_reviewer": reviews_count,
        "foto_reviewer": photos_count,
        "profil": profile,
        "foto_profil": _clean(item.get("foto_profil")),
        "balasan_pemilik": _clean(item.get("balasan_pemilik")),
        "id_ulasan": _clean(item.get("id")),
        "tempat": place.get("nama", ""),
        "link_tempat": place.get("link", ""),
        "_ts": date_dt,
        "_order": order,
    }
    return row


def _dedupe_key(row: dict[str, Any]) -> str:
    rid = _first_str(row.get("id_ulasan"))
    if rid:
        return rid
    return "|".join(
        [
            _first_str(row.get("nama")),
            _first_str(row.get("tanggal")),
            _first_str(row.get("ulasan"))[:160],
        ]
    )


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #


def _scrape_session(
    *,
    session,
    url: str,
    state: dict[str, Any],
    log: Callable[[str], None],
    wait_ms: int,
    max_minutes: int,
    max_reviews: int,
    stop_event=None,
    checkpoint_cb=None,
) -> list[dict[str, Any]]:
    started = time.time()
    pacing = {"last": time.time()}
    collected: dict[str, dict[str, Any]] = {}

    def add_items(items: list[dict[str, Any]], now: datetime) -> int:
        added = 0
        for item in items:
            row = _flatten_review(
                item,
                place=state.get("place") or {},
                now=now,
                order=len(collected),
            )
            if not row:
                continue
            key = _dedupe_key(row)
            if not key or key in collected:
                continue
            row["_order"] = len(collected)
            collected[key] = row
            added += 1
        if checkpoint_cb and added:
            rows = list(collected.values())
            checkpoint_cb([{k: v for k, v in row.items() if not k.startswith("_")}
                           for row in (rows[:max_reviews] if max_reviews else rows)])
        return added

    def action(page) -> None:
        now = _now_jakarta()
        _handle_consent(page, log)

        try:
            page.wait_for_selector("div[role='main']", timeout=25000)
        except Exception:
            log("⚠️ Panel utama Google Maps tidak terlihat. Mencoba lanjut…")

        _open_search_result_if_needed(page, log)

        try:
            page.wait_for_selector("h1", timeout=20000)
        except Exception:
            log("⚠️ Nama tempat belum muncul; halaman mungkin bukan halaman tempat.")

        try:
            state["logged_in"] = _has_google_session(page.context.cookies())
        except Exception:
            state["logged_in"] = None

        state["place"] = _extract_place(page)
        place_name = state["place"].get("nama") or "tempat ini"
        state["url"] = page.url
        log(f"📍 Tempat: {place_name}")
        if state["place"].get("alamat"):
            log(f"   Alamat: {state['place']['alamat'][:160]}")
        if state.get("logged_in"):
            log("🔐 Sesi Google aktif.")
        else:
            log("🔓 Tanpa login Google — Google biasanya membatasi daftar ulasan.")

        _open_reviews(page, log)

        try:
            page.wait_for_selector("div[data-review-id]", timeout=25000)
        except Exception:
            log("⚠️ Tidak ada kartu ulasan yang terlihat.")

        state["limited"] = _body_limited(page)
        if state["limited"]:
            log(
                "🚧 Google menampilkan 'tampilan terbatas' (biasanya karena belum "
                "login). Ulasan yang bisa diambil mungkin hanya sebagian kecil."
            )

        _expand_and_wait(page, 900)
        initial = _extract_reviews(page)
        add_items(initial, now)
        log(f"🧩 Ulasan awal: {len(collected)}")

        no_new = 0
        scrolls = 0
        while scrolls < _MAX_SCROLLS and no_new < _STOP_AFTER:
            if stop_event is not None and stop_event.is_set():
                break
            if max_reviews and len(collected) >= max_reviews:
                log(f"🛑 Batas maksimum {max_reviews} ulasan tercapai.")
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                log(f"⏱️ Batas waktu {max_minutes} menit tercapai.")
                break
            if (time.time() - started) > _MAX_RUNTIME_MIN * 60:
                log(f"⏱️ Batas keras {_MAX_RUNTIME_MIN} menit tercapai.")
                break

            # Baca kartu yang sedang terlihat SEBELUM menggulir. Ini penting
            # karena daftar Google Maps bisa me-render ulang kartu lama.
            _expand_and_wait(page, 500)
            add_items(_extract_reviews(page), now)

            try:
                before_stats = page.evaluate(_PANEL_JS) or {}
            except Exception:
                before_stats = {}

            try:
                stats = page.evaluate(_SCROLL_JS) or {}
            except Exception:
                stats = {}
            scrolls += 1
            total_before_scroll = len(collected)

            # Google Maps memuat batch ulasan secara lazy. Jangan menganggap
            # "tidak ada ulasan baru" hanya karena batch belum muncul setelah
            # satu kali tunggu singkat. Polling ini yang membedakan daftar
            # yang masih memuat dari daftar yang benar-benar sudah habis.
            new_after_scroll = 0
            loading_seen = False
            after_stats = {}
            for attempt in range(7):
                if stop_event is not None and stop_event.is_set():
                    break
                pause = max(600, min(int(wait_ms), 1500)) if attempt == 0 else 650
                page.wait_for_timeout(pause)
                _expand_and_wait(page, 300)
                add_items(_extract_reviews(page), now)
                new_after_scroll = len(collected) - total_before_scroll

                try:
                    after_stats = page.evaluate(_PANEL_JS) or {}
                except Exception:
                    after_stats = {}

                grew = (
                    int(after_stats.get("height") or 0)
                    > int(before_stats.get("height") or 0) + 10
                )
                if after_stats.get("loading"):
                    loading_seen = True
                if new_after_scroll > 0:
                    break
                if not grew and not after_stats.get("loading") and attempt >= 2:
                    break

            total = len(collected)
            if new_after_scroll > 0:
                no_new = 0
                log(
                    f"  ➕ {new_after_scroll} ulasan baru setelah gulir #{scrolls} "
                    f"· total {total}"
                )
            else:
                # Jangan menghitung "tidak ada ulasan baru" selama panel masih
                # berada di tengah daftar yang sudah termuat. Batch berikutnya
                # baru dimuat ketika panel mencapai dasar.
                try:
                    at_bottom_now = bool(after_stats.get("at_bottom"))
                except UnboundLocalError:
                    at_bottom_now = bool(stats.get("at_bottom"))
                stuck = (
                    stats.get("top") is not None
                    and stats.get("before") is not None
                    and stats.get("top") == stats.get("before")
                )
                if at_bottom_now or stuck:
                    no_new += 1
                else:
                    no_new = 0

            if scrolls % 3 == 0:
                log(
                    f"  ↕️ gulir #{scrolls} · kartu terlihat: {stats.get('cards', 0)} "
                    f"· ulasan unik: {total}"
                )
            if loading_seen and no_new >= 1:
                # Beri waktu ekstra jika Google masih menampilkan indikator memuat.
                page.wait_for_timeout(1200)
                _expand_and_wait(page, 300)
                add_items(_extract_reviews(page), now)
                if len(collected) > total:
                    total = len(collected)
                    no_new = 0

            if time.time() - pacing["last"] >= _PAUSE_EVERY_S:
                log(
                    f"⏸️ Jeda {_PAUSE_DURATION_S} detik untuk mengurangi risiko "
                    "rate-limit…"
                )
                if stop_event is not None:
                    stop_event.wait(_PAUSE_DURATION_S)
                else:
                    page.wait_for_timeout(_PAUSE_DURATION_S * 1000)
                pacing["last"] = time.time()

            # Tanpa login, biasanya langsung mentok di 5 ulasan. Jangan
            # menghabiskan waktu menggulir panel yang sudah di dasar.
            if (
                state.get("limited")
                and not state.get("logged_in")
                and no_new >= 2
                and bool(stats.get("at_bottom"))
            ):
                log("⛔ Tampilan terbatas sudah mentok; berhenti menggulir.")
                break

            if bool(stats.get("at_bottom")) and no_new >= 4:
                log("🏁 Mencapai dasar daftar ulasan.")
                break

        # Ambil sekali lagi setelah jeda singkat untuk menangkap render terakhir.
        page.wait_for_timeout(600)
        _expand_and_wait(page, 900)
        add_items(_extract_reviews(page), now)
        log(f"✅ Selesai menggulir ({scrolls} gulir).")
        state["limited"] = state.get("limited") or _body_limited(page)
        _refresh_place(page, state)

    session.fetch(
        url,
        page_action=action,
        timeout=max(180000, (max_minutes or _MAX_RUNTIME_MIN) * 60 * 1000 + 180000),
    )

    rows = list(collected.values())
    rows.sort(key=lambda r: (r.get("_order") or 0))
    if max_reviews:
        rows = rows[:max_reviews]
    return [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]


def _scrape_api_reviews_stealth(
    *,
    place: dict[str, Any],
    sort: str,
    max_reviews: int,
    max_minutes: int,
    include_empty: bool,
    headless: bool,
    real_chrome: bool,
    user_data_dir: str,
    log: Callable[[str], None],
    stop_event=None,
    checkpoint_cb=None,
) -> tuple[list[dict[str, Any]], str]:
    """Sama seperti mode cepat, tetapi request dijalankan dari browser samaran."""
    place_id = place.get("place_id") or ""
    if not place_id:
        return [], "no_place_id"

    now = _now_jakarta()
    started = time.time()
    sort_order = _SORT_ORDERS.get(sort, 1)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_tokens: set[str] = set()
    state = {"token": "", "page": 0, "stopped": "exhausted"}

    def add_reviews(reviews: Any) -> int:
        added = 0
        for review in reviews:
            row = _flatten_api_review(
                review,
                place=place,
                now=now,
                order=len(rows),
            )
            if not row:
                continue
            if not include_empty and not row["ulasan"].strip():
                continue
            rid = row["id_ulasan"]
            if rid in seen:
                continue
            seen.add(rid)
            row["_order"] = len(rows)
            rows.append(row)
            added += 1
        return added

    def action(page) -> None:
        try:
            page.wait_for_timeout(2500)
        except Exception:
            pass

        while True:
            if stop_event is not None and stop_event.is_set():
                state["stopped"] = "cancelled"
                break
            if max_reviews and len(rows) >= max_reviews:
                state["stopped"] = "max_reviews"
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                state["stopped"] = "timeout"
                break

            url = _build_boq_url(place_id, sort_order, state["token"])
            result = None
            for attempt in range(_API_MAX_RETRIES + 1):
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    result = page.evaluate(_FETCH_JSON_JS, url)
                except Exception as exc:  # noqa: BLE001
                    result = None
                    log(f"⚠️ Request samaran gagal: {exc}")
                if isinstance(result, dict) and result.get("status") == 200:
                    break
                status = result.get("status") if isinstance(result, dict) else "?"
                if attempt < _API_MAX_RETRIES:
                    wait_s = 2 + attempt * 3
                    log(f"⚠️ Status {status}; ulang dalam {wait_s}s…")
                    if stop_event is not None:
                        stop_event.wait(wait_s)
                    else:
                        page.wait_for_timeout(wait_s * 1000)

            if not isinstance(result, dict) or result.get("status") != 200:
                state["stopped"] = "blocked" if rows else "error"
                log("⚠️ Browser samaran tidak mendapat respons ulasan.")
                break

            try:
                reviews, next_token = _parse_boq_text(str(result.get("text") or ""))
            except Exception as exc:  # noqa: BLE001
                state["stopped"] = "parse_error" if rows else "error"
                log(f"⚠️ Gagal membaca respons samaran: {exc}")
                break

            state["page"] += 1
            added = add_reviews(reviews)
            if checkpoint_cb and added:
                checkpoint_cb(rows[:max_reviews] if max_reviews else rows)
            if state["page"] == 1 or state["page"] % 5 == 0 or added == 0:
                elapsed_min = max((time.time() - started) / 60.0, 0.01)
                rate = len(rows) / elapsed_min
                log(
                    f"🥷 Halaman {state['page']} · +{added} · "
                    f"total {len(rows)} ulasan · {rate:.0f}/menit"
                )

            if max_reviews and len(rows) >= max_reviews:
                state["stopped"] = "max_reviews"
                break
            if not next_token or next_token in seen_tokens:
                state["stopped"] = "exhausted"
                break
            seen_tokens.add(next_token)
            state["token"] = next_token
            try:
                page.wait_for_timeout(int(_API_PAGE_DELAY_S * 1000))
            except Exception:
                time.sleep(_API_PAGE_DELAY_S)

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="id-ID",
        ) as session:
            session.fetch(
                "https://www.google.com/maps?hl=id",
                page_action=action,
                timeout=max(180000, (max_minutes or _MAX_RUNTIME_MIN) * 60 * 1000 + 180000),
            )
    except Exception as exc:  # noqa: BLE001
        log(f"⚠️ Mode samaran berhenti: {exc}")
        state["stopped"] = "blocked" if rows else "error"

    if max_reviews:
        rows = rows[:max_reviews]
    return rows, state["stopped"]


def scrape_reviews(
    url_input: str,
    *,
    method: str = "stealth",
    sort: str = "relevant",
    headless: bool = True,
    real_chrome: bool = True,
    user_data_dir: Optional[str] = None,
    wait_ms: int = 1600,
    max_minutes: int = 10,
    max_reviews: int = 0,
    include_empty_reviews: bool = False,
    log_cb: Optional[Callable[[str], None]] = None,
    stop_event=None,
    checkpoint_cb=None,
) -> dict[str, Any]:
    """Scrape ulasan dari sebuah link tempat Google Maps.

    ``method``:
      - ``"api"``     : alias lama untuk mode ``"stealth"``.
      - ``"stealth"`` : endpoint yang sama, tetapi request dijalankan dari
                        browser samaran ``StealthySession``.
      - ``"browser"`` : gulir tampilan Google Maps (lebih lambat, biasanya
                        berhenti di beberapa ratus kartu).

    Mengembalikan dict:
      rows, place, limited, logged_in, error, message, url, method,
      api_stopped.
    """
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    method = "browser" if method == "browser" else "stealth"
    sort = sort if sort in _SORT_ORDERS else "relevant"

    def log(msg: str) -> None:
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    result: dict[str, Any] = {
        "rows": [],
        "place": {},
        "limited": False,
        "logged_in": None,
        "error": None,
        "message": "",
        "url": "",
        "method": method,
        "api_stopped": None,
        "include_empty_reviews": bool(include_empty_reviews),
    }

    try:
        url = normalize_maps_url(url_input)
    except ValueError as exc:
        result["error"] = "invalid_url"
        result["message"] = str(exc)
        return result

    if max_reviews < 0:
        max_reviews = 0
    if max_minutes < 0:
        max_minutes = 0

    log(f"🔗 Membuka: {url}")
    if method == "api":
        log("⚡ Mode cepat: endpoint ulasan Google (tanpa menggulir browser).")
    elif method == "stealth":
        log(
            "🥷 Mode samaran: request ulasan dijalankan dari browser "
            "StealthySession."
        )
    else:
        log(
            "🧭 Mode browser: "
            + ("headless" if headless else "browser tampak")
            + (" · Chrome asli" if real_chrome else " · Chromium Scrapling")
        )

    if method in ("api", "stealth"):
        place_id = extract_place_id(url)
        place = _place_stub(url, place_id)

        if not place_id:
            log("🔎 Place ID tidak ada di link; membuka browser untuk resolve…")
            resolved = _resolve_place_via_browser(
                url,
                headless=headless,
                real_chrome=real_chrome,
                user_data_dir=user_data_dir,
                log=log,
            )
            place = resolved.get("place") or place
            place_id = place.get("place_id") or extract_place_id(
                resolved.get("url") or ""
            )
            place["place_id"] = place_id
            url = _first_str(resolved.get("url"), url)
            place["link"] = url
            result["logged_in"] = resolved.get("logged_in")

        if stop_event is not None and stop_event.is_set():
            return result
        if place_id:
            log(f"🆔 Place ID: {place_id}")
            if place.get("nama"):
                log(f"📍 Tempat: {place['nama']}")
            log(
                "🧮 Mengambil ulasan per halaman; ini bisa memakan waktu untuk "
                "tempat dengan ribuan ulasan."
            )
            try:
                rows, stopped = _scrape_api_reviews_stealth(
                    place=place,
                    sort=sort,
                    max_reviews=max_reviews,
                    max_minutes=max_minutes,
                    include_empty=include_empty_reviews,
                    headless=headless,
                    real_chrome=real_chrome,
                    user_data_dir=user_data_dir,
                    log=log,
                    stop_event=stop_event, checkpoint_cb=checkpoint_cb,
                )
            except Exception as exc:  # noqa: BLE001
                rows, stopped = [], f"error: {exc}"
                log(f"⚠️ Mode cepat gagal: {exc}")

            result["rows"] = rows
            result["place"] = place
            result["url"] = url
            result["api_stopped"] = stopped

            if rows:
                log(
                    f"🏁 Selesai · {len(rows)} ulasan unik diambil lewat "
                + ("mode samaran." if method == "stealth" else "mode cepat.")
                )
                if stopped == "max_reviews":
                    result["message"] = (
                        f"Dihentikan karena batas maksimum {max_reviews} ulasan."
                    )
                elif stopped == "timeout":
                    result["message"] = (
                        "Dihentikan karena batas waktu. Naikkan waktu maksimum "
                        "untuk mengambil lebih banyak."
                    )
                elif stopped == "exhausted":
                    result["message"] = (
                        "Endpoint Google tidak lagi memberikan halaman ulasan "
                        "berikutnya."
                    )
                return result

            if stopped == "exhausted":
                result["error"] = "no_reviews"
                result["message"] = (
                    "Endpoint ulasan Google tidak mengembalikan ulasan untuk "
                    "tempat ini."
                )
                log(f"⚠️ {result['message']}")
                return result

            log(
                "↩️ Mode cepat tidak mengembalikan data; beralih ke mode browser "
                "sebagai cadangan."
            )
            method = "browser"
            result["method"] = "browser"
        else:
            log(
                "↩️ Place ID tidak ditemukan; beralih ke mode browser sebagai "
                "cadangan."
            )
            method = "browser"
            result["method"] = "browser"

    state: dict[str, Any] = {
        "place": {},
        "logged_in": None,
        "limited": False,
        "url": "",
    }

    if stop_event is not None and stop_event.is_set():
        return result
    if method == "browser":
        log(
            "🧭 Mode browser: "
            + ("headless" if headless else "browser tampak")
            + (" · Chrome asli" if real_chrome else " · Chromium Scrapling")
        )
        try:
            with StealthySession(
                headless=headless,
                real_chrome=real_chrome,
                user_data_dir=user_data_dir,
                locale="id-ID",
            ) as session:
                rows = _scrape_session(
                    session=session,
                    url=url,
                    state=state,
                    log=log,
                    wait_ms=max(300, int(wait_ms)),
                    max_minutes=max_minutes,
                    max_reviews=max_reviews,
                    stop_event=stop_event, checkpoint_cb=checkpoint_cb,
                )
        except Exception as exc:  # noqa: BLE001
            result["error"] = "browser_error"
            result["message"] = f"Gagal menjalankan browser: {exc}"
            result["place"] = state.get("place") or result.get("place") or {}
            result["limited"] = bool(state.get("limited"))
            result["logged_in"] = state.get("logged_in")
            return result

    place = state.get("place") or result.get("place") or {}
    result["rows"] = rows
    result["place"] = place
    result["limited"] = bool(state.get("limited"))
    result["logged_in"] = state.get("logged_in")
    result["url"] = _first_str(state.get("url"), url)
    result["method"] = method

    if rows:
        log(
            f"🏁 Selesai · {len(rows)} ulasan unik dari "
            f"{len({r['nama'] for r in rows if r['nama']})} reviewer."
        )
        if state.get("limited"):
            result["message"] = (
                "Google Maps menampilkan versi terbatas. Login ke akun Google "
                "melalui sidebar untuk membuka lebih banyak ulasan (batas tetap "
                "ditentukan Google)."
            )
        return result

    if state.get("limited"):
        result["error"] = "limited_view"
        result["message"] = (
            "Google Maps membatasi tampilan ulasan dan tidak ada ulasan yang bisa "
            "dibaca. Periksa tautan tempat di browser biasa."
        )
    else:
        result["error"] = "no_reviews"
        result["message"] = (
            "Tidak ada ulasan yang berhasil dibaca. Pastikan link menunjuk ke "
            "tempat (bukan daftar pencarian), tempat memiliki ulasan publik, dan "
            "halaman tidak sedang memblokir otomatis."
        )
    log(f"⚠️ {result['message']}")
    return result


# --------------------------------------------------------------------------- #
# Ekspor
# --------------------------------------------------------------------------- #


def _csv_safe(value: Any) -> Any:
    """Cegah formula injection saat CSV dibuka di Excel/Sheets."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    import csv
    import io

    columns = [
        "nama", "rating", "tanggal", "perkiraan_tanggal", "ulasan", "suka", "foto",
        "local_guide", "info_reviewer", "ulasan_reviewer", "foto_reviewer",
        "profil", "balasan_pemilik", "id_ulasan", "link_ulasan", "tempat",
        "link_tempat",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(
        {key: _csv_safe(value) for key, value in row.items()} for row in rows
    )
    return buffer.getvalue()


def rows_to_json(rows: list[dict[str, Any]], place: Optional[dict[str, Any]] = None) -> str:
    return json.dumps(
        {"tempat": place or {}, "ulasan": rows},
        ensure_ascii=False,
        indent=2,
        default=str,
    )
