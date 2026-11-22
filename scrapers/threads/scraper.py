"""Scraping post Threads (threads.com) menggunakan Scrapling.

Modul ini membungkus ``StealthySession`` dari Scrapling untuk membuka halaman
pencarian Threads, menggulir hasil, dan menangkap respons GraphQL internal
Threads. Data post lalu diparsing dari respons tersebut (bukan dari teks DOM),
sehingga metrik seperti suka/balasan/repost tetap akurat.

Fitur:
  - Pencarian berdasarkan kata kunci.
  - Filter rentang tanggal (inklusif) pada sisi klien, memakai zona GMT+7.
  - Urutan "Terbaru" (recent) atau "Terpopuler" (top).
  - Sesi login Threads persisten (opsional, tetapi sangat disarankan).

Catatan penting:
  Threads membatasi hasil pencarian untuk pengunjung yang belum login
  (sekitar 24 post pertama). Login dengan akun sendiri membuka hasil yang
  jauh lebih banyak. Aplikasi ini TIDAK melewati login, CAPTCHA, atau
  kontrol akses apa pun.
"""

from __future__ import annotations

import json
import pathlib
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession

# --------------------------------------------------------------------------- #
# Konstanta & path
# --------------------------------------------------------------------------- #

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = _PROJECT_DIR / "sessions" / "threads_profile"

_JAKARTA = ZoneInfo("Asia/Jakarta")
_UTC = timezone.utc

# Skrip menggulir semua kontainer yang bisa di-scroll (Threads memakai div,
# bukan window, sebagai kontainer daftar post).
_SCROLL_JS = r"""
() => {
  const candidates = Array.from(document.querySelectorAll('div, main, section'));
  const scrollers = candidates.filter((el) => {
    const s = getComputedStyle(el);
    return (s.overflowY === 'auto' || s.overflowY === 'scroll')
      && el.scrollHeight > el.clientHeight + 100;
  });
  let moved = false;
  for (const el of scrollers) {
    const before = el.scrollTop;
    el.scrollTop = el.scrollHeight;
    if (el.scrollTop !== before) moved = true;
  }
  window.scrollTo(0, document.body.scrollHeight);
  return { scrollers: scrollers.length, moved };
}
"""

_COUNT_JS = r"""
() => ({
  containers: document.querySelectorAll('[data-pressable-container]').length,
  times: document.querySelectorAll('time').length,
})
"""

_SCRIPT_JSON_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S | re.I
)

_MAX_SCROLLS = 600        # pengaman: jangan menggulir selamanya
_STOP_AFTER = 6           # berhenti jika N gulir berturut-turut tanpa post baru

_PAUSE_EVERY_S = 120      # jeda berkala setiap 2 menit proses scraping
_PAUSE_DURATION_S = 30    # durasi jeda (detik)


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def default_user_data_dir() -> str:
    """Path profil browser yang persisten (menyimpan sesi login Threads)."""
    return str(DEFAULT_PROFILE_DIR)


def build_search_url(keyword: str, sort: str = "recent") -> str:
    """Bangun URL pencarian Threads.

    ``filter=recent`` meminta urutan terbaru di UI Threads. Jika Threads
    mengabaikan parameter tersebut (mis. saat belum login), hasil tetap
    difilter ulang berdasarkan tanggal di sisi klien.
    """
    params = f"q={quote(keyword.strip())}&serp_type=default"
    if sort == "recent":
        params += "&filter=recent"
    return f"https://www.threads.com/search?{params}"


def _has_session_cookie(cookies: list[dict[str, Any]]) -> bool:
    """True jika cookie sesi login Instagram/Threads ditemukan."""
    names = {str(c.get("name") or "") for c in cookies if isinstance(c, dict)}
    return bool(names & {"sessionid", "ds_user_id"})


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


def _to_datetime(raw: Any) -> Optional[datetime]:
    """Ubah ``taken_at`` Threads (epoch detik) menjadi datetime GMT+7."""
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (ValueError, TypeError):
        return None
    if ts <= 0:
        return None
    # Sebagian respons memakai milidetik.
    if ts > 10_000_000_000:
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=_UTC).astimezone(_JAKARTA)
    except (OverflowError, OSError, ValueError):
        return None


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# --------------------------------------------------------------------------- #
# Parsing post Threads
# --------------------------------------------------------------------------- #


