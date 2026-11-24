"""Forms and result views for the local Streamlit application."""
from datetime import date, datetime, timedelta
from functools import partial
from html import escape
import csv
import re
from io import StringIO

import pandas as pd
import streamlit as st

from workspace_app import ui
from workspace_app.activity import activity
from workspace_app.excel import MAX_CELL_TEXT, xlsx_bytes
from workspace_app.jobs import ACTIVE, LOGIN_DETECTED, clean_project, encode
from workspace_app.sources import LABELS, LOGINS, label, load

STATUS = ui.STATUS
SORTS = {
    "gmaps": {"relevant": ("Paling relevan", "Urutan bawaan Google Maps"),
              "newest": ("Terbaru", "Ulasan paling baru lebih dulu"),
              "highest": ("Rating tertinggi", "Bintang 5 lebih dulu"),
              "lowest": ("Rating terendah", "Bintang 1 lebih dulu")},
    "playstore": {"terbaru": ("Terbaru", "Ulasan paling baru lebih dulu"),
                  "relevan": ("Paling relevan", "Urutan bawaan Play Store"),
                  "rating": ("Rating", "Dikelompokkan menurut bintang")},
    "appstore": {"terbaru": ("Terbaru", "Ulasan paling baru lebih dulu"),
                 "membantu": ("Paling membantu", "Ulasan dengan suara terbanyak lebih dulu")},
    "threads": {"recent": ("Terbaru", "Post paling baru lebih dulu"),
                "top": ("Teratas", "Post paling populer lebih dulu")},
    "x": {"latest": ("Terbaru dulu", "Mulai dari tanggal selesai, mundur ke tanggal mulai"),
          "top": ("Teratas", "Tweet dengan interaksi terbanyak lebih dulu")},
    "youtube": {"teratas": ("Teratas", "Komentar paling disukai lebih dulu"),
                "terbaru": ("Terbaru", "Komentar paling baru lebih dulu")},
}
METHODS = {
    "gmaps": {"stealth": ("Cepat", "Membaca data ulasan langsung, lewat browser tersembunyi"),
              "browser": ("Menggulir halaman", "Membuka Google Maps dan menggulir seperti pengguna; lebih lambat")},
    "youtube": {"api": ("Cepat", "Membaca data komentar langsung; mendukung balasan"),
                "browser": ("Menggulir halaman", "Membuka video dan menggulir seperti pengguna; lebih lambat")},
}
MODES = {
    "google_scholar": {"profile": ("Profil Scholar", "Publikasi dan sitasi satu peneliti"),
                       "search": ("Cari Kata Kunci", "Publikasi berdasarkan topik")},
    "instagram": {"content": ("Konten", "Post dari username, kata kunci, atau hashtag"),
                  "comments": ("Komentar + balasan", "Komentar dari satu post atau reel")},
    "x": {"search": ("Pencarian", "Cari tweet berdasarkan kata kunci atau hashtag"),
          "profile": ("Profil", "Ambil tweet dari profil pengguna tertentu"),
          "tweet_reply": ("Tweet + balasan", "Ambil satu tweet beserta semua balasannya"),
          "search_reply": ("Pencarian + balasan", "Cari tweet lalu ambil balasan tiap tweet")},
}


def _key(source, name):
    return f"f-{source}-{name}"


def _initial(key, value, arg="value"):
    """Pass a default only while the widget has no state, so a re-run can pre-fill the form."""
    return {} if key in st.session_state else {arg: value}


def choice(label, options, key=None, index=0, horizontal=True):
    """Option cards: a radio whose options carry a title and a short description."""
    return st.radio(label, list(options), index=index, key=key, horizontal=horizontal,
                    format_func=lambda value: options[value][0],
                    captions=[caption for _, caption in options.values()], width="stretch")


def text(source, name, label, default="", **kwargs):
    key = _key(source, name)
    return st.text_input(label, key=key, **_initial(key, default), **kwargs)


def check(source, name, label, default=False, **kwargs):
    key = _key(source, name)
    return st.checkbox(label, key=key, **_initial(key, default), **kwargs)


def number(source, name, label, low, high, default, **kwargs):
    key = _key(source, name)
    return int(st.number_input(label, min_value=low, max_value=high, key=key, **_initial(key, default), **kwargs))


def date_bounds(source, days=30):
    today = date.today()
    left, right = st.columns(2)
    start_key, end_key = _key(source, "start"), _key(source, "end")
    start = left.date_input("Tanggal mulai", max_value=today, format="DD/MM/YYYY", key=start_key,
                            **_initial(start_key, today - timedelta(days=days)))
    end = right.date_input("Tanggal selesai", max_value=today, format="DD/MM/YYYY", key=end_key,
                           **_initial(end_key, today))
    if start and end and start > end:
        st.warning("Tanggal mulai harus sebelum tanggal selesai.")
        return None, None
    return start, end


