"""Scraping tweet X (Twitter) menggunakan Scrapling.

Modul ini membungkus StealthySession dari Scrapling untuk menangkap respons API
GraphQL X, lalu mem-parsing data lengkap tiap tweet (metrik, bahasa, reply-to,
lokasi user, status verifikasi, dsb).

Mode yang didukung:
  - ``search``        : cari tweet berdasarkan kata kunci/hashtag.
  - ``profile``       : ambil tweet dari profil user tertentu.
  - ``tweet_reply``   : ambil satu tweet beserta semua balasannya.
  - ``search_reply``  : cari tweet per kata kunci, lalu ambil balasan tiap tweet.
"""

from __future__ import annotations

import pathlib
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession

# --------------------------------------------------------------------------- #
# Konstanta & path
# --------------------------------------------------------------------------- #

_PROJECT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_PROFILE_DIR = _PROJECT_DIR / "sessions" / "x_profile"

_JAKARTA = ZoneInfo("Asia/Jakarta")

_SCROLL_JS = r"""
() => {
  window.scrollBy(0, 3200);
  const main = document.querySelector('main');
  if (main) main.scrollTop += 3200;
}
"""

_MAX_SCROLLS = 600        # pengaman: jangan menggulir selamanya
_STOP_AFTER = 6           # berhenti jika N gulir berturut-turut tanpa tweet baru

_PAUSE_EVERY_S = 120      # jeda berkala setiap 2 menit proses scraping
_PAUSE_DURATION_S = 30    # durasi jeda (detik)


# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def default_user_data_dir() -> str:
    """Path profil browser yang persisten (menyimpan sesi login X)."""
    return str(DEFAULT_PROFILE_DIR)


def build_query(keyword: str, start_date: Optional[date] = None, end_date: Optional[date] = None) -> str:
    """Gabungkan kata kunci dengan operator pencarian tanggal X."""
    parts = [keyword.strip()]
    if start_date:
        parts.append(f"since:{start_date.isoformat()}")
    if end_date:
        # Operator `until:` bersifat eksklusif, jadi tambah 1 hari agar tanggal akhir ikut.
        parts.append(f"until:{(end_date + timedelta(days=1)).isoformat()}")
    return " ".join(parts)


def build_search_url(query: str, sort: str = "latest") -> str:
    f_param = "live" if sort == "latest" else "top"
    return f"https://x.com/search?q={quote(query)}&src=typed_query&f={f_param}"


def _clean_username(value: str) -> str:
    """Terima 'user', '@user', atau URL profil; kembalikan username polos."""
    value = (value or "").strip()
    m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)", value)
    if m:
        return m.group(1)
    return value.lstrip("@").strip("/")


def _parse_tweet_url(value: str) -> Optional[tuple[str, str]]:
    """Ambil (username, status_id) dari URL tweet; None jika tidak valid."""
    m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)", value or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0


def _to_jakarta(raw: Any) -> Optional[datetime]:
    """Ubah timestamp X ('Wed Nov 12 12:34:56 +0000 2026') menjadi datetime GMT+7."""
    if not raw:
        return None
    s = str(raw).strip()
    try:
        dt = datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_JAKARTA)


# --------------------------------------------------------------------------- #
# Parsing respons GraphQL
# --------------------------------------------------------------------------- #


def _unwrap(result: Any) -> Any:
    """Lepas lapisan pembungkus (TweetWithVisibilityResults, dsb) ke objek tweet nyata."""
    while isinstance(result, dict):
        if result.get("legacy"):
            return result
        if isinstance(result.get("tweet"), dict):
            result = result["tweet"]
            continue
        if isinstance(result.get("result"), dict):
            result = result["result"]
            continue
        return result
    return result


def _iter_tweet_results(data: Any):
    """Yield tiap ``tweet_results.result`` dari respons GraphQL X."""
    def rec(node: Any):
        if isinstance(node, dict):
            if isinstance(node.get("tweet_results"), dict):
                result = node["tweet_results"].get("result")
                if isinstance(result, dict):
                    yield result
                # Jangan turun ke dalam tweet (hindari quoted/retweet ganda).
                for key, value in node.items():
                    if key == "tweet_results":
                        continue
                    yield from rec(value)
            else:
                for value in node.values():
                    yield from rec(value)
        elif isinstance(node, list):
            for value in node:
                yield from rec(value)

    yield from rec(data)


