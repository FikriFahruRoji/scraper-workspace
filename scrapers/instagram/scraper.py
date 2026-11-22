"""Scraping komentar dan konten Instagram menggunakan Scrapling.

Dua mode pengambilan:

1. **Komentar post** — tempel link post/reel Instagram. Scrapling membuka
   halaman post, membaca data komentar yang sudah tertanam di HTML, lalu
   mengklik tombol *load more comments* / *view replies* sambil menangkap
   respons GraphQL internal Instagram. Hasilnya: komentar, balasan, suka,
   waktu, dan profil komentator.
2. **Konten** — masukkan username, kata kunci, atau hashtag. Scrapling membuka
   halaman profil / pencarian / hashtag, menggulir daftar post, dan menangkap
   data post dari HTML + respons GraphQL.

Catatan penting:
  - Instagram membatasi konten untuk pengunjung yang belum login (biasanya
    ±10 komentar dan ±12 post). Login manual dengan akun sendiri lewat tombol
    **Login** membuka lebih banyak data. Aplikasi tidak membaca kata sandi.
  - Aplikasi ini **tidak** melewati CAPTCHA, paywall, login, atau kontrol akses
    apa pun, dan tidak memakai proxy untuk menyamarkan diri.
  - Gunakan hanya untuk konten publik yang Anda berhak akses, patuhi Ketentuan
    Layanan Instagram/Meta, dan jangan mengumpulkan data pribadi/sensitif.
  - Instagram dapat mengubah struktur halaman/endpoint kapan saja. Hasil tidak
    dijamin lengkap.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from datetime import date, datetime, timezone
from html import unescape
from typing import Any, Callable, Iterator, Optional
from urllib.parse import quote, urlparse
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession

# --------------------------------------------------------------------------- #
# Konstanta & path
# --------------------------------------------------------------------------- #

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = _PROJECT_DIR / "sessions" / "instagram_profile"

_JAKARTA = ZoneInfo("Asia/Jakarta")
_UTC = timezone.utc

_ALLOWED_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "m.instagram.com",
    "instagr.am",
    "www.instagr.am",
}

_RESERVED_USERNAMES = {
    "p",
    "reel",
    "reels",
    "tv",
    "explore",
    "accounts",
    "about",
    "developer",
    "directory",
    "legal",
    "privacy",
    "terms",
    "web",
    "api",
    "graphql",
    "challenge",
    "session",
    "emails",
    "oauth",
    "direct",
    "stories",
    "s",
    "static",
}

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{5,40}$")
_POST_CODE_RE = re.compile(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")

_SCRIPT_JSON_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S | re.I
)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*["\'](.*?)["\']', re.S)

_NETWORK_MARKERS = (
    "XIGComment",
    "comments_connection",
    "XIGPolaris",
    "xig_user_by_username",
    "polaris_ordered_timeline_connection",
    "xig_logged_out_popular_search_media_info",
    "xdt_fbsearch__top_serp_graphql",
    "edge_owner_to_timeline_media",
    "xdt_api__v1__feed__user_timeline_graphql_connection",
    "xdt_api__v1__media__media_id__comments",
    "shortcode",
)

_MAX_SCROLLS = 400          # pengaman: jangan menggulir selamanya
_STOP_AFTER = 6             # berhenti setelah N putaran tanpa data baru
_MAX_RUNTIME_MIN = 30       # batas keras satu proses (menit)

_PAUSE_EVERY_S = 120        # jeda berkala setiap 2 menit
_PAUSE_DURATION_S = 20      # durasi jeda (detik)

_FETCH_TIMEOUT_MS = 180_000  # batas satu navigasi/aksi browser
_CHECK_TIMEOUT_MS = 30_000   # batas cek sesi singkat

# --------------------------------------------------------------------------- #
# JavaScript
# --------------------------------------------------------------------------- #

# Menggulir window + semua kontainer yang bisa di-scroll. Instagram kadang
# memakai div internal, bukan window, sebagai kontainer daftar post.
_SCROLL_CONTENT_JS = r"""
() => {
  const links = Array.from(document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]'));
  if (links.length) {
    try { links[links.length - 1].scrollIntoView({ block: 'end' }); } catch (e) {}
  }
  window.scrollTo(0, document.body.scrollHeight);
  const candidates = Array.from(document.querySelectorAll('div, main, section'));
  for (const el of candidates) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 100) {
      el.scrollTop = el.scrollHeight;
    }
  }
  return { links: links.length, height: document.body.scrollHeight };
}
"""

_SCROLL_COMMENTS_JS = r"""
() => {
  let moved = 0;
  const candidates = Array.from(document.querySelectorAll('div, main, section, ul'));
  for (const el of candidates) {
    const s = getComputedStyle(el);
    if ((s.overflowY === 'auto' || s.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 60) {
      if (el.scrollTop !== el.scrollHeight) {
        el.scrollTop = el.scrollHeight;
        moved += 1;
      }
    }
  }
  window.scrollTo(0, document.body.scrollHeight);
  return moved;
}
"""

_COMMENT_SCROLLER_JS = r"""
() => {
  const candidates = Array.from(document.querySelectorAll('div, main, section, ul'));
  for (const el of candidates) {
    const style = getComputedStyle(el);
    if ((style.overflowY === 'auto' || style.overflowY === 'scroll')
        && el.scrollHeight > el.clientHeight + 40
        && el.querySelector('time')) {
      try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
      const rect = el.getBoundingClientRect();
      return {
        x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        scrollTop: el.scrollTop, scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      };
    }
  }
  return null;
}
"""

_CLICK_LOAD_MORE_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const els = Array.from(document.querySelectorAll(
    'button, a, div[role="button"], span[role="button"]'
  ));
  for (const el of els) {
    let label = norm(el.getAttribute('aria-label')) + ' ' + norm(el.innerText);
    label += ' ' + Array.from(el.querySelectorAll('title'))
      .map((t) => norm(t.textContent)).join(' ');
    if (/load more comments|more comments|lihat komentar|tampilkan komentar|muat komentar/.test(label)) {
      try {
        el.scrollIntoView({ block: 'center' });
        el.click();
        return true;
      } catch (e) {}
    }
  }
  return false;
}
"""

_CLICK_REPLIES_JS = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const els = Array.from(document.querySelectorAll(
    'button, a, div[role="button"], span[role="button"]'
  ));
  let count = 0;
  for (const el of els) {
    const label = (norm(el.getAttribute('aria-label')) + ' ' + norm(el.innerText)).trim();
    if (/^(view|lihat)\s+(all\s+|semua\s+)?(\d+\s+)?(repl|balasan)/.test(label)
        || /view replies|lihat balasan|tampilkan balasan/.test(label)) {
      try {
        el.click();
        count += 1;
        if (count >= 8) break;
      } catch (e) {}
    }
  }
  return count;
}
"""

# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def default_user_data_dir() -> str:
    """Path profil browser persisten (menyimpan sesi login Instagram)."""
    return str(DEFAULT_PROFILE_DIR)


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _id_str(*values: Any) -> str:
    """Ambil ID dari string maupun angka."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            return str(int(value))
    return ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\r\n?", "\n", str(value)).strip()


def _to_int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return int(value or 0)
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0


def _first_int(*values: Any) -> int:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (ValueError, TypeError):
            try:
                return int(float(value))
            except (ValueError, TypeError):
                continue
    return 0


def _parse_compact_int(value: Any) -> int:
    """Ubah angka gaya Instagram ('703K', '7,937', '1.2M') jadi int."""
    if value is None:
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    text = str(value).strip().upper().replace(",", "").replace(" ", "")
    if not text:
        return 0
    multiplier = 1
    if text.endswith("K"):
        multiplier, text = 1_000, text[:-1]
    elif text.endswith("M"):
        multiplier, text = 1_000_000, text[:-1]
    elif text.endswith("B"):
        multiplier, text = 1_000_000_000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except (ValueError, TypeError):
        return 0


def _to_datetime(*values: Any) -> Optional[datetime]:
    """Konversi epoch (detik/milidetik), ISO string, atau datetime ke GMT+7."""
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, datetime):
            dt = value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_UTC)
            try:
                return dt.astimezone(_JAKARTA)
            except (OverflowError, OSError, ValueError):
                continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            ts = float(value)
            if ts <= 0:
                continue
            if ts > 10_000_000_000:
                ts /= 1000.0
            try:
                return datetime.fromtimestamp(ts, tz=_UTC).astimezone(_JAKARTA)
            except (OverflowError, OSError, ValueError):
                continue
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            if re.fullmatch(r"\d{9,16}", text):
                continue  # ditangani sebagai angka di bawah
            try:
                iso = text.replace("Z", "+00:00")
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_UTC)
                return dt.astimezone(_JAKARTA)
            except (ValueError, TypeError):
                pass
            try:
                ts = float(text)
                if ts > 10_000_000_000:
                    ts /= 1000.0
                return datetime.fromtimestamp(ts, tz=_UTC).astimezone(_JAKARTA)
            except (ValueError, TypeError, OverflowError, OSError):
                continue
    return None