def maximum(source, label="Jumlah data yang dibutuhkan", initial=300):
    return number(source, "count", label, 0, 100_000, initial, step=50,
                  help="0 mengambil sebanyak yang tersedia dalam batas waktu.")


def time_limit(source):
    with st.expander("Pengaturan lanjutan", icon=":material/tune:"):
        return number(source, "minutes", "Batas waktu (menit)", 1, 30, 10,
                      help="Pengambilan berhenti otomatis setelah waktu ini. Data yang "
                           "sudah terkumpul tetap disimpan.")


OPEN_DATA = ("worldbank", "imf", "usgs", "firms", "openmeteo", "sinta", "pangan")


def params_for(source, mode):
    if source in OPEN_DATA:
        from workspace_app.open_data_view import form
        return form(source)
    if source == "google_scholar":
        from workspace_app.scholar_view import form
        return form(mode)
    k = partial(_key, source)
    if source == "news":
        portal_labels = load("news").PORTAL_LABELS
        keyword = text(source, "keyword", "Kata kunci pencarian", placeholder="Contoh: banjir jakarta",
                       help='Mendukung AND, OR, dan frasa dalam tanda kutip.')
        st.caption('Gunakan OR untuk beberapa kata kunci. Contoh: banjir OR longsor')
        portals = st.multiselect("Portal", list(portal_labels), format_func=portal_labels.get, key=k("portals"),
                                 **_initial(k("portals"), list(portal_labels), "default"))
        start, end = date_bounds(source)
        with st.expander("Pengaturan browser", icon=":material/tune:"):
            headless = check(source, "headless", "Jalankan browser di latar belakang", True)
            resources = check(source, "resources", "Batasi pemuatan sumber daya", True)
            tabs = st.slider("Maksimum tab", 1, 4, key=k("tabs"), **_initial(k("tabs"), 4))
        return dict(portal_keys=portals, keyword=keyword, start_date=start and start.isoformat(),
                    end_date=end and end.isoformat(), headless=headless,
                    disable_resources=resources, max_tabs=tabs)
    if source == "saham":
        symbol = text(source, "symbol", "Kode saham atau aset", placeholder="BBCA, AAPL, BTC-USD, USD/IDR")
        market = choice("Pasar", MARKETS, key=k("market"))
        interval = choice("Interval", INTERVALS, key=k("interval"))
        start, end = date_bounds(source, 365)
        return dict(symbol=symbol, market=market, interval=interval, start_date=start, end_date=end)
    if source == "gmaps":
        url = text(source, "url", "URL atau nama lokasi Google Maps", placeholder="Contoh: Monas Jakarta")
        method = choice("Cara pengambilan", METHODS["gmaps"], key=k("method"))
        sort = choice("Urutan ulasan", SORTS["gmaps"], key=k("sort"))
        count = maximum(source)
        empty = check(source, "empty", "Sertakan rating tanpa teks")
        return dict(url_input=url, method=method, sort=sort, max_minutes=time_limit(source),
                    max_reviews=count, include_empty_reviews=empty)
    if source == "gmaps_places":
        keyword = text(source, "keyword", "Kata kunci tempat",
                       placeholder="Contoh: kopi bandung")
        area = text(source, "area", "Area (opsional)", placeholder="Contoh: Bandung")
        count = number(source, "count", "Jumlah tempat maksimum", 10, 120, 60, step=10,
                       help="Google Maps biasanya berhenti memuat sekitar 120 hasil per pencarian.")
        detail = check(source, "detail", "Ambil detail tiap tempat (lebih lambat)",
                       help="Membuka halaman tiap tempat untuk melengkapi telepon, website, "
                            "plus code, dan jam lengkap.")
        detail_limit = (number(source, "detail_limit", "Batas tempat yang diambil detailnya",
                               1, 60, 20, step=5) if detail else 0)
        headless = check(source, "headless", "Jalankan browser di latar belakang", True)
        return dict(keyword=keyword, area=area, max_places=count, detail_limit=detail_limit,
                    headless=headless, max_minutes=time_limit(source))
    if source == "instagram":
        if mode == "comments":
            target = text(source, "post", "URL post atau reel", placeholder="https://www.instagram.com/p/…")
            count = maximum(source)
            replies = check(source, "replies", "Sertakan balasan", True)
            return dict(comments=True, post_input=target, max_comments=count, include_replies=replies,
                        max_minutes=time_limit(source))
        kind = choice("Jenis pencarian", KINDS, key=k("kind"))
        target = text(source, "query", KINDS[kind][0],
                      placeholder={"username": "Contoh: kompascom", "keyword": "Contoh: kuliner",
                                   "hashtag": "Contoh: kulinerbandung"}[kind])
        start, end = date_bounds(source) if check(source, "limit_dates", "Batasi tanggal") else (None, None)
        count = maximum(source)
        return dict(comments=False, query=target, input_type=kind, max_posts=count,
                    max_minutes=time_limit(source), start_date=start, end_date=end)
    if source == "playstore":
        package = text(source, "package", "Package ID atau URL Play Store", placeholder="com.android.chrome")
        left, right = st.columns(2)
        with left:
            lang = text(source, "lang", "Kode bahasa", "id", help="Dua huruf, misalnya id atau en.")
        with right:
            country = text(source, "country", "Kode negara", "id", help="Dua huruf, misalnya id atau us.")
        sort = choice("Urutan ulasan", SORTS["playstore"], key=k("sort"))
        score = st.pills("Rating", [0, 1, 2, 3, 4, 5], key=k("score"), **_initial(k("score"), 0, "default"),
                         format_func=lambda value: "Semua" if not value else f"{value} ★") or None
        count = maximum(source)
        only_text = check(source, "only_text", "Hanya ulasan dengan teks", True)
        return dict(app_id=package, count=count, lang=lang.strip().lower(),
                    country=country.strip().lower(), sort=sort, score=score,
                    max_minutes=time_limit(source), only_with_text=only_text)
    if source == "appstore":
        app = text(source, "package", "ID aplikasi atau URL App Store",
                   placeholder="389801252 atau https://apps.apple.com/id/app/…/id389801252")
        country = text(source, "country", "Kode negara toko", "id",
                       help="Dua huruf, misalnya id atau us. Ulasan dipisah per toko negara.")
        sort = choice("Urutan ulasan", SORTS["appstore"], key=k("sort"))
        score = st.pills("Rating", [0, 1, 2, 3, 4, 5], key=k("score"), **_initial(k("score"), 0, "default"),
                         format_func=lambda value: "Semua" if not value else f"{value} ★") or None
        st.caption("Apple hanya membuka 500 ulasan terakhir per aplikasi dan negara, tanpa balasan pengembang.")
        count = maximum(source)
        only_text = check(source, "only_text", "Hanya ulasan dengan teks", True)
        return dict(app_id=app, count=count, country=country.strip().lower(), sort=sort, score=score,
                    max_minutes=time_limit(source), only_with_text=only_text)
    if source == "threads":
        keyword = text(source, "keyword", "Kata kunci pencarian", placeholder="Contoh: timnas")
        start, end = date_bounds(source)
        sort = choice("Urutan post", SORTS["threads"], key=k("sort"))
        count = maximum(source)
        return dict(keyword=keyword, sort=sort, start_date=start, end_date=end,
                    max_minutes=time_limit(source), max_posts=count)
    if source == "x":
        field = {"search": "keyword", "search_reply": "keyword",
                 "profile": "username", "tweet_reply": "tweet_url"}[mode]
        value = text(source, field, PARAM_LABELS[field],
                     placeholder={"keyword": "Contoh: Gojek OR Grab", "username": "Contoh: kompascom",
                                  "tweet_url": "https://x.com/…/status/…"}[field])
        if field == "keyword":
            st.caption("Gunakan OR untuk beberapa kata kunci. Contoh: Gojek OR Grab OR Maxim")
        start, end = date_bounds(source) if mode != "tweet_reply" else (None, None)
        sort = choice("Urutan tweet", SORTS["x"], key=k("sort"))
        reply_limit = (number(source, "reply_limit", "Maksimum tweet yang diambil balasannya", 1, 50, 10)
                       if mode == "search_reply" else 10)
        return dict(mode=mode, start_date=start, end_date=end, sort=sort,
                    max_reply_expand=reply_limit, max_minutes=time_limit(source), **{field: value})
    url = text(source, "url", "URL atau ID video YouTube", placeholder="https://www.youtube.com/watch?v=…")
    method = choice("Cara pengambilan", METHODS["youtube"], key=k("method"))
    sort = choice("Urutan komentar", SORTS["youtube"], key=k("sort"))
    replies = check(source, "replies", "Sertakan balasan", disabled=method != "api") and method == "api"
    max_replies = number(source, "max_replies", "Batas balasan per komentar", 0, 1000, 20) if replies else 20
    count = maximum(source, "Jumlah komentar utama")
    return dict(url_input=url, method=method, sort=sort, include_replies=replies,
                max_replies=max_replies, max_comments=count, max_minutes=time_limit(source))