def _tweet_payload(result: Any) -> Optional[dict[str, Any]]:
    """Kembalikan objek tweet kanonik; untuk retweet, pakai tweet aslinya."""
    payload = _unwrap(result)
    if not isinstance(payload, dict) or not payload.get("legacy"):
        return None
    rtsr = payload.get("legacy", {}).get("retweeted_status_result")
    if isinstance(rtsr, dict):
        inner = _unwrap(rtsr.get("result"))
        if isinstance(inner, dict) and inner.get("legacy"):
            return inner
    return payload


def _first_str(*values: Any) -> str:
    """Ambil string non-kosong pertama dari beberapa kandidat."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _user_location(user: dict[str, Any]) -> str:
    """Ambil lokasi profil; di skema baru nilainya bersarang di ``location.location``."""
    raw = user.get("location") if isinstance(user, dict) else None
    if isinstance(raw, dict):
        raw = raw.get("location")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    legacy = user.get("legacy") if isinstance(user, dict) else None
    if isinstance(legacy, dict):
        loc = legacy.get("location")
        if isinstance(loc, str) and loc.strip():
            return loc.strip()
    return ""


def _flatten_tweet(payload: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[datetime]]:
    """Ekstrak satu tweet menjadi dict kolom + timestamp GMT+7 (untuk filter/sort)."""
    legacy = payload.get("legacy") or {}
    core = payload.get("core") or {}
    user_result = (core.get("user_results") or {}).get("result") if isinstance(core, dict) else None
    user = _unwrap(user_result) if isinstance(user_result, dict) else {}
    if not isinstance(user, dict):
        user = {}
    user_core = user.get("core") or {}
    user_legacy = user.get("legacy") or {}
    verification = user.get("verification") or {}
    views = payload.get("views") or {}

    rest_id = str(payload.get("rest_id") or legacy.get("id_str") or "").strip()
    # Skema baru X: nama & username ada di user["core"]; skema lama di user["legacy"].
    name = _first_str(user_core.get("name"), user_legacy.get("name"), user.get("name"))
    screen_name = _first_str(
        user_core.get("screen_name"), user_legacy.get("screen_name"), user.get("screen_name")
    )
    location = _user_location(user)
    verified = bool(
        user.get("is_blue_verified")
        or (verification.get("verified") if isinstance(verification, dict) else False)
        or user.get("verified_type")
        or user_legacy.get("verified")
    )
    full_text = (legacy.get("full_text") or "").strip()
    lang = (legacy.get("lang") or "").strip()

    in_reply_to = (legacy.get("in_reply_to_screen_name") or "").strip()
    in_reply_to_status = str(legacy.get("in_reply_to_status_id_str") or "").strip()

    ts = _to_jakarta(legacy.get("created_at"))

    row = {
        "waktu": ts.strftime("%Y-%m-%d %H:%M:%S %z") if ts else "",
        "nama": name,
        "username": screen_name,
        "VERIFIED": verified,
        "text": full_text,
        "bahasa": lang,
        "IN_REPLY_TO": in_reply_to,
        "IN_REPLY_TO_URL": (
            f"https://x.com/{in_reply_to}/status/{in_reply_to_status}"
            if in_reply_to and in_reply_to_status
            else ""
        ),
        "VIEW_COUNT": _to_int(views.get("count")),
        "QUOTE_COUNT": _to_int(legacy.get("quote_count")),
        "REPLY_COUNT": _to_int(legacy.get("reply_count")),
        "RETWEET_COUNT": _to_int(legacy.get("retweet_count")),
        "FAVORITE_COUNT": _to_int(legacy.get("favorite_count")),
        "USER_LOCATION": location,
        "TWEET URL": (
            f"https://x.com/{screen_name}/status/{rest_id}"
            if screen_name and rest_id
            else ""
        ),
    }
    return row, ts


def _parse_responses(
    json_list: list[Any],
    start_date: Optional[date],
    end_date: Optional[date],
) -> list[dict[str, Any]]:
    """Gabungkan semua respons API menjadi daftar tweet unik + filter tanggal (GMT+7)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for data in json_list:
        for result in _iter_tweet_results(data):
            payload = _tweet_payload(result)
            if payload is None:
                continue
            row, ts = _flatten_tweet(payload)
            if row is None:
                continue
            key = row["TWEET URL"] or f"{row['username']}|{row['text'][:80]}"
            if not key or key in seen:
                continue
            seen.add(key)
            if ts is not None:
                d = ts.date()
                if start_date and d < start_date:
                    continue
                if end_date and d > end_date:
                    continue
            rows.append(row)

    rows.sort(key=lambda r: r["waktu"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Login & status
# --------------------------------------------------------------------------- #


def login(
    user_data_dir: Optional[str] = None,
    real_chrome: bool = True,
    timeout_s: int = 600,
    stop_event=None,
) -> dict[str, Any]:
    """Buka browser tampak agar pengguna login ke X; profil tersimpan otomatis."""
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {"ok": False, "message": ""}

    def wait_login(page) -> None:
        for _ in range(timeout_s):
            if stop_event is not None and stop_event.is_set():
                result["message"] = "Login dihentikan."
                return
            try:
                cookies = page.context.cookies()
            except Exception:
                page.wait_for_timeout(1000)
                continue
            if any(c.get("name") == "auth_token" for c in cookies):
                result["ok"] = True
                return
            page.wait_for_timeout(1000)
        result["message"] = "Waktu login habis (tidak ada sesi X terdeteksi)."

    with StealthySession(
        headless=False,
        real_chrome=real_chrome,
        user_data_dir=user_data_dir,
        locale="en-US",
    ) as session:
        session.fetch(
            "https://x.com/login",
            page_action=wait_login,
            timeout=(timeout_s + 30) * 1000,
        )
    return result


def is_logged_in(user_data_dir: Optional[str] = None, real_chrome: bool = True) -> bool:
    """Cek cepat (headless) apakah profil sudah punya sesi login X."""
    user_data_dir = user_data_dir or default_user_data_dir()
    found = {"ok": False}

    def check(page) -> None:
        try:
            cookies = page.context.cookies()
            found["ok"] = any(c.get("name") == "auth_token" for c in cookies)
        except Exception:
            found["ok"] = False

    try:
        with StealthySession(
            headless=True,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
        ) as session:
            session.fetch("https://x.com/home", page_action=check, timeout=45000)
    except Exception:
        return False
    return found["ok"]


# --------------------------------------------------------------------------- #
# Scraping per mode
# --------------------------------------------------------------------------- #


def _login_ok(page) -> bool:
    try:
        cookies = page.context.cookies()
    except Exception:
        return False
    return any(c.get("name") == "auth_token" for c in cookies)


def _new_capture(pattern: str) -> dict[str, Any]:
    return {"pattern": pattern, "rows": [], "last_id": 0, "ids": set()}


def _drain(session, capture: dict[str, Any]) -> None:
    """Kuras respons API baru dari recorder (selagi masih di memori) ke capture."""
    try:
        responses = session.network.search(
            url_pattern=capture["pattern"], after_id=capture["last_id"], limit=None
        )
    except Exception:
        return
    before = len(capture["rows"])
    for r in responses:
        nid = r.meta.get("network_id")
        if not isinstance(nid, int) or nid <= capture["last_id"]:
            continue
        capture["last_id"] = nid
        if nid in capture["ids"]:
            continue
        try:
            data = r.json()
        except Exception:
            # Body terpotong (mis. > 1 MiB) atau bukan JSON.
            continue
        capture["ids"].add(nid)
        capture["rows"].append(data)
    if capture.get("checkpoint") and len(capture["rows"]) > before:
        capture["checkpoint"](capture["rows"])


def _fetch_and_scroll(
    session,
    url: str,
    *,
    wait_ms: int,
    max_minutes: int,
    log: Callable[[str], None],
    label: str,
    state: dict[str, Any],
    capture: dict[str, Any],
    pacing: dict[str, Any],
) -> None:
    """Buka URL, tunggu tweet, gulir sampai habis, sambil menguras respons API."""
    started = time.time()
    stop_event = state.get("stop_event")
    capture["checkpoint"] = state.get("checkpoint")
    if stop_event is not None and stop_event.is_set():
        return

    def action(page) -> None:
        if not _login_ok(page):
            log("❌ Belum login ke X (cookie auth_token tidak ditemukan).")
            state["not_logged_in"] = True
            return
        try:
            page.wait_for_selector('article[data-testid="tweet"]', timeout=25000)
        except Exception:
            log(f"⚠️ Tidak ada tweet muncul di {label} (kosong / rate-limit / dibatasi).")
            _drain(session, capture)
            return

        log(f"📜 {label}: termuat. Menggulir…")
        last_count = 0
        no_change = 0
        scrolls = 0
        while scrolls < _MAX_SCROLLS and no_change < _STOP_AFTER:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                count = page.evaluate(
                    "document.querySelectorAll('article[data-testid=\"tweet\"]').length"
                ) or 0
            except Exception:
                count = 0
            if count == last_count:
                no_change += 1
            else:
                no_change = 0
            last_count = count
            try:
                page.evaluate(_SCROLL_JS)
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
            scrolls += 1

            # Jeda berkala untuk mengurangi risiko rate-limit X.
            if time.time() - pacing["last"] >= _PAUSE_EVERY_S:
                interval = (
                    f"{_PAUSE_EVERY_S // 60} menit"
                    if _PAUSE_EVERY_S >= 60
                    else f"{_PAUSE_EVERY_S} detik"
                )
                log(
                    f"⏸️ Jeda {_PAUSE_DURATION_S} detik "
                    f"(setiap {interval}) untuk mengurangi risiko rate-limit…"
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
                _drain(session, capture)
            if max_minutes and (time.time() - started) > max_minutes * 60:
                log(f"⏱️ Batas waktu {max_minutes} menit tercapai.")
                break
            if scrolls % 10 == 0:
                log(f"  ↕️ {label}: gulir #{scrolls} · tweet: {count}")
        log(f"✅ {label}: selesai menggulir ({scrolls} gulir).")
        page.wait_for_timeout(2000)
        _drain(session, capture)

    session.fetch(url, page_action=action, timeout=600000)
    _drain(session, capture)


def scrape_tweets(
    mode: str = "search",
    keyword: str = "",
    username: str = "",
    tweet_url: str = "",
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    sort: str = "latest",
    headless: bool = True,
    real_chrome: bool = True,
    user_data_dir: Optional[str] = None,
    wait_ms: int = 1500,
    max_minutes: int = 0,
    max_reply_expand: int = 10,
    log_cb: Optional[Callable[[str], None]] = None,
    stop_event=None,
    checkpoint_cb=None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Scrape tweet sesuai mode. Mengembalikan ``(rows, error)``.

    ``error`` berisi pesan teks, atau ``"not_logged_in"`` bila belum login.
    """
    user_data_dir = user_data_dir or default_user_data_dir()
    pathlib.Path(user_data_dir).mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def checkpoint(data):
        bounds = (None, None) if mode == "tweet_reply" else (start_date, end_date)
        checkpoint_cb(_parse_responses(data, *bounds))

    state: dict[str, Any] = {
        "not_logged_in": False, "stop_event": stop_event,
        "checkpoint": checkpoint if checkpoint_cb else None,
    }

    # --- Validasi & siapkan URL sesuai mode ---
    if mode == "search":
        if not keyword.strip():
            return [], "Masukkan kata kunci/hashtag pencarian."
        url = build_search_url(build_query(keyword, start_date, end_date), sort)
        log(f"🔎 Mode: Search · kata kunci: {keyword}")
        if start_date or end_date:
            log(f"🗓️ Rentang: {start_date or '-'} s.d. {end_date or '-'}")
    elif mode == "profile":
        uname = _clean_username(username)
        if not uname:
            return [], "Masukkan username profil (contoh: elonmusk)."
        url = f"https://x.com/{uname}"
        log(f"👤 Mode: Profile · @{uname}")
        if start_date or end_date:
            log(f"🗓️ Filter tanggal (sisi klien): {start_date or '-'} s.d. {end_date or '-'}")
    elif mode == "tweet_reply":
        parsed = _parse_tweet_url(tweet_url)
        if not parsed:
            return [], "URL tweet tidak valid. Contoh: https://x.com/user/status/12345"
        url = f"https://x.com/{parsed[0]}/status/{parsed[1]}"
        log(f"🔗 Mode: Tweet + Reply · {url}")
    elif mode == "search_reply":
        if not keyword.strip():
            return [], "Masukkan kata kunci/hashtag pencarian."
        url = build_search_url(build_query(keyword, start_date, end_date), sort)
        log(f"🔎 Mode: Search + Reply · kata kunci: {keyword}")
        if start_date or end_date:
            log(f"🗓️ Rentang: {start_date or '-'} s.d. {end_date or '-'}")
    else:
        return [], f"Mode tidak dikenal: {mode}"

    rows: list[dict[str, Any]] = []
    pacing: dict[str, Any] = {"last": time.time()}

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            user_data_dir=user_data_dir,
            locale="en-US",
            record_requests=True,
            max_recorded_requests=60000,
        ) as session:
            if mode == "search":
                capture = _new_capture(r"SearchTimeline")
                _fetch_and_scroll(
                    session, url, wait_ms=wait_ms, max_minutes=max_minutes,
                    log=log, label="hasil pencarian", state=state, capture=capture, pacing=pacing,
                )
                if state["not_logged_in"]:
                    return [], "not_logged_in"
                log(f"📡 Respons API: {len(capture['rows'])}")
                rows = _parse_responses(capture["rows"], start_date, end_date)

            elif mode == "profile":
                capture = _new_capture(r"(UserOriginalsTimeline|UserTweets)")
                _fetch_and_scroll(
                    session, url, wait_ms=wait_ms, max_minutes=max_minutes,
                    log=log, label="profil", state=state, capture=capture, pacing=pacing,
                )
                if state["not_logged_in"]:
                    return [], "not_logged_in"
                log(f"📡 Respons profil: {len(capture['rows'])}")
                rows = _parse_responses(capture["rows"], start_date, end_date)

            elif mode == "tweet_reply":
                capture = _new_capture(r"TweetDetail")
                _fetch_and_scroll(
                    session, url, wait_ms=wait_ms, max_minutes=max_minutes,
                    log=log, label="tweet & balasan", state=state, capture=capture, pacing=pacing,
                )
                if state["not_logged_in"]:
                    return [], "not_logged_in"
                log(f"📡 Respons detail: {len(capture['rows'])}")
                rows = _parse_responses(capture["rows"], None, None)

            else:  # search_reply
                capture = _new_capture(r"(SearchTimeline|TweetDetail)")
                _fetch_and_scroll(
                    session, url, wait_ms=wait_ms, max_minutes=max_minutes,
                    log=log, label="hasil pencarian", state=state, capture=capture, pacing=pacing,
                )
                if state["not_logged_in"]:
                    return [], "not_logged_in"

                search_rows = _parse_responses(capture["rows"], start_date, end_date)
                log(f"📡 Respons pencarian: {len(capture['rows'])} · tweet: {len(search_rows)}")

                tweet_urls: list[str] = []
                seen_urls: set[str] = set()
                for r in search_rows:
                    u = r.get("TWEET URL") or ""
                    if u and u not in seen_urls:
                        seen_urls.add(u)
                        tweet_urls.append(u)
                limit = max(0, int(max_reply_expand))
                tweet_urls = tweet_urls[:limit]
                log(f"↪️ Mengambil balasan untuk {len(tweet_urls)} tweet…")
                for i, u in enumerate(tweet_urls, 1):
                    if state["not_logged_in"] or (stop_event is not None and stop_event.is_set()):
                        break
                    log(f"  → ({i}/{len(tweet_urls)}) {u}")
                    _fetch_and_scroll(
                        session, u, wait_ms=wait_ms, max_minutes=max_minutes,
                        log=log, label=f"balasan {i}", state=state, capture=capture, pacing=pacing,
                    )
                log(f"📡 Total respons API: {len(capture['rows'])}")
                rows = _parse_responses(capture["rows"], start_date, end_date)

    except Exception as exc:  # noqa: BLE001
        return [], f"Gagal menjalankan browser: {exc}"

    if state["not_logged_in"]:
        return [], "not_logged_in"
    if not rows:
        return [], "Tidak ada tweet yang cocok ditemukan."
    return rows, None