def _iter_thread_posts(node: Any):
    """Yield setiap objek ``post`` di dalam ``thread_items`` (rekursif).

    Struktur GraphQL Threads kira-kira:
      data.searchResults.edges[].node.thread.thread_items[].post
    """
    if isinstance(node, dict):
        items = node.get("thread_items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                post = item.get("post")
                if isinstance(post, dict):
                    yield post
            # Jangan turun lagi ke dalam thread_items agar tidak dobel.
        for key, value in node.items():
            if key == "thread_items":
                continue
            yield from _iter_thread_posts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_thread_posts(value)


def _iter_json_blobs(html: str):
    """Yield objek JSON dari tag ``<script type="application/json">``."""
    for match in _SCRIPT_JSON_RE.finditer(html or ""):
        raw = match.group(1).strip()
        if len(raw) < 40:
            continue
        try:
            yield json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue


def _posts_from_html(html: str) -> list[dict[str, Any]]:
    """Ambil post dari SSR/embedded JSON di HTML halaman Threads."""
    posts: list[dict[str, Any]] = []
    for blob in _iter_json_blobs(html):
        posts.extend(_iter_thread_posts(blob))
    return posts


def _post_text(post: dict[str, Any]) -> str:
    caption = post.get("caption")
    if isinstance(caption, dict):
        text = caption.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    elif isinstance(caption, str) and caption.strip():
        return caption.strip()

    # Cadangan: rangkai text_fragments.
    info = post.get("text_post_app_info") or {}
    if isinstance(info, dict):
        fragments = (info.get("text_fragments") or {}).get("fragments")
        if isinstance(fragments, list):
            parts = [
                str(f.get("plaintext") or "")
                for f in fragments
                if isinstance(f, dict) and f.get("plaintext")
            ]
            if parts:
                return "".join(parts).strip()
    return ""


def _media_label(post: dict[str, Any]) -> str:
    media_type = _to_int(post.get("media_type"))
    if post.get("carousel_media"):
        return "Carousel"
    if media_type == 2 or post.get("video_versions"):
        return "Video"
    if media_type == 1 or post.get("image_versions2"):
        return "Gambar"
    return "Teks"


def _reply_to(post: dict[str, Any]) -> str:
    info = post.get("text_post_app_info") or {}
    if not isinstance(info, dict) or not info.get("is_reply"):
        return ""
    author = info.get("reply_to_author")
    if isinstance(author, dict):
        username = _first_str(author.get("username"))
        if username:
            return username
        user = author.get("user")
        if isinstance(user, dict):
            return _first_str(user.get("username"))
    if isinstance(author, str):
        return author.strip()
    return ""


def _post_url(post: dict[str, Any], username: str, code: str) -> str:
    for candidate in (post.get("canonical_url"), post.get("permalink")):
        if isinstance(candidate, str) and candidate.strip():
            value = candidate.strip()
            if value.startswith("http"):
                return value
            if value.startswith("/"):
                return f"https://www.threads.com{value}"
            if value:
                return f"https://www.threads.com/{value.lstrip('/')}"
    if username and code:
        return f"https://www.threads.com/@{username}/post/{code}"
    return ""


def _flatten_post(post: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Ubah satu objek post GraphQL menjadi baris tabel + timestamp internal."""
    if not isinstance(post, dict):
        return None

    code = _first_str(post.get("code"))
    post_id = _first_str(post.get("id"), str(post.get("pk") or ""))
    if not code and not post_id:
        return None

    user = post.get("user") if isinstance(post.get("user"), dict) else {}
    username = _first_str(user.get("username"))
    name = _first_str(user.get("full_name"), username)
    ts = _to_datetime(post.get("taken_at"))
    if ts is None:
        return None

    info = post.get("text_post_app_info") or {}
    if not isinstance(info, dict):
        info = {}

    row = {
        "waktu": ts.strftime("%Y-%m-%d %H:%M:%S %z"),
        "nama": name,
        "username": username,
        "VERIFIED": bool(user.get("is_verified")),
        "text": _post_text(post),
        "MEDIA": _media_label(post),
        "LIKE_COUNT": _to_int(post.get("like_count")),
        "REPLY_COUNT": _to_int(info.get("direct_reply_count")),
        "REPOST_COUNT": _to_int(info.get("repost_count") or info.get("reshare_count")),
        "QUOTE_COUNT": _to_int(info.get("quote_count")),
        "IN_REPLY_TO": _reply_to(post),
        "USER_BIO": _first_str(user.get("biography")),
        "POST URL": _post_url(post, username, code),
        "POST_ID": post_id,
        "_ts": ts,
    }
    return row


def _flatten_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten + deduplikasi berdasarkan ID/kode post."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        row = _flatten_post(post)
        if row is None:
            continue
        key = row["POST_ID"] or row["POST URL"] or f"{row['username']}|{row['text'][:80]}"
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _filter_by_date(
    rows: list[dict[str, Any]],
    start_date: Optional[date],
    end_date: Optional[date],
) -> list[dict[str, Any]]:
    """Filter inklusif berdasarkan tanggal lokal GMT+7, lalu urutkan terbaru."""
    kept: list[dict[str, Any]] = []
    for row in rows:
        ts = row.get("_ts")
        if ts is None:
            continue
        d = ts.date()
        if start_date and d < start_date:
            continue
        if end_date and d > end_date:
            continue
        kept.append(row)
    kept.sort(key=lambda r: r["_ts"], reverse=True)
    # Salin tanpa kolom internal agar fungsi ini aman dipanggil berulang.
    return [{k: v for k, v in row.items() if k != "_ts"} for row in kept]


def _dedupe_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplikasi objek post mentah (sebelum flatten)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        key = _first_str(post.get("code"), post.get("id"), str(post.get("pk") or ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(post)
    return out


# --------------------------------------------------------------------------- #
# Login & status
# --------------------------------------------------------------------------- #


def login(
    user_data_dir: Optional[str] = None,
    real_chrome: bool = True,
    timeout_s: int = 600,
    stop_event=None,
) -> dict[str, Any]:
    """Buka browser tampak agar pengguna login ke Threads; profil disimpan."""
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"ok": False, "message": ""}

    def wait_login(page) -> None:
        for _ in range(max(1, timeout_s)):
            if stop_event is not None and stop_event.is_set():
                result["message"] = "Login dihentikan."
                return
            try:
                if _has_session_cookie(page.context.cookies()):
                    result["ok"] = True
                    return
            except Exception:
                pass
            try:
                page.wait_for_timeout(1000)
            except Exception:
                # Jendela browser ditutup pengguna.
                break
        result["message"] = (
            "Waktu login habis / jendela ditutup (cookie sesi Threads/Instagram "
            "tidak terdeteksi)."
        )

    try:
        with StealthySession(
            headless=False,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
        ) as session:
            session.fetch(
                "https://www.threads.com/login",
                page_action=wait_login,
                timeout=(timeout_s + 30) * 1000,
            )
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"Gagal membuka browser login: {exc}"
    return result


def is_logged_in(user_data_dir: Optional[str] = None, real_chrome: bool = True) -> bool:
    """Cek cepat (headless) apakah profil sudah punya sesi login Threads."""
    user_data_dir = user_data_dir or default_user_data_dir()
    found = {"ok": False}

    def check(page) -> None:
        try:
            found["ok"] = _has_session_cookie(page.context.cookies())
        except Exception:
            found["ok"] = False

    try:
        with StealthySession(
            headless=True,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
        ) as session:
            session.fetch("https://www.threads.com/", page_action=check, timeout=45000)
    except Exception:
        return False
    return found["ok"]


# --------------------------------------------------------------------------- #
# Scraping
# --------------------------------------------------------------------------- #


def _new_capture() -> dict[str, Any]:
    return {"last_id": 0, "ids": set(), "posts": []}


def _drain_network(session, capture: dict[str, Any], add_posts: Callable[[list[dict]], None]) -> None:
    """Kuras respons GraphQL baru dari recorder Scrapling."""
    try:
        responses = session.network.search(
            url_pattern=r"graphql", after_id=capture["last_id"], limit=None
        )
    except Exception:
        return

    for response in responses:
        network_id = response.meta.get("network_id")
        if not isinstance(network_id, int) or network_id <= capture["last_id"]:
            continue
        capture["last_id"] = network_id
        if network_id in capture["ids"]:
            continue
        capture["ids"].add(network_id)

        body = response.body
        if not body:
            continue
        try:
            text = body.decode("utf-8", "ignore") if isinstance(body, (bytes, bytearray)) else str(body)
        except Exception:
            continue
        if "thread_items" not in text:
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Body terpotong / bukan JSON utuh.
            continue
        add_posts(list(_iter_thread_posts(data)))


def _scrape_session(
    *,
    session,
    url: str,
    state: dict[str, Any],
    log: Callable[[str], None],
    wait_ms: int,
    max_minutes: int,
    max_posts: int,
    stop_event=None,
    on_posts=None,
) -> list[dict[str, Any]]:
    """Buka URL, gulir hasil, dan kumpulkan post mentah dari SSR + GraphQL."""
    capture = _new_capture()
    started = time.time()
    pacing = {"last": time.time()}

    def add_posts(posts: list[dict[str, Any]]) -> None:
        if not posts:
            return
        known = {_first_str(p.get("code"), p.get("id"), str(p.get("pk") or "")) for p in capture["posts"]}
        before = len(known)
        for post in posts:
            if not isinstance(post, dict):
                continue
            key = _first_str(post.get("code"), post.get("id"), str(post.get("pk") or ""))
            if key and key not in known:
                known.add(key)
                capture["posts"].append(post)
        if on_posts and len(known) > before:
            on_posts(capture["posts"])

    def action(page) -> None:
        try:
            state["logged_in"] = _has_session_cookie(page.context.cookies())
        except Exception:
            state["logged_in"] = None

        if state["logged_in"] is False:
            log(
                "⚠️ Belum login ke Threads. Hasil dibatasi ±24 post pertama "
                "(Threads menampilkan ajakan login setelahnya)."
            )
        elif state["logged_in"]:
            log("🔐 Sesi Threads aktif.")

        # --- 1) Ambil post yang sudah tertanam di HTML (SSR) ---
        try:
            html = page.content()
            add_posts(_posts_from_html(html))
            log(f"🧩 Post dari HTML awal: {len(capture['posts'])}")
        except Exception:
            pass

        # --- 2) Tunggu daftar post muncul ---
        try:
            page.wait_for_selector(
                '[data-pressable-container], time, a[href*="/post/"]', timeout=25000
            )
        except Exception:
            log(
                "⚠️ Tidak ada post yang terlihat. Kemungkinan hasil kosong, "
                "sedang login, atau Threads memblokir sementara."
            )
            _drain_network(session, capture, add_posts)
            return

        try:
            body_text = page.inner_text("body")
        except Exception:
            body_text = ""
        if "log in for more" in body_text.lower() or "login for more" in body_text.lower():
            state["login_wall"] = True
            if state["logged_in"] is False:
                log("🚧 Threads menampilkan batas login setelah beberapa post.")

        _drain_network(session, capture, add_posts)
        try:
            if state.get("sort") == "recent" and "filter=recent" not in (page.url or ""):
                log(
                    "ℹ️ Threads mengabaikan parameter urutan 'Terbaru'; hasil "
                    "diurutkan ulang berdasarkan waktu setelah difilter."
                )
        except Exception:
            pass
        log(f"📜 Halaman hasil termuat. Menggulir… (post terkumpul: {len(capture['posts'])})")

        last_total = -1
        no_change = 0
        scrolls = 0
        while scrolls < _MAX_SCROLLS and no_change < _STOP_AFTER:
            if stop_event is not None and stop_event.is_set():
                break
            if max_posts and len(capture["posts"]) >= max_posts:
                log(f"🛑 Batas maksimum {max_posts} post tercapai.")
                break
            if max_minutes and (time.time() - started) > max_minutes * 60:
                log(f"⏱️ Batas waktu {max_minutes} menit tercapai.")
                break

            _drain_network(session, capture, add_posts)
            total = len(capture["posts"])
            if total > last_total:
                no_change = 0
            else:
                no_change += 1
            last_total = total

            try:
                stats = page.evaluate(_COUNT_JS) or {}
            except Exception:
                stats = {}
            try:
                page.evaluate(_SCROLL_JS)
            except Exception:
                pass

            page.wait_for_timeout(wait_ms)
            scrolls += 1

            # Jeda berkala untuk mengurangi risiko rate-limit.
            if time.time() - pacing["last"] >= _PAUSE_EVERY_S:
                log(
                    f"⏸️ Jeda {_PAUSE_DURATION_S} detik "
                    f"(setiap {_PAUSE_EVERY_S // 60} menit) untuk mengurangi risiko rate-limit…"
                )
                try:
                    if stop_event is not None:
                        stop_event.wait(_PAUSE_DURATION_S)
                    else:
                        page.wait_for_timeout(_PAUSE_DURATION_S * 1000)
                except Exception:
                    time.sleep(_PAUSE_DURATION_S)
                pacing["last"] = time.time()

            if scrolls % 3 == 0:
                _drain_network(session, capture, add_posts)
            if scrolls % 10 == 0:
                log(
                    f"  ↕️ gulir #{scrolls} · post terlihat: {stats.get('times', 0)} "
                    f"· terkumpul: {len(capture['posts'])}"
                )

        log(f"✅ Selesai menggulir ({scrolls} gulir).")
        page.wait_for_timeout(1500)
        _drain_network(session, capture, add_posts)

    session.fetch(url, page_action=action, timeout=600000)
    _drain_network(session, capture, add_posts)
    return capture["posts"]


def scrape_posts(
    keyword: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    sort: str = "recent",
    headless: bool = True,
    real_chrome: bool = True,
    user_data_dir: Optional[str] = None,
    wait_ms: int = 1800,
    max_minutes: int = 10,
    max_posts: int = 0,
    log_cb: Optional[Callable[[str], None]] = None,
    stop_event=None,
    checkpoint_cb=None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Scrape post Threads sesuai kata kunci dan rentang tanggal.

    Mengembalikan ``(rows, error)``.

    ``error`` bisa berupa:
      - ``"not_logged_in"``       : tidak ada sesi dan tidak ada post.
      - ``"logged_out_limited"``  : ada hasil, tetapi dibatasi karena belum login.
      - pesan teks lain           : selesai dengan catatan / gagal.
      - ``None``                  : sukses penuh.
    """
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    keyword = (keyword or "").strip()
    if not keyword:
        return [], "Masukkan kata kunci pencarian."
    if start_date and end_date and start_date > end_date:
        return [], 'Tanggal "Dari" tidak boleh lebih besar dari "Sampai".'

    sort = "recent" if sort not in ("recent", "top") else sort
    url = build_search_url(keyword, sort)
    log(f"🔎 Kata kunci: {keyword}")
    log(
        f"🗓️ Rentang: {start_date or '-'} s.d. {end_date or '-'} (GMT+7, inklusif)"
    )
    log(f"↕️ Urutan: {'Terbaru' if sort == 'recent' else 'Terpopuler'}")
    if max_posts:
        log(f"🔢 Batas post: {max_posts}")

    state: dict[str, Any] = {
        "logged_in": None,
        "login_wall": False,
        "sort": sort,
    }

    def checkpoint(posts):
        checkpoint_cb(_filter_by_date(_flatten_posts(posts), start_date, end_date))

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
            record_requests=True,
            max_recorded_requests=60000,
        ) as session:
            raw_posts = _scrape_session(
                session=session,
                url=url,
                state=state,
                log=log,
                wait_ms=wait_ms,
                max_minutes=max_minutes,
                max_posts=max_posts,
                stop_event=stop_event,
                on_posts=checkpoint if checkpoint_cb else None,
            )
    except Exception as exc:  # noqa: BLE001
        return [], f"Gagal menjalankan browser: {exc}"

    raw_posts = _dedupe_posts(raw_posts)
    log(f"📡 Post unik terkumpul: {len(raw_posts)}")

    rows = _filter_by_date(_flatten_posts(raw_posts), start_date, end_date)
    log(f"🗓️ Setelah filter rentang tanggal: {len(rows)} post")
    if rows:
        log(
            f"🏁 Selesai · {len(rows)} post dari "
            f"{len({r['username'] for r in rows if r['username']})} akun."
        )

    logged_out = state.get("logged_in") is False or bool(state.get("login_wall"))

    if not rows:
        if logged_out:
            return [], "not_logged_in"
        if raw_posts:
            return [], "Tidak ada post dalam rentang tanggal yang dipilih."
        return [], "Tidak ada post publik yang cocok ditemukan."

    if logged_out:
        return rows, "logged_out_limited"
    return rows, None