# --------------------------------------------------------------------------- #
# Method record: how a dataset was collected, in plain words
# --------------------------------------------------------------------------- #

MARKETS = {"IDX": ("IDX", "Bursa Efek Indonesia"), "US": ("US", "Bursa Amerika"),
           "CRYPTO": ("Kripto", "Pasangan USD"), "FX": ("Kurs", "Pasangan mata uang, misalnya USD/IDR"),
           "CUSTOM": ("Lainnya", "Kode apa adanya")}
INTERVALS = {"1d": ("Harian", "Satu baris per hari bursa"), "1wk": ("Mingguan", "Satu baris per minggu"),
             "1mo": ("Bulanan", "Satu baris per bulan")}
KINDS = {"username": ("Username", "Post dari satu akun"), "keyword": ("Kata kunci", "Post yang memuat kata"),
         "hashtag": ("Hashtag", "Post dengan tagar tertentu")}
PARAM_LABELS = {"keyword": "Kata kunci pencarian", "username": "Username", "tweet_url": "URL tweet"}
TARGETS = {"keyword": "Kata kunci", "query": "Pencarian", "post_input": "Post", "app_id": "Aplikasi",
           "symbol": "Kode aset", "username": "Username", "tweet_url": "Tweet", "area": "Area"}
LIMITS = ("max_places", "max_reviews", "max_comments", "max_posts", "count")
# Parameter name -> form field, for "Jalankan ulang".
FIELDS = {
    "keyword": "keyword", "query": "query", "post_input": "post", "url_input": "url", "app_id": "package",
    "symbol": "symbol", "username": "username", "tweet_url": "tweet_url", "portal_keys": "portals",
    "market": "market", "interval": "interval", "method": "method", "sort": "sort", "max_minutes": "minutes",
    "include_replies": "replies", "max_replies": "max_replies", "lang": "lang", "country": "country",
    "only_with_text": "only_text", "include_empty_reviews": "empty", "input_type": "kind",
    "max_reply_expand": "reply_limit", "headless": "headless", "disable_resources": "resources",
    "max_tabs": "tabs", "area": "area", "detail_limit": "detail_limit",
}