def _datetime_from_media_id(pk: Any) -> Optional[datetime]:
    """Perkirakan waktu post dari ID media Instagram.

    ID media Instagram menyimpan timestamp di bit atas. Rumus yang umum
    dipakai: ``(id >> 23) + 1314220021721`` milidetik. Hasilnya perkiraan,
    bukan tanggal resmi dari API.
    """
    try:
        media_id = int(str(pk).strip())
    except (ValueError, TypeError):
        return None
    if media_id <= 0:
        return None
    try:
        ms = (media_id >> 23) + 1314220021721
        dt = datetime.fromtimestamp(ms / 1000.0, tz=_UTC).astimezone(_JAKARTA)
    except (OverflowError, OSError, ValueError):
        return None
    # Tolak hasil yang tidak masuk akal (Instagram baru ada sejak 2010).
    if dt.year < 2010 or dt.year > datetime.now(_JAKARTA).year + 1:
        return None
    return dt


def _fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %z") if dt else ""


def _fmt_number(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _now_jakarta() -> datetime:
    return datetime.now(tz=_JAKARTA)


# --------------------------------------------------------------------------- #
# Normalisasi input
# --------------------------------------------------------------------------- #


def _host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0]
    except ValueError:
        return ""


def _ensure_scheme(value: str) -> str:
    text = value.strip()
    if text.startswith("//"):
        return "https:" + text
    if "://" not in text:
        return "https://" + text
    return text


def _code_from_url(url: str) -> str:
    match = _POST_CODE_RE.search(url or "")
    return match.group(1) if match else ""


def normalize_post_url(value: str) -> tuple[str, str]:
    """Validasi link post/reel Instagram.

    Returns:
        ``(url_kanonik, kode_post)``. Kode bisa kosong untuk link share yang
        perlu di-resolve browser.

    Raises:
        ValueError: bila input kosong, host bukan Instagram, atau bukan link post.
    """
    text = (value or "").strip()
    if not text:
        raise ValueError(
            "Tempel link post/reel Instagram, mis. "
            "https://www.instagram.com/p/XXXXXXXXXXX/"
        )
    if text.startswith("@"):
        raise ValueError(
            "Itu username, bukan link post. Untuk mode konten, pilih "
            "**Username**; untuk komentar, tempel link post/reel."
        )
    if " " in text or "\n" in text:
        raise ValueError("Link tidak boleh mengandung spasi. Salin ulang link post-nya.")

    if "://" not in text and not text.lower().startswith(("instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am")):
        if _SHORTCODE_RE.fullmatch(text):
            return f"https://www.instagram.com/p/{text}/", text
        raise ValueError(
            "Link tidak dikenali. Contoh yang benar: "
            "https://www.instagram.com/p/XXXXXXXXXXX/ atau "
            "https://www.instagram.com/reel/XXXXXXXXXXX/"
        )

    url = _ensure_scheme(text)
    host = _host_of(url)
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            "Link harus berasal dari instagram.com, mis. "
            "https://www.instagram.com/p/XXXXXXXXXXX/"
        )

    parsed = urlparse(url)
    parts = [part for part in (parsed.path or "").split("/") if part]

    # Link share: biarkan browser mengikuti redirect, kode diambil nanti.
    # Cek ini SEBELUM regex kode, karena path share juga bisa memuat /reel/.
    if parts and parts[0].lower() == "share":
        return url, ""

    code = _code_from_url(url)

    if not code:
        raise ValueError(
            "Link itu bukan link post/reel. Salin link dari tombol **Bagikan → "
            "Salin tautan** pada post, mis. "
            "https://www.instagram.com/p/XXXXXXXXXXX/"
        )

    return f"https://www.instagram.com/p/{code}/", code


def normalize_username(value: str) -> str:
    """Ubah input username / link profil menjadi username Instagram."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Masukkan username Instagram, mis. instagram atau @instagram.")
    if " " in text or "\n" in text:
        raise ValueError("Username tidak boleh mengandung spasi.")

    if "instagram.com" in text.lower() or "instagr.am" in text.lower():
        url = _ensure_scheme(text)
        if _host_of(url) not in _ALLOWED_HOSTS:
            raise ValueError("Link profil harus berasal dari instagram.com.")
        parts = [part for part in (urlparse(url).path or "").split("/") if part]
        if not parts:
            raise ValueError(
                "Link profil tidak memuat username. Contoh: "
                "https://www.instagram.com/instagram/"
            )
        if parts[0].lower() in _RESERVED_USERNAMES:
            raise ValueError(
                "Link itu bukan link profil. Untuk komentar, gunakan mode "
                "**Komentar** dan tempel link post/reel."
            )
        text = parts[0]

    text = text.strip().lstrip("@").strip("/")
    if not _USERNAME_RE.fullmatch(text):
        raise ValueError(
            "Username tidak valid. Gunakan huruf, angka, titik, atau garis bawah "
            "(maks. 30 karakter), mis. instagram."
        )
    if text.lower() in _RESERVED_USERNAMES:
        raise ValueError("Itu bukan username akun Instagram.")
    return text


def normalize_hashtag(value: str) -> str:
    """Ubah input hashtag / link hashtag menjadi nama tag tanpa '#'."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Masukkan hashtag, mis. #ballondor atau ballondor.")
    if "instagram.com" in text.lower():
        url = _ensure_scheme(text)
        if _host_of(url) not in _ALLOWED_HOSTS:
            raise ValueError("Link hashtag harus berasal dari instagram.com.")
        parts = [part for part in (urlparse(url).path or "").split("/") if part]
        if len(parts) >= 2 and parts[0].lower() == "explore" and parts[1].lower() == "tags":
            text = parts[2] if len(parts) > 2 else ""
        elif len(parts) >= 2 and parts[0].lower() == "popular":
            text = parts[1]
        else:
            raise ValueError(
                "Link itu bukan link hashtag. Contoh: "
                "https://www.instagram.com/explore/tags/ballondor/"
            )
    text = text.strip().lstrip("#").strip()
    if not text or re.search(r"[\s/]", text) or len(text) > 100:
        raise ValueError(
            "Hashtag tidak valid. Gunakan satu kata tanpa spasi, mis. #ballondor."
        )
    return text


def normalize_keyword(value: str) -> str:
    """Validasi kata kunci pencarian konten."""
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        raise ValueError("Masukkan kata kunci, mis. ballon d'or atau resep rendang.")
    if len(text) > 200:
        raise ValueError("Kata kunci terlalu panjang (maks. 200 karakter).")
    return text


def build_profile_url(username: str) -> str:
    return f"https://www.instagram.com/{quote(username)}/"


def build_hashtag_url(tag: str) -> str:
    return f"https://www.instagram.com/explore/tags/{quote(tag)}/"


def build_keyword_url(keyword: str) -> str:
    return f"https://www.instagram.com/explore/search/keyword/?q={quote(keyword)}"


# --------------------------------------------------------------------------- #
# Login & status sesi
# --------------------------------------------------------------------------- #


def _has_session_cookie(cookies: list[dict[str, Any]]) -> bool:
    names = {str(c.get("name") or "") for c in cookies if isinstance(c, dict)}
    return bool(names & {"sessionid", "ds_user_id"})


def _logged_in_from_page(page) -> Optional[bool]:
    try:
        return _has_session_cookie(page.context.cookies())
    except Exception:
        return None


def _browser_error_message(exc: Exception) -> str:
    """Pesan error browser yang ramah untuk kasus umum."""
    text = str(exc)
    if "ProcessSingleton" in text or "profile is already in use" in text:
        return (
            "Profil browser Instagram sedang dipakai proses scraping lain. "
            "Tunggu proses itu selesai (atau klik Hentikan), tutup tab/jendela "
            "scraping lain, lalu coba lagi."
        )
    return f"Gagal menjalankan browser: {type(exc).__name__}: {exc}"


def _clear_stale_profile_lock(user_data_dir: str) -> None:
    """Hapus lock profil Chrome yang tertinggal dari proses yang sudah mati.

    Chrome membuat symlink ``SingletonLock`` berisi ``hostname-pid``. Bila PID
    sudah tidak hidup, lock lama bisa membuat browser baru gagal dibuka.
    """
    base = pathlib.Path(user_data_dir)
    lock = base / "SingletonLock"
    if not lock.is_symlink():
        return
    try:
        target = os.readlink(lock)
    except OSError:
        return
    match = re.search(r"-(\d+)$", target or "")
    if not match:
        return
    pid = int(match.group(1))
    try:
        os.kill(pid, 0)
        return  # proses masih hidup; jangan sentuh profilenya
    except ProcessLookupError:
        pass
    except PermissionError:
        return
    except OSError:
        return
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = base / name
        try:
            if path.is_symlink() or path.exists():
                path.unlink()
        except OSError:
            pass


