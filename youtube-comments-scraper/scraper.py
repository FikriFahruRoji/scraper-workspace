"""Scraping komentar publik video YouTube menggunakan Scrapling.

Dua jalur pengambilan:

1. **Mode cepat (API)** — melalui browser stealth. Halaman tonton diambil sekali dengan
   ``StealthySession`` untuk mendapatkan kunci internal YouTube, lalu komentar
   diambil berpaginasi lewat endpoint ``youtubei/v1/next`` memakai
   *continuation token*. Cara ini bisa mengambil ribuan komentar dalam hitungan
   menit dan menyediakan timestamp relatif, jumlah suka, serta balasan.
2. **Mode browser** — cadangan. ``StealthySession`` membuka halaman video,
   menggulir bagian komentar, dan mengekstrak kartu komentar dari DOM.

Catatan penting:
  - Endpoint ``youtubei/v1/next`` adalah API internal YouTube yang **tidak
    terdokumentasi resmi**. YouTube dapat mengubah atau menutupnya kapan saja.
  - Aplikasi tidak melewati CAPTCHA, paywall, login, atau kontrol akses apa pun.
  - Gunakan hanya untuk komentar publik yang Anda berhak akses, patuhi
    Ketentuan Layanan YouTube, dan jangan mengumpulkan data pribadi/sensitif.
  - Untuk kebutuhan produksi, gunakan **YouTube Data API v3** resmi.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from scrapling.fetchers import StealthySession
from stealth_transport import StealthTransport

# --------------------------------------------------------------------------- #
# Konstanta
# --------------------------------------------------------------------------- #

_JAKARTA = ZoneInfo("Asia/Jakarta")

_API_ENDPOINT = "https://www.youtube.com/youtubei/v1/next"
_WATCH_URL = "https://www.youtube.com/watch?v={video_id}&hl=id&gl=ID"

_MAX_RUNTIME_MIN = 30       # batas keras satu proses, apa pun pengaturannya
_MAX_SCROLLS = 400          # pengaman mode browser
_STOP_AFTER = 6             # berhenti setelah N gulir tanpa komentar baru
_MAX_RETRIES = 3
_API_PAGE_DELAY_S = 0.4
_BROWSER_PAUSE_EVERY_S = 120
_BROWSER_PAUSE_S = 15

_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "www.youtu.be",
}

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_SORTS = {
    "teratas": "Teratas",
    "terbaru": "Terbaru",
}

# --------------------------------------------------------------------------- #
# JavaScript mode browser
# --------------------------------------------------------------------------- #

_BROWSER_VIDEO_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const meta = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.content || el.getAttribute('content') || '') : '';
  };
  const titleEl = document.querySelector('h1.ytd-watch-metadata yt-formatted-string')
    || document.querySelector('h1.title yt-formatted-string')
    || document.querySelector('h1');
  const ownerEl = document.querySelector('#owner #channel-name a')
    || document.querySelector('#owner a');

  // Penghitung tayangan YouTube memakai animasi digit, sehingga innerText
  // tidak stabil. Ambil angka aslinya dari skrip ytInitialPlayerResponse.
  let views = 0;
  const scripts = Array.from(document.scripts);
  const playerScript = scripts.find(
    (s) => (s.textContent || '').includes('ytInitialPlayerResponse')
  );
  if (playerScript) {
    const match = (playerScript.textContent || '').match(/"viewCount":"(\d+)"/);
    if (match) views = parseInt(match[1], 10) || 0;
  }

  return {
    judul: clean(meta('meta[property="og:title"]'))
      || clean(titleEl?.innerText || '')
      || document.title.replace(/\s*-\s*YouTube\s*$/, ''),
    kanal: clean(ownerEl?.innerText || ''),
    kanal_url: ownerEl?.href || '',
    penonton: views,
    tanggal_iso: meta('meta[itemprop="datePublished"]'),
    durasi_iso: meta('meta[itemprop="duration"]'),
    thumbnail: meta('meta[property="og:image"]'),
  };
}
"""

_BROWSER_EXPAND_JS = r"""
() => {
  let count = 0;
  const seen = new Set();
  const buttons = document.querySelectorAll(
    'ytd-comment-view-model #more, ytd-comment-view-model #expander #more'
  );
  for (const b of buttons) {
    if (seen.has(b)) continue;
    seen.add(b);
    const text = (b.innerText || '').trim();
    if (!/^(Lainnya|Selengkapnya|More|Show more)$/i.test(text)) continue;
    try { b.click(); count += 1; } catch (e) {}
  }
  return count;
}
"""

_BROWSER_EXTRACT_JS = r"""
() => {
  const clean = (v) => (v || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const thread of document.querySelectorAll('ytd-comment-thread-renderer')) {
    const c = thread.querySelector('ytd-comment-view-model#comment')
      || thread.querySelector('#comment')
      || thread;
    const linkEl = Array.from(c.querySelectorAll('a[href*="lc="]'))
      .find((a) => /[?&]lc=/.test(a.getAttribute('href') || ''));
    let id = '';
    if (linkEl) {
      const m = (linkEl.getAttribute('href') || '').match(/[?&]lc=([^&]+)/);
      if (m) id = decodeURIComponent(m[1]);
    }
    const authorEl = c.querySelector('#author-text');
    const author = clean(authorEl?.innerText || '').replace(/^@/, '');
    const text = clean(c.querySelector('#content-text')?.innerText || '');
    if (!author && !text) continue;
    out.push({
      id,
      nama: author,
      komentar: text,
      waktu: clean(c.querySelector('#published-time-text')?.innerText || ''),
      suka_teks: clean(c.querySelector('#vote-count-middle')?.innerText || ''),
      balasan_teks: clean(
        (thread.querySelector('#more-replies') || c.querySelector('#more-replies'))?.innerText || ''
      ),
      profil: authorEl?.getAttribute('href') || '',
      foto_profil: c.querySelector('#author-thumbnail img')?.src || '',
    });
  }
  return out;
}
"""

_BROWSER_SCROLL_JS = r"""
() => {
  window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700));
  return {
    y: Math.round(window.scrollY),
    h: Math.round(document.documentElement.scrollHeight),
    threads: document.querySelectorAll('ytd-comment-thread-renderer').length,
  };
}
"""

_BROWSER_COUNT_JS = r"""
() => {
  const el = document.querySelector('ytd-comments-header-renderer #count')
    || document.querySelector('#comments #count');
  return el ? (el.innerText || '').trim() : '';
}
"""

# --------------------------------------------------------------------------- #
# Helper umum
# --------------------------------------------------------------------------- #


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _now_jakarta() -> datetime:
    return datetime.now(tz=_JAKARTA)


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _sleep(seconds: float, stop_event: Optional[Any] = None) -> None:
    if seconds <= 0:
        return
    if stop_event is not None:
        stop_event.wait(seconds)
    else:
        time.sleep(seconds)


def _should_stop(stop_event: Optional[Any]) -> bool:
    return bool(stop_event is not None and stop_event.is_set())


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