def _as_date(value):
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def mode_of(source, params):
    if source == "instagram":
        return "comments" if params.get("comments") else "content"
    return params.get("mode") if source in {"x", "google_scholar"} else None


def method_rows(job):
    """(label, value) pairs that describe how a scraping job collected its data."""
    source, params = job["source"], job["params"]
    mode = mode_of(source, params)
    rows = [("Sumber", LABELS.get(source, source) + (f" · {MODES[source][mode][0]}" if mode in MODES.get(source, {}) else ""))]
    if source == "instagram" and not params.get("comments") and params.get("input_type") in KINDS:
        rows.append(("Jenis pencarian", KINDS[params["input_type"]][0]))
    if source in OPEN_DATA:
        from workspace_app.open_data_view import method_rows as open_data_rows
        rows += open_data_rows(job)
    if source == "google_scholar":
        from workspace_app.scholar_view import SORTS as SCHOLAR_SORTS
        rows.append(("Urutan", SCHOLAR_SORTS[mode][params["sort"]]))
        if params.get("year_from") or params.get("year_to"):
            rows.append(("Tahun publikasi", f"{params.get('year_from') or '…'} – {params.get('year_to') or '…'}"))
    if params.get("url_input"):
        rows.append(("Video" if source == "youtube" else "Lokasi", params["url_input"]))
    rows += [(name, params[key]) for key, name in TARGETS.items() if params.get(key)]
    if source == "news" and params.get("portal_keys"):
        portals = load("news").PORTAL_LABELS
        rows.append(("Portal", ", ".join(portals.get(key, key) for key in params["portal_keys"])))
    if source == "saham":
        rows += [("Pasar", MARKETS.get(params.get("market"), (params.get("market"),))[0]),
                 ("Interval", INTERVALS.get(params.get("interval"), (params.get("interval"),))[0])]
    start, end = _as_date(params.get("start_date")), _as_date(params.get("end_date"))
    if start and end:
        rows.append(("Rentang tanggal", f"{ui.short_date(start)} – {ui.short_date(end)}"))
    if params.get("sort") in SORTS.get(source, {}):
        rows.append(("Urutan", SORTS[source][params["sort"]][0]))
    if params.get("method") in METHODS.get(source, {}):
        rows.append(("Cara pengambilan", METHODS[source][params["method"]][0]))
    if source == "appstore":
        rows.append(("Negara toko", params.get("country", "-")))
        rows.append(("Rating", f"{params['score']} ★" if params.get("score") else "Semua"))
    if source == "playstore":
        rows.append(("Bahasa / negara", f"{params.get('lang', '-')} / {params.get('country', '-')}"))
        rows.append(("Rating", f"{params['score']} ★" if params.get("score") else "Semua"))
    if "include_replies" in params:
        rows.append(("Balasan", "Disertakan" if params["include_replies"] else "Tidak"))
    limit = next((params[key] for key in LIMITS if key in params), None)
    if limit is not None:
        rows.append(("Batas data", "Sebanyak tersedia" if not limit else ui.fmt(limit)))
    if source == "gmaps_places":
        detail = params.get("detail_limit") or 0
        rows.append(("Detail per tempat", f"{ui.fmt(detail)} tempat" if detail else "Tidak"))
    if params.get("max_minutes"):
        rows.append(("Batas waktu", f"{params['max_minutes']} menit"))
    rows.append(("Diambil", ui.long_date(job["started"])))
    if job.get("finished"):
        rows.append(("Durasi", ui.duration(job["started"], job["finished"])))
    rows.append(("Hasil", f"{ui.fmt(job.get('count', len(job.get('rows', []))))} baris · {STATUS.get(job['status'], job['status'])}"))
    return [(name, str(value)) for name, value in rows]