def login(
    user_data_dir: Optional[str] = None,
    real_chrome: bool = True,
    timeout_s: int = 600,
    stop_event=None,
) -> dict[str, Any]:
    """Buka browser tampak agar pengguna login manual ke Instagram.

    Kata sandi tidak dibaca aplikasi; hanya cookie sesi yang tersimpan di
    profil browser untuk dipakai scraping berikutnya.
    """
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    _clear_stale_profile_lock(user_data_dir)
    result: dict[str, Any] = {"ok": False, "message": ""}

    def wait_login(page) -> None:
        for _ in range(max(1, int(timeout_s))):
            if stop_event is not None and stop_event.is_set():
                result["message"] = "Login dihentikan."
                return
            try:
                if _has_session_cookie(page.context.cookies()):
                    result["ok"] = True
                    try:
                        page.wait_for_timeout(800)
                    except Exception:
                        pass
                    return
            except Exception:
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:
                # Jendela browser ditutup pengguna.
                break
        result["message"] = (
            "Waktu login habis / jendela ditutup. Cookie sesi Instagram tidak "
            "terdeteksi."
        )

    try:
        with StealthySession(
            headless=False,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
        ) as session:
            session.fetch(
                "https://www.instagram.com/accounts/login/",
                page_action=wait_login,
                timeout=(timeout_s + 60) * 1000,
            )
    except Exception as exc:  # noqa: BLE001
        result["message"] = _browser_error_message(exc)
    return result


def _profile_cookie_names(user_data_dir: str) -> set[str]:
    """Baca nama cookie dari database profil Chrome tanpa membuka browser.

    Chrome menyimpan cookie di ``Default/Cookies`` atau
    ``Default/Network/Cookies``. Nilai cookie terenkripsi, tetapi nama cookie
    bisa dibaca untuk mendeteksi sesi login tanpa mengunci profil browser.
    """
    import sqlite3

    base = pathlib.Path(user_data_dir)
    candidates = [
        base / "Default" / "Network" / "Cookies",
        base / "Default" / "Cookies",
        base / "Cookies",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        rows = None
        for uri in (f"file:{path}?mode=ro", f"file:{path}?mode=ro&immutable=1"):
            try:
                con = sqlite3.connect(uri, uri=True)
                try:
                    rows = con.execute(
                        "SELECT name, expires_utc, has_expires FROM cookies "
                        "WHERE host_key LIKE '%instagram.com'"
                    ).fetchall()
                finally:
                    con.close()
                break
            except Exception:
                rows = None
                continue
        if rows is None:
            continue
        now_s = time.time()
        names: set[str] = set()
        for name, expires_utc, has_expires in rows:
            if not name:
                continue
            if has_expires and expires_utc:
                # Chrome memakai mikrodetik sejak 1601-01-01 UTC.
                expiry_s = float(expires_utc) / 1_000_000 - 11_644_473_600
                if expiry_s < now_s - 60:
                    continue
            names.add(str(name))
        if names:
            return names
    return set()


def is_logged_in(
    user_data_dir: Optional[str] = None, real_chrome: bool = True
) -> bool:
    """Cek apakah profil punya cookie sesi Instagram.

    Membaca database cookie profil lebih dulu (cepat, tidak mengunci profil).
    Bila database belum ada, fallback membuka browser headless sebentar.
    """
    user_data_dir = user_data_dir or default_user_data_dir()
    cookie_names = _profile_cookie_names(user_data_dir)
    if cookie_names:
        return bool(cookie_names & {"sessionid", "ds_user_id"})

    found = {"ok": False}

    def check(page) -> None:
        found["ok"] = bool(_logged_in_from_page(page))

    try:
        with StealthySession(
            headless=True,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
            retries=1,
            load_dom=False,
        ) as session:
            session.fetch(
                "https://www.instagram.com/",
                page_action=check,
                timeout=_CHECK_TIMEOUT_MS,
                load_dom=False,
                wait=300,
            )
    except Exception:
        return False
    return bool(found["ok"])


# --------------------------------------------------------------------------- #
# Parsing JSON
# --------------------------------------------------------------------------- #


def _iter_json_blobs(html: str) -> Iterator[Any]:
    for match in _SCRIPT_JSON_RE.finditer(html or ""):
        raw = match.group(1).strip()
        if len(raw) < 30:
            continue
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue


def _walk_json(
    root: Any, *, skip_keys: Optional[set[str]] = None
) -> Iterator[dict[str, Any]]:
    """Iterasi semua dict di dalam struktur JSON (iteratif, aman).

    ``skip_keys`` berguna untuk tidak menelusuri isi kunci tertentu, mis.
    ``carousel_media``. Slide carousel punya ``code`` dan ``pk`` sendiri,
    tetapi bukan post terpisah; ia hanya bagian dari satu post carousel.
    """
    stack: list[Any] = [root]
    seen = 0
    while stack and seen < 800_000:
        current = stack.pop()
        seen += 1
        if isinstance(current, dict):
            yield current
            for key, value in current.items():
                if skip_keys and key in skip_keys:
                    continue
                stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)


def _looks_like_comment(data: dict[str, Any]) -> bool:
    typename = str(data.get("__typename") or "").lower()
    if typename == "xigcomment" or typename.endswith("comment"):
        return True
    return (
        "text" in data
        and "user" in data
        and ("created_at" in data or "taken_at" in data)
        and ("comment_like_count" in data or "parent_comment_id" in data)
    )