_SUFFIX_MULTIPLIERS = {
    "rb": 1_000,
    "ribu": 1_000,
    "k": 1_000,
    "jt": 1_000_000,
    "juta": 1_000_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
}


def _parse_compact_count(raw: Any) -> int:
    """Baca angka ringkas YouTube: "72 rb", "1,2 jt", "1.2K", "3M"."""
    text = _clean(raw).replace("\xa0", " ")
    if not text:
        return 0
    if re.fullmatch(r"[\d.,]+", text):
        return _parse_count(text)

    match = re.search(r"([\d.,]+)\s*(rb|ribu|jt|juta|k|m|b)\b", text, re.I)
    if not match:
        return _parse_count(text)

    number = match.group(1)
    multiplier = _SUFFIX_MULTIPLIERS.get(match.group(2).lower(), 0)
    if not multiplier:
        return _parse_count(text)

    if "," in number and "." in number:
        if number.rfind(",") > number.rfind("."):
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif "," in number:
        number = number.replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        return _parse_count(text)
    return int(round(value * multiplier))


# --------------------------------------------------------------------------- #
# Tanggal relatif
# --------------------------------------------------------------------------- #

_UNIT_DAYS = {
    "detik": 1 / 86400,
    "menit": 1 / 1440,
    "jam": 1 / 24,
    "hari": 1,
    "minggu": 7,
    "bulan": 30,
    "tahun": 365,
    "second": 1 / 86400,
    "seconds": 1 / 86400,
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


def parse_relative_date(
    raw: str, now: Optional[datetime] = None
) -> Optional[datetime]:
    """Perkirakan tanggal absolut dari label relatif YouTube.

    Contoh: "7 tahun yang lalu", "6 menit yang lalu", "3 weeks ago".
    Nilainya perkiraan (bulan = 30 hari, tahun = 365 hari), bukan tanggal resmi.
    """
    text = _clean(raw)
    if not text:
        return None
    text = re.sub(r"\s*\((?:diedit|edited)\)\s*$", "", text, flags=re.I)
    now = now or _now_jakarta()
    low = text.lower()

    if re.search(r"\b(baru saja|sebentar lalu|just now|right now)\b", low):
        return now
    if re.search(r"\b(kemarin|yesterday)\b", low):
        return now - timedelta(days=1)

    match = re.search(
        r"(\d+)\s*(detik|menit|jam|hari|minggu|bulan|tahun|"
        r"second|seconds|minute|minutes|hour|hours|day|days|"
        r"week|weeks|month|months|year|years)\b",
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

    match = re.search(
        r"\b(?:a|an)\s+(second|minute|hour|day|week|month|year)\b", low
    )
    if match:
        days = _UNIT_DAYS.get(match.group(1), 0)
        if days:
            return now - timedelta(days=days)
    return None


# --------------------------------------------------------------------------- #
# Validasi link video
# --------------------------------------------------------------------------- #


def extract_video_id(value: str) -> str:
    """Ambil ID video 11 karakter dari link YouTube atau teks tempelan."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Masukkan link video YouTube terlebih dahulu.")

    url_match = re.search(r"https?://[^\s<>\"']+", raw, re.I)
    if url_match:
        raw = url_match.group(0).rstrip(".,;)]}")
    elif _VIDEO_ID_RE.match(raw):
        return raw
    elif re.match(
        r"^(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/", raw, re.I
    ):
        raw = "https://" + raw
    else:
        raise ValueError(
            "Link tidak dikenali. Tempel link video YouTube "
            "(youtube.com/watch?v=…, youtu.be/…, atau youtube.com/shorts/…)."
        )

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Link harus diawali http:// atau https://.")
    if parsed.username or parsed.password:
        raise ValueError("Link tidak boleh memuat kredensial.")

    host = (parsed.hostname or "").lower().strip(".")
    if host not in _ALLOWED_HOSTS:
        raise ValueError(
            "Hanya link YouTube (youtube.com atau youtu.be) yang didukung."
        )

    video_id = ""
    path = parsed.path or ""
    if host.endswith("youtu.be"):
        video_id = path.strip("/").split("/")[0]
    elif path.startswith("/watch"):
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
    elif path.startswith(("/shorts/", "/live/", "/embed/", "/v/")):
        video_id = path.strip("/").split("/")[1] if "/" in path.strip("/") else ""
    elif "v" in parse_qs(parsed.query):
        video_id = parse_qs(parsed.query)["v"][0]

    video_id = video_id.strip()
    if not _VIDEO_ID_RE.match(video_id):
        raise ValueError(
            "ID video tidak valid. Pastikan link menunjuk ke satu video, "
            "bukan channel, playlist, atau halaman utama YouTube."
        )
    return video_id


def normalize_youtube_url(value: str) -> tuple[str, str]:
    """Kembalikan (URL tonton kanonik, ID video)."""
    video_id = extract_video_id(value)
    return _WATCH_URL.format(video_id=video_id), video_id


def _absolute_youtube_url(value: str) -> str:
    value = _clean(value)
    if not value:
        return ""
    if value.startswith("//"):
        return "https:" + value
    if value.startswith("/"):
        return "https://www.youtube.com" + value
    if not value.startswith("http"):
        return "https://www.youtube.com/" + value.lstrip("/")
    return value


# --------------------------------------------------------------------------- #
# Parsing halaman tonton
# --------------------------------------------------------------------------- #


def _joined_text(value: Any) -> str:
    """Gabungkan simpleText / runs YouTube menjadi teks biasa."""
    if isinstance(value, str):
        return _clean(value)
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("simpleText"), str):
        return _clean(value["simpleText"])
    runs = value.get("runs")
    if isinstance(runs, list):
        return _clean(
            "".join(
                str(run.get("text") or "")
                for run in runs
                if isinstance(run, dict)
            )
        )
    return ""


def _extract_assignment(text: str, name: str) -> Optional[Any]:
    """Ambil objek JSON dari assignment seperti ``var ytInitialData = {...}``."""
    match = re.search(rf"{re.escape(name)}\s*=\s*\{{", text)
    if not match:
        return None
    start = match.end() - 1
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
        return data
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_ytcfg_keys(text: str) -> dict[str, str]:
    def grab(key: str) -> str:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', text)
        return match.group(1) if match else ""

    return {
        "api_key": grab("INNERTUBE_API_KEY"),
        "client_version": grab("INNERTUBE_CONTEXT_CLIENT_VERSION"),
        "client_name": grab("INNERTUBE_CLIENT_NAME") or "WEB",
        "visitor_data": grab("VISITOR_DATA"),
    }


def _find_objects(root: Any, key: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get(key)
            if isinstance(value, dict):
                found.append(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(root)
    return found


def _parse_video(
    initial: Any, player: Any, video_id: str, url: str
) -> dict[str, Any]:
    details = {}
    if isinstance(player, dict):
        details = player.get("videoDetails") or {}

    title = _first_str(details.get("title"))
    author = _first_str(details.get("author"))
    channel_id = _first_str(details.get("channelId"))
    views = _parse_count(details.get("viewCount"))
    try:
        length = int(details.get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        length = 0

    thumbnail = ""
    thumbs = (details.get("thumbnail") or {}).get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        thumbnail = _first_str(thumbs[-1].get("url"))

    date_text = ""
    channel_url = ""
    subscribers = ""

    for primary in _find_objects(initial, "videoPrimaryInfoRenderer"):
        if not title:
            title = _joined_text(primary.get("title"))
        if not date_text:
            date_text = _joined_text(primary.get("dateText"))
        if not views:
            views = _parse_count(_joined_text(primary.get("viewCount")))

    for owner in _find_objects(initial, "videoOwnerRenderer"):
        if not author:
            author = _joined_text(owner.get("title"))
        if not channel_id:
            browse = (
                (owner.get("navigationEndpoint") or {}).get("browseEndpoint")
                or {}
            )
            channel_id = _first_str(browse.get("browseId"))
            channel_url = _first_str(browse.get("canonicalBaseUrl"))
        if not subscribers:
            subscribers = _joined_text(owner.get("subscriberCountText"))

    if channel_url.startswith("/"):
        channel_url = "https://www.youtube.com" + channel_url
    if not channel_url and channel_id:
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
    if not thumbnail:
        thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    return {
        "video_id": video_id,
        "judul": title or f"Video {video_id}",
        "kanal": author,
        "kanal_id": channel_id,
        "kanal_url": channel_url,
        "pelanggan": subscribers,
        "penonton": views,
        "tanggal": date_text,
        "durasi_detik": length if length > 0 else 0,
        "thumbnail": thumbnail,
        "link": url,
    }


def _playability_error(player: Any) -> Optional[str]:
    if not isinstance(player, dict):
        return None
    status = player.get("playabilityStatus") or {}
    code = _clean(status.get("status")).upper()
    if code in ("", "OK", "LIVE_STREAM_OFFLINE"):
        return None
    if code in ("LOGIN_REQUIRED", "AGE_VERIFICATION_REQUIRED"):
        return "login_required"
    if code in ("ERROR", "UNPLAYABLE"):
        return "video_unavailable"
    return None


def _comment_continuation(initial: Any) -> str:
    """Token untuk memuat bagian komentar dari ytInitialData."""
    tokens: list[str] = []

    def token_from_item(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        renderer = item.get("continuationItemRenderer") or {}
        endpoint = renderer.get("continuationEndpoint") or {}
        command = endpoint.get("continuationCommand") or {}
        return _clean(command.get("token"))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("sectionIdentifier") == "comment-item-section":
                for item in node.get("contents") or []:
                    token = token_from_item(item)
                    if token:
                        tokens.append(token)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(initial)
    return tokens[0] if tokens else ""


def _sort_tokens(initial: Any) -> dict[str, str]:
    """Token urutan komentar: teratas / terbaru (dari menu sortir YouTube)."""
    found: dict[str, str] = {}
    fallback: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            menu = node.get("sortFilterSubMenuRenderer")
            if isinstance(menu, dict):
                for item in menu.get("subMenuItems") or []:
                    if not isinstance(item, dict):
                        continue
                    title = _joined_text(item.get("title")).lower()
                    endpoint = item.get("serviceEndpoint") or {}
                    token = _clean(
                        (endpoint.get("continuationCommand") or {}).get("token")
                    )
                    if not token:
                        continue
                    fallback.append(token)
                    if "terbaru" in title or "newest" in title:
                        found.setdefault("terbaru", token)
                    elif "teratas" in title or "top" in title:
                        found.setdefault("teratas", token)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(initial)
    if "terbaru" not in found and len(fallback) > 1:
        # Urutan menu YouTube selalu: Teratas, Terbaru.
        found["terbaru"] = fallback[1]
    return found


def _continuation_token(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    renderer = item.get("continuationItemRenderer") or {}
    endpoint = renderer.get("continuationEndpoint") or {}
    if not endpoint:
        button = (renderer.get("button") or {}).get("buttonRenderer") or {}
        endpoint = button.get("command") or {}
    command = endpoint.get("continuationCommand") or {}
    return _clean(command.get("token"))


def _reply_continuation(thread: Any) -> str:
    replies = (thread.get("replies") or {}).get("commentRepliesRenderer") or {}
    for item in replies.get("contents") or []:
        token = _continuation_token(item)
        if token:
            return token
    return ""


def _response_items(data: Any) -> list[dict[str, Any]]:
    """Kumpulkan item dari semua aksi continuation di respons Innertube."""
    items: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return items
    for endpoint in data.get("onResponseReceivedEndpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        for value in endpoint.values():
            if isinstance(value, dict):
                chunk = value.get("continuationItems")
                if isinstance(chunk, list):
                    items.extend(item for item in chunk if isinstance(item, dict))
    if items:
        return items

    # Cadangan untuk bentuk respons lama/berbeda.
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            thread = node.get("commentThreadRenderer")
            if isinstance(thread, dict):
                items.append({"commentThreadRenderer": thread})
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(data)
    return items


def _response_payloads(data: Any) -> dict[str, dict[str, Any]]:
    """Peta commentId -> commentEntityPayload dari frameworkUpdates."""
    payloads: dict[str, dict[str, Any]] = {}
    if not isinstance(data, dict):
        return payloads
    mutations = (
        ((data.get("frameworkUpdates") or {}).get("entityBatchUpdate") or {})
        .get("mutations")
        or []
    )
    for mutation in mutations:
        if not isinstance(mutation, dict):
            continue
        payload = (mutation.get("payload") or {}).get("commentEntityPayload")
        if not isinstance(payload, dict):
            continue
        comment_id = _clean((payload.get("properties") or {}).get("commentId"))
        if comment_id:
            payloads[comment_id] = payload
    return payloads


def _header_count(items: list[dict[str, Any]]) -> int:
    for item in items:
        header = item.get("commentsHeaderRenderer")
        if isinstance(header, dict):
            count = _parse_compact_count(_joined_text(header.get("countText")))
            if count:
                return count
    return 0


def _next_continuation(items: list[dict[str, Any]]) -> str:
    for item in reversed(items):
        token = _continuation_token(item)
        if token:
            return token
    return ""


# --------------------------------------------------------------------------- #
# Baris komentar
# --------------------------------------------------------------------------- #


def _payload_text(payload: dict[str, Any]) -> str:
    content = (payload.get("properties") or {}).get("content")
    if isinstance(content, str):
        return _clean(content)
    if isinstance(content, dict):
        inner = content.get("content")
        if isinstance(inner, str):
            return _clean(inner)
        runs = content.get("runs") or []
        return _clean(
            "".join(
                str(run.get("text") or "")
                for run in runs
                if isinstance(run, dict)
            )
        )
    return ""


def _row_from_payload(
    payload: dict[str, Any],
    *,
    video: dict[str, Any],
    now: datetime,
    kind: str,
    parent_id: str = "",
    level: int = 0,
) -> Optional[dict[str, Any]]:
    properties = payload.get("properties") or {}
    author = payload.get("author") or {}
    toolbar = payload.get("toolbar") or {}

    comment_id = _clean(properties.get("commentId"))
    text = _payload_text(payload)
    name = _clean(author.get("displayName")).lstrip("@")
    if not comment_id and not text and not name:
        return None

    published = _clean(properties.get("publishedTime"))
    parsed_date = parse_relative_date(published, now)
    likes = _parse_compact_count(
        _first_str(
            toolbar.get("likeCountNotliked"),
            toolbar.get("likeCountLiked"),
            toolbar.get("likeCountA11y"),
        )
    )
    reply_count = _parse_compact_count(
        _first_str(toolbar.get("replyCount"), toolbar.get("replyCountA11y"))
    )
    if kind == "balasan":
        reply_count = 0

    channel_id = _clean(author.get("channelId"))
    channel_url = ""
    command = (author.get("channelCommand") or {}).get("innertubeCommand") or {}
    browse = command.get("browseEndpoint") or {}
    web = (command.get("commandMetadata") or {}).get("webCommandMetadata") or {}
    channel_url = _first_str(
        web.get("url"), browse.get("canonicalBaseUrl")
    )
    channel_url = _absolute_youtube_url(channel_url)
    if not channel_url and channel_id:
        channel_url = f"https://www.youtube.com/channel/{channel_id}"

    video_id = video.get("video_id") or ""
    link_comment = (
        f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
        if video_id and comment_id
        else ""
    )

    return {
        "id_komentar": comment_id,
        "jenis": kind,
        "id_induk": parent_id,
        "level": level,
        "nama": name,
        "komentar": text,
        "suka": likes,
        "balasan": reply_count,
        "waktu": published,
        "perkiraan_tanggal": (
            parsed_date.strftime("%Y-%m-%d") if parsed_date else ""
        ),
        "terverifikasi": bool(author.get("isVerified")),
        "kreator": bool(author.get("isCreator")),
        "kanal_id": channel_id,
        "profil": channel_url,
        "foto_profil": _clean(author.get("avatarThumbnailUrl")),
        "link_komentar": link_comment,
        "video_id": video_id,
        "judul_video": video.get("judul", ""),
        "link_video": video.get("link", ""),
    }


def _row_from_legacy_thread(
    thread: dict[str, Any], *, video: dict[str, Any], now: datetime
) -> Optional[dict[str, Any]]:
    """Cadangan untuk respons format lama (commentRenderer)."""
    renderer = thread.get("commentRenderer") or {}
    if not isinstance(renderer, dict):
        return None

    comment_id = _clean(renderer.get("commentId"))
    text = _joined_text(renderer.get("contentText"))
    author = (renderer.get("authorText") or {}).get("simpleText") or ""
    author = _clean(author).lstrip("@")
    if not comment_id and not text:
        return None

    published = _joined_text(renderer.get("publishedTimeText"))
    parsed_date = parse_relative_date(published, now)
    likes = _parse_count(
        (renderer.get("voteCount") or {}).get("simpleText") or ""
    )
    replies = _parse_count(renderer.get("replyCount") or 0)
    browse = (
        (renderer.get("authorEndpoint") or {}).get("browseEndpoint") or {}
    )
    channel_id = _clean(browse.get("browseId"))
    profile = _absolute_youtube_url(
        _first_str(
            browse.get("canonicalBaseUrl"),
            (renderer.get("authorEndpoint") or {})
            .get("commandMetadata", {})
            .get("webCommandMetadata", {})
            .get("url"),
        )
    )
    if not profile and channel_id:
        profile = f"https://www.youtube.com/channel/{channel_id}"

    video_id = video.get("video_id") or ""
    return {
        "id_komentar": comment_id,
        "jenis": "komentar",
        "id_induk": "",
        "level": 0,
        "nama": author,
        "komentar": text,
        "suka": likes,
        "balasan": replies,
        "waktu": published,
        "perkiraan_tanggal": (
            parsed_date.strftime("%Y-%m-%d") if parsed_date else ""
        ),
        "terverifikasi": False,
        "kreator": False,
        "kanal_id": channel_id,
        "profil": profile,
        "foto_profil": "",
        "link_komentar": (
            f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
            if video_id and comment_id
            else ""
        ),
        "video_id": video_id,
        "judul_video": video.get("judul", ""),
        "link_video": video.get("link", ""),
    }


# --------------------------------------------------------------------------- #
# Permintaan HTTP
# --------------------------------------------------------------------------- #


def _page_text(response: Any) -> str:
    html_content = getattr(response, "html_content", None)
    if html_content is not None:
        text = str(html_content)
        if text:
            return text
    body = getattr(response, "body", b"") or b""
    if isinstance(body, (bytes, bytearray)):
        return bytes(body).decode("utf-8", "ignore")
    return str(body)


def _load_watch_page(session: Any, url: str) -> str:
    response = session.get(
        url,
        timeout=30,
    )
    if getattr(response, "status", 0) != 200:
        raise RuntimeError(f"HTTP {getattr(response, 'status', '?')}")
    text = _page_text(response)
    if not text:
        raise RuntimeError("Halaman video kosong.")
    return text


def _post_next(
    session: Any,
    *,
    api_key: str,
    client_version: str,
    visitor_data: str,
    continuation: str,
    referer: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": client_version or "2.20260101.00.00",
                "hl": "id",
                "gl": "ID",
            }
        },
        "continuation": continuation,
    }
    if visitor_data:
        body["context"]["client"]["visitorData"] = visitor_data

    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "Referer": referer,
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": client_version,
    }
    if visitor_data:
        headers["X-Goog-Visitor-Id"] = visitor_data

    response = session.post(
        _API_ENDPOINT,
        params={"key": api_key, "prettyPrint": "false"},
        json=body,
        headers=headers,
        timeout=30,
    )
    status = getattr(response, "status", 0)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")

    body_bytes = getattr(response, "body", b"") or b""
    if isinstance(body_bytes, (bytes, bytearray)):
        text = bytes(body_bytes).decode("utf-8", "ignore")
    else:
        text = str(body_bytes)
    if text.startswith(")]}'"):
        text = text.split("\n", 1)[-1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError("Respons Innertube tidak dikenali.")
    return data


def _post_with_retry(
    session: Any,
    *,
    api_key: str,
    client_version: str,
    visitor_data: str,
    continuation: str,
    referer: str,
    log: Callable[[str], None],
    stop_event: Optional[Any],
) -> dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        if _should_stop(stop_event):
            break
        try:
            return _post_next(
                session,
                api_key=api_key,
                client_version=client_version,
                visitor_data=visitor_data,
                continuation=continuation,
                referer=referer,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < _MAX_RETRIES - 1:
                wait_s = 2 + attempt * 3
                log(f"⚠️ Permintaan komentar gagal ({exc}); ulang dalam {wait_s}s…")
                _sleep(wait_s, stop_event)
    raise RuntimeError(str(last_error) if last_error else "Permintaan dibatalkan.")


def _elapsed_minutes(started: float) -> float:
    return (time.time() - started) / 60.0


def _limit_reason(
    *,
    started: float,
    max_minutes: int,
    stop_event: Optional[Any],
) -> Optional[str]:
    if _should_stop(stop_event):
        return "dibatalkan"
    if max_minutes and _elapsed_minutes(started) > max_minutes:
        return "waktu"
    if _elapsed_minutes(started) > _MAX_RUNTIME_MIN:
        return "waktu"
    return None


# --------------------------------------------------------------------------- #
# Mode cepat (Innertube)
# --------------------------------------------------------------------------- #


def _scrape_api(
    session: Any,
    *,
    url: str,
    video_id: str,
    sort: str,
    max_comments: int,
    include_replies: bool,
    max_replies: int,
    max_minutes: int,
    delay_s: float,
    log: Callable[[str], None],
    progress: Callable[[dict[str, Any]], None],
    stop_event: Optional[Any],
    checkpoint_cb=None,
) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "rows": [],
        "video": {},
        "total_komentar": None,
        "stop_reason": "habis",
        "error": None,
        "message": "",
    }

    html_text = _load_watch_page(session, url)
    initial = _extract_assignment(html_text, "ytInitialData")
    player = _extract_assignment(html_text, "ytInitialPlayerResponse")
    if not isinstance(initial, dict):
        result["error"] = "api_error"
        result["message"] = (
            "Data halaman YouTube tidak ditemukan. YouTube mungkin menampilkan "
            "halaman persetujuan atau memblokir permintaan otomatis."
        )
        return result

    playability = _playability_error(player)
    video = _parse_video(initial, player, video_id, url)
    result["video"] = video

    if playability == "login_required":
        result["error"] = "login_required"
        result["message"] = (
            "YouTube meminta login/verifikasi usia untuk video ini. Scraper "
            "tidak memakai akun, jadi video seperti ini tidak dapat diambil."
        )
        return result
    if playability == "video_unavailable":
        reason = ""
        if isinstance(player, dict):
            reason = _joined_text(
                (player.get("playabilityStatus") or {}).get("reason")
            )
        result["error"] = "video_unavailable"
        result["message"] = reason or (
            "Video tidak tersedia: mungkin privat, dihapus, atau dibatasi."
        )
        return result

    keys = _extract_ytcfg_keys(html_text)
    if not keys["api_key"]:
        result["error"] = "api_error"
        result["message"] = (
            "Kunci API internal YouTube tidak ditemukan di halaman video. "
            "Coba lagi nanti atau gunakan mode browser."
        )
        return result

    token = _comment_continuation(initial)
    sort_tokens = _sort_tokens(initial)
    if sort == "terbaru":
        newest = sort_tokens.get("terbaru")
        if newest:
            token = newest
            log("↕️ Urutan komentar: Terbaru.")
        else:
            log("⚠️ Token urutan 'Terbaru' tidak ditemukan; memakai urutan Teratas.")
    if not token:
        result["error"] = "comments_disabled"
        result["message"] = (
            "Bagian komentar tidak ditemukan. Kemungkinan komentar video ini "
            "dinonaktifkan, dibatasi, atau belum tersedia."
        )
        return result

    log("⚡ Mode cepat: endpoint komentar internal YouTube (melalui browser stealth).")
    log(f"🎬 {video['judul']}")
    if video.get("kanal"):
        log(f"👤 Kanal: {video['kanal']}")
    if video.get("penonton"):
        log(f"👀 {video['penonton']:,} penonton".replace(",", "."))

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    reply_tokens: dict[str, str] = {}
    page = 0
    total_claim = 0
    next_token = token
    seen_tokens: set[str] = set()
    stop_reason = "habis"
    now = _now_jakarta()

    while next_token:
        reason = _limit_reason(
            started=started, max_minutes=max_minutes, stop_event=stop_event
        )
        if reason:
            stop_reason = reason
            break
        if max_comments and len(rows) >= max_comments:
            stop_reason = "maks"
            break

        try:
            data = _post_with_retry(
                session,
                api_key=keys["api_key"],
                client_version=keys["client_version"],
                visitor_data=keys["visitor_data"],
                continuation=next_token,
                referer=url,
                log=log,
                stop_event=stop_event,
            )
        except Exception as exc:  # noqa: BLE001
            if rows:
                stop_reason = "gangguan"
                result["message"] = (
                    f"Pengambilan berhenti karena gangguan jaringan: {exc}. "
                    "Komentar yang sudah terkumpul tetap disimpan."
                )
            else:
                result["error"] = "blocked"
                result["message"] = (
                    f"Permintaan komentar gagal: {exc}. YouTube mungkin sedang "
                    "membatasi akses; coba lagi nanti atau gunakan mode browser."
                )
            log(f"⚠️ {result['message']}")
            break

        page += 1
        items = _response_items(data)
        payloads = _response_payloads(data)
        if not total_claim:
            total_claim = _header_count(items)
            if total_claim:
                result["total_komentar"] = total_claim
                log(
                    f"💬 YouTube mencantumkan {total_claim:,} komentar."
                    .replace(",", ".")
                )

        added = 0
        for item in items:
            thread = item.get("commentThreadRenderer")
            if not isinstance(thread, dict):
                continue

            view_model = (
                (thread.get("commentViewModel") or {}).get("commentViewModel")
                or {}
            )
            comment_id = _clean(view_model.get("commentId"))
            payload = payloads.get(comment_id) if comment_id else None
            row: Optional[dict[str, Any]] = None
            if payload:
                row = _row_from_payload(
                    payload,
                    video=video,
                    now=now,
                    kind="komentar",
                    level=0,
                )
            elif thread.get("commentRenderer"):
                row = _row_from_legacy_thread(thread, video=video, now=now)
            if not row:
                continue

            comment_id = row["id_komentar"] or (
                f"{row['nama']}|{row['waktu']}|{row['komentar'][:100]}"
            )
            key = comment_id
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1

            reply_token = _reply_continuation(thread)
            if include_replies and reply_token and row.get("balasan"):
                reply_tokens[row["id_komentar"]] = reply_token

            if max_comments and len(rows) >= max_comments:
                break

        if checkpoint_cb and added:
            checkpoint_cb(rows)
        progress(
            {
                "komentar": len(rows),
                "balasan": 0,
                "halaman": page,
                "total_klaim": total_claim or None,
            }
        )
        if page == 1 or page % 5 == 0 or added == 0:
            log(f"📡 Halaman {page} · +{added} · total {len(rows)} komentar")

        if max_comments and len(rows) >= max_comments:
            stop_reason = "maks"
            break
        if _should_stop(stop_event):
            stop_reason = "dibatalkan"
            break

        next_token = _next_continuation(items)
        if not next_token or next_token in seen_tokens:
            stop_reason = "habis"
            break
        seen_tokens.add(next_token)
        _sleep(delay_s, stop_event)

    if not rows:
        result["stop_reason"] = stop_reason
        if result["error"]:
            return result
        result["error"] = "no_comments"
        result["message"] = (
            "Tidak ada komentar yang berhasil dibaca. Video mungkin memiliki "
            "komentar dinonaktifkan, komentar dibatasi, atau struktur respons "
            "YouTube berubah."
        )
        return result

    # ---- Balasan (opsional) --------------------------------------------- #
    replies_map: dict[str, list[dict[str, Any]]] = {}
    if include_replies and reply_tokens:
        log(
            f"↩️ Mengambil balasan untuk {len(reply_tokens)} komentar "
            f"(maks. {max_replies or 'semua'} per komentar)…"
        )
        total_replies = 0
        for index, (parent_id, reply_token) in enumerate(reply_tokens.items(), 1):
            reason = _limit_reason(
                started=started,
                max_minutes=max_minutes,
                stop_event=stop_event,
            )
            if reason:
                stop_reason = reason
                break
            collected: list[dict[str, Any]] = []
            token_cursor = reply_token
            seen_reply_tokens: set[str] = set()
            while token_cursor:
                reason = _limit_reason(started=started, max_minutes=max_minutes, stop_event=stop_event)
                if reason:
                    stop_reason = reason
                    break
                if max_replies and len(collected) >= max_replies:
                    break
                try:
                    reply_data = _post_with_retry(
                        session,
                        api_key=keys["api_key"],
                        client_version=keys["client_version"],
                        visitor_data=keys["visitor_data"],
                        continuation=token_cursor,
                        referer=url,
                        log=log,
                        stop_event=stop_event,
                    )
                except Exception as exc:  # noqa: BLE001
                    log(f"⚠️ Balasan {parent_id[:12]}… gagal: {exc}")
                    break

                reply_payloads = _response_payloads(reply_data)
                reply_items = _response_items(reply_data)
                for payload in reply_payloads.values():
                    row = _row_from_payload(
                        payload,
                        video=video,
                        now=now,
                        kind="balasan",
                        parent_id=parent_id,
                        level=1,
                    )
                    if not row:
                        continue
                    if max_replies and len(collected) >= max_replies:
                        break
                    collected.append(row)

                if checkpoint_cb and collected:
                    checkpoint_cb(rows + [r for replies in replies_map.values() for r in replies] + collected)
                token_cursor = _next_continuation(reply_items)
                if not token_cursor or token_cursor in seen_reply_tokens:
                    break
                seen_reply_tokens.add(token_cursor)
                _sleep(max(0.25, delay_s * 0.75), stop_event)

            if collected:
                replies_map[parent_id] = collected
                total_replies += len(collected)
                if index == 1 or index % 5 == 0:
                    log(
                        f"  ↩️ {index}/{len(reply_tokens)} komentar · "
                        f"{total_replies} balasan"
                    )
            progress(
                {
                    "komentar": len(rows),
                    "balasan": total_replies,
                    "halaman": page,
                    "total_klaim": total_claim or None,
                }
            )
            if _should_stop(stop_event):
                stop_reason = "dibatalkan"
                break

    ordered: list[dict[str, Any]] = []
    for row in rows:
        ordered.append(row)
        ordered.extend(replies_map.get(row["id_komentar"], []))
    for index, row in enumerate(ordered):
        row["_order"] = index

    if stop_reason == "gangguan" and not result["message"]:
        result["message"] = "Sebagian komentar gagal diambil karena gangguan."

    result["rows"] = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in ordered
    ]
    result["stop_reason"] = stop_reason
    result["video"] = video
    result["total_komentar"] = total_claim or result["total_komentar"]
    log(
        f"🏁 Selesai · {len(rows)} komentar"
        + (f" + {sum(len(v) for v in replies_map.values())} balasan" if replies_map else "")
        + f" · {page} halaman."
    )
    return result


# --------------------------------------------------------------------------- #
# Mode browser
# --------------------------------------------------------------------------- #


def _handle_consent(page: Any, log: Callable[[str], None]) -> None:
    try:
        host = (urlparse(page.url).hostname or "").lower()
        body = (page.inner_text("body") or "").lower()
        needs = (
            "consent" in host
            or "sebelum anda melanjutkan" in body
            or "before you continue" in body
        )
        if not needs:
            return
        log("🍪 Layar persetujuan YouTube terdeteksi, mencoba menerima…")
        for label in ("Terima semua", "Accept all", "Setuju", "I agree"):
            try:
                button = page.get_by_role(
                    "button", name=re.compile(rf"^{re.escape(label)}$", re.I)
                ).first
                if button and button.is_visible():
                    button.click()
                    page.wait_for_timeout(3000)
                    return
            except Exception:
                continue
    except Exception:
        pass


def _load_browser_comments(page: Any, stop_event=None) -> bool:
    try:
        page.evaluate(
            "() => { const el = document.querySelector('#comments'); "
            "if (el) el.scrollIntoView({block: 'start'}); }"
        )
    except Exception:
        pass
    for _ in range(40):
        if _should_stop(stop_event):
            return False
        try:
            if page.query_selector("ytd-comment-thread-renderer"):
                return True
        except Exception:
            pass
        try:
            page.evaluate(
                "window.scrollBy(0, Math.max(window.innerHeight, 800))"
            )
            page.wait_for_timeout(1200)
        except Exception:
            break
    return False


def _parse_iso_duration(value: str) -> int:
    """Ubah durasi ISO-8601 YouTube ("PT3M34S") menjadi detik."""
    match = re.match(
        r"^P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", _clean(value), re.I
    )
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


_MONTHS_ID = [
    "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
    "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
]


def _format_iso_date(value: str) -> str:
    """Format "2009-10-24T23:57:33-07:00" menjadi "24 Okt 2009"."""
    text = _clean(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return f"{parsed.day} {_MONTHS_ID[parsed.month - 1]} {parsed.year}"


def _browser_video(page: Any, video_id: str, url: str) -> dict[str, Any]:
    try:
        raw = page.evaluate(_BROWSER_VIDEO_JS) or {}
    except Exception:
        raw = {}

    views = int(raw.get("penonton") or 0)
    if not views:
        try:
            info = _clean(page.inner_text("#info"))
        except Exception:
            info = ""
        match = re.search(
            r"([\d.,]+\s*(?:rb|ribu|jt|juta|k|m|b)?)\s*(?:x\s*)?"
            r"(?:ditonton|views?)",
            info,
            re.I,
        )
        if match:
            views = _parse_compact_count(match.group(1))

    thumbnail = _clean(raw.get("thumbnail")) or (
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    )
    return {
        "video_id": video_id,
        "judul": _clean(raw.get("judul")) or f"Video {video_id}",
        "kanal": _clean(raw.get("kanal")),
        "kanal_id": "",
        "kanal_url": _clean(raw.get("kanal_url")),
        "pelanggan": "",
        "penonton": views,
        "tanggal": _format_iso_date(raw.get("tanggal_iso") or ""),
        "durasi_detik": _parse_iso_duration(raw.get("durasi_iso") or ""),
        "thumbnail": thumbnail,
        "link": url,
    }


def _browser_total(page: Any) -> int:
    try:
        return _parse_count(page.evaluate(_BROWSER_COUNT_JS) or "")
    except Exception:
        return 0


def _browser_extract(page: Any) -> list[dict[str, Any]]:
    try:
        data = page.evaluate(_BROWSER_EXTRACT_JS)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _browser_row(
    item: dict[str, Any],
    *,
    video: dict[str, Any],
    now: datetime,
    order: int,
) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    name = _clean(item.get("nama"))
    text = _clean(item.get("komentar"))
    if not name and not text:
        return None

    comment_id = _clean(item.get("id"))
    published = _clean(item.get("waktu"))
    parsed_date = parse_relative_date(published, now)
    profile = _absolute_youtube_url(item.get("profil") or "")
    video_id = video.get("video_id") or ""

    return {
        "id_komentar": comment_id,
        "jenis": "komentar",
        "id_induk": "",
        "level": 0,
        "nama": name,
        "komentar": text,
        "suka": _parse_compact_count(item.get("suka_teks")),
        "balasan": _parse_compact_count(item.get("balasan_teks")),
        "waktu": published,
        "perkiraan_tanggal": (
            parsed_date.strftime("%Y-%m-%d") if parsed_date else ""
        ),
        "terverifikasi": False,
        "kreator": False,
        "kanal_id": "",
        "profil": profile,
        "foto_profil": _clean(item.get("foto_profil")),
        "link_komentar": (
            f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
            if video_id and comment_id
            else ""
        ),
        "video_id": video_id,
        "judul_video": video.get("judul", ""),
        "link_video": video.get("link", ""),
        "_order": order,
    }


def _scrape_browser(
    *,
    url: str,
    video_id: str,
    headless: bool,
    real_chrome: bool,
    wait_ms: int,
    max_comments: int,
    max_minutes: int,
    log: Callable[[str], None],
    progress: Callable[[dict[str, Any]], None],
    stop_event: Optional[Any],
    checkpoint_cb=None,
) -> dict[str, Any]:
    started = time.time()
    state: dict[str, Any] = {
        "video": {},
        "rows": [],
        "total": 0,
        "error": None,
        "message": "",
        "stop_reason": "habis",
    }

    def action(page: Any) -> None:
        try:
            # Halaman YouTube sempat melakukan navigasi ulang setelah dimuat;
            # query DOM terlalu awal membuat execution context terdestroy.
            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            _handle_consent(page, log)
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

            if not _load_browser_comments(page, stop_event):
                state["error"] = "no_comments"
                state["message"] = (
                    "Komentar tidak dapat dimuat di browser. Kemungkinan "
                    "komentar dinonaktifkan atau halaman gagal dirender."
                )
                log(f"⚠️ {state['message']}")
                return

            video = _browser_video(page, video_id, url)
            state["video"] = video
            state["total"] = _browser_total(page)
            log(f"🖥️ Mode browser: {'headless' if headless else 'tampak'}.")
            log(f"🎬 {video['judul']}")
            if video.get("kanal"):
                log(f"👤 Kanal: {video['kanal']}")
            if state["total"]:
                log(
                    f"💬 YouTube mencantumkan {state['total']:,} komentar."
                    .replace(",", ".")
                )

            now = _now_jakarta()
            collected: dict[str, dict[str, Any]] = {}

            def add_items() -> int:
                added = 0
                for item in _browser_extract(page):
                    row = _browser_row(
                        item, video=video, now=now, order=len(collected)
                    )
                    if not row:
                        continue
                    key = row["id_komentar"] or "|".join(
                        [
                            row["nama"],
                            row["waktu"],
                            row["komentar"][:120],
                        ]
                    )
                    if key in collected:
                        continue
                    collected[key] = row
                    added += 1
                if checkpoint_cb and added:
                    rows = list(collected.values())
                    checkpoint_cb(rows[:max_comments] if max_comments else rows)
                return added

            try:
                page.evaluate(_BROWSER_EXPAND_JS)
            except Exception:
                pass
            add_items()
            log(f"🧩 Komentar awal: {len(collected)}")
            progress(
                {
                    "komentar": len(collected),
                    "balasan": 0,
                    "halaman": 0,
                    "total_klaim": state["total"] or None,
                }
            )

            no_new = 0
            last_pause = time.time()
            for scroll in range(1, _MAX_SCROLLS + 1):
                reason = _limit_reason(
                    started=started,
                    max_minutes=max_minutes,
                    stop_event=stop_event,
                )
                if reason:
                    state["stop_reason"] = reason
                    break
                if max_comments and len(collected) >= max_comments:
                    state["stop_reason"] = "maks"
                    log(f"🛑 Batas maksimum {max_comments} komentar tercapai.")
                    break

                before = len(collected)
                try:
                    page.evaluate(_BROWSER_EXPAND_JS)
                except Exception:
                    pass
                add_items()

                try:
                    stats = page.evaluate(_BROWSER_SCROLL_JS) or {}
                except Exception:
                    stats = {}
                page.wait_for_timeout(max(600, int(wait_ms)))
                try:
                    page.evaluate(_BROWSER_EXPAND_JS)
                except Exception:
                    pass
                add_items()

                delta = len(collected) - before
                if delta > 0:
                    no_new = 0
                    log(
                        f"  ➕ {delta} komentar baru setelah gulir #{scroll} "
                        f"· total {len(collected)}"
                    )
                else:
                    no_new += 1

                progress(
                    {
                        "komentar": len(collected),
                        "balasan": 0,
                        "halaman": scroll,
                        "total_klaim": state["total"] or None,
                    }
                )

                if scroll % 5 == 0:
                    log(
                        f"  ↕️ gulir #{scroll} · y={stats.get('y', 0)} "
                        f"· komentar unik: {len(collected)}"
                    )

                if time.time() - last_pause >= _BROWSER_PAUSE_EVERY_S:
                    log(
                        f"⏸️ Jeda {_BROWSER_PAUSE_S} detik untuk mengurangi "
                        "risiko rate-limit…"
                    )
                    page.wait_for_timeout(_BROWSER_PAUSE_S * 1000)
                    last_pause = time.time()

                at_bottom = (
                    int(stats.get("y") or 0) + 1000
                    >= int(stats.get("h") or 0)
                )
                if at_bottom and no_new >= 4:
                    log("🏁 Mencapai dasar halaman komentar.")
                    break
                if no_new >= _STOP_AFTER:
                    log("🏁 Tidak ada komentar baru; berhenti menggulir.")
                    break

            rows = list(collected.values())
            if max_comments:
                rows = rows[:max_comments]
            state["rows"] = [
                {key: value for key, value in row.items() if not key.startswith("_")}
                for row in rows
            ]
            log(f"✅ Selesai menggulir. {len(rows)} komentar unik terkumpul.")
        except Exception as exc:  # noqa: BLE001
            state["error"] = "browser_error"
            state["message"] = f"Gagal menjalankan browser: {exc}"
            log(f"⚠️ {state['message']}")

    try:
        with StealthySession(
            headless=headless,
            real_chrome=real_chrome,
            locale="id-ID",
        ) as session:
            session.fetch(
                url,
                page_action=action,
                timeout=max(
                    180000,
                    (max_minutes or _MAX_RUNTIME_MIN) * 60 * 1000 + 180000,
                ),
            )
    except Exception as exc:  # noqa: BLE001
        state["error"] = "browser_error"
        state["message"] = f"Gagal membuka browser: {exc}"
        log(f"⚠️ {state['message']}")

    return state


# --------------------------------------------------------------------------- #
# API publik
# --------------------------------------------------------------------------- #


def scrape_comments(
    url_input: str,
    *,
    method: str = "api",
    sort: str = "teratas",
    max_comments: int = 0,
    include_replies: bool = False,
    max_replies: int = 20,
    max_minutes: int = 10,
    delay_s: float = _API_PAGE_DELAY_S,
    headless: bool = True,
    real_chrome: bool = True,
    wait_ms: int = 1500,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[dict[str, Any]], None]] = None,
    stop_event: Optional[Any] = None,
    checkpoint_cb=None,
) -> dict[str, Any]:
    """Scrape komentar dari link video YouTube.

    Parameter utama:
      - ``method``: ``"api"`` (cepat, melalui browser stealth) atau ``"browser"``.
      - ``sort``: ``"teratas"`` atau ``"terbaru"`` (mode cepat).
      - ``max_comments``: batas komentar utama; 0 = semua yang tersedia.
      - ``include_replies`` / ``max_replies``: ambil balasan (mode cepat).
      - ``max_minutes``: batas waktu; 0 = memakai batas keras 30 menit.

    Mengembalikan dict berisi ``rows``, ``video``, ``total_komentar``,
    ``method``, ``sort``, ``stop_reason``, ``error``, ``message``, ``url``.
    """
    method = method if method in ("api", "browser") else "api"
    sort = sort if sort in _SORTS else "teratas"

    def log(message: str) -> None:
        if log_cb:
            try:
                log_cb(message)
            except Exception:
                pass

    def progress(data: dict[str, Any]) -> None:
        if progress_cb:
            try:
                progress_cb(data)
            except Exception:
                pass

    result: dict[str, Any] = {
        "rows": [],
        "video": {},
        "total_komentar": None,
        "method": method,
        "sort": sort,
        "stop_reason": "",
        "error": None,
        "message": "",
        "url": "",
        "video_id": "",
        "include_replies": bool(include_replies),
        "diambil_pada": _now_jakarta().isoformat(timespec="seconds"),
    }

    try:
        url, video_id = normalize_youtube_url(url_input)
    except ValueError as exc:
        result["error"] = "invalid_url"
        result["message"] = str(exc)
        return result

    result["url"] = url
    result["video_id"] = video_id

    max_comments = max(0, int(max_comments or 0))
    max_replies = max(0, int(max_replies or 0))
    max_minutes = max(0, int(max_minutes or 0))
    delay_s = max(0.0, min(float(delay_s or 0), 5.0))
    wait_ms = max(300, int(wait_ms or 1500))

    log(f"🔗 Video: {url}")

    if method == "api":
        api_result: dict[str, Any]
        try:
            with StealthTransport(headless=headless, real_chrome=real_chrome, timeout=30) as session:
                api_result = _scrape_api(
                    session,
                    url=url,
                    video_id=video_id,
                    sort=sort,
                    max_comments=max_comments,
                    include_replies=include_replies,
                    max_replies=max_replies,
                    max_minutes=max_minutes,
                    delay_s=delay_s,
                    log=log,
                    progress=progress,
                    stop_event=stop_event, checkpoint_cb=checkpoint_cb,
                )
        except Exception as exc:  # noqa: BLE001
            api_result = {
                "rows": [],
                "video": {},
                "total_komentar": None,
                "stop_reason": "gagal",
                "error": "api_error",
                "message": f"Mode cepat gagal: {exc}",
            }

        result["video"] = api_result.get("video") or {}
        result["total_komentar"] = api_result.get("total_komentar")
        result["stop_reason"] = api_result.get("stop_reason") or ""

        if api_result.get("rows"):
            result["rows"] = api_result["rows"]
            result["message"] = api_result.get("message") or ""
            return result

        fatal = api_result.get("error") in (
            "comments_disabled",
            "video_unavailable",
            "login_required",
        )
        if fatal or _should_stop(stop_event):
            result["error"] = api_result.get("error")
            result["message"] = api_result.get("message") or ""
            if _should_stop(stop_event):
                result["stop_reason"] = "dibatalkan"
            log(f"⚠️ {result['message']}")
            return result

        log(
            "↩️ Mode cepat tidak mengembalikan komentar; beralih ke mode browser "
            "sebagai cadangan."
        )
        method = "browser"
        result["method"] = "browser"

    log(
        "🧭 Mode browser: "
        + ("headless" if headless else "browser tampak")
        + (" · Chrome asli" if real_chrome else " · Chromium Scrapling")
    )
    browser_result = _scrape_browser(
        url=url,
        video_id=video_id,
        headless=headless,
        real_chrome=real_chrome,
        wait_ms=wait_ms,
        max_comments=max_comments,
        max_minutes=max_minutes,
        log=log,
        progress=progress,
        stop_event=stop_event, checkpoint_cb=checkpoint_cb,
    )

    if not result["video"]:
        result["video"] = browser_result.get("video") or {}
    result["total_komentar"] = (
        browser_result.get("total") or result.get("total_komentar")
    )
    result["rows"] = browser_result.get("rows") or []
    result["stop_reason"] = browser_result.get("stop_reason") or "habis"
    result["method"] = "browser"
    result["include_replies"] = False

    if browser_result.get("error"):
        result["error"] = browser_result["error"]
        result["message"] = browser_result.get("message") or ""
        log(f"⚠️ {result['message']}")
        return result

    if not result["rows"]:
        result["error"] = "no_comments"
        result["message"] = (
            "Tidak ada komentar yang berhasil dibaca. Kemungkinan komentar "
            "dinonaktifkan atau halaman gagal dimuat."
        )
        log(f"⚠️ {result['message']}")
        return result

    if browser_result.get("total") and len(result["rows"]) < browser_result["total"]:
        result["message"] = (
            f"Berhasil mengambil {len(result['rows'])} dari sekitar "
            f"{browser_result['total']} komentar yang dicantumkan YouTube."
        )
    return result


# --------------------------------------------------------------------------- #
# Ekspor
# --------------------------------------------------------------------------- #

CSV_COLUMNS = [
    "id_komentar",
    "jenis",
    "id_induk",
    "nama",
    "komentar",
    "suka",
    "balasan",
    "waktu",
    "perkiraan_tanggal",
    "terverifikasi",
    "kreator",
    "kanal_id",
    "profil",
    "foto_profil",
    "link_komentar",
    "video_id",
    "judul_video",
    "link_video",
]


def _csv_safe(value: Any) -> Any:
    """Cegah formula injection saat CSV dibuka di Excel/Sheets."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore"
    )
    writer.writeheader()
    writer.writerows(
        {key: _csv_safe(value) for key, value in row.items()}
        for row in rows
    )
    return buffer.getvalue()


def rows_to_json(
    rows: list[dict[str, Any]],
    video: Optional[dict[str, Any]] = None,
    meta: Optional[dict[str, Any]] = None,
) -> str:
    payload: dict[str, Any] = {
        "video": video or {},
        "komentar": rows,
    }
    if meta:
        payload["info_pengambilan"] = meta
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