def method_text(job):
    """One paragraph a researcher can paste into a methods section."""
    rows = dict(method_rows(job))
    details = "; ".join(f"{name.lower()}: {value}" for name, value in rows.items()
                        if name not in {"Sumber", "Diambil", "Durasi", "Hasil"})
    return (f"Data dikumpulkan dari {rows['Sumber']} menggunakan nOCturnal Scraper pada {rows['Diambil']}"
            f"{' (durasi ' + rows['Durasi'] + ')' if 'Durasi' in rows else ''}. "
            f"Parameter: {details}. Hasil: {rows['Hasil']}.")


def recovery_hints(job):
    """What a researcher can try next after a failed, partial, or empty job.

    Phrased as suggestions: the app rarely knows the exact cause.
    """
    if job["source"] not in LABELS or (job["status"] == "completed" and job.get("count")):
        return []
    params, hints = job["params"], []
    if job["source"] in OPEN_DATA:
        from workspace_app.open_data_view import hints as open_data_hints
        return open_data_hints(job)
    message = str(job.get("message") or "").lower()
    if job["source"] in LOGINS:
        hints.append("Pastikan kamu sudah login: buka form sumber lalu klik **Login platform**.")
    start, end = _as_date(params.get("start_date")), _as_date(params.get("end_date"))
    if start and end and (end - start).days < 60:
        hints.append("Lebarkan rentang tanggal.")
    if job["source"] == "gmaps_places":
        hints.append("Coba kata kunci yang lebih umum atau tambahkan area, misalnya nama kota.")
        if params.get("detail_limit"):
            hints.append("Kurangi batas detail atau matikan opsi detail agar lebih cepat.")
    elif params.get("keyword") or params.get("query"):
        hints.append("Pakai kata kunci yang lebih umum, atau gabungkan beberapa dengan OR.")
    if job["source"] in SORTS and params.get("sort") in SORTS[job["source"]] and len(SORTS[job["source"]]) > 1:
        other = next(title for value, (title, _) in SORTS[job["source"]].items() if value != params["sort"])
        hints.append(f"Coba urutan **{other}**.")
    if params.get("method") in METHODS.get(job["source"], {}):
        other = next(title for value, (title, _) in METHODS[job["source"]].items() if value != params["method"])
        hints.append(f"Coba cara pengambilan **{other}**.")
    if any(word in message for word in ("waktu", "timeout")) or job["status"] == "partial":
        hints.append("Naikkan batas waktu di **Pengaturan lanjutan**.")
    if job["status"] == "interrupted":
        hints = ["Jalankan ulang untuk mengambil lagi dari awal. Dataset ini tetap tersimpan terpisah."]
    return hints


def problem_notice(job):
    """What went wrong, what it means for the saved data, and what to try next."""
    status, message = job["status"], ui.friendly(job.get("message"))
    if status not in ui.STATUS_HELP:
        if message:
            st.warning(message, icon=":material/warning:")
        return
    body = message or ui.STATUS_HELP[status]
    if message and status != "failed":
        body = f"{body} {ui.STATUS_HELP[status]}"
    if hints := recovery_hints(job):
        body += "\n\n**Yang bisa dicoba:**\n" + "\n".join(f"- {hint}" for hint in hints)
    (st.error if status == "failed" else st.warning)(body, icon=":material/warning:")