def _looks_like_media(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or _looks_like_comment(data):
        return False
    code = data.get("code") or data.get("shortcode")
    if not isinstance(code, str) or not code.strip():
        return False
    typename = str(data.get("__typename") or "").lower()
    if "comment" in typename and "media" not in typename:
        return False
    keys = set(data)
    markers = {
        "media_type",
        "product_type",
        "caption",
        "display_uri",
        "display_url",
        "thumbnail_src",
        "is_video",
        "carousel_media",
        "edge_media_to_caption",
        "video_versions",
        "image_versions2",
        "taken_at",
        "taken_at_timestamp",
        "comments_connection",
    }
    if keys & markers:
        return True
    return "media" in typename


def _looks_like_profile(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or _looks_like_comment(data):
        return False
    username = data.get("username")
    if not isinstance(username, str) or not username.strip():
        return False
    keys = set(data)
    markers = {
        "follower_count",
        "edge_followed_by",
        "all_media_count",
        "biography",
        "full_name",
        "edge_owner_to_timeline_media",
        "is_private",
        "profile_pic_url",
    }
    if "profile_pic_url" in keys and len(keys & markers) >= 2:
        return True
    return "follower_count" in keys and "all_media_count" in keys


def _caption_text(data: dict[str, Any]) -> str:
    caption = data.get("caption")
    if isinstance(caption, dict):
        text = caption.get("text")
        if isinstance(text, str) and text.strip():
            return _clean_text(text)
    elif isinstance(caption, str) and caption.strip():
        return _clean_text(caption)

    edge = data.get("edge_media_to_caption")
    if isinstance(edge, dict):
        edges = edge.get("edges")
        if isinstance(edges, list) and edges:
            node = edges[0].get("node") if isinstance(edges[0], dict) else None
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str) and text.strip():
                    return _clean_text(text)
    return ""


def _media_label(data: dict[str, Any]) -> str:
    typename = str(data.get("__typename") or "").lower()
    product = str(data.get("product_type") or "").lower()
    media_type = _to_int(data.get("media_type"))
    if data.get("carousel_media") or media_type == 8 or "sidecar" in typename or "carousel" in typename:
        return "Carousel"
    if product == "clips":
        return "Reel"
    if media_type == 2 or data.get("is_video") or "video" in typename:
        return "Video"
    if media_type == 1 or "image" in typename:
        return "Gambar"
    return "Post"


def _count_from(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            value = value.get("count")
        if value not in (None, ""):
            return _to_int(value)
    return 0


def _image_url(data: dict[str, Any]) -> str:
    """Ambil URL gambar utama; UI baru hanya menyediakan ``image_versions2``."""
    url = _first_str(
        data.get("display_uri"), data.get("display_url"), data.get("thumbnail_src")
    )
    if url:
        return url
    versions = data.get("image_versions2")
    if isinstance(versions, dict):
        candidates = versions.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if isinstance(candidate, dict):
                    candidate_url = candidate.get("url")
                    if isinstance(candidate_url, str) and candidate_url:
                        return candidate_url
    return ""


def _media_row(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    code = _first_str(data.get("code"), data.get("shortcode"))
    if not code:
        return None
    pk = _id_str(data.get("pk"), data.get("id"))
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    if not user and isinstance(data.get("owner"), dict):
        user = data["owner"]
    username = _first_str(user.get("username"))
    full_name = _first_str(user.get("full_name"), username)

    taken = _to_datetime(
        data.get("taken_at"),
        data.get("taken_at_timestamp"),
        data.get("created_at"),
    )
    approx = False
    if taken is None and pk:
        taken = _datetime_from_media_id(pk)
        approx = taken is not None

    likes = _count_from(
        data,
        "like_count",
        "edge_media_preview_like",
        "edge_liked_by",
    )
    comments = _count_from(
        data,
        "comment_count",
        "edge_media_to_comment",
        "edge_media_to_parent_comment",
    )
    views = _count_from(
        data,
        "play_count",
        "video_view_count",
        "video_play_count",
        "view_count",
    )

    url = _first_str(data.get("seo_canonical_url"))
    if not url.startswith("http"):
        url = f"https://www.instagram.com/p/{code}/"

    location = data.get("location")
    location_name = ""
    if isinstance(location, dict):
        location_name = _first_str(location.get("name"), location.get("city"))

    coauthors = data.get("coauthor_producers")
    coauthor_names: list[str] = []
    if isinstance(coauthors, list):
        for item in coauthors:
            if isinstance(item, dict):
                name = _first_str(item.get("username"))
                if name:
                    coauthor_names.append(name)

    product_type = _first_str(data.get("product_type"))
    caption_text = _caption_text(data)
    # Slide anak carousel tidak punya username/caption/metrik sendiri. Jangan
    # jadikan baris tersendiri; post induknya sudah mewakili seluruh carousel.
    if not username and not caption_text and not likes and not comments:
        return None
    return {
        "kode_post": code,
        "waktu": _fmt_dt(taken),
        "tanggal": taken.strftime("%Y-%m-%d") if taken else "",
        "username": username,
        "nama": full_name,
        "caption": caption_text,
        "media": _media_label(data),
        "jenis_post": product_type
        or ("carousel" if _media_label(data) == "Carousel" else "video" if _media_label(data) in ("Video", "Reel") else "image"),
        "suka": likes,
        "komentar": comments,
        "tayangan": views,
        "lokasi": location_name,
        "kolaborator": ", ".join(coauthor_names),
        "url": url,
        "thumbnail": _image_url(data),
        "aksesibilitas": _first_str(data.get("accessibility_caption")),
        "tanggal_perkiraan": approx,
        "_has_comments": "comments_connection" in data,
        "_ts": taken,
    }


def _profile_row(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    username = _first_str(data.get("username"))
    if not username:
        return None
    return {
        "username": username,
        "nama": _first_str(data.get("full_name"), username),
        "bio": _clean_text(data.get("biography")),
        "followers": _count_from(data, "follower_count", "edge_followed_by"),
        "following": _count_from(data, "following_count", "edge_follow"),
        "jumlah_post": _count_from(
            data, "all_media_count", "edge_owner_to_timeline_media"
        ),
        "verified": bool(data.get("is_verified")),
        "private": bool(data.get("is_private")),
        "foto_profil": _first_str(data.get("profile_pic_url")),
        "url": build_profile_url(username),
    }


def _comment_row(data: dict[str, Any]) -> Optional[dict[str, Any]]:
    comment_id = _id_str(data.get("pk"), data.get("id"))
    if not comment_id:
        return None
    user = data.get("user") if isinstance(data.get("user"), dict) else {}
    username = _first_str(user.get("username"))
    created = _to_datetime(data.get("created_at"), data.get("taken_at"))
    if created is None:
        return None

    text = _clean_text(data.get("text"))
    if not text:
        if data.get("giphy_media_info"):
            text = "[GIF]"
        elif data.get("media") or data.get("image_versions2"):
            text = "[Media]"

    parent = _first_str(data.get("parent_comment_id"))
    return {
        "id_komentar": comment_id,
        "waktu": _fmt_dt(created),
        "tanggal": created.strftime("%Y-%m-%d"),
        "username": username,
        "nama": _first_str(user.get("full_name"), username),
        "komentar": text,
        "suka": _first_int(
            data.get("comment_like_count"), data.get("like_count")
        ),
        "balasan": _to_int(data.get("child_comment_count")),
        "jenis": "Balasan" if parent else "Komentar",
        "parent_id": parent,
        "VERIFIED": bool(user.get("is_verified")),
        "profil": f"https://www.instagram.com/{username}/" if username else "",
        "foto_profil": _first_str(user.get("profile_pic_url")),
        "post_url": "",
        "kode_post": "",
        "_ts": created,
    }


def _merge_row(bucket: dict[str, dict[str, Any]], key: str, row: dict[str, Any]) -> None:
    """Gabungkan data dari beberapa respons untuk satu objek yang sama.

    Instagram sering mengirim objek yang sama beberapa kali dengan kelengkapan
    berbeda (mis. satu respons punya komentar, respons lain punya caption dan
    metrik). Nilai yang lebih lengkap tidak boleh ditimpa nilai kosong.
    """
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = row
        return
    for field, value in row.items():
        if field == "_ts":
            if existing.get("_ts") is None or (
                existing.get("tanggal_perkiraan") and value is not None
            ):
                existing["_ts"] = value
            continue
        if field in ("caption", "komentar", "bio"):
            current = str(existing.get(field) or "")
            if len(str(value or "")) > len(current):
                existing[field] = value
            continue
        if field == "_has_comments":
            existing[field] = bool(existing.get(field)) or bool(value)
            continue
        if field == "tanggal_perkiraan":
            # Tanggal pasti lebih baik daripada perkiraan.
            if existing.get(field) and not value:
                existing[field] = False
            continue
        if field in ("media", "jenis_post"):
            generic = {"", "post", "image", "none"}
            current = str(existing.get(field) or "").lower()
            incoming = str(value or "").lower()
            if current in generic and incoming not in generic:
                existing[field] = value
            continue
        if field in ("suka", "komentar", "tayangan", "balasan", "_ts"):
            if (existing.get(field) or 0) == 0 and (value or 0) > 0:
                existing[field] = value
            continue
        if field == "nama":
            current = existing.get(field) or ""
            if not current or current == existing.get("username"):
                if value and value != existing.get("username"):
                    existing[field] = value
            continue
        if existing.get(field) in (None, "", [], {}):
            existing[field] = value


def _ingest_json(
    data: Any,
    state: dict[str, Any],
    *,
    target_code: str = "",
    profile_target: str = "",
) -> tuple[int, int, int]:
    """Ambil komentar, media, dan profil dari satu objek JSON."""
    comments = media = profiles = 0
    skip_keys = {
        "carousel_media",
        "edge_sidecar_to_children",
        "carousel_media_items",
    }
    for node in _walk_json(data, skip_keys=skip_keys):
        if _looks_like_comment(node):
            row = _comment_row(node)
            if row:
                _merge_row(state["comments"], row["id_komentar"], row)
                comments += 1
            continue
        if _looks_like_media(node):
            row = _media_row(node)
            if not row:
                continue
            if target_code and row["kode_post"] != target_code:
                continue
            if (
                profile_target
                and row["username"]
                and row["username"].lower() != profile_target.lower()
            ):
                continue
            _merge_row(state["media"], row["kode_post"], row)
            media += 1
            continue
        if _looks_like_profile(node):
            row = _profile_row(node)
            if row:
                _merge_row(state["profile"], row["username"].lower(), row)
                profiles += 1
    return comments, media, profiles


def _ingest_html(
    html: str,
    state: dict[str, Any],
    *,
    target_code: str = "",
    profile_target: str = "",
) -> tuple[int, int, int]:
    comments = media = profiles = 0
    for blob in _iter_json_blobs(html):
        c, m, p = _ingest_json(
            blob, state, target_code=target_code, profile_target=profile_target
        )
        comments += c
        media += m
        profiles += p
    return comments, media, profiles


def _meta_content(html: str, wanted: str) -> str:
    for tag in _META_TAG_RE.findall(html or ""):
        attrs = {k.lower(): v for k, v in _META_ATTR_RE.findall(tag)}
        name = (attrs.get("property") or attrs.get("name") or "").lower()
        if name == wanted:
            return unescape(attrs.get("content") or "")
    return ""


def _meta_counts(description: str) -> tuple[int, int]:
    likes = 0
    comments = 0
    match = re.search(r"([\d.,]+\s*[KMB]?)\s+likes?", description or "", re.I)
    if match:
        likes = _parse_compact_int(match.group(1))
    match = re.search(r"([\d.,]+\s*[KMB]?)\s+comments?", description or "", re.I)
    if match:
        comments = _parse_compact_int(match.group(1))
    return likes, comments


def _meta_date(description: str) -> Optional[datetime]:
    match = re.search(
        r"\bon\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s*:", description or ""
    )
    if not match:
        return None
    for fmt in ("%B %d, %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(match.group(1), fmt).replace(tzinfo=_UTC)
            return dt.astimezone(_JAKARTA)
        except ValueError:
            continue
    return None


def _meta_username(description: str) -> str:
    match = re.search(
        r"-\s*([A-Za-z0-9._]+)\s+on\s+[A-Z][a-z]+", description or ""
    )
    return match.group(1) if match else ""


def _augment_profile_from_meta(profile: dict[str, Any], html: str) -> None:
    """Lengkapi jumlah follower/following/post dari meta description profil."""
    description = _meta_content(html, "og:description") or _meta_content(
        html, "description"
    )
    if not description:
        return
    patterns = {
        "followers": r"([\d.,]+\s*[KMB]?)\s+followers?",
        "following": r"([\d.,]+\s*[KMB]?)\s+following",
        "jumlah_post": r"([\d.,]+\s*[KMB]?)\s+posts?",
    }
    for field, pattern in patterns.items():
        if profile.get(field):
            continue
        match = re.search(pattern, description, re.I)
        if match:
            profile[field] = _parse_compact_int(match.group(1))
    if not profile.get("nama") or profile.get("nama") == profile.get("username"):
        match = re.search(
            r"photos and videos from (.+?) \(@" + re.escape(str(profile.get("username") or "")) + r"\)",
            description,
            re.I,
        )
        if match:
            profile["nama"] = _clean_text(match.group(1))


def _augment_post_from_meta(post: dict[str, Any], html: str) -> None:
    description = _meta_content(html, "og:description") or _meta_content(
        html, "description"
    )
    if not description:
        return
    likes, comments = _meta_counts(description)
    if not post.get("suka") and likes:
        post["suka"] = likes
    if not post.get("komentar") and comments:
        post["komentar"] = comments
    if not post.get("username"):
        username = _meta_username(description)
        if username:
            post["username"] = username
            post["nama"] = post.get("nama") or username
    if not post.get("waktu"):
        dt = _meta_date(description)
        if dt:
            post["waktu"] = _fmt_dt(dt)
            post["tanggal"] = dt.strftime("%Y-%m-%d")
            post["tanggal_perkiraan"] = True
            post["_ts"] = dt
    if not post.get("caption"):
        match = re.search(r":\s*[\"“](.+?)[\"”]\s*$", description, re.S)
        if match:
            post["caption"] = _clean_text(match.group(1))
    if not post.get("thumbnail"):
        post["thumbnail"] = _meta_content(html, "og:image")


def _post_from_meta(url: str, code: str, html: str) -> dict[str, Any]:
    description = _meta_content(html, "og:description") or _meta_content(
        html, "description"
    )
    likes, comments = _meta_counts(description)
    username = _meta_username(description)
    dt = _meta_date(description)
    post = {
        "kode_post": code,
        "waktu": _fmt_dt(dt),
        "tanggal": dt.strftime("%Y-%m-%d") if dt else "",
        "username": username,
        "nama": username,
        "caption": "",
        "media": "Post",
        "jenis_post": "",
        "suka": likes,
        "komentar": comments,
        "tayangan": 0,
        "lokasi": "",
        "kolaborator": "",
        "url": url,
        "thumbnail": _meta_content(html, "og:image"),
        "aksesibilitas": "",
        "tanggal_perkiraan": bool(dt),
        "_has_comments": True,
        "_ts": dt,
    }
    _augment_post_from_meta(post, html)
    return post


def _select_post(state: dict[str, Any], code: str = "") -> dict[str, Any]:
    media = state["media"]
    if code and code in media:
        return media[code]
    if not media:
        return {}
    # Prioritaskan media yang memang membawa data komentar.
    candidates = sorted(
        media.values(),
        key=lambda row: (bool(row.get("_has_comments")), row.get("komentar") or 0),
        reverse=True,
    )
    return candidates[0]


# --------------------------------------------------------------------------- #
# Jaringan & pengambilan halaman
# --------------------------------------------------------------------------- #


def _new_state(post_url: str = "", post_code: str = "") -> dict[str, Any]:
    return {
        "comments": {},
        "media": {},
        "profile": {},
        "post": {},
        "post_url": post_url,
        "post_code": post_code,
        "last_network_id": 0,
        "seen_network_ids": set(),
        "logged_in": None,
        "login_wall": False,
        "login_required": False,
        "not_found": False,
        "private": False,
        "limited": False,
        "stop_reason": "",
        "rounds": 0,
    }


def _page_html(page) -> str:
    try:
        content = page.content()
        return content if isinstance(content, str) else str(content)
    except Exception:
        return ""


def _page_body(page) -> str:
    try:
        return page.inner_text("body") or ""
    except Exception:
        return ""


def _drain_network(
    session,
    state: dict[str, Any],
    *,
    target_code: str = "",
    profile_target: str = "",
) -> int:
    """Kuras respons GraphQL/API baru dari recorder Scrapling."""
    try:
        responses = session.network.search(limit=None)
    except Exception:
        return 0

    ingested = 0
    seen_ids = state.setdefault("seen_network_ids", set())
    for response in responses:
        network_id = response.meta.get("network_id")
        if not isinstance(network_id, int) or network_id in seen_ids:
            continue
        # Respons tidak dijamin datang berurutan; jangan memakai high-water
        # mark, karena respons lama bisa terlewat saat respons baru muncul
        # lebih dulu. Setiap network_id hanya diproses sekali.
        seen_ids.add(network_id)
        if network_id > state.get("last_network_id", 0):
            state["last_network_id"] = network_id

        body = response.body
        if not body:
            continue
        if isinstance(body, (bytes, bytearray)):
            try:
                text = body.decode("utf-8", "ignore")
            except Exception:
                continue
        else:
            text = str(body)
        if len(text) < 40 or not any(marker in text for marker in _NETWORK_MARKERS):
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        _ingest_json(
            data, state, target_code=target_code, profile_target=profile_target
        )
        ingested += 1
    if ingested and state.get("checkpoint"):
        state["checkpoint"]()
    return ingested


def _is_login_wall(body: str, page_url: str) -> bool:
    lower = (body or "").lower()
    if "/accounts/login" in (page_url or ""):
        return True
    phrases = (
        "log in to like or comment",
        "log in to see",
        "masuk untuk menyukai",
        "masuk untuk melihat",
        "login to continue",
    )
    return any(phrase in lower for phrase in phrases)


def _is_not_found(body: str, page_url: str) -> bool:
    lower = (body or "").lower()
    phrases = (
        "sorry, this page isn't available",
        "halaman ini tidak tersedia",
        "the link you followed may be broken",
        "tautan yang anda ikuti mungkin rusak",
        "page not found",
    )
    return any(phrase in lower for phrase in phrases)


def _sleep_interruptible(seconds: float, stop_event: Optional[Any]) -> None:
    if stop_event is not None:
        stop_event.wait(max(0.0, seconds))
    else:
        time.sleep(max(0.0, seconds))


# --------------------------------------------------------------------------- #
# Scraping komentar
# --------------------------------------------------------------------------- #


def _wheel_comments(page, wait_ms: int) -> bool:
    """Gulir area komentar dengan event wheel asli.

    Instagram memuat halaman komentar berikutnya dari event scroll roda mouse
    pada kontainer komentar. Mengubah ``scrollTop`` lewat JavaScript saja sering
    tidak memicu permintaan berikutnya.
    """
    try:
        box = page.evaluate(_COMMENT_SCROLLER_JS)
    except Exception:
        box = None

    if box and (box.get("width") or 0) > 0 and (box.get("height") or 0) > 0:
        try:
            center_x = float(box["x"]) + float(box["width"]) / 2
            center_y = float(box["y"]) + min(float(box["height"]) / 2, 200.0)
            page.mouse.move(center_x, center_y)
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(max(2000, int(wait_ms)))
            return True
        except Exception:
            pass

    # Fallback: scroll programatik (berguna bila page.mouse tidak tersedia).
    try:
        page.evaluate(_SCROLL_COMMENTS_JS)
    except Exception:
        pass
    try:
        page.wait_for_timeout(max(2000, int(wait_ms)))
    except Exception:
        pass
    return False


def _collect_comments(
    session,
    page,
    state: dict[str, Any],
    *,
    log: Callable[[str], None],
    include_replies: bool,
    wait_ms: int,
    deadline: float,
    max_comments: int,
    stop_event: Optional[Any],
) -> None:
    last_total = -1
    no_change = 0
    rounds = 0
    pacing = time.time()

    while rounds < _MAX_SCROLLS and no_change < _STOP_AFTER:
        if stop_event is not None and stop_event.is_set():
            state["stop_reason"] = "dibatalkan"
            log("⏹️ Proses dibatalkan oleh pengguna.")
            break
        if max_comments and len(state["comments"]) >= max_comments:
            state["stop_reason"] = "target"
            log(f"🛑 Batas maksimum {_fmt_number(max_comments)} komentar tercapai.")
            break
        if time.time() > deadline:
            state["stop_reason"] = "waktu"
            log("⏱️ Batas waktu tercapai; menyimpan komentar yang sudah didapat.")
            break

        _drain_network(session, state, target_code=state["post_code"])

        total = len(state["comments"])
        if total > last_total:
            no_change = 0
        else:
            no_change += 1
        last_total = total

        clicked = False
        try:
            clicked = bool(page.evaluate(_CLICK_LOAD_MORE_JS))
        except Exception:
            clicked = False

        replies = 0
        if include_replies:
            try:
                replies = int(page.evaluate(_CLICK_REPLIES_JS) or 0)
            except Exception:
                replies = 0

        wheeled = _wheel_comments(page, wait_ms)
        if not wheeled:
            _sleep_interruptible(max(2.0, wait_ms / 1000.0), stop_event)

        rounds += 1
        if replies:
            log(f"  💬 {replies} grup balasan dibuka (putaran #{rounds}).")
        state["rounds"] = rounds

        if rounds % 3 == 0:
            _drain_network(session, state, target_code=state["post_code"])
        if rounds % 5 == 0 or clicked or replies:
            log(
                f"  ↕️ putaran #{rounds} · komentar terkumpul: "
                f"{_fmt_number(len(state['comments']))}"
            )

        if time.time() - pacing >= _PAUSE_EVERY_S:
            log(
                f"⏸️ Jeda {_PAUSE_DURATION_S} detik untuk mengurangi risiko "
                "rate-limit…"
            )
            _sleep_interruptible(_PAUSE_DURATION_S, stop_event)
            pacing = time.time()

    if not state["stop_reason"]:
        state["stop_reason"] = "habis" if rounds else "kosong"
    log(f"✅ Selesai memuat komentar ({rounds} putaran).")


def _comment_snapshot(state, include_replies, max_comments):
    comment_parents = {
        _first_str(item.get("id_komentar")): _first_str(item.get("parent_id"))
        for item in state["comments"].values()
    }

    rows: list[dict[str, Any]] = []
    for row in state["comments"].values():
        if not include_replies and row.get("parent_id"):
            continue
        row = dict(row)
        row["post_url"] = state["post_url"]
        row["kode_post"] = state["post_code"]
        comment_id = _first_str(row.get("id_komentar"))
        parent_id = _first_str(row.get("parent_id"))

        # Telusuri ke atas sampai komentar utama (root). Instagram umumnya
        # hanya punya satu level balasan, tetapi loop ini aman untuk thread
        # bertingkat dan mencegah siklus data.
        root_id = parent_id
        seen: set[str] = set()
        while root_id and root_id in comment_parents and root_id not in seen:
            seen.add(root_id)
            next_parent = comment_parents.get(root_id, "")
            if not next_parent:
                break
            root_id = next_parent

        # Untuk balasan: id = id komentar utama + "_" + id balasan.
        # Untuk komentar utama: id = id komentar itu sendiri.
        row["id_komentar_utama"] = root_id or comment_id
        row["id"] = f"{root_id}_{comment_id}" if root_id else comment_id
        rows.append(row)
    rows.sort(key=lambda item: item.get("_ts") or datetime.min.replace(tzinfo=_UTC), reverse=True)
    if max_comments:
        rows = rows[: int(max_comments)]
    for row in rows:
        row.pop("_ts", None)

    return rows


def scrape_comments(
    post_input: str,
    *,
    headless: bool = True,
    real_chrome: bool = True,
    user_data_dir: Optional[str] = None,
    include_replies: bool = True,
    wait_ms: int = 1800,
    max_minutes: int = 10,
    max_comments: int = 0,
    stop_event: Optional[Any] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    checkpoint_cb=None,
) -> dict[str, Any]:
    """Scrape komentar dari satu post/reel Instagram.

    Mengembalikan dict berisi ``rows``, ``post``, ``error``, ``message``,
    ``limited``, dan statistik proses. Kegagalan wajar tidak dilempar sebagai
    exception.
    """
    started = time.time()
    log = log_cb or (lambda _msg: None)
    result: dict[str, Any] = {
        "mode": "comments",
        "rows": [],
        "post": {},
        "profile": {},
        "query": {},
        "logged_in": None,
        "limited": False,
        "error": None,
        "message": "",
        "stop_reason": "",
        "rounds": 0,
        "started_at": datetime.now(_UTC).isoformat(),
        "finished_at": "",
        "duration_s": 0.0,
    }

    try:
        post_url, code = normalize_post_url(post_input)
    except ValueError as exc:
        result["error"] = "invalid_url"
        result["message"] = str(exc)
        result["finished_at"] = datetime.now(_UTC).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        return result

    max_minutes_value = int(max_minutes or 0)
    if max_minutes_value <= 0:
        max_minutes_value = _MAX_RUNTIME_MIN
        log(f"ℹ️ Tanpa batas waktu khusus; memakai batas keras {_MAX_RUNTIME_MIN} menit.")
    deadline = started + max_minutes_value * 60

    state = _new_state(post_url=post_url, post_code=code)
    if checkpoint_cb:
        state["checkpoint"] = lambda: checkpoint_cb(_comment_snapshot(state, include_replies, max_comments))
    _clear_stale_profile_lock(user_data_dir or default_user_data_dir())
    log(f"🔗 Post: {post_url}")

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir or default_user_data_dir(),
            locale="en-US",
            record_requests=True,
            max_recorded_requests=60000,
        ) as session:

            def action(page) -> None:
                state["logged_in"] = _logged_in_from_page(page)
                if state["logged_in"] is False:
                    log(
                        "⚠️ Belum login ke Instagram. Komentar yang tampil untuk "
                        "pengunjung biasanya dibatasi (±10 komentar)."
                    )
                elif state["logged_in"]:
                    log("🔐 Sesi Instagram aktif.")

                final_url = getattr(page, "url", "") or post_url
                resolved_code = _code_from_url(final_url) or code
                if resolved_code:
                    state["post_code"] = resolved_code
                    state["post_url"] = f"https://www.instagram.com/p/{resolved_code}/"

                html = _page_html(page)
                c, m, _ = _ingest_html(
                    html, state, target_code=state["post_code"], profile_target=""
                )
                post = _select_post(state, state["post_code"])
                if not post:
                    post = _post_from_meta(state["post_url"], state["post_code"], html)
                _augment_post_from_meta(post, html)
                state["post"] = post

                if c or m:
                    log(
                        f"🧩 Data awal dari HTML: {_fmt_number(len(state['comments']))} "
                        f"komentar · post @{post.get('username') or '—'}"
                    )

                try:
                    page.wait_for_selector("article, main, time", timeout=25000)
                except Exception:
                    log("⚠️ Halaman post lambat dimuat / tidak menampilkan komentar.")

                body = _page_body(page)
                if _is_not_found(body, final_url):
                    state["not_found"] = True
                    log("❌ Post tidak ditemukan atau sudah dihapus.")
                    return
                if _is_login_wall(body, final_url) and state["logged_in"] is False:
                    state["login_wall"] = True
                    log("🚧 Instagram menampilkan ajakan login untuk melihat komentar lain.")

                if state["post"]:
                    total = int(state["post"].get("komentar") or 0)
                    if total:
                        log(
                            f"💬 Post mencantumkan {_fmt_number(total)} komentar; "
                            "memuat komentar yang tersedia…"
                        )
                    else:
                        log("💬 Memuat komentar…")
                if checkpoint_cb:
                    state["checkpoint"]()
                _collect_comments(
                    session,
                    page,
                    state,
                    log=log,
                    include_replies=include_replies,
                    wait_ms=wait_ms,
                    deadline=deadline,
                    max_comments=max_comments,
                    stop_event=stop_event,
                )

            session.fetch(post_url, page_action=action, timeout=_FETCH_TIMEOUT_MS)
            _drain_network(session, state, target_code=state["post_code"])
    except Exception as exc:  # noqa: BLE001
        result["error"] = "browser_error"
        result["message"] = _browser_error_message(exc)
        result["finished_at"] = datetime.now(_UTC).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        log(f"❌ {result['message']}")
        return result

    # ---- Rapikan hasil ---------------------------------------------------- #
    post = state["post"] or _post_from_meta(state["post_url"], state["post_code"], "")
    post = dict(post)
    post.setdefault("kode_post", state["post_code"])
    post.setdefault("url", state["post_url"])

    rows = _comment_snapshot(state, include_replies, max_comments)

    post.pop("_ts", None)
    post.pop("_has_comments", None)
    post["komentar_terkumpul"] = len(rows)
    result["post"] = post
    result["rows"] = rows
    result["logged_in"] = state["logged_in"]
    result["stop_reason"] = state["stop_reason"]
    result["rounds"] = state["rounds"]

    total_comments = int(post.get("komentar") or 0)
    logged_out = state["logged_in"] is False

    if state["not_found"]:
        result["error"] = "not_found"
        result["message"] = (
            "Post tidak ditemukan atau sudah dihapus. Periksa kembali link-nya. "
            "Post dari akun privat juga tidak bisa dibaca tanpa izin."
        )
    elif not rows:
        if logged_out or state["login_wall"]:
            result["error"] = "login_required"
            result["message"] = (
                "Instagram tidak menampilkan komentar untuk pengunjung. Login "
                "lewat sidebar dengan akun Anda sendiri, lalu ulangi scraping."
            )
        elif total_comments == 0:
            result["error"] = "no_comments"
            result["message"] = "Post ini belum memiliki komentar atau komentarnya dinonaktifkan."
        else:
            result["error"] = "no_comments"
            result["message"] = (
                "Tidak ada komentar yang berhasil dibaca. Coba lagi, atau login "
                "untuk membuka komentar yang dibatasi Instagram."
            )
    else:
        result["limited"] = logged_out or state["login_wall"]
        if result["limited"]:
            result["message"] = (
                "Komentar yang terbaca dibatasi karena Anda belum login. "
                "Instagram biasanya hanya menampilkan ±10 komentar untuk pengunjung. "
                "Login lewat sidebar untuk membuka lebih banyak."
            )
        elif total_comments and len(rows) < total_comments:
            result["message"] = (
                f"Terkumpul {_fmt_number(len(rows))} dari "
                f"{_fmt_number(total_comments)} komentar yang tercantum di post. "
                "Sisanya mungkin memerlukan pemuatan tambahan, dibatasi Instagram, "
                "atau tidak tersedia untuk akun Anda."
            )
        if state["stop_reason"] == "dibatalkan":
            result["message"] = (
                "Proses dihentikan pengguna. Hasil sementara tetap disimpan."
            )
        elif state["stop_reason"] == "waktu":
            result["message"] = (
                "Batas waktu tercapai. Hasil sementara tetap disimpan; ulangi "
                "dengan batas waktu lebih besar bila perlu."
            )
        elif state["stop_reason"] == "target":
            result["message"] = (
                f"Batas maksimum {_fmt_number(max_comments)} komentar tercapai. "
                "Hasil yang ditampilkan sesuai batas tersebut."
            )

    result["finished_at"] = datetime.now(_UTC).isoformat()
    result["duration_s"] = round(time.time() - started, 2)
    log(
        f"🏁 Selesai · {_fmt_number(len(rows))} komentar dari "
        f"{len({r['username'] for r in rows if r['username']})} akun."
    )
    return result


# --------------------------------------------------------------------------- #
# Scraping konten
# --------------------------------------------------------------------------- #


def _collect_content(
    session,
    page,
    state: dict[str, Any],
    *,
    log: Callable[[str], None],
    wait_ms: int,
    deadline: float,
    max_posts: int,
    profile_target: str,
    stop_event: Optional[Any],
) -> None:
    last_total = -1
    no_change = 0
    rounds = 0
    pacing = time.time()

    while rounds < _MAX_SCROLLS and no_change < _STOP_AFTER:
        if stop_event is not None and stop_event.is_set():
            state["stop_reason"] = "dibatalkan"
            log("⏹️ Proses dibatalkan oleh pengguna.")
            break
        if max_posts and len(state["media"]) >= max_posts:
            state["stop_reason"] = "target"
            log(f"🛑 Batas maksimum {_fmt_number(max_posts)} post tercapai.")
            break
        if time.time() > deadline:
            state["stop_reason"] = "waktu"
            log("⏱️ Batas waktu tercapai; menyimpan post yang sudah didapat.")
            break

        _drain_network(session, state, profile_target=profile_target)

        total = len(state["media"])
        if total > last_total:
            no_change = 0
        else:
            no_change += 1
        last_total = total

        stats: dict[str, Any] = {}
        try:
            stats = page.evaluate(_SCROLL_CONTENT_JS) or {}
        except Exception:
            stats = {}
        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            _sleep_interruptible(wait_ms / 1000.0, stop_event)

        rounds += 1
        state["rounds"] = rounds
        if rounds % 3 == 0:
            _drain_network(session, state, profile_target=profile_target)
        if rounds % 5 == 0:
            log(
                f"  ↕️ gulir #{rounds} · link terlihat: {stats.get('links', '—')} "
                f"· post terkumpul: {_fmt_number(len(state['media']))}"
            )

        if time.time() - pacing >= _PAUSE_EVERY_S:
            log(
                f"⏸️ Jeda {_PAUSE_DURATION_S} detik untuk mengurangi risiko "
                "rate-limit…"
            )
            _sleep_interruptible(_PAUSE_DURATION_S, stop_event)
            pacing = time.time()

    if not state["stop_reason"]:
        state["stop_reason"] = "habis" if rounds else "kosong"
    log(f"✅ Selesai menggulir ({rounds} putaran).")


def _filter_content_rows(
    rows: list[dict[str, Any]],
    start_date: Optional[date],
    end_date: Optional[date],
) -> list[dict[str, Any]]:
    if not start_date and not end_date:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        ts = row.get("_ts")
        if ts is None:
            continue
        day = ts.date()
        if start_date and day < start_date:
            continue
        if end_date and day > end_date:
            continue
        kept.append(row)
    return kept


def scrape_content(
    query: str,
    *,
    input_type: str = "username",
    headless: bool = True,
    real_chrome: bool = True,
    user_data_dir: Optional[str] = None,
    wait_ms: int = 1800,
    max_minutes: int = 10,
    max_posts: int = 0,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    stop_event: Optional[Any] = None,
    log_cb: Optional[Callable[[str], None]] = None,
    checkpoint_cb=None,
) -> dict[str, Any]:
    """Scrape daftar post/konten Instagram dari username, kata kunci, atau tag.

    ``input_type``: ``"username"``, ``"keyword"``, atau ``"hashtag"``.
    """
    started = time.time()
    log = log_cb or (lambda _msg: None)
    result: dict[str, Any] = {
        "mode": "content",
        "rows": [],
        "post": {},
        "profile": {},
        "query": {},
        "logged_in": None,
        "limited": False,
        "total_available": 0,
        "error": None,
        "message": "",
        "stop_reason": "",
        "rounds": 0,
        "started_at": datetime.now(_UTC).isoformat(),
        "finished_at": "",
        "duration_s": 0.0,
    }

    profile_target = ""
    normalized_type = input_type
    try:
        if input_type == "username":
            target = normalize_username(query)
            profile_target = target
            url = build_profile_url(target)
            label = f"@{target}"
            log(f"👤 Profil: @{target}")
        elif input_type == "hashtag":
            target = normalize_hashtag(query)
            url = build_hashtag_url(target)
            label = f"#{target}"
            log(f"#️⃣ Hashtag: #{target}")
        else:
            keyword = normalize_keyword(query)
            if keyword.startswith("#"):
                normalized_type = "hashtag"
                target = normalize_hashtag(keyword)
                url = build_hashtag_url(target)
                label = f"#{target}"
                log(f"#️⃣ Hashtag: #{target}")
            else:
                target = keyword
                url = build_keyword_url(keyword)
                label = f'"{keyword}"'
                log(f"🔎 Kata kunci: {keyword}")
    except ValueError as exc:
        result["error"] = "invalid_input"
        result["message"] = str(exc)
        result["finished_at"] = datetime.now(_UTC).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        return result

    if start_date and end_date and start_date > end_date:
        result["error"] = "invalid_input"
        result["message"] = 'Tanggal "Dari" tidak boleh lebih besar dari "Sampai".'
        result["finished_at"] = datetime.now(_UTC).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        return result

    result["query"] = {
        "input_type": normalized_type,
        "target": target,
        "label": label,
        "url": url,
    }
    log(f"🌐 URL: {url}")
    if normalized_type == "keyword":
        log(
            "🔐 Pencarian kata kunci memerlukan login. Username dan hashtag "
            "bisa dibuka tanpa login."
        )
        log("🔎 Memeriksa sesi login terlebih dahulu…")
        if not is_logged_in(
            user_data_dir=user_data_dir or default_user_data_dir(),
            real_chrome=real_chrome,
        ):
            result["logged_in"] = False
            result["error"] = "login_required"
            result["message"] = (
                "Pencarian **kata kunci** di Instagram memerlukan login. Klik "
                "**Login** di sidebar, masuk dengan akun Anda, lalu ulangi. "
                "Sebagai alternatif tanpa login, gunakan mode **Hashtag** dengan "
                "satu kata kunci."
            )
            result["finished_at"] = datetime.now(_UTC).isoformat()
            result["duration_s"] = round(time.time() - started, 2)
            log("❌ Belum login. Proses dihentikan sebelum membuka pencarian.")
            return result
        result["logged_in"] = True
        log("🔐 Sesi login terdeteksi; melanjutkan pencarian kata kunci.")
    else:
        log(
            "ℹ️ Username/hashtag bisa dibuka tanpa login, tetapi jumlahnya "
            "dibatasi Instagram."
        )

    max_minutes_value = int(max_minutes or 0)
    if max_minutes_value <= 0:
        max_minutes_value = _MAX_RUNTIME_MIN
        log(f"ℹ️ Tanpa batas waktu khusus; memakai batas keras {_MAX_RUNTIME_MIN} menit.")
    deadline = started + max_minutes_value * 60

    state = _new_state()
    state["profile_target"] = profile_target

    def save_content():
        rows = _filter_content_rows(list(state["media"].values()), start_date, end_date)
        rows.sort(key=lambda item: item.get("_ts") or datetime.min.replace(tzinfo=_UTC), reverse=True)
        if max_posts:
            rows = rows[:max_posts]
        checkpoint_cb([{k: v for k, v in row.items() if not k.startswith("_")} for row in rows])

    if checkpoint_cb:
        state["checkpoint"] = save_content
    _clear_stale_profile_lock(user_data_dir or default_user_data_dir())

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir or default_user_data_dir(),
            locale="en-US",
            record_requests=True,
            max_recorded_requests=60000,
        ) as session:

            def action(page) -> None:
                state["logged_in"] = _logged_in_from_page(page)
                if state["logged_in"] is False:
                    log(
                        "⚠️ Belum login ke Instagram. Daftar post untuk pengunjung "
                        "biasanya dibatasi (±12 post)."
                    )
                elif state["logged_in"]:
                    log("🔐 Sesi Instagram aktif.")

                final_url = getattr(page, "url", "") or url
                body = _page_body(page)

                if normalized_type == "keyword" and "/accounts/login" in final_url:
                    state["login_required"] = True
                    log("🚧 Pencarian kata kunci dialihkan ke halaman login.")
                    return
                if "/accounts/login" in final_url:
                    state["login_required"] = True
                    log("🚧 Instagram mengalihkan ke halaman login.")
                    return
                if _is_not_found(body, final_url):
                    state["not_found"] = True
                    log("❌ Profil/halaman tidak ditemukan.")
                    return
                if (
                    "this account is private" in body.lower()
                    or "akun ini privat" in body.lower()
                ):
                    state["private"] = True

                html = _page_html(page)
                c, m, p = _ingest_html(
                    html, state, target_code="", profile_target=profile_target
                )
                if p or m:
                    log(
                        f"🧩 Data awal dari HTML: {_fmt_number(len(state['media']))} post"
                        + (f" · profil @{next(iter(state['profile']), '—')}" if state["profile"] else "")
                    )

                try:
                    page.wait_for_selector("main, article, header", timeout=25000)
                except Exception:
                    log("⚠️ Halaman lambat dimuat / tidak menampilkan daftar post.")

                body = _page_body(page)
                if _is_login_wall(body, final_url) and state["logged_in"] is False:
                    log("🚧 Halaman menampilkan ajakan login; hasil mungkin dibatasi.")

                profile = state["profile"].get(profile_target.lower()) if profile_target else None
                if profile:
                    _augment_profile_from_meta(profile, html)
                    log(
                        f"👤 {profile.get('nama') or profile_target} · "
                        f"{_fmt_number(profile.get('followers') or 0)} follower · "
                        f"{_fmt_number(profile.get('jumlah_post') or 0)} post"
                    )

                if checkpoint_cb:
                    state["checkpoint"]()
                _collect_content(
                    session,
                    page,
                    state,
                    log=log,
                    wait_ms=wait_ms,
                    deadline=deadline,
                    max_posts=max_posts,
                    profile_target=profile_target,
                    stop_event=stop_event,
                )

            session.fetch(url, page_action=action, timeout=_FETCH_TIMEOUT_MS)
            _drain_network(session, state, profile_target=profile_target)
    except Exception as exc:  # noqa: BLE001
        result["error"] = "browser_error"
        result["message"] = _browser_error_message(exc)
        result["finished_at"] = datetime.now(_UTC).isoformat()
        result["duration_s"] = round(time.time() - started, 2)
        log(f"❌ {result['message']}")
        return result

    # ---- Rapikan hasil ---------------------------------------------------- #
    rows = list(state["media"].values())
    rows = _filter_content_rows(rows, start_date, end_date)
    rows.sort(
        key=lambda item: item.get("_ts") or datetime.min.replace(tzinfo=_UTC),
        reverse=True,
    )
    if max_posts:
        rows = rows[: int(max_posts)]
    for row in rows:
        row.pop("_ts", None)
        row.pop("_has_comments", None)

    # Bila seluruh baris tidak punya metrik tertentu, simpan sebagai null
    # (bukan 0) agar ekspor tidak menyesatkan. Ini umum terjadi untuk
    # tayangan saat Instagram tidak memberikannya pada feed profil.
    if rows:
        for metric in ("suka", "komentar", "tayangan"):
            if all(not (row.get(metric) or 0) for row in rows):
                for row in rows:
                    row[metric] = None

    profile = {}
    if profile_target:
        profile = state["profile"].get(profile_target.lower()) or {
            "username": profile_target,
            "nama": profile_target,
            "bio": "",
            "followers": 0,
            "following": 0,
            "jumlah_post": 0,
            "verified": False,
            "private": False,
            "foto_profil": "",
            "url": build_profile_url(profile_target),
        }
    elif rows:
        # Untuk hashtag/kata kunci, tampilkan akun paling sering muncul.
        counts: dict[str, int] = {}
        for row in rows:
            if row.get("username"):
                counts[row["username"]] = counts.get(row["username"], 0) + 1
        profile = {
            "username": "",
            "nama": "",
            "bio": "",
            "followers": 0,
            "following": 0,
            "jumlah_post": 0,
            "verified": False,
            "private": False,
            "foto_profil": "",
            "url": "",
            "akun_teratas": sorted(counts, key=counts.get, reverse=True)[:5],
        }

    if profile_target and profile.get("nama"):
        for row in rows:
            if (row.get("username") or "").lower() == profile_target.lower():
                if not row.get("nama") or row["nama"] == row["username"]:
                    row["nama"] = profile["nama"]

    result["rows"] = rows
    result["profile"] = profile
    result["total_available"] = int(profile.get("jumlah_post") or 0)
    result["logged_in"] = state["logged_in"]
    result["stop_reason"] = state["stop_reason"]
    result["rounds"] = state["rounds"]

    logged_out = state["logged_in"] is False
    if state["login_required"]:
        result["error"] = "login_required"
        if normalized_type == "keyword":
            result["message"] = (
                "Pencarian **kata kunci** di Instagram memerlukan login. Klik "
                "**Login** di sidebar, masuk dengan akun Anda, lalu ulangi. "
                "Sebagai alternatif tanpa login, gunakan mode **Hashtag** dengan "
                "satu kata kunci."
            )
        else:
            result["message"] = (
                "Instagram mengalihkan ke halaman login. Login lewat sidebar "
                "dengan akun Anda, lalu ulangi scraping."
            )
    elif state["not_found"]:
        result["error"] = "not_found"
        if normalized_type == "username":
            result["message"] = (
                f"Akun **@{profile_target}** tidak ditemukan. Periksa username-nya "
                "(tanpa spasi, tanpa simbol @)."
            )
        else:
            result["message"] = "Halaman tidak ditemukan. Periksa kembali input Anda."
    elif not rows:
        if state["private"] or profile.get("private"):
            result["error"] = "private"
            result["message"] = (
                f"Akun **@{profile_target}** bersifat privat. Post hanya bisa "
                "dibaca jika akun Anda menjadi pengikut yang disetujui."
            )
        elif normalized_type == "keyword":
            result["error"] = "no_posts"
            result["message"] = (
                "Tidak ada post yang terbaca untuk kata kunci ini. Coba kata kunci "
                "lain, gunakan hashtag, atau pastikan Anda sudah login."
            )
        elif normalized_type == "hashtag":
            result["error"] = "no_posts"
            result["message"] = (
                f"Tidak ada post publik yang terbaca untuk **#{target}**. "
                "Coba hashtag lain atau login untuk membuka lebih banyak."
            )
        else:
            result["error"] = "no_posts"
            result["message"] = (
                f"Tidak ada post publik yang terbaca dari **@{profile_target}**. "
                "Akun mungkin privat, kosong, atau membatasi tampilan untuk pengunjung."
            )
    else:
        result["limited"] = logged_out
        if logged_out:
            if normalized_type == "username":
                extra = "Instagram biasanya hanya menampilkan ±12 post terbaru untuk pengunjung."
            else:
                extra = (
                    "Instagram membatasi jumlah post yang bisa dimuat tanpa login; "
                    "hasil yang terbaca adalah yang tersedia di halaman populer."
                )
            result["message"] = (
                "Daftar post dibatasi karena Anda belum login. " + extra +
                " Login lewat sidebar untuk membuka lebih banyak."
            )
        elif result["total_available"] and len(rows) < result["total_available"]:
            result["message"] = (
                f"Terkumpul {_fmt_number(len(rows))} dari "
                f"{_fmt_number(result['total_available'])} post yang tercantum. "
                "Sisanya mungkin memerlukan pemuatan tambahan atau dibatasi Instagram."
            )
        if state["stop_reason"] == "dibatalkan":
            result["message"] = (
                "Proses dihentikan pengguna. Hasil sementara tetap disimpan."
            )
        elif state["stop_reason"] == "waktu":
            result["message"] = (
                "Batas waktu tercapai. Hasil sementara tetap disimpan; ulangi "
                "dengan batas waktu lebih besar bila perlu."
            )
        elif state["stop_reason"] == "target":
            result["message"] = (
                f"Batas maksimum {_fmt_number(max_posts)} post tercapai. "
                "Hasil yang ditampilkan sesuai batas tersebut."
            )

    result["finished_at"] = datetime.now(_UTC).isoformat()
    result["duration_s"] = round(time.time() - started, 2)
    log(
        f"🏁 Selesai · {_fmt_number(len(rows))} post dari "
        f"{len({r['username'] for r in rows if r['username']})} akun."
    )
    return result


# --------------------------------------------------------------------------- #
# Ekspor
# --------------------------------------------------------------------------- #

COMMENT_CSV_FIELDS = [
    "id",
    "id_komentar_utama",
    "id_komentar",
    "waktu",
    "tanggal",
    "username",
    "nama",
    "komentar",
    "suka",
    "balasan",
    "jenis",
    "parent_id",
    "VERIFIED",
    "profil",
    "foto_profil",
    "post_url",
    "kode_post",
]

CONTENT_CSV_FIELDS = [
    "kode_post",
    "waktu",
    "tanggal",
    "username",
    "nama",
    "caption",
    "media",
    "jenis_post",
    "suka",
    "komentar",
    "tayangan",
    "lokasi",
    "kolaborator",
    "url",
    "thumbnail",
    "aksesibilitas",
    "tanggal_perkiraan",
]


def rows_to_csv(rows: list[dict[str, Any]], mode: str = "comments") -> str:
    """Serialisasi baris menjadi CSV (UTF-8 dengan BOM untuk Excel)."""
    import csv
    import io

    fields = CONTENT_CSV_FIELDS if mode == "content" else COMMENT_CSV_FIELDS
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fields})
    return buffer.getvalue()


def rows_to_json(
    rows: list[dict[str, Any]], result: Optional[dict[str, Any]] = None
) -> str:
    """Serialisasi hasil + metadata proses menjadi JSON."""
    result = result or {}
    mode = result.get("mode") or "comments"
    payload = {
        "mode": mode,
        "query": result.get("query") or {},
        "post": result.get("post") or {},
        "profile": result.get("profile") or {},
        "parameter": {
            "login": result.get("logged_in"),
            "dibatasi": result.get("limited"),
            "stop_reason": result.get("stop_reason"),
            "durasi_detik": result.get("duration_s"),
        },
        "diambil_pada": datetime.now(_UTC).isoformat(),
        "total": len(rows),
    }
    payload["komentar" if mode == "comments" else "konten"] = rows
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