def show_method(rows):
    items = "".join(f"<dt>{escape(name)}</dt><dd>{escape(value)}</dd>" for name, value in rows)
    st.html(f'<dl class="method">{items}</dl>')


def seed_form(source, params, project):
    """Pre-fill a source form with an earlier job's parameters, for "Jalankan ulang"."""
    state = st.session_state
    if source in OPEN_DATA:
        from workspace_app.open_data_view import seed
        seed(source, params)
    if source == "google_scholar":
        for name, value in params.items():
            if name != "mode":
                state[f"scholar-{params['mode']}-{name}"] = value
    for name, value in params.items():
        if name in FIELDS and value is not None:
            state[_key(source, FIELDS[name])] = value
        elif name in LIMITS:
            state[_key(source, "count")] = value
    if source in {"playstore", "appstore"}:
        state[_key(source, "score")] = params.get("score") or 0
    if source == "gmaps_places":
        state[_key(source, "detail")] = bool(params.get("detail_limit"))
    start, end = _as_date(params.get("start_date")), _as_date(params.get("end_date"))
    if start and end:
        state[_key(source, "start")], state[_key(source, "end")] = start, end
        state[_key(source, "limit_dates")] = True
    if mode := mode_of(source, params):
        state[f"mode-{source}"] = mode
    if project:
        state[f"project-{source}"] = project


def summary_lines(source, mode, params):
    """Recap shown before starting: what will be collected and within which limits."""
    if source in OPEN_DATA:
        from workspace_app.open_data_view import summary
        return summary(source, params)
    target = next((params[key] for key in ("keyword", "query", "post_input", "url_input", "app_id", "symbol",
                                            "username", "tweet_url") if params.get(key)), "")
    lines = [("Sumber", LABELS[source] + (f" · {MODES[source][mode][0]}" if source in MODES else "")),
             ("Input", target or "belum diisi")]
    if params.get("area"):
        lines.append(("Area", params["area"]))
    if params.get("start_date") and params.get("end_date"):
        start, end = (value if isinstance(value, date) else date.fromisoformat(value)
                      for value in (params["start_date"], params["end_date"]))
        lines.append(("Rentang tanggal", f"{start:%d/%m/%Y} – {end:%d/%m/%Y}"))
    if source == "news":
        lines.append(("Portal", f"{len(params['portal_keys'])} portal"))
    limit = next((params[key] for key in ("max_places", "max_reviews", "max_comments", "max_posts", "count")
                  if key in params), None)
    if limit is not None:
        lines.append(("Jumlah data maksimum", "sebanyak tersedia" if limit == 0 else ui.fmt(limit)))
    if params.get("detail_limit"):
        lines.append(("Detail per tempat", f"{ui.fmt(params['detail_limit'])} tempat"))
    if "max_minutes" in params:
        lines.append(("Batas waktu", f"{params['max_minutes']} menit"))
    return lines


def show_summary(lines):
    with st.container(key="summary"):
        for index, (name, value) in enumerate(lines):
            left, right = st.columns([1, 1])
            left.markdown(name)
            shown = '<span class="missing">belum diisi</span>' if value == "belum diisi" else f"<b>{escape(str(value))}</b>"
            right.markdown(f"<div style='text-align:right'>{shown}</div>", unsafe_allow_html=True)
            if index == 0:
                st.markdown("---")


def project_field(manager, source):
    """Type a new project name or pick an existing one; there is no automatic name."""
    name = st.selectbox(
        "Nama proyek", manager.projects(), index=None, accept_new_options=True,
        placeholder="Ketik nama proyek baru atau pilih yang sudah ada", key=f"project-{source}",
        help="Wajib diisi. Gunakan nama yang sama untuk mengelompokkan beberapa scraping "
             "dalam satu proyek.",
    )
    return clean_project(name)


def started_label(value):
    return ui.short_datetime(value)


def file_stem(job):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", job.get("project") or job["source"]).strip("-").lower()
    return f"{slug[:60] or job['source']}-{job['id'][:8]}"


def validate(source, params):
    if source in OPEN_DATA:
        from workspace_app.open_data_view import check
        check(params, source)
        return
    if source == "google_scholar":
        if params["mode"] == "profile":
            load(source).extract_scholar_id(params["query"])
        elif params.get("year_from") and params.get("year_to") and params["year_from"] > params["year_to"]:
            raise ValueError("Tahun awal harus sebelum atau sama dengan tahun akhir.")
    item_keys = {
        "google_scholar": ["query"],
        "news": ["keyword"], "saham": ["symbol"], "gmaps": ["url_input"],
        "gmaps_places": ["keyword"],
        "instagram": ["post_input", "query"], "playstore": ["app_id"], "appstore": ["app_id"],
        "threads": ["keyword"], "x": ["keyword", "username", "tweet_url"],
        "youtube": ["url_input"],
    }
    for key in item_keys[source]:
        if key in params and not params[key].strip():
            raise ValueError("Isi input pencarian terlebih dahulu.")
    if source == "news" and not params["portal_keys"]:
        raise ValueError("Pilih minimal satu portal.")
    if "start_date" in params and source != "instagram" and not (params["start_date"] and params["end_date"]):
        if source != "x" or params.get("mode") != "tweet_reply":
            raise ValueError("Pilih tanggal awal dan akhir.")
    if source == "playstore":
        params["app_id"] = load(source).normalize_app_input(params["app_id"])
        for key in ("lang", "country"):
            value = params[key]
            if len(value) != 2 or not value.isascii() or not value.isalpha():
                raise ValueError("Kode bahasa dan negara harus terdiri dari dua huruf.")
    if source == "appstore":
        params["app_id"] = load(source).normalize_app_input(params["app_id"])
        country = params["country"]
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError("Kode negara harus terdiri dari dua huruf.")


def source_page(manager, source):
    from workspace_app.studio import CATALOG

    active, login_status = manager.snapshot()
    st.session_state["workspace_active"] = (active["source"], active["id"]) if active else None

    def login():
        status = login_status.get(source)
        if st.button("Login platform", icon=":material/login:",
                     disabled=active is not None or status == LOGIN_DETECTED,
                     key=f"login-{source}", width="stretch"):
            try:
                manager.login(source)
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
        st.caption(status or "Profil browser khusus workspace ini.")

    ui.page_header(LABELS[source], CATALOG[source][2], back="scraping",
                   actions=login if source in LOGINS else None)
    ui.highlight_nav()
    scraper_form(manager, source, active, login_status)

    jobs = manager.list_jobs(source, limit=1)
    if jobs:
        with st.container(key="panel-last-job"):
            st.header("Pekerjaan terakhir", anchor=False)
            show_job(manager, jobs[0])


def scraper_form(manager, source, active, login_status):
    with st.container(key="panel-form"):
        mode = (choice("Ambil data dari", MODES[source], key=f"mode-{source}") if source in MODES else None)
        params = params_for(source, mode)
        show_summary(summary_lines(source, mode, params))
        project = project_field(manager, source)
        if st.button("Mulai Ambil Data", type="primary", icon=":material/arrow_forward:", icon_position="right",
                     disabled=active is not None or not project, key=f"start-{source}"):
            try:
                validate(source, params)
                manager.start(source, params, project=project)
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if active is not None:
            st.caption("Tunggu pekerjaan yang sedang berjalan selesai atau hentikan dulu.")
        elif not project:
            st.caption("Isi nama proyek untuk mengaktifkan tombol mulai.")
        activity(manager, framed=False)


def csv_bytes(rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({
            key: "'" + value if isinstance(value, str) and value.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")) else value
            for key, value in row.items()
        })
    return buffer.getvalue().encode("utf-8-sig")


def show_job(manager, summary, compact=False):
    job_id = summary["id"]
    if summary["status"] in ACTIVE:
        live_result(manager, job_id, compact)
    else:
        render_result(manager.get(job_id), manager.logs(job_id), compact)


@st.fragment(run_every=2)
def live_result(manager, job_id, compact=False):
    render_result(manager.get(job_id), manager.logs(job_id), compact)


def render_result(job, logs=(), compact=False):
    if not job:
        return
    rows = job["rows"]
    active = job["status"] in ACTIVE
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{job.get('project') or label(job['source'])}**")
        ui.status_badge(job["status"])
    finished = f" · durasi {ui.duration(job['started'], job['finished'])}" if job.get("finished") else ""
    st.caption(f"{label(job['source'])} · {ui.long_date(job['started'])}{finished} · "
               f"{ui.fmt(len(rows))} baris tersimpan")
    if not active:
        problem_notice(job)
    if compact and rows and not active:
        data_tabs(job, rows)
        downloads(job, rows)
        return
    with st.expander("Log pekerjaan", expanded=active, icon=":material/terminal:"):
        if logs:
            st.code("\n".join(logs), language="text")
            if len(logs) == 300:
                st.caption("Menampilkan maksimal 300 pesan terakhir.")
        else:
            st.caption("Belum ada log tersimpan untuk pekerjaan ini.")
    with st.expander("Parameter dan metadata", icon=":material/data_object:"):
        st.json({"parameter": job["params"], "metadata": job["metadata"]}, expanded=False)
    if not rows and not (job["source"] == "google_scholar" and job["metadata"]):
        st.info("Menunggu hasil pertama." if active else "Pekerjaan ini tidak menghasilkan data.")
        return
    if active:
        st.markdown("**Hasil sementara**")
        if len(rows) > 500:
            st.caption("Tabel sementara menampilkan 500 baris pertama.")
        st.dataframe(pd.DataFrame(rows[:500]).drop(columns=["paragraphs"], errors="ignore"),
                     width="stretch", hide_index=True)
        return
    data_tabs(job, rows)
    downloads(job, rows)


def data_tabs(job, rows):
    if job["source"] == "google_scholar":
        from workspace_app.scholar_view import data_tabs as scholar_tabs
        return scholar_tabs(job)
    if rows:
        frame = pd.DataFrame(rows).drop(columns=["paragraphs"], errors="ignore")
        result_tab, detail_tab = st.tabs([":material/table_view: Data", ":material/bar_chart: Ringkasan"])
        with result_tab:
            query = st.text_input("Cari di hasil", key=f"find-{job['id']}", icon=":material/search:",
                                  placeholder="Cari di hasil (hanya memfilter tampilan)", label_visibility="collapsed")
            if query.strip():
                mask = frame.fillna("").astype(str).apply(
                    lambda col: col.str.contains(query.strip(), case=False, regex=False)
                ).any(axis=1)
                frame = frame[mask]
            st.dataframe(frame, width="stretch", hide_index=True)
        with detail_tab:
            if job["source"] in OPEN_DATA:
                from workspace_app.open_data_view import chart
                chart(frame, job["source"])
            elif job["source"] == "saham" and {"date", "close"}.issubset(frame):
                st.line_chart(frame.set_index("date")["close"])
                if events := job["metadata"].get("events"):
                    st.dataframe(events, hide_index=True)
            elif job["source"] == "gmaps_places":
                if {"lintang", "bujur"}.issubset(frame):
                    points = frame.dropna(subset=["lintang", "bujur"])
                    if not points.empty:
                        st.map(points, latitude="lintang", longitude="bujur")
                if not frame.empty:
                    stats = st.columns(3)
                    stats[0].metric("Tempat", ui.fmt(len(frame)))
                    if "rating" in frame:
                        rating = pd.to_numeric(frame["rating"], errors="coerce").mean()
                        stats[1].metric("Rating rata-rata", f"{rating:.2f}" if pd.notna(rating) else "-")
                    if "kategori" in frame:
                        stats[2].metric("Kategori", ui.fmt(frame["kategori"].nunique()))
                    if "kategori" in frame:
                        st.bar_chart(frame["kategori"].value_counts().head(15))
            else:
                group = next((column for column in ("portal", "rating", "media", "username") if column in frame), None)
                if group and not frame.empty:
                    st.bar_chart(frame[group].value_counts().head(15))
                else:
                    st.metric("Baris", ui.fmt(len(frame)))


def downloads(job, rows):
    if any(isinstance(value, str) and len(value) > MAX_CELL_TEXT for row in rows for value in row.values()):
        st.caption(":material/info: Ada teks lebih dari 32.767 karakter. Excel akan memotongnya; "
                   "pakai CSV atau JSON agar teks tetap utuh.")
    excel_options = {}
    if job["source"] == "google_scholar":
        excel_options = {"sheet_name": "Artikel" if job["params"]["mode"] == "profile" else "Hasil",
                         "extra_sheets": job["metadata"]}
    cols = st.columns(3)
    filename = file_stem(job)
    cols[0].download_button(
        "Unduh Excel", partial(xlsx_bytes, rows, **excel_options), f"{filename}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click="ignore", key=f"xlsx-{job['id']}", icon=":material/table_view:", width="stretch",
    )
    cols[1].download_button("Unduh CSV", partial(csv_bytes, rows), f"{filename}.csv", "text/csv",
                            on_click="ignore", key=f"csv-{job['id']}", icon=":material/csv:", width="stretch")
    cols[2].download_button("Unduh JSON", lambda: encode(job), f"{filename}.json", "application/json",
                            on_click="ignore", key=f"json-{job['id']}", icon=":material/data_object:",
                            width="stretch")
